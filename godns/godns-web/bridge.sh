#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — мост веб-панели: server.py зовёт отсюда те же функции lib/*.sh,
# что выполняет SSH-меню. Один вызов = одно действие, всё неинтерактивно.
#
# Аргументы — только идентификаторы (порт, id провайдера, имя файла копии).
# Секретов в argv не бывает по построению: /proc/<pid>/cmdline читает любой
# локальный пользователь. Путь DoH своего апстрима (custom_probe/custom_add)
# приходит переменной окружения GODNS_CUSTOM_PATH: /proc/<pid>/environ
# читает только владелец процесса, а у чужого резолвера путь может быть
# чужим секретом.
#
# Дерево программы задаёт GODNS_PROGRAM_DIR (пишется в юнит godns-web при
# установке). Мост НЕ исполняет godns.sh — тот запускает main() при source;
# грузятся только библиотеки.
set -euo pipefail

GODNS_PROGRAM_DIR="${GODNS_PROGRAM_DIR:-/opt/godns-installer}"
LIB="${GODNS_PROGRAM_DIR}/lib"
# engine_load и stats.sh ищут файлы через GODNS_LIB_DIR — задаём до source.
GODNS_LIB_DIR="${GODNS_LIB_DIR:-$LIB}"
export GODNS_LIB_DIR

if [ ! -r "${LIB}/common.sh" ]; then
    # Двуязычно: это печатается ДО подключения lib/i18n.sh, то есть
    # таблицы строк ещё нет. Тот же приём, что у ранних стражей проекта.
    echo "bridge: нет ${LIB}/common.sh — проверьте GODNS_PROGRAM_DIR" >&2
    echo "bridge: no ${LIB}/common.sh — check GODNS_PROGRAM_DIR" >&2
    exit 3
fi

ACTION="${1:-}"
shift || true

# ── лёгкий путь: только common.sh ───────────────────────────────────────
# «drifted» дёргается панелью каждые десять секунд; полная загрузка всех
# библиотек на каждый тик — сотни миллисекунд впустую.
. "${LIB}/common.sh"

if [ "$ACTION" = "drifted" ]; then
    if godns_config_drifted; then exit 0; else exit 1; fi
fi

# Сроки: до какого числа ОПЛАЧЕН домен и до какого годен СЕРТИФИКАТ.
# Два разных срока с разными последствиями, поэтому и отдаются раздельно.
# domain_expiry_cached сети не касается: читает кэш, а протухший обновляет
# в фоне — панель не должна ждать WHOIS.
if [ "$ACTION" = "expiry" ]; then
    . "${LIB}/domain.sh"
    . "${LIB}/certs.sh"
    _d=$(config_get domain "") || _d=""
    _c=$(config_get cert_path "") || _c=""
    printf 'domain\t%s\n' "$_d"
    printf 'registered\t%s\n' "$(domain_expiry_cached "$_d" 2>/dev/null | tr '\t' ' ')"
    printf 'cert\t%s\n' "$(certs_expiry_iso "$_c" 2>/dev/null)"
    exit 0
fi

# ── полный набор — как в godns.sh, минус мастера и update ───────────────
. "${LIB}/i18n.sh"
. "${LIB}/certs.sh"
. "${LIB}/providers.sh"
. "${LIB}/bench.sh"
. "${LIB}/engine.sh"
. "${LIB}/logs.sh"
. "${LIB}/stats.sh"
. "${LIB}/qr.sh"
. "${LIB}/selftest.sh"
. "${LIB}/backup.sh"
# update.sh — ради godns_config_migrate: восстановление копии
# зовёт её через `declare -F`, и без этой строки миграция схемы
# молча не выполнялась именно здесь, а в SSH выполнялась.
. "${LIB}/update.sh"
. "${LIB}/webui.sh"
# bot.sh — ради godns_bot_installed. Восстановление копии проверяет, на
# месте ли бот, и в процессе панели этой функции не было: command not
# found под `!` читается как «не установлен», то есть панель ВСЕГДА
# сообщала «бот пропал» при развороте архива с ботом.
. "${LIB}/bot.sh"

load_language "$(detect_language)" || true
engine_select "$(config_get engine dnsdist)" || exit 3

# Мост неинтерактивен: stdin закрыт, чтобы ни один случайный read не
# завис в ожидании оператора, которого здесь нет.
exec </dev/null

# Разрешаем только известные имена файлов копий — и никогда пути.
_bridge_backup_path() {
    local name="$1"
    case "$name" in
        */*|*..*) return 1 ;;
        *.tar.gz|*.tar.gz.enc) : ;;
        *) return 1 ;;
    esac
    printf '%s/%s' "${GODNS_BACKUP_DIR:-/root/godns-backups}" "$name"
    return 0
}

rc=0
case "$ACTION" in
    # ── данные (stdout — только машинный вывод) ─────────────────────────
    catalog)          webui_catalog_tsv || rc=$? ;;
    custom_list)      webui_custom_list_tsv || rc=$? ;;
    backup_list)      webui_backup_list_tsv || rc=$? ;;
    bench)
        # bench_run_all печатает в stdout путь к JSON результатов —
        # его содержимое и есть ответ моста.
        p=$(bench_run_all) || p=""
        if [ -n "$p" ] && [ -s "$p" ]; then cat "$p"; else rc=1; fi
        ;;

    # ── действия (stdout+stderr — текст для оператора) ──────────────────
    show_config)      engine_show_config || rc=$? ;;
    logs)             engine_logs "${1:-60}" || rc=$? ;;
    upstream_report)  engine_upstream_report || rc=$? ;;
    reload_certs)     engine_reload_certs || rc=$? ;;
    service_restart)  webui_service_restart || rc=$? ;;
    cert_rehook)      webui_cert_rehook || rc=$? ;;
    rotate_path)      webui_rotate_path || rc=$? ;;
    set_ports)        webui_set_ports "${1:-}" "${2:-}" || rc=$? ;;
    set_dot)          webui_set_dot "${1:-}" || rc=$? ;;
    set_upstreams)    webui_set_upstreams "${1:-}" "${2:-}" || rc=$? ;;
    # ── свои апстримы. Путь DoH — ТОЛЬКО из окружения: в argv он был бы
    # виден любому локальному пользователю через /proc/<pid>/cmdline, а у
    # чужого резолвера путь может быть чужим секретом. Порядок argv:
    # custom_probe proto host port ack · custom_add proto host port name
    # enable ack · custom_remove id ──
    custom_probe)     webui_custom_probe  "${1:-}" "${2:-}" "${3:-}" "${GODNS_CUSTOM_PATH:-}" "${4:-no}" || rc=$? ;;
    custom_add)       webui_custom_add    "${1:-}" "${2:-}" "${3:-}" "${GODNS_CUSTOM_PATH:-}" "${4:-}" "${5:-no}" "${6:-no}" || rc=$? ;;
    custom_remove)    webui_custom_remove "${1:-}" || rc=$? ;;
    check_quick)      selftest_quick || rc=$? ;;
    check_full)       selftest_full || rc=$? ;;
    check_leak)       selftest_leak_audit || rc=$? ;;
    check_external)   webui_external_check || rc=$? ;;
    snip_routers)     selftest_snip_routers || rc=$? ;;
    snip_os)          selftest_snip_os || rc=$? ;;
    snip_browsers)    selftest_snip_browsers || rc=$? ;;
    phone_profile)    st_apple_profile_write || rc=$? ;;
    backup_create)    godns_backup_create "${1:-}" || rc=$? ;;
    backup_prune)     godns_backup_prune || rc=$? ;;
    backup_schedule_set) godns_backup_schedule_set "${1:-off}" || rc=$? ;;
    backup_restore)
        arc=$(_bridge_backup_path "${1:-}") || arc=""
        if [ -z "$arc" ] || [ ! -f "$arc" ]; then
            log_error "$(tf bk_not_found "${1:-?}")"
            rc=1
        else
            # Третий аргумент yes = без confirm: слово-подтверждение уже
            # введено в браузере. Пароль копии через argv не передаётся —
            # шифрованные копии восстанавливаются только из SSH-меню.
            godns_backup_restore "$arc" "" "yes" || rc=$?
        fi
        ;;
    *)
        echo "$(tf webui_bridge_unknown "${ACTION}")" >&2
        rc=2
        ;;
esac

# Мусор от временных файлов подчищается всегда: мост живёт секунды, но
# зовётся часто.
cleanup_temp_files || true
exit "$rc"
