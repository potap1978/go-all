#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# lib/deps.sh — проверка зависимостей goTelegram Pro при УСТАНОВКЕ и ОБНОВЛЕНИИ
#
# Что делает:
#   • проверяет команды, модули ядра, библиотеки и версии по ПРОФИЛЯМ;
#   • печатает человекочитаемую таблицу «статус · компонент · зачем»;
#   • отдаёт машиночитаемое: deps_missing_list (name|pkg|severity) и
#     deps_report_json (для бота и веб-админки);
#   • по ЯВНОМУ вызову доустанавливает apt-пакеты (deps_install_missing).
#
# Контракт модуля:
#   • ни одна функция не делает exit — только return (модуль сорсится в меню);
#   • безопасен под `set -uo pipefail` (везде ${VAR:-}, нет `... | grep -q`
#     в условиях — SIGPIPE + pipefail дают ложные «нет»);
#   • при source ничего не выполняет и ничего не меняет в системе;
#   • идемпотентен. Единственный побочный эффект проверки — тихий `modprobe`
#     отсутствующего модуля ядра (штатный способ узнать, есть ли он вообще).
#   • таблица и логи идут в stderr (как все log_* в проекте), stdout остаётся
#     чистым для JSON/списков.
#
# Профили: core proxy fix23 fix4 warp botweb tls qr   (а также `all`)
#
# Примеры:
#   source lib/deps.sh
#   deps_check core fix23 warp || echo "не хватает критичного"
#   deps_missing_list                      # curl|curl|crit
#   deps_install_missing --yes core botweb
#   deps_report_json | jq .
#   bash lib/deps.sh core tls              # автономный прогон
# ══════════════════════════════════════════════════════════════════════════════

# ── Цвета: если модуль сорснут без common.sh — заводим свои (формат echo -e) ──
: "${RED:=\033[0;31m}"
: "${GREEN:=\033[0;32m}"
: "${YELLOW:=\033[1;33m}"
: "${CYAN:=\033[0;36m}"
: "${WHITE:=\033[1;37m}"
: "${BOLD:=\033[1m}"
: "${DIM:=\033[2m}"
: "${NC:=\033[0m}"

# ── Логгеры: используем из common.sh, если он уже загружен ────────────────────
if ! type log_info    >/dev/null 2>&1; then log_info()    { echo -e "  ${CYAN}ℹ${NC} $*" >&2; }; fi
if ! type log_success >/dev/null 2>&1; then log_success() { echo -e "  ${GREEN}✓${NC} $*" >&2; }; fi
if ! type log_warning >/dev/null 2>&1; then log_warning() { echo -e "  ${YELLOW}⚠${NC} $*" >&2; }; fi
if ! type log_error   >/dev/null 2>&1; then log_error()   { echo -e "  ${RED}✗${NC} $*" >&2; }; fi
if ! type log_step    >/dev/null 2>&1; then log_step()    { echo -e "\n${BOLD}${WHITE}  $*${NC}" >&2; }; fi
if ! type log_dim     >/dev/null 2>&1; then log_dim()     { echo -e "  ${DIM}$*${NC}" >&2; }; fi

# ── Состояние последней проверки ──────────────────────────────────────────────
DEPS_PROFILES_ALL="core proxy fix23 fix4 warp botweb tls qr"
DEPS_MISSING=()                 # строки "name|pkg|severity"
DEPS_LAST_PROFILES="core"
DEPS_CHECKED=0                  # 1 = deps_check хоть раз отработал
DEPS_QUIET="${DEPS_QUIET:-0}"   # 1 = не печатать таблицу (внутренний режим)
DEPS_N_CRIT=0
DEPS_N_WARN=0
DEPS_N_OPT=0

# ══════════════════════════════════════════════════════════════════════════════
#  ТАБЛИЦА ЗАВИСИМОСТЕЙ
#  формат строки:  профиль|имя|проверка|apt-пакет|важность|зачем
#    проверка : cmd:<команда> | mod:<модуль ядра> | lib:<libname> |
#               file:<путь>   | func:<функция bash>
#    пакет    : имя пакета в apt, либо "-" если поставить нельзя
#               (модуль ядра, версия, свойство системы)
#    важность : crit — без него не работает | warn — важно, но есть обход |
#               opt  — приятно иметь
#  Один и тот же компонент может встречаться в нескольких профилях — при
#  проверке дубликаты схлопываются по имени (побеждает первое вхождение).
# ══════════════════════════════════════════════════════════════════════════════
DEPS_TABLE=(
  # ── core: нужно всегда ──────────────────────────────────────────────────────
  "core|bash|func:_deps_have_bash4|bash|crit|движок скриптов (массивы, подстановки)"
  "core|curl|cmd:curl|curl|crit|скачивание telemt, HTTPS-проверки"
  "core|jq|cmd:jq|jq|crit|разбор config.json и каталога шаблонов"
  "core|tar|cmd:tar|tar|crit|распаковка релизов telemt, бэкапы"
  "core|systemctl|cmd:systemctl|systemd|crit|запуск и автостарт сервисов"
  "core|ca-certificates|func:_deps_have_ca|ca-certificates|crit|корневые сертификаты для TLS"
  "core|ss|cmd:ss|iproute2|crit|кто занял порт (пакет iproute2)"

  # ── proxy: ядро telemt ──────────────────────────────────────────────────────
  "proxy|glibc|func:_deps_have_glibc|-|crit|telemt собран под glibc (не musl)"
  "proxy|ca-certificates|func:_deps_have_ca|ca-certificates|crit|цепочка TLS для fake-TLS/домена"

  # ── fix23: SYN-фикс V2/V3 ───────────────────────────────────────────────────
  "fix23|iptables|cmd:iptables|iptables|crit|правила SYN-лимита V2/V3"
  "fix23|xt_hashlimit|mod:xt_hashlimit|-|crit|лимит SYN на IP (-m hashlimit)"
  "fix23|xt_u32|mod:xt_u32|-|warn|V3 на iptables: match по TCP-опциям"
  "fix23|nft|cmd:nft|nftables|warn|V3 на nftables (альтернатива u32)"
  "fix23|nf_tables|mod:nf_tables|-|warn|модуль ядра для nft-варианта V3"

  # ── fix4: V4 zapret2 (NFQUEUE) ──────────────────────────────────────────────
  "fix4|libnetfilter_queue|lib:libnetfilter_queue|libnetfilter-queue1|opt|zapret2: нужна только для несобранных сборок — релизный nfqws2 статический"
  "fix4|nfnetlink_queue|mod:nfnetlink_queue|-|crit|поддержка NFQUEUE в ядре"
  "fix4|iptables|cmd:iptables|iptables|warn|правило -j NFQUEUE для zapret2"
  "fix4|curl|cmd:curl|curl|crit|скачивание сборки zapret2"
  "fix4|tar|cmd:tar|tar|crit|распаковка сборки zapret2"

  # ── warp: Cloudflare WARP egress ────────────────────────────────────────────
  "warp|wg|cmd:wg|wireguard-tools|crit|ключи и статус туннеля WARP"
  "warp|wg-quick|cmd:wg-quick|wireguard-tools|crit|поднятие WARP-интерфейса"
  "warp|wireguard|mod:wireguard|-|warn|модуль ядра WireGuard (иначе userspace)"

  # ── botweb: Telegram-бот и веб-админка ──────────────────────────────────────
  "botweb|python3|cmd:python3|python3|crit|Telegram-бот и веб-админка"
  "botweb|python3>=3.10|func:_deps_have_py310|-|warn|aiogram v3 и современный asyncio"
  "botweb|python3-venv|func:_deps_have_venv|python3-venv|crit|изолированное окружение бота"
  "botweb|python3-pip|func:_deps_have_pip|python3-pip|crit|установка зависимостей бота"

  # ── tls: секреты и сертификаты ──────────────────────────────────────────────
  "tls|openssl|cmd:openssl|openssl|crit|секреты, проверка сертификата домена"
  "tls|openssl>=3.5|func:deps_openssl_has_pq|-|warn|постквантовый X25519MLKEM768 (iOS)"

  # ── qr: приятные мелочи ─────────────────────────────────────────────────────
  "qr|qrencode|cmd:qrencode|qrencode|opt|QR-коды для ссылок на прокси"
)

# ══════════════════════════════════════════════════════════════════════════════
#  Низкоуровневые проверки
# ══════════════════════════════════════════════════════════════════════════════

# Сравнение версий: _deps_ver_ge A B → 0, если A >= B (semver-ish, через sort -V)
_deps_ver_ge() {
    local a="${1:-}" b="${2:-}"
    [ -n "$a" ] || return 1
    [ "$a" = "$b" ] && return 0
    [ "$(printf '%s\n%s\n' "$a" "$b" | sort -V 2>/dev/null | head -n1)" = "$b" ]
}

_deps_have_bash4() { [ "${BASH_VERSINFO[0]:-0}" -ge 4 ]; }

# Корневые сертификаты: Debian, RHEL, либо непустой /etc/ssl/certs
_deps_have_ca() {
    [ -s /etc/ssl/certs/ca-certificates.crt ] && return 0
    [ -s /etc/pki/tls/certs/ca-bundle.crt ] && return 0
    if [ -d /etc/ssl/certs ]; then
        local n; n="$(ls -1 /etc/ssl/certs 2>/dev/null | wc -l | tr -d ' ')"
        [ "${n:-0}" -gt 5 ] 2>/dev/null && return 0
    fi
    return 1
}

# glibc (а не musl): три независимых сигнала, чтобы не дать ложный «нет»
_deps_have_glibc() {
    getconf GNU_LIBC_VERSION >/dev/null 2>&1 && return 0
    local out; out="$(ldd --version 2>&1 | head -n1)"
    case "$out" in *[Gg][Ll][Ii][Bb][Cc]*|*"GNU libc"*|*"GNU C Library"*) return 0 ;; esac
    local f
    for f in /lib/x86_64-linux-gnu/libc.so.6 /lib/aarch64-linux-gnu/libc.so.6 \
             /lib64/libc.so.6 /lib/libc.so.6; do
        [ -e "$f" ] && return 0
    done
    return 1
}

# Разделяемая библиотека по имени (libnetfilter_queue → libnetfilter_queue.so.1)
_deps_have_lib() {
    local so="${1:-}"; [ -n "$so" ] || return 1
    # ldconfig -p целиком в переменную: `| grep -q` под pipefail даёт 141
    local out; out="$(ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null || true)"
    case "$out" in *"$so"*) return 0 ;; esac
    local d
    for d in /lib /usr/lib /lib64 /usr/lib64 /usr/lib/x86_64-linux-gnu \
             /usr/lib/aarch64-linux-gnu /usr/local/lib; do
        [ -d "$d" ] || continue
        ls "$d"/"$so".so* >/dev/null 2>&1 && return 0
    done
    return 1
}

_deps_have_py310() { python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; }
# На Debian/Ubuntu venv без ensurepip не создаётся — проверяем именно ensurepip
_deps_have_venv()  { python3 -c 'import venv, ensurepip' >/dev/null 2>&1; }
_deps_have_pip()   { python3 -m pip --version >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1; }

# ── Модули ядра ───────────────────────────────────────────────────────────────
# deps_kernel_module <mod> → 0, если модуль загружен, вкомпилен в ядро или
# доступен для загрузки. Тихо пытается modprobe (без root просто не сработает).
deps_kernel_module() {
    local m="${1:-}"
    [ -n "$m" ] || return 1
    m="${m//-/_}"

    # 1) уже загружен (список целиком в переменную — без `| grep -q`)
    local loaded; loaded="$(lsmod 2>/dev/null | awk 'NR>1{printf " %s", $1}')"
    case "${loaded} " in *" $m "*) return 0 ;; esac

    # 2) вкомпилен в ядро (builtin)
    [ -d "/sys/module/$m" ] && return 0
    local kr; kr="$(uname -r 2>/dev/null || echo "")"
    if [ -n "$kr" ] && [ -f "/lib/modules/$kr/modules.builtin" ]; then
        grep -q "/${m}\.ko" "/lib/modules/$kr/modules.builtin" 2>/dev/null && return 0
    fi

    # 3) есть в дереве модулей — считаем доступным, попутно тихо грузим
    if modinfo "$m" >/dev/null 2>&1; then
        modprobe "$m" >/dev/null 2>&1 || true
        return 0
    fi

    # 4) попытка вслепую (бывает в контейнерах без /lib/modules)
    modprobe "$m" >/dev/null 2>&1 && return 0
    return 1
}

# Подсказка про пакет модулей ядра (uname может быть недоступен — тогда даём команду как есть)
_deps_modext_hint() {
    local kr; kr="$(uname -r 2>/dev/null || echo "")"
    [ -n "$kr" ] || kr='$(uname -r)'
    log_dim "Обычно лечится: apt-get install -y linux-modules-extra-${kr} (и перезагрузка)"
    return 0
}

# deps_ensure_modules <mod...> — загрузить все; 1, если что-то недоступно
deps_ensure_modules() {
    local m rc=0 bad=""
    for m in "$@"; do
        [ -n "${m:-}" ] || continue
        if deps_kernel_module "$m"; then continue; fi
        bad="${bad} ${m}"; rc=1
    done
    if [ -n "$bad" ]; then
        log_warning "Модули ядра недоступны:${bad}"
        _deps_modext_hint
    fi
    return $rc
}

# ── OpenSSL ───────────────────────────────────────────────────────────────────
# deps_openssl_version → «3.0.13» (буквенный суффикс 1.1.1f срезается), либо пусто
deps_openssl_version() {
    local v
    v="$(openssl version 2>/dev/null | awk '{print $2}')"
    v="${v%%[!0-9.]*}"
    v="${v%.}"
    echo "${v:-}"
    return 0
}

# deps_openssl_has_pq → 0, если OpenSSL >= 3.5 (есть X25519MLKEM768)
deps_openssl_has_pq() {
    local v; v="$(deps_openssl_version)"
    [ -n "$v" ] || return 1
    _deps_ver_ge "$v" "3.5"
}

# Предупреждение про постквантовый обмен ключами (домен/SelfSteal на iOS)
deps_openssl_pq_warn() {
    local v; v="$(deps_openssl_version)"
    log_warning "OpenSSL ${v:-неизвестно} < 3.5 — нет постквантового X25519MLKEM768."
    log_dim "Домен/SelfSteal может не работать на iOS: свежие Apple-клиенты просят PQ-обмен."
    log_dim "Решения: 1) Caddy (свой TLS-стек); 2) OpenSSL 3.5+; 3) публичный домен с поддержкой PQ."
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
#  Внутренняя кухня проверки
# ══════════════════════════════════════════════════════════════════════════════

# Выполнить одну проверку из колонки «проверка»
_deps_check_one() {
    local spec="${1:-}" kind arg
    kind="${spec%%:*}"; arg="${spec#*:}"
    [ -n "${arg:-}" ] || return 1
    case "$kind" in
        cmd)  command -v "$arg" >/dev/null 2>&1 ;;
        mod)  deps_kernel_module "$arg" ;;
        lib)  _deps_have_lib "$arg" ;;
        file) [ -e "$arg" ] ;;
        func) "$arg" >/dev/null 2>&1 ;;
        *)    return 1 ;;
    esac
}

# Строка таблицы. Ширина колонок считается вручную: printf %-Ns бьёт по БАЙТАМ,
# и кириллица в статусе развалила бы выравнивание.
_deps_row() {
    local st="${1:-}" name="${2:-}" why="${3:-}" lbl="" pad=0 pads="" namecell=""
    case "$st" in
        ok)   lbl="${GREEN}✓ есть${NC}";         pad=12 ;;   # «✓ есть» = 6 симв.
        crit) lbl="${RED}✗ нет (критично)${NC}"; pad=2  ;;   # 16 симв.
        warn) lbl="${YELLOW}✗ нет (важно)${NC}"; pad=5  ;;   # 13 симв.
        *)    lbl="${DIM}• опционально${NC}";    pad=5  ;;   # 13 симв.
    esac
    printf -v pads '%*s' "$pad" ''
    printf -v namecell '%-18s' "$name"
    echo -e "  ${lbl}${pads}${namecell} ${DIM}${why}${NC}" >&2
}

# Уникальные apt-пакеты недостающего (без "-")
_deps_pkg_list() {
    local line name pkg sev out=" "
    if [ ${#DEPS_MISSING[@]} -gt 0 ]; then
        for line in "${DEPS_MISSING[@]}"; do
            IFS='|' read -r name pkg sev <<< "$line"
            [ -n "${pkg:-}" ] && [ "$pkg" != "-" ] || continue
            case "$out" in *" $pkg "*) continue ;; esac
            out="${out}${pkg} "
        done
    fi
    out="${out# }"; out="${out% }"
    echo "$out"
}

# Тип проверки компонента по имени (нужно, чтобы отделить модули ядра)
_deps_kind_of() {
    local want="${1:-}" row prof name chk rest
    for row in "${DEPS_TABLE[@]}"; do
        IFS='|' read -r prof name chk rest <<< "$row"
        if [ "$name" = "$want" ]; then echo "${chk%%:*}"; return 0; fi
    done
    echo ""
    return 0
}

# Недостающие модули ядра (их apt не поставит)
_deps_mod_list() {
    local line name pkg sev out=" "
    if [ ${#DEPS_MISSING[@]} -gt 0 ]; then
        for line in "${DEPS_MISSING[@]}"; do
            IFS='|' read -r name pkg sev <<< "$line"
            [ "$(_deps_kind_of "$name")" = "mod" ] || continue
            case "$out" in *" $name "*) continue ;; esac
            out="${out}${name} "
        done
    fi
    out="${out# }"; out="${out% }"
    echo "$out"
}

# Тихая проверка (без таблицы) — для JSON, списков и доустановки
_deps_quiet_check() {
    local prev="${DEPS_QUIET:-0}"
    DEPS_QUIET=1
    if [ $# -gt 0 ]; then
        deps_check "$@" >/dev/null 2>&1 || true
    else
        # shellcheck disable=SC2086
        deps_check ${DEPS_LAST_PROFILES:-core} >/dev/null 2>&1 || true
    fi
    DEPS_QUIET="$prev"
    return 0
}

_deps_ensure_checked() {
    [ "${DEPS_CHECKED:-0}" = "1" ] && return 0
    _deps_quiet_check
}

# Экранирование для JSON (имена ASCII, но пусть будет честно)
_deps_jesc() {
    local s="${1:-}"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '%s' "$s"
}

# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНОЕ API
# ══════════════════════════════════════════════════════════════════════════════

# deps_profiles — список доступных профилей (для UI)
deps_profiles() { echo "$DEPS_PROFILES_ALL"; }

# deps_check [профиль...] — проверка + таблица-отчёт.
#   0 — всё критичное на месте;  1 — есть неудовлетворённое критичное.
# Без аргументов проверяет профиль core.
deps_check() {
    local profiles=() p sel=" " selt="" quiet="${DEPS_QUIET:-0}"
    local row prof name chk pkg sev why seen=" " st line="" hdr=""

    if [ $# -gt 0 ]; then profiles=("$@"); else profiles=("core"); fi

    for p in "${profiles[@]}"; do
        [ -n "${p:-}" ] || continue
        if [ "$p" = "all" ]; then sel=" ${DEPS_PROFILES_ALL} "; break; fi
        case " ${DEPS_PROFILES_ALL} " in
            *" $p "*)
                case "$sel" in *" $p "*) : ;; *) sel="${sel}${p} " ;; esac ;;
            *)
                [ "$quiet" = "1" ] || log_warning "Неизвестный профиль: ${p} (доступны: ${DEPS_PROFILES_ALL})" ;;
        esac
    done
    [ "$sel" = " " ] && sel=" core "
    selt="${sel# }"; selt="${selt% }"

    # ── сброс состояния ──
    DEPS_MISSING=(); DEPS_N_CRIT=0; DEPS_N_WARN=0; DEPS_N_OPT=0
    DEPS_LAST_PROFILES="$selt"; DEPS_CHECKED=1

    if [ "$quiet" != "1" ]; then
        printf -v line '%*s' 66 ''; line="${line// /─}"
        # ширина колонок: статус = 18 симв., компонент = 18 + пробел
        printf -v hdr '%s%*s%s%*s%s' "Статус" 12 '' "Компонент" 10 '' "Зачем"
        log_step "Проверка зависимостей: ${selt// /, }"
        echo -e "  ${DIM}${hdr}${NC}" >&2
        echo -e "  ${DIM}${line}${NC}" >&2
    fi

    for row in "${DEPS_TABLE[@]}"; do
        IFS='|' read -r prof name chk pkg sev why <<< "$row"
        [ -n "${name:-}" ] || continue
        case "$sel" in *" $prof "*) : ;; *) continue ;; esac
        case "$seen" in *" $name "*) continue ;; esac      # дубликат из другого профиля
        seen="${seen}${name} "

        if _deps_check_one "$chk"; then
            st="ok"
        else
            st="$sev"
            DEPS_MISSING+=("${name}|${pkg}|${sev}")
            case "$sev" in
                crit) DEPS_N_CRIT=$((DEPS_N_CRIT + 1)) ;;
                warn) DEPS_N_WARN=$((DEPS_N_WARN + 1)) ;;
                *)    DEPS_N_OPT=$((DEPS_N_OPT + 1)) ;;
            esac
        fi
        [ "$quiet" = "1" ] || _deps_row "$st" "$name" "$why"
    done

    # ── отдельный разбор OpenSSL: версия + постквантовый обмен ──
    if [ "$quiet" != "1" ]; then
        case "$sel" in
            *" tls "*)
                local ov; ov="$(deps_openssl_version)"
                if [ -n "$ov" ]; then
                    echo -e "  ${DIM}${line}${NC}" >&2
                    log_dim "Версия OpenSSL: ${ov}"
                    deps_openssl_has_pq || deps_openssl_pq_warn
                fi
                ;;
        esac
    fi

    # ── итог ──
    if [ "$quiet" != "1" ]; then
        echo -e "  ${DIM}${line}${NC}" >&2
        if [ "$DEPS_N_CRIT" -gt 0 ]; then
            log_error "Не хватает критичного: ${DEPS_N_CRIT} · важного: ${DEPS_N_WARN} · опционального: ${DEPS_N_OPT}"
        elif [ "$DEPS_N_WARN" -gt 0 ] || [ "$DEPS_N_OPT" -gt 0 ]; then
            log_success "Критичное на месте · важного не хватает: ${DEPS_N_WARN} · опционального: ${DEPS_N_OPT}"
        else
            log_success "Все зависимости на месте"
        fi
        local plist; plist="$(_deps_pkg_list)"
        [ -n "$plist" ] && log_dim "Доустановить: deps_install_missing   (apt: ${plist})"
    fi

    [ "$DEPS_N_CRIT" -gt 0 ] && return 1
    return 0
}

# deps_missing_list — машиночитаемо: по строке «name|pkg|severity».
# Если проверка ещё не запускалась — тихо прогоняет её (профили: последние/core).
deps_missing_list() {
    _deps_ensure_checked
    [ ${#DEPS_MISSING[@]} -gt 0 ] || return 0
    printf '%s\n' "${DEPS_MISSING[@]}"
    return 0
}

# deps_install_missing [--yes] [профиль...] — доустановка недостающих apt-пакетов.
#   0 — ставить нечего / поставили / не Debian (не блокируем);
#   1 — установка не удалась либо критичное осталось.
deps_install_missing() {
    local yes=0 profiles=() a pkgs="" mods="" err=""

    while [ $# -gt 0 ]; do
        a="${1:-}"
        case "$a" in
            --yes|-y) yes=1 ;;
            --)       : ;;
            -*)       log_warning "Неизвестный ключ: ${a}" ;;
            *)        [ -n "$a" ] && profiles+=("$a") ;;
        esac
        shift
    done

    # Профили заданы явно — перепроверяем по ним; иначе используем прошлую проверку
    if [ ${#profiles[@]} -gt 0 ]; then
        _deps_quiet_check "${profiles[@]}"
    else
        _deps_ensure_checked
    fi

    pkgs="$(_deps_pkg_list)"
    mods="$(_deps_mod_list)"

    if [ -z "$pkgs" ] && [ -z "$mods" ]; then
        log_success "Зависимости уже на месте — ставить нечего"
        return 0
    fi

    if [ -n "$mods" ]; then
        log_warning "Модули ядра недоступны: ${mods} (apt их не поставит)"
        _deps_modext_hint
    fi
    [ -n "$pkgs" ] || return 0

    # Не-Debian: не блокируем установку, просто объясняем, что поставить руками
    if ! command -v apt-get >/dev/null 2>&1; then
        log_warning "Это не Debian/Ubuntu (apt-get не найден) — автоустановка недоступна."
        log_dim "Поставьте вручную: ${pkgs}"
        return 0
    fi

    if [ "$(id -u 2>/dev/null || echo 1)" != "0" ]; then
        log_warning "Для установки нужны права root."
        log_dim "Вручную: sudo apt-get install -y ${pkgs}"
        return 1
    fi

    if [ "$yes" != "1" ] && type confirm >/dev/null 2>&1; then
        if ! confirm "Доустановить пакеты: ${pkgs}?"; then
            log_dim "Установка пропущена пользователем"
            return 0
        fi
    fi

    log_step "Доустановка пакетов: ${pkgs}"
    # Ждём чужой apt/dpkg-лок, если common.sh загружен (unattended-upgrades)
    if type apt_lock_wait >/dev/null 2>&1; then apt_lock_wait 120 >/dev/null 2>&1 || true; fi

    err="$(mktemp 2>/dev/null || echo "/tmp/gt_deps_apt.$$")"
    # shellcheck disable=SC2086
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=120 $pkgs 2>"$err"; then
        log_warning "Не вышло с ходу — обновляю списки пакетов и пробую ещё раз"
        DEBIAN_FRONTEND=noninteractive apt-get update -qq -o DPkg::Lock::Timeout=120 >/dev/null 2>&1 || true
        # shellcheck disable=SC2086
        if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=120 $pkgs 2>"$err"; then
            log_error "Не удалось установить: ${pkgs}"
            [ -s "$err" ] && tail -n 5 "$err" | sed 's/^/    /' >&2
            rm -f "$err" 2>/dev/null
            return 1
        fi
    fi
    rm -f "$err" 2>/dev/null
    log_success "Пакеты установлены"

    # Контрольная перепроверка тех же профилей
    # shellcheck disable=SC2086
    _deps_quiet_check ${DEPS_LAST_PROFILES:-core}
    if [ "${DEPS_N_CRIT:-0}" -gt 0 ]; then
        log_error "После установки критичное всё ещё отсутствует:"
        deps_missing_list | sed 's/^/    /' >&2
        return 1
    fi
    log_success "Критичные зависимости на месте"
    return 0
}

# deps_report_json [профиль...] — валидный JSON для бота/вебки (в stdout):
# {"ok":true,"missing":[{"name","pkg","severity"}],"openssl":"3.0.13","openssl_pq":false}
deps_report_json() {
    if [ $# -gt 0 ]; then _deps_quiet_check "$@"; else _deps_ensure_checked; fi

    local ok="true" pq="false" ov items="" line name pkg sev
    [ "${DEPS_N_CRIT:-0}" -gt 0 ] && ok="false"
    ov="$(deps_openssl_version)"
    deps_openssl_has_pq && pq="true"

    if [ ${#DEPS_MISSING[@]} -gt 0 ]; then
        for line in "${DEPS_MISSING[@]}"; do
            IFS='|' read -r name pkg sev <<< "$line"
            [ -n "$items" ] && items="${items},"
            items="${items}{\"name\":\"$(_deps_jesc "$name")\",\"pkg\":\"$(_deps_jesc "$pkg")\",\"severity\":\"$(_deps_jesc "$sev")\"}"
        done
    fi

    printf '{"ok":%s,"missing":[%s],"openssl":"%s","openssl_pq":%s}\n' \
        "$ok" "$items" "$(_deps_jesc "$ov")" "$pq"
    return 0
}

# ── Автономный запуск: bash lib/deps.sh [профили...] (при source не срабатывает) ──
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
    deps_check "$@"
fi
