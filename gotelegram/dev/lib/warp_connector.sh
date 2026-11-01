#!/bin/bash
# ══════════════════════════════════════════════════════════════════
#  warp_connector.sh — portable Cloudflare WARP connector library
#  Extracted from goWARP PRO v2.3 (anten-ka/go_warp_pro, branch alpha)
#  Every function is battle-tested on live VPS (Ubuntu 22-26.04) with
#  real WARP+ keys. See SKILL.md next to this file for full docs.
#
#  Quick start (source, then call):
#      source /path/to/warp_connector.sh
#      tier=$(wc_setup_socks_gateway "$WARP_PLUS_KEY")   # "" for Free
#      [ "$tier" = plus ] || echo "not on Plus: $tier"
#      curl --socks5-hostname 127.0.0.1:40000 https://example.com
#
#  Contract for every function:
#    - return 0 on success, non-zero on failure;
#    - print a machine-readable RESULT to stdout (tier / reason token);
#    - print human diagnostics to stderr (never the raw key — masked);
#    - safe to source under `set -euo pipefail` (orchestrators guard $()).
#
#  Exit-code / reason tokens (see wc_last_reason):
#    apply_* :  0 ok | 1 precondition | 2 got-free | 3 unverifiable | rc wgcf
#    reasons :  ok | bad-format | no-warpcli | no-wgcf | no-account |
#               device-limit-1056 | key-free | unverifiable | rejected
# ══════════════════════════════════════════════════════════════════

# ---- tunables (override via env before sourcing, or reassign after) --
WC_SOCKS_PORT="${WC_SOCKS_PORT:-40000}"
WC_WGCF_VERSION="${WC_WGCF_VERSION:-2.2.30}"
WC_WGCF_BIN="${WC_WGCF_BIN:-/root/wgcf}"
WC_WGCF_ACCOUNT="${WC_WGCF_ACCOUNT:-/root/wgcf-account.toml}"
WC_WGCF_PROFILE="${WC_WGCF_PROFILE:-/root/wgcf-profile.conf}"
WC_WARP_CONF="${WC_WARP_CONF:-/opt/warp/warp.conf}"
WC_LOCK_DIR="${WC_LOCK_DIR:-/run/warp-connector}"
WC_INSTALL_SYSLOG_FILTER="${WC_INSTALL_SYSLOG_FILTER:-1}"   # 1 = auto-silence warp-svc in rsyslog

# Pinned wgcf checksums (official v2.2.30 checksums.txt). If you bump
# WC_WGCF_VERSION, update these or set WC_WGCF_SHA_<arch> in the environment.
WC_WGCF_SHA_amd64="${WC_WGCF_SHA_amd64:-fc443008fe29a6f0b05b45d27436b7ce89e87a6836718597ccb39e41da418304}"
WC_WGCF_SHA_arm64="${WC_WGCF_SHA_arm64:-761f1d35157feb7c527cd8b13fbe428b47a9786af207a73e380630609c3d1f40}"
WC_WGCF_SHA_armv7="${WC_WGCF_SHA_armv7:-e7b2db8265295866d4e4f49163c23771e7b4e289d5e74d04cdacea291fd9c500}"

WC_LAST_REASON=""
wc_log()  { echo "[warp-connector] $*" >&2; }
wc_reason() { WC_LAST_REASON="$1"; }
wc_last_reason() { echo "$WC_LAST_REASON"; }

# ---- guards -------------------------------------------------------
wc_require_root() { [ "$(id -u)" -eq 0 ] || { wc_log "need root"; return 1; }; }
wc_require_debianlike() {
    . /etc/os-release 2>/dev/null
    case "${ID:-}" in
        ubuntu|debian) return 0 ;;
        *) wc_log "unsupported distro '${ID:-?}' (need ubuntu/debian)"; return 1 ;;
    esac
}

# Serialize setup so two scripts don't corrupt the single warp-cli/wgcf identity.
# Usage: wc_with_lock <name> <function> [args...]
wc_with_lock() {
    local name="$1"; shift
    # Reentrant: если этот лок уже удержан в текущем стеке вызовов — выполняем напрямую
    # (иначе self-deadlock: apply_key → enable → wc_setup_warp_iface все берут "iface").
    case ":${_WC_LOCKS_HELD:-}:" in
        *":$name:"*) "$@"; return $? ;;
    esac
    mkdir -p "$WC_LOCK_DIR" 2>/dev/null
    if command -v flock >/dev/null 2>&1; then
        ( flock -w 30 9 || { wc_log "another warp-connector op is running ($name)"; exit 9; }
          export _WC_LOCKS_HELD="${_WC_LOCKS_HELD:+$_WC_LOCKS_HELD:}$name"
          "$@" ) 9>"$WC_LOCK_DIR/${name}.lock"
    else
        _WC_LOCKS_HELD="${_WC_LOCKS_HELD:+$_WC_LOCKS_HELD:}$name" "$@"
    fi
}

# Mask a key for logs — never log the full key (v2.3 mask_key)
wc_mask_key() { local k="$1"; [ -z "$k" ] && { echo ""; return; }; echo "***-***-***-${k: -4}"; }

# Validate WARP+ key: 4-4-4-4 or 8-8-8 (v2.3 is_valid_warp_plus_key)
wc_is_valid_plus_key() {
    local k="$1"
    [[ "$k" =~ ^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$ ]] && return 0
    [[ "$k" =~ ^[A-Za-z0-9]{8}-[A-Za-z0-9]{8}-[A-Za-z0-9]{8}$ ]] && return 0
    return 1
}

# ---- SECURITY: scrub the plaintext-key leak -----------------------
# warp-svc writes the PLAINTEXT license to /var/log/cloudflare-warp/*.txt AND,
# via journald, into /var/log/syslog (+ messages/daemon.log on rsyslog hosts).
# Observed live (goWARP bridge 015). Call IMMEDIATELY after every
# `warp-cli registration license`. This handles ALL THREE sinks.
wc_install_syslog_filter() {
    [ "$WC_INSTALL_SYSLOG_FILTER" = "1" ] || return 0
    [ -d /etc/rsyslog.d ] || return 0
    local f=/etc/rsyslog.d/99-warp-svc-silence.conf
    [ -f "$f" ] && return 0
    echo ':programname, isequal, "warp-svc" stop' > "$f"
    systemctl restart rsyslog >/dev/null 2>&1 || service rsyslog restart >/dev/null 2>&1 || true
    wc_log "installed rsyslog filter to silence warp-svc (prevents future syslog leak)"
}

wc_scrub_cf_logs() {
    # 1. Cloudflare's own daemon logs
    if [ -d /var/log/cloudflare-warp ]; then
        find /var/log/cloudflare-warp -type f -exec truncate -s 0 {} + 2>/dev/null
        find /var/log/cloudflare-warp -type f -exec chmod 600 {} + 2>/dev/null
        chmod 700 /var/log/cloudflare-warp 2>/dev/null
    fi
    # 2. rsyslog text sinks that already captured the key (best-effort, key-lines only)
    local lf
    for lf in /var/log/syslog /var/log/messages /var/log/daemon.log; do
        [ -f "$lf" ] || continue
        sed -i '/warp-svc/{/license/d}' "$lf" 2>/dev/null || true
    done
    # 3. journald: rotate only. `--vacuum-time=1s` is HOST-WIDE — it destroys the
    #    journal of every service on the box (and this function is reachable from
    #    the web-admin API, i.e. remotely triggerable). The rsyslog filter above
    #    already stops FUTURE leaks, so the collateral is not worth it.
    #    Rotate is harmless: it just closes the active file.
    if command -v journalctl >/dev/null 2>&1; then
        journalctl --rotate >/dev/null 2>&1 || true
        # Опциональная разовая уборка уже утёкшей записи — ТОЛЬКО по явному
        # осознанному запросу оператора (WC_JOURNAL_VACUUM=1), никогда по умолчанию.
        if [ "${WC_JOURNAL_VACUUM:-0}" = "1" ]; then
            sleep 2
            journalctl --vacuum-time=1s >/dev/null 2>&1 || true
            wc_log "journald vacuum executed on explicit WC_JOURNAL_VACUUM=1 (host-wide!)"
        fi
    fi
    wc_log "cloudflare-warp key sinks scrubbed (cf-logs + syslog + journald rotate)"
    # NOTE: a pre-existing key line may still sit in an already-rotated journal
    # archive. Wiping it means wiping every other service's history too, so we
    # leave that decision to the operator (WC_JOURNAL_VACUUM=1).
}

# ---- tier verification via Cloudflare edge (the ONLY truth) -------
#   warp=plus → Plus;  warp=on → Free;  warp=off → not via WARP; empty → unknown
wc_parse_trace() {
    local body="$1" val
    [ -z "$body" ] && { echo ""; return 1; }
    val=$(echo "$body" | grep -E '^warp=' | head -1 | cut -d= -f2 | tr -d '\r\n')
    case "$val" in
        plus) echo "plus"; return 0 ;;
        on)   echo "free"; return 0 ;;
        off)  echo "off";  return 0 ;;
        *)    echo "";     return 1 ;;
    esac
}
wc_verify_socks() {
    local port="${1:-$WC_SOCKS_PORT}" body
    body=$(curl -s --max-time 5 --proxy "socks5h://127.0.0.1:${port}" \
        https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null)
    wc_parse_trace "$body"
}
wc_verify_iface() {
    local iface="${1:-warp}" body
    body=$(curl -s --max-time 5 --interface "$iface" \
        https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null)
    wc_parse_trace "$body"
}
wc_verify_container() {
    local c="$1" body
    [ -z "$c" ] && { echo ""; return 1; }
    body=$(docker exec "$c" sh -c \
        'curl -s --max-time 5 --interface warp https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null' 2>/dev/null)
    wc_parse_trace "$body"
}

# ---- state predicates (cheap, local — no network) -----------------
wc_is_installed_3xui() { command -v warp-cli >/dev/null 2>&1; }
wc_is_registered_3xui() {
    warp-cli --accept-tos registration show >/dev/null 2>&1 \
        && ! warp-cli --accept-tos registration show 2>/dev/null | grep -qi "registration missing"
}
wc_is_connected_3xui() {
    local st; st=$(warp-cli --accept-tos status 2>/dev/null)
    echo "$st" | grep -qi "status.*connected" && ! echo "$st" | grep -qi "disconnected"
}
wc_is_installed_iface() { [ -x "$WC_WGCF_BIN" ] && [ -f "$WC_WARP_CONF" ]; }
# shellcheck disable=SC2120  # optional arg; callers may pass an interface name
wc_is_up_iface() { ip addr show "${1:-warp}" >/dev/null 2>&1; }

# One-line status summary for a caller's dashboard/logs
wc_status() {
    if wc_is_installed_3xui; then
        printf 'path-A(socks:%s) installed=%s connected=%s tier=%s\n' \
            "$WC_SOCKS_PORT" yes \
            "$(wc_is_connected_3xui && echo yes || echo no)" \
            "$(wc_verify_socks || echo unknown)"
    fi
    if [ -f "$WC_WARP_CONF" ]; then
        printf 'path-B(iface:warp) up=%s tier=%s\n' \
            "$(wc_is_up_iface && echo yes || echo no)" \
            "$(wc_verify_iface || echo unknown)"
    fi
    [ ! -e "$WC_WARP_CONF" ] && ! wc_is_installed_3xui && echo "no WARP configured"
    return 0
}

# ═══════════════════════════════════════════════════════════════════
#  PATH A — warp-cli + SOCKS5 gateway (TCP; simplest; no interface)
# ═══════════════════════════════════════════════════════════════════

wc_install_warp_cli() {
    wc_require_root || return 1
    wc_require_debianlike || return 1
    wc_is_installed_3xui && { wc_log "warp-cli already installed"; return 0; }

    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y curl gnupg ca-certificates apt-transport-https >/dev/null 2>&1

    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
        | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg 2>/dev/null \
        || { wc_log "GPG key fetch failed"; return 1; }

    . /etc/os-release
    local native="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null)}" codename="" tested
    for tested in "$native" noble jammy focal; do
        [ -z "$tested" ] && continue
        curl -fsSL --max-time 5 "https://pkg.cloudflareclient.com/dists/${tested}/Release" >/dev/null 2>&1 \
            && { codename="$tested"; break; }
    done
    [ -z "$codename" ] && { wc_log "no compatible codename in Cloudflare repo"; return 1; }
    [ "$codename" != "$native" ] && wc_log "native '$native' not in repo, using '$codename'"

    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ ${codename} main" \
        > /etc/apt/sources.list.d/cloudflare-client.list
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y cloudflare-warp >/dev/null 2>&1
    wc_is_installed_3xui || { wc_log "cloudflare-warp install failed"; return 1; }

    systemctl enable --now warp-svc >/dev/null 2>&1
    local _i
    for _i in $(seq 1 15); do warp-cli --accept-tos status >/dev/null 2>&1 && break; sleep 1; done
    wc_log "warp-cli installed (repo codename: $codename)"
}

# Register only if not already registered — re-registering discards an applied
# Plus key (silent downgrade). Idempotent, unlike a bare `registration new`.
wc_register_3xui() {
    if wc_is_registered_3xui; then wc_log "already registered — keeping existing device"; return 0; fi
    warp-cli --accept-tos registration new >/dev/null 2>&1 || { wc_log "registration failed"; return 1; }
    wc_log "registered (Free)"
}

# Apply WARP+ key + verify + scrub. Sets wc_last_reason.
# Guarded so a stray `set -x` in the caller can't echo the key.
wc_apply_plus_3xui() {
    local plus_key="$1"
    wc_is_valid_plus_key "$plus_key" || { wc_reason bad-format; wc_log "bad key format"; return 1; }
    wc_is_installed_3xui || { wc_reason no-warpcli; wc_log "warp-cli not installed"; return 1; }

    local xtrace=0; case "$-" in *x*) xtrace=1; set +x ;; esac
    warp-cli --accept-tos registration license "$plus_key" >/dev/null 2>&1
    local rc=$?
    [ "$xtrace" = 1 ] && set -x
    wc_install_syslog_filter
    wc_scrub_cf_logs
    [ $rc -ne 0 ] && { wc_reason rejected; wc_log "warp-cli rejected key $(wc_mask_key "$plus_key") rc=$rc"; return "$rc"; }

    local acct_type
    acct_type=$(warp-cli --accept-tos registration show 2>/dev/null | grep -iE '^Account type' | head -1 \
        | sed -E 's/.*[Tt]ype:[[:space:]]*([A-Za-z+]+).*/\1/' | tr '[:upper:]' '[:lower:]')
    case "$acct_type" in
        premium|limited|"warp+"|unlimited) wc_reason ok; wc_log "Plus verified: Account type=$acct_type"; return 0 ;;
        free) wc_reason key-free; wc_log "Cloudflare says Free after apply — key invalid/expired/device-limit"; return 2 ;;
        "")
            if warp-cli --accept-tos account 2>/dev/null | grep -qiE 'unlimited|warp\+|premium|limited'; then
                wc_reason ok; wc_log "Plus verified via legacy 'account'"; return 0
            fi
            wc_reason unverifiable; wc_log "cannot verify Plus status"; return 3 ;;
        *) wc_reason ok; wc_log "unknown Account type='$acct_type' — assuming Plus"; return 0 ;;
    esac
}

# Switch to SOCKS5 proxy mode and connect. Guards against full-tunnel:
# if proxy mode cannot be set, ABORT rather than connect (connecting in the
# default full-tunnel mode would capture host traffic and can drop SSH).
wc_connect_3xui() {
    local port="${1:-$WC_SOCKS_PORT}"

    # SSH safety: the setting command's OWN exit code is the reliable signal
    # that proxy mode was accepted — far more robust than parsing `settings`
    # output (which lists a "Proxy port:" line regardless of active mode and
    # would fail open). If BOTH CLI dialects fail (version drift), we have NOT
    # set proxy mode, so we must NOT connect (default full-tunnel would capture
    # host traffic and can drop SSH on a remote box). Fail CLOSED.
    local mode_ok=0
    if   warp-cli --accept-tos mode proxy     >/dev/null 2>&1; then mode_ok=1
    elif warp-cli --accept-tos set-mode proxy >/dev/null 2>&1; then mode_ok=1
    fi
    warp-cli --accept-tos proxy port "$port"     >/dev/null 2>&1 \
        || warp-cli --accept-tos set-proxy-port "$port" >/dev/null 2>&1
    if [ "$mode_ok" -ne 1 ]; then
        wc_log "ABORT: could not set SOCKS proxy mode (both CLI dialects failed); refusing to connect (would risk full-tunnel/SSH loss)"
        return 1
    fi
    # Secondary confirmation (best-effort): if `settings` explicitly reports a
    # non-proxy active mode, bail too. Anchored on 'mode:' so 'Proxy port:'
    # can't false-match.
    local active_mode
    active_mode=$(warp-cli --accept-tos settings 2>/dev/null \
        | grep -iE '^[[:space:]]*mode[[:space:]]*:' | head -1 | tr '[:upper:]' '[:lower:]')
    if [ -n "$active_mode" ] && ! echo "$active_mode" | grep -q 'proxy'; then
        wc_log "ABORT: warp-cli reports active mode '${active_mode}' (not proxy); refusing to connect"
        return 1
    fi

    warp-cli --accept-tos connect >/dev/null 2>&1
    sleep 3
    wc_is_connected_3xui && { wc_log "connected — SOCKS5 at 127.0.0.1:$port"; return 0; }
    wc_log "connect not confirmed"; return 1
}

wc_egress_ip_socks() {
    local port="${1:-$WC_SOCKS_PORT}"
    curl -s4 --max-time 5 --proxy "socks5h://127.0.0.1:${port}" ifconfig.me 2>/dev/null || echo "N/A"
}

# Pause/resume without unregistering (keeps the device slot + Plus binding)
wc_disconnect_3xui() { warp-cli --accept-tos disconnect >/dev/null 2>&1; wc_log "disconnected (registration kept)"; }
wc_reconnect_3xui()  { warp-cli --accept-tos connect    >/dev/null 2>&1; sleep 2; wc_is_connected_3xui; }

# Full teardown (frees the device slot on the key!)
wc_teardown_3xui() {
    warp-cli --accept-tos disconnect >/dev/null 2>&1
    warp-cli --accept-tos registration delete >/dev/null 2>&1
    wc_log "disconnected + registration deleted (device slot freed)"
}

# ═══════════════════════════════════════════════════════════════════
#  PATH B — wgcf + wg-quick `warp` interface (L3; UDP/ICMP; containers)
# ═══════════════════════════════════════════════════════════════════

wc_install_wgcf() {
    # gotelegram-patch: wg-quick/wg обязательны для path B, коннектор их не ставит
    if ! command -v wg-quick >/dev/null 2>&1 || ! command -v wg >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=120 wireguard-tools >/dev/null 2>&1 \
          || { apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq -o DPkg::Lock::Timeout=120 wireguard-tools >/dev/null 2>&1; } \
          || wc_log "WARN: wireguard-tools install failed"
    fi
    [ -x "$WC_WGCF_BIN" ] && "$WC_WGCF_BIN" --help >/dev/null 2>&1 && return 0
    local arch wa=""
    arch=$(uname -m)
    case "$arch" in
        x86_64) wa="amd64" ;; aarch64) wa="arm64" ;; armv7l) wa="armv7" ;;
        *) wc_log "unsupported arch: $arch"; return 1 ;;
    esac
    local url="https://github.com/ViRb3/wgcf/releases/download/v${WC_WGCF_VERSION}/wgcf_${WC_WGCF_VERSION}_linux_${wa}"
    local want; eval "want=\${WC_WGCF_SHA_${wa}:-}"

    curl -fLI --max-time 10 "$url" >/dev/null 2>&1 || { wc_log "wgcf release not found: $url"; return 1; }
    local tmp; tmp=$(mktemp);
    curl -fL --max-time 60 -o "$tmp" "$url" 2>/dev/null || { rm -f "$tmp"; wc_log "wgcf download failed"; return 1; }

    # Supply-chain integrity: verify SHA256 against the pinned official checksum.
    if [ -n "$want" ]; then
        local got; got=$(sha256sum "$tmp" | awk '{print $1}')
        if [ "$got" != "$want" ]; then
            rm -f "$tmp"
            wc_log "wgcf checksum MISMATCH (want $want got $got) — refusing to install"
            return 1
        fi
        wc_log "wgcf checksum verified"
    else
        wc_log "WARN: no pinned checksum for arch '$wa' — installing UNVERIFIED binary"
    fi

    install -m 0755 "$tmp" "$WC_WGCF_BIN" && rm -f "$tmp"
    "$WC_WGCF_BIN" --help >/dev/null 2>&1 || { wc_log "wgcf binary broken"; return 1; }
    wc_log "wgcf ${WC_WGCF_VERSION} installed"
}

# Register a Cloudflare account (Free). NEVER `yes | wgcf` — SIGPIPE→rc=141
# under pipefail (goWARP P0.5). umask 077 closes the create→chmod race.
wc_register_wgcf() {
    [ -f "$WC_WGCF_ACCOUNT" ] && return 0
    ( umask 077; cd "$(dirname "$WC_WGCF_ACCOUNT")" && printf 'y\n' | "$WC_WGCF_BIN" register 2>/dev/null )
    if [ -f "$WC_WGCF_ACCOUNT" ]; then
        chmod 600 "$WC_WGCF_ACCOUNT"
        wc_log "wgcf registered (Free)"
        return 0
    fi
    # Cloudflare отдаёт 429 клиенту wgcf (Go-HTTP режется по TLS-отпечатку) —
    # тот же запрос из curl в ту же секунду проходит с кодом 200. Регистрируемся
    # напрямую и пишем оба файла сами: дальше по цепочке нужен только профиль.
    # 429 у wgcf бывает и временным — даём один повтор, потом идём в обход.
    sleep 3
    ( umask 077; cd "$(dirname "$WC_WGCF_ACCOUNT")" && printf 'y\n' | "$WC_WGCF_BIN" register 2>/dev/null )
    if [ -f "$WC_WGCF_ACCOUNT" ]; then
        chmod 600 "$WC_WGCF_ACCOUNT"
        wc_log "wgcf registered (Free, со второй попытки)"
        return 0
    fi
    wc_log "wgcf register failed — пробую прямую регистрацию (curl)"
    wc_register_curl || { wc_log "все способы регистрации отказали"; return 1; }
}

# Прямая регистрация в Cloudflare без wgcf. Пишет wgcf-account.toml и
# wgcf-profile.conf в том же формате, что и wgcf, — остальная цепочка не меняется.
wc_register_curl() {
    command -v wg >/dev/null 2>&1 || { wc_log "curl-регистрация: нет wg"; return 1; }
    command -v python3 >/dev/null 2>&1 || { wc_log "curl-регистрация: нет python3"; return 1; }
    local priv pub js dir
    dir=$(dirname "$WC_WGCF_ACCOUNT")
    priv=$(wg genkey 2>/dev/null) || return 1
    pub=$(printf '%s' "$priv" | wg pubkey 2>/dev/null) || return 1
    # Перебираем версии клиентского API: Cloudflare со временем отключает старые
    # и ограничивает отдельные, поэтому один зашитый адрес — точка отказа.
    local api ver body code attempt=0
    js=""
    for api_ver in "v0a2158 a-6.10-2158" "v0a1922 a-6.3-1922" "v0a977 a-6.30-977" "v0i1909051800 a-6.3-1909051800"; do
        api=${api_ver%% *}; ver=${api_ver##* }
        attempt=$((attempt+1))
        body=$(curl -s --max-time 30 -w '\n%{http_code}' -X POST "https://api.cloudflareclient.com/${api}/reg" \
            -H "Content-Type: application/json" -H "User-Agent: okhttp/3.12.1" \
            -H "CF-Client-Version: ${ver}" \
            -d "{\"install_id\":\"\",\"tos\":\"$(date -u +%Y-%m-%dT%H:%M:%S.000Z)\",\"key\":\"${pub}\",\"fcm_token\":\"\",\"type\":\"Android\",\"locale\":\"en_US\"}" 2>/dev/null)
        code=$(printf '%s' "$body" | tail -1)
        if [ "$code" = "200" ]; then
            js=$(printf '%s' "$body" | sed '$d')
            WC_REG_API="$api"; WC_REG_VER="$ver"
            wc_log "curl-регистрация: ${api} принял (попытка ${attempt})"
            break
        fi
        wc_log "curl-регистрация: ${api} → HTTP ${code:-нет ответа}"
        sleep 2
    done
    [ -n "$js" ] || { wc_log "curl-регистрация: ни одна версия API не ответила"; return 1; }

    local parsed
    parsed=$(printf '%s' "$js" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    cfg = d["config"]; peer = cfg["peers"][0]
    out = [d["id"], d["token"], d["account"]["license"],
           cfg["interface"]["addresses"]["v4"], cfg["interface"]["addresses"].get("v6", ""),
           peer["public_key"], peer["endpoint"]["host"]]
    if not all(out[:4]) or not out[5]: raise ValueError("неполный ответ")
    print("\t".join(out))
except Exception as e:
    print("ERR\t" + str(e)[:80])
' 2>/dev/null)
    case "$parsed" in ERR*|"") wc_log "curl-регистрация: Cloudflare ответил отказом (${parsed#ERR	})"; return 1 ;; esac

    local dev_id token license addr4 addr6 peer_pub endpoint
    IFS=$'\t' read -r dev_id token license addr4 addr6 peer_pub endpoint <<< "$parsed"
    [ -n "$endpoint" ] || endpoint="engage.cloudflareclient.com:2408"

    # Устройство регистрируется выключенным — включаем WARP, иначе туннель
    # поднимется, но трафик через Cloudflare не пойдёт.
    curl -s -o /dev/null --max-time 20 -X PATCH "https://api.cloudflareclient.com/${WC_REG_API:-v0a2158}/reg/${dev_id}" \
        -H "Content-Type: application/json" -H "User-Agent: okhttp/3.12.1" \
        -H "CF-Client-Version: ${WC_REG_VER:-a-6.10-2158}" -H "Authorization: Bearer ${token}" \
        -d '{"warp_enabled":true}' 2>/dev/null

    ( umask 077
      mkdir -p "$dir"
      cat > "$WC_WGCF_ACCOUNT" <<ACCEOF
access_token = '${token}'
device_id = '${dev_id}'
license_key = '${license}'
private_key = '${priv}'
ACCEOF
      cat > "$WC_WGCF_PROFILE" <<PROFEOF
[Interface]
PrivateKey = ${priv}
Address = ${addr4}/32${addr6:+, ${addr6}/128}
DNS = 1.1.1.1
MTU = 1280

[Peer]
PublicKey = ${peer_pub}
AllowedIPs = 0.0.0.0/0
AllowedIPs = ::/0
Endpoint = ${endpoint}
PROFEOF
    )
    [ -s "$WC_WGCF_ACCOUNT" ] && [ -s "$WC_WGCF_PROFILE" ] || { wc_log "curl-регистрация: файлы не записались"; return 1; }
    chmod 600 "$WC_WGCF_ACCOUNT" "$WC_WGCF_PROFILE"
    wc_log "зарегистрировано напрямую через curl (Free), профиль записан"
}

# Apply WARP+ via the NATIVE flag. Do NOT sed the key into the toml + plain
# `wgcf update` — that silently no-ops the tier upgrade (bridges 017/020).
# rc=1 + stderr 1056 = device limit. Sets wc_last_reason.
wc_apply_plus_wgcf() {
    local plus_key="$1"
    wc_is_valid_plus_key "$plus_key" || { wc_reason bad-format; wc_log "bad key format"; return 1; }
    [ -f "$WC_WGCF_ACCOUNT" ] || { wc_reason no-account; wc_log "no wgcf account — run wc_register_wgcf"; return 1; }
    [ -x "$WC_WGCF_BIN" ] || { wc_reason no-wgcf; wc_log "wgcf binary missing"; return 1; }

    # Inline cleanup before every return (no RETURN trap — a trap would clobber
    # a caller's own RETURN trap and late-expand $errf into the caller's frame).
    local errf; errf=$(mktemp); chmod 600 "$errf"
    local xtrace=0; case "$-" in *x*) xtrace=1; set +x ;; esac
    ( umask 077; cd "$(dirname "$WC_WGCF_ACCOUNT")" && "$WC_WGCF_BIN" update --license-key "$plus_key" >/dev/null 2>"$errf" )
    local rc=$?
    [ "$xtrace" = 1 ] && set -x
    chmod 600 "$WC_WGCF_ACCOUNT" 2>/dev/null   # wgcf may rewrite the toml at default umask

    if [ $rc -ne 0 ]; then
        local had1056=0; grep -q '1056' "$errf" 2>/dev/null && had1056=1
        rm -f "$errf"
        if [ "$had1056" = 1 ]; then
            wc_reason device-limit-1056
            wc_log "device limit hit (error 1056) — free a slot in the 1.1.1.1 app"
        else
            wc_reason rejected
            wc_log "wgcf update rejected key $(wc_mask_key "$plus_key") rc=$rc"
        fi
        return "$rc"
    fi
    rm -f "$errf"

    local acct_type
    acct_type=$( ( cd "$(dirname "$WC_WGCF_ACCOUNT")" && "$WC_WGCF_BIN" status 2>/dev/null ) \
        | grep -iE '^Account type' | head -1 | sed -E 's/.*[Tt]ype:[[:space:]]*([A-Za-z+]+).*/\1/' | tr '[:upper:]' '[:lower:]')
    [ -z "$acct_type" ] && acct_type=$(grep -E '^account_type[[:space:]]*=' "$WC_WGCF_ACCOUNT" 2>/dev/null \
        | sed -E "s/.*=[[:space:]]*['\"]([^'\"]*)['\"].*/\1/" | tr '[:upper:]' '[:lower:]')
    case "$acct_type" in
        limited|premium|unlimited) wc_reason ok; wc_log "Plus verified: account_type=$acct_type"; return 0 ;;
        free) wc_reason key-free; wc_log "Cloudflare says Free after key apply"; return 2 ;;
        *) wc_reason ok; wc_log "account_type ambiguous ('$acct_type') — treating rc=0 as success"; return 0 ;;
    esac
}

# Strip the license from the account toml (revert to Free) — v2.3 reset_wgcf_account_license
wc_reset_license_wgcf() {
    [ -f "$WC_WGCF_ACCOUNT" ] || return 0
    local tmpf; tmpf=$(mktemp -p "$(dirname "$WC_WGCF_ACCOUNT")" 2>/dev/null || mktemp); chmod 600 "$tmpf"
    awk '!/^[[:space:]]*license_key[[:space:]]*=/' "$WC_WGCF_ACCOUNT" > "$tmpf" 2>/dev/null || { rm -f "$tmpf"; return 1; }
    mv "$tmpf" "$WC_WGCF_ACCOUNT"; chmod 600 "$WC_WGCF_ACCOUNT"
    wc_log "wgcf license stripped (reverted to Free)"
}

wc_generate_profile() {
    # Профиль, записанный прямой регистрацией, самодостаточен: wgcf generate
    # ходит в тот же API и получил бы тот же 429, а переписывать рабочий файл
    # нечем. Признак — отсутствие приватного ключа в account.toml от wgcf.
    if [ -s "$WC_WGCF_PROFILE" ] && grep -q "^PrivateKey = " "$WC_WGCF_PROFILE" 2>/dev/null \
       && grep -q "^PublicKey = " "$WC_WGCF_PROFILE" 2>/dev/null; then
        local _pk_acc _pk_prof
        _pk_acc=$(awk -F"'" '/^private_key/{print $2}' "$WC_WGCF_ACCOUNT" 2>/dev/null)
        _pk_prof=$(awk -F' = ' '/^PrivateKey = /{print $2}' "$WC_WGCF_PROFILE" 2>/dev/null)
        if [ -n "$_pk_acc" ] && [ "$_pk_acc" = "$_pk_prof" ]; then
            chmod 600 "$WC_WGCF_PROFILE"
            wc_log "профиль уже есть и совпадает с аккаунтом — wgcf generate не нужен"
            return 0
        fi
    fi
    rm -f "$WC_WGCF_PROFILE"
    ( umask 077; cd "$(dirname "$WC_WGCF_PROFILE")" && "$WC_WGCF_BIN" generate 2>/dev/null )
    local rc=$?
    [ -f "$WC_WGCF_PROFILE" ] && [ $rc -eq 0 ] || { wc_log "wgcf generate failed rc=$rc"; return 1; }
    chmod 600 "$WC_WGCF_PROFILE"
}

wc_resolve_endpoint() {
    local ep; ep=$(getent ahostsv4 engage.cloudflareclient.com 2>/dev/null | awk 'NR==1{print $1}')
    [ -z "$ep" ] && { wc_log "cannot resolve engage.cloudflareclient.com"; return 1; }
    echo "$ep"
}

# Build a server-safe warp.conf: Table=off (no routes → SSH safe), MTU 1280,
# pinned IPv4 endpoint, keepalive. Bind explicitly to route (see SKILL.md).
# shellcheck disable=SC2120  # both args optional; orchestrator uses defaults
wc_build_warp_conf() {
    local out="${1:-$WC_WARP_CONF}" endpoint_ip="${2:-}"
    [ -f "$WC_WGCF_PROFILE" ] || { wc_log "no profile — run wc_generate_profile"; return 1; }
    [ -z "$endpoint_ip" ] && { endpoint_ip=$(wc_resolve_endpoint) || return 1; }

    local pk pub addr
    pk=$(awk -F' = '  '/^PrivateKey = /{print $2}' "$WC_WGCF_PROFILE")
    pub=$(awk -F' = ' '/^PublicKey = /{print $2}'  "$WC_WGCF_PROFILE")
    addr=$(awk -F' = ' '/^Address = /{print $2}'   "$WC_WGCF_PROFILE" | cut -d',' -f1)
    [ -n "$pk" ] && [ -n "$pub" ] && [ -n "$addr" ] || { wc_log "profile parse failed"; return 1; }

    ( umask 077; mkdir -p "$(dirname "$out")"; chmod 700 "$(dirname "$out")" 2>/dev/null
      cat > "$out" <<WARPEOF
[Interface]
PrivateKey = ${pk}
Address = ${addr}
MTU = 1280
Table = off

[Peer]
PublicKey = ${pub}
AllowedIPs = 0.0.0.0/0
Endpoint = ${endpoint_ip}:2408
PersistentKeepalive = 25
WARPEOF
    )
    chmod 600 "$out"
    wc_log "warp.conf written to $out (endpoint $endpoint_ip)"
}

# Bring up/down. Interface name is the conf basename (wg-quick convention).
# shellcheck disable=SC2120  # optional arg; orchestrator uses $WC_WARP_CONF
wc_up() {
    local conf="${1:-$WC_WARP_CONF}" iface
    iface=$(basename "$conf" .conf)
    wg-quick down "$conf" >/dev/null 2>&1 || true
    if wg-quick up "$conf" >/dev/null 2>&1 && ip addr show "$iface" >/dev/null 2>&1; then
        wc_log "interface $iface is up"
        return 0
    fi
    # AppArmor (Ubuntu 24.04+, штатный профиль wg-quick) разрешает читать конфиг
    # ТОЛЬКО из /etc/wireguard: наш путь в /opt даёт «Permission denied» даже
    # под root. Изоляцию от чужого WARP держит имя интерфейса, а не каталог, —
    # поэтому кладём копию в /etc/wireguard и поднимаем по имени.
    mkdir -p /etc/wireguard 2>/dev/null; chmod 700 /etc/wireguard 2>/dev/null
    if [ -f "/etc/wireguard/${iface}.conf" ] && ! cmp -s "$conf" "/etc/wireguard/${iface}.conf"; then
        cp -a "/etc/wireguard/${iface}.conf" "/etc/wireguard/${iface}.conf.gt-bak.$(date +%s)" 2>/dev/null || true
    fi
    ( umask 077; cp "$conf" "/etc/wireguard/${iface}.conf" ) || { wc_log "wg-quick up failed (копия в /etc/wireguard не удалась)"; return 1; }
    chmod 600 "/etc/wireguard/${iface}.conf"
    wg-quick down "$iface" >/dev/null 2>&1 || true
    wg-quick up "$iface" >/dev/null 2>&1 || { wc_log "wg-quick up failed"; return 1; }
    ip addr show "$iface" >/dev/null 2>&1 || { wc_log "interface $iface did not appear"; return 1; }
    wc_log "interface $iface is up (через /etc/wireguard — ограничение AppArmor)"
}
wc_down() {
    local conf="${1:-$WC_WARP_CONF}" iface
    iface=$(basename "$conf" .conf)
    wg-quick down "$conf" >/dev/null 2>&1 || true
    # Интерфейс мог быть поднят по имени (обход AppArmor) — снимаем и так.
    ip link show "$iface" >/dev/null 2>&1 && wg-quick down "$iface" >/dev/null 2>&1
    ip link show "$iface" >/dev/null 2>&1 && ip link delete dev "$iface" >/dev/null 2>&1
    wc_log "interface $iface is down"
}
wc_egress_ip_iface() {
    curl -s4 --max-time 5 --interface "${1:-warp}" ifconfig.me 2>/dev/null || echo "N/A"
}

# Persist a host-path-B tunnel across reboots via wg-quick@ systemd unit
wc_persist_iface() {
    local conf="${1:-$WC_WARP_CONF}" iface
    iface=$(basename "$conf" .conf)
    command -v systemctl >/dev/null 2>&1 || { wc_log "no systemd — cannot persist"; return 1; }
    mkdir -p /etc/wireguard
    if [ "$conf" != "/etc/wireguard/${iface}.conf" ]; then
        if [ -f "/etc/wireguard/${iface}.conf" ] && ! cmp -s "$conf" "/etc/wireguard/${iface}.conf"; then
            cp -a "/etc/wireguard/${iface}.conf" "/etc/wireguard/${iface}.conf.gt-bak.$(date +%s)" 2>/dev/null || true
        fi
        cp "$conf" "/etc/wireguard/${iface}.conf"; chmod 600 "/etc/wireguard/${iface}.conf"
    fi
    systemctl enable "wg-quick@${iface}" >/dev/null 2>&1 && wc_log "enabled wg-quick@${iface} (survives reboot)"
}

# Full teardown of path B (strip license, down, remove files)
wc_teardown_iface() {
    wc_down "$WC_WARP_CONF"
    wc_reset_license_wgcf
    rm -f "$WC_WGCF_PROFILE"
    wc_log "path-B torn down (iface down, license stripped, profile removed)"
    wc_log "NOTE: wgcf-account.toml kept — delete it to abandon the device slot"
}

# ═══════════════════════════════════════════════════════════════════
#  One-shot orchestrators (safe under set -euo pipefail)
# ═══════════════════════════════════════════════════════════════════

# Path A end-to-end. $1 = "" | "free" | WARP+ key. Echoes tier on stdout.
_wc_setup_socks_gateway() {
    local key="${1:-}"
    wc_install_warp_cli   || return 1
    wc_register_3xui      || return 1
    if [ -n "$key" ] && [ "$key" != "free" ]; then
        wc_apply_plus_3xui "$key" || wc_log "Plus apply failed ($(wc_last_reason)) — continuing as Free"
    fi
    wc_connect_3xui "$WC_SOCKS_PORT" || return 1
    local tier=""; tier=$(wc_verify_socks "$WC_SOCKS_PORT") || tier=""
    wc_log "tier=${tier:-unknown} egress=$(wc_egress_ip_socks "$WC_SOCKS_PORT")"
    echo "$tier"
}
wc_setup_socks_gateway() { wc_with_lock socks _wc_setup_socks_gateway "$@"; }

# Path B end-to-end on the HOST. $1 = "" | "free" | WARP+ key. Echoes tier.
_wc_setup_warp_iface() {
    local key="${1:-}"
    wc_require_root  || return 1
    wc_install_wgcf  || return 1
    wc_register_wgcf || return 1
    if [ -n "$key" ] && [ "$key" != "free" ]; then
        wc_apply_plus_wgcf "$key" || wc_log "Plus apply failed ($(wc_last_reason)) — continuing as Free"
    fi
    wc_generate_profile || return 1
    wc_build_warp_conf  || return 1
    wc_up               || return 1
    # gotelegram-patch: имя интерфейса задаёт конфиг (у нас gtwarp) — по захардкоженному «warp» curl бьётся в несуществующий iface и тариф всегда пустой
    local _if; _if=$(basename "$WC_WARP_CONF" .conf)
    local tier=""; tier=$(wc_verify_iface "$_if") || tier=""
    wc_log "tier=${tier:-unknown}"
    echo "$tier"
}
wc_setup_warp_iface() { wc_with_lock iface _wc_setup_warp_iface "$@"; }
