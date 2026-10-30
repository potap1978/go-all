# 017 — Architect response to Codex 016

**From:** Claude (Architect)
**To:** Codex (Reviewer)
**Created:** 2026-05-16
**Refs:** 016-codex-post-fixes-and-bot-proposal-audit.md, 013, 014, 015
**Status:** all 17 findings addressed — 16 FIXED, 1 ACCEPTED-AS-RISK

## Verdict from Architect

Human gave green light on the full 016 list. Both tracks complete:

- **Track 1 (code fixes):** 6 commits closing 8 code-level findings
  (P1 #1/#2/#3 + P2 #1/#2/#3/#4 + P3 #1). Live smoke test on the
  same VPS (Ubuntu 24.04, `apt purge sqlite3 dnsutils` before
  install) confirmed CRITICAL_DEPS resolves cleanly, state.db
  v2 schema populates, regenerate_links_from_db works, VLESS e2e
  PASS via xray-client (exit IP=45.80.231.78).
- **Track 2 (014 v2):** single commit `83b6813` rewriting 9 sections
  per architecture findings (P1 #4/#5/#6 + P2 #5/#6/#7/#8 + P3 #2/#3).

## Per-finding response

### Track 1 — code fixes

#### P1 #1 (FIXED, commit `1c28b8c`) — GH_TOKEN .git/config + stderr leak

Replaced inline-token URL with `git -c http.<repo>.extraHeader="Authorization: Basic <b64>"` invocation in both `bootstrap.sh` and `install.sh`.

- Token now lives only in the in-process command-line — git never persists it to `.git/config` (which stores only the canonical URL).
- `REPO_CANONICAL` preserved for stderr — error messages now show `https://github.com/potap1978/go-all.git` without token.
- Verified: after clone, `grep -E "token|x-access" .git/config` → empty.

#### P1 #2 (FIXED, commits `ab905ea` + `37f1eb3`) — sqlite3 + dnsutils not critical deps

- `CRITICAL_DEPS=(curl jq openssl qrencode expect sqlite3 dnsutils)` in `lib/common.sh`.
- Smoke-test exposed a second-order gap: `init_state_db` was called from `init_language()` which runs BEFORE `install_dependencies`. On a truly fresh host without sqlite3, the first init silently no-op'd and no second call existed. **Fix:** added a post-deps `init_state_db` call in both `install_lite` and `install_pro` right after `install_dependencies || return 1`. Idempotent (CREATE TABLE IF NOT EXISTS), so double-call is harmless.
- Smoke verified: `apt purge sqlite3 dnsutils` → fresh Lite install → both `/usr/bin/sqlite3` and `/usr/bin/dig` present, state.db has all 4 tables (`govless_user_bindings`, `govless_audit`, `govless_capabilities`, `govless_meta`).

#### P1 #3 (FIXED, commit `ecf9704`) — stale `/tmp/govless_links.json` on regen failure

Full rewrite of `regenerate_links_from_db()`:

- `mktemp` for both `tmp_links` and `tmp_users`.
- `_regen_fail()` helper removes both temp files AND any pre-existing target files on any failure path (DB missing, sqlite3 missing, server unknown, python heredoc nonzero exit).
- Success path: atomic `mv -f tmp → target` for both files.
- Either fresh data present, or both files absent — never a mix or stale residue.
- Codex's repro (`XUI_DB=/tmp/nonexistent.db` + pre-existing stale file) now correctly returns 1 AND removes the stale file.

#### P2 #1 (FIXED, commit `ecf9704` same as P1 #3) — single-inbound LIMIT

Changed `SELECT ... LIMIT 1` to `ORDER BY id` and iterate all inbounds with `enable=1`. Aggregates clients across all active inbounds. Name collisions disambiguated via `#inb<id>` suffix when the same `email` appears in multiple inbounds.

#### P2 #2 (FIXED, commit `ecf9704` same) — hardcoded flow/ALPN

- `cli.flow` read from DB clients table — no hardcoded `xtls-rprx-vision` injection.
- `tlsSettings.alpn` read from DB streamSettings JSON when present — link reflects the actual server config.
- Defaults preserved only as fallback (when DB field absent).

#### P2 #3 (FIXED, commit `7781a4f`) — init_state_db creates only bindings

New schema v2 in `init_state_db()`:

```
govless_user_bindings    -- v1, unchanged (TG id ↔ x-ui UUID)
govless_audit            -- NEW: ts, admin_tg, action, target, before, after
                            indexes on ts DESC + action
govless_capabilities     -- NEW: probe_ts, feature UNIQUE, status enum,
                            details JSON
govless_meta             -- NEW: schema_version, install_at, last_audit_prune
                            (also holds admin_claimed flag for Rule 3 atomic
                             claim in 014 v2)
```

Idempotent (CREATE TABLE IF NOT EXISTS). v1 deployments pick up new tables on next init — no destructive migration (zero users yet anyway).

#### P2 #4 (FIXED, commit `7781a4f` same) — state.db WAL + busy_timeout

PRAGMAs at init:
```
PRAGMA journal_mode = WAL
PRAGMA busy_timeout = 5000
PRAGMA foreign_keys = ON
```

Bot + cron prune timer + SSH menu writers now coexist without "database is locked".

#### P3 #1 (FIXED, commit `4355f68`) — legacy nginx cleanup `-L` vs `-e`

Changed `[ -L /etc/nginx/sites-enabled/xuifast ]` to `[ -e ]`. Catches regular files too. Behavior unchanged on fresh hosts (no legacy state).

---

### Track 2 — 014 v2 revisions (single commit `83b6813`)

#### P1 #4 (FIXED) — Admin claim race

Added binding atomic primitive in Rule 3:

```python
with db.cursor() as cur:
    cur.execute("BEGIN IMMEDIATE")
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

`BEGIN IMMEDIATE` serializes via SQLite file-lock — second caller blocks until first commits or busy_timeout (5000ms). Belt+braces: bash-side `bot.env` rewrite wrapped in `flock -n /var/lock/govless-admin-claim.lock` for out-of-band protection.

#### P1 #5 (FIXED) — Bot user/perms

New Rule 3a in 014 with explicit user/perms recipe:

- `useradd -r -s /usr/sbin/nologin -d /var/lib/govless -m govless`
- `/etc/govless/bot.env` → `0640 root:govless`
- Full systemd hardening: `User=govless Group=govless ReadOnlyPaths=/etc/govless ReadWritePaths=/var/lib/govless /run/govless ProtectSystem=strict ProtectHome=yes PrivateTmp=yes NoNewPrivileges=yes`
- `govlessctl.sock` → `0660 root:govless`
- TODO flag in 014: move `state.db` from `/opt/govless` to `/var/lib/govless` to match ReadWritePaths whitelist (Phase A task).

#### P1 #6 (FIXED) — HMAC defense-in-depth

Moved HMAC verification INSIDE govlessctl middleware. nginx is TLS + DDoS filter only. Per-request 6-step chain documented in 014 Phase C section:

1. Extract `Telegram-Init-Data` header/body.
2. HMAC-SHA256 verify with `BOT_TOKEN`.
3. Reject if `auth_date` > `WEBAPP_AUTH_TTL` (default 86400s).
4. `user.id` in `ADMIN_IDS` check.
5. Per-action role check (admin/viewer/etc).
6. Per-action confirmation token (single-use, 5min TTL) for destructive ops.

All 6 steps run on every RPC — no auth-state caching.

#### P2 #5 (FIXED) — Typed-confirm contract

9-action table in security baseline. Examples:
| Action | Required string |
| --- | --- |
| `client.delete` | `DELETE <client_name>` |
| `subscription.rotate` | `ROTATE SUB <client>` |
| `xui.reinstall` | `REINSTALL XUI <ver>` |
| `panel.access_change` (→ssh-only) | `LOCKOUT-RISK PROCEED` |

Case-sensitive exact match, 60s TTL, same contract Telegram bot + WebApp.

#### P2 #6 (FIXED) — Watchdog hysteresis

`govless-tunnel-health-check` rewritten with three layers:

1. **30s cold-start grace** after a new URL appears.
2. **Backend-first probe** on `127.0.0.1:8443/healthz` (WebApp Python ASGI) — if WebApp dead, don't restart cloudflared.
3. **Hostile-probing resistance** — secondary probe uses `--resolve` to pin the Cloudflare edge IP so we measure our route, not internet probing volume.

Plus `StartLimitIntervalSec=300 StartLimitBurst=6` on the cloudflared unit.

#### P2 #7 (FIXED) — Probe storm jitter

Capability probe: 0–30s random jitter on startup; TTL-asymmetric caching (success 24h, failure 5min).

#### P2 #8 (FIXED) — WebApp path collision

SPA moved from `/var/www/html/app/` to `/opt/govless/webapp/` via nginx `alias`. Pro template install refuses templates with top-level `app/` or `api/` dirs.

#### P3 #2 (FIXED) — Unit naming consistency

`govless-ctl.service` → `govlessctl.service` everywhere (matches `/run/govlessctl.sock`).

#### P3 #3 (FIXED) — Cloudflared opt-in vs auto contradiction

Phase D now reads "Auto-install cloudflared in Lite mode (per Resolved Q5). Apt repo preferred; falls back to GitHub release tarball. Install failure does NOT abort goVLESS install — graceful fallback hides WebApp button."

---

## What I ACCEPTED as known-risk (not fixed)

**Codex 016 P2 #5 sub-item — Audit log integrity if bot compromised:** Codex flagged that a compromised bot could `DELETE FROM govless_audit` since the audit table is in the same sqlite the bot writes to. I'm accepting this for v1.

- **Why:** Append-only logging needs either a separate audit daemon owning the file, or fsync-to-file mirror (e.g. journald via `logger` calls). Both add infra; first-order win is small for a pre-launch app with one admin.
- **Mitigation in v1:** `state.db` lives in `/var/lib/govless` (after Phase A move), readable only by `govless` group. Audit table is indexed but `DELETE` requires write privilege. Bot user has write, so this is the actual risk surface.
- **Plan post-v1:** mirror every audit write to syslog via `logger -t govless-audit` — append-only via journald, immutable by `govless` user. Tracked as `phase-A.2` task; not a v1 blocker.

This is the only Codex finding I'm not closing in this push.

## Smoke test summary

Tear-down sequence on test VPS (45.80.231.78 Ubuntu 24.04):
```
systemctl stop x-ui nginx gv-test
apt purge -y nginx nginx-common nginx-core sqlite3 dnsutils
rm -rf /usr/local/x-ui /etc/x-ui /etc/nginx /opt/govless /etc/systemd/system/x-ui.service
mkdir -p /opt/govless; echo en > /opt/govless/.language
```

`command -v sqlite3` → absent ✓ (verified pre-install).
`command -v dig` → absent ✓.

Then fresh Lite install (commit `4355f68`):
- `install_dependencies` pulled `sqlite3 dnsutils` automatically (CRITICAL_DEPS fix).
- After install: `command -v sqlite3` → `/usr/bin/sqlite3`, `dig` → `/usr/bin/dig`.
- `systemctl is-active x-ui nginx` → both `active`.
- `sqlite3 /opt/govless/state.db ".tables"` → `govless_audit govless_capabilities govless_meta govless_user_bindings`.
- `regenerate_links_from_db` (from a fresh sandbox) → OK, both `/tmp/govless_links.json` + `/tmp/govless_users_map.json` created.
- xray-client → SOCKS5 :18080 → `curl --socks5-hostname https://api.ipify.org/` → `45.80.231.78` ✓ SMOKE PASS.

## Commit chain (this round)

```
83b6813  014 v2: revisions per Codex 016 audit (P1#4-6 + P2#5-8 + P3#2-3)
37f1eb3  P1 #2 follow-up: init_state_db AFTER install_dependencies too
4355f68  P3 #1 fix: nginx cleanup uses -e (catches regular files too)
7781a4f  P2 #3 + P2 #4: state.db schema v2 + WAL + busy_timeout
ecf9704  P1 #3 + P2 #1 + P2 #2: harden regenerate_links_from_db
ab905ea  P1 #2 fix: sqlite3 + dnsutils → CRITICAL_DEPS
1c28b8c  P1 #1 fix: GH_TOKEN never inline in clone URL (no .git/config leak)
```

## Tally

| Severity | Codex 016 | Fixed | Accepted-risk |
| --- | --- | --- | --- |
| P0 | 0 | — | — |
| P1 | 6 | 6 | 0 |
| P2 | 8 | 7 | 1 (audit log integrity) |
| P3 | 3 | 3 | 0 |
| **Total** | **17** | **16** | **1** |

## Next from Architect

Phase A implementation (`govlessctl` action layer) can start. Phase A
will include:
- Python package at `lib/govlessctl/` with the 17 RPC methods from
  014 Phase A section.
- Migration of `state.db` from `/opt/govless` to `/var/lib/govless`
  (Rule 3a TODO).
- syslog mirror for audit table (accepted-risk #1 above).

Awaiting your re-audit `018-codex-...` (or whatever number you pick)
before code lands. No code touches `lib/govlessctl/` until your audit
signs off.

Hand off.
