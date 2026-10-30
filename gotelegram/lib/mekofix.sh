#!/usr/bin/env bash
# lib/mekofix.sh — SYN-фикс MTProto (V2/V3) на файрволе сервера.
#
# Лечит проблему «с 4 июня»: клиент Telegram зависает на первичном TCP-рукопожатии,
# затем сеть клиента блокируется на ~2 минуты. Идея и наборы правил: MEKO fix
# (c) @Mekotofeuka — github.com/Mekotofeuka/MTPROTO_FIX_By_MEKO (MIT).
#
# Суть: iOS-клиенты пропускаются БЕЗ лимита, все остальные — SYN-лимит 54/мин
# (=1 пакет в 1.1с) на IP, превысившие получают REJECT+RST (мгновенный ретрай),
# а не DROP (таймаут 3-5с). Разделение по устройствам:
#   V3 — по TCP-отпечатку (u32/nft raw-offsets)  ← рекомендуется, надёжнее
#   V2 — по TTL+Length (проще, но даёт сбои за балансировщиками)
# V4 (zapret2) — в отдельном модуле lib/mekofix_v4.sh (экспериментальный).
#
# ВАЖНО: при активном внешнем фиксе встроенные в telemt synlimit и MSS ДОЛЖНЫ быть
# выключены (двойной лимит = тормоза медиа). Это делает mekofix_disable_builtin().

MEKOFIX_DIR="/opt/gotelegram-mekofix"
MEKOFIX_RULES="$MEKOFIX_DIR/rules.sh"
MEKOFIX_STATE="${MEKOFIX_STATE:-$MEKOFIX_DIR/state.conf}"
MEKOFIX_LOG="/var/log/gotelegram-mekofix.log"
MEKOFIX_SVC="gotelegram-mekofix"
MEKOFIX_NFT_TABLE="gotelegram_mekofix"      # свой неймспейс (не mtpr_synfix — чужое)
MEKOFIX_IPT_CHAIN="GT_MEKOFIX"
MEKOFIX_MARK="0x400"
MEKOFIX_RATE="${MEKOFIX_RATE:-54/minute}"    # = 1 пакет в 1.1с (запас 100мс, см. README MEKO)
# burst — сколько SYN можно принять «пачкой». У MEKO burst=1, но десктопные клиенты
# (Telegram Desktop) открывают несколько соединений ОДНОВРЕМЕННО: при burst=1 первое
# проходит, остальные ловят RST и клиент не собирается. Дефолт 5 — десктоп стартует,
# при этом устойчивая скорость остаётся 1 SYN/1.1с (ТСПУ-защита сохраняется).
MEKOFIX_BURST="${MEKOFIX_BURST:-5}"
# Самообучение «доверенных» IP (решает случай 10-15 устройств за одним NAT):
# лимит считается НА IP, поэтому вся домашняя/офисная сеть делит одну квоту.
# Как только с IP прошла настоящая рабочая сессия — IP попадает в динамический set
# и дальше подключается БЕЗ лимита (сколько угодно устройств). Лимит остаётся
# только для новых/неизвестных IP — защита от ТСПУ сохраняется.
MEKOFIX_LEARN="${MEKOFIX_LEARN:-on}"          # on|off
MEKOFIX_TRUST_TTL="${MEKOFIX_TRUST_TTL:-6h}"  # сколько держать IP доверенным
MEKOFIX_TUNE_SVC="gotelegram-mekofix-tune"

# ── Профили сети ────────────────────────────────────────────────────────────
# Проводной: один стабильный IP, 10-15 своих устройств → доверие копится, burst умеренный.
# Мобильный (LTE): CGNAT — за одним IP сотни чужих абонентов, адрес часто меняется,
#   доверие не успевает накопиться → нужен щедрый burst и более длинный TTL.
# Смешанный: компромисс. Авто: стартует со смешанного и сам подстраивается по счётчикам.
_mf_profile_params() {
    case "${1:-auto}" in
        # Значения, проверенные на живых устройствах владельца в ранней 2.9:
        # именно на них подключение работало лучше всего. Более щедрые пороги
        # (150/мин, пачка 35) были подобраны теоретически, «на всякий случай под
        # NAT», и на реальных клиентах не проверялись — возвращаем исходные.
        wired)  echo "54/minute 5 6h" ;;
        mobile) echo "90/minute 15 12h" ;;
        mixed)  echo "60/minute 10 12h" ;;
        *)      echo "54/minute 5 6h" ;;      # auto = проверенный вариант
    esac
}
mekofix_profile_apply_vars() {
    local prof="${1:-$(mekofix_state_get profile)}"; [ -z "$prof" ] && prof="auto"
    local p; p=$(_mf_profile_params "$prof")
    MEKOFIX_RATE="${p%% *}"; p="${p#* }"
    MEKOFIX_BURST="${p%% *}"; MEKOFIX_TRUST_TTL="${p##* }"
    # ручной burst из состояния (автотюнер мог поднять) имеет приоритет
    local sb; sb=$(mekofix_state_get burst); [ -n "$sb" ] && MEKOFIX_BURST="$sb"
    MEKOFIX_PROFILE="$prof"
}

# TCP-отпечаток iOS (V3). u32: 4-байтовые фрагменты со смещения 32 от IP-заголовка.
MEKOFIX_U32='32 & 0x000FFFFF = 0x0002FFFF && 40 & 0xFF000000 = 0x02000000 && 44 & 0xFFFF0000 = 0x01030000 && 48 & 0xFFFFFF00 = 0x01010800 && 60 & 0xFFFFFFFF = 0x04020000'
# то же для nft (raw-смещения от начала TCP-заголовка)
MEKOFIX_NFT_FP='@th,108,20 0x2ffff @th,160,16 0x204 @th,192,16 0x103 @th,224,24 0x10108 @th,320,32 0x4020000'

# self-contained запись в config.json (bot_update_config_field есть только внутри install.sh)
_mf_cfg_set() {
    if type bot_update_config_field >/dev/null 2>&1; then bot_update_config_field "$@"; return $?; fi
    local key="$1" value="$2" tmp now cfg
    cfg="${GOTELEGRAM_CONFIG:-${GOTELEGRAM_DIR:-/opt/gotelegram}/config.json}"
    [ -f "$cfg" ] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    tmp=$(mktemp) || return 1
    now=$(date -Iseconds 2>/dev/null || date +%s)
    if jq --arg k "$key" --arg v "$value" --arg t "$now" '.[$k]=$v | .updated_at=$t' "$cfg" >"$tmp" 2>/dev/null; then
        mv "$tmp" "$cfg"; chmod 600 "$cfg" 2>/dev/null; return 0
    fi
    rm -f "$tmp"; return 1
}

# Лог создаём под umask 077 (иначе 644 в /var/log): туда попадает вывод правил,
# конфигов и диагностики, читать его посторонним незачем.
_mf_log_ensure() {
    [ -e "$MEKOFIX_LOG" ] || ( umask 077; : > "$MEKOFIX_LOG" ) 2>/dev/null || true
    chmod 600 "$MEKOFIX_LOG" 2>/dev/null || true
}
_mf_log() {
    _mf_log_ensure
    printf '%s %s\n' "$(date -Iseconds 2>/dev/null || date)" "$*" >> "$MEKOFIX_LOG" 2>/dev/null
}
_mf_cap() {
    [ -f "$MEKOFIX_LOG" ] || return 0
    local sz; sz=$(stat -c %s "$MEKOFIX_LOG" 2>/dev/null || echo 0)
    [ "$sz" -gt $((20*1024*1024)) ] 2>/dev/null && { ( umask 077; tail -c $((10*1024*1024)) "$MEKOFIX_LOG" >"$MEKOFIX_LOG.tmp" ) 2>/dev/null && mv "$MEKOFIX_LOG.tmp" "$MEKOFIX_LOG" && chmod 600 "$MEKOFIX_LOG" 2>/dev/null; }
    return 0
}

# ── состояние ────────────────────────────────────────────────────────────────
mekofix_state_get() { [ -f "$MEKOFIX_STATE" ] && grep -E "^$1=" "$MEKOFIX_STATE" 2>/dev/null | tail -1 | cut -d= -f2- || echo ""; }
_mf_state_save() {
    mkdir -p "$MEKOFIX_DIR" 2>/dev/null
    printf 'version=%s\nbackend=%s\nports=%s\nprofile=%s\nburst=%s\nrate=%s\napplied_at=%s\n' \
        "$1" "$2" "$3" "${MEKOFIX_PROFILE:-auto}" "${MEKOFIX_BURST}" "${MEKOFIX_RATE}" "$(date -Iseconds 2>/dev/null || date +%s)" > "$MEKOFIX_STATE" 2>/dev/null
    # Файл перезаписывается целиком, поэтому режим лимита пишем здесь же —
    # иначе он терялся бы при каждом переприменении правил.
    printf 'nolimit=%s\n' "${MEKOFIX_NOLIMIT:-0}" >> "$MEKOFIX_STATE" 2>/dev/null
    chmod 600 "$MEKOFIX_STATE" 2>/dev/null || true
}

# ── SSH-порт (защита от самоблокировки) ──────────────────────────────────────
# Все известные SSH-порты (конфиг + порт ТЕКУЩЕЙ сессии) — аналог _v4_ssh_ports.
_mf_ssh_ports() {
    local out="" p
    p=$(mekofix_ssh_port); [ -n "$p" ] && out="$out $p"
    p=$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $4}')
    [[ "$p" =~ ^[0-9]+$ ]] && out="$out $p"
    # shellcheck disable=SC2086
    printf '%s\n' $out | grep -E '^[0-9]+$' | sort -un | tr '\n' ' '
}
_mf_port_is_ssh() {
    local p="${1:-}" q
    for q in $(_mf_ssh_ports); do [ "$p" = "$q" ] && return 0; done
    return 1
}

mekofix_ssh_port() {
    local p=""
    command -v sshd >/dev/null 2>&1 && p=$(timeout 3 sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')
    [ -z "$p" ] && p=$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/{print $2; exit}' /etc/ssh/sshd_config 2>/dev/null)
    [ -z "$p" ] && [ -d /etc/ssh/sshd_config.d ] && p=$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/{print $2; exit}' /etc/ssh/sshd_config.d/*.conf 2>/dev/null)
    [ -z "$p" ] && p=$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $4}')
    [[ "$p" =~ ^[0-9]+$ ]] || p=22
    echo "$p"
}

# ── выбор бэкенда: nft (осн.) → iptables (нужен xt_u32 для V3) ───────────────
mekofix_backend_detect() {
    local want="${1:-auto}" ver="${2:-v3}"
    case "$want" in
        nft)      command -v nft >/dev/null 2>&1 && { echo nft; return 0; }; echo ""; return 1 ;;
        iptables) command -v iptables >/dev/null 2>&1 && { echo iptables; return 0; }; echo ""; return 1 ;;
    esac
    if command -v nft >/dev/null 2>&1; then echo nft; return 0; fi
    if command -v iptables >/dev/null 2>&1; then
        if [ "$ver" = "v3" ] && ! { modinfo xt_u32 >/dev/null 2>&1 || lsmod 2>/dev/null | grep -q '^xt_u32'; }; then
            echo ""; return 2   # V3 на iptables без xt_u32 невозможен
        fi
        echo iptables; return 0
    fi
    echo ""; return 1
}

# ── детект чужих установок (MEKO-скрипт, реаниматор и т.п.) ──────────────────
mekofix_foreign_found() {
    local found=""
    [ -d /opt/mtpr-simple ] && found="$found /opt/mtpr-simple"
    iptables -L MTPR_SYNFIX -n >/dev/null 2>&1 && found="$found iptables:MTPR_SYNFIX"
    command -v nft >/dev/null 2>&1 && nft list table inet mtpr_synfix >/dev/null 2>&1 && found="$found nft:mtpr_synfix"
    systemctl list-unit-files 2>/dev/null | grep -qE '^mtpr-(synfix|nft-synfix)\.service' && found="$found mtpr-synfix.service"
    [ -n "$found" ] && { echo "${found# }"; return 0; }
    return 1
}

# встроенные в telemt synlimit/MSS (конфликтуют с внешним фиксом)
mekofix_builtin_conflicts() {
    local cfg="${TELEMT_CONFIG:-/etc/telemt/config.toml}" out=""
    [ -f "$cfg" ] || { echo ""; return 1; }
    grep -qE '^[[:space:]]*synlimit[[:space:]]*=' "$cfg" 2>/dev/null && out="$out synlimit"
    grep -qE '^[[:space:]]*client_mss[[:space:]]*=[[:space:]]*"[0-9]' "$cfg" 2>/dev/null && out="$out MSS"
    [ -n "$out" ] && { echo "${out# }"; return 0; }
    return 1
}

# ── генератор скрипта правил (единый источник правды: up|down|check|show) ────
_mf_write_rules() {
    local ver="$1" backend="$2" ports="$3"
    mkdir -p "$MEKOFIX_DIR" 2>/dev/null
    cat > "$MEKOFIX_RULES" <<RULESEOF
#!/bin/bash
# goTelegram SYN-фикс ($ver, $backend). Автогенерация — не редактировать вручную.
# Идея правил: MEKO fix (c) @Mekotofeuka (MIT).
set -u
PORTS="$ports"
VER="$ver"
BACKEND="$backend"
NFT_TABLE="$MEKOFIX_NFT_TABLE"
IPT_CHAIN="$MEKOFIX_IPT_CHAIN"
MARK="$MEKOFIX_MARK"
RATE="$MEKOFIX_RATE"
BURST="$MEKOFIX_BURST"
NOLIMIT="${MEKOFIX_NOLIMIT:-0}"
LEARN="$MEKOFIX_LEARN"
TRUST_TTL="$MEKOFIX_TRUST_TTL"
U32='$MEKOFIX_U32'
NFT_FP='$MEKOFIX_NFT_FP'
SSH_PORT="\$(sshd -T 2>/dev/null | awk '/^port /{print \$2; exit}')"
[[ "\$SSH_PORT" =~ ^[0-9]+\$ ]] || SSH_PORT=22

# Весь набор правил одним текстом — применяется атомарно (nft -f -).
# Идиома "add пустую таблицу → delete → создать заново" внутри ОДНОЙ транзакции:
# промежуточных состояний нет. Раньше правила добавлялись по одному, и если
# падало предпоследнее (accept), а последнее (reject) проходило — все клиенты
# получали RST.
nft_ruleset() {
    local p
    printf 'table inet %s { }\n'      "\$NFT_TABLE"
    printf 'delete table inet %s\n'   "\$NFT_TABLE"
    printf 'table inet %s {\n'        "\$NFT_TABLE"
    # set доверенных IP: пополняется автоматически теми, у кого прошла рабочая сессия
    printf '  set trusted { type ipv4_addr; flags dynamic,timeout; timeout %s; size 65535; }\n' "\$TRUST_TTL"
    printf '  chain input {\n'
    printf '    type filter hook input priority -10; policy accept;\n'
    # SSH — всегда первым (защита от самоблокировки)
    printf '    tcp dport %s counter accept comment "ssh_guard"\n' "\$SSH_PORT"
    for p in \${PORTS//,/ }; do
        if [ "\$LEARN" = "on" ]; then
            # 1) уже доверенный IP → без лимита (все устройства за одним NAT)
            printf '    tcp dport %s tcp flags & (syn|ack) == syn ip saddr @trusted counter accept comment "trusted_nolimit"\n' "\$p"
            # 2) обучение: рабочая сессия (established + реальные данные) → запомнить IP
            printf '    tcp dport %s ct state established meta length > 300 update @trusted { ip saddr } counter comment "learn_trusted"\n' "\$p"
        fi
        if [ "\$VER" = "v3" ]; then
            printf '    tcp dport %s tcp flags & (syn|ack) == syn %s counter accept comment "ios_nolimit"\n' "\$p" "\$NFT_FP"
        else
            printf '    tcp dport %s tcp flags & (syn|ack) == syn ip ttl lt 65 meta length 64 counter accept comment "ios_nolimit"\n' "\$p"
        fi
        # Режим «без ограничения»: умная часть (пропуск iOS, обучение доверенных,
        # защита SSH) работает, а лимитер и отбойник не создаются вовсе. Никого
        # не режем — это основной режим, самый безопасный для клиентов.
        if [ "\$NOLIMIT" = "1" ]; then
            printf '    tcp dport %s tcp flags & (syn|ack) == syn counter accept comment "other_nolimit"\n' "\$p"
        else
            printf '    tcp dport %s tcp flags & (syn|ack) == syn meter gtmf_%s { ip saddr timeout 60s limit rate %s burst %s packets } counter accept comment "other_limited"\n' "\$p" "\$p" "\$RATE" "\$BURST"
            printf '    tcp dport %s tcp flags & (syn|ack) == syn counter reject with tcp reset comment "other_reject"\n' "\$p"
        fi
    done
    printf '  }\n'
    printf '}\n'
}
nft_up() { nft_ruleset | nft -f - || return 1; }
nft_dry() { nft_ruleset | nft -c -f - ; }
nft_down() { nft delete table inet "\$NFT_TABLE" 2>/dev/null || true; }

ipt_up() {
    iptables -t filter -N "\$IPT_CHAIN" 2>/dev/null || true
    iptables -t filter -F "\$IPT_CHAIN"
    iptables -t filter -C INPUT -j "\$IPT_CHAIN" 2>/dev/null || iptables -t filter -I INPUT 1 -j "\$IPT_CHAIN"
    # ВАЖНО: внутри своей цепочки только RETURN, никогда ACCEPT. Цепочка воткнута
    # в INPUT первой, и ACCEPT здесь ТЕРМИНИРУЕТ обход всей таблицы filter — пакет
    # ушёл бы мимо ufw и цепочек fail2ban. Наша задача — «не лимитировать», а не
    # «пропустить мимо всех»: RETURN возвращает пакет в INPUT, дальше решают соседи.
    # SSH — всегда первым (защита от самоблокировки)
    iptables -t filter -A "\$IPT_CHAIN" -p tcp --dport "\$SSH_PORT" -j RETURN
    local p
    for p in \${PORTS//,/ }; do
        if [ "\$VER" = "v3" ]; then
            # маркируем iOS по отпечатку (только наш порт, только SYN)
            iptables -t mangle -C PREROUTING -p tcp --dport "\$p" --syn -m u32 --u32 "\$U32" -j MARK --set-mark "\$MARK" 2>/dev/null || \\
                iptables -t mangle -A PREROUTING -p tcp --dport "\$p" --syn -m u32 --u32 "\$U32" -j MARK --set-mark "\$MARK"
            iptables -t filter -A "\$IPT_CHAIN" -p tcp --dport "\$p" --syn -m mark --mark "\$MARK" -j RETURN
        else
            iptables -t filter -A "\$IPT_CHAIN" -p tcp --dport "\$p" --syn -m length --length 64 -m ttl --ttl-lt 65 -j RETURN
        fi
        iptables -t filter -A "\$IPT_CHAIN" -p tcp --dport "\$p" --syn \\
            -m hashlimit --hashlimit-name gtmf_"\$p" --hashlimit-mode srcip \\
            --hashlimit-upto "\$RATE" --hashlimit-burst "\$BURST" \\
            --hashlimit-htable-expire 60000 --hashlimit-htable-size 32768 -j RETURN
        iptables -t filter -A "\$IPT_CHAIN" -p tcp --dport "\$p" --syn -j REJECT --reject-with tcp-reset
    done
    # Проверка ПОСЛЕ применения: если лимитер не встал (нет xt_hashlimit), в цепочке
    # остаются только RETURN для ssh/iOS и финальный REJECT — то есть каждый обычный
    # SYN получает RST и прокси недоступен, а статус при этом зелёный. nft-путь уже
    # атомарен транзакцией, iptables добавляет правила по одному — страхуемся здесь.
    for p in \${PORTS//,/ }; do
        iptables -t filter -C "\$IPT_CHAIN" -p tcp --dport "\$p" --syn \\
            -m hashlimit --hashlimit-name gtmf_"\$p" --hashlimit-mode srcip \\
            --hashlimit-upto "\$RATE" --hashlimit-burst "\$BURST" \\
            --hashlimit-htable-expire 60000 --hashlimit-htable-size 32768 -j RETURN 2>/dev/null || {
            echo "mekofix: лимитер не применился (нет xt_hashlimit?) — откатываюсь, чтобы не резать всех" >&2
            ipt_down
            return 1
        }
    done
}
ipt_down() {
    iptables -t filter -C INPUT -j "\$IPT_CHAIN" 2>/dev/null && iptables -t filter -D INPUT -j "\$IPT_CHAIN" || true
    iptables -t filter -F "\$IPT_CHAIN" 2>/dev/null || true
    iptables -t filter -X "\$IPT_CHAIN" 2>/dev/null || true
    local p
    for p in \${PORTS//,/ }; do
        while iptables -t mangle -C PREROUTING -p tcp --dport "\$p" --syn -m u32 --u32 "\$U32" -j MARK --set-mark "\$MARK" 2>/dev/null; do
            iptables -t mangle -D PREROUTING -p tcp --dport "\$p" --syn -m u32 --u32 "\$U32" -j MARK --set-mark "\$MARK" || break
        done
    done
}

case "\${1:-up}" in
    up)    if [ "\$BACKEND" = "nft" ]; then nft_up; else ipt_up; fi ;;
    down)  nft_down 2>/dev/null; ipt_down 2>/dev/null; true ;;
    check) if [ "\$BACKEND" = "nft" ]; then nft list table inet "\$NFT_TABLE" >/dev/null 2>&1; else iptables -L "\$IPT_CHAIN" -n >/dev/null 2>&1; fi ;;
    show)  if [ "\$BACKEND" = "nft" ]; then nft list table inet "\$NFT_TABLE" 2>/dev/null; else iptables -L "\$IPT_CHAIN" -n -v 2>/dev/null; iptables -t mangle -L PREROUTING -n -v 2>/dev/null | grep -i u32; fi ;;
esac
RULESEOF
    chmod +x "$MEKOFIX_RULES"
}

_mf_write_unit() {
    cat > "/etc/systemd/system/${MEKOFIX_SVC}.service" <<EOF
[Unit]
Description=goTelegram MTProto SYN-fix (MEKO V2/V3)
After=network-online.target nftables.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$MEKOFIX_RULES up
ExecStop=$MEKOFIX_RULES down
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload 2>/dev/null || true
}

# ── ПЛАН изменений (показать ДО применения) ──────────────────────────────────
mekofix_plan() {
    # План обязан показывать ТЕ ЖЕ числа, что применит mekofix_apply, иначе человек
    # соглашается на 54/minute burst 5, а получает 150/minute burst 35.
    mekofix_profile_apply_vars "$(mekofix_state_get profile 2>/dev/null)" 2>/dev/null || true
    local ver="${1:-v3}" ports="${2:-443}" backend="${3:-auto}" b conf fk
    b=$(mekofix_backend_detect "$backend" "$ver") || true
    echo ""
    echo "  Будет сделано (SYN-фикс ${ver}, порты: ${ports}):"
    if [ -z "$b" ]; then
        echo "  ✗ Нет подходящего бэкенда: нужен nft, либо iptables + модуль xt_u32 (для V3)."
        return 1
    fi
    echo "  • Бэкенд: ${b}$( [ "$b" = nft ] && echo ' (nftables — рекомендуется)' )"
    if [ "$b" = "nft" ]; then
        echo "  • Создать СВОЮ таблицу nft inet ${MEKOFIX_NFT_TABLE} (чужие правила не трогаем)"
    else
        echo "  • Создать СВОЮ цепочку iptables ${MEKOFIX_IPT_CHAIN} + подключить к INPUT"
        [ "$ver" = "v3" ] && echo "  • Пометка iOS в mangle PREROUTING по TCP-отпечатку (u32), только для порта(ов) ${ports}"
    fi
    echo "  • SSH-порт $(mekofix_ssh_port) разрешается ПЕРВЫМ правилом (защита от потери доступа)"
    if [ "$ver" = "v3" ]; then
        echo "  • iOS определяется по TCP-отпечатку → пропуск БЕЗ лимита"
    else
        echo "  • iOS определяется по TTL<65 + length 64 → пропуск БЕЗ лимита"
    fi
    echo "  • Остальные устройства: ${MEKOFIX_RATE} с IP, пачкой до ${MEKOFIX_BURST} (десктоп открывает несколько соединений сразу); превышение → REJECT+RST"
    echo "  • Автозапуск при перезагрузке: systemd-юнит ${MEKOFIX_SVC}.service"
    if [ "$(config_get mode 2>/dev/null)" = "pro" ]; then
        echo "  ⚠ Pro: на этом порту отдаётся сайт-маскировка — лимит коснётся и его посетителей."
        echo "    Порог поднят до ${MEKOFIX_BURST}, но при жалобах на сайт фикс лучше выключить."
    fi
    if conf=$(mekofix_builtin_conflicts); then
        echo "  • ОТКЛЮЧИТЬ встроенные в telemt: ${conf} (иначе двойной лимит и медленное медиа) + перезапуск telemt"
    fi
    if fk=$(mekofix_foreign_found); then
        echo "  ⚠ Обнаружена ЧУЖАЯ установка фикса: ${fk}"
        echo "    Она НЕ будет удалена автоматически — возможен конфликт правил."
    fi
    echo "  • Откат в любой момент: меню → Управление → SYN-фикс → Выключить"
    echo ""
    return 0
}

# ── отключение встроенных synlimit/MSS telemt ────────────────────────────────
mekofix_disable_builtin() {
    _mf_cfg_set mekofix_builtin_off on >/dev/null 2>&1 || true
    local cfg="${TELEMT_CONFIG:-/etc/telemt/config.toml}"
    [ -f "$cfg" ] || return 0
    cp -a "$cfg" "${cfg}.mekofix.bak" 2>/dev/null || true
    # убираем synlimit-строки и узкий client_mss (telemt тогда не клэмпит MSS)
    sed -i -E 's/^([[:space:]]*synlimit.*)$/# \1/; s/^([[:space:]]*client_mss[[:space:]]*=[[:space:]]*"[0-9]+".*)$/# \1/' "$cfg" 2>/dev/null || true
    _mf_log "builtin synlimit/MSS disabled in $cfg (backup .mekofix.bak)"
    systemctl restart telemt >/dev/null 2>&1 || true
    return 0
}

# ── ВКЛЮЧИТЬ ────────────────────────────────────────────────────────────────
mekofix_apply() {
    # v4 обслуживается отдельным модулем (lib/mekofix_v4.sh). Раньше сюда можно
    # было передать v4: генерились правила V2, а в state писалось version=v4 —
    # и статус врал о том, что реально работает.
    case "${1:-v3}" in v2|v3) ;; *) log_error "mekofix_apply: версия '${1}' не поддерживается (только v2/v3)"; return 1 ;; esac
    local ver="${1:-v3}" ports="${2:-443}" backend="${3:-auto}" b
    # SSH не должен пострадать — на SSH-порт фикс не ставим никогда (как в V4).
    local _p
    for _p in ${ports//,/ }; do
        if _mf_port_is_ssh "$_p"; then
            type log_error >/dev/null 2>&1 && log_error "Порт ${_p} используется SSH — SYN-фикс на него не ставим (риск потери доступа)" >&2
            type log_dim >/dev/null 2>&1 && log_dim "SSH-порты: $(_mf_ssh_ports)" >&2
            return 1
        fi
    done
    b=$(mekofix_backend_detect "$backend" "$ver") || true
    if [ -z "$b" ]; then
        type log_error >/dev/null 2>&1 && log_error "Нет бэкенда для ${ver}: нужен nft либо iptables+xt_u32" >&2
        return 1
    fi
    mkdir -p "$MEKOFIX_DIR" 2>/dev/null
    mekofix_profile_apply_vars "${MEKOFIX_PROFILE:-$(mekofix_state_get profile)}"
    # Pro/Ленивый: на этом же порту отдаётся сайт-маскировка. Браузер открывает 6+
    # соединений сразу — при малом burst часть получит RST и сайт будет выглядеть
    # сломанным для DPI-пробы (маскировка теряет смысл). Поэтому поднимаем порог.
    if [ "${MEKOFIX_NOLIMIT:-0}" != "1" ] && [ "$(config_get mode 2>/dev/null)" = "pro" ] && [ "${MEKOFIX_BURST:-0}" -lt 15 ] 2>/dev/null; then
        MEKOFIX_BURST=15
        _mf_log "Pro-режим: burst поднят до 15 (на :443 живёт сайт-маскировка)"
    fi
    _mf_write_rules "$ver" "$b" "$ports"
    _mf_log_ensure
    if ! "$MEKOFIX_RULES" up >>"$MEKOFIX_LOG" 2>&1; then
        type log_error >/dev/null 2>&1 && log_error "Не удалось применить правила (детали: $MEKOFIX_LOG)" >&2
        "$MEKOFIX_RULES" down >/dev/null 2>&1 || true
        return 1
    fi
    if ! "$MEKOFIX_RULES" check >/dev/null 2>&1; then
        type log_error >/dev/null 2>&1 && log_error "Правила не подтвердились после применения — откат" >&2
        "$MEKOFIX_RULES" down >/dev/null 2>&1 || true
        return 1
    fi
    _mf_write_unit
    systemctl enable --now "${MEKOFIX_SVC}.service" >/dev/null 2>&1 || true
    [ "${MEKOFIX_PROFILE:-auto}" = "auto" ] && _mf_install_tuner
    _mf_state_save "$ver" "$b" "$ports"
    mekofix_builtin_conflicts >/dev/null && mekofix_disable_builtin
    _mf_cfg_set mekofix "$ver" >/dev/null 2>&1 || true
    _mf_cap; _mf_log "APPLY ok ver=$ver backend=$b ports=$ports"
    type log_success >/dev/null 2>&1 && log_success "SYN-фикс ${ver} включён (${b}, порты ${ports})" >&2
    return 0
}

# ── ВЫКЛЮЧИТЬ ───────────────────────────────────────────────────────────────
mekofix_remove() {
    systemctl disable --now "${MEKOFIX_SVC}.service" >/dev/null 2>&1 || true
    systemctl disable --now "${MEKOFIX_TUNE_SVC}.timer" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${MEKOFIX_TUNE_SVC}.timer" "/etc/systemd/system/${MEKOFIX_TUNE_SVC}.service" 2>/dev/null
    [ -x "$MEKOFIX_RULES" ] && "$MEKOFIX_RULES" down >/dev/null 2>&1 || true
    # подстраховка, если скрипт правил утерян
    command -v nft >/dev/null 2>&1 && nft delete table inet "$MEKOFIX_NFT_TABLE" 2>/dev/null || true
    if command -v iptables >/dev/null 2>&1; then
        iptables -t filter -C INPUT -j "$MEKOFIX_IPT_CHAIN" 2>/dev/null && iptables -t filter -D INPUT -j "$MEKOFIX_IPT_CHAIN" 2>/dev/null || true
        iptables -t filter -F "$MEKOFIX_IPT_CHAIN" 2>/dev/null || true
        iptables -t filter -X "$MEKOFIX_IPT_CHAIN" 2>/dev/null || true
    fi
    rm -f "/etc/systemd/system/${MEKOFIX_SVC}.service" "$MEKOFIX_STATE" 2>/dev/null
    systemctl daemon-reload 2>/dev/null || true
    _mf_cfg_set mekofix "" >/dev/null 2>&1 || true
    _mf_log "REMOVE ok"
    type log_success >/dev/null 2>&1 && log_success "SYN-фикс выключен, правила сняты" >&2
    return 0
}

# ── Автотюнер: смотрит счётчики и сам поднимает burst, если легитимных клиентов бьёт лимит ──
mekofix_autotune() {
    mekofix_is_active || return 0
    # В режиме без ограничения крутить нечего: лимитера не существует.
    [ "$(mekofix_state_get nolimit)" = "1" ] && return 0
    [ "$(mekofix_state_get profile)" = "auto" ] || return 0
    # Тюнер запускается таймером в СВЕЖЕМ шелле, где MEKOFIX_RATE/TRUST_TTL равны
    # дефолтам файла (54/minute, 6h). Без восстановления профиля он, «поднимая»
    # burst, втихую ужесточал rate втрое и укорачивал доверие вшестеро.
    mekofix_profile_apply_vars "$(mekofix_state_get profile)" 2>/dev/null || true
    command -v nft >/dev/null 2>&1 || return 0
    local dump lim rej trust burst
    dump=$(nft list table inet "$MEKOFIX_NFT_TABLE" 2>/dev/null) || return 0
    lim=$(printf '%s' "$dump"   | grep 'other_limited' | grep -oE 'packets [0-9]+' | head -1 | awk '{print $2}')
    rej=$(printf '%s' "$dump"   | grep 'other_reject'  | grep -oE 'packets [0-9]+' | head -1 | awk '{print $2}')
    trust=$(printf '%s' "$dump" | grep 'trusted_nolimit' | grep -oE 'packets [0-9]+' | head -1 | awk '{print $2}')
    lim=${lim:-0}; rej=${rej:-0}; trust=${trust:-0}
    burst=$(mekofix_state_get burst); [[ "$burst" =~ ^[0-9]+$ ]] || burst="${MEKOFIX_BURST:-35}"
    # burst из state не должен опускать нас ниже профильного значения
    [ "$burst" -lt "${MEKOFIX_BURST:-0}" ] && burst="${MEKOFIX_BURST}"
    local _cap=$(( ${MEKOFIX_BURST:-35} * 2 )); [ "$_cap" -lt 30 ] && _cap=30
    # признак беды: есть живые клиенты (доверенные/принятые), но отбитых в разы больше
    if [ "$((trust+lim))" -gt 20 ] && [ "$rej" -gt "$(( (trust+lim) * 3 ))" ] && [ "$burst" -lt "$_cap" ]; then
        burst=$((burst+5)); [ "$burst" -gt "$_cap" ] && burst="$_cap"
        sed -i "s/^burst=.*/burst=$burst/" "$MEKOFIX_STATE" 2>/dev/null || printf 'burst=%s\n' "$burst" >> "$MEKOFIX_STATE"
        MEKOFIX_BURST="$burst"
        _mf_log "AUTOTUNE: burst → $burst (trusted=$trust limited=$lim rejected=$rej)"
        local v b p
        v=$(mekofix_state_get version); b=$(mekofix_state_get backend); p=$(mekofix_state_get ports)
        _mf_write_rules "${v:-v3}" "${b:-nft}" "${p:-443}" && "$MEKOFIX_RULES" up >/dev/null 2>&1
    fi
    return 0
}
_mf_install_tuner() {
    # каталог берём из GOTELEGRAM_DIR: функции _mf_dir не существует, и её вызов молча давал /opt/gotelegram даже при нестандартной установке
    local gd; gd="${GOTELEGRAM_DIR:-/opt/gotelegram}"
    cat > "/etc/systemd/system/${MEKOFIX_TUNE_SVC}.service" <<EOF
[Unit]
Description=goTelegram SYN-fix autotune
[Service]
Type=oneshot
ExecStart=/bin/bash -c 'source ${gd}/lib/common.sh 2>/dev/null; source ${gd}/lib/mekofix.sh; mekofix_autotune'
EOF
    cat > "/etc/systemd/system/${MEKOFIX_TUNE_SVC}.timer" <<EOF
[Unit]
Description=goTelegram SYN-fix autotune каждые 5 мин
[Timer]
OnBootSec=300
OnUnitActiveSec=300
[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now "${MEKOFIX_TUNE_SVC}.timer" >/dev/null 2>&1 || true
}

mekofix_is_active() { [ -x "$MEKOFIX_RULES" ] && "$MEKOFIX_RULES" check >/dev/null 2>&1; }

mekofix_status() {
    local ver bk ports
    ver=$(mekofix_state_get version); bk=$(mekofix_state_get backend); ports=$(mekofix_state_get ports)
    # key=value — машинный формат. Значение локализовать нельзя: при переключении
    # языка любой парсер, ищущий "ВКЛ", молча ломается. Перевод — в месте показа.
    if mekofix_is_active; then echo "state=on"; else echo "state=off"; fi
    echo "version=${ver:-—}"; echo "backend=${bk:-—}"; echo "ports=${ports:-—}"
    # systemctl is-enabled на отсутствующем юните ПЕЧАТАЕТ "not-found" И возвращает
    # ненулевой код — срабатывают обе половины `||`, и значение становится двухстрочным.
    local unit; unit=$(systemctl is-enabled "${MEKOFIX_SVC}.service" 2>/dev/null | head -n1)
    case "$unit" in ""|not-found) unit="нет" ;; esac
    echo "unit=$unit"
}

mekofix_status_json() {
    local on ver bk ports unit
    mekofix_is_active && on=true || on=false
    ver=$(mekofix_state_get version); bk=$(mekofix_state_get backend); ports=$(mekofix_state_get ports)
    # То же, что в mekofix_status: без head -n1 сюда приезжало "not-found\nnone",
    # перевод строки уезжал внутрь JSON-строки и делал весь ответ невалидным —
    # --selftest краснел на каждом сервере, где mekofix не установлен.
    unit=$(systemctl is-enabled "${MEKOFIX_SVC}.service" 2>/dev/null | head -n1)
    case "$unit" in ""|not-found) unit="none" ;; esac
    printf '{"active":%s,"version":"%s","backend":"%s","ports":"%s","unit":"%s"}\n' \
        "$on" "${ver//\"/}" "${bk//\"/}" "${ports//\"/}" "${unit//\"/}"
}

# счётчики срабатываний (для диагностики: растут ли ios/limited/reject)
mekofix_counters() {
    [ -x "$MEKOFIX_RULES" ] || { echo "нет данных"; return 1; }
    "$MEKOFIX_RULES" show 2>/dev/null | grep -E 'ios_nolimit|other_limited|other_reject|trusted_nolimit|RETURN|ACCEPT|REJECT|u32' | head -20
}
