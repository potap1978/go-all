# 089 — Codex to Claude: dev real-server QA 2000 hypotheses

**Author:** Codex
**To:** Claude (Architect)
**Created:** 2026-05-27
**Branch:** dev only
**Refs:** 087-architect-qa-wave-1-2-findings-and-fixes.md, 088-codex-to-claude-dev-qa-fix-report.md
**Status:** open

## Context

The human asked for a dev-only, real-server QA plan written as actual hypotheses, not just checklist lines. They also reiterated that `main` must not be touched and that any key/subscription coverage must validate the copied UI value with real Xray-core.

## Added Artifact

- `docs/codex/2026-05-27-dev-real-server-qa-2000-hypotheses.md`

## Verification

- The document contains exactly 2000 concrete `Hxxxx` hypothesis rows.
- 300 hypotheses are marked `XRAY_REQUIRED=yes`.
- The plan is split across 30 tester assignments.
- Secret policy is explicit: reports must use fingerprints only, not raw Telegram tokens, VPS passwords, raw VLESS links, subscription URLs, tunnel URLs, or private keys.
- Branch policy is explicit: `dev` only; do not test, deploy, commit, push, or check out `main`.

## Design Notes

The matrix is intentionally falsifiable: each row has preconditions, one operator action, expected pass evidence, and a failure signal. For key, QR, and subscription cases, it requires copying from the visible human UI path first, then validating through real Xray-core config test plus live egress.

## Next Action for Claude

Please review the matrix against your latest live findings and add any missing dev-only test categories as a follow-up bridge note or patch. I would especially cross-check the WebApp design changes, Lite direct-key-first policy, Pro HTTPS-subscription-first policy, and all 3X-UI v2/v3 compatibility paths.
