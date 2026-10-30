#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# ═══════════════════════════════════════════════════════════════════════
# lib/templates_catalog.sh — cover-site (masquerade) templates for Pro mode
# ═══════════════════════════════════════════════════════════════════════
#
# v2 (goVLESS 1.4.0): the catalog is the verified library from the
# `site-templates-library` skill — 30,000+ static sites (GitHub / ThemeWagon /
# dawidolko / HTML5 UP / StartBootstrap), every entry was cloned and checked
# with the same functions that live here. Ported from goTelegram Pro 2.10.45
# and adapted to goVLESS conventions (I18N[], confirm(), config_get/set).
#
# Interactive (SSH menu / install_pro):
#   interactive_template_selection   → path to a ready template dir, or "__skip__"
#   select_category / select_template <cat> / show_template_preview <id>
#   download_custom_git_template [url]
#
# Non-interactive (Telegram bot + Mini App via govlessctl, `govless --site-template-*`):
#   site_template_apply <id>          download + deploy + reload nginx + config
#   site_template_apply_custom <url>  same for a public https git repo
#   site_template_apply_random        random catalog entry (up to 3 tries)
#   site_template_apply_stub          built-in "Server is running" page
#   site_restore_backup <file.tgz>    bring back a previous site snapshot
#   site_template_cli <--site-template-*> [arg] [--json]   entry from govless.sh
#
# Catalog readers: get_categories, get_templates_by_category <cat>,
#   get_template_info <id>, get_template_field <id> <field>, pick_random_template_id
#
# Download failure reason lands in TEMPLATE_DL_ERROR and in
# "$TEMPLATES_CACHE/.last_error" (download_template is called via $(...), so the
# variable alone does not reach the caller):
#   download_failed  clone failed (no network / repo gone) — git stderr tail printed
#   no_index         repo has sources for a build, not a ready page
#   app_shell        CRA/Vite shell: index.html exists but renders a blank screen
#   broken_assets    page references local css/js that are not in the repo
# ═══════════════════════════════════════════════════════════════════════

_script_dir_tc="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")"
_TC_DATA_CATALOG="${GOVLESS_DIR:-/opt/govless}/templates_catalog.json"
_TC_REPO_CATALOG="${_script_dir_tc}/templates_catalog.json"
if [ -f "$_TC_DATA_CATALOG" ]; then
    CATALOG_FILE="$_TC_DATA_CATALOG"
elif [ -f "$_TC_REPO_CATALOG" ]; then
    CATALOG_FILE="$_TC_REPO_CATALOG"
else
    CATALOG_FILE="$_TC_DATA_CATALOG"
fi
TEMPLATES_CACHE="${TEMPLATES_CACHE:-}"      # created lazily with mktemp -d
TEMPLATE_DL_ERROR=""
CUSTOM_GIT_MAX_SIZE_MB=100
CUSTOM_GIT_CLONE_TIMEOUT=90
TEMPLATES_PAGE_SIZE=25
SITE_BACKUP_DIR="${GOVLESS_DIR:-/opt/govless}/site-backups"
SITE_BACKUP_KEEP=5
SITE_LOCK_FILE="/run/lock/govless-site-template.lock"
SITE_STUB_ID="__stub__"

# Result of the last site_template_* action (read by site_template_cli --json)
SITE_RESULT_ID=""
SITE_RESULT_NAME=""
SITE_RESULT_ERROR_CODE=""
SITE_RESULT_ERROR=""
SITE_RESULT_HTTP=""
LAST_SITE_BACKUP="${LAST_SITE_BACKUP:-}"

# ── Catalog sync: the repo copy is the source of truth ────────────────
# /opt/govless/templates_catalog.json is what the govlessctl daemon (bot +
# Mini App) reads, so it must follow the installer's copy on every update.
sync_templates_catalog() {
    [ -f "$_TC_REPO_CATALOG" ] || return 0
    if [ ! -f "$_TC_DATA_CATALOG" ] || ! cmp -s "$_TC_REPO_CATALOG" "$_TC_DATA_CATALOG"; then
        mkdir -p "$(dirname "$_TC_DATA_CATALOG")" 2>/dev/null || return 0
        local tmp
        tmp=$(mktemp "${_TC_DATA_CATALOG}.XXXXXX" 2>/dev/null) || return 0
        if cp "$_TC_REPO_CATALOG" "$tmp" 2>/dev/null && mv -f "$tmp" "$_TC_DATA_CATALOG" 2>/dev/null; then
            chmod 644 "$_TC_DATA_CATALOG" 2>/dev/null || true
        else
            rm -f "$tmp" 2>/dev/null || true
            return 0
        fi
    fi
    CATALOG_FILE="$_TC_DATA_CATALOG"
    return 0
}

_templates_cache_dir() {
    if [ -z "${TEMPLATES_CACHE:-}" ] || [ ! -d "$TEMPLATES_CACHE" ]; then
        TEMPLATES_CACHE=$(mktemp -d "${TMPDIR:-/tmp}/govless_templates.XXXXXXXX") || return 1
        chmod 700 "$TEMPLATES_CACHE" 2>/dev/null || true
    fi
    printf '%s' "$TEMPLATES_CACHE"
}

cleanup_templates_cache() {
    [ -n "${TEMPLATES_CACHE:-}" ] && [ -d "$TEMPLATES_CACHE" ] && rm -rf "$TEMPLATES_CACHE" 2>/dev/null
    [ -n "${_TPL_GIT_ERR:-}" ] && rm -f "$_TPL_GIT_ERR" 2>/dev/null
    return 0
}

# ── Catalog loading ────────────────────────────────────────────────────
load_catalog() {
    if [ ! -f "$CATALOG_FILE" ]; then
        if type tf &>/dev/null; then
            log_error "$(tf templates_catalog_not_found "$CATALOG_FILE")"
        else
            log_error "$(tf tplcat_missing_file "$CATALOG_FILE")"
        fi
        return 1
    fi
    return 0
}

catalog_total_count() {
    jq '[.categories[].templates[]] | length' "$CATALOG_FILE" 2>/dev/null || echo 0
}

# ── Categories ─────────────────────────────────────────────────────────
get_categories() {
    jq -r '.categories[] | "\(.id)|\(.name)|\(.icon)|\(.templates | length)"' "$CATALOG_FILE" 2>/dev/null
}

# Lazy mode / bot "random": one random template id from the whole catalog.
pick_random_template_id() {
    [ -f "$CATALOG_FILE" ] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    if command -v shuf >/dev/null 2>&1; then
        jq -r '.categories[].templates[].id' "$CATALOG_FILE" 2>/dev/null | shuf -n 1
    else
        jq -r '.categories[].templates[].id' "$CATALOG_FILE" 2>/dev/null | awk -v seed="$RANDOM" 'BEGIN{srand(seed)} {a[NR]=$0} END{if(NR>0) print a[int(rand()*NR)+1]}'
    fi
}

get_category_name() {
    local cat_id="$1"
    jq --arg id "$cat_id" -r '.categories[] | select(.id == $id) | .name' "$CATALOG_FILE" 2>/dev/null
}

# Codex 029 P3: localize category name via I18N[tplcat_<id>] with fallback
# to the raw JSON name. RU keeps original Russian names; EN gets clean
# English translations. Unknown categories fall through to the JSON name.
localized_category_name() {
    local cat_id="$1"
    local raw_name="$2"
    local key="tplcat_${cat_id}"
    local translated="${I18N[$key]:-}"
    if [ -n "$translated" ]; then
        echo "$translated"
    else
        echo "$raw_name"
    fi
}

# ── Templates in a category ────────────────────────────────────────────
get_templates_by_category() {
    local cat_id="$1"
    jq --arg id "$cat_id" -r '.categories[] | select(.id == $id) | .templates[] | "\(.id)|\(.name)|\(.source)|\(.preview_url)"' "$CATALOG_FILE" 2>/dev/null
}

# ── Template info ──────────────────────────────────────────────────────
get_template_info() {
    local tpl_id="$1"
    jq --arg id "$tpl_id" '.categories[].templates[] | select(.id == $id)' "$CATALOG_FILE" 2>/dev/null
}

get_template_field() {
    local tpl_id="$1"
    local field="$2"
    jq --arg id "$tpl_id" --arg f "$field" -r '.categories[].templates[] | select(.id == $id) | .[$f] // empty' "$CATALOG_FILE" 2>/dev/null
}

_tpl_id_ok() {
    [[ "${1:-}" =~ ^[A-Za-z0-9_.-]{1,120}$ ]]
}

# ── Hardened git clone ─────────────────────────────────────────────────
_tpl_repo_url_ok() {
    local u="${1:-}"
    [ -n "$u" ] || return 1
    [ "${#u}" -le 512 ] || return 1
    [[ "$u" =~ ^https:// ]] || return 1
    # no shell metacharacters / whitespace inside the URL
    [[ "$u" =~ [[:space:]\;\`\$\(\)\<\>\|\\\&\'\"] ]] && return 1
    return 0
}

_tpl_git_clone() {
    local url="${1:-}" dest="${2:-}"
    shift 2 2>/dev/null || return 1
    if ! _tpl_repo_url_ok "$url"; then
        log_error "$(tf tplcat_bad_url "${url:-<empty>}")"
        return 1
    fi
    # Keep git's stderr: callers silence it, and a "no GitHub connectivity"
    # used to surface as a misleading "no index.html".
    if [ -z "${_TPL_GIT_ERR:-}" ] || [ ! -f "$_TPL_GIT_ERR" ]; then
        # Inside the download cache when it exists: download_template runs in
        # $(...), so a mktemp'd path never reaches the parent and leaked one
        # /tmp/govless_git_err.* per attempt (QA BUG-03). The cache dir is removed
        # by cleanup_templates_cache on exit.
        if [ -n "${TEMPLATES_CACHE:-}" ] && [ -d "$TEMPLATES_CACHE" ]; then
            _TPL_GIT_ERR="$TEMPLATES_CACHE/.git_err"
            : > "$_TPL_GIT_ERR" 2>/dev/null || _TPL_GIT_ERR="/dev/null"
        else
            _TPL_GIT_ERR=$(mktemp "${TMPDIR:-/tmp}/govless_git_err.XXXXXXXX" 2>/dev/null || echo "/dev/null")
        fi
    fi
    GIT_TERMINAL_PROMPT=0 git \
        -c protocol.allow=never \
        -c protocol.https.allow=always \
        -c protocol.ext.allow=never \
        -c protocol.file.allow=never \
        clone --depth 1 "$@" -- "$url" "$dest" 2>"$_TPL_GIT_ERR"
}

# ── Where is the page? ─────────────────────────────────────────────────
# Order matters (`find | head -1` used to pick documentation/ or src/ pages).
_tpl_pick_index_dir() {
    local base="$1" d
    for d in dist build . html theme live public_html site _site; do
        [ -f "$base/$d/index.html" ] && { printf '%s' "$base/$d"; return 0; }
    done
    # Two passes: first skipping docs/sources/examples; then allowing
    # examples/ and docs/ (some templates keep their pages there).
    d=$(find "$base" -mindepth 2 -maxdepth 5 -type f -name index.html \
        -not -path '*/documentation/*' -not -path '*/docs/*' -not -path '*/src/*' \
        -not -path '*/public/*' -not -path '*/node_modules/*' -not -path '*/.github/*' \
        -not -path '*/test/*' -not -path '*/tests/*' -not -path '*/example*' -not -path '*/.git/*' 2>/dev/null \
        | awk '{ print gsub("/","/") "\t" $0 }' | sort -n | head -1 | cut -f2-) || true
    [ -z "$d" ] && d=$(find "$base" -mindepth 2 -maxdepth 5 -type f -name index.html \
        -not -path '*/documentation/*' -not -path '*/src/*' -not -path '*/node_modules/*' \
        -not -path '*/.github/*' -not -path '*/.git/*' 2>/dev/null \
        | awk '{ print gsub("/","/") "\t" $0 }' | sort -n | head -1 | cut -f2-) || true
    [ -n "$d" ] && { printf '%s' "$(dirname "$d")"; return 0; }
    # No index.html but home.html / main.html — same landing page
    for d in home.html main.html Home.html; do
        [ -f "$base/$d" ] && { cp "$base/$d" "$base/index.html" 2>/dev/null && printf '%s' "$base" && return 0; }
    done
    return 1
}

# The page must be READY, not merely present: app shells (CRA/Vite) and
# pages whose local css/js are missing render blank — reject them.
_tpl_page_ok() {
    local dir="$1" idx="$1/index.html" txt refs missing=0 total=0 ref f
    [ -f "$idx" ] || { TEMPLATE_DL_ERROR="no_index"; return 1; }
    if grep -q '%PUBLIC_URL%' "$idx" 2>/dev/null || grep -qE 'src="\.?/?src/[^"]*\.(jsx?|tsx?)"' "$idx" 2>/dev/null; then
        TEMPLATE_DL_ERROR="app_shell"; return 1
    fi
    txt=$(sed -e 's/<script[^>]*>.*<\/script>//g' -e 's/<style[^>]*>.*<\/style>//g' -e 's/<[^>]*>/ /g' "$idx" 2>/dev/null | tr -s ' \t\n' ' ' | wc -c) || txt=0
    if [ "${txt:-0}" -lt 300 ] && ! grep -qiE '<img|background(-image)?:' "$idx" 2>/dev/null; then
        TEMPLATE_DL_ERROR="app_shell"; return 1
    fi
    refs=$(grep -oiE '(href|src)="[^"#?:]+\.(css|js)(\?[^"]*)?"' "$idx" 2>/dev/null | sed -E 's/^[a-zA-Z]+="//; s/"$//; s/\?.*$//' | grep -vE '^(https?:)?//' | head -20) || true
    while IFS= read -r ref; do
        [ -n "$ref" ] || continue
        total=$((total+1)); f="$dir/${ref#/}"
        [ -f "$f" ] || missing=$((missing+1))
    done <<< "$refs"
    if [ "$total" -ge 2 ] && [ "$missing" -eq "$total" ]; then
        TEMPLATE_DL_ERROR="broken_assets"; return 1
    fi
    return 0
}

_tpl_clone_sparse() {
    # $1=repo $2=sparse_path $3=dest — mono-repos (html5up, learning-zone, dawidolko)
    local repo="$1" sparse="$2" dest="$3" tmp_clone
    tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/govless_sparse.XXXXXXXX") || return 1
    if ! _tpl_git_clone "$repo" "$tmp_clone" --filter=blob:none --sparse 2>/dev/null; then
        rm -rf "$tmp_clone"; mkdir -p "$tmp_clone"
        _tpl_git_clone "$repo" "$tmp_clone" 2>/dev/null || true
    fi
    if [ -d "$tmp_clone" ]; then
        git -C "$tmp_clone" sparse-checkout set "$sparse" 2>/dev/null || true
        _tpl_mark_cloned "$tmp_clone"
        if [ -d "$tmp_clone/$sparse" ]; then
            cp -r "$tmp_clone/$sparse/." "$dest/" 2>/dev/null || true
        fi
    fi
    rm -rf "$tmp_clone"
    return 0
}

_tpl_mark_cloned() {
    [ -n "$(ls -A "$1" 2>/dev/null | grep -v '^\.git$')" ] && _TPL_CLONED=1
    return 0
}

# ── Template download (from catalog) ───────────────────────────────────
# Prints the template directory on success. On failure: reason code in
# TEMPLATE_DL_ERROR + "$output_dir/.last_error", directory removed.
download_template() {
    local tpl_id="$1"
    local output_dir="${2:-}"
    if [ -z "$output_dir" ]; then
        _templates_cache_dir >/dev/null || return 1
        output_dir="$TEMPLATES_CACHE"
    fi
    rm -f "$output_dir/.last_error" 2>/dev/null || true
    if ! _tpl_id_ok "$tpl_id"; then
        TEMPLATE_DL_ERROR="invalid_id"
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(tf tplcat_bad_id "${tpl_id:-<empty>}")"
        return 1
    fi
    local info
    info=$(get_template_info "$tpl_id")
    if [ -z "$info" ]; then
        TEMPLATE_DL_ERROR="not_found"
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(tf templates_not_in_catalog "$tpl_id")"
        return 1
    fi

    local repo_url sparse_path source name
    repo_url=$(echo "$info" | jq -r '.repo_url // empty')
    sparse_path=$(echo "$info" | jq -r '.sparse_path // empty')
    source=$(echo "$info" | jq -r '.source // empty')
    name=$(echo "$info" | jq -r '.name // empty')
    [ -n "$name" ] || name="$tpl_id"

    # A template must come from an https repo — anything else is a potential RCE
    if ! _tpl_repo_url_ok "$repo_url"; then
        TEMPLATE_DL_ERROR="bad_url"
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(tf tplcat_bad_repo_url "$tpl_id")"
        return 1
    fi

    local clone_dir="$output_dir/${tpl_id}"
    rm -rf "$clone_dir"
    mkdir -p "$clone_dir"
    # "did the repo download at all" is tracked separately from "did we find
    # a page": files land in clone_dir only when index.html was found.
    _TPL_CLONED=0

    log_info "$(tf templates_downloading "$name")"

    case "$source" in
        html5up|learning-zone|dawidolko)
            _tpl_clone_sparse "$repo_url" "$sparse_path" "$clone_dir"
            ;;
        *)
            # startbootstrap / themewagon / colorlib / github — one repo per
            # template: full shallow clone, page in root or dist/public/docs/build.
            local tw_tmp
            tw_tmp=$(mktemp -d "${TMPDIR:-/tmp}/govless_clone.XXXXXXXX") || return 1
            _tpl_git_clone "$repo_url" "$tw_tmp" 2>/dev/null || true
            if [ -d "$tw_tmp" ]; then
                _tpl_mark_cloned "$tw_tmp"
                rm -rf "$tw_tmp/.git"
                local tw_dir
                if tw_dir=$(_tpl_pick_index_dir "$tw_tmp"); then
                    cp -r "$tw_dir/." "$clone_dir/" 2>/dev/null || true
                fi
            fi
            rm -rf "$tw_tmp"
            ;;
    esac

    # Check result: the page must exist AND be ready (not a shell, not broken)
    rm -rf "$clone_dir/.git" 2>/dev/null || true
    # Drop symlinks before verification (QA BUG-13): _tpl_page_ok must not read
    # a page "through" a link out of the tree, and nothing symlinked may reach
    # the web root even if deploy is called on this directory directly.
    find "$clone_dir" -type l -delete 2>/dev/null || true
    TEMPLATE_DL_ERROR=""
    if [ -f "$clone_dir/index.html" ] && _tpl_page_ok "$clone_dir"; then
        log_success "$(tf templates_downloaded "$name")"
        echo "$clone_dir"
        return 0
    elif [ -f "$clone_dir/index.html" ]; then
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        case "$TEMPLATE_DL_ERROR" in
            broken_assets) log_error "$(tf templates_broken_assets "$name")" ;;
            *)             log_error "$(tf templates_app_shell "$name")" ;;
        esac
        log_dim "$(t templates_pick_another)"
        rm -rf "$clone_dir" 2>/dev/null || true
        return 1
    else
        # fallback: page in a sub-folder (non-standard layout)
        local fallback_dir
        if fallback_dir=$(_tpl_pick_index_dir "$clone_dir") && [ "$fallback_dir" != "$clone_dir" ] && [ "$fallback_dir" != "$clone_dir/." ]; then
            cp -r "$fallback_dir/." "$clone_dir/" 2>/dev/null || true
            if _tpl_page_ok "$clone_dir"; then
                log_success "$(tf templates_downloaded_subfolder "$name")"
                echo "$clone_dir"
                return 0
            fi
        fi
        # Two different problems used to hide behind one "no index.html":
        # empty dir = the repo did not download (network / GitHub / repo gone);
        # files present but no page = build sources, not a ready site.
        TEMPLATE_DL_ERROR="no_index"
        [ "${_TPL_CLONED:-0}" = "1" ] || TEMPLATE_DL_ERROR="download_failed"
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        if [ "$TEMPLATE_DL_ERROR" = "download_failed" ]; then
            log_error "$(tf templates_download_failed "$name")"
            [ -n "${repo_url:-}" ] && log_dim "$(tf templates_repo_line "$repo_url")"
            if [ -s "${_TPL_GIT_ERR:-/nonexistent}" ]; then
                { grep -vE '^\s*$|^Cloning into|^Клонирование' "$_TPL_GIT_ERR" || true; } | tail -n 3 | sed 's/^/      git: /' >&2
            fi
        else
            log_error "$(tf templates_not_static "$name")"
        fi
        log_dim "$(t templates_pick_another)"
        rm -rf "$clone_dir" 2>/dev/null || true
        return 1
    fi
}

# ── Custom git URL helpers ─────────────────────────────────────────────

# Validate a user-supplied git URL
# Accepts: https://host/path[.git][@branch]
# Rejects: ssh://, git://, file://, absolute file paths, shell metacharacters
_validate_custom_git_url() {
    local url="$1"
    [[ "$url" =~ ^https:// ]] || return 1
    [[ "$url" =~ [[:space:]\;\`\$\(\)\<\>\|\\\&\'\"] ]] && return 1
    [ "${#url}" -gt 512 ] && return 1
    return 0
}

# Parse URL → sets CUSTOM_GIT_CLEAN and CUSTOM_GIT_BRANCH globals
_parse_custom_git_url() {
    local url="$1"
    CUSTOM_GIT_CLEAN=""
    CUSTOM_GIT_BRANCH=""
    if [[ "$url" =~ ^(https://[^@]+)@([A-Za-z0-9._/-]+)$ ]]; then
        CUSTOM_GIT_CLEAN="${BASH_REMATCH[1]}"
        CUSTOM_GIT_BRANCH="${BASH_REMATCH[2]}"
    else
        CUSTOM_GIT_CLEAN="$url"
    fi
    CUSTOM_GIT_CLEAN="${CUSTOM_GIT_CLEAN%/}"
    if [[ ! "$CUSTOM_GIT_CLEAN" =~ \.git$ ]]; then
        CUSTOM_GIT_CLEAN="${CUSTOM_GIT_CLEAN}.git"
    fi
}

_clone_dir_size_mb() {
    local dir="$1"
    du -sm "$dir" 2>/dev/null | awk '{print $1}'
}

# ── Show detailed help for custom git template ─────────────────────────
show_custom_git_help() {
    local line
    line=$(printf '─%.0s' $(seq 1 60))
    echo "" >&2
    echo -e "  ${BOLD}${GREEN}$(t custom_git_title)${NC}" >&2
    echo -e "  ${DIM}${line}${NC}" >&2
    echo -e "  $(t custom_git_help_1)" >&2
    echo -e "  $(t custom_git_help_2)" >&2
    echo -e "  $(t custom_git_help_3)" >&2
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t custom_git_formats)${NC}" >&2
    echo -e "  ${CYAN}$(t custom_git_fmt_github)${NC}" >&2
    echo -e "  ${CYAN}$(t custom_git_fmt_gitlab)${NC}" >&2
    echo -e "  ${CYAN}$(t custom_git_fmt_gitext)${NC}" >&2
    echo -e "  ${CYAN}$(t custom_git_fmt_branch)${NC}" >&2
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t custom_git_auto_detect)${NC}" >&2
    echo -e "  $(t custom_git_auto_1)" >&2
    echo -e "  $(t custom_git_auto_2)" >&2
    echo -e "  $(t custom_git_auto_3)" >&2
    echo -e "  $(t custom_git_auto_4)" >&2
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t custom_git_requirements)${NC}" >&2
    echo -e "  ${YELLOW}$(t custom_git_req_1)${NC}" >&2
    echo -e "  ${YELLOW}$(t custom_git_req_2)${NC}" >&2
    echo -e "  ${YELLOW}$(t custom_git_req_3)${NC}" >&2
    echo -e "  ${YELLOW}$(t custom_git_req_4)${NC}" >&2
    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t custom_git_examples)${NC}" >&2
    echo -e "  ${DIM}$(t custom_git_ex_1)${NC}" >&2
    echo -e "  ${DIM}$(t custom_git_ex_2)${NC}" >&2
    echo -e "  ${DIM}${line}${NC}" >&2
    echo "" >&2
}

# ── Download a custom git template ─────────────────────────────────────
# Prompts for a URL (unless passed), clones with a timeout, detects the page,
# verifies it, copies into $output_dir/custom_<hash>, prints the path.
download_custom_git_template() {
    local url="${1:-}"
    local output_dir="${2:-}"
    if [ -z "$output_dir" ]; then
        _templates_cache_dir >/dev/null || return 1
        output_dir="$TEMPLATES_CACHE"
    fi
    rm -f "$output_dir/.last_error" 2>/dev/null || true

    if [ -z "$url" ]; then
        show_custom_git_help
        echo -ne "  ${WHITE}$(t custom_git_enter_url)${NC} " >&2
        read -r url || url=""
        url=$(echo "$url" | tr -d '\r\n[:space:]')
    fi

    if [ -z "$url" ]; then
        TEMPLATE_DL_ERROR="bad_url"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(t custom_git_empty)"
        return 1
    fi

    if ! _validate_custom_git_url "$url"; then
        TEMPLATE_DL_ERROR="bad_url"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(t custom_git_bad_url)"
        return 1
    fi

    _parse_custom_git_url "$url"
    local clean_url="$CUSTOM_GIT_CLEAN"
    local branch="$CUSTOM_GIT_BRANCH"

    # Stable-ish directory name from a hash of the original URL
    local hash
    hash=$(echo -n "$url" | md5sum 2>/dev/null | awk '{print $1}' | head -c 10)
    [ -z "$hash" ] && hash=$(date +%s)
    local tpl_id="custom_${hash}"
    local clone_dir="$output_dir/${tpl_id}"
    # mktemp instead of a predictable /tmp/custom_git_clone_$$ (symlink attack)
    local tmp_clone err_file
    tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/govless_custom_git.XXXXXXXX") || return 1
    err_file=$(mktemp "${TMPDIR:-/tmp}/govless_custom_git_err.XXXXXXXX") || { rm -rf "$tmp_clone"; return 1; }

    rm -rf "$clone_dir"
    mkdir -p "$clone_dir"

    log_info "$(t custom_git_cloning)"

    local clone_status=0
    # https only; ext::/file:: are explicitly forbidden (URL = command execution as root otherwise)
    local git_args=(-c protocol.allow=never -c protocol.https.allow=always
                    -c protocol.ext.allow=never -c protocol.file.allow=never
                    clone --depth 1)
    [ -n "$branch" ] && git_args+=(--branch "$branch")
    git_args+=(-- "$clean_url" "$tmp_clone")

    if command -v timeout &>/dev/null; then
        GIT_TERMINAL_PROMPT=0 timeout "$CUSTOM_GIT_CLONE_TIMEOUT" git "${git_args[@]}" 2>"$err_file" || clone_status=$?
    else
        GIT_TERMINAL_PROMPT=0 git "${git_args[@]}" 2>"$err_file" || clone_status=$?
    fi

    if [ $clone_status -eq 124 ]; then
        # `timeout` killed git: the repo exists but is too big / GitHub too slow —
        # not "repository unavailable" (QA BUG-07 reported three.js, 912 MB, as such).
        rm -f "$err_file"
        rm -rf "$tmp_clone" "$clone_dir"
        TEMPLATE_DL_ERROR="timeout"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(tf custom_git_timeout "$CUSTOM_GIT_CLONE_TIMEOUT")"
        return 1
    fi
    if [ $clone_status -ne 0 ] || [ -z "$(ls -A "$tmp_clone" 2>/dev/null)" ]; then
        local err_msg
        err_msg=$(head -3 "$err_file" 2>/dev/null | tr '\n' ' ')
        rm -f "$err_file"
        rm -rf "$tmp_clone" "$clone_dir"
        TEMPLATE_DL_ERROR="download_failed"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(tf custom_git_clone_failed "${err_msg:-$clone_status}")"
        return 1
    fi
    rm -f "$err_file"

    rm -rf "$tmp_clone/.git"

    local size_mb
    size_mb=$(_clone_dir_size_mb "$tmp_clone")
    if [ -n "$size_mb" ] && [ "$size_mb" -gt "$CUSTOM_GIT_MAX_SIZE_MB" ]; then
        rm -rf "$tmp_clone" "$clone_dir"
        TEMPLATE_DL_ERROR="too_big"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        # Carry the ACTUAL size to the bot / Mini App: the RPC error said
        # "too large: 100MB (max 100MB)" while the log said 158MB (QA BUG-10).
        printf '%s' "${size_mb}MB" > "$output_dir/.last_detail" 2>/dev/null || true
        log_error "$(tf custom_git_too_big "${size_mb}MB")"
        return 1
    fi

    log_info "$(t custom_git_scanning)"

    local found_dir=""
    local sub try_dir
    for sub in "" "dist" "public" "build" "_site" "site" "docs" "out" "www"; do
        try_dir="$tmp_clone"
        [ -n "$sub" ] && try_dir="$tmp_clone/$sub"
        if [ -f "$try_dir/index.html" ]; then
            found_dir="$try_dir"
            break
        fi
    done
    if [ -z "$found_dir" ]; then
        found_dir=$(_tpl_pick_index_dir "$tmp_clone" 2>/dev/null) || found_dir=""
    fi

    if [ -z "$found_dir" ] || [ ! -f "$found_dir/index.html" ]; then
        rm -rf "$tmp_clone" "$clone_dir"
        TEMPLATE_DL_ERROR="no_index"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(t custom_git_no_index)"
        return 1
    fi

    local rel_path="${found_dir#"$tmp_clone"}"
    rel_path="${rel_path#/}"
    [ -z "$rel_path" ] && rel_path="(root)"
    log_dim "$(tf custom_git_found_at "$rel_path")"

    cp -r "$found_dir"/. "$clone_dir/" 2>/dev/null || true
    find "$clone_dir" -type l -delete 2>/dev/null || true   # QA BUG-13
    rm -rf "$tmp_clone"

    if [ ! -f "$clone_dir/index.html" ]; then
        rm -rf "$clone_dir"
        TEMPLATE_DL_ERROR="no_index"; printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        log_error "$(t custom_git_no_index)"
        return 1
    fi
    if ! _tpl_page_ok "$clone_dir"; then
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        case "$TEMPLATE_DL_ERROR" in
            broken_assets) log_error "$(tf templates_broken_assets "$url")" ;;
            *)             log_error "$(tf templates_app_shell "$url")" ;;
        esac
        rm -rf "$clone_dir"
        return 1
    fi

    # Remember the URL so the menu can show what the site came from
    echo "$url" > "$clone_dir/.custom_git_source" 2>/dev/null

    log_success "$(tf custom_git_installed "$url")"
    echo "$clone_dir"
    return 0
}

# ── Interactive category picker ────────────────────────────────────────
# Returns category id or a sentinel: __skip__ / __custom_git__ / __random_tpl__:<id>
# Codex 029 P2: retry-in-place on invalid input; explicit "0) Skip — use stub"
# so users have a deliberate way to bail out instead of silently deploying a
# stub on garbage input.
select_category() {
    load_catalog || return 1

    while true; do
        echo "" >&2
        echo -e "  ${BOLD}${WHITE}$(t templates_categories)${NC}" >&2
        echo -e "  ${DIM}$(tf templates_total_fmt "$(catalog_total_count)")${NC}" >&2
        echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2

        # Item 0: at install time = "skip, use the stub"; from the post-install
        # menu (TEMPLATES_ZERO_IS_BACK=1) = plain "Back" — pressing the natural
        # "0 = back" used to silently replace a working site with the stub (QA BUG-04).
        if [ "${TEMPLATES_ZERO_IS_BACK:-0}" = "1" ]; then
            printf "  ${CYAN}%2d)${NC} ${YELLOW}%s${NC}\n" 0 "$(t back)" >&2
        else
            printf "  ${CYAN}%2d)${NC} ${YELLOW}%s${NC}\n" 0 "$(t templates_skip_stub)" >&2
        fi
        printf "  ${CYAN}%2d)${NC} ${GREEN}%s${NC}\n" 1 "$(t templates_custom_git)" >&2

        local cats=()
        local i=2
        local id name icon count emoji display_name pad
        while IFS='|' read -r id name icon count; do
            [ -n "$id" ] || continue
            [ "${count:-0}" -eq 0 ] && continue
            case "$icon" in
                briefcase)     emoji="🏢" ;;
                shopping-cart) emoji="🛒" ;;
                heart)         emoji="🏥" ;;
                book)          emoji="🎓" ;;
                palette)       emoji="📸" ;;
                home)          emoji="🏠" ;;
                utensils)      emoji="🍕" ;;
                rocket)        emoji="🎨" ;;
                chart-bar)     emoji="🔧" ;;
                *)             emoji="📄" ;;
            esac
            display_name=$(localized_category_name "$id" "$name")
            # printf %-30s pads by BYTES — Cyrillic names got ragged columns (QA I1);
            # pad by character count instead.
            pad=$(( 31 - ${#display_name} )); [ "$pad" -lt 1 ] && pad=1
            printf "  ${CYAN}%2d)${NC} ${emoji} %s%*s${DIM}$(tf templates_count_fmt "$count")${NC}\n" "$i" "$display_name" "$pad" "" >&2
            cats+=("$id")
            i=$((i+1))
        done < <(get_categories)

        printf "  ${CYAN}%2d)${NC} %s\n" "$i" "$(t templates_random)" >&2
        echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
        echo -ne "  ${WHITE}$(t choose):${NC} " >&2
        local choice
        read -r choice || return 1

        if ! [[ "$choice" =~ ^[0-9]+$ ]]; then
            log_warning "$(t invalid_choice)"
            sleep 1
            continue
        fi

        if [ "$choice" -eq 0 ]; then
            echo "__skip__"
            return 0
        fi
        if [ "$choice" -eq 1 ]; then
            echo "__custom_git__"
            return 0
        fi

        # Random — a truly random template from the whole catalog, previewed first
        if [ "$choice" -eq "$i" ]; then
            local random_tpl
            random_tpl=$(pick_random_template_id) || random_tpl=""
            if [ -n "$random_tpl" ]; then
                if ! show_template_preview "$random_tpl"; then
                    continue
                fi
                echo "__random_tpl__:${random_tpl}"
                return 0
            fi
            if [ ${#cats[@]} -gt 0 ]; then
                echo "${cats[$((RANDOM % ${#cats[@]}))]}"
                return 0
            fi
            log_warning "$(t invalid_choice)"
            sleep 1
            continue
        fi

        if [ "$choice" -ge 2 ] && [ "$choice" -lt "$i" ]; then
            echo "${cats[$((choice-2))]}"
            return 0
        fi

        log_warning "$(t invalid_choice)"
        sleep 1
    done
}

# ── Interactive template picker (paged, searchable) ───────────────────
# Categories hold thousands of templates now: 25 per page, n/p paging, any
# word = search by name/id, r = random from the current view, 0 = back.
# Returns the template id, or "__back__" to return to the category menu.
select_template() {
    local cat_id="$1"
    local cat_raw_name cat_name
    cat_raw_name=$(get_category_name "$cat_id")
    cat_name=$(localized_category_name "$cat_id" "$cat_raw_name")

    local ids=() names=() srcs=() id name source preview
    while IFS='|' read -r id name source preview; do
        [ -n "$id" ] || continue
        ids+=("$id"); names+=("$name"); srcs+=("$source")
    done < <(get_templates_by_category "$cat_id")
    if [ ${#ids[@]} -eq 0 ]; then
        log_info "$(t templates_cat_empty)"
        echo "__back__"
        return 0
    fi

    local per="$TEMPLATES_PAGE_SIZE" page=0 filter="" view=() k choice total pages start end n lc_filter
    while true; do
        view=()
        lc_filter="${filter,,}"
        for k in "${!ids[@]}"; do
            if [ -n "$filter" ]; then
                [[ "${names[$k],,}" == *"$lc_filter"* || "${ids[$k],,}" == *"$lc_filter"* ]] || continue
            fi
            view+=("$k")
        done
        total=${#view[@]}
        pages=$(( (total + per - 1) / per )); [ "$pages" -lt 1 ] && pages=1
        [ "$page" -ge "$pages" ] && page=$((pages-1))
        [ "$page" -lt 0 ] && page=0
        start=$((page*per)); end=$((start+per)); [ "$end" -gt "$total" ] && end=$total

        echo "" >&2
        echo -e "  ${BOLD}${WHITE}$(tf templates_list "$cat_name")${NC}" >&2
        if [ -n "$filter" ]; then
            echo -e "  ${DIM}$(tf templates_filter_line "$filter" "$total")${NC}" >&2
        fi
        echo -e "  ${DIM}$(printf '─%.0s' {1..60})${NC}" >&2
        printf "  ${CYAN}%4d)${NC} ${YELLOW}%s${NC}\n" 0 "$(t back)" >&2
        if [ "$total" -eq 0 ]; then
            echo -e "  ${DIM}$(t templates_nothing_found)${NC}" >&2
        fi
        n=$start
        while [ "$n" -lt "$end" ]; do
            k=${view[$n]}
            printf "  ${CYAN}%4d)${NC} %-38s ${DIM}[%s]${NC}\n" "$((n+1))" "${names[$k]:0:38}" "${srcs[$k]}" >&2
            n=$((n+1))
        done
        echo -e "  ${DIM}$(printf '─%.0s' {1..60})${NC}" >&2
        echo -e "  ${DIM}$(tf templates_page_line "$((page+1))" "$pages" "$total")${NC}" >&2
        echo -e "  ${DIM}$(t templates_nav_hint)${NC}" >&2
        echo -ne "  ${WHITE}$(t choose):${NC} " >&2
        read -r choice || { echo "__back__"; return 0; }
        choice="${choice#"${choice%%[![:space:]]*}"}"   # ltrim
        case "$choice" in
            0)
                echo "__back__"; return 0 ;;
            "")
                continue ;;
            n|N|+|.)
                [ "$page" -lt $((pages-1)) ] && page=$((page+1)); continue ;;
            p|P|-|,)
                [ "$page" -gt 0 ] && page=$((page-1)); continue ;;
            r|R)
                [ "$total" -eq 0 ] && continue
                k=${view[$(( RANDOM % total ))]}
                show_template_preview "${ids[$k]}" || continue
                echo "${ids[$k]}"; return 0 ;;
            /*)
                filter="${choice#/}"; page=0; continue ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]]; then
                    if [ "$choice" -ge 1 ] && [ "$choice" -le "$total" ]; then
                        k=${view[$((choice-1))]}
                        show_template_preview "${ids[$k]}" || continue
                        echo "${ids[$k]}"; return 0
                    fi
                    log_warning "$(t invalid_choice)"
                    sleep 1
                    continue
                fi
                # any other word — search by name
                filter="$choice"; page=0; continue ;;
        esac
    done
}

# ── Template preview ───────────────────────────────────────────────────
show_template_preview() {
    local tpl_id="$1"
    local info
    info=$(get_template_info "$tpl_id")
    [ -n "$info" ] || { log_error "$(tf templates_not_in_catalog "$tpl_id")"; return 1; }

    local name source preview_url repo_url description license
    name=$(echo "$info" | jq -r '.name // empty')
    source=$(echo "$info" | jq -r '.source // empty')
    preview_url=$(echo "$info" | jq -r '.preview_url // empty')
    repo_url=$(echo "$info" | jq -r '.repo_url // empty')
    description=$(echo "$info" | jq -r '.description // "—"')
    license=$(echo "$info" | jq -r '.license // empty')

    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t templates_preview_title)${NC}" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
    echo -e "  ${WHITE}$(t templates_name)${NC}  $name" >&2
    echo -e "  ${WHITE}$(t templates_source)${NC}  $source" >&2
    echo -e "  ${WHITE}$(t templates_description)${NC}  $description" >&2
    [ -n "$license" ] && echo -e "  ${WHITE}$(t templates_license)${NC}  $license" >&2

    if [ -n "$preview_url" ]; then
        echo "" >&2
        echo -e "  ${GREEN}$(t templates_preview)${NC} ${CYAN}${preview_url}${NC}" >&2
        echo -e "  ${DIM}$(t templates_preview_hint)${NC}" >&2
    fi
    if [ -n "$repo_url" ]; then
        echo -e "  ${DIM}$(t templates_repo)    ${repo_url}${NC}" >&2
    fi

    # Thanks — for the generic "github" source thank the repo owner, not "github"
    local thanks="$source"
    if [ "$source" = "github" ] && [[ "$repo_url" =~ github\.com/([^/]+)/ ]]; then thanks="${BASH_REMATCH[1]}"; fi
    echo "" >&2
    echo -e "  ${MAGENTA}$(tf templates_thanks "$thanks")${NC}" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
    echo "" >&2

    if ! confirm "$(t templates_install_this)"; then
        return 1
    fi
    return 0
}

# ── Full interactive template selection ───────────────────────────────
# Codex 029 P2: outer loop so "back" from template picker re-prompts the
# category menu. Returns:
#   0 + stdout = path        → template ready (downloaded + verified)
#   0 + stdout = "__skip__"  → user explicitly chose the stub site
#   1                        → real failure (catalog missing, gave up)
interactive_template_selection() {
    load_catalog || return 1
    # Create the download cache HERE (parent shell): download_template runs in
    # $(...) and would otherwise mktemp a fresh dir per attempt that nobody cleans.
    _templates_cache_dir >/dev/null || return 1

    local attempt=0
    while true; do
        local cat_id
        cat_id=$(select_category) || return 1

        if [ "$cat_id" = "__skip__" ]; then
            echo "__skip__"
            return 0
        fi

        if [ "$cat_id" = "__custom_git__" ]; then
            local template_dir
            if template_dir=$(download_custom_git_template) && [ -n "$template_dir" ]; then
                echo "$template_dir"
                return 0
            fi
            attempt=$((attempt+1))
            [ "$attempt" -ge 5 ] && return 1
            continue
        fi

        if [[ "$cat_id" == __random_tpl__:* ]]; then
            local rnd_id="${cat_id#__random_tpl__:}"
            local template_dir
            if template_dir=$(download_template "$rnd_id") && [ -n "$template_dir" ]; then
                echo "$template_dir"
                return 0
            fi
            attempt=$((attempt+1))
            [ "$attempt" -ge 5 ] && return 1
            continue
        fi

        # A failed download (a repo without a ready page) used to throw the
        # operator back to the category menu — now stay in the same list.
        while true; do
            local tpl_id template_dir again
            tpl_id=$(select_template "$cat_id") || return 1
            [ "$tpl_id" = "__back__" ] && break
            if template_dir=$(download_template "$tpl_id") && [ -n "$template_dir" ]; then
                echo "$template_dir"
                return 0
            fi
            attempt=$((attempt+1))
            [ "$attempt" -ge 5 ] && return 1
            echo -ne "  ${WHITE}$(t templates_try_another) [Y/n]:${NC} " >&2
            read -r again || again="n"
            case "$again" in n|N|н|Н|2) break ;; esac
        done
    done
}

# ═══════════════════════════════════════════════════════════════════════
# Site management (Pro): current template, apply, restore — shared by the
# SSH menu, the govlessctl daemon (bot + Mini App) and `govless --site-template-*`.
# ═══════════════════════════════════════════════════════════════════════

site_is_pro() {
    local mode domain
    mode=$(config_get mode "" 2>/dev/null || true)
    domain=$(config_get domain "" 2>/dev/null || true)
    [ "$mode" = "pro" ] && [ -n "$domain" ]
}

site_domain() { config_get domain "" 2>/dev/null || true; }

# Prints "<id>\t<source-url>" of the site currently in WEBSITE_ROOT.
# id = catalog id | custom_<hash> | __stub__ | __unknown__ | __none__
site_template_current() {
    local root="${WEBSITE_ROOT:-/var/www/html}" id="" src=""
    if [ -f "$root/.govless_template_id" ]; then
        id=$(head -1 "$root/.govless_template_id" 2>/dev/null | tr -d '[:space:]')
    fi
    [ -f "$root/.govless_template_source" ] && src=$(head -1 "$root/.govless_template_source" 2>/dev/null | tr -d '\r\n')
    if [ -z "$id" ]; then
        if [ -f "$root/index.html" ]; then
            if grep -q 'This server is configured and operating normally' "$root/index.html" 2>/dev/null; then
                id="$SITE_STUB_ID"
            else
                id="__unknown__"
            fi
        else
            id="__none__"
        fi
    fi
    printf '%s\t%s\n' "$id" "$src"
}

# Human-readable label of the current template (menu + status)
site_template_label() {
    local cur id src name
    cur=$(site_template_current)
    id="${cur%%	*}"; src="${cur#*	}"
    case "$id" in
        "$SITE_STUB_ID") echo "$(t site_current_stub)" ;;
        __unknown__)     echo "$(t site_current_unknown)" ;;
        __none__)        echo "$(t site_current_none)" ;;
        custom_*)        echo "$(tf site_current_custom "${src:-git}")" ;;
        *)
            name=$(get_template_field "$id" name 2>/dev/null || true)
            if [ -n "$name" ]; then echo "$name  (${id})"; else echo "$id"; fi ;;
    esac
}

site_print_current() {
    local domain
    domain=$(site_domain)
    echo -e "  $(tf site_domain_line "${CYAN}${domain}${NC}")" >&2
    echo -e "  $(tf site_url_line "${CYAN}${domain}${NC}")" >&2
    echo -e "  $(tf site_current_line "${GREEN}$(site_template_label)${NC}")" >&2
    echo "" >&2
}

_site_lock() {
    command -v flock >/dev/null 2>&1 || return 0
    mkdir -p "$(dirname "$SITE_LOCK_FILE")" 2>/dev/null || return 0
    # NB: a command-less `exec` applies its redirections PERMANENTLY to the
    # whole process, so `exec 9>>... 2>/dev/null` silenced stderr for the rest
    # of the run — and the entire goVLESS UI (menu prompts, results, errors)
    # goes to stderr. Keep the 2>/dev/null scoped to the group command.
    { exec 9>>"$SITE_LOCK_FILE"; } 2>/dev/null || return 0
    if ! flock -n 9; then
        SITE_RESULT_ERROR_CODE="busy"
        SITE_RESULT_ERROR="$(t site_busy)"
        log_error "$(t site_busy)"
        return 1
    fi
    return 0
}

_site_reload_nginx() {
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        return 0
    fi
    log_warning "$(t site_nginx_reload_fail)"
    return 1
}

# HTTP check through nginx on :80 with the site's Host header (xray on :443
# falls back to the same server block). Prints the status code.
# Как посетитель увидит сайт: настоящий HTTPS на домене, а не петля.
# Раньше проверка била в http://127.0.0.1/ с подставленным Host — тот же «200»
# приходил и на несуществующий домен, то есть «Сайт отвечает: HTTP 200» ничего
# не доказывал (QA BUG-30). Петля осталась запасным вариантом: на некоторых VPS
# запрос к собственному внешнему адресу не проходит (hairpin NAT).
site_http_code() {
    local domain code
    domain=$(site_domain)
    if [ -n "$domain" ]; then
        code=$(curl -s -o /dev/null -m 12 -w '%{http_code}' "https://${domain}/" 2>/dev/null) || code="000"
        [ "$code" != "000" ] && { printf '%s' "$code"; return 0; }
        log_dim "$(t site_check_loopback)"
    fi
    curl -s -o /dev/null -m 8 -w '%{http_code}' -H "Host: ${domain:-localhost}" http://127.0.0.1/ 2>/dev/null || echo "000"
}

# Deploy a verified template dir, reload nginx, persist id, verify HTTP.
_site_finalize() {
    local dir="$1" id="${2:-}"
    [ -n "$id" ] || id=$(basename "$dir")
    SITE_RESULT_ID="$id"
    SITE_RESULT_NAME=$(get_template_field "$id" name 2>/dev/null || true)
    if [ -z "$SITE_RESULT_NAME" ]; then
        # Human-readable name for non-catalog sites (bot/Mini App show it — QA E7
        # showed the raw "__stub__" id)
        case "$id" in
            "$SITE_STUB_ID") SITE_RESULT_NAME="$(t site_current_stub)" ;;
            custom_*)
                SITE_RESULT_NAME=$(head -1 "$dir/.custom_git_source" 2>/dev/null || true)
                [ -n "$SITE_RESULT_NAME" ] || SITE_RESULT_NAME=$(head -1 "$dir/.govless_template_source" 2>/dev/null || true)
                ;;
        esac
    fi
    [ -n "$SITE_RESULT_NAME" ] || SITE_RESULT_NAME="$id"
    if ! deploy_template_to_nginx "$dir" "$id"; then
        SITE_RESULT_ERROR_CODE="deploy_failed"
        SITE_RESULT_ERROR="$(t site_apply_failed)"
        log_error "$(t site_apply_failed)"
        return 1
    fi
    _site_reload_nginx || true
    config_set "site_template" "$id" 2>/dev/null || true
    SITE_RESULT_HTTP=$(site_http_code)
    log_success "$(tf site_apply_done "$SITE_RESULT_NAME")"
    [ -n "${LAST_SITE_BACKUP:-}" ] && log_dim "$(tf site_backup_line "$LAST_SITE_BACKUP")"
    if [ "$SITE_RESULT_HTTP" = "200" ]; then
        log_info "$(tf site_apply_url "$(site_domain)")"
    else
        log_warning "$(tf site_check_fail "$SITE_RESULT_HTTP")"
    fi
    return 0
}

_site_reset_result() {
    SITE_RESULT_ID=""; SITE_RESULT_NAME=""; SITE_RESULT_ERROR_CODE=""; SITE_RESULT_ERROR=""; SITE_RESULT_HTTP=""
    LAST_SITE_BACKUP=""
}

_site_require_pro() {
    if ! site_is_pro; then
        SITE_RESULT_ERROR_CODE="not_pro"
        SITE_RESULT_ERROR="$(t website_only_pro)"
        log_warning "$(t website_only_pro)"
        log_dim "$(t site_hint_switch_pro)"
        return 1
    fi
    return 0
}

_site_download_error() {
    # Fill SITE_RESULT_* from the last download attempt
    local cache="${TEMPLATES_CACHE:-}"
    local code="" detail=""
    [ -n "$cache" ] && [ -f "$cache/.last_error" ] && code=$(cat "$cache/.last_error" 2>/dev/null)
    [ -n "$cache" ] && [ -f "$cache/.last_detail" ] && detail=$(cat "$cache/.last_detail" 2>/dev/null)
    [ -n "$cache" ] && rm -f "$cache/.last_detail" 2>/dev/null
    [ -n "$code" ] || code="${TEMPLATE_DL_ERROR:-download_failed}"
    SITE_RESULT_ERROR_CODE="$code"
    case "$code" in
        app_shell)       SITE_RESULT_ERROR="$(tf templates_app_shell "$1")" ;;
        broken_assets)   SITE_RESULT_ERROR="$(tf templates_broken_assets "$1")" ;;
        no_index)        SITE_RESULT_ERROR="$(tf templates_not_static "$1")" ;;
        not_found)       SITE_RESULT_ERROR="$(tf templates_not_in_catalog "$1")" ;;
        invalid_id|bad_url) SITE_RESULT_ERROR="$(t custom_git_bad_url)" ;;
        too_big)         SITE_RESULT_ERROR="$(tf custom_git_too_big "${detail:-${CUSTOM_GIT_MAX_SIZE_MB}MB}")" ;;
        timeout)         SITE_RESULT_ERROR="$(tf custom_git_timeout "$CUSTOM_GIT_CLONE_TIMEOUT")" ;;
        *)               SITE_RESULT_ERROR="$(tf templates_download_failed "$1")" ;;
    esac
}

# site_template_apply <catalog id>
site_template_apply() {
    local id="${1:-}"
    _site_reset_result
    _site_require_pro || return 1
    load_catalog || { SITE_RESULT_ERROR_CODE="no_catalog"; SITE_RESULT_ERROR="$(tf templates_catalog_not_found "$CATALOG_FILE")"; return 1; }
    if ! _tpl_id_ok "$id"; then
        SITE_RESULT_ERROR_CODE="invalid_id"; SITE_RESULT_ERROR="Invalid template id"
        log_error "$(tf tplcat_bad_id "${id:-<empty>}")"
        return 1
    fi
    _site_lock || return 1
    local name dir
    name=$(get_template_field "$id" name 2>/dev/null || true)
    [ -n "$name" ] || name="$id"
    log_step "$(tf site_apply_start "$name")"
    _templates_cache_dir >/dev/null || return 1
    if ! dir=$(download_template "$id") || [ -z "$dir" ]; then
        _site_download_error "$name"
        log_error "$(t site_apply_failed)"
        return 1
    fi
    _site_finalize "$dir" "$id"
}

# site_template_apply_custom <https git url>
site_template_apply_custom() {
    local url="${1:-}"
    _site_reset_result
    _site_require_pro || return 1
    _site_lock || return 1
    log_step "$(tf site_apply_start "$url")"
    _templates_cache_dir >/dev/null || return 1
    local dir
    if ! dir=$(download_custom_git_template "$url") || [ -z "$dir" ]; then
        _site_download_error "$url"
        log_error "$(t site_apply_failed)"
        return 1
    fi
    _site_finalize "$dir" "$(basename "$dir")"
}

# site_template_apply_random — up to 3 different catalog entries
site_template_apply_random() {
    _site_reset_result
    _site_require_pro || return 1
    load_catalog || { SITE_RESULT_ERROR_CODE="no_catalog"; SITE_RESULT_ERROR="$(tf templates_catalog_not_found "$CATALOG_FILE")"; return 1; }
    _site_lock || return 1
    _templates_cache_dir >/dev/null || return 1
    local attempt id dir name
    for attempt in 1 2 3; do
        id=$(pick_random_template_id) || id=""
        [ -n "$id" ] || continue
        name=$(get_template_field "$id" name 2>/dev/null || true)
        log_step "$(tf site_apply_start "${name:-$id}")"
        if dir=$(download_template "$id") && [ -n "$dir" ]; then
            _site_finalize "$dir" "$id"
            return $?
        fi
        _site_download_error "${name:-$id}"
        [ "$attempt" -lt 3 ] && log_info "$(t site_random_retry)"
    done
    log_error "$(t site_apply_failed)"
    return 1
}

# site_template_apply_stub — built-in page, no download
site_template_apply_stub() {
    _site_reset_result
    _site_require_pro || return 1
    _site_lock || return 1
    local stub_dir
    stub_dir=$(mktemp -d "${TMPDIR:-/tmp}/govless_stub.XXXXXXXX") || return 1
    WEBSITE_ROOT="$stub_dir" deploy_stub_site >/dev/null 2>&1 || true
    if [ ! -f "$stub_dir/index.html" ]; then
        rm -rf "$stub_dir"
        SITE_RESULT_ERROR_CODE="deploy_failed"; SITE_RESULT_ERROR="$(t site_apply_failed)"
        return 1
    fi
    rm -f "$stub_dir/.govless-owned" 2>/dev/null || true
    local rc=0
    _site_finalize "$stub_dir" "$SITE_STUB_ID" || rc=1
    rm -rf "$stub_dir"
    [ "$rc" -eq 0 ] && log_success "$(t site_stub_done)"
    return $rc
}

# ── Site backups (created by deploy_template_to_nginx) ────────────────
site_backups_list() {
    [ -d "$SITE_BACKUP_DIR" ] || return 0
    ls -t "$SITE_BACKUP_DIR"/site_*.tgz 2>/dev/null || true
}

# site_restore_backup <file.tgz>
site_restore_backup() {
    local file="${1:-}"
    _site_reset_result
    _site_require_pro || return 1
    case "$file" in
        "$SITE_BACKUP_DIR"/site_*.tgz) ;;
        *) SITE_RESULT_ERROR_CODE="bad_path"; SITE_RESULT_ERROR="$(t site_restore_failed)"; log_error "$(t site_restore_failed)"; return 1 ;;
    esac
    [ -f "$file" ] || { SITE_RESULT_ERROR_CODE="not_found"; SITE_RESULT_ERROR="$(t site_restore_failed)"; log_error "$(t site_restore_failed)"; return 1; }
    _site_lock || return 1
    local stage
    stage=$(mktemp -d "${TMPDIR:-/tmp}/govless_site_restore.XXXXXXXX") || return 1
    if ! tar -xzf "$file" -C "$stage" 2>/dev/null || [ ! -f "$stage/index.html" ]; then
        rm -rf "$stage"
        SITE_RESULT_ERROR_CODE="restore_failed"; SITE_RESULT_ERROR="$(t site_restore_failed)"
        log_error "$(t site_restore_failed)"
        return 1
    fi
    local id
    id=$(head -1 "$stage/.govless_template_id" 2>/dev/null | tr -d '[:space:]') || id=""
    [ -n "$id" ] || id="__unknown__"
    rm -f "$stage/.govless-owned" 2>/dev/null || true
    local rc=0
    _site_finalize "$stage" "$id" || rc=1
    rm -rf "$stage"
    [ "$rc" -eq 0 ] && log_success "$(tf site_restore_done "$(basename "$file")")"
    return $rc
}

# ── Interactive helpers for the SSH menu ───────────────────────────────
site_change_from_catalog() {
    _site_reset_result
    _site_require_pro || return 1
    load_catalog || return 1
    _templates_cache_dir >/dev/null || return 1
    local out
    # From the menu "0" means Back, not "install the stub" (QA BUG-04): the stub
    # has its own explicit menu item with a confirmation.
    if ! out=$(TEMPLATES_ZERO_IS_BACK=1 interactive_template_selection); then
        log_warning "$(t site_change_cancelled)"
        return 1
    fi
    if [ "$out" = "__skip__" ]; then
        log_info "$(t site_change_cancelled)"
        return 0
    fi
    _site_lock || return 1
    _site_finalize "$out" "$(basename "$out")"
}

site_change_custom_git() {
    _site_reset_result
    _site_require_pro || return 1
    _templates_cache_dir >/dev/null || return 1
    local dir
    if ! dir=$(download_custom_git_template) || [ -z "$dir" ]; then
        log_warning "$(t site_change_cancelled)"
        return 1
    fi
    _site_lock || return 1
    _site_finalize "$dir" "$(basename "$dir")"
}

site_restore_menu() {
    _site_require_pro || return 1
    local -a backups=()
    local f
    while IFS= read -r f; do [ -n "$f" ] && backups+=("$f"); done < <(site_backups_list)
    if [ ${#backups[@]} -eq 0 ]; then
        log_info "$(t site_restore_none)"
        return 0
    fi
    echo "" >&2
    echo -e "  ${BOLD}$(t site_restore_pick)${NC}" >&2
    local i=1 size
    for f in "${backups[@]}"; do
        size=$(du -h "$f" 2>/dev/null | cut -f1)
        echo -e "    ${CYAN}${i})${NC} $(basename "$f")  ${DIM}${size}${NC}" >&2
        i=$((i+1))
    done
    echo -e "    ${CYAN}0)${NC} $(t back)" >&2
    local pick
    read -rp "  ▸ " pick || return 0
    [ "$pick" = "0" ] && return 0
    if [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#backups[@]}" ]; then
        f="${backups[$((pick-1))]}"
        confirm "$(tf site_restore_confirm "$(basename "$f")")" || return 0
        site_restore_backup "$f"
    else
        log_warning "$(t invalid_choice)"
    fi
}

site_check() {
    _site_require_pro || return 1
    local code
    code=$(site_http_code)
    if [ "$code" = "200" ] && [ -f "${WEBSITE_ROOT:-/var/www/html}/index.html" ]; then
        log_success "$(tf site_check_ok "$code")"
        log_info "$(tf site_apply_url "$(site_domain)")"
        return 0
    fi
    log_error "$(tf site_check_fail "$code")"
    return 1
}

# After Lite→Pro switch: offer to pick a cover-site template right away
site_offer_template_after_switch() {
    site_is_pro || return 0
    echo "" >&2
    if confirm "$(t site_offer_after_switch)"; then
        site_change_from_catalog || true
    else
        log_info "$(t ms_tpl_hint)"
    fi
    return 0
}

# ── Non-interactive CLI entry (govlessctl daemon / scripts) ──────────
# govless.sh --site-template-status [--json]
# govless.sh --site-template-apply <id> [--json]
# govless.sh --site-template-apply-url <https-url> [--json]
# govless.sh --site-template-random [--json]
# govless.sh --site-template-stub [--json]
# govless.sh --site-template-restore <file.tgz> [--json]
# --json: exactly one JSON object on stdout (all human output goes to stderr).
_site_json_result() {
    local ok="$1" action="$2"
    local cur id src
    cur=$(site_template_current); id="${cur%%	*}"; src="${cur#*	}"
    jq -cn \
        --argjson ok "$ok" \
        --arg action "$action" \
        --arg mode "$(config_get mode "" 2>/dev/null || true)" \
        --arg domain "$(site_domain)" \
        --arg template_id "$SITE_RESULT_ID" \
        --arg name "$SITE_RESULT_NAME" \
        --arg current_id "$id" \
        --arg current_source "$src" \
        --arg error_code "$SITE_RESULT_ERROR_CODE" \
        --arg error "$SITE_RESULT_ERROR" \
        --arg http_code "$SITE_RESULT_HTTP" \
        --arg backup "${LAST_SITE_BACKUP:-}" \
        '{ok:$ok, action:$action, mode:$mode, domain:$domain, template_id:$template_id, name:$name,
          current_id:$current_id, current_source:$current_source,
          error_code:$error_code, error:$error, http_code:$http_code, backup:$backup}' 2>/dev/null \
        || printf '{"ok":%s,"action":"%s","error_code":"json"}\n' "$ok" "$action"
}

site_template_cli() {
    local action="${1:-}"; shift || true
    local json=0 arg="" a
    for a in "$@"; do
        case "$a" in
            --json) json=1 ;;
            *) [ -z "$arg" ] && arg="$a" ;;
        esac
    done
    # GOVLESS_LANG: the daemon passes the client's language (bot = en, Mini App =
    # user's) so job messages aren't a RU/EN mix (QA BUG-06); default = saved language.
    case "${GOVLESS_LANG:-}" in
        ru|en) load_language "$GOVLESS_LANG" >/dev/null 2>&1 || true ;;
        *)     load_language "$(detect_language)" >/dev/null 2>&1 || true ;;
    esac
    sync_templates_catalog >/dev/null 2>&1 || true
    _site_reset_result
    local rc=0
    case "$action" in
        --site-template-status)
            site_is_pro || { SITE_RESULT_ERROR_CODE="not_pro"; SITE_RESULT_ERROR="$(t website_only_pro)"; }
            if [ "$json" -eq 0 ]; then
                if site_is_pro; then site_print_current; else log_warning "$(t website_only_pro)"; fi
            fi
            ;;
        --site-template-apply)      site_template_apply "$arg" || rc=1 ;;
        --site-template-apply-url)  site_template_apply_custom "$arg" || rc=1 ;;
        --site-template-random)     site_template_apply_random || rc=1 ;;
        --site-template-stub)       site_template_apply_stub || rc=1 ;;
        --site-template-restore)    site_restore_backup "$arg" || rc=1 ;;
        *)
            SITE_RESULT_ERROR_CODE="bad_action"; SITE_RESULT_ERROR="unknown action: $action"; rc=2 ;;
    esac
    if [ "$json" -eq 1 ]; then
        if [ "$rc" -eq 0 ]; then _site_json_result true "$action"; else _site_json_result false "$action"; fi
    fi
    return $rc
}
