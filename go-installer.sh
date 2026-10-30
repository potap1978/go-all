#!/bin/bash
# goInstaller — Copyright (c) 2026 potap1978. All rights reserved.
#
# Единый установщик продуктов серии go: goVLESS pro, goTelegram pro,
# goDNS pro, goKaskad pro, goWARP pro, goAmnezia pro. Сам ничего не настраивает — проверяет машину,
# порты и соседей ДО запуска штатного установщика продукта, а потом отдаёт
# управление ему. Каждый продукт ставится своим собственным кодом.
#
#   GH_TOKEN=<ключ на чтение> bash go-installer.sh
#   bash go-installer.sh --list              # что установлено
#
# После первого запуска доступна команда `goinstall`. Ключ доступа хранится
# в /opt/go-installer/token (0600) и проверяется при каждом запуске запросом
# к репозиториям продуктов; не подошёл — установщик попросит новый.
#   bash go-installer.sh --ports             # кто какие порты держит
#   bash go-installer.sh --check govless pro # проверка портов без установки
#   bash go-installer.sh --install godns     # сразу к установке продукта
#
# Один ключ на все закрытые репозитории: GH_TOKEN (принимаются также
# GOTELEGRAM_PAT, GITHUB_PAT, GOINST_TOKEN). В аргументы curl ключ не
# попадает: /proc/<pid>/cmdline читает любой локальный пользователь.
#
# Локальное дерево вместо GitHub: GOINST_SRC_<id>=/путь, где id — один из
# govless gotelegram godns gokaskad gowarp. Ветка: GOINST_REF_<id>.

set -uo pipefail

GOINST_VERSION="1.0.12"
GOINST_DIR="${GOINST_DIR:-/opt/go-installer}"
GOINST_BIN="${GOINST_BIN:-/usr/local/bin/goinstall}"
GOINST_BIN_ALIAS="${GOINST_BIN_ALIAS:-/usr/local/bin/go-installer}"
GOINST_SELF_REPO="${GOINST_SELF_REPO:-potap1978/go-all}"
GOINST_SELF_REF="${GOINST_SELF_REF:-main}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'

PRODUCTS=(govless gotelegram godns gokaskad gowarp goamnezia)

# ── язык ────────────────────────────────────────────────────────────────
# Установщик работает раньше любого продукта, своей таблицы строк у него
# нет — две фразы рядом. Порядок: маркер соседа → локаль → русский.
# Файл, где запомнен язык, выбранный в самом goInstaller.
lang_file() { printf '%s' "${GOINST_LANG_FILE:-${GOINST_DIR}/.language}"; }

# _lang_marker <файл> — «ru» или «en» из маркера языка, иначе rc 1.
_lang_marker() {
    local v=""
    [ -r "$1" ] || return 1
    IFS= read -r v < "$1" 2>/dev/null || true
    case "$v" in
        ru*) printf 'ru' ;;
        en*) printf 'en' ;;
        *) return 1 ;;
    esac
}

# Язык, который оператор уже выбрал в соседнем продукте на этой машине.
# goAmnezia пишет такой же маркер; пока его не читали, выбор, сделанный в
# нём, установщик не видел.
lang_neighbour() {
    local r="${GOINST_FS_ROOT:-}" f
    for f in "$r/opt/godns/.language" "$r/opt/govless/.language" \
             "$r/opt/gotelegram/.language" "$r/opt/goamnezia/.language"; do
        _lang_marker "$f" && return 0
    done
    return 1
}

# Язык без вопроса: для справочных команд и запусков без терминала. Меню и
# установка спрашивают его сами первым экраном, см. lang_startup.
_pick_lang() {
    _lang_marker "$(lang_file)" && return 0
    lang_neighbour && return 0
    case "${LC_ALL:-${LANG:-}}" in
        ru*|RU*) printf 'ru'; return 0 ;;
        en*|EN*|C.*|C|POSIX) printf 'en'; return 0 ;;
    esac
    printf 'ru'
}
LANG_CODE="${GOINST_LANG:-$(_pick_lang)}"
bi() { if [ "$LANG_CODE" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# Наличие команды; GOINST_FAKE_CMDS="warp-cli" — подмена для тестов.
have_cmd() {
    if [ -n "${GOINST_FAKE_CMDS+x}" ]; then
        case " ${GOINST_FAKE_CMDS} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
    fi
    command -v "$1" >/dev/null 2>&1
}

# PF_CAPTURE — проверка портов идёт для экрана «Аркады»: сообщения правил
# соседства ещё и складываются в PF_MSGS, экран покажет их сам.
say()  { [ -n "${PF_CAPTURE:-}" ] && PF_MSGS+=("i|$*"); echo -e "  ${CYAN}ℹ${NC}  $*" >&2; }
ok()   { [ -n "${PF_CAPTURE:-}" ] && PF_MSGS+=("o|$*"); echo -e "  ${GREEN}✓${NC}  $*" >&2; }
warn() { [ -n "${PF_CAPTURE:-}" ] && PF_MSGS+=("w|$*"); echo -e "  ${YELLOW}!${NC}  $*" >&2; }
err()  { [ -n "${PF_CAPTURE:-}" ] && PF_MSGS+=("e|$*"); echo -e "  ${RED}✗${NC}  $*" >&2; }
die()  { err "$*"; exit 1; }
hr()   { echo -e "  ${DIM}$(printf '─%.0s' {1..60})${NC}"; }

print_header() {
    echo "" >&2
    echo -e "  ${CYAN}${BOLD}$1${NC}" >&2
    hr >&2
}

# Enter и Ctrl-D — разные ответы. Код 0 и пустая строка — Enter (умолчание),
# код 1 при пустой переменной — отмена. Код 1 с непустой — тоже ответ
# (строка без перевода строки).
read_line() {
    local __var="$1" __prompt="${2:-  ▸ }" __v=""
    if IFS= read -rp "$__prompt" __v; then
        printf -v "$__var" '%s' "$__v"; return 0
    fi
    printf -v "$__var" '%s' "$__v"
    [ -n "$__v" ] && return 0
    echo ""; return 1
}

# confirm <вопрос> <умолчание y|n>
#
# Ответы ТОЛЬКО цифрами: 1 — да, 2 — нет. Буквенное «[Y/n]» на русской
# раскладке требует переключения ради одного символа, а в остальных
# вопросах установщика и так жмут цифры — две разные привычки в одном
# разговоре человек не держит.
confirm() {
    local q="$1" def="${2:-n}" ans tries=0 mark_yes="" mark_no=""
    if [ "$def" = "y" ]; then mark_yes=" ${DIM}— $(bi "по умолчанию" "default")${NC}"
    else mark_no=" ${DIM}— $(bi "по умолчанию" "default")${NC}"; fi
    while [ "$tries" -lt 3 ]; do
        tries=$((tries+1))
        echo "" >&2
        echo -e "  $q" >&2
        echo -e "  ${CYAN}1)${NC} $(bi "Да" "Yes")${mark_yes}" >&2
        echo -e "  ${CYAN}2)${NC} $(bi "Нет" "No")${mark_no}" >&2
        read_line ans || return 1
        if [ -z "$ans" ]; then
            [ "$def" = "y" ] && return 0
            return 1
        fi
        case "$ans" in
            1) return 0 ;;
            2) return 1 ;;
            *) warn "$(bi "Ответьте цифрой: 1 — да, 2 — нет." "Answer with a digit: 1 for yes, 2 for no.")" ;;
        esac
    done
    return 1
}

# ── язык: спрашиваем первым делом ───────────────────────────────────────
# Угадывать по локали нельзя: на сервере со штатной en_US.UTF-8 русскому
# подписчику доставался английский, а SSH нередко присылает ту же en_US.
# Язык спрашивается первым экраном, ДО проверок: иначе отказ по ОС или
# «Проверяю ключ…» уже прозвучали бы на чужом языке. Спрошенное
# запоминается; сменить — пунктом меню, задать без вопроса — GOINST_LANG.
LANG_ASKED=""

# lang_pick [умолчание] — экран выбора. Ставит LANG_CODE; rc 1 — отказ.
# Экран сам двуязычный: язык человека ещё неизвестен.
lang_pick() {
    local def="${1:-}" c tries=0 m1="" m2=""
    case "$def" in
        ru|en) ;;
        *) def=$(lang_neighbour) || def="ru" ;;
    esac
    if [ "$def" = "en" ]; then m2=" ${DIM}Enter${NC}"; else m1=" ${DIM}Enter${NC}"; fi
    while [ "$tries" -lt 3 ]; do
        tries=$((tries+1))
        echo "" >&2
        echo -e "  ${CYAN}${BOLD}goInstaller ${GOINST_VERSION}${NC}" >&2
        echo -e "  Выберите язык / Choose language" >&2
        echo -e "  ${CYAN}1)${NC} Русский${m1}" >&2
        echo -e "  ${CYAN}2)${NC} English${m2}" >&2
        read_line c || return 1
        if [ -z "$c" ]; then
            if [ "$def" = "en" ]; then c=2; else c=1; fi
        fi
        case "$c" in
            1) LANG_CODE="ru"; LANG_ASKED="yes"; return 0 ;;
            2) LANG_CODE="en"; LANG_ASKED="yes"; return 0 ;;
            *) echo -e "  ${YELLOW}!${NC}  Ответьте цифрой 1 или 2 / Answer with 1 or 2" >&2 ;;
        esac
    done
    return 1
}

# Спросить язык, если он ещё не выбран и есть с кем говорить.
lang_startup() {
    [ -z "${GOINST_LANG:-}" ] || return 0
    _lang_marker "$(lang_file)" >/dev/null && return 0
    [ -t 0 ] && [ -t 1 ] || return 0
    lang_pick
}

# Запомнить выбор. Зовётся ПОСЛЕ проверки root: язык нужно было спросить
# ещё до неё, а писать в /opt без прав бессмысленно.
lang_persist() {
    local f d
    [ "$LANG_ASKED" = "yes" ] || return 0
    f=$(lang_file); d=$(dirname "$f")
    mkdir -p "$d" 2>/dev/null || return 0
    printf '%s\n' "$LANG_CODE" > "$f" 2>/dev/null || return 0
    chmod 644 "$f" 2>/dev/null || true
    LANG_ASKED=""
    return 0
}

# ── продукты: имена и описания ──────────────────────────────────────────
prod_name() {
    case "$1" in
        govless)    printf 'goVLESS pro' ;;
        gotelegram) printf 'goTelegram pro' ;;
        godns)      printf 'goDNS pro' ;;
        gokaskad)   printf 'goKaskad pro' ;;
        gowarp)     printf 'goWARP pro' ;;
        goamnezia)  printf 'goAmnezia pro' ;;
        gocdn)      printf 'gocdn' ;;
        xui)        printf '3X-UI' ;;
        mtproxy)    printf 'MTProxy (mtg)' ;;
        *)          printf '%s' "$1" ;;
    esac
}

prod_desc() {
    case "$1" in
        govless)    bi "VLESS Reality/TLS на 3X-UI, бот, mini-app" "VLESS Reality/TLS on 3X-UI, bot, mini-app" ;;
        gotelegram) bi "MTProxy (telemt), сайт-маска, бот, панель" "MTProxy (telemt), mask site, bot, panel" ;;
        godns)      bi "DoH/DoT-резолвер на сертификате соседа" "DoH/DoT resolver on a neighbour's certificate" ;;
        gokaskad)   bi "каскад портов (iptables), бот, прокси для API Telegram" "port cascade (iptables), bot, Telegram API egress" ;;
        gowarp)     bi "Cloudflare WARP как SOCKS5 для 3X-UI" "Cloudflare WARP as SOCKS5 for 3X-UI" ;;
        goamnezia)  bi "AmneziaWG в Docker, сайт-маска, бот, панель" "AmneziaWG in Docker, mask site, bot, panel" ;;
    esac
}

prod_cmd() {
    case "$1" in
        govless) printf 'govless' ;; gotelegram) printf 'gotelegram' ;;
        godns) printf 'godns' ;; gokaskad) printf 'gokaskad' ;; gowarp) printf 'gowarp' ;;
        goamnezia) printf 'goamnezia' ;;
    esac
}

# Признак «установлен» — тот же, каким пользуется сам продукт.
# GOINST_FAKE_INSTALLED="godns govless" — подмена для тестов: они гоняют
# проверку портов на фикстурах, а не на живой машине.
prod_installed() {
    if [ -n "${GOINST_FAKE_INSTALLED+x}" ]; then
        case " ${GOINST_FAKE_INSTALLED} " in *" $1 "*) return 0 ;; *) return 1 ;; esac
    fi
    case "$1" in
        govless)    [ -f /usr/local/x-ui/x-ui ] && [ -f /opt/govless/config.json ] ;;
        gotelegram) [ -x /usr/local/bin/telemt ] && [ -f /opt/gotelegram/config.json ] ;;
        godns)      _godns_state_present ;;
        gokaskad)   [ -x /usr/local/bin/gokaskad ] || [ -f /etc/kaskad/config ] ;;
        gowarp)     [ -x /usr/local/bin/gowarp ] || [ -f /etc/warp-manager/config ] ;;
        goamnezia)  _goamnezia_installed ;;
        *) return 1 ;;
    esac
}

# ── бесплатный Lite из инструкции на месте goVLESS pro ──────────────────
# goVLESS Lite из бесплатной инструкции (potap1978/go-all-lite-version)
# ставится туда же, куда goVLESS pro: код — клоном в /opt/govless-installer,
# настройки — в /opt/govless/config.json, команда — govless. По признакам
# установки он неотличим от pro, и goInstaller показывал его как «goVLESS pro
# установлен 1.3.6», а пункт «Открыть меню продукта» запускал меню Lite.
# Отличает их только то, откуда взят код: клон Lite смотрит в репозиторий Lite.
GOVLESS_LITE_REPO=""

# git_origin <каталог> — адрес origin клона. Читается прямо из .git/config:
# git на машине может не стоять, а в тестах дерево подложено. Без awk:
# mawk 1.3.3 из Debian 10 не знает классов вроде [[:space:]].
git_origin() {
    local cfg="$1/.git/config" line sect="" url=""
    [ -r "$cfg" ] || return 1
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        case "$line" in
            '['*) sect="$line"; continue ;;
        esac
        [ "$sect" = '[remote "origin"]' ] || continue
        case "$line" in
            url=*|url[[:space:]]*=*)
                url="${line#*=}"; url="${url#"${url%%[![:space:]]*}"}"
                break ;;
        esac
    done < "$cfg"
    [ -n "$url" ] || return 1
    printf '%s' "$url"
}

# repo_slug <адрес> — «owner/repo» строчными: https, ssh, адрес с ключом и
# адрес без .git — один репозиторий, а GitHub не различает регистр в именах.
repo_slug() {
    local u="${1%/}"
    u="${u%.git}"; u="${u#*://}"; u="${u#*@}"; u="${u/github.com:/github.com/}"
    u="${u#github.com/}"
    printf '%s' "${u,,}"
}

# prod_free_lite <id> — код продукта на сервере взят из бесплатного Lite.
# Установленным продукт при этом остаётся: Lite держит те же порты и юниты,
# и соседям он мешает ровно так же, как goVLESS pro в режиме Lite.
prod_free_lite() {
    local url
    [ "$1" = govless ] || return 1
    url=$(git_origin "${GOINST_FS_ROOT:-}/opt/govless-installer") || return 1
    # По имени репозитория, а не по владельцу: форк и зеркало Lite имя берегут.
    case "/$(repo_slug "$url")" in
        */"${GOVLESS_LITE_REPO##*/}") return 0 ;;
    esac
    return 1
}

# Бесплатный Kaskad (potap1978/kaskad) копирует себя в ту же
# /usr/local/bin/gokaskad, что и goKaskad pro: goInstaller принимал его за pro
# и пунктом «Открыть меню продукта» запускал бесплатное меню. Отличает их
# строка версии: KASKAD_VERSION= есть в каждом коммите pro и ни в одной
# бесплатной версии.
_gokaskad_free() {
    local f="${GOINST_FS_ROOT:-}/usr/local/bin/gokaskad"
    [ -f "$f" ] || return 1
    ! ver_extract "$f" KASKAD_VERSION >/dev/null 2>&1
}

# Бесплатный MTProxy (potap1978/gotelegram_mtproxy) при каждом запуске копирует
# себя в /usr/local/bin/gotelegram. У goTelegram pro до 2.10.50 это был симлинк
# на /opt/gotelegram/install.sh, и копия затирала сам установщик pro; с
# обёрткой затирается только команда. В обоих случаях goInstaller видел
# «goTelegram pro установлен» и пунктом меню запускал бесплатный скрипт. Метка
# бесплатного — строка `docker inspect mtproto-proxy`: она есть во всех его
# версиях и ни в одном коммите pro (там имя контейнера в переменной).
_gotelegram_free_code() {
    local r="${GOINST_FS_ROOT:-}" f
    for f in "$r/usr/local/bin/gotelegram" "$r/opt/gotelegram/install.sh"; do
        [ -f "$f" ] || continue
        grep -qF 'docker inspect mtproto-proxy' "$f" 2>/dev/null && return 0
    done
    return 1
}

# prod_free_edition <id> — вместо продукта стоит его бесплатная версия из
# публичной инструкции. Печатает её имя для экрана: «бесплатный <имя>».
# Номер версии рядом осмыслен только у Lite: у Kaskad его нет, а у goTelegram
# это номер pro, чей код бесплатный скрипт подменил (prod_free_shows_version).
prod_free_shows_version() { [ "$1" = govless ]; }

prod_free_edition() {
    case "$1" in
        govless)  prod_free_lite govless || return 1; printf 'Lite' ;;
        gokaskad) _gokaskad_free || return 1; printf 'Kaskad' ;;
        gotelegram) _gotelegram_free_code || return 1; printf 'MTProxy' ;;
        *) return 1 ;;
    esac
}

# Где продукт держит свои настройки. Оттуда же читается режим.
prod_config_file() {
    local r="${GOINST_FS_ROOT:-}"
    case "$1" in
        govless)    printf '%s' "${GOINST_GOVLESS_CONFIG:-$r/opt/govless/config.json}" ;;
        gotelegram) printf '%s' "${GOINST_GOTELEGRAM_CONFIG:-$r/opt/gotelegram/config.json}" ;;
        goamnezia)  printf '%s' "${GOINST_GOAMNEZIA_CONFIG:-$r/opt/goamnezia/config.json}" ;;
        godns)      printf '%s' "${GOINST_GODNS_CONFIG:-$r/opt/godns/config.json}" ;;
        *) return 1 ;;
    esac
}

# Режим установленного продукта. Все три продукта с режимами пишут его
# одним и тем же ключом `mode` в свой config.json — спрашивать человека о
# том, что уже записано на диске, незачем.
prod_installed_mode() {
    local id="$1" f m
    f=$(prod_config_file "$id") || return 1
    [ -r "$f" ] || return 1
    m=$(sed -n -E 's/.*"mode"[[:space:]]*:[[:space:]]*"([a-z_]+)".*/\1/p' "$f" 2>/dev/null | head -1) || m=""
    [ -n "$m" ] || return 1
    # Значение должно быть из списка режимов ЭТОГО продукта: чужое или
    # незнакомое слово режимом не считается, иначе проверка портов пойдёт
    # по пустому набору и скажет «всё свободно».
    case " $(prod_modes "$id") " in
        *" $m "*) printf '%s' "$m"; return 0 ;;
    esac
    return 1
}

# goAmnezia сам считает себя установленным по ключу `.installed` в своём
# config.json, а не по наличию файла: файл появляется раньше, чем мастер
# доходит до конца.
_goamnezia_installed() {
    local f
    f=$(prod_config_file goamnezia)
    [ -f "$f" ] || return 1
    grep -qE '"installed"[[:space:]]*:[[:space:]]*true' "$f" 2>/dev/null
}

# У goDNS config.json появляется уже при выборе языка; «стоит» — только
# когда есть домен, отпечаток применённой конфигурации или версия.
_godns_state_present() {
    local f=/opt/godns/config.json
    [ -f "$f" ] || return 1
    command -v python3 >/dev/null 2>&1 || { grep -qE '"(domain|applied_fingerprint|godns_version)"[[:space:]]*:[[:space:]]*"[^"]+"' "$f"; return; }
    python3 - "$f" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
sys.exit(0 if any(d.get(k) for k in ("domain", "applied_fingerprint", "godns_version")) else 1)
PY
}

# Версия — как получится: у каждого продукта она лежит по-своему.
# ── версии: одна таблица на локальную и на удалённую ────────────────────
# Номер версии у каждого продукта лежит в своём файле и в своей переменной.
# Таблица ОДНА: тот же путь читается и в дереве на сервере, и в репозитории
# на GitHub. Разойдись они — «установлено 2.8» и «в ветке 2.8» считались бы
# по разным правилам, и совет «обновиться нечем» врал бы молча.
#
# prod_ver_spec <id> → "путь_в_репозитории|переменная"
prod_ver_spec() {
    case "$1" in
        govless)    printf 'lib/common.sh|GOVLESS_VERSION' ;;
        gotelegram) printf 'lib/common.sh|GOTELEGRAM_VERSION' ;;
        godns)      printf 'lib/common.sh|GODNS_VERSION' ;;
        gokaskad)   printf 'install.sh|KASKAD_VERSION' ;;
        gowarp)     printf 'warp.sh|WARP_VERSION' ;;
        goamnezia)  printf 'lib/common.sh|GOAMNEZIA_VERSION' ;;
    esac
}

# Каталоги, где на сервере лежит КОД установленного продукта. Их несколько:
# goVLESS кладёт дерево по-разному в зависимости от того, каким из двух
# своих загрузчиков его ставили, а Kaskad и goWARP — сами себе программа
# в /usr/local/bin.
# GOINST_FS_ROOT — префикс для тестов: версию установленного продукта иначе
# нечем проверить, кроме как на машине, где он стоит.
prod_local_dirs() {
    local r="${GOINST_FS_ROOT:-}"
    case "$1" in
        govless)    printf '%s\n' "$r/opt/govless-installer" "$r$HOME/goVLESS" "$r/root/goVLESS" ;;
        gotelegram) printf '%s\n' "$r/opt/gotelegram" ;;
        godns)      printf '%s\n' "$r/opt/godns-installer" ;;
        gokaskad)   printf '%s\n' "$r/usr/local/bin" ;;
        gowarp)     printf '%s\n' "$r/usr/local/bin" ;;
        goamnezia)  printf '%s\n' "$r/opt/goamnezia" ;;
    esac
}

# Имя файла на диске: у Kaskad и goWARP оно другое, чем в репозитории.
prod_local_file() {
    case "$1" in
        gokaskad) printf 'gokaskad' ;;
        gowarp)   printf 'gowarp' ;;
        *)        printf '%s' "${2}" ;;
    esac
}

# Достать номер версии из файла. Присваивание ищем в начале строки:
# упоминание переменной в комментарии или в сообщении версией не является.
ver_extract() {
    local file="$1" var="$2" v
    [ -r "$file" ] || return 1
    v=$(sed -n -E "s/^[[:space:]]*${var}=\"?([0-9][^\"[:space:]]*)\"?.*/\1/p" "$file" 2>/dev/null | head -1) || v=""
    [ -n "$v" ] || return 1
    printf '%s' "$v"
}

# То же, но без знания имени переменной: годится любая
# «ЧТО_ТО_VERSION="1.2.3"» в начале строки. Нужна, когда продукт переехал и
# таблица-подсказка устарела: список продуктов не должен требовать правки
# установщика ради переименованной переменной.
ver_extract_any() {
    local file="$1" v
    [ -r "$file" ] || return 1
    v=$(sed -n -E 's/^[[:space:]]*[A-Z][A-Z0-9_]*VERSION=\"?([0-9][^\"[:space:]]*)\"?.*/\1/p' "$file" 2>/dev/null | head -1) || v=""
    [ -n "$v" ] || return 1
    printf '%s' "$v"
}

# Файлы, в которых версия может лежать. Первый — подсказка из таблицы,
# дальше общие места. Спрашиваем по очереди и останавливаемся на первом
# ответе: лишние запросы к GitHub стоят времени на глазах у человека.
prod_ver_paths() {
    local id="$1" spec hint
    spec=$(prod_ver_spec "$id") || spec=""
    hint="${spec%%|*}"
    [ -n "$hint" ] && printf '%s\n' "$hint"
    local c
    for c in lib/common.sh install.sh bootstrap.sh "${id}.sh" warp.sh; do
        [ "$c" = "$hint" ] || printf '%s\n' "$c"
    done
}

prod_version() {
    local id="$1" spec path var d f v
    spec=$(prod_ver_spec "$id") || return 0
    path="${spec%%|*}"; var="${spec#*|}"
    while IFS= read -r d; do
        [ -n "$d" ] || continue
        f="$d/$(prod_local_file "$id" "$path")"
        if v=$(ver_extract "$f" "$var"); then printf '%s' "$v"; return 0; fi
    done < <(prod_local_dirs "$id")
    # goDNS дополнительно штампует версию в состоянии — годится, когда
    # каталог программы снесли, а состояние осталось.
    if [ "$id" = "godns" ]; then
        v=$(sed -n -E 's/.*"godns_version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "${GOINST_GODNS_CONFIG:-/opt/godns/config.json}" 2>/dev/null | head -1) || v=""
        [ -n "$v" ] && printf '%s' "$v"
    fi
    return 0
}

# Ветки спрашиваем у самого GitHub. Зашитый список устаревает молча: пока
# он лежал в коде, у goDNS появилась `main`, у goKaskad — `dev` (13.10, там
# goKaskad pro 3), а у goWARP ветки `dev` нет — и установщик либо не показывал существующее, либо
# стучался в несуществующее.
#
# Порядок осмысленный, а не алфавитный: сначала стабильная, потом
# отладочная, потом остальные. Человек читает первую строку как
# «обычный выбор».
# Оставляем только два уровня: стабильный и отладочный. Остальное в
# репозиториях — рабочие ветки авторов (`rc1`, `alfa-v2`, `v2.4-menu`,
# `backup/pre-v2.5`, `claude/*`), и предлагать их оператору незачем: он
# выбирает не место разработки, а зрелость кода. Динамика здесь в том,
# СУЩЕСТВУЕТ ли ветка: у goDNS `main` появилась, у goWARP `dev` нет.
# Кому нужна своя — задаёт GOINST_REFS_<id>.
refs_sort() {
    local b stable="" dev="" out
    while read -r b; do
        [ -n "$b" ] || continue
        case "$b" in
            main|master) stable="$b" ;;
            dev|develop) dev="$b" ;;
        esac
    done
    out="${stable:+$stable }${dev:+$dev}"
    printf '%s' "${out% }"
}

# Список веток кладём в тот же кеш, что и версии: ключ «<id>.@refs».
refs_fetch_one() {
    local id="$1" cfg="$2" repo names
    repo=$(prod_repo "$id")
    if [ -n "${GOINST_BRANCHES_FIXTURE:-}" ]; then
        names=$(sed -n -E "s#^${repo}=(.*)\$#\1#p" "$GOINST_BRANCHES_FIXTURE" 2>/dev/null | head -1 | tr ' ' '\n')
    else
        local tmp rc=0
        tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-refs.XXXXXX") || return 0
        gh_get "https://api.github.com/repos/${repo}/branches?per_page=100" "$cfg" "$tmp" || rc=$?
        if [ "$rc" -eq 2 ]; then
            # Лимит: прежний список остаётся, штамп свежий — иначе его
            # спрашивали бы на каждом такте.
            rm -f "$tmp"; ver_cache_touch "$id" "@refs"; return 0
        fi
        names=$(sed -n -E 's/.*"name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$tmp" 2>/dev/null) || names=""
        rm -f "$tmp"
    fi
    names=$(printf '%s\n' "$names" | refs_sort)
    ver_cache_write "$id" "@refs" "$names"
    return 0
}

# prod_refs <id> — что спрашивать и что показывать. Порядок источников:
# заданное человеком, узнанное у GitHub, и лишь потом запасной список для
# случая «сети нет, ключа нет».
prod_refs() {
    local v="GOINST_REFS_$1" cached
    if [ -n "${!v:-}" ]; then printf '%s' "${!v}"; return 0; fi
    if cached=$(ver_cache_get "$1" "@refs") && [ -n "$cached" ]; then
        printf '%s' "$cached"; return 0
    fi
    printf 'main dev'
}

# Сколько веток опрашиваем на версию. Репозиторий с десятком веток иначе
# превращается в десяток запросов на каждый продукт, а смысла в версии
# ветки `backup/pre-v2.5` нет никакого.
REFS_MAX="${GOINST_REFS_MAX:-3}"
prod_refs_probe() {
    local id="$1" n=0 r
    for r in $(prod_refs "$id"); do
        n=$((n+1)); [ "$n" -le "$REFS_MAX" ] || break
        printf '%s\n' "$r"
    done
}

# ── ОС ──────────────────────────────────────────────────────────────────
# /etc/os-release не source-им: файл управляем извне.
osr() {
    local v
    v=$(sed -n -E "s/^[[:space:]]*$1=(.*)\$/\1/p" "${GOINST_OS_RELEASE:-/etc/os-release}" 2>/dev/null) || v=""
    v="${v%%$'\n'*}"; v="${v%\"}"; v="${v#\"}"; printf '%s' "$v"
}
OS_ID=""; OS_VER=""; OS_PRETTY=""; OS_LIKE=""; OS_MAJ=0; OS_MIN=0; OS_ARCH=""
os_detect() {
    OS_ARCH="${GOINST_ARCH:-$(uname -m 2>/dev/null)}"
    OS_ID="$(osr ID)"; OS_VER="$(osr VERSION_ID)"; OS_PRETTY="$(osr PRETTY_NAME)"; OS_LIKE="$(osr ID_LIKE)"
    local maj="${OS_VER%%.*}" min="${OS_VER#*.}"; min="${min%%.*}"
    case "$maj" in ''|*[!0-9]*) maj=0 ;; esac
    case "$min" in ''|*[!0-9]*) min=0 ;; esac
    OS_MAJ=$((10#$maj)); OS_MIN=$((10#$min))
}

# Общие ворота: Ubuntu или Debian с apt. Всё остальное отсекается здесь,
# частные требования продуктов — в prod_os_check.
os_gate_common() {
    case "$OS_ID" in
        ubuntu|debian) ;;
        *)
            case " $OS_LIKE " in
                *" ubuntu "*|*" debian "*)
                    die "$(bi "Это производная (${OS_PRETTY:-ID=$OS_ID}), а не Ubuntu/Debian. Продукты серии go проверены только на оригинальных выпусках." "This is a derivative (${OS_PRETTY:-ID=$OS_ID}), not Ubuntu/Debian. The go products are tested only on original releases.")" ;;
                *) die "$(bi "Неподдерживаемая система: ${OS_PRETTY:-ID=$OS_ID}. Нужна Ubuntu или Debian." "Unsupported system: ${OS_PRETTY:-ID=$OS_ID}. Ubuntu or Debian is required.")" ;;
            esac ;;
    esac
    [ -n "${GOINST_TEST:-}" ] || command -v apt-get >/dev/null 2>&1 || die "$(bi "Нет apt-get — установщики продуктов на нём построены." "apt-get is missing — the product installers depend on it.")"
}

# prod_os_check <id> → печатает строки "must|…" / "soft|…"; ничего — всё хорошо.
# Требования сняты с установщиков самих продуктов, а не придуманы.
prod_os_check() {
    case "$1" in
        godns)
            if [ "$OS_ID" != "ubuntu" ]; then
                echo "must|$(bi "goDNS pro ставится только на Ubuntu 22.04 / 24.04 / 26.04 (найдена ${OS_PRETTY})." "goDNS pro installs only on Ubuntu 22.04 / 24.04 / 26.04 (found ${OS_PRETTY}).")"
            elif [ "$OS_MAJ" -lt 22 ]; then
                echo "must|$(bi "goDNS pro требует Ubuntu 22.04 или новее (найдена ${OS_PRETTY})." "goDNS pro requires Ubuntu 22.04 or newer (found ${OS_PRETTY}).")"
            fi ;;
        gotelegram)
            if [ "$OS_ID" = "ubuntu" ] && [ "$OS_MAJ" -lt 24 ]; then
                echo "must|$(bi "goTelegram pro требует Ubuntu 24.04 или новее (найдена ${OS_PRETTY}); установщик продукта откажется сам." "goTelegram pro requires Ubuntu 24.04 or newer (found ${OS_PRETTY}); the product installer refuses on its own.")"
            elif [ "$OS_ID" = "debian" ]; then
                echo "soft|$(bi "Debian у goTelegram pro не в списке проверенных — установщик продукта предупредит." "Debian is not on goTelegram pro's tested list — the product installer will warn.")"
            fi ;;
        govless)
            if [ "$OS_ID" = "ubuntu" ] && [ "$OS_MAJ" -lt 22 ]; then
                echo "soft|$(bi "goVLESS pro проверен на Ubuntu 22.04+ и Debian 11/12 (найдена ${OS_PRETTY})." "goVLESS pro is tested on Ubuntu 22.04+ and Debian 11/12 (found ${OS_PRETTY}).")"
            fi ;;
        gowarp)
            # Пакет cloudflare-warp лежит в репозитории Cloudflare под кодовыми
            # именами focal/jammy/noble; для более новых Ubuntu его может не быть.
            if [ "$OS_ID" = "ubuntu" ] && [ "$OS_MAJ" -gt 24 ]; then
                echo "soft|$(bi "Репозиторий Cloudflare знает Ubuntu 20.04/22.04/24.04; на ${OS_PRETTY} пакет cloudflare-warp может не поставиться." "Cloudflare's repository covers Ubuntu 20.04/22.04/24.04; on ${OS_PRETTY} the cloudflare-warp package may not install.")"
            fi ;;
        goamnezia)
            # Образы Amnezia собраны только под amd64, и продукт отказывается
            # ставиться на всём остальном. Сказать это надо до установки, а не
            # после скачивания.
            case "$OS_ARCH" in
                x86_64|amd64) ;;
                *) echo "must|$(bi "goAmnezia pro ставится только на amd64 (найдена ${OS_ARCH:-неизвестно}): образы Amnezia собраны под неё одну." "goAmnezia pro installs only on amd64 (found ${OS_ARCH:-unknown}): Amnezia's images are built for it alone.")" ;;
            esac
            ;;
        gokaskad) ;;
    esac
    return 0
}

# ── порты: кто что держит ───────────────────────────────────────────────
# Снимок слушателей: строки "proto|addr|port|proc|pid". Один вызов ss на
# весь экран, дальше — чистая работа со строками.
LISTENERS=""
_SCAN_STAMP=""
# Кеш нужен ровно на время сборки экрана меню: там preflight зовётся по разу
# на каждый продукт, и без кеша это семь вызовов ss на одну перерисовку.
# Включает и выключает его только menu_items_main — во всех остальных местах
# снимок обязан быть свежим.
scan_cache_begin() { _SCAN_STAMP=""; _DOCKER_STAMP=""; GOINST_SCAN_CACHE=1; }
scan_cache_end()   { unset GOINST_SCAN_CACHE; _SCAN_STAMP=""; _DOCKER_STAMP=""; }
# with_scan_cache <команда…> — один скан ss и docker на всё время команды.
# Внутри уже открытого кеша — просто команда: конец вложенного кеша закрыл
# бы внешний, и остаток пересборки снова звал бы docker ps на каждый порт.
with_scan_cache() {
    if [ -n "${GOINST_SCAN_CACHE:-}" ]; then "$@"; return $?; fi
    local rc=0
    scan_cache_begin; "$@" || rc=$?; scan_cache_end
    return "$rc"
}
scan_listeners() {
    local raw
    if [ -n "${GOINST_SCAN_CACHE:-}" ] && [ -n "$_SCAN_STAMP" ]; then return 0; fi
    _SCAN_STAMP="done"
    if [ -n "${GOINST_SS_FIXTURE:-}" ]; then
        raw=$(cat "$GOINST_SS_FIXTURE" 2>/dev/null) || raw=""
    else
        raw=$(ss -H -lntup 2>/dev/null) || raw=""
    fi
    LISTENERS=$(printf '%s\n' "$raw" | awk '
        NF >= 5 {
            proto = $1; local = $5; users = "";
            for (i = 6; i <= NF; i++) if ($i ~ /^users:/) users = $i;
            n = split(local, a, ":"); port = a[n];
            addr = substr(local, 1, length(local) - length(port) - 1);
            gsub(/^\[|\]$/, "", addr); sub(/%.*$/, "", addr);
            proc = ""; pid = "";
            if (match(users, /\("[^"]+",pid=[0-9]+/)) {
                s = substr(users, RSTART + 2, RLENGTH - 2);
                split(s, b, "\",pid="); proc = b[1]; pid = b[2];
            }
            if (port ~ /^[0-9]+$/) print proto "|" addr "|" port "|" proc "|" pid;
        }')
}

# Юнит systemd по PID: /proc/<pid>/cgroup → «…/dnsdist.service».
pid_unit() {
    local pid="$1" line
    [ -n "$pid" ] || return 0
    if [ -n "${GOINST_UNIT_FIXTURE:-}" ]; then
        sed -n -E "s/^${pid}=//p" "$GOINST_UNIT_FIXTURE" 2>/dev/null | head -1; return 0
    fi
    line=$(sed -n -E 's#^0::.*/([^/]+\.service)$#\1#p' "/proc/$pid/cgroup" 2>/dev/null | head -1) || line=""
    printf '%s' "$line"
}

# Кому принадлежит слушатель: id продукта, «nginx», «system» или имя процесса.
holder_product() {
    local proc="$1" pid="$2" unit
    unit=$(pid_unit "$pid")
    case "$unit" in
        goamnezia*) printf 'goamnezia'; return ;;
        x-ui.service) _xui_owner; return ;;
        govless*|govlessctl*|cloudflared*|webapp-frontend*|tunnel-health*) printf 'govless'; return ;;
        telemt.service|gotelegram*) printf 'gotelegram'; return ;;
        dnsdist.service|godns*) printf 'godns'; return ;;
        kaskad*) printf 'gokaskad'; return ;;
        warp-svc.service|warp-bot.service|gowarp*) printf 'gowarp'; return ;;
        gocdn*) printf 'gocdn'; return ;;
        nginx.service) _nginx_owner; return ;;
        systemd-resolved.service|sshd.service|ssh.service|chrony*|cups*|rpcbind*|exim*|postfix*) printf 'system'; return ;;
    esac
    case "$proc" in
        xray|xray-linux*|x-ui) _xui_owner ;;
        telemt) printf 'gotelegram' ;;
        dnsdist) printf 'godns' ;;
        warp-svc) printf 'gowarp' ;;
        nginx) _nginx_owner ;;
        systemd-resolve|systemd-resolved|sshd|chronyd|ntpd) printf 'system' ;;
        *) printf '%s' "${proc:-?}" ;;
    esac
}

# x-ui — штатный юнит ЛЮБОЙ панели 3X-UI, а не только goVLESS pro: панель
# ставили и по старым инструкциям, и руками. Порты такой панели проверка
# звала «остатками прежней установки goVLESS pro» и запрещала ставить, хотя
# установщик goVLESS pro на ней не спотыкается, а берёт её себе. Панель —
# goVLESS pro, только когда он сам стоит (бесплатный Lite тоже считается).
_xui_owner() { if prod_installed govless; then printf 'govless'; else printf 'xui'; fi; }

# Панель 3X-UI есть, а goVLESS pro нет.
govless_foreign_panel() {
    [ -f "${GOINST_FS_ROOT:-}/usr/local/x-ui/x-ui" ] && ! prod_installed govless
}

# nginx общий: смотрим, чей сайт включён.
_nginx_owner() {
    local root="${GOINST_NGINX_ROOT:-/etc/nginx}"
    if [ -e "$root/sites-enabled/gotelegram" ] || [ -e "$root/stream-conf.d/gotelegram-shared443.conf" ]; then printf 'gotelegram'
    elif [ -e "$root/sites-enabled/govless" ]; then printf 'govless'
    elif [ -e "$root/sites-enabled/goamnezia" ]; then printf 'goamnezia'
    else printf 'nginx'; fi
}

# ── порты, занятые контейнерами ─────────────────────────────────────────
# Опубликованный порт контейнера виден в `ss` как docker-proxy, а при
# `userland-proxy: false` не виден ВОВСЕ: трафик уводит DNAT, слушателя на
# хосте нет. Без этого опроса проверка сказала бы «свободен» про порт,
# который занят, — и это ровно тот случай, ради которого затевалась
# проверка. goAmnezia держит AmneziaWG именно в контейнерах.
DOCKER_PORTS=""
_DOCKER_STAMP=""; DOCKER_ERR=""
# docker ps, который не молчит о провале. Клиент docker новее демона —
# частый случай после обновления пакета без перезапуска демона: «client
# version 1.52 is too new. Maximum supported API version is 1.47». Тогда
# повторяем с той версией API, которую демон назвал сам (так же делает
# goAmnezia pro). Не вышло — причина в DOCKER_ERR: её показывает проверка
# портов, иначе «docker-proxy» на порту остаётся загадкой.
_docker_ps() {
    local errf err v rc=0
    DOCKER_ERR=""
    errf=$(mktemp "${TMPDIR:-/tmp}/goinst-dk.XXXXXX") || errf=/dev/null
    # Зависший демон не должен вешать меню: пять секунд, дальше — «не ответил».
    local -a to=() dt="${GOINST_DOCKER_TIMEOUT:-5}"
    case "$dt" in ''|*[!0-9]*) dt=5 ;; esac
    command -v timeout >/dev/null 2>&1 && to=(timeout "$dt")
    DOCKER_PS=$(${to[@]+"${to[@]}"} docker ps --format '{{.Names}} {{.Ports}}' 2>"$errf") || rc=$?
    if [ "$rc" -ne 0 ]; then
        err=$(cat "$errf" 2>/dev/null) || err=""
        v="${err#*aximum supported API version is }"; v="${v%%[!0-9.]*}"
        if [ "$v" != "$err" ] && [[ "$v" =~ ^[0-9]+\.[0-9]+$ ]]; then
            rc=0; DOCKER_PS=$(DOCKER_API_VERSION="$v" ${to[@]+"${to[@]}"} docker ps --format '{{.Names}} {{.Ports}}' 2>"$errf") || rc=$?
            [ "$rc" -eq 0 ] || err=$(cat "$errf" 2>/dev/null) || err=""
        fi
        if [ "$rc" -ne 0 ]; then
            DOCKER_PS=""; err="${err%%$'\n'*}"; err="${err//[|]//}"; err="${err//[^[:print:]]/}"
            [ "$rc" -eq 124 ] && err="docker ps не ответил за ${dt} с"
            DOCKER_ERR="${err:0:120}"; [ -n "$DOCKER_ERR" ] || DOCKER_ERR="docker ps: код $rc"
        fi
    fi
    [ "$errf" = /dev/null ] || rm -f "$errf"
    return 0
}

scan_docker() {
    local raw name rest tok p port proto
    if [ -n "${GOINST_SCAN_CACHE:-}" ] && [ -n "$_DOCKER_STAMP" ]; then return 0; fi
    _DOCKER_STAMP="done"; DOCKER_PORTS=""; DOCKER_ERR=""
    if [ -n "${GOINST_DOCKER_FIXTURE:-}" ]; then
        raw=$(cat "$GOINST_DOCKER_FIXTURE" 2>/dev/null) || raw=""
    elif command -v docker >/dev/null 2>&1; then
        _docker_ps; raw="$DOCKER_PS"
    else
        return 0
    fi
    while read -r name rest; do
        [ -n "$name" ] || continue
        for tok in $(printf '%s' "$rest" | tr ',' ' '); do
            case "$tok" in
                *'->'*)
                    # Порт — со стороны сервера, до «->»: его и держит
                    # docker-proxy. После «->» — порт внутри контейнера, при
                    # публикации 8443->443 это другой номер.
                    p="${tok%%->*}"; port="${p##*:}"; proto="${tok##*/}"
                    case "$proto" in tcp|udp) ;; *) continue ;; esac
                    # docker сам сворачивает соседние порты: 1000-1002->1000-1002/udp.
                    if [[ "$port" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                        local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"
                        [ $(( 10#$b - 10#$a )) -ge 0 ] && [ $(( 10#$b - 10#$a )) -le 1024 ] || continue
                        for ((a = 10#$a; a <= 10#$b; a++)); do DOCKER_PORTS+="${proto}|${a}|${name}"$'\n'; done
                        continue
                    fi
                    case "$port" in ''|*[!0-9]*) continue ;; esac
                    DOCKER_PORTS+="${proto}|${port}|${name}"$'\n' ;;
            esac
        done
    done <<<"$raw"
    return 0
}

# Контейнер → продукт. Имена AmneziaWG — общий неймспейс приложения Amnezia,
# goAmnezia и goWARP; для нас важно, что порт занят, и кем именно заведён.
# Продукт, который держит свои порты в контейнерах Docker.
prod_uses_docker() { case "$1" in goamnezia) return 0 ;; *) return 1 ;; esac; }

container_product() {
    case "$1" in
        amnezia-awg[0-9]*|amnezia-awg) printf 'goamnezia' ;;
        # Прежний MTProxy на mtg: бесплатная версия из инструкции
        # (potap1978/gotelegram_mtproxy) и goTelegram pro первой версии держат
        # прокси в контейнере с этим именем.
        mtproto-proxy) printf 'mtproxy' ;;
        *) printf '%s' "$1" ;;
    esac
}

# port_holders <proto> <port> → строки "proc|pid|owner|addr" (может быть несколько)
port_holders() {
    local proto="$1" port="$2" l p a po pr pi found=0 name
    if [ -n "$LISTENERS" ]; then
        while IFS= read -r l; do
            [ -n "$l" ] || continue
            IFS='|' read -r p a po pr pi <<<"$l"
            [ "$p" = "$proto" ] && [ "$po" = "$port" ] || continue
            # docker-proxy сам по себе ничего не говорит: за ним стоит
            # контейнер, и назвать надо его.
            if [ "$pr" = "docker-proxy" ]; then
                name=$(_docker_holder "$proto" "$port") || name=""
                if [ -n "$name" ]; then
                    printf '%s|%s|%s|%s\n' "docker:${name}" "$pi" "$(container_product "$name")" "$a"
                    found=1; continue
                fi
            fi
            printf '%s|%s|%s|%s\n' "$pr" "$pi" "$(holder_product "$pr" "$pi")" "$a"
            found=1
        done <<<"$LISTENERS"
    fi
    [ "$found" -eq 1 ] && return 0
    # Слушателя нет — порт всё ещё может держать контейнер через DNAT.
    name=$(_docker_holder "$proto" "$port") || return 0
    [ -n "$name" ] || return 0
    printf 'docker:%s||%s|0.0.0.0\n' "$name" "$(container_product "$name")"
}

_docker_holder() {
    local proto="$1" port="$2" l p po name
    scan_docker
    [ -n "$DOCKER_PORTS" ] || return 1
    while IFS='|' read -r p po name; do
        [ "$p" = "$proto" ] && [ "$po" = "$port" ] || continue
        printf '%s' "$name"; return 0
    done <<<"$DOCKER_PORTS"
    return 1
}

# ── порты: что нужно каждому продукту ───────────────────────────────────
# Строка: proto|port|level|назначение. level: must — без этого порта
# продукт не встанет; soft — у продукта есть запасной вариант, но
# предупредить надо; info — просто сказать, что порт станет занят.
# Каждая строка снята с кода продукта (см. README, таблица портов).
prod_ports() {
    local id="$1" mode="${2:-pro}"
    case "$id" in
        govless)
            echo "tcp|443|must|$(bi "VLESS inbound xray — жёстко 443 в обоих режимах" "VLESS inbound (xray) — hardcoded 443 in both modes")"
            if [ "$mode" = "pro" ]; then
                echo "tcp|80|must|$(bi "nginx: сайт-маска и ACME-проверка certbot" "nginx: mask site and certbot ACME challenge")"
            else
                echo "tcp|80|soft|$(bi "nginx-заглушка и acme.sh для сертификата панели" "nginx stub and acme.sh for the panel certificate")"
            fi
            echo "tcp|8443|soft|$(bi "loopback: mini-app бота (режим Lite)" "loopback: bot mini-app (Lite mode)")"
            echo "tcp|10808|soft|$(bi "loopback: SOCKS5 прокси для API Telegram" "loopback: SOCKS5 Telegram API egress")"
            echo "tcp|10851|soft|$(bi "loopback: API xray для балансировщика прокси" "loopback: xray API for the egress balancer")"
            echo "tcp|18181|soft|$(bi "loopback: метрики cloudflared (режим Lite)" "loopback: cloudflared metrics (Lite mode)")"
            ;;
        gotelegram)
            if [ "$mode" = "pro" ]; then
                echo "tcp|443|must|$(bi "telemt MTProxy — жёстко 443 в режиме pro" "telemt MTProxy — hardcoded 443 in pro mode")"
                echo "tcp|80|must|$(bi "nginx: ACME-проверка certbot и редирект на https" "nginx: certbot ACME challenge and https redirect")"
                echo "tcp|8443|must|$(bi "loopback: nginx с сайтом-маской за telemt" "loopback: nginx mask site behind telemt")"
            else
                echo "tcp|443|soft|$(bi "telemt MTProxy — порт по умолчанию, можно выбрать другой" "telemt MTProxy — default port, another can be chosen")"
            fi
            echo "tcp|9090|must|$(bi "loopback: метрики telemt (Prometheus)" "loopback: telemt metrics (Prometheus)")"
            echo "tcp|9091|must|$(bi "loopback: локальный API telemt" "loopback: telemt local API")"
            echo "tcp|1984|soft|$(bi "loopback: веб-панель (если ставить бота)" "loopback: admin web panel (if the bot is installed)")"
            echo "tcp|18080|soft|$(bi "loopback: WEB-транспорт telemt (если включён)" "loopback: telemt WEB transport (if enabled)")"
            echo "tcp|8444|soft|$(bi "loopback: decoy-хост nginx для WEB-транспорта" "loopback: nginx decoy vhost for WEB transport")"
            ;;
        godns)
            echo "tcp|443|info|$(bi "DoH: возьмёт 443 только если свободен и соседей нет" "DoH: takes 443 only if free and no neighbours")"
            echo "tcp|4443|soft|$(bi "DoH: первый кандидат при занятом 443 (дальше 8444 9444 5443 7444)" "DoH: first candidate when 443 is busy (then 8444 9444 5443 7444)")"
            echo "tcp|853|soft|$(bi "DoT (запасные 8853 9853 1853)" "DoT (fallbacks 8853 9853 1853)")"
            echo "tcp|5199|must|$(bi "loopback: консоль dnsdist" "loopback: dnsdist console")"
            echo "tcp|53|info|$(bi "loopback: обычный DNS 127.0.0.1:53, при занятости уедет на 127.0.0.2" "loopback: plain DNS 127.0.0.1:53, moves to 127.0.0.2 if busy")"
            echo "tcp|9445|soft|$(bi "веб-панель (кандидаты 9446 9447 8446 8447 9448 9449)" "web panel (candidates 9446 9447 8446 8447 9448 9449)")"
            echo "tcp|8642|soft|$(bi "loopback: внутренний порт веб-панели (8643…8646)" "loopback: web panel internal port (8643…8646)")"
            echo "tcp|80|info|$(bi "нужен ТОЛЬКО если выпускать свой сертификат, а не брать у соседа" "needed ONLY when issuing an own certificate instead of reusing a neighbour's")"
            ;;
        gokaskad)
            echo "tcp|10808|soft|$(bi "loopback: SOCKS5 прокси для API Telegram (если включить)" "loopback: SOCKS5 Telegram API egress (if enabled)")"
            echo "tcp|10851|soft|$(bi "loopback: API xray прокси (если включить)" "loopback: xray API of the egress (if enabled)")"
            ;;
        gowarp)
            echo "tcp|40000|must|$(bi "loopback: SOCKS5 WARP (сам продукт занятость не проверяет)" "loopback: WARP SOCKS5 (the product itself never checks it)")"
            ;;
        goamnezia)
            # Как на первом экране мастера goAmnezia pro 1.0: Ленивый pro, pro,
            # Semi-pro, Lite. Режимам с сайтом TCP 80 и 443 обязательны (так
            # решает и его preflight_modes); без домена сайт встаёт на IP с
            # самоподписанным сертификатом — порты те же. Pro — это AmneziaWG
            # на UDP 443: мастер без этого порта ставит Semi-pro («Режим Pro
            # без UDP/443 невозможен»), поэтому для pro порт обязателен, а
            # Ленивому pro — замечание: он сам встанет Semi-pro. В Lite TCP
            # не нужен вовсе: сайта нет, порты AmneziaWG случайные.
            case "$mode" in
                lazy|pro|lite_site)
                    echo "tcp|443|must|$(bi "nginx: сайт на домене с сертификатом Let's Encrypt" "nginx: site on the domain with a Let's Encrypt certificate")"
                    echo "tcp|80|must|$(bi "nginx: проверка Let's Encrypt и редирект на https" "nginx: Let's Encrypt challenge and https redirect")"
                    ;;
            esac
            case "$mode" in
                pro)  echo "udp|443|must|$(bi "AmneziaWG на UDP 443: без него pro невозможен, встанет только Semi-pro" "AmneziaWG on UDP 443: pro is impossible without it, only Semi-pro installs")" ;;
                lazy) echo "udp|443|soft|$(bi "AmneziaWG 2.0 на UDP 443; занят — Ленивый pro встанет Semi-pro" "AmneziaWG 2.0 on UDP 443; if taken, Lazy pro ends up as Semi-pro")" ;;
            esac
            echo "tcp|1985|soft|$(bi "loopback: веб-панель (1984 занят goTelegram pro)" "loopback: admin panel (1984 belongs to goTelegram pro)")"
            echo "tcp|1986|info|$(bi "loopback: MCP по HTTP, по умолчанию выключен" "loopback: MCP over HTTP, off by default")"
            case "$mode" in
                lazy|pro|lite_site)
                    echo "tcp|1987|info|$(bi "loopback: страницы подключения, с первой страницей" "loopback: connection pages, from the first page on")" ;;
            esac
            ;;
    esac
}

# prod_ports_opt <id> — порты дополнительных функций, которые встают не при
# установке, а когда их включают в меню самого продукта. Проверка портов и
# карта портов их показывают, строка «НУЖНЫ» в карточке — нет: продукту они
# для установки не нужны. Уровень всегда info. goKaskad pro 3 (ветка dev)
# занятый порт видит сам: протоколу шлюза берёт другой свободный, сайт на
# занятый 443 не встаёт. Сняты с ядра 3.3.3: panel/conf.py,
# gw/inbounds/{wg,ocserv,openvpn,socks5,vless,accel,l2tp,ikev2}.py,
# site/front.py, ipcert/http01.py. Случайные порты (AmneziaWG, открытая
# панель) и внутренние на 127.0.0.1 (xray шлюза) — не строки таблицы.
prod_ports_opt() {
    case "$1" in
        gokaskad)
            echo "tcp|1980|info|$(bi "3.x, loopback: веб-панель (если включить)" "3.x, loopback: web panel (if enabled)")"
            echo "tcp|443|info|$(bi "3.x: VLESS шлюза и сайт на домене (если включить; занят — VLESS на 8443, сайт не встаёт)" "3.x: gateway VLESS and the domain site (if enabled; busy — VLESS on 8443, no site)")"
            echo "tcp|8443|info|$(bi "3.x, шлюз: VLESS, когда 443 занят (если включить)" "3.x, gateway: VLESS when 443 is busy (if enabled)")"
            echo "tcp|80|info|$(bi "3.x: сертификат на IP при быстрой настройке, сайт на домене (если включить)" "3.x: IP certificate in the quick setup, the domain site (if enabled)")"
            echo "udp|51820|info|$(bi "3.x, шлюз: WireGuard по умолчанию (если включить; занят — другой)" "3.x, gateway: WireGuard default (if enabled; busy — another)")"
            echo "tcp|4443|info|$(bi "3.x, шлюз: OpenConnect по умолчанию (если включить; занят — другой)" "3.x, gateway: OpenConnect default (if enabled; busy — another)")"
            echo "udp|4443|info|$(bi "3.x, шлюз: OpenConnect по умолчанию (если включить; занят — другой)" "3.x, gateway: OpenConnect default (if enabled; busy — another)")"
            echo "udp|1194|info|$(bi "3.x, шлюз: OpenVPN по умолчанию (если включить; занят — другой)" "3.x, gateway: OpenVPN default (if enabled; busy — another)")"
            echo "tcp|1080|info|$(bi "3.x, шлюз: SOCKS5 по умолчанию (если включить; занят — другой)" "3.x, gateway: SOCKS5 default (if enabled; busy — another)")"
            echo "udp|1080|info|$(bi "3.x, шлюз: SOCKS5 по умолчанию (если включить; занят — другой)" "3.x, gateway: SOCKS5 default (if enabled; busy — another)")"
            echo "tcp|4433|info|$(bi "3.x, шлюз: SSTP по умолчанию (если включить; занят — другой)" "3.x, gateway: SSTP default (if enabled; busy — another)")"
            echo "udp|500|info|$(bi "3.x, шлюз: IKEv2 и L2TP/IPsec, постоянный (если включить)" "3.x, gateway: IKEv2 and L2TP/IPsec, fixed (if enabled)")"
            echo "udp|4500|info|$(bi "3.x, шлюз: IKEv2 и L2TP/IPsec, постоянный (если включить)" "3.x, gateway: IKEv2 and L2TP/IPsec, fixed (if enabled)")"
            echo "udp|1701|info|$(bi "3.x, шлюз: L2TP, постоянный (если включить)" "3.x, gateway: L2TP, fixed (if enabled)")"
            echo "tcp|1723|info|$(bi "3.x, шлюз: PPTP, постоянный (если включить)" "3.x, gateway: PPTP, fixed (if enabled)")"
            ;;
    esac
}

# Продукту нужен режим? Порты goVLESS и goTelegram зависят от Lite/pro.
# Режимы продукта, от требовательного к скромному: проверка идёт по этому
# порядку, и первый прошедший даёт ответ «готов». Двумя режимами список не
# ограничен — у goAmnezia pro их три, и «pro или Lite» про него было бы враньём.
prod_modes() {
    case "$1" in
        govless|gotelegram) printf 'pro lite' ;;
        goamnezia)          printf 'pro lite_site lite' ;;
        *)                  printf '' ;;
    esac
}

# prod_mode_choices <id> — что предлагается в вопросе о режиме, в порядке
# первого экрана мастера продукта. У goAmnezia pro первым идёт Ленивый pro:
# это не режим, а сценарий установки (итог — pro, при занятом UDP 443 —
# Semi-pro), поэтому в prod_modes его нет: config.json его не хранит, а
# готовность считается по режимам, которые в итоге встают.
prod_mode_choices() {
    case "$1" in
        goamnezia) printf 'lazy pro lite_site lite' ;;
        *)         prod_modes "$1" ;;
    esac
}

# prod_mode_default <id> — ответ на Enter в вопросе о режиме. У goAmnezia pro
# — Lite, как в его мастере («Enter — 4, Lite»): ни домена, ни TCP-портов.
# У остальных — первый, самый требовательный.
prod_mode_default() {
    local m=""
    case "$1" in
        goamnezia) printf 'lite'; return 0 ;;
    esac
    read -r m _ <<<"$(prod_modes "$1")"
    printf '%s' "$m"
}

# mode_ok <id> <режим> — слово из вопроса о режиме этого продукта. Опечатка в
# `--check goamnezia Pro` иначе проверила бы пустой набор портов и ответила
# «всё свободно». У продукта без режимов слово ни на что не влияет.
mode_ok() {
    prod_has_modes "$1" || return 0
    case " $(prod_mode_choices "$1") " in *" $2 "*) return 0 ;; esac
    return 1
}

# mode_arg <id> <режим> — режим из командной строки: пустой годится (спросим
# или возьмём из настроек продукта), чужое слово — отказ с перечнем.
mode_arg() {
    [ -z "$2" ] && return 0
    mode_ok "$1" "$2" && return 0
    err "$(bi "У $(prod_name "$1") нет режима «$2». Есть: $(prod_mode_choices "$1")." "$(prod_name "$1") has no mode '$2'. Available: $(prod_mode_choices "$1").")"
    return 1
}
prod_has_modes() { [ -n "$(prod_modes "$1")" ]; }

# ref_arg <id> <ветка> — ветка из командной строки, как mode_arg для режима.
# 3.x goKaskad pro пишет её в свои настройки: опечатка сломала бы потом
# обновление в самом продукте, а не только эту установку.
ref_arg_known() {
    local r
    for r in $(prod_refs "$1"); do [ "$r" = "$2" ] && return 0; done
    return 1
}
ref_arg() {
    [ -z "$2" ] && return 0
    ref_arg_known "$1" "$2" && return 0
    err "$(bi "У $(prod_name "$1") нет ветки «$2». Есть: $(prod_refs "$1")." "$(prod_name "$1") has no branch '$2'. Available: $(prod_refs "$1").")"
    return 1
}

mode_name() {
    case "$1" in
        pro)       printf 'pro' ;;
        lite)      printf 'Lite' ;;
        # Названия — как у goAmnezia pro с 0.6.0 (Semi-Pro, Ленивый Pro), но
        # «pro» строчными, как везде в goInstaller.
        lite_site) printf 'Semi-pro' ;;
        lazy)      bi "Ленивый pro" "Lazy pro" ;;
        *)         printf '%s' "$1" ;;
    esac
}

mode_desc() {
    local id="$1" m="$2"
    case "$id:$m" in
        goamnezia:lazy) bi "только домен, остальное сделает продукт и режим больше не спросит; UDP 443 занят — встанет Semi-pro" "just a domain, the product does the rest and does not ask the mode again; UDP 443 taken — it becomes Semi-pro" ;;
        *:pro)
            case "$id" in
                goamnezia) bi "сайт на TCP 443, AmneziaWG на UDP 443: нужны домен, TCP 80 и 443, UDP 443" "site on TCP 443, AmneziaWG on UDP 443: needs a domain, TCP 80 and 443, UDP 443" ;;
                *)         bi "домен, сертификат, сайт-маска: 443 и 80" "domain, certificate, mask site: 443 and 80" ;;
            esac ;;
        goamnezia:lite_site) bi "VPN и сайт: сайт на TCP 443 и 80, AmneziaWG на своих портах" "VPN and site: site on TCP 443 and 80, AmneziaWG on its own ports" ;;
        goamnezia:lite)      bi "только VPN: TCP-порты не нужны, AmneziaWG на своих портах" "VPN only: no TCP ports, AmneziaWG on its own ports" ;;
        *:lite)              bi "без домена: только 443" "no domain: 443 only" ;;
    esac
}

# ── проверка перед установкой ───────────────────────────────────────────
# Итог в PF_MUST / PF_SOFT (счётчики) и PF_LINES (для теста и --check).
PF_MUST=0; PF_SOFT=0; PF_LINES=""
_pf_add() { PF_LINES+="$1"$'\n'; }

preflight() {
    local id="$1" mode="${2:-pro}" line proto port level what holders h proc pid owner addr
    local self_reinstall="no" osl
    PF_MUST=0; PF_SOFT=0; PF_LINES=""
    # Для экрана «Аркады»: строки таблицы «порт|ok|bad|note|info|что это|кто
    # держит» и сообщения правил. Печать при этом прежняя.
    PF_ROWS=(); PF_MSGS=()
    scan_listeners
    # Контейнеры — здесь, а не только внутри $(port_holders …): там это
    # подоболочка, и причина отказа docker (DOCKER_ERR) до строки таблицы
    # не доходила.
    scan_docker

    print_header "$(bi "Проверка перед установкой" "Pre-install check"): $(prod_name "$id")$( prod_has_modes "$id" && printf ' (%s)' "$(mode_name "$mode")")"

    # ОС
    PF_CAPTURE=1
    while IFS= read -r osl; do
        [ -n "$osl" ] || continue
        level="${osl%%|*}"; what="${osl#*|}"
        if [ "$level" = "must" ]; then err "$what"; PF_MUST=$((PF_MUST+1)); _pf_add "os|must|$what"
        else warn "$what"; PF_SOFT=$((PF_SOFT+1)); _pf_add "os|soft|$what"; fi
    done < <(prod_os_check "$id")
    PF_CAPTURE=""

    if prod_installed "$id"; then
        local free
        if free=$(prod_free_edition "$id"); then
            say "$(bi "Стоит бесплатный ${free} из инструкции — $(prod_name "$id") встанет поверх него; порты ${free} считаются своими." "The free ${free} from the guide is installed — $(prod_name "$id") installs over it; the ${free} ports count as its own.")"
        else
            say "$(bi "$(prod_name "$id") уже установлен — это переустановка/обновление; его собственные порты не считаются занятыми." "$(prod_name "$id") is already installed — this is a reinstall/upgrade; its own ports do not count as busy.")"
        fi
        self_reinstall="yes"
    fi

    # Порты
    echo ""
    # Колонка статуса — один символ: ✓ свободен/свой, ✗ занят (препятствие),
    # ! занят (замечание), · занят (к сведению). Слова со статусом — в
    # хвосте строки: printf '%-9s' считает БАЙТЫ, и кириллица разъезжается.
    # Вся таблица — в stderr, как и say/warn/err: иначе через пайп строки
    # двух потоков перемешиваются.
    echo -e "  ${DIM}$(bi "✓ свободен или свой   ✗ занят, препятствие   ! занят, замечание   · занят, к сведению" "✓ free or own   ✗ busy, blocker   ! busy, remark   · busy, note")${NC}" >&2
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        IFS='|' read -r proto port level what <<<"$line"
        holders=$(port_holders "$proto" "$port")
        if [ -z "$holders" ]; then
            printf "  %-11s ${GREEN}✓${NC}  %s\n" "$port/$proto" "$what" >&2
            _pf_add "$port/$proto|free|$level"
            PF_ROWS+=("$port/$proto|ok|$what|")
            continue
        fi
        # Берём первого держателя — для решения его достаточно.
        h="${holders%%$'\n'*}"
        IFS='|' read -r proc pid owner addr <<<"$h"
        # Чужую панель 3X-UI установщик goVLESS pro не сносит, а берёт себе:
        # xray на её портах остаётся тем же. Что при этом меняется — в
        # правилах соседства ниже.
        # Прежний MTProxy установщик goTelegram pro останавливает сам (миграция
        # v1): порт достаётся telemt.
        if [ "$id" = gotelegram ] && [ "$owner" = mtproxy ]; then
            printf "  %-11s ${GREEN}✓${NC}  %s ${DIM}— $(bi "прежний MTProxy, его остановит" "earlier MTProxy, stopped by") $(prod_name "$id"): %s${NC}\n" "$port/$proto" "$what" "$proc" >&2
            _pf_add "$port/$proto|own|$level|$owner"
            PF_ROWS+=("$port/$proto|ok|$what|$(bi "прежний MTProxy" "earlier MTProxy")")
            continue
        fi
        if [ "$id" = govless ] && [ "$owner" = xui ]; then
            printf "  %-11s ${GREEN}✓${NC}  %s ${DIM}— $(bi "панель 3X-UI, её возьмёт" "3X-UI panel, taken over by") $(prod_name "$id"): %s${NC}\n" "$port/$proto" "$what" "$proc" >&2
            _pf_add "$port/$proto|own|$level|$owner"
            PF_ROWS+=("$port/$proto|ok|$what|$(bi "панель 3X-UI" "3X-UI panel")")
            continue
        fi
        # «Свой» — только при переустановке. Если продукт по маркерам не
        # стоит, а его юниты держат порты, это остатки прежней установки,
        # и их надо назвать: установщик продукта на них споткнётся.
        if [ "$owner" = "$id" ] && [ "$self_reinstall" = "yes" ]; then
            printf "  %-11s ${GREEN}✓${NC}  %s ${DIM}— $(bi "свой" "own"): %s${NC}\n" "$port/$proto" "$what" "$proc" >&2
            _pf_add "$port/$proto|own|$level|$owner"
            PF_ROWS+=("$port/$proto|ok|$what|$(bi "свой: " "own: ")$proc")
            continue
        fi
        local who
        if [ "$proc" = docker-proxy ] && [ "$self_reinstall" = yes ] && prod_uses_docker "$id"; then
            # Порт держит контейнер, а чей — docker не сказал (не ответил или
            # отказал). Продукт стоит и сам держит свои порты в контейнерах:
            # при его обновлении это почти наверняка его же контейнер, и
            # запрещать обновление из-за него нельзя. Причину называем.
            who="docker-proxy ($(bi "контейнер не опознан, при обновлении $(prod_name "$id") это, скорее всего, его собственный" "container not identified; when updating $(prod_name "$id") it is most likely its own")${DOCKER_ERR:+; docker: $DOCKER_ERR})"
            [ "$level" = must ] && level=soft
        elif [ "$owner" = "$id" ] && [ "${proc#docker:}" != "$proc" ]; then
            # Контейнер AmneziaWG приложения Amnezia (или прежней установки):
            # первый экран мастера goAmnezia pro предлагает подхватить его
            # вместе с людьми, Ленивый pro подхватывает без вопроса, и порт
            # остаётся за тем же контейнером. Поэтому это замечание, а не
            # препятствие; откажется человек от подхвата — pro без UDP 443
            # не встанет, и это скажет уже сам мастер.
            who="$proc ($(bi "контейнер AmneziaWG уже на машине — $(prod_name "$id") предложит подхватить его вместе с людьми" "an AmneziaWG container is already here — $(prod_name "$id") offers to adopt it along with its users"))"
            [ "$level" = must ] && level=soft
        elif [ "$owner" = "$id" ]; then who="$proc ($(bi "остатки прежней установки" "leftovers of a previous install") $(prod_name "$owner"))"
        elif [ "$owner" = "$proc" ] || [ "$owner" = "system" ] || [ "$owner" = "nginx" ]; then who="$proc"
        else who="$proc → $(prod_name "$owner")"; fi
        local whou="$who"
        [ -n "$pid" ] && who="$who, pid $pid"
        case "$level" in
            must) PF_ROWS+=("$port/$proto|bad|$what|$whou") ;;
            soft) PF_ROWS+=("$port/$proto|note|$what|$whou") ;;
            *)    PF_ROWS+=("$port/$proto|info|$what|$whou") ;;
        esac
        case "$level" in
            must)
                printf "  %-11s ${RED}✗${NC}  %s ${RED}— $(bi "занят" "held by"): %s${NC}\n" "$port/$proto" "$what" "$who" >&2
                PF_MUST=$((PF_MUST+1)); _pf_add "$port/$proto|busy|must|$owner|$proc" ;;
            soft)
                printf "  %-11s ${YELLOW}!${NC}  %s ${YELLOW}— $(bi "занят" "held by"): %s${NC}\n" "$port/$proto" "$what" "$who" >&2
                PF_SOFT=$((PF_SOFT+1)); _pf_add "$port/$proto|busy|soft|$owner|$proc" ;;
            info)
                printf "  %-11s ${DIM}·  %s — $(bi "занят" "held by"): %s${NC}\n" "$port/$proto" "$what" "$who" >&2
                _pf_add "$port/$proto|busy|info|$owner|$proc" ;;
        esac
    done < <(prod_ports "$id" "$mode"; prod_ports_opt "$id")
    echo "" >&2

    # Особые правила соседства — то, чего таблица портов не выражает.
    PF_CAPTURE=1
    preflight_extra "$id" "$mode"
    PF_CAPTURE=""

    # Итог
    if [ "$PF_MUST" -gt 0 ]; then
        err "$(bi "Ставить нельзя: препятствий — ${PF_MUST}. Освободите порты или снимите конфликт и запустите проверку снова." "Cannot install: ${PF_MUST} blocker(s). Free the ports or resolve the conflict and re-run the check.")"
        return 1
    elif [ "$PF_SOFT" -gt 0 ]; then
        warn "$(bi "Препятствий нет, замечаний — ${PF_SOFT}. Установщик продукта предложит запасные варианты." "No blockers, ${PF_SOFT} remark(s). The product installer will offer fallbacks.")"
        return 2
    fi
    ok "$(bi "Порты свободны, требования выполнены." "Ports are free, requirements are met.")"
    return 0
}

# Правила, которые видно только из кода соседей.
preflight_extra() {
    local id="$1" mode="$2" h owner proc
    case "$id" in
        govless)
            if [ "$mode" = "pro" ]; then
                h=$(port_holders tcp 443 | head -1)
                if [ -n "$h" ]; then
                    IFS='|' read -r proc _ owner _ <<<"$h"
                    if [ "$owner" != "govless" ] && [ "$owner" != "xui" ]; then
                        err "$(bi "goVLESS pro в режиме pro на шаге 8 УБИВАЕТ любой не-xray процесс на 443 без вопроса (fuser -k). Сейчас там ${proc} ($(prod_name "$owner")) — он будет снесён." "goVLESS pro in pro mode at step 8 KILLS any non-xray process on 443 without asking (fuser -k). Right now that is ${proc} ($(prod_name "$owner")) — it would be taken down.")"
                        _pf_add "rule|must|govless-kills-443|$owner"
                        # Уже посчитано как must по таблице; второй раз не считаем.
                    fi
                fi
            fi
            # Чужая панель 3X-UI: установщик оставляет её (xui_already_installed),
            # при неудачном входе задаёт ей новые логин и пароль
            # (recover_panel_login), а подключение на 443 забирает, только если
            # оно того же вида, что режим: Reality для Lite, TLS для Pro
            # (reality_inbound_serving / tls_inbound_serving). Иное на 443 —
            # и своё подключение он создать не сможет.
            if govless_foreign_panel; then
                local kind=Reality; [ "$mode" = "pro" ] && kind=TLS
                warn "$(bi "Панель 3X-UI поставлена не $(prod_name govless): по другой инструкции или вручную. Установщик оставит её и возьмёт себе, а если не сможет войти, задаст ей новые логин и пароль. Готовое подключение ${kind} на 443 он заберёт, а подключение другого вида на этом порту помешает автонастройке: его придётся удалить в панели." "The 3X-UI panel was not installed by $(prod_name govless): another guide or by hand. The installer keeps it and takes it over, and if it cannot log in it sets a new panel login and password. An existing ${kind} inbound on 443 is taken over, an inbound of any other kind on that port blocks the auto-setup: delete it in the panel.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|govless-foreign-panel|xui"
            fi
            if prod_installed gokaskad && [ -f /etc/kaskad/egress/config.json ]; then
                warn "$(bi "У goKaskad pro включён прокси для API Telegram на 10808/10851 — те же порты возьмёт прокси goVLESS pro. Включать можно только один." "goKaskad pro has its Telegram API egress on 10808/10851 — the goVLESS pro egress takes the same ports. Only one can be enabled.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|egress-ports-shared|gokaskad"
            fi
            ;;
        gotelegram)
            # Контейнер mtproto-proxy: установщик pro находит его сам
            # (detect_v1_installation), спрашивает и останавливает — сразу, до
            # дисклеймера и выбора режима. Секрет mtg в telemt не переносится.
            scan_docker
            if grep -q '|mtproto-proxy$' <<<"$DOCKER_PORTS"; then
                warn "$(bi "На сервере прежний MTProxy на mtg (контейнер mtproto-proxy): бесплатная версия из инструкции или goTelegram pro первой версии. Установщик $(prod_name gotelegram) сам предложит его остановить. Секрет не переносится, клиентам понадобится новая ссылка. Старый прокси остановится сразу после ответа «да», ещё до выбора режима." "An earlier MTProxy on mtg is here (container mtproto-proxy): the free edition from the guide or goTelegram pro v1. The $(prod_name gotelegram) installer offers to stop it itself. The secret is not carried over, clients need a new link. The old proxy stops right after you answer yes, before the mode choice.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|gotelegram-old-mtproxy|mtproxy"
            fi
            h=$(port_holders tcp 443 | head -1)
            if [ -n "$h" ]; then
                IFS='|' read -r proc _ owner _ <<<"$h"
                case "$owner" in
                    govless|xui)
                        warn "$(bi "443 держит xray $(prod_name "$owner"). Совместить их можно только вручную: shared-443 (nginx stream + ssl_preread) включается из меню goTelegram pro ПОСЛЕ установки, а установка требует свободный 443." "443 is held by the $(prod_name "$owner") xray. They coexist only manually: shared-443 (nginx stream + ssl_preread) is enabled from the goTelegram pro menu AFTER install, and the install needs 443 free.")"
                        _pf_add "rule|info|shared443-hint|$owner" ;;
                    godns)
                        warn "$(bi "443 держит dnsdist goDNS pro (DoH на 443). Перенесите DoH goDNS pro на 4443 в его меню «Изменить порты» — тогда 443 освободится." "443 is held by the goDNS pro dnsdist (DoH on 443). Move the goDNS pro DoH to 4443 via its 'Change ports' menu — that frees 443.")"
                        _pf_add "rule|info|godns-doh-443|godns" ;;
                    goamnezia)
                        warn "$(bi "443 держит nginx goAmnezia pro. Его сайт можно выключить, переставив goAmnezia pro в режим Lite (только VPN): AmneziaWG работает по UDP и TCP 443 не занимает." "443 is held by the goAmnezia pro nginx. Its site can be dropped by switching goAmnezia pro to Lite mode (VPN only): AmneziaWG works over UDP and does not take TCP 443.")"
                        _pf_add "rule|info|goamnezia-site-443|goamnezia" ;;
                esac
            fi
            ;;
        godns)
            # 443 занят чужим — на стандартном порту goDNS pro не встанет, но
            # это не отказ: DoH уйдёт на 4443 (или следующий свободный), DoT
            # останется на 853. Сказать это прямо: строка «· к сведению» в
            # таблице и «готов» в меню читались как «встанет на 443».
            h=$(port_holders tcp 443 | head -1)
            if [ -n "$h" ]; then
                IFS='|' read -r proc _ owner _ <<<"$h"
                if [ "$owner" != godns ]; then
                    local who443="$proc"
                    case "$owner" in govless|gotelegram|goamnezia|gokaskad|gowarp|xui|mtproxy) who443="$(prod_name "$owner")" ;; esac
                    warn "$(bi "443 занят (${who443}): на стандартном порту $(prod_name godns) не встанет — встанет рядом: DoH на 4443 или следующем свободном, DoT на 853. iPhone и Mac получат профиль DoT: DoH не на 443 они не принимают." "443 is taken (${who443}): $(prod_name godns) cannot use the standard port — it installs beside it: DoH on 4443 or the next free port, DoT on 853. iPhone and Mac get a DoT profile: they do not accept DoH off 443.")"
                    PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|godns-beside-443|$owner"
                fi
            fi
            if ! prod_installed godns && ! prod_installed govless && ! prod_installed gotelegram \
               && ! ls -d "${GOINST_LE_ROOT:-/etc/letsencrypt}"/live/*/ >/dev/null 2>&1; then
                warn "$(bi "Сертификата на машине нет: ни goVLESS pro, ни goTelegram pro, ни Let's Encrypt. goDNS pro предложит выпустить свой — для этого нужны домен с A-записью и свободный :80." "No certificate on this machine: no goVLESS pro, no goTelegram pro, no Let's Encrypt. goDNS pro will offer to issue its own — that needs a domain with an A record and a free :80.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|no-cert|"
            fi
            ;;
        gokaskad)
            if [ -f /etc/govless/egress/config.json ] || systemctl is-enabled govless-egress.service >/dev/null 2>&1; then
                warn "$(bi "У goVLESS pro включён прокси для API Telegram на 10808/10851 — прокси goKaskad pro возьмёт те же порты. Включать можно только один." "goVLESS pro has its Telegram API egress on 10808/10851 — the goKaskad pro egress takes the same ports. Only one can be enabled.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|egress-ports-shared|govless"
            fi
            say "$(bi "Каскад работает через DNAT в PREROUTING: правило на порт, где что-то слушает локально (443 у xray/telemt, 853 у goDNS pro), молча перехватит внешний трафик у этого слушателя. Порты каскада выбирайте из свободных." "The cascade uses DNAT in PREROUTING: a rule on a port with a local listener (443 of xray/telemt, 853 of goDNS pro) silently hijacks external traffic from it. Pick cascade ports among free ones.")"
            ;;
        goamnezia)
            # Главное, что надо сказать про этот продукт: занятый TCP 443 не
            # мешает VPN. Сайт без него не поставить, а AmneziaWG живёт на
            # UDP, и UDP 443 у соседей по серии не занят никем.
            h=$(port_holders tcp 443 | head -1)
            if [ -n "$h" ] && [ "$mode" != "lite" ]; then
                IFS='|' read -r proc _ owner _ <<<"$h"
                if [ "$owner" != "goamnezia" ]; then
                    # UDP 443 в Lite не обещаем: AmneziaWG на UDP 443 — это
                    # и есть pro, а ему нужен сайт на TCP 443.
                    say "$(bi "TCP 443 держит ${proc}. Это мешает только сайту: в режиме Lite (только VPN) goAmnezia pro ставится рядом — AmneziaWG работает по UDP на своих портах." "TCP 443 is held by ${proc}. That blocks only the site: in Lite mode (VPN only) goAmnezia pro installs alongside — AmneziaWG works over UDP on its own ports.")"
                    _pf_add "rule|info|goamnezia-lite-alongside|$owner"
                fi
            fi
            if ! have_cmd docker && [ -z "${GOINST_FAKE_CMDS+x}" ]; then
                say "$(bi "Docker не установлен — goAmnezia pro поставит его сам." "Docker is not installed — goAmnezia pro installs it itself.")"
            fi
            # Штатный сайт Debian удаляется без спроса: сосед, который на него
            # рассчитывает, перестанет отвечать.
            if [ "$mode" != "lite" ] && [ -e "${GOINST_NGINX_ROOT:-/etc/nginx}/sites-enabled/default" ]; then
                warn "$(bi "goAmnezia pro удалит /etc/nginx/sites-enabled/default без вопроса: сайт по умолчанию перестанет отвечать." "goAmnezia pro removes /etc/nginx/sites-enabled/default without asking: the default site stops answering.")"
                PF_SOFT=$((PF_SOFT+1)); _pf_add "rule|soft|nginx-default-removed|"
            fi
            ;;
        gowarp)
            if have_cmd warp-cli && ! prod_installed gowarp; then
                err "$(bi "На машине уже есть warp-cli (пакет cloudflare-warp), поставленный не goWARP pro. Второй warp-svc на одной машине невозможен — сначала удалите существующий." "warp-cli (the cloudflare-warp package) is already here, not installed by goWARP pro. A second warp-svc on one machine is impossible — remove the existing one first.")"
                PF_MUST=$((PF_MUST+1)); _pf_add "rule|must|warp-cli-foreign|"
            fi
            ;;
    esac
    return 0
}

# ── карта портов ────────────────────────────────────────────────────────
show_ports() {
    local dl dproto dport dname h
    scan_listeners
    scan_docker
    print_header "$(bi "Кто какие порты держит" "Who holds which ports")"
    if [ -z "$LISTENERS" ]; then say "$(bi "Слушателей нет (или ss недоступен)." "No listeners (or ss is unavailable).")"; return 0; fi
    # Заголовок латиницей: %-Ns считает байты, кириллица разъехалась бы.
    printf "  ${DIM}%-5s %-22s %-26s %s${NC}\n" "proto" "address" "process (pid)" "$(bi "→ продукт" "→ product")" >&2
    printf '%s\n' "$LISTENERS" | sort -t'|' -k3,3n -k1,1 | while IFS='|' read -r proto addr port proc pid; do
        [ -n "$port" ] || continue
        local owner label
        owner=$(holder_product "$proc" "$pid")
        case "$owner" in
            govless|gotelegram|godns|gokaskad|gowarp|gocdn) label="${GREEN}$(prod_name "$owner")${NC}" ;;
            system) label="${DIM}$(bi "система" "system")${NC}" ;;
            xui)    label="${YELLOW}$(prod_name xui) ($(bi "без" "without") $(prod_name govless))${NC}" ;;
            mtproxy) label="${YELLOW}$(prod_name mtproxy)${NC}" ;;
            nginx)  label="${DIM}nginx ($(bi "ничей сайт" "no product site"))${NC}" ;;
            *)      label="${YELLOW}$(bi "чужой" "foreign")${NC}" ;;
        esac
        printf "  %-5s %-22s %-26s ${label}\n" "$proto" "${addr:-*}:$port" "${proc:-?}${pid:+ ($pid)}" >&2
    done
    # Контейнеры: при `userland-proxy: false` слушателя на хосте нет вовсе,
    # и в списке выше такой порт не появится.
    if [ -n "$DOCKER_PORTS" ]; then
        while IFS='|' read -r dproto dport dname; do
            [ -n "$dport" ] || continue
            h=$(port_holders "$dproto" "$dport") || h=""
            [ -n "$h" ] && grep -qv '^docker:' <<<"$h" && continue
            printf "  %-5s %-22s %-26s ${GREEN}%s${NC}\n" "$dproto" "0.0.0.0:$dport" "docker: $dname" "$(prod_name "$(container_product "$dname")")" >&2
        done <<<"$DOCKER_PORTS"
    fi
    echo "" >&2
}

# ── ключ доступа ────────────────────────────────────────────────────────
# Один ключ на все закрытые репозитории. Kaskad и goWARP тоже закрытые, но
# у их установщиков нет своей переменной — качаем файл сами.
# Ключ берётся из окружения, а если его там нет — из сохранённого файла.
# Окружение выигрывает: разовый запуск с другим ключом не должен молча
# подменять сохранённый.
TOKEN_FILE="${GOINST_TOKEN_FILE:-${GOINST_DIR}/token}"
BOOSTY_URL="${GOINST_BOOSTY_URL:-https://boosty.to/potap1978/purchase/3134076?ssource=DIRECT&share=subscription_link}"
CLUB_TOPIC="${GOINST_CLUB_TOPIC:-goinstall}"
TOKEN=""
TOKEN_FROM=""          # env | file | asked
TOKEN_STATUS=""        # ok | bad | denied | nonet | limited | none
TOKEN_DENIED=""        # репозитории, до которых ключ не дотянулся

# ── ожидание ответа GitHub и повторы ────────────────────────────────────
# Было 5 с на соединение, 8 на весь запрос и ни одного повтора. На сервере
# подписчика (6.10) каждый запрос к GitHub шёл 5–9 с и отвечал 200, а
# goInstaller обрывал все шесть проверок ключа и писал «GitHub недоступен»
# при живом GitHub. Заказчик: максимальная отказоустойчивость, и человек
# видит, что происходит и почему.
GH_CT="${GOINST_GH_CONNECT_TIMEOUT:-30}"   # соединение: DNS, TCP, TLS
GH_MT="${GOINST_GH_TIMEOUT:-60}"           # весь запрос, одна попытка
GH_TRIES="${GOINST_GH_TRIES:-4}"           # попыток на запрос
GH_DELAY="${GOINST_GH_RETRY_DELAY:-2}"     # первая пауза, дальше вдвое
GH_PINS=""; GH_PIN_DONE=""
# Куда сообщать о повторах: GH_RETRY_LOG — файл (фоновый опрос, параллельные
# проверки), GH_SAY=1 — сразу на экран (одиночное скачивание). GH_DOWN_FILE —
# метка «сети нет»: запрос, исчерпавший все попытки, ставит её, и следующие
# запросы того же запуска не ждут по кругу, а сразу отвечают тем же.
GH_RETRY_LOG=""; GH_SAY=""; GH_DOWN_FILE=""
GH_WHAT=""   # что сейчас качаем — в сообщении о повторе, чтобы строки различались

# Медленный DNS сервера добавлял свои секунды к каждому из десятков запросов
# опроса. Если адрес GitHub узнаётся дольше секунды, он узнаётся один раз и
# закрепляется в конфиге curl (resolve) до конца запуска. Только при живом
# IPv4-маршруте: на сервере без IPv4 закреплённый адрес отрезал бы GitHub.
gh_pin_slow_dns() {
    [ -n "$GH_PIN_DONE" ] && return 0
    GH_PIN_DONE=1
    [ -n "${GOINST_TEST:-}" ] && [ -z "${GOINST_PIN_TEST:-}" ] && return 0
    [ -n "${GOINST_HTTP_FIXTURE:-}" ] && return 0
    [ -n "$(ip -4 route show default 2>/dev/null)" ] || [ -n "${GOINST_PIN_TEST:-}" ] || return 0
    local h a t0
    for h in api.github.com raw.githubusercontent.com; do
        _ui_now_ms; t0="$UI_NOW"
        a=$(_net_resolve "$h")
        _ui_now_ms
        case "$a" in *.*.*.*) ;; *) continue ;; esac
        [ $(( UI_NOW - t0 )) -ge 1000 ] && GH_PINS+="resolve = \"${h}:443:${a}\""$'\n'
    done
    return 0
}
# Строки конфига curl для GitHub: ожидание и закреплённые адреса. В группе
# «{ …; } > cfg» — та же оболочка, поэтому закрепление делается один раз.
gh_cfg_net() {
    gh_pin_slow_dns
    printf 'connect-timeout = %s\n' "$GH_CT"
    printf '%s' "$GH_PINS"
}
# Код выхода curl без ответа GitHub → причина словами.
gh_net_reason() {
    case "$1" in
        5)  bi "не отвечает прокси сервера (переменные http_proxy и https_proxy)" "the server's proxy does not answer (http_proxy and https_proxy)" ;;
        6)  bi "сервер не узнаёт адрес api.github.com: не работает DNS (/etc/resolv.conf)" "the server cannot resolve api.github.com: DNS is broken (/etc/resolv.conf)" ;;
        7)  bi "соединение с GitHub не устанавливается: путь закрыт у хостера или фильтром" "no connection to GitHub: the path is closed by the hoster or a filter" ;;
        28) bi "время ожидания вышло: очень медленная сеть, фильтр в пути или размер пакета — goinstall --net" "the wait timed out: a very slow network, a filter on the path or the packet size — goinstall --net" ;;
        35|52|55|56) bi "соединение с GitHub рвётся на полпути: фильтр в пути или размер пакета — goinstall --net" "the GitHub connection breaks midway: a filter on the path or the packet size — goinstall --net" ;;
        60|77) bi "сертификат GitHub не прошёл проверку: неверное время на сервере (date) или нет пакета ca-certificates" "the GitHub certificate failed verification: wrong server time (date) or no ca-certificates package" ;;
        *)  bi "ошибка curl ${1}" "curl error ${1}" ;;
    esac
}

# Сетевой сбой — ответа GitHub не было: такое повторяем. Ответ GitHub
# (200, 304, 401, 403, 404) окончателен — повтор его не изменит, а лимит
# только потратит.
_gh_net_rc() { case "$1" in 5|6|7|16|18|28|35|52|55|56|92) return 0 ;; esac; return 1; }

gh_retry_note() {  # gh_retry_note <код curl> <попытка> <пауза>
    [ -n "$GH_RETRY_LOG" ] && printf '%s %s %s\n' "$1" "$(( $2 + 1 ))" "$GH_TRIES" >> "$GH_RETRY_LOG" 2>/dev/null
    [ "$GH_SAY" = 1 ] && warn "${GH_WHAT:+${GH_WHAT}: }$(bi "попытка ${2} из ${GH_TRIES} не удалась: $(gh_net_reason "$1"). Повтор через ${3} с…" "attempt ${2} of ${GH_TRIES} failed: $(gh_net_reason "$1"). Retrying in ${3} s…")"
    return 0
}

# gh_wait_report — дождаться фоновых запросов, по ходу говоря человеку, какая
# идёт попытка и почему: по журналу повторов GH_RETRY_LOG. Иначе на плохой
# сети человек минутами смотрел бы на «Проверяю ключ доступа…».
# Ждём только свои запросы (их PID), а не все фоновые задачи: в «Аркаде» во
# время ввода ключа фоном идёт опрос, и ждать его здесь незачем. Пока на
# экране «Аркада» (UI_ALT=1), строки не печатаем — они легли бы поверх меню.
gh_can_say() { [ "${UI_ALT:-0}" != 1 ]; }
gh_wait_report() {  # gh_wait_report <pid…>
    local shown=1 cur m r p alive
    while :; do
        alive=0
        for p in "$@"; do kill -0 "$p" 2>/dev/null && { alive=1; break; }; done
        [ "$alive" = 1 ] || break
        if gh_can_say && [ -s "$GH_RETRY_LOG" ]; then
            cur=$(awk '{ if ($2 + 0 > m) { m = $2 + 0; r = $1 } } END { print m + 0, r }' "$GH_RETRY_LOG")
            m="${cur%% *}"; r="${cur#* }"
            if [ "$m" -gt "$shown" ]; then
                shown="$m"
                say "$(bi "Попытка ${m} из ${GH_TRIES}: $(gh_net_reason "$r"). Жду ответа GitHub…" "Attempt ${m} of ${GH_TRIES}: $(gh_net_reason "$r"). Waiting for GitHub…")"
            fi
        fi
        sleep 1
    done
    wait "$@" 2>/dev/null
    return 0
}

# gh_curl <аргументы curl…> — curl к GitHub с повторами. Печатает вывод
# последней попытки (код из -w), отдаёт её код выхода. Временный ответ
# самого GitHub (5xx, 429, 408), видный по -w, тоже повторяется.
gh_curl() {
    local try=1 out rc delay="$GH_DELAY" again
    if [ -n "$GH_DOWN_FILE" ] && [ -s "$GH_DOWN_FILE" ]; then
        printf '000'; return "$(head -1 "$GH_DOWN_FILE" 2>/dev/null || echo 28)"
    fi
    while :; do
        # Сообщения самого curl — в никуда: причину мы говорим словами сами.
        out=$(curl "$@" 2>/dev/null); rc=$?
        again=0
        _gh_net_rc "$rc" && again=1
        case "$out" in 5[0-9][0-9]|429|408) again=1 ;; esac
        [ "$again" = 1 ] || break
        if [ "$try" -ge "$GH_TRIES" ]; then
            # Все попытки — сетевой сбой: сети нет, остальным не ждать.
            _gh_net_rc "$rc" && [ -n "$GH_DOWN_FILE" ] && printf '%s\n' "$rc" > "$GH_DOWN_FILE" 2>/dev/null
            break
        fi
        gh_retry_note "$rc" "$try" "$delay"
        sleep "$delay"; delay=$(( delay * 2 )); try=$(( try + 1 ))
    done
    printf '%s' "$out"
    return "$rc"
}

token_load() {
    local env_tok="${GH_TOKEN:-${GOTELEGRAM_PAT:-${GITHUB_PAT:-${GOINST_TOKEN:-}}}}"
    if [ -n "$env_tok" ]; then TOKEN="$env_tok"; TOKEN_FROM="env"; return 0; fi
    if [ -r "$TOKEN_FILE" ]; then
        TOKEN=$(head -1 "$TOKEN_FILE" 2>/dev/null) || TOKEN=""
        TOKEN="${TOKEN//[[:space:]]/}"
        [ -n "$TOKEN" ] && { TOKEN_FROM="file"; return 0; }
    fi
    TOKEN=""; TOKEN_FROM=""; return 1
}

# Сохраняем так, чтобы ключ не оказался доступен никому, кроме root:
# каталог 0700, файл 0600, режим выставляется ДО записи через umask.
token_save() {
    local dir
    dir=$(dirname "$TOKEN_FILE")
    mkdir -p "$dir" 2>/dev/null || { warn "$(bi "Не удалось создать ${dir} — ключ не сохранён." "Could not create ${dir} — the key is not saved.")"; return 1; }
    chmod 700 "$dir" 2>/dev/null || true
    ( umask 077; printf '%s\n' "$1" > "$TOKEN_FILE" ) 2>/dev/null || {
        warn "$(bi "Не удалось записать ${TOKEN_FILE} — ключ не сохранён." "Could not write ${TOKEN_FILE} — the key is not saved.")"; return 1; }
    chmod 600 "$TOKEN_FILE" 2>/dev/null || true
    return 0
}

token_forget() { rm -f "$TOKEN_FILE" 2>/dev/null || true; }

# Один запрос к GitHub на репозиторий: печатает код ответа. Ключ уходит
# заголовком из конфига 0600, а не аргументом.
_token_probe() {
    local repo="$1" cfg="$2" code
    # Фикстура для набора: строки «owner/repo=код». Иначе ветки «ключ
    # отвергнут», «доступа нет» и «сети нет» проверить нечем, а именно в
    # них живёт весь смысл этой функции.
    local hdr key etag
    local -a cond=()
    if [ -n "${GOINST_HTTP_FIXTURE:-}" ]; then
        code=$(sed -n -E "s#^${repo}=([0-9]+|RL)\$#\1#p" "$GOINST_HTTP_FIXTURE" 2>/dev/null | head -1) || code=""
        printf '%s' "${code:-000}"; return 0
    fi
    # Лимит уже исчерпан — спрашивать незачем: ответ известен заранее.
    gh_limited >/dev/null && { printf 'RL'; return 0; }
    # Условный запрос: 304 приходит только тому, кто прошёл проверку ключа,
    # а в лимит не засчитывается.
    key="token_${repo//[^A-Za-z0-9._-]/_}"; etag="$GH_HTTP_CACHE/$key.etag"
    [ -s "$etag" ] && cond=(-H "If-None-Match: $(head -1 "$etag")")
    hdr=$(mktemp "${TMPDIR:-/tmp}/goinst-hdr.XXXXXX") || { printf '000'; return 0; }
    # Код ответа берём из -w, а не из кода выхода: с опцией fail curl
    # выходит с ошибкой на любой 4xx, и «|| code=000» стирал бы 403 —
    # единственное, по чему лимит отличается от обрыва сети.
    local rc
    code=$(gh_curl --config "$cfg" -D "$hdr" -o /dev/null -w '%{http_code}' ${cond[@]+"${cond[@]}"} \
           --max-time "${GOINST_TOKEN_TIMEOUT:-$GH_MT}" \
           "https://api.github.com/repos/${repo}"); rc=$?
    [ -n "$code" ] || code="000"
    case "$code" in
        200) mkdir -p "$GH_HTTP_CACHE" 2>/dev/null && chmod 700 "$GH_HTTP_CACHE" 2>/dev/null
             _gh_etag_save "$hdr" "$etag" ;;
        304) code=200 ;;
        403|429) gh_limit_mark "$hdr" && code="RL" ;;
    esac
    rm -f "$hdr"
    # Ответа не было — к коду добавляем код выхода curl: по нему причина.
    [ "$code" = 000 ] && [ "$rc" -ne 0 ] && code="000/${rc}"
    printf '%s' "${code:-000}"
}

# token_check — проверяет, ЧИТАЕТ ли ключ наши репозитории. Именно читает:
# «ключ валиден» и «ключ открывает нужное» — разные утверждения, и второе
# единственное, которое здесь имеет смысл.
#   ok     — хотя бы один репозиторий отдал 200 и отказов нет
#   denied — ключ принят, но до части репозиториев не дотянулся (404/403)
#   bad    — ключ отвергнут (401) или закрыт для всего
#   nonet  — до GitHub не достучались: ключ не виноват, трогать его нельзя
token_check() {
    local id repo cfg code ok=0 denied=0 neterr=0 unauth=0 limited=0 tmp
    TOKEN_STATUS=""; TOKEN_DENIED=""; TOKEN_NETRC=""
    [ -n "$TOKEN" ] || { TOKEN_STATUS="none"; return 1; }
    if [ -n "${GOINST_NO_NET:-}" ]; then TOKEN_STATUS="ok"; return 0; fi
    command -v curl >/dev/null 2>&1 || { TOKEN_STATUS="ok"; return 0; }
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-tok.XXXXXX") || { TOKEN_STATUS="nonet"; return 1; }
    chmod 600 "$cfg"
    { echo 'silent'; echo 'location'; gh_cfg_net
      printf 'header = "Authorization: token %s"\n' "$TOKEN"; } > "$cfg"
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/goinst-tokd.XXXXXX") || { rm -f "$cfg"; TOKEN_STATUS="nonet"; return 1; }
    # Шесть проверок идут разом: о повторах каждая пишет в общий журнал, а
    # человеку — одна строка после. Свой журнал и метку «сети нет» берём,
    # только если их не задал вызывающий (фоновый опрос задаёт свои).
    local own_log="" own_down=""
    [ -n "$GH_RETRY_LOG" ] || { GH_RETRY_LOG="$tmp/retries"; own_log=1; }
    [ -n "$GH_DOWN_FILE" ] || { GH_DOWN_FILE="$tmp/down"; own_down=1; }
    local -a pids=()
    for id in "${PRODUCTS[@]}"; do
        repo=$(prod_repo "$id")
        ( GH_SAY=""; printf '%s' "$(_token_probe "$repo" "$cfg")" > "$tmp/$id" ) &
        pids+=("$!")
    done
    if [ -n "$own_log" ]; then gh_wait_report "${pids[@]}"; else wait "${pids[@]}"; fi
    TOKEN_RETRIES=0
    [ -s "$GH_RETRY_LOG" ] && TOKEN_RETRIES=$(wc -l < "$GH_RETRY_LOG" | tr -d ' ')
    local retry_rc=""; [ "$TOKEN_RETRIES" -gt 0 ] && retry_rc=$(awk 'END{print $1}' "$GH_RETRY_LOG")
    [ -n "$own_log" ] && GH_RETRY_LOG=""
    [ -n "$own_down" ] && GH_DOWN_FILE=""
    rm -f "$cfg"
    for id in "${PRODUCTS[@]}"; do
        code=$(cat "$tmp/$id" 2>/dev/null) || code="000"
        case "$code" in
            200) ok=$((ok+1)) ;;
            401) unauth=$((unauth+1)) ;;
            RL)  limited=$((limited+1)) ;;
            403|404) denied=$((denied+1)); TOKEN_DENIED+="$(prod_name "$id") " ;;
            000/*) neterr=$((neterr+1)); TOKEN_NETRC="${code#000/}" ;;
            *)   neterr=$((neterr+1)) ;;
        esac
    done
    rm -rf "$tmp"
    # Исчерпанный лимит — не приговор ключу: GitHub отвечает 403 и так, а
    # ключ исправен и после сброса снова заработает. Раньше такой отказ
    # читался как «ключ не принят»: ключ стирался на каждом сервере клуба,
    # запущенном в этот час.
    if [ "$limited" -gt 0 ]; then TOKEN_STATUS="limited"; return 0; fi
    if [ "$ok" -eq 0 ] && [ "$neterr" -gt 0 ] && [ "$unauth" -eq 0 ] && [ "$denied" -eq 0 ]; then
        TOKEN_STATUS="nonet"; return 1
    fi
    if [ "$unauth" -gt 0 ] || { [ "$ok" -eq 0 ] && [ "$denied" -gt 0 ]; }; then
        [ -n "$own_log" ] && [ "$TOKEN_RETRIES" -gt 0 ] && gh_can_say && say "$(bi "Связь с GitHub неустойчива: ответ получен после повторов (${TOKEN_RETRIES}). Причина сбоев: $(gh_net_reason "$retry_rc")." "The connection to GitHub is unstable: the answer came after retries (${TOKEN_RETRIES}). Cause of the failures: $(gh_net_reason "$retry_rc").")"
        TOKEN_STATUS="bad"; return 1
    fi
    # Ответ получен, но не с первого раза — сказать, что связь неустойчива.
    if [ -n "$own_log" ] && [ "$TOKEN_RETRIES" -gt 0 ] && gh_can_say; then
        say "$(bi "Связь с GitHub неустойчива: ответ получен после повторов (${TOKEN_RETRIES}). Причина сбоев: $(gh_net_reason "$retry_rc")." "The connection to GitHub is unstable: the answer came after retries (${TOKEN_RETRIES}). Cause of the failures: $(gh_net_reason "$retry_rc").")"
    fi
    if [ "$denied" -gt 0 ]; then TOKEN_STATUS="denied"; return 0; fi
    TOKEN_STATUS="ok"; return 0
}

# Где взять ключ. Печатается всегда, когда ключа нет или он не подошёл:
# человек в этот момент не должен идти искать инструкцию.
token_where() {
    echo "" >&2
    echo -e "  ${BOLD}$(bi "Где взять ключ" "Where to get the key")${NC}" >&2
    echo -e "  ${DIM}$(bi "Ключ на чтение выдаётся подписчикам в закрытой группе «potap1978 club»," "The read-only key is given to subscribers in the private «potap1978 club» group,")${NC}" >&2
    echo -e "  ${DIM}$(bi "в ветке" "in the") ${NC}${CYAN}${CLUB_TOPIC}${NC}${DIM}.${NC}" >&2
    echo -e "  $(bi "Подписка" "Subscription"): ${CYAN}${BOOSTY_URL}${NC}" >&2
    echo "" >&2
}

# Спросить ключ и проверить его на месте. Непроверенный ключ сохранять
# нельзя: человек уйдёт с ощущением, что всё настроено, и узнает правду
# в середине установки.
token_prompt() {
    local t attempt=0
    token_where
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt+1))
        if ! IFS= read -rsp "  $(bi "Ключ (ввод скрыт)" "Key (input hidden)"): " t; then echo "" >&2; return 1; fi
        echo "" >&2
        t="${t//[[:space:]]/}"
        # Пустой ответ — это «не сейчас», а не промах. Считать его ошибкой и
        # спрашивать ещё дважды значит держать человека, который всего лишь
        # хотел посмотреть занятые порты.
        if [ -z "$t" ]; then
            say "$(bi "Пропускаю. Ключ можно ввести позже: goinstall --key set" "Skipped. The key can be entered later: goinstall --key set")"
            return 1
        fi
        TOKEN="$t"; TOKEN_FROM="asked"
        say "$(bi "Проверяю ключ…" "Checking the key…")"
        token_check
        case "$TOKEN_STATUS" in
            ok)
                ok "$(bi "Ключ читает репозитории продуктов." "The key reads the product repositories.")"
                token_save "$t" && ok "$(bi "Ключ сохранён в ${TOKEN_FILE} (0600), спрашивать больше не буду." "The key is saved to ${TOKEN_FILE} (0600); it will not be asked again.")"
                return 0 ;;
            denied)
                warn "$(bi "Ключ принят, но не открывает: ${TOKEN_DENIED}" "The key works, but does not open: ${TOKEN_DENIED}")"
                token_save "$t" && ok "$(bi "Ключ сохранён; недостающий доступ спросите в группе." "The key is saved; ask in the group for the missing access.")"
                return 0 ;;
            nonet)
                warn "$(bi "До GitHub не достучаться — ключ проверить нечем. Сохраняю как есть." "GitHub is unreachable — the key cannot be checked. Saving as is.")"
                [ -n "${TOKEN_NETRC:-}" ] && say "$(bi "Причина" "Cause"): $(gh_net_reason "$TOKEN_NETRC"). $(bi "Пробовал ${GH_TRIES} раза на каждый запрос, ожидание до ${GH_MT} с." "Tried ${GH_TRIES} times per request, waiting up to ${GH_MT} s.")"
                token_save "$t"
                return 0 ;;
            limited)
                warn "$(bi "GitHub сейчас не проверяет ключи: исчерпан общий лимит запросов, снова заработает к $(gh_reset_text). Сохраняю ключ как есть." "GitHub is not checking keys right now: the shared request limit is used up until $(gh_reset_text). Saving the key as is.")"
                token_save "$t"
                return 0 ;;
            *)
                err "$(bi "GitHub этот ключ не принял. Проверьте, что скопирован он целиком." "GitHub rejected this key. Check that it was copied in full.")" ;;
        esac
    done
    TOKEN=""; TOKEN_FROM=""
    return 1
}

# Вызывается перед всем, чему ключ нужен.
ensure_token() {
    [ -n "$TOKEN" ] && return 0
    token_load && return 0
    token_prompt
}

# Проверка при запуске. Отсутствие ключа установку не отменяет: проверка
# портов и карта портов работают и без него, а требовать ключ ради того,
# чтобы посмотреть занятые порты, было бы враньём про необходимость.
token_startup() {
    return 0
}

prod_needs_token() {
    local v="GOINST_SRC_$1"
    [ -z "${!v:-}" ]
}

# Похоже ли скачанное на скрипт. Проверяем ПЕРВУЮ СТРОКУ, без конвейера.
#
# ГРАБЛИ: здесь стояло `head -c 200 "$f" | grep -q '^#!/'`. Под
# `set -o pipefail` это мина: grep -q выходит на первом совпадении и
# закрывает канал, head получает SIGPIPE, и статус ВСЕГО конвейера
# ненулевой — но только если head не успел дописать. Получалась гонка:
# один и тот же файл то скачивался, то объявлялся «не скриптом». На живом
# сервере из десяти загрузок проходили три.
looks_like_script() {
    local f="$1" first=""
    [ -s "$f" ] || return 1
    IFS= read -r first < "$f" 2>/dev/null || true
    case "$first" in '#!'*) return 0 ;; *) return 1 ;; esac
}

# ref_sha <repo> <ref> <cfg> — голова ветки одним хешем.
#
# ГРАБЛИ, стоившие живой проверки: у raw.githubusercontent на URL ВЕТКИ
# свой кэш (Cache-Control: max-age=300). Сразу после пуша он отдаёт
# ПРЕЖНИЙ файл, и установщик молча ставит старую версию, сообщая новую.
# Замечено на самом goInstaller: свежевыложенный 0.6.0 скачался как 0.5.1.
# Ни `no-cache`, ни `?t=<штамп>` этого не пробивают. Работает одно:
# спросить у API хеш коммита и качать по нему — такие URL неизменяемы.
# Бонусом все файлы приезжают из ОДНОГО коммита, и пуш в середине
# установки не смешает версии.
ref_sha() {
    local repo="$1" ref="$2" cfg="$3" tmp sha=""
    [ -n "${GOINST_NO_SHA:-}" ] && return 1
    tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-sha.XXXXXX") || return 1
    # Только хеш: полный ответ о коммите — десятки килобайт со списком
    # изменённых файлов (59 КБ на коммите goInstaller), а нужны 40 знаков.
    if gh_get "https://api.github.com/repos/${repo}/commits/${ref}" "$cfg" "$tmp" "application/vnd.github.sha"; then
        IFS= read -r sha < "$tmp" 2>/dev/null || true
        sha="${sha//[^0-9a-f]/}"
    fi
    rm -f "$tmp"
    [ "${#sha}" -eq 40 ] || return 1
    printf '%s' "$sha"
}

# fetch_raw <owner/repo> <ref> <path> <dest>
# Ключ — в конфиг-файле curl с режимом 0600, не в argv.
fetch_raw() {
    local repo="$1" ref="$2" path="$3" dest="$4" cfg url rc sha
    # Скачивание идёт на глазах у человека: о каждом повторе — сразу, и про
    # хеш ветки, и про сам файл.
    local GH_SAY=1 GH_DOWN_FILE="" GH_WHAT=""
    # Метка «сети нет» на одно скачивание: исчерпал попытки запрос хеша —
    # сам файл не ждёт по второму кругу.
    GH_DOWN_FILE=$(mktemp "${TMPDIR:-/tmp}/goinst-down.XXXXXX") || GH_DOWN_FILE=""
    : > "${GH_DOWN_FILE:-/dev/null}" 2>/dev/null
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-curl.XXXXXX") || return 1
    chmod 600 "$cfg"
    {
        echo 'silent'; echo 'location'; echo 'fail'
        gh_cfg_net; echo 'max-time = 180'
        [ -n "$TOKEN" ] && printf 'header = "Authorization: token %s"\n' "$TOKEN"
    } > "$cfg"
    # Качаем по хешу коммита, а не по имени ветки: см. ref_sha.
    GH_WHAT="$(bi "Хеш ветки" "Branch hash") ${ref}"
    if sha=$(ref_sha "$repo" "$ref" "$cfg"); then
        url="https://raw.githubusercontent.com/${repo}/${sha}/${path}"
    else
        url="https://raw.githubusercontent.com/${repo}/${ref}/${path}"
    fi
    GH_WHAT="${path}"
    gh_curl --config "$cfg" -o "$dest" "$url" >/dev/null; rc=$?
    rm -f "$cfg" "${GH_DOWN_FILE:-}"
    if [ "$rc" -ne 0 ] || [ ! -s "$dest" ]; then
        # Ни до какого ответа GitHub дело не дошло: имя, соединение, TLS,
        # обрыв. Ключ тут не проверялся, винить его нельзя.
        if _gh_net_rc "$rc"; then
            err "$(bi "Не скачалось: ${repo}@${ref}/${path}. GitHub не ответил ни на одну из ${GH_TRIES} попыток. Причина: $(gh_net_reason "$rc") (код curl ${rc}). Ключ тут ни при чём." "Download failed: ${repo}@${ref}/${path}. GitHub answered none of ${GH_TRIES} attempts. Cause: $(gh_net_reason "$rc") (curl code ${rc}). The key is not at fault.")"
            return 1
        fi
        err "$(bi "Не скачалось: ${repo}@${ref}/${path}. Проверьте ключ (нужен доступ на чтение к ${repo}), ветку и сеть." "Download failed: ${repo}@${ref}/${path}. Check the key (read access to ${repo} is required), the branch and the network.")"
        return 1
    fi
    # Страховка от страницы ошибки вместо скрипта.
    if ! looks_like_script "$dest"; then
        err "$(bi "Скачанный файл не похож на скрипт (нет shebang) — вероятно, 404 или неверный ключ." "The downloaded file does not look like a script (no shebang) — probably a 404 or a wrong key.")"
        return 1
    fi
    return 0
}

# ── источники продуктов ─────────────────────────────────────────────────
prod_repo() {
    local v="GOINST_REPO_$1"
    if [ -n "${!v:-}" ]; then printf '%s' "${!v}"; return; fi
    case "$1" in
        govless)    printf 'potap1978/go-all' ;;
        gotelegram) printf 'potap1978/go-all' ;;
        godns)      printf 'potap1978/go-all' ;;
        gokaskad)   printf 'potap1978/go-all' ;;
        gowarp)     printf 'potap1978/go-all' ;;
        goamnezia)  printf 'potap1978/go-all' ;;
    esac
}
prod_ref() {
    local v="GOINST_REF_$1"
    if [ -n "${!v:-}" ]; then printf '%s' "${!v}"; return; fi
    case "$1" in
        godns) printf 'main' ;;   # на GitHub у goDNS есть только dev
        *)     printf 'main' ;;
    esac
}
# Файл, который надо скачать и запустить.
prod_entry() {
    case "$1" in
        govless)    printf 'govless/bootstrap.sh' ;;
        gotelegram) printf 'gotelegram/bootstrap.sh' ;;
        godns)      printf 'godns/install.sh' ;;
        gokaskad)   printf 'gokaskad/install.sh' ;;
        gowarp)     printf 'gowarp/warp.sh' ;;
        goamnezia)  printf 'goamnezia/bootstrap.sh' ;;
    esac
}

# stdin: при `curl | bash` он занят самим скриптом — перецепляем на терминал.
reattach_tty() {
    if [ ! -t 0 ] && { true < /dev/tty; } 2>/dev/null; then
        exec < /dev/tty
    fi
}

# Проверка ПОСЛЕ установщика продукта: сменилась ли ветка и та ли версия.
# «✓ Updated» на экране продукта — не доказательство: goVLESS печатал его,
# оставаясь на прежней ветке. Отказ или отмена установки — не повод судить.
installer_postcheck() {
    local id="$1" ref="$2" got_ref want_v got_v src free m
    [ -n "$ref" ] || return 0
    prod_installed "$id" || return 0
    # Клон Lite на месте — pro не встал, что бы ни напечатал загрузчик: до
    # исправления он «обновлял» этот клон из репозитория Lite и запускал Lite.
    # С локальным источником каталог клона не трогается вовсе — тогда молчим.
    src="GOINST_SRC_${id}"
    if [ -z "${!src:-}" ] && free=$(prod_free_edition "$id"); then
        case "$id" in
            govless) warn "$(bi "Код в /opt/govless-installer по-прежнему от бесплатного Lite: загрузчик $(prod_name "$id") его не заменил, команда govless открывает Lite." "The code in /opt/govless-installer is still the free Lite: the $(prod_name "$id") loader did not replace it, and the govless command opens Lite.")" ;;
            *) warn "$(bi "Вместо $(prod_name "$id") по-прежнему бесплатный ${free}: установщик его не заменил, команда $(prod_cmd "$id") открывает бесплатную версию." "The free ${free} is still in place of $(prod_name "$id"): the installer did not replace it, and the $(prod_cmd "$id") command opens the free edition.")" ;;
        esac
        return 1
    fi
    if got_ref=$(prod_installed_ref "$id" raw); then
        if [ "$got_ref" != "$ref" ]; then
            warn "$(bi "Ветка не сменилась: $(prod_name "$id") стоит с ветки ${got_ref}, а выбрана ${ref}." "The branch did not change: $(prod_name "$id") runs from ${got_ref}, but ${ref} was chosen.")"
            return 1
        fi
    fi
    want_v=$(ver_cache_get "$id" "$ref") || want_v=""
    got_v=$(prod_version "$id")
    if [ -n "$want_v" ] && [ -n "$got_v" ] && [ "$want_v" != "$got_v" ]; then
        warn "$(bi "Стоит версия ${got_v}, а в ветке ${ref} лежит ${want_v}: установка могла не дойти до конца." "Installed ${got_v}, but ${ref} has ${want_v}: the install may not have completed.")"
        return 1
    fi
    ok "$(bi "Проверено: $(prod_name "$id") стоит с ветки ${ref}${got_v:+, версия ${got_v}}." "Verified: $(prod_name "$id") runs from ${ref}${got_v:+, version ${got_v}}.")"
    # Ленивый pro — сценарий, а не режим: встаёт pro или, при занятом UDP 443,
    # Semi-pro. Каким он встал, человек узнаёт здесь, не открывая продукт.
    if [ "${MODE:-}" = lazy ] && m=$(prod_installed_mode "$id"); then
        say "$(bi "Ленивый pro встал в режиме" "Lazy pro ended up in mode") $(mode_name "$m")."
    fi
    return 0
}

# Язык для установщика goKaskad pro 3 (KASKAD_LANG): только при первой
# установке. Язык из окружения 3.x ставит выше записанного и запоминает:
# обновление переписало бы язык, выбранный в меню продукта, в панели или в
# боте, а переход с 2.x — русский, который 3.x оставляет сам. Пусто — 3.x
# возьмёт свой (или спросит).
gokaskad_lang_env() {
    prod_installed gokaskad && return 0
    grep -qs '^[[:space:]]*KASKAD_LANG=' "${GOINST_FS_ROOT:-}/etc/kaskad/config" && return 0
    if [ "${LANG_CODE:-ru}" = en ]; then printf 'en'; else printf 'ru'; fi
}

# Запуск установщика продукта. Ключ — только через окружение и только в
# те переменные, которые продукт читает сам.
run_product_installer() {
    local id="$1" work entry rc key="" repo ref src local_src
    # ГРАБЛИ: `local src="GOINST_SRC_$1" local_src="${!src:-}"` одной строкой
    # НЕ работает. bash раскрывает все правые части ДО присваиваний, поэтому
    # на момент `${!src}` переменная `src` ещё пуста — выходит «invalid
    # indirect expansion», а под `set -u` это обрывает весь скрипт. Ломалась
    # при этом ЛЮБАЯ установка: строка стоит первой в функции. Ни один тест
    # этого не ловил, потому что запуск чужого установщика они не трогают.
    # Присваивать раздельно.
    src="GOINST_SRC_$1"
    local_src="${!src:-}"
    # Ленивый pro у goAmnezia pro запускается флагом, без меню продукта:
    # `install.sh --lazy [--domain=…] [--keys=N]` (его docs/MODES_047.md, п. 7),
    # bootstrap передаёт аргументы install.sh как есть. Домен без
    # GOINST_LAZY_DOMAIN продукт спросит сам, со своими подсказками.
    local -a amz=()
    if [ "$id" = goamnezia ] && [ "${MODE:-}" = lazy ]; then
        amz=(--lazy)
        [ -n "${GOINST_LAZY_DOMAIN:-}" ] && amz+=("--domain=${GOINST_LAZY_DOMAIN}")
        [ -n "${GOINST_LAZY_KEYS:-}" ] && amz+=("--keys=${GOINST_LAZY_KEYS}")
    fi
    repo=$(prod_repo "$id"); ref="${REF:-$(prod_ref "$id")}"; entry=$(prod_entry "$id")
    work="${GOINST_DIR}/${id}"
    # Сообщение от mkdir вылезало сырым посреди разговора с человеком.
    # Своё — называет и каталог, и причину.
    if ! mkdir -p "$work" 2>/dev/null; then
        err "$(bi "Не удалось создать ${work} — нужны права root." "Could not create ${work} — root is required.")"
        return 1
    fi
    chmod 700 "$GOINST_DIR" 2>/dev/null || true

    if [ "$id" = "gowarp" ]; then
        # Лицензионный ключ goWARP передаётся первым аргументом warp.sh.
        key="${GOWARP_KEY:-}"
        if [ -z "$key" ] && ! prod_installed gowarp; then
            read_line key "  $(bi "Лицензионный ключ goWARP pro (из группы)" "goWARP pro license key (from the group)"): " || return 1
        fi
    fi

    if [ -n "$local_src" ]; then
        say "$(bi "Локальный источник: ${local_src}" "Local source: ${local_src}")"
        case "$id" in
            govless)    [ -f "$local_src/govless.sh" ] || { err "$(bi "Нет govless.sh в ${local_src}" "No govless.sh in ${local_src}")"; return 1; }
                        ( cd "$local_src" && bash ./govless.sh ); rc=$? ;;
            gotelegram) [ -f "$local_src/install.sh" ] || { err "$(bi "Нет install.sh в ${local_src}" "No install.sh in ${local_src}")"; return 1; }
                        ( cd "$local_src" && bash ./install.sh ); rc=$? ;;
            godns)      [ -f "$local_src/install.sh" ] || { err "$(bi "Нет install.sh в ${local_src}" "No install.sh in ${local_src}")"; return 1; }
                        GODNS_LOCAL_SRC="$local_src" bash "$local_src/install.sh"; rc=$? ;;
            gokaskad)   cp -f "$local_src/install.sh" "$work/install.sh" || return 1
                        chmod 700 "$work/install.sh"; ( cd "$work" && KASKAD_BRANCH="${REF:-$(prod_ref "$id")}" KASKAD_LANG="$(gokaskad_lang_env)" ./install.sh ); rc=$? ;;
            gowarp)     cp -f "$local_src/warp.sh" "$work/warp.sh" || return 1
                        chmod 700 "$work/warp.sh"; ( cd "$work" && bash ./warp.sh "$key" ); rc=$? ;;
            goamnezia)  [ -f "$local_src/install.sh" ] || { err "$(bi "Нет install.sh в ${local_src}" "No install.sh in ${local_src}")"; return 1; }
                        ( cd "$local_src" && bash ./install.sh ${amz[@]+"${amz[@]}"} ); rc=$? ;;
        esac
        return "$rc"
    fi

    true
    say "$(bi "Скачиваю ${entry} из ${repo}@${ref}…" "Downloading ${entry} from ${repo}@${ref}…")"
    fetch_raw "$repo" "$ref" "$entry" "$work/$entry" || return 1
    chmod 700 "$work/$entry"
    echo ""
    hr
    echo -e "  ${BOLD}$(bi "Передаю управление установщику" "Handing over to the installer") $(prod_name "$id")${NC}"
    hr
    echo ""
    case "$id" in
        # bootstrap goVLESS клонирует репозиторий в /opt/govless-installer; ключ читает из GH_TOKEN.
        # Смену ветки у установленного загрузчик goVLESS делает сам с 1.3.7;
        # итог сверяет installer_postcheck.
        govless)    ( cd "$work" && GOVLESS_BRANCH="$ref" bash ./bootstrap.sh ); rc=$? ;;
        # bootstrap goTelegram качает файлы по SHA ветки; ключ — GOTELEGRAM_PAT.
        gotelegram) ( cd "$work" && GOTELEGRAM_BRANCH="$ref" bash ./bootstrap.sh ); rc=$? ;;
        # install.sh goDNS тянет tarball через api.github.com; ключ — GH_TOKEN.
        godns)      ( cd "$work" && GODNS_REF="$ref" bash ./install.sh ); rc=$? ;;
        # Kaskad и goWARP — сами себе программа: копируют $0 в /usr/local/bin,
        # поэтому запускать их надо ИЗ ФАЙЛА, а не из подстановки процесса.
        # 3.x записывает KASKAD_BRANCH ветвью обновлений; 2.x ни её, ни KASKAD_LANG не читает.
        gokaskad)   ( cd "$work" && KASKAD_BRANCH="$ref" KASKAD_LANG="$(gokaskad_lang_env)" ./install.sh ); rc=$? ;;
        gowarp)     ( cd "$work" && bash ./warp.sh "$key" ); rc=$? ;;
        # bootstrap goAmnezia качает срез ветки по SHA; ключ — GOAMNEZIA_PAT.
        # ${amz[@]+…}: пустой массив под set -u в bash до 4.4 — «unbound variable».
        goamnezia)  ( cd "$work" && GOAMNEZIA_BRANCH="$ref" bash ./bootstrap.sh ${amz[@]+"${amz[@]}"} ); rc=$? ;;
    esac
    return "$rc"
}

# ── опрос версий на GitHub ──────────────────────────────────────────────
# Что доступно в ветке, видно только из репозитория, а репозитории закрытые:
# без ключа опрос не делается вовсе. Молча висеть на сети меню не должно,
# поэтому: все запросы разом в фоне, жёсткий тайм-аут на каждый, ответ
# кладётся в кеш со штампом времени. Промах кеша тоже кладётся — иначе
# недоступная ветка опрашивалась бы на каждой перерисовке экрана.
VER_CACHE="${GOINST_VER_CACHE:-${GOINST_DIR}/versions}"
VER_TTL="${GOINST_VER_TTL:-60}"
VER_NET_TRIED="no"   # опрос делали хотя бы раз
VER_NET_OK="no"      # хоть один ответ получен

# ── GitHub: условные запросы и общий лимит ──────────────────────────────
# Ключ клуба один на всех, а лимит GitHub — 5000 запросов в час на
# владельца ключа, то есть на все серверы клуба разом. Опрос версий стоил
# около двадцати запросов, открытое меню повторяло его раз в три минуты, и
# полтора десятка открытых меню выбирали лимит за час. Два приёма:
#  - условный запрос: ETag прошлого ответа уходит в If-None-Match, и ответ
#    304 «не изменилось» GitHub в лимит не засчитывает (проверено на ключе
#    клуба: x-ratelimit-used на 304 не растёт);
#  - исчерпанный лимит — отдельное состояние, а не «ключ плохой». GitHub
#    отвечает 403 и на него, и на закрытый репозиторий; различает их только
#    заголовок. До часа сброса сеть не трогаем, прежние сведения держим.
GH_HTTP_CACHE="${VER_CACHE}/http"
GH_LIMIT_FILE="${VER_CACHE}/@ratelimit"

_hhmm() { date -d "@$1" +%H:%M 2>/dev/null || date -r "$1" +%H:%M 2>/dev/null || printf '?'; }

# gh_limited → 0, пока действует отметка об исчерпанном лимите; печатает
# время сброса «ЧЧ:ММ». Прошедшая отметка удаляется.
gh_limited() {
    local until="" now
    [ -r "$GH_LIMIT_FILE" ] || return 1
    read -r until < "$GH_LIMIT_FILE" 2>/dev/null || true
    case "$until" in ''|*[!0-9]*) return 1 ;; esac
    now=$(_ver_now)
    if [ "$now" -ge "$until" ]; then rm -f "$GH_LIMIT_FILE" 2>/dev/null; return 1; fi
    _hhmm "$until"
    return 0
}
# «к 15:00» или «к началу следующего часа», когда отметки нет.
gh_reset_text() {
    local t
    if t=$(gh_limited); then printf '%s' "$t"; else bi "началу следующего часа" "the top of the hour"; fi
}

# gh_limit_mark <заголовки> → 0, если отказ — исчерпанный лимит, и отметка
# записана. Признаки: x-ratelimit-remaining: 0 (основной лимит) или
# retry-after (вторичный, за частоту).
gh_limit_mark() {
    local h rem reset retry now until
    [ -r "$1" ] || return 1
    h=$(tr -d '\r' < "$1" | tr '[:upper:]' '[:lower:]')
    rem=$(sed -n -E 's/^x-ratelimit-remaining:[[:space:]]*([0-9]+).*/\1/p' <<<"$h" | tail -1)
    reset=$(sed -n -E 's/^x-ratelimit-reset:[[:space:]]*([0-9]+).*/\1/p' <<<"$h" | tail -1)
    retry=$(sed -n -E 's/^retry-after:[[:space:]]*([0-9]+).*/\1/p' <<<"$h" | tail -1)
    now=$(_ver_now)
    if [ -n "$retry" ]; then until=$(( now + retry ))
    elif [ "$rem" = "0" ]; then until="${reset:-$(( now + 3600 ))}"
    else return 1; fi
    mkdir -p "$VER_CACHE" 2>/dev/null || true
    printf '%s\n' "$until" > "$GH_LIMIT_FILE" 2>/dev/null || true
    return 0
}

_gh_etag_save() {  # _gh_etag_save <заголовки> <файл метки>
    tr -d '\r' < "$1" | sed -n -E 's/^[Ee][Tt][Aa][Gg]:[[:space:]]*(.+)$/\1/p' | head -1 > "$2" 2>/dev/null || true
}

# gh_get <url> <cfg> <куда> [Accept] → 0 и тело в файле; 2 — лимит
# исчерпан; 1 — нет ответа или нет доступа. Тело прошлого ответа и его ETag
# лежат в кеше: на 304 отдаётся тело оттуда.
gh_get() {
    local url="$1" cfg="$2" out="$3" accept="${4:-}" key body etag hdr code
    local -a extra=()
    gh_limited >/dev/null && return 2
    key="${url#https://api.github.com/}"; key="${key//[^A-Za-z0-9._-]/_}"
    body="$GH_HTTP_CACHE/$key.body"; etag="$GH_HTTP_CACHE/$key.etag"
    mkdir -p "$GH_HTTP_CACHE" 2>/dev/null && chmod 700 "$GH_HTTP_CACHE" 2>/dev/null
    [ -n "$accept" ] && extra+=(-H "Accept: $accept")
    if [ -s "$body" ] && [ -s "$etag" ]; then extra+=(-H "If-None-Match: $(head -1 "$etag")"); fi
    hdr=$(mktemp "${TMPDIR:-/tmp}/goinst-hdr.XXXXXX") || return 1
    rm -f "$out"
    # ${extra[@]+…}: пустой массив под set -u в bash до 4.4 — «unbound variable».
    code=$(gh_curl --config "$cfg" --max-time "${GOINST_VER_TIMEOUT:-$GH_MT}" ${extra[@]+"${extra[@]}"} \
           -D "$hdr" -o "$out" -w '%{http_code}' "$url") || true
    case "$code" in
        200)
            cp -f "$out" "$body" 2>/dev/null || true
            _gh_etag_save "$hdr" "$etag"
            rm -f "$hdr"; return 0 ;;
        304)
            # На 304 тела нет, и curl файла не создаёт.
            rm -f "$hdr"
            cp -f "$body" "$out" 2>/dev/null && return 0
            return 1 ;;
        403|429)
            if gh_limit_mark "$hdr"; then rm -f "$hdr" "$out"; return 2; fi ;;
    esac
    rm -f "$hdr" "$out"
    return 1
}

_ver_now() { printf '%s' "$(date +%s)"; }

# Файл кеша: одна строка "<штамп> <версия>"; пустая версия — «спрашивали,
# там нет».
# ГРАБЛИ: имя ветки бывает со слэшем (`backup/pre-v2.5`), и оно уезжало в
# путь файла как подкаталог — запись падала с «No such file or directory».
# Заменяем всё, что не буква и не цифра.
_ver_cache_file() {
    local id="${1//[^A-Za-z0-9@_.-]/_}" ref="${2//[^A-Za-z0-9@_.-]/_}"
    printf '%s/%s.%s' "$VER_CACHE" "$id" "$ref"
}

ver_cache_read() {   # ver_cache_read <id> <ref> → версия; rc 1 — нет свежего
    local f stamp rest now
    f=$(_ver_cache_file "$1" "$2")
    [ -r "$f" ] || return 1
    read -r stamp rest < "$f" 2>/dev/null || return 1
    case "$stamp" in ''|*[!0-9]*) return 1 ;; esac
    now=$(_ver_now)
    [ $(( now - stamp )) -lt "$VER_TTL" ] || return 1
    printf '%s' "$rest"
    return 0
}

# ver_cache_get — то же, но без срока годности: для ПОКАЗА. Срок в минуту
# решает, пора ли спросить GitHub заново, а не то, можно ли показать
# последнее известное. Иначе через минуту после опроса колонка версий
# пустела бы до следующего такта, хотя ответ лежит на диске.
ver_cache_get() {
    local f stamp rest
    f=$(_ver_cache_file "$1" "$2")
    [ -r "$f" ] || return 1
    read -r stamp rest < "$f" 2>/dev/null || return 1
    case "$stamp" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s' "$rest"
    return 0
}

ver_cache_write() { local f; f=$(_ver_cache_file "$1" "$2"); printf '%s %s\n' "$(_ver_now)" "${3:-}" > "$f" 2>/dev/null || true; }
# Прежнее значение с новым штампом: сведения не терялись, а переспрос ждал TTL.
ver_cache_touch() { local v; v=$(ver_cache_get "$1" "$2") || v=""; ver_cache_write "$1" "$2" "$v"; }

# Один запрос: сырой файл из ветки → номер версии. Ошибки молчат: отсутствие
# ветки и отсутствие сети для этого экрана — не событие.
_ver_fetch_one() {
    local id="$1" ref="$2" cfg="$3" repo spec var path url tmp v=""
    repo=$(prod_repo "$id")
    spec=$(prod_ver_spec "$id") || spec=""
    var="${spec#*|}"; [ "$var" = "$spec" ] && var=""
    tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-ver.XXXXXX") || return 0
    local at="$ref" sha="" shaf s0="" v0=""
    sha=$(ref_sha "$repo" "$ref" "$cfg") && at="$sha"
    # Версия на коммите не меняется никогда: тот же хеш — тот же ответ, и
    # файл из ветки качать незачем.
    shaf="$(_ver_cache_file "$id" "$ref").sha"
    if [ -n "$sha" ] && [ -r "$shaf" ]; then
        read -r s0 v0 < "$shaf" 2>/dev/null || s0=""
        if [ "$s0" = "$sha" ] && [ -n "$v0" ]; then rm -f "$tmp"; ver_cache_write "$id" "$ref" "$v0"; return 0; fi
    fi
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        url="https://raw.githubusercontent.com/${repo}/${at}/${path}"
        gh_curl --config "$cfg" --max-time "${GOINST_VER_TIMEOUT:-$GH_MT}" -o "$tmp" "$url" >/dev/null 2>&1 || continue
        # Сначала ищем названную переменную, потом любую «…VERSION=».
        # Второе спасает, когда продукт переименовал её у себя.
        if [ -n "$var" ] && v=$(ver_extract "$tmp" "$var"); then break; fi
        if v=$(ver_extract_any "$tmp"); then break; fi
        v=""
    done < <(prod_ver_paths "$id")
    rm -f "$tmp"
    ver_cache_write "$id" "$ref" "$v"
    if [ -n "$sha" ] && [ -n "$v" ]; then printf '%s %s\n' "$sha" "$v" > "$shaf" 2>/dev/null || true; fi
    return 0
}

# Ветка, С КОТОРОЙ продукт стоит. goTelegram и goAmnezia записывают её при
# установке, goKaskad pro 3 — в своих настройках, goVLESS — HEAD клона; у
# остальных спросить неоткуда. Нужна как умолчание при
# обновлении: человек, сидящий на стабильной ветке, не должен переезжать на
# отладочную оттого, что промахнулся по Enter.
prod_installed_ref() {
    local r="${GOINST_FS_ROOT:-}" f v
    # goVLESS не пишет .install-source: его код лежит git-клоном, и ветка —
    # это просто HEAD клона.
    if [ "$1" = "govless" ]; then
        # Клон бесплатного Lite — не ветка pro. У Lite своя dev, и её HEAD,
        # взятый умолчанием, увёл бы человека на отладочную ветку pro.
        prod_free_lite govless && return 1
        f="$r/opt/govless-installer"
        [ -d "$f/.git" ] || return 1
        v=$(git -C "$f" rev-parse --abbrev-ref HEAD 2>/dev/null) || v=""
        case "$v" in ''|HEAD) return 1 ;; esac
        printf '%s' "$v"
        return 0
    fi
    # goKaskad pro 3 пишет ветку обновлений в свои настройки (KASKAD_BRANCH).
    # 2.x веток не знает: строка, оставшаяся от 3.x после возврата на 2.x,
    # ветку не означает. 3.x живёт в dev (на main — 2.x), а сам себе пишет
    # и k3, и test (цепочка обновления main → test → k3), или ничего, если
    # ставили без переменной. Ветка не из списка — значит dev: иначе
    # умолчанием стала бы main, и Enter откатил бы 3.x на 2.x. Сверке после
    # установки (второй аргумент raw) нужна записанная ветка как есть: k3
    # после оборванного установщика — не «стоит с dev».
    if [ "$1" = "gokaskad" ]; then
        local rf
        v=$(prod_version gokaskad 2>/dev/null) || v=""
        case "$v" in [3-9].*|[1-9][0-9]*.*) ;; *) return 1 ;; esac
        f="$r/etc/kaskad/config"
        v=$(sed -n -E 's/^[[:space:]]*KASKAD_BRANCH=["'"'"']?([A-Za-z0-9._/-]+)["'"'"']?[[:space:]]*$/\1/p' "$f" 2>/dev/null | tail -1) || v=""
        if [ "${2:-}" = raw ]; then [ -n "$v" ] || return 1; printf '%s' "$v"; return 0; fi
        for rf in $(prod_refs gokaskad); do [ "$rf" = "$v" ] && { printf '%s' "$v"; return 0; }; done
        for rf in $(prod_refs gokaskad); do [ "$rf" = dev ] && { printf 'dev'; return 0; }; done
        return 1
    fi
    case "$1" in
        gotelegram) f="$r/opt/gotelegram/.install-source" ;;
        goamnezia)  f="$r/opt/goamnezia/.install-source" ;;
        *) return 1 ;;
    esac
    [ -r "$f" ] || return 1
    v=$(sed -n -E 's/^[[:space:]]*branch=(.+)$/\1/p' "$f" 2>/dev/null | head -1) || v=""
    [ -n "$v" ] || return 1
    printf '%s' "$v"
}

# Чем ветка отличается для того, кто выбирает. Имена веток говорят это
# только тем, кто их уже знает.
ref_kind() {
    case "$1" in
        main|master) bi "стабильная" "stable" ;;
        dev)         bi "экспериментальная" "experimental" ;;
        rc*)         bi "предрелизная" "release candidate" ;;
        *)           printf '' ;;
    esac
}

# Опрос «удался» не тогда, когда curl вернул 0, а тогда, когда на руках есть
# хоть один номер версии. Иначе колонка рисуется пустой на всю высоту.
_ver_mark_ok() {
    local id
    VER_NET_OK="no"
    for id in "$@"; do
        if prod_best_remote "$id" >/dev/null 2>&1; then VER_NET_OK="yes"; return 0; fi
    done
    return 0
}

# versions_fetch [id…] — опросить всё, чего нет в свежем кеше. Запросы идут
# параллельно: последовательно шесть продуктов на двух ветках — это дюжина
# рукопожатий TLS подряд, то есть заметное ожидание на пустом месте.
versions_fetch() {
    local ids=("${@:-${PRODUCTS[@]}}") id ref cfg need=0
    # Не спрашивать — не значит не знать: спрошенное раньше лежит в кеше, и
    # прятать его из-за отсутствия сети значило бы терять готовый ответ.
    [ -z "${GOINST_NO_NET:-}" ] || { _ver_mark_ok "${ids[@]}"; return 0; }
    [ -n "$TOKEN" ] || { _ver_mark_ok "${ids[@]}"; return 0; }
    command -v curl >/dev/null 2>&1 || { _ver_mark_ok "${ids[@]}"; return 0; }
    # Лимит исчерпан — до сброса сеть не трогаем: прежние сведения из кеша
    # остаются на экране, а новый вопрос получил бы тот же отказ.
    if gh_limited >/dev/null; then VER_NET_TRIED="yes"; _ver_mark_ok "${ids[@]}"; return 0; fi
    mkdir -p "$VER_CACHE" 2>/dev/null || return 0
    chmod 700 "$VER_CACHE" 2>/dev/null || true
    for id in "${ids[@]}"; do
        ver_cache_read "$id" "@refs" >/dev/null || need=1
        for ref in $(prod_refs_probe "$id"); do
            ver_cache_read "$id" "$ref" >/dev/null || need=1
        done
    done
    [ "$need" -eq 1 ] || { VER_NET_TRIED="yes"; _ver_mark_ok "${ids[@]}"; return 0; }
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-vercurl.XXXXXX") || return 0
    chmod 600 "$cfg"
    {
        echo 'silent'; echo 'location'; echo 'fail'
        gh_cfg_net
        printf 'header = "Authorization: token %s"\n' "$TOKEN"
    } > "$cfg"
    say "$(bi "Спрашиваю GitHub про ветки и версии…" "Asking GitHub about branches and versions…")"
    VER_NET_TRIED="yes"
    # Свои журнал повторов и метка «сети нет» на этот заход — если их не
    # задал вызывающий. После — одна строка о том, что было и почему.
    local vrun="" vdir=""
    if [ -z "$GH_DOWN_FILE" ] && vdir=$(mktemp -d "${TMPDIR:-/tmp}/goinst-vrun.XXXXXX"); then
        GH_DOWN_FILE="$vdir/down"; GH_RETRY_LOG="$vdir/retries"; vrun=1
    fi
    # Ветки — первым заходом: какие версии спрашивать, известно только
    # после того, как известно, какие ветки вообще есть.
    local -a vp=()
    for id in "${ids[@]}"; do
        ver_cache_read "$id" "@refs" >/dev/null && continue
        refs_fetch_one "$id" "$cfg" &
        vp+=("$!")
    done
    if [ -n "$vrun" ]; then gh_wait_report ${vp[@]+"${vp[@]}"}; else wait; fi
    vp=()
    for id in "${ids[@]}"; do
        for ref in $(prod_refs_probe "$id"); do
            ver_cache_read "$id" "$ref" >/dev/null && continue
            _ver_fetch_one "$id" "$ref" "$cfg" &
            vp+=("$!")
        done
    done
    # Ключ лежит в файле, пока идут запросы: удалять его можно только
    # после того, как все дочерние curl закончили им пользоваться.
    if [ -n "$vrun" ]; then gh_wait_report ${vp[@]+"${vp[@]}"}; else wait; fi
    rm -f "$cfg"
    if [ -n "$vrun" ] && gh_can_say; then
        if [ -s "$GH_DOWN_FILE" ]; then
            warn "$(bi "GitHub не отвечает: $(gh_net_reason "$(head -1 "$GH_DOWN_FILE")"). Показываю прежние сведения о версиях." "GitHub does not answer: $(gh_net_reason "$(head -1 "$GH_DOWN_FILE")"). Showing the previous version data.")"
        elif [ -s "$GH_RETRY_LOG" ]; then
            say "$(bi "Связь с GitHub неустойчива: сведения получены после повторов ($(wc -l < "$GH_RETRY_LOG" | tr -d ' ')). Причина сбоев: $(gh_net_reason "$(awk 'END{print $1}' "$GH_RETRY_LOG")")." "The GitHub connection is unstable: the data came after retries ($(wc -l < "$GH_RETRY_LOG" | tr -d ' ')). Cause of the failures: $(gh_net_reason "$(awk 'END{print $1}' "$GH_RETRY_LOG")").")"
        fi
    fi
    [ -n "$vrun" ] && { rm -rf "$vdir"; GH_DOWN_FILE=""; GH_RETRY_LOG=""; }
    _ver_mark_ok "${ids[@]}"
    return 0
}

# ver_fill_refs <id> — добрать версии тех веток, которые ещё не спрашивали.
# Нужна экрану выбора ветки: там показываются ВСЕ ветки, а при запуске
# опрашиваются только первые REFS_MAX — остальные без этого были бы вечно
# «не спрошены», и выбирать пришлось бы вслепую.
ver_fill_refs() {
    local id="$1" ref cfg need=0
    [ -z "${GOINST_NO_NET:-}" ] || return 0
    [ -n "$TOKEN" ] || return 0
    command -v curl >/dev/null 2>&1 || return 0
    mkdir -p "$VER_CACHE" 2>/dev/null || return 0
    for ref in $(prod_refs "$id"); do
        ver_cache_read "$id" "$ref" >/dev/null || need=1
    done
    [ "$need" -eq 1 ] || return 0
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-vercurl.XXXXXX") || return 0
    chmod 600 "$cfg"
    {
        echo 'silent'; echo 'location'; echo 'fail'
        gh_cfg_net
        printf 'header = "Authorization: token %s"\n' "$TOKEN"
    } > "$cfg"
    local fdir="" fown=""
    if [ -z "$GH_DOWN_FILE" ] && fdir=$(mktemp -d "${TMPDIR:-/tmp}/goinst-frun.XXXXXX"); then GH_DOWN_FILE="$fdir/down"; fown=1; fi
    for ref in $(prod_refs "$id"); do
        ver_cache_read "$id" "$ref" >/dev/null && continue
        _ver_fetch_one "$id" "$ref" "$cfg" &
    done
    wait
    rm -f "$cfg"
    [ -n "$fown" ] && { rm -rf "$fdir"; GH_DOWN_FILE=""; }
    return 0
}

# Забыть опрошенное: пункт «обновить сведения» обязан спрашивать заново, а
# не показывать то же самое из кеша.
versions_forget() { rm -f "${VER_CACHE}"/*.* 2>/dev/null || true; VER_NET_TRIED="no"; VER_NET_OK="no"; }

# ver_newer <a> <b> — rc 0, если b строго новее a. `sort -V` не путается на
# 0.9.0 против 0.10.0, чего не умеет ни строковое сравнение, ни разбор по
# точкам «на глаз».
ver_newer() {
    local a="$1" b="$2" top
    [ -n "$b" ] || return 1
    [ -n "$a" ] || return 0
    [ "$a" = "$b" ] && return 1
    top=$(printf '%s\n%s\n' "$a" "$b" | sort -V 2>/dev/null | tail -1) || return 1
    [ "$top" = "$b" ]
}

# Лучшее, что нашли по веткам: печатает "ветка версия". При равных версиях
# на разных ветках побеждает первая в списке — он идёт от стабильной к
# отладочной.
prod_best_remote() {
    local id="$1" ref v best_v="" best_ref=""
    for ref in $(prod_refs "$id"); do
        v=$(ver_cache_get "$id" "$ref") || continue
        [ -n "$v" ] || continue
        if [ -z "$best_v" ] || ver_newer "$best_v" "$v"; then best_v="$v"; best_ref="$ref"; fi
    done
    [ -n "$best_v" ] || return 1
    printf '%s %s' "$best_ref" "$best_v"
}

# ── своя версия и обновление самого себя ────────────────────────────────
# goInstaller живёт в таком же закрытом репозитории, с теми же ветками и тем
# же ключом, что и продукты. Значит и версию свою он обязан узнавать так же
# — из ветки, а не из того, что записано в нём самом.
SELF_ID="@self"

self_refs() {
    local cached
    if [ -n "${GOINST_SELF_REFS:-}" ]; then printf '%s' "$GOINST_SELF_REFS"; return 0; fi
    if cached=$(ver_cache_get "$SELF_ID" "@refs") && [ -n "$cached" ]; then printf '%s' "$cached"; return 0; fi
    printf '%s' "$GOINST_SELF_REF"
}

self_fetch_versions() {
    local cfg ref need=0
    [ -z "${GOINST_NO_NET:-}" ] || return 0
    [ -n "$TOKEN" ] || return 0
    command -v curl >/dev/null 2>&1 || return 0
    gh_limited >/dev/null && return 0
    mkdir -p "$VER_CACHE" 2>/dev/null || return 0
    ver_cache_read "$SELF_ID" "@refs" >/dev/null || need=1
    for ref in $(self_refs); do ver_cache_read "$SELF_ID" "$ref" >/dev/null || need=1; done
    [ "$need" -eq 1 ] || return 0
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-self.XXXXXX") || return 0
    chmod 600 "$cfg"
    { echo 'silent'; echo 'location'; echo 'fail'; gh_cfg_net
      printf 'header = "Authorization: token %s"\n' "$TOKEN"; } > "$cfg"
    if ! ver_cache_read "$SELF_ID" "@refs" >/dev/null; then
        local names
        if [ -n "${GOINST_BRANCHES_FIXTURE:-}" ]; then
            names=$(sed -n -E "s#^${GOINST_SELF_REPO}=(.*)\$#\1#p" "$GOINST_BRANCHES_FIXTURE" 2>/dev/null | head -1 | tr ' ' '\n')
        else
            local tmpb rc=0
            names=""
            if tmpb=$(mktemp "${TMPDIR:-/tmp}/goinst-selfr.XXXXXX"); then
                gh_get "https://api.github.com/repos/${GOINST_SELF_REPO}/branches?per_page=100" "$cfg" "$tmpb" || rc=$?
                names=$(sed -n -E 's/.*"name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$tmpb" 2>/dev/null) || names=""
                rm -f "$tmpb"
            fi
            [ "$rc" -eq 2 ] && ver_cache_touch "$SELF_ID" "@refs"
        fi
        [ "${rc:-0}" -eq 2 ] || ver_cache_write "$SELF_ID" "@refs" "$(printf '%s\n' "$names" | refs_sort)"
    fi
    local tmp v at s shaf s0 v0
    for ref in $(self_refs); do
        ver_cache_read "$SELF_ID" "$ref" >/dev/null && continue
        (
            tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-selfv.XXXXXX") || exit 0
            v=""; at=""; s=""
            at="$ref"; s=$(ref_sha "$GOINST_SELF_REPO" "$ref" "$cfg") && at="$s"
            shaf="$(_ver_cache_file "$SELF_ID" "$ref").sha"; s0=""; v0=""
            if [ -n "$s" ] && [ -r "$shaf" ]; then read -r s0 v0 < "$shaf" 2>/dev/null || s0=""; fi
            if [ -n "$s" ] && [ "$s0" = "$s" ] && [ -n "$v0" ]; then
                v="$v0"
            elif gh_curl --config "$cfg" --max-time "${GOINST_VER_TIMEOUT:-$GH_MT}" -o "$tmp" \
                 "https://raw.githubusercontent.com/${GOINST_SELF_REPO}/${at}/go-installer.sh" >/dev/null 2>&1; then
                v=$(ver_extract "$tmp" "GOINST_VERSION") || v=""
                if [ -n "$s" ] && [ -n "$v" ]; then printf '%s %s\n' "$s" "$v" > "$shaf" 2>/dev/null || true; fi
            fi
            rm -f "$tmp"
            ver_cache_write "$SELF_ID" "$ref" "$v"
        ) &
    done
    wait
    rm -f "$cfg"
    return 0
}

# Лучшее, что лежит в ветках самого установщика: "ветка версия".
self_best() {
    local ref v best_v="" best_ref=""
    for ref in $(self_refs); do
        v=$(ver_cache_get "$SELF_ID" "$ref") || continue
        [ -n "$v" ] || continue
        if [ -z "$best_v" ] || ver_newer "$best_v" "$v"; then best_v="$v"; best_ref="$ref"; fi
    done
    [ -n "$best_v" ] || return 1
    printf '%s %s' "$best_ref" "$best_v"
}

# ── сводка версий для всего клуба ──────────────────────────────────────
# Таймер сводки при входе стоит на сотнях серверов клуба, и каждый опрашивал
# бы шесть репозиториев сам — два десятка запросов в полчаса с сервера на
# ОДНОМ ключе. Условные запросы (304) основной лимит не тратят, но у GitHub
# есть и вторичный — на частоту, — и его они не снимают. Поэтому сводку
# собирает GitHub Actions (.github/workflows/versions.yml): раз в час
# `go-installer.sh --digest` опрашивает всё тем же кодом, что и сервер, и
# кладёт versions.txt в ветку versions этого репозитория. Таймер берёт её
# одним условным запросом; открытое меню спрашивает само — там важна
# точность, а не цена.
#
# Формат — строки, чтобы читать без jq:
#   format=1
#   generated=<unix-время сборки>
#   <id> @refs <ветки через пробел>
#   <id> <ветка> <версия или -> <хеш коммита или ->
DIGEST_REF="${GOINST_DIGEST_REF:-versions}"
DIGEST_PATH="versions.txt"
# Actions публикует сводку при изменениях и не реже раза в 6 часов; старше
# 12 часов — сборка сломалась, и такой сводке верить нельзя.
DIGEST_MAX_AGE="${GOINST_DIGEST_MAX_AGE:-43200}"

# digest_build — собрать сводку (для Actions). Печатает её в stdout; rc 1,
# если хоть у одного продукта не видно веток: полсводки хуже старой, и
# Actions тогда не публикует ничего.
digest_build() {
    local id ref v sha rest refs bad=0
    if [ -z "${TOKEN:-}" ]; then err "$(bi "Нет ключа: сводку собрать нечем (GH_TOKEN)." "No key: nothing to build the digest with (GH_TOKEN).")"; return 1; fi
    VER_TTL=0
    versions_fetch >/dev/null
    self_fetch_versions
    printf 'format=1\ngenerated=%s\n' "$(_ver_now)"
    for id in "${PRODUCTS[@]}" "$SELF_ID"; do
        refs=$(ver_cache_get "$id" "@refs") || refs=""
        if [ -z "$refs" ]; then
            err "$(bi "${id}: веток не видно — у ключа нет доступа или GitHub не ответил." "${id}: no branches visible — the key has no access or GitHub did not answer.")"
            bad=1; continue
        fi
        printf '%s @refs %s\n' "$id" "$refs"
        for ref in $refs; do
            v=$(ver_cache_get "$id" "$ref") || v=""
            sha=""; read -r sha rest < "$(_ver_cache_file "$id" "$ref").sha" 2>/dev/null || sha=""
            printf '%s %s %s %s\n' "$id" "$ref" "${v:--}" "${sha:--}"
        done
    done
    [ "$bad" -eq 0 ]
}

# digest_apply <файл> — разложить сводку по кешу версий, как будто сервер
# спросил сам. rc 1 — формат чужой, сводка старая или неполная: тогда в кеш
# не пишется ничего. Каждое поле сверяется со строгим образцом: значения
# попадают на экран и в имена файлов кеша.
digest_apply() {
    local f="$1" l fmt="" gen="" now id ref rest v sha seen=" "
    local -a rows=()
    [ -r "$f" ] || return 1
    while IFS= read -r l || [ -n "$l" ]; do
        case "$l" in
            format=*)    fmt="${l#format=}" ;;
            generated=*) gen="${l#generated=}" ;;
            '') ;;
            *) rows+=("$l") ;;
        esac
    done < "$f"
    [ "$fmt" = 1 ] || return 1
    case "$gen" in ''|*[!0-9]*) return 1 ;; esac
    now=$(_ver_now)
    [ $(( now - gen )) -lt "$DIGEST_MAX_AGE" ] || return 1
    # Сначала проверить всё, потом писать: полсводки в кеше хуже старого кеша.
    # Незнакомый продукт пропускаем, а не отвергаем сводку: её собирает
    # goInstaller из main, и новый продукт появится в ней раньше, чем
    # обновятся серверы, — иначе все они разом ушли бы спрашивать сами.
    local -a mine=()
    for l in ${rows[@]+"${rows[@]}"}; do
        read -r id ref rest <<<"$l"
        case " ${PRODUCTS[*]} $SELF_ID " in *" $id "*) mine+=("$l") ;; *) continue ;; esac
        if [ "$ref" = "@refs" ]; then
            [[ "$rest" =~ ^[A-Za-z0-9._-]+(\ [A-Za-z0-9._-]+)*$ ]] || return 1
            seen+="$id "
        else
            [[ "$ref" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
            read -r v sha <<<"$rest"
            [[ "$v" =~ ^(-|[0-9][A-Za-z0-9._+-]*)$ ]] || return 1
            [[ "$sha" =~ ^(-|[0-9a-f]{40})$ ]] || return 1
        fi
    done
    for id in "${PRODUCTS[@]}" "$SELF_ID"; do
        case "$seen" in *" $id "*) ;; *) return 1 ;; esac
    done
    mkdir -p "$VER_CACHE" 2>/dev/null || return 1
    for l in "${mine[@]}"; do
        read -r id ref rest <<<"$l"
        if [ "$ref" = "@refs" ]; then ver_cache_write "$id" "@refs" "$rest"; continue; fi
        read -r v sha <<<"$rest"
        [ "$v" = - ] && v=""
        ver_cache_write "$id" "$ref" "$v"
        # Хеш с версией — тот же кеш, что у прямого опроса: следующий прямой
        # вопрос по этому коммиту файл из ветки уже не качает.
        if [ "$sha" != - ] && [ -n "$v" ]; then printf '%s %s\n' "$sha" "$v" > "$(_ver_cache_file "$id" "$ref").sha" 2>/dev/null || true; fi
    done
    return 0
}

# digest_fetch → 0 — кеш версий обновлён из сводки; 2 — лимит исчерпан;
# 1 — сводки нет или ей нельзя верить (зовущий спрашивает сам). Запрос
# условный: пока сводка та же, GitHub отвечает 304 и в лимит его не пишет.
digest_fetch() {
    local cfg out rc=0
    [ -z "${GOINST_NO_NET:-}" ] || return 1
    [ -n "${TOKEN:-}" ] || return 1
    command -v curl >/dev/null 2>&1 || return 1
    mkdir -p "$VER_CACHE" 2>/dev/null || return 1
    chmod 700 "$VER_CACHE" 2>/dev/null || true
    cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-dg.XXXXXX") || return 1
    chmod 600 "$cfg"
    { echo 'silent'; echo 'location'; gh_cfg_net
      printf 'header = "Authorization: token %s"\n' "$TOKEN"; } > "$cfg"
    out=$(mktemp "${TMPDIR:-/tmp}/goinst-dgo.XXXXXX") || { rm -f "$cfg"; return 1; }
    gh_get "https://api.github.com/repos/${GOINST_SELF_REPO}/contents/${DIGEST_PATH}?ref=${DIGEST_REF}" \
        "$cfg" "$out" "application/vnd.github.raw" || rc=$?
    rm -f "$cfg"
    if [ "$rc" -eq 0 ] && ! digest_apply "$out"; then rc=1; fi
    rm -f "$out"
    return "$rc"
}

# Подменяем себя атомарно: скачали, проверили, положили рядом, переставили.
# Обрыв посреди copy оставил бы оператора без работающей команды.
self_update() {
    local ref="${1:-}" rv v tmp dest="$GOINST_BIN"
    true
    self_fetch_versions
    if [ -z "$ref" ]; then
        if rv=$(self_best); then ref="${rv%% *}"; else ref="$GOINST_SELF_REF"; fi
    fi
    v=$(ver_cache_get "$SELF_ID" "$ref") || v=""
    say "$(bi "Обновляю goInstaller из ветки" "Updating goInstaller from branch") ${ref}${v:+ (${v})}…"
    tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-new.XXXXXX") || return 1
    if ! fetch_raw "$GOINST_SELF_REPO" "$ref" "go-installer.sh" "$tmp"; then rm -f "$tmp"; return 1; fi
    if ! bash -n "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        err "$(bi "Скачанный файл не разбирается как скрипт — обновление отменено." "The downloaded file does not parse as a script — the update is cancelled.")"
        return 1
    fi
    cp -f "$tmp" "${dest}.new" 2>/dev/null || { rm -f "$tmp"; err "$(bi "Не удалось записать рядом с ${dest}." "Could not write next to ${dest}.")"; return 1; }
    chmod 755 "${dest}.new"
    mv -f "${dest}.new" "$dest" || { rm -f "$tmp" "${dest}.new"; err "$(bi "Не удалось заменить ${dest}." "Could not replace ${dest}.")"; return 1; }
    ln -sf "$dest" "$GOINST_BIN_ALIAS" 2>/dev/null || true
    rm -f "$tmp"
    ok "$(bi "Обновлено. Запустите goinstall заново." "Updated. Start goinstall again.")"
    return 0
}

# ── самоустановка ───────────────────────────────────────────────────────
# Чтобы после первого запуска была команда go-installer. При `bash <(curl…)`
# $0 — не файл; тогда качаем свою копию из репозитория (ключ уже есть).
self_install() {
    [ -n "${GOINST_NO_SELF_INSTALL:-}" ] && return 0
    if [ -x "$GOINST_BIN" ] && grep -q "GOINST_VERSION=\"${GOINST_VERSION}\"" "$GOINST_BIN" 2>/dev/null; then return 0; fi
    if [ -f "$0" ] && looks_like_script "$0"; then
        if cp -f "$0" "$GOINST_BIN" 2>/dev/null && chmod 755 "$GOINST_BIN"; then
            ln -sf "$GOINST_BIN" "$GOINST_BIN_ALIAS" 2>/dev/null || true
            ok "$(bi "Команда goinstall установлена." "The goinstall command is installed.")"
        fi
        return 0
    fi
    [ -n "$TOKEN" ] || return 0
    local tmp; tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-self.XXXXXX") || return 0
    if fetch_raw "$GOINST_SELF_REPO" "$GOINST_SELF_REF" "go-installer.sh" "$tmp" 2>/dev/null; then
        if cp -f "$tmp" "$GOINST_BIN" && chmod 755 "$GOINST_BIN"; then
            ln -sf "$GOINST_BIN" "$GOINST_BIN_ALIAS" 2>/dev/null || true
            ok "$(bi "Команда goinstall установлена." "The goinstall command is installed.")"
        fi
    fi
    rm -f "$tmp"
    return 0
}

# ── меню: один набор пунктов, два способа его показать ──────────────────
# Пункты объявляются ОДИН раз в MENU_ITEMS, рисуются либо стрелочным
# экраном, либо нумерованным списком. Второго набора пунктов нет нигде:
# разойдясь, они дали бы пункт, который виден в одном режиме и недоступен
# в другом.
#
# Строка пункта: "ключ|имя|цвет:статус|цвет:доступно|подпись".
# Ключ "-" — разделитель (пропускается при ходьбе стрелками).
# Цвет: g зелёный, y жёлтый, r красный, d приглушённый.
MENU_TITLE=""; MENU_SUB=""; MENU_ITEMS=(); PICK_KEY=""
MENU_ROW=(); MENU_KEY=()

# Длина в СИМВОЛАХ. `${#s}` считает символы только при UTF-8-локали, а на
# сервере часто LANG=C — там кириллица в колонке разъезжается ровно вдвое.
# Продолжающие байты UTF-8 (10xxxxxx) символа не начинают: вычитаем их.
str_len() {
    local s="$1"
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
        *[Uu][Tt][Ff]*) printf '%s' "${#s}"; return 0 ;;
    esac
    s="${s//[$'\x80'-$'\xbf']/}"
    printf '%s' "${#s}"
}

# str_cut <строка> <сколько символов> — обрезать, не порвав символ.
# Одна реализация на обе локали: в UTF-8 `${s:i:1}` возвращает символ и
# внутренний цикл просто не срабатывает, в C-локали — байт, и цикл
# дочитывает продолжения. Отдельной ветки «в C не режем» быть не должно:
# LANG под sudo и cron часто не задан вовсе, и такая ветка означала бы, что
# колонки разъезжаются именно там, где никто не смотрит.
str_cut() {
    local s="$1" w="$2" out="" i=0 n=0 len=${#s}
    while [ "$i" -lt "$len" ] && [ "$n" -lt "$w" ]; do
        out+="${s:i:1}"; i=$((i+1))
        while [ "$i" -lt "$len" ]; do
            case "${s:i:1}" in
                [$'\x80'-$'\xbf']) out+="${s:i:1}"; i=$((i+1)) ;;
                *) break ;;
            esac
        done
        n=$((n+1))
    done
    printf '%s' "$out"
}

# cut_to <строка> <ширина> — обрезать до ширины, отметив обрез многоточием.
cut_to() {
    local s="$1" w="$2"
    if [ "$(str_len "$s")" -gt "$w" ]; then printf '%s…' "$(str_cut "$s" "$((w-1))")"
    else printf '%s' "$s"; fi
}

# pad_to <строка> <ширина> — дополнить пробелами до ширины в символах.
# Слишком длинное обрезаем: одно длинное имя иначе сдвигает всю строку.
pad_to() {
    local s="$1" w="$2" n
    n=$(str_len "$s")
    if [ "$n" -gt "$w" ]; then cut_to "$s" "$w"; return 0; fi
    printf '%s%*s' "$s" "$((w-n))" ''
}

_st_color() {
    case "$1" in
        g) printf '%s' "$GREEN" ;; y) printf '%s' "$YELLOW" ;;
        r) printf '%s' "$RED" ;;  *) printf '%s' "$DIM" ;;
    esac
}

# Строки готовим ЗАРАНЕЕ: перерисовка по нажатию клавиши должна быть чистым
# printf. Сборка строки дёргает pad_to и _st_color, и делать это на каждое
# нажатие — тот самый «список, который перезагружается».
menu_build_rows() {
    MENU_ROW=(); MENU_KEY=()
    local it key name st extra sub stc sttext exc extext
    local w_name="${MENU_W_NAME:-16}" w_st="${MENU_W_STATUS:-22}" w_ex="${MENU_W_EXTRA:-0}"
    for it in "${MENU_ITEMS[@]}"; do
        IFS='|' read -r key name st extra sub <<<"$it"
        if [ "$key" = "-" ]; then MENU_ROW+=(""); MENU_KEY+=("-"); continue; fi
        stc="${st%%:*}"; sttext="${st#*:}"
        case "$stc" in g|y|r|d) ;; *) stc="d"; sttext="$st" ;; esac
        exc="${extra%%:*}"; extext="${extra#*:}"
        case "$exc" in g|y|r|d) ;; *) exc="d"; extext="$extra" ;; esac
        # Подпись обрезаем по ширине окна: перенос длинной строки сбил бы
        # раскладку экрана — на каждый пункт стало бы по две строки.
        case "${MENU_W_SUB:-}" in
            ''|*[!0-9]*) ;;
            0) sub="" ;;
            *) sub=$(cut_to "$sub" "$MENU_W_SUB") ;;
        esac
        if [ "$w_ex" -gt 0 ]; then
            MENU_ROW+=("$(printf '%s  %s%s%s  %s%s%s  %s%s%s' \
                "$(pad_to "$name" "$w_name")" \
                "$(_st_color "$stc")" "$(pad_to "$sttext" "$w_st")" "$NC" \
                "$(_st_color "$exc")" "$(pad_to "$extext" "$w_ex")" "$NC" \
                "$DIM" "$sub" "$NC")")
        else
            MENU_ROW+=("$(printf '%s  %s%s%s  %s%s%s' \
                "$(pad_to "$name" "$w_name")" \
                "$(_st_color "$stc")" "$(pad_to "$sttext" "$w_st")" "$NC" \
                "$DIM" "$sub" "$NC")")
        fi
        MENU_KEY+=("$key")
    done
}

# Стрелочный экран годится не всегда: под `curl | bash` stdin занят, в
# тестах терминала нет вовсе, а в коротком окне перерисовка от \033[H
# дерётся с прокруткой. Во всех этих случаях — нумерованный список.
menu_is_interactive() {
    [ -z "${GOINST_PLAIN_MENU:-}" ] || return 1
    [ -t 0 ] && [ -t 1 ] || return 1
    case "${TERM:-dumb}" in dumb|'') return 1 ;; esac
    local lines need
    lines=$(tput lines 2>/dev/null) || lines=""
    case "$lines" in ''|*[!0-9]*) lines=24 ;; esac
    need=$(( ${#MENU_ITEMS[@]} + 8 ))
    [ "$lines" -ge "$need" ]
}

_menu_next() {  # _menu_next <текущий> <шаг> — ход с пропуском разделителей
    local cur="$1" step="$2" n="${#MENU_KEY[@]}" i=0
    while [ "$i" -lt "$n" ]; do
        cur=$(( (cur + step + n) % n ))
        [ "${MENU_KEY[$cur]}" = "-" ] || { printf '%s' "$cur"; return 0; }
        i=$((i+1))
    done
    printf '%s' "$1"
}

_menu_paint() {
    local cur="$1" i n
    printf '\033[H'
    printf '  %b\033[K\n' "${CYAN}${BOLD}${MENU_TITLE}${NC}"
    [ -n "$MENU_SUB" ] && printf '  %b\033[K\n' "${DIM}${MENU_SUB}${NC}"
    printf '  %b\033[K\n' "${DIM}$(printf '─%.0s' {1..60})${NC}"
    n=${#MENU_ROW[@]}
    for ((i=0; i<n; i++)); do
        if [ "${MENU_KEY[$i]}" = "-" ]; then printf '\033[K\n'; continue; fi
        if [ "$i" -eq "$cur" ]; then
            printf '  %b%b\033[K\n' "${CYAN}▸ ${NC}${BOLD}" "${MENU_ROW[$i]}${NC}"
        else
            printf '    %b\033[K\n' "${MENU_ROW[$i]}"
        fi
    done
    printf '\033[K\n'
    if [ -n "${MENU_FOOTER_INFO:-}" ]; then printf '  %b\033[K\n' "${DIM}${MENU_FOOTER_INFO}${NC}"; fi
    printf '  %b\033[K\n' "${DIM}$(bi "↑↓ выбрать · Enter открыть · цифра сразу · q выход" "↑↓ move · Enter open · digit jumps · q quit")${NC}"
    printf '\033[J'
}

# Нажатие кладём в KEY, без подстановки команд: подстановка — это ещё один
# процесс на КАЖДОЕ нажатие.
KEY=""
_menu_read_key() {
    local k rest="" rc t="${1:-}"
    KEY=""
    if [ -n "$t" ]; then IFS= read -rsn1 -t "$t" k; rc=$?
    else IFS= read -rsn1 k; rc=$?; fi
    if [ "$rc" -ne 0 ]; then
        # Код больше 128 — вышло время ожидания, а не конец ввода. Спутать
        # их значит выходить из меню каждые три минуты.
        if [ "$rc" -gt 128 ]; then KEY="tick"; else KEY="quit"; fi   # Ctrl-D — выход
        return 0
    fi
    case "$k" in
        '')  KEY="enter"; return 0 ;;                        # Enter — открыть
        q|Q) KEY="quit";  return 0 ;;
        $'\033')
            IFS= read -rsn2 -t 0.2 rest 2>/dev/null || rest=""
            case "$rest" in
                '[A') KEY="up" ;;   '[B') KEY="down" ;;
                '[C') KEY="enter" ;; '[D') KEY="quit" ;;
                '')   KEY="quit" ;;                          # голый Esc
                *)    KEY="other" ;;
            esac
            return 0 ;;
        *) KEY="$k"; return 0 ;;
    esac
}

_menu_restore() { printf '\033[?25h\n'; }

# pick_menu → PICK_KEY, rc 0 выбрано / 1 отменено.
pick_menu() {
    PICK_KEY=""
    menu_build_rows
    menu_is_interactive || { _pick_menu_plain; return $?; }
    local cur n i left refresh="${MENU_REFRESH_SEC:-0}" next_at=0
    n=${#MENU_KEY[@]}
    [ "$n" -gt 0 ] || return 1
    cur=0; [ "${MENU_KEY[0]}" = "-" ] && cur=$(_menu_next 0 1)
    # Ctrl-C не должен оставить терминал без курсора. EXIT не трогаем:
    # ловушка на нём — общая для скрипта, и своя её бы затёрла.
    trap '_menu_restore; exit 130' INT
    case "$refresh" in ''|*[!0-9]*) refresh=0 ;; esac
    [ "$refresh" -gt 0 ] && next_at=$(( SECONDS + refresh ))
    printf '\033[?25l'; clear
    while true; do
        _menu_paint "$cur"
        # Отсчёт от SECONDS, а не «тайм-аут на каждое чтение»: иначе любое
        # нажатие клавиши отодвигало бы обновление ещё на три минуты.
        if [ "$refresh" -gt 0 ]; then
            left=$(( next_at - SECONDS )); [ "$left" -ge 1 ] || left=1
            _menu_read_key "$left"
        else
            _menu_read_key
        fi
        case "$KEY" in
            tick)
                # Обновление на месте: без clear и без выхода из меню, курсор
                # остаётся где был. Иначе раз в три минуты человека
                # выбрасывало бы наверх списка посреди чтения.
                if [ -n "${MENU_REFRESH_HOOK:-}" ]; then "$MENU_REFRESH_HOOK" >/dev/null 2>&1 || true; fi
                menu_build_rows
                n=${#MENU_KEY[@]}
                [ "$n" -gt 0 ] || { PICK_KEY=""; break; }
                [ "$cur" -lt "$n" ] || cur=0
                [ "${MENU_KEY[$cur]}" = "-" ] && cur=$(_menu_next "$cur" 1)
                next_at=$(( SECONDS + refresh )) ;;
            up)    cur=$(_menu_next "$cur" -1) ;;
            down)  cur=$(_menu_next "$cur" 1) ;;
            enter) PICK_KEY="${MENU_KEY[$cur]}"; break ;;
            quit)  PICK_KEY=""; break ;;
            other) ;;
            *)
                for ((i=0; i<n; i++)); do
                    if [ "${MENU_KEY[$i]}" = "$KEY" ]; then PICK_KEY="$KEY"; break 2; fi
                done ;;
        esac
    done
    trap - INT
    printf '\033[?25h'; clear
    [ -n "$PICK_KEY" ] || return 1
    return 0
}

_pick_menu_plain() {
    local i n c
    while true; do
        print_header "$MENU_TITLE"
        [ -n "$MENU_SUB" ] && echo -e "  ${DIM}${MENU_SUB}${NC}" >&2
        echo "" >&2
        n=${#MENU_ROW[@]}
        for ((i=0; i<n; i++)); do
            if [ "${MENU_KEY[$i]}" = "-" ]; then echo "" >&2; continue; fi
            printf "  ${CYAN}%s)${NC} %b\n" "${MENU_KEY[$i]}" "${MENU_ROW[$i]}" >&2
        done
        echo "" >&2
        read_line c || { PICK_KEY=""; return 1; }
        n=${#MENU_KEY[@]}
        for ((i=0; i<n; i++)); do
            if [ "${MENU_KEY[$i]}" = "$c" ]; then PICK_KEY="$c"; return 0; fi
        done
        warn "$(bi "Неверный ввод." "Invalid input.")"; sleep 1
    done
}

# ── готовность продукта к установке ─────────────────────────────────────
# Считается тем же preflight, тихо: второй набор правил рядом с первым
# разошёлся бы с ним — и меню обещало бы «готов» там, где проверка
# отказывает.
_pf_first_blocker() {
    local f1 f2 f3 f4 who
    while IFS='|' read -r f1 f2 f3 f4 _; do
        [ -n "$f1" ] || continue
        if [ "$f1" = "os" ] && [ "$f2" = "must" ]; then
            printf '%s' "$(bi "система не подходит" "unsupported OS")"; return 0
        fi
        if [ "$f1" = "rule" ] && [ "$f2" = "must" ]; then
            printf '%s' "$(bi "конфликт с соседом" "neighbour conflict")"; return 0
        fi
        if [ "$f2" = "busy" ] && [ "$f3" = "must" ]; then
            case "$f4" in
                system) who=$(bi "система" "system") ;;
                nginx)  who="nginx" ;;
                *)      who=$(prod_name "$f4") ;;
            esac
            printf '%s %s: %s' "${f1%%/*}" "$(bi "занят" "busy")" "$who"; return 0
        fi
    done <<<"$PF_LINES"
    printf '%s' "$(bi "ставить нельзя" "cannot install")"
}

prod_readiness() {
    local id="$1" rc m first="" i=0 modes=()
    read -r -a modes <<<"$(prod_modes "$id")"
    [ "${#modes[@]}" -gt 0 ] || modes=(pro)
    for m in "${modes[@]}"; do
        preflight "$id" "$m" >/dev/null 2>&1; rc=$?
        if [ "$i" -eq 0 ]; then
            case "$rc" in
                0) printf 'g:%s' "$(bi "готов" "ready")"; return 0 ;;
                2)
                    # Замечание, которое меняет суть, — своим словом, а не
                    # общим «есть замечания».
                    if grep -q '^rule|soft|godns-beside-443|' <<<"$PF_LINES"; then printf 'y:%s' "$(bi "не на 443" "not on 443")"; return 0; fi
                    printf 'y:%s' "$(bi "готов, есть замечания" "ready, with remarks")"; return 0 ;;
            esac
            # Причину берём сразу: следующий вызов preflight перепишет PF_LINES.
            first=$(_pf_first_blocker)
        elif [ "$rc" -ne 1 ]; then
            # Скромный режим проходит там, где требовательный нет. Сказать об
            # этом в строке меню важнее, чем назвать препятствие: человек
            # иначе считает продукт вовсе неустановимым.
            printf 'y:%s %s' "$(bi "только" "only")" "$(mode_name "$m")"; return 0
        fi
        i=$((i+1))
    done
    printf 'r:%s' "$first"
}

# ── сценарии ────────────────────────────────────────────────────────────
ask_mode() {
    local id="$1" c i=0 m def defidx=1 modes=() keep=()
    read -r -a modes <<<"$(prod_mode_choices "$id")"
    [ "${#modes[@]}" -gt 0 ] || { MODE=pro; return 0; }
    # Ленивый pro — сценарий ПЕРВОЙ установки: на стоящем продукте его место
    # занимает пункт «Режим и сайт» в меню самого goAmnezia pro, а флаг
    # --lazy там ничего не делает.
    if prod_installed "$id"; then
        for m in "${modes[@]}"; do [ "$m" = lazy ] || keep+=("$m"); done
        modes=(${keep[@]+"${keep[@]}"})
    fi
    def=$(prod_mode_default "$id")
    # Второй вопрос о режиме задаст мастер продукта — но не Ленивому pro: тот
    # уходит в установщик флагом и сразу спрашивает домен.
    local again
    case " ${modes[*]} " in
        *" lazy "*) again=$(bi "Установщик продукта спросит режим ещё раз — кроме Ленивого pro: он запускается сразу." "The product installer asks the mode again — except Lazy pro, which starts straight away.") ;;
        *)          again=$(bi "Установщик продукта спросит ещё раз." "The product installer asks again.") ;;
    esac
    echo ""
    echo -e "  $(bi "Какой режим планируете? Здесь это нужно, чтобы проверить правильный набор портов." "Which mode do you plan? Here it selects the right port set to check.") ${again}"
    for m in "${modes[@]}"; do
        i=$((i+1))
        [ "$m" = "$def" ] && defidx=$i
        echo -e "  ${CYAN}${i})${NC} $(mode_name "$m") ${DIM}— $(mode_desc "$id" "$m")${NC}"
    done
    # Что значит Enter, сказано вслух: у goAmnezia pro это Lite, как у его
    # мастера, у остальных — первый пункт.
    read_line c "  $(bi "Выбор" "Choice") (Enter — ${defidx}, $(mode_name "${modes[$((defidx-1))]}")): " || return 1
    [ -z "$c" ] && c=$defidx
    # «1a» раньше доходил до `[ -ge ]` и сыпал в экран «integer expression expected».
    case "$c" in
        *[!0-9]*) warn "$(bi "Неверный ввод." "Invalid input.")"; return 1 ;;
    esac
    if [ "$c" -lt 1 ] || [ "$c" -gt "${#modes[@]}" ]; then
        warn "$(bi "Неверный ввод." "Invalid input.")"; return 1
    fi
    MODE="${modes[$((c-1))]}"
    return 0
}

# ask_ref <id> → REF. Ветку выбирает человек, а не умолчание в коде: на
# стабильной и отладочной лежат разные версии, и разница видна только здесь.
REF=""
ask_ref() {
    local id="$1" refs=() i=0 r c v inst def="" kind mark defidx=1
    read -r -a refs <<<"$(prod_refs "$id")"
    if [ "${#refs[@]}" -eq 0 ]; then REF=$(prod_ref "$id"); return 0; fi
    # Одна ветка — выбирать не из чего, но сказать какая всё равно надо.
    if [ "${#refs[@]}" -eq 1 ]; then
        REF="${refs[0]}"
        v=$(ver_cache_get "$id" "$REF") || v=""
        say "$(bi "Ветка" "Branch"): ${BOLD}${REF}${NC}${v:+ ${v}} $(kind=$(ref_kind "$REF"); [ -n "$kind" ] && printf '(%s)' "$kind") — $(bi "другой у этого продукта нет" "the product has no other")"
        return 0
    fi

    inst=$(prod_version "$id"); prod_installed "$id" || inst=""
    def=$(prod_installed_ref "$id") || def=""
    # Показываем ВСЕ ветки, а версии спрошены не у всех: добираем
    # недостающие одним заходом, чтобы в списке не было пустых строк.
    ver_fill_refs "$id"
    # Умолчание: ветка, с которой стоит, иначе первая в списке (стабильная).
    if [ -n "$def" ]; then
        local found=""
        for ((i=0; i<${#refs[@]}; i++)); do [ "${refs[$i]}" = "$def" ] && { defidx=$((i+1)); found=1; }; done
        # Стоит с ветки, которой в списке нет, — умолчанием её не называем.
        [ -n "$found" ] || def=""
    fi

    echo ""
    echo -e "  $(bi "С какой ветки ставить?" "Which branch to install from?")"
    i=0
    for r in "${refs[@]}"; do
        i=$((i+1))
        v=$(ver_cache_get "$id" "$r") || v=""
        kind=$(ref_kind "$r")
        mark=""
        if [ -n "$v" ] && [ -n "$inst" ] && ver_newer "$inst" "$v"; then
            mark=" ${GREEN}$(bi "новее установленной" "newer than installed") ${inst}${NC}"
        elif [ -n "$v" ] && [ -n "$inst" ] && [ "$v" = "$inst" ]; then
            mark=" ${DIM}$(bi "та же, что стоит" "same as installed")${NC}"
        fi
        # Версия не спрошена — так и сказать. Пустое место читается как
        # «ветки нет», а это разные вещи.
        if [ -z "$v" ]; then
            if [ "$VER_NET_OK" = "yes" ]; then v="${DIM}$(bi "не ответила" "no answer")${NC}"
            else v="${DIM}$(bi "версия не спрошена" "version not fetched")${NC}"; fi
        fi
        printf "  ${CYAN}%d)${NC} %-6s ${DIM}%s${NC}  %b%b\n" "$i" "$r" "$kind" "$v" "$mark"
    done
    [ -n "$def" ] && echo -e "  ${DIM}$(bi "Enter — оставить ту, с которой стоит" "Enter keeps the branch it is installed from"): ${refs[$((defidx-1))]}${NC}"
    read_line c || return 1
    [ -z "$c" ] && c="$defidx"
    case "$c" in
        [0-9]*) [ "$c" -ge 1 ] && [ "$c" -le "${#refs[@]}" ] || { warn "$(bi "Неверный ввод." "Invalid input.")"; return 1; }
                REF="${refs[$((c-1))]}" ;;
        *) warn "$(bi "Неверный ввод." "Invalid input.")"; return 1 ;;
    esac
    return 0
}

MODE=pro
# mode_resolve <id> — определить режим, не спрашивая, если он уже записан
# продуктом. Возвращает 1, если спросить всё-таки надо.
mode_resolve() {
    local id="$1" m
    prod_has_modes "$id" || { MODE=pro; return 0; }
    [ -z "${GOINST_ASK_MODE:-}" ] || return 1
    prod_installed "$id" || return 1
    m=$(prod_installed_mode "$id") || return 1
    MODE="$m"
    say "$(bi "Режим установленного" "Installed mode"): ${BOLD}$(mode_name "$m")${NC} — $(bi "взят из настроек продукта, спрашивать не буду" "taken from the product's own settings, not asking")"
    return 0
}

# Что будет с бесплатной версией, если поставить pro поверх. Говорится ДО
# вопросов: человек должен знать, что переживёт установку и что сменится.
free_takeover_note() {
    prod_installed "$1" && prod_free_edition "$1" >/dev/null || return 0
    echo "" >&2
    case "$1" in
    govless)
    say "$(bi "На сервере бесплатный Lite из инструкции. $(prod_name "$1") встанет поверх него: панель 3X-UI, ключи и пользователи останутся, заменится только код в /opt/govless-installer." "This server runs the free Lite from the guide. $(prod_name "$1") installs over it: the 3X-UI panel, keys and users stay, only the code in /opt/govless-installer is replaced.")"
    say "$(bi "Потом откроется меню $(prod_name "$1") в прежнем режиме Lite. Режим Pro со своим доменом и TLS включается там же: раздел VPN, смена режима." "Then the $(prod_name "$1") menu opens in the same Lite mode. Pro mode with your own domain and TLS is switched on right there: the VPN section, mode switch.")"
    ;;
    gokaskad)
    # Правила бесплатной версии идут без меток «kaskad:», и pro до 2.12 их не
    # видел: они работали мимо меню, а правило pro на том же порту вставало
    # за старым DNAT и не срабатывало. С 2.12 pro берёт их себе при запуске.
    say "$(bi "На сервере бесплатный Kaskad из инструкции: команда gokaskad сейчас открывает его. $(prod_name "$1") заменит её своей, а правила каскада бесплатной версии возьмёт себе: они продолжат работать и появятся в его меню." "This server runs the free Kaskad from the guide: the gokaskad command opens it now. $(prod_name "$1") replaces it with its own and takes over the free cascade rules: they keep working and show up in its menu.")"
    say "$(bi "Команду установки из бесплатной инструкции больше не запускайте: она вернёт бесплатную версию на место pro." "Do not run the free guide's install command again: it puts the free edition back in place of pro.")"
    ;;
    gotelegram)
    say "$(bi "Команда gotelegram сейчас открывает бесплатный MTProxy из инструкции: его запустили уже после $(prod_name "$1"), и он подменил её собой. Установщик pro вернёт свою команду, прокси pro и его настройки останутся." "The gotelegram command now opens the free MTProxy from the guide: it was run after $(prod_name "$1") and replaced the command. The pro installer restores its own command; the pro proxy and its settings stay.")"
    ;;
    esac
}

install_flow() {
    local id="$1" rc
    free_takeover_note "$id"
    if ! mode_resolve "$id"; then ask_mode "$id" || return 0; fi
    preflight "$id" "$MODE"; rc=$?
    if [ "$rc" -eq 1 ]; then
        echo ""
        if [ -n "${GOINST_FORCE:-}" ]; then
            warn "$(bi "GOINST_FORCE задан — продолжаю несмотря на препятствия." "GOINST_FORCE is set — continuing despite blockers.")"
        else
            say "$(bi "Карта портов: пункт «p» в главном меню." "Port map: item 'p' in the main menu.")"
            echo -ne "  $(bi "Нажмите Enter, чтобы вернуться" "Press Enter to return") "; read -r || true
            return 0
        fi
    fi
    ask_ref "$id" || return 0
    echo ""
    local vsel; vsel=$(ver_cache_get "$id" "$REF") || vsel=""
    # Без цветов: приглашение печатает `read -p`, а он escape-последовательности
    # НЕ разворачивает — в проекте это литеральные строки для `echo -e`, и
    # человек увидел бы «\033[1mmain\033[0m» вместо жирного «main».
    local free; free=$(prod_installed "$id" && prod_free_edition "$id") || free=""
    if [ -n "$free" ]; then
        confirm "$(bi "Поставить" "Install") $(prod_name "$id") $(bi "поверх" "over") ${free} — $(bi "ветка" "branch") ${REF}${vsel:+, ${vsel}}?" y || return 0
    else
        confirm "$(bi "Запустить установщик" "Launch the installer") $(prod_name "$id") — $(bi "ветка" "branch") ${REF}${vsel:+, ${vsel}}?" y || return 0
    fi
    run_product_installer "$id"; rc=$?
    echo ""
    if [ "$rc" -eq 0 ]; then ok "$(bi "Установщик $(prod_name "$id") завершился." "The $(prod_name "$id") installer has finished.")"
    else warn "$(bi "Установщик $(prod_name "$id") вышел с кодом ${rc}." "The $(prod_name "$id") installer exited with code ${rc}.")"; fi
    installer_postcheck "$id" "$REF" || true
    echo -ne "  $(bi "Нажмите Enter, чтобы вернуться" "Press Enter to return") "; read -r || true
    return 0
}

# Короткий статус для `--list`: там колонок нет, только версия или прочерк.
status_line() {
    local id="$1" v free
    if prod_installed "$id"; then
        v=$(prod_version "$id")
        if free=$(prod_free_edition "$id"); then
            prod_free_shows_version "$id" || v=""
            printf "${YELLOW}[%s%s]${NC}" "$(bi "бесплатный" "free") ${free}" "${v:+ $v}"
            return 0
        fi
        printf "${GREEN}[%s%s]${NC}" "$(bi "установлен" "installed")" "${v:+ $v}"
    else
        printf "${DIM}[—]${NC}"
    fi
}

# Пункты подменю продукта. Отдельной функцией, потому что их читает не
# только экран: страж проверяет, что у каждого ключа есть ветка в
# обработчике, а мёртвый пункт и дубль номера — ошибки, которые в меню
# уже случались.
menu_items_product() {
    local id="$1" v st avail ref rv free name
    # Подменю продукта не обновляется само: там человек что-то выбирает, и
    # перерисовка под рукой только мешала бы.
    MENU_REFRESH_SEC=0; MENU_REFRESH_HOOK=""; MENU_FOOTER_INFO=""
    MENU_TITLE="$(prod_name "$id")"
    MENU_SUB="$(prod_desc "$id")"
    MENU_ITEMS=()
    MENU_W_NAME=34; MENU_W_STATUS=26; MENU_W_EXTRA=0; MENU_W_SUB=""
    v=$(prod_version "$id")
    if prod_installed "$id" && free=$(prod_free_edition "$id"); then
        # На месте продукта — его бесплатная версия. Её команду отсюда не
        # запускаем: человек пришёл за pro, а «Открыть меню продукта»
        # открывало ему бесплатное меню. Первый пункт ставит pro поверх.
        # Подпись не длиннее соседних: ширину окна подменю не считает, а что
        # переживёт установку, человек прочтёт в пояснении перед ней.
        case "$id" in
            govless)  avail=$(bi "ключи и пользователи останутся" "keys and users are kept") ;;
            gokaskad) avail=$(bi "правила каскада останутся" "cascade rules are kept") ;;
            gotelegram) avail=$(bi "настройки pro останутся" "pro settings are kept") ;;
            *)        avail=$(bi "штатным установщиком" "with the product's own installer") ;;
        esac
        if rv=$(prod_best_remote "$id"); then
            ref="${rv%% *}"; rv="${rv#* }"
            avail="$(bi "поставится" "will install") ${ref}: ${rv}"
        fi
        name="$(bi "Поставить" "Install") $(prod_name "$id") $(bi "поверх" "over") ${free}"
        [ "$(str_len "$name")" -gt "$MENU_W_NAME" ] && MENU_W_NAME=$(str_len "$name")
        prod_free_shows_version "$id" || v=""
        MENU_ITEMS+=("1|${name}|y:$(bi "бесплатный" "free") ${free}${v:+ $v}||${avail}")
    elif prod_installed "$id"; then
        MENU_ITEMS+=("1|$(bi "Открыть меню продукта" "Open the product menu")|g:$(bi "установлен" "installed")${v:+ $v}||$(prod_cmd "$id")")
        # У пункта обновления стоит то, ЧЕМ обновятся: без этого «обновить»
        # — предложение вслепую.
        avail=$(bi "версии с GitHub не спрошены" "versions not fetched from GitHub")
        if rv=$(prod_best_remote "$id"); then
            ref="${rv%% *}"; rv="${rv#* }"
            if ver_newer "$v" "$rv"; then avail="$(bi "в ветке" "on branch") ${ref}: ${rv}"
            else avail="$(bi "новее нет" "nothing newer")"; fi
        fi
        MENU_ITEMS+=("2|$(bi "Обновить или переустановить" "Update or reinstall")|d:${avail}||$(bi "штатным установщиком" "with the product's own installer")")
    else
        st=$(prod_readiness "$id")
        avail=""
        if rv=$(prod_best_remote "$id"); then ref="${rv%% *}"; rv="${rv#* }"; avail="$(bi "поставится" "will install") ${ref}: ${rv}"; fi
        MENU_ITEMS+=("1|$(bi "Установить" "Install")|${st}||${avail}")
    fi
    MENU_ITEMS+=("3|$(bi "Проверить порты" "Check ports")|d:||$(bi "без установки" "without installing")")
    MENU_ITEMS+=("-|||")
    MENU_ITEMS+=("0|$(bi "Назад" "Back")|d:||")
}

product_menu() {
    local id="$1" cmd
    cmd=$(prod_cmd "$id")
    while true; do
        menu_items_product "$id"
        pick_menu || return 0
        case "$PICK_KEY" in
            1)
                if prod_installed "$id" && ! prod_free_edition "$id" >/dev/null; then
                    if command -v "$cmd" >/dev/null 2>&1; then "$cmd" || true
                    else warn "$(bi "Команды ${cmd} нет в PATH — запустите обновление." "Command ${cmd} is not in PATH — run the update.")"; sleep 1; fi
                else install_flow "$id"; fi ;;
            2) install_flow "$id" ;;
            3)
                if ! mode_resolve "$id"; then ask_mode "$id" || continue; fi
                preflight "$id" "$MODE" || true
                echo -ne "  $(bi "Нажмите Enter, чтобы вернуться" "Press Enter to return") "; read -r || true ;;
            0) return 0 ;;
        esac
    done
}

# Главный экран — он же сводка: против каждого продукта либо версия, либо
# приговор проверки портов. Иначе «готов ли он к установке» узнаётся только
# заходом внутрь, а это ровно то, что оператор и хочет увидеть сразу.
# Ячейка «доступно»: что лежит в ветках и новее ли оно установленного.
prod_avail_cell() {
    local id="$1" inst="$2" rv ref v
    if ! rv=$(prod_best_remote "$id"); then printf 'd:—'; return 0; fi
    ref="${rv%% *}"; v="${rv#* }"
    if [ -n "$inst" ] && ver_newer "$inst" "$v"; then printf 'g:%s %s ↑' "$ref" "$v"
    else printf 'd:%s %s' "$ref" "$v"; fi
}

menu_items_main() {
    local i=0 n st v cols avail_w free
    MENU_TITLE="goInstaller ${GOINST_VERSION} $(bi "— установщик продуктов серии go" "— installer for the go product family")"
    # Главное меню само заново спрашивает GitHub раз в GOINST_MENU_REFRESH
    # секунд: свежий пуш должен появляться, пока экран открыт.
    MENU_REFRESH_SEC="${GOINST_MENU_REFRESH:-180}"
    case "$MENU_REFRESH_SEC" in ''|*[!0-9]*) MENU_REFRESH_SEC=0 ;; esac
    MENU_REFRESH_HOOK="menu_refresh_main"
    menu_footer_info
    MENU_SUB="$OS_PRETTY"
    MENU_ITEMS=()
    MENU_W_NAME=16; MENU_W_STATUS=22
    # Колонку «доступно» показываем, только если опрос вообще состоялся:
    # пустой столбец прочерков занимает место и ничего не сообщает.
    if [ "$VER_NET_OK" = "yes" ]; then MENU_W_EXTRA=16; else MENU_W_EXTRA=0; fi
    cols=$(tput cols 2>/dev/null) || cols=""
    case "$cols" in ''|*[!0-9]*) cols="${COLUMNS:-80}" ;; esac
    avail_w=$MENU_W_EXTRA; [ "$avail_w" -gt 0 ] && avail_w=$((avail_w + 2))
    MENU_W_SUB=$(( cols - 6 - MENU_W_NAME - 2 - MENU_W_STATUS - avail_w ))
    [ "$MENU_W_SUB" -lt 12 ] && MENU_W_SUB=0
    scan_cache_begin
    for n in "${PRODUCTS[@]}"; do
        i=$((i+1))
        v=$(prod_version "$n")
        if prod_installed "$n" && free=$(prod_free_edition "$n"); then
            prod_free_shows_version "$n" || v=""
            st="y:$(bi "бесплатный" "free") ${free}${v:+ $v}"
        elif prod_installed "$n"; then
            st="g:$(bi "установлен" "installed")${v:+ $v}"
        else
            st=$(prod_readiness "$n"); v=""
        fi
        MENU_ITEMS+=("$i|$(prod_name "$n")|$st|$(prod_avail_cell "$n" "$v")|$(prod_desc "$n")")
    done
    scan_cache_end
    MENU_ITEMS+=("-|||")
    MENU_ITEMS+=("p|$(bi "Карта портов" "Port map")|d:||$(bi "кто какие порты держит" "who holds which ports")")
    if [ "$VER_NET_TRIED" = "yes" ] || [ -n "$TOKEN" ]; then
        MENU_ITEMS+=("r|$(bi "Обновить сведения о версиях" "Refresh version info")|d:||$(bi "спросить GitHub заново" "ask GitHub again")")
    fi
    # Своя версия — из ветки, как у продуктов. Пункт показываем, только
    # когда в ветке действительно новее: предлагать обновление до того же
    # самого значит тратить время человека.
    local srv sref sver
    if srv=$(self_best); then
        sref="${srv%% *}"; sver="${srv#* }"
        if ver_newer "$GOINST_VERSION" "$sver"; then
            MENU_ITEMS+=("u|$(bi "Обновить goInstaller" "Update goInstaller")|g:${sver} ↑||$(bi "ветка" "branch") ${sref}, $(bi "сейчас" "now") ${GOINST_VERSION}")
        fi
    fi
    MENU_ITEMS+=("l|$(bi "Язык" "Language")|d:$(bi "русский" "English")||$(bi "сменить язык установщика" "change the installer language")")
    MENU_ITEMS+=("x|$(bi "Удалить goInstaller" "Remove goInstaller")|d:||$(bi "продукты останутся" "products stay")")
    MENU_ITEMS+=("0|$(bi "Выход" "Exit")|d:||")
}

# Такт главного меню: спросить GitHub заново и пересобрать пункты. Вместе с
# версиями пересчитывается и готовность — порты за три минуты тоже могли
# освободиться или заняться. Вывод опроса глушит вызывающий, иначе
# «Спрашиваю GitHub…» печаталось бы поверх экрана.
menu_refresh_main() {
    versions_fetch
    self_fetch_versions
    menu_footer_info
    menu_items_main
    return 0
}

# Время под меню — время последнего опроса, а не входа в меню: раньше оно
# застывало на минуте запуска, хотя сведения обновлялись раз в три минуты.
menu_footer_info() {
    local t
    if [ "${MENU_REFRESH_SEC:-0}" -gt 0 ]; then
        MENU_FOOTER_INFO="$(bi "Сведения на" "Info as of") $(date +%H:%M), $(bi "обновляются сами раз в" "refreshed automatically every") $(( (MENU_REFRESH_SEC + 59) / 60 )) $(bi "мин" "min")"
    else
        MENU_FOOTER_INFO=""
    fi
    if t=$(gh_limited); then
        MENU_FOOTER_INFO="${MENU_FOOTER_INFO:+${MENU_FOOTER_INFO} · }$(bi "лимит GitHub исчерпан до" "GitHub limit used up until") ${t}"
    fi
    return 0
}

main_menu() {
    while true; do
        menu_items_main
        pick_menu || return 0
        case "$PICK_KEY" in
            # Цифра — номер продукта в списке. Перечислять их здесь поимённо
            # нельзя: добавление шестого продукта молча оставило бы его
            # пункт мёртвым, а таких пунктов в меню уже заводилось.
            [1-9])
                [ "$PICK_KEY" -le "${#PRODUCTS[@]}" ] && product_menu "${PRODUCTS[$((PICK_KEY-1))]}" ;;
            p) show_ports; echo -ne "  $(bi "Нажмите Enter, чтобы вернуться" "Press Enter to return") "; read -r || true ;;
            r) versions_forget; versions_fetch; self_fetch_versions ;;
            u) self_update; echo -ne "  $(bi "Нажмите Enter, чтобы вернуться" "Press Enter to return") "; read -r || true; return 0 ;;
            l) lang_pick "$LANG_CODE" && lang_persist ;;
            x) goinst_uninstall && [ ! -e "$GOINST_BIN" ] && exit 0 ;;
            0) return 0 ;;
        esac
    done
}

# ── размер пакета: сетевая карта против пути до GitHub ──────────────────
# Хостер отдаёт виртуалке MTU 1500, а его сеть пропускает меньше (на
# 177.3.218.172 — 1452). Сервер при соединении просит пакеты по 1460 байт,
# крупный ответ GitHub с сертификатом застревает на узком месте, и TLS
# рвётся по таймауту: curl (28), то при опросе версий, то при скачивании
# установщика. Ключ при этом ни при чём, а сообщение винило его.
# Это единственное место, где goInstaller меняет систему, и только по
# ответу «1) Да»: так решил заказчик (5.10). Без согласия — лишь сказать.
NET_TARGET="${GOINST_NET_TARGET:-api.github.com}"
NET_DEV=""; NET_IFMTU=""; NET_PMTU=""; NET_IP=""

# Системные вызовы — отдельными функциями: тесты подменяют их, а не сеть.
_net_resolve() { getent ahostsv4 "$1" 2>/dev/null | awk 'NR==1{print $1}'; }
_net_route_dev() { ip -4 route get "$1" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1; }
_net_if_mtu() { cat "/sys/class/net/$1/mtu" 2>/dev/null; }
# _net_ping_df <ip> <размер данных> → вывод ping; rc 0 — пакет прошёл.
_net_ping_df() { LC_ALL=C ping -4 -n -c1 -W2 -M do -s "$2" "$1" 2>&1; }
_net_set_mtu() { ip link set dev "$1" mtu "$2" 2>/dev/null; }

# net_mtu_probe — заполняет NET_*. rc: 0 путь не уже карты, 1 уже (NET_PMTU),
# 2 узнать нельзя (нет маршрута, ping нет, ICMP режется).
net_mtu_probe() {
    local out size lo hi m try
    NET_DEV=""; NET_IFMTU=""; NET_PMTU=""
    command -v ping >/dev/null 2>&1 || [ -n "${GOINST_TEST:-}" ] || return 2
    NET_IP=$(_net_resolve "$NET_TARGET"); [ -n "$NET_IP" ] || return 2
    NET_DEV=$(_net_route_dev "$NET_IP"); [ -n "$NET_DEV" ] || return 2
    NET_IFMTU=$(_net_if_mtu "$NET_DEV")
    case "$NET_IFMTU" in ''|*[!0-9]*) return 2 ;; esac
    [ "$NET_IFMTU" -gt 1280 ] || return 0
    size=$((NET_IFMTU - 28))
    # Узкое место отвечает «Frag needed (mtu = N)», а повтор ядро режет само:
    # «message too long, mtu=N». Узких мест бывает несколько: на 177.3.218.172
    # первый узел называл 1472, а дальше путь сужался до 1452. Поэтому
    # спускаемся по ответам, пока пакет не пройдёт.
    local cur="$NET_IFMTU" quiet=0
    for try in 1 2 3 4 5 6 7 8; do
        if out=$(_net_ping_df "$NET_IP" $((cur - 28))); then
            [ "$cur" -lt "$NET_IFMTU" ] && { NET_PMTU="$cur"; return 1; }
            return 0
        fi
        m=$(printf '%s\n' "$out" | sed -n -E 's/.*mtu ?= ?([0-9]+).*/\1/p' | head -1)
        case "$m" in ''|*[!0-9]*) m="" ;; esac
        if [ -n "$m" ] && [ "$m" -lt "$cur" ]; then cur="$m"; quiet=0; continue; fi
        # Тот же размер ещё раз — ядро могло не успеть запомнить ответ.
        quiet=$((quiet + 1)); [ "$quiet" -ge 2 ] && break
    done
    size=$((cur - 28))
    # Ответа о размере нет — ищем сами. Не прошли и 1200 байт — ICMP режут
    # целиком, и о пути сказать нечего.
    _net_ping_df "$NET_IP" 1200 >/dev/null || return 2
    lo=1200; hi=$size
    while [ $((hi - lo)) -gt 1 ]; do
        m=$(( (lo + hi) / 2 ))
        if _net_ping_df "$NET_IP" "$m" >/dev/null; then lo=$m; else hi=$m; fi
    done
    NET_PMTU=$((lo + 28))
    [ "$NET_PMTU" -lt "$NET_IFMTU" ] && return 1
    return 0
}

# Кто настраивает карту после перезагрузки: ifupdown, netplan или неизвестно.
# netplan на 177.3.218.172 описывал enp1s0, которого нет, — поэтому ищем
# именно эту карту, а не просто наличие файлов.
net_persist_kind() {
    local dev="$1" r="${GOINST_FS_ROOT:-}" f
    if [ -r "$r/etc/network/interfaces" ] && grep -Eq "^[[:space:]]*iface[[:space:]]+${dev}[[:space:]]+inet" "$r/etc/network/interfaces"; then
        printf 'ifupdown'; return 0
    fi
    for f in "$r"/etc/netplan/*.yaml; do
        [ -r "$f" ] || continue
        grep -Eq "^[[:space:]]+${dev}:[[:space:]]*$" "$f" && { printf 'netplan'; return 0; }
    done
    printf 'none'
}

# Вписать mtu в блок карты в /etc/network/interfaces: прежнюю строку mtu
# этого блока убрать, новую поставить сразу за «iface». Резервная копия рядом.
net_persist_ifupdown() {
    local dev="$1" mtu="$2" f="${GOINST_FS_ROOT:-}/etc/network/interfaces" tmp
    cp -a "$f" "${f}.bak-goinstall-$(date +%Y%m%d%H%M%S)" 2>/dev/null || return 1
    tmp=$(mktemp "${TMPDIR:-/tmp}/goinst-ifaces.XXXXXX") || return 1
    awk -v dev="$dev" -v mtu="$mtu" '
        /^[[:space:]]*(iface|auto|allow-|source|mapping)/ { inblk = 0 }
        inblk && $1 == "mtu" { next }
        { print }
        $1 == "iface" && $2 == dev && $3 == "inet" { inblk = 1; print "    mtu " mtu }
    ' "$f" > "$tmp" && cat "$tmp" > "$f"
    local rc=$?; rm -f "$tmp"; return "$rc"
}

net_persist_netplan() {
    local dev="$1" mtu="$2"
    command -v netplan >/dev/null 2>&1 || return 1
    netplan set "ethernets.${dev}.mtu=${mtu}" >/dev/null 2>&1 || return 1
    netplan generate >/dev/null 2>&1
}

# Отказ запоминаем вместе с найденным размером: при том же — не спрашивать.
NET_DECLINE_FILE="${GOINST_NET_DECLINE_FILE:-${GOINST_DIR}/.mtu-declined}"

# net_mtu_offer [force] — проверить и, если путь уже карты, предложить
# исправление. force — спросить и после прежнего отказа (команда --net).
net_mtu_offer() {
    local force="${1:-}" rc kind
    # Набор тестов настоящую сеть не трогает, кроме тех проверок, что
    # подменяют системные вызовы и включают GOINST_NET_TEST.
    if [ -n "${GOINST_TEST:-}" ] && [ -z "${GOINST_NET_TEST:-}" ]; then return 0; fi
    [ -n "${GOINST_NO_NET:-}" ] && [ -z "$force" ] && return 0
    net_mtu_probe; rc=$?
    if [ "$rc" -eq 0 ]; then
        [ -n "$force" ] && ok "$(bi "Размер пакета в порядке: ${NET_DEV:-?} — ${NET_IFMTU:-?}, путь до GitHub его пропускает." "Packet size is fine: ${NET_DEV:-?} is ${NET_IFMTU:-?}, the path to GitHub carries it.")"
        return 0
    fi
    if [ "$rc" -eq 2 ]; then
        [ -n "$force" ] && warn "$(bi "Проверить размер пакета не вышло: нет ping, маршрута или ответов ICMP." "Could not check the packet size: no ping, no route or no ICMP replies.")"
        return 0
    fi
    if [ -z "$force" ] && [ "$(cat "$NET_DECLINE_FILE" 2>/dev/null)" = "${NET_DEV}:${NET_PMTU}" ]; then return 0; fi
    # Без терминала спросить некого: только сказать. Конец ввода — не «нет»,
    # отказ из него не записываем.
    local asking=1
    [ -t 0 ] || [ -n "${GOINST_NET_TEST:-}" ] || asking=0
    echo "" >&2
    warn "$(bi "Сетевая карта ${NET_DEV} настроена на пакеты в ${NET_IFMTU} байт, а путь до GitHub пропускает только ${NET_PMTU}. Из-за этого соединения с GitHub рвутся через раз: «SSL connection timeout» при опросе версий и при скачивании установщиков. Ключ тут ни при чём." "Network card ${NET_DEV} is set to ${NET_IFMTU}-byte packets, but the path to GitHub carries only ${NET_PMTU}. That is why GitHub connections fail every few tries: «SSL connection timeout» while checking versions and downloading installers. The key is not at fault.")"
    kind=$(net_persist_kind "$NET_DEV")
    case "$kind" in
        ifupdown) say "$(bi "Исправление: размер ${NET_PMTU} для ${NET_DEV} сейчас и запись в /etc/network/interfaces (с копией рядом) — чтобы пережило перезагрузку." "Fix: size ${NET_PMTU} for ${NET_DEV} now, plus a line in /etc/network/interfaces (backed up) so it survives a reboot.")" ;;
        netplan)  say "$(bi "Исправление: размер ${NET_PMTU} для ${NET_DEV} сейчас и в настройках netplan — чтобы пережило перезагрузку." "Fix: size ${NET_PMTU} for ${NET_DEV} now and in the netplan settings so it survives a reboot.")" ;;
        *)        say "$(bi "Исправление: размер ${NET_PMTU} для ${NET_DEV} сейчас. Где сеть настраивается после перезагрузки, не нашёл — закрепить придётся вручную." "Fix: size ${NET_PMTU} for ${NET_DEV} now. I did not find where the network is configured at boot, so making it permanent is manual.")" ;;
    esac
    if [ "$asking" -eq 0 ]; then
        say "$(bi "Исправить: goinstall --net, или вручную ip link set dev ${NET_DEV} mtu ${NET_PMTU}." "To fix: goinstall --net, or by hand ip link set dev ${NET_DEV} mtu ${NET_PMTU}.")"
        return 0
    fi
    if ! confirm "$(bi "Изменить размер пакета сетевой карты ${NET_DEV} на ${NET_PMTU}?" "Change the packet size of network card ${NET_DEV} to ${NET_PMTU}?")" y; then
        mkdir -p "$(dirname "$NET_DECLINE_FILE")" 2>/dev/null && printf '%s\n' "${NET_DEV}:${NET_PMTU}" > "$NET_DECLINE_FILE" 2>/dev/null
        say "$(bi "Оставил как есть. Вручную: ip link set dev ${NET_DEV} mtu ${NET_PMTU}. Спросить снова: goinstall --net" "Left as is. By hand: ip link set dev ${NET_DEV} mtu ${NET_PMTU}. Ask again: goinstall --net")"
        return 0
    fi
    net_mtu_apply "$kind"
}

net_mtu_apply() {
    local kind="$1" dev="$NET_DEV" mtu="$NET_PMTU" old="$NET_IFMTU" rc
    if ! _net_set_mtu "$dev" "$mtu"; then
        err "$(bi "Не удалось поменять размер пакета ${dev}." "Could not change the packet size of ${dev}.")"; return 1
    fi
    # Проверяем делом: после правки полный пакет должен проходить.
    # Успех — только «полный пакет прошёл». «Узнать нельзя» после правки тоже
    # повод вернуть как было: записывать непроверенное в настройки нельзя.
    net_mtu_probe; rc=$?
    if [ "$rc" -ne 0 ]; then
        _net_set_mtu "$dev" "$old"
        err "$(bi "После правки полный пакет до GitHub не проходит — вернул ${old}, ничего не записал." "After the change a full packet still does not reach GitHub — restored ${old}, wrote nothing.")"
        return 1
    fi
    ok "$(bi "Размер пакета ${dev}: ${mtu}." "Packet size of ${dev}: ${mtu}.")"
    case "$kind" in
        ifupdown)
            if net_persist_ifupdown "$dev" "$mtu"; then ok "$(bi "Записано в /etc/network/interfaces, копия рядом." "Written to /etc/network/interfaces, a backup is next to it.")"
            else warn "$(bi "Записать в /etc/network/interfaces не удалось: после перезагрузки вернётся ${old}." "Could not write /etc/network/interfaces: ${old} comes back after a reboot.")"; fi ;;
        netplan)
            if net_persist_netplan "$dev" "$mtu"; then ok "$(bi "Записано в настройки netplan." "Written to the netplan settings.")"
            else warn "$(bi "Записать в netplan не удалось: после перезагрузки вернётся ${old}." "Could not write to netplan: ${old} comes back after a reboot.")"; fi ;;
        *)  warn "$(bi "Действует до перезагрузки. Закрепите mtu ${mtu} для ${dev} в настройках сети сервера." "Active until reboot. Make mtu ${mtu} for ${dev} permanent in the server's network settings.")" ;;
    esac
    rm -f "$NET_DECLINE_FILE" 2>/dev/null
    return 0
}

# ── удаление самого goInstaller ─────────────────────────────────────────
# Уносит только своё: команды goinstall и go-installer, каталог
# /opt/go-installer (ключ, кеш версий, язык, размер меню), таймер сводки и
# скрипт входа. Продукты, их команды и настройки не трогает. Размер пакета
# сетевой карты (если его меняли через --net) остаётся: это настройка сети,
# и без неё вернутся обрывы до GitHub.
# Ключ — по выбору: его как запасной читает goKaskad pro, когда качает ядро
# AmneziaWG, и его же подхватит goInstaller, если поставить заново.
goinst_remove_list() {  # что именно уйдёт — для показа
    local f
    for f in "$GOINST_BIN" "$GOINST_BIN_ALIAS"; do [ -e "$f" ] || [ -L "$f" ] && printf '%s\n' "$f"; done
    [ -d "$GOINST_DIR" ] && printf '%s\n' "$GOINST_DIR"
    for f in "$SUM_UNIT_DIR/goinstall-watch.timer" "$SUM_UNIT_DIR/goinstall-watch.service" "$SUM_HOOK"; do [ -e "$f" ] && printf '%s\n' "$f"; done
    return 0
}

# goinst_remove <keep|drop> — сделать, без вопросов. rc 1 — каталог странный.
goinst_remove() {
    local key="$1" f
    # Страховка от пустой или подменённой переменной: rm -rf «/» недопустим.
    case "$GOINST_DIR" in
        /*/*|/opt/go-installer) ;;
        *) err "$(bi "Каталог goInstaller задан странно (${GOINST_DIR:-пусто}) — ничего не удаляю." "The goInstaller directory looks wrong (${GOINST_DIR:-empty}) — removing nothing.")"; return 1 ;;
    esac
    if command -v systemctl >/dev/null 2>&1 && [ -e "$SUM_UNIT_DIR/goinstall-watch.timer" ]; then
        systemctl disable --now goinstall-watch.timer >/dev/null 2>&1 || true
    fi
    rm -f "$SUM_UNIT_DIR/goinstall-watch.service" "$SUM_UNIT_DIR/goinstall-watch.timer" "$SUM_HOOK" 2>/dev/null
    command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >/dev/null 2>&1
    # Псевдоним — только если это наш: ссылка на goinstall или наш скрипт.
    if [ -L "$GOINST_BIN_ALIAS" ]; then rm -f "$GOINST_BIN_ALIAS"
    elif grep -q '^GOINST_VERSION=' "$GOINST_BIN_ALIAS" 2>/dev/null; then rm -f "$GOINST_BIN_ALIAS"; fi
    if [ -e "$GOINST_BIN" ] && ! grep -q '^GOINST_VERSION=' "$GOINST_BIN" 2>/dev/null; then
        warn "$(bi "${GOINST_BIN} — не goInstaller, оставляю." "${GOINST_BIN} is not goInstaller, leaving it.")"
    else
        rm -f "$GOINST_BIN"
    fi
    if [ "$key" = keep ] && [ -f "$TOKEN_FILE" ]; then
        for f in "$GOINST_DIR"/* "$GOINST_DIR"/.[!.]*; do
            [ -e "$f" ] || continue
            [ "$f" = "$TOKEN_FILE" ] && continue
            rm -rf "$f"
        done
    else
        rm -rf "$GOINST_DIR"
    fi
    return 0
}

goinst_remove_done() {  # goinst_remove_done <keep|drop>
    ok "$(bi "goInstaller удалён. Продукты на месте, их команды работают." "goInstaller is removed. The products stay, their commands work.")"
    [ "$1" = keep ] && [ -f "$TOKEN_FILE" ] && say "$(bi "Ключ оставлен: ${TOKEN_FILE}." "The key is kept: ${TOKEN_FILE}.")"
    say "$(bi "Поставить снова — той же командой из клуба, что и в первый раз." "To install again, use the same club command as the first time.")"
}

# goinst_uninstall [--yes] [--keep-key|--drop-key] — команда --uninstall.
goinst_uninstall() {
    local yes="" key="" a list
    for a in "$@"; do
        case "$a" in
            --yes|-y) yes=1 ;;
            --keep-key) key=keep ;;
            --drop-key) key=drop ;;
            *) usage; return 64 ;;
        esac
    done
    list=$(goinst_remove_list)
    if [ -z "$list" ]; then ok "$(bi "goInstaller на этом сервере уже не стоит." "goInstaller is not installed on this server.")"; return 0; fi
    # Без терминала спросить некого — отказ до любых вопросов, а не немой
    # выход на первом же чтении.
    if [ -z "$yes" ] && [ ! -t 0 ]; then
        err "$(bi "Без терминала спросить некого: goinstall --uninstall --yes" "No terminal to ask: goinstall --uninstall --yes")"
        return 1
    fi
    print_header "$(bi "Удаление goInstaller" "Removing goInstaller")"
    say "$(bi "Будет удалено:" "Will be removed:")"
    printf '      %s\n' $list >&2
    say "$(bi "Не тронется: продукты, их команды и настройки; размер пакета сетевой карты." "Untouched: the products, their commands and settings; the network card packet size.")"
    if [ -z "$key" ] && [ -f "$TOKEN_FILE" ]; then
        if [ -n "$yes" ]; then key=drop
        else
            echo "" >&2
            echo -e "  $(bi "Ключ доступа клуба лежит в ${TOKEN_FILE}. goKaskad pro берёт его как запасной, а goInstaller подхватит, если поставить заново." "The club access key is in ${TOKEN_FILE}. goKaskad pro uses it as a fallback, and goInstaller picks it up if installed again.")" >&2
            echo -e "  ${CYAN}1)${NC} $(bi "Удалить и ключ" "Remove the key too") ${DIM}— $(bi "по умолчанию" "default")${NC}" >&2
            echo -e "  ${CYAN}2)${NC} $(bi "Ключ оставить" "Keep the key")" >&2
            read_line a || return 1
            case "${a:-1}" in 1) key=drop ;; 2) key=keep ;; *) warn "$(bi "Неверный ввод." "Invalid input.")"; return 1 ;; esac
        fi
    fi
    [ -n "$key" ] || key=drop
    if [ -z "$yes" ]; then
        confirm "$(bi "Удалить goInstaller?" "Remove goInstaller?")" n || { say "$(bi "Отменено, ничего не удалено." "Cancelled, nothing removed.")"; return 0; }
    fi
    goinst_remove "$key" || return 1
    goinst_remove_done "$key"
    return 0
}

usage() {
    local p modes_line=""
    for p in "${PRODUCTS[@]}"; do
        prod_has_modes "$p" && modes_line="${modes_line:+${modes_line}; }${p} — $(prod_mode_choices "$p")"
    done
    cat <<USAGE
goInstaller ${GOINST_VERSION}

  goinstall                       $(bi "меню" "menu")
  goinstall --list                $(bi "что установлено" "what is installed")
  goinstall --ports               $(bi "кто какие порты держит" "who holds which ports")
  goinstall --versions            $(bi "версии на сервере и в ветках GitHub" "versions on this server and on GitHub branches")
  goinstall --self-update [$(bi "ветка" "branch")]
                                  $(bi "обновить сам goInstaller" "update goInstaller itself")
  goinstall --key [set|check|forget]
                                  $(bi "ключ доступа: ввести, проверить, удалить" "access key: set, check, remove")
  goinstall --check <id> [$(bi "режим" "mode")]
                                  $(bi "проверка портов без установки; код: 0 ок, 1 препятствия, 2 замечания" "port check without installing; rc: 0 ok, 1 blockers, 2 remarks")
  goinstall --install <id> [$(bi "режим" "mode")] [$(bi "ветка" "branch")]
                                  $(bi "сразу к установке; без ветки спросит; без режимов — сразу ветка: --install gokaskad dev" "straight to installing; asks for the branch if omitted; no modes — branch right away: --install gokaskad dev")
  goinstall --uninstall [--yes] [--keep-key]
                                  $(bi "удалить сам goInstaller; продукты не трогает" "remove goInstaller itself; products are untouched")
  goinstall --net                 $(bi "проверить размер пакета до GitHub и предложить исправление" "check the packet size to GitHub and offer a fix")
  goinstall --summary on|off       $(bi "сводка при входе по SSH и опрос раз в 30 минут" "login summary over SSH and polling every 30 minutes")
  goinstall --version

  id: ${PRODUCTS[*]}
  $(bi "режимы" "modes"): ${modes_line}

  $(bi "Окружение" "Environment"): GH_TOKEN, GOINST_SRC_<id>, GOINST_REF_<id>, GOINST_REPO_<id>, GOWARP_KEY, GOINST_LANG=ru|en,
    GOINST_LAZY_DOMAIN, GOINST_LAZY_KEYS ($(bi "Ленивый pro у goamnezia" "Lazy pro of goamnezia"))
USAGE
}

valid_id() { local p; for p in "${PRODUCTS[@]}"; do [ "$p" = "$1" ] && return 0; done; return 1; }

list_products() {
    local n
    for n in "${PRODUCTS[@]}"; do
        printf "  %-16s %s\n" "$(prod_name "$n")" "$(status_line "$n")"
    done
}

# Таблица версий: что стоит на сервере и что лежит в каждой ветке.
show_versions() {
    local n ref v inst cell shown free
    versions_fetch
    self_fetch_versions
    print_header "$(bi "Версии: на сервере и в ветках" "Versions: on this server and on the branches")"
    if [ -z "$TOKEN" ] && [ -z "${GOINST_NO_NET:-}" ]; then
        warn "$(bi "Ветки не опрошены: нет ключа. Задайте GH_TOKEN." "Branches not queried: no key. Set GH_TOKEN.")"
    fi
    # Колонки через pad_to: `printf '%-16s'` считает БАЙТЫ, и «goTelegram pro»
    # рядом с «goДНС» разъехалось бы на длину кириллицы.
    printf "  ${DIM}%s %s %s${NC}\n" "$(pad_to "$(bi "продукт" "product")" 16)" \
        "$(pad_to "$(bi "на сервере" "on server")" 12)" "$(bi "в ветках" "on branches")" >&2
    cell=""
    for ref in $(self_refs); do
        v=$(ver_cache_get "$SELF_ID" "$ref") || v=""
        [ -n "$v" ] || continue
        if ver_newer "$GOINST_VERSION" "$v"; then cell+="${ref} ${v} ↑  "; else cell+="${ref} ${v}  "; fi
    done
    printf "  %s %s %s\n" "$(pad_to "goInstaller" 16)" "$(pad_to "$GOINST_VERSION" 12)" "${cell:-—}" >&2
    for n in "${PRODUCTS[@]}"; do
        inst=$(prod_version "$n")
        prod_installed "$n" || inst=""
        shown="${inst:-—}"
        if prod_installed "$n" && free=$(prod_free_edition "$n"); then
            shown="$free"; prod_free_shows_version "$n" && shown="${free}${inst:+ $inst}"
        fi
        cell=""
        for ref in $(prod_refs "$n"); do
            v=$(ver_cache_get "$n" "$ref") || v=""
            [ -n "$v" ] || continue
            if [ -n "$inst" ] && ver_newer "$inst" "$v"; then cell+="${ref} ${v} ↑  "
            else cell+="${ref} ${v}  "; fi
        done
        printf "  %s %s %s\n" "$(pad_to "$(prod_name "$n")" 16)" "$(pad_to "$shown" 12)" "${cell:-—}" >&2
    done
    echo "" >&2
}

# ── точка входа ─────────────────────────────────────────────────────────
# ═══ Интерфейс «Аркада» ════════════════════════════════════════════════
# Макет — пакет Claude Design в design/handoff: прототип, экраны построчно,
# цвета. Правила там же, в design/README.md, и они сильнее пакета.
#
# Экран — массив строк UI_BODY. Строка — сегменты «токен␟текст␞»: токен —
# роль цвета (title, muted, grad…), суффикс «+sel» — выделенная строка.
# Одна и та же строка печатается тремя способами: в цвете (truecolor, 256,
# 16 или без цвета), в ASCII и дампом по клеткам — им тест сверяет экран с
# эталоном клетка в клетку. Ширина считается в символах, поэтому локаль —
# UTF-8 (ui_locale_fix); без неё bash считает байты, и рамки разъезжаются.

UI_US=$'\x1f'; UI_RS=$'\x1e'
UI_COLOR="true"; UI_BG="dark"; UI_ASCII=0
UI_C=80; UI_R=24; UI_COLS=80; UI_LINES=24
UI_BODY=(); UI_INFO=""; UI_KEYS=""
UI_GRADN=0

# Токены «Аркады»: снято с design/handoff/spec/tokens.json.
# имя|тёмный|светлый|256 тёмный|256 светлый|ANSI-16|без цвета|жирный|текст выделения на тёмном
_UI_TOKENS='title|#ffd23f|#0038b8|221|25|33;1|1|1|-
text|#e6e9f5|#1a1f3a|255|235|39|-|0|-
muted|#8e96b8|#565e80|103|60|90|2|0|-
rule|#3d6bff|#2f5bdc|63|26|34|2|0|-
name|#ffffff|#0d1230|231|234|39;1|-|0|-
cursor|#34d2ff|#0068a8|81|25|36;1|1|1|-
key|#34d2ff|#0068a8|81|25|36|1|0|-
ok|#3ee07a|#16803e|78|29|32|-|0|-
update|#ffb02e|#9a5a00|214|94|33;1|1|1|-
new|#ff5fd2|#b0158a|206|126|35;1|1|1|-
warn|#ff9a3c|#a44a00|209|130|33|1|0|-
err|#ff4d5e|#c0182e|203|124|31;1|1|1|-
grad|#2f6bff:#34d2ff|#1d4ed8:#0086b8|27:81|26:31|34;1|1|0|-
sel|#1d3aa8|#dfe7ff|25|189|7|7|0|#ffffff
logo0|#fff4b0|#f0a000|229|214|33;1|1|1|-
logo1|#ffd23f|#e07800|221|172|33;1|1|1|-
logo2|#ff9a1f|#d04800|208|166|31;1|1|1|-
logo3|#ff5a1f|#b02400|202|124|31;1|1|1|-
shade|#9c2433|#5a1020|125|52|31;2|2|0|-
shadow|#343a66|#c3c9e6|238|252|34;2|2|0|-'

# ASCII-замены: design/handoff/spec/ascii-fallback.json, у каждого значка
# статуса своя буква (✓ v, ✚ +, ◆ ~).
_UI_ASC='─ - ━ - │ | ┌ + ┐ + └ + ┘ + ├ + ┤ + ┬ + ┴ + ┼ + ╭ + ╮ + ╰ + ╯ + ═ = ║ | ╔ + ╗ + ╚ + ╝ + █ # ▓ # ▒ = ░ . ▀ " ▄ _ ▌ [ ▐ ] ▏ = ▎ = ▍ = ▋ = ▊ = ▉ = ▕ ] ● * ○ o ▲ ^ ▼ V ▸ > ◆ ~ ✓ v ✗ x ✚ + · . → > ← < ↑ ^ ↓ v … . — - « " » " – - ⠋ | ⠙ / ⠹ - ⠸ \ ⠼ | ⠴ / ⠦ - ⠧ \ ⠇ | ⠏ /'

# Локаль сессии не установлена (SSH принёс ru_RU.UTF-8, а на сервере её
# нет) — bash считает байты вместо символов, и кириллица рвёт рамки.
# C.UTF-8 есть на любой современной Ubuntu и Debian; нет и её — ASCII.
ui_locale_fix() {
    local cm la
    if [ -n "${GOINST_ASCII:-}" ]; then UI_ASCII=1; return 0; fi
    cm=$(locale charmap 2>/dev/null) || cm=""
    case "$cm" in UTF-8|utf8|UTF8) UI_ASCII=0; return 0 ;; esac
    # Список — в переменную, не в `| grep -q`: под pipefail grep закрывает
    # канал на первом совпадении, locale -a ловит SIGPIPE, и «C.UTF-8 нет»
    # выходило там, где она есть (на macOS — каждый раз).
    la=$(locale -a 2>/dev/null) || la=""
    if grep -qiE '^c\.utf-?8$' <<<"$la"; then
        export LC_ALL=C.UTF-8
        UI_ASCII=0; return 0
    fi
    UI_ASCII=1
    return 0
}

# Режим цвета и фон — по правилам пакета (README, «Выбор режима»).
ui_detect() {
    if [ -n "${GOINST_COLOR:-}" ]; then UI_COLOR="$GOINST_COLOR"
    elif [ -n "${NO_COLOR:-}" ] || [ "${TERM:-dumb}" = dumb ]; then UI_COLOR=none
    elif [ "${COLORTERM:-}" = truecolor ] || [ "${COLORTERM:-}" = 24bit ]; then UI_COLOR=true
    else case "${TERM:-}" in *256color*) UI_COLOR=256 ;; *) UI_COLOR=16 ;; esac
    fi
    case "${GOINST_BG:-}" in
        light|dark) UI_BG="$GOINST_BG" ;;
        *) case "${COLORFGBG:-}" in
               *\;15|*\;7) UI_BG=light ;;
               *) if [ "${TERM_PROGRAM:-}" = Apple_Terminal ]; then UI_BG=light; else UI_BG=dark; fi ;;
           esac ;;
    esac
    ui_locale_fix
    ui_size
    ui_tokens
}

# Размер окна → раскладка: от 80 колонок — две панели, 46–79 — один столбец,
# меньше 46 × 24 — экран «окно слишком мало».
#
# На большом окне холст 80×24 стоял в левом верхнем углу, и меню казалось
# мелким и брошенным с краю (заказчик, 5.10). Три размера, F2 по кругу:
#   full    — рамки по ширине окна, не шире 160, по центру. Высота остаётся
#             24: содержимое короткое, и высокие окна выходили пустыми
#             коробками — проверено отрисовкой на 130×34;
#   center  — прежние 80×24 посреди окна;
#   compact — прежние 80×24 в углу.
# Шрифт скрипт не меняет: это настройка терминала.
UI_SIZES="full center compact"
ui_size_file() { printf '%s' "${GOINST_UI_SIZE_FILE:-${GOINST_DIR}/.ui-size}"; }
ui_size_mode() {
    local m="${GOINST_UI_SIZE:-}"
    [ -n "$m" ] || m=$(head -1 "$(ui_size_file)" 2>/dev/null) || m=""
    case "$m" in full|center|compact) printf '%s' "$m" ;; *) printf 'full' ;; esac
}
ui_size_next() {  # следующий размер по кругу, запомнить
    local cur n
    cur=$(ui_size_mode)
    case "$cur" in full) n=center ;; center) n=compact ;; *) n=full ;; esac
    GOINST_UI_SIZE="$n"
    mkdir -p "$(dirname "$(ui_size_file)")" 2>/dev/null && printf '%s\n' "$n" > "$(ui_size_file)" 2>/dev/null
    return 0
}
ui_size() {
    local c l m
    c="${GOINST_UI_COLS:-$(tput cols 2>/dev/null)}"; l="${GOINST_UI_LINES:-$(tput lines 2>/dev/null)}"
    case "$c" in ''|*[!0-9]*) c=80 ;; esac
    case "$l" in ''|*[!0-9]*) l=24 ;; esac
    UI_COLS="$c"; UI_LINES="$l"; UI_PADL=0; UI_PADT=0
    if [ "$c" -lt 46 ] || [ "$l" -lt 24 ]; then UI_C=40; UI_R=16
    elif [ "$c" -lt 80 ]; then UI_C=46; UI_R=24
    else UI_C=80; UI_R=24; fi
    [ "$UI_C" = 80 ] || return 0
    m=$(ui_size_mode)
    if [ "$m" = full ]; then
        UI_C=$(( c > 160 ? 160 : c ))
    fi
    if [ "$m" != compact ]; then
        UI_PADL=$(( (c - UI_C) / 2 )); UI_PADT=$(( (l - UI_R) / 2 ))
    fi
    return 0
}

_ui_rgb() { printf -v "$1" '%d;%d;%d' "0x${2:1:2}" "0x${2:3:2}" "0x${2:5:2}"; }
# Ближайший цвет палитры 256: куб 16–231 или серая шкала 232–255.
_ui_q6() { if [ "$1" -lt 48 ]; then UI_Q=0; elif [ "$1" -lt 115 ]; then UI_Q=1; else UI_Q=$(( ($1 - 35) / 40 )); fi; }
_ui_to256() {
    local r="$2" g="$3" b="$4" qr qg qb cr cg cb gi gv dc dg
    _ui_q6 "$r"; qr=$UI_Q; _ui_q6 "$g"; qg=$UI_Q; _ui_q6 "$b"; qb=$UI_Q
    local cube=(0 95 135 175 215 255)
    cr=${cube[$qr]}; cg=${cube[$qg]}; cb=${cube[$qb]}
    gi=$(( ( (r + g + b) / 3 - 8 + 5 ) / 10 )); [ "$gi" -lt 0 ] && gi=0; [ "$gi" -gt 23 ] && gi=23
    gv=$(( 8 + 10 * gi ))
    dc=$(( (r-cr)*(r-cr) + (g-cg)*(g-cg) + (b-cb)*(b-cb) ))
    dg=$(( (r-gv)*(r-gv) + (g-gv)*(g-gv) + (b-gv)*(b-gv) ))
    if [ "$dg" -lt "$dc" ]; then printf -v "$1" '%s' "$(( 232 + gi ))"
    else printf -v "$1" '%s' "$(( 16 + 36*qr + 6*qg + qb ))"; fi
}

# Последовательности цвета для каждого токена в текущем режиме и фоне.
ui_tokens() {
    declare -gA UI_SGR=() UI_SGRSEL=()
    UI_GRAD=(); UI_GRADN=0
    local name td tl nd nl a16 nc bold selfg fg n256 rgb code codes sel_bg="" sel_fg="" sel256 hexs a b c r g bb n
    local line
    # Фон и текст выделенной строки.
    while IFS='|' read -r name td tl nd nl a16 nc bold selfg; do
        [ "$name" = sel ] || continue
        if [ "$UI_BG" = dark ]; then hexs="$td"; sel256="$nd"; else hexs="$tl"; sel256="$nl"; fi
        _ui_rgb rgb "$hexs"
        case "$UI_COLOR" in
            true) sel_bg="48;2;$rgb" ;;
            256) sel_bg="48;5;$sel256" ;;
        esac
        if [ "$UI_BG" = dark ] && [ "$selfg" != "-" ]; then sel_fg="$selfg"; fi
    done <<<"$_UI_TOKENS"
    while IFS='|' read -r name td tl nd nl a16 nc bold selfg; do
        [ -n "$name" ] || continue
        if [ "$UI_BG" = dark ]; then fg="$td"; n256="$nd"; else fg="$tl"; n256="$nl"; fi
        codes=""
        case "$UI_COLOR" in
            none)
                [ "$nc" != "-" ] && codes="$nc"
                UI_SGR[$name]=$'\033[0'"${codes:+;$codes}m"
                UI_SGRSEL[$name]=$'\033[0'"${codes:+;$codes};7m" ;;
            16)
                UI_SGR[$name]=$'\033[0;'"${a16}m"
                UI_SGRSEL[$name]=$'\033[0;'"${a16};7m" ;;
            256|true)
                [ "$bold" = 1 ] && codes="1;"
                if [ "$name" = grad ]; then
                    UI_SGR[$name]=$'\033[0;'"${codes}38;5;${n256%%:*}m"
                else
                    if [ "$UI_COLOR" = true ]; then _ui_rgb rgb "$fg"; code="38;2;$rgb"
                    else code="38;5;$n256"; fi
                    UI_SGR[$name]=$'\033[0;'"${codes}${code}m"
                    if [ -n "$sel_fg" ]; then
                        if [ "$UI_COLOR" = true ]; then _ui_rgb rgb "$sel_fg"; code="38;2;$rgb"; else code="38;5;231"; fi
                    fi
                    UI_SGRSEL[$name]=$'\033[0;'"${codes}${code}${sel_bg:+;$sel_bg}m"
                fi ;;
        esac
    done <<<"$_UI_TOKENS"
    # Перелив рамки: цвет клетки по номеру колонки, от первого цвета ко второму.
    if [ "$UI_COLOR" = true ] || [ "$UI_COLOR" = 256 ]; then
        while IFS='|' read -r name td tl nd nl a16 nc bold selfg; do
            [ "$name" = grad ] || continue
            if [ "$UI_BG" = dark ]; then hexs="$td"; else hexs="$tl"; fi
            local ha="${hexs%%:*}" hb="${hexs##*:}" ar ag ab br bg2 bb2 i den
            ar=$((16#${ha:1:2})); ag=$((16#${ha:3:2})); ab=$((16#${ha:5:2}))
            br=$((16#${hb:1:2})); bg2=$((16#${hb:3:2})); bb2=$((16#${hb:5:2}))
            den=$(( UI_C - 1 )); [ "$den" -lt 1 ] && den=1
            for ((i = 0; i < UI_C; i++)); do
                r=$(( ar + ( (br - ar) * i * 2 + den ) / (2 * den) ))
                g=$(( ag + ( (bg2 - ag) * i * 2 + den ) / (2 * den) ))
                bb=$(( ab + ( (bb2 - ab) * i * 2 + den ) / (2 * den) ))
                if [ "$UI_COLOR" = true ]; then UI_GRAD[$i]=$'\033[0;'"38;2;$r;$g;${bb}m"
                else _ui_to256 n "$r" "$g" "$bb"; UI_GRAD[$i]=$'\033[0;'"38;5;${n}m"; fi
            done
            UI_GRADN="$UI_C"
        done <<<"$_UI_TOKENS"
    fi
    declare -gA UI_ASC=()
    local k v
    set -f
    # shellcheck disable=SC2086
    set -- $_UI_ASC
    set +f
    while [ $# -ge 2 ]; do UI_ASC["$1"]="$2"; shift 2; done
    return 0
}

# ── сегменты ───────────────────────────────────────────────────────────
# ui_len <сегменты> → UI_N — длина строки в клетках.
ui_len() {
    local s="$1" t n=0
    while [ -n "$s" ]; do
        t="${s%%"${UI_RS}"*}"; s="${s#*"${UI_RS}"}"
        t="${t#*"${UI_US}"}"; n=$(( n + ${#t} ))
    done
    UI_N=$n
}
# ui_padr <текст> <ширина> → UI_P: дополнить пробелами или обрезать с «…».
ui_padr() {
    local s="$1" n="$2"
    if [ "${#s}" -gt "$n" ]; then UI_P="${s:0:$(( n - 1 ))}…"
    else printf -v UI_P '%s%*s' "$s" "$(( n - ${#s} ))" ''; fi
}
# ui_rep <символ> <сколько> → UI_P
ui_rep() { local i s=""; for ((i = 0; i < $2; i++)); do s+="$1"; done; UI_P="$s"; }
# ui_fit <сегменты> <ширина> → UI_FIT и UI_FITN: не шире W, хвост — «…».
ui_fit() {
    local s="$1" w="$2" seg tok txt used=0 out="" l
    while [ -n "$s" ]; do
        [ "$used" -ge "$w" ] && break
        seg="${s%%"${UI_RS}"*}"; s="${s#*"${UI_RS}"}"
        tok="${seg%%"${UI_US}"*}"; txt="${seg#*"${UI_US}"}"
        l=${#txt}
        if [ $(( used + l )) -gt "$w" ]; then txt="${txt:0:$(( w - used - 1 ))}…"; l=${#txt}; fi
        out+="$tok${UI_US}$txt${UI_RS}"; used=$(( used + l ))
    done
    UI_FIT="$out"; UI_FITN="$used"
}
# ui_sel <сегменты> → UI_P: те же сегменты, выделенные.
ui_sel() {
    local s="$1" seg tok out=""
    while [ -n "$s" ]; do
        seg="${s%%"${UI_RS}"*}"; s="${s#*"${UI_RS}"}"
        tok="${seg%%"${UI_US}"*}"
        case "$tok" in *+sel) ;; *) tok="$tok+sel" ;; esac
        out+="$tok${UI_US}${seg#*"${UI_US}"}${UI_RS}"
    done
    UI_P="$out"
}
# ui_wrap <текст> <ширина> → массив UI_WRAP: перенос по словам.
ui_wrap() {
    local text="$1" w="$2" word line=""
    UI_WRAP=()
    for word in $text; do
        if [ -z "$line" ]; then line="$word"
        elif [ $(( ${#line} + 1 + ${#word} )) -le "$w" ]; then line+=" $word"
        else UI_WRAP+=("$line"); line="$word"; fi
    done
    [ -n "$line" ] && UI_WRAP+=("$line")
    return 0
}
# ui_spread <левое> <правое> <ширина> → UI_P: правое прижато к краю.
ui_spread() {
    local g
    ui_len "$1"; g=$UI_N; ui_len "$2"; g=$(( $3 - g - UI_N ))
    if [ "$g" -ge 1 ]; then printf -v UI_P '%s%s%*s%s%s' "$1" "text${UI_US}" "$g" '' "${UI_RS}" "$2"
    else UI_P="$1"; fi
}
# ui_bar <доля в тысячных> <ширина> <токен> → UI_BAR: шкала с шагом ⅛ клетки.
ui_bar() {
    local pm="$1" w="$2" tok="$3" c8 full part out=""
    local P=('' '▏' '▎' '▍' '▌' '▋' '▊' '▉')
    [ "$pm" -lt 0 ] && pm=0; [ "$pm" -gt 1000 ] && pm=1000
    c8=$(( pm * w * 8 / 1000 )); full=$(( c8 / 8 )); part=$(( c8 % 8 ))
    ui_rep '█' "$full"; out+="$tok${UI_US}$UI_P${UI_RS}"
    if [ "$full" -lt "$w" ]; then
        if [ "$part" -gt 0 ]; then out+="$tok${UI_US}${P[$part]}${UI_RS}"; else out+="rule${UI_US}░${UI_RS}"; fi
        ui_rep '░' $(( w - full - 1 )); out+="rule${UI_US}$UI_P${UI_RS}"
    fi
    UI_BAR="$out"
}

# ── окна ───────────────────────────────────────────────────────────────
# Строки содержимого окна: UI_LS (сегменты) и UI_LSEL (1 — выделена).
ui_lclear() { UI_LS=(); UI_LSEL=(); }
ui_lpush() { UI_LS+=("$1"); UI_LSEL+=("${2:-0}"); }
# ui_frame2 <W> <H> <заголовок-сегменты> <правая метка-сегменты или ""> → UI_FR
# Верх «╔═ заголовок ═══ метка ═╗», бока «║…║», низ «╚══╝». Не влезает метка — её нет.
ui_frame2() {
    local W="$1" H="$2" title="$3" right="$4" top tr fill i l s pad
    UI_FR=()
    top="grad${UI_US}╔═ ${UI_RS}$title""grad${UI_US} ${UI_RS}"
    if [ -n "$right" ]; then tr="grad${UI_US} ${UI_RS}$right""grad${UI_US} ═╗${UI_RS}"; else tr="grad${UI_US}═╗${UI_RS}"; fi
    ui_len "$top"; fill=$UI_N; ui_len "$tr"; fill=$(( W - fill - UI_N ))
    if [ "$fill" -lt 1 ]; then tr="grad${UI_US}═╗${UI_RS}"; ui_len "$top"; fill=$(( W - UI_N - 2 )); fi
    [ "$fill" -lt 0 ] && fill=0
    ui_rep '═' "$fill"
    ui_fit "$top""grad${UI_US}$UI_P${UI_RS}$tr" "$W"; UI_FR+=("$UI_FIT")
    for ((i = 0; i < H; i++)); do
        l="${UI_LS[$i]:-}"; s="${UI_LSEL[$i]:-0}"
        if [ "$s" = 1 ]; then ui_sel "$l"; l="$UI_P"; fi
        ui_fit "$l" $(( W - 2 ))
        pad=$(( W - 2 - UI_FITN ))
        if [ "$s" = 1 ]; then printf -v l '%*s' "$pad" ''; l="text+sel${UI_US}$l${UI_RS}"
        else printf -v l '%*s' "$pad" ''; l="text${UI_US}$l${UI_RS}"; fi
        UI_FR+=("grad${UI_US}║${UI_RS}$UI_FIT$l""grad${UI_US}║${UI_RS}")
    done
    ui_rep '═' $(( W - 2 )); UI_FR+=("grad${UI_US}╚${UI_P}╝${UI_RS}")
}

# ── экран ──────────────────────────────────────────────────────────────
ui_body() { UI_BODY+=("$1"); }
# Подсказка клавиш: пары «клавиша|подпись|короткая»; не влезает — короткие.
ui_keys() {
    local short="${1:-0}"; shift
    local s="text${UI_US}  ${UI_RS}" i=0 k l sh lab item
    for item in "$@"; do
        IFS='|' read -r k l sh <<<"$item"
        if [ "$short" = 1 ]; then lab="$sh"; else lab="$l"; fi
        [ "$i" -gt 0 ] && s+="muted${UI_US} · ${UI_RS}"
        s+="key${UI_US}$k${UI_RS}"
        [ -n "$lab" ] && s+="muted${UI_US} $lab${UI_RS}"
        i=$(( i + 1 ))
    done
    UI_P="$s"
}
ui_keys_fit() { ui_keys 0 "$@"; ui_len "$UI_P"; if [ "$UI_N" -gt "$UI_C" ]; then ui_keys 1 "$@"; fi; UI_KEYS="$UI_P"; }

# ui_compose → UI_ROWS: тело, добитое до UI_R строк, внизу сведения и клавиши.
# Выделенная строка тела помечается префиксом «+» и заливается до края.
ui_compose() {
    local foot=0 i row n
    UI_ROWS=()
    [ -n "$UI_INFO" ] && foot=$(( foot + 1 )); [ -n "$UI_KEYS" ] && foot=$(( foot + 1 ))
    n=$(( UI_R - foot ))
    for ((i = 0; i < n; i++)); do
        row="${UI_BODY[$i]:-}"
        if [ "${row:0:1}" = $'\x1d' ]; then
            row="${row:1}"; ui_fit "$row" "$UI_C"; row="$UI_FIT"
            ui_sel "$row"; row="$UI_P"
            printf -v UI_P '%*s' $(( UI_C - UI_FITN )) ''
            row+="text+sel${UI_US}$UI_P${UI_RS}"
        else
            ui_fit "$row" "$UI_C"; row="$UI_FIT"
        fi
        UI_ROWS+=("$row")
    done
    [ -n "$UI_INFO" ] && { ui_fit "$UI_INFO" "$UI_C"; UI_ROWS+=("$UI_FIT"); }
    if [ -n "$UI_KEYS" ]; then
        local keys="$UI_KEYS" f2
        # Подсказка про размер — только там, где выбирать есть из чего.
        if [ "${UI_COLS:-80}" -gt 80 ] && [ "${UI_LINES:-24}" -ge 24 ]; then
            ut "размер" "size"; f2="$UI_TX"
            keys+="muted${UI_US} · ${UI_RS}key${UI_US}F2${UI_RS}muted${UI_US} ${f2}${UI_RS}"
        fi
        ui_fit "$keys" "$UI_C"; UI_ROWS+=("$UI_FIT")
    fi
    return 0
}
# ui_selrow <сегменты> — выделенная строка тела (заливка до края экрана).
ui_selrow() { UI_BODY+=($'\x1d'"$1"); }

# ui_ascii <текст> → UI_P: замены по таблице, каждая в одну клетку.
ui_ascii() {
    local s="$1" k
    for k in "${!UI_ASC[@]}"; do [[ "$s" == *"$k"* ]] && s="${s//"$k"/"${UI_ASC[$k]}"}"; done
    UI_P="$s"
}

# ui_emit — напечатать UI_ROWS поверх экрана: курсор в начало, каждая строка
# гасит хвост, в конце — остаток экрана. Последняя строка без перевода:
# иначе на окне ровно в 24 строки экран прокрутится.
ui_emit() {
    local out=$'\033[H' row seg tok txt sel s col k i=0 n="${#UI_ROWS[@]}" pad="" t
    # Отступы до холста — по выбранному размеру (ui_size): по центру окна.
    for ((t = 0; t < ${UI_PADT:-0}; t++)); do out+=$'\033[K\n'; done
    [ "${UI_PADL:-0}" -gt 0 ] && printf -v pad '%*s' "$UI_PADL" ''
    for row in "${UI_ROWS[@]}"; do
        out+="$pad"
        col=0
        while [ -n "$row" ]; do
            seg="${row%%"${UI_RS}"*}"; row="${row#*"${UI_RS}"}"
            tok="${seg%%"${UI_US}"*}"; txt="${seg#*"${UI_US}"}"
            [ -n "$txt" ] || continue
            [ "$UI_ASCII" = 1 ] && { ui_ascii "$txt"; txt="$UI_P"; }
            sel=0; case "$tok" in *+sel) sel=1; tok="${tok%+sel}" ;; esac
            if [ "$tok" = grad ] && [ "$UI_GRADN" -gt 0 ]; then
                for ((k = 0; k < ${#txt}; k++)); do out+="${UI_GRAD[$(( col + k < UI_GRADN ? col + k : UI_GRADN - 1 ))]}${txt:k:1}"; done
            else
                if [ "$sel" = 1 ]; then s="${UI_SGRSEL[$tok]:-}"; else s="${UI_SGR[$tok]:-}"; fi
                out+="$s$txt"
            fi
            col=$(( col + ${#txt} ))
        done
        i=$(( i + 1 ))
        out+=$'\033[0m\033[K'
        [ "$i" -lt "$n" ] && out+=$'\n'
    done
    out+=$'\033[J'
    printf '%s' "$out"
}
# ui_dump — строки экрана для сверки: «текст␉токен:длина,…».
ui_dump() {
    local row seg tok txt text runs
    for row in "${UI_ROWS[@]}"; do
        text=""; runs=""
        while [ -n "$row" ]; do
            seg="${row%%"${UI_RS}"*}"; row="${row#*"${UI_RS}"}"
            tok="${seg%%"${UI_US}"*}"; txt="${seg#*"${UI_US}"}"
            [ -n "$txt" ] || continue
            text+="$txt"; runs+="$tok:${#txt},"
        done
        printf '%s\t%s\n' "$text" "$runs"
    done
}

# ── модель: что показывать ─────────────────────────────────────────────
# Боевой код заполняет её из системы (ui_model_load), тест — данными примера
# из пакета и так сверяет раскладку с эталоном клетка в клетку. Индекс — номер
# продукта в PRODUCTS.
UD_HOST=""; UD_OS=""; UD_VER=""; UD_VERNEW=""; UD_SVED=""; UD_DISK=0; UD_MEM=0; UD_LOAD=""
UD_NAME=(); UD_CMD=(); UD_DESC=(); UD_INST=(); UD_SRV=(); UD_MAIN=(); UD_DEV=(); UD_BRANCH=()
# UD_DEVREF — имя второй ветки (dev или develop);
# UD_UPDV — версия, до которой обновится стоящий, с его же ветки. Пусто —
# dev и UD_MAIN, как в данных примера.
UD_DEVREF=(); UD_UPDV=()
UD_UPD=(); UD_NEW=(); UD_ST=(); UD_STTXT=(); UD_MODES=(); UD_DEFMODE=(); UD_SVC=(); UD_SVCOK=(); UD_HELD=()
UD_NEED=(); UD_VBAD=(); UD_VNOTES=(); UD_VWHY=()
UD_PROB=()          # замечания: «имя|что|коротко|шаг[|err]»; err — ✗, иначе !
UD_KEYNOTE=""; UD_KEYTOK="muted"; UD_LANGNOTE=""
UI_POLL=""          # «N» — идёт опрос GitHub, ответили N из 8
UI_POLLDIR=""; UI_POLLPID=""; UI_ALT=0
UI_POLLERR=""       # github | rate — чем кончился последний опрос

# ut <ru> <en> → UI_TX. Без подоболочки: экран перерисовывается на каждое
# нажатие, и вилка на каждую строку текста ощутимо тормозит отклик.
ut() { if [ "${LANG_CODE:-ru}" = en ]; then UI_TX="$2"; else UI_TX="$1"; fi; }
_s() { UI_SEG+="$1${UI_US}$2${UI_RS}"; }   # _s <токен> <текст> — к собираемой строке

# Значок, токен и слово статуса продукта (STATUS в прототипе).
_ui_st() {
    case "${UD_ST[$1]}" in
        installed) UI_G='●'; UI_GT=ok;    ut "установлен" "installed"; UI_SW="$UI_TX" ;;
        ready)     UI_G='○'; UI_GT=muted; ut "готов" "ready"; UI_SW="$UI_TX" ;;
        notes)     UI_G='!'; UI_GT=warn;  ut "готов, есть замечания" "ready, with notes"; UI_SW="$UI_TX" ;;
        lite|free) UI_G='◆'; UI_GT=warn;  UI_SW="${UD_STTXT[$1]}" ;;
        *)         UI_G='✗'; UI_GT=err;   UI_SW="${UD_STTXT[$1]}" ;;
    esac
}
_ui_modename() {
    case "$1" in
        lazy) ut "Ленивый pro" "Lazy pro" ;;
        lite_site) UI_TX="Semi-pro" ;;
        lite) UI_TX="Lite" ;;
        *) UI_TX="$1" ;;
    esac
}
# Итог проверки портов тегом: «✗ препятствий: 2», «! замечаний: 1», «✓ готов».
_ui_verdict() {
    local bad="${UD_VBAD[$1]:-0}" notes="${UD_VNOTES[$1]:-0}"
    if [ "$bad" -gt 0 ]; then ut "препятствий: " "obstacles: "; UI_VT="✗ $UI_TX$bad"; UI_VK=err
    elif [ "$notes" -gt 0 ]; then ut "замечаний: " "notes: "; UI_VT="! $UI_TX$notes"; UI_VK=warn
    else ut "готов" "ready"; UI_VT="✓ $UI_TX"; UI_VK=ok; fi
}

# ── общие строки ───────────────────────────────────────────────────────
# Строка состояния наверху: имя и сервер слева, диск, память, нагрузка справа.
ui_hud() {
    local n=0 left right pm tok h="$UD_HOST" room lw
    [ "$UI_C" -lt 60 ] && n=1
    UI_SEG=""
    _ui_meter() {  # _ui_meter <подпись> <проценты> <ширина>
        pm=$(( $2 * 10 )); if [ "$2" -lt 70 ]; then tok=ok; elif [ "$2" -lt 90 ]; then tok=warn; else tok=err; fi
        _s muted "$1 "; _s rule "▕"; ui_bar "$pm" "$3" "$tok"; UI_SEG+="$UI_BAR"; _s rule "▏"; _s text "$2 %"
    }
    ut "диск" "disk"; _ui_meter "$UI_TX" "$UD_DISK" $(( n ? 4 : 6 ))
    if [ "$n" = 1 ]; then ut "озу" "mem"; _s muted "  $UI_TX "; _s text "$UD_MEM % "
    else _s text "  "; ut "озу" "mem"; _ui_meter "$UI_TX" "$UD_MEM" 6; ut "нагр" "load"; _s muted "  $UI_TX "; _s text "$UD_LOAD "; fi
    right="$UI_SEG"
    # Длинное имя машины (у облаков это обычно: ubuntu-s-1vcpu-1gb-fra1-01)
    # сокращаем с «…»: иначе ui_spread отбросил бы диск, память и нагрузку
    # целиком.
    UI_SEG=""; _s title " goInstaller"; _s muted " $UD_VER"
    if [ "$n" = 0 ]; then
        ui_len "$UI_SEG"; lw=$UI_N; ui_len "$right"
        room=$(( UI_C - lw - 5 - UI_N - 2 ))
        if [ "${#h}" -gt "$room" ]; then if [ "$room" -ge 4 ]; then h="${h:0:$(( room - 1 ))}…"; else h=""; fi; fi
        [ -n "$h" ] && _s text "  ·  $h"
    fi
    left="$UI_SEG"
    ui_spread "$left" "$right" "$UI_C"; ui_body "$UI_P"
}
# Заголовок экранов без окон: «  goInstaller 0.9.5 …… vpn-fin-1» и черта.
ui_hdr() {  # ui_hdr <левое-сегменты> [правая подпись]
    local right=""
    [ -n "${2:-}" ] && right="muted${UI_US}$2${UI_RS}"
    ui_spread "text${UI_US}  ${UI_RS}$1" "$right" "$UI_C"; ui_fit "$UI_P" "$UI_C"; ui_body "$UI_FIT"
    ui_rep '═' $(( UI_C - 4 )); ui_body "grad${UI_US}  $UI_P${UI_RS}"
}
ui_main_title() { UI_SEG=""; _s title "goInstaller"; _s muted " $UD_VER"; UI_MT="$UI_SEG"; }

# Сведения под меню: опрос идёт, GitHub молчит, лимит или время последнего опроса.
ui_menu_info() {
    UI_SEG=""
    if [ -n "$UI_POLL" ]; then
        _s cursor "  ${UI_SPIN:-⠋}"; ut " опрос GitHub: " " GitHub poll: "; local a="$UI_TX"; ut " из 8" " of 8"; _s muted "$a$UI_POLL$UI_TX"
    elif [ "$UI_POLLERR" = github ]; then
        _s err "  ✗ "; ut "GitHub недоступен · сведения от " "GitHub unreachable · data as of "; _s muted "$UI_TX$UD_SVED"
    elif [ "$UI_POLLERR" = rate ]; then
        _s warn "  ! "; ut "лимит GitHub, повтор в ${UI_RESET:-?} · сведения от " "GitHub rate limit, retry at ${UI_RESET:-?} · data as of "; _s muted "$UI_TX$UD_SVED"
    else
        ut "сведения от " "data as of "; local a="$UI_TX"
        if [ "$UI_C" -lt 60 ]; then _s muted "  $a$UD_SVED"
        else ut " · обновляются сами раз в 3 мин" " · refresh every 3 min by themselves"; _s muted "  $a$UD_SVED$UI_TX"; fi
    fi
    UI_INFO="$UI_SEG"
}
ui_menu_keys() {
    local a=() k1 k2
    ut "сразу" "jump"; k1="$UI_TX"
    a=("↑↓||" "Enter||" "1–9|$k1|")
    if [ -n "$UD_VERNEW" ]; then ut "обновить" "update"; a+=("u|$UI_TX|"); fi
    ut "порты" "ports"; a+=("p|$UI_TX|"); ut "ключ" "key"; a+=("r|$UI_TX|"); ut "язык" "language"; a+=("l|$UI_TX|")
    ut "выход" "quit"; a+=("0|$UI_TX|$UI_TX")
    ui_keys_fit "${a[@]}"
}
# Замечания под меню: полная строка на 80 колонках, короткая на 46.
ui_alerts() {  # ui_alerts <80|46>
    local p name msg short step sev g t
    for p in "${UD_PROB[@]}"; do
        IFS='|' read -r name msg short step sev <<<"$p"
        if [ "$sev" = err ]; then g='✗'; t=err; else g='!'; t=warn; fi
        UI_SEG=""
        if [ "$1" = 80 ]; then _s "$t" " $g "; _s text "$name: $msg"; _s muted " → "; _s key "$step"
        else _s "$t" "  $g "; _s text "$short"; fi
        ui_body "$UI_SEG"
    done
}

# ── пункты главного меню ───────────────────────────────────────────────
ui_menu_items() {
    local i
    MI_KIND=(); MI_KEY=(); MI_HOT=(); MI_LABEL=(); MI_NOTE=(); MI_NTOK=(); MI_TOK=(); MI_P=()
    _mi() { MI_KIND+=("$1"); MI_KEY+=("$2"); MI_HOT+=("$3"); MI_LABEL+=("$4"); MI_NOTE+=("$5"); MI_NTOK+=("${6:-muted}"); MI_TOK+=("${7:-}"); MI_P+=("${8:--1}"); }
    for ((i = 0; i < ${#UD_NAME[@]}; i++)); do _mi p "$(( i + 1 ))" "" "${UD_NAME[$i]}" "" muted "" "$i"; done
    local a b
    ut "Карта портов" "Port map"; a="$UI_TX"; ut "кто какие порты держит" "who holds which ports"; _mi s 7 p "$a" "$UI_TX"
    ut "Ключ доступа" "Access key"; _mi s 8 r "$UI_TX" "$UD_KEYNOTE" "$UD_KEYTOK"
    ut "Язык" "Language"; _mi s 9 l "$UI_TX" "$UD_LANGNOTE"
    if [ -n "$UD_VERNEW" ]; then ut "Обновить goInstaller" "Update goInstaller"; _mi s u "" "$UI_TX" "$UD_VER → $UD_VERNEW" update update; fi
    ut "Удалить goInstaller" "Remove goInstaller"; _mi s x "" "$UI_TX" ""
    ut "Выход" "Quit"; _mi s 0 q "$UI_TX" ""
}

# Левое окно «ПРОДУКТЫ» (arcadeList): продукты, черта, служебные пункты.
ui_arcade_list() {  # ui_arcade_list <cur> <W> → UI_LS/UI_LSEL
    local cur="$1" W="$2" i n="${#MI_KEY[@]}" sel p g gt
    ui_lclear
    for ((i = 0; i < n; i++)); do
        sel=0; [ "$i" = "$cur" ] && sel=1
        UI_SEG=""; _s text " "
        if [ "$sel" = 1 ]; then _s cursor "▸ "; _s cursor "${MI_KEY[$i]} "; else _s cursor "  "; _s muted "${MI_KEY[$i]} "; fi
        if [ "${MI_KIND[$i]}" = p ]; then
            p="${MI_P[$i]}"
            if [ "${UD_UPD[$p]:-0}" = 1 ]; then g="↑ ${UD_UPDV[$p]:-${UD_MAIN[$p]}}"; gt=update
            elif [ "${UD_NEW[$p]:-0}" = 1 ]; then ut "новое" "new"; g="✚ $UI_TX"; gt=new
            elif [ "${UD_ST[$p]}" = free ]; then ut "бесплатный" "free"; g="◆ $UI_TX"; gt=warn
            elif [ "${UD_INST[$p]:-0}" = 1 ]; then g="● ${UD_SRV[$p]}"; gt=ok
            else case "${UD_ST[$p]}" in
                notes) ut "замечания" "notes"; g="! $UI_TX"; gt=warn ;;
                lite)  g="◆ ${UD_STTXT[$p]}"; gt=warn ;;
                block) ut "нельзя" "blocked"; g="✗ $UI_TX"; gt=err ;;
                *)     ut "готов" "ready"; g="○ $UI_TX"; gt=muted ;;
            esac; fi
            ui_padr "${MI_LABEL[$i]}" 16
            if [ "$sel" = 1 ]; then _s title "$UI_P"; else _s name "$UI_P"; fi
            _s "$gt" "$g"
        else
            ui_padr "${MI_LABEL[$i]}" $(( W - 11 ))
            if [ "$sel" = 1 ]; then _s title "$UI_P"; else _s "${MI_TOK[$i]:-name}" "$UI_P"; fi
            _s key "${MI_HOT[$i]}"
        fi
        ui_lpush "$UI_SEG" "$sel"
        if [ "$i" = 5 ]; then ui_rep '─' $(( W - 4 )); ui_lpush "rule${UI_US} $UI_P${UI_RS}" 0; fi
    done
}

# Карточка выбранного пункта (arcadeCard). «ЧТО НОВОГО» заказчик убрал.
ui_arcade_card() {  # ui_arcade_card <пункт> <IW> <H> [в меню продукта] → UI_LS, UI_CT, UI_CR
    local it="$1" IW="$2" H="$3" inprod="${4:-0}" p l i foot u
    ui_lclear
    _P() { ui_lpush "text${UI_US} ${UI_RS}$1" 0; }
    if [ "${MI_KIND[$it]}" = p ]; then
        p="${MI_P[$it]}"; _ui_st "$p"
        UI_CT="title${UI_US}${UD_NAME[$p]}${UI_RS}"
        local wt="$UI_GT"; [ "$wt" = muted ] && wt=text
        UI_CR="$UI_GT${UI_US}$UI_G ${UI_RS}$wt${UI_US}$UI_SW${UI_RS}"
        ui_wrap "${UD_DESC[$p]}" $(( IW - 2 ))
        for ((i = 0; i < ${#UI_WRAP[@]} && i < 2; i++)); do _P "muted${UI_US}${UI_WRAP[$i]}${UI_RS}"; done
        _P ""
        ut "ВЕТКА" "BRANCH"; ui_padr "$UI_TX" 8; l="$UI_P"; ut "СЕРВЕР" "SERVER"; ui_padr "$UI_TX" 10; _P "muted${UI_US}$l${UI_P}GITHUB${UI_RS}"
        local inst="${UD_INST[$p]:-0}" br="${UD_BRANCH[$p]:-main}" upd="${UD_UPD[$p]:-0}" new="${UD_NEW[$p]:-0}" dr="${UD_DEVREF[$p]:-dev}"
        # Обновление — на строке той ветки, с которой стоит.
        local upm=0 upd2=0; [ "$upd" = 1 ] && { if [ "$br" = "$dr" ] && [ -n "${UD_DEV[$p]:-}" ]; then upd2=1; else upm=1; fi; }
        UI_SEG=""
        ui_padr main 8; if [ "$inst" = 1 ] && [ "$br" = main ]; then _s title "$UI_P"; else _s text "$UI_P"; fi
        if [ "$inst" = 1 ] && [ "$br" = main ]; then ui_padr "${UD_SRV[$p]}" 10; else ui_padr "—" 10; fi
        if [ "$upm" = 1 ]; then _s update "$UI_P"; else _s text "$UI_P"; fi
        ui_padr "${UD_MAIN[$p]:-—}" 9
        if [ "$upm" = 1 ]; then _s update "$UI_P"; ut "новее" "newer"; _s update "↑ $UI_TX"
        elif [ "$new" = 1 ]; then _s new "$UI_P"; ut "новое" "new"; _s new "✚ $UI_TX"
        else _s text "$UI_P"; fi
        _P "$UI_SEG"
        UI_SEG=""
        ui_padr "$dr" 8; if [ "$inst" = 1 ] && [ "$br" = "$dr" ]; then _s title "$UI_P"; else _s text "$UI_P"; fi
        if [ "$inst" = 1 ] && [ "$br" = "$dr" ]; then ui_padr "${UD_SRV[$p]}" 10; else ui_padr "—" 10; fi
        _s text "$UI_P"
        if [ "$upd2" = 1 ]; then ui_padr "${UD_DEV[$p]}" 9; _s update "$UI_P"; ut "новее" "newer"; _s update "↑ $UI_TX"
        elif [ -n "${UD_DEV[$p]:-}" ]; then _s muted "${UD_DEV[$p]}"; else ut "нет ветки" "no branch"; _s muted "$UI_TX"; fi
        _P "$UI_SEG"
        _P ""
        if [ "$inst" = 1 ]; then
            ui_wrap "${UD_HELD[$p]:-—}" $(( IW - 10 ))
            for ((i = 0; i < ${#UI_WRAP[@]}; i++)); do
                if [ "$i" = 0 ]; then ut "ПОРТЫ" "PORTS"; ui_padr "$UI_TX" 8; else ui_padr "" 8; fi
                _P "muted${UI_US}$UI_P${UI_RS}text${UI_US}${UI_WRAP[$i]}${UI_RS}"
            done
            UI_SEG=""; ut "СЛУЖБЫ" "UNITS"; ui_padr "$UI_TX" 8; _s muted "$UI_P"
            local k=0 ok
            for u in ${UD_SVC[$p]:-}; do
                ok=1; case " ${UD_SVCOK[$p]:-} " in *" $u:0 "*) ok=0 ;; *" $u:2 "*) ok=2 ;; esac
                local sp=""; [ "$k" -gt 0 ] && sp="  "
                case "$ok" in 1) _s ok "${sp}● " ;; 2) _s muted "${sp}○ " ;; *) _s err "${sp}✗ " ;; esac
                _s text "$u"; k=$(( k + 1 ))
            done
            [ "$k" -gt 0 ] || _s text "—"
            _P "$UI_SEG"
        else
            ui_wrap "${UD_NEED[$p]:-—}" $(( IW - 10 ))
            for ((i = 0; i < ${#UI_WRAP[@]} && i < 2; i++)); do
                if [ "$i" = 0 ]; then ut "НУЖНЫ" "NEEDS"; ui_padr "$UI_TX" 8; else ui_padr "" 8; fi
                _P "muted${UI_US}$UI_P${UI_RS}text${UI_US}${UI_WRAP[$i]}${UI_RS}"
            done
            UI_SEG=""; ut "ИТОГ" "RESULT"; ui_padr "$UI_TX" 8; _s muted "$UI_P"
            _ui_verdict "$p"; _s "$UI_VK" "$UI_VT"
            if [ -n "${UD_MODES[$p]:-}" ]; then _ui_modename "${UD_DEFMODE[$p]}"; _s muted " · $UI_TX"; fi
            _P "$UI_SEG"
            if [ -n "${UD_VWHY[$p]:-}" ] && [ "$UI_VK" != ok ]; then
                ui_wrap "${UD_VWHY[$p]}" $(( IW - 10 ))
                for ((i = 0; i < ${#UI_WRAP[@]} && i < 4; i++)); do ui_padr "" 8; _P "muted${UI_US}$UI_P${UI_RS}${UI_VK}${UI_US}${UI_WRAP[$i]}${UI_RS}"; done
            fi
        fi
        UI_SEG=""
        if [ "$inprod" = 1 ]; then ut "команда " "command "; _s muted "$UI_TX"; _s key "${UD_CMD[$p]}"
        else
            _s key "Enter"
            if [ "$inst" = 1 ] && [ "${UD_ST[$p]}" != free ]; then ut " — меню продукта" " — product menu"; else ut " — установка" " — install"; fi
            _s muted "$UI_TX"; _s rule "  ·  "; _s key "${UD_CMD[$p]}"
        fi
        foot="$UI_SEG"
    else
        UI_CT="${MI_TOK[$it]:-title}${UI_US}${MI_LABEL[$it]}${UI_RS}"; UI_CR=""
        if [ -n "${MI_NOTE[$it]}" ]; then
            ui_wrap "${MI_NOTE[$it]}" $(( IW - 2 ))
            for l in "${UI_WRAP[@]}"; do _P "${MI_NTOK[$it]:-muted}${UI_US}$l${UI_RS}"; done
        fi
        _P ""
        case "${MI_KEY[$it]}" in
            7) local q h port proc
               for ((q = 0; q < ${#UD_NAME[@]}; q++)); do
                   [ "${UD_INST[$q]:-0}" = 1 ] || continue
                   local c=0
                   for h in ${UD_HELDP[$q]:-}; do
                       [ "$c" -lt 2 ] || break
                       port="${h%%=*}"; proc="${h#*=}"
                       ui_padr "$port" 10; l="$UI_P"; ui_padr "$proc" 9
                       _P "text${UI_US}$l${UI_RS}muted${UI_US}$UI_P${UI_RS}muted${UI_US}${UD_NAME[$q]}${UI_RS}"
                       c=$(( c + 1 ))
                   done
               done ;;
            8) _P "muted${UI_US}${TOKEN_FILE:-/opt/go-installer/token}${UI_RS}"; ut "режим 0600, только root" "mode 0600, root only"; _P "muted${UI_US}$UI_TX${UI_RS}" ;;
            9) _P "key${UI_US}1${UI_RS}text${UI_US} Русский   ${UI_RS}key${UI_US}2${UI_RS}text${UI_US} English${UI_RS}" ;;
            u) ut "ветка main · " "branch main · "; _P "muted${UI_US}$UI_TX${UI_RS}text${UI_US}go-installer.sh${UI_RS}" ;;
            0) ut "вернуться в консоль" "back to the shell"; _P "muted${UI_US}$UI_TX${UI_RS}" ;;
        esac
        UI_SEG=""
        if [ -n "${MI_HOT[$it]}" ]; then
            _s key "Enter"; ut " или " " or "; _s muted "$UI_TX"; _s key "${MI_HOT[$it]}"; ut " с любого экрана" " from any screen"; _s muted "$UI_TX"
        else _s key "Enter"; ut " — открыть" " — open"; _s muted "$UI_TX"; fi
        foot="$UI_SEG"
    fi
    while [ "${#UI_LS[@]}" -lt $(( H - 1 )) ]; do ui_lpush "" 0; done
    UI_LS=("${UI_LS[@]:0:$(( H - 1 ))}"); UI_LSEL=("${UI_LSEL[@]:0:$(( H - 1 ))}")
    ui_lpush "text${UI_US} ${UI_RS}$foot" 0
}

# Главное меню. От 80 колонок — два окна (arcadeMenu), уже — список (g1).
ui_scr_menu() {  # ui_scr_menu <cur>
    local cur="$1" i LW=37 RW H=16 lf rf p
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_menu_items
    if [ "$UI_C" -ge 80 ]; then
        RW=$(( UI_C - LW - 2 ))
        ui_hud
        ui_arcade_list "$cur" "$LW"
        ut "ПРОДУКТЫ" "PRODUCTS"; ui_frame2 "$LW" "$H" "title${UI_US}$UI_TX${UI_RS}" ""; lf=("${UI_FR[@]}")
        ui_arcade_card "$cur" $(( RW - 2 )) "$H"
        ui_frame2 "$RW" "$H" "$UI_CT" "$UI_CR"; rf=("${UI_FR[@]}")
        for ((i = 0; i < H + 2; i++)); do
            if [ "$i" = 0 ]; then p=" "; else p="░"; fi
            ui_body "${lf[$i]}shadow${UI_US}$p${UI_RS}${rf[$i]}shadow${UI_US}$p${UI_RS}"
        done
        ui_rep '░' "$LW"; lf="$UI_P"; ui_rep '░' "$RW"
        ui_body "text${UI_US} ${UI_RS}""shadow${UI_US}$lf${UI_RS}""text${UI_US} ${UI_RS}""shadow${UI_US}$UI_P${UI_RS}"
        ui_alerts 80
    else
        ui_main_title; ui_hdr "$UI_MT" "$UD_HOST"
        for ((i = 0; i < ${#MI_KEY[@]}; i++)); do
            UI_SEG=""
            if [ "$i" = "$cur" ]; then _s cursor "▸ "; _s cursor "${MI_KEY[$i]} "; else _s cursor "  "; _s muted "${MI_KEY[$i]} "; fi
            if [ "${MI_KIND[$i]}" = p ]; then
                p="${MI_P[$i]}"; _ui_st "$p"
                ui_padr "${MI_LABEL[$i]}" 15
                if [ "$i" = "$cur" ]; then _s title "$UI_P"; else _s name "$UI_P"; fi
                if [ "${UD_UPD[$p]:-0}" = 1 ]; then _s update "↑"; _s text " "; _s update "${UD_SRV[$p]} → ${UD_UPDV[$p]:-${UD_MAIN[$p]}}"
                elif [ "${UD_NEW[$p]:-0}" = 1 ]; then _s new "✚"; _s text " "; ut "новое · " "new · "; _s new "$UI_TX$UI_SW"
                elif [ "${UD_INST[$p]:-0}" = 1 ]; then _s "$UI_GT" "$UI_G"; _s text " "; _s text "$UI_SW ${UD_SRV[$p]}"
                else _s "$UI_GT" "$UI_G"; _s text " "; if [ "${UD_ST[$p]}" = ready ]; then _s text "$UI_SW"; else _s warn "$UI_SW"; fi; fi
            else
                if [ "${MI_KEY[$i]}" = 9 ]; then ui_padr "${MI_LABEL[$i]} · ${MI_NOTE[$i]}" 34; else ui_padr "${MI_LABEL[$i]}" 34; fi
                if [ "$i" = "$cur" ]; then _s title "$UI_P"; else _s "${MI_TOK[$i]:-name}" "$UI_P"; fi
                _s key "${MI_HOT[$i]}"
            fi
            if [ "$i" = "$cur" ]; then ui_selrow "$UI_SEG"; else ui_body "$UI_SEG"; fi
            if [ "$i" = 5 ]; then
                ui_body ""
                if [ "${MI_KIND[$cur]}" = p ]; then ui_body "muted${UI_US}  ${UD_DESC[${MI_P[$cur]}]}${UI_RS}"
                else ui_body "${MI_NTOK[$cur]:-muted}${UI_US}  ${MI_NOTE[$cur]}${UI_RS}"; fi
                ui_body ""
            fi
        done
        ui_body ""
        ui_alerts 46
    fi
    ui_menu_info; ui_menu_keys
}

# Окно слишком мало: 40 × 16, подсказка и выход.
ui_scr_small() {
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ut "окно $UI_COLS × $UI_LINES" "window $UI_COLS × $UI_LINES"; ui_body "title${UI_US}goinstall: $UI_TX${UI_RS}"; ui_body ""
    ut "Нужно не меньше 46 × 24." "Needs at least 46 × 24."; ui_body "text${UI_US}$UI_TX${UI_RS}"
    ut "Растяните окно или уменьшите шрифт." "Enlarge the window or shrink the font."; ui_body "muted${UI_US}$UI_TX${UI_RS}"; ui_body ""
    ut "Без меню: " "Without the menu: "; ui_body "muted${UI_US}$UI_TX${UI_RS}key${UI_US}goinstall --list${UI_RS}"; ui_body ""
    ut " — выход" " — quit"; ui_body "key${UI_US}q${UI_RS}muted${UI_US}$UI_TX${UI_RS}"
}

# ── списки выбора ──────────────────────────────────────────────────────
# Пункты экрана: ключ, подпись, пояснение, тег («✗ препятствий: 2»), действие.
ui_items_clear() { IT_KEY=(); IT_LABEL=(); IT_NOTE=(); IT_NTOK=(); IT_TOK=(); IT_TAG=(); IT_TAGT=(); IT_ACT=(); }
ui_item() {  # ui_item <ключ> <подпись> <действие> [пояснение] [токен пояснения] [тег] [токен тега] [токен подписи]
    IT_KEY+=("$1"); IT_LABEL+=("$2"); IT_ACT+=("$3"); IT_NOTE+=("${4:-}"); IT_NTOK+=("${5:-muted}")
    IT_TAG+=("${6:-}"); IT_TAGT+=("${7:-}"); IT_TOK+=("${8:-}")
}
ui_item_back() { ut "Назад" "Back"; ui_item 0 "$UI_TX" back; }

# Пункты в окне (optLines): «▸ 1  Подпись», пояснение под пунктом или справа.
ui_optlines() {  # ui_optlines <cur> <W> <below 0|1> [ширина подписи]
    local cur="$1" W="$2" below="$3" lw="${4:-0}" i sel lab tok l tg
    for ((i = 0; i < ${#IT_KEY[@]}; i++)); do
        sel=0; [ "$i" = "$cur" ] && sel=1
        UI_SEG=""; _s text " "
        if [ "$sel" = 1 ]; then _s cursor "▸ "; _s cursor "${IT_KEY[$i]}  "; else _s cursor "  "; _s key "${IT_KEY[$i]}  "; fi
        if [ "$sel" = 1 ]; then tok=title; else tok="${IT_TOK[$i]:-name}"; fi
        if [ "$below" = 1 ]; then
            _s "$tok" "${IT_LABEL[$i]}"; tg=0
            if [ -n "${IT_TAG[$i]}" ] && [ $(( ${#IT_LABEL[$i]} + 9 + ${#IT_TAG[$i]} )) -le "$W" ]; then _s text "   "; _s "${IT_TAGT[$i]}" "${IT_TAG[$i]}"; tg=1; fi
            ui_lpush "$UI_SEG" "$sel"
            if [ -n "${IT_NOTE[$i]}" ]; then
                ui_wrap "${IT_NOTE[$i]}" $(( W - 7 ))
                for l in "${UI_WRAP[@]}"; do ui_lpush "text${UI_US}      ${UI_RS}muted${UI_US}${l}${UI_RS}" 0; done
            fi
            if [ -n "${IT_TAG[$i]}" ] && [ "$tg" = 0 ]; then ui_lpush "text${UI_US}      ${UI_RS}${IT_TAGT[$i]}${UI_US}${IT_TAG[$i]}${UI_RS}" 0; fi
        else
            if [ "$lw" -gt 0 ] && [ -n "${IT_NOTE[$i]}" ]; then ui_padr "${IT_LABEL[$i]}" "$lw"; _s "$tok" "$UI_P"; _s "${IT_NTOK[$i]:-muted}" "${IT_NOTE[$i]}"
            else _s "$tok" "${IT_LABEL[$i]}"; fi
            ui_lpush "$UI_SEG" "$sel"
        fi
    done
}
# Пункты без окна (choiceRows): экраны ошибок и «нет ключа».
ui_choicerows() {  # ui_choicerows <cur> [below 0|1]
    local cur="$1" below="${2:-0}" lw i sel tok l
    if [ "$UI_C" -ge 80 ]; then lw=32; else lw=26; fi
    for ((i = 0; i < ${#IT_KEY[@]}; i++)); do
        sel=0; [ "$i" = "$cur" ] && sel=1
        UI_SEG=""; _s text "  "
        if [ "$sel" = 1 ]; then _s cursor "▸ "; _s cursor "${IT_KEY[$i]}  "; tok=title; else _s cursor "  "; _s key "${IT_KEY[$i]}  "; tok="${IT_TOK[$i]:-name}"; fi
        if [ -n "${IT_NOTE[$i]}" ] && [ "$UI_C" -ge 60 ]; then ui_padr "${IT_LABEL[$i]}" "$lw"; _s "$tok" "$UI_P"; _s "${IT_NTOK[$i]:-muted}" "${IT_NOTE[$i]}"
        else _s "$tok" "${IT_LABEL[$i]}"; fi
        if [ "$sel" = 1 ]; then ui_selrow "$UI_SEG"; else ui_body "$UI_SEG"; fi
    done
}
ui_keys_nav() {
    local a b c
    ut "выбрать" "move"; a="$UI_TX"; ut "выбрать" "select"; b="$UI_TX"; ut "цифра" "digit"; c="$UI_TX"; ut "сразу" "jumps"
    local d="$UI_TX"; ut "назад" "back"
    ui_keys_fit "↑↓|$a|" "Enter|$b|" "$c|$d|" "0|$UI_TX|$UI_TX"
}

# ── мастер: ступени ────────────────────────────────────────────────────
# Ступени строятся по продукту: Режим (если есть) → Проверка → Ветка (если
# есть dev) → Установка → Сверка.
ui_wiz_steps() {  # ui_wiz_steps <продукт> → UI_STEPS
    local p="$1"
    UI_STEPS=()
    [ -n "${UD_MODES[$p]:-}" ] && { ut "Режим" "Mode"; UI_STEPS+=("$UI_TX"); }
    ut "Проверка" "Check"; UI_STEPS+=("$UI_TX")
    [ -n "${UD_DEV[$p]:-}" ] && { ut "Ветка" "Branch"; UI_STEPS+=("$UI_TX"); }
    ut "Установка" "Install"; UI_STEPS+=("$UI_TX"); ut "Сверка" "Verify"; UI_STEPS+=("$UI_TX")
}
ui_step_idx() {  # ui_step_idx <ru> <en> → UI_I — номер ступени
    local i; ut "$1" "$2"; UI_I=0
    for ((i = 0; i < ${#UI_STEPS[@]}; i++)); do [ "${UI_STEPS[$i]}" = "$UI_TX" ] && UI_I=$i; done
}
# Строка ступеней над окном: полная от 60 колонок, «Шаг 2 из 5 · …» уже.
ui_steps_row() {  # ui_steps_row <cur> → UI_P
    local cur="$1" i n="${#UI_STEPS[@]}"
    UI_SEG=""
    if [ "$UI_C" -lt 60 ]; then
        ut "Шаг " "Step "; local a="$UI_TX"; ut " из " " of "
        _s muted "  $a$(( cur + 1 ))$UI_TX$n · "; _s title "${UI_STEPS[$cur]}"
        [ $(( cur + 1 )) -lt "$n" ] && _s muted " → ${UI_STEPS[$(( cur + 1 ))]}"
    else
        _s text "  "
        for ((i = 0; i < n; i++)); do
            [ "$i" -gt 0 ] && _s muted " → "
            if [ "$i" -lt "$cur" ]; then _s ok "✓ "; _s text "${UI_STEPS[$i]}"
            elif [ "$i" = "$cur" ]; then _s cursor "● "; _s title "${UI_STEPS[$i]}"
            else _s muted "○ "; _s muted "${UI_STEPS[$i]}"; fi
        done
    fi
    UI_P="$UI_SEG"
}
# Левое окно мастера «УСТАНОВКА» (stepsL): ступени, выбранное, продукт.
ui_build_steps() {  # ui_build_steps <W>  (UI_WP — продукт, UI_WIDX — текущая ступень)
    local W="$1" p="$UI_WP" idx="$UI_WIDX" i n="${#UI_STEPS[@]}" v m b
    ut "Режим" "Mode"; m="$UI_TX"; ut "Ветка" "Branch"; b="$UI_TX"
    ui_lclear
    for ((i = 0; i < n; i++)); do
        UI_SEG=""; _s text " "
        if [ "$i" -lt "$idx" ]; then _s ok "✓ "; elif [ "$i" = "$idx" ]; then _s cursor "● "; else _s muted "○ "; fi
        ui_padr "${UI_STEPS[$i]}" 11
        if [ "$i" = "$idx" ]; then _s title "$UI_P"; elif [ "$i" -lt "$idx" ]; then _s text "$UI_P"; else _s muted "$UI_P"; fi
        v=""
        if [ "${UI_STEPS[$i]}" = "$m" ] && [ -n "${UI_PMODE:-}" ]; then _ui_modename "$UI_PMODE"; v="$UI_TX"; fi
        if [ "${UI_STEPS[$i]}" = "$b" ] && [ -n "${UI_PBRANCH:-}" ]; then v="$UI_PBRANCH"; fi
        _s muted "$v"
        ui_lpush "$UI_SEG" 0
        if [ $(( i + 1 )) -lt "$n" ]; then if [ "$i" -lt "$idx" ]; then ui_lpush "ok${UI_US} │${UI_RS}" 0; else ui_lpush "rule${UI_US} │${UI_RS}" 0; fi; fi
    done
    ui_lpush "" 0
    ui_rep '─' $(( W - 2 )); ui_lpush "rule${UI_US} ${UI_P}${UI_RS}" 0
    ui_lpush "name${UI_US} ${UD_NAME[$p]}${UI_RS}" 0
    _ui_st "$p"
    v="$UI_SW"; [ "${UD_INST[$p]:-0}" = 1 ] && v+=" ${UD_SRV[$p]}"
    ui_lpush "${UI_GT}${UI_US} ${UI_G} ${UI_RS}muted${UI_US}${v}${UI_RS}" 0
}

# ── два окна или одно (shell2) ─────────────────────────────────────────
# Переменные раскладки: SH_LW, SH_LTITLE, SH_LLIST, SH_LBUILD, SH_STEPS46,
# SH_RTITLE, SH_RRIGHT, SH_RRIGHT46, SH_RBUILD, SH_RBUILD46, SH_WIDE.
# Сборщики — имена функций, которые наполняют UI_LS по ширине.
ui_shell2() {
    local i H LW RW p lf rf W main=() mains=() nb H2
    ui_hud
    if [ "$UI_C" -ge 80 ] && [ "${SH_WIDE:-0}" = 0 ]; then
        # На растянутом холсте левое окно получает шестую часть прироста
        # ширины, остальное — правому, где длинные описания и таблицы.
        H=$(( 17 + UI_R - 24 )); LW=$(( SH_LW + (UI_C - 80) / 6 )); RW=$(( UI_C - LW - 2 ))
        "$SH_LBUILD" $(( LW - 2 )); ui_frame2 "$LW" "$H" "$SH_LTITLE" "${SH_LRIGHT:-}"; lf=("${UI_FR[@]}")
        "$SH_RBUILD" $(( RW - 2 )); ui_frame2 "$RW" "$H" "$SH_RTITLE" "${SH_RRIGHT:-}"; rf=("${UI_FR[@]}")
        for ((i = 0; i < H + 2; i++)); do
            if [ "$i" = 0 ]; then p=" "; else p="░"; fi
            ui_body "${lf[$i]}shadow${UI_US}${p}${UI_RS}${rf[$i]}shadow${UI_US}${p}${UI_RS}"
        done
        ui_rep '░' "$LW"; lf="$UI_P"; ui_rep '░' "$RW"
        ui_body "text${UI_US} ${UI_RS}shadow${UI_US}${lf}${UI_RS}text${UI_US} ${UI_RS}shadow${UI_US}${UI_P}${UI_RS}"
    else
        W=$(( UI_C - 1 ))
        [ -n "${SH_STEPS46:-}" ] && ui_body "$SH_STEPS46"
        "${SH_RBUILD46:-$SH_RBUILD}" $(( W - 2 )); main=("${UI_LS[@]}"); mains=("${UI_LSEL[@]}")
        if [ "${SH_LLIST:-0}" = 1 ] && [ "${SH_WIDE:-0}" = 0 ]; then
            if [ "${#main[@]}" -gt 0 ]; then ui_rep '─' $(( W - 4 )); main+=("rule${UI_US} ${UI_P}${UI_RS}"); mains+=(0); fi
            "$SH_LBUILD" $(( W - 2 )); main+=("${UI_LS[@]}"); mains+=("${UI_LSEL[@]}")
        fi
        nb="${#UI_BODY[@]}"
        H2=$(( UI_R - 2 - nb - 3 - ${SH_EXTRA:-0} )); [ "${#main[@]}" -lt "$H2" ] && H2="${#main[@]}"; [ "$H2" -lt 1 ] && H2=1
        UI_LS=("${main[@]}"); UI_LSEL=("${mains[@]}")
        ui_frame2 "$W" "$H2" "$SH_RTITLE" "${SH_RRIGHT46:-}"
        for ((i = 0; i < ${#UI_FR[@]}; i++)); do
            if [ "$i" = 0 ]; then p=" "; else p="░"; fi
            ui_body "${UI_FR[$i]}shadow${UI_US}${p}${UI_RS}"
        done
        ui_rep '░' "$W"; ui_body "text${UI_US} ${UI_RS}shadow${UI_US}${UI_P}${UI_RS}"
    fi
    SH_LRIGHT=""; SH_RBUILD46=""; SH_WIDE=0; SH_STEPS46=""; SH_LLIST=0; SH_RRIGHT=""; SH_RRIGHT46=""; SH_EXTRA=0
}

# ── Д. меню продукта ───────────────────────────────────────────────────
ui_items_product() {  # ui_items_product <продукт>
    local p="$1" a
    ui_items_clear
    if [ "${UD_ST[$p]}" = free ]; then
        # На месте продукта — бесплатная версия из инструкции: первым пунктом
        # pro ставится поверх, её собственное меню отсюда не открываем.
        ut "Поставить ${UD_NAME[$p]} поверх ${UD_STTXT[$p]#* }" "Install ${UD_NAME[$p]} over ${UD_STTXT[$p]#* }"
        ui_item 1 "$UI_TX" wizard "main ${UD_MAIN[$p]:-?}" muted
        ut "Проверить порты" "Check ports"; ui_item 2 "$UI_TX" check
    elif [ "${UD_INST[$p]:-0}" = 1 ]; then
        ut "Открыть меню продукта" "Open the product menu"; ui_item 1 "$UI_TX" openprod "${UD_CMD[$p]}" key
        ut "Обновить или переустановить" "Update or reinstall"; a="$UI_TX"
        if [ "${UD_UPD[$p]:-0}" = 1 ]; then ut " в ${UD_BRANCH[$p]:-main}" " in ${UD_BRANCH[$p]:-main}"; ui_item 2 "$a" wizard "↑ ${UD_UPDV[$p]:-${UD_MAIN[$p]}}$UI_TX" update
        else ut "версия " "version "; local v="$UI_TX${UD_SRV[$p]}"; ut " актуальна" " is current"; ui_item 2 "$a" wizard "$v$UI_TX" muted; fi
        ut "Проверить порты" "Check ports"; ui_item 3 "$UI_TX" check
    else
        _ui_st "$p"; local nt="$UI_GT"; [ "$nt" = muted ] && nt=text
        ut "Установить" "Install"; ui_item 1 "$UI_TX" wizard "$UI_SW" "$nt"
        ut "Проверить порты" "Check ports"; ui_item 2 "$UI_TX" check
    fi
    ui_item_back
}
_ui_b_prod_actions() {  # левое окно «ДЕЙСТВИЯ»: пункты, под чертой — пояснение текущего
    local W="$1" l
    ui_lclear; ui_optlines "$UI_CUR" "$W" 0
    if [ -n "${IT_NOTE[$UI_CUR]:-}" ] && [ "$W" -gt 30 ]; then
        ui_lpush "" 0; ui_rep '─' $(( W - 2 )); ui_lpush "rule${UI_US} ${UI_P}${UI_RS}" 0
        ui_wrap "${IT_NOTE[$UI_CUR]}" $(( W - 3 ))
        for l in "${UI_WRAP[@]}"; do ui_lpush "${IT_NTOK[$UI_CUR]:-muted}${UI_US}  ${l}${UI_RS}" 0; done
    fi
}
_ui_b_prod_card() { MI_KIND=(p); MI_P=("$UI_WP"); ui_arcade_card 0 "$1" 17 1; }
_ui_b_prod_card46() {  # одно окно: описание, строка состояния
    local W="$1" p="$UI_WP" l s
    ui_lclear
    ui_wrap "${UD_DESC[$p]}" $(( W - 2 ))
    for l in "${UI_WRAP[@]}"; do ui_lpush "muted${UI_US} ${l}${UI_RS}" 0; done
    _ui_st "$p"
    if [ "${UD_INST[$p]:-0}" = 1 ]; then s="$UI_SW ${UD_SRV[$p]} · ${UD_BRANCH[$p]:-main}"; else s="$UI_SW · main ${UD_MAIN[$p]}"; fi
    UI_SEG=""; _s "$UI_GT" " $UI_G "; _s text "$s"
    if [ "${UD_UPD[$p]:-0}" = 1 ]; then _s update "  ↑ ${UD_UPDV[$p]:-${UD_MAIN[$p]}}"
    elif [ "${UD_NEW[$p]:-0}" = 1 ]; then ut "новое" "new"; _s new "  ✚ $UI_TX"; fi
    ui_lpush "$UI_SEG" 0
}
ui_scr_product() {  # ui_scr_product <продукт> <cur>
    local p="$1"
    UI_WP="$p"; UI_CUR="$2"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_product "$p"
    MI_KIND=(p); MI_P=("$p"); ui_arcade_card 0 36 17 1
    ut "ДЕЙСТВИЯ" "ACTIONS"
    SH_LW=40; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LLIST=1; SH_LBUILD=_ui_b_prod_actions
    SH_RTITLE="$UI_CT"; SH_RRIGHT="$UI_CR"; SH_RBUILD=_ui_b_prod_card; SH_RBUILD46=_ui_b_prod_card46
    ui_shell2
    ui_keys_nav
}

# ── Ж. вопросы: режим, ветка, подтверждение, язык ──────────────────────
_ui_mode_desc() {  # _ui_mode_desc <продукт> <режим> → UI_TX
    if [ "${UD_CMD[$1]}" = goamnezia ]; then
        case "$2" in
            lazy) ut "только домен, остальное сделает продукт; UDP 443 занят — встанет Semi-pro" "just a domain, the product does the rest; UDP 443 taken — it becomes Semi-pro" ;;
            pro) ut "сайт на TCP 443, AmneziaWG на UDP 443" "site on TCP 443, AmneziaWG on UDP 443" ;;
            lite_site) ut "сайт на TCP 443 и 80, AmneziaWG на своих портах" "site on TCP 443 and 80, AmneziaWG on its own ports" ;;
            *) ut "только VPN, TCP-порты не нужны" "VPN only, no TCP ports needed" ;;
        esac
    elif [ "$2" = pro ]; then ut "домен, сертификат, сайт-маска: 443 и 80" "domain, certificate, mask site: 443 and 80"
    else ut "без домена: только 443" "no domain: 443 only"; fi
}
_ui_b_opts_below() { ui_lclear; ui_optlines "$UI_CUR" "$1" 1; }
_ui_b_steps() { ui_build_steps "$1"; }
_ui_b_prodl() {  # левое окно «ПРОДУКТ» — когда режим выбирают только для проверки
    local W="$1" p="$UI_WP" l v
    ui_lclear
    ui_lpush "name${UI_US} ${UD_NAME[$p]}${UI_RS}" 0
    _ui_st "$p"; v="$UI_SW"; [ "${UD_INST[$p]:-0}" = 1 ] && v+=" ${UD_SRV[$p]}"
    ui_lpush "${UI_GT}${UI_US} ${UI_G} ${UI_RS}muted${UI_US}${v}${UI_RS}" 0
    ui_lpush "" 0
    ui_wrap "${UD_DESC[$p]}" $(( W - 2 )); for l in "${UI_WRAP[@]}"; do ui_lpush "muted${UI_US} ${l}${UI_RS}" 0; done
}
# Режим: пункты как на экране продукта, у каждого — готовность этого режима.
ui_items_mode() {  # ui_items_mode <продукт> — режимы и их итог проверки (UI_MV_<режим>)
    local p="$1" m i=0 bad notes tag tt
    ui_items_clear
    for m in ${UD_MODES[$p]}; do
        i=$(( i + 1 ))
        _ui_modename "$m"; local lab="$UI_TX"; _ui_mode_desc "$p" "$m"
        bad="${UI_MVBAD[$m]:-0}"; notes="${UI_MVNOTES[$m]:-0}"
        if [ "$bad" -gt 0 ]; then ut "препятствий: " "obstacles: "; tag="✗ $UI_TX$bad"; tt=err
        elif [ "$notes" -gt 0 ]; then ut "замечаний: " "notes: "; tag="! $UI_TX$notes"; tt=warn
        else ut "готов" "ready"; tag="✓ $UI_TX"; tt=ok; fi
        _ui_mode_desc "$p" "$m"
        ui_item "$i" "$lab" "mode:$m" "$UI_TX" muted "$tag" "$tt"
    done
    ui_item_back
}
ui_scr_mode() {  # ui_scr_mode <продукт> <cur> [только проверка 0|1]
    local p="$1" only="${3:-0}" di=0 i=0 m
    UI_WP="$p"; UI_CUR="$2"; UI_PMODE=""; UI_PBRANCH=""
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_mode "$p"
    for m in ${UD_MODES[$p]}; do [ "$m" = "${UD_DEFMODE[$p]}" ] && di=$i; i=$(( i + 1 )); done
    _ui_modename "${UD_DEFMODE[$p]}"
    local hint="muted${UI_US}Enter — $(( di + 1 )), ${UI_TX}${UI_RS}"
    ui_wiz_steps "$p"; UI_WIDX=0
    if [ "$only" = 1 ]; then
        ut "ПРОДУКТ" "PRODUCT"; SH_LW=26; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LBUILD=_ui_b_prodl; SH_STEPS46=""
    else
        ut "УСТАНОВКА" "INSTALL"; SH_LW=26; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LBUILD=_ui_b_steps
        ui_steps_row 0; SH_STEPS46="$UI_P"
    fi
    ut "РЕЖИМ" "MODE"; SH_RTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_RRIGHT="$hint"; SH_RRIGHT46="$hint"; SH_RBUILD=_ui_b_opts_below
    ui_shell2
    ui_keys_nav
    UI_DEFCUR=$di
}
ui_scr_branch() {  # ui_scr_branch <продукт> <cur>
    local p="$1" note a
    UI_WP="$p"; UI_CUR="$2"; UI_PBRANCH=""
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    # Стоит с dev — пометки «сейчас стоит» и «новее» у неё, и курсор на ней:
    # Enter не уводит goKaskad pro 3.x с dev на 2.x с main.
    local dr="${UD_DEVREF[$p]:-dev}" on2=0 di=1
    [ "${UD_INST[$p]:-0}" = 1 ] && [ "${UD_BRANCH[$p]:-main}" = "$dr" ] && { on2=1; di=2; }
    ut "стабильная" "stable"; note="$UI_TX"
    if [ "$on2" = 0 ] && [ "${UD_INST[$p]:-0}" = 1 ]; then ut "; сейчас стоит " "; installed now "; note+="$UI_TX${UD_SRV[$p]}"; fi
    if [ "$on2" = 0 ] && [ "${UD_UPD[$p]:-0}" = 1 ]; then ut "; ↑ новее" "; ↑ newer"; note+="$UI_TX"; fi
    ui_items_clear
    ui_item 1 "main ${UD_MAIN[$p]}" branch:main "$note"
    a=$(ref_kind "$dr"); [ -n "$a" ] || { ut "экспериментальная" "experimental"; a="$UI_TX"; }
    if [ "$on2" = 1 ]; then ut "; сейчас стоит " "; installed now "; a+="$UI_TX${UD_SRV[$p]}"
        if [ "${UD_UPD[$p]:-0}" = 1 ]; then ut "; ↑ новее" "; ↑ newer"; a+="$UI_TX"; fi; fi
    ui_item 2 "$dr ${UD_DEV[$p]}" "branch:$dr" "$a"
    ui_item_back
    ui_wiz_steps "$p"; ui_step_idx "Ветка" "Branch"; UI_WIDX=$UI_I
    ut "УСТАНОВКА" "INSTALL"; SH_LW=26; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LBUILD=_ui_b_steps
    ui_steps_row "$UI_I"; SH_STEPS46="$UI_P"
    local hint="muted${UI_US}Enter — 1, main${UI_RS}"
    [ "$di" = 2 ] && hint="muted${UI_US}Enter — 2, ${dr}${UI_RS}"
    ut "ВЕТКА" "BRANCH"; SH_RTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_RRIGHT="$hint"; SH_RRIGHT46="$hint"; SH_RBUILD=_ui_b_opts_below
    ui_shell2
    ui_keys_nav
}
_ui_b_confirm() {
    local W="$1" l
    ui_lclear
    ui_wrap "$UI_Q" $(( W - 2 )); for l in "${UI_WRAP[@]}"; do ui_lpush "title${UI_US} ${l}${UI_RS}" 0; done
    ui_lpush "" 0
    ui_optlines "$UI_CUR" "$W" 0
    ui_lpush "" 0
    ut "проверка портов пройдена" "port check passed"; ui_lpush "ok${UI_US} ✓ ${UI_RS}muted${UI_US}${UI_TX}${UI_RS}" 0
}
ui_scr_confirm() {  # ui_scr_confirm <продукт> <cur>  (UI_PBRANCH — выбранная ветка)
    local p="$1" ver a b
    UI_WP="$p"; UI_CUR="$2"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    if [ "$UI_PBRANCH" = "${UD_DEVREF[$p]:-dev}" ]; then ver="${UD_DEV[$p]}"; else ver="${UD_MAIN[$p]}"; fi
    ut "Запустить установщик " "Run the installer of "; a="$UI_TX"; ut "ветка " "branch "
    UI_Q="$a${UD_NAME[$p]} — $UI_TX$UI_PBRANCH, $ver?"
    ui_items_clear; ut "Да" "Yes"; ui_item 1 "$UI_TX" yes; ut "Нет" "No"; ui_item 2 "$UI_TX" no
    ui_wiz_steps "$p"; ui_step_idx "Установка" "Install"; UI_WIDX=$UI_I
    ut "УСТАНОВКА" "INSTALL"; SH_LW=26; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LBUILD=_ui_b_steps
    ui_steps_row "$UI_I"; SH_STEPS46="$UI_P"
    ut "Да" "Yes"; local hint="muted${UI_US}Enter — 1, ${UI_TX}${UI_RS}"
    ut "ПОДТВЕРЖДЕНИЕ" "CONFIRM"; SH_RTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_RRIGHT="$hint"; SH_RRIGHT46="$hint"; SH_RBUILD=_ui_b_confirm
    ui_shell2
    ut "да" "yes"; a="$UI_TX"; ut "нет" "no"; ui_keys_fit "1|$a|" "2|$UI_TX|" "Enter|1|1"
}
_ui_b_lang_list() { ui_lclear; ui_optlines "$UI_CUR" "$1" 0; }
_ui_b_lang_info() {
    local W="$1" l
    ui_lclear
    ui_lpush "title${UI_US} Язык / Language${UI_RS}" 0
    if [ "${LANG_CODE:-ru}" = en ]; then ui_lpush "muted${UI_US} Enter — 2, English${UI_RS}" 0; else ui_lpush "muted${UI_US} Enter — 1, Русский${UI_RS}" 0; fi
    ui_lpush "" 0
    ut "Выбор запоминается в /opt/go-installer/.language и больше не спрашивается." "The choice is saved to /opt/go-installer/.language and not asked again."
    ui_wrap "$UI_TX" $(( W - 2 )); for l in "${UI_WRAP[@]}"; do ui_lpush "muted${UI_US} ${l}${UI_RS}" 0; done
}
_ui_b_empty() { ui_lclear; }
ui_scr_lang() {  # ui_scr_lang <cur>
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_clear; ui_item 1 "Русский" lang:ru; ui_item 2 "English" lang:en; ui_item_back
    SH_LW=30; SH_LTITLE="title${UI_US}ЯЗЫК · LANGUAGE${UI_RS}"; SH_LLIST=1; SH_LBUILD=_ui_b_lang_list
    SH_RTITLE="title${UI_US}ЯЗЫК · LANGUAGE${UI_RS}"; SH_RBUILD=_ui_b_lang_info; SH_RBUILD46=_ui_b_empty
    ui_shell2
    ut "назад" "back"; ui_keys_fit "1|Русский|" "2|English|" "0|$UI_TX|"
}

# ── Е. проверка портов ─────────────────────────────────────────────────
# Строки проверки — массивы UI_PFR_* (порт, статус ok|bad|note|info,
# описание, кто держит), правила — UI_PFU_* (значок, токен, текст).
_ui_pfg() { case "$1" in ok) UI_G='✓'; UI_GT=ok ;; bad) UI_G='✗'; UI_GT=err ;; note) UI_G='!'; UI_GT=warn ;; *) UI_G='·'; UI_GT=muted ;; esac; }
_ui_b_check() {
    local W="$1" i n=0 HW=20 DW g l k
    ui_lclear
    [ "$W" -lt 50 ] && n=1
    DW=$(( W - 13 - HW ))
    if [ "$n" = 0 ]; then
        ut "ПОРТ" "PORT"; ui_padr "$UI_TX" 10; l="$UI_P"; ut "ЧТО ЭТО" "WHAT FOR"; ui_padr "$UI_TX" "$DW"; local a="$UI_P"; ut "КТО ДЕРЖИТ" "HELD BY"
        ui_lpush "muted${UI_US} ${l}  ${a}${UI_TX}${UI_RS}" 0
    fi
    for ((i = 0; i < ${#UI_PFR_PORT[@]}; i++)); do
        _ui_pfg "${UI_PFR_ST[$i]}"
        ui_padr "${UI_PFR_PORT[$i]}" 10
        UI_SEG=""; _s text " $UI_P"; _s "$UI_GT" "$UI_G"; _s text " "
        if [ "$n" = 1 ]; then
            if [ -n "${UI_PFR_H[$i]}" ] && [ "${UI_PFR_ST[$i]}" != ok ]; then
                if [ "${UI_PFR_ST[$i]}" = bad ]; then _s err "${UI_PFR_H[$i]}"; else _s text "${UI_PFR_H[$i]}"; fi
            else _s muted "${UI_PFR_D[$i]}"; fi
        else
            ui_padr "${UI_PFR_D[$i]}" $(( DW - 1 )); _s text "$UI_P "
            case "${UI_PFR_ST[$i]}" in bad) k=err ;; ok) k=muted ;; *) k=text ;; esac
            _s "$k" "${UI_PFR_H[$i]}"
        fi
        ui_lpush "$UI_SEG" 0
    done
    ui_lpush "" 0
    for ((i = 0; i < ${#UI_PFU_TXT[@]}; i++)); do
        ui_wrap "${UI_PFU_TXT[$i]}" $(( W - 4 ))
        for ((k = 0; k < ${#UI_WRAP[@]}; k++)); do
            if [ "$k" = 0 ]; then g="${UI_PFU_G[$i]}"; else g=" "; fi
            ui_lpush "${UI_PFU_T[$i]}${UI_US} ${g} ${UI_RS}text${UI_US}${UI_WRAP[$k]}${UI_RS}" 0
        done
    done
    if [ "${UI_PF_BAD:-0}" -gt 0 ]; then
        ut "Ставить нельзя: препятствий — ${UI_PF_BAD}." "Cannot install: ${UI_PF_BAD} obstacles."
        ui_lpush "err${UI_US} ✗ ${UI_RS}err${UI_US}${UI_TX}${UI_RS}" 0
    elif [ "${UI_PF_NOTES:-0}" -gt 0 ]; then
        ut "Можно ставить: препятствий нет, замечаний — ${UI_PF_NOTES}." "Ready to install: no obstacles, ${UI_PF_NOTES} note(s)."
        ui_lpush "ok${UI_US} ✓ ${UI_RS}title${UI_US}${UI_TX}${UI_RS}" 0
    else
        ut "Можно ставить: препятствий нет." "Ready to install: no obstacles."
        ui_lpush "ok${UI_US} ✓ ${UI_RS}title${UI_US}${UI_TX}${UI_RS}" 0
    fi
    ui_lpush "" 0
    ui_optlines "$UI_CUR" "$W" 0
}
ui_scr_check() {  # ui_scr_check <продукт> <cur> <мастер 0|1>  (UI_PMODE — режим)
    local p="$1" wiz="$3" a
    UI_WP="$p"; UI_CUR="$2"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_clear
    if [ "$wiz" = 1 ]; then
        if [ "${UI_PF_BAD:-0}" -gt 0 ]; then
            if [ -n "${UD_MODES[$p]:-}" ]; then ut "Выбрать другой режим" "Pick another mode"; ui_item 1 "$UI_TX" back; fi
        else
            if [ -n "${UD_DEV[$p]:-}" ]; then ut "дальше — выбор ветки" "next: branch"; else ut "дальше — подтверждение" "next: confirm"; fi
            a="$UI_TX"; ut "Продолжить" "Continue"; ui_item 1 "$UI_TX" continue "$a"
        fi
    else
        if [ -n "${UD_MODES[$p]:-}" ]; then ut "Проверить другой режим" "Check another mode"; ui_item 1 "$UI_TX" othermode; fi
    fi
    ui_item_back
    ui_wiz_steps "$p"; ui_step_idx "Проверка" "Check"; UI_WIDX=$UI_I
    if [ "$wiz" = 1 ]; then ui_steps_row "$UI_I"; SH_STEPS46="$UI_P"; else SH_STEPS46=""; fi
    UI_SEG=""; _s title "${UD_NAME[$p]}"; ut " · проверка портов" " · port check"; a="$UI_TX"
    if [ -n "${UI_PMODE:-}" ]; then _ui_modename "$UI_PMODE"; a+=" · $UI_TX"; fi
    _s muted "$a"
    SH_RTITLE="$UI_SEG"; SH_RBUILD=_ui_b_check; SH_WIDE=1; SH_RRIGHT46=""
    ui_shell2
    UI_SEG=""
    if [ "$UI_C" -lt 60 ]; then ut "свой · ✗ препятствие · ! замечание" "own · ✗ obstacle · ! note"
    else ut "свободен или свой · ✗ занят, препятствие · ! занят, замечание · · к сведению" "free or own · ✗ taken, obstacle · ! taken, note · · for info"; fi
    _s muted "  ✓ $UI_TX"; UI_INFO="$UI_SEG"
    ui_keys_nav
}

# ── З. итог ────────────────────────────────────────────────────────────
_ui_b_result() {
    local W="$1" p="$UI_WP" a
    ui_lclear
    _R() {  # _R <значок> <токен> <текст> [токен текста]
        local k
        ui_wrap "$3" $(( W - 4 ))
        for ((k = 0; k < ${#UI_WRAP[@]}; k++)); do
            if [ "$k" = 0 ]; then ui_lpush "$2${UI_US} $1 ${UI_RS}${4:-text}${UI_US}${UI_WRAP[$k]}${UI_RS}" 0
            else ui_lpush "$2${UI_US}   ${UI_RS}${4:-text}${UI_US}${UI_WRAP[$k]}${UI_RS}" 0; fi
        done
    }
    if [ "${UI_MISM:-0}" = 1 ]; then
        ut "Ветка не сменилась: ${UD_NAME[$p]} стоит с ветки ${UD_BRANCH[$p]}, а выбрана ${UI_PBRANCH}" "Branch did not change: ${UD_NAME[$p]} runs from branch ${UD_BRANCH[$p]}, but ${UI_PBRANCH} was chosen"
        _R '!' warn "$UI_TX" title
        ut "Повторите установку или смените ветку в меню продукта: ${UD_CMD[$p]}" "Run the install again or switch the branch in the product menu: ${UD_CMD[$p]}"
        _R ' ' muted "$UI_TX" muted
    else
        ut "Проверено: ${UD_NAME[$p]} стоит с ветки ${UD_BRANCH[$p]}, версия ${UD_SRV[$p]}" "Verified: ${UD_NAME[$p]} runs from branch ${UD_BRANCH[$p]}, version ${UD_SRV[$p]}"
        _R '✓' ok "$UI_TX" title
    fi
    if [ -n "${UI_LAZYMODE:-}" ]; then
        ui_lpush "" 0
        _ui_modename "$UI_LAZYMODE"; a="$UI_TX"
        ut "Ленивый pro встал в режиме $a" "Lazy pro ended up in $a mode"; _R '!' warn "$UI_TX" title
        if [ "$UI_LAZYMODE" = lite_site ]; then
            ut "UDP 443 был занят. Режим меняется в меню продукта: ${UD_CMD[$p]}" "UDP 443 was taken. Change the mode in the product menu: ${UD_CMD[$p]}"
            _R ' ' muted "$UI_TX" muted
        fi
    fi
    ui_lpush "" 0
    ui_optlines "$UI_CUR" "$W" 0
}
ui_scr_result() {  # ui_scr_result <продукт> <cur>  (UI_PBRANCH, UI_MISM, UI_LAZYMODE)
    local p="$1"
    UI_WP="$p"; UI_CUR="$2"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_clear
    ut "Открыть меню продукта" "Open the product menu"; ui_item 1 "$UI_TX" openprod "${UD_CMD[$p]}" key
    ut "В главное меню" "Main menu"; ui_item 0 "$UI_TX" root
    ui_wiz_steps "$p"; UI_WIDX="${#UI_STEPS[@]}"
    ut "УСТАНОВКА" "INSTALL"; SH_LW=26; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LBUILD=_ui_b_steps
    ui_steps_row $(( ${#UI_STEPS[@]} - 1 )); SH_STEPS46="$UI_P"
    if [ "${UI_MISM:-0}" = 1 ]; then ut "замечание" "note"; SH_RRIGHT="warn${UI_US}! ${UI_TX}${UI_RS}"
    else ut "готово" "done"; SH_RRIGHT="ok${UI_US}✓ ${UI_TX}${UI_RS}"; fi
    ut "СВЕРКА" "VERIFY"; SH_RTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_RBUILD=_ui_b_result
    ui_shell2
    ui_keys_nav
}

# ── И. карта портов ────────────────────────────────────────────────────
# Группы: UI_PG_NAME[] и строки UI_PG_ROWS[] — «порт|процесс|что это;…».
_ui_b_ports_list() { ui_lclear; ui_optlines "$UI_CUR" "$1" 0 $(( $1 - 9 )); }
_ui_b_ports_group() {
    local W="$1" g n=0 row port proc what l
    ui_lclear
    [ "$W" -lt 50 ] && n=1
    g="${UI_PG_CUR:-0}"
    if [ "$n" = 0 ]; then
        ut "ПОРТ" "PORT"; ui_padr "$UI_TX" 11; l="$UI_P"; ut "ПРОЦЕСС" "PROCESS"; ui_padr "$UI_TX" 12; local a="$UI_P"; ut "ЧТО ЭТО" "WHAT FOR"
        ui_lpush "muted${UI_US} ${l}${a}${UI_TX}${UI_RS}" 0
    fi
    local IFS_OLD="$IFS"; IFS=';'
    for row in ${UI_PG_ROWS[$g]}; do
        IFS='|' read -r port proc what <<<"$row"
        if [ "$n" = 1 ]; then ui_padr "$port" 10; l="$UI_P"; ui_padr "$proc" 9; else ui_padr "$port" 11; l="$UI_P"; ui_padr "$proc" 12; fi
        ui_lpush "text${UI_US} ${l}${UI_RS}key${UI_US}${UI_P}${UI_RS}muted${UI_US}${what}${UI_RS}" 0
    done
    IFS="$IFS_OLD"
}
ui_scr_ports() {  # ui_scr_ports <cur>
    local i
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_clear
    for ((i = 0; i < ${#UI_PG_NAME[@]}; i++)); do
        local rows="${UI_PG_ROWS[$i]}" c=1; rows="${rows//[!;]/}"; c=$(( ${#rows} + 1 ))
        ui_item "$(( i + 1 ))" "${UI_PG_NAME[$i]}" "group:$i" "$c"
    done
    ui_item_back
    UI_PG_CUR="$UI_CUR"; [ "$UI_PG_CUR" -ge "${#UI_PG_NAME[@]}" ] && UI_PG_CUR=$(( ${#UI_PG_NAME[@]} - 1 ))
    ut "КТО ДЕРЖИТ" "HOLDERS"; SH_LW=28; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LLIST=1; SH_LBUILD=_ui_b_ports_list
    SH_RTITLE="title${UI_US}${UI_PG_NAME[$UI_PG_CUR]}${UI_RS}"; SH_RRIGHT="muted${UI_US}goinstall --ports${UI_RS}"; SH_RBUILD=_ui_b_ports_group
    ui_shell2
    local a b; ut "выбрать" "move"; a="$UI_TX"; ut "выбрать" "select"; b="$UI_TX"; ut "цифра" "digit"; local c="$UI_TX"; ut "сразу" "jumps"; local d="$UI_TX"; ut "назад" "back"
    ui_keys_fit "↑↓|$a|" "Enter|$b|" "$c|$d|" "0|$UI_TX|$UI_TX"
}

# ── К. ключ доступа ────────────────────────────────────────────────────
_ui_b_key_list() { ui_lclear; ui_optlines "$UI_CUR" "$1" 0; }
_ui_b_key_card() {
    local W="$1" l
    ui_lclear
    ut "сохранён" "saved"; ui_lpush "ok${UI_US} ● ${UI_RS}text${UI_US}${UI_TX}${UI_RS}" 0
    ui_lpush "muted${UI_US}   ${TOKEN_FILE:-/opt/go-installer/token} · 0600${UI_RS}" 0
    ui_lpush "" 0
    if [ -n "${UI_KEYCHECKING:-}" ]; then
        ut "проверяю 6 репозиториев…" "checking 6 repositories…"; ui_lpush "cursor${UI_US} ${UI_SPIN:-⠋} ${UI_RS}muted${UI_US}${UI_TX}${UI_RS}" 0
    elif [ -n "${UD_KEYDENIED:-}" ]; then
        ut "открывает ${UD_KEYOK:-5} из 6" "opens ${UD_KEYOK:-5} of 6"; ui_lpush "warn${UI_US} ! ${UI_RS}text${UI_US}${UI_TX}${UI_RS}" 0
        ut "нет доступа" "no access"; ui_lpush "warn${UI_US}   ${UD_KEYDENIED} — ${UI_TX}${UI_RS}" 0
        ui_lpush "" 0
        ut "Возьмите свежий ключ в «potap1978 club», ветка «goinstall», и введите его пунктом 2." "Get a fresh key in “potap1978 club”, topic “goinstall”, and enter it via item 2."
        ui_wrap "$UI_TX" $(( W - 4 )); for l in "${UI_WRAP[@]}"; do ui_lpush "muted${UI_US}   ${l}${UI_RS}" 0; done
    else
        ut "открывает 6 из 6" "opens 6 of 6"; ui_lpush "ok${UI_US} ✓ ${UI_RS}text${UI_US}${UI_TX}${UI_RS}" 0
        ut "проверен в " "checked at "; ui_lpush "muted${UI_US}   ${UI_TX}${UD_KEYAT:-$UD_SVED}${UI_RS}" 0
    fi
}
ui_scr_key() {  # ui_scr_key <cur>
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_items_clear
    ut "Проверить ключ" "Check the key"; ui_item 1 "$UI_TX" keycheck
    ut "Ввести новый ключ" "Enter a new key"; ui_item 2 "$UI_TX" keyinput
    ut "Удалить ключ" "Forget the key"; ui_item 3 "$UI_TX" keyforget "goinstall --key forget"
    ui_item_back
    ut "ДЕЙСТВИЯ" "ACTIONS"; SH_LW=30; SH_LTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_LLIST=1; SH_LBUILD=_ui_b_key_list
    if [ -n "${UD_KEYDENIED:-}" ]; then SH_RRIGHT="warn${UI_US}! ${UD_KEYOK:-5}/6${UI_RS}"; else SH_RRIGHT="ok${UI_US}✓ 6/6${UI_RS}"; fi
    ut "КЛЮЧ ДОСТУПА" "ACCESS KEY"; SH_RTITLE="title${UI_US}${UI_TX}${UI_RS}"; SH_RBUILD=_ui_b_key_card
    ui_shell2
    ui_keys_nav
}

# ── рамка без окон (box) — экран «нет ключа» и сводка при входе ────────
# ui_box <заголовок-сегменты> <правая метка-сегменты или ""> <низ-сегменты>;
# строки внутри — UI_BX[] («SEP» — пустая строка-разделитель).
ui_box() {
    local title="$1" right="$2" bottom="$3" Wb=$(( UI_C - 1 )) iw top tr n l bl
    iw=$(( Wb - 4 ))
    top="grad${UI_US}╔═ ${UI_RS}${title}grad${UI_US} ${UI_RS}"
    if [ -n "$right" ]; then tr="grad${UI_US} ${UI_RS}${right}grad${UI_US} ═╗${UI_RS}"; else tr="grad${UI_US}═╗${UI_RS}"; fi
    ui_len "$top"; n=$UI_N; ui_len "$tr"
    if [ $(( Wb - n - UI_N )) -lt 1 ]; then tr="grad${UI_US}═╗${UI_RS}"; ui_len "$tr"; fi
    ui_rep '═' $(( Wb - n - UI_N ))
    ui_body "${top}grad${UI_US}${UI_P}${UI_RS}${tr}text${UI_US} ${UI_RS}"
    for l in "${UI_BX[@]}"; do
        if [ "$l" = SEP ]; then ui_rep ' ' $(( Wb - 2 )); ui_body "grad${UI_US}║${UI_P}║${UI_RS}shadow${UI_US}░${UI_RS}"; continue; fi
        ui_fit "$l" "$iw"; printf -v UI_P '%*s' $(( iw - UI_FITN )) ''
        ui_body "grad${UI_US}║ ${UI_RS}${UI_FIT}text${UI_US}${UI_P}${UI_RS}grad${UI_US} ║${UI_RS}shadow${UI_US}░${UI_RS}"
    done
    bl="grad${UI_US}╚═ ${UI_RS}${bottom}grad${UI_US} ${UI_RS}"
    ui_len "$bl"; ui_rep '═' $(( Wb - UI_N - 1 ))
    ui_body "${bl}grad${UI_US}${UI_P}${UI_RS}grad${UI_US}╝${UI_RS}shadow${UI_US}░${UI_RS}"
    ui_rep '░' "$Wb"; ui_body "text${UI_US} ${UI_RS}shadow${UI_US}${UI_P}${UI_RS}"
}
ui_scr_nokey() {  # ui_scr_nokey <cur>
    local l
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_main_title; ui_hdr "$UI_MT" ""; ui_body ""
    UI_BX=()
    if [ "${UI_KEYBAD:-0}" = 1 ]; then ut "Сохранённый ключ больше не работает: GitHub его не принимает." "The saved key no longer works: GitHub rejects it."; UI_BX+=("text${UI_US} ${UI_RS}err${UI_US}${UI_TX}${UI_RS}"); fi
    ut "Репозитории продуктов закрытые: без ключа не скачивается ни один." "Product repositories are private: nothing downloads without a key."
    UI_BX+=("text${UI_US} ${UI_RS}text${UI_US}${UI_TX}${UI_RS}"); UI_BX+=("")
    ut "Ключ на чтение выдаётся подписчикам в закрытой группе «potap1978 club», в ветке «goinstall». Подписка: boosty.to/potap1978" "A read key is given to subscribers in the private “potap1978 club” group, topic “goinstall”. Subscription: boosty.to/potap1978"
    ui_wrap "$UI_TX" $(( UI_C - 6 )); for l in "${UI_WRAP[@]}"; do UI_BX+=("text${UI_US} ${UI_RS}muted${UI_US}${l}${UI_RS}"); done
    ut "Нужен ключ доступа" "An access key is needed"
    ui_box "warn${UI_US}${UI_TX}${UI_RS}" "" "key${UI_US}goinstall --key set${UI_RS}"
    ui_body ""
    ui_items_clear; ut "Ввести ключ" "Enter the key"; ui_item 1 "$UI_TX" keyinput; ut "Выход" "Quit"; ui_item 0 "$UI_TX" quit
    ui_choicerows "$UI_CUR"
    ui_keys_nav
}
# Ввод ключа: символы звёздочками, Enter — проверить, Esc — назад.
ui_scr_keyinput() {  # ui_scr_keyinput <сколько введено> [состояние: checking|ok|bad]
    local n="$1" st="${2:-}"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ut "Ключ доступа" "Access key"; ui_hdr "title${UI_US}${UI_TX}${UI_RS}" ""; ui_body ""
    ut "Вставьте ключ и нажмите Enter." "Paste the key and press Enter."; ui_body "text${UI_US}  ${UI_TX}${UI_RS}"
    ut "Он сохранится в /opt/go-installer/token, режим 0600." "It is saved to /opt/go-installer/token, mode 0600."; ui_body "muted${UI_US}  ${UI_TX}${UI_RS}"; ui_body ""
    [ "$n" -gt $(( UI_C - 12 )) ] && n=$(( UI_C - 12 ))
    ui_rep '*' "$n"; ut "ключ: " "key: "; ui_body "muted${UI_US}  ${UI_TX}${UI_RS}text${UI_US}${UI_P}${UI_RS}cursor${UI_US}█${UI_RS}"; ui_body ""
    case "$st" in
        checking) ut "проверяю…" "checking…"; ui_body "cursor${UI_US}  ${UI_SPIN:-⠋} ${UI_RS}muted${UI_US}${UI_TX}${UI_RS}" ;;
        ok) ut "ключ подошёл: открывает 6 из 6" "the key works: opens 6 of 6"; ui_body "ok${UI_US}  ✓ ${UI_RS}text${UI_US}${UI_TX}${UI_RS}" ;;
        bad) ut "GitHub этот ключ не принял. Проверьте, что скопирован он целиком." "GitHub rejected this key. Check that it was copied in full."; ui_body "err${UI_US}  ✗ ${UI_RS}text${UI_US}${UI_TX}${UI_RS}" ;;
    esac
    local a; ut "сохранить" "save"; a="$UI_TX"; ut "назад" "back"; ui_keys_fit "Enter|$a|" "Esc|$UI_TX|$UI_TX"
}

# ── К. опрос не удался ─────────────────────────────────────────────────
ui_scr_err() {  # ui_scr_err <github|rate> <cur>
    local gh=0 l tx
    [ "$1" = github ] && gh=1
    UI_CUR="$2"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_main_title; ui_hdr "$UI_MT" ""; ui_body ""
    if [ "$gh" = 1 ]; then ut "GitHub недоступен" "GitHub is unreachable"; ui_body "err${UI_US}  ✗ ${UI_RS}title${UI_US}${UI_TX}${UI_RS}"
    else ut "Лимит запросов к GitHub исчерпан, повтор в ${UI_RESET:-?}" "GitHub rate limit reached, retry at ${UI_RESET:-?}"; ui_body "warn${UI_US}  ! ${UI_RS}title${UI_US}${UI_TX}${UI_RS}"; fi
    ui_body ""
    local L=()
    if [ "$gh" = 1 ]; then
        ut "Новые версии узнать не удалось. Меню откроется со сведениями от ${UD_SVED}." "Could not fetch new versions. The menu opens with data as of ${UD_SVED}."; L+=("$UI_TX")
        if [ -n "${UI_NETRC:-}" ]; then
            L+=("$(bi "Причина" "Cause"): $(gh_net_reason "$UI_NETRC"). $(bi "Попыток на каждый запрос: ${GH_TRIES}, ожидание до ${GH_MT} с." "Attempts per request: ${GH_TRIES}, waiting up to ${GH_MT} s each.")")
        else
            ut "Проверьте сеть сервера: curl -I https://api.github.com" "Check the server network: curl -I https://api.github.com"; L+=("$UI_TX")
        fi
    else
        ut "Ответили ${UI_POLLOK:-5} из 8. Остальное меню покажет по сведениям от ${UD_SVED}." "${UI_POLLOK:-5} of 8 answered. The rest shows data as of ${UD_SVED}."; L+=("$UI_TX")
        ut "В ${UI_RESET:-?} goinstall спросит GitHub сам." "At ${UI_RESET:-?} goinstall asks GitHub again by itself."; L+=("$UI_TX")
    fi
    for tx in "${L[@]}"; do ui_wrap "$tx" $(( UI_C - 6 )); for l in "${UI_WRAP[@]}"; do ui_body "text${UI_US}    ${l}${UI_RS}"; done; done
    ui_body ""
    ui_items_clear
    ut "Открыть меню со сведениями от ${UD_SVED}" "Open the menu with data as of ${UD_SVED}"; ui_item 1 "$UI_TX" root
    ut "Повторить опрос" "Retry the poll"; ui_item 2 "$UI_TX" retry
    ut "Выход" "Quit"; ui_item 0 "$UI_TX" quit
    ui_choicerows "$UI_CUR"
    ui_keys_nav
}

# ── В. заставка с опросом GitHub ───────────────────────────────────────
# Строки опроса: UI_PI_NAME[], UI_PI_RES[] (что ответил), UI_PI_TOK[],
# UI_PI_ST[] (wait|run|done|fail). Шкала — ответившие из восьми.
ui_scr_splash() {
    local n=0 i c r s ch up face row side done=0 st g res
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    [ "$UI_C" -lt 60 ] && n=1
    local GO=('▄██████▄  ▄██████▄' '██        ██    ██' '██  ████  ██    ██' '▀██████▀  ▀██████▀')
    local SPEED=('      ' '  ════' '══════' ' ═════' '      ')
    local SIDE=()
    SIDE[0]=""; SIDE[1]="title${UI_US}goInstaller ${UI_RS}muted${UI_US}${UD_VER}${UI_RS}"
    if [ "$n" = 1 ]; then ut "установщик серии go" "go series installer"; else ut "установщик продуктов серии go" "installer for the go series"; fi
    SIDE[2]="muted${UI_US}${UI_TX}${UI_RS}"
    if [ "$n" = 1 ]; then SIDE[3]="muted${UI_US}${UD_HOST}${UI_RS}"; else SIDE[3]="muted${UI_US}${UD_HOST} · ${UD_OS}${UI_RS}"; fi
    SIDE[4]=""
    for ((r = 0; r <= 4; r++)); do
        UI_SEG=""; _s text " "
        if [ "$n" = 0 ]; then _s grad "${SPEED[$r]}"; else _s text " "; fi
        local prev="" run=""
        for ((c = 0; c <= 18; c++)); do
            ch=""; [ "$r" -lt 4 ] && ch="${GO[$r]:$c:1}"
            if [ -n "$ch" ] && [ "$ch" != " " ]; then face="logo$r"; s="$ch"
            else
                up=" "; [ "$r" -gt 0 ] && [ "$c" -gt 0 ] && up="${GO[$(( r - 1 ))]:$(( c - 1 )):1}"
                if [ -n "$up" ] && [ "$up" != " " ]; then face=shade; if [ "$r" = 4 ]; then s='▀'; else s='▓'; fi
                else face=text; s=' '; fi
            fi
            if [ "$face" = "$prev" ]; then run+="$s"; else [ -n "$prev" ] && _s "$prev" "$run"; prev="$face"; run="$s"; fi
        done
        _s "$prev" "$run"; _s text "   "
        ui_body "$UI_SEG${SIDE[$r]}"
    done
    ui_body ""
    for st in "${UI_PI_ST[@]}"; do case "$st" in done|fail) done=$(( done + 1 )) ;; esac; done
    ut "  Опрос GitHub  " "  GitHub poll   "; UI_SEG=""; _s text "$UI_TX"
    ui_bar $(( done * 1000 / 8 )) $(( n ? 14 : 24 )) cursor; UI_SEG+="$UI_BAR"
    ut " из 8" " of 8"; _s muted "  $done$UI_TX"; ui_body "$UI_SEG"
    ui_body ""
    for ((i = 0; i < ${#UI_PI_NAME[@]}; i++)); do
        case "${UI_PI_ST[$i]}" in
            done) g='✓'; s=ok; res="${UI_PI_RES[$i]}"; r="${UI_PI_TOK[$i]:-text}" ;;
            fail) g='✗'; s=err; if [ "$UI_POLLERR" = rate ]; then ut "403: лимит запросов" "403: rate limit"; else ut "нет ответа" "no answer"; fi; res="$UI_TX"; r=err ;;
            run)  g="${UI_SPIN:-⠋}"; s=cursor; ut "спрашиваю…" "asking…"; res="$UI_TX"; r=muted ;;
            *)    g='·'; s=muted; ut "ждёт" "queued"; res="$UI_TX"; r=muted ;;
        esac
        ui_padr "${UI_PI_NAME[$i]}" $(( n ? 15 : 17 ))
        ui_body "text${UI_US}  ${UI_RS}${s}${UI_US}${g}${UI_RS}name${UI_US} ${UI_P}${UI_RS}${r}${UI_US}${res}${UI_RS}"
    done
    if [ -n "${UI_PI_NOTE:-}" ]; then
        # Что происходит и почему — пока опрос ещё идёт.
        local nl
        ui_body ""
        ui_wrap "$UI_PI_NOTE" $(( UI_C - 6 )); for nl in "${UI_WRAP[@]}"; do ui_body "warn${UI_US}  ${nl}${UI_RS}"; done
    fi
    if [ "$done" -lt 8 ]; then
        local a; ut "любая клавиша" "any key"; a="$UI_TX"; ut "пропустить, опрос идёт дальше" "skip, polling continues"; local b="$UI_TX"; ut "пропустить" "skip"
        ui_keys_fit "$a|$b|$UI_TX"
    else ut "в меню" "to the menu"; local a="$UI_TX"; ut "меню" "menu"; ui_keys_fit "Enter|$a|$UI_TX"; fi
}

# ── А. сводка при входе ────────────────────────────────────────────────
# Новое (UD_NEWS: «значок|токен|имя|версии|что») и замечания UD_PROB; всё
# тихо — две строки без рамки. UD_NINST — сколько продуктов стоит.
ui_summary() {
    local n=0 u g tk nm vv what sved p name msg short step sev
    [ "$UI_C" -lt 60 ] && n=1
    ut "сведения от " "data as of "; sved="$UI_TX$UD_SVED"
    if [ "${#UD_NEWS[@]}" -eq 0 ] && [ "${#UD_PROB[@]}" -eq 0 ]; then
        UI_SEG=""; _s ok "✓ "; ut "goInstaller: всё актуально" "goInstaller: all up to date"; _s text "$UI_TX"
        if [ "$n" = 0 ]; then ui_plural "${UD_NINST:-0}" продукт продукта продуктов product products; ut ", $UI_TX, проблем нет" ", $UI_TX, no problems"; _s muted "$UI_TX"; fi
        ui_body "$UI_SEG"
        UI_SEG=""; _s muted "  $sved · "; _s key "goinstall"; if [ "$n" = 1 ]; then ut " — меню" " — menu"; else ut " — открыть меню" " — open the menu"; fi; _s muted "$UI_TX"
        ui_body "$UI_SEG"
        return 0
    fi
    UI_BX=()
    for u in "${UD_NEWS[@]}"; do
        IFS='|' read -r g tk nm vv what <<<"$u"
        UI_SEG=""
        if [ "$n" = 1 ]; then _s "$tk" "$g"; ui_padr "$nm" 15; _s name " $UI_P"; _s "$tk" "$vv"
        else _s "$tk" " $g"; ui_padr "$nm" 17; _s name " $UI_P"; ui_padr "$vv" 20; _s "$tk" "$UI_P"; _s muted "$what"; fi
        UI_BX+=("$UI_SEG")
    done
    if [ "${#UD_PROB[@]}" -gt 0 ]; then
        [ "${#UD_NEWS[@]}" -gt 0 ] && UI_BX+=(SEP)
        for p in "${UD_PROB[@]}"; do
            IFS='|' read -r name msg short step sev <<<"$p"
            if [ "$sev" = err ]; then g='✗'; tk=err; else g='!'; tk=warn; fi
            if [ "$n" = 1 ]; then
                UI_BX+=("${tk}${UI_US}${g}${UI_RS}text${UI_US} ${short}${UI_RS}")
                UI_BX+=("muted${UI_US}  → ${UI_RS}key${UI_US}${step}${UI_RS}")
            else
                ui_padr "$name" 17
                UI_BX+=("${tk}${UI_US} ${g}${UI_RS}name${UI_US} ${UI_P}${UI_RS}text${UI_US}${msg}${UI_RS}muted${UI_US} → ${UI_RS}key${UI_US}${step}${UI_RS}")
            fi
        done
        ut "диск ${UD_DISK} % · память ${UD_MEM} % · нагрузка ${UD_LOAD}" "disk ${UD_DISK} % · memory ${UD_MEM} % · load ${UD_LOAD}"
        if [ "$n" = 1 ]; then UI_BX+=("muted${UI_US}${UI_TX}${UI_RS}"); else UI_BX+=("text${UI_US} ${UI_RS}muted${UI_US}${UI_TX}${UI_RS}"); fi
    fi
    local right bottom
    if [ "$n" = 1 ]; then right="muted${UI_US}${UD_SVED}${UI_RS}"; else right="muted${UI_US}${sved}${UI_RS}"; fi
    if [ "$n" = 1 ]; then ut " — меню" " — menu"; else ut " — открыть меню" " — open the menu"; fi
    bottom="key${UI_US}goinstall${UI_RS}muted${UI_US}${UI_TX}${UI_RS}"
    ui_box "title${UI_US}goInstaller${UI_RS}muted${UI_US} · ${UD_HOST}${UI_RS}" "$right" "$bottom"
}
# Значок в приглашении: [go ↑2 ✚1 !2] — сколько обновлений, нового, замечаний.
ui_badge() {
    local u nu=0 nn=0 np="${#UD_PROB[@]}"
    for u in "${UD_NEWS[@]}"; do case "$u" in '↑|'*) nu=$(( nu + 1 )) ;; '✚|'*) nn=$(( nn + 1 )) ;; esac; done
    UI_SEG=""
    if [ $(( nu + nn + np )) -gt 0 ]; then
        _s muted " [go"; [ "$nu" -gt 0 ] && _s update " ↑$nu"; [ "$nn" -gt 0 ] && _s new " ✚$nn"; [ "$np" -gt 0 ] && _s warn " !$np"; _s muted "]"
    fi
    UI_BADGE="$UI_SEG"
}
# Экран входа целиком — как в макете: строка sshd, сводка, приглашение.
ui_scr_login() {  # ui_scr_login [отсчёт: сколько осталось, мс]
    local rem="${1:-}" n=0
    [ "$UI_C" -lt 60 ] && n=1
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_body "text${UI_US}${UI_LASTLOGIN:-}${UI_RS}"; ui_body ""
    ui_summary
    ui_body ""
    if [ -n "$rem" ]; then
        UI_SEG=""; _s text "  "; _s key "Enter"
        if [ "$n" = 1 ]; then ut " — goinstall · другая клавиша — консоль" " — goinstall · other key — shell"; else ut " — открыть goinstall · любая другая клавиша — в консоль" " — open goinstall · any other key — shell"; fi
        _s muted "$UI_TX"; ui_body "$UI_SEG"
        UI_SEG=""; _s text "  "; ui_bar $(( rem / 5 )) $(( n ? 20 : 24 )) cursor; UI_SEG+="$UI_BAR"; ut " с" " s"; _s muted "  $(( (rem + 999) / 1000 ))$UI_TX"
        ui_body "$UI_SEG"
    else
        ui_badge
        ui_body "text${UI_US}root@${UD_HOST}${UI_RS}${UI_BADGE}text${UI_US}:~# ${UI_RS}cursor${UI_US}█${UI_RS}"
    fi
}

# ── живые данные ───────────────────────────────────────────────────────
# Системные службы продукта: что показывать в карточке «СЛУЖБЫ».
prod_units() {
    case "$1" in
        govless)    printf 'x-ui nginx' ;;
        gotelegram) printf 'telemt' ;;
        godns)      printf 'dnsdist' ;;
        gowarp)     printf 'warp-svc warp-bot' ;;
        goamnezia)  printf 'goamnezia-admin' ;;
        *)          printf '' ;;
    esac
}
# Состояние службы продукта: run — работает; down — включена, но не
# работает (сбой); off — стоит выключенной (решение человека, не сбой);
# none — на машине её нет. prod_units — все службы продукта, а стоят не все:
# goWARP pro ставит warp-svc только для 3X-UI, warp-bot — только с ботом. До
# 1.0.8 сводка при входе звала отсутствующие службы упавшими.
unit_state() {
    local load
    # Без --value: его нет до systemd 230, и там все службы выпали бы молча.
    load=$(systemctl show -p LoadState "$1" 2>/dev/null) || load=""
    load=${load#LoadState=}
    case "$load" in loaded|masked) ;; *) printf 'none'; return 0 ;; esac
    if systemctl is-active --quiet "$1" 2>/dev/null; then printf 'run'
    elif systemctl is-enabled --quiet "$1" 2>/dev/null; then printf 'down'
    else printf 'off'; fi
}
# Диск, память, нагрузка — для строки состояния.
ui_sysload() {
    local d m l
    if [ -n "${GOINST_UI_SYS:-}" ]; then read -r UD_DISK UD_MEM UD_LOAD <<<"$GOINST_UI_SYS"; return 0; fi
    d=$(df -P / 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5}') || d=""
    m=$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {if (t > 0) printf "%d", (t - a) * 100 / t}' /proc/meminfo 2>/dev/null) || m=""
    l=$(awk '{printf "%.1f", $1}' /proc/loadavg 2>/dev/null) || l=""
    case "$d" in ''|*[!0-9]*) d=0 ;; esac; case "$m" in ''|*[!0-9]*) m=0 ;; esac
    UD_DISK="$d"; UD_MEM="$m"; UD_LOAD="${l:-0.0}"
}
# «Новое»: продукт не стоит, а его main появилась с прошлого раза. Первый
# запуск запоминает всё, что есть, — иначе новым оказалось бы всё сразу.
UI_SEEN_FILE="${GOINST_SEEN_FILE:-${GOINST_DIR}/seen}"
ui_seen_init() {
    local id
    [ -e "$UI_SEEN_FILE" ] && return 0
    mkdir -p "$(dirname "$UI_SEEN_FILE")" 2>/dev/null || return 0
    for id in "${PRODUCTS[@]}"; do ver_cache_get "$id" main >/dev/null 2>&1 && printf '%s:main\n' "$id"; done > "$UI_SEEN_FILE" 2>/dev/null || true
}
ui_is_new() { [ -r "$UI_SEEN_FILE" ] && ! grep -qx "$1:main" "$UI_SEEN_FILE" 2>/dev/null; }
ui_mark_seen() { ui_is_new "$1" && printf '%s:main\n' "$1" >> "$UI_SEEN_FILE" 2>/dev/null; return 0; }

# Число по-русски: 1 день, 2 дня, 5 дней; по-английски — one/many (день по умолчанию).
ui_plural() {  # ui_plural <n> <один> <два> <пять> [one] [many] → UI_TX
    local n="$1" a=$(( $1 % 10 )) b=$(( $1 % 100 ))
    if [ "${LANG_CODE:-ru}" = en ]; then if [ "$n" = 1 ]; then UI_TX="$n ${5:-day}"; else UI_TX="$n ${6:-days}"; fi; return 0; fi
    if [ "$a" = 1 ] && [ "$b" != 11 ]; then UI_TX="$n $2"
    elif [ "$a" -ge 2 ] && [ "$a" -le 4 ] && { [ "$b" -lt 12 ] || [ "$b" -gt 14 ]; }; then UI_TX="$n $3"
    else UI_TX="$n $4"; fi
}
# Сколько дней осталось сертификату: -checkend вместо разбора даты — он
# одинаков в openssl и LibreSSL и не зависит от GNU date.
_cert_days() {  # _cert_days <файл> → UI_DAYS (−1 — истёк)
    local f="$1" lo=0 hi=400 mid
    if ! openssl x509 -checkend 0 -noout -in "$f" >/dev/null 2>&1; then UI_DAYS=-1; return 0; fi
    while [ "$lo" -lt "$hi" ]; do
        mid=$(( (lo + hi + 1) / 2 ))
        if openssl x509 -checkend $(( mid * 86400 )) -noout -in "$f" >/dev/null 2>&1; then lo=$mid; else hi=$(( mid - 1 )); fi
    done
    UI_DAYS=$lo
}
_json_str() { sed -n -E "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"([^\"]*)\".*/\\1/p" "$1" 2>/dev/null | head -1; }
# Сертификаты по карте продуктов (design/README.md): владелец — по домену в
# config.json продукта, подсказка — проверка, которая подходит ему.
cert_scan() {
    local r="${GOINST_FS_ROOT:-}" le="${GOINST_LE_ROOT:-/etc/letsencrypt}" id d f hint seen=" " name msg short
    UI_CERTP=()
    _cert_one() {  # _cert_one <домен или IP> <файл> <подсказка> <порог дней>
        local sev=warn
        [ -r "$2" ] || return 0
        _cert_days "$2"
        [ "$UI_DAYS" -lt "$4" ] || return 0
        # Истёк — препятствие ✗. Доменному (порог 14) меньше 3 дней — тоже:
        # certbot продлевает за 30, и раз не продлил, сам уже не успеет.
        if [ "$UI_DAYS" -lt 0 ] || { [ "$4" -ge 14 ] && [ "$UI_DAYS" -lt 3 ]; }; then sev=err; fi
        if [ "$UI_DAYS" -lt 0 ]; then ut "сертификат истёк" "certificate has expired"; msg="$UI_TX"; ut "сертификат $1: истёк" "$1 cert: expired"; short="$UI_TX"
        # Меньше суток — так и сказать: «через 0 дней» читается как «уже».
        elif [ "$UI_DAYS" -eq 0 ]; then ut "сертификат истекает меньше чем через сутки" "certificate expires in less than a day"; msg="$UI_TX"
             ut "сертификат $1: меньше суток" "$1 cert: under a day left"; short="$UI_TX"
        else ui_plural "$UI_DAYS" день дня дней; local dd="$UI_TX"
             ut "сертификат истекает через $dd" "certificate expires in $dd"; msg="$UI_TX"
             ut "сертификат $1: $dd" "$1 cert: $dd left"; short="$UI_TX"; fi
        UI_CERTP+=("$1|$msg|$short|$3|$sev")
    }
    for id in govless gotelegram goamnezia godns; do
        prod_installed "$id" || continue
        f=$(prod_config_file "$id") || continue
        [ -r "$f" ] || continue
        d=$(_json_str "$f" domain)
        case "$id" in
            govless)    hint="certbot certificates" ;;
            gotelegram) [ "$(prod_installed_mode gotelegram 2>/dev/null)" = pro ] || d=""; hint="gotelegram: Сайт / SSL"; ut "gotelegram: Сайт / SSL" "gotelegram: Site / SSL"; hint="$UI_TX" ;;
            goamnezia)  grep -q '"cert"[[:space:]]*:[[:space:]]*"letsencrypt"' "$f" 2>/dev/null || d=""; ut "goamnezia: Сертификат" "goamnezia: Certificate"; hint="$UI_TX" ;;
            godns)      grep -q '"cert_owned_by_godns"[[:space:]]*:[[:space:]]*"yes"' "$f" 2>/dev/null || d=""; ut "godns: Сертификат" "godns: Certificate"; hint="$UI_TX" ;;
        esac
        [ -n "$d" ] || continue
        case "$seen" in *" $d "*) continue ;; esac
        seen+="$d "
        _cert_one "$d" "$le/live/$d/cert.pem" "$hint" 14
    done
    # Панель goVLESS pro в Lite: IP-сертификат acme.sh живёт около 6 дней,
    # поэтому тревога — только когда осталось меньше двух.
    if prod_installed govless; then
        for f in "$r"/root/cert/ip/*.crt; do
            [ -r "$f" ] || continue
            d=$(basename "$f" .crt); _cert_one "$d" "$f" "acme.sh --list" 2
        done
    fi
    return 0
}

# ui_model_load — модель экрана из системы: продукты, версии, готовность,
# порты, службы, сертификаты, ключ.
ui_model_load() {
    local i id v inst free st rv refs m rest u us ok k c
    # Один скан портов и контейнеров на всю пересборку: без этого каждая
    # проверка каждого продукта снова звала ss и docker ps, и на сервере с
    # Docker меню замирало на секунды.
    local own_cache=""
    if [ -z "${GOINST_SCAN_CACHE:-}" ]; then scan_cache_begin; own_cache=1; fi
    UD_HOST=$(hostname -s 2>/dev/null) || UD_HOST=""; [ -n "$UD_HOST" ] || UD_HOST=server
    UD_OS="${OS_PRETTY:-}"; UD_VER="$GOINST_VERSION"
    UD_VERNEW=""; v=$(ver_cache_get "$SELF_ID" main 2>/dev/null) || v=""
    if [ -n "$v" ] && ver_newer "$GOINST_VERSION" "$v"; then UD_VERNEW="$v"; fi
    UD_SVED="${UI_SVED:-$(date +%H:%M)}"
    ui_sysload
    scan_listeners; scan_docker
    UD_NAME=(); UD_CMD=(); UD_DESC=(); UD_INST=(); UD_SRV=(); UD_MAIN=(); UD_DEV=(); UD_BRANCH=(); UD_UPD=(); UD_NEW=()
    UD_DEVREF=(); UD_UPDV=()
    UD_ST=(); UD_STTXT=(); UD_MODES=(); UD_DEFMODE=(); UD_SVC=(); UD_SVCOK=(); UD_HELD=(); UD_HELDP=(); UD_NEED=(); UD_VBAD=(); UD_VNOTES=(); UD_VWHY=()
    UD_NEWS=(); UD_PROB=(); UD_NINST=0
    ui_seen_init
    for ((i = 0; i < ${#PRODUCTS[@]}; i++)); do
        id="${PRODUCTS[$i]}"
        UD_NAME[i]=$(prod_name "$id"); UD_CMD[i]=$(prod_cmd "$id"); UD_DESC[i]=$(prod_desc "$id")
        inst=0; prod_installed "$id" && inst=1
        free=""; [ "$inst" = 1 ] && free=$(prod_free_edition "$id" 2>/dev/null)
        v=$(prod_version "$id" 2>/dev/null) || v=""
        UD_INST[i]=$inst
        read -r -a refs <<<"$(prod_refs "$id")"
        UD_MAIN[i]=$(ver_cache_get "$id" "${refs[0]:-main}" 2>/dev/null) || UD_MAIN[i]=""
        UD_DEV[i]=""; UD_DEVREF[i]="${refs[1]:-}"; UD_UPDV[i]=""
        if [ -n "${refs[1]:-}" ]; then UD_DEV[i]=$(ver_cache_get "$id" "${refs[1]}" 2>/dev/null) || UD_DEV[i]="?"; [ -n "${UD_DEV[i]}" ] || UD_DEV[i]="?"; fi
        UD_BRANCH[i]=$(prod_installed_ref "$id" 2>/dev/null) || UD_BRANCH[i]=""
        [ -n "${UD_BRANCH[i]}" ] || UD_BRANCH[i]="${refs[0]:-main}"
        UD_UPD[i]=0; UD_NEW[i]=0; UD_STTXT[i]=""
        if [ "$inst" = 1 ]; then
            UD_NINST=$(( UD_NINST + 1 ))
            if [ -n "$free" ]; then
                UD_ST[i]=free; ut "бесплатный $free" "free $free"; UD_STTXT[i]="$UI_TX"
                if prod_free_shows_version "$id" && [ -n "$v" ]; then UD_SRV[i]="$v"; else UD_SRV[i]="—"; fi
            else
                UD_ST[i]=installed; UD_SRV[i]="${v:-?}"
                rv=$(ver_cache_get "$id" "${UD_BRANCH[i]}" 2>/dev/null) || rv=""
                if [ -n "$rv" ] && [ -n "$v" ] && ver_newer "$v" "$rv"; then
                    UD_UPD[i]=1; UD_UPDV[i]="$rv"
                    ut "обновление в ${UD_BRANCH[i]}" "update in ${UD_BRANCH[i]}"
                    UD_NEWS+=("↑|update|${UD_NAME[i]}|$v → $rv|$UI_TX")
                fi
            fi
        else
            UD_SRV[i]="—"
            st=$(prod_readiness "$id" 2>/dev/null) || st="g:"
            rest="${st#*:}"
            case "$st" in
                g:*) UD_ST[i]=ready ;;
                y:*) ut "готов, есть замечания" "ready, with remarks"; if [ "$rest" = "$UI_TX" ]; then UD_ST[i]=notes; else UD_ST[i]=lite; UD_STTXT[i]="$rest"; fi ;;
                *)   UD_ST[i]=block; UD_STTXT[i]="$rest" ;;
            esac
            if [ -n "${UD_MAIN[i]}" ] && ui_is_new "$id"; then
                UD_NEW[i]=1
                ut "новый продукт серии, main" "new in the series, main"
                UD_NEWS+=("✚|new|${UD_NAME[i]}|${UD_MAIN[i]}|$UI_TX")
            fi
        fi
        m=$(prod_mode_choices "$id")
        [ "$inst" = 1 ] && m="${m//lazy /}"
        UD_MODES[i]="$m"
        if [ "$inst" = 1 ] && v=$(prod_installed_mode "$id" 2>/dev/null); then UD_DEFMODE[i]="$v"; else UD_DEFMODE[i]=$(prod_mode_default "$id"); fi
        # Службы и порты.
        UD_SVC[i]=$(prod_units "$id"); UD_SVCOK[i]=""
        if [ "$inst" = 1 ] && command -v systemctl >/dev/null 2>&1; then
            UD_SVC[i]=""
            for u in $(prod_units "$id"); do
                us=$(unit_state "$u")
                case "$us" in
                    none) continue ;;
                    run)  UD_SVCOK[i]+=" $u:1" ;;
                    off)  UD_SVCOK[i]+=" $u:2" ;;
                    *)    UD_SVCOK[i]+=" $u:0"; ut "$u не работает" "$u is not running"
                          UD_PROB+=("${UD_NAME[i]}|$UI_TX|${UD_NAME[i]}: $UI_TX|journalctl -u $u") ;;
                esac
                UD_SVC[i]+="${UD_SVC[i]:+ }$u"
            done
        fi
        UD_HELD[i]=""; UD_HELDP[i]=""
        if [ "$inst" = 1 ]; then
            local l p a po pr pi own seen=" "
            while IFS='|' read -r p a po pr pi; do
                [ -n "$po" ] || continue
                own=$(holder_product "$pr" "$pi")
                [ "$own" = "$id" ] || continue
                case "$seen" in *" $po/$p "*) continue ;; esac
                seen+="$po/$p "; UD_HELD[i]+="${UD_HELD[i]:+ }$po/$p"; UD_HELDP[i]+="${UD_HELDP[i]:+ }$po/$p=$pr"
            done < <(printf '%s\n' "$LISTENERS" | sort -t'|' -k3,3n)
        else
            UD_NEED[i]=$(prod_ports "$id" "${UD_DEFMODE[i]:-pro}" | awk -F'|' '{printf "%s%s/%s", (NR>1?" ":""), $2, $1}')
            preflight "$id" "${UD_DEFMODE[i]:-pro}" >/dev/null 2>&1
            UD_VBAD[i]="$PF_MUST"; UD_VNOTES[i]="$PF_SOFT"
            # Первое замечание или препятствие словами — для карточки: одно
            # число «замечаний: 1» не говорит, что именно не так.
            UD_VWHY[i]=""
            for m in ${PF_MSGS[@]+"${PF_MSGS[@]}"}; do case "$m" in e\|*|w\|*) UD_VWHY[i]="${m#?|}"; break ;; esac; done
        fi
    done
    # Сам goInstaller.
    if [ -n "$UD_VERNEW" ]; then ut "новая версия установщика" "new installer version"; UD_NEWS+=("↑|update|goInstaller|$GOINST_VERSION → $UD_VERNEW|$UI_TX"); fi
    cert_scan
    UD_PROB=("${UI_CERTP[@]}" ${UD_PROB[@]+"${UD_PROB[@]}"})
    # Ключ.
    UD_KEYDENIED=""; UD_KEYOK=6; UD_KEYTOK=muted
    case "${TOKEN_STATUS:-}" in
        ok)      ut "сохранён, проверен в ${UI_KEYAT:-$UD_SVED}" "saved, checked at ${UI_KEYAT:-$UD_SVED}"; UD_KEYNOTE="$UI_TX" ;;
        denied)  UD_KEYDENIED="${TOKEN_DENIED% }"; c=0; for k in $TOKEN_DENIED; do [ "$k" = pro ] && c=$(( c + 1 )); done
                 UD_KEYOK=$(( 6 - c )); ut "не открывает ${UD_KEYDENIED}" "does not open ${UD_KEYDENIED}"; UD_KEYNOTE="$UI_TX"; UD_KEYTOK=warn ;;
        limited) ut "лимит GitHub до $(gh_reset_text)" "GitHub limit until $(gh_reset_text)"; UD_KEYNOTE="$UI_TX"; UD_KEYTOK=warn ;;
        nonet)   ut "GitHub недоступен" "GitHub is unreachable"; UD_KEYNOTE="$UI_TX"; UD_KEYTOK=warn ;;
        *)       if [ -n "${TOKEN:-}" ]; then ut "сохранён" "saved"; UD_KEYNOTE="$UI_TX"; else ut "ключа нет" "no key"; UD_KEYNOTE="$UI_TX"; UD_KEYTOK=warn; fi ;;
    esac
    if [ "${LANG_CODE:-ru}" = en ]; then UD_LANGNOTE="English"; else UD_LANGNOTE="русский"; fi
    [ -n "$own_cache" ] && scan_cache_end
    return 0
}

# Строки проверки портов для экрана: preflight считает, экран показывает.
ui_pf_load() {  # ui_pf_load <id> <режим>
    local r port st what who m t
    with_scan_cache preflight "$1" "${2:-pro}" >/dev/null 2>&1
    UI_PFR_PORT=(); UI_PFR_ST=(); UI_PFR_D=(); UI_PFR_H=()
    # Свободные порты функций, которые включают потом (prod_ports_opt), —
    # одной строкой: у goKaskad pro 3 их полтора десятка, и экран без
    # прокрутки терял пункт «Продолжить». Занятые — каждый своей строкой.
    local opt nopt=0
    opt=" $(prod_ports_opt "$1" | awk -F'|' '{printf "%s/%s ", $2, $1}')"
    for r in ${PF_ROWS[@]+"${PF_ROWS[@]}"}; do
        IFS='|' read -r port st what who <<<"$r"
        if [ "$st" = ok ] && [[ "$opt" == *" $port "* ]]; then nopt=$(( nopt + 1 )); continue; fi
        UI_PFR_PORT+=("$port"); UI_PFR_ST+=("$st"); UI_PFR_D+=("$what"); UI_PFR_H+=("$who")
    done
    if [ "$nopt" -gt 0 ]; then
        ut "порты функций продукта свободны" "the product's feature ports are free"
        UI_PFR_PORT+=("+${nopt}"); UI_PFR_ST+=(ok); UI_PFR_D+=("$UI_TX"); UI_PFR_H+=("")
    fi
    UI_PFU_G=(); UI_PFU_T=(); UI_PFU_TXT=()
    for m in ${PF_MSGS[@]+"${PF_MSGS[@]}"}; do
        t="${m%%|*}"; m="${m#*|}"
        case "$t" in e) UI_PFU_G+=('✗'); UI_PFU_T+=(err) ;; w) UI_PFU_G+=('!'); UI_PFU_T+=(warn) ;; o) UI_PFU_G+=('✓'); UI_PFU_T+=(ok) ;; *) UI_PFU_G+=('·'); UI_PFU_T+=(muted) ;; esac
        UI_PFU_TXT+=("$m")
    done
    UI_PF_BAD="$PF_MUST"; UI_PF_NOTES="$PF_SOFT"
}
# Итог проверки для каждого режима — тег в вопросе о режиме.
ui_modes_load() {  # ui_modes_load <индекс продукта>
    local m id="${PRODUCTS[$1]}"
    declare -gA UI_MVBAD=() UI_MVNOTES=()
    _ui_modes_scan() {
        for m in ${UD_MODES[$1]}; do
            preflight "$id" "$m" >/dev/null 2>&1
            UI_MVBAD[$m]="$PF_MUST"; UI_MVNOTES[$m]="$PF_SOFT"
        done
    }
    with_scan_cache _ui_modes_scan "$1"
}
# Карта портов: группы по владельцу — продукты серии, потом система и прочие.
ui_ports_load() {
    local p a po pr pi own id what key i
    declare -A rows=() seenp=()
    scan_listeners; scan_docker
    while IFS='|' read -r p a po pr pi; do
        [ -n "$po" ] || continue
        [ -z "${seenp[$po/$p]:-}" ] || continue
        seenp[$po/$p]=1
        own=$(holder_product "$pr" "$pi")
        what="$pr"
        for id in "${PRODUCTS[@]}"; do
            [ "$own" = "$id" ] || continue
            what=$({ for m in $(prod_modes "$id") pro; do prod_ports "$id" "$m"; done; prod_ports_opt "$id"; } | awk -F'|' -v P="$po" -v Q="$p" '$1==Q && $2==P {print $4; exit}')
            [ -n "$what" ] || what="$pr"
        done
        case "$own" in govless|gotelegram|godns|gokaskad|gowarp|goamnezia) key="$own" ;; system) key="@system" ;; *) key="@other" ;; esac
        rows[$key]+="${rows[$key]:+;}$po/$p|$pr|$what"
    done < <(printf '%s\n' "$LISTENERS" | sort -t'|' -k3,3n -k1,1)
    UI_PG_NAME=(); UI_PG_ROWS=()
    for id in "${PRODUCTS[@]}"; do [ -n "${rows[$id]:-}" ] && { UI_PG_NAME+=("$(prod_name "$id")"); UI_PG_ROWS+=("${rows[$id]}"); }; done
    if [ -n "${rows[@system]:-}" ]; then ut "система" "system"; UI_PG_NAME+=("$UI_TX"); UI_PG_ROWS+=("${rows[@system]}"); fi
    if [ -n "${rows[@other]:-}" ]; then ut "прочие" "others"; UI_PG_NAME+=("$UI_TX"); UI_PG_ROWS+=("${rows[@other]}"); fi
    if [ "${#UI_PG_NAME[@]}" -eq 0 ]; then ut "слушателей нет" "no listeners"; UI_PG_NAME=("$UI_TX"); UI_PG_ROWS=(""); fi
    return 0
}

# ── опрос GitHub в фоне с ходом для заставки ───────────────────────────
# Восемь строк: goInstaller, ключ, шесть продуктов. Каждая задача по
# окончании кладёт файл в UI_POLLDIR; экран читает их на каждом такте.
ui_poll_start() {
    local id cfg
    # Без сети (GOINST_NO_NET) не опрашиваем и по таймеру меню: с подставным
    # ключом опрос получал отказ и затирал кеш версий пустыми строками.
    [ -z "${GOINST_NO_NET:-}" ] || return 1
    UI_POLLDIR=$(mktemp -d "${TMPDIR:-/tmp}/goinst-poll.XXXXXX") || return 1
    UI_POLLT0=$SECONDS
    (
        cfg=$(mktemp "${TMPDIR:-/tmp}/goinst-pollc.XXXXXX") || exit 0
        chmod 600 "$cfg"
        { echo 'silent'; echo 'location'; echo 'fail'; gh_cfg_net
          printf 'header = "Authorization: token %s"\n' "$TOKEN"; } > "$cfg"
        # Повторы и «сети нет» — общие на весь опрос: заставка их показывает.
        GH_RETRY_LOG="$UI_POLLDIR/retries"; GH_DOWN_FILE="$UI_POLLDIR/down"; GH_SAY=""
        ( token_check >/dev/null 2>&1; printf '%s|%s|%s\n' "${TOKEN_STATUS:-}" "${TOKEN_DENIED:-}" "${TOKEN_NETRC:-}" > "$UI_POLLDIR/key" ) &
        ( rm -f "$(_ver_cache_file "$SELF_ID" main)"; self_fetch_versions >/dev/null 2>&1; : > "$UI_POLLDIR/self" ) &
        for id in "${PRODUCTS[@]}"; do
            (
                refs_fetch_one "$id" "$cfg" >/dev/null 2>&1
                for ref in $(prod_refs_probe "$id"); do _ver_fetch_one "$id" "$ref" "$cfg" >/dev/null 2>&1; done
                if ver_cache_get "$id" main >/dev/null 2>&1 && [ -n "$(ver_cache_get "$id" main)" ]; then : > "$UI_POLLDIR/p_$id"; else echo fail > "$UI_POLLDIR/p_$id"; fi
            ) &
        done
        wait
        rm -f "$cfg"
        : > "$UI_POLLDIR/all"
    ) </dev/null >/dev/null 2>&1 &
    UI_POLLPID=$!
    return 0
}
# Состояние опроса → строки заставки UI_PI_* и счётчик UI_POLL.
ui_poll_state() {
    local i id n=0 st res tok k den
    UI_PI_NOTE=""
    if [ -s "$UI_POLLDIR/retries" ]; then
        local lrc ltry ltot
        read -r lrc ltry ltot < <(tail -n 1 "$UI_POLLDIR/retries")
        UI_PI_NOTE="$(bi "GitHub отвечает с трудом, повтор ${ltry} из ${ltot}" "GitHub answers with difficulty, retry ${ltry} of ${ltot}"): $(gh_net_reason "$lrc")"
    fi
    UI_PI_NAME=("goInstaller"); ut "ключ доступа" "access key"; UI_PI_NAME+=("$UI_TX")
    for id in "${PRODUCTS[@]}"; do UI_PI_NAME+=("$(prod_name "$id")"); done
    UI_PI_ST=(); UI_PI_RES=(); UI_PI_TOK=()
    if [ -e "$UI_POLLDIR/self" ]; then
        local sv; sv=$(ver_cache_get "$SELF_ID" main 2>/dev/null) || sv=""
        UI_PI_ST+=(done); UI_PI_RES+=("${sv:-$GOINST_VERSION}"); if [ -n "$sv" ] && ver_newer "$GOINST_VERSION" "$sv"; then UI_PI_TOK+=(update); else UI_PI_TOK+=(text); fi
    else UI_PI_ST+=(run); UI_PI_RES+=(""); UI_PI_TOK+=(text); fi
    if [ -e "$UI_POLLDIR/key" ]; then
        IFS='|' read -r st den _ < "$UI_POLLDIR/key"
        k=0; for id in $den; do [ "$id" = pro ] && k=$(( k + 1 )); done
        case "$st" in
            ok|denied) ut "открывает $(( 6 - k )) из 6" "opens $(( 6 - k )) of 6"; UI_PI_ST+=(done); UI_PI_RES+=("$UI_TX"); if [ "$k" -gt 0 ]; then UI_PI_TOK+=(warn); else UI_PI_TOK+=(text); fi ;;
            *) UI_PI_ST+=(fail); UI_PI_RES+=(""); UI_PI_TOK+=(err) ;;
        esac
    else UI_PI_ST+=(run); UI_PI_RES+=(""); UI_PI_TOK+=(text); fi
    for ((i = 0; i < ${#PRODUCTS[@]}; i++)); do
        id="${PRODUCTS[$i]}"
        if [ -e "$UI_POLLDIR/p_$id" ]; then
            if [ -s "$UI_POLLDIR/p_$id" ]; then UI_PI_ST+=(fail); UI_PI_RES+=(""); UI_PI_TOK+=(err); continue; fi
            local refs mv dv; read -r -a refs <<<"$(prod_refs "$id")"
            mv=$(ver_cache_get "$id" "${refs[0]:-main}" 2>/dev/null) || mv=""; res="${refs[0]:-main} ${mv:-—}"
            if [ -n "${refs[1]:-}" ]; then dv=$(ver_cache_get "$id" "${refs[1]}" 2>/dev/null) || dv=""; res+=" · ${refs[1]} ${dv:-—}"; fi
            tok=text
            if prod_installed "$id"; then local lv; lv=$(prod_version "$id" 2>/dev/null) || lv=""; [ -n "$lv" ] && [ -n "$mv" ] && ver_newer "$lv" "$mv" && tok=update
            elif ui_is_new "$id"; then tok=new; fi
            UI_PI_ST+=(done); UI_PI_RES+=("$res"); UI_PI_TOK+=("$tok")
        else UI_PI_ST+=(run); UI_PI_RES+=(""); UI_PI_TOK+=(text); fi
    done
    for st in "${UI_PI_ST[@]}"; do case "$st" in done|fail) n=$(( n + 1 )) ;; esac; done
    UI_POLL="$n"
    [ -e "$UI_POLLDIR/all" ]
}
# Опрос кончился: отметки, ключ, сведения, ошибка — и прибрать за собой.
ui_poll_finish() {
    local st den
    [ -n "${UI_POLLDIR:-}" ] || return 0
    UI_NETRC=""
    if [ -r "$UI_POLLDIR/key" ]; then IFS='|' read -r st den UI_NETRC < "$UI_POLLDIR/key"; TOKEN_STATUS="$st"; TOKEN_DENIED="$den"; UI_KEYAT=$(date +%H:%M); fi
    # Ключ из окружения сохраняем, когда он подтвердился: иначе установка
    # одной командой работала бы ровно один раз.
    if [ "${TOKEN_FROM:-}" = env ] && [ "${TOKEN_STATUS:-}" = ok ] && [ "$TOKEN" != "$(head -1 "$TOKEN_FILE" 2>/dev/null)" ]; then token_save "$TOKEN" >/dev/null 2>&1 || true; fi
    UI_POLLERR=""
    if gh_limited >/dev/null; then UI_POLLERR=rate; UI_RESET=$(gh_reset_text)
    elif [ "${TOKEN_STATUS:-}" = nonet ]; then UI_POLLERR=github
    else UI_SVED=$(date +%H:%M); fi
    UI_POLLOK=0; local s; for s in ${UI_PI_ST[@]+"${UI_PI_ST[@]}"}; do [ "$s" = done ] && UI_POLLOK=$(( UI_POLLOK + 1 )); done
    VER_NET_TRIED="yes"; _ver_mark_ok "${PRODUCTS[@]}"
    rm -rf "$UI_POLLDIR"; UI_POLLDIR=""; UI_POLL=""; UI_POLLPID=""
}

# ── клавиши ────────────────────────────────────────────────────────────
# ui_read_key [тайм-аут] → UI_KEY: up down enter esc back tick eof paste
# или сам символ. Русская раскладка: з к д г й — это p r l u q.
# Вставка из буфера приходит пачкой за миллисекунды: её гасим целиком,
# иначе вставленная команда нажимала бы пункты меню буква за буквой.
ui_read_key() {
    local k rest="" rc t="${1:-}" more
    UI_KEY=""
    if [ -n "$t" ]; then IFS= read -rsn1 -t "$t" k; rc=$?; else IFS= read -rsn1 k; rc=$?; fi
    if [ "$rc" -ne 0 ]; then if [ "$rc" -gt 128 ]; then UI_KEY=tick; else UI_KEY=eof; fi; return 0; fi
    case "$k" in
        # \r — тоже Enter: так приходит Enter не из терминала (ввод каналом,
        # «отправить текст» в SSH-клиенте). Из терминала bash на время
        # read -n сам включает icrnl — проверено на сервере с stty -icrnl.
        # Несколько Enter пачкой — одно нажатие.
        ''|$'\r')
            if [ "${UI_INPUT:-0}" = 0 ]; then
                while IFS= read -rsn1 -t 0.01 more; do
                    case "$more" in ''|$'\r') ;; *) while IFS= read -rsn1 -t 0.01 more; do :; done; UI_KEY=paste; return 0 ;; esac
                done
            fi
            UI_KEY=enter; return 0 ;;
        $'\177'|$'\b') UI_KEY=back; return 0 ;;
        $'\033')
            IFS= read -rsn2 -t 0.05 rest 2>/dev/null || rest=""
            # «\033O…» — те же стрелки в режиме курсора приложения: в нём
            # терминал остаётся, если его не вернула прошлая программа.
            case "$rest" in
                '[A'|OA) UI_KEY=up ;; '[B'|OB) UI_KEY=down ;; '[C'|OC) UI_KEY=enter ;; '[D'|OD) UI_KEY=esc ;;
                # F-клавиши: «\033OQ» (xterm, iTerm, Termius), «\033[12~»
                # (rxvt, PuTTY), «\033[[B» (консоль Linux). F1, F10, F11 не
                # берём: их перехватывают система и терминалы.
                OQ) UI_KEY=f2 ;; OR) UI_KEY=f3 ;; OS) UI_KEY=f4 ;;
                '[1'|'[[')
                    local tail="" ch
                    while IFS= read -rsn1 -t 0.05 ch; do tail+="$ch"; case "$ch" in '~'|[A-E]) break ;; esac; done
                    case "$rest$tail" in
                        '[12~'|'[[B'|'[1;1Q'|'[1Q') UI_KEY=f2 ;;
                        '[13~'|'[[C'|'[1R') UI_KEY=f3 ;;
                        '[14~'|'[[D'|'[1S') UI_KEY=f4 ;;
                        '[15~'|'[[E') UI_KEY=f5 ;;
                        *) UI_KEY=other ;;
                    esac ;;
                '') UI_KEY=esc ;;
                *) while IFS= read -rsn1 -t 0.01 more; do :; done; UI_KEY=other ;;
            esac
            return 0 ;;
    esac
    if [ "${UI_INPUT:-0}" = 0 ] && IFS= read -rsn1 -t 0.01 more; then
        # Вслед пришло ещё. «Цифра и сразу Enter» — привычка прежнего меню, а
        # ту же клавишу жмут по нескольку раз, когда экран не отозвался сразу:
        # это одно нажатие. До 1.0.2 такая пачка шла во «вставку» и молча
        # пропадала — меню выглядело зависшим. Всё прочее — вставка из буфера,
        # её не исполняем: вставленная команда не должна жать пункты меню.
        local burst="$more"
        while IFS= read -rsn1 -t 0.01 more; do burst+="$more"; done
        burst="${burst//$'\r'/}"; burst="${burst//"$k"/}"
        if [ -n "$burst" ]; then UI_KEY=paste; return 0; fi
    fi
    [ "${UI_INPUT:-0}" = 0 ] && case "$k" in з|З) k=p ;; к|К) k=r ;; д|Д) k=l ;; г|Г) k=u ;; й|Й) k=q ;; esac
    UI_KEY="$k"
}

# Сейчас в миллисекундах → UI_NOW. EPOCHREALTIME пишет дробь через
# десятичный знак локали (в ru_RU — запятая), поэтому убираем оба.
_ui_now_ms() {
    local t="${EPOCHREALTIME:-}"
    if [ -n "$t" ]; then t="${t//[!0-9]/}"; UI_NOW=$(( 10#${t:0:${#t}-3} )); else UI_NOW=$(( SECONDS * 1000 )); fi
}
# Выбросить набранное вслепую, пока экран был отдан продукту.
ui_flush_input() { local _k; while IFS= read -rsn1 -t 0.01 _k; do :; done; return 0; }

# ── навигация ──────────────────────────────────────────────────────────
# Экран — UI_SCR и его параметры; назад — стек. Рисует ui_draw, действия —
# ui_act. Установщик продукта идёт в обычном терминале: экран отдаётся ему
# целиком, как было и до «Аркады».
UI_STACK=()
ui_push() { UI_STACK+=("$UI_SCR|$UI_CUR|${UI_WP:-}|${UI_PMODE:-}|${UI_PBRANCH:-}|${UI_WIZ:-0}|${UI_ONLY:-0}"); }
ui_go() { ui_push; UI_SCR="$1"; UI_CUR="${2:-0}"; }
ui_back() {
    local n="${#UI_STACK[@]}" e
    if [ "$n" -eq 0 ]; then UI_SCR=quit; return 0; fi
    e="${UI_STACK[$(( n - 1 ))]}"; unset "UI_STACK[$(( n - 1 ))]"; UI_STACK=(${UI_STACK[@]+"${UI_STACK[@]}"})
    IFS='|' read -r UI_SCR UI_CUR UI_WP UI_PMODE UI_PBRANCH UI_WIZ UI_ONLY <<<"$e"
}
ui_root() { UI_STACK=(); UI_SCR="${1:-menu}"; UI_CUR=0; }
ui_back_to() {  # ui_back_to <экран> — назад до него, нет в стеке — в меню
    local i n="${#UI_STACK[@]}" e s
    for ((i = n - 1; i >= 0; i--)); do
        e="${UI_STACK[$i]}"; s="${e%%|*}"
        if [ "$s" = "$1" ]; then
            UI_STACK=("${UI_STACK[@]:0:$i}")
            IFS='|' read -r UI_SCR UI_CUR UI_WP UI_PMODE UI_PBRANCH UI_WIZ UI_ONLY <<<"$e"; return 0
        fi
    done
    ui_root menu
}

ui_enter_screen() { printf '\033[?1049h\033[?25l' >/dev/tty 2>/dev/null || printf '\033[?1049h\033[?25l'; UI_ALT=1; }
ui_leave_screen() { printf '\033[0m\033[?25h\033[?1049l' >/dev/tty 2>/dev/null || printf '\033[0m\033[?25h\033[?1049l'; UI_ALT=0; }

# Нарисовать текущий экран.
ui_draw() {
    local p="${UI_WP:-0}"
    UI_SPIN="${UI_SPINS:0:1}"
    case "$UI_C" in 40) UI_SCR_DRAWN=small; ui_scr_small; ui_compose; ui_emit
                        [ -n "${GOINST_UI_TRACE:-}" ] && printf 'small\n' >> "${GOINST_UI_TRACE}.draw"
                        return 0 ;; esac
    UI_SCR_DRAWN="$UI_SCR"
    case "$UI_SCR" in
        menu)    [ -n "${UI_POLLDIR:-}" ] && ui_poll_state; ui_scr_menu "$UI_CUR" ;;
        splash)  ui_poll_state; ui_scr_splash ;;
        product) ui_scr_product "$p" "$UI_CUR" ;;
        mode)    ui_scr_mode "$p" "$UI_CUR" "${UI_ONLY:-0}" ;;
        check)   ui_scr_check "$p" "$UI_CUR" "${UI_WIZ:-0}" ;;
        branch)  ui_scr_branch "$p" "$UI_CUR" ;;
        confirm) ui_scr_confirm "$p" "$UI_CUR" ;;
        result)  ui_scr_result "$p" "$UI_CUR" ;;
        ports)   ui_scr_ports "$UI_CUR" ;;
        key)     ui_scr_key "$UI_CUR" ;;
        keyinput) ui_scr_keyinput "${#UI_KEYBUF}" "${UI_KEYST:-}" ;;
        lang)    ui_scr_lang "$UI_CUR" ;;
        err)     ui_scr_err "${UI_POLLERR:-github}" "$UI_CUR" ;;
        selfupd) ui_scr_selfupd "$UI_CUR" ;;
        uninstall) ui_scr_uninstall "$UI_CUR" ;;
        sumask)  ui_scr_sumask "$UI_CUR" ;;
    esac
    # Короткая подсказка поверх строки сведений: пропущенная вставка и т. п.
    [ -n "${UI_NOTE:-}" ] && UI_INFO="warn${UI_US}  ${UI_NOTE}${UI_RS}"
    ui_compose; ui_emit
    [ -n "${GOINST_UI_TRACE:-}" ] && printf '%s\n' "$UI_SCR_DRAWN" >> "${GOINST_UI_TRACE}.draw"
    return 0
}
# Удалить goInstaller: что уйдёт, что останется, выбор про ключ.
ui_scr_uninstall() {
    local l
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ut "Удалить goInstaller" "Remove goInstaller"; ui_hdr "title${UI_US}${UI_TX}${UI_RS}" ""; ui_body ""
    ut "Уйдут команды goinstall и go-installer, каталог /opt/go-installer и таймер сводки при входе. Продукты, их команды и настройки останутся, размер пакета сетевой карты — тоже." "The goinstall and go-installer commands, /opt/go-installer and the login summary timer go away. The products, their commands and settings stay, and so does the network card packet size."
    ui_wrap "$UI_TX" $(( UI_C - 4 )); for l in "${UI_WRAP[@]}"; do ui_body "muted${UI_US}  ${l}${UI_RS}"; done
    ui_body ""
    if [ -f "$TOKEN_FILE" ]; then
        ut "Ключ клуба goKaskad pro берёт как запасной, а goInstaller подхватит при новой установке." "goKaskad pro uses the club key as a fallback, and goInstaller picks it up when installed again."
        ui_wrap "$UI_TX" $(( UI_C - 4 )); for l in "${UI_WRAP[@]}"; do ui_body "muted${UI_US}  ${l}${UI_RS}"; done
        ui_body ""
    fi
    ui_items_clear
    ut "Удалить вместе с ключом" "Remove, key included"; ui_item 1 "$UI_TX" uninstall_drop
    if [ -f "$TOKEN_FILE" ]; then ut "Удалить, ключ оставить" "Remove, keep the key"; ui_item 2 "$UI_TX" uninstall_keep; fi
    ut "Назад" "Back"; ui_item 0 "$UI_TX" back
    ui_choicerows "$UI_CUR"
    local a; ut "выбрать" "choose"; a="$UI_TX"; ut "назад" "back"; ui_keys_fit "1–2|$a|" "0|$UI_TX|"
}
# Обновить goInstaller: подтверждение цифрой.
ui_scr_selfupd() {
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ut "Обновить goInstaller" "Update goInstaller"; ui_hdr "title${UI_US}${UI_TX}${UI_RS}" ""; ui_body ""
    ut " · ветка main" " · branch main"
    ui_body "title${UI_US}  goInstaller ${UI_RS}text${UI_US}${UD_VER}${UI_RS}muted${UI_US} → ${UI_RS}update${UI_US}${UD_VERNEW:-?}${UI_RS}muted${UI_US}${UI_TX}${UI_RS}"; ui_body ""
    ut "Запустить обновление?" "Run the update?"; ui_body "title${UI_US}  ${UI_TX}${UI_RS}"
    ut "Да" "Yes"; ui_body "muted${UI_US}  Enter — 1, ${UI_TX}${UI_RS}"; ui_body ""
    ui_items_clear; ut "Да" "Yes"; ui_item 1 "$UI_TX" selfyes; ut "Нет" "No"; ui_item 2 "$UI_TX" back
    ui_choicerows "$UI_CUR"
    local a; ut "да" "yes"; a="$UI_TX"; ut "нет" "no"; local b="$UI_TX"; ut "назад" "back"; ui_keys_fit "1|$a|" "2|$b|" "0|$UI_TX|"
}

# Пункты текущего экрана — те же, что нарисованы: действие по ключу берётся
# из IT_*/MI_* после ui_draw.
ui_items_count() { if [ "$UI_SCR" = menu ]; then UI_NI="${#MI_KEY[@]}"; else UI_NI="${#IT_KEY[@]}"; fi; }
ui_item_act() {  # ui_item_act <номер пункта>
    local i="$1"
    if [ "$UI_SCR" = menu ]; then
        case "${MI_KIND[$i]}" in
            p) UI_WP="${MI_P[$i]}"; ui_mark_seen "${PRODUCTS[${MI_P[$i]}]}"; ui_go product 0 ;;
            *) case "${MI_KEY[$i]}" in 7) ui_ports_load; ui_go ports 0 ;; 8) ui_go key 0 ;; 9) ui_go lang "$([ "${LANG_CODE:-ru}" = en ] && echo 1 || echo 0)" ;; u) ui_go selfupd 0 ;; x) ui_go uninstall 0 ;; 0) UI_SCR=quit ;; esac ;;
        esac
        return 0
    fi
    ui_act "${IT_ACT[$i]}"
}
# Выбор пункта по ключу: цифра или буква пункта (горячая клавиша меню).
ui_item_by_key() {  # ui_item_by_key <символ> → UI_I или rc 1
    local k="$1" i
    ui_items_count
    for ((i = 0; i < UI_NI; i++)); do
        if [ "$UI_SCR" = menu ]; then
            if [ "${MI_KEY[$i]}" = "$k" ] || { [ -n "${MI_HOT[$i]}" ] && [ "${MI_HOT[$i]}" = "$k" ]; }; then UI_I=$i; return 0; fi
        elif [ "${IT_KEY[$i]}" = "$k" ]; then UI_I=$i; return 0; fi
    done
    return 1
}

# Мастер установки: режим → проверка → ветка → подтверждение → установка → сверка.
ui_wizard() {  # ui_wizard <индекс продукта>
    local p="$1" id="${PRODUCTS[$1]}" m
    UI_WP="$p"; UI_WIZ=1; UI_ONLY=0; UI_PMODE=""; UI_PBRANCH=""
    if [ -n "${UD_MODES[$p]:-}" ]; then
        # Режим стоящего продукта записан в его настройках — не спрашиваем.
        if [ "${UD_INST[$p]:-0}" = 1 ] && [ "${UD_ST[$p]}" != free ] && m=$(prod_installed_mode "$id" 2>/dev/null); then
            UI_PMODE="$m"; ui_pf_load "$id" "$m"; ui_go check 0
        else
            ui_modes_load "$p"; ui_go mode 0
            local i=0 x; for x in ${UD_MODES[$p]}; do [ "$x" = "${UD_DEFMODE[$p]}" ] && UI_CUR=$i; i=$(( i + 1 )); done
        fi
    else ui_pf_load "$id" pro; ui_go check 0; fi
}
ui_check_only() {  # ui_check_only <индекс продукта>
    local p="$1" id="${PRODUCTS[$1]}" m
    UI_WP="$p"; UI_WIZ=0
    if [ -n "${UD_MODES[$p]:-}" ]; then
        if [ "${UD_INST[$p]:-0}" = 1 ] && m=$(prod_installed_mode "$id" 2>/dev/null); then UI_PMODE="$m"; ui_pf_load "$id" "$m"; ui_go check 0
        else UI_ONLY=1; ui_modes_load "$p"; ui_go mode 0; fi
    else UI_PMODE=""; ui_pf_load "$id" pro; ui_go check 0; fi
}
# Запуск установщика продукта: экран отдаётся ему, после — сверка.
ui_run_installer() {
    local p="$UI_WP" id="${PRODUCTS[$UI_WP]}" rc got m st
    ui_leave_screen; clear 2>/dev/null || true
    st=$(stty -g 2>/dev/null) || st=""
    MODE="${UI_PMODE:-pro}"; REF="${UI_PBRANCH:-$(prod_ref "$id")}"
    free_takeover_note "$id"
    run_product_installer "$id"; rc=$?
    [ -n "$st" ] && stty "$st" 2>/dev/null
    echo ""
    if [ "$rc" -eq 0 ]; then ok "$(bi "Установщик $(prod_name "$id") завершился." "The $(prod_name "$id") installer has finished.")"
    else warn "$(bi "Установщик $(prod_name "$id") вышел с кодом ${rc}." "The $(prod_name "$id") installer exited with code ${rc}.")"; fi
    echo -ne "  $(bi "Нажмите Enter — сверка итога" "Press Enter — verify the result") "; read -r || true
    ui_flush_input
    say "$(bi "Сверяю итог…" "Verifying the result…")"
    ui_model_load
    UI_MISM=0; UI_LAZYMODE=""
    got=$(prod_installed_ref "$id" raw 2>/dev/null) || got=""
    if [ -n "$got" ] && [ "$got" != "$REF" ]; then UI_MISM=1; fi
    if [ "$MODE" = lazy ] && m=$(prod_installed_mode "$id" 2>/dev/null); then UI_LAZYMODE="$m"; fi
    ui_enter_screen
    ui_back_to product; ui_go result 0
}
# Открыть меню самого продукта: его команда в обычном терминале.
ui_open_product() {
    local cmd="${UD_CMD[$UI_WP]}" st
    ui_leave_screen; clear 2>/dev/null || true
    # Настройки терминала — до продукта и назад после: меню продукта может
    # оставить его «сырым» (без icrnl), и построчный read («Нажмите Enter»)
    # тогда ждал бы Ctrl-J вместо Enter.
    st=$(stty -g 2>/dev/null) || st=""
    if command -v "$cmd" >/dev/null 2>&1; then "$cmd" || true
    else warn "$(bi "Команды ${cmd} нет в PATH — запустите обновление." "Command ${cmd} is not in PATH — run the update.")"; sleep 2; fi
    [ -n "$st" ] && stty "$st" 2>/dev/null
    # Экран — сразу, сведения — потом: пересборка занимает секунды, и
    # пустое окно всё это время выглядело зависшим.
    ui_flush_input
    ui_enter_screen; ui_draw; ui_model_load
}

ui_act() {  # ui_act <действие пункта>
    local a="$1" id
    case "$a" in
        back) ui_back ;;
        root) ui_root menu ;;
        quit) UI_SCR=quit ;;
        openprod) ui_open_product ;;
        wizard) ui_wizard "$UI_WP" ;;
        check) ui_check_only "$UI_WP" ;;
        othermode) UI_ONLY=1; ui_modes_load "$UI_WP"; ui_go mode 0 ;;
        mode:*) UI_PMODE="${a#mode:}"; ui_pf_load "${PRODUCTS[$UI_WP]}" "$UI_PMODE"; ui_go check 0 ;;
        continue) if [ -n "${UD_DEV[$UI_WP]:-}" ]; then
                      if [ "${UD_INST[$UI_WP]:-0}" = 1 ] && [ "${UD_BRANCH[$UI_WP]:-main}" = "${UD_DEVREF[$UI_WP]:-dev}" ]; then ui_go branch 1; else ui_go branch 0; fi
                  else UI_PBRANCH="main"; ui_go confirm 0; fi ;;
        branch:*) UI_PBRANCH="${a#branch:}"; ui_go confirm 0 ;;
        yes) ui_run_installer ;;
        no) ui_back_to product ;;
        group:*) UI_CUR="${a#group:}" ;;
        keycheck) token_check; UI_KEYAT=$(date +%H:%M); ui_model_load ;;
        keyinput) UI_KEYBUF=""; UI_KEYST=""; UI_INPUT=1; ui_go keyinput 0 ;;
        keyforget) true ;;
        lang:*) LANG_CODE="${a#lang:}"; LANG_ASKED=yes; lang_persist; ui_model_load; ui_back ;;
        retry) ui_poll_start && ui_root splash ;;
        sumyes) summary_enable; ui_first_screen ;;
        sumno) summary_disable; ui_first_screen ;;
        uninstall_drop|uninstall_keep)
            # Удаление — после выхода из экрана: сам скрипт после него
            # продолжать нечем.
            UI_UNINSTALL="${1#uninstall_}"; UI_SCR=quit ;;
        selfyes) ui_leave_screen; clear 2>/dev/null || true; self_update; echo -ne "  $(bi "Нажмите Enter" "Press Enter") "; read -r || true
                 if [ -x "$GOINST_BIN" ]; then exec "$GOINST_BIN"; fi; UI_SCR=quit ;;
    esac
}

# Первый экран: нет ключа — ввод ключа; сведения свежие — меню; иначе
# заставка с опросом GitHub.
ui_first_screen() {
    ui_root menu
}

# Главный цикл «Аркады».
ui_main() {
    local t0 tick=0.5 last_poll=$SECONDS refresh="${GOINST_MENU_REFRESH:-180}" n dirty=1 scr0=""
    case "$refresh" in ''|*[!0-9]*) refresh=0 ;; esac
    UI_SPINS='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'; UI_INPUT=0; UI_RESIZED=0
    ui_detect
    trap 'UI_RESIZED=1' WINCH
    trap 'ui_leave_screen; exit 130' INT TERM
    # Упали посреди экрана — вернуть терминал из альтернативного экрана с
    # видимым курсором, иначе человек остаётся перед пустым окном.
    trap 'ui_crash_exit' EXIT
    ui_enter_screen
    ui_model_load
    # Сводку при входе спрашиваем один раз, до первого экрана.
    if [ ! -e "$SUM_CHOICE" ] && [ -z "${GOINST_NO_SUMASK:-}" ]; then ui_root sumask; else ui_first_screen; fi
    while [ "$UI_SCR" != quit ]; do
        if [ "$UI_RESIZED" = 1 ]; then UI_RESIZED=0; ui_size; ui_tokens; dirty=1; fi
        if [ -n "${UI_NOTE:-}" ] && [ "$SECONDS" -ge "${UI_NOTE_T:-0}" ]; then UI_NOTE=""; dirty=1; fi
        # Кадр — только когда есть что менять: нажатие, опрос со спиннером,
        # новое окно. Раньше экран уходил целиком раз в секунду, а это лишние
        # килобайты в секунду по SSH с телефона.
        if [ "$dirty" = 1 ] || [ -n "${UI_POLLDIR:-}" ]; then ui_draw; fi
        dirty=0
        # Заставка и опрос: такт для спиннера; иначе — ждём клавишу, но
        # раз в секунду проверяем, не пора ли меню спросить GitHub.
        if [ -n "${UI_POLLDIR:-}" ]; then tick=0.1; else tick=1; fi
        ui_read_key "$tick"
        UI_SPINS="${UI_SPINS:1}${UI_SPINS:0:1}"
        if [ -n "${UI_POLLDIR:-}" ] && ui_poll_state; then
            ui_poll_finish; last_poll=$SECONDS; dirty=1
            # Ключ отвергнут — стираем и просим новый (исчерпанный лимит сюда
            # не попадает: это состояние limited, ключ остаётся).
            if [ "${TOKEN_STATUS:-}" = bad ]; then token_forget; TOKEN=""; UI_KEYBAD=1; ui_model_load; continue; fi
            ui_model_load
            if [ "$UI_SCR" = splash ]; then if [ -n "$UI_POLLERR" ]; then ui_root err; else ui_root menu; fi; fi
        fi
        if [ "$UI_SCR" = menu ] && [ "$refresh" -gt 0 ] && [ -z "${UI_POLLDIR:-}" ] && [ $(( SECONDS - last_poll )) -ge "$refresh" ] && ! gh_limited >/dev/null; then
            ui_poll_start; last_poll=$SECONDS
        fi
        # Enter сразу после действия — «цифра, Enter» в человеческом темпе —
        # относится к прошлому экрану, а не к новому: на подтверждении он
        # запустил бы установщик, которого человек ещё не видел.
        if [ "$UI_KEY" = enter ] && [ "${UI_INPUT:-0}" = 0 ]; then
            _ui_now_ms; [ "$UI_NOW" -lt "${UI_SWALLOW_T:-0}" ] && UI_KEY=swallow
        fi
        # Журнал для набора: клавиша, экран, на котором её прочли, и продукт.
        [ -n "${GOINST_UI_TRACE:-}" ] && [ "$UI_KEY" != tick ] && printf '%s %s %s\n' "$UI_KEY" "$UI_SCR" "${UI_WP:-}" >> "$GOINST_UI_TRACE"
        if [ "$UI_KEY" = paste ]; then
            if [ "$UI_C" -lt 60 ]; then ut "Пачка клавиш пропущена — по одной" "Key burst skipped — one at a time"
            else ut "Пришло несколько клавиш разом — пропущено. Нажимайте по одной." "Several keys arrived at once — skipped. Press one at a time."; fi
            UI_NOTE="$UI_TX"; UI_NOTE_T=$(( SECONDS + 4 )); dirty=1
        fi
        case "$UI_KEY" in tick|paste|other|swallow) continue ;; esac
        dirty=1
        # Ввод ключа: всё — символы ключа, кроме Enter, Backspace и Esc.
        if [ "$UI_SCR" = keyinput ]; then
            case "$UI_KEY" in
                esc|eof) UI_INPUT=0; UI_KEYBUF=""; ui_back ;;
                back) UI_KEYBUF="${UI_KEYBUF%?}" ;;
                enter)
                    [ -n "$UI_KEYBUF" ] || continue
                    UI_KEYST=checking; ui_draw
                    TOKEN="$UI_KEYBUF"; TOKEN_FROM="asked"; token_check
                    case "$TOKEN_STATUS" in
                        ok|denied|nonet|limited) token_save "$UI_KEYBUF" >/dev/null 2>&1; UI_KEYST=ok; ui_draw; sleep 1
                                                 UI_INPUT=0; UI_KEYBUF=""; UI_KEYAT=$(date +%H:%M); ui_model_load
                                                 if ui_poll_start; then ui_root splash; else ui_root menu; fi ;;
                        *) UI_KEYST=bad; TOKEN="" ;;
                    esac ;;
                *) UI_KEYBUF+="$UI_KEY"; UI_KEYST="" ;;
            esac
            continue
        fi
        [ "$UI_SCR_DRAWN" = small ] && { case "$UI_KEY" in q|0|esc|eof) UI_SCR=quit ;; esac; continue; }
        if [ "$UI_SCR" = splash ]; then ui_root menu; continue; fi
        scr0="$UI_SCR|${#UI_STACK[@]}"
        ui_items_count
        case "$UI_KEY" in
            up)    [ "$UI_NI" -gt 0 ] && UI_CUR=$(( (UI_CUR - 1 + UI_NI) % UI_NI )); [ "$UI_SCR" = ports ] || true ;;
            down)  [ "$UI_NI" -gt 0 ] && UI_CUR=$(( (UI_CUR + 1) % UI_NI )) ;;
            enter) [ "$UI_NI" -gt 0 ] && ui_item_act "$UI_CUR" ;;
            eof)   UI_SCR=quit ;;
            esc)   if [ "$UI_SCR" = menu ]; then UI_SCR=quit; else ui_back; fi ;;
            q)     UI_SCR=quit ;;
            *)
                if ui_item_by_key "$UI_KEY"; then UI_CUR="$UI_I"; ui_item_act "$UI_I"
                else case "$UI_KEY" in
                    0) ui_back ;;
                    p|f3) ui_ports_load; ui_go ports 0 ;;
                    f2) ui_size_next; ui_size
                        case "$(ui_size_mode)" in
                            full)    ut "Размер: на всё окно (F2 — дальше)" "Size: whole window (F2 — next)" ;;
                            center)  ut "Размер: 80×24 по центру (F2 — дальше)" "Size: 80×24 centred (F2 — next)" ;;
                            compact) ut "Размер: 80×24 в углу (F2 — дальше)" "Size: 80×24 in the corner (F2 — next)" ;;
                        esac
                        UI_NOTE="$UI_TX"; UI_NOTE_T=$(( SECONDS + 4 )) ;;
                    r) [ "$UI_SCR" = key ] || ui_go key 0 ;;
                    l|f4) [ "$UI_SCR" = lang ] || ui_go lang "$([ "${LANG_CODE:-ru}" = en ] && echo 1 || echo 0)" ;;
                    u) [ -n "${UD_VERNEW:-}" ] && ui_go selfupd 0 ;;
                esac; fi ;;
        esac
        # После действия цифрой или буквой — и после любой смены экрана, хоть
        # Enter, хоть Esc, — Enter ещё 0,4 с ничего не жмёт: несколько Enter,
        # нажатых, пока экран думал, иначе прошли бы мастер насквозь до
        # «Да, запустить установщик».
        case "$UI_KEY" in up|down|enter|esc|back|eof) ;; *) _ui_now_ms; UI_SWALLOW_T=$(( UI_NOW + 400 )) ;; esac
        if [ "$UI_SCR|${#UI_STACK[@]}" != "$scr0" ]; then _ui_now_ms; UI_SWALLOW_T=$(( UI_NOW + 400 )); fi
        # На карте портов курсор выбирает владельца — правое окно следует за ним.
        [ "$UI_SCR" = ports ] && [ "$UI_CUR" -lt "${#UI_PG_NAME[@]}" ] && UI_PG_CUR="$UI_CUR"
    done
    ui_leave_screen
    [ -n "${UI_POLLPID:-}" ] && kill "$UI_POLLPID" 2>/dev/null
    [ -n "${UI_POLLDIR:-}" ] && rm -rf "$UI_POLLDIR"
    trap - WINCH INT TERM EXIT
    return 0
}
ui_crash_exit() {
    [ "${UI_ALT:-0}" = 1 ] || return 0
    ui_leave_screen
    [ -n "${UI_POLLPID:-}" ] && kill "$UI_POLLPID" 2>/dev/null
    [ -n "${UI_POLLDIR:-}" ] && rm -rf "$UI_POLLDIR"
    printf '\n  %s\n' "$(bi "Меню goInstaller упало. Прежнее меню: GOINST_PLAIN_MENU=1 goinstall" "The goInstaller menu crashed. The previous menu: GOINST_PLAIN_MENU=1 goinstall")" >&2
    return 0
}

# ── сводка при входе и фоновый опрос ───────────────────────────────────
# Решение заказчика: фон спрашивает GitHub раз в 30 минут, открытое меню —
# раз в 3. Вход по SSH никогда не ждёт сеть: скрипт входа печатает сводку
# из файла, который пишет таймер. Всё это ставится только с согласия — это
# первый случай, когда goInstaller оставляет что-то работать на сервере.
SUM_FILE="${GOINST_SUM_FILE:-${GOINST_DIR}/summary}"
SUM_CHOICE="${GOINST_SUM_CHOICE:-${GOINST_DIR}/.summary}"
SUM_UNIT_DIR="${GOINST_SYSTEMD_DIR:-/etc/systemd/system}"
SUM_HOOK="${GOINST_PROFILE_HOOK:-/etc/profile.d/goinstall-summary.sh}"

# --watch: взять сводку версий и записать сведения для входа. Сводку клуба
# собирает GitHub Actions — это один условный запрос. Её нет (Actions ещё
# не собрал её или сломался) — спросить самому, но скромно: только стоящие
# продукты и себя, без проверки ключа. Ничего не вышло — время сведений
# остаётся прежним: «сведения от» не должно врать о свежести.
summary_watch() {
    local tmp n p id ok=0 v prev rc=0
    local -a ids=()
    summary_enabled && summary_hook_write
    [ -n "${TOKEN:-}" ] || return 0
    VER_TTL=0
    digest_fetch || rc=$?
    if [ "$rc" -eq 0 ]; then ok=1
    elif [ "$rc" -ne 2 ] && ! gh_limited >/dev/null; then
        for id in "${PRODUCTS[@]}"; do prod_installed "$id" && ids+=("$id"); done
        [ "${#ids[@]}" -gt 0 ] && versions_fetch "${ids[@]}" >/dev/null 2>&1
        self_fetch_versions >/dev/null 2>&1
        if v=$(VER_TTL=600 ver_cache_read "$SELF_ID" main) && [ -n "$v" ]; then ok=1; fi
    fi
    if [ "$ok" = 1 ]; then UI_SVED=$(date +%H:%M)
    else prev=$(sed -n 's/^sved=//p' "$SUM_FILE" 2>/dev/null) || prev=""; UI_SVED="${prev%%$'\n'*}"; UI_SVED="${UI_SVED:-—}"; fi
    ui_model_load
    mkdir -p "$(dirname "$SUM_FILE")" 2>/dev/null || return 1
    tmp="$SUM_FILE.tmp"
    {
        printf 'sved=%s\nninst=%s\nhost=%s\n' "$UI_SVED" "$UD_NINST" "$UD_HOST"
        for n in ${UD_NEWS[@]+"${UD_NEWS[@]}"}; do printf 'news=%s\n' "$n"; done
        for p in ${UD_PROB[@]+"${UD_PROB[@]}"}; do printf 'prob=%s\n' "$p"; done
    } > "$tmp" 2>/dev/null && mv -f "$tmp" "$SUM_FILE"
    return 0
}
# ui_emit_inline — те же строки, но просто вниз по экрану: сводка печатается
# перед приглашением командной строки, а не поверх окна.
ui_emit_inline() {  # ui_emit_inline [noeol] — noeol: последняя строка без перевода
    local out="" row seg tok txt sel s col k i=0 n="${#UI_ROWS[@]}"
    for row in "${UI_ROWS[@]}"; do
        i=$(( i + 1 ))
        col=0
        while [ -n "$row" ]; do
            seg="${row%%"$UI_RS"*}"; row="${row#*"$UI_RS"}"
            tok="${seg%%"$UI_US"*}"; txt="${seg#*"$UI_US"}"
            [ -n "$txt" ] || continue
            [ "$UI_ASCII" = 1 ] && { ui_ascii "$txt"; txt="$UI_P"; }
            sel=0; case "$tok" in *+sel) sel=1; tok="${tok%+sel}" ;; esac
            if [ "$tok" = grad ] && [ "$UI_GRADN" -gt 0 ]; then
                for ((k = 0; k < ${#txt}; k++)); do out+="${UI_GRAD[$(( col + k < UI_GRADN ? col + k : UI_GRADN - 1 ))]}${txt:k:1}"; done
            else
                if [ "$sel" = 1 ]; then s="${UI_SGRSEL[$tok]:-}"; else s="${UI_SGR[$tok]:-}"; fi
                out+="$s$txt"
            fi
            col=$(( col + ${#txt} ))
        done
        if [ "${1:-}" = noeol ] && [ "$i" -eq "$n" ]; then out+=$'\033[0m'; else out+=$'\033[0m\n'; fi
    done
    printf '%s' "$out"
}
# --motd: сводка из записанного файла, без сети; ширина и цвет — терминала.
summary_print() {
    local l k v
    [ -r "$SUM_FILE" ] || return 0
    UD_NEWS=(); UD_PROB=(); UD_SVED=""; UD_NINST=0; UD_HOST=""
    while IFS= read -r l; do
        k="${l%%=*}"; v="${l#*=}"
        case "$k" in
            sved) UD_SVED="$v" ;; ninst) UD_NINST="$v" ;; host) UD_HOST="$v" ;;
            news) UD_NEWS+=("$v") ;; prob) UD_PROB+=("$v") ;;
        esac
    done < "$SUM_FILE"
    ui_sysload
    ui_detect
    [ "$UI_C" = 40 ] && UI_C=46
    UI_R=0; UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_summary
    UI_ROWS=(); for l in "${UI_BODY[@]}"; do ui_fit "$l" "$UI_C"; UI_ROWS+=("$UI_FIT"); done
    ui_emit_inline
    if [ "${#UD_NEWS[@]}" -gt 0 ] || [ "${#UD_PROB[@]}" -gt 0 ]; then summary_countdown; return $?; fi
    return 0
}
# Отсчёт под сводкой (экран «Б» макета): Enter — открыть goinstall, любая
# другая клавиша — в консоль, через 5 секунд — тоже в консоль. Только когда
# в сводке есть что делать и только в терминале: вход не должен ждать там,
# где отвечать некому. rc 10 — открыть меню.
summary_countdown() {
    local total="${GOINST_COUNTDOWN_MS:-5000}" step=250 rem k n=0 w st="" _r
    case "$total" in ''|*[!0-9]*|0) total=5000 ;; esac
    if [ -z "${GOINST_COUNTDOWN_FORCE:-}" ]; then { [ -t 0 ] && [ -t 1 ]; } || return 0; fi
    # Уже что-то набрано (человек печатает команду, ssh с вводом из скрипта)
    # — отсчёта нет: он съел бы первую букву, а перевод строки открыл бы меню.
    if IFS= read -t 0 _r 2>/dev/null; then return 0; fi
    [ "$UI_C" -lt 60 ] && n=1
    # Не шире окна: перенесённая строка не стёрлась бы, и приглашение
    # оболочки встало бы под остатком отсчёта.
    w="$UI_C"; [ "${UI_COLS:-80}" -le "$w" ] && w=$(( UI_COLS - 1 ))
    UI_SEG=""; _s text "  "; _s key "Enter"
    if [ "$n" = 1 ]; then ut " — goinstall · другая клавиша — консоль" " — goinstall · other key — shell"
    else ut " — открыть goinstall · любая другая клавиша — в консоль" " — open goinstall · any other key — shell"; fi
    _s muted "$UI_TX"; ui_fit "$UI_SEG" "$w"; UI_ROWS=("$UI_FIT"); ui_emit_inline
    st=$(stty -g 2>/dev/null) || st=""
    # Прервали Ctrl-C — стереть отсчёт, вернуть терминал и отдать консоль.
    trap '[ -n "$st" ] && stty "$st" 2>/dev/null; printf "\r\033[K\033[1A\033[K"; exit 0' INT
    rem="$total"
    while [ "$rem" -gt 0 ]; do
        UI_SEG=""; _s text "  "; ui_bar $(( rem * 1000 / total )) $(( n ? 20 : 24 )) cursor; UI_SEG+="$UI_BAR"
        ut " с" " s"; _s muted "  $(( (rem + 999) / 1000 ))$UI_TX"
        printf '\r'; ui_fit "$UI_SEG" "$w"; UI_ROWS=("$UI_FIT"); ui_emit_inline noeol; printf '\033[K'
        if IFS= read -rsn1 -t 0.25 k; then
            # Стрелка или функциональная клавиша — это несколько байт: хвост
            # «[A» иначе ушёл бы в оболочку командой.
            if [ "$k" = $'\033' ]; then while IFS= read -rsn1 -t 0.02 _r; do :; done; fi
            printf '\r\033[K\033[1A\033[K'; trap - INT
            [ -n "$st" ] && stty "$st" 2>/dev/null
            case "$k" in ''|$'\r') return 10 ;; esac
            return 0
        fi
        rem=$(( rem - step ))
    done
    printf '\r\033[K\033[1A\033[K'; trap - INT
    [ -n "$st" ] && stty "$st" 2>/dev/null
    return 0
}

summary_enabled() { [ "$(cat "$SUM_CHOICE" 2>/dev/null)" = yes ]; }

# Скрипт входа. Ловушка на Ctrl-C — в оболочке входа: без неё Ctrl-C во
# время сводки обрывал и остаток профиля (PS1 и прочее не выставлялись).
summary_hook_text() {
    cat <<'HOOK'
# goInstaller: сводка при входе. Выключить — goinstall --summary off.
if [ -t 0 ] && [ -t 1 ] && [ "$(id -u)" = 0 ] && [ -z "${GOINST_SUMMARY_SHOWN:-}" ] && [ ! -e "$HOME/.hushlogin" ] && command -v goinstall >/dev/null 2>&1; then
    export GOINST_SUMMARY_SHOWN=1
    trap : INT
    goinstall --motd 2>/dev/null
    trap - INT
fi
HOOK
}
# Записать скрипт входа, если он не такой, как у этой версии: так серверы,
# включившие сводку раньше, получают правки скрипта с первым же опросом.
summary_hook_write() {
    local want
    want=$(summary_hook_text)
    [ "$(cat "$SUM_HOOK" 2>/dev/null)" = "$want" ] && return 0
    mkdir -p "$(dirname "$SUM_HOOK")" 2>/dev/null || return 1
    printf '%s\n' "$want" > "$SUM_HOOK" 2>/dev/null || return 1
    chmod 644 "$SUM_HOOK" 2>/dev/null || true
    return 0
}
# Включить: таймер раз в 30 минут со сдвигом до 10, скрипт входа — только
# для root, только в терминале и не для ~/.hushlogin.
summary_enable() {
    mkdir -p "$SUM_UNIT_DIR" "$(dirname "$SUM_HOOK")" "$(dirname "$SUM_CHOICE")" 2>/dev/null || return 1
    cat > "$SUM_UNIT_DIR/goinstall-watch.service" <<UNIT
[Unit]
Description=goInstaller: опрос обновлений для сводки при входе
After=network-online.target

[Service]
Type=oneshot
Nice=10
ExecStart=${GOINST_BIN} --watch
UNIT
    cat > "$SUM_UNIT_DIR/goinstall-watch.timer" <<UNIT
[Unit]
Description=goInstaller: опрос обновлений раз в 30 минут

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
    summary_hook_write
    chmod 644 "$SUM_HOOK" "$SUM_UNIT_DIR/goinstall-watch.service" "$SUM_UNIT_DIR/goinstall-watch.timer" 2>/dev/null
    printf 'yes\n' > "$SUM_CHOICE"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl enable --now goinstall-watch.timer >/dev/null 2>&1 || true
        systemctl start --no-block goinstall-watch.service >/dev/null 2>&1 || true
    fi
    return 0
}
summary_disable() {
    if command -v systemctl >/dev/null 2>&1; then systemctl disable --now goinstall-watch.timer >/dev/null 2>&1 || true; fi
    rm -f "$SUM_UNIT_DIR/goinstall-watch.service" "$SUM_UNIT_DIR/goinstall-watch.timer" "$SUM_HOOK" "$SUM_FILE"
    command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload >/dev/null 2>&1
    mkdir -p "$(dirname "$SUM_CHOICE")" 2>/dev/null && printf 'no\n' > "$SUM_CHOICE"
    return 0
}
# Вопрос о сводке — один раз, при первом запуске меню.
ui_scr_sumask() {
    local l
    UI_CUR="$1"
    UI_BODY=(); UI_INFO=""; UI_KEYS=""
    ui_main_title; ui_hdr "$UI_MT" ""; ui_body ""
    ut "Показывать сводку при входе на сервер?" "Show a summary when you log in to the server?"; ui_body "title${UI_US}  ${UI_TX}${UI_RS}"; ui_body ""
    ut "Вошли по SSH — и сразу видно, что вышли обновления, появились новые продукты или скоро истекает сертификат. Для этого goInstaller поставит таймер: раз в 30 минут он тихо спрашивает GitHub. Вход сети не ждёт — сводка берётся из записанного." "Log in over SSH and see right away that updates are out, new products appeared or a certificate is about to expire. goInstaller installs a timer for it: every 30 minutes it quietly asks GitHub. Logging in never waits for the network — the summary comes from saved data."
    ui_wrap "$UI_TX" $(( UI_C - 4 )); for l in "${UI_WRAP[@]}"; do ui_body "muted${UI_US}  ${l}${UI_RS}"; done
    ui_body ""
    ut "Выключить потом: goinstall --summary off" "Turn off later: goinstall --summary off"; ui_body "muted${UI_US}  ${UI_TX}${UI_RS}"; ui_body ""
    ui_items_clear; ut "Да" "Yes"; ui_item 1 "$UI_TX" sumyes; ut "Нет" "No"; ui_item 2 "$UI_TX" sumno
    ui_choicerows "$UI_CUR"
    local a; ut "да" "yes"; a="$UI_TX"; ut "нет" "no"; ui_keys_fit "1|$a|" "2|$UI_TX|"
}

# Новый интерфейс — когда есть терминал на входе и выходе и TERM не dumb.
# Иначе прежний нумерованный список: запуск из скрипта, TERM=dumb,
# GOINST_PLAIN_MENU=1.
ui_can_run() {
    [ -z "${GOINST_PLAIN_MENU:-}" ] && [ -t 0 ] && [ -t 1 ] && [ "${TERM:-dumb}" != dumb ]
}

main() {
    case "${1:-}" in
        -v|--version) echo "goInstaller ${GOINST_VERSION}"; exit 0 ;;
        -h|--help) usage; exit 0 ;;
        # Сводка при входе: из записанного файла, без сети и без проверок —
        # вход по SSH не должен ждать ничего.
        --motd)
            summary_print
            if [ "$?" -eq 10 ] && [ -x "$GOINST_BIN" ]; then
                # Enter на отсчёте — меню. Скрипт входа глушит stderr
                # (2>/dev/null); возвращаем его терминалу, иначе ошибки
                # установщиков пропадут.
                if { : >/dev/tty; } 2>/dev/null; then exec 2>/dev/tty; fi
                ui_flush_input
                exec "$GOINST_BIN"
            fi
            exit 0 ;;
        # Сводка версий для клуба — её собирает GitHub Actions, не root и не
        # на сервере: ключ из GH_TOKEN, вывод в stdout.
    esac

    # Язык — первым экраном, до любых проверок: всё дальнейшее, включая
    # отказы, человек должен прочитать на своём языке. Спрашиваем только там,
    # где с ним говорят, — в меню и при установке. Справочные команды берут
    # запомненный язык, чтобы не ломать запуск из скриптов.
    case "${1:-}" in
        ''|--install)
            reattach_tty
            lang_startup || exit 1 ;;
    esac

    if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
        die "$(bi "Нужен bash 4.0+ (найден ${BASH_VERSION:-?})" "bash 4.0+ is required (found ${BASH_VERSION:-?})")"
    fi
    os_detect
    os_gate_common
    # GOINST_TEST=1 — тесты: без root, без ss (фикстура), без установки curl.
    if [ -z "${GOINST_TEST:-}" ]; then
    [ "$(id -u)" -eq 0 ] || die "$(bi "Запустите от root: sudo bash …" "Run as root: sudo bash …")"
    command -v ss >/dev/null 2>&1 || die "$(bi "Нет ss (пакет iproute2) — без него порты не проверить." "ss is missing (package iproute2) — ports cannot be checked without it.")"
    command -v curl >/dev/null 2>&1 || {
        say "$(bi "Ставлю curl…" "Installing curl…")"
        DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=600 update >/dev/null 2>&1 || true
        DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=600 install --no-install-recommends curl ca-certificates >/dev/null 2>&1 \
            || die "$(bi "Не удалось поставить curl." "Could not install curl.")"
    }
    fi

    lang_persist

    # Ключ читаем ДО разбора команды. Раньше его подхватывал только путь
    # меню, и `--versions` на машине с сохранённым ключом честно отвечал
    # «нет ключа»: ключ лежал рядом, но никто его не открывал.

    case "${1:-}" in
        --list) list_products; exit 0 ;;
        --ports) show_ports; exit 0 ;;
        --versions) show_versions; exit 0 ;;
        --self-update) self_update "${2:-}"; exit $? ;;
        --watch) os_detect; summary_watch; exit 0 ;;
        --net) reattach_tty; net_mtu_offer force; exit $? ;;
        --uninstall) shift; reattach_tty; goinst_uninstall "$@"; exit $? ;;
        --summary)
            case "${2:-}" in
                on)  summary_enable && ok "$(bi "Сводка при входе включена: опрос раз в 30 минут." "Login summary is on: polling every 30 minutes.")"; exit 0 ;;
                off) summary_disable; ok "$(bi "Сводка при входе выключена, таймер снят." "Login summary is off, the timer is removed.")"; exit 0 ;;
                *) usage; exit 64 ;;
            esac ;;
        --key)
            echo "$(bi "���� �� �����: ����������� ���������." "No key needed: repository is public.")"; exit 0 ;;
        --check)
            valid_id "${2:-}" || { usage; exit 64; }
            mode_arg "$2" "${3:-}" || exit 64
            if [ -n "${3:-}" ]; then MODE="$3"; else mode_resolve "$2" || MODE=pro; fi
            preflight "$2" "$MODE"; exit $? ;;
        --install)
            valid_id "${2:-}" || { usage; exit 64; }
            # У продукта без режимов третье слово — ветка:
            # `goinstall --install gokaskad dev`. Иначе dev стал бы «режимом».
            # Имя ветки (main, dev…) — ветка, прочее остаётся прежним «режимом»
            # без смысла: `--install gokaskad pro` работал и раньше. Сверка
            # ветки — после опроса GitHub: в кеше сервера мог остаться список
            # без dev, а запасной `main dev` есть не у всех.
            local im="${3:-}" ir="${4:-}"
            if ! prod_has_modes "$2" && [ -n "$im" ] && [ -z "$ir" ]; then
                if [[ "$im" =~ ^(main|master|dev|develop)$ ]] || ref_arg_known "$2" "$im"; then ir="$im"; im=""; fi
            fi
            mode_arg "$2" "$im" || exit 64
            reattach_tty; net_mtu_offer; self_install
            versions_fetch "$2"
            ref_arg "$2" "$ir" || exit 64
            REF="$ir"
            free_takeover_note "$2"
            if [ -n "$im" ]; then MODE="$im"
            elif ! mode_resolve "$2"; then ask_mode "$2" || exit 1; fi
            [ -n "$REF" ] || ask_ref "$2" || exit 1
            preflight "$2" "$MODE"; local rc=$?
            if [ "$rc" -eq 1 ] && [ -z "${GOINST_FORCE:-}" ]; then exit 1; fi
            run_product_installer "$2"; rc=$?
            installer_postcheck "$2" "${REF:-$(prod_ref "$2")}" || true
            exit "$rc" ;;
        '') ;;
        *) usage; exit 64 ;;
    esac

    reattach_tty
    # До первого обращения к GitHub: узкий путь рвёт TLS через раз.
    net_mtu_offer
    self_install
    # «Аркада»: ключ проверяется и версии спрашиваются в фоне, под заставкой;
    # меню открывается сразу, без ожидания сети.
    if ui_can_run; then
        ui_main
        if [ -n "${UI_UNINSTALL:-}" ]; then
            goinst_remove "$UI_UNINSTALL" && goinst_remove_done "$UI_UNINSTALL"
        fi
        return 0
    fi
    # Ключ проверяется до всего остального: без него нечего ни опрашивать,
    # ни скачивать, и узнать об этом лучше сразу, а не в середине установки.
    token_startup
    self_fetch_versions
    # Опрос версий — до первого экрана: пункт «обновить» без номера версии
    # в ветке предлагает обновляться вслепую. Без ключа и без сети опрос
    # молча пропускается, меню от этого не задерживается.
    versions_fetch
    main_menu
}

# GOINST_SOURCE_ONLY=1 — тесты подключают файл ради функций, не запуская меню.
if [ -z "${GOINST_SOURCE_ONLY:-}" ]; then
    main "$@"
fi
