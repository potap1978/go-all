#!/usr/bin/env bash
# tools/fetch_test_bins.sh — real validators for the offline tests (tests/realbin.py): xray of the version the nodes
# run (the pinned EGRESS_XVER of install.sh), for THIS machine (macOS / Linux, amd64 / arm64), from the
# releases of XTLS/Xray-core — as egress_ensure_xray of install.sh does — checked by the SHA2-256 of its .dgst,
# into ~/.cache/kaskad-tests/xray/<version>/xray. Once: a binary in the cache is not downloaded again.
# No network, no curl, a bad sum — a line on stderr and exit 0: the tests then say «SKIP», not green silently.
# KASKAD_TEST_OFFLINE=1 — never go to the network.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER=$(sed -n 's/^EGRESS_XVER="\(v[0-9.]*\)"$/\1/p' "$ROOT/install.sh" | head -n1)
[ -n "$VER" ] || { echo "fetch_test_bins: версия xray в install.sh не найдена" >&2; exit 0; }
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/kaskad-tests/xray/$VER"
BIN="$CACHE/xray"
if [ -x "$BIN" ]; then echo "xray $VER: $BIN"; exit 0; fi
[ "${KASKAD_TEST_OFFLINE:-}" = "1" ] && { echo "fetch_test_bins: KASKAD_TEST_OFFLINE=1 — xray не скачиваю" >&2; exit 0; }
command -v curl >/dev/null 2>&1 || { echo "fetch_test_bins: нет curl" >&2; exit 0; }
case "$(uname -s)/$(uname -m)" in
    Darwin/arm64)               Z="Xray-macos-arm64-v8a.zip" ;;
    Darwin/x86_64)              Z="Xray-macos-64.zip" ;;
    Linux/x86_64|Linux/amd64)   Z="Xray-linux-64.zip" ;;
    Linux/aarch64|Linux/arm64)  Z="Xray-linux-arm64-v8a.zip" ;;
    *) echo "fetch_test_bins: нет сборки xray для $(uname -s)/$(uname -m)" >&2; exit 0 ;;
esac
TMP=$(mktemp -d "${TMPDIR:-/tmp}/kaskad-fetch.XXXXXX") || exit 0
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/XTLS/Xray-core/releases/download/$VER/$Z"
if ! curl -fsSL --retry 2 --connect-timeout 15 --max-time 180 -o "$TMP/x.zip" "$URL" \
   || ! curl -fsSL --retry 2 --connect-timeout 15 --max-time 30 -o "$TMP/x.dgst" "$URL.dgst"; then
    echo "fetch_test_bins: xray $VER не скачался ($Z)" >&2; exit 0
fi
WANT=$(sed -n 's/^SHA2-256= *\([0-9a-f]\{64\}\)$/\1/p' "$TMP/x.dgst" | head -n1)
if command -v sha256sum >/dev/null 2>&1; then GOT=$(sha256sum "$TMP/x.zip" | cut -d' ' -f1)
else GOT=$(shasum -a 256 "$TMP/x.zip" | cut -d' ' -f1); fi
[ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || { echo "fetch_test_bins: сумма архива xray не совпала — не ставлю" >&2; exit 0; }
mkdir -p "$TMP/x"
python3 -I -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extract("xray", sys.argv[2])' "$TMP/x.zip" "$TMP/x" \
    || { echo "fetch_test_bins: в архиве нет xray" >&2; exit 0; }
# a name of its own next to the binary (two runners at once must not move each other's half-written file); then one
# atomic rename
mkdir -p "$CACHE" && chmod 755 "$TMP/x/xray" && mv -f "$TMP/x/xray" "$BIN.new.$$" && mv -f "$BIN.new.$$" "$BIN"
echo "xray $VER: $BIN (скачан, SHA2-256 сверен)"
