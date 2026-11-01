#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — Обнаружение, проверка и перезагрузка TLS-сертификатов.
#
# Главная задача модуля: НАЙТИ сертификат, который сосед (goVLESS Pro или
# goTelegram Pro) уже выпустил на этом сервере, и предложить оператору
# переиспользовать его — не выпуская второй, не трогая чужой ACME-конфиг и
# не копируя приватный ключ никуда.
#
# Контракт (см. lib/common.sh):
#   • всё логирование — в stderr, stdout занят возвращаемыми значениями;
#   • jq НЕТ: JSON читаем через json_get (python3);
#   • ни один внешний бинарник (openssl, sqlite3, runuser, acme.sh) не
#     обязателен — отсутствие любого из них не должно ронять сканирование;
#   • последняя команда функции никогда не `[ … ] && …`;
#   • ни одного `cmd | head` в подстановке: head закрывает канал, писатель
#     умирает по SIGPIPE, pipefail делает подстановку неуспешной, и
#     `x=$(…) || x=""` выбрасывает УЖЕ ПОЛУЧЕННОЕ значение.
#
# shellcheck disable=SC2148

# ── i18n: заглушка на случай, если модуль подключён раньше lib/i18n.sh ───
# Настоящие t/tf из lib/i18n.sh, подключённые ПОЗЖЕ, просто переопределят
# эти. Заглушка нужна, чтобы модуль можно было прогнать тестами в одиночку.
if ! declare -f t >/dev/null 2>&1; then
    t()  { echo "$1"; }
    tf() { local _k="$1" _a; shift; printf '%s' "$_k"
           for _a in "$@"; do printf ' [%s]' "$_a"; done; echo ""; }
fi

# ── Пути ────────────────────────────────────────────────────────────────
# Значения из lib/common.sh не перекрываются (:= срабатывает только на
# пустом/неустановленном), но позволяют прогнать модуль изолированно.
: "${LE_ROOT:=/etc/letsencrypt}"
: "${GOVLESS_CONFIG_PATH:=/opt/govless/config.json}"
: "${GOTELEGRAM_CONFIG_PATH:=/opt/gotelegram/config.json}"
: "${TELEMT_CONFIG_PATH:=/etc/telemt/config.toml}"
: "${XUI_DB_PATH:=/etc/x-ui/x-ui.db}"
: "${GODNS_SERVICE:=dnsdist}"

GOVLESS_IP_CERT_DIR="${GOVLESS_IP_CERT_DIR:-/root/cert/ip}"
LE_HOOK_DIR="${LE_HOOK_DIR:-${LE_ROOT}/renewal-hooks/deploy}"
LE_HOOK_FILE="${LE_HOOK_FILE:-${LE_HOOK_DIR}/50-godns.sh}"
CERT_WATCH_UNIT="${CERT_WATCH_UNIT:-godns-cert-reload}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"

# ── Результат сканирования ──────────────────────────────────────────────
# Каждая запись — 8 полей через TAB:
#   1 domain  2 cert_path  3 key_path  4 source_label  5 days_left
#   6 san_summary  7 usable(yes|no)  8 why_not
#
# ВНИМАНИЕ при разборе записи: `IFS=$'\t' read -r a b c …` НЕ ГОДИТСЯ.
# TAB входит в «пробельные» символы IFS, поэтому два подряд идущих TAB
# (а пустые поля тут норма) схлопываются в один разделитель, и все поля
# уезжают. Разбирать только через certs_field() (cut -f), он не схлопывает.
#
# source_label и why_not хранят КЛЮЧИ i18n, а не готовый текст.
# Метка источника может нести один аргумент после '|' — он подставляется
# через tf() при отрисовке (домен/режим в имени соседа не содержит '|').
CERT_CANDIDATES=()
CERT_SEEN_PATHS=""

CERT_TAB=$'\t'

# ═══════════════════════════════════════════════════════════════════════
# Примитивы поверх openssl
# ═══════════════════════════════════════════════════════════════════════

certs_have_openssl() { command -v openssl >/dev/null 2>&1; }

# Текст расширения subjectAltName. -ext есть в OpenSSL 1.1.1+, но НЕ в
# LibreSSL, поэтому обязателен запасной разбор через -text.
_certs_san_raw() {
    local cert="$1" raw=""
    certs_have_openssl || { printf ''; return 1; }
    [ -r "$cert" ] || { printf ''; return 1; }

    raw=$(openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null </dev/null) || raw=""
    case "$raw" in
        *DNS:*|*IP:*|*"IP Address:"*|*email:*|*URI:*) printf '%s' "$raw"; return 0 ;;
    esac

    # Без `exit` в awk: ранний выход убил бы openssl по SIGPIPE, а pipefail
    # превратил бы это в «команда не удалась».
    raw=$(openssl x509 -in "$cert" -noout -text 2>/dev/null </dev/null \
          | awk '
                take && out == "" { out = $0 }
                /X509v3 Subject Alternative Name/ { take = 1 }
                END { print out }
            ') || raw=""
    printf '%s' "$raw"
    return 0
}

# Список SAN через запятую: "DNS:a.example.com,DNS:*.example.com,IP:1.2.3.4"
# Пусто (rc1), если расширения нет вовсе.
certs_san_list() {
    local cert="$1" raw="" out=""
    raw=$(_certs_san_raw "$cert") || raw=""
    [ -n "$raw" ] || { printf ''; return 1; }

    out=$(printf '%s' "$raw" | tr ',' '\n' \
        | sed -n -E 's/^[[:space:]]*(DNS|IP Address|IP|email|URI|othername|Registered ID)[[:space:]]*:[[:space:]]*(.*[^[:space:]])[[:space:]]*$/\1:\2/p' \
        | sed -e 's/^IP Address:/IP:/' \
        | awk 'NR>1 { printf "," } { printf "%s", $0 } END { if (NR) printf "\n" }') || out=""

    printf '%s' "$out"
    if [ -n "$out" ]; then return 0; fi
    return 1
}

# Первая строка вывода — без `head`, чтобы не ловить SIGPIPE.
_certs_first_line() {
    local s="${1:-}"
    printf '%s' "${s%%$'\n'*}"
    return 0
}

# Значение поля -subject / -issuer, без префикса и лишних пробелов.
_certs_dn() {
    local cert="$1" what="$2" line=""
    certs_have_openssl || { printf ''; return 1; }
    [ -r "$cert" ] || { printf ''; return 1; }
    line=$(openssl x509 -in "$cert" -noout "-$what" 2>/dev/null </dev/null) || line=""
    line=$(_certs_first_line "$line")
    line="${line#${what}=}"
    line=$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    printf '%s' "$line"
    return 0
}

# CN из subject, в нижнем регистре. Пусто, если CN нет.
_certs_cn() {
    local cert="$1" dn="" cn=""
    dn=$(_certs_dn "$cert" subject) || dn=""
    [ -n "$dn" ] || { printf ''; return 1; }
    cn=$(printf '%s' "$dn" | sed -n -E 's|.*CN[[:space:]]*=[[:space:]]*([^,/]+).*|\1|p')
    cn=$(_certs_first_line "$cn")
    cn=$(printf '%s' "$cn" | sed -e 's/[[:space:]]*$//')
    printf '%s' "$cn"
    if [ -n "$cn" ]; then return 0; fi
    return 1
}

# rc0 — самоподписанный (issuer совпадает с subject).
# Именно так распознаётся аварийный CN=goVLESS-panel: goVLESS выпускает его,
# когда Let's Encrypt на голый IP не дался три раза подряд.
certs_is_self_signed() {
    local cert="$1" s="" i=""
    s=$(_certs_dn "$cert" subject) || s=""
    i=$(_certs_dn "$cert" issuer)  || i=""
    if [ -n "$s" ] && [ "$s" = "$i" ]; then return 0; fi
    return 1
}

# Дата истечения сертификата как YYYY-MM-DD; пусто — если не прочитать.
# Отдельно от certs_days_left: панель обязана показывать ДАТУ, а не только
# «осталось дней». Число без даты не отвечает на вопрос «а когда именно»,
# и рядом с датой оплаты домена читалось как срок домена.
certs_expiry_iso() {
    local cert="$1" line="" end=""
    if ! certs_have_openssl || [ ! -r "$cert" ]; then return 1; fi
    line=$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null </dev/null) || return 1
    line=$(_certs_first_line "$line")
    end="${line#notAfter=}"
    [ -n "$end" ] && [ "$end" != "$line" ] || return 1
    python3 -c '
import sys, datetime
raw = sys.argv[1].strip()
for suffix in (" GMT", " UTC"):
    if raw.endswith(suffix):
        raw = raw[: -len(suffix)]
        break
for fmt in ("%b %d %H:%M:%S %Y", "%Y%m%d%H%M%SZ", "%Y-%m-%d %H:%M:%S"):
    try:
        print(datetime.datetime.strptime(raw, fmt).date().isoformat())
        break
    except ValueError:
        continue
' "$end" 2>/dev/null
}

# Целое число дней до notAfter; "?" — если прочитать не удалось.
certs_days_left() {
    local cert="$1" line="" end="" days=""
    if ! certs_have_openssl || [ ! -r "$cert" ]; then printf '?'; return 1; fi
    line=$(openssl x509 -in "$cert" -noout -enddate 2>/dev/null </dev/null) || line=""
    line=$(_certs_first_line "$line")
    end="${line#notAfter=}"
    if [ -z "$end" ] || [ "$end" = "$line" ]; then printf '?'; return 1; fi

    # ГРАБЛИ: heredoc внутри $( ), которая закрывается на той же строке,
    # не работает — тело heredoc окажется ЗА пределами подстановки.
    # Поэтому здесь python3 -c со строкой в одинарных кавычках (внутри
    # кода не должно быть ни одной одинарной кавычки).
    days=$(python3 -c '
import sys, datetime
raw = sys.argv[1].strip()
for suffix in (" GMT", " UTC"):
    if raw.endswith(suffix):
        raw = raw[: -len(suffix)]
        break
end = None
for fmt in ("%b %d %H:%M:%S %Y", "%Y%m%d%H%M%SZ", "%Y-%m-%d %H:%M:%S"):
    try:
        end = datetime.datetime.strptime(raw, fmt)
        break
    except ValueError:
        continue
if end is None:
    sys.stdout.write("?")
else:
    now = datetime.datetime.utcnow()
    sys.stdout.write(str(int((end - now).total_seconds() // 86400)))
' "$end" 2>/dev/null) || days=""
    if [ -z "$days" ] || [ "$days" = "?" ]; then printf '?'; return 1; fi
    printf '%s' "$days"
    return 0
}

# rc0 — сертификат покрывает имя. Wildcard ровно на одну метку:
#   *.example.com  →  dns.example.com  ДА
#   *.example.com  →  example.com      НЕТ
#   *.example.com  →  a.b.example.com  НЕТ
certs_covers_name() {
    local cert="$1" name="$2" sans="" entry="" val="" lname="" suffix="" prefix="" cn=""

    [ -n "$name" ] || return 1
    lname=$(printf '%s' "$name" | tr 'A-Z' 'a-z')
    lname="${lname%.}"

    sans=$(certs_san_list "$cert") || sans=""
    if [ -z "$sans" ]; then
        # Сертификат без SAN современные клиенты и так не примут, но CN
        # проверить дешевле, чем врать оператору «имя не покрыто».
        cn=$(_certs_cn "$cert") || cn=""
        cn=$(printf '%s' "$cn" | tr 'A-Z' 'a-z')
        if [ -n "$cn" ] && [ "$cn" = "$lname" ]; then return 0; fi
        return 1
    fi

    while IFS= read -r entry; do
        [ -n "$entry" ] || continue
        val="${entry#*:}"
        val=$(printf '%s' "$val" | tr 'A-Z' 'a-z')
        val="${val%.}"
        case "$entry" in
            DNS:*|dns:*)
                case "$val" in
                    '*.'*)
                        suffix="${val#\*}"
                        case "$lname" in
                            *"$suffix")
                                prefix="${lname%$suffix}"
                                if [ -n "$prefix" ]; then
                                    case "$prefix" in
                                        *.*) : ;;
                                        *)   return 0 ;;
                                    esac
                                fi
                                ;;
                        esac
                        ;;
                    *)
                        if [ "$val" = "$lname" ]; then return 0; fi
                        ;;
                esac
                ;;
            IP:*|ip:*)
                # IP-SAN спасает только клиента, который сам обращается по IP.
                if [ "$val" = "$lname" ]; then return 0; fi
                ;;
        esac
    done <<EOF
$(printf '%s' "$sans" | tr ',' '\n')
EOF
    return 1
}

# rc0 — пара пригодна: оба файла читаются, сертификат жив ещё сутки,
# ключ действительно от этого сертификата.
certs_validate() {
    local cert="${1:-}" key="${2:-}" pub_cert="" pub_key=""

    [ -n "$cert" ] || return 1
    [ -n "$key" ]  || return 1
    [ -r "$cert" ] || return 1
    [ -r "$key" ]  || return 1
    certs_have_openssl || return 1

    # Тот же тест, которым goVLESS решает «переиспользовать или выпускать».
    openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null 2>&1 </dev/null || return 1

    # </dev/null обязателен: зашифрованный ключ иначе повесит установщик
    # на приглашении ввести пароль.
    pub_cert=$(openssl x509 -in "$cert" -noout -pubkey 2>/dev/null </dev/null) || pub_cert=""
    pub_key=$(openssl pkey -in "$key" -pubout 2>/dev/null </dev/null) || pub_key=""
    if [ -z "$pub_cert" ] || [ -z "$pub_key" ]; then return 1; fi
    if [ "$pub_cert" = "$pub_key" ]; then return 0; fi
    return 1
}

# Основное имя сертификата: первый DNS-SAN, иначе CN.
certs_primary_name() {
    local cert="$1" sans="" name=""
    sans=$(certs_san_list "$cert") || sans=""
    if [ -n "$sans" ]; then
        name=$(printf '%s' "$sans" | tr ',' '\n' | sed -n -E 's/^DNS:(.+)$/\1/p')
        name=$(_certs_first_line "$name")
    fi
    if [ -z "$name" ]; then
        name=$(_certs_cn "$cert") || name=""
    fi
    printf '%s' "$name"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Сбор кандидатов
# ═══════════════════════════════════════════════════════════════════════

_certs_seen() {
    local p="$1"
    case "${CERT_SEEN_PATHS}" in
        *"|${p}|"*) return 0 ;;
    esac
    return 1
}

# _certs_add <domain> <cert> <key> <label> [force_why]
# Пустой <domain> — вывести имя из самого сертификата.
# force_why — «непригоден в любом случае», причина используется, только если
# более конкретная не нашлась.
_certs_add() {
    local domain="${1:-}" cert="${2:-}" key="${3:-}" label="${4:-}" force_why="${5:-}"
    local days="?" sans="" usable="yes" why=""

    [ -n "$cert" ] || return 0
    if _certs_seen "$cert"; then return 0; fi

    if [ ! -r "$cert" ] || [ ! -r "$key" ]; then
        usable="no"; why="certs_why_no_files"
    elif ! certs_have_openssl; then
        usable="no"; why="certs_why_no_openssl"
    else
        days=$(certs_days_left "$cert") || days="?"
        sans=$(certs_san_list "$cert")  || sans=""
        if [ -z "$domain" ]; then
            domain=$(certs_primary_name "$cert") || domain=""
        fi

        if certs_is_self_signed "$cert"; then
            usable="no"; why="certs_why_self_signed"
        elif ! certs_validate "$cert" "$key"; then
            usable="no"
            if openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null 2>&1 </dev/null; then
                why="certs_why_key_mismatch"
            else
                why="certs_why_expired"
            fi
        elif [ -z "$sans" ]; then
            # Ни одного SAN: браузеры и современные DNS-клиенты такой
            # сертификат отвергают, на CN уже никто не смотрит.
            usable="no"; why="certs_why_no_san"
        else
            # Сертификат только с IP-SAN бесполезен резолверу, к которому
            # клиент приходит ПО ИМЕНИ: проверка имени провалится у каждого.
            case "$sans" in
                *DNS:*) : ;;
                *)      usable="no"; why="certs_why_ip_only" ;;
            esac
        fi
    fi

    if [ -n "$force_why" ]; then
        usable="no"
        [ -n "$why" ] || why="$force_why"
    fi

    [ -n "$domain" ] || domain="-"

    CERT_CANDIDATES+=("${domain}${CERT_TAB}${cert}${CERT_TAB}${key}${CERT_TAB}${label}${CERT_TAB}${days}${CERT_TAB}${sans}${CERT_TAB}${usable}${CERT_TAB}${why}")
    CERT_SEEN_PATHS="${CERT_SEEN_PATHS}|${cert}|"
    return 0
}

# ── Источник 2b: [censorship] tls_domain из TOML telemt ─────────────────
# Зеркало get_config_value() из goTelegram (lib/telemt_config.sh:302-375):
# TOML-парсера в системе нет, и тащить его ради одного ключа незачем.
# awk читает ФАЙЛ, а не канал, поэтому ранний exit тут безопасен.
certs_telemt_toml_get() {
    local key="$1" cfg="${2:-$TELEMT_CONFIG_PATH}" out=""
    [ -r "$cfg" ] || { printf ''; return 1; }
    case "$key" in
        tls_domain)
            out=$(awk '
                /^\[censorship\]/ { in_cens=1; next }
                /^\[/ && in_cens   { exit }
                in_cens && $1 == "tls_domain" {
                    sub(/^[^=]*=[[:space:]]*"/, "")
                    sub(/".*$/, "")
                    print
                    exit
                }' "$cfg") || out="" ;;
        tls_emulation)
            out=$(awk '
                /^\[censorship\]/ { in_cens=1; next }
                /^\[/ && in_cens   { exit }
                in_cens && $1 == "tls_emulation" {
                    sub(/^[^=]*=[[:space:]]*/, "")
                    gsub(/[[:space:]"]/, "")
                    print
                    exit
                }' "$cfg") || out="" ;;
        dns_overrides)
            out=$(awk '
                /^\[network\]/   { in_net=1; next }
                /^\[/ && in_net  { exit }
                in_net && $1 == "dns_overrides" { print "yes"; exit }' "$cfg") || out="" ;;
        port)
            out=$(awk '
                /^\[server\]/     { in_srv=1; next }
                /^\[/ && in_srv   { exit }
                in_srv && $1 == "port" {
                    sub(/^[^=]*=[[:space:]]*/, "")
                    gsub(/[[:space:]"]/, "")
                    print
                    exit
                }' "$cfg") || out="" ;;
        *) out="" ;;
    esac
    printf '%s' "$out"
    if [ -n "$out" ]; then return 0; fi
    return 1
}

# Домен, которым goTelegram РЕАЛЬНО владеет. Пусто (rc1) — своего домена нет.
#
# ГРАБЛИ. В режиме Lite config.json.domain пустой, а .mask_host содержит
# ЧУЖОЙ домен (google.com и подобные) — это цель маскировки фейкового TLS,
# а не наш хост. Принять mask_host за свой домен = искать сертификат на
# google.com и предложить оператору чужое имя. Поэтому mask_host здесь не
# читается НИКОГДА, а tls_domain из TOML берётся только когда режим
# достоверно pro.
certs_gotelegram_domain() {
    local mode="" domain="" emu="" ovr=""

    domain=$(json_get "$GOTELEGRAM_CONFIG_PATH" domain "") || domain=""
    if [ -n "$domain" ]; then printf '%s' "$domain"; return 0; fi

    mode=$(json_get "$GOTELEGRAM_CONFIG_PATH" mode "") || mode=""
    if [ -z "$mode" ]; then
        # Режим не записан (доисторическая установка) — восстанавливаем
        # эвристикой самого goTelegram: pro, если TLS не эмулируется или
        # есть dns_overrides.
        emu=$(certs_telemt_toml_get tls_emulation) || emu=""
        ovr=$(certs_telemt_toml_get dns_overrides) || ovr=""
        if [ "$emu" = "false" ] || [ -n "$ovr" ]; then mode="pro"; fi
    fi

    if [ "$mode" = "pro" ]; then
        domain=$(certs_telemt_toml_get tls_domain) || domain=""
        printf '%s' "$domain"
        if [ -n "$domain" ]; then return 0; fi
        return 1
    fi

    printf ''
    return 1
}

# /etc/letsencrypt/live/<d>/fullchain.pem → <d>; иначе пусто.
_certs_domain_from_le_path() {
    local p="${1:-}" d=""
    case "$p" in
        "${LE_ROOT}/live/"*)
            d="${p#${LE_ROOT}/live/}"
            d="${d%%/*}"
            ;;
    esac
    printf '%s' "$d"
    return 0
}

# ── Источник 4: пути из базы 3X-UI ──────────────────────────────────────
# Оператор мог задать их прямо в панели — иначе о них никто не узнает.
# sqlite3 на минимальных образах отсутствует: тогда источник пропускается,
# а сканирование продолжается.
_certs_scan_xui() {
    local rows="" k="" v="" web_c="" web_k="" sub_c="" sub_k="" line="" dom=""

    [ -r "$XUI_DB_PATH" ] || return 0
    if ! command -v sqlite3 >/dev/null 2>&1; then
        log_dim "$(t certs_sqlite_missing)"
        return 0
    fi

    rows=$(sqlite3 -readonly -noheader -separator '|' "$XUI_DB_PATH" \
        "SELECT key,value FROM settings WHERE key IN ('webCertFile','webKeyFile','subCertFile','subKeyFile');" 2>/dev/null) || rows=""
    if [ -z "$rows" ]; then
        # ГРАБЛИ: запасной путь (старый sqlite3 без -readonly) открывал
        # ЧУЖУЮ базу обычным способом. Сам SELECT ничего не пишет, но
        # sqlite может завести рядом -wal и -shm — файлы в чужом каталоге,
        # которых там до нас не было. URI mode=ro закрывает и это.
        rows=$(sqlite3 -noheader -separator '|' "file:${XUI_DB_PATH}?mode=ro" \
            "SELECT key,value FROM settings WHERE key IN ('webCertFile','webKeyFile','subCertFile','subKeyFile');" 2>/dev/null) || rows=""
    fi
    [ -n "$rows" ] || return 0

    # У `key` в settings нет UNIQUE — дубли реальны; побеждает последняя
    # непустая строка.
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        k="${line%%|*}"
        v="${line#*|}"
        [ -n "$v" ] || continue
        case "$k" in
            webCertFile) web_c="$v" ;;
            webKeyFile)  web_k="$v" ;;
            subCertFile) sub_c="$v" ;;
            subKeyFile)  sub_k="$v" ;;
        esac
    done <<EOF
$rows
EOF

    if [ -n "$web_c" ] && [ -n "$web_k" ] && [ -f "$web_c" ] && [ -f "$web_k" ]; then
        dom=$(_certs_domain_from_le_path "$web_c")
        _certs_add "$dom" "$web_c" "$web_k" "certs_src_xui_web"
    fi
    if [ -n "$sub_c" ] && [ -n "$sub_k" ] && [ -f "$sub_c" ] && [ -f "$sub_k" ]; then
        dom=$(_certs_domain_from_le_path "$sub_c")
        _certs_add "$dom" "$sub_c" "$sub_k" "certs_src_xui_sub"
    fi
    return 0
}

# ── Главный сканер ──────────────────────────────────────────────────────
certs_discover() {
    local domain="" mode="" dir="" name="" cert="" key="" ip=""

    CERT_CANDIDATES=()
    CERT_SEEN_PATHS=""

    log_step "$(t certs_scan_start)"

    if ! certs_have_openssl; then
        log_warning "$(t certs_openssl_missing)"
    fi

    # 1. goVLESS Pro — единственный достоверный источник его домена.
    #    config.json.mask_domain — цель маскировки Reality (google.com),
    #    НЕ наш домен; читать её здесь нельзя.
    domain=$(json_get "$GOVLESS_CONFIG_PATH" domain "") || domain=""
    mode=$(json_get "$GOVLESS_CONFIG_PATH" mode "")     || mode=""
    if [ -n "$domain" ]; then
        cert="${LE_ROOT}/live/${domain}/fullchain.pem"
        key="${LE_ROOT}/live/${domain}/privkey.pem"
        if [ -f "$cert" ]; then
            _certs_add "$domain" "$cert" "$key" "certs_src_govless|${mode:-pro}"
        else
            log_dim "$(tf certs_govless_cert_absent "$cert")"
        fi
    elif [ -f "$GOVLESS_CONFIG_PATH" ]; then
        log_dim "$(t certs_govless_lite_no_cert)"
    fi

    # 2. goTelegram Pro.
    domain=$(certs_gotelegram_domain) || domain=""
    if [ -n "$domain" ]; then
        cert="${LE_ROOT}/live/${domain}/fullchain.pem"
        key="${LE_ROOT}/live/${domain}/privkey.pem"
        if [ -f "$cert" ]; then
            _certs_add "$domain" "$cert" "$key" "certs_src_gotelegram|${domain}"
        else
            log_dim "$(tf certs_gotelegram_cert_absent "$cert")"
        fi
    elif [ -f "$GOTELEGRAM_CONFIG_PATH" ] || [ -f "$TELEMT_CONFIG_PATH" ]; then
        log_dim "$(t certs_lite_no_cert)"
    fi

    # 3. Любые прочие линии Let's Encrypt.
    for dir in "${LE_ROOT}"/live/*; do
        [ -d "$dir" ] || continue
        dir="${dir%/}"
        name="$(basename "$dir")"
        case "$name" in
            README|'*') continue ;;
        esac
        cert="${dir}/fullchain.pem"
        key="${dir}/privkey.pem"
        [ -f "$cert" ] || continue
        if _certs_seen "$cert"; then continue; fi
        _certs_add "" "$cert" "$key" "certs_src_letsencrypt|${name}"
    done

    # 4. Пути, прописанные в базе 3X-UI.
    _certs_scan_xui || true

    # 5. Сертификат goVLESS на голый IP (acme.sh) — ВСЕГДА непригоден для
    #    резолвера, к которому обращаются по имени хоста: SAN там IP:<addr>,
    #    и проверка имени падает у любого клиента. Аварийный вариант
    #    (CN=goVLESS-panel) вдобавок самоподписанный.
    if [ -d "$GOVLESS_IP_CERT_DIR" ]; then
        for cert in "${GOVLESS_IP_CERT_DIR}"/*.crt; do
            [ -f "$cert" ] || continue
            key="${cert%.crt}.key"
            ip="$(basename "$cert")"; ip="${ip%.crt}"
            _certs_add "$ip" "$cert" "$key" "certs_src_acme_ip|${ip}" "certs_why_ip_only"
        done
    fi

    if [ "${#CERT_CANDIDATES[@]}" -eq 0 ]; then
        log_warning "$(t certs_scan_none)"
        return 1
    fi
    log_success "$(tf certs_scan_found "${#CERT_CANDIDATES[@]}")"
    return 0
}

# ── Доступ к записям ────────────────────────────────────────────────────
certs_count() { printf '%s' "${#CERT_CANDIDATES[@]}"; return 0; }

# certs_field <1-based index> <1..8>
# cut, а не read: см. предупреждение о схлопывании TAB выше.
certs_field() {
    local idx="${1:-0}" field="${2:-1}" rec=""
    case "$idx" in ''|*[!0-9]*) printf ''; return 1 ;; esac
    [ "$idx" -ge 1 ] || { printf ''; return 1; }
    [ "$idx" -le "${#CERT_CANDIDATES[@]}" ] || { printf ''; return 1; }
    rec="${CERT_CANDIDATES[$((idx-1))]}"
    printf '%s' "$rec" | cut -d"$CERT_TAB" -f"$field"
    return 0
}

certs_domain()   { certs_field "$1" 1; }
certs_cert_path(){ certs_field "$1" 2; }
certs_key_path() { certs_field "$1" 3; }
certs_usable()   { certs_field "$1" 7; }

# ── Отрисовка метки источника ───────────────────────────────────────────
_certs_render_label() {
    local raw="${1:-}" key="" arg=""
    [ -n "$raw" ] || { printf ''; return 0; }
    case "$raw" in
        *"|"*) key="${raw%%|*}"; arg="${raw#*|}"; printf '%s' "$(tf "$key" "$arg")" ;;
        *)     printf '%s' "$(t "$raw")" ;;
    esac
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Меню выбора — визуальная идиома goVLESS
# ═══════════════════════════════════════════════════════════════════════
certs_render_menu() {
    local n="${#CERT_CANDIDATES[@]}" i=1
    local domain="" cert="" label="" days="" sans="" usable="" why="" shown="" any=0

    print_header "$(t certs_menu_title)"

    if [ "$n" -eq 0 ]; then
        log_warning "$(t certs_scan_none)"
        return 1
    fi

    while [ "$i" -le "$n" ]; do
        domain=$(certs_field "$i" 1) || domain=""
        cert=$(certs_field   "$i" 2) || cert=""
        label=$(certs_field  "$i" 4) || label=""
        days=$(certs_field   "$i" 5) || days=""
        sans=$(certs_field   "$i" 6) || sans=""
        usable=$(certs_field "$i" 7) || usable=""
        why=$(certs_field    "$i" 8) || why=""

        shown="$sans"
        if [ "${#shown}" -gt 58 ]; then
            shown="$(printf '%s' "$shown" | cut -c1-55)…"
        fi

        if [ "$usable" = "yes" ]; then
            any=1
            echo -e "  ${CYAN}${i})${NC} ${BOLD}${domain}${NC}  ${DIM}— $(_certs_render_label "$label")${NC}"
            echo -e "     ${GREEN}✓${NC} ${DIM}$(tf certs_days_left_fmt "${days:-?}")${NC}"
        else
            echo -e "  ${DIM}${i}) ${domain}  — $(_certs_render_label "$label")${NC}"
            echo -e "     ${YELLOW}✗${NC} ${DIM}$(t "${why:-certs_why_no_files}")${NC}"
        fi
        echo -e "     ${DIM}${cert}${NC}"
        if [ -n "$shown" ]; then
            echo -e "     ${DIM}SAN: ${shown}${NC}"
        fi
        i=$((i+1))
    done

    echo ""
    if [ "$any" -eq 0 ]; then
        log_warning "$(t certs_none_usable)"
    fi
    # Подсказку «введите номер…» здесь НЕ печатаем: мастер после списка
    # дорисовывает свои пункты («Выпустить новый», «Назад»), и подсказка
    # оказывалась ПОСЕРЕДИНЕ меню — между сертификатами и пунктом 2).
    # Печатает её тот, кто спрашивает: certs_select и сам мастер.
    return 0
}

# Печатает в stdout выбранный НОМЕР (1..N); rc1 — оператор отказался.
certs_select() {
    local choice="" n="${#CERT_CANDIDATES[@]}" usable=""
    certs_render_menu || return 1
    log_dim "$(t certs_menu_hint)"
    echo -ne "  ▸ " >&2
    read -r choice || { echo "" >&2; return 1; }
    case "$choice" in
        ''|0)     return 1 ;;
        *[!0-9]*) log_warning "$(t certs_bad_choice)"; return 1 ;;
    esac
    if [ "$choice" -lt 1 ] || [ "$choice" -gt "$n" ]; then
        log_warning "$(t certs_bad_choice)"
        return 1
    fi
    usable=$(certs_field "$choice" 7) || usable=""
    if [ "$usable" != "yes" ]; then
        if ! confirm "$(t certs_pick_unusable_confirm)" n; then return 1; fi
    fi
    printf '%s' "$choice"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Доступ демона к приватному ключу
# ═══════════════════════════════════════════════════════════════════════
#
# /etc/letsencrypt/{live,archive} у certbot — 0700 root:root. Поэтому chmod
# на один privkey.pem не даёт НИЧЕГО: обход каталога всё равно запрещён.
# Штатный для дистрибутива путь — группа ssl-cert: certbot сознательно
# сохраняет группу и права privkey.pem при продлении, так что настройка
# делается один раз и переживает все обновления. Ключ никуда НЕ копируется:
# вторая копия приватного ключа расходится с оригиналом и удваивает ущерб
# при компрометации.
certs_grant_read_access() {
    local domain="${1:-}" svcuser="${2:-}"
    local le="${LE_ROOT}" live="" arch="" runuser_bin=""

    [ -n "$domain" ]  || { log_error "$(t certs_grant_no_domain)"; return 1; }
    [ -n "$svcuser" ] || { log_error "$(t certs_grant_no_user_arg)"; return 1; }

    # Развёртывание зовёт выдачу прав дважды (мастер — явно, engine_apply —
    # для самозащиты), и оператор видел «Открываю доступ…» два раза подряд,
    # что читается как сбой первой попытки. Повторный вызов для уже
    # ДОКАЗАННОЙ пары домен+пользователь выполняем — права могли смениться
    # снаружи, — но молча: сообщения только при провале.
    local _grant_quiet="no"
    if [ "${GODNS_GRANT_PROVED:-}" = "${domain}|${svcuser}" ]; then
        _grant_quiet="yes"
    fi

    if [ "$svcuser" = "root" ]; then
        log_dim "$(t certs_grant_root)"
        return 0
    fi

    live="${le}/live/${domain}"
    arch="${le}/archive/${domain}"
    if [ ! -d "$live" ]; then
        # Линии certbot нет — сертификат живёт вне /etc/letsencrypt (пути
        # из панели 3X-UI, acme.sh в /root/cert). Групповой трюк с
        # каталогами certbot к нему неприменим; выдаём право на САМИ файлы.
        # Раньше здесь стоял безусловный отказ, и любой не-letsencrypt
        # сертификат было невозможно развернуть вовсе.
        _certs_grant_files_access "$svcuser"
        return $?
    fi
    if ! id "$svcuser" >/dev/null 2>&1; then
        log_error "$(tf certs_grant_no_user "$svcuser")"
        return 1
    fi

    [ "$_grant_quiet" = "yes" ] || log_step "$(tf certs_grant_start "$svcuser")"

    if ! getent group ssl-cert >/dev/null 2>&1; then
        if command -v addgroup >/dev/null 2>&1; then
            addgroup --system ssl-cert >/dev/null 2>&1 || true
        fi
        if ! getent group ssl-cert >/dev/null 2>&1; then
            if command -v groupadd >/dev/null 2>&1; then
                groupadd --system ssl-cert >/dev/null 2>&1 || true
            fi
        fi
        if ! getent group ssl-cert >/dev/null 2>&1; then
            log_error "$(t certs_grant_group_fail)"
            return 1
        fi
    fi

    case " $(id -nG "$svcuser" 2>/dev/null || true) " in
        *" ssl-cert "*) : ;;
        *)
            if ! usermod -aG ssl-cert "$svcuser" >/dev/null 2>&1; then
                log_error "$(tf certs_grant_user_fail "$svcuser")"
                return 1
            fi
            ;;
    esac

    # 0750, не 0755: 0755 показал бы список всех доменов сервера любому
    # локальному пользователю.
    if ! chgrp ssl-cert "${le}/live" "${le}/archive" 2>/dev/null; then
        log_error "$(tf certs_grant_chmod_fail "${le}/live")"
        return 1
    fi
    if ! chmod 0750 "${le}/live" "${le}/archive" 2>/dev/null; then
        log_error "$(tf certs_grant_chmod_fail "${le}/live")"
        return 1
    fi

    chgrp -R ssl-cert "$live" 2>/dev/null || true
    chmod 0750 "$live" 2>/dev/null || true
    # ГРАБЛИ: раньше здесь стоял только privkey*.pem — исходя из того, что
    # certbot кладёт публичные части 0644. Это НЕ гарантия: на живом сервере
    # fullchain.pem оказался 0600 root:ssl-cert, dnsdist упал с «Permission
    # denied» на fullchain, а проверка ниже щупала только ключ и рапортовала
    # успех. Публичные части секрета не содержат — они летят в каждом
    # TLS-рукопожатии, — поэтому 0640 на них безопасен.
    chmod 0640 "$live"/privkey*.pem "$live"/cert*.pem \
               "$live"/chain*.pem "$live"/fullchain*.pem 2>/dev/null || true
    if [ -d "$arch" ]; then
        chgrp -R ssl-cert "$arch" 2>/dev/null || true
        chmod 0750 "$arch" 2>/dev/null || true
        # Реальные файлы лежат именно тут; live/ — симлинки на них.
        chmod 0640 "$arch"/privkey*.pem "$arch"/cert*.pem \
                   "$arch"/chain*.pem "$arch"/fullchain*.pem 2>/dev/null || true
    fi

    # Доказываем, а не верим: под целевым пользователем с новой группой.
    # runuser лежит в util-linux, который есть даже на минимальном образе.
    runuser_bin=$(command -v runuser 2>/dev/null) || runuser_bin=""
    if [ -z "$runuser_bin" ] && [ -x /usr/sbin/runuser ]; then
        runuser_bin=/usr/sbin/runuser
    fi
    if [ -z "$runuser_bin" ]; then
        log_warning "$(t certs_grant_unproven)"
        return 2
    fi

    # Проверять ОБА файла. Служба открывает и ключ, и цепочку; проверка
    # одного лишь ключа однажды уже «доказала» доступ, которого не было.
    if "$runuser_bin" -u "$svcuser" -g ssl-cert -- test -r "${live}/privkey.pem" 2>/dev/null \
       && "$runuser_bin" -u "$svcuser" -g ssl-cert -- test -r "${live}/fullchain.pem" 2>/dev/null; then
        [ "$_grant_quiet" = "yes" ] || log_success "$(tf certs_grant_proved "$svcuser")"
        GODNS_GRANT_PROVED="${domain}|${svcuser}"
        return 0
    fi

    log_error "$(tf certs_grant_denied "$svcuser" "${live}/privkey.pem")"
    return 1
}

# Доступ к сертификату, лежащему ВНЕ /etc/letsencrypt (пути из панели
# 3X-UI, acme.sh). Группа ssl-cert выдаётся на сами файлы; ключ никуда не
# копируется. Если каталог по пути непроходим для службы (например /root,
# 0700 root:root), доказательство честно провалится — chmod на чужие
# родительские каталоги не делаем никогда.
# Возврат: 0 — доказано; 1 — отказ; 2 — выдано, но проверить нечем.
_certs_grant_files_access() {
    local svcuser="${1:-}" cert="" key="" runuser_bin=""
    if declare -F config_get >/dev/null 2>&1; then
        cert=$(config_get cert_path "") || cert=""
        key=$(config_get key_path "")   || key=""
    fi
    if [ -z "$cert" ] || [ ! -f "$cert" ] || [ -z "$key" ] || [ ! -f "$key" ]; then
        log_error "$(tf certs_grant_no_lineage "${cert:-?}")"
        return 1
    fi
    if ! id "$svcuser" >/dev/null 2>&1; then
        log_error "$(tf certs_grant_no_user "$svcuser")"
        return 1
    fi

    log_step "$(tf certs_grant_start "$svcuser")"

    if ! getent group ssl-cert >/dev/null 2>&1; then
        if command -v addgroup >/dev/null 2>&1; then
            addgroup --system ssl-cert >/dev/null 2>&1 || true
        fi
        if ! getent group ssl-cert >/dev/null 2>&1; then
            if command -v groupadd >/dev/null 2>&1; then
                groupadd --system ssl-cert >/dev/null 2>&1 || true
            fi
        fi
        if ! getent group ssl-cert >/dev/null 2>&1; then
            log_error "$(t certs_grant_group_fail)"
            return 1
        fi
    fi
    case " $(id -nG "$svcuser" 2>/dev/null || true) " in
        *" ssl-cert "*) : ;;
        *)
            if ! usermod -aG ssl-cert "$svcuser" >/dev/null 2>&1; then
                log_error "$(tf certs_grant_user_fail "$svcuser")"
                return 1
            fi
            ;;
    esac

    chgrp ssl-cert "$cert" "$key" 2>/dev/null || true
    chmod 0640 "$cert" "$key" 2>/dev/null || true

    runuser_bin=$(command -v runuser 2>/dev/null) || runuser_bin=""
    if [ -z "$runuser_bin" ] && [ -x /usr/sbin/runuser ]; then
        runuser_bin=/usr/sbin/runuser
    fi
    if [ -z "$runuser_bin" ]; then
        log_warning "$(t certs_grant_unproven)"
        return 2
    fi
    if "$runuser_bin" -u "$svcuser" -g ssl-cert -- test -r "$key" 2>/dev/null \
       && "$runuser_bin" -u "$svcuser" -g ssl-cert -- test -r "$cert" 2>/dev/null; then
        log_success "$(tf certs_grant_proved "$svcuser")"
        return 0
    fi
    log_error "$(tf certs_grant_denied "$svcuser" "$key")"
    return 1
}

# Реакция на перевыпуск — по МЕСТУ, где лежит сертификат:
#   • линия certbot (/etc/letsencrypt) → deploy-hook: каталог хуков
#     аддитивен, чужие файлы не трогаются;
#   • любой другой путь (acme.sh, панель 3X-UI) → systemd .path-юнит,
#     следящий за mtime файла: ноль записей в чужие конфиги.
# Ставить deploy-hook для не-certbot сертификата бессмысленно: хуки зовёт
# только certbot и только для СВОИХ линий — реакция не сработала бы никогда,
# и после первого же перевыпуска dnsdist продолжал бы отдавать старый
# (в итоге истёкший) сертификат.
certs_setup_renewal_reaction() {
    local domain="${1:-}" cert="${2:-}"
    if [ -z "$cert" ] && declare -F config_get >/dev/null 2>&1; then
        cert=$(config_get cert_path "") || cert=""
    fi
    case "$cert" in
        ''|"${LE_ROOT}/"*)
            certs_install_renewal_hook "$domain"
            return $?
            ;;
    esac
    certs_watch_path_unit "$cert"
}

# ═══════════════════════════════════════════════════════════════════════
# Хук certbot
# ═══════════════════════════════════════════════════════════════════════
#
# Каталог /etc/letsencrypt/renewal-hooks/deploy аддитивен: certbot выполняет
# ВСЕ файлы из него в алфавитном порядке по байтам (отсюда числовой префикс).
# Поэтому свой файл здесь ничего не ломает — govless-restart.sh и хуки
# goTelegram остаются целы. Редактировать чужой хук нельзя: goVLESS
# перезаписывает свой при каждом setup_ssl_auto_renewal.
# Проверка: certbot renew --dry-run --run-deploy-hooks
certs_install_renewal_hook() {
    local domain="${1:-}" lineage="" tmp=""

    if [ -n "$domain" ]; then
        lineage="${LE_ROOT}/live/${domain}"
    fi

    if ! mkdir -p "$LE_HOOK_DIR" 2>/dev/null; then
        log_error "$(tf certs_hook_fail "$LE_HOOK_DIR")"
        return 1
    fi
    chmod 0755 "$LE_HOOK_DIR" 2>/dev/null || true

    tmp="${LE_HOOK_FILE}.tmp.$$"
    if ! {
        printf '%s\n' '#!/bin/sh'
        printf '%s\n' '# goDNS Pro — deploy hook for certbot. Managed by goDNS Pro.'
        printf '%s\n' '# certbot exports RENEWED_LINEAGE and RENEWED_DOMAINS per renewed cert.'
        printf '%s\n' '# This directory is additive: certbot runs every file in it, so this'
        printf '%s\n' "# file never clobbers goVLESS's govless-restart.sh or goTelegram hooks."
        printf '%s\n' 'set -eu'
        printf "GODNS_LINEAGE='%s'\n" "$lineage"
        printf "GODNS_SERVICE='%s'\n" "${GODNS_SERVICE}"
        cat <<'HOOK'
if [ -n "$GODNS_LINEAGE" ] && [ "${RENEWED_LINEAGE:-}" != "$GODNS_LINEAGE" ]; then
    exit 0
fi
systemctl reload "$GODNS_SERVICE" >/dev/null 2>&1 \
    || systemctl restart "$GODNS_SERVICE" >/dev/null 2>&1 \
    || true
exit 0
HOOK
    } > "$tmp" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null || true
        log_error "$(tf certs_hook_fail "$LE_HOOK_FILE")"
        return 1
    fi

    chmod 0755 "$tmp" 2>/dev/null || true
    if ! mv -f "$tmp" "$LE_HOOK_FILE" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null || true
        log_error "$(tf certs_hook_fail "$LE_HOOK_FILE")"
        return 1
    fi
    chmod 0755 "$LE_HOOK_FILE" 2>/dev/null || true

    log_success "$(tf certs_hook_written "$LE_HOOK_FILE")"
    log_dim "$(t certs_hook_note)"
    return 0
}

certs_remove_renewal_hook() {
    if [ -e "$LE_HOOK_FILE" ]; then
        if ! rm -f "$LE_HOOK_FILE" 2>/dev/null; then
            log_error "$(tf certs_hook_fail "$LE_HOOK_FILE")"
            return 1
        fi
        log_success "$(tf certs_hook_removed "$LE_HOOK_FILE")"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# systemd .path — БЕЗОПАСНАЯ альтернатива правке конфига acme.sh
# ═══════════════════════════════════════════════════════════════════════
#
# У acme.sh нет каталога хуков: перезагрузка задаётся единственным
# --reloadcmd внутри конфига домена, и любая его правка чужим проектом —
# риск (см. certs_acme_add_reloadcmd ниже). Слежение за mtime файла
# сертификата даёт тот же эффект и НЕ трогает конфиг goVLESS вообще.
# Когда сертификат уже кладётся в фиксированный путь — выбирать надо
# именно этот вариант.
certs_watch_path_unit() {
    local cert="${1:-}" unit="$CERT_WATCH_UNIT"
    local pfile="${SYSTEMD_UNIT_DIR}/${unit}.path"
    local sfile="${SYSTEMD_UNIT_DIR}/${unit}.service"

    [ -n "$cert" ] || { log_error "$(t certs_watch_no_path)"; return 1; }

    if ! command -v systemctl >/dev/null 2>&1; then
        log_warning "$(t certs_watch_no_systemd)"
        return 1
    fi

    mkdir -p "$SYSTEMD_UNIT_DIR" 2>/dev/null || true

    if ! {
        printf '%s\n' '[Unit]'
        printf '%s\n' 'Description=goDNS Pro: reload the resolver when its TLS certificate changes'
        printf '%s\n' ''
        printf '%s\n' '[Service]'
        printf '%s\n' 'Type=oneshot'
        printf 'ExecStart=/bin/systemctl reload-or-restart %s\n' "${GODNS_SERVICE}"
    } > "$sfile" 2>/dev/null; then
        log_error "$(t certs_watch_fail)"
        return 1
    fi

    if ! {
        printf '%s\n' '[Unit]'
        printf '%s\n' 'Description=goDNS Pro: watch the TLS certificate file'
        printf '%s\n' ''
        printf '%s\n' '[Path]'
        printf 'PathChanged=%s\n'  "$cert"
        printf 'PathModified=%s\n' "$cert"
        printf 'Unit=%s.service\n' "$unit"
        printf '%s\n' ''
        printf '%s\n' '[Install]'
        printf '%s\n' 'WantedBy=multi-user.target'
    } > "$pfile" 2>/dev/null; then
        log_error "$(t certs_watch_fail)"
        return 1
    fi

    chmod 0644 "$sfile" "$pfile" 2>/dev/null || true

    systemctl daemon-reload >/dev/null 2>&1 || true
    if systemctl enable --now "${unit}.path" >/dev/null 2>&1; then
        log_success "$(tf certs_watch_written "$cert")"
        return 0
    fi
    log_warning "$(t certs_watch_enable_fail)"
    return 1
}

# ═══════════════════════════════════════════════════════════════════════
# acme.sh — ЗДЕСЬ НАМЕРЕННО НИЧЕГО НЕТ
# ═══════════════════════════════════════════════════════════════════════
#
# Соблазн очевиден: у acme.sh нет каталога хуков, перезагрузка задаётся
# единственным --reloadcmd в конфиге домена, и хочется его дополнить.
# Реализация «безопасного слияния» тут была и удалена намеренно.
#
# Почему: `acme.sh --install-cert -d D --reloadcmd X` БЕЗ повторной передачи
# --key-file / --fullchain-file молча записывает в конфиг пустые пути.
# installcert() зовёт _savedomainconf безусловно, а _save_conf пишет значение
# даже когда оно пустое. Пути, настроенные goVLESS, исчезают — без ошибки,
# без предупреждения. Поломка всплывает при следующем перевыпуске, до 60 дней
# спустя, и выглядит как «goVLESS сам развалился».
#
# Вычитать старые значения и вернуть их обратно можно. Но это по-прежнему
# ЗАПИСЬ в чужой конфиг, и цена ошибки — тихо сломанный чужой продукт.
# Требование заказчика прямое: чужие сертификаты не трогать.
#
# Тот же результат без единой записи даёт certs_watch_path_unit() выше:
# systemd .path следит за mtime файла сертификата и перезапускает наш сервис.
# acme.sh при этом не знает о нас ничего.
#
# Если эта функция понадобится снова — сначала перечитать CLAUDE.md, раздел
# «Два железных требования заказчика».
