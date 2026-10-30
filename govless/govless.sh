#!/bin/bash
# goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
# Licensed under the goVLESS Source-Available License (see the LICENSE file).
# Redistribution, mirroring, or republishing in any form — whole or partial,
# modified or not — is prohibited without prior written permission.

# ╔═══════════════════════════════════════════════════════════════╗
# ║  goVLESS v${GOVLESS_VERSION} — 3X-UI VPN installer with stealth masking  ║
# ║  Lite: VLESS + Reality (masquerade as popular site)          ║
# ║  Pro:  VLESS + TLS (your domain + real website)              ║
# ║                                                               ║
# ║  github.com/potap1978 • YouTube: potap1978                     ║
# ╚═══════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── Resolve script directory ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# ── Source modules ──────────────────────────────────────────────────────
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/i18n.sh"
source "${SCRIPT_DIR}/lib/xui.sh"
source "${SCRIPT_DIR}/lib/xui_api.sh"
source "${SCRIPT_DIR}/lib/reality_domains.sh"
source "${SCRIPT_DIR}/lib/website.sh"
source "${SCRIPT_DIR}/lib/templates_catalog.sh"
source "${SCRIPT_DIR}/lib/mode_switch.sh"
source "${SCRIPT_DIR}/lib/egress.sh"
source "${SCRIPT_DIR}/lib/migrate.sh"

# ── Cleanup trap ────────────────────────────────────────────────────────
trap cleanup_temp_files EXIT

# ── Root check ──────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${RED}✗${NC} This script must be run as root / Запустите от root"
    exit 1
fi

# ── Detect or pick language ─────────────────────────────────────────────
init_language() {
    local lang picked=""
    lang=$(detect_language)
    if [ "$lang" = "en" ] && [ ! -f "${GOVLESS_DIR}/.language" ]; then
        # First run — ask user. Persist the choice ONLY if the user actually made
        # one; on an empty/EOF read (broken tty) keep English for THIS run but do
        # NOT save it, so the next run asks again (fixes the "sticky en" bug).
        picked=$(pick_language_interactive)
        if [ -n "$picked" ]; then
            lang="$picked"
            load_language "$lang"
            save_language "$lang"
        else
            load_language "$lang"
        fi
        init_state_db 2>/dev/null || true
        return 0
    fi
    load_language "$lang"
    save_language "$lang"
    # Initialize state.db (idempotent) — for Telegram bot bindings
    init_state_db 2>/dev/null || true
}

# ═══════════════════════════════════════════════════════════════════════
# INSTALL FLOW — LITE MODE (Reality)
# ═══════════════════════════════════════════════════════════════════════
install_lite() {
    log_step "$(t install_lite_step)"

    local server_ip
    server_ip=$(get_server_ip) || { log_error "$(t common_no_ip)"; return 1; }

    # 1. Select masquerade domain
    local mask_domain
    mask_domain=$(select_reality_domain "$server_ip") || return 1

    # Select 3X-UI version (Legacy 2.x or New 3.x) — but only if not
    # already installed. Otherwise install_3xui will short-circuit and the
    # user's choice would silently mismatch the actual binary (Codex 012 P2).
    if xui_already_installed; then
        load_xui_version_from_config
        log_dim "3X-UI already installed (${XUI_BRANCH:-?} ${XUI_INSTALL_VERSION:-?}) — keeping it"
    else
        select_xui_version
    fi

    # 3. Select transport protocol (TCP / XHTTP / gRPC)
    select_transport
    select_fingerprint

    # 4. Ask how many keys
    echo ""
    echo -ne "  $(t users_ask_count) $(t users_ask_count_hint): "
    local users_count_input
    read -r users_count_input || users_count_input=""
    local users_count="${users_count_input:-3}"
    # Validate: must be number 1-100
    if ! [[ "$users_count" =~ ^[0-9]{1,3}$ ]] || [ "$users_count" -lt 1 ] || [ "$users_count" -gt 100 ]; then
        users_count=3
    fi

    # 5. Show config summary (all choices visible before confirm)
    print_header "$(t config_title)"
    echo -e "  $(t config_ip)       ${CYAN}${server_ip}${NC}"
    echo -e "  $(t config_port)     ${CYAN}443${NC}"
    echo -e "  $(t config_mode)     ${CYAN}Lite (Reality)${NC}"
    echo -e "  $(t config_mask)     ${CYAN}${mask_domain}${NC}"
    echo -e "  $(t config_transport) ${CYAN}${XUI_TRANSPORT^^}${NC}"
    echo -e "  $(t config_users)    ${CYAN}${users_count}${NC}"
    echo ""

    confirm "$(t config_confirm)" "$(t confirm_install)" "$(t confirm_back)" || return 0

    # 7. Install dependencies
    install_dependencies || return 1

    # Re-init state.db now that sqlite3 is guaranteed (first call in
    # init_language ran before deps install, may have been a no-op)
    init_state_db 2>/dev/null || true

    # 8. Install 3X-UI (critical — must succeed)
    install_3xui || return 1
    # Свежая установка панели останавливает govlessctl/govless-bot (чтобы не
    # подхватить старые креды) и не поднимала их обратно (QA S-13).
    for _u in govlessctl govless-bot; do
        systemctl cat "$_u" >/dev/null 2>&1 && systemctl start "$_u" >/dev/null 2>&1 || true
    done

    # B1/B2/B3 fix: enable panel TLS + bind sub server + lang via sqlite
    configure_panel_tls "${LANG_CODE:-en}" || log_warning "$(t panel_tls_warn)"


    # 9. Extract credentials & setup API (critical for panel access)
    extract_credentials
    save_credentials
    setup_api_base

    # === Auto-configuration (best-effort) ===
    # Panel is already installed and accessible at this point.
    # The following steps configure VPN automatically but are NOT fatal.
    local auto_ok=true

    # 10. Wait for API & login
    if run_with_spinner "$(t api_waiting)" wait_for_api 90 && api_login_with_retry; then
        :  # API ready — language set earlier via configure_panel_tls (sqlite)
    elif recover_panel_login && api_login_with_retry; then
        log_success "$(t creds_recovered)"
    else
        log_warning "$(t api_login_fail_manual)"
        auto_ok=false
    fi

    # 11. Generate Reality keypair + users + inbound
    if $auto_ok; then
        if generate_reality_keypair && generate_clients "$users_count" "lite"; then
            # Create the inbound. v2.9.4's API can return non-zero even when the
            # row IS committed, so don't trust the rc — verify by result below.
            if reality_inbound_serving; then
                # Переустановка: Reality-inbound уже отдаёт :443 — не пересоздаём
                # (иначе красная "port already used"), переиспользуем существующий.
                log_dim "$(t auto_config_reuse)"
            else
                api_create_reality_inbound "$mask_domain" || log_dim "$(t auto_config_api_note)"
            fi
            log_info "$(tf users_creating "$users_count")"
            # Restart x-ui so it regenerates config.json from the DB, then verify
            # xray is actually serving Reality on :443 (retry — v2 can be slow).
            local _serve=false _try
            for _try in 1 2 3; do
                systemctl restart x-ui 2>/dev/null || true
                sleep 3
                if reality_inbound_serving; then _serve=true; break; fi
            done
            $_serve || { log_warning "$(t auto_config_fail)"; auto_ok=false; }
        else
            log_warning "$(t auto_config_fail)"
            auto_ok=false
        fi
    fi

    # 12. Generate VLESS links
    if $auto_ok; then
        generate_all_vless_links "lite" "$server_ip" "$mask_domain" || auto_ok=false
    fi

    # 13. Setup stub nginx (optional, for port 80)
    setup_lite_nginx || log_warning "$(t lite_nginx_optional_fail)"

    # 14. Save config
    config_set "mode" "lite"
    config_set "mask_domain" "$mask_domain"
    config_set "server_ip" "$server_ip"
    config_set "transport" "$XUI_TRANSPORT"
    config_set "fingerprint" "$XUI_FP"
    config_set fp_offer_done "1"   # fresh install already chose fp — skip the post-update offer
    config_set "xui_branch" "$XUI_BRANCH"
    [ -n "$XUI_INSTALL_VERSION" ] && config_set "xui_version" "$XUI_INSTALL_VERSION"
    config_set_int "port" 443
    config_set_int "users_count" "$users_count"
    config_set "version" "$GOVLESS_VERSION"
    config_set_int "schema" "$GOVLESS_SCHEMA"
    config_set "installed_at" "$(date -Iseconds)"

    # Save Reality keys to config ONLY if the inbound was actually created ($auto_ok).
    # On the failed-autoconfig path (e.g. :443 already held by an existing inbound) the
    # panel is up but no goVLESS inbound exists — persisting freshly generated keys would
    # leave orphan keys in config that match no live inbound (mismatched pbk).
    if $auto_ok; then
        [ -n "${REALITY_PRIVATE_KEY:-}" ] && config_set "reality_private_key" "$REALITY_PRIVATE_KEY"
        [ -n "${REALITY_PUBLIC_KEY:-}" ] && config_set "reality_public_key" "$REALITY_PUBLIC_KEY"
    fi

    # 15. Done!
    echo ""
    echo -e "  ${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
    echo -e "  ${GREEN}${BOLD}  $(tf install_done "$GOVLESS_VERSION" "Lite")${NC}"
    echo -e "  ${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
    echo ""

    show_credentials

    if ! $auto_ok; then
        log_warning "$(t auto_config_incomplete)"
    fi

    post_install_flow "lite" "$server_ip" "$mask_domain"
}

# ═══════════════════════════════════════════════════════════════════════
# INSTALL FLOW — PRO MODE (TLS)
# ═══════════════════════════════════════════════════════════════════════
install_pro() {
    log_step "$(t install_pro_step)"
    if [ "${GOVLESS_LAZY:-0}" = "1" ]; then
        log_info "$(t lazy_active)"
    fi

    local server_ip
    server_ip=$(get_server_ip) || { log_error "$(t common_no_ip)"; return 1; }

    # 1. Ask for domain
    echo ""
    # Опечатка или пустой Enter раньше завершали установщик целиком
    # (return 1 под set -e → выход в shell без подсказки, QA U-03).
    local domain _try=0
    while :; do
        echo -ne "  $(t pro_enter_domain) "
        read -e -r domain || domain=""
        domain=$(normalize_domain "$domain")
        if [ "$domain" = "0" ]; then log_info "$(t ms_cancelled)"; return 0; fi
        valid_domain "$domain" && break
        log_error "$(tf pro_bad_domain "$domain")"
        echo -e "  ${DIM}$(t pro_domain_retry)${NC}"
        _try=$((_try+1)); [ "$_try" -ge 3 ] && return 0
    done

    # 2. DNS check — interactive menu with wait+countdown option.
    # Lazy: do not block on a prompt; warn and continue (operator pointed the
    # A-record just before; cert step will report clearly if it isn't ready).
    if ! check_dns "$domain" "$server_ip"; then
        if [ "${GOVLESS_LAZY:-0}" = "1" ]; then
            log_warning "$(tf lazy_dns_warn "$domain" "$server_ip")"
        else
            dns_wait_or_choose "$domain" "$server_ip" || return 0
        fi
    fi

    # 3. Email for SSL (lazy: skip — Let's Encrypt registers without email)
    local email=""
    if [ "${GOVLESS_LAZY:-0}" != "1" ]; then
        local _etry=0
        while :; do
            echo -ne "  $(t pro_enter_email) "
            read -r email || email=""
            email=$(echo "$email" | tr -d '[:space:]')
            # Let's Encrypt отвергает невалидный контакт, а подсказка винила DNS
            # и порт 80 (QA U-04) — проверяем формат здесь.
            [ -z "$email" ] && break
            printf '%s' "$email" | grep -qE '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$' && break
            log_warning "$(t pro_bad_email)"
            _etry=$((_etry+1)); [ "$_etry" -ge 3 ] && { email=""; break; }
        done
    fi

    # 4. Template selection (verified catalog of 33 000+ static sites, custom git, or stub)
    local template_dir=""
    # Keep /opt/govless/templates_catalog.json in sync with the installer's copy —
    # the govlessctl daemon serves the bot / Mini App template picker from it.
    sync_templates_catalog
    if command -v jq &>/dev/null; then
        if [ "${GOVLESS_LAZY:-0}" = "1" ] && type pick_random_template_id &>/dev/null; then
            # Lazy: auto-pick a random template, no catalog browsing. A repo can
            # vanish after verification — try up to 3 random entries.
            local _rtpl _try
            _templates_cache_dir >/dev/null 2>&1 || true
            for _try in 1 2 3; do
                _rtpl=$(pick_random_template_id) || _rtpl=""
                [ -n "$_rtpl" ] || continue
                if template_dir=$(download_template "$_rtpl" 2>/dev/null) && [ -n "$template_dir" ]; then
                    log_dim "$(tf lazy_template "$_rtpl")"
                    break
                fi
                template_dir=""
            done
        elif type interactive_template_selection &>/dev/null; then
            # Codex 029 P2: distinguish "user picked skip" from "selection
            # failed". On real failure we abort install rather than silently
            # deploying a stub. "__skip__" → deliberate stub.
            local _tpl_out
            if _tpl_out=$(interactive_template_selection); then
                if [ "$_tpl_out" = "__skip__" ]; then
                    template_dir=""
                else
                    template_dir="$_tpl_out"
                fi
            else
                log_error "$(t invalid_choice)"
                return 1
            fi
        fi
    fi

    # 5. Ask how many keys (lazy = 5, no prompt)
    local users_count=5
    if [ "${GOVLESS_LAZY:-0}" != "1" ]; then
        echo ""
        echo -ne "  $(t users_ask_count) $(t users_ask_count_hint): "
        local users_count_input
        read -r users_count_input || users_count_input=""
        users_count="${users_count_input:-3}"
        if ! [[ "$users_count" =~ ^[0-9]{1,3}$ ]] || [ "$users_count" -lt 1 ] || [ "$users_count" -gt 100 ]; then
            users_count=3
        fi
    fi

    # Select 3X-UI version (Legacy 2.x or New 3.x) — but only if not
    # already installed. Otherwise install_3xui will short-circuit and the
    # user's choice would silently mismatch the actual binary (Codex 012 P2).
    if xui_already_installed; then
        load_xui_version_from_config
        log_dim "3X-UI already installed (${XUI_BRANCH:-?} ${XUI_INSTALL_VERSION:-?}) — keeping it"
    elif [ "${GOVLESS_LAZY:-0}" = "1" ]; then
        XUI_BRANCH="new"   # newest 3.x
    else
        select_xui_version
    fi

    # 6b. Transport + fingerprint (lazy = TCP + safari, no prompts)
    if [ "${GOVLESS_LAZY:-0}" = "1" ]; then
        XUI_TRANSPORT="tcp"
        XUI_FP="${GOVLESS_FP:-safari}"
    else
        select_transport pro
        select_fingerprint
    fi

    # 7. Show config summary
    print_header "$(t config_title)"
    echo -e "  $(t config_ip)       ${CYAN}${server_ip}${NC}"
    echo -e "  $(t config_domain)   ${CYAN}${domain}${NC}"
    echo -e "  $(t config_port)     ${CYAN}443${NC}"
    echo -e "  $(t config_mode)     ${CYAN}Pro (TLS)${NC}"
    echo -e "  $(t config_transport) ${CYAN}${XUI_TRANSPORT^^}${NC}"
    echo -e "  $(t config_users)    ${CYAN}${users_count}${NC}"
    echo ""

    if [ "${GOVLESS_LAZY:-0}" = "1" ]; then
        log_info "$(t lazy_autoconfirm)"
    else
        confirm "$(t config_confirm)" "$(t confirm_install)" "$(t confirm_back)" || return 0
    fi

    # 7. Install dependencies
    install_dependencies || return 1

    # Re-init state.db now that sqlite3 is guaranteed (first call in
    # init_language ran before deps install, may have been a no-op)
    init_state_db 2>/dev/null || true

    # 8. Free port 443 if occupied (but not xray — it might be our running VPN)
    local port443_proc
    port443_proc=$(ss -tlnp 'sport = :443' 2>/dev/null | grep -o 'users:(("[^"]*' | sed 's/users:(("//' | head -1) || true
    if [ -n "$port443_proc" ]; then
        case "$port443_proc" in
            xray|xray-linux-*) ;;
            *) kill_port 443 ;;
        esac
    fi

    # 8. Setup website + SSL first (needs port 80 and 443 free)
    if [ -n "$template_dir" ]; then
        setup_pro_website "$domain" "$template_dir" "$email" || return 1
    else
        # No template — deploy stub and get SSL
        install_nginx || return 1
        install_certbot || return 1
        deploy_stub_site
        obtain_ssl_certificate "$domain" "$email" || return 1
        generate_nginx_pro_config "$domain"
        systemctl restart nginx 2>/dev/null
        setup_ssl_auto_renewal
    fi
    # Remember which cover-site template is deployed (menu / bot / Mini App show it)
    if [ -n "$template_dir" ]; then
        config_set "site_template" "$(basename "$template_dir")"
    else
        config_set "site_template" "__stub__"
    fi

    # 9. Stop nginx on 443 — xray will take over
    # nginx stays on :80, xray takes :443 with fallback to :80

    # 10. Install 3X-UI (critical — must succeed)
    install_3xui || return 1
    # Свежая установка панели останавливает govlessctl/govless-bot (чтобы не
    # подхватить старые креды) и не поднимала их обратно (QA S-13).
    for _u in govlessctl govless-bot; do
        systemctl cat "$_u" >/dev/null 2>&1 && systemctl start "$_u" >/dev/null 2>&1 || true
    done

    # Panel TLS is configured AFTER the domain is written to config (below), so
    # configure_panel_tls uses the domain LE cert directly and skips issuing a
    # throwaway ~6-day IP cert (a premature call here fell into the Lite/IP branch
    # because config.domain was not set yet — wasted an LE issuance + hit DB lock).

    # 11. Extract credentials & setup API (critical for panel access)
    extract_credentials
    # Set mode/domain in config BEFORE save_credentials so the saved
    # /root/.govless_credentials reflects MODE=pro (was P2: defaulted to lite).
    config_set "mode" "pro"
    config_set "domain" "$domain"
    # Re-point the panel at the domain LE cert now that the cert exists and the
    # domain is in config — so the panel is served at https://<domain>:<port>
    # with a browser-valid certificate (was: IP self-signed).
    configure_panel_tls "${LANG_CODE:-en}" "$server_ip" "$domain" \
        || log_warning "$(t panel_tls_domain_warn)"
    configure_sub_server "pro" "$domain" 2>/dev/null || log_warning "$(t panel_subsrv_https_skipped)"
    save_credentials
    setup_api_base

    # === Auto-configuration (best-effort) ===
    # Panel is already installed and accessible at this point.
    local auto_ok=true

    # 12. Wait for API & login
    if run_with_spinner "$(t api_waiting)" wait_for_api 90 && api_login_with_retry; then
        :  # API ready — language set earlier via configure_panel_tls (sqlite)
    elif recover_panel_login && api_login_with_retry; then
        log_success "$(t creds_recovered)"
    else
        log_warning "$(t api_login_fail_manual)"
        auto_ok=false
    fi

    # 13. Generate users + TLS inbound
    if $auto_ok; then
        local cert_file="/etc/letsencrypt/live/${domain}/fullchain.pem"
        local key_file="/etc/letsencrypt/live/${domain}/privkey.pem"
        if generate_clients "$users_count" "pro"; then
            if tls_inbound_serving; then
                # Переустановка поверх рабочей панели: наш TLS-inbound уже отдаёт :443.
                # НЕ пересоздаём (иначе панель вернёт "port 443 already used" и юзер
                # увидит пугающую красную ошибку) — переиспользуем существующий.
                log_dim "$(t auto_config_reuse)"
                log_info "$(tf users_creating "$users_count")"
                systemctl restart x-ui 2>/dev/null || true
                sleep 2
            elif api_create_tls_inbound "$domain" "$cert_file" "$key_file"; then
                log_info "$(tf users_creating "$users_count")"
                systemctl restart x-ui 2>/dev/null || true
                sleep 2
            else
                log_warning "$(t auto_config_fail)"
                auto_ok=false
            fi
        else
            log_warning "$(t auto_config_fail)"
            auto_ok=false
        fi
    fi

    # 14. Generate VLESS links
    if $auto_ok; then
        generate_all_vless_links "pro" "$domain" || auto_ok=false
    fi

    # 15. Save config
    config_set "mode" "pro"
    config_set "domain" "$domain"
    config_set "server_ip" "$server_ip"
    config_set "email" "$email"
    config_set "transport" "$XUI_TRANSPORT"
    config_set "fingerprint" "$XUI_FP"
    config_set fp_offer_done "1"   # fresh install already chose fp — skip the post-update offer
    config_set_int "port" 443
    config_set_int "users_count" "$users_count"
    config_set "version" "$GOVLESS_VERSION"
    config_set_int "schema" "$GOVLESS_SCHEMA"
    config_set "installed_at" "$(date -Iseconds)"
    config_set "xui_branch" "$XUI_BRANCH"
    [ -n "$XUI_INSTALL_VERSION" ] && config_set "xui_version" "$XUI_INSTALL_VERSION"

    # 16. Done!
    echo ""
    echo -e "  ${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
    echo -e "  ${GREEN}${BOLD}  $(tf install_done "$GOVLESS_VERSION" "Pro")${NC}"
    echo -e "  ${GREEN}${BOLD}════════════════════════════════════════════════════${NC}"
    echo ""

    show_credentials

    if ! $auto_ok; then
        log_warning "$(t auto_config_incomplete)"
    fi

    post_install_flow "pro" "$domain"
}

# ═══════════════════════════════════════════════════════════════════════
# POST-INSTALL FLOW
# ═══════════════════════════════════════════════════════════════════════
post_install_flow() {
    local mode="$1"
    local server="$2"              # IP (lite) or domain (pro)
    local mask_domain="${3:-}"      # only for lite
    # Interactive post-install phase (prompts, QR, bot setup): a fallible display
    # step must not abort the script under errexit.
    set +e

    echo ""
    echo -ne "  $(t press_enter) "
    read -r

    # App download step
    show_app_download

    # Show first user's QR — regen from DB first, prefer subscription URL
    regenerate_links_from_db 2>/dev/null
    if [ -s /tmp/govless_links.json ]; then
        local first_name first_link first_sub
        first_name=$(python3 -c "import json; d=json.load(open('/tmp/govless_links.json')); print(list(d.keys())[0])" 2>/dev/null)
        first_link=$(python3 -c "import json; d=json.load(open('/tmp/govless_links.json')); print(list(d.values())[0])" 2>/dev/null)
        first_sub=""
        if [ "$mode" = "pro" ] && [ -s /tmp/govless_subs.json ]; then
            first_sub=$(python3 -c "import json, sys; d=json.load(open('/tmp/govless_subs.json')); print(d.get(sys.argv[1],''))" "$first_name" 2>/dev/null)
        fi

        if [ -n "$first_name" ] && [ -n "$first_link" ]; then
            show_user_link_choice "$first_name" "$first_link" "$first_sub"
        fi
    fi

    # Connection test
    echo ""
    echo -e "  ${BOLD}$(t test_title)${NC}"
    echo -e "  ${DIM}$(t test_skip)${NC}"
    echo ""

    # 30s default (was 120s) — Layer 3 traffic-stats fallback in
    # check_client_online catches connects within seconds now, so longer
    # wait is unnecessary noise. Override via GOVLESS_CONNTEST_TIMEOUT.
    local test_timeout="${GOVLESS_CONNTEST_TIMEOUT:-30}"
    local poll_interval="${GOVLESS_CONNTEST_INTERVAL:-3}"
    local elapsed=0
    local first_email
    [ -s /tmp/govless_users_map.json ] || regenerate_links_from_db 2>/dev/null
    first_email=$(python3 -c "import json; d=json.load(open('/tmp/govless_users_map.json')); print(list(d.keys())[0])" 2>/dev/null)

    if [ -n "$first_email" ]; then
        while [ "$elapsed" -lt "$test_timeout" ]; do
            if check_client_online "$first_email"; then
                printf "\r%80s\r" " "
                echo -e "  $(tf test_online "$first_email")"
                break
            fi
            local _msg
            _msg=$(tf test_offline "$first_email")
            # Show skip-hint INLINE on the wait line so user sees it
            printf "\r  %s (%ds)  ${DIM}[%s]${NC}    " "$_msg" "$elapsed" "$(t test_skip)" >&2
            read -t "$poll_interval" -r </dev/tty 2>/dev/null && { echo ""; break; } || true
            elapsed=$((elapsed + poll_interval))
        done
        echo ""
    fi

    # Ask to show all users
    echo ""
    if confirm "$(t users_show_all)"; then
        show_all_users_formatted
    fi

    # Offer the step-by-step Telegram bot setup (token -> auto-detect admin -> web menu)
    offer_bot_setup

    echo ""
    echo -e "  $(t enjoy)"
    echo -e "  ${DIM}$(t install_done_hint)${NC}"
    echo ""
}

# ── Show app download ───────────────────────────────────────────────────
show_app_download() {
    print_header "$(t app_title)"
    echo -e "  ${CYAN}1)${NC} $(t app_ios)"
    echo -e "  ${CYAN}2)${NC} $(t app_android)"
    echo ""
    echo -ne "  $(t app_platform) "
    local platform
    read -r platform || platform=""

    case "$platform" in
        1) echo -e "  ${GREEN}$(t app_ios_hint)${NC}"
           echo ""
           echo -e "  ${DIM}App Store: INCY${NC}"
           qr_print "https://apps.apple.com/us/app/incy/id6756943388"
           ;;
        2) echo -e "  ${GREEN}$(t app_android_hint)${NC}"
           echo ""
           echo -e "  ${DIM}Google Play: INCY${NC}"
           qr_print "https://play.google.com/store/apps/details?id=llc.itdev.incy"
           ;;
    esac

    echo ""
    echo -ne "  $(t app_installed) "
    read -r
}

# ── Show all users formatted ────────────────────────────────────────────
show_all_users_formatted() {
    regenerate_links_from_db 2>/dev/null
    if [ ! -s /tmp/govless_links.json ]; then
        return 1
    fi

    print_header "$(t users_title)"

    while IFS=$'\t' read -r num name_b64 link_b64; do
        local name link
        name=$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$name_b64" 2>/dev/null)
        link=$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$link_b64" 2>/dev/null)
        echo -e "  ${CYAN}${num})${NC} ${BOLD}${name}${NC}"
        echo -e "     ${GREEN}${link}${NC}"
        echo ""
    done < <(show_all_users)
}

# ═══════════════════════════════════════════════════════════════════════
# MAIN MENU (interactive, after installation)
# ═══════════════════════════════════════════════════════════════════════
show_dashboard() {
    clear 2>/dev/null || true
    print_banner

    local mode xui_st nginx_st
    mode=$(config_get mode "N/A")
    xui_st=$(xui_status)
    nginx_st=$(nginx_status)

    # Status indicators
    local xui_icon nginx_icon
    case "$xui_st" in
        running) xui_icon="${GREEN}●${NC}" ;;
        stopped) xui_icon="${YELLOW}○${NC}" ;;
        *)       xui_icon="${RED}✗${NC}" ;;
    esac
    case "$nginx_st" in
        running) nginx_icon="${GREEN}●${NC}" ;;
        stopped) nginx_icon="${YELLOW}○${NC}" ;;
        *)       nginx_icon="${RED}✗${NC}" ;;
    esac

    echo -e "  ${BOLD}$(t dashboard_title)${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}"
    echo -e "  $(t svc_xui):  ${xui_icon} $(t "$xui_st")    $(t svc_nginx): ${nginx_icon} $(t "$nginx_st")"

    local ip domain mask
    ip=$(config_get server_ip "")
    domain=$(config_get domain "")
    mask=$(config_get mask_domain "")

    local xui_ver transport_cfg
    xui_ver=$(xui_saved_version)
    transport_cfg=$(config_get transport "")

    echo -e "  $(t net_mode)    ${CYAN}${mode}${NC}"
    [ -n "$xui_ver" ] && echo -e "  $(t dashboard_xui_ver)  ${CYAN}${xui_ver}${NC}"
    [ -n "$transport_cfg" ] && echo -e "  $(t config_transport) ${CYAN}${transport_cfg^^}${NC}"
    [ -n "$ip" ] && echo -e "  $(t net_ip)      ${CYAN}${ip}${NC}"
    [ -n "$domain" ] && echo -e "  $(t net_domain)  ${CYAN}${domain}${NC}"
    # Домен маскировки относится только к Lite (Reality). В Pro поле остаётся
    # в конфиге от прошлого режима и вводило в заблуждение (QA BUG-29).
    [ -n "$mask" ] && [ "$mode" = "lite" ] && echo -e "  $(t config_mask) ${CYAN}${mask}${NC}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..50})${NC}"
}

# ── Telegram bot: settings & administration ─────────────────────────────
BOT_ENV_FILE="/etc/govless/bot.env"

# Признак того, что Telegram control-plane (phase-a) РЕАЛЬНО развёрнут.
# ВАЖНО: нельзя судить по наличию /etc/govless/bot.env — этот файл создают и
# другие пути БЕЗ установки компонента: настройка egress-прокси пишет туда
# BOT_PROXY (lib/egress.sh), а _bot_env_set создаёт файл при вводе токена.
# Из-за этого меню считало бота установленным, прятало пункт "Установить",
# а systemctl enable/restart молча падал на несуществующем юните → бот не работал.
_bot_installed() {
    [ -d /opt/govless/govlessctl ] && [ -d /opt/govless/bot ] \
        && [ -x /opt/govless/venv/bin/python ] \
        && systemctl cat govless-bot >/dev/null 2>&1
}

_bot_env_get() {  # $1=key -> value on stdout
    [ -f "$BOT_ENV_FILE" ] || return 1
    sed -nE "s/^$1=(.*)$/\1/p" "$BOT_ENV_FILE" | head -n1
}

_bot_env_set() {  # $1=key $2=value — replace-or-append, preserve perms/owner
    local key="$1" val="$2" tmp
    mkdir -p /etc/govless 2>/dev/null
    [ -f "$BOT_ENV_FILE" ] || : > "$BOT_ENV_FILE"
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" '
        $0 ~ "^"k"=" { if (!seen) { print k"="v; seen=1 } ; next }
        { print }
        END { if (!seen) print k"="v }
    ' "$BOT_ENV_FILE" > "$tmp"
    cat "$tmp" > "$BOT_ENV_FILE"
    rm -f "$tmp"
    chmod 0640 "$BOT_ENV_FILE" 2>/dev/null
    chown root:govless "$BOT_ENV_FILE" 2>/dev/null || true
}

# curl-аргументы для api.telegram.org: если настроен «Прокси для API Telegram»
# (BOT_PROXY=socks5://127.0.0.1:10808), ходим через него — на RU-хостинге
# напрямую api.telegram.org недоступен, и мастер зацикливался на «Telegram не
# подтвердил токен», хотя бот через прокси работал бы (QA U-05).
_tg_curl_args() {
    local px; px="$(_bot_env_get BOT_PROXY)"
    case "$px" in
        socks5://*|socks5h://*) printf -- '--socks5-hostname %s' "${px#*://}" ;;
        http://*|https://*)     printf -- '--proxy %s' "$px" ;;
    esac
}
_bot_getme() {  # $1=token -> echoes username if ok, else returns 1
    local resp
    # shellcheck disable=SC2046
    resp="$(curl -fsS --max-time 10 $(_tg_curl_args) "https://api.telegram.org/bot$1/getMe" 2>/dev/null)" || return 1
    printf '%s' "$resp" | grep -q '"ok":true' || return 1
    printf '%s' "$resp" | grep -oE '"username":"[^"]*"' | head -n1 | sed -E 's/.*:"([^"]*)"/\1/'
}

bot_set_token() {
    local cur tok uname yn
    cur="$(_bot_env_get BOT_TOKEN)"
    if [ -n "$cur" ]; then echo -e "  $(t bot_token_current_set) ${DIM}${cur:0:6}…${NC}"
    else log_warning "$(t bot_token_current_empty)"; fi
    echo -ne "  $(t bot_token_prompt) "
    read -r tok
    tok="$(printf '%s' "$tok" | tr -d '[:space:]')"
    if ! printf '%s' "$tok" | grep -qE '^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; then
        log_warning "$(t bot_token_invalid)"; return
    fi
    log_info "$(t bot_token_verifying)"
    if uname="$(_bot_getme "$tok")" && [ -n "$uname" ]; then
        log_success "$(tf bot_token_ok "$uname")"
    else
        log_warning "$(t bot_token_verify_fail)"
        confirm "$(t bot_save_anyway)" || return
    fi
    _bot_env_set BOT_TOKEN "$tok"
    log_success "$(t bot_token_saved)"
    if systemctl restart govless-bot 2>/dev/null; then log_success "$(t bot_restarted)"
    else log_info "$(t bot_reload_note)"; fi
}

bot_manage_admins() {
    local cur ids
    cur="$(_bot_env_get ADMIN_IDS)"
    if [ -n "$cur" ]; then echo -e "  $(tf bot_admins_current "$cur")"; else log_warning "$(t bot_admins_none)"; fi
    echo -e "  ${DIM}$(t bot_admins_hint)${NC}"
    echo -ne "  $(t bot_admins_prompt) "
    read -r ids
    ids="$(printf '%s' "$ids" | tr -d '[:space:]')"
    [ -z "$ids" ] && { log_warning "$(t bot_admins_unchanged)"; return; }
    if ! printf '%s' "$ids" | grep -qE '^[0-9]+(,[0-9]+)*$'; then
        log_warning "$(t bot_admins_invalid)"; return
    fi
    _bot_env_set ADMIN_IDS "$ids"
    log_success "$(t bot_admins_saved)"
    systemctl restart govless-bot 2>/dev/null || true
}

bot_show_status() {
    local tok ids uname svc mode svcs
    tok="$(_bot_env_get BOT_TOKEN)"; ids="$(_bot_env_get ADMIN_IDS)"
    if [ -n "$tok" ]; then echo -e "  ${GREEN}✓${NC} $(t bot_token_current_set)"; else echo -e "  ${YELLOW}—${NC} $(t bot_token_current_empty)"; fi
    if [ -n "$ids" ]; then echo -e "  $(tf bot_admins_current "$ids")"; else echo -e "  ${DIM}$(t bot_admins_none)${NC}"; fi
    # In Pro the WebApp is served by nginx (proxying /api/rpc to the daemon), so the
    # standalone webapp-frontend service is intentionally unused; listing it here would
    # show a misleading red ✗. Show it only in Lite (its real home); in Pro show a note.
    mode=$(config_get mode "lite")
    if [ "$mode" = "pro" ]; then svcs="govlessctl govless-bot"; else svcs="govlessctl govless-bot webapp-frontend"; fi
    for svc in $svcs; do
        systemctl list-unit-files "${svc}.service" >/dev/null 2>&1 || continue
        if systemctl is-active --quiet "$svc" 2>/dev/null; then echo -e "  ${GREEN}✓${NC} ${svc}: $(t bot_status_running)"
        else echo -e "  ${RED}✗${NC} ${svc}: $(t bot_status_stopped)"; fi
    done
    if [ "$mode" = "pro" ]; then
        if [ "$(nginx_status 2>/dev/null)" = "running" ]; then echo -e "  ${GREEN}✓${NC} $(t bot_webapp_via_nginx)"
        else echo -e "  ${YELLOW}○${NC} $(t bot_webapp_via_nginx)"; fi
    fi
    if [ -n "$tok" ] && uname="$(_bot_getme "$tok")" && [ -n "$uname" ]; then
        echo -e "  $(t bot_link): ${CYAN}https://t.me/${uname}${NC}"
    fi
}

bot_restart() {
    if ! _bot_installed; then log_warning "$(t bot_not_installed)"; return 1; fi
    log_info "$(t bot_restarting)"
    local ok=1 svc
    for svc in govlessctl govless-bot; do systemctl restart "$svc" 2>/dev/null || ok=0; done
    [ "$ok" = 1 ] && log_success "$(t bot_restarted)" || log_warning "$(t bot_restart_fail)"
}

bot_install_phasea() {
    local installer="${SCRIPT_DIR}/phase-a/systemd/install/install_phase_a.sh"
    [ -f "$installer" ] || { log_error "$(t bot_phasea_missing)"; return 1; }
    log_info "$(t bot_installing_phasea)"
    if bash "$installer"; then log_success "$(t bot_phasea_installed)"; return 0; fi
    log_error "$(t bot_phasea_fail)"; return 1
}

submenu_bot() {
    while true; do
        print_header "$(t submenu_bot_title)"
        if ! _bot_installed; then
            log_warning "$(t bot_not_installed)"
            echo -e "  ${CYAN}1)${NC} $(t bot_opt_install)"
            echo -e "  ${CYAN}2)${NC} $(t bot_opt_egress)"
            echo -e "  ${CYAN}0)${NC} $(t back)"
            echo ""
            local c; read -rp "  ▸ " c
            case "$c" in
                1) bot_install_phasea; _bot_installed && bot_setup_wizard ;;
                2) egress_menu ;;
                0) return ;;
                *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
            esac
            echo -ne "  $(t press_enter_return) "; read -r; continue
        fi
        echo -e "  ${CYAN}1)${NC} $(t bot_opt_token)"
        echo -e "  ${CYAN}2)${NC} $(t bot_opt_admins)"
        echo -e "  ${CYAN}3)${NC} $(t bot_opt_status)"
        echo -e "  ${CYAN}4)${NC} $(t bot_opt_restart)"
        echo -e "  ${CYAN}5)${NC} $(t bot_opt_wizard)"
        echo -e "  ${CYAN}6)${NC} $(t bot_opt_egress)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""
        local choice; read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            1) bot_set_token ;;
            2) bot_manage_admins ;;
            3) bot_show_status ;;
            4) bot_restart ;;
            5) bot_setup_wizard ;;
            6) egress_menu ;;
            0) return ;;
            *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
        esac
        echo -ne "  $(t press_enter_return) "; read -r
    done
}


# ── Telegram bot: auto-detect admin + step-by-step setup wizard ──────────
_bot_detect_admin() {  # $1=token -> prints "id|first_name|username"; rc 0 ok / 1 timeout
    # The bot polls getUpdates (single consumer) — stop it so we can read updates.
    systemctl stop govless-bot 2>/dev/null || true
    # getUpdates тоже через прокси, если он настроен (см. _tg_curl_args): urllib
    # не умеет SOCKS без сторонних модулей, поэтому ходим curl-ом.
    python3 - "$1" "$(_tg_curl_args)" <<'PYEOF'
import sys, json, time, subprocess, shlex
tok = sys.argv[1]
proxy_args = shlex.split(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else []
base = "https://api.telegram.org/bot%s/getUpdates" % tok
def call(q, to=30):
    r = subprocess.run(["curl", "-fsS", "--max-time", str(to)] + proxy_args + [base + "?" + q],
                       capture_output=True, text=True, timeout=to + 5)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:120])
    return json.loads(r.stdout)
offset = 0
# drain anything already pending so we only react to a fresh message
try:
    d = call("offset=-1&timeout=0", 15)
    res = d.get("result", [])
    if res:
        offset = res[-1]["update_id"] + 1
except Exception:
    pass
deadline = time.time() + 90
while time.time() < deadline:
    try:
        d = call("offset=%d&timeout=20" % offset, 30)
    except Exception:
        time.sleep(2); continue
    for u in d.get("result", []):
        offset = max(offset, u["update_id"] + 1)
        m = u.get("message") or u.get("edited_message")
        frm = (m or {}).get("from") or {}
        if frm.get("id") and not frm.get("is_bot"):
            print("%s|%s|%s" % (frm["id"], frm.get("first_name", ""), frm.get("username", "")))
            sys.exit(0)
sys.exit(1)
PYEOF
}

bot_setup_wizard() {
    local tok cur uname det aid aname auser admins="" yn m
    print_header "$(t bot_wiz_title)"

    # Step 1/3 — token (with @BotFather hint + getMe verification)
    echo -e "  ${BOLD}$(t bot_wiz_step1)${NC}"
    echo -e "  ${DIM}$(t bot_wiz_token_help)${NC}"
    cur="$(_bot_env_get BOT_TOKEN)"
    [ -n "$cur" ] && echo -e "  ${DIM}$(t bot_token_current_set) ${cur:0:6}…${NC}"
    while true; do
        echo -ne "  $(t bot_token_prompt) "
        read -r tok
        tok="$(printf '%s' "$tok" | tr -d '[:space:]')"
        if [ -z "$tok" ]; then log_warning "$(t bot_wiz_cancelled)"; return 1; fi
        if ! printf '%s' "$tok" | grep -qE '^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; then
            log_warning "$(t bot_token_invalid)"; continue
        fi
        log_info "$(t bot_token_verifying)"
        if uname="$(_bot_getme "$tok")" && [ -n "$uname" ]; then
            log_success "$(tf bot_token_ok "$uname")"; break
        fi
        log_warning "$(t bot_token_verify_fail)"
    done
    _bot_env_set BOT_TOKEN "$tok"

    # Step 2/3 — auto-detect first admin by messaging the bot
    echo ""
    echo -e "  ${BOLD}$(t bot_wiz_step2)${NC}"
    echo -e "  $(tf bot_wiz_open_bot "$uname")"
    log_info "$(t bot_wiz_waiting)"
    if det="$(_bot_detect_admin "$tok")"; then
        IFS='|' read -r aid aname auser <<<"$det"
        log_success "$(tf bot_wiz_detected "${aname:-${auser:-id}}" "$aid")"
        # Первый, кто написал боту в окно ожидания, не обязательно владелец
        # сервера (QA S-17) — подтверждаем, прежде чем выдать права.
        if confirm "$(tf bot_wiz_confirm_admin "${aname:-${auser:-id}}" "$aid")"; then admins="$aid"; else admins=""; fi
    else
        log_warning "$(t bot_wiz_detect_timeout)"
        echo -ne "  $(t bot_admins_prompt) "; read -r m
        m="$(printf '%s' "$m" | tr -d '[:space:]')"
        printf '%s' "$m" | grep -qE '^[0-9]+(,[0-9]+)*$' && admins="$m"
    fi

    # Step 3/3 — offer to add more admins
    while [ -n "$admins" ]; do
        echo ""
        confirm "$(t bot_wiz_add_more)" || break
        echo -e "  $(t bot_wiz_open_bot_more)"
        log_info "$(t bot_wiz_waiting)"
        if det="$(_bot_detect_admin "$tok")"; then
            IFS='|' read -r aid aname auser <<<"$det"
            case ",$admins," in
                *",$aid,"*) log_warning "$(t bot_wiz_already)" ;;
                *) log_success "$(tf bot_wiz_detected "${aname:-${auser:-id}}" "$aid")"
                   confirm "$(tf bot_wiz_confirm_admin "${aname:-${auser:-id}}" "$aid")" && admins="${admins},${aid}" ;;
            esac
        else
            log_warning "$(t bot_wiz_detect_timeout)"
        fi
    done
    [ -n "$admins" ] && _bot_env_set ADMIN_IDS "$admins"

    # Step 4 — start the bot + final instructions (incl. web menu / Mini App).
    # Если компонент почему-то не развёрнут (например bot.env был создан egress-ом
    # до установки) — ставим его сейчас, иначе enable/restart молча ничего не сделают.
    if ! _bot_installed; then
        bot_install_phasea || { log_error "$(t bot_phasea_fail)"; return 1; }
    fi
    systemctl enable govless-bot >/dev/null 2>&1
    if ! systemctl restart govless-bot 2>/dev/null; then
        log_error "$(t bot_restart_fail)"
        return 1
    fi
    echo ""
    if [ -n "$admins" ]; then
        echo -e "  ${GREEN}${BOLD}$(t bot_wiz_done_title)${NC}"
        log_success "$(tf bot_wiz_done_bot "$uname")"
        echo -e "  $(tf bot_admins_current "$admins")"
    else
        # Раньше здесь печаталось «Бот настроен ✓» и без администратора, а бот
        # в ответ на /start требовал «/admin <ADMIN_CLAIM_TOKEN>» — токен, который
        # никто не показывал (QA U-06). Показываем команду целиком.
        local claim; claim="$(_bot_env_get ADMIN_CLAIM_TOKEN)"
        if [ -z "$claim" ] && command -v openssl >/dev/null 2>&1; then
            claim="$(openssl rand -hex 16)"; _bot_env_set ADMIN_CLAIM_TOKEN "$claim"
            systemctl restart govlessctl govless-bot 2>/dev/null || true
        fi
        log_warning "$(t bot_wiz_no_admin)"
        [ -n "$claim" ] && echo -e "  ${WHITE}/admin ${claim}${NC}"
        echo -e "  ${DIM}$(t bot_wiz_no_admin_alt)${NC}"
    fi
    echo -e "  $(t bot_wiz_webmenu)"
    echo -e "  ${CYAN}https://t.me/${uname}${NC}"
    echo ""
}

offer_bot_setup() {
    echo ""
    echo -e "  ${BOLD}$(t bot_offer_title)${NC}"
    if ! confirm "$(t bot_offer_setup)"; then log_info "$(t bot_offer_later)"; return 0; fi
    if ! _bot_installed; then
        log_info "$(t bot_installing_phasea)"
        bot_install_phasea || { log_warning "$(t bot_phasea_fail)"; return 1; }
    fi
    bot_setup_wizard
}


main_menu() {
    while true; do
        show_dashboard

        echo ""
        echo -e "  ${CYAN}1)${NC} $(t menu_proxy)"
        echo -e "  ${CYAN}2)${NC} $(t menu_users)"
        echo -e "  ${CYAN}3)${NC} $(t menu_manage)"
        echo -e "  ${CYAN}4)${NC} $(t menu_bot)"
        echo -e "  ${CYAN}5)${NC} $(t menu_about)"
        echo -e "  ${CYAN}0)${NC} $(t exit)"
        echo ""
        echo -e "  ${DIM}$(t auto_refresh_30s)${NC}"

        local choice
        read -t 30 -rp "  ▸ " choice || { echo ""; continue; }

        case "$choice" in
            1) submenu_proxy ;;
            2) submenu_users ;;
            3) submenu_manage ;;
            4) submenu_bot ;;
            5) submenu_about ;;
            0) echo -e "  $(t bye)"; exit 0 ;;
            *)
                # Stay in main menu — re-render dashboard + options
                log_warning "$(t invalid_choice)"
                sleep 1
                ;;
        esac
    done
}

# ── Submenu: Proxy ──────────────────────────────────────────────────────
# Looping submenu — invalid input stays here (does NOT pop to main menu).
# Only `0` returns to the caller (main menu).
submenu_proxy() {
    while true; do
        print_header "$(t submenu_proxy_title)"
        echo -e "  ${CYAN}1)${NC} $(t proxy_install_update)"
        echo -e "  ${CYAN}2)${NC} $(t proxy_restart)"
        echo -e "  ${CYAN}3)${NC} $(t proxy_logs)"
        echo -e "  ${CYAN}4)${NC} $(t proxy_change_mode)"
        echo -e "  ${CYAN}5)${NC} $(t proxy_site_template)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""

        local choice
        read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            1) select_and_install ;;
            2) restart_xui ;;
            3) xui_logs 50 ;;
            4) switch_mode_interactive ;;
            5) submenu_site; continue ;;
            0) return ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Submenu: Cover site / template (Pro) ────────────────────────────────
# The template picker used to exist only inside install_pro — after install
# there was no way to change the masquerade site. Now: catalog (33 000+
# verified static sites, paged + search), random, custom git, stub, restore.
# Same backend as the Telegram bot / Mini App (govlessctl → govless --site-template-*).
submenu_site() {
    while true; do
        print_header "$(t website_title)"
        if ! site_is_pro; then
            log_warning "$(t website_only_pro)"
            echo -e "  ${DIM}$(t site_hint_switch_pro)${NC}"
            echo ""
            echo -e "  ${CYAN}1)${NC} $(t site_switch_to_pro_now)"
            echo -e "  ${CYAN}0)${NC} $(t back)"
            echo ""
            local c
            read -rp "  ▸ " c || { echo ""; return; }
            case "$c" in
                1) switch_mode_interactive ;;
                0) return ;;
                *) log_warning "$(t invalid_choice)"; sleep 1; continue ;;
            esac
            echo -ne "  $(t press_enter_return) "; read -r
            continue
        fi
        sync_templates_catalog
        site_print_current
        echo -e "  ${CYAN}1)${NC} $(tf site_opt_catalog "$(catalog_total_count)")"
        echo -e "  ${CYAN}2)${NC} $(t site_opt_random)"
        echo -e "  ${CYAN}3)${NC} $(t site_opt_custom_git)"
        echo -e "  ${CYAN}4)${NC} $(t site_opt_stub)"
        echo -e "  ${CYAN}5)${NC} $(t site_opt_restore)"
        echo -e "  ${CYAN}6)${NC} $(t site_opt_check)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""

        local choice
        read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            1) site_change_from_catalog ;;
            2) site_template_apply_random ;;
            3) site_change_custom_git ;;
            4) confirm "$(t site_stub_confirm)" && site_template_apply_stub ;;
            5) site_restore_menu ;;
            6) site_check ;;
            0) return ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Submenu: Users (NEW — owns links/QR, was scattered in Proxy) ────────
submenu_users() {
    while true; do
        print_header "$(t submenu_users_title)"
        echo -e "  ${CYAN}1)${NC} $(t users_show_list)"
        echo -e "  ${CYAN}2)${NC} $(t users_show_links_action)"
        echo -e "  ${CYAN}3)${NC} $(t users_show_qr_action)"
        echo -e "  ${CYAN}4)${NC} $(t users_regen_links)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""

        local choice
        read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            1) show_all_users_formatted ;;
            2)
                # All VLESS links as plain text (good for SSH copy-paste)
                regenerate_links_from_db 2>/dev/null
                if [ -s /tmp/govless_links.json ]; then
                    python3 -c "
import json
d = json.load(open('/tmp/govless_links.json'))
for name, link in d.items():
    print(f'{name}:')
    print(f'  {link}')
    print()
"
                else
                    log_warning "$(t users_no_links)"
                fi
                ;;
            3)
                # Per-user QR: list users and let the operator PICK which one (loop to pick more).
                regenerate_links_from_db 2>/dev/null
                if [ ! -s /tmp/govless_links.json ]; then
                    log_warning "$(t users_no_links)"
                else
                    local -a _qnames=()
                    while IFS= read -r _qn; do [ -n "$_qn" ] && _qnames+=("$_qn"); done < <(python3 -c "import json;[print(k) for k in json.load(open('/tmp/govless_links.json')).keys()]" 2>/dev/null)
                    if [ ${#_qnames[@]} -eq 0 ]; then
                        log_warning "$(t users_no_links)"
                    else
                        while true; do
                            echo ""
                            echo -e "  ${BOLD}$(t users_pick_qr)${NC}"
                            local _qi=1 _qn
                            for _qn in "${_qnames[@]}"; do echo -e "    ${CYAN}${_qi})${NC} ${_qn}"; _qi=$((_qi+1)); done
                            echo -e "    ${CYAN}0)${NC} $(t back)"
                            local _qp; read -rp "  ▸ " _qp </dev/tty
                            [ "$_qp" = "0" ] && break
                            if [[ "$_qp" =~ ^[0-9]+$ ]] && [ "$_qp" -ge 1 ] && [ "$_qp" -le "${#_qnames[@]}" ]; then
                                local _qsel="${_qnames[$((_qp-1))]}" _qlink _qsub=""
                                _qlink=$(python3 -c "import json,sys; print(json.load(open('/tmp/govless_links.json')).get(sys.argv[1],''))" "$_qsel" 2>/dev/null)
                                [ -s /tmp/govless_subs.json ] && _qsub=$(python3 -c "import json,sys; print(json.load(open('/tmp/govless_subs.json')).get(sys.argv[1],''))" "$_qsel" 2>/dev/null)
                                show_user_link_choice "$_qsel" "$_qlink" "$_qsub"
                            else
                                log_warning "$(t invalid_choice)"
                            fi
                        done
                    fi
                fi
                ;;
            4)
                regenerate_links_from_db && log_success "$(t users_links_regen_ok)"
                ;;
            0) return ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Submenu: Manage ─────────────────────────────────────────────────────
submenu_manage() {
    while true; do
        print_header "$(t submenu_manage_title)"
        echo -e "  ${CYAN}1)${NC} $(t manage_language)"
        echo -e "  ${CYAN}2)${NC} $(t proxy_restart)"
        echo -e "  ${CYAN}3)${NC} $(t manage_repair)"
        echo -e "  ${CYAN}4)${NC} $(t manage_backup)"
        echo -e "  ${CYAN}5)${NC} $(t manage_restore)"
        echo -e "  ${CYAN}6)${NC} $(t manage_remove)"
        echo -e "  ${CYAN}7)${NC} $(t manage_show_creds)"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""

        local choice
        read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            7)
                # Данные панели печатались только при установке; закрыл терминал —
                # и логин/пароль/URL больше негде посмотреть (QA U-09).
                load_credentials 2>/dev/null || true
                show_credentials
                ;;
            1)
                local new_lang
                new_lang=$(pick_language_interactive)
                load_language "$new_lang"
                save_language "$new_lang"
                ;;
            2) restart_xui ;;
            3) repair_user_facing ;;
            4) backup_govless ;;
            5)
                # Pick the most recent backup interactively
                local pick
                local -a backups
                if [ -d /root/govless-backups ]; then
                    while IFS= read -r f; do backups+=("$f"); done < <(ls -t /root/govless-backups/govless-*.tgz 2>/dev/null)
                fi
                if [ ${#backups[@]} -eq 0 ]; then
                    log_warning "$(t backup_no_files)"
                else
                    echo "  $(t restore_pick):"
                    local i=1
                    for b in "${backups[@]}"; do
                        echo -e "    ${CYAN}${i})${NC} $(basename "$b")"
                        i=$((i+1))
                    done
                    read -rp "  ▸ " pick
                    if [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#backups[@]}" ]; then
                        restore_govless "${backups[$((pick-1))]}"
                    else
                        log_warning "$(t invalid_choice)"
                    fi
                fi
                ;;
            6) submenu_remove ;;
            0) return ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Submenu: Remove (NEW — granular: site / panel / everything) ─────────
submenu_remove() {
    while true; do
        print_header "$(t submenu_remove_title)"
        echo -e "  ${CYAN}1)${NC} $(t remove_only_site)"
        echo -e "  ${CYAN}2)${NC} $(t remove_only_panel)"
        echo -e "  ${CYAN}3)${NC} ${RED}${BOLD}$(t remove_everything)${NC}"
        echo -e "  ${CYAN}0)${NC} $(t back)"
        echo ""

        local choice
        read -rp "  ▸ " choice || { echo ""; return; }
        case "$choice" in
            1)
                if typed_confirm "DELETE SITE" "$(t remove_confirm_site)"; then
                    remove_site_only
                fi
                ;;
            2)
                if typed_confirm "DELETE PANEL" "$(t remove_confirm_panel)"; then
                    remove_panel_only
                fi
                ;;
            3)
                if typed_confirm "DELETE EVERYTHING" "$(t remove_confirm_all)"; then
                    remove_everything
                    # After full nuke, exiting is the only sane next step
                    echo ""
                    echo -e "  $(t bye)"
                    exit 0
                fi
                ;;
            0) return ;;
            *)
                log_warning "$(t invalid_choice)"
                sleep 1
                continue
                ;;
        esac
        echo -ne "  $(t press_enter_return) "
        read -r
    done
}

# ── Submenu: About ──────────────────────────────────────────────────────
submenu_about() {
    print_header "$(t submenu_about_title)"
    echo -e "  goVLESS:    v${GOVLESS_VERSION}"
    echo -e "  Engine:     3X-UI + Xray-core"
    echo -e "  Protocol:   VLESS + XTLS-Vision"
    echo -e "  Security:   Reality / TLS"
    echo -e "  Author:     potap1978"
    echo -e "  GitHub:     github.com/potap1978"
    echo -e "  YouTube:    youtube.com/@potap1978"
    show_credits
    # Disclaimer (info-only, no gate — operator can re-read anytime)
    show_disclaimer
    echo -ne "  $(t press_enter_return) "
    read -r
}

# ═══════════════════════════════════════════════════════════════════════
# MODE SELECTION
# ═══════════════════════════════════════════════════════════════════════
select_and_install() {
    # Disclaimer gate — shown on first install; cached in $GOVLESS_DIR/.disclaimer-accepted
    show_disclaimer --gate || return 1

    # Уже установлено: пункт «Установить / Обновить» не должен давать поставить
    # ДРУГОЙ режим поверх — install_lite поверх Pro затирал сайт-маскировку
    # заглушкой и переписывал Pro-vhost (пропадали ACME-location и include
    # Mini App), оставляя mode=lite при TLS-inbound (QA U-01, 22.09.2026).
    # Переустановка — только в текущем режиме; смена режима — VPN → 4;
    # обновление кода — goinstall.
    local cur_mode
    cur_mode="$(config_get mode "")"
    if [ -n "$cur_mode" ] && [ "$cur_mode" != "null" ]; then
        print_header "$(t reinstall_title)"
        echo -e "  $(tf reinstall_current "$cur_mode")"
        echo -e "  ${DIM}$(t reinstall_hint_switch)${NC}"
        echo -e "  ${DIM}$(t reinstall_hint_update)${NC}"
        echo ""
        confirm "$(tf reinstall_confirm "$cur_mode")" || return 0
        case "$cur_mode" in
            pro)  install_pro ;;
            lite) install_lite ;;
            *)    log_error "$(tf ms_unknown_mode_err "$cur_mode")"; return 1 ;;
        esac
        return $?
    fi

    print_header "$(t install_select_mode)"
    echo ""
    echo -e "  ${CYAN}1)${NC} ${BOLD}$(t install_lazy_title)${NC}"
    echo -e "     ${DIM}$(t install_lazy_desc1)${NC}"
    echo -e "     ${DIM}$(t install_lazy_desc2)${NC}"
    echo ""
    echo -e "  ${CYAN}2)${NC} ${BOLD}$(t install_pro_title)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc1)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc2)${NC}"
    echo -e "     ${DIM}$(t install_pro_desc3)${NC}"
    echo ""
    echo -e "  ${CYAN}3)${NC} ${BOLD}$(t install_lite_title)${NC}"
    echo -e "     ${DIM}$(t install_lite_desc1)${NC}"
    echo -e "     ${DIM}$(t install_lite_desc2)${NC}"
    echo -e "     ${DIM}$(t install_lite_desc3)${NC}"
    echo ""

    local mode_choice
    echo -ne "  $(t install_mode_choice) "
        read -r mode_choice || { echo ""; return 1; }

    case "$mode_choice" in
        ""|1) GOVLESS_LAZY=1 install_pro ;;
        2) install_pro ;;
        3) install_lite ;;
        *) log_error "$(t invalid_choice)"; return 1 ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════
# ── Version sync on in-place update ─────────────────────────────────
# A plain `git pull` + re-run updates the code, but the schema-gated migration won't
# fire for a version-only bump, so config.json keeps the OLD version and the bot/WebApp
# (which read it via the daemon) keep reporting it. Detect the drift and: rewrite
# config.json, and — if the Telegram control-plane is installed — redeploy phase-a so the
# daemon/bot/WebApp run the NEW code and report the new version. Idempotent: fires only
# once, right after a code update (afterwards config version == GOVLESS_VERSION).
maybe_sync_version() {
    local cfgver
    cfgver="$(config_get version "")"
    [ -n "$cfgver" ] || return 0
    [ "$cfgver" = "$GOVLESS_VERSION" ] && return 0
    print_header "$(tf version_update_title "$cfgver" "$GOVLESS_VERSION")"
    # Safety net before we rewrite config + redeploy the control-plane: snapshot
    # x-ui.db (clients/inbounds) + goVLESS config to /root/govless-backups so the
    # user can restore_govless if anything looks off. Non-fatal — the sync itself
    # never touches x-ui.db, but a restore point is cheap peace of mind.
    log_info "$(t version_update_backup)"
    backup_govless || log_warning "$(t version_update_backup_warn)"
    local _phasea_ok=1
    if _bot_installed; then
        log_info "$(t version_update_phasea)"
        bot_install_phasea || { log_warning "$(t version_update_phasea_fail)"; _phasea_ok=0; }
    fi
    # Версию пишем ПОСЛЕ перевыкладки phase-a: раньше она записывалась до неё,
    # и при сбое install_phase_a.sh демон и бот навсегда оставались на старом
    # коде — следующий запуск видел «версии совпадают» (QA S-15).
    if [ "$_phasea_ok" = 1 ]; then config_set "version" "$GOVLESS_VERSION"; fi
    # Pro vhost is generated once at install time, so hardening added later
    # (disable_symlinks, the Mini App include) never reached servers that only
    # UPDATED — they kept the old config forever (QA BUG-17). Regenerate it and
    # reload nginx; on a bad config keep the previous file so the site stays up.
    local _mode _domain
    _mode="$(config_get mode "")"
    _domain="$(config_get domain "")"
    if [ "$_mode" = "pro" ] && [ -n "$_domain" ] && [ -f "$NGINX_SITE_CONF" ]; then
        log_info "$(t version_update_nginx)"
        cp -a "$NGINX_SITE_CONF" "${NGINX_SITE_CONF}.pre-update" 2>/dev/null || true
        if generate_nginx_pro_config "$_domain" && nginx -t >/dev/null 2>&1; then
            systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
            rm -f "${NGINX_SITE_CONF}.pre-update" 2>/dev/null
        else
            log_warning "$(t version_update_nginx_fail)"
            [ -f "${NGINX_SITE_CONF}.pre-update" ] && mv -f "${NGINX_SITE_CONF}.pre-update" "$NGINX_SITE_CONF"
        fi
    fi
    log_success "$(tf version_update_done "$GOVLESS_VERSION")"
    echo -ne "  $(t press_enter_return) "
    read -r _ || true
}

# ═══════════════════════════════════════════════════════════════════════
# ── One-time fingerprint-change offer on update (Pro + subscription) ────
# We reordered the fp picker and now recommend `safari` (fixed = faster/more
# stable to connect) over the old `randomized` default. Existing Pro users on a
# subscription can switch seamlessly (their devices just refresh the sub). Offer
# it once: explain the trade-off, warn that after the change they must refresh
# the subscription on devices (or, if they handed out a raw key, re-scan it / set
# the fp in the client app), then ask. Gated so it never nags: fires only for
# Pro-with-subscription that predates the choice, and records fp_offer_done after.
maybe_offer_fingerprint_change() {
    local mode domain sub_port sub_path cur_fp done_flag
    mode="$(config_get mode "lite")"
    domain="$(config_get domain "")"
    # Requires a domain (Pro) AND an active subscription — per the request.
    [ "$mode" = "pro" ] && [ -n "$domain" ] || return 0
    sub_port="$(config_get sub_port "")"
    sub_path="$(config_get sub_path "")"
    [ -n "$sub_port" ] || [ -n "$sub_path" ] || return 0
    done_flag="$(config_get fp_offer_done "")"
    [ -n "$done_flag" ] && return 0
    cur_fp="$(config_get fingerprint "safari")"
    # Already on the recommended default — nothing to offer, just remember.
    if [ "$cur_fp" = "safari" ]; then
        config_set fp_offer_done "1"
        return 0
    fi

    print_header "$(t fp_offer_title)"
    echo -e "  $(tf fp_offer_intro "$cur_fp")" >&2
    echo "" >&2
    echo -e "  $(t fp_offer_explain1)" >&2
    echo -e "  $(t fp_offer_explain2)" >&2
    echo "" >&2
    echo -e "  ${YELLOW}${BOLD}$(t fp_offer_warn_title)${NC}" >&2
    echo -e "  $(t fp_offer_warn_sub)" >&2
    echo -e "  $(t fp_offer_warn_key)" >&2
    echo "" >&2
    echo -ne "  $(t fp_offer_prompt) " >&2
    local ans; read -r ans || true
    case "$ans" in
        y|Y|yes|Yes|YES|д|Д|да|Да|ДА)
            select_fingerprint
            log_info "$(t fp_offer_applying)"
            if apply_fingerprint_to_inbounds "$XUI_FP"; then
                echo "" >&2
                echo -e "  ${GREEN}${BOLD}$(t fp_offer_after_title)${NC}" >&2
                echo -e "  ${GREEN}$(t fp_offer_warn_sub)${NC}" >&2
                echo -e "  ${GREEN}$(t fp_offer_warn_key)${NC}" >&2
            else
                log_warning "$(t fp_offer_failed)"
            fi
            ;;
        *)
            log_info "$(t fp_offer_skipped)"
            ;;
    esac
    config_set fp_offer_done "1"
    echo -ne "  $(t press_enter_return) " >&2
    read -r _ || true
}

main() {
    # ── systemd entry modes for the Telegram-API egress (no banner/preflight) ──
    case "${1:-}" in
        # Без trap EXIT: cleanup_temp_files сносит общие /tmp/govless_cookie.txt
        # и payload-файлы, а пробник потерь запускается таймером каждые 10 с —
        # он выкидывал сессию панели у параллельной смены режима/автонастройки
        # (QA S-03).
        --egress-awg-up)   trap - EXIT; egress_awg_up; exit $? ;;
        --egress-awg-down) trap - EXIT; egress_awg_teardown; exit $? ;;
        --egress-probe)    trap - EXIT; egress_probe_sample; exit $? ;;
        # Cover-site template actions for the govlessctl daemon (bot / Mini App):
        # non-interactive, --json prints one result object on stdout.
        --site-template-*) trap - EXIT; site_template_cli "$@"; exit $? ;;
    esac
    # `curl ... | bash` puts the SCRIPT on stdin, so interactive `read` prompts
    # hit EOF and every gate "declines" no matter what the user types. If stdin
    # isn't a TTY but a controlling terminal exists, reconnect stdin to it.
    # Detached/non-interactive runs (no /dev/tty) keep their piped stdin so
    # answer-ribbon automation still works.
    # Prefer the real controlling terminal whenever it exists: an upstream
    # apt && git clone && sudo chain can leave fd0 a half-closed/erroring tty.
    # NEVER add 2>... here — a command-less exec applies it permanently and would
    # silence the whole UI (all output is on >&2).
    # Only grab /dev/tty if it can ACTUALLY be opened (access()/[ -r ] lies under
    # setsid/detached: says readable but open() gives ENXIO -> a read would hang).
    # Test by opening it for a no-op in a stderr-scoped group; the real exec carries
    # NO 2>... (a command-less exec applies redirects permanently = silences the UI).
    if { : < /dev/tty; } 2>/dev/null; then exec < /dev/tty || true; fi
    init_language
    print_banner

    # Ярлык govless — всегда на текущий код (до early-return установки)
    install_govless_shortcut

    # Чистая установка требует современную ОС; обновление уже установленного
    # goVLESS не блокируется (старые ОС продолжают получать фиксы).
    if ! { is_xui_installed && [ -f "$GOVLESS_CONFIG" ]; }; then
        require_fresh_os
    fi

    # Check disk space
    if ! check_disk_space 500; then
        local avail
        avail=$(df -m / 2>/dev/null | awk 'NR==2 {print $4}')
        log_error "$(tf err_low_disk "${avail:-?}" "500")"
        exit 1
    fi

    # Preflight: verify + install all required packages once, up front, so a
    # missing tool can't surface as a confusing mid-install failure.
    if ! preflight_deps; then
        log_error "$(t preflight_abort)"
        exit 1
    fi

    # If already installed — show menu
    if is_xui_installed && [ -f "$GOVLESS_CONFIG" ]; then
        load_credentials
        setup_api_base
        # Interactive menu: handlers (restart/logs/backup/repair, empty user list, mode
        # switch) legitimately return non-zero; under `set -e` that would eject the
        # user from the menu to the shell. The install flow guards its own steps.
        set +e
        maybe_run_migrations
        maybe_sync_version
        maybe_offer_fingerprint_change
        # Templates catalog for the bot / Mini App follows the installer's copy
        sync_templates_catalog
        # Если порт 443 занимает чужой сервис (goTelegram/telemt и т.п.) — предупредить:
        # xray и он дерутся за порт, поэтому VPN работает нестабильно.
        if ! detect_443_conflict; then
            if [ "$GV_443_KIND" = "listener" ]; then
                log_warning "$(tf port443_warn_listener "$GV_443_CONFLICT")"
            else
                log_warning "$(tf port443_warn_service "$GV_443_CONFLICT")"
            fi
            sleep 2
        fi
        main_menu
    else
        # First run — install. Откажемся, если порт 443 занят другим сервисом
        # (goTelegram/telemt и т.п.) — вместе с ним goVLESS работать не будет.
        if ! detect_443_conflict; then
            print_header "$(t port443_title)"
            if [ "$GV_443_KIND" = "listener" ]; then
                log_error "$(tf port443_listener "$GV_443_CONFLICT")"
            else
                log_error "$(tf port443_service "$GV_443_CONFLICT")"
            fi
            echo "" >&2
            echo -e "  $(t port443_hint)" >&2
            exit 1
        fi
        select_and_install
    fi
}

main "$@"
