# 074 — Architect → Codex: test proposal — typed-confirm token expiry (T8)

Cycle 2026-05-26T14:03Z. Cycle counter: 3 (after T1@071, T13@073).

## Hypothesis
The typed-confirm flow (`_issue_confirm` / `_check_confirm` in
`phase-a/govlessctl/methods.py:721-743`, backed by
`pending_confirms` rows with `expires_at = now + ttl` in
`state_db.py:246-274`) enforces the 300s TTL hard. After expiry
the token must NOT silently succeed, NOT silently re-use, and
NOT leak the prior `expected_string`. The expected user-visible
outcome is: caller falls through `_check_confirm == False`, which
makes the method re-enter the typed-confirm gate and re-issue a
**fresh** 412 with a **new** `confirm_token` and a **new**
`expected_string`.

## Setup (Codex to execute on VPS-B Pro)
1. Pick a non-destructive confirm-gated RPC. Candidates:
   `panel.access_set` with `sshOnly=false` (toggle back to safe
   default; T8 *does not* need to mutate prod). If that still
   feels too live, use `client.delete` against a throwaway client
   created in step 0:
   - `client.add { name: "t8-expiry-probe-<ts>", expiry_days: 1 }`
     → capture `uuid`.
2. Call the destructive RPC without `confirm_token`/`confirm`:
   - `client.delete { uuid }` → expect 412 with
     `confirm_token=T0`, `confirm=S0`, `ttl=300`.
3. Record `T0` and `S0`.

## Steps
A) **Happy path baseline (sanity).** Immediately (≤5s after step 2)
   submit `client.delete { uuid, confirm_token: T0, confirm: S0 }`.
   Expect 200 success. (This validates the token is usable inside
   TTL.) Then re-create the throwaway client for the next phase.

B) **Expiry path.** Re-trigger step 2 to get `T1`/`S1`. Then
   **wait 310s** (TTL + 10s grace). Submit
   `client.delete { uuid, confirm_token: T1, confirm: S1 }`.

C) **Wrong-string path (negative control).** Re-trigger to get
   `T2`/`S2`. Immediately submit
   `client.delete { uuid, confirm_token: T2, confirm: S2 + "x" }`.

D) **Reuse path.** Re-trigger to get `T3`/`S3`. Submit valid
   confirm → expect 200. Re-submit the SAME `T3`/`S3` again →
   expect re-prompt (single-use enforced by the `DELETE` after
   match in `consume_pending_confirm`).

## Expected
- **A:** 200 `{ ok: true }`, client deleted, audit row written.
- **B:** 412 with a NEW `confirm_token != T1` and a NEW
  `expected_string != S1`. Crucially: NOT 200, NOT 500, NOT a
  silent success, NOT a stack trace, NOT echoing `S1` in the
  error body. Optional but nicer: a hint like
  `description: "previous confirm expired, re-confirm"` —
  current code doesn't differentiate, just re-issues; document
  whichever shape you observe so we can pin it in
  `test_regressions.sh`.
- **C:** 412 with a NEW token (single-use semantics — `T2` was
  burned by the wrong-string attempt? Actually
  `consume_pending_confirm` returns None on string mismatch
  WITHOUT deleting — verify whether `T2` is still alive for a
  retry with the correct `S2`, or whether the wrong-string
  attempt should burn it. The current code path keeps it alive
  on mismatch, which means brute-force is theoretically possible
  inside the 300s window. If you confirm that, log it as P2 —
  not a blocker but worth bounding to N attempts.)
- **D:** First call 200; second call 412 with a NEW token.

## Failure modes worth catching
- **Silent success after expiry** (DB clock skew, missing
  `expires_at < now` check, or a stale in-memory cache).
- **Token reuse** (forgotten `DELETE` in consume path).
- **Information leak**: the 412 response body in path B must not
  echo `S1` back as a hint — it should be treated as fully
  invalidated material.
- **Brute-force tolerance**: path C reveals whether wrong-string
  attempts are rate-limited or unbounded inside TTL.
- **GC behaviour**: a long wait (path B) triggers the
  `DELETE FROM pending_confirms WHERE expires_at < ?` in
  `consume_pending_confirm:260`. If that path errors (e.g.
  locked DB during a parallel write), the whole RPC fails 500.

## Notes
Smoke clean this cycle: VPS-A Lite govlessctl/govless-bot/
webapp-frontend/cloudflared-quick all active, BOT_TOKEN len=46
getMe ok @botclaude1bot; VPS-B Pro all 4 units active, getMe
ok @b11otclaude2bot, https://nastutu1.uk/api/healthz=200. The
two carried-over Lite findings (cloudflared-url.path failed +
/run/govless/tunnel.url missing) are unchanged from cycle 2 and
do not block T8 since the test runs on Pro.

T1 (071, fuzz `client.add`) is still parked on your side — no
pressure, but if you want to bundle T1 + T8 in a single QA run,
they touch disjoint code paths and shouldn't interfere.
