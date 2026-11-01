#!/bin/bash
# GoTelegram v2.5.0 — Управление telemt binary
# Скачивание, обновление, запуск, остановка через systemd

TELEMT_GITHUB="telemt/telemt"
TELEMT_RELEASE_API="https://api.github.com/repos/${TELEMT_GITHUB}/releases/latest"
TELEMT_USER="telemt"
TELEMT_GROUP="telemt"

# ── Получение последней версии ───────────────────────────────────────────────
get_latest_telemt_version() {
    local resp
    resp=$(curl -s --max-time 10 "$TELEMT_RELEASE_API" 2>/dev/null)
    if [ $? -ne 0 ] || [ -z "$resp" ]; then
        log_error "Не удалось получить информацию о релизах telemt"
        return 1
    fi
    echo "$resp" | jq -r '.tag_name // empty'
}

# Метаданные последних релизов ядра: строки "tag|YYYY-MM-DD|стабильная|предрелиз".
_telemt_releases_meta() {
    local resp
    resp=$(curl -s --max-time 10 "https://api.github.com/repos/${TELEMT_GITHUB}/releases?per_page=12" 2>/dev/null)
    [ -z "$resp" ] && return 1
    echo "$resp" | jq -r '.[] | "\(.tag_name)|\(.published_at[0:10])|\(if .prerelease then "предрелиз" else "стабильная" end)"' 2>/dev/null
}

get_telemt_download_url() {
    local version="${1:-}"
    local api="$TELEMT_RELEASE_API"
    if [ -n "$version" ] && [ "$version" != "latest" ]; then
        api="https://api.github.com/repos/${TELEMT_GITHUB}/releases/tags/${version}"
    fi
    local arch
    arch=$(get_arch)
    local resp
    resp=$(curl -s --max-time 10 "$api" 2>/dev/null)
    if [ -z "$resp" ]; then return 1; fi

    # URL format: telemt-x86_64-linux-gnu.tar.gz (arch BEFORE linux)
    local arch_pattern
    case "$arch" in
        amd64)  arch_pattern="(amd64|x86_64)" ;;
        arm64)  arch_pattern="(arm64|aarch64)" ;;
        armv7)  arch_pattern="(armv7|arm)" ;;
        *)      arch_pattern="${arch}" ;;
    esac

    # Из списка ассетов выбираем ИМЕННО бинарь: файлы подписей/сумм отсекаем здесь,
    # но не выбрасываем совсем — их отдельно тянет _telemt_expected_sha256() ниже
    # и сверяет ДО установки (ядро запускается от root).
    echo "$resp" | jq -r ".assets[].browser_download_url" 2>/dev/null \
        | grep -iE "$arch_pattern" \
        | grep -i "linux" \
        | grep -viE '\.(sha256|sha256sum|sha512|md5|asc|sig|pem)$' \
        | grep -viE 'checksums?' \
        | grep "gnu" \
        | head -1
}

# ── Ожидаемая SHA256 скачиваемого ассета ─────────────────────────────────────
# 1) <asset>.sha256 рядом с ассетом; 2) общий файл сумм релиза
# (sha256sum.txt / SHA256SUMS / checksums.txt). Пусто = сумма недоступна.
_telemt_expected_sha256() {
    local url="$1" version="${2:-}"
    local asset sum
    asset=$(basename "$url")

    sum=$(curl -fsSL --max-time 30 "${url}.sha256" 2>/dev/null | grep -oiE '[0-9a-fA-F]{64}' | head -1)
    if [ -n "$sum" ]; then
        echo "$sum" | tr 'A-F' 'a-f'
        return 0
    fi

    local api="$TELEMT_RELEASE_API"
    if [ -n "$version" ] && [ "$version" != "latest" ]; then
        api="https://api.github.com/repos/${TELEMT_GITHUB}/releases/tags/${version}"
    fi
    local resp sums_url
    resp=$(curl -s --max-time 15 "$api" 2>/dev/null)
    [ -n "$resp" ] || return 1
    while IFS= read -r sums_url; do
        [ -n "$sums_url" ] || continue
        sum=$(curl -fsSL --max-time 30 "$sums_url" 2>/dev/null | awk -v a="$asset" '
            {
                n = $NF; sub(/^\*/, "", n); sub(/.*\//, "", n)
                if (n == a && length($1) == 64 && $1 ~ /^[0-9a-fA-F]+$/) { print tolower($1); exit }
            }')
        if [ -n "$sum" ]; then
            echo "$sum"
            return 0
        fi
    done < <(echo "$resp" | jq -r '.assets[].browser_download_url' 2>/dev/null | grep -iE 'sha256|sha512|checksums?')
    return 1
}

# Сверка суммы скачанного файла. 0 = ок; 1 = не совпало; 2 = сумма недоступна.
_telemt_verify_sha256() {
    local file="$1" url="$2" version="${3:-}"
    command -v sha256sum >/dev/null 2>&1 || return 2
    local want
    want=$(_telemt_expected_sha256 "$url" "$version") || want=""
    [ -n "$want" ] || return 2
    printf '%s  %s\n' "$want" "$file" | sha256sum -c --status - 2>/dev/null || {
        log_error "SHA256 не совпала: ожидалось $want, получено $(sha256sum "$file" 2>/dev/null | awk '{print $1}')"
        return 1
    }
    log_dim "SHA256 подтверждена: $want"
    return 0
}

# ── Установленная версия ─────────────────────────────────────────────────────
get_installed_telemt_version() {
    if [ -x "$TELEMT_BIN" ]; then
        "$TELEMT_BIN" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1
    else
        echo ""
    fi
}

is_telemt_installed() {
    [ -x "$TELEMT_BIN" ]
}

# Целевая версия ядра: из config.json telemt_version, иначе глобал TELEMT_VERSION_PREF,
# иначе последняя. Дефолт/recommended/latest -> актуальный тег (фолбэк на пин). Возвращает КОНКРЕТНЫЙ тег.
telemt_target_version() {
    local v
    v=$(read_config_or_default telemt_version "" 2>/dev/null)
    [ -z "$v" ] && v="${TELEMT_VERSION_PREF:-}"
    case "$v" in
        pinned|stable) echo "${TELEMT_PINNED_VERSION:-3.4.18}" ;;
        ""|recommended|latest)
            local _lt; _lt=$(get_latest_telemt_version 2>/dev/null)
            [ -n "$_lt" ] && echo "$_lt" || echo "${TELEMT_PINNED_VERSION:-3.4.18}" ;;
        *) echo "$v" ;;
    esac
}

# ── Скачивание и установка ───────────────────────────────────────────────────
download_telemt() {
    local version="${1:-$(telemt_target_version)}"
    local url
    url=$(get_telemt_download_url "$version")
    if [ -z "$url" ]; then
        log_error "Не найден бинарник telemt для архитектуры $(get_arch)"
        return 1
    fi

    # mktemp вместо предсказуемых путей: иначе локальный непривилегированный
    # юзер заранее кладёт симлинк, а root пишет по нему в произвольный файл.
    local tmp_file extract_dir
    tmp_file=$(mktemp "${TMPDIR:-/tmp}/telemt_download.XXXXXXXX") || return 1
    extract_dir=$(mktemp -d "${TMPDIR:-/tmp}/telemt_extract.XXXXXXXX") || { rm -f "$tmp_file"; return 1; }
    chmod 700 "$extract_dir" 2>/dev/null || true
    log_info "Скачивание telemt ${version}: $url"

    if ! curl -L -s --max-time 120 -o "$tmp_file" "$url"; then
        log_error "Ошибка скачивания telemt"
        rm -f "$tmp_file"
        rm -rf "$extract_dir"
        return 1
    fi

    # Проверяем что файл не пустой и не HTML
    local file_size
    file_size=$(stat -c%s "$tmp_file" 2>/dev/null || echo 0)
    if [ "$file_size" -lt 1000 ]; then
        log_error "Скачанный файл слишком маленький ($file_size байт) — возможна ошибка сети"
        rm -f "$tmp_file"
        rm -rf "$extract_dir"
        return 1
    fi

    # ── Контрольная сумма ДО установки ───────────────────────────────────────
    # Бинарь копируется в /usr/local/bin и запускается от root, поэтому
    # непроверенный файл не ставим. Поведение явное, без тихого пропуска.
    _telemt_verify_sha256 "$tmp_file" "$url" "$version"
    case $? in
        0) ;;
        1)  log_error "Контрольная сумма telemt не совпала — файл повреждён или подменён. Установка отменена."
            rm -f "$tmp_file"; rm -rf "$extract_dir"; return 1 ;;
        2)  if [ "${TELEMT_ALLOW_UNVERIFIED:-0}" = "1" ]; then
                log_warning "Контрольная сумма для telemt ${version} недоступна — ставлю по явному TELEMT_ALLOW_UNVERIFIED=1"
            else
                log_error "Не найдена контрольная сумма релиза telemt ${version} (${url}.sha256 / sha256sum.txt)."
                log_error "Установка отменена: ядро работает от root, непроверенный бинарь не ставим."
                log_dim  "Осознанно продолжить: TELEMT_ALLOW_UNVERIFIED=1 $0 ... (не рекомендуется)"
                rm -f "$tmp_file"; rm -rf "$extract_dir"; return 1
            fi ;;
    esac

    # Определяем тип файла и распаковываем
    local mime extracted=""
    mime=$(file -b --mime-type "$tmp_file" 2>/dev/null)

    case "$mime" in
        application/gzip|application/x-gzip)
            tar xzf "$tmp_file" -C "$extract_dir" --no-same-owner --no-same-permissions 2>/dev/null
            extracted=$(find "$extract_dir" -name "telemt" -type f 2>/dev/null | head -1)
            if [ -z "$extracted" ]; then
                # Может быть просто gzip без tar
                gunzip -c "$tmp_file" > "$extract_dir/telemt_bin" 2>/dev/null
                extracted="$extract_dir/telemt_bin"
            fi
            ;;
        application/x-tar)
            tar xf "$tmp_file" -C "$extract_dir" --no-same-owner --no-same-permissions 2>/dev/null
            extracted=$(find "$extract_dir" -name "telemt" -type f 2>/dev/null | head -1)
            ;;
        application/zip)
            unzip -o "$tmp_file" -d "$extract_dir" 2>/dev/null
            extracted=$(find "$extract_dir" -name "telemt" -type f 2>/dev/null | head -1)
            ;;
        application/octet-stream|application/x-executable)
            extracted="$tmp_file"
            ;;
        *)
            # Пробуем определить по содержимому
            if file "$tmp_file" 2>/dev/null | grep -q "ELF"; then
                extracted="$tmp_file"
            else
                # Пробуем как tar.gz
                tar xzf "$tmp_file" -C "$extract_dir" --no-same-owner --no-same-permissions 2>/dev/null
                extracted=$(find "$extract_dir" -name "telemt" -type f 2>/dev/null | head -1)
            fi
            ;;
    esac

    if [ -z "$extracted" ] || [ ! -f "$extracted" ]; then
        log_error "Не удалось извлечь бинарник telemt (mime: $mime)"
        rm -f "$tmp_file"
        rm -rf "$extract_dir"
        return 1
    fi

    # Устанавливаем: кладём рядом и подменяем через rename — запись поверх
    # работающего бинаря даёт ETXTBSY, и тогда обновление молча не применяется.
    if ! install -m 0755 "$extracted" "${TELEMT_BIN}.new"; then
        log_error "Не удалось записать новый бинарь telemt в ${TELEMT_BIN}.new"
        rm -f "${TELEMT_BIN}.new"
        rm -f "$tmp_file"
        rm -rf "$extract_dir"
        return 1
    fi
    if ! mv -f "${TELEMT_BIN}.new" "$TELEMT_BIN"; then
        log_error "Не удалось заменить $TELEMT_BIN новым бинарём — осталась прежняя версия"
        rm -f "${TELEMT_BIN}.new"
        rm -f "$tmp_file"
        rm -rf "$extract_dir"
        return 1
    fi
    rm -f "$tmp_file"
    rm -rf "$extract_dir"

    # Проверяем
    if ! "$TELEMT_BIN" --version &>/dev/null; then
        log_error "Бинарник telemt не запускается ($(file -b "$TELEMT_BIN" 2>/dev/null))"
        return 1
    fi

    # Версию читаем уже с установленного бинаря и сверяем с запрошенной: иначе
    # человек выбирает версию ядра, видит «готово», а работает совсем другая.
    local installed_ver want_ver
    installed_ver=$(get_installed_telemt_version)
    want_ver=$(echo "$version" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ -n "$want_ver" ] && [ -n "$installed_ver" ] && [ "$want_ver" != "$installed_ver" ]; then
        log_warning "Запрошена версия telemt ${version}, а установленный бинарь сообщает ${installed_ver}"
    fi
    log_success "telemt ${installed_ver:-неизвестной версии} установлен в $TELEMT_BIN"
    return 0
}

# ── Системный пользователь ───────────────────────────────────────────────────
create_telemt_user() {
    if ! id "$TELEMT_USER" &>/dev/null; then
        useradd -r -s /usr/sbin/nologin -d /etc/telemt "$TELEMT_USER" 2>/dev/null
        log_dim "Создан системный пользователь: $TELEMT_USER"
    fi
}

# ── Systemd сервис ───────────────────────────────────────────────────────────
install_telemt_service() {
    local config_path="${1:-$TELEMT_CONFIG}"

    cat > "/etc/systemd/system/${TELEMT_SERVICE}.service" << EOF
[Unit]
Description=goTelegram Pro MTProxy (telemt engine)
Documentation=https://github.com/telemt/telemt
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=$TELEMT_BIN run $config_path
Restart=always
RestartSec=5
LimitNOFILE=65535

# Писহемая рабочая папка для снапшота beobachten.txt и кэша proxy-config
# (иначе при ProtectSystem=full telemt не может писать в /etc -> WARN каждые 15с)
WorkingDirectory=/var/lib/telemt
StateDirectory=telemt

# Безопасность
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

    mkdir -p /var/lib/telemt 2>/dev/null || true
    systemctl daemon-reload
    log_success "Systemd сервис $TELEMT_SERVICE создан"
}

# ── Управление сервисом ──────────────────────────────────────────────────────
# start_telemt ensures telemt is running with the CURRENT on-disk config.
# If the service is already active we must restart (not plain start) — otherwise
# the running process keeps its old in-memory config and the freshly generated
# /etc/telemt/config.toml is silently ignored. This was the root cause of the
# "lite-mode key doesn't work after reinstall" bug: telemt had loaded the
# previous Pro config (tls_domain=anten-ka.com) and was rejecting SNI=google.com
# clients with unknown_sni_action=Drop even though the on-disk config said
# tls_domain=google.com.
wait_telemt_ready() {
    local timeout="${1:-90}"
    local port elapsed=0
    port=$(awk '
        /^\[server\]/ { in_server=1; next }
        /^\[/ && in_server { exit }
        in_server && $1 == "port" {
            sub(/^[^=]*=[[:space:]]*/, "")
            gsub(/[[:space:]]/, "")
            print
            exit
        }
    ' "$TELEMT_CONFIG" 2>/dev/null)
    # Порт главного listener'а важнее [server] port: при WEB на отдельном имени
    # ядро слушает loopback за nginx stream, а 443 принадлежит nginx — ожидание
    # «telemt на 443» висело бы все 90 секунд и докладывало бы отказ.
    local lport
    lport=$(awk '
        function done_blk() { if (blk && !web && p != "") { print p; found=1; exit } }
        /^\[\[server\.listeners\]\]/ { done_blk(); blk=1; web=0; p=""; next }
        /^\[/ { done_blk(); blk=0; next }
        blk && /^port[[:space:]]*=/ { v=$0; sub(/^[^=]*=[[:space:]]*/, "", v); gsub(/[[:space:]]/, "", v); p=v }
        blk && /^transport[[:space:]]*=[[:space:]]*"web"/ { web=1 }
        END { if (!found && blk && !web && p != "") print p }
    ' "$TELEMT_CONFIG" 2>/dev/null)
    [[ "$lport" =~ ^[0-9]+$ ]] && port="$lport"
    [[ "$port" =~ ^[0-9]+$ ]] || port=443

    while [ "$elapsed" -lt "$timeout" ]; do
        if ! systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
            return 1
        fi
        if ss -ltnp 2>/dev/null | grep -E ":${port}\b" | grep -q "telemt"; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

start_telemt() {
    # Разводка 443 по имени (WEB на отдельном имени): кто держит 443 — ядро или
    # nginx stream — решает конфиг ядра; порядок соблюдает website.sh
    # Сбой разводки — ошибка: ядро на loopback без nginx на 443 — это лежащий
    # прокси, а вызывающий (включение WEB, смена имени) откатывает режим.
    if type web_front_sync_before_restart >/dev/null 2>&1 && ! web_front_sync_before_restart; then
        log_error "Разводка 443 по имени не применена — telemt не запущен"
        return 1
    fi
    if systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        systemctl restart "$TELEMT_SERVICE" 2>/dev/null
    else
        systemctl start "$TELEMT_SERVICE" 2>/dev/null
    fi
    if wait_telemt_ready 90; then
        log_success "telemt запущен"
        return 0
    else
        log_error "telemt не запустился или не открыл порт"
        journalctl -u "$TELEMT_SERVICE" --no-pager -n 10 2>/dev/null
        return 1
    fi
}

stop_telemt() {
    if systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        systemctl stop "$TELEMT_SERVICE"
        log_success "telemt остановлен"
    else
        log_dim "telemt уже остановлен"
    fi
}

restart_telemt() {
    if type web_front_sync_before_restart >/dev/null 2>&1 && ! web_front_sync_before_restart; then
        log_error "Разводка 443 по имени не применена — telemt не перезапущен"
        return 1
    fi
    systemctl restart "$TELEMT_SERVICE" 2>/dev/null
    if wait_telemt_ready 90; then
        log_success "telemt перезапущен"
        return 0
    else
        log_error "telemt не перезапустился или не открыл порт"
        journalctl -u "$TELEMT_SERVICE" --no-pager -n 10 2>/dev/null
        return 1
    fi
}

enable_telemt() {
    systemctl enable "$TELEMT_SERVICE" 2>/dev/null
}

telemt_status() {
    if ! is_telemt_installed; then
        echo "not_installed"
        return
    fi
    if systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        echo "running"
    elif systemctl is-enabled --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        echo "stopped"
    else
        echo "disabled"
    fi
}

telemt_logs() {
    local lines="${1:-40}"
    journalctl -u "$TELEMT_SERVICE" --no-pager -n "$lines" 2>/dev/null
}

telemt_uptime() {
    local started
    started=$(systemctl show "$TELEMT_SERVICE" --property=ActiveEnterTimestamp --value 2>/dev/null)
    if [ -n "$started" ] && [ "$started" != "" ]; then
        echo "$started"
    else
        echo "N/A"
    fi
}

# ── Обновление ───────────────────────────────────────────────────────────────
check_telemt_update() {
    local current latest
    current=$(get_installed_telemt_version)
    latest=$(get_latest_telemt_version)

    if [ -z "$current" ] || [ -z "$latest" ]; then
        return 1
    fi

    if [ "$current" != "$latest" ]; then
        echo "$latest"
        return 0  # есть обновление
    fi
    return 1  # актуально
}

update_telemt() {
    local latest
    latest=$(check_telemt_update)
    if [ $? -ne 0 ]; then
        log_info "telemt уже последней версии ($(get_installed_telemt_version))"
        return 0
    fi

    log_info "Доступно обновление: $(get_installed_telemt_version) → $latest"
    if ! confirm "Обновить telemt?"; then
        return 0
    fi

    stop_telemt
    if download_telemt; then
        start_telemt
        log_success "telemt обновлён до $latest"
    else
        start_telemt  # запускаем старую версию обратно
        log_error "Обновление не удалось"
        return 1
    fi
}

# ── Полная установка telemt ──────────────────────────────────────────────────
install_telemt_full() {
    log_step "Установка telemt"

    # Создаём директории
    mkdir -p /etc/telemt

    # Скачиваем бинарник
    run_with_spinner "Скачивание telemt" download_telemt || return 1

    # Устанавливаем systemd сервис
    install_telemt_service

    # Включаем автозапуск
    enable_telemt

    log_success "telemt готов к работе"
    return 0
}

# ── Удаление telemt ──────────────────────────────────────────────────────────
remove_telemt() {
    stop_telemt
    systemctl disable "$TELEMT_SERVICE" 2>/dev/null
    rm -f "/etc/systemd/system/${TELEMT_SERVICE}.service"
    systemctl daemon-reload
    rm -f "$TELEMT_BIN"
    rm -rf /etc/telemt
    log_success "telemt полностью удалён"
}
