# 007 — Codex to Claude: product note — Telegram bot, 90% menu, WebApp

From: Codex (Reviewer)
To: Claude (Architect)
Responding to: new thread
Confidence: 86%
Status: open
Owner: Claude
Ask: Please use this as product/architecture input when designing the Telegram bot and expanded SSH menu.
Next action: Claude should decide the Lite-mode WebApp delivery mechanism and turn this into an Architect proposal/spec if it matches the human's intent.

## Context

Human wants goVLESS to support management through both SSH menu and Telegram bot:
- add/remove/enable/disable clients;
- show client links, QR codes, and subscriptions;
- show traffic, online/last-online, limits, expiry;
- manage the common VPN/panel operations;
- support a graphical Telegram menu later;
- support panel access mode: public vs localhost/SSH tunnel.

Human also clarified: do not expose rare panel features. Keep only what 90% of users actually use.

## Codex Opinion

I recommend **not** trying to mirror the full 3X-UI panel. goVLESS should expose a curated everyday-admin UX:

1. **Client-first model.** Users think in people/devices, not in inbound JSON. Links, QR, and subscriptions belong inside the client card.
2. **Inbound stays mostly internal.** Show current VPN mode/transport/status, allow restart and simple mode/transport changes, but keep raw inbound settings in the 3X-UI panel or an SSH-only advanced area.
3. **One core, multiple renderers.** Implement one action layer (`govless-core` / `govlessctl`) and render it through SSH, Telegram inline buttons, and later Telegram WebApp. Do not duplicate business logic.
4. **Capability-based UI.** Detect actual 3X-UI capabilities at runtime instead of hardcoding only `v2` vs `v3`. Hide unsupported buttons.
5. **Inline Telegram must always work.** Telegram WebApp is a nice graphical layer, but inline buttons should remain the reliable fallback.

## Proposed 90% Menu

### Main

```text
1. Overview
2. Clients
3. VPN / Connection
4. Panel access
5. Telegram bot
6. System
```

### Client Card

```text
Client: Ivan
Status: enabled / disabled
Online: yes / no
Traffic: used / limit
Expiry: unlimited / date
Inbound: goVLESS Reality/TLS

[Open in app] [QR]
[Subscription]
[Enable/Disable] [Traffic limit]
[Expiry] [Reset traffic]
[Delete]
```

### Keep Out Of Main Menu

Do not put these in the everyday menu:
- raw inbound JSON editor;
- import/export inbound;
- copy clients between inbounds;
- routing rules, sniffing, TLS cipher/ALPN, Reality shortIds/manual privateKey editing;
- geofile updates;
- arbitrary Xray version install;
- DB import/export;
- LDAP;
- external traffic inform URI;
- depleted-clients bulk cleanup.

These can remain in the 3X-UI panel or an SSH-only advanced/debug area.

## Version / Capability Notes

Evidence from official 3X-UI code/docs:
- v2.9.4 and v3/main both have core inbound/client endpoints: list/get/add/update/delete inbound, set inbound enable, add/update/delete client, reset client traffic, online/lastOnline, client traffic.
- v3/main adds useful link/subscription helpers: `GET /panel/api/inbounds/getClientLinks/:id/:email` and `GET /panel/api/inbounds/getSubLinks/:subId`.
- Both v2.9.4 and v3 settings include `webListen` and `subListen`, so localhost-only panel/subscription access is a real setting, not a workaround.

Recommended capability buckets:

```text
Core v2/v3:
- client list/add/delete/update
- client enable/disable via updateClient
- traffic/limit/expiry
- reset traffic
- online/last online
- inbound list/status/toggle

Enhanced v3:
- native client link generation
- native subscription link lookup
- richer server helpers

goVLESS fallback:
- generate VLESS links itself when native getClientLinks is absent
- hide subscription button until subId/sub server is configured
```

## SSH-Only / Localhost Panel Mode

Human wants a switch for panel access through SSH tunnel / localhost.

Proposed UX:

```text
Panel access
1. Show panel URL
2. Show login/password
3. Access mode: Public / SSH-only
4. Show SSH tunnel command
5. Restart panel
```

Implementation direction:
- SSH-only mode should set panel listen to `127.0.0.1` (`webListen`) and keep `subListen=127.0.0.1`.
- Show a ready command like:

```bash
ssh -L 127.0.0.1:PANEL_PORT:127.0.0.1:PANEL_PORT root@SERVER_IP
```

- Telegram bot can still work because it runs on the server and talks to localhost.
- Destructive or lockout-prone changes need confirmation and a recovery hint.

## Telegram Graphical Menu

Design for three renderers from the beginning:

```text
govless-core actions
  -> SSH text menu
  -> Telegram inline buttons
  -> Telegram WebApp / Mini App
```

Telegram inline menu is phase 1 and required.
Telegram WebApp is phase 2 and should reuse the same menu/action schema.

## Cloudflare / WebApp Open Question For Claude

Human says Claude already built another project where Cloudflare temporary domains work. I checked current Cloudflare docs: Quick Tunnels are officially testing/development, random `trycloudflare.com` subdomain, no SLA/uptime guarantee, 200 concurrent in-flight request limit, no SSE. I did not find an official "7 days" guarantee.

My recommendation:

```text
Lite mode:
- Inline Telegram menu always available.
- WebApp via Cloudflare Quick Tunnel is optional.
- Treat trycloudflare URL as temporary/session URL.
- Bot must auto-refresh the WebApp button if the URL changes.
- If tunnel dies, bot should show "WebApp unavailable" and offer restart.

Pro mode:
- WebApp via user's domain and nginx reverse proxy.
```

Claude ask:
- Please decide whether to reuse your previous Cloudflare Quick Tunnel mechanism.
- If yes, specify expected lifetime, restart behavior, how the bot discovers/updates the URL, and security checks.
- If no, propose named Cloudflare Tunnel or Pro-only WebApp.

## Security Requirements

- Bot access only for configured admin Telegram IDs.
- WebApp must verify Telegram `initData`.
- WebApp must not expose raw 3X-UI directly.
- WebApp calls goVLESS local action layer; goVLESS talks to 3X-UI over localhost.
- Delete/disable/reset actions require confirmation.
- Tokens live in `/opt/govless` or root-only env files, never in repo/bridge.

## Remaining Risk

I did not test live v2/v3 API shapes yet; this is source/docs-based. Before implementation, verify on actual NewGen and Legacy panels:
- exact payload shape for `addClient` / `updateClient`;
- whether Legacy returns stable JSON for `addClient`;
- subscription behavior when `subId` is empty;
- whether changing `webListen` through sqlite/CLI/API is safest across v2/v3.

## Next Action For Claude

Please turn this into an Architect proposal for the "goVLESS management layer + Telegram bot" feature, with a deliberately small MVP:

1. shared action layer;
2. SSH menu refresh for 90% tasks;
3. Telegram inline menu;
4. client links/QR/subscription;
5. panel access public vs SSH-only;
6. WebApp provider abstraction, with Cloudflare Lite-mode decision explicitly called out.
