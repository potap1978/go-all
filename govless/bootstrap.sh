#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# goVLESS bootstrap — one-liner installer
# Usage: bash <(curl -sL URL/bootstrap.sh)
set -euo pipefail

# Codex 019: ensure git never tries interactive auth on any path.
# Without these, `git fetch` against a private HTTPS remote with no
# credentials will fall through to `Username for 'https://github.com:'`
# prompt and block the installer indefinitely.
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/false
# Anti-hang on throttled links (RU DPI stalls transfers mid-stream): abort any
# git transfer that drops under 1 KB/s for 20s instead of hanging forever.
export GIT_HTTP_LOW_SPEED_LIMIT=1024
export GIT_HTTP_LOW_SPEED_TIME=20

# Source repo. goVLESS pro lives in the public monorepo potap1978/go-all,
# in the "govless" subdirectory. The whole repository is cloned because
# goInstaller reads this checkout's HEAD to learn the installed branch; the
# product's own files are run from $INSTALL_DIR/$GOVLESS_REPO_DIR.
# GH_TOKEN is optional and only needed for a fork or a private mirror:
#   1) gh auth login (interactive, GH PAT) before running this script;
#   2) GH_TOKEN env var set, passed via Authorization Basic header (NOT in URL —
#      keeps token out of .git/config and stderr on failure).
REPO="${GOVLESS_REPO:-https://github.com/potap1978/go-all.git}"
# Subdirectory of the monorepo that holds this product.
SUBDIR="${GOVLESS_REPO_DIR:-govless}"
BRANCH="${GOVLESS_BRANCH:-main}"
# Overridable only so the branch-switch test can drive the real script against a
# throwaway directory; every real install uses the default.
INSTALL_DIR="${GOVLESS_INSTALL_DIR:-/opt/govless-installer}"
# Canonical URL (no token) — for error messages and final remote
REPO_CANONICAL="$REPO"

# Build auth args for git: use Authorization header (NEVER put token in URL,
# git would persist it into .git/config and leak via stderr on failure).
GIT_AUTH_ARGS=()
if [ -n "${GH_TOKEN:-}" ] && [[ "$REPO" == https://github.com/* ]] && [[ "$REPO" != *"@"* ]]; then
    AUTH_B64=$(printf "x-access-token:%s" "$GH_TOKEN" | base64 -w0)
    # Через окружение, а не `git -c …`: аргументы командной строки читает любой
    # локальный пользователь в `ps`/`/proc/<pid>/cmdline`, окружение — только root
    # (QA S-22). git ≥ 2.31 (Ubuntu 22.04+, Debian 12+).
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0="http.${REPO}.extraHeader"
    export GIT_CONFIG_VALUE_0="Authorization: Basic $AUTH_B64"
fi

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

# Stop with an explanation instead of running code from a branch nobody asked
# for. First argument is the headline, the rest are indented detail lines;
# empty ones are dropped so callers can pass an optional git error through.
die() {
    echo -e "  ${RED}✗${NC} $1"
    shift
    while [ $# -gt 0 ]; do
        [ -z "$1" ] || echo "     $1"
        shift
    done
    echo ""
    exit 1
}

echo ""
echo -e "  ${CYAN}${BOLD}goVLESS Installer${NC}"
echo -e "  ${CYAN}─────────────────${NC}"
echo ""

# Root check
if [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${RED}✗${NC} Run as root: ${BOLD}sudo bash bootstrap.sh${NC}"
    exit 1
fi

# Install git if missing
if ! command -v git &>/dev/null; then
    echo -e "  ${CYAN}ℹ${NC}  Installing git..."
    timeout 180 apt-get update -qq >/dev/null 2>&1 && timeout 180 apt-get install -y -qq git >/dev/null 2>&1
fi

# Clone or update.
#
# `git clone -b <branch> --depth 1` produces a SINGLE-BRANCH clone: its only
# fetch refspec is +refs/heads/<that branch>:refs/remotes/origin/<that branch>.
# A later `git fetch origin dev` on such a clone writes FETCH_HEAD and nothing
# else — refs/remotes/origin/dev is never created, so every checkout against it
# fails, the old `|| true` chain swallowed the failures, and "Updated to latest
# dev" got printed over a tree that was still on main. goInstaller reads this
# clone's HEAD to decide which branch is live, so that lie reached its UI too.
# Hence: widen the refspec, switch with a single -B checkout, and VERIFY before
# claiming anything.
ALL_BRANCHES_REFSPEC='+refs/heads/*:refs/remotes/origin/*'

# The clone has to be OURS before it is updated. goVLESS Lite from the public
# guide (potap1978/go-all-lite-version) clones itself into this very directory,
# and "updating" that clone fetched Lite again: goInstaller's "install goVLESS
# pro" printed "Updated to latest main" and opened the Lite menu. A clone of any
# other repository is replaced with a fresh clone of $REPO. Only installer code
# lives here: the VPN, the 3X-UI panel, keys and users are in /opt/govless and
# the panel database, and govless.sh takes them over as an existing install
# (Lite and pro share the config schema). Lite's own remove_everything deletes
# this directory for the same reason: nothing but code is in it.
#
# Compared as lower-case owner/repo: https, ssh, token-in-URL and ".git"-less
# spellings of one repository must match, and GitHub names ignore case.
repo_id() {
    local u="${1%/}"
    u="${u%.git}"
    u="${u#*://}"                     # scheme
    u="${u#*@}"                       # user:token@ or git@
    u="${u/github.com:/github.com/}"  # scp-like git@github.com:owner/repo
    printf '%s' "$u" | tr '[:upper:]' '[:lower:]'
}
FOREIGN_ORIGIN=""
if [ -d "$INSTALL_DIR/.git" ]; then
    ORIGIN_URL="$(git -C "$INSTALL_DIR" config --get remote.origin.url 2>/dev/null || true)"
    if [ "$(repo_id "$ORIGIN_URL")" != "$(repo_id "$REPO_CANONICAL")" ]; then
        FOREIGN_ORIGIN="${ORIGIN_URL:-no origin}"
    fi
fi

if [ -d "$INSTALL_DIR/.git" ] && [ -z "$FOREIGN_ORIGIN" ]; then
    echo -e "  ${CYAN}ℹ${NC}  Updating..."
    cd "$INSTALL_DIR"
    # Read BEFORE touching anything: needed to tell "no update happened, same
    # branch" (survivable) from "no update happened, wrong branch" (fatal).
    WAS_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    # Heals clones made by earlier versions of this script, which are still
    # single-branch on every server installed so far.
    git config --replace-all remote.origin.fetch "$ALL_BRANCHES_REFSPEC" 2>/dev/null || true

    # Keep a shallow clone shallow — otherwise fetching a branch it has never
    # seen drags in that branch's entire history.
    FETCH_ARGS=()
    if [ -f "$(git rev-parse --git-dir 2>/dev/null || echo .git)/shallow" ]; then
        FETCH_ARGS=(--depth 1)
    fi
    # Explicit destination ref rather than a bare branch name: this creates
    # origin/$BRANCH even if the config rewrite above did not stick.
    if timeout 180 git "${GIT_AUTH_ARGS[@]}" fetch "${FETCH_ARGS[@]}" origin \
        "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" --quiet 2>/dev/null; then
        # -B creates the local branch or force-moves it onto the remote one, in
        # one step; -f makes a dirty working copy a non-issue.
        CHECKOUT_ERR="$(git checkout -q -f -B "$BRANCH" "origin/$BRANCH" 2>&1 || true)"

        # Verify rather than trust. A broken index, a missing origin/$BRANCH or a
        # permissions problem all leave HEAD where it was, and "Updated" must
        # never be printed over a HEAD that did not move.
        NOW_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        NOW_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
        WANT_HEAD="$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)"
        if [ "$NOW_BRANCH" != "$BRANCH" ] || [ -z "$WANT_HEAD" ] || [ "$NOW_HEAD" != "$WANT_HEAD" ]; then
            die "Could not switch $INSTALL_DIR to branch '$BRANCH'." \
                "HEAD is on '${NOW_BRANCH:-unknown}' (${NOW_HEAD:0:7}), expected '$BRANCH' (${WANT_HEAD:0:7})." \
                "${CHECKOUT_ERR}" \
                "Nothing was started. Fix: rm -rf $INSTALL_DIR and run this installer again."
        fi
        echo -e "  ${GREEN}✓${NC}  Updated to latest $BRANCH"
    else
        # Two very different problems land here: a branch that does not exist
        # (bad GOVLESS_BRANCH) and a repo we cannot reach (token/network). One
        # cheap ls-remote separates them, and only on the failure path.
        if REMOTE_HEADS="$(timeout 60 git "${GIT_AUTH_ARGS[@]}" ls-remote --heads origin "$BRANCH" 2>/dev/null)"; then
            [ -n "$REMOTE_HEADS" ] || die \
                "Branch '$BRANCH' does not exist in $REPO_CANONICAL." \
                "Check GOVLESS_BRANCH. Nothing was changed."
        fi
        if [ "$WAS_BRANCH" = "$BRANCH" ]; then
            # Same branch as installed: the local copy IS $BRANCH, just possibly
            # stale. Run it, but never call that an update.
            echo -e "  ${CYAN}ℹ${NC}  Could not reach the repo — running the LOCAL copy of '$BRANCH'."
            echo -e "  ${CYAN}ℹ${NC}  NO update was applied; this copy may be outdated."
            echo -e "  ${CYAN}ℹ${NC}  To update next time: fix the network and run the installer again."
        else
            # A different branch was asked for and we have no way to get it.
            # Starting the installed one would run code the operator did not pick.
            die "Asked for branch '$BRANCH', but the repo is unreachable." \
                "The installed copy is on '${WAS_BRANCH:-unknown}', so running it would be the wrong branch." \
                "Fix the network (or set GH_TOKEN for a private mirror) and run again. Nothing was changed."
        fi
    fi
else
    if [ -n "$FOREIGN_ORIGIN" ]; then
        echo -e "  ${CYAN}ℹ${NC}  $INSTALL_DIR is a clone of another repository: $FOREIGN_ORIGIN"
        echo -e "  ${CYAN}ℹ${NC}  Replacing it with goVLESS pro. The VPN, the panel, keys and users stay as they are."
    fi
    echo -e "  ${CYAN}ℹ${NC}  Downloading goVLESS..."
    # Clone NEXT TO the target and swap only once the clone is complete: with no
    # key or no network the server keeps the installer it had, and the `govless`
    # command pointing into it keeps working.
    NEW_DIR="${INSTALL_DIR}.new.$$"
    rm -rf "$NEW_DIR"
    if ! timeout 180 git "${GIT_AUTH_ARGS[@]}" clone --progress -b "$BRANCH" --depth 1 "$REPO_CANONICAL" "$NEW_DIR"; then
        rm -rf "$NEW_DIR"
        echo -e "  ${RED}✗${NC} Failed to reach $REPO_CANONICAL (branch $BRANCH)."
        echo -e "  ${RED}✗${NC} Check the network; set GH_TOKEN only if the repository needs auth."
        if [ -e "$INSTALL_DIR" ]; then
            echo -e "  ${RED}✗${NC} $INSTALL_DIR was left as it was. Nothing was started."
        fi
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
    mv "$NEW_DIR" "$INSTALL_DIR"
    # Widen it here too, not only on update: the FIRST branch switch after a
    # fresh install has to work without a reinstall.
    git -C "$INSTALL_DIR" config --replace-all remote.origin.fetch "$ALL_BRANCHES_REFSPEC" 2>/dev/null || true
    CLONED_BRANCH="$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    [ "$CLONED_BRANCH" = "$BRANCH" ] || die \
        "Cloned $REPO_CANONICAL but HEAD is on '${CLONED_BRANCH:-unknown}', not '$BRANCH'."
fi

if [ ! -f "$INSTALL_DIR/$SUBDIR/govless.sh" ]; then
    echo -e "  ${RED}✗${NC} Download failed"
    exit 1
fi

echo -e "  ${GREEN}✓${NC}  Ready"
echo ""

# Sync-only mode: bring the checkout to $BRANCH, verify it, and stop there
# without starting the installer. goInstaller reads the branch from this clone's
# HEAD and the version from lib/common.sh, and a caller that only wants those
# two answers — or an operator verifying a branch move on a live server — must
# not have an install kicked off underneath them.
if [ "${GOVLESS_BOOTSTRAP_ONLY:-0}" = "1" ]; then
    echo -e "  ${CYAN}ℹ${NC}  Sync only (GOVLESS_BOOTSTRAP_ONLY=1) — installer not started."
    echo ""
    exit 0
fi

# Run
cd "$INSTALL_DIR/$SUBDIR"
# curl|bash puts THIS script on stdin; reconnect the child's stdin to the
# controlling terminal (when one exists) IN the exec so govless.sh prompts read
# keystrokes. Detached/non-interactive runs (no /dev/tty) keep piped stdin.
#
# The probe OPENS /dev/tty instead of asking `test -r`: with no controlling
# terminal (systemd unit, cron, `ssh host bash ...` without -t) the device node
# is still readable by access(2) but opening it fails with ENXIO, and the old
# `[ -r /dev/tty ]` sent the installer straight into
# "bootstrap.sh: /dev/tty: No such device or address" right after "✓ Ready".
if [ ! -t 0 ] && (exec 3</dev/tty) 2>/dev/null; then
    exec bash govless.sh "$@" < /dev/tty
else
    exec bash govless.sh "$@"
fi
