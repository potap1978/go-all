#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — i18n engine: таблица строк (ассоциативный массив) + t()/tf().
#
# Использование:
#   source lib/common.sh          # (не обязательно, но даёт json_get/config_set)
#   source lib/i18n.sh
#   load_language "$(detect_language)"
#   echo "$(t menu_install)"
#   log_info "$(tf cert_found "$domain")"
#
# Контракт:
#   • Язык по умолчанию — РУССКИЙ. Оператор этого проекта русскоязычный,
#     и «не смог выбрать язык» должно означать русский, а не английский.
#   • t <ключ> печатает САМ КЛЮЧ, если строки нет. Пустая строка вместо
#     фразы невидима в интерфейсе и уезжает в релиз; ключ — виден сразу.
#   • Всё, что печатает подсказки (pick_language_interactive), пишет их
#     в stderr: результат функции забирают через $( ), и приглашение
#     в stdout склеилось бы с кодом языка.

# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ №1 — bash 4.0+. НЕ УБИРАТЬ ЭТУ ПРОВЕРКУ.
#
# Таблица строк — ассоциативный массив. В bash 3.2 (штатный в macOS)
# ассоциативных массивов нет: `I18N[some_key]=...` там разбирается как
# ОБЫЧНЫЙ индексный массив, ключ вычисляется как арифметическое выражение,
# нечисловое имя даёт 0 — и КАЖДАЯ строка молча пишется в I18N[0].
# Ошибки при этом нет: интерфейс просто превращается в кашу, где все
# фразы одинаковые (последняя из файла). Отладить это по симптому почти
# невозможно, поэтому отказываемся стартовать заранее и громко.
#
# Такая же проверка продублирована в godns.sh (точка входа) — намеренно:
# сюда можно попасть и через прямой `source lib/i18n.sh` из теста.
# ═══════════════════════════════════════════════════════════════════════
if [ -z "${BASH_VERSINFO+x}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "" >&2
    echo "  goDNS Pro: нужен bash 4.0 или новее — сейчас ${BASH_VERSION:-не bash}." >&2
    echo "  goDNS Pro: bash 4.0+ required — this is ${BASH_VERSION:-not bash}." >&2
    echo "" >&2
    echo "  Ubuntu 22.04/24.04 несёт bash 5.x, так что на целевой системе" >&2
    echo "  этого сообщения быть не должно. На macOS: brew install bash." >&2
    echo "  On Ubuntu 22.04/24.04 bash is 5.x; on macOS: brew install bash." >&2
    echo "" >&2
    exit 1
fi

# `declare -g` появился в bash 4.2. На 4.0/4.1 обходимся без него: файл
# подключается на верхнем уровне, где объявление и так глобальное.
declare -gA I18N 2>/dev/null || declare -A I18N

# Язык по умолчанию — русский (см. контракт выше).
GODNS_LANG_DEFAULT="ru"
GODNS_LANGS="ru en"
LANG_CODE="${LANG_CODE:-$GODNS_LANG_DEFAULT}"

# ── Внутреннее: каталог с файлами локалей ───────────────────────────────
# readlink -f нет в BSD/macOS старых версий, поэтому путь разрешаем через
# cd+pwd. BASH_SOURCE внутри функции указывает на файл, ГДЕ функция
# объявлена, то есть на lib/i18n.sh — что нам и нужно.
i18n_lang_dir() {
    if [ -n "${GODNS_LANG_DIR:-}" ]; then
        printf '%s\n' "$GODNS_LANG_DIR"
        return 0
    fi
    local here=""
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || here=""
    if [ -z "$here" ]; then here="."; fi
    printf '%s\n' "${here}/lang"
}

# Допустимый код языка? (rc 0 — да)
i18n_lang_known() {
    local want="$1" have
    for have in $GODNS_LANGS; do
        if [ "$want" = "$have" ]; then return 0; fi
    done
    return 1
}

# ── Загрузка языка ──────────────────────────────────────────────────────
# rc 0 — файл найден и подключён.
# rc 1 — ни одного файла локали нет. Это НЕ фатально: t() продолжит
#        печатать ключи, и установщик останется работоспособным.
load_language() {
    local lang="${1:-$GODNS_LANG_DEFAULT}"
    i18n_lang_known "$lang" || lang="$GODNS_LANG_DEFAULT"

    local dir=""
    dir=$(i18n_lang_dir) || dir=""

    local candidate file=""
    # Порядок: запрошенный → русский (дефолт) → английский.
    for candidate in "$lang" "$GODNS_LANG_DEFAULT" "en"; do
        if [ -f "${dir}/${candidate}.sh" ]; then
            file="${dir}/${candidate}.sh"
            lang="$candidate"
            break
        fi
    done

    if [ -z "$file" ]; then
        I18N=()
        LANG_CODE="$lang"
        # Тихо в stderr и один раз за процесс — иначе меню зальёт варнингами.
        if [ -z "${I18N_NO_LANG_WARNED:-}" ]; then
            I18N_NO_LANG_WARNED=1
            # Двуязычно и без t(): таблицы строк здесь нет по определению —
            # именно её отсутствие мы и сообщаем.
            echo "  goDNS Pro: файлы локалей не найдены в ${dir} — интерфейс покажет ключи." >&2
            echo "  goDNS Pro: no locale files in ${dir} — the interface will show keys." >&2
        fi
        return 1
    fi

    I18N=()
    # shellcheck disable=SC1090
    source "$file"
    LANG_CODE="$lang"
    return 0
}

# ── Перевод ─────────────────────────────────────────────────────────────
# Нет ключа → печатаем сам ключ. Пустое значение НЕ подменяем (используем
# `-`, а не `:-`): строка-разделитель может быть пустой намеренно, и
# подмена её на имя ключа была бы ошибкой самого движка.
t() {
    local key="${1:-}"
    if [ -z "$key" ]; then
        printf '\n'
        return 0
    fi
    printf '%s\n' "${I18N[$key]-$key}"
}

# ── Перевод с подстановкой (printf) ─────────────────────────────────────
tf() {
    local key="${1:-}"
    shift || true
    local fmt="${I18N[$key]-}"

    if [ -z "$fmt" ]; then
        # Ключа нет — печатаем ключ и аргументы, чтобы пропажа была видна.
        printf '%s' "$key"
        if [ "$#" -gt 0 ]; then printf ' %s' "$@"; fi
        printf '\n'
        return 0
    fi

    # shellcheck disable=SC2059
    printf "${fmt}\n" "$@" 2>/dev/null || printf '%s\n' "$fmt"
    return 0
}

# ── Текущий язык ────────────────────────────────────────────────────────
get_language() { printf '%s\n' "${LANG_CODE:-$GODNS_LANG_DEFAULT}"; }

# ── Определение сохранённого языка ──────────────────────────────────────
# Порядок: config.json (.language) → $GODNS_DIR/.language → соседи
# (goVLESS / goTelegram, чтобы не спрашивать оператора второй раз) → ru.
#
# json_get может не быть (i18n.sh подключают и в отрыве от common.sh),
# поэтому есть запасной разбор через python3. jq здесь нет и не будет.
detect_language() {
    local cfg="${GODNS_CONFIG:-/opt/godns/config.json}"
    local lang=""

    if [ -f "$cfg" ]; then
        if declare -F json_get >/dev/null 2>&1; then
            lang=$(json_get "$cfg" "language" "") || lang=""
        else
            # ГРАБЛИ: heredoc НЕЛЬЗЯ открывать внутри $( ) — тело окажется
            # уже за закрывающей скобкой, и bash не соберёт файл вовсе
            # («syntax error near unexpected token»). Поэтому python3 -c.
            lang=$(python3 -c 'import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
    value = data.get("language") if isinstance(data, dict) else None
    sys.stdout.write(value if isinstance(value, str) else "")
except Exception:
    sys.stdout.write("")' "$cfg" 2>/dev/null) || lang=""
        fi
    fi

    if [ -z "$lang" ]; then
        lang=$(i18n_read_marker "${GODNS_DIR:-/opt/godns}/.language") || lang=""
    fi
    # Соседей читаем через переменные, а не по жёсткому пути: иначе тесты
    # на реальном сервере начинают зависеть от того, что там уже стоит.
    if [ -z "$lang" ]; then
        lang=$(i18n_read_marker "${GOVLESS_LANG_MARKER:-/opt/govless/.language}") || lang=""
    fi
    if [ -z "$lang" ]; then
        lang=$(i18n_read_marker "${GOTELEGRAM_LANG_MARKER:-/opt/gotelegram/.language}") || lang=""
    fi

    i18n_lang_known "$lang" || lang="$GODNS_LANG_DEFAULT"
    printf '%s\n' "$lang"
}

# Прочитать двухбуквенный код из файла-маркера.
# NB: `tr … | head -c 2` здесь был бы миной — head закрывает канал, tr
# умирает по SIGPIPE, и под `set -o pipefail` подстановка вернёт не ноль.
# cut дочитывает вход до конца, поэтому сигнала не возникает.
i18n_read_marker() {
    local path="$1" value=""
    [ -f "$path" ] || return 0
    value=$(tr -d '[:space:]' < "$path" 2>/dev/null | cut -c1-2) || value=""
    printf '%s' "$value"
}

# ── Сохранение языка ────────────────────────────────────────────────────
save_language() {
    local lang="${1:-}"
    i18n_lang_known "$lang" || return 1

    local dir="${GODNS_DIR:-/opt/godns}"
    mkdir -p "$dir" 2>/dev/null || true
    # Локаль системы — последняя подсказка перед умолчанием. Установщик
    # её читает (_pick_lang), а продукт не читал: на английской машине
    # первая же строка (startup_checks) выходила по-русски ДО окна выбора
    # языка. Два разных ответа на один вопрос в одном продукте.
    if [ -z "$lang" ]; then
        case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in
            en*|C|POSIX) lang="en" ;;
        esac
    fi
    [ -n "$lang" ] || lang="${GODNS_LANG_DEFAULT:-ru}"
    printf '%s\n' "$lang" > "${dir}/.language" 2>/dev/null || true
    # ГРАБЛИ: файл наследовал 0600 root, а бот и панель работают под
    # своими пользователями — в журнале бота стояло «cannot read
    # /opt/godns/.language: Permission denied», и выбранный оператором
    # язык до бота не доходил вовсе: он молча говорил на языке по
    # умолчанию. Язык — не секрет (там «ru» или «en»), в отличие от
    # config.json, который читается только от root через демон.
    chmod 0644 "${dir}/.language" 2>/dev/null || true

    # config.json — только если common.sh рядом; свой писатель JSON
    # заводить не нужно, config_set уже атомарный и ставит 600.
    if declare -F config_set >/dev/null 2>&1; then
        config_set language "$lang" >/dev/null 2>&1 || true
    fi
    return 0
}

# ── Выбор языка при первом запуске ──────────────────────────────────────
# Печатается ДО того, как язык известен, поэтому текст здесь двуязычный и
# захардкожен намеренно — прогонять его через t() было бы нечем.
# Приглашение идёт в stderr, код языка — в stdout.
pick_language_interactive() {
    echo "" >&2
    echo "  ┌─────────────────────────────────────────────────────┐" >&2
    echo "  │  Выберите язык / Select language                    │" >&2
    echo "  ├─────────────────────────────────────────────────────┤" >&2
    echo "  │  1) Русский                                         │" >&2
    echo "  │  2) English                                         │" >&2
    echo "  └─────────────────────────────────────────────────────┘" >&2
    echo -n "  ▸ " >&2

    # Читаем ровно так же, как confirm() в common.sh — обычным read со
    # stdin. Захват /dev/tty здесь был бы соблазнителен (сценарий
    # `curl … | bash`), но он делает функцию непроверяемой и отбирает ввод
    # у вызывающего. Случай «скрипт пришёл по трубе» решает install.sh,
    # перезапуская godns.sh файлом.
    local choice=""
    read -r choice || choice=""

    case "$choice" in
        1|ru|RU|Ru|рус|Русский|русский) printf '%s\n' "ru" ;;
        2|en|EN|En|eng|English|english) printf '%s\n' "en" ;;
        *)                              printf '%s\n' "$GODNS_LANG_DEFAULT" ;;
    esac
    return 0
}

# ── Пункт меню «Язык» ───────────────────────────────────────────────────
# Вызывать через run_action. Всегда возвращает 0: смена языка — не та
# операция, из-за которой стоит ронять меню.
language_menu() {
    echo "" >&2
    echo "  $(t i18n_lang_menu_title)" >&2
    echo "  $(tf i18n_lang_current "$(get_language)")" >&2
    echo "" >&2
    echo "  1) $(t i18n_lang_russian)" >&2
    echo "  2) $(t i18n_lang_english)" >&2
    echo -n "  ▸ " >&2

    local choice=""
    read -r choice || choice=""

    local lang=""
    case "$choice" in
        1|ru|RU|Ru|rU) lang="ru" ;;
        2|en|EN|En|eN) lang="en" ;;
        ''|0)    return 0 ;;
        *)
            # ГРАБЛИ: неверный ввод молча выбрасывал в главное меню —
            # единственное меню в программе, которое так делает. Человек
            # решал, что язык сменился, и шёл искать, почему не сменился.
            echo "  $(t invalid_choice)" >&2
            return 0
            ;;
    esac

    load_language "$lang" || true
    save_language "$lang" || true
    echo "  $(tf i18n_lang_saved "$lang")" >&2
    return 0
}
