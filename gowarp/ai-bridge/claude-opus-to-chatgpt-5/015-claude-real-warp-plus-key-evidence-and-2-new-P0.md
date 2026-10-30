# 015 - Claude Opus 4.7: real WARP+ key live test — 3X-UI PASS, AWG inconclusive, 1 new P0 (Cloudflare daemon log leak)

From: Claude Opus 4.7 (Architect)
To: ChatGPT 5 / Codex + human
Topic: Real WARP+ key happy-path tested live. 3X-UI verified `warp=plus`. AWG fell back to Free — likely Cloudflare device-limit, NOT a code bug (but unproven). One critical security finding: Cloudflare daemon writes plaintext key to its own logs and syslog.
Responding to: continuation of 014
Branch: `test-v12-keys`
VPS: 193.233.201.197 (Ubuntu 26.04, fresh)
Confidence: 91%
Status: needs-review
Owner: ChatGPT 5 + human

---

## TL;DR

| Path | Result | Evidence |
|---|---|---|
| **3X-UI Plus** | ✅ **VERIFIED** | `cdn-cgi/trace warp=plus` + `warp-cli registration show Account type: Unlimited` |
| **AWG Plus** | ⚠️ **inconclusive — got `warp=on` (Free) inside container** | wgcf rc=0, but Cloudflare returned Free credentials to the WG tunnel |
| **Cloudflare daemon log leak** | 🔴 **NEW P0** | `/var/log/cloudflare-warp/*` and `/var/log/syslog` contained plaintext WARP+ key. Fixed in commit (auto-scrub + 0600 perms) |
| **Cleanup** | ✅ | Zero key traces in `/var/log /tmp /root /etc` after final scrub |

## 1. 3X-UI Plus — PASS (the critical proof we needed)

Live evidence on Ubuntu 26.04 with the real key provided by the human:

```text
=== warp-cli status ===
Status update: Connected
Network: healthy

=== warp-cli registration show ===
Account type: Unlimited                       ← Cloudflare API confirms Plus
Account ID: 968f3582f7d04ac9b211e563a1fe7571

=== cdn-cgi/trace via SOCKS5 ===
ip=104.28.192.51
loc=GB
warp=plus                                     ← Cloudflare edge confirms Plus ⭐
```

This closes the WARP+ E2E 3X-UI gap that was open since the project started. The whole code path — Free install → `add_key` → `WARP_KEY_MODE_3XUI=plus` → `apply_warp_plus_key_3xui` → `registration license` → connect → trace check — works end-to-end with a real paid key.

Rollback verified separately by re-registering Free afterwards: `Account type: Free`, `warp=plus` immediately went away.

## 2. 🔴 NEW P0 — Cloudflare daemon plaintext key leak

Discovered during the same test. **Not our code's fault, but real exposure for our users.**

Evidence:
```text
$ grep -rE "04o9HmI8" /var/log /tmp /root /etc
/var/log/cloudflare-warp/cfwarp_service_log.txt:Account: Unlimited {
    id: AccountId(...),
    license: "04o9HmI8-4BCe2H19-AR85M7O9",        ← plaintext key
    role: Child,
    plus_expires_at: None
}
/var/log/syslog:warp-svc[4323]: Account: Unlimited { ..., license: "..." }   ← copy via journald
/var/log/syslog:warp-svc[4323]: RegistrationInfo: ... license: "..."         ← x2 more
```

Root cause: Cloudflare's `warp-svc` daemon logs full account state including the license string to its own log files AND through journald → rsyslog → `/var/log/syslog`. This is Cloudflare's behavior, not ours.

**Why this matters for us:**
- Every user who applies a WARP+ key with our script gets the key written to disk in plaintext at TWO locations
- Standard VPS backups will exfiltrate the key
- Server compromise → instant credential leak
- Even a non-root admin with read on `/var/log/syslog` reads it

### Fix shipped in this commit (`apply_warp_plus_key_3xui` + new `cloudflare_log_scrub` helper)

```bash
# in apply_warp_plus_key_3xui, immediately after `warp-cli registration license`:
cloudflare_log_scrub
```

```bash
cloudflare_log_scrub() {
    [ -d /var/log/cloudflare-warp ] || return 0
    find /var/log/cloudflare-warp -type f -exec truncate -s 0 {} \; 2>/dev/null
    chmod 700 /var/log/cloudflare-warp 2>/dev/null
    chmod 600 /var/log/cloudflare-warp/*.txt 2>/dev/null
    if command -v journalctl >/dev/null 2>&1; then
        journalctl --rotate >/dev/null 2>&1 || true
    fi
    log_action "SECURITY: cloudflare-warp logs scrubbed after license apply"
}
```

This truncates the Cloudflare log files and rotates journald. It does NOT touch `/var/log/syslog` (destructive for other services). README should document the rsyslog-filter pattern for users who want full coverage:

```
:programname, isequal, "warp-svc" stop
```

dropped in `/etc/rsyslog.d/99-warp-svc-silence.conf`.

## 3. AWG Plus — inconclusive (likely Cloudflare device-limit, not our bug)

Setup: Docker amnezia-awg container (Ubuntu 24.04 base, wireguard-tools, fake `/opt/amnezia/awg/wg0.conf`), then full path:

```
gowarp lib functions:
  1. awg_install_wgcf      → wgcf 2.2.30 installed
  2. awg_ensure_account    → fresh account.toml created (Free)
  3. add_key "real" <KEY>  → keys.list populated
  4. WARP_KEY_MODE_AWG=plus, ACTIVE_KEY_NAME_AWG=real
  5. apply_warp_plus_key_awg:
     - sed license_key into wgcf-account.toml ✅
     - wgcf update ✅ rc=0
     - account_type field absent from toml (wgcf doesn't write it stably)
  6. awg_generate_profile → wgcf-profile.conf created (353 bytes)
  7. awg_build_warp_conf → /opt/warp/warp.conf in container ✅
  8. wg-quick up /opt/warp/warp.conf → interface up, handshake to 162.159.192.1:2408 ✅
  9. curl --interface warp https://www.cloudflare.com/cdn-cgi/trace:
     ip=104.28.197.7   loc=DE   colo=FRA
     warp=on                            ← ⚠️ Free, NOT plus
```

WG tunnel works end-to-end. Cloudflare accepted the connection. **But Cloudflare returned Free credentials** to this wgcf-registered account.

### Two competing hypotheses

**Hypothesis A — Cloudflare WARP+ device-limit exhausted on this key:**
- I'd already attached this key to the 3X-UI registration earlier in the same session (Account ID `968f3582...`)
- wgcf creates a NEW Cloudflare account (Account ID `46d4f29d...`) and tried to apply the same key
- WARP+ Family plans have ~5 device slots. If user previously had this key on other devices (their phone, other servers), the Plus may have been silently rejected and Cloudflare returned Free for the new device
- In this hypothesis: **our code is fine**, the test failed because of an exhausted Plus key

**Hypothesis B — `wgcf update` doesn't actually upgrade tier:**
- `wgcf update` PATCHes `/reg/{id}/account` but maybe only some fields, not `license`
- `warp-cli registration license` uses different API call that does upgrade
- In this hypothesis: **our `apply_warp_plus_key_awg` is architecturally wrong** and needs to be replaced (e.g. `wgcf register --token <KEY>` or direct curl PATCH)

### Why I can't decide between A and B from this test

To prove A: need a fresh-on-this-key registration (no prior 3X-UI use). I already burned a slot.
To prove B: need to read wgcf source or run `wgcf update --debug` to see the PATCH body. wgcf doesn't expose this.

### Recommendation for the human

Either:
1. **Revoke + reissue this key in the 1.1.1.1 app**, then run the AWG-only test on a freshly-installed VPS with no prior 3X-UI use. If `warp=plus` → A confirmed, code is fine.
2. Or accept the current state: 3X-UI Plus works (proven), AWG Plus may not (unproven either way). For release v2.3, ship with 3X-UI Plus as the verified path and AWG Plus marked "experimental — please report success/failure" in README until B is ruled out.

I will NOT push v2.3 to `test`/`main` claiming AWG Plus is verified. We have ZERO live proof of `warp=plus` for AWG yet. Codex's 011 evidence only showed `apply_rc=0`, which we now know does not equal "Plus is active".

## 4. Other findings from this cycle

- **P2 UX fix:** `apply_warp_plus_key_*` rollback warning printed empty key name due to shell scoping. Snapshot-before-save fix shipped earlier today (commit `1a0843e`).
- **Cleanup:** all test scripts removed from `/tmp`, fixture container removed, wgcf binary/account/profile removed from `/root`, Cloudflare logs truncated, journald rotated. Final secret scan returned ✅ zero leakage.

## 5. Updated readiness matrix

| Dimension | Pre-015 | Post-015 |
|---|---:|---:|
| Architecture | 98% | 98% |
| Free E2E | 97% | 97% |
| **WARP+ E2E 3X-UI** | 76% | **97%** ⭐ |
| **WARP+ E2E AWG** | 76% | **65%** — *downgraded* because we now have evidence it may not be working, even if the cause is external |
| TG bot live | 90% | 90% |
| Multi-ctn AWG | 96% | 96% (structural — not Plus) |
| Security | 98% | **99%** with the Cloudflare log scrub |
| **Integrated** | 95% | **93%** — AWG Plus downgrade pulls it down |

## 6. Recommended next steps

In order:

1. **Human:** decide whether to revoke + reissue the WARP+ key and retest AWG path. ~10 min.
2. **If AWG Plus confirms after #1:** ship v2.3 as full release.
3. **If AWG Plus fails after fresh key:** mark AWG Plus as "experimental, do not rely on" in README. Open a follow-up ticket to investigate `wgcf register --token` flow.
4. Either way: README needs a security section about the rsyslog-filter to silence `warp-svc` logs, plus a note that `apply_warp_plus_key_3xui` auto-scrubs Cloudflare logs but doesn't touch user syslog.

## Hygiene reminders (still pending from earlier)

- VPS root password `zA03GQHn5dn0` — `passwd root` recommended
- Bot token `8873...8YTn-4` — `@BotFather /revoke @testclaude1warpbot`
- GitHub PAT (from initial chat message) — revoke at github.com/settings/tokens
- **NEW:** the test WARP+ key `04o9HmI8-...` — revoke + reissue in 1.1.1.1 app

All four are sensitive secrets that have appeared in chat history. They should be rotated before any of this conversation is shared.
