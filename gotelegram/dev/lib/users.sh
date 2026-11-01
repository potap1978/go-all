#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# goTelegram Pro — управление клиентскими ключами из SSH-меню
#
# Историческая дыра: ключи заводились только через Telegram-бота или веб-панель.
# Человек, у которого бот не поставлен (или сломался, или Telegram недоступен
# без прокси — а прокси-то и настраиваем), оставался без главной функции.
#
# Источник правды — [access.users] в config.toml, ровно как у бота и панели.
# Все записи под общим замком gt_with_config_lock: одновременная правка из двух
# интерфейсов теряет ключи (воспроизведено: пропало 9 из 40).
# ══════════════════════════════════════════════════════════════════════════════

users_list() {
    get_telemt_users_block "${1:-$TELEMT_CONFIG}" 2>/dev/null | awk -F= '
        /^[[:space:]]*#/ || ! /=/ { next }
        { key=$1; val=$2
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
          gsub(/^"|"$/, "", key); gsub(/^"|"$/, "", val)
          if (key != "") printf "%s\t%s\n", key, val }'
}
users_count() { users_list "$@" | grep -c . ; }

users_ip_limit() {
    get_telemt_ip_limits_block "$TELEMT_CONFIG" 2>/dev/null | awk -F= -v n="$1" '
        { key=$1; val=$2
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", key); gsub(/^"|"$/, "", key)
          gsub(/[^0-9]/, "", val)
          if (key == n) { print val; exit } }'
}
users_exists() { users_list | awk -F'\t' -v n="$1" '$1==n{f=1} END{exit !f}'; }

# Отключённые ключи живут не в TOML, а в disabled_users.json (его пишут бот и
# панель). SSH их не видел вовсе: `users_add` с тем же именем молча создавал
# дубликат с новым секретом, а следующее «Включить» в панели возвращало старый
# секрет поверх — розданная по SSH ссылка умирала без единого сообщения.
users_disabled_list() {
    local f="${GOTELEGRAM_DIR:-/opt/gotelegram}/disabled_users.json"
    [ -f "$f" ] && command -v jq >/dev/null 2>&1 || return 0
    # Формат тот же, что читают бот и панель: обёртка {version, updated_at,
    # users:{имя:секрет}}, но старые файлы бывают плоскими — берём оба вида и
    # отбрасываем служебные ключи.
    jq -r '(if (.users | type) == "object" then .users else . end)
           | to_entries[]?
           | select(.key != "version" and .key != "updated_at")
           | select(.value | type == "string")
           | "\(.key)\t\(.value)"' "$f" 2>/dev/null
}
users_disabled_exists() { users_disabled_list | awk -F'\t' -v n="$1" '$1==n{f=1} END{exit !f}'; }
# Имя уезжает в TOML и в имя файла QR — charset строго ограничен.
# Имя должно содержать хотя бы один буквенно-цифровой символ: панель такие
# имена и так отвергала, а SSH с ботом принимали — из-за чего ключ "..." жил
# в конфиге, но не открывался в веб-интерфейсе.
users_name_valid() { [[ "$1" =~ ^[A-Za-z0-9_.-]{1,48}$ && "$1" =~ [A-Za-z0-9] ]]; }
# Раньше show_qr не была определена нигде — охранник type на вызовах всегда лгал, и QR молча не печатался.
# Потом функция появилась, но QR всё равно не показывался: qrencode помечен в deps.sh
# как opt, а deps_install_missing не вызывается из установщика ни разу — то есть пакет
# не ставился НИКОГДА, независимо от важности. Пункт меню обещал «ссылку и QR», а
# показывал только ссылку, и ни строчки о причине.
# Теперь: ставим сам (пакет ~50 КБ), одна попытка на загрузку, и в любом случае
# объясняем словами, если не вышло.
GT_QR_TRY_FLAG="${GT_QR_TRY_FLAG:-/run/gotelegram-qr.tried}"
show_qr() {
    local link="${1:-}"
    [ -n "$link" ] || return 0
    if ! command -v qrencode >/dev/null 2>&1 && [ ! -e "$GT_QR_TRY_FLAG" ]; then
        : > "$GT_QR_TRY_FLAG" 2>/dev/null
        if [ "$(id -u 2>/dev/null || echo 1)" = "0" ] && command -v apt-get >/dev/null 2>&1; then
            log_dim "$(t usr_qr_installing)"
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qrencode >/dev/null 2>&1 || true
        fi
    fi
    command -v qrencode >/dev/null 2>&1 || { log_dim "$(t usr_qr_missing)"; return 0; }
    echo ""
    qrencode -t UTF8 -m 2 "$link" 2>/dev/null || log_dim "$(t usr_qr_missing)"
}

users_link() {
    local secret="$1" mode domain port host mask_host
    mode=$(config_get mode 2>/dev/null || echo lite)
    port=$(config_get port 2>/dev/null || echo 443)
    domain=$(config_get domain 2>/dev/null || echo "")
    mask_host=$(config_get mask_host 2>/dev/null || echo "")
    # Пустой mask_host в config.json — ссылка без ee, ядро в fake-TLS её не
    # примет. Источник правды — tls_domain конфига ядра.
    [ -z "$mask_host" ] && mask_host=$(get_config_value mask_host "$TELEMT_CONFIG" 2>/dev/null || echo "")
    # server= — домен только в Pro: в Lite домен маскировки нам не принадлежит, ссылка на него мертва (логика = menu_link)
    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then host="$domain"; mask_host="$domain"
    else host=$(get_server_ip 2>/dev/null || echo ""); fi
    generate_proxy_link "$host" "$port" "$secret" "$mask_host"
}

# ── Правки ключей без рестарта ────────────────────────────────────────────────
# Рестарт telemt рвёт ВСЕХ клиентов: 443 закрыт ~10 с, ME-пул поднимается заново.
# Ядро же само перечитывает config.toml (inotify-watcher и SIGHUP) и на лету
# применяет [access.users], [access.user_max_unique_ips] и (3.5.x) профили WEB.
# Поэтому: SIGHUP главному процессу и подтверждение через API ядра; не подтвердилось
# за GOTELEGRAM_TELEMT_RELOAD_TIMEOUT секунд — прежний restart_telemt. Та же логика —
# у бота и панели.
# Отзыв ключа горячо — только с 3.5.14: там ядро рвёт живые сессии отозванного
# секрета. В 3.4.18 и 3.5.5 перезагрузка закрывает лишь новые подключения, а уже
# открытые жили бы до разрыва — поэтому там рестарт.
USERS_HOT_REVOKE_MIN="3.5.14"
USERS_TELEMT_API="${USERS_TELEMT_API:-http://127.0.0.1:9091}"

_users_api() { curl -s --max-time 2 "${USERS_TELEMT_API}$1" 2>/dev/null; }

# «старт_процесса число_перезагрузок последняя_перезагрузка версия»; пусто — API молчит
_users_sysinfo() {
    _users_api /v1/system/info | jq -r 'select(.ok == true) | .data
        | "\(.process_started_at_epoch_secs // 0) \(.config_reload_count // 0) \(.last_config_reload_epoch_secs // 0) \(.version // "")"' 2>/dev/null
}

# «статус момент_готовности» из /v1/runtime/initialization
_users_init_state() {
    _users_api /v1/runtime/initialization | jq -r 'select(.ok == true) | .data
        | "\(.status // "") \(.ready_at_epoch_secs // 0)"' 2>/dev/null
}

# $1 >= $2 по номеру версии
_users_ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

# Видит ли ядро конфиг, записанный в момент $1 (mtime). Признаки /v1/system/info
# (одинаковы в 3.4.18 и 3.5.x): процесс стартовал позже записи; у того же процесса
# вырос config_reload_count (растёт только на опубликованном снимке); снимок не
# раньше записи — его успел опубликовать inotify-watcher, это берём лишь после паузы
# на разбор ($2=1): в ту же секунду мог лечь снимок от предыдущей записи.
# $3 $4 — старт и счётчик до SIGHUP, $5 $6 $7 — старт, счётчик, последний снимок сейчас.
_users_config_applied() {
    local written="$1" settled="$2" b_started="$3" b_count="$4" started="$5" count="$6" last="$7" v
    for v in "$written" "$started" "$count" "$last"; do [[ "$v" =~ ^[0-9]+$ ]] || return 1; done
    [ "$started" -gt "$written" ] && return 0
    if [[ "$b_started" =~ ^[0-9]+$ && "$b_count" =~ ^[0-9]+$ ]] \
        && [ "$started" = "$b_started" ] && [ "$count" -gt "$b_count" ]; then
        return 0
    fi
    [ "$settled" = 1 ] && [ "$last" -ge "$written" ]
}

# Новые ключи видны работающему ядру (in_runtime в GET /v1/users/{имя}).
# Имени уже нет в config.toml (ok=false) — проверять нечего.
_users_in_runtime() {
    local name state
    for name in "$@"; do
        state=$(_users_api "/v1/users/${name}" | jq -r '
            if .ok == false then "gone" elif .data.in_runtime == true then "yes" else "no" end' 2>/dev/null)
        case "$state" in gone|yes) ;; *) return 1 ;; esac
    done
    return 0
}

# telemt_hot_reload add|limit|revoke [имена новых ключей]
# 0 — ядро подтвердило новый конфиг без рестарта; 1 — нужен рестарт.
telemt_hot_reload() {
    local change="${1:-add}"; shift
    local timeout="${GOTELEGRAM_TELEMT_RELOAD_TIMEOUT:-8}" written started count last version
    local st ready_at b_started b_count pid i=0 j=0 max settled
    [[ "$timeout" =~ ^[0-9]+$ ]] || timeout=8
    max=$(( timeout * 4 ))
    command -v curl >/dev/null 2>&1 && command -v jq >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "$TELEMT_SERVICE" 2>/dev/null || return 1
    written=$(stat -c %Y "$TELEMT_CONFIG" 2>/dev/null || stat -f %m "$TELEMT_CONFIG" 2>/dev/null)
    [[ "$written" =~ ^[0-9]+$ ]] || return 1
    read -r started count last version <<< "$(_users_sysinfo)"
    [ -n "$version" ] || return 1   # API ядра молчит — подтвердить нечем
    if [ "$change" = "revoke" ] && ! _users_ver_ge "$version" "$USERS_HOT_REVOKE_MIN"; then
        return 1
    fi
    # SIGHUP до того, как ядро повесило обработчик, по умолчанию убивает процесс:
    # шлём только ядру, которое уже не меньше секунды в статусе ready
    while :; do
        if _users_config_applied "$written" 0 "" "" "$started" "$count" "$last" && _users_in_runtime "$@"; then
            return 0
        fi
        read -r st ready_at <<< "$(_users_init_state)"
        if [ "$st" = "ready" ] && [[ "$ready_at" =~ ^[1-9][0-9]*$ ]] \
            && [ $(( $(date +%s) - ready_at )) -ge 2 ]; then
            break
        fi
        i=$(( i + 2 )); [ "$i" -ge "$max" ] && return 1
        sleep 0.5
        read -r started count last version <<< "$(_users_sysinfo)"
        [ -n "$version" ] || return 1
    done
    b_started="$started"; b_count="$count"
    pid=$(systemctl show -p MainPID --value "$TELEMT_SERVICE" 2>/dev/null)
    [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ] || return 1
    kill -HUP "$pid" 2>/dev/null || return 1
    while [ "$i" -lt "$max" ]; do
        sleep 0.25
        i=$(( i + 1 )); j=$(( j + 1 ))
        settled=0; [ "$j" -ge 3 ] && settled=1
        read -r started count last version <<< "$(_users_sysinfo)"
        if _users_config_applied "$written" "$settled" "$b_started" "$b_count" "$started" "$count" "$last" \
            && _users_in_runtime "$@"; then
            return 0
        fi
    done
    return 1
}

# telemt_apply_user_change add|limit|revoke [имена] — перезагрузка конфига, рестарт — запасной путь
telemt_apply_user_change() {
    telemt_hot_reload "$@" && return 0
    restart_telemt
}

# _users_commit блок add|revoke [имена новых ключей]
_users_commit() {
    local block="$1" change="${2:-add}" web_limits_added=0
    shift 2 2>/dev/null || shift $#
    [ -n "$block" ] || { log_error "$(t usr_empty_block)"; return 1; }
    # Конфиг до 2.10.46: синхронизация профилей WEB допишет [web.limits], а его
    # ядро читает только при старте — такую правку применит лишь рестарт.
    if grep -q '^\[\[web\.vhosts\]\]' "$TELEMT_CONFIG" 2>/dev/null \
        && ! grep -q '^\[web\.limits\]' "$TELEMT_CONFIG" 2>/dev/null; then
        web_limits_added=1
    fi
    replace_telemt_users_block "$block" "$TELEMT_CONFIG" || { log_error "$(t usr_write_failed)"; return 1; }
    validate_telemt_config >/dev/null 2>&1 || { log_error "$(t usr_config_invalid)"; return 1; }
    if [ "$web_limits_added" = 1 ]; then
        restart_telemt >/dev/null 2>&1 || log_warning "$(t usr_restart_failed)"
    else
        telemt_apply_user_change "$change" "$@" >/dev/null 2>&1 || log_warning "$(t usr_restart_failed)"
    fi
    return 0
}

users_add() { gt_with_config_lock _users_add_locked "$@"; }
_users_add_locked() {
    local name="$1" secret block link
    users_name_valid "$name" || { log_error "$(t usr_bad_name)"; return 1; }
    users_exists "$name" && { log_error "$(tf usr_exists "$name")"; return 1; }
    users_disabled_exists "$name" && { log_error "$(tf usr_exists_disabled "$name")"; return 1; }
    secret=$(generate_hex 32)
    block=$(get_telemt_users_block "$TELEMT_CONFIG" 2>/dev/null)
    block="${block}
\"${name}\" = \"${secret}\""
    _users_commit "$block" add "$name" || return 1
    log_success "$(tf usr_created "$name")"
    # Секрет, ссылку и QR печатает НЕ эта функция, а menu_users — после того, как
    # перерисует список. Раньше блок выводился здесь, следом цикл рисовал таблицу
    # поверх, и на списке из двух десятков ключей только что созданный ключ уезжал
    # за верхний край экрана: человек его просто не видел. Единственный вызов
    # users_add — из меню, поэтому вывод и живёт там же, рядом с таблицей.
    return 0
}

users_delete() { gt_with_config_lock _users_delete_locked "$@"; }
_users_delete_locked() {
    local name="$1" block lim
    [ "$name" = "main" ] && { log_error "$(t usr_cant_del_main)"; return 1; }
    users_exists "$name" || { log_error "$(tf usr_not_found "$name")"; return 1; }
    block=$(get_telemt_users_block "$TELEMT_CONFIG" | awk -v n="$name" -F= '
        { key=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", key); gsub(/^"|"$/, "", key)
          if (key != n) print }')
    [ -z "$block" ] && { log_error "$(t usr_last_key)"; return 1; }
    _users_commit "$block" revoke || return 1
    # осиротевший лимит убираем — иначе он висит на несуществующем имени
    lim=$(get_telemt_ip_limits_block "$TELEMT_CONFIG" 2>/dev/null | awk -v n="$name" -F= '
        { key=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", key); gsub(/^"|"$/, "", key)
          if (key != n) print }')
    # единственный лимит даёт пустой блок, а replace_* пустой игнорирует — заглушка, как в _users_set_ip_limit_locked
    [ -z "$lim" ] && lim="# нет персональных лимитов"
    replace_telemt_ip_limits_block "$lim" "$TELEMT_CONFIG" >/dev/null 2>&1
    log_success "$(tf usr_deleted "$name")"
    return 0
}

users_set_ip_limit() { gt_with_config_lock _users_set_ip_limit_locked "$@"; }
_users_set_ip_limit_locked() {
    local name="$1" limit="$2" block
    users_exists "$name" || { log_error "$(tf usr_not_found "$name")"; return 1; }
    # потолок: число шире 64 бит роняет [ -gt ] сырой ошибкой bash и ложно рапортует «лимит снят»
    [[ "$limit" =~ ^[0-9]{1,4}$ ]] && [ "$limit" -le 1000 ] || { log_error "$(t usr_bad_limit)"; return 1; }
    # Единица запрещена: телефон при смене сети меняет адрес, старые сокеты ещё живы,
    # и клиент отбивается собственным лимитом. Раньше здесь было лишь предупреждение
    # ПОСЛЕ записи — человек узнавал о проблеме из жалоб. Ноль по-прежнему означает
    # «нет персонального лимита», минимальное осмысленное значение — 2.
    if [ "$limit" = "1" ]; then log_error "$(t usr_limit_min_err)"; return 1; fi
    # тот же лимит — конфиг не трогаем: ядру нечего применять, а без перемен в
    # конфиге подтвердить перезагрузку нечем и пришлось бы рестартовать
    local cur; cur=$(users_ip_limit "$name")
    if [ "${cur:-0}" = "$limit" ]; then
        if [ "$limit" -gt 0 ]; then log_success "$(tf usr_limit_set "$name" "$limit")"
        else log_success "$(tf usr_limit_cleared "$name")"; fi
        return 0
    fi
    block=$(get_telemt_ip_limits_block "$TELEMT_CONFIG" 2>/dev/null | awk -v n="$name" -F= '
        { key=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", key); gsub(/^"|"$/, "", key)
          if (key != n) print }')
    [ "$limit" -gt 0 ] && block="${block}
\"${name}\" = ${limit}"
    [ -z "$block" ] && block="# нет персональных лимитов"
    replace_telemt_ip_limits_block "$block" "$TELEMT_CONFIG" || { log_error "$(t usr_write_failed)"; return 1; }
    telemt_apply_user_change limit >/dev/null 2>&1 || log_warning "$(t usr_restart_failed)"
    if [ "$limit" -gt 0 ]; then log_success "$(tf usr_limit_set "$name" "$limit")"
    else log_success "$(tf usr_limit_cleared "$name")"; fi
    return 0
}

users_show_table() {
    local i=0 name secret lim glob
    glob=$(grep -oE 'user_max_unique_ips_global_each[[:space:]]*=[[:space:]]*[0-9]+' "$TELEMT_CONFIG" 2>/dev/null | grep -oE '[0-9]+$')
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t usr_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
    if [ "$(users_count)" = "0" ]; then
        echo -e "  ${DIM}$(t usr_none)${NC}"
    else
        printf "  %-4s %-22s %-16s %s\n" "#" "$(t usr_col_name)" "$(t usr_col_limit)" "$(t usr_col_secret)"
        while IFS=$'\t' read -r name secret; do
            [ -z "$name" ] && continue
            i=$((i+1))
            lim=$(users_ip_limit "$name")
            [ -z "$lim" ] && lim="${glob:-—}*"
            printf "  %-4s %-22s %-16s %s…\n" "$i" "$name" "$lim" "${secret:0:8}"
        done < <(users_list)
        echo -e "  ${DIM}* $(t usr_global)${NC}"
    fi
    # Отключённые ключи показываем отдельным списком (нумерации у них нет —
    # действия SSH работают только с активными). Раньше их не было видно
    # вовсе, и человек создавал дубликат уже существующего имени.
    local dis
    dis=$(users_disabled_list 2>/dev/null | cut -f1 | paste -sd' ' -)
    if [ -n "$dis" ]; then
        echo -e "  ${DIM}$(t usr_disabled_hdr): ${dis}${NC}"
    fi
    echo -e "  ${DIM}$(printf '─%.0s' {1..54})${NC}"
}

users_pick() {
    local src="$1" n name
    echo -ne "  ${WHITE}$(t usr_pick):${NC} " >&2
    read -r n < "$src" || return 1
    [[ "$n" =~ ^[0-9]+$ ]] || return 1
    name=$(users_list | sed -n "${n}p" | cut -f1)
    [ -n "$name" ] || return 1
    printf '%s' "$name"
}

menu_users() {
    [ -f "$TELEMT_CONFIG" ] || { log_error "$(t usr_not_installed)"; return 1; }
    local src ch name val secret show_for=""
    src="/dev/stdin"; { : < /dev/tty; } 2>/dev/null && [ ! -t 0 ] && src=/dev/tty
    while true; do
        users_show_table
        # Ключ и QR печатаем ЗДЕСЬ, после таблицы — во ВСЕХ сценариях: и когда ключ
        # только что создан, и когда его попросили показать. Раньше блок выводился
        # сразу по выбору пункта, а следом цикл перерисовывал список поверх, и на
        # длинной таблице ключ уезжал за верхний край — человек его не видел.
        # Порядок на экране: список ключей -> секрет, ссылка и QR -> пункты меню.
        if [ -n "$show_for" ]; then
            secret=$(users_list | awk -F'\t' -v n="$show_for" '$1==n{print $2; exit}')
            if [ -n "$secret" ]; then
                echo ""
                echo -e "  ${BOLD}${show_for}${NC}"
                echo -e "  ${DIM}$(t usr_secret):${NC} ${WHITE}${secret}${NC}"
                echo -e "  ${DIM}$(t usr_link):${NC}"
                echo -e "  ${CYAN}$(users_link "$secret")${NC}"
                show_qr "$(users_link "$secret")"
                type show_web_link >/dev/null 2>&1 && show_web_link "$secret" 1
                echo ""
            else
                log_error "$(tf usr_not_found "$show_for")"
            fi
            show_for=""
        fi
        echo -e "  ${CYAN}1${NC}) $(t usr_add)"
        echo -e "  ${CYAN}2${NC}) $(t usr_del)"
        echo -e "  ${CYAN}3${NC}) $(t usr_limit)"
        echo -e "  ${CYAN}4${NC}) $(t usr_show)"
        echo -e "  ${CYAN}0${NC}) $(t back)"
        echo -ne "  ${WHITE}$(t choose):${NC} "
        read -r ch < "$src" || ch=0     # EOF (оборванная сессия) → выход, не busy-loop
        case "$ch" in
            1)  echo -ne "  ${WHITE}$(t usr_enter_name):${NC} "
                read -r name < "$src" || continue
                [ -z "$name" ] && { log_error "$(t usr_bad_name)"; continue; }
                # Показать ключ после перерисовки таблицы, а не поверх неё.
                # Судим по факту наличия ключа, а не по коду возврата: users_add идёт
                # через gt_with_config_lock и _users_commit, где неудачная проверка
                # конфига даёт ненулевой код уже ПОСЛЕ записи ключа. В таком случае
                # ключ существует, и показать его нужно тем более.
                users_add "$name"
                users_exists "$name" && show_for="$name" ;;
            2)  [ "$(users_count)" -lt 2 ] && { log_error "$(t usr_last_key)"; continue; }
                name=$(users_pick "$src") || { log_error "$(t invalid_choice)"; continue; }
                if confirm "$(tf usr_del_confirm "$name")"; then users_delete "$name"; else log_info "$(t usr_cancelled)"; fi ;;
            3)  name=$(users_pick "$src") || { log_error "$(t invalid_choice)"; continue; }
                echo -e "  ${DIM}$(t usr_limit_hint)${NC}"
                echo -ne "  ${WHITE}$(t usr_enter_limit):${NC} "
                read -r val < "$src" || continue
                users_set_ip_limit "$name" "$val" ;;
            4)  name=$(users_pick "$src") || { log_error "$(t invalid_choice)"; continue; }
                show_for="$name" ;;
            0)  break ;;
            *)  log_error "$(t invalid_choice)" ;;
        esac
    done
}
