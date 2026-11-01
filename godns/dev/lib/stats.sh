#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — счётчик запросов: минутные срезы накопительных счётчиков движка,
# окна 1м/15м/1ч/1д/7д, строки для главной панели и подробный экран.
#
# ── Устройство ──────────────────────────────────────────────────────────
# dnsdist отдаёт через консоль ТОЛЬКО накопительные счётчики (dumpStats),
# и один вызов консоли стоит сотни миллисекунд. Поэтому:
#   • раз в минуту systemd-таймер снимает ОДИН срез (godns_stats_sample —
#     один вызов консоли) и дописывает его в /var/lib/godns/stats.tsv;
#   • панель и экран читают ТОЛЬКО файл — консоль при отрисовке не
#     дёргается вообще.
#
# Файл — состояние, а не лог: не в /var/log (там чужая ротация) и не в
# /run (tmpfs не переживёт перезагрузку). Размер фиксирован навсегда:
# поминутные срезы за сутки (≤1441 строк) + часовые свёртки за неделю
# (≤169 строк); свёртка происходит при добавлении среза. Вся арифметика,
# включая три ловушки (обнуление счётчиков при перезапуске службы, дыра
# от перезагрузки сервера, свёртка) — в lib/stats.py: python3 есть на всех
# целевых образах, а окна и корзины на bash — поле мин.
#
# Требует: lib/common.sh — но НЕ обязательно. Файл сознательно
# самодостаточен: systemd-юнит сборщика делает `. lib/stats.sh` и зовёт
# godns_stats_sample напрямую; недостающее тот догружает сам.

# ── i18n и логи: мягкая подстраховка ────────────────────────────────────
# Ровно тот же приём, что в lib/logs.sh, и по той же причине: юнит
# сборщика грузит только этот файл, а его сообщения об ошибках попадают
# в journald и читаются человеком — голый ключ вместо фразы там был бы
# ровно тем классом ошибки, который ловит check-str-usage.
if ! declare -F log_warning >/dev/null 2>&1; then
    log_info()    { echo "  $*" >&2; }
    log_success() { echo "  $*" >&2; }
    log_warning() { echo "  $*" >&2; }
    log_error()   { echo "  $*" >&2; }
    log_dim()     { echo "  $*" >&2; }
    log_step()    { echo "  $*" >&2; }
fi
if ! declare -F t >/dev/null 2>&1; then
    _stats_lang_file() {
        local d lang="ru"
        d=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || d=""
        [ -n "$d" ] || return 1
        if [ -r "${GODNS_DIR:-/opt/godns}/.language" ]; then
            lang=$(cat "${GODNS_DIR:-/opt/godns}/.language" 2>/dev/null) || lang="ru"
        fi
        case "$lang" in en) : ;; *) lang="ru" ;; esac
        [ -r "${d}/lang/${lang}.sh" ] || return 1
        printf '%s' "${d}/lang/${lang}.sh"
        return 0
    }
    GODNS_STATS_LANG_FILE=$(_stats_lang_file) || GODNS_STATS_LANG_FILE=""

    t() {
        local _v=""
        if [ -n "${GODNS_STATS_LANG_FILE:-}" ]; then
            _v=$(sed -n "s/^I18N\[$1\]=\"\(.*\)\"\$/\1/p" "$GODNS_STATS_LANG_FILE" 2>/dev/null) || _v=""
            _v="${_v%%$'\n'*}"
        fi
        if [ -n "$_v" ]; then printf '%s' "$_v"; else printf '%s' "$1"; fi
        return 0
    }
    tf() {
        local _k="$1" _fmt=""; shift
        _fmt=$(t "$_k")
        case "$_fmt" in
            *%*) printf "$_fmt" "$@" ;;
            *)   printf '%s' "$_fmt"
                 if [ "$#" -gt 0 ]; then printf ' [%s]' "$*"; fi ;;
        esac
        return 0
    }
fi

# ── Пути. Всё наше, чужого здесь нет ────────────────────────────────────
GODNS_STATS_DIR="${GODNS_STATS_DIR:-/var/lib/godns}"
GODNS_STATS_FILE="${GODNS_STATS_FILE:-${GODNS_STATS_DIR}/stats.tsv}"
GODNS_STATS_UNIT="${GODNS_STATS_UNIT:-godns-stats}"
GODNS_STATS_UNIT_DIR="${GODNS_STATS_UNIT_DIR:-/etc/systemd/system}"
GODNS_STATS_TMPFILES="${GODNS_STATS_TMPFILES:-/etc/tmpfiles.d/godns-stats.conf}"

# Арифметика — в python3 (см. шапку). Путь разрешаем как DNSDIST_MON_PY.
if [ -z "${GODNS_STATS_PY:-}" ]; then
    if [ -n "${GODNS_LIB_DIR:-}" ] && [ -r "${GODNS_LIB_DIR}/stats.py" ]; then
        GODNS_STATS_PY="${GODNS_LIB_DIR}/stats.py"
    else
        GODNS_STATS_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/stats.py"
    fi
fi

# Тестовый крюк: «сейчас» для окон. Пустое значение = настоящее время.
# GODNS_STATS_NOW выставляют только тесты — арифметику окон иначе не
# проверить без суток ожидания.
_stats_now_arg() {
    if [ -n "${GODNS_STATS_NOW:-}" ]; then
        printf -- '--now %s' "$GODNS_STATS_NOW"
    fi
    return 0
}

# Записать файл, только если содержимое изменилось (см. _logs_put — без
# сравнения каждый запуск дёргал бы daemon-reload на ровном месте).
# rc 0 — записано, rc 1 — совпало, rc 2 — не смогли записать.
_stats_put() {   # <файл> <текст>
    local f="$1" want="$2" have=""
    if [ -f "$f" ]; then have=$(cat "$f" 2>/dev/null) || have=""; fi
    if [ "$have" = "$want" ]; then return 1; fi
    local d; d=$(dirname "$f")
    mkdir -p "$d" 2>/dev/null || true
    printf '%s\n' "$want" > "$f" 2>/dev/null || return 2
    chmod 0644 "$f" 2>/dev/null || true
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СБОР
# ═══════════════════════════════════════════════════════════════════════

# Чистая половина среза: принять сырые значения и дописать в файл.
# Все три ловушки (перезапуск, дыра, свёртка) обрабатывает lib/stats.py —
# поэтому эта функция проверяется в test_common.sh без root и без systemd.
# godns_stats_ingest <epoch> <метка старта службы|-> <queries> <hits> <fails>
godns_stats_ingest() {
    local ts="${1:-}" mark="${2:--}" q="${3:-}" h="${4:-0}" f="${5:-0}"
    case "$ts" in ''|*[!0-9]*) return 1 ;; esac
    case "$q"  in ''|*[!0-9]*) return 1 ;; esac
    case "$h"  in ''|*[!0-9]*) h=0 ;; esac
    case "$f"  in ''|*[!0-9]*) f=0 ;; esac
    mkdir -p "$(dirname "$GODNS_STATS_FILE")" 2>/dev/null || true
    printf '%s %s %s %s %s\n' "$ts" "$mark" "$q" "$h" "$f" \
        | python3 "$GODNS_STATS_PY" ingest "$GODNS_STATS_FILE" || return 1
    return 0
}

# Снять срез с работающего движка. Идемпотентно: повторный вызов в ту же
# минуту сливается с уже записанным срезом, а не плодит строки.
#
# Зовётся двумя способами: из godns.sh (всё загружено) и из systemd-юнита,
# который сорсит ТОЛЬКО этот файл, — тогда недостающее догружаем сами.
# «Служба не работает» — не ошибка, а штатная пауза в сборе: юнит обязан
# выйти нулём, иначе systemd копил бы failed-юниты на каждой минуте
# простоя.
godns_stats_sample() {
    local here=""
    if ! declare -F engine_dispatch >/dev/null 2>&1; then
        here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || return 1
        # shellcheck source=/dev/null
        . "${here}/common.sh" || return 1
        # shellcheck source=/dev/null
        . "${here}/engine.sh" || return 1
    fi
    if ! engine_loaded; then
        local eng=""; eng=$(config_get engine dnsdist) || eng="dnsdist"
        engine_load "$eng" 2>/dev/null || return 1
    fi

    local snap=""
    snap=$(engine_stats_snapshot 2>/dev/null) || return 0
    [ -n "$snap" ] || return 0

    local ts=""; ts=$(date +%s) || return 1
    # snap: «метка_старта запросы из_кэша servfail» одной строкой.
    # shellcheck disable=SC2086
    set -- $snap
    [ "$#" -ge 4 ] || return 0
    if ! godns_stats_ingest "$ts" "$1" "$2" "$3" "$4"; then
        log_warning "$(tf stats_write_failed "$GODNS_STATS_FILE")"
        return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ОКНА И СТРОКИ
# ═══════════════════════════════════════════════════════════════════════

# Число запросов за окно, голым числом в stdout.
# rc 0 — есть число; rc 1 — данных на это окно нет (печатает пусто);
# rc 2 — такое окно неизвестно. Ноль и «нет данных» — РАЗНЫЕ ответы:
# ноль означает «запросов не было», и врать его вместо прочерка нельзя.
godns_stats_window() {
    local w="${1:-}"
    case "$w" in 1m|15m|1h|1d|7d) : ;; *) return 2 ;; esac
    [ -f "$GODNS_STATS_FILE" ] || return 1
    # shellcheck disable=SC2046
    python3 "$GODNS_STATS_PY" window "$GODNS_STATS_FILE" "$w" $(_stats_now_arg) \
        2>/dev/null || return 1
    return 0
}

# Спарклайн: N знаков по K минутных срезов каждый (▁▂▃▄▅▆▇█, '·' — слот
# без данных). rc 1 — срезов нет вовсе.
godns_stats_sparkline() {
    local n="${1:-30}" k="${2:-1}"
    case "$n" in ''|*[!0-9]*) n=30 ;; esac
    case "$k" in ''|*[!0-9]*) k=1 ;; esac
    [ -f "$GODNS_STATS_FILE" ] || return 1
    # shellcheck disable=SC2046
    python3 "$GODNS_STATS_PY" spark "$GODNS_STATS_FILE" "$n" "$k" $(_stats_now_arg) \
        2>/dev/null || return 1
    return 0
}

# Весь отчёт одним вызовом python3 — панель дёргает его раз в 10 секунд,
# и два-три отдельных запуска на кадр были бы тратой на ровном месте.
_stats_report() {
    # shellcheck disable=SC2046
    python3 "$GODNS_STATS_PY" report "$GODNS_STATS_FILE" $(_stats_now_arg) \
        2>/dev/null || return 1
    return 0
}

# Готовые строки для главной панели: ровно ДВЕ, обе уже с цветами.
#   строка 1 — окна:      Запросы  1м 12 · 15м 384 · 1ч 1.2k · 1д 8.4k · 7д 51k
#   строка 2 — спарклайн: За час   ▁▂▃▄▅▆▇█… (30 знаков × 2 мин)  пик N/мин
# «≈» перед числом — окно неполное: внутри разрыв (служба или сервер
# перезапускались) либо данных меньше, чем глубина окна. «—» — данных нет
# вовсе; ноль вместо прочерка не рисуем никогда: ноль значит «запросов не
# было», а это враньё.
# rc 1 — файла срезов нет (панель тогда не показывает блок вообще).
# i18n-dynamic: stats_w_1m stats_w_15m stats_w_1h stats_w_1d stats_w_7d
godns_stats_line() {
    [ -f "$GODNS_STATS_FILE" ] || return 1
    local rep=""
    rep=$(_stats_report) || return 1
    [ -n "$rep" ] || return 1

    local l1="" sep="" seg="" spark="-" peak="-"
    local tag f2 f3 f4 f5 f6 f7 f8
    while IFS=$'\t' read -r tag f2 f3 f4 f5 f6 f7 f8; do
        case "$tag" in
            w)
                seg="${DIM}$(t "stats_w_${f2}")${NC} "
                case "$f5" in
                    none)   seg="${seg}${DIM}—${NC}" ;;
                    approx) seg="${seg}${YELLOW}≈${NC}${WHITE}${f4}${NC}" ;;
                    *)      seg="${seg}${WHITE}${f4}${NC}" ;;
                esac
                l1="${l1}${sep}${seg}"
                sep=" ${DIM}·${NC} "
                ;;
            spark)
                spark="$f2"; peak="$f3"
                ;;
        esac
    done <<EOF
$rep
EOF

    local w="${GODNS_LBL_W:-12}" l2=""
    l1="  $(pad_label "$(t stats_dash_label):" "$w")${l1}"
    if [ -z "$spark" ] || [ "$spark" = "-" ]; then
        l2="  $(pad_label "$(t stats_dash_hour):" "$w")${DIM}$(t stats_dash_waiting)${NC}"
    else
        l2="  $(pad_label "$(t stats_dash_hour):" "$w")${CYAN}${spark}${NC}"
        if [ -n "$peak" ] && [ "$peak" != "-" ]; then
            l2="${l2}  ${DIM}$(tf stats_dash_peak "$peak")${NC}"
        fi
    fi
    printf '%b\n%b\n' "$l1" "$l2"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ПОДРОБНЫЙ ЭКРАН
# ═══════════════════════════════════════════════════════════════════════
_stats_row() {   # <окно> <запросов> <в сек> <из кэша> <servfail> [цвет значений]
    local c="${6:-$WHITE}"
    printf '  %s%b%s%b %b%s%b %b%s%b %b%s%b\n' \
        "$(pad_label "$1" 11)" \
        "$c"    "$(pad_label "$2" 10)" "$NC" \
        "$DIM"  "$(pad_label "$3" 10)" "$NC" \
        "$DIM"  "$(pad_label "$4" 9)"  "$NC" \
        "$DIM"  "$5" "$NC"
    return 0
}

# i18n-dynamic: stats_scr_1m stats_scr_15m stats_scr_1h stats_scr_1d stats_scr_7d
godns_stats_screen() {
    print_header "$(t stats_screen_title)"

    if [ ! -f "$GODNS_STATS_FILE" ]; then
        log_info "$(t stats_no_data_yet)"
        if ! godns_stats_timer_active; then
            log_warning "$(tf stats_timer_off "${GODNS_STATS_UNIT}.timer")"
            if confirm "$(t stats_install_ask)" y; then
                godns_stats_install || return 1
                log_info "$(t stats_first_slice_hint)"
            fi
        else
            log_dim "$(t stats_first_slice_hint)"
        fi
        return 0
    fi

    local rep=""
    rep=$(_stats_report) || { log_error "$(t stats_read_failed)"; return 1; }

    local tag f2 f3 f4 f5 f6 f7 f8
    local spark24="-" peak24="-" age="-" rows="0" bytes="0" seen_approx="no"
    local label="" num="" rate="" cache="" fails=""

    echo -e "  ${DIM}$(pad_label "$(t stats_col_window)" 11)$(pad_label "$(t stats_col_queries)" 10) $(pad_label "$(t stats_col_rate)" 10) $(pad_label "$(t stats_col_cache)" 9) $(t stats_col_servfail)${NC}"
    while IFS=$'\t' read -r tag f2 f3 f4 f5 f6 f7 f8; do
        case "$tag" in
            w)
                label=$(t "stats_scr_${f2}")
                case "$f5" in
                    none)
                        _stats_row "$label" "—" "—" "—" "—" "$DIM"
                        ;;
                    *)
                        num="$f3"
                        if [ "$f5" = "approx" ]; then num="≈${f3}"; seen_approx="yes"; fi
                        rate="$f6"
                        cache="—"
                        if [ "$f7" != "-" ]; then cache="${f7}%"; fi
                        fails="$f8"
                        if [ "$fails" = "-" ]; then fails="—"; fi
                        _stats_row "$label" "$num" "$rate" "$cache" "$fails"
                        ;;
                esac
                ;;
            spark24) spark24="$f2"; peak24="$f3" ;;
            last)    age="$f2" ;;
            size)    rows="$f2"; bytes="$f3" ;;
        esac
    done <<EOF
$rep
EOF

    echo ""
    if [ "$spark24" != "-" ] && [ -n "$spark24" ]; then
        echo -e "  ${DIM}$(t stats_day_spark):${NC}"
        echo -e "  ${CYAN}${spark24}${NC}"
        if [ "$peak24" != "-" ] && [ -n "$peak24" ]; then
            echo -e "  ${DIM}$(tf stats_day_peak "$peak24")${NC}"
        fi
    else
        echo -e "  ${DIM}$(t stats_dash_waiting)${NC}"
    fi

    echo ""
    if [ "$age" != "-" ] && [ -n "$age" ]; then
        log_dim "$(tf stats_last_slice "$age")"
        # Срез старше двух интервалов — сборщик не ходит: скажем прямо,
        # иначе оператор будет смотреть на замершие числа как на живые.
        if [ "$age" -gt 150 ] 2>/dev/null && ! godns_stats_timer_active; then
            log_warning "$(tf stats_timer_off "${GODNS_STATS_UNIT}.timer")"
            if confirm "$(t stats_install_ask)" y; then
                godns_stats_install || true
            fi
        fi
    fi
    case "$bytes" in ''|*[!0-9]*) bytes=0 ;; esac
    log_dim "$(tf stats_file_info "$GODNS_STATS_FILE" "$rows" "$(( (bytes + 1023) / 1024 ))")"
    if [ "$seen_approx" = "yes" ]; then
        log_dim "$(t stats_legend_approx)"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# УСТАНОВКА / СНЯТИЕ
# ═══════════════════════════════════════════════════════════════════════
# Юниты рендерятся отдельными чистыми функциями — их содержимое проверяет
# test_common.sh без systemd (тот же приём, что _bk_render_units).
_stats_render_service() {
    local lib="${GODNS_INSTALLER_DIR:-/opt/godns-installer}/lib/stats.sh"
    # ConditionPathExists — чтобы полуснятый goDNS не сыпал отказами юнита
    # каждую минуту: без файла таймер просто ничего не делает.
    printf '%s' "[Unit]
Description=goDNS Pro: query counter sample
ConditionPathExists=${lib}

[Service]
Type=oneshot
ExecStart=/bin/bash -c '. ${lib} && godns_stats_sample'"
    return 0
}

_stats_render_timer() {
    # OnCalendar каждую минуту, а не OnUnitActiveSec: интервал «от прошлого
    # запуска» дрейфует на длительность самого среза (сотни миллисекунд
    # консоли), и минутные корзины постепенно съезжали бы с календарной
    # сетки. OnBootSec даёт раннюю точку отсчёта после перезагрузки.
    printf '%s' "[Unit]
Description=goDNS Pro: query counter sample every minute

[Timer]
OnBootSec=90s
OnCalendar=*-*-* *:*:00
AccuracySec=15s

[Install]
WantedBy=timers.target"
    return 0
}

godns_stats_timer_active() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "${GODNS_STATS_UNIT}.timer" 2>/dev/null || return 1
    return 0
}

godns_stats_installed() {
    [ -f "${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.timer" ] || return 1
    return 0
}

godns_stats_install() {
    if ! command -v systemctl >/dev/null 2>&1; then
        log_warning "$(t stats_install_no_systemd)"
        return 1
    fi

    # Каталог состояния: и сразу, и через tmpfiles — так он переживает
    # и «прямо сейчас», и любую будущую чистку /var systemd-tmpfiles'ом.
    mkdir -p "$GODNS_STATS_DIR" 2>/dev/null || true
    chmod 0750 "$GODNS_STATS_DIR" 2>/dev/null || true
    local want rc=0 changed=0 f=""
    want="# goDNS Pro — статистика запросов. Файл принадлежит goDNS Pro.
d ${GODNS_STATS_DIR} 0750 root root -"
    rc=0; _stats_put "$GODNS_STATS_TMPFILES" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then
        systemd-tmpfiles --create "$GODNS_STATS_TMPFILES" >/dev/null 2>&1 || true
    fi

    f="${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.service"
    rc=0; _stats_put "$f" "$(_stats_render_service)" || rc=$?
    if [ "$rc" -eq 2 ]; then log_error "$(tf stats_install_write_fail "$f")"; return 1; fi
    if [ "$rc" -eq 0 ]; then changed=1; fi

    f="${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.timer"
    rc=0; _stats_put "$f" "$(_stats_render_timer)" || rc=$?
    if [ "$rc" -eq 2 ]; then log_error "$(tf stats_install_write_fail "$f")"; return 1; fi
    if [ "$rc" -eq 0 ]; then changed=1; fi

    if [ "$changed" -eq 1 ]; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if ! systemctl enable --now "${GODNS_STATS_UNIT}.timer" >/dev/null 2>&1; then
        log_warning "$(tf stats_install_timer_fail "${GODNS_STATS_UNIT}.timer")"
        return 1
    fi
    # Первый срез — сразу, не дожидаясь календарной минуты: он всего лишь
    # точка отсчёта, и чем раньше он снят, тем раньше вместо прочерков
    # появятся числа. --no-block: срез ходит в консоль, ждать его незачем.
    systemctl start --no-block "${GODNS_STATS_UNIT}.service" >/dev/null 2>&1 || true
    log_dim "$(tf stats_install_ok "${GODNS_STATS_UNIT}.timer" "$GODNS_STATS_FILE")"
    return 0
}

# Снимаем ТОЛЬКО юниты и tmpfiles. Файл срезов — отдельный вопрос в
# uninstall_godns: молча стирать историю запросов так же нельзя, как логи.
godns_stats_remove() {
    local f="" removed=""
    if command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "${GODNS_STATS_UNIT}.timer" >/dev/null 2>&1 || true
        # Уже запущенный таймером срез дорабатывает и пишет файл ПОСЛЕ
        # нашего rm — гасим саму службу, как это делает godns_logs_remove.
        systemctl stop "${GODNS_STATS_UNIT}.service" >/dev/null 2>&1 || true
    fi
    for f in "${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.timer" \
             "${GODNS_STATS_UNIT_DIR}/${GODNS_STATS_UNIT}.service" \
             "$GODNS_STATS_TMPFILES"; do
        if [ -e "$f" ]; then
            rm -f "$f" 2>/dev/null && removed="${removed}${f} "
        fi
    done
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if [ -n "$removed" ]; then
        log_dim "$(tf stats_remove_done "$removed")"
    fi
    return 0
}
