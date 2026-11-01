#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# goTelegram Pro v2.10.60 — MTProxy powered by telemt (Rust + Tokio)
# Anti-DPI • Fake TLS • TCP Splice • JA3/JA4 Resistance • i18n (EN/RU)
#
# Install:
#   curl -sL URL/install.sh | sudo bash
# ══════════════════════════════════════════════════════════════════════════════

set -uo pipefail

# PATH задаём САМИ, а не полагаемся на вызывающего. Скрипт работает от root и
# зовёт nft, iptables, ip — а они лежат в /usr/sbin. У systemd-служб PATH свой:
# юнит бота задавал `venv/bin:/usr/bin`, где sbin нет вовсе, и из бота молча
# ломалось всё, что трогает файрвол: смена анти-DPI отвечала «не удалось
# включить v3», а проверка активности не находила nft и рапортовала «фикс
# выключен» — интерфейс показывал MEKO style поверх работающего V3.
# Из SSH те же действия проходили, потому что у входа в систему PATH полный.
for _d in /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin; do
    case ":${PATH}:" in *":${_d}:"*) ;; *) PATH="${PATH}:${_d}" ;; esac
done
export PATH
unset _d

MIGRATED_FROM="${MIGRATED_FROM:-}"   # init для set -u (выставляется в миграции)
REANIM_PRESET_CHANGED=0   # оффер при апгрейде сменил пресет -> форсить регенерацию конфига
TELEMT_VERSION_PREF="${TELEMT_VERSION_PREF:-}"   # выбор версии ядра на время установки

# Script path and libraries
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"
# Защитный fallback: если скрипт запущен как одиночный файл (например, копия в
# /usr/local/bin без папки lib рядом — нестандартная установка), берём библиотеки
# и ресурсы из стандартной директории установки /opt/gotelegram.
if [ ! -f "$LIB_DIR/common.sh" ] && [ -f "/opt/gotelegram/lib/common.sh" ]; then
    SCRIPT_DIR="/opt/gotelegram"
    LIB_DIR="$SCRIPT_DIR/lib"
fi

# Load libraries
source "$LIB_DIR/common.sh"
source "$LIB_DIR/i18n.sh"
source "$LIB_DIR/telemt.sh"
source "$LIB_DIR/telemt_config.sh"
source "$LIB_DIR/website.sh"
source "$LIB_DIR/templates_catalog.sh"
source "$LIB_DIR/backup.sh"
[ -f "$LIB_DIR/stats.sh" ] && source "$LIB_DIR/stats.sh"
[ -f "$LIB_DIR/shared443.sh" ] && source "$LIB_DIR/shared443.sh"
[ -f "$LIB_DIR/edgeips.sh" ] && source "$LIB_DIR/edgeips.sh"
[ -f "$LIB_DIR/diagnose.sh" ] && source "$LIB_DIR/diagnose.sh"
[ -f "$LIB_DIR/warp_connector.sh" ] && source "$LIB_DIR/warp_connector.sh"
[ -f "$LIB_DIR/warp_egress.sh" ] && source "$LIB_DIR/warp_egress.sh"
[ -f "$LIB_DIR/metrics.sh" ] && source "$LIB_DIR/metrics.sh"
[ -f "$LIB_DIR/deps.sh" ] && source "$LIB_DIR/deps.sh"
[ -f "$LIB_DIR/mekofix.sh" ] && source "$LIB_DIR/mekofix.sh"
[ -f "$LIB_DIR/mekofix_v4.sh" ] && source "$LIB_DIR/mekofix_v4.sh"
[ -f "$LIB_DIR/users.sh" ] && source "$LIB_DIR/users.sh"
[ -f "$LIB_DIR/selftest.sh" ] && source "$LIB_DIR/selftest.sh"
[ -f "$LIB_DIR/logbudget.sh" ] && source "$LIB_DIR/logbudget.sh"

# Load language (from config.json or marker file, default en)
load_language "$(detect_language)"

# ── Главное меню (Compact Dashboard + 5 Top-Level Items) ──────────────────────
show_main_menu() {
    local proxy_status bot_status nginx_st mode domain secret port ip link ssl_expiry
    proxy_status=$(telemt_status)
    bot_status=$(bot_service_status)
    nginx_st=$(nginx_status 2>/dev/null || echo "stopped")
    mode=$(config_get mode 2>/dev/null || echo "—")
    domain=$(config_get domain 2>/dev/null || echo "")
    secret=$(get_config_value secret 2>/dev/null || echo "")
    port=$(get_config_value port 2>/dev/null || echo "443")
    # get_server_ip при отказе печатает 0.0.0.0: «|| echo» внутри $(…) давал
    # две строки, «0.0.0.0» и «N/A». Подставляем N/A вместо, а не вслед.
    # Пауза после отказа (300 с) — только здесь: за NAT без ответа сервисов
    # иначе каждая перерисовка ждала бы 3 с (см. get_server_ip).
    ip=$(SERVER_IP_FAIL_BACKOFF=300 get_server_ip 2>/dev/null) || ip="N/A"

    local W=54
    local line; line=$(printf '━%.0s' $(seq 1 $W))
    local line2; line2=$(printf '─%.0s' $(seq 1 $W))

    # ── Header (no right border — ANSI breaks alignment) ──
    echo ""
    echo -e "  ${BOLD}${CYAN}━${line}━${NC}"
    echo -e "  ${BOLD}${WHITE} goTelegram Pro v${GOTELEGRAM_VERSION}${NC}  ${DIM}— $(t dashboard_title)${NC}"
    echo -e "  ${BOLD}${CYAN}━${line}━${NC}"

    # ── Service health ──
    echo ""
    echo -e "  ${DIM}${line2}${NC}"

    # Proxy
    local proxy_icon proxy_color
    case "$proxy_status" in
        running) proxy_icon="●"; proxy_color="${GREEN}" ;;
        stopped) proxy_icon="○"; proxy_color="${YELLOW}" ;;
        *)       proxy_icon="✗"; proxy_color="${RED}" ;;
    esac
    echo -e "  ${proxy_color}${proxy_icon}${NC} $(t svc_proxy)    ${proxy_color}${proxy_status}${NC}    ${DIM}(telemt ${mode})${NC}"

    # nginx
    local nginx_icon nginx_color
    case "$nginx_st" in
        running) nginx_icon="●"; nginx_color="${GREEN}" ;;
        *)       nginx_icon="✗"; nginx_color="${RED}" ;;
    esac
    echo -e "  ${nginx_icon}${nginx_color}${NC} $(t svc_nginx)     ${nginx_color}${nginx_st}${NC}    ${DIM}(127.0.0.1:8443)${NC}"


    # Site (pro)
    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
        local site_icon site_color
        if site_local_ok "$domain"; then
            site_icon="●"; site_color="${GREEN}"
        else
            site_icon="✗"; site_color="${RED}"
        fi
        echo -e "  ${site_color}${site_icon}${NC} $(t svc_site)      ${site_color}https://${domain}${NC}"

        ssl_expiry=$(get_ssl_expiry "$domain" 2>/dev/null || echo "N/A")
        echo -e "  ${GREEN}●${NC} $(t svc_ssl)       ${DIM}$(tf ssl_until "$ssl_expiry")${NC}"
    fi

    # Bot
    case "$bot_status" in
        running) echo -e "  ${GREEN}●${NC} $(t svc_bot)       ${GREEN}$(t running)${NC}" ;;
        stopped) echo -e "  ${YELLOW}○${NC} $(t svc_bot)       ${YELLOW}$(t stopped)${NC}" ;;
    esac

    echo -e "  ${DIM}${line2}${NC}"

    # ── Network parameters ──
    echo -e "  ${WHITE}$(t net_ip)${NC}    ${CYAN}${ip}${NC}    ${WHITE}$(t net_port)${NC} ${CYAN}${port}${NC}    ${WHITE}$(t net_mode)${NC} ${CYAN}${mode}${NC}"
    if [ -n "$domain" ]; then
        echo -e "  ${WHITE}$(t net_domain)${NC} ${CYAN}${domain}${NC}"
        # Прокси слушает только IPv4: AAAA у домена уводит IPv6-клиентов в пустоту
        local _aaaa; _aaaa=$(domain_aaaa_records "$domain" | head -3 | paste -sd ' ' -)
        [ -n "$_aaaa" ] && echo -e "  ${YELLOW}⚠${NC} ${YELLOW}$(tf dns_aaaa_warn "$domain" "$_aaaa")${NC}"
        # Домен и имя WEB должны указывать на КАЖДЫЙ адрес сервера: адрес без
        # A-записи клиенты по ссылке не увидят (если он не запасной для SSH)
        if [ "$mode" = "pro" ] && type edgeips_dns_warn >/dev/null 2>&1; then
            local _dnsw _dn _whost
            _whost=$(telemt_web_host 2>/dev/null || true)
            for _dn in "$domain" "$_whost"; do
                [ -n "$_dn" ] || continue
                _dnsw=$(edgeips_dns_warn "$_dn" "$ip")
                [ -n "$_dnsw" ] && echo -e "  ${YELLOW}⚠${NC} ${YELLOW}${_dnsw}${NC}"
            done
        fi
    fi

    # Несколько внешних адресов — показываем распределение прямо в шапке: это
    # первое, на что смотрят, когда «у части людей не работает». На машине с
    # одним адресом блок не выводится: там сообщать нечего.
    if type edgeips_brief >/dev/null 2>&1 && [ "$(edgeips_list 2>/dev/null | wc -l)" -gt 1 ]; then
        echo -e "  ${DIM}${line2}${NC}"
        echo -e "  ${BOLD}${WHITE}$(t edge_title)${NC}"
        edgeips_brief
    fi

    # Запасной вход: молчим, пока всё цело, но проверяем ОТДЕЛЬНО от таблицы
    # выше. Раньше этот блок стоял внутри условия «адресов больше одного», и на
    # машине с двумя адресами пропажа запасного роняла счётчик до единицы —
    # предупреждение о пропаже пряталось ровно в тот момент, ради которого оно
    # писалось.
    if type edgeips_ssh_warn >/dev/null 2>&1; then
        local _sshw; _sshw="$(edgeips_ssh_warn 2>/dev/null)"
        if [ -n "$_sshw" ]; then
            echo -e "  ${DIM}${line2}${NC}"
            while IFS= read -r _l; do
                if [ -n "$_l" ]; then echo -e "  ${YELLOW}⚠${NC} ${_l}"; fi
            done <<< "$_sshw"
        fi
    fi

    echo -e "  ${DIM}${line2}${NC}"

    # ── Качество работы (потери до Telegram, трафик, WARP-вотчдог) ──
    if type metrics_menu_block >/dev/null 2>&1 && [ "$proxy_status" = "running" ]; then
        metrics_menu_block
        echo -e "  ${DIM}${line2}${NC}"
    fi

    # ── Proxy link + QR ──
    local mask_host
    mask_host=$(config_get mask_host 2>/dev/null || echo "")
    if [ -n "$secret" ] && [ "$proxy_status" = "running" ]; then
        if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
            link=$(generate_proxy_link "$domain" "$port" "$secret" "$domain")
        else
            link=$(generate_proxy_link "$ip" "$port" "$secret" "$mask_host")
        fi

        echo -e "  ${BOLD}${WHITE}$(t connection_link)${NC}"
        echo -e "  ${GREEN}${link}${NC}"

        if command -v qrencode &>/dev/null; then
            echo ""
            qrencode -t UTF8 -m 2 "$link" 2>/dev/null | while IFS= read -r qr_line; do
                echo "  ${qr_line}"
            done
            echo ""
        fi
    else
        echo -e "  ${DIM}$(t proxy_not_configured)${NC}"
        echo ""
    fi

    # ── Menu ──
    echo -e "  ${DIM}${line2}${NC}"
    echo -e "  ${CYAN}1${NC}) $(t menu_proxy)"
    echo -e "  ${CYAN}2${NC}) $(t menu_stats)"
    echo -e "  ${CYAN}3${NC}) $(t menu_manage)"
    echo -e "  ${CYAN}4${NC}) $(t menu_telegram_bot)"
    echo -e "  ${CYAN}5${NC}) $(t menu_about)"
    echo -e "  ${CYAN}0${NC}) ${DIM}$(t exit)${NC}"
    echo -e "  ${DIM}${line2}${NC}"
    echo -e "  ${DIM}$(t auto_refresh_30s)${NC}"
    echo -ne "  ${WHITE}▸ ${NC}"
}

# ── Submenu: Proxy ──────────────────────────────────────────────────────────
submenu_proxy() {
    while true; do
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t submenu_proxy_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${CYAN}1${NC}) $(t proxy_mode_item)"
        echo -e "  ${CYAN}2${NC}) $(t proxy_install_update)"
        echo -e "  ${CYAN}3${NC}) $(t proxy_status_detail)"
        echo -e "  ${CYAN}4${NC}) $(t proxy_copy_link)"
        echo -e "  ${CYAN}5${NC}) $(t proxy_share)"
        echo -e "  ${CYAN}6${NC}) $(t proxy_restart)"
        echo -e "  ${CYAN}7${NC}) $(t proxy_logs)"
        echo -e "  ${CYAN}8${NC}) $(t proxy_change_mode)"
        echo -e "  ${CYAN}9${NC}) $(t usr_title)"
        echo -e "  ${CYAN}10${NC}) $(tf proxy_tls_item "$(tls_protocols_label)")"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        # EOF (оборванная SSH-сессия) → 0: иначе ветка *) «неверный выбор»
        # и снова read — бесконечный цикл на 100% CPU.
        read -r ch || ch=0

        case "$ch" in
            1) mode_menu ;;
            2) menu_install ;;
            3) menu_status ;;
            4) menu_link ;;
            5) menu_share ;;
            6) menu_restart ;;
            7) menu_logs ;;
            8) menu_change_mode ;;
            9) menu_users ;;
            10) tls_protocols_menu ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac

        echo ""
        echo -ne "  ${DIM}$(t press_enter)${NC}"
        read -r
    done
}

# ── Submenu: Management ─────────────────────────────────────────────────────
submenu_manage() {
    while true; do
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t submenu_manage_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${CYAN}1${NC}) $(t manage_backup)"
        echo -e "  ${CYAN}2${NC}) $(t manage_restore)"
        echo -e "  ${CYAN}3${NC}) $(t manage_update_telemt)"
        echo -e "  ${CYAN}4${NC}) $(t manage_site_ssl)"
        echo -e "  ${CYAN}5${NC}) $(t manage_remove)"
        echo -e "  ${CYAN}6${NC}) $(t manage_language)"
        echo -e "  ${CYAN}7${NC}) $(t manage_warp)"
        echo -e "  ${CYAN}8${NC}) $(t ma_title)"
        echo -e "  ${CYAN}9${NC}) $(t aw_title)"
        echo -e "  ${CYAN}10${NC}) $(t web_title)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        # EOF (оборванная SSH-сессия) → 0: иначе ветка *) «неверный выбор»
        # и снова read — бесконечный цикл на 100% CPU.
        read -r ch || ch=0

        case "$ch" in
            1) interactive_backup ;;
            2) interactive_restore ;;
            3) telemt_version_change ;;
            4) menu_website ;;
            5) menu_remove ;;
            6) menu_language ;;
            7) warp_egress_menu ;;
            8) miniapp_menu ;;
            9) admin_web_screen ;;
            10) web_menu ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac

        echo ""
        echo -ne "  ${DIM}$(t press_enter)${NC}"
        read -r
    done
}


# ── SYN-фикс (MEKO V2/V3/V4): интерактивное подменю ─────────────────────────
# ── Единое меню режима работы ────────────────────────────────────────────────
# Раньше настройки защиты жили в трёх местах: «SYN-фикс» в Управлении, «Подбор
# режима» в главном меню и профили сети подменю внутри подменю. Человек не мог
# понять, что на что влияет, и выбор в одном месте молча менял поведение другого.
# Теперь один экран: выбрал строку — применилось. Сверху то, что рекомендуется.
mode_menu() {
    local src ch p
    src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    while true; do
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t mode_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  $(t mode_now): ${BOLD}$(mode_current_label)${NC}"
        echo ""
        echo -e "  ${DIM}$(t mode_hint_order)${NC}"
        echo ""
        echo -e "  ${CYAN}1${NC}) $(t mode_v3_free)   ${GREEN}$(t mode_main)${NC}"
        echo -e "     ${DIM}$(t mode_v3_free_d)${NC}"
        echo -e "  ${CYAN}2${NC}) $(t mode_v3_lim)"
        echo -e "     ${DIM}$(t mode_v3_lim_d)${NC}"
        echo -e "  ${CYAN}3${NC}) $(t mode_v4)   ${YELLOW}$(t mode_exp)${NC}"
        echo -e "     ${DIM}$(t mode_v4_d)${NC}"
        echo ""
        echo -e "  ${DIM}── $(t mode_std) ──${NC}"
        echo -e "  ${CYAN}4${NC}) $(t mode_p1)  ${DIM}$(t mode_p1_d)${NC}"
        echo -e "  ${CYAN}5${NC}) $(t mode_p2)  ${DIM}$(t mode_p2_d)${NC}"
        echo -e "  ${CYAN}6${NC}) $(t mode_p4)  ${DIM}$(t mode_p4_d)${NC}"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        read -r ch < "$src" || ch=0
        p=$(config_get port 2>/dev/null); [[ "$p" =~ ^[0-9]+$ ]] || p=443
        case "$ch" in
            1) mode_apply_fix v3 0 "$p" ;;
            2) mode_apply_fix v3 1 "$p" ;;
            3) mode_apply_fix v4 0 "$p" ;;
            4) mode_apply_preset 1 ;;
            5) mode_apply_preset 2 ;;
            6) mode_apply_preset 4 ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac
    done
}

# Что показывать в строке «Сейчас».
mode_current_label() {
    if type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null; then
        t mode_v4; return
    fi
    if type mekofix_is_active >/dev/null 2>&1 && mekofix_is_active 2>/dev/null; then
        if [ "$(mekofix_state_get nolimit 2>/dev/null)" = "1" ]; then t mode_v3_free; else t mode_v3_lim; fi
        return
    fi
    local pid; pid=$(config_get preset_id 2>/dev/null || echo "${DIAG_DEFAULT_PRESET:-4}")
    case "$pid" in
        1) t mode_p1 ;; 2) t mode_p2 ;; *) t mode_p4 ;;
    esac
}

# Спаривание фикса с MEKO style (#4) — ОДНО место для SSH-меню и контракта
# mekofix-apply: раньше пресет форсило только SSH, и «один и тот же» режим из
# бота и из SSH давал разный telemt.
gt_fix_pair_preset() {
    [ "$(config_get preset_id 2>/dev/null)" = "4" ] && return 0
    # Глушим только stdout (чистота JSON-контракта): stderr и код возврата живут,
    # иначе упавший рестарт ядра выглядел успешной сменой режима.
    apply_preset 4 >/dev/null
}

# Режимы 1-3: внешний фикс + анти-DPI профиль MEKO (BBR, без MSS).
mode_apply_fix() {
    local ver="$1" limited="$2" port="$3"
    if [ "$ver" = "v4" ]; then
        log_warning "$(t mode_v4_warn)"
        confirm "$(t mode_confirm)" || { log_info "$(t usr_cancelled)"; return 0; }
        export V4_NO_CONFIRM=1
    fi
    export MEKOFIX_NOLIMIT="$([ "$limited" = "1" ] && echo 0 || echo 1)"
    gt_ensure_synfix "$ver" "$port" || return 1
    # Пункты 1-3 работают в паре с MEKO style: BBR и без MSS.
    local _prc=0
    gt_fix_pair_preset || _prc=1
    unset MEKOFIX_NOLIMIT
    return $_prc
}

# Режимы 4-6: только анти-DPI профиль. Внешний фикс снимаем — иначе человек
# остаётся с фиксом от прошлого выбора и не понимает, что у него включено.
mode_apply_preset() {
    local pid="$1"
    # Снятие обязано быть подтверждённым — см. _gt_drop_external_fix. Иначе
    # SSH-меню повторяло бы ту же ложь, что и контракт: поле очищено, правила
    # висят. Пресет поверх оставшихся правил — не тот режим, который выбрали.
    if ! _gt_drop_external_fix; then
        log_error "Правила внешнего SYN-фикса снять не удалось — режим #${pid} не применяю"
        return 1
    fi
    apply_preset "$pid"
}

# ── Оффер SYN-фикса при ЧИСТОЙ установке (с объяснением, как WARP) ─────────
# ── gt_preserve_existing — сохранить secret и ключи живой установки.
# Переустановка поверх и смена режима Lite↔Pro шли через install_*_mode, а те
# генерировали НОВЫЙ secret и писали [access.users] с нуля: у человека разом
# протухали все tg://proxy-ссылки и исчезали дополнительные ключи. Возврата нет.
# Здесь читаем прежние значения ДО генерации конфига; вернуть их обратно — gt_restore_users.
# Результат — ТОЛЬКО в глобальных GT_PRESERVED_SECRET/GT_PRESERVED_USERS: прежний
# вызов через $(…) исполнял функцию в подшелле, присваивания гибли, и restore
# молча ничего не восстанавливал.
GT_PRESERVED_USERS=""
GT_PRESERVED_SECRET=""
gt_preserve_existing() {
    GT_PRESERVED_USERS=""
    GT_PRESERVED_SECRET=""
    [ -f "$TELEMT_CONFIG" ] || return 0
    GT_PRESERVED_SECRET=$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    GT_PRESERVED_USERS=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null || echo "")
    if [ -n "$GT_PRESERVED_SECRET" ]; then
        log_dim "Найдена существующая установка — переиспользую secret и ключи (ссылки не протухнут)."
    fi
}
gt_restore_users() {
    [ -n "${GT_PRESERVED_USERS:-}" ] || return 0
    type replace_telemt_users_block >/dev/null 2>&1 || return 0
    replace_telemt_users_block "$GT_PRESERVED_USERS" "$TELEMT_CONFIG" 2>/dev/null \
        && log_success "Ключи прежней установки сохранены ($(printf '%s' "$GT_PRESERVED_USERS" | grep -c '=') шт.)."
    GT_PRESERVED_USERS=""
}

# ── gt_ensure_synfix <v3|v2|v4> — привести внешний SYN-фикс к нужной версии ──
# Вызывается на чистой установке (дефолт V3) и при апгрейде со старых версий,
# где фикса не было вовсе или стоял встроенный в ядро V2. Идемпотентна.
gt_ensure_synfix() {
    local want="${1:-v3}" port="${2:-}" cur
    case "$want" in v2|v3|v4) ;; *) log_error "gt_ensure_synfix: неизвестная версия '$want'"; return 1 ;; esac
    type mekofix_apply >/dev/null 2>&1 || { log_dim "Модуль SYN-фикса недоступен — пропускаю."; return 0; }
    # Порт можно передать явно: на чистой установке config.json ещё может не быть,
    # и тогда фикс вешался на 443 вместо реального порта Lite (8443/произвольный).
    [[ "$port" =~ ^[0-9]+$ ]] || port=$(config_get port 2>/dev/null)
    [[ "$port" =~ ^[0-9]+$ ]] || port=443
    cur=$(config_get mekofix 2>/dev/null || echo "")
    # Уже активна нужная версия? Для V4 состояние спрашиваем у ЕГО модуля:
    # mekofix_is_active знает только про nft-правила V2/V3 и про V4 всегда врёт «нет».
    if [ "$cur" = "$want" ]; then
        if [ "$want" = "v4" ]; then
            type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null && { log_dim "SYN-фикс v4 уже активен."; return 0; }
        elif mekofix_is_active 2>/dev/null; then
            # Версия та же (v3), но пункты 1 и 2 меню различаются НАЛИЧИЕМ лимита.
            # Ранний выход сверял только версию — переключение «без ограничения»
            # ↔ «с ограничением» молча ничего не делало, а статус бодро отвечал
            # «включён». Человек жал кнопку, получал «ок» и оставался где был.
            local _cur_nl _want_nl
            _cur_nl=$(mekofix_state_get nolimit 2>/dev/null || echo 0)
            [ -z "$_cur_nl" ] && _cur_nl=0
            # MEKOFIX_NOLIMIT не задан (миграция, автозапуск) → считаем «как есть»,
            # чтобы не перетряхивать правила без нужды.
            _want_nl="${MEKOFIX_NOLIMIT:-$_cur_nl}"
            if [ "$_cur_nl" = "$_want_nl" ]; then
                log_dim "SYN-фикс ${want} уже активен (лимит без изменений)."; return 0
            fi
        fi
    fi
    # снимаем чужую версию, чтобы механизмы не наслаивались
    type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null && { type v4_uninstall >/dev/null 2>&1 && v4_uninstall >/dev/null 2>&1; }
    mekofix_is_active 2>/dev/null && mekofix_remove >/dev/null 2>&1

    # V4 — ОТДЕЛЬНЫЙ модуль (zapret2/nfqws2), не nft-правила. Раньше сюда уходил
    # mekofix_apply v4, который V4 не умеет и честно отвергал — из-за чего выбор
    # V4 в боте и в меню молча заканчивался «не удалось включить».
    if [ "$want" = "v4" ]; then
        if ! type v4_install >/dev/null 2>&1; then
            log_error "Модуль V4 недоступен (lib/mekofix_v4.sh не загружен)."
            return 1
        fi
        if v4_install "$port" >&2; then
            bot_update_config_field mekofix v4 >/dev/null 2>&1
            log_success "SYN-фикс v4 (zapret2) включён — экспериментальный режим."
            return 0
        fi
        log_warning "V4 применить не удалось — прокси работает без фикса."
        return 1
    fi

    if mekofix_apply "$want" "$port" >/dev/null 2>&1; then
        bot_update_config_field mekofix "$want" >/dev/null 2>&1
        log_success "SYN-фикс ${want} включён (умная блокировка, без лимита на число устройств)."
    else
        log_warning "SYN-фикс ${want} применить не удалось — прокси работает без него."
        return 1
    fi
}

offer_mekofix_setup() {
    type mekofix_apply >/dev/null 2>&1 || return 0
    # Установщик уже включает V3 сам. Спрашивать после этого «включить фикс?» —
    # значит убеждать человека, что фикса нет, и путать диагностику.
    if mekofix_is_active 2>/dev/null; then
        log_dim "SYN-фикс уже включён ($(config_get mekofix 2>/dev/null || echo v3))."
        return 0
    fi
    # Ленивый режим = «самое стабильное и универсальное, без вопросов».
    # По итогам живых тестов внешний SYN-лимит ни разу не помог и дважды навредил
    # (Telegram Desktop и мобильный LTE/CGNAT), а в Pro он ещё и режет посетителей
    # сайта-маскировки на том же :443 → маскировка выглядит сломанной для DPI-пробы.
    if [ "${GOTELEGRAM_LAZY:-0}" = "1" ]; then
        log_dim "$(t mf_lazy_skip)"
        return 0
    fi
    local p ch
    p=$(config_get port 2>/dev/null); [[ "$p" =~ ^[0-9]+$ ]] || p=443
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t mf_offer_title)${NC}"
    echo -e "  ${DIM}$(t mf_offer_desc1)${NC}"
    echo -e "  ${DIM}$(t mf_offer_desc2)${NC}"
    mekofix_plan v3 "$p" || return 0
    echo -ne "  ${WHITE}$(t mf_offer_ask) ${NC}"; read -r ch || ch=""
    case "${ch:-n}" in
        [yYдД]*) mekofix_apply v3 "$p" || true ;;
        *) log_dim "$(t mf_offer_later)" ;;
    esac
    return 0
}

# WARP-egress: интерактивное подменю (SSH)
warp_egress_menu() {
    if ! type warp_egress_enable >/dev/null 2>&1; then log_error "$(t warp_module_missing)"; return 1; fi
    local src ch k r _ wn; src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    while true; do
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t warp_title)${NC}"
        echo -e "  ${DIM}$(t warp_hint1)${NC}"
        echo -e "  ${DIM}$(t warp_hint2)${NC}"
        local st state tier egr colo hs route
        st=$(warp_egress_status 2>/dev/null)
        state=$(printf '%s\n' "$st"|awk -F= '/^state=/{print $2}')
        # Сырое значение нужно сохранить: ниже по нему решается, показывать ли
        # строку CF-трафика. Сравнение с переведённой подписью работало только
        # в русской локали — в английской (ON) строка молча пропадала.
        local state_raw="$state"
        case "$state" in on) state="$(t mf_on)" ;; off) state="$(t mf_off)" ;; esac
        tier=$(printf '%s\n' "$st"|awk -F= '/^tier=/{print $2}')
        egr=$(printf '%s\n' "$st"|awk -F= '/^egress=/{print $2}')
        colo=$(printf '%s\n' "$st"|awk -F= '/^colo=/{print $2}')
        hs=$(printf '%s\n' "$st"|awk -F= '/^handshake=/{print $2}')
        route=$(printf '%s\n' "$st"|awk -F= '/^route=/{print $2}')
        echo ""
        echo -e "  $(t warp_state): ${BOLD}${state:-?}${NC}  ·  tier: ${tier:-?}  ·  $(t warp_route): ${route:-?}"
        echo -e "  $(t warp_egress_via): ${egr:-?}  ·  $(t warp_colo): ${colo:-?}  ·  $(t warp_hs): ${hs:-—}"
        if [ "$state_raw" = "on" ] && type metrics_cf_window >/dev/null 2>&1; then
            echo -e "  $(t q_cf_traffic) 1м $(metrics_cf_window 60) · 5м $(metrics_cf_window 300) · 1ч $(metrics_cf_window 3600) · 1д $(metrics_cf_window 86400)"
        fi
        echo ""
        echo -e "  ${CYAN}1${NC}) $(t warp_opt_free)"
        echo -e "  ${CYAN}2${NC}) $(t warp_opt_key)"
        echo -e "  ${CYAN}3${NC}) $(t warp_opt_off)"
        echo -e "  ${CYAN}4${NC}) $(t warp_opt_changekey)"
        echo -e "  ${CYAN}5${NC}) $(t warp_opt_monitor)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -ne "  ${WHITE}> ${NC}"; read -r ch < "$src" || ch=0
        case "$ch" in
            1) warp_egress_enable "" ;;
            2) echo -ne "  $(t warp_ask_key)"; read -r k < "$src"
               if [ -z "$k" ]; then
                   log_warning "$(t warp_key_empty)"
               elif ! _warp_valid_key "$k"; then
                   log_error "$(t warp_key_badfmt)"
               else
                   warp_egress_enable "$k"
                   wn=$(cat /run/gotelegram-warp.note 2>/dev/null)
                   [ -n "$wn" ] && [ "$wn" != "tg-unreachable" ] && log_warning "$(t warp_key_rejected) ($wn)"
               fi ;;
            3) warp_egress_disable ;;
            4) echo -ne "  $(t warp_ask_key_short)"; read -r k < "$src"
               if [ -n "$k" ] && ! _warp_valid_key "$k"; then
                   log_error "$(t warp_key_badfmt)"
               else
                   r=$(warp_egress_apply_key "$k")
                   case "$r" in
                       ok)   log_success "$(t warp_key_ok)" ;;
                       free) log_info "$(t warp_key_free)" ;;
                       *)    log_warning "$(t warp_key_rejected) ($r)" ;;
                   esac
               fi ;;
            5) warp_egress_dashboard ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac
        case "$ch" in 5) : ;; *) echo -ne "  ${DIM}$(t warp_continue)${NC}"; read -r _ < "$src" ;; esac
    done
}

# ── Submenu: About ──────────────────────────────────────────────────────────
submenu_about() {
    while true; do
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t submenu_about_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${CYAN}1${NC}) $(t about_version_info)"
        echo -e "  ${CYAN}2${NC}) $(t about_promo)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        # EOF (оборванная SSH-сессия) → 0: иначе ветка *) «неверный выбор»
        # и снова read — бесконечный цикл на 100% CPU.
        read -r ch || ch=0

        case "$ch" in
            1) menu_version ;;
            2) menu_promo ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac

        echo ""
        echo -ne "  ${DIM}$(t press_enter)${NC}"
        read -r
    done
}

# ── Version info ────────────────────────────────────────────────────────────
menu_version() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t version_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
    echo -e "  ${WHITE}$(t version_label)${NC}   v${GOTELEGRAM_VERSION}"
    echo -e "  ${WHITE}$(t version_engine)${NC}         telemt (Rust + Tokio)"
    echo -e "  ${WHITE}$(t version_tech)${NC}   Anti-DPI, Fake TLS, TCP Splice"
    echo -e "  ${WHITE}$(t version_license)${NC}     MIT"
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
}

# ── Upgrade migration ────────────────────────────────────────────────────────
snapshot_preupgrade_state() {
    local marker="$GOTELEGRAM_DIR/.preupgrade_${GOTELEGRAM_VERSION}_done"
    [ -f "$marker" ] && return 0
    mkdir -p "$BACKUP_DIR"

    local ts tmp archive
    ts=$(date +%Y%m%d_%H%M%S)
    tmp="/tmp/gotelegram_preupgrade_${ts}"
    archive="$BACKUP_DIR/preupgrade_${GOTELEGRAM_VERSION}_${ts}.tar.gz"
    mkdir -p "$tmp"

    [ -f "$GOTELEGRAM_CONFIG" ] && mkdir -p "$tmp/opt/gotelegram" && cp "$GOTELEGRAM_CONFIG" "$tmp/opt/gotelegram/config.json" 2>/dev/null
    [ -f "$TELEMT_CONFIG" ] && mkdir -p "$tmp/etc/telemt" && cp "$TELEMT_CONFIG" "$tmp/etc/telemt/config.toml" 2>/dev/null
    [ -f "$NGINX_SITE_CONF" ] && mkdir -p "$tmp/etc/nginx/sites-available" && cp "$NGINX_SITE_CONF" "$tmp/etc/nginx/sites-available/gotelegram" 2>/dev/null
    [ -d "$WEBSITE_ROOT" ] && mkdir -p "$tmp/var/www/gotelegram-site" && cp -a "$WEBSITE_ROOT/." "$tmp/var/www/gotelegram-site/" 2>/dev/null
    [ -f "$BOT_DIR/.env" ] && mkdir -p "$tmp/opt/gotelegram-bot" && cp "$BOT_DIR/.env" "$tmp/opt/gotelegram-bot/.env" 2>/dev/null

    chmod 700 "$BACKUP_DIR" 2>/dev/null
    # -s: tar может «успешно» создать пустой архив при нехватке места. Без этой
    # проверки миграция шла дальше и перезаписывала config.toml, будучи уверенной,
    # что откат возможен — а откатываться было не с чего.
    if ( umask 077; tar czf "$archive" -C "$tmp" . 2>/dev/null ) && [ -s "$archive" ]; then
        chmod 600 "$archive" 2>/dev/null
        log_dim "Pre-upgrade snapshot: $archive"
        touch "$marker" 2>/dev/null || true
    else
        rm -f "$archive" 2>/dev/null
        log_error "Не удалось создать резервную копию (нет места на диске?)."
        log_error "Миграция прервана — конфиг НЕ тронут. Освободите место и повторите."
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"
}

read_config_or_default() {
    local key="$1" fallback="$2"
    config_get "$key" 2>/dev/null || echo "$fallback"
}

detect_deployed_template_id() {
    local tpl=""
    if [ -f "$WEBSITE_ROOT/.gotelegram_template_id" ]; then
        tpl=$(head -1 "$WEBSITE_ROOT/.gotelegram_template_id" 2>/dev/null || echo "")
        [ -n "$tpl" ] && { echo "$tpl"; return 0; }
    fi
    if [ -d "$WEBSITE_ROOT" ] && [ -f "$WEBSITE_ROOT/index.html" ]; then
        echo "deployed_site"
        return 0
    fi
    tpl=$(read_config_or_default template_id "")
    [ -n "$tpl" ] && { echo "$tpl"; return 0; }
    echo ""
}

detect_template_source() {
    local src
    if [ -f "$WEBSITE_ROOT/.gotelegram_template_source" ]; then
        src=$(head -1 "$WEBSITE_ROOT/.gotelegram_template_source" 2>/dev/null || echo "")
        [ -n "$src" ] && { echo "$src"; return 0; }
    fi
    [ -d "$WEBSITE_ROOT" ] && [ -f "$WEBSITE_ROOT/index.html" ] && return 0
    read_config_or_default template_source ""
}

write_normalized_gotelegram_config() {
    local mode="$1" port="$2" secret="$3" mask_host="$4" domain="$5" tpl_id="$6" tpl_source="$7"
    local lang installed_at stats_enabled tmp
    # Файл есть, но не парсится? Это НЕ то же самое, что «ключа нет»: раньше оба
    # случая давали одинаковый fallback, и у русскоязычного человека интерфейс
    # молча становился английским, а дата установки — сегодняшней. Отличаем явно
    # и вытягиваем язык из файла-маркера, который пишется отдельно.
    if [ -f "$GOTELEGRAM_CONFIG" ] && ! jq -e . "$GOTELEGRAM_CONFIG" >/dev/null 2>&1; then
        log_warning "config.json повреждён — беру язык из маркера, дату установки не выдумываю."
        cp -a "$GOTELEGRAM_CONFIG" "${GOTELEGRAM_CONFIG}.broken.$(date +%s)" 2>/dev/null || true
        lang=$(cat "$GOTELEGRAM_DIR/.language" 2>/dev/null | tr -d '[:space:]')
        [ -z "$lang" ] && lang=$(get_language 2>/dev/null || echo en)
        installed_at=""
        # пробуем достать дату из последнего снапшота — он делается перед миграцией
        local _snap; _snap=$(ls -1t "$BACKUP_DIR"/preupgrade_*.tar.gz 2>/dev/null | head -1)
        if [ -n "$_snap" ]; then
            installed_at=$(tar -xzOf "$_snap" ./config.json 2>/dev/null | jq -r '.installed_at // empty' 2>/dev/null)
        fi
        [ -z "$installed_at" ] && installed_at=$(date -Iseconds)
    else
        lang=$(read_config_or_default language "$(get_language 2>/dev/null || echo en)")
        installed_at=$(read_config_or_default installed_at "$(date -Iseconds)")
    fi
    # stats_enabled — БУЛЕВО: config_get использует jq '.k // empty', а для false
    # это пусто → поле выпадало из нормализованного конфига и сбор статистики
    # сам включался после апгрейда. Читаем через has() + tostring.
    stats_enabled=$(jq -r 'if has("stats_enabled") then (.stats_enabled|tostring) else "" end' "$GOTELEGRAM_CONFIG" 2>/dev/null) || stats_enabled=""
    [ "$stats_enabled" = "null" ] && stats_enabled=""
    local preset_id
    preset_id=$(read_config_or_default preset_id "")
    local preset_ratings
    preset_ratings=$(jq -c '.preset_ratings // empty' "$GOTELEGRAM_CONFIG" 2>/dev/null)
    [ -z "$preset_ratings" ] && preset_ratings='{}' 
    local reanim_synlimit reanim_ios telemt_version
    reanim_synlimit=$(read_config_or_default reanim_synlimit "")
    reanim_ios=$(read_config_or_default reanim_ios_keepalive "")
    telemt_version=$(read_config_or_default telemt_version "${TELEMT_VERSION_PREF:-}")
    local warp_egress warp_key warp_tier
    local mekofix mekofix_off
    mekofix=$(read_config_or_default mekofix "")
    mekofix_off=$(read_config_or_default mekofix_builtin_off "")
    # miniapp_path и настройки WEB в этом списке отсутствовали и терялись при
    # каждом обновлении: панель на домене выключалась сама собой.
    local miniapp_path web_enabled web_carrier web_host
    miniapp_path=$(read_config_or_default miniapp_path "")
    web_enabled=$(read_config_or_default web_enabled "")
    web_carrier=$(read_config_or_default web_carrier "")
    # Отдельное имя WEB: без него следующая перегенерация молча вернула бы WEB
    # на основной домен, где он душит обычные ссылки за тем же NAT
    web_host=$(read_config_or_default web_host "")
    # Версии TLS сайта: без ключа обновление вернуло бы TLSv1.3 тому, кто его
    # снял (провайдер резал 443). Значение не проверяем — это делает
    # nginx_tls_protocols при генерации, и в nginx сырое не попадает.
    local tls_protocols
    tls_protocols=$(read_config_or_default tls_protocols "")
    warp_egress=$(read_config_or_default warp_egress "")
    warp_key=$(read_config_or_default warp_key "")
    warp_tier=$(read_config_or_default warp_tier "")
    tmp=$(mktemp) || return 1

    jq -n \
        --arg version "$GOTELEGRAM_VERSION" \
        --arg engine "telemt" \
        --arg mode "$mode" \
        --argjson port "$port" \
        --arg secret "$secret" \
        --arg mask_host "$mask_host" \
        --arg domain "$domain" \
        --arg template_id "$tpl_id" \
        --arg template_source "$tpl_source" \
        --arg language "$lang" \
        --arg installed_at "$installed_at" \
        --arg updated_at "$(date -Iseconds)" \
        --arg stats_enabled "$stats_enabled" \
        --arg preset_id "$preset_id" \
        --arg reanim_synlimit "$reanim_synlimit" \
        --arg reanim_ios "$reanim_ios" \
        --arg telemt_version "$telemt_version" \
        --arg mekofix "$mekofix" \
        --arg mekofix_builtin_off "$mekofix_off" \
        --arg miniapp_path "$miniapp_path" \
        --arg web_enabled "$web_enabled" \
        --arg web_carrier "$web_carrier" \
        --arg web_host "$web_host" \
        --arg tls_protocols "$tls_protocols" \
        --arg warp_egress "$warp_egress" \
        --arg warp_key "$warp_key" \
        --arg warp_tier "$warp_tier" \
        --argjson preset_ratings "$preset_ratings" \
        '{
            version: $version,
            engine: $engine,
            mode: $mode,
            port: $port,
            secret: $secret,
            mask_host: $mask_host,
            domain: $domain,
            template_id: $template_id,
            language: $language,
            installed_at: $installed_at,
            updated_at: $updated_at
        }
        + (if $template_source != "" then {template_source: $template_source} else {} end)
        + (if $stats_enabled == "true" then {stats_enabled: true} elif $stats_enabled == "false" then {stats_enabled: false} else {} end)
        + (if $preset_id != "" then {preset_id: $preset_id} else {} end)
        + (if $reanim_synlimit != "" then {reanim_synlimit: $reanim_synlimit} else {} end)
        + (if $reanim_ios != "" then {reanim_ios_keepalive: $reanim_ios} else {} end)
        + (if $telemt_version != "" then {telemt_version: $telemt_version} else {} end)
        + (if $mekofix != "" then {mekofix: $mekofix} else {} end)
        + (if $mekofix_builtin_off != "" then {mekofix_builtin_off: $mekofix_builtin_off} else {} end)
        + (if $miniapp_path != "" then {miniapp_path: $miniapp_path} else {} end)
        + (if $web_enabled != "" then {web_enabled: $web_enabled} else {} end)
        + (if $web_carrier != "" then {web_carrier: $web_carrier} else {} end)
        + (if $web_host != "" then {web_host: $web_host} else {} end)
        + (if $tls_protocols != "" then {tls_protocols: $tls_protocols} else {} end)
        + (if $warp_egress != "" then {warp_egress: $warp_egress} else {} end)
        + (if $warp_key != "" then {warp_key: $warp_key} else {} end)
        + (if $warp_tier != "" then {warp_tier: $warp_tier} else {} end)
        + (if ($preset_ratings | length) > 0 then {preset_ratings: $preset_ratings} else {} end)' \
        > "$tmp" || { rm -f "$tmp"; return 1; }

    mkdir -p "$(dirname "$GOTELEGRAM_CONFIG")"
    mv "$tmp" "$GOTELEGRAM_CONFIG"
    chmod 600 "$GOTELEGRAM_CONFIG"
}

# ── Network tuning: CC по активному пресету + fq_codel ──────────────────────
# CC делегируется diagnose_set_cc по пресету: cubic (реаниматор/доки), bbr (MEKO —
# там mss=off, поэтому опасная связка «bbr + узкий MSS 92» из 2.6.3 не возникает).
# Если diagnose.sh не загружен — фолбэк форсит cubic (эталонная конфигурация). Идемпотентно.
apply_network_sysctl() {
    # CC по сохранённому пресету (или cubic). Делегируем в diagnose_set_cc, если движок загружен.
    local cc=cubic
    if type diagnose_preset_cc >/dev/null 2>&1; then cc=$(diagnose_preset_cc 2>/dev/null || echo cubic); fi
    if type diagnose_set_cc >/dev/null 2>&1; then diagnose_set_cc "$cc"; return 0; fi
    # fallback (diagnose.sh отсутствует): исходная логика 2.6.3 — форс cubic
    if grep -qsE '^[[:space:]]*net\.ipv4\.tcp_congestion_control[[:space:]]*=[[:space:]]*bbr' /etc/sysctl.conf; then
        sed -i.gotelegram.bak '/^[[:space:]]*net\.ipv4\.tcp_congestion_control[[:space:]]*=[[:space:]]*bbr/d' /etc/sysctl.conf 2>/dev/null || true
    fi
    local f="/etc/sysctl.d/99-zz-gotelegram.conf"
    local desired="net.core.rmem_max=4194304
net.core.wmem_max=4194304
net.core.default_qdisc=fq_codel
net.ipv4.tcp_congestion_control=cubic"
    if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$desired" ]; then
        printf '%s\n' "$desired" > "$f" 2>/dev/null || { log_warning "Не удалось записать $f (sysctl)"; return 0; }
    fi
    modprobe tcp_cubic 2>/dev/null || true
    sysctl --system >/dev/null 2>&1 || true
    sysctl -w net.ipv4.tcp_congestion_control=cubic >/dev/null 2>&1 || true
    log_info "Сетевые sysctl: cubic + fq_codel (bbr отключён — ломает узкий MSS)"
    return 0
}

# ── DNS сервера: порядок nameserver и options в /etc/resolv.conf ─────────────
# 29.10.2026, сервер владельца: файл хостера (SolusVM) с 8.8.8.8 первым, а 8.8.8.8
# терял ~2 запроса из 5. glibc ждёт ответа 5 с, прежде чем спросить следующий
# сервер, — загрузки, certbot и всё, что ходит по именам, тормозило и падало.
# Ручная правка (1.1.1.1 первым, options timeout:1 attempts:2) дала 20/20.
# Здесь то же самое автоматически: сбоящие серверы — ниже, timeout:1, attempts
# не меньше 2. Ни один сервер не удаляется; комментарии и прочие строки остаются
# дословно на своих местах. Ссылку, файл менеджера DNS и локальный кэш не трогаем.
# Хостер может переписать файл — следующее обновление продукта применит настройку
# снова. Выключатель — файл /etc/resolv.conf.gotelegram.off (рядом с самим файлом:
# переживает обновление, переустановку и удаление). Подробно — DOCS_AI.md, раздел
# «DNS сервера». Функция не роняет установку: всё — в подоболочке, выход 0.
DNS_RESOLV_CONF="${DNS_RESOLV_CONF:-/etc/resolv.conf}"
DNS_TUNE_FALLBACKS="1.1.1.1 8.8.8.8 9.9.9.9"
# Маркер — одна строка в конце файла (ASCII: файл читают не только glibc).
DNS_TUNE_MARK_PREFIX="# goTelegram Pro dns_tune:"
DNS_TUNE_MARK="${DNS_TUNE_MARK_PREFIX} slow nameservers moved down, options timeout:1 attempts>=2; original: resolv.conf.gotelegram.orig; off: touch resolv.conf.gotelegram.off"
# Метка запасного — отдельная строка-комментарий прямо перед его nameserver.
# Не хвостом строки: парсер BIND (dig, host, nslookup) на «extra junk» после
# адреса отказывается читать resolv.conf. Запасной не замеряется, не
# переставляется и всегда стоит после исходных серверов.
DNS_TUNE_FB_MARK_PREFIX="# goTelegram Pro dns_tune fallback:"
DNS_TUNE_FB_MARK="${DNS_TUNE_FB_MARK_PREFIX} the next nameserver was added because the only one was losing queries"

# Адрес годится для dig: каноничный IPv4 или IPv6 (у IPv6 можно %интерфейс).
# Всё прочее в dig не уходит — и не удаляется из файла.
_dns_ns_valid() {
    local a="${1:-}" h s c o='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
    local re4="^${o}[.]${o}[.]${o}[.]${o}\$"
    local re64="^[0-9A-Fa-f:]+:${o}[.]${o}[.]${o}[.]${o}\$"
    [[ "$a" =~ $re4 ]] && return 0
    h="${a%%\%*}"
    if [[ "$a" == *%* ]]; then
        s="${a#*%}"
        [[ "$s" =~ ^[A-Za-z0-9_.-]{1,32}$ ]] || return 1
    fi
    [ "${#h}" -le 45 ] || return 1
    [[ "$h" == *:*:* && "$h" != *:::* ]] || return 1
    c="${h/::/}"; [[ "$c" != *::* ]] || return 1          # «::» не больше одного
    c="${h//[^:]/}"; [ "${#c}" -le 8 ] || return 1         # не больше 8 групп
    [[ "$h" =~ [0-9A-Fa-f]{5} ]] && return 1                # группа — до 4 знаков
    [[ "$h" =~ ^[0-9A-Fa-f:]+$ ]] && return 0
    [[ "$h" =~ $re64 ]] && return 0                         # ::ffff:1.2.3.4
    return 1
}

# Локальный адрес: свой кэш DNS (systemd-resolved 127.0.0.53, dnsmasq, unbound…).
# timeout:1 к нему вреден, а порядок за ним ведёт сам кэш — такой файл не трогаем.
_dns_ns_is_local() {
    local a="${1:-}" z
    a="${a%%\%*}"; a="${a,,}"
    case "$a" in 127.*|0.0.0.0|::ffff:127.*|::ffff:7f*|::ffff:0.0.0.0) return 0 ;; esac
    if [[ "$a" == *:* ]]; then
        z="${a//[0:]/}"                                     # ::1 и :: в любой записи
        { [ -z "$z" ] || { [ "$z" = 1 ] && [[ "$a" == *1 ]]; }; } && return 0
    fi
    return 1
}

# Числовой IPv4 в нестандартной записи (127.1, 0x7f.1, 0177.0.0.1, 2130706433):
# glibc понимает его через inet_aton, мы — нет; такой файл не трогаем.
_dns_ns_odd_v4() {
    [[ "${1:-}" =~ ^(0[xX][0-9A-Fa-f]*|[0-9]+)([.](0[xX][0-9A-Fa-f]*|[0-9]+)){0,3}$ ]]
}

# Внутренний адрес хостера (10/8, 172.16/12, 192.168/16, 100.64/10 — например,
# Alibaba 100.100.2.136, 169.254/16, fc00::/7, fe80::/10). На внутренние имена
# публичный сервер отвечает NXDOMAIN, и glibc к следующему уже не идёт, — поэтому
# при таком адресе в файле порядок не трогаем. Только для годных адресов.
_dns_ns_is_private() {
    local a="${1:-}" g
    a="${a%%\%*}"; a="${a,,}"
    # IPv4-mapped: ::ffff:a.b.c.d, 0:0:0:0:0:ffff:a.b.c.d и hex ::ffff:XXXX:YYYY
    if [[ "$a" =~ ^[0:]*:ffff:(.+)$ ]]; then
        a="${BASH_REMATCH[1]}"
        if [[ "$a" =~ ^([0-9a-f]{1,4}):([0-9a-f]{1,4})$ ]]; then
            g=$((16#${BASH_REMATCH[1]}))
            a="$((g >> 8)).$((g & 255)).0.0"
        fi
    fi
    if [[ "$a" =~ ^([0-9]{1,3})[.]([0-9]{1,3})[.][0-9]{1,3}[.][0-9]{1,3}$ ]]; then
        local o1="${BASH_REMATCH[1]}" o2="${BASH_REMATCH[2]}"
        case "$o1" in
            10)  return 0 ;;
            172) [ "$o2" -ge 16 ] && [ "$o2" -le 31 ] && return 0 ;;
            192) [ "$o2" = 168 ] && return 0 ;;
            100) [ "$o2" -ge 64 ] && [ "$o2" -le 127 ] && return 0 ;;
            169) [ "$o2" = 254 ] && return 0 ;;
        esac
        return 1
    fi
    [[ "$a" == *:* ]] || return 1
    g="${a%%:*}"; [ -n "$g" ] || return 1                # «::…» — нули впереди
    while [ "${#g}" -lt 4 ]; do g="0$g"; done
    case "$g" in fc*|fd*|fe[89ab]*) return 0 ;; esac
    return 1
}

# Строка из файла — для экрана и журнала: log_* выводят через echo -e, поэтому
# убираем обратную косую черту и управляющие символы. В сам файл идёт дословно.
_dns_safe() {
    local s="${1-}"
    s="${s//\\/}"; s="${s//[[:cntrl:]]/}"
    printf '%s' "$s"
}

# Комментарий от менеджера DNS, который сам переписывает файл. Печатает имя.
_dns_manager_of() {
    case "${1,,}" in
        *networkmanager*)   echo NetworkManager ;;
        *systemd-resolved*) echo systemd-resolved ;;
        *resolvconf*)       echo resolvconf ;;
        *dhcpcd*)           echo dhcpcd ;;
        *connman*|*"connection manager"*) echo connman ;;
        *netconfig*)        echo netconfig ;;
        *) return 1 ;;
    esac
}

_dns_have_dig() { command -v dig >/dev/null 2>&1; }

# Сколько из 8 запросов к серверу $1 остались без ответа: печатает 0..8.
# Ответ засчитан только при коде 0 и «status: NOERROR». HOME — несуществующий:
# ~/.digrc (например, +short) спрятал бы строку статуса; ключ -r есть не у всех dig.
# Отдельная функция — чтобы самопроверка могла её подменить.
_dns_ns_losses() {
    local ns="${1:-}" i lost=0 out
    _dns_ns_valid "$ns" || return 1
    for i in 1 2 3 4 5 6 7 8; do
        if out=$(HOME=/nonexistent dig +time=1 +tries=1 "@${ns}" . NS 2>/dev/null) \
           && [[ "$out" == *"status: NOERROR"* ]]; then
            :
        else
            lost=$((lost + 1))
        fi
    done
    echo "$lost"
}

# attempts уже не меньше 2? (число любой длины, ведущие нули — как у atoi)
_dns_attempts_ok() {
    local v="${1:-}"
    [[ "$v" =~ ^[0-9]+$ ]] || return 1
    v="${v#"${v%%[!0]*}"}"
    [ "${#v}" -ge 2 ] || [ "${v:-0}" -ge 2 ]
}

# Запись на месте: тот же inode, права, владелец и контекст SELinux; работает и
# для файла, примонтированного в контейнер (rename там даёт EBUSY).
_dns_write_in_place() { cat "$1" > "$2"; }

dns_resolver_tune() {
    (
        _DT_WORK="" _DT_TMP=""
        trap '[ -n "${_DT_WORK:-}" ] && rm -rf "$_DT_WORK"; [ -n "${_DT_TMP:-}" ] && rm -f "$_DT_TMP"' EXIT
        _dns_resolver_tune_run
    )
    return 0
}

_dns_resolver_tune_run() {
    local conf="${DNS_RESOLV_CONF:-/etc/resolv.conf}"
    local orig="${conf}.gotelegram.orig" off="${conf}.gotelegram.off"
    if [ -e "$off" ] || [ -L "$off" ]; then
        log_info "$(tf dns_tune_off "$off" "$conf")"; log_to_file "dns_tune: $off exists, $conf untouched"
        return 0
    fi
    if [ -L "$conf" ] || [ ! -f "$conf" ]; then
        log_info "$(tf dns_tune_skip_link "$conf")"; log_to_file "dns_tune: $conf is a symlink or not a regular file, untouched"
        return 0
    fi
    _DT_WORK=$(mktemp -d "${TMPDIR:-/tmp}/gt-dns.XXXXXX" 2>/dev/null) || _DT_WORK=""
    if [ -z "$_DT_WORK" ] || ! cat "$conf" > "$_DT_WORK/cur" 2>/dev/null; then
        log_warning "$(tf dns_tune_unreadable "$conf")"; return 0
    fi

    # ── Разбор как у glibc: ключевое слово с начала строки, за ним пробел или таб;
    # адрес nameserver — до первого пробела или таба (glibc 2.26+: strpbrk " \t\n",
    # затем inet_aton_exact; «1.1.1.1#x» для неё негоден). Строки храним дословно.
    local -a L=() kind=() addr=()
    local line rest a i mgr=""
    while IFS= read -r line || [ -n "$line" ]; do
        L+=("$line")
        case "$line" in
            "$DNS_TUNE_MARK_PREFIX"*) kind+=(mark); addr+=("") ;;
            "$DNS_TUNE_FB_MARK_PREFIX"*) kind+=(fbmark); addr+=("") ;;
            '#'*|';'*)
                kind+=(text); addr+=("")
                [ -n "$mgr" ] || mgr=$(_dns_manager_of "$line") || mgr="" ;;
            nameserver[[:blank:]]*)
                rest="${line#nameserver}"; rest="${rest#"${rest%%[![:blank:]]*}"}"
                a="${rest%%[[:blank:]]*}"
                if _dns_ns_valid "$a"; then kind+=(ns); else kind+=(nsbad); fi
                addr+=("$a") ;;
            options[[:blank:]]*) kind+=(opt); addr+=("") ;;
            *) kind+=(text); addr+=("") ;;
        esac
    done < "$_DT_WORK/cur"
    # Запасной — годный nameserver сразу за своей меткой. Метка без него — лишняя:
    # при записи убирается.
    for i in "${!L[@]}"; do
        [ "${kind[$i]}" = fbmark ] || continue
        if [ "${kind[$((i + 1))]-}" = ns ]; then kind[$((i + 1))]=fbns; else kind[$i]=orphan; fi
    done

    if [ -n "$mgr" ]; then
        log_info "$(tf dns_tune_skip_manager "$conf" "$mgr")"; log_to_file "dns_tune: $conf is managed by $mgr, untouched"
        return 0
    fi
    local -a vi=() fbi=()               # исходные серверы с годным адресом; запасные
    local sa
    for i in "${!L[@]}"; do
        case "${kind[$i]}" in ns|nsbad|fbns) ;; *) continue ;; esac
        a="${addr[$i]}"; sa=""
        { _dns_ns_is_local "$a" || { [ "${kind[$i]}" = nsbad ] && _dns_ns_odd_v4 "$a"; }; } && sa=$(_dns_safe "$a")
        if _dns_ns_is_local "$a"; then
            log_info "$(tf dns_tune_skip_local "$conf" "$sa")"; log_to_file "dns_tune: $conf has local nameserver $sa, untouched"
            return 0
        fi
        if [ "${kind[$i]}" = nsbad ] && _dns_ns_odd_v4 "$a"; then
            log_info "$(tf dns_tune_skip_odd "$conf" "$sa")"; log_to_file "dns_tune: $conf has nameserver in non-canonical form ($sa), untouched"
            return 0
        fi
        case "${kind[$i]}" in ns) vi+=("$i") ;; fbns) fbi+=("$i") ;; esac
    done
    if [ "${#vi[@]}" -eq 0 ] && [ "${#fbi[@]}" -gt 0 ]; then
        # Строку хостера убрали, остался только наш запасной — теперь он обычный сервер.
        for i in "${fbi[@]}"; do kind[$i]=ns; kind[$((i - 1))]=orphan; vi+=("$i"); done
        fbi=()
    fi
    if [ "${#vi[@]}" -eq 0 ]; then
        log_info "$(tf dns_tune_skip_nons "$conf")"; log_to_file "dns_tune: no usable nameserver in $conf, untouched"
        return 0
    fi
    local single=0 priv=""
    [ "${#vi[@]}" -eq 1 ] && single=1
    for i in "${vi[@]}"; do
        _dns_ns_is_private "${addr[$i]}" && { priv="${addr[$i]}"; break; }
    done

    # ── options: все строки — в одну на месте первой; у ключ:значение действует
    # последнее значение; timeout:1 всегда, attempts не меньше 2 ──
    local -a okeys=() toks=()
    local -A oval=() ohas=()
    local tok key opts_before=""
    for i in "${!L[@]}"; do
        [ "${kind[$i]}" = opt ] || continue
        opts_before+="${opts_before:+ | }${L[$i]}"
        IFS=$' \t\n' read -r -a toks <<< "${L[$i]#options}"
        for tok in "${toks[@]}"; do
            if [[ "$tok" == *:* ]]; then key="k:${tok%%:*}"; oval[$key]="${tok#*:}"; else key="f:$tok"; fi
            [ -n "${ohas[$key]+x}" ] || { ohas[$key]=1; okeys+=("$key"); }
        done
    done
    [ -n "${ohas[k:timeout]+x}" ] || { ohas[k:timeout]=1; okeys+=("k:timeout"); }
    oval[k:timeout]=1
    if ! _dns_attempts_ok "${oval[k:attempts]-}"; then
        [ -n "${ohas[k:attempts]+x}" ] || { ohas[k:attempts]=1; okeys+=("k:attempts"); }
        oval[k:attempts]=2
    fi
    local opt_line="options"
    for key in "${okeys[@]}"; do
        case "$key" in k:*) opt_line+=" ${key#k:}:${oval[$key]}" ;; *) opt_line+=" ${key#f:}" ;; esac
    done

    # ── Замер: разные серверы параллельно; при одном сервере без запасного —
    # сразу и кандидаты в запасные. Один сервер с запасным замерять незачем:
    # порядок у одного не меняется, второй запасной не добавляется. ──
    local -a cand=() mq=() pids=()
    local -A loss=() seen=()
    local c
    if [ "$single" = 1 ] && [ "${#fbi[@]}" -gt 0 ]; then
        :
    elif _dns_have_dig; then
        for i in "${vi[@]}"; do
            a="${addr[$i]}"; [ -n "${seen[$a]+x}" ] && continue
            seen[$a]=1; mq+=("$a")
        done
        if [ "$single" = 1 ] && [ -z "${ohas[f:rotate]+x}" ]; then
            local -a fbs=()
            IFS=$' \t\n' read -r -a fbs <<< "$DNS_TUNE_FALLBACKS"
            for c in "${fbs[@]}"; do
                [ -n "${seen[$c]+x}" ] && continue
                for i in "${!addr[@]}"; do [ "${addr[$i]}" = "$c" ] && continue 2; done
                seen[$c]=1; cand+=("$c"); mq+=("$c")
            done
        fi
        log_info "$(tf dns_tune_measuring "$conf")"
        for i in "${!mq[@]}"; do
            _dns_ns_losses "${mq[$i]}" > "$_DT_WORK/m$i" 2>/dev/null &
            pids+=("$!")
        done
        # wait по номерам: голый wait ждал бы и чужие фоновые задачи (спиннер)
        for i in "${!mq[@]}"; do
            wait "${pids[$i]}" 2>/dev/null
            a=$(cat "$_DT_WORK/m$i" 2>/dev/null)
            [[ "$a" =~ ^[0-8]$ ]] && loss[${mq[$i]}]="$a"
        done
        local measured=""
        for c in "${mq[@]}"; do measured+=" ${c}=${loss[$c]-?}/8"; done
        log_to_file "dns_tune: lost of 8:${measured}"
    else
        log_info "$(t dns_tune_nodig)"; log_to_file "dns_tune: no dig, nameserver order left as is"
    fi

    # ── Порядок: сбоящие (от 2 потерь из 8) — ниже здоровых, в прежнем взаимном
    # порядке. Не трогаем, если сервер один, если в файле есть внутренний адрес
    # хостера или если сбоят все. Сообщения о перестановке и запасном — в pend:
    # они выводятся только после успешной записи. ──
    local fb="" l e
    local -a good=() bad=() order=() pend=()
    for i in "${vi[@]}"; do
        l="${loss[${addr[$i]}]-}"
        if [ -n "$l" ] && [ "$l" -ge 2 ]; then bad+=("$i"); else good+=("$i"); fi
    done
    order=("${vi[@]}")
    if [ "$single" = 1 ]; then
        if [ "${#bad[@]}" -gt 0 ] && [ "${#fbi[@]}" -eq 0 ]; then
            a="${addr[${vi[0]}]}"; l="${loss[$a]}"
            for c in "${cand[@]}"; do [ "${loss[$c]-}" = 0 ] && { fb="$c"; break; }; done
            if [ -n "${ohas[f:rotate]+x}" ]; then
                # rotate: glibc начинает запросы по очереди с каждого сервера, запасной
                # стал бы равноправным первым — и внутренние имена хостера получали бы
                # NXDOMAIN от публичного. Только предупреждаем.
                log_warning "$(tf dns_tune_no_fallback_rotate "$a" "$l")"
                log_to_file "dns_tune: $a lost $l of 8, no fallback added: options rotate"
            elif [ -n "$fb" ]; then
                pend+=("$(tf dns_tune_fallback "$a" "$l" "$fb")")
            else
                local cl=""; for c in "${cand[@]}"; do cl+="${cl:+, }$c"; done
                log_warning "$(tf dns_tune_no_fallback "$a" "$l" "$cl")"
            fi
        fi
    elif [ "${#bad[@]}" -gt 0 ] && [ -n "$priv" ]; then
        local -a would=("${good[@]}" "${bad[@]}")
        for e in "${bad[@]}"; do
            l="${loss[${addr[$e]}]}"
            if [ "${would[*]}" = "${vi[*]}" ]; then         # сбоящие и так ниже здоровых
                log_info "$(tf dns_tune_slow_kept "${addr[$e]}" "$l")"
            else
                log_warning "$(tf dns_tune_slow_private "${addr[$e]}" "$l" "$priv")"
            fi
            log_to_file "dns_tune: ${addr[$e]} lost $l of 8, order kept: private address $priv in the file"
        done
    elif [ "${#bad[@]}" -gt 0 ] && [ "${#good[@]}" -eq 0 ]; then
        local lst=""
        for e in "${bad[@]}"; do lst+="${lst:+, }${addr[$e]} ${loss[${addr[$e]}]}/8"; done
        log_warning "$(tf dns_tune_all_bad "$lst")"
    elif [ "${#bad[@]}" -gt 0 ]; then
        order=("${good[@]}" "${bad[@]}")
        local moved=0; [ "${order[*]}" = "${vi[*]}" ] || moved=1
        for e in "${bad[@]}"; do
            l="${loss[${addr[$e]}]}"
            if [ "$moved" = 1 ]; then
                pend+=("$(tf dns_tune_slow "${addr[$e]}" "$l")")
            else
                log_info "$(tf dns_tune_slow_kept "${addr[$e]}" "$l")"
            fi
        done
    fi

    # ── Сборка: исходные nameserver — в прежние гнёзда в новом порядке; сразу за
    # последним гнездом — запасные с метками (прежние, затем новый); прочее —
    # дословно на своих местах ──
    local -a out=() curb=()
    local k=0 marks=0 orphans=0 opt_done=0 j
    for i in "${!L[@]}"; do
        case "${kind[$i]}" in
            mark) marks=$((marks + 1)); continue ;;
            orphan) orphans=$((orphans + 1)); continue ;;
            fbmark|fbns) ;;
            ns)
                out+=("${L[${order[$k]}]}"); k=$((k + 1))
                if [ "$k" -eq "${#order[@]}" ]; then
                    for j in "${fbi[@]}"; do out+=("${L[$((j - 1))]}" "${L[$j]}"); done
                    [ -z "$fb" ] || out+=("$DNS_TUNE_FB_MARK" "nameserver $fb")
                fi ;;
            opt) [ "$opt_done" = 1 ] || { out+=("$opt_line"); opt_done=1; } ;;
            *) out+=("${L[$i]}") ;;
        esac
        curb+=("${L[$i]}")
    done
    [ "$opt_done" = 1 ] || out+=("$opt_line")

    local ns_before="" ns_after=""
    for i in "${vi[@]}"; do ns_before+="${ns_before:+, }${addr[$i]}"; done
    for e in "${order[@]}"; do ns_after+="${ns_after:+, }${addr[$e]}"; done
    for j in "${fbi[@]}"; do ns_after+=", ${addr[$j]}"; done
    [ -z "$fb" ] || ns_after+=", $fb"

    printf '%s\n' "${curb[@]}" > "$_DT_WORK/cur_body"
    printf '%s\n' "${out[@]}"  > "$_DT_WORK/new_body"
    if cmp -s "$_DT_WORK/cur_body" "$_DT_WORK/new_body" && [ "$marks" -le 1 ] && [ "$orphans" = 0 ]; then
        log_info "$(tf dns_tune_ok "$conf")"; log_to_file "dns_tune: $conf already tuned, unchanged"
        return 0
    fi
    { cat "$_DT_WORK/new_body"; printf '%s\n' "$DNS_TUNE_MARK"; } > "$_DT_WORK/new" || {
        log_warning "$(tf dns_tune_nowrite "$conf")"; return 0; }
    cmp -s "$_DT_WORK/new" "$_DT_WORK/cur" && return 0

    # ── Запись: копия исходника один раз, tmp рядом, замена на месте, сверка ──
    if [ ! -w "$conf" ]; then
        log_warning "$(tf dns_tune_nowrite "$conf")"; log_to_file "dns_tune: $conf not writable, untouched"
        return 0
    fi
    if ! cmp -s "$_DT_WORK/cur" "$conf"; then
        log_warning "$(tf dns_tune_raced "$conf")"; log_to_file "dns_tune: $conf changed during the check, untouched"
        return 0
    fi
    if [ ! -e "$orig" ] && [ ! -L "$orig" ]; then
        if ! { cp -p "$conf" "$orig" 2>/dev/null && cmp -s "$conf" "$orig"; }; then
            rm -f "$orig" 2>/dev/null
            log_warning "$(tf dns_tune_nobackup "$orig")"; log_to_file "dns_tune: cannot save $orig, $conf untouched"
            return 0
        fi
    fi
    local dir
    case "$conf" in */*) dir="${conf%/*}" ;; *) dir=. ;; esac
    [ -n "$dir" ] || dir=/
    _DT_TMP=$(mktemp "$dir/.gotelegram-resolv.XXXXXX" 2>/dev/null) || _DT_TMP=""
    if [ -z "$_DT_TMP" ] || ! cat "$_DT_WORK/new" > "$_DT_TMP" 2>/dev/null || ! cmp -s "$_DT_WORK/new" "$_DT_TMP"; then
        log_warning "$(tf dns_tune_nowrite "$conf")"; log_to_file "dns_tune: cannot write temp file in $dir, $conf untouched"
        return 0
    fi
    if _dns_write_in_place "$_DT_TMP" "$conf" 2>/dev/null && cmp -s "$_DT_TMP" "$conf"; then
        local msg sopt sopt_before
        for msg in "${pend[@]}"; do log_warning "$msg"; done
        sopt=$(_dns_safe "$opt_line"); sopt_before=$(_dns_safe "${opts_before:-none}")
        log_info "$(tf dns_tune_done "$conf" "$ns_after" "$sopt" "$orig")"
        log_to_file "dns_tune: $conf updated; nameserver: $ns_before -> $ns_after; options: '$sopt_before' -> '$sopt'${fb:+; fallback added below: $fb}; original: $orig"
        return 0
    fi
    if _dns_write_in_place "$_DT_WORK/cur" "$conf" 2>/dev/null && cmp -s "$_DT_WORK/cur" "$conf"; then
        log_warning "$(tf dns_tune_restored "$conf")"; log_to_file "dns_tune: write to $conf failed, previous content restored"
    else
        log_warning "$(tf dns_tune_restore_fail "$conf" "$orig")"; log_to_file "dns_tune: write to $conf failed and restore failed; original: $orig"
    fi
    return 0
}

# ── Гигиена логов ─────────────────────────────────────────────────────────────
# telemt на log_level=normal пишет ~66МБ/сутки в journald (handshake/health на
# каждое соединение), а rsyslog ДУБЛИРУЕТ это в /var/log/syslog → диск съедается
# ~100МБ/день. Ограничиваем journald и убираем дубль telemt из syslog. Свежая
# диагностика остаётся в journal (кольцо 200M). Идемпотентно.
apply_log_hygiene() {
    local changed=0 rc=0
    # Дубль telemt из syslog убираем здесь — это не про бюджет, а про то, что
    # rsyslog писал те же строки ВТОРОЙ раз в /var/log/syslog.
    local rs="/etc/rsyslog.d/00-gotelegram-telemt.conf"
    if command -v rsyslogd >/dev/null 2>&1 && [ ! -f "$rs" ]; then
        printf 'if $programname == "telemt" then stop\n' > "$rs" 2>/dev/null && changed=1
    fi
    if [ "$changed" = "1" ]; then
        # Перезапуск rsyslog — всё, что здесь нужно: drop-in выше прекращает
        # ДУБЛИРОВАНИЕ строк telemt, а это и была задача.
        #
        # Раньше следом шли `rm -f /var/log/syslog.*` и `truncate -s0
        # /var/log/syslog`. Это чужие данные: syslog — общесистемный журнал, в
        # нём попытки входа по SSH, сообщения ядра, cron и все прочие службы.
        # Установка стирала всю историю машины без спроса, в том числе следы
        # взлома, случившегося ДО установки, и восстановить её было нечем —
        # бэкап продукта системный syslog не содержит. Задаче про дубликаты
        # такое удаление несоразмерно: под нож шли и строки, к telemt отношения
        # не имеющие.
        #
        # Место освобождают штатная ротация и бюджет логов ниже: logb_enforce
        # сносит только самые старые архивы и только при перерасходе.
        command -v rsyslogd >/dev/null 2>&1 && systemctl restart rsyslog 2>/dev/null || true
        log_dim "Дубль строк telemt в /var/log/syslog остановлен; уже накопленное уберёт ротация."
    fi

    # Потолки, ротация и сторож — в lib/logbudget.sh. Раньше здесь стоял только
    # потолок journald (200M), а /var/log/nginx и собственные логи продукта не
    # ротировались вовсе; заполненный раздел кладёт nginx и sshd — они встают на
    # записи в лог, принимают TCP и молчат, и на сервер уже не зайти.
    if type logb_apply_all >/dev/null 2>&1; then
        logb_apply_all; rc=$?
        log_info "Бюджет логов: ≤${LOGB_COMPONENT_MB}МБ на компонент (журнал, nginx, логи продукта, atop), ротация по размеру каждый час, сторож каждые 15 минут, аварийный порог ${LOGB_KEEP_FREE_MB}МБ свободного места"
        [ "$rc" = "10" ] && log_info "Урезание журнала telemt в источнике применится при следующем перезапуске telemt (≈98% строк — рутинные INFO об успехе)"
    fi
    return 0
}

# ── Выбор версии ядра telemt ─────────────────────────────────────────────────
# Дефолт (рекомендованная) — ПОСЛЕДНЯЯ версия; стабильный пин доступен как запасной, чтобы битый
# свежий релиз ядра не ломал установки. Сохраняем выбор в config.json telemt_version.
choose_telemt_version() {
    local meta latest latest_meta pin_meta pin_d pin_f lat_d lat_f src ans v
    meta=$(_telemt_releases_meta 2>/dev/null || echo "")
    latest=$(get_latest_telemt_version 2>/dev/null || echo "?")
    latest_meta=$(printf '%s\n' "$meta" | grep "^${latest}|" | head -1)
    pin_meta=$(printf '%s\n' "$meta" | grep "^${TELEMT_PINNED_VERSION}|" | head -1)
    pin_d=$(echo "$pin_meta" | cut -d'|' -f2); pin_f=$(echo "$pin_meta" | cut -d'|' -f3)
    lat_d=$(echo "$latest_meta" | cut -d'|' -f2); lat_f=$(echo "$latest_meta" | cut -d'|' -f3)
    local cur curpref m1 m2
    cur=$(get_installed_telemt_version 2>/dev/null || echo "")
    curpref=$(read_config_or_default telemt_version "" 2>/dev/null)
    m1=""; m2=""
    [ -n "$cur" ] && [ "$cur" = "$TELEMT_PINNED_VERSION" ] && m1="  ${GREEN}← установлена${NC}"
    [ -n "$cur" ] && [ "$cur" = "$latest" ] && m2="  ${GREEN}← установлена${NC}"
    src=""; if [ -t 0 ]; then src=/dev/stdin; elif { : < /dev/tty; } 2>/dev/null; then src=/dev/tty; fi
    if [ -z "$src" ]; then
        TELEMT_VERSION_PREF="latest"
        bot_update_config_field telemt_version "latest" >/dev/null 2>&1 || true
        return 0
    fi
    echo "" >&2
    echo -e "  ${BOLD}Версия ядра telemt${NC}" >&2
    echo -e "  ${DIM}Установлено сейчас: ${cur:-нет} · настройка: ${curpref:-рекомендованная}${NC}" >&2
    echo -e "  ${GREEN}1)${NC} Рекомендованная (последняя) — ${latest}${lat_d:+ · $lat_d}${lat_f:+ · $lat_f}${m2}" >&2
    echo -e "  ${GREEN}2)${NC} Стабильная (протестированная) — ${TELEMT_PINNED_VERSION}${pin_d:+ · $pin_d}${pin_f:+ · $pin_f} ${DIM}(запасная)${NC}${m1}" >&2
    echo -e "  ${GREEN}3)${NC} Выбрать из списка / ввести тег" >&2
    printf '  > ' >&2; read -r ans < "$src" || ans=1
    case "$ans" in
        2) TELEMT_VERSION_PREF="pinned"; bot_update_config_field telemt_version "pinned" >/dev/null 2>&1 || true
           log_info "Версия ядра: стабильная (${TELEMT_PINNED_VERSION})" >&2 ;;
        3) echo "  Доступные версии (новые сверху):" >&2
           printf '%s\n' "$meta" | head -12 | awk -F'|' '{printf "     %-9s · %s · %s\n",$1,$2,$3}' >&2
           printf '  Введите тег (напр. %s): ' "$TELEMT_PINNED_VERSION" >&2; read -r v < "$src" || v=""
           v=$(echo "$v" | tr -d '[:space:]')
           # Тег уходит в URL GitHub API — "../" увёл бы на чужой репозиторий.
           if [ -n "$v" ] && ! [[ "$v" =~ ^v?[0-9]+(\.[0-9]+){1,3}$ ]]; then
               log_warning "Неверный формат тега: ${v} — беру рекомендованную версию" >&2; v=""
           fi
           if [ -n "$v" ]; then TELEMT_VERSION_PREF="$v"; bot_update_config_field telemt_version "$v" >/dev/null 2>&1 || true; log_info "Версия ядра: ${v}" >&2
           else TELEMT_VERSION_PREF="latest"; bot_update_config_field telemt_version "latest" >/dev/null 2>&1 || true; fi ;;
        *) TELEMT_VERSION_PREF="latest"; bot_update_config_field telemt_version "latest" >/dev/null 2>&1 || true
           log_info "Версия ядра: рекомендованная — последняя (${latest})" >&2 ;;
    esac
    return 0
}

# Сменить версию ядра из меню: выбрать + применить (скачать целевую + перезапустить).
telemt_version_change() {
    choose_telemt_version
    log_step "Применяю выбранную версию ядра telemt..."
    auto_update_telemt_if_possible
    log_success "Версия ядра telemt: $(get_installed_telemt_version 2>/dev/null || echo '?')"
}

# ── Версии: _ver_lt A B → 0 если A строго меньше B (semver-ish) ──
_ver_lt() {
    [ "$1" = "$2" ] && return 1
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V 2>/dev/null | head -1)" = "$1" ]
}

# Маппинг старого пресета (до 2.7.4) → новый тир-3 (та же маскировка, без SYN-лимита).
# Старых режимов было 10, осталось три (1, 2, 4). Всё, что не совпало —
# уезжает на #4 (MEKO style): это дефолт продукта, и вместе с ним включается
# внешний SYN-фикс V3 (умная блокировка без лимита на число устройств).
_migrate_map_old_preset() {
    case "$1" in
        1) echo 1 ;; 2) echo 2 ;; 4) echo 4 ;;
        *) echo 4 ;;
    esac
}

# Предложение реаниматора при апгрейде со старой версии (<2.7.4).
# Интерактивно — спрашиваем; неинтерактивно (авто-апдейт) — сохраняем прежнее поведение
# (НЕ включаем SYN-лимит молча, чтобы не сломать клиентов за общим IP).
# gt_migrate_normalize_mode — привести режим к живой модели ПРИ ЛЮБОМ апгрейде.
# Гейт _ver_lt "$MIGRATED_FROM" "2.7.4" отсекал всех реальных пользователей
# (2.7.4-2.9.2): у них preset_id оставался 3/5..10 — режимов, которых больше нет,
# из-за чего конфиг молча уезжал на legacy-профиль с MSS 92, а SYN-защита
# исчезала совсем (встроенный synlimit вычищается, внешний V3 никто не ставил).
# Отдельно спасаем сервер, у которого ME через WARP реально не проходит: ME-пул
# не поднимается, и все клиенты получают «Telegram handshake timeout». Это НЕ
# свойство связки WARP+middle-proxy — порт 8888 через Cloudflare проходит
# (замер 23.08.2026, см. шапку lib/warp_egress.sh), — а состояние конкретной
# машины. Поэтому проверяем пробой, а не по конфигу, и чиним в момент
# обновления, не дожидаясь вотчдога.
gt_migrate_rescue_warp_middleproxy() {
    type _warp_middle_proxy_on >/dev/null 2>&1 || return 0
    _warp_middle_proxy_on || return 0
    # Не запрещаем связку как таковую: у части серверов порт ME через Cloudflare проходит.
    # Вмешиваемся, только если он реально не проходит — тогда сервер прямо сейчас лежит.
    type _warp_reach_via_warp >/dev/null 2>&1 || return 0
    ip addr show "${WARP_IFACE:-gtwarp}" >/dev/null 2>&1 || return 0
    _warp_reach_via_warp && return 0
    local was=0
    { type warp_egress_is_on >/dev/null 2>&1 && warp_egress_is_on; } && was=1
    { type _warp_routing_present >/dev/null 2>&1 && _warp_routing_present; } && was=1
    [ "$was" = "1" ] || return 0
    log_warning "Порт middle-proxy через Cloudflare не проходит — прокси сейчас без связи с Telegram. Выключаю WARP-egress и возвращаю прямой egress."
    type warp_egress_disable >/dev/null 2>&1 && warp_egress_disable >/dev/null 2>&1
    type _warp_teardown_routing >/dev/null 2>&1 && _warp_teardown_routing >/dev/null 2>&1
    systemctl restart telemt >/dev/null 2>&1 || true
    log_success "WARP-egress выключен, telemt перезапущен на прямом egress."
    return 0
}

# Лимит в один уникальный IP рвёт обычный телефон: при переходе Wi-Fi ↔ LTE адрес
# меняется, старые соединения ещё живы, и клиент отбивается собственным лимитом.
# Замер на боевом сервере: 19 эпизодов за полтора часа, медиана 20 секунд без связи,
# максимум 374 секунды. С этой версии единицу нельзя выставить ни в SSH, ни в боте,
# ни в панели — а уже проставленную поднимаем до двойки и говорим, почему.
# Ноль не трогаем: это «нет персонального лимита», осмысленное значение.
gt_migrate_bump_ip_limit_one() {
    local cfg="${TELEMT_CONFIG:-/etc/telemt/config.toml}" n tmp
    [ -f "$cfg" ] || return 0
    # Считаем и правим строго внутри своей секции: полагаться на «до конца файла»
    # нельзя — секция не обязана быть последней, а "x = 1" встречается и в других.
    n=$(awk '
        /^\[/ { insec = ($0 == "[access.user_max_unique_ips]") }
        insec && /^"[^"]*"[[:space:]]*=[[:space:]]*1[[:space:]]*$/ { c++ }
        END { print c+0 }' "$cfg" 2>/dev/null)
    [ "${n:-0}" -gt 0 ] 2>/dev/null || return 0
    # Временный файл РЯДОМ с целью, а не в /tmp: подменять файл через `mv` можно
    # только в пределах одной файловой системы. Права выставляем сразу — конфиг
    # содержит ключи и не должен ни секунды лежать с правами по умолчанию.
    tmp=$(mktemp "${cfg}.XXXXXX") || return 0
    chmod 600 "$tmp" 2>/dev/null || true
    awk '
        /^\[/ { insec = ($0 == "[access.user_max_unique_ips]") }
        insec && /^"[^"]*"[[:space:]]*=[[:space:]]*1[[:space:]]*$/ { sub(/=[[:space:]]*1[[:space:]]*$/, "= 2") }
        { print }' "$cfg" > "$tmp" 2>/dev/null
    # Здесь стояло `cat "$tmp" > "$cfg"` — усечение боевого конфига и запись
    # поверх. Обрыв на середине (кончилось место — а ради этого и написан весь
    # logbudget.sh, питание, OOM) оставлял /etc/telemt/config.toml обрезанным,
    # то есть ТЕРЯЛ ВСЕ КЛЮЧИ, причём бэкапа перед миграцией никто не делает.
    # Проверка была только «файл непустой». Приводим к тому же порядку, каким
    # пишет lib/telemt_config.sh:240-246 — единственный писатель этого файла,
    # делавший всё правильно: убедиться, что секция ключей на месте, сбросить на
    # диск, подменить атомарно.
    if [ ! -s "$tmp" ] || ! grep -q '^\[access\.users\]' "$tmp"; then
        log_warning "Миграция лимита IP пропущена: результат не прошёл проверку, конфиг не тронут"
        rm -f "$tmp" 2>/dev/null
        return 0
    fi
    sync "$tmp" 2>/dev/null || true
    if mv -f "$tmp" "$cfg"; then
        log_warning "$(tf mig_iplimit_bumped "$n")" && log_dim "$(t mig_iplimit_why)"
        restart_telemt >/dev/null 2>&1 || true
    else
        log_error "Миграция лимита IP: не удалось заменить $cfg"
        rm -f "$tmp" 2>/dev/null
    fi
    return 0
}

gt_migrate_normalize_mode() {
    local old newp
    old=$(read_config_or_default preset_id "")
    [[ "$old" =~ ^[0-9]+$ ]] || old=4
    newp=$(_migrate_map_old_preset "$old")
    if [ "$newp" != "$old" ]; then
        bot_update_config_field preset_id "$newp" >/dev/null 2>&1
        REANIM_PRESET_CHANGED=1
        log_info "Режим #${old} удалён в этой версии → переведён на #${newp} (MEKO style)."
    fi
    # Раньше защита жила во встроенном synlimit ядра; теперь её место занял
    # внешний V3. Апгрейд обязан довести человека до рабочего состояния, а не
    # оставить без обоих механизмов.
    # Основной режим продукта — «без ограничения»: он никого не режет. Раньше
    # сюда шёл вызов без MEKOFIX_NOLIMIT, дефолт давал 0, и после апгрейда человек
    # оказывался на лимитированном режиме, которого не выбирал. Если фикс уже
    # стоял — сохраняем его вариант, чтобы не менять поведение молча.
    # Человек сознательно выбрал V4 — апгрейд не имеет права молча снести его
    # и поставить V3. Ченджлог обещает «настройки SYN-фикса сохранятся», и это
    # должно быть правдой.
    if type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null; then
        log_dim "Активен SYN-фикс V4 — оставляю как есть."
        return 0
    fi
    local _keep_nl
    _keep_nl=$(mekofix_state_get nolimit 2>/dev/null || echo "")
    [ -z "$_keep_nl" ] && _keep_nl=1
    MEKOFIX_NOLIMIT="$_keep_nl" gt_ensure_synfix v3 || true
}

_migrate_reanimator_offer() {
    local old_preset newp src ans
    old_preset=$(read_config_or_default preset_id "1"); [[ "$old_preset" =~ ^[0-9]+$ ]] || old_preset=1
    newp=$(_migrate_map_old_preset "$old_preset")
    src=""
    if [ -t 0 ]; then src=/dev/stdin; elif { : < /dev/tty; } 2>/dev/null; then src=/dev/tty; fi
    if [ -z "$src" ]; then
        bot_update_config_field preset_id "$newp" >/dev/null 2>&1; REANIM_PRESET_CHANGED=1
        log_info "Авто-обновление: сохранён прежний стиль работы (режим #${newp}, без SYN-лимита). Режим работы меняется в меню «Прокси → 1) Режим работы»."
        return 0
    fi
    echo "" >&2
    echo -e "  ${BOLD}${CYAN}Обновление: добавлен РЕАНИМАТОР — новые алгоритмы стабильности${NC}" >&2
    echo -e "  ${DIM}────────────────────────────────────────────────────────────${NC}" >&2
    echo "  Новые режимы #1-5 включают SYN-лимит на IP + поджатые таймауты ядра —" >&2
    echo "  это держит прокси стабильнее под DPI-пробами и сканерами." >&2
    echo "" >&2
    echo -e "  ${YELLOW}! Важно про лимит на один IP:${NC}" >&2
    echo "    SYN-лимит ограничивает новые подключения с ОДНОГО IP-адреса." >&2
    echo "    Если несколько устройств выходят через общий IP (домашний роутер/" >&2
    echo "    NAT/VPN) — часть их подключений будет отбрасываться." >&2
    echo -e "    ${BOLD}=> Выдавай отдельный ключ каждому устройству${NC} (доп. ключи есть в боте/меню)." >&2
    echo "" >&2
    echo "  Твой текущий режим: #${old_preset}. Что делаем?" >&2
    echo -e "  ${GREEN}1)${NC} Перейти на реаниматор - режим #1 (SYN-лимит + таймауты)" >&2
    echo -e "  ${GREEN}2)${NC} Оставить как было - режим #${newp} (та же маскировка, без SYN-лимита)" >&2
    printf '  > ' >&2
    read -r ans < "$src" || ans=2
    case "$ans" in
        1) bot_update_config_field preset_id 1 >/dev/null 2>&1; REANIM_PRESET_CHANGED=1
           log_success "Включён реаниматор (режим #1). Совет: выдай отдельный ключ на каждое устройство." ;;
        *) bot_update_config_field preset_id "$newp" >/dev/null 2>&1; REANIM_PRESET_CHANGED=1
           log_info "Оставлено прежнее поведение (режим #${newp}, без SYN-лимита)." ;;
    esac
}

auto_migrate_legacy_state() { gt_with_config_lock _auto_migrate_legacy_state_locked "$@"; }
_auto_migrate_legacy_state_locked() {
    # Чистая установка этой же версии — мигрировать нечего. Раньше функция
    # выходила ниже по проверке файлов, НЕ поставив маркер, и человек при каждом
    # следующем заходе видел «Миграция состояния… Продолжить обновление?» и
    # вопрос про версию ядра — плюс лишний архив на диск каждый раз.
    if [ "$(read_config_or_default version "")" = "$GOTELEGRAM_VERSION" ] \
       && [ -f "$GOTELEGRAM_CONFIG" ] && [ -z "${MIGRATED_FROM:-}" ]; then
        touch "$GOTELEGRAM_DIR/.migrated_${GOTELEGRAM_VERSION}" 2>/dev/null || true
        touch "$GOTELEGRAM_DIR/.preupgrade_${GOTELEGRAM_VERSION}_done" 2>/dev/null || true
        return 0
    fi
    local marker="$GOTELEGRAM_DIR/.migrated_${GOTELEGRAM_VERSION}"
    local current_version
    current_version=$(read_config_or_default version "")
    if [ -f "$marker" ] && [ "$current_version" = "$GOTELEGRAM_VERSION" ]; then
        return 0
    fi
    # запомним, с какой версии обновляемся — для показа ченджлога в main()
    if [ -n "$current_version" ] && [ "$current_version" != "$GOTELEGRAM_VERSION" ]; then
        MIGRATED_FROM="$current_version"
    fi

    # Мигрировать можно только состояние, а оно живёт в конфигах. Каталог сайта
    # сам по себе состоянием не является: после неполной очистки он остаётся, и
    # человек на ЧИСТОЙ машине получал вопрос «Продолжить обновление?» — при том
    # что обновлять нечего, ключей нет, конфигов нет.
    # Гейт строго по config.json: он пишется в самом КОНЦЕ установки, то есть
    # означает «установка когда-то завершилась». Один config.toml без него —
    # оборванная установка: «миграция» здесь лишь маскировала обрыв (telemt лежит),
    # а ключи из toml и так подхватит install_*_mode через gt_preserve_existing.
    [ -f "$GOTELEGRAM_CONFIG" ] || return 0

    log_step "Миграция состояния goTelegram Pro"
    # Мягкая миграция: показываем пользователю, ЧТО будет сделано, и спрашиваем (только в интерактиве).
    if [ -t 0 ] || { : < /dev/tty; } 2>/dev/null; then
        echo "" >&2
        echo -e "  ${BOLD}${WHITE}$(t mig_plan_title)${NC}" >&2
        echo -e "  ${DIM}• $(t mig_plan_keep)${NC}" >&2
        echo -e "  ${DIM}• $(t mig_plan_upd)${NC}" >&2
        echo -e "  ${DIM}• $(t mig_plan_backup)${NC}" >&2
        if ! confirm "$(t mig_plan_ask)"; then
            log_info "$(t mig_cancelled)" >&2
            return 1
        fi
    fi
    # Нет бэкапа — нет миграции: раньше провал tar проглатывался и конфиг
    # перезаписывался без возможности отката.
    snapshot_preupgrade_state || return 1

    local mode port secret mask_host domain mask_port tpl_id tpl_source users_block tls_emulation changed=0 users_block_needs_write=0
    users_block=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null || true)
    secret=$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$secret" ] && secret=$(read_config_or_default secret "")
    [ -z "$secret" ] && secret=$(first_telemt_user_secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$secret" ] && secret=$(generate_hex 32)

    if [ -n "$users_block" ] && ! telemt_users_block_has_main "$users_block"; then
        users_block=$(printf 'main = "%s"\n%s\n' "$secret" "$users_block")
        users_block_needs_write=1
    fi
    if [ -z "$users_block" ]; then
        users_block="main = \"$secret\""
        users_block_needs_write=1
    fi

    port=$(get_config_value port "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$port" ] && port=$(read_config_or_default port "443")
    [[ "$port" =~ ^[0-9]+$ ]] || port=443

    mask_host=$(get_config_value mask_host "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$mask_host" ] && mask_host=$(read_config_or_default mask_host "google.com")
    domain=$(read_config_or_default domain "")
    mask_port=$(get_config_value mask_port "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$mask_port" ] && mask_port="443"
    tls_emulation=$(toml_bool_value censorship tls_emulation "$TELEMT_CONFIG" 2>/dev/null || echo "")

    mode=$(read_config_or_default mode "")
    if [ -z "$mode" ]; then
        if [ -n "$domain" ] || [ "$tls_emulation" = "false" ] || grep -q 'dns_overrides' "$TELEMT_CONFIG" 2>/dev/null; then
            mode="pro"
        else
            mode="lite"
        fi
    fi
    if [ "$mode" = "pro" ]; then
        [ -z "$domain" ] && domain="$mask_host"
        [ -n "$domain" ] && mask_host="$domain"
        [ "$mask_port" = "443" ] && mask_port="8443"
    else
        domain=""
        mask_port="443"
    fi

    tpl_id=$(detect_deployed_template_id)
    tpl_source=$(detect_template_source || echo "")
    if [ -d "$WEBSITE_ROOT" ] && [ -f "$WEBSITE_ROOT/index.html" ] && [ -n "$tpl_id" ]; then
        echo "$tpl_id" > "$WEBSITE_ROOT/.gotelegram_template_id" 2>/dev/null || true
        [ -n "$tpl_source" ] && echo "$tpl_source" > "$WEBSITE_ROOT/.gotelegram_template_source" 2>/dev/null || true
    fi

    # Апгрейд со старой версии (<2.7.4, без реаниматора): предложить переход или сохранить режим
    if [ -n "${MIGRATED_FROM:-}" ] && _ver_lt "${MIGRATED_FROM:-}" "2.7.4"; then
        _migrate_reanimator_offer
    fi
    # Любой апгрейд: сначала вытащить сервер, если WARP включён при middle-proxy —
    # такой сервер прямо сейчас не пропускает ни одного клиента.
    gt_migrate_rescue_warp_middleproxy
    # Лимит в один адрес был разрешён до 2.10.3 — поднимаем до минимально рабочего.
    gt_migrate_bump_ip_limit_one
    # Любой апгрейд: режим привести к живому списку {1,2,4} и поднять V3.
    [ -n "${MIGRATED_FROM:-}" ] && gt_migrate_normalize_mode
    # Версии до 2.8 писали в telemt_version ЛИТЕРАЛЬНЫЙ тег автоматически, без
    # участия человека. Резолвер трактует любой тег как осознанный пин — и ядро
    # оставалось на 3.4.18 навсегда, причём вопрос о версии больше не задавался.
    # Отличить «пин от пользователя» от «артефакт старой версии» можно только
    # здесь: если апгрейд идёт с версии, которая ещё не умела слов pinned/latest,
    # и записан ровно её дефолтный тег — считаем это артефактом и возвращаем
    # «рекомендованную». Тег, выбранный руками в меню, останется нетронутым.
    if [ -n "${MIGRATED_FROM:-}" ] && _ver_lt "${MIGRATED_FROM}" "2.8.0"; then
        local _tv; _tv=$(read_config_or_default telemt_version "")
        if [ "$_tv" = "${TELEMT_PINNED_VERSION:-3.4.18}" ]; then
            bot_update_config_field telemt_version "recommended" >/dev/null 2>&1
            log_info "Версия ядра была зафиксирована старой версией на ${_tv} — переключаю на рекомендованную."
        fi
    fi
    # Ключи прежней модели больше ни на что не влияют (SYN-фикс отвязан от пресета,
    # подменю жёсткости удалено). Оставлять их — вводить в заблуждение того, кто
    # заглянет в config.json. Чистим и заодно снимаем iOS-keepalive, если он был
    # включён старой версией: ручки выключения в новой уже нет.
    if [ -n "${MIGRATED_FROM:-}" ]; then
        for _dead in reanim_synlimit reanim_ios_keepalive; do
            if [ -n "$(read_config_or_default "$_dead" "")" ]; then
                bot_update_config_field "$_dead" "" >/dev/null 2>&1 || true
            fi
        done
        if [ -f /etc/sysctl.d/99-zz-gotelegram-keepalive.conf ] && type diagnose_ios_keepalive >/dev/null 2>&1; then
            diagnose_ios_keepalive off >/dev/null 2>&1 || true
        fi
    fi

    # Выбор версии ядра telemt при обновлении — один раз, если ещё не выбрано.
    # Интерактивно спросит (рекоменд./последняя/тег), неинтерактивно поставит пин.
    if [ -z "$(read_config_or_default telemt_version "")" ] && type choose_telemt_version >/dev/null 2>&1; then
        choose_telemt_version
    fi

    # Фикс read-only рабочей папки telemt (beobachten/cache -> WARN каждые 15с): дать пишемую папку
    if [ -f "/etc/systemd/system/${TELEMT_SERVICE}.service" ] && \
       ! grep -q 'WorkingDirectory=/var/lib/telemt' "/etc/systemd/system/${TELEMT_SERVICE}.service" 2>/dev/null; then
        mkdir -p /var/lib/telemt 2>/dev/null || true
        if type install_telemt_service >/dev/null 2>&1; then
            install_telemt_service "$TELEMT_CONFIG" >/dev/null 2>&1 || true
            changed=1
            log_info "telemt: добавлена пишемая рабочая папка /var/lib/telemt (убирает WARN beobachten каждые 15с)"
        fi
        # Уборка «сиротских» служебных файлов telemt со старого рабочего пути (/root, /etc/telemt).
        # Точечные имена + rmdir только пустых каталогов — ничего лишнего не трогаем.
        local _d
        for _d in /root /etc/telemt; do
            rm -f "$_d/beobachten.txt" "$_d/proxy-secret" "$_d/telemt.limit.json" 2>/dev/null || true
            rm -f "$_d/cache/proxy-config-v"*.txt 2>/dev/null || true
            rmdir "$_d/cache" 2>/dev/null || true
        done
    fi

    if [ -f "$TELEMT_CONFIG" ]; then
        if ! grep -q '\[server.api\]' "$TELEMT_CONFIG" 2>/dev/null || \
           ! grep -q 'metrics_listen' "$TELEMT_CONFIG" 2>/dev/null || \
           ! grep -q "goTelegram Pro v${GOTELEGRAM_VERSION}" "$TELEMT_CONFIG" 2>/dev/null || \
           [ "${REANIM_PRESET_CHANGED:-0}" = "1" ]; then
            generate_telemt_toml "$secret" "$port" "$mode" "$mask_host" "$mask_port" "$TELEMT_CONFIG" >&2
            # Схема nginx зависит от WEB-прокси: обновляем и её, иначе после
            # обновления шаблон nginx и конфиг ядра разошлись бы.
            # Не только по флагу: если флаг уже снят, а конфиги остались от WEB
            # (сорвавшееся выключение), nginx ждал бы PROXY-заголовок, которого
            # свежий TOML не шлёт.
            if [ "$mode" = "pro" ] && { telemt_web_enabled 2>/dev/null \
                 || grep -q '^transport = "web"' "$TELEMT_CONFIG" 2>/dev/null \
                 || grep -q 'proxy_protocol;' "${NGINX_SITE_CONF:-/etc/nginx/sites-available/gotelegram}" 2>/dev/null; }; then
                generate_nginx_config "${domain:-$mask_host}" "$mask_port" >/dev/null 2>&1 \
                    && nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
            fi
            replace_telemt_users_block "$users_block" "$TELEMT_CONFIG"
            changed=1
            users_block_needs_write=0
        elif [ "$users_block_needs_write" = "1" ]; then
            replace_telemt_users_block "$users_block" "$TELEMT_CONFIG"
            changed=1
        fi
    fi

    # WEB, собранный до 2.10.46, без [web.limits]: у ядра по умолчанию 32 профиля,
    # и 33-й ключ (профиль на каждый ключ) ронял ядро при следующем рестарте.
    # Синхронизация профилей дописывает лимит; здесь — чтобы это случилось при
    # обновлении, а не в момент добавления ключа.
    if grep -q '^\[\[web\.vhosts\]\]' "$TELEMT_CONFIG" 2>/dev/null && ! grep -q '^\[web\.limits\]' "$TELEMT_CONFIG" 2>/dev/null; then
        if telemt_web_sync_profiles "$TELEMT_CONFIG" >/dev/null 2>&1 && grep -q '^\[web\.limits\]' "$TELEMT_CONFIG" 2>/dev/null; then
            log_info "WEB: добавлен предел профилей ядра (max_profiles = ${WEB_MAX_PROFILES:-4096})"
            changed=1
        fi
    fi

    write_normalized_gotelegram_config "$mode" "$port" "$secret" "$mask_host" "$domain" "$tpl_id" "$tpl_source" || \
        log_warning "Не удалось нормализовать config.json"

    # WARP-egress: со старой dev-схемы (2.8.0/2.8.1 — iface warp/table 51820) systemd-юниты
    # несовместимы с новой (gtwarp/table 7788). Снимаем стейл-юниты (имена уникальны для goTelegram),
    # НЕ трогая чужой goWARP. Дёшево, не блокирует. Для 2.7.x юнитов WARP нет — это no-op.
    if [ -n "${MIGRATED_FROM:-}" ] && _ver_lt "${MIGRATED_FROM:-}" "2.8.2" \
       && [ -f /etc/systemd/system/gotelegram-warp-watch.timer ] && ! ip addr show gtwarp >/dev/null 2>&1; then
        systemctl disable --now gotelegram-warp-watch.timer gotelegram-warp-egress.service >/dev/null 2>&1 || true
        rm -f /etc/systemd/system/gotelegram-warp-watch.timer /etc/systemd/system/gotelegram-warp-watch.service /etc/systemd/system/gotelegram-warp-egress.service 2>/dev/null || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        [ "$(read_config_or_default warp_egress "")" = "on" ] && log_info "WARP-egress обновлён на новую схему — включите заново в меню (Управление → WARP-egress)"
    fi

    if [ "$changed" = "1" ] && systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        log_info "Перезапускаю telemt, чтобы применить нормализованный конфиг..."
        restart_telemt || log_warning "telemt не перезапустился после миграции; проверьте journalctl -u telemt"
    fi

    apply_network_sysctl
    dns_resolver_tune
    apply_log_hygiene
    metrics_install_timer
    # Каталог ACME, созданный при umask 077/027 (700/750), давал nginx 403 —
    # продление сертификата и имя WEB падали. Чиним на каждом обновлении Pro.
    # Не тронули (например, владелец www-data), но nginx проходит — молчим.
    if [ "$mode" = "pro" ] && type acme_webroot_prepare >/dev/null 2>&1; then
        acme_webroot_prepare || acme_webroot_web_ok || log_warning "$(tf acme_webroot_left "$ACME_WEBROOT")"
    fi
    # Записываем версию в config.json. Раньше её туда не писал НИКТО: миграция
    # поле только читала, чтобы понять, откуда обновляемся, и ставила файл-маркер.
    # А бот и панель берут версию именно из config.json (там «единый источник
    # правды»), поэтому после каждого обновления SSH показывал новую версию, а
    # бот и панель — ту, что записалась при первой установке. Пользователи это и
    # заметили: «в боте старая версия».
    bot_update_config_field version "$GOTELEGRAM_VERSION" >/dev/null 2>&1 || true
    touch "$marker" 2>/dev/null || true
    log_success "Миграция завершена: ключи, режим, домен и сайт сохранены"
    # Обновление — удобный момент проверить запасной вход: адреса подняты,
    # sshd слушает на всех. Ждать пятиминутного тика сторожа незачем.
    type edgeips_ssh_notify >/dev/null 2>&1 && edgeips_ssh_notify || true
    # Миграция ≠ работающий прокси: после сбоя/обрыва telemt может лежать — говорим
    # об этом прямо, иначе человек уходит в уверенности, что всё поднято.
    if ! systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        log_warning "telemt не запущен после миграции — проверьте: systemctl status telemt"
    fi
}

# ── Install: mode selection ─────────────────────────────────────────────────
# Показ ченджлога при обновлении версии (вызывается из main() после баннера).
show_upgrade_changelog() {
    local from="${1:-?}"
    echo ""
    echo -e "  ${BOLD}${GREEN}$(tf upd_title "$from" "$GOTELEGRAM_VERSION")${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..58})${NC}"
    echo -e "  $(t upd_l1)"
    echo -e "  $(t upd_l2)"
    echo -e "  $(t upd_l3)"
    echo -e "  $(t upd_l4)"
    echo -e "  $(t upd_l5)"
    echo -e "  $(t upd_l6)"
    echo -e "  $(t upd_l7)"
    echo -e "  $(t upd_l8)"
    echo -e "  $(t upd_l9)"
    echo -e "  $(t upd_l10)"
    echo ""
    echo -ne "  ${DIM}$(t press_enter_to_return)${NC}"
    read -r _ || true
}

# ── Pre-flight проверки перед установкой ─────────────────────────────────────
# Кто занял порт: "имя (pid N)" или пусто, если свободен.
port_occupant() {
    local port="$1" line name pid
    line=$(ss -tlnp 2>/dev/null | grep -E ":${port}\b" | head -1)
    [ -z "$line" ] && line=$(netstat -tlnp 2>/dev/null | grep -E ":${port}\b" | head -1)
    [ -z "$line" ] && return 1
    name=$(printf '%s' "$line" | grep -oE 'users:\(\("[^"]+"' | head -1 | sed 's/.*(("//; s/"$//')
    pid=$(printf '%s' "$line" | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
    if [ -n "$name" ]; then echo "${name}${pid:+ (pid $pid)}"; else echo "$(t pf_unknown_proc)"; fi
    return 0
}

# Гейт ОС: нужна Ubuntu >= 24.04 (иначе отказ). Debian/прочее — предупреждение.
preflight_os_gate() {
    [ "${GOTELEGRAM_SKIP_OS_CHECK:-0}" = "1" ] && { log_warning "$(t pf_os_skip)"; return 0; }
    if [ ! -f /etc/os-release ]; then log_error "$(t pf_os_unknown)"; return 1; fi
    grep -qE '(;|`|\$\(|\$\{)' /etc/os-release 2>/dev/null && return 0
    . /etc/os-release
    local id="${ID:-}" ver="${VERSION_ID:-}" pretty="${PRETTY_NAME:-$ID}"
    case "$id" in
        ubuntu)
            if [ -z "$ver" ] || _ver_lt "$ver" "24.04"; then
                log_error "$(tf pf_os_old "$pretty")"
                log_dim "$(t pf_os_hint)"
                return 1
            fi
            log_dim "OS: $pretty ✓"; return 0 ;;
        debian)
            log_warning "$(t pf_os_debian)"; return 0 ;;
        *)
            log_warning "$(tf pf_os_other "$pretty")"; return 0 ;;
    esac
}

# Фаервол: активен ufw/firewalld и закрыты ли нужные порты.
preflight_firewall() {
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qiE '^Status:[[:space:]]*active'; then
        local p missing=""
        for p in "$@"; do
            ufw status 2>/dev/null | grep -qE "(^|[[:space:]])${p}(/tcp)?[[:space:]]+ALLOW" || missing="$missing $p"
        done
        if [ -n "$missing" ]; then
            log_warning "$(tf pf_ufw_active "$missing")"
            if confirm "$(t pf_ufw_open)"; then
                for p in $missing; do ufw allow "${p}/tcp" >/dev/null 2>&1 && log_info "ufw allow ${p}/tcp"; done
            fi
        fi
    fi
    if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state 2>/dev/null | grep -qi running; then
        log_warning "$(t pf_firewalld)"
    fi
}

# Порт держит наш собственный сервис? Переустановка поверх живой установки — норма:
# буквальный совет «остановите занявший сервис» про свой же telemt/nginx ронял прокси.
port_occupant_is_ours() {
    local name="${1%% *}"    # port_occupant печатает "имя (pid N)"
    case "$name" in
        telemt) systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null && return 0 ;;
        nginx)  systemctl is-active --quiet nginx 2>/dev/null && return 0 ;;
    esac
    return 1
}

# Порты для Pro/Ленивого: 443 (прокси+сайт) и 80 (выпуск сертификата LE). Возвращает 1 если конфликт.
preflight_ports_pro() {
    local occ blocked=0 p
    for p in 443 80; do
        occ=$(port_occupant "$p")
        if [ -n "$occ" ]; then
            # свой telemt/nginx конфликтом не считаем: установка их же и перезапустит
            if port_occupant_is_ours "$occ"; then
                log_dim "Порт ${p} занят нашим сервисом (${occ}) — будет перезапущен установкой."
            else
                log_warning "$(tf pf_port_busy "$p" "$occ")"; blocked=1
            fi
        fi
    done
    preflight_firewall 443 80
    return $blocked
}

# Порт для Lite: показать занявшего и предложить сменить. Печатает итоговый порт в stdout.
preflight_port_lite() {
    local port="$1" occ
    while :; do
        occ=$(port_occupant "$port")
        [ -z "$occ" ] && break
        # свой telemt на этом же порту = переустановка поверх, порт менять не нужно
        if port_occupant_is_ours "$occ"; then
            log_dim "Порт ${port} занят нашим сервисом (${occ}) — будет перезапущен установкой." >&2
            break
        fi
        log_warning "$(tf pf_port_busy "$port" "$occ")" >&2
        if confirm "$(t pf_port_change)"; then
            port=$(select_port) || return 1
        else
            log_warning "$(t pf_port_continue)" >&2; break
        fi
    done
    preflight_firewall "$port" >&2
    echo "$port"
}

menu_install() {
    # Pre-flight: гейт ОС (Ubuntu >= 24.04). Блокирует только СВЕЖУЮ установку.
    preflight_os_gate || return

    # Check for v1
    if detect_v1_installation; then
        echo ""
        echo -e "  ${YELLOW}$(t v1_detected)${NC}"
        echo -e "  ${DIM}$(tf v1_container "$V1_CONTAINER_NAME")${NC}"
        echo ""
        if ! migrate_v1_to_v2; then
            return
        fi
    fi


    # Дисклеймер-gate (на первой установке; кэш в $GOTELEGRAM_DIR/.disclaimer-accepted)
    show_disclaimer --gate || return

    echo ""
    echo -e "  ${BOLD}${WHITE}$(t install_select_mode)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -e "  ${CYAN}1)${NC} ${BOLD}${GREEN}$(t install_lazy_title)${NC}"
    echo -e "     ${DIM}$(t install_lazy_desc1)${NC}"
    echo -e "     ${DIM}$(t install_lazy_desc2)${NC}"
    echo ""
    echo -e "  ${CYAN}2)${NC} ${MAGENTA}$(t install_pro_title)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc1)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc2)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc3)${NC}"
    echo ""
    echo -e "  ${CYAN}3)${NC} ${GREEN}$(t install_lite_title)${NC}"
    echo -e "     ${DIM}$(t install_lite_desc1)${NC}"
    echo -e "     ${DIM}$(t install_lite_desc2)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -ne "  ${WHITE}$(t install_mode_choice)${NC} "
    # EOF (не-tty stdin / мёртвая сессия) → q: пустая строка здесь означала бы
    # «ленивую» установку, запущенную без человека.
    read -r mode_choice || mode_choice=q
    mode_choice="${mode_choice:-}"

    case "$mode_choice" in
        ""|1) GOTELEGRAM_LAZY=1 install_pro_mode ;;
        2) install_pro_mode ;;
        3) install_lite_mode ;;
        q) return 0 ;;
        *) log_error "$(tf install_bad_choice "${mode_choice:-<empty>}")" ;;
    esac
}

# ── Lite mode ───────────────────────────────────────────────────────────────
# Тело записи конфигов Lite: работает в локальных переменных install_lite_mode
# (secret/port/domain), вызывать только оттуда — через gt_with_config_lock.
_install_lite_apply_locked() {
    # Вызов БЕЗ $(…): подшелл терял GT_PRESERVED_USERS, и переустановка поверх
    # стирала все ключи, кроме main.
    gt_preserve_existing
    [ -n "${GT_PRESERVED_SECRET:-}" ] && secret="$GT_PRESERVED_SECRET"
    generate_telemt_toml "$secret" "$port" "lite" "$domain" "443"
    gt_restore_users

    # Validate
    validate_telemt_config || return

    # Start
    start_telemt || return

    # Save goTelegram Pro config
    save_gotelegram_config "telemt" "lite" "$port" "$secret" "$domain" "" ""
    # WEB-прокси бывает только в Pro: флаг из прежней установки здесь не нужен
    bot_update_config_field web_enabled "false" >/dev/null 2>&1 || true
    bot_update_config_field preset_id "${DIAG_DEFAULT_PRESET:-4}" >/dev/null 2>&1 || true
    # Пин ядра: choose_telemt_version отрабатывает ДО появления config.json, и его
    # запись молча проваливалась — первое обновление уводило ядро с выбранной версии.
    [ -n "${TELEMT_VERSION_PREF:-}" ] && bot_update_config_field telemt_version "$TELEMT_VERSION_PREF" >/dev/null 2>&1 || true
    # CC ядра читается из СОХРАНЁННОГО preset_id: до записи выше переустановка
    # видела старый пресет и ставила cubic, хотя режим объявлен MEKO (BBR).
    apply_network_sysctl
    apply_log_hygiene
    return 0
}

install_lite_mode() {
    log_step "$(t install_lite_step)"

    # Domain selection
    local domain
    domain=$(select_quick_domain)
    [ $? -ne 0 ] && return

    # Port selection
    local port
    port=$(select_port)
    [ $? -ne 0 ] && return
    # Pre-flight: занят ли выбранный порт — показать процесс и предложить сменить
    port=$(preflight_port_lite "$port") || return
    [ "$port" = "443" ] && { warn_3xui_443_conflict || true; }

    # Generate secret
    local secret
    secret=$(generate_hex 32)

    # Confirm
    local ip
    ip=$(get_server_ip)
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t install_config_title)${NC}"
    echo -e "  $(t install_cfg_ip)         ${CYAN}${ip}${NC}"
    echo -e "  $(t install_cfg_port)       ${CYAN}${port}${NC}"
    echo -e "  $(t install_cfg_mask) ${CYAN}${domain}${NC}"
    echo -e "  $(t install_cfg_mode)      ${GREEN}Lite${NC}"
    echo ""

    if ! confirm "$(t install_confirm_proxy)"; then
        return
    fi

    # Install
    ensure_deps
    # DNS сервера — до загрузок: ядро, шаблоны и certbot пойдут уже с настройкой
    dns_resolver_tune
    type deps_check >/dev/null 2>&1 && { deps_check core proxy || log_warning "$(t deps_some_missing)"; }
    choose_telemt_version
    install_telemt_full || return

    # Generate telemt config
    # Чистая установка: дефолтный анти-DPI пресет (MEKO style) + SYN-фикс V3.
    # V3 = умная блокировка по TCP-отпечатку с обучением доверенных IP; в отличие
    # от встроенного в ядро V2 не режет живых клиентов за общим NAT.
    TELEMT_PRESET_PREF="${DIAG_DEFAULT_PRESET:-4}"
    GT_WANT_SYNFIX=v3
    # Под общим config-замком (как apply_preset): ключ, созданный из бота/панели
    # между чтением блока пользователей и его возвратом, молча терялся.
    gt_with_config_lock _install_lite_apply_locked || return

    # ПОСЛЕ записи config.json: иначе gt_ensure_synfix не видел порт (брал 443)
    # и не мог сохранить mekofix=v3 — файла ещё не существовало.
    # Основной режим продукта — без ограничения. Без явного флага модуль берёт
    # дефолт «с ограничением», и новый человек получал бы лимит, которого не
    # выбирал (а на чистой установке ему и подавно неоткуда взяться).
    [ -n "${GT_WANT_SYNFIX:-}" ] && MEKOFIX_NOLIMIT=1 gt_ensure_synfix "$GT_WANT_SYNFIX" "$port"

    # Credits
    show_credits

    # Result
    show_proxy_info
    log_success "$(tf install_done "$GOTELEGRAM_VERSION" "Lite")"
    # Несколько адресов имеют смысл, только если по запасному можно ещё и войти:
    # блокировка главного не должна отрезать администратора от сервера.
    type edgeips_ssh_notify >/dev/null 2>&1 && edgeips_ssh_notify || true

    metrics_install_timer
    offer_warp_setup
    offer_mekofix_setup
}

# ── Pro mode ────────────────────────────────────────────────────────────────
# Тело записи конфигов Pro: работает в локальных переменных install_pro_mode
# (raw_secret/user_domain/…), вызывать только оттуда — через gt_with_config_lock.
_install_pro_apply_locked() {
    # Вызов БЕЗ $(…): подшелл терял GT_PRESERVED_USERS, и переустановка поверх
    # стирала все ключи, кроме main.
    gt_preserve_existing
    # Флаг чистой установки снимаем ДО gt_restore_users (он очищает GT_PRESERVED_USERS).
    local had_users="${GT_PRESERVED_USERS:+1}"
    [ -n "${GT_PRESERVED_SECRET:-}" ] && raw_secret="$GT_PRESERVED_SECRET"
    generate_telemt_toml "$raw_secret" "443" "pro" "$user_domain" "$nginx_internal_port"
    gt_restore_users

    # Ленивый: 5 ключей (main + key2..key5). Charset имён [A-Za-z0-9_.-] — безопасно.
    # Только на ЧИСТОЙ установке: при переустановке свежие generate_hex затирали бы
    # только что восстановленные key2..key5 — и розданные ссылки протухали.
    if [ "$lazy" = "1" ] && [ -z "$had_users" ]; then
        local ub="main = \"$raw_secret\"" i
        for i in 2 3 4 5; do
            ub="${ub}
key${i} = \"$(generate_hex 32)\""
        done
        replace_telemt_users_block "$ub" "$TELEMT_CONFIG" 2>/dev/null || true
        log_info "$(tf lazy_keys_created 5)"
    fi

    # Сайт (nginx + certbot + шаблон)
    setup_pro_mode "$user_domain" "$template_dir" "$nginx_internal_port" "$ssl_email" || return
    systemctl restart nginx 2>/dev/null
    start_telemt || return

    local tpl_id; tpl_id=$(basename "$template_dir")
    save_gotelegram_config "telemt" "pro" "443" "$raw_secret" "$user_domain" "$user_domain" "$tpl_id"
    bot_update_config_field preset_id "${DIAG_DEFAULT_PRESET:-4}" >/dev/null 2>&1 || true
    # Пин ядра: choose_telemt_version отрабатывает ДО появления config.json, и его
    # запись молча проваливалась — первое обновление уводило ядро с выбранной версии.
    [ -n "${TELEMT_VERSION_PREF:-}" ] && bot_update_config_field telemt_version "$TELEMT_VERSION_PREF" >/dev/null 2>&1 || true
    # CC ядра читается из СОХРАНЁННОГО preset_id: до записи выше переустановка
    # видела старый пресет и ставила cubic, хотя режим объявлен MEKO (BBR).
    apply_network_sysctl
    apply_log_hygiene
    return 0
}

install_pro_mode() {
    local lazy="${GOTELEGRAM_LAZY:-0}"
    log_step "$(t install_pro_step)"
    [ "$lazy" = "1" ] && log_info "$(t lazy_active)"

    # Pre-flight: Pro/Ленивый режим требует свободные 443 и 80
    if ! preflight_ports_pro; then
        log_warning "$(t pf_pro_ports_busy)"
        warn_3xui_443_conflict || true
        if ! confirm "$(t install_continue_anyway)"; then return; fi
    fi

    # Домен
    echo ""
    echo -ne "  ${WHITE}$(t install_enter_domain)${NC} "
    read -r user_domain
    user_domain="$(printf '%s' "$user_domain" | tr -d '[:space:]')"
    if [ -z "$user_domain" ] || ! validate_domain "$user_domain"; then
        log_error "$(tf install_bad_domain "${user_domain:-<empty>}")"
        return
    fi

    # DNS-проверка
    local resolved_ip server_ip
    resolved_ip=$(dig +short "$user_domain" A 2>/dev/null | head -1)
    server_ip=$(get_server_ip)
    if [ -n "$resolved_ip" ] && [ "$resolved_ip" != "$server_ip" ]; then
        if [ "$lazy" = "1" ]; then
            log_warning "$(tf lazy_dns_warn "$user_domain" "$server_ip")"
        else
            log_warning "$(tf install_dns_mismatch "$user_domain" "$resolved_ip" "$server_ip")"
            if ! confirm "$(t install_continue_anyway)"; then return; fi
        fi
    fi
    # Прокси слушает только IPv4 — AAAA у домена надо убрать до выдачи ссылок
    local resolved_aaaa
    resolved_aaaa=$(domain_aaaa_records "$user_domain" | head -3 | paste -sd ' ' -)
    [ -n "$resolved_aaaa" ] && log_warning "$(tf dns_aaaa_warn "$user_domain" "$resolved_aaaa")"

    # Email для Let's Encrypt (ленивый: пропускаем — LE без почты)
    local ssl_email=""
    if [ "$lazy" != "1" ]; then
        echo -ne "  ${WHITE}$(t install_enter_email)${NC} "
        read -r ssl_email
    fi

    # Выбор шаблона
    local template_dir=""
    if [ "$lazy" = "1" ]; then
        type load_catalog >/dev/null 2>&1 && load_catalog >/dev/null 2>&1 || true
        local _rtpl; _rtpl=$(pick_random_template_id 2>/dev/null)
        if [ -n "$_rtpl" ]; then
            log_dim "$(tf lazy_template "$_rtpl")"
            template_dir=$(download_template "$_rtpl" 2>/dev/null) || template_dir=""
        fi
        if [ -z "$template_dir" ]; then
            template_dir=$(interactive_template_selection) || return
        fi
    else
        template_dir=$(interactive_template_selection)
        [ $? -ne 0 ] && return
    fi

    local nginx_internal_port=8443
    echo ""
    echo -e "  ${DIM}$(t install_arch_desc1)${NC}"
    echo -e "  ${DIM}$(tf install_arch_desc2 "$nginx_internal_port")${NC}"
    echo -e "  ${DIM}$(tf install_arch_desc3 "$user_domain")${NC}"

    # Fake-TLS секрет (ee + secret + hex домена); сама ee-ссылка собирается ниже,
    # уже ПОСЛЕ возможной подмены raw_secret секретом прежней установки.
    local raw_secret; raw_secret=$(generate_hex 32)

    # Сводка
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t install_config_title)${NC}"
    echo -e "  $(t install_cfg_domain)      ${CYAN}${user_domain}${NC}"
    echo -e "  $(t install_cfg_port)       ${CYAN}443 (telemt + nginx)${NC}"
    echo -e "  $(t install_cfg_mode)      ${MAGENTA}Pro (fake-TLS)${NC}"
    echo ""

    if [ "$lazy" = "1" ]; then
        log_info "$(t lazy_autoconfirm)"
    else
        if ! confirm "$(t install_confirm_proxy_site)"; then return; fi
    fi

    # Установка
    ensure_deps
    # DNS сервера — до загрузок: ядро, шаблоны и certbot пойдут уже с настройкой
    dns_resolver_tune
    choose_telemt_version
    install_telemt_full || return
    # Чистая установка: дефолтный анти-DPI пресет (MEKO style) + SYN-фикс V3.
    TELEMT_PRESET_PREF="${DIAG_DEFAULT_PRESET:-4}"
    GT_WANT_SYNFIX=v3
    # Под общим config-замком (как apply_preset): ключ, созданный из бота/панели
    # между чтением блока пользователей и его возвратом, молча терялся.
    gt_with_config_lock _install_pro_apply_locked || return
    [ -n "${GT_WANT_SYNFIX:-}" ] && MEKOFIX_NOLIMIT=1 gt_ensure_synfix "$GT_WANT_SYNFIX" 443

    # ee-ссылку собираем ПОСЛЕ подмены raw_secret сохранённым секретом: раньше
    # она собиралась до подмены, и на финальный экран/в QR уходил отброшенный
    # случайный секрет — ссылка с экрана «Pro-прокси настроен» не подключалась.
    local domain_hex; domain_hex=$(printf '%s' "$user_domain" | xxd -p | tr -d '\n')
    local faketls_secret="ee${raw_secret}${domain_hex}"
    show_proxy_info_pro "$user_domain" "$faketls_secret"
    echo -e "  ${WHITE}$(t svc_site):${NC} ${GREEN}https://${user_domain}${NC}"
    log_success "$(tf install_done "$GOTELEGRAM_VERSION" "Pro")"
    # Несколько адресов имеют смысл, только если по запасному можно ещё и войти:
    # блокировка главного не должна отрезать администратора от сервера.
    type edgeips_ssh_notify >/dev/null 2>&1 && edgeips_ssh_notify || true

    # Авто-подбор оптимального режима (вкл. CC ядра) под сеть — во ВСЕХ режимах
    metrics_install_timer
    offer_warp_setup
    offer_mekofix_setup
}

# ── Статус ───────────────────────────────────────────────────────────────────
menu_status() {
    # Счётчики SYN-фикса раньше жили отдельным пунктом в меню фикса. Пункт убран,
    # но сами цифры полезны, когда человек жалуется на подключение — показываем их
    # здесь, вместе с остальным состоянием.
    if type mekofix_is_active >/dev/null 2>&1 && mekofix_is_active 2>/dev/null; then
        echo ""
        echo -e "  ${DIM}$(t mode_counters)${NC}"
        mekofix_counters 2>/dev/null | sed 's/^/  /' || true
    fi
    show_proxy_info

    # Extras for pro
    local mode
    mode=$(config_get mode 2>/dev/null)
    if [ "$mode" = "pro" ]; then
        local domain
        domain=$(config_get domain 2>/dev/null)
        if [ -n "$domain" ]; then
            local ssl_expiry
            ssl_expiry=$(get_ssl_expiry "$domain")
            local nginx_st
            nginx_st=$(nginx_status)
            echo -e "  ${WHITE}$(t svc_nginx):${NC}      ${nginx_st}"
            echo -e "  ${WHITE}$(t website_ssl_until)${NC}     ${ssl_expiry}"
            echo -e "  ${WHITE}$(t svc_site):${NC}       https://${domain}"
            echo ""
        fi
    fi

    # Разбивка по адресам показывается только когда адресов несколько: на
    # обычной машине с одним IP это лишняя строка, которая ничего не сообщает.
    if type edgeips_list >/dev/null 2>&1 && [ "$(edgeips_list | wc -l)" -gt 1 ]; then
        echo -e "  ${BOLD}${WHITE}$(t edge_title)${NC}"
        edgeips_status
        echo -e "  ${DIM}$(t edge_hint)${NC}"
        echo ""
    fi
}

# ── Ссылка ───────────────────────────────────────────────────────────────────
menu_link() {
    local secret port ip link mode domain mask_host
    secret=$(get_config_value secret)
    port=$(get_config_value port)
    ip=$(get_server_ip)
    mode=$(config_get mode 2>/dev/null || echo "lite")
    domain=$(config_get domain 2>/dev/null || echo "")
    mask_host=$(config_get mask_host 2>/dev/null || echo "")

    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
        link=$(generate_proxy_link "$domain" "$port" "$secret" "$domain")
    else
        link=$(generate_proxy_link "$ip" "$port" "$secret" "$mask_host")
    fi

    echo ""
    echo -e "  ${BOLD}${WHITE}$(t link_title)${NC}"
    echo ""
    echo -e "  ${GREEN}${link}${NC}"
    echo ""

    if command -v qrencode &>/dev/null; then
        qrencode -t UTF8 -m 2 "$link" 2>/dev/null
    fi
    type show_web_link >/dev/null 2>&1 && show_web_link "$secret" 1
}

# ── Поделиться ───────────────────────────────────────────────────────────────
menu_share() {
    local secret port ip link mode domain mask_host server_display
    secret=$(get_config_value secret)
    port=$(get_config_value port)
    ip=$(get_server_ip)
    mode=$(config_get mode 2>/dev/null || echo "lite")
    domain=$(config_get domain 2>/dev/null || echo "")
    mask_host=$(config_get mask_host 2>/dev/null || echo "")

    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
        link=$(generate_proxy_link "$domain" "$port" "$secret" "$domain")
        server_display="$domain"
    else
        link=$(generate_proxy_link "$ip" "$port" "$secret" "$mask_host")
        server_display="$ip"
    fi

    echo ""
    echo -e "  ${BOLD}$(t share_title)${NC}"
    echo ""
    printf "$(t share_line1)\n" "$GOTELEGRAM_VERSION"
    echo ""
    printf "$(t share_server)\n" "$server_display"
    printf "$(t share_port)\n" "$port"
    echo ""
    echo "$(t share_connect_cta)"
    echo "$link"
    if type web_link_for_secret >/dev/null 2>&1; then
        local _wl; _wl=$(web_link_for_secret "$secret" 2>/dev/null || echo "")
        [ -n "$_wl" ] && { echo ""; echo "$(t web_link_title)"; echo "$_wl"; }
    fi
    echo ""
    echo "$(t share_footer)"
    echo ""
}

# ── Перезапуск ───────────────────────────────────────────────────────────────
menu_restart() {
    restart_telemt
    local mode
    mode=$(config_get mode 2>/dev/null)
    if [ "$mode" = "pro" ]; then
        restart_nginx
    fi
}

# ── Logs ────────────────────────────────────────────────────────────────────
menu_logs() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(tf logs_telemt_title 40)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    telemt_logs 40
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
}

# ── Change mode / template ──────────────────────────────────────────────────
update_current_template_id() {
    local template_dir="$1"
    local tpl_id
    tpl_id=$(basename "$template_dir")
    [ -z "$tpl_id" ] && return 0

    if [ -f "$template_dir/.custom_git_source" ]; then
        local source_url
        source_url=$(head -1 "$template_dir/.custom_git_source" 2>/dev/null || echo "")
        bot_update_config_field "template_source" "$source_url" || true
    fi
    bot_update_config_field "template_id" "$tpl_id" || \
        log_warning "Не удалось обновить template_id в config.json"
}

menu_change_mode() {
    local current_mode
    current_mode=$(config_get mode 2>/dev/null)
    echo ""
    echo -e "  ${WHITE}$(t change_current_mode)${NC} ${CYAN}${current_mode}${NC}"
    echo ""
    echo -e "  ${CYAN}1${NC}) $(t change_template)"
    echo -e "  ${CYAN}2${NC}) $(t change_mode_switch)"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch

    case "$ch" in
        1)
            if [ "$current_mode" != "pro" ]; then
                log_error "$(t change_only_pro)"
                return
            fi
            local template_dir
            template_dir=$(interactive_template_selection)
            [ $? -ne 0 ] && return
            switch_template "$template_dir"
            update_current_template_id "$template_dir"
            ;;
        2)
            log_warning "$(t change_requires_reinstall)"
            if confirm "$(t change_reinstall_confirm)"; then
                menu_install
            fi
            ;;
    esac
}

# ── Website management ─────────────────────────────────────────────────────
menu_website() {
    local mode
    mode=$(config_get mode 2>/dev/null)

    if [ "$mode" != "pro" ]; then
        log_info "$(t website_only_pro)"
        return
    fi

    local domain
    domain=$(config_get domain 2>/dev/null)

    echo ""
    echo -e "  ${BOLD}${WHITE}$(t website_title)${NC}"
    echo -e "  $(t website_domain) ${CYAN}${domain}${NC}"
    echo -e "  $(t website_ssl_until) $(get_ssl_expiry "$domain")"
    echo ""
    echo -e "  ${CYAN}1${NC}) $(t website_renew_ssl)"
    echo -e "  ${CYAN}2${NC}) $(t website_restart_nginx)"
    echo -e "  ${CYAN}3${NC}) $(t website_change_template)"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch

    case "$ch" in
        1) renew_ssl_certificate ;;
        2) restart_nginx ;;
        3)
            local template_dir
            template_dir=$(interactive_template_selection)
            [ $? -ne 0 ] && return
            switch_template "$template_dir"
            update_current_template_id "$template_dir"
            ;;
    esac
}

# ── Remove ─────────────────────────────────────────────────────────────────
# Функция gotelegram_self_update удалена: не была подключена ни к одному меню
# (обновление идёт через bootstrap.sh), мёртвый код только путал при чтении.

# ── Telegram Mini App: панель со своего домена ──────────────────────────────
# Зачем: панель управления живёт на 127.0.0.1:1984 и доступна только по SSH-
# туннелю. Для мини-аппа Telegram нужен публичный HTTPS-адрес. Раньше такие
# вещи делали через временный туннель Cloudflare — и адрес умирал вместе с ним.
# Мы отдаём панель со СВОЕГО домена, где уже есть валидный сертификат.
# Наружу открывается неугадываемый путь, а пускает внутрь только валидная
# подпись Telegram (HMAC WebAppData) от пользователя из списка админов бота.
miniapp_url() {
    local p d
    p=$(config_get miniapp_path 2>/dev/null || echo ""); [ -z "$p" ] && return 1
    d=$(config_get domain 2>/dev/null || echo ""); [ -z "$d" ] && return 1
    printf 'https://%s/%s/' "$d" "$p"
}

# Почему мини-приложение сейчас нельзя включить (пусто = можно).
# Отдельной функцией, потому что этот ответ нужен в трёх местах: SSH-меню, JSON
# для бота и сама проверка при включении. Раньше причина существовала только
# внутри miniapp_enable, и бот не мог объяснить, ЧЕГО не хватает.
# Готов ли вход по коду: панель шлёт код в Telegram, значит нужен установленный
# бот и хотя бы один админ в его настройках. Без этого адрес в браузере
# откроется, но войти по нему будет нечем — и об этом надо предупреждать ТАМ ЖЕ,
# где показан адрес, а не после неудачной попытки.
admin_web_tfa_ready() {
    [ "$(bot_service_status 2>/dev/null)" = "not_installed" ] && return 1
    grep -qE "^ALLOWED_IDS=.*[0-9]" "$BOT_DIR/.env" 2>/dev/null
}

miniapp_blocker() {
    local mode domain
    mode=$(config_get mode 2>/dev/null || echo lite)
    domain=$(config_get domain 2>/dev/null || echo "")
    if [ "$mode" != "pro" ] || [ -z "$domain" ]; then echo "need_pro"; return 0; fi
    if [ "$(bot_service_status 2>/dev/null)" = "not_installed" ]; then echo "need_bot"; return 0; fi
    echo ""
}

# ── WEB-прокси (Telegram Desktop 7.1+) ───────────────────────────────────────
# Печатает причину, по которой включить нельзя; пусто — можно.
web_blocker() {
    local mode domain
    mode=$(config_get mode 2>/dev/null || echo lite)
    domain=$(config_get domain 2>/dev/null || echo "")
    if [ "$mode" != "pro" ] || [ -z "$domain" ]; then echo "need_pro"; return 0; fi
    if ! type telemt_web_supported >/dev/null 2>&1 || ! telemt_web_supported; then echo "need_core"; return 0; fi
    [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ] || { echo "need_cert"; return 0; }
    # Без публичного IPv4 (IPv6-only VPS) генератор молча писал конфиг без [web],
    # а включение отчитывалось успехом.
    telemt_web_public_addr >/dev/null 2>&1 || { echo "need_addr"; return 0; }
    # WEB живёт только на отдельном имени: на основном домене он душил обычные
    # ссылки за тем же NAT (см. «Отдельное имя для WEB» в telemt_config.sh).
    # Установки, где WEB включили до 2.10.50, работают по-старому, пока имя не
    # задано, — блокер показывает им, чего не хватает.
    [ -n "$(telemt_web_host)" ] || { echo "need_web_host"; return 0; }
    [ -f "${SHARED443_STREAM_CONF:-/etc/nginx/stream-conf.d/gotelegram-shared443.conf}" ] && { echo "need_no_shared443"; return 0; }
    echo ""
}

# Имя WEB смотрит на этот сервер: A-запись совпадает с публичным адресом или
# с одним из адресов интерфейсов (несколько IP у сервера). 2 — записи нет.
_web_host_points_here() {
    local host="$1" ips pub local_ips ip
    command -v dig >/dev/null 2>&1 || return 3
    ips=$(dig +short +time=3 +tries=1 A "$host" 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+){3}$')
    [ -n "$ips" ] || return 2
    pub=$(get_server_ip 2>/dev/null || echo "")
    local_ips=$(ip -4 -o addr show scope global 2>/dev/null | awk '{ sub(/\/.*/, "", $4); print $4 }')
    for ip in $ips; do
        [ "$ip" = "$pub" ] && return 0
        printf '%s\n' "$local_ips" | grep -qx "$ip" && return 0
    done
    return 1
}

# Сертификат домена покрывает имя WEB (SAN), точное совпадение.
_web_cert_covers() {
    openssl x509 -noout -ext subjectAltName -in "/etc/letsencrypt/live/$1/fullchain.pem" 2>/dev/null \
        | tr ',' '\n' | sed 's/^[[:space:]]*//' | grep -qx "DNS:$2"
}

# certbot --expand для имени WEB. Вывод — во временный файл (mktemp, 600,
# удаляется): по нему причина отказа (certbot_fail_text) — в WEB_CERT_ERR.
# Второй заход без почты — только если до проверки имени дело не дошло (нет
# аккаунта и т.п.): иначе он лишь тратит лимит неудачных проверок Let's Encrypt.
_web_certbot_expand() {
    local domain="$1" host="$2" log rc traps
    traps=$(trap -p INT TERM HUP)
    log=$(mktemp "${TMPDIR:-/tmp}/gt-certbot.XXXXXX" 2>/dev/null) || log=""
    if [ -n "$log" ]; then
        chmod 600 "$log" 2>/dev/null
        _gt_tmp_trap "rm -f -- $(printf '%q' "$log")" "$traps"
    else log=/dev/null; fi
    # timeout: бот и панель ждут ответа ограниченное время, а certbot без
    # предела мог висеть на сети — действие убивали посреди перестройки
    timeout 100 certbot certonly --webroot -w "$ACME_WEBROOT" --cert-name "$domain" -d "$domain" -d "$host" \
        --expand --non-interactive --agree-tos > "$log" 2>&1; rc=$?
    if [ "$rc" != 0 ] && [ "$(certbot_fail_kind "$log")" = other ]; then
        timeout 100 certbot certonly --webroot -w "$ACME_WEBROOT" --cert-name "$domain" -d "$domain" -d "$host" \
            --expand --non-interactive --agree-tos --register-unsafely-without-email > "$log" 2>&1; rc=$?
    fi
    [ "$rc" = 0 ] || WEB_CERT_ERR=$(certbot_fail_text "$log" "$rc" "$host")
    [ "$log" = /dev/null ] || { rm -f "$log"; _gt_tmp_untrap "$traps"; }
    [ "$rc" = 0 ]
}

# Расширить сертификат домена именем WEB тем же webroot. Один выпуск на одну
# смену имени — лимиты Let's Encrypt это не задевает. Отказ — rc 1, текст
# причины в WEB_CERT_ERR, файлы nginx — как были до вызова (снимок).
web_cert_expand() {
    local domain="$1" host="$2" snap reloaded=0 traps prc
    local files=("$NGINX_SITE_CONF" "$NGINX_SITE_LINK" "$WEB_FRONT_STREAM_CONF")
    WEB_CERT_ERR=""
    _web_cert_covers "$domain" "$host" && return 0
    acme_webroot_prepare || true
    traps=$(trap -p INT TERM HUP)
    snap=$(mktemp -d "${TMPDIR:-/tmp}/gt-webcert.XXXXXX" 2>/dev/null) || snap=""
    if [ -z "$snap" ] || ! _nginx_tls_snap save "$snap" "${files[@]}"; then
        [ -n "$snap" ] && rm -rf "$snap"
        WEB_CERT_ERR=$(tf web_cert_snap "${TMPDIR:-/tmp}"); return 1
    fi
    # Прервали посреди шага — файлы nginx вернуть из снимка, снимок убрать (не
    # вернулись — снимок остаётся). nginx не перечитываем: в памяти лишнее имя
    # на :80, это безвредно до следующего reload. restore сначала удаляет файл,
    # поэтому ловушка снимается до удаления снимка и без снимка ничего не делает.
    _gt_tmp_trap "[ -d $(printf '%q' "$snap") ] && _nginx_tls_snap restore $(printf '%q ' "$snap" "${files[@]}")&& rm -rf -- $(printf '%q' "$snap")" "$traps"
    # ACME по http: порт 80 отвечает и на имя WEB — оно ещё не в config.json,
    # генератору его передаём через WEB_HOST_PENDING. Не собралось или nginx
    # не перечитал — certbot не зовём: проверка шла бы вслепую.
    if ! WEB_HOST_PENDING="$host" generate_nginx_config "$domain" 8443 >/dev/null 2>&1 \
       || ! nginx -t >/dev/null 2>&1; then
        WEB_CERT_ERR=$(tf web_cert_nginx "$host")
    elif ! systemctl reload nginx >/dev/null 2>&1; then
        WEB_CERT_ERR=$(tf web_cert_reload "$host")
    else
        reloaded=1
        # Отдаёт ли nginx файл из acme-challenge на это имя — до certbot: каждая
        # неудачная проверка у Let's Encrypt идёт в лимит (5 в час на имя)
        acme_local_probe "$host"; prc=$?
        # reload не ждёт старых воркеров nginx: первый ответ мог дать прежний конфиг
        if [ "$prc" = 4 ] || [ "$prc" = 5 ]; then
            sleep "${ACME_PROBE_RETRY_DELAY:-2}"; acme_local_probe "$host"; prc=$?
        fi
        case $prc in
            3) WEB_CERT_ERR=$(tf web_cert_probe_403 "$host" "$ACME_WEBROOT") ;;
            4) WEB_CERT_ERR=$(tf web_cert_probe_other "$host") ;;
            5) WEB_CERT_ERR=$(t web_cert_probe_none) ;;
        esac
        if [ -z "$WEB_CERT_ERR" ] && _web_certbot_expand "$domain" "$host"; then
            systemctl reload nginx >/dev/null 2>&1
            if _web_cert_covers "$domain" "$host"; then _gt_tmp_untrap "$traps"; rm -rf "$snap"; return 0; fi
            WEB_CERT_ERR=$(tf web_host_cert "$host")
        fi
    fi
    [ -n "$WEB_CERT_ERR" ] || WEB_CERT_ERR=$(tf web_host_cert "$host")
    if _nginx_tls_snap restore "$snap" "${files[@]}"; then
        # перечитывали с новым именем — вернуть nginx на прежний конфиг
        [ "$reloaded" = 1 ] && nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1
        _gt_tmp_untrap "$traps"; rm -rf "$snap"
    else
        _gt_tmp_untrap "$traps"
        type log_to_file >/dev/null 2>&1 \
            && log_to_file "web_cert_expand: ${host} failed, ROLLBACK FAILED; nginx snapshot kept in ${snap}"
        WEB_CERT_ERR="${WEB_CERT_ERR} $(tf web_cert_rollback "$snap")"
    fi
    return 1
}

# Имя WEB — поддомен основного домена: для домена второго уровня это третий
# уровень (www.<домен>). Сертификат WEB расширяет сертификат домена, а DNS
# заводится в той же зоне — владельцу проще и понятнее держать имя рядом.
web_host_is_sub() {
    local host="$1" domain="$2"
    [ -n "$host" ] && [ -n "$domain" ] || return 1
    case "$host" in *."$domain") ;; *) return 1 ;; esac
    [ "$host" != ".${domain}" ]
}

# WEB включён по-старому, на основном домене (web-status legacy): работает, но
# душит обычные ссылки за тем же NAT.
web_legacy_active() {
    telemt_web_active 2>/dev/null && web_configured_in_core 2>/dev/null && ! web_front_in_core 2>/dev/null
}

# Вопрос после обновления: WEB включён по-старому, на основном домене. Ключи
# делятся по именам: обычные остаются на домене, WEB-ссылки переезжают на
# отдельное имя, и их надо раздать заново — поэтому спрашиваем, а не делаем
# молча. Только в интерактиве: обновление из бота или панели идёт без tty.
# Вызывается из main() ПОСЛЕ миграции: web_set_host сам берёт замок config.
web_upgrade_offer_host() {
    { : < /dev/tty; } 2>/dev/null || return 0
    web_legacy_active || return 0
    [ -z "$(telemt_web_host 2>/dev/null)" ] || return 0
    local domain def ans host miss rc total covered ips
    domain=$(config_get domain 2>/dev/null || echo "")
    [ -n "$domain" ] && [ "$domain" != "null" ] || return 0
    def="www.${domain}"
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t web_upg_title)${NC}"
    echo -e "  $(tf web_upg_l1 "$domain")"
    echo -e "  $(t web_upg_l2)"
    echo -e "  ${DIM}$(t web_upg_l3)${NC}"
    if ! confirm "$(t web_upg_ask)"; then
        log_info "$(t web_upg_later)"
        return 0
    fi
    echo -ne "  $(tf web_upg_name_prompt "$def" "$domain") "
    IFS= read -r ans < /dev/tty || ans=""
    host=$(printf '%s' "$ans" | tr 'A-Z' 'a-z' | tr -d '[:space:]')
    [ -n "$host" ] || host="$def"
    # Не имя или не поддомен домена — web_set_host откажет сразу, без побочных
    # действий, и сам объяснит почему; DNS такого имени проверять незачем
    if ! validate_domain "$host" || ! web_host_is_sub "$host" "$domain"; then
        web_set_host "$host" || true
        log_info "$(t web_upg_later)"
        return 0
    fi
    # На сколько адресов сервера имя указывает уже сейчас
    if type edgeips_dns_missing >/dev/null 2>&1; then
        ips=$(edgeips_server_ips)
        total=$(printf '%s\n' "$ips" | grep -c . || true)
        miss=$(edgeips_dns_missing "$host"); rc=$?
        if [ "$rc" = 0 ] || [ "$rc" = 2 ]; then
            covered=$(( ${total:-0} - $(printf '%s\n' "$miss" | grep -c . || true) ))
            [ "$covered" -lt 0 ] && covered=0
            echo -e "  $(tf web_upg_dns_count "$host" "$covered" "${total:-0}")"
            if [ "$covered" -eq 0 ]; then
                # DNS не готов: web_set_host всё равно откажет — объясняем, что
                # завести, и оставляем WEB как есть
                echo -e "  ${YELLOW}$(tf web_upg_dns_todo "$host" "$(printf '%s\n' "$ips" | paste -sd ' ' - | sed 's/ /, /g')")${NC}"
                log_info "$(t web_upg_later)"
                return 0
            fi
        fi
    fi
    if ! web_set_host "$host"; then
        log_info "$(t web_upg_later)"
        return 0
    fi
    # Новая WEB-ссылка основного ключа — её и раздавать
    local link
    link=$(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null || echo "")
    [ -n "$link" ] && echo -e "  ${GREEN}${link}${NC}"
    return 0
}

# Задать отдельное имя WEB: DNS → сертификат → (если WEB включён) разводка 443.
# Любой отказ возвращает прежнее имя.
web_set_host() {
    local host domain prev rc
    host=$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z' | tr -d '[:space:]')
    domain=$(config_get domain 2>/dev/null || echo "")
    if [ "$(config_get mode 2>/dev/null || echo lite)" != "pro" ] || [ -z "$domain" ]; then
        log_error "$(t web_need_pro)"; return 1
    fi
    if [ -z "$host" ] || ! validate_domain "$host"; then log_error "$(tf web_host_bad "${host:-<empty>}")"; return 1; fi
    [ "$host" != "$domain" ] || { log_error "$(t web_host_same)"; return 1; }
    web_host_is_sub "$host" "$domain" || { log_error "$(tf web_host_not_sub "$host" "$domain" "$domain")"; return 1; }
    [ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ] || { log_error "$(t web_need_cert)"; return 1; }
    # До выпуска сертификата: на ядре без WEB или без публичного IPv4 имя всё
    # равно не включится, а выпуск тратит лимит Let's Encrypt
    if ! type telemt_web_supported >/dev/null 2>&1 || ! telemt_web_supported; then
        log_error "$(tf web_need_core "$WEB_MIN_TELEMT")"; return 1
    fi
    telemt_web_public_addr >/dev/null 2>&1 || { log_error "$(t web_need_addr)"; return 1; }
    _web_host_points_here "$host"; rc=$?
    [ "$rc" = 3 ] && { log_error "$(t web_host_nodig)"; return 1; }
    if [ "$rc" != 0 ]; then log_error "$(tf web_host_dns "$host" "$(get_server_ip 2>/dev/null)")"; return 1; fi
    # Имя указывает сюда, но, возможно, не на все адреса: не блокируем (адрес
    # мог быть оставлен запасным для SSH), но говорим
    if type edgeips_dns_warn >/dev/null 2>&1; then
        local _hw; _hw=$(edgeips_dns_warn "$host")
        [ -n "$_hw" ] && log_warning "$_hw"
    fi
    prev=$(telemt_web_host)
    # Имя пишем в config.json только после сертификата: если действие оборвут
    # посреди выпуска, следующая перегенерация не включит разводку на имя,
    # которого нет в сертификате
    # Отказ: web_cert_expand уже вернул файлы nginx как были; причина — одной
    # строкой (её последней строкой берёт --action=web-host для бота и панели)
    if ! web_cert_expand "$domain" "$host"; then
        log_error "${WEB_CERT_ERR:-$(tf web_host_cert "$host")}"; return 1
    fi
    bot_update_config_field web_host "$host" >/dev/null 2>&1
    if telemt_web_enabled 2>/dev/null; then
        if ! gt_with_config_lock _web_apply_locked; then
            bot_update_config_field web_host "$prev" >/dev/null 2>&1
            # --expand заменил набор имён: прежнее имя WEB из сертификата ушло,
            # без возврата откат оставил бы WEB на имени без сертификата
            [ -n "$prev" ] && web_cert_expand "$domain" "$prev" >/dev/null 2>&1
            gt_with_config_lock _web_apply_locked >/dev/null 2>&1 || true
            log_error "$(t web_apply_failed)"; return 1
        fi
    fi
    log_success "$(tf web_host_set "$host")"
    return 0
}

# После смены ядра согласовать WEB с тем, что новое ядро умеет. Даунгрейд на
# ядро без WEB оставлял nginx ждать PROXY-заголовок, которого ядро не шлёт, —
# сайт-маскировка лежала, а бот и панель в этом состоянии не давали выключить.
web_reconcile_after_core_change() {
    telemt_web_enabled 2>/dev/null || return 0
    [ "$(config_get mode 2>/dev/null || echo lite)" = "pro" ] || return 0
    if telemt_web_supported 2>/dev/null; then
        grep -q '^transport = "web"' "$TELEMT_CONFIG" 2>/dev/null && return 0
        gt_with_config_lock _web_apply_locked >/dev/null 2>&1 || log_warning "$(t web_apply_failed)"
        return 0
    fi
    log_warning "$(tf web_need_core "$WEB_MIN_TELEMT")"
    web_disable >/dev/null 2>&1 || true
    log_warning "$(t web_disabled_by_core)"
}

# Перегенерация telemt и nginx по текущему config.json с сохранением ключей —
# тот же порядок, что у смены пресета.
_web_apply_locked() {
    local secret domain users_block
    secret=$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$secret" ] && secret=$(config_get secret 2>/dev/null || echo "")
    domain=$(config_get domain 2>/dev/null || echo "")
    [ -n "$secret" ] && [ -n "$domain" ] || return 1
    users_block=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null || true)
    generate_telemt_toml "$secret" "443" "pro" "$domain" "8443" "$TELEMT_CONFIG" >&2 || return 1
    [ -n "$users_block" ] && replace_telemt_users_block "$users_block" "$TELEMT_CONFIG" >/dev/null 2>&1
    generate_nginx_config "$domain" 8443 >/dev/null 2>&1
    nginx -t >/dev/null 2>&1 || return 2
    # Порядок: сначала nginx начинает ждать PROXY-заголовок, потом telemt
    # начинает его слать. Рестарт ядра закрывает listener 443 примерно на
    # десять секунд для всех клиентов — это цена включения/выключения (состав
    # listeners ядро меняет только рестартом); смена carrier сюда не ходит.
    # Переход на разводку по имени: nginx может занять 443 только после того,
    # как его отпустит ядро, — это сделает restart_telemt. Перезагрузка здесь
    # упала бы на занятом порту, а запасной restart уронил бы nginx целиком.
    if ! { web_front_in_core 2>/dev/null && ! _web_front_nginx_on_443; }; then
        systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx >/dev/null 2>&1
    fi
    rm -f /run/gotelegram/web.bridge 2>/dev/null
    restart_telemt >&2 || return 3
    return 0
}

web_enable() {
    local b prev rc link
    b=$(web_blocker 2>/dev/null || echo "")
    if [ -n "$b" ]; then
        case "$b" in need_core) log_error "$(tf web_need_core "$WEB_MIN_TELEMT")" ;; *) log_error "$(t "web_$b")" ;; esac
        return 1
    fi
    prev=$(config_get web_enabled 2>/dev/null || echo "false")
    bot_update_config_field web_enabled "true" >/dev/null 2>&1
    [ -n "$(config_get web_carrier 2>/dev/null || echo "")" ] || bot_update_config_field web_carrier "https" >/dev/null 2>&1
    gt_with_config_lock _web_apply_locked; rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "$(t web_apply_failed) (rc=$rc)"
        bot_update_config_field web_enabled "$prev" >/dev/null 2>&1
        gt_with_config_lock _web_apply_locked >/dev/null 2>&1 || true
        return 1
    fi
    log_success "$(t web_enabled)"
    link=$(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null || echo "")
    [ -n "$link" ] && echo -e "  ${GREEN}${link}${NC}"
    log_info "$(t web_hint)"
    return 0
}

web_disable() {
    bot_update_config_field web_enabled "false" >/dev/null 2>&1
    if [ "$(config_get mode 2>/dev/null || echo lite)" = "pro" ]; then
        gt_with_config_lock _web_apply_locked || { log_error "$(t web_apply_failed)"; return 1; }
    fi
    log_success "$(t web_disabled)"
}

# Carrier — hot-reload-ключ ядра: правим только его строку в живом конфиге, и
# config watcher применяет на лету. Полная перегенерация с рестартом закрывала
# главный listener 443 на ~10 с для ВСЕХ клиентов ради одной строки.
_web_set_carrier_locked() {
    local carrier="$1" tmp
    if ! grep -q '^carrier = "' "$TELEMT_CONFIG" 2>/dev/null; then
        # WEB в конфиге ядра не собран — соберём целиком (с рестартом)
        _web_apply_locked; return $?
    fi
    tmp=$(mktemp "${TELEMT_CONFIG}.XXXXXX") || return 1
    chmod 600 "$tmp" 2>/dev/null
    sed "s/^carrier = \".*\"/carrier = \"${carrier}\"/" "$TELEMT_CONFIG" > "$tmp" || { rm -f "$tmp"; return 1; }
    grep -q "^carrier = \"${carrier}\"" "$tmp" || { rm -f "$tmp"; return 1; }
    sync "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$TELEMT_CONFIG" || { rm -f "$tmp"; return 1; }
    chmod 600 "$TELEMT_CONFIG"
    # watcher подхватит сам; SIGHUP — на случай, если inotify не завёлся
    local pid; pid=$(systemctl show -p MainPID --value "$TELEMT_SERVICE" 2>/dev/null)
    [ -n "$pid" ] && [ "$pid" != "0" ] && kill -HUP "$pid" 2>/dev/null || true
    rm -f /run/gotelegram/web.bridge 2>/dev/null
    return 0
}

web_set_carrier() {
    case "${1:-}" in https|https-lanes|websocket|websocket-lanes) ;; *) log_error "$(t web_bad_carrier)"; return 1 ;; esac
    bot_update_config_field web_carrier "$1" >/dev/null 2>&1
    if telemt_web_enabled 2>/dev/null; then
        gt_with_config_lock _web_set_carrier_locked "$1" || { log_error "$(t web_apply_failed)"; return 1; }
    fi
    log_success "$(tf web_carrier_set "$1")"
    # Telegram для iPhone умеет только https — с другим carrier WEB там молчит
    [ "$1" = "https" ] || log_warning "$(t web_carrier_ios)"
}

# Самопроверка через приватный listener: считаем capability так же, как
# клиент, и смотрим, отдаёт ли ядро bridge-страницу. Наружу не ходит.
web_bridge_check() {
    local domain secret cap body cache="/run/gotelegram/web.bridge" age ttl cached
    # Каждый успешный запрос bridge выдаёт ядру неиспользованный bootstrap с
    # лимитом на адрес. Шапка и бот перерисовываются часто — держим УСПЕХ пять
    # минут, чтобы самопроверка не выедала лимит и не выглядела как сканер.
    # Отказ держим 20 секунд: сразу после рестарта ядра listener уже слушает, а
    # профили ещё не прогружены — единственная неудачная проба висела пять минут
    # и показывала «мост не отдаётся» на полностью рабочем WEB.
    if [ -r "$cache" ]; then
        cached=$(cat "$cache" 2>/dev/null)
        age=$(( $(date +%s) - $(stat -c %Y "$cache" 2>/dev/null || echo 0) ))
        ttl=300; [ "$cached" != "ok" ] && ttl=20
        if [ "$age" -ge 0 ] && [ "$age" -lt "$ttl" ]; then printf '%s' "$cached"; echo; return 0; fi
    fi
    # capability считается от имени WEB-vhost (при отдельном имени — не домен)
    domain=$(web_vhost_host 2>/dev/null)
    [ -n "$domain" ] || domain=$(config_get domain 2>/dev/null || echo "")
    secret=$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -n "$domain" ] && [ -n "$secret" ] && command -v python3 >/dev/null 2>&1 || { echo "skipped"; return 0; }
    cap=$(python3 - "$domain" "dd$secret" <<'PYCAP' 2>/dev/null
import sys, hmac, hashlib, base64
host, sec = sys.argv[1], bytes.fromhex(sys.argv[2])
ctx = ("tdesktop-web-proxy-bridge-v1\n" + host).encode()
print(base64.urlsafe_b64encode(hmac.new(sec, ctx, hashlib.sha256).digest()).decode().rstrip("="))
PYCAP
)
    [ -n "$cap" ] || { echo "skipped"; return 0; }
    # Две попытки с паузой: ядро отвечает decoy ещё несколько секунд после
    # рестарта, и одиночная проба записала бы отказ на рабочем мосте.
    local verdict="fail" try
    for try in 1 2; do
        body=$(curl -s --max-time 5 -H "Host: ${domain}" -H "X-Forwarded-For: 203.0.113.5" \
               "http://127.0.0.1:${WEB_LISTEN_PORT}/?bridge=${cap}" 2>/dev/null)
        # Без конвейера: под `set -o pipefail` связка printf|grep -q всегда даёт
        # ненулевой код — grep выходит на первом совпадении, printf получает
        # SIGPIPE (141), и вердикт был «отказ» даже на полностью рабочем мосте.
        case "$body" in *"/api/v1/session"*) verdict="ok"; break ;; esac
        [ "$try" = "1" ] && sleep 3
    done
    mkdir -p "$(dirname "$cache")" 2>/dev/null; printf '%s' "$verdict" > "$cache" 2>/dev/null
    echo "$verdict"
}

web_status_json() {
    local b enabled=false carrier domain listener=false profiles link="" bridge="skipped" supported=false
    b=$(web_blocker 2>/dev/null || echo "")
    type telemt_web_supported >/dev/null 2>&1 && telemt_web_supported && supported=true
    # «включён» по факту: флаг, Pro, ядро умеет, адрес есть. Один флаг после
    # даунгрейда ядра показывал бы «включён» при мёртвом listener'е.
    # …и WEB реально собран в конфиге ядра: в окне между сменой ядра и
    # согласованием флаг уже есть, а listener'а ещё нет.
    telemt_web_active 2>/dev/null && web_configured_in_core 2>/dev/null && enabled=true
    carrier=$(telemt_web_carrier 2>/dev/null || echo https)
    domain=$(config_get domain 2>/dev/null || echo "")
    ss -ltnH "( sport = :${WEB_LISTEN_PORT} )" 2>/dev/null | grep -q . && listener=true
    profiles=$(grep -c '^\[\[web\.vhosts\.profiles\]\]' "$TELEMT_CONFIG" 2>/dev/null); profiles=${profiles:-0}
    if [ "$enabled" = "true" ]; then
        link=$(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null || echo "")
        [ "$listener" = "true" ] && bridge=$(web_bridge_check)
    fi
    local configured=false front=false legacy=false web_host
    [ "$(config_get web_enabled 2>/dev/null || echo "")" = "true" ] && configured=true
    web_host=$(telemt_web_host 2>/dev/null)
    web_front_in_core 2>/dev/null && front=true
    # WEB включён по-старому, на основном домене: работает, но душит обычные
    # ссылки за тем же NAT — экраны показывают, что нужно задать отдельное имя
    [ "$enabled" = "true" ] && [ "$front" = "false" ] && legacy=true
    # Сколько адресов у сервера и какие из них имя WEB не покрывает (пусто —
    # все покрыты, имя не задано или DNS проверить не удалось)
    local server_ips=0 wmiss="" wjson="" _a
    if type edgeips_server_ips >/dev/null 2>&1; then
        server_ips=$(edgeips_server_ips | grep -c . || true)
        if [ -n "$web_host" ]; then
            wmiss=$(edgeips_dns_missing "$web_host"); case "$?" in 0|2) ;; *) wmiss="" ;; esac
        fi
    fi
    for _a in $wmiss; do wjson="${wjson:+${wjson},}\"${_a}\""; done
    printf '{"status":"ok","supported":%s,"enabled":%s,"configured":%s,"blocker":"%s","carrier":"%s","domain":"%s","web_host":"%s","front":%s,"legacy":%s,"listener":%s,"profiles":%s,"bridge":"%s","link":"%s","min_core":"%s","server_ips":%s,"dns_web_missing":[%s],"hint":"%s"}\n' \
        "$supported" "$enabled" "$configured" "$b" "$carrier" "$domain" "$web_host" "$front" "$legacy" "$listener" "${profiles:-0}" "$bridge" "$link" "$WEB_MIN_TELEMT" "${server_ips:-0}" "$wjson" "$(t web_hint | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

web_status_text() {
    local st
    telemt_web_active 2>/dev/null && st="$(t web_on)" || st="$(t web_off)"
    echo "  $(t web_title): ${st}"
    if telemt_web_active 2>/dev/null; then
        echo "  carrier: $(telemt_web_carrier)"
        web_front_in_core 2>/dev/null || echo "  $(t web_legacy_warn)"
        echo "  $(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null)"
        echo "  bridge: $(web_bridge_check)"
    fi
}

web_menu() {
    local src ch b link
    src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    while true; do
        b=$(web_blocker 2>/dev/null || echo "")
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t web_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${DIM}$(t web_desc)${NC}"
        if telemt_web_active 2>/dev/null; then
            echo -e "  $(t web_state): ${GREEN}$(t web_on)${NC}  ${DIM}carrier: $(telemt_web_carrier)${NC}"
            web_front_in_core 2>/dev/null || echo -e "  ${YELLOW}⚠ $(t web_legacy_warn)${NC}"
            link=$(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null || echo "")
            [ -n "$link" ] && echo -e "  ${CYAN}${link}${NC}"
            if ss -ltnH "( sport = :${WEB_LISTEN_PORT} )" 2>/dev/null | grep -q .; then
                case "$(web_bridge_check)" in
                    ok)   echo -e "  ${GREEN}✓ $(t web_bridge_ok)${NC}" ;;
                    fail) echo -e "  ${RED}✗ $(t web_bridge_fail)${NC}" ;;
                esac
            else
                echo -e "  ${RED}✗ $(t web_listener_down)${NC}"
            fi
        else
            echo -e "  $(t web_state): ${DIM}$(t web_off)${NC}"
            case "$b" in
                need_core)          echo -e "  ${YELLOW}$(tf web_need_core "$WEB_MIN_TELEMT")${NC}" ;;
                need_pro|need_cert|need_web_host|need_no_shared443) echo -e "  ${YELLOW}$(t "web_$b")${NC}" ;;
            esac
        fi
        echo -e "  $(t web_host_label): ${CYAN}$(telemt_web_host 2>/dev/null || true)${NC}"
        # Имя WEB (или домен, пока WEB живёт на нём по-старому) должно
        # указывать на каждый адрес сервера
        local _wdn _wdw
        _wdn=$(telemt_web_host 2>/dev/null || true)
        [ -z "$_wdn" ] && telemt_web_active 2>/dev/null && _wdn=$(config_get domain 2>/dev/null || echo "")
        if [ -n "$_wdn" ] && type edgeips_dns_warn >/dev/null 2>&1; then
            _wdw=$(edgeips_dns_warn "$_wdn")
            [ -n "$_wdw" ] && echo -e "  ${YELLOW}⚠ ${_wdw}${NC}"
        fi
        echo -e "  ${DIM}$(t web_hint)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${CYAN}1${NC}) $(t web_opt_on)"
        echo -e "  ${CYAN}2${NC}) $(t web_opt_off)"
        echo -e "  ${CYAN}3${NC}) $(tf web_opt_carrier "$(telemt_web_carrier)")"
        echo -e "  ${CYAN}4${NC}) $(t web_opt_host)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        read -r ch < "$src" || ch=0
        case "$ch" in
            1) if [ -z "$(telemt_web_host 2>/dev/null)" ]; then
                   echo -e "  ${DIM}$(tf web_host_prompt "$(config_get domain 2>/dev/null)")${NC}"
                   echo -ne "  ${WHITE}> ${NC}"
                   read -r ch < "$src" || ch=""
                   [ -n "$ch" ] && web_set_host "$ch" && web_enable
               else
                   web_enable
               fi ;;
            2) web_disable ;;
            4) echo -e "  ${DIM}$(tf web_host_prompt "$(config_get domain 2>/dev/null)")${NC}"
               echo -ne "  ${WHITE}> ${NC}"
               read -r ch < "$src" || ch=""
               [ -n "$ch" ] && web_set_host "$ch" ;;
            3) echo -e "  ${DIM}$(t web_carrier_prompt)${NC}"
               echo -ne "  ${WHITE}$(t choose):${NC} "
               read -r ch < "$src" || ch=""
               case "$ch" in
                   1) web_set_carrier https ;;
                   2) web_set_carrier websocket ;;
                   3) web_set_carrier https-lanes ;;
                   4) web_set_carrier websocket-lanes ;;
                   *) log_error "$(t invalid_choice)" ;;
               esac ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac
    done
}

# ── Версии TLS сайта и WEB (lib/website.sh: nginx_tls_*) ────────────────────
# Подпись текущего значения: «TLS 1.2 + 1.3» / «только TLS 1.2»; без TLS-сайта —
# «не используется».
tls_protocols_label() {
    if [ -n "$(nginx_tls_blocker 2>/dev/null)" ]; then printf '%s\n' "$(t tls_val_unused)"
    elif [ "$(nginx_tls_current 2>/dev/null)" = "TLSv1.2" ]; then printf '%s\n' "$(t tls_val_12)"
    else printf '%s\n' "$(t tls_val_both)"; fi
}

# Итог nginx_tls_set → «код<TAB>текст»: код — для JSON бота и панели, текст —
# для людей. $1 — rc, $2 — литерал, который ставили.
tls_protocols_outcome() {
    local lbl
    if [ "${2-}" = "TLSv1.2" ]; then lbl=$(t tls_val_12); else lbl=$(t tls_val_both); fi
    case "$1" in
        0) printf 'ok\t%s' "$(tf tls_set_ok "$lbl")" ;;
        9) printf 'unchanged\t%s' "$(tf tls_set_same "$lbl")" ;;
        2) if [ "$(nginx_tls_blocker 2>/dev/null)" = "mode" ]; then printf 'not_used\t%s' "$(t tls_unused_mode)"
           else printf 'no_site\t%s' "$(t tls_unused_no_site)"; fi ;;
        4) printf 'bad_value\t%s' "$(t tls_bad_value)" ;;
        5) printf 'config_write\t%s' "$(t tls_cfg_failed)" ;;
        6) printf 'nginx_test\t%s' "$(t tls_nginx_bad)" ;;
        8) printf 'rollback_failed\t%s' "$(t tls_rollback_failed)" ;;
        *) printf 'nginx_reload\t%s' "$(t tls_reload_failed)" ;;
    esac
}

# Экран «TLS сайта и WEB» (Прокси → 10). Выбор применяется сразу; EOF → назад.
tls_protocols_menu() {
    local src ch want rc out
    src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t tls_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
    case "$(nginx_tls_blocker 2>/dev/null)" in
        mode)    echo -e "  ${YELLOW}$(t tls_unused_mode)${NC}"; return 0 ;;
        no_site) echo -e "  ${YELLOW}$(t tls_unused_no_site)${NC}"; return 0 ;;
    esac
    echo -e "  $(t tls_now): ${BOLD}$(tls_protocols_label)${NC}"
    echo ""
    echo -e "  ${DIM}$(t tls_desc1)${NC}"
    echo -e "  ${DIM}$(t tls_desc2)${NC}"
    echo -e "  ${DIM}$(t tls_desc3)${NC}"
    echo ""
    echo -e "  ${CYAN}1${NC}) $(t tls_opt_both)"
    echo -e "  ${CYAN}2${NC}) $(t tls_opt_12)"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch < "$src" || ch=0
    [ "$ch" = "0" ] && return 0
    want=$(nginx_tls_menu_value "$ch") || { log_error "$(t invalid_choice)"; return 0; }
    nginx_tls_set "$want"; rc=$?
    out=$(tls_protocols_outcome "$rc" "$want")
    case "$rc" in
        0) log_success "${out#*$'\t'}" ;;
        9) log_info "${out#*$'\t'}" ;;
        *) log_error "${out#*$'\t'}" ;;
    esac
    return 0
}

# tls-protocols без --value= только читает: ни замка, ни миграции (как *-status).
_gt_tls_action_reads() {
    local a act="" val=0
    for a in "$@"; do
        case "$a" in
            --action=*) [ -n "$act" ] || act="${a#--action=}" ;;
            --value=*)  val=1 ;;
        esac
    done
    [ "$act" = "tls-protocols" ] && [ "$val" = 0 ]
}

miniapp_enable() {
    local mode domain p
    mode=$(config_get mode 2>/dev/null || echo lite)
    domain=$(config_get domain 2>/dev/null || echo "")
    if [ "$mode" != "pro" ] || [ -z "$domain" ]; then
        log_error "$(t ma_need_pro)"; return 1
    fi
    if [ "$(bot_service_status 2>/dev/null)" = "not_installed" ]; then
        log_error "$(t ma_need_bot)"; return 1
    fi
    p=$(config_get miniapp_path 2>/dev/null || echo "")
    [ -z "$p" ] && p="app-$(generate_hex 12)"
    bot_update_config_field miniapp_path "$p" >/dev/null 2>&1
    generate_nginx_config "$domain" 8443 >/dev/null 2>&1
    if ! nginx -t >/dev/null 2>&1; then
        log_error "$(t ma_nginx_bad)"
        bot_update_config_field miniapp_path "" >/dev/null 2>&1
        generate_nginx_config "$domain" 8443 >/dev/null 2>&1
        return 1
    fi
    systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx >/dev/null 2>&1
    # Перезапуск нужен ради КНОПКИ МЕНЮ в Telegram: при включённом мини-аппе она
    # становится кнопкой «Панель», а выставляет её _announce_self на старте бота.
    # Сам адрес бот читает из конфига каждый раз и в перезапуске не нуждается.
    # Когда действие пришло ОТ бота, перезапускать нельзя — он убьёт себя, не
    # успев ответить; в этом случае бот переставляет кнопку у себя же в процессе.
    [ "${GT_MINIAPP_NO_BOT_RESTART:-0}" = "1" ] || systemctl restart gotelegram-bot >/dev/null 2>&1
    log_success "$(t ma_enabled)"
    echo -e "  ${CYAN}$(miniapp_url)${NC}"
    log_info "$(t ma_hint)"
}

miniapp_disable() {
    local domain; domain=$(config_get domain 2>/dev/null || echo "")
    bot_update_config_field miniapp_path "" >/dev/null 2>&1
    [ -n "$domain" ] && generate_nginx_config "$domain" 8443 >/dev/null 2>&1
    systemctl reload nginx >/dev/null 2>&1
    [ "${GT_MINIAPP_NO_BOT_RESTART:-0}" = "1" ] || systemctl restart gotelegram-bot >/dev/null 2>&1
    log_success "$(t ma_disabled)"
}

# Веб-админка: экран был только в боте, в SSH пункта не существовало вовсе —
# панель ставится автоматически, а посмотреть её состояние и способ входа было
# негде. Экран сугубо справочный: ничего не запускает и не меняет.
admin_web_screen() {
    local st url
    case "$(admin_web_service_status 2>/dev/null)" in
        running) st="$(t aw_running)" ;;
        *)       st="$(t aw_stopped)" ;;
    esac
    url=$(miniapp_url 2>/dev/null || echo "")
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t aw_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
    echo -e "  $(t ma_state): ${CYAN}${st}${NC}"
    echo ""
    echo -e "  ${BOLD}$(t aw_tunnel)${NC}"
    echo -e "  ${CYAN}ssh -L ${ADMIN_WEB_PORT}:127.0.0.1:${ADMIN_WEB_PORT} root@$(config_get domain 2>/dev/null || get_server_ip)${NC}"
    echo -e "  ${DIM}$(tf aw_then "http://127.0.0.1:${ADMIN_WEB_PORT}/")${NC}"
    echo -e "  ${DIM}$(t aw_tunnel_note)${NC}"
    echo ""
    if [ -n "$url" ]; then
        echo -e "  ${BOLD}$(t aw_public)${NC}"
        echo -e "  ${CYAN}${url}${NC}"
        echo -e "  ${DIM}$(t ma_howto)${NC}"
        # Условие пишем рядом с адресом: человек копирует ссылку отсюда, и
        # узнать, что войти нечем, он должен здесь, а не на странице входа.
        if admin_web_tfa_ready; then
            echo -e "  ${GREEN}$(t aw_tfa_ok)${NC}"
        else
            echo -e "  ${YELLOW}$(t aw_tfa_no)${NC}"
        fi
        echo -e "  ${DIM}$(t ma_hint)${NC}"
    else
        # Тот же ответ, что даёт бот: ссылки нет не «просто так», а потому что
        # наружу панель не опубликована, и вот где это включается.
        echo -e "  ${DIM}$(tf aw_local_only "${ADMIN_WEB_PORT}")${NC}"
        # Подписи разделов в SSH выровнены пробелами под колонку меню; внутри
        # фразы это выглядит опечаткой, поэтому схлопываем.
        echo -e "  ${DIM}$(tf aw_how_public "$(t submenu_manage_title | tr -s ' ')" "$(t ma_title)")${NC}"
    fi
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
}

miniapp_menu() {
    local src ch url
    src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    while true; do
        url=$(miniapp_url 2>/dev/null)
        echo ""
        echo -e "  ${BOLD}${WHITE}$(t ma_title)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        if [ -n "$url" ]; then
            echo -e "  $(t ma_state): ${GREEN}$(t ma_on)${NC}"
            echo -e "  ${CYAN}${url}${NC}"
        else
            echo -e "  $(t ma_state): ${DIM}$(t ma_off)${NC}"
        fi
        echo -e "  ${DIM}$(t ma_security)${NC}"
        echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
        echo -e "  ${CYAN}1${NC}) $(t ma_opt_on)"
        echo -e "  ${CYAN}2${NC}) $(t ma_opt_off)"
        echo -e "  ${CYAN}3${NC}) $(t ma_opt_rotate)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        read -r ch < "$src" || ch=0
        case "$ch" in
            1) miniapp_enable ;;
            2) miniapp_disable ;;
            3) bot_update_config_field miniapp_path "" >/dev/null 2>&1; miniapp_enable ;;
            0) break ;;
            *) log_error "$(t invalid_choice)" ;;
        esac
    done
}


menu_remove() {
    echo ""
    echo -e "  ${BOLD}${RED}$(t remove_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -e "  ${CYAN}1${NC}) $(t remove_proxy_only)"
    echo -e "  ${CYAN}2${NC}) $(t remove_bot_only)"
    echo -e "  ${CYAN}3${NC}) $(t remove_all)"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r rm_choice

    case "$rm_choice" in
        1)
            log_warning "$(t remove_warn_proxy)"
            if ! confirm "$(t remove_confirm_proxy)"; then return; fi
            if confirm "$(t remove_backup_before)"; then
                interactive_backup
            fi
            remove_telemt
            local mode
            mode=$(config_get mode 2>/dev/null)
            if [ "$mode" = "pro" ]; then
                remove_pro_mode
            fi
            rm -f "$GOTELEGRAM_CONFIG"
            log_success "$(t remove_proxy_done)"
            ;;
        2)
            bot_remove
            ;;
        3)
            log_warning "$(t remove_warn_all)"
            if ! confirm "$(t remove_confirm_all)"; then return; fi
            if confirm "$(t remove_backup_before)"; then
                # Архив создаётся в $BACKUP_DIR ВНУТРИ удаляемого каталога: не унеси
                # мы его в /root — «подстраховку» снёс бы этот же rm -rf ниже.
                local _bk_mark _bk_f _bk_moved=""
                _bk_mark=$(mktemp) || _bk_mark=""
                interactive_backup
                if [ -n "$_bk_mark" ]; then
                    while IFS= read -r _bk_f; do
                        mv -f "$_bk_f" /root/ 2>/dev/null || continue
                        case "$_bk_f" in *.sha256) ;; *) _bk_moved="/root/$(basename "$_bk_f")" ;; esac
                    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -newer "$_bk_mark" 2>/dev/null)
                    rm -f "$_bk_mark"
                    [ -n "$_bk_moved" ] && log_success "Бекап сохранён вне удаляемого каталога: $_bk_moved"
                fi
            fi
            # Proxy
            type metrics_remove_timer >/dev/null 2>&1 && metrics_remove_timer
            # Всё, что живёт ВНЕ telemt: SYN-фикс (nft/iptables на :443 + юниты,
            # переприменяющиеся при каждой загрузке), V4, WARP-интерфейс с
            # маршрутизацией и таймер сборщика статистики. Без этого «удаление»
            # оставляло рабочие правила и включённые юниты на сервере.
            type mekofix_remove >/dev/null 2>&1 && mekofix_remove >/dev/null 2>&1
            type v4_uninstall >/dev/null 2>&1 && v4_uninstall >/dev/null 2>&1
            type warp_egress_disable >/dev/null 2>&1 && warp_egress_disable >/dev/null 2>&1
            type remove_stats_collector >/dev/null 2>&1 && remove_stats_collector >/dev/null 2>&1
            # Диспетчер 443 снимаем ПЕРВЫМ и до удаления telemt: иначе
            # stream-конфиг остаётся в nginx и продолжает держать 0.0.0.0:443,
            # отправляя всё на порт, где после удаления никто не слушает. Чужой
            # сервис на этом сервере (3x-ui) в такой ситуации выживал только на
            # своём явно прописанном домене, остальное молча умирало.
            # Возвращать telemt на 443 не просим — он всё равно удаляется.
            type shared443_disable >/dev/null 2>&1 && shared443_disable 0 >/dev/null 2>&1
            remove_telemt
            local mode
            mode=$(config_get mode 2>/dev/null)
            if [ "$mode" = "pro" ]; then
                remove_pro_mode
            fi
            rm -f "$GOTELEGRAM_CONFIG"
            # Bot
            if [ "$(bot_service_status)" != "not_installed" ]; then
                systemctl stop "$BOT_SERVICE" 2>/dev/null
                systemctl disable "$BOT_SERVICE" 2>/dev/null
                rm -f "/etc/systemd/system/${BOT_SERVICE}.service"
                systemctl daemon-reload
                rm -rf "$BOT_DIR"
            fi
            remove_admin_web
            # Хвосты, которые «Удалить всё» раньше не трогал (агент нашёл на живом
            # сервере): каталог внешнего SYN-фикса, логи/снапшоты метрик, маркеры
            # состояния и, наконец, сам /opt/gotelegram. Файлы работающего скрипта
            # можно удалить — на Linux inode живёт до выхода процесса.
            rm -rf /opt/gotelegram-mekofix /opt/gotelegram-warp 2>/dev/null || true
            rm -f /var/log/gotelegram-loss.log /var/log/gotelegram-cf.log 2>/dev/null || true
            rm -f /run/gotelegram-cf.snap /run/gotelegram/admin-users.lock 2>/dev/null || true
            rm -rf "$BACKUP_DIR" 2>/dev/null || true
            log_success "$(t remove_all_done)"
            # Раньше условие было завязано на SCRIPT_DIR — а он не выставлен, если
            # функцию дёрнули не из install.sh, и каталог оставался на диске.
            if [ -d "$GOTELEGRAM_DIR" ]; then
                # выходим и сносим установочный каталог фоном: сам скрипт живёт
                # в памяти до exit, файлы на диске убираем следом.
                ( sleep 1; rm -rf "$GOTELEGRAM_DIR" ) >/dev/null 2>&1 &
                log_info "$(t remove_all_bye)"
                exit 0
            fi
            ;;
    esac
}

# ── Telegram-бот ────────────────────────────────────────────────────────────
BOT_DIR="/opt/gotelegram-bot"
BOT_SERVICE="gotelegram-bot"

admin_web_service_status() {
    if ! systemctl list-unit-files "$ADMIN_WEB_SERVICE.service" &>/dev/null 2>&1; then
        echo "not_installed"
    elif systemctl is-active "$ADMIN_WEB_SERVICE" &>/dev/null 2>&1; then
        echo "running"
    else
        echo "stopped"
    fi
}

install_admin_web() {
    local src_dir="$SCRIPT_DIR/admin-web"
    [ -d "$src_dir" ] || { log_warning "admin-web files not found: $src_dir"; return 1; }
    command -v python3 &>/dev/null || { log_warning "python3 not found; web admin skipped"; return 1; }

    mkdir -p "$ADMIN_WEB_DIR/static"
    cp "$src_dir/server.py" "$ADMIN_WEB_DIR/server.py"
    cp -a "$src_dir/static/." "$ADMIN_WEB_DIR/static/"
    chmod 700 "$ADMIN_WEB_DIR"
    chmod 755 "$ADMIN_WEB_DIR/server.py" "$ADMIN_WEB_DIR/static"
    rm -f "$ADMIN_WEB_DIR/token" 2>/dev/null || true

    local python_bin
    python_bin=$(command -v python3)
    cat > "/etc/systemd/system/${ADMIN_WEB_SERVICE}.service" << SVCEOF
[Unit]
Description=goTelegram Pro v${GOTELEGRAM_VERSION} Local Web Admin
After=network.target

[Service]
Type=simple
WorkingDirectory=$ADMIN_WEB_DIR
ExecStart=$python_bin $ADMIN_WEB_DIR/server.py
Restart=always
RestartSec=5
Environment=GOTELEGRAM_ADMIN_HOST=$ADMIN_WEB_HOST
Environment=GOTELEGRAM_ADMIN_PORT=$ADMIN_WEB_PORT

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$ADMIN_WEB_SERVICE" &>/dev/null
    systemctl restart "$ADMIN_WEB_SERVICE" 2>/dev/null || systemctl start "$ADMIN_WEB_SERVICE"
    if type install_stats_collector &>/dev/null; then
        install_stats_collector >/dev/null 2>&1 || log_warning "stats collector was not started; open Web Admin traffic page and use Repair"
    fi
    log_success "Web admin installed: ${ADMIN_WEB_HOST}:${ADMIN_WEB_PORT}"
    # Сразу называем ОБА пути внутрь: иначе человек запоминает единственный
    # показанный — туннель — и про вход из браузера не узнаёт никогда.
    log_info "$(t aw_two_ways)"
}

auto_install_admin_web_if_possible() {
    [ -d "$SCRIPT_DIR/admin-web" ] || return 0
    command -v python3 &>/dev/null || return 0
    if [ "$(admin_web_service_status)" != "not_installed" ] && \
       [ -f "$ADMIN_WEB_DIR/server.py" ] && \
       cmp -s "$SCRIPT_DIR/admin-web/server.py" "$ADMIN_WEB_DIR/server.py" && \
       cmp -s "$SCRIPT_DIR/admin-web/static/index.html" "$ADMIN_WEB_DIR/static/index.html" && \
       cmp -s "$SCRIPT_DIR/admin-web/static/app.js" "$ADMIN_WEB_DIR/static/app.js" && \
       cmp -s "$SCRIPT_DIR/admin-web/static/styles.css" "$ADMIN_WEB_DIR/static/styles.css"; then
        return 0
    fi
    install_admin_web >/dev/null 2>&1 || true
}

remove_admin_web() {
    systemctl stop "$ADMIN_WEB_SERVICE" 2>/dev/null
    systemctl disable "$ADMIN_WEB_SERVICE" 2>/dev/null
    rm -f "/etc/systemd/system/${ADMIN_WEB_SERVICE}.service"
    systemctl daemon-reload 2>/dev/null
    rm -rf "$ADMIN_WEB_DIR"
}

bot_service_status() {
    if ! systemctl list-unit-files "$BOT_SERVICE.service" &>/dev/null 2>&1; then
        echo "not_installed"
    elif systemctl is-active "$BOT_SERVICE" &>/dev/null 2>&1; then
        echo "running"
    else
        echo "stopped"
    fi
}

auto_update_telemt_if_possible() {
    # If the binary is missing or no live config, skip — fresh install handles it later
    [ -x "$TELEMT_BIN" ] || return 0
    [ -f "$TELEMT_CONFIG" ] || return 0

    local installed target
    installed=$(get_installed_telemt_version 2>/dev/null || echo "")
    target=$(telemt_target_version 2>/dev/null || echo "")
    [ -z "$target" ] && return 0
    [ -z "$installed" ] && installed="unknown"
    [ "$installed" = "$target" ] && return 0

    log_step "telemt-ядро: $installed -> $target (выбранная версия)"
    # Stop service to safely swap the binary; auto_migrate_legacy_state has
    # already normalized config before this runs (see main() ordering).
    if systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null; then
        systemctl stop "$TELEMT_SERVICE" 2>/dev/null || true
    fi
    if download_telemt "$target" >/dev/null 2>&1; then
        log_success "telemt: $(get_installed_telemt_version 2>/dev/null || echo unknown)"
        type web_reconcile_after_core_change >/dev/null 2>&1 && web_reconcile_after_core_change
    else
        log_warning "Автоматическое обновление telemt не удалось; запустите menu 13 → Update telemt вручную"
    fi
    # Через start_telemt: при WEB на отдельном имени он сначала приводит nginx к
    # конфигу ядра — голый systemctl start поднял бы ядро без владельца 443
    start_telemt >&2 || true
}

auto_update_bot_if_possible() {
    [ -d "$SCRIPT_DIR/gotelegram-bot" ] || return 0
    [ "$(bot_service_status)" = "not_installed" ] && return 0
    [ -f "$BOT_DIR/.env" ] || return 0

    local needs_update=0
    [ -f "$SCRIPT_DIR/gotelegram-bot/bot.py" ] && \
        ! cmp -s "$SCRIPT_DIR/gotelegram-bot/bot.py" "$BOT_DIR/bot.py" && needs_update=1
    [ -f "$SCRIPT_DIR/gotelegram-bot/i18n.py" ] && \
        ! cmp -s "$SCRIPT_DIR/gotelegram-bot/i18n.py" "$BOT_DIR/i18n.py" && needs_update=1
    [ -f "$SCRIPT_DIR/gotelegram-bot/requirements.txt" ] && \
        ! cmp -s "$SCRIPT_DIR/gotelegram-bot/requirements.txt" "$BOT_DIR/requirements.txt" && needs_update=1

    local lang_file lang_name
    for lang_file in "$SCRIPT_DIR"/gotelegram-bot/lang/*.json; do
        [ -e "$lang_file" ] || continue
        lang_name=$(basename "$lang_file")
        ! cmp -s "$lang_file" "$BOT_DIR/lang/$lang_name" && needs_update=1
    done

    [ "$needs_update" = "1" ] || return 0
    # --auto: без мастера первого админа (он ждёт до 300с) и БЕЗ глушения stderr —
    # раньше обновление молча висело с пустым экраном ещё до показа баннера.
    bot_install --auto >/dev/null || \
        log_warning "Telegram bot auto-update failed; run menu 12 → Telegram-bot → Install/update"
}

menu_bot() {
    local st
    st=$(bot_service_status)

    echo ""
    echo -e "  ${BOLD}${WHITE}$(t bot_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"

    case "$st" in
        running)
            echo -e "  $(t bot_status_colon) ${GREEN}$(t bot_status_running)${NC}"
            echo ""
            echo -e "  ${CYAN}1${NC}) $(t bot_menu_status)"
            echo -e "  ${CYAN}2${NC}) $(t bot_menu_logs)"
            echo -e "  ${CYAN}3${NC}) $(t bot_menu_restart)"
            echo -e "  ${CYAN}4${NC}) $(t bot_menu_stop)"
            echo -e "  ${CYAN}5${NC}) $(t bot_menu_settings)"
            echo -e "  ${CYAN}6${NC}) $(t bot_menu_remove)"
            ;;
        stopped)
            echo -e "  $(t bot_status_colon) ${YELLOW}$(t bot_status_stopped)${NC}"
            echo ""
            echo -e "  ${CYAN}1${NC}) $(t bot_menu_status)"
            echo -e "  ${CYAN}2${NC}) $(t bot_menu_logs)"
            echo -e "  ${CYAN}3${NC}) $(t bot_menu_start)"
            echo -e "  ${CYAN}5${NC}) $(t bot_menu_settings)"
            echo -e "  ${CYAN}6${NC}) $(t bot_menu_remove)"
            ;;
        *)
            echo -e "  $(t bot_status_colon) ${RED}$(t bot_status_not_installed)${NC}"
            echo ""
            echo -e "  ${DIM}$(t bot_intro1)${NC}"
            echo -e "  ${DIM}$(t bot_intro2)${NC}"
            echo ""
            echo -e "  ${CYAN}1${NC}) $(t bot_menu_install)"
            ;;
    esac

    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch

    case "$st" in
        running)
            case "$ch" in
                1) bot_show_status ;;
                2) bot_show_logs ;;
                3) systemctl restart "$BOT_SERVICE" && log_success "$(t bot_restarted)" ;;
                4) systemctl stop "$BOT_SERVICE" && log_info "$(t bot_stopped)" ;;
                5) bot_edit_config ;;
                6) bot_remove ;;
            esac
            ;;
        stopped)
            case "$ch" in
                1) bot_show_status ;;
                2) bot_show_logs ;;
                3) systemctl start "$BOT_SERVICE" && log_success "$(t bot_started)" ;;
                5) bot_edit_config ;;
                6) bot_remove ;;
            esac
            ;;
        *)
            case "$ch" in
                1) bot_install ;;
            esac
            ;;
    esac
}

bot_install() {
    # --auto: неинтерактивный запуск из auto_update_bot_if_possible — без вопросов
    # и без ожидания первого админа (иначе 5 минут «зависания» до баннера).
    local auto="${1:-}"
    log_step "$(t bot_install_step)"

    # Python + venv + pip (always ensure — python3 can be present without venv/pip)
    local need_py=0
    command -v python3 &>/dev/null || need_py=1
    # python3-venv not having its own command; probe by trying 'python3 -m venv --help'
    if ! python3 -m venv --help &>/dev/null; then need_py=1; fi
    # pip check
    if ! python3 -m pip --version &>/dev/null; then need_py=1; fi

    if [ "$need_py" = "1" ]; then
        log_info "$(t bot_install_python)"
        if command -v apt-get &>/dev/null; then
            # Detect Python version for versioned venv package (Debian 12 / Ubuntu 24.04 need python3.12-venv)
            local py_ver=""
            if command -v python3 &>/dev/null; then
                py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
            fi

            apt_update

            # Build package list with versioned venv fallback
            local pkg_list=(python3 python3-venv python3-pip)
            [ -n "$py_ver" ] && pkg_list+=("python${py_ver}-venv")
            # python3-full optional
            if ! apt_install "${pkg_list[@]}" python3-full; then
                log_warning "python3-full unavailable, installing core packages only..."
                apt_install "${pkg_list[@]}" || {
                    log_error "Failed to install Python packages. Run manually: apt install ${pkg_list[*]}"
                    return 1
                }
            fi
        elif command -v dnf &>/dev/null; then
            dnf install -y -q python3 python3-pip
        elif command -v yum &>/dev/null; then
            yum install -y -q python3 python3-pip
        fi
    fi

    # Copy bot files
    mkdir -p "$BOT_DIR"
    if [ -f "$SCRIPT_DIR/gotelegram-bot/bot.py" ]; then
        cp "$SCRIPT_DIR/gotelegram-bot/bot.py" "$BOT_DIR/"
        cp "$SCRIPT_DIR/gotelegram-bot/requirements.txt" "$BOT_DIR/"
        [ -f "$SCRIPT_DIR/gotelegram-bot/config.example.env" ] && \
            cp "$SCRIPT_DIR/gotelegram-bot/config.example.env" "$BOT_DIR/"
        # Copy i18n language files for bot
        if [ -d "$SCRIPT_DIR/gotelegram-bot/lang" ]; then
            mkdir -p "$BOT_DIR/lang"
            cp -f "$SCRIPT_DIR/gotelegram-bot/lang/"*.json "$BOT_DIR/lang/" 2>/dev/null
        fi
        [ -f "$SCRIPT_DIR/gotelegram-bot/i18n.py" ] && \
            cp "$SCRIPT_DIR/gotelegram-bot/i18n.py" "$BOT_DIR/"
    else
        log_error "$(tf bot_files_not_found "$SCRIPT_DIR/gotelegram-bot/")"
        return 1
    fi

    # Templates catalog — skip if source and dest are the same file (symlink install case)
    if [ -f "$SCRIPT_DIR/templates_catalog.json" ]; then
        local src_tc="$SCRIPT_DIR/templates_catalog.json"
        local dst_tc="$GOTELEGRAM_DIR/templates_catalog.json"
        if [ "$(readlink -f "$src_tc" 2>/dev/null)" != "$(readlink -f "$dst_tc" 2>/dev/null)" ]; then
            cp "$src_tc" "$dst_tc"
        fi
    fi

    # Venv — create, and verify pip exists (python3-venv can silently create broken venv)
    if [ ! -d "$BOT_DIR/venv" ] || [ ! -x "$BOT_DIR/venv/bin/pip" ]; then
        log_info "$(t bot_create_venv)"
        rm -rf "$BOT_DIR/venv"
        if ! python3 -m venv "$BOT_DIR/venv" 2>/tmp/venv_err; then
            log_error "venv creation failed:"
            cat /tmp/venv_err >&2 2>/dev/null
            # Try to fix by installing versioned python3.X-venv package
            if command -v apt-get &>/dev/null; then
                local py_ver
                py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
                log_info "reinstalling python${py_ver}-venv..."
                apt_install python3-venv python3-pip "python${py_ver}-venv" python3-full || \
                    apt_install python3-venv python3-pip "python${py_ver}-venv" || true
                rm -rf "$BOT_DIR/venv"
                python3 -m venv "$BOT_DIR/venv" || { log_error "venv still broken, aborting. Manual fix: apt install python${py_ver}-venv python3-pip"; return 1; }
            else
                return 1
            fi
        fi
    fi

    if [ ! -x "$BOT_DIR/venv/bin/pip" ]; then
        log_info "bootstrapping pip via ensurepip..."
        "$BOT_DIR/venv/bin/python" -m ensurepip --upgrade 2>/dev/null || true
    fi

    if [ ! -x "$BOT_DIR/venv/bin/pip" ]; then
        log_error "pip missing in venv — install python3-venv manually: apt install python3-venv python3-pip"
        return 1
    fi

    log_info "$(t bot_install_deps)"
    if ! "$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" -q 2>/tmp/pip_err; then
        log_error "pip install failed:"
        tail -n 5 /tmp/pip_err >&2
        return 1
    fi

    # Sanity check: verify critical imports succeed
    if ! "$BOT_DIR/venv/bin/python" -c "import telegram, toml, dotenv" 2>/tmp/imp_err; then
        log_error "dependency import check failed:"
        cat /tmp/imp_err >&2
        return 1
    fi

    # Configuration
    if [ ! -f "$BOT_DIR/.env" ]; then
        # В авто-режиме спрашивать токен некому: prompt ушёл бы в /dev/null,
        # а скрытый read молча ждал бы ввода до бесконечности.
        [ "$auto" = "--auto" ] && return 1
        echo ""
        echo -e "  ${YELLOW}$(t bot_enter_token)${NC}"
        local token=""
        while [ -z "$token" ]; do
            echo -ne "  ${WHITE}$(t bot_token)${NC} "
            # EOF (оборванная SSH-сессия): без выхода read сразу отдаёт пусто,
            # и цикл крутится на 100% CPU до ручного kill.
            read -r token || { log_error "$(t bot_token_empty)"; return 1; }
            token=$(echo "$token" | tr -d '[:space:]')
            [ -z "$token" ] && log_error "$(t bot_token_empty)"
        done

        echo ""
        echo -e "  ${WHITE}$(t bot_add_admin_how)${NC}"
        echo -e "  ${CYAN}1${NC}) $(t bot_admin_auto)"
        echo -e "  ${CYAN}2${NC}) $(t bot_admin_manual)"
        echo -ne "  ${WHITE}$(t choose) [1]:${NC} "
        read -r admin_mode
        admin_mode="${admin_mode:-1}"

        local admin_ids=""
        if [ "$admin_mode" = "2" ]; then
            echo -ne "  ${WHITE}$(t bot_admin_ids_prompt)${NC} "
            read -r admin_ids
            admin_ids=$(echo "$admin_ids" | tr ' ' ',' | sed 's/,,*/,/g; s/^,//; s/,$//')
        fi

        # Propagate selected language to bot so UI matches
        local bot_lang
        bot_lang=$(get_language 2>/dev/null || echo en)
        {
            echo "BOT_TOKEN=$token"
            [ -n "$admin_ids" ] && echo "ALLOWED_IDS=$admin_ids"
            echo "BOT_LANG=$bot_lang"
        } > "$BOT_DIR/.env"
        chmod 600 "$BOT_DIR/.env"
        log_success "$(t bot_env_created)"
    else
        log_info "$(t bot_env_exists)"
    fi

    # Systemd
    cat > "/etc/systemd/system/${BOT_SERVICE}.service" << SVCEOF
[Unit]
Description=goTelegram Pro v${GOTELEGRAM_VERSION} Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=5
Environment=PATH=$BOT_DIR/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload
    systemctl enable "$BOT_SERVICE" &>/dev/null
    systemctl restart "$BOT_SERVICE" 2>/dev/null || systemctl start "$BOT_SERVICE"

    install_admin_web || log_warning "Web admin could not be installed"

    # If auto mode — wait until bot captures first admin
    local has_ids
    has_ids=$(grep "^ALLOWED_IDS=" "$BOT_DIR/.env" 2>/dev/null | cut -d= -f2)
    # При --auto мастер первого админа пропускаем: его 300-секундное ожидание со
    # спиннером в задушенный stderr и было «зависанием» установщика на 5 минут.
    if [ -z "$has_ids" ] && [ "$auto" != "--auto" ]; then
        echo ""
        # Simple bullet-style block (no box — printf %-Ns breaks on UTF-8 multibyte chars)
        echo -e "  ${YELLOW}▸${NC} ${BOLD}$(t bot_wait_admin_title)${NC}"
        echo ""
        echo -e "    $(t bot_wait_admin_msg1) ${CYAN}/start${NC}"
        echo -e "    $(t bot_wait_admin_msg2)"
        echo ""
        echo -e "    ${DIM}$(t bot_wait_admin_skip)${NC}"
        echo ""

        local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
        local i=0
        local waited=0
        local max_wait=300  # 5 min max

        # Catch Ctrl+C to skip waiting without killing the script
        local interrupted=0
        trap 'interrupted=1' INT

        while [ $waited -lt $max_wait ] && [ $interrupted -eq 0 ]; do
            printf "\r  ${CYAN}${frames[$i]}${NC} $(tf bot_wait_spinner "$waited")  " >&2
            i=$(( (i+1) % ${#frames[@]} ))
            sleep 1
            waited=$((waited + 1))

            # Check if ALLOWED_IDS has appeared
            has_ids=$(grep "^ALLOWED_IDS=" "$BOT_DIR/.env" 2>/dev/null | cut -d= -f2)
            if [ -n "$has_ids" ]; then
                break
            fi
        done

        trap - INT
        printf "\r\033[K" >&2  # clear spinner line

        if [ -n "$has_ids" ]; then
            echo ""
            log_success "$(t bot_admin_assigned)"
            echo -e "  ${WHITE}ID:${NC} ${GREEN}${has_ids}${NC}"
        elif [ $interrupted -eq 1 ]; then
            echo ""
            log_warning "$(t bot_wait_skipped)"
        else
            echo ""
            log_warning "$(t bot_wait_timeout)"
        fi
    fi

    echo ""
    log_success "$(t bot_installed)"
    echo -e "  ${DIM}systemctl status $BOT_SERVICE${NC}"
    echo -e "  ${DIM}journalctl -u $BOT_SERVICE -f${NC}"
}

bot_show_status() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t bot_status_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    systemctl status "$BOT_SERVICE" --no-pager -l 2>/dev/null | head -15 | while IFS= read -r line; do
        echo "  $line"
    done
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"

    if [ -f "$BOT_DIR/.env" ]; then
        local has_token has_ids
        has_token=$(grep -c "BOT_TOKEN=" "$BOT_DIR/.env" 2>/dev/null || echo 0)
        has_ids=$(grep "ALLOWED_IDS=" "$BOT_DIR/.env" 2>/dev/null | cut -d= -f2)
        if [ "${has_token:-0}" -gt 0 ]; then
            echo -e "  $(t bot_token)   ${GREEN}✓ $(t bot_token_configured)${NC}"
        fi
        if [ -n "$has_ids" ]; then
            echo -e "  $(t bot_access_colon)  $(tf bot_access_ids_fmt "$has_ids")"
        else
            echo -e "  $(t bot_access_colon)  ${YELLOW}$(t bot_access_open)${NC}"
        fi
    fi
}

bot_show_logs() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t bot_logs_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    journalctl -u "$BOT_SERVICE" --no-pager -n 30 2>/dev/null | while IFS= read -r line; do
        echo "  $line"
    done
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
}

# Безопасная запись KEY=VALUE в .env без sed: значение вида
# `x|;w /etc/cron.d/pwn` раньше подставлялось прямо в s|...|...| и позволяло
# записать произвольный файл от root. Ключи здесь только литеральные.
env_file_set() {
    local file="$1" key="$2" value="$3" tmp
    [ -f "$file" ] || : > "$file" || return 1
    # temp рядом с целью: mv в пределах одной ФС атомарен (бот может читать .env)
    tmp=$(mktemp "${file}.XXXXXX") || return 1
    grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    printf '%s=%s\n' "$key" "$value" >> "$tmp" || { rm -f "$tmp"; return 1; }
    chmod 600 "$tmp" 2>/dev/null || true
    mv "$tmp" "$file"
}

env_file_del() {
    local file="$1" key="$2" tmp
    [ -f "$file" ] || return 0
    tmp=$(mktemp "${file}.XXXXXX") || return 1
    grep -v "^${key}=" "$file" > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp" 2>/dev/null || true
    mv "$tmp" "$file"
}

bot_edit_config() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t bot_settings_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"

    if [ -f "$BOT_DIR/.env" ]; then
        echo -e "  ${DIM}$(t bot_current_env)${NC}"
        while IFS= read -r line; do
            # Mask token for security
            if [[ "$line" == BOT_TOKEN=* ]]; then
                local tok="${line#BOT_TOKEN=}"
                echo -e "  BOT_TOKEN=${tok:0:10}...${tok: -5}"
            else
                echo "  $line"
            fi
        done < "$BOT_DIR/.env"
    fi

    echo ""
    echo -e "  ${CYAN}1${NC}) $(t bot_change_token)"
    echo -e "  ${CYAN}2${NC}) $(t bot_change_allowed)"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch

    case "$ch" in
        1)
            echo -ne "  ${WHITE}$(t bot_new_token)${NC} "
            read -r new_token || new_token=""
            new_token=$(echo "$new_token" | tr -d '[:space:]')
            if [ -z "$new_token" ]; then
                log_error "$(t bot_token_empty_err)"
            elif ! [[ "$new_token" =~ ^[0-9]{5,}:[A-Za-z0-9_-]{30,}$ ]]; then
                log_error "Неверный формат токена (ожидается 123456789:AA...)"
            else
                env_file_set "$BOT_DIR/.env" BOT_TOKEN "$new_token" || log_error "Не удалось записать .env"
                systemctl restart "$BOT_SERVICE"
                log_success "$(t bot_token_updated)"
            fi
            ;;
        2)
            echo -ne "  ${WHITE}$(t bot_allowed_prompt)${NC} "
            read -r new_ids || new_ids=""
            # Normalize: spaces and commas → commas, strip extras
            new_ids=$(echo "$new_ids" | tr ' ' ',' | sed 's/,,*/,/g; s/^,//; s/,$//')
            if [ -n "$new_ids" ] && ! [[ "$new_ids" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
                log_error "Неверный список ID (ожидаются числа через запятую)"
            else
                if [ -n "$new_ids" ]; then
                    env_file_set "$BOT_DIR/.env" ALLOWED_IDS "$new_ids" || log_error "Не удалось записать .env"
                else
                    env_file_del "$BOT_DIR/.env" ALLOWED_IDS || log_error "Не удалось записать .env"
                fi
                systemctl restart "$BOT_SERVICE"
                log_success "$(t bot_access_updated)"
            fi
            ;;
    esac
}

bot_remove() {
    echo ""
    log_warning "$(t bot_remove_warn)"
    if ! confirm "$(t bot_remove_confirm)"; then
        return
    fi

    systemctl stop "$BOT_SERVICE" 2>/dev/null
    systemctl disable "$BOT_SERVICE" 2>/dev/null
    rm -f "/etc/systemd/system/${BOT_SERVICE}.service"
    systemctl daemon-reload
    rm -rf "$BOT_DIR"
    log_success "$(t bot_removed)"
}

# ── Promo ────────────────────────────────────────────────────────────────────
_promo_block() {
    # Print a promo section without width-fragile box borders (i18n safe)
    local line2; line2=$(printf '─%.0s' {1..54})
    local youtube_link="${GOTELEGRAM_YOUTUBE_LINK:-}"
    echo ""
    echo -e "  ${DIM}${line2}${NC}"
    echo -e "  ${BOLD}${YELLOW}$(t promo_host1_title)${NC}"
    echo -e "  $(t promo_link_label) ${CYAN}https://vk.cc/ct29NQ${NC}"
    echo -e "     ${WHITE}OFF60${NC}     — $(tf promo_off60)"
    echo -e "     ${WHITE}antenka20${NC} — $(tf promo_ant20)"
    echo -e "     ${WHITE}antenka6${NC}  — $(tf promo_ant6)"
    echo -e "  ${DIM}${line2}${NC}"
    echo -e "  ${BOLD}${YELLOW}$(t promo_host2_title)${NC}"
    echo -e "  $(t promo_link_label) ${CYAN}https://vk.cc/cUxAhj${NC}"
    echo -e "     ${WHITE}OFF60${NC}     — $(tf promo_off60)"
    echo -e "  ${DIM}${line2}${NC}"
    echo -e "  ${BOLD}${YELLOW}$(t promo_tips_title)${NC}"
    echo -e "  ${CYAN}https://pay.cloudtips.ru/p/7410814f${NC}"
    if [ -n "$youtube_link" ]; then
        echo -e "  ${DIM}${line2}${NC}"
        echo -e "  ${BOLD}${YELLOW}$(t promo_youtube_title)${NC}"
        echo -e "  $(t promo_link_label) ${CYAN}${youtube_link}${NC}"
    fi
    echo -e "  ${DIM}${line2}${NC}"
    echo ""
}

menu_promo() {
    _promo_block
}

# ── Проверка: показывать ли промо (раз в сутки) ────────────────────────────
should_show_promo() {
    local stamp_file="$GOTELEGRAM_DIR/.promo_last_shown"
    if [ ! -f "$stamp_file" ]; then
        return 0  # никогда не показывали
    fi
    local last_shown now diff
    last_shown=$(cat "$stamp_file" 2>/dev/null || echo "0")
    last_shown="${last_shown//[^0-9]/}"
    last_shown="${last_shown:-0}"
    now=$(date +%s)
    diff=$(( now - last_shown ))
    # 86400 = 24 часа
    [ "$diff" -ge 86400 ]
}

mark_promo_shown() {
    mkdir -p "$GOTELEGRAM_DIR"
    date +%s > "$GOTELEGRAM_DIR/.promo_last_shown"
}

_promo_qr() {
    local label="$1" url="$2"
    [ -n "$url" ] || return 0
    echo -e "  ${DIM}${label}${NC}"
    qrencode -t UTF8 -m 1 "$url" 2>/dev/null | while IFS= read -r qr_line; do
        echo "    $qr_line"
    done
}

# ── Promo with QR + delay (on install + once per day) ───────────────────
show_promo_with_qr() {
    _promo_block

    if command -v qrencode &>/dev/null; then
        _promo_qr "$(t promo_qr_host1)" "https://vk.cc/ct29NQ"
        _promo_qr "$(t promo_qr_host2)" "https://vk.cc/cUxAhj"
        _promo_qr "$(t promo_qr_tips)" "https://pay.cloudtips.ru/p/7410814f"
        _promo_qr "$(t promo_qr_youtube)" "${GOTELEGRAM_YOUTUBE_LINK:-}"
    fi

    mark_promo_shown

    # 5-second countdown
    for i in 5 4 3 2 1; do
        echo -ne "\r  ${DIM}$(tf promo_menu_in "$i")${NC}  "
        sleep 1
    done
    echo -ne "\r                                            \r"
}

# ── First-run: pick language ─────────────────────────────────────────────────
first_run_language_picker() {
    # Show picker only if language not yet saved
    local marker="${GOTELEGRAM_DIR:-/opt/gotelegram}/.language"
    local cfg_lang=""
    if [ -f "$GOTELEGRAM_CONFIG" ] && command -v jq >/dev/null 2>&1; then
        cfg_lang=$(jq -r '.language // empty' "$GOTELEGRAM_CONFIG" 2>/dev/null)
    fi
    if [ -f "$marker" ] || [ -n "$cfg_lang" ]; then
        return 0
    fi

    local chosen
    chosen=$(pick_language_interactive)
    save_language "$chosen"
    load_language "$chosen"
}

# ── Change language on demand ────────────────────────────────────────────────
menu_language() {
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t lang_change_prompt)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -e "  ${CYAN}1${NC}) English"
    echo -e "  ${CYAN}2${NC}) Русский"
    echo -e "  ${CYAN}0${NC}) $(t back)"
    echo -ne "  ${WHITE}$(t choose):${NC} "
    read -r ch
    case "$ch" in
        1) save_language "en"; load_language "en"; log_success "$(tf lang_saved English)" ;;
        2) save_language "ru"; load_language "ru"; log_success "$(tf lang_saved Русский)" ;;
    esac
}

# ══════════════════════════════════════════════════════════════════════════════
# Non-interactive action dispatcher (bot / CI / scripting interface)
# ══════════════════════════════════════════════════════════════════════════════
# Usage examples:
#   gotelegram --action=change-template --template=th_ariclaw --json
#   gotelegram --action=change-lite-domain --domain=google.com --json
#
# Rules for action handlers:
#  - Only JSON may be written to stdout (the caller parses it).
#  - All human-oriented logging must go to stderr (log_* already do that).
#  - Exit code 0 on success, non-zero on failure (caller still parses JSON).
# ══════════════════════════════════════════════════════════════════════════════

bot_emit_json() {
    # bot_emit_json <status> <message> [key=value ...]
    local status="$1"; shift
    local message="$1"; shift
    local extra="" kv k v
    for kv in "$@"; do
        k="${kv%%=*}"
        v="${kv#*=}"
        # escape backslashes and double quotes in value
        v="${v//\\/\\\\}"
        v="${v//\"/\\\"}"
        extra="${extra},\"${k}\":\"${v}\""
    done
    # escape message
    local msg_esc="${message//\\/\\\\}"
    msg_esc="${msg_esc//\"/\\\"}"
    printf '{"status":"%s","message":"%s"%s}\n' "$status" "$msg_esc" "$extra"
}

# Update a single key in config.json without rewriting the whole file.
# Uses `date -Iseconds` rather than jq's `now | todate` — the latter requires
# jq 1.6+ which is not available on Debian 10 or older CentOS.
bot_update_config_field() {
    local key="$1"
    local value="$2"
    if [ ! -f "$GOTELEGRAM_CONFIG" ]; then
        return 1
    fi
    local tmp now
    tmp=$(mktemp) || return 1
    now=$(date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z)
    if jq --arg k "$key" --arg v "$value" --arg t "$now" \
        '.[$k] = $v | .updated_at = $t' \
        "$GOTELEGRAM_CONFIG" > "$tmp" 2>/dev/null; then
        mv "$tmp" "$GOTELEGRAM_CONFIG"
        chmod 600 "$GOTELEGRAM_CONFIG"
        return 0
    fi
    rm -f "$tmp"
    return 1
}

# ── Action: change-template (pro mode only) ──────────────────────────────────
bot_action_change_template() {
    local tpl_id="$1"
    local json_out="${2:-0}"

    if [ -z "$tpl_id" ]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "template id is required" "code=missing_arg"
        log_error "change-template: --template is required"
        return 2
    fi

    # Строгая валидация ДО любой работы с ФС: id уходит в пути внутри
    # templates_catalog (там есть rm -rf), т.е. "../../.." опасен.
    if ! [[ "$tpl_id" =~ ^[A-Za-z0-9_-]{1,64}$ ]]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "недопустимый id шаблона" "code=bad_template_id"
        log_error "change-template: invalid template id: $tpl_id"
        return 4
    fi

    # Must be in pro mode
    local mode
    mode=$(config_get mode 2>/dev/null || echo "")
    if [ "$mode" != "pro" ]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "change-template requires pro mode (current: ${mode:-none})" "code=wrong_mode"
        log_error "change-template: current mode is '${mode:-none}', requires 'pro'"
        return 3
    fi

    # Validate template id exists in catalog
    if ! get_template_info "$tpl_id" >/dev/null 2>&1; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "unknown template: $tpl_id" "code=unknown_template"
        log_error "change-template: template not found in catalog: $tpl_id"
        return 4
    fi

    # Make sure git (and other deps) are present. download_template uses git
    # clone under the hood — on a minimal host (bootstrap-only install) git may
    # not be installed yet, and the clone would fail silently.
    ensure_deps >&2

    log_info "change-template: downloading $tpl_id..."
    local template_dir
    TEMPLATE_DL_ERROR=""
    # Кэш создаём ЗДЕСЬ, до $(...): иначе подоболочка заведёт свой каталог, и
    # файл с кодом причины (.last_error) останется нам неизвестен.
    type _templates_cache_dir >/dev/null 2>&1 && _templates_cache_dir >/dev/null 2>&1
    template_dir=$(download_template "$tpl_id")
    if [ $? -ne 0 ] || [ -z "$template_dir" ] || [ ! -d "$template_dir" ] || [ ! -f "$template_dir/index.html" ]; then
        # Код причины — боту: «репозиторий без готовой страницы» и «не скачалось»
        # лечатся по-разному (другой шаблон против связи с GitHub).
        local _code="${TEMPLATE_DL_ERROR:-}" _msg
        [ -z "$_code" ] && [ -n "${TEMPLATES_CACHE:-}" ] && _code=$(cat "$TEMPLATES_CACHE/.last_error" 2>/dev/null)
        [ -z "$_code" ] && _code="download_failed"
        case "$_code" in
            no_index)      _msg="template has no static index.html (build sources, not a site)" ;;
            app_shell)     _msg="template page is an empty application shell" ;;
            broken_assets) _msg="template references styles/scripts missing from the repository" ;;
            *)             _code="download_failed"; _msg="download failed for $tpl_id (repository unreachable)" ;;
        esac
        [ "$json_out" = "1" ] && bot_emit_json "error" "$_msg" "code=$_code"
        log_error "change-template: download_template failed for $tpl_id ($_code)"
        return 5
    fi

    log_info "change-template: deploying to nginx..."
    if ! deploy_template_to_nginx "$template_dir" >&2; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "deploy failed" "code=deploy_failed"
        return 6
    fi

    # Reload nginx (no full restart needed for static files — but be safe)
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null

    # Update config.json template_id field
    bot_update_config_field "template_id" "$tpl_id" || \
        log_warning "change-template: could not update config.json template_id"

    local domain
    domain=$(config_get domain 2>/dev/null || echo "")
    log_success "change-template: $tpl_id deployed"

    if [ "$json_out" = "1" ]; then
        bot_emit_json "success" "template changed to $tpl_id" \
            "template=$tpl_id" "domain=$domain" "mode=pro"
    fi
    return 0
}

# ── Action: change-lite-domain ───────────────────────────────────────────────
# Regenerates telemt TOML with a new fake-TLS mask domain. Lite mode only.
bot_action_change_lite_domain() {
    local new_domain="$1"
    local json_out="${2:-0}"

    if [ -z "$new_domain" ]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "domain is required" "code=missing_arg"
        log_error "change-lite-domain: --domain is required"
        return 2
    fi

    if ! validate_domain "$new_domain" 2>/dev/null; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "invalid domain: $new_domain" "code=invalid_domain"
        log_error "change-lite-domain: invalid domain: $new_domain"
        return 3
    fi

    local mode
    mode=$(config_get mode 2>/dev/null || echo "")
    if [ "$mode" != "lite" ]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "change-lite-domain requires lite mode (current: ${mode:-none})" "code=wrong_mode"
        log_error "change-lite-domain: current mode is '${mode:-none}', requires 'lite'"
        return 4
    fi

    local secret port
    secret=$(get_config_value secret 2>/dev/null || echo "")
    port=$(get_config_value port 2>/dev/null || echo "443")

    if [ -z "$secret" ]; then
        [ "$json_out" = "1" ] && bot_emit_json "error" "no secret in config" "code=no_secret"
        log_error "change-lite-domain: no secret in config.json"
        return 5
    fi

    # generate_telemt_toml пишет [users] заново, только с main = "$secret" —
    # без сохранения блока смена домена стирала ВСЕ ключи пользователей.
    local ub=""
    if type get_telemt_users_block >/dev/null 2>&1; then
        ub=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null || true)
    fi

    log_info "change-lite-domain: regenerating telemt TOML..."
    generate_telemt_toml "$secret" "$port" "lite" "$new_domain" "443" >&2 || {
        [ "$json_out" = "1" ] && bot_emit_json "error" "config generation failed" "code=gen_failed"
        return 6
    }
    if [ -n "$ub" ] && type replace_telemt_users_block >/dev/null 2>&1; then
        replace_telemt_users_block "$ub" "$TELEMT_CONFIG" >&2 2>/dev/null || true
    fi

    validate_telemt_config >&2 || {
        [ "$json_out" = "1" ] && bot_emit_json "error" "config validation failed" "code=validate_failed"
        return 7
    }

    restart_telemt >&2 || {
        [ "$json_out" = "1" ] && bot_emit_json "error" "telemt restart failed" "code=restart_failed"
        return 8
    }

    # Update both domain and mask_host fields in config.json
    bot_update_config_field "mask_host" "$new_domain" || \
        log_warning "change-lite-domain: could not update mask_host"
    bot_update_config_field "domain" "$new_domain" || \
        log_warning "change-lite-domain: could not update domain"

    log_success "change-lite-domain: switched to $new_domain"

    if [ "$json_out" = "1" ]; then
        bot_emit_json "success" "lite mask domain changed to $new_domain" \
            "domain=$new_domain" "mode=lite" "port=$port"
    fi
    return 0
}

# Main dispatcher — called from main() when --action=X is present.
# Uses a file lock (flock) so concurrent CLI invocations (from multiple bot
# users, or from bot + manual CLI) serialize cleanly. Without this, two
# parallel `change-lite-domain` calls raced on the jq-rewrite of config.json
# and one process would see a truncated file ("no secret in config").
bot_action_dispatch() {
    # Читающие действия замок НЕ берут. Панель опрашивает edgeips-status каждые
    # пять секунд, и на время долгого действия (web-on до 300 с, warp-on 240 с,
    # восстановление) весь интерфейс вставал: обзор ждал по 30 с и обрывался по
    # таймауту фронта, карточки исчезали. Они ничего не пишут — сериализовать их
    # не с чем; конкурирующая запись им и так не мешает (файлы обновляются
    # атомарной подменой).
    local _act="" _a
    for _a in "$@"; do case "$_a" in --action=*) _act="${_a#--action=}"; break ;; esac; done
    case "$_act" in
        *-status|*-probe|quality|warp-coverage)
            _bot_action_dispatch_locked "$@"
            return $? ;;
    esac
    # tls-protocols без --value — тоже только чтение
    if _gt_tls_action_reads "$@"; then _bot_action_dispatch_locked "$@"; return $?; fi

    # Тот же файл, что берут бот и веб-панель (bot.py USER_LOCK_FILE), иначе
    # --action и прямые записи из панели сериализуются независимо и гонятся.
    local lock_file="${GT_CONFIG_LOCK:-/run/gotelegram/admin-users.lock}"
    mkdir -p "$(dirname "$lock_file")" 2>/dev/null || true
    # Make sure /var/lock exists (it does on Debian/Ubuntu; be defensive for minimal images)
    [ -d /var/lock ] || mkdir -p /var/lock 2>/dev/null || true

    if command -v flock >/dev/null 2>&1; then
        # Wait up to 30 seconds for the lock — bot actions are fast (<5s
        # typical), so 30s is plenty for legitimate serialization but short
        # enough to surface a stuck process.
        (
            flock -w 30 9 || {
                # If we time out, emit JSON error for the bot parent.
                local json_out=0 a
                for a in "$@"; do
                    [ "$a" = "--json" ] && json_out=1
                done
                if [ "$json_out" = "1" ]; then
                    bot_emit_json "error" "another action in progress (lock timeout)" "code=lock_timeout"
                fi
                exit 75  # EX_TEMPFAIL
            }
            # Замок этого файла уже наш: вложенные gt_with_config_lock (apply_preset
            # и т.п.) должны выполнить тело, а не ждать тот же файл — взаимоблокировка.
            GT_CONFIG_LOCK_HELD=1 _bot_action_dispatch_locked "$@"
        ) 9>"$lock_file"
        return $?
    else
        # No flock installed — run unlocked with a warning. ensure_deps/check_deps
        # normally ensures util-linux is present, so this branch is defensive.
        log_warning "flock not available — bot actions not serialized"
        _bot_action_dispatch_locked "$@"
        return $?
    fi
}

# Снять внешний SYN-фикс и УБЕДИТЬСЯ, что он действительно снят.
# Раньше в обоих местах (mekofix-off и preset-apply) коды возврата v4_uninstall
# и mekofix_remove не проверялись, вывод гасился, а поле конфига `mekofix`
# обнулялось следующей строкой безусловно. При неудачном снятии все три
# интерфейса показывали «фикс выключен», пока правила висели на файрволе —
# ровно то расхождение, которое §5 AUDIT.md называет главной путаницей узла.
# Судим по ФАКТУ (правил больше нет), а не по кодам возврата, и чистим поле
# только после подтверждения.
_gt_drop_external_fix() {
    if type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null; then
        type v4_uninstall >/dev/null 2>&1 && v4_uninstall >/dev/null 2>&1
    fi
    if type mekofix_is_active >/dev/null 2>&1 && mekofix_is_active 2>/dev/null; then
        mekofix_remove >/dev/null 2>&1
    fi
    if type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null; then
        return 1
    fi
    if type mekofix_is_active >/dev/null 2>&1 && mekofix_is_active 2>/dev/null; then
        return 1
    fi
    bot_update_config_field mekofix "" >/dev/null 2>&1
    return 0
}

_bot_action_dispatch_locked() {
    local action="" tpl_id="" domain="" key="" version="" preset="" limited="" carrier="" host="" value="" has_value=0 json_out=0 arg
    for arg in "$@"; do
        case "$arg" in
            --action=*)   action="${arg#--action=}" ;;
            --template=*) tpl_id="${arg#--template=}" ;;
            --domain=*)   domain="${arg#--domain=}" ;;
            --key=*)      key="${arg#--key=}" ;;
            --version=*)  version="${arg#--version=}" ;;
            --limited=*)  limited="${arg#--limited=}" ;;
            --preset=*)   preset="${arg#--preset=}" ;;
            --carrier=*)  carrier="${arg#--carrier=}" ;;
            --host=*)     host="${arg#--host=}" ;;
            --value=*)    value="${arg#--value=}"; has_value=1 ;;
            --json)       json_out=1 ;;
        esac
    done

    case "$action" in
        # ── SYN-фикс и анти-DPI режим: до 2.9.7 управлять ими можно было ТОЛЬКО
        # по SSH. Человек с одним ботом в момент блокировки не мог ни посмотреть
        # состояние фикса, ни переключить версию, ни увидеть потери до Telegram —
        # то есть именно то, ради чего продукт и нужен.
        mekofix-status)
            local mf_cur mf_state mf_ver mf_rate v4on mf_limited="false"
            mf_cur=$(config_get mekofix 2>/dev/null || echo "")
            mf_state="off"; mf_ver=""; mf_rate=""
            if type mekofix_is_active >/dev/null 2>&1 && mekofix_is_active 2>/dev/null; then
                mf_state="on"
                mf_ver=$(mekofix_state_get version 2>/dev/null)
                if [ "$(mekofix_state_get nolimit 2>/dev/null)" = "1" ]; then
                    mf_rate="без ограничения"; mf_limited="false"
                else
                    mf_rate="$(mekofix_state_get rate 2>/dev/null) burst $(mekofix_state_get burst 2>/dev/null)"; mf_limited="true"
                fi
            fi
            v4on="false"
            if type v4_is_active >/dev/null 2>&1 && v4_is_active 2>/dev/null; then
                v4on="true"; mf_state="on"; mf_ver="v4"
                mf_rate="$(type v4_mode_desc >/dev/null 2>&1 && v4_mode_desc 2>/dev/null || echo 'zapret2')"
            fi
            if [ "$json_out" = "1" ]; then
                printf '{"status":"ok","state":"%s","version":"%s","limits":"%s","limited":%s,"v4":%s,"configured":"%s"}\n' \
                    "$mf_state" "${mf_ver:-}" "${mf_rate:-}" "$mf_limited" "$v4on" "${mf_cur:-}"
            else
                echo "SYN-фикс: ${mf_state} ${mf_ver:-} ${mf_rate:-}"
            fi
            return 0 ;;
        mekofix-apply)
            case "$version" in
                v2|v3|v4) ;;
                *) [ "$json_out" = "1" ] && bot_emit_json "error" "нужна версия v2, v3 или v4" "code=bad_version"; return 2 ;;
            esac
            # V4 внутри спрашивает подтверждение через /dev/tty. В боте и панели
            # никакого tty нет, и установка молча заканчивалась «Отменено» — то
            # есть выбрать V4 из бота было невозможно в принципе. Нажатие кнопки
            # (после экрана с предупреждением «ЭКСПЕРИМЕНТАЛЬНО») и есть согласие.
            if [ "$version" = "v4" ]; then export V4_NO_CONFIRM=1; fi
            # limited=1 → лимит 54/мин; иначе режим без ограничения (основной)
            export MEKOFIX_NOLIMIT="$([ "$limited" = "1" ] && echo 0 || echo 1)"
            if type gt_ensure_synfix >/dev/null 2>&1 && gt_ensure_synfix "$version" >&2; then
                # Паритет с SSH-меню (§4 ТЗ): фикс работает в паре с MEKO style —
                # иначе один и тот же режим из бота и из SSH давал разный telemt.
                if ! gt_fix_pair_preset; then
                    [ "$json_out" = "1" ] && bot_emit_json "error" "SYN-фикс ${version} включён, но режим MEKO (#4) не применился" "code=preset_failed"
                    return 1
                fi
                [ "$json_out" = "1" ] && bot_emit_json "ok" "SYN-фикс ${version} включён"
                return 0
            fi
            [ "$json_out" = "1" ] && bot_emit_json "error" "не удалось включить ${version}" "code=apply_failed"
            return 1 ;;
        mekofix-off)
            if ! _gt_drop_external_fix; then
                [ "$json_out" = "1" ] && bot_emit_json "error" "правила SYN-фикса снять не удалось" "code=remove_failed"
                log_error "mekofix-off: правила остались на файрволе — состояние в конфиге не меняю, чтобы интерфейс не врал"
                return 1
            fi
            [ "$json_out" = "1" ] && bot_emit_json "ok" "SYN-фикс выключен, правила сняты"
            return 0 ;;
        preset-status)
            local p_cur p_list p_name
            p_cur=$(config_get preset_id 2>/dev/null || echo "${DIAG_DEFAULT_PRESET:-4}")
            p_list=$(diag_preset_ids 2>/dev/null | tr '\n' ',' | sed 's/,$//')
            p_name=$(_diag_row "$p_cur" 2>/dev/null | awk -F'|' '{print $2}')
            if [ "$json_out" = "1" ]; then
                printf '{"status":"ok","current":"%s","name":"%s","available":"%s"}\n' "$p_cur" "${p_name:-}" "${p_list:-}"
            else
                echo "Режим: #${p_cur} ${p_name:-} (доступны: ${p_list:-})"
            fi
            return 0 ;;
        preset-apply)
            if ! _diag_row "$preset" >/dev/null 2>&1; then
                [ "$json_out" = "1" ] && bot_emit_json "error" "режима #${preset} нет" "code=bad_preset"
                return 2
            fi
            # Режимы 4-6 — чистый анти-DPI профиль БЕЗ внешнего фикса. Меню и бот
            # снимали фикс сами перед вызовом, а прямой вызов контракта — нет: и
            # тогда пресет «выбран», а по факту продолжает работать V3 или V4, и
            # все интерфейсы показывают фикс. Снимаем здесь, чтобы контракт вёл
            # себя так же, как кнопки.
            # Снятие обязано быть ПОДТВЕРЖДЁННЫМ: пресет, применённый поверх
            # оставшихся правил, — это не тот режим, который человек выбрал.
            if ! _gt_drop_external_fix; then
                [ "$json_out" = "1" ] && bot_emit_json "error" "не удалось снять внешний SYN-фикс — режим не менял" "code=remove_failed"
                log_error "preset-apply: правила внешнего фикса остались, режим #${preset} не применяю"
                return 1
            fi
            if apply_preset "$preset" >&2; then
                [ "$json_out" = "1" ] && bot_emit_json "ok" "режим #${preset} применён"
                return 0
            fi
            [ "$json_out" = "1" ] && bot_emit_json "error" "не удалось применить режим" "code=apply_failed"
            return 1 ;;
        quality)
            if type metrics_quality_json >/dev/null 2>&1; then
                metrics_quality_json
            else
                [ "$json_out" = "1" ] && bot_emit_json "error" "модуль метрик недоступен" "code=no_metrics"
                return 1
            fi
            return 0 ;;
        change-template)
            bot_action_change_template "$tpl_id" "$json_out"
            return $?
            ;;
        change-lite-domain)
            bot_action_change_lite_domain "$domain" "$json_out"
            return $?
            ;;
        warp-on)
            warp_egress_enable "$key" >&2; local wrc=$?
            local wnote=""; [ -s /run/gotelegram-warp.note ] && wnote=$(cat /run/gotelegram-warp.note 2>/dev/null)
            if [ "$json_out" = "1" ]; then
                if [ "$wrc" = "0" ]; then
                    if [ -n "$wnote" ] && [ "$wnote" != "tg-unreachable" ]; then bot_emit_json "warn" "WARP включён как Free (ключ WARP+ отвергнут: $wnote)" "code=$wnote"; else bot_emit_json "ok" "WARP-egress включён"; fi
                elif [ "$wnote" = "me-unreachable" ]; then bot_emit_json "error" "WARP не включён: порт 8888 (middle-proxy) через Cloudflare с этого сервера не проходит. Прямой egress сохранён, прокси работает." "code=me_unreachable"
                elif [ "$wnote" = "tg-unreachable" ]; then bot_emit_json "error" "Telegram недоступен через Cloudflare с этого сервера — WARP не включён (telemt на прямом egress)" "code=tg_unreachable"
                else bot_emit_json "error" "не удалось включить WARP" "code=warp_failed"; fi
            fi
            return $wrc ;;
        warp-off)
            warp_egress_disable >&2
            [ "$json_out" = "1" ] && bot_emit_json "ok" "WARP-egress выключен"
            return 0 ;;
        warp-key)
            local wreason; wreason=$(warp_egress_apply_key "$key")
            if [ "$json_out" = "1" ]; then
                case "$wreason" in
                    ok)   bot_emit_json "ok" "ключ WARP+ применён (plus)" ;;
                    free) bot_emit_json "ok" "переключено на free" ;;
                    me-unreachable) bot_emit_json "error" "Порт 8888 (middle-proxy) через Cloudflare не проходит — WARP не включён, прокси остался на прямом egress." "code=me_unreachable" ;;
                    *)    bot_emit_json "warn" "ключ не применён: $wreason" "code=$wreason" ;;
                esac
            fi
            { [ "$wreason" = "ok" ] || [ "$wreason" = "free" ]; } && return 0 || return 1 ;;
        edgeips-status)
            # Читающее действие: попадает под *-status и миграцию не тянет.
            if ! type edgeips_status_json >/dev/null 2>&1; then
                if [ "$json_out" = "1" ]; then
                    bot_emit_json "error" "модуль учёта адресов не загружен" "code=no_edgeips"
                else
                    # Без --json раньше выходили молча с кодом 1: человек в
                    # консоли не понимал, почему ничего не произошло.
                    log_error "модуль учёта адресов не загружен (lib/edgeips.sh)"
                fi
                return 1
            fi
            if [ "$json_out" = "1" ]; then edgeips_status_json; else edgeips_status; fi
            return 0 ;;
        web-status)
            # Читающее действие: попадает под *-status и миграцию не тянет.
            if [ "$json_out" = "1" ]; then web_status_json; else web_status_text; fi
            return 0 ;;
        web-on)
            if web_enable >/dev/null 2>&1; then
                local _wl; _wl=$(web_link_for_secret "$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null)" 2>/dev/null || echo "")
                [ "$json_out" = "1" ] && bot_emit_json "ok" "$(t web_enabled)" "link=$_wl" || log_success "$(t web_enabled)"
                return 0
            fi
            local _wb _wmsg
            _wb=$(web_blocker 2>/dev/null || echo ""); [ -n "$_wb" ] || _wb="apply_failed"
            case "$_wb" in need_core) _wmsg=$(tf web_need_core "$WEB_MIN_TELEMT") ;; *) _wmsg=$(t "web_$_wb") ;; esac
            [ "$json_out" = "1" ] && bot_emit_json "error" "$_wmsg" "code=$_wb" || log_error "$_wmsg"
            return 1 ;;
        web-carrier)
            case "$carrier" in
                https|https-lanes|websocket|websocket-lanes) ;;
                *) [ "$json_out" = "1" ] && bot_emit_json "error" "$(t web_bad_carrier)" "code=bad_carrier" || log_error "$(t web_bad_carrier)"
                   return 1 ;;
            esac
            if web_set_carrier "$carrier" >/dev/null 2>&1; then
                local _wcm; _wcm="$(tf web_carrier_set "$carrier")"
                [ "$carrier" = "https" ] || _wcm="${_wcm}. $(t web_carrier_ios)"
                [ "$json_out" = "1" ] && bot_emit_json "ok" "$_wcm" "carrier=$carrier" || log_success "$_wcm"
                return 0
            fi
            [ "$json_out" = "1" ] && bot_emit_json "error" "$(t web_apply_failed)" "code=apply_failed" || log_error "$(t web_apply_failed)"
            return 1 ;;
        web-host)
            # Отдельное имя WEB: DNS → сертификат → разводка 443 (если WEB включён).
            # Причину отказа web_set_host пишет в журнал; наружу — её текст.
            local _whout _whrc _whn _whd _whcode=web_host _whmiss="" _whwarn=""
            _whn=$(printf '%s' "$host" | tr 'A-Z' 'a-z' | tr -d '[:space:]')
            _whd=$(config_get domain 2>/dev/null || echo "")
            _whout=$(web_set_host "$host" 2>&1); _whrc=$?
            if [ "$_whrc" = 0 ]; then
                # Какие адреса сервера имя не покрывает (пусто — все): не отказ,
                # адрес мог быть оставлен запасным для SSH
                if type edgeips_dns_missing >/dev/null 2>&1; then
                    _whmiss=$(edgeips_dns_missing "$_whn" | paste -sd ' ' -)
                    _whwarn=$(edgeips_dns_warn_text "$_whn" "$_whmiss")
                fi
                if [ "$json_out" = "1" ]; then
                    bot_emit_json "ok" "$(tf web_host_set "$_whn")" "web_host=$_whn" "dns_missing=$_whmiss"
                else
                    log_success "$(tf web_host_set "$_whn")"
                    [ -n "$_whwarn" ] && log_warning "$_whwarn"
                fi
                return 0
            fi
            # Отказ «не поддомен» — отдельным кодом: экраны подсказывают www.<домен>
            if [ -n "$_whn" ] && [ -n "$_whd" ] && [ "$_whn" != "$_whd" ] && validate_domain "$_whn" \
               && ! web_host_is_sub "$_whn" "$_whd"; then
                _whcode=web_host_not_sub
            fi
            # Значок log_* снимаем как есть: класс [^A-Za-zА-Яа-яЁё] при локали C
            # (у служб бота и панели LANG может не быть) резал «✗» по байтам, и в
            # тексте оставался мусор
            _whout=$(printf '%s' "$_whout" | sed -E 's/\x1b\[[0-9;]*m//g; s/^[[:space:]]*(✗|⚠|ℹ|✓)?[[:space:]]*//' | grep -v '^$' | tail -1)
            [ "$json_out" = "1" ] && bot_emit_json "error" "${_whout:-$(t web_apply_failed)}" "code=$_whcode" || log_error "${_whout:-$(t web_apply_failed)}"
            return 1 ;;
        tls-protocols)
            # Версии TLS сайта и WEB. Без --value — текущее (читает, без замка и
            # миграции); --value=1.2|1.2+1.3 — применить. Иное — отказ до любых
            # действий: значение приходит от бота и панели.
            local _tw _trc _tout _tcode _tmsg _tcur _tblk
            if [ "$has_value" = 0 ]; then
                _tcur=$(nginx_tls_current 2>/dev/null)
                _tblk=$(nginx_tls_blocker 2>/dev/null)
                case "$_tblk" in
                    mode)    _tmsg=$(t tls_unused_mode) ;;
                    no_site) _tmsg=$(t tls_unused_no_site) ;;
                    *)       _tmsg="$(t tls_title): $(tls_protocols_label)" ;;
                esac
                if [ "$json_out" = "1" ]; then
                    bot_emit_json "ok" "$_tmsg" "value=$(nginx_tls_cli_name "$_tcur")" \
                        "tls_protocols=$_tcur" "applicable=$([ -z "$_tblk" ] && echo true || echo false)" \
                        "blocker=$_tblk"
                else
                    echo "$_tmsg"
                    [ -z "$_tblk" ] && echo "--value=$(nginx_tls_cli_name "$_tcur") (ssl_protocols $_tcur)"
                fi
                return 0
            fi
            if ! _tw=$(nginx_tls_cli_value "$value"); then
                [ "$json_out" = "1" ] && bot_emit_json "error" "$(t tls_bad_value)" "code=bad_value" || log_error "$(t tls_bad_value)"
                return 1
            fi
            nginx_tls_set "$_tw"; _trc=$?
            _tout=$(tls_protocols_outcome "$_trc" "$_tw")
            _tcode=${_tout%%$'\t'*}; _tmsg=${_tout#*$'\t'}
            case "$_trc" in
                0|9)
                    if [ "$json_out" = "1" ]; then
                        bot_emit_json "ok" "$_tmsg" "value=$value" "tls_protocols=$_tw" \
                            "changed=$([ "$_trc" = 0 ] && echo true || echo false)"
                    else
                        log_success "$_tmsg"
                    fi
                    return 0 ;;
            esac
            [ "$json_out" = "1" ] && bot_emit_json "error" "$_tmsg" "code=$_tcode" || log_error "$_tmsg"
            return 1 ;;
        web-off)
            if web_disable >/dev/null 2>&1; then
                [ "$json_out" = "1" ] && bot_emit_json "ok" "$(t web_disabled)" || log_success "$(t web_disabled)"
                return 0
            fi
            [ "$json_out" = "1" ] && bot_emit_json "error" "$(t web_apply_failed)" "code=apply_failed" || log_error "$(t web_apply_failed)"
            return 1 ;;
        miniapp-status)
            # Читающее действие: попадает под *-status и миграцию не тянет.
            local _ma_url _ma_block
            _ma_url=$(miniapp_url 2>/dev/null || echo "")
            _ma_block=$(miniapp_blocker 2>/dev/null || echo "")
            if [ "$json_out" = "1" ]; then
                bot_emit_json "ok" "" \
                    "state=$([ -n "$_ma_url" ] && echo on || echo off)" \
                    "url=$_ma_url" \
                    "blocker=$_ma_block" \
                    "tfa=$(admin_web_tfa_ready && echo on || echo off)"
            else
                [ -n "$_ma_url" ] && echo "state=on" || echo "state=off"
                [ -n "$_ma_url" ] && echo "url=$_ma_url"
                [ -n "$_ma_block" ] && echo "blocker=$_ma_block"
            fi
            return 0 ;;
        miniapp-on|miniapp-rotate)
            # rotate = выдать НОВЫЙ путь: старый адрес перестаёт работать сразу.
            # Нужен, когда ссылку могли подсмотреть; включение с уже занятым
            # путём его сохраняет, поэтому для смены путь сначала обнуляем.
            local _ma_block2
            _ma_block2=$(miniapp_blocker 2>/dev/null || echo "")
            if [ -n "$_ma_block2" ]; then
                [ "$json_out" = "1" ] \
                    && bot_emit_json "error" "$(t "ma_$_ma_block2")" "code=$_ma_block2" \
                    || log_error "$(t "ma_$_ma_block2")"
                return 1
            fi
            [ "$action" = "miniapp-rotate" ] && bot_update_config_field miniapp_path "" >/dev/null 2>&1
            if GT_MINIAPP_NO_BOT_RESTART=1 miniapp_enable >/dev/null 2>&1; then
                local _u; _u=$(miniapp_url 2>/dev/null || echo "")
                [ "$json_out" = "1" ] \
                    && bot_emit_json "ok" "$(t ma_enabled)" "url=$_u" \
                    || echo "$_u"
                return 0
            fi
            [ "$json_out" = "1" ] \
                && bot_emit_json "error" "$(t ma_nginx_bad)" "code=nginx_bad" \
                || log_error "$(t ma_nginx_bad)"
            return 1 ;;
        miniapp-off)
            GT_MINIAPP_NO_BOT_RESTART=1 miniapp_disable >/dev/null 2>&1
            [ "$json_out" = "1" ] && bot_emit_json "ok" "$(t ma_disabled)"
            return 0 ;;
        warp-status)
            if [ "$json_out" = "1" ]; then warp_egress_status_json; else warp_egress_status; fi
            return 0 ;;
        warp-probe)
            warp_egress_probe_json
            return 0 ;;
        warp-coverage)
            # Что переживёт включение WARP — по фактической карте ДЦ этого сервера.
            if [ "$json_out" = "1" ]; then warp_egress_coverage_json; else warp_egress_coverage_text; fi
            return 0 ;;
        logs-status)
            # Бюджет логов по компонентам — чтобы «раздулись логи» было видно
            # из бота и панели, а не только по SSH, когда уже поздно.
            if ! type logb_status >/dev/null 2>&1; then
                [ "$json_out" = "1" ] && bot_emit_json "error" "модуль бюджета логов не загружен" "code=no_logbudget"
                return 1
            fi
            if [ "$json_out" = "1" ]; then
                local _rows="" _n _mb _bud _pct _free=""
                while read -r _n _mb _bud _pct; do
                    if [ "$_n" = "free" ]; then _free="$_mb"; continue; fi
                    _rows="${_rows}${_rows:+,}{\"component\":\"$_n\",\"used_mb\":$_mb,\"budget_mb\":$_bud,\"pct\":$_pct}"
                done <<< "$(logb_status)"
                printf '{"status":"ok","free_mb":%s,"components":[%s]}\n' "${_free:-0}" "$_rows"
            else
                logb_status | while read -r _n _mb _bud _pct; do
                    [ "$_n" = "free" ] && { echo "свободно на /var/log: ${_mb} МБ"; continue; }
                    printf '%-12s %6s МБ из %s МБ (%s%%)\n' "$_n" "$_mb" "$_bud" "$_pct"
                done
            fi
            return 0 ;;
        logs-enforce)
            # Подрезать прямо сейчас, не дожидаясь таймера.
            if ! type logb_enforce >/dev/null 2>&1; then
                [ "$json_out" = "1" ] && bot_emit_json "error" "модуль бюджета логов не загружен" "code=no_logbudget"
                return 1
            fi
            logb_enforce
            [ "$json_out" = "1" ] && bot_emit_json "ok" "бюджет логов применён: $(cat /run/gotelegram-logbudget.state 2>/dev/null | cut -d' ' -f2-)"
            return 0 ;;
        "")
            [ "$json_out" = "1" ] && bot_emit_json "error" "не указано действие" "code=no_action"
            log_error "no --action specified"
            return 64
            ;;
        *)
            [ "$json_out" = "1" ] && bot_emit_json "error" "unknown action: $action" "code=unknown_action"
            log_error "unknown action: $action"
            return 64
            ;;
    esac
}

# ── Точка входа / Entry point ───────────────────────────────────────────────
main() {
    # Non-interactive action mode: if --action=X is in args, dispatch and exit.
    # Must run BEFORE interactive banner/menus so the bot gets clean JSON.
    local a has_action=0 action_name=""
    for a in "$@"; do
        case "$a" in --action=*) has_action=1; action_name="${a#--action=}"; break ;; esac
    done
    if [ "$has_action" = "1" ]; then
        check_root
        init_dirs
        # Для bot-экшенов тоже нужны зависимости (git для change-template), но
        # без шумного apt-get update если всё уже на месте.
        if ! check_deps_present; then
            ensure_deps >&2 || exit 1
        fi
        # Read-only действия вебка/бот дёргают по таймеру и убивают по таймауту
        # 30с — оборванная посреди записи конфига миграция рвёт конфиг.
        case "$action_name" in
            # Читающие действия НЕ должны тянуть миграцию: бот и панель дёргают
            # их на каждом обновлении экрана, а миграция пишет снапшот и может
            # перезапустить telemt. quality опрашивается чаще всех.
            *-status|*-probe|quality|warp-coverage) : ;;
            *) _gt_tls_action_reads "$@" || auto_migrate_legacy_state >&2 || true ;;
        esac
        bot_action_dispatch "$@"
        exit $?
    fi

    # bootstrap (exec bash) / curl|bash: stdin может быть не tty -> перецепляем на
    # управляющий терминал, чтобы все read/confirm видели ввод (полный фикс #1/#3).
    if [ ! -t 0 ] && { true < /dev/tty; } 2>/dev/null; then exec < /dev/tty; fi

    check_root
    init_dirs

    # Первый запуск: если критические зависимости отсутствуют — ставим их ДО
    # того как пользователь дойдёт до меню. На последующих запусках это просто
    # дёшево проверяет command -v по всем командам и ничего не делает.
    if ! check_deps_present; then
        log_step "Первый запуск: проверяю зависимости..."
        ensure_deps || {
            log_error "Не удалось установить зависимости. См. сообщения выше."
            exit 1
        }
    fi

    auto_migrate_legacy_state || true
    # Обновление ядра telemt — ТОЛЬКО при накатывании обновления goTelegram (сменилась
    # версия проекта = MIGRATED_FROM выставлен), а не при каждом запуске `gotelegram`.
    if [ -n "${MIGRATED_FROM:-}" ]; then
        auto_update_telemt_if_possible || true
    fi
    auto_update_bot_if_possible || true
    auto_install_admin_web_if_possible || true

    # First-run language picker (before banner so banner appears in chosen lang)
    first_run_language_picker

    show_banner

    # Pre-flight
    check_os
    check_disk_space 500

    # Promo once per day
    if should_show_promo; then
        show_promo_with_qr
    fi

    # Ченджлог при обновлении версии (миграция выставила MIGRATED_FROM)
    if [ -n "${MIGRATED_FROM:-}" ] && type show_upgrade_changelog >/dev/null 2>&1; then
        show_upgrade_changelog "$MIGRATED_FROM"
        # WEB включён по-старому — предложить отдельное имя (вне замка миграции)
        web_upgrade_offer_host || true
    fi

    # Первый запуск (прокси ещё не установлен) → сразу установка (дисклеймер-gate внутри)
    if ! is_telemt_installed 2>/dev/null || [ ! -f "$GOTELEGRAM_CONFIG" ]; then
        menu_install || true
    fi

    local choice="" rc=0
    while true; do
        clear
        show_main_menu
        # Auto-refresh: 30 sec timeout.
        # ВАЖНО: различаем таймаут (rc > 128) и EOF (rc = 1). Раньше здесь было
        # `if read -t 30 -r choice`, и на мёртвой SSH-сессии stdin отдавал EOF
        # мгновенно → busy-loop на 100% CPU (каждая итерация форкала
        # curl/systemctl/openssl в show_main_menu).
        read -t 30 -r choice; rc=$?
        if [ $rc -gt 128 ]; then
            continue                                  # таймаут → перерисовка дашборда
        elif [ $rc -ne 0 ]; then
            echo ""; log_info "$(t bye)"; exit 0      # EOF → сессия мертва, выходим
        fi

        case "$choice" in
            1) submenu_proxy ;;
            2) submenu_stats ;;
            3) submenu_manage ;;
            4) menu_bot ;;
            5) submenu_about ;;
            0|q|exit) echo ""; log_info "$(t bye)"; exit 0 ;;
            *)  log_error "$(t invalid_choice)" ;;
        esac

        # Pause after submenu (except stats — it has its own loop)
        if [ "$choice" != "2" ]; then
            echo ""
            echo -ne "  ${DIM}$(t press_enter_to_return)${NC}"
            read -r || true
        fi
    done
}

# ── Статистика (авто-обновление 1 сек, без мерцания) ───────────────────────
submenu_stats() {
    # Инициализируем статистику при первом входе
    if type stats_init &>/dev/null; then
        stats_init 2>/dev/null
    fi

    local line2; line2=$(printf '─%.0s' {1..54})
    local first_draw=1 rc=0

    # Скрываем курсор для плавного обновления
    tput civis 2>/dev/null

    # Восстанавливаем курсор при выходе из функции
    trap 'tput cnorm 2>/dev/null; trap - RETURN' RETURN

    while true; do
        if [ "$first_draw" -eq 1 ]; then
            clear
            first_draw=0
        else
            # Перемещаем курсор в начало экрана вместо clear — нет мерцания
            tput cup 0 0 2>/dev/null || printf '\033[H'
        fi

        # Draw the whole screen over the previous content
        echo -e "\033[J"  # erase from cursor to end (removes trails)
        echo -e "  ${BOLD}${WHITE}$(t stats_title)${NC}"
        echo -e "  ${DIM}${line2}${NC}"

        if type show_traffic_stats &>/dev/null; then
            show_traffic_stats
        else
            echo -e "  ${DIM}$(t stats_module_missing)${NC}"
            echo -e "  ${DIM}$(t stats_file_missing)${NC}"
            echo ""
        fi

        echo -e "  ${DIM}${line2}${NC}"
        local stats_on
        stats_on=$(t stats_on)
        if type toggle_stats &>/dev/null; then
            local cfg_val
            # jq '.k // empty' в config_get отдаёт пусто для false — читаем сами,
            # иначе выключенная статистика показывалась как включённая.
            cfg_val=$(jq -r 'if has("stats_enabled") then (.stats_enabled|tostring) else "true" end' "$GOTELEGRAM_CONFIG" 2>/dev/null) || cfg_val="true"
            case "$cfg_val" in ""|null) cfg_val="true" ;; esac
            [ "$cfg_val" = "false" ] && stats_on=$(t stats_off)
        fi
        echo -e "  ${CYAN}1${NC}) $(tf stats_toggle "$stats_on")"
        echo -e "  ${CYAN}2${NC}) $(t stats_install_collector)"
        echo -e "  ${CYAN}0${NC}) ${DIM}$(t back)${NC}"
        echo -e "  ${DIM}${line2}${NC}"
        echo -e "  ${DIM}$(t stats_auto_refresh)${NC}"

        # Show cursor for input, then hide again
        tput cnorm 2>/dev/null
        echo -ne "  ${WHITE}▸ ${NC}"

        # Таймаут (rc > 128) → авто-обновление; EOF (rc = 1) → выход из подменю.
        # Без различия EOF крутил экран статистики в busy-loop.
        read -t 3 -r ch; rc=$?
        if [ $rc -gt 128 ]; then
            tput civis 2>/dev/null
            continue
        elif [ $rc -ne 0 ]; then
            tput cnorm 2>/dev/null
            return 0
        fi
        tput civis 2>/dev/null
        case "$ch" in
            1)
                if type toggle_stats &>/dev/null; then
                    toggle_stats
                    echo -ne "  ${DIM}$(t press_enter)${NC}"; read -r || true
                    first_draw=1  # full redraw after action
                fi
                ;;
            2)
                if type install_stats_collector &>/dev/null; then
                    install_stats_collector
                    echo -ne "  ${DIM}$(t press_enter)${NC}"; read -r || true
                    first_draw=1
                fi
                ;;
            0|"") return 0 ;;
        esac
        tput civis 2>/dev/null
    done
}

# --selftest: автопроверка функций без сервера и без root (см. lib/selftest.sh)
if [ "${1:-}" = "--selftest" ]; then
    if type selftest_run >/dev/null 2>&1; then selftest_run; exit $?; fi
    echo "lib/selftest.sh не установлен"; exit 2
fi

main "$@"
