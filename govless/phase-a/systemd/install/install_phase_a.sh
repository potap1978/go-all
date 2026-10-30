#!/usr/bin/env bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# install_phase_a.sh — master installer for goVLESS Phase A.
#   1. Creates govless user + dirs + bot.env (install_govless_user.sh).
#   2. Installs cloudflared if Lite mode (install_cloudflared.sh, non-fatal).
#   3. Installs systemd units + helper scripts to /etc/systemd/system/ and /usr/local/bin/.
#   4. Optionally wires the nginx snippet into the Pro site config.
#   5. daemon-reload + enables base units (govlessctl, govless-bot, audit-prune).
#   6. In Lite mode also enables cloudflared chain + webapp-frontend.
# Idempotent. Does NOT start govless-bot (needs human to set BOT_TOKEN first).

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "install_phase_a.sh: must run as root" >&2
    exit 1
fi

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_SRC="$(cd "${SELF_DIR}/.." && pwd)"
PHASE_A_SRC="$(cd "${SYSTEMD_SRC}/.." && pwd)"
BIN_SRC="${SYSTEMD_SRC}/bin"
NGINX_SRC="${SYSTEMD_SRC}/nginx"

CONFIG_FILE="${GOVLESS_CONFIG:-/opt/govless/config.json}"
MODE="lite"  # default; overridden from config.json if present.

# --- detect mode ---
if [ -r "$CONFIG_FILE" ]; then
    # Try python first (pretty-prints any JSON); fall back to grep.
    if command -v python3 >/dev/null 2>&1; then
        DETECTED_MODE="$(python3 -c "import json,sys; d=json.load(open('${CONFIG_FILE}')); print(d.get('mode','lite'))" 2>/dev/null || true)"
    fi
    if [ -z "${DETECTED_MODE:-}" ]; then
        DETECTED_MODE="$(grep -oE '"mode"[[:space:]]*:[[:space:]]*"[a-z]+"' "$CONFIG_FILE" 2>/dev/null | head -n1 | sed -E 's/.*"([a-z]+)"$/\1/' || true)"
    fi
    case "${DETECTED_MODE:-}" in
        pro|lite) MODE="$DETECTED_MODE" ;;
        *) echo "install_phase_a.sh: could not detect mode from ${CONFIG_FILE}, defaulting to lite" >&2 ;;
    esac
fi
echo "install_phase_a.sh: MODE=${MODE}"

# --- step 1: user/dirs/bot.env ---
bash "${SELF_DIR}/install_govless_user.sh"

# --- step 1b: deploy Python control-plane, bot, and WebApp assets ---
# Wait out any boot-time apt lock (unattended-upgrades on a fresh boot) instead of
# failing silently — otherwise the venv packages don't install and venv creation
# aborts later with a confusing "ensurepip is not available" error.
# QA 1.4.0 (fresh Ubuntu 24.04): unattended-upgrades held the lock for 20+ min,
# the 180 s apt timeout expired quietly and the installer died with
# "FATAL: cannot create venv". Mirror lib/common.sh apt_lock_wait: wait up to
# 15 min with visible progress, same as the main installer does since 1.3.6.
_apt_lock_wait() {
    local timeout="${1:-900}" elapsed=0 holder=""
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "install_phase_a.sh: WARN: apt lock still held after ${timeout}s — package install may fail" >&2
            return 1
        fi
        if [ "$elapsed" -eq 0 ]; then
            holder=$(fuser -v /var/lib/dpkg/lock-frontend 2>&1 | awk 'NR>1{print $NF}' | grep -v '^$' | head -1)
            echo "install_phase_a.sh: waiting for apt lock (held by ${holder:-apt}, usually unattended-upgrades on a fresh server)…"
        elif [ $((elapsed % 60)) -eq 0 ]; then
            echo "install_phase_a.sh: still waiting for apt lock (${elapsed}s / ${timeout}s)…"
        fi
        sleep 5; elapsed=$((elapsed + 5))
    done
    return 0
}
_apt_lock_wait || true
apt-get -o DPkg::Lock::Timeout=300 update -qq >/dev/null 2>&1 || true
apt-get -o DPkg::Lock::Timeout=300 install -y -qq python3-venv python3-pip >/dev/null 2>&1 || \
    echo "install_phase_a.sh: WARN: apt-get install python3-venv python3-pip failed (see apt lock / repo state)" >&2

install -d -m 0755 /opt/govless
install -d -m 0755 /opt/govless/bin

rm -rf /opt/govless/govlessctl /opt/govless/bot /opt/govless/webapp
cp -R "${PHASE_A_SRC}/govlessctl" /opt/govless/govlessctl
cp -R "${PHASE_A_SRC}/bot" /opt/govless/bot
cp -R "${PHASE_A_SRC}/webapp" /opt/govless/webapp
find /opt/govless/govlessctl /opt/govless/bot -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find /opt/govless/govlessctl /opt/govless/bot /opt/govless/webapp -type d -exec chmod 0755 {} +
find /opt/govless/govlessctl /opt/govless/bot /opt/govless/webapp -type f -exec chmod 0644 {} +
chown -R root:govless /opt/govless/govlessctl /opt/govless/bot /opt/govless/webapp

# --- step 1d: cover-site templates catalog for the bot / Mini App picker ---
# The daemon reads /opt/govless/templates_catalog.json; keep it equal to the
# installer's copy (the SSH menu syncs it too, but the bot may run first).
CATALOG_SRC="$(cd "${PHASE_A_SRC}/.." && pwd)/templates_catalog.json"
if [ -f "$CATALOG_SRC" ] && ! cmp -s "$CATALOG_SRC" /opt/govless/templates_catalog.json 2>/dev/null; then
    install -m 0644 "$CATALOG_SRC" /opt/govless/templates_catalog.json
    echo "install_phase_a.sh: templates catalog synced to /opt/govless/templates_catalog.json"
fi

# Pick the best Python to build the control-plane venv: prefer an already-installed
# interpreter >= 3.9; otherwise fall back to the system python3 — the code stays 3.8-safe,
# so Ubuntu 20.04 is supported. We NEVER replace/upgrade the system python3 itself (apt/OS
# depend on it); we only choose which interpreter builds our isolated venv.
GOVLESS_PYBIN=""
for _py in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    command -v "$_py" >/dev/null 2>&1 || continue
    _v="$("$_py" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)"
    if [ "${_v:-0}" -ge 309 ]; then GOVLESS_PYBIN="$_py"; break; fi
done
[ -n "$GOVLESS_PYBIN" ] || GOVLESS_PYBIN="python3"
echo "install_phase_a.sh: venv python = ${GOVLESS_PYBIN} ($(${GOVLESS_PYBIN} -V 2>&1))"
# Make sure the chosen interpreter can actually build a venv (ensurepip present).
# On fresh Ubuntu the version-specific pythonX.Y-venv can be missing even when
# python3-venv "installed", so install it explicitly for the picked interpreter.
_pyver_tag="$("$GOVLESS_PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 3)"
if ! "$GOVLESS_PYBIN" -c 'import ensurepip' >/dev/null 2>&1; then
    _apt_lock_wait || true
    apt-get -o DPkg::Lock::Timeout=300 install -y -qq "python${_pyver_tag}-venv" >/dev/null 2>&1 || \
        echo "install_phase_a.sh: WARN: apt-get install python${_pyver_tag}-venv failed" >&2
fi
if [ ! -x /opt/govless/venv/bin/python ]; then
    if ! "$GOVLESS_PYBIN" -m venv /opt/govless/venv 2>/dev/null; then
        # Retry once after force-installing the versioned venv package; fail loud if still broken.
        rm -rf /opt/govless/venv
        _apt_lock_wait || true
        apt-get -o DPkg::Lock::Timeout=300 install -y -qq "python${_pyver_tag}-venv" python3-venv >/dev/null 2>&1 || true
        "$GOVLESS_PYBIN" -m venv /opt/govless/venv || { echo "install_phase_a.sh: FATAL: cannot create venv — install python${_pyver_tag}-venv and re-run" >&2; exit 1; }
    fi
fi
/opt/govless/venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
/opt/govless/venv/bin/python -m pip install -q \
    -r "${PHASE_A_SRC}/govlessctl/requirements.txt" \
    -r "${PHASE_A_SRC}/bot/requirements.txt"
chmod -R a+rX /opt/govless/venv

# --- step 1c: Python 3.8 / old-Ubuntu (20.04) compatibility guard (runs on every update) ---
# The control-plane targets Python 3.8+. If this box's venv runs an interpreter older than
# 3.9, defensively rewrite any 3.9-only construct in the DEPLOYED code to a 3.8-safe form so
# the daemon can't 500 with "Server got itself in trouble". Idempotent: a no-op on fixed source.
_venv_py="/opt/govless/venv/bin/python"
[ -x "$_venv_py" ] || _venv_py="/usr/bin/python3"
_pyver="$("$_venv_py" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null || echo 0)"
if [ "${_pyver:-0}" -lt 309 ]; then
    echo "install_phase_a.sh: Python <3.9 detected (v${_pyver}, e.g. Ubuntu 20.04) — applying 3.8 compat hotfix"
    # asyncio.to_thread(fn) -> asyncio.get_running_loop().run_in_executor(None, fn)  [3.9 -> 3.7+]
    for _d in /opt/govless/govlessctl /opt/govless/bot /opt/govless/webapp; do
        [ -d "$_d" ] || continue
        { grep -rlE 'asyncio\.to_thread\(' "$_d" 2>/dev/null || true; } | while IFS= read -r _f; do
            sed -i -E 's/asyncio\.to_thread\(([A-Za-z_][A-Za-z0-9_]*)\)/asyncio.get_running_loop().run_in_executor(None, \1)/g' "$_f"
            echo "install_phase_a.sh:   patched $_f"
        done
    done
else
    echo "install_phase_a.sh: Python OK (v${_pyver}) — no 3.8 compat patch needed"
fi

cat >/opt/govless/bin/govlessctl-daemon <<'EOF_DAEMON'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/govless
PY=/opt/govless/venv/bin/python
[ -x "$PY" ] || PY=/usr/bin/python3
exec "$PY" -m govlessctl.daemon "$@"
EOF_DAEMON
cat >/usr/local/bin/govlessctl <<'EOF_CLI'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/govless
PY=/opt/govless/venv/bin/python
[ -x "$PY" ] || PY=/usr/bin/python3
exec "$PY" -m govlessctl.cli "$@"
EOF_CLI
chmod 0755 /opt/govless/bin/govlessctl-daemon /usr/local/bin/govlessctl
chown root:root /opt/govless/bin/govlessctl-daemon /usr/local/bin/govlessctl

# --- step 2: cloudflared (Lite only) ---
if [ "$MODE" = "lite" ]; then
    bash "${SELF_DIR}/install_cloudflared.sh" || true
fi

# --- step 3: copy scripts to /usr/local/bin ---
install -d -m 0755 /usr/local/bin
for s in govless-bot govless-tunnel-url-extract govless-tunnel-health-check \
         govless-tunnel-escalation-check govless-audit-prune; do
    if [ -f "${BIN_SRC}/${s}" ]; then
        install -m 0755 "${BIN_SRC}/${s}" "/usr/local/bin/${s}"
    else
        echo "install_phase_a.sh: WARN: ${BIN_SRC}/${s} missing" >&2
    fi
done

# --- step 4: copy systemd units ---
UNIT_DST=/etc/systemd/system
install -d -m 0755 "$UNIT_DST"

BASE_UNITS=(
    govlessctl.service
    govless-bot.service
    govless-audit-prune.service
    govless-audit-prune.timer
)
LITE_UNITS=(
    cloudflared-quick.service
    cloudflared-url.path
    cloudflared-url.service
    tunnel-health.service
    tunnel-health.timer
    webapp-frontend.service
)

for u in "${BASE_UNITS[@]}" "${LITE_UNITS[@]}"; do
    if [ -f "${SYSTEMD_SRC}/${u}" ]; then
        install -m 0644 "${SYSTEMD_SRC}/${u}" "${UNIT_DST}/${u}"
    else
        echo "install_phase_a.sh: WARN: unit ${u} missing in ${SYSTEMD_SRC}" >&2
    fi
done

# --- step 5: nginx snippet (Pro only) ---
if [ "$MODE" = "pro" ] && command -v nginx >/dev/null 2>&1; then
    SNIP_DST=/etc/nginx/snippets
    install -d -m 0755 "$SNIP_DST"
    install -m 0644 "${NGINX_SRC}/govless-webapp.conf" "${SNIP_DST}/govless-webapp.conf"

    # Try to wire the include into the existing Pro site, if not present already.
    if [ -n "${GOVLESS_NGINX_SITE:-}" ]; then
        SITE="$GOVLESS_NGINX_SITE"
    else
        SITE=""
        for candidate in \
            /etc/nginx/sites-enabled/govless \
            /etc/nginx/sites-available/govless \
            /etc/nginx/sites-enabled/govless-pro.conf \
            /etc/nginx/sites-available/govless-pro.conf; do
            if [ -f "$candidate" ]; then
                SITE="$candidate"
                break
            fi
        done
    fi

    if [ -n "${SITE:-}" ] && [ -f "$SITE" ] && ! grep -q 'govless-webapp.conf' "$SITE"; then
        # Pro usually has Xray on :443 with fallback to nginx :80. If a classic
        # TLS server exists, prefer it; otherwise insert into the first server.
        webapp_insert_mode="first-server"
        if grep -qE 'listen[[:space:]]+(\[::\]:)?443[[:space:]]+ssl' "$SITE"; then
            webapp_insert_mode="tls-server"
        fi
        if awk -v mode="$webapp_insert_mode" '
            BEGIN { inserted=0; in_target=0 }
            /^[[:space:]]*server[[:space:]]*\{/ {
                if (mode == "first-server" && !inserted) in_target=1
            }
            mode == "tls-server" && /listen[[:space:]]+(\[::\]:)?443[[:space:]]+ssl/ {
                in_target=1
            }
            in_target && /^}/ && !inserted {
                print "    include /etc/nginx/snippets/govless-webapp.conf;"
                inserted=1
                in_target=0
            }
            { print }
            END { if (!inserted) exit 9 }
        ' "$SITE" >"${SITE}.govless.tmp"; then
            mv "${SITE}.govless.tmp" "$SITE"
            echo "install_phase_a.sh: wired govless-webapp.conf into ${SITE}"
        else
            rm -f "${SITE}.govless.tmp"
            echo "install_phase_a.sh: WARN: could not wire govless-webapp.conf into ${SITE}" >&2
        fi
    elif [ -z "${SITE:-}" ]; then
        echo "install_phase_a.sh: WARN: goVLESS nginx site not found; WebApp not wired" >&2
    fi

    if nginx -t >/dev/null 2>&1; then
        systemctl restart nginx >/dev/null 2>&1 || systemctl reload nginx >/dev/null 2>&1 || true
    else
        echo "install_phase_a.sh: WARN: nginx -t failed; review ${SITE}" >&2
    fi
fi

# --- step 6: daemon-reload + enable ---
systemctl daemon-reload

# Base units: enable (do NOT start govless-bot — needs BOT_TOKEN).
systemctl enable govlessctl.service >/dev/null 2>&1 || true
systemctl enable govless-bot.service >/dev/null 2>&1 || true
systemctl enable govless-audit-prune.timer >/dev/null 2>&1 || true

# Redeployed code must actually RUN. `systemctl start` is a no-op on an active
# unit, so an update (maybe_sync_version → this installer) used to leave the
# daemon and the bot on the OLD code until a manual restart (QA 1.4.0, BUG-02:
# new site.template_* RPC methods and the bot's «Cover site» button were missing
# after 1.3.6 → 1.4.0). try-restart touches only units that are running — the bot
# stays stopped until BOT_TOKEN is set, as before.
for _u in govlessctl.service govless-bot.service webapp-frontend.service; do
    if systemctl is-active --quiet "$_u" 2>/dev/null; then
        systemctl try-restart "$_u" >/dev/null 2>&1 \
            && echo "install_phase_a.sh: restarted $_u (new code)" \
            || echo "install_phase_a.sh: WARN: could not restart $_u" >&2
    fi
done

# Start what we can start safely now.
systemctl start govlessctl.service >/dev/null 2>&1 || \
    echo "install_phase_a.sh: WARN: govlessctl.service did not start (check /opt/govless/bin/govlessctl-daemon)" >&2
systemctl start govless-audit-prune.timer >/dev/null 2>&1 || true

if [ "$MODE" = "lite" ]; then
    systemctl enable webapp-frontend.service >/dev/null 2>&1 || true
    systemctl enable cloudflared-quick.service >/dev/null 2>&1 || true
    systemctl enable cloudflared-url.path >/dev/null 2>&1 || true
    systemctl enable tunnel-health.timer >/dev/null 2>&1 || true

    systemctl start webapp-frontend.service >/dev/null 2>&1 || true
    if [ -x /usr/local/bin/cloudflared ] || [ -x /usr/bin/cloudflared ]; then
        systemctl start cloudflared-url.path >/dev/null 2>&1 || true
        systemctl restart cloudflared-quick.service >/dev/null 2>&1 || \
            systemctl start cloudflared-quick.service >/dev/null 2>&1 || true
        # cloudflared can print the quick-tunnel URL before the .path unit is
        # active; run the extractor once so a fresh install/deploy does not wait
        # for another log append before Telegram receives a WebApp URL.
        systemctl start cloudflared-url.service >/dev/null 2>&1 || true
        systemctl start tunnel-health.timer >/dev/null 2>&1 || true
    else
        echo "install_phase_a.sh: NOTE: cloudflared not present; tunnel stack enabled but not started." >&2
    fi
fi

# Friendly reminder about BOT_TOKEN.
if grep -qE '^BOT_TOKEN=[[:space:]]*$' /etc/govless/bot.env 2>/dev/null; then
    cat <<EOF

────────────────────────────────────────────────────────────────────
goVLESS Phase A installed (mode=${MODE}).

NEXT STEP: edit /etc/govless/bot.env, set BOT_TOKEN=<value from @BotFather>,
then start the bot:

    systemctl restart govlessctl.service
    systemctl start govless-bot.service
    systemctl status govless-bot.service

Use /admin <ADMIN_CLAIM_TOKEN> in the bot chat to claim the first admin seat.
────────────────────────────────────────────────────────────────────
EOF
fi

exit 0
