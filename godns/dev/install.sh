#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# Установщик-загрузчик. Репозиторий ЗАКРЫТЫЙ: без GH_TOKEN публичный
# raw.githubusercontent.com отдаёт 404, поэтому исходники тянутся через
# api.github.com/…/tarball, а токен берётся из окружения.
#
#   GH_TOKEN=<ключ на чтение> bash install.sh      # из репозитория
#   GODNS_LOCAL_SRC=/путь bash install.sh          # из принесённого дерева
#
# Ветка — GODNS_REF (по умолчанию main).
#
# ГРАБЛИ: здесь стояло `main`, а на GitHub такой ветки нет вовсе — она
# осталась локальной и на 69 коммитов позади. Команда установки из README
# просила `tarball/main`, получала 404 и умирала сообщением про сеть и
# токен, то есть отправляла человека чинить не то. Умолчание обязано
# совпадать с тем, что реально опубликовано.
#
# Что делает: проверяет ОС и root, тянет исходники, кладёт в /opt/godns-installer,
# ставит обёртку /usr/local/bin/godns и запускает мастер.

set -euo pipefail

# B+ монорепо: potap1978/go-all, код dev-ветки — в подкаталоге godns/dev.
REPO_OWNER="${GODNS_REPO_OWNER:-potap1978}"
REPO_NAME="${GODNS_REPO_NAME:-go-all}"
SUBDIR="godns/dev"
TARBALL_REF="main"
REPO_REF="${GODNS_REF:-main}"
INSTALL_DIR="${GODNS_INSTALL_DIR:-/opt/godns-installer}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'

# ── Язык установщика ────────────────────────────────────────────────────
# Установщик работает ДО того, как оператор выберет язык в меню, поэтому
# таблицы строк проекта здесь нет — она лежит в дереве, которого ещё нет.
# Свою маленькую таблицу держим прямо тут: файл запускают через curl, и
# ничего рядом с ним может не оказаться вовсе.
#
# Язык выбираем в том же порядке, что и сама программа, плюс локаль
# системы — единственный внятный сигнал на чистой машине:
#   1) маркер goDNS (значит, это обновление и выбор уже сделан);
#   2) маркер соседа (тот же оператор, тот же язык);
#   3) LC_ALL / LANG начинается с ru;
#   4) русский — язык проекта по умолчанию.
_pick_lang() {
    local f
    for f in /opt/godns/.language /opt/govless/.language /opt/gotelegram/.language; do
        if [ -r "$f" ]; then
            case "$(head -c 2 "$f" 2>/dev/null)" in
                en) printf 'en'; return 0 ;;
                ru) printf 'ru'; return 0 ;;
            esac
        fi
    done
    case "${LC_ALL:-${LANG:-}}" in
        ru*|RU*) printf 'ru'; return 0 ;;
    esac
    case "${LC_ALL:-${LANG:-}}" in
        en*|EN*|C.*|C|POSIX) printf 'en'; return 0 ;;
    esac
    printf 'ru'
}
LANG_CODE="$(_pick_lang)"

# bi <русский> <english> — двуязычный выбор.
# Имя НЕ `t`: так называется обращение к таблице строк во всём остальном
# проекте, и проверка check-str-usage принимала первый аргумент за ключ.
# Одно имя должно значить одно.
bi() { if [ "$LANG_CODE" = "en" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

say()  { echo -e "  ${CYAN}ℹ${NC}  $*" >&2; }
ok()   { echo -e "  ${GREEN}✓${NC}  $*" >&2; }
warn() { echo -e "  ${YELLOW}!${NC}  $*" >&2; }
die()  { echo -e "  ${RED}✗${NC}  $*" >&2; exit 1; }

echo ""
echo -e "  ${CYAN}${BOLD}goDNS Pro${NC} ${DIM}$(bi "— шифрованный DNS на сертификате goVLESS / goTelegram" "— encrypted DNS on the goVLESS / goTelegram certificate")${NC}"
echo -e "  ${DIM}$(printf '─%.0s' {1..55})${NC}"

# ── root ────────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "$(bi "Запустите от root: sudo bash …" "Run as root: sudo bash …")"

# ── bash 4.0+ ───────────────────────────────────────────────────────────
if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    die "$(bi "Нужен bash 4.0+ (найден ${BASH_VERSION:-неизвестно})" "bash 4.0+ is required (found ${BASH_VERSION:-unknown})")"
fi

# ── ОС: проверяем ДО того, как что-либо поставлено ──────────────────────
# /etc/os-release не source-им: файл управляем извне и затрёт наши переменные.
osr() {
    local v
    v=$(sed -n -E "s/^[[:space:]]*$1=(.*)\$/\1/p" /etc/os-release 2>/dev/null) || v=""
    v="${v%%$'\n'*}"
    v="${v%\"}"; v="${v#\"}"; printf '%s' "$v"
}
OS_ID="$(osr ID)"; OS_VER="$(osr VERSION_ID)"; OS_PRETTY="$(osr PRETTY_NAME)"
OS_LIKE="$(osr ID_LIKE)"

if [ "$OS_ID" != "ubuntu" ]; then
    case " $OS_LIKE " in
        *" ubuntu "*) die "$(bi "Это производная Ubuntu (ID=${OS_ID}), а не Ubuntu. Нужна оригинальная Ubuntu LTS." "This is an Ubuntu derivative (ID=${OS_ID}), not Ubuntu. Original Ubuntu LTS is required.")" ;;
        *) die "$(bi "Неподдерживаемая система: ${OS_PRETTY:-ID=$OS_ID}. Нужна Ubuntu 22.04 / 24.04 / 26.04." "Unsupported system: ${OS_PRETTY:-ID=$OS_ID}. Ubuntu 22.04 / 24.04 / 26.04 is required.")" ;;
    esac
fi
_maj="${OS_VER%%.*}"; _min="${OS_VER#*.}"; _min="${_min%%.*}"
case "${_maj}${_min}" in ''|*[!0-9]*) die "$(bi "Некорректный VERSION_ID='${OS_VER}'" "Malformed VERSION_ID='${OS_VER}'")" ;; esac
if [ "$((10#$_maj))" -lt 22 ]; then
    die "$(bi "${OS_PRETTY} слишком старая. Нужна Ubuntu 22.04 LTS или новее." "${OS_PRETTY} is too old. Ubuntu 22.04 LTS or newer is required.")"
fi
case "$OS_VER" in
    22.04|24.04|26.04) ok "$(bi "Система: ${OS_PRETTY}" "System: ${OS_PRETTY}")" ;;
    *) warn "$(bi "Система: ${OS_PRETTY} — новее протестированных 22.04/24.04/26.04, продолжаю." "System: ${OS_PRETTY} — newer than the tested 22.04/24.04/26.04, continuing.")" ;;
esac

# ── зависимости загрузчика ──────────────────────────────────────────────
need_pkgs=()
command -v curl    >/dev/null 2>&1 || need_pkgs+=(curl)
command -v tar     >/dev/null 2>&1 || need_pkgs+=(tar)
command -v python3 >/dev/null 2>&1 || need_pkgs+=(python3)
command -v openssl >/dev/null 2>&1 || need_pkgs+=(openssl)
if [ ${#need_pkgs[@]} -gt 0 ]; then
    say "$(bi "Ставлю: ${need_pkgs[*]}" "Installing: ${need_pkgs[*]}")"
    # unattended-upgrades стоит на каждом облачном образе и держит dpkg-lock
    # в первые минуты жизни VPS. Ждём его, а не воюем с ним.
    if command -v cloud-init >/dev/null 2>&1; then
        timeout 300 cloud-init status --wait >/dev/null 2>&1 || true
    fi
    DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=600 update >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=600 \
        install --no-install-recommends "${need_pkgs[@]}" >/dev/null 2>&1 \
        || die "$(bi "Не удалось поставить: ${need_pkgs[*]}" "Could not install: ${need_pkgs[*]}")"
fi

# ── скачивание ──────────────────────────────────────────────────────────
# GH_TOKEN читается из окружения (приватный репозиторий). В аргументы curl
# его не кладём: /proc/<pid>/cmdline читает любой локальный пользователь.
TARBALL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/tarball/${TARBALL_REF}"
TMP="$(mktemp -d /tmp/godns-dl.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

CURL_CFG="${TMP}/curl.cfg"
# umask — в подоболочке. Голый `umask 077` тут не возвращался, и его
# наследовало ВСЁ, что установщик создаёт дальше: каталог программы
# выходил 0700, а /usr/local/bin/godns — 0700 на чистой машине и 0755
# там, где файл уже лежал. Режим установки не должен зависеть от того,
# ставили ли раньше.
(
umask 077
{
    echo 'silent'
    echo 'show-error'
    echo 'location'
    echo 'fail'
    echo 'connect-timeout = 15'
    echo 'max-time = 180'
    if [ -n "${GH_TOKEN:-}" ]; then
        printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN"
    fi
} > "$CURL_CFG"
)
chmod 600 "$CURL_CFG"

# Установка из локального дерева или архива: GODNS_LOCAL_SRC=/путь.
# Нужна там, где до GitHub не дотянуться, — офлайн-сервер, своя копия,
# проверка правки до публикации. Без неё единственный способ поставить
# приватный репозиторий — принести на сервер токен, а токен на сервере
# живёт дольше, чем задача, ради которой его принесли.
mkdir -p "$TMP/x"
if [ -n "${GODNS_LOCAL_SRC:-}" ]; then
    rm -f "$CURL_CFG"
    if [ -d "$GODNS_LOCAL_SRC" ]; then
        say "$(bi "Ставлю из локального каталога ${GODNS_LOCAL_SRC}…" "Installing from the local directory ${GODNS_LOCAL_SRC}…")"
        [ -f "${GODNS_LOCAL_SRC}/godns.sh" ] || die "$(bi "В каталоге нет godns.sh." "No godns.sh in the directory.")"
        mkdir -p "$TMP/x/godns-local"
        # --no-same-owner: каталог может быть принесён откуда угодно, а
        # разворачиваем мы его от root.
        ( cd "$GODNS_LOCAL_SRC" && tar cf - . ) \
            | tar xf - -C "$TMP/x/godns-local" --no-same-owner --no-same-permissions \
            || die "$(bi "Локальный каталог не скопировался." "The local directory could not be copied.")"
    elif [ -f "$GODNS_LOCAL_SRC" ]; then
        say "$(bi "Ставлю из локального архива ${GODNS_LOCAL_SRC}…" "Installing from the local archive ${GODNS_LOCAL_SRC}…")"
        tar xzf "$GODNS_LOCAL_SRC" -C "$TMP/x" --no-same-owner --no-same-permissions \
            || die "$(bi "Локальный архив не распаковался." "The local archive could not be unpacked.")"
        # Архив может быть как с корневым каталогом, так и без него.
        if [ -f "$TMP/x/godns.sh" ]; then
            mkdir -p "$TMP/x/godns-local"
            find "$TMP/x" -maxdepth 1 -mindepth 1 ! -name godns-local \
                -exec mv {} "$TMP/x/godns-local/" \; 2>/dev/null || true
        fi
    else
        die "$(bi "GODNS_LOCAL_SRC=${GODNS_LOCAL_SRC} — нет такого каталога или файла." "GODNS_LOCAL_SRC=${GODNS_LOCAL_SRC} — no such directory or file.")"
    fi
else
say "$(bi "Скачиваю goDNS Pro (${REPO_REF})…" "Downloading goDNS Pro (${REPO_REF})…")"
if ! curl --config "$CURL_CFG" -o "${TMP}/src.tar.gz" "$TARBALL"; then
    rm -f "$CURL_CFG"
    die "$(bi "Не скачалось. Проверьте сеть; для приватного репозитория задайте GH_TOKEN." "Download failed. Check the network; for a private repository set GH_TOKEN.")"
fi
rm -f "$CURL_CFG"

[ -s "${TMP}/src.tar.gz" ] || die "$(bi "Скачан пустой архив." "The downloaded archive is empty.")"

tar xzf "${TMP}/src.tar.gz" -C "$TMP/x" || die "$(bi "Архив не распаковался." "The archive could not be unpacked.")"
fi
SRC="$(find "$TMP/x" -maxdepth 1 -mindepth 1 -type d 2>/dev/null)" || SRC=""
SRC="${SRC%%$'\n'*}"
# Монорепо: корень архива — <owner>-<repo>-<sha>/, код продукта — <product>/<branch>/.
if [ -n "$SRC" ] && [ -f "$SRC/$SUBDIR/godns.sh" ]; then
    SRC="$SRC/$SUBDIR"
fi
[ -n "$SRC" ] && [ -f "$SRC/godns.sh" ] || die "$(bi "В архиве нет godns.sh." "No godns.sh in the archive.")"

# ── обновление поверх ───────────────────────────────────────────────────
# Момент выбран точно: НОВЫЙ lib/backup.sh уже распакован в $SRC, а старая
# программа и старое состояние ещё на месте. Значит копию снимает свежий
# код (он умеет манифест), а видит она ровно то состояние, которое было до
# обновления.
#
# Признак «уже стоит» — только config.json. Каталог для этого не годится:
# /etc/godns остаётся после неполного удаления, и человек на ЧИСТОЙ машине
# получал бы разговор про обновление там, где обновлять нечего.
#
# Всё в подоболочке: lib/common.sh определяет свои log_*, а у загрузчика
# есть say/ok/warn/die. Смешивать эти пространства имён незачем, а после
# подоболочки от продукта здесь не остаётся ни одной переменной.
GODNS_STATE_CONFIG="${GODNS_DIR:-/opt/godns}/config.json"
UPDATE_SNAPSHOT="no"
if [ -f "$GODNS_STATE_CONFIG" ]; then
    say "$(bi "Найдена установка goDNS Pro — сохраняю настройки перед обновлением…" "goDNS Pro is already installed — saving the settings before the upgrade…")"
    if (
        set +e
        # shellcheck source=/dev/null
        . "$SRC/lib/common.sh" || exit 1
        # shellcheck source=/dev/null
        . "$SRC/lib/i18n.sh"   || exit 1
        # shellcheck source=/dev/null
        . "$SRC/lib/backup.sh" || exit 1
        # shellcheck source=/dev/null
        . "$SRC/lib/update.sh" || exit 1
        load_language "$(detect_language)" >/dev/null 2>&1
        godns_update_prepare
    ); then
        # Только если копия ДЕЙСТВИТЕЛЬНО снята. На совпадающих версиях
        # godns_update_prepare возвращает 0, ничего не сняв (это
        # переустановка), а флаг взводился и печатал «перенос доделается
        # при первом запуске» — переноса не будет, finish на same молчит.
        # Маркер пишет godns_update_prepare и ТОЛЬКО при настоящем
        # обновлении: на fresh и на same он возвращает 0, ничего не сняв.
        # Раньше флаг взводился на любом нуле, и переустановка той же
        # версии печатала подряд «Та же версия — это переустановка» и
        # «перенос доделается при первом запуске». Переноса не будет:
        # godns_update_finish на same — молчаливый no-op.
        if [ -f "${GODNS_ETC:-/etc/godns}/.pre-update-backup" ]; then
            UPDATE_SNAPSHOT="yes"
        fi
    else
        # Отказ здесь установку НЕ отменяет: подмена программы состояния не
        # касается вовсе. Но человек обязан знать, что страховки нет — а
        # настоящий запрет «нет копии — нет миграции» стоит там, где конфиг
        # действительно переписывается (godns_update_finish).
        warn "$(bi "Копию настроек снять не удалось (нет места в /root?)." "Could not take a copy of the settings (no space in /root?).")"
        warn "$(bi "Установка продолжится; настройки в ${GODNS_DIR:-/opt/godns} не тронутся." "The install continues; the settings in ${GODNS_DIR:-/opt/godns} stay untouched.")"
    fi
fi

# ── установка ───────────────────────────────────────────────────────────
# Старую версию сносим целиком, но /etc/godns и /opt/godns (состояние и
# конфиг оператора) не трогаем — они живут отдельно от исходников.
# Здесь же проходит граница «код vs данные»: в $INSTALL_DIR не должно быть
# ничего, что оператор настраивал руками, иначе эта строка съедала бы это
# при каждом обновлении.
# ГРАБЛИ: раньше здесь было `rm -rf $INSTALL_DIR` и следом `cp -R`. Обрыв
# между ними (потеря связи, нехватка места, Ctrl-C) оставлял обёртку
# /usr/local/bin/godns указывающей на полудерево, и продукт переставал
# запускаться — причём именно при ОБНОВЛЕНИИ поверх рабочей установки.
# Собираем рядом и подменяем одним движением: в худшем случае остаётся
# прежняя рабочая копия и мусорный .new.
NEW_DIR="${INSTALL_DIR}.new"
OLD_DIR="${INSTALL_DIR}.old"
rm -rf "$NEW_DIR" "$OLD_DIR"
mkdir -p "$NEW_DIR"
cp -R "$SRC/." "$NEW_DIR/" || die "$(bi "Не удалось разложить файлы в ${NEW_DIR}." "Could not lay out the files in ${NEW_DIR}.")"
# Режимы задаём явно, а не оставляем на umask: `chmod +x` поверх 0600
# даёт 0700, поверх 0644 — 0755, и одна и та же установка получалась
# разной на чистой машине и поверх прежней.
chmod 0755 "$NEW_DIR"
# AppleDouble-мусор из архивов, собранных штатным tar на macOS (._foo,
# .DS_Store). На сервере он безвреден для работы, но test_structure на
# цели честно падал на нём, приняв за посторонние файлы. Чистим при
# раскладке — на чистом дереве это no-op.
find "$NEW_DIR" \( -name '._*' -o -name '.DS_Store' \) -delete 2>/dev/null || true
find "$NEW_DIR" -type d -exec chmod 0755 {} + 2>/dev/null || true
find "$NEW_DIR" -type f -exec chmod 0644 {} + 2>/dev/null || true
chmod 0755 "$NEW_DIR/godns.sh" 2>/dev/null || true
find "$NEW_DIR" -name '*.sh' -exec chmod 0755 {} + 2>/dev/null || true
[ -x "$NEW_DIR/godns.sh" ] || die "$(bi "Дерево разложено не полностью — установка отменена." "The tree is incomplete — the install is cancelled.")"

if [ -d "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "$OLD_DIR" || die "$(bi "Не удалось убрать прежнюю версию." "Could not move the previous version away.")"
fi
if ! mv "$NEW_DIR" "$INSTALL_DIR"; then
    # Подмена не удалась — возвращаем прежнюю, чтобы не оставить оператора
    # без работающей программы.
    [ -d "$OLD_DIR" ] && mv "$OLD_DIR" "$INSTALL_DIR"
    die "$(bi "Не удалось установить новую версию, прежняя возвращена." "Could not install the new version; the previous one is back in place.")"
fi
rm -rf "$OLD_DIR"

cat > /usr/local/bin/godns <<WRAP
#!/bin/bash
exec bash "${INSTALL_DIR}/godns.sh" "\$@"
WRAP
chmod 0755 /usr/local/bin/godns

ok "$(bi "Установлено в ${INSTALL_DIR}" "Installed to ${INSTALL_DIR}")"
ok "$(bi "Команда: ${BOLD}godns${NC}" "Command: ${BOLD}godns${NC}")"
if [ "$UPDATE_SNAPSHOT" = "yes" ]; then
    ok "$(bi "Настройки прежней установки на месте — перенос доделается при первом запуске." "The previous settings are in place — the migration finishes on the first run.")"
    say "$(bi "Порты и секретный путь DoH останутся прежними: клиентам менять нечего." "Ports and the secret DoH path stay the same: clients need no changes.")"
fi
echo ""

# ГРАБЛИ: exec ЗАМЕНЯЕТ процесс, и trap EXIT не срабатывает — каждый запуск
# установщика оставлял бы в /tmp распакованное дерево исходников целиком.
# Убираем руками ДО exec.
rm -rf "$TMP"
trap - EXIT

exec bash "${INSTALL_DIR}/godns.sh"
