#!/bin/bash
# GoTelegram v2.5.0 — Управление сайтом (nginx + certbot + шаблоны)

# ── Установка nginx ──────────────────────────────────────────────────────────
install_nginx() {
    if command -v nginx &>/dev/null; then
        log_dim "nginx уже установлен"
        return 0
    fi
    log_info "Установка nginx..."
    case "$(get_pkg_manager)" in
        apt) apt_update && apt_install nginx || return 1 ;;
        dnf) dnf install -y -q nginx || return 1 ;;
        yum) yum install -y -q nginx || return 1 ;;
    esac
    systemctl enable nginx 2>/dev/null
}

# ── Установка certbot ────────────────────────────────────────────────────────
install_certbot() {
    if command -v certbot &>/dev/null; then
        log_dim "certbot уже установлен"
        return 0
    fi
    log_info "Установка certbot..."
    case "$(get_pkg_manager)" in
        apt) apt_install certbot python3-certbot-nginx || return 1 ;;
        dnf) dnf install -y -q certbot python3-certbot-nginx || return 1 ;;
        yum) yum install -y -q certbot python3-certbot-nginx || return 1 ;;
    esac
}

# ── Генерация nginx конфига ──────────────────────────────────────────────────
generate_nginx_config() {
    local domain="$1"
    local proxy_port="${2:-443}"
    local use_ssl="${3:-true}"

    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

    # WEB-прокси меняет схему: TLS остаётся здесь, но весь vhost уходит в
    # telemt, а прежний сайт становится decoy на внутреннем порту.
    # Источник правды — уже сгенерированный конфиг ядра: если в нём есть
    # WEB-listener, telemt шлёт PROXY-заголовок, и nginx обязан его ждать; если
    # нет — не обязан. Два генератора с разными условиями расходились.
    local web_on=0
    if [ -f "${TELEMT_CONFIG:-/etc/telemt/config.toml}" ] \
       && grep -q '^transport = "web"' "${TELEMT_CONFIG:-/etc/telemt/config.toml}" 2>/dev/null; then
        web_on=1
    elif [ ! -f "${TELEMT_CONFIG:-/etc/telemt/config.toml}" ] \
         && type telemt_web_active >/dev/null 2>&1 && telemt_web_active 2>/dev/null \
         && [ "$(config_get mode 2>/dev/null || echo lite)" = "pro" ]; then
        web_on=1
    fi

    cat > "$NGINX_SITE_CONF" << 'EONGINX'
# GoTelegram v2.5.0 — nginx config
# Pro: nginx на 127.0.0.1:8443 (внутренний), telemt на 0.0.0.0:443 (внешний)
# Обычный браузер → :443 → telemt → 127.0.0.1:8443 → nginx (сайт)

server {
    listen 80;
    listen [::]:80;
    server_name DOMAIN_PLACEHOLDER;

    # Let's Encrypt ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    # Редирект на HTTPS
    location / {
        return 301 https://$server_name$request_uri;
    }
}

EONGINX
    if [ "$web_on" = "1" ]; then
        cat >> "$NGINX_SITE_CONF" << 'EONGINX'
# ── WEB-прокси: весь vhost уходит в telemt, TLS терминируем здесь ───────────
# Требование ядра: направлять в WEB-listener весь публичный vhost, а не только
# известные пути, иначе обычные и аутентифицированные запросы ведут себя
# наблюдаемо по-разному и защита от зондирования обходится.
map $http_upgrade $telemt_connection_upgrade {
    default upgrade;
    ''      '';
}

upstream telemt_web {
    server 127.0.0.1:WEB_LISTEN_PLACEHOLDER;
    keepalive 64;
}

server {
    # telemt пробрасывает сюда соединения с PROXY-заголовком (mask_proxy_protocol)
    listen 127.0.0.1:SSL_PORT_PLACEHOLDER ssl http2 proxy_protocol;
    server_name DOMAIN_PLACEHOLDER;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

    # В URI живёт bridge capability, в Authorization — bearer сессии.
    # Логировать их нельзя.
    access_log off;

    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;

    # Заголовки безопасности живут в decoy-блоке и приходят оттуда через ядро;
    # здесь их дублировать нельзя — двойной HSTS выдавал бы прокси-цепочку.
    client_max_body_size 2m;

    location / {
        proxy_pass http://telemt_web;
        proxy_http_version 1.1;
        # Host — статически: с чужим Host (IP-литерал, без SNI) ядро отвечало
        # своим «not found» вместо сайта, и сканер видел не-nginx 404 за nginx.
        proxy_set_header Host DOMAIN_PLACEHOLDER;
        # перезаписываем, а не дополняем: ядро принимает ровно один адрес
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $telemt_connection_upgrade;
        # long poll 25 с и удвоенный интервал WebSocket-пингов — 65 с покрывают
        proxy_connect_timeout 5s;
        proxy_send_timeout 65s;
        proxy_read_timeout 65s;
        proxy_request_buffering off;
        proxy_buffering off;
        # bridge сам повторяет запросы байт в байт; WebSocket повторить нельзя
        proxy_next_upstream off;
    }
}

# Decoy: прежний сайт-маскировка и мини-апп. Сюда telemt отправляет обычные
# запросы; ответы уходят клиенту через тот же HTTPS.
server {
    listen 127.0.0.1:WEB_DECOY_PLACEHOLDER;
    server_name DOMAIN_PLACEHOLDER;
    # адрес клиента приезжает в X-Forwarded-For от публичного блока — иначе
    # панель и лимиты видели бы telemt вместо человека
    set_real_ip_from 127.0.0.1;
    real_ip_header X-Forwarded-For;
    # Сюда ядро отправляет и запросы с живой capability, которые не приняло
    # (неканоничная форма, исчерпан лимит bootstrap) — вместе с query. В логе
    # им не место.
    access_log off;




    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # ── Telegram Mini App (если включён) ─────────────────────────────────────
    # Отдаём панель управления с СОБСТВЕННОГО домена по секретному пути, а не
    # через сторонний туннель: чужой хост живёт своей жизнью и однажды просто
    # перестаёт резолвиться. Путь неугадываемый, но главная защита — проверка
    # подписи Telegram на стороне панели (см. verify_telegram_init_data).
    # X-Frame-Options здесь снимаем: Telegram Web показывает мини-апп в iframe.
    # Модификатор ^~ обязателен: ниже есть regex-локация кеширования статики
    # (\.(js|css|png|…)$), а regex в nginx приоритетнее префиксных — без ^~ она
    # перехватывала /<путь>/app.js и искала файл в корне сайта, отдавая 404.
    # Панель при этом грузилась, но без своего JS, то есть пустой.
    MINIAPP_LOCATION_PLACEHOLDER

    # Корень сайта
    root /var/www/gotelegram-site;
    index index.html;

    # html не кешируем: шаблон подменяется на месте под тем же именем, и при
    # 30-дневном кеше владелец после смены шаблона видел бы прежний сайт.
    location / {
        try_files $uri $uri/ =404;
        expires -1;
    }

    # Кеширование статики: Cache-Control задаём через expires, а не add_header —
    # свой add_header на этом уровне отменил бы наследование пяти заголовков
    # безопасности с уровня server, и на .js/.css/.png они бы не отдавались.
    # Неделя вместо «года с immutable»: файлы шаблонов не версионируются, с
    # immutable вернувшийся посетитель после смены шаблона видел бы старую вёрстку.
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
        expires 7d;
    }

    # Скрываем служебные файлы
    location ~ /\. { deny all; }
    location = /robots.txt { allow all; log_not_found off; access_log off; }
    location = /favicon.ico { log_not_found off; access_log off; }
}
EONGINX
    else
        cat >> "$NGINX_SITE_CONF" << 'EONGINX'
server {
    listen 127.0.0.1:SSL_PORT_PLACEHOLDER ssl http2;
    server_name DOMAIN_PLACEHOLDER;

    # SSL сертификаты
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;

    # Современные TLS настройки
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # OCSP stapling
    ssl_stapling on;
    ssl_stapling_verify on;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # ── Telegram Mini App (если включён) ─────────────────────────────────────
    # Отдаём панель управления с СОБСТВЕННОГО домена по секретному пути, а не
    # через сторонний туннель: чужой хост живёт своей жизнью и однажды просто
    # перестаёт резолвиться. Путь неугадываемый, но главная защита — проверка
    # подписи Telegram на стороне панели (см. verify_telegram_init_data).
    # X-Frame-Options здесь снимаем: Telegram Web показывает мини-апп в iframe.
    # Модификатор ^~ обязателен: ниже есть regex-локация кеширования статики
    # (\.(js|css|png|…)$), а regex в nginx приоритетнее префиксных — без ^~ она
    # перехватывала /<путь>/app.js и искала файл в корне сайта, отдавая 404.
    # Панель при этом грузилась, но без своего JS, то есть пустой.
    MINIAPP_LOCATION_PLACEHOLDER

    # Корень сайта
    root /var/www/gotelegram-site;
    index index.html;

    # html не кешируем: шаблон подменяется на месте под тем же именем, и при
    # 30-дневном кеше владелец после смены шаблона видел бы прежний сайт.
    location / {
        try_files $uri $uri/ =404;
        expires -1;
    }

    # Кеширование статики: Cache-Control задаём через expires, а не add_header —
    # свой add_header на этом уровне отменил бы наследование пяти заголовков
    # безопасности с уровня server, и на .js/.css/.png они бы не отдавались.
    # Неделя вместо «года с immutable»: файлы шаблонов не версионируются, с
    # immutable вернувшийся посетитель после смены шаблона видел бы старую вёрстку.
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
        expires 7d;
    }

    # Скрываем служебные файлы
    location ~ /\. { deny all; }
    location = /robots.txt { allow all; log_not_found off; access_log off; }
    location = /favicon.ico { log_not_found off; access_log off; }
}
EONGINX
    fi

    # Подставляем значения (используем | как разделитель, чтобы / в домене не ломал sed)
    local escaped_domain
    escaped_domain=$(printf '%s\n' "$domain" | sed 's/[&/\]/\\&/g')
    sed -i "s|DOMAIN_PLACEHOLDER|${escaped_domain}|g" "$NGINX_SITE_CONF"
    sed -i "s|SSL_PORT_PLACEHOLDER|${proxy_port}|g" "$NGINX_SITE_CONF"
    sed -i "s|WEB_LISTEN_PLACEHOLDER|${WEB_LISTEN_PORT:-18080}|g; s|WEB_DECOY_PLACEHOLDER|${WEB_DECOY_PORT:-8444}|g" "$NGINX_SITE_CONF"

    # Мини-апп: путь берём из config.json. Пусто = блок не создаётся вовсе,
    # то есть по умолчанию панель наружу НЕ смотрит.
    local _mp _mloc=""
    _mp=$(config_get miniapp_path 2>/dev/null || echo "")
    if [ -n "$_mp" ]; then
        _mloc=$(cat <<EOMINI
location ^~ /${_mp}/ {
        proxy_pass http://127.0.0.1:1984/;
        # Панель держит строгий allowlist Host (защита от DNS-rebinding при
        # прямом обращении). Проксируем под её собственным именем, а реальный
        # домен отдаём отдельным заголовком — иначе она отвечает 403 и мини-апп
        # не загружается вообще.
        proxy_set_header Host 127.0.0.1:1984;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_http_version 1.1;
        proxy_read_timeout 60s;
        # Telegram Web открывает мини-апп в iframe — SAMEORIGIN его убьёт
        proxy_hide_header X-Frame-Options;
        add_header X-Frame-Options "" always;
    }
EOMINI
)
    fi
    python3 - "$NGINX_SITE_CONF" "$_mloc" <<'EOPY' 2>/dev/null || sed -i "s|MINIAPP_LOCATION_PLACEHOLDER||" "$NGINX_SITE_CONF"
import sys
p, loc = sys.argv[1], sys.argv[2]
c = open(p, encoding='utf-8').read().replace('MINIAPP_LOCATION_PLACEHOLDER', loc)
open(p, 'w', encoding='utf-8').write(c)
EOPY

    # Активируем сайт
    rm -f /etc/nginx/sites-enabled/default 2>/dev/null
    ln -sf "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"

    log_success "nginx конфиг создан для $domain"
}

# ── Временный конфиг (до получения SSL) ──────────────────────────────────────
generate_nginx_temp_config() {
    local domain="$1"

    cat > "$NGINX_SITE_CONF" << EONGINX_TEMP
# GoTelegram — временный конфиг (до получения SSL)
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    root /var/www/gotelegram-site;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EONGINX_TEMP

    rm -f /etc/nginx/sites-enabled/default 2>/dev/null
    ln -sf "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"
    mkdir -p /var/www/certbot
}

# ── Получение SSL сертификата ────────────────────────────────────────────────
obtain_ssl_certificate() {
    local domain="$1"
    local email="${2:-}"

    if [ ! -d "/etc/letsencrypt/live/$domain" ]; then
        log_info "Получение SSL сертификата для $domain..."

        # Временный конфиг для ACME challenge
        generate_nginx_temp_config "$domain"
        systemctl restart nginx 2>/dev/null

        local certbot_args=(
            certonly
            --webroot
            -w /var/www/certbot
            -d "$domain"
            --non-interactive
            --agree-tos
        )

        if [ -n "$email" ]; then
            certbot_args+=(--email "$email")
        else
            certbot_args+=(--register-unsafely-without-email)
        fi

        if certbot "${certbot_args[@]}" 2>/dev/null; then
            log_success "SSL сертификат получен для $domain"
            return 0
        else
            log_error "Не удалось получить SSL сертификат"
            log_dim "Убедитесь что домен $domain направлен на IP этого сервера"
            log_dim "и порт 80 открыт в файрволе."
            return 1
        fi
    else
        log_dim "SSL сертификат уже существует для $domain"
        return 0
    fi
}

# ── Авто-обновление сертификата ──────────────────────────────────────────────
setup_ssl_auto_renewal() {
    # Certbot systemd timer (предпочтительно)
    if [ -f /etc/systemd/system/certbot.timer ] || [ -f /lib/systemd/system/certbot.timer ]; then
        systemctl enable certbot.timer 2>/dev/null
        systemctl start certbot.timer 2>/dev/null
        log_success "Авто-обновление SSL через systemd timer"
        return 0
    fi

    # Fallback: cron
    if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
        (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --post-hook 'systemctl reload nginx'") | crontab -
        log_success "Авто-обновление SSL через cron (3:00 ежедневно)"
    fi
}

# ── Обновление сертификата вручную ───────────────────────────────────────────
renew_ssl_certificate() {
    log_info "Обновление SSL сертификата..."
    if certbot renew --quiet --post-hook "systemctl reload nginx" 2>/dev/null; then
        log_success "Сертификат обновлён"
        return 0
    else
        log_error "Ошибка обновления сертификата"
        return 1
    fi
}

# ── Дата истечения SSL ───────────────────────────────────────────────────────
get_ssl_expiry() {
    local domain="$1"
    local cert="/etc/letsencrypt/live/$domain/fullchain.pem"
    if [ -f "$cert" ]; then
        openssl x509 -enddate -noout -in "$cert" 2>/dev/null | sed 's/notAfter=//'
    else
        echo "N/A"
    fi
}

# Служебные файлы шаблона в корне сайта — след для маскировки: у большинства
# репозиториев https://домен/Readme.txt отдаёт «Template Name: … / Author: …»,
# package.json и .github выдают происхождение. Сайту они не нужны — убираем
# после копирования (в корне и на один уровень ниже).
strip_template_service_files() {
    local root="$1"
    [ -d "$root" ] || return 0
    local patterns=(README readme Readme LICENSE license License LICENCE CHANGELOG changelog CONTRIBUTING SECURITY CODE_OF_CONDUCT
                    package.json package-lock.json yarn.lock pnpm-lock.yaml composer.json composer.lock
                    gulpfile.js gulpfile.babel.js Gruntfile.js webpack.config.js webpack.mix.js vite.config.js tailwind.config.js postcss.config.js
                    .gitignore .gitattributes .editorconfig .eslintrc .eslintrc.json .prettierrc .stylelintrc .travis.yml .nojekyll .htaccess .DS_Store Thumbs.db)
    local pat
    for pat in "${patterns[@]}"; do
        find "$root" -maxdepth 2 \( -iname "$pat" -o -iname "$pat.*" \) -type f -delete 2>/dev/null || true
    done
    find "$root" -maxdepth 2 -type f -iname '*.md' -delete 2>/dev/null || true
    local dir
    for dir in .git .github .sass-cache node_modules .vscode .idea; do
        rm -rf "$root/$dir" 2>/dev/null || true
    done
}

# ── Деплой шаблона сайта ─────────────────────────────────────────────────────
deploy_template_to_nginx() {
    local template_dir="$1"
    local template_id="${2:-}"
    local source_url=""

    if [ ! -d "$template_dir" ] || [ ! -f "$template_dir/index.html" ]; then
        log_error "Шаблон не содержит index.html: $template_dir"
        return 1
    fi
    [ -z "$template_id" ] && template_id=$(basename "$template_dir")
    [ -f "$template_dir/.custom_git_source" ] && source_url=$(head -1 "$template_dir/.custom_git_source" 2>/dev/null || echo "")

    # Бекапим старый сайт. Путь — через mktemp -d: предсказуемый /tmp/site_backup_<дата>
    # позволял локальному юзеру заранее подложить симлинк и увести туда каталог сайта
    # (тот же класс, что и остальные /tmp-пути в модулях).
    if [ -d "$WEBSITE_ROOT" ] && [ "$(ls -A "$WEBSITE_ROOT" 2>/dev/null)" ]; then
        local site_bk_dir
        site_bk_dir=$(mktemp -d "${TMPDIR:-/tmp}/site_backup_$(date +%Y%m%d_%H%M%S).XXXXXX" 2>/dev/null)
        if [ -n "$site_bk_dir" ]; then
            # mktemp -d уже создал каталог — переносим содержимое, а не сам каталог
            cp -a "$WEBSITE_ROOT/." "$site_bk_dir/" 2>/dev/null && rm -rf "$WEBSITE_ROOT"
            log_dim "Старый сайт сохранён в $site_bk_dir"
        fi
    fi

    mkdir -p "$WEBSITE_ROOT"
    cp -a "$template_dir/." "$WEBSITE_ROOT/"
    rm -f "$WEBSITE_ROOT/.custom_git_source" 2>/dev/null || true
    strip_template_service_files "$WEBSITE_ROOT"
    echo "$template_id" > "$WEBSITE_ROOT/.gotelegram_template_id" 2>/dev/null || true
    [ -n "$source_url" ] && echo "$source_url" > "$WEBSITE_ROOT/.gotelegram_template_source" 2>/dev/null || true
    chown -R www-data:www-data "$WEBSITE_ROOT" 2>/dev/null || chown -R nginx:nginx "$WEBSITE_ROOT" 2>/dev/null
    chmod -R 755 "$WEBSITE_ROOT"

    log_success "Шаблон развёрнут в $WEBSITE_ROOT"
}

# ── Полная установка pro-режима ──────────────────────────────────────────────
setup_pro_mode() {
    local domain="$1"
    local template_dir="$2"
    local proxy_port="${3:-443}"
    local email="${4:-}"

    log_step "Настройка pro-режима"

    # 1. Устанавливаем nginx
    run_with_spinner "Установка nginx" install_nginx || return 1

    # 2. Устанавливаем certbot
    run_with_spinner "Установка certbot" install_certbot || return 1

    # 3. Деплоим шаблон сайта
    deploy_template_to_nginx "$template_dir" || return 1

    # 4. Получаем SSL
    obtain_ssl_certificate "$domain" "$email" || return 1

    # 5. Генерируем полный nginx конфиг с SSL
    generate_nginx_config "$domain" "$proxy_port"

    # 6. Тестируем и перезапускаем nginx
    if nginx -t 2>/dev/null; then
        systemctl restart nginx
        log_success "nginx запущен с SSL"
    else
        log_error "Ошибка в конфигурации nginx"
        nginx -t
        return 1
    fi

    # 7. Настраиваем авто-обновление SSL
    setup_ssl_auto_renewal

    # 8. Показываем благодарности авторам шаблонов
    show_credits

    log_success "Pro-режим настроен: https://${domain}"
    return 0
}

# ── Управление nginx ────────────────────────────────────────────────────────
nginx_status() {
    if systemctl is-active --quiet nginx 2>/dev/null; then
        echo "running"
    else
        echo "stopped"
    fi
}

restart_nginx() {
    if nginx -t 2>/dev/null; then
        systemctl restart nginx 2>/dev/null
        log_success "nginx перезапущен"
    else
        log_error "Ошибка конфигурации nginx"
        nginx -t
        return 1
    fi
}

# ── Удаление pro-режима ──────────────────────────────────────────────────────
remove_pro_mode() {
    log_info "Удаление pro-режима..."
    rm -f "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"
    rm -rf "$WEBSITE_ROOT"
    systemctl restart nginx 2>/dev/null
    log_success "Pro-режим удалён (nginx оставлен)"
}

# ── Смена шаблона ────────────────────────────────────────────────────────────
switch_template() {
    local new_template_dir="$1"
    deploy_template_to_nginx "$new_template_dir" "$(basename "$new_template_dir")"
    # nginx не требует перезапуска — статика обновилась на месте
    log_success "Шаблон сайта обновлён"
}
