#!/bin/bash
# GoTelegram Pro — Bootstrap installer for private repo
# Downloads all files and launches install.sh

set -euo pipefail

REPO="potap1978/go-all"
BRANCH="${GOTELEGRAM_BRANCH:-main}"
REPO_SLUG="${REPO}"
PAT="${GOTELEGRAM_PAT:-}"
INSTALL_DIR="/opt/gotelegram"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'

echo ""
echo -e "  ${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "  ${YELLOW}║${NC}  ${BOLD}GoTelegram Pro — Установка${NC}                          ${YELLOW}║${NC}"
echo -e "  ${YELLOW}║${NC}                                                      ${YELLOW}║${NC}"
echo -e "  ${YELLOW}║${NC}  MTProxy менеджер с Telegram-ботом                   ${YELLOW}║${NC}"
echo -e "  ${YELLOW}║${NC}  Stealth-режим, 1800+ шаблонов сайтов                ${YELLOW}║${NC}"
echo -e "  ${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# Check root
if [ "$(id -u)" -ne 0 ]; then
    echo -e "  ${RED}✗${NC} Запустите от root: ${CYAN}sudo bash bootstrap.sh${NC}"
    exit 1
fi

if [ -z "$PAT" ]; then
    echo -e "  ${RED}✗${NC} Не задан GitHub token."
    echo -e "  ${YELLOW}Запустите так:${NC} ${CYAN}GOTELEGRAM_PAT=YOUR_PAT sudo -E bash bootstrap.sh${NC}"
    exit 1
fi

# Check dependencies
for cmd in curl jq; do
    if ! command -v "$cmd" &>/dev/null; then
        echo -e "  ${CYAN}↻${NC} Установка $cmd..."
        apt-get update -qq && apt-get install -y -qq "$cmd" >/dev/null 2>&1
    fi
done

# Токен — только в заголовке, и не в аргументах curl: командную строку любого
# процесса (`ps -eo args`, /proc/<pid>/cmdline) видит любой пользователь сервера,
# а `-H "Authorization: token …"` клал токен именно туда. `-H @файл` (curl 7.55+)
# берёт заголовок из файла; файлом служит подстановка `<(auth_header)` — канал
# без следа на диске, а printf встроен в bash и своей командной строки не имеет.
# Не `-H @-`: при `curl … | bash` stdin — это сам скрипт, curl съел бы его остаток.
# В каждом вызове curl первым идёт `-q`: не читать ~/.curlrc. Там у root бывает
# verbose или trace — и заголовок с токеном ушёл бы на экран и в журнал.
auth_header() { printf 'Authorization: token %s\n' "$PAT"; }

# Качаем через raw.githubusercontent (CDN) — быстро. Но у raw свой кэш на пять минут
# (cache-control: max-age=300) на URL ВЕТКИ: обновление, запущенное сразу после пуша,
# молча получало предыдущую версию файлов — на диске оставался старый код, а человек
# видел «Установка завершена» и прежний номер версии в шапке.
#
# Замерено вживую: спустя минуту после пуша URL ветки отдавал старое содержимое, и ни
# `Cache-Control: no-cache`, ни `?nocache=<штамп>` этого НЕ пробивали. Что работает —
# спросить у API SHA головы ветки и качать по SHA: такие URL неизменяемы, поэтому
# кэш на них всегда корректен. Бонусом получаем атомарность: все файлы приезжают из
# ОДНОГО коммита, и пуш в середине обновления не смешает разные версии.
#
# Один запрос к API на весь запуск. Не вышло (лимит/сеть) — откатываемся на ветку,
# как раньше: лучше обновиться с риском кэша, чем не обновиться вовсе.
# Осторожно: вверху стоит `set -euo pipefail`. Поэтому ответ сначала кладём в
# переменную, и только потом разбираем: если тянуть `curl | grep -m1`, то grep
# закрывает пайп на первом совпадении, curl получает SIGPIPE, pipefail делает
# присваивание неуспешным — и скрипт молча уходил на запасной путь (а то и падал).
# По той же причине на каждом шаге стоит `|| true`.
# Таймауты обязательны: без них заглохший запрос к API вешал установку молча.
# Запрос идёт после проверок root и токена: без них сеть трогать незачем.
# B+ монорепо: код dev-ветки всегда лежит в main подкаталогом <product>/dev,
# поэтому fallback — main, а не dev (dev-git-ветки в монорепо нет).
RESOLVED_REF="main"
_api_json="$(curl -q -fsSL --connect-timeout 10 --max-time 30 -H @<(auth_header) \
             "https://api.github.com/repos/${REPO}/commits/main" 2>/dev/null || true)"
_sha="$(printf '%s' "${_api_json}" | grep -oE '"sha"[[:space:]]*:[[:space:]]*"[0-9a-f]{40}"' \
        | head -1 | grep -oE '[0-9a-f]{40}' || true)"
if [ -n "${_sha}" ]; then RESOLVED_REF="${_sha}"; fi
# B+ монорепо: файлы продукта лежат в подкаталоге <product>/<branch>.
# Ветка `main` — корень продукта, `dev` — <product>/dev.
case "$BRANCH" in
    main|master) SUBDIR="gotelegram" ;;
    dev)         SUBDIR="gotelegram/dev" ;;
    *)           SUBDIR="gotelegram/$BRANCH" ;;
esac
RAW="https://raw.githubusercontent.com/${REPO}/${RESOLVED_REF}/${SUBDIR}"

# Как качаем. Живой случай 29.10.2026: каталог шаблонов (13,6 МБ) шёл из приватного
# репозитория со скоростью ~80 КБ/с — три минуты без единой строки на экране, и
# установщик трижды прервали как «зависший». Отсюда:
#  • --compressed: raw.githubusercontent отдаёт gzip, каталог сжимается в ~10 раз.
#    curl без zlib на этом ключе падает, поэтому ставим его, только если zlib есть;
#  • таймауты: подключение — 15 с; меньше 1 КБ/с в течение 30 с — обрыв (раньше
#    заглохшее соединение висело вечно). Последний предел — 10 минут на файл,
#    для каталога 30: если raw отдаст его без gzip, 13,6 МБ на медленном канале
#    в 10 минут не влезут, а каждый повтор начинается с нуля;
#  • до трёх попыток с паузой — и при ошибке curl, и при плохом HTTP-коде.
#    401/403/404 не повторяем: это токен или путь, повтор не поможет;
#  • перед большим файлом — строка, что он идёт долго, а в терминале ещё и
#    полоса прогресса curl. Без терминала (goInstaller, автоматизация) полосы
#    нет: в журнал она легла бы мусором из \r.
CURL_DL_OPTS=(-L --connect-timeout 15 --speed-limit 1024 --speed-time 30)
case "$(curl -q -V 2>/dev/null)" in *libz*) CURL_DL_OPTS+=(--compressed) ;; esac
SHOW_PROGRESS=0
if [ -t 1 ] && [ -t 2 ]; then SHOW_PROGRESS=1; fi

# Код выхода curl — человеческими словами, для строки об ошибке и повторе.
curl_reason() {
    case "$1" in
        6)        echo "не найден адрес сервера (DNS)" ;;
        7)        echo "не удалось подключиться" ;;
        28)       echo "таймаут: сервер не отвечает или данные не идут" ;;
        18|56|92) echo "соединение оборвалось" ;;
        35|60)    echo "ошибка TLS" ;;
        23)       echo "не удалось записать файл (место на диске?)" ;;
        *)        echo "ошибка curl $1" ;;
    esac
}

# Качает файл в "<local_path>.part" рядом с рабочим; сам рабочий не трогает —
# на место его ставит вызывающий, когда скачались все (см. ниже).
# Успех — только код curl 0, HTTP 200 и непустой файл. При неудаче в DL_FAIL_RC —
# код curl последней попытки (0 — curl отработал, но ответ не тот).
DL_FAIL_RC=0
download_file() {
    local remote_path="$1"
    local local_path="$2"
    local part="${local_path}.part"
    local attempt http_code rc reason=""
    local -a opts=("${CURL_DL_OPTS[@]}")
    mkdir -p "$(dirname "$local_path")"

    case "$remote_path" in
        templates_catalog.json)
            echo -e "  ${CYAN}↻${NC} ${remote_path} — каталог шаблонов сайтов, файл большой: загрузка может занять несколько минут…"
            opts+=(--max-time 1800)
            if [ "$SHOW_PROGRESS" = 1 ]; then opts+=(--progress-bar); else opts+=(-s); fi
            ;;
        *) opts+=(--max-time 600 -s) ;;
    esac

    for attempt in 1 2 3; do
        rm -f "$part"
        rc=0
        http_code=$(curl -q "${opts[@]}" -w '%{http_code}' -o "$part" \
            -H @<(auth_header) "${RAW}/${remote_path}") || rc=$?
        if [ "$rc" -eq 0 ] && [ "$http_code" = "200" ] && [ -s "$part" ]; then
            return 0
        fi
        rm -f "$part"
        if [ "$rc" -ne 0 ]; then
            reason=$(curl_reason "$rc")
        elif [ "$http_code" != "200" ]; then
            reason="HTTP ${http_code}"
        else
            reason="пустой ответ"
        fi
        case "$http_code" in 401|403|404) break ;; esac
        if [ "$attempt" -lt 3 ]; then
            echo -e "  ${YELLOW}↻${NC} ${remote_path}: ${reason} — повтор через $((attempt * 3)) с (попытка $((attempt + 1)) из 3)" >&2
            sleep $((attempt * 3))
        fi
    done

    DL_FAIL_RC=$rc
    echo -e "  ${RED}✗${NC} Ошибка загрузки ${remote_path} (${reason})" >&2
    return 1
}

# File list
FILES=(
    "install.sh"
    "install_gotelegram_bot.sh"
    "templates_catalog.json"
    "lib/common.sh"
    "lib/telemt.sh"
    "lib/telemt_config.sh"
    "lib/backup.sh"
    "lib/website.sh"
    "lib/templates_catalog.sh"
    "lib/stats.sh"
    "lib/shared443.sh"
    "lib/edgeips.sh"
    "lib/diagnose.sh"
    "lib/warp_connector.sh"
    "lib/warp_egress.sh"
    "lib/metrics.sh"
    "lib/deps.sh"
    "lib/mekofix.sh"
    "lib/mekofix_v4.sh"
    "lib/users.sh"
    "lib/selftest.sh"
    "lib/logbudget.sh"
    "lib/i18n.sh"
    "lib/lang/en.sh"
    "lib/lang/ru.sh"
    "gotelegram-bot/bot.py"
    "gotelegram-bot/i18n.py"
    "gotelegram-bot/lang/en.json"
    "gotelegram-bot/lang/ru.json"
    "gotelegram-bot/config.example.env"
    "gotelegram-bot/requirements.txt"
    "gotelegram-bot/README.md"
    "admin-web/server.py"
    "admin-web/static/index.html"
    "admin-web/static/styles.css"
    "admin-web/static/app.js"
)

echo -e "  ${CYAN}↻${NC} Загрузка файлов в ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}/lib/lang" "${INSTALL_DIR}/gotelegram-bot/lang" "${INSTALL_DIR}/admin-web/static"

# Сначала все файлы ложатся в .part рядом с рабочими, и только когда скачались
# ВСЕ, переезжают на место (mv в пределах каталога атомарен). Раньше curl писал
# прямо в рабочий файл: обрыв посреди обновления оставлял обрезанный install.sh,
# а сбой на десятом файле — смесь новых и старых версий. Теперь при любой
# неудаче, в том числе Ctrl+C, .part удаляются, а установленные файлы остаются
# как были. На первом же сбое останавливаемся: без всех файлов ставить нечего.
# Что честно сказать при сбое, зависит от того, была ли установка до нас.
if [ -f "${INSTALL_DIR}/install.sh" ]; then
    KEPT_MSG="установленные файлы не изменены"
else
    KEPT_MSG="ничего не установлено"
fi
cleanup_parts() {
    local p
    for p in "${FILES[@]}"; do rm -f "${INSTALL_DIR}/${p}.part" 2>/dev/null || true; done
}
trap cleanup_parts EXIT
trap 'echo -e "\n  ${RED}✗${NC} Прервано, ${KEPT_MSG}" >&2; exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

for f in "${FILES[@]}"; do
    if download_file "$f" "${INSTALL_DIR}/${f}"; then
        echo -e "  ${GREEN}✓${NC} ${f}"
    else
        echo "" >&2
        echo -e "  ${RED}✗${NC} Загрузка прервана, ${KEPT_MSG}" >&2
        # Ошибка записи (23) — это диск, а не токен или сеть: причина уже в строке выше.
        if [ "$DL_FAIL_RC" -ne 23 ]; then
            echo -e "  ${YELLOW}Проверьте токен доступа и подключение к сети${NC}" >&2
        fi
        exit 1
    fi
done

# Перестановка занимает миллисекунды; Ctrl+C посреди неё оставил бы смесь версий,
# поэтому на это время сигналы прерывания игнорируем.
# Если mv всё же откажет (диск, права), часть файлов может быть уже новой —
# так и говорим и просим запустить установку ещё раз.
trap '' INT TERM HUP
moved=0
for f in "${FILES[@]}"; do
    if ! mv -f "${INSTALL_DIR}/${f}.part" "${INSTALL_DIR}/${f}"; then
        if [ "$moved" -gt 0 ]; then
            echo -e "  ${RED}✗${NC} Не удалось поставить на место ${f}: часть файлов уже обновлена — запустите установку ещё раз" >&2
        else
            echo -e "  ${RED}✗${NC} Не удалось поставить на место ${f}, ${KEPT_MSG}" >&2
        fi
        exit 1
    fi
    moved=$((moved + 1))
done
trap - EXIT INT TERM HUP

# Fix permissions and line endings
echo -e "  ${CYAN}↻${NC} Настройка прав..."
chmod +x "${INSTALL_DIR}/install.sh" "${INSTALL_DIR}/install_gotelegram_bot.sh"
chmod +x "${INSTALL_DIR}"/lib/*.sh
chmod +x "${INSTALL_DIR}/admin-web/server.py" 2>/dev/null || true
sed -i 's/\r$//' "${INSTALL_DIR}/install.sh" "${INSTALL_DIR}/install_gotelegram_bot.sh" "${INSTALL_DIR}"/lib/*.sh "${INSTALL_DIR}"/lib/lang/*.sh 2>/dev/null || true
sed -i 's/\r$//' "${INSTALL_DIR}"/gotelegram-bot/*.py "${INSTALL_DIR}"/gotelegram-bot/lang/*.json 2>/dev/null || true
sed -i 's/\r$//' "${INSTALL_DIR}"/admin-web/*.py "${INSTALL_DIR}"/admin-web/static/* 2>/dev/null || true

# Create symlink
ln -sf "${INSTALL_DIR}/install.sh" /usr/local/bin/gotelegram
echo -e "  ${GREEN}✓${NC} Команда ${CYAN}gotelegram${NC} доступна"

echo ""
echo -e "  ${GREEN}✓${NC} Установка завершена! Запуск..."
echo ""

# Запоминаем источник установки, чтобы пункт меню «Обновить goTelegram» знал,
# из какой ветки тянуть обновление.
printf 'branch=%s\nrepo=%s\n' "$BRANCH" "$REPO" > "${INSTALL_DIR}/.install-source" 2>/dev/null || true

# Launch
# curl|bash кладёт ЭТОТ скрипт в stdin; перецепляем stdin дочернего install.sh на
# управляющий терминал (если он есть), чтобы промпты читали ввод. Detached-запуск
# (нет /dev/tty) сохраняет пайп — для автоматизации.
if [ ! -t 0 ] && { true < /dev/tty; } 2>/dev/null; then
    exec bash "${INSTALL_DIR}/install.sh" "$@" < /dev/tty
else
    exec bash "${INSTALL_DIR}/install.sh" "$@"
fi
