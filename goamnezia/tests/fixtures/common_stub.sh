#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — заглушка lib/common.sh + lib/i18n.sh для tests/test_core_dryrun.sh
#
# Подключается ТОЛЬКО когда настоящих lib/common.sh и lib/i18n.sh ещё нет
# (модули пишутся параллельно разными агентами). Реализует контракт §4.1 в том
# объёме, который нужен ядру docker/amnezia/users/export: константы путей,
# log_*, замок, чтение/запись config.json и users.json, ga_emit_json, ga_audit,
# ga_now, validate_*, движок t/tf со словарями lib/lang/<lang>.sh + <lang>.d/*.sh.
# Поведение повторяет goTelegram (реентрантный замок, атомарная запись через
# mktemp рядом с целью + mv, «null → пусто» у ga_config_get).
# shellcheck disable=SC2034
# ══════════════════════════════════════════════════════════════════════════════

GOAMNEZIA_VERSION="0.1.0"
GOAMNEZIA_DIR="${GOAMNEZIA_DIR:-/opt/goamnezia}"
CONFIG_FILE="${CONFIG_FILE:-$GOAMNEZIA_DIR/config.json}"
USERS_FILE="${USERS_FILE:-$GOAMNEZIA_DIR/users.json}"
STATE_DIR="${STATE_DIR:-$GOAMNEZIA_DIR/state}"
BACKUP_DIR="${BACKUP_DIR:-$GOAMNEZIA_DIR/backups}"
UPSTREAM_DIR="${UPSTREAM_DIR:-$GOAMNEZIA_DIR/lib/upstream}"
# Имена переопределений — как у настоящего common.sh (GOAMNEZIA_*).
LOCK_DIR="${GOAMNEZIA_LOCK_DIR:-/run/goamnezia}"
LOCK_FILE="${GOAMNEZIA_LOCK_FILE:-$LOCK_DIR/config.lock}"
LOG_FILE="${GOAMNEZIA_LOG_FILE:-/var/log/goamnezia.log}"
AUDIT_LOG="${GOAMNEZIA_AUDIT_LOG:-/var/log/goamnezia-audit.log}"
# Код последней ошибки для моста --action (см. lib/common.sh): библиотеки не
# делают exit, а кладут причину сюда через ga_set_error.
GA_LAST_ERROR_CODE=""
ga_set_error() { GA_LAST_ERROR_CODE="${1:-}"; return 0; }
BOT_DIR="/opt/goamnezia-bot"
ADMIN_DIR="/opt/goamnezia-admin"
MCP_DIR="/opt/goamnezia-mcp"
SITE_ROOT="/var/www/goamnezia-site"
PROFILE_IDS="awg2 awg3 awg1"

RED=""; GREEN=""; YELLOW=""; CYAN=""; WHITE=""; BOLD=""; DIM=""; NC=""

log_info()    { printf '  i %s\n' "$*" >&2; }
log_success() { printf '  + %s\n' "$*" >&2; }
log_warning() { printf '  ! %s\n' "$*" >&2; }
log_error()   { printf '  x %s\n' "$*" >&2; }
log_step()    { printf '\n  == %s\n' "$*" >&2; }
log_dim()     { printf '  . %s\n' "$*" >&2; }

# ── i18n ──────────────────────────────────────────────────────────────────────
declare -gA I18N
LANG_CODE="${LANG_CODE:-ru}"
GA_STUB_LANG_DIR="${LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../lib" && pwd)}/lang"

load_language() {
    local lang="${1:-ru}" f
    [[ "$lang" =~ ^[a-z]{2}$ ]] || lang="ru"
    I18N=()
    if [ -f "$GA_STUB_LANG_DIR/$lang.sh" ]; then
        # shellcheck disable=SC1090
        source "$GA_STUB_LANG_DIR/$lang.sh"
    fi
    for f in "$GA_STUB_LANG_DIR/$lang.d"/*.sh; do
        # shellcheck disable=SC1090
        [ -f "$f" ] && source "$f"
    done
    LANG_CODE="$lang"
    return 0
}

t() {
    local val="${I18N[$1]:-}"
    if [ -z "$val" ]; then echo "$1"; else echo "$val"; fi
}

tf() {
    local key="$1"; shift
    local fmt="${I18N[$key]:-$key}"
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# ── Замок ─────────────────────────────────────────────────────────────────────
# Реентрантный: вложенный вызов под GA_CONFIG_LOCK_HELD просто выполняет тело.
# На macOS flock нет — выполняем без замка (тесты однопоточные).
ga_with_config_lock() {
    [ $# -gt 0 ] || return 0
    if [ -n "${GA_CONFIG_LOCK_HELD:-}" ]; then "$@"; return $?; fi
    if ! command -v flock >/dev/null 2>&1; then
        GA_CONFIG_LOCK_HELD=1 "$@"
        return $?
    fi
    mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true
    local _fd _rc=0
    { exec {_fd}>"$LOCK_FILE"; } 2>/dev/null || { "$@"; return $?; }
    if ! flock -w 30 "$_fd"; then
        { exec {_fd}>&-; } 2>/dev/null
        return 75
    fi
    GA_CONFIG_LOCK_HELD=1 "$@" || _rc=$?
    { exec {_fd}>&-; } 2>/dev/null
    return $_rc
}

# ── config.json / users.json ─────────────────────────────────────────────────
ga_json_write_atomic() {  # <файл> ← stdin (валидный JSON), 0600
    local target="$1" tmp
    mkdir -p "$(dirname "$target")" 2>/dev/null
    tmp=$(mktemp "$(dirname "$target")/.tmp.XXXXXX") || return 1
    if ! jq -e . >"$tmp" 2>/dev/null; then rm -f "$tmp"; return 1; fi
    chmod 600 "$tmp"
    mv "$tmp" "$target"
}

ga_config_get() {
    [ -f "$CONFIG_FILE" ] || return 0
    jq -r "$1 | if . == null then empty else . end" "$CONFIG_FILE" 2>/dev/null
}

ga_config_set() {
    local path="$1" value="$2" cur
    if [ -f "$CONFIG_FILE" ]; then cur=$(cat "$CONFIG_FILE"); else cur='{}'; fi
    printf '%s' "$cur" | jq --argjson v "$value" "$path = \$v" | ga_json_write_atomic "$CONFIG_FILE"
}

ga_config_merge() {
    local patch="$1" cur
    if [ -f "$CONFIG_FILE" ]; then cur=$(cat "$CONFIG_FILE"); else cur='{}'; fi
    printf '%s' "$cur" | jq --argjson p "$patch" '. * $p' | ga_json_write_atomic "$CONFIG_FILE"
}

ga_users_read() {
    if [ -f "$USERS_FILE" ]; then cat "$USERS_FILE"; else printf '{"schema":1,"users":[]}\n'; fi
}

ga_users_write() { ga_json_write_atomic "$USERS_FILE"; }

# ── Прочее из §4.1 ────────────────────────────────────────────────────────────
ga_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

ga_audit() {
    local who="$1" action="$2" details="${3:-}"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || return 0
    printf '%s\t%s\t%s\t%s\n' "$(ga_now)" "$who" "$action" "$details" >>"$AUDIT_LOG" 2>/dev/null || true
}

# ga_emit_json <status> <message> [k=v …]; значения с префиксом @json: — как JSON.
ga_emit_json() {
    local status="$1"; shift
    local message="$1"; shift
    local extra="" kv k v
    for kv in "$@"; do
        k="${kv%%=*}"; v="${kv#*=}"
        if [[ "$v" == @json:* ]]; then
            extra="${extra},\"${k}\":${v#@json:}"
        else
            v="${v//\\/\\\\}"; v="${v//\"/\\\"}"
            extra="${extra},\"${k}\":\"${v}\""
        fi
    done
    local msg="${message//\\/\\\\}"; msg="${msg//\"/\\\"}"
    printf '{"status":"%s","message":"%s"%s}\n' "$status" "$msg" "$extra"
}

validate_name()   { [[ "$1" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; }
validate_port()   { [[ "$1" =~ ^[0-9]{1,5}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
validate_domain() { [[ "$1" =~ ^([a-z0-9](-*[a-z0-9])*)(\.([a-z0-9](-*[a-z0-9])*))+$ ]]; }
generate_hex()    { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])//2))' "${1:-32}"; }
get_server_ip()   { echo "0.0.0.0"; return 1; }
apt_install()     { log_info "stub apt_install: $*"; return 0; }
apt_lock_wait()   { return 0; }
check_deps_present() { return 0; }
init_dirs() {
    mkdir -p "$GOAMNEZIA_DIR" "$STATE_DIR" "$BACKUP_DIR" 2>/dev/null
    chmod 700 "$STATE_DIR" "$BACKUP_DIR" 2>/dev/null
}
