#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — веб-панель (godns-web): установка, снятие, статус, адрес доступа
# и НЕинтерактивные действия для моста godns-web/bridge.sh.
#
# ── Устройство ──────────────────────────────────────────────────────────
# Панель — один python3-процесс (godns-web/server.py) с двумя слушателями:
#   • публичный: 0.0.0.0:<webui_port>, TLS тем же сертификатом Let's
#     Encrypt, что у dnsdist; секретный путь <webui_path>, всё прочее — 404;
#   • локальный: 127.0.0.1:<webui_local_port> без TLS — аварийный вход по
#     SSH-туннелю с токеном из ${GODNS_ETC}/webui/token.
# nginx НЕ используется: conf.d — общий с соседями каталог, а на чистом
# goDNS-сервере nginx и не установлен. Свой порт, свой сокет, имя юнита
# строго с префиксом godns-.
#
# Функции webui_set_* / webui_rotate_path / webui_service_restart — то же,
# что делают пункты SSH-меню, но без confirm(): подтверждение вводом слова
# уже спрошено в браузере, а bash-confirm при закрытом stdin молча брал бы
# умолчание — то есть решал бы за оператора.
#
# Требует: lib/common.sh (config_*, port_*, random_path, log_*),
# lib/certs.sh (grant/renewal), lib/engine.sh (+engines), lib/providers.sh.

# ── Пути. Всё через ${VAR:-…}: голое присваивание затирало бы значения,
# выставленные тестами, и проверка молча читала бы настоящий /etc ─────────
GODNS_WEBUI_DIR="${GODNS_WEBUI_DIR:-${GODNS_DIR:-/opt/godns}/web}"
GODNS_WEBUI_TOKEN_DIR="${GODNS_WEBUI_TOKEN_DIR:-${GODNS_ETC:-/etc/godns}/webui}"
GODNS_WEBUI_TOKEN_FILE="${GODNS_WEBUI_TOKEN_FILE:-${GODNS_WEBUI_TOKEN_DIR}/token}"
GODNS_WEBUI_UNIT="${GODNS_WEBUI_UNIT:-godns-web}"
GODNS_WEBUI_UNIT_DIR="${GODNS_WEBUI_UNIT_DIR:-/etc/systemd/system}"
# Исходники панели лежат рядом с lib/ в дереве программы.
GODNS_WEBUI_SRC="${GODNS_WEBUI_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/godns-web}"
# База 3X-UI читается ТОЛЬКО на чтение — чтобы не занять её случайные порты.
XUI_DB_PATH="${XUI_DB_PATH:-/etc/x-ui/x-ui.db}"

# ── Состояние ───────────────────────────────────────────────────────────
webui_installed() {
    [ -f "${GODNS_WEBUI_UNIT_DIR}/${GODNS_WEBUI_UNIT}.service" ]
}

webui_running() {
    systemctl is-active --quiet "$GODNS_WEBUI_UNIT" 2>/dev/null
}

webui_url() {
    local dom port path
    dom=$(config_get domain "") || dom=""
    port=$(config_get webui_port "") || port=""
    path=$(config_get webui_path "") || path=""
    [ -n "$dom" ] && [ -n "$port" ] && [ -n "$path" ] || { printf ''; return 1; }
    printf 'https://%s:%s/%s/' "$dom" "$port" "${path#/}"
    return 0
}

# ── Выбор портов ────────────────────────────────────────────────────────
# «Порт числится за кем-то по построению» живёт теперь в lib/common.sh
# (godns_port_reserved) — её же использует мастер. Раньше она была только
# здесь, и мастер брал порты соседа, пока тот не запущен.
_webui_port_reserved() { godns_port_reserved "$@"; }

webui_pick_port() {
    local cand out=""
    for cand in 9445 9446 9447 8446 8447 9448 9449; do
        _webui_port_reserved "$cand" && continue
        port_in_use "$cand" && continue
        out="$cand"
        break
    done
    [ -n "$out" ] || return 1
    printf '%s' "$out"
    return 0
}

webui_pick_local_port() {
    local cand out=""
    for cand in 8642 8643 8644 8645 8646; do
        _webui_port_reserved "$cand" && continue
        port_in_use "$cand" && continue
        out="$cand"
        break
    done
    [ -n "$out" ] || return 1
    printf '%s' "$out"
    return 0
}

# ── ufw: свой порт открываем сами (функции godns.sh мосту недоступны) ────


# ── Токен аварийного входа ──────────────────────────────────────────────
# Файл 0600 в каталоге 0700. Пишет его установка; server.py умеет создать
# сам, но тогда токен появился бы только после первого старта — а показать
# его оператору надо сразу.
webui_ensure_token() {
    if [ -s "$GODNS_WEBUI_TOKEN_FILE" ]; then
        return 0
    fi
    mkdir -p "$GODNS_WEBUI_TOKEN_DIR" 2>/dev/null || return 1
    chmod 700 "$GODNS_WEBUI_TOKEN_DIR" 2>/dev/null || true
    # random_path даёт [A-Za-z0-9] — безопасно и для URL, и для заголовка.
    local tok=""
    tok=$(random_path 43) || tok=""
    [ -n "$tok" ] || return 1
    ( umask 077; printf '%s\n' "$tok" > "$GODNS_WEBUI_TOKEN_FILE" ) || return 1
    chmod 600 "$GODNS_WEBUI_TOKEN_FILE" 2>/dev/null || true
    return 0
}

# ── systemd-юнит ────────────────────────────────────────────────────────
_webui_write_unit() {
    local prog_dir="$1" unit="${GODNS_WEBUI_UNIT_DIR}/${GODNS_WEBUI_UNIT}.service"
    mkdir -p "$GODNS_WEBUI_UNIT_DIR" 2>/dev/null || return 1
    cat > "$unit" <<UNIT || return 1
[Unit]
Description=goDNS Pro web panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${GODNS_WEBUI_DIR}/server.py
Environment=GODNS_PROGRAM_DIR=${prog_dir}
Environment=GODNS_CONFIG=${GODNS_CONFIG}
Environment=GODNS_ETC=${GODNS_ETC:-/etc/godns}
Restart=on-failure
RestartSec=3
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
    [ -s "$unit" ] || return 1
    return 0
}

# ── Установка ───────────────────────────────────────────────────────────
# Неинтерактивное ядро: порты и путь берутся из config.json, если уже
# заданы (переустановка не меняет адрес), иначе выбираются заново.
webui_install() {
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "$(t webui_need_python)"
        return 1
    fi
    if [ ! -f "${GODNS_WEBUI_SRC}/server.py" ]; then
        log_error "$(tf webui_src_missing "$GODNS_WEBUI_SRC")"
        return 1
    fi

    local pub local_p path
    pub=$(config_get webui_port "") || pub=""
    local_p=$(config_get webui_local_port "") || local_p=""
    path=$(config_get webui_path "") || path=""
    if [ -z "$pub" ]; then
        pub=$(webui_pick_port) || pub=""
        if [ -z "$pub" ]; then
            log_error "$(t webui_no_free_port)"
            return 1
        fi
    fi
    if [ -z "$local_p" ]; then
        local_p=$(webui_pick_local_port) || local_p=""
        if [ -z "$local_p" ]; then
            log_error "$(t webui_no_free_port)"
            return 1
        fi
    fi
    if [ -z "$path" ]; then
        path=$(random_path 16) || path=""
        if [ -z "$path" ]; then
            log_error "$(t webui_path_failed)"
            return 1
        fi
    fi

    # Файлы панели — копией из дерева программы в СВОЙ каталог состояния.
    # Копия, а не симлинк: дерево программы подменяется целиком при
    # обновлении, и работающая служба не должна держать за него ссылок.
    mkdir -p "$GODNS_WEBUI_DIR" 2>/dev/null || { log_error "$(tf webui_copy_failed "$GODNS_WEBUI_DIR")"; return 1; }
    if ! cp -R "${GODNS_WEBUI_SRC}/." "$GODNS_WEBUI_DIR/"; then
        log_error "$(tf webui_copy_failed "$GODNS_WEBUI_DIR")"
        return 1
    fi
    chmod 755 "$GODNS_WEBUI_DIR" 2>/dev/null || true

    webui_ensure_token || { log_error "$(t webui_token_failed)"; return 1; }

    config_set webui_port "$pub" || return 1
    config_set webui_local_port "$local_p" || return 1
    config_set webui_path "${path#/}" || return 1
    config_set webui_enabled "yes" || return 1

    local prog_dir
    prog_dir=$(cd "${GODNS_WEBUI_SRC}/.." 2>/dev/null && pwd) || prog_dir="${GODNS_INSTALLER_DIR:-/opt/godns-installer}"
    _webui_write_unit "$prog_dir" || { log_error "$(t webui_unit_failed)"; return 1; }

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        if ! systemctl enable --now "$GODNS_WEBUI_UNIT" >/dev/null 2>&1; then
            log_error "$(t webui_start_failed)"
            return 1
        fi
        systemctl restart "$GODNS_WEBUI_UNIT" >/dev/null 2>&1 || true
    fi
    godns_fw_open "$pub"

    # Сертификата может не быть (домен ещё не выбран): панель тогда живёт
    # только на localhost — это честный аварийный режим, а не поломка.
    local cert=""
    cert=$(config_get cert_path "") || cert=""
    if [ -z "$cert" ] && [ -z "$(config_get domain "")" ]; then
        log_warning "$(t webui_no_cert_warn)"
    fi

    log_success "$(t webui_installed_ok)"
    return 0
}

# ── Снятие ──────────────────────────────────────────────────────────────
# Разоружаем ВСЁ своё: службу, юнит, файлы, правило ufw. Токен и адрес
# (порт/путь в config.json) остаются: повторная установка вернёт панель
# по тому же адресу, ничего не раздавая заново.
webui_remove() {
    local pub
    pub=$(config_get webui_port "") || pub=""
    # Отчитываться о снятии того, чего не было, нельзя: при полном удалении
    # goDNS на машине без панели оператор читал «✓ Веб-панель снята» и
    # думал, что она у него стояла. У бота такая проверка есть, у панели
    # не было. Уборку всё равно доводим до конца — остатки могли пережить
    # прерванную установку.
    local was="no"
    if [ -d "$GODNS_WEBUI_DIR" ] \
       || [ -f "${GODNS_WEBUI_UNIT_DIR}/${GODNS_WEBUI_UNIT}.service" ]; then
        was="yes"
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "$GODNS_WEBUI_UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "${GODNS_WEBUI_UNIT_DIR}/${GODNS_WEBUI_UNIT}.service" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    rm -rf "$GODNS_WEBUI_DIR" 2>/dev/null || true
    godns_fw_close "$pub"
    config_set webui_enabled "no" || true
    if [ "$was" = "yes" ]; then
        log_success "$(t webui_removed)"
    else
        log_info "$(t webui_not_installed)"
    fi
    return 0
}

# То же для панели: server.py и статика живут копией в своём каталоге,
# и без синхронизации обновление goDNS оставляет работать старую панель.
webui_sync_app() {
    webui_installed || return 0
    local now="" was=""
    now=$(godns_app_fingerprint "$GODNS_WEBUI_SRC") || return 0
    [ -n "$now" ] || return 0
    was=$(config_get webui_app_fingerprint "") || was=""
    [ "$now" = "$was" ] && return 0

    log_info "$(t webui_app_updating)"
    if ! cp -R "${GODNS_WEBUI_SRC}/." "$GODNS_WEBUI_DIR/" 2>/dev/null; then
        log_warning "$(t webui_app_update_failed)"
        return 1
    fi
    config_set webui_app_fingerprint "$now" || true
    systemctl restart "$GODNS_WEBUI_UNIT" >/dev/null 2>&1 || true
    log_success "$(t webui_app_updated)"
    return 0
}

webui_restart() {
    if ! webui_installed; then
        log_warning "$(t webui_not_installed)"
        return 1
    fi
    if systemctl restart "$GODNS_WEBUI_UNIT" >/dev/null 2>&1; then
        log_success "$(t webui_restarted)"
        return 0
    fi
    log_error "$(t webui_restart_failed)"
    return 1
}

# Новый секретный путь САМОЙ ПАНЕЛИ (не DoH): мини-апп и закладки
# отваливаются, поэтому из меню это отдельное осознанное действие.
webui_rotate_panel_path() {
    local p=""
    p=$(random_path 16) || p=""
    if [ -z "$p" ]; then
        log_error "$(t webui_path_failed)"
        return 1
    fi
    config_set webui_path "$p" || return 1
    if webui_installed; then
        systemctl restart "$GODNS_WEBUI_UNIT" >/dev/null 2>&1 || true
    fi
    log_success "$(tf webui_panel_newpath_ok "$(webui_url)")" || true
    return 0
}

# ── Показ доступа ───────────────────────────────────────────────────────
# Токен печатается ТОЛЬКО здесь — в защищённой SSH-сессии оператора.
# В журнал службы и в браузер он не попадает никогда.
webui_show_access() {
    print_header "$(t webui_access_title)"
    local url tok local_p
    url=$(webui_url) || url=""
    local_p=$(config_get webui_local_port "") || local_p=""
    if [ -n "$url" ]; then
        kv_row "$(t webui_access_url)" "${WHITE}${url}${NC}" 16
    else
        kv_row "$(t webui_access_url)" "${DIM}$(t webui_access_url_none)${NC}" 16
    fi
    tok=""
    [ -r "$GODNS_WEBUI_TOKEN_FILE" ] && tok=$(cat "$GODNS_WEBUI_TOKEN_FILE" 2>/dev/null)
    if [ -n "$tok" ] && [ -n "$local_p" ]; then
        kv_row "$(t webui_access_tunnel)" "${DIM}ssh -L ${local_p}:127.0.0.1:${local_p} root@$(get_server_ip)${NC}" 16
        kv_row "$(t webui_access_local)" "${WHITE}http://127.0.0.1:${local_p}/?token=${tok}${NC}" 16
    fi
    echo ""
    log_dim "$(t webui_access_code_hint)"
    log_dim "$(t webui_access_miniapp_hint)"
    return 0
}

webui_status_line() {
    if ! webui_installed; then
        printf '%s' "$(t webui_state_not_installed)"
        return 0
    fi
    if webui_running; then
        printf '%s' "$(t webui_state_on)"
    else
        printf '%s' "$(t webui_state_off)"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# НЕИНТЕРАКТИВНЫЕ ДЕЙСТВИЯ ДЛЯ МОСТА (и повторного применения)
# ═══════════════════════════════════════════════════════════════════════

# Хвост reconfigure() из godns.sh: права на ключ, реакция на перевыпуск,
# применение, файрвол, штамп. Функции godns.sh мосту недоступны (main там
# исполняется при source), поэтому логика повторена здесь — и ровно
# поэтому она обязана оставаться маленькой.
# Закрыть порты, с которых ушли. Только ПОСЛЕ открытия новых.
webui_apply() {
    if ! engine_is_installed; then
        log_dim "$(t reconf_not_installed)"
        return 0
    fi
    local dom rc=0
    dom=$(config_get domain "") || dom=""
    if [ -n "$dom" ]; then
        certs_grant_read_access "$dom" "$(engine_service_user)" || rc=$?
        if [ "$rc" -eq 1 ]; then
            log_error "$(t deploy_cert_access_failed)"
            log_warning "$(t reconf_saved_not_applied)"
            return 1
        fi
        certs_setup_renewal_reaction "$dom" "$(config_get cert_path "")" \
            || log_warning "$(t deploy_hook_failed)"
    fi
    if engine_apply; then
        # Общая логика — та же, что у SSH и бота (см. godns_fw_apply_ports).
        godns_fw_apply_ports || true
        godns_mark_applied
        log_success "$(t reconf_applied)"
        return 0
    fi
    log_error "$(t reconf_apply_failed)"
    log_warning "$(t reconf_saved_not_applied)"
    return 1
}

# Порт занят, но НАШИМ же dnsdist — это не «занят»: при повторном
# применении порты держит наша работающая служба.
_webui_port_is_ours() {
    local owner="$1" main_pid=""
    [ -n "$owner" ] || return 1
    main_pid=$(systemctl show -p MainPID --value dnsdist 2>/dev/null) || main_pid=""
    [ -n "$main_pid" ] && [ "$main_pid" != "0" ] || return 1
    case "$owner" in *"pid=${main_pid}"*) return 0 ;; esac
    return 1
}

webui_set_ports() {
    local doh="$1" dot="$2" owner=""
    if ! valid_port "$doh" || ! valid_port "$dot"; then
        log_error "$(t webui_bad_port)"
        return 1
    fi
    if [ "$doh" = "$dot" ]; then
        log_error "$(t webui_ports_equal)"
        return 1
    fi
    # Порт DoT проверяем ТОЛЬКО когда DoT включён. ГРАБЛИ: панель
    # отказывалась сменить порт DoH, потому что занят порт ВЫКЛЮЧЕННОГО
    # DoT — то есть порт, на который мы всё равно не сядем. Оператор
    # читал «Порт 853 занят» и не понимал, при чём тут его 443.
    local p checks=("$doh")
    if [ "$(config_get dot_enabled "no")" = "yes" ]; then
        checks+=("$dot")
    fi
    for p in "${checks[@]}"; do
        if port_in_use "$p"; then
            owner=$(port_owner "$p") || owner=""
            if ! _webui_port_is_ours "$owner"; then
                log_error "$(tf ports_busy "$p" "$owner")"
                return 1
            fi
        fi
    done
    # Прежние порты запоминаются, чтобы webui_apply закрыл их в ufw…
    # закрытие старых делает godns.sh при следующем полном применении;
    # здесь только открытие новых — лучше лишнее открытое, чем отрезанный
    # оператор.
    # Старые порты — в config ДО записи новых: закрыть их в ufw надо
    # ПОСЛЕ успешного применения (закроем первым и споткнёмся — оператор
    # останется без доступа). Ровно та же схема, что в SSH-пути; без неё
    # смена порта из панели оставляла прежний открытым в экране.
    config_set doh_port_prev "$(config_get doh_port "")" || true
    config_set dot_port_prev "$(config_get dot_port "")" || true
    config_set doh_port "$doh" || return 1
    config_set dot_port "$dot" || return 1
    webui_apply
}

webui_set_dot() {
    local want="$1" port
    case "$want" in yes|no) : ;; *) log_error "$(t webui_bad_arg)"; return 1 ;; esac
    if [ "$want" = "yes" ]; then
        port=$(config_get dot_port "853") || port="853"
        if port_in_use "$port"; then
            local owner=""
            owner=$(port_owner "$port") || owner=""
            if ! _webui_port_is_ours "$owner"; then
                log_error "$(tf ports_busy "$port" "$owner")"
                return 1
            fi
        fi
        config_set dot_enabled "yes" || return 1
        config_set dot_port "$port" || return 1
        log_success "$(tf ports_dot_on "$port")"
    else
        config_set dot_enabled "no" || return 1
        log_success "$(t ports_dot_off)"
        log_dim "$(t ports_dot_android_lost)"
    fi
    webui_apply
}

# Смена секретного пути DoH: сначала применяем, потом объявляем успех —
# на провале откатываем путь (та же дисциплина, что rotate_path в godns.sh).
webui_rotate_path() {
    local p old
    old=$(config_get doh_path "") || old=""
    p=$(random_path 12) || p=""
    if [ -z "$p" ]; then
        log_error "$(t webui_path_failed)"
        return 1
    fi
    config_set doh_path "${p#/}" || return 1
    if engine_is_installed; then
        if ! engine_apply; then
            config_set doh_path "$old"
            log_error "$(t reconf_apply_failed)"
            log_warning "$(t ports_newpath_rolled_back)"
            return 1
        fi
        godns_mark_applied
    fi
    log_success "$(tf ports_newpath_ok "$p")"
    return 0
}

webui_set_upstreams() {
    local ids="$1" policy="$2" id
    case "$policy" in roundrobin|fastest) : ;; *) log_error "$(t webui_bad_policy)"; return 1 ;; esac
    [ -n "$ids" ] || { log_error "$(t webui_no_upstreams)"; return 1; }
    # ГРАБЛИ: printf '%s' без завершающего \n — последний id приходит в
    # read с кодом 1, цикл выходит, НЕ проверив его. Именно так «cloudflare,
    # evil» проскакивал бы валидацию. printf '%s\n' закрывает дыру.
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        if ! prov_exists "$id"; then
            log_error "$(tf webui_unknown_upstream "$id")"
            return 1
        fi
    done < <(printf '%s\n' "$ids" | tr ',' '\n')
    # Через общую сборку: панель раньше писала ТОЛЬКО upstream_ids, движок
    # рендерил из старых канонических записей, и смена набора из панели не
    # доезжала до dnsdist вовсе — при бодром «апстримы применены».
    local skipped=""
    skipped=$(engine_upstreams_store "$ids") || {
        log_error "$(t prov_none_usable)"
        return 1
    }
    [ -n "$skipped" ] && log_warning "$(tf prov_skipped "$skipped")"
    config_set policy "$policy" || return 1
    webui_apply
}

webui_service_restart() {
    engine_apply || return 1
    godns_mark_applied
    return 0
}

webui_cert_rehook() {
    local d
    d=$(config_get domain "") || d=""
    if [ -z "$d" ]; then
        log_warning "$(t cert_none_configured)"
        return 1
    fi
    if certs_setup_renewal_reaction "$d" "$(config_get cert_path "")"; then
        log_success "$(t cert_hook_ok)"
        return 0
    fi
    return 1
}

# Проверка снаружи БЕЗ bash-confirm: согласие «host:port уйдёт третьей
# стороне» уже дано вводом слова в браузере. Тело — selftest_external_check
# из lib/selftest.sh минус вопрос.
webui_external_check() {
    local rc=0
    print_header "$(t test_title_external)"
    if ! selftest_prepare; then
        return 1
    fi
    echo -e "  ${DIM}$(t test_ext_discloses)${NC}"
    echo -e "  ${DIM}$(t test_ext_no_secret)${NC}"
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

# ═══════════════════════════════════════════════════════════════════════
# СВОИ АПСТРИМЫ — неинтерактивно, для моста панели и фасада бота
# ═══════════════════════════════════════════════════════════════════════
# Порядок шагов — тот же, что у SSH-экрана custom_upstream_add в godns.sh
# (он эталон): протокол → адрес → имя в IP → цена открытого :53 → живая
# проба → запись → включение в набор. Здесь он закреплён один раз, а не
# повторён в двух интерфейсах: панель и бот зовут ровно эти функции.
#
# Путь DoH приходит обычным позиционным параметром: аргументы bash-функции
# в /proc не видны. На границе процесса (bridge.sh, actions.sh) он ездит
# переменной окружения GODNS_CUSTOM_PATH, а не argv: /proc/<pid>/cmdline
# читает любой локальный пользователь, а у чужого резолвера путь может
# быть чужим секретом (второй goDNS как апстрим).
#
# В stdout — только машинный TSV «ключ<TAB>значение»; всё для человека —
# log_* в stderr. Сервер панели вынимает TSV-строки, остальное показывает.

# _webui_custom_resolve <proto> <host> <port>
#   stdout: ip<TAB>sni<TAB>port. rc 1 — отказ, причина уже напечатана.
#   IP берётся как есть; имя → valid_domain → godns_resolve_host, первый
#   адрес. Для dot/doh SNI обязан быть именем: по IP сертификат не
#   проверить, а проверка у своих включена всегда. Порт пустой →
#   853/443/53 по протоколу.
_webui_custom_resolve() {
    local proto="${1:-}" host="${2:-}" port="${3:-}" ip="" sni="" addrs=""
    case "$proto" in
        dot|doh|plain) ;;
        *) log_error "$(tf prov_custom_bad_proto "$proto")"; return 1 ;;
    esac
    if [ -z "$port" ]; then
        case "$proto" in dot) port=853 ;; doh) port=443 ;; *) port=53 ;; esac
    fi
    if ! valid_port "$port"; then
        log_error "$(tf prov_custom_bad_port "$port")"
        return 1
    fi
    if [ -z "$host" ] || [[ "$host" =~ [^A-Za-z0-9.:_-] ]]; then
        log_error "$(t ups_custom_bad_url)"
        return 1
    fi
    if valid_ip "$host"; then
        ip="$host"
        if [ "$proto" != "plain" ]; then sni="$host"; fi
    else
        if ! valid_domain "$host"; then
            log_error "$(t ups_custom_bad_url)"
            return 1
        fi
        log_info "$(tf ups_custom_resolving "$host")"
        addrs=$(godns_resolve_host "$host") || addrs=""
        if [ -z "$addrs" ]; then
            log_error "$(tf ups_custom_resolve_failed "$host")"
            return 1
        fi
        ip="${addrs%%$'\n'*}"
        log_dim "$(tf ups_custom_resolved "$host" "$(printf '%s' "$addrs" | tr '\n' ' ')")"
        sni="$host"
    fi
    # Для TLS с IP вместо имени сертификат проверить нельзя — сервер обязан
    # иметь имя. IP в SNI dnsdist примет, но валидация не пройдёт.
    if [ "$proto" != "plain" ] && valid_ip "$sni"; then
        log_error "$(tf prov_custom_bad_sni "$sni")"
        return 1
    fi
    printf '%s\t%s\t%s\n' "$ip" "$sni" "$port"
    return 0
}

# webui_custom_probe <proto> <host> <port> <path> <ack_plain yes|no>
#   stdout (TSV): ip, sni, port, private (yes|no — только plain),
#   needs_ack (yes), ok (yes|no), latency.
#   rc 0 — ответил;  rc 3 — не ответил (latency несёт причину: «— таймаут»,
#   «— TLS»…);  rc 4 — открытый DNS к публичному адресу без подтверждения:
#   проба НЕ выполнялась, ни один пакет не ушёл — цена названа ДО пробы,
#   как в SSH;  rc 1 — протокол/порт/имя/путь/SNI негодны или имя не
#   разрешилось.
webui_custom_probe() {
    local proto="${1:-}" host="${2:-}" port="${3:-}" path="${4:-}" ack="${5:-no}"
    local res="" rest="" ip="" sni="" private="" line="" url="" lat="" ok="yes"
    res=$(_webui_custom_resolve "$proto" "$host" "$port") || return 1
    # ГРАБЛИ: не `IFS=$'\t' read -r ip sni port`. Табуляция — пробельный
    # символ IFS, подряд идущие схлопываются, и у plain (sni пустой) порт
    # уезжал бы в sni. Расширение параметров пустое поле сохраняет.
    ip="${res%%$'\t'*}"; rest="${res#*$'\t'}"
    sni="${rest%%$'\t'*}"; port="${rest#*$'\t'}"

    if [ "$proto" = "doh" ]; then
        [ -n "$path" ] || path="/dns-query"
        case "$path" in /*) ;; *) path="/$path" ;; esac
        if ! _prov_valid_path "$path"; then
            log_error "$(tf prov_custom_bad_path "$path")"
            return 1
        fi
    else
        path=""
    fi

    # ── открытый :53 — цена названа до того, как ушёл первый пакет ──────
    if [ "$proto" = "plain" ]; then
        if godns_ip_is_private "$ip"; then
            private="yes"
            log_dim "$(t ups_custom_plain_private)"
        else
            private="no"
            if [ "$ack" != "yes" ]; then
                log_warning "$(t ups_custom_plain_public)"
                printf 'ip\t%s\nsni\t%s\nport\t%s\nprivate\tno\nneeds_ack\tyes\nok\t-\n' \
                    "$ip" "$sni" "$port"
                return 4
            fi
        fi
    fi

    # ── живая проба — строка кандидата та же, что у SSH-экрана ──────────
    case "$proto" in
        doh)
            url="https://${sni}"
            if [ "$port" != "443" ]; then url="${url}:${port}"; fi
            url="${url}${path}"
            line="probe|doh|${sni}||${url}|${ip}" ;;
        dot)   line="probe|dot|${sni}|${port}||${ip}" ;;
        plain) line="probe|plain|${ip}|${port}||${ip}" ;;
    esac
    log_info "$(t ups_custom_probing)"
    lat=$(bench_probe_line "$line") || ok="no"
    lat="${lat#"${lat%%[![:space:]]*}"}"
    if [ "$ok" = "yes" ]; then
        log_success "$(tf ups_custom_probe_result "$lat")"
    else
        log_warning "$(tf ups_custom_probe_failed "${lat:-?}")"
    fi
    printf 'ip\t%s\nsni\t%s\nport\t%s\n' "$ip" "$sni" "$port"
    if [ -n "$private" ]; then printf 'private\t%s\n' "$private"; fi
    printf 'ok\t%s\nlatency\t%s\n' "$ok" "$lat"
    if [ "$ok" = "yes" ]; then return 0; fi
    return 3
}

# webui_custom_add <proto> <host> <port> <path> <name> <enable yes|no> <ack_plain yes|no>
#   stdout: id<TAB>custom-…  rc 0 — записан (и применён, если enable=yes);
#   rc 1 — отказ: см. _webui_custom_resolve, дубль/предел (prov_custom_add),
#   открытый DNS к публичному адресу без подтверждения, набор не собрался.
#   Пробы здесь нет: интерфейс обязан был спросить webui_custom_probe до
#   записи — как SSH пробует до вопроса «добавить всё равно?».
webui_custom_add() {
    local proto="${1:-}" host="${2:-}" port="${3:-}" path="${4:-}" name="${5:-}"
    local enable="${6:-no}" ack="${7:-no}"
    local res="" rest="" ip="" sni="" id="" cur="" skipped=""
    res=$(_webui_custom_resolve "$proto" "$host" "$port") || return 1
    ip="${res%%$'\t'*}"; rest="${res#*$'\t'}"
    sni="${rest%%$'\t'*}"; port="${rest#*$'\t'}"
    # Серверное правило, а не галочка в JS: без явного ack открытый DNS к
    # публичному адресу не записывается — тот же confirm с умолчанием «нет»,
    # что у SSH, только ответ пришёл параметром.
    if [ "$proto" = "plain" ] && [ "$ack" != "yes" ] && ! godns_ip_is_private "$ip"; then
        log_error "$(t ups_custom_plain_public)"
        log_error "$(t webui_custom_need_ack)"
        return 1
    fi
    # Напрямую, не через `$( )`: в подоболочке таблица перечиталась бы
    # только там, а здесь осталась бы старой (см. prov_custom_add).
    prov_custom_add "$proto" "$ip" "$port" "$sni" "$path" "$name" >/dev/null || return 1
    id="$PROV_CUSTOM_LAST_ID"
    [ -n "$id" ] || return 1
    log_success "$(tf ups_custom_added "$(prov_name "$id")")"
    printf 'id\t%s\n' "$id"
    if [ "$enable" != "yes" ]; then
        log_dim "$(t webui_custom_enable_hint)"
        return 0
    fi
    cur=$(config_get upstream_ids "") || cur=""
    skipped=$(engine_upstreams_store "${cur}${cur:+,}${id}") || {
        log_error "$(t prov_none_usable)"
        return 1
    }
    if [ -n "$skipped" ]; then log_warning "$(tf prov_skipped "$skipped")"; fi
    webui_apply
}

# webui_custom_remove <id>
#   Единственный апстрим набора снять нельзя — правило проверяется ДО любой
#   записи, чтобы отказ ничего не оставил полусделанным. Не из набора →
#   службу не трогаем: «ничего не изменилось» — не повод применять.
webui_custom_remove() {
    local id="${1:-}" cur="" rest="" x="" shown=""
    if [ -z "$id" ] || ! prov_exists "$id" || [ "$(prov_field "$id" custom)" != "yes" ]; then
        log_error "$(tf webui_custom_unknown "${id:-?}")"
        return 1
    fi
    cur=$(config_get upstream_ids "") || cur=""
    for x in ${cur//,/ }; do
        if [ "$x" != "$id" ]; then rest="${rest}${rest:+,}${x}"; fi
    done
    if [ -n "$cur" ] && [ -z "$rest" ]; then
        log_error "$(t ups_custom_last_one)"
        return 1
    fi
    shown=$(prov_name "$id") || shown="$id"
    if ! prov_custom_remove "$id"; then
        log_error "$(tf webui_custom_unknown "$id")"
        return 1
    fi
    log_success "$(tf ups_custom_removed "$shown")"
    if [ "$rest" = "$cur" ]; then
        return 0
    fi
    engine_upstreams_store "$rest" >/dev/null || {
        log_error "$(t prov_none_usable)"
        return 1
    }
    webui_apply
}

# _webui_custom_addr <id> — «ip:port» / «[v6]:port» из первого bootstrap-
# адреса и dot_port записи (у своей это её порт, у каталога — порт DoT).
_webui_custom_addr() {
    local id="${1:-}" ip="" port=""
    ip=$(prov_bootstrap_first "$id" v4) || ip=""
    if [ -z "$ip" ]; then
        ip=$(prov_bootstrap_first "$id" v6) || ip=""
        if [ -n "$ip" ]; then ip="[${ip}]"; fi
    fi
    if [ -z "$ip" ]; then
        printf '%s' ""
        return 0
    fi
    port=$(prov_field "$id" dot_port) || port=""
    printf '%s:%s' "$ip" "${port:-853}"
    return 0
}

# webui_custom_list_tsv — свои апстримы машинному читателю. Первой строкой
# set_count<TAB>N — размер рабочего набора: боту он нужен для правила
# «единственный не снимать» ещё до вызова. Дальше по строке на запись:
# id, name, proto, addr, sni, in_set (yes|no). Путь DoH не печатается —
# он может быть чужим секретом, а вывод бота живёт на серверах Telegram.
webui_custom_list_tsv() {
    local cur="" n=0 x="" id="" name="" proto="" addr="" sni="" in_set=""
    cur=$(config_get upstream_ids "") || cur=""
    for x in ${cur//,/ }; do
        if [ -n "$x" ]; then n=$((n + 1)); fi
    done
    printf 'set_count\t%s\n' "$n"
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        name=$(prov_name "$id") || name="$id"
        proto=$(prov_field "$id" proto) || proto=""
        addr=$(_webui_custom_addr "$id") || addr=""
        sni=""
        if [ "$proto" != "plain" ]; then
            sni=$(prov_field "$id" dot_host) || sni=""
        fi
        in_set="no"
        case ",${cur}," in *",${id},"*) in_set="yes" ;; esac
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$id" "$name" "${proto:-dot}" "$addr" "$sni" "$in_set"
    done < <(prov_custom_list)
    return 0
}

# Список копий машинному читателю: TSV имя/версия/дата/домен/размер/шифр.
webui_backup_list_tsv() {
    local files="" f ver date_str dom size enc
    files=$(godns_backup_files) || files=""
    [ -n "$files" ] || return 0
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        ver=$(godns_backup_manifest_field "$f" godns_version "?") || ver="?"
        date_str=$(godns_backup_manifest_field "$f" created "?") || date_str="?"
        dom=$(godns_backup_manifest_field "$f" domain "") || dom=""
        size=$(_bk_size "$f") || size="?"
        enc="no"
        case "$f" in *.enc) enc="yes" ;; esac
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$(basename "$f")" "$ver" "$date_str" "$dom" "$size" "$enc"
    done <<EOF
$files
EOF
    return 0
}

# Каталог провайдеров машинному читателю: TSV для /api/upstreams.
# Поля: id, name, filter, filter_label, region, custom (yes|no), proto
# (пусто → dot), addr (_webui_custom_addr), sni (dot_host; у plain пусто).
# Путь DoH в каталог не попадает никогда. Регион — переводом, как в
# SSH-списке: сырой код «own» рядом с бейджем «свой» читался бы как ошибка.
webui_catalog_tsv() {
    local id name filt filt_label region custom proto addr sni
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        name=$(prov_name "$id") || name="$id"
        filt=$(prov_field "$id" filter) || filt=""
        filt_label=$(prov_filter_text "$id") || filt_label=""
        region=$(prov_region_text "$id") || region=""
        custom=$(prov_field "$id" custom) || custom=""
        if [ "$custom" != "yes" ]; then custom="no"; fi
        proto=$(prov_field "$id" proto) || proto=""
        [ -n "$proto" ] || proto="dot"
        addr=$(_webui_custom_addr "$id") || addr=""
        sni=""
        if [ "$proto" != "plain" ]; then
            sni=$(prov_field "$id" dot_host) || sni=""
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$id" "$name" "${filt:-none}" "$filt_label" "$region" \
            "$custom" "$proto" "$addr" "$sni"
    done < <(prov_menu_ids)
    return 0
}

