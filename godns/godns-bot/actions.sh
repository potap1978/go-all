#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS bot — неинтерактивный фасад над библиотекой goDNS.
#
# Единственный вызывающий — привилегированный демон godns-botd (root).
# Каждое действие — ровно одна функция bot_act_*, дальше — только функции
# lib/*.sh. Никакого чтения с клавиатуры: всё, что в SSH-меню спрашивает
# confirm/typed_confirm, здесь уже подтверждено ботом (typed-подтверждение
# в Telegram), и фасад исполняет без вопросов.
#
# Почему фасад, а не «бот дёргает godns.sh»: godns.sh — интерактивное меню
# и завершается безусловным main "$@". Сорсить его нельзя, кормить меню
# через пайп — хрупко. Библиотека же чистая, и фасад собирает из неё те же
# шаги, что и пункты меню. Там, где пункт меню живёт в godns.sh и в lib
# его нет (service_restart, rotate_path, toggle_dot), шаги повторены здесь
# с пометкой «зеркало godns.sh:<функция>» — при правке оригинала править
# оба места.
#
# stdout+stderr уходят демону как есть (цвета снимает демон), коды возврата
# честные: 0 — успех, не 0 — отказ с объяснением в тексте.
#
# В argv — только идентификаторы (/proc/<pid>/cmdline читает любой локальный
# пользователь). Путь DoH своего апстрима может быть чужим секретом, поэтому
# он приходит переменной окружения GODNS_CUSTOM_PATH — ровно как в мосте
# панели (godns-web/bridge.sh) — и передаётся lib позиционным параметром.

set -euo pipefail

# Библиотека goDNS. Переопределяется тестами.
GODNS_BOT_LIB="${GODNS_BOT_LIB:-/opt/godns-installer/lib}"

if [ ! -f "${GODNS_BOT_LIB}/common.sh" ]; then
    echo "goDNS Pro library not found at ${GODNS_BOT_LIB}" >&2
    exit 3
fi

# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/common.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/i18n.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/certs.sh"
# shellcheck source=lib/domain.sh
source "${GODNS_BOT_LIB}/domain.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/certs_issue.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/providers.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/bench.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/engine.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/logs.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/stats.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/qr.sh"
source "${GODNS_BOT_LIB}/selftest.sh"
# shellcheck source=lib/webui.sh
source "${GODNS_BOT_LIB}/webui.sh"
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/backup.sh"
# update.sh — ради godns_config_migrate: восстановление копии
# зовёт её через `declare -F`, и без этой строки миграция схемы
# молча не выполнялась именно здесь, а в SSH выполнялась.
source "${GODNS_BOT_LIB}/update.sh"
# lib/bot.sh нужен ровно одному действию — uninstall: он умеет снять бота.
# shellcheck source=/dev/null
source "${GODNS_BOT_LIB}/bot.sh"

trap cleanup_temp_files EXIT

# Язык — тот, что выбран в SSH-меню goDNS: у бота свой i18n для СВОИХ фраз,
# а отчёты библиотеки должны говорить на языке, который оператор уже выбрал.
load_language "$(detect_language)" >/dev/null 2>&1 || true
engine_load "$(config_get engine dnsdist)" >/dev/null 2>&1 || true

# ── panel: машиночитаемая сводка (ключ<TAB>значение) ────────────────────
# Бот рисует главный экран сам, поэтому здесь НЕ перевод, а факты.
# ISO → человеческая дата: 2026-11-25 → 25.11.2026.
_bot_human_date() {
    local iso="${1:-}"
    case "$iso" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
            printf '%s.%s.%s' "${iso:8:2}" "${iso:5:2}" "${iso:0:4}" ;;
        *) printf '%s' "${iso:-—}" ;;
    esac
}

bot_act_panel() {
    local state="not_installed"
    if engine_is_running; then state="running"
    elif engine_is_installed; then state="stopped"; fi

    local drift="no"
    if godns_config_drifted; then drift="yes"; fi

    # Домен и сертификат — РАЗНЫЕ сроки. Раньше бот печатал одно
    # «(сертификат: осталось 89 дн.)» рядом с именем домена, без даты, и
    # читалось это как срок домена. То же исправлено в SSH-панели и в
    # веб-панели; бот обязан говорить о том же теми же словами.
    local domain days="" cert_until="" reg_st="" reg_iso="" doh_url="" ups="" n=0
    domain=$(config_get domain "") || domain=""
    if [ -n "$domain" ]; then
        days=$(certs_days_left "$(config_get cert_path "")" 2>/dev/null) || days=""
        cert_until=$(certs_expiry_iso "$(config_get cert_path "")" 2>/dev/null) || cert_until=""
        IFS=$'\t' read -r reg_st reg_iso <<<"$(domain_expiry_cached "$domain" 2>/dev/null)"
    fi
    doh_url=$(godns_doh_url) || doh_url=""
    ups=$(config_get upstream_ids "") || ups=""
    if [ -n "$ups" ]; then
        n=$(printf '%s' "$ups" | tr ',' '\n' | grep -c .) || n=0
    fi

    printf 'version\t%s\n'      "$GODNS_VERSION"
    printf 'state\t%s\n'        "$state"
    printf 'drift\t%s\n'        "$drift"
    printf 'domain\t%s\n'       "$domain"
    printf 'cert_days\t%s\n'    "$days"
    printf 'cert_until\t%s\n'   "$cert_until"
    printf 'reg_status\t%s\n'   "${reg_st:-pending}"
    printf 'reg_until\t%s\n'    "${reg_iso:-}"
    printf 'doh_url\t%s\n'      "$doh_url"
    printf 'doh_port\t%s\n'     "$(config_get doh_port "")"
    printf 'dot_enabled\t%s\n'  "$(config_get dot_enabled "no")"
    printf 'dot_port\t%s\n'     "$(config_get dot_port "853")"
    printf 'policy\t%s\n'       "$(config_get policy "")"
    printf 'upstreams\t%s\n'    "$ups"
    printf 'upstream_count\t%s\n' "$n"

    # Адрес мини-приложения — ТОЛЬКО через webui_url из lib/webui.sh.
    #
    # ГРАБЛИ: здесь стояла своя сборка адреса, и она теряла ПОРТ —
    # получалось https://<домен>/<путь>/, то есть 443, а на 443 у нас
    # dnsdist с DoH. Он честно отвечал 404 «there is no endpoint
    # configured for this path», и кнопка «Мини-приложение» открывала
    # именно эту строку вместо панели. Поймано живым нажатием в Telegram.
    #
    # Второй сборки быть не должно: адрес панели знает ровно один модуль.
    local wu=""
    wu=$(webui_url) || wu=""
    printf 'webui_url\t%s\n' "$wu"
    return 0
}

# ── Статистика запросов ─────────────────────────────────────────────────
bot_act_stats() {
    # Полный экран — только когда файл срезов есть. Ветку «файла нет»
    # обрабатываем сами: в godns_stats_screen там живёт confirm с
    # умолчанием «да», который в неинтерактивном запуске молча ставил бы
    # таймер — побочный эффект от кнопки «показать».
    if [ ! -f "$GODNS_STATS_FILE" ]; then
        log_info "$(t stats_no_data_yet)"
        if ! godns_stats_timer_active; then
            log_warning "$(tf stats_timer_off "${GODNS_STATS_UNIT}.timer")"
        else
            log_dim "$(t stats_first_slice_hint)"
        fi
        return 0
    fi
    godns_stats_screen
}

# ── Сертификат ──────────────────────────────────────────────────────────
# Зеркало godns.sh:cert_show_current — та же карточка, тот же порядок.
bot_act_cert_info() {
    local c; c=$(config_get cert_path "") || c=""
    if [ -z "$c" ] || [ ! -f "$c" ]; then
        log_warning "$(t cert_none_configured)"
        return 0
    fi
    kv_row "$(t plan_domain)" "$(config_get domain "")"          14
    kv_row "$(t cert_file)"   "$c"                               14
    # Дата, а не только «сколько осталось»: числом нельзя свериться с
    # письмом от Let's Encrypt.
    kv_row "$(t cert_until)"  "$(_bot_human_date "$(certs_expiry_iso "$c" 2>/dev/null)")" 14
    kv_row "$(t cert_days)"   "$(certs_days_left "$c")"          14
    kv_row "$(t cert_san)"    "$(certs_san_list "$c")"           14
    return 0
}

# Зеркало godns.sh:cert_reinstall_hook.
bot_act_cert_hook() {
    local d; d=$(config_get domain "") || d=""
    if [ -z "$d" ]; then log_warning "$(t cert_none_configured)"; return 1; fi
    if certs_setup_renewal_reaction "$d" "$(config_get cert_path "")"; then
        log_success "$(t cert_hook_ok)"
        return 0
    fi
    return 1
}

# ── Апстримы ────────────────────────────────────────────────────────────
bot_act_upstream_report() { engine_upstream_report; }

# Зеркало godns.sh:measure_only.
bot_act_measure() {
    local results
    log_info "$(t prov_measuring)"
    results=$(bench_run_all) || results=""
    if [ -z "$results" ]; then log_warning "$(t prov_measure_failed)"; return 1; fi
    local n=0 id
    while IFS= read -r id; do
        [ -n "$id" ] || continue
        n=$((n + 1))
        prov_render_row "$id" "$n" "no" "$(bench_fmt_latency "$results" "$id")"
    done < <(prov_menu_ids)
    return 0
}

# Смена политики. Значения — те же, что пишет wizard_policy.
bot_act_policy_set() {
    local p="${1:-}"
    case "$p" in
        fastest|roundrobin) ;;
        *) log_error "policy: fastest|roundrobin"; return 1 ;;
    esac
    config_set policy "$p" || return 1
    bot_apply_if_installed
}

# ── Порты и путь ────────────────────────────────────────────────────────
bot_act_ports_info() {
    kv_row "$(t bot_ports_doh)" "$(config_get doh_port "")"  16
    if [ "$(config_get dot_enabled no)" = "yes" ]; then
        kv_row "$(t bot_ports_dot)" "$(config_get dot_port 853)" 16
    else
        kv_row "$(t bot_ports_dot)" "$(t dash_dot_off)" 16
    fi
    kv_row "$(t bot_ports_path)" "/$(config_get doh_path "")" 16
    return 0
}

# Зеркало godns.sh:rotate_path, без интерактивного показа сниппетов.
bot_act_rotate_path() {
    local p old
    old=$(config_get doh_path "") || old=""
    # Тот же порядок проверок, что в godns.sh:rotate_path. Без него пустой
    # путь от сорвавшегося генератора уезжал в конфиг, а «новый адрес» в
    # ответе бота указывал в никуда.
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
            config_set doh_path "$old" || true
            log_error "$(t reconf_apply_failed)"
            log_warning "$(t ports_newpath_rolled_back)"
            return 1
        fi
        godns_mark_applied || true
    fi
    log_success "$(tf ports_newpath_ok "$p")"
    printf 'new_url\t%s\n' "$(godns_doh_url)"
    return 0
}

# Зеркало godns.sh:toggle_dot — обе ветки, без confirm (подтвердил бот).
bot_act_dot_on() {
    if [ "$(config_get dot_enabled "no")" = "yes" ]; then
        log_info "$(tf ports_dot_now_on "$(config_get dot_port 853)")"
        return 0
    fi
    local port; port=$(config_get dot_port "853")
    if [ "$port" != "853" ]; then
        log_warning "$(tf ports_dot_nonstd "$port")"
    fi
    if port_in_use "$port" && ! bot_port_is_ours "$(port_owner "$port")"; then
        log_error "$(tf ports_busy "$port" "$(port_owner "$port")")"
        return 1
    fi
    config_set dot_enabled "yes" || return 1
    config_set dot_port "$port" || return 1
    log_success "$(tf ports_dot_on "$port")"
    bot_apply_if_installed
}

bot_act_dot_off() {
    if [ "$(config_get dot_enabled "no")" != "yes" ]; then
        log_info "$(t dash_dot_off)"
        return 0
    fi
    config_set dot_enabled "no" || return 1
    log_success "$(t ports_dot_off)"
    log_dim "$(t ports_dot_android_lost)"
    bot_apply_if_installed
}

# Зеркало godns.sh:_port_is_ours.
bot_port_is_ours() {
    local owner="${1:-}" svc
    [ -n "$owner" ] || return 1
    svc=$(engine_name 2>/dev/null) || svc=""
    case "$owner" in
        *dnsdist*) return 0 ;;
        *"${svc}"*) [ -n "$svc" ] && return 0 ;;
    esac
    return 1
}

# Зеркало хвоста godns.sh:reconfigure: применить, если развёрнуто.
bot_apply_if_installed() {
    if ! engine_is_installed; then
        log_dim "$(t reconf_not_installed)"
        return 0
    fi
    if engine_apply; then
        godns_mark_applied || true
        # ГРАБЛИ: здесь не было открытия портов, хотя это зеркало хвоста
        # `reconfigure` из godns.sh. Админ жал в боте «Включить DoT», бот
        # рапортовал «Конфигурация применена», dnsdist слушал :853, а ufw
        # его резал — клиенты получали таймаут при зелёном ответе бота.
        # SSH-путь и панель порт открывают, бот не открывал.
        godns_fw_apply_ports || true
        log_success "$(t reconf_applied)"
        return 0
    fi
    log_error "$(t reconf_apply_failed)"
    log_warning "$(t reconf_saved_not_applied)"
    return 1
}

# ── Проверка ────────────────────────────────────────────────────────────
bot_act_test_quick() { selftest_quick; }
bot_act_test_full()  { selftest_full; }
bot_act_test_leak()  { selftest_leak_audit; }

# Проверка снаружи раскрывает host:port третьей стороне — согласие уже
# дано кнопкой в боте, поэтому её confirm кормим готовым «y».
bot_act_test_external() {
    printf 'y\n' | selftest_external_check
}

# ── Как подключаться ────────────────────────────────────────────────────
bot_act_snippets() {
    local section="${1:-all}"
    selftest_load_ctx
    if [ -z "${ST_DOMAIN:-}" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    echo -e "  $(t test_snip_url)  https://${ST_DOMAIN}:${ST_DOH_PORT}/${ST_DOH_PATH}"
    if selftest_dot_on; then
        echo -e "  $(t test_snip_dot)  ${ST_DOMAIN}:${ST_DOT_PORT}"
    else
        echo -e "  $(t test_snip_dot)  $(t test_snip_dot_off)"
    fi
    echo ""
    case "$section" in
        routers)  selftest_snip_routers ;;
        os)       selftest_snip_os ;;
        browsers) selftest_snip_browsers ;;
        profile)  selftest_snip_apple_profile ;;
        all)      selftest_snip_all ;;
        *) log_error "snippets: routers|os|browsers|profile|all"; return 1 ;;
    esac
}

# QR и профиль для телефонов. Файлы кладутся в каталог состояния бота:
# он принадлежит пользователю бота (бот их читает и отправляет), а пишет
# сюда root — демон. Строки TGPHOTO:/TGFILE: — контракт с bot.py: он
# вынимает их из вывода и шлёт содержимое фотографией/документом, а весь
# остальной текст уходит сообщением как обычно.
bot_act_phone_qr() {
    local kind="${1:-android}"
    selftest_load_ctx
    if [ -z "${ST_DOMAIN:-}" ]; then
        log_error "$(t test_no_domain)"
        return 1
    fi
    local share="${GODNS_BOT_STATE:-/opt/godns/bot/state}/share"
    mkdir -p "$share" 2>/dev/null || true
    # Пишем от root, а ЧИТАЕТ бот — непривилегированный godns-bot.
    # Без chown файлы выходили root:root 0600, бот получал EACCES, и
    # отправка фото/документа молча не работала. Поймано аудитом; мой
    # живой тест это пропустил, потому что гонял чтение от root же.
    chown "${GODNS_BOT_USER:-godns-bot}:${GODNS_BOT_USER:-godns-bot}" "$share" 2>/dev/null || true
    chmod 700 "$share" 2>/dev/null || true

    case "$kind" in
        android)
            local tp=""
            tp=$(config_get dot_port "853") || tp="853"
            if ! selftest_dot_on; then
                echo -e "  $(t test_snip_android_nodot)"
                echo -e "  $(t test_snip_android_nodot2)"
                echo -e "  $(t qr_android_enable_hint)"
                return 0
            fi
            if [ "$tp" != "853" ]; then
                echo -e "  $(tf test_snip_android_port "$tp")"
                echo -e "  $(t qr_android_fix_port)"
                return 0
            fi
            echo -e "  $(t test_snip_android)"
            echo -e "  ${ST_DOMAIN}"
            echo -e "  $(t test_snip_android_warn)"
            if godns_qr_ensure; then
                local png="${share}/qr-android.png"
                if godns_qr_png "$ST_DOMAIN" "$png"; then
                    chown "${GODNS_BOT_USER:-godns-bot}:${GODNS_BOT_USER:-godns-bot}" "$png" 2>/dev/null || true
                    chmod 600 "$png" 2>/dev/null || true
                    printf 'TGPHOTO:%s\n' "$png"
                fi
            fi
            return 0
            ;;
        ios)
            local profile=""
            local prc=0
            profile=$(st_apple_profile_write) || prc=$?
            if [ "$prc" -eq 2 ]; then
                echo -e "  $(t test_snip_prof_impossible)"
                echo -e "  $(t test_snip_prof_impossible_why)"
                return 0
            fi
            if [ -z "$profile" ]; then
                log_error "$(t test_snip_prof_fail)"
                return 1
            fi
            # Профиль копируем в каталог бота: оригинал в /opt/godns
            # бот-пользователю не читается, и это правильно.
            local copy="${share}/godns-doh.mobileconfig"
            cp -f "$profile" "$copy" 2>/dev/null || return 1
            chown "${GODNS_BOT_USER:-godns-bot}:${GODNS_BOT_USER:-godns-bot}" "$copy" 2>/dev/null || true
            chmod 600 "$copy" 2>/dev/null || true
            echo -e "  $(t qr_ios_bot_what)"
            echo -e "  $(t test_snip_ios2)"
            echo -e "  $(t test_snip_ios3)"
            echo -e "  $(t test_snip_unsigned)"
            printf 'TGFILE:%s\n' "$copy"
            return 0
            ;;
        *) log_error "phone_qr: android|ios"; return 1 ;;
    esac
}

# ── Служба ──────────────────────────────────────────────────────────────
bot_act_svc_status() { engine_status; }

bot_act_svc_start() {
    if ! engine_is_installed; then
        log_warning "$(t reconf_not_installed)"
        return 1
    fi
    if systemctl start "$GODNS_SERVICE" 2>&1; then
        log_success "$(t bot_svc_started)"
        return 0
    fi
    log_error "$(t bot_svc_start_failed)"
    return 1
}

bot_act_svc_stop() {
    if systemctl stop "$GODNS_SERVICE" 2>&1; then
        log_success "$(t bot_svc_stopped)"
        return 0
    fi
    log_error "$(t bot_svc_stop_failed)"
    return 1
}

# Зеркало godns.sh:service_restart: у dnsdist «перезапустить» = «применить
# текущий config.json», и после успеха отпечаток обязан обновиться.
bot_act_svc_apply() {
    engine_apply || return 1
    godns_mark_applied || true
    return 0
}

bot_act_svc_logs() {
    local n="${1:-60}"
    case "$n" in ''|*[!0-9]*) n=60 ;; esac
    [ "$n" -gt 200 ] && n=200
    engine_logs "$n"
}

bot_act_svc_showconf()  { engine_show_config; }
bot_act_reload_certs()  { engine_reload_certs; }

# ── Наблюдение ──────────────────────────────────────────────────────────
bot_act_qlog_state() {
    local s
    s=$(engine_query_log_state 2>/dev/null) || s="unknown"
    printf 'qlog\t%s\n' "$s"
    return 0
}
bot_act_qlog_on()  { engine_query_log_enable; }
bot_act_qlog_off() { engine_query_log_disable; }
bot_act_logs_report() { godns_logs_report; }

# ── Копии ───────────────────────────────────────────────────────────────
bot_act_backup_create() {
    local out=""
    out=$(godns_backup_create "tg-bot") || return 1
    printf 'archive\t%s\n' "$out"
    return 0
}

bot_act_backup_list() { godns_backup_list; }

bot_act_backup_schedule_status() { godns_backup_schedule_status; }

bot_act_backup_schedule_set() {
    local freq="${1:-}"
    case "$freq" in
        daily|weekly|monthly|off) ;;
        *) log_error "schedule: daily|weekly|monthly|off"; return 1 ;;
    esac
    godns_backup_schedule_set "$freq"
}

# ── Свои апстримы ───────────────────────────────────────────────────────
# Тонкая обёртка над lib/webui.sh — ровно те же функции зовёт мост панели
# (bridge.sh), второго пути «своих» нет. Порядок argv закреплён демоном
# (botd.py ACTIONS): probe — proto host port ack; add — proto host port
# name enable ack; remove — id. Путь DoH приходит окружением
# GODNS_CUSTOM_PATH, не argv (см. заголовок), и встаёт четвёртым
# параметром lib-функции.
#
# custom_list печатает первой строкой предел своих записей: бот прячет
# кнопку «Добавить», когда предел достигнут, а число он не сочиняет —
# источник один, PROV_CUSTOM_MAX в lib/providers.sh. Остальное — TSV
# webui_custom_list_tsv (set_count, затем строки id/name/proto/addr/sni/
# in_set); бот различает строки по числу полей.
#
# Тела — в несколько строк, не `f() { g; }`: check-callgraph узнаёт вызов по
# первому токену фрагмента, а в однострочнике это `f()`, и опечатка в имени
# lib-функции проезжала бы страж насквозь.
bot_act_custom_list() {
    printf 'max\t%s\n' "${PROV_CUSTOM_MAX:-8}"
    webui_custom_list_tsv
}
bot_act_custom_probe() {
    webui_custom_probe "${1:-}" "${2:-}" "${3:-}" "${GODNS_CUSTOM_PATH:-}" "${4:-no}"
}
bot_act_custom_add() {
    webui_custom_add "${1:-}" "${2:-}" "${3:-}" "${GODNS_CUSTOM_PATH:-}" "${4:-}" "${5:-no}" "${6:-no}"
}
bot_act_custom_remove() {
    webui_custom_remove "${1:-}"
}

# ── Полное удаление goDNS ───────────────────────────────────────────────
# Зеркало godns.sh:uninstall_godns в неинтерактивных решениях:
#   • копия настроек — ВСЕГДА (в SSH это вопрос с умолчанием «да»);
#   • журналы и статистика — ОСТАВИТЬ (в SSH умолчание «нет» на удаление);
#   • права группы ssl-cert — не трогать (в SSH это вопрос; безопасный
#     неинтерактивный ответ — оставить как есть и сказать об этом);
#   • чужие сертификаты не трогаются никогда.
# Плюс наше: снять самого бота (отложенно, чтобы ответ успел дойти).
bot_act_uninstall() {
    # Сообщать «копия лежит там-то» можно только СНЯВ её. Раньше провал
    # (нет места, нет tar) проглатывался, а строка печаталась безусловно:
    # оператор соглашался «сохранить настройки», читал, что копия есть, и
    # подтверждал снос. Для системы копий это худший вид лжи.
    if run_action godns_backup_create pre-uninstall >/dev/null; then
        log_dim "$(tf bk_kept_after_uninstall "$GODNS_BACKUP_DIR")"
    else
        log_warning "$(t bk_pre_uninstall_failed)"
    fi

    engine_uninstall || true

    # ГРАБЛИ: снятие из бота не трогало ПАНЕЛЬ — юнит godns-web оставался
    # включённым, а следующий `rm -rf /opt/godns` сносил его ExecStart.
    # Получался вечно падающий юнит, и порт панели оставался открытым в
    # межсетевом экране. Оператор при этом видел «удалено».
    webui_remove || true

    # ГРАБЛИ, ломавшие снятие целиком: здесь стоял `godns_bot_remove`, а он
    # делает `systemctl disable --now godns-botd`. Но actions.sh исполняется
    # ДОЧЕРНИМ процессом этого самого демона: start_new_session меняет
    # сессию, а не cgroup, KillMode в юните не задан (умолчание
    # control-group), поэтому SIGTERM прилетал и сюда. Всё, что ниже, не
    # выполнялось НИКОГДА — ни снятие хука certbot, ни таймеры копий, ни
    # закрытие портов, ни `rm -rf /etc/godns /opt/godns`, ни даже создание
    # отложенного скрипта, написанного ровно чтобы этого избежать. Оператор
    # видел: бот замолчал, а система наполовину жива.
    #
    # Поэтому бот и его демон снимаются ТОЛЬКО отложенным скриптом ниже,
    # чужими руками и уже после того, как ответ уехал в Telegram.

    rm -f "$LE_HOOK_FILE" 2>/dev/null || true
    if systemctl list-unit-files godns-cert-reload.path >/dev/null 2>&1; then
        systemctl disable --now godns-cert-reload.path >/dev/null 2>&1 || true
    fi
    rm -f /etc/systemd/system/godns-cert-reload.path \
          /etc/systemd/system/godns-cert-reload.service 2>/dev/null || true

    systemctl disable --now "${GODNS_BACKUP_TIMER}.timer" >/dev/null 2>&1 || true
    rm -f "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.timer" \
          "${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.service" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true

    godns_logs_remove || true
    godns_stats_remove || true

    # Файрвол: закрыть только наши порты (зеркало _uninstall_firewall).
    if command -v ufw >/dev/null 2>&1 && [ -r /etc/ufw/ufw.conf ] \
        && grep -qE '^[[:space:]]*ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null; then
        local p
        for p in "$(config_get doh_port "")" "$(config_get dot_port "")"; do
            [ -n "$p" ] || continue
            ufw delete allow "${p}/tcp" >/dev/null 2>&1 || true
        done
    fi

    # Крон продления — только строка с НАШИМ маркером (зеркало
    # _uninstall_cron; фильтр по чужой подстроке уже однажды снёс чужое
    # автопродление — поэтому только точный маркер «# goDNS»).
    if command -v crontab >/dev/null 2>&1 \
        && [ "$(config_get cert_owned_by_godns "no")" = "yes" ] \
        && crontab -l 2>/dev/null | grep -qF "certbot renew --quiet # goDNS"; then
        crontab -l 2>/dev/null | grep -vF "certbot renew --quiet # goDNS" | crontab - 2>/dev/null || true
    fi

    log_dim "$(t bot_uninstall_kept_logs)"

    rm -rf "$GODNS_ETC" "$GODNS_DIR" 2>/dev/null || true
    rm -f /tmp/godns.* /tmp/godns-progress.* 2>/dev/null || true

    # Программа, обёртка, бот и его демон — отложенно и чужими руками:
    # мы сами исполняемся дочерним процессом godns-botd, и снести его
    # из-под себя синхронно значит оборвать и себя, и ответ в Telegram.
    #
    # Пути берём из переменных, а не строкой: прежний вариант чистил
    # /opt/godns-bot, тогда как бот живёт в ${GODNS_DIR}/bot — след
    # разъехавшегося зеркала. Ошибка была невидима, потому что до этого
    # места выполнение и так не доходило.
    local cleanup=""
    cleanup=$(mktemp /tmp/godns-cleanup.XXXXXX) || cleanup=""
    if [ -n "$cleanup" ]; then
        {
            printf '%s\n' '#!/bin/sh'
            printf 'sleep 5\n'
            printf 'systemctl disable --now %s.service %s.service >/dev/null 2>&1\n' \
                   "$GODNS_BOT_UNIT" "$GODNS_BOTD_UNIT"
            printf 'rm -f %s/%s.service %s/%s.service\n' \
                   "$GODNS_BOT_UNIT_DIR" "$GODNS_BOT_UNIT" \
                   "$GODNS_BOT_UNIT_DIR" "$GODNS_BOTD_UNIT"
            printf 'systemctl daemon-reload >/dev/null 2>&1\n'
            printf 'systemctl reset-failed %s %s >/dev/null 2>&1\n' \
                   "$GODNS_BOT_UNIT" "$GODNS_BOTD_UNIT"
            printf 'userdel %s >/dev/null 2>&1\n' "$GODNS_BOT_USER"
            printf 'rm -rf %s %s %s /usr/local/bin/godns\n' \
                   "$GODNS_BOT_HOME" "$GODNS_DIR" "$GODNS_INSTALLER_DIR"
            printf 'rm -f %s %s\n' "$GODNS_BOTD_SOCK" "$GODNS_BOT_ENV_FILE"
            printf 'rm -rf %s\n' "$GODNS_ETC"
            printf 'rm -f "$0"\n'
        } > "$cleanup"
        chmod +x "$cleanup"
        setsid "$cleanup" >/dev/null 2>&1 &
    fi
    log_success "$(t uninstall_done)"
    return 0
}

# ── Диспетчер ───────────────────────────────────────────────────────────
bot_actions_main() {
    local action="${1:-}" rc=0
    shift || true
    case "$action" in
        panel)                  bot_act_panel "$@" || rc=$? ;;
        stats)                  bot_act_stats "$@" || rc=$? ;;
        cert_info)              bot_act_cert_info "$@" || rc=$? ;;
        cert_hook)              bot_act_cert_hook "$@" || rc=$? ;;
        upstream_report)        bot_act_upstream_report "$@" || rc=$? ;;
        measure)                bot_act_measure "$@" || rc=$? ;;
        policy_set)             bot_act_policy_set "$@" || rc=$? ;;
        ports_info)             bot_act_ports_info "$@" || rc=$? ;;
        rotate_path)            bot_act_rotate_path "$@" || rc=$? ;;
        dot_on)                 bot_act_dot_on "$@" || rc=$? ;;
        dot_off)                bot_act_dot_off "$@" || rc=$? ;;
        test_quick)             bot_act_test_quick "$@" || rc=$? ;;
        test_full)              bot_act_test_full "$@" || rc=$? ;;
        test_external)          bot_act_test_external "$@" || rc=$? ;;
        test_leak)              bot_act_test_leak "$@" || rc=$? ;;
        snippets)               bot_act_snippets "$@" || rc=$? ;;
        svc_status)             bot_act_svc_status "$@" || rc=$? ;;
        svc_start)              bot_act_svc_start "$@" || rc=$? ;;
        svc_stop)               bot_act_svc_stop "$@" || rc=$? ;;
        svc_apply)              bot_act_svc_apply "$@" || rc=$? ;;
        svc_logs)               bot_act_svc_logs "$@" || rc=$? ;;
        svc_showconf)           bot_act_svc_showconf "$@" || rc=$? ;;
        reload_certs)           bot_act_reload_certs "$@" || rc=$? ;;
        qlog_state)             bot_act_qlog_state "$@" || rc=$? ;;
        qlog_on)                bot_act_qlog_on "$@" || rc=$? ;;
        qlog_off)               bot_act_qlog_off "$@" || rc=$? ;;
        logs_report)            bot_act_logs_report "$@" || rc=$? ;;
        phone_qr)               bot_act_phone_qr "$@" || rc=$? ;;
        backup_create)          bot_act_backup_create "$@" || rc=$? ;;
        backup_list)            bot_act_backup_list "$@" || rc=$? ;;
        backup_schedule_status) bot_act_backup_schedule_status "$@" || rc=$? ;;
        backup_schedule_set)    bot_act_backup_schedule_set "$@" || rc=$? ;;
        custom_list)            bot_act_custom_list "$@" || rc=$? ;;
        custom_probe)           bot_act_custom_probe "$@" || rc=$? ;;
        custom_add)             bot_act_custom_add "$@" || rc=$? ;;
        custom_remove)          bot_act_custom_remove "$@" || rc=$? ;;
        uninstall)              bot_act_uninstall "$@" || rc=$? ;;
        *)
            echo "unknown action: ${action}" >&2
            rc=2
            ;;
    esac
    return "$rc"
}

bot_actions_main "$@"
