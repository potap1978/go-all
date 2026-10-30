#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# goVLESS Installer Bootstrap
# Usage: curl -sL https://is.gd/govless | sudo bash
set -e

# Codex 019: never let git prompt interactively (private repo + no creds
# would otherwise block on "Username for 'https://github.com:'").
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/false

# Ensure root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Run as root (sudo)!"
    exit 1
fi

# Install minimal deps for bootstrap
apt-get update -qq >/dev/null 2>&1 || true; apt-get install -y -qq git curl >/dev/null 2>&1 || true

# Clone or update repo
REPO_DIR="${HOME}/goVLESS"  # local checkout; URL controlled by GOVLESS_REPO/GH_TOKEN
# Build auth args once (used for both clone and fetch)
REPO_URL="${GOVLESS_REPO:-https://github.com/potap1978/go-all.git}"
BRANCH="${GOVLESS_BRANCH:-main}"
GIT_AUTH_ARGS=()
if [ -n "${GH_TOKEN:-}" ] && [[ "$REPO_URL" == https://github.com/* ]] && [[ "$REPO_URL" != *"@"* ]]; then
    AUTH_B64=$(printf "x-access-token:%s" "$GH_TOKEN" | base64 -w0)
    GIT_AUTH_ARGS=(-c "http.${REPO_URL}.extraHeader=Authorization: Basic $AUTH_B64")
fi

if [ -d "$REPO_DIR" ]; then
    cd "$REPO_DIR"
    WAS_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    # Same trap as bootstrap.sh: on a single-branch clone `git fetch origin dev`
    # never creates origin/dev, so the switch silently no-ops. Widen the refspec
    # before fetching, and fetch into an explicit destination ref.
    git config --replace-all remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null || true
    # Codex 019 fail-soft: if private-repo update can't auth, continue
    # with local copy rather than crashing. Avoid `git pull` (combines
    # fetch+merge, less predictable); use explicit fetch + checkout -B.
    if git "${GIT_AUTH_ARGS[@]}" fetch origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" --quiet 2>/dev/null; then
        # -B in one step, then verify. The old `checkout || true` + `reset --hard
        # origin/$BRANCH` reset WHATEVER branch was checked out onto the requested
        # commit, so HEAD kept the old branch name over the new branch's code.
        git checkout -q -f -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || true
        _now="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        if [ "$_now" != "$BRANCH" ] || \
           [ "$(git rev-parse HEAD 2>/dev/null || true)" != "$(git rev-parse "origin/$BRANCH" 2>/dev/null || true)" ]; then
            echo "✗  Не удалось переключить $REPO_DIR на ветку «$BRANCH» (сейчас «${_now:-неизвестно}»)."
            echo "✗  Ничего не запускаю. Удалите $REPO_DIR и повторите команду."
            exit 1
        fi
    else
        # Обновление не удалось (обычно — нет доступа/токена). НЕ крутим молча
        # возможно-УСТАРЕВШУЮ локальную копию: пробуем переклонировать начисто в
        # temp и подменить; только если и это не вышло — оставляем локальную копию
        # и ГРОМКО предупреждаем, показывая её версию.
        echo "ℹ  Не удалось обновить из $REPO_URL — переклонирую начисто…"
        _tmp="${REPO_DIR}.new.$$"
        rm -rf "$_tmp"
        if git "${GIT_AUTH_ARGS[@]}" clone -q --branch "$BRANCH" "$REPO_URL" "$_tmp" 2>/dev/null; then
            cd / && rm -rf "$REPO_DIR" && mv "$_tmp" "$REPO_DIR" && cd "$REPO_DIR"
            echo "✓  Обновлено начисто."
        else
            rm -rf "$_tmp"
            _lv=$(grep -oE 'GOVLESS_VERSION=\"[^\"]+\"' "$REPO_DIR/lib/common.sh" 2>/dev/null | head -1)
            # Running a stale copy of the SAME branch is a judgement call the
            # operator can live with. Running a different branch under the name
            # they asked for is not — stop instead.
            if [ "$WAS_BRANCH" != "$BRANCH" ]; then
                echo "✗  Запрошена ветка «$BRANCH», но репозиторий недоступен,"
                echo "✗  а локальная копия — на ветке «${WAS_BRANCH:-неизвестно}». Ничего не запускаю."
                echo "✗  Задайте корректный GH_TOKEN (или почините сеть) и повторите команду."
                exit 1
            fi
            echo "⚠  ВНИМАНИЕ: обновиться не удалось — запускается ЛОКАЛЬНАЯ копия (${_lv:-версия неизвестна}) ветки «$BRANCH»."
            echo "⚠  Она может быть УСТАРЕВШЕЙ. Если ловите ошибки установки —"
            echo "⚠  задайте корректный GH_TOKEN и запустите команду снова."
        fi
    fi
else
    if ! git "${GIT_AUTH_ARGS[@]}" clone -q --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" 2>/dev/null; then
        echo "Error: failed to clone $REPO_URL"
        echo "goVLESS is private. Set GH_TOKEN env var or run 'gh auth login' first."
        echo "Or set GOVLESS_REPO to a public mirror URL."
        exit 1
    fi
    git -C "$REPO_DIR" config --replace-all remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*' 2>/dev/null || true
fi

cd "$REPO_DIR"
chmod +x govless.sh
./govless.sh
