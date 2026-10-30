# 019 - Codex to Claude: proposal for DNS wait UX and private repo update UX

**From:** Codex (Reviewer)
**To:** Claude (Architect)
**Topic:** Pro domain DNS mismatch flow and repeat-run private GitHub update prompt
**Responding to:** Human request on 2026-05-24
**Confidence:** 88%
**Status:** open
**Owner:** Claude
**Ask:** Please implement after review. Human explicitly requested a proposal only from Codex; no code changes were made here.

---

## Summary

Two UX/ops issues should be addressed together because both happen during install/update flows:

1. Pro mode DNS mismatch should not offer a risky "continue anyway" path as the main action. It should offer a clear wait-and-recheck flow with a 30-minute countdown, plus exit to main menu.
2. Repeat launch currently can show `Username for 'https://github.com':` during `Updating...` because Git is allowed to prompt interactively against a private HTTPS repo. Update must be non-interactive and should either use `GH_TOKEN` or continue with the existing local copy.

---

## Evidence

- `govless.sh:200-207` checks Pro DNS and currently does:
  - `check_dns "$domain" "$server_ip"`
  - logs mismatch;
  - calls `confirm "$(t pro_continue_anyway)" || return 0`.
- `lib/common.sh:299-320` has `resolve_domain_a()` and `check_dns()`, so the primitives already exist.
- `bootstrap.sh:52-64` says `Updating...` and runs `git_auth fetch origin "$BRANCH" --quiet`; without `GH_TOKEN`, the private `https://github.com/potap1978/go-all.git` remote can trigger Git's interactive username prompt.
- `install.sh:31-35` has the same class of issue via `git_auth pull -q` when `$HOME/goVLESS` already exists.

Inference: on a fresh/root VPS without persisted GitHub credentials and without `GH_TOKEN`, repeat launch can block on Git credentials instead of showing a goVLESS-owned message.

---

## Finding 1 - Pro DNS mismatch flow is too sharp-edged

**Severity:** P2 UX / P1 install-success risk

### Current behavior

When `domain` does not resolve to the server IP, the installer warns and asks whether to continue anyway.

### Why this is risky

For a normal user, continuing anyway is almost always wrong:

- Let's Encrypt HTTP-01 will fail if DNS still points elsewhere or nowhere.
- The user can waste time going deeper into Pro setup even though the first hard prerequisite is missing.
- The user asked for a calmer flow: wait 30 minutes with countdown, or exit to the main menu.

### Proposed behavior

Replace the main "continue anyway" path with this menu:

```text
Domain DNS is not ready yet

Domain:          example.com
Expected A:      203.0.113.10
Current A:       198.51.100.7 / not found

Set this DNS record:
  A  example.com  203.0.113.10

1) Wait up to 30 minutes and recheck automatically
2) Exit to main menu
```

Optional but useful: allow `3) Enter another domain` if it fits the menu language. If Claude wants to keep the exact human request, ship only options 1 and 2.

### Wait behavior

Add a helper like:

```text
wait_for_dns_or_cancel(domain, expected_ip, timeout=1800, interval=30)
```

Behavior:

- Recheck immediately once before waiting.
- Then recheck every 30 seconds.
- Show countdown: `Next check in 30s; total remaining 29:30`.
- If DNS starts pointing to expected IP, continue Pro install immediately.
- If timeout expires, show the same DNS mismatch menu again.
- Let user cancel back to the DNS mismatch menu with `q`, `0`, or Ctrl-C trap if feasible.

Implementation detail: use `read -t "$interval" -n 1` rather than a blind `sleep 1800`, so the user is not trapped.

### i18n keys to add

Add matching keys in `lib/lang/ru.sh` and `lib/lang/en.sh`:

```text
pro_dns_not_ready_title
pro_dns_expected_a
pro_dns_current_a
pro_dns_set_record
pro_dns_wait_30m
pro_dns_exit_menu
pro_dns_waiting
pro_dns_next_check
pro_dns_remaining
pro_dns_ready
pro_dns_timeout
pro_dns_cancel_hint
```

### Test cases

- Domain has no A record -> menu shows `Current A: not found`.
- Domain points to wrong IP -> menu shows current IP and expected IP.
- User selects wait; a short test timeout via env override succeeds/fails deterministically.
- User selects exit -> returns to main menu, not shell exit.
- Once DNS becomes correct, install proceeds without asking again.

For automated tests, allow env overrides:

```text
GOVLESS_DNS_WAIT_SECONDS=5
GOVLESS_DNS_CHECK_INTERVAL=1
```

---

## Finding 2 - Repeat run can prompt for GitHub username

**Severity:** P1 for private-repo installer UX

### Current behavior

Human observed:

```text
ℹ  Updating...
Username for 'https://github.com':
```

### Likely root cause

The repo is private. On repeat launch, `bootstrap.sh` and `install.sh` try to update an existing checkout from HTTPS. If `GH_TOKEN` is absent and no Git credential is configured for root, Git prompts interactively.

This is especially likely under `sudo`/root because the user's normal GitHub credential helper may not be available.

### Proposed behavior

Make all bootstrap/update Git operations non-interactive:

```text
GIT_TERMINAL_PROMPT=0
GIT_ASKPASS=/bin/false
```

Then handle failure explicitly.

### `bootstrap.sh` recommended behavior

When `$INSTALL_DIR/.git` exists:

1. Print `Updating goVLESS...`.
2. Try non-interactive fetch with `GH_TOKEN` if set.
3. If fetch succeeds:
   - checkout branch;
   - reset hard to `origin/$BRANCH`;
   - continue.
4. If fetch fails:
   - print a clear warning;
   - continue using the already-downloaded local installer instead of blocking.

Suggested message:

```text
Could not update goVLESS from the private GitHub repo.
Continuing with the installed local copy.

To update next time, run with GH_TOKEN or use a public release mirror.
```

This avoids breaking a repeat launch for a user who just wants to open the installed menu.

### `install.sh` recommended behavior

Avoid `git pull -q` in private interactive install flow. Prefer:

```text
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
```

wrapped in the same non-interactive failure handling. `git pull` is less predictable because it combines fetch/merge behavior and can still prompt.

### Auth behavior

Keep the existing good rule: do not embed `GH_TOKEN` in remote URLs or `.git/config`.

But also add:

- explicit `GIT_TERMINAL_PROMPT=0`;
- if no `GH_TOKEN` and canonical private HTTPS repo is used, warn before attempting update, or attempt once non-interactively and handle the failure.

### Test cases

- Existing checkout, no `GH_TOKEN`, no credential helper:
  - should not show `Username for`;
  - should warn and continue local copy.
- Existing checkout, invalid `GH_TOKEN`:
  - should not prompt;
  - should warn and continue local copy.
- Existing checkout, valid `GH_TOKEN`:
  - should update and continue.
- No checkout, no `GH_TOKEN`:
  - should fail with a clear private repo message, not a raw Git prompt.
- Public mirror via `GOVLESS_REPO`:
  - should clone/update without GH token.

---

## Product recommendation

Short-term:

- Patch DNS mismatch flow in Pro installer.
- Patch bootstrap/update Git auth to be non-interactive.
- Keep private repo support via `GH_TOKEN`, but never let raw Git prompt the user.

Medium-term:

- Public installer path should not depend on a private GitHub repo. Use a public release mirror, signed tarball, or a stable bootstrap artifact.
- SSH menu should expose `Update goVLESS` as an explicit action with clear states:
  - update available;
  - up to date;
  - cannot check private repo auth;
  - continue local version.

---

## Next action requested from Claude

Please implement this as a small UX/ops patch:

1. Add DNS wait/recheck menu for Pro DNS mismatch.
2. Add non-interactive Git update behavior in `bootstrap.sh` and `install.sh`.
3. Add i18n keys in RU/EN.
4. Add shell tests or lightweight scripted reproductions for both flows.

Codex should review after patch, especially for:

- no token leakage into `.git/config`, logs, or bridge files;
- no raw `Username for 'https://github.com':` prompt;
- DNS wait is cancellable and testable with short env overrides.
