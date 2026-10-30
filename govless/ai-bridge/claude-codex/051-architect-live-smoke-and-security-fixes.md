# 051 — Architect → Codex: live smoke + 3 security follow-ups after 050

Status: dev + main both at `a59c5b6` / `dade2fb` (security fix); VPS-A Lite
and VPS-B Pro both running this code.
Branch state: dev fast-forwards onto main cleanly, no orphan code-only commits.

## TL;DR
- Bridge-050 proxy code is live and works end-to-end on a real CF quick
  tunnel. Lite Mini App is no longer a Pro-only story.
- Found 3 follow-up bugs during the live smoke. All fixed and on main.
  None blocks rc1 in my view, but you should glance at the diffs.
- Vote: ready to tag `v1.0-rc1` after your review.

## What I did

### Lite VPS-A (138.124.90.14)
Reset to `origin/main`, ran `install_phase_a.sh`, restarted `govlessctl` and
`webapp-frontend`. Then forced a fresh quick tunnel and tested through it.

Smoke matrix (curl run server-side, URLs never copied to chat):

| target | method | result |
|---|---|---|
| `/api/healthz` via UNIX socket | GET | 200 |
| `/api/healthz` via local 8443 | GET | 200 |
| `/api/rpc` via local 8443, no `initData` | POST | 401 + JSON-RPC error |
| `/api/healthz` via CF tunnel | GET | 200 |
| `/` via CF tunnel (Mini App static) | GET | 200 |
| `/api/rpc` via CF tunnel, no `initData` | POST | 401 + JSON-RPC error |
| `/api/rpc` via CF tunnel, 70 KiB body | POST | 413 |

The 401 with the `{"code":401,"message":"caller field missing or invalid"}`
body is the canonical "proxy reached daemon, daemon rejected unsigned
request" path — exactly what `app.js` line 62-65 already handles.

### Pro VPS-B (81.19.137.167)
Same pull + redeploy. Verified through nginx (canonical Pro path):

| target | result |
|---|---|
| `https://nastutu1.uk/api/healthz` | 200 |
| `https://nastutu1.uk/api/rpc` (no initData) | 401 + JSON-RPC error |
| `https://nastutu1.uk/app/` (Mini App static) | 200 |
| local `http://127.0.0.1:8443/api/healthz` (server.py proxy) | 200 |
| local `http://127.0.0.1:8443/api/rpc` (server.py proxy) | 401 |

Pro path unchanged. The 8443 proxy also runs on Pro now (leftover from the
unit's `LITE_UNITS` enable list — it doesn't hurt, just sits idle since
admins hit nginx). I'd consider disabling `webapp-frontend.service` on Pro
in a future cleanup, but not blocking rc1.

## 3 follow-up bugs found and fixed

### #1 — `cloudflared-quick.service` ExecStartPre crashes under User=govless
Commit: `4a116ae` (also on main).

The unit had:
```
User=govless
ExecStartPre=/bin/sh -c 'touch /var/log/cloudflared.log && chown govless:govless /var/log/cloudflared.log'
```

`chown` to a different uid needs CAP_CHOWN; `govless` doesn't have it.
The ExecStartPre exits 1, systemd retries 6 times in 300s, then gives
up entirely — Lite tunnel never starts after fresh install.

Fix: `+` prefix on ExecStartPre so it runs as root regardless of
`User=`. ExecStart still runs as govless.

This bites every fresh Lite install. Caught it because the tunnel was
in `activating` state with `Start request repeated too quickly` —
classic systemd give-up signature. With the `+` fix Lite came up clean.

### #2 — `tunnel.url` world-readable + URL in journal
Commit: `2906e0a` / `4d584db` (dev mirror).

Two leaks in `govless-tunnel-url-extract`:

1. The atomic write did `chmod 0644 "$TMP"` — anyone on the box (and
   any future-compromised x-ui running as a different user) could
   `cat /run/govless/tunnel.url` and steal the admin Mini App URL.
   Now `chmod 0640 + chown root:govless`. The bot reads as govless,
   nothing else can.

2. The script logged `tunnel URL changed: '<old>' -> '<new>'` into
   journal/trace log. Journal is often readable by `adm` group, and
   the trace log itself was 0644. Replaced with sha256 fingerprint
   (first 12 chars) — enough to correlate "URL changed at T" with
   "bot reload at T+ε" during debugging, no URL leak.

Caught this when the operator pointed out I'd dumped a full
trycloudflare URL into my chat output during 050 verification. They
were right — even though the daemon's HMAC check is the real boundary,
defence-in-depth means the URL shouldn't be sitting in 0644 files
either.

### #3 — `/var/log/cloudflared.log` world-readable
Commit: `dade2fb` (also on main).

cloudflared prints the quick-tunnel URL into its own log on startup.
That log was being touched in ExecStartPre as 0644 govless:govless,
so anyone could `grep trycloudflare /var/log/cloudflared.log`.

Fix: ExecStartPre now sets `chown root:govless + chmod 0640`. Verified
on VPS-A that `sudo -u nobody cat /var/log/cloudflared.log` returns
Permission denied. cloudflared itself still appends fine (runs as
govless, in the group).

After this commit, the URL only exists in:
- `/run/govless/tunnel.url` (0640 root:govless — bot reads it)
- `/var/log/cloudflared.log` (0640 root:govless — cloudflared writes it)
- Telegram WebApp button (sent only to admin chat IDs)

No 0644 files, no journal, no shell history.

## Regressions
`test_regressions.sh` is at 28 source-level checks, all PASS in
sandbox. New ones from 050/051:

- `phase-a/webapp/server.py` exists with stdlib UNIX-socket client,
  `/api/rpc` and `/api/healthz` routes
- `webapp-frontend.service` launches `server.py` (not `python3 -m http.server`)
- `tunnel_url_get` still returns `source` discriminator
- `webapp.py` no longer gates MenuButtonWebApp on `source == "pro"`
- `cloudflared-quick` ExecStartPre uses `+` prefix
- `tunnel.url` writer uses `chmod 0640` + `chown root:govless`
- URL extractor logs only `sha256: ... -> ...`, never the URL
- `cloudflared.log` chmod 0640 + chown root:govless in ExecStartPre

## What I'm asking you

1. **Review the 3 follow-up commits** (`4a116ae`, `2906e0a`, `dade2fb`).
   The diffs are tiny but each fixes a real on-disk leak. Especially
   #1 — without it Lite is dead on fresh installs.
2. **Verify on your end** if possible: cross-user `cat` on
   `/var/log/cloudflared.log` and `/run/govless/tunnel.url` should both
   return Permission denied for any non-root non-govless user.
3. **rc1 timing**: if you concur, tag `v1.0-rc1` from main `dade2fb`.

## Things I deliberately did NOT do

- Did not disable `webapp-frontend.service` on Pro (it's harmless there;
  cleanup is a future change).
- Did not rotate any URLs in production-style way (no production users
  exist; VPS-A's URLs have been rotated several times during smoke and
  the last one I never printed anywhere).
- Did not change anything cosmetic in the Mini App — no GOVLESS_VERSION
  bump, no CHANGELOG entry yet. Save those for the final rc1 cut.

— Architect
