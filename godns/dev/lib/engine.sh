#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — Диспетчер движков (engine dispatcher).
#
# ── Зачем этот слой ─────────────────────────────────────────────────────
# godns.sh НЕ должен знать, что под капотом dnsdist. Он ходит только сюда.
# Если в godns.sh появляется `if [ "$GODNS_ENGINE" = "dnsdist" ]` — это
# признак того, что не хватает ВОЗМОЖНОСТИ (capability), а не повод писать
# условие. Ровно тот же урок, что был выучен на CDN-провайдерах в gocdn.
#
# ── Контракт движка ─────────────────────────────────────────────────────
# Файл lib/engines/<name>.sh обязан определить функции с префиксом <name>_:
#     <name>_engine_init        — объявляет возможности (engine_cap_set)
#     <name>_name               — человекочитаемое имя, stdout
#     <name>_install            — поставить движок (идемпотентно)
#     <name>_render_config <f>  — отрендерить конфиг в файл <f>
#     <name>_apply              — render + проверка + установка + рестарт
#     <name>_reload             — перечитать конфигурацию
#     <name>_reload_certs       — подхватить новые сертификаты
#     <name>_status             — отчёт для оператора (stdout)
#     <name>_is_running         — rc0 = служба работает
#     <name>_upstream_report    — кто из апстримов что обслужил (stdout)
#
# Необязательные, под возможности can_live_monitor / can_query_log:
#     <name>_live_monitor       — экран живого наблюдения (сам рисует и ждёт)
#     <name>_logs_follow        — хвост лога в реальном времени
#     <name>_query_log_enable   — включить подробный лог запросов
#     <name>_query_log_disable  — выключить его
#     <name>_query_log_state    — on|off|unknown, stdout
#     <name>_stats_snapshot     — накопительные счётчики одной строкой:
#                                 «метка_старта запросы из_кэша servfail»;
#                                 rc≠0 = служба стоит или консоли нет
#
# ── Правила проекта, которые здесь соблюдены ────────────────────────────
#   • все log_* пишут в stderr; на stdout — только полезные значения;
#   • ни одного присваивания из подстановки, которая штатно возвращает 1,
#     без `|| true`;
#   • ни одна функция не заканчивается на `[ … ] && …`.
#
# Требует: lib/common.sh и lib/i18n.sh (в таком порядке) до себя.

# ── i18n: мягкая подстраховка ───────────────────────────────────────────
# Если lib/i18n.sh ещё не загружен, определяем заглушки, чтобы библиотека
# не падала с «t: command not found». Настоящий i18n.sh определяет t()/tf()
# безусловно и просто перекроет заглушки, в каком бы порядке ни грузили.
if ! declare -F t >/dev/null 2>&1; then
    t()  { printf '%s' "$1"; }
    tf() {
        local _k="$1"; shift
        printf '%s' "$_k"
        if [ "$#" -gt 0 ]; then printf ' [%s]' "$*"; fi
        return 0
    }
fi

# ── Где лежат реализации ────────────────────────────────────────────────
if [ -z "${GODNS_LIB_DIR:-}" ]; then
    GODNS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || GODNS_LIB_DIR="/opt/godns/lib"
fi
GODNS_ENGINE_DEFAULT="dnsdist"
GODNS_ENGINE="${GODNS_ENGINE:-}"

# ── Хранилище возможностей ──────────────────────────────────────────────
# Штатное хранилище — ассоциативный массив (bash 4.0+, как и весь проект).
# Плоский запасной путь нужен ровно для одного: гонять чистую логику
# рендера конфига на bash 3.2 (macOS) в тестах, без контейнера.
if [ "${BASH_VERSINFO[0]:-0}" -ge 4 ]; then
    declare -gA ENGINE_CAP 2>/dev/null || declare -A ENGINE_CAP
    ENGINE_CAP_ASSOC=1
else
    ENGINE_CAP_ASSOC=0
fi

# Канонический список. Всё, чего здесь нет, — опечатка, и engine_cap
# обязана об этом сказать, а не молча вернуть «no».
ENGINE_CAP_NAMES="can_enforce_doh_path can_true_roundrobin can_reload_certs_live can_doq has_console needs_third_party_repo can_live_monitor can_query_log"

engine_cap_known() {
    local _n="$1" _k
    for _k in $ENGINE_CAP_NAMES; do
        if [ "$_k" = "$_n" ]; then return 0; fi
    done
    return 1
}

engine_cap_reset() {
    local _k
    if [ "$ENGINE_CAP_ASSOC" = "1" ]; then
        ENGINE_CAP=()
    else
        for _k in $ENGINE_CAP_NAMES; do
            unset "ENGINE_CAP__${_k}" 2>/dev/null || true
        done
    fi
    return 0
}

# engine_cap_set <name> <yes|no>
engine_cap_set() {
    local _n="${1:-}" _v="${2:-no}"
    if ! engine_cap_known "$_n"; then
        log_warning "$(tf eng_cap_unknown "$_n")"
        return 1
    fi
    case "$_v" in
        yes|no) : ;;
        true|1) _v="yes" ;;
        false|0) _v="no" ;;
        *) log_warning "$(tf eng_cap_bad_value "$_n" "$_v")"; _v="no" ;;
    esac
    if [ "$ENGINE_CAP_ASSOC" = "1" ]; then
        ENGINE_CAP["$_n"]="$_v"
    else
        eval "ENGINE_CAP__${_n}=\"\$_v\""
    fi
    return 0
}

# engine_cap <name>
#   печатает "yes"/"no" в stdout; rc 0 = yes, rc 1 = no/неизвестно.
#   Оба способа применения корректны:
#       if engine_cap can_doq >/dev/null; then …
#       v=$(engine_cap has_console) || true
engine_cap() {
    local _n="${1:-}" _v=""
    if ! engine_cap_known "$_n"; then
        log_warning "$(tf eng_cap_unknown "$_n")"
        printf 'no'
        return 1
    fi
    if [ "$ENGINE_CAP_ASSOC" = "1" ]; then
        _v="${ENGINE_CAP[$_n]:-no}"
    else
        eval "_v=\"\${ENGINE_CAP__${_n}:-no}\""
    fi
    printf '%s' "$_v"
    if [ "$_v" = "yes" ]; then return 0; fi
    return 1
}

# Все возможности разом: "name=value" построчно, stdout. Для status и тестов.
engine_cap_list() {
    local _k _v
    for _k in $ENGINE_CAP_NAMES; do
        _v=$(engine_cap "$_k" 2>/dev/null) || true
        printf '%s=%s\n' "$_k" "${_v:-no}"
    done
    return 0
}

# ── Загрузка реализации ─────────────────────────────────────────────────
engine_load() {
    local name="${1:-}"
    [ -n "$name" ] || name="${GODNS_ENGINE:-$GODNS_ENGINE_DEFAULT}"

    case "$name" in
        ''|*[!a-z0-9_]*)
            log_error "$(tf eng_bad_name "$name")"
            return 1
            ;;
    esac

    local f="${GODNS_LIB_DIR}/engines/${name}.sh"
    if [ ! -r "$f" ]; then
        log_error "$(tf eng_impl_missing "$name" "$f")"
        return 1
    fi

    # shellcheck source=/dev/null
    . "$f" || { log_error "$(tf eng_impl_load_failed "$name")"; return 1; }

    GODNS_ENGINE="$name"
    engine_cap_reset

    if declare -F "${name}_engine_init" >/dev/null 2>&1; then
        "${name}_engine_init" || log_warning "$(tf eng_init_failed "$name")"
    fi
    return 0
}

engine_loaded() {
    if [ -n "${GODNS_ENGINE:-}" ] && declare -F "${GODNS_ENGINE}_name" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

# ── Диспетчер ───────────────────────────────────────────────────────────
# Единственное место, где имя движка склеивается с именем функции.
engine_dispatch() {
    local _fn="${1:-}"; shift || true
    if [ -z "${GODNS_ENGINE:-}" ]; then
        log_error "$(t eng_not_loaded)"
        return 127
    fi
    local _impl="${GODNS_ENGINE}_${_fn}"
    if ! declare -F "$_impl" >/dev/null 2>&1; then
        log_error "$(tf eng_no_impl "$GODNS_ENGINE" "$_fn")"
        return 127
    fi
    "$_impl" "$@"
}

# ═══════════════════════════════════════════════════════════════════════
# КАНОНИЧЕСКАЯ ЗАПИСЬ АПСТРИМА
# ═══════════════════════════════════════════════════════════════════════
# Формат, который понимает ЛЮБОЙ движок goDNS (ключ конфига `upstreams`,
# записи через ';' или перевод строки):
#
#       id|proto|host|port|sni|path
#
#   proto ∈ dot | doh
#   host  — ЛИТЕРАЛЬНЫЙ IP. Апстрим прибивается к адресу, а доверие даёт
#           sni (subjectName + проверка сертификата). Так резолверу не нужен
#           открытый :53 «на разогрев», чтобы узнать адрес своего же
#           апстрима — курица и яйцо решаются без plaintext-запроса.
#   port  — пусто = 853 для dot, 443 для doh
#   sni   — имя из сертификата апстрима
#   path  — только для doh, пусто = /dns-query
#
# engine_upstream_record собирает такую запись из каталога lib/providers.sh,
# чтобы мастеру настройки не пришлось знать формат наизусть.
#
# Берём ПЕРВЫЙ адрес провайдера, а не все: одна запись = один провайдер,
# иначе «по кругу» начнёт крутиться между двумя IP одного и того же
# Cloudflare вместо чередования провайдеров.
#
# engine_upstream_record <provider_id> <dot|doh> [4|6]
engine_upstream_record() {
    local id="${1:-}" proto="${2:-dot}" ipver="${3:-4}"
    local host="" port="" sni="" path="" url="" addrs=""

    if ! declare -F prov_field >/dev/null 2>&1; then
        log_error "$(t eng_prov_module_missing)"
        return 1
    fi
    if [ -z "$id" ]; then
        log_error "$(t eng_prov_no_id)"
        return 1
    fi

    if [ "$ipver" = "6" ]; then
        addrs=$(prov_field "$id" ipv6) || addrs=""
    else
        addrs=$(prov_field "$id" ipv4) || addrs=""
        # Свой апстрим может быть задан одним лишь IPv6-адресом.
        [ -n "$addrs" ] || addrs=$(prov_field "$id" ipv6) || addrs=""
    fi
    host="${addrs%%,*}"
    # Пробелы вокруг адреса в каталоге — не повод молча собрать битую запись.
    host="${host#"${host%%[![:space:]]*}"}"
    host="${host%"${host##*[![:space:]]}"}"
    if [ -z "$host" ] || [ "$host" = "-" ] || [ "$host" = "none" ]; then
        log_warning "$(tf eng_prov_no_addr "$id" "$ipver")"
        return 1
    fi

    case "$proto" in
        dot)
            sni=$(prov_dot_hostname "$id") || sni=""
            port=$(prov_field "$id" dot_port) || port=""
            [ -n "$port" ] || port="853"
            path=""
            ;;
        plain)
            # Открытый DNS — только явно добавленный оператором: у каталога
            # такого протокола нет по построению. Ни SNI, ни пути.
            port=$(prov_field "$id" dot_port) || port=""
            [ -n "$port" ] || port="53"
            sni=""; path=""
            ;;
        doh)
            url=$(prov_upstream_doh "$id") || url=""
            if [ -z "$url" ]; then
                log_warning "$(tf eng_prov_no_doh "$id")"
                return 1
            fi
            # https://host[:port]/path — разбираем без внешних утилит.
            url="${url#https://}"
            url="${url#http://}"
            path="/${url#*/}"
            case "$url" in */*) : ;; *) path="/dns-query" ;; esac
            sni="${url%%/*}"
            case "$sni" in
                *:*) port="${sni##*:}"; sni="${sni%%:*}" ;;
                *)   port="443" ;;
            esac
            ;;
        *)
            log_error "$(tf eng_prov_bad_proto "$proto")"
            return 1
            ;;
    esac

    if [ -z "$sni" ] && [ "$proto" != "plain" ]; then
        log_warning "$(tf eng_prov_no_sni "$id")"
        return 1
    fi

    # Фигурная скобка в имени или пути — не адрес, а незамещённый шаблон.
    # Провайдеров с шаблонами в таблице больше нет (ушли вместе с NextDNS),
    # но страж остаётся: запись каталога с опечаткой не должна доехать до
    # конфига dnsdist и уронить службу «недопустимым именем сертификата».
    case "${sni}${path}" in
        *'{'*|*'}'*)
            log_warning "$(tf eng_prov_bad_template "$id")"
            return 1
            ;;
    esac

    printf '%s|%s|%s|%s|%s|%s' "$id" "$proto" "$host" "$port" "$sni" "$path"
    return 0
}

engine_name()            { engine_dispatch name            "$@"; }
engine_install()         { engine_dispatch install         "$@"; }
engine_render_config()   { engine_dispatch render_config   "$@"; }
# Применение — под межпроцессным замком. Демон бота проводит мутации по
# одной через свой asyncio-лок, но панель и SSH-меню этого лока не видят:
# два одновременных engine_apply — это два перезапуска dnsdist друг под
# другом и порванный applied_fingerprint. Замок здесь, в диспетчере, —
# чтобы под ним оказались ВСЕ три пути сразу.
#
# Подоболочка не мешает: единственный глобал dnsdist_apply
# (DNSDIST_CHECK_OUTPUT) печатается внутри самого apply и снаружи не
# читается — проверено грепом. flock есть всюду (util-linux); если его
# вдруг нет, применяем без замка — отказ применять хуже гонки.
engine_apply() {
    local _lock="${GODNS_DIR:-/opt/godns}/.apply.lock"
    if ! command -v flock >/dev/null 2>&1; then
        engine_dispatch apply "$@"
        return $?
    fi
    (
        if ! flock -w 180 9; then
            log_error "$(t eng_apply_locked)"
            exit 75
        fi
        engine_dispatch apply "$@"
    ) 9>>"$_lock"
}
engine_reload()          { engine_dispatch reload          "$@"; }
engine_reload_certs()    { engine_dispatch reload_certs    "$@"; }
engine_status()          { engine_dispatch status          "$@"; }
engine_is_running()      { engine_dispatch is_running      "$@"; }
engine_upstream_report() { engine_dispatch upstream_report "$@"; }

# ═══════════════════════════════════════════════════════════════════════
# ОСТАЛЬНАЯ ЧАСТЬ ДИСПЕТЧЕРА
# ═══════════════════════════════════════════════════════════════════════
# Эти обёртки вызывает godns.sh. Держим их здесь, а не в godns.sh, чтобы
# меню ни при каких условиях не обращалось к <движок>_* напрямую: как только
# в godns.sh появляется `if [ "$GODNS_ENGINE" = dnsdist ]`, это сигнал, что
# не хватает возможности (engine_cap) или обёртки — а не повод ветвиться.
engine_is_installed()  { engine_dispatch is_installed  "$@"; }
engine_service_user()  { engine_dispatch service_user  "$@"; }
engine_ratelimit_qps() { engine_dispatch ratelimit_qps "$@"; }
engine_config_is_current() { engine_dispatch config_is_current "$@"; }
engine_logs()          { engine_dispatch logs          "$@"; }
engine_show_config()   { engine_dispatch show_config   "$@"; }
engine_uninstall()     { engine_dispatch uninstall     "$@"; }
engine_cache_flush()   { engine_dispatch cache_flush   "$@"; }

# ── Наблюдение ──────────────────────────────────────────────────────────
# Живой экран и подробный лог запросов — РАЗНЫЕ возможности, и разводить
# их по разным флагам не педантизм: у dnsdist экран работает всегда (кольца
# включены по умолчанию), а подробный лог существует только пока движок
# умеет переключать многословность на лету. Движок, у которого есть одно
# и нет другого, обязан иметь возможность так и сказать.
engine_live_monitor()      { engine_dispatch live_monitor      "$@"; }
engine_logs_follow()       { engine_dispatch logs_follow       "$@"; }
engine_query_log_enable()  { engine_dispatch query_log_enable  "$@"; }
engine_query_log_disable() { engine_dispatch query_log_disable "$@"; }
engine_query_log_state()   { engine_dispatch query_log_state   "$@"; }
# Срез накопительных счётчиков для lib/stats.sh — один вызов консоли.
engine_stats_snapshot()    { engine_dispatch stats_snapshot    "$@"; }

# Синоним: в меню читается лучше «выбрать движок», в библиотеке — «загрузить».
engine_select() { engine_load "$@"; }

# ── Канонические записи апстримов ───────────────────────────────────────
# ГРАБЛИ, дважды: движок рендерит ТОЛЬКО из канонической строки
# `id|proto|host|port|sni|path`, а не из списка идентификаторов. Сначала в
# конфиг клали «cloudflare,google,…» — dnsdist поднимался без единого
# апстрима. Потом мастер в SSH научился собирать записи, а веб-панель нет:
# она меняла upstream_ids, рапортовала «применено», и резолвер продолжал
# ходить в СТАРЫЙ набор. Оператор видел один набор на экране и другой в
# работе, и понять это по логам было нечем.
#
# Поэтому сборка здесь одна на всех. Пишет обе величины сразу: список для
# показа и записи для движка — разъехаться им больше негде.
#
# stdout: пропущенные идентификаторы через запятую (может быть пусто).
# rc 1 — не осталось ни одной годной записи, состояние НЕ тронуто.
engine_upstreams_store() {
    local ids="$1" recs="" rec="" skipped="" kept="" id=""
    [ -n "$ids" ] || return 1
    local proto=""
    for id in ${ids//,/ }; do
        # Протокол — у записи, а не «dot» для всех. Каталог по-прежнему
        # ходит DoT (он есть у всех двадцати записей и им же меряется), а
        # свой апстрим приносит свой: dot, doh или plain.
        proto=$(prov_field "$id" proto 2>/dev/null) || proto=""
        [ -n "$proto" ] || proto="dot"
        rec=$(engine_upstream_record "$id" "$proto") || rec=""
        if [ -z "$rec" ]; then
            skipped="${skipped}${skipped:+, }${id}"
            continue
        fi
        recs="${recs}${recs:+;}${rec}"
        kept="${kept}${kept:+,}${id}"
    done
    [ -n "$recs" ] || return 1
    # ГРАБЛИ: сюда писался СЫРОЙ список — вместе с теми, кого пришлось
    # отбросить (например NextDNS без ID профиля). Записи движка при этом
    # содержали только годных, и получалось два разных набора: панель
    # показывала апстрим выбранным, а dnsdist его не знал. Хуже, что
    # расхождение само себя поддерживало: панель подставляла сохранённый
    # список в следующее «Применить», и отброшенный возвращался снова.
    # В конфиг идёт только то, что ДЕЙСТВИТЕЛЬНО применено.
    config_set upstream_ids "$kept" || return 1
    config_set upstreams    "$recs" || return 1
    printf '%s' "$skipped"
    return 0
}
