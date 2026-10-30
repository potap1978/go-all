#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# Выпуск СВОЕГО сертификата, когда по известным путям ничего не нашлось.
#
# ─────────────────────────────────────────────────────────────────────────
# ГЛАВНЫЙ ИНВАРИАНТ: чужой сертификат не перезаписывается никогда.
#
# Здесь выпускается ТОЛЬКО новый lineage для домена, у которого его ещё нет.
# Если /etc/letsencrypt/live/<домен>/ или renewal/<домен>.conf уже существуют —
# выпуск отменяется, и оператору предлагается взять существующий. Причина:
# повторный `certbot certonly` для занятого домена перепишет renewal-конфиг
# соседа (webroot, плагин, хуки), и его автопродление тихо сломается.
# ─────────────────────────────────────────────────────────────────────────

# ── Есть ли чем выпускать ───────────────────────────────────────────────
certs_have_certbot() { command -v certbot >/dev/null 2>&1; }

certs_install_certbot() {
    if certs_have_certbot; then
        log_dim "$(t issue_certbot_present)"
        return 0
    fi
    # ГРАБЛИ: здесь тоже был молчаливый apt в /dev/null. Установка dnsdist
    # получила индикатор ещё раньше, а установка certbot осталась немой — и
    # оператор снова упёрся в «зависло» ровно на этом шаге. Долгая операция
    # без признаков жизни — всегда баг, в каком бы месте она ни была.
    wait_for_cloud_init
    apt_wait_ready || return 1
    run_with_progress "$(t issue_certbot_updating)" apt_q update || true
    if run_with_progress "$(t issue_certbot_installing)" \
            apt_q install --no-install-recommends certbot; then
        log_success "$(t issue_certbot_ok)"
        return 0
    fi
    log_error "$(t issue_certbot_failed)"
    return 1
}

# ── Предложить домен ────────────────────────────────────────────────────
# Естественный выбор — поддомен соседа: dns.example.com. Так мы не трогаем
# lineage соседа вообще и получаем отдельный сертификат под свой сервис.
certs_suggest_domain() {
    local base="" d=""
    d=$(json_get "$GOVLESS_CONFIG_PATH" domain "") || d=""
    if [ -n "$d" ] && [ "$d" != "null" ]; then base="$d"; fi
    if [ -z "$base" ]; then
        d=$(json_get "$GOTELEGRAM_CONFIG_PATH" domain "") || d=""
        if [ -n "$d" ] && [ "$d" != "null" ]; then base="$d"; fi
    fi
    if [ -n "$base" ]; then
        printf 'dns.%s' "$base"
        return 0
    fi
    printf ''
    return 0
}

# ── Занят ли домен чужим сертификатом ───────────────────────────────────
certs_lineage_exists() {
    local d="$1"
    [ -d "${LE_ROOT}/live/${d}" ] && return 0
    [ -f "${LE_ROOT}/renewal/${d}.conf" ] && return 0
    return 1
}

# ── Резолвится ли домен в этот сервер ───────────────────────────────────
# Без этой проверки Let's Encrypt отдаст отказ, а мы сожжём лимит
# «5 неудачных проверок на домен в час».
certs_dns_points_here() {
    local d="$1" want="$2" got=""
    if command -v dig >/dev/null 2>&1; then
        got=$(dig +short +time=3 +tries=2 A "$d" 2>/dev/null | grep -E '^[0-9.]+$' | head -1) || got=""
    fi
    if [ -z "$got" ]; then
        got=$(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1; exit}') || got=""
    fi
    GODNS_DNS_SEEN="$got"
    if [ -z "$got" ]; then return 2; fi
    if [ "$got" = "$want" ]; then return 0; fi
    return 1
}

# ── Как проходить проверку ACME ─────────────────────────────────────────
# Возвращает через глобальные: ISSUE_METHOD=standalone|webroot,
# ISSUE_WEBROOT=<путь>, ISSUE_PORT80_OWNER=<кто>.
certs_pick_challenge() {
    local domain="$1"
    ISSUE_METHOD=""; ISSUE_WEBROOT=""; ISSUE_PORT80_OWNER=""

    if ! port_in_use 80; then
        ISSUE_METHOD="standalone"
        log_dim "$(t issue_ch_standalone)"
        return 0
    fi

    ISSUE_PORT80_OWNER=$(port_owner 80) || ISSUE_PORT80_OWNER=""
    log_dim "$(tf issue_ch_port80_busy "${ISSUE_PORT80_OWNER:-$(t ports_unknown_owner)}")"

    # nginx на :80 — не останавливаем его (это сайт соседа), а доставляем
    # проверочный файл через webroot и ПРОВЕРЯЕМ, что он реально отдаётся.
    case "$ISSUE_PORT80_OWNER" in
        *nginx*|*apache*|*httpd*)
            local cand
            # Сначала СПРАШИВАЕМ сам nginx, потом уже гадаем. Четыре
            # захардкоженных пути покрывают типовую установку, но не сайт
            # соседа: у goTelegram корень свой, проба его не находила, и мы
            # сваливались в остановку nginx — а это оставляет автопродление
            # сломанным (см. ниже). `nginx -T` только ЧИТАЕТ конфигурацию.
            for cand in $(certs_nginx_roots) \
                        /var/www/certbot /var/www/html /usr/share/nginx/html /var/www; do
                [ -d "$cand" ] || continue
                if certs_probe_webroot "$domain" "$cand"; then
                    ISSUE_METHOD="webroot"; ISSUE_WEBROOT="$cand"
                    log_success "$(tf issue_ch_webroot_ok "$cand")"
                    return 0
                fi
            done
            log_warning "$(t issue_ch_webroot_failed)"
            ;;
    esac

    # Ничего не вышло: предложить кратко остановить владельца :80.
    if [ -n "$ISSUE_PORT80_OWNER" ]; then
        log_warning "$(tf issue_ch_stop_ask "$ISSUE_PORT80_OWNER")"
        if confirm "$(t issue_ch_stop_confirm)" n; then
            ISSUE_METHOD="standalone-stop"
            return 0
        fi
    fi
    log_error "$(t issue_ch_none)"
    return 1
}

# Корни документов, которые ДЕЙСТВИТЕЛЬНО настроены у работающего nginx.
# Только чтение: `nginx -T` печатает действующую конфигурацию целиком и
# ничего не меняет. Дубликаты убираем, порядок сохраняем — первым идёт то,
# что nginx назвал раньше.
certs_nginx_roots() {
    command -v nginx >/dev/null 2>&1 || return 0
    local dump="" seen=" " r=""
    dump=$(nginx -T 2>/dev/null) || return 0
    [ -n "$dump" ] || return 0
    while IFS= read -r r; do
        [ -n "$r" ] || continue
        case "$r" in /*) ;; *) continue ;; esac
        case "$seen" in *" $r "*) continue ;; esac
        seen="${seen}${r} "
        printf '%s\n' "$r"
    done < <(printf '%s\n' "$dump" \
             | sed -n -E 's/^[[:space:]]*(root|alias)[[:space:]]+([^;]+);.*$/\2/p' \
             | tr -d '"'"'"'"')
    return 0
}

# Кладём файл в webroot и забираем его снаружи по HTTP. Это доказательство,
# а не догадка: ровно тем же путём пойдёт Let's Encrypt.
certs_probe_webroot() {
    local domain="$1" root="$2"
    local dir="${root}/.well-known/acme-challenge"
    local token body got rc=1
    token="godns-probe-$(random_path 16)"
    body="godns-${token}"

    mkdir -p "$dir" 2>/dev/null || return 1
    printf '%s' "$body" > "${dir}/${token}" 2>/dev/null || return 1
    chmod 644 "${dir}/${token}" 2>/dev/null || true

    got=$(curl -fsS --max-time 8 --resolve "${domain}:80:127.0.0.1" \
              "http://${domain}/.well-known/acme-challenge/${token}" 2>/dev/null) || got=""
    if [ "$got" != "$body" ]; then
        got=$(curl -fsS --max-time 8 \
                  "http://${domain}/.well-known/acme-challenge/${token}" 2>/dev/null) || got=""
    fi
    [ "$got" = "$body" ] && rc=0

    rm -f "${dir}/${token}" 2>/dev/null || true
    return $rc
}

# ── Сборка аргументов certbot ───────────────────────────────────────────
_certs_certbot_args() {
    local domain="$1" email="$2"
    CERTBOT_ARGS=(certonly --non-interactive --agree-tos --no-eff-email -d "$domain")
    case "$ISSUE_METHOD" in
        webroot)          CERTBOT_ARGS+=(--webroot -w "$ISSUE_WEBROOT") ;;
        standalone)       CERTBOT_ARGS+=(--standalone) ;;
        standalone-stop)  CERTBOT_ARGS+=(--standalone) ;;
        *) return 1 ;;
    esac
    if [ -n "$email" ]; then
        CERTBOT_ARGS+=(-m "$email")
    else
        CERTBOT_ARGS+=(--register-unsafely-without-email)
    fi
    return 0
}

_certs_with_port80_free() {
    # Выполняет "$@", при необходимости остановив владельца :80 и вернув его
    # на место в любом случае — даже если certbot упал.
    local svc="" rc=0
    if [ "$ISSUE_METHOD" = "standalone-stop" ]; then
        case "$ISSUE_PORT80_OWNER" in
            *nginx*)  svc="nginx" ;;
            *apache*) svc="apache2" ;;
            *httpd*)  svc="httpd" ;;
        esac
    fi
    if [ -n "$svc" ]; then
        log_info "$(tf issue_stopping "$svc")"
        systemctl stop "$svc" >/dev/null 2>&1 || true
    fi
    "$@" || rc=$?
    if [ -n "$svc" ]; then
        systemctl start "$svc" >/dev/null 2>&1 || log_warning "$(tf issue_restart_failed "$svc")"
    fi
    return $rc
}

# ── Выпуск ──────────────────────────────────────────────────────────────
certs_issue_interactive() {
    print_header "$(t issue_title)"
    # Вступление зависит от того, ЕСТЬ ли уже сертификат: «по известным
    # путям нет» врёт на машине, где он есть и работает (дашборд двумя
    # строками выше показывает срок). certs_lineage_exists — только чтение.
    #
    # ГРАБЛИ, стоившие всего сценария первичной установки: здесь стояло
    # `certs_lineage_exists "$domain"`, а local domain объявляется на
    # пятнадцать строк НИЖЕ. Под `set -u` это фатальная ошибка раскрытия —
    # её не гасит ни run_action, ни условный контекст: терминал оператора
    # закрывался сразу после заголовка. Ломались все три пути к функции,
    # включая шаг 1 мастера на машине без сертификатов.
    #
    # Берём домен из конфига: на этом экране своего ещё нет, а сравнивать
    # надо именно с уже настроенным.
    local cur_domain=""
    cur_domain=$(config_get domain "") || cur_domain=""
    if [ -n "$cur_domain" ] && certs_lineage_exists "$cur_domain" 2>/dev/null; then
        echo -e "  ${DIM}$(t issue_intro_have)${NC}"
    else
        echo -e "  ${DIM}$(t issue_intro)${NC}"
    fi
    echo ""

    local ip; ip=$(get_server_ip) || ip=""
    if [ -z "$ip" ]; then
        log_error "$(t issue_no_ip)"
        return 1
    fi
    log_dim "$(tf issue_server_ip "$ip")"

    # ── домен ────────────────────────────────────────────────────────────
    local suggested domain=""
    suggested=$(certs_suggest_domain) || suggested=""
    while true; do
        if [ -n "$suggested" ]; then
            echo -ne "  $(tf issue_ask_domain_sug "$suggested") " >&2
        else
            echo -ne "  $(t issue_ask_domain) " >&2
        fi
        read -r domain || { echo ""; return 1; }
        domain=$(printf '%s' "$domain" | tr -d '[:space:]' | tr 'A-Z' 'a-z')
        [ -z "$domain" ] && domain="$suggested"
        [ -z "$domain" ] && { log_warning "$(t issue_domain_required)"; continue; }
        if ! valid_domain "$domain"; then
            log_warning "$(tf issue_domain_bad "$domain")"
            continue
        fi
        # Инвариант: занятый домен не трогаем.
        if certs_lineage_exists "$domain"; then
            log_error "$(tf issue_lineage_exists "$domain")"
            log_dim "$(t issue_lineage_hint)"
            confirm "$(t issue_try_other_domain)" y || return 1
            continue
        fi
        break
    done

    # ── DNS ──────────────────────────────────────────────────────────────
    GODNS_DNS_SEEN=""
    local rc=0
    while true; do
        certs_dns_points_here "$domain" "$ip" || rc=$?
        case "$rc" in
            0) log_success "$(tf issue_dns_ok "$domain" "$ip")"; break ;;
            2) log_warning "$(tf issue_dns_none "$domain")" ;;
            *) log_warning "$(tf issue_dns_mismatch "$domain" "${GODNS_DNS_SEEN:-?}" "$ip")" ;;
        esac
        echo -e "  ${DIM}$(tf issue_dns_hint "$domain" "$ip")${NC}"
        echo ""
        echo -e "  ${CYAN}1)${NC} $(t issue_dns_recheck)"
        echo -e "  ${CYAN}2)${NC} $(t issue_dns_ignore)"
        echo -e "  ${CYAN}0)${NC} $(t issue_dns_abort)"
        local c=""
        echo -ne "  ▸ " >&2
        read -r c || { echo ""; return 1; }
        case "$c" in
            1) rc=0; sleep 3; continue ;;
            2) log_warning "$(t issue_dns_ignored)"; break ;;
            *) return 1 ;;
        esac
    done

    # ── почта ────────────────────────────────────────────────────────────
    local email=""
    echo -ne "  $(t issue_ask_email) " >&2
    # Ctrl-D здесь — отмена: следом идёт настоящий выпуск сертификата,
    # и проходить его «по умолчанию» от жеста выхода нельзя.
    godns_read_line || { echo "" >&2; return 1; }
    email="$GODNS_REPLY"
    email=$(printf '%s' "$email" | tr -d '[:space:]')
    if [ -n "$email" ] && ! printf '%s' "$email" | grep -qE '^[^@[:space:]]+@[^@[:space:]]+\.[a-zA-Z]{2,}$'; then
        log_warning "$(t issue_email_bad)"
        email=""
    fi
    [ -z "$email" ] && log_dim "$(t issue_email_none)"

    # ── подготовка ───────────────────────────────────────────────────────
    certs_install_certbot || return 1
    certs_pick_challenge "$domain" || return 1
    _certs_certbot_args "$domain" "$email" || return 1

    # ── пробный прогон ───────────────────────────────────────────────────
    # Дешевле, чем сжечь лимит Let's Encrypt: 5 неудачных проверок на домен
    # в час, 50 сертификатов на зарегистрированный домен в неделю.
    log_step "$(t issue_dryrun)"
    # Лог живёт в /opt/godns, а не во временном файле: при провале мы
    # показываем оператору путь к нему, и файл обязан пережить выход из
    # программы (временные файлы godns.* убираются при выходе). Каталог
    # 0700 root — симлинк-атаки из /tmp сюда не достают.
    local log=""
    mkdir -p "$GODNS_DIR" 2>/dev/null || true
    log="${GODNS_DIR}/certbot-issue.log"
    if ! : > "$log" 2>/dev/null; then
        log=$(godns_mktemp) || log="/tmp/godns-certbot.log"
    fi
    # Без внешнего редиректа: он забирал спиннер (экран молчал десятки
    # секунд) и НЕ забирал вывод certbot. Путь передаём внутрь.
    if ! GODNS_PROGRESS_LOG="$log" run_with_progress "$(t issue_dryrun)" \
            _certs_with_port80_free certbot "${CERTBOT_ARGS[@]}" --dry-run; then
        log_error "$(t issue_dryrun_failed)"
        echo "" >&2
        tail -n 20 "$log" >&2 || true
        echo "" >&2
        log_dim "$(tf issue_log_at "$log")"
        return 1
    fi
    log_success "$(t issue_dryrun_ok)"

    # ── настоящий выпуск ─────────────────────────────────────────────────
    log_step "$(tf issue_issuing "$domain")"
    if ! GODNS_PROGRESS_LOG="$log" run_with_progress "$(tf issue_issuing "$domain")" \
            _certs_with_port80_free certbot "${CERTBOT_ARGS[@]}"; then
        log_error "$(t issue_failed)"
        tail -n 20 "$log" >&2 || true
        log_dim "$(tf issue_log_at "$log")"
        return 1
    fi

    local cert="${LE_ROOT}/live/${domain}/fullchain.pem"
    local key="${LE_ROOT}/live/${domain}/privkey.pem"
    if [ ! -f "$cert" ] || [ ! -f "$key" ]; then
        log_error "$(tf issue_no_files "$cert")"
        return 1
    fi
    log_success "$(tf issue_ok "$domain")"

    # ── продление: проверяем СРАЗУ, а не через 60 дней ───────────────────
    # ГРАБЛИ: выпуск через standalone требует свободного :80, и мы его
    # освобождаем, останавливая владельца на время выпуска. Но продление
    # пойдёт БЕЗ нас — и упрётся в занятый порт. Пользователь узнавал об
    # этом, сам запустив `certbot renew --dry-run` после установки; на
    # сервере без такого пользователя сертификат просто перестал бы
    # обновляться молча, через два месяца.
    #
    # Прогон ограничен НАШИМ lineage (--cert-name): общий `certbot renew`
    # затронул бы и сертификаты соседа, включая его хуки.
    if [ "$ISSUE_METHOD" = "standalone-stop" ]; then
        # С пределом терпения. `certbot --dry-run` умеет висеть минутами
        # (замерено: 248 с и продолжал), а это ПРОВЕРКА, а не сам выпуск —
        # подвесить из-за неё установку нельзя. Не уложился — говорим то же
        # самое, что и при провале: продление надо проверить руками.
        if GODNS_PROGRESS_LOG="$log" run_with_progress "$(t issue_renew_check)" \
                timeout 180 certbot renew --cert-name "$domain" --dry-run; then
            log_success "$(t issue_renew_ok)"
        else
            log_warning "$(tf issue_renew_broken "${ISSUE_PORT80_OWNER:-:80}")"
            log_dim "$(t issue_renew_broken_why)"
            log_dim "$(t issue_renew_broken_fix)"
            log_dim "$(tf issue_log_at "$log")"
        fi
    fi

    # ── автопродление ────────────────────────────────────────────────────
    certs_enable_autorenew "$domain" || log_warning "$(t issue_autorenew_warn)"

    # ── записать в конфиг ────────────────────────────────────────────────
    config_set domain "$domain"
    config_set cert_path "$cert"
    config_set key_path "$key"
    config_set cert_source "goDNS Pro (Let's Encrypt)"
    config_set cert_owned_by_godns "yes"

    echo ""
    echo -e "  $(t plan_domain):  ${WHITE}${domain}${NC}"
    kv_row "$(t cert_file)" "${DIM}${cert}${NC}"                    14
    kv_row "$(t cert_days)" "${WHITE}$(certs_days_left "$cert")${NC}" 14
    return 0
}

# ── Автопродление ───────────────────────────────────────────────────────
# certbot из пакета сам ставит systemd-таймер. Наша задача — убедиться, что
# он включён, и добавить СВОЙ deploy-хук. Каталог renewal-hooks/deploy чисто
# аддитивный: certbot выполняет все файлы в нём, поэтому хуки goVLESS и
# goTelegram остаются нетронутыми.
certs_enable_autorenew() {
    local domain="$1" ok=1

    if systemctl list-unit-files certbot.timer >/dev/null 2>&1; then
        systemctl enable --now certbot.timer >/dev/null 2>&1 && ok=0
        if [ "$ok" -eq 0 ]; then
            log_success "$(t issue_timer_ok)"
        else
            log_warning "$(t issue_timer_failed)"
        fi
    else
        log_warning "$(t issue_timer_absent)"
    fi

    # Запасной путь: если таймера нет, ставим cron один раз и идемпотентно.
    #
    # Маркер «# goDNS» в хвосте строки ОБЯЗАТЕЛЕН: удаление при uninstall
    # фильтрует крон-таблицу ровно по нему. Без маркера «наша» строка
    # неотличима от строки соседа или оператора («certbot renew --quiet
    # --deploy-hook …»), и удаление goDNS тихо снесло бы ЧУЖОЕ автопродление
    # — поломка всплыла бы при следующем перевыпуске, до 60 дней спустя.
    if [ "$ok" -ne 0 ] && command -v crontab >/dev/null 2>&1; then
        local line="17 3 * * * certbot renew --quiet # goDNS"
        if ! crontab -l 2>/dev/null | grep -qF "certbot renew"; then
            if { crontab -l 2>/dev/null || true; echo "$line"; } | crontab - 2>/dev/null; then
                log_success "$(t issue_cron_ok)"
                config_set cron_added_by_godns "yes" || true
            fi
        fi
    fi

    certs_install_renewal_hook "$domain" || return 1

    # Проверяем, что продление вообще способно отработать.
    # ГРАБЛИ: certbot при НЕинтерактивном запуске сам засыпает на случайное
    # время до десяти минут, чтобы не бить залпом по серверам Let's Encrypt.
    # Мы запускаем его без терминала, поэтому он считает себя таковым — и
    # проверка, занимающая пять секунд работы, растягивалась на минуты
    # чистого сна. В логе это видно как «random delay of 263 seconds».
    # Для разовой проверки по команде оператора разброс не нужен: залпа нет.
    if run_with_progress "$(t issue_renew_check)" \
            certbot renew --dry-run --no-random-sleep-on-renew --cert-name "$domain"; then
        log_success "$(t issue_renew_dryrun_ok)"
    else
        log_warning "$(t issue_renew_dryrun_warn)"
    fi
    return 0
}
