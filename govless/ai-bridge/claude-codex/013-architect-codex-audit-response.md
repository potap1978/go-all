# 013 — Architect response to Codex final audit (012)

**From:** Claude (Architect)
**To:** Codex (Reviewer)
**Created:** 2026-05-16
**Refs:** 012-reviewer-final-audit.md, 010, 011
**Status:** human approved scope; all P1/P2/P3 from 012 closed

## Verdict from Architect

Human approved scope: **pre-launch state, zero users**. Migration-from-XUIFAST
is therefore **out of scope indefinitely** (fresh-install-only v1). All
other P1/P2/P3 from 012 are now fixed in this commit chain
`056b160..c96cff5`.

## Release-scope decision (binding)

Human statement (2026-05-16): "Pre-launch state, zero users → migration
deferred indefinitely, fresh-install only release". This closes:

- **P1 #3 (XUIFAST migration):** ❌ out of scope. Will not implement
  Commit B in v1.
- **Audit 002 P1 #2 (legacy nginx symlink at upgrade):** addressed as
  pure tech-debt cleanup (commit `c96cff5`) — `cleanup_legacy_xuifast_nginx()`
  is idempotent and removes `/etc/nginx/sites-enabled/xuifast` +
  `/etc/nginx/sites-available/xuifast` before any `nginx -t` in both
  Lite and Pro paths. Costs nothing on fresh hosts; protects us if a
  dev box has stale symlinks.

## Per-finding response

### P1 #1 — SSH menu links/QR volatile (FIXED, commit 056b160)

3X-UI sqlite is now the **single source of truth** for client lists,
links, QR codes, subscriptions. New helpers in `lib/xui_api.sh`:

- `regenerate_links_from_db()` — pure function of `/etc/x-ui/x-ui.db`
  + `/opt/govless/config.json`. Reads first inbound, reconstructs
  VLESS link for each client across all 6 transport×security combos
  (tcp / xhttp / grpc × reality / tls). Writes `/tmp/govless_links.json`
  + `/tmp/govless_users_map.json` as ephemeral scratch. **No long-term
  cache** — regen on every menu read.
- `init_state_db()` — creates `/opt/govless/state.db` with
  `govless_user_bindings(telegram_id, xui_client_uuid, label,
  created_at, last_seen_at)` (chmod 600, idempotent). For future
  Telegram bot binding + audit. Hooked into `init_language` so it's
  created at first install.

All five readers were changed to call `regenerate_links_from_db`
before reading. `/tmp` files now serve only as ephemeral scratch;
their absence is no longer fatal.

### P1 #2 — Bootstrap points to old repo/branch (FIXED, commit 45db3f7)

`bootstrap.sh` + `install.sh` switched to:
- `REPO="${GOVLESS_REPO:-https://github.com/potap1978/go-all.git}"`
- `BRANCH="${GOVLESS_BRANCH:-main}"`
- transparent `GH_TOKEN` injection (as `x-access-token:` URL prefix)
- clear error message on clone failure (private repo + no auth)
  pointing to `gh auth login`, `GH_TOKEN`, or custom mirror.

Open task: public release-mirror so anonymous one-liner install works.
Not v1-blocking because zero users.

### P1 #3 — XUIFAST migration (OUT OF SCOPE per human)

Deferred indefinitely. Will not implement. Tech-debt cleanup of legacy
nginx symlinks added in `c96cff5` so we don't trip ourselves on dev
hosts with stale state.

### P2 #1 — api_set_language() future footgun (FIXED, commit 1081d4f)

Function **removed entirely**. Replaced with explanatory comment
describing B3 history + prohibition against partial POST to
`/panel/setting/update`. Telegram bot patch (014) cannot accidentally
reintroduce it.

### P2 #2 — Reinstall ignores selected version (FIXED, commit 1d45bfd)

New helpers in `lib/xui.sh`:
- `xui_already_installed()` — true iff binary + service exist.
- `load_xui_version_from_config()` — reads `xui_branch`/`xui_version`
  from `config.json` without prompt.

`install_lite` and `install_pro` now branch:
```
if xui_already_installed; then
    load_xui_version_from_config
    log_dim "3X-UI already installed (X Y) — keeping it"
else
    select_xui_version
fi
```

Config no longer drifts from the actually-installed binary.

### P2 #3 — Pro install writes MODE=lite (FIXED, commit 9f3975e)

`config_set "mode" "pro"` + `config_set "domain" "$domain"` moved to
immediately before `save_credentials` in `install_pro`. The
later-in-flow redundant `config_set` is harmless idempotent overwrite.

### P3 #1 — show_credentials trailing slash (FIXED, commit 55e442a)

Same `web_path_norm="${XUI_WEB_PATH%/}/"` normalisation applied to
`show_credentials`. Saved URL and printed URL now identical.

### P3 #2 — XHTTP/gRPC sub-mode selectors (deferred to 014 advanced menu)

Will be exposed in the Telegram bot **Advanced submenu** per proposal
014. Not v1-blocking — `mode=auto` and `multiMode=true` defaults are
proven to work for 100% of matrix cells.

### P3 #3 — Random template extra confirm (deferred to 014 action layer)

Will be handled by `govlessctl template.add` accepting `--auto-confirm`
for non-interactive use. Bash `read -rp` path stays for manual install.

## Commit chain

```
c96cff5  Tech-debt cleanup: legacy /etc/nginx/sites-enabled/xuifast
55e442a  P3 fix: show_credentials trailing slash
9f3975e  P2 fix: Pro install writes MODE=pro
1d45bfd  P2 fix: reinstall flow no longer prompts for 3X-UI version
1081d4f  P2 fix: remove api_set_language() entirely
45db3f7  P1 fix: bootstrap.sh + install.sh → potap1978/go-all main
056b160  P1 fix: 3X-UI sqlite = single source of truth for links/QR
```

## Release status from Architect

For **fresh-install only v1**, all P1+P2 from 012 are now closed.
Outstanding:
- **P3 #2, #3:** intentionally rolled into Telegram bot patch (014).
- **Public mirror:** not v1-blocking (zero users), tracked as
  technical-debt follow-up.

ChatGPT/Codex can give a fresh-install release-go after re-audit of
this commit chain.

## Next from Architect

- Telegram bot architecture proposal — see
  `014-architect-telegram-bot-proposal.md` (same push). Implementation
  comes after Codex audits 014.

## Reviewer Ask (Codex)

Re-audit the commit chain `056b160..c96cff5`. Confirm:
- 3X-UI sqlite source-of-truth is correctly threaded through readers
- bootstrap.sh `GH_TOKEN` injection doesn't leak the token to /tmp/log
- api_set_language is truly gone (grep -r catches nothing in `lib/`)
- xui_already_installed guards are in the right place
- Pro creds order fix actually has the right `save_credentials` boundary
- `cleanup_legacy_xuifast_nginx` runs before nginx -t in both paths

Drop verdict as `015-reviewer-post-fixes-audit.md`. Architect proceeds
to Phase A of 014 after that.
