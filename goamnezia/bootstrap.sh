#!/bin/bash
# goAmnezia pro — загрузчик из приватного репозитория (порт из goTelegram Pro).
# Скачивает файлы продукта в /opt/goamnezia и запускает install.sh.
#
# Однострочник установки — стабильная ветка main:
#   export GOAMNEZIA_PAT=…; bash <(printf 'Authorization: token %s\n' "$GOAMNEZIA_PAT" | curl -fsSL -H @- \
#     https://raw.githubusercontent.com/potap1978/go-all/main/bootstrap.sh)
# Экспериментальная ветка dev — тот же однострочник, а перед ним `export GOAMNEZIA_BRANCH=dev`. Ветку выбирает
# переменная, а не адрес в однострочнике: загрузчик сам передаёт управление своей копии из коммита выбранной
# ветки (ниже, «Сам загрузчик — тоже по SHA»).

set -euo pipefail

REPO="potap1978/go-all"
# Продукт живёт в подкаталоге монорепозитория go-all: файлы качаются из
# https://raw.githubusercontent.com/<repo>/<ref>/<SUBDIR>/...
SUBDIR="${GOAMNEZIA_REPO_DIR:-goamnezia}"
# Веток две, как у всей серии (goinstaller-ready §2): main — стабильная и ставится по умолчанию, dev —
# экспериментальная. С 13.09.2026 до выхода в main ветка была одна, dev, и умолчанием стояла она. Рабочую
# ветку автора (стенды) тоже можно назвать явно — людям предлагаются только эти две.
BRANCH="${GOAMNEZIA_BRANCH:-main}"
PAT="${GOAMNEZIA_PAT:-}"
# Заголовок идёт через stdin: во время долгой загрузки приватного репозитория
# токен не должен появляться в argv curl, доступном другим пользователям VPS.
github_curl() {
    if [ -n "$PAT" ]; then
        printf 'Authorization: token %s\n' "$PAT" | curl -H @- "$@"
    else
        curl "$@"
    fi
}
# Каталог установки. GOAMNEZIA_DIR — та же переменная, которой стенды и тесты
# уводят продукт из /opt (CONTRACT §1); на боевой машине она не задана.
INSTALL_DIR="${GOAMNEZIA_DIR:-/opt/goamnezia}"
# GOAMNEZIA_DRY_RUN=1 — прогон проверок: ни root, ни симлинков в /usr/local/bin,
# ни запуска install.sh. Проверяется ровно то, ради чего тест и написан, —
# что из архива на диск легли ИМЕННО перечисленные файлы.
DRY="${GOAMNEZIA_DRY_RUN:-0}"
# GOAMNEZIA_LOCAL_TREE=<каталог> — файлы не с GitHub, а из этого дерева репозитория (стенды: живая проверка
# update-rollback, tests/test_update_dryrun.sh). Путь тот же, что у настоящего обновления: снимок прежнего кода,
# проверка запуска новой версии, раскладка, .install-source; не нужны ни ключ, ни сеть.
LOCAL_TREE="${GOAMNEZIA_LOCAL_TREE:-}"
# Проверка «новая версия запускается» (bash -n, компиляция python-частей, install.sh --version) идёт всегда,
# кроме сухого прогона; GOAMNEZIA_BOOTSTRAP_CHECK=1 включает её и там.
CHECK_NEW=1
[ "$DRY" = "1" ] && [ "${GOAMNEZIA_BOOTSTRAP_CHECK:-0}" != "1" ] && CHECK_NEW=0

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
DIM='\033[2m'
NC='\033[0m'
BOLD='\033[1m'

# ── Двуязычные строки загрузчика ─────────────────────────────────────────────
# Единственное место продукта, где текст лежит не в lib/lang: словаря на диске
# ещё нет, его как раз предстоит скачать. Поэтому — маленькая таблица с теми же
# ключами и выбором языка через GOAMNEZIA_LANG (по умолчанию ru). Всё, что
# печатается ПОСЛЕ загрузки, идёт уже через обычные t/tf из lib/i18n.sh.
BS_LANG="${GOAMNEZIA_LANG:-ru}"
[[ "$BS_LANG" =~ ^(ru|en)$ ]] || BS_LANG="ru"
bs() {
    local key="${1:-}"
    if [ "$BS_LANG" = "en" ]; then
        case "$key" in
            title)        echo "goAmnezia pro — installation" ;;
            subtitle1)    echo "AmneziaWG 1.0 / 1.5 / 2.0 / 3.1" ;;
            subtitle2)    echo "Amnezia app compatible, Telegram bot, web panel" ;;
            need_root)    echo "Run as root: sudo bash bootstrap.sh" ;;
            need_pat)     echo "GitHub token is not set." ;;
            need_pat_how) echo "Run it like this: GOAMNEZIA_PAT=YOUR_PAT sudo -E bash bootstrap.sh" ;;
            installing)   echo "Installing" ;;
            downloading)  echo "Downloading files into" ;;
            dl_failed)    echo "Failed to download" ;;
            failed_n)     echo "Could not download files:" ;;
            check_token)  echo "Check the access token and the network connection" ;;
            skipped_opt)  echo "not in this branch yet, skipped" ;;
            arch_try)     echo "Downloading the branch snapshot as a single archive…" ;;
            arch_ok)      echo "Snapshot unpacked, laying the files out" ;;
            arch_fallback) echo "Snapshot unavailable — falling back to file-by-file download" ;;
            way_archive)  echo "way: one archive" ;;
            way_files)    echo "way: file by file" ;;
            files_1)      echo "file" ;;
            files_2)      echo "files" ;;
            files_5)      echo "files" ;;
            elapsed)      echo "took" ;;
            seconds)      echo "s" ;;
            dry_stop)     echo "dry run: files laid out, install.sh not started" ;;
            dry_no_apt)   echo "dry run, the package is not installed" ;;
            bad_branch)   echo "Invalid branch name" ;;
            no_branch)    echo "The repository has no branch" ;;
            no_branch_how) echo "The stable branch is main, the experimental one is dev: export GOAMNEZIA_BRANCH=dev" ;;
            branch)       echo "Branch" ;;
            kind_main)    echo "stable" ;;
            kind_dev)     echo "experimental" ;;
            kind_other)   echo "working branch" ;;
            commit)       echo "commit" ;;
            version)      echo "version" ;;
            no_sha)       echo "GitHub API did not name the branch commit — downloading by branch name: right after a release the files may lag a few minutes" ;;
            loader_self)  echo "Could not fetch the loader of this commit — continuing with this copy; the files still come from the commit" ;;
            on_disk)      echo "On disk" ;;
            branch_w)     echo "branch" ;;
            verify_bad)   echo "The version on disk is not the one expected:" ;;
            verify_how)   echo "The update did not land completely. Repeat «Update goAmnezia» or the install command." ;;
            local_tree)   echo "files from the directory" ;;
            no_tree)      echo "No product tree (install.sh, lib/common.sh) in the directory" ;;
            no_work)      echo "Could not create a temporary directory for the download" ;;
            new_broken)   echo "The new version does not start:" ;;
            deps_failed)  echo "Required dependencies could not be installed or updated; installation stopped." ;;
            not_started)  echo "The update was not started — the previous version keeps working, nothing changed." ;;
            not_installed) echo "Installation stopped. Report the branch and the commit above." ;;
            prev_saved)   echo "The previous version is kept next to the product for a rollback:" ;;
            prev_kept)    echo "Kept the copy of the version before the unfinished update:" ;;
            prev_failed)  echo "Could not keep the previous version for a rollback (disk space?)." ;;
            lay_failed)   echo "The new files did not fit (disk space?)." ;;
            lay_broken)   echo "The files broke off halfway — the previous version is back:" ;;
            lay_lost)     echo "The files broke off halfway and the previous version could not be put back. Repeat the install command." ;;
            *)            echo "$key" ;;
        esac
    else
        case "$key" in
            title)        echo "goAmnezia pro — установка" ;;
            subtitle1)    echo "AmneziaWG 1.0 / 1.5 / 2.0 / 3.1" ;;
            subtitle2)    echo "Совместимо с приложением Amnezia, бот, веб-панель" ;;
            need_root)    echo "Запустите от root: sudo bash bootstrap.sh" ;;
            need_pat)     echo "Не задан GitHub-токен." ;;
            need_pat_how) echo "Запустите так: GOAMNEZIA_PAT=ВАШ_ТОКЕН sudo -E bash bootstrap.sh" ;;
            installing)   echo "Установка" ;;
            downloading)  echo "Загрузка файлов в" ;;
            dl_failed)    echo "Ошибка загрузки" ;;
            failed_n)     echo "Не удалось загрузить файлов:" ;;
            check_token)  echo "Проверьте токен доступа и подключение к сети" ;;
            skipped_opt)  echo "в этой ветке ещё нет, пропущено" ;;
            arch_try)     echo "Скачиваю снимок ветки одним архивом…" ;;
            arch_ok)      echo "Архив распакован, раскладываю файлы" ;;
            arch_fallback) echo "Архив не получился — качаю файлы по одному" ;;
            way_archive)  echo "путь: одним архивом" ;;
            way_files)    echo "путь: по одному файлу" ;;
            files_1)      echo "файл" ;;
            files_2)      echo "файла" ;;
            files_5)      echo "файлов" ;;
            elapsed)      echo "заняло" ;;
            seconds)      echo "с" ;;
            dry_stop)     echo "сухой прогон: файлы разложены, install.sh не запускаем" ;;
            dry_no_apt)   echo "сухой прогон, пакет не ставим" ;;
            bad_branch)   echo "Недопустимое имя ветки" ;;
            no_branch)    echo "В репозитории нет ветки" ;;
            no_branch_how) echo "Стабильная ветка — main, экспериментальная — dev: export GOAMNEZIA_BRANCH=dev" ;;
            branch)       echo "Ветка" ;;
            kind_main)    echo "стабильная" ;;
            kind_dev)     echo "экспериментальная" ;;
            kind_other)   echo "рабочая ветка" ;;
            commit)       echo "коммит" ;;
            version)      echo "версия" ;;
            no_sha)       echo "GitHub API не назвал коммит ветки — качаю по имени ветки: сразу после выпуска файлы могут отставать на несколько минут" ;;
            loader_self)  echo "Загрузчик этого коммита не скачался — продолжаю этой копией, файлы всё равно из коммита" ;;
            on_disk)      echo "На диске" ;;
            branch_w)     echo "ветка" ;;
            verify_bad)   echo "На диске не та версия, что ждали:" ;;
            verify_how)   echo "Обновление легло не целиком. Повторите «Обновить goAmnezia» или команду установки." ;;
            local_tree)   echo "файлы из каталога" ;;
            no_tree)      echo "В каталоге нет дерева продукта (install.sh, lib/common.sh)" ;;
            no_work)      echo "Не удалось создать временный каталог для загрузки" ;;
            new_broken)   echo "Новая версия не запускается:" ;;
            deps_failed)  echo "Нужные зависимости установить или обновить не удалось; установка остановлена." ;;
            not_started)  echo "Обновление не начато — работает прежняя версия, ничего не изменилось." ;;
            not_installed) echo "Установка остановлена. Сообщите ветку и коммит выше." ;;
            prev_saved)   echo "Прежняя версия сохранена рядом для отката:" ;;
            prev_kept)    echo "Сохранена копия версии до незаконченного обновления:" ;;
            prev_failed)  echo "Не удалось сохранить прежнюю версию для отката (место на диске?)." ;;
            lay_failed)   echo "Файлы новой версии не легли (место на диске?)." ;;
            lay_broken)   echo "Раскладка оборвалась на полпути — возвращена прежняя версия:" ;;
            lay_lost)     echo "Раскладка оборвалась на полпути, и прежнюю версию вернуть не вышло. Повторите команду установки." ;;
            *)            echo "$key" ;;
        esac
    fi
}

# bs_files <n> → «94 файла», «21 файл», «11 файлов» (EN — «1 file», «94 files»): «94 файлов» резало глаз с первой же
# строки установки (находка NC-28 критика-новичка).
bs_files() {
    local n="${1:-0}" two last form=files_5
    [[ "$n" =~ ^[0-9]+$ ]] || { printf '%s %s' "$n" "$(bs files_5)"; return 0; }
    two=$(( 10#$n % 100 )); last=$(( 10#$n % 10 ))
    if [ "$BS_LANG" = en ]; then
        [ "$((10#$n))" = 1 ] && form=files_1
    elif [ "$two" -lt 11 ] || [ "$two" -gt 14 ]; then
        if [ "$last" = 1 ]; then form=files_1
        elif [ "$last" -ge 2 ] && [ "$last" -le 4 ]; then form=files_2
        fi
    fi
    printf '%s %s' "$n" "$(bs "$form")"
}

# ── Ветка и коммит ───────────────────────────────────────────────────────────
# Имя ветки уходит в адреса GitHub и построчно в .install-source: перевод строки дописал бы туда свою строку
# `repo=…`, и следующее «Обновить goAmnezia» пошло бы в чужой репозиторий с ключом владельца. Поэтому — только
# буквы, цифры и . _ / - без «..», как у имён веток git. В сообщение имя попадает без управляющих знаков.
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ ]] || [[ "$BRANCH" == *..* ]]; then
    echo -e "  ${RED}✗${NC} $(bs bad_branch): ${BRANCH//[^A-Za-z0-9._\/-]/?}"
    exit 1
fi

# Качаем через raw.githubusercontent (CDN) — быстро. Но у raw свой кэш на пять
# минут (cache-control: max-age=300) на URL ВЕТКИ: обновление, запущенное сразу
# после пуша, молча получало предыдущую версию файлов — на диске оставался
# старый код, а человек видел «Установка завершена» и прежний номер версии.
#
# Замерено вживую в goTelegram: спустя минуту после пуша URL ветки отдавал старое
# содержимое, и ни `Cache-Control: no-cache`, ни `?nocache=<штамп>` этого НЕ
# пробивали. Что работает — спросить у API SHA головы ветки и качать по SHA:
# такие URL неизменяемы, поэтому кэш на них всегда корректен. Бонусом получаем
# атомарность: все файлы приезжают из ОДНОГО коммита, и пуш в середине
# обновления не смешает разные версии.
#
# Один запрос к API на весь запуск. Не вышло (лимит/сеть) — откатываемся на
# ветку: лучше обновиться с риском кэша, чем не обновиться вовсе. Ответ API —
# голый SHA (тип application/vnd.github.sha), а не JSON всего коммита: у большого
# коммита тот весит мегабайты. Разбираем без конвейера: `curl | grep -m1` под
# pipefail ловил SIGPIPE, присваивание становилось неуспешным — и скрипт молча
# уходил на запасной путь (а то и падал).
#
# _head_sha <ветка> — stdout: SHA головы ветки. rc 0 — узнали; 2 — такой ветки в
# репозитории нет (API отвечает 422 «No commit found»); 1 — API не ответил
# (сеть, лимит, 5xx) или ключ не тот — это решит загрузка ниже.
_head_sha() {
    local out code body re='"sha"[[:space:]]*:[[:space:]]*"([0-9a-f]{40})"'
    out=$(github_curl -sSL --max-time 30 -H 'Accept: application/vnd.github.sha' -w '\n%{http_code}' \
          "https://api.github.com/repos/${REPO}/commits/$1" 2>/dev/null || true)
    code="${out##*$'\n'}"
    body="${out%$'\n'*}"
    # Строки с кодом нет вовсе (curl оборвался до -w) — кода не знаем, разбираем что пришло.
    if [ "$body" = "$out" ]; then code=""; fi
    case "$code" in
        422)    return 2 ;;
        ''|200) ;;
        *)      return 1 ;;
    esac
    if [[ "$body" =~ ^[[:space:]]*([0-9a-f]{40})[[:space:]]*$ ]] || [[ "$body" =~ $re ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

# ── Сам загрузчик — тоже по SHA ──────────────────────────────────────────────
# Однострочник берёт ЭТОТ файл по имени ветки — с тем же пятиминутным кэшем raw. Файлы продукта загрузчик и так
# качает по SHA, но список FILES, проверки и шаги у него свои: сразу после пуша сюда приезжал прежний загрузчик,
# и модуль, добавленный в FILES тем же пушем, на сервер не ехал — установка «проходила» без него. Поэтому,
# узнав SHA, загрузчик скачивает свою копию ИЗ ЭТОГО КОММИТА и передаёт управление ей (exec). Копия получает
# GOAMNEZIA_REF=<SHA> и второй раз этого не делает: не качает себя и не спрашивает API, а файлы берёт из того же
# коммита. Тот же GOAMNEZIA_REF передаёт «Обновить goAmnezia» — он уже скачал загрузчик по SHA сам. Копия не
# скачалась или это не скрипт (страница ошибки вместо файла) — работаем этой копией, файлы всё равно по SHA.
#
# _loader_by_sha <sha> — stdout: путь к скачанной копии загрузчика (первая строка — «#!»).
_loader_by_sha() {
    local tmp first=""
    tmp=$(mktemp "${TMPDIR:-/tmp}/goamnezia-bootstrap.XXXXXX" 2>/dev/null) || return 1
    if ! github_curl -fsSL --max-time 60 -o "$tmp" \
         "https://raw.githubusercontent.com/${REPO}/$1/${SUBDIR}/bootstrap.sh" 2>/dev/null; then
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi
    # Первая строка — без конвейера `head | grep -q` (goinstaller-ready §9: SIGPIPE под pipefail).
    { IFS= read -r first < "$tmp"; } 2>/dev/null || true
    case "$first" in
        '#!'*) printf '%s' "$tmp"; return 0 ;;
    esac
    rm -f "$tmp" 2>/dev/null || true
    return 1
}

RESOLVED_REF="$BRANCH"
# Как узнан коммит: given — пришёл в GOAMNEZIA_REF (этот загрузчик уже из него), sha — от API, branch — не узнан
# (качаем по имени ветки), missing — такой ветки в репозитории нет.
REF_STATE="branch"
LOADER_SELF=0
_ref_given="${GOAMNEZIA_REF:-}"
if [[ "$_ref_given" =~ ^[0-9a-f]{40}$ ]]; then
    RESOLVED_REF="$_ref_given"
    REF_STATE="given"
elif [ -n "$LOCAL_TREE" ]; then
    # Из каталога коммит неизвестен: в .install-source остаётся прежний (по нему «Обновить goAmnezia» и зеркало
    # сборок ходят в GitHub), а прежнего нет — имя ветки.
    REF_STATE="local"
    _prev_ref=$(grep -m1 '^ref=' "${INSTALL_DIR}/.install-source" 2>/dev/null) || _prev_ref=""
    _prev_ref="${_prev_ref#ref=}"
    if [[ "$_prev_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$ ]] && [[ "$_prev_ref" != *..* ]]; then
        RESOLVED_REF="$_prev_ref"
    fi
    unset _prev_ref
elif [ -n "$PAT" ]; then
    _rc=0
    _sha=$(_head_sha "$BRANCH") || _rc=$?
    if [ "$_rc" = 0 ] && [ -n "$_sha" ]; then
        RESOLVED_REF="$_sha"
        REF_STATE="sha"
        # Копию запускаем, только когда дальше пойдём: без root (и не в сухом прогоне) загрузчик сейчас же
        # остановится, и качать себя незачем.
        if [ "$DRY" = "1" ] || [ "$(id -u)" -eq 0 ]; then
            _loader=""
            if _loader=$(_loader_by_sha "$_sha"); then
                # /dev/fd/3 — путь, по которому новая оболочка откроет копию; нет его (урезанный контейнер без
                # /proc) — exec оборвал бы установку, поэтому остаёмся этой копией.
                if { exec 3<"$_loader"; } 2>/dev/null && [ -r /dev/fd/3 ]; then
                    # Файл открыт — удаляем сразу: новая оболочка читает загрузчик из дескриптора, а времянка
                    # не остаётся в TMPDIR навсегда (exec ловушек EXIT не переживает).
                    rm -f "$_loader" 2>/dev/null || true
                    # `curl … | bash`: эта копия читается из stdin, и её остаток лежит там же. Без вычерпывания
                    # копия из коммита, а за ней install.sh на запуске без терминала получили бы строки этого
                    # файла ответами на свои вопросы. Вычерпываем здесь, внутри того же if: bash уже прочёл весь
                    # блок вместе с exec ниже. Скрипт из файла или из <(…) — stdin чужой, его не трогаем.
                    if [ -z "${BASH_SOURCE[0]:-}" ] && [ ! -t 0 ]; then cat >/dev/null 2>&1 || true; fi
                    export GOAMNEZIA_REF="$_sha" GOAMNEZIA_BRANCH="$BRANCH"
                    exec "${BASH:-bash}" /dev/fd/3 "$@"
                fi
                { exec 3<&-; } 2>/dev/null || true
                rm -f "$_loader" 2>/dev/null || true
            fi
            LOADER_SELF=1
        fi
    elif [ "$_rc" = 2 ]; then
        REF_STATE="missing"
    fi
fi
unset _ref_given _rc _sha _loader
RAW="https://raw.githubusercontent.com/${REPO}/${RESOLVED_REF}/${SUBDIR}"

# ── Рамка приветствия ────────────────────────────────────────────────────────
# Раньше рамка была нарисована «на глаз»: верх и низ — 54 знака ═, слева ║, а
# справа НИЧЕГО, поэтому кириллические строки разной длины торчали за угол —
# первое, что человек видит при установке, и первое, на что он пожаловался.
# Ширина считается по колонкам терминала: bash в C-локали видит байты, поэтому
# UTF-8 разбираем сами (кириллица — 2 байта на букву, а колонка одна).
bs_width() {
    local LC_ALL=C LANG=C
    local s="${1:-}" i=0 n b len w=0
    n=${#s}
    while [ "$i" -lt "$n" ]; do
        printf -v b '%d' "'${s:$i:1}"
        if   [ "$b" -lt 128 ]; then len=1
        elif [ "$b" -lt 224 ]; then len=2
        elif [ "$b" -lt 240 ]; then len=3
        else                        len=4
        fi
        i=$((i + len)); w=$((w + 1))
    done
    printf '%s' "$w"
}
bs_rule() {
    local left="$1" right="$2" inner="$3" i out=""
    for ((i = 0; i < inner + 2; i++)); do out+="═"; done
    echo -e "  ${YELLOW}${left}${out}${right}${NC}"
}
bs_row() {
    local text="$1" inner="$2" color="${3:-}" w pad
    w=$(bs_width "$text"); pad=$(( inner - w )); [ "$pad" -lt 0 ] && pad=0
    printf '  %b║%b %b%s%b%*s %b║%b\n' "$YELLOW" "$NC" "$color" "$text" "$NC" "$pad" '' "$YELLOW" "$NC"
}

BS_ROWS=("$(bs title)" "$(bs subtitle1)" "$(bs subtitle2)")
BS_INNER=0
for _row in "${BS_ROWS[@]}"; do
    _w=$(bs_width "$_row")
    [ "$_w" -gt "$BS_INNER" ] && BS_INNER="$_w"
done
echo ""
bs_rule "╔" "╗" "$BS_INNER"
bs_row "${BS_ROWS[0]}" "$BS_INNER" "$BOLD"
bs_row "${BS_ROWS[1]}" "$BS_INNER"
bs_row "${BS_ROWS[2]}" "$BS_INNER"
bs_rule "╚" "╝" "$BS_INNER"
echo ""

if [ "$DRY" != "1" ] && [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${RED}✗${NC} $(bs need_root)"
    exit 1
fi

if [ -n "$LOCAL_TREE" ]; then
    if [ ! -f "$LOCAL_TREE/install.sh" ] || [ ! -f "$LOCAL_TREE/lib/common.sh" ]; then
        echo -e "  ${RED}✗${NC} $(bs no_tree): ${LOCAL_TREE//[^A-Za-z0-9._\/-]/?}"
        exit 1
    fi
    LOCAL_TREE=$(cd "$LOCAL_TREE" && pwd)
fi

# Такой ветки нет — идти дальше некуда: raw ответил бы 404 на каждый файл, и человек читал бы «проверьте токен».
# Молча ставить другую ветку нельзя: стабильная и экспериментальная — выбор человека, а не загрузчика.
if [ "$REF_STATE" = "missing" ]; then
    echo -e "  ${RED}✗${NC} $(bs no_branch) ${BRANCH}"
    echo -e "  ${YELLOW}$(bs no_branch_how)${NC}"
    exit 1
fi

# Откуда ставим — строкой до загрузки: ветка, какая она для людей, и коммит. Ветку выбирает GOAMNEZIA_BRANCH, а не
# адрес однострочника: без этой строки человек, взявший адрес с dev и не задавший переменную, не узнал бы, что
# ставит стабильную main.
case "$BRANCH" in
    main) _kind="$(bs kind_main)" ;;
    dev)  _kind="$(bs kind_dev)" ;;
    *)    _kind="$(bs kind_other)" ;;
esac
if [ "$REF_STATE" = "branch" ]; then
    echo -e "  ${CYAN}•${NC} $(bs branch) ${BOLD}${BRANCH}${NC} — ${_kind}"
    echo -e "  ${YELLOW}•${NC} $(bs no_sha)"
else
    echo -e "  ${CYAN}•${NC} $(bs branch) ${BOLD}${BRANCH}${NC} — ${_kind}, $(bs commit) ${RESOLVED_REF:0:12}"
fi
[ -n "$LOCAL_TREE" ] && echo -e "  ${CYAN}•${NC} $(bs local_tree) ${LOCAL_TREE}"
if [ "$LOADER_SELF" = "1" ]; then
    echo -e "  ${DIM}• $(bs loader_self)${NC}"
fi

for cmd in curl jq tar; do
    if ! command -v "$cmd" &>/dev/null; then
        # Сухой прогон пакетов не ставит: apt-get update/install на машине разработчика или в CI меняет чужую
        # систему, а root у сухого прогона нет по договору (см. DRY выше). Печатаем, что сделали бы, и идём
        # дальше — самому загрузчику из трёх нужны только curl и tar (находка L2-03).
        if [ "$DRY" = "1" ]; then
            echo -e "  ${DIM}DRY: apt-get install -y ${cmd} — $(bs dry_no_apt)${NC}"
            continue
        fi
        echo -e "  ${CYAN}↻${NC} $(bs installing) $cmd..."
        apt-get update -qq && apt-get install -y -qq "$cmd" >/dev/null 2>&1
    fi
done

download_file() {
    local remote_path="$1"
    local local_path="$2"
    mkdir -p "$(dirname "$local_path")"

    # Три попытки с короткой паузой: CDN изредка отдаёт 5xx на ровном месте,
    # и один такой ответ не должен ронять всю установку.
    local attempt http_code=""
    for attempt in 1 2 3; do
        http_code=$(github_curl -sL -w "%{http_code}" -o "$local_path" \
            "${RAW}/${remote_path}")
        if [ "$http_code" = "200" ]; then
            return 0
        fi
        # 404 повторять бессмысленно — файла в этом коммите просто нет
        [ "$http_code" = "404" ] && break
        sleep 1
    done
    rm -f "$local_path" 2>/dev/null || true
    printf '%s' "$http_code"
    return 1
}

# ══════════════════════════════════════════════════════════════════════════════
#  СПИСОК ФАЙЛОВ (раскладка — docs/CONTRACT.md §1)
#
#  Список сверяется тестом tests/check_bootstrap_files.py: он проверяет, что
#  каждый файл репозитория из каталогов продукта перечислен здесь (иначе
#  установка приедет неполной и это заметят только на сервере), и что каждая
#  запись отсюда существует в репозитории.
#
#  FILES          — обязательные: 404 на любом из них останавливает установку.
#  FILES_OPTIONAL — необязательные: модули поздних спринтов и словарные
#                   фрагменты, которых в конкретной ветке может ещё не быть.
#                   404 на них не ошибка, а строка «пропущено».
# ══════════════════════════════════════════════════════════════════════════════
FILES=(
    "install.sh"
    # ── ядро ──
    "lib/common.sh"
    "lib/i18n.sh"
    "lib/deps.sh"
    "lib/logbudget.sh"
    "lib/backup.sh"
    "lib/selftest.sh"
    # Откат неудачного обновления по снимку прежнего кода, который кладёт этот загрузчик (OPS-02)
    "lib/update.sh"
    "lib/actions.json"
    # ── ядро продукта: Docker, контейнеры AmneziaWG, параметры, пользователи, ключи ──
    "lib/docker.sh"
    "lib/amnezia.sh"
    "lib/awg_params.sh"
    "lib/awgkeys.py"
    # Частые чтения ядра (список людей, карточка, ключи, подписка, страницы, раздачи) внутри процесса панели — тем же
    # JSON, что мост, без запуска install.sh (PERF-01, docs/PANEL.md §5b); нет файла — панель читает мостом, как прежде
    "lib/core_read.py"
    "lib/users.sh"
    # Набор случайных имён «прилагательное + столица» (users_name_suggest, действие name-suggest,
    # «🎲 Случайное имя» в SSH, боте и панели — docs/MODES_047.md «Имена ключей»)
    "lib/names_capitals.json"
    "lib/export.sh"
    # ── предполётная проверка портов 80/443 перед выбором режима (docs/PREFLIGHT.md) ──
    "lib/preflight.sh"
    "lib/lang/ru.d/preflight.sh"
    "lib/lang/en.d/preflight.sh"
    # ── Pro-режим: сайт-маскировка на домене (lib/website.sh, docs/PRO.md §4) ──
    # Обязательные, а не необязательные: без каталога шаблонов Pro-режим молча
    # ставил бы сайт «404», а без шаблона vhost nginx не собирается вовсе.
    # Каталог lib/nginx/ загрузчик создаёт сам (mkdir -p по dirname пути).
    "lib/website.sh"
    "lib/templates_lib.sh"
    "lib/catalog_tool.py"
    "lib/nginx/goamnezia.conf.tpl"
    "templates_catalog.json"
    "lib/lang/ru.d/website.sh"
    "lib/lang/en.d/website.sh"
    # ── доступы агентов к MCP по HTTP (lib/agents.sh, PLAN §6.10): без модуля
    # пункт меню и действия agent-*/mcp-http-* отвечали бы module_missing ──
    "lib/agents.sh"
    "lib/lang/ru.d/agents.sh"
    "lib/lang/en.d/agents.sh"
    # ── временная ссылка на скачивание файла (lib/share.sh, docs/SHARE.md):
    # без модуля и сервера пункт «Скачать файлом по ссылке» и действия share-*
    # отвечали бы module_missing ──
    "lib/share.sh"
    "lib/share_server.py"
    "lib/lang/ru.d/share.sh"
    "lib/lang/en.d/share.sh"
    # ── страница подключения, каталог приложений и зеркало сборок
    # (lib/pages.sh, lib/mirror.sh, docs/CLIENTS.md): без каталога службе
    # страниц нечего показывать, без модулей действия page-*/mirror-* отвечали
    # бы module_missing ──
    "lib/clients.json"
    "lib/pages.sh"
    "lib/pages_server.py"
    "lib/mirror.sh"
    "lib/lang/ru.d/pages.sh"
    "lib/lang/en.d/pages.sh"
    "lib/lang/ru.d/mirror.sh"
    "lib/lang/en.d/mirror.sh"
    # ── подписка: постоянная ссылка на человека для Clash/mihomo и роутеров (lib/subs.sh,
    # docs/SUBSCRIPTION.md): без модуля пункт «Подписка» и действия sub-* отвечали бы module_missing ──
    "lib/subs.sh"
    "lib/lang/ru.d/subs.sh"
    "lib/lang/en.d/subs.sh"
    # Короткие ссылки подписки с 2FA через Telegram-бота (docs/SUBSCRIPTION.md §11): один модуль у службы страниц
    # (маршрут /<КОД>) и у моста (действия short-*)
    "lib/shortlinks.py"
    "lib/lang/ru.d/short.sh"
    "lib/lang/en.d/short.sh"
    # ── спринт 4: обновления из upstream Amnezia, импорт существующей установки,
    # статистика по суткам, таймеры systemd (quota/watch/stats/upstream) ──
    "lib/upstream.sh"
    "lib/lang/ru.d/upstream.sh"
    "lib/lang/en.d/upstream.sh"
    "lib/import.sh"
    "lib/lang/ru.d/import.sh"
    "lib/lang/en.d/import.sh"
    "lib/stats.sh"
    "lib/lang/ru.d/stats.sh"
    "lib/lang/en.d/stats.sh"
    "lib/timers.sh"
    "lib/edgeips.sh"
    "lib/lang/ru.d/timers.sh"
    "lib/lang/en.d/timers.sh"
    # ── режимы 0.6.0: «Ленивый Pro» (первая установка по одному домену) и линия на UDP 443 в Pro
    # (lib/modes.sh, docs/MODES_047.md): без модуля пункт «1 Ленивый Pro», install.sh --lazy и действия
    # lazy-check / lazy-setup / pro-line отвечали бы module_missing ──
    "lib/modes.sh"
    "lib/lang/ru.d/modes.sh"
    "lib/lang/en.d/modes.sh"
    # ── словари: ядро и фрагменты модулей (по одному на модуль) ──
    "lib/lang/ru.sh"
    "lib/lang/en.sh"
    "lib/lang/ru.d/selftest.sh"
    "lib/lang/en.d/selftest.sh"
    "lib/lang/ru.d/docker.sh"
    "lib/lang/en.d/docker.sh"
    "lib/lang/ru.d/amnezia.sh"
    "lib/lang/en.d/amnezia.sh"
    "lib/lang/ru.d/awg_params.sh"
    "lib/lang/en.d/awg_params.sh"
    "lib/lang/ru.d/users.sh"
    "lib/lang/en.d/users.sh"
    "lib/lang/ru.d/export.sh"
    "lib/lang/en.d/export.sh"
    "lib/lang/ru.d/menu.sh"
    "lib/lang/en.d/menu.sh"
    # Запись служебных файлов, обновление и его откат, место на диске (OPS-01…05)
    "lib/lang/ru.d/update.sh"
    "lib/lang/en.d/update.sh"
    # ── вендоренный upstream Amnezia и закреплённые дайджесты образов ──
    "lib/upstream/images.json"
    "lib/upstream/awg/Dockerfile"
    "lib/upstream/awg/configure_container.sh"
    "lib/upstream/awg/run_container.sh"
    "lib/upstream/awg/start.sh"
    "lib/upstream/awg/start.sh.upstream"
    "lib/upstream/awg/template.conf"
    # ── модуль ядра AmneziaWG по выбору человека (docs/KMOD_PLAN.md, этап Б): модуль, словари и патчи модуля
    # (GPL-2.0, lib/upstream/kmod/PATCHES.md — описание в репозитории, на сервер не едет) ──
    "lib/kmod.sh"
    "lib/lang/ru.d/kmod.sh"
    "lib/lang/en.d/kmod.sh"
    "lib/upstream/kmod/awg-kmod-218.patch"
    "lib/upstream/kmod/awg-kmod-31.patch"
    # lib/upstream/xray/* в этот список НЕ входит: ядра Xray в продукте нет
    # (решение владельца 09.09.2026), каталог остаётся только в репозитории как
    # провенанс официальных скриптов Amnezia — см. lib/upstream/README.md.
    # ── MCP-сервер (stdio через ssh; HTTP-транспорт включается отдельно) ──
    "goamnezia-mcp/server.py"
    # Переходник goamnezia-connect: работает на машине АГЕНТА, на сервере лежит
    # затем, чтобы владелец забрал его оттуда же, откуда выдаёт доступ (scp).
    "goamnezia-mcp/connect.py"
    # Гайд для агента — РЕСУРС MCP (resources[].file в actions.json), а не
    # документация: сервер читает его рядом с собой на боевой машине и отдаёт
    # агенту по goamnezia://guide.
    "goamnezia-mcp/AGENT_GUIDE.md"
)

FILES_OPTIONAL=(
    # ── Telegram-бот ──
    "goamnezia-bot/bot.py"
    "goamnezia-bot/i18n.py"
    "goamnezia-bot/lang/ru.json"
    "goamnezia-bot/lang/en.json"
    "goamnezia-bot/requirements.txt"
    "goamnezia-bot/config.example.env"
    "goamnezia-bot/install_bot.sh"
    # ── веб-панель ──
    "admin-web/server.py"
    "admin-web/install_admin.sh"
    "admin-web/static/index.html"
    "admin-web/static/styles.css"
    "admin-web/static/app.js"
)

# ══════════════════════════════════════════════════════════════════════════════
#  БЫСТРЫЙ ПУТЬ: весь снимок ветки ОДНИМ архивом
#
#  Поштучная загрузка — это 76 запросов к CDN подряд, каждый со своим TLS-
#  рукопожатием: на живой установке владелец ждал заметно долго. GitHub умеет
#  отдать тот же коммит одним tar.gz (`/repos/<repo>/tarball/<ref>`), и это один
#  запрос вместо семидесяти шести. Ссылка берётся по тому же RESOLVED_REF (SHA
#  головы ветки), поэтому защита от кэша raw и атомарность «все файлы из одного
#  коммита» сохраняются.
#
#  Из архива раскладываем ТОЛЬКО пути из FILES / FILES_OPTIONAL: остальное
#  (docs/, tests/, .git-атрибуты) на сервер не едет, как и при поштучной
#  загрузке. Состояние сервера — config.json, users.json, agents.json, state/,
#  backups/, tls/, lib/upstream/images.json — не в списках и потому не
#  затрагивается: мы копируем перечисленное поверх, а не заменяем каталог.
#  Единственное исключение из «не трогать» — images.json: он в FILES, потому что
#  дайджесты образов идут вместе с кодом (так было и раньше).
#
#  Любой сбой (нет сети, 404, битый tar) — честный откат на прежний поштучный
#  путь: лучше медленно, но установиться.
# ══════════════════════════════════════════════════════════════════════════════
TARBALL_URL="https://api.github.com/repos/${REPO}/tarball/${RESOLVED_REF}"

# Снимок ветки в каталог. Возвращает 0 и печатает путь к корню снимка.
fetch_snapshot() {
    local work="$1"
    local tgz="${work}/snapshot.tar.gz" code="" root=""
    code=$(github_curl -sL --max-time 300 -w '%{http_code}' -o "$tgz" \
           -H "Accept: application/vnd.github+json" \
           "$TARBALL_URL" 2>/dev/null) || return 1
    [ "$code" = "200" ] || return 1
    [ -s "$tgz" ] || return 1
    mkdir -p "${work}/x" || return 1
    tar xzf "$tgz" -C "${work}/x" 2>/dev/null || return 1
    # GitHub кладёт всё в один каталог вида <owner>-<repo>-<sha7>. head здесь
    # закрыл бы пайп find'у, а сверху стоит pipefail — поэтому без пайпа.
    for root in "${work}/x"/*/; do
        [ -d "$root" ] || continue
        root="${root%/}"
        # Монорепозиторий go-all: продукт лежит в подкаталоге ${SUBDIR}.
        if [ -f "${root}/${SUBDIR}/install.sh" ]; then
            printf '%s' "${root}/${SUBDIR}"
            return 0
        fi
        [ -f "${root}/install.sh" ] || continue
        printf '%s' "$root"
        return 0
    done
    return 1
}

# ══════════════════════════════════════════════════════════════════════════════
#  НОВЫЙ КОД — ВО ВРЕМЕННЫЙ КАТАЛОГ, ПРЕЖНИЙ — РЯДОМ, РАСКЛАДКА — ЧЕРЕЗ RENAME (находка OPS-02)
#
#  Раньше файлы ложились поверх прежних по одному (cp -f): оборванная загрузка оставляла смесь версий, а
#  упавшая миграция новой версии — продукт, который некуда откатить, потому что прежнего кода не хранилось
#  нигде. Теперь:
#   1. Скачанное (архивом, по файлу или из GOAMNEZIA_LOCAL_TREE) собирается в STAGE: только перечисленные пути,
#      с исполняемыми битами и без CRLF. Обязательного нет — выход, каталог продукта не тронут.
#   2. Новая версия обязана запускаться — прямо из STAGE: bash -n модулей и словарей (source с синтаксической
#      ошибкой не роняет install.sh, а молча теряет модуль), компиляция python-частей, install.sh --version тем
#      же bash. Нет — обновление не начинается, работает прежняя версия.
#   3. Прежний код — рядом, в $INSTALL_DIR/.prev_code (_bs_prev_snapshot): по нему install.sh откатывает
#      неудавшуюся миграцию новой версии вместе с данными (lib/update.sh).
#   4. Раскладка в два шага (_bs_layout): все файлы — рядом с целями (<путь>.ga-new; здесь нужно место — не
#      хватило, времянки прочь, ничего не изменилось), затем rename каждого. Rename атомарен: работающий bash
#      дочитывает прежний install.sh по своему дескриптору, снимок держит прежние inode.
# ══════════════════════════════════════════════════════════════════════════════

# _bs_required <путь> — файл из обязательного списка FILES.
_bs_required() {
    local x
    for x in "${FILES[@]}"; do [ "$x" = "$1" ] && return 0; done
    return 1
}
# _bs_version_of <lib/common.sh> → GOAMNEZIA_VERSION оттуда (goinstaller-ready §1).
_bs_version_of() {
    local v
    v=$(sed -n -E 's/^GOAMNEZIA_VERSION="([0-9][0-9A-Za-z.+-]*)".*/\1/p' "$1" 2>/dev/null || true)
    printf '%s' "${v%%$'\n'*}"
}
# _bs_kv <файл> <ключ> → значение строки «ключ=значение».
_bs_kv() {
    local l
    l=$(grep -m1 -- "^$2=" "$1" 2>/dev/null) || return 1
    printf '%s' "${l#*=}"
}
# _bs_data_version → версия ДАННЫХ (config.json .version); пусто — не установлено или не узнать.
_bs_data_version() {
    local cfg="${INSTALL_DIR}/config.json" v=""
    [ -f "$cfg" ] || return 0
    if command -v jq >/dev/null 2>&1; then
        v=$(jq -r '.version // empty' "$cfg" 2>/dev/null) || v=""
    else
        # Без конвейера `| head`: под pipefail его SIGPIPE обрывал бы загрузчик (goinstaller-ready §9).
        v=$(sed -n -E 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/p' "$cfg" 2>/dev/null) || v=""
        v="${v%%$'\n'*}"
    fi
    v="${v//[^0-9A-Za-z.+-]/}"
    printf '%s' "$v"
}

# _bs_check_new <STAGE> — новая версия запускается? stdout — первая строка причины отказа.
_bs_check_new() {
    local st="$1" f out rc=0
    local -a py=() tmo=()
    for f in "$st/install.sh" "$st"/lib/*.sh "$st"/lib/lang/*.sh "$st"/lib/lang/*.d/*.sh; do
        [ -f "$f" ] || continue
        if ! out=$("${BASH:-bash}" -n "$f" 2>&1); then
            printf '%s' "${out%%$'\n'*}"
            return 1
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        for f in "$st"/lib/*.py "$st"/goamnezia-bot/*.py "$st"/goamnezia-mcp/*.py "$st"/admin-web/*.py; do
            [ -f "$f" ] && py+=("$f")
        done
        # Компиляция без записи .pyc: только синтаксис.
        if [ "${#py[@]}" -gt 0 ] && ! out=$(python3 -c 'import sys
for f in sys.argv[1:]:
    compile(open(f, "rb").read(), f, "exec")' "${py[@]}" 2>&1); then
            printf '%s' "$(printf '%s\n' "$out" | tail -n 1)"
            return 1
        fi
    fi
    command -v timeout >/dev/null 2>&1 && tmo=(timeout 60)
    out=$(GOAMNEZIA_DIR="$INSTALL_DIR" "${tmo[@]}" "${BASH:-bash}" "$st/install.sh" --version </dev/null 2>&1) || rc=$?
    if [ "$rc" != 0 ] || [[ "$out" != *"goAmnezia pro ${DISK_VER}"* ]]; then
        printf '%s' "$(printf '%s\n' "$out" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-200)"
        return 1
    fi
    return 0
}

# ── Прежний код — рядом, до замены ───────────────────────────────────────────
# $INSTALL_DIR/.prev_code/:
#   files/          прежние файлы, которые эта раскладка заменит, — жёсткими ссылками (места не занимают: новые
#                   файлы ложатся через rename, прежние inode остаются за снимком), без них — копией;
#   manifest        «old <путь>» — был и сохранён, «new <путь>» — появится с новой версией;
#   install-source  прежний .install-source (его не было — не было и до обновления);
#   meta            from/to — версии, from_ref/to_ref — коммиты, branch, at. Пишется последним: нет meta —
#                   снимок недоделан, и откатывать по нему нельзя.
# Снимок от незаконченного обновления (новый код ещё не мигрировал: версия данных та же, что у снимка) не
# переснимается: в нём последний код, который с этими данными работал, — только дописывается.
PREV="${INSTALL_DIR}/.prev_code"
PREV_MADE=0
PREV_KEPT=0
PREV_FROM=""

_bs_prev_add() {   # <каталог снимка> <путь> — одна запись манифеста (и сам файл, если он был)
    local dir="$1" f="$2"
    if [ -e "${INSTALL_DIR}/${f}" ]; then
        mkdir -p "$(dirname "${dir}/files/${f}")" 2>/dev/null || return 1
        ln "${INSTALL_DIR}/${f}" "${dir}/files/${f}" 2>/dev/null \
            || cp -p "${INSTALL_DIR}/${f}" "${dir}/files/${f}" 2>/dev/null || return 1
        { printf 'old %s\n' "$f" >>"${dir}/manifest"; } 2>/dev/null || return 1
    else
        { printf 'new %s\n' "$f" >>"${dir}/manifest"; } 2>/dev/null || return 1
    fi
}
_bs_prev_meta() {   # <каталог снимка> <версия прежнего кода>
    local ref=""
    [ -f "$1/install-source" ] && ref=$(_bs_kv "$1/install-source" ref)
    { printf 'from=%s\nto=%s\nfrom_ref=%s\nto_ref=%s\nbranch=%s\nat=%s\n' "$2" "$DISK_VER" "$ref" "$RESOLVED_REF" \
        "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$1/meta.new"; } 2>/dev/null \
        && mv -f "$1/meta.new" "$1/meta" 2>/dev/null
}
_bs_prev_snapshot() {
    local tmp f pfrom dv
    # Первая установка — возвращать нечего; снимок, оставшийся от прежней установки, не про этот код.
    if [ ! -f "${INSTALL_DIR}/install.sh" ] || [ ! -f "${INSTALL_DIR}/lib/common.sh" ]; then
        rm -rf "$PREV" 2>/dev/null
        return 0
    fi
    dv=$(_bs_data_version)
    if [ -f "$PREV/meta" ] && [ -f "$PREV/manifest" ]; then
        pfrom=$(_bs_kv "$PREV/meta" from)
        if [ -n "$pfrom" ] && { [ -z "$dv" ] || [ "$pfrom" = "$dv" ]; }; then
            for f in "${LAYOUT[@]}"; do
                grep -qxF -e "old $f" -e "new $f" "$PREV/manifest" 2>/dev/null && continue
                _bs_prev_add "$PREV" "$f" || return 1
            done
            _bs_prev_meta "$PREV" "$pfrom" || return 1
            PREV_FROM="$pfrom"
            PREV_KEPT=1
            return 0
        fi
    fi
    tmp="${INSTALL_DIR}/.prev_code.new"
    rm -rf "$tmp" 2>/dev/null
    { mkdir -p "$tmp/files" && chmod 700 "$tmp" && : >"$tmp/manifest"; } 2>/dev/null || { rm -rf "$tmp"; return 1; }
    for f in "${LAYOUT[@]}"; do
        _bs_prev_add "$tmp" "$f" || { rm -rf "$tmp" 2>/dev/null; return 1; }
    done
    if [ -f "${INSTALL_DIR}/.install-source" ] \
       && ! cp -p "${INSTALL_DIR}/.install-source" "$tmp/install-source" 2>/dev/null; then
        rm -rf "$tmp" 2>/dev/null
        return 1
    fi
    PREV_FROM=$(_bs_version_of "${INSTALL_DIR}/lib/common.sh")
    _bs_prev_meta "$tmp" "$PREV_FROM" || { rm -rf "$tmp" 2>/dev/null; return 1; }
    rm -rf "$PREV" 2>/dev/null
    mv "$tmp" "$PREV" 2>/dev/null || { rm -rf "$tmp" 2>/dev/null; return 1; }
    PREV_MADE=1
    return 0
}
# _bs_prev_restore — прежний код из снимка на место (раскладка оборвалась на втором шаге). Тот же манифест,
# что читает lib/update.sh (ga_upd_restore_code).
_bs_prev_restore() {
    local kind path bad=0
    [ -f "$PREV/manifest" ] || return 1
    while read -r kind path; do
        [[ "$path" =~ ^[A-Za-z0-9_][A-Za-z0-9_./-]*$ ]] && [[ "$path" != *..* ]] || continue
        case "$kind" in
            old)
                [ -e "$PREV/files/$path" ] || continue
                mv -f "$PREV/files/$path" "${INSTALL_DIR}/$path" 2>/dev/null \
                    || cp -p "$PREV/files/$path" "${INSTALL_DIR}/$path" 2>/dev/null || bad=1
                ;;
            new)
                rm -f "${INSTALL_DIR}/$path" 2>/dev/null
                ;;
        esac
    done <"$PREV/manifest"
    [ "$bad" = 0 ] || return 1
    if [ -f "$PREV/install-source" ]; then
        mv -f "$PREV/install-source" "${INSTALL_DIR}/.install-source" 2>/dev/null
    fi
    rm -rf "$PREV" 2>/dev/null
    return 0
}
# _bs_layout → 0 — разложено; 1 — не легло ничего (времянки убраны, прежний код цел); 2 — легло не всё.
_bs_layout() {
    local f g
    for f in "${LAYOUT[@]}"; do
        if ! mkdir -p "$(dirname "${INSTALL_DIR}/${f}")" 2>/dev/null \
           || ! cp -p "${STAGE}/${f}" "${INSTALL_DIR}/${f}.ga-new" 2>/dev/null; then
            for g in "${LAYOUT[@]}"; do rm -f "${INSTALL_DIR}/${g}.ga-new" 2>/dev/null; done
            return 1
        fi
    done
    for f in "${LAYOUT[@]}"; do
        if ! mv -f "${INSTALL_DIR}/${f}.ga-new" "${INSTALL_DIR}/${f}" 2>/dev/null; then
            for g in "${LAYOUT[@]}"; do rm -f "${INSTALL_DIR}/${g}.ga-new" 2>/dev/null; done
            return 2
        fi
    done
    return 0
}

echo -e "  ${CYAN}↻${NC} $(bs downloading) ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"

failed=0
placed=0
way="files"
SECONDS=0

# Рабочий каталог: снимок ветки, поштучная загрузка и STAGE. Не вышло в общем каталоге времянок — рядом с продуктом.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-snap.XXXXXX" 2>/dev/null) \
    || WORK=$(mktemp -d "${INSTALL_DIR}/.bootstrap-work.XXXXXX" 2>/dev/null) || WORK=""
if [ -z "$WORK" ]; then
    echo -e "  ${RED}✗${NC} $(bs no_work)"
    exit 1
fi
# Любой выход — без хвоста во времянках (exec ниже ловушку не переносит, к нему каталог уже убран).
trap 'rm -rf "${WORK:-/nonexistent}" 2>/dev/null || true' EXIT
SRC=""
if [ -n "$LOCAL_TREE" ]; then
    SRC="$LOCAL_TREE"
    way="local"
elif [ "${GOAMNEZIA_NO_TARBALL:-0}" != "1" ] && command -v tar >/dev/null 2>&1; then
    echo -e "  ${CYAN}↻${NC} $(bs arch_try)"
    SNAP_ROOT="$(fetch_snapshot "$WORK" || true)"
    if [ -n "${SNAP_ROOT:-}" ]; then
        echo -e "  ${GREEN}✓${NC} $(bs arch_ok)"
        SRC="$SNAP_ROOT"
        way="archive"
        # Обязательного файла нет в снимке — снимок неполный, доверять ему нельзя
        for f in "${FILES[@]}"; do
            [ -f "${SRC}/${f}" ] && continue
            echo -e "  ${YELLOW}•${NC} ${f} — $(bs arch_fallback)"
            way="files"
            break
        done
    else
        echo -e "  ${YELLOW}•${NC} $(bs arch_fallback)"
    fi
fi

if [ "$way" = "files" ]; then
    SRC="${WORK}/files"
    mkdir -p "$SRC"
    for f in "${FILES[@]}"; do
        if download_file "$f" "${SRC}/${f}" >/dev/null; then
            echo -e "  ${GREEN}✓${NC} ${f}"
        else
            echo -e "  ${RED}✗${NC} $(bs dl_failed) ${f}"
            failed=$((failed + 1))
        fi
    done
    for f in "${FILES_OPTIONAL[@]}"; do
        if download_file "$f" "${SRC}/${f}" >/dev/null; then
            echo -e "  ${GREEN}✓${NC} ${f}"
        else
            echo -e "  ${DIM}• ${f} — $(bs skipped_opt)${NC}"
        fi
    done
fi

# ── STAGE: только перечисленное ──────────────────────────────────────────────
# Исходное дерево не трогаем (из каталога — это чужая рабочая копия): права и переводы строк правим в копии.
STAGE="${WORK}/stage"
LAYOUT=()
if [ "$failed" = 0 ]; then
    for f in "${FILES[@]}" "${FILES_OPTIONAL[@]}"; do
        if [ ! -f "${SRC}/${f}" ]; then
            # Поштучная загрузка своё уже посчитала; в дереве из каталога обязательного нет — тоже отказ.
            if [ "$way" = local ] && _bs_required "$f"; then
                echo -e "  ${RED}✗${NC} $(bs dl_failed) ${f}"
                failed=$((failed + 1))
            fi
            continue
        fi
        if mkdir -p "$(dirname "${STAGE}/${f}")" 2>/dev/null && cp "${SRC}/${f}" "${STAGE}/${f}" 2>/dev/null; then
            LAYOUT+=("$f")
        else
            echo -e "  ${RED}✗${NC} $(bs dl_failed) ${f}"
            failed=$((failed + 1))
        fi
    done
fi

if [ "$failed" -gt 0 ]; then
    rm -rf "$WORK" 2>/dev/null || true
    echo ""
    echo -e "  ${RED}✗${NC} $(bs failed_n) ${failed}"
    echo -e "  ${YELLOW}$(bs check_token)${NC}"
    exit 1
fi
placed=${#LAYOUT[@]}

# ── Права и переводы строк ───────────────────────────────────────────────────
# CRLF в sourced-файле ломает bash не сразу, а на первой же функции с `local`,
# и сообщение об ошибке при этом ни на что не намекает — поэтому чистим всегда.
chmod +x "${STAGE}/install.sh" 2>/dev/null || true
chmod +x "${STAGE}"/lib/*.sh 2>/dev/null || true
chmod +x "${STAGE}/goamnezia-mcp/server.py" 2>/dev/null || true
chmod +x "${STAGE}/goamnezia-mcp/connect.py" 2>/dev/null || true
chmod +x "${STAGE}/admin-web/server.py" 2>/dev/null || true
# Установщики бота и панели зовутся из меню через bash, но владелец может
# запустить их и руками — исполняемый бит нужен обоим.
chmod +x "${STAGE}/goamnezia-bot/install_bot.sh" "${STAGE}/admin-web/install_admin.sh" 2>/dev/null || true
sed -i 's/\r$//' "${STAGE}/install.sh" "${STAGE}"/lib/*.sh 2>/dev/null || true
sed -i 's/\r$//' "${STAGE}"/lib/lang/*.sh "${STAGE}"/lib/lang/*.d/*.sh 2>/dev/null || true
sed -i 's/\r$//' "${STAGE}"/lib/upstream/*/*.sh 2>/dev/null || true
sed -i 's/\r$//' "${STAGE}"/lib/*.py "${STAGE}"/goamnezia-bot/*.py "${STAGE}"/goamnezia-mcp/*.py 2>/dev/null || true
sed -i 's/\r$//' "${STAGE}"/admin-web/*.py "${STAGE}"/admin-web/static/* 2>/dev/null || true

# Каким путём пошли и сколько заняло — чтобы «долго грузится» больше не был
# вопросом без ответа: видно и способ, и секунды, и число файлов.
case "$way" in
    archive) echo -e "  ${GREEN}✓${NC} $(bs way_archive), $(bs_files "$placed"), $(bs elapsed) ${SECONDS}$(bs seconds)" ;;
    local)   echo -e "  ${GREEN}✓${NC} $(bs local_tree), $(bs_files "$placed"), $(bs elapsed) ${SECONDS}$(bs seconds)" ;;
    *)       echo -e "  ${GREEN}✓${NC} $(bs way_files), $(bs_files "$placed"), $(bs elapsed) ${SECONDS}$(bs seconds)" ;;
esac

# ── Какая версия приехала ────────────────────────────────────────────────────
# «Установка завершена» — не доказательство: goVLESS печатал «Updated to latest dev», оставаясь на main
# (goinstaller-ready §2). Версию читаем из приехавшего lib/common.sh. «Обновить goAmnezia» передаёт, какую версию
# он показал человеку перед переездом (GOAMNEZIA_EXPECT_VERSION); приехала другая — обновление легло бы не
# тем, и ставить его нельзя: каталог продукта и .install-source остаются прежними, человек получает причину и
# совет повторить.
DISK_VER=$(_bs_version_of "${STAGE}/lib/common.sh")
EXPECT_VER="${GOAMNEZIA_EXPECT_VERSION:-}"
if [ -n "$EXPECT_VER" ] && [ "$DISK_VER" != "$EXPECT_VER" ]; then
    rm -rf "$WORK" 2>/dev/null || true
    echo -e "  ${RED}✗${NC} $(bs verify_bad) ${DISK_VER:-?} ≠ ${EXPECT_VER//[^0-9A-Za-z.+-]/?}"
    echo -e "  ${YELLOW}$(bs verify_how)${NC}"
    exit 1
fi

# ── Новая версия запускается? ────────────────────────────────────────────────
# Зависимости нового кода — ДО компиляции Python и раскладки: старый интерпретатор не должен
# объявить исправный выпуск «битым». В dry-run пакеты машины не меняем.
if [ "$DRY" != 1 ]; then
    if ! ( LIB_DIR="$STAGE/lib"
        # shellcheck source=/dev/null
        source "$LIB_DIR/deps.sh"
        # shellcheck source=/dev/null
        source "$LIB_DIR/i18n.sh"
        load_language "${GOAMNEZIA_LANG:-ru}" >/dev/null 2>&1
        deps_install_missing --yes core
    ); then
        rm -rf "$WORK" 2>/dev/null || true
        echo -e "  ${RED}✗${NC} $(bs deps_failed)" >&2
        exit 1
    fi
fi
if [ "$CHECK_NEW" = 1 ]; then
    _bs_why=""
    if ! _bs_why=$(_bs_check_new "$STAGE"); then
        rm -rf "$WORK" 2>/dev/null || true
        echo -e "  ${RED}✗${NC} $(bs new_broken) ${_bs_why}"
        if [ -f "${INSTALL_DIR}/install.sh" ]; then
            echo -e "  ${YELLOW}$(bs not_started)${NC}"
        else
            echo -e "  ${YELLOW}$(bs not_installed)${NC}"
        fi
        exit 1
    fi
fi

# ── Прежний код — рядом, новый — на место ────────────────────────────────────
if ! _bs_prev_snapshot; then
    rm -rf "$WORK" 2>/dev/null || true
    echo -e "  ${RED}✗${NC} $(bs prev_failed)"
    echo -e "  ${YELLOW}$(bs not_started)${NC}"
    exit 1
fi
if [ -n "$PREV_FROM" ]; then
    if [ "$PREV_KEPT" = 1 ]; then
        echo -e "  ${CYAN}•${NC} $(bs prev_kept) ${PREV_FROM}"
    else
        echo -e "  ${CYAN}•${NC} $(bs prev_saved) ${PREV_FROM}"
    fi
fi
_lay_rc=0
_bs_layout || _lay_rc=$?
rm -rf "$WORK" 2>/dev/null || true
if [ "$_lay_rc" = 1 ]; then
    [ "$PREV_MADE" = 1 ] && rm -rf "$PREV" 2>/dev/null
    echo -e "  ${RED}✗${NC} $(bs lay_failed)"
    if [ -n "$PREV_FROM" ]; then echo -e "  ${YELLOW}$(bs not_started)${NC}"; else echo -e "  ${YELLOW}$(bs not_installed)${NC}"; fi
    exit 1
elif [ "$_lay_rc" != 0 ]; then
    if [ -n "$PREV_FROM" ] && _bs_prev_restore; then
        echo -e "  ${RED}✗${NC} $(bs lay_broken) ${PREV_FROM}"
    else
        echo -e "  ${RED}✗${NC} $(bs lay_lost)"
    fi
    exit 1
fi

# ── Что легло на диск: ветка, версия, коммит ─────────────────────────────────
if [[ "$RESOLVED_REF" =~ ^[0-9a-f]{40}$ ]]; then _shown_ref="${RESOLVED_REF:0:12}"; else _shown_ref="—"; fi
echo -e "  ${GREEN}✓${NC} $(bs on_disk): $(bs branch_w) ${BRANCH}, $(bs version) $(_bs_version_of "${INSTALL_DIR}/lib/common.sh" | grep . || echo '?'), $(bs commit) ${_shown_ref}"

# Конфиги с секретами могли приехать при обновлении поверх — права на месте
chmod 600 "${INSTALL_DIR}/config.json" "${INSTALL_DIR}/users.json" 2>/dev/null || true

# ── Симлинки ─────────────────────────────────────────────────────────────────
if [ "$DRY" != "1" ]; then
    ln -sf "${INSTALL_DIR}/install.sh" /usr/local/bin/goamnezia
    [ -f "${INSTALL_DIR}/goamnezia-mcp/server.py" ] && \
        ln -sf "${INSTALL_DIR}/goamnezia-mcp/server.py" /usr/local/bin/goamnezia-mcp
fi

# ── Дальше говорим уже словарём продукта ─────────────────────────────────────
# Здесь lib/i18n.sh и lib/lang уже на диске, поэтому смысла держать вторую
# копию строк в загрузчике нет.
if [ -f "${INSTALL_DIR}/lib/i18n.sh" ]; then
    # shellcheck source=/dev/null
    source "${INSTALL_DIR}/lib/i18n.sh"
    load_language "$BS_LANG" 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} $(t bs_perms)"
    echo -e "  ${GREEN}✓${NC} $(tf bs_symlink_ready goamnezia)"
    echo ""
    echo -e "  ${GREEN}✓${NC} $(t bs_done) $(t bs_launching)"
    echo ""
fi

# Запоминаем источник установки — ветку, из которой ФАКТИЧЕСКИ легли файлы, и их коммит (или имя ветки, если
# коммит не узнан). Отсюда «Обновить goAmnezia» берёт ветку по умолчанию, а goInstaller — и умолчание при
# обновлении, и сверку «ветка сменилась» после установки: без файла человек со стабильной ветки уехал бы на
# экспериментальную одним Enter (goinstaller-ready §6). Через времянку и mv: оборванная запись не оставит
# половину файла.
_src="${INSTALL_DIR}/.install-source"
if printf 'branch=%s\nrepo=%s\nref=%s\ndir=%s\n' "$BRANCH" "$REPO" "$RESOLVED_REF" "$SUBDIR" 2>/dev/null >"${_src}.tmp"; then
    mv -f "${_src}.tmp" "$_src" 2>/dev/null || rm -f "${_src}.tmp" 2>/dev/null || true
else
    rm -f "${_src}.tmp" 2>/dev/null || true
fi

# Сухой прогон проверок заканчивается здесь: раскладка сделана, источник
# записан, а запускать install.sh и трогать /usr/local/bin незачем.
if [ "$DRY" = "1" ]; then
    echo -e "  ${DIM}$(bs dry_stop)${NC}"
    exit 0
fi

# Коммит и ожидаемая версия — только для этого запуска загрузчика. В окружении install.sh они пережили бы
# сессию меню, и следующее «Обновить goAmnezia» без ответа API приколотило бы загрузчик к старому коммиту.
# Каталог вместо GitHub (стенд) — тоже: следующее обновление из меню взяло бы файлы не из выбранной ветки.
unset GOAMNEZIA_REF GOAMNEZIA_EXPECT_VERSION GOAMNEZIA_LOCAL_TREE GOAMNEZIA_BOOTSTRAP_CHECK

# curl|bash кладёт ЭТОТ скрипт в stdin; перецепляем stdin дочернего install.sh на
# управляющий терминал (если он есть), чтобы промпты читали ввод человека.
# Detached-запуск (нет /dev/tty) сохраняет пайп — это режим для автоматизации.
if [ ! -t 0 ] && { true < /dev/tty; } 2>/dev/null; then
    exec bash "${INSTALL_DIR}/install.sh" "$@" < /dev/tty
else
    exec bash "${INSTALL_DIR}/install.sh" "$@"
fi
