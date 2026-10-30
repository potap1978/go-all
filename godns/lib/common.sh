#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — Core utilities.
# Colours, logging, safety wrappers, OS gate, apt, ports, JSON config.
#
# Contract for every other lib/*.sh:
#   • All log_* write to STDERR. Never to stdout — they would poison $( ).
#   • JSON is read with python3, NOT jq: jq is absent on Ubuntu 22.04
#     standard and on every minimal cloud image, and we must read
#     goVLESS/goTelegram configs BEFORE we are allowed to apt-install.
#   • Every menu action goes through run_action.

# ── Version & paths ─────────────────────────────────────────────────────
GODNS_VERSION="1.0.1"
GODNS_SCHEMA="1"
GODNS_DIR="${GODNS_DIR:-/opt/godns}"
GODNS_ETC="${GODNS_ETC:-/etc/godns}"
GODNS_CONFIG="${GODNS_CONFIG:-${GODNS_DIR}/config.json}"
GODNS_CERT_DIR="${GODNS_CERT_DIR:-${GODNS_ETC}/cert}"
GODNS_SERVICE="dnsdist"

# Логи. Каталог СВОЙ, и это принципиально: бюджет логов (lib/logs.sh) считает
# его целиком и удаляет из него самое старое, поэтому чужих файлов там быть
# не должно. Раньше GODNS_LOG жил прямо в /var/log/godns.log и не попадал ни
# под какую ротацию вообще — переезд делает godns_logs_ensure_dir.
GODNS_LOG_DIR="${GODNS_LOG_DIR:-/var/log/godns}"
GODNS_LOG="${GODNS_LOG:-${GODNS_LOG_DIR}/godns.log}"

# Соседи, с которыми интегрируемся (ТОЛЬКО ЧТЕНИЕ).
#
# Через ${VAR:-...}, а не голым присваиванием: иначе source common.sh затирал
# бы значения, выставленные снаружи, и прогнать обнаружение сертификатов по
# фикстурам стало бы невозможно — тест молча читал бы настоящий
# /etc/letsencrypt и сообщал «ничего не найдено».
GOVLESS_CONFIG_PATH="${GOVLESS_CONFIG_PATH:-/opt/govless/config.json}"
GOTELEGRAM_CONFIG_PATH="${GOTELEGRAM_CONFIG_PATH:-/opt/gotelegram/config.json}"
TELEMT_CONFIG_PATH="${TELEMT_CONFIG_PATH:-/etc/telemt/config.toml}"
XUI_DB_PATH="${XUI_DB_PATH:-/etc/x-ui/x-ui.db}"
LE_ROOT="${LE_ROOT:-/etc/letsencrypt}"

umask 077

# NOTE: the bash 4.0+ guard lives in godns.sh (entry point) and in
# lib/i18n.sh (where the associative array actually is), NOT here — so that
# the test suite can source common.sh on a bash 3.2 host to exercise the
# pure logic (os_gate, ports, JSON) without a container.

# ── Colours ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# ── Logging — always stderr ─────────────────────────────────────────────
log_info()    { echo -e "  ${CYAN}ℹ${NC}  $*" >&2; }
log_success() { echo -e "  ${GREEN}✓${NC}  $*" >&2; }
log_warning() { echo -e "  ${YELLOW}!${NC}  $*" >&2; }
log_error()   { echo -e "  ${RED}✗${NC}  $*" >&2; }
log_dim()     { echo -e "  ${DIM}$*${NC}" >&2; }
log_step()    { echo -e "\n  ${BOLD}${WHITE}▶ $*${NC}" >&2; }

# ── Menu safety ─────────────────────────────────────────────────────────
# The script runs under `set -euo pipefail`, but a non-zero exit is a routine
# outcome here: no certificate found, DNS not propagated, dnsdist not
# installed. A bare call in a `case` branch would kill the WHOLE script and
# the operator's terminal just closes without a single message.
#
# `f || true` in a conditional context also disables errexit for the entire
# call tree inside f — so it defuses mines several levels deep, not just the
# top-level call.
run_action() {
    local _fn="$1"; shift
    "$_fn" "$@" || true
}

# ── Header / separators ─────────────────────────────────────────────────
print_header() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$1${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
}

print_rule() { echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"; }

# ── Confirm ─────────────────────────────────────────────────────────────
# Last command must not be `[ … ] && …` — on a false condition the function
# would return 1 and, in a non-guarded context, take the script with it.
# Ответ приводим к нижнему регистру и снимаем пробелы, а не перечисляем
# варианты написания: список «y|Y|yes|YES» пропускал «Yes» и «YES » — то
# есть ровно то, что человек и печатает. Принимаем оба языка: раскладку
# переключают не всегда.
# ── Ввод: Enter и Ctrl-D — разные ответы ────────────────────────────────
# Возвращает 0 — ответ лежит в GODNS_REPLY (пустой ответ = «просто Enter»,
# то есть согласие с умолчанием). Возвращает 1 — ввод кончился (Ctrl-D или
# закрытый stdin), и это ОТМЕНА.
#
# ГРАБЛИ, снятые с живого стенда: везде было написано `read -r x || x=""`,
# то есть Ctrl-D приравнивался к Enter. На экране «Изменить порты» это
# значило, что жест отмены проходил все вопросы на умолчаниях и уходил в
# полное применение с перезапуском службы. Соседний пункт «Сменить
# политику» на том же Ctrl-D честно отменял — две разные семантики в
# соседних строках меню.
#
# Различить их можно: Enter — это read с кодом 0 и пустой строкой.
# Но код 1 бывает и у настоящего ответа: строка без завершающего перевода
# (`printf yes | godns.sh`) приезжает в переменную, а read всё равно
# возвращает 1. Такой ввод — ответ, а не отмена.
godns_read_line() {
    GODNS_REPLY=""
    if read -r GODNS_REPLY; then return 0; fi
    [ -n "$GODNS_REPLY" ] && return 0
    return 1
}

confirm() {
    local prompt="$1" default="${2:-n}" ans="" tries=0
    local hint="[y/N]"
    [ "$default" = "y" ] && hint="[Y/n]"

    while : ; do
        echo -ne "  ${prompt} ${DIM}${hint}${NC} " >&2
        if ! godns_read_line; then
            # Ctrl-D. Раньше первый конец ввода считался за «просто Enter»
            # и брал умолчание — то есть жест отмены отвечал «да» на
            # вопросах с умолчанием «да». Отмена значит отмена.
            echo "" >&2
            return 1
        fi
        ans="$GODNS_REPLY"
        # Невидимые символы. Неразрывный пробел (U+00A0) прилетает при
        # вставке из документов и веба, [[:space:]] его НЕ ловит, и ответ
        # молча отвергается. Убираем его и обычные пробелы по краям.
        ans=$(printf '%s' "$ans" | tr -d '\302\240')
        ans="${ans#"${ans%%[![:space:]]*}"}"
        ans="${ans%"${ans##*[![:space:]]}"}"
        [ -z "$ans" ] && ans="$default"

        # Латиницу приводим к нижнему регистру через tr — он побайтовый и с
        # ASCII справляется. Кириллицу tr НЕ осилит: буква в UTF-8 занимает
        # два байта, и «ДА» превращается в мусор. `${ans,,}` тоже нельзя —
        # это синтаксис bash 4, а файл обязан разбираться на bash 3.2, иначе
        # тесты чистой логики не запустятся на macOS. Кириллица перечислена.
        ans=$(printf '%s' "$ans" | tr 'A-Z' 'a-z')
        case "$ans" in
            y|ye|yes|+|1)                  return 0 ;;
            д|Д|да|ДА|Да|дА|ага|Ага|АГА)   return 0 ;;
            n|no|-|0)                      return 1 ;;
            не|Не|НЕ|нет|Нет|НЕТ|неТ)      return 1 ;;
        esac

        # ГРАБЛИ, найденные оператором. На раскладке ЙЦУКЕН физическая
        # клавиша Y даёт «н», а N даёт «т». Однобуквенное «н» поэтому
        # смертельно двусмысленно: это либо промах раскладкой при ответе
        # «да», либо начало слова «нет». Раньше оно молча означало «нет» —
        # то есть человек жал Y, имея в виду согласие, и получал отказ.
        # Угадывать здесь нельзя: цена ошибки — не тот ответ на вопрос,
        # который меняет систему. Спрашиваем заново и называем причину.
        case "$ans" in
            н|Н|т|Т|ё|Ё)
                log_warning "$(t confirm_layout)"
                tries=$((tries + 1))
                [ "$tries" -ge 3 ] && { log_warning "$(t confirm_giving_up)"; return 1; }
                continue
                ;;
        esac

        tries=$((tries + 1))
        if [ "$tries" -ge 3 ]; then
            log_warning "$(t confirm_giving_up)"
            return 1
        fi
        log_warning "$(t confirm_unclear)"
    done
}

# Слово-подтверждение сверяем ТОЧНО, включая регистр. Пробелы по краям
# снимаем — их добавляет вставка из буфера, а не невнимательность.
#
# Регистр НЕ игнорируем намеренно: это последняя преграда перед удалением,
# и её смысл — заставить прочитать и осознанно набрать. Так же поступает
# GitHub, требуя точное имя репозитория.
typed_confirm() {
    local word="$1" ans=""
    echo -ne "  ${YELLOW}$(printf '%s' "${2:-Type}")${NC} ${BOLD}${word}${NC}: " >&2
    read -r ans || ans=""
    ans="${ans#"${ans%%[![:space:]]*}"}"
    ans="${ans%"${ans##*[![:space:]]}"}"
    if [ "$ans" = "$word" ]; then return 0; fi
    return 1
}

# ── Root check ──────────────────────────────────────────────────────────
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "goDNS Pro must be run as root / запустите от root"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# OS GATE — Ubuntu 22.04 / 24.04 only, checked BEFORE anything is installed
# ═══════════════════════════════════════════════════════════════════════
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"

# Never `source` os-release: it is attacker-controllable inside an image and
# would clobber our own variables under `set -u`.
os_release_get() {
    local _k="$1" _v=""
    [ -r "$OS_RELEASE_FILE" ] || return 0
    # Без `| head -n1`: head закрыл бы пайп, sed умер бы по SIGPIPE, и под
    # pipefail подстановка вернула бы ненулевой код — то есть os_gate падал бы
    # на ровном месте. Берём весь вывод и отрезаем первую строку в bash.
    _v=$(sed -n -E "s/^[[:space:]]*${_k}=(.*)\$/\1/p" "$OS_RELEASE_FILE" 2>/dev/null) || _v=""
    _v="${_v%%$'\n'*}"
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    printf '%s' "$_v"
}

# Exported for the caller / tests.
OS_ID=""; OS_VERSION_ID=""; OS_CODENAME=""; OS_PRETTY=""

# rc 0 = supported (22.04 / 24.04)
# rc 1 = refuse (too old, interim, non-Ubuntu)
# ── Запасной вид t/tf, когда таблицы строк рядом нет ───────────────────
# lib/common.sh намеренно НЕ зависит от lib/i18n.sh: его подключают и в
# одиночку — тесты чистой логики (они идут на macOS с bash 3.2, где
# ассоциативных массивов нет) и ранние стражи. Но os_gate ниже печатает
# человеку, и делает это через tf.
#
# ГРАБЛИ: без этой заглушки `$(tf os_derivative "$id")` превращался в
# «command not found» и пустую строку — отказ переставал НАЗЫВАТЬ систему,
# из-за которой он произошёл. Перевод потерять можно, данные нельзя:
# запасной вид печатает ключ и аргументы.
#
# Определения условные: godns.sh подключает common.sh ПЕРВЫМ, а i18n.sh
# следом, и настоящие t/tf просто перекрывают эти.
if ! declare -F t >/dev/null 2>&1; then
    t()  { printf '%s' "${1:-}"; }
fi
if ! declare -F tf >/dev/null 2>&1; then
    tf() { local _k="${1:-}"; shift 2>/dev/null || true; printf '%s %s' "$_k" "$*"; }
fi

# rc 2 = newer LTS than tested — caller warns and asks
os_gate() {
    local id id_like version_id codename ubuntu_codename base_ver major minor

    id=$(os_release_get ID)
    id_like=$(os_release_get ID_LIKE)
    version_id=$(os_release_get VERSION_ID)
    codename=$(os_release_get VERSION_CODENAME)
    ubuntu_codename=$(os_release_get UBUNTU_CODENAME)
    OS_PRETTY=$(os_release_get PRETTY_NAME)

    if [ -z "$id" ]; then
        log_error "$(tf os_read_failed "$OS_RELEASE_FILE")"
        return 1
    fi

    if [ "$id" != "ubuntu" ]; then
        case " $id_like " in
            *" ubuntu "*)
                base_ver=""
                if [ -r /etc/upstream-release/lsb-release ]; then
                    base_ver=$(sed -n -E 's/^DISTRIB_RELEASE=(.*)$/\1/p' \
                        /etc/upstream-release/lsb-release | head -n1)
                fi
                log_error "$(tf os_derivative "${id}")"
                log_dim   "$(tf os_derivative_base "${ubuntu_codename:-?}" "${base_ver:-?}")"
                ;;
            *" debian "*)
                log_error "$(tf os_debian_like "${id}")" ;;
            *)
                log_error "$(tf os_unsupported "${id}")" ;;
        esac
        return 1
    fi

    OS_ID="$id"; OS_VERSION_ID="$version_id"
    OS_CODENAME="${codename:-$ubuntu_codename}"

    if [ -z "$version_id" ]; then
        log_error "$(tf os_no_version "$OS_RELEASE_FILE")"
        return 1
    fi

    major="${version_id%%.*}"
    minor="${version_id#*.}"; minor="${minor%%.*}"
    case "${major}${minor}" in
        ''|*[!0-9]*) log_error "$(tf os_bad_version "${version_id}")"; return 1 ;;
    esac
    major=$((10#$major)); minor=$((10#$minor))

    if [ "$major" -lt 22 ]; then
        log_error "$(tf os_too_old "${version_id}")"
        return 1
    fi

    # Явно поддерживаем 22.04 / 24.04 / 26.04.
    case "$version_id" in
        22.04|24.04|26.04) return 0 ;;
    esac

    # Промежуточный выпуск (x.10) — поддержка всего 9 месяцев. Не отказ,
    # но оператор должен знать, во что ввязывается.
    if [ "$minor" -ne 4 ]; then
        log_warning "$(tf os_interim "${version_id}")"
        return 2
    fi

    # Более новая LTS. Жёсткий список версий превратил бы установщик в бомбу
    # замедленного действия в день выхода следующей LTS, поэтому пропускаем
    # с предупреждением.
    log_warning "$(tf os_newer "${version_id}")"
    return 2
}

# ── Package manager helpers ─────────────────────────────────────────────
# unattended-upgrades стоит на КАЖДОМ облачном образе Ubuntu, и apt-daily
# срабатывает вскоре после первой загрузки. Установщик, запущенный в первые
# минуты жизни VPS, гарантированно упрётся в блокировку dpkg.
apt_q() {
    DEBIAN_FRONTEND=noninteractive \
    apt-get -y \
        -o DPkg::Lock::Timeout=1800 \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        "$@"
}

# ГРАБЛИ: `timeout 300 cloud-init status --wait` — это до ПЯТИ МИНУТ полной
# тишины на свежем сервере, где cloud-init ещё доводит первую загрузку.
# Оператор видит замерший экран и делает единственный доступный вывод.
# Ждём только если он и правда ещё работает, и показываем это.
wait_for_cloud_init() {
    command -v cloud-init >/dev/null 2>&1 || return 0
    # Уже закончил — не ждём вовсе, это мгновенная проверка.
    if timeout 5 cloud-init status 2>/dev/null | grep -qE 'status:\s*(done|disabled|error)'; then
        return 0
    fi
    run_with_progress "$(t wait_cloud_init)" \
        timeout 300 cloud-init status --wait || true
    return 0
}

# ── Готовность apt: спросить ДО того, как начали ────────────────────────
#
# ГРАБЛИ, найденные на свежепереустановленном сервере: unattended-upgrades
# на первой загрузке ставит полное обновление системы — в логе было больше
# двухсот пакетов вместе с ядром — и держит блокировку dpkg ДЕСЯТКАМИ минут.
# Замерено: 47 минут и ещё не закончил.
#
# Наш apt при этом ждёт DPkg::Lock::Timeout=600, то есть 10 минут, и потом
# ОТКАЗЫВАЕТ. Получалось худшее из двух: десять минут тишины, а следом
# «не удалось установить certbot» — на машине, где всё в порядке, просто
# система занята собой.
#
# Поэтому спрашиваем ПЕРЕД началом работ и говорим правду о сроках, а не
# упираемся в таймаут молча.
apt_lock_holder() {
    local f h
    for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock; do
        [ -e "$f" ] || continue
        h=$(fuser "$f" 2>/dev/null | tr -d ' ') || h=""
        if [ -n "$h" ]; then
            printf '%s' "$(ps -p "$h" -o comm= 2>/dev/null | tr -d ' ')"
            return 0
        fi
    done
    printf ''
    return 1
}

# Сколько секунд держит.
apt_lock_age() {
    local f h
    for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock; do
        [ -e "$f" ] || continue
        h=$(fuser "$f" 2>/dev/null | tr -d ' ') || h=""
        if [ -n "$h" ]; then
            ps -p "$h" -o etimes= 2>/dev/null | tr -d ' '
            return 0
        fi
    done
    printf '0'
    return 1
}

# Последняя строка о том, чем система занята — чтобы ожидание было видимым.
_apt_current_work() {
    local l
    l=$(tail -n 40 /var/log/unattended-upgrades/unattended-upgrades-dpkg.log 2>/dev/null \
        | grep -oE '^(Unpacking|Setting up|Preparing to unpack) [^ ]+' | tail -1) || l=""
    printf '%s' "$l"
    return 0
}

# rc0 — можно работать. rc1 — оператор отказался ждать.
apt_wait_ready() {
    local holder age waited=0 step=10 work=""
    holder=$(apt_lock_holder) || holder=""
    [ -n "$holder" ] || return 0

    age=$(apt_lock_age) || age=0
    log_warning "$(tf apt_busy_who "$holder" "$((age / 60))")"
    log_dim "$(t apt_busy_why)"
    if ! confirm "$(t apt_busy_wait_ask)" y; then
        log_info "$(t apt_busy_declined)"
        return 1
    fi

    while : ; do
        holder=$(apt_lock_holder) || holder=""
        if [ -z "$holder" ]; then
            printf '\r\033[2K' >&2
            log_success "$(t apt_busy_free)"
            return 0
        fi
        work=$(_apt_current_work) || work=""
        printf '\r\033[2K  %b⋯%b %s  %b%s%b' "$CYAN" "$NC" \
            "$(tf apt_busy_waiting "$((waited / 60))")" "$DIM" "${work:-}" "$NC" >&2
        sleep "$step"
        waited=$((waited + step))
    done
}

ensure_packages() {
    local missing=() p
    for p in "$@"; do
        case "$p" in
            bind9-dnsutils) command -v dig    >/dev/null 2>&1 || missing+=("$p") ;;
            knot-dnsutils)  command -v kdig   >/dev/null 2>&1 || missing+=("$p") ;;
            ca-certificates) [ -r /etc/ssl/certs/ca-certificates.crt ] || missing+=("$p") ;;
            *)              command -v "$p"   >/dev/null 2>&1 || missing+=("$p") ;;
        esac
    done
    if [ ${#missing[@]} -eq 0 ]; then
        return 0
    fi
    apt_wait_ready || return 1
    run_with_progress "$(t deps_updating)" apt_q update || true
    if ! run_with_progress "$(tf deps_installing "${missing[*]}")" \
            apt_q install --no-install-recommends "${missing[@]}"; then
        log_error "$(tf deps_install_failed "${missing[*]}")"
        return 1
    fi
    return 0
}

# ── JSON — python3, not jq ──────────────────────────────────────────────
# python3 присутствует на всех образах Ubuntu 22.04/24.04; jq — нет.
json_get() {
    local file="$1" key="$2" default="${3:-}"
    [ -f "$file" ] || { printf '%s' "$default"; return 0; }
    python3 - "$file" "$key" "$default" <<'PY' 2>/dev/null || printf '%s' "$default"
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
except Exception:
    sys.stdout.write(default); raise SystemExit(0)
cur = data
for part in key.split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        sys.stdout.write(default); raise SystemExit(0)
if cur is None or isinstance(cur, (dict, list)):
    sys.stdout.write(default)
elif isinstance(cur, bool):
    sys.stdout.write("true" if cur else "false")
else:
    sys.stdout.write(str(cur))
PY
}

config_get() { json_get "$GODNS_CONFIG" "$1" "${2:-}"; }

config_set() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$GODNS_CONFIG")" 2>/dev/null || true
    [ -f "$GODNS_CONFIG" ] || echo '{}' > "$GODNS_CONFIG"
    python3 - "$GODNS_CONFIG" "$key" "$value" <<'PY' || return 1
import fcntl, json, os, sys, tempfile
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]

# Межпроцессный замок. Писателей ТРИ по построению: SSH-меню, демон бота
# (root) и панель (root) — все через этот же config_set. Атомарный
# os.replace защищал файл от порчи, но не от потерянного обновления:
# двое читают один снимок, каждый пишет свой ключ, второй replace
# затирает первый. Оператор менял апстримы в панели, пока бот крутил
# путь DoH, — и пропадал либо новый путь (клиенты отваливаются молча),
# либо половина пары upstream_ids/upstreams.
#
# Замок — ОТДЕЛЬНЫЙ файл-сосед: сам config.json заменяется через
# os.replace, и flock на нём держал бы старый inode — второй писатель
# спокойно залочил бы новый файл. Файл-замок не заменяется никогда.
lock_path = path + ".lock"
lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[key] = value
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
finally:
    os.close(lock_fd)
PY
    chmod 600 "$GODNS_CONFIG" 2>/dev/null || true
    return 0
}

# ── Ports ───────────────────────────────────────────────────────────────
# Returns 0 if something already listens on the TCP port.
port_in_use() {
    local port="$1"
    ss -Hlnt 2>/dev/null | awk -v p=":${port}" '
        { split($4, a, ":"); if (":" a[length(a)] == p) found = 1 }
        END { exit !found }
    '
}

# Who holds it — "nginx (pid=123)" or "" if unknown.
port_owner() {
    local port="$1"
    ss -Hlntp 2>/dev/null | awk -v p=":${port}" '
        { split($4, a, ":");
          if (":" a[length(a)] == p) { print $0; exit } }
    ' | sed -n 's/.*users:((\("[^"]*"\),pid=\([0-9]*\).*/\1 (pid=\2)/p' | tr -d '"'
}

# First free TCP port from the candidate list; empty if all are taken.
first_free_port() {
    local p
    for p in "$@"; do
        port_in_use "$p" || { printf '%s' "$p"; return 0; }
    done
    return 1
}

valid_port() {
    local p="$1"
    case "$p" in ''|*[!0-9]*) return 1 ;; esac
    [ "$p" -ge 1 ] && [ "$p" -le 65535 ]
}

valid_domain() {
    [[ "$1" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$ ]]
}

# ── IP-литералы ─────────────────────────────────────────────────────────
# Движок прибивает каждый апстрим к ЛИТЕРАЛЬНОМУ IP (см. контракт в
# lib/engines/dnsdist.sh): dnsdist в address имя не разрешает, а нам и не
# надо — так резолвер стартует без единого открытого :53 «на разогрев».
# Свой апстрим человек вводит именем; имя разрешаем при добавлении, а в
# запись кладём адрес. Проверка здесь — чистый bash, без python на каждую
# запись при рендере.
valid_ip4() {
    local ip="${1:-}" a="" b="" c="" d="" o=""
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    IFS=. read -r a b c d <<< "$ip"
    for o in "$a" "$b" "$c" "$d"; do
        [ "$o" -le 255 ] || return 1
    done
    return 0
}
valid_ip6() {
    local ip="${1:-}" rest=""
    case "$ip" in *:*:*) ;; *) return 1 ;; esac
    [[ "$ip" =~ ^[0-9A-Fa-f:.]{2,45}$ ]] || return 1
    case "$ip" in *:::*) return 1 ;; esac
    # «::» допустимо не больше одного раза.
    rest="${ip//::/}"
    [ $(( ${#ip} - ${#rest} )) -le 2 ] || return 1
    return 0
}
valid_ip() { valid_ip4 "$1" || valid_ip6 "$1"; }

# Приватный адрес: RFC 1918, loopback, link-local, ULA. Нужен ровно для
# одной фразы — «обычный DNS к 10.0.0.53 остаётся внутри вашей сети», в
# отличие от открытого текста в интернет.
godns_ip_is_private() {
    case "${1:-}" in
        10.*|127.*|192.168.*|169.254.*)                 return 0 ;;
        172.1[6-9].*|172.2[0-9].*|172.3[01].*)          return 0 ;;
        ::1|[fF][cCdD]*|[fF][eE][89aAbB]*)              return 0 ;;
    esac
    return 1
}

# Имя → адреса, по одному в строке, IPv4 первыми. Через getaddrinfo самой
# системы: это единственный резолвер, который есть на любой машине до
# того, как goDNS настроен. Пусто — не разрешилось.
#
# Порядок — ОТСОРТИРОВАННЫЙ, а не как отдал резолвер. ГРАБЛИ с живого
# сервера: dns.google при пробе разрешился в 8.8.4.4, при записи — в
# 8.8.8.8 (round-robin у резолвера), и строка «имя → адрес» на экране
# расходилась с адресом в таблице. Первый адрес обязан быть одним и тем же
# в двух вызовах подряд.
godns_resolve_host() {
    local name="${1:-}"
    [ -n "$name" ] || return 1
    python3 - "$name" <<'RES' 2>/dev/null
import ipaddress, socket, sys
name = sys.argv[1]
seen, v4, v6 = set(), [], []
try:
    for fam, _t, _p, _c, sa in socket.getaddrinfo(name, 53, type=socket.SOCK_DGRAM):
        ip = sa[0]
        if ip in seen:
            continue
        seen.add(ip)
        (v4 if fam == socket.AF_INET else v6).append(ip)
except OSError:
    pass
key = lambda a: int(ipaddress.ip_address(a))
for ip in sorted(v4, key=key) + sorted(v6, key=key):
    print(ip)
RES
}

# ── Server IP ───────────────────────────────────────────────────────────
get_server_ip() {
    local ip=""
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p') || ip=""
    ip="${ip%%$'\n'*}"
    if [ -z "$ip" ]; then
        ip=$(curl -fsS --max-time 6 https://api.ipify.org 2>/dev/null || true)
    fi
    printf '%s' "$ip"
}

# ── Random secret path ──────────────────────────────────────────────────
# NB: `tr … | head -c N` looks obvious and is wrong — head closes the pipe,
# tr dies on SIGPIPE, and under `set -o pipefail` the whole substitution
# Границы секретного пути DoH. Восемь символов — не вкус, а порог:
# точная сверка пути в dnsdist есть ЕДИНСТВЕННОЕ, что отличает наш
# резолвер от открытого, и короткий путь подбирается перебором.
GODNS_DOH_PATH_MIN="${GODNS_DOH_PATH_MIN:-8}"
GODNS_DOH_PATH_MAX="${GODNS_DOH_PATH_MAX:-64}"

# Годен ли секретный путь DoH.
# stdout: причина отказа машинным словом (short|long|chars) — вызывающий
# сам решает, что сказать человеку. rc 0 — годен.
godns_doh_path_ok() {
    local p="${1:-}"
    p="${p#/}"
    case "$p" in
        ''|*[!A-Za-z0-9_/-]*) printf 'chars'; return 1 ;;
    esac
    if [ "${#p}" -lt "$GODNS_DOH_PATH_MIN" ]; then printf 'short'; return 1; fi
    if [ "${#p}" -gt "$GODNS_DOH_PATH_MAX" ]; then printf 'long';  return 1; fi
    return 0
}

# returns non-zero. `cut -c1-N` drains its input instead, so no signal.
random_path() {
    local n="${1:-12}" out=""
    out=$(LC_ALL=C head -c 512 /dev/urandom 2>/dev/null \
          | LC_ALL=C tr -dc 'A-Za-z0-9' 2>/dev/null \
          | cut -c1-"$n") || out=""
    if [ "${#out}" -lt "$n" ]; then
        out=$(python3 -c "import secrets,sys;sys.stdout.write(secrets.token_urlsafe(64).replace('-','').replace('_','')[:${n}])" 2>/dev/null) || out=""
    fi
    printf '%s' "$out"
}

# ── Temp file bookkeeping ───────────────────────────────────────────────
# ГРАБЛИ: почти каждый вызов — `f=$(godns_mktemp)`, то есть подоболочка.
# Пополнение массива GODNS_TMP_FILES умирало вместе с ней, cleanup при
# выходе не удалял НИЧЕГО, и /tmp зарастал файлами godns.* (замечено 30+
# на живом стенде). Поэтому в имя зашит PID главного процесса: `$$` внутри
# $( ) — это PID родителя, и уборка по глобу /tmp/godns.<pid>.* находит
# файлы независимо от того, в какой оболочке они родились.
GODNS_TMP_FILES=()
godns_mktemp() {
    local f
    f=$(mktemp "/tmp/godns.$$.XXXXXX") || return 1
    GODNS_TMP_FILES+=("$f")
    printf '%s' "$f"
}
cleanup_temp_files() {
    local f
    for f in "${GODNS_TMP_FILES[@]:-}"; do
        [ -n "$f" ] && rm -f "$f" 2>/dev/null
    done
    rm -f "/tmp/godns.$$."* 2>/dev/null || true
    return 0
}

# ── Единая склейка публичного адреса DoH ────────────────────────────────
# Собирать «https://…:порт/» + путь в трёх местах руками означало три разных
# соглашения про ведущий слэш. Путь в config.json хранится БЕЗ слэша,
# слэш добавляется ровно здесь и в отрендеренном Lua.
godns_doh_url() {
    local d p port
    d=$(config_get domain "")   || d=""
    port=$(config_get doh_port "") || port=""
    p=$(config_get doh_path "") || p=""
    p="${p#/}"
    [ -n "$d" ] && [ -n "$port" ] || { printf ''; return 1; }
    printf 'https://%s:%s/%s' "$d" "$port" "$p"
    return 0
}

# ── Долгая операция с признаками жизни ──────────────────────────────────
# ГРАБЛИ: `apt_q update >/dev/null 2>&1` на свежем сервере молчит до десяти
# минут — ровно столько DPkg::Lock::Timeout ждёт, пока unattended-upgrades
# (он стартует сам на первой загрузке) отпустит блокировку dpkg. Оператор
# видит замерший экран и делает единственный доступный вывод: «зависло».
# Молчание дольше пары секунд обязано показывать, что процесс жив и чего
# именно он ждёт.
# Третий аргумент-флаг не заводим: подсказку про apt показываем только
# тогда, когда мы и правда ждём apt. Иначе «ждём, пока система доустановит
# обновления» приписывается ЛЮБОЙ долгой операции — на живом сервере эта
# фраза висела рядом с проверкой сертификата, к apt отношения не имеющей,
# и уводила диагностику в сторону.
run_with_progress() {
    local msg="$1"; shift
    local is_apt="no"
    case "${1:-}" in apt_q|apt-get|apt|dpkg) is_apt="yes" ;; esac
    # Куда положить ВЫВОД КОМАНДЫ. По умолчанию — во временный файл, он
    # нужен только чтобы показать хвост при провале, и стирается в конце.
    #
    # ГРАБЛИ: вызывающие писали `run_with_progress … cmd >"$log" 2>&1`,
    # думая, что ловят вывод команды. Ловили спиннер: сам вывод уходил
    # СЮДА и удалялся. В /opt/godns/certbot-issue.log вместо строк certbot
    # лежала километровая строка из \r и \033[2K, а экран при этом молчал
    # всё время выпуска — спиннер-то уехал в файл. Обещание «лог живёт и
    # переживает выход» не выполнялось.
    #
    # Кому нужен настоящий вывод — передаёт путь через GODNS_PROGRESS_LOG:
    # тогда файл наш не стирает, а спиннер остаётся на экране.
    local log pid rc=0 sec=0 hint="" keep="no"
    if [ -n "${GODNS_PROGRESS_LOG:-}" ] && : > "${GODNS_PROGRESS_LOG}" 2>/dev/null; then
        log="$GODNS_PROGRESS_LOG"; keep="yes"
    else
        log=$(godns_mktemp) || log="/tmp/godns-progress.$$"
    fi

    "$@" >"$log" 2>&1 &
    pid=$!

    printf '  %b %s' "${CYAN}⋯${NC}" "$msg" >&2
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        sec=$((sec + 1))
        # После пяти секунд объясняем, чего ждём: молчаливый счётчик и сам
        # по себе лучше пустоты, но причина полезнее.
        hint=""
        if [ "$is_apt" = "yes" ] && [ "$sec" -ge 5 ] && _apt_is_busy; then
            hint=" — $(t wait_apt_lock)"
        fi
        # Крутящийся знак важнее счётчика: он доказывает, что процесс жив,
        # даже когда секунды идут медленно. Само слово «ждите» обязательно —
        # без него счётчик читается как «сколько уже висит».
        # ГРАБЛИ: цвета в проекте хранятся как ЛИТЕРАЛЬНЫЕ последовательности
        # («\033[0;36m»), потому что весь вывод идёт через echo -e. В printf
        # их разворачивает только %b — с %s оператор увидит на экране сам
        # текст «\033[2m» вместо тусклого шрифта.
        local spin_i=$(( sec % 4 )) spin
        case "$spin_i" in 0) spin="|" ;; 1) spin="/" ;; 2) spin="-" ;; *) spin="\\" ;; esac
        # ГРАБЛИ: строка длиннее ширины терминала ПЕРЕНОСИТСЯ, \r после
        # этого возвращает курсор в начало ВТОРОЙ строки, \033[2K чистит
        # только её — и спиннер начинает штабелевать строки: на живом
        # стенде в 80 колонок «Установка dnsdist … ждём, пока система…»
        # оставляла по строке на каждую секунду. Обрезаем видимый хвост
        # по СИМВОЛАМ (str_width считает их, а не байты) под ширину.
        local cols line
        cols=$( { tput cols; } 2>/dev/null ) || cols=""
        case "$cols" in ''|*[!0-9]*) cols="${COLUMNS:-80}" ;; esac
        line="${msg}  $(t wait_please)  ⏱ ${sec}s${hint}"
        while [ "$(str_width "$line")" -gt $((cols - 5)) ] && [ -n "$line" ]; do
            line="${line%?}"
        done
        printf '\r\033[2K  %b%s%b %s' "$CYAN" "$spin" "$NC" "$line" >&2
    done
    wait "$pid" || rc=$?

    printf '\r\033[2K' >&2
    if [ "$rc" -eq 0 ]; then
        log_success "$(printf '%s' "$msg")"
    else
        log_error "$(printf '%s' "$msg")"
        tail -n 12 "$log" >&2 2>/dev/null || true
    fi
    [ "$keep" = "yes" ] || rm -f "$log" 2>/dev/null || true
    return "$rc"
}

# Держит ли кто-то apt/dpkg прямо сейчас.
_apt_is_busy() {
    local f
    for f in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock; do
        [ -e "$f" ] || continue
        if command -v fuser >/dev/null 2>&1 && fuser "$f" >/dev/null 2>&1; then return 0; fi
    done
    systemctl is-active --quiet unattended-upgrades.service 2>/dev/null && return 0
    systemctl is-active --quiet apt-daily.service 2>/dev/null && return 0
    systemctl is-active --quiet apt-daily-upgrade.service 2>/dev/null && return 0
    return 1
}

# ── Ширина строки в СИМВОЛАХ, а не в байтах ─────────────────────────────
# ${#s} считает байты, когда LANG не выставлен (sudo -i на минимальном
# образе, запуск из systemd), и кириллица — два байта на букву. Приём:
# под LC_ALL=C выбросить продолжающие байты UTF-8 (10xxxxxx) и посчитать
# остаток — это и есть число символов, независимо от локали.
str_width() {
    local LC_ALL=C
    local s="${1:-}"
    s="${s//[$'\x80'-$'\xbf']/}"
    printf '%s' "${#s}"
    return 0
}

# ── Мягкий перенос длинной строки ───────────────────────────────────────
# Терминал переносит ЖЁСТКО: хвост уезжает в колонку 0, и дерево отчёта
# самопроверки («└ нота») ломается — продолжение выглядит как отдельная
# запись верхнего уровня. Переносим сами, по пробелам, с отступом.
#
#   str_wrap <ширина> <отступ продолжения> <текст>
#
# Первая строка печатается БЕЗ отступа: её префикс ставит вызывающий
# («        └ »). Каждая следующая — с указанным числом пробелов.
#
# Слово длиннее ширины не режем: это всегда адрес или путь, и разорванный
# пополам он перестаёт копироваться мышью. Такая строка вылезет — но одна,
# а не весь хвост абзаца.
#
# Глоббинг на время разбора выключаем: нота вроде «пути *.gz не отвечают»
# иначе развернётся в список файлов текущего каталога.
# Ширину меряем ВНУТРИ, без вызова str_width: тот живёт в $( ), то есть
# подоболочка на КАЖДОЕ слово. На отчёте самопроверки это сотни форков на
# ровном месте. `local LC_ALL=C` переводит все подстановки в байты, и приём
# со снятием продолжающих байт UTF-8 даёт число символов прямо здесь.
# Печатаем при этом ИСХОДНУЮ строку, а не обрезанную копию.
str_wrap() {
    local LC_ALL=C
    local w="${1:-70}" ind="${2:-0}" s="${3:-}"
    local line="" word="" cand="" bare="" pad="" n=0 first=1 had_f=0
    case "$w"   in ''|*[!0-9]*) w=70 ;; esac
    case "$ind" in ''|*[!0-9]*) ind=0 ;; esac
    if [ "$w" -lt 20 ]; then w=20; fi

    n="$ind"
    while [ "$n" -gt 0 ]; do pad="${pad} "; n=$((n - 1)); done

    case "$-" in *f*) had_f=1 ;; esac
    set -f
    for word in $s; do
        if [ -z "$line" ]; then
            line="$word"
            continue
        fi
        cand="${line} ${word}"
        bare="${cand//[$'\x80'-$'\xbf']/}"
        if [ "${#bare}" -le "$w" ]; then
            line="$cand"
        else
            if [ "$first" -eq 1 ]; then printf '%s\n' "$line"; first=0
            else                        printf '%s%s\n' "$pad" "$line"; fi
            line="$word"
        fi
    done
    if [ "$had_f" -eq 0 ]; then set +f; fi

    if [ -n "$line" ]; then
        if [ "$first" -eq 1 ]; then printf '%s' "$line"
        else                        printf '%s%s' "$pad" "$line"; fi
    fi
    return 0
}

# ── Колонка «подпись → значение» ────────────────────────────────────────
# Выравнивание одно на продукт. Отступы, забитые пробелами вручную под
# длину РУССКОЙ подписи, разъезжаются при переводе: «Version:» на символ
# длиннее «Версия:», «Days left:» на четыре короче «Осталось дней:».
# Пять таких блоков давали пять разных колонок в каждом из двух языков.
#
# Ширина по умолчанию — 12: самая длинная подпись главного экрана
# («Certificate:») ровно столько и занимает.
GODNS_LBL_W="${GODNS_LBL_W:-12}"

pad_label() {
    local s="${1:-}" w="${2:-${GODNS_LBL_W:-12}}" n=0
    n=$(( w - $(str_width "$s") ))
    printf '%s' "$s"
    while [ "$n" -gt 0 ]; do printf ' '; n=$((n - 1)); done
    return 0
}

# kv_row <подпись БЕЗ двоеточия> <значение с цветом> [ширина колонки]
# Двоеточие ставит эта функция — иначе половина подписей в каталоге строк
# была бы с ним, половина без, и разнобой уехал бы в перевод.
kv_row() {
    printf '  %s %b\n' "$(pad_label "${1}:" "${3:-${GODNS_LBL_W:-12}}")" "$2"
    return 0
}

# ── Снимок конфига на время мастера ─────────────────────────────────────
# Мастер пишет config_set на каждом шаге сразу. Если оператор отказался на
# подтверждении или прервал мастер, настройки уже изменены, а служба живёт
# по старым — панель начинает показывать адрес, которого нет. Снимок и
# откат делают отказ по-настоящему безвредным.
_wizard_snapshot_config() {
    GODNS_CFG_SNAPSHOT=""
    [ -f "$GODNS_CONFIG" ] || return 0
    local f; f=$(godns_mktemp) || return 0
    cp -a "$GODNS_CONFIG" "$f" 2>/dev/null && GODNS_CFG_SNAPSHOT="$f"
    return 0
}

_wizard_restore_config() {
    [ -n "${GODNS_CFG_SNAPSHOT:-}" ] || return 0
    [ -f "$GODNS_CFG_SNAPSHOT" ] || return 0
    if cp -a "$GODNS_CFG_SNAPSHOT" "$GODNS_CONFIG" 2>/dev/null; then
        log_dim "$(t wiz_config_restored)"
    fi
    return 0
}

# ── Отпечаток применённой конфигурации ──────────────────────────────────
# Единственный способ отличить «настроено» от «работает»: запомнить, с
# какими значениями служба была поднята, и сверять. Без этого расхождение
# видно только когда клиент жалуется, что DNS не работает.
#
# Набор полей = всё, что влияет на отрендеренный конфиг движка. Раньше тут
# было шесть «главных» полей, и правка cert_path/acl/plain_listen руками в
# config.json (шапка dnsdist.conf прямо предлагает «меняйте config.json»)
# не зажигала «есть непринятые изменения» — панель молчала о расхождении.
# ГРАБЛИ: эта функция сначала жила в lib/bot.sh, а зовут её ОБА приложения —
# и бот, и панель. В godns.sh подключены оба модуля, поэтому меню работало;
# а веб-мост подключает только webui.sh, и там она превращалась в
# «command not found» ровно в тот момент, когда панель пыталась обновиться.
# ── Отпечаток исходников установленного приложения ─────────────────────
# Отпечаток исходников приложения: имя+содержимое всех файлов, что мы
# копируем. sha256sum есть не везде, openssl — есть.
godns_app_fingerprint() {   # <каталог-источник> [расширения…]
    local src="$1"; shift
    [ -d "$src" ] || return 1
    find "$src" -type f \( -name '*.py' -o -name '*.js' -o -name '*.css' \
         -o -name '*.html' -o -name '*.json' -o -name '*.sh' -o -name '*.txt' \) \
         2>/dev/null | LC_ALL=C sort | while IFS= read -r f; do
        printf '%s\n' "${f#"$src"}"
        cat "$f" 2>/dev/null
    done \
    | { command -v sha256sum >/dev/null 2>&1 && sha256sum || openssl dgst -sha256; } 2>/dev/null \
    | tr -dc 'a-f0-9' | cut -c1-32
}

godns_config_fingerprint() {
    # dot_enabled ОБЯЗАН быть здесь: он меняет отрендеренный конфиг
    # (addTLSLocal есть или нет), но в отпечаток не входил. Включённый и
    # непринятый DoT давал совпадающий отпечаток, метка «есть непринятые
    # изменения» не зажигалась, и панель состояния печатала адрес DoT
    # рядом с мёртвым портом — а это ровно то место, откуда адрес копируют.
    # ratelimit_qps — по той же причине: меняет отрендеренный конфиг.
    printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s' \
        "$(config_get domain "")"     "$(config_get doh_port "")" \
        "$(config_get dot_port "")"   "$(config_get doh_path "")" \
        "$(config_get policy "")"     "$(config_get upstreams "")" \
        "$(config_get cert_path "")"  "$(config_get key_path "")" \
        "$(config_get acl "")"        "$(config_get plain_listen "")" \
        "$(config_get cache "")"      "$(config_get dot_enabled "")" \
        "$(config_get ratelimit_qps "")" \
    | { command -v sha256sum >/dev/null 2>&1 && sha256sum || openssl dgst -sha256; } 2>/dev/null \
    | cut -c1-16
    return 0
}

# Штамп «эта конфигурация была применена ЭТОЙ версией». Версия и схема
# пишутся ровно здесь, а не при первом запуске меню: момент успешного
# применения — единственный, про который честно можно сказать «установка
# дошла до конца». Именно по нему lib/update.sh отличает обновление от
# первой установки, и именно поэтому просмотр меню без установки не
# оставляет следа, который потом читался бы как «уже стояло».
godns_mark_applied() {
    config_set applied_fingerprint "$(godns_config_fingerprint)" || return 1
    config_set godns_version "$GODNS_VERSION" || return 1
    config_set schema "$GODNS_SCHEMA" || return 1
    return 0
}

# rc0 — есть непринятые изменения.
godns_config_drifted() {
    local now saved
    now=$(godns_config_fingerprint) || return 1
    saved=$(config_get applied_fingerprint "") || saved=""
    [ -n "$saved" ] || return 1
    [ "$now" != "$saved" ]
}

# Каталог, куда install.sh кладёт саму программу.
GODNS_INSTALLER_DIR="${GODNS_INSTALLER_DIR:-/opt/godns-installer}"

# ── Межсетевой экран: открыть и закрыть свой порт ──────────────────────
# ГРАБЛИ: этих помощников было ДВЕ почти одинаковых пары — в godns.sh и в
# lib/webui.sh, — и они уже разошлись: одна ставила на правило пометку
# «goDNS», другая нет, и регулярки для ufw.conf отличались якорем. Пара в
# общем модуле нужна ещё и потому, что веб-мост подключает webui.sh без
# godns.sh: там версия из godns.sh была недоступна в принципе.
# Установлен ли рядом сосед. Печатает его имя (одно или через запятую),
# rc 1 — соседей нет.
#
# ЗАЧЕМ: «порт свободен» и «порт останется свободным» — РАЗНЫЕ утверждения.
# goTelegram ставит nginx на :443 только когда настроят прокси, а до того
# порт свободен. Мастер это видел и радостно занимал 443, обещая, что там
# «DoH не отличить от HTTPS», — а через день оператор настраивал соседа, и
# либо сосед не поднимался, либо резолвер отваливался у всех клиентов
# разом. Предупреждали при этом только про goVLESS, хотя стоять мог любой.
# Порты 3X-UI выбираются случайно при установке, статического списка не
# существует — читаем базу. Только чтение; sqlite3-бинарника на
# minimal-образах нет, python3 есть.
godns_xui_ports() {
    [ -r "$XUI_DB_PATH" ] || return 0
    python3 - "$XUI_DB_PATH" <<'XUIPY' 2>/dev/null || true
import sqlite3, sys
try:
    con = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
    rows = con.execute(
        "select value from settings where key in ('webPort','subPort')").fetchall()
    for (v,) in rows:
        v = str(v).strip()
        if v.isdigit():
            print(v)
except Exception:
    pass
XUIPY
    return 0
}

# Порт числится за кем-то ПО ПОСТРОЕНИЮ, даже если сейчас его никто не
# слушает: таблица портов соседей, наши собственные, случайные порты 3X-UI.
#
# ГРАБЛИ: это умела панель (_webui_port_reserved) и НЕ умел мастер. Мастер
# смотрел только «занят ли сейчас», а goTelegram занимает :443 своим nginx
# лишь после настройки прокси. На машине с установленным, но ещё не
# настроенным соседом мастер брал 443 — а следом предлагал 8443, то есть
# порт САЙТА goTelegram. Через день соседа настраивали, и либо он не
# поднимался, либо резолвер отваливался у всех клиентов разом.
GODNS_NEIGHBOUR_PORTS="${GODNS_NEIGHBOUR_PORTS:- 80 443 853 1984 7443 8443 9090 9091 9443 }"

godns_port_reserved() {
    local p="${1:-}" r
    [ -n "$p" ] || return 1
    case "$GODNS_NEIGHBOUR_PORTS" in
        *" $p "*) return 0 ;;
    esac
    for r in "$(config_get doh_port "")" "$(config_get dot_port "")" \
             "$(config_get webui_port "")" "$(config_get webui_local_port "")" \
             $(godns_xui_ports); do
        [ -n "$r" ] && [ "$r" = "$p" ] && return 0
    done
    return 1
}

# Первый кандидат, который и свободен СЕЙЧАС, и ни за кем не числится.
godns_first_free_port() {
    local p
    for p in "$@"; do
        port_in_use "$p" && continue
        godns_port_reserved "$p" && continue
        printf '%s' "$p"
        return 0
    done
    return 1
}

godns_neighbour_present() {
    local found=""
    if [ -d "${GOVLESS_DIR:-/opt/govless}" ]; then
        found="goVLESS"
    fi
    if [ -d "${GOTELEGRAM_DIR:-/opt/gotelegram}" ]; then
        found="${found}${found:+, }goTelegram"
    fi
    [ -n "$found" ] || return 1
    printf '%s' "$found"
    return 0
}

# Привести файрвол в соответствие текущим портам: открыть DoH; DoT —
# только если включён, иначе закрыть; закрыть порты, с которых ушли.
#
# ГРАБЛИ, из-за которых функция здесь, а не в godns.sh: её звал бот
# (bot_apply_if_installed), а определена она была ТОЛЬКО в godns.sh,
# который бот не сорсит вовсе. В процессе демона это `command not found`,
# проглоченный `|| true`: «Включить DoT» из Telegram применяло конфиг, но
# порт в ufw не открывало — при зелёном ответе клиенты ловили таймаут.
# check-callgraph слеп к границе наборов source, поэтому вызов «резолвился»
# определением из godns.sh и страж молчал. Три пути (SSH, панель, бот)
# теперь зовут ОДНУ функцию — расхождению зеркал неоткуда взяться.
godns_fw_apply_ports() {
    local doh dot dot_on p
    doh=$(config_get doh_port "") || doh=""
    dot=$(config_get dot_port "") || dot=""
    dot_on=$(config_get dot_enabled "no") || dot_on="no"
    godns_fw_open "$doh" || true
    if [ "$dot_on" = "yes" ]; then
        godns_fw_open "$dot" || true
    else
        godns_fw_close "$dot" || true
    fi
    # Закрыть ушедшие порты ПОСЛЕ открытия новых: закрыть первым и
    # споткнуться — оставить оператора без доступа.
    for p in "$(config_get doh_port_prev "")" "$(config_get dot_port_prev "")"; do
        [ -n "$p" ] || continue
        [ "$p" = "$doh" ] && continue
        [ "$p" = "$dot" ] && [ "$dot_on" = "yes" ] && continue
        godns_fw_close "$p" || true
    done
    config_set doh_port_prev "" || true
    config_set dot_port_prev "" || true
    return 0
}

godns_fw_open() {
    local p="$1"
    [ -n "$p" ] || return 0
    if command -v ufw >/dev/null 2>&1 && [ -r /etc/ufw/ufw.conf ] \
       && grep -qE '^[[:space:]]*ENABLED=yes[[:space:]]*$' /etc/ufw/ufw.conf; then
        ufw allow "${p}/tcp" comment "goDNS Pro" >/dev/null 2>&1 \
            && log_dim "$(tf fw_opened "$p")"
        return 0
    fi
    if command -v nft >/dev/null 2>&1; then
        # ГРАБЛИ со стенда: совет «откройте порт вручную» печатался на любой
        # машине, где вообще есть input-цепочка — в том числе с policy accept
        # и без единого drop, то есть там, где не блокируется ничего. Оператор
        # шёл открывать открытое (и check-host.net в это же время видел порт
        # open). Смотрим не на наличие цепочки, а на то, может ли она ронять.
        local _blk=""
        _blk=$(nft list ruleset 2>/dev/null | awk '
            /hook input/ { c = 1 }
            c            { print }
            c && /^[[:space:]]*}[[:space:]]*$/ { c = 0 }
        ') || _blk=""
        if [ -n "$_blk" ]; then
            if grep -qE 'policy[[:space:]]+(drop|reject)|(^|[[:space:]])(drop|reject)([[:space:]]|;|$)' <<< "$_blk"; then
                log_dim "$(tf fw_manual_nft "$p")"
            elif [ -z "${GODNS_FW_NFT_OPEN_SAID:-}" ]; then
                GODNS_FW_NFT_OPEN_SAID=1
                log_dim "$(t fw_nft_accept)"
            fi
            return 0
        fi
    fi
    # «Файрвола нет» не зависит от порта, а _fw_open зовётся на каждый.
    # Без стража одна и та же строка печаталась дважды подряд (DoH и DoT),
    # и оператор читал это как заикание, а не как факт. Один раз на процесс.
    if [ -z "${GODNS_FW_NONE_SAID:-}" ]; then
        GODNS_FW_NONE_SAID=1
        log_dim "$(t fw_none)"
    fi
    return 0
}

godns_fw_close() {
    local p="$1"
    [ -n "$p" ] || return 0
    command -v ufw >/dev/null 2>&1 || return 0
    [ -r /etc/ufw/ufw.conf ] || return 0
    grep -qE '^[[:space:]]*ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null || return 0
    ufw delete allow "${p}/tcp" >/dev/null 2>&1 && log_dim "$(tf fw_closed "$p")"
    return 0
}
