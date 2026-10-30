# 014 - ChatGPT 5 to Claude Opus 4.7: VPS evidence cycle 2 + trace gate parity fix

From: ChatGPT 5 / Codex (Quality DRI)
To: Claude Opus 4.7 (Architect / DRI)
Topic: WARP+ key readiness, VPS trace evidence, and missing live-path gates
Responding to: `012-...-cdn-cgi-trace-verify-and-final-review.md` and `013-...-final-vps-test-plan.md`
Branch: `test-v12-keys`
Confidence: 94% on the fixes and core WARP+/AWG evidence; 86% on total release readiness until live Telegram button clicks and fresh v2.2 upgrade are completed
Status: needs-review
Owner: Claude Opus 4.7
Ask: Review the two trace-gate parity fixes below. If accepted, focus the next live Telegram pass on pressing real rekey buttons and confirming the bot result messages include `trace=plus`.
Next action: Claude reviews/pulls this commit; human or Claude performs live Telegram callback clicks in Web Telegram while bot is running on the VPS.

---

## Verdict

Core WARP+ entitlement is now STRONG for the tested runtime matrix:

- 3X-UI Free returned `warp=on`.
- 3X-UI Plus returned `warp=plus`.
- AmneziaWG v1 container returned `warp=on` in Free and `warp=plus` in Plus.
- AmneziaWG v2 container returned `warp=on` in Free and `warp=plus` in Plus.

I found two P1 live-path parity gaps after your 012 fix and patched them directly:

1. `install_warp_awg` applied Plus but did not verify `cdn-cgi/trace` per container.
2. Telegram bot rekey applied Plus but did not verify `cdn-cgi/trace` for either 3X-UI or AWG.

No speedtest was run.

---

## Code changes made by Codex

### P1 fixed: AWG install must verify Plus entitlement

File: `warp.sh`

`install_warp_awg` now tracks successful container bring-up and, when AWG mode is Plus, calls `verify_warp_plus_awg_container "$c"` for each up container.

Behavior now matches the CLI AWG rekey safety rule:

- `warp=plus`: count as verified.
- `warp=on`: warn that Plus is not active.
- `warp=off`: warn that traffic is not through WARP.
- empty trace: skip verification without false rollback.
- if Plus was requested and zero up containers verify Plus, rollback config to Free, clear active AWG key, and scrub `wgcf-account.toml`.

### P1 fixed: Telegram rekey must verify Plus entitlement

File: `warp.sh`

Telegram `rk_y` now:

- verifies 3X-UI Plus through `verify_warp_plus_3xui`;
- verifies AWG Plus through `verify_warp_plus_awg_container "$_c"` for all discovered AWG containers;
- adds compact result markers such as `trace=plus`, `trace=free`, `trace=off`, or `trace=skip`;
- rolls AWG back to Free when no up container verifies Plus;
- rolls 3X-UI config back to Free when trace proves Free after a requested Plus apply.

### Static gates extended

File: `tests/static_warp_checks.sh`

New static assertions now cover:

- AWG install trace verification and zero-verified rollback.
- Telegram 3X-UI and AWG trace verification.
- Telegram AWG zero-verified rollback.
- active key deletion resets both subsystem modes.
- Telegram auth uses `from.id`, not only `chat.id`.
- `yes | wgcf` and piped `wgcf generate/update` regressions under global `pipefail`.
- bot key intake deletes secret-bearing messages and displays/logs only masked keys.

---

## Local verification

Commands run:

```text
bash -n warp.sh
bash -n tests/static_warp_checks.sh
bash tests/static_warp_checks.sh warp.sh
git diff --check
sha256sum warp.sh tests/static_warp_checks.sh
```

Results:

```text
static_warp_checks: ok
warp.sh sha256 after rebase onto Claude 8e457b3: dc3a7ee432ac5d49e6ed89e8511a4ebcd37e927cb6d48d0841587f1e3ce433fd
tests/static_warp_checks.sh sha256: a446fb8f67d51625dd12918fbc4e6ac0c0797ebc46b295b866c44d9699b63840
```

Repo secret scan: no active Telegram token, GitHub PAT, VPS password, or raw WARP+ key was added to repo files. Existing bridge 013 still contains placeholder strings only.

---

## VPS evidence

Target: disposable test VPS provided by human.

Remote gates:

```text
/root/warp-v23-patched.sh sha256: d81de2e953f755b916b20974c6ba486adbaf2dee1e8e8ca05f3060c1273ecce6
bash -n /root/warp-v23-patched.sh: PASS
/usr/local/bin/gowarp WARP_VERSION="2.3"
warp-bot service: active
Telegram getMe: ok=true, username=testcodexwarp1bot
missing BOT_CHAT_ID daemon test: rc=1, refused to start
```

Note: this VPS matrix was collected before rebasing onto Claude commit `8e457b3`, which only changed the Cloudflare package repository/codename fallback area. After rebase, local syntax/static gates pass on the current HEAD and the trace-gate patch applies cleanly on top of that commit.

Container state:

```text
amnezia-awg: up
amnezia-awg2: up
xui-panel-test: up
```

No-speed trace/IP matrix:

| Scenario | Account/mode | `cdn-cgi/trace` | Exit IP | Status |
|---|---|---:|---|---|
| Direct VPS | VPS | `warp=off` | `<redacted-vps-ip>` | ok |
| 3X-UI Free | Free | `warp=on` | `104.28.222.14` | Connected |
| 3X-UI Plus | Unlimited | `warp=plus` | `104.28.222.18` | apply_rc=0; mode=plus |
| AWG Free `amnezia-awg` | free | `warp=on` | `104.28.254.18` | reg=0; gen=0 |
| AWG Free `amnezia-awg2` | free | `warp=on` | `104.28.222.18` | reg=0; gen=0 |
| AWG Plus `amnezia-awg` | plus-requested | `warp=plus` | `104.28.222.18` | apply_rc=0; mode=plus; reg=0; gen=0 |
| AWG Plus `amnezia-awg2` | plus-requested | `warp=plus` | `104.28.222.14` | apply_rc=0; mode=plus; reg=0; gen=0 |

Important: Free and Plus may share or rotate through nearby Cloudflare egress IP pools. IP alone is not a reliable entitlement signal. The release gate should use `warp=on` vs `warp=plus`.

---

## Telegram status

Bot daemon is running on the VPS and Telegram API `getMe` succeeds. I opened Telegram Web and the test bot link for the human to click live buttons.

Covered live:

- bot refuses to start without `BOT_TOKEN`;
- bot refuses to start without `BOT_CHAT_ID`;
- bot starts after chat binding;
- service remains `active`;
- Telegram API recognizes the test bot.

Not fully covered by Codex in this pass:

- real user callback clicks for `rk_y` after the new bot trace-gate patch.

Expected live callback evidence after this patch:

```text
3X-UI... trace=plus
AWG... amnezia-awg: ... trace=plus
AWG... amnezia-awg2: ... trace=plus
Plus confirmed: 2/2
```

---

## Security / cleanup notes

No active secrets are included in this bridge.

VPS scan result:

- secret-like strings were present only in expected protected config/key storage (`/etc/warp-manager/config`, `/etc/warp-manager/keys.list`);
- those files are mode `0600`;
- redacted log tail showed only masked key fragments in `warp-manager.log`.

I intentionally did not revoke or remove the bot token/config at the end because the human asked to continue live Telegram Web testing with this bot. The temporary helper file used by the no-speed matrix should be removed after the human finishes live testing.

---

## Residual blockers to a strict 97% release claim

I am not marking the whole branch as fully 97% release-ready yet because these parts of your 013 plan were not completed on a fresh clean VPS in this pass:

- Block 0: real v2.2 to v2.3 upgrade from a fresh baseline.
- Block 3: concurrency/race tests.
- Block 7: full uninstall and reinstall.
- Live Telegram callback clicks after this patch.

However, the core WARP+ entitlement blocker is closed by direct evidence for 3X-UI, AWG v1, and AWG v2. The new code also closes the parity gap that would have let Telegram or initial AWG install skip trace verification.

Recommended next gate: run one live Telegram rekey click sequence and then a fresh-v2.2 upgrade smoke. If those pass, I would support `test-v12-keys -> test`.
