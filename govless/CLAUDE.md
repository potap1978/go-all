# Project context for AI assistants

## Repo
- **Name:** goVLESS
- **Visibility:** private (potap1978/go-all)
- **Roles in this project (per `coordinating-ai-duos` skill):**
  - Architect: Claude
  - Reviewer: Codex
  - Human: Vitalii (potap1978)

## Branch policy
- Default work branch: `main` (private repo, нет отдельной `test`-ветки).
- Public release flow: cherry-pick / squash-merge to the public release repo only on explicit human command, always with a GitHub Release and version bump in `lib/common.sh` (`GOVLESS_VERSION`).

## Secrets policy
- **NEVER** commit GitHub tokens, server passwords, SSH keys, or other secrets.
- For GitHub auth use `gh auth login` or environment variable `GH_TOKEN` (never inline in files).
- For VPS access use SSH keys, not password auth. Test VPS root pwd is rotated regularly; do not store it in repo.
- Local-only notes that may contain credentials must use the `.local.md` suffix (covered by `.gitignore`).

## What this project is
goVLESS — bash-installer для VLESS (3X-UI + Xray, Reality/TLS) для нетехнических пользователей.
Полная документация: `GOVLESS_DOCS.md`.

## Working with AI agents
- All cross-agent handoffs go via `ai-bridge/claude-codex/` with monotonic numbering.
- Architect (Claude) proposes, Reviewer (Codex) audits, Human approves production-touching changes.
- See `ai-bridge/README.md` for the protocol details.
