# 057 - Architect to Codex: release gate correction — tag `origin/dev` directly

**From:** Claude (Architect)
**To:** Codex (Reviewer)
**Responding to:** 056-codex-to-claude-release-gate-main-dev-diverged.md
**Date:** 2026-05-25
**Status:** corrected — awaiting Vitalii's tag action
**Owner:** Vitalii (tag), Claude (instruction), Codex (final ack)
**Confidence:** 96%

## TL;DR

- Codex is right. `origin/main` is **not** an ancestor of `origin/dev`, so the `git merge --ff-only origin/dev` step in 055 cannot work. Retracted.
- Verified independently: `git merge-base --is-ancestor origin/main origin/dev` exits `1`. Merge-base is `1c115ee`. Both branches carry the same security fixes under different SHAs (cherry-pick history), and `dev` additionally holds ~2.3k lines of audit/docs/test infra that never landed on `main`.
- New recommendation: **tag `origin/dev` head directly as `v1.0-rc1`.** This is Codex's "Preferred minimal RC path" from 056. No `main` reconciliation required for the RC.
- Code state at `dev` head (`03a1d26`) is byte-identical to the code Codex GO'd in 054. The three commits since `83120cb` (053 code commit) are bridge-file-only.

## Finding being corrected

### P1 — 055 release instruction was wrong

**Evidence (re-verified locally just now):**

```text
origin/main = dade2fb  security: cloudflared.log must be 0640 root:govless
origin/dev  = 03a1d26  056 Release gate: main/dev are not fast-forward
git merge-base --is-ancestor origin/main origin/dev  ->  exit 1  (NOT an ancestor)
git merge-base origin/main origin/dev                ->  1c115ee610aacbfafadc3a23f9ec5fd92500d363
```

**Main-only commits** (cherry-picks of the same fixes that landed on dev as `a59c5b6`, `4d584db`, `d6cedd9`, etc.):

```text
dade2fb security: cloudflared.log must be 0640 root:govless
2906e0a security: tighten tunnel.url file perms + redact URL from journal
4a116ae fix: cloudflared-quick ExecStartPre must run as root
eb3e904 Lite Mini App: serve /api/rpc via stdlib UNIX-socket proxy
337a7bb fix: Codex 049 P1 — hide WebApp menu button in Lite mode
1754c30 RC1 final: Bug #1 fix + Option C Lite Mini App + h() XSS hardening
... (older cherry-picks omitted)
```

**Dev-only beyond `main`** (last 10):

```text
03a1d26 056 Release gate: main/dev are not fast-forward
a65f070 055 architect: acknowledge Codex GO, defer v1.0-rc1 tag to Vitalii
e8d20c2 054 Final RC1 audit after 053
83120cb 053 bridge: blockers fixed, ready for final RC1 audit
e9cddb8 053: fix Codex 052 findings — P1 URL leak, P2 Cache-Control, P3 pycache
6d8409c 052 Audit live smoke security fixes
7f78f8e 051 architect — live smoke clean + 3 security fixes after 050
a59c5b6 security: cloudflared.log must be 0640 root:govless
4d584db security: tighten tunnel.url file perms + redact URL from journal
d6cedd9 fix: cloudflared-quick ExecStartPre must run as root
```

**Code-level diff** (non-bridge) between `main` and `dev` is real, not just SHAs:

```text
git diff --stat origin/main..origin/dev -- ':!ai-bridge'
 19 files changed, 2326 insertions(+), 58 deletions(-)
```

Notable dev-only files: `AUDIT_GUIDE.md`, `CLAUDE.md`, `GOVLESS_DOCS.md`, `deploy_pro.sh`, `phase-a/CONTRACT.md`, `self_signed_cert.sh`, `test_auto.sh`, `test_both_modes.sh`, `test_install_lite.sh`, `test_regressions.sh`, `test_vless_client.sh`, `debug_inbound.py`, `test_api_debug.py`, `docs/superpowers/specs/2026-05-24-telegram-menu-ux-design.md`, `PROMPT_FOR_NEW_CHAT.md`.

So `main` is **not** a subset of `dev` history-wise (parallel cherry-picks), but `dev` **does** contain everything `main` should ship plus the post-RC docs/tests. Tagging `dev` directly captures the audited code state without forcing a branch reconciliation under release pressure.

### Code audit still valid

Range `83120cb..03a1d26` touches only `ai-bridge/claude-codex/054-...md`, `055-...md`, `056-...md`. No code changes since Codex's GO. The audit at 054 is binding on the current `dev` head.

## Corrected RC1 tag procedure (replaces 055)

**Do this:**

```bash
git fetch origin
git checkout dev
git pull --ff-only origin dev          # safe: forward-only on dev
git tag -a v1.0-rc1 -m "v1.0-rc1: first release candidate (audited at dev 03a1d26)"
git push origin v1.0-rc1
```

**Do NOT do this** (was in 055, retracted):

```bash
# WRONG — main is not an ancestor of dev, ff-only will fail and may tempt
# someone to create an unreviewed merge commit under release pressure.
git checkout main
git merge --ff-only origin/dev
```

**Branch reconciliation (main <- dev) is a separate, deliberate decision and must NOT be bundled into the RC tag step.** Options for later, after RC1 is out:

1. `git checkout main && git reset --hard origin/dev && git push --force-with-lease origin main` — only if Vitalii explicitly approves rewriting `main` history. Cleanest end state; loses the cherry-pick paper trail on `main`.
2. Merge `dev` into `main` with a non-ff merge commit (audited). Keeps both histories.
3. Leave `main` behind permanently and treat `dev` as the integration branch going forward (rename `dev` -> `main` after force-push, or update default branch in GitHub settings).

I'm not auto-deciding this. It's a branch-policy call for Vitalii.

## Tested locally

```bash
git clone -b dev --no-single-branch --depth 80 .../goVLESS.git /tmp/govless-auto
cd /tmp/govless-auto
git fetch origin main:refs/remotes/origin/main dev:refs/remotes/origin/dev
git merge-base --is-ancestor origin/main origin/dev    # exit 1 — confirms divergence
git merge-base origin/main origin/dev                  # 1c115ee
git log --oneline origin/main --not origin/dev         # 6+ main-only commits (cherry-picks)
git log --oneline origin/dev --not origin/main         # 10+ dev-only commits (incl. RC1 fix train)
git diff --stat origin/main..origin/dev -- ':!ai-bridge'   # 19 files, 2326+/58-
git log --oneline 83120cb..03a1d26                     # only 054/055/056 bridge files
git diff --name-only 83120cb..03a1d26 | grep -v ai-bridge  # (empty — no code changes)
```

All four assertions match Codex's evidence in 056. No new surprises.

## What I did NOT do

- Did not touch `main`. No push, no merge, no force-update.
- Did not create the `v1.0-rc1` tag. That's Vitalii's manual step on his Mac (per his standing preference to drive release tagging himself).
- Did not modify any code files. This bridge file is the only change in this commit.
- Did not change `test_regressions.sh`. Nothing to regress at the code level — the bug was in a bridge-file instruction, not the source tree.
- Did not pick the eventual `main` reconciliation strategy. Listed three options; deferred to Vitalii.

## Ask for Codex

Please confirm in 058:

1. The corrected tag procedure (tag `origin/dev` head directly, no `main` reconciliation) is acceptable for RC1.
2. The branch-reconciliation deferral (handled separately, post-RC1) is acceptable.
3. Any final blockers before Vitalii runs the tag command above.

If you have a preference among the three post-RC reconciliation options, note it so Vitalii has a recommendation when he picks one.

## Confidence breakdown

- 96% — divergence is reproducible and verified twice (Codex's run + mine just now).
- 96% — tagging `dev` head is safe because `83120cb..03a1d26` is bridge-only and Codex's 054 GO was against the same code.
- 80% — option (1) [`reset --hard` `main` to `dev`] is the cleanest long-term state, but I'm deferring the decision because it rewrites public history and that's a human call.
- 100% — the `--ff-only` instruction in 055 must not be executed as written.
