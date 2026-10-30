#!/usr/bin/env bash
# lib/diagnose.sh — режимы анти-DPI (anti-DPI presets): лестница и применение.
#
# Три тира режимов (сверху вниз = от макс. защиты к макс. маскировке формы):
#   🛟 тир1 реаниматор (synlimit + таймауты, рек. @LiafanX) — макс. стабильность под DPI
#   📘 тир2 по докам (synlimit + keepalive ядра)
#   🛡 тир3 наши маск-режимы (MSS-клэмп + шейпинг формы)
# Пресет выбирается человеком в едином меню «Прокси → 1) Режим работы» (install.sh);
# мастера с автоподбором и оценками здесь нет — он снят по 12.2, чтобы перебор режимов
# не загонял сервер под временную блокировку DPI.
#
# Главный урок 2.6.3: handshake ломал не конфиг маскировки, а bbr + узкий MSS. Поэтому
# CC зависит от пресета: cubic (реаниматор/доки), bbr (MEKO, там mss=off — безопасно).
#
# Источник правды для значений конфига — generate_telemt_toml: он читает preset_id из
# config.json и через preset_params() подставляет параметры. Здесь — лестница и применение.
# Функции, вызываемые через $() (preset_params, diagnose_preset_cc), печатают в stdout
# ТОЛЬКО полезную нагрузку; любой UI/лог — в stderr.
# Все видимые человеку строки — через t()/tf() (ключи diag_*): экран смены режима зовут
# и из EN-локали, а захардкоженный русский там выглядел как чужой язык посреди меню.

# ── Лестница пресетов ────────────────────────────────────────────────────────
# Поля: id|name|mss|cc|timing_floor|timing_ceil|bucket_floor|bucket_cap|sh_delay|tls_override|desc|profile|synlimit|timeouts
#   synlimit: ВСЕГДА off. SYN-фикс живёт отдельно (меню «SYN-фикс», lib/mekofix.sh)
#             и не привязан к пресету — иначе смена пресета молча меняла бы фикс.  ·  timeouts: on|off (tg_connect+[timeouts])
#   Три тира: 🛟 реаниматор(автор) → 📘 доки → 🛡 наши маск-режимы
#   mss: off | <число-строкой: 92=MSS92/x16, 800, 1200…> | 2in8 | extreme-low
#   tls_override: keep (по режиму) | true | false
DIAG_PRESETS=(
  '1|реаним·firefox|off|cubic|20|60|1024|49152|0|keep|отпечаток Firefox — универсальный|firefox|off|on'
  '2|реаним·chrome|off|cubic|20|60|1024|49152|0|keep|отпечаток Chrome — если Firefox палится|chrome|off|on'
  '4|MEKO style|off|bbr|20|60|1024|49152|0|keep|BBR + MSS off — по умолчанию (идея @Mekotofeuka)|firefox|off|off'
)

# disable SC2034: переменную читает install.sh — внутри файла ссылок больше нет, и без пометки её однажды снесут как «неиспользуемую»
# shellcheck disable=SC2034
DIAG_DEFAULT_PRESET=4          # дефолт при чистой установке = MEKO style (SYN-фильтр + BBR)
# Ручки окна/порога/прогрева убраны вместе с мастером подбора: без него их некому читать,
# а живая переменная в файле выглядит как работающая настройка и вводит в заблуждение.

diag_preset_count() { echo "${#DIAG_PRESETS[@]}"; }
# Реальные id (нумерация НЕ сплошная: 1,2,4) — для любых переборов.
diag_preset_ids() { printf '%s\n' "${DIAG_PRESETS[@]}" | awk -F'|' '{print $1}'; }

# _diag_row <id> -> строка пресета (или код 1)
_diag_row() { printf '%s\n' "${DIAG_PRESETS[@]}" | awk -F'|' -v i="$1" '$1==i{print;f=1} END{exit !f}'; }

# preset_params <id> — ПЕЧАТАЕТ в stdout: "mss cc tf tc bf bc shd tlsov profile synlimit timeouts" (для generate_telemt_toml).
# Только данные, без UI (вызывается через $()).
preset_params() {
    local row; row="$(_diag_row "$1")" || return 1
    awk -F'|' '{print $3, $4, $5, $6, $7, $8, $9, $10, $12, $13, $14}' <<< "$row"
}

# diagnose_preset_cc — congestion control сохранённого пресета (или cubic). Для apply_network_sysctl.
diagnose_preset_cc() {
    local pid cc
    pid=$(config_get preset_id 2>/dev/null || echo "")
    [ -z "$pid" ] && pid="${TELEMT_PRESET_PREF:-}"   # чистая установка: пресет ещё не сохранён в config (как в generate_telemt_toml)
    [ -z "$pid" ] && { echo cubic; return 0; }
    cc=$(_diag_row "$pid" 2>/dev/null | awk -F'|' '{print $4}')
    [ -n "$cc" ] && echo "$cc" || echo cubic
}

# ── Применение CC (sysctl), параметризовано ──────────────────────────────────
# diagnose_set_cc <cc> — выставить congestion control. cubic/reno — глушим bbr в
# /etc/sysctl.conf (он перебивает drop-in). bbr — наоборот, не трогаем и грузим модуль.
diagnose_set_cc() {
    local cc="${1:-cubic}"
    [[ "$cc" =~ ^(cubic|reno|bbr)$ ]] || cc=cubic
    if [ "$cc" != "bbr" ]; then
        if grep -qsE '^[[:space:]]*net\.ipv4\.tcp_congestion_control[[:space:]]*=[[:space:]]*bbr' /etc/sysctl.conf 2>/dev/null; then
            sed -i.gotelegram.bak '/^[[:space:]]*net\.ipv4\.tcp_congestion_control[[:space:]]*=[[:space:]]*bbr/d' /etc/sysctl.conf 2>/dev/null || true
        fi
    fi
    local qdisc="fq_codel"; [ "$cc" = "bbr" ] && qdisc="fq"
    local f="/etc/sysctl.d/99-zz-gotelegram.conf"
    local desired="net.core.rmem_max=4194304
net.core.wmem_max=4194304
net.core.default_qdisc=${qdisc}
net.ipv4.tcp_congestion_control=${cc}"
    if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$desired" ]; then
        printf '%s\n' "$desired" > "$f" 2>/dev/null || { log_warning "$(tf diag_sysctl_write_failed "$f")" >&2; return 0; }
    fi
    modprobe "tcp_${cc}" 2>/dev/null || true
    sysctl --system >/dev/null 2>&1 || true
    sysctl -w "net.ipv4.tcp_congestion_control=${cc}" >/dev/null 2>&1 || true
    log_info "CC: ${cc} + ${qdisc}" >&2
    return 0
}

# ── Применение пресета ───────────────────────────────────────────────────────
# apply_preset <id> — сохранить preset_id, перегенерить конфиг (подхватит пресет),
# выставить CC, перезапустить telemt. Side-effect функция (не через $()); UI в stderr.
apply_preset() { gt_with_config_lock _apply_preset_locked "$@"; }
_apply_preset_locked() {
    local id="$1" row name cc
    row="$(_diag_row "$id")" || { log_error "$(tf diag_preset_not_found "$id")" >&2; return 1; }
    name=$(awk -F'|' '{print $2}' <<< "$row")
    cc=$(awk -F'|' '{print $4}' <<< "$row")
    # ── Благодарности авторам при выборе режима ──
    local _syn _tmo
    _syn=$(awk -F'|' '{print $13}' <<< "$row"); _tmo=$(awk -F'|' '{print $14}' <<< "$row")
    case "$name" in
        *MEKO*) log_info "$(t diag_thx_meko)" >&2 ;;
        *) if [ "$_syn" = "on" ] && [ "$_tmo" = "on" ]; then
               log_info "$(t diag_thx_reanim)" >&2
           elif [ "$_syn" = "on" ]; then
               log_info "$(t diag_thx_docs)" >&2
           fi ;;
    esac

    local secret port mode domain mask_port
    secret=$(get_config_value secret "$TELEMT_CONFIG" 2>/dev/null || echo "")
    [ -z "$secret" ] && secret=$(config_get secret 2>/dev/null || echo "")
    port=$(config_get port 2>/dev/null || echo 443)
    mode=$(config_get mode 2>/dev/null || echo lite)
    domain=$(config_get domain 2>/dev/null || echo "")
    if [ "$mode" = "pro" ]; then
        [ -z "$domain" ] && domain=$(config_get mask_host 2>/dev/null || echo "")
        mask_port=8443
    else
        domain=$(config_get mask_host 2>/dev/null || echo google.com)
        mask_port=443
    fi
    if [ -z "$secret" ]; then log_error "$(t diag_no_secret)" >&2; return 1; fi

    log_step "$(tf diag_applying "$id" "$name" "$cc")" >&2
    bot_update_config_field preset_id "$id" || log_warning "$(t diag_preset_not_saved)" >&2
    # users_block сохраняем (миграционный инвариант — не теряем доп. ключи)
    local users_block; users_block=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null || true)
    generate_telemt_toml "$secret" "$port" "$mode" "$domain" "$mask_port" "$TELEMT_CONFIG" >&2
    if [ -n "$users_block" ] && type replace_telemt_users_block >/dev/null 2>&1; then
        replace_telemt_users_block "$users_block" "$TELEMT_CONFIG" >&2 2>/dev/null || true
    fi
    diagnose_set_cc "$cc"
    restart_telemt >&2 || { log_error "$(tf diag_restart_failed "$id")" >&2; return 1; }
    return 0
}

# ── 🛟 Реаниматор: настройки + благодарность автору ──────────────────────────
# iOS keepalive-фикс ядра (по докам MTproxy-reanimation): 60/15/3 вместо 7200/75/9.
diagnose_ios_keepalive() {
    local mode="${1:-on}" f="/etc/sysctl.d/99-zz-gotelegram-keepalive.conf"
    if [ "$mode" = "on" ]; then
        printf 'net.ipv4.tcp_keepalive_time=60\nnet.ipv4.tcp_keepalive_intvl=15\nnet.ipv4.tcp_keepalive_probes=3\n' > "$f" 2>/dev/null \
            || { log_warning "$(tf diag_ka_write_failed "$f")" >&2; return 1; }
        sysctl -p "$f" >/dev/null 2>&1 || sysctl --system >/dev/null 2>&1 || true
        log_success "$(t diag_ka_on)" >&2
    else
        rm -f "$f" 2>/dev/null
        sysctl -w net.ipv4.tcp_keepalive_time=7200 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.tcp_keepalive_intvl=75   >/dev/null 2>&1 || true
        sysctl -w net.ipv4.tcp_keepalive_probes=9   >/dev/null 2>&1 || true
        log_info "$(t diag_ka_off)" >&2
    fi
}


