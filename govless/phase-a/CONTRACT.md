# goVLESS Phase A — inter-module contract

This is the binding interface spec for 4 parallel implementation subagents.
Each module MUST conform to these contracts. Source of truth: 014 v2
(`ai-bridge/claude-codex/014-architect-telegram-bot-proposal.md`).

---

## 1. govlessctl daemon

**Process:** Python 3 (system python, prefer stdlib only; aiohttp OK if needed).
**Listens on:** UNIX socket `/run/govlessctl.sock` (root:govless 0660).
**Protocol:** JSON-RPC 2.0 over HTTP (POST /rpc, Content-Type: application/json).

### Auth model

Every request body MUST include one of:
- `"caller": "local"` — accepted only from local socket and origin = bot process (uid check).
- `"caller": "webapp", "initData": "<raw Telegram WebApp initData string>"` — HMAC-verified against `BOT_TOKEN`.

Auth chain per request (defense-in-depth, Codex 016 P1 #6):
1. Verify peer credentials (`SO_PEERCRED`); bot caller must run as `govless` group.
2. If webapp: HMAC-SHA256 verify initData with `BOT_TOKEN` (per Telegram WebApp spec).
3. Reject if `auth_date` older than `WEBAPP_AUTH_TTL` (default 86400 sec).
4. Extract `user.id`; check in `ADMIN_IDS` (from `/etc/govless/bot.env`).
5. Per-action role check (admin/viewer).
6. Per-action confirmation token (single-use, 5min TTL) for destructive ops.

### 17 RPC methods

| Method | Args | Returns | Destructive? | Notes |
| --- | --- | --- | --- | --- |
| `system.status` | — | `{xui_active, xray_active, nginx_active, panel_url, mode, transport, version}` | no | |
| `client.list` | `{inbound_id?}` | `[{name, uuid, inbound_id, enabled, online, traffic_used, traffic_limit, expiry_ts, flow, link, sub_url}]` | no | regenerates links from x-ui.db |
| `client.add` | `{name, traffic_limit?, expiry_ts?, label?}` | `{uuid, link, qr_text}` | no | adds via 3X-UI API to active inbound |
| `client.update` | `{uuid, fields:{name?, traffic_limit?, expiry_ts?, label?}}` | `{ok}` | mid | needs typed-confirm if traffic_limit changes >50% or expiry forward >30d |
| `client.enable` | `{uuid}` | `{ok}` | no | |
| `client.disable` | `{uuid, confirm?}` | `{ok}` | yes if last admin's | typed-confirm `DISABLE LAST` if last admin's client |
| `client.reset_traffic` | `{uuid, confirm:"RESET <name>"}` | `{ok}` | yes | typed-confirm required |
| `client.delete` | `{uuid, confirm:"DELETE <name>"}` | `{ok}` | yes | typed-confirm required |
| `client.qr` | `{uuid}` | `{qr_text, link}` | no | UTF-8 QR via qrencode |
| `client.sub_url` | `{uuid}` | `{sub_url}` | no | Lite/Pro when 3X-UI sub server is configured; null otherwise |
| `subscription.rotate` | `{uuid, confirm:"ROTATE SUB <name>"}` | `{new_sub_url}` | yes | typed-confirm required |
| `inbound.list` | — | `[{id, port, protocol, network, security, enable, remark, client_count}]` | no | |
| `inbound.toggle` | `{inbound_id, enable, confirm?}` | `{ok}` | yes if disable | typed-confirm `DISABLE INBOUND <id>` |
| `panel.access_get` | — | `{listen, port, url}` | no | |
| `panel.access_set` | `{mode:"public"\|"ssh-only", confirm?}` | `{ok}` | yes for ssh-only | typed-confirm `LOCKOUT-RISK PROCEED` |
| `audit.tail` | `{limit:int<=200}` | `[{ts, admin_tg, action, target}]` | no | |
| `cert.force_renew` | `{domain_or_ip, confirm:"RENEW <target>"}` | `{ok, new_expiry}` | yes | typed-confirm; runs acme.sh or certbot |
| `admin.claim` | `{token}` | `{ok}` | atomic | BEGIN IMMEDIATE on state.db `admin_claimed` row |
| `admin.list` | — | `[tg_id]` | no | |
| `admin.invite` | `{tg_id}` | `{ok}` | mid | idempotent (re-invite no-op) |
| `tunnel.url_get` | — | `{url, source:"quick"\|"named"\|"none"}` | no | reads `/run/govless/tunnel.url` |
| `notify-admins` | `{message, severity}` | `{sent}` | no | bot reads + forwards (callback into bot via local socket) |
| `site.template_status` | — | `{available, mode, domain, site_url, current:{id, kind, source_url, template}, catalog:{ok,total,categories}, job, backups}` | no | Pro cover site; `kind` = catalog/custom/stub/unknown |
| `site.template_categories` | — | `[{id, name, name_en, count}]` | no | 18 categories, RU names from the catalog + EN map |
| `site.template_list` | `{category?, q?, page?, per?≤50}` | `{category, q, page, pages, per, total, items:[{id, key, name, source, license, stars, preview_url, repo_url, description, category, category_en}]}` | no | `q` searches name/id across all or within a category (≤500 hits) |
| `site.template_get` | `{template_id}` \| `{key}` | `{template}` | no | `key` = sha1(id)[:12] for Telegram callback_data |
| `site.template_random` | `{category?}` | `{template}` | no | |
| `site.template_apply` | `{template_id}` \| `{key}` \| `{custom_url}` \| `{stub:true}` \| `{random:true}` \| `{restore:"/opt/govless/site-backups/site_*.tgz"}` | `{job:{id, state, action, target, name, started_ts, …}}` | mid | requires Pro mode (409 otherwise); one job at a time (409 while running); runs `govless --site-template-* --json` in the background, old site backed up |
| `site.template_job` | — | `{job}` with `state` running/done/failed, `ok`, `template_id`, `error_code`, `error`, `http_code`, `backup`, `log` | no | poll every 2-3 s; daemon kills the job after 420 s |

### State stores

- `/etc/x-ui/x-ui.db` — read+write via 3X-UI HTTP API (don't write directly to clients/inbounds; settings ok via sqlite).
- `/var/lib/govless/state.db` — owned by `govless:govless`. Schema in `init_state_db()` (already in `lib/xui_api.sh`). **Path change from `/opt/govless/state.db` per Rule 3a TODO** — on startup, if `/opt/govless/state.db` exists and `/var/lib/govless/state.db` doesn't, move it.
- `/etc/govless/bot.env` — read-only (0640 root:govless). Format: KEY=VALUE shell.
- `/run/govless/tunnel.url` — read (Lite mode only).

### Errors

All errors return JSON:
```json
{"error": {"code": <int>, "message": "<human>"}, "id": <req id>}
```
Standard codes: 401 unauth, 403 forbidden, 404 not found, 409 conflict (e.g. admin already claimed), 423 locked (state.db busy after 5s), 500 internal, 503 3X-UI unavailable.

### Audit hook

Every write method MUST insert a row into `govless_audit` BEFORE returning:
```python
audit(admin_tg, action, target, before_state_json, after_state_json)
```
Read methods do NOT audit.

### Sample request

```
POST /rpc HTTP/1.1
Content-Type: application/json

{"jsonrpc":"2.0","method":"client.list","params":{},"id":1,"caller":"local"}
```

---

## 2. TG bot

**Process:** Python 3 + aiogram v3 (`pip install aiogram`).
**Service:** `/etc/systemd/system/govless-bot.service`, runs as `govless:govless`.
**Loads:** `/etc/govless/bot.env` → `BOT_TOKEN`, `ADMIN_CLAIM_TOKEN`, `ADMIN_IDS`, `WEBAPP_AUTH_TTL`.

### Handlers

- `/start` — if user not in ADMIN_IDS → "ask admin to invite you" + claim-instructions if no admin yet. If admin → main menu.
- `/admin <token>` — atomic claim via `admin.claim` RPC. Locks via state.db BEGIN IMMEDIATE.
- `/invite <tg_id>` — admin-only, calls `admin.invite` RPC. Idempotent.
- `/audit [N]` — last N (default 20) audit entries.
- `/help` — list of commands.

### Inline menu (Codex 007 90% menu)

Main:
- `🏠 Overview`
- `👥 Clients`
- `🛡 VPN / Connection`
- `🔐 Panel access`
- `🤖 Bot admins`
- `⚙️ System`

Each → submenu via callback_data. Stateless: every menu render → `govlessctl` RPC, no in-memory cache.

Client card:
- `[Open in app] [Show QR]`
- `[Subscription URL]` (when 3X-UI sub server is configured)
- `[Disable] [Set traffic limit]`
- `[Set expiry] [Reset traffic]`
- `[Rename] [Delete client]`

### Typed-confirm flow

When destructive action selected → bot sends:
```
⚠️ Destructive action: <description>
To confirm, reply with: <CONFIRM STRING>
(60s TTL; expired requires restart)
```
Operator must reply with EXACT case-sensitive string. Bot stores pending confirmations in-memory dict `{user_id: (action, expected_string, expiry_ts)}`. Worker thread prunes expired. On match → call govlessctl with `confirm` param.

### WebApp button

Read `tunnel.url_get` RPC; if Pro mode, button URL = `https://<domain>/app/`; if Lite, button URL = current `/run/govless/tunnel.url`. Bot polls `/run/govless/tunnel.url` mtime every 30s; on change → `bot.set_chat_menu_button(MenuButtonWebApp(...))` for every admin + send notification.

### Rate limit

In-process dict (acceptable accepted-risk for v1 per 017): `{(user_id, scope): deque[ts]}`.
Message commands: 10/min, 100/hour, exceed → 5min cooldown.
Inline callback navigation: 30/min, 300/hour, exceed → 45s cooldown.

### Bot deliverables

- `/opt/govless/bot/bot.py` — main aiogram bot
- `/opt/govless/bot/handlers/` — handler modules (start, admin, menu, clients, etc.)
- `/opt/govless/bot/rpc.py` — govlessctl client (UNIX socket POST /rpc)
- `requirements.txt` — aiogram, ujson (if used)

---

## 3. WebApp

**Hosting:** static files at `/opt/govless/webapp/dist/` (NOT /var/www/html/app/ per P2 #8).
**nginx alias:** `location /app/ { alias /opt/govless/webapp/dist/; try_files $uri /index.html; }` + `location /api/ { proxy_pass http://unix:/run/govlessctl.sock; }`.
**TWA API:** `https://<domain>/app/` (Pro) or `https://<random>.trycloudflare.com/app/` (Lite).

### Tech

- Vanilla HTML/CSS/JS. No React/Vue. One `index.html` + `app.js` + `style.css`.
- LocalStorage keys: `gov_last_view`, `gov_filter`. Reset on user_id change.
- API call wrapper: `fetch('/api/rpc', {method: 'POST', body: JSON.stringify({jsonrpc:"2.0", method, params, id, caller:"webapp", initData: window.Telegram.WebApp.initData})})`.

### Pages (single-page route via hash)

- `#/` Dashboard (system.status + last 5 audit)
- `#/clients` Client list (search/filter, paginated)
- `#/clients/:uuid` Client card (with all actions per CONTRACT)
- `#/inbounds` Inbound list
- `#/admins` Admin list + invite form
- `#/system` Force renew, panel access mode, audit full

### UX

- Mobile-first (TG WebView viewport ~380px).
- Destructive actions show modal asking for exact typed-confirm string.
- After write → show optimistic state, then re-fetch from server, reconcile.
- Offline detection: if RPC returns 503, show banner "3X-UI offline — reads only".

### WebApp deliverables

- `/opt/govless/webapp/dist/index.html`
- `/opt/govless/webapp/dist/app.js` (~300-500 lines, hand-written, no build step)
- `/opt/govless/webapp/dist/style.css`
- `/opt/govless/webapp/dist/healthz` (plain text "ok" file for nginx /healthz route — BACKUP, primary /healthz is via govlessctl)

---

## 4. systemd + cloudflared + nginx

**Deliverables in `/etc/systemd/system/`:**

- `govlessctl.service` — runs `/opt/govless/bin/govlessctl-daemon` as root (it needs to talk to 3X-UI on localhost which sometimes needs root for sqlite WAL). Wait — actually socket should be 0660 root:govless so bot reads. Decision: run as root for simplicity in v1; chown socket 0660 root:govless.
- `govless-bot.service` — runs `/opt/govless/bot/bot.py` as govless:govless with full hardening per 014 Rule 3a.
- `cloudflared-quick.service` — Lite mode only, Restart=always with StartLimitIntervalSec=300 StartLimitBurst=6.
- `cloudflared-url.path` — PathModified on `/var/log/cloudflared.log`.
- `cloudflared-url.service` — oneshot triggered by .path, runs `/usr/local/bin/govless-tunnel-url-extract`.
- `tunnel-health.timer` — every 5 minutes.
- `tunnel-health.service` — runs `/usr/local/bin/govless-tunnel-health-check`.
- `govless-audit-prune.timer` — daily at 03:14 UTC.
- `govless-audit-prune.service` — `DELETE FROM govless_audit WHERE ts < strftime('%s','now') - 90*86400` + VACUUM weekly.

**Scripts in `/usr/local/bin/`:**

- `govless-tunnel-url-extract` — parses cloudflared.log, atomic write to /run/govless/tunnel.url, reload bot if changed.
- `govless-tunnel-health-check` — per 014 v2 hysteresis design.
- `govless-tunnel-escalation-check` — alerts via wall+systemd-journald+govlessctl notify-admins.
- `govlessctl` — thin Python CLI wrapper around the UNIX socket for SSH operators.

**nginx config:**

`/etc/nginx/snippets/govless-webapp.conf` (included from main site config):
```nginx
location /app/ {
    alias /opt/govless/webapp/dist/;
    try_files $uri $uri/ /app/index.html;
}
location /api/rpc {
    proxy_pass http://unix:/run/govlessctl.sock:/rpc;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 30s;
}
```

In Lite mode (no nginx for the panel), `/app/` is served via cloudflared tunnel → backend on `127.0.0.1:8443` which is the Python ASGI of webapp (we run a tiny Python http server as `webapp-frontend.service` in Lite mode).

**Install hook:** `cloudflared` auto-install via apt repo on Lite mode, GitHub release tarball fallback, install failure non-fatal per Resolved Q5.

---

## Glue: bot.env

`/etc/govless/bot.env`:
```
BOT_TOKEN=
ADMIN_CLAIM_TOKEN=<random 32-hex generated at install>
ADMIN_IDS=
WEBAPP_AUTH_TTL=86400
```
Generated at install: `ADMIN_CLAIM_TOKEN=$(openssl rand -hex 16)`. `BOT_TOKEN` left empty for human to fill.

If `BOT_TOKEN=""` → bot service refuses to start with clear error: "BOT_TOKEN not set in /etc/govless/bot.env — create a bot via @BotFather and paste the token, then systemctl restart govless-bot".

---

## Output layout for subagents

Each subagent writes into `/Users/vitalijlitvinov/Library/Application Support/Claude/.../outputs/govless-phase-a/<module>/`. After all 4 return, integration commits.

- `govless-phase-a/govlessctl/` — Python package files
- `govless-phase-a/bot/` — aiogram bot files
- `govless-phase-a/webapp/` — HTML/CSS/JS
- `govless-phase-a/systemd/` — unit files + scripts + nginx conf
