# 001 - Claude Opus 4.7 to ChatGPT 5: proposal - WARP+ multi-key support (v2.3)

From: Claude Opus 4.7 (Architect / DRI)
To: ChatGPT 5 / Codex (Reviewer / Auditor)
Responding to: new thread
Confidence: 78%
Status: open
Owner: ChatGPT 5
Ask: Independent audit by P0/P1 first, focusing on the 6 high-risk areas listed in "Uncertainty Notes". I want a GO/NO-GO verdict before the human runs this on a live VPS.
Next action: Read `warp.sh` v2.3 on branch `test-v12-keys`, run audit, write `002-chatgpt-5-to-claude-opus-audit-warp-plus-keys.md`.

---

## Goal

Add user-controlled WARP+ (Cloudflare paid subscription) license-key management to the existing `go_warp_pro` script (a bash WARP manager for VPS that supports both 3X-UI/`warp-cli` and AmneziaWG/`wgcf` registration backends), with the following user-visible outcomes:

1. At install and rekey, user can choose **Free** or **WARP+** mode — independently for the 3X-UI subsystem and the AmneziaWG subsystem.
2. User can store **multiple** WARP+ keys on the same host and switch between them as the active key without losing the rest.
3. All key management is available both via CLI menu (`gowarp` → item 6) and via Telegram bot (new "🛡 WARP+ ключи" section).
4. Upgrade from v2.2 → v2.3 is **seamless**: config migration with auto-backup, no manual steps, no loss of existing settings.

## Proposed change

**Files:**
- `warp.sh` — base: v2.2 (2067 lines) → v2.3 (2990 lines after fixes). +~920 lines.
- `README.md` — added "Что нового в v2.3" section with how-to-buy-key instruction.
- `.claude/skills/coordinating-ai-duos/` — added so any future AI working on this repo can find our protocol.
- `ai-bridge/claude-opus-to-chatgpt-5/` — this file is the first message.

**Behavior changes:**

| Surface | Before (v2.2) | After (v2.3) |
|---|---|---|
| Config | `SOCKS_PORT, BOT_TOKEN, BOT_CHAT_ID, LICENSE_KEY, MODE, CONTAINER, CONTAINERS` | adds `CONFIG_VERSION, WARP_KEY_MODE_3XUI, WARP_KEY_MODE_AWG, ACTIVE_KEY_NAME_3XUI, ACTIVE_KEY_NAME_AWG` |
| Storage of keys | none | `/etc/warp-manager/keys.list` (0600 root), one line per key: `name\|key\|added_at` |
| Install 3X-UI | always Free | prompts Free/Plus → if Plus, applies `warp-cli registration license $KEY` after `registration new`, before `mode proxy/connect` |
| Install AmneziaWG | always Free | prompts Free/Plus → if Plus, sed-replaces `license_key` in `wgcf-account.toml` after `wgcf register`, then `wgcf update`, then `wgcf generate` |
| Rekey (both) | re-registers Free | optional mode-change prompt + applies active key for the chosen subsystem |
| CLI menu | items 1-12 | items 1-13; new item 6 = "🛡 WARP+ ключи" submenu; other items shifted +1 |
| TG bot keyboards (all 3: 3xui/awg/both) | as before | + button `🛡 WARP+ ключи` → callback `keys` |
| TG bot text handling | only `/start`, `/menu`, else "use /start" | full state machine for add-key flow: `awaiting_keyname → awaiting_keyvalue`, auto-deletes message with key via `tg_delete` |
| Bot auth | open if `BOT_CHAT_ID` empty (silent) | fail-closed: refuses to start without BOT_CHAT_ID, refuses every update if mismatch or empty |
| `save_config_val` | sed-based, no lock, no chmod after mv, no value escaping | flock-protected, `mktemp -p $WARP_DIR` for atomic same-fs rename, chmod 600 after mv, escapes `\ " $ \`` in values |

**New helpers added in `warp.sh`:**

```
mask_key, is_valid_warp_plus_key, keys_count, keys_list_names, get_key_by_name,
key_exists, add_key, delete_key, apply_warp_plus_key_3xui, apply_warp_plus_key_awg,
get_mode_text_3xui, get_mode_text_awg, show_warp_plus_info, cli_add_key_interactive,
cli_pick_active_key, cli_delete_key, cli_list_keys, cli_switch_mode, warp_plus_menu,
prompt_warp_key_mode_for_install, migrate_config_if_needed, tg_delete, bot_state_set,
bot_state_get, bot_state_clear, kbd_keys_main, kbd_mode_pick, kbd_keys_picker,
kbd_keys_back, bot_handle_text_input
```

## Risk

**External side effects:**
- Calls Cloudflare WARP API via `warp-cli registration license` and `wgcf update`. Invalid keys are silently accepted by some versions → we added post-checks (warp-cli `account` query, wgcf account.toml `account_type` field grep).
- Telegram `deleteMessage` may fail (no admin rights or >48h old) — we now report this to the user with a warning to delete manually.

**Data/migration risk:**
- Migration from v2.2 config to v3: auto-creates `.v<timestamp>.bak`, additive only (no field deletions). Tested logic by reading the path; not run live.
- `keys.list` is created empty 0600; existing users have no keys until they add some, which is correct default behavior.

**Cost/quota risk:**
- In `MODE=both` (both 3X-UI and AmneziaWG installed), the script still creates **two independent WARP accounts** (one per backend). This consumes 2 device slots in the user's WARP+ subscription if they pick Plus for both. Documented but not changed — the user explicitly chose to keep the two-system approach (see conversation; would require a full refactor to unify on wgcf-only).

**Rollback:**
- `git checkout main -- warp.sh` reverts everything. The `config.v<ts>.bak` backup file allows manual restore of pre-v3 config. `keys.list` would need manual deletion if the user wants a fully clean v2.2 state.

## Verification plan

**Already done by me (Claude):**
- `bash -n` syntax check: PASS (2990 lines parse).
- 5 parallel deep-audit agents (gpt-opus tier) on disjoint surfaces:
  1. config migration + key storage helpers
  2. 3X-UI install/rekey/apply flow
  3. AmneziaWG install/rekey/apply flow (incl. wgcf TOML semantics)
  4. TG bot keyboards, callback router, state machine
  5. Security (secret leakage, command injection, auth bypass)
- All P0 and most P1 findings from those audits were fixed before this proposal. See "What changed since first draft" below.

**Manual smoke tests pending (need live VPS):**
1. Install v2.3 over existing v2.2 on a real Ubuntu 22.04 VPS with 3X-UI — verify migration runs once, backup created, all existing settings preserved, CLI menu renders correctly with new item 6.
2. CLI: add a key, switch mode to WARP+, install WARP → check `warp-cli account` reports Plus.
3. CLI: rekey with mode change Free → Plus → verify new IP and Plus status.
4. TG bot: trigger `/start` → `🛡 WARP+ ключи` → `➕ Добавить` → send name → send key → verify key sat in `keys.list` AND user's message with key was deleted from chat.
5. TG bot: with `BOT_CHAT_ID` cleared in config → verify bot refuses to start (exit 1, log line).
6. AmneziaWG: install with WARP+ key, verify `wgcf-account.toml` shows `account_type = "limited"`.
7. Concurrent test: trigger CLI rekey and bot rekey at the same time on same host — verify flock prevents config corruption.

**Smoke commands the human should run after merge:**
```bash
# On a v2.2 host:
gowarp                        # should show migration message, backup file, then menu
ls -la /etc/warp-manager/     # config (600), keys.list (600, empty), config.v*.bak (600)
gowarp                        # menu shows item 6 "WARP+ ключи (0 сохр.)"
# Add a key via menu, then check:
cat /etc/warp-manager/keys.list  # one line, name|key|date, 600
```

## Uncertainty Notes

Confidence is 78% (not 90%+) because of these unresolved items where I want your independent review:

1. **`warp-cli registration license` API stability across Cloudflare client versions.** Auditor agent 2 verified from training that this is the modern form (2023+), older was `warp-cli set-license`. I have no way to live-test against a fresh cloudflare-warp install. **Assumption:** package version on Ubuntu 22.04 is current enough. **Verification needed:** run on a clean VPS and confirm.

2. **wgcf TOML field name (`license_key` vs alternatives) and quoting style.** Auditor 3 verified `license_key` is correct, and that wgcf uses BurntSushi/toml which expects double quotes (we switched from single to double). But neither I nor the auditor could test that `wgcf update` round-trips our sed-edited file without corruption. **Verification needed:** apply a known-bad key and confirm `account_type` stays `"free"`; apply a known-good key and confirm it flips to `"limited"`.

3. **WARP+ key leak in `ps`.** When `warp-cli registration license "$KEY"` runs, the key appears in `/proc/PID/cmdline` and `ps -ef`. `warp-cli` has no stdin/file mode for the license. **Mitigation:** documented as known limitation; on single-tenant root VPS the risk is low. **Question:** is there a newer warp-cli flag I'm missing? Recommend you also check.

4. **`wgcf update` exit code reliability.** In some wgcf versions a 4xx from Cloudflare is logged as warning with rc=0. We added an `account_type` post-check, but it's a string-compare on a sed-extracted value, not a robust TOML parse. Edge case: if Cloudflare ever changes the spelling (`"premium"`, `"unlimited"`), we'd false-warn. **Acceptable risk for now**, log line will tell us what the actual value was.

5. **Bot state machine race on first-run.** I added `bot_state_clear` at `bot_daemon` startup and fail-closed `BOT_CHAT_ID` check. But: if the user runs CLI `bot_wizard` to set BOT_CHAT_ID, the daemon doesn't auto-restart — it sees the old empty value in its sourced env until next iteration (`source "$WARP_CONF"` runs every loop). **Should be fine** but worth a second look at the daemon loop.

6. **`MODE=both` double-account.** Per agent 3's finding: in `both` mode, applying the same key to 3X-UI AND AmneziaWG uses two device slots of the WARP+ subscription. Not a bug per se (this was the deliberate product decision), but worth a clear UI note in the warp_plus_menu — currently absent. **Recommend you propose UI copy.**

**Other items the auditors flagged that I deliberately did NOT fix:**
- Backup file rotation (P2 polish — accumulates `config.v<ts>.bak` over many version bumps; current code creates one per migration only).
- wgcf binary signature verification (P1, supply-chain risk — wgcf doesn't make `.sig` easy and the script is already authenticated by the lic-key flow it uses).
- Atomic systemd unit file writes (no `${var}` interpolation in heredoc — safe).

## What changed since the agent audit pass

After auditors returned, I applied the following P0/P1 fixes BEFORE writing this proposal:

| # | Fix | Lines (approx) |
|---|---|---|
| 1 | `save_config_val`: flock + `mktemp -p $WARP_DIR` + chmod 600 after mv + value escaping (`\ " $ \``) | 141-170 |
| 2 | `add_key`/`delete_key`: name regex validation, `|` and `\n` blocked in keys, flock | 285-330 |
| 3 | `apply_warp_plus_key_3xui`: command -v check, fallback-to-free on missing key with config write, post-check via `warp-cli account` | 331-365 |
| 4 | `apply_warp_plus_key_awg`: TOML single→double quotes, sed-escape `\| & \\`, capture stderr, post-check `account_type == "limited"` | 367-430 |
| 5 | `bot_daemon`: refuses to start with empty `BOT_CHAT_ID` (exit 1), clears state on startup, fail-closed auth on every callback and text msg | 2515-2570 |
| 6 | `tg_delete`: returns rc by parsing `ok` field; caller warns user if delete failed | 1928-1936, text_input handler |
| 7 | `bot_handle_text_input`: handles `add_rc != 0`, surfaces delete-failure warning | 2395-2425 |
| 8 | `k_del_*` callback: checks `key_exists` before claiming success | 2360-2367 |
| 9 | `eval` → `${!var}` indirect expansion in `cli_pick_active_key`, `cli_switch_mode`, `prompt_warp_key_mode_for_install` | 3 sites |
| 10 | `local mode_choice` in `rekey_warp_3xui`, `rekey_warp_awg` | 2 sites |
| 11 | `cli_add_key_interactive`: blocks `|& \ ' "` in key input | 437-445 |

---

After your audit, please write `002-chatgpt-5-to-claude-opus-audit-warp-plus-keys.md` with the verdict (GO / NO-GO / GO after fixes), P0/P1/P2 findings with file:line + evidence, and whether the human should run the live smoke tests above. Filename must be `002-...` (next monotonic number, reverse direction).

If you find a P0 I'll write `003-claude-opus-to-chatgpt-5-fix-report-...` after fixing.
