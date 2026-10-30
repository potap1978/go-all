#!/bin/bash
# GoTelegram v2.5.0 — i18n engine
# Internationalization support: EN (English) / RU (Русский)
#
# Usage:
#   source lib/i18n.sh
#   load_language "ru"         # or "en"
#   echo "$(t menu_install)"   # translated string
#   printf "$(t greeting)\n" "$name"   # with format args

# ── Global i18n state ──
declare -gA I18N
LANG_CODE="${LANG_CODE:-en}"
LANG_FILE=""

# ── Load a language ──
# Sources lib/lang/${lang}.sh into the I18N associative array.
# Falls back to English if requested language file is missing.
load_language() {
    local lang="${1:-en}"
    # Sanitize: only allow [a-z]{2} codes
    if ! [[ "$lang" =~ ^[a-z]{2}$ ]]; then
        lang="en"
    fi

    local lang_dir
    lang_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lang"
    local lang_file="${lang_dir}/${lang}.sh"

    if [ ! -f "$lang_file" ]; then
        lang_file="${lang_dir}/en.sh"
        lang="en"
    fi

    if [ -f "$lang_file" ]; then
        # Clear previous keys then source the new language
        I18N=()
        # shellcheck disable=SC1090
        source "$lang_file"
        LANG_CODE="$lang"
        LANG_FILE="$lang_file"
        return 0
    fi
    return 1
}

# ── Translate: fetch value by key ──
# t <key>   → echoes translation (or key if missing)
t() {
    local key="$1"
    local val="${I18N[$key]:-}"
    if [ -z "$val" ]; then
        # Fallback to key name so missing translations are visible
        echo "$key"
    else
        echo "$val"
    fi
}

# ── Translate + printf-style formatting ──
# tf <key> <arg1> <arg2> ...
tf() {
    local key="$1"
    shift
    local fmt="${I18N[$key]:-$key}"
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# ── Get current language code ──
get_language() {
    echo "$LANG_CODE"
}

# ── Detect saved language from config.json, default en ──
detect_language() {
    local cfg="${GOTELEGRAM_CONFIG:-/opt/gotelegram/config.json}"
    local lang=""
    if [ -f "$cfg" ] && command -v jq >/dev/null 2>&1; then
        lang=$(jq -r '.language // empty' "$cfg" 2>/dev/null)
    fi
    # Also check marker file (language set before config.json exists)
    if [ -z "$lang" ]; then
        local marker="${GOTELEGRAM_DIR:-/opt/gotelegram}/.language"
        if [ -f "$marker" ]; then
            lang=$(head -c 2 "$marker" 2>/dev/null | tr -d '[:space:]')
        fi
    fi
    # Sanitize
    if ! [[ "$lang" =~ ^(en|ru)$ ]]; then
        lang="en"
    fi
    echo "$lang"
}

# ── Persist selected language ──
# Saves to config.json if present, otherwise to marker file
save_language() {
    local lang="$1"
    if ! [[ "$lang" =~ ^(en|ru)$ ]]; then
        return 1
    fi
    mkdir -p "${GOTELEGRAM_DIR:-/opt/gotelegram}" 2>/dev/null
    # Always write marker for early-access (before config.json exists)
    echo "$lang" > "${GOTELEGRAM_DIR:-/opt/gotelegram}/.language" 2>/dev/null

    local cfg="${GOTELEGRAM_CONFIG:-/opt/gotelegram/config.json}"
    if [ -f "$cfg" ] && command -v jq >/dev/null 2>&1; then
        local tmp
        tmp=$(mktemp) || return 1
        if jq --arg lang "$lang" '. + {language: $lang}' "$cfg" > "$tmp" 2>/dev/null; then
            mv "$tmp" "$cfg"
            chmod 600 "$cfg"
        else
            rm -f "$tmp"
        fi
    fi
    return 0
}

# ── First-run interactive language picker ──
# Shows a minimal, language-agnostic picker (keeps it culture-neutral).
# Returns the chosen code via echo.
pick_language_interactive() {
    echo "" >&2
    echo "  ┌──────────────────────────────────────────┐" >&2
    echo "  │  Select language / Выберите язык         │" >&2
    echo "  ├──────────────────────────────────────────┤" >&2
    echo "  │  1) English                              │" >&2
    echo "  │  2) Русский                              │" >&2
    echo "  └──────────────────────────────────────────┘" >&2
    echo -n "  > " >&2
    local ch
    read -r ch
    case "$ch" in
        1|en|EN|english|English) echo "en" ;;
        2|ru|RU|russian|Russian|русский) echo "ru" ;;
        *) echo "en" ;;
    esac
}
