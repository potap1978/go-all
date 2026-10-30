#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# ╔═══════════════════════════════════════════════════════════════════╗
# ║  goDNS — DoH/DoT резолвер на сертификате goVLESS / goTelegram     ║
# ║                                                                   ║
# ║  Клиентам — только шифрованный DNS (DoH на своём пути, DoT).      ║
# ║  Наверх — только шифрованный DNS (DoT/DoH к публичным резолверам).║
# ║  Открытого :53 в этой схеме нет нигде.                            ║
# ╚═══════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── bash 4.0+ ───────────────────────────────────────────────────────────
# Таблица строк — ассоциативный массив. На bash 3.2 I18N[ключ] молча пишет
# в индекс 0, и весь интерфейс превращается в кашу. Не убирать.
if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "goDNS Pro требует bash 4.0+ (найден ${BASH_VERSION:-неизвестно})" >&2
    echo "goDNS Pro requires bash 4.0+ (found ${BASH_VERSION:-unknown})" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/i18n.sh"
source "${SCRIPT_DIR}/lib/domain.sh"
source "${SCRIPT_DIR}/lib/certs.sh"
source "${SCRIPT_DIR}/lib/certs_issue.sh"
source "${SCRIPT_DIR}/lib/providers.sh"
source "${SCRIPT_DIR}/lib/bench.sh"
source "${SCRIPT_DIR}/lib/engine.sh"
source "${SCRIPT_DIR}/lib/logs.sh"
source "${SCRIPT_DIR}/lib/stats.sh"
source "${SCRIPT_DIR}/lib/qr.sh"
source "${SCRIPT_DIR}/lib/selftest.sh"
source "${SCRIPT_DIR}/lib/backup.sh"
source "${SCRIPT_DIR}/lib/update.sh"
source "${SCRIPT_DIR}/lib/bot.sh"
source "${SCRIPT_DIR}/lib/webui.sh"

trap cleanup_temp_files EXIT

# ═══════════════════════════════════════════════════════════════════════
# ЗАПУСК: root → ОС → язык
# ═══════════════════════════════════════════════════════════════════════
startup_checks() {
    require_root || exit 1

    # Проверка ОС ДО того, как что-либо поставлено. Это требование заказчика:
    # «проект должен ставиться на убунту 22 или 24, не старше».
    local rc=0
    os_gate || rc=$?
    case "$rc" in
        0) log_dim "$(tf startup_os_ok "${OS_PRETTY:-Ubuntu $OS_VERSION_ID}")" ;;
        2)
            # Более новая LTS: жёсткий список версий превратил бы установщик
            # в бомбу замедленного действия, поэтому спрашиваем, а не падаем.
            log_warning "$(tf startup_os_newer "${OS_PRETTY:-Ubuntu $OS_VERSION_ID}")"
            confirm "$(t startup_os_newer_ask)" n || {
                log_info "$(t startup_cancelled)"
                exit 0
            }
            ;;
        *)
            log_error "$(t startup_os_refused)"
            exit 1
            ;;
    esac
}

init_language() {
    local lang
    lang=$(detect_language) || lang="ru"
    if [ ! -f "${GODNS_DIR}/.language" ]; then
        lang=$(pick_language_interactive) || lang="ru"
    fi
    load_language "$lang" || true
    save_language "$lang" || true
}

# ═══════════════════════════════════════════════════════════════════════
# ПАНЕЛЬ СОСТОЯНИЯ
# ═══════════════════════════════════════════════════════════════════════
# Ширина подписи в колонке живёт в lib/common.sh (GODNS_LBL_W) вместе с
# pad_label: выравнивание одно на продукт, иначе каждый экран заводит своё.
# Выравниваем ВРУЧНУЮ, а не через printf '%-12s': bash считает в printf
# БАЙТЫ, а не символы, поэтому кириллическая подпись (2 байта на букву)
# разъезжается ровно на свою длину.
_dash_pad() { pad_label "$1" "${2:-${GODNS_LBL_W:-12}}"; }

_dash_row() {   # _dash_row <подпись С двоеточием> <значение с цветом>
    printf '  %s %b\n' "$(_dash_pad "$1")" "$2"
}

show_dashboard() {
    clear 2>/dev/null || true
    # Ширина и левый край — как у print_rule (55 знаков с отступа 2).
    # ГРАБЛИ: шапка рисовалась в 54 знака ОТ КРАЯ ЭКРАНА, а строки под ней —
    # с отступа 2. Левый край главного экрана получался рваным, и линейка
    # шапки не сходилась ни с одной другой линейкой в программе.
    local w=55
    echo ""
    echo -e "  ${MAGENTA}$(printf '*%.0s' $(seq 1 $w))${NC}"
    echo -e "  ${MAGENTA}potap1978  ·  ${BOLD}goDNS Pro${NC}${MAGENTA} v${GODNS_VERSION}${NC}"
    echo -e "  ${MAGENTA}$(t banner_subtitle)${NC}"
    echo -e "  ${MAGENTA}$(printf '*%.0s' $(seq 1 $w))${NC}"

    local running="no" state_txt domain doh_port dot_port policy ups days secret
    engine_is_running && running="yes"
    if [ "$running" = "yes" ]; then
        state_txt="${GREEN}● $(t state_running)${NC}"
    elif engine_is_installed; then
        state_txt="${YELLOW}○ $(t state_stopped)${NC}"
    else
        state_txt="${DIM}○ $(t state_not_installed)${NC}"
    fi
    _dash_row "$(t dash_service):" "$state_txt"
    # Молчать о расхождении нельзя: панель — то место, откуда копируют
    # адрес, и она обязана признаться, что показывает намерение, а не факт.
    if godns_config_drifted; then
        _dash_row "" "${YELLOW}⚠ $(t dash_drift)${NC}"
    fi

    domain=$(config_get domain "") || domain=""
    if [ -n "$domain" ]; then
        # Две РАЗНЫЕ даты, и раньше они стояли одной строкой: рядом с
        # именем домена печаталось «осталось дней: 89», а считало это
        # сертификат. Читалось как срок домена. Сроки не связаны между
        # собой и чинятся по-разному: сертификат — перевыпуском за минуту,
        # регистрация — у регистратора и деньгами. Поэтому две строки,
        # и в каждой ДАТА, а не только число дней.
        local reg_st="" reg_iso="" reg_txt="" reg_days=""
        IFS=$'\t' read -r reg_st reg_iso <<<"$(domain_expiry_cached "$domain")"
        case "$reg_st" in
            ok)
                reg_days=$(domain_days_left "$reg_iso") || reg_days=""
                if [ -n "$reg_days" ] && [ "$reg_days" -le 0 ] 2>/dev/null; then
                    reg_txt="  ${RED}${BOLD}$(tf dash_reg_gone "$(domain_date_human "$reg_iso")")${NC}"
                elif [ -n "$reg_days" ] && [ "$reg_days" -le 30 ] 2>/dev/null; then
                    reg_txt="  ${YELLOW}$(tf dash_reg_soon "$(domain_date_human "$reg_iso")" "$reg_days")${NC}"
                else
                    reg_txt="  ${DIM}$(tf dash_reg_paid "$(domain_date_human "$reg_iso")")${NC}"
                fi
                ;;
            # pending — ответ WHOIS ещё в пути, молчим: через десять
            # секунд он появится сам, а мигать «неизвестно» ни к чему.
            pending) reg_txt="" ;;
            *)       reg_txt="  ${DIM}$(t dash_reg_unknown)${NC}" ;;
        esac
        _dash_row "$(t dash_domain):" "${WHITE}${domain}${NC}${reg_txt}"

        days=$(certs_days_left "$(config_get cert_path "")" 2>/dev/null) || days="?"
        local cert_iso="" cert_when="" days_txt=""
        cert_iso=$(certs_expiry_iso "$(config_get cert_path "")" 2>/dev/null) || cert_iso=""
        cert_when=$(domain_date_human "$cert_iso")
        # ГРАБЛИ: просроченный сертификат показывался как «осталось дней: -4»
        # тусклым серым — самым незаметным элементом экрана. В этот момент
        # НИ ОДИН клиент уже не подключается, и сказать об этом надо громко.
        case "$days" in
            ''|\?)      days_txt="${DIM}$(tf dash_cert_days "?")${NC}" ;;
            -*|0)        days_txt="${RED}${BOLD}$(tf dash_cert_gone "${cert_when:-?}")${NC}" ;;
            *)
                if [ "$days" -le 14 ] 2>/dev/null; then
                    days_txt="${YELLOW}$(tf dash_cert_soon2 "${cert_when:-?}" "$days")${NC}"
                else
                    days_txt="${DIM}$(tf dash_cert_until "${cert_when:-?}" "$days")${NC}"
                fi
                ;;
        esac
        _dash_row "$(t dash_cert):" "$days_txt"
    else
        _dash_row "$(t dash_domain):" "${DIM}$(t dash_none)${NC}"
    fi

    doh_port=$(config_get doh_port "") || doh_port=""
    dot_port=$(config_get dot_port "") || dot_port=""
    secret=$(config_get doh_path "") || secret=""
    if [ -n "$doh_port" ] && [ -n "$domain" ]; then
        _dash_row "$(t dash_doh):" "${WHITE}$(godns_doh_url)${NC}"
        # ГРАБЛИ: эта проверка тут уже была и ПРОПАЛА при переписывании
        # блока под строки статистики. Оператор, выключивший DoT, снова
        # видел живой адрес несуществующей службы — и мог его кому-то
        # выдать. Регресс закрыт проверкой в test_structure.sh: ни один
        # экран не имеет права печатать адрес DoT без этого условия.
        if [ "$(config_get dot_enabled no)" = "yes" ]; then
            _dash_row "$(t dash_dot):" "${WHITE}${domain}:${dot_port:-853}${NC}"
        else
            _dash_row "$(t dash_dot):" "${RED}$(t dash_dot_off)${NC}"
        fi
    elif [ -n "$doh_port" ]; then
        _dash_row "$(t dash_doh):" "${DIM}$(t dash_need_domain)${NC}"
    fi

    policy=$(config_get policy "") || policy=""
    [ -n "$policy" ] && _dash_row "$(t dash_policy):" "${WHITE}$(t "policy_${policy}")${NC}"

    ups=$(config_get upstream_ids "") || ups=""
    if [ -n "$ups" ]; then
        local n; n=$(printf '%s' "$ups" | tr ',' '\n' | grep -c . ) || n=0
        # Полный список из 10 id — это 130+ колонок: в терминале 80 строка
        # переносится и ломает панель. Режем по последней ЦЕЛОЙ запятой,
        # честно показывая «,…» — полный список живёт в меню апстримов.
        # id апстримов — ASCII, поэтому ${#…} здесь считает то, что видно.
        local ups_show="$ups"
        if [ "${#ups_show}" -gt 58 ]; then
            ups_show="${ups_show:0:58}"
            ups_show="${ups_show%,*},…"
        fi
        _dash_row "$(t dash_upstreams):" "${WHITE}${n}${NC} ${DIM}(${ups_show})${NC}"
    fi
    print_rule
}

# ── Выход из раздела ────────────────────────────────────────────────────
# «0) Назад» стоил двух нажатий: подменю возвращалось, а main_menu всё
# равно печатал «Enter — назад в меню» на экране, где читать уже нечего.
# Флаг ставит подменю, снимает и проверяет главный цикл. Через код возврата
# это не сделать: действия меню идут через run_action, а он гасит код.
GODNS_SUBMENU_BACK="${GODNS_SUBMENU_BACK:-no}"
_back() { GODNS_SUBMENU_BACK="yes"; return 0; }

# ── Заголовок шага мастера ──────────────────────────────────────────────
# Один и тот же экран открывается двумя дорогами: из мастера (тогда «Шаг 4
# из 5» — правда и помогает) и из «Настроек DNS» отдельным пунктом (тогда
# это ложь: человек зашёл сменить одну настройку и видит себя внутри
# пятишагового мастера, ожидая продолжения). Заголовок выбирается по тому,
# как сюда пришли.
GODNS_IN_WIZARD="${GODNS_IN_WIZARD:-no}"
_wiz_header() {
    if [ "$GODNS_IN_WIZARD" = "yes" ]; then
        print_header "$(t "$1")"
    else
        print_header "$(t "$2")"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# ШАГ 1 — СЕРТИФИКАТ
# ═══════════════════════════════════════════════════════════════════════
wizard_cert() {
    _wiz_header wiz_cert_title wiz_cert_title_solo
    # certs_discover сам объявляет и поиск, и «не найдено» — дублировать эти
    # строки здесь значило показывать оператору одно и то же дважды подряд.
    certs_discover || true

    # Сертификата нет вообще — это штатный случай: goVLESS в Lite-режиме его
    # не выпускает, а goTelegram в Lite не ставит ни nginx, ни certbot.
    if [ "${#CERT_CANDIDATES[@]}" -eq 0 ]; then
        log_dim "$(t certs_none_hint)"
        echo ""
        if confirm "$(t certs_offer_issue)" y; then
            certs_issue_interactive && return 0
            return 1
        fi
        return 1
    fi

    certs_render_menu
    local total="${#CERT_CANDIDATES[@]}"
    local issue_num=$((total + 1))
    echo -e "  ${CYAN}${issue_num})${NC} $(t certs_issue_new)"
    echo -e "  ${CYAN}0)${NC} $(t back)"

    local choice="" idx rec
    while true; do
        echo ""
        echo -ne "  $(t certs_pick) " >&2
        read -r choice || { echo ""; return 1; }
        [ "$choice" = "0" ] && return 1
        case "$choice" in ''|*[!0-9]*) log_warning "$(t invalid_choice)"; continue ;; esac
        if [ "$choice" -eq "$issue_num" ]; then
            certs_issue_interactive && return 0
            return 1
        fi
        idx=$((choice - 1))
        if [ "$idx" -lt 0 ] || [ "$idx" -ge "$total" ]; then
            log_warning "$(t invalid_choice)"; continue
        fi
        rec="${CERT_CANDIDATES[$idx]}"
        local usable; usable=$(printf '%s' "$rec" | cut -f7)
        if [ "$usable" != "yes" ]; then
            log_warning "$(t certs_unusable_pick)"
            log_dim "$(printf '%s' "$rec" | cut -f8)"
            confirm "$(t certs_unusable_force)" n || continue
        fi
        break
    done

    local domain cert key src
    domain=$(printf '%s' "$rec" | cut -f1)
    cert=$(printf '%s' "$rec" | cut -f2)
    key=$(printf '%s' "$rec" | cut -f3)
    src=$(printf '%s' "$rec" | cut -f4)

    # ГРАБЛИ: метка источника — это КЛЮЧ строки, а не текст. В сообщение
    # уходил сырой `certs_src_letsencrypt|iwanttowatchyoutube.site`, и
    # оператор читал внутренний идентификатор вместо фразы — ровно тот
    # класс, ради которого написан check-str-usage.
    local src_txt=""
    src_txt=$(_certs_render_label "$src") || src_txt="$src"

    # ГРАБЛИ вторые: признак «сертификат выпущен нами» сбрасывался ВСЕГДА,
    # даже когда оператор пересканировал и выбрал ТОТ ЖЕ наш сертификат.
    # А от него зависит снятие: `_uninstall_cron` убирает строку
    # «certbot renew … # goDNS» только у своей линии. Потеряв признак,
    # удаление оставляло чужому виду свою строку в crontab навсегда.
    # Сбрасываем, только если выбрали ДРУГОЙ домен.
    local prev_domain prev_owned
    prev_domain=$(config_get domain "") || prev_domain=""
    prev_owned=$(config_get cert_owned_by_godns "no") || prev_owned="no"

    config_set domain "$domain"
    config_set cert_path "$cert"
    config_set key_path "$key"
    config_set cert_source "$src"
    if [ "$prev_owned" = "yes" ] && [ "$prev_domain" = "$domain" ]; then
        : # тот же наш сертификат — владение сохраняем
    else
        config_set cert_owned_by_godns "no"
    fi
    log_success "$(tf certs_chosen "$domain" "$src_txt")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ШАГ 2 — АПСТРИМЫ (с реальным замером задержки)
# ═══════════════════════════════════════════════════════════════════════
wizard_upstreams() {
    _wiz_header wiz_ups_title wiz_ups_title_solo

    local region preselect
    region=$(prov_geo_hint) || region="eu"
    preselect=$(prov_default_set "$region") || preselect=""
    log_dim "$(tf prov_region_guess "$region")"

    # Замер запускаем СРАЗУ, не спрашивая: без цифр список бесполезен, а
    # вопрос «мерить ли» оператор всё равно всегда отвечает «да».
    # Это не ping: ICMP идёт другим путём, другим портом, и многие
    # endpoint'ы его режут. Меряем настоящий DoT — рукопожатие плюс запрос
    # на живом соединении.
    log_info "$(t prov_measuring)"
    bench_note
    local results=""
    results=$(bench_run_all) || results=""
    [ -z "$results" ] && log_warning "$(t prov_measure_failed)"
    # Сводка перед развёртыванием покажет те же цифры, не гоняя замер заново.
    GODNS_BENCH_RESULTS="$results"

    declare -A SEL=()
    local id saved=""
    saved=$(config_get upstream_ids "") || saved=""
    [ -n "$saved" ] && preselect="${saved//,/ }"
    for id in $preselect; do SEL["$id"]=1; done

    local ids=() split=0 rc=0
    while true; do
        ids=()
        while IFS= read -r id; do [ -n "$id" ] && ids+=("$id"); done < <(prov_menu_ids)
        split=$(prov_menu_split) || split=0
        # Третья группа — свои апстримы. Заголовок печатается на позиции, с
        # которой они начинаются; нет своих — позиция равна длине списка, и
        # заголовок не печатается вовсе.
        PROV_SPLIT_CUSTOM=$(prov_menu_split_custom) || PROV_SPLIT_CUSTOM=999999

        rc=0
        _ups_select ids SEL "$split" "$results" || rc=$?
        case "$rc" in
            0) break ;;
            2)  # «+»: добавить свой здесь же — и в список, уже отмеченным.
                # Без «включить в набор?»: набор выбирается прямо сейчас.
                CUSTOM_UPSTREAM_ADDED_ID=""
                custom_upstream_add --no-enable || true
                if [ -n "$CUSTOM_UPSTREAM_ADDED_ID" ]; then
                    SEL["$CUSTOM_UPSTREAM_ADDED_ID"]=1
                fi
                continue ;;
            *) return 1 ;;
        esac
    done

    local chosen=""
    for id in "${ids[@]}"; do
        [ -n "${SEL[$id]:-}" ] && chosen="${chosen}${chosen:+,}${id}"
    done
    if [ -z "$chosen" ]; then
        log_error "$(t prov_none_selected)"
        return 1
    fi


    # ГРАБЛИ: движок ждёт КАНОНИЧЕСКУЮ запись `id|proto|host|port|sni|path`,
    # а не список идентификаторов. Раньше сюда клался «cloudflare,google,…»,
    # dnsdist_upstreams_parse не находил протокола и отбрасывал ВСЁ, после
    # чего служба поднималась без единого апстрима и не слушала ни один порт.
    # Расхождение форматов не ловится ни проверкой графа вызовов, ни bash -n —
    # только живым запуском.
    # Фильтрующий апстрим молча отдаёт NXDOMAIN на домены клиентов туннеля,
    # и со стороны это выглядит как сломанный туннель, а не как фильтр.
    # Сказать об этом надо ДО записи, пока выбор ещё можно передумать.
    for id in ${chosen//,/ }; do prov_warn_if_filtered "$id" || true; done

    local skipped=""
    skipped=$(engine_upstreams_store "$chosen") || {
        log_error "$(t prov_none_usable)"
        return 1
    }
    [ -n "$skipped" ] && log_warning "$(tf prov_skipped "$skipped")"
    log_success "$(tf prov_selected "$chosen")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СВОЙ АПСТРИМ: ДОБАВИТЬ / УДАЛИТЬ
# ═══════════════════════════════════════════════════════════════════════
# Вписал адрес, выбрал протокол, добавил. Всё остальное — чтобы человек не
# добавил мёртвое: имя разрешается в адрес здесь же, кандидат ПРОБУЕТСЯ до
# записи, у открытого :53 честно названа цена. Проверка сертификата у
# DoT/DoH всегда включена. Вызывается через reconfigure: если апстрим
# попал в рабочий набор, конфигурация применяется; если нет — «ничего не
# изменилось», службу не трогаем.
CUSTOM_UPSTREAM_ADDED_ID=""
custom_upstream_add() {
    # --no-enable: из мастера, где набор выбирается прямо сейчас — вопрос
    # «включить в рабочий набор?» там лишний, id отдаём переменной.
    local no_enable="no"
    [ "${1:-}" = "--no-enable" ] && no_enable="yes"
    CUSTOM_UPSTREAM_ADDED_ID=""
    print_header "$(t ups_custom_add_title)"
    echo -e "  ${DIM}$(t ups_custom_intro)${NC}"
    echo ""
    echo -e "  ${CYAN}1)${NC} $(t ups_custom_proto_dot)"
    echo -e "  ${CYAN}2)${NC} $(t ups_custom_proto_doh)"
    echo -e "  ${CYAN}3)${NC} $(t ups_custom_proto_plain)"
    echo -ne "  $(t ups_custom_proto_ask) " >&2
    local proto=""
    while true; do
        godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 0; }
        case "$GODNS_REPLY" in
            1|dot|DoT)   proto="dot";   break ;;
            2|doh|DoH)   proto="doh";   break ;;
            3|plain|udp) proto="plain"; break ;;
            0|"")        log_info "$(t startup_cancelled)"; return 0 ;;
            *) log_warning "$(t invalid_choice)"; echo -ne "  $(t ups_custom_proto_ask) " >&2 ;;
        esac
    done

    # ── адрес ────────────────────────────────────────────────────────────
    local ask="" raw="" host="" port="" path="" sni="" ip=""
    case "$proto" in
        dot)   ask=$(t ups_custom_addr_dot) ;;
        doh)   ask=$(t ups_custom_addr_doh) ;;
        plain) ask=$(t ups_custom_addr_plain) ;;
    esac
    echo -ne "  ${ask}: " >&2
    godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 0; }
    raw=$(printf '%s' "$GODNS_REPLY" | tr -d '[:space:]')
    [ -n "$raw" ] || { log_info "$(t startup_cancelled)"; return 0; }
    raw="${raw#https://}"; raw="${raw#http://}"
    if [ "$proto" = "doh" ]; then
        case "$raw" in */*) path="/${raw#*/}"; raw="${raw%%/*}" ;; esac
    fi
    # [v6]:port · host:port · host
    case "$raw" in
        \[*\]:*) host="${raw#[}"; host="${host%%]*}"; port="${raw##*:}" ;;
        \[*\])   host="${raw#[}"; host="${host%]}" ;;
        *:*:*)   host="$raw" ;;                       # голый IPv6
        *:*)     host="${raw%%:*}"; port="${raw##*:}" ;;
        *)       host="$raw" ;;
    esac
    if [ -z "$host" ] || [[ "$host" =~ [^A-Za-z0-9.:_-] ]]; then
        log_error "$(t ups_custom_bad_url)"
        return 1
    fi

    # ── порт ─────────────────────────────────────────────────────────────
    local def_port=""
    case "$proto" in dot) def_port=853 ;; doh) def_port=443 ;; *) def_port=53 ;; esac
    if [ -z "$port" ]; then
        echo -ne "  $(t ups_custom_port_ask) ${DIM}[${def_port}]${NC} " >&2
        godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 0; }
        port=$(printf '%s' "$GODNS_REPLY" | tr -d '[:space:]')
        [ -n "$port" ] || port="$def_port"
    fi
    if ! valid_port "$port"; then
        log_error "$(tf prov_custom_bad_port "$port")"
        return 1
    fi

    # ── имя → адрес ──────────────────────────────────────────────────────
    if valid_ip "$host"; then
        ip="$host"
        [ "$proto" = "plain" ] || sni="$host"
    else
        if ! valid_domain "$host"; then
            log_error "$(t ups_custom_bad_url)"
            return 1
        fi
        log_info "$(tf ups_custom_resolving "$host")"
        local addrs=""
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

    # ── открытый :53 — цена названа до записи ───────────────────────────
    if [ "$proto" = "plain" ]; then
        if godns_ip_is_private "$ip"; then
            log_dim "$(t ups_custom_plain_private)"
        else
            log_warning "$(t ups_custom_plain_public)"
            if ! confirm "$(t ups_custom_plain_confirm)" n; then
                log_info "$(t startup_cancelled)"
                return 0
            fi
        fi
    fi

    # ── живая проба до записи ────────────────────────────────────────────
    local line="" url="" lat="" probe_ok="yes"
    case "$proto" in
        doh)
            url="https://${sni}"; [ "$port" = "443" ] || url="${url}:${port}"
            url="${url}${path:-/dns-query}"
            line="probe|doh|${sni}||${url}|${ip}" ;;
        dot)   line="probe|dot|${sni}|${port}||${ip}" ;;
        plain) line="probe|plain|${ip}|${port}||${ip}" ;;
    esac
    log_info "$(t ups_custom_probing)"
    lat=$(bench_probe_line "$line") || probe_ok="no"
    lat="${lat#"${lat%%[![:space:]]*}"}"
    if [ "$probe_ok" = "yes" ]; then
        log_success "$(tf ups_custom_probe_result "$lat")"
    else
        log_warning "$(tf ups_custom_probe_failed "${lat:-?}")"
        if ! confirm "$(t ups_custom_probe_failed_ask)" n; then
            log_info "$(t startup_cancelled)"
            return 0
        fi
    fi

    # ── имя и запись ─────────────────────────────────────────────────────
    local name="" id=""
    echo -ne "  $(t ups_custom_name_ask) ${DIM}[${sni:-$ip}]${NC} " >&2
    godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 0; }
    name="$GODNS_REPLY"
    [ -n "$name" ] || name="${sni:-$ip}"
    # Напрямую, не через `$( )`: в подоболочке таблица перечиталась бы
    # только там, а здесь осталась бы старой (см. prov_custom_add).
    prov_custom_add "$proto" "$ip" "$port" "$sni" "$path" "$name" >/dev/null || return 1
    id="$PROV_CUSTOM_LAST_ID"
    [ -n "$id" ] || return 1
    CUSTOM_UPSTREAM_ADDED_ID="$id"
    log_success "$(tf ups_custom_added "$(prov_name "$id")")"
    [ "$no_enable" = "yes" ] && return 0

    # ── в рабочий набор? ────────────────────────────────────────────────
    if confirm "$(t ups_custom_enable_ask)" y; then
        local cur="" skipped=""
        cur=$(config_get upstream_ids "") || cur=""
        skipped=$(engine_upstreams_store "${cur}${cur:+,}${id}") || {
            log_error "$(t prov_none_usable)"
            return 1
        }
        [ -n "$skipped" ] && log_warning "$(tf prov_skipped "$skipped")"
        return 0
    fi
    log_dim "$(t ups_custom_enable_hint)"
    return 0
}

custom_upstream_remove() {
    print_header "$(t ups_custom_remove_title)"
    local ids=() id="" n=0
    while IFS= read -r id; do [ -n "$id" ] && ids+=("$id"); done < <(prov_custom_list)
    if [ "${#ids[@]}" -eq 0 ]; then
        log_info "$(t ups_custom_none)"
        return 0
    fi
    for id in "${ids[@]}"; do
        n=$((n + 1))
        prov_render_row "$id" "$n" "no" ""
        echo -e "           ${DIM}$(prov_bootstrap_ips "$id" all):$(prov_field "$id" dot_port)${NC}"
    done
    echo -e "  ${CYAN}0)${NC} $(t back)"
    echo -ne "  $(t ups_custom_which) ▸ " >&2
    godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 0; }
    case "$GODNS_REPLY" in
        ''|0) log_info "$(t startup_cancelled)"; return 0 ;;
        *[!0-9]*) log_warning "$(t invalid_choice)"; return 0 ;;
    esac
    local i=$((GODNS_REPLY - 1))
    if [ "$i" -lt 0 ] || [ "$i" -ge "${#ids[@]}" ]; then
        log_warning "$(t invalid_choice)"; return 0
    fi
    id="${ids[$i]}"

    # Единственный апстрим в наборе снимать нельзя — резолвер останется
    # без единого сервера. Проверяем ДО записи, чтобы отказ ничего не
    # оставил полусделанным.
    local cur="" rest="" x=""
    cur=$(config_get upstream_ids "") || cur=""
    for x in ${cur//,/ }; do [ "$x" = "$id" ] || rest="${rest}${rest:+,}${x}"; done
    if [ -n "$cur" ] && [ -z "$rest" ]; then
        log_error "$(t ups_custom_last_one)"
        return 1
    fi
    if ! confirm "$(tf ups_custom_remove_ask "$(prov_name "$id")")" n; then
        log_info "$(t startup_cancelled)"
        return 0
    fi
    local shown=""
    shown=$(prov_name "$id") || shown="$id"
    prov_custom_remove "$id" || return 1
    if [ "$rest" != "$cur" ]; then
        engine_upstreams_store "$rest" >/dev/null || {
            log_error "$(t prov_none_usable)"
            return 1
        }
    fi
    log_success "$(tf ups_custom_removed "$shown")"
    return 0
}

# ── Список с галочками: стрелки + пробел ────────────────────────────────
# Оператор просил именно так: пробел ставит галочку, Enter продолжает.
#
# Клавиши читаем посимвольно через `read -rsn1`. Стрелка приходит тремя
# байтами (ESC [ A), поэтому после ESC дочитываем хвост с коротким
# таймаутом — без него одиночное нажатие Esc повисло бы в ожидании.
#
# Если стандартный ввод не терминал (запуск по SSH одной командой, из
# скрипта, из cron), посимвольный ввод невозможен — там остаётся ввод
# номеров. Молча ломаться в этом случае нельзя.
# ЗАМЕЧАНИЕ: `local -n` (nameref) требует bash 4.3+. Общий страж проекта
# требует 4.0+, но Ubuntu 22.04 несёт bash 5.1, а 24.04 — 5.2, так что на
# целевых системах это безопасно.
_ups_select() {
    local -n _ids="$1"
    local -n _sel="$2"
    local split="$3" results="$4"
    local cur=0 prev=0 total="${#_ids[@]}" key rest id i n

    if [ ! -t 0 ]; then
        # ГРАБЛИ: передавать «_ids» нельзя — это имя НАШЕЙ ссылки, и внутри
        # получится ссылка на саму себя (bash: circular name reference), а
        # массив приедет пустым. `${!_ids}` даёт имя ИСХОДНОЙ переменной.
        _ups_select_numeric "${!_ids}" "${!_sel}" "$split" "$results"
        return $?
    fi

    # ── Строки готовим ОДИН раз ─────────────────────────────────────────
    # prov_render_row внутри дёргает prov_name, prov_filter_text, _prov_pad —
    # это около шести подпроцессов на строку. На девятнадцати строках выходит
    # за сотню подпроцессов на КАЖДОЕ нажатие клавиши: список заметно
    # «перезагружается». Собираем оба варианта каждой строки заранее, дальше
    # перерисовка — чистый printf.
    local -a row_on=() row_off=() lat=()
    for ((i = 0; i < total; i++)); do
        id="${_ids[$i]}"
        lat[i]=""
        [ -n "$results" ] && { lat[i]=$(bench_fmt_latency "$results" "$id") || lat[i]=""; }
        row_on[i]=$(prov_render_row "$id" "" "yes" "${lat[$i]}")
        row_off[i]=$(prov_render_row "$id" "" "no"  "${lat[$i]}")
    done

    # ── Перерисовка: от начала экрана, без арифметики ───────────────────
    # ГРАБЛИ: здесь дважды была попытка считать номер строки каждого пункта
    # и прыгать к нему через \033[NA. Оба раза расчёт врал на единицу, и
    # перерисовка затирала соседнюю строку — с экрана пропадал заголовок
    # группы, а пункты задваивались. Арифметика по строкам хрупка: любая
    # правка раскладки (лишний echo, перенос длинной строки) её ломает, и
    # ломает молча.
    #
    # Надёжнее не считать вовсе: ставим курсор в начало экрана (\033[H) и
    # печатаем блок заново. Строки уже собраны заранее, поэтому это чистый
    # printf без единого подпроцесса — мерцания нет, потому что нет clear:
    # экран не гасится, а переписывается поверх. Каждая строка гасит свой
    # хвост через \033[K, иначе от длинной строки остался бы обрывок.
    local rows=""
    rows=$( { tput lines; } 2>/dev/null ) || rows=""
    case "$rows" in ''|*[!0-9]*) rows="${LINES:-24}" ;; esac
    local need=$((total + 10))
    local home="yes"
    [ "$rows" -lt "$need" ] && home="no"

    _ups_paint_all() {
        if [ "$home" = "yes" ]; then
            printf '\033[H'
        else
            clear 2>/dev/null || true
        fi
        printf '\033[K\n'
        printf '  %b%s%b\033[K\n' "${BOLD}${WHITE}" "$(t prov_menu_title)" "$NC"
        printf '  %b%s%b\033[K\n' "$DIM" "$(printf '─%.0s' $(seq 1 55))" "$NC"
        printf '  %b%s%b\033[K\n' "$DIM" "$(t prov_group_unfiltered)" "$NC"
        local k
        for ((k = 0; k < total; k++)); do
            if [ "$k" -eq "$split" ]; then
                printf '\033[K\n'
                printf '  %b%s%b\033[K\n' "$DIM" "$(t prov_group_filtered)" "$NC"
            fi
            if [ "$k" -eq "${PROV_SPLIT_CUSTOM:-999999}" ]; then
                printf '\033[K\n'
                printf '  %b%s%b\033[K\n' "$DIM" "$(t prov_group_custom)" "$NC"
            fi
            if [ "$k" -eq "$cur" ]; then printf '%b▸%b' "$CYAN" "$NC"; else printf ' '; fi
            if [ -n "${_sel[${_ids[$k]}]:-}" ]; then
                printf '%b\033[K\n' "${row_on[$k]}"
            else
                printf '%b\033[K\n' "${row_off[$k]}"
            fi
        done
        printf '\033[K\n'
        printf '  %b%s%b\033[K\n' "$DIM" "$(t prov_legend_warn)" "$NC"
        printf '  %b%s%b\033[K\n' "$DIM" "$(t prov_help_keys)" "$NC"
        local c=0 x
        for x in "${_ids[@]}"; do [ -n "${_sel[$x]:-}" ] && c=$((c + 1)); done
        printf '  %b%s%b\033[K' "$BOLD" "$(tf prov_chosen_count "$c")" "$NC"
        # Дочистить экран НИЖЕ списка. Мы рисуем от \033[H поверх прежнего
        # содержимого, и без этого под списком оставались хвосты прошлого
        # экрана («Файл: …», «Осталось дней: …»), а первая строка после
        # выхода из списка печаталась ПОВЕРХ старой строки, склеиваясь с её
        # хвостом: «1) Кто быстрее определён как «eu»…».
        printf '\033[J'
    }

    _ups_paint_all
    while true; do
        IFS= read -rsn1 key || { echo ""; return 1; }
        if [ "$key" = $'\e' ]; then
            read -rsn2 -t 0.05 rest || rest=""
            key="${key}${rest}"
        fi

        case "$key" in
            $'\e[A'|k|K)  cur=$(( (cur - 1 + total) % total )) ;;
            $'\e[B'|j|J)  cur=$(( (cur + 1) % total )) ;;
            " ")
                id="${_ids[$cur]}"
                if [ -n "${_sel[$id]:-}" ]; then unset "_sel[$id]"; else _sel["$id"]=1; fi
                ;;
            "")
                n=0
                for id in "${_ids[@]}"; do [ -n "${_sel[$id]:-}" ] && n=$((n + 1)); done
                if [ "$n" -gt 0 ]; then echo ""; echo ""; return 0; fi
                continue
                ;;
            a|A) for id in "${_ids[@]}"; do _sel["$id"]=1; done ;;
            n|N) for id in "${_ids[@]}"; do unset "_sel[$id]"; done ;;
            d|D) for id in "${_ids[@]}"; do unset "_sel[$id]"; done
                 for id in $(prov_default_set); do _sel["$id"]=1; done ;;
            i|I|"ш"|"Ш")
                # Карточка была написана и переведена, но дотянуться до неё
                # было нечем: адрес, bootstrap-IP и политику логирования
                # оператор нигде не видел.
                clear 2>/dev/null || true
                prov_render_detail "${_ids[$cur]}"
                echo ""
                echo -ne "  $(t press_enter_return) " >&2
                read -rsn1 _ || true
                ;;
            q|Q) echo ""; return 1 ;;
            # «+» — добавить свой апстрим прямо отсюда. Код 2: вызывающий
            # откроет экран добавления и вернётся в список с новой записью
            # уже отмеченной. Иначе человек, у которого свой резолвер и есть
            # главный, обязан был бы сначала выбрать чужой и идти менять.
            +|=) echo ""; echo ""; return 2 ;;
            *) continue ;;
        esac

        _ups_paint_all
    done
}

# Запасной ввод номерами — когда stdin не терминал.
_ups_select_numeric() {
    local -n _ids="$1"
    local -n _sel="$2"
    local split="$3" results="$4"
    local input tok i id n mark lat cnt

    while true; do
        echo ""
        print_header "$(t prov_menu_title)"
        echo -e "  ${DIM}$(t prov_group_unfiltered)${NC}"
        n=0
        for id in "${_ids[@]}"; do
            n=$((n + 1))
            [ "$n" -eq $((split + 1)) ] && { echo ""; echo -e "  ${DIM}$(t prov_group_filtered)${NC}"; }
            [ "$n" -eq $(( ${PROV_SPLIT_CUSTOM:-999999} + 1 )) ] && { echo ""; echo -e "  ${DIM}$(t prov_group_custom)${NC}"; }
            lat=""
            [ -n "$results" ] && { lat=$(bench_fmt_latency "$results" "$id") || lat=""; }
            mark="no"; [ -n "${_sel[$id]:-}" ] && mark="yes"
            prov_render_row "$id" "$n" "$mark" "$lat"
        done
        cnt=0
        for id in "${_ids[@]}"; do [ -n "${_sel[$id]:-}" ] && cnt=$((cnt + 1)); done
        echo ""
        echo -e "  ${DIM}$(t prov_legend_warn)${NC}"
        echo -e "  ${DIM}$(t prov_help)${NC}"
        echo -ne "  ${DIM}$(tf prov_chosen_count "$cnt")${NC}  ▸ " >&2
        read -r input || return 1
        if [ -z "$input" ]; then
            [ "$cnt" -gt 0 ] && return 0
            log_warning "$(t prov_none_selected)"
            continue
        fi
        case "$input" in
            a|A) for id in "${_ids[@]}"; do _sel["$id"]=1; done; continue ;;
            n|N) for id in "${_ids[@]}"; do unset "_sel[$id]"; done; continue ;;
            d|D) for id in "${_ids[@]}"; do unset "_sel[$id]"; done
                 for id in $(prov_default_set "$(prov_geo_hint)"); do _sel["$id"]=1; done; continue ;;
            +)   return 2 ;;
        esac
        for tok in ${input//,/ }; do
            case "$tok" in ''|*[!0-9]*) continue ;; esac
            i=$((tok - 1))
            [ "$i" -lt 0 ] && continue
            [ "$i" -ge "${#_ids[@]}" ] && continue
            id="${_ids[$i]}"
            if [ -n "${_sel[$id]:-}" ]; then unset "_sel[$id]"; else _sel["$id"]=1; fi
        done
    done
}

# ═══════════════════════════════════════════════════════════════════════
# ШАГ 3 — ПОЛИТИКА
# ═══════════════════════════════════════════════════════════════════════
wizard_policy() {
    _wiz_header wiz_policy_title wiz_policy_title_solo
    echo -e "  ${CYAN}1)${NC} $(t policy_fastest)"
    echo -e "     ${DIM}$(t policy_fastest_hint)${NC}"
    echo -e "  ${CYAN}2)${NC} $(t policy_roundrobin)"
    echo -e "     ${DIM}$(t policy_roundrobin_hint)${NC}"
    echo ""
    local choice=""
    while true; do
        echo -ne "  ▸ " >&2
        read -r choice || { echo ""; return 1; }
        case "$choice" in
            1) config_set policy "fastest";    log_success "$(t policy_fastest)";    return 0 ;;
            2) config_set policy "roundrobin"; log_success "$(t policy_roundrobin)"; return 0 ;;
            *) log_warning "$(t invalid_choice)" ;;
        esac
    done
}

# ═══════════════════════════════════════════════════════════════════════
# ЛИМИТ ЗАПРОСОВ НА КЛИЕНТА
# ═══════════════════════════════════════════════════════════════════════
# Один адрес — не всегда один человек: роутер (OpenWrt с Forkop/Podkop,
# Keenetic) приносит DNS всей квартиры, а туннель — всех своих клиентов
# разом. Поэтому лимит поштучный (лишнее отбрасывается в ту же секунду), а
# не бан, и его можно поднять или снять здесь. 0 — без лимита. Значение
# читается через движок, а не из config.json напрямую: умолчание и
# прижатие живут в одном месте — там, где значение уезжает в конфиг.
wizard_ratelimit() {
    print_header "$(t rl_title)"
    local cur
    cur=$(engine_ratelimit_qps) || cur="200"
    echo -e "  ${DIM}$(t rl_hint_1)${NC}"
    echo -e "  ${DIM}$(t rl_hint_2)${NC}"
    echo -e "  ${DIM}$(t rl_hint_3)${NC}"
    echo ""
    local ans=""
    while true; do
        echo -ne "  $(t rl_prompt) ${DIM}[${cur}]${NC} " >&2
        # Ctrl-D — отмена; Enter — оставить как есть.
        godns_read_line || { echo "" >&2; log_info "$(t startup_cancelled)"; return 1; }
        ans="$GODNS_REPLY"
        [ -z "$ans" ] && ans="$cur"
        case "$ans" in
            ''|*[!0-9]*) log_warning "$(t rl_invalid)"; continue ;;
        esac
        # Длина — до арифметики (двадцать знаков ломают -gt), 10# снимает
        # ведущие нули: «08» иначе доезжало до рендера и валило $((08 * 2)).
        if [ "${#ans}" -gt 6 ]; then log_warning "$(t rl_invalid)"; continue; fi
        ans=$((10#$ans))
        if [ "$ans" -gt 100000 ]; then log_warning "$(t rl_invalid)"; continue; fi
        break
    done
    if [ "$ans" = "$cur" ]; then
        log_dim "$(t rl_unchanged)"
        return 0
    fi
    config_set ratelimit_qps "$ans" || return 1
    if [ "$ans" = "0" ]; then
        log_success "$(t rl_set_off)"
    else
        log_success "$(tf rl_set "$ans")"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ШАГ 4 — ПОРТЫ И СЕКРЕТНЫЙ ПУТЬ
# ═══════════════════════════════════════════════════════════════════════
# У обоих соседей :443 занят: goVLESS Pro отдаёт его xray, goTelegram Pro —
# nginx-диспетчеру ssl_preread. Поэтому свой порт здесь не прихоть, а
# единственный рабочий вариант.
wizard_ports() {
    _wiz_header wiz_ports_title wiz_ports_title_solo

    # ГРАБЛИ: умолчанием предлагался «первый свободный» порт, а не текущий.
    # Оператор заходил посмотреть, жал Enter дважды — и подписывался на
    # переезд резолвера на другой порт, отключая всех клиентов. Текущее
    # значение всегда важнее свободного.
    local doh_default dot_default
    doh_default=$(config_get doh_port "") || doh_default=""
    dot_default=$(config_get dot_port "") || dot_default=""
    if [ -z "$doh_default" ]; then
        # 443 ПЕРВЫМ, если он свободен. У DoH нет своего порта намеренно
        # (RFC 8484): на 443 он неотличим от обычного HTTPS, а на 8443 —
        # выделяется, и вдобавок из гостевых и корпоративных сетей наружу
        # часто выпускают только 80 и 443.
        #
        # Если 443 занят, значит рядом уже стоит сосед (goVLESS отдаёт его
        # xray, goTelegram — SNI-диспетчеру nginx), и трогать его нельзя.
        #
        # Но «свободен» ≠ «останется свободным»: goTelegram занимает :443
        # только после настройки прокси, а до того порт пуст. Мастер это
        # видел и брал 443, обещая «DoH не отличить от HTTPS». Через день
        # сосед настраивался — и либо не поднимался, либо сносил резолвер у
        # всех клиентов разом. Сосед на диске есть — 443 не предлагаем.
        # Сосед на диске — не берём ни 443, ни его прочие порты, даже
        # если они сейчас свободны: godns_first_free_port пропускает всё,
        # что числится за соседом по построению. Первый вариант этой
        # правки предлагал 8443 — порт САЙТА goTelegram.
        if godns_neighbour_present >/dev/null 2>&1; then
            doh_default=$(godns_first_free_port 4443 8444 9444 5443 7444) \
                || doh_default="4443"
        else
            doh_default=$(first_free_port 443 8443 4443 8444 9443) || doh_default="8443"
        fi
    fi
    if [ -z "$dot_default" ]; then
        dot_default="853"
        if port_in_use 853; then
            dot_default=$(first_free_port 8853 9853 1853) || dot_default="8853"
        fi
    fi

    if port_in_use 443; then
        # «Порт 443 занят: dnsdist» + совет «берём свой порт» сбивали с
        # толку, когда 443 держит наш же работающий dnsdist: оператор менял
        # порт, спасаясь от самого себя. Про свой порт не предупреждаем.
        if ! _port_is_ours "$(port_owner 443)"; then
            _port_warn_if_busy 443 "$(t ports_443_note)"
        fi
    else
        local nb=""
        nb=$(godns_neighbour_present) || nb=""
        if [ -n "$nb" ]; then
            # Сосед стоит, но 443 ещё не занял. Говорим правду: порт пуст
            # СЕЙЧАС, и занять его — значит поссориться с соседом позже.
            log_warning "$(tf ports_443_neighbour "$nb")"
            log_dim "$(t ports_443_neighbour_why)"
        else
            log_success "$(t ports_443_free)"
            log_dim "$(t ports_443_future)"
        fi
    fi

    local doh_port dot_port
    doh_port=$(_ask_port "$(t ports_ask_doh)" "$doh_default") || return 1

    # Честно и СРАЗУ, а не когда человек уже раздал адрес клиентам.
    # Нестандартный порт DoH закрывает целые классы устройств:
    #   • iPhone/iPad/Mac — системный профиль Apple игнорирует порт в
    #     ServerURL и всегда идёт на 443 (профиль ставится и молча не
    #     работает). Остаётся DoT — и только если он включён на 853;
    #   • часть прошивок роутеров ходит по DoH только на 443;
    #   • гостевые и корпоративные сети часто выпускают наружу лишь 80/443.
    # Это не запрет: у нестандартного порта своя причина (сосед на 443).
    # Но узнать цену человек обязан здесь, а не через неделю жалоб.
    if [ "$doh_port" != "443" ]; then
        echo ""
        log_warning "$(tf ports_nonstd_warn "$doh_port")"
        log_dim "$(t ports_nonstd_apple)"
        log_dim "$(t ports_nonstd_routers)"
        log_dim "$(t ports_nonstd_nets)"
        if port_in_use 443 && ! _port_is_ours "$(port_owner 443)"; then
            log_dim "$(tf ports_nonstd_why_busy "$(port_owner 443)")"
        else
            log_dim "$(t ports_nonstd_hint_free)"
        fi
        echo ""
    fi
    # DoT спрашиваем ОТДЕЛЬНО и по умолчанию отвечаем «нет».
    #
    # У DoT нет секретного пути — протокол его не предусматривает. Значит
    # включённый DoT открыт любому, кто знает домен, а домен виден в
    # сертификате. Проверено на стенде: посторонний клиент нашёл резолвер и
    # начал им пользоваться в первые минуты после установки.
    #
    # Держать его стоит ровно ради Android: «Частный DNS» в настройках
    # системы умеет ТОЛЬКО DoT, только по имени хоста, только порт 853.
    # Для всего остального DoH и удобнее, и безопаснее.
    echo ""
    echo -e "  ${DIM}$(t ports_dot_why)${NC}"
    echo -e "  ${DIM}$(t ports_dot_android)${NC}"
    echo ""
    local dot_port="" dot_now="n"
    # Умолчание — ТЕКУЩЕЕ состояние, а не «нет». Оператор с работающим DoT,
    # зашедший сменить только порт DoH и жмущий Enter на всех вопросах,
    # молча выключал DoT: клиенты Android отваливались без единого
    # сообщения. Те же грабли «текущее важнее свободного» для портов здесь
    # уже починены — для DoT почему-то нет.
    [ "$(config_get dot_enabled "no")" = "yes" ] && dot_now="y"
    # dot_enabled НЕ пишем здесь. Раньше «да» записывалось до того, как
    # спрошен порт, и отмена на следующем же вопросе оставляла половину
    # настройки применённой: в конфиге DoT включён, порт прежний, экран
    # сказал «Отменено». Все записи — одной группой в конце функции.
    local dot_want="no"
    if confirm "$(t ports_dot_ask)" "$dot_now"; then
        dot_want="yes"
        dot_port=$(_ask_port "$(t ports_ask_dot)" "$dot_default") || return 1
        if [ "$doh_port" = "$dot_port" ]; then
            log_error "$(t ports_same)"
            return 1
        fi
    else
        dot_port="$dot_default"
        log_dim "$(t ports_dot_off)"
    fi

    # Секретный путь. dnsdist сверяет путь точно и отдаёт 404 на любой другой —
    # то есть путь здесь настоящий, а не декоративный.
    local suggested path=""
    suggested=$(config_get doh_path "") || suggested=""
    [ -z "$suggested" ] && suggested=$(random_path 12)
    echo ""
    echo -ne "  $(tf ports_ask_path "$suggested") " >&2
    godns_read_line || { echo "" >&2; return 1; }
    path="$GODNS_REPLY"
    [ -z "$path" ] && path="$suggested"
    path="${path#/}"
    # ГРАБЛИ: проверялся только СОСТАВ символов, но не длина — и путь из
    # одной буквы принимался молча. Поймано случайным вводом на живом
    # сервере: получился адрес вида https://домен:4443/n. А точная сверка
    # пути в dnsdist — единственное, что отличает наш резолвер от
    # открытого: короткий путь подбирается за секунды, и резолвером
    # начинают пользоваться посторонние. Генератор даёт 12 символов;
    # меньше восьми принимать нельзя.
    local why=""
    why=$(godns_doh_path_ok "$path") || {
        case "$why" in
            short) log_warning "$(tf ports_path_short "$GODNS_DOH_PATH_MIN")" ;;
            long)  log_warning "$(tf ports_path_long  "$GODNS_DOH_PATH_MAX")" ;;
            *)     log_warning "$(t ports_path_bad)" ;;
        esac
        path="$suggested"
    }

    # Прежние порты запоминаем, чтобы закрыть их в файрволе после смены:
    # иначе на сервере навсегда остаётся открытая дыра на порт, который
    # больше никто не слушает.
    local prev_doh prev_dot
    prev_doh=$(config_get doh_port "") || prev_doh=""
    prev_dot=$(config_get dot_port "") || prev_dot=""
    [ -n "$prev_doh" ] && [ "$prev_doh" != "$doh_port" ] && config_set doh_port_prev "$prev_doh"
    [ -n "$prev_dot" ] && [ "$prev_dot" != "$dot_port" ] && config_set dot_port_prev "$prev_dot"

    config_set dot_enabled "$dot_want"
    config_set doh_port "$doh_port"
    config_set dot_port "$dot_port"
    config_set doh_path "${path#/}"
    log_success "$(tf ports_set "$doh_port" "$dot_port" "$path")"
    return 0
}

# Порт держит наша же служба?
_port_is_ours() {
    local owner="${1:-}" svc
    [ -n "$owner" ] || return 1
    svc=$(engine_name 2>/dev/null) || svc=""
    case "$owner" in
        *dnsdist*) return 0 ;;
        *"${svc}"*) [ -n "$svc" ] && return 0 ;;
    esac
    return 1
}

_port_warn_if_busy() {
    local p="$1" note="${2:-}"
    if port_in_use "$p"; then
        local who; who=$(port_owner "$p") || who=""
        log_dim "$(tf ports_busy "$p" "${who:-$(t ports_unknown_owner)}")"
        [ -n "$note" ] && log_dim "$note"
    fi
    return 0
}

_ask_port() {
    local prompt="$1" default="$2" ans=""
    while true; do
        echo -ne "  ${prompt} ${DIM}[${default}]${NC} " >&2
        # Ctrl-D — отмена, а не «согласен с умолчанием»: иначе жест выхода
        # проводил мастер до конца и перезапускал службу. Enter (пустая
        # строка при коде 0) по-прежнему берёт текущее значение.
        godns_read_line || { echo "" >&2; return 1; }
        ans="$GODNS_REPLY"
        [ -z "$ans" ] && ans="$default"
        if ! valid_port "$ans"; then
            log_warning "$(t ports_invalid)"
            continue
        fi
        if port_in_use "$ans"; then
            local who; who=$(port_owner "$ans") || who=""
            # Свой же работающий dnsdist занятым не считаем — иначе смена
            # настроек на живой службе каждый раз требует подтверждения
            # «занять порт», который и так наш.
            if ! _port_is_ours "$who"; then
                log_warning "$(tf ports_busy "$ans" "${who:-$(t ports_unknown_owner)}")"
                confirm "$(t ports_use_anyway)" n || continue
            fi
        fi
        printf '%s' "$ans"
        return 0
    done
}

# ═══════════════════════════════════════════════════════════════════════
# ШАГ 5 — РАЗВЁРТЫВАНИЕ
# ═══════════════════════════════════════════════════════════════════════
wizard_deploy() {
    print_header "$(t wiz_deploy_title)"

    wait_for_cloud_init
    ensure_packages curl openssl ca-certificates || return 1

    engine_install || return 1

    # Сертификат: dnsdist с 1.5.0 не работает от root и не прочитает privkey
    # certbot'а. Группа ssl-cert — штатный для дистрибутива способ, и certbot
    # сохраняет владельца-группу при перевыпуске.
    local domain; domain=$(config_get domain "") || domain=""
    # ГРАБЛИ: certs_grant_read_access трёхзначна. rc=2 значит «права выданы,
    # но проверить нечем» (нет runuser) — это НЕ отказ. Раньше здесь ловился
    # любой ненулевой код, и установка вставала на пятом шаге с диагнозом
    # «Служба не смогла прочитать приватный ключ», которого никто не
    # устанавливал. В reconfigure разбор уже был правильный — переносим.
    local _grc=0
    certs_grant_read_access "$domain" "$(engine_service_user)" || _grc=$?
    if [ "$_grc" -eq 1 ]; then
        log_error "$(t deploy_cert_access_failed)"
        return 1
    fi
    [ "$_grc" -eq 2 ] && log_warning "$(t deploy_cert_access_unproven)"

    # Бюджет логов — ДО engine_apply, а не после. Порядок обязателен:
    # drop-in юнита уводит вывод службы в /var/log/godns/dnsdist.log через
    # StandardOutput=append:, и systemd открывает этот файл сам. Если каталога
    # ещё нет, юнит не стартует вовсе — то есть цена перепутанного порядка
    # здесь не «нет логов», а «нет резолвера».
    godns_logs_apply_all || log_warning "$(t logs_deploy_failed)"

    # engine_apply сам рендерит во временный файл, проверяет его
    # `dnsdist --check-config` и только потом ставит на место. Отдельный
    # вызов engine_render_config здесь был лишним и вдобавок обходил бы эту
    # защиту — а поскольку реализация требует путь к файлу, он ещё и всегда
    # возвращал 1, из-за чего развёртывание падало, ни разу не дойдя до apply.
    engine_apply || return 1

    # Счётчик запросов: минутный таймер срезов — та же обвязка вокруг
    # службы, что и бюджет логов, но ставится ПОСЛЕ engine_apply: первый
    # срез (точка отсчёта окон) снимается сразу, и с уже работающего
    # движка, — панель раньше перестаёт показывать прочерки.
    godns_stats_install || log_warning "$(t stats_deploy_failed)"

    # Реакция на перевыпуск подбирается по месту хранения сертификата:
    # certbot-линия → deploy-hook, любой другой путь → .path-юнит systemd.
    certs_setup_renewal_reaction "$domain" "$(config_get cert_path "")" \
        || log_warning "$(t deploy_hook_failed)"
    firewall_open_ports || true

    if engine_is_running; then
        godns_mark_applied
        log_success "$(t deploy_ok)"
        return 0
    fi
    log_error "$(t deploy_failed)"
    engine_status || true
    return 1
}

# Тонкая обёртка над общей godns_fw_apply_ports (lib/common.sh): SSH,
# панель и бот зовут одну и ту же логику. Имя оставлено — на него завязаны
# вызовы в wizard_deploy и reconfigure.
firewall_open_ports() { godns_fw_apply_ports; }


# ufw «Status: active» переведён gettext'ом — читаем конфиг, не вывод.

# ═══════════════════════════════════════════════════════════════════════
# МАСТЕР ЦЕЛИКОМ
# ═══════════════════════════════════════════════════════════════════════
wizard_full() {
    _wizard_snapshot_config
    GODNS_IN_WIZARD="yes"
    print_header "$(t wiz_title)"
    echo -e "  ${DIM}$(t wiz_intro)${NC}"
    echo ""

    wizard_cert      || { log_warning "$(t wiz_aborted)"; _wizard_restore_config; return 1; }
    wizard_upstreams || { log_warning "$(t wiz_aborted)"; _wizard_restore_config; return 1; }
    wizard_policy    || { log_warning "$(t wiz_aborted)"; _wizard_restore_config; return 1; }
    wizard_ports     || { log_warning "$(t wiz_aborted)"; _wizard_restore_config; return 1; }

    echo ""
    print_header "$(t wiz_summary_title)"
    show_plan
    echo ""
    confirm "$(t wiz_confirm)" y || {
        log_info "$(t startup_cancelled)"
        _wizard_restore_config
        return 1
    }

    wizard_deploy || return 1

    echo ""
    run_action selftest_quick
    echo ""
    run_action submenu_connect
    # Отдельным БЛОКОМ в конце мастера: это тот момент, когда человек
    # закончил настройку и сейчас закроет терминал. Строкой в общем потоке
    # она тонет между выводом самопроверки и подсказками клиентам.
    echo ""
    echo -e "  ${DIM}$(printf -- '─%.0s' $(seq 1 55))${NC}"
    echo -e "  ${BOLD}$(t wiz_menu_label)${NC}  ${CYAN}${BOLD}godns${NC}"
    echo -e "  ${DIM}$(t wiz_menu_note)${NC}"
    echo -e "  ${DIM}$(printf -- '─%.0s' $(seq 1 55))${NC}"
    return 0
}

# Сводка перед развёртыванием.
#
# ГРАБЛИ: отступы здесь были забиты пробелами вручную под длину русских
# подписей — пять подписей давали пять разных колонок, а в английском всё
# разъезжалось ещё сильнее. Выравнивание одно на продукт: _dash_row.
#
# И вторая: апстримы печатались значением ключа `upstreams`, то есть сырыми
# каноническими записями «cloudflare|dot|1.1.1.1|853|one.one.one.one|;…».
# Оператор должен увидеть то же, что выбирал в списке: имя, фильтрацию и
# задержку.
show_plan() {
    local d; d=$(config_get domain "") || d=""
    _dash_row "$(t plan_domain):"    "${WHITE}${d}${NC}"
    _dash_row "$(t plan_cert):"      "${DIM}$(config_get cert_path "")${NC}"
    _dash_row "$(t plan_doh):"       "${WHITE}$(godns_doh_url)${NC}"
    if [ "$(config_get dot_enabled no)" = "yes" ]; then
        _dash_row "$(t plan_dot):" "${WHITE}${d}:$(config_get dot_port "")${NC}"
    else
        _dash_row "$(t plan_dot):" "${RED}$(t dash_dot_off)${NC}"
    fi
    _dash_row "$(t plan_policy):"    "${WHITE}$(t "policy_$(config_get policy fastest)")${NC}"

    local ids; ids=$(config_get upstream_ids "") || ids=""
    if [ -z "$ids" ]; then
        _dash_row "$(t plan_upstreams):" "${DIM}—${NC}"
    else
        local n=0 id
        for id in ${ids//,/ }; do n=$((n + 1)); done
        _dash_row "$(t plan_upstreams):" "${WHITE}${n}${NC}"
        for id in ${ids//,/ }; do
            _plan_upstream_row "$id"
        done
    fi
    echo -e "  ${DIM}$(t plan_encrypted_only)${NC}"
}

# Одна строка апстрима в сводке: имя, фильтрация, задержка из последнего
# замера. Замер переиспользуем, а не гоняем заново: он только что прошёл
# на шаге выбора, и повторять 13 секунд ожидания перед подтверждением —
# худшее, что можно сделать с человеком, который уже всё выбрал.
_plan_upstream_row() {
    local id="$1" name filt lat=""
    name=$(prov_name "$id" 2>/dev/null) || name="$id"
    filt=$(prov_filter_text "$id" 2>/dev/null) || filt=""
    if [ -n "${GODNS_BENCH_RESULTS:-}" ] && [ -f "${GODNS_BENCH_RESULTS}" ]; then
        lat=$(bench_fmt_latency "$GODNS_BENCH_RESULTS" "$id" 2>/dev/null) || lat=""
    fi
    # Колонки те же, что в списке выбора (prov_render_row): оператор
    # сверяет сводку со списком, из которого только что выбирал.
    printf '    %b%s%b%b%s%b%b%s%b\n' \
        "$WHITE" "$(_prov_pad "$name" 26)" "$NC" \
        "$DIM"   "$(_prov_pad "$filt" 19)" "$NC" \
        "$DIM"   "$lat" "$NC"
    return 0
}


# ═══════════════════════════════════════════════════════════════════════
# МЕНЮ
# ═══════════════════════════════════════════════════════════════════════
# ── Счётчик запросов в главном меню ─────────────────────────────────────
# Две строки статистики стоят ПОСЛЕДНИМИ перед приглашением. Пока оператор
# молчит, они обновляются НА МЕСТЕ раз в 10 секунд — без перерисовки
# экрана: ожидание ввода и таймер обновления — это ОДИН `read -t 10`.
#
# ГРАБЛИ (дважды наступали в перерисовке списка апстримов): прыжок курсора
# не вычисляется по содержимому экрана — курсор ставится в ИЗВЕСТНУЮ
# точку. Здесь она известна по построению: строки статистики — ровно две
# и ровно над строкой приглашения, значит смещение всегда «вверх на 2».
# Если строка могла бы перенестись (терминал уже 80 колонок) или это не
# TTY — обновление на месте выключается совсем (GODNS_MENU_STATS_ROWS=0),
# и меню ведёт себя как раньше: обычный read без таймаута.
GODNS_MENU_STATS_ROWS=0
GODNS_MENU_STATS_L1=""
GODNS_MENU_STATS_L2=""

_menu_stats_fetch() {
    GODNS_MENU_STATS_L1=""
    GODNS_MENU_STATS_L2=""
    local out=""
    out=$(godns_stats_line 2>/dev/null) || return 1
    [ -n "$out" ] || return 1
    GODNS_MENU_STATS_L1="${out%%$'\n'*}"
    GODNS_MENU_STATS_L2="${out#*$'\n'}"
    return 0
}

_menu_stats_paint() {
    GODNS_MENU_STATS_ROWS=0
    # Нет файла срезов (goDNS ещё не развёрнут) — нет и блока: прочерки
    # на пустой машине были бы шумом, а не информацией.
    _menu_stats_fetch || return 0
    printf '%b\n%b\n' "$GODNS_MENU_STATS_L1" "$GODNS_MENU_STATS_L2"
    if [ ! -t 0 ] || [ ! -t 1 ]; then return 0; fi
    local cols=0
    cols=$(tput cols 2>/dev/null) || cols=0
    case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
    if [ "$cols" -ge 80 ]; then GODNS_MENU_STATS_ROWS=2; fi
    return 0
}

_menu_stats_repaint() {
    [ "${GODNS_MENU_STATS_ROWS:-0}" -eq 2 ] || return 0
    _menu_stats_fetch || return 0
    # \r — в нулевую колонку строки приглашения; вверх ровно на 2; каждую
    # строку стереть целиком (\033[2K) и напечатать заново; два \n
    # возвращают курсор на строку приглашения, последний \033[2K стирает
    # старое приглашение — read напечатает его заново.
    printf '\r\033[2A\033[2K%b\n\033[2K%b\n\r\033[2K' \
        "$GODNS_MENU_STATS_L1" "$GODNS_MENU_STATS_L2"
    return 0
}

# Прощание — в одной функции. Мест выхода из меню три (пункт 0, Ctrl-D в
# приглашении и Ctrl-D на «нажмите Enter»), и подсказку пришлось бы
# дописывать в каждое: одно забыли бы наверняка.
#
# ЗАЧЕМ ОНА: установщик печатает «Команда: godns» и тут же делает exec в
# мастер — к концу установки строка уезжает на сотни строк вверх. Человек
# заканчивал настройку, выходил и не знал, чем открыть меню обратно.
_say_bye() {
    echo -e "  $(t bye)"
    # Оформление то же, что у «DoH URL:» в подсказках клиентам: жирная
    # подпись, значение голубым. Раньше вся строка была ${DIM} — самым
    # незаметным, что есть в продукте, — то есть ровно наоборот тому, чего
    # мы от неё хотим.
    echo -e "  ${BOLD}$(t menu_again_hint)${NC}  ${CYAN}${BOLD}godns${NC}"
}

main_menu() {
    while true; do
        show_dashboard
        # Мастер — только пока настраивать нечего. На настроенной машине он
        # ничего не даёт (всё то же есть в разделах), а строку занимает
        # навсегда и стоит первым, оттесняя то, ради чего сюда заходят.
        # ГРАБЛИ: мастер печатался как «1)» И следом безусловный блок тоже
        # начинался с «1)». На свежей машине оператор видел ДВА пункта с
        # номером один, а «Как подключиться» был недостижим никаким числом.
        # Мастер — это «0-й шаг»: он и стоит отдельно, под своим номером.
        local _connect_n=" 1"
        if ! godns_state_present; then
            echo -e "  ${CYAN} 1)${NC} $(t menu_wizard)"
            echo -e "  ${DIM}     $(t menu_wizard_hint)${NC}"
            echo ""
            _connect_n=" 6"
        fi
        echo -e "  ${CYAN}${_connect_n})${NC} $(t menu_connect)"
        echo -e "  ${CYAN} 2)${NC} $(t menu_observe)"
        echo -e "  ${CYAN} 3)${NC} $(t menu_dns)"
        echo -e "  ${CYAN} 4)${NC} $(t menu_godns)"
        echo -e "  ${CYAN} 5)${NC} $(t menu_remote)"
        echo -e "  ${CYAN} 0)${NC} $(t exit)"
        echo -e "  ${DIM}$(printf -- '─%.0s' $(seq 1 55))${NC}"
        _menu_stats_paint

        # Истёкший таймаут read возвращает код >128, а НЕДОБРАННЫЙ ввод
        # оставляет в переменной — копим его в pending и показываем в
        # приглашении, чтобы обновление статистики не съедало уже
        # набранное. EOF (Ctrl-D) — код 1: выходим, как и раньше.
        local choice="" pending="" _rrc=0
        while true; do
            choice=""; _rrc=0
            if [ "${GODNS_MENU_STATS_ROWS:-0}" -gt 0 ]; then
                read -rp "  $(t choose): ${pending}" -t 10 choice || _rrc=$?
            else
                read -rp "  $(t choose): ${pending}" choice || _rrc=$?
            fi
            if [ "$_rrc" -gt 128 ]; then
                pending="${pending}${choice}"
                _menu_stats_repaint
                continue
            fi
            if [ "$_rrc" -ne 0 ]; then echo ""; _say_bye; exit 0; fi
            choice="${pending}${choice}"
            break
        done
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1)  # «1» — мастер, пока настраивать нечего; дальше это
                # подключение. Номера на экране совпадают: на свежей машине
                # подключение печатается как «6», и оно же принимается.
                if godns_state_present; then
                    run_action submenu_connect
                else
                    run_action wizard_full
                fi
                ;;
            6) run_action submenu_connect ;;
            2) run_action submenu_observe ;;
            3) run_action submenu_dns ;;
            4) run_action submenu_godns ;;
            5) run_action submenu_remote ;;
            0) run_action _say_bye; exit 0 ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        # Раздел вышел по «0) Назад» — читать нечего, пауза лишняя.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        # `|| …`: main_menu — единственный цикл ВНЕ run_action, здесь errexit
        # жив, и голый read на Ctrl-D убивал бы скрипт с кодом 1 без слов.
        read -r || { echo ""; _say_bye; exit 0; }
    done
}

# ── Раздел 1: как подключиться ──────────────────────────────────────────
# Самое частое действие после установки — дать человеку адрес. Раньше оно
# лежало на третьем уровне (5→3), глубже, чем «удалить goDNS».
submenu_connect() {
    while true; do
        print_header "$(t menu_connect)"
        local dom doh dot
        dom=$(config_get domain "") || dom=""
        if [ -z "$dom" ]; then
            log_error "$(t test_no_domain)"
            echo -ne "  $(t press_enter_return) "; read -r; return
        fi
        doh=$(godns_doh_url) || doh=""
        echo -e "  ${BOLD}$(t test_snip_url)${NC}  ${CYAN}${doh}${NC}"
        if [ "$(config_get dot_enabled "no")" = "yes" ]; then
            dot="${dom}:$(config_get dot_port 853)"
            echo -e "  ${BOLD}$(t test_snip_dot)${NC}  ${CYAN}${dot}${NC}"
        else
            echo -e "  ${BOLD}$(t test_snip_dot)${NC}  ${DIM}$(t test_snip_dot_off)${NC}"
        fi
        print_rule
        echo -e "  ${CYAN}1)${NC} $(t test_snip_sec_qr)"
        echo -e "  ${CYAN}2)${NC} $(t test_snip_sec_profile)"
        echo -e "  ${CYAN}3)${NC} $(t test_snip_sec_routers)"
        echo -e "  ${CYAN}4)${NC} $(t test_snip_sec_os)"
        echo -e "  ${CYAN}5)${NC} $(t test_snip_sec_browsers)"
        echo -e "  ${CYAN}6)${NC} $(t test_snip_sec_all)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action selftest_snip_phone_qr ;;
            2) run_action selftest_snip_apple_profile ;;
            3) run_action selftest_snip_routers ;;
            4) run_action selftest_snip_os ;;
            5) run_action selftest_snip_browsers ;;
            6) run_action selftest_snip_all ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Раздел 2: наблюдение и отладка ──────────────────────────────────────
# Проверки, живое наблюдение и журналы — один инструментальный раздел.
# Журналы переехали сюда из «Службы»: их ищут при разборе, а не в настройках.
submenu_observe() {
    while true; do
        print_header "$(t menu_observe)"
        local qs=""
        case "$(engine_query_log_state 2>/dev/null)" in
            on)  qs="${GREEN}$(t qlog_state_on)${NC}" ;;
            off) qs="${DIM}$(t qlog_state_off)${NC}" ;;
            *)   qs="${DIM}$(t qlog_state_unknown)${NC}" ;;
        esac
        echo -e "  ${DIM}── $(t obs_grp_checks) ──${NC}"
        echo -e "  ${CYAN} 1)${NC} $(t test_quick)"
        echo -e "  ${CYAN} 2)${NC} $(t test_full)"
        echo -e "  ${CYAN} 3)${NC} $(t test_leak)"
        echo -e "  ${CYAN} 4)${NC} $(t test_external)"
        echo -e "  ${DIM}── $(t obs_grp_live) ──${NC}"
        echo -e "  ${CYAN} 5)${NC} $(t mon_item_live)"
        echo -e "  ${CYAN} 6)${NC} $(t mon_item_upstreams)"
        echo -e "  ${CYAN} 7)${NC} $(t mon_item_stats)"
        echo -e "  ${DIM}── $(t obs_grp_logs) ──${NC}"
        echo -e "  ${CYAN} 8)${NC} $(t mon_item_follow)"
        echo -e "  ${CYAN} 9)${NC} $(t logs_menu_report)"
        echo -e "  ${CYAN}10)${NC} $(t logs_menu_sweep)"
        echo -e "  ${CYAN}11)${NC} $(t logs_menu_rate)"
        echo -e "  ${CYAN}12)${NC} $(t mon_qlog_label)   ${DIM}[${NC}${qs}${DIM}]${NC}"
        echo -e "  ${CYAN} 0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action selftest_quick ;;
            2) run_action selftest_full ;;
            3) run_action selftest_leak_audit ;;
            4) run_action selftest_external_check ;;
            5) run_action engine_live_monitor ;;
            6) run_action engine_upstream_report ;;
            7) run_action godns_stats_screen ;;
            8) run_action engine_logs_follow ;;
            9) run_action godns_logs_report ;;
            10) run_action godns_logs_cleanup_now ;;
            11) run_action logs_rate_and_source ;;
            12) run_action toggle_query_log ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# Один пункт вместо двух («включить» и «выключить» рядом читались как
# выбор из двух состояний, хотя это переключатель одного).
toggle_query_log() {
    if [ "$(engine_query_log_state 2>/dev/null)" = "on" ]; then
        engine_query_log_disable
    else
        engine_query_log_enable
    fi
}

# ── Раздел 3: настройки DNS ─────────────────────────────────────────────
submenu_dns() {
    while true; do
        print_header "$(t menu_dns)"
        local pol dot_state
        pol=$(config_get policy "roundrobin") || pol="roundrobin"
        case "$pol" in
            roundrobin) pol=$(t policy_roundrobin) ;;
            *)          pol=$(t policy_fastest) ;;
        esac
        if [ "$(config_get dot_enabled "no")" = "yes" ]; then
            dot_state="${GREEN}$(t state_on)${NC} :$(config_get dot_port 853)"
        else
            dot_state="${RED}$(t dash_dot_off)${NC}"
        fi
        echo -e "  ${DIM}── $(t dns_grp_upstreams) ──${NC}"
        echo -e "  ${CYAN} 1)${NC} $(t ups_reselect)"
        echo -e "  ${CYAN} 2)${NC} $(t ups_policy)   ${DIM}[${pol}]${NC}"
        echo -e "  ${CYAN} 3)${NC} $(t ups_measure)"
        local ncust=0
        ncust=$(prov_custom_count 2>/dev/null) || ncust=0
        echo -e "  ${CYAN} 4)${NC} $(t ups_custom_add)   ${DIM}[$(tf ups_custom_count "${ncust:-0}")]${NC}"
        echo -e "  ${CYAN} 5)${NC} $(t ups_custom_remove)"
        echo -e "  ${DIM}── $(t dns_grp_ports) ──${NC}"
        echo -e "  ${CYAN} 6)${NC} $(t ports_change)   ${DIM}[DoH $(config_get doh_port 443)]${NC}"
        echo -e "  ${CYAN} 7)${NC} $(t ports_newpath)"
        echo -e "  ${CYAN} 8)${NC} $(t ports_dot_toggle)   ${DIM}[${NC}${dot_state}${DIM}]${NC}"
        echo -e "  ${DIM}── $(t dns_grp_cert) ──${NC}"
        echo -e "  ${CYAN} 9)${NC} $(t cert_rescan)"
        echo -e "  ${CYAN}10)${NC} $(t cert_show)"
        echo -e "  ${CYAN}11)${NC} $(t cert_renew_hook)"
        echo -e "  ${CYAN}12)${NC} $(t certs_issue_new)"
        echo -e "  ${DIM}── $(t dns_grp_limit) ──${NC}"
        # Состояние — в строке пункта. В песочнице меню движка нет, и
        # обёртка отсутствует: тогда «?», а не падение экрана.
        local rl_state=""
        rl_state=$(engine_ratelimit_qps 2>/dev/null) || rl_state=""
        if [ "$rl_state" = "0" ]; then
            rl_state=$(t rl_state_off)
        elif [ -n "$rl_state" ]; then
            rl_state=$(tf rl_state_on "$rl_state")
        else
            rl_state="?"
        fi
        echo -e "  ${CYAN}13)${NC} $(t rl_menu)   ${DIM}[${rl_state}]${NC}"
        echo -e "  ${CYAN} 0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action reconfigure wizard_upstreams ;;
            2) run_action reconfigure wizard_policy ;;
            3) run_action measure_only ;;
            4) run_action reconfigure custom_upstream_add ;;
            5) run_action reconfigure custom_upstream_remove ;;
            6) run_action reconfigure wizard_ports ;;
            7)  # Смена пути отваливает ВСЕХ клиентов разом.
                if typed_confirm "$(t ports_newpath_word)" "$(t ports_newpath_warn)"; then
                    run_action rotate_path
                else
                    log_info "$(t startup_cancelled)"
                fi
                ;;
            8) run_action reconfigure toggle_dot ;;
            9) run_action reconfigure wizard_cert ;;
            10) run_action cert_show_current ;;
            11) run_action cert_reinstall_hook ;;
            12) run_action reconfigure certs_issue_interactive ;;
            13) run_action reconfigure wizard_ratelimit ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Раздел 4: настройки самого goDNS ────────────────────────────────────
submenu_godns() {
    while true; do
        print_header "$(t menu_godns)"
        local sched lang
        sched=$(config_get backup_schedule "off") || sched="off"
        sched=$(godns_backup_schedule_label "$sched") || sched="?"
        lang=$(detect_language) || lang="ru"
        echo -e "  ${DIM}── $(t gd_grp_service) ──${NC}"
        echo -e "  ${CYAN} 1)${NC} $(t svc_status)"
        echo -e "  ${CYAN} 2)${NC} $(t svc_restart)"
        echo -e "  ${CYAN} 3)${NC} $(t svc_reload_certs)"
        echo -e "  ${CYAN} 4)${NC} $(t svc_showconf)"
        echo -e "  ${DIM}── $(t gd_grp_backup) ──${NC}"
        echo -e "  ${CYAN} 5)${NC} $(t bk_m_create)"
        echo -e "  ${CYAN} 6)${NC} $(t bk_m_list)"
        echo -e "  ${CYAN} 7)${NC} $(t bk_m_restore)"
        echo -e "  ${CYAN} 8)${NC} $(t bk_m_prune)"
        echo -e "  ${CYAN} 9)${NC} $(t bk_m_schedule)   ${DIM}[${sched}]${NC}"
        echo -e "  ${DIM}── $(t gd_grp_program) ──${NC}"
        echo -e "  ${CYAN}10)${NC} $(t logs_menu_install)"
        echo -e "  ${CYAN}11)${NC} $(t menu_language)   ${DIM}[${lang}]${NC}"
        echo -e "  ${CYAN}12)${NC} $(t menu_about)"
        print_rule
        echo -e "  ${CYAN}99)${NC} ${RED}$(t svc_uninstall)${NC}"
        echo -e "  ${CYAN} 0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action engine_status ;;
            2) run_action service_restart ;;
            3) run_action engine_reload_certs ;;
            4) run_action engine_show_config ;;
            5) run_action godns_backup_interactive ;;
            6) run_action godns_backup_list ;;
            7) run_action godns_backup_restore_interactive ;;
            8) run_action prune_backups_ask ;;
            9) run_action submenu_backup_schedule ;;
            10) run_action godns_logs_apply_all ;;
            11) run_action language_menu ;;
            12) run_action submenu_about ;;
            99) run_action uninstall_godns ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}


cert_show_current() {
    local c; c=$(config_get cert_path "") || c=""
    if [ -z "$c" ] || [ ! -f "$c" ]; then
        log_warning "$(t cert_none_configured)"
        return 0
    fi
    print_header "$(t cert_show)"
    # ГРАБЛИ: отступы тут были забиты пробелами вручную под длину русских
    # подписей — четыре подписи давали четыре разные колонки, а «Days left»
    # против «Осталось дней» разъезжало их ещё и по-английски. kv_row.
    kv_row "$(t plan_domain)" "${WHITE}$(config_get domain "")${NC}"     14
    kv_row "$(t cert_file)"   "${DIM}${c}${NC}"                          14
    kv_row "$(t cert_days)"   "${WHITE}$(certs_days_left "$c")${NC}"     14
    kv_row "$(t cert_san)"    "${DIM}$(certs_san_list "$c")${NC}"        14
    return 0
}

cert_reinstall_hook() {
    local d; d=$(config_get domain "") || d=""
    if [ -z "$d" ]; then log_warning "$(t cert_none_configured)"; return 0; fi
    # ГРАБЛИ: certs_setup_renewal_reaction уже печатает свой успех с
    # именем файла, и второе «Хук продления установлен.» подряд читалось
    # как «установлен дважды». Сообщение оставляем ОДНО — то, что называет
    # файл; здесь только честный отказ, если не получилось.
    certs_setup_renewal_reaction "$d" "$(config_get cert_path "")" \
        || log_warning "$(t deploy_hook_failed)"
    return 0
}


# Замер БЕЗ выбора. Экран рисуется теми же строками, что и настоящий выбор,
# и от этого читался как выбор: заголовок «Выбор апстримов», у всех строк
# пустые [ ] — включая три работающих, — и ничего нельзя нажать. Оператор
# читал это как «выбор слетел». Свой заголовок, галочки по ФАКТУ конфига,
# и прямо сказано, что здесь ничего не меняется.
measure_only() {
    local results
    log_info "$(t prov_measuring)"
    results=$(bench_run_all) || results=""
    if [ -z "$results" ]; then log_warning "$(t prov_measure_failed)"; return 0; fi
    print_header "$(t prov_measure_title)"
    local chosen=""
    chosen=$(config_get upstream_ids "") || chosen=""
    local n=0 id sel
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        n=$((n + 1))
        sel="no"
        case ",${chosen}," in *",${id},"*) sel="yes" ;; esac
        prov_render_row "$id" "$n" "$sel" "$(bench_fmt_latency "$results" "$id")"
    done < <(prov_menu_ids)
    echo ""
    echo -e "  ${DIM}$(t prov_measure_readonly)${NC}"
    return 0
}

# Меняем настройку и, если служба уже развёрнута, перегенерируем конфиг.
# Меняем настройку и, если служба уже развёрнута, применяем.
#
# ГРАБЛИ: раньше функция ВСЕГДА возвращала 0, даже когда engine_apply падал.
# Настройка при этом уже записана в config.json. Получалось «сохранено, но
# не применено» без единого признака: панель рисует новые значения, служба
# работает по старым. Это корень почти всех расхождений между конфигом и
# реальностью.
reconfigure() {
    local step="$1" rc=0
    shift || true
    # Отпечаток ДО шага. ГРАБЛИ: шаг возвращает 0 и когда что-то изменил,
    # и когда оператор отказался («Включить DoT? n» → «Отменено»), а
    # reconfigure применял конфигурацию в обоих случаях — перезапускал
    # службу и печатал «Изменения применены» под словом «Отменено».
    # Человек, зашедший посмотреть и передумавший, ронял соединения
    # клиентов на ровном месте.
    local fp_before=""
    fp_before=$(godns_config_fingerprint) || fp_before=""
    "$step" "$@" || return 1

    # Ничего не изменилось И прежнее уже применено — применять нечего.
    # Второе условие обязательно: при дрейфе (правка извне, оборванное
    # применение) пункт меню обязан оставаться способом всё вправить.
    local fp_after=""
    fp_after=$(godns_config_fingerprint) || fp_after=""
    if [ -n "$fp_before" ] && [ "$fp_before" = "$fp_after" ] \
       && ! godns_config_drifted; then
        log_dim "$(t reconf_nothing_changed)"
        return 0
    fi

    if ! engine_is_installed; then
        log_dim "$(t reconf_not_installed)"
        return 0
    fi

    # Домен мог смениться — права на ключ и хук продления привязаны к нему,
    # и без переоформления служба не прочитает новый сертификат, а продление
    # будет сторожить старую линию.
    local dom; dom=$(config_get domain "") || dom=""
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
        firewall_open_ports || true
        godns_mark_applied
        log_success "$(t reconf_applied)"
        return 0
    fi
    log_error "$(t reconf_apply_failed)"
    log_warning "$(t reconf_saved_not_applied)"
    return 1
}


# Включить или выключить DoT одним пунктом, не проходя весь шаг портов.
#
# DoT выключен по умолчанию не из осторожности, а по устройству протокола:
# секретного пути у него нет и быть не может, поэтому включённый он открыт
# любому, кто знает домен — а домен виден в сертификате.
toggle_dot() {
    local on; on=$(config_get dot_enabled "no")
    if [ "$on" = "yes" ]; then
        log_warning "$(tf ports_dot_now_on "$(config_get dot_port 853)")"
        # Умолчание «нет»: Enter на этом вопросе отключал ВСЕХ клиентов
        # Android разом. Разрушающий ответ не должен быть умолчанием.
        if confirm "$(t ports_dot_disable_ask)" n; then
            config_set dot_enabled "no"
            log_success "$(t ports_dot_off)"
            log_dim "$(t ports_dot_android_lost)"
            return 0
        fi
        log_info "$(t startup_cancelled)"
        return 0
    fi

    log_dim "$(t ports_dot_why)"
    log_dim "$(t ports_dot_android)"
    if ! confirm "$(t ports_dot_enable_ask)" n; then
        log_info "$(t startup_cancelled)"
        return 0
    fi

    # 853 не просто «по умолчанию»: Android ходит только туда и порт задать
    # не даёт. На другом порту DoT для Android бесполезен.
    local port; port=$(config_get dot_port "853")
    if [ "$port" != "853" ]; then
        log_warning "$(tf ports_dot_nonstd "$port")"
    fi
    if port_in_use "$port" && ! _port_is_ours "$(port_owner "$port")"; then
        log_error "$(tf ports_busy "$port" "$(port_owner "$port")")"
        return 1
    fi
    config_set dot_enabled "yes"
    config_set dot_port "$port"
    log_success "$(tf ports_dot_on "$port")"
    return 0
}

# ГРАБЛИ: раньше здесь стоял log_success, потом engine_apply || true, потом
# показ нового адреса клиентам. При провале применения оператор получал
# зелёную галку и рассылал URL, которого нет: dnsdist сверяет путь ТОЧНО и
# отдаёт 404 — DNS отваливался разом у всех. Сначала применяем.
rotate_path() {
    local p old
    old=$(config_get doh_path "") || old=""
    # Внутри действий меню errexit мёртв, поэтому коды проверяем сами.
    # Раньше обе строки шли без проверки: не записался config.json (нет
    # места) — engine_apply применял СТАРЫЙ путь, а log_success печатал
    # НОВЫЙ. Оператор рассылал клиентам адрес, которого нет. Зеркала в
    # боте и панели это уже проверяли, здесь — нет.
    p=$(random_path 12) || p=""
    if [ -z "$p" ]; then
        log_error "$(t ports_path_gen_failed)"
        return 1
    fi
    if ! config_set doh_path "${p#/}"; then
        log_error "$(t cfg_write_failed)"
        return 1
    fi

    if engine_is_installed; then
        if ! engine_apply; then
            config_set doh_path "$old"
            log_error "$(t reconf_apply_failed)"
            log_warning "$(t ports_newpath_rolled_back)"
            return 1
        fi
        # Без штампа панель после УСПЕШНОЙ смены пути показывала бы
        # «есть непринятые изменения»: отпечаток включает doh_path.
        godns_mark_applied
    fi
    log_success "$(tf ports_newpath_ok "$p")"
    run_action submenu_connect
    return 0
}



# «Перезапустить» у dnsdist на деле означает «применить текущий config.json»:
# перечитать конфиг иначе движок не умеет (engine_apply = render + check +
# restart). Значит после успеха отпечаток обязан обновиться — иначе панель
# продолжает показывать «есть непринятые изменения», которых уже нет.
service_restart() {
    engine_apply || return 1
    godns_mark_applied
    return 0
}



# Замер потока + разбор источника одним действием: голое число «строк в
# минуту» без ответа «почему их столько» оператору бесполезно.
logs_rate_and_source() {
    print_header "$(t logs_cut_title)"
    godns_logs_apply_source_cut || true
    echo ""
    godns_logs_rate_report 20 || true
    return 0
}


submenu_backup_schedule() {
    while true; do
        print_header "$(t bk_sched_title)"
        run_action godns_backup_schedule_status
        echo ""
        echo -e "  ${CYAN}1)${NC} $(t bk_sched_daily)"
        echo -e "  ${CYAN}2)${NC} $(t bk_sched_weekly)"
        echo -e "  ${CYAN}3)${NC} $(t bk_sched_monthly)"
        echo -e "  ${CYAN}4)${NC} $(t bk_sched_off)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action godns_backup_schedule_set daily ;;
            2) run_action godns_backup_schedule_set weekly ;;
            3) run_action godns_backup_schedule_set monthly ;;
            4) run_action godns_backup_schedule_set off ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# Полное удаление. Единственное, к чему не прикасаемся, — чужие
# сертификаты: они принадлежат goVLESS или goTelegram.
#
# ГРАБЛИ: раньше здесь снимались только служба, хук и /etc/godns. На сервере
# оставались сама команда godns, каталог программы и настройки — оператор
# видел работающее меню и справедливо решал, что ничего не удалилось.
# Прореживание копий удаляет архивы НАВСЕГДА. Сам godns_backup_prune
# спрашивать не может: его молча зовут godns_update_finish и создание
# копии. Поэтому вопрос живёт здесь, у пункта меню, и называет поимённо
# то, что исчезнет, — как это делает опись при удалении goDNS.
prune_backups_ask() {
    local keep="$GODNS_BACKUP_KEEP" files="" f n=0 doomed=()
    files=$(godns_backup_files) || files=""
    if [ -z "$files" ]; then
        log_info "$(tf bk_none "$GODNS_BACKUP_DIR")"
        return 0
    fi
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        n=$((n + 1))
        [ "$n" -le "$keep" ] && continue
        doomed+=("$f")
    done <<EOF
$files
EOF
    if [ "${#doomed[@]}" -eq 0 ]; then
        log_info "$(tf bk_prune_nothing "$n" "$keep")"
        return 0
    fi
    log_warning "$(tf bk_prune_will_remove "${#doomed[@]}" "$keep")"
    for f in "${doomed[@]}"; do echo -e "    ${DIM}$(basename "$f")${NC}"; done
    echo ""
    if ! confirm "$(t bk_prune_confirm)" n; then
        log_info "$(t startup_cancelled)"
        return 0
    fi
    godns_backup_prune "$keep"
}

uninstall_godns() {
    print_header "$(t uninstall_title)"

    # Опись ДО вопроса: человек должен видеть, на что соглашается.
    local -a targets=()
    local p
    for p in "$GODNS_ETC" "$GODNS_DIR" "$GODNS_INSTALLER_DIR" \
             /usr/local/bin/godns \
             "$LE_HOOK_FILE" \
             /etc/systemd/system/godns-cert-reload.path \
             /etc/systemd/system/godns-cert-reload.service \
             "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.timer" \
             "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.service" \
             "$GODNS_LOGROTATE_CONF" "$GODNS_LOG_TMPFILES" \
             "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_ROT_UNIT}.timer" \
             "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_SWEEP_UNIT}.timer" \
             "${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.timer" \
             "${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.service" \
             "$GODNS_STATS_TMPFILES" "$GODNS_STATS_DIR" \
             "$GODNS_LOG_DIR"; do
        [ -e "$p" ] && targets+=("$p")
    done

    echo -e "  ${DIM}$(t uninstall_will_remove)${NC}"
    for p in "${targets[@]:-}"; do [ -n "$p" ] && echo -e "    ${DIM}${p}${NC}"; done
    echo -e "    ${DIM}$(t uninstall_will_remove_svc)${NC}"
    # Бот и панель снимаются вместе с goDNS, но в описи их не было: она
    # перечисляла пути и «служба dnsdist», а Telegram-бот с его отдельным
    # пользователем и веб-панель с её адресом человек в этом списке не
    # видел. Подтверждать снос надо, зная, что именно исчезнет.
    if godns_bot_installed 2>/dev/null; then
        echo -e "    ${DIM}$(tf uninstall_will_remove_bot "$GODNS_BOT_USER")${NC}"
    fi
    if webui_installed 2>/dev/null; then
        echo -e "    ${DIM}$(t uninstall_will_remove_web)${NC}"
    fi
    echo ""
    log_success "$(t uninstall_keeps_cert)"
    echo ""

    typed_confirm "$(t uninstall_word)" "$(t uninstall_type)" \
        || { log_info "$(t startup_cancelled)"; return 0; }

    # Предложить копию настроек: путь и апстримы восстанавливать вручную
    # дороже, чем нажать «да».
    if [ -f "$GODNS_CONFIG" ] && confirm "$(t uninstall_backup_ask)" y; then
        # Метка — чтобы копия перед удалением опознавалась в списке спустя
        # месяцы. stdout гасим: путь к архиву godns_backup_create печатает
        # в stdout как возвращаемое значение, а человеку его уже показал
        # log_success — второй раз, голой строкой, он только мешает.
        # Сообщение о копии — только СНЯВ её. Безусловная печать означала,
        # что при нехватке места оператор читал «копия лежит там-то» и
        # подтверждал снос, не имея отката.
        if run_action godns_backup_create pre-uninstall >/dev/null; then
            log_dim "$(tf bk_kept_after_uninstall "$GODNS_BACKUP_DIR")"
        else
            log_warning "$(t bk_pre_uninstall_failed)"
        fi
    fi

    engine_uninstall || true

    # Бот и панель — до сноса /opt/godns и /etc/godns: их юниты, сокет и
    # пользователь живут отдельно, и `rm -rf` состояния их не снимет.
    # Оставленный юнит — это failed-служба в systemd навсегда, ровно тот
    # мусор, из-за которого снятие уже однажды признали блокирующим.
    godns_bot_remove --no-ask || true
    webui_remove || true

    # Хук продления и слежение за сертификатом — наши, снимаем.
    # Через функцию, а не голым `rm -f … || true`: наш файл лежит в ЧУЖОМ
    # каталоге certbot, и если снять его не удалось (только чтение,
    # immutable), молчаливое удаление отрапортует об успехе, а сосед будет
    # вечно исполнять хук, зовущий несуществующую службу.
    certs_remove_renewal_hook || true
    if systemctl list-unit-files godns-cert-reload.path >/dev/null 2>&1; then
        systemctl disable --now godns-cert-reload.path >/dev/null 2>&1 || true
    fi
    rm -f /etc/systemd/system/godns-cert-reload.path \
          /etc/systemd/system/godns-cert-reload.service 2>/dev/null || true

    # Таймер расписания копий — тоже наш. Оставленный включённым, он после
    # удаления /opt/godns-installer падал бы при КАЖДОМ срабатывании: его
    # ExecStart сорсит lib/*.sh из каталога, которого больше нет, — и
    # systemd копил бы failed-юниты на «чистой» машине.
    systemctl disable --now "${GODNS_BACKUP_TIMER}.timer" >/dev/null 2>&1 || true
    rm -f "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.timer" \
          "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.service" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true

    # Правила ротации, таймеры и tmpfiles — наши файлы, снимаем их сами.
    # Чужие конфиги в /etc/logrotate.d/ и /etc/tmpfiles.d/ не читаем и не
    # трогаем: каталоги аддитивные, соседи там живут своей жизнью.
    godns_logs_remove || true

    # Таймер срезов статистики — наш, снимаем вместе с прочими юнитами.
    # Сам файл срезов — отдельным вопросом ниже, как и логи.
    godns_stats_remove || true

    _uninstall_firewall
    _uninstall_cron
    _uninstall_cert_group

    # Сами логи — отдельным вопросом. Молча стирать журнал службы нельзя:
    # именно по нему разбирают, почему goDNS пришлось удалять.
    if [ -d "$GODNS_LOG_DIR" ]; then
        if confirm "$(t logs_remove_files_ask)" n; then
            rm -rf "$GODNS_LOG_DIR" 2>/dev/null || true
        else
            log_dim "$(tf logs_remove_files_kept "$GODNS_LOG_DIR")"
        fi
    fi

    # Статистика запросов — тем же отдельным вопросом и по той же причине:
    # история нагрузки может понадобиться уже после удаления.
    if [ -d "$GODNS_STATS_DIR" ]; then
        if confirm "$(t stats_remove_file_ask)" n; then
            rm -rf "$GODNS_STATS_DIR" 2>/dev/null || true
        else
            log_dim "$(tf stats_remove_file_kept "$GODNS_STATS_DIR")"
        fi
    fi

    rm -rf "$GODNS_ETC" "$GODNS_DIR" 2>/dev/null || true

    # Временные файлы. Уборка по EXIT их не гарантирует: подстановка $(…)
    # плодила файлы в подоболочках (историческое имя без PID), а процесс,
    # убитый kill -9, не убирает ничего. На живом стенде после одной
    # установки осталось 26 сирот /tmp/godns.* — и лежали бы до перезагрузки.
    # Удаление — последний шанс подмести весь наш неймспейс. Имена строго
    # наши: godns.* и godns-progress.*; чужой godns-cleanup.* не трогаем —
    # это скрипт финальной уборки, свой удалит себя сам.
    rm -f /tmp/godns.* /tmp/godns-progress.* 2>/dev/null || true

    log_success "$(t uninstall_done)"

    # Каталог программы и обёртку сносим ПОСЛЕДНИМИ и чужими руками: bash
    # дочитывает скрипт по мере выполнения, и удаление файла из-под себя
    # обрывает его на середине.
    #
    # mktemp, а не предсказуемое имя с $$: PID угадываем, /tmp общий, и
    # заранее подложенный симлинк /tmp/godns-cleanup.<pid> заставил бы root
    # писать скрипт СКВОЗЬ симлинк в чужой файл. mktemp создаёт файл с
    # O_EXCL и правами 0600 — по симлинку он не пойдёт.
    local cleanup=""
    cleanup=$(mktemp /tmp/godns-cleanup.XXXXXX) || cleanup=""
    if [ -z "$cleanup" ]; then
        # /tmp недоступен — каталог программы остаётся, но небезопасную
        # запись по предсказуемому имени не делаем ни при каких условиях.
        log_dim "$(t uninstall_bye)"
        exit 0
    fi
    {
        printf '%s\n' '#!/bin/sh'
        printf 'sleep 1\n'
        printf 'rm -rf %s /usr/local/bin/godns\n' "$GODNS_INSTALLER_DIR"
        printf 'rm -f "$0"\n'
    } > "$cleanup"
    chmod +x "$cleanup"
    setsid "$cleanup" >/dev/null 2>&1 &
    log_dim "$(t uninstall_bye)"
    exit 0
}

# Закрыть порты, которые открывали мы.
_uninstall_firewall() {
    local p
    command -v ufw >/dev/null 2>&1 || return 0
    [ -r /etc/ufw/ufw.conf ] || return 0
    grep -qE '^[[:space:]]*ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null || return 0
    for p in "$(config_get doh_port "")" "$(config_get dot_port "")"; do
        [ -n "$p" ] || continue
        ufw delete allow "${p}/tcp" >/dev/null 2>&1 && log_dim "$(tf fw_closed "$p")"
    done
    return 0
}

# Снять крон-строку продления, если её добавляли мы (при выпуске своего
# сертификата). Чужие строки не трогаем.
#
# ГРАБЛИ: фильтр по подстроке «certbot renew --quiet» сносил и ЧУЖУЮ строку
# — у goVLESS/оператора она отличается только хвостом («--deploy-hook …»),
# и после удаления goDNS чужое автопродление тихо умирало; поломка всплыла
# бы при следующем перевыпуске, до 60 дней спустя. Поэтому наша строка
# помечена маркером «# goDNS» (certs_enable_autorenew), и удаляется ТОЛЬКО
# строка с маркером. Строку без маркера (старые установки) не трогаем:
# лишний «certbot renew» безвреден, а снесённый чужой — нет.
_uninstall_cron() {
    command -v crontab >/dev/null 2>&1 || return 0
    [ "$(config_get cert_owned_by_godns "no")" = "yes" ] || return 0
    crontab -l 2>/dev/null | grep -qF "certbot renew --quiet # goDNS" || return 0
    crontab -l 2>/dev/null | grep -vF "certbot renew --quiet # goDNS" | crontab - 2>/dev/null \
        && log_dim "$(t uninstall_cron_removed)"
    return 0
}

# Вернуть права на каталоги Let's Encrypt, если после нас группа ssl-cert
# больше никому не нужна. Сами сертификаты не трогаем — только режим
# каталогов, который меняли мы.
_uninstall_cert_group() {
    local others
    getent group ssl-cert >/dev/null 2>&1 || return 0
    others=$(getent group ssl-cert | cut -d: -f4 | tr ',' '\n' | grep -v '^$' | grep -v '^_dnsdist$' | wc -l)
    others=$(printf '%s' "$others" | tr -d ' ')
    if [ "${others:-0}" -gt 0 ]; then
        log_dim "$(tf uninstall_group_kept "$others")"
        return 0
    fi
    if confirm "$(t uninstall_group_ask)" y; then
        chmod 0700 "${LE_ROOT}/live" "${LE_ROOT}/archive" 2>/dev/null || true
        chgrp root "${LE_ROOT}/live" "${LE_ROOT}/archive" 2>/dev/null || true
        log_dim "$(t uninstall_group_restored)"
    fi
    return 0
}

# Бот и веб-панель — один пункт на двоих: обе возможности про одно и то же
# («управлять не из SSH»), и разводить их по верхнему уровню значило бы
# растить главное меню там, где растёт список, а не набор задач.
# ── Раздел 5: удалённое управление ──────────────────────────────────────
# Бот и панель одним экраном, двумя блоками. Раньше это был третий уровень
# (8→1→…, 8→2→…): всего два пункта наверху ради семи и шести внутри.
submenu_remote() {
    while true; do
        print_header "$(t menu_remote)"
        # Состояние берём готовыми помощниками библиотек, а не своим
        # systemctl: у каждой из них своя правда о том, что значит
        # «установлен» (у панели — ещё и порт), и дублировать её здесь
        # значит завести третье зеркало, которое разъедется.
        local bot_st web_st
        if godns_bot_installed 2>/dev/null; then
            if godns_bot_running; then
                bot_st="${GREEN}$(t state_running)${NC}"
            else
                bot_st="${YELLOW}$(t state_stopped)${NC}"
            fi
        else
            bot_st="${DIM}$(t state_not_installed)${NC}"
        fi
        web_st="${DIM}$(webui_status_line)${NC}"

        echo -e "  ${DIM}── $(t menu_remote_bot) ──${NC}  ${bot_st}"
        echo -e "  ${CYAN} 1)${NC} $(t bot_menu_install)"
        echo -e "  ${CYAN} 2)${NC} $(t bot_menu_status)"
        echo -e "  ${CYAN} 3)${NC} $(t bot_menu_claim)"
        echo -e "  ${CYAN} 4)${NC} $(t bot_menu_admins)"
        echo -e "  ${CYAN} 5)${NC} $(t bot_menu_restart)"
        echo -e "  ${CYAN} 6)${NC} $(t bot_menu_logs)"
        echo -e "  ${CYAN} 7)${NC} ${RED}$(t bot_menu_remove)${NC}"
        echo -e "  ${DIM}── $(t menu_remote_web) ──${NC}  ${web_st}"
        echo -e "  ${CYAN} 8)${NC} $(t webui_menu_install)"
        echo -e "  ${CYAN} 9)${NC} $(t webui_menu_access)"
        echo -e "  ${CYAN}10)${NC} $(t webui_menu_restart)"
        echo -e "  ${CYAN}11)${NC} $(t webui_menu_newpath)"
        echo -e "  ${CYAN}12)${NC} ${RED}$(t webui_menu_remove)${NC}"
        echo -e "  ${CYAN} 0)${NC} $(t back)"
        echo ""
        local choice
        read -rp "  ▸ " choice || { echo ""; _back; return; }
        GODNS_SUBMENU_BACK="no"
        case "$choice" in
            1) run_action godns_bot_setup_wizard ;;
            2) run_action godns_bot_status ;;
            3) run_action godns_bot_claim_code_show ;;
            4) run_action godns_bot_admins_show ;;
            5) run_action godns_bot_restart ;;
            6) run_action godns_bot_logs ;;
            7) run_action godns_bot_remove ;;
            8) run_action webui_install ;;
            9) run_action webui_show_access ;;
            10) run_action webui_restart ;;
            11) # Та же цена, что у смены пути DoH: адрес панели умирает
                # у всех, кто им пользуется. Спрашиваем так же — словом.
                if typed_confirm "$(t webui_newpath_word)" "$(t webui_newpath_warn)"; then
                    run_action webui_rotate_panel_path
                else
                    log_info "$(t startup_cancelled)"
                fi
                ;;
            12)
                if confirm "$(t webui_remove_ask)" n; then
                    run_action webui_remove
                else
                    log_info "$(t startup_cancelled)"
                fi
                ;;
            0) _back; return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        # Вложенный экран вышел по «0) Назад» — читать нечего.
        if [ "$GODNS_SUBMENU_BACK" = "yes" ]; then continue; fi
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

submenu_about() {
    print_header "$(t submenu_about_title)"
    echo -e "  ${BOLD}goDNS Pro${NC} v${GODNS_VERSION}"
    echo -e "  ${DIM}$(t about_engine): $(engine_name)${NC}"
    echo -e "  ${DIM}$(t about_os): ${OS_PRETTY:-?}${NC}"
    echo ""
    echo -e "  ${DIM}$(t about_caps):${NC}"
    local c
    for c in can_enforce_doh_path can_true_roundrobin can_reload_certs_live has_console; do
        printf '    %-24s %s\n' "$c" "$(engine_cap "$c")"
    done
    echo ""
    print_rule
    echo -e "  ${DIM}dnsdist: powerdns.com  •  goDNS Pro: potap1978${NC}"
    print_rule
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
main() {
    # Таблицу строк поднимаем ПЕРВОЙ: её просит и справка ниже, и
    # startup_checks. Только загрузка, без записи на диск — до os_gate мы
    # на диск не пишем ничего.
    load_language "$(detect_language)" || true

    # Флаги — сразу за ней: `godns --version` запускал ПОЛНОЕ меню,
    # потому что обёртка честно передаёт "$@", а godns.sh про аргументы не
    # знал вовсе. Человек, спросивший версию у скрипта, получал
    # интерактивный экран на сервере без терминала.
    case "${1:-}" in
        -v|--version)
            printf 'goDNS Pro %s\n' "$GODNS_VERSION"
            exit 0
            ;;
        -h|--help)
            printf 'goDNS Pro %s\n\n' "$GODNS_VERSION"
            printf '  godns              %s\n' "$(t cli_help_menu)"
            printf '  godns --version    %s\n' "$(t cli_help_version)"
            printf '  godns --help       %s\n' "$(t cli_help_help)"
            exit 0
            ;;
    esac

    # startup_checks печатает строки через t()/tf(): на чистой машине без
    # загрузки выше оператор видел голый ключ «startup_os_ok Ubuntu 24.04»
    # первой же строкой.
    startup_checks
    init_language
    engine_select "$(config_get engine dnsdist)" || exit 1

    # Установки, сделанные ДО появления бюджета логов, донастраиваем один
    # раз при первом же запуске. Без этого logrotate и таймеры появились бы
    # только после следующего полного развёртывания, а до тех пор
    # /var/log/godns рос бы вообще без ограничений.
    #
    # Условие одноразовое по построению: как только конфиг ротации лёг на
    # место, ветка больше не срабатывает никогда.
    # Гейт — godns_state_present, а НЕ существование config.json. Файл
    # создаёт уже save_language при первом входе в меню: язык выбирают до
    # того, как что-либо установлено. Человек, зашедший посмотреть меню на
    # чистой машине и вышедший, оставлял после себя /var/log/godns,
    # /etc/logrotate.d/godns, /etc/tmpfiles.d/godns.conf и два таймера.
    # Ровно ту же ловушку CLAUDE.md описывает для гейта обновления.
    if godns_state_present && [ ! -f "$GODNS_LOGROTATE_CONF" ]; then
        run_action godns_logs_apply_all
    fi

    # Той же логикой — счётчик запросов: установки, сделанные ДО его
    # появления (0.5.0), получают минутный таймер срезов при первом же
    # запуске. Гейт по факту, а не по версии: движок стоит, таймера нет.
    # Тот же гейт. Вторая ветка опаснее первой: engine_is_installed для
    # dnsdist — это «бинарник есть», а штатное снятие goDNS пакет
    # осознанно оставляет. На вычищенной машине один выбор языка возвращал
    # минутный таймер срезов.
    if godns_state_present && engine_is_installed && ! godns_stats_installed; then
        run_action godns_stats_install
    fi

    # Обновление поверх. Зовём ПОСЛЕ engine_select — иначе проверка «а
    # поднялась ли служба после обновления» некому было бы ответить, и
    # предложить откат было бы не на чем.
    #
    # На совпадающих версиях и на первой установке это молчаливый no-op:
    # ни строчки на экране, ни файла на диске. Через run_action, потому
    # что «не удалось мигрировать» — исход, а не повод закрыть терминал.
    run_action godns_update_finish
    # Код бота и панели живёт КОПИЕЙ в своём каталоге состояния, поэтому
    # подмена дерева программы их не касается. Без этих двух строк
    # обновление goDNS оставляло бы работать старые бот и панель — на
    # диске новый код, в памяти старый, и ни слова об этом. Обе функции
    # молчат и ничего не делают, если приложение не установлено или
    # исходники не изменились.
    run_action godns_bot_sync_app
    run_action webui_sync_app
    # Пауза, а не косметика: следующая же строка — main_menu, а он начинает
    # с show_dashboard, то есть с clear. Без остановки «обновлено с X до Y,
    # настройки сохранены» стёрлось бы, не успев быть прочитанным.
    if [ "${GODNS_UPDATE_SHOWN:-no}" = "yes" ]; then
        echo -ne "  $(t press_enter_return) "
        read -r || true
    fi

    main_menu
}

main "$@"
