#!/usr/bin/env bash
# lib/logbudget.sh — жёсткий бюджет логов: не более 300 МБ на компонент, из них
# половина — живой несжатый слой, половина — сжатый архив с постоянной ротацией.
#
# Зачем это вообще. На боевом сервере /var/log разросся до 552 МБ (atop 273 МБ,
# journal 228 МБ). Это не косметика: когда раздел забивается, nginx и sshd встают
# на записи в лог — принимают TCP и молчат, а telemt продолжает работать на уже
# установленных сокетах. Снаружи выглядит как «прокси жив, а сервер недоступен»,
# и по SSH на такую машину уже не зайти. Ротация тут — не гигиена, а доступность.
#
# Три слоя, по убыванию отдачи:
#   1) резать в источнике  — 98% журнала telemt это три вида рутинных INFO;
#   2) объявленные потолки — journald + logrotate, ротация по размеру, а не по дате;
#   3) сторож              — таймер считает ФАКТ и подрезает, если вышли за бюджет.
#
# Третий слой нужен именно потому, что logrotate срабатывает раз в сутки: между
# запусками всплеск трафика успевает выесть диск, и «объявленный» потолок не
# спасает. Сторож ходит каждые 15 минут и смотрит на реальные байты.

# ── Бюджеты ──────────────────────────────────────────────────────────────────
LOGB_COMPONENT_MB="${LOGB_COMPONENT_MB:-300}"   # потолок на КАЖДЫЙ компонент
LOGB_LIVE_MB="${LOGB_LIVE_MB:-150}"             # из них живой (несжатый) слой
LOGB_KEEP_FREE_MB="${LOGB_KEEP_FREE_MB:-1024}"  # аварийный порог свободного места
LOGB_FILE_MB="${LOGB_FILE_MB:-50}"              # размер, на котором файл ротируется
LOGB_SWEEP_SVC="gotelegram-logbudget"
LOGB_STATE="/run/gotelegram-logbudget.state"

# Компоненты: имя|путь|маска. Пустая маска = каталог принадлежит компоненту
# целиком. Непустая = каталог ОБЩИЙ, и трогать в нём можно только своё.
#
# Это не мелочь: /var/log общий с системой, и без маски сюда попадут syslog.1,
# auth.log.2 и прочее чужое — а сторож их сожмёт и удалит. Маска покрывает и
# живые файлы, и ротированные (gotelegram-warp.log.3.gz тоже начинается с
# gotelegram), поэтому она задаётся префиксом, а не полным именем.
# atop чужой (ставит хостер, не мы), но каталог у него свой и съел он больше всех.
_logb_components() {
    cat <<'EOF'
journal|/var/log/journal|
nginx|/var/log/nginx|access.log* error.log*
gotelegram|/var/log|gotelegram*
atop|/var/log/atop|
EOF
}

_logb_mb() { echo $(( ${1:-0} / 1024 / 1024 )); }

# _logb_bytes <путь> [маска] — размер в байтах. С маской считаем только своё,
# без неё — каталог целиком. du -sb есть не везде (busybox), отсюда запасной путь.
# Маска может содержать НЕСКОЛЬКО шаблонов через пробел. Одного не хватало для
# общего каталога вроде /var/log/nginx, где рядом лежат логи чужих сайтов:
# сожительство с 3x-ui на 443 — штатный сценарий продукта, и сносить архивы
# соседа мы права не имеем. Собираем для find выражение вида
#   \( -name a -o -name b \)
_LOGB_NAME_ARGS=()
_logb_set_name_args() {
    local mask="${1:-}" pat first=1
    _LOGB_NAME_ARGS=()
    [ -n "$mask" ] || return 0
    _LOGB_NAME_ARGS=( '(' )
    for pat in $mask; do
        [ "$first" = "1" ] || _LOGB_NAME_ARGS+=( -o )
        _LOGB_NAME_ARGS+=( -name "$pat" )
        first=0
    done
    _LOGB_NAME_ARGS+=( ')' )
}

_logb_bytes() {
    local p="${1:-}" mask="${2:-}"
    [ -e "$p" ] || { echo 0; return 0; }
    if [ -n "$mask" ]; then
        _logb_set_name_args "$mask"
        find "$p" -maxdepth 1 -type f "${_LOGB_NAME_ARGS[@]}" -printf '%s\n' 2>/dev/null \
            | awk '{s+=$1} END{print s+0}'
        return 0
    fi
    du -sb "$p" 2>/dev/null | awk '{print $1+0; exit}' && return 0
    find "$p" -type f -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{print s+0}'
}

_logb_free_mb() {
    df -Pm /var/log 2>/dev/null | awk 'NR==2 {print $4+0; exit}'
}

# ── Слой 1: резать в источнике ───────────────────────────────────────────────
# telemt собран на tracing_subscriber и понимает RUST_LOG с директивами по модулям.
# Суточный замер (боевой сервер, 227 100 строк): ME key derivation 75 997,
# RPC handshake OK 75 941, Idle writer refreshed 69 759 — 98% объёма на три вида
# рутинных сообщений об УСПЕХЕ. Глушим их до warn: отказы этих же модулей
# (handshake timeout, ME init incomplete) остаются, потому что они выше уровнем.
# Всё остальное — pool_init, config_updater, me_startup, listeners, desync —
# не трогаем, это как раз то, по чему разбирают аварии.
LOGB_RUST_LOG="${LOGB_RUST_LOG:-info,telemt::transport::middle_proxy::health=warn,telemt::transport::middle_proxy::handshake=warn}"

logb_apply_source_cut() {
    local dropin="/etc/systemd/system/telemt.service.d/20-logbudget.conf"
    local want="[Service]
Environment=RUST_LOG=${LOGB_RUST_LOG}"
    [ -f /etc/systemd/system/telemt.service ] || [ -f /lib/systemd/system/telemt.service ] || return 0
    if [ ! -f "$dropin" ] || [ "$(cat "$dropin" 2>/dev/null)" != "$want" ]; then
        mkdir -p "$(dirname "$dropin")" 2>/dev/null
        printf '%s\n' "$want" > "$dropin" 2>/dev/null || return 1
        systemctl daemon-reload 2>/dev/null || true
        return 10   # 10 = «нужен перезапуск telemt, чтобы применилось»
    fi
    return 0
}

logb_revert_source_cut() {
    rm -f /etc/systemd/system/telemt.service.d/20-logbudget.conf 2>/dev/null
    systemctl daemon-reload 2>/dev/null || true
}

# Проверка, что урезание действительно сработало: считаем строки за окно.
# Директивы RUST_LOG молча игнорируются, если сборка без env-filter, — поэтому
# не верим на слово, а меряем. Возвращает «строк_в_минуту».
logb_measure_rate() {
    local window="${1:-60}" a b
    a=$(journalctl -u telemt --since "-${window} seconds" --no-pager 2>/dev/null | wc -l)
    sleep "$window"
    b=$(journalctl -u telemt --since "-${window} seconds" --no-pager 2>/dev/null | wc -l)
    echo $(( (a + b) * 30 / window ))
}

# ── Слой 2: объявленные потолки ──────────────────────────────────────────────
logb_apply_journald() {
    local jd="/etc/systemd/journald.conf.d/99-gotelegram.conf"
    # SystemMaxUse — жёсткий потолок на активные+архивные вместе.
    # SystemMaxFileSize — ротация по размеру, не по дате: всплеск режется сразу.
    # SystemKeepFree — главная защита: journald отступает, не добивая раздел.
    local want="[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=${LOGB_COMPONENT_MB}M
SystemMaxFileSize=${LOGB_FILE_MB}M
SystemMaxFiles=$(( LOGB_COMPONENT_MB / LOGB_FILE_MB ))
SystemKeepFree=${LOGB_KEEP_FREE_MB}M
MaxFileSec=1day
RateLimitIntervalSec=30s
RateLimitBurst=5000"
    if [ ! -f "$jd" ] || [ "$(cat "$jd" 2>/dev/null)" != "$want" ]; then
        mkdir -p /etc/systemd/journald.conf.d 2>/dev/null
        printf '%s\n' "$want" > "$jd" 2>/dev/null || return 1
        systemctl restart systemd-journald 2>/dev/null || true
        journalctl --vacuum-size="${LOGB_COMPONENT_MB}M" >/dev/null 2>&1 || true
        return 1
    fi
    return 0
}

logb_apply_logrotate() {
    local changed=0 f want rot
    # Живой слой ≤ LOGB_LIVE_MB, архив — остальное. При файле в LOGB_FILE_MB это
    # (300-150)/50*10 поколений: сжатые весят примерно вдесятеро меньше исходных.
    rot=$(( (LOGB_COMPONENT_MB - LOGB_LIVE_MB) / LOGB_FILE_MB * 10 ))
    [ "$rot" -lt 3 ] && rot=3

    f="/etc/logrotate.d/gotelegram-nginx"
    if [ -d /var/log/nginx ]; then
        want="/var/log/nginx/*.log {
    su root adm
    hourly
    maxsize ${LOGB_FILE_MB}M
    rotate ${rot}
    missingok
    notifempty
    compress
    delaycompress
    create 0640 www-data adm
    sharedscripts
    postrotate
        [ -f /run/nginx.pid ] && kill -USR1 \$(cat /run/nginx.pid) 2>/dev/null || true
    endscript
}"
        if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
            printf '%s\n' "$want" > "$f" 2>/dev/null && changed=1
            # дебиановский дефолт (14 суток, без потолка по размеру) уводим с дороги
            [ -f /etc/logrotate.d/nginx ] && mv -f /etc/logrotate.d/nginx /etc/logrotate.d/nginx.gotelegram-disabled 2>/dev/null
        fi
    fi

    # Собственные логи продукта до сих пор не ротировались вообще.
    # gotelegram-warp.log держит сырой вывод wgcf (там бывает ключ) — 600 и su root.
    f="/etc/logrotate.d/gotelegram"
    want="/var/log/gotelegram*.log {
    su root root
    hourly
    maxsize ${LOGB_FILE_MB}M
    rotate ${rot}
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    create 0600 root root
}"
    if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$f" 2>/dev/null && changed=1
    fi

    # atop: 28 поколений по ~10 МБ = 273 МБ. Число поколений считаем по ФАКТИЧЕСКОМУ
    # среднему размеру файла на этой машине — «поделить бюджет на 10 МБ» дало бы 30,
    # то есть на живом сервере правка не урезала бы, а НАРАСТИЛА хранение.
    # И только вниз: сам сбор не трогаем, он бывает нужен хостеру для разбора аварий.
    if [ -f /etc/default/atop ] && [ -d /var/log/atop ]; then
        local cnt sz avg gen cur
        cnt=$(find /var/log/atop -maxdepth 1 -type f -name 'atop_*' 2>/dev/null | wc -l)
        sz=$(_logb_bytes /var/log/atop)
        if [ "${cnt:-0}" -gt 0 ] && [ "${sz:-0}" -gt 0 ]; then
            avg=$(( sz / cnt )); [ "$avg" -lt 1048576 ] && avg=1048576
            gen=$(( LOGB_COMPONENT_MB * 1024 * 1024 / avg ))
        else
            gen=$(( LOGB_COMPONENT_MB / 10 ))
        fi
        [ "$gen" -lt 3 ] && gen=3
        cur=$(grep -oE '^LOGGENERATIONS=[0-9]+' /etc/default/atop 2>/dev/null | cut -d= -f2)
        if [ -n "$cur" ] && [ "$cur" -le "$gen" ] 2>/dev/null; then
            : # уже не больше нашего потолка — не трогаем
        elif [ -n "$cur" ]; then
            sed -i -E "s/^LOGGENERATIONS=.*/LOGGENERATIONS=${gen}/" /etc/default/atop 2>/dev/null && changed=1
        else
            printf 'LOGGENERATIONS=%s\n' "$gen" >> /etc/default/atop 2>/dev/null && changed=1
        fi
    fi

    # logrotate по умолчанию ходит раз в сутки — для «maxsize» этого мало.
    # Свой таймер раз в час: тогда hourly+maxsize действительно работают.
    local lt="/etc/systemd/system/gotelegram-logrotate.timer"
    want="[Unit]
Description=goTelegram: ротация логов по размеру каждый час
[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
AccuracySec=1min
[Install]
WantedBy=timers.target"
    if [ ! -f "$lt" ] || [ "$(cat "$lt" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$lt" 2>/dev/null && changed=1
    fi
    local ls_="/etc/systemd/system/gotelegram-logrotate.service"
    want="[Unit]
Description=goTelegram: ротация логов по размеру
[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate /etc/logrotate.d/gotelegram /etc/logrotate.d/gotelegram-nginx"
    if [ ! -f "$ls_" ] || [ "$(cat "$ls_" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$ls_" 2>/dev/null && changed=1
    fi

    if [ "$changed" = "1" ]; then
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable --now gotelegram-logrotate.timer >/dev/null 2>&1 || true
    fi
    return 0
}

# ── Слой 3: сторож ───────────────────────────────────────────────────────────
# Считает реальные байты и подрезает. Порядок сноса: сначала жмём то, что ещё
# не сжато, потом удаляем самые старые архивы. Активный файл не трогаем никогда —
# его отдаёт ротация, а не мы: обрезать файл под открытым дескриптором значит
# получить дыру в логе и разъехавшийся offset у пишущего процесса.
_logb_trim_dir() {
    local dir="${1:-}" mask="${2:-}" budget_b="$3" used f
    [ -d "$dir" ] || return 0
    # Маска обязательна для общих каталогов. Пустая означает «каталог наш целиком»,
    # и тогда границей служит сам путь — в /var/log с пустой маской не заходим.
    local scope=( -maxdepth 1 -type f )
    if [ -n "$mask" ]; then
        _logb_set_name_args "$mask"
        scope=( "${scope[@]}" "${_LOGB_NAME_ARGS[@]}" )
    fi
    if [ -z "$mask" ] && [ "$dir" = "/var/log" ]; then
        return 0   # предохранитель: без маски /var/log — это чужие логи тоже
    fi

    used=$(_logb_bytes "$dir" "$mask")
    [ "$used" -le "$budget_b" ] && return 0

    # 1) дожать несжатое из уже ротированных (имена вида *.1, *.log.2 и т.п.).
    #    Активный файл (без числового хвоста) не трогаем: обрезать файл под
    #    открытым дескриптором — это дыра в логе и разъехавшийся offset у писателя.
    find "$dir" "${scope[@]}" -regextype posix-extended -regex '.*\.[0-9]+$' \
         ! -name '*.gz' ! -name '*.xz' ! -name '*.zst' \
         -print0 2>/dev/null | xargs -0 -r gzip -9 2>/dev/null || true

    used=$(_logb_bytes "$dir" "$mask")
    [ "$used" -le "$budget_b" ] && return 0

    # 2) сносить самые старые архивы, пока не уложимся
    while [ "$used" -gt "$budget_b" ]; do
        f=$(find "$dir" "${scope[@]}" \( -name '*.gz' -o -name '*.xz' -o -name '*.zst' \) \
              -printf '%T@ %p\n' 2>/dev/null | sort -n | head -1 | cut -d' ' -f2-)
        [ -n "$f" ] || break
        rm -f "$f" 2>/dev/null || break
        used=$(_logb_bytes "$dir" "$mask")
    done
    return 0
}

logb_enforce() {
    local line name path mask used budget_b trimmed="" free
    budget_b=$(( LOGB_COMPONENT_MB * 1024 * 1024 ))

    # journald подрезаем его же средствами — руками в /var/log/journal лезть нельзя.
    used=$(_logb_bytes /var/log/journal)
    if [ "$used" -gt "$budget_b" ]; then
        journalctl --vacuum-size="${LOGB_COMPONENT_MB}M" >/dev/null 2>&1 || true
        trimmed="${trimmed}journal "
    fi

    while IFS='|' read -r name path mask; do
        [ -n "$name" ] || continue
        [ "$name" = "journal" ] && continue
        used=$(_logb_bytes "$path" "$mask")
        if [ "$used" -gt "$budget_b" ]; then
            _logb_trim_dir "$path" "$mask" "$budget_b"
            trimmed="${trimmed}${name} "
        fi
    done <<< "$(_logb_components)"

    # Аварийный режим: место кончается — режем всех вдвое, не дожидаясь бюджетов.
    # Именно этот сценарий кладёт nginx и sshd, поэтому он важнее любых потолков.
    free=$(_logb_free_mb)
    if [ -n "$free" ] && [ "$free" -lt "$LOGB_KEEP_FREE_MB" ]; then
        journalctl --vacuum-size="$(( LOGB_COMPONENT_MB / 2 ))M" >/dev/null 2>&1 || true
        while IFS='|' read -r name path mask; do
            [ -n "$name" ] || continue
            [ "$name" = "journal" ] && continue
            _logb_trim_dir "$path" "$mask" $(( budget_b / 2 ))
        done <<< "$(_logb_components)"
        trimmed="${trimmed}(аварийно: свободно ${free}МБ) "
        type log_warning >/dev/null 2>&1 && \
            log_warning "Место на /var/log кончалось (${free} МБ) — логи урезаны вдвое" >&2
    fi

    printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "${trimmed:-в бюджете}" > "$LOGB_STATE" 2>/dev/null || true
    return 0
}

logb_install_timer() {
    local changed=0 want
    want="[Unit]
Description=goTelegram: сторож бюджета логов
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'source ${GOTELEGRAM_DIR:-/opt/gotelegram}/lib/logbudget.sh && logb_enforce'"
    local f="/etc/systemd/system/${LOGB_SWEEP_SVC}.service"
    if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$f" 2>/dev/null && changed=1
    fi
    want="[Unit]
Description=goTelegram: сторож бюджета логов каждые 15 минут
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
AccuracySec=1min
[Install]
WantedBy=timers.target"
    f="/etc/systemd/system/${LOGB_SWEEP_SVC}.timer"
    if [ ! -f "$f" ] || [ "$(cat "$f" 2>/dev/null)" != "$want" ]; then
        printf '%s\n' "$want" > "$f" 2>/dev/null && changed=1
    fi
    if [ "$changed" = "1" ]; then
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable --now "${LOGB_SWEEP_SVC}.timer" >/dev/null 2>&1 || true
    fi
    return 0
}

# ── Отчёт (для меню, бота и панели) ──────────────────────────────────────────
# Печатает по строке на компонент: имя, занято МБ, бюджет МБ, доля.
logb_status() {
    local name path mask used mb pct
    while IFS='|' read -r name path mask; do
        [ -n "$name" ] || continue
        used=$(_logb_bytes "$path" "$mask"); mb=$(_logb_mb "$used")
        pct=$(( mb * 100 / (LOGB_COMPONENT_MB > 0 ? LOGB_COMPONENT_MB : 1) ))
        printf '%s %s %s %s\n' "$name" "$mb" "$LOGB_COMPONENT_MB" "$pct"
    done <<< "$(_logb_components)"
    printf 'free %s - -\n' "$(_logb_free_mb)"
}

# Единая точка входа: применить всё. Идемпотентно.
# Возвращает 10, если telemt нужно перезапустить (появилась новая RUST_LOG).
logb_apply_all() {
    local need_restart=0 rc
    logb_apply_source_cut; rc=$?; [ "$rc" = "10" ] && need_restart=1
    logb_apply_journald || true
    logb_apply_logrotate || true
    logb_install_timer || true
    logb_enforce || true
    [ "$need_restart" = "1" ] && return 10
    return 0
}
