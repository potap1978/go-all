#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — lib/selftest.sh — механизм тестирования установленного резолвера.
#
# Устройство: нумерованный список проверок, каждая — функция, возвращающая
#   0  PASS   — проверено и подтверждено
#   1  FAIL   — проверено и опровергнуто
#   2  SKIP   — проверить нечем (причина в ST_REASON)
#   3  INFO   — наблюдение, а не приговор (не влияет на итог «всё хорошо»)
# Драйвер st_run_check печатает отчёт в стиле goVLESS и НИКОГДА не падает:
# проверка вызывается как `"$fn" || rc=$?`, а условный контекст глушит
# errexit во всём дереве вызовов внутри неё.
#
# Честность формулировок — часть контракта этого модуля:
#   • «слушает сокет» и «до него достучится интернет» — РАЗНЫЕ утверждения;
#   • dnsdist пересылает, а DNSSEC валидирует АПСТРИМ — так и написано;
#   • счётчик nft доказывает, что plaintext не ушёл С ЭТОЙ МАШИНЫ, и ничего
#     не говорит о том, что делает апстрим после получения запроса.
#
# Требует: lib/common.sh (логи, run_action, config_get, godns_mktemp),
#          lib/i18n.sh  (t / tf).

# ── Контекст ────────────────────────────────────────────────────────────
ST_DOMAIN=""            # имя, на которое выписан сертификат
ST_DOH_PORT=""
ST_DOT_PORT=""
ST_DOH_PATH=""          # секретный путь БЕЗ ведущего слэша
ST_PUBIP=""
ST_CONNECT_HOST=""      # куда реально коннектиться (имя или IP)
ST_RESOLVE_ARGS=()      # --resolve для curl, если имя локально не резолвится
ST_DNSDIST_CONF="${ST_DNSDIST_CONF:-/etc/dnsdist/dnsdist.conf}"
# Набор публичных корней. Переопределяется только тестами; в бою — путь
# ca-certificates из Ubuntu.
ST_CA_BUNDLE="${ST_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"

# Рабочие файлы (заполняются в selftest_prepare через godns_mktemp).
ST_Q_B64=""; ST_Q_BIN=""; ST_R_BIN=""; ST_R_GET=""; ST_TLS_OUT=""
ST_CERT_PEM=""; ST_DIG_OUT=""; ST_SCRATCH=""

# Результаты последней curl-пробы.
ST_CODE=""; ST_VER=""; ST_CT=""; ST_TLSV=""; ST_PINNED=0

# Счётчики и состояние драйвера.
ST_PASS=0; ST_FAIL=0; ST_SKIP=0; ST_INFO=0; ST_NUM=0
ST_REASON=""
ST_NOTES=()
ST_FAILED=()
ST_GROUP_SEEN=""

# Третьи стороны — только по явному согласию оператора.
ST_ALLOW_THIRDPARTY="${ST_ALLOW_THIRDPARTY:-0}"

# Транспорт для запросов kdig: dot | doh | "" (нет)
ST_QTRANSPORT=""

# ═══════════════════════════════════════════════════════════════════════
# Мелкие утилиты
# ═══════════════════════════════════════════════════════════════════════

st_have() { command -v "$1" >/dev/null 2>&1; }

# timeout(1) есть в coreutils Ubuntu, но не везде (и не на macOS, где
# гоняются локальные тесты). Без него — просто запускаем как есть.
st_timeout() {
    local _s="$1"; shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$_s" "$@"
    else
        "$@"
    fi
}

st_note()  { ST_NOTES+=("$1"); return 0; }
st_skip()  { ST_REASON="$1"; return 2; }

# Обрезка длинных строк — cut, а НЕ head: head закрывает канал, producer
# получает SIGPIPE, и под pipefail подстановка падает.
st_trim() { printf '%s' "$1" | cut -c1-"${2:-160}"; }

# Локальный резолв имени. python3 вместо getent — переносимо и не зависит
# от того, что мы только что сделали с /etc/resolv.conf.
st_resolve_host() {
    local _h="$1" _ip=""
    _ip=$(python3 - "$_h" <<'PY' 2>/dev/null
import socket, sys
try:
    print(socket.getaddrinfo(sys.argv[1], None, 0, socket.SOCK_STREAM)[0][4][0])
except Exception:
    pass
PY
    ) || _ip=""
    printf '%s' "$_ip"
}

# ── Разбор ss ───────────────────────────────────────────────────────────
# Строки LISTEN для порта. Столбцы `ss -Hlntp`:
#   1 State  2 Recv-Q  3 Send-Q  4 Local:Port  5 Peer:Port  6 users:((...))
st_listen_lines() {
    local _port="$1"
    st_have ss || return 1
    [ -n "$_port" ] || return 1
    ss -Hlntp 2>/dev/null | awk -v p="$_port" '
        { a = $4; n = split(a, q, ":"); if (q[n] == p) print }
    '
}

# Локальный адрес из строки ss — всё до последнего двоеточия.
st_addr_of_line() {
    local _l="$1" _f=""
    _f=$(printf '%s\n' "$_l" | awk '{print $4}')
    printf '%s' "${_f%:*}"
}

# ── Разбор DNS-текста (kdig/dig) ────────────────────────────────────────
# Читаем ФАЙЛ, не канал: никаких `| head`, никакого SIGPIPE.
st_dns_field() {
    local _file="$1" _lbl="$2"
    [ -r "$_file" ] || return 1
    awk -v lbl="$_lbl" '
        { i = index($0, lbl ": ")
          if (i > 0) {
              s = substr($0, i + length(lbl) + 2)
              sub(/[^A-Za-z0-9].*$/, "", s)
              if (s != "") { print s; exit }
          } }
    ' "$_file" 2>/dev/null
}

# ВНИМАНИЕ: искать «status:» по всему выводу нельзя. У kdig в режиме DoH
# ПЕРВОЙ идёт строка баннера HTTP:
#   ;; HTTP session (HTTP/2-POST)-(dom/path)-(status: 200)
# и наивный разбор возвращает «200» вместо кода ответа DNS. Читаем строку
# ->>HEADER<<- и только её (у kdig разделитель «;», у dig — «,»).
st_dns_status() {
    local v=""
    [ -r "$1" ] || return 1
    v=$(awk '
        /->>HEADER<<-/ {
            i = index($0, "status: ")
            if (i > 0) {
                s = substr($0, i + 8)
                sub(/[^A-Za-z0-9].*$/, "", s)
                if (s != "") { print s; exit }
            }
        }' "$1" 2>/dev/null) || v=""
    if [ -n "$v" ]; then printf '%s' "$v"; return 0; fi
    st_dns_field "$1" "status"
}

st_dns_ancount() { st_dns_field "$1" "ANSWER"; }

# ═══════════════════════════════════════════════════════════════════════
# Подготовка
# ═══════════════════════════════════════════════════════════════════════

# Запрос A/IN example.com, ID = 0 (RFC 8484 SHOULD: постоянный ID не
# дробит HTTP-кэш). base64url БЕЗ набивки — Quad9 отвечает 403 на «=».
st_build_query() {
    local _name="${1:-example.com}"
    ST_Q_B64=$(python3 - "$_name" <<'PY' 2>/dev/null
import base64, struct, sys
name = sys.argv[1]
hdr = struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 0)
qn = b"".join(bytes([len(l)]) + l.encode("idna") for l in name.split(".")) + b"\x00"
msg = hdr + qn + struct.pack(">HH", 1, 1)
sys.stdout.write(base64.urlsafe_b64encode(msg).decode("ascii").rstrip("="))
PY
    ) || ST_Q_B64=""
    [ -n "$ST_Q_B64" ] || return 1
    python3 - "$_name" "$ST_Q_BIN" <<'PY' 2>/dev/null || return 1
import struct, sys
name, out = sys.argv[1], sys.argv[2]
hdr = struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 0)
qn = b"".join(bytes([len(l)]) + l.encode("idna") for l in name.split(".")) + b"\x00"
open(out, "wb").write(hdr + qn + struct.pack(">HH", 1, 1))
PY
    return 0
}

# Разбор ответа в проводном формате. Печатает сводку, rc 1 если это не
# настоящий DNS-ответ (например, HTML-страница ошибки с кодом 200).
st_parse_wire() {
    python3 - "$1" <<'PY'
import struct, sys
try:
    m = open(sys.argv[1], "rb").read()
except Exception as e:
    sys.stdout.write("read error: %s" % e); raise SystemExit(1)
if len(m) < 12:
    sys.stdout.write("too short: %d B" % len(m)); raise SystemExit(1)
mid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", m[:12])
bad = []
if mid != 0: bad.append("id=%d(!=0)" % mid)
if not (flags & 0x8000): bad.append("QR=0")
rc = flags & 0x000F
if rc != 0: bad.append("RCODE=%d" % rc)
if qd != 1: bad.append("QDCOUNT=%d" % qd)
if an < 1: bad.append("ANCOUNT=%d" % an)
sys.stdout.write("id=%d rcode=%d qd=%d an=%d ra=%d ad=%d %dB%s" % (
    mid, rc, qd, an,
    1 if flags & 0x0080 else 0,
    1 if flags & 0x0020 else 0,
    len(m),
    ("  << " + ", ".join(bad)) if bad else ""))
raise SystemExit(1 if bad else 0)
PY
}

# DoT включён? Тот же ключ и тот же дефолт, что у рендера движка
# (lib/engines/dnsdist.sh): отсутствие ключа означает «выключен».
#
# ГРАБЛИ: проверки DoT не спрашивали об этом вовсе и при выключенном DoT
# честно ВАЛИЛИСЬ — «DoT-порт слушает: провалено», «Имя разрешается через
# DoT: провалено». Оператор, сознательно выключивший DoT (а он выключен
# по умолчанию), видел два красных пункта на исправной установке и
# переставал верить остальным. Выключенная по решению возможность — это
# «нечего проверять», а не «сломано».
selftest_dot_on() {
    local v="${GODNS_DOT_ENABLED:-}"
    if [ -z "$v" ]; then v=$(config_get dot_enabled "no") || v="no"; fi
    [ "$v" = "yes" ]
}

selftest_load_ctx() {
    ST_DOMAIN="${GODNS_DOMAIN:-}"
    [ -n "$ST_DOMAIN" ] || ST_DOMAIN=$(config_get domain "") || ST_DOMAIN=""
    [ -n "$ST_DOMAIN" ] || ST_DOMAIN=$(config_get doh_domain "") || ST_DOMAIN=""

    ST_DOH_PORT="${GODNS_DOH_PORT:-}"
    [ -n "$ST_DOH_PORT" ] || ST_DOH_PORT=$(config_get doh_port "443") || ST_DOH_PORT="443"
    ST_DOT_PORT="${GODNS_DOT_PORT:-}"
    [ -n "$ST_DOT_PORT" ] || ST_DOT_PORT=$(config_get dot_port "853") || ST_DOT_PORT="853"

    ST_DOH_PATH="${GODNS_DOH_PATH:-}"
    [ -n "$ST_DOH_PATH" ] || ST_DOH_PATH=$(config_get doh_path "") || ST_DOH_PATH=""
    [ -n "$ST_DOH_PATH" ] || ST_DOH_PATH=$(config_get secret_path "") || ST_DOH_PATH=""
    ST_DOH_PATH="${ST_DOH_PATH#/}"

    ST_PUBIP=$(get_server_ip) || ST_PUBIP=""
    return 0
}

selftest_prepare() {
    selftest_load_ctx

    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    if ! st_have python3; then
        log_error "$(t test_no_python)"
        return 1
    fi

    ST_Q_BIN=$(godns_mktemp)   || return 1
    ST_R_BIN=$(godns_mktemp)   || return 1
    ST_R_GET=$(godns_mktemp)   || return 1
    ST_TLS_OUT=$(godns_mktemp) || return 1
    ST_CERT_PEM=$(godns_mktemp)|| return 1
    ST_DIG_OUT=$(godns_mktemp) || return 1
    ST_SCRATCH=$(godns_mktemp) || return 1

    if ! st_build_query example.com; then
        log_error "$(t test_query_build_failed)"
        return 1
    fi

    # Куда коннектиться. Мы только что переставили резолвер на этой машине,
    # поэтому имя может локально не резолвиться — тогда идём на публичный IP
    # и ГОВОРИМ об этом: end-to-end через публичный DNS в этом случае
    # НЕ проверен.
    local ip=""
    ip=$(st_resolve_host "$ST_DOMAIN") || ip=""
    ST_RESOLVE_ARGS=()
    if [ -n "$ip" ]; then
        ST_CONNECT_HOST="$ST_DOMAIN"
    elif [ -n "$ST_PUBIP" ]; then
        ST_CONNECT_HOST="$ST_PUBIP"
        ST_RESOLVE_ARGS=(--resolve "${ST_DOMAIN}:${ST_DOH_PORT}:${ST_PUBIP}"
                         --resolve "${ST_DOMAIN}:${ST_DOT_PORT}:${ST_PUBIP}")
        log_warning "$(tf test_name_pinned "$ST_DOMAIN" "$ST_PUBIP")"
    else
        ST_CONNECT_HOST="$ST_DOMAIN"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Драйвер и отчёт
# ═══════════════════════════════════════════════════════════════════════

ST_SUITE=()
st_add() { ST_SUITE+=("$1|$2|$3"); return 0; }   # fn|labelkey|groupkey

st_reset_tally() {
    ST_PASS=0; ST_FAIL=0; ST_SKIP=0; ST_INFO=0; ST_NUM=0
    ST_FAILED=(); ST_GROUP_SEEN=""
    return 0
}

st_run_check() {
    local fn="$1" labelkey="$2" rc=0 num="" n=""
    ST_NOTES=(); ST_REASON=""
    ST_NUM=$((ST_NUM + 1))
    num=$(printf '%02d' "$ST_NUM")

    # Условный контекст: errexit выключен во всём дереве вызовов внутри fn.
    "$fn" || rc=$?

    local label=""
    label=$(t "$labelkey") || label="$labelkey"

    case "$rc" in
        0)  ST_PASS=$((ST_PASS + 1))
            echo -e "  ${GREEN}✓${NC}  ${DIM}${num}${NC} ${label}" ;;
        2)  ST_SKIP=$((ST_SKIP + 1))
            # ГРАБЛИ: причина стояла В ТОЙ ЖЕ строке, что и метка. Метка до
            # 60 символов плюс причина до 67 («счётчики бэкендов dnsdist
            # недоступны…») давали до 148 колонок — почти вдвое шире окна,
            # и хвост уезжал в колонку 0. Причина — такая же нота, как всё
            # остальное, что проверка хочет сказать: своя строка под «└ ».
            echo -e "  ${DIM}—${NC}  ${DIM}${num}${NC} ${DIM}${label}${NC}"
            if [ -n "$ST_REASON" ]; then
                echo -e "        ${DIM}└ $(str_wrap 70 10 "$(t test_skipped): ${ST_REASON}")${NC}"
            fi ;;
        3)  ST_INFO=$((ST_INFO + 1))
            echo -e "  ${CYAN}i${NC}  ${DIM}${num}${NC} ${label}" ;;
        *)  ST_FAIL=$((ST_FAIL + 1))
            ST_FAILED+=("${num} ${label}")
            echo -e "  ${RED}✗${NC}  ${DIM}${num}${NC} ${BOLD}${label}${NC}" ;;
    esac

    # Нота идёт под отступом «        └ » — это 10 колонок, значит на текст
    # остаётся 70 из восьмидесяти. Терминал переносит жёстко: хвост уезжает
    # в колонку 0 и читается как отдельная запись верхнего уровня, а не как
    # продолжение. Часть нот вообще не текст из каталога, а обрезанный до
    # 170 символов вывод чужой команды — их короче не сделаешь никаким
    # переводом. Поэтому переносим сами, с отступом под «└ ».
    for n in "${ST_NOTES[@]:-}"; do
        if [ -n "$n" ]; then
            echo -e "        ${DIM}└ $(str_wrap 70 10 "$n")${NC}"
        fi
    done
    return 0
}

st_group_header() {
    local g="$1"
    if [ "$g" != "$ST_GROUP_SEEN" ]; then
        ST_GROUP_SEEN="$g"
        echo ""
        echo -e "  ${BLUE}${BOLD}$(t "$g")${NC}"
    fi
    return 0
}

st_run_suite() {
    local item fn labelkey groupkey
    for item in "${ST_SUITE[@]:-}"; do
        [ -n "$item" ] || continue
        fn="${item%%|*}"
        labelkey="${item#*|}"; labelkey="${labelkey%%|*}"
        groupkey="${item##*|}"
        st_group_header "$groupkey"
        st_run_check "$fn" "$labelkey"
    done
    return 0
}

st_print_tally() {
    local f
    echo ""
    print_rule
    echo -e "  ${GREEN}$(tf test_tally_pass "$ST_PASS")${NC}   ${RED}$(tf test_tally_fail "$ST_FAIL")${NC}   ${DIM}$(tf test_tally_skip "$ST_SKIP")${NC}   ${CYAN}$(tf test_tally_info "$ST_INFO")${NC}"
    if [ "$ST_FAIL" -gt 0 ]; then
        echo ""
        echo -e "  ${RED}$(t test_failed_list)${NC}"
        for f in "${ST_FAILED[@]:-}"; do
            if [ -n "$f" ]; then
                echo -e "    ${RED}·${NC} $f"
            fi
        done
    fi
    print_rule
    if [ "$ST_FAIL" -gt 0 ]; then
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 1. СЛУШАЕТ И ДОСТУПЕН
# ═══════════════════════════════════════════════════════════════════════

st_chk_listen_generic() {
    local port="$1" what="$2" lines=""
    st_have ss || { st_skip "$(t test_reason_no_ss)"; return $?; }
    if [ -z "$port" ]; then
        st_skip "$(t test_reason_no_port)"; return $?
    fi
    lines=$(st_listen_lines "$port") || lines=""
    if [ -z "$lines" ]; then
        st_note "$(tf test_listen_none "$what" "$port")"
        return 1
    fi
    st_note "$(st_trim "$(printf '%s' "$lines" | tr '\n' ' ')" 170)"
    return 0
}

st_chk_listen_doh() { st_chk_listen_generic "$ST_DOH_PORT" "DoH"; }
st_chk_listen_dot() {
    selftest_dot_on || { st_skip "$(t test_reason_dot_off)"; return $?; }
    st_chk_listen_generic "$ST_DOT_PORT" "DoT"
}

# Сокет принадлежит именно нашему юниту, а не случайному nginx на том же
# порту, оставшемуся от прошлой жизни сервера.
st_chk_socket_owner() {
    local mainpid="" port lines="" bad="" seen=0
    st_have ss || { st_skip "$(t test_reason_no_ss)"; return $?; }
    st_have systemctl || { st_skip "$(t test_reason_no_systemctl)"; return $?; }

    mainpid=$(systemctl show -p MainPID --value "$GODNS_SERVICE" 2>/dev/null) || mainpid=""
    case "$mainpid" in
        ''|0|*[!0-9]*) st_skip "$(tf test_reason_no_mainpid "$GODNS_SERVICE")"; return $? ;;
    esac

    for port in "$ST_DOH_PORT" "$(selftest_dot_on && printf %s "$ST_DOT_PORT")"; do
        [ -n "$port" ] || continue
        lines=$(st_listen_lines "$port") || lines=""
        # Технический маркер, не проза: пользовательский текст живёт в t/tf.
        [ -n "$lines" ] || { bad="${bad} ${port}:no-socket"; continue; }
        if ! grep -q 'users:' <<< "$lines"; then
            # без root ss не показывает процесс — это не провал, это слепота
            st_skip "$(t test_reason_ss_noproc)"; return $?
        fi
        seen=$((seen + 1))
        case "$lines" in
            *"pid=${mainpid},"*|*"pid=${mainpid})"*) : ;;
            *) bad="${bad} ${port}" ;;
        esac
    done

    if [ "$seen" -eq 0 ]; then
        st_note "$(t test_owner_no_sockets)"
        return 1
    fi
    st_note "$(tf test_owner_mainpid "$GODNS_SERVICE" "$mainpid")"
    if [ -n "$bad" ]; then
        st_note "$(tf test_owner_foreign "${bad# }")"
        return 1
    fi
    return 0
}

# Самая частая молчаливая поломка: демон слушает 127.0.0.1 и «всё работает»
# ровно до момента, когда клиент приходит снаружи.
st_chk_not_loopback() {
    local port lines="" line addr="" public=0 addrs="" anyport=0 failed=""
    st_have ss || { st_skip "$(t test_reason_no_ss)"; return $?; }

    for port in "$ST_DOH_PORT" "$(selftest_dot_on && printf %s "$ST_DOT_PORT")"; do
        [ -n "$port" ] || continue
        lines=$(st_listen_lines "$port") || lines=""
        [ -n "$lines" ] || continue
        anyport=1
        public=0
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            addr=$(st_addr_of_line "$line")
            addrs="${addrs} ${addr}:${port}"
            case "$addr" in
                '0.0.0.0'|'*'|'[::]'|'::'|'[::]%'*) public=1 ;;
                '127.'*|'[::1]'|'::1'|'localhost')  : ;;
                '')                                  : ;;
                *)                                   public=1 ;;
            esac
        done <<< "$lines"
        if [ "$public" -eq 0 ]; then
            failed="${failed} ${port}"
        fi
    done

    if [ "$anyport" -eq 0 ]; then
        st_note "$(t test_loopback_nothing)"
        return 1
    fi
    st_note "$(st_trim "${addrs# }" 170)"
    if [ -n "$failed" ]; then
        st_note "$(tf test_loopback_only "${failed# }")"
        return 1
    fi
    return 0
}

# Информационно: заглушка systemd-resolved на 127.0.0.53:53. Это не провал —
# она мешает только если мы сами хотим сесть на :53.
st_chk_resolved_stub() {
    local out=""
    st_have ss || { st_skip "$(t test_reason_no_ss)"; return $?; }
    out=$(ss -Hlnup 2>/dev/null) || out=""
    # На Ubuntu ss печатает адрес заглушки с суффиксом интерфейса —
    # `127.0.0.53%lo:53`, а не `127.0.0.53:53`. Шаблон без него молча
    # сообщает «заглушки нет» на машине, где она есть.
    if grep -qE '127\.0\.0\.53(%[^:[:space:]]+)?:53' <<< "$out"; then
        st_note "$(t test_stub_present)"
    else
        st_note "$(t test_stub_absent)"
    fi
    return 3
}

# Локальный межсетевой экран. Доказывает ТОЛЬКО локальное правило: облачная
# security group живёт вне этой машины и отсюда не видна.
st_chk_firewall_local() {
    local ufw_out="" nft_out="" active=0 missing="" port ports_seen=""
    st_note "$(t test_fw_local_only)"

    # Разбор БЕЗ ранневыходных каналов: `... | grep -q` убивает producer
    # сигналом SIGPIPE, и под pipefail «нашлось» читается как «не нашлось».
    if st_have ufw; then
        # LC_ALL=C обязателен: «Status: active» переведён gettext'ом, и на
        # русской локали активный ufw читался бы как «inactive» — ложный
        # отказ ровно того класса, из-за которого _fw_open читает конфиг.
        ufw_out=$(LC_ALL=C ufw status 2>/dev/null) || ufw_out=""
        if grep -qi 'Status: active' <<< "$ufw_out"; then
            active=1
            ports_seen=" $(grep -E 'ALLOW|LIMIT' <<< "$ufw_out" | tr -c '0-9' ' ') "
            for port in "$ST_DOH_PORT" "$(selftest_dot_on && printf %s "$ST_DOT_PORT")"; do
                [ -n "$port" ] || continue
                case "$ports_seen" in
                    *" ${port} "*) : ;;
                    *) missing="${missing} ${port}" ;;
                esac
            done
            st_note "ufw: active"
        else
            st_note "ufw: inactive"
        fi
    fi

    if [ "$active" -eq 0 ] && st_have nft; then
        nft_out=$(nft list ruleset 2>/dev/null) || nft_out=""
        if [ -z "$nft_out" ]; then
            st_note "$(t test_fw_nft_unreadable)"
        elif grep -qE 'policy[[:space:]]+drop' <<< "$nft_out"; then
            active=1
            ports_seen=" $(grep -E 'dport' <<< "$nft_out" | tr -c '0-9' ' ') "
            for port in "$ST_DOH_PORT" "$(selftest_dot_on && printf %s "$ST_DOT_PORT")"; do
                [ -n "$port" ] || continue
                case "$ports_seen" in
                    *" ${port} "*) : ;;
                    *) missing="${missing} ${port}" ;;
                esac
            done
            st_note "nftables: policy drop"
        else
            st_note "nftables: policy accept"
        fi
    fi

    if [ "$active" -eq 0 ]; then
        st_note "$(t test_fw_none_active)"
        return 0
    fi
    if [ -n "$missing" ]; then
        st_note "$(tf test_fw_missing "${missing# }")"
        return 1
    fi
    return 0
}

# ── Внешняя доступность ─────────────────────────────────────────────────
# Отсюда доказуемо: сокет открыт и локальный фильтр пропускает. Что до нас
# дотянется интернет — отсюда НЕ доказуемо, нужна точка наблюдения снаружи.
st_check_host_net() {
    local host="$1" port="$2" resp="" rid="" i=0 res="" prc=0
    st_have curl || return 1
    resp=$(st_timeout 20 curl -sS -m 15 -H 'Accept: application/json' \
           "https://check-host.net/check-tcp?host=${host}:${port}&max_nodes=3" 2>/dev/null) || resp=""
    [ -n "$resp" ] || return 1
    rid=$(printf '%s' "$resp" | python3 -c 'import sys,json
try:
    sys.stdout.write(json.load(sys.stdin).get("request_id",""))
except Exception:
    pass' 2>/dev/null) || rid=""
    [ -n "$rid" ] || return 1

    i=0
    while [ "$i" -lt 6 ]; do
        i=$((i + 1))
        sleep 3
        res=$(st_timeout 20 curl -sS -m 15 -H 'Accept: application/json' \
              "https://check-host.net/check-result/${rid}" 2>/dev/null) || res=""
        [ -n "$res" ] || continue
        printf '%s' "$res" > "$ST_SCRATCH"
        prc=0
        python3 - "$ST_SCRATCH" <<'PY' || prc=$?
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(2)
if not isinstance(d, dict) or not d:
    raise SystemExit(2)
pending = 0
ok = 0
lines = []
for node, val in sorted(d.items()):
    short = node.split(".")[0]
    if val is None:
        pending += 1
        lines.append("%s: ..." % short)
        continue
    entry = val[0] if isinstance(val, list) and val else val
    if isinstance(entry, dict) and "address" in entry:
        ok += 1
        lines.append("%s: open %.0f ms" % (short, float(entry.get("time", 0)) * 1000))
    elif isinstance(entry, dict) and "error" in entry:
        lines.append("%s: %s" % (short, str(entry["error"])[:40]))
    else:
        lines.append("%s: %s" % (short, str(entry)[:40]))
if pending:
    raise SystemExit(2)
sys.stdout.write(" | ".join(lines))
raise SystemExit(0 if ok else 1)
PY
        # 0 = хотя бы один узел достучался; 1 = все закрыты;
        # 2 = узлы ещё не отчитались — ждём следующий круг.
        if [ "$prc" -eq 0 ]; then return 0; fi
        if [ "$prc" -eq 1 ]; then return 1; fi
        # prc=2 — ответа ещё нет, ждём следующий круг.
    done
    # Круги кончились, а годного ответа так и не было: это «узнать не
    # удалось», а не «порт закрыт». Отдельный код, чтобы вызывающий не
    # выдал сильнейшее утверждение при нулевых данных.
    return 2
}

st_chk_external_reach() {
    local out="" rc=0
    if [ "$ST_ALLOW_THIRDPARTY" != "1" ]; then
        st_note "$(t test_ext_honest)"
        st_note "$(t test_ext_laptop_hint)"
        st_note "  kdig +tls-ca +tls-hostname=${ST_DOMAIN} @${ST_DOMAIN} -p ${ST_DOT_PORT} example.com A"
        st_skip "$(t test_reason_ext_optin)"
        return $?
    fi
    st_note "$(t test_ext_discloses)"

    # ГРАБЛИ: проверялся ТОЛЬКО порт DoT — а он выключен по умолчанию у
    # всех новых установок. Оператор с исправным DoH видел «снаружи
    # закрыт» и шёл искать несуществующую поломку. Главное здесь — DoH:
    # это тот адрес, которым пользуются клиенты. DoT добавляем к проверке
    # лишь когда он включён.
    local doh_rc=0 dot_rc=0 dot_out=""
    out=$(st_check_host_net "$ST_DOMAIN" "$ST_DOH_PORT") || doh_rc=$?
    [ -n "$out" ] && st_note "DoH :${ST_DOH_PORT} — $(st_trim "$out" 150)"

    if selftest_dot_on; then
        dot_out=$(st_check_host_net "$ST_DOMAIN" "$ST_DOT_PORT") || dot_rc=$?
        [ -n "$dot_out" ] && st_note "DoT :${ST_DOT_PORT} — $(st_trim "$dot_out" 150)"
    else
        st_note "$(t test_reason_dot_off)"
    fi

    # ГРАБЛИ вторые: «сторонний сервис не ответил» выдавалось теми же
    # словами, что «все узлы получили отказ». Это разные утверждения:
    # первое — про сервис, второе — про наш порт. Код 2 означает «узнать
    # не удалось», и это НАБЛЮДЕНИЕ, а не приговор нашему серверу.
    if [ "$doh_rc" -eq 2 ] || { selftest_dot_on && [ "$dot_rc" -eq 2 ]; }; then
        st_note "$(t test_ext_nodata)"
        return 3
    fi
    if [ "$doh_rc" -ne 0 ] || { selftest_dot_on && [ "$dot_rc" -ne 0 ]; }; then
        st_note "$(t test_ext_failed)"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 2. DoH
# ═══════════════════════════════════════════════════════════════════════

st_doh_url() {
    local path="$1"
    printf 'https://%s:%s/%s' "$ST_DOMAIN" "$ST_DOH_PORT" "$path"
}

# Одна curl-проба. Заполняет ST_CODE/ST_VER/ST_CT/ST_TLSV, тело кладёт в
# $ST_R_BIN. Если имя локально не резолвится — повторяет с --resolve и
# поднимает ST_PINNED (об этом потом пишем в отчёт, а не молчим).
st_curl_probe() {
    local url="$1"; shift
    local out="" rc=0 k v
    ST_CODE=""; ST_VER=""; ST_CT=""; ST_TLSV=""; ST_PINNED=0
    st_have curl || return 127

    # ВАЖНО: "${arr[@]:-}" на ПУСТОМ массиве даёт один ПУСТОЙ аргумент,
    # и curl принимает его за второй URL. Собираем argv вручную.
    local base=(-sS --http2 -m 12 -o "$ST_R_BIN"
        -w 'code=%{http_code}\nver=%{http_version}\nct=%{content_type}\ntls=%{ssl_verify_result}\n')
    local args=("${base[@]}")
    if [ ${#ST_RESOLVE_ARGS[@]} -gt 0 ]; then args+=("${ST_RESOLVE_ARGS[@]}"); fi
    args+=("$@" "$url")

    out=$(st_timeout 25 curl "${args[@]}" 2>/dev/null) || rc=$?

    if { [ "$rc" -ne 0 ] || [ -z "$out" ]; } \
       && [ ${#ST_RESOLVE_ARGS[@]} -eq 0 ] && [ -n "$ST_PUBIP" ]; then
        rc=0
        args=("${base[@]}" --resolve "${ST_DOMAIN}:${ST_DOH_PORT}:${ST_PUBIP}" "$@" "$url")
        out=$(st_timeout 25 curl "${args[@]}" 2>/dev/null) || rc=$?
        ST_PINNED=1
    fi

    # Разбор БЕЗ каналов: `sed | head` под pipefail — мина (SIGPIPE).
    while IFS='=' read -r k v; do
        case "$k" in
            code) ST_CODE="$v" ;;
            ver)  ST_VER="$v" ;;
            ct)   ST_CT="$v" ;;
            tls)  ST_TLSV="$v" ;;
        esac
    done <<< "$out"

    if [ -z "$ST_CODE" ]; then ST_CODE="000"; fi
    return "$rc"
}

st_chk_doh_get() {
    local rc=0 fails=""
    [ -n "$ST_DOH_PATH" ] || { st_skip "$(t test_reason_no_path)"; return $?; }
    st_have curl || { st_skip "$(t test_reason_no_curl)"; return $?; }

    st_curl_probe "$(st_doh_url "$ST_DOH_PATH")?dns=${ST_Q_B64}" \
        -H 'accept: application/dns-message' || rc=$?

    st_note "code=${ST_CODE} http/${ST_VER:-?} ct=${ST_CT:-?} ssl_verify=${ST_TLSV:-?}"
    if [ "$ST_PINNED" = "1" ]; then
        st_note "$(tf test_name_pinned "$ST_DOMAIN" "$ST_PUBIP")"
    fi

    [ "$ST_CODE" = "200" ] || fails="${fails} code!=200"
    case "$ST_CT" in *application/dns-message*) : ;; *) fails="${fails} content-type" ;; esac
    [ "$ST_VER" = "2" ] || fails="${fails} not-http/2"
    [ "$ST_TLSV" = "0" ] || fails="${fails} tls-verify"

    if [ -n "$fails" ]; then
        st_note "$(tf test_doh_get_bad "${fails# }")"
        return 1
    fi
    cp -f "$ST_R_BIN" "$ST_R_GET" 2>/dev/null || true
    return 0
}

# 200 сам по себе ничего не значит: HTML-страница ошибки тоже бывает 200.
# Разбираем проводной формат.
st_chk_doh_wire() {
    local out="" rc=0
    if [ ! -s "$ST_R_GET" ]; then
        st_skip "$(t test_reason_no_body)"; return $?
    fi
    out=$(st_parse_wire "$ST_R_GET") || rc=$?
    st_note "$(st_trim "$out" 170)"
    if [ "$rc" -ne 0 ]; then
        st_note "$(t test_wire_bad)"
        return 1
    fi
    return 0
}

st_chk_doh_post() {
    local rc=0 fails="" out="" prc=0
    [ -n "$ST_DOH_PATH" ] || { st_skip "$(t test_reason_no_path)"; return $?; }
    st_have curl || { st_skip "$(t test_reason_no_curl)"; return $?; }

    st_curl_probe "$(st_doh_url "$ST_DOH_PATH")" \
        -H 'content-type: application/dns-message' \
        -H 'accept: application/dns-message' \
        --data-binary "@${ST_Q_BIN}" || rc=$?

    st_note "code=${ST_CODE} ct=${ST_CT:-?}"
    [ "$ST_CODE" = "200" ] || fails="${fails} code!=200"
    case "$ST_CT" in *application/dns-message*) : ;; *) fails="${fails} content-type" ;; esac

    # Побайтовое совпадение GET и POST НЕ требуется: TTL уезжает.
    if [ -s "$ST_R_BIN" ]; then
        out=$(st_parse_wire "$ST_R_BIN") || prc=$?
        st_note "$(st_trim "$out" 170)"
        [ "$prc" -eq 0 ] || fails="${fails} wire"
    else
        fails="${fails} empty-body"
    fi

    if [ -n "$fails" ]; then
        st_note "$(tf test_doh_post_bad "${fails# }")"
        return 1
    fi
    return 0
}

# 200 на /dns-query означает, что маршрут по секретному пути НЕ включён и
# резолвер открыт всему интернету по общеизвестному адресу.
st_chk_doh_wrong_paths() {
    local p url bad="" line=""
    [ -n "$ST_DOH_PATH" ] || { st_skip "$(t test_reason_no_path)"; return $?; }
    st_have curl || { st_skip "$(t test_reason_no_curl)"; return $?; }

    for p in "dns-query" "" "wrong" "${ST_DOH_PATH}/extra" "${ST_DOH_PATH}x"; do
        url="$(st_doh_url "$p")?dns=${ST_Q_B64}"
        st_curl_probe "$url" -H 'accept: application/dns-message' || true
        line="${line} /${p}=${ST_CODE}"
        case "$ST_CODE" in
            4*) : ;;
            *)  bad="${bad} /${p}:${ST_CODE}" ;;
        esac
    done

    st_note "$(st_trim "${line# }" 170)"
    if [ -n "$bad" ]; then
        st_note "$(tf test_paths_open "${bad# }")"
        return 1
    fi
    return 0
}

st_chk_doh_malformed() {
    local bad="" line=""
    [ -n "$ST_DOH_PATH" ] || { st_skip "$(t test_reason_no_path)"; return $?; }
    st_have curl || { st_skip "$(t test_reason_no_curl)"; return $?; }

    # 1) base64url с набивкой «%3D» — по RFC 8484 набивки быть не должно.
    # Набивку НЕ считаем провалом. RFC 8484 говорит, что её быть не должно,
    # но приём набитого запроса — снисходительность, а не дыра: декодируется
    # тот же самый запрос. Quad9 отвечает 403, dnsdist — 200; наказывать
    # goDNS за поведение чужой реализации нечестно. Замечаем и идём дальше.
    st_curl_probe "$(st_doh_url "$ST_DOH_PATH")?dns=${ST_Q_B64}%3D" || true
    line="${line} padded=${ST_CODE}"
    case "$ST_CODE" in
        4*) : ;;
        *)  st_note "$(tf test_malformed_padded_lenient "$ST_CODE")" ;;
    esac

    # 2) мусор вместо base64
    st_curl_probe "$(st_doh_url "$ST_DOH_PATH")?dns=!!!!" || true
    line="${line} garbage=${ST_CODE}"
    case "$ST_CODE" in 4*) : ;; *) bad="${bad} garbage:${ST_CODE}" ;; esac

    # 3) вовсе без параметра dns=
    st_curl_probe "$(st_doh_url "$ST_DOH_PATH")" || true
    line="${line} nodns=${ST_CODE}"
    case "$ST_CODE" in 4*) : ;; *) bad="${bad} nodns:${ST_CODE}" ;; esac

    st_note "$(st_trim "${line# }" 170)"
    # 415 на «неправильный» POST content-type НЕ проверяем: Quad9 отвечает
    # на application/json кодом 200. Утверждать можно только про
    # content-type ОТВЕТА, что и делает проверка POST.
    st_note "$(t test_malformed_note_415)"
    if [ -n "$bad" ]; then
        st_note "$(tf test_malformed_accepted "${bad# }")"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 3. TLS
# ═══════════════════════════════════════════════════════════════════════

st_s_client() {
    local port="$1"; shift
    : > "$ST_TLS_OUT"
    st_timeout 20 openssl s_client -connect "${ST_CONNECT_HOST}:${port}" \
        -servername "$ST_DOMAIN" "$@" </dev/null >"$ST_TLS_OUT" 2>&1
}

st_chk_tls_generic() {
    local port="$1" what="$2" rc=0 proto=""
    st_have openssl || { st_skip "$(t test_reason_no_openssl)"; return $?; }
    [ -n "$port" ] || { st_skip "$(t test_reason_no_port)"; return $?; }

    st_s_client "$port" -verify_hostname "$ST_DOMAIN" -brief || rc=$?
    proto=$(awk '/^Protocol version:/{print $3; exit}' "$ST_TLS_OUT" 2>/dev/null) || proto=""
    st_note "${what}:${port}  ${proto:-?}"

    if ! grep -q 'Verification: OK' "$ST_TLS_OUT" 2>/dev/null; then
        st_note "$(st_trim "$(grep -m1 -E 'Verification|verify error|error:' "$ST_TLS_OUT" 2>/dev/null || true)" 170)"
        st_note "$(t test_tls_verify_fail)"
        return 1
    fi
    if ! grep -q 'Verified peername' "$ST_TLS_OUT" 2>/dev/null; then
        st_note "$(tf test_tls_peername_fail "$ST_DOMAIN")"
        return 1
    fi
    return 0
}

st_chk_tls_doh() { st_chk_tls_generic "$ST_DOH_PORT" "DoH"; }
st_chk_tls_dot() {
    selftest_dot_on || { st_skip "$(t test_reason_dot_off)"; return $?; }
    st_chk_tls_generic "$ST_DOT_PORT" "DoT"
}

# Вытянуть лист-сертификат в PEM. awk, а не `sed | head`: awk сам читает
# файл до `exit`, никакого SIGPIPE.
st_fetch_cert() {
    local port="${1:-$ST_DOH_PORT}"
    st_have openssl || return 1
    st_s_client "$port" >/dev/null 2>&1 || true
    : > "$ST_CERT_PEM"
    awk '/-----BEGIN CERTIFICATE-----/{f=1}
         f{print}
         /-----END CERTIFICATE-----/{if(f) exit}' "$ST_TLS_OUT" > "$ST_CERT_PEM" 2>/dev/null || true
    if [ ! -s "$ST_CERT_PEM" ]; then
        return 1
    fi
    openssl x509 -in "$ST_CERT_PEM" -noout >/dev/null 2>&1
}

# Цепочка должна вести к ПУБЛИЧНОМУ УЦ: локально добавленный корень даёт
# «Verification: OK» и при этом ничего не значит для клиента из интернета.
st_chk_tls_public_ca() {
    local bundle="$ST_CA_BUNDLE" rc=0 subj="" iss=""
    st_have openssl || { st_skip "$(t test_reason_no_openssl)"; return $?; }
    if [ ! -r "$bundle" ]; then
        st_skip "$(tf test_reason_no_ca_bundle "$bundle")"; return $?
    fi

    st_s_client "$ST_DOH_PORT" -CAfile "$bundle" -verify_return_error -brief >/dev/null 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
        st_note "$(st_trim "$(grep -m1 -E 'verify error|Verification error|error:' "$ST_TLS_OUT" 2>/dev/null || true)" 170)"
        st_note "$(t test_tls_not_public)"
        return 1
    fi

    if ! st_fetch_cert "$ST_DOH_PORT"; then
        st_note "$(t test_tls_no_cert)"
        return 1
    fi
    subj=$(openssl x509 -in "$ST_CERT_PEM" -noout -subject 2>/dev/null) || subj=""
    iss=$(openssl x509 -in "$ST_CERT_PEM" -noout -issuer 2>/dev/null) || iss=""
    st_note "$(st_trim "${iss}" 170)"
    if [ "${subj#subject=}" = "${iss#issuer=}" ]; then
        st_note "$(t test_tls_self_signed)"
        return 1
    fi
    return 0
}

# notAfter — по КОДУ ВОЗВРАТА openssl x509 -checkend, а не разбором строки.
st_chk_tls_expiry() {
    local rc=0 end="" days=""
    st_have openssl || { st_skip "$(t test_reason_no_openssl)"; return $?; }
    if ! st_fetch_cert "$ST_DOH_PORT"; then
        st_note "$(t test_tls_no_cert)"
        return 1
    fi

    end=$(openssl x509 -in "$ST_CERT_PEM" -noout -enddate 2>/dev/null) || end=""
    if [ -n "$end" ]; then
        days=$(python3 - "$end" <<'PY' 2>/dev/null
import sys, datetime
s = " ".join(sys.argv[1].replace("notAfter=", "").split())
d = None
for fmt in ("%b %d %H:%M:%S %Y %Z", "%b %d %H:%M:%S %Y"):
    try:
        d = datetime.datetime.strptime(s, fmt); break
    except ValueError:
        d = None
if d is None:
    sys.stdout.write("?")
else:
    sys.stdout.write(str(int((d - datetime.datetime.utcnow()).total_seconds() // 86400)))
PY
        ) || days=""
    fi
    st_note "${end:-notAfter=?}  ($(tf test_tls_days_left "${days:-?}"))"

    openssl x509 -in "$ST_CERT_PEM" -noout -checkend 604800 >/dev/null 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
        st_note "$(t test_tls_expiring)"
        return 1
    fi
    case "$days" in
        ''|*[!0-9-]*) : ;;
        *) if [ "$days" -lt 30 ]; then st_note "$(t test_tls_renew_soon)"; fi ;;
    esac
    return 0
}

# Android Private DNS шлёт ТОЛЬКО имя хоста. Нет этого имени в SAN — на
# телефоне будет немое «Couldn't connect» без единой подсказки.
st_chk_tls_san_android() {
    local san="" names="" n rc=1 suffix=""
    # Проверка про Android «Частный DNS», а он работает только по DoT.
    selftest_dot_on || { st_skip "$(t test_reason_dot_off)"; return $?; }
    st_have openssl || { st_skip "$(t test_reason_no_openssl)"; return $?; }
    if ! st_fetch_cert "$ST_DOT_PORT"; then
        st_note "$(t test_tls_no_cert)"
        return 1
    fi
    san=$(openssl x509 -in "$ST_CERT_PEM" -noout -ext subjectAltName 2>/dev/null) || san=""
    if [ -z "$san" ]; then
        st_note "$(t test_san_absent)"
        return 1
    fi
    names=$(printf '%s\n' "$san" | tr ',' '\n' | sed -n 's/^[[:space:]]*DNS://p' | tr -d ' ') || names=""

    while IFS= read -r n; do
        [ -n "$n" ] || continue
        if [ "$n" = "$ST_DOMAIN" ]; then rc=0; break; fi
        case "$n" in
            '*.'*)
                # *.example.com покрывает РОВНО один уровень слева:
                # dns.example.com — да, a.b.example.com — нет.
                suffix="${n#\*.}"
                if [ "${ST_DOMAIN#*.}" = "$suffix" ] && [ "$ST_DOMAIN" != "$suffix" ]; then
                    rc=0; break
                fi
                ;;
        esac
    done <<< "$names"

    st_note "SAN: $(st_trim "$(printf '%s' "$names" | tr '\n' ' ')" 150)"
    if [ "$rc" -ne 0 ]; then
        st_note "$(tf test_san_missing "$ST_DOMAIN")"
        return 1
    fi
    st_note "$(t test_san_android_ok)"
    return 0
}

# ALPN h2 — только на DoH-порту. На 853 значения ALPN НЕ проверяем:
# RFC 7858 его не требует, Quad9 не согласовывает ничего.
st_chk_tls_alpn_h2() {
    local alpn=""
    st_have openssl || { st_skip "$(t test_reason_no_openssl)"; return $?; }
    st_s_client "$ST_DOH_PORT" -alpn h2 >/dev/null 2>&1 || true
    alpn=$(awk '/^ALPN protocol:/{print $3; exit}' "$ST_TLS_OUT" 2>/dev/null) || alpn=""
    st_note "ALPN(${ST_DOH_PORT}) = ${alpn:-—}"
    st_note "$(t test_alpn_dot_note)"
    if [ "$alpn" != "h2" ]; then
        st_note "$(t test_alpn_missing)"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 4. РАЗРЕШЕНИЕ ИМЁН И DNSSEC
# ═══════════════════════════════════════════════════════════════════════

st_ensure_kdig() {
    if st_have kdig; then return 0; fi
    if [ "$(id -u)" -ne 0 ]; then return 1; fi
    ensure_packages knot-dnsutils >/dev/null 2>&1 || return 1
    st_have kdig
}

st_pick_transport() {
    ST_QTRANSPORT=""
    st_have kdig || return 1
    # ГРАБЛИ: условием было «ST_DOT_PORT непуст», а он непуст ВСЕГДА —
    # selftest_load_ctx подставляет 853 по умолчанию, потому что порт
    # хранится в конфиге и при выключенном DoT. На установке по умолчанию
    # (DoT выключен — это дефолт мастера) обе проверки DNSSEC били в
    # :853, где никто не слушает, и печатали приговор «резолвер просто
    # мёртв» при исправном резолвере: DoH в том же прогоне отвечал.
    # Спрашиваем состояние, а не наличие числа.
    if [ -n "$ST_DOT_PORT" ] && selftest_dot_on; then ST_QTRANSPORT="dot"; return 0; fi
    if [ -n "$ST_DOH_PORT" ] && [ -n "$ST_DOH_PATH" ]; then ST_QTRANSPORT="doh"; return 0; fi
    return 1
}

# Запрос через НАШ транспорт (не plain dig на 53). Вывод — в $ST_DIG_OUT.
st_query() {
    : > "$ST_DIG_OUT"
    case "$ST_QTRANSPORT" in
        dot)
            st_timeout 25 kdig +tls-ca "+tls-hostname=${ST_DOMAIN}" \
                "@${ST_CONNECT_HOST}" -p "$ST_DOT_PORT" \
                +timeout=6 +retry=1 "$@" >"$ST_DIG_OUT" 2>&1
            ;;
        doh)
            st_timeout 25 kdig +tls-ca "+https=/${ST_DOH_PATH}" "+tls-hostname=${ST_DOMAIN}" \
                "@${ST_CONNECT_HOST}" -p "$ST_DOH_PORT" \
                +timeout=6 +retry=1 "$@" >"$ST_DIG_OUT" 2>&1
            ;;
        *) return 1 ;;
    esac
}

st_chk_resolve_dot() {
    local st="" an=""
    selftest_dot_on || { st_skip "$(t test_reason_dot_off)"; return $?; }
    if ! st_ensure_kdig; then st_skip "$(t test_reason_no_kdig)"; return $?; fi
    [ -n "$ST_DOT_PORT" ] || { st_skip "$(t test_reason_no_port)"; return $?; }
    ST_QTRANSPORT="dot"
    st_query example.com A || true
    st=$(st_dns_status "$ST_DIG_OUT") || st=""
    an=$(st_dns_ancount "$ST_DIG_OUT") || an=""
    st_note "DoT :${ST_DOT_PORT}  status=${st:-—} answers=${an:-0}"
    if [ "$st" != "NOERROR" ]; then
        st_note "$(st_trim "$(grep -m1 -iE 'error|failed|refused' "$ST_DIG_OUT" 2>/dev/null || true)" 170)"
        return 1
    fi
    case "$an" in ''|0|*[!0-9]*) return 1 ;; esac
    return 0
}

st_chk_resolve_doh() {
    local st="" an=""
    if ! st_ensure_kdig; then st_skip "$(t test_reason_no_kdig)"; return $?; fi
    [ -n "$ST_DOH_PATH" ] || { st_skip "$(t test_reason_no_path)"; return $?; }
    ST_QTRANSPORT="doh"
    st_query example.com A || true
    st=$(st_dns_status "$ST_DIG_OUT") || st=""
    an=$(st_dns_ancount "$ST_DIG_OUT") || an=""
    st_note "DoH :${ST_DOH_PORT}/${ST_DOH_PATH}  status=${st:-—} answers=${an:-0}"
    st_note "$(st_trim "$(grep -m1 'HTTP session' "$ST_DIG_OUT" 2>/dev/null || true)" 170)"
    st_pick_transport || true
    if [ "$st" != "NOERROR" ]; then
        return 1
    fi
    case "$an" in ''|0|*[!0-9]*) return 1 ;; esac
    return 0
}

# Пара «сломано / контроль». Без контроля мёртвый резолвер тоже «проходит»
# проверку на SERVFAIL — он отвечает SERVFAIL вообще на всё.
st_dnssec_pair() {
    local badname="$1" mode="$2" goodname="$3"
    local st_bad="" st_good="" an_good=""

    st_query "$badname" A || true
    st_bad=$(st_dns_status "$ST_DIG_OUT") || st_bad=""

    if [ "$mode" = "cd" ]; then
        st_query +cdflag "$badname" A || true
    else
        st_query "$goodname" A || true
    fi
    st_good=$(st_dns_status "$ST_DIG_OUT") || st_good=""
    an_good=$(st_dns_ancount "$ST_DIG_OUT") || an_good=""

    if [ "$mode" = "cd" ]; then
        st_note "${badname}: ${st_bad:-—}   +cdflag: ${st_good:-—} (answers=${an_good:-0})"
    else
        st_note "${badname}: ${st_bad:-—}   ${goodname}: ${st_good:-—} (answers=${an_good:-0})"
    fi

    if [ "$st_bad" != "SERVFAIL" ]; then
        st_note "$(tf test_dnssec_no_servfail "$badname" "${st_bad:-—}")"
        return 1
    fi
    if [ "$st_good" != "NOERROR" ]; then
        st_note "$(t test_dnssec_control_bad)"
        return 1
    fi
    case "$an_good" in ''|0|*[!0-9]*) st_note "$(t test_dnssec_control_empty)"; return 1 ;; esac
    return 0
}

# ГРАБЛИ, найденные на живом сервере: при нескольких апстримах результат
# НЕ детерминирован. Из десяти выбранных резолверов восемь проверяли подписи,
# а оба Яндекса — нет; какой ответит, решает политика выбора. Один прогон
# поэтому доказывает не «DNSSEC работает», а «сегодня повезло».
#
# Гоняем пару несколько раз. Все прогоны SERVFAIL — защита есть. Ни одного —
# её нет. Вперемешку — это ХУЖЕ, чем кажется: злоумышленнику достаточно
# дождаться, когда ответит непроверяющий апстрим, поэтому говорим об этом
# прямо и валим проверку.
ST_DNSSEC_ROUNDS="${ST_DNSSEC_ROUNDS:-5}"

st_chk_dnssec_failed_org() {
    if ! st_ensure_kdig; then st_skip "$(t test_reason_no_kdig)"; return $?; fi
    st_pick_transport || { st_skip "$(t test_reason_no_transport)"; return $?; }
    st_note "$(t test_dnssec_who_validates)"

    local i=1 servfail=0 other=0 st="" flushed="no"
    while [ "$i" -le "$ST_DNSSEC_ROUNDS" ]; do
        # БЕЗ сброса кэша тест наблюдает не резолвер, а собственный кэш:
        # один осевший там ответ раздаётся весь TTL, и исправный резолвер
        # выглядит дырявым (проверено на живом сервере — 5 из 5 «провалов»
        # оказались одним кэшированным ответом).
        if declare -F engine_cache_flush >/dev/null 2>&1; then
            engine_cache_flush >/dev/null 2>&1 && flushed="yes"
        fi
        st_query "www.dnssec-failed.org" A || true
        st=$(st_dns_status "$ST_DIG_OUT") || st=""
        if [ "$st" = "SERVFAIL" ]; then servfail=$((servfail + 1)); else other=$((other + 1)); fi
        i=$((i + 1))
    done
    st_note "$(tf test_dnssec_rounds "$ST_DNSSEC_ROUNDS" "$servfail" "$other")"
    if [ "$flushed" != "yes" ]; then st_note "$(t test_dnssec_no_flush)"; fi

    # Контрольный прогон: без него полностью мёртвый резолвер тоже «проходит».
    st_query +cdflag "www.dnssec-failed.org" A || true
    local ctl an
    ctl=$(st_dns_status "$ST_DIG_OUT") || ctl=""
    an=$(st_dns_ancount "$ST_DIG_OUT") || an=""
    if [ "$ctl" != "NOERROR" ]; then st_note "$(t test_dnssec_control_bad)"; return 1; fi
    case "$an" in ''|0|*[!0-9]*) st_note "$(t test_dnssec_control_empty)"; return 1 ;; esac

    if [ "$servfail" -eq "$ST_DNSSEC_ROUNDS" ]; then return 0; fi
    if [ "$servfail" -eq 0 ]; then
        st_note "$(t test_dnssec_none_validates)"
        # DNSSEC валидирует апстрим, не мы. Если в наборе есть свой —
        # виновник, скорее всего, он; назовём это, а не оставим ✗ без имени.
        local _ids=""
        _ids=$(config_get upstream_ids "" 2>/dev/null) || _ids=""
        case "$_ids" in *custom-*) st_note "$(t test_dnssec_custom_hint)" ;; esac
        return 1
    fi
    st_note "$(t test_dnssec_mixed)"
    return 1
}

st_chk_dnssec_verteilte() {
    if ! st_ensure_kdig; then st_skip "$(t test_reason_no_kdig)"; return $?; fi
    st_pick_transport || { st_skip "$(t test_reason_no_transport)"; return $?; }
    st_dnssec_pair "sigfail.verteiltesysteme.net" "pair" "sigok.verteiltesysteme.net"
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 5. НИ ОДНОГО ОТКРЫТОГО DNS НАРУЖУ
# ═══════════════════════════════════════════════════════════════════════

# Нагрузка, под которой имеет смысл смотреть счётчик.
st_leak_workload() {
    local i=1 rnd=""
    st_pick_transport || return 0
    while [ "$i" -le 5 ]; do
        rnd=$(random_path 8) || rnd="x$i"
        st_query "probe-${rnd}.example.com" A >/dev/null 2>&1 || true
        i=$((i + 1))
    done
    st_query example.com A >/dev/null 2>&1 || true
    return 0
}

# Красный крест «поймана утечка» без имени виновника — плохая новость,
# после которой оператор остаётся один. Самый частый виновник известен
# заранее: публичный nameserver в /etc/resolv.conf — на :53 ходит САМА
# система, а не goDNS, и клиентов DoH/DoT это не касается. Проверяем и
# говорим прямо; resolv.conf при этом не правим — он чужой.
# rc 0 — утечку объясняет собственный resolv.conf сервера; rc 1 — нет.
# Различие принципиальное. «Открытый DNS с этой машины» и «goDNS выпускает
# запросы клиентов открытыми» — РАЗНЫЕ утверждения, а проверка красила их
# одинаковым провалом. Оператор на исправном сервере видел ✗ и читал его
# как «резолвер сломан», хотя запросы клиентов идут только по DoH/DoT, а
# на :53 ходит сама операционная система, потому что так настроен её
# /etc/resolv.conf. Направлять его на себя — осознанный выбор со своей
# ценой: остановка dnsdist оставит сервер вообще без DNS. Поэтому здесь
# наблюдение, а приговор — только для необъяснённой утечки.
st_leak_resolvconf_notes() {
    local ns=""
    [ -r /etc/resolv.conf ] || return 1
    # nameserver-строки без loopback: 127.x и ::1 утечкой наружу не являются.
    ns=$(awk '$1 == "nameserver" && $2 !~ /^127\./ && $2 != "::1" { printf "%s%s", sep, $2; sep=", " }' \
        /etc/resolv.conf 2>/dev/null) || ns=""
    if [ -n "$ns" ]; then
        st_note "$(tf test_leak_resolvconf_culprit "$ns")"
        st_note "$(t test_leak_resolvconf_fix)"
        st_note "$(t test_leak_by_os)"
        st_note "$(t test_leak_by_os_hint)"
        return 0
    fi
    # В resolv.conf только loopback. Это ЕЩЁ НЕ значит «виновник неизвестен»:
    # на Ubuntu по умолчанию там стоит 127.0.0.53 — заглушка systemd-resolved,
    # и наружу за неё ходит она сама. Пойманный пакет вида
    # `127.0.0.1 > 127.0.0.53.53` буквально называет виновника, а проверка
    # смотрела только на resolv.conf, не делала следующий шаг и печатала
    # «разберитесь, кто ходит на :53» на самой обычной исправной машине.
    st_note "$(t test_leak_resolvconf_clean)"
    if st_resolved_stub_active; then
        local up=""
        up=$(st_resolved_uplinks) || up=""
        st_note "$(t test_leak_resolved_culprit)"
        if [ -n "$up" ]; then
            st_note "$(tf test_leak_resolved_upstream "$up")"
        else
            st_note "$(t test_leak_resolved_unknown)"
        fi
        st_note "$(t test_leak_by_os)"
        st_note "$(t test_leak_by_os_hint)"
        return 0
    fi
    st_note "$(t test_leak_unexplained)"
    return 1
}

# Заглушка systemd-resolved слушает 127.0.0.53:53? Тот же шаблон, что и в
# st_chk_resolved_stub: на Ubuntu ss печатает `127.0.0.53%lo:53`.
st_resolved_stub_active() {
    local out=""
    st_have ss || return 1
    out=$(ss -Hlnup 2>/dev/null) || return 1
    grep -qE '127\.0\.0\.53(%[^:[:space:]]+)?:53' <<< "$out"
}

# Куда systemd-resolved ходит наружу. Печатаем ИМЕНА апстримов, потому что
# «виновник назван» — это адрес, а не «где-то там». Пусто — так и скажем.
st_resolved_uplinks() {
    local out=""
    st_have resolvectl || return 1
    out=$(st_timeout 5 resolvectl dns 2>/dev/null) || out=""
    # `resolvectl dns` печатает строки «Link 2 (eth0): 1.1.1.1 8.8.8.8» и
    # «Global:». Берём всё после двоеточия, выкидывая пустые.
    printf '%s' "$(awk -F': ' 'NF > 1 { n = split($2, a, " "); for (i = 1; i <= n; i++) if (a[i] != "") { printf "%s%s", sep, a[i]; sep = ", " } }' <<< "$out" | cut -c1-120)"
}

# Адреса СВОИХ обычных апстримов (proto=plain в записях движка). Запросы
# к ним ходят на :53 по построению — это осознанный выбор оператора, а не
# утечка, и аудит обязан их знать, иначе он покрасил бы этот выбор ✗.
st_plain_upstream_ips() {
    local raw="" rec="" id proto host port rest
    raw=$(config_get upstreams "" 2>/dev/null) || raw=""
    [ -n "$raw" ] || return 0
    while IFS='|' read -r id proto host port rest; do
        [ "${proto:-}" = "plain" ] || continue
        host="${host:-}"; host="${host#[}"; host="${host%]}"
        [ -n "$host" ] && printf '%s\n' "$host"
    done <<< "$(printf '%s' "$raw" | tr ';' '\n')"
    return 0
}
# Фильтр tcpdump, исключающий эти адреса: « and not host A and not host B».
st_plain_upstream_pcap_excl() {
    local ip="" out=""
    while IFS= read -r ip; do
        [ -n "$ip" ] || continue
        out="${out} and not host ${ip}"
    done <<< "$(st_plain_upstream_ips)"
    printf '%s' "$out"
    return 0
}

# Слабейшая форма: снимок живых сокетов. Говорит только «прямо сейчас
# ничего нет» — UDP-запрос 200 мс назад никакого сокета не оставил.
st_chk_leak_sockets() {
    local tcp="" udp=""
    st_have ss || { st_skip "$(t test_reason_no_ss)"; return $?; }
    tcp=$(ss -Hn state established '( dport = :53 )' 2>/dev/null) || tcp=""
    udp=$(ss -Hnu '( dport = :53 )' 2>/dev/null) || udp=""
    st_note "$(t test_leak_snapshot_only)"
    # Свой обычный апстрим держит сокет к :53 по построению — это не
    # утечка, а его работа. Поймано на живом сервере: сразу после добавления
    # 127.0.0.53 этот пункт красил ✗ ровно тот выбор, который оператор
    # только что подтвердил.
    local pip="" excl=""
    while IFS= read -r pip; do
        [ -n "$pip" ] || continue
        tcp=$(printf '%s\n' "$tcp" | grep -vF "${pip}:53" || true)
        udp=$(printf '%s\n' "$udp" | grep -vF "${pip}:53" || true)
        excl="${excl}${excl:+ }${pip}"
    done <<< "$(st_plain_upstream_ips)"
    [ -n "$excl" ] && st_note "$(tf test_leak_plain_excluded "$excl")"
    if [ -n "$tcp" ] || [ -n "$udp" ]; then
        st_note "$(st_trim "$(printf '%s %s' "$tcp" "$udp" | tr '\n' ' ')" 170)"
        return 1
    fi
    return 0
}

st_nft_audit_teardown() {
    nft delete table inet godns_audit >/dev/null 2>&1 || true
    return 0
}

# Счётчик в output-хуке: policy accept, правило только считает и НИКОГДА
# не роняет пакеты. Это самый честный способ доказать отсутствие утечки
# без tcpdump.
st_chk_leak_nft() {
    local pkts="" outp="" out="" rc=0
    st_have nft || { st_skip "$(t test_reason_no_nft)"; return $?; }
    if [ "$(id -u)" -ne 0 ]; then st_skip "$(t test_reason_need_root)"; return $?; fi

    st_nft_audit_teardown
    # Кавычки вокруг тела правила обязательны: `{ tcp, udp }` без них
    # разбирает bash, а не nft. И никаких `[ … ] && …` цепочкой: при ложном
    # условии такая строка возвращает 1.
    if ! nft add table inet godns_audit >/dev/null 2>&1; then rc=1; fi
    if [ "$rc" -eq 0 ] && ! nft add counter inet godns_audit plain53 >/dev/null 2>&1; then rc=1; fi
    if [ "$rc" -eq 0 ] && ! nft add counter inet godns_audit plain53out >/dev/null 2>&1; then rc=1; fi
    if [ "$rc" -eq 0 ] && ! nft add chain inet godns_audit OUT \
        '{ type filter hook output priority 0; policy accept; }' >/dev/null 2>&1; then rc=1; fi
    # Свой обычный апстрим — раньше счётчиков: accept в цепочке с policy
    # accept судьбу пакета не меняет, но дальше по нашей цепочке он не идёт
    # и в счётчики не попадает. Чужие таблицы этот accept не трогает —
    # каждая base-цепочка на хуке видит пакет сама.
    local pip="" v4set="" v6set=""
    while IFS= read -r pip; do
        [ -n "$pip" ] || continue
        case "$pip" in
            *:*) v6set="${v6set}${v6set:+, }${pip}" ;;
            *)   v4set="${v4set}${v4set:+, }${pip}" ;;
        esac
    done <<< "$(st_plain_upstream_ips)"
    if [ "$rc" -eq 0 ] && [ -n "$v4set" ] && ! nft add rule inet godns_audit OUT \
        "ip daddr { ${v4set} } meta l4proto { tcp, udp } th dport 53 accept" >/dev/null 2>&1; then rc=1; fi
    if [ "$rc" -eq 0 ] && [ -n "$v6set" ] && ! nft add rule inet godns_audit OUT \
        "ip6 daddr { ${v6set} } meta l4proto { tcp, udp } th dport 53 accept" >/dev/null 2>&1; then rc=1; fi
    if [ "$rc" -eq 0 ] && ! nft add rule inet godns_audit OUT \
        'meta l4proto { tcp, udp } th dport 53 counter name plain53' >/dev/null 2>&1; then rc=1; fi
    # Второй счётчик — только то, что РЕАЛЬНО покинуло машину. Обмен с
    # заглушкой systemd-resolved идёт по loopback и наружу не уходит, а
    # первый счётчик считал его наравне с настоящей утечкой.
    if [ "$rc" -eq 0 ] && ! nft add rule inet godns_audit OUT \
        'oifname != "lo" meta l4proto { tcp, udp } th dport 53 counter name plain53out' >/dev/null 2>&1; then rc=1; fi

    if [ "$rc" -ne 0 ]; then
        st_nft_audit_teardown
        st_skip "$(t test_reason_nft_setup)"
        return $?
    fi

    st_leak_workload

    out=$(nft list counter inet godns_audit plain53 2>/dev/null) || out=""
    pkts=$(awk '{for (i = 1; i <= NF; i++) if ($i == "packets") { print $(i+1); exit }}' <<< "$out") || pkts=""
    out=$(nft list counter inet godns_audit plain53out 2>/dev/null) || out=""
    outp=$(awk '{for (i = 1; i <= NF; i++) if ($i == "packets") { print $(i+1); exit }}' <<< "$out") || outp=""
    st_nft_audit_teardown

    st_note "$(t test_leak_preconditions)"
    st_note "$(t test_leak_upstream_limit)"
    if [ -n "${v4set}${v6set}" ]; then
        st_note "$(tf test_leak_plain_excluded "$(st_plain_upstream_ips | paste -sd ' ' -)")"
    fi

    case "$pkts" in
        ''|*[!0-9]*)
            st_note "$(t test_leak_counter_unreadable)"
            return 1 ;;
    esac
    case "$outp" in ''|*[!0-9]*) outp="$pkts" ;; esac
    st_note "$(tf test_leak_counter "$pkts")"
    st_note "$(tf test_leak_counter_out "$outp")"
    if [ "$outp" -ne 0 ]; then
        if st_leak_resolvconf_notes; then return 3; fi
        return 1
    fi
    # Наружу не ушло ничего. Если по loopback обмен был — говорим прямо,
    # чтобы ненулевой первый счётчик не читался как «утечка есть».
    [ "$pkts" -ne 0 ] && st_note "$(t test_leak_loopback_only)"
    return 0
}

# tcpdump — только если он УЖЕ стоит. Ставить его ради теста не будем:
# на минимальных образах 24.04 его нет, и это не повод тянуть пакет.
st_chk_leak_tcpdump() {
    local cap="" pid="" rc=0
    st_have tcpdump || { st_skip "$(t test_reason_no_tcpdump)"; return $?; }
    if [ "$(id -u)" -ne 0 ]; then st_skip "$(t test_reason_need_root)"; return $?; fi

    cap=$(godns_mktemp) || { st_skip "$(t test_reason_no_tmp)"; return $?; }
    : > "$cap"
    # Loopback из фильтра ИСКЛЮЧЁН намеренно. Пакет `127.0.0.1 > 127.0.0.53.53`
    # машину не покидает — это система разговаривает со своей же заглушкой
    # systemd-resolved, то есть штатная Ubuntu из коробки. Ловя его, проверка
    # рисовала ✗ «разберитесь, кто ходит на :53» на исправном сервере — самое
    # частое ложное «сломано» на самой частой конфигурации. Настоящую утечку
    # это не прячет: как только resolved (или кто угодно) пойдёт на :53
    # НАРУЖУ, пакет попадёт под фильтр и будет пойман.
    local excl=""
    excl=$(st_plain_upstream_pcap_excl) || excl=""
    st_timeout 12 tcpdump -ni any -c 1 -q \
        "port 53 and not net 127.0.0.0/8 and not host ::1${excl}" >"$cap" 2>/dev/null &
    pid=$!
    sleep 1
    st_leak_workload
    wait "$pid" 2>/dev/null || rc=$?

    if [ -s "$cap" ]; then
        st_note "$(st_trim "$(tr '\n' ' ' < "$cap")" 170)"
        st_note "$(t test_leak_tcpdump_caught)"
        if st_leak_resolvconf_notes; then return 3; fi
        return 1
    fi
    # Пусто И tcpdump упал — это не «чисто», это «нечем было смотреть».
    # Ошибка в фильтре роняет его мгновенно с кодом 1, и «за окно наблюдения
    # не увидел ни одного пакета» стало бы враньём. 124 — штатный выход по
    # таймауту: окно кончилось, ловить было нечего.
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ]; then
        st_skip "$(t test_reason_tcpdump_failed)"
        return $?
    fi
    st_note "$(t test_leak_tcpdump_clean)"
    # Не «пакеты были и остались в loopback» — этого мы не наблюдали.
    # Говорим ровно то, что сделали: loopback из фильтра исключён.
    st_note "$(t test_leak_tcpdump_scope)"
    if [ -n "$excl" ]; then
        st_note "$(tf test_leak_plain_excluded "$(st_plain_upstream_ips | paste -sd ' ' -)")"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГРУППА 6. ПОЛИТИКА ВЫБОРА АПСТРИМА
# ═══════════════════════════════════════════════════════════════════════

st_dnsdist_console() {
    local args=()
    st_have dnsdist || return 1
    [ "$(id -u)" -eq 0 ] || return 1
    if [ -r "$ST_DNSDIST_CONF" ]; then args=(-C "$ST_DNSDIST_CONF"); fi
    args+=(-e "$1")
    st_timeout 10 dnsdist "${args[@]}" 2>/dev/null
}

# Веб-API dnsdist как запасной путь. Ключ — через файл конфигурации curl с
# правами 600: /proc/<pid>/cmdline читает любой локальный пользователь.
st_dnsdist_api() {
    local key="" port="" cfg="" out="" rc=0
    st_have curl || return 1
    key=$(config_get api_key "") || key=""
    port=$(config_get api_port "") || port=""
    [ -n "$key" ] && [ -n "$port" ] || return 1
    cfg=$(godns_mktemp) || return 1
    printf 'header = "X-API-Key: %s"\n' "$key" > "$cfg"
    chmod 600 "$cfg" 2>/dev/null || true
    out=$(st_timeout 10 curl -sS -m 8 --config "$cfg" \
          "http://127.0.0.1:${port}/api/v1/servers/localhost" 2>/dev/null) || rc=$?
    rm -f "$cfg" 2>/dev/null || true
    [ "$rc" -eq 0 ] || return 1
    printf '%s' "$out" > "$ST_SCRATCH"
    python3 - "$ST_SCRATCH" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(1)
srv = d.get("servers") or []
if not srv:
    raise SystemExit(1)
for s in srv:
    sys.stdout.write("%s %s\n" % (s.get("name") or s.get("address") or "?", s.get("queries", 0)))
PY
}

# "name queries" построчно. Сперва консоль, потом веб-API.
st_backend_counters() {
    local out=""
    out=$(st_dnsdist_console 'showServers()') || out=""
    if [ -n "$out" ]; then
        printf '%s' "$out" > "$ST_SCRATCH"
        awk '
            NF == 0 { next }
            /^#/ { for (i = 1; i <= NF; i++) { if ($i == "Name") ni = i; if ($i == "Queries") qi = i } ; next }
            (ni && qi && NF >= qi) {
                nm = $ni; q = $qi
                if (nm == "All" || nm == "") next
                if (q ~ /^[0-9]+$/) print nm, q
            }
        ' "$ST_SCRATCH" 2>/dev/null
        return 0
    fi
    st_dnsdist_api
}

st_upstream_policy() {
    local p=""
    p=$(config_get policy "") || p=""
    if [ -z "$p" ] && [ -r "$ST_DNSDIST_CONF" ]; then
        p=$(awk '/^[[:space:]]*--/ { next }
                 match($0, /setServerPolicy\(([A-Za-z_]+)\)/) {
                     s = substr($0, RSTART + 16, RLENGTH - 17); print s; exit }' \
            "$ST_DNSDIST_CONF" 2>/dev/null) || p=""
    fi
    printf '%s' "$p"
}

# Ключевой момент: КАЖДЫЙ запрос — со СВОЕЙ случайной меткой. С постоянным
# именем пакетный кэш замкнёт всё после первого запроса, и тест «пройдёт»
# по одной-единственной строке.
st_chk_upstream_rotation() {
    local before="" after="" policy="" used=0 total=0 i=1 rnd=""
    local dist="" unused=""

    if ! st_ensure_kdig; then st_skip "$(t test_reason_no_kdig)"; return $?; fi
    st_pick_transport || { st_skip "$(t test_reason_no_transport)"; return $?; }

    before=$(st_backend_counters) || before=""
    if [ -z "$before" ]; then
        st_skip "$(t test_reason_no_counters)"; return $?
    fi
    policy=$(st_upstream_policy) || policy=""

    i=1
    while [ "$i" -le 20 ]; do
        rnd=$(random_path 10) || rnd="r$i"
        st_query "probe-${rnd}-${i}.example.com" A >/dev/null 2>&1 || true
        i=$((i + 1))
    done

    after=$(st_backend_counters) || after=""
    if [ -z "$after" ]; then
        st_skip "$(t test_reason_no_counters)"; return $?
    fi

    printf '%s\n' "$before" > "${ST_SCRATCH}.before"
    printf '%s\n' "$after"  > "${ST_SCRATCH}.after"
    dist=$(python3 - "${ST_SCRATCH}.before" "${ST_SCRATCH}.after" <<'PY' 2>/dev/null
import sys
def load(p):
    d = {}
    for line in open(p):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            d[parts[0]] = int(parts[1])
    return d
b, a = load(sys.argv[1]), load(sys.argv[2])
used = 0
parts = []
for name in sorted(a):
    delta = a[name] - b.get(name, 0)
    if delta < 0:
        delta = a[name]          # счётчик обнулился (перезапуск бэкенда)
    if delta > 0:
        used += 1
    parts.append("%s=%d" % (name, delta))
sys.stdout.write("%d %d %s" % (used, len(a), " ".join(parts)))
PY
    ) || dist=""
    rm -f "${ST_SCRATCH}.before" "${ST_SCRATCH}.after" 2>/dev/null || true

    if [ -z "$dist" ]; then
        st_skip "$(t test_reason_no_counters)"; return $?
    fi
    used="${dist%% *}"
    total=$(printf '%s' "${dist#* }" | cut -d' ' -f1)
    st_note "$(st_trim "${dist#* * }" 170)"
    st_note "$(tf test_rot_policy "${policy:-?}")"

    case "$total" in ''|*[!0-9]*) total=0 ;; esac
    case "$used"  in ''|*[!0-9]*) used=0 ;; esac

    if [ "$total" -le 1 ]; then
        st_note "$(t test_rot_single_backend)"
        return 3
    fi
    if [ "$used" -ge "$total" ]; then
        st_note "$(tf test_rot_all_used "$total")"
        return 0
    fi
    unused=$((total - used))
    st_note "$(tf test_rot_not_all "$used" "$total" "$unused")"
    case "$policy" in
        roundrobin|wrandom|whashed|chashed)
            return 1 ;;
        *)
            st_note "$(t test_rot_policy_explains)"
            return 3 ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
# КАК ИСПОЛЬЗОВАТЬ НА УСТРОЙСТВАХ
#
# Подменю с тремя разделами: роутеры → операционные системы → браузеры.
# Роутеры первыми намеренно: один настроенный роутер закрывает все
# устройства дома разом, остальное — точечные настройки.
#
# Правила честности этого экрана (каждое проверено по документации
# производителя, а не срисовано с чужих скриншотов):
#   • DoT упоминаем только когда он реально включён (dot_enabled=yes):
#     инструкция про выключенный листенер — инструкция в никуда;
#   • Android «Частный DNS» умеет ТОЛЬКО DoT, только имя хоста и только
#     порт 853 — ни порт, ни путь там ввести некуда. При выключенном DoT
#     или нестандартном порте честно отправляем в приложения с DoH-URL
#     (Intra, Nebulo, AdGuard), а не сочиняем несуществующие поля;
#   • TP-Link со штатной прошивкой свой DoH-сервер не принимает вовсе:
#     только выбор из встроенного списка Google/Cloudflare/Quad9;
#   • MikroTik: наш dnsdist отдаёт входящий DoH только по HTTP/2
#     (nghttp2), а RouterOS выучил HTTP/2 в 7.23 и только на
#     ARM64/x86/CHR. Плюс классика: без импортированного корня УЦ и
#     verify-doh-cert его DoH молчит без единой ошибки;
#   • Keenetic: DoH-прокси части прошивок ходит только на порт 443,
#     поэтому рядом даём запасной путь через DoT;
#   • профиль Apple не подписан — предупреждаем про «Не проверено» ДО
#     того, как человек увидит его на устройстве.
# ═══════════════════════════════════════════════════════════════════════

# ── Раздел 1: роутеры ───────────────────────────────────────────────────
selftest_snip_routers() {
    selftest_load_ctx
    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    local d="$ST_DOMAIN" hp="$ST_DOH_PORT" tp="$ST_DOT_PORT"
    local url="https://${d}:${hp}/${ST_DOH_PATH}"
    local ip="${ST_PUBIP:-<IP>}"

    print_header "$(t test_snip_sec_routers)"
    echo -e "  ${DIM}$(t test_snip_routers_first)${NC}"

    echo ""
    echo -e "  ${BOLD}Keenetic / Netcraze${NC}"
    echo -e "           ${DIM}$(t test_snip_keen_netcraze)${NC}"
    echo -e "           ${DIM}$(t test_snip_keen_component)${NC}"
    echo -e "           ${DIM}$(t test_snip_keen_component2)${NC}"
    echo -e "           ${DIM}$(t test_snip_keen_add)${NC}"
    echo -e "           ${DIM}$(t test_snip_keen_add2)${NC}"
    echo -e "           ${CYAN}${url}${NC}"
    echo -e "           ${YELLOW}$(t test_snip_keen_port443)${NC}"
    if selftest_dot_on; then
        echo -e "           ${YELLOW}$(tf test_snip_keen_port443b "$hp")${NC}"
        echo -e "           ${CYAN}${d}:${tp}${NC}"
    else
        echo -e "           ${YELLOW}$(tf test_snip_keen_port443_off "$hp")${NC}"
        echo -e "           ${YELLOW}$(t test_snip_keen_port443_off2)${NC}"
    fi

    echo -e "           ${DIM}$(t snip_undo)${NC}"
    echo -e "           ${DIM}$(t snip_undo_keenetic)${NC}"

    echo ""
    echo -e "  ${BOLD}OpenWrt${NC}"
    echo -e "           ${DIM}$(t test_snip_owrt_pkg)${NC}"
    echo "     opkg update && opkg install https-dns-proxy luci-app-https-dns-proxy"
    echo -e "           ${DIM}$(t test_snip_owrt_luci)${NC}"
    echo -e "           ${DIM}$(t test_snip_owrt_luci2)${NC}"
    echo -e "           ${CYAN}${url}${NC}"
    echo -e "           ${DIM}$(t test_snip_owrt_auto)${NC}"
    echo -e "           ${DIM}$(t snip_undo)${NC}"
    echo "     /etc/init.d/https-dns-proxy stop && /etc/init.d/https-dns-proxy disable"
    echo "     opkg remove luci-app-https-dns-proxy https-dns-proxy"

    echo ""
    echo -e "  ${BOLD}MikroTik (RouterOS)${NC}"
    echo -e "           ${YELLOW}$(t test_snip_mikro_http2)${NC}"
    echo -e "           ${YELLOW}$(t test_snip_mikro_http2b)${NC}"
    echo -e "           ${DIM}$(t test_snip_mikro_cert)${NC}"
    echo -e "           ${DIM}$(t test_snip_mikro_cmds)${NC}"
    echo "     /tool fetch url=\"https://letsencrypt.org/certs/isrgrootx1.pem\""
    echo "     /certificate import file-name=isrgrootx1.pem passphrase=\"\""
    echo "     /ip dns static add name=${d} address=${ip}"
    echo "     /ip dns set use-doh-server=\"${url}\" verify-doh-cert=yes"
    echo -e "           ${DIM}$(t test_snip_mikro_static)${NC}"
    echo -e "           ${DIM}$(t test_snip_mikro_nodot)${NC}"
    echo -e "           ${DIM}$(t snip_undo)${NC}"
    echo "     /ip dns set use-doh-server=\"\" verify-doh-cert=no"
    echo "     /ip dns static remove [find name=${d}]"
    echo "     /ip dns cache flush"

    echo ""
    echo -e "  ${BOLD}TP-Link${NC}"
    echo -e "           ${YELLOW}$(t test_snip_tpl_none)${NC}"
    echo -e "           ${YELLOW}$(t test_snip_tpl_none2)${NC}"
    echo -e "           ${DIM}$(t test_snip_tpl_alt)${NC}"
    echo -e "           ${DIM}$(t test_snip_tpl_alt2)${NC}"
    print_rule
    return 0
}

# ── Раздел 2: операционные системы ──────────────────────────────────────
selftest_snip_os() {
    selftest_load_ctx
    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    local d="$ST_DOMAIN" hp="$ST_DOH_PORT" tp="$ST_DOT_PORT"
    local url="https://${d}:${hp}/${ST_DOH_PATH}"
    local ip="${ST_PUBIP:-<IP>}"

    print_header "$(t test_snip_sec_os)"

    # Windows 11 принимает СВОЙ DoH-сервер только после регистрации через
    # netsh: поле ручного шаблона в Параметрах сверяется со списком
    # известных серверов. udpfallback=no — иначе при любой икоте DoH
    # система тихо откатывается в открытый DNS.
    echo -e "  ${BOLD}Windows 11${NC}"
    echo -e "           ${DIM}$(t test_snip_win_netsh)${NC}"
    echo "     netsh dns add encryption server=${ip} dohtemplate=${url} autoupgrade=yes udpfallback=no"
    echo -e "           ${DIM}$(t test_snip_win_leak)${NC}"
    echo -e "           ${DIM}$(t test_snip_win_ui)${NC}"
    echo -e "           ${DIM}$(t test_snip_win_ui2)${NC}"
    echo -e "           ${DIM}$(t test_snip_win_ui3)${NC} ${CYAN}${ip}${NC}"
    echo -e "           ${DIM}$(t test_snip_win_ui4)${NC}"
    echo -e "           ${DIM}$(t snip_undo)${NC}"
    echo "     netsh dns delete encryption server=${ip}"

    echo ""
    echo -e "  ${BOLD}macOS 11+${NC}"
    echo -e "           ${DIM}$(t test_snip_mac)${NC}"
    echo -e "           ${DIM}$(t test_snip_mac2)${NC}"
    echo -e "           ${DIM}$(t test_snip_mac3)${NC}"

    echo ""
    echo -e "  ${BOLD}iPhone / iPad${NC}"
    echo -e "           ${DIM}$(t test_snip_ios1)${NC}"
    echo -e "           ${DIM}$(t test_snip_ios2)${NC}"
    echo -e "           ${DIM}$(t test_snip_ios3)${NC}"
    echo -e "           ${YELLOW}$(t test_snip_unsigned)${NC}"

    echo ""
    echo -e "  ${BOLD}Android 9+${NC}"
    if selftest_dot_on && [ "$tp" = "853" ]; then
        echo -e "           ${DIM}$(t test_snip_android)${NC}"
        echo -e "           ${CYAN}${d}${NC}"
        echo -e "           ${YELLOW}$(t test_snip_android_warn)${NC}"
    else
        if selftest_dot_on; then
            echo -e "           ${YELLOW}$(tf test_snip_android_port "$tp")${NC}"
        else
            echo -e "           ${YELLOW}$(t test_snip_android_nodot)${NC}"
            echo -e "           ${YELLOW}$(t test_snip_android_nodot2)${NC}"
        fi
        echo -e "           ${DIM}$(t test_snip_android_apps)${NC}"
        echo -e "           ${DIM}$(t test_snip_android_apps2)${NC}"
        echo -e "           ${CYAN}${url}${NC}"
    fi
    echo -e "           ${DIM}$(t snip_undo)${NC}"
    echo -e "           ${DIM}$(t snip_undo_android)${NC}"
    print_rule
    return 0
}

# ── Раздел 3: браузеры ──────────────────────────────────────────────────
selftest_snip_browsers() {
    selftest_load_ctx
    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    local url="https://${ST_DOMAIN}:${ST_DOH_PORT}/${ST_DOH_PATH}"

    print_header "$(t test_snip_sec_browsers)"
    echo -e "  ${DIM}$(t test_snip_browsers_note)${NC}"

    echo ""
    echo -e "  ${BOLD}Chrome${NC}"
    echo -e "           ${DIM}$(t test_snip_chrome)${NC}"
    echo -e "           ${CYAN}${url}${NC}"
    echo ""
    echo -e "  ${BOLD}Edge${NC}"
    echo -e "           ${DIM}$(t test_snip_edge)${NC}"
    echo -e "           ${CYAN}${url}${NC}"
    echo ""
    echo -e "  ${BOLD}Firefox${NC}"
    echo -e "           ${DIM}$(t test_snip_firefox)${NC}"
    echo -e "           ${CYAN}${url}${NC}"
    echo -e "           ${DIM}$(t test_snip_firefox_verify)${NC}"
    echo ""
    echo -e "  ${DIM}$(t snip_undo)${NC}"
    echo -e "  ${DIM}$(t snip_undo_browsers)${NC}"
    print_rule
    return 0
}

selftest_snip_all() {
    selftest_snip_routers  || return 1
    selftest_snip_os       || return 1
    selftest_snip_browsers || return 1
    return 0
}

# ── Профиль конфигурации Apple (.mobileconfig) ──────────────────────────
# У macOS и iOS поля для DoH в настройках нет — только профиль с полезной
# нагрузкой com.apple.dnsSettings.managed (iOS 14+ / macOS 11+).
# Пишем его штатным plistlib, а не heredoc'ом: XML-экранирование руками —
# это второй генератор багов после ручного JSON.
# Записать .mobileconfig на диск. Печатает путь к файлу; rc 1 — не вышло.
# Вынесено из selftest_snip_apple_profile: файл нужен и экрану с QR, и
# боту — а генератор должен быть один, иначе профили разъедутся.
st_apple_profile_write() {
    selftest_load_ctx
    [ -n "$ST_DOMAIN" ] || return 1
    local out="${GODNS_DIR}/godns-doh.mobileconfig"
    local proto="" url="" dot_port=""
    dot_port=$(config_get dot_port "853") || dot_port="853"

    # ЧТО КЛАДЁМ В ПРОФИЛЬ — и почему не всегда DoH.
    #
    # ГРАБЛИ, стоившие пользователю нерабочего iPhone: iOS и macOS
    # ИГНОРИРУЮТ порт в ServerURL и всегда идут на <хост>:443. Профиль с
    # https://домен:4443/путь ставится без единой ошибки, а система стучит
    # на 443 — где у нас сосед (SNI-диспетчер goTelegram). Резолвер для
    # телефона мёртв, диагностики никакой. Подтверждено независимыми
    # воспроизведениями в encrypted-dns#58 и AdGuardHome#4658; в
    # документации Apple ServerURL описан как источник ИМЕНИ ХОСТА.
    #
    # Поэтому: DoH в профиль идёт ТОЛЬКО когда он на 443. Иначе — DoT,
    # который Apple поддерживает штатно и у которого порт по RFC 7858
    # всегда 853 (в пейлоаде TLS поля порта нет вовсе). DoT у нас на 853 и
    # закрыт тем же сертификатом Let's Encrypt.
    if [ "$ST_DOH_PORT" = "443" ]; then
        proto="HTTPS"
        url="https://${ST_DOMAIN}/${ST_DOH_PATH}"
    elif selftest_dot_on && [ "$dot_port" = "853" ]; then
        proto="TLS"
    else
        # Ни DoH на 443, ни DoT на 853 — системный профиль Apple невозможен.
        return 2
    fi

    mkdir -p "$GODNS_DIR" 2>/dev/null || true
    local rc=0
    GODNS_MC_PROTO="$proto" GODNS_MC_URL="$url" GODNS_MC_DOMAIN="$ST_DOMAIN" \
    GODNS_MC_OUT="$out" \
    python3 - <<'MCPY' 2>/dev/null || rc=1
import os, plistlib, uuid

proto  = os.environ["GODNS_MC_PROTO"]
url    = os.environ.get("GODNS_MC_URL", "")
domain = os.environ["GODNS_MC_DOMAIN"]
out    = os.environ["GODNS_MC_OUT"]

# UUID детерминированные (uuid5 от домена): повторная генерация даёт тот
# же идентификатор, и устройство ЗАМЕНЯЕТ профиль, а не копит дубликаты.
ns = uuid.uuid5(uuid.NAMESPACE_DNS, "godns." + domain)

if proto == "HTTPS":
    dns = {"DNSProtocol": "HTTPS", "ServerURL": url}
    label = "DoH"
else:
    # У DoT в пейлоаде Apple нет поля порта: всегда 853 по RFC 7858.
    # ServerName — имя для проверки сертификата и для поиска адресов.
    dns = {"DNSProtocol": "TLS", "ServerName": domain}
    label = "DoT"

payload = {
    "DNSSettings": dns,
    "PayloadDescription": "Encrypted DNS (%s) via %s" % (label, domain),
    "PayloadDisplayName": "goDNS Pro %s (%s)" % (label, domain),
    "PayloadIdentifier": "org.godns.dnssettings." + domain,
    "PayloadType": "com.apple.dnsSettings.managed",
    "PayloadUUID": str(uuid.uuid5(ns, "payload")).upper(),
    "PayloadVersion": 1,
}
profile = {
    "PayloadContent": [payload],
    "PayloadDescription": "goDNS Pro encrypted DNS resolver",
    "PayloadDisplayName": "goDNS Pro (" + domain + ")",
    "PayloadIdentifier": "org.godns.profile." + domain,
    "PayloadRemovalDisallowed": False,
    # System: без него macOS ставит профиль пользовательским.
    "PayloadScope": "System",
    "PayloadType": "Configuration",
    "PayloadUUID": str(uuid.uuid5(ns, "profile")).upper(),
    "PayloadVersion": 1,
}
tmp = out + ".tmp"
with open(tmp, "wb") as fh:
    plistlib.dump(profile, fh)
os.replace(tmp, out)
MCPY
    [ "$rc" -eq 0 ] || return 1
    chmod 600 "$out" 2>/dev/null || true
    printf '%s' "$out"
    return 0
}

selftest_snip_apple_profile() {
    selftest_load_ctx
    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    local out=""
    local prc=0
    out=$(st_apple_profile_write) || prc=$?
    if [ "$prc" -eq 2 ]; then
        # Ни DoH на 443, ни DoT на 853 — Apple системный профиль не примет.
        log_warning "$(t test_snip_prof_impossible)"
        log_dim "$(t test_snip_prof_impossible_why)"
        return 0
    fi
    if [ -z "$out" ]; then
        log_error "$(t test_snip_prof_fail)"
        return 1
    fi

    print_header "$(t test_snip_sec_profile)"
    log_success "$(tf test_snip_prof_written "$out")"
    # Подпись зависит от того, ЧТО реально внутри: при DoH не на 443
    # профиль строится как DoT (ServerName, без всякого пути), и текст про
    # «DoH URL с секретным путём» врал бы прямым текстом.
    if [ "$ST_DOH_PORT" = "443" ]; then
        echo -e "  ${DIM}$(t test_snip_prof_what)${NC}"
    else
        echo -e "  ${DIM}$(t test_snip_prof_what_dot)${NC}"
    fi
    echo ""
    echo -e "  ${DIM}$(t test_snip_prof_send)${NC}"
    echo "     scp root@${ST_PUBIP:-<IP>}:${out} ."
    echo ""
    echo -e "  ${DIM}$(t test_snip_prof_next)${NC}"
    echo -e "  ${BOLD}iPhone / iPad${NC}"
    # ГРАБЛИ: первая половина фразы жила в test_snip_ios1 и здесь не
    # печаталась — экран начинался с середины предложения, со скобки
    # «(AirDrop, почта, «Файлы»)». Своя первая строка, а не ios1: там
    # написано «Тот же профиль», что верно в списке ОС и бессмысленно
    # здесь, где файл только что создан на этом же экране.
    echo -e "           ${DIM}$(t test_snip_prof_ios1)${NC}"
    echo -e "           ${DIM}$(t test_snip_ios2)${NC}"
    echo -e "           ${DIM}$(t test_snip_ios3)${NC}"
    echo -e "  ${BOLD}Mac${NC}"
    echo -e "           ${DIM}$(t test_snip_mac3)${NC}"
    echo ""
    echo -e "  ${YELLOW}$(t test_snip_unsigned)${NC}"
    echo ""
    echo -e "  ${DIM}$(t snip_undo)${NC}"
    echo -e "  ${DIM}$(t snip_undo_apple_ios)${NC}"
    echo -e "  ${DIM}$(t snip_undo_apple_mac)${NC}"
    return 0
}

# ── QR для телефонов ────────────────────────────────────────────────────
# Жалобы «на роутере работает, на телефоне нет» имеют один корень: роутер
# настраивает оператор по сниппетам, а телефону нечего скопировать.
# Android «Частный DNS» требует ИМЯ ХОСТА (DoT, жёстко порт 853), iPhone —
# профиль, который надо доставить на устройство. QR закрывает обе доставки.

# Раздать профиль по HTTPS на эфемерном порту, пока оператор показывает QR.
# Сервер живёт до Enter или до самоистечения (10 минут): если SSH оборвётся
# и убить его будет некому, он умрёт сам — оставлять слушатель с чужим
# по сути файлом навсегда нельзя.
st_profile_qr_serve() {
    local profile="$1" port="" token="" url="" pid=""
    port=$(godns_first_free_port 18443 18444 18445 19443 20443) || port=""
    if [ -z "$port" ]; then
        log_error "$(t qr_ios_port_fail)"
        return 1
    fi
    token=$(random_path 24) || token=""
    [ -n "$token" ] || return 1

    # Сертификат тот же, которым живёт dnsdist; root его читает. Пути —
    # из config.json: переменных ST_CERT/ST_KEY в контексте самопроверки
    # НЕТ, и первый вариант передавал пустоту — python молча умирал, а
    # оператор видел «не удалось поднять раздачу» на исправной машине.
    local _cert="" _key=""
    _cert=$(config_get cert_path "") || _cert=""
    _key=$(config_get key_path "") || _key=""
    if [ ! -r "$_cert" ] || [ ! -r "$_key" ]; then
        log_error "$(t qr_ios_serve_fail)"
        return 1
    fi
    GODNS_QRS_CERT="$_cert" GODNS_QRS_KEY="$_key" \
    GODNS_QRS_PORT="$port" GODNS_QRS_TOKEN="$token" \
    GODNS_QRS_FILE="$profile" \
    python3 - <<'PYSRV' >/dev/null 2>&1 &
import http.server, os, ssl, threading

cert, key = os.environ["GODNS_QRS_CERT"], os.environ["GODNS_QRS_KEY"]
port, token = int(os.environ["GODNS_QRS_PORT"]), os.environ["GODNS_QRS_TOKEN"]
path = os.environ["GODNS_QRS_FILE"]
data = open(path, "rb").read()
want = "/" + token + ".mobileconfig"

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != want:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-apple-aspen-config")
        self.send_header("Content-Disposition",
                         'attachment; filename="godns-doh.mobileconfig"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass

srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
threading.Timer(600, srv.shutdown).start()
srv.serve_forever()
os._exit(0)
PYSRV
    pid=$!
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_error "$(t qr_ios_serve_fail)"
        return 1
    fi
    godns_fw_open "$port" || true
    # Сторож на случай обрыва SSH. Сервер раздачи гаснет сам через 600 с,
    # а правило в ufw без уборки осталось бы НАВСЕГДА: порт с профилем
    # открыт наружу, и никто об этом уже не помнит. Ловушку EXIT здесь
    # ставить нельзя — на ней висит cleanup_temp_files из godns.sh, и
    # своя её затрёт (`trap - EXIT` не вернёт чужую). Поэтому отдельный
    # отвязанный процесс: setsid переживает SIGHUP умирающей сессии.
    if command -v setsid >/dev/null 2>&1 && command -v ufw >/dev/null 2>&1; then
        setsid bash -c "
            while kill -0 $pid 2>/dev/null; do sleep 5; done
            ufw delete allow ${port}/tcp >/dev/null 2>&1 || true
        " </dev/null >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
    # На nftables мы чужой ruleset не трогаем (соседи!), поэтому порт
    # раздачи оператор открывает сам. Даём точную команду и — на всякий
    # случай — говорим, что через панель того же делать не нужно вовсе:
    # там профиль идёт через уже открытый порт панели.
    if command -v nft >/dev/null 2>&1 && ! { command -v ufw >/dev/null 2>&1 \
         && grep -qE '^[[:space:]]*ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null; }; then
        log_dim "$(tf qr_ios_nft_hint "$port")"
        log_dim "$(t qr_ios_panel_hint)"
    fi

    url="https://${ST_DOMAIN}:${port}/${token}.mobileconfig"
    echo ""
    log_success "$(tf qr_ios_serving "$url")"
    echo -e "  ${DIM}$(t qr_ios_scan)${NC}"
    echo ""
    godns_qr_term "$url" | sed 's/^/    /'
    echo ""
    echo -e "  ${DIM}$(t qr_ios_after)${NC}"
    echo -e "  ${YELLOW}$(t test_snip_unsigned)${NC}"
    echo ""
    # Ctrl-C здесь не должен убивать всё меню — как в хвосте лога.
    trap ':' INT
    read -rp "  $(t qr_ios_stop) " _ || true
    trap - INT
    kill "$pid" 2>/dev/null || true
    godns_fw_close "$port" || true
    log_dim "$(t qr_ios_stopped)"
    return 0
}

# Экран QR даёт человеку две настоящие настройки его устройства: «Частный
# DNS» в Android и профиль в iOS/macOS. Пути назад у него не было — правило
# «инструкция без отмены не дописана» действует и здесь. Печатаем ВСЕГДА, в
# том числе на ранних выходах (нет qrencode, профиль невозможен): человек уже
# мог настроить телефон по верхней половине экрана.
selftest_snip_phone_qr() {
    local _rc=0
    _snip_phone_qr_body || _rc=$?
    echo ""
    echo -e "  ${DIM}$(t snip_undo)${NC}"
    echo -e "  ${DIM}$(t snip_undo_android)${NC}"
    echo -e "  ${DIM}$(t snip_undo_apple_ios)${NC}"
    echo -e "  ${DIM}$(t snip_undo_apple_mac)${NC}"
    return "$_rc"
}

_snip_phone_qr_body() {
    selftest_load_ctx
    if [ -z "$ST_DOMAIN" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    print_header "$(t test_snip_sec_qr)"

    if ! godns_qr_available; then
        if confirm "$(t qr_need_pkg)" y; then
            run_with_progress "$(t qr_installing)" apt_q install qrencode || true
        fi
        if ! godns_qr_available; then
            log_warning "$(t qr_no_pkg)"
        fi
    fi

    local tp=""
    tp=$(config_get dot_port "853") || tp="853"

    # ── Android ──────────────────────────────────────────────────────────
    echo -e "  ${BOLD}Android 9+${NC}  ${DIM}$(t qr_android_what)${NC}"
    if selftest_dot_on && [ "$tp" = "853" ]; then
        echo -e "  ${DIM}$(t test_snip_android)${NC}"
        echo -e "  ${CYAN}${ST_DOMAIN}${NC}"
        echo -e "  ${DIM}$(t test_snip_android_where)${NC}"
        echo -e "  ${YELLOW}$(t test_snip_android_vpn)${NC}"
        if godns_qr_available; then
            echo ""
            echo -e "  ${DIM}$(t qr_android_scan)${NC}"
            godns_qr_term "$ST_DOMAIN" | sed 's/^/    /'
        fi
        echo -e "  ${YELLOW}$(t test_snip_android_warn)${NC}"
    elif selftest_dot_on; then
        echo -e "  ${YELLOW}$(tf test_snip_android_port "$tp")${NC}"
        echo -e "  ${DIM}$(t qr_android_fix_port)${NC}"
    else
        echo -e "  ${YELLOW}$(t test_snip_android_nodot)${NC}"
        echo -e "  ${YELLOW}$(t test_snip_android_nodot2)${NC}"
        echo -e "  ${DIM}$(t qr_android_enable_hint)${NC}"
    fi
    print_rule

    # ── iPhone ───────────────────────────────────────────────────────────
    echo -e "  ${BOLD}iPhone / iPad${NC}  ${DIM}$(t qr_ios_what)${NC}"
    local profile=""
    local prc2=0
    profile=$(st_apple_profile_write) || prc2=$?
    if [ "$prc2" -eq 2 ]; then
        log_warning "$(t test_snip_prof_impossible)"
        log_dim "$(t test_snip_prof_impossible_why)"
        return 0
    fi
    if [ -z "$profile" ]; then
        log_error "$(t test_snip_prof_fail)"
        return 1
    fi
    if ! godns_qr_available; then
        log_warning "$(t qr_ios_text_only)"
        log_dim "$(tf test_snip_prof_written "$profile")"
        return 0
    fi
    st_profile_qr_serve "$profile" || return 1
    return 0
}


# ═══════════════════════════════════════════════════════════════════════
# НАБОРЫ
# ═══════════════════════════════════════════════════════════════════════

st_suite_quick() {
    ST_SUITE=()
    st_add st_chk_listen_doh      test_c_listen_doh      test_g_listen
    st_add st_chk_listen_dot      test_c_listen_dot      test_g_listen
    st_add st_chk_not_loopback    test_c_not_loopback    test_g_listen
    st_add st_chk_tls_doh         test_c_tls_doh         test_g_tls
    st_add st_chk_doh_get         test_c_doh_get         test_g_doh
    st_add st_chk_resolve_dot     test_c_resolve_dot     test_g_resolve
    return 0
}

st_suite_full() {
    ST_SUITE=()
    st_add st_chk_listen_doh        test_c_listen_doh        test_g_listen
    st_add st_chk_listen_dot        test_c_listen_dot        test_g_listen
    st_add st_chk_socket_owner      test_c_socket_owner      test_g_listen
    st_add st_chk_not_loopback      test_c_not_loopback      test_g_listen
    st_add st_chk_resolved_stub     test_c_resolved_stub     test_g_listen
    st_add st_chk_firewall_local    test_c_firewall_local    test_g_listen
    st_add st_chk_external_reach    test_c_external_reach    test_g_listen

    st_add st_chk_doh_get           test_c_doh_get           test_g_doh
    st_add st_chk_doh_wire          test_c_doh_wire          test_g_doh
    st_add st_chk_doh_post          test_c_doh_post          test_g_doh
    st_add st_chk_doh_wrong_paths   test_c_doh_wrong_paths   test_g_doh
    st_add st_chk_doh_malformed     test_c_doh_malformed     test_g_doh

    st_add st_chk_tls_doh           test_c_tls_doh           test_g_tls
    st_add st_chk_tls_dot           test_c_tls_dot           test_g_tls
    st_add st_chk_tls_public_ca     test_c_tls_public_ca     test_g_tls
    st_add st_chk_tls_expiry        test_c_tls_expiry        test_g_tls
    st_add st_chk_tls_san_android   test_c_tls_san_android   test_g_tls
    st_add st_chk_tls_alpn_h2       test_c_tls_alpn_h2       test_g_tls

    st_add st_chk_resolve_dot       test_c_resolve_dot       test_g_resolve
    st_add st_chk_resolve_doh       test_c_resolve_doh       test_g_resolve
    st_add st_chk_dnssec_failed_org test_c_dnssec_failed_org test_g_resolve
    st_add st_chk_dnssec_verteilte  test_c_dnssec_verteilte  test_g_resolve

    st_add st_chk_leak_sockets      test_c_leak_sockets      test_g_leak
    st_add st_chk_leak_nft          test_c_leak_nft          test_g_leak
    st_add st_chk_leak_tcpdump      test_c_leak_tcpdump      test_g_leak

    st_add st_chk_upstream_rotation test_c_upstream_rotation test_g_policy
    return 0
}

st_run_named_suite() {
    local builder="$1" title="$2" rc=0
    print_header "$title"
    if ! selftest_prepare; then
        return 1
    fi
    echo -e "  ${DIM}$(tf test_ctx_line "$ST_DOMAIN" "$ST_DOH_PORT" "$ST_DOT_PORT")${NC}"
    st_reset_tally
    "$builder"
    st_run_suite
    st_print_tally || rc=$?
    cleanup_temp_files
    return "$rc"
}

selftest_quick() {
    st_run_named_suite st_suite_quick "$(t test_title_quick)"
}

selftest_full() {
    st_run_named_suite st_suite_full "$(t test_title_full)"
}

# Отдельная кнопка: проверка снаружи через третью сторону. Только после
# явного согласия — она раскрывает host:port постороннему сервису.
selftest_external_check() {
    local rc=0
    print_header "$(t test_title_external)"
    if ! selftest_prepare; then
        return 1
    fi
    echo -e "  ${DIM}$(t test_ext_discloses)${NC}"
    echo -e "  ${DIM}$(t test_ext_no_secret)${NC}"
    echo ""
    if ! confirm "$(t test_ext_confirm)" n; then
        log_info "$(t test_ext_declined)"
        cleanup_temp_files
        return 0
    fi
    ST_ALLOW_THIRDPARTY=1
    st_reset_tally
    ST_SUITE=()
    st_add st_chk_external_reach test_c_external_reach test_g_listen
    st_run_suite
    st_print_tally || rc=$?
    ST_ALLOW_THIRDPARTY=0
    cleanup_temp_files
    return "$rc"
}

# Отдельная кнопка: аудит утечки открытого DNS.
selftest_leak_audit() {
    local rc=0
    print_header "$(t test_title_leak)"
    if ! selftest_prepare; then
        return 1
    fi
    st_reset_tally
    ST_SUITE=()
    st_add st_chk_leak_sockets  test_c_leak_sockets  test_g_leak
    st_add st_chk_leak_nft      test_c_leak_nft      test_g_leak
    st_add st_chk_leak_tcpdump  test_c_leak_tcpdump  test_g_leak
    st_run_suite
    st_print_tally || rc=$?
    cleanup_temp_files
    return "$rc"
}
