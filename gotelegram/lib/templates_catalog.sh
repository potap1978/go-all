#!/bin/bash
# GoTelegram v2.5.0 — website templates catalog
# Pick from ~1800 templates, preview links, git sparse-checkout downloads,
# + custom git URL templates (user-supplied public repos)

CATALOG_FILE="$(dirname "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")")/templates_catalog.json"
# Кэш шаблонов: НЕ фиксированный путь в /tmp (симлинк-атака от локального юзера —
# root перезаписал бы произвольный файл). Каталог создаётся лениво через mktemp -d
# и живёт до конца процесса; переопределяется извне переменной TEMPLATES_CACHE.
TEMPLATES_CACHE="${TEMPLATES_CACHE:-}"

_templates_cache_dir() {
    if [ -z "${TEMPLATES_CACHE:-}" ] || [ ! -d "$TEMPLATES_CACHE" ]; then
        TEMPLATES_CACHE=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_templates.XXXXXXXX") || return 1
        chmod 700 "$TEMPLATES_CACHE" 2>/dev/null || true
    fi
    printf '%s' "$TEMPLATES_CACHE"
}

# Custom git template limits
CUSTOM_GIT_MAX_SIZE_MB=100
CUSTOM_GIT_CLONE_TIMEOUT=90

# ── Catalog loading ────────────────────────────────────────────────────
load_catalog() {
    if [ ! -f "$CATALOG_FILE" ]; then
        if type tf &>/dev/null; then
            log_error "$(tf templates_catalog_not_found "$CATALOG_FILE")"
        else
            log_error "Templates catalog not found: $CATALOG_FILE"
        fi
        return 1
    fi
    return 0
}

# ── Categories ─────────────────────────────────────────────────────────
get_categories() {
    jq -r '.categories[] | "\(.id)|\(.name)|\(.icon)|\(.templates | length)"' "$CATALOG_FILE" 2>/dev/null
}

get_category_name() {
    local cat_id="$1"
    jq -r --arg id "$cat_id" '.categories[] | select(.id == $id) | .name' "$CATALOG_FILE" 2>/dev/null
}

# ── Templates in a category ────────────────────────────────────────────
get_templates_by_category() {
    local cat_id="$1"
    jq -r --arg id "$cat_id" '.categories[] | select(.id == $id) | .templates[] | "\(.id)|\(.name)|\(.source)|\(.preview_url)"' "$CATALOG_FILE" 2>/dev/null
}

# ── Template info ──────────────────────────────────────────────────────
get_template_info() {
    local tpl_id="$1"
    jq --arg id "$tpl_id" '.categories[].templates[] | select(.id == $id)' "$CATALOG_FILE" 2>/dev/null
}

get_template_field() {
    local tpl_id="$1"
    local field="$2"
    jq -r --arg id "$tpl_id" --arg f "$field" '.categories[].templates[] | select(.id == $id) | .[$f]' "$CATALOG_FILE" 2>/dev/null
}

# ── Interactive category picker (returns category id or special __custom_git__/__random__) ──
select_category() {
    load_catalog || return 1

    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t templates_categories)${NC}" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2

    # First item: custom git URL template
    printf "  ${CYAN}%2d)${NC} ${GREEN}%s${NC}\n" 1 "$(t templates_custom_git)" >&2

    local cats=()
    local i=2
    while IFS='|' read -r id name icon count; do
        [ "$count" -eq 0 ] && continue
        local emoji
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
        printf "  ${CYAN}%2d)${NC} ${emoji} %-30s ${DIM}$(tf templates_count_fmt "$count")${NC}\n" "$i" "$name" >&2
        cats+=("$id")
        ((i++))
    done < <(get_categories)

    printf "  ${CYAN}%2d)${NC} %s\n" "$i" "$(t templates_random)" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
    echo -ne "  ${WHITE}$(t choose):${NC} " >&2
    read -r choice

    if ! [[ "$choice" =~ ^[0-9]+$ ]]; then
        log_error "$(t invalid_choice)"
        return 1
    fi

    # Custom git URL
    if [ "$choice" -eq 1 ]; then
        echo "__custom_git__"
        return 0
    fi

    # Random
    if [ "$choice" -eq "$i" ]; then
        local random_cat="${cats[$((RANDOM % ${#cats[@]}))]}"
        echo "$random_cat"
        return 0
    fi

    # Regular category (offset by 1 because item 1 is custom git)
    if [ "$choice" -ge 2 ] && [ "$choice" -lt "$i" ]; then
        echo "${cats[$((choice-2))]}"
        return 0
    fi

    log_error "$(t invalid_choice)"
    return 1
}

# ── Interactive template picker ────────────────────────────────────────
select_template() {
    local cat_id="$1"
    local cat_name
    cat_name=$(get_category_name "$cat_id")
    # Категории теперь на тысячи шаблонов: список показывается страницами по
    # 25, есть поиск по названию и случайный выбор. Раньше «Лендинги» выводили
    # три тысячи строк одним махом.
    local all=() ids=() names=() srcs=() id name source preview
    while IFS='|' read -r id name source preview; do
        [ -n "$id" ] || continue
        ids+=("$id"); names+=("$name"); srcs+=("$source")
    done < <(get_templates_by_category "$cat_id")
    if [ ${#ids[@]} -eq 0 ]; then
        log_info "$(t templates_cat_empty)"
        return 1
    fi
    local per=25 page=0 filter="" view=() k choice total pages start end n
    while :; do
        view=()
        for k in "${!ids[@]}"; do
            if [ -n "$filter" ]; then
                [[ "${names[$k],,}" == *"${filter,,}"* || "${ids[$k],,}" == *"${filter,,}"* ]] || continue
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
        read -r choice
        case "$choice" in
            0|"") return 1 ;;
            n|N|+|.) [ "$page" -lt $((pages-1)) ] && page=$((page+1)); continue ;;
            p|P|-|,) [ "$page" -gt 0 ] && page=$((page-1)); continue ;;
            r|R)
                [ "$total" -eq 0 ] && continue
                k=${view[$(( RANDOM % total ))]}
                show_template_preview "${ids[$k]}" || continue
                echo "${ids[$k]}"; return 0 ;;
            /*|s\ *|S\ *)
                filter="${choice#/}"; filter="${filter#s }"; filter="${filter#S }"; page=0; continue ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$total" ]; then
                    k=${view[$((choice-1))]}
                    show_template_preview "${ids[$k]}" || continue
                    echo "${ids[$k]}"; return 0
                fi
                # любое другое слово — поиск по названию
                filter="$choice"; page=0; continue ;;
        esac
    done
}

# ── Template preview ───────────────────────────────────────────────────
show_template_preview() {
    local tpl_id="$1"
    local info
    info=$(get_template_info "$tpl_id")

    local name source preview_url repo_url description
    name=$(echo "$info" | jq -r '.name')
    source=$(echo "$info" | jq -r '.source')
    preview_url=$(echo "$info" | jq -r '.preview_url // empty')
    repo_url=$(echo "$info" | jq -r '.repo_url // empty')
    description=$(echo "$info" | jq -r '.description // "—"')

    echo "" >&2
    echo -e "  ${BOLD}${WHITE}$(t templates_preview_title)${NC}" >&2
    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
    echo -e "  ${WHITE}$(t templates_name)${NC}  $name" >&2
    echo -e "  ${WHITE}$(t templates_source)${NC}  $source" >&2
    echo -e "  ${WHITE}$(t templates_description)${NC}  $description" >&2

    if [ -n "$preview_url" ]; then
        echo "" >&2
        echo -e "  ${GREEN}$(t templates_preview)${NC} ${CYAN}${preview_url}${NC}" >&2
        echo -e "  ${DIM}$(t templates_preview_hint)${NC}" >&2
    fi

    if [ -n "$repo_url" ]; then
        echo -e "  ${DIM}$(t templates_repo)    ${repo_url}${NC}" >&2
    fi
    local license
    license=$(echo "$info" | jq -r '.license // empty')
    [ -n "$license" ] && echo -e "  ${DIM}$(t templates_license)  ${license}${NC}" >&2

    # Thanks
    echo "" >&2
    # Для общего источника github благодарим владельца репозитория, а не «github».
    local _thanks="$source"
    if [ "$source" = "github" ] && [[ "$repo_url" =~ github\.com/([^/]+)/ ]]; then _thanks="${BASH_REMATCH[1]}"; fi
    echo -e "  ${MAGENTA}$(tf templates_thanks "$_thanks")${NC}" >&2

    echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}" >&2
    echo "" >&2

    if ! confirm "$(t templates_install_this)"; then
        return 1
    fi
    return 0
}

# ── Безопасный git clone ───────────────────────────────────────────────
# repo_url приходит из каталога, но каталог может быть подменён (или подобран
# tpl_id-ом при склейке в jq). Транспорты вида "ext::sh -c <cmd>" в URL дают
# выполнение команд от root, поэтому: только https, только явно разрешённый
# протокол, и "--" перед URL (иначе URL, начинающийся с "-", станет опцией).
_tpl_repo_url_ok() {
    local u="${1:-}"
    [ -n "$u" ] || return 1
    [ "${#u}" -le 512 ] || return 1
    [[ "$u" =~ ^https:// ]] || return 1
    # никаких shell-метасимволов и пробелов в URL
    [[ "$u" =~ [[:space:]\;\`\$\(\)\<\>\|\\\&\'\"] ]] && return 1
    return 0
}

# _tpl_git_clone <url> <dest> [доп. флаги git clone...]
_tpl_git_clone() {
    local url="${1:-}" dest="${2:-}"
    shift 2 2>/dev/null || return 1
    if ! _tpl_repo_url_ok "$url"; then
        if type log_error &>/dev/null; then
            log_error "Небезопасный или пустой repo_url шаблона: ${url:-<пусто>}"
        else
            echo "Небезопасный repo_url: ${url:-<пусто>}" >&2
        fi
        return 1
    fi
    # Ошибку git сохраняем: вызывающие глушат stderr, и человек видел
    # «нет index.html» там, где на самом деле не было связи с GitHub.
    _TPL_GIT_ERR="${_TPL_GIT_ERR:-$(mktemp "${TMPDIR:-/tmp}/gotelegram_git_err.XXXXXXXX" 2>/dev/null)}"
    GIT_TERMINAL_PROMPT=0 git \
        -c protocol.allow=never \
        -c protocol.https.allow=always \
        -c protocol.ext.allow=never \
        -c protocol.file.allow=never \
        clone --depth 1 "$@" -- "$url" "$dest" 2>"${_TPL_GIT_ERR:-/dev/null}"
}

# Где в репозитории лежит сама страница. Порядок важен: dist/ и build/ — сборка,
# корень — обычный шаблон, дальше типовые папки; остальное — по возрастанию
# глубины, минуя документацию, исходники и оболочки приложений. Раньше брался
# первый попавшийся `find … | head -1`, и вместо шаблона уезжала
# documentation/index.html или src/ Angular-проекта.
_tpl_pick_index_dir() {
    local base="$1" d
    for d in dist build . html theme live public_html site _site; do
        [ -f "$base/$d/index.html" ] && { printf '%s' "$base/$d"; return 0; }
    done
    # Два прохода: сначала минуя документацию, исходники и примеры; если пусто —
    # ещё раз, разрешив examples/ и docs/ (у части шаблонов страницы лежат там).
    d=$(find "$base" -mindepth 2 -maxdepth 5 -type f -name index.html \
        -not -path '*/documentation/*' -not -path '*/docs/*' -not -path '*/src/*' \
        -not -path '*/public/*' -not -path '*/node_modules/*' -not -path '*/.github/*' \
        -not -path '*/test/*' -not -path '*/tests/*' -not -path '*/example*' -not -path '*/.git/*' 2>/dev/null \
        | awk '{ print gsub("/","/") "\t" $0 }' | sort -n | head -1 | cut -f2-)
    [ -z "$d" ] && d=$(find "$base" -mindepth 2 -maxdepth 5 -type f -name index.html \
        -not -path '*/documentation/*' -not -path '*/src/*' -not -path '*/node_modules/*' \
        -not -path '*/.github/*' -not -path '*/.git/*' 2>/dev/null \
        | awk '{ print gsub("/","/") "\t" $0 }' | sort -n | head -1 | cut -f2-)
    [ -n "$d" ] && { printf '%s' "$(dirname "$d")"; return 0; }
    # index.html нет, но есть home.html / main.html — это та же главная
    for d in home.html main.html Home.html; do
        [ -f "$base/$d" ] && { cp "$base/$d" "$base/index.html" 2>/dev/null && printf '%s' "$base" && return 0; }
    done
    return 1
}

# Готовая ли это страница. index.html есть и у оболочек CRA/Vite (в /var/www
# дают белый экран), и у шаблонов, чьи css/js в репозитории отсутствуют.
# Код причины — в TEMPLATE_DL_ERROR.
_tpl_page_ok() {
    local dir="$1" idx="$1/index.html" txt refs missing=0 total=0 ref f
    [ -f "$idx" ] || { TEMPLATE_DL_ERROR="no_index"; return 1; }
    if grep -q '%PUBLIC_URL%' "$idx" 2>/dev/null || grep -qE 'src="\.?/?src/[^"]*\.(jsx?|tsx?)"' "$idx" 2>/dev/null; then
        TEMPLATE_DL_ERROR="app_shell"; return 1
    fi
    txt=$(sed -e 's/<script[^>]*>.*<\/script>//g' -e 's/<style[^>]*>.*<\/style>//g' -e 's/<[^>]*>/ /g' "$idx" 2>/dev/null | tr -s ' \t\n' ' ' | wc -c)
    if [ "${txt:-0}" -lt 300 ] && ! grep -qiE '<img|background(-image)?:' "$idx" 2>/dev/null; then
        TEMPLATE_DL_ERROR="app_shell"; return 1
    fi
    # локальные css/js: если их несколько и НИ ОДНОГО нет на месте — вёрстка битая
    refs=$(grep -oiE '(href|src)="[^"#?:]+\.(css|js)(\?[^"]*)?"' "$idx" 2>/dev/null | sed -E 's/^[a-zA-Z]+="//; s/"$//; s/\?.*$//' | grep -vE '^(https?:)?//' | head -20)
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

# ── Template download (from catalog) ───────────────────────────────────
download_template() {
    local tpl_id="$1"
    local output_dir="${2:-}"
    if [ -z "$output_dir" ]; then
        _templates_cache_dir >/dev/null || return 1
        output_dir="$TEMPLATES_CACHE"
    fi
    local info
    info=$(get_template_info "$tpl_id")

    local repo_url sparse_path source name
    repo_url=$(echo "$info" | jq -r '.repo_url // empty')
    sparse_path=$(echo "$info" | jq -r '.sparse_path // empty')
    source=$(echo "$info" | jq -r '.source // empty')
    name=$(echo "$info" | jq -r '.name // empty')

    # Шаблон обязан иметь https-репозиторий: всё остальное — потенциальный RCE
    if ! _tpl_repo_url_ok "$repo_url"; then
        log_error "Шаблон ${tpl_id}: недопустимый repo_url (ожидается https://...)"
        return 1
    fi

    local clone_dir="$output_dir/${tpl_id}"
    rm -rf "$clone_dir"
    mkdir -p "$clone_dir"
    # Скачался ли репозиторий вообще — отдельно от «нашлась ли страница»:
    # файлы в clone_dir появляются только когда index.html найден, и по нему
    # клон от отсутствия страницы не отличить.
    _TPL_CLONED=0
    _tpl_mark_cloned() { [ -n "$(ls -A "$1" 2>/dev/null | grep -v '^\.git$')" ] && _TPL_CLONED=1; return 0; }

    log_info "$(tf templates_downloading "$name")"

    # HTML5 UP — one repo with folders
    if [ "$source" = "html5up" ]; then
        local tmp_clone
        tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_html5up.XXXXXXXX") || return 1

        # Sparse checkout
        if ! _tpl_git_clone "$repo_url" "$tmp_clone" --filter=blob:none --sparse 2>/dev/null; then
            # Fallback: full clone (в уже созданный пустой каталог)
            rm -rf "$tmp_clone"; mkdir -p "$tmp_clone"
            _tpl_git_clone "$repo_url" "$tmp_clone" 2>/dev/null
        fi

        if [ -d "$tmp_clone" ]; then
            git -C "$tmp_clone" sparse-checkout set "$sparse_path" 2>/dev/null
            _tpl_mark_cloned "$tmp_clone"
            if [ -d "$tmp_clone/$sparse_path" ]; then
                cp -r "$tmp_clone/$sparse_path/." "$clone_dir/"
            fi
        fi
        rm -rf "$tmp_clone"

    # learning-zone — one big repo
    elif [ "$source" = "learning-zone" ]; then
        local tmp_clone
        tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_lz.XXXXXXXX") || return 1

        if ! _tpl_git_clone "$repo_url" "$tmp_clone" --filter=blob:none --sparse 2>/dev/null; then
            rm -rf "$tmp_clone"; mkdir -p "$tmp_clone"
            _tpl_git_clone "$repo_url" "$tmp_clone" 2>/dev/null
        fi

        if [ -d "$tmp_clone" ]; then
            git -C "$tmp_clone" sparse-checkout set "$sparse_path" 2>/dev/null
            _tpl_mark_cloned "$tmp_clone"
            if [ -d "$tmp_clone/$sparse_path" ]; then
                cp -r "$tmp_clone/$sparse_path/." "$clone_dir/"
            fi
        fi
        rm -rf "$tmp_clone"

    # StartBootstrap — each template in its own repo
    elif [ "$source" = "startbootstrap" ]; then
        local sb_tmp
        sb_tmp=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_sb.XXXXXXXX") || return 1
        _tpl_git_clone "$repo_url" "$sb_tmp" 2>/dev/null
        if [ -d "$sb_tmp" ]; then
            _tpl_mark_cloned "$sb_tmp"
            rm -rf "$sb_tmp/.git"
            local sb_dir
            if sb_dir=$(_tpl_pick_index_dir "$sb_tmp"); then
                cp -r "$sb_dir/." "$clone_dir/"
            fi
        fi
        rm -rf "$sb_tmp"

    # ThemeWagon / ColorlibHQ / github — each template in its own repo.
    # «github» — общий источник для шаблонов, найденных по всему GitHub: клон
    # целиком, страница в корне либо в dist/public/docs/build.
    elif [ "$source" = "themewagon" ] || [ "$source" = "colorlib" ] || [ "$source" = "github" ]; then
        local tw_tmp
        tw_tmp=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_tw.XXXXXXXX") || return 1
        _tpl_git_clone "$repo_url" "$tw_tmp" 2>/dev/null
        if [ -d "$tw_tmp" ]; then
            _tpl_mark_cloned "$tw_tmp"
            rm -rf "$tw_tmp/.git"
            local tw_dir
            if tw_dir=$(_tpl_pick_index_dir "$tw_tmp"); then
                cp -r "$tw_dir/." "$clone_dir/"
            fi
        fi
        rm -rf "$tw_tmp"

    # dawidolko — one big repo with folders (similar to learning-zone)
    elif [ "$source" = "dawidolko" ]; then
        local tmp_clone
        tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_dw.XXXXXXXX") || return 1
        if ! _tpl_git_clone "$repo_url" "$tmp_clone" --filter=blob:none --sparse 2>/dev/null; then
            rm -rf "$tmp_clone"; mkdir -p "$tmp_clone"
            _tpl_git_clone "$repo_url" "$tmp_clone" 2>/dev/null
        fi
        if [ -d "$tmp_clone" ]; then
            git -C "$tmp_clone" sparse-checkout set "$sparse_path" 2>/dev/null
            _tpl_mark_cloned "$tmp_clone"
            if [ -d "$tmp_clone/$sparse_path" ]; then
                cp -r "$tmp_clone/$sparse_path/." "$clone_dir/"
            fi
        fi
        rm -rf "$tmp_clone"
    fi

    # Check result: страница не только есть, но и готова (не оболочка, не битая)
    rm -rf "$clone_dir/.git" 2>/dev/null || true
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
        # fallback: страница в подпапке (нестандартная структура)
        local fallback_dir
        if fallback_dir=$(_tpl_pick_index_dir "$clone_dir") && [ "$fallback_dir" != "$clone_dir" ] && [ "$fallback_dir" != "$clone_dir/." ]; then
            cp -r "$fallback_dir/." "$clone_dir/"
            if _tpl_page_ok "$clone_dir"; then
                log_success "$(tf templates_downloaded_subfolder "$name")"
                echo "$clone_dir"
                return 0
            fi
        fi
        # Две разные беды под одной надписью «нет index.html» путали людей:
        # пустой каталог — репозиторий не скачался (сеть, GitHub, репозиторий
        # удалён); файлы есть, а страницы нет — в каталог попали исходники для
        # сборки (Next.js, Vite, Tailwind-стартеры), готового сайта там нет.
        TEMPLATE_DL_ERROR="no_index"
        [ "${_TPL_CLONED:-0}" = "1" ] || TEMPLATE_DL_ERROR="download_failed"
        # download_template почти всегда зовут через $(...): переменная из
        # подоболочки не доходит, поэтому код причины дублируем в файл.
        printf '%s' "$TEMPLATE_DL_ERROR" > "$output_dir/.last_error" 2>/dev/null || true
        if [ "$TEMPLATE_DL_ERROR" = "download_failed" ]; then
            log_error "$(tf templates_download_failed "$name")"
            [ -n "${repo_url:-}" ] && log_dim "$(tf templates_repo_line "$repo_url")"
            if [ -s "${_TPL_GIT_ERR:-/nonexistent}" ]; then
                grep -vE '^\s*$|^Cloning into|^Клонирование' "$_TPL_GIT_ERR" | tail -n 3 | sed 's/^/      git: /' >&2
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
# Rejects: ssh://, git://, file://, absolute file paths
_validate_custom_git_url() {
    local url="$1"
    # Must begin with https://
    [[ "$url" =~ ^https:// ]] || return 1
    # Reject shell metacharacters that could be exploited
    [[ "$url" =~ [[:space:]\;\`\$\(\)\<\>\|\\\&] ]] && return 1
    # Reasonable length limit
    [ "${#url}" -gt 512 ] && return 1
    return 0
}

# Parse URL → sets CUSTOM_GIT_CLEAN and CUSTOM_GIT_BRANCH globals
_parse_custom_git_url() {
    local url="$1"
    CUSTOM_GIT_CLEAN=""
    CUSTOM_GIT_BRANCH=""
    # Handle trailing @branch
    if [[ "$url" =~ ^(https://[^@]+)@([A-Za-z0-9._/-]+)$ ]]; then
        CUSTOM_GIT_CLEAN="${BASH_REMATCH[1]}"
        CUSTOM_GIT_BRANCH="${BASH_REMATCH[2]}"
    else
        CUSTOM_GIT_CLEAN="$url"
    fi
    # Strip trailing slash
    CUSTOM_GIT_CLEAN="${CUSTOM_GIT_CLEAN%/}"
    # Append .git if missing (works better with git clone on some hosts)
    if [[ ! "$CUSTOM_GIT_CLEAN" =~ \.git$ ]]; then
        CUSTOM_GIT_CLEAN="${CUSTOM_GIT_CLEAN}.git"
    fi
}

# Check repo size (in MB) by inspecting cloned directory
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
# Prompts user for a URL (unless passed), clones, detects index.html,
# copies result into $output_dir/custom_<hash>, echoes the final path.
download_custom_git_template() {
    local url="${1:-}"
    local output_dir="${2:-}"
    if [ -z "$output_dir" ]; then
        _templates_cache_dir >/dev/null || return 1
        output_dir="$TEMPLATES_CACHE"
    fi

    show_custom_git_help

    if [ -z "$url" ]; then
        echo -ne "  ${WHITE}$(t custom_git_enter_url)${NC} " >&2
        read -r url
        url=$(echo "$url" | tr -d '\r\n[:space:]')
    fi

    if [ -z "$url" ]; then
        log_error "$(t custom_git_empty)"
        return 1
    fi

    if ! _validate_custom_git_url "$url"; then
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
    # mktemp вместо предсказуемого /tmp/custom_git_clone_$$ — иначе локальный
    # непривилегированный юзер заранее кладёт симлинк и root пишет куда угодно.
    local tmp_clone err_file
    tmp_clone=$(mktemp -d "${TMPDIR:-/tmp}/gotelegram_custom_git.XXXXXXXX") || return 1
    err_file=$(mktemp "${TMPDIR:-/tmp}/gotelegram_custom_git_err.XXXXXXXX") || { rm -rf "$tmp_clone"; return 1; }

    rm -rf "$clone_dir"
    mkdir -p "$clone_dir"

    log_info "$(t custom_git_cloning)"

    # Clone with timeout so a hung server can't freeze the installer
    local clone_status=0
    # только https, ext::/file:: запрещены явно (иначе URL = выполнение команд от root)
    local git_args=(-c protocol.allow=never -c protocol.https.allow=always
                    -c protocol.ext.allow=never -c protocol.file.allow=never
                    clone --depth 1)
    [ -n "$branch" ] && git_args+=(--branch "$branch")
    git_args+=(-- "$clean_url" "$tmp_clone")

    if command -v timeout &>/dev/null; then
        GIT_TERMINAL_PROMPT=0 timeout "$CUSTOM_GIT_CLONE_TIMEOUT" git "${git_args[@]}" 2>"$err_file"
        clone_status=$?
    else
        GIT_TERMINAL_PROMPT=0 git "${git_args[@]}" 2>"$err_file"
        clone_status=$?
    fi

    if [ $clone_status -ne 0 ] || [ -z "$(ls -A "$tmp_clone" 2>/dev/null)" ]; then
        local err_msg
        err_msg=$(head -3 "$err_file" 2>/dev/null | tr '\n' ' ')
        rm -f "$err_file"
        rm -rf "$tmp_clone" "$clone_dir"
        log_error "$(tf custom_git_clone_failed "${err_msg:-$clone_status}")"
        return 1
    fi
    rm -f "$err_file"

    # Drop .git before measuring size (we only care about payload)
    rm -rf "$tmp_clone/.git"

    # Size guard
    local size_mb
    size_mb=$(_clone_dir_size_mb "$tmp_clone")
    if [ -n "$size_mb" ] && [ "$size_mb" -gt "$CUSTOM_GIT_MAX_SIZE_MB" ]; then
        rm -rf "$tmp_clone" "$clone_dir"
        log_error "$(tf custom_git_too_big "${size_mb}MB")"
        return 1
    fi

    log_info "$(t custom_git_scanning)"

    # Priority list of common static-site output folders
    local candidates=("" "dist" "public" "build" "_site" "site" "docs" "out" "www")
    local found_dir=""
    for sub in "${candidates[@]}"; do
        local try_dir="$tmp_clone"
        [ -n "$sub" ] && try_dir="$tmp_clone/$sub"
        if [ -f "$try_dir/index.html" ]; then
            found_dir="$try_dir"
            break
        fi
    done

    # Fallback: search for any index.html in the repo (shallow depth first)
    if [ -z "$found_dir" ]; then
        local fallback_index
        fallback_index=$(find "$tmp_clone" -maxdepth 4 -name "index.html" -type f 2>/dev/null | head -1)
        if [ -n "$fallback_index" ]; then
            found_dir=$(dirname "$fallback_index")
        fi
    fi

    if [ -z "$found_dir" ] || [ ! -f "$found_dir/index.html" ]; then
        rm -rf "$tmp_clone" "$clone_dir"
        log_error "$(t custom_git_no_index)"
        return 1
    fi

    # Show what we found (human-friendly relative path)
    local rel_path="${found_dir#$tmp_clone}"
    rel_path="${rel_path#/}"
    [ -z "$rel_path" ] && rel_path="(root)"
    log_dim "$(tf custom_git_found_at "$rel_path")"

    # Copy the detected directory as the new template
    cp -r "$found_dir"/* "$clone_dir/" 2>/dev/null
    cp -r "$found_dir"/.[!.]* "$clone_dir/" 2>/dev/null

    rm -rf "$tmp_clone"

    if [ ! -f "$clone_dir/index.html" ]; then
        rm -rf "$clone_dir"
        log_error "$(t custom_git_no_index)"
        return 1
    fi

    # Remember the URL so users can see what template they used
    echo "$url" > "$clone_dir/.custom_git_source" 2>/dev/null

    log_success "$(tf custom_git_installed "$url")"
    echo "$clone_dir"
    return 0
}

# ── Full interactive template selection ───────────────────────────────
interactive_template_selection() {
    load_catalog || return 1

    # Category selection
    local cat_id
    cat_id=$(select_category)
    [ $? -ne 0 ] && return 1

    # Custom git URL path
    if [ "$cat_id" = "__custom_git__" ]; then
        local template_dir
        template_dir=$(download_custom_git_template)
        [ $? -ne 0 ] && return 1
        echo "$template_dir"
        return 0
    fi

    # Выбор и загрузка. Неудачная загрузка (в каталоге попадаются репозитории
    # без готовой страницы) раньше выбрасывала в меню — человек начинал путь с
    # категории заново. Теперь возвращаемся к списку той же категории.
    local tpl_id template_dir attempt=0 again
    while :; do
        tpl_id=$(select_template "$cat_id")
        [ $? -ne 0 ] && return 1
        template_dir=$(download_template "$tpl_id")
        if [ $? -eq 0 ] && [ -n "$template_dir" ]; then
            echo "$template_dir"
            return 0
        fi
        attempt=$((attempt+1))
        [ "$attempt" -ge 5 ] && return 1
        echo -ne "  ${WHITE}$(t templates_try_another) [Y/n]:${NC} " >&2
        read -r again
        case "$again" in n|N|н|Н) return 1 ;; esac
    done
}


# pick_random_template_id — случайный id шаблона из каталога (для ленивого режима).
pick_random_template_id() {
    [ -f "$CATALOG_FILE" ] || return 1
    jq -r '.categories[].templates[].id' "$CATALOG_FILE" 2>/dev/null | shuf -n1
}
