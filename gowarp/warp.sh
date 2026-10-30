#!/bin/bash
set -o pipefail

# ══════════════════════════════════════════════════════════════
#  WARP Manager v2.5.2 — Unified 3X-UI + AmneziaWG
#  Cloudflare WARP · Free / WARP+ · Multi-key · Telegram Bot
#  Channel: https://www.youtube.com/@antenkaru
# ══════════════════════════════════════════════════════════════

WARP_VERSION="2.5.2"
CONFIG_VERSION="4"
CONFIG_SCHEMA_VERSION="5"
GOWARP_BIN="/usr/local/bin/gowarp"
WARP_DIR="/etc/warp-manager"
WARP_CONF="$WARP_DIR/config"
WARP_KEYS="$WARP_DIR/keys.list"
WARP_BOT_STATE="$WARP_DIR/bot.state"
WARP_LOG="/var/log/warp-manager.log"
BOT_PID_FILE="/var/run/warp_bot.pid"
DEFAULT_PORT=40000

WGCF_VERSION="2.2.31"
# SHA-256 сборок wgcf 2.2.31, сверено с digest релиза на GitHub.
# 2.2.30 и 2.2.32 Cloudflare с 2026-09-10 отбивает на регистрации ошибкой 429.
WGCF_SHA256_AMD64="69147e1a517c66129edd8ac8cb60484d6c9515178d7b4a2f95e3c925f225572a"
WGCF_SHA256_ARM64="b9bdbdeaa3f9f4ba741ba55b8bd94c24f7166c27668eb7e8192ccf9746961182"
WGCF_SHA256_ARMV7="207a932daba270dce9a604cda69554e08a25626a1f0b2ab4c00fc841a70b5667"
AWG_REGISTER_GAP=65
# Стандартные wg и wg-quick для WARP в контейнере: сначала репозитории контейнера, затем зеркала Alpine.
AWG_ALPINE_MIRRORS="https://mirror.yandex.ru/mirrors/alpine https://dl-cdn.alpinelinux.org/alpine https://mirrors.edge.kernel.org/alpine"
WGCF_BIN="/root/wgcf"
WGCF_ACCOUNT="/root/wgcf-account.toml"
WGCF_PROFILE="/root/wgcf-profile.conf"

AWG_WARP_DIR="/opt/warp"
AWG_WARP_CONF="$AWG_WARP_DIR/warp.conf"
# Выбор клиентов WARP: публичные ключи клиентов, по строке. Адрес по ключу берётся из конфига сервера,
# поэтому выбор переживает смену подсети и перенумерацию в goAmnezia. Строки-адреса — от версий до 2.5.2.
AWG_WARP_CLIENTS="$AWG_WARP_DIR/clients.list"
# Клиенты по ключам — одна программа awk, её выполняет awk внутри контейнера (goWARP, блок в start.sh, автопроверка):
#   без list — «адрес ключ» каждого клиента из конфига сервера (адрес — первый IPv4 /32 из AllowedIPs);
#   с -v list=<clients.list> — адреса выбранных: строки-адреса как есть, затем ключи в порядке конфига.
# Без обратных косых и одинарных кавычек: программа стоит в start.sh внутри одинарных кавычек.
AWG_CLIENTS_AWK='function out(v) { if (!(v in seen)) { seen[v] = 1; print v } }
function flush() { if (key != "" && ip4 != "") { if (list == "") print ip4 " " key; else if (key in want) out(ip4) }; key = ""; ip4 = "" }
BEGIN { if (list != "") while ((getline l < list) > 0) { gsub("[[:space:]]", "", l); if (l ~ "^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+(/[0-9]+)?$") { if (l !~ "/") l = l "/32"; out(l) } else if (l != "") want[l] = 1 }; if (list != "") close(list) }
$0 ~ "^[[:space:]]*[[]" { flush() }
$0 ~ "^[[:space:]]*PublicKey[[:space:]]*=" { s = $0; sub("^[^=]*=", "", s); gsub("[[:space:]]", "", s); key = s }
$0 ~ "^[[:space:]]*AllowedIPs[[:space:]]*=" { s = $0; sub("^[^=]*=", "", s); n = split(s, a, ","); for (i = 1; i <= n; i++) { x = a[i]; gsub("[[:space:]]", "", x); if (ip4 == "" && x ~ "^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+/32$") ip4 = x } }
END { flush() }'
AWG_WARP_TOOLS_DIR="/opt/warp-tools"
# Снимок контейнера на время перевыпуска и смены клиентов лежит на хосте (HOST_WGCF_BAK_ROOT/<контейнер>/container):
# goAmnezia пересоздаёт контейнеры, и снимок в /tmp контейнера, как до 2.5.2, пропадал вместе с ним.
AWG_LEGACY_SNAP="/tmp/warp-manager-rekey-backup"
AWG_MARKER_BEGIN="# --- WARP-MANAGER BEGIN ---"
AWG_MARKER_END="# --- WARP-MANAGER END ---"
# Вторая строка блока — его версия. Блок без неё (пропал или от версий до 2.5.2 с зашитыми адресами)
# автопроверка вставляет заново, заодно переводя выбор клиентов в ключи.
AWG_BLOCK_TAG="# gowarp-block 2: clients by key"
AWG_WATCH_BIN="/usr/local/bin/gowarp-awg-watch"
AWG_WATCH_SERVICE="/etc/systemd/system/gowarp-awg-watch.service"
HEALTH_SERVICE="/etc/systemd/system/gowarp-health.service"
HEALTH_INTERVAL=10          # локальные проверки
HEALTH_PROBE_EVERY=60       # сетевая проверка трафика; после провала — каждый тик
HEALTH_PROBE_FAILS_MAX=3    # провалов подряд до лечения
HEALTH_STALE_SEC=180        # WireGuard REJECT_AFTER_TIME: старше — сессия мертва
HEALTH_START_GRACE=30       # контейнер только что стартовал: туннель поднимает start.sh
HEALTH_BACKOFF_MAX=1800
HEALTH_PLUS_FREE_MAX=3      # WARP+ выбран, а трафик Free столько проверок подряд — повторить ключ
HEALTH_PLUS_BACKOFF_MIN=3600
HEALTH_PLUS_BACKOFF_MAX=86400
HEALTH_WARP_PORTS="2408 500 1701 4500"
HEALTH_RTT_WARN_MS=1000     # задержка через WARP выше этого столько проверок подряд — предупреждение
HEALTH_RTT_STREAK=3
HEALTH_KIND_BACKOFF_MIN=3600  # WARP на модуле amneziawg: повтор пересборки или скачивания инструментов
HEALTH_KIND_BACKOFF_MAX=86400
# v2.4: у каждого контейнера AmneziaWG своё устройство WARP (свой wgcf-аккаунт/ключ)
AWG_WGCF_BASE="/root/wgcf-containers"
WGCF_DIR="/root"

VALID_KEYS=(
    "WARP-PRO-2026-ALPHA"
    "WARP-PRO-2026-BETA"
    "WARP-PRO-2026-GAMMA"
)

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; MAGENTA='\033[0;35m'; WHITE='\033[1;37m'
BLUE='\033[0;34m'; DIM='\033[2m'; NC='\033[0m'

SOCKS_PORT=""
MY_IP=""
BOT_TOKEN=""
BOT_CHAT_ID=""
LICENSE_KEY=""
MODE=""
# Результат последней попытки применить WARP+ ключ (для сообщений в CLI/боте):
# ok | limited | free | device-limit | invalid | network | rejected | unverified
PLUS_LAST_REASON=""
PLUS_LAST_QUOTA=""

CONTAINER=""
CONTAINERS=""
AWG_VPN_CONF=""
AWG_VPN_IF=""
AWG_VPN_QUICK_CMD=""
AWG_CLIENTS_TABLE=""
AWG_START_SH=""
AWG_SUBNET=""
AWG_WARP_EXIT_IP=""
# Выбор: ключи (или адреса от прежних версий) и их адреса по текущему конфигу — для правил.
declare -a AWG_SELECTED_KEYS=()
declare -a AWG_SELECTED_IPS=()
# Клиенты из конфига сервера по порядку: адрес и публичный ключ.
declare -a AWG_CLIENT_IPS=()
declare -a AWG_CLIENT_KEYS=()
require_bash4() {
    if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
        echo "WARP Manager requires Bash 4.0 or newer. Current: ${BASH_VERSION:-unknown}" >&2
        exit 2
    fi
}

require_bash4
declare -A AWG_CLIENT_NAMES=()
declare -A AWG_TOOLS_FAILED_AT=()
declare -a AWG_ALL_CONTAINERS=()

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

init_config() {
    mkdir -p "$WARP_DIR"
    chmod 700 "$WARP_DIR" 2>/dev/null
    if [ ! -f "$WARP_CONF" ]; then
        cat > "$WARP_CONF" <<CONF
CONFIG_VERSION="${CONFIG_VERSION}"
SOCKS_PORT="40000"
BOT_TOKEN=""
BOT_CHAT_ID=""
BOT_USER_IDS=""
LICENSE_KEY=""
MODE=""
CONTAINER=""
CONTAINERS=""
WARP_KEY_MODE_3XUI="free"
WARP_KEY_MODE_AWG="free"
ACTIVE_KEY_NAME_3XUI=""
ACTIVE_KEY_NAME_AWG=""
INSTALLED_VERSION=""
WARP_WANTED_3XUI="on"
CONF
        chmod 600 "$WARP_CONF"
    fi
    if [ ! -f "$WARP_KEYS" ]; then
        : > "$WARP_KEYS"
        chmod 600 "$WARP_KEYS"
    fi
    source "$WARP_CONF"
    SOCKS_PORT="${SOCKS_PORT:-$DEFAULT_PORT}"

    migrate_config_if_needed && normalize_key_labels
}

migrate_config_if_needed() {
    # source config loads the installed version; never use it as target schema.
    CONFIG_VERSION="$CONFIG_SCHEMA_VERSION"
    local current_ver legacy_awg=0
    current_ver=$(grep -E '^CONFIG_VERSION=' "$WARP_CONF" 2>/dev/null | sed -E 's/^CONFIG_VERSION="?([^"]*)"?/\1/')
    grep -q '^WARP_KEY_MODE_AWG=' "$WARP_CONF" || legacy_awg=1

    # Если версия уже актуальная — выходим
    if [ "$current_ver" = "$CONFIG_VERSION" ]; then
        return 0
    fi

    # Бэкап перед миграцией
    local backup="${WARP_CONF}.v$(date +%s).bak"
    cp "$WARP_CONF" "$backup" 2>/dev/null
    chmod 600 "$backup" 2>/dev/null

    # Добавляем недостающие поля (идемпотентно)
    grep -q '^CONFIG_VERSION='         "$WARP_CONF" || echo "CONFIG_VERSION=\"${CONFIG_VERSION}\""    >> "$WARP_CONF"
    grep -q '^WARP_KEY_MODE_3XUI='     "$WARP_CONF" || echo "WARP_KEY_MODE_3XUI=\"free\""              >> "$WARP_CONF"
    grep -q '^WARP_KEY_MODE_AWG='      "$WARP_CONF" || echo "WARP_KEY_MODE_AWG=\"free\""               >> "$WARP_CONF"
    grep -q '^ACTIVE_KEY_NAME_3XUI='   "$WARP_CONF" || echo "ACTIVE_KEY_NAME_3XUI=\"\""                >> "$WARP_CONF"
    grep -q '^ACTIVE_KEY_NAME_AWG='    "$WARP_CONF" || echo "ACTIVE_KEY_NAME_AWG=\"\""                 >> "$WARP_CONF"
    # P0.6 fix: BOT_USER_IDS — опциональный allowlist user.id (через запятую)
    # для случая, когда бот используется в группе и chat_id один на всех.
    grep -q '^BOT_USER_IDS='           "$WARP_CONF" || echo "BOT_USER_IDS=\"\""                        >> "$WARP_CONF"
    # v4: версия скрипта, для которой уже выполнены post-upgrade hooks (см. run_upgrade_hooks)
    grep -q '^INSTALLED_VERSION='      "$WARP_CONF" || echo "INSTALLED_VERSION=\"\""                   >> "$WARP_CONF"
    # v5: намерение для 3X-UI. «off» ставит только ручное выключение; по умолчанию WARP включён,
    # поэтому уже упавшие у пользователей подключения автопроверка поднимет после обновления.
    grep -q '^WARP_WANTED_3XUI='      "$WARP_CONF" || echo "WARP_WANTED_3XUI=\"on\""                  >> "$WARP_CONF"

    # Обновляем CONFIG_VERSION на текущую (через sed — безопасно)
    if grep -q '^CONFIG_VERSION=' "$WARP_CONF"; then
        local tmpf; tmpf=$(mktemp)
        while IFS= read -r line; do
            if [[ "$line" == "CONFIG_VERSION="* ]]; then
                echo "CONFIG_VERSION=\"${CONFIG_VERSION}\""
            else
                echo "$line"
            fi
        done < "$WARP_CONF" > "$tmpf"
        mv "$tmpf" "$WARP_CONF"
    fi

    chmod 600 "$WARP_CONF"
    source "$WARP_CONF"
    log_action "MIGRATE: config v${current_ver:-unknown} -> v${CONFIG_VERSION}, backup=${backup}"

    # A newly added Free default does not mean the pre-2.3 account was Free.
    # Preserve its credentials and recover a verified paid binding when available.
    if [ "$legacy_awg" = 1 ]; then migrate_legacy_awg_key || return 1; fi

    echo -e "\n${CYAN}━━━ Конфигурация обновлена: v${current_ver:-старая} → v${CONFIG_VERSION} ━━━${NC}"
    echo -e "${WHITE}Все настройки сохранены (порт, бот, ключи WARP+). Бэкап: ${YELLOW}${backup}${NC}\n"
    sleep 1
}

migrate_legacy_awg_key() {
    [ -f "$WGCF_ACCOUNT" ] || return 0
    local key name base i=1 probe tier
    key=$(sed -nE "s/^license_key[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$WGCF_ACCOUNT" | head -1)
    is_valid_warp_plus_key "$key" || return 0
    name=$(awk -F'|' -v k="$key" '$2==k {print $1; exit}' "$WARP_KEYS")
    if [ -z "$name" ]; then
        base="imported-${key: -4}"; name="$base"
        while key_exists "$name"; do name="${base}-${i}"; i=$((i+1)); done
        add_key "$name" "$key" || return 1
    fi
    probe=$(cf_account_probe_wgcf 2>/dev/null) || probe=""
    tier="${probe%%|*}"
    case "$tier" in
        premium|unlimited|limited)
            save_config_val WARP_KEY_MODE_AWG plus && save_config_val ACTIVE_KEY_NAME_AWG "$name" || return 1
            log_action "MIGRATE: preserved legacy paid AWG account ($name)" ;;
        *) log_action "MIGRATE: preserved legacy AWG credentials; saved key $name for verification" ;;
    esac
}

# ═══════════════════════════════════════════════════════════════
#  SELF-INSTALL / UPGRADE — обновление без запинок
# ═══════════════════════════════════════════════════════════════
# Принципы:
#   • gowarp заменяется АТОМАРНО (tmp + mv): работающий бот-демон продолжает
#     читать старый inode и никогда не получает полфайла;
#   • конфиг мигрирует кумулятивно (любая старая версия → текущая) с бэкапом;
#   • после смены версии бот перезапускается сам, чтобы работать на новом коде;
#   • запуск через bash <(curl ...) (когда $0 = /dev/fd/N) больше не может
#     записать в gowarp обрезанный файл — такой запуск просто не копируется.
UPGRADED_FROM=""

# Печатает: installed | upgraded | same | skipped
self_install_gowarp() {
    local src; src=$(readlink -f "$0" 2>/dev/null || echo "$0")
    [ "$src" = "$GOWARP_BIN" ] && { echo "same"; return 0; }
    if [ ! -f "$src" ] || ! grep -q "^WARP_VERSION=\"${WARP_VERSION}\"" "$src" 2>/dev/null || ! bash -n "$src" 2>/dev/null; then
        echo "skipped"; return 1
    fi
    if [ -f "$GOWARP_BIN" ] && cmp -s "$src" "$GOWARP_BIN"; then
        echo "same"; return 0
    fi
    local status="installed"
    [ -f "$GOWARP_BIN" ] && status="upgraded"
    local tmp="${GOWARP_BIN}.new.$$"
    if cp -f "$src" "$tmp" && chmod 755 "$tmp" && mv -f "$tmp" "$GOWARP_BIN"; then
        echo "$status"; return 0
    fi
    rm -f "$tmp"
    echo "skipped"; return 1
}

# Разовые действия после смены версии скрипта. Вызывать ПОСЛЕ detect_mode.
# Список контейнеров через запятую: "a b " → "a, b".
awg_list() { local l="${1% }"; echo "${l// /, }"; }

# Контейнеры AmneziaWG при обновлении: довести прерванные откаты, перевести выбор клиентов на ключи, обновить
# автозапуск, поставить стандартные инструменты, пересобрать WARP с amneziawg или tun на wireguard и развести
# общий ключ от 2.3.
upgrade_awg_containers() {
    local CONTAINER="${CONTAINER:-}" c failed=0 fd rebuilt="" dupes out rc=0 try tools_ok kind
    local resumed="" resume_dead="" resume_failed="" drc=0
    # Контейнеров AmneziaWG нет совсем — обновлять нечего. Есть, но все остановлены, или не ответил Docker —
    # повторить при следующем запуске, иначе их автозапуск и выбор клиентов так и остались бы прежними.
    awg_discover_containers >/dev/null 2>&1 || drc=$?
    if [ "$drc" != 0 ]; then
        [ "$drc" = 1 ] || return 1
        drc=0; awg_discover_containers all >/dev/null 2>&1 || drc=$?
        [ "$drc" = 1 ]; return
    fi
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        if ! awg_resume_interrupted_rekey "$c"; then
            resume_failed+="${c} "
            log_action "UPGRADE: ${c} — прерванный перевыпуск завершить не удалось, данные восстановления сохранены"
        fi
        case "$AWG_RESUME_RESULT" in forward|back) resumed+="${c} " ;; dead) resume_dead+="${c} " ;; esac
    done
    awg_wgcf_ctx_reset
    [ -z "$resumed" ] || UPGRADE_NOTES+="Прерванный перевыпуск WARP завершён: $(awg_list "$resumed"). "
    [ -z "$resume_dead" ] || UPGRADE_NOTES+="WARP не проходит проверку после восстановления: $(awg_list "$resume_dead") — нажмите 3. "
    [ -z "$resume_failed" ] || UPGRADE_NOTES+="Перевыпуск WARP завершить не удалось: $(awg_list "$resume_failed") — нажмите 3. "
    exec {fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    flock -w 120 "$fd" || { exec {fd}>&-; return 1; }
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        CONTAINER="$c"
        docker exec "$c" test -f "$AWG_WARP_CONF" >/dev/null 2>&1 || continue
        awg_load_container_data || { failed=1; continue; }
        tools_ok=1
        awg_prepare_warp_tools || { tools_ok=0; echo "  ${c}: стандартные инструменты WireGuard не скачались, автопроверка повторит."; }
        # Выбор клиентов до 2.5.2 хранился адресами: переводим в ключи, пока адреса ещё совпадают с конфигом.
        awg_load_clients || { failed=1; continue; }
        if [ "$AWG_CLIENTS_MIGRATED" = 1 ]; then
            awg_save_clients || { failed=1; continue; }
            log_action "UPGRADE: ${c} — выбор клиентов WARP переведён с адресов на ключи"
        fi
        awg_patch_start_sh || { failed=1; continue; }
        kind=$(awg_warp_kind "$c")
        if [ "$tools_ok" = 1 ] && awg_kind_needs_rebuild "$kind" && docker exec "$c" test ! -f "$AWG_WARP_DIR/disabled"; then
            if ! awg_kernel_wireguard_ok "$c"; then
                log_action "UPGRADE: ${c} — WARP через $(awg_kind_text "$kind"), ядро не создаёт интерфейсы wireguard: оставлен как есть"
            else
                echo "  ${c}: WARP через $(awg_kind_text "$kind"), пересобираю на стандартном WireGuard…"
                if awg_warp_up restart >/dev/null 2>&1 && awg_load_clients && awg_apply_rules >/dev/null 2>&1 && [ "$AWG_WARP_KIND" = wireguard ]; then
                    rebuilt+="${c} "
                    log_action "UPGRADE: ${c} — WARP пересобран: ${kind} → wireguard"
                else
                    log_action "UPGRADE: ${c} — WARP не пересобран на wireguard (сейчас ${AWG_WARP_KIND:-нет}), автопроверка повторит"
                fi
            fi
        fi
    done
    exec {fd}>&-
    if [ "$failed" = 0 ]; then awg_install_host_watcher || failed=1; fi
    [ -z "$rebuilt" ] || UPGRADE_NOTES+="WARP переведён на стандартный WireGuard: $(awg_list "$rebuilt"). "
    # Общий ключ от 2.3: каждому повтору — своё устройство, около минуты на регистрацию.
    if [ "$failed" = 0 ] && awg_shared_key_detected; then
        dupes="${AWG_SHARED_KEY_DUPES[*]}"
        echo "  Контейнеры делят один ключ WARP: выдаю своё устройство (${dupes}), около минуты на контейнер…"
        for try in 1 2 3; do
            rc=0
            out=$(AWG_REKEY_ONLY="$dupes" rekey_awg_core | tee /dev/stderr) || rc=$?
            if [ "$rc" != 0 ] && [[ "$out" == *"Другая операция"* ]]; then sleep 15; continue; fi
            break
        done
        if [ "$rc" = 0 ]; then
            UPGRADE_NOTES+="Общий ключ разведён, своё устройство WARP выдано: $(awg_list "$dupes"). "
            log_action "UPGRADE: общий ключ WARP разведён (${dupes})"
        else
            UPGRADE_NOTES+="Общий ключ WARP развести не удалось, нажмите 3. "
            log_action "UPGRADE: общий ключ WARP не разведён (${dupes})"
        fi
    fi
    return "$failed"
}

run_upgrade_hooks() {
    local prev="${INSTALLED_VERSION:-}"
    # Самопроверка при каждом запуске: WARP стоит, а службы автопроверки нет — вернуть.
    # Остановлена (прерванное обновление останавливает её на время контейнеров) — запустить.
    if health_anything_installed; then
        if ! systemctl is-enabled "$(basename "$HEALTH_SERVICE")" >/dev/null 2>&1; then
            health_service_install >/dev/null 2>&1 || true
        elif ! systemctl is-active "$(basename "$HEALTH_SERVICE")" >/dev/null 2>&1; then
            systemctl start "$(basename "$HEALTH_SERVICE")" >/dev/null 2>&1 || true
        fi
    fi
    [ "$prev" = "$WARP_VERSION" ] && return 0
    UPGRADED_FROM="${prev:-2.3-}"
    log_action "UPGRADE: v${UPGRADED_FROM} -> v${WARP_VERSION}"
    # wgcf: на уже установленных серверах заменить прежнюю сборку закреплённой.
    if [ -e "$WGCF_BIN" ]; then
        awg_install_wgcf >/dev/null 2>&1 || log_action "UPGRADE: wgcf ${WGCF_VERSION} не установлен, осталась прежняя сборка"
    fi
    # Автопроверка: поставить службу, но перезапустить на новом коде только после контейнеров,
    # чтобы она не чинила их одновременно с обновлением.
    local health_unit awg_rc=0
    health_unit=$(basename "$HEALTH_SERVICE")
    UPGRADE_NOTES=""
    if health_anything_installed; then
        health_service_install >/dev/null 2>&1 || log_action "UPGRADE: служба gowarp-health не установлена"
        systemctl stop "$health_unit" >/dev/null 2>&1 || true
    fi
    # Refresh generated startup code as well as the host script; keep live tunnels up.
    if has_awg_mode; then upgrade_awg_containers || awg_rc=1; fi
    if health_anything_installed; then systemctl restart "$health_unit" >/dev/null 2>&1 || true; fi
    [ "$awg_rc" = 0 ] || { echo "Не удалось обновить автозапуск AmneziaWG. Повторите запуск gowarp после восстановления контейнеров."; return 1; }
    # Бот работает как отдельный процесс из /usr/local/bin/gowarp — перезапускаем,
    # чтобы клавиатура и обработчики были от новой версии.
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active warp-bot &>/dev/null; then
        if systemctl restart warp-bot 2>/dev/null; then
            log_action "UPGRADE: warp-bot restarted on v${WARP_VERSION}"
        else
            log_action "UPGRADE: warp-bot restart FAILED — see journalctl -u warp-bot"
        fi
    fi
    save_config_val "INSTALLED_VERSION" "$WARP_VERSION"
}

save_config_val() { save_config_values "$@"; }

# Related settings are one commit, so readers never see a new mode with an old key.
save_config_values() {
    [ "$#" -gt 0 ] && [ $(( $# % 2 )) -eq 0 ] || return 1
    local key value rc
    local -a write_keys=() write_values=()
    while [ "$#" -gt 0 ]; do
        key="$1"; value="$2"; shift 2
        [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || return 1
        [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || return 1
        value="${value//\\/\\\\}"
        value="${value//\"/\\\"}"
        value="${value//\$/\\\$}"
        value="${value//\`/\\\`}"
        write_keys+=("$key"); write_values+=("$value")
    done
    (
        flock -w 5 -x 9 2>/dev/null || exit 7
        local tmpf line i match
        local -a found=()
        tmpf=$(mktemp "${WARP_CONF}.XXXXXX") || exit 1
        trap 'rm -f "$tmpf"' EXIT
        {
            while IFS= read -r line || [ -n "$line" ]; do
                match=0
                for i in "${!write_keys[@]}"; do
                    if [[ "$line" == "${write_keys[$i]}="* ]]; then
                        printf '%s="%s"\n' "${write_keys[$i]}" "${write_values[$i]}" || exit 1
                        found[$i]=1; match=1; break
                    fi
                done
                if [ "$match" = 0 ]; then printf '%s\n' "$line" || exit 1; fi
            done < "$WARP_CONF"
            for i in "${!write_keys[@]}"; do
                if [ "${found[$i]:-0}" = 0 ]; then
                    printf '%s="%s"\n' "${write_keys[$i]}" "${write_values[$i]}" || exit 1
                fi
            done
        } > "$tmpf" || exit 1
        chmod 600 "$tmpf" && mv "$tmpf" "$WARP_CONF" || exit 1
    ) 9>"$WARP_DIR/.config.lock"
    rc=$?
    [ $rc -ne 0 ] && return $rc
    source "$WARP_CONF"
}

# ═══════════════════════════════════════════════════════════════
#  MODE DETECTION
# ═══════════════════════════════════════════════════════════════

has_3xui_mode() { [[ "$MODE" == "3xui" || "$MODE" == "both" ]]; }
has_awg_mode()  { [[ "$MODE" == "amnezia" || "$MODE" == "both" ]]; }

detect_mode() {
    source "$WARP_CONF" 2>/dev/null
    if [ -n "${MODE:-}" ] && [[ "$MODE" == "3xui" || "$MODE" == "amnezia" || "$MODE" == "both" ]]; then
        return 0
    fi

    local has_docker=0 has_amnezia=0 has_3xui=0
    command -v docker &>/dev/null && has_docker=1
    if [ "$has_docker" -eq 1 ]; then
        local awg_ct
        awg_ct=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^amnezia-awg2$|^amnezia-awg$' | head -1)
        # P0.1 fix: вместо широкого grep -i "amnezia" — анкорированный whitelist
        [ -z "$awg_ct" ] && awg_ct=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^amnezia-awg[0-9]*$' | head -1)
        [ -n "$awg_ct" ] && has_amnezia=1
    fi
    systemctl is-active x-ui &>/dev/null 2>&1 && has_3xui=1
    [ "$has_3xui" -eq 0 ] && command -v x-ui &>/dev/null && has_3xui=1

    if [ "$has_amnezia" -eq 1 ] && [ "$has_3xui" -eq 1 ]; then
        MODE="both"
    elif [ "$has_amnezia" -eq 1 ]; then
        MODE="amnezia"
    elif [ "$has_3xui" -eq 1 ]; then
        MODE="3xui"
    else
        echo -e "\n${YELLOW}Не обнаружено ни 3X-UI, ни AmneziaWG Docker.${NC}"
        echo -e "  ${GREEN}1)${NC} 3X-UI (SOCKS5-прокси для Xray)"
        echo -e "  ${GREEN}2)${NC} AmneziaWG (Docker WireGuard)"
        echo -e "  ${GREEN}3)${NC} Оба режима"
        while true; do
            read -p "Выберите режим (1/2/3): " choice
            case "$choice" in
                1) MODE="3xui"; break ;;
                2) MODE="amnezia"; break ;;
                3) MODE="both"; break ;;
            esac
        done
    fi

    save_config_val "MODE" "$MODE"
}

# ═══════════════════════════════════════════════════════════════
#  LICENSE
# ═══════════════════════════════════════════════════════════════

is_valid_key() {
    local k="$1"
    for vk in "${VALID_KEYS[@]}"; do [ "$k" = "$vk" ] && return 0; done
    return 1
}

check_license() {
    local arg_key="${1:-}"
    source "$WARP_CONF" 2>/dev/null
    if [ -n "${LICENSE_KEY:-}" ] && is_valid_key "$LICENSE_KEY"; then return 0; fi
    if [ -n "$arg_key" ] && is_valid_key "$arg_key"; then
        save_config_val "LICENSE_KEY" "$arg_key"; LICENSE_KEY="$arg_key"; return 0
    fi
    if [ "$(readlink -f "$0" 2>/dev/null)" = "/usr/local/bin/gowarp" ] || [ -f "$WARP_CONF" ]; then
        return 0
    fi
    echo -e "\n${RED}**********************************************************${NC}"
    echo -e "${RED}  ЛИЦЕНЗИОННЫЙ КЛЮЧ НЕДЕЙСТВИТЕЛЕН${NC}"
    echo -e "${RED}**********************************************************${NC}"
    echo ""
    [ -n "$arg_key" ] && echo -e "  ${WHITE}Ключ ${YELLOW}${arg_key}${WHITE} не найден.${NC}" \
        || echo -e "  ${WHITE}Для установки требуется лицензионный ключ.${NC}"
    echo -e "\n  ${CYAN}Использование:${NC}\n  ${WHITE}bash <(curl -sL ...) ${GREEN}ВАШ_КЛЮЧ${NC}"
    echo -e "\n  ${WHITE}Получить ключ: ${CYAN}https://www.youtube.com/@antenkaru${NC}\n"
    exit 1
}

# ═══════════════════════════════════════════════════════════════
#  WARP+ KEYS STORAGE & APPLICATION
# ═══════════════════════════════════════════════════════════════
# Формат файла keys.list: name|key|added_at (строка на ключ)
# Один и тот же ключ может быть выбран как активный отдельно для
# 3X-UI режима (warp-cli) и для AmneziaWG режима (wgcf) — храним
# два указателя ACTIVE_KEY_NAME_3XUI и ACTIVE_KEY_NAME_AWG.

mask_key() {
    local k="$1"
    [ -z "$k" ] && { echo ""; return; }
    local last="${k: -4}"
    echo "***-***-***-${last}"
}

is_valid_warp_plus_key() {
    local k="$1"
    if [[ "$k" =~ ^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$ ]]; then return 0; fi
    if [[ "$k" =~ ^[A-Za-z0-9]{8}-[A-Za-z0-9]{8}-[A-Za-z0-9]{8}$ ]]; then return 0; fi
    return 1
}

normalize_key_labels() {
    local n key added label suffix tmp
    [ -f "$WARP_KEYS" ] || return 0
    local fd
    exec {fd}>"$WARP_DIR/.keys.lock" || return 1
    flock -w 5 "$fd" || { exec {fd}>&-; return 1; }
    tmp=$(mktemp -p "$WARP_DIR") || { exec {fd}>&-; return 1; }
    while IFS='|' read -r n key added; do
        [ -n "$n" ] || continue
        label="$n"
        if is_valid_warp_plus_key "$n"; then
            label="key-${key: -4}"; suffix=2
            while key_exists "$label" || awk -F'|' -v n="$label" '$1==n{f=1} END{exit !f}' "$tmp"; do
                label="key-${key: -4}-$suffix"; suffix=$((suffix+1))
            done
            if [ "$ACTIVE_KEY_NAME_3XUI" = "$n" ]; then save_config_val ACTIVE_KEY_NAME_3XUI "$label" || break; fi
            if [ "$ACTIVE_KEY_NAME_AWG" = "$n" ]; then save_config_val ACTIVE_KEY_NAME_AWG "$label" || break; fi
        fi
        printf '%s|%s|%s\n' "$label" "$key" "$added" >> "$tmp"
    done < "$WARP_KEYS"
    if [ "$(wc -l < "$tmp")" = "$(wc -l < "$WARP_KEYS")" ]; then
        chmod 600 "$tmp" && mv "$tmp" "$WARP_KEYS"
    else rm -f "$tmp"; exec {fd}>&-; return 1; fi
    exec {fd}>&-
}

keys_count() {
    [ -f "$WARP_KEYS" ] || { echo 0; return; }
    local n
    n=$(grep -cE '.+\|.+' "$WARP_KEYS" 2>/dev/null)
    echo "${n:-0}"
}

keys_list_names() {
    [ -f "$WARP_KEYS" ] || return
    awk -F'|' 'NF>=2 {print $1}' "$WARP_KEYS"
}

get_key_by_name() {
    local name="$1"
    [ -f "$WARP_KEYS" ] || return
    awk -F'|' -v n="$name" '$1==n {print $2; exit}' "$WARP_KEYS"
}

key_exists() {
    local name="$1"
    [ -n "$(get_key_by_name "$name")" ]
}

add_key() {
    local name="$1" key="$2"
    [[ "$name" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || return 3
    [[ -n "$key" && "$key" != *"|"* && "$key" != *$'\n'* && "$key" != *$'\r'* ]] || return 4
    (
        flock -w 5 -x 9 2>/dev/null || exit 5
        if awk -F'|' -v n="$name" '$1==n {found=1; exit} END{exit found ? 0 : 1}' "$WARP_KEYS" 2>/dev/null; then
            exit 2
        fi
        local tmp
        tmp=$(mktemp "${WARP_KEYS}.XXXXXX") || exit 1
        trap 'rm -f "$tmp"' EXIT
        cat "$WARP_KEYS" > "$tmp" || exit 1
        printf '%s|%s|%s\n' "$name" "$key" "$(date '+%Y-%m-%d %H:%M:%S')" >> "$tmp" || exit 1
        chmod 600 "$tmp" && mv "$tmp" "$WARP_KEYS" || exit 1
    ) 9>"$WARP_DIR/.keys.lock"
}

delete_key() {
    local name="$1" rc=0 was_awg_key="${ACTIVE_KEY_NAME_AWG:-}"
    [[ "$name" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || return 1
    [ -f "$WARP_KEYS" ] || return 1
    (
        flock -w 5 -x 9 2>/dev/null || exit 6
        source "$WARP_CONF" || exit 1
        local tmp old_3mode="$WARP_KEY_MODE_3XUI" old_3key="$ACTIVE_KEY_NAME_3XUI"
        local old_amode="$WARP_KEY_MODE_AWG" old_akey="$ACTIVE_KEY_NAME_AWG"
        local -a changes=()
        tmp=$(mktemp "${WARP_KEYS}.XXXXXX") || exit 1
        trap 'rm -f "$tmp"' EXIT
        awk -F'|' -v n="$name" '$1!=n' "$WARP_KEYS" > "$tmp" && chmod 600 "$tmp" || exit 1
        if [ "$old_3key" = "$name" ]; then changes+=(WARP_KEY_MODE_3XUI free ACTIVE_KEY_NAME_3XUI ""); fi
        if [ "$old_akey" = "$name" ]; then changes+=(WARP_KEY_MODE_AWG free ACTIVE_KEY_NAME_AWG ""); fi
        if [ "${#changes[@]}" -gt 0 ]; then save_config_values "${changes[@]}" || exit 1; fi
        if ! mv "$tmp" "$WARP_KEYS"; then
            if [ "${#changes[@]}" -gt 0 ]; then
                save_config_values WARP_KEY_MODE_3XUI "$old_3mode" ACTIVE_KEY_NAME_3XUI "$old_3key" \
                    WARP_KEY_MODE_AWG "$old_amode" ACTIVE_KEY_NAME_AWG "$old_akey" || \
                    log_action "KEY DELETE: config restore failed; saved key retained"
            fi
            exit 1
        fi
    ) 9>"$WARP_DIR/.keys.lock" || rc=$?
    source "$WARP_CONF" || return 1
    [ "$rc" = 0 ] || { log_action "KEY DELETE: storage update failed"; return "$rc"; }
    # Runtime registrations stay intact; only the default for future rekeys changes.
    if [ "$was_awg_key" = "$name" ]; then reset_wgcf_account_license || return 1; fi
    log_action "KEY DELETE: saved key removed; active defaults cleared where applicable"
    return 0
}

# Применение WARP+ ключа в режиме 3X-UI (warp-cli)
# ВАЖНО: warp-cli принимает ключ только аргументом командной строки → ключ
# на короткое время виден в `ps`. Это известное ограничение warp-cli, обойти
# без патчинга нельзя. Минимизируем окно: на VPS с одним root-юзером риск низок.
#
# P0.3 fix: при любой ошибке — откат WARP_KEY_MODE_3XUI в free.
# P0.4 fix: используем `registration show` (надёжно отдаёт Account type)
#          и явно reject если Account type = Free.
apply_warp_plus_key_3xui() {
    [ "$WARP_KEY_MODE_3XUI" != "plus" ] && return 0
    if [ -z "$ACTIVE_KEY_NAME_3XUI" ]; then
        log_action "3XUI WARP+ APPLY: plus mode without active key — rollback to free"
        save_config_val "WARP_KEY_MODE_3XUI" "free"
        save_config_val "ACTIVE_KEY_NAME_3XUI" ""
        return 1
    fi
    if ! command -v warp-cli &>/dev/null; then
        log_action "3XUI WARP+ APPLY: warp-cli not installed"
        save_config_val "WARP_KEY_MODE_3XUI" "free"
        save_config_val "ACTIVE_KEY_NAME_3XUI" ""
        return 1
    fi
    local plus_key
    plus_key=$(get_key_by_name "$ACTIVE_KEY_NAME_3XUI")
    if [ -z "$plus_key" ]; then
        # P2 UX fix (self-audit live VPS): сохраняем имя ДО save_config_val,
        # потому что save_config_val делает source и зануляет $ACTIVE_KEY_NAME_3XUI
        local _missing_name="$ACTIVE_KEY_NAME_3XUI"
        log_action "3XUI WARP+ APPLY: active key '$_missing_name' not in keys.list — fallback to free"
        save_config_val "WARP_KEY_MODE_3XUI" "free"
        save_config_val "ACTIVE_KEY_NAME_3XUI" ""
        echo -e "  ${YELLOW}⚠ Ключ '$_missing_name' не найден в keys.list — переключаюсь на Free${NC}" >&2
        return 1
    fi
    PLUS_LAST_REASON=""; PLUS_LAST_QUOTA=""
    # stderr — в файл 0600: warp-cli может отразить ключ в тексте ошибки, поэтому
    # читаем его только grep'ом (классификация), никогда не печатаем.
    local errf; errf=$(mktemp -p "$WARP_DIR")
    chmod 600 "$errf"
    warp-cli --accept-tos registration license "$plus_key" > /dev/null 2>"$errf"
    local rc=$?
    # SECURITY (Claude 015 finding, live VPS verified): Cloudflare warp-svc
    # пишет plaintext ключ в /var/log/cloudflare-warp/*.txt и через journald
    # в syslog. Срабатывает помещение в его daemon log сразу после команды.
    # Scrub immediately — пока ключ не утёк в backup.
    cloudflare_log_scrub
    if [ $rc -ne 0 ]; then
        PLUS_LAST_REASON=$(_classify_license_error "$errf")
        rm -f "$errf"
        log_action "3XUI WARP+ APPLY: warp-cli returned $rc (${PLUS_LAST_REASON}) for key '$(mask_key "$plus_key")' — rollback to free"
        # NEW P1 fix (self-audit): чистим и активный, чтобы не накапливался стейл-указатель
        save_config_val "WARP_KEY_MODE_3XUI" "free"
        save_config_val "ACTIVE_KEY_NAME_3XUI" ""
        plus_explain "$PLUS_LAST_REASON" "3X-UI"
        return $rc
    fi
    rm -f "$errf"
    # P0.4: registration show — надёжнее, чем `account`. Выводит "Account type: Free/Limited/Unlimited"
    local show_out acct_type
    show_out=$(warp-cli --accept-tos registration show 2>/dev/null)
    acct_type=$(_parse_account_type "$show_out")
    case "$acct_type" in
        premium|"warp+"|unlimited)
            PLUS_LAST_REASON="ok"
            log_action "3XUI WARP+ APPLY: key '$(mask_key "$plus_key")' (name=$ACTIVE_KEY_NAME_3XUI) applied, verified Account type=${acct_type}"
            return 0
            ;;
        limited)
            PLUS_LAST_REASON="limited"
            log_action "3XUI WARP+ APPLY: key '$(mask_key "$plus_key")' (name=$ACTIVE_KEY_NAME_3XUI) applied, Account type=limited (referral quota)"
            return 0
            ;;
        free)
            PLUS_LAST_REASON="free"
            log_action "3XUI WARP+ APPLY: key accepted but Account type=Free — no active WARP+ subscription on this account, rollback to free"
            save_config_val "WARP_KEY_MODE_3XUI" "free"
            save_config_val "ACTIVE_KEY_NAME_3XUI" ""
            plus_explain "free" "3X-UI"
            return 2
            ;;
        "")
            # registration show ничего не показал (старая версия warp-cli или ещё не connected).
            # Делаем fallback на `account` команду.
            local acct_legacy
            acct_legacy=$(warp-cli --accept-tos account 2>/dev/null)
            if echo "$acct_legacy" | grep -qiE 'unlimited|warp\+|premium|limited'; then
                PLUS_LAST_REASON="ok"
                log_action "3XUI WARP+ APPLY: verified via legacy 'account' command for key '$(mask_key "$plus_key")'"
                return 0
            fi
            PLUS_LAST_REASON="unverified"
            log_action "3XUI WARP+ APPLY: cannot verify Plus status (both 'registration show' and 'account' returned ambiguous output) — rollback to free for safety"
            save_config_val "WARP_KEY_MODE_3XUI" "free"
            save_config_val "ACTIVE_KEY_NAME_3XUI" ""
            plus_explain "unverified" "3X-UI"
            return 3
            ;;
        *)
            PLUS_LAST_REASON="unverified"
            plus_explain "unverified" "3X-UI"
            return 3
            ;;
    esac
}

# Применение WARP+ ключа в режиме AmneziaWG (wgcf)
# Должно вызываться ПОСЛЕ awg_ensure_account и ДО awg_generate_profile.
# TOML: используем двойные кавычки (канонический формат BurntSushi/toml,
# который использует wgcf).
reset_wgcf_account_license() {
    [ -f "$WGCF_ACCOUNT" ] || return 0
    local tmpf
    tmpf=$(mktemp -p "$WARP_DIR") || return 1
    if ! awk '!/^[[:space:]]*license_key[[:space:]]*=/' "$WGCF_ACCOUNT" > "$tmpf"; then
        rm -f "$tmpf"; return 1
    fi
    chmod 600 "$tmpf" && mv "$tmpf" "$WGCF_ACCOUNT"
}

# SECURITY: Cloudflare warp-svc daemon пишет plaintext WARP+ ключ в свои логи
# и через journald в /var/log/syslog. Это observed behavior on real VPS test
# (Claude 015 evidence). Защитная мера: после каждого warp-cli registration
# license truncate Cloudflare-specific логи и зачистить journald буфер.
# Не трогаем /var/log/syslog (destructive для других сервисов). Для production
# юзеру стоит настроить rsyslog filter для warp-svc (документировано в README).
cloudflare_log_scrub() {
    [ -d /var/log/cloudflare-warp ] || return 0
    find /var/log/cloudflare-warp -type f -exec truncate -s 0 {} \; 2>/dev/null
    chmod 700 /var/log/cloudflare-warp 2>/dev/null
    chmod 600 /var/log/cloudflare-warp/*.txt 2>/dev/null
    if command -v journalctl >/dev/null 2>&1; then
        journalctl --rotate >/dev/null 2>&1 || true
    fi
    log_action "SECURITY: cloudflare-warp logs scrubbed after license apply"
}

# ═══════════════════════════════════════════════════════════════
# WARP+ ACCOUNT DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════
# Cloudflare отвечает на ключ тремя разными состояниями, и их нельзя смешивать:
#   • ключ ОТВЕРГНУТ (rc≠0): лимит 5 устройств (код 1056), опечатка, сеть;
#   • ключ ПРИНЯТ, но аккаунт за ним Free — подписки WARP+ нет или истекла
#     (account_type=free, quota=0). Это НЕ «невалидный ключ»;
#   • ключ ПРИНЯТ и аккаунт limited (квота по рефералам) / unlimited (подписка).

CF_API_BASE="https://api.cloudflareclient.com/v0a1922"

# Достаёт тип аккаунта из вывода `warp-cli registration show` ("Account type: Free")
# или `wgcf status` ("Account type : free"). Печатает lowercase: free|limited|unlimited|…
# Пустая строка = поле не найдено. Не печатает всю строку при несовпадении.
_parse_account_type() {
    local text="$1"
    printf '%s\n' "$text" \
        | sed -nE 's/^[[:space:]]*[Aa]ccount[[:space:]]+[Tt]ype[[:space:]]*:[[:space:]]*([A-Za-z+]+).*/\1/p' \
        | head -1 | tr '[:upper:]' '[:lower:]'
}

# Прямой запрос к API Cloudflare по данным wgcf-account.toml.
# Печатает "account_type|quota_bytes|premium_bytes" или ничего (rc=1) при ошибке.
cf_account_probe_wgcf() {
    [ -f "$WGCF_ACCOUNT" ] || return 1
    local dev tok
    dev=$(sed -nE "s/^device_id[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$WGCF_ACCOUNT" | head -1)
    tok=$(sed -nE "s/^access_token[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$WGCF_ACCOUNT" | head -1)
    [ -n "$dev" ] && [ -n "$tok" ] || return 1
    local resp
    resp=$(curl -s --max-time 8 "${CF_API_BASE}/reg/${dev}/account" \
        -H "Authorization: Bearer ${tok}" -H "User-Agent: okhttp/3.12.1" \
        -H "CF-Client-Version: a-6.30-3596" 2>/dev/null) || return 1
    local out
    out=$(printf '%s' "$resp" | jq -r 'select(type=="object" and .account_type != null) | "\(.account_type)|\(.quota // 0)|\(.premium_data // 0)"' 2>/dev/null)
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
}

# Классификация ошибки применения ключа по файлу со stderr. Сам stderr
# НЕ печатаем — внешний бинарь может отразить в нём ключ.
# Печатает: device-limit | invalid | network | rejected
_classify_license_error() {
    local f="$1"
    [ -f "$f" ] || { echo "rejected"; return; }
    if grep -qiE '1056|too many (connected )?devices' "$f"; then echo "device-limit"
    elif grep -qiE 'invalid (license|key)|unknown (license|key)|license.*not found|key.*not found' "$f"; then echo "invalid"
    elif grep -qiE 'timed? ?out|connection refused|could not resolve|no route|network is unreachable|tls' "$f"; then echo "network"
    elif grep -qiE '400[[:space:]]+Bad Request' "$f"; then echo "bad-request"
    else echo "rejected"; fi
}

# Человеческое объяснение результата (plain text, без цветов — используется и в боте).
# $1 = причина, $2 = подсистема (3X-UI / AmneziaWG), $3 = квота в байтах (для limited)
plus_reason_text() {
    local reason="$1" sys="${2:-WARP}" quota="${3:-0}"
    case "$reason" in
        ok)
            echo "Cloudflare подтвердил платный тип аккаунта. Прохождение трафика проверяется отдельно." ;;
        limited)
            local gb; gb=$(LC_ALL=C awk -v q="$quota" 'BEGIN{printf "%.1f", q/1024/1024/1024}')
            echo "WARP+ с ограниченной квотой: осталось ${gb} ГБ (бонус за рефералов, не подписка)."
            echo "Когда квота кончится, ${sys} автоматически станет Free — тогда оформите подписку в приложении." ;;
        free)
            echo "Cloudflare принял ключ, но сейчас сообщает тип аккаунта Free."
            echo "Проверьте состояние подписки в приложении 1.1.1.1:"
            echo "  Настройки (Settings) → Аккаунт (Account) → должна быть активна подписка «WARP+ Unlimited»."
            echo "Если подписка истекла — продлите её. Ключ менять не нужно: после продления снова"
            echo "повторите применение ключа. Фактический режим ${sys} смотрите в диагностике." ;;
        device-limit)
            echo "Cloudflare отверг ключ: превышен лимит 5 устройств на один аккаунт WARP+ (код 1056)."
            echo "Удалите лишние устройства в приложении 1.1.1.1 (Настройки → Аккаунт → Устройства)"
            echo "После освобождения места повторите применение ключа." ;;
        invalid)
            echo "Cloudflare не узнал такой ключ. Проверьте, что ключ скопирован целиком и без пробелов:"
            echo "формат XXXXXXXX-XXXXXXXX-XXXXXXXX (старый формат XXXX-XXXX-XXXX-XXXX)." ;;
        network)
            echo "Не удалось связаться с Cloudflare (сеть/DNS/таймаут). Ключ не проверен — повторите позже." ;;
        bad-request)
            echo "Cloudflare отклонил привязку нового устройства (HTTP 400). Проверьте ключ и свободные места в аккаунте." ;;
        unverified)
            echo "Ключ принят, но подтвердить трафик WARP+ не удалось. Повторите проверку позже." ;;
        *)
            echo "Cloudflare отверг ключ (${reason}). Изменение не подтверждено." ;;
    esac
}

# Цветная обёртка для CLI (в stderr)
plus_explain() {
    local reason="$1" sys="${2:-WARP}" quota="${3:-0}" color="$YELLOW" line
    case "$reason" in ok|limited) color="$GREEN" ;; esac
    while IFS= read -r line; do
        echo -e "  ${color}${line}${NC}" >&2
    done < <(plus_reason_text "$reason" "$sys" "$quota")
}

# ═══════════════════════════════════════════════════════════════
# WARP Plus VERIFICATION via Cloudflare cdn-cgi/trace endpoint
# ═══════════════════════════════════════════════════════════════
# Cloudflare возвращает в trace поле `warp=plus|on|off`:
#   warp=plus → Plus подписка активна
#   warp=on   → WARP включён, но Free
#   warp=off  → запрос пришёл не через WARP
# Это лёгкий (< 1KB) GET запрос — не требует speedtest.
#
# Возвращает:
#   0 + "plus"  → подтверждённый Plus
#   0 + "free"  → подтверждённый Free
#   1 + ""      → не удалось проверить (сетевая ошибка / timeout)

_parse_warp_trace_status() {
    local body="$1"
    [ -z "$body" ] && { echo ""; return 1; }
    local val
    val=$(echo "$body" | grep -E '^warp=' | head -1 | cut -d= -f2 | tr -d '\r\n')
    case "$val" in
        plus)  echo "plus"; return 0 ;;
        on)    echo "free"; return 0 ;;
        off)   echo "off";  return 0 ;;
        *)     echo "";     return 1 ;;
    esac
}

# Проверяет 3X-UI режим через SOCKS5 прокси
# Stdout: plus|free|off|""  rc: 0 если parsed
verify_warp_plus_3xui() {
    local body
    body=$(curl -s --max-time 5 \
        --proxy "socks5h://127.0.0.1:${SOCKS_PORT}" \
        https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null)
    _parse_warp_trace_status "$body"
}

# Проверяет AWG контейнер через docker exec + curl через warp интерфейс
# Аргумент: имя контейнера. Stdout: plus|free|off|""  rc: 0 если parsed
verify_warp_plus_awg_container() {
    local c="$1"
    [ -z "$c" ] && { echo ""; return 1; }
    # curl --interface warp заставляет идти через WG интерфейс
    local body
    body=$(docker exec "$c" sh -c '
        curl -4 -s --connect-timeout 15 --max-time 20 --interface warp https://1.1.1.1/cdn-cgi/trace 2>/dev/null
    ' 2>/dev/null)
    _parse_warp_trace_status "$body"
}

apply_warp_plus_key_awg() {
    [ "$WARP_KEY_MODE_AWG" != "plus" ] && return 0
    if [ -z "$ACTIVE_KEY_NAME_AWG" ]; then
        log_action "AWG WARP+ APPLY: plus mode without active key — rollback to free"
        save_config_val "WARP_KEY_MODE_AWG" "free"
        save_config_val "ACTIVE_KEY_NAME_AWG" ""
        reset_wgcf_account_license
        return 1
    fi
    local plus_key
    plus_key=$(get_key_by_name "$ACTIVE_KEY_NAME_AWG")
    if [ -z "$plus_key" ]; then
        # P2 UX fix (self-audit live VPS): сохраняем имя ДО save_config_val
        local _missing_name="$ACTIVE_KEY_NAME_AWG"
        log_action "AWG WARP+ APPLY: active key '$_missing_name' not in keys.list — fallback to free"
        save_config_val "WARP_KEY_MODE_AWG" "free"
        save_config_val "ACTIVE_KEY_NAME_AWG" ""
        reset_wgcf_account_license
        echo -e "  ${YELLOW}⚠ Ключ '$_missing_name' не найден в keys.list — переключаюсь на Free${NC}" >&2
        return 1
    fi
    if [ ! -f "$WGCF_ACCOUNT" ]; then
        # P1.1 (Codex 004): rollback также при pre-apply failures
        # NEW P1: симметрично чистим и active name
        log_action "AWG WARP+ APPLY: $WGCF_ACCOUNT missing — rollback to free"
        save_config_val "WARP_KEY_MODE_AWG" "free"
        save_config_val "ACTIVE_KEY_NAME_AWG" ""
        echo -e "  ${YELLOW}⚠ wgcf-account.toml отсутствует — откат на Free${NC}" >&2
        return 1
    fi
    local wgcf_cmd="$WGCF_BIN"
    if [ ! -x "$wgcf_cmd" ]; then
        wgcf_cmd=$(command -v wgcf 2>/dev/null || true)
    fi
    if [ -z "$wgcf_cmd" ] || [ ! -x "$wgcf_cmd" ]; then
        log_action "AWG WARP+ APPLY: wgcf binary missing — rollback to free"
        save_config_val "WARP_KEY_MODE_AWG" "free"
        save_config_val "ACTIVE_KEY_NAME_AWG" ""
        echo -e "  ${YELLOW}⚠ wgcf не установлен — откат на Free${NC}" >&2
        return 1
    fi

    # ⭐ FIX (Claude live VPS test 2026-05-18 with fresh key — verified warp=plus):
    # Правильный путь — `wgcf update --license-key`, не sed+update.
    # Из wgcf --help: "If a new/different license key is provided, the current device
    # will be bound to the new key and its parent account."
    # Старый sed+update silently no-op'ил tier upgrade на Cloudflare-side
    # (rc=0 без реального изменения). Новый flag даёт явный rc=1 на rejection
    # (например error 1056 "Too many connected devices") → мы можем rollback.
    #
    # stderr скрываем — внешний бинарь может отразить секрет в error messages.
    PLUS_LAST_REASON=""; PLUS_LAST_QUOTA=""
    local errf; errf=$(mktemp -p "$WARP_DIR")
    chmod 600 "$errf"
    (cd "$WGCF_DIR" && "$wgcf_cmd" update --license-key "$plus_key" > /dev/null 2>"$errf")
    local rc=$?
    if [ $rc -ne 0 ]; then
        PLUS_LAST_REASON=$(_classify_license_error "$errf")
        rm -f "$errf"
        log_action "AWG WARP+ APPLY: wgcf update --license-key rc=$rc (${PLUS_LAST_REASON}) — rollback to free"
        reset_wgcf_account_license
        save_config_val "WARP_KEY_MODE_AWG" "free"
        save_config_val "ACTIVE_KEY_NAME_AWG" ""
        plus_explain "$PLUS_LAST_REASON" "AmneziaWG"
        return $rc
    fi
    rm -f "$errf"

    # Verify: сначала прямой запрос к API Cloudflare (точный account_type + квота),
    # затем `wgcf status` (печатает "Account type : free" — с пробелом перед двоеточием),
    # затем toml. Старый парсер требовал "type:" без пробела и на реальном выводе wgcf
    # молча уходил в ветку «unknown → assume Plus».
    local status_out acct_type probe
    probe=$(cf_account_probe_wgcf 2>/dev/null || true)
    if [ -n "$probe" ]; then
        acct_type=$(printf '%s' "$probe" | cut -d'|' -f1 | tr '[:upper:]' '[:lower:]')
        PLUS_LAST_QUOTA=$(printf '%s' "$probe" | cut -d'|' -f2)
    fi
    if [ -z "$acct_type" ]; then
        status_out=$( (cd "$WGCF_DIR" && "$wgcf_cmd" status 2>/dev/null) )
        acct_type=$(_parse_account_type "$status_out")
    fi
    if [ -z "$acct_type" ]; then
        acct_type=$(grep -E '^account_type[[:space:]]*=' "$WGCF_ACCOUNT" 2>/dev/null | sed -E "s/.*=[[:space:]]*['\"]([^'\"]*)['\"].*/\1/" | tr '[:upper:]' '[:lower:]')
    fi

    case "$acct_type" in
        premium|unlimited)
            PLUS_LAST_REASON="ok"
            log_action "AWG WARP+ APPLY: key '$(mask_key "$plus_key")' applied, account_type=${acct_type}"
            return 0
            ;;
        limited)
            PLUS_LAST_REASON="limited"
            log_action "AWG WARP+ APPLY: key '$(mask_key "$plus_key")' applied, account_type=limited quota=${PLUS_LAST_QUOTA:-?}"
            return 0
            ;;
        free)
            PLUS_LAST_REASON="free"
            log_action "AWG WARP+ APPLY: wgcf update ok but account_type=free (quota=${PLUS_LAST_QUOTA:-0}) — no active WARP+ subscription on this account, rollback to free"
            reset_wgcf_account_license
            save_config_val "WARP_KEY_MODE_AWG" "free"
            save_config_val "ACTIVE_KEY_NAME_AWG" ""
            plus_explain "free" "AmneziaWG"
            return 2
            ;;
        *)
            PLUS_LAST_REASON="unverified"
            plus_explain "unverified" "AmneziaWG"
            return 3
            ;;
    esac
}

# Сводные текстовые описания режима (для UI)
get_mode_text_3xui() {
    if [ "$WARP_KEY_MODE_3XUI" = "plus" ]; then
        [ -n "$ACTIVE_KEY_NAME_3XUI" ] && echo "WARP+ (${ACTIVE_KEY_NAME_3XUI})" || echo "WARP+ (ключ не выбран!)"
    else
        echo "Free"
    fi
}

get_mode_text_awg() {
    if [ "$WARP_KEY_MODE_AWG" = "plus" ]; then
        [ -n "$ACTIVE_KEY_NAME_AWG" ] && echo "WARP+ (${ACTIVE_KEY_NAME_AWG})" || echo "WARP+ (ключ не выбран!)"
    else
        echo "Free"
    fi
}

# ─── Информационный экран про покупку WARP+ ───────────────────

show_warp_plus_info() {
    echo ""
    echo -e "${CYAN}━━━ Ключ WARP+ ━━━${NC}"
    echo "  В приложении 1.1.1.1 откройте настройки аккаунта и скопируйте License Key."
    echo "  Проверьте, что подписка WARP+ активна. Один ключ без подписки не включает Plus."
    echo "  В меню «WARP+ ключ» выберите «Включить WARP+» и вставьте ключ целиком."
    echo "  В Telegram используйте «Добавить ключ», затем кнопку применения."
    echo "  Если поле ключа недоступно, проверьте справку или поддержку Cloudflare."
    echo ""
}

# ─── CLI: удаление сохранённого ключа ─────────────────────────
cli_delete_key() {
    local names=()
    while IFS= read -r n; do [ -n "$n" ] && names+=("$n"); done < <(keys_list_names)
    if [ "${#names[@]}" -eq 0 ]; then
        echo -e "${YELLOW}Нет сохранённых ключей.${NC}"; return
    fi
    echo ""
    echo -e "${CYAN}━━━ Удаление WARP+ ключа ━━━${NC}"
    local i=1
    for n in "${names[@]}"; do
        local k; k=$(get_key_by_name "$n")
        echo -e "  ${WHITE}${i})${NC} ${YELLOW}${n}${NC}  ${CYAN}$(mask_key "$k")${NC}"
        i=$((i+1))
    done
    echo -e "  ${WHITE}0)${NC} Отмена"
    echo ""
    local choice; read -p "Удалить (номер): " choice
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || [ "$choice" -lt 0 ] || [ "$choice" -gt "${#names[@]}" ]; then
        echo -e "${RED}Некорректный выбор.${NC}"; return
    fi
    [ "$choice" = "0" ] && return
    local target="${names[$((choice-1))]}"
    read -p "Точно удалить «${target}»? (y/n): " yn
    [[ "$yn" != "y" ]] && return
    if ! delete_key "$target"; then
        echo -e "${RED}Не удалось завершить удаление ключа. Проверьте диагностику.${NC}"
        return 1
    fi
    log_action "KEY DELETE: name=${target}"
    echo -e "${GREEN}✓ Ключ «${target}» удалён.${NC}"
}

# Переключение режима Free/Plus для конкретной подсистемы
# Главное меню работы с ключами (вызывается из show_menu)
# ═══════════════════════════════════════════════════════════════
#  WARP+ MENU v2.4 — одно действие «Включить WARP+», без третьего уровня
# ═══════════════════════════════════════════════════════════════
# Старое меню требовало: добавить ключ → назначить активным для 3X-UI →
# назначить для AWG → переключить режим 3X-UI → переключить режим AWG →
# вернуться → «Перевыпуск» → «оставить режим» → выбрать контейнеры → y.
# Новое: «Включить WARP+» = ключ → к чему → y → применяется сразу и
# показывает честный результат (Unlimited / Limited / аккаунт Free / лимит
# устройств / ключ не распознан).

PLUS_TARGET_3XUI=0
PLUS_TARGET_AWG=0
REKEY_3XUI_IP=""
REKEY_3XUI_TRACE=""

plus_trace_label() {
    case "$1" in
        plus) echo -e "${GREEN}warp=plus — WARP+ работает${NC}" ;;
        free) echo -e "${YELLOW}warp=on — Free (WARP+ не активен)${NC}" ;;
        off)  echo -e "${RED}warp=off — трафик идёт не через WARP${NC}" ;;
        *)    echo -e "${DIM}нет ответа от cdn-cgi/trace${NC}" ;;
    esac
}

awg_any_installed() {
    awg_discover_containers 2>/dev/null || return 1
    local c
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        docker exec "$c" sh -c "[ -f '$AWG_WARP_CONF' ]" 2>/dev/null && return 0
    done
    return 1
}

# Спрашивает ключ, сохраняет под автоименем key-XXXX (имя больше не спрашиваем —
# пользователи вставляли в поле «название» сам ключ). Печатает имя в stdout.
# Промпты read -p идут в stderr, поэтому $(plus_enter_key) безопасен.
plus_enter_key() {
    local key name
    while true; do
        read -r -p "  Вставьте ключ из приложения 1.1.1.1 (Enter — отмена): " key
        key=$(printf '%s' "$key" | tr -d '[:space:]')
        [ -z "$key" ] && return 1
        if [[ "$key" == *"|"* || "$key" == *"&"* || "$key" == *"\\"* || "$key" == *"'"* || "$key" == *"\""* ]]; then
            echo -e "  ${RED}В ключе недопустимые символы (| & \\ ' \").${NC}" >&2
            continue
        fi
        if ! is_valid_warp_plus_key "$key"; then
            echo -e "  ${YELLOW}Не похоже на ключ WARP+: ожидается XXXXXXXX-XXXXXXXX-XXXXXXXX (или XXXX-XXXX-XXXX-XXXX).${NC}" >&2
            echo -e "  ${YELLOW}Ключ: приложение 1.1.1.1 → Настройки → Аккаунт → Ключ (Key). Вставьте ещё раз.${NC}" >&2
            continue
        fi
        break
    done
    local existing
    existing=$(awk -F'|' -v k="$key" '$2==k {print $1; exit}' "$WARP_KEYS" 2>/dev/null)
    if [ -n "$existing" ]; then
        echo -e "  ${DIM}Этот ключ уже сохранён как «${existing}».${NC}" >&2
        echo "$existing"
        return 0
    fi
    name="key-${key: -4}"
    local i=2
    while key_exists "$name"; do name="key-${key: -4}-${i}"; i=$((i+1)); done
    if ! add_key "$name" "$key"; then
        echo -e "  ${RED}Не удалось сохранить ключ.${NC}" >&2
        return 1
    fi
    log_action "KEY ADD: name=${name}, key=$(mask_key "$key")"
    echo -e "  ${GREEN}✓ Ключ сохранён как «${name}» ($(mask_key "$key"))${NC}" >&2
    echo "$name"
}

# Выбор ключа: список сохранённых + «ввести новый». Печатает имя в stdout.
plus_choose_key() {
    local names=() n
    while IFS= read -r n; do [ -n "$n" ] && names+=("$n"); done < <(keys_list_names)
    if [ ${#names[@]} -eq 0 ]; then
        plus_enter_key
        return
    fi
    echo -e "  ${WHITE}Какой ключ использовать?${NC}" >&2
    local i=1
    for n in "${names[@]}"; do
        echo -e "    ${i}) ${YELLOW}${n}${NC}  ${CYAN}$(mask_key "$(get_key_by_name "$n")")${NC}" >&2
        i=$((i+1))
    done
    echo -e "    ${i}) ➕ Ввести новый ключ" >&2
    echo -e "    0) Отмена" >&2
    local c
    read -r -p "  Выбор (Enter=1): " c
    c="${c:-1}"
    [ "$c" = "0" ] && return 1
    if [ "$c" = "$i" ]; then plus_enter_key; return; fi
    if [[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge 1 ] && [ "$c" -lt "$i" ]; then
        echo "${names[$((c-1))]}"
        return 0
    fi
    echo -e "  ${RED}Некорректный выбор.${NC}" >&2
    return 1
}

# К чему применить (в режиме both — вопрос, иначе автоматически).
plus_pick_subsystems() {
    PLUS_TARGET_3XUI=0; PLUS_TARGET_AWG=0
    if has_3xui_mode && has_awg_mode; then
        echo -e "  ${WHITE}К чему применить?${NC}"
        echo -e "    1) 3X-UI и AmneziaWG (оба)"
        echo -e "    2) только 3X-UI"
        echo -e "    3) только AmneziaWG"
        echo -e "    0) Отмена"
        local c
        read -r -p "  Выбор (Enter=1): " c
        case "${c:-1}" in
            1) PLUS_TARGET_3XUI=1; PLUS_TARGET_AWG=1 ;;
            2) PLUS_TARGET_3XUI=1 ;;
            3) PLUS_TARGET_AWG=1 ;;
            *) return 1 ;;
        esac
    else
        has_3xui_mode && PLUS_TARGET_3XUI=1
        has_awg_mode  && PLUS_TARGET_AWG=1
    fi
    return 0
}

plus_summary_line() {
    source "$WARP_CONF" 2>/dev/null
    has_3xui_mode && echo -e "  ${WHITE}3X-UI:     ${CYAN}$(get_mode_text_3xui)${NC}"
    has_awg_mode  && echo -e "  ${WHITE}AmneziaWG: ${CYAN}$(get_mode_text_awg)${NC}"
}

# ─── Включить WARP+: ключ → к чему → применить сразу ──────────
# One action used by CLI and Telegram. AWG preserves prior defaults on rollback.
plus_apply_choice() {
    if [ "${1:-}" = 3xui ]; then with_3xui_lock _plus_apply_choice_impl "$@"; else _plus_apply_choice_impl "$@"; fi
}

_plus_apply_choice_impl() {
    local sub="$1" requested="$2" name="${3:-}" rc=0
    [[ "$requested" = free || "$requested" = plus ]] || return 1
    [ "$requested" != plus ] || key_exists "$name" || return 1
    [ "$requested" != free ] || name=""
    if [ "$sub" = awg ]; then
        if awg_any_installed; then rekey_awg_core "$requested" "$name"
        else
            awg_restore_mode_snapshot "$requested" "$name" || return 1
            echo "Ключ сохранён; применится при установке AmneziaWG."
        fi
        return $?
    fi
    [ "$sub" = 3xui ] || return 1
    local old_mode="$WARP_KEY_MODE_3XUI" old_name="$ACTIVE_KEY_NAME_3XUI" old_license="" recovery="$WARP_DIR/3xui-recovery"
    if ! is_warp_installed_3xui; then
        save_config_values WARP_KEY_MODE_3XUI "$requested" ACTIVE_KEY_NAME_3XUI "$name"
        return $?
    fi
    if [ -f "$recovery" ]; then
        echo "3X-UI: сохранён незавершённый откат. Сначала восстановите настройки из $recovery."
        return 1
    fi
    if [ "$requested" = free ]; then
        save_config_values WARP_KEY_MODE_3XUI free ACTIVE_KEY_NAME_3XUI "" || return 1
        rekey_3xui_core
        return $?
    fi
    old_license=$(get_key_by_name "$old_name")
    if [ -z "$old_license" ]; then
        old_license=$(warp-cli --accept-tos registration show 2>/dev/null | sed -nE 's/^[[:space:]]*License[[:space:]]*:[[:space:]]*(.*)/\1/p' | head -1)
    fi
    # Preserve both choices for manual recovery if Cloudflare refuses the rollback.
    (umask 077; printf '%s\n%s\n%s\n%s\n' "$old_mode" "$old_name" "$name" "$old_license" > "$recovery") || return 1
    WARP_KEY_MODE_3XUI=plus; ACTIVE_KEY_NAME_3XUI="$name"
    if apply_warp_plus_key_3xui && warp-cli --accept-tos mode proxy >/dev/null 2>&1 &&
       warp-cli --accept-tos proxy port "$SOCKS_PORT" >/dev/null 2>&1 &&
       warp-cli --accept-tos connect >/dev/null 2>&1 && verify_3xui_mode plus; then
        if save_config_values WARP_KEY_MODE_3XUI plus ACTIVE_KEY_NAME_3XUI "$name"; then
            rm -f "$recovery"
            echo "3X-UI: WARP+ подтверждён трафиком."
            return 0
        fi
        echo "3X-UI: WARP+ работает, но настройки не записаны. Данные восстановления сохранены."
        return 1
    fi
    local restored=0
    if [ -n "$old_license" ]; then
        if warp-cli --accept-tos registration license "$old_license" >/dev/null 2>&1; then
            cloudflare_log_scrub
            if warp-cli --accept-tos connect >/dev/null 2>&1 && verify_3xui_mode "$old_mode"; then restored=1; fi
        else cloudflare_log_scrub; fi
    elif [ "$old_mode" = free ]; then
        # A licence cannot be unset on an existing paid registration.
        if warp-cli --accept-tos registration delete >/dev/null 2>&1 &&
           warp-cli --accept-tos registration new >/dev/null 2>&1 &&
           warp-cli --accept-tos mode proxy >/dev/null 2>&1 &&
           warp-cli --accept-tos proxy port "$SOCKS_PORT" >/dev/null 2>&1 &&
           warp-cli --accept-tos connect >/dev/null 2>&1 && verify_3xui_mode free; then restored=1; fi
    fi
    if [ "$restored" = 1 ] && save_config_values WARP_KEY_MODE_3XUI "$old_mode" ACTIVE_KEY_NAME_3XUI "$old_name"; then
        rm -f "$recovery"
        echo "3X-UI: новый ключ не подтверждён; прежний режим восстановлен и проверен."
    else
        echo "3X-UI: откат не подтверждён. Проверьте диагностику; данные восстановления сохранены в $recovery."
        log_action "3XUI APPLY: rollback unresolved; recovery retained"
    fi
    return 1
}

verify_3xui_mode() {
    local expected="$1" attempt trace
    for attempt in 1 2 3; do
        trace=$(verify_warp_plus_3xui) || trace=""
        [ "$trace" = "$expected" ] && return 0
        [ "$attempt" = 3 ] || sleep 2
    done
    return 1
}

plus_enable_wizard() {
    echo ""
    echo -e "${CYAN}━━━ Включить WARP+ ━━━${NC}"
    local name
    name=$(plus_choose_key) || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }
    [ -n "$name" ] || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }
    plus_pick_subsystems || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }
    local what=""
    [ "$PLUS_TARGET_3XUI" = 1 ] && what+="3X-UI "
    [ "$PLUS_TARGET_AWG" = 1 ]  && what+="AmneziaWG"
    echo ""
    echo -e "  Ключ ${YELLOW}${name}${NC} → ${WHITE}${what}${NC}"
    echo -e "  ${DIM}WARP переподключится с новым IP; клиенты через WARP кратковременно переподключатся.${NC}"
    echo -e "  ${DIM}Каждый контейнер AmneziaWG и 3X-UI — отдельное устройство на ключе (лимит Cloudflare: 5, телефон тоже считается).${NC}"
    local yn
    read -r -p "  Применить сейчас? (y/n): " yn
    [[ "$yn" == "y" ]] || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }

    local failed=0
    if [ "$PLUS_TARGET_3XUI" = 1 ]; then plus_apply_choice 3xui plus "$name" || failed=1; fi
    if [ "$PLUS_TARGET_AWG" = 1 ]; then plus_apply_choice awg plus "$name" || failed=1; fi
    [ "$failed" = 0 ] || echo "Не все изменения применены. Подробности выше."
    echo ""
    echo -e "${WHITE}Итог:${NC}"
    plus_summary_line
}

# ─── Переключить на Free (применяется сразу) ──────────────────
plus_switch_to_free() {
    echo ""
    echo -e "${CYAN}━━━ Переключить на Free ━━━${NC}"
    plus_pick_subsystems || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }
    local yn
    read -r -p "  WARP переподключится с бесплатным аккаунтом. Продолжить? (y/n): " yn
    [[ "$yn" == "y" ]] || { echo -e "  ${YELLOW}Отменено.${NC}"; return; }
    if [ "$PLUS_TARGET_3XUI" = 1 ]; then plus_apply_choice 3xui free ""; fi
    if [ "$PLUS_TARGET_AWG" = 1 ]; then plus_apply_choice awg free ""; fi
    echo ""
    echo -e "${WHITE}Итог:${NC}"
    plus_summary_line
}

# ─── Проверить WARP+ сейчас (только чтение) ───────────────────
plus_check_now() {
    echo ""
    echo -e "${CYAN}━━━ Проверка WARP+ ━━━${NC}"
    if has_3xui_mode; then
        echo -e "${WHITE}3X-UI${NC} — в конфиге: ${CYAN}$(get_mode_text_3xui)${NC}"
        if is_warp_installed_3xui; then
            local at; at=$(_parse_account_type "$(warp-cli --accept-tos registration show 2>/dev/null)")
            echo -e "   аккаунт по данным warp-cli: ${CYAN}${at:-неизвестно}${NC}"
            local t; t=$(verify_warp_plus_3xui)
            echo -e "   трассировка через SOCKS5:   $(plus_trace_label "$t")"
        else
            echo -e "   ${DIM}WARP не установлен${NC}"
        fi
    fi
    if has_awg_mode; then
        echo -e "${WHITE}AmneziaWG${NC} — в конфиге: ${CYAN}$(get_mode_text_awg)${NC}"
        awg_discover_containers 2>/dev/null
        local c
        for c in "${AWG_ALL_CONTAINERS[@]}"; do
            if docker exec "$c" sh -c "[ -f '$AWG_WARP_CONF' ]" 2>/dev/null; then
                awg_wgcf_ctx "$c"
                [ -f "$WGCF_ACCOUNT" ] || awg_wgcf_ctx_reset   # старый общий аккаунт до v2.4
                local probe acct="нет данных"; probe=$(cf_account_probe_wgcf 2>/dev/null || true)
                if [ -n "$probe" ]; then
                    local at q; at=$(printf '%s' "$probe" | cut -d'|' -f1); q=$(printf '%s' "$probe" | cut -d'|' -f2)
                    acct="$at"
                    [ "$at" = "limited" ] && acct+=", квота $(LC_ALL=C awk -v q="$q" 'BEGIN{printf "%.1f", q/1073741824}') ГБ"
                fi
                local t; t=$(verify_warp_plus_awg_container "$c")
                echo -e "   ${c}: аккаунт ${CYAN}${acct}${NC}, трассировка $(plus_trace_label "$t")"
            else
                echo -e "   ${c}: ${DIM}WARP не установлен${NC}"
            fi
        done
        awg_wgcf_ctx_reset
        if awg_shared_key_detected; then
            echo -e "   ${YELLOW}⚠ контейнеры делят один ключ WARP (старая установка) — нажмите «Включить WARP+» или п.3, чтобы выдать каждому своё устройство${NC}"
        fi
    fi
    echo ""
    echo -e "${DIM}Если аккаунт уже Unlimited, а трассировка показывает Free — нажмите «Включить WARP+» ещё раз:${NC}"
    echo -e "${DIM}туннель переподключится и подхватит подписку.${NC}"
}

# ─── L2: меню WARP+ ───────────────────────────────────────────
plus_menu() {
    while true; do
        clear; source "$WARP_CONF" 2>/dev/null
        echo -e "${MAGENTA}━━━ WARP+ ключ ━━━${NC}"
        plus_summary_line
        local cnt; cnt=$(keys_count)
        if [ "$cnt" -gt 0 ]; then
            echo -e "  ${WHITE}Сохранённые ключи:${NC}"
            local n k ts
            while IFS='|' read -r n k ts; do
                [ -z "$n" ] && continue
                local marks=""
                [ "$ACTIVE_KEY_NAME_3XUI" = "$n" ] && marks+=" [3X-UI]"
                [ "$ACTIVE_KEY_NAME_AWG"  = "$n" ] && marks+=" [AWG]"
                echo -e "    ${YELLOW}${n}${NC}  ${CYAN}$(mask_key "$k")${NC}${GREEN}${marks}${NC}"
            done < "$WARP_KEYS"
        else
            echo -e "  ${DIM}Сохранённых ключей нет${NC}"
        fi
        echo ""
        echo -e "  1) ⭐ ${YELLOW}Включить WARP+${NC} — ввести ключ или выбрать сохранённый, применяется сразу"
        echo -e "  2) 🆓 Переключить на Free"
        echo -e "  3) 🔎 Проверить WARP+ сейчас"
        echo -e "  4) 🗑  Удалить сохранённый ключ"
        echo -e "  5) 💡 Как получить ключ WARP+"
        echo -e "  0) ⬅️  Назад"
        echo ""
        local ch
        read -r -p "  Выбор: " ch || return 0
        case "$ch" in
            1) plus_enable_wizard; read -r -p "  Enter..." ;;
            2) plus_switch_to_free; read -r -p "  Enter..." ;;
            3) plus_check_now; read -r -p "  Enter..." ;;
            4) cli_delete_key; read -r -p "  Enter..." ;;
            5) show_warp_plus_info; read -r -p "  Enter..." ;;
            0) return ;;
        esac
    done
}

# Снять старое устройство wgcf с аккаунта Cloudflare (освобождает слот из 5).
# Вызывать только ПОСЛЕ успешного перевыпуска: при откате старый профиль ещё нужен.
awg_unregister_device_id() {
    local dev="$1" tok="$2" code
    [ -n "$dev" ] && [ -n "$tok" ] || return 1
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -X DELETE "${CF_API_BASE}/reg/${dev}" \
        -H "Authorization: Bearer ${tok}" -H "User-Agent: okhttp/3.12.1" \
        -H "CF-Client-Version: a-6.30-3596" 2>/dev/null) || return 1
    case "$code" in
        200|204|404) log_action "AWG DEVICE: deletion confirmed (HTTP $code)"; return 0 ;;
        *) log_action "AWG DEVICE: deletion unconfirmed (HTTP ${code:-unknown})"; return 1 ;;
    esac
}

# Store cleanup credentials outside paths removed by a normal uninstall.
awg_queue_retirement() {
    local account="$1" profile="${2:-${1%/*}/wgcf-profile.conf}" queue="${HOST_WGCF_BAK_ROOT%/*}/pending-retirements" id
    [ -f "$account" ] || return 0
    id=$(sha256sum "$account" | awk '{print $1}') || return 1
    mkdir -p "$queue" && chmod 700 "$queue" || return 1
    cp -p "$account" "$queue/$id.toml" && chmod 600 "$queue/$id.toml" || return 1
    if [ -f "$profile" ]; then cp -p "$profile" "$queue/$id.conf" && chmod 600 "$queue/$id.conf" || return 1; fi
    log_action "AWG DEVICE: cleanup credentials retained in pending-retirements"
}

# 0=in use, 1=proven unused, 2=unknown. docker cp also reads stopped containers.
awg_profile_in_use() {
    local profile="$1" exclude="${2:-}" names c old_hash current_hash temp
    [ -s "$profile" ] || return 2
    grep -q '^PrivateKey[[:space:]]*=' "$profile" || return 2
    old_hash=$(sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' "$profile" | sha256sum | awk '{print $1}') || return 2
    # Docker на хосте нет — нет и контейнеров, которые могли бы держать это устройство.
    command -v docker >/dev/null 2>&1 || return 1
    names=$(docker ps -a --format '{{.Names}}' 2>/dev/null) || return 2
    temp=$(mktemp -d) || return 2
    chmod 700 "$temp"
    while IFS= read -r c; do
        [[ "$c" =~ ^amnezia-awg[0-9]*$ ]] || continue
        [ "$c" != "$exclude" ] || continue
        if docker cp "$c:$AWG_WARP_DIR/wgcf-profile.conf" "$temp/profile" >/dev/null 2>&1; then
            if ! grep -q '^PrivateKey[[:space:]]*=' "$temp/profile"; then rm -rf "$temp"; return 2; fi
            current_hash=$(sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' "$temp/profile" | sha256sum | awk '{print $1}')
            if [ "$old_hash" = "$current_hash" ]; then rm -rf "$temp"; return 0; fi
        elif ! docker exec "$c" test ! -f "$AWG_WARP_CONF" >/dev/null 2>&1; then
            rm -rf "$temp"; return 2
        fi
    done <<< "$names"
    rm -rf "$temp"
    return 1
}

awg_retire_unused_account() {
    local account="$1" profile="$2" exclude="${3:-}" usage=0
    [ -f "$account" ] || return 0
    awg_profile_in_use "$profile" "$exclude" || usage=$?
    if [ "$usage" = 1 ]; then
        awg_unregister_account_file "$account" "$profile"
    else
        awg_queue_retirement "$account" "$profile" || return 2
        return 1
    fi
}

# ─── Единый перевыпуск 3X-UI без вопросов (режим берётся из конфига) ───
rekey_3xui_core() {
    save_config_val WARP_WANTED_3XUI on
    with_3xui_lock _rekey_3xui_core_locked "$@"
}

_rekey_3xui_core_locked() {
    is_warp_installed_3xui || { echo -e "${RED}WARP (3X-UI) не установлен.${NC}"; return 1; }
    local requested_mode="$WARP_KEY_MODE_3XUI"
    REKEY_3XUI_IP=""; REKEY_3XUI_TRACE=""
    echo -e "${YELLOW}[1/4]${NC} Отключение..."; warp-cli --accept-tos disconnect > /dev/null 2>&1; echo -e "${GREEN}  ✓${NC}"
    echo -e "${YELLOW}[2/4]${NC} Удаление регистрации..."; warp-cli --accept-tos registration delete > /dev/null 2>&1; echo -e "${GREEN}  ✓${NC}"
    echo -e "${YELLOW}[3/4]${NC} Новая регистрация..."
    if ! warp-cli --accept-tos registration new > /dev/null 2>&1; then
        echo -e "${RED}  ✗ Ошибка регистрации в Cloudflare. Выбранный режим и ключ сохранены для повтора.${NC}"
        log_action "3XUI REKEY: registration new failed"
        return 1
    fi
    echo -e "${GREEN}  ✓${NC}"
    if [ "$WARP_KEY_MODE_3XUI" = "plus" ] && [ -n "$ACTIVE_KEY_NAME_3XUI" ]; then
        local want_name="$ACTIVE_KEY_NAME_3XUI"
        echo -e "${YELLOW}      Применение WARP+ (${want_name})...${NC}"
        if apply_warp_plus_key_3xui; then
            echo -e "${GREEN}      ✓ ключ принят: $(plus_reason_text "$PLUS_LAST_REASON" "3X-UI" | head -1)${NC}"
        else
            # Сразу после registration new Cloudflare иногда отвечает ошибкой (см. лог
            # 2026-09-05 16:05) — при неоднозначной причине пробуем ещё раз через 3 с.
            case "$PLUS_LAST_REASON" in
                rejected|network)
                    sleep 3
                    echo -e "${YELLOW}      повтор...${NC}"
                    save_config_val "ACTIVE_KEY_NAME_3XUI" "$want_name"
                    save_config_val "WARP_KEY_MODE_3XUI" "plus"
                    apply_warp_plus_key_3xui && echo -e "${GREEN}      ✓ ключ принят со второй попытки${NC}"
                    ;;
            esac
            source "$WARP_CONF"
        fi
    fi
    echo -e "${YELLOW}[4/4]${NC} Подключение..."
    warp-cli --accept-tos mode proxy > /dev/null 2>&1
    warp-cli --accept-tos proxy port "${SOCKS_PORT}" > /dev/null 2>&1
    warp-cli --accept-tos connect > /dev/null 2>&1; sleep 3
    if ! is_warp_running_3xui; then
        echo -e "${YELLOW}  ⚠ Подключение не подтверждено.${NC}"
        log_action "3XUI REKEY: connect not confirmed, mode=${WARP_KEY_MODE_3XUI}"
        return 1
    fi
    REKEY_3XUI_IP=$(get_warp_ip_3xui)
    echo -e "${GREEN}  ✓ Новый WARP IP: ${REKEY_3XUI_IP}${NC}"
    local attempt
    for attempt in 1 2 3; do
        REKEY_3XUI_TRACE=$(verify_warp_plus_3xui)
        [ "$REKEY_3XUI_TRACE" = "$requested_mode" ] && break
        [ "$attempt" -lt 3 ] && sleep 2
    done
    if [ "$WARP_KEY_MODE_3XUI" = "plus" ]; then
        case "$REKEY_3XUI_TRACE" in
            plus) echo -e "  ${GREEN}✓ cdn-cgi/trace: warp=plus — WARP+ подтверждён${NC}" ;;
            free) echo -e "  ${YELLOW}⚠ cdn-cgi/trace: warp=on (Free) — WARP+ не подтверждён, откат на Free${NC}"
                  save_config_val "WARP_KEY_MODE_3XUI" "free"
                  save_config_val "ACTIVE_KEY_NAME_3XUI" "" ;;
            off)  echo -e "  ${YELLOW}⚠ cdn-cgi/trace: warp=off${NC}" ;;
            "")   echo -e "  ${DIM}(проверка trace недоступна)${NC}" ;;
        esac
    else
        echo -e "  ${WHITE}Режим: ${GREEN}Free${NC}"
    fi
    log_action "3XUI REKEY: mode=${WARP_KEY_MODE_3XUI}, warp_ip=${REKEY_3XUI_IP}, trace=${REKEY_3XUI_TRACE:-skip}"
    [ "$REKEY_3XUI_TRACE" = "$requested_mode" ]
}

# A replacement is prepared while the existing tunnel stays up. Each container
# commits only after an end-to-end trace; failed replacements restore snapshots.
rekey_awg_core() {
    local requested_mode="${1:-$WARP_KEY_MODE_AWG}" requested_key="${2-$ACTIVE_KEY_NAME_AWG}"
    local snap_mode="$WARP_KEY_MODE_AWG" snap_active="$ACTIVE_KEY_NAME_AWG"
    REKEY_AWG_DETAILS=""
    local saved_container="${CONTAINER:-}" c ok_cnt=0 fail_cnt=0
    local -a installed_targets=()
    awg_discover_containers >/dev/null 2>&1 || return 1
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        # Обновление разводит общий ключ: перевыпуск только для перечисленных контейнеров.
        if [ -n "${AWG_REKEY_ONLY:-}" ]; then
            case " $AWG_REKEY_ONLY " in *" $c "*) ;; *) continue ;; esac
        fi
        if [ "${3:-}" = install ] || docker exec "$c" sh -c "[ -f '$AWG_WARP_CONF' ]" 2>/dev/null; then
            installed_targets+=("$c")
        fi
    done
    [ "${#installed_targets[@]}" -gt 0 ] || { echo "WARP не установлен в AmneziaWG."; return 1; }
    # Регистрация идёт через wgcf: старую сборку на сервере меняем до неё.
    awg_install_wgcf || { echo "Не удалось подготовить wgcf ${WGCF_VERSION}; перевыпуск отменён."; return 1; }
    AWG_TARGETS=("${installed_targets[@]}")
    local operation_fd
    exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    if ! flock -n "$operation_fd"; then
        echo "Другая операция AmneziaWG уже выполняется."
        exec {operation_fd}>&-; return 1
    fi
    for c in "${AWG_TARGETS[@]}"; do
        CONTAINER="$c"
        echo "► ${c}: подготовка нового профиля"
        if ! awg_load_container_data || ! awg_wgcf_ctx "$c"; then
            ((fail_cnt++)); continue
        fi
        if [ -f "$HOST_WGCF_BAK_DIR/recovery-required" ]; then
            # Перевыпуск, прерванный раньше (в 2.5.0 — на WARP-интерфейсе amneziawg): сначала довести его.
            if ! awg_resume_interrupted_rekey "$c"; then
                echo "  ✗ Прерванный перевыпуск завершить не удалось; данные восстановления сохранены."
                ((fail_cnt++)); continue
            fi
            if [ "$AWG_RESUME_RESULT" = forward ]; then
                echo "  ✓ Прерванный перевыпуск завершён: новое устройство WARP работает."
                ((ok_cnt++)); continue
            fi
        fi
        awg_prepare_warp_tools || echo "  ⚠ Стандартные инструменты WireGuard не скачались; WARP поднимется инструментами контейнера."
        awg_adopt_legacy_account "$c" || { ((fail_cnt++)); continue; }
        if ! awg_finish_client_rollback "$c"; then
            echo "  ✗ Прошлая смена клиентов не откатилась до конца; туннель сохранён."
            ((fail_cnt++)); continue
        fi
        local endpoint
        if ! endpoint=$(awg_container_endpoint "$c"); then
            echo "  ✗ Не удалось определить адрес Cloudflare (DNS); туннель сохранён."
            ((fail_cnt++)); continue
        fi
        if ! awg_snapshot_container "$c" || ! awg_snapshot_host_wgcf; then
            echo "  Не удалось сделать резервную копию; туннель сохранён."
            ((fail_cnt++)); continue
        fi
        local prepare_ok=1 switched=0 failure_reason=""
        if ! rm -f "$WGCF_ACCOUNT" "$WGCF_PROFILE"; then
            if awg_restore_host_wgcf; then
                awg_clear_host_wgcf_snapshot
                awg_clear_container_snapshot "$c"
            else
                touch "$HOST_WGCF_BAK_DIR/recovery-required"
            fi
            echo "  Не удалось подготовить файлы нового устройства; рабочая регистрация не отозвана."
            fail_cnt=$((fail_cnt + 1)); continue
        fi
        awg_ensure_account >/dev/null || { prepare_ok=0; failure_reason="${AWG_REGISTER_ERROR:-}"; }
        if [ "$prepare_ok" = 1 ] && [ "$requested_mode" = plus ]; then
            # Application helpers may update defaults on failure; restore below.
            WARP_KEY_MODE_AWG=plus; ACTIVE_KEY_NAME_AWG="$requested_key"
            if ! apply_warp_plus_key_awg; then
                prepare_ok=0
                failure_reason=$(plus_reason_text "${PLUS_LAST_REASON:-rejected}" AmneziaWG | head -1)
            fi
        fi
        if [ "$prepare_ok" = 1 ]; then awg_generate_profile >/dev/null 2>&1 || prepare_ok=0; fi
        # Keep configured endpoint and local UDP port across rotations.
        if [ "$prepare_ok" = 1 ]; then
            switched=1
            if awg_build_warp_conf "$endpoint"; then
                awg_warp_up >/dev/null 2>&1 && awg_load_clients && awg_apply_rules || prepare_ok=0
            else prepare_ok=0; fi
            if [ "$prepare_ok" = 1 ] && [ "${AWG_WARP_KIND:-wireguard}" != wireguard ]; then
                echo "  ⚠ WARP поднят не на стандартном WireGuard (${AWG_WARP_KIND}); автопроверка пересоберёт, когда появятся инструменты."
            fi
        fi
        local verified=""
        if [ "$prepare_ok" = 1 ]; then
            verified=$(awg_verify_ready "$c" "$requested_mode") || prepare_ok=0
        fi
        if [ "$prepare_ok" = 1 ] && awg_patch_start_sh; then
            # Retire only this manager's old device, after traffic verification.
            local retire_rc=0
            awg_retire_snapshot_account || retire_rc=$?
            if [ "$retire_rc" -eq 2 ]; then
                echo "  ⚠ Данные старого устройства сохранены в резервной копии: не удалось записать очередь удаления."
                touch "$HOST_WGCF_BAK_DIR/recovery-required"
                fail_cnt=$((fail_cnt + 1))
            else
                awg_clear_host_wgcf_snapshot
                awg_clear_container_snapshot "$c"
            fi
            ((ok_cnt++))
            echo "  ✓ Трафик проверен: ${verified}."
        else
            # Restore and verify before deleting the candidate. Keep both identities
            # and snapshots if recovery fails, so a later retry cannot destroy them.
            local recovery_ok=1 candidate_dir
            candidate_dir=$(mktemp -d "$WGCF_DIR/.candidate.XXXXXX") || recovery_ok=0
            if [ "$recovery_ok" = 1 ]; then
                [ ! -f "$WGCF_ACCOUNT" ] || cp -p "$WGCF_ACCOUNT" "$candidate_dir/wgcf-account.toml" || recovery_ok=0
                [ ! -f "$WGCF_PROFILE" ] || cp -p "$WGCF_PROFILE" "$candidate_dir/wgcf-profile.conf" || recovery_ok=0
            fi
            if [ "$recovery_ok" = 1 ] && [ "$switched" = 1 ]; then
                awg_restore_container_snapshot "$c" || recovery_ok=0
            fi
            if [ "$recovery_ok" = 1 ]; then awg_restore_host_wgcf || recovery_ok=0; fi
            if [ "$recovery_ok" = 1 ]; then
                local cleanup_rc=0
                local candidate_id old_id
                candidate_id=$(sed -n '/^device_id[[:space:]]*=/p' "$candidate_dir/wgcf-account.toml" 2>/dev/null)
                old_id=$(sed -n '/^device_id[[:space:]]*=/p' "$HOST_WGCF_BAK_DIR/wgcf-account.toml" 2>/dev/null)
                if [ -z "$candidate_id" ] || [ "$candidate_id" != "$old_id" ]; then
                    awg_unregister_account_file "$candidate_dir/wgcf-account.toml" "$candidate_dir/wgcf-profile.conf" || cleanup_rc=$?
                fi
                if [ "$cleanup_rc" -ne 2 ]; then rm -rf "$candidate_dir"; fi
                awg_clear_host_wgcf_snapshot
                awg_clear_container_snapshot "$c"
                local shown_reason="${failure_reason%.}"
                echo "  ✗ ${shown_reason:-Новый профиль не прошёл проверку}; прежние настройки восстановлены."
            else
                touch "$HOST_WGCF_BAK_DIR/recovery-required"
                awg_mark_container_recovery "$c"
                echo "  ✗ Откат $c не завершён. Сохранены обе регистрации и резервные копии; повторный перевыпуск заблокирован."
                log_action "AWG REKEY: $c recovery unresolved; identities and snapshots retained"
            fi
            REKEY_AWG_DETAILS+="$c: ${failure_reason:-новый профиль не прошёл проверку.}"$'\n'
            log_action "AWG REKEY FAILURE: $c ${failure_reason:-profile verification failed}"
            ((fail_cnt++))
        fi
        awg_restore_mode_snapshot "$snap_mode" "$snap_active" || fail_cnt=$((fail_cnt + 1))
    done
    CONTAINER="$saved_container"
    awg_wgcf_ctx_reset
    if [ "$ok_cnt" -eq "${#AWG_TARGETS[@]}" ]; then
        awg_restore_mode_snapshot "$requested_mode" "$requested_key" || fail_cnt=$((fail_cnt + 1))
    fi
    if [ "$ok_cnt" -gt 0 ]; then awg_install_host_watcher || fail_cnt=$((fail_cnt + 1)); fi
    if [ "$ok_cnt" -gt 0 ]; then health_service_install >/dev/null 2>&1 || log_action "HEALTH: служба не установлена"; fi
    exec {operation_fd}>&-
    echo "Проверено: $ok_cnt; ошибок: $fail_cnt."
    [ "$fail_cnt" -gt 0 ] && echo "При частичном успехе фактический режим каждого контейнера виден в диагностике."
    log_action "AWG REKEY: ${ok_cnt} verified / ${fail_cnt} failed"
    [ "$fail_cnt" -eq 0 ]
}

awg_verify_ready() {
    local c="$1" mode="$2" attempt trace
    for attempt in 1 2 3; do
        trace=$(verify_warp_plus_awg_container "$c") || trace=""
        if { [ "$mode" = plus ] && [ "$trace" = plus ]; } ||
           { [ "$mode" = free ] && [ "$trace" = free ]; }; then
            echo "$trace"; return 0
        fi
        [ "$attempt" -lt 3 ] && sleep 2
    done
    return 1
}

# Перевыпуск в 2.5.0 не мог снять WARP-интерфейс amneziawg: откат срывался, контейнер оставался с меткой
# выключения, а перевыпуск и удаление блокировались. Новый профиль к этому моменту уже лежит в контейнере,
# поэтому перевыпуск доводим до конца: поднимаем новый профиль стандартными инструментами и проверяем
# трафик. Прежнее устройство соседние контейнеры могли успеть удалить в Cloudflare, поэтому к нему
# возвращаемся, только если новое не прошло проверку.
# Снимок контейнера — на хосте; снимок версий до 2.5.2 из /tmp контейнера сюда переносится. Если снимка нет
# (контейнер пересоздан, пока перевыпуск ждал), прежний WARP собирается из хостовой копии прежнего устройства.
awg_resume_interrupted_rekey() {
    local c="$1" cand cand_id old_id rc endpoint
    AWG_RESUME_RESULT=none
    awg_wgcf_ctx "$c" || return 1
    [ -f "$HOST_WGCF_BAK_DIR/recovery-required" ] || return 0
    CONTAINER="$c"
    awg_load_container_data >/dev/null 2>&1 || return 1
    awg_import_legacy_snapshot "$c" || return 1
    # Прошлый откат уже вернул в контейнер прежнее устройство и сорвался позже — «вперёд» некуда, доводим откат.
    # Иначе хост остался бы на новом устройстве, а контейнер работал бы на прежнем.
    local run_hash old_hash
    run_hash=$(docker exec "$c" sh -c "sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' '$AWG_WARP_CONF'" 2>/dev/null | head -1 | sha256sum | cut -c1-32)
    old_hash=$(sed -n 's/^PrivateKey[[:space:]]*=[[:space:]]*//p' "$HOST_WGCF_BAK_DIR/wgcf-profile.conf" 2>/dev/null | head -1 | sha256sum | cut -c1-32)
    # Режим не сверяем: при переходе Free → WARP+ новое устройство уже WARP+, а в конфиге остался Free.
    if { [ ! -s "$HOST_WGCF_BAK_DIR/wgcf-profile.conf" ] || [ "$run_hash" != "$old_hash" ]; } &&
       awg_warp_up restart >/dev/null 2>&1 && awg_load_clients && awg_apply_rules >/dev/null 2>&1 &&
       awg_verify_warp_any "$c" >/dev/null && awg_patch_start_sh >/dev/null 2>&1; then
        # Новое устройство работает: прежнее удаляется, если его не использует другой контейнер.
        rc=0; awg_retire_snapshot_account >/dev/null 2>&1 || rc=$?
        [ "$rc" != 2 ] || return 1
        for cand in "$WGCF_DIR"/.candidate.*; do [ ! -d "$cand" ] || rm -rf "$cand"; done
        awg_clear_host_wgcf_snapshot
        awg_clear_container_snapshot "$c"
        AWG_RESUME_RESULT=forward
        log_action "AWG: ${c} — прерванный перевыпуск завершён: новое устройство WARP работает"
        return 0
    fi
    # Новое не прошло проверку: прежние туннель и регистрация, новое устройство удалить.
    if [ -f "$(awg_ct_snap_dir "$c")/ready" ]; then
        awg_restore_container_snapshot "$c" >/dev/null 2>&1 || return 1
    else
        [ -s "$HOST_WGCF_BAK_DIR/wgcf-profile.conf" ] || return 1
        endpoint=$(awg_container_endpoint "$c") || return 1
        WGCF_PROFILE="$HOST_WGCF_BAK_DIR/wgcf-profile.conf" awg_build_warp_conf "$endpoint" || return 1
        awg_warp_up restart >/dev/null 2>&1 && awg_load_clients && awg_apply_rules >/dev/null 2>&1 &&
            awg_patch_start_sh >/dev/null 2>&1 || return 1
    fi
    awg_restore_host_wgcf || return 1
    old_id=$(sed -n '/^device_id[[:space:]]*=/p' "$WGCF_ACCOUNT" 2>/dev/null)
    for cand in "$WGCF_DIR"/.candidate.*; do
        [ -d "$cand" ] || continue
        cand_id=$(sed -n '/^device_id[[:space:]]*=/p' "$cand/wgcf-account.toml" 2>/dev/null)
        rc=0
        if [ -n "$cand_id" ] && [ "$cand_id" != "$old_id" ]; then
            awg_unregister_account_file "$cand/wgcf-account.toml" "$cand/wgcf-profile.conf" >/dev/null 2>&1 || rc=$?
        fi
        [ "$rc" = 2 ] || rm -rf "$cand"
    done
    awg_clear_host_wgcf_snapshot
    awg_clear_container_snapshot "$c"
    if awg_verify_warp_any "$c" >/dev/null; then
        AWG_RESUME_RESULT=back
        log_action "AWG: ${c} — прерванный перевыпуск откачен: новое устройство не прошло проверку, прежний WARP восстановлен"
    else
        # Прежнее устройство соседние контейнеры могли удалить в Cloudflare: блокировку снимаем,
        # чтобы п.3 выдал новое.
        AWG_RESUME_RESULT=dead
        log_action "AWG: ${c} — прерванный перевыпуск откачен, но WARP не проходит проверку; нажмите 3"
    fi
    return 0
}

# Трафик через WARP в контейнере проходит — Free или WARP+, неважно. Вывод: free|plus.
awg_verify_warp_any() {
    local c="$1" attempt trace
    for attempt in 1 2 3; do
        trace=$(verify_warp_plus_awg_container "$c") || trace=""
        case "$trace" in free|plus) echo "$trace"; return 0 ;; esac
        [ "$attempt" -lt 3 ] && sleep 2
    done
    return 1
}

awg_unregister_account_file() {
    local f="$1" profile="${2:-${1%/*}/wgcf-profile.conf}" dev tok
    [ -f "$f" ] || return 0
    dev=$(sed -nE "s/^device_id[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$f" | head -1)
    tok=$(sed -nE "s/^access_token[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" "$f" | head -1)
    if awg_unregister_device_id "$dev" "$tok"; then return 0; fi
    awg_queue_retirement "$f" "$profile" || return 2
    return 1
}

awg_retire_snapshot_account() {
    awg_retire_unused_account "$HOST_WGCF_BAK_DIR/wgcf-account.toml" "$HOST_WGCF_BAK_DIR/wgcf-profile.conf"
}

awg_adopt_legacy_account() {
    local c="$1" legacy="${WGCF_LEGACY_DIR:-/root}" old_hash live_hash f id
    [ -f "$WGCF_ACCOUNT" ] && return 0
    [ -f "$legacy/wgcf-account.toml" ] && [ -f "$legacy/wgcf-profile.conf" ] || return 0
    old_hash=$(sha256sum "$legacy/wgcf-profile.conf" | awk '{print $1}')
    live_hash=$(docker exec "$c" sh -c "sha256sum '$AWG_WARP_DIR/wgcf-profile.conf'" 2>/dev/null | awk '{print $1}')
    [ "$old_hash" = "$live_hash" ] || return 0
    id=$(sed -n '/^device_id[[:space:]]*=/p' "$legacy/wgcf-account.toml")
    for f in "$AWG_WGCF_BASE"/*/wgcf-account.toml; do
        [ -f "$f" ] && grep -qFx "$id" "$f" && return 0
    done
    cp -p "$legacy/wgcf-account.toml" "$WGCF_ACCOUNT" &&
    cp -p "$legacy/wgcf-profile.conf" "$WGCF_PROFILE" &&
    chmod 600 "$WGCF_ACCOUNT" "$WGCF_PROFILE"
}

# ─── Обёртки для главного меню (один вопрос «Продолжить?») ─────
rekey_warp_3xui() {
    is_warp_installed_3xui || { echo -e "\n${RED}WARP (3X-UI) не установлен.${NC}"; read -r -p "Enter..."; return; }
    echo -e "\n${CYAN}━━━ Сменить WARP IP (3X-UI) ━━━${NC}"
    echo -e "${WHITE}Режим ${CYAN}$(get_mode_text_3xui)${WHITE} сохраняется; Free/WARP+ меняется в меню «WARP+ ключ».${NC}"
    local c; read -r -p "Продолжить? (y/n): " c
    [[ "$c" == "y" ]] || { echo -e "${YELLOW}Отменено.${NC}"; return; }
    rekey_3xui_core
    read -r -p "Enter..."
}

rekey_warp_awg() {
    awg_any_installed || { echo -e "\n${RED}WARP не установлен ни в одном контейнере AmneziaWG.${NC}"; read -r -p "Enter..."; return; }
    echo -e "\n${CYAN}━━━ Сменить WARP IP (AmneziaWG) ━━━${NC}"
    echo -e "${WHITE}Режим ${CYAN}$(get_mode_text_awg)${WHITE} сохраняется; перевыпуск для всех контейнеров с WARP.${NC}"
    local c; read -r -p "Продолжить? (y/n): " c
    [[ "$c" == "y" ]] || { echo -e "${YELLOW}Отменено.${NC}"; return; }
    rekey_awg_core
    read -r -p "Enter..."
}

# Из главного меню: один вопрос на обе подсистемы
rekey_all_menu() {
    local avail=""
    has_3xui_mode && is_warp_installed_3xui && avail+="3X-UI "
    has_awg_mode && awg_any_installed && avail+="AmneziaWG"
    if [ -z "$avail" ]; then
        echo -e "\n${RED}WARP не установлен — сначала п.1.${NC}"; read -r -p "Enter..."; return
    fi
    echo -e "\n${CYAN}━━━ Сменить WARP IP ━━━${NC}"
    echo -e "${WHITE}Будет перевыпущен: ${YELLOW}${avail}${NC}"
    has_3xui_mode && echo -e "  3X-UI:     ${CYAN}$(get_mode_text_3xui)${NC}"
    has_awg_mode  && echo -e "  AmneziaWG: ${CYAN}$(get_mode_text_awg)${NC}"
    echo -e "${DIM}Режим Free/WARP+ сохраняется (меняется в п.4). Клиенты через WARP кратковременно переподключатся.${NC}"
    local c; read -r -p "Продолжить? (y/n): " c
    [[ "$c" == "y" ]] || { echo -e "${YELLOW}Отменено.${NC}"; return; }
    if has_3xui_mode && is_warp_installed_3xui; then echo ""; echo -e "${CYAN}── 3X-UI ──${NC}"; rekey_3xui_core; fi
    if has_awg_mode && awg_any_installed; then echo ""; echo -e "${CYAN}── AmneziaWG ──${NC}"; rekey_awg_core; fi
    read -r -p "Enter..."
}

# Installation uses the same key picker without changing active tunnel defaults.
prompt_warp_key_mode_for_install() {
    local subsystem="$1" choice name
    INSTALL_KEY_MODE=""; INSTALL_KEY_NAME=""
    echo -e "${CYAN}━━━ Режим WARP для ${subsystem} ━━━${NC}"
    echo "  1) Free — бесплатный WARP"
    echo "  2) WARP+ — ключ подписки из приложения"
    echo "  0) Отмена"
    read -r -p "  Выбор: " choice || return 1
    case "$choice" in
        1) INSTALL_KEY_MODE=free ;;
        2) name=$(plus_choose_key) || return 1
           [ -n "$name" ] || return 1
           INSTALL_KEY_MODE=plus; INSTALL_KEY_NAME="$name" ;;
        *) return 1 ;;
    esac
}

# ═══════════════════════════════════════════════════════════════
#  LOGGING / SYSTEM
# ═══════════════════════════════════════════════════════════════

log_action() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$WARP_LOG"; }

bot_user_allowed() {
    local user_id="$1"
    [ -z "$user_id" ] && return 1
    if [ -n "${BOT_USER_IDS:-}" ]; then
        local _norm
        # P1 fix (self-audit): убираем whitespace ДО collapsing commas.
        # Иначе "12345 ,67890" остаётся со space внутри и не матчится.
        _norm=$(echo "$BOT_USER_IDS" | tr -d '[:space:]' | tr -s ',' | sed 's/^,//;s/,$//')
        echo ",${_norm}," | grep -q ",${user_id},"
    else
        [ "$user_id" = "${BOT_CHAT_ID:-}" ]
    fi
}

bot_chat_allowed() {
    local chat_id="$1" user_id="$2"
    [ -n "${BOT_CHAT_ID:-}" ] || return 1
    if [ "$chat_id" = "$BOT_CHAT_ID" ]; then
        bot_user_allowed "$user_id"
        return $?
    fi
    # Групповой BOT_CHAT_ID + BOT_USER_IDS: секретные флоу проходят в личке
    # allowlisted-пользователя, чтобы ключ не светился участникам группы.
    [ -n "${BOT_USER_IDS:-}" ] && [ "$chat_id" = "$user_id" ] && bot_user_allowed "$user_id"
}

check_root() {
    [ "$EUID" -ne 0 ] && { echo -e "${RED}[ERROR] Запустите от root!${NC}"; exit 1; }
}

check_deps() {
    for cmd in jq curl; do
        if ! command -v "$cmd" &>/dev/null; then
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -y > /dev/null 2>&1
            apt-get install -y jq curl > /dev/null 2>&1
            break
        fi
    done
}

get_my_ip() {
    MY_IP=$(curl -s4 --max-time 5 ifconfig.me 2>/dev/null || echo "N/A")
}

get_system_stats() {
    local cpu_line load_avg mem_info disk_info uptime_str cpu_usage
    cpu_line=$(grep -c ^processor /proc/cpuinfo 2>/dev/null || echo "?")
    load_avg=$(cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}')
    mem_info=$(free -m 2>/dev/null | awk '/^Mem:/ {printf "%d/%dMB (%.1f%%)", $3, $2, $3/$2*100}')
    disk_info=$(df -h / 2>/dev/null | awk 'NR==2 {printf "%s/%s (%s)", $3, $2, $5}')
    uptime_str=$(uptime -p 2>/dev/null || uptime | sed 's/.*up /up /' | sed 's/,.*load.*//')
    cpu_usage=$(awk '/^cpu / {u=$2+$4; t=$2+$3+$4+$5+$6+$7+$8; if(t>0) printf "%.1f", u/t*100; else print "0"}' /proc/stat 2>/dev/null)
    local r=""
    r+="<b>📊 Системная информация</b>\n\n"
    r+="<b>Uptime:</b> ${uptime_str}\n"
    r+="<b>CPU:</b> ${cpu_line} ядер | ${cpu_usage}%\n"
    r+="<b>Load:</b> ${load_avg}\n"
    r+="<b>RAM:</b> ${mem_info}\n"
    r+="<b>Disk /:</b> ${disk_info}\n"
    echo "$r"
}

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release; OS_ID="$ID"; OS_VERSION="$VERSION_ID"; OS_CODENAME="$VERSION_CODENAME"
    else
        OS_ID="unknown"
    fi
}

# ═══════════════════════════════════════════════════════════════
#  3X-UI BACKEND — WARP via warp-cli (SOCKS5)
# ═══════════════════════════════════════════════════════════════

is_warp_installed_3xui() { command -v warp-cli &>/dev/null; }

is_warp_running_3xui() {
    local st; st=$(warp-cli --accept-tos status 2>/dev/null)
    echo "$st" | grep -qi "status.*connected" && ! echo "$st" | grep -qi "disconnected"
}

get_warp_status_3xui() {
    if ! is_warp_installed_3xui; then echo "Не установлен"; return; fi
    local s; s=$(warp-cli --accept-tos status 2>/dev/null | head -5)
    if echo "$s" | grep -qi "disconnected"; then echo "Отключён"
    elif echo "$s" | grep -qi "connected"; then echo "Подключён"
    elif echo "$s" | grep -qi "registration missing"; then echo "Нет регистрации"
    else echo "Неизвестно"; fi
}

get_warp_ip_3xui() {
    curl -s4 --max-time 5 --proxy socks5h://127.0.0.1:${SOCKS_PORT} ifconfig.me 2>/dev/null || echo "N/A"
}

install_warp_3xui() {
    clear; echo -e "\n${CYAN}━━━ Установка Cloudflare WARP (3X-UI) ━━━${NC}\n"
    if is_warp_installed_3xui; then
        echo -e "${YELLOW}WARP уже установлен.${NC}"; read -p "Enter..."; return
    fi
    detect_os
    if [[ "$OS_ID" != "ubuntu" && "$OS_ID" != "debian" ]]; then
        echo -e "${RED}Поддерживаются только Ubuntu и Debian (ваша: ${OS_ID}).${NC}"; read -p "Enter..."; return
    fi

    # Выбор режима WARP (Free / WARP+) ДО установки пакета
    prompt_warp_key_mode_for_install 3xui || return 1
    save_config_val WARP_KEY_MODE_3XUI "$INSTALL_KEY_MODE" || return 1
    save_config_val ACTIVE_KEY_NAME_3XUI "$INSTALL_KEY_NAME" || return 1

    echo -e "${YELLOW}[1/6]${NC} GPG-ключ Cloudflare..."
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg 2>/dev/null \
        || { echo -e "${RED}Ошибка GPG.${NC}"; read -p "Enter..."; return; }
    echo -e "${GREEN}  ✓${NC}"

    echo -e "${YELLOW}[2/6]${NC} Репозиторий..."
    # P0 fix (self-audit on Ubuntu 26.04): Cloudflare WARP repo поддерживает
    # focal (20.04), jammy (22.04), noble (24.04). Для новых Ubuntu (resolute=26.04
    # и далее) пакета НЕТ — старый fallback на focal не работал из-за свежих
    # зависимостей. Делаем умный pick: сначала родной codename, потом noble→jammy→focal.
    local native_codename="${OS_CODENAME:-$(lsb_release -cs 2>/dev/null)}"
    local codename="" tested
    for tested in "$native_codename" noble jammy focal; do
        [ -z "$tested" ] && continue
        # Проверяем что в репо есть пакет для этого codename
        if curl -fsSL --max-time 5 "https://pkg.cloudflareclient.com/dists/${tested}/Release" >/dev/null 2>&1; then
            codename="$tested"
            break
        fi
    done
    if [ -z "$codename" ]; then
        echo -e "${RED}  ✗ Cloudflare WARP не поддерживает вашу версию Ubuntu (codename: ${native_codename:-unknown}).${NC}"
        echo -e "${WHITE}    Поддерживаемые: focal (20.04), jammy (22.04), noble (24.04).${NC}"
        read -p "Enter..."; return
    fi
    if [ "$codename" != "$native_codename" ]; then
        echo -e "${YELLOW}  ⚠ Native ${native_codename} нет в Cloudflare repo, использую ${codename} (совместимо).${NC}"
    fi
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ ${codename} main" \
        > /etc/apt/sources.list.d/cloudflare-client.list
    echo -e "${GREEN}  ✓ (${codename})${NC}"

    echo -e "${YELLOW}[3/6]${NC} Установка cloudflare-warp..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y > /dev/null 2>&1
    # Капчуим stderr на случай ошибок зависимостей — для лучшего диагноза
    local install_err; install_err=$(mktemp)
    apt-get install -y cloudflare-warp > /dev/null 2>"$install_err"
    if ! command -v warp-cli &>/dev/null; then
        local err_head; err_head=$(head -3 "$install_err" 2>/dev/null | tr '\n' ' ')
        rm -f "$install_err"
        echo -e "${RED}  ✗ Не удалось установить cloudflare-warp.${NC}"
        [ -n "$err_head" ] && echo -e "${DIM}    apt error: ${err_head}${NC}"
        read -p "Enter..."; return
    fi
    rm -f "$install_err"
    echo -e "${GREEN}  ✓${NC}"

    echo -e "${YELLOW}[4/6]${NC} Регистрация..."
    warp-cli --accept-tos registration new > /dev/null 2>&1 || { echo -e "${RED}Ошибка регистрации.${NC}"; read -p "Enter..."; return; }
    echo -e "${GREEN}  ✓${NC}"

    # Применяем WARP+ ключ, если выбран режим plus
    if [ "$WARP_KEY_MODE_3XUI" = "plus" ] && [ -n "$ACTIVE_KEY_NAME_3XUI" ]; then
        echo -e "${YELLOW}      Применение WARP+ ключа (${ACTIVE_KEY_NAME_3XUI})...${NC}"
        if apply_warp_plus_key_3xui; then
            echo -e "${GREEN}      ✓ WARP+ активирован${NC}"
        else
            source "$WARP_CONF"
            echo -e "${YELLOW}      ⚠ Не удалось применить ключ. WARP останется в Free.${NC}"
        fi
    fi

    echo -e "${YELLOW}[5/6]${NC} SOCKS5-прокси..."
    warp-cli --accept-tos mode proxy > /dev/null 2>&1
    warp-cli --accept-tos proxy port "${SOCKS_PORT}" > /dev/null 2>&1
    echo -e "${GREEN}  ✓ 127.0.0.1:${SOCKS_PORT}${NC}"

    echo -e "${YELLOW}[6/6]${NC} Подключение..."
    warp-cli --accept-tos connect > /dev/null 2>&1; sleep 3
    if is_warp_running_3xui; then
        local wip; wip=$(get_warp_ip_3xui)
        echo -e "${GREEN}  ✓ WARP IP: ${wip}${NC}"
        # Дополнительная независимая верификация Plus через cdn-cgi/trace
        if [ "$WARP_KEY_MODE_3XUI" = "plus" ]; then
            local trace_status; trace_status=$(verify_warp_plus_3xui)
            case "$trace_status" in
                plus) echo -e "  ${GREEN}✓ cdn-cgi/trace подтверждает: warp=plus${NC}"
                      log_action "3XUI INSTALL: cdn-cgi/trace verified PLUS" ;;
                free) echo -e "  ${YELLOW}⚠ cdn-cgi/trace показывает warp=on (Free) — ключ не применился${NC}"
                      log_action "3XUI INSTALL: cdn-cgi/trace says FREE despite mode=plus — rollback"
                      save_config_val "WARP_KEY_MODE_3XUI" "free"
                      save_config_val "ACTIVE_KEY_NAME_3XUI" "" ;;
                off)  echo -e "  ${YELLOW}⚠ cdn-cgi/trace показывает warp=off — трафик не через WARP${NC}"
                      log_action "3XUI INSTALL: cdn-cgi/trace says OFF" ;;
                "")   echo -e "  ${DIM}(cdn-cgi/trace недоступен — verify пропущен)${NC}" ;;
            esac
        fi
        log_action "3XUI INSTALL: port=${SOCKS_PORT}, warp_ip=${wip}"
        save_config_val WARP_WANTED_3XUI on
        health_service_install >/dev/null 2>&1 || log_action "HEALTH: служба не установлена"
    else
        echo -e "${YELLOW}  ⚠ Подключение не подтверждено.${NC}"
    fi
    echo -e "\n${WHITE}Настройка 3X-UI: п.${YELLOW}6${WHITE} (JSON, инструкция, порт)${NC}"
    read -p "Enter..."
}

start_warp_3xui() {
    is_warp_installed_3xui || { echo -e "\n${RED}WARP не установлен.${NC}"; read -r -p "Enter..."; return; }
    # Сначала намерение, потом действие: автопроверка не должна увидеть промежуточное состояние.
    save_config_val WARP_WANTED_3XUI on
    with_3xui_lock _start_3xui_locked
    read -r -p "Enter..."
}

_start_3xui_locked() {
    if is_warp_running_3xui; then echo -e "\n${YELLOW}Уже подключён.${NC}"; return 0; fi
    echo -e "\n${YELLOW}Подключение...${NC}"
    warp-cli --accept-tos mode proxy >/dev/null 2>&1
    warp-cli --accept-tos proxy port "$SOCKS_PORT" >/dev/null 2>&1
    warp-cli --accept-tos connect >/dev/null 2>&1; sleep 3
    if is_warp_running_3xui; then
        echo -e "${GREEN}[OK] Подключён.${NC}"; log_action "3XUI START"
    else
        echo -e "${RED}Ошибка подключения. Автопроверка продолжит попытки.${NC}"; return 1
    fi
}

stop_warp_3xui() {
    is_warp_installed_3xui || { echo -e "\n${RED}WARP не установлен.${NC}"; read -r -p "Enter..."; return; }
    save_config_val WARP_WANTED_3XUI off
    with_3xui_lock _stop_3xui_locked
    read -r -p "Enter..."
}

_stop_3xui_locked() {
    is_warp_running_3xui || { echo -e "\n${YELLOW}Уже отключён.${NC}"; return 0; }
    warp-cli --accept-tos disconnect >/dev/null 2>&1
    echo -e "${GREEN}[OK] Отключён. Автопроверка не будет включать его сама.${NC}"; log_action "3XUI STOP (manual)"
}

change_port_3xui() {
    is_warp_installed_3xui || { echo -e "\n${RED}WARP не установлен.${NC}"; read -p "Enter..."; return; }
    echo -e "\n${CYAN}━━━ Изменение порта ━━━${NC}\n"
    echo -e "${WHITE}Текущий: ${GREEN}${SOCKS_PORT}${NC}\n"
    local np
    while true; do
        read -p "Новый порт (1024-65535): " np
        [[ "$np" =~ ^[0-9]+$ ]] && (( np >= 1024 && np <= 65535 )) && break
        echo -e "${RED}Ошибка.${NC}"
    done
    warp-cli --accept-tos proxy port "$np" > /dev/null 2>&1
    save_config_val "SOCKS_PORT" "$np"; SOCKS_PORT="$np"
    echo -e "\n${GREEN}[OK] Порт: ${np}${NC}\n${YELLOW}Обновите порт в 3X-UI!${NC}"
    log_action "3XUI PORT: ${np}"; read -p "Enter..."
}

show_3xui_menu() {
    while true; do
        clear
        echo -e "\n${CYAN}━━━ Настройки SOCKS5 для 3X-UI ━━━${NC}\n"
        echo -e "  ${WHITE}SOCKS5-прокси:${NC} ${GREEN}127.0.0.1:${SOCKS_PORT}${NC}"
        if is_warp_running_3xui; then
            echo -e "  ${WHITE}WARP IP:${NC}       ${GREEN}$(get_warp_ip_3xui)${NC}"
        fi
        echo -e "\n  1) 📋 Показать JSON Outbound и Routing"
        echo -e "  2) 📖 Пошаговая инструкция для 3X-UI"
        echo -e "  3) 🔧 Изменить порт SOCKS5 (сейчас: ${SOCKS_PORT})"
        echo -e "  0) ⬅️  Назад"
        echo ""
        read -r -p "  Выбор: " ch || return 0
        case $ch in
            1) show_3xui_json ;;
            2) show_3xui_guide ;;
            3) change_port_3xui ;;
            0) return ;;
        esac
    done
}

show_3xui_json() {
    clear; echo -e "\n${CYAN}━━━ JSON для 3X-UI ━━━${NC}\n"
    echo -e "${WHITE}Добавьте в ${YELLOW}Xray Settings → Outbounds${WHITE} (JSON):${NC}\n"
    echo -e "${GREEN}── 1. Outbound (добавить в массив outbounds) ──${NC}\n"
    cat <<EOF
{
  "tag": "warp",
  "protocol": "socks",
  "settings": {
    "servers": [
      {
        "address": "127.0.0.1",
        "port": ${SOCKS_PORT}
      }
    ]
  }
}
EOF

    echo -e "\n${GREEN}── 2. Routing Rule (маршруты через WARP) ──${NC}\n"
    echo -e "${WHITE}Только определённые сайты через WARP:${NC}\n"
    cat <<EOF
{
  "outboundTag": "warp",
  "domain": [
    "geosite:openai",
    "geosite:netflix",
    "geosite:disney",
    "geosite:spotify",
    "domain:chat.openai.com",
    "domain:claude.ai"
  ]
}
EOF

    echo -e "\n${WHITE}Весь трафик через WARP:${NC}\n"
    cat <<EOF
{
  "outboundTag": "warp",
  "network": "tcp,udp"
}
EOF

    echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${WHITE}SOCKS5:${NC}  ${GREEN}127.0.0.1:${SOCKS_PORT}${NC}"
    if is_warp_running_3xui; then
        echo -e "  ${WHITE}WARP IP:${NC} ${GREEN}$(get_warp_ip_3xui)${NC}"
    fi
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""; read -p "Enter..."
}

show_3xui_guide() {
    clear; echo -e "\n${CYAN}━━━ Пошаговая настройка 3X-UI ━━━${NC}\n"

    echo -e "${YELLOW}═══ ДОБАВЛЕНИЕ OUTBOUND ═══${NC}\n"
    echo -e "${YELLOW}Шаг 1.${NC} Откройте панель 3X-UI в браузере"
    echo -e "${YELLOW}Шаг 2.${NC} Перейдите в ${WHITE}Настройки Xray${NC} → ${WHITE}Outbounds${NC}"
    echo -e "${YELLOW}Шаг 3.${NC} Нажмите ${GREEN}+ Добавить Outbound${NC}"
    echo -e "${YELLOW}Шаг 4.${NC} Заполните поля в форме:"
    echo -e ""
    echo -e "  ${WHITE}Тег (Tag):${NC}      ${GREEN}warp${NC}"
    echo -e "  ${WHITE}Протокол:${NC}       ${GREEN}SOCKS${NC}"
    echo -e "  ${WHITE}Адрес сервера:${NC}  ${GREEN}127.0.0.1${NC}"
    echo -e "  ${WHITE}Порт:${NC}           ${GREEN}${SOCKS_PORT}${NC}"
    echo -e ""
    echo -e "  ${DIM}Если панель требует JSON, вставьте в редактор:${NC}"
    echo -e "  ${CYAN}(пункт 1 основного подменю показывает полный JSON)${NC}"
    echo -e ""

    echo -e "${YELLOW}═══ МАРШРУТИЗАЦИЯ (по желанию) ═══${NC}\n"
    echo -e "  ${DIM}По умолчанию WARP не используется пока нет маршрутизации.${NC}"
    echo -e "  ${DIM}Добавьте правило, чтобы указать какие сайты идут через WARP:${NC}\n"
    echo -e "${YELLOW}Шаг 5.${NC} Перейдите в ${WHITE}Настройки Xray${NC} → ${WHITE}Routing Rules${NC}"
    echo -e "${YELLOW}Шаг 6.${NC} Нажмите ${GREEN}+ Добавить правило${NC}"
    echo -e "${YELLOW}Шаг 7.${NC} Заполните:"
    echo -e ""
    echo -e "  ${WHITE}Outbound Tag:${NC}   ${GREEN}warp${NC}"
    echo -e "  ${WHITE}Domain:${NC}"
    echo -e "    ${GREEN}geosite:openai${NC}"
    echo -e "    ${GREEN}geosite:netflix${NC}"
    echo -e "    ${GREEN}geosite:disney${NC}"
    echo -e "    ${GREEN}geosite:spotify${NC}"
    echo -e "    ${GREEN}domain:chat.openai.com${NC}"
    echo -e "    ${GREEN}domain:claude.ai${NC}"
    echo -e ""
    echo -e "  ${DIM}Каждый домен на отдельной строке.${NC}"
    echo -e "  ${DIM}Можно добавить свои домены или использовать network: tcp,udp для всего трафика.${NC}"
    echo -e ""

    echo -e "${YELLOW}═══ ПРИМЕНЕНИЕ ═══${NC}\n"
    echo -e "${YELLOW}Шаг 8.${NC} Нажмите ${WHITE}Сохранить${NC}"
    echo -e "${YELLOW}Шаг 9.${NC} Нажмите ${WHITE}Перезапустить Xray${NC} (или в SSH: ${GREEN}x-ui restart${NC})"
    echo -e ""

    echo -e "${YELLOW}═══ ПРОВЕРКА ═══${NC}\n"
    echo -e "  1. Подключитесь к VPN через клиент"
    echo -e "  2. Откройте ${CYAN}https://whoer.net${NC} или ${CYAN}https://ifconfig.me${NC}"
    echo -e "  3. Сайты из списка → ${GREEN}IP Cloudflare${NC}"
    echo -e "     Остальные сайты → ${WHITE}IP вашего сервера${NC}"
    echo -e ""
    echo -e "  ${WHITE}Проверка через SSH:${NC}"
    echo -e "  ${GREEN}curl -s --proxy socks5h://127.0.0.1:${SOCKS_PORT} ifconfig.me${NC}"
    echo ""; read -p "Enter..."
}

uninstall_3xui() {
    echo -e "\n${YELLOW}Удаление WARP (3X-UI)...${NC}\n"
    systemctl stop warp-bot 2>/dev/null; systemctl disable warp-bot 2>/dev/null
    rm -f /etc/systemd/system/warp-bot.service; systemctl daemon-reload 2>/dev/null
    [ -f "$BOT_PID_FILE" ] && { kill "$(cat "$BOT_PID_FILE")" 2>/dev/null; rm -f "$BOT_PID_FILE"; }
    echo -e "  ${GREEN}✓${NC}  Бот остановлен"
    warp-cli --accept-tos disconnect > /dev/null 2>&1; echo -e "  ${GREEN}✓${NC}  WARP отключён"
    warp-cli --accept-tos registration delete > /dev/null 2>&1; echo -e "  ${GREEN}✓${NC}  Регистрация удалена"
    # P1.2 fix (Codex 002): purge вместо remove — иначе остаётся в dpkg rc state
    apt-get purge -y cloudflare-warp > /dev/null 2>&1; apt-get autoremove -y > /dev/null 2>&1; echo -e "  ${GREEN}✓${NC}  Пакет удалён (purge)"
    rm -f /etc/apt/sources.list.d/cloudflare-client.list /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
    echo -e "  ${GREEN}✓${NC}  Репозиторий удалён"
}

# ═══════════════════════════════════════════════════════════════
#  AMNEZIA BACKEND — WARP via wgcf (WireGuard inside Docker)
# ═══════════════════════════════════════════════════════════════

# rc=0 — контейнеры есть; rc=1 — контейнеров AmneziaWG нет (или нет самого Docker); rc=2 — Docker не ответил.
# Удаление различает 1 и 2: без контейнеров оно чистит хост, а без ответа Docker останавливается.
awg_discover_containers() {
    local include_stopped="${1:-running}" ps_flag="" names=""
    [ "$include_stopped" = "all" ] && ps_flag="-a"
    AWG_ALL_CONTAINERS=()
    # P0.1 fix (self-audit on Codex aa395f9): убран fallback `grep -i "amnezia"`
    # — он матчил произвольные контейнеры (amnezia-helper, amnezia-dns, etc),
    # из-за чего uninstall_awg all мог уничтожить чужие контейнеры пользователя.
    # Допустимый whitelist: только amnezia-awg, amnezia-awg2, и явные варианты v3+.
    if command -v docker >/dev/null 2>&1 && ! names=$(docker ps $ps_flag --format '{{.Names}}' 2>/dev/null); then
        echo -e "${RED}Docker не отвечает: список контейнеров не получен.${NC}"
        return 2
    fi
    mapfile -t AWG_ALL_CONTAINERS < <(printf '%s\n' "$names" | grep -E '^amnezia-awg[0-9]*$' || true)
    if [ ${#AWG_ALL_CONTAINERS[@]} -eq 0 ]; then
        echo -e "${RED}Контейнеры amnezia-awg / amnezia-awg2 не найдены.${NC}"
        return 1
    fi
    CONTAINERS="${AWG_ALL_CONTAINERS[*]}"
    save_config_val "CONTAINERS" "$CONTAINERS"
    return 0
}

awg_pick_container() {
    awg_discover_containers || return 1
    if [ -n "${CONTAINER:-}" ]; then
        docker exec "$CONTAINER" sh -c "true" 2>/dev/null && return 0
    fi
    CONTAINER="${AWG_ALL_CONTAINERS[0]}"
    save_config_val "CONTAINER" "$CONTAINER"
    return 0
}

awg_select_container() {
    awg_discover_containers 2>/dev/null || return 1
    if [ ${#AWG_ALL_CONTAINERS[@]} -eq 1 ]; then
        CONTAINER="${AWG_ALL_CONTAINERS[0]}"
        awg_load_container_data
        return 0
    fi
    echo -e "\n${CYAN}Выберите контейнер:${NC}\n"
    local i=1
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        local warp_status="нет"
        docker exec "$c" sh -c "[ -f '/opt/warp/warp.conf' ]" 2>/dev/null && warp_status="да"
        local clients="0"
        clients=$(docker exec "$c" sh -c "cat /opt/warp/clients.list 2>/dev/null | grep -c '.' || true" 2>/dev/null | tr -d '\r\n')
        local mark=""
        [ "$c" = "${CONTAINER:-}" ] && mark=" *"
        echo -e "  ${GREEN}$i)${NC} $c  (WARP: $warp_status, клиентов: $clients)${mark}"
        ((i++))
    done
    echo -e "\n  ${DIM}* = текущий${NC}"
    echo -e "  ${DIM}0) Отмена${NC}"
    echo ""
    while true; do
        read -p "  > " ch
        [ "$ch" = "0" ] && return 1
        if [[ "$ch" =~ ^[0-9]+$ ]] && (( ch >= 1 && ch <= ${#AWG_ALL_CONTAINERS[@]} )); then
            CONTAINER="${AWG_ALL_CONTAINERS[$((ch-1))]}"
            save_config_val "CONTAINER" "$CONTAINER"
            awg_load_container_data
            return 0
        fi
    done
}

# Где искать конфиг сервера VPN: у amnezia-awg2 (AmneziaWG 3.1) сначала awg0.conf, у остальных — wg0.conf.
# Тот же порядок у блока в start.sh и у автопроверки: адреса клиентов по ключам везде берутся из одного файла.
awg_vpn_conf_candidates() {
    local first=/opt/amnezia/awg/wg0.conf
    [ "${1:-}" != amnezia-awg2 ] || first=/opt/amnezia/awg/awg0.conf
    echo "$first /opt/amnezia/awg/wg0.conf /opt/amnezia/awg/awg0.conf /etc/wireguard/wg0.conf"
}

awg_load_container_data() {
    AWG_CLIENTS_TABLE="/opt/amnezia/awg/clientsTable"
    AWG_START_SH="/opt/amnezia/start.sh"
    AWG_VPN_CONF=$(docker exec "$CONTAINER" sh -c 'for f in $1; do [ -f "$f" ] && { echo "$f"; exit 0; }; done; exit 1' \
        sh "$(awg_vpn_conf_candidates "$CONTAINER")" 2>/dev/null | tr -d '\r')
    if [ -z "$AWG_VPN_CONF" ]; then
        echo -e "${RED}Не найден конфиг VPN в контейнере ${CONTAINER}${NC}"
        return 1
    fi

    # AWG 2 and 3.1 both use awg0.conf; container numbering is not a protocol version.
    AWG_VPN_IF="${AWG_VPN_CONF##*/}"
    AWG_VPN_IF="${AWG_VPN_IF%.conf}"
    case "$AWG_VPN_IF" in
        awg*) AWG_VPN_QUICK_CMD=awg-quick ;;
        *) AWG_VPN_QUICK_CMD=wg-quick ;;
    esac

    AWG_SUBNET=$(docker exec "$CONTAINER" sh -c "sed -n 's/^Address = \(.*\)$/\1/p' '$AWG_VPN_CONF' | head -n1 | cut -d',' -f1" 2>/dev/null | tr -d '\r')
    return 0
}

awg_detect_warp_exit_ip() {
    AWG_WARP_EXIT_IP=""
    if docker exec "$CONTAINER" sh -c "ip addr show warp >/dev/null 2>&1" 2>/dev/null; then
        AWG_WARP_EXIT_IP=$(docker exec "$CONTAINER" sh -c \
            "curl -4 -s --interface warp --connect-timeout 3 --max-time 5 https://ifconfig.me 2>/dev/null || true" | tr -d '\r\n')
    fi
}

is_warp_installed_awg() {
    docker exec "$CONTAINER" sh -c "[ -f '$AWG_WARP_CONF' ]" 2>/dev/null
}

is_warp_running_awg() {
    docker exec "$CONTAINER" sh -c "ip addr show warp >/dev/null 2>&1" 2>/dev/null
}

get_warp_status_awg() {
    if ! is_warp_installed_awg; then echo "Не установлен"; return; fi
    is_warp_running_awg && echo "Подключён" || echo "Отключён"
}

wgcf_expected_sha256() {
    case "$1" in
        amd64) echo "$WGCF_SHA256_AMD64" ;;
        arm64) echo "$WGCF_SHA256_ARM64" ;;
        armv7) echo "$WGCF_SHA256_ARMV7" ;;
        *) return 1 ;;
    esac
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# Ставит wgcf ровно той сборки, что закреплена SHA-256. Прежняя версия, лежащая
# на сервере, заменяется атомарно; при сбое загрузки или сверки остаётся как была.
awg_install_wgcf() {
    local wa want have tmp
    case "$(uname -m)" in
        x86_64) wa="amd64" ;; aarch64) wa="arm64" ;; armv7l) wa="armv7" ;;
        *) echo -e "${RED}Архитектура не поддерживается: $(uname -m)${NC}" >&2; return 1 ;;
    esac
    want=$(wgcf_expected_sha256 "$wa") || return 1
    if [ -x "$WGCF_BIN" ] && [ "$(file_sha256 "$WGCF_BIN" 2>/dev/null)" = "$want" ]; then return 0; fi
    tmp=$(mktemp "${WGCF_BIN}.new.XXXXXX") || return 1
    if ! curl -fsSL --max-time 120 -o "$tmp" \
        "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_${wa}"; then
        rm -f "$tmp"; echo -e "${RED}Не удалось скачать wgcf ${WGCF_VERSION}.${NC}" >&2; return 1
    fi
    have=$(file_sha256 "$tmp" 2>/dev/null)
    if [ "$have" != "$want" ]; then
        rm -f "$tmp"
        echo -e "${RED}Контрольная сумма wgcf ${WGCF_VERSION} не совпала, файл отброшен.${NC}" >&2
        log_action "WGCF: checksum mismatch for v${WGCF_VERSION} (${wa}), download discarded"
        return 1
    fi
    chmod 755 "$tmp" && mv -f "$tmp" "$WGCF_BIN" || { rm -f "$tmp"; return 1; }
    log_action "WGCF: installed v${WGCF_VERSION} (${wa})"
}

# Cloudflare выдаёт новое устройство wgcf с одного IP не чаще раза в минуту, иначе отвечает 429.
# Когда контейнеров несколько, регистрации идут подряд: выдерживаем паузу и повторяем после 429.
awg_register_wait() {
    local last now left
    last=$(cat "$WARP_DIR/.wgcf-last-register" 2>/dev/null)
    [[ "$last" =~ ^[0-9]+$ ]] || { echo 0; return; }
    now=$(date +%s); left=$((last + AWG_REGISTER_GAP - now))
    { [ "$left" -gt 0 ] && [ "$left" -le "$AWG_REGISTER_GAP" ]; } || left=0
    echo "$left"
}

awg_ensure_account() {
    [ ! -f "$WGCF_ACCOUNT" ] || return 0
    local errf try wait left
    AWG_REGISTER_ERROR=""
    errf=$(mktemp -p "$WARP_DIR") || return 1
    chmod 600 "$errf"
    for try in 1 2 3 4; do
        wait=0; [ "$try" = 1 ] || wait=30
        left=$(awg_register_wait); [ "$left" -le "$wait" ] || wait=$left
        if [ "$wait" -gt 0 ]; then
            echo "  Cloudflare выдаёт новое устройство не чаще раза в минуту, жду ${wait} с…" >&2
            sleep "$wait"
        fi
        echo -e "${YELLOW}Регистрация WARP через wgcf...${NC}"
        # printf закрывает ввод после одной строки: `yes |` под pipefail давал rc=141.
        (cd "$WGCF_DIR" && printf 'y\n' | "$WGCF_BIN" register >/dev/null 2>"$errf")
        if [ -f "$WGCF_ACCOUNT" ]; then
            date +%s > "$WARP_DIR/.wgcf-last-register" 2>/dev/null || true
            rm -f "$errf"
            return 0
        fi
        grep -q '429' "$errf" || break
    done
    if grep -q '429' "$errf"; then
        AWG_REGISTER_ERROR="Cloudflare не выдал новое устройство: слишком частые регистрации, повторите через пару минут"
    else
        AWG_REGISTER_ERROR="Cloudflare не выдал новое устройство"
    fi
    rm -f "$errf"
    log_action "AWG REGISTER: ${AWG_REGISTER_ERROR}"
    echo -e "${RED}Не создан $WGCF_ACCOUNT${NC}"
    return 1
}

awg_generate_profile() {
    # P0.5: wgcf generate не требует input, без пайпа
    # P1.2 (Codex 004): удаляем старый profile до generate — иначе stale
    # файл может замаскировать failure (мы проверяли только file existence).
    rm -f "$WGCF_PROFILE"
    (cd "$WGCF_DIR" && "$WGCF_BIN" generate 2>/dev/null)
    local rc=$?
    if [ ! -f "$WGCF_PROFILE" ] || [ $rc -ne 0 ]; then
        log_action "AWG GENERATE: failed (rc=$rc, profile_exists=$([ -f "$WGCF_PROFILE" ] && echo yes || echo no))"
        echo -e "${RED}Не создан профиль (rc=$rc).${NC}"
        return 1
    fi
    return 0
}

awg_resolve_endpoint() {
    local ep; ep=$(getent ahostsv4 engage.cloudflareclient.com 2>/dev/null | awk 'NR==1{print $1}')
    [ -z "$ep" ] && { echo -e "${RED}Не удалось определить IP endpoint.${NC}" >&2; return 1; }
    echo "$ep"
}

# Endpoint WARP контейнера: из его warp.conf (адрес и порт, уже проверенные на этом сервере), иначе — адрес
# Cloudflare с портом 2408. Строку, не похожую на «адрес:порт» (например, текст ошибки DNS), не берём.
awg_container_endpoint() {
    local ep
    ep=$(docker exec "$1" sh -c "sed -n 's/^Endpoint = //p' '$AWG_WARP_CONF'" 2>/dev/null | head -1 | tr -d '\r')
    if ! [[ "$ep" =~ ^[A-Za-z0-9.-]+:[0-9]{1,5}$ ]]; then
        ep=$(awg_resolve_endpoint) || return 1
        ep="${ep}:2408"
    fi
    echo "$ep"
}

awg_build_warp_conf() {
    local endpoint_ip="$1" listen_port=""
    [[ "$endpoint_ip" == *:* ]] || endpoint_ip="${endpoint_ip}:2408"
    listen_port=$(docker exec "$CONTAINER" sh -c "sed -n 's/^ListenPort = //p' '$AWG_WARP_CONF'" 2>/dev/null | head -1)
    if [ -z "$listen_port" ] || [ "$listen_port" = 0 ]; then
        listen_port=$(docker exec "$CONTAINER" sh -c 'env PATH=/opt/warp-tools/usr/bin:$PATH wg show warp listen-port' 2>/dev/null)
    fi
    [[ "$listen_port" =~ ^[0-9]+$ ]] || listen_port=0
    local pk pub addr
    pk=$(awk -F' = ' '/^PrivateKey = /{print $2}' "$WGCF_PROFILE")
    pub=$(awk -F' = ' '/^PublicKey = /{print $2}' "$WGCF_PROFILE")
    addr=$(awk -F' = ' '/^Address = /{print $2}' "$WGCF_PROFILE" | cut -d',' -f1)

    [ -n "$pk" ] && [ -n "$pub" ] && [ -n "$addr" ] || return 1
    docker exec "$CONTAINER" sh -c "mkdir -p '$AWG_WARP_DIR'" || return 1
    docker cp "$WGCF_PROFILE" "${CONTAINER}:${AWG_WARP_DIR}/wgcf-profile.conf" 2>/dev/null || return 1
    # Конфиг с приватным ключом идёт через stdin: в аргументах docker exec его видел бы ps на хосте.
    docker exec -i "$CONTAINER" sh -c 'set -e; tmp=$(mktemp "$1.XXXXXX"); trap "rm -f \"$tmp\"" EXIT; cat > "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$1"' \
        sh "$AWG_WARP_CONF" <<WARPEOF
[Interface]
PrivateKey = ${pk}
Address = ${addr}
MTU = 1280
Table = off
ListenPort = ${listen_port}

[Peer]
PublicKey = ${pub}
AllowedIPs = 0.0.0.0/0
Endpoint = ${endpoint_ip}
PersistentKeepalive = 25
WARPEOF
}

# Keep Amnezia's wg/awg symlinks intact; install separate standard WG tools for WARP.
# WARP в контейнере поднимается стандартными wg и wg-quick из пакетов Alpine (/opt/warp-tools):
# тогда интерфейс создаёт модуль ядра wireguard. Инструменты Amnezia создают интерфейс amneziawg,
# а на модуле amneziawg 1.0 ответы Cloudflare приходят с задержкой до секунд. В старых образах
# Amnezia пропатченные инструменты называются просто wg, по имени их не отличить — ставим всегда.
awg_prepare_warp_tools() {
    local now; now=$(date +%s)
    # Недавняя неудача: не качать заново на каждом вызове (перевыпуск зовёт дважды подряд).
    [ $((now - ${AWG_TOOLS_FAILED_AT[$CONTAINER]:-0})) -ge 600 ] || return 1
    # shellcheck disable=SC2086
    if docker exec -i "$CONTAINER" sh -s -- $AWG_ALPINE_MIRRORS <<'TOOLS'
command -v flock >/dev/null || apk add --no-cache util-linux-misc >/dev/null 2>&1 || exit 1
[ -x /opt/warp-tools/usr/bin/wg-quick ] && [ -x /opt/warp-tools/usr/bin/wg ] && exit 0
command -v apk >/dev/null || exit 1
# Не дольше минуты: скачивание может стоять на пути подъёма упавшего туннеля.
t=""; command -v timeout >/dev/null && t="timeout 20"
end=$(( $(date +%s) + 60 ))
ver=$(cut -d. -f1,2 /etc/alpine-release 2>/dev/null)
mkdir -p /opt/warp-tools/packages && cd /opt/warp-tools/packages || exit 1
got() { ls ./wireguard-tools-wg-[0-9]*.apk ./wireguard-tools-wg-quick-[0-9]*.apk >/dev/null 2>&1; }
rm -f ./*.apk
# Сначала репозитории контейнера, затем зеркала: основной адрес Alpine бывает недоступен.
$t apk update >/dev/null 2>&1 && $t apk fetch wireguard-tools-wg wireguard-tools-wg-quick >/dev/null 2>&1
for m in "$@"; do
    got && break
    [ "$(date +%s)" -lt "$end" ] || break
    rm -f ./*.apk
    [ -n "$ver" ] || break
    $t apk fetch --no-cache --repositories-file /dev/null -X "$m/v$ver/main" wireguard-tools-wg wireguard-tools-wg-quick >/dev/null 2>&1
done
got || exit 1
apk verify ./*.apk >/dev/null 2>&1 || { rm -f ./*.apk; exit 1; }
for f in ./*.apk; do tar -xzf "$f" -C /opt/warp-tools usr/bin 2>/dev/null; done
/opt/warp-tools/usr/bin/wg --version >/dev/null && test -x /opt/warp-tools/usr/bin/wg-quick
TOOLS
    then
        unset "AWG_TOOLS_FAILED_AT[$CONTAINER]"
        return 0
    fi
    AWG_TOOLS_FAILED_AT[$CONTAINER]=$now
    log_action "AWG: ${CONTAINER} — стандартные инструменты WireGuard не скачались ни из репозитория контейнера, ни с зеркал Alpine"
    return 1
}

# Поднять WARP в контейнере. Прежний интерфейс снимается любого типа: стандартный wg-quick не трогает
# amneziawg и tun от Amnezia, которые остаются, например, от 2.3. Поднимаем стандартными инструментами;
# если их нет или они не справились — инструментами контейнера, чтобы WARP не остался лежать.
# Тип получившегося интерфейса — в AWG_WARP_KIND.
awg_warp_up() {
    local tools=1 out
    awg_prepare_warp_tools || tools=0
    AWG_WARP_KIND=""
    out=$(docker exec -i "$CONTAINER" sh -s -- "$AWG_WARP_CONF" "${1:-restart}" "$tools" <<'UP'
set -e
conf=$1
exec 9>/tmp/gowarp-runtime.lock
flock -x 9
rm -f /opt/warp/disabled
kind() { k=$(sed -n 's/^DEVTYPE=//p' /sys/class/net/warp/uevent 2>/dev/null); [ -n "$k" ] || { [ -e /sys/class/net/warp/tun_flags ] && k=tun || k=other; }; echo "$k"; }
own() { if command -v awg-quick >/dev/null 2>&1; then awg-quick "$@"; else /usr/bin/wg-quick "$@"; fi; }
if [ "$2" = ensure ] && ip link show warp >/dev/null 2>&1; then kind; exit 0; fi
if ip link show warp >/dev/null 2>&1; then
    if [ "$(kind)" = wireguard ]; then
        env PATH=/opt/warp-tools/usr/bin:$PATH wg-quick down "$conf" 9>&- >/dev/null 2>&1 || true
    else
        own down "$conf" 9>&- >/dev/null 2>&1 || true
    fi
    ! ip link show warp >/dev/null 2>&1 || ip link del warp
fi
if [ "$3" = 1 ] && env PATH=/opt/warp-tools/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin wg-quick up "$conf" 9>&- >&2; then
    :
else
    ! ip link show warp >/dev/null 2>&1 || ip link del warp
    own up "$conf" 9>&- >&2
fi
ip link show warp >/dev/null
kind
UP
    ) || return 1
    AWG_WARP_KIND=${out##*$'\n'}
    if [ "$AWG_WARP_KIND" != wireguard ]; then
        log_action "AWG: ${CONTAINER} — WARP поднят не на стандартном WireGuard (тип ${AWG_WARP_KIND:-?}): стандартные инструменты недоступны или не справились"
    fi
    return 0
}

awg_warp_down() {
    docker exec -i "$CONTAINER" sh -s -- "$AWG_WARP_CONF" <<'DOWN'
set -e
conf=$1
exec 9>/tmp/gowarp-runtime.lock
flock -x 9
mkdir -p /opt/warp
touch /opt/warp/disabled
ip link show warp >/dev/null 2>&1 || exit 0
# Стандартный wg-quick не снимает amneziawg и tun от Amnezia: для них — инструменты контейнера.
if [ "$(sed -n 's/^DEVTYPE=//p' /sys/class/net/warp/uevent 2>/dev/null)" = wireguard ]; then
    env PATH=/opt/warp-tools/usr/bin:$PATH wg-quick down "$conf" 9>&- >/dev/null 2>&1 || true
elif command -v awg-quick >/dev/null 2>&1; then
    awg-quick down "$conf" 9>&- >/dev/null 2>&1 || true
else
    /usr/bin/wg-quick down "$conf" 9>&- >/dev/null 2>&1 || true
fi
! ip link show warp >/dev/null 2>&1 || ip link del warp
DOWN
}

# WARP не на стандартном wireguard, а на модуле amneziawg или userspace-интерфейсе tun (amneziawg-go):
# такой пересобирается, как только есть стандартные инструменты.
awg_kind_needs_rebuild() { case "$1" in amneziawg|tun) return 0 ;; esac; return 1; }
# Ядро создаёт интерфейсы wireguard (модуль есть и грузится из контейнера)? Без этого пересборка бесполезна:
# стандартный wg-quick не поднимет интерфейс, WARP снова встанет инструментами контейнера, а туннель зря дёрнется.
# Бывает на LXC/OpenVZ и старых ядрах: там WARP остаётся на tun.
awg_kernel_wireguard_ok() {
    health_docker exec "$1" sh -c 'ip link add gowarp-probe type wireguard 2>/dev/null || exit 1; ip link del gowarp-probe 2>/dev/null; exit 0' \
        >/dev/null 2>&1
}
awg_kind_text() {
    case "$1" in
        amneziawg) echo "модуль amneziawg (задержка до секунд)" ;;
        tun) echo "userspace-интерфейс tun" ;;
        *) echo "$1" ;;
    esac
}

# Тип WARP-интерфейса в контейнере: wireguard, amneziawg, tun, other или none.
awg_warp_kind() {
    docker exec "$1" sh -c 'k=$(sed -n "s/^DEVTYPE=//p" /sys/class/net/warp/uevent 2>/dev/null); [ -n "$k" ] || { if [ -e /sys/class/net/warp/tun_flags ]; then k=tun; elif [ -e /sys/class/net/warp ]; then k=other; else k=none; fi; }; echo "$k"' 2>/dev/null
}

install_warp_awg() {
    clear; echo "Установка WARP для AmneziaWG"
    awg_discover_containers || return 1
    prompt_warp_key_mode_for_install awg || return 1
    awg_install_wgcf || return 1
    rekey_awg_core "$INSTALL_KEY_MODE" "$INSTALL_KEY_NAME" install
    local rc=$?
    read -r -p "Enter..."
    return "$rc"
}

awg_set_running() {
    local action="$1" saved="${CONTAINER:-}" c failed=0 operation_fd
    [[ "$action" = on || "$action" = off ]] || return 1
    exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    flock -n "$operation_fd" || { exec {operation_fd}>&-; return 1; }
    awg_discover_containers >/dev/null 2>&1 || { exec {operation_fd}>&-; return 1; }
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        CONTAINER="$c"
        awg_load_container_data >/dev/null 2>&1 || { failed=1; continue; }
        docker exec "$c" test -f "$AWG_WARP_CONF" || continue
        if [ "$action" = on ]; then
            awg_warp_up ensure >/dev/null 2>&1 && awg_load_clients && awg_apply_rules && awg_patch_start_sh || failed=1
        else
            awg_warp_down || failed=1
        fi
        echo "$c: $action"
    done
    CONTAINER="$saved"
    exec {operation_fd}>&-
    return "$failed"
}

start_warp_awg() {
    awg_set_running on
    read -r -p "Enter..."
}

stop_warp_awg() {
    awg_set_running off
    read -r -p "Enter..."
}

awg_pick_targets() {
    # Спрашивает у юзера: текущий контейнер ИЛИ все ИЛИ конкретный.
    # Заполняет глобальный массив AWG_TARGETS.
    AWG_TARGETS=()
    awg_discover_containers 2>/dev/null
    if [ ${#AWG_ALL_CONTAINERS[@]} -eq 0 ]; then
        return 1
    fi
    if [ ${#AWG_ALL_CONTAINERS[@]} -eq 1 ]; then
        AWG_TARGETS=("${AWG_ALL_CONTAINERS[0]}")
        return 0
    fi
    echo -e "${CYAN}Выберите цель:${NC}"
    local i=1
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        local mark=""
        [ "$c" = "${CONTAINER:-}" ] && mark=" ${DIM}(текущий)${NC}"
        local has_warp="нет"
        docker exec "$c" sh -c "[ -f '/opt/warp/warp.conf' ]" 2>/dev/null && has_warp="да"
        echo -e "  ${WHITE}${i})${NC} ${c} ${DIM}(WARP: ${has_warp})${NC}${mark}"
        ((i++))
    done
    echo -e "  ${WHITE}${i})${NC} ${YELLOW}Все контейнеры${NC}"
    echo -e "  ${WHITE}0)${NC} Отмена"
    local choice
    read -p "  > " choice
    if [ "$choice" = "0" ]; then return 1; fi
    if [ "$choice" = "$i" ]; then
        AWG_TARGETS=("${AWG_ALL_CONTAINERS[@]}")
        return 0
    fi
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice < i )); then
        AWG_TARGETS=("${AWG_ALL_CONTAINERS[$((choice-1))]}")
        return 0
    fi
    return 1
}

# Снимок контейнера на время перевыпуска и смены клиентов — на хосте, рядом с копией регистрации:
# warp.tar (/opt/warp целиком), was-up (WARP был поднят), ready. goAmnezia пересоздаёт контейнеры, и снимок
# в /tmp контейнера пропадал вместе с ним. start.sh не копируем: при откате свой блок вставляется заново,
# а остальное в start.sh — правки Amnezia и goAmnezia, их откат не трогает.
awg_ct_snap_dir() { echo "$HOST_WGCF_BAK_ROOT/$1/container"; }

awg_snapshot_container() {
    local c="$1" snap
    snap=$(awg_ct_snap_dir "$c")
    # Незавершённый откат (свой или перевыпуска) держит прежний снимок — поверх не пишем.
    [ ! -f "$snap/recovery-required" ] && [ ! -f "${snap%/*}/recovery-required" ] || return 1
    rm -rf "$snap" && mkdir -p "$snap" && chmod 700 "${snap%/*}" "$snap" || return 1
    # Снимок версий до 2.5.2 в /tmp контейнера здесь уже не нужен.
    if ! docker exec "$c" sh -c "rm -rf '$AWG_LEGACY_SNAP'; [ ! -d '$AWG_WARP_DIR' ] || tar -C '${AWG_WARP_DIR%/*}' -cf - '${AWG_WARP_DIR##*/}'" \
            > "$snap/warp.tar" 2>/dev/null || ! chmod 600 "$snap/warp.tar"; then
        rm -rf "$snap"; return 1
    fi
    [ -s "$snap/warp.tar" ] || rm -f "$snap/warp.tar"
    if docker exec "$c" ip link show warp >/dev/null 2>&1; then touch "$snap/was-up"; fi
    touch "$snap/ready"
}

awg_restore_container_snapshot() {
    local c="$1" snap
    snap=$(awg_ct_snap_dir "$c")
    CONTAINER="$c"
    awg_load_container_data >/dev/null 2>&1 || return 1
    [ -f "$snap/ready" ] || return 1
    awg_warp_down || return 1
    if [ -f "$snap/warp.tar" ]; then
        docker exec -i "$c" sh -c "rm -rf '$AWG_WARP_DIR' && mkdir -p '${AWG_WARP_DIR%/*}' && tar -C '${AWG_WARP_DIR%/*}' -xf -" \
            < "$snap/warp.tar" || return 1
        awg_patch_start_sh || return 1
    else
        docker exec "$c" rm -rf "$AWG_WARP_DIR" || return 1
        awg_remove_from_start_sh || return 1
    fi
    if [ -f "$snap/was-up" ]; then
        awg_warp_up >/dev/null 2>&1 && awg_load_clients && awg_apply_rules || return 1
    else
        awg_cleanup_rules
    fi
}

awg_clear_container_snapshot() {
    rm -rf "$(awg_ct_snap_dir "$1")" 2>/dev/null || true
    rmdir "$HOST_WGCF_BAK_ROOT/$1" 2>/dev/null || true
}

# Смена клиентов не откатилась до конца (метка только у снимка контейнера, без метки перевыпуска): вернуть
# сохранённое. Выключенный с тех пор руками WARP остаётся выключенным. rc=1 — откат снова не удался.
awg_finish_client_rollback() {
    local c="$1" snap off=0
    snap=$(awg_ct_snap_dir "$c")
    [ -f "$snap/recovery-required" ] && [ ! -f "${snap%/*}/recovery-required" ] || return 0
    docker exec "$c" test -f "$AWG_WARP_DIR/disabled" >/dev/null 2>&1 && off=1
    awg_restore_container_snapshot "$c" >/dev/null 2>&1 || return 1
    [ "$off" = 0 ] || awg_warp_down >/dev/null 2>&1 || true
    awg_clear_container_snapshot "$c"
    log_action "AWG CLIENTS: unresolved rollback completed for $c"
}

# Откат не завершился: снимок остаётся, поверх него новый не пишется.
awg_mark_container_recovery() {
    local snap; snap=$(awg_ct_snap_dir "$1")
    [ ! -d "$snap" ] || touch "$snap/recovery-required"
}

# Перевыпуск, прерванный до 2.5.2, оставил снимок в /tmp контейнера: перенести его на хост.
awg_import_legacy_snapshot() {
    local c="$1" snap list f
    snap=$(awg_ct_snap_dir "$c")
    [ ! -f "$snap/ready" ] || return 0
    list=$(docker exec "$c" sh -c "cd '$AWG_LEGACY_SNAP' 2>/dev/null && [ -f ready ] && ls" 2>/dev/null) || return 0
    rm -rf "$snap" && mkdir -p "$snap" && chmod 700 "${snap%/*}" "$snap" || return 1
    if [[ $'\n'"$list"$'\n' == *$'\n'warp$'\n'* ]]; then
        if ! docker exec "$c" tar -C "$AWG_LEGACY_SNAP" -cf - warp > "$snap/warp.tar" 2>/dev/null || ! chmod 600 "$snap/warp.tar"; then
            rm -rf "$snap"; return 1
        fi
    fi
    for f in was-up recovery-required; do
        [[ $'\n'"$list"$'\n' != *$'\n'"$f"$'\n'* ]] || touch "$snap/$f"
    done
    touch "$snap/ready"
    docker exec "$c" rm -rf "$AWG_LEGACY_SNAP" >/dev/null 2>&1 || true
    log_action "AWG: ${c} — снимок прерванного перевыпуска перенесён из /tmp контейнера на хост"
}

# P0.2 fix (self-audit on Codex aa395f9): in-container snapshot не покрывает
# host wgcf-account/profile. Если rekey удалил их + сменил Plus-ключ + один
# контейнер упал — его восстановленный warp.conf использует СТАРЫЕ private-key,
# которых уже нет в Cloudflare → контейнер silently умрёт при следующем reboot.
# Поэтому делаем атомарный host-snapshot ДО mutate.
HOST_WGCF_BAK_ROOT="/var/lib/warp-manager/rekey-bak"
HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"

# v2.4 (живой VPS 2026-09-05): раньше ВСЕ контейнеры получали один PrivateKey из
# /root/wgcf-profile.conf. Cloudflare держит одну WireGuard-сессию на ключ, поэтому
# два контейнера выбивали друг друга — это и ощущалось как «потери пакетов»
# (один контейнер: 5/5 warp=plus; подняли второй с тем же ключом — оба LOSS).
# Теперь каждый контейнер регистрирует своё устройство (лимит Cloudflare: 5 на ключ).
awg_wgcf_ctx() {
    local c="$1"
    WGCF_DIR="$AWG_WGCF_BASE/$c"
    [[ "$c" =~ ^amnezia-awg[0-9]*$ ]] || return 1
    mkdir -p "$WGCF_DIR" 2>/dev/null || return 1
    chmod 700 "$AWG_WGCF_BASE" "$WGCF_DIR" 2>/dev/null || return 1
    WGCF_ACCOUNT="$WGCF_DIR/wgcf-account.toml"
    WGCF_PROFILE="$WGCF_DIR/wgcf-profile.conf"
    HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT/$c"
}
awg_wgcf_ctx_reset() {
    WGCF_DIR="/root"
    WGCF_ACCOUNT="/root/wgcf-account.toml"
    WGCF_PROFILE="/root/wgcf-profile.conf"
    HOST_WGCF_BAK_DIR="$HOST_WGCF_BAK_ROOT"
}
# rc=0, если ≥2 контейнера с WARP используют один и тот же публичный ключ warp
# rc=0, если ≥2 контейнера с WARP используют один ключ (так ставила 2.3). Ключ сравниваем по хэшу
# из конфига: wg и awg видят только интерфейсы своего типа. Контейнеры, повторяющие ключ,
# кроме первого, — в AWG_SHARED_KEY_DUPES. Выключенные вручную не считаем: сессию у Cloudflare
# они не держат, а перевыпуск включил бы их обратно.
awg_shared_key_detected() {
    AWG_SHARED_KEY_DUPES=()
    awg_discover_containers >/dev/null 2>&1 || return 1
    [ ${#AWG_ALL_CONTAINERS[@]} -ge 2 ] || return 1
    local c k seen=" "
    for c in "${AWG_ALL_CONTAINERS[@]}"; do
        k=$(docker exec "$c" sh -c "[ ! -f '$AWG_WARP_DIR/disabled' ] || exit 0; k=\$(sed -n 's/^PrivateKey = //p' '$AWG_WARP_CONF' 2>/dev/null | head -1); [ -z \"\$k\" ] || printf '%s' \"\$k\" | sha256sum | cut -c1-32" 2>/dev/null | tr -d '\r\n')
        [ -n "$k" ] || continue
        case "$seen" in
            *" $k "*) AWG_SHARED_KEY_DUPES+=("$c") ;;
            *) seen+="$k " ;;
        esac
    done
    [ ${#AWG_SHARED_KEY_DUPES[@]} -gt 0 ]
}

awg_snapshot_host_wgcf() {
    [ ! -f "$HOST_WGCF_BAK_DIR/recovery-required" ] || return 1
    mkdir -p "$HOST_WGCF_BAK_DIR" || return 1
    chmod 700 "$HOST_WGCF_BAK_DIR" || return 1
    rm -f "$HOST_WGCF_BAK_DIR/wgcf-account.toml" "$HOST_WGCF_BAK_DIR/wgcf-profile.conf"
    if [ -f "$WGCF_ACCOUNT" ]; then
        cp -p "$WGCF_ACCOUNT" "$HOST_WGCF_BAK_DIR/wgcf-account.toml" || return 1
    fi
    if [ -f "$WGCF_PROFILE" ]; then
        cp -p "$WGCF_PROFILE" "$HOST_WGCF_BAK_DIR/wgcf-profile.conf" || return 1
    fi
    return 0
}

awg_restore_host_wgcf() {
    [ -d "$HOST_WGCF_BAK_DIR" ] || return 1
    local f tmp target
    for f in wgcf-account.toml wgcf-profile.conf; do
        if [ "$f" = wgcf-account.toml ]; then target="$WGCF_ACCOUNT"; else target="$WGCF_PROFILE"; fi
        if [ -f "$HOST_WGCF_BAK_DIR/$f" ]; then
            tmp=$(mktemp "${target}.restore.XXXXXX") || return 1
            if ! cp -p "$HOST_WGCF_BAK_DIR/$f" "$tmp" || ! chmod 600 "$tmp" || ! mv "$tmp" "$target"; then
                rm -f "$tmp"; return 1
            fi
        else
            rm -f "$target" || return 1
        fi
    done
    return 0
}

awg_clear_host_wgcf_snapshot() {
    rm -rf "$HOST_WGCF_BAK_DIR" 2>/dev/null || true
}

awg_restore_mode_snapshot() {
    save_config_values WARP_KEY_MODE_AWG "$1" ACTIVE_KEY_NAME_AWG "$2"
}

# Устройства WARP, которые на хосте остались без контейнера (контейнер удалён, откат прерванного перевыпуска
# больше не нужен): снять в Cloudflare, иначе под ключом WARP+ остаётся занятое место. Не вышло — учётные
# данные остаются в очереди pending-retirements. rc=2 — не удалось и сохранить их: удалять нельзя.
awg_retire_leftover_devices() {
    local f r rc=0
    for f in "$AWG_WGCF_BASE"/*/wgcf-account.toml "$AWG_WGCF_BASE"/*/.candidate.*/wgcf-account.toml \
             "$HOST_WGCF_BAK_ROOT"/*/wgcf-account.toml; do
        [ -f "$f" ] || continue
        r=0; awg_retire_unused_account "$f" "${f%/*}/wgcf-profile.conf" || r=$?
        [ "$r" != 2 ] || rc=2
    done
    return "$rc"
}

# Хост без WARP в контейнерах: снять оставшиеся устройства, сторожа, wgcf и копии регистраций.
awg_remove_host_state() {
    awg_retire_leftover_devices || return 1
    awg_retire_unused_account "$WGCF_ACCOUNT" "$WGCF_PROFILE" || { [ "$?" -ne 2 ] || return 1; }
    awg_remove_host_watcher
    rm -f "$WGCF_BIN" "$WGCF_ACCOUNT" "$WGCF_PROFILE"; rm -rf "$AWG_WGCF_BASE" "$HOST_WGCF_BAK_ROOT"
    # Каталог состояния уходит, если в нём не осталось очереди удаления устройств.
    rmdir "${HOST_WGCF_BAK_ROOT%/*}" 2>/dev/null || true
}

uninstall_awg() (
    local operation_fd
    exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    flock -n "$operation_fd" || { echo "Другая операция AmneziaWG уже выполняется."; return 1; }
    uninstall_awg_locked "$@"
)

uninstall_awg_locked() {
    # Аргумент "all" → без интерактива, чистим все (используется из full_uninstall).
    # Без аргумента → даём юзеру выбрать целевые контейнеры.
    local force_all="${1:-}" recovery owner existing drc=0
    # Контейнеров AmneziaWG может не быть совсем (сняты goAmnezia или руками) — это норма, удаление
    # чистит хост. Если же не ответил Docker, неизвестно, что в контейнерах: останавливаемся.
    awg_discover_containers all >/dev/null 2>&1 || drc=$?
    if [ "$drc" = 2 ]; then
        echo "Удаление остановлено: Docker не отвечает. Данные сохранены."
        return 1
    fi
    existing=" ${AWG_ALL_CONTAINERS[*]} "
    for recovery in "$HOST_WGCF_BAK_ROOT"/*/recovery-required "$AWG_WGCF_BASE"/*/.candidate.*; do
        [ -e "$recovery" ] || continue
        owner="${recovery%/*}"; owner="${owner##*/}"
        # Контейнера больше нет — откатывать некуда: его устройства снимаются вместе с остальными.
        [[ "$existing" == *" $owner "* ]] || continue
        echo "Удаление остановлено: сначала завершите восстановление AmneziaWG. Данные сохранены."
        return 1
    done
    echo -e "\n${YELLOW}Удаление WARP (AmneziaWG)...${NC}\n"

    local saved_container="${CONTAINER:-}"
    if [ "$force_all" = "all" ]; then
        AWG_TARGETS=("${AWG_ALL_CONTAINERS[@]}")
        [ ${#AWG_TARGETS[@]} -gt 0 ] || echo -e "  ${DIM}Контейнеров AmneziaWG нет — чищу только хост.${NC}"
    else
        awg_discover_containers 2>/dev/null || return 1
        if ! awg_pick_targets; then
            echo -e "${YELLOW}Отменено.${NC}"
            return
        fi
    fi

    systemctl stop warp-bot 2>/dev/null; systemctl disable warp-bot 2>/dev/null
    rm -f /etc/systemd/system/warp-bot.service; systemctl daemon-reload 2>/dev/null
    [ -f "$BOT_PID_FILE" ] && { kill "$(cat "$BOT_PID_FILE")" 2>/dev/null; rm -f "$BOT_PID_FILE"; }
    echo -e "  ${GREEN}✓${NC}  Бот остановлен"

    local cleaned=0 failed=0
    for c in "${AWG_TARGETS[@]}"; do
        local was_running=1
        if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]; then
            was_running=0
            if ! docker start "$c" >/dev/null 2>&1; then
                echo -e "  ${RED}► ${c}: не удалось запустить для очистки${NC}"
                ((failed++))
                continue
            fi
        fi
        CONTAINER="$c"
        if ! awg_load_container_data 2>/dev/null; then
            echo -e "  ${RED}► ${c}: не удалось загрузить данные контейнера${NC}"
            [ "$was_running" -eq 0 ] && docker stop "$c" >/dev/null 2>&1 || true
            ((failed++))
            continue
        fi
        echo -e "  ${CYAN}► ${c}${NC}"
        awg_cleanup_rules || { failed=$((failed + 1)); continue; }
        # Снять интерфейс тем, кто его поднял (стандартный wg-quick не снимает amneziawg и tun).
        awg_warp_down >/dev/null 2>&1 || true
        docker exec "$c" sh -c "
            ip link del warp 2>/dev/null || true
            rm -rf '$AWG_WARP_DIR' '$AWG_WARP_TOOLS_DIR' '$AWG_LEGACY_SNAP' || exit 1
            ! ip link show warp >/dev/null 2>&1 && test ! -e '$AWG_WARP_DIR' && test ! -e '$AWG_WARP_TOOLS_DIR'
        " >/dev/null 2>&1 || { failed=$((failed + 1)); continue; }
        echo -e "    ${GREEN}✓${NC} WARP и его инструменты удалены из ${c}"

        # Из start.sh вырезается только свой блок: остальное в нём — правки Amnezia и goAmnezia.
        awg_remove_from_start_sh || { failed=$((failed + 1)); continue; }
        echo -e "    ${GREEN}✓${NC} Автозапуск WARP убран из start.sh"
        local retire_rc=0
        awg_retire_unused_account "$AWG_WGCF_BASE/$c/wgcf-account.toml" "$AWG_WGCF_BASE/$c/wgcf-profile.conf" "$c" || retire_rc=$?
        if [ "$retire_rc" -eq 2 ]; then failed=$((failed + 1)); continue; fi
        rm -rf "$AWG_WGCF_BASE/$c" "$HOST_WGCF_BAK_ROOT/$c" || { failed=$((failed + 1)); continue; }
        ((cleaned++))
        [ "$was_running" -eq 0 ] && docker stop "$c" >/dev/null 2>&1 || true
    done
    CONTAINER="$saved_container"
    [ $cleaned -gt 0 ] && echo -e "  ${GREEN}✓${NC}  Очищено контейнеров: ${cleaned}"
    [ $failed -gt 0 ] && echo -e "  ${YELLOW}⚠${NC}  Не очищено контейнеров: ${failed}"

    # wgcf удаляем только если все контейнеры почищены (force_all=all) или
    # на хосте больше не осталось контейнеров с WARP установленным.
    if [ "$force_all" = "all" ] && [ "$failed" -eq 0 ]; then
        awg_remove_host_state || return 1
        echo -e "  ${GREEN}✓${NC}  wgcf и профили удалены (host-wide)"
    elif [ "$force_all" = "all" ]; then
        echo -e "  ${YELLOW}⚠${NC}  wgcf оставлен: не все контейнеры очищены"
    else
        # Проверим: остались ли ещё контейнеры с warp.conf?
        local has_any=0
        awg_discover_containers all >/dev/null 2>&1 || { [ "$?" = 1 ] || has_any=1; }
        for c in "${AWG_ALL_CONTAINERS[@]}"; do
            if ! docker exec "$c" test ! -f "$AWG_WARP_CONF" >/dev/null 2>&1; then
                # Stopped containers cannot execute a probe. Preserve shared resources.
                has_any=1; break
            fi
        done
        if [ $has_any -eq 0 ]; then
            awg_remove_host_state || return 1
            echo -e "  ${GREEN}✓${NC}  Последний WARP удалён — wgcf тоже снят с хоста"
        else
            echo -e "  ${DIM}wgcf и хостовые файлы оставлены (другие контейнеры ещё используют WARP)${NC}"
        fi
    fi
    [ "$failed" -eq 0 ]
}

# ═══════════════════════════════════════════════════════════════
#  AMNEZIA CLIENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════

# Выбор клиентов: AWG_SELECTED_KEYS — строки clients.list; адрес от версий до 2.5.2 переводится в ключ клиента
# с этим адресом (AWG_CLIENTS_MIGRATED=1 — было что перевести), без такого клиента остаётся адресом, как раньше.
# AWG_SELECTED_IPS — их адреса по текущему конфигу сервера. Заодно заполняет AWG_CLIENT_IPS и AWG_CLIENT_KEYS.
awg_load_clients() {
    local raw line i
    AWG_SELECTED_KEYS=(); AWG_SELECTED_IPS=(); AWG_CLIENTS_MIGRATED=0
    raw=$(docker exec "$CONTAINER" sh -c "if [ -f '$AWG_WARP_CLIENTS' ]; then cat '$AWG_WARP_CLIENTS'; fi" 2>/dev/null) || return 1
    awg_get_client_ips || return 1
    while IFS= read -r line; do
        line="${line//[[:space:]]/}"
        [ -n "$line" ] || continue
        if awg_valid_ipv4 "$line"; then
            [[ "$line" == */* ]] || line="$line/32"
            for i in "${!AWG_CLIENT_IPS[@]}"; do
                if [ "${AWG_CLIENT_IPS[$i]}" = "$line" ]; then
                    line="${AWG_CLIENT_KEYS[$i]}"; AWG_CLIENTS_MIGRATED=1; break
                fi
            done
        fi
        [[ " ${AWG_SELECTED_KEYS[*]} " == *" $line "* ]] || AWG_SELECTED_KEYS+=("$line")
    done <<< "$raw"
    awg_resolve_selected_ips
}

# Адреса выбранных по конфигу сервера — в том же порядке, что у блока в start.sh: сначала строки-адреса,
# затем ключи в порядке конфига. Ключа в конфиге нет (клиента удалили) — нет и правил для него.
awg_resolve_selected_ips() {
    local i e
    local -A want=() seen=()
    AWG_SELECTED_IPS=()
    for e in "${AWG_SELECTED_KEYS[@]}"; do
        if awg_valid_ipv4 "$e"; then
            [[ "$e" == */* ]] || e="$e/32"
            [ -n "${seen[$e]+_}" ] || { seen[$e]=1; AWG_SELECTED_IPS+=("$e"); }
        else
            want[$e]=1
        fi
    done
    for i in "${!AWG_CLIENT_KEYS[@]}"; do
        [ -n "${want[${AWG_CLIENT_KEYS[$i]}]+_}" ] || continue
        e="${AWG_CLIENT_IPS[$i]}"
        [ -n "${seen[$e]+_}" ] || { seen[$e]=1; AWG_SELECTED_IPS+=("$e"); }
    done
}

awg_save_clients() {
    awg_validate_client_keys || return 1
    local content="" e
    for e in "${AWG_SELECTED_KEYS[@]}"; do content+="${e}"$'\n'; done
    docker exec -i "$CONTAINER" sh -s -- "$AWG_WARP_DIR" "$AWG_WARP_CLIENTS" "$content" <<'CLIENTS'
set -e
mkdir -p "$1"
tmp=$(mktemp "${2}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf '%s' "$3" > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$2"
CLIENTS
}

# Применить выбор клиентов контейнера: ключи (строки clients.list) — список, правила, автозапуск.
awg_commit_clients() {
    local CONTAINER="$1" operation_fd rc=0
    shift
    local -a desired=("$@")
    [[ "$CONTAINER" =~ ^amnezia-awg[0-9]*$ ]] || return 1
    exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    flock -n "$operation_fd" || { exec {operation_fd}>&-; return 1; }
    # Прошлая смена клиентов не откатилась до конца: сначала вернуть сохранённое, потом менять.
    awg_finish_client_rollback "$CONTAINER" || true
    if ! awg_load_container_data >/dev/null 2>&1 || ! awg_get_client_ips || ! awg_snapshot_container "$CONTAINER"; then
        exec {operation_fd}>&-; return 1
    fi
    AWG_SELECTED_KEYS=("${desired[@]}")
    awg_resolve_selected_ips
    if ! awg_save_clients; then rc=1
    elif is_warp_running_awg && ! awg_apply_rules; then rc=1
    elif ! awg_patch_start_sh; then rc=1; fi
    if [ "$rc" = 0 ]; then
        awg_clear_container_snapshot "$CONTAINER"
    elif awg_restore_container_snapshot "$CONTAINER"; then
        awg_clear_container_snapshot "$CONTAINER"
        log_action "AWG CLIENTS: failed; previous selection restored for $CONTAINER"
    else
        awg_mark_container_recovery "$CONTAINER"
        log_action "AWG CLIENTS: rollback unresolved for $CONTAINER; snapshot retained"
    fi
    exec {operation_fd}>&-
    return "$rc"
}

awg_parse_clients_table() {
    AWG_CLIENT_NAMES=()

    local raw
    raw=$(docker exec "$CONTAINER" sh -c "cat '$AWG_CLIENTS_TABLE' 2>/dev/null || true" | tr -d '\r')
    [ -z "$raw" ] && return 0

    declare -A key_to_name=()
    local id_name_pairs
    id_name_pairs=$(echo "$raw" | awk '
        /"clientId"/ {
            s = $0
            gsub(/.*"clientId"[[:space:]]*:[[:space:]]*"/, "", s)
            gsub(/".*/, "", s)
            cid = s
        }
        /"clientName"/ {
            s = $0
            gsub(/.*"clientName"[[:space:]]*:[[:space:]]*"/, "", s)
            gsub(/".*/, "", s)
            name = s
            if (cid != "" && name != "") {
                print cid "|" name
            }
        }')

    if [ -n "$id_name_pairs" ]; then
        while IFS='|' read -r cid name; do
            [ -n "$cid" ] && [ -n "$name" ] && key_to_name["$cid"]="$name"
        done <<< "$id_name_pairs"
    fi

    local conf_peers
    conf_peers=$(docker exec "$CONTAINER" sh -c "cat '$AWG_VPN_CONF' 2>/dev/null || true" | tr -d '\r' | awk '
        /^\[Peer\]/ { pubkey=""; ip="" }
        /^PublicKey/ {
            s = $0
            sub(/^[^=]*= */, "", s)
            pubkey = s
        }
        /^AllowedIPs/ {
            s = $0
            sub(/^[^=]*= */, "", s)
            ip = s
            if (pubkey != "" && ip != "") {
                print pubkey "|" ip
            }
        }')

    if [ -n "$conf_peers" ]; then
        while IFS='|' read -r pubkey ip; do
            if [ -n "$pubkey" ] && [ -n "$ip" ] && [ -n "${key_to_name[$pubkey]+_}" ]; then
                local name="${key_to_name[$pubkey]}"
                AWG_CLIENT_NAMES["$ip"]="$name"
                local bare="${ip%/32}"
                AWG_CLIENT_NAMES["$bare"]="$name"
            fi
        done <<< "$conf_peers"
    fi

    return 0
}

awg_get_name() {
    local ip="$1" bare="${1%/32}"
    [ -n "${AWG_CLIENT_NAMES[$bare]+_}" ] && { echo "${AWG_CLIENT_NAMES[$bare]}"; return; }
    [ -n "${AWG_CLIENT_NAMES[$ip]+_}" ] && { echo "${AWG_CLIENT_NAMES[$ip]}"; return; }
    [ -n "${AWG_CLIENT_NAMES[${bare}/32]+_}" ] && { echo "${AWG_CLIENT_NAMES[${bare}/32]}"; return; }
}

awg_format_label() {
    local ip="$1" name; name=$(awg_get_name "$ip")
    [ -n "$name" ] && echo "$ip ($name)" || echo "$ip"
}

# Клиенты из конфига сервера по порядку: адрес (первый IPv4 /32) и публичный ключ. Разбирает awk контейнера —
# та же программа, что у блока в start.sh и у автопроверки.
awg_get_client_ips() {
    local out ip key
    AWG_CLIENT_IPS=(); AWG_CLIENT_KEYS=()
    out=$(docker exec "$CONTAINER" awk "$AWG_CLIENTS_AWK" "$AWG_VPN_CONF" 2>/dev/null) || return 1
    while read -r ip key; do
        [ -n "$key" ] || continue
        AWG_CLIENT_IPS+=("$ip"); AWG_CLIENT_KEYS+=("$key")
    done <<< "${out//$'\r'/}"
    return 0
}

awg_toggle_clients_ssh() {
    awg_select_container || return
    awg_parse_clients_table
    if ! awg_load_clients; then
        echo -e "\n  ${RED}Не удалось прочитать клиентов контейнера.${NC}"
        read -p "Enter..."; return
    fi

    if [ ${#AWG_CLIENT_IPS[@]} -eq 0 ]; then
        echo -e "\n  ${RED}Нет клиентов в конфиге VPN.${NC}"
        read -p "Enter..."; return
    fi

    # Выбор держим ключами клиентов: адрес меняется при смене подсети, ключ — нет.
    local -a pending=()
    local k i e
    for k in "${AWG_CLIENT_KEYS[@]}"; do
        [[ " ${AWG_SELECTED_KEYS[*]} " == *" $k "* ]] && pending+=("$k")
    done

    while true; do
        local pending_set=" ${pending[*]+"${pending[*]}"} "
        clear; echo -e "\n${CYAN}━━━ Управление клиентами WARP ━━━${NC}"
        [ ${#AWG_ALL_CONTAINERS[@]} -gt 1 ] && echo -e "  ${DIM}Контейнер: ${CONTAINER}${NC}"
        echo -e "  ${DIM}Нажмите номер чтобы вкл/выкл WARP для клиента${NC}\n"

        local warp_count=0
        for i in "${!AWG_CLIENT_IPS[@]}"; do
            local label; label=$(awg_format_label "${AWG_CLIENT_IPS[$i]}")
            if [[ "$pending_set" == *" ${AWG_CLIENT_KEYS[$i]} "* ]]; then
                echo -e "  ${GREEN} $((i + 1))) ✅  $label${NC}"
                ((warp_count++))
            else
                echo -e "  ${WHITE} $((i + 1)))${NC} ☐   $label"
            fi
        done

        echo ""
        echo -e "  ${WHITE}Через WARP: ${CYAN}${warp_count}${NC} из ${#AWG_CLIENT_IPS[@]}"
        echo -e "${CYAN}──────────────────────────────────────────────────────${NC}"
        echo -e "  ${GREEN}all${NC}) Включить всех   ${YELLOW}none${NC}) Выключить всех"
        echo -e "  ${GREEN}ok${NC})  Применить        ${DIM}0${NC})    Отмена (без изменений)"
        echo ""
        read -p "  > " answer

        case "$answer" in
            0|"")
                return ;;
            all)
                pending=("${AWG_CLIENT_KEYS[@]}") ;;
            none)
                pending=() ;;
            ok)
                echo -e "\n${YELLOW}  Применяю правила...${NC}"
                if ! awg_commit_clients "$CONTAINER" "${pending[@]+"${pending[@]}"}"; then
                    echo -e "${RED}  Не удалось применить выбор. Проверьте диагностику и результат отката.${NC}"
                    read -r -p "  Enter..."; return 1
                fi
                echo -e "${GREEN}  ✓ Правила сохранены${NC}"
                log_action "AWG CLIENTS APPLIED: ${#pending[@]} in WARP, rules updated live"
                read -p "  Enter..."; return ;;
            *)
                IFS=',' read -ra parts <<< "$answer"
                for p in "${parts[@]}"; do
                    p="${p//[[:space:]]/}"
                    if [[ "$p" =~ ^[0-9]+$ ]] && (( p >= 1 && p <= ${#AWG_CLIENT_KEYS[@]} )); then
                        pending_set=" ${pending[*]+"${pending[*]}"} "
                        k="${AWG_CLIENT_KEYS[$((p-1))]}"
                        if [[ "$pending_set" == *" $k "* ]]; then
                            local -a tmp=()
                            for e in "${pending[@]}"; do
                                [ "$e" != "$k" ] && tmp+=("$e")
                            done
                            pending=("${tmp[@]+"${tmp[@]}"}")
                        else
                            pending+=("$k")
                        fi
                    fi
                done ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  AMNEZIA RUNTIME RULES & PERSISTENCE
# ═══════════════════════════════════════════════════════════════

awg_cleanup_rules() {
    docker exec -i "$CONTAINER" sh -s <<'CLEAN_RULES'
set -e
rules=$(ip -4 rule show)
priorities=$(printf '%s\n' "$rules" | awk '/lookup 100/ {gsub(/:/,"",$1); print $1}')
for pr in $priorities; do ip -4 rule del priority "$pr"; done
nat_rules=$(iptables -t nat -S POSTROUTING)
while IFS= read -r line; do
    case "$line" in
        *' -o warp -j MASQUERADE'*)
            set -- $line
            shift
            iptables -t nat -D "$@" ;;
    esac
done <<NAT_RULES
$nat_rules
NAT_RULES
mangle_rules=$(iptables -t mangle -S FORWARD)
while IFS= read -r line; do
    case "$line" in
        *' -o warp '*'-j TCPMSS'*)
            set -- $line
            shift
            iptables -t mangle -D "$@" ;;
    esac
done <<MANGLE_RULES
$mangle_rules
MANGLE_RULES
# Flushing a nonexistent FIB table returns 2; distinguish it from an actual failure.
routes=$(ip -N -4 route show table all)
if printf '%s\n' "$routes" | grep -qE '(^| )table 100( |$)'; then ip -4 route flush table 100; fi
CLEAN_RULES
}

# Only sockets explicitly bound to warp use this rule; ordinary container egress stays direct.
awg_probe_route() {
    docker exec "$CONTAINER" sh -c '
        ip link show warp >/dev/null 2>&1 || exit 0
        ip route replace default dev warp table 100 || exit 1
        ip rule show | grep -q "oif warp.*lookup 100" || ip rule add oif warp table 100 priority 90
    '
}

awg_apply_rules() {
    awg_validate_client_ips || return 1
    awg_cleanup_rules || return 1
    awg_probe_route || return 1
    [ ${#AWG_SELECTED_IPS[@]} -eq 0 ] && return 0
    docker exec "$CONTAINER" sh -c "
        iptables -t mangle -C FORWARD -o warp -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
        iptables -t mangle -A FORWARD -o warp -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    " || return 1
    local prio=100 ip
    for ip in "${AWG_SELECTED_IPS[@]}"; do
        docker exec "$CONTAINER" sh -c "
            ip rule add from ${ip} table 100 priority ${prio} || exit 1
            iptables -t nat -C POSTROUTING -s ${ip} -o warp -j MASQUERADE 2>/dev/null || \
            iptables -t nat -I POSTROUTING 1 -s ${ip} -o warp -j MASQUERADE
        " || return 1
        prio=$((prio + 1))
    done
    return 0
}

# Адрес IPv4 с необязательной маской: так выбор хранили версии до 2.5.2.
awg_valid_ipv4() {
    local octet prefix
    local -a octets
    [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$ ]] || return 1
    IFS=. read -r -a octets <<< "${1%/*}"
    prefix="${1#*/}"
    [ "$prefix" != "$1" ] || prefix=32
    (( 10#$prefix <= 32 )) || return 1
    for octet in "${octets[@]}"; do
        (( 10#$octet <= 255 )) || return 1
    done
}

awg_validate_client_ips() {
    local ip
    for ip in "${AWG_SELECTED_IPS[@]}"; do
        awg_valid_ipv4 "$ip" && continue
        echo "Некорректный IPv4-адрес клиента; правила не изменены." >&2
        return 1
    done
    return 0
}

# Строка выбора — публичный ключ WireGuard или адрес от версий до 2.5.2.
awg_validate_client_keys() {
    local e
    for e in "${AWG_SELECTED_KEYS[@]}"; do
        [[ "$e" =~ ^[A-Za-z0-9+/]{43}=$ ]] && continue
        awg_valid_ipv4 "$e" && continue
        echo "Некорректный ключ клиента; выбор не изменён." >&2
        return 1
    done
    return 0
}

# Блок goWARP в start.sh: поднять WARP и направить в него выбранных клиентов. Клиенты — по ключам: адреса блок
# берёт из конфига сервера при каждом запуске, поэтому переживает смену подсети и перенумерацию в goAmnezia,
# которая переносит блок в новый контейнер как есть.
awg_patch_start_sh() {
    [ -n "${AWG_START_SH:-}" ] || return 1
    local warp_block cands
    cands=$(awg_vpn_conf_candidates "$CONTAINER")
    warp_block="${AWG_MARKER_BEGIN}"$'\n'"${AWG_BLOCK_TAG}"$'\n'"("$'\n'"  flock -x 9 || exit 1"$'\n'
    warp_block+="if [ -f '${AWG_WARP_CONF}' ] && [ ! -f '${AWG_WARP_DIR}/disabled' ]; then"$'\n'
    warp_block+="  ip link show warp >/dev/null 2>&1 || env PATH=${AWG_WARP_TOOLS_DIR}/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin wg-quick up '${AWG_WARP_CONF}' 9>&-"$'\n'
    warp_block+="  if ip link show warp >/dev/null 2>&1; then"$'\n'
    warp_block+="    ip route replace default dev warp table 100"$'\n'
    warp_block+="    ip rule show | grep -q 'oif warp.*lookup 100' || ip rule add oif warp table 100 priority 90"$'\n'
    warp_block+="    for conf in ${cands}; do [ -f \"\$conf\" ] && break; done"$'\n'
    warp_block+="    ips=\$(awk -v list='${AWG_WARP_CLIENTS}' '${AWG_CLIENTS_AWK}' \"\$conf\" 2>/dev/null)"$'\n'
    warp_block+="    [ -z \"\$ips\" ] || iptables -t mangle -C FORWARD -o warp -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || iptables -t mangle -A FORWARD -o warp -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu"$'\n'
    warp_block+="    prio=100"$'\n'
    warp_block+="    for ip in \$ips; do"$'\n'
    warp_block+="      ip rule show | grep -q \"from \${ip%/32} .*lookup 100\" || ip rule add from \"\$ip\" table 100 priority \"\$prio\""$'\n'
    warp_block+="      iptables -t nat -C POSTROUTING -s \"\$ip\" -o warp -j MASQUERADE 2>/dev/null || iptables -t nat -I POSTROUTING 1 -s \"\$ip\" -o warp -j MASQUERADE"$'\n'
    warp_block+="      prio=\$((prio + 1))"$'\n'
    warp_block+="    done"$'\n'
    warp_block+="  fi"$'\n'"fi"$'\n'") 9>/tmp/gowarp-runtime.lock"$'\n'"${AWG_MARKER_END}"

    # Build and validate beside the original; a failed write must leave it intact.
    # Блок передаётся через окружение: awk -v развернул бы обратные косые в нём.
    docker exec -i "$CONTAINER" sh -s -- "$AWG_START_SH" "$warp_block" <<'PATCH_START'
set -e
start=$1
clean=$(mktemp "${start}.clean.XXXXXX")
tmpfile=""
trap 'rm -f "$clean" "$tmpfile"' EXIT
tmpfile=$(mktemp "${start}.XXXXXX")
sed '/^# --- WARP-MANAGER BEGIN ---$/,/^# --- WARP-MANAGER END ---$/d' "$start" > "$clean"
# Preserve permissions, then insert before the startup script's blocking tail/exit.
cp -p "$start" "$tmpfile"
GOWARP_BLOCK=$2 awk '
    !inserted && (/tail -f \/dev\/null/ || /^[[:space:]]*exit([[:space:]]|$)/) {
        print ENVIRON["GOWARP_BLOCK"]; inserted=1
    }
    {print}
    END {if (!inserted) print ENVIRON["GOWARP_BLOCK"]}
' "$clean" > "$tmpfile"
sh -n "$tmpfile"
chmod +x "$tmpfile"
mv "$tmpfile" "$start"
PATCH_START
}

awg_install_host_watcher() {
    command -v docker >/dev/null 2>&1 || return 0
    local watch_tmp changed=0
    watch_tmp=$(mktemp "${AWG_WATCH_BIN}.XXXXXX") || return 1
    cat > "$watch_tmp" <<'WATCH'
#!/usr/bin/env bash
set -u

container_names() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^amnezia-awg[0-9]*$' || true
}

restore_one() {
    local c="$1"
    [ -n "$c" ] || return 0
    [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "true" ] || return 0
    docker exec "$c" sh -c '[ -f /opt/warp/warp.conf ]' >/dev/null 2>&1 || return 0
    docker exec "$c" sh -c '
        [ -f /opt/warp/disabled ] && exit 0
        if ip addr show warp >/dev/null 2>&1; then
            exit 0
        fi
        if [ -f /opt/amnezia/start.sh ] && grep -q "# --- WARP-MANAGER BEGIN ---" /opt/amnezia/start.sh 2>/dev/null; then
            sed -n "/# --- WARP-MANAGER BEGIN ---/,/# --- WARP-MANAGER END ---/p" /opt/amnezia/start.sh | sh
        else
            (
                flock -x 9 || exit 1
                [ ! -f /opt/warp/disabled ] || exit 0
                ip link show warp >/dev/null 2>&1 && exit 0
                env PATH=/opt/warp-tools/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin wg-quick up /opt/warp/warp.conf 9>&-
            ) 9>/tmp/gowarp-runtime.lock
        fi
    ' >/dev/null 2>&1 || true
}

restore_all() {
    local c
    while IFS= read -r c; do
        restore_one "$c"
    done < <(container_names)
}

if [ "${1:-}" = "--once" ]; then
    restore_all
    exit 0
fi

restore_all
docker events --filter type=container --filter event=start --format '{{.Actor.Attributes.name}}' 2>/dev/null |
while IFS= read -r c; do
    case "$c" in
        amnezia-awg|amnezia-awg[0-9]*) restore_one "$c" ;;
    esac
done
WATCH
    if ! bash -n "$watch_tmp" || ! chmod 755 "$watch_tmp"; then
        rm -f "$watch_tmp"; return 1
    fi
    cmp -s "$watch_tmp" "$AWG_WATCH_BIN" || changed=1
    mv -f "$watch_tmp" "$AWG_WATCH_BIN" || { rm -f "$watch_tmp"; return 1; }

    if command -v systemctl >/dev/null 2>&1; then
        cat > "$AWG_WATCH_SERVICE" <<EOF
[Unit]
Description=goWARP AmneziaWG container WARP restore watcher
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=${AWG_WATCH_BIN}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable --now "$(basename "$AWG_WATCH_SERVICE")" >/dev/null 2>&1 || true
        if [ "$changed" -eq 1 ]; then
            systemctl restart "$(basename "$AWG_WATCH_SERVICE")" || return 1
        fi
    fi
    "$AWG_WATCH_BIN" --once >/dev/null 2>&1 || true
}

awg_remove_host_watcher() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop "$(basename "$AWG_WATCH_SERVICE")" 2>/dev/null || true
        systemctl disable "$(basename "$AWG_WATCH_SERVICE")" 2>/dev/null || true
    fi
    rm -f "$AWG_WATCH_BIN" "$AWG_WATCH_SERVICE"
    command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null || true
}

# Вырезать свой блок из start.sh. Без пары меток не трогаем: вырезка до конца файла сняла бы и
# `tail -f /dev/null`, и контейнер перестал бы стартовать. Копию start.sh.final-backup от версий до 2.5.2
# (снималась один раз при первой вставке и устаревала) удаляем, но не восстанавливаем.
awg_remove_from_start_sh() {
    [ -n "${AWG_START_SH:-}" ] || return 0
    docker exec "$CONTAINER" sh -c "
        rm -f /opt/amnezia/start.sh.final-backup
        [ -f '$AWG_START_SH' ] || exit 0
        grep -qF '${AWG_MARKER_BEGIN}' '$AWG_START_SH' || exit 0
        grep -qF '${AWG_MARKER_END}' '$AWG_START_SH' || exit 1
        sed -i '/# --- WARP-MANAGER BEGIN ---/,/# --- WARP-MANAGER END ---/d' '$AWG_START_SH' || exit 1
        ! grep -qF '${AWG_MARKER_BEGIN}' '$AWG_START_SH'
    " 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════
#  UNIFIED WRAPPERS
# ═══════════════════════════════════════════════════════════════

get_warp_status() {
    if [ "$MODE" = "both" ]; then
        local s3 sa
        s3=$(get_warp_status_3xui); sa=$(get_warp_status_awg)
        echo "3X-UI: ${s3} | AWG: ${sa}"
    elif [ "$MODE" = "3xui" ]; then
        get_warp_status_3xui
    else
        get_warp_status_awg
    fi
}

get_warp_ip() {
    if [ "$MODE" = "both" ]; then
        local ip3 ipa
        ip3=$(get_warp_ip_3xui)
        awg_detect_warp_exit_ip; ipa="${AWG_WARP_EXIT_IP:-N/A}"
        echo "3X-UI: ${ip3} | AWG: ${ipa}"
    elif [ "$MODE" = "3xui" ]; then
        get_warp_ip_3xui
    else
        awg_detect_warp_exit_ip; echo "${AWG_WARP_EXIT_IP:-N/A}"
    fi
}

is_warp_running() {
    if [ "$MODE" = "both" ]; then
        is_warp_running_3xui 2>/dev/null || is_warp_running_awg 2>/dev/null
    elif [ "$MODE" = "3xui" ]; then
        is_warp_running_3xui
    else
        is_warp_running_awg
    fi
}

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM BOT
# ═══════════════════════════════════════════════════════════════

tg_api() {
    curl -s --connect-timeout 10 --max-time 20 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/$1" \
        -H "Content-Type: application/json" -d "$2" 2>/dev/null
}

tg_send() {
    local chat_id="$1" text keyboard="${3:-}"
    text=$(printf '%b' "$2")
    local payload
    if [ -n "$keyboard" ]; then
        payload=$(jq -n --arg c "$chat_id" --arg t "$text" --argjson k "$keyboard" \
            '{chat_id:$c, text:$t, parse_mode:"HTML", reply_markup:{inline_keyboard:$k}}')
    else
        payload=$(jq -n --arg c "$chat_id" --arg t "$text" \
            '{chat_id:$c, text:$t, parse_mode:"HTML"}')
    fi
    tg_api "sendMessage" "$payload"
}

tg_edit() {
    local chat_id="$1" msg_id="$2" text keyboard="${4:-}"
    text=$(printf '%b' "$3")
    local payload
    if [ -n "$keyboard" ]; then
        payload=$(jq -n --arg c "$chat_id" --argjson m "$msg_id" --arg t "$text" --argjson k "$keyboard" \
            '{chat_id:$c, message_id:$m, text:$t, parse_mode:"HTML", reply_markup:{inline_keyboard:$k}}')
    else
        payload=$(jq -n --arg c "$chat_id" --argjson m "$msg_id" --arg t "$text" \
            '{chat_id:$c, message_id:$m, text:$t, parse_mode:"HTML"}')
    fi
    tg_api "editMessageText" "$payload"
}

tg_answer_cb() {
    tg_api "answerCallbackQuery" "{\"callback_query_id\":\"$1\",\"text\":\"${2:-}\"}"
}

tg_delete() {
    # Возвращает 0 если ok:true в ответе Telegram API, иначе 1
    local chat_id="$1" msg_id="$2"
    [ -z "$msg_id" ] && return 1
    local resp ok
    resp=$(tg_api "deleteMessage" "{\"chat_id\":\"$chat_id\",\"message_id\":${msg_id}}" 2>/dev/null)
    ok=$(echo "$resp" | jq -r '.ok // "false"' 2>/dev/null)
    [ "$ok" = "true" ]
}

# ─── Bot keyboards ───────────────────────────────────────────

kbd_main_3xui() {
    cat <<'JSON'
[
  [{"text":"📊 Статус","callback_data":"st"},{"text":"🌐 IP","callback_data":"ip"}],
  [{"text":"▶️ Запустить","callback_data":"on"},{"text":"⏹ Остановить","callback_data":"off"}],
  [{"text":"🔑 Перевыпуск","callback_data":"rk"}],
  [{"text":"🛡 WARP+ ключи","callback_data":"keys"}],
  [{"text":"📋 JSON 3X-UI","callback_data":"js"}],
  [{"text":"💻 Система","callback_data":"sys"}],
  [{"text":"🏢 Хостинг","callback_data":"promo"}]
]
JSON
}

kbd_main_awg() {
    cat <<'JSON'
[
  [{"text":"📊 Статус","callback_data":"st"},{"text":"🌐 IP","callback_data":"ip"}],
  [{"text":"▶️ Запустить","callback_data":"on"},{"text":"⏹ Остановить","callback_data":"off"}],
  [{"text":"🔑 Перевыпуск","callback_data":"rk"}],
  [{"text":"🛡 WARP+ ключи","callback_data":"keys"}],
  [{"text":"👥 Клиенты WARP","callback_data":"cl"},{"text":"🔄 Контейнер","callback_data":"rc"}],
  [{"text":"💻 Система","callback_data":"sys"}],
  [{"text":"🏢 Хостинг","callback_data":"promo"}]
]
JSON
}

kbd_main_both() {
    cat <<'JSON'
[
  [{"text":"📊 Статус","callback_data":"st"},{"text":"🌐 IP","callback_data":"ip"}],
  [{"text":"▶️ Запустить","callback_data":"on"},{"text":"⏹ Остановить","callback_data":"off"}],
  [{"text":"🔑 Перевыпуск","callback_data":"rk"}],
  [{"text":"🛡 WARP+ ключи","callback_data":"keys"}],
  [{"text":"📋 JSON 3X-UI","callback_data":"js"},{"text":"👥 Клиенты AWG","callback_data":"cl"}],
  [{"text":"🔄 Контейнер","callback_data":"rc"}],
  [{"text":"💻 Система","callback_data":"sys"}],
  [{"text":"🏢 Хостинг","callback_data":"promo"}]
]
JSON
}

kbd_main() {
    if [ "$MODE" = "both" ]; then kbd_main_both
    elif [ "$MODE" = "3xui" ]; then kbd_main_3xui
    else kbd_main_awg; fi
}

kbd_back() { echo '[[{"text":"⬅️ Меню","callback_data":"m"}]]'; }
kbd_rekey_confirm() {
    local nonce="$1"
    echo "[[{\"text\":\"✅ Да\",\"callback_data\":\"rk_y:${nonce}\"}],[{\"text\":\"⬅️ Отмена\",\"callback_data\":\"m\"}]]"
}
kbd_delete_confirm() {
    local nonce="$1"
    echo "[[{\"text\":\"✅ Удалить\",\"callback_data\":\"k_del_yes:${nonce}\"}],[{\"text\":\"⬅️ Отмена\",\"callback_data\":\"keys\"}]]"
}
kbd_keys_back() { echo '[[{"text":"⬅️ Назад","callback_data":"keys"}]]'; }

kbd_keys_main() {
    local rows='[]' n k ts
    while IFS='|' read -r n k ts; do
        [ -n "$n" ] || continue
        local row='[]'
        if has_3xui_mode; then row=$(jq -cn --argjson r "$row" --arg t "${n} → 3X-UI" --arg c "k_apply:3xui:key:$n" '$r+[{text:$t,callback_data:$c}]'); fi
        if has_awg_mode; then row=$(jq -cn --argjson r "$row" --arg t "${n} → AWG" --arg c "k_apply:awg:key:$n" '$r+[{text:$t,callback_data:$c}]'); fi
        row=$(jq -cn --argjson r "$row" --arg c "k_del_$n" '$r+[{text:"Удалить",callback_data:$c}]')
        rows=$(jq -cn --argjson r "$rows" --argjson row "$row" '$r+[$row]')
    done < "$WARP_KEYS"
    local row='[]'
    has_3xui_mode && row=$(jq -cn --argjson r "$row" '$r+[{text:"3X-UI → Free",callback_data:"k_apply:3xui:free"}]')
    has_awg_mode && row=$(jq -cn --argjson r "$row" '$r+[{text:"AWG → Free",callback_data:"k_apply:awg:free"}]')
    jq -cn --argjson r "$rows" --argjson row "$row" '$r+[$row,[{text:"Добавить ключ",callback_data:"k_add"},{text:"Проверить",callback_data:"k_check"}],[{text:"Как получить ключ",callback_data:"k_help"},{text:"Меню",callback_data:"m"}]]'
}

# ─── State-машина бота (для приёма ключа текстом) ────────────
# P2.1 (Codex 004): ключуем по chat+user, чтобы в группе аллоулист-юзеры
# не наступали на флоу друг друга. Файл: chat|user|state|extra (одна строка
# на пару chat+user).

bot_state_set() {
    local chat_id="$1" state="$2" extra="${3:-}" user_id="${4:-}" tmp
    tmp=$(mktemp "${WARP_BOT_STATE}.XXXXXX") || return 1
    if [ -f "$WARP_BOT_STATE" ]; then
        awk -F'|' -v c="$chat_id" -v u="$user_id" '!($1==c && $2==u)' "$WARP_BOT_STATE" > "$tmp" || { rm -f "$tmp"; return 1; }
    fi
    if ! printf '%s|%s|%s|%s\n' "$chat_id" "$user_id" "$state" "$extra" >> "$tmp" ||
       ! chmod 600 "$tmp" || ! mv "$tmp" "$WARP_BOT_STATE"; then rm -f "$tmp"; return 1; fi
}

bot_state_get() {
    local chat_id="$1" user_id="${2:-}"
    [ -f "$WARP_BOT_STATE" ] || return 0
    awk -F'|' -v c="$chat_id" -v u="$user_id" '$1==c && $2==u {print $3"|"$4; exit}' "$WARP_BOT_STATE"
}

bot_state_clear() {
    [ -f "$WARP_BOT_STATE" ] || return 0
    if [ -n "${1:-}" ]; then
        local chat_id="$1" user_id="${2:-}" tmp
        tmp=$(mktemp "${WARP_BOT_STATE}.XXXXXX") || return 1
        if ! awk -F'|' -v c="$chat_id" -v u="$user_id" '!($1==c && $2==u)' "$WARP_BOT_STATE" > "$tmp" ||
           ! chmod 600 "$tmp" || ! mv "$tmp" "$WARP_BOT_STATE"; then rm -f "$tmp"; return 1; fi
    else
        rm -f "$WARP_BOT_STATE"
    fi
}

# P0.3 fix (self-audit on Codex aa395f9): nonce должен быть криптографически
# случайным. $RANDOM = 15 бит, 2 вызова + epoch = ~30 бит + предсказуемый
# префикс → bruteforce ~10^9. Берём 16 hex (64 бита) из /dev/urandom.
make_callback_nonce() {
    if [ -r /dev/urandom ]; then
        head -c 8 /dev/urandom 2>/dev/null | od -An -tx1 2>/dev/null | tr -d ' \n'
    else
        # Запасной вариант, если /dev/urandom недоступен (контейнер?)
        printf '%s%s%s' "$RANDOM" "$RANDOM" "$RANDOM"
    fi
}

# P0.4 fix: nonce с timestamp для expiry. Возвращает 0 если nonce валиден и
# не истёк (TTL=120 сек), иначе 1.
NONCE_TTL_SEC=120

check_nonce_valid() {
    local stored="$1" given="$2"
    # stored = "nonce_hex:ts" (или "nonce:ts:extra_data" для k_del_yes)
    local stored_nonce="${stored%%:*}"
    local rest="${stored#*:}"
    local stored_ts="${rest%%:*}"
    [ "$stored_nonce" != "$given" ] && return 1
    [ -z "$stored_ts" ] && return 1
    if ! [[ "$stored_ts" =~ ^[0-9]+$ ]]; then return 1; fi
    local now; now=$(date +%s)
    local age=$((now - stored_ts))
    if [ "$age" -lt 0 ] || [ "$age" -gt "$NONCE_TTL_SEC" ]; then
        return 1
    fi
    return 0
}

# Хелпер: формирует "nonce:ts" для bot_state_set extra-поля
# ─── Bot main menu ───────────────────────────────────────────

bot_main_menu() {
    local chat_id="$1" msg_id="${2:-}"
    local ws wip="" extra=""
    ws=$(get_warp_status)
    is_warp_running && wip=" | WARP IP: $(get_warp_ip)"
    if has_3xui_mode; then
        extra+="\n🌐 3X-UI: <b>$(get_mode_text_3xui)</b>"
        extra+="\nSOCKS5: <code>127.0.0.1:${SOCKS_PORT}</code>"
    fi
    if has_awg_mode; then
        extra+="\n📦 AmneziaWG: <b>$(get_mode_text_awg)</b>"
        awg_discover_containers 2>/dev/null
        for _c in "${AWG_ALL_CONTAINERS[@]}"; do
            extra+="\n   • <code>${_c}</code>"
        done
    fi
    local mode_label="3X-UI"
    [ "$MODE" = "amnezia" ] && mode_label="AmneziaWG"
    [ "$MODE" = "both" ] && mode_label="3X-UI + AWG"
    local text="<b>WARP Manager v${WARP_VERSION}</b> (${mode_label})\nСервер: <code>${MY_IP:-N/A}</code>\nСтатус: <b>${ws}</b>${wip}${extra}\n\nВыберите:"
    local kbd; kbd=$(kbd_main)
    if [ -n "$msg_id" ]; then
        tg_edit "$chat_id" "$msg_id" "$text" "$kbd"
    else
        tg_send "$chat_id" "$text" "$kbd"
    fi
}

# Кнопка клиента — первые AWG_BOT_KEY_PREFIX символов его ключа: весь ключ не помещается в 64 байта Telegram.
AWG_BOT_KEY_PREFIX=16
bot_show_clients() {
    local chat_id="$1" msg_id="$2" CONTAINER="$3" i name label desired rows='[]' button text selected
    [[ "$CONTAINER" =~ ^amnezia-awg[0-9]*$ ]] || return 1
    if ! awg_load_container_data >/dev/null 2>&1 || ! awg_load_clients; then
        tg_edit "$chat_id" "$msg_id" "Контейнер недоступен. Откройте список заново." "$(kbd_back)"; return
    fi
    awg_parse_clients_table
    selected=" ${AWG_SELECTED_KEYS[*]} "
    local n=0
    for i in "${!AWG_CLIENT_KEYS[@]}"; do [[ "$selected" != *" ${AWG_CLIENT_KEYS[$i]} "* ]] || n=$((n + 1)); done
    text="<b>Клиенты WARP</b> — <code>${CONTAINER}</code>\n(${n} из ${#AWG_CLIENT_IPS[@]})\n\n"
    for i in "${!AWG_CLIENT_IPS[@]}"; do
        name=$(awg_get_name "${AWG_CLIENT_IPS[$i]}"); label="${name:-${AWG_CLIENT_IPS[$i]}}"; desired=1
        if [[ "$selected" == *" ${AWG_CLIENT_KEYS[$i]} "* ]]; then label="✅ $label"; desired=0; else label="☐ $label"; fi
        button="ct:${CONTAINER}:set:${AWG_CLIENT_KEYS[$i]:0:$AWG_BOT_KEY_PREFIX}:${desired}"
        if [ "${#button}" -gt 64 ]; then
            tg_edit "$chat_id" "$msg_id" "Имя контейнера слишком длинное для кнопок Telegram. Используйте CLI." "$(kbd_back)"; return
        fi
        rows=$(jq -cn --argjson r "$rows" --arg t "$label" --arg c "$button" '$r+[[{text:$t,callback_data:$c}]]')
        text+="$(jq -rn --arg t "$label" '$t|@html')\n"
    done
    rows=$(jq -cn --argjson r "$rows" --arg a "ct:$CONTAINER:all" --arg n "ct:$CONTAINER:none" '$r+[[{text:"Все",callback_data:$a},{text:"Никого",callback_data:$n}],[{text:"Контейнеры",callback_data:"cl"},{text:"Меню",callback_data:"m"}]]')
    tg_edit "$chat_id" "$msg_id" "$text" "$rows"
}

# ─── Bot handlers ────────────────────────────────────────────

bot_handle_callback() {
    # P1.3 (Codex 004): принимаем user_id 5-м аргументом для group-aware checks
    local chat_id="$1" msg_id="$2" cb_id="$3" data="$4" user_id="${5:-}"
    local CONTAINER="${CONTAINER:-}"
    [ -n "$cb_id" ] && tg_answer_cb "$cb_id" > /dev/null

    case "$data" in
        rk_y:*|k_del_yes:*|k_confirm:*) ;;
        *) bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; } ;;
    esac

    # Если флоу секретный (add-key) — отвергаем callback в группе
    if [ "$data" = "k_add" ]; then
        if [ -n "$user_id" ] && [ "$chat_id" != "$user_id" ]; then
            tg_edit "$chat_id" "$msg_id" "⛔ <b>Добавление ключа только в private-чате</b>\n\nОткройте бота в личке и пришлите /start." "$(kbd_keys_back)"
            log_action "BOT KEY ADD callback: rejected, chat=${chat_id} user=${user_id} (group)"
            return
        fi
    fi

    case "$data" in
        m) bot_main_menu "$chat_id" "$msg_id" ;;

        st)
            local ws wip="" extra=""
            ws=$(get_warp_status)
            is_warp_running && wip="\nWARP IP: <code>$(get_warp_ip)</code>"
            if has_3xui_mode; then
                extra+="\nSOCKS5: <code>127.0.0.1:${SOCKS_PORT}</code>"
                local raw; raw=$(warp-cli --accept-tos status 2>/dev/null | head -3)
                [ -n "$raw" ] && extra+="\n\n<pre>${raw}</pre>"
            fi
            if has_awg_mode; then
                awg_discover_containers 2>/dev/null
                for _c in "${AWG_ALL_CONTAINERS[@]}"; do
                    local _w="☐"
                    docker exec "$_c" sh -c "ip addr show warp >/dev/null 2>&1" 2>/dev/null && _w="✅"
                    local _cn; _cn=$(docker exec "$_c" sh -c "cat /opt/warp/clients.list 2>/dev/null | grep -c '.' || true" 2>/dev/null | tr -d '\r\n')
                    extra+="\n📦 <code>${_c}</code> ${_w} (клиентов: ${_cn})"
                done
            fi
            tg_edit "$chat_id" "$msg_id" "📊 <b>Статус WARP</b>\n\nСтатус: <b>${ws}</b>\nСервер: <code>${MY_IP:-N/A}</code>${wip}${extra}" "$(kbd_back)" ;;

        ip)
            local wip="N/A"; is_warp_running && wip=$(get_warp_ip)
            local t="🌐 <b>IP адреса</b>\n\n<b>Реальный:</b> <code>${MY_IP:-N/A}</code>\n<b>WARP:</b> <code>${wip}</code>"
            if has_3xui_mode; then t+="\n<b>SOCKS5:</b> <code>127.0.0.1:${SOCKS_PORT}</code>"; fi
            tg_edit "$chat_id" "$msg_id" "$t" "$(kbd_back)" ;;

        on)
            tg_edit "$chat_id" "$msg_id" "⏳ Запуск..." ""
            local result=""
            if has_3xui_mode && is_warp_installed_3xui; then
                save_config_val WARP_WANTED_3XUI on
                with_3xui_lock _start_3xui_locked >/dev/null 2>&1
                if is_warp_running_3xui; then
                    result+="✅ 3X-UI: $(get_warp_ip_3xui)\n"; log_action "BOT 3XUI ON"
                else result+="❌ 3X-UI: ошибка\n"; fi
            fi
            if has_awg_mode; then
                if awg_set_running on >/dev/null 2>&1; then result+="✅ AmneziaWG запущен\n"; else result+="⚠️ Не все контейнеры запущены\n"; fi
            fi
            [ -z "$result" ] && result="❌ WARP не установлен."
            tg_edit "$chat_id" "$msg_id" "<b>Запуск WARP</b>\n\n${result}" "$(kbd_back)" ;;

        off)
            local result=""
            if has_3xui_mode && is_warp_installed_3xui; then
                save_config_val WARP_WANTED_3XUI off
                if is_warp_running_3xui 2>/dev/null; then
                    with_3xui_lock _stop_3xui_locked >/dev/null 2>&1
                    result+="⏹ 3X-UI отключён\n"; log_action "BOT 3XUI OFF"
                fi
            fi
            if has_awg_mode; then
                if awg_set_running off >/dev/null 2>&1; then result+="⏹ AmneziaWG остановлен\n"; else result+="⚠️ Не все контейнеры остановлены\n"; fi
            fi
            [ -z "$result" ] && result="ℹ️ Уже отключён."
            tg_edit "$chat_id" "$msg_id" "<b>Остановка WARP</b>\n\n${result}" "$(kbd_back)" ;;

        rk)
            # P0.4 fix: state хранит nonce:ts для expiry-проверки
            local nonce; nonce=$(make_callback_nonce)
            bot_state_set "$chat_id" "confirm_rekey" "${nonce}:$(date +%s)" "$user_id"
            tg_edit "$chat_id" "$msg_id" "🔑 <b>Перевыпуск ключа</b>\n\nПродолжить? (действительно ${NONCE_TTL_SEC} сек)" "$(kbd_rekey_confirm "$nonce")" ;;

        rk_y|rk_y:*)
            local nonce="${data#rk_y:}"
            local state_raw state extra
            state_raw=$(bot_state_get "$chat_id" "$user_id")
            state="${state_raw%%|*}"
            extra="${state_raw#*|}"
            # P0.4: check_nonce_valid проверяет совпадение И что TTL не истёк
            if [ "$data" = "rk_y" ] || [ "$state" != "confirm_rekey" ] || ! check_nonce_valid "$extra" "$nonce"; then
                bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; }
                tg_edit "$chat_id" "$msg_id" "⚠️ Подтверждение устарело или невалидно. Откройте меню и нажмите «🔑 Перевыпуск» ещё раз." "$(kbd_back)"
                return
            fi
            bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; }
            tg_edit "$chat_id" "$msg_id" "⏳ Перевыпуск..." ""
            local result=""
            if has_3xui_mode; then
                if rekey_3xui_core >/dev/null 2>&1; then
                    result+="✅ 3X-UI: перевыпуск завершён.\n"
                else
                    result+="⚠️ 3X-UI: перевыпуск не завершён.\n"
                fi
            fi
            if has_awg_mode; then
                if rekey_awg_core >/dev/null 2>&1; then
                    result+="✅ AmneziaWG: перевыпуск завершён.\n"
                else
                    result+="⚠️ AmneziaWG: перевыпуск не завершён.\n${REKEY_AWG_DETAILS:-Проверьте диагностику.}\n"
                fi
            fi
            tg_edit "$chat_id" "$msg_id" "🔑 <b>Перевыпуск ключа</b>\n\n${result}" "$(kbd_back)" ;;

        js)
            if has_3xui_mode; then
                local t="📋 <b>Конфигурация для 3X-UI</b>\n\n<b>Outbound:</b>\n<pre>{\n  \"tag\": \"warp\",\n  \"protocol\": \"socks\",\n  \"settings\": {\n    \"servers\": [{\"address\": \"127.0.0.1\", \"port\": ${SOCKS_PORT}}]\n  }\n}</pre>\n\n<b>Routing:</b>\n<pre>{\"outboundTag\": \"warp\", \"domain\": [\"geosite:openai\",\"geosite:netflix\"]}</pre>"
                tg_edit "$chat_id" "$msg_id" "$t" "$(kbd_back)"
            else
                tg_edit "$chat_id" "$msg_id" "ℹ️ JSON не требуется для AmneziaWG." "$(kbd_back)"
            fi ;;

        cl|cl_show)
            awg_discover_containers >/dev/null 2>&1 || {
                tg_edit "$chat_id" "$msg_id" "Контейнеры AmneziaWG не найдены." "$(kbd_back)"; return;
            }
            if [ ${#AWG_ALL_CONTAINERS[@]} -eq 1 ]; then
                bot_show_clients "$chat_id" "$msg_id" "${AWG_ALL_CONTAINERS[0]}"
            else
                local rows='[]' cn
                for cn in "${AWG_ALL_CONTAINERS[@]}"; do
                    rows=$(jq -cn --argjson r "$rows" --arg c "cc:$cn" --arg t "$cn" '$r+[[{text:$t,callback_data:$c}]]')
                done
                rows=$(jq -cn --argjson r "$rows" '$r+[[{text:"Меню",callback_data:"m"}]]')
                tg_edit "$chat_id" "$msg_id" "Выберите контейнер:" "$rows"
            fi ;;

        cc:*)
            local target="${data#cc:}"
            if [[ "$target" =~ ^amnezia-awg[0-9]*$ ]]; then
                bot_show_clients "$chat_id" "$msg_id" "$target"
            else
                bot_handle_callback "$chat_id" "$msg_id" "" cl "$user_id"
            fi ;;

        ct:*)
            local target action ip desired extra current k match="" matches=0
            IFS=: read -r target action ip desired extra <<< "${data#ct:}"
            if ! [[ "$target" =~ ^amnezia-awg[0-9]*$ ]]; then
                bot_handle_callback "$chat_id" "$msg_id" "" cl "$user_id"; return
            fi
            CONTAINER="$target"
            if ! awg_load_container_data >/dev/null 2>&1 || ! awg_load_clients; then
                tg_edit "$chat_id" "$msg_id" "Не удалось прочитать настройки контейнера." "$(kbd_back)"; return
            fi
            # В выбор попадают только клиенты из конфига: строки от удалённых клиентов уходят при сохранении.
            local -a selected=()
            for k in "${AWG_CLIENT_KEYS[@]}"; do
                [[ " ${AWG_SELECTED_KEYS[*]} " != *" $k "* ]] || selected+=("$k")
            done
            case "$action" in
                all) [ -z "$ip$desired$extra" ] || return 1; selected=("${AWG_CLIENT_KEYS[@]}") ;;
                none) [ -z "$ip$desired$extra" ] || return 1; selected=() ;;
                set)
                    [[ "$desired" = 0 || "$desired" = 1 ]] && [ -z "$extra" ] || return 1
                    # ip — начало ключа клиента; кнопки прежних версий несли адрес — такие считаем устаревшими.
                    if [[ "$ip" =~ ^[A-Za-z0-9+/]{${AWG_BOT_KEY_PREFIX}}$ ]]; then
                        for k in "${AWG_CLIENT_KEYS[@]}"; do
                            [[ "$k" != "$ip"* ]] || { match="$k"; matches=$((matches + 1)); }
                        done
                    fi
                    if [ "$matches" != 1 ]; then
                        tg_edit "$chat_id" "$msg_id" "Список клиентов изменился. Откройте контейнер заново." "$(kbd_back)"; return
                    fi
                    local -a next=()
                    for current in "${selected[@]}"; do [ "$current" = "$match" ] || next+=("$current"); done
                    [ "$desired" = 0 ] || next+=("$match")
                    selected=("${next[@]}") ;;
                *) return 1 ;;
            esac
            if awg_commit_clients "$target" "${selected[@]}"; then
                log_action "BOT AWG CLIENTS: $target selection committed"
                bot_show_clients "$chat_id" "$msg_id" "$target"
            else
                tg_edit "$chat_id" "$msg_id" "❌ Не удалось применить выбор клиентов. Проверьте диагностику и результат отката." "$(kbd_back)"
            fi ;;

        rc)
            if ! has_awg_mode; then
                tg_edit "$chat_id" "$msg_id" "ℹ️ Только для AmneziaWG." "$(kbd_back)"; return
            fi
            awg_discover_containers 2>/dev/null
            local result=""
            for cn in "${AWG_ALL_CONTAINERS[@]}"; do
                tg_edit "$chat_id" "$msg_id" "🔄 Перезапуск ${cn}..." ""
                docker restart "$cn" >/dev/null 2>&1; sleep 5
                if docker exec "$cn" sh -c "true" 2>/dev/null; then
                    result+="✅ ${cn} перезапущен\n"
                else
                    result+="⚠️ ${cn} не отвечает\n"
                fi
            done
            tg_edit "$chat_id" "$msg_id" "🔄 <b>Перезапуск контейнеров</b>\n\n${result}" "$(kbd_back)"
            log_action "BOT AWG RESTART: ${#AWG_ALL_CONTAINERS[@]} containers" ;;

        sys)
            local s; s=$(get_system_stats)
            local ws; ws=$(get_warp_status)
            s+="\n<b>WARP:</b> ${ws}"
            s+="\n<b>Режим:</b> ${MODE}"
            if has_3xui_mode; then s+="\n<b>SOCKS5:</b> 127.0.0.1:${SOCKS_PORT}"; fi
            if has_awg_mode; then
                awg_discover_containers 2>/dev/null
                for _c in "${AWG_ALL_CONTAINERS[@]}"; do
                    s+="\n<b>📦</b> ${_c}"
                done
            fi
            tg_edit "$chat_id" "$msg_id" "$s" "$(kbd_back)" ;;

        promo)
            local pt="<b>🏢 Хостинг со скидкой до -60%</b>\n\n<b>🌍 РФ и Европа</b>\n👉 https://vk.cc/ct29NQ\n\n<code>OFF60</code> — 60% скидка\n<code>antenka20</code> — +20% к балансу (3мес)\n<code>antenka6</code> — +15% к балансу (6мес)\n<code>antenka12</code> — +5% к балансу (12мес)\n\n<b>🇧🇾 Беларусь</b>\n👉 https://vk.cc/cUxAhj\n<code>OFF60</code> — 60% скидка"
            tg_edit "$chat_id" "$msg_id" "$pt" "$(kbd_back)" ;;

        # ═══════ WARP+ keys ═══════
        keys)
            local cnt; cnt=$(keys_count)
            local body="<b>🛡 WARP+ ключи</b>\n\nСохранено ключей: <b>${cnt}</b>\nКнопка с ключом применяет его после подтверждения."
            has_3xui_mode && body+="\nРежим 3X-UI: <b>$(get_mode_text_3xui)</b>"
            has_awg_mode  && body+="\nРежим AmneziaWG: <b>$(get_mode_text_awg)</b>"
            body+="\n\nВыберите действие:"
            tg_edit "$chat_id" "$msg_id" "$body" "$(kbd_keys_main)" ;;

        k_list)
            local text="<b>📋 Список WARP+ ключей</b>\n\n"
            local cnt; cnt=$(keys_count)
            if [ "$cnt" -eq 0 ]; then
                text+="<i>Нет сохранённых ключей.</i>"
            else
                while IFS='|' read -r n k ts; do
                    [ -z "$n" ] && continue
                    local marks=""
                    [ "$ACTIVE_KEY_NAME_3XUI" = "$n" ] && marks+=" [3X-UI⭐]"
                    [ "$ACTIVE_KEY_NAME_AWG"  = "$n" ] && marks+=" [AWG⭐]"
                    text+="• <b>${n}</b>${marks}\n  <code>$(mask_key "$k")</code>\n  <i>${ts}</i>\n\n"
                done < "$WARP_KEYS"
            fi
            tg_edit "$chat_id" "$msg_id" "$text" "$(kbd_keys_back)" ;;

        k_add)
            bot_state_set "$chat_id" "awaiting_keyvalue" "auto" "$user_id"
            tg_edit "$chat_id" "$msg_id" "Пришлите ключ WARP+ целиком. Имя создастся автоматически; сообщение с ключом будет удалено. Отмена: /cancel." "$(kbd_keys_back)" ;;

        k_check)
            local result="Проверка трафика WARP:\n" c
            has_3xui_mode && result+="3X-UI: $(verify_warp_plus_3xui || echo 'нет ответа')\n"
            if has_awg_mode; then
                awg_discover_containers >/dev/null 2>&1
                for c in "${AWG_ALL_CONTAINERS[@]}"; do result+="${c}: $(verify_warp_plus_awg_container "$c" || echo 'нет ответа')\n"; done
            fi
            tg_edit "$chat_id" "$msg_id" "$result" "$(kbd_keys_main)" ;;

        k_apply:*)
            local rest="${data#k_apply:}" sub name nonce requested=plus
            sub="${rest%%:*}"; name="${rest#*:}"
            [[ "$sub" = 3xui || "$sub" = awg ]] || return 1
            [ "$sub" != awg ] || has_awg_mode || return 1
            [ "$sub" != 3xui ] || has_3xui_mode || return 1
            if [[ "$name" = key:* ]]; then name="${name#key:}"; key_exists "$name" || return 1
            elif [ "$name" = free ]; then requested=free
            else key_exists "$name" || return 1; fi
            nonce=$(make_callback_nonce)
            bot_state_set "$chat_id" confirm_apply "${nonce}:$(date +%s):${sub}:${requested}:${name}" "$user_id"
            local keyboard
            keyboard=$(jq -cn --arg c "k_confirm:$nonce" '[[{text:"Применить сейчас",callback_data:$c},{text:"Отмена",callback_data:"keys"}]]')
            tg_edit "$chat_id" "$msg_id" "${sub}: применить ${requested}? WARP кратковременно переподключится." "$keyboard" ;;

        k_confirm:*)
            local nonce="${data#k_confirm:}" raw state extra rest sub requested name
            raw=$(bot_state_get "$chat_id" "$user_id"); state="${raw%%|*}"; extra="${raw#*|}"
            if [ "$state" != confirm_apply ] || ! check_nonce_valid "$extra" "$nonce"; then
                tg_edit "$chat_id" "$msg_id" "Подтверждение устарело. Выберите действие заново." "$(kbd_keys_main)"; return
            fi
            rest="${extra#*:}"; rest="${rest#*:}"
            sub="${rest%%:*}"; rest="${rest#*:}"; requested="${rest%%:*}"; name="${rest#*:}"
            [ "$requested" = free ] && name=""
            bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; }
            tg_edit "$chat_id" "$msg_id" "Применяю и проверяю трафик…" ""
            local result
            if plus_apply_choice "$sub" "$requested" "$name" >/dev/null 2>&1; then
                result="${sub}: ${requested} применён."
            else
                result="${sub}: изменение не завершено."
                if [ "$sub" = awg ]; then result+="\n${REKEY_AWG_DETAILS:-Проверьте диагностику.}"; fi
            fi
            tg_edit "$chat_id" "$msg_id" "$result" "$(kbd_keys_main)" ;;

        k_pick_3xui|k_pick_awg|k_del|k_mode_3xui|k_mode_awg)
            tg_edit "$chat_id" "$msg_id" "Выберите действие с ключом." "$(kbd_keys_main)" ;;

        k_help)
            local t="<b>Ключ WARP+</b>\n\nВ приложении 1.1.1.1 откройте настройки аккаунта → License Key. Проверьте активную подписку WARP+.\n\nДобавьте ключ и выберите кнопку применения к нужной подсистеме.\n\nЕсли ключ недоступен, обратитесь к справке или поддержке Cloudflare."
            tg_edit "$chat_id" "$msg_id" "$t" "$(kbd_keys_back)" ;;

        k_set_3xui_free|k_set_awg_free|k_set_3xui_plus|k_set_awg_plus|k_use_3xui_*|k_use_awg_*)
            tg_edit "$chat_id" "$msg_id" "Меню обновлено. Выберите ключ и подсистему; изменение применяется сразу после подтверждения." "$(kbd_keys_main)" ;;

        k_del_yes:*)
            local nonce="${data#k_del_yes:}"
            local state_raw state extra name
            state_raw=$(bot_state_get "$chat_id" "$user_id")
            state="${state_raw%%|*}"
            extra="${state_raw#*|}"
            # P0.4: формат extra = "nonce:ts:name". Имя — всё что после второго ":"
            local extra_after_nonce extra_after_ts
            extra_after_nonce="${extra#*:}"
            name="${extra_after_nonce#*:}"
            # check_nonce_valid принимает stored="nonce:ts:..." и проверяет TTL
            if [ "$state" != "confirm_delete" ] || ! check_nonce_valid "$extra" "$nonce" || [ -z "$name" ]; then
                bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; }
                tg_edit "$chat_id" "$msg_id" "⚠️ Подтверждение удаления устарело или невалидно. Выберите ключ заново." "$(kbd_keys_back)"
                return
            fi
            bot_state_clear "$chat_id" "$user_id" || { tg_edit "$chat_id" "$msg_id" "Ошибка записи состояния. Действие не выполнено." "$(kbd_back)"; return 1; }
            if key_exists "$name"; then
                if delete_key "$name"; then
                    log_action "BOT KEY DELETE: name=${name}"
                    tg_edit "$chat_id" "$msg_id" "✅ Ключ <b>${name}</b> удалён." "$(kbd_keys_back)"
                else
                    tg_edit "$chat_id" "$msg_id" "❌ Удаление ключа не завершено. Проверьте диагностику." "$(kbd_keys_back)"
                fi
            else
                tg_edit "$chat_id" "$msg_id" "❌ Ключ <b>${name}</b> не найден." "$(kbd_keys_back)"
            fi ;;

        k_del_*)
            local name="${data#k_del_}"
            if key_exists "$name"; then
                local nonce masked
                nonce=$(make_callback_nonce)
                masked=$(mask_key "$(get_key_by_name "$name")")
                # P0.4: формат extra = "nonce:ts:name" для expiry-проверки + binding на имя
                bot_state_set "$chat_id" "confirm_delete" "${nonce}:$(date +%s):${name}" "$user_id"
                tg_edit "$chat_id" "$msg_id" "🗑 <b>Удалить ключ?</b>\n\n<b>${name}</b>\n<code>${masked}</code>\n\n<i>Подтверждение действительно ${NONCE_TTL_SEC} сек</i>" "$(kbd_delete_confirm "$nonce")"
            else
                tg_edit "$chat_id" "$msg_id" "❌ Ключ <b>${name}</b> не найден." "$(kbd_keys_back)"
            fi ;;
    esac
}

# ─── Обработчик текстовых сообщений (state-машина для ключа) ─

bot_handle_text_input() {
    # P1.3 (Codex 004): 4-й аргумент — user_id (нужен для определения «группа vs private»)
    local chat_id="$1" msg_id="$2" text="$3" user_id="${4:-}"

    if [ "$text" = "/cancel" ]; then
        bot_state_clear "$chat_id" "$user_id"
        tg_send "$chat_id" "❌ Отменено." "$(kbd_keys_back)" > /dev/null
        return
    fi

    # P1.3: secret-bearing flow (приём WARP+ ключа) ТОЛЬКО в private-чате.
    # В private chat.id == user.id; в группе они разные. Если задан BOT_USER_IDS
    # и кто-то начал add-key flow в группе — отвергаем до того, как ключ
    # попадёт в чат, чтобы он не светился членам группы.
    local is_private=1
    if [ -n "$user_id" ] && [ "$chat_id" != "$user_id" ]; then
        is_private=0
    fi

    local state_raw state extra
    state_raw=$(bot_state_get "$chat_id" "$user_id")
    state="${state_raw%%|*}"
    extra="${state_raw#*|}"

    # Если флоу секретный — требуем private
    if [ "$state" = "awaiting_keyname" ] || [ "$state" = "awaiting_keyvalue" ]; then
        if [ $is_private -eq 0 ]; then
            # Удаляем сообщение если можем (может содержать ключ если state=awaiting_keyvalue)
            tg_delete "$chat_id" "$msg_id" 2>/dev/null
            bot_state_clear "$chat_id" "$user_id"
            tg_send "$chat_id" "⛔ <b>Добавление WARP+ ключа разрешено только в private-чате с ботом.</b>\n\nОтправьте боту <code>/start</code> в личке." "" > /dev/null
            log_action "BOT KEY ADD: rejected, chat=${chat_id} user=${user_id} (group)"
            return
        fi
    fi

    case "$state" in
        awaiting_keyname)
            if [[ ! "$text" =~ ^[A-Za-z0-9_-]{1,32}$ ]]; then
                tg_send "$chat_id" "⚠️ Некорректное название. Допустимы a-z, 0-9, _, -, до 32 симв.\nПопробуйте ещё или /cancel." "" > /dev/null
                return
            fi
            if key_exists "$text"; then
                tg_send "$chat_id" "⚠️ Имя <b>${text}</b> уже занято. Выберите другое или /cancel." "" > /dev/null
                return
            fi
            bot_state_set "$chat_id" "awaiting_keyvalue" "$text" "$user_id"
            tg_send "$chat_id" "✅ Имя: <b>${text}</b>\n\nТеперь пришлите сам ключ.\n\n⚠️ <b>Ваше сообщение с ключом будет удалено</b> — это для безопасности." "" > /dev/null
            ;;

        awaiting_keyvalue)
            local name="$extra"
            # СРАЗУ удаляем сообщение с ключом, проверяем результат
            local delete_warning=""
            if ! tg_delete "$chat_id" "$msg_id"; then
                delete_warning="\n\n⚠️ <b>Не удалось удалить ваше сообщение с ключом</b> — удалите его из чата вручную."
                log_action "BOT KEY ADD: tg_delete FAILED for chat=${chat_id} msg=${msg_id}"
            fi
            if ! is_valid_warp_plus_key "$text"; then
                bot_state_clear "$chat_id" "$user_id"
                tg_send "$chat_id" "❌ Формат не похож на WARP+ ключ (<code>XXXXXXXX-XXXXXXXX-XXXXXXXX</code>; старый формат: <code>XXXX-XXXX-XXXX-XXXX</code>).\nПопробуйте через «➕ Добавить ключ».${delete_warning}" "$(kbd_keys_back)" > /dev/null
                return
            fi
            if [ "$name" = auto ]; then
                name="key-${text: -4}"
                local suffix=2
                while key_exists "$name"; do name="key-${text: -4}-$suffix"; suffix=$((suffix+1)); done
            fi
            add_key "$name" "$text"
            local add_rc=$?
            if [ $add_rc -ne 0 ]; then
                bot_state_clear "$chat_id" "$user_id"
                tg_send "$chat_id" "❌ Не удалось сохранить ключ (код $add_rc).${delete_warning}" "$(kbd_keys_back)" > /dev/null
                return
            fi
            log_action "BOT KEY ADD: name=${name}, key=$(mask_key "$text")"
            bot_state_clear "$chat_id" "$user_id"
            tg_send "$chat_id" "✅ Ключ <b>${name}</b> (<code>$(mask_key "$text")</code>) сохранён.\n\n<i>Нажмите кнопку с ключом и нужной подсистемой для применения.</i>${delete_warning}" "$(kbd_keys_back)" > /dev/null
            ;;

        *)
            tg_send "$chat_id" "Используйте /start или /menu" "" > /dev/null
            ;;
    esac
}

# ─── Bot daemon ───────────────────────────────────────────────

bot_daemon() {
    log_action "Bot daemon started (PID $$)"; echo $$ > "$BOT_PID_FILE"
    source "$WARP_CONF"
    [ -z "$BOT_TOKEN" ] && { log_action "BOT ERROR: no token"; exit 1; }
    if [ -z "$BOT_CHAT_ID" ]; then
        log_action "BOT ERROR: BOT_CHAT_ID not set — refusing to start (would allow open access)"
        exit 1
    fi
    # Сбрасываем state на старте — мог остаться от crash в середине add-flow
    bot_state_clear
    get_my_ip
    if has_awg_mode && [ -n "$CONTAINER" ]; then awg_load_container_data 2>/dev/null; fi
    local offset=0
    while true; do
        source "$WARP_CONF" 2>/dev/null
        local response
        response=$(curl -s --max-time 35 \
            "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${offset}&timeout=30" 2>/dev/null)
        [ -z "$response" ] && sleep 2 && continue
        local ok; ok=$(echo "$response" | jq -r '.ok // "false"')
        [ "$ok" != "true" ] && sleep 5 && continue
        local cnt; cnt=$(echo "$response" | jq '.result | length')
        for (( i=0; i<cnt; i++ )); do
            local upd; upd=$(echo "$response" | jq ".result[$i]")
            local uid; uid=$(echo "$upd" | jq -r '.update_id')
            offset=$((uid + 1))
            local cbd; cbd=$(echo "$upd" | jq -r '.callback_query.data // empty')
            if [ -n "$cbd" ]; then
                local cbi cci cmi cui
                cbi=$(echo "$upd" | jq -r '.callback_query.id')
                cci=$(echo "$upd" | jq -r '.callback_query.message.chat.id')
                cmi=$(echo "$upd" | jq -r '.callback_query.message.message_id')
                cui=$(echo "$upd" | jq -r '.callback_query.from.id // empty')
                # Fail-closed: from.id обязателен; group BOT_CHAT_ID может
                # принимать private-chat callbacks только от BOT_USER_IDS.
                if [ -z "$cui" ]; then
                    log_action "BOT AUTH DENY: empty from.id (channel post / anon admin) chat=${cci}"
                    tg_answer_cb "$cbi" "Нет доступа (анонимы запрещены)" > /dev/null
                    continue
                fi
                if ! bot_chat_allowed "$cci" "$cui"; then
                    tg_answer_cb "$cbi" "Нет доступа" > /dev/null
                    continue
                fi
                bot_handle_callback "$cci" "$cmi" "$cbi" "$cbd" "$cui"
            else
                local mci mtx mmi mui
                mci=$(echo "$upd" | jq -r '.message.chat.id // empty')
                mtx=$(echo "$upd" | jq -r '.message.text // empty')
                mmi=$(echo "$upd" | jq -r '.message.message_id // empty')
                mui=$(echo "$upd" | jq -r '.message.from.id // empty')
                if [ -n "$mci" ] && [ -n "$mtx" ]; then
                    # Fail-closed chat-id
                    # NEW P0.2: пустой mui (channel post / anon admin) — reject
                    if [ -z "$mui" ]; then
                        log_action "BOT AUTH DENY: empty from.id on msg chat=${mci}"
                        tg_send "$mci" "⛔ Нет доступа (анонимы запрещены)" "" > /dev/null
                        continue
                    fi
                    if ! bot_chat_allowed "$mci" "$mui"; then
                        log_action "BOT AUTH DENY: msg user=${mui} chat=${mci}"
                        tg_send "$mci" "⛔ Нет доступа.\nChat ID: <code>$mci</code>" "" > /dev/null
                        continue
                    fi
                    if [[ "$mtx" == "/start" || "$mtx" == "/menu" ]]; then
                        bot_state_clear "$mci" "$mui"
                        bot_main_menu "$mci"
                    else
                        bot_handle_text_input "$mci" "$mmi" "$mtx" "$mui"
                    fi
                fi
            fi
        done
    done
}

# ─── Bot menu (SSH) ──────────────────────────────────────────

start_bot() {
    source "$WARP_CONF"
    [ -z "$BOT_TOKEN" ] && { echo -e "${RED}Задайте BOT_TOKEN!${NC}"; return; }
    [ -f "$BOT_PID_FILE" ] && kill -0 "$(cat "$BOT_PID_FILE")" 2>/dev/null && { echo -e "${YELLOW}Уже запущен.${NC}"; return; }
    cat > /etc/systemd/system/warp-bot.service <<EOF
[Unit]
Description=WARP Manager Telegram Bot
After=network.target
[Service]
Type=simple
ExecStart=/usr/local/bin/gowarp --bot-daemon
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable warp-bot > /dev/null 2>&1; systemctl start warp-bot; sleep 1
    if systemctl is-active warp-bot &>/dev/null; then
        echo -e "${GREEN}[OK] Бот запущен.${NC}"; log_action "Bot started"
    else
        echo -e "${RED}[ERROR] journalctl -u warp-bot${NC}"
    fi
}

stop_bot() {
    systemctl stop warp-bot 2>/dev/null; systemctl disable warp-bot 2>/dev/null; rm -f "$BOT_PID_FILE"
    echo -e "${GREEN}[OK] Бот остановлен.${NC}"; log_action "Bot stopped"
}

bot_auto_chatid() {
    systemctl disable warp-bot 2>/dev/null
    systemctl stop warp-bot 2>/dev/null
    sleep 3
    local flush_resp last_uid
    flush_resp=$(curl -s --connect-timeout 10 --max-time 20 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=-1&limit=1")
    last_uid=$(echo "$flush_resp" | jq -r '.result[-1].update_id // empty' 2>/dev/null)
    if [ -n "$last_uid" ]; then
        curl -s --connect-timeout 10 --max-time 20 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=$((last_uid + 1))&limit=1" >/dev/null 2>&1
    fi
    echo -e "${YELLOW}  Отправьте боту любое сообщение в Telegram.${NC}"
    echo -e "${YELLOW}  Ожидаю до 30 секунд...${NC}"
    local attempts=0
    local c=""
    while [ "$attempts" -lt 6 ]; do
        sleep 5
        local resp; resp=$(curl -s --max-time 5 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?limit=5")
        c=$(echo "$resp" | jq -r '[.result[].message.chat.id // empty] | map(select(. != "")) | unique | first // empty' 2>/dev/null)
        if [ -n "$c" ] && [[ "$c" =~ ^[0-9-]+$ ]]; then
            break
        fi
        ((attempts++))
        echo -ne "  ${DIM}Жду сообщение... ($((attempts*5))/30с)${NC}\r"
    done
    echo ""
    if [ -n "$c" ] && [[ "$c" =~ ^[0-9-]+$ ]]; then
        save_config_val "BOT_CHAT_ID" "$c"; BOT_CHAT_ID="$c"
        echo -e "  ${GREEN}✓ Chat ID: $c${NC}"
        # P2.2 (Codex 004): отрицательный chat_id = группа. Без BOT_USER_IDS
        # бот fail-closed откажет ВСЕМ → бот будет unusable.
        if [[ "$c" =~ ^- ]] && [ -z "${BOT_USER_IDS:-}" ]; then
            echo ""
            echo -e "  ${YELLOW}⚠ Это chat_id ГРУППЫ (отрицательный).${NC}"
            echo -e "  ${YELLOW}  Без BOT_USER_IDS бот будет отказывать всем (private-only fallback).${NC}"
            echo -e "  ${WHITE}  Что сделать:${NC}"
            echo -e "  ${WHITE}  1) Получите свой user_id у ${CYAN}@userinfobot${WHITE} → /start${NC}"
            echo -e "  ${WHITE}  2) Впишите user_id в BOT_USER_IDS:${NC}"
            echo -e "     ${CYAN}sudo sed -i 's|^BOT_USER_IDS=.*|BOT_USER_IDS=\"YOUR_USER_ID\"|' /etc/warp-manager/config${NC}"
            echo -e "  ${WHITE}  Несколько user'ов через запятую: ${CYAN}BOT_USER_IDS=\"123,456,789\"${NC}"
        fi
        return 0
    else
        echo -e "  ${RED}✗ Не удалось определить Chat ID.${NC}"
        echo -e ""
        echo -e "  ${WHITE}Получите Chat ID вручную:${NC}"
        echo -e "  ${CYAN}1.${NC} Откройте ${GREEN}@userinfobot${NC} в Telegram"
        echo -e "  ${CYAN}2.${NC} Нажмите /start"
        echo -e "  ${CYAN}3.${NC} Скопируйте число ${WHITE}Id: XXXXXXXX${NC}"
        echo -e "  ${CYAN}4.${NC} Впишите через меню бота (п.4)"
        return 1
    fi
}

bot_wizard() {
    clear
    echo -e "\n${CYAN}━━━ Настройка Telegram-бота ━━━${NC}\n"

    echo -e "${YELLOW}Шаг 1/3.${NC} Введите токен бота"
    echo -e "${DIM}  Получить: @BotFather → /newbot → скопировать токен${NC}\n"
    read -p "  Токен: " t
    if [ -z "$t" ]; then echo -e "  ${RED}Отменено.${NC}"; read -p "Enter..."; return; fi
    save_config_val "BOT_TOKEN" "$t"; BOT_TOKEN="$t"
    echo -e "  ${GREEN}✓ Токен сохранён${NC}\n"

    echo -e "${YELLOW}Шаг 2/3.${NC} Определение Chat ID"
    echo -e "${DIM}  Отправьте боту сообщение в Telegram — ID определится автоматически${NC}\n"
    if ! bot_auto_chatid; then
        echo -e "\n  ${YELLOW}Можно задать вручную через меню бота (п.4).${NC}"
    fi
    echo ""

    echo -e "${YELLOW}Шаг 3/3.${NC} Запуск бота"
    start_bot
    echo ""

    if systemctl is-active warp-bot &>/dev/null; then
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}  ✅ Бот настроен и запущен!${NC}"
        echo -e "${GREEN}  Откройте бота в Telegram и нажмите /start${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    else
        echo -e "${YELLOW}  ⚠ Бот может не запуститься. Проверьте:${NC}"
        echo -e "  ${WHITE}journalctl -u warp-bot --no-pager -n 10${NC}"
    fi
    read -p "  Enter..."
}

bot_menu() {
    source "$WARP_CONF" 2>/dev/null
    if [ -z "${BOT_TOKEN:-}" ]; then
        bot_wizard
        return
    fi

    while true; do
        clear; source "$WARP_CONF" 2>/dev/null
        local bs="${RED}Выкл${NC}"
        systemctl is-active warp-bot &>/dev/null && bs="${GREEN}Вкл${NC}"
        local td="***${BOT_TOKEN: -6}"

        echo -e "\n${CYAN}━━━ Telegram Bot ━━━${NC}\n"
        echo -e "  Статус:  $bs"
        echo -e "  Токен:   ${YELLOW}$td${NC}"
        echo -e "  Chat ID: ${YELLOW}${BOT_CHAT_ID:-не задан (доступ закрыт)}${NC}\n"
        echo -e "  1) ${GREEN}Запустить${NC}"
        echo -e "  2) ${RED}Остановить${NC}"
        echo -e "  3) Изменить токен"
        echo -e "  4) Задать Chat ID (вручную)"
        echo -e "  5) Определить Chat ID (авто)"
        echo -e "  0) Назад"
        echo ""
        read -r -p "  Выбор: " ch || return 0
        case $ch in
            1) start_bot; read -p "  Enter..." ;;
            2) stop_bot; read -p "  Enter..." ;;
            3) echo "  Токен:"; read -p "  > " t
               [ -n "$t" ] && save_config_val "BOT_TOKEN" "$t" && BOT_TOKEN="$t" && echo -e "  ${GREEN}OK${NC}"
               read -p "  Enter..." ;;
            4) echo -e "\n  ${WHITE}Узнать Chat ID: ${GREEN}@userinfobot${WHITE} → /start → скопируйте Id${NC}"
               echo "  Chat ID:"; read -p "  > " c
               if [ -n "$c" ] && [[ "$c" =~ ^[0-9-]+$ ]]; then
                   save_config_val "BOT_CHAT_ID" "$c"; BOT_CHAT_ID="$c"
                   echo -e "  ${GREEN}✓ Chat ID: $c${NC}"
                   echo -e "  ${YELLOW}Перезапустите бота (п.1) чтобы применить.${NC}"
               else
                   echo -e "  ${RED}Некорректный ID${NC}"
               fi
               read -p "  Enter..." ;;
            5) bot_auto_chatid
               echo -e "  ${YELLOW}Перезапустите бота (п.1) чтобы применить.${NC}"
               read -p "  Enter..." ;;
            0) return ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════
#  PROMO / INFO
# ═══════════════════════════════════════════════════════════════

show_promo() {
    clear; echo ""
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo -e "${MAGENTA}  ХОСТИНГ СО СКИДКОЙ ДО -60%${NC}"
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo -e "\n${CYAN}🌍 РФ И ЕВРОПА${NC}\n${WHITE} >>> https://vk.cc/ct29NQ${NC}"
    printf " ${YELLOW}%-12s${NC} : ${WHITE}%s${NC}\n" "OFF60" "60% скидка" "antenka20" "+20% (3мес)" "antenka6" "+15% (6мес)" "antenka12" "+5% (12мес)"
    echo -e "\n${CYAN}🇧🇾 БЕЛАРУСЬ${NC}\n${WHITE} >>> https://vk.cc/cUxAhj${NC}"
    printf " ${YELLOW}%-12s${NC} : ${WHITE}%s${NC}\n" "OFF60" "60% скидка"
    echo -e "\n${YELLOW}QR-код...${NC}"; sleep 2
    echo -e "\n${WHITE}"; command -v qrencode &>/dev/null && qrencode -t ANSIUTF8 "https://vk.cc/ct29NQ" || echo "Ссылки выше."; echo -e "${NC}"
    read -p "Enter..."
}

show_info() {
    clear; echo ""
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo -e "${MAGENTA}  WARP Manager v${WARP_VERSION} — Инструкция${NC}"
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo ""
    if has_3xui_mode; then
        echo -e "${CYAN}═══ 3X-UI ═══${NC}\n"
        echo -e "${WHITE}  Клиент → 3X-UI (Xray) → SOCKS5 (WARP) → Cloudflare → Интернет${NC}\n"
        echo -e "${GREEN}  1.${NC} cloudflare-warp установлен нативно"
        echo -e "${GREEN}  2.${NC} SOCKS5-прокси на 127.0.0.1:${SOCKS_PORT}"
        echo -e "${GREEN}  3.${NC} В 3X-UI: outbound SOCKS → warp"
        echo -e "${GREEN}  4.${NC} Маршрутизация по доменам в Xray"
        echo ""
    fi
    if has_awg_mode; then
        echo -e "${CYAN}═══ AmneziaWG ═══${NC}\n"
        echo -e "${WHITE}  Клиент → AmneziaWG Docker → warp WG → Cloudflare → Интернет${NC}\n"
        echo -e "${GREEN}  1.${NC} wgcf генерирует WireGuard-профиль WARP"
        echo -e "${GREEN}  2.${NC} WG-интерфейс warp внутри Docker-контейнера"
        echo -e "${GREEN}  3.${NC} Маршрутизация per-client через ip rule"
        echo -e "${GREEN}  4.${NC} Персистентность через start.sh контейнера"
    fi
    echo -e "\n${GREEN}  ✓${NC}  Разблокировка ChatGPT, Netflix, Disney+, Spotify"
    echo -e "${GREEN}  ✓${NC}  Чистый IP от Cloudflare"
    echo -e "${GREEN}  ✓${NC}  Telegram-бот"
    echo -e "${GREEN}  ✓${NC}  Бесплатно (Cloudflare WARP)"
    echo ""; read -p "Enter..."
}

# ═══════════════════════════════════════════════════════════════
#  FULL UNINSTALL
# ═══════════════════════════════════════════════════════════════

full_uninstall() {
    clear
    echo -e "\n${RED}**********************************************************${NC}"
    echo -e "${RED}  ПОЛНОЕ УДАЛЕНИЕ WARP MANAGER${NC}"
    echo -e "${RED}**********************************************************${NC}\n"
    echo -e "${WHITE}Режим: ${CYAN}${MODE}${NC}\n"
    read -p "$(echo -e "${RED}Удалить полностью? (y/n): ${NC}")" c1
    [[ "$c1" != "y" ]] && return

    local operation_fd
    exec {operation_fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    flock -n "$operation_fd" || { exec {operation_fd}>&-; echo "Другая операция AmneziaWG уже выполняется."; return 1; }
    [ ! -f "$WARP_DIR/3xui-recovery" ] || { exec {operation_fd}>&-; echo "Сначала завершите восстановление 3X-UI; данные сохранены."; return 1; }
    # P0.1: full_uninstall чистит ВСЕ контейнеры без выбора
    if has_awg_mode && ! uninstall_awg_locked all; then
        echo -e "${RED}Удаление контейнеров не завершено. Менеджер и данные восстановления сохранены.${NC}"
        exec {operation_fd}>&-
        return 1
    fi

    if has_3xui_mode; then uninstall_3xui || { exec {operation_fd}>&-; return 1; }; fi
    # P1.2 fix (Codex 002): log_action ДО удаления лога, иначе файл пересоздаётся.
    health_service_remove
    log_action "UNINSTALL: full removal ($MODE) — about to remove $WARP_DIR and $WARP_LOG"
    rm -rf "$WARP_DIR" "$WARP_LOG"
    echo -e "  ${GREEN}✓${NC}  Конфигурация и логи"
    rm -f /usr/local/bin/gowarp
    echo -e "  ${GREEN}✓${NC}  Команда gowarp"

    echo -e "\n${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  WARP Manager полностью удалён.${NC}"
    has_3xui_mode && echo -e "${WHITE}  Уберите outbound \"warp\" из 3X-UI!${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    # НЕ вызываем log_action после rm $WARP_LOG — иначе recreate
    read -p "Enter..."
    exit 0
}

# ═══════════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════════

show_menu() {
    while true; do
        clear; source "$WARP_CONF" 2>/dev/null
        local mode_label="3X-UI"
        [ "$MODE" = "amnezia" ] && mode_label="AmneziaWG"
        [ "$MODE" = "both" ] && mode_label="3X-UI + AmneziaWG"
        echo -e "${MAGENTA}**********************************************************"
        echo -e "  potap1978 · WARP Manager v${WARP_VERSION}      YouTube: @antenkaru"
        echo -e "**********************************************************${NC}"
        echo -e "  ${WHITE}Сервер:${NC} ${GREEN}${MY_IP}${NC}   ${WHITE}Режим:${NC} ${CYAN}${mode_label}${NC}"
        local any_running=0
        if has_3xui_mode; then
            local st3 c3="$RED"; st3=$(get_warp_status_3xui)
            [ "$st3" = "Подключён" ] && { c3="$GREEN"; any_running=1; }
            [ "$st3" = "Отключён" ] && c3="$YELLOW"
            echo -e "  ${WHITE}3X-UI:${NC}     ${c3}${st3}${NC} ${DIM}·${NC} $(get_mode_text_3xui) ${DIM}· SOCKS5 127.0.0.1:${SOCKS_PORT}${NC}"
        fi
        if has_awg_mode; then
            awg_discover_containers >/dev/null 2>&1
            if [ ${#AWG_ALL_CONTAINERS[@]} -eq 0 ]; then
                echo -e "  ${WHITE}AmneziaWG:${NC} ${RED}контейнеры amnezia-awg не найдены${NC}"
            else
                local _c
                for _c in "${AWG_ALL_CONTAINERS[@]}"; do
                    local _w="${DIM}WARP не установлен${NC}" _n=""
                    if docker exec "$_c" sh -c "[ -f '$AWG_WARP_CONF' ]" 2>/dev/null; then
                        if docker exec "$_c" sh -c "ip addr show warp >/dev/null 2>&1" 2>/dev/null; then
                            _w="${GREEN}Подключён${NC}"; any_running=1
                        else
                            _w="${YELLOW}Отключён${NC}"
                        fi
                        _n=$(docker exec "$_c" sh -c "grep -c . /opt/warp/clients.list 2>/dev/null || true" 2>/dev/null | tr -d '\r\n')
                        _n=" ${DIM}· клиентов через WARP: ${_n:-0}${NC}"
                    fi
                    echo -e "  ${WHITE}AmneziaWG:${NC} ${CYAN}${_c}${NC} ${_w}${_n}"
                done
                echo -e "  ${WHITE}Режим AWG:${NC} $(get_mode_text_awg)"
                if awg_shared_key_detected; then
                    echo -e "  ${YELLOW}⚠ Контейнеры используют одну регистрацию WARP; возможны потери пакетов. Нажмите 3.${NC}"
                fi
                _pend=$(for _f in "$HOST_WGCF_BAK_ROOT"/*/recovery-required; do [ -f "$_f" ] && basename "$(dirname "$_f")"; done | tr '\n' ' ')
                [ -z "$_pend" ] || echo -e "  ${YELLOW}⚠ Перевыпуск WARP не завершён: $(awg_list "$_pend"). Нажмите 3 — goWARP доведёт его до конца.${NC}"
                _pend=$(for _f in "$HOST_WGCF_BAK_ROOT"/*/container/recovery-required; do _d=${_f%/container/recovery-required}; [ -f "$_f" ] && [ ! -f "$_d/recovery-required" ] && basename "$_d"; done | tr '\n' ' ')
                [ -z "$_pend" ] || echo -e "  ${YELLOW}⚠ Смена клиентов WARP не откатилась: $(awg_list "$_pend"). Откройте п.5 и примените выбор — goWARP сначала вернёт прежний.${NC}"
            fi
        fi
        local bs="${DIM}не настроен${NC}"
        if [ -n "${BOT_TOKEN:-}" ]; then
            systemctl is-active warp-bot &>/dev/null && bs="${GREEN}работает${NC}" || bs="${YELLOW}остановлен${NC}"
        fi
        echo -e "  ${WHITE}Бот:${NC}       ${bs}"
        local health_line=""
        if systemctl is-active "$(basename "$HEALTH_SERVICE")" >/dev/null 2>&1; then
            health_line="${GREEN}работает${NC}, каждые ${HEALTH_INTERVAL} с"
            local hf="$WARP_DIR/health.last" hday hcount hevent hat
            if [ -f "$hf" ]; then
                hday=$(sed -n 's/^day=//p' "$hf"); hcount=$(sed -n 's/^reconnects=//p' "$hf")
                hevent=$(sed -n 's/^last_event=//p' "$hf"); hat=$(sed -n 's/^last_event_at=//p' "$hf")
                if [ "$hday" = "$(date +%Y%m%d)" ] && [ "${hcount:-0}" != 0 ]; then
                    health_line+=" · переподключений сегодня: ${hcount}"
                fi
                [ -n "$hevent" ] && [ -n "$hat" ] && health_line+=" ${DIM}· $(date -d "@$hat" +%H:%M 2>/dev/null) ${hevent}${NC}"
            fi
        elif health_anything_installed; then
            health_line="${YELLOW}не запущена${NC} ${DIM}(systemctl status gowarp-health)${NC}"
        fi
        [ -z "$health_line" ] || echo -e "  ${WHITE}Автопроверка:${NC} ${health_line}"
        if [ -n "$UPGRADED_FROM" ]; then
            local upgrade_text="выбор клиентов WARP — по ключам, переживает смену подсети и пересоздание контейнеров."
            if [ "$(printf '%s\n2.5.1\n' "${UPGRADED_FROM%-}" | sort -V | head -1)" != 2.5.1 ]; then
                upgrade_text="WARP в контейнерах — на стандартном WireGuard, автопроверка следит за задержкой; ${upgrade_text}"
            fi
            if [ "$(printf '%s\n2.5.0\n' "${UPGRADED_FROM%-}" | sort -V | head -1)" != 2.5.0 ]; then
                upgrade_text="автопроверка WARP каждые 10 с и самолечение при обрыве; ${upgrade_text}"
            fi
            echo -e "  ${CYAN}✨ Обновлено с v${UPGRADED_FROM}: ${upgrade_text}${NC}"
            [ -z "${UPGRADE_NOTES:-}" ] || echo -e "  ${CYAN}   ${UPGRADE_NOTES% }${NC}"
        fi
        echo -e "${CYAN}──────────────────────────────────────────────────────${NC}"
        echo -e "  1) ${GREEN}Установить / переустановить WARP${NC}"
        if [ "$any_running" -eq 1 ]; then
            echo -e "  2) ${YELLOW}Выключить WARP${NC}"
        else
            echo -e "  2) ${CYAN}Включить WARP${NC}"
        fi
        echo -e "  3) 🔑 Сменить WARP IP (перевыпуск)"
        echo -e "  4) ⭐ ${MAGENTA}WARP+ ключ${NC} ${DIM}($(keys_count) сохр.)${NC}"
        has_awg_mode  && echo -e "  5) 👥 Клиенты AmneziaWG через WARP"
        has_3xui_mode && echo -e "  6) 📋 Настройка 3X-UI: JSON, инструкция, порт"
        echo -e "  7) 🤖 Telegram-бот"
        echo -e "  8) 📊 Статус и диагностика"
        echo -e "  9) 📚 Инфо и промо"
        echo -e " 10) ${RED}Удалить WARP Manager полностью${NC}"
        echo -e "  0) Выход"
        echo -e "${CYAN}──────────────────────────────────────────────────────${NC}"
        local ch
        read -r -p "  Выбор: " ch || return 0
        case "$ch" in
            1)  if has_3xui_mode; then install_warp_3xui; fi
                if has_awg_mode; then install_warp_awg; fi ;;
            2)  if [ "$any_running" -eq 1 ]; then
                    if has_3xui_mode; then stop_warp_3xui; fi
                    if has_awg_mode; then stop_warp_awg; fi
                else
                    if has_3xui_mode; then start_warp_3xui; fi
                    if has_awg_mode; then start_warp_awg; fi
                fi ;;
            3)  rekey_all_menu ;;
            4)  plus_menu ;;
            5)  if has_awg_mode; then awg_toggle_clients_ssh; fi ;;
            6)  if has_3xui_mode; then show_3xui_menu; fi ;;
            7)  bot_menu ;;
            8)  show_status_all ;;
            9)  show_info; show_promo ;;
            10) full_uninstall ;;
            0)  exit 0 ;;
        esac
    done
}

# Пункт 8: один экран вместо трёх (статус 3X-UI, таблица контейнеров, проверка WARP+)
show_status_all() {
    clear; echo -e "\n${CYAN}━━━ Статус и диагностика ━━━${NC}\n"
    echo -e "  ${WHITE}Реальный IP сервера:${NC} ${GREEN}${MY_IP}${NC}"
    if has_3xui_mode; then
        echo -e "\n${CYAN}── 3X-UI (warp-cli, SOCKS5) ──${NC}"
        if is_warp_installed_3xui; then
            echo -e "  Статус: $(get_warp_status_3xui)   SOCKS5: ${GREEN}127.0.0.1:${SOCKS_PORT}${NC}"
            is_warp_running_3xui && echo -e "  WARP IP: ${GREEN}$(get_warp_ip_3xui)${NC}"
            warp-cli --accept-tos status 2>/dev/null | head -3 | while IFS= read -r l; do echo -e "  ${DIM}${l}${NC}"; done
        else
            echo -e "  ${DIM}не установлен (п.1)${NC}"
        fi
    fi
    if has_awg_mode; then
        echo -e "\n${CYAN}── AmneziaWG (контейнеры) ──${NC}"
        awg_discover_containers >/dev/null 2>&1
        printf "  ${WHITE}%-16s %-8s %-8s %-9s %s${NC}\n" "Контейнер" "Статус" "WARP" "Клиентов" "WARP IP"
        local c
        for c in "${AWG_ALL_CONTAINERS[@]}"; do
            local status="${GREEN}up${NC}"; docker exec "$c" sh -c "true" 2>/dev/null || status="${RED}down${NC}"
            local w="${DIM}нет${NC}" n="—" ip="—"
            if docker exec "$c" sh -c "[ -f '/opt/warp/warp.conf' ]" 2>/dev/null; then
                if docker exec "$c" sh -c "ip addr show warp >/dev/null 2>&1" 2>/dev/null; then w="${GREEN}вкл${NC}"; else w="${YELLOW}выкл${NC}"; fi
                n=$(docker exec "$c" sh -c "grep -c . /opt/warp/clients.list 2>/dev/null || true" 2>/dev/null | tr -d '\r\n')
                ip=$(docker exec "$c" sh -c "curl -s -4 --interface warp --max-time 4 https://ifconfig.me 2>/dev/null" 2>/dev/null | tr -d '\r\n')
                [ -n "$ip" ] || ip="?"
            fi
            printf "  %-16s %-19b %-19b %-9s %s\n" "$c" "$status" "$w" "$n" "$ip"
        done
    fi
    echo ""
    plus_check_now
    echo ""; read -r -p "  Enter..."
}

# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

run_startup() {
    local total=6 s=0

    clear; echo ""
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo -e "${MAGENTA}  WARP Manager v${WARP_VERSION} — Загрузка${NC}"
    echo -e "${MAGENTA}**********************************************************${NC}"
    echo ""

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Проверка root..." "$s" "$total"
    check_root
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   root OK                                    \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Лицензия..." "$s" "$total"
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Лицензия активна                           \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Зависимости..." "$s" "$total"
    check_deps
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Зависимости OK                             \n" "$s" "$total"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Установка gowarp..." "$s" "$total"
    local inst upgrade_msg upgrade_icon="${GREEN}✓${NC}"
    inst=$(self_install_gowarp)
    case "$inst" in
        installed) upgrade_msg="установлена" ;;
        upgraded)  upgrade_msg="обновлена до v${WARP_VERSION}" ;;
        same)      upgrade_msg="v${WARP_VERSION}" ;;
        *)         upgrade_msg="НЕ обновлена: запускайте скрипт из файла, не через bash <(curl …)"
                   upgrade_icon="${YELLOW}⚠${NC}" ;;
    esac
    printf "\r  ${CYAN}[%d/%d]${NC}  %b   Команда gowarp %s                    \n" "$s" "$total" "$upgrade_icon" "$upgrade_msg"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Определение IP..." "$s" "$total"
    get_my_ip
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   IP: %-25s             \n" "$s" "$total" "$MY_IP"

    ((s++))
    printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Определение режима..." "$s" "$total"
    detect_mode
    local mode_label="3X-UI"
    [ "$MODE" = "amnezia" ] && mode_label="AmneziaWG"
    [ "$MODE" = "both" ] && mode_label="3X-UI + AmneziaWG"
    has_awg_mode && ((total++))
    has_3xui_mode && ((total++))
    printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Режим: %-25s           \n" "$s" "$total" "$mode_label"
    case "$inst" in installed|upgraded|same) run_upgrade_hooks ;; esac

    if has_awg_mode; then
        ((s++))
        printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Docker контейнер..." "$s" "$total"
        if awg_pick_container 2>/dev/null; then
            awg_load_container_data 2>/dev/null
            printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   Контейнер: %-20s        \n" "$s" "$total" "$CONTAINER"
        else
            printf "\r  ${CYAN}[%d/%d]${NC}  ${YELLOW}⚠${NC}   Контейнер не найден                     \n" "$s" "$total"
        fi
    fi
    if has_3xui_mode; then
        ((s++))
        printf "  ${CYAN}[%d/%d]${NC}  ${YELLOW}⏳${NC}  Проверка WARP (3X-UI)..." "$s" "$total"
        local ws; ws=$(get_warp_status_3xui)
        printf "\r  ${CYAN}[%d/%d]${NC}  ${GREEN}✓${NC}   3X-UI WARP: %-20s        \n" "$s" "$total" "$ws"
    fi

    echo ""
    local w=40 bar=""
    for ((i=0; i<w; i++)); do bar+="█"; done
    echo -e "  ${CYAN}[${GREEN}${bar}${CYAN}]${NC} ${GREEN}100%${NC}"
    echo -e "\n  ${GREEN}✅  WARP Manager v${WARP_VERSION} (${mode_label}) готов!${NC}\n"
    sleep 1

    show_menu
}

# ═══════════════════════════════════════════════════════════════
#  HEALTH — автопроверка каждые 10 секунд и самолечение
# ═══════════════════════════════════════════════════════════════
# Служба gowarp-health крутит health_tick каждые HEALTH_INTERVAL секунд.
# Локальные проверки дешёвые и идут каждый тик. Проверка трафика идёт раз в
# HEALTH_PROBE_EVERY секунд, после провала — каждый тик. Лечение идёт по
# ступеням, между неудачами пауза растёт до HEALTH_BACKOFF_MAX.
# Ручное выключение не перебивается: для 3X-UI это WARP_WANTED_3XUI=off,
# для AmneziaWG — маркер /opt/warp/disabled внутри контейнера.
# Пока идёт операция меню или бота, тик для этой подсистемы пропускается.
# Выбор WARP+ служба не меняет: если трафик идёт как Free, она повторяет ключ.

declare -A HEALTH_LEVEL=() HEALTH_NEXT=() HEALTH_BACKOFF=() HEALTH_BAD=()
declare -A HEALTH_PROBE_AT=() HEALTH_PROBE_FAILS=() HEALTH_RX0=() HEALTH_WARNED=() HEALTH_STATUS_BAD=()
declare -A HEALTH_KIND_NEXT=() HEALTH_KIND_BACKOFF=() HEALTH_RTT_SLOW=() HEALTH_RTT_WARNED=() HEALTH_BLOCK_NEXT=()
HEALTH_LAST_TOUCH=0
# 3X-UI: итог последней проверки трафика (plus|free|off|пусто) и повтор ключа WARP+.
HEALTH_TRACE_3XUI="" HEALTH_PLUS_FREE_3XUI=0 HEALTH_PLUS_BACKOFF_3XUI=0 HEALTH_PLUS_NEXT_3XUI=0

health_now() { echo "${HEALTH_NOW:-$(date +%s)}"; }

# Значение прямо из файла конфига: пока шла проверка, меню или бот могли его сменить.
health_conf() { awk -F'"' -v k="$1=" 'index($0, k) == 1 {print $2; exit}' "$WARP_CONF" 2>/dev/null; }

health_docker() {
    if [ -z "${HEALTH_NO_TIMEOUT:-}" ] && command -v timeout >/dev/null 2>&1; then
        timeout -k 5 25 docker "$@"
    else
        docker "$@"
    fi
}

health_cli() {
    if [ -z "${HEALTH_NO_TIMEOUT:-}" ] && command -v timeout >/dev/null 2>&1; then
        timeout -k 3 15 warp-cli --accept-tos "$@"
    else
        warp-cli --accept-tos "$@"
    fi
}

# Взаимоисключение операций 3X-UI между меню, ботом и автопроверкой.
# Вложенные вызовы в том же процессе проходят без повторного захвата.
with_3xui_lock() {
    if [ -n "${THREEXUI_LOCK_HELD:-}" ]; then "$@"; return; fi
    local fd rc
    exec {fd}>"$WARP_DIR/.3xui-operation.lock" || { "$@"; return; }
    if ! flock -w 60 "$fd"; then
        exec {fd}>&-
        echo "3X-UI: другая операция ещё выполняется, повторите позже." >&2
        return 1
    fi
    THREEXUI_LOCK_HELD=1
    "$@"; rc=$?
    unset THREEXUI_LOCK_HELD
    exec {fd}>&-
    return "$rc"
}

health_anything_installed() {
    if has_3xui_mode && is_warp_installed_3xui; then return 0; fi
    if has_awg_mode && [ -e "$WGCF_BIN" ]; then return 0; fi
    return 1
}

health_service_install() {
    command -v systemctl >/dev/null 2>&1 || return 0
    local unit tmp
    unit=$(basename "$HEALTH_SERVICE")
    tmp=$(mktemp "${HEALTH_SERVICE}.XXXXXX") || return 1
    cat > "$tmp" <<EOF
[Unit]
Description=goWARP: проверка WARP каждые ${HEALTH_INTERVAL} с и автопереподключение
After=network-online.target docker.service warp-svc.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=${GOWARP_BIN} --health-daemon
Restart=always
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
EOF
    if [ -f "$HEALTH_SERVICE" ] && cmp -s "$tmp" "$HEALTH_SERVICE"; then
        rm -f "$tmp"
    else
        chmod 644 "$tmp" && mv -f "$tmp" "$HEALTH_SERVICE" || { rm -f "$tmp"; return 1; }
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    systemctl enable --now "$unit" >/dev/null 2>&1
}

health_service_remove() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$(basename "$HEALTH_SERVICE")" >/dev/null 2>&1 || true
    fi
    rm -f "$HEALTH_SERVICE" "$WARP_DIR/health.last"
    if command -v systemctl >/dev/null 2>&1; then systemctl daemon-reload >/dev/null 2>&1 || true; fi
    return 0
}

# Итог для шапки меню. $1: reconnect|fail|ok|warn|tick, $2: событие (пусто — оставить прежнее).
health_summary() {
    local f="$WARP_DIR/health.last" today n=0 day="" event="" event_at="" tmp
    [ -d "$WARP_DIR" ] || return 0
    today=$(date +%Y%m%d)
    if [ -f "$f" ]; then
        day=$(sed -n 's/^day=//p' "$f"); n=$(sed -n 's/^reconnects=//p' "$f")
        event=$(sed -n 's/^last_event=//p' "$f"); event_at=$(sed -n 's/^last_event_at=//p' "$f")
    fi
    [ "$day" = "$today" ] || n=0
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    [ "$1" != reconnect ] || n=$((n + 1))
    if [ -n "${2:-}" ]; then event="$2"; event_at=$(health_now); fi
    tmp=$(mktemp "$f.XXXXXX" 2>/dev/null) || return 0
    if printf 'last_run=%s\nday=%s\nreconnects=%s\nlast_event=%s\nlast_event_at=%s\n' \
        "$(health_now)" "$today" "$n" "$event" "$event_at" > "$tmp"; then
        chmod 600 "$tmp" && mv -f "$tmp" "$f" || rm -f "$tmp"
    else
        rm -f "$tmp"
    fi
}

health_touch() {
    local now; now=$(health_now)
    [ $((now - HEALTH_LAST_TOUCH)) -ge 60 ] || return 0
    HEALTH_LAST_TOUCH=$now
    health_summary tick ""
}

health_clear() {
    unset "HEALTH_LEVEL[$1]" "HEALTH_NEXT[$1]" "HEALTH_BACKOFF[$1]" "HEALTH_BAD[$1]" \
        "HEALTH_PROBE_FAILS[$1]" "HEALTH_RX0[$1]" "HEALTH_STATUS_BAD[$1]"
}

health_mark_ok() {
    local key="$1" label="$2"
    if [ -n "${HEALTH_BAD[$key]:-}" ]; then
        log_action "HEALTH ${label}: снова работает"
        health_summary ok "${label}: снова работает"
    fi
    health_clear "$key"
}

health_due() { [ "$2" -ge "${HEALTH_NEXT[$1]:-0}" ]; }

# Итог попытки: успех сбрасывает всё, неудача поднимает ступень и удваивает паузу.
health_after_heal() {
    local key="$1" ok="$2" now="$3" label="$4" what="$5" max_level="$6" level backoff
    if [ "$ok" = 1 ]; then
        log_action "HEALTH ${label}: ${what} — помогло"
        health_summary reconnect "${label}: ${what}"
        health_clear "$key"
        return 0
    fi
    HEALTH_BAD[$key]=1
    level=$(( ${HEALTH_LEVEL[$key]:-1} + 1 ))
    [ "$level" -le "$max_level" ] || level=$max_level
    HEALTH_LEVEL[$key]=$level
    backoff=${HEALTH_BACKOFF[$key]:-0}
    if [ "$backoff" -eq 0 ]; then backoff=10; else backoff=$((backoff * 2)); fi
    [ "$backoff" -le "$HEALTH_BACKOFF_MAX" ] || backoff=$HEALTH_BACKOFF_MAX
    HEALTH_BACKOFF[$key]=$backoff
    HEALTH_NEXT[$key]=$((now + backoff))
    log_action "HEALTH ${label}: ${what} — не помогло, следующая попытка через ${backoff} с, ступень ${level}"
    health_summary fail "${label}: ${what} не помогло"
    return 1
}

# Трафик идёт через WARP (free или plus). Сам итог остаётся в HEALTH_TRACE_3XUI.
health_trace_ok_3xui() {
    HEALTH_TRACE_3XUI=$(verify_warp_plus_3xui 2>/dev/null) || HEALTH_TRACE_3XUI=""
    [ "$HEALTH_TRACE_3XUI" = free ] || [ "$HEALTH_TRACE_3XUI" = plus ]
}

# API Cloudflare напрямую, мимо SOCKS: без него новая регистрация не пройдёт.
# Доступен — любой HTTP-код, кроме 000.
health_cf_api_reachable() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 4 --max-time 6 https://api.cloudflareclient.com/ 2>/dev/null)
    [ -n "$code" ] && [ "$code" != 000 ]
}

# Применение ключа при отказе пишет в конфиг Free, а служба выбор человека не меняет.
# $1 $2 — режим и имя ключа до попытки. Код 0 — выбор WARP+ возвращён в конфиг.
health_keep_plus_3xui() {
    [ "$1" = plus ] && [ -n "$2" ] || return 1
    [ "$(health_conf WARP_KEY_MODE_3XUI)" != plus ] || [ "$(health_conf ACTIVE_KEY_NAME_3XUI)" != "$2" ] || return 1
    save_config_val WARP_KEY_MODE_3XUI plus ACTIVE_KEY_NAME_3XUI "$2" && return 0
    log_action "HEALTH 3X-UI: не удалось вернуть выбор WARP+ (${2}) в конфиг"
    return 1
}

health_tick_3xui() {
    has_3xui_mode || return 0
    is_warp_installed_3xui || return 0
    if [ "${WARP_WANTED_3XUI:-on}" != on ]; then health_clear 3xui; return 0; fi
    [ ! -f "$WARP_DIR/3xui-recovery" ] || return 0
    # Проверка только читает состояние и блокировку не берёт: иначе действия меню
    # и бота раз в 10 секунд натыкались бы на занятую блокировку.
    health_check_3xui
    return 0
}

health_check_3xui() {
    local key=3xui label="3X-UI" now st need="" status_need="" required=0 level what reason ok=0 i
    local api_down=0 snap_mode="" snap_name="" plus_kept=0 rc
    now=$(health_now)
    st=$(health_cli status 2>/dev/null) || st=""
    # Статус считается сбоем, только если держится несколько тиков подряд: сразу после
    # рестарта warp-svc (обновление пакета) он секунды бывает Unable / Registration Missing
    # due to: Daemon Startup. По одному снимку новая регистрация сменила бы устройство.
    case "$st" in
        *"Registration Missing"*) status_need=register; required=6 ;;
        *"Status update: Connected"*) ;;
        *"Status update: Connecting"*) status_need=connect; required=3 ;;
        *"Status update: Disconnected"*|*"Status update: Unable"*) status_need=connect; required=2 ;;
        "") status_need=daemon; required=3 ;;
    esac
    if [ -n "$status_need" ]; then
        HEALTH_STATUS_BAD[$key]=$(( ${HEALTH_STATUS_BAD[$key]:-0} + 1 ))
        [ "${HEALTH_STATUS_BAD[$key]}" -ge "$required" ] || return 0
        need=$status_need
    else
        HEALTH_STATUS_BAD[$key]=0
    fi
    if [ -z "$need" ] && [[ "$st" == *"Status update: Connected"* ]]; then
        local fails=${HEALTH_PROBE_FAILS[$key]:-0} last=${HEALTH_PROBE_AT[$key]:-0}
        if [ "$fails" -gt 0 ] || [ $((now - last)) -ge "$HEALTH_PROBE_EVERY" ]; then
            HEALTH_PROBE_AT[$key]=$now
            if health_trace_ok_3xui; then
                health_mark_ok "$key" "$label"
                health_plus_retry_3xui "$now"
                return 0
            fi
            fails=$((fails + 1)); HEALTH_PROBE_FAILS[$key]=$fails
            [ "$fails" -lt "$HEALTH_PROBE_FAILS_MAX" ] || need=reconnect
        fi
    fi
    [ -n "$need" ] || return 0
    health_due "$key" "$now" || return 0
    # Чиним только под блокировкой операций 3X-UI и перечитав намерение из конфига:
    # пока шла проверка, человек мог выключить WARP или запустить перевыпуск.
    local fd
    exec {fd}>"$WARP_DIR/.3xui-operation.lock" || return 0
    if ! flock -n "$fd"; then exec {fd}>&-; return 0; fi
    if [ "$(health_conf WARP_WANTED_3XUI)" = off ] || [ -f "$WARP_DIR/3xui-recovery" ]; then
        exec {fd}>&-; health_clear "$key"; return 0
    fi
    THREEXUI_LOCK_HELD=1
    level=${HEALTH_LEVEL[$key]:-1}
    [ "$need" != daemon ] || [ "$level" -ge 3 ] || level=3
    [ "$need" != register ] || level=4
    # Новая регистрация сначала удаляет старую, а без API Cloudflare новую не получить:
    # тогда только перезапуск warp-svc. Пропавшую регистрацию терять нечего.
    if [ "$level" -ge 4 ] && [ "$need" != register ] && ! health_cf_api_reachable; then
        level=3; api_down=1
    fi
    reason=$(printf '%s\n' "$st" | awk '/^Reason: /{sub(/^Reason: /, ""); print; exit}')
    [ -n "$reason" ] || reason=$([ "$need" = reconnect ] && echo "трафик не идёт" || echo "$need")
    case "$level" in
        1) what="переподключение"
           health_cli mode proxy >/dev/null 2>&1
           health_cli proxy port "$SOCKS_PORT" >/dev/null 2>&1
           health_cli connect >/dev/null 2>&1 ;;
        2) what="разрыв и новое подключение"
           health_cli disconnect >/dev/null 2>&1; sleep 2
           health_cli mode proxy >/dev/null 2>&1
           health_cli proxy port "$SOCKS_PORT" >/dev/null 2>&1
           health_cli connect >/dev/null 2>&1 ;;
        3) what="перезапуск службы warp-svc"
           [ "$api_down" = 0 ] || what="перезапуск службы warp-svc (API Cloudflare недоступен, регистрацию не трогаем)"
           systemctl restart warp-svc >/dev/null 2>&1; sleep 5
           health_cli mode proxy >/dev/null 2>&1
           health_cli proxy port "$SOCKS_PORT" >/dev/null 2>&1
           health_cli connect >/dev/null 2>&1 ;;
        *) what="новая регистрация"
           # Выбор WARP+ берём из файла: переменные тика могли устареть, пока шла проверка.
           snap_mode=$(health_conf WARP_KEY_MODE_3XUI); snap_name=$(health_conf ACTIVE_KEY_NAME_3XUI)
           WARP_KEY_MODE_3XUI="$snap_mode"; ACTIVE_KEY_NAME_3XUI="$snap_name"
           _rekey_3xui_core_locked >/dev/null 2>&1
           if health_keep_plus_3xui "$snap_mode" "$snap_name"; then plus_kept=1; fi ;;
    esac
    for i in 1 2 3 4 5; do
        sleep 3
        if health_trace_ok_3xui; then ok=1; break; fi
    done
    unset THREEXUI_LOCK_HELD
    exec {fd}>&-
    health_after_heal "$key" "$ok" "$now" "$label" "${what}, было: ${reason}" 4; rc=$?
    if [ "$plus_kept" = 1 ]; then
        log_action "HEALTH ${label}: после новой регистрации WARP+ (${snap_name}) не подтвердился; выбор WARP+ сохранён, служба повторит ключ позже"
        health_summary warn "${label}: WARP+ (${snap_name}) не подтвердился, выбор сохранён"
    fi
    return "$rc"
}

# WARP+ выбран, а трафик идёт как Free: после HEALTH_PLUS_FREE_MAX таких проверок подряд
# ключ применяется ещё раз. Неудача — пауза от часа, дальше вдвое, до суток.
# В журнал попадает только имя ключа, сам ключ — никогда.
health_plus_retry_3xui() {
    local now="$1" name="${ACTIVE_KEY_NAME_3XUI:-}" fd ok=0 i backoff
    if [ "${WARP_KEY_MODE_3XUI:-}" != plus ] || [ -z "$name" ]; then
        HEALTH_PLUS_FREE_3XUI=0; HEALTH_PLUS_BACKOFF_3XUI=0; HEALTH_PLUS_NEXT_3XUI=0
        return 0
    fi
    if [ "$HEALTH_TRACE_3XUI" != free ]; then HEALTH_PLUS_FREE_3XUI=0; return 0; fi
    HEALTH_PLUS_FREE_3XUI=$((HEALTH_PLUS_FREE_3XUI + 1))
    [ "$HEALTH_PLUS_FREE_3XUI" -ge "$HEALTH_PLUS_FREE_MAX" ] || return 0
    [ "$now" -ge "$HEALTH_PLUS_NEXT_3XUI" ] || return 0
    exec {fd}>"$WARP_DIR/.3xui-operation.lock" || return 0
    if ! flock -n "$fd"; then exec {fd}>&-; return 0; fi
    # Под блокировкой перечитываем конфиг: WARP могли выключить или сменить выбор WARP+.
    if [ "$(health_conf WARP_WANTED_3XUI)" = off ] || [ -f "$WARP_DIR/3xui-recovery" ] ||
       [ "$(health_conf WARP_KEY_MODE_3XUI)" != plus ] || [ "$(health_conf ACTIVE_KEY_NAME_3XUI)" != "$name" ]; then
        exec {fd}>&-; return 0
    fi
    THREEXUI_LOCK_HELD=1
    PLUS_LAST_REASON=""
    apply_warp_plus_key_3xui >/dev/null 2>&1
    health_keep_plus_3xui plus "$name"
    # Как меню после ключа: подключаемся, если warp-svc разорвал соединение.
    health_cli connect >/dev/null 2>&1
    for i in 1 2 3 4 5; do
        sleep 3
        if [ "$(verify_warp_plus_3xui 2>/dev/null)" = plus ]; then ok=1; break; fi
    done
    unset THREEXUI_LOCK_HELD
    exec {fd}>&-
    if [ "$ok" = 1 ]; then
        HEALTH_PLUS_FREE_3XUI=0; HEALTH_PLUS_BACKOFF_3XUI=0; HEALTH_PLUS_NEXT_3XUI=0
        log_action "HEALTH 3X-UI: WARP+ (${name}) снова подтверждён"
        health_summary reconnect "3X-UI: WARP+ (${name}) снова подтверждён"
        return 0
    fi
    backoff=$((HEALTH_PLUS_BACKOFF_3XUI * 2))
    [ "$backoff" -ge "$HEALTH_PLUS_BACKOFF_MIN" ] || backoff=$HEALTH_PLUS_BACKOFF_MIN
    [ "$backoff" -le "$HEALTH_PLUS_BACKOFF_MAX" ] || backoff=$HEALTH_PLUS_BACKOFF_MAX
    HEALTH_PLUS_BACKOFF_3XUI=$backoff; HEALTH_PLUS_NEXT_3XUI=$((now + backoff))
    local why=""
    case "${PLUS_LAST_REASON:-}" in
        ok) why="ключ принят, но трафик идёт как Free" ;;
        ?*) why=$(plus_reason_text "$PLUS_LAST_REASON" 3X-UI "${PLUS_LAST_QUOTA:-0}"); why=${why%%$'\n'*}; why=${why%.} ;;
    esac
    log_action "HEALTH 3X-UI: WARP+ (${name}) не подтверждается, работает как Free (причина: ${why:-не ясна}); следующая попытка через $((backoff / 3600)) ч"
    health_summary warn "3X-UI: WARP+ (${name}) не подтверждается, работает как Free"
    return 1
}

# Одна проверка контейнера одним docker exec. $2=1 — ещё и проверить трафик.
# Вывод: absent | disabled | <down|up> hs now rx nat selected age trace
# Одна проверка контейнера одним docker exec. $2=1 — ещё и проверить трафик.
# Вывод: absent | disabled | <down|up> hs now rx nat selected age trace kind tools rtt_ms
health_awg_probe() {
    health_docker exec -i "$1" sh -s -- "$2" "$AWG_CLIENTS_AWK" "$(awg_vpn_conf_candidates "$1")" "$AWG_BLOCK_TAG" <<'PROBE' 2>/dev/null
[ -f /opt/warp/warp.conf ] || { echo absent; exit 0; }
[ ! -f /opt/warp/disabled ] || { echo disabled; exit 0; }
now=$(date +%s)
# Возраст контейнера: старт PID 1 в тиках от загрузки ядра против аптайма (ядро у хоста общее).
age=$(awk -v up="$(awk '{print int($1)}' /proc/uptime)" '{sub(/^.*\) /, ""); print int(up - $20 / 100)}' /proc/1/stat 2>/dev/null)
# Выбранные клиенты — адреса по их ключам, как у блока в start.sh.
sel=0; want=""
if [ -s /opt/warp/clients.list ]; then
    for conf in $3; do [ -f "$conf" ] && break; done
    want=$(awk -v list=/opt/warp/clients.list "$2" "$conf" 2>/dev/null | sort)
    sel=$(printf '%s\n' "$want" | grep -c . || true)
fi
tools=0; [ -x /opt/warp-tools/usr/bin/wg ] && [ -x /opt/warp-tools/usr/bin/wg-quick ] && tools=1
# Блок goWARP текущей версии в start.sh на месте (или start.sh нет вовсе — вставлять некуда).
blk=1; [ ! -f /opt/amnezia/start.sh ] || grep -qxF "$4" /opt/amnezia/start.sh || blk=0
if ! ip link show warp >/dev/null 2>&1; then
    echo "down 0 $now 0 0 ${sel:-0} ${age:-9999} 0 - $tools 0 $blk 1"; exit 0
fi
kind=$(sed -n 's/^DEVTYPE=//p' /sys/class/net/warp/uevent 2>/dev/null)
[ -n "$kind" ] || { [ -e /sys/class/net/warp/tun_flags ] && kind=tun || kind=other; }
# Стандартный wg не видит интерфейсы amneziawg, awg — стандартные: читаем тем, кто видит.
w() { env PATH=/opt/warp-tools/usr/bin:$PATH wg "$@" 2>/dev/null || awg "$@" 2>/dev/null; }
hs=$(w show warp latest-handshakes | awk 'NR==1{print $2+0}')
rx=$(w show warp transfer | awk 'NR==1{print $2+0}')
nat=$(iptables -t nat -S POSTROUTING 2>/dev/null | grep -c -- '-o warp -j MASQUERADE' || true)
# Правила ведут ровно выбранных? Новый клиент goAmnezia мог получить адрес удалённого выбранного — без
# перезапуска контейнера старое правило пустило бы его через WARP, хотя счёт правил сходится.
have=$(iptables -t nat -S POSTROUTING 2>/dev/null | sed -n 's/^-A POSTROUTING -s \([^ ]*\) -o warp -j MASQUERADE$/\1/p' | sort)
match=1; [ "$want" = "$have" ] || match=0
trace=0; rtt=0
if [ "$1" = 1 ]; then
    body=$(curl -4 -s --connect-timeout 4 --max-time 6 --interface warp -w '\n%{time_connect}' https://1.1.1.1/cdn-cgi/trace 2>/dev/null)
    case "$body" in *warp=on*|*warp=plus*) trace=1 ;; *) trace=2 ;; esac
    rtt=$(printf '%s\n' "$body" | tail -n 1 | awk '{printf "%d", $1 * 1000}')
fi
echo "up ${hs:-0} $now ${rx:-0} ${nat:-0} ${sel:-0} ${age:-9999} $trace $kind $tools ${rtt:-0} $blk $match"
PROBE
}

health_tick_awg() {
    has_awg_mode || return 0
    command -v docker >/dev/null 2>&1 || return 0
    local c saved="${CONTAINER:-}"
    local -a names=()
    # Без блокировки: зонд только читает. Блокировку берёт лечение, см. health_heal_awg.
    mapfile -t names < <(health_docker ps --format '{{.Names}}' 2>/dev/null | grep -E '^amnezia-awg[0-9]*$' || true)
    for c in "${names[@]}"; do
        [ -z "$c" ] || health_check_awg_container "$c"
    done
    CONTAINER="$saved"
    return 0
}

health_check_awg_container() {
    local c="$1" key="awg:$1" label="AmneziaWG $1" now probe want_trace=0
    local kind hs cnow rx nat sel age trace devtype tools rtt blk match need="" why=""
    now=$(health_now)
    if [ "${HEALTH_PROBE_FAILS[$key]:-0}" -gt 0 ] || [ $((now - ${HEALTH_PROBE_AT[$key]:-0})) -ge "$HEALTH_PROBE_EVERY" ]; then
        want_trace=1
    fi
    probe=$(health_awg_probe "$c" "$want_trace") || probe=""
    read -r kind hs cnow rx nat sel age trace devtype tools rtt blk match <<< "$probe"
    case "$kind" in
        up|down) ;;
        disabled) health_clear "$key"; return 0 ;;
        *) return 0 ;;
    esac
    [ "$want_trace" = 0 ] || HEALTH_PROBE_AT[$key]=$now
    # Контейнер только что стартовал: туннель поднимают start.sh и сторож.
    if [ "${age:-9999}" -lt "$HEALTH_START_GRACE" ]; then return 0; fi
    # Блока goWARP текущей версии нет в start.sh: его переписали снаружи («Поднять заново» в goAmnezia пишет в
    # контейнер свою копию start.sh) или он от версии до 2.5.2 (контейнер был остановлен при обновлении).
    # Без блока WARP после перезапуска контейнера встаёт без правил клиентов.
    [ "${blk:-1}" != 0 ] || health_awg_refresh_block "$c" "$key" "$label" "$now"
    # WARP на модуле amneziawg или userspace-интерфейсе tun (поднят инструментами Amnezia: версией 2.3 или
    # блоком в start.sh, когда стандартных инструментов не было) — на wireguard.
    if [ "$kind" = up ] && awg_kind_needs_rebuild "${devtype:-wireguard}" &&
       health_awg_fix_kind "$c" "$key" "$label" "${tools:-1}" "$now"; then
        return 0
    fi
    [ "$trace" != 1 ] || health_awg_rtt "$key" "$label" "${rtt:-0}" "$now"
    if [ "$kind" = down ]; then
        need=up; why="интерфейс warp пропал"
    elif [ "${hs:-0}" -eq 0 ]; then
        need=restart; why="handshake не проходит"
    elif [ $((cnow - hs)) -gt "$HEALTH_STALE_SEC" ]; then
        need=restart; why="нет handshake $((cnow - hs)) с"
    elif [ "$trace" = 2 ]; then
        local fails=$(( ${HEALTH_PROBE_FAILS[$key]:-0} + 1 ))
        HEALTH_PROBE_FAILS[$key]=$fails
        [ "$fails" -gt 1 ] || HEALTH_RX0[$key]=$rx
        [ "$fails" -ge "$HEALTH_PROBE_FAILS_MAX" ] || return 0
        if [ "${rx:-0}" -gt "${HEALTH_RX0[$key]:-0}" ]; then
            # Байты по туннелю приходят: он жив, сбой дальше по пути. Перезапуск не поможет,
            # поэтому клиентов не дёргаем, а пишем в журнал не чаще раза в час.
            HEALTH_PROBE_FAILS[$key]=0
            local warned="${HEALTH_WARNED[$key]:-}"
            if [ -z "$warned" ] || [ $((now - warned)) -ge 3600 ]; then
                HEALTH_WARNED[$key]=$now
                log_action "HEALTH ${label}: туннель жив, но проверка трафика не проходит; перезапуск не поможет, проверьте путь до Cloudflare"
                health_summary warn "${label}: трафик не проходит проверку"
            fi
            return 0
        fi
        need=restart; why="трафик не идёт, байты не приходят"
    else
        [ "$trace" != 1 ] || HEALTH_PROBE_FAILS[$key]=0
        if [ "${match:-1}" = 0 ] || { [ "${sel:-0}" -gt 0 ] && [ "${nat:-0}" -lt "${sel:-0}" ]; }; then
            need=rules; why="сбились правила клиентов"
        fi
    fi
    if [ -z "$need" ]; then health_mark_ok "$key" "$label"; return 0; fi
    health_due "$key" "$now" || return 0
    health_heal_awg "$c" "$key" "$label" "$need" "$why" "$now"
}

# WARP-интерфейс amneziawg или tun пересобирается на стандартном wireguard; нет стандартных инструментов —
# сначала скачать их. Неудача — пауза от часа, дальше вдвое, до суток; пока ждём, идут обычные проверки.
# rc=0 — действие выполнено (или больше не нужно), rc=1 — ждём паузу или блокировку.
health_awg_fix_kind() {
    local c="$1" key="$2" label="$3" tools="$4" now="$5" fd ok=0 i p k h n dt was what backoff
    [ "$now" -ge "${HEALTH_KIND_NEXT[$key]:-0}" ] || return 1
    exec {fd}>"$WARP_DIR/.awg-operation.lock" || return 1
    if ! flock -n "$fd"; then exec {fd}>&-; return 1; fi
    # Под блокировкой перепроверяем: WARP могли выключить или уже пересобрать.
    p=$(health_awg_probe "$c" 0) || p=""
    read -r k _ _ _ _ _ _ _ dt _ <<< "$p"
    if [ "$k" != up ] || ! awg_kind_needs_rebuild "$dt"; then exec {fd}>&-; return 0; fi
    was=$(awg_kind_text "$dt")
    if ! awg_kernel_wireguard_ok "$c"; then
        exec {fd}>&-
        # Ядро без wireguard: туннель не трогаем, проверяем раз в сутки (модуль могут поставить).
        HEALTH_KIND_NEXT[$key]=$((now + HEALTH_KIND_BACKOFF_MAX))
        log_action "HEALTH ${label}: WARP работает через ${was}; ядро не создаёт интерфейсы wireguard — WARP оставлен как есть, проверка раз в сутки"
        return 0
    fi
    CONTAINER="$c"
    if ! awg_load_container_data >/dev/null 2>&1; then exec {fd}>&-; return 1; fi
    what="пересборка на стандартном WireGuard"
    if [ "$tools" != 1 ] && ! awg_prepare_warp_tools >/dev/null 2>&1; then
        what="скачивание стандартных инструментов WireGuard"
    else
        awg_warp_up restart >/dev/null 2>&1
        awg_load_clients >/dev/null 2>&1 && awg_apply_rules >/dev/null 2>&1
        for i in 1 2 3 4 5 6; do
            sleep 3
            p=$(health_awg_probe "$c" 0) || p=""
            read -r k h n _ _ _ _ _ dt _ <<< "$p"
            if [ "$k" = up ] && [ "$dt" = wireguard ] && [ "${h:-0}" -gt 0 ] && [ $((n - h)) -le 30 ]; then ok=1; break; fi
        done
    fi
    exec {fd}>&-
    if [ "$ok" = 1 ]; then
        unset "HEALTH_KIND_NEXT[$key]" "HEALTH_KIND_BACKOFF[$key]"
        log_action "HEALTH ${label}: ${what}, было: ${was} — помогло"
        health_summary reconnect "${label}: WARP переведён на стандартный WireGuard"
        health_clear "$key"
        return 0
    fi
    backoff=$(( ${HEALTH_KIND_BACKOFF[$key]:-0} * 2 ))
    [ "$backoff" -ge "$HEALTH_KIND_BACKOFF_MIN" ] || backoff=$HEALTH_KIND_BACKOFF_MIN
    [ "$backoff" -le "$HEALTH_KIND_BACKOFF_MAX" ] || backoff=$HEALTH_KIND_BACKOFF_MAX
    HEALTH_KIND_BACKOFF[$key]=$backoff; HEALTH_KIND_NEXT[$key]=$((now + backoff))
    log_action "HEALTH ${label}: WARP работает через ${was}; ${what} не удалась, следующая попытка через $((backoff / 3600)) ч"
    health_summary warn "${label}: WARP через ${was}, ${what} не удалась"
    return 0
}

# Вставить свой блок в start.sh заново; выбор клиентов из адресов (от версий до 2.5.2) — заодно в ключи.
# Под блокировкой перепроверяем: WARP могли удалить или выключить. Неудача — повтор через час.
health_awg_refresh_block() {
    local c="$1" key="$2" label="$3" now="$4" fd p k b ok=0 note=""
    [ "$now" -ge "${HEALTH_BLOCK_NEXT[$key]:-0}" ] || return 0
    exec {fd}>"$WARP_DIR/.awg-operation.lock" || return 0
    if ! flock -n "$fd"; then exec {fd}>&-; return 0; fi
    p=$(health_awg_probe "$c" 0) || p=""
    read -r k _ _ _ _ _ _ _ _ _ _ b _ <<< "$p"
    if { [ "$k" != up ] && [ "$k" != down ]; } || [ "${b:-1}" != 0 ]; then exec {fd}>&-; return 0; fi
    CONTAINER="$c"
    if awg_load_container_data >/dev/null 2>&1 && awg_load_clients >/dev/null 2>&1; then
        if [ "${AWG_CLIENTS_MIGRATED:-0}" = 1 ]; then
            awg_save_clients >/dev/null 2>&1 && note=", выбор клиентов переведён на ключи"
        fi
        awg_patch_start_sh >/dev/null 2>&1 && ok=1
    fi
    exec {fd}>&-
    if [ "$ok" = 1 ]; then
        unset "HEALTH_BLOCK_NEXT[$key]"
        log_action "HEALTH ${label}: блок автозапуска WARP в start.sh пропал или устарел — вставлен заново${note}"
        health_summary info "${label}: блок автозапуска WARP вставлен заново"
    else
        HEALTH_BLOCK_NEXT[$key]=$((now + 3600))
        log_action "HEALTH ${label}: блок автозапуска WARP в start.sh пропал или устарел, вставить не удалось; следующая попытка через 1 ч"
        health_summary warn "${label}: блок автозапуска WARP не восстановлен"
    fi
}

# Задержка через WARP — время TCP-соединения в проверке трафика. Выше порога несколько проверок
# подряд — предупреждение в журнал и шапку меню не чаще раза в час; перезапуск здесь не поможет.
health_awg_rtt() {
    local key="$1" label="$2" rtt="$3" now="$4" warned
    [[ "$rtt" =~ ^[0-9]+$ ]] || return 0
    if [ "$rtt" -lt "$HEALTH_RTT_WARN_MS" ]; then HEALTH_RTT_SLOW[$key]=0; return 0; fi
    HEALTH_RTT_SLOW[$key]=$(( ${HEALTH_RTT_SLOW[$key]:-0} + 1 ))
    [ "${HEALTH_RTT_SLOW[$key]}" -ge "$HEALTH_RTT_STREAK" ] || return 0
    warned="${HEALTH_RTT_WARNED[$key]:-}"
    if [ -z "$warned" ] || [ $((now - warned)) -ge 3600 ]; then
        HEALTH_RTT_WARNED[$key]=$now
        log_action "HEALTH ${label}: задержка через WARP ${rtt} мс — дольше $((HEALTH_RTT_WARN_MS / 1000)) с ${HEALTH_RTT_STREAK} проверки подряд"
        health_summary warn "${label}: задержка через WARP ${rtt} мс"
    fi
}

health_heal_awg() {
    local c="$1" key="$2" need="$4" fd p k rc
    exec {fd}>"$WARP_DIR/.awg-operation.lock" || return 0
    if ! flock -n "$fd"; then exec {fd}>&-; return 0; fi
    # Под блокировкой перепроверяем: пока шла проверка, WARP могли выключить руками
    # (подъём снял бы маркер disabled) или он уже поднялся сам.
    p=$(health_awg_probe "$c" 0) || p=""
    read -r k _ <<< "$p"
    if [ "$k" != up ] && [ "$k" != down ]; then exec {fd}>&-; health_clear "$key"; return 0; fi
    if [ "$need" = up ] && [ "$k" = up ]; then exec {fd}>&-; return 0; fi
    _health_heal_awg_locked "$@"; rc=$?
    exec {fd}>&-
    return "$rc"
}

_health_heal_awg_locked() {
    local c="$1" key="$2" label="$3" need="$4" why="$5" now="$6" level what ok=0 i p k h n
    CONTAINER="$c"
    awg_load_container_data >/dev/null 2>&1 || return 0
    if [ "$need" = rules ]; then
        if awg_load_clients >/dev/null 2>&1 && awg_apply_rules >/dev/null 2>&1; then ok=1; fi
        health_after_heal "$key" "$ok" "$now" "$label" "восстановление правил клиентов, было: ${why}" 1
        return
    fi
    level=${HEALTH_LEVEL[$key]:-1}
    case "$level" in
        1) if [ "$need" = up ]; then what="подъём туннеля"; awg_warp_up ensure >/dev/null 2>&1
           else what="перезапуск туннеля"; awg_warp_up restart >/dev/null 2>&1; fi ;;
        2) what="повторный перезапуск туннеля"; awg_warp_up restart >/dev/null 2>&1 ;;
        *) what="смена порта Cloudflare"
           health_awg_rotate_port "$c" >/dev/null 2>&1
           awg_warp_up restart >/dev/null 2>&1 ;;
    esac
    awg_load_clients >/dev/null 2>&1 && awg_apply_rules >/dev/null 2>&1
    # Считаем успехом только свежий handshake: интерфейс без него трафик не везёт.
    for i in 1 2 3 4 5 6; do
        sleep 3
        p=$(health_awg_probe "$c" 0) || p=""
        read -r k h n _ <<< "$p"
        if [ "$k" = up ] && [ "${h:-0}" -gt 0 ] && [ $((n - h)) -le 30 ]; then ok=1; break; fi
    done
    health_after_heal "$key" "$ok" "$now" "$label" "${what}, было: ${why}" 3
}

health_awg_rotate_port() {
    local c="$1" ep host port next="" p found=0
    ep=$(health_docker exec "$c" sh -c "sed -n '/^Endpoint = /{s/^Endpoint = //p;q}' '$AWG_WARP_CONF'" 2>/dev/null)
    host=${ep%:*}; port=${ep##*:}
    [ -n "$ep" ] && [ "$host" != "$ep" ] || return 1
    for p in $HEALTH_WARP_PORTS; do
        if [ "$found" = 1 ]; then next=$p; break; fi
        [ "$p" != "$port" ] || found=1
    done
    [ -n "$next" ] || next=${HEALTH_WARP_PORTS%% *}
    health_docker exec "$c" sh -c "sed -i 's/^Endpoint = .*/Endpoint = ${host}:${next}/' '$AWG_WARP_CONF'" || return 1
    log_action "HEALTH AmneziaWG ${c}: порт Cloudflare ${port} сменён на ${next}"
}

health_tick() {
    [ -f "$WARP_CONF" ] || return 0
    local cv
    cv=$(awk -F'"' '/^CONFIG_VERSION=/{print $2; exit}' "$WARP_CONF" 2>/dev/null)
    # Конфиг ещё не перенесён интерактивным запуском новой версии — не трогаем.
    [ "$cv" = "$CONFIG_SCHEMA_VERSION" ] || return 0
    # shellcheck disable=SC1090
    source "$WARP_CONF" 2>/dev/null || return 0
    SOCKS_PORT="${SOCKS_PORT:-$DEFAULT_PORT}"
    health_tick_3xui
    health_tick_awg
    health_touch
}

health_daemon() {
    [ "$EUID" -eq 0 ] || { echo "gowarp-health: нужен root" >&2; exit 1; }
    trap 'exit 0' TERM INT
    log_action "HEALTH: служба запущена, проверка каждые ${HEALTH_INTERVAL} с"
    while true; do
        health_tick
        sleep "$HEALTH_INTERVAL" & wait $!
    done
}

# ═══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

case "${1:-}" in
    --bot-daemon) init_config; bot_daemon ;;
    --health-daemon) health_daemon ;;
    --healthcheck) [ "$EUID" -eq 0 ] || exit 1; health_tick; exit 0 ;;
    *) check_root; init_config || exit 1; check_license "${1:-}"; run_startup ;;
esac
