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
# ── Разводка 443 по имени (WEB на отдельном имени) ───────────────────────────
# Имя WEB уходит в nginx мимо ядра telemt, всё остальное — в ядро с
# PROXY-заголовком. Почему так — см. «Отдельное имя для WEB» в telemt_config.sh.
WEB_FRONT_STREAM_CONF="${WEB_FRONT_STREAM_CONF:-/etc/nginx/stream-conf.d/gotelegram-web-front.conf}"

# Через nginx теперь идёт каждое соединение прокси, и каждое занимает у него
# два слота (клиент + ядро). Дефолт Ubuntu — 768 слотов на воркер, на одном
# ядре это ~380 одновременных соединений против 5000 у telemt.
web_front_tune_nginx_limits() {
    local conf=/etc/nginx/nginx.conf cur
    [ -f "$conf" ] || return 0
    cur=$(awk '/^[[:space:]]*worker_connections[[:space:]]+[0-9]+/ { gsub(/[^0-9]/, "", $2); print $2; exit }' "$conf")
    if [ -z "$cur" ] || [ "$cur" -lt 16384 ] 2>/dev/null; then
        [ -f "${conf}.gotelegram.orig" ] || cp -p "$conf" "${conf}.gotelegram.orig" 2>/dev/null
        if [ -n "$cur" ]; then
            sed -i -E 's/^([[:space:]]*worker_connections[[:space:]]+)[0-9]+;/\116384;/' "$conf"
        else
            sed -i -E 's/^([[:space:]]*events[[:space:]]*\{)/\1\n    worker_connections 16384;/' "$conf"
        fi
    fi
    grep -qE '^[[:space:]]*worker_rlimit_nofile' "$conf" \
        || sed -i -E '0,/^[[:space:]]*worker_processes[^;]*;/s//&\nworker_rlimit_nofile 65535;/' "$conf"
}

web_front_write_stream_config() {
    local web_host="$1"
    [ -n "$web_host" ] || return 1
    type shared443_install_stream_module >/dev/null 2>&1 \
        || source "$(dirname "${BASH_SOURCE[0]}")/shared443.sh" 2>/dev/null
    # Общий 443 с 3x-ui держит свой stream-сервер на том же порту — двух быть не может
    if [ -f "${SHARED443_STREAM_CONF:-/etc/nginx/stream-conf.d/gotelegram-shared443.conf}" ]; then
        log_error "WEB на отдельном имени несовместим с общим 443 (shared-443) — сначала снимите его"
        return 1
    fi
    shared443_install_stream_module || { log_error "nginx stream (ssl_preread) недоступен"; return 1; }
    # include уже есть в файлах — nginx -T не нужен: он падает на любом битом
    # конфиге, и вывод «include нет» отказывал в разводке на ровном месте
    grep -rqs 'stream-conf.d/\*.conf' /etc/nginx/nginx.conf /etc/nginx/conf.d/ 2>/dev/null \
        || shared443_ensure_nginx_include || return 1
    web_front_tune_nginx_limits
    mkdir -p "$(dirname "$WEB_FRONT_STREAM_CONF")"
    cat > "$WEB_FRONT_STREAM_CONF" << EOSTREAM
# goTelegram Pro: разводка 443 по имени. Имя WEB (${web_host}) — в nginx мимо ядра,
# всё остальное (обычные ссылки, сайт, сканеры) — в telemt с PROXY-заголовком.
map \$ssl_preread_server_name \$gotelegram_web_front {
    hostnames;
    ${web_host} 127.0.0.1:${WEB_FRONT_TLS_PORT:-8445};
    default 127.0.0.1:${WEB_FRONT_TELEMT_PORT:-7443};
}

server {
    # so_keepalive — как у ядра на его клиентских сокетах (client_keepalive = 15):
    # без него простаивающее соединение умирало в NAT телефона примерно за
    # 5 минут, телефон об этом не знал, и сообщение уходило в пустоту, пока
    # прокси не переключат руками. 4 пробы по 15 с — мёртвое видно за минуту.
    listen 0.0.0.0:443 backlog=4096 so_keepalive=15s:15s:4;
    ssl_preread on;
    proxy_pass \$gotelegram_web_front;
    proxy_protocol on;
    proxy_connect_timeout 5s;
    # MTProto держит соединения долго; закрывает их ядро, это лишь страховка.
    # keepalive — чтобы брошенные клиентом сокеты не висели этот час
    proxy_timeout 1h;
    proxy_socket_keepalive on;
}
EOSTREAM
}

web_front_remove_stream_config() {
    [ -f "$WEB_FRONT_STREAM_CONF" ] && rm -f "$WEB_FRONT_STREAM_CONF"
    return 0
}

_web_front_nginx_on_443() { ss -ltnpH '( sport = :443 )' 2>/dev/null | grep -q nginx; }

# Порт 443 держит либо ядро, либо nginx stream. Перед (пере)запуском ядра
# приводим nginx к конфигу ядра и соблюдаем порядок: при разводке сначала
# ядро отпускает 443, потом nginx его занимает; без разводки — наоборот.
# Зовут start_telemt/restart_telemt: через них идут все смены режима (WEB
# вкл/выкл, имя WEB, переход в Lite, смена ядра, восстановление бекапа).
web_front_sync_before_restart() {
    type web_front_in_core >/dev/null 2>&1 || return 0
    local svc="${TELEMT_SERVICE:-telemt}"
    if web_front_in_core; then
        [ -f "$WEB_FRONT_STREAM_CONF" ] || web_front_write_stream_config "$(web_vhost_host)" || return 1
        _web_front_nginx_on_443 && return 0
        # Старое ядро ещё на 0.0.0.0:443 — отпускаем порт до перезагрузки nginx
        ss -ltnpH '( sport = :443 )' 2>/dev/null | grep -q telemt && systemctl stop "$svc" 2>/dev/null
        nginx -t >/dev/null 2>&1 || { log_error "nginx -t не прошёл — разводка 443 не применена"; return 1; }
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null
        local i; for i in 1 2 3 4 5 6 7 8 9 10; do _web_front_nginx_on_443 && return 0; sleep 0.5; done
        log_error "nginx не занял порт 443 для разводки по имени"
        return 1
    fi
    [ -f "$WEB_FRONT_STREAM_CONF" ] || return 0
    web_front_remove_stream_config
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null
    fi
    local i; for i in 1 2 3 4 5 6 7 8 9 10; do _web_front_nginx_on_443 || return 0; sleep 0.5; done
    return 0
}

# ── Версии TLS сайта (ssl_protocols) ─────────────────────────────────────────
# 29.10.2026: домашний провайдер владельца периодически резал весь 443 на ~7 мин,
# пока nginx предлагал TLSv1.3; с ручным «ssl_protocols TLSv1.2;» окон не было
# 5 ч 46 мин, а обновление 2.10.54 перегенерировало nginx и вернуло 1.3. Выбор
# живёт в config.json (tls_protocols) и переживает обновление, смену имени WEB и
# перевыпуск сертификата. Нет ключа — как было: «TLSv1.2 TLSv1.3» (но см.
# nginx_tls_adopt_manual: сгенерированный «только 1.2» без ключа читается как
# ручная фиксация, поэтому меню и --action пишут явное «TLSv1.2 TLSv1.3»).
# В nginx попадает только одна из двух строк-литералов, сырое значение — никогда.
nginx_tls_protocols() {
    local v shown
    v=$(config_get tls_protocols 2>/dev/null) || v=""
    # пробелы по краям — не ошибка: «TLSv1.2 » молча давал 1.3
    v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
    case "$v" in
        "TLSv1.2")             echo "TLSv1.2" ;;
        ""|"TLSv1.2 TLSv1.3")  echo "TLSv1.2 TLSv1.3" ;;
        *)
            # значение в сообщение — без переводов строк и управляющих символов
            shown=$(printf '%s' "$v" | tr -c '[:alnum:]. ;/_-' '?' | cut -c1-40)
            log_warning "config.json: tls_protocols «${shown}» недопустимо (можно «TLSv1.2» или «TLSv1.2 TLSv1.3») — беру TLSv1.2 TLSv1.3"
            type log_to_file >/dev/null 2>&1 \
                && log_to_file "nginx_tls: invalid tls_protocols in config.json, using default TLSv1.2 TLSv1.3"
            echo "TLSv1.2 TLSv1.3" ;;
    esac
}

# Запись ключа в config.json: temp рядом с целью + mv (читатели не увидят
# обрезанный файл), права 600. Нет файла или он не JSON-объект — отказ.
_nginx_tls_cfg_set() {
    local cfg="${GOTELEGRAM_CONFIG:-/opt/gotelegram/config.json}" tmp now
    [ -f "$cfg" ] && command -v jq >/dev/null 2>&1 || return 1
    tmp=$(mktemp "${cfg}.XXXXXX" 2>/dev/null) || return 1
    now=$(date -Iseconds 2>/dev/null || date +%s)
    if jq --arg v "$1" --arg t "$now" '.tls_protocols = $v | .updated_at = $t' "$cfg" > "$tmp" 2>/dev/null; then
        chmod 600 "$tmp" 2>/dev/null
        mv -f "$tmp" "$cfg" 2>/dev/null && return 0
    fi
    rm -f "$tmp"
    return 1
}

# Действующий конфиг сайта — тот, что читает nginx (см. nginx_tls_adopt_manual).
_nginx_tls_live_conf() {
    if [ -e "$NGINX_SITE_LINK" ]; then printf '%s' "$NGINX_SITE_LINK"; else printf '%s' "$NGINX_SITE_CONF"; fi
}

# Все директивы ssl_protocols действующего конфига — ровно «ssl_protocols $1;»,
# каждая одна на своей строке, и их не меньше одной. $1 — один из двух литералов.
# Комментарий (#…) отбрасывается. Директиву ищем не только в начале строки, но
# и после другой («ssl_session_tickets off; ssl_protocols …»): строка с ней
# обязана целиком совпасть с точным шаблоном, иначе ответ «нет». Края строки
# окаймлены пробелами — так начало и конец тоже граница директивы без «(^|…)»,
# который понимают не все awk (mawk, BSD awk, gawk).
_nginx_tls_live_all() {
    local live
    case "${1:-}" in "TLSv1.2"|"TLSv1.2 TLSv1.3") ;; *) return 1 ;; esac
    live=$(_nginx_tls_live_conf)
    [ -f "$live" ] || return 1
    awk -v want="$1" '
        BEGIN {
            v = (want == "TLSv1.2") ? "TLSv1[.]2" : "TLSv1[.]2[ \t]+TLSv1[.]3"
            exact = "^[ \t]*ssl_protocols[ \t]+" v "[ \t]*;[ \t\r]*$"
        }
        {
            sub(/#.*/, "")
            if ((" " $0 " ") ~ /[;{ \t]ssl_protocols[ \t;\r]/) { n++; if ($0 ~ exact) ok++ }
        }
        END { exit !(n > 0 && n == ok) }' "$live" 2>/dev/null
}

# Ручная фиксация: ключа tls_protocols нет, а в действующем конфиге nginx ВСЕ
# строки ssl_protocols — ровно «TLSv1.2;». Переносим выбор в config.json ДО
# перезаписи файла. Смесь, 1.2+1.3, ни одной строки (временный конфиг до SSL),
# нет файла — ничего. Действующий — тот, что читает nginx: sites-enabled (обычно
# симлинк на sites-available; `sed -i` по пути из sites-enabled заменяет симлинк
# обычным файлом — тогда действует он). rc 0 — фиксация найдена: брать TLSv1.2,
# даже если записать в config.json не вышло.
# Своё «только 1.2» генератор не помечает: config.json без ключа (старый бекап,
# правка руками) с пометкой вернул бы 1.3 тому, кому провайдер режет 443. Цена —
# удаление ключа 1.3 не возвращает; для этого меню и --action пишут явное значение.
nginx_tls_adopt_manual() {
    config_get tls_protocols >/dev/null 2>&1 && return 1
    local live; live=$(_nginx_tls_live_conf)
    _nginx_tls_live_all "TLSv1.2" || return 1
    if _nginx_tls_cfg_set "TLSv1.2"; then
        log_info "TLS: в nginx вручную стоял только TLSv1.2 — сохранил в config.json (tls_protocols), обновления его не сбросят"
        type log_to_file >/dev/null 2>&1 \
            && log_to_file "nginx_tls: manual 'ssl_protocols TLSv1.2;' in $live, saved tls_protocols=TLSv1.2 to config.json"
    else
        log_warning "TLS: в nginx вручную стоит только TLSv1.2 — оставляю, но записать в config.json не удалось"
        type log_to_file >/dev/null 2>&1 \
            && log_to_file "nginx_tls: manual 'ssl_protocols TLSv1.2;' in $live, kept; failed to save to config.json"
    fi
    return 0
}

# ── Переключатель версий TLS (SSH-меню, бот, панель) ────────────────────────
# Значение из бота/панели (--value=) → литерал ключа. Только «1.2» и «1.2+1.3»;
# всё остальное — отказ (rc 1) без вывода.
nginx_tls_cli_value() {
    case "${1-}" in
        "1.2")     echo "TLSv1.2" ;;
        "1.2+1.3") echo "TLSv1.2 TLSv1.3" ;;
        *) return 1 ;;
    esac
}
# Обратно: литерал → значение для --value (его же показывают бот и панель).
nginx_tls_cli_name() { if [ "${1-}" = "TLSv1.2" ]; then echo "1.2"; else echo "1.2+1.3"; fi; }
# Пункт экрана SSH-меню → литерал ключа: 1 — 1.2+1.3 (по умолчанию), 2 — только 1.2.
nginx_tls_menu_value() {
    case "${1-}" in
        1) echo "TLSv1.2 TLSv1.3" ;;
        2) echo "TLSv1.2" ;;
        *) return 1 ;;
    esac
}

# Что действует сейчас: ключ config.json, без ключа — ручная фиксация TLSv1.2 в
# действующем конфиге (её подхватит следующая перегенерация), иначе умолчание.
# Ничего не пишет и не предупреждает (зовётся на каждой отрисовке меню).
nginx_tls_current() {
    if config_get tls_protocols >/dev/null 2>&1; then
        # о мусоре в ключе предупреждает генератор; здесь — ни stderr, ни журнала
        ( log_to_file() { :; }; nginx_tls_protocols ) 2>/dev/null
    elif _nginx_tls_live_all "TLSv1.2"; then echo "TLSv1.2"
    else echo "TLSv1.2 TLSv1.3"; fi
}

# Почему переключать нечего: «mode» — не Pro (Lite: своего сайта и nginx нет),
# «no_site» — Pro, но нет домена или действующего конфига с сертификатом
# (установка не дошла, временный конфиг до выпуска SSL). Пусто — можно.
nginx_tls_blocker() {
    if [ "$(config_get mode 2>/dev/null || echo lite)" != "pro" ]; then echo mode; return 0; fi
    if [ -z "$(config_get domain 2>/dev/null)" ] \
       || ! grep -qE '^[[:space:]]*ssl_certificate[[:space:]]' "$(_nginx_tls_live_conf)" 2>/dev/null; then
        echo no_site
    fi
    return 0
}

# Снимок файлов до перегенерации: файл и симлинк — как есть (cp -P), отсутствие —
# пометкой. restore возвращает ровно прежнее состояние (лишний файл удаляется).
# Ни копии, ни пометки (снимок удалён или повреждён) — живой файл не трогаем, rc 1:
# иначе rm без cp оставил бы nginx без конфига.
_nginx_tls_snap() {
    local op="$1" dir="$2" f i=0 rc=0
    shift 2
    for f in "$@"; do
        i=$((i + 1))
        case "$op" in
            save)
                if [ -e "$f" ] || [ -L "$f" ]; then cp -P -p "$f" "$dir/$i" 2>/dev/null || rc=1
                else : > "$dir/$i.none" || rc=1; fi ;;
            restore)
                if [ -e "$dir/$i.none" ]; then rm -f "$f" 2>/dev/null
                elif [ -e "$dir/$i" ] || [ -L "$dir/$i" ]; then
                    rm -f "$f" 2>/dev/null; cp -P -p "$dir/$i" "$f" 2>/dev/null || rc=1
                else rc=1; fi ;;
        esac
    done
    return $rc
}

# Вернуть tls_protocols и updated_at (его тоже меняет _nginx_tls_cfg_set) как
# были: $1 — объект JSON с их прежними значениями (jq -c); ключа в объекте нет —
# его не было, удалить. Остальные поля config.json не трогаем: их мог поменять
# кто-то другой, пока шла проверка nginx.
_nginx_tls_cfg_restore() {
    local cfg="${GOTELEGRAM_CONFIG:-/opt/gotelegram/config.json}" tmp ok=0
    [ -f "$cfg" ] && [ -n "${1-}" ] && command -v jq >/dev/null 2>&1 || return 1
    tmp=$(mktemp "${cfg}.XXXXXX" 2>/dev/null) || return 1
    jq --argjson o "$1" 'reduce ("tls_protocols", "updated_at") as $k (.;
        if ($o | has($k)) then .[$k] = $o[$k] else del(.[$k]) end)' "$cfg" > "$tmp" 2>/dev/null && ok=1
    if [ "$ok" = 1 ]; then
        chmod 600 "$tmp" 2>/dev/null
        mv -f "$tmp" "$cfg" 2>/dev/null && return 0
    fi
    rm -f "$tmp"
    return 1
}

# Переключить версии TLS сайта и WEB. $1 — литерал ключа. Путь тот же, что у
# мини-аппа и имени WEB: ключ → generate_nginx_config → nginx -t → reload. Любой
# сбой возвращает прежний ключ и прежние файлы nginx (сайт, симлинк, разводка
# 443). telemt не трогаем: TLS сайта и WEB завершает nginx, reload соединения не рвёт.
# restart не делаем никогда: при WEB на отдельном имени 443 держит nginx stream,
# и restart оборвал бы все соединения. reload не прошёл, а nginx жив — откат;
# nginx стоял — start (рвать нечего).
# rc: 0 — применено; 9 — nginx уже так, его не трогали (ключ дописывается, если
# его не было или он с мусором); 2 — здесь не используется (причина —
# nginx_tls_blocker); 4 — недопустимое значение; 5 — не записался config.json;
# 6 — конфиг nginx не собрался или не прошёл проверку; 7 — nginx не перечитал
# конфиг; 8 — сбой и откат не удался (снимок файлов nginx оставлен, путь — в журнале).
nginx_tls_set() {
    if type gt_with_config_lock >/dev/null 2>&1; then gt_with_config_lock _nginx_tls_set_locked "$@"
    else _nginx_tls_set_locked "$@"; fi
}

_nginx_tls_set_locked() {
    local want="${1-}" cfg="${GOTELEGRAM_CONFIG:-/opt/gotelegram/config.json}" raw old snap rc=0 bad="" how="reloaded"
    case "$want" in "TLSv1.2"|"TLSv1.2 TLSv1.3") ;; *) return 4 ;; esac
    [ -z "$(nginx_tls_blocker)" ] || return 2
    raw=$(config_get tls_protocols 2>/dev/null) || raw=""
    # Во всех ssl_protocols nginx уже так: перегенерация и reload не нужны. Ключ —
    # ровно выбранный (явный выбор переживает обновление и ручную правку nginx);
    # нет его или он другой — дописываем, nginx не трогаем.
    if _nginx_tls_live_all "$want"; then
        [ "$raw" = "$want" ] || _nginx_tls_cfg_set "$want" || return 5
        return 9
    fi
    local files=("$NGINX_SITE_CONF" "$NGINX_SITE_LINK" "$WEB_FRONT_STREAM_CONF")
    old=$(jq -c 'if type == "object" then with_entries(select(.key == "tls_protocols" or .key == "updated_at"))
        else empty end' "$cfg" 2>/dev/null)
    [ -n "$old" ] || return 5   # без прежних значений откатить было бы нечем
    snap=$(mktemp -d "${TMPDIR:-/tmp}/gt-tls.XXXXXX" 2>/dev/null) || return 5
    if ! _nginx_tls_snap save "$snap" "${files[@]}" || ! _nginx_tls_cfg_set "$want"; then
        rm -rf "$snap"; return 5
    fi
    if ! generate_nginx_config "$(config_get domain 2>/dev/null)" 8443 >/dev/null 2>&1 \
       || ! _nginx_tls_live_all "$want" || ! nginx -t >/dev/null 2>&1; then
        rc=6
    elif ! systemctl reload nginx >/dev/null 2>&1; then
        # жив — работает на прежнем конфиге, его и оставляем (откат); стоял — поднимаем
        if systemctl is-active --quiet nginx 2>/dev/null; then rc=7
        elif systemctl start nginx >/dev/null 2>&1; then how="started"
        else rc=7; fi
    fi
    if [ "$rc" != 0 ]; then
        _nginx_tls_snap restore "$snap" "${files[@]}" || bad="nginx files"
        _nginx_tls_cfg_restore "$old" || bad="${bad:+$bad, }config.json"
        # nginx снова на прежнем конфиге: жив — reload, стоял — start (без restart)
        if [ "$rc" = 7 ] && [ -z "$bad" ] && nginx -t >/dev/null 2>&1; then
            systemctl reload nginx >/dev/null 2>&1 \
                || { systemctl is-active --quiet nginx 2>/dev/null || systemctl start nginx >/dev/null 2>&1; }
        fi
        if [ -n "$bad" ]; then
            type log_to_file >/dev/null 2>&1 \
                && log_to_file "nginx_tls: switch to '$want' failed (rc=$rc), ROLLBACK FAILED ($bad); previous tls_protocols/updated_at: $old; nginx snapshot kept in $snap"
            return 8
        fi
        type log_to_file >/dev/null 2>&1 \
            && log_to_file "nginx_tls: switch to '$want' failed (rc=$rc), previous key and nginx config restored"
    else
        type log_to_file >/dev/null 2>&1 && log_to_file "nginx_tls: switched to '$want' (nginx $how, telemt untouched)"
    fi
    rm -rf "$snap"
    return $rc
}

generate_nginx_config() {
    local domain="$1"
    local proxy_port="${2:-443}"
    local use_ssl="${3:-true}"

    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled

    # Версии TLS — до перезаписи файла: ручная фиксация TLSv1.2 сначала
    # переносится в config.json (см. nginx_tls_protocols).
    local tls_protocols
    if nginx_tls_adopt_manual; then tls_protocols="TLSv1.2"; else tls_protocols=$(nginx_tls_protocols); fi

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
    # WEB на отдельном имени — тоже по факту конфига ядра: главный listener за
    # nginx stream. Тогда WEB-блок слушает свой порт под своим именем, а
    # основной домен становится обычным сайтом (см. «Отдельное имя для WEB»).
    local front=0 web_host="" acme_names="$domain" cfg_wh=""
    if [ "$web_on" = "1" ] && type web_front_in_core >/dev/null 2>&1 && web_front_in_core; then
        web_host=$(web_vhost_host 2>/dev/null)
        [ -n "$web_host" ] && [ "$web_host" != "$domain" ] && front=1
    fi
    # Имя WEB слушает и порт 80 (ACME для сертификата с двумя именами) — ещё до
    # того, как разводка включена: выпуск идёт раньше переключения.
    type telemt_web_host >/dev/null 2>&1 && cfg_wh=$(telemt_web_host 2>/dev/null)
    # Новое имя до записи в config.json (web_set_host: сначала сертификат)
    [ -n "${WEB_HOST_PENDING:-}" ] && cfg_wh="$WEB_HOST_PENDING"
    [ -n "$cfg_wh" ] && [ "$cfg_wh" != "$domain" ] && acme_names="$domain $cfg_wh"
    [ "$front" = "1" ] && [ "$web_host" != "$cfg_wh" ] && acme_names="$acme_names $web_host"

    cat > "$NGINX_SITE_CONF" << 'EONGINX'
# GoTelegram v2.5.0 — nginx config
# Pro: nginx на 127.0.0.1:8443 (внутренний), telemt на 0.0.0.0:443 (внешний)
# Обычный браузер → :443 → telemt → 127.0.0.1:8443 → nginx (сайт)

server {
    listen 80;
    listen [::]:80;
    server_name ACME_NAMES_PLACEHOLDER;

    # Let's Encrypt ACME challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    # Редирект на HTTPS
    location / {
        # на то же имя, на которое пришли: $server_name — всегда первое имя блока
        # (основной домен), и http://www-имя уводило бы на домен без www
        return 301 https://$host$request_uri;
    }
}

EONGINX
    if [ "$web_on" = "1" ]; then
        # Общие куски: сертификат и тело сайта (заголовки, мини-апп, статика) —
        # сайт живёт и в decoy, и (при отдельном имени WEB) на основном домене.
        local ssl_block site_body
        ssl_block=$(cat << 'EOSSL'
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;
    ssl_protocols TLS_PROTOCOLS_PLACEHOLDER;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
EOSSL
)
        site_body=$(cat << 'EOBODY'
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
EOBODY
)
        {
        cat << 'EONGINX'
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
    # PROXY-заголовок приходит от telemt (mask_proxy_protocol, WEB на основном
    # домене) или от nginx stream (WEB на отдельном имени)
    listen 127.0.0.1:WEBLISTEN_PLACEHOLDER ssl http2 proxy_protocol;
    server_name WEBVH_PLACEHOLDER;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

    # В URI живёт bridge capability, в Authorization — bearer сессии.
    # Логировать их нельзя.
    access_log off;

    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;
    ssl_protocols TLS_PROTOCOLS_PLACEHOLDER;
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
        proxy_set_header Host WEBVH_PLACEHOLDER;
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

EONGINX
        if [ "$front" = "1" ]; then
            cat << 'EONGINX'
# Основной домен при WEB на отдельном имени: сюда telemt пробрасывает всё, что
# не прошло FakeTLS (браузеры, сканеры), с PROXY-заголовком — это обычный сайт.
server {
    listen 127.0.0.1:SSL_PORT_PLACEHOLDER ssl http2 proxy_protocol;
    server_name DOMAIN_PLACEHOLDER;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;

EONGINX
            printf '%s\n\n%s\n}\n\n' "$ssl_block" "$site_body"
        fi
        cat << 'EONGINX'
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

EONGINX
        printf '%s\n}\n' "$site_body"
        } >> "$NGINX_SITE_CONF"
    else
        cat >> "$NGINX_SITE_CONF" << 'EONGINX'
server {
    listen 127.0.0.1:SSL_PORT_PLACEHOLDER ssl http2;
    server_name DOMAIN_PLACEHOLDER;

    # SSL сертификаты
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;

    # Современные TLS настройки
    ssl_protocols TLS_PROTOCOLS_PLACEHOLDER;
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
    sed -i "s|ACME_NAMES_PLACEHOLDER|${acme_names}|g" "$NGINX_SITE_CONF"
    # tls_protocols — один из двух литералов nginx_tls_protocols, не сырой ввод
    sed -i "s|TLS_PROTOCOLS_PLACEHOLDER|${tls_protocols}|g" "$NGINX_SITE_CONF"
    if [ "$front" = "1" ]; then
        sed -i "s|WEBVH_PLACEHOLDER|${web_host}|g; s|WEBLISTEN_PLACEHOLDER|${WEB_FRONT_TLS_PORT:-8445}|g" "$NGINX_SITE_CONF"
    else
        sed -i "s|WEBVH_PLACEHOLDER|${escaped_domain}|g; s|WEBLISTEN_PLACEHOLDER|${proxy_port}|g" "$NGINX_SITE_CONF"
    fi

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
        # Долгие действия панели (включение WEB, имя WEB с выпуском сертификата)
        # идут минутами: на 60 с nginx отдавал 504, а действие шло дальше
        proxy_read_timeout 600s;
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

    # Разводка 443 по имени живёт в stream-конфиге и следует за тем же фактом
    # конфига ядра: без неё ядро на loopback, и 443 никто бы не слушал.
    # Строго после того, как сайт дописан целиком: проверка stream-модуля
    # читает конфиг nginx, а недописанный файл (плейсхолдер мини-аппа) её роняет.
    if [ "$front" = "1" ]; then
        web_front_write_stream_config "$web_host" || return 1
    else
        web_front_remove_stream_config
    fi

    log_success "nginx конфиг создан для $domain"
}

# ── Каталог ACME (webroot certbot) ───────────────────────────────────────────
# nginx (www-data) отдаёт /.well-known/acme-challenge/ из ACME_WEBROOT. Каталог,
# созданный root через mkdir -p при umask 077/027, получал 700/750 — nginx
# отвечал 403, и падали выпуск под имя WEB и продление сертификата (certbot
# renew идёт тем же webroot). certbot свои каталоги создаёт 755, но уже
# существующие не чинит.
: "${ACME_WEBROOT:=/var/www/certbot}"

# «режим uid» пути: GNU stat, иначе BSD (selftest на macOS). $2 — «-L»: по симлинку.
_acme_stat() {
    if stat -c %a / >/dev/null 2>&1; then stat ${2:+"$2"} -c '%a %u' -- "$1" 2>/dev/null
    else stat ${2:+"$2"} -f '%Lp %u' -- "$1" 2>/dev/null; fi
}

# В каталоге никто, кроме root (или нас), не подменит запись симлинком: владелец
# root/мы, записи для группы и остальных нет. $2 — «-L» (родитель /var/www может
# быть ссылкой, её ставит root).
_acme_trusted() {
    local m u
    read -r m u <<<"$(_acme_stat "$1" "${2:-}")"
    [[ "$m" =~ ^[0-7]+$ ]] || return 1
    { [ "$u" = 0 ] || [ "$u" = "$(id -u)" ]; } && [ $(( 8#$m & 8#022 )) -eq 0 ]
}

# Подготовить webroot: ACME_WEBROOT, .well-known, .well-known/acme-challenge — 755
# (нет — создать, есть — chmod); родителю (/var/www) — только недостающий o+x.
# Симлинки не трогаем и по ним не идём; каталог, родитель которого открыт на
# запись не только root, не трогаем (симлинк подложили бы между проверкой и
# chmod). rc 1 — что-то осталось как было (причина в журнале).
acme_webroot_prepare() {
    local base="$ACME_WEBROOT" parent d m at=""
    parent=$(dirname -- "$base")
    if [ ! -e "$parent" ] && [ ! -L "$parent" ]; then
        { _acme_trusted "$(dirname -- "$parent")" -L && mkdir -m 755 -- "$parent" 2>/dev/null; } || at="$parent"
    elif [ -d "$parent" ] && [ ! -L "$parent" ]; then
        read -r m _ <<<"$(_acme_stat "$parent")"
        if [[ "$m" =~ ^[0-7]+$ ]] && [ $(( 8#$m & 1 )) -eq 0 ]; then
            { _acme_trusted "$(dirname -- "$parent")" -L && chmod o+x "$parent" 2>/dev/null; } || at="$parent"
        fi
    fi
    for d in "$base" "$base/.well-known" "$base/.well-known/acme-challenge"; do
        if [ -L "$d" ] || ! _acme_trusted "$(dirname -- "$d")" -L; then at="$d"; break; fi
        if [ ! -e "$d" ]; then
            mkdir -m 755 -- "$d" 2>/dev/null || { at="$d"; break; }
        elif [ -d "$d" ]; then
            read -r m _ <<<"$(_acme_stat "$d")"
            [ "$m" = 755 ] || chmod 755 "$d" 2>/dev/null || { at="$d"; break; }
        else
            at="$d"; break
        fi
    done
    [ -z "$at" ] && return 0
    type log_to_file >/dev/null 2>&1 \
        && log_to_file "acme: webroot left as is at ${at} (symlink, foreign owner, parent writable by others or not a directory) — nginx may answer 403 to Let's Encrypt"
    return 1
}

# Каталог $1 проходим для nginx (www-data): x для остальных или владелец www-data с u+x.
acme_web_enters() {
    local m u wu
    read -r m u <<<"$(_acme_stat "$1" -L)"
    [[ "$m" =~ ^[0-7]+$ ]] || return 1
    [ $(( 8#$m & 1 )) -ne 0 ] && return 0
    wu=$(id -u www-data 2>/dev/null) || return 1
    [ "$u" = "$wu" ] && [ $(( 8#$m & 8#100 )) -ne 0 ]
}

# acme_webroot_prepare что-то не тронул, но nginx всё равно проходит: родитель и
# существующие ACME_WEBROOT и подкаталоги проходимы (например, владелец
# www-data). Симлинк — «нет»: его не проверяем и не чиним. Каталога нет — не
# беда, certbot создаст его сам (755).
acme_webroot_web_ok() {
    local d
    acme_web_enters "$(dirname -- "$ACME_WEBROOT")" || return 1
    for d in "$ACME_WEBROOT" "$ACME_WEBROOT/.well-known" "$ACME_WEBROOT/.well-known/acme-challenge"; do
        [ -L "$d" ] && return 1
        [ -e "$d" ] || continue
        { [ -d "$d" ] && acme_web_enters "$d"; } || return 1
    done
}

# Временные файлы шага при Ctrl+C, обрыве SSH или TERM: ловушка INT/TERM/HUP
# выполняет $1 (уборку), возвращает прежние ловушки ($2 — вывод
# «trap -p INT TERM HUP» до вызова) и повторяет сигнал себе — дальше его
# обрабатывают они или умолчание (завершение), как без нас. Снять — _gt_tmp_untrap.
_gt_tmp_trap() {
    local s
    for s in INT TERM HUP; do
        # shellcheck disable=SC2064  # уборка и прежние ловушки — значения на момент вызова
        trap "$1; trap - INT TERM HUP; eval $(printf '%q' "$2"); kill -s $s \$BASHPID" "$s"
    done
}
_gt_tmp_untrap() { trap - INT TERM HUP; eval "$1"; }

# Вся цепочка webroot — настоящие каталоги, куда пишет только root (или мы):
# файл самопроверки можно класть без гонки с симлинком.
_acme_chain_trusted() {
    local d
    _acme_trusted "$(dirname -- "$ACME_WEBROOT")" -L || return 1
    for d in "$ACME_WEBROOT" "$ACME_WEBROOT/.well-known" "$ACME_WEBROOT/.well-known/acme-challenge"; do
        [ -d "$d" ] && [ ! -L "$d" ] && _acme_trusted "$d" || return 1
    done
}

# Самопроверка до certbot: случайный файл из acme-challenge запрашиваем у nginx
# по http на имя $1 — на 127.0.0.1:80 (--resolve, без DNS), мимо прокси.
# rc: 0 — отдан наш файл; 3 — 403 (у nginx нет доступа к каталогу); 4 — другой
# код или чужое тело (имя обслуживает не блок goTelegram на :80); 5 — нет ответа
# (80 не слушается); 2 — проверить нельзя (нет curl, имя не имя, каталог не
# наш) — тогда решает certbot.
acme_local_probe() {
    local host="$1" dir="$ACME_WEBROOT/.well-known/acme-challenge" f tok out code body traps
    command -v curl >/dev/null 2>&1 || return 2
    validate_domain "$host" 2>/dev/null || return 2
    _acme_chain_trusted || return 2
    traps=$(trap -p INT TERM HUP)
    f=$(mktemp "$dir/gtprobe-XXXXXXXXXXXX" 2>/dev/null) || return 2
    _gt_tmp_trap "rm -f -- $(printf '%q' "$f")" "$traps"
    tok=${f##*/}
    if ! printf '%s' "$tok" > "$f" || ! chmod 644 "$f"; then rm -f -- "$f"; _gt_tmp_untrap "$traps"; return 2; fi
    # -q первым: ~/.curlrc root (-L, -o, прокси) исказил бы ответ
    out=$(curl -q -s --noproxy '*' --max-time "${ACME_PROBE_TIMEOUT:-5}" -w '\n%{http_code}' \
        --resolve "${host}:80:127.0.0.1" "http://${host}/.well-known/acme-challenge/${tok}" 2>/dev/null)
    rm -f -- "$f"; _gt_tmp_untrap "$traps"
    code=${out##*$'\n'}; body=${out%$'\n'*}
    [ "$out" = "$code" ] && body=""
    case "$code" in
        200) [ "$body" = "$tok" ] && return 0; return 4 ;;
        403) return 3 ;;
        [1-9][0-9][0-9]) return 4 ;;
        *) return 5 ;;
    esac
}

# Причина отказа certbot по его выводу: ratelimit | dns | port80 | perm | other.
# port80 — Let's Encrypt не соединился с портом 80 сервера (а не certbot с
# Let's Encrypt: «Failed to establish a new connection» — other).
certbot_fail_kind() {
    local f="$1"
    if grep -qiE 'rateLimited|rate.?limit|too many (certificates|failed authori[sz]ations|new orders|registrations|requests)' "$f" 2>/dev/null; then
        echo ratelimit
    elif grep -qE 'Type:[[:space:]]*dns|acme:error:dns|DNS problem|NXDOMAIN|SERVFAIL' "$f" 2>/dev/null; then
        echo dns
    elif grep -qE 'Type:[[:space:]]*connection|acme:error:connection|Timeout during connect|Timeout after connect|likely firewall problem|(Detail:|Fetching http).*(Connection refused|Connection reset|[Tt]imeout)' "$f" 2>/dev/null; then
        echo port80
    elif grep -qE 'Type:[[:space:]]*unauthorized|acme:error:unauthorized|Invalid response from' "$f" 2>/dev/null; then
        echo perm
    else
        echo other
    fi
}

# Одна короткая строка ошибки certbot: Detail:, иначе последняя строка с ошибкой,
# иначе последняя непустая — обе без служебного шума certbot. Заголовок «An
# unexpected error occurred:» заменяется следующей непустой строкой (сама ошибка).
# Без управляющих символов и «\» (log_* печатают через echo -e), не длиннее 200 символов.
certbot_short_err() {
    local f="$1" line next
    local noise='some challenges have failed|ask for help|logfile|letsencrypt\.log|re-run certbot|saving debug log|^hint'
    line=$(grep -m1 -E '^[[:space:]]*Detail:' "$f" 2>/dev/null | sed -E 's/^[[:space:]]*Detail:[[:space:]]*//')
    [ -n "$line" ] || line=$(grep -iE 'error|failed|timeout|unable|could not|denied' "$f" 2>/dev/null \
        | grep -viE "$noise" | tail -n 1)
    [ -n "$line" ] || line=$(grep -vE '^[[:space:]]*$' "$f" 2>/dev/null | grep -viE "$noise" | tail -n 1)
    if printf '%s' "$line" | grep -qiE '^[[:space:]]*an unexpected error occurred:?[[:space:]]*$'; then
        next=$(awk 'hit; tolower($0) ~ /^[[:space:]]*an unexpected error occurred:?[[:space:]]*$/ { hit = 1 }' "$f" 2>/dev/null \
            | grep -vE '^[[:space:]]*$' | grep -viE "$noise" | head -n 1)
        [ -n "$next" ] && line=$next
    fi
    line=$(printf '%s' "$line" | tr -d '\000-\037\177\\')
    line=${line#"${line%%[![:space:]]*}"}
    [ "${#line}" -gt 200 ] && line="${line:0:200}…"
    printf '%s' "$line"
}

# Текст отказа certbot для человека. $1 — файл вывода, $2 — код certbot, $3 — имя.
certbot_fail_text() {
    local f="$1" rc="$2" host="$3" when wait line
    case "$(certbot_fail_kind "$f")" in
        port80) tf web_cert_port80 "$host" ;;
        perm)   tf web_cert_perm "$host" "$ACME_WEBROOT" ;;
        dns)    tf web_cert_dns "$host" ;;
        ratelimit)
            when=$(grep -oE 'retry after [0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2})?(Z| UTC)?' "$f" 2>/dev/null \
                | head -n 1 | sed 's/^retry after //')
            if [ -n "$when" ]; then wait=$(tf web_cert_wait_until "$when")
            elif grep -qiE 'failed authori[sz]ations|failed validation' "$f" 2>/dev/null; then wait=$(t web_cert_wait_hour)
            else wait=$(t web_cert_wait_week); fi
            tf web_cert_ratelimit "$wait" ;;
        *)
            if [ "$rc" = 124 ]; then line="timeout 100 s"; else line=$(certbot_short_err "$f"); fi
            if [ -n "$line" ]; then printf '%s certbot: %s' "$(tf web_host_cert "$host")" "$line"
            else tf web_host_cert "$host"; fi ;;
    esac
}

# ── Временный конфиг (до получения SSL) ──────────────────────────────────────
generate_nginx_temp_config() {
    local domain="$1"

    # Временный конфиг без SSL затирает действующий (выпуск сертификата под новое
    # имя на живом сервере) — ручную фиксацию TLSv1.2 сохранить до этого.
    nginx_tls_adopt_manual || true

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
    acme_webroot_prepare || true
}

# ── Получение SSL сертификата ────────────────────────────────────────────────
obtain_ssl_certificate() {
    local domain="$1"
    local email="${2:-}"

    # И при уже выпущенном сертификате: продление идёт тем же webroot
    acme_webroot_prepare || true
    if [ ! -d "/etc/letsencrypt/live/$domain" ]; then
        log_info "Получение SSL сертификата для $domain..."

        # Временный конфиг для ACME challenge
        generate_nginx_temp_config "$domain"
        systemctl restart nginx 2>/dev/null

        local certbot_args=(
            certonly
            --webroot
            -w "$ACME_WEBROOT"
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
    acme_webroot_prepare || true
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

# ── Самопроверка сайта с самого сервера ──────────────────────────────────────
# Без DNS: у части хостеров в /etc/resolv.conf первым стоит резолвер, теряющий
# запросы, glibc ждёт 5 с перед следующим, и проверка падала по таймауту при
# живом сайте (29.10.2026). 443 слушается на 0.0.0.0 во всех вариантах Pro
# (ядро, nginx stream разводки WEB, общий 443 с 3x-ui), поэтому 127.0.0.1:443
# с SNI домена проходит ту же цепочку внутри сервера (443 → ядро/nginx → сайт).
# DNS, сеть хостера и внешний фаервол эта проверка не видит — DNS проверяют
# отдельные плашки в шапке. --noproxy: при https_proxy в окружении curl ушёл бы
# в прокси, и --resolve не действовал бы. Успех — любой HTTP-ответ.
site_local_ok() {
    local domain="$1"
    [ -n "$domain" ] || return 1
    curl -sk -o /dev/null --max-time "${SITE_PROBE_TIMEOUT:-5}" --noproxy '*' \
        --resolve "${domain}:443:127.0.0.1" "https://${domain}/" 2>/dev/null
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
    # Разводка 443 без ядра за ней держала бы порт и слала всё в пустоту
    web_front_remove_stream_config
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
