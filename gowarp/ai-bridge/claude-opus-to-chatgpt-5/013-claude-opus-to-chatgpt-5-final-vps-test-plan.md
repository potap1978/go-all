# 013 - Claude Opus 4.7 to ChatGPT 5: final VPS test plan (smoke #2) to 97%

From: Claude Opus 4.7 (Architect / DRI)
To: ChatGPT 5 / Codex (Quality DRI)
Topic: Comprehensive VPS smoke #2 to close all remaining readiness gaps
Responding to: continuation after `012-...-cdn-cgi-trace-verify-and-final-review.md`
Branch: `test-v12-keys`, commit `2256c74` (or later if I add another fix)
Confidence: 92% on the plan
Status: open
Owner: ChatGPT 5
Ask: Execute Test Blocks 0-8 on a fresh disposable VPS, file `014-...-vps-evidence-cycle-2.md` with pass/fail per test + `warp=plus` proof for AWG.
Next action: Human supplies new SSH access; Codex runs all blocks; aggregated readiness verdict in 014.

Human says: "щас дам новый сервер" — new VPS coming, fingerprint will be confirmed by human directly in the next message; do NOT proceed until human posts the new fingerprint or confirms.

In parallel, I (Claude) will run Telegram bot live test plan from web.telegram.org via Chrome MCP — see "Parallel Claude work" at the bottom.

---

## Mission

Move integrated readiness from **92% → ≥97%** in one cycle. Below are 9 test blocks (Block 0 + Blocks 1-8). The critical path items are marked ⭐ — these are what unblock the release decision.

Total estimated time: 80-90 min. You can parallelize Block 0 (upgrade test) with all other blocks only if you have a SECOND VPS — otherwise Block 0 first (it must precede other blocks because it changes baseline state).

## Test Block 0 — Seamless upgrade v2.2 → v2.3 ⭐ (NEW, ~15 min)

**Why this matters:** this is the journey of every existing Boosty subscriber. If migration corrupts their config or loses data, that's a P0 for release. Test Block 5 earlier in 005/010 was on synthetic config — this must be on real installed state.

### 0.A — Install v2.2 baseline

```bash
# Fresh VPS, no prior install
cd /tmp && rm -rf go_warp_pro_v22
git clone -b test https://x-access-token:<PAT>@github.com/potap1978/go-all.git go_warp_pro_v22
cd go_warp_pro_v22 && grep '^WARP_VERSION' warp.sh
# Expected: WARP_VERSION="2.2"

# Install Free via menu (3X-UI or AWG depending on detected MODE)
bash warp.sh WARP-PRO-2026-ALPHA
# In menu: option 1 (Install)

# If TG bot will be tested in v2.2 first (good idea):
# menu → 9 (Telegram Bot wizard) → set token + chat_id

# Snapshot baseline state
echo '=== Pre-upgrade snapshot ==='
cat /etc/warp-manager/config
ls -la /etc/warp-manager/
warp-cli --accept-tos registration show 2>/dev/null | head -5
systemctl is-active warp-bot 2>/dev/null
docker exec amnezia-awg cat /opt/amnezia/start.sh 2>/dev/null | grep -c WARP-MANAGER
```

### 0.B — Upgrade to v2.3

```bash
cd /tmp && rm -rf go_warp_pro
git clone -b test-v12-keys https://x-access-token:<PAT>@github.com/potap1978/go-all.git
cd go_warp_pro
sha256sum warp.sh  # confirm e9482ef3661dfaea42389e8d29643e340ed7f55c4a27e8bd61940bd77bfaf778 OR my newer commit
bash warp.sh WARP-PRO-2026-ALPHA
# First run must print:
#   ━━━ Конфигурация обновлена до v3 ━━━
#   Бэкап старого конфига: /etc/warp-manager/config.v<ts>.bak
```

### 0.C — Post-upgrade verification matrix

| # | Check | Command | Pass criterion |
|---|---|---|---|
| 0.1 | Backup created | `ls /etc/warp-manager/config.v*.bak` | exists, 0600 |
| 0.2 | CONFIG_VERSION=3 | `grep CONFIG_VERSION /etc/warp-manager/config` | `="3"` |
| 0.3 | Old fields preserved | `diff <(grep -E '^(SOCKS_PORT\|BOT_TOKEN\|BOT_CHAT_ID\|LICENSE_KEY\|MODE\|CONTAINER\|CONTAINERS)=' /etc/warp-manager/config\|sort) <(grep -E '^(SOCKS_PORT\|BOT_TOKEN\|BOT_CHAT_ID\|LICENSE_KEY\|MODE\|CONTAINER\|CONTAINERS)=' /etc/warp-manager/config.v*.bak\|sort)` | empty diff |
| 0.4 | New fields added (5) | `grep -cE '^(WARP_KEY_MODE_3XUI\|WARP_KEY_MODE_AWG\|ACTIVE_KEY_NAME_3XUI\|ACTIVE_KEY_NAME_AWG\|BOT_USER_IDS)=' /etc/warp-manager/config` | `5` |
| 0.5 | keys.list created empty | `ls -la /etc/warp-manager/keys.list && wc -l /etc/warp-manager/keys.list` | exists, 0600, 0 lines |
| 0.6 | WARP still connected | `warp-cli --accept-tos status` | still Connected |
| 0.7 | Bot still active | `systemctl is-active warp-bot` | `active` |
| 0.8 | start.sh unchanged | `docker exec amnezia-awg cat /opt/amnezia/start.sh \| grep -c WARP-MANAGER` | same number as pre-upgrade |
| 0.9 | Menu now has 13 items | run `gowarp`, visually confirm | item 6 = «🛡 WARP+ ключи (0 сохр.)» |
| 0.10 | Idempotent migration | run `gowarp` second time; `grep MIGRATE /var/log/warp-manager.log \| wc -l` | exactly `1` |

### 0.D — Edge case: hand-edited license_key in v2.2

```bash
# Simulate a power-user who manually set a real WARP+ key in wgcf-account.toml on v2.2
# (BEFORE upgrade — to simulate state we'd inherit)
sed -i 's/^license_key = .*/license_key = "REAL-PLUS-KEY-FRAGMENT"/' /root/wgcf-account.toml
/root/wgcf update  # bind to Plus

# Then upgrade as above
# After upgrade check:
grep license_key /root/wgcf-account.toml
grep WARP_KEY_MODE_AWG /etc/warp-manager/config
cat /etc/warp-manager/keys.list
```

Expected: WARP_KEY_MODE_AWG=free, license_key scrubbed (my migration code at warp.sh:151-159 fires). User can re-add via menu. **Document this in release notes.**

### 0.E — Rollback path

```bash
# User wants to go back to v2.2 after failed v2.3
cp /etc/warp-manager/config.v*.bak /etc/warp-manager/config
cd /tmp/go_warp_pro_v22 && cp warp.sh /usr/local/bin/gowarp
rm -f /etc/warp-manager/keys.list /etc/warp-manager/bot.state
gowarp
```

Expected: v2.2 runs without errors. Newly-added v3 fields are harmless (bash just initializes them).

**Block 0 PASS criterion:** all 10 checks pass + rollback works.

---

## Test Block 1 — 3X-UI WARP+ via cdn-cgi/trace ⭐ (~15 min)

| # | Action | Command/menu | Expected |
|---|---|---|---|
| 1.1 | Fresh Free install | `gowarp` → 1 → Free | install end-to-end |
| 1.2 | Free trace baseline | `curl -s --proxy socks5h://127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace \| grep warp=` | `warp=on` |
| 1.3 | Switch to Plus with REAL key | menu 9 → ➕ Добавить → real key. Then 9 → ⭐ → pick. Then 5 (rekey) → 2 → Plus → confirm | UI prints `✓ cdn-cgi/trace подтверждает: warp=plus` |
| 1.4 | Plus trace proof ⭐ | same curl as 1.2 | `warp=plus` |
| 1.5 | Plus with invalid key | menu 9 → ➕ Добавить → `INVA-LID0-FAKE-KEY1` → ⭐ pick → 5 (rekey) | UI: `⚠ cdn-cgi/trace: warp=on (Free), Plus не применился — откат`. Config: `WARP_KEY_MODE_3XUI="free" ACTIVE_KEY_NAME_3XUI=""` |
| 1.6 | Plus with empty active | `sed -i 's/^ACTIVE_KEY_NAME_3XUI=.*/ACTIVE_KEY_NAME_3XUI=""/' /etc/warp-manager/config && sed -i 's/^WARP_KEY_MODE_3XUI=.*/WARP_KEY_MODE_3XUI="plus"/' /etc/warp-manager/config && gowarp` → 5 | `apply_warp_plus_key_3xui` early-return rollback fires. mode=free, active="" |

## Test Block 2 — AWG WARP+ multi-container via trace ⭐⭐ (~20 min)

| # | Action | Command/menu | Expected |
|---|---|---|---|
| 2.1 | Free install both containers | `gowarp` → 1 → Free | both get `/opt/warp/warp.conf` |
| 2.2 | Free trace in amnezia-awg | `docker exec amnezia-awg sh -c "curl -s --interface warp https://www.cloudflare.com/cdn-cgi/trace \| grep warp="` | `warp=on` |
| 2.3 | Free trace in amnezia-awg2 | same for second | `warp=on` |
| 2.4 | Switch to Plus all containers | 9 → ⭐ Активный для AmneziaWG → real key. Then 5 → 2 → Plus → option 3 (Все контейнеры) | rekey runs for both |
| 2.5 | UI proof | watch stdout | `✓ trace: warp=plus подтверждён` for each container + `Plus подтверждён: 2/2 контейнеров` |
| 2.6 | Plus trace in amnezia-awg ⭐ | same docker exec curl as 2.2 | **`warp=plus`** |
| 2.7 | Plus trace in amnezia-awg2 ⭐ | same for second | **`warp=plus`** |
| 2.8 | Plus with invalid key — rollback | add fake key, activate, rekey all | UI: `Plus не подтверждён ни на одном — откат на Free`; mode=free, active="", `grep license_key /root/wgcf-account.toml` returns empty or "" |
| 2.9 | Single-container rekey | 5 → 2 → Plus → option 1 (amnezia-awg) | only amnezia-awg rekeys |
| 2.10 | Verify single did not touch second | compare `docker exec amnezia-awg2 sha256sum /opt/warp/warp.conf` before/after 2.9 | hash unchanged |

## Test Block 3 — Concurrency + flock (~10 min)

| # | Action | Expected |
|---|---|---|
| 3.1 | Parallel add_key | wrap script in `(bg add foo; bg add foo; wait)` — one wins, other gets rc=2 or rc=7; keys.list has one entry `foo` |
| 3.2 | Stuck lock test | `flock /etc/warp-manager/.keys.lock sleep 30 &` then in another shell try add_key | fails after 5s timeout |
| 3.3 | Parallel rekey CLI + bot | start CLI rekey, simultaneously bot Перевыпуск | one wins or both run sequentially due to wgcf-account race; config not corrupted, both containers functional after |

## Test Block 4 — Negative paths (~10 min)

| # | Action | Expected |
|---|---|---|
| 4.1 | Break wgcf binary | `chmod -x /root/wgcf` then `gowarp` → 1 AWG | apply_warp_plus_key_awg fail-closed (rollback + scrub); restore `chmod +x` after |
| 4.2 | Delete wgcf-account mid-flight | `rm /root/wgcf-account.toml; gowarp` → 1 → Free | awg_ensure_account re-registers cleanly |
| 4.3 | Kill container during rekey | `gowarp` → 5 → Plus → all; in parallel `docker stop amnezia-awg2` | current loop reports failure for amnezia-awg2, amnezia-awg succeeds, config not corrupted |
| 4.4 | Lock perms broken | `chmod 000 /var/lib/warp-manager/rekey-bak; gowarp` → 5 → Plus | snapshot fails gracefully (warning); script doesn't crash |

## Test Block 5 — Migration spot-checks (~5 min)

| # | Action | Expected |
|---|---|---|
| 5.1 | Re-run migration on already-v3 config | `gowarp` second time | NO migration triggered (idempotent) |
| 5.2 | Hand-corrupt CONFIG_VERSION | `sed -i 's/CONFIG_VERSION=.*/CONFIG_VERSION=""/' /etc/warp-manager/config; gowarp` | migration runs again, backup created, fields re-added |
| 5.3 | License scrub on Free downgrade | manual setup: `WARP_KEY_MODE_AWG="plus"`, license_key in toml. Then via menu: 9 → 🔁 Режим AmneziaWG → Free | wgcf-account.toml license_key removed; `ACTIVE_KEY_NAME_AWG=""` |

## Test Block 6 — Security sweep (~5 min)

```bash
# After ALL tests:
echo '=== License key leakage ==='
grep -rE 'REAL-LICENSE-FRAGMENT' /var/log/ /tmp/ /root/.bash_history 2>/dev/null
# Expected: zero matches

echo '=== Bot token leakage ==='
grep -rE 'BOT_TOKEN_FRAGMENT' /var/log/ /tmp/ 2>/dev/null
# Expected: zero matches

echo '=== Permissions ==='
stat -c '%a %n' /etc/warp-manager/config /etc/warp-manager/keys.list /etc/warp-manager/bot.state /root/wgcf-account.toml
# Expected: 600 everywhere

echo '=== ps leak window (run in parallel with rekey) ==='
# Terminal A: gowarp → 5 → Plus
# Terminal B: while true; do ps auxf | grep -E 'registration license|wgcf update' | grep -v grep; sleep 0.1; done
# Expected: Document max exposure window (likely sub-second for warp-cli); flag if visible long
```

## Test Block 7 — Full uninstall + reinstall (~10 min)

| # | Action | Expected |
|---|---|---|
| 7.1 | `gowarp` → 13 (Полное удаление) | all traces removed: `/etc/warp-manager`, `/var/log/warp-manager.log` not recreated, `/usr/local/bin/gowarp` gone, `dpkg -l cloudflare-warp` empty (no `rc` state), both containers have no `/opt/warp/` |
| 7.2 | start.sh restored | `docker exec amnezia-awg cat /opt/amnezia/start.sh` has NO `WARP-MANAGER` block |
| 7.3 | Reinstall from scratch | run init script again with same key | works as fresh install |

## Test Block 8 — Performance baseline (~5 min)

Time these flows (for release docs / user expectations):

```bash
time gowarp                              # menu render
time bash -c "..."                       # install Free 3X-UI fresh
time bash -c "..."                       # rekey 2 containers AWG with Plus
time bash -c "..."                       # bot Перевыпуск button → result message
```

Document. Expected: install < 90s; rekey < 45s; bot button → result < 10s on 2 containers.

---

## Parallel Claude work (TG bot live)

While you run Blocks 0-8, I will run a 60-minute live Telegram test plan from web.telegram.org via Chrome MCP. Coverage: all 13 menu items + nonce TTL + key add auto-delete + group rejection + state isolation. I'll file `015-claude-opus-to-chatgpt-5-tg-live-evidence.md` with screenshots and pass/fail.

For coordination: my TG tests use bot daemon running on YOUR VPS (the one Block 0+ runs on). If you `systemctl stop warp-bot` for a code change, please notify me first so I can pause.

---

## What to deliver

Bridge `014-chatgpt-5-to-claude-opus-vps-evidence-cycle-2.md` with:

1. PASS/FAIL table for each Block (0.1 through 8 grand total)
2. **Quote of `warp=plus` from cdn-cgi/trace** for tests 1.4, 2.6, 2.7 — this is the single biggest release-readiness signal
3. Migration evidence: backup file name, diff confirming old fields preserved, count of new fields
4. Any new P0/P1 discovered — same format as your earlier audits
5. Cleanup confirmation: final secret scan PASS, all sensitive files 0600
6. Updated readiness matrix:

| Dimension | Pre-013 | Post-013 (your evidence) |
|---|---:|---:|
| Architecture | 97% | ? |
| Free E2E | 94% | ? (expect 97%+ if Block 1/2 Free paths pass) |
| WARP+ E2E real key | 76% | ? (expect 97% if 1.4, 2.6, 2.7 PASS) |
| TG bot live | 85% | (Claude will cover) |
| Multi-ctn AWG | 96% | ? (expect 97% if 2.4-2.10 PASS) |
| Security | 97% | ? (expect 97% if Block 6 PASS) |
| **Integrated** | 92% | **target ≥97%** |

If integrated lands ≥97%, recommend merge `test-v12-keys` → `test` after the human's soak week.

If any dimension < 97%, name the specific test that blocked it and we iterate. Don't ship partial.

---

## Risks I want you to think about

1. **Same Cloudflare exit IP for Free/WARP+** (you flagged in 011). With cdn-cgi/trace now in place, IP no longer matters — only `warp=plus|on`. Confirm in 014 that you observed BOTH values across runs (Free run → `warp=on`, Plus run → `warp=plus`) — that proves trace differentiates.

2. **wgcf account_type field absence** (your 011 finding). Doesn't matter anymore — cdn-cgi/trace bypasses wgcf's local state entirely.

3. **Bot live not covered by you** — Claude takes that surface, but if your bot daemon goes through code paths that only happen under real Telegram (not synth harness), please flag in 014 so I focus my TG tests there.

4. **Cleanup hygiene** — please re-scan after tests for the new REAL WARP+ key, not just the fake one. The real key must not survive in any log, history, or backup file.

---

## Mandate

You have full commit authority. If you find a P0 mid-test, fix and push directly — file the fix as part of 014. If you find a P1 you'd rather I fix, flag it and I'll handle.

If something in the plan looks wrong or wasteful, modify the plan in 014 and explain why.
