#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — Telegram-бот: установка, привязка админа, снятие (SSH-сторона).
#
# Устройство (по playbook goVLESS «telegram-control-bot-design»):
#   godns-bot.service   — сам бот, БЕЗ root (пользователь godns-bot),
#                         код и venv в /opt/godns/bot;
#   godns-botd.service  — привилегированный демон: UNIX-сокет
#                         /run/godns-botd.sock, JSON-RPC, SO_PEERCRED;
#                         исполняет только закрытый список действий
#                         (godns-bot/actions.sh из каталога программы).
#
# Государственная граница: состояние бота — ТОЛЬКО в /opt/godns/bot и
# /etc/godns/bot.env. Никаких имён govless/gotelegram/x-ui/telemt: у
# соседей свои боты, и на одной машине все трое должны жить не мешая.
#
# Токен: только в /etc/godns/bot.env (0600 root:root). В аргументы команд
# не попадает никогда — /proc/<pid>/cmdline читается любым локальным
# пользователем. systemd передаёт его боту через EnvironmentFile, curl
# получает через --config-файл, python3 — через окружение процесса.
#
# Привязка админа: одноразовый код ADMIN_CLAIM_TOKEN в bot.env. Оператор
# видит код здесь, в SSH-меню, и отправляет боту `/start КОД`. Демон
# сверяет constant-time и ГАСИТ код после первого успеха. Окна «первый
# написавший — админ» нет вообще: у goTelegram такое окно открывалось
# заново при каждом рестарте юнита, и любой, кто знал @username, мог
# перехватить сервер (закрыто маркером только в 2.10.x).

# ── Имена и пути (переопределяемы тестами через ${VAR:-…}) ──────────────
GODNS_BOT_HOME="${GODNS_BOT_HOME:-${GODNS_DIR}/bot}"
GODNS_BOT_APP="${GODNS_BOT_APP:-${GODNS_BOT_HOME}/app}"
GODNS_BOT_VENV="${GODNS_BOT_VENV:-${GODNS_BOT_HOME}/venv}"
GODNS_BOT_STATE_DIR="${GODNS_BOT_STATE_DIR:-${GODNS_BOT_HOME}/state}"
GODNS_BOT_ENV_FILE="${GODNS_BOT_ENV_FILE:-${GODNS_ETC}/bot.env}"
GODNS_BOT_USER="${GODNS_BOT_USER:-godns-bot}"
GODNS_BOT_UNIT="godns-bot"
GODNS_BOTD_UNIT="godns-botd"
GODNS_BOT_UNIT_DIR="${GODNS_BOT_UNIT_DIR:-/etc/systemd/system}"
GODNS_BOTD_SOCK="${GODNS_BOTD_SOCK:-/run/godns-botd.sock}"
GODNS_BOT_SRC="${GODNS_BOT_SRC:-${GODNS_INSTALLER_DIR}/godns-bot}"

# ═══════════════════════════════════════════════════════════════════════
# bot.env — чтение и запись
# ═══════════════════════════════════════════════════════════════════════
# Файл читают четверо: systemd (EnvironmentFile бота), демон, веб-часть и
# это меню. Формат нарочно примитивный: KEY=value, без кавычек. Файл
# НИКОГДА не source-ится: значения могут содержать `$`, а формат чужих
# credentials-файлов уже дважды менялся у соседей — парсим построчно.

godns_bot_env_get() {   # <ключ> [умолчание]  → всегда rc 0
    local key="$1" default="${2:-}" line found=""
    if [ ! -f "$GODNS_BOT_ENV_FILE" ]; then
        printf '%s' "$default"
        return 0
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "#"*|"") continue ;;
            "${key}="*) found="${line#*=}" ;;
        esac
    done < "$GODNS_BOT_ENV_FILE"
    if [ -n "$found" ]; then printf '%s' "$found"; else printf '%s' "$default"; fi
    return 0
}

# Запись через python3 и ОКРУЖЕНИЕ, не через argv: значение (токен!) в
# аргументах видел бы любой локальный пользователь через /proc. Окружение
# процесса читает только владелец, то есть root.
godns_bot_env_set() {   # <ключ> <значение>
    mkdir -p "$(dirname "$GODNS_BOT_ENV_FILE")" 2>/dev/null || true
    GODNS_BOT_SET_FILE="$GODNS_BOT_ENV_FILE" \
    GODNS_BOT_SET_KEY="$1" \
    GODNS_BOT_SET_VALUE="$2" \
    python3 - <<'PY' || return 1
import os, tempfile
path = os.environ["GODNS_BOT_SET_FILE"]
key = os.environ["GODNS_BOT_SET_KEY"]
value = os.environ["GODNS_BOT_SET_VALUE"]
lines = []
try:
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
except FileNotFoundError:
    pass
out, seen = [], False
for line in lines:
    s = line.strip()
    if s and not s.startswith("#") and s.split("=", 1)[0].strip() == key:
        out.append(f"{key}={value}")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={value}")
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".bot.env.")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
    chmod 600 "$GODNS_BOT_ENV_FILE" 2>/dev/null || true
    return 0
}

# Первичное наполнение. Идемпотентно: существующие значения не трогает.
godns_bot_env_init() {
    if [ ! -f "$GODNS_BOT_ENV_FILE" ]; then
        godns_bot_env_set "BOT_TOKEN" "" || return 1
        godns_bot_env_set "ADMIN_IDS" "" || return 1
        godns_bot_env_set "ADMIN_CLAIM_TOKEN" "" || return 1
    fi
    godns_bot_env_set "BOT_LANG" "${LANG_CODE:-ru}" || return 1
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Токен
# ═══════════════════════════════════════════════════════════════════════
godns_bot_token_valid() {   # rc 0 — похоже на токен BotFather
    local tok="${1:-}"
    case "$tok" in
        *[!0-9A-Za-z:_-]*) return 1 ;;
    esac
    printf '%s' "$tok" | grep -Eq '^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'
}

# Проверка токена живым getMe. Токен уходит В ФАЙЛЕ конфигурации curl
# (0600), не в argv — тот же приём, что у install.sh с GH_TOKEN.
# stdout — username бота (без @); rc 1 — не подтвердился.
godns_bot_token_getme() {
    local tok="$1" cfg="" out="" name=""
    command -v curl >/dev/null 2>&1 || return 1
    cfg=$(godns_mktemp) || return 1
    {
        echo 'silent'
        echo 'show-error'
        echo 'connect-timeout = 10'
        echo 'max-time = 20'
        # БЕЗ --fail: он глушит тело и делает 401 неотличимым от обрыва
        # сети. Оператор с опечаткой в токене читал «нет сети до Telegram?»,
        # соглашался «продолжить без проверки» и получал юнит в вечной
        # петле Restart=always, долбящий api.telegram.org каждые пять
        # секунд. Сервер-то ОТВЕТИЛ — и ответ надо показать.
        printf 'url = "https://api.telegram.org/bot%s/getMe"\n' "$tok"
        echo 'write-out = "\nHTTPCODE:%{http_code}"'
    } > "$cfg"
    chmod 600 "$cfg" 2>/dev/null || true
    out=$(curl --config "$cfg" 2>/dev/null) || out=""
    rm -f "$cfg"
    # Код возвращаем вызывающему через глобальную: 401 — токен отвергнут
    # сервером, пусто — до сервера не дошли.
    GODNS_BOT_GETME_CODE=$(printf '%s' "$out" | sed -n 's/.*HTTPCODE:\([0-9]*\).*/\1/p')
    out=$(printf '%s' "$out" | sed 's/HTTPCODE:[0-9]*//')
    name=$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    r = d.get("result") or {}
    if d.get("ok") and r.get("username"):
        sys.stdout.write(str(r["username"]))
except Exception:
    pass
' 2>/dev/null) || name=""
    [ -n "$name" ] || return 1
    printf '%s' "$name"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Код привязки админа
# ═══════════════════════════════════════════════════════════════════════
godns_bot_gen_code() {
    local code=""
    code=$(random_path 10) || code=""
    if [ "${#code}" -lt 8 ]; then return 1; fi
    printf '%s' "$code"
    return 0
}

# Срок жизни кода привязки. Код, выпущенный и забытый, — это вечно
# открытое окно «кто первый напишет, тот и админ», причём сам код к тому
# времени уже осел в истории терминала или на скриншоте. 15 минут.
GODNS_BOT_CLAIM_TTL="${GODNS_BOT_CLAIM_TTL:-900}"

# ЧЧ:ММ из UNIX-времени. GNU date (Ubuntu) понимает -d @N, BSD (macOS,
# где гоняются тесты) — -r N. Не угадываем: пробуем по очереди.
godns_bot_fmt_time() {
    local ts="$1" out=""
    out=$(date -d "@${ts}" '+%H:%M' 2>/dev/null) || out=""
    if [ -z "$out" ]; then
        out=$(date -r "$ts" '+%H:%M' 2>/dev/null) || out=""
    fi
    printf '%s' "${out:-?}"
    return 0
}

# Выпустить и показать новый одноразовый код. Работает на живом боте:
# демон перечитывает bot.env на каждом запросе, рестарт не нужен.
godns_bot_claim_code_show() {
    if [ ! -f "$GODNS_BOT_ENV_FILE" ]; then
        log_warning "$(t bot_not_installed)"
        return 1
    fi
    local code="" name="" expires=0
    code=$(godns_bot_gen_code) || { log_error "$(t bot_code_gen_failed)"; return 1; }
    expires=$(( $(date +%s) + GODNS_BOT_CLAIM_TTL ))
    godns_bot_env_set "ADMIN_CLAIM_TOKEN" "$code" || return 1
    godns_bot_env_set "ADMIN_CLAIM_EXPIRES" "$expires" || return 1
    name=$(godns_bot_env_get "BOT_USERNAME" "") || name=""
    echo ""
    log_success "$(t bot_code_issued)"
    echo -e "      ${BOLD}${WHITE}/start ${code}${NC}"
    if [ -n "$name" ]; then
        echo -e "      ${DIM}https://t.me/${name}${NC}"
    fi
    log_dim "$(tf bot_code_valid_until "$((GODNS_BOT_CLAIM_TTL / 60))" "$(godns_bot_fmt_time "$expires")")"
    log_dim "$(t bot_code_one_time)"
    return 0
}

godns_bot_admins_show() {
    local ids=""
    if ! godns_bot_installed; then
        log_info "$(t bot_not_installed)"
        return 0
    fi
    ids=$(godns_bot_env_get "ADMIN_IDS" "") || ids=""
    if [ -z "$ids" ]; then
        log_warning "$(t bot_admins_none)"
        log_dim "$(t bot_admins_none_hint)"
        return 0
    fi
    log_info "$(tf bot_admins_list "${ids//,/, }")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Состояние
# ═══════════════════════════════════════════════════════════════════════
godns_bot_installed() {
    [ -f "${GODNS_BOT_UNIT_DIR}/${GODNS_BOT_UNIT}.service" ]
}

godns_bot_running() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "$GODNS_BOT_UNIT" 2>/dev/null
}

godns_bot_status() {
    print_header "$(t bot_status_title)"
    if ! godns_bot_installed; then
        log_warning "$(t bot_not_installed)"
        log_dim "$(t bot_install_hint)"
        return 0
    fi

    local a_bot="" a_botd="" name="" ids="" claim=""
    if command -v systemctl >/dev/null 2>&1; then
        a_bot=$(systemctl is-active "$GODNS_BOT_UNIT" 2>/dev/null) || a_bot="?"
        a_botd=$(systemctl is-active "$GODNS_BOTD_UNIT" 2>/dev/null) || a_botd="?"
    fi
    if [ "$a_bot" = "active" ]; then
        kv_row "$(t bot_st_bot)" "${GREEN}${a_bot}${NC}" 14
    else
        kv_row "$(t bot_st_bot)" "${RED}${a_bot:-—}${NC}" 14
    fi
    if [ "$a_botd" = "active" ]; then
        kv_row "$(t bot_st_botd)" "${GREEN}${a_botd}${NC}" 14
    else
        kv_row "$(t bot_st_botd)" "${RED}${a_botd:-—}${NC}" 14
    fi

    if [ -S "$GODNS_BOTD_SOCK" ]; then
        kv_row "$(t bot_st_sock)" "${DIM}${GODNS_BOTD_SOCK}${NC}" 14
    else
        kv_row "$(t bot_st_sock)" "${RED}$(t bot_st_sock_missing)${NC}" 14
    fi

    name=$(godns_bot_env_get "BOT_USERNAME" "") || name=""
    if [ -n "$name" ]; then
        kv_row "$(t bot_st_name)" "${WHITE}@${name}${NC}" 14
    fi

    ids=$(godns_bot_env_get "ADMIN_IDS" "") || ids=""
    if [ -n "$ids" ]; then
        kv_row "$(t bot_st_admins)" "${WHITE}${ids//,/, }${NC}" 14
    else
        kv_row "$(t bot_st_admins)" "${YELLOW}$(t bot_admins_none)${NC}" 14
    fi

    # Код активен, только пока не истёк: показывать «активен» после TTL
    # значило бы врать оператору про открытое окно, которого уже нет.
    claim=$(godns_bot_env_get "ADMIN_CLAIM_TOKEN" "") || claim=""
    local claim_exp; claim_exp=$(godns_bot_env_get "ADMIN_CLAIM_EXPIRES" "0") || claim_exp=0
    case "$claim_exp" in ''|*[!0-9]*) claim_exp=0 ;; esac
    if [ -n "$claim" ] && [ "$claim_exp" -ge "$(date +%s)" ]; then
        kv_row "$(t bot_st_claim)" "${YELLOW}$(t bot_st_claim_open)${NC}" 14
        kv_row "" "${DIM}$(tf bot_code_until_short "$(godns_bot_fmt_time "$claim_exp")")${NC}" 14
    else
        kv_row "$(t bot_st_claim)" "${DIM}$(t bot_st_claim_closed)${NC}" 14
    fi
    return 0
}

godns_bot_logs() {
    command -v journalctl >/dev/null 2>&1 || { log_warning "journalctl?"; return 1; }
    # Без этой проверки экран показывал дважды «-- No entries --» и молчал
    # о причине: журнала нет, потому что нет и юнита.
    if ! godns_bot_installed; then
        log_info "$(t bot_not_installed)"
        return 0
    fi
    print_header "$(t bot_logs_title)"
    journalctl -u "$GODNS_BOT_UNIT" -n 30 --no-pager 2>/dev/null || true
    echo ""
    echo -e "  ${DIM}── ${GODNS_BOTD_UNIT} ──${NC}"
    journalctl -u "$GODNS_BOTD_UNIT" -n 15 --no-pager 2>/dev/null || true
    return 0
}



# ГРАБЛИ, найденные на живом сервере: обновление goDNS кладёт новый код в
# /opt/godns-installer, а бот работает из СВОЕЙ копии в /opt/godns/bot/app.
# Копия сделана намеренно — служба не должна держать ссылок на дерево
# программы, которое подменяется целиком. Обратная сторона: без этой
# функции исправление в боте (хоть бы и в маскировке секретов) до сервера
# НЕ ДОЕЗЖАЕТ. На диске новый код, в памяти старый, и никто не сообщает.
#
# Перезапуск — только когда исходники ДЕЙСТВИТЕЛЬНО изменились: иначе бот
# ронялся бы на секунду при каждом запуске меню, а оператор видел бы
# разрывы на ровном месте.
godns_bot_sync_app() {
    godns_bot_installed || return 0
    local now="" was=""
    now=$(godns_app_fingerprint "$GODNS_BOT_SRC") || return 0
    [ -n "$now" ] || return 0
    was=$(config_get bot_app_fingerprint "") || was=""
    [ "$now" = "$was" ] && return 0

    log_info "$(t bot_app_updating)"
    if ! godns_bot_install_files; then
        log_warning "$(t bot_app_update_failed)"
        return 1
    fi
    config_set bot_app_fingerprint "$now" || true
    godns_bot_restart >/dev/null 2>&1 || true
    log_success "$(t bot_app_updated)"
    return 0
}

godns_bot_restart() {
    command -v systemctl >/dev/null 2>&1 || return 1
    # Не установлен — это не «не удалось перезапустить». Красный крест на
    # машине без бота читался как поломка, и оператор шёл её чинить.
    if ! godns_bot_installed; then
        log_info "$(t bot_not_installed)"
        return 0
    fi
    systemctl restart "$GODNS_BOTD_UNIT" 2>/dev/null || true
    if systemctl restart "$GODNS_BOT_UNIT" 2>/dev/null; then
        log_success "$(t bot_restarted)"
        return 0
    fi
    log_error "$(t bot_restart_failed)"
    return 1
}

# ═══════════════════════════════════════════════════════════════════════
# systemd-юниты
# ═══════════════════════════════════════════════════════════════════════
# Рендер отделён от записи: тесты проверяют содержимое без root.

godns_bot_render_botd_unit() {
    cat <<UNIT
[Unit]
Description=goDNS Pro bot privileged daemon (godns-botd)
Documentation=https://github.com/potap1978/go-all
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${GODNS_BOT_APP}/botd.py
Environment=GODNS_BOTD_SOCK=${GODNS_BOTD_SOCK}
Environment=GODNS_BOT_ENV=${GODNS_BOT_ENV_FILE}
Environment=GODNS_BOT_ACTIONS=${GODNS_BOT_SRC}/actions.sh
Environment=GODNS_BOT_USER=${GODNS_BOT_USER}
Environment=GODNS_BOT_GROUP=${GODNS_BOT_USER}
Restart=always
RestartSec=5
SyslogIdentifier=godns-botd

[Install]
WantedBy=multi-user.target
UNIT
    return 0
}

godns_bot_render_bot_unit() {
    cat <<UNIT
[Unit]
Description=goDNS Pro Telegram bot (godns-bot)
Documentation=https://github.com/potap1978/go-all
After=network-online.target ${GODNS_BOTD_UNIT}.service
Wants=network-online.target

[Service]
Type=simple
User=${GODNS_BOT_USER}
Group=${GODNS_BOT_USER}
WorkingDirectory=${GODNS_BOT_APP}
EnvironmentFile=${GODNS_BOT_ENV_FILE}
Environment=GODNS_BOTD_SOCK=${GODNS_BOTD_SOCK}
Environment=GODNS_BOT_STATE=${GODNS_BOT_STATE_DIR}
ExecStart=${GODNS_BOT_VENV}/bin/python ${GODNS_BOT_APP}/bot.py
Restart=always
RestartSec=5
SyslogIdentifier=godns-bot
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
    return 0
}

godns_bot_write_units() {
    mkdir -p "$GODNS_BOT_UNIT_DIR" 2>/dev/null || true
    godns_bot_render_botd_unit > "${GODNS_BOT_UNIT_DIR}/${GODNS_BOTD_UNIT}.service" || return 1
    godns_bot_render_bot_unit  > "${GODNS_BOT_UNIT_DIR}/${GODNS_BOT_UNIT}.service" || return 1
    [ -s "${GODNS_BOT_UNIT_DIR}/${GODNS_BOTD_UNIT}.service" ] || return 1
    [ -s "${GODNS_BOT_UNIT_DIR}/${GODNS_BOT_UNIT}.service" ] || return 1
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Пользователь, файлы, venv
# ═══════════════════════════════════════════════════════════════════════
godns_bot_ensure_user() {
    if getent passwd "$GODNS_BOT_USER" >/dev/null 2>&1; then
        return 0
    fi
    command -v useradd >/dev/null 2>&1 || return 1
    useradd --system --home-dir "$GODNS_BOT_HOME" --no-create-home \
        --shell /usr/sbin/nologin "$GODNS_BOT_USER" 2>/dev/null || return 1
    return 0
}

# Копирование кода бота. Идемпотентно: повторный запуск обновляет файлы.
# actions.sh НЕ копируется: демон исполняет его прямо из каталога
# программы — фасад обязан быть той же версии, что и lib/, которую сорсит.
godns_bot_install_files() {
    if [ ! -f "${GODNS_BOT_SRC}/bot.py" ]; then
        log_error "$(tf bot_src_missing "$GODNS_BOT_SRC")"
        return 1
    fi
    mkdir -p "$GODNS_BOT_APP" "$GODNS_BOT_STATE_DIR" 2>/dev/null || return 1
    cp "${GODNS_BOT_SRC}/bot.py" "${GODNS_BOT_SRC}/botd.py" \
       "${GODNS_BOT_SRC}/i18n.py" "$GODNS_BOT_APP/" 2>/dev/null || return 1
    cp "${GODNS_BOT_SRC}/requirements.txt" "$GODNS_BOT_APP/" 2>/dev/null || true
    mkdir -p "${GODNS_BOT_APP}/lang" 2>/dev/null || return 1
    cp "${GODNS_BOT_SRC}/lang/"*.json "${GODNS_BOT_APP}/lang/" 2>/dev/null || return 1
    rm -rf "${GODNS_BOT_APP}/__pycache__" 2>/dev/null || true

    # Каталог бота должен быть ПРОХОДИМ для непривилегированного бота:
    # /opt/godns закрыт (0700 root) — и это правильно, но подкаталог bot/
    # приоткрываем через group execute, не трогая остального.
    if getent passwd "$GODNS_BOT_USER" >/dev/null 2>&1; then
        chgrp -R "$GODNS_BOT_USER" "$GODNS_BOT_HOME" 2>/dev/null || true
        chmod 750 "$GODNS_BOT_HOME" "$GODNS_BOT_APP" 2>/dev/null || true
        chmod 750 "${GODNS_BOT_APP}/lang" 2>/dev/null || true
        chmod 640 "$GODNS_BOT_APP"/*.py "${GODNS_BOT_APP}/lang/"*.json 2>/dev/null || true
        chown -R "${GODNS_BOT_USER}:${GODNS_BOT_USER}" "$GODNS_BOT_STATE_DIR" 2>/dev/null || true
        chmod 700 "$GODNS_BOT_STATE_DIR" 2>/dev/null || true
        # Сам /opt/godns: пользователю бота нужен только ПРОХОД к bot/,
        # не чтение каталога. o+x не даём; группу ставим точечно.
        chgrp "$GODNS_BOT_USER" "$GODNS_DIR" 2>/dev/null || true
        chmod g+x "$GODNS_DIR" 2>/dev/null || true
    fi
    return 0
}

godns_bot_setup_venv() {
    if [ -x "${GODNS_BOT_VENV}/bin/python" ]; then
        return 0
    fi
    if ! run_with_progress "$(t bot_venv_creating)" \
            python3 -m venv "$GODNS_BOT_VENV"; then
        # На minimal-образах нет ensurepip: доставляем python3-venv и
        # пробуем ещё раз. apt ждём честно, как всюду в goDNS.
        apt_wait_ready || return 1
        run_with_progress "$(t deps_updating)" apt_q update || true
        run_with_progress "$(tf deps_installing "python3-venv")" \
            apt_q install --no-install-recommends python3-venv || return 1
        rm -rf "$GODNS_BOT_VENV" 2>/dev/null || true
        run_with_progress "$(t bot_venv_creating)" \
            python3 -m venv "$GODNS_BOT_VENV" || return 1
    fi
    return 0
}

godns_bot_pip_install() {
    [ -x "${GODNS_BOT_VENV}/bin/pip" ] || return 1
    run_with_progress "$(t bot_pip_installing)" \
        "${GODNS_BOT_VENV}/bin/pip" install --disable-pip-version-check \
        --no-input -q -r "${GODNS_BOT_APP}/requirements.txt" || return 1
    # Группе — проход и чтение: pip кладёт всё под root по umask 077.
    chgrp -R "$GODNS_BOT_USER" "$GODNS_BOT_VENV" 2>/dev/null || true
    chmod -R g+rX "$GODNS_BOT_VENV" 2>/dev/null || true
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Мастер установки
# ═══════════════════════════════════════════════════════════════════════
godns_bot_setup_wizard() {
    print_header "$(t bot_wizard_title)"
    log_dim "$(t bot_wizard_intro)"
    log_dim "$(t bot_wizard_intro2)"
    echo ""

    # ── Шаг 1: токен ────────────────────────────────────────────────────
    local tok="" name="" have_tok=""
    have_tok=$(godns_bot_env_get "BOT_TOKEN" "") || have_tok=""
    if [ -n "$have_tok" ] && godns_bot_token_valid "$have_tok"; then
        if confirm "$(t bot_token_reuse_ask)" y; then
            tok="$have_tok"
        fi
    fi
    while [ -z "$tok" ]; do
        echo -e "  ${DIM}$(t bot_token_hint)${NC}"
        echo -ne "  $(t bot_token_prompt) " >&2
        local ans=""
        read -r ans || { echo ""; log_info "$(t startup_cancelled)"; return 1; }
        ans=$(printf '%s' "$ans" | tr -d '[:space:]')
        [ -n "$ans" ] || continue
        if ! godns_bot_token_valid "$ans"; then
            log_warning "$(t bot_token_bad_format)"
            continue
        fi
        tok="$ans"
    done

    name=$(godns_bot_token_getme "$tok") || name=""
    if [ -n "$name" ]; then
        log_success "$(tf bot_token_ok "$name")"
    elif [ "${GODNS_BOT_GETME_CODE:-}" = "401" ] \
         || [ "${GODNS_BOT_GETME_CODE:-}" = "404" ]; then
        # Сервер ОТВЕТИЛ и токен отверг. Продолжать бессмысленно: юнит
        # уйдёт в вечную петлю Restart=always, долбя api.telegram.org
        # каждые пять секунд, а в меню это выглядит просто как «stopped».
        log_error "$(t bot_token_rejected)"
        log_dim "$(t bot_token_rejected_hint)"
        return 1
    else
        # До api.telegram.org не дошли вовсе — это не обязательно опечатка
        # в токене, сервер может быть отрезан от Telegram. Решает оператор.
        log_warning "$(t bot_token_unverified)"
        if ! confirm "$(t bot_token_continue_ask)" n; then
            log_info "$(t startup_cancelled)"
            return 1
        fi
    fi

    godns_bot_env_init || { log_error "$(t bot_env_write_failed)"; return 1; }
    godns_bot_env_set "BOT_TOKEN" "$tok" || { log_error "$(t bot_env_write_failed)"; return 1; }
    if [ -n "$name" ]; then
        godns_bot_env_set "BOT_USERNAME" "$name" || true
    fi

    # ── Шаг 2: пользователь, файлы, окружение ───────────────────────────
    if ! godns_bot_ensure_user; then
        log_error "$(tf bot_user_failed "$GODNS_BOT_USER")"
        return 1
    fi
    godns_bot_install_files || return 1
    godns_bot_setup_venv || { log_error "$(t bot_venv_failed)"; return 1; }
    godns_bot_pip_install || { log_error "$(t bot_pip_failed)"; return 1; }

    # ── Шаг 3: юниты и запуск ───────────────────────────────────────────
    command -v systemctl >/dev/null 2>&1 || { log_error "$(t bot_no_systemd)"; return 1; }
    godns_bot_write_units || { log_error "$(t bot_units_failed)"; return 1; }
    systemctl enable --now "$GODNS_BOTD_UNIT" >/dev/null 2>&1 || true
    systemctl enable "$GODNS_BOT_UNIT" >/dev/null 2>&1 || true
    if ! systemctl restart "$GODNS_BOT_UNIT" 2>/dev/null; then
        log_error "$(t bot_start_failed)"
        log_dim "journalctl -u ${GODNS_BOT_UNIT} -n 30"
        return 1
    fi
    log_success "$(t bot_started)"

    # ── Шаг 4: привязка первого админа ──────────────────────────────────
    echo ""
    log_info "$(t bot_claim_explain)"
    godns_bot_claim_code_show || return 1
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# Снятие
# ═══════════════════════════════════════════════════════════════════════
# Обязано убирать юниты И разоружать их: оставленный failed-юнит копится
# у systemd и пугает при каждом `systemctl --failed` (этими граблями уже
# отличился таймер копий при удалении goDNS).
godns_bot_remove() {
    if ! godns_bot_installed && [ ! -d "$GODNS_BOT_HOME" ]; then
        log_info "$(t bot_not_installed)"
        return 0
    fi
    # ГРАБЛИ: этот вопрос задавался и внутри ПОЛНОГО удаления goDNS, где
    # оператор уже подтвердил снос словом. Умолчание «нет» означало, что
    # нажатие Enter оставляло юниты и пользователя бота, а следующей
    # строкой `rm -rf /opt/godns /etc/godns` сносил их код и окружение —
    # получался юнит, падающий с 217/USER навсегда. Внутри полного
    # удаления не спрашиваем: решение уже принято.
    if [ "${1:-}" != "--no-ask" ] && ! confirm "$(t bot_remove_ask)" n; then
        log_info "$(t startup_cancelled)"
        return 0
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$GODNS_BOT_UNIT" >/dev/null 2>&1 || true
        systemctl disable --now "$GODNS_BOTD_UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "${GODNS_BOT_UNIT_DIR}/${GODNS_BOT_UNIT}.service" \
          "${GODNS_BOT_UNIT_DIR}/${GODNS_BOTD_UNIT}.service" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl reset-failed "$GODNS_BOT_UNIT" "$GODNS_BOTD_UNIT" >/dev/null 2>&1 || true
    fi

    rm -rf "$GODNS_BOT_HOME" 2>/dev/null || true
    rm -f "$GODNS_BOTD_SOCK" 2>/dev/null || true

    # Токен — отдельным вопросом: без bot.env повторная установка попросит
    # токен заново, зато на сервере не останется секретов бота.
    if [ -f "$GODNS_BOT_ENV_FILE" ]; then
        if confirm "$(t bot_remove_env_ask)" y; then
            rm -f "$GODNS_BOT_ENV_FILE" 2>/dev/null || true
        else
            log_dim "$(tf bot_env_kept "$GODNS_BOT_ENV_FILE")"
        fi
    fi

    if getent passwd "$GODNS_BOT_USER" >/dev/null 2>&1 \
        && command -v userdel >/dev/null 2>&1; then
        userdel "$GODNS_BOT_USER" >/dev/null 2>&1 || true
    fi

    log_success "$(t bot_removed)"
    return 0
}

