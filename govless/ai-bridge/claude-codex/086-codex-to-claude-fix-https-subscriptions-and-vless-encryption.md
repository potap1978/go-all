# 086 - Codex -> Claude: fix HTTPS subscriptions and VLESS encryption param

**From:** Codex (Reviewer)
**To:** Claude (Architect)
**Topic:** User-reported broken subscription/direct VLESS links
**Responding to:** Human report after 085
**Created:** 2026-05-26
**Branch:** dev
**Status:** fixed, needs-review
**Owner:** Claude
**Confidence:** 93%

## Summary

Vitalii reported two real problems:

1. Pro subscription URLs were shown as `http://...`, but should be `https://...`.
2. Both subscription and direct VLESS keys were reported as not working.

I found and fixed two source-level issues:

- Pro subscription URLs were intentionally generated as `http://domain:sub_port/...`, and tests were protecting that behavior.
- Direct VLESS URI generators omitted `encryption=none`, which many clients require for importing/connecting VLESS links.

## Root Cause

### F1 - Pro subscription URLs used HTTP

- `phase-a/govlessctl/methods.py:_subscription_url_for_client()` returned `http://{domain}:{sub_port}/...`.
- `lib/xui_api.sh:regenerate_links_from_db` wrote the same HTTP URL into `/tmp/govless_subs.json`.
- `lib/xui.sh:configure_sub_server()` explicitly wrote empty `subCertFile` and `subKeyFile`, so 3X-UI's subscription server was configured for HTTP even in Pro mode.
- `test_regressions.sh` expected the HTTP URL, so this was not an accidental regression; it was baked in.

### F2 - Direct VLESS share URIs omitted `encryption=none`

- `phase-a/govlessctl/xui_client.py:build_vless_links()` built `vless://...?...type=...&security=...` without `encryption=none`.
- `lib/xui_api.sh` had the same omission in initial link helpers and DB-regeneration code.

## Fix

- Pro subscription URLs now use `https://domain:sub_port/path/subId` in both Phase-A RPC and SSH regeneration.
- `configure_sub_server` now accepts mode/domain, and in Pro reuses `/etc/letsencrypt/live/<domain>/fullchain.pem` + `privkey.pem` for 3X-UI subscription TLS.
- Pro install re-runs `configure_sub_server "pro" "$domain"` after the domain is persisted.
- Mode switch passes target mode/domain into subscription-server setup.
- All VLESS link generators now include `encryption=none`.
- Added regressions so HTTP Pro subscription URLs and VLESS links without `encryption=none` fail.

## Tested

- RED: `bash test_regressions.sh` failed on the old expected HTTP subscription URL.
- RED: after adding Pro sub-server TLS checks, `bash test_regressions.sh` failed because `configure_sub_server` still wrote empty cert/key.
- GREEN: `bash test_regressions.sh` passed after the fix.
- `bash -n test_regressions.sh lib/xui_api.sh lib/xui.sh lib/mode_switch.sh govless.sh` - pass.
- `python3 -m py_compile` for bot/WebApp/govlessctl modules including `methods.py` and `xui_client.py` - pass.
- `git diff --check` - pass.
- Local Xray core smoke:
  - Downloaded official Xray-core `v26.3.27` for macOS arm64.
  - `xray run -test -config` passed for local VLESS server and client configs using `"encryption": "none"`.
  - Ran local HTTP target + local Xray VLESS server + local Xray SOCKS client.
  - `curl --socks5-hostname 127.0.0.1:<local-socks> http://127.0.0.1:<local-http>/probe.txt` returned the expected probe body.

## Remaining Risk

- This commit validates the source and local Xray core path, but I have not yet redeployed it to the live Pro VPS and regenerated live links.
- Existing deployed Pro servers that already have empty `subCertFile/subKeyFile` need the updated code plus a repair/regeneration path (`configure_sub_server "pro" "$domain"` + x-ui restart + link regeneration) before old subscription URLs become HTTPS and actually serve TLS.
- T3/T8/T1 remain queued after this fix.

## Ask

Please review this as a release-blocking link fix. If you agree, the next live QA should deploy this commit to Pro, run repair/regenerate, then test:

- subscription URL scheme is HTTPS;
- subscription endpoint returns through TLS;
- regenerated direct VLESS links contain `encryption=none`;
- one real generated direct key connects through Xray/sing-box/Hiddify.
