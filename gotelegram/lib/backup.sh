#!/bin/bash
# goTelegram Pro — backup and restore (i18n-aware)

# Единая глубина хранения бекапов: раньше SSH-меню чистило до 5 и молча удаляло архивы, накопленные ботом/таймером (у них 30)
BACKUP_KEEP_DEFAULT=30

# ── Создание бекапа ──────────────────────────────────────────────────────────
# Тело функции — подоболочка "( ... )": umask 077 действует только внутри и не
# протекает наружу. Внутри архива лежат .env с BOT_TOKEN, config.toml со всеми
# MTProto-секретами, config.json с warp_key и приватные ключи Let's Encrypt —
# ни архив, ни рабочие файлы не должны быть доступны никому, кроме root.
create_backup() (
    umask 077

    local password="$1"
    local output_dir="${2:-$BACKUP_DIR}"
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)
    local backup_name tmp_dir stage suffix=0

    mkdir -p "$output_dir"
    chmod 700 "$output_dir" 2>/dev/null || true

    # Приватная песочница со случайным именем: фиксированный /tmp/<имя> позволял
    # локальному юзеру заранее подложить симлинк и подставить root'у чужой путь.
    stage=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_backup.XXXXXXXX") || return 1
    chmod 700 "$stage" 2>/dev/null || true

    while true; do
        if [ "$suffix" -eq 0 ]; then
            backup_name="gotelegram_backup_${timestamp}"
        else
            backup_name="gotelegram_backup_${timestamp}_${suffix}"
        fi
        tmp_dir="${stage}/${backup_name}"
        if [ ! -e "$tmp_dir" ] && \
           [ ! -e "${output_dir}/${backup_name}.tar.gz" ] && \
           [ ! -e "${output_dir}/${backup_name}.tar.gz.enc" ]; then
            break
        fi
        suffix=$((suffix + 1))
    done
    mkdir -p "$tmp_dir"

    # Собираем файлы
    log_info "$(_t_or backup_collecting 'Собираю конфигурацию...')"

    # telemt конфиг
    if [ -f "$TELEMT_CONFIG" ]; then
        cp "$TELEMT_CONFIG" "$tmp_dir/config.toml"
    fi

    # goTelegram Pro конфиг
    if [ -f "$GOTELEGRAM_CONFIG" ]; then
        cp "$GOTELEGRAM_CONFIG" "$tmp_dir/gotelegram.json"
    fi
    if [ -f "$GOTELEGRAM_DIR/disabled_users.json" ]; then
        cp "$GOTELEGRAM_DIR/disabled_users.json" "$tmp_dir/disabled_users.json" 2>/dev/null
    fi
    if [ -f "$GOTELEGRAM_DIR/backup_schedule.json" ]; then
        cp "$GOTELEGRAM_DIR/backup_schedule.json" "$tmp_dir/backup_schedule.json" 2>/dev/null
    fi

    # Language marker (i18n)
    if [ -f "$GOTELEGRAM_DIR/.language" ]; then
        cp "$GOTELEGRAM_DIR/.language" "$tmp_dir/.language"
    fi

    # nginx конфиг (stealth mode)
    if [ -f "$NGINX_SITE_CONF" ]; then
        cp "$NGINX_SITE_CONF" "$tmp_dir/nginx.conf"
    fi

    # SSL сертификаты и renewal metadata для переносов между VPS
    local domain
    domain=$(config_get domain 2>/dev/null)
    if [ -n "$domain" ] && [ -d "/etc/letsencrypt/live/$domain" ]; then
        mkdir -p "$tmp_dir/letsencrypt/live" "$tmp_dir/letsencrypt/archive" "$tmp_dir/letsencrypt/renewal"
        cp -a "/etc/letsencrypt/live/$domain" "$tmp_dir/letsencrypt/live/" 2>/dev/null
        [ -d "/etc/letsencrypt/archive/$domain" ] && \
            cp -a "/etc/letsencrypt/archive/$domain" "$tmp_dir/letsencrypt/archive/" 2>/dev/null
        [ -f "/etc/letsencrypt/renewal/$domain.conf" ] && \
            cp -a "/etc/letsencrypt/renewal/$domain.conf" "$tmp_dir/letsencrypt/renewal/" 2>/dev/null
        log_dim "SSL сертификаты включены"
    fi

    # Шаблон сайта (если есть)
    if [ -d "$WEBSITE_ROOT" ] && [ -f "$WEBSITE_ROOT/index.html" ]; then
        mkdir -p "$tmp_dir/site"
        cp -a "$WEBSITE_ROOT/." "$tmp_dir/site/"
        log_dim "$(_t_or backup_site_included 'Шаблон сайта включён')"
    fi

    # Custom templates and catalog
    if [ -d "$GOTELEGRAM_DIR/custom_templates" ]; then
        mkdir -p "$tmp_dir/custom_templates"
        cp -a "$GOTELEGRAM_DIR/custom_templates/." "$tmp_dir/custom_templates/" 2>/dev/null
    fi
    if [ -f "$GOTELEGRAM_DIR/templates_catalog.json" ]; then
        cp "$GOTELEGRAM_DIR/templates_catalog.json" "$tmp_dir/templates_catalog.json" 2>/dev/null
    fi

    # Bot state (.env has BotFather token, so encrypted backups are strongly recommended)
    if [ -d "$BOT_DIR" ]; then
        mkdir -p "$tmp_dir/bot"
        [ -f "$BOT_DIR/.env" ] && cp "$BOT_DIR/.env" "$tmp_dir/bot/.env" 2>/dev/null
        # i18n.py в архив не кладём: это исполняемый код, он должен приезжать только из дистрибутива — бекап не канал доставки кода
        [ -d "$BOT_DIR/lang" ] && cp -a "$BOT_DIR/lang" "$tmp_dir/bot/" 2>/dev/null
    fi

    # Код web-админки (server.py, static) в архив не кладём: восстановление чужого архива не должно привозить исполняемое (см. restore_backup)

    # Traffic history
    if [ -f "$GOTELEGRAM_DIR/stats_history.csv" ]; then
        cp "$GOTELEGRAM_DIR/stats_history.csv" "$tmp_dir/stats_history.csv" 2>/dev/null
    fi
    if [ -f "$GOTELEGRAM_DIR/user_stats_history.csv" ]; then
        cp "$GOTELEGRAM_DIR/user_stats_history.csv" "$tmp_dir/user_stats_history.csv" 2>/dev/null
    fi
    if [ -f "$GOTELEGRAM_DIR/shared-443.json" ]; then
        cp "$GOTELEGRAM_DIR/shared-443.json" "$tmp_dir/shared-443.json" 2>/dev/null
    fi

    # Метаданные
    local ip mode engine lang port domain
    ip=$(get_server_ip)
    mode=$(config_get mode 2>/dev/null || echo "unknown")
    engine=$(config_get engine 2>/dev/null || echo "telemt")
    lang=$(type get_language &>/dev/null && get_language 2>/dev/null || echo "en")
    port=$(config_get port 2>/dev/null || echo "443")
    # Ensure port is numeric; fall back to 443 if garbage
    [[ "$port" =~ ^[0-9]+$ ]] || port=443
    domain=$(config_get domain 2>/dev/null || echo "")

    cat > "$tmp_dir/metadata.json" << EOMETA
{
    "backup_version": "1.6",
    "gotelegram_version": "$GOTELEGRAM_VERSION",
    "created_at": "$(date -Iseconds)",
    "hostname": "$(hostname)",
    "ip": "$ip",
    "engine": "$engine",
    "mode": "$mode",
    "language": "$lang",
    "port": $port,
    "domain": "$domain"
}
EOMETA

    # Архивируем (в той же приватной песочнице, 0600 благодаря umask 077)
    local tar_file="${stage}/${backup_name}.tar.gz"
    if ! tar czf "$tar_file" -C "$stage" "$backup_name" 2>/dev/null; then
        log_error "$(_t_or backup_archive_err 'Ошибка создания архива')"
        rm -rf "$stage"
        return 1
    fi

    if [ ! -f "$tar_file" ]; then
        log_error "$(_t_or backup_archive_missing 'Архив не создан')"
        rm -rf "$stage"
        return 1
    fi

    # Шифруем если задан пароль
    local final_file=""
    if [ -n "$password" ]; then
        final_file="${output_dir}/${backup_name}.tar.gz.enc"
        # Пароль — через файл (0600 благодаря umask 077), не через argv: -pass pass:… читается любым локальным юзером в /proc/*/cmdline
        local pass_file="${stage}/.pass"
        printf '%s' "$password" > "$pass_file" || { rm -rf "$stage"; return 1; }
        openssl enc -aes-256-cbc -salt -pbkdf2 -in "$tar_file" -out "$final_file" -pass "file:${pass_file}" 2>/dev/null
        if [ $? -ne 0 ]; then
            log_error "$(_t_or backup_encrypt_err 'Ошибка шифрования')"
            rm -f "$final_file"
            rm -rf "$stage"
            return 1
        fi
        rm -f "$tar_file" "$pass_file"   # незашифрованный tar и файл пароля не держим ни секунды дольше нужного
        log_success "$(_t_or backup_encrypted 'Бекап зашифрован (AES-256-CBC)')"
    else
        final_file="${output_dir}/${backup_name}.tar.gz"
        # /tmp и каталог бекапов часто на разных ФС: mv здесь = копирование, при ENOSPC файл обрезан — без проверки rc и сверки суммы «успех» фиктивен
        local src_sum
        src_sum=$(sha256sum "$tar_file" 2>/dev/null | awk '{print $1}')
        if ! mv "$tar_file" "$final_file"; then
            log_error "$(_t_or backup_archive_err 'Ошибка создания архива')"
            rm -f "$final_file"
            rm -rf "$stage"
            return 1
        fi
        if [ -n "$src_sum" ] && [ "$(sha256sum "$final_file" 2>/dev/null | awk '{print $1}')" != "$src_sum" ]; then
            log_error "$(_t_or backup_archive_err 'Ошибка создания архива')"
            rm -f "$final_file"
            rm -rf "$stage"
            return 1
        fi
    fi

    # Только root: внутри секреты бота, MTProto и приватные ключи TLS
    chmod 600 "$final_file" 2>/dev/null || true

    # SHA256 подпись
    sha256sum "$final_file" > "${final_file}.sha256" 2>/dev/null
    chmod 600 "${final_file}.sha256" 2>/dev/null || true

    # Очистка
    rm -rf "$stage"

    local size
    size=$(du -h "$final_file" | cut -f1)
    if type tf &>/dev/null; then
        log_success "$(tf backup_created_fmt "$final_file" "$size")"
    else
        log_success "Бекап создан: $final_file ($size)"
    fi
    echo "$final_file"
    return 0
)

# ── Восстановление из бекапа ────────────────────────────────────────────────
restore_backup() {
    local backup_file="$1"
    # ${2:-}/${3:-} обязательны: interactive_restore зовёт с одним аргументом, под set -u голый $2 ронял весь install.sh
    local password="${2:-}"
    local assume_yes="${3:-}"

    if [ ! -f "$backup_file" ]; then
        if type tf &>/dev/null; then
            log_error "$(tf backup_file_not_found_fmt "$backup_file")"
        else
            log_error "Файл не найден: $backup_file"
        fi
        return 1
    fi

    # Целостность: если рядом лежит .sha256 — сверяем ДО распаковки
    if [ -f "${backup_file}.sha256" ] && command -v sha256sum >/dev/null 2>&1; then
        local want_sum got_sum
        want_sum=$(grep -oiE '[0-9a-fA-F]{64}' "${backup_file}.sha256" 2>/dev/null | head -1 | tr 'A-F' 'a-f')
        got_sum=$(sha256sum "$backup_file" 2>/dev/null | awk '{print tolower($1)}')
        if [ -n "$want_sum" ] && [ "$want_sum" != "$got_sum" ]; then
            log_error "$(_t_or backup_sha_mismatch 'Контрольная сумма бекапа не совпала — файл повреждён или подменён')"
            log_dim "ожидалось: $want_sum"
            log_dim "получено:  ${got_sum:-—}"
            return 1
        fi
        [ -n "$want_sum" ] && log_dim "$(_t_or backup_sha_ok 'Контрольная сумма бекапа подтверждена')"
    fi

    # mktemp: фиксированный /tmp/gotelegram_restore_$$ предсказуем — локальный
    # юзер мог подложить симлинк и подставить root'у произвольный путь.
    local tmp_dir
    tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_restore.XXXXXXXX") || return 1
    chmod 700 "$tmp_dir" 2>/dev/null || true

    # Расшифровываем если нужно
    local tar_file=""
    if echo "$backup_file" | grep -q '\.enc$'; then
        if [ -z "$password" ]; then
            echo -ne "  $(_t_or backup_enter_pass 'Введите пароль от бекапа'): "
            read -rs password
            echo ""
        fi
        tar_file=$(mktemp "${TMPDIR:-/tmp}/gotelegram_restore.XXXXXXXX.tar.gz") || { rm -rf "$tmp_dir"; return 1; }
        # Пароль — через файл (mktemp даёт 0600), не через argv: иначе он виден в ps всё время расшифровки
        local pass_file
        pass_file=$(mktemp "${tmp_dir}/.pass.XXXXXXXX") || { rm -rf "$tmp_dir"; rm -f "$tar_file"; return 1; }
        printf '%s' "$password" > "$pass_file"
        openssl enc -aes-256-cbc -d -pbkdf2 -in "$backup_file" -out "$tar_file" -pass "file:${pass_file}" 2>/dev/null
        if [ $? -ne 0 ]; then
            log_error "$(_t_or backup_bad_pass 'Неверный пароль или повреждённый файл')"
            rm -rf "$tmp_dir"; rm -f "$tar_file"
            return 1
        fi
        rm -f "$pass_file"
    else
        tar_file="$backup_file"
    fi

    # Распаковываем. Архив может быть присланным «чужим» бекапом, а разворачиваем
    # мы его от root: --no-same-owner/--no-same-permissions не дают внести в
    # систему setuid-root файлы и чужие владельцы.
    tar xzf "$tar_file" -C "$tmp_dir" --no-same-owner --no-same-permissions 2>/dev/null
    if [ $? -ne 0 ]; then
        log_error "$(_t_or backup_extract_err 'Ошибка распаковки архива')"
        rm -rf "$tmp_dir"
        return 1
    fi

    # Находим папку бекапа
    local backup_dir
    backup_dir=$(find "$tmp_dir" -maxdepth 1 -type d -name "gotelegram_backup_*" | head -1)
    [ -z "$backup_dir" ] && backup_dir="$tmp_dir"

    # Legacy bot backups before v2.5.0 stored absolute paths directly in tar:
    # opt/gotelegram/config.json and etc/telemt/config.toml.
    if [ ! -f "$backup_dir/config.toml" ] && [ -f "$tmp_dir/etc/telemt/config.toml" ]; then
        cp "$tmp_dir/etc/telemt/config.toml" "$backup_dir/config.toml" 2>/dev/null || true
    fi
    if [ ! -f "$backup_dir/gotelegram.json" ] && [ -f "$tmp_dir/opt/gotelegram/config.json" ]; then
        cp "$tmp_dir/opt/gotelegram/config.json" "$backup_dir/gotelegram.json" 2>/dev/null || true
    fi
    if [ ! -f "$backup_dir/disabled_users.json" ] && [ -f "$tmp_dir/opt/gotelegram/disabled_users.json" ]; then
        cp "$tmp_dir/opt/gotelegram/disabled_users.json" "$backup_dir/disabled_users.json" 2>/dev/null || true
    fi

    # Проверяем метаданные
    if [ -f "$backup_dir/metadata.json" ]; then
        local bk_version bk_mode bk_ip bk_lang bk_date
        bk_version=$(jq -r '.gotelegram_version // "unknown"' "$backup_dir/metadata.json")
        bk_mode=$(jq -r '.mode // "unknown"' "$backup_dir/metadata.json")
        bk_ip=$(jq -r '.ip // "unknown"' "$backup_dir/metadata.json")
        bk_lang=$(jq -r '.language // "-"' "$backup_dir/metadata.json")
        bk_date=$(jq -r '.created_at // "-"' "$backup_dir/metadata.json")
        echo ""
        echo -e "  ${BOLD}${WHITE}📦 $(_t_or backup_label 'Бекап'):${NC}"
        echo -e "  $(_t_or backup_version_label 'Версия'): $bk_version | $(_t_or backup_mode_label 'Режим'): $bk_mode | IP: $bk_ip | $(_t_or backup_lang_label 'Язык'): $bk_lang"
        echo -e "  $(_t_or backup_date_label 'Дата'): $bk_date"
        echo ""
    fi

    if [ "$assume_yes" != "yes" ] && ! confirm "$(_t_or backup_confirm_restore 'Восстановить конфигурацию? Текущие настройки будут перезаписаны.')"; then
        rm -rf "$tmp_dir"
        return 0
    fi

    # Останавливаем сервисы
    stop_telemt 2>/dev/null
    systemctl stop nginx 2>/dev/null

    # Восстанавливаем telemt конфиг
    if [ -f "$backup_dir/config.toml" ]; then
        mkdir -p /etc/telemt
        cp "$backup_dir/config.toml" "$TELEMT_CONFIG"
        chmod 600 "$TELEMT_CONFIG"
        log_success "$(_t_or backup_restored_telemt 'telemt конфиг восстановлен')"
    fi

    # Восстанавливаем goTelegram Pro конфиг
    if [ -f "$backup_dir/gotelegram.json" ]; then
        mkdir -p "$GOTELEGRAM_DIR"
        cp "$backup_dir/gotelegram.json" "$GOTELEGRAM_CONFIG"
        log_success "$(_t_or backup_restored_gotelegram 'GoTelegram конфиг восстановлен')"
    fi
    if [ -f "$backup_dir/disabled_users.json" ]; then
        mkdir -p "$GOTELEGRAM_DIR"
        cp "$backup_dir/disabled_users.json" "$GOTELEGRAM_DIR/disabled_users.json"
        chmod 600 "$GOTELEGRAM_DIR/disabled_users.json" 2>/dev/null || true
    fi
    if [ -f "$backup_dir/backup_schedule.json" ]; then
        mkdir -p "$GOTELEGRAM_DIR"
        cp "$backup_dir/backup_schedule.json" "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null
        chmod 600 "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null || true
        if command -v jq >/dev/null 2>&1; then
            local restored_schedule
            restored_schedule=$(jq -r '.frequency // "off"' "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null || echo "off")
            case "$restored_schedule" in
                off|daily|weekly|monthly) set_backup_schedule "$restored_schedule" >/dev/null 2>&1 || true ;;
            esac
        fi
    fi

    # Восстанавливаем language marker (i18n)
    if [ -f "$backup_dir/.language" ]; then
        mkdir -p "$GOTELEGRAM_DIR"
        cp "$backup_dir/.language" "$GOTELEGRAM_DIR/.language"
        log_success "$(_t_or backup_restored_lang 'Язык интерфейса восстановлен')"
    fi

    # Восстанавливаем nginx конфиг
    if [ -f "$backup_dir/nginx.conf" ]; then
        mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
        cp "$backup_dir/nginx.conf" "$NGINX_SITE_CONF"
        ln -sf "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"
        log_success "$(_t_or backup_restored_nginx 'nginx конфиг восстановлен')"
    fi

    # Восстанавливаем SSL / Let's Encrypt structure
    if [ -d "$backup_dir/letsencrypt" ]; then
        mkdir -p /etc/letsencrypt/live /etc/letsencrypt/archive /etc/letsencrypt/renewal
        [ -d "$backup_dir/letsencrypt/live" ] && cp -a "$backup_dir/letsencrypt/live/." /etc/letsencrypt/live/ 2>/dev/null
        [ -d "$backup_dir/letsencrypt/archive" ] && cp -a "$backup_dir/letsencrypt/archive/." /etc/letsencrypt/archive/ 2>/dev/null
        [ -d "$backup_dir/letsencrypt/renewal" ] && cp -a "$backup_dir/letsencrypt/renewal/." /etc/letsencrypt/renewal/ 2>/dev/null
        log_success "$(_t_or backup_restored_ssl 'SSL сертификаты восстановлены')"
    elif [ -d "$backup_dir/certs" ]; then
        local domain
        domain=$(config_get domain 2>/dev/null)
        if [ -n "$domain" ]; then
            local cert_dir="/etc/letsencrypt/live/$domain"
            mkdir -p "$cert_dir"
            cp "$backup_dir/certs/"* "$cert_dir/" 2>/dev/null
            log_success "$(_t_or backup_restored_ssl 'SSL сертификаты восстановлены')"
        fi
    fi

    # Восстанавливаем шаблон сайта
    if [ -d "$backup_dir/site" ]; then
        mkdir -p "$WEBSITE_ROOT"
        cp -a "$backup_dir/site/." "$WEBSITE_ROOT/"
        chown -R www-data:www-data "$WEBSITE_ROOT" 2>/dev/null
        log_success "$(_t_or backup_restored_site 'Шаблон сайта восстановлен')"
    fi

    # Восстанавливаем custom templates/catalog/statistics
    if [ -d "$backup_dir/custom_templates" ]; then
        mkdir -p "$GOTELEGRAM_DIR/custom_templates"
        cp -a "$backup_dir/custom_templates/." "$GOTELEGRAM_DIR/custom_templates/" 2>/dev/null
        log_success "Пользовательские шаблоны восстановлены"
    fi
    if [ -f "$backup_dir/templates_catalog.json" ]; then
        cp "$backup_dir/templates_catalog.json" "$GOTELEGRAM_DIR/templates_catalog.json" 2>/dev/null
    fi
    if [ -f "$backup_dir/stats_history.csv" ]; then
        cp "$backup_dir/stats_history.csv" "$GOTELEGRAM_DIR/stats_history.csv" 2>/dev/null
        log_success "История статистики восстановлена"
    fi
    if [ -f "$backup_dir/user_stats_history.csv" ]; then
        cp "$backup_dir/user_stats_history.csv" "$GOTELEGRAM_DIR/user_stats_history.csv" 2>/dev/null
        log_success "История статистики пользователей восстановлена"
    fi
    if [ -f "$backup_dir/shared-443.json" ]; then
        cp "$backup_dir/shared-443.json" "$GOTELEGRAM_DIR/shared-443.json" 2>/dev/null
    fi

    # Восстанавливаем состояние бота (только данные: .env)
    if [ -d "$backup_dir/bot" ]; then
        mkdir -p "$BOT_DIR"
        [ -f "$backup_dir/bot/.env" ] && cp "$backup_dir/bot/.env" "$BOT_DIR/.env" 2>/dev/null && chmod 600 "$BOT_DIR/.env"
        # i18n.py и lang/ из архива не берём: архив может быть «чужим» (та же модель угроз, что у tar выше), а бот перезапускается от root — их кладёт установщик из дистрибутива
        log_success "Конфигурация Telegram-бота восстановлена"
    fi

    # Код web-админки (server.py, static) из архива не восстанавливаем: подменённый server.py из «чужого» бекапа исполнился бы от root после рестарта — код только из дистрибутива
    if [ -d "$backup_dir/admin_web" ]; then
        rm -f "$ADMIN_WEB_DIR/token" 2>/dev/null || true
        if type install_admin_web &>/dev/null; then
            install_admin_web || log_warning "Не удалось переустановить web-админку из дистрибутива"
        else
            log_warning "Код web-админки из бекапа не восстанавливается (безопасность): при необходимости переустановите панель установщиком"
        fi
    fi

    # Запускаем сервисы
    if is_telemt_installed && [ ! -f "/etc/systemd/system/${TELEMT_SERVICE}.service" ]; then
        install_telemt_service
    fi
    if is_telemt_installed; then
        start_telemt
    fi
    command -v nginx &>/dev/null && systemctl start nginx 2>/dev/null
    systemctl restart gotelegram-bot 2>/dev/null || true
    systemctl restart gotelegram-admin 2>/dev/null || true

    # Очистка
    rm -rf "$tmp_dir"
    [ "$tar_file" != "$backup_file" ] && rm -f "$tar_file"

    log_success "$(_t_or backup_restore_done 'Восстановление завершено!')"
    show_proxy_info
    return 0
}

# ── Список бекапов ───────────────────────────────────────────────────────────
list_backups() {
    if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then
        log_info "$(_t_or backup_none 'Бекапов нет')"
        return 1
    fi

    echo ""
    echo -e "  ${BOLD}${WHITE}📦 $(_t_or backup_list_title 'Доступные бекапы'):${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..60})${NC}"

    local i=1
    for f in "$BACKUP_DIR"/*.tar.gz*; do
        [ -f "$f" ] || continue
        [[ "$f" == *.sha256 ]] && continue
        local size date_str name
        size=$(du -h "$f" | cut -f1)
        name=$(basename "$f")
        date_str=$(echo "$name" | grep -oE '[0-9]{8}_[0-9]{6}' | head -1)
        local encrypted=""
        [[ "$f" == *.enc ]] && encrypted=" 🔒"
        echo -e "  ${CYAN}${i})${NC} ${name} (${size})${encrypted}"
        ((i++))
    done
    echo -e "  ${DIM}$(printf '─%.0s' {1..60})${NC}"
}

# ── Очистка старых бекапов ───────────────────────────────────────────────────
cleanup_old_backups() {
    # было 5: ручной бекап из SSH молча удалял архивы, которые бот/панель/таймер хранят по 30
    local keep="${1:-$BACKUP_KEEP_DEFAULT}"
    local count
    count=$(find "$BACKUP_DIR" -name "*.tar.gz*" ! -name "*.sha256" 2>/dev/null | wc -l)

    if [ "$count" -gt "$keep" ]; then
        local to_delete=$((count - keep))
        find "$BACKUP_DIR" -name "*.tar.gz*" ! -name "*.sha256" 2>/dev/null | sort | head -n "$to_delete" | while read -r f; do
            rm -f "$f" "${f}.sha256"
        done
        if type tf &>/dev/null; then
            log_dim "$(tf backup_cleanup_fmt "$to_delete" "$keep")"
        else
            log_dim "Удалено $to_delete старых бекапов (оставлено $keep)"
        fi
    fi
}

# ── Расписание бекапов ───────────────────────────────────────────────────────
backup_schedule_calendar() {
    case "${1:-off}" in
        off) echo "" ;;
        daily) echo "*-*-* 03:20:00" ;;
        weekly) echo "Sun 03:20:00" ;;
        monthly) echo "*-*-01 03:20:00" ;;
        *) return 1 ;;
    esac
}

set_backup_schedule() {
    local frequency="${1:-off}"
    local calendar
    if ! calendar=$(backup_schedule_calendar "$frequency"); then
        log_error "Unsupported backup schedule: $frequency"
        return 1
    fi

    mkdir -p "$GOTELEGRAM_DIR" "$BACKUP_DIR"

    if [ "$frequency" = "off" ]; then
        systemctl disable --now gotelegram-backup.timer >/dev/null 2>&1 || true
        rm -f /etc/systemd/system/gotelegram-backup.timer /etc/systemd/system/gotelegram-backup.service
        systemctl daemon-reload >/dev/null 2>&1 || true
    else
        cat > /etc/systemd/system/gotelegram-backup.service << 'EOSVC'
[Unit]
Description=goTelegram Pro backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
Environment=GOTELEGRAM_BACKUP_KEEP=30
ExecStart=/bin/bash -lc 'source /opt/gotelegram/lib/common.sh; source /opt/gotelegram/lib/i18n.sh; source /opt/gotelegram/lib/telemt.sh; source /opt/gotelegram/lib/website.sh; source /opt/gotelegram/lib/backup.sh; load_language "$(detect_language 2>/dev/null || echo en)"; create_backup ""; cleanup_old_backups "${GOTELEGRAM_BACKUP_KEEP:-30}"'
EOSVC

        cat > /etc/systemd/system/gotelegram-backup.timer << EOTIMER
[Unit]
Description=goTelegram Pro scheduled backup

[Timer]
OnCalendar=$calendar
Persistent=true
RandomizedDelaySec=15m
Unit=gotelegram-backup.service

[Install]
WantedBy=timers.target
EOTIMER
        systemctl daemon-reload >/dev/null 2>&1 || return 1
        systemctl enable --now gotelegram-backup.timer >/dev/null 2>&1 || return 1
    fi

    cat > "$GOTELEGRAM_DIR/backup_schedule.json" << EOSCHEDULE
{
    "frequency": "$frequency",
    "calendar": "$calendar",
    "keep": $BACKUP_KEEP_DEFAULT,
    "updated_at": "$(date -Iseconds)"
}
EOSCHEDULE
    chmod 600 "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null || true
    log_success "Backup schedule: $frequency"
    echo "$frequency"
}

backup_schedule_status() {
    local frequency="off" calendar=""
    if [ -f "$GOTELEGRAM_DIR/backup_schedule.json" ] && command -v jq >/dev/null 2>&1; then
        frequency=$(jq -r '.frequency // "off"' "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null || echo "off")
        calendar=$(jq -r '.calendar // ""' "$GOTELEGRAM_DIR/backup_schedule.json" 2>/dev/null || echo "")
    fi
    echo "frequency=$frequency calendar=$calendar"
    systemctl list-timers gotelegram-backup.timer --no-pager 2>/dev/null || true
}

# ── Интерактивный бекап ──────────────────────────────────────────────────────
interactive_backup() {
    echo ""
    echo -e "  ${BOLD}${WHITE}💾 $(_t_or backup_create_title 'Создание бекапа')${NC}"
    echo -ne "  $(_t_or backup_encrypt_prompt 'Зашифровать бекап паролем?') [Y/n]: "
    read -r use_pass

    local password=""
    if [[ ! "$use_pass" =~ ^[Nn] ]]; then
        echo -ne "  $(_t_or backup_enter_pass 'Введите пароль'): "
        read -rs password
        echo ""
        echo -ne "  $(_t_or backup_repeat_pass 'Повторите пароль'): "
        read -rs password2
        echo ""
        if [ "$password" != "$password2" ]; then
            log_error "$(_t_or backup_pass_mismatch 'Пароли не совпадают')"
            return 1
        fi
        if [ ${#password} -lt 6 ]; then
            log_error "$(_t_or backup_pass_short 'Пароль слишком короткий (минимум 6 символов)')"
            return 1
        fi
    fi

    create_backup "$password"
    cleanup_old_backups
}

# ── Интерактивное восстановление ─────────────────────────────────────────────
interactive_restore() {
    list_backups || return 1

    echo -ne "  $(_t_or backup_pick_prompt 'Номер бекапа (или путь к файлу)'): "
    read -r choice

    local backup_file=""
    if [[ "$choice" =~ ^[0-9]+$ ]]; then
        local i=1
        for f in "$BACKUP_DIR"/*.tar.gz*; do
            [ -f "$f" ] || continue
            [[ "$f" == *.sha256 ]] && continue
            if [ "$i" -eq "$choice" ]; then
                backup_file="$f"
                break
            fi
            ((i++))
        done
    elif [ -f "$choice" ]; then
        backup_file="$choice"
    fi

    if [ -z "$backup_file" ]; then
        log_error "$(_t_or backup_not_found 'Бекап не найден')"
        return 1
    fi

    restore_backup "$backup_file"
}
