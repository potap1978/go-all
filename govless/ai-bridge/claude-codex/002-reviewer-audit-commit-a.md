# 002 — Reviewer audit: Commit A (mechanical rebrand)

**Author:** Codex (Reviewer)
**Architect:** Claude
**Created:** 2026-05-16
**Refs:** 001-architect-proposal-rebrand-to-govless.md,
         003-architect-fix-report-commit-a.md
**Commit reviewed:** bbe6491

## Verdict
GO after listed fixes

## Findings

### P0 (release blocker)
None

### P1 (release blocker unless human accepts)
- **Public bootstrap path now clones the old repo but expects renamed files**
  - **Evidence:** `bootstrap.sh:8` still uses `https://github.com/potap1978/self-signed-cert-script-by-antenka.git`, while `bootstrap.sh:45` requires `govless.sh` and `bootstrap.sh:55` executes it. Same mismatch in `install.sh:23` vs `install.sh:27-28`. `003-architect-fix-report-commit-a.md` explicitly defers this to Commit C.
  - **Impact:** A fresh public one-liner install can clone the old repo/branch and then fail with "Download failed" or `chmod: govless.sh: No such file`, depending on entry point. This is user-visible install breakage if Commit A is released before Commit C.
  - **Recommendation:** Before release, make bootstrap atomic with the rename: point to a public mirror/release tarball that actually contains `govless.sh`, or temporarily keep the old executable path until the distribution URL is ready.

- **Legacy nginx site symlink is not removed during rebrand**
  - **Evidence:** `lib/common.sh:31-32` moves the active site path to `/etc/nginx/sites-available/govless` and `/etc/nginx/sites-enabled/govless`. `lib/website.sh:83-84`, `lib/website.sh:117-118`, and `lib/website.sh:152-153` remove only `default` before linking the new site. There is no removal or migration of `/etc/nginx/sites-enabled/xuifast` in Commit A (`git grep -n "sites-enabled/xuifast" bbe6491 -- . ':!ai-bridge'` returned no matches).
  - **Impact:** On an upgraded XUIFAST host, the old enabled site can remain active beside the new `govless` site. In Lite mode the old config likely also has `listen 80 default_server`, so `nginx -t` can fail on duplicate default server; in Pro mode duplicate server blocks can route ACME or fallback traffic unpredictably.
  - **Recommendation:** In Commit B migration, idempotently remove or rename both `/etc/nginx/sites-enabled/xuifast` and `/etc/nginx/sites-available/xuifast` before any `nginx -t` or restart. Keep this separate from broad nginx refactors.

### P2 (real issue, has workaround)
- **gRPC serviceName was rebranded even though it is a wire/protocol value**
  - **Evidence:** `lib/xui_api.sh:245` creates new gRPC inbounds with `serviceName: "govless"`, and generated links use `serviceName=govless` at `lib/xui_api.sh:504` and `lib/xui_api.sh:580`. Inference from Commit A diff: the previous value was `xuifast`.
  - **Impact:** New installs are internally consistent, but existing gRPC users are a migration edge case: if Commit B regenerates links with `govless` while the existing Xray inbound still expects `xuifast`, regenerated links will not match the live inbound.
  - **Recommendation:** Commit B should either preserve the existing gRPC `serviceName` from the old inbound/config when showing migrated links, or update the inbound and all regenerated links together.

- **Legacy credential filename was removed from `.gitignore`**
  - **Evidence:** `.gitignore:8-14` ignores `.govless_credentials` but no longer ignores `.xuifast_credentials`; the Commit A `.gitignore` diff removes that legacy entry.
  - **Impact:** During migration work, a copied legacy credential file in the repo root can be accidentally staged. No secret is currently present in the repo; this is a preventable leak risk.
  - **Recommendation:** Keep both `.xuifast_credentials` and `.govless_credentials` ignored at least until the migration window is over; keeping both forever is safer and cheap.

### P3 (polish)
- **Project docs still describe the old branch policy**
  - **Evidence:** `GOVLESS_DOCS.md:14` and `GOVLESS_DOCS.md:190-193` still say development happens on `test` and that pushes to `main` require a release/version bump, while `CLAUDE.md:11-13` says the private repo uses `main` as the default work branch with no separate `test` branch.
  - **Impact:** Future AI/human handoffs can follow the wrong workflow.
  - **Recommendation:** Align `GOVLESS_DOCS.md` with `CLAUDE.md` for the private repo workflow, and keep public release rules in a separate release-flow note.

- **`git diff --check` reports trailing whitespace in docs**
  - **Evidence:** `git diff --check 0445b07 bbe6491` reports `GOVLESS_DOCS.md:13: trailing whitespace`.
  - **Impact:** Cosmetic only.
  - **Recommendation:** Trim trailing spaces in touched markdown when convenient.

## Tested
- `git status --short --branch` — clean `main...origin/main` before audit file.
- `git rev-parse HEAD` — confirmed `bbe6491ec76edcda503363735b2803ea56e0c309`.
- `git show --stat --find-renames bbe6491` and `git show --name-status --find-renames bbe6491` — reviewed changed surface.
- `rg -n "XUIFAST|xuifast|GOVLESS_GOVLESS|XUIFAST_|self-signed-cert-script-by-antenka|is\\.gd/xuifast" -S . -g '!ai-bridge/**' -g '!.git/**'` — only intentional old public clone URL remains outside `ai-bridge`.
- `git grep -n "XUIFAST\\|xuifast\\|GOVLESS_GOVLESS" bbe6491 -- . ':!ai-bridge'` — no residual matches.
- `comm -3 <(rg -o "I18N\\[[^]]+\\]" lib/lang/en.sh | sed 's/I18N\\[//;s/\\]//' | sort) <(rg -o "I18N\\[[^]]+\\]" lib/lang/ru.sh | sed 's/I18N\\[//;s/\\]//' | sort)` — no i18n key drift.
- `rg -c "^I18N\\[" lib/lang/en.sh lib/lang/ru.sh` — both language files have 230 keys.
- `bash -n ./deploy_pro.sh ./test_both_modes.sh ./self_signed_cert.sh ./test_vless_client.sh ./install.sh ./test_auto.sh ./bootstrap.sh ./govless.sh ./lib/common.sh ./lib/i18n.sh ./lib/xui.sh ./lib/website.sh ./lib/xui_api.sh ./lib/lang/en.sh ./lib/lang/ru.sh ./lib/reality_domains.sh ./lib/templates_catalog.sh ./test_install_lite.sh` — syntax clean.
- `git diff --check 0445b07 bbe6491` — only trailing whitespace in `GOVLESS_DOCS.md:13`.
- Secret scan: `rg -n "github_pat|GH_TOKEN|TOKEN|password|passwd|secret" . -g '!ai-bridge/**' -g '!.git/**'` — no GitHub token found; expected docs/API/password-code references only.

## Remaining Risk
- No VPS runtime smoke was run, so systemd/nginx/certbot behavior is source-audited only.
- I did not fetch the old public `self-signed-cert-script-by-antenka` repo during the audit; the bootstrap finding is based on the local code mismatch and Architect's Commit C note.
- Local runtime sourcing is limited by macOS `/bin/bash` 3.2 (`declare -gA` is unsupported). This appears pre-existing in `0445b07`, and the target environment is Ubuntu/Debian with newer bash, so I did not count it against Commit A.

## Next Action for Architect
Fix P1 #2 in Commit B migration before testing upgrades, and fix P1 #1 in Commit C before any public release/one-liner announcement. Also handle the gRPC `serviceName` migration edge case while Commit B has the old inbound context.
