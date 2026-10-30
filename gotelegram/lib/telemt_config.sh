#!/bin/bash
# GoTelegram v2.5.0 — Генерация TOML конфигурации для telemt

# ── Популярные домены (не заблокированные в РФ) ──────────────────────────────
QUICK_DOMAINS=(
    "google.com"
    "microsoft.com"
    "cloudflare.com"
    "apple.com"
    "amazon.com"
    "github.com"
    "stackoverflow.com"
    "medium.com"
    "wikipedia.org"
    "coursera.org"
    "udemy.com"
    "habr.com"
    "stepik.org"
    "duolingo.com"
    "khanacademy.org"
    "bbc.com"
    "reuters.com"
    "nytimes.com"
    "ted.com"
    "zoom.us"
)

# ── Генерация TOML конфига (telemt v3 формат) ───────────────────────────────
# v2.6.0: добавлены параметры маскировки, отлаженные на тестовом VPS 9 июня 2026 года.
# Стабильно работает с telemt 3.4.15 как через VPN, так и под жёсткий DPI:
#   * client_mss = "92" в [server] — узкий MSS-клэмп (MSS=92, segment_multiplier=16)
#   * mask_shape_hardening + buckets — padding для unknown SNI mask path
#   * mask_timing_normalization 180-320ms — timing envelope для masking
#   * server_hello_delay_min_ms = 20 (только min, без max — иначе Telegram-клиент дропает)
#   * mask_host = "127.0.0.1" явно (без него работает через dns_overrides, но явно надёжнее)
#   * [censorship.tls_fetch] profiles только (доп. параметры grease_enabled/strict_route ломали handshake)
#   * use_middle_proxy = true (use_middle_proxy=false ломал Telegram-клиент)
#   * NO mask_shape_above_cap_blur=true (флаг ломал MTProto reply-flight)
#   * NO tls_new_session_tickets (клиент не ждёт дополнительных tickets)
# ── WEB-прокси (Telegram Desktop 7.1+) ───────────────────────────────────────
# MTProxy-потоки внутри обычного HTTPS с нашим доменом. TLS терминирует nginx на
# 127.0.0.1:8443 и передаёт HTTP/1.1 на приватный listener telemt; обычные
# запросы telemt отправляет в decoy — тот же сайт-маскировка на 127.0.0.1:8444.
# Ядро умеет это с 3.5.5; ниже — только Pro с настоящим сертификатом.
WEB_LISTEN_PORT="${WEB_LISTEN_PORT:-18080}"
WEB_DECOY_PORT="${WEB_DECOY_PORT:-8444}"
# Предел WEB-профилей в ядре: по умолчанию у ядра 32 — меньше, чем ключей у многих установок.
WEB_MAX_PROFILES="${WEB_MAX_PROFILES:-4096}"
WEB_MIN_TELEMT="3.5.5"

telemt_web_supported() {
    local v
    v=$("${TELEMT_BIN:-/usr/local/bin/telemt}" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    [ -n "$v" ] || return 1
    [ "$(printf '%s\n%s\n' "$WEB_MIN_TELEMT" "$v" | sort -V | head -1)" = "$WEB_MIN_TELEMT" ]
}

# «Включён» — только флаг И Pro с доменом: после перехода в Lite флаг остаётся в
# config.json, а конфиг ядра уже без [web]; экраны врали бы «включён».
telemt_web_enabled() {
    [ "$(config_get web_enabled 2>/dev/null || echo "")" = "true" ] || return 1
    [ "$(config_get mode 2>/dev/null || echo lite)" = "pro" ] || return 1
    [ -n "$(config_get domain 2>/dev/null || echo "")" ]
}

# WEB фактически активен: включён владельцем И ядро умеет И есть публичный адрес.
# Именно по нему решают ОБА генератора — telemt и nginx. Раньше nginx смотрел
# только на флаг, telemt — ещё и на ядро/адрес: при даунгрейде ядра nginx ждал
# PROXY-заголовок, которого telemt не слал, и сайт с WEB лежали молча.
telemt_web_active() {
    telemt_web_enabled 2>/dev/null || return 1
    telemt_web_supported 2>/dev/null || return 1
    telemt_web_public_addr >/dev/null 2>&1
}

telemt_web_carrier() {
    local c; c=$(config_get web_carrier 2>/dev/null || echo "")
    case "$c" in https|https-lanes|websocket|websocket-lanes) echo "$c" ;; *) echo "https" ;; esac
}

# Публичный адрес vhost: ядро использует его как ключ внутреннего tuple relay и
# требует конкретный IP с портом 443. Берём адрес, с которого машина ходит
# наружу, — без обращения к внешним сервисам.
telemt_web_public_addr() {
    local ip
    ip=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}')
    # За NAT (облака с приватным адресом на интерфейсе) src — приватный; ядру
    # нужен публичный адрес hostname — спрашиваем снаружи.
    case "$ip" in
        10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*|169.254.*|127.*|"")
            ip=$(get_server_ip 2>/dev/null || echo "") ;;
    esac
    [ -n "$ip" ] && [ "$ip" != "0.0.0.0" ] || return 1
    printf '%s:443\n' "$ip"
}

# Профили WEB — по одному на каждый ключ из [access.users], тем же секретом.
# WEB-рукопожатие привязано к пользователю и режиму секрета профиля, поэтому
# один секрет служит и обычной ссылке (ee…), и WEB-ссылке (dd…).
telemt_web_render_profiles() {
    local users_block="$1" line name
    while IFS= read -r line; do
        name=$(printf '%s' "$line" | sed -E 's/^[[:space:]]*"?([A-Za-z0-9_.-]+)"?[[:space:]]*=.*/\1/')
        [ -n "$name" ] && [ "$name" != "$line" ] || continue
        printf '\n[[web.vhosts.profiles]]\nuser = "%s"\nsecret_mode = "dd"\n' "$name"
    done <<< "$users_block"
}

# Пересобрать профили из [access.users]. Вызывается из КАЖДОГО писателя секции
# ключей: профиль, ссылающийся на удалённого пользователя, делает конфиг
# невалидным, и следующий перезапуск telemt оставил бы прокси лежать. Когда WEB
# выключен (нет [[web.vhosts]]), просто убирает старые блоки профилей.
telemt_web_sync_profiles() {
    local config="${1:-$TELEMT_CONFIG}" tmp users profiles=""
    [ -f "$config" ] || return 1
    users=$(get_telemt_users_block "$config" 2>/dev/null || true)
    if grep -q '^\[\[web\.vhosts\]\]' "$config"; then
        profiles=$(telemt_web_render_profiles "$users")
        # WEB включён, а ключей нет — ядро такой конфиг не примет
        [ -n "$profiles" ] || return 1
    fi
    tmp=$(mktemp "${config}.XXXXXX") || return 1
    chmod 600 "$tmp" 2>/dev/null
    awk '
        /^\[\[web\.vhosts\.profiles\]\]/ { skip=1; next }
        /^\[/ && skip { skip=0 }
        skip { next }
        { print }
    ' "$config" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' > "$tmp" || { rm -f "$tmp"; return 1; }
    [ -n "$profiles" ] && printf '%s\n' "$profiles" >> "$tmp"
    # Конфиг, собранный до 2.10.46, без [web.limits]: с 33-м профилем ядро не
    # стартует. Дописываем лимит перед первым [[web.vhosts]] — так безопасно и
    # при добавлении ключа из бота/панели, не дожидаясь перегенерации.
    if grep -q '^\[\[web\.vhosts\]\]' "$tmp" && ! grep -q '^\[web\.limits\]' "$tmp"; then
        awk -v n="${WEB_MAX_PROFILES:-4096}" '!done && /^\[\[web\.vhosts\]\]/ { print "[web.limits]"; print "max_profiles = " n; print ""; done=1 } { print }' "$tmp" > "$tmp.lim" && mv -f "$tmp.lim" "$tmp"
    fi
    if [ ! -s "$tmp" ] || ! grep -q '^\[access\.users\]' "$tmp"; then rm -f "$tmp" 2>/dev/null; return 1; fi
    sync "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$config" || { rm -f "$tmp" 2>/dev/null; return 1; }
    chmod 600 "$config"
    return 0
}

generate_web_link() {
    local domain="$1" secret="$2"
    [ -n "$domain" ] && [ -n "$secret" ] || return 1
    echo "tg://webproxy?server=${domain}&secret=dd${secret}"
}

# WEB-ссылка для секрета, если WEB-прокси включён; иначе пусто.
# Ссылку даём только когда WEB реально собран в конфиге ядра: флаг мог
# остаться от ядра, которое WEB не умеет, и ссылка была бы мёртвой.
web_configured_in_core() {
    grep -q '^transport = "web"' "${TELEMT_CONFIG:-/etc/telemt/config.toml}" 2>/dev/null
}

web_link_for_secret() {
    local secret="$1" domain
    telemt_web_enabled 2>/dev/null || return 1
    web_configured_in_core || return 1
    domain=$(config_get domain 2>/dev/null || echo "")
    generate_web_link "$domain" "$secret"
}

# Печать WEB-ссылки под обычной: заголовок, ссылка, QR (если есть) и подсказка
# про предупреждение клиента. Молчит, пока WEB выключен.
show_web_link() {
    local secret="$1" with_qr="${2:-1}" link
    link=$(web_link_for_secret "$secret" 2>/dev/null) || return 0
    [ -n "$link" ] || return 0
    echo ""
    echo -e "  ${BOLD}${WHITE}$(t web_link_title)${NC}"
    echo -e "  ${GREEN}${link}${NC}"
    if [ "$with_qr" = "1" ] && command -v qrencode >/dev/null 2>&1; then
        echo ""; qrencode -t UTF8 -m 2 "$link" 2>/dev/null
    fi
    echo -e "  ${DIM}$(t web_hint)${NC}"
}

generate_telemt_toml() {
    local secret="$1"
    local port="${2:-443}"
    local mask_mode="${3:-lite}"   # lite | pro
    local mask_domain="${4:-google.com}"
    local mask_port="${5:-443}"
    local output="${6:-$TELEMT_CONFIG}"

    mkdir -p "$(dirname "$output")"

    # Пер-юзерные лимиты уникальных IP ([access.user_max_unique_ips]) пишут бот и
    # вебка. Раньше они стирались при ЛЮБОЙ перегенерации конфига (апгрейд, смена
    # пресета, смена домена) — снимаем слепок ДО перезаписи и вернём в конце.
    local preserved_ip_limits=""
    if [ -f "$output" ]; then
        preserved_ip_limits=$(get_telemt_ip_limits_block "$output" 2>/dev/null || true)
    fi

    # DNS override для pro: домен резолвится в 127.0.0.1
    # чтобы mask-трафик шёл на локальный nginx, а не в интернет
    local dns_line=""
    if [ "$mask_mode" = "pro" ]; then
        dns_line="dns_overrides = [\"${mask_domain}:${mask_port}:127.0.0.1\"]"
    fi

    # ── Fixed mask values (no per-installation jitter) ─────────────────────
    # We tried random per-installation jitter on bucket_floor/timing earlier,
    # but found a fail mode on 2026-06-09: random bucket_floor=1024 +
    # timing_normalization 225..350ms broke real Telegram clients while
    # mtproto-tunnel handshake still passed. With fixed 512/4096 + 180..320ms
    # the issue does not reproduce. Same values as the reference working
    # config on the reference server.
    local bucket_floor=512
    local bucket_cap=4096
    local timing_floor=180
    local timing_ceiling=320

    # tls_emulation: true для lite (нет реального mask_host), false для pro (есть nginx)
    local tls_emul
    if [ "$mask_mode" = "pro" ]; then
        tls_emul="false"
    else
        tls_emul="true"
    fi

    # public_host: в pro mode — реальный домен, в lite — IP сервера (потом пересоберём ссылки)
    local public_host_line=""
    if [ "$mask_mode" = "pro" ]; then
        public_host_line="public_host = \"${mask_domain}\""
    fi

    # ── Preset overrides («режим подбора», lib/diagnose.sh) ───────────────
    # По умолчанию — проверенный рабочий профиль .42 (client_mss=92=MSS92/x16, sh_delay=20).
    # Если в config.json задан preset_id и движок diagnose.sh загружен — берём
    # client_mss / timing / buckets / sh_delay / (опц.) tls_emulation из пресета.
    # Единый источник правды: и свежий install, и миграция зовут эту функцию.
    local client_mss="92"
    local sh_delay=20
    local tls_profiles='["modern_firefox_like", "compat_tls12"]'
    local reanim_synlimit_block="" reanim_timeouts_block="" reanim_tg_connect=""
    local _pid
    _pid=$(config_get preset_id 2>/dev/null || echo "")
    [ -z "$_pid" ] && _pid="${TELEMT_PRESET_PREF:-}"   # чистая установка: дефолтный пресет (MEKO), апгрейд его не трогает
    # Внешний SYN-фикс (mekofix V2/V3/V4) активен → встроенные synlimit и MSS ОТКЛЮЧАЕМ:
    # двойной лимит режет скорость медиа (см. README MEKO). Источник правды — config.mekofix.
    local _extfix; _extfix=$(config_get mekofix 2>/dev/null || echo "")
    if [ -n "$_pid" ] && type preset_params >/dev/null 2>&1; then
        local _pp _mss _cc _tf _tc _bf _bc _shd _tlsov _profile _synlimit _timeouts
        _pp=$(preset_params "$_pid" 2>/dev/null) || _pp=""
        if [ -n "$_pp" ]; then
            read -r _mss _cc _tf _tc _bf _bc _shd _tlsov _profile _synlimit _timeouts <<< "$_pp"
            [ "$_mss" = "off" ] && _mss=""
            client_mss="$_mss"
            timing_floor="$_tf"; timing_ceiling="$_tc"
            bucket_floor="$_bf"; bucket_cap="$_bc"; sh_delay="$_shd"
            [ "$_tlsov" = "true" ] && tls_emul="true"
            [ "$_tlsov" = "false" ] && tls_emul="false"
            case "$_profile" in
                chrome)        tls_profiles='["modern_chrome_like", "compat_tls12"]' ;;
                firefox-only)  tls_profiles='["modern_firefox_like"]' ;;
                chrome-only)   tls_profiles='["modern_chrome_like"]' ;;
                legacy)        tls_profiles='["legacy_minimal", "compat_tls12"]' ;;
            esac
            # ── Реаниматор (стабильность под DPI). Идея и параметры:
            #    MTproxy-reanimation (c) @LiafanX — github.com/Liafanx/MTproxy-reanimation
            #    У нас сделано НАТИВНО в ядре telemt: synlimit (= тот же per-IP nft SYN-лимит)
            #    + [timeouts]. Без внешних nft-скриптов и watchdog.
            [ -n "${_extfix:-}" ] && { _synlimit="off"; client_mss=""; log_dim "Внешний SYN-фикс (${_extfix}) активен → встроенные synlimit/MSS отключены"; }
            if [ "$_synlimit" = "on" ]; then
                local _slh=1 _slb=1   # жёсткий = рекомендация автора (rate 1/s, burst 1)
                case "$(config_get reanim_synlimit 2>/dev/null || echo hard)" in
                    mid)  _slh=1; _slb=3 ;;
                    soft) _slh=2; _slb=5 ;;
                esac
                reanim_synlimit_block=$(printf '\n# SYN-лимит на IP (нативно в telemt). (c) MTproxy-reanimation @LiafanX\n[[server.listeners]]\nip = "0.0.0.0"\nport = %s\nsynlimit = "nftables"\nsynlimit_seconds = 1\nsynlimit_hitcount = %s\nsynlimit_burst = %s\n' "$port" "$_slh" "$_slb")
            fi
            if [ "$_timeouts" = "on" ]; then
                reanim_tg_connect=$'\ntg_connect = 30'
                reanim_timeouts_block=$(printf '\n# Таймауты по рекомендации MTproxy-reanimation (c) @LiafanX\n[timeouts]\nclient_handshake = 90\nclient_keepalive = 120\n')
            fi
            log_dim "Preset #${_pid}: mss='${client_mss}' cc=${_cc} timing=${_tf}..${_tc} buckets=${_bf}/${_bc} shd=${_shd} tls=${_profile:-firefox} synlimit=${_synlimit:-off} timeouts=${_timeouts:-off}"
        fi
    fi
    local client_mss_toml="\"${client_mss}\""


    # telemt требует bucket_floor>0 при mask_shape_hardening=true (иначе
    # "censorship.mask_shape_bucket_floor_bytes must be > 0" и telemt НЕ стартует).
    # Пресеты «без шейпинга» (mss-off: bucket=0, timing=0) -> отключаем shaping
    # и нормализацию таймингов флагами, а значения держим валидными.
    local mask_hardening="true" timing_enabled="true"
    if [ "${bucket_floor:-0}" -le 0 ] 2>/dev/null; then mask_hardening="false"; bucket_floor=512; bucket_cap=4096; fi
    if [ "${timing_ceiling:-0}" -le 0 ] 2>/dev/null; then timing_enabled="false"; timing_floor=0; timing_ceiling=0; fi

    # ── WEB-режим ─────────────────────────────────────────────────────────
    # Ловушка ядра: listen_addr_ipv4 превращается в listener только при ПУСТОМ
    # [[server.listeners]]. Стоит добавить web-listener — основной 0.0.0.0:443
    # молча исчезает. Поэтому при включённом WEB оба listener'а пишем явно.
    local web_on=0 web_pub="" web_block="" web_censorship_line="" listeners_block=""
    local server_listen_line='listen_addr_ipv4 = "0.0.0.0"'
    if [ "$mask_mode" = "pro" ] && telemt_web_active; then
        web_pub=$(telemt_web_public_addr 2>/dev/null) && web_on=1
    fi
    if [ "$web_on" = "1" ]; then
        server_listen_line=""
        if [ -n "$reanim_synlimit_block" ]; then
            # SYN-лимит реаниматора уже описывает главный listener — не дублируем
            listeners_block="$reanim_synlimit_block"
            reanim_synlimit_block=""
        else
            listeners_block=$(printf '\n[[server.listeners]]\nip = "0.0.0.0"\nport = %s\ntransport = "mtproxy"\n' "$port")
        fi
        listeners_block="${listeners_block}$(printf '\n# WEB-listener: только с nginx на этой же машине, наружу не смотрит\n[[server.listeners]]\nip = "127.0.0.1"\nport = %s\ntransport = "web"\nproxy_protocol = false\nreuse_allow = false\nweb_client_ip_source = "x_forwarded_for"\nweb_trusted_proxy_cidrs = ["127.0.0.1/32"]\n' "$WEB_LISTEN_PORT")"
        # nginx за mask-пробросом видел бы 127.0.0.1 вместо клиента, и все
        # WEB-сессии для per-client лимитов слились бы в одну.
        # WEB-трафик идёт по mask-пробросу ядра, а у него лимиты для чужих
        # соединений: 5 с простоя, минута жизни, 5 МиБ. Long poll на 25 с
        # рвался на пятой секунде — доказано реальной сессией по протоколу.
        # Поднимаем: простой с запасом над long poll и пингами WebSocket,
        # жизнь — сутки, объём — максимум ядра (64 МиБ на соединение; на
        # больших объёмах WebView переоткрывает соединение, bridge повторяет
        # запрос байт в байт).
        web_censorship_line=$(printf 'mask_proxy_protocol = 2\nmask_relay_idle_timeout_ms = 70000\nmask_relay_timeout_ms = 86400000\nmask_relay_max_bytes = 67108864')
        # Carrier один, без auto-negotiation: со списком кандидатов ядро пробовало
        # бы websocket первым, а выбранный владельцем carrier был бы лишь последним
        # запасным — переключатель в меню ничего бы не значил.
        # [web.limits].max_profiles: у ядра по умолчанию 32 профиля, а профиль
        # создаётся на КАЖДЫЙ ключ — с 33-м ключом ядро отказывалось стартовать
        # («WEB profiles exceed web.limits.max_profiles»), и включение WEB
        # откатывалось, а добавление ключа из бота роняло прокси. Ядро принимает
        # до 65536; ставим запас, которого хватит любой установке.
        web_block=$(printf '\n# ── WEB-прокси (Telegram Desktop 7.1+): MTProxy внутри обычного HTTPS ──\n[web]\nenabled = true\ncarrier = "%s"\n\n[web.limits]\nmax_profiles = %s\n\n[[web.vhosts]]\nhost = "%s"\npublic_addr = "%s"\n\n[web.vhosts.decoy]\nmode = "http_upstream"\nupstream = "http://127.0.0.1:%s"\n' \
            "$(telemt_web_carrier)" "$WEB_MAX_PROFILES" "$mask_domain" "$web_pub" "$WEB_DECOY_PORT")
    fi

    # Пишем во временный файл рядом и подменяем через mv (атомарно в пределах ФС).
    # Раньше здесь стоял `cat > "$output"`, который СНАЧАЛА обнуляет боевой конфиг:
    # при ENOSPC или отключении питания в этот момент оставался обрезанный файл,
    # telemt не стартовал, и откатываться было не с чего. Все остальные пути записи
    # (ключи, лимиты, config.json, бот, панель) давно атомарны — этот отставал.
    local _tmp_out
    _tmp_out=$(mktemp "${output}.XXXXXX") || { log_error "Не удалось создать временный файл рядом с $output"; return 1; }
    chmod 600 "$_tmp_out" 2>/dev/null

    cat > "$_tmp_out" << EOTOML
# goTelegram Pro v${GOTELEGRAM_VERSION} — telemt v3 configuration
# Сгенерировано: $(date -Iseconds)
# Режим: ${mask_mode}

[general]
use_middle_proxy = true
log_level = "normal"

# Реаниматор (ME-recovery): авто-восстановление аплинка к Telegram.
# Встроено в ядро telemt (single-endpoint outage mode + async refill). Пишем явно,
# чтобы функция была активна и пережила перегенерацию конфига при обновлении.
# Значения = дефолты ядра (тюнибельно). На 3.4.18+ активны; старое ядро ключи игнорит.
me_single_endpoint_outage_mode_enabled = true
me_single_endpoint_outage_disable_quarantine = true
me_single_endpoint_outage_backoff_min_ms = 250
me_single_endpoint_outage_backoff_max_ms = 3000
me_route_no_writer_mode = "hybrid_async_persistent"
me_route_inline_recovery_attempts = 3
me_route_inline_recovery_wait_ms = 3000
me_adaptive_floor_recover_grace_secs = 180${reanim_tg_connect}

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"
${public_host_line}
public_port = ${port}

[server]
port = ${port}
${server_listen_line}
max_connections = 5000
metrics_listen = "127.0.0.1:9090"
metrics_whitelist = ["127.0.0.1/32", "::1/128"]
client_mss = ${client_mss_toml}

[server.api]
enabled = true
listen = "127.0.0.1:9091"
whitelist = ["127.0.0.1/32", "::1/128"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

[censorship]
tls_domain = "${mask_domain}"
tls_emulation = ${tls_emul}
mask = true
mask_host = "127.0.0.1"
mask_port = ${mask_port}
unknown_sni_action = "mask"
mask_shape_hardening = ${mask_hardening}
mask_shape_bucket_floor_bytes = ${bucket_floor}
mask_shape_bucket_cap_bytes = ${bucket_cap}
mask_shape_above_cap_blur_max_bytes = 64
mask_timing_normalization_enabled = ${timing_enabled}
mask_timing_normalization_floor_ms = ${timing_floor}
mask_timing_normalization_ceiling_ms = ${timing_ceiling}
server_hello_delay_min_ms = ${sh_delay}
${web_censorship_line}

[censorship.tls_fetch]
profiles = ${tls_profiles}
${listeners_block}${reanim_synlimit_block}${reanim_timeouts_block}
[access]
user_max_unique_ips_global_each = 13

[access.users]
main = "${secret}"

[network]
${dns_line}
${web_block}
EOTOML

    # Проверяем, что успели дописать до конца: пустой/обрезанный файл боевым не делаем.
    if [ ! -s "$_tmp_out" ] || ! grep -q '^\[access.users\]' "$_tmp_out"; then
        log_error "Конфиг записался не полностью (нет места на диске?) — прежний файл НЕ тронут."
        rm -f "$_tmp_out" 2>/dev/null
        return 1
    fi
    sync "$_tmp_out" 2>/dev/null || true
    mv -f "$_tmp_out" "$output" || { log_error "Не удалось заменить $output"; rm -f "$_tmp_out"; return 1; }
    chmod 600 "$output"

    # Возвращаем пер-юзерные лимиты IP, снятые перед перезаписью
    if [ -n "$preserved_ip_limits" ]; then
        if replace_telemt_ip_limits_block "$preserved_ip_limits" "$output"; then
            log_dim "Пер-юзерные лимиты IP сохранены: $(printf '%s\n' "$preserved_ip_limits" | grep -c '=') шт."
        else
            log_warning "Не удалось вернуть [access.user_max_unique_ips] в $output"
        fi
    fi

    # Профили WEB — из [access.users] (пока там только main; остальные ключи
    # вернёт gt_restore_users, и их писатель пересоберёт профили заново).
    telemt_web_sync_profiles "$output" >/dev/null 2>&1 || true
    [ "$web_on" = "1" ] && log_dim "WEB-прокси: listener 127.0.0.1:${WEB_LISTEN_PORT}, decoy 127.0.0.1:${WEB_DECOY_PORT}, carrier $(telemt_web_carrier)"

    log_success "Конфиг telemt записан: $output"
    log_dim "Режим: $mask_mode, домен: $mask_domain, порт mask: $mask_port"
    log_dim "Masking params: bucket=${bucket_floor}..${bucket_cap}, timing=${timing_floor}..${timing_ceiling}ms"
}

# ── Добавление дополнительного секрета ───────────────────────────────────────
add_secret_to_config() {
    local name="$1"
    local secret="$2"
    local config="${3:-$TELEMT_CONFIG}"

    if [ ! -f "$config" ]; then
        log_error "Конфиг не найден: $config"
        return 1
    fi

    # Валидация: раньше name/secret уходили в `sed -i` без экранирования — любой
    # символ вроде / или перевода строки позволял переписать произвольную часть
    # конфига. Теперь строгий charset + запись через общий awk-переписчик блока.
    if ! [[ "$name" =~ ^[A-Za-z0-9_.-]{1,64}$ ]]; then
        log_error "Недопустимое имя ключа: '${name}' (разрешено [A-Za-z0-9_.-], до 64 символов)"
        return 1
    fi
    if ! [[ "$secret" =~ ^[A-Za-z0-9+/=_.:-]{8,128}$ ]]; then
        log_error "Недопустимый секрет для '${name}' (разрешено [A-Za-z0-9+/=_.:-], 8-128 символов)"
        return 1
    fi

    # telemt v3: ключ живёт в секции [access.users], формат: name = "secret"
    local users_block
    users_block=$(get_telemt_users_block "$config" 2>/dev/null || true)
    # без дублей: старую строку с тем же именем выкидываем
    users_block=$(printf '%s\n' "$users_block" | grep -vE "^[[:space:]]*\"?${name}\"?[[:space:]]*=" || true)
    users_block=$(printf '%s\n%s = "%s"\n' "$users_block" "$name" "$secret" | grep -v '^[[:space:]]*$')

    replace_telemt_users_block "$users_block" "$config" || {
        log_error "Не удалось записать ключ $name в $config"
        return 1
    }

    log_success "Добавлен секрет: $name"
}

# ── Чтение текущего конфига (telemt v3 формат) ──────────────────────────────
get_config_value() {
    local key="$1"
    local config="${2:-$TELEMT_CONFIG}"

    if [ ! -f "$config" ]; then return 1; fi

    case "$key" in
        secret)
            # [access.users] main = "..."
            awk '
                /^\[access\.users\]/ { in_users=1; next }
                /^\[/ && in_users { exit }
                in_users && /^[[:space:]]*[^#[:space:]][^=]*=/ {
                    user_key=$1
                    gsub(/"/, "", user_key)
                    if (user_key != "main") next

                    value=$0
                    sub(/^[^=]*=[[:space:]]*/, "", value)
                    sub(/^"/, "", value)
                    sub(/".*$/, "", value)
                    gsub(/[[:space:]]/, "", value)
                    if (value != "") {
                        print value
                        found=1
                    }
                    exit
                }
                END { exit found ? 0 : 1 }
            ' "$config"
            ;;
        port)
            # [server] port = 443
            awk '
                /^\[server\]/ { in_server=1; next }
                /^\[/ && in_server { exit }
                in_server && $1 == "port" {
                    sub(/^[^=]*=[[:space:]]*/, "")
                    gsub(/[[:space:]]/, "")
                    print
                    exit
                }
            ' "$config"
            ;;
        mask_host|tls_domain)
            # [censorship] tls_domain = "..."
            awk '
                /^\[censorship\]/ { in_cens=1; next }
                /^\[/ && in_cens { exit }
                in_cens && $1 == "tls_domain" {
                    sub(/^[^=]*=[[:space:]]*"/, "")
                    sub(/".*$/, "")
                    print
                    exit
                }
            ' "$config"
            ;;
        mask_port)
            awk '
                /^\[censorship\]/ { in_cens=1; next }
                /^\[/ && in_cens { exit }
                in_cens && $1 == "mask_port" {
                    sub(/^[^=]*=[[:space:]]*/, "")
                    gsub(/[[:space:]]/, "")
                    print
                    exit
                }
            ' "$config"
            ;;
        *)
            grep "$key" "$config" | head -1 | sed 's/^[^=]*=[[:space:]]*//; s/^"//; s/"$//' | tr -d ' '
            ;;
    esac
}

get_telemt_users_block() {
    local config="${1:-$TELEMT_CONFIG}"
    [ -f "$config" ] || return 1
    # Пропускаем строки, не похожие на корректную запись ключа. Если config.toml
    # пострадал (обрыв записи, сбой ФС), обрезанная строка с незакрытой кавычкой
    # раньше переносилась в новый конфиг дословно и ломала парсинг ВСЕЙ секции —
    # вместе с ней терялись и здоровые ключи.
    awk '
        /^\[access\.users\]/ { in_users=1; next }
        /^\[/ && in_users { exit }
        in_users && /^[[:space:]]*"?[A-Za-z0-9_.-]+"?[[:space:]]*=[[:space:]]*"[^"]*"[[:space:]]*$/ { print; next }
        in_users && /^[[:space:]]*[^#[:space:]]/ {
            printf("goTelegram: пропущена повреждённая строка ключа: %s\n", $0) > "/dev/stderr"
        }
    ' "$config"
}

first_telemt_user_secret() {
    local config="${1:-$TELEMT_CONFIG}"
    get_telemt_users_block "$config" | head -1 | sed 's/^[^=]*=[[:space:]]*//; s/^"//; s/".*$//' | tr -d ' '
}

telemt_users_block_has_main() {
    local users_block="$1"
    printf '%s\n' "$users_block" | awk -F= '
        /^[[:space:]]*#/ || ! /=/ { next }
        {
            key=$1
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
            if (key ~ /^".*"$/ || key ~ /^\047.*\047$/) {
                key=substr(key, 2, length(key) - 2)
            }
            if (key == "main") {
                found=1
                exit
            }
        }
        END { exit found ? 0 : 1 }
    '
}

replace_telemt_users_block() {
    local users_block="$1"
    local config="${2:-$TELEMT_CONFIG}"
    [ -f "$config" ] || return 1
    [ -n "$users_block" ] || return 0

    local tmp
    # mktemp рядом с целью: mv из $TMPDIR через границу ФС — это копирование, обрыв на нём оставляет битый конфиг
    tmp=$(mktemp "${config}.XXXXXX") || return 1
    chmod 600 "$tmp" 2>/dev/null
    awk -v users="$users_block" '
        BEGIN { split(users, lines, "\n") }
        /^\[access\.users\]/ {
            found=1
            print
            for (i = 1; i in lines; i++) {
                if (lines[i] != "") print lines[i]
            }
            in_users=1
            next
        }
        /^\[/ && in_users { in_users=0 }
        in_users { next }
        { print }
        END {
            if (!found) {
                print ""
                print "[access.users]"
                for (i = 1; i in lines; i++) {
                    if (lines[i] != "") print lines[i]
                }
            }
        }
    ' "$config" > "$tmp" || { rm -f "$tmp" 2>/dev/null; return 1; }
    # обрезанный вывод (ENOSPC) боевым не делаем: в целом файле секция обязана присутствовать
    if [ ! -s "$tmp" ] || ! grep -q '^\[access\.users\]' "$tmp"; then rm -f "$tmp" 2>/dev/null; return 1; fi
    sync "$tmp" 2>/dev/null || true
    # раньше кодом возврата функции был код chmod (под root всегда 0) — провал записи глотался, `|| return 1` вызывающих были мертвы
    mv -f "$tmp" "$config" || { rm -f "$tmp" 2>/dev/null; return 1; }
    chmod 600 "$config"
    # Ключи изменились — профили WEB обязаны им соответствовать
    telemt_web_sync_profiles "$config" >/dev/null 2>&1 || true
    return 0
}

# ── [access.user_max_unique_ips] — пер-юзерные лимиты уникальных IP ──────────
# Секцию пишут бот и вебка; при перегенерации конфига её нужно сохранять
# ровно так же, как [access.users].
get_telemt_ip_limits_block() {
    local config="${1:-$TELEMT_CONFIG}"
    [ -f "$config" ] || return 1
    awk '
        /^\[access\.user_max_unique_ips\]/ { in_lim=1; next }
        /^\[/ && in_lim { exit }
        in_lim && /^[[:space:]]*[^#[:space:]][^=]*=/ { print }
    ' "$config"
}

replace_telemt_ip_limits_block() {
    local limits_block="$1"
    local config="${2:-$TELEMT_CONFIG}"
    [ -f "$config" ] || return 1
    [ -n "$limits_block" ] || return 0

    local tmp
    # mktemp рядом с целью: mv из $TMPDIR через границу ФС — это копирование, обрыв на нём оставляет битый конфиг
    tmp=$(mktemp "${config}.XXXXXX") || return 1
    chmod 600 "$tmp" 2>/dev/null
    awk -v limits="$limits_block" '
        BEGIN { split(limits, lines, "\n") }
        /^\[access\.user_max_unique_ips\]/ {
            found=1
            print
            for (i = 1; i in lines; i++) {
                if (lines[i] != "") print lines[i]
            }
            in_lim=1
            next
        }
        /^\[/ && in_lim { in_lim=0 }
        in_lim { next }
        { print }
        END {
            if (!found) {
                print ""
                print "[access.user_max_unique_ips]"
                for (i = 1; i in lines; i++) {
                    if (lines[i] != "") print lines[i]
                }
            }
        }
    ' "$config" > "$tmp" || { rm -f "$tmp" 2>/dev/null; return 1; }
    # обрезанный вывод (ENOSPC) боевым не делаем: в целом файле секция обязана присутствовать
    if [ ! -s "$tmp" ] || ! grep -q '^\[access\.user_max_unique_ips\]' "$tmp"; then rm -f "$tmp" 2>/dev/null; return 1; fi
    sync "$tmp" 2>/dev/null || true
    # раньше кодом возврата функции был код chmod (под root всегда 0) — провал записи глотался, `|| return 1` вызывающих были мертвы
    mv -f "$tmp" "$config" || { rm -f "$tmp" 2>/dev/null; return 1; }
    chmod 600 "$config"
    return 0
}

toml_bool_value() {
    local table="$1"
    local key="$2"
    local config="${3:-$TELEMT_CONFIG}"
    awk -v table="$table" -v key="$key" '
        $0 == "[" table "]" { in_table=1; next }
        /^\[/ && in_table { exit }
        in_table && $1 == key {
            sub(/^[^=]*=[[:space:]]*/, "")
            gsub(/[[:space:]]/, "")
            print
            exit
        }
    ' "$config"
}

# ── Валидация конфига ────────────────────────────────────────────────────────
validate_telemt_config() {
    local config="${1:-$TELEMT_CONFIG}"

    if [ ! -f "$config" ]; then
        log_error "Конфиг не найден: $config"
        return 1
    fi

    # Проверяем обязательные поля
    local secret port host
    secret=$(get_config_value secret "$config")
    port=$(get_config_value port "$config")
    host=$(get_config_value mask_host "$config")

    local errors=0

    if [ -z "$secret" ]; then
        log_error "Не задан secret"
        ((errors++))
    elif [ ${#secret} -lt 32 ]; then
        log_warning "Secret слишком короткий (${#secret} символов, рекомендуется 32+)"
    fi

    if [ -z "$port" ]; then
        log_error "Не задан порт (bind_to)"
        ((errors++))
    elif [ "$port" -lt 1 ] || [ "$port" -gt 65535 ] 2>/dev/null; then
        log_error "Порт вне диапазона: $port"
        ((errors++))
    fi

    if [ -z "$host" ]; then
        log_error "Не задан маскировочный хост (censorship.tls_domain)"
        ((errors++))
    fi

    if [ $errors -gt 0 ]; then
        log_error "Найдено ошибок: $errors"
        return 1
    fi

    log_success "Конфиг валиден"
    return 0
}

# ── Выбор домена (интерактивный) ─────────────────────────────────────────────
select_quick_domain() {
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}🌐 Выберите домен для маскировки (Fake TLS):${NC}" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}" >&2

    local i=1
    local row=""
    for d in "${QUICK_DOMAINS[@]}"; do
        printf "  ${CYAN}%2d)${NC} %-25s" "$i" "$d" >&2
        if (( i % 2 == 0 )); then
            echo "" >&2
        fi
        ((i++))
    done
    if (( (i-1) % 2 != 0 )); then echo "" >&2; fi

    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}" >&2
    echo -ne "  ${WHITE}Выбор (1-${#QUICK_DOMAINS[@]}):${NC} " >&2
    read -r choice

    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le ${#QUICK_DOMAINS[@]} ]; then
        echo "${QUICK_DOMAINS[$((choice-1))]}"
        return 0
    fi

    log_error "Неверный выбор"
    return 1
}

# ── Выбор порта (интерактивный) ──────────────────────────────────────────────
select_port() {
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}🔌 Выберите порт:${NC}" >&2

    # Проверяем стандартные порты
    local busy_443 busy_8443
    busy_443=$(check_port 443)
    busy_8443=$(check_port 8443)

    local label_443="443 (рекомендуется)"
    local label_8443="8443"
    [ -n "$busy_443" ] && label_443="443 ⚠️  занят"
    [ -n "$busy_8443" ] && label_8443="8443 ⚠️  занят"

    echo -e "  ${CYAN}1)${NC} $label_443" >&2
    echo -e "  ${CYAN}2)${NC} $label_8443" >&2
    echo -e "  ${CYAN}3)${NC} Свой порт" >&2

    if [ -n "$busy_443" ]; then
        echo -e "  ${DIM}  ⚠ Порт 443 занят: $(echo "$busy_443" | head -c 60)${NC}" >&2
    fi

    echo -ne "  ${WHITE}Выбор:${NC} " >&2
    read -r choice

    case "$choice" in
        1) echo "443" ;;
        2) echo "8443" ;;
        3)
            echo -ne "  Введите порт (1-65535): " >&2
            read -r custom_port
            if [[ "$custom_port" =~ ^[0-9]+$ ]] && [ "$custom_port" -ge 1 ] && [ "$custom_port" -le 65535 ]; then
                echo "$custom_port"
            else
                log_error "Неверный порт"
                return 1
            fi
            ;;
        *) echo "443" ;;
    esac
}

# ── Генерация ссылки tg://proxy ──────────────────────────────────────────────
generate_proxy_link() {
    local server="${1:-$(get_server_ip)}"
    local port="${2:-443}"
    local secret="$3"
    local mask_host="${4:-}"

    # Если указан mask_host (fake-TLS), формируем ee-секрет
    if [ -n "$mask_host" ]; then
        local domain_hex
        domain_hex=$(printf '%s' "$mask_host" | xxd -p | tr -d '\n')
        secret="ee${secret}${domain_hex}"
    fi

    echo "tg://proxy?server=${server}&port=${port}&secret=${secret}"
}

# ── Вывод информации о прокси ────────────────────────────────────────────────
show_proxy_info() {
    local config="${1:-$TELEMT_CONFIG}"
    local secret port mask_host ip link status

    secret=$(get_config_value secret "$config")
    port=$(get_config_value port "$config")
    mask_host=$(get_config_value mask_host "$config")
    ip=$(get_server_ip)
    status=$(telemt_status)

    local mode domain
    mode=$(config_get mode 2>/dev/null || echo "lite")
    domain=$(config_get domain 2>/dev/null || echo "")

    # Генерация ссылки: оба режима используют ee-секрет с mask_host
    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
        link=$(generate_proxy_link "$domain" "$port" "$secret" "$domain")
    else
        link=$(generate_proxy_link "$ip" "$port" "$secret" "$mask_host")
    fi

    local status_icon status_text
    case "$status" in
        running) status_icon="✅"; status_text="Работает" ;;
        stopped) status_icon="⏸️";  status_text="Остановлен" ;;
        *)       status_icon="❌"; status_text="Не установлен" ;;
    esac

    echo ""
    echo -e "  ${BOLD}${WHITE}${status_icon} Статус прокси: ${status_text}${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}"
    echo -e "  ${WHITE}Ядро:${NC}       telemt (Rust)"
    if [ "$mode" = "pro" ] && [ -n "$domain" ]; then
        echo -e "  ${WHITE}Домен:${NC}      ${CYAN}${domain}${NC}"
    else
        echo -e "  ${WHITE}IP:${NC}         ${CYAN}${ip}${NC}"
    fi
    echo -e "  ${WHITE}Порт:${NC}       ${CYAN}${port}${NC}"
    echo -e "  ${WHITE}Режим:${NC}      ${CYAN}${mode}${NC}"
    echo -e "  ${WHITE}Маскировка:${NC} ${CYAN}${mask_host}${NC}"
    echo -e "  ${WHITE}Secret:${NC}     ${CYAN}${secret:0:16}...${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}"
    echo -e "  ${WHITE}Ссылка:${NC}"
    echo -e "  ${GREEN}${link}${NC}"
    echo ""

    # QR если доступен
    if command -v qrencode &>/dev/null; then
        qrencode -t UTF8 -m 2 "$link" 2>/dev/null
    fi
}

# ── Вывод информации о прокси (Pro-режим) ──────────────────────────────────
# В pro-режиме ссылка содержит домен (не IP) и fake-TLS секрет (ee...)
show_proxy_info_pro() {
    local domain="$1"
    local faketls_secret="$2"

    local link="tg://proxy?server=${domain}&port=443&secret=${faketls_secret}"

    echo ""
    echo -e "  ${BOLD}${WHITE}✅ Pro-прокси настроен${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -e "  ${WHITE}Ядро:${NC}       telemt (Rust)"
    echo -e "  ${WHITE}Домен:${NC}      ${CYAN}${domain}${NC}"
    echo -e "  ${WHITE}Порт:${NC}       ${CYAN}443${NC} (внешний, telemt)"
    echo -e "  ${WHITE}Режим:${NC}      ${MAGENTA}Pro (fake-TLS)${NC}"
    echo -e "  ${WHITE}nginx:${NC}      ${CYAN}127.0.0.1:8443${NC} (внутренний)"
    echo -e "  ${WHITE}Secret:${NC}     ${CYAN}${faketls_secret:0:20}...${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"
    echo -e "  ${WHITE}Ссылка для Telegram:${NC}"
    echo -e "  ${GREEN}${link}${NC}"
    echo ""
    echo -e "  ${DIM}Провайдер видит: HTTPS-трафик к ${domain}:443${NC}"
    echo -e "  ${DIM}Telegram-клиент маскирует соединение под TLS${NC}"
    echo ""

    # QR если доступен
    if command -v qrencode &>/dev/null; then
        qrencode -t UTF8 -m 2 "$link" 2>/dev/null
    fi
    # Сюда приходит ee-форма (ee + секрет + hex домена); WEB-ссылке нужен сырой
    # 32-символьный секрет.
    local _raw="${faketls_secret#ee}"
    show_web_link "${_raw:0:32}" 1
}
