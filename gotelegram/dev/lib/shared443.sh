#!/bin/bash
# goTelegram Pro — shared TCP/443 dispatcher helpers

SHARED443_CONFIG="${SHARED443_CONFIG:-/opt/gotelegram/shared-443.json}"
SHARED443_STREAM_CONF="${SHARED443_STREAM_CONF:-/etc/nginx/stream-conf.d/gotelegram-shared443.conf}"
SHARED443_TELEMT_PORT="${SHARED443_TELEMT_PORT:-7443}"
SHARED443_PUBLIC_PORT="${SHARED443_PUBLIC_PORT:-443}"

SHARED443_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
type log_error >/dev/null 2>&1 || source "$SHARED443_LIB_DIR/common.sh"
type install_nginx >/dev/null 2>&1 || source "$SHARED443_LIB_DIR/website.sh"

shared443_detect_nginx_stream() {
    # ВАЖНО про `--with-stream=dynamic`. На Ubuntu nginx собран именно так, и
    # прежняя проверка считала это «stream есть» — подстрока `--with-stream`
    # совпадает. На деле «=dynamic» означает ровно обратное: модуль вынесен в
    # отдельный пакет, без него директива `stream` неизвестна. Проверено на
    # живом сервере: детектор говорил «есть», пакет не ставился, nginx падал на
    # `unknown directive "stream"`.
    local v; v="$(nginx -V 2>&1)"

    # Статическая сборка: stream внутри бинаря, ставить нечего. Требуем ПРОБЕЛ
    # или конец строки после «--with-stream»: иначе шаблон цепляет соседей —
    # --with-stream_ssl_module, --with-stream_realip_module и прочие, которые
    # есть всегда и о наличии самого модуля не говорят ничего.
    printf '%s' "$v" | grep -Eq -- '--with-stream([[:space:]]|$)' && return 0

    # Динамическая: годится, только если модуль реально загружается.
    if printf '%s' "$v" | grep -q -- '--with-stream=dynamic'; then
        [ -f /usr/lib/nginx/modules/ngx_stream_module.so ] || return 1
        nginx -T 2>/dev/null | grep -q 'ngx_stream_module' && return 0
        ls /etc/nginx/modules-enabled/*stream* >/dev/null 2>&1 && return 0
        return 1
    fi
    return 1
}

shared443_install_stream_module() {
    if shared443_detect_nginx_stream; then
        return 0
    fi

    case "$(get_pkg_manager 2>/dev/null || echo unknown)" in
        apt)
            apt_update >/dev/null 2>&1 || true
            apt_install libnginx-mod-stream || return 1
            ;;
        dnf|yum)
            install_pkg nginx-mod-stream || true
            ;;
    esac

    shared443_detect_nginx_stream
}

shared443_ensure_nginx_include() {
    mkdir -p /etc/nginx/stream-conf.d
    if nginx -T 2>/dev/null | grep -q '/etc/nginx/stream-conf.d/\*.conf'; then
        return 0
    fi
    if grep -Eq '^[[:space:]]*stream[[:space:]]*\{' /etc/nginx/nginx.conf 2>/dev/null; then
        log_warning "В nginx уже есть stream-блок, но нет include /etc/nginx/stream-conf.d/*.conf"
        log_dim "Добавьте include вручную или перенесите $SHARED443_STREAM_CONF в существующий stream-блок."
        return 1
    fi

    cp /etc/nginx/nginx.conf "/etc/nginx/nginx.conf.gotelegram.$(date +%Y%m%d_%H%M%S).bak" 2>/dev/null || true
    cat >> /etc/nginx/nginx.conf <<'EOF'

# goTelegram Pro shared TCP/443 routes
stream {
    include /etc/nginx/stream-conf.d/*.conf;
}
EOF
}

shared443_rewrite_telemt_bind() {
    local listen_port="${1:-$SHARED443_TELEMT_PORT}"
    local public_port="${2:-$SHARED443_PUBLIC_PORT}"
    local listen_addr="${3:-127.0.0.1}"

    command -v python3 >/dev/null 2>&1 || {
        log_error "python3 нужен для безопасного изменения $TELEMT_CONFIG"
        return 1
    }

    python3 - "$TELEMT_CONFIG" "$listen_port" "$public_port" "$listen_addr" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
listen_port = sys.argv[2]
public_port = sys.argv[3]
listen_addr = sys.argv[4]
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
out = []
section = ""
server_seen = False
server_port_seen = False
server_addr_seen = False
links_seen = False
public_seen = False
# generate_telemt_toml пишет главный listener явным [[server.listeners]]: при нём
# ядро legacy listen_addr_ipv4 не читает, и правка одной этой строки оставляла
# telemt на внешнем 443 — там, где должен стоять nginx stream. Переносим сам
# listener. WEB-listener (transport = "web") живёт на loopback своим портом —
# его не трогаем.
has_listeners = any(l.strip() == "[[server.listeners]]" for l in lines)
listener_buf = []

def flush_listener():
    is_web = any(re.match(r'transport\s*=\s*"web"', l.strip()) for l in listener_buf)
    has_port = any(re.match(r'port\s*=', l.strip()) for l in listener_buf)
    for l in listener_buf:
        st = l.strip()
        if not is_web and re.match(r'ip\s*=', st):
            out.append(f'ip = "{listen_addr}"')
            if not has_port:
                out.append(f"port = {listen_port}")
            continue
        if not is_web and re.match(r'port\s*=', st):
            out.append(f"port = {listen_port}")
            continue
        out.append(l)
    listener_buf.clear()

def flush_section(next_line=None):
    global section, server_port_seen, server_addr_seen, public_seen
    if section == "server.listeners":
        flush_listener()
    if section == "server":
        if not server_port_seen:
            out.append(f"port = {listen_port}")
        if not server_addr_seen and not has_listeners:
            out.append(f'listen_addr_ipv4 = "{listen_addr}"')
    if section == "general.links" and not public_seen:
        out.append(f"public_port = {public_port}")
    if next_line is not None:
        out.append(next_line)

for raw in lines:
    stripped = raw.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        flush_section(raw)
        section = stripped.strip("[]")
        if section == "server":
            server_seen = True
            server_port_seen = False
            server_addr_seen = False
        elif section == "general.links":
            links_seen = True
            public_seen = False
        continue

    if section == "server.listeners":
        listener_buf.append(raw)
        continue
    if section == "server" and stripped.startswith("port") and "=" in stripped:
        out.append(f"port = {listen_port}")
        server_port_seen = True
        continue
    if section == "server" and stripped.startswith("listen_addr_ipv4") and "=" in stripped:
        out.append(f'listen_addr_ipv4 = "{listen_addr}"')
        server_addr_seen = True
        continue
    if section == "general.links" and stripped.startswith("public_port") and "=" in stripped:
        out.append(f"public_port = {public_port}")
        public_seen = True
        continue
    out.append(raw)

flush_section()
if not links_seen:
    if out and out[-1].strip():
        out.append("")
    out.extend(["[general.links]", f"public_port = {public_port}"])
if not server_seen:
    if out and out[-1].strip():
        out.append("")
    out.extend(["[server]", f"port = {listen_port}", f'listen_addr_ipv4 = "{listen_addr}"'])

tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(path)
PY
}

shared443_write_stream_config() {
    local domain="$1"
    local xray_domain="${2:-}"
    local xray_target="${3:-}"
    local telemt_target="${4:-127.0.0.1:${SHARED443_TELEMT_PORT}}"

    mkdir -p "$(dirname "$SHARED443_STREAM_CONF")"
    {
        echo "# goTelegram Pro shared TCP/443 dispatcher"
        echo "# Browser/Telegram for goTelegram domain goes to telemt; telemt masks the site to nginx."
        echo "map \$ssl_preread_server_name \$gotelegram_shared443_backend {"
        echo "    hostnames;"
        if [[ -n "$xray_domain" && -n "$xray_target" ]]; then
            echo "    ${xray_domain} ${xray_target};"
        fi
        echo "    default ${telemt_target};"
        echo "}"
        echo ""
        echo "server {"
        echo "    listen 0.0.0.0:${SHARED443_PUBLIC_PORT};"
        echo "    proxy_pass \$gotelegram_shared443_backend;"
        echo "    ssl_preread on;"
        echo "    proxy_connect_timeout 5s;"
        echo "    proxy_timeout 10m;"
        echo "}"
    } > "$SHARED443_STREAM_CONF"

    mkdir -p "$(dirname "$SHARED443_CONFIG")"
    if command -v jq >/dev/null 2>&1; then
        jq -n \
            --arg domain "$domain" \
            --arg telemt "$telemt_target" \
            --arg xdomain "$xray_domain" \
            --arg xtarget "$xray_target" \
            --arg updated "$(date -Iseconds)" \
            --argjson public_port "$SHARED443_PUBLIC_PORT" \
            '{
                enabled: true,
                dispatcher: "nginx-stream",
                public_port: $public_port,
                domain: $domain,
                telemt_target: $telemt,
                site_target: "127.0.0.1:8443",
                xray_routes: (if ($xdomain != "" and $xtarget != "") then [{public: ($xdomain + ":443"), target: $xtarget}] else [] end),
                updated_at: $updated
            }' > "$SHARED443_CONFIG"
    else
        cat > "$SHARED443_CONFIG" <<EOF
{"enabled":true,"dispatcher":"nginx-stream","public_port":${SHARED443_PUBLIC_PORT},"domain":"${domain}","telemt_target":"${telemt_target}","site_target":"127.0.0.1:8443","xray_routes":[],"updated_at":"$(date -Iseconds)"}
EOF
    fi
    chmod 600 "$SHARED443_CONFIG" 2>/dev/null || true
}

shared443_enable() {
    local domain="$1"
    local xray_domain="${2:-}"
    local xray_target="${3:-}"
    local telemt_target="127.0.0.1:${SHARED443_TELEMT_PORT}"

    [[ -n "$domain" ]] || domain="$(config_get domain 2>/dev/null || echo "")"
    [[ -n "$domain" ]] || {
        log_error "Не указан домен goTelegram Pro для shared-443"
        return 1
    }

    # WEB на отдельном имени уже держит свой stream-сервер на 443, а главный
    # listener ядра ждёт PROXY-заголовок — второй диспетчер сломал бы оба
    if type web_front_in_core >/dev/null 2>&1 && web_front_in_core; then
        log_error "shared-443 несовместим с WEB на отдельном имени — сначала выключите WEB"
        return 1
    fi

    install_nginx || return 1
    shared443_install_stream_module || {
        log_error "nginx stream/ssl_preread недоступен"
        return 1
    }
    shared443_ensure_nginx_include || return 1

    # Снимки конфигов ДО правок: без них после неудачи вернуть 443 наружу нечем.
    local stream_bak="" telemt_bak="" telemt_moved=0 failed=""
    if [ -f "$SHARED443_STREAM_CONF" ]; then
        stream_bak="${SHARED443_STREAM_CONF}.gotelegram.bak"
        cp -p "$SHARED443_STREAM_CONF" "$stream_bak" 2>/dev/null || stream_bak=""
    fi
    if [ -f "${TELEMT_CONFIG:-}" ]; then
        telemt_bak="${TELEMT_CONFIG}.shared443.bak"
        cp -p "$TELEMT_CONFIG" "$telemt_bak" 2>/dev/null || telemt_bak=""
    fi

    # Порядок важен: nginx -t портов не занимает, поэтому проверяем диспетчер
    # ПЕРЕД уводом telemt на loopback — иначе при негодном nginx внешний 443 пуст.
    shared443_write_stream_config "$domain" "$xray_domain" "$xray_target" "$telemt_target"
    if ! nginx -t 2>/dev/null; then
        nginx -t
        failed="nginx -t не прошёл"
    elif ! shared443_rewrite_telemt_bind "$SHARED443_TELEMT_PORT" "$SHARED443_PUBLIC_PORT" "127.0.0.1"; then
        failed="не удалось перевести telemt на ${telemt_target}"
    else
        telemt_moved=1
        systemctl restart "$TELEMT_SERVICE" 2>/dev/null || true
        systemctl restart nginx || failed="nginx не запустился на ${SHARED443_PUBLIC_PORT}"
    fi

    if [ -n "$failed" ]; then
        log_error "shared-443: ${failed} — откатываю, telemt остаётся на ${SHARED443_PUBLIC_PORT}"
        # Откат обязан вернуть и nginx.conf. shared443_ensure_nginx_include
        # дописывает туда блок `stream { … }`; при неудаче он оставался, и
        # nginx.conf становился НЕГОДНЫМ. Работающий процесс этого не замечает —
        # он держит прежний конфиг в памяти, — но любой следующий reload или
        # перезагрузка машины роняли веб целиком. Найдено на живом сервере.
        local _ncbak
        _ncbak="$(ls -t /etc/nginx/nginx.conf.gotelegram.*.bak 2>/dev/null | head -1)"
        if [ -n "$_ncbak" ] && ! nginx -t >/dev/null 2>&1; then
            cp -p "$_ncbak" /etc/nginx/nginx.conf 2>/dev/null \
                && log_warning "shared-443: nginx.conf возвращён из ${_ncbak}"
        fi
        # Свой stream-конфиг убираем: иначе он сломает любой следующий reload nginx.
        if [ -n "$stream_bak" ]; then
            mv -f "$stream_bak" "$SHARED443_STREAM_CONF" 2>/dev/null || true
        else
            rm -f "$SHARED443_STREAM_CONF" "$SHARED443_CONFIG" 2>/dev/null || true
        fi
        if [ "$telemt_moved" = "1" ]; then
            if [ -n "$telemt_bak" ]; then
                mv -f "$telemt_bak" "$TELEMT_CONFIG" 2>/dev/null || true
            else
                shared443_rewrite_telemt_bind "$SHARED443_PUBLIC_PORT" "$SHARED443_PUBLIC_PORT" "0.0.0.0" || true
            fi
            systemctl restart "$TELEMT_SERVICE" 2>/dev/null || true
        fi
        [ -n "$telemt_bak" ] && rm -f "$telemt_bak" 2>/dev/null
        return 1
    fi

    [ -n "$stream_bak" ] && rm -f "$stream_bak" 2>/dev/null
    [ -n "$telemt_bak" ] && rm -f "$telemt_bak" 2>/dev/null
    log_success "shared-443 включён: 0.0.0.0:${SHARED443_PUBLIC_PORT} -> nginx stream -> telemt ${telemt_target}"
    if [[ -n "$xray_domain" && -n "$xray_target" ]]; then
        log_success "Xray route: ${xray_domain}:443 -> ${xray_target}"
    fi
}

# ── Снятие ────────────────────────────────────────────────────────────────────
# Обратная операция к shared443_enable. Раньше её не существовало вовсе, и
# удаление продукта оставляло stream-конфиг в nginx: тот продолжал держать
# 0.0.0.0:443 и раскидывать по SNI на 127.0.0.1:7443, где после удаления telemt
# уже никто не слушает. Домен Xray, прописанный явной строкой карты, продолжал
# работать — а ВСЁ остальное на 443 (обращение по IP, любое другое имя, любой
# новый сервис, который захотят поставить на этот порт) умирало молча, и причину
# — осиротевший файл — ничто не называло.
#
# $1 = 1 (по умолчанию) вернуть telemt на внешний 443; при удалении продукта
# передаётся 0 — telemt всё равно уходит.
shared443_disable() {
    local restore_telemt="${1:-1}"
    local had=0
    [ -f "$SHARED443_STREAM_CONF" ] && had=1
    rm -f "$SHARED443_STREAM_CONF" "$SHARED443_CONFIG" 2>/dev/null || true

    # Порядок важен: сначала освободить 443 у nginx, и только потом возвращать
    # туда telemt — иначе он не сможет занять уже занятый порт.
    if [ "$had" = "1" ]; then
        if nginx -t 2>/dev/null; then
            systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        else
            log_warning "shared-443: nginx -t не прошёл после снятия диспетчера — перезагрузку не делаю, проверьте конфиг вручную"
        fi
    fi

    if [ "$restore_telemt" = "1" ] && [ -f "${TELEMT_CONFIG:-}" ]; then
        if shared443_rewrite_telemt_bind "$SHARED443_PUBLIC_PORT" "$SHARED443_PUBLIC_PORT" "0.0.0.0"; then
            systemctl restart "$TELEMT_SERVICE" 2>/dev/null || true
        else
            log_warning "shared-443: не удалось вернуть telemt на ${SHARED443_PUBLIC_PORT} — проверьте $TELEMT_CONFIG"
        fi
    fi

    [ "$had" = "1" ] && log_success "shared-443 снят: порт ${SHARED443_PUBLIC_PORT} освобождён от диспетчера"
    return 0
}

shared443_detect_direct_conflict() {
    ss -ltnp 2>/dev/null | grep -E '(:|])443[[:space:]]' | grep -Eiv '(nginx|telemt)' || true
}

shared443_status() {
    echo "shared-443 config: $SHARED443_CONFIG"
    [ -f "$SHARED443_CONFIG" ] && cat "$SHARED443_CONFIG" || echo "not enabled"
    local conflict
    conflict="$(shared443_detect_direct_conflict)"
    if [[ -n "$conflict" ]]; then
        echo ""
        echo "direct 443 listeners that need migration behind dispatcher:"
        echo "$conflict"
    fi
}

export -f shared443_detect_nginx_stream shared443_install_stream_module shared443_ensure_nginx_include
export -f shared443_rewrite_telemt_bind shared443_write_stream_config shared443_enable
export -f shared443_detect_direct_conflict shared443_status
