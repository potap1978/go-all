# 014 — Architect proposal: Telegram bot + shared action layer + WebApp

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**Refs:** 007-codex-to-claude-product-note-telegram-bot-menu.md,
          012-reviewer-final-audit.md,
          013-architect-codex-audit-response.md
**Status:** proposal — awaiting Codex audit before implementation

## Summary

This proposal expands Codex's 007 product note into a concrete
architecture for goVLESS Telegram bot + shared action layer + an
explicitly-designed WebApp hosting strategy (Pro vs Lite). Eight
architectural rules bind future implementation. Implementation comes
in Phases A→D, each as separate commit after Codex audit.

## Architectural rules (binding)

### Rule 1 — 3X-UI sqlite is the single source of truth

`govlessctl` is a thin facade over:
- `/etc/x-ui/x-ui.db` (clients, inbounds, traffic, online state, settings)
- `/opt/govless/config.json` (mode, domain, server IP, transport)
- `/opt/govless/state.db` → tables `govless_user_bindings` (TG id ↔ x-ui UUID),
  `govless_audit` (write log), `govless_capabilities` (probe cache).

Already implemented by P1 #1 fix (commit `056b160`). No replication,
no sync, no cache invalidation. Every read regenerates from sqlite.

### Rule 2 — WebApp: Pro = nginx-on-domain, Lite = Quick Tunnel + auto-recovery

| Mode | WebApp delivery | URL | Stability |
| --- | --- | --- | --- |
| **Pro** (domain + LE cert) | `nginx` reverse-proxy to govlessctl UNIX socket at `/app/` on user's domain | `https://<domain>/app/` | Stable, no tunnel |
| **Lite** (IP only) | Cloudflare **Quick Tunnel** with **auto-recovery watchdog** | `https://<random>.trycloudflare.com` | Self-healing; URL rotates on each cloudflared restart |
| **Lite — advanced opt-in** | Cloudflare **Named Tunnel** (free, requires CF account) | `https://<user-chosen>.example.com` | Stable, documented separately |

Inline-button Telegram menu always works in both modes — WebApp is enhancement.

#### Quick Tunnel auto-recovery design (Lite mode)

Per human's 7-step scheme:

**1. cloudflared as a managed systemd unit**

`/etc/systemd/system/cloudflared-quick.service`:
```ini
[Unit]
Description=goVLESS Cloudflare Quick Tunnel
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate \
    --metrics 127.0.0.1:18181 \
    --url http://127.0.0.1:8443
Restart=always
RestartSec=10
StandardOutput=append:/var/log/cloudflared.log
StandardError=append:/var/log/cloudflared.log
User=govless
Group=govless

[Install]
WantedBy=multi-user.target
```

`Restart=always RestartSec=10` ensures cloudflared respawns; each
respawn gets a new `trycloudflare.com` subdomain.

**2. URL parser (systemd path-unit + tiny script)**

`/etc/systemd/system/cloudflared-url.path`:
```ini
[Path]
PathModified=/var/log/cloudflared.log
Unit=cloudflared-url.service

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/cloudflared-url.service`:
```ini
[Service]
Type=oneshot
ExecStart=/usr/local/bin/govless-tunnel-url-extract
```

`/usr/local/bin/govless-tunnel-url-extract` (bash):
```bash
#!/bin/bash
# Parses cloudflared log for the latest trycloudflare URL and writes it
# atomically to /run/govless/tunnel.url. Triggers bot URL-update hook.
set -u
LOG=/var/log/cloudflared.log
OUT=/run/govless/tunnel.url
mkdir -p /run/govless
URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" \
      | tail -1)
[ -z "$URL" ] && exit 0
# Only write if changed
CURRENT=$(cat "$OUT" 2>/dev/null || echo "")
if [ "$CURRENT" = "$URL" ]; then exit 0; fi
TMP=$(mktemp /run/govless/tunnel.url.XXXXXX)
echo "$URL" > "$TMP"
mv -f "$TMP" "$OUT"
chmod 644 "$OUT"
# Trigger bot URL-update hook (best-effort)
systemctl reload-or-restart govless-bot.service 2>/dev/null || true
logger -t govless-tunnel "new URL: $URL"
```

**3. Health watchdog (systemd timer)**

`/etc/systemd/system/tunnel-health.timer`:
```ini
[Timer]
OnBootSec=60
OnUnitActiveSec=60
Unit=tunnel-health.service

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/tunnel-health.service`:
```ini
[Service]
Type=oneshot
ExecStart=/usr/local/bin/govless-tunnel-health-check
```

`/usr/local/bin/govless-tunnel-health-check` (hardened per Codex 016 P2 #6):
```bash
#!/bin/bash
set -u
URL_FILE=/run/govless/tunnel.url
FAIL_FILE=/run/govless/tunnel.fail_count
START_FILE=/run/govless/tunnel.started_at
LOG=/var/log/govless-tunnel-health.log
exec >>"$LOG" 2>&1
date -u

# Cold-start grace: cloudflared respawns take ~10s + LE/edge.
# Skip health checks for the first 30s after URL appears.
if [ -s "$URL_FILE" ] && [ ! -s "$START_FILE" ]; then
    date +%s > "$START_FILE"
fi
if [ -s "$START_FILE" ]; then
    SINCE=$(( $(date +%s) - $(cat "$START_FILE") ))
    [ "$SINCE" -lt 30 ] && { echo "warmup ${SINCE}s"; exit 0; }
fi

[ ! -s "$URL_FILE" ] && { echo "no URL yet"; exit 0; }
URL=$(cat "$URL_FILE")

# Primary probe: local backend (NOT via tunnel). Defeats hostile
# public /healthz hammering — internet probers can't flip restart.
if ! curl -fsS --max-time 5 'http://127.0.0.1:8443/healthz' >/dev/null 2>&1; then
    echo "backend DOWN — not a tunnel issue, leaving cloudflared alone"
    exit 0
fi

# Backend OK; probe the tunnel as secondary. --resolve pins edge IP
# so hostile probing of public URL can't degrade our check.
HOST=$(echo "$URL" | sed -E 's,^https?://,,;s,/.*,,')
EDGE_IP=$(getent hosts "$HOST" | awk '{print $1; exit}')
if [ -n "$EDGE_IP" ] && \
   curl -fsS --max-time 5 --resolve "$HOST:443:$EDGE_IP" \
        "${URL}/healthz" >/dev/null 2>&1; then
    echo "OK $URL"; echo 0 > "$FAIL_FILE"; exit 0
fi

FAIL=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
FAIL=$((FAIL + 1)); echo "$FAIL" > "$FAIL_FILE"
echo "FAIL $FAIL/3 $URL"

if [ "$FAIL" -ge 3 ]; then
    echo "restarting cloudflared-quick"
    systemctl restart cloudflared-quick.service
    echo 0 > "$FAIL_FILE"
    rm -f "$START_FILE"  # reset warmup grace for new instance
    /usr/local/bin/govless-tunnel-escalation-check
fi
```

Three layers of hysteresis (Codex 016 P2 #6):
1. **Cold-start grace 30s** after a new URL — cloudflared boot +
   Cloudflare edge propagation lands inside this window. Without it,
   every restart immediately bumps FAIL.
2. **Backend-first probe** at `127.0.0.1:8443/healthz`: if the
   WebApp itself is down, restarting cloudflared doesn't help. The
   WebApp's Python ASGI must serve `/healthz` directly.
3. **Hostile-probing resistance**: `--resolve` pins the actual
   Cloudflare edge IP so we measure our tunnel route, not internet
   probing volume.

Plus `cloudflared-quick.service` gains:
```ini
StartLimitIntervalSec=300
StartLimitBurst=6
```
so >6 restarts in 5min trips systemd's `failed` state and
`govless-tunnel-escalation-check` fires the Named-Tunnel alert.

**4. Bot integration**

- Bot polls `mtime` of `/run/govless/tunnel.url` every 30s (or uses
  inotify if available).
- On change:
  - Read new URL into in-memory state.
  - For every admin TG id, call `bot.setChatMenuButton(menu_button=
    MenuButtonWebApp(text="Open goVLESS app", web_app=WebAppInfo(url=NEW_URL)))`.
  - Send TG message: `⚠️ Quick Tunnel перезапущен. Новый URL: NEW_URL.
    Bookmarks обнови, если сохранил локально.`
- Every "Open WebApp" inline button is rendered with current URL at
  send time (not pre-cached) — so even without restart-poll, next user
  interaction sees the right URL.

**5. WebApp session continuity**

- WebApp state lives in browser `localStorage` keyed by `tg_user_id`,
  not by URL. URL change ⇒ same user, same data.
- All API calls include fresh `Telegram-initData` from `WebApp.initData`
  — server verifies HMAC server-side before reaching govlessctl, so
  expired URLs can't be used for replay.

**6. Fallback on persistent outage**

`govless-tunnel-escalation-check`:
```bash
#!/bin/bash
RECENT_RESTARTS=/run/govless/tunnel.restarts
NOW=$(date +%s)
echo "$NOW" >> "$RECENT_RESTARTS"
# Keep only last 5 min worth
awk -v cutoff=$((NOW - 300)) '$1 >= cutoff' "$RECENT_RESTARTS" > "${RECENT_RESTARTS}.tmp"
mv "${RECENT_RESTARTS}.tmp" "$RECENT_RESTARTS"
COUNT=$(wc -l < "$RECENT_RESTARTS")
if [ "$COUNT" -ge 3 ]; then
    govlessctl notify-admins \
        --severity critical \
        --message "Quick Tunnel недоступен — 3 рестарта за 5 минут. Проверь /var/log/cloudflared.log. Рекомендация: переключись на Named Tunnel (см. docs/lite-webapp-via-named-tunnel.md)."
fi
```

**7. Documentation**

`docs/webapp-hosting.md` (new):
- **Pro:** "WebApp на твоём домене через nginx. Стабильно, без тоннелей."
- **Lite default (Quick Tunnel):** "Это **dev/staging tier**. Для прода рекомендуем Named Tunnel или Pro mode (свой домен). Auto-recovery даёт best-effort uptime — типичная пропускная способность Quick Tunnel ограничена, no SLA."
- **Lite advanced (Named Tunnel):** пошаговая инструкция (`cloudflared tunnel create`, route DNS, install service unit). Указать что это требует CF account и DNS-управление.

### Rule 3 — Explicit admin claim with one-shot token + atomic lock

NOT "first `/start` = admin". Two concurrent `/admin <token>` calls
MUST resolve to **at most one** successful claim — Codex 016 P1 #4.

**Atomic claim primitive (binding):** `/admin <token>` handler MUST
perform the check-and-claim inside a single SQLite IMMEDIATE
transaction against `state.db`:

```python
with db.cursor() as cur:
    cur.execute("BEGIN IMMEDIATE")        # writer-lock acquired or fail
    row = cur.execute(
        "SELECT value FROM govless_meta WHERE key='admin_claimed'"
    ).fetchone()
    if row and row[0] == '1':
        cur.execute("ROLLBACK")
        return reply("Admin already claimed.")
    if submitted_token != current_claim_token():
        cur.execute("ROLLBACK")
        return reply("Invalid token.")
    cur.execute("INSERT OR REPLACE INTO govless_meta(key,value) VALUES('admin_claimed','1')")
    cur.execute("COMMIT")
rotate_claim_token_and_write_admin_ids(tg_id)
```

`BEGIN IMMEDIATE` serializes concurrent writers via SQLite's
file-locking — the second caller's `BEGIN IMMEDIATE` blocks until
the first commits or times out (`busy_timeout=5000`). After the
row is set to `'1'`, the second caller's branch bails.

Belt+braces: the bash-side `bot.env` rewrite (rotating
`ADMIN_CLAIM_TOKEN`, appending to `ADMIN_IDS`) is wrapped in
`flock -n /var/lock/govless-admin-claim.lock` so even out-of-band
edits (manual `sed -i` by another admin SSH session) serialize
against the bot.

- At install, `govless.sh` generates `/etc/govless/bot.env`:
  ```
  BOT_TOKEN=<from human via prompt or env>
  ADMIN_CLAIM_TOKEN=<random 32-hex>
  ADMIN_IDS=
  ```
  Owner `root:govless`, mode `0640` (see Rule 3a — bot user).
- Bot startup: if `ADMIN_IDS=""`, advertise claim via every `/start` response.
- Additional admins added via `/invite <tg_id>` from existing admin
  (idempotent: re-inviting an existing admin no-ops).
- `BOT_TOKEN` never in repo, never in bridge files, never in logs.

### Rule 3a — Dedicated `govless` user, no root bot process

Per Codex 016 P1 #5: bot must NOT run as root, and `bot.env` must
be readable by the bot user without being world-readable.

**Install creates:**
```bash
useradd -r -s /usr/sbin/nologin -d /var/lib/govless -m govless
install -d -m 0750 -o govless -g govless /var/lib/govless /run/govless
install -d -m 0755 -o root -g govless /etc/govless
install -m 0640 -o root -g govless /etc/govless/bot.env
```

**`/etc/systemd/system/govless-bot.service`:**
```ini
[Unit]
Description=goVLESS Telegram bot
After=network-online.target govlessctl.service
Wants=network-online.target
Requires=govlessctl.service

[Service]
Type=simple
User=govless
Group=govless
SupplementaryGroups=
EnvironmentFile=/etc/govless/bot.env
ExecStart=/usr/local/bin/govless-bot
ReadOnlyPaths=/etc/govless
ReadWritePaths=/var/lib/govless /run/govless
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Group `govless` membership grants the bot read on `bot.env` without
write on its own credential file. `govlessctl` socket
(`/run/govlessctl.sock`, owner `root:govless`, mode `0660`) gives
the bot RPC access without daemon-level privilege. ProtectSystem
+ ReadOnlyPaths means a compromised bot cannot tamper with
`/etc/govless` or system files. **TODO:** move `state.db` from
`/opt/govless` to `/var/lib/govless` to match the ReadWritePaths
whitelist (track in Phase A implementation).

### Rule 4 — Capability detection at runtime

Bot probes 3X-UI at startup + hourly (0–30s **random jitter** on
startup per Codex 016 P2 #7 — prevents probe storms when several
services come up simultaneously after a VPS reboot). Per-feature
results have **TTL-asymmetric caching**: success caches 24h;
failure caches only 5min (transient x-ui restart shouldn't hide
features for an hour).
- panel API reachable
- `inbound.protocol` types in DB (vless / vmess / shadowsocks / trojan)
- `streamSettings.network` types (tcp / xhttp / grpc / ws / etc.)
- `streamSettings.security` types (tls / reality / none)
- `xhttpSettings.mode` field present
- `getClientLinks` / `getSubLinks` endpoints (v3-only) — probe with one request

Cached in `state.db` table `govless_capabilities(key, value, refreshed_at)`.
Menu hides unsupported buttons; degraded capability ⇒ `⚠` tooltip.

### Rule 5 — Audit log

`/opt/govless/state.db`:
```sql
CREATE TABLE govless_audit (
    ts        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    admin_tg  TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT,
    before    TEXT,
    after     TEXT
);
CREATE INDEX idx_audit_ts ON govless_audit(ts DESC);
```

Bot exposes `/audit` → last 20 entries. `/audit full` → last 200 with full diff.

Retention TBD (human Q5): keep all rows forever vs rotate (proposed: 10k rows, archive older).

### Rule 6 — Read-after-write consistency

After any write via 3X-UI API:
- Bot re-reads via `regenerate_links_from_db` and shows post-state.
- If x-ui restart was triggered (cert reload), detect API offline,
  show `⏳ 3X-UI restarting; refresh in 10s` with timer.
- Up to 3× retries with 5s backoff before fallback message.

### Rule 7 — Offline resilience

| Subsystem | 3X-UI unreachable | 3X-UI returns 5xx |
| --- | --- | --- |
| Reads | Serve cached state, mark "⚠ stale, last sync HH:MM" | Same |
| Writes | Block. "3X-UI unreachable; try again in 60s" | Retry 3× / 5s backoff |
| Capability probe | Skip refresh, reuse cached | Same |
| Audit log | Always writes (local sqlite) | — |

Bot never crashes on 3X-UI flap. Watchdog systemd unit restarts the
bot if it hangs > 60s.

### Rule 8 — Single core, three renderers

```
                 ┌─────────────────────────┐
                 │  govlessctl (action layer) │
                 │  • CRUD client            │
                 │  • CRUD inbound (limited) │
                 │  • Capability probe       │
                 │  • Audit write            │
                 └────────────┬────────────┘
                              │ JSON-RPC over UNIX socket
                              │ /run/govlessctl.sock (root:govless 0660)
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼─────┐         ┌────▼─────┐          ┌────▼─────┐
   │ SSH menu │         │ TG bot   │          │ WebApp   │
   │ (bash)   │         │ (Python) │          │ (static  │
   │          │         │          │          │ + nginx) │
   └──────────┘         └──────────┘          └──────────┘
```

SSH menu → `govlessctl <method> <args>` CLI.
TG bot → Python JSON-RPC wrapper to socket.
WebApp → nginx is **TLS termination + DDoS prefilter only**; HMAC
of Telegram `initData` is verified inside **govlessctl** middleware
on every RPC call (Codex 016 P1 #6). nginx misconfig cannot bypass
auth.

## 90% menu (mirroring Codex 007)

### Main

```
1. Overview          (status, mode, transport, online users, last 24h traffic)
2. Clients           (list, add, find)
3. VPN / Connection  (start/stop, restart xray, change mode, current cert)
4. Panel access      (URL, login, SSH-only toggle, restart panel)
5. Telegram bot      (admin list, invite, claim token, audit log)
6. System            (logs, version, update goVLESS, uninstall)
```

### Client Card

```
Client: alex
Status: enabled / disabled / expired
Online: yes / no (last seen 12m ago)
Traffic: 4.2 GB used / unlimited
Expiry: never
Inbound: goVLESS Reality TCP (mask=google.com)

[Open in app] [Show QR]
[Subscription URL]  (Pro only, hidden if not configured)
[Disable]  [Set traffic limit]
[Set expiry]  [Reset traffic]
[Rename]  [Delete client]
```

### Out of everyday menu (per 007)

raw inbound JSON editor, import/export inbound, copy clients between
inbounds, routing rules / sniffing / TLS cipher/ALPN / Reality shortIds
/ manual privateKey, geofile updates, arbitrary Xray version install,
DB import/export, LDAP, external traffic-inform URI, depleted-clients
bulk cleanup. All remain in 3X-UI native panel.

### Advanced submenu (`/advanced` opt-in)

- XHTTP sub-mode selector (`stream-up` / `packet-up` / `auto`) — closes 011 P3 #2
- gRPC `mode=single` selector — closes 011 P3 #2
- Reality mask domain change
- Pro template change with content backup
- Force LE cert renewal (`acme.sh --renew --force`)
- Switch panel listen mode (public / SSH-only)
- WebApp delivery mode (Lite: Quick Tunnel / Named Tunnel)
- Reinstall 3X-UI to different generation (with destructive warning)

## SSH-only / localhost panel mode

Toggle in `Panel access` menu:

- **Public** (default): `webListen=""` → 0.0.0.0
- **SSH-only**: `webListen=127.0.0.1`. Bot shows ready-to-copy command:
  ```
  ssh -L 127.0.0.1:<PORT>:127.0.0.1:<PORT> root@<server>
  ```

Implementation:
- `govlessctl panel access set --mode {public,ssh}` writes to x-ui
  sqlite `settings` → restart x-ui.
- Refuses to switch to SSH-only if request came from outside localhost
  (guard via UNIX socket peer-creds check).

## Implementation plan

### Phase A — `govlessctl` action layer (1 commit)

- `lib/govlessctl/` new dir.
- **Lang stack: Python** (Architect's recommendation). Rationale:
  sqlite3 + http + json + asyncio stdlib only, no compile step,
  type-hints for maintainability, mature TG libs. Bash-only would
  bloat past comfort; Go would add compile/CI for marginal binary-size
  win. (Codex may push back — see Open question #1.)
- UNIX socket `/run/govlessctl.sock`, root:govless 0660.
- JSON-RPC methods: `client.list`, `client.add`, `client.delete`,
  `client.update`, `client.qr`, `client.link`, `client.enable`,
  `client.disable`, `client.reset_traffic`, `client.set_limit`,
  `client.set_expiry`, `inbound.list`, `inbound.toggle`,
  `panel.access`, `system.status`, `audit.tail`, `audit.full`,
  `tunnel.url_get`, `notify-admins`.
- CLI: `govlessctl <method> <args> [--format json|text]`.
- `lib/xui_api.sh` callers refactored to call `govlessctl client.*`
  internally — SSH bash menu still works during transition.

### Phase B — Telegram bot (1 commit)

- `lib/bot/` Python with `aiogram` v3 (async, modern).
- systemd unit `govless-bot.service`, `EnvironmentFile=/etc/govless/bot.env`.
- Inline button menu mirroring Phase A actions.
- Capability probe at startup + hourly via apscheduler.
- Audit hooks on every write.
- Rules 3 (admin claim), 6 (read-after-write), 7 (offline resilience).
- Tunnel URL polling via inotify on `/run/govless/tunnel.url`.

### Phase C — WebApp (Pro-only first, 1 commit)

- Static SPA hosted from `/opt/govless/webapp/` (NOT `/var/www/html/app/`)
  to avoid collision with Pro templates that might contain their own
  `app/` directory — Codex 016 P2 #8. nginx serves it via
  `location /app/ { alias /opt/govless/webapp/; }`.
- nginx config reserves `/app/` and `/api/` as goVLESS-owned; Pro
  template install refuses to deploy templates whose tree contains a
  top-level `app/` or `api/` directory (operator picks another).
- nginx location block proxies `/api/` → UNIX socket. nginx does
  TLS termination + optional DDoS prefilter only.
- govlessctl Python middleware verifies Telegram `initData` HMAC
  inside the action layer on EVERY RPC call (Codex 016 P1 #6).
  Verification chain per request:
  1. Extract `Telegram-Init-Data` header or `initData` body param.
  2. HMAC-SHA256 verify with `BOT_TOKEN` per Telegram WebApp spec.
  3. Reject if `auth_date` older than `WEBAPP_AUTH_TTL` (default 86400s).
  4. Extract `user.id`; reject if not in `ADMIN_IDS`.
  5. Per-action role check (e.g. `inbound.toggle` requires admin).
  6. Per-action confirmation token (single-use, 5min TTL) for
     destructive actions (see P2 #5 typed-confirm).
  All 6 steps run per-request — no caching of auth state.
- Reuses Phase A RPC contract.

### Phase D — Quick Tunnel + Named Tunnel for Lite (1 commit)

- Auto-install `cloudflared` binary in Lite mode (per Resolved Q5).
  Apt repo preferred; falls back to GitHub release tarball.
  Install failure does NOT abort goVLESS install — see Q5 graceful
  fallback. WebApp button hidden if cloudflared missing.
- Deploy 4 systemd units (cloudflared-quick.service +
  cloudflared-url.path + cloudflared-url.service +
  tunnel-health.timer + tunnel-health.service) + 3 helper scripts.
- Document Named Tunnel as advanced in `docs/webapp-hosting.md`.

### Phase E — SSH-only panel mode (1 commit)

- Implementation per "SSH-only / localhost panel mode" section.

Phases can be re-ordered but A is gate for everything else.

## Security baseline (binding)

- Bot token + admin claim token in `/etc/govless/bot.env`, root:root 0600.
- `ADMIN_IDS` whitelist enforced on every callback handler (decorator).
- Destructive actions require **typed-confirmation** (not just Y/N
  callback). Operator must echo a specific string to the bot/WebApp:

  | Action                              | Required confirm string  |
  | ----------------------------------- | ------------------------ |
  | `client.delete`                     | `DELETE <client_name>`   |
  | `client.reset_traffic`              | `RESET <client_name>`    |
  | `client.disable` (last admin's)     | `DISABLE LAST`           |
  | `inbound.toggle` (disable)          | `DISABLE INBOUND <id>`   |
  | `subscription.rotate`               | `ROTATE SUB <client>`    |
  | `cert.force_renew`                  | `RENEW <domain_or_IP>`   |
  | `tunnel.mode_change`                | `TUNNEL <mode>`          |
  | `panel.access_change` (→ssh-only)   | `LOCKOUT-RISK PROCEED`   |
  | `xui.reinstall`                     | `REINSTALL XUI <ver>`    |

  Exact case-sensitive match. 60s TTL on the confirmation prompt;
  expired → restart. Same contract Telegram bot + WebApp.
- Non-typed quick-confirm (Y/N) acceptable for low-blast actions:
  `client.enable`, `client.disable` (non-last), traffic-limit edit
  within ±50% of current, expiry edit forward by ≤30 days.
- Rate limit: 10 commands/min per TG id, 100/hour. Exceed → 5min cooldown.
- WebApp: HMAC verification lives INSIDE govlessctl (Python middleware
  on every RPC handler). nginx terminates TLS and may rate-limit /
  DDoS-filter, but is NEVER the only auth gate (Codex 016 P1 #6).
- govlessctl socket: UNIX (localhost) only, never TCP.
- No echoing of UUIDs / PAT / x-ui passwords in logs.
- Every `--dry-run` mode for destructive `govlessctl` calls.

## Resolved scope (human, 2026-05-16)

All 5 open questions answered. These decisions are binding for Phase A implementation.

### Q1. Bot language stack → **Python + aiogram v3** (default accepted)

Rationale (Architect's): sqlite3/http/json/asyncio in stdlib, no compile
step, mature TG libs, type-hinted. aiogram v3 async, modern API. Codex
may still flag specific lib choices in 015 audit.

### Q2. Bot deployment → **Same VPS, systemd unit `govless-bot.service`**

Bot runs on the same VPS as 3X-UI panel, talks to `govlessctl` over
the localhost UNIX socket `/run/govlessctl.sock`. systemd unit
`govless-bot.service` with `EnvironmentFile=/etc/govless/bot.env`,
`After=govlessctl.service`, `Restart=on-failure`, `RestartSec=10`.

### Q3. WebApp scope for v1 → **FULL** (read + writes)

WebApp from day one must support the full Client-Card action set:
- **Read:** client list, traffic, online state, links, QR, subscription URL
- **Writes:** create client, edit (rename / set traffic limit / set
  expiry / reset trial / reset traffic), enable/disable (ban/unban),
  delete client, rotate subscription URL

Phases A→D are NOT staged as "read-only first". A `govlessctl` action
layer must expose the full write surface from Phase A; WebApp consumes
the same surface from Phase C. No degraded v1 → v1.1 dance.

This raises the security stakes (see 015 audit ask) — every write
through WebApp must HMAC-verify Telegram `initData` server-side BEFORE
reaching govlessctl. Confirmations required on destructive writes
(delete client, reset trial, disable last admin).

### Q4. Audit retention → **90 days, auto-prune via systemd timer**

`govless_audit` table keeps rows for 90 days. Daily cron/systemd timer
`govless-audit-prune.timer` (fires 03:14 UTC) runs:
```sql
DELETE FROM govless_audit
 WHERE ts < strftime('%s','now') - 90*86400;
```

Older rows are deleted permanently (no archival to file in v1 — can be
added later if compliance needs it). Audit table acquires a soft
ceiling around 10k rows per 90 days at expected admin activity; the
sqlite file stays ≪ 1 MB. ANALYZE/VACUUM done weekly.

### Q5. cloudflared install → **Auto-install on Lite mode, graceful fallback**

When user picks Lite mode at install:
1. Script tries to install `cloudflared` via the official Cloudflare apt
   repo (or direct GitHub release tarball for non-apt distros).
2. **If install succeeds:** all 4 systemd units from Rule 2 are deployed
   and enabled; Quick Tunnel comes up; bot picks up the URL.
3. **If install fails** (apt down, no network, etc.): goVLESS install
   continues normally without cloudflared. Bot starts, but WebApp menu
   button is **hidden**. On `/start` the bot tells admin:
   `"⚠ WebApp недоступен — cloudflared не установлен. Поставь вручную
   (apt install cloudflared) и перезапусти govless-bot.service, или
   используй inline-меню."`
4. Admin can later run `govless cloudflared install` to retry, or
   manually `apt install cloudflared && systemctl enable --now
   cloudflared-quick.service cloudflared-url.path tunnel-health.timer`.

Install does NOT fail because of cloudflared. WebApp is enhancement,
not v1 functional requirement (bot inline-buttons cover the full
action surface).

In Pro mode cloudflared is **not installed** by default — WebApp goes
through nginx on the user's domain (no tunnel needed).


## Reviewer Ask (Codex)

Please audit this proposal end-to-end:

1. **Rule violations** — does any Phase A/B/C/D step accidentally
   violate Rules 1–8? Especially Rule 1 (no caching) and Rule 6
   (read-after-write).
2. **Quick Tunnel auto-recovery** — sanity check the 7-step
   design. Especially: race between log-parser writing URL and bot
   reading it; can health-check false-positive during cloudflared
   warm-up; does `Restart=always` + RestartSec=10 actually give
   cloudflared time to register? Anything missing for hostile probing
   (e.g. someone hammering /healthz).
3. **Capability probe completeness** — what 3X-UI features did I miss?
4. **Action surface (`govlessctl` RPC methods)** — is the method list
   right-sized? Does every UI button in 007's Client Card have a
   matching method?
5. **Telegram-initData HMAC** for WebApp — confirm approach is sound
   or propose hardening.
6. **Open question #1 (lang stack)** — recommend Python/Go/bash.
7. **Open question #5 (cloudflared default)** — opt-in or auto-install?

Drop your audit as `015-reviewer-bot-proposal-audit.md`.
Architect proceeds to **Phase A implementation** after your audit.
