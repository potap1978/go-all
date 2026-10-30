#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — structural guards over the whole repository.
#
# These do not test behaviour; they test SHAPE. Each one covers a class of bug
# that is invisible in review, silent at runtime, and expensive in production:
# a bare call in a menu branch that closes the operator's terminal, a log line
# on stdout that poisons a $( ), a token on a curl command line that any local
# user can read out of /proc.
#
# Runs anywhere: bash 3.2+, grep, awk, sed. No root, no network.
# Prints one PASS/FAIL line per guard; exits non-zero if any guard failed.
#
# Usage: ./test_structure.sh [repo_root]

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

GUARD_PASS=0
GUARD_FAIL=0
WARNINGS=0

WORK=$(mktemp -d "${TMPDIR:-/tmp}/godns-struct.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT

pass() { GUARD_PASS=$((GUARD_PASS+1)); printf "  \033[0;32mPASS\033[0m  %s\n" "$1"; }
fail() {
    GUARD_FAIL=$((GUARD_FAIL+1))
    printf "  \033[0;31mFAIL\033[0m  %s\n" "$1"
    if [ -n "${2:-}" ]; then printf '%s\n' "$2" | sed 's/^/          /'; fi
}
warn() {
    WARNINGS=$((WARNINGS+1))
    printf "  \033[1;33mWARN\033[0m  %s\n" "$1"
    if [ -n "${2:-}" ]; then printf '%s\n' "$2" | sed 's/^/          /'; fi
}
note() { printf "        \033[2m%s\033[0m\n" "$1"; }

# ── Адрес DoT не печатается без проверки dot_enabled ───────────────────
# Эта защита уже дважды исчезала при переписывании соседнего кода, и оба
# раза оператор, выключивший DoT, видел живой адрес несуществующей службы.
# Ловим структурно: каждая строка, печатающая адрес DoT, обязана иметь
# проверку dot_enabled в пределах пяти строк выше.
bad_dot=0
dot_seen=0
while IFS= read -r hit; do
    dot_seen=$((dot_seen + 1))
    f="${hit%%:*}"; rest="${hit#*:}"; n="${rest%%:*}"
    from=$(( n > 6 ? n - 6 : 1 ))
    if ! sed -n "${from},${n}p" "$f" 2>/dev/null | grep -q 'dot_enabled'; then
        bad_dot=$((bad_dot + 1))
        printf "        %s:%s\n" "$f" "$n"
    fi
done < <(grep -rn 'dash_dot\|eng_status_dot' "$ROOT"/godns.sh "$ROOT"/lib/engines/*.sh 2>/dev/null \
         | grep -vE ':[0-9]+:[[:space:]]*#' | grep -v dash_dot_off)
if [ "$dot_seen" -eq 0 ]; then
    fail "не нашёл ни одной печати адреса DoT — шаблон поиска сломан"
elif [ "$bad_dot" -eq 0 ]; then
    pass "адрес DoT печатается только при dot_enabled=yes (осмотрено мест: ${dot_seen})"
else
    fail "адрес DoT печатается без проверки dot_enabled ($bad_dot мест)"
fi

# ── Ничего с чужими именами ────────────────────────────────────────────
# Требование заказчика, повторённое трижды: goDNS не должен мешать соседям.
# Держать это на внимательности нельзя — проверяем структурно.
#
# Ловим попытки создать или изменить что-либо с чужим префиксом: юнит,
# файл nginx, каталог состояния. Чтение чужих конфигов разрешено (мы
# ищем сертификат и порты соседей), запись — нет.
foreign=0
foreign_files=$(ls -1 "$ROOT"/godns.sh "$ROOT"/install.sh "$ROOT"/lib/*.sh \
                      "$ROOT"/lib/engines/*.sh 2>/dev/null | wc -l | tr -d ' ')
while IFS= read -r hit; do
    foreign=$((foreign + 1))
    printf "        %s\n" "$hit"
done < <(grep -rnE '(>|>>|tee|cp|mv|rm|install|chmod|chown|mkdir|systemctl (enable|disable|start|stop|restart))[^|]*\b(govless|gotelegram|gocdn|x-ui|telemt)\b' \
         "$ROOT"/godns.sh "$ROOT"/install.sh "$ROOT"/lib/*.sh "$ROOT"/lib/engines/*.sh 2>/dev/null \
         | grep -vE ':[0-9]+:[[:space:]]*#' \
         | grep -vE 'GOVLESS_CONFIG_PATH|GOTELEGRAM_CONFIG_PATH|XUI_DB_PATH|TELEMT_CONFIG_PATH' )
if [ "$foreign_files" -lt 10 ]; then
    fail "проверке чужих имён нечего смотреть (файлов: ${foreign_files})"
elif [ "$foreign" -eq 0 ]; then
    pass "ничего не пишется под чужими именами (осмотрено файлов: ${foreign_files})"
else
    fail "запись под чужим именем ($foreign мест)"
fi

# Свои имена — строго с префиксом godns-. Иначе завтра совпадём с соседом.
badname=0
sysnames=0
while IFS= read -r hit; do
    sysnames=$((sysnames + 1))
    case "$hit" in *godns*) ;; *) badname=$((badname + 1)); printf "        %s\n" "$hit" ;; esac
done < <(sed 's/\.service\.d/.DROPIN/g' \
             "$ROOT"/godns.sh "$ROOT"/lib/*.sh "$ROOT"/lib/engines/*.sh 2>/dev/null \
         | grep -hoE '/etc/systemd/system/[a-z0-9._-]+\.(service|timer|path)|/etc/nginx/conf\.d/[a-z0-9._-]+\.conf|/etc/logrotate\.d/[a-z0-9._-]+' \
         | sort -u)

# Drop-in — единственный законный способ тронуть чужой юнит: каталог
# `<юнит>.service.d/` аддитивен, systemd читает из него всё, и соседские
# файлы остаются на месте. Но САМ файл в нём обязан быть нашим по имени,
# иначе мы перезапишем чужой drop-in с тем же номером.
dropin=0
dropin_seen=0
while IFS= read -r f; do
    dropin_seen=$((dropin_seen + 1))
    case "$f" in *godns*) ;; *) dropin=$((dropin + 1)); printf "        %s\n" "$f" ;; esac
done < <(grep -rhoE '\.service\.d/[A-Za-z0-9._-]+\.conf|DROPIN_FILE="[^"]*"' \
         "$ROOT"/godns.sh "$ROOT"/lib/*.sh "$ROOT"/lib/engines/*.sh 2>/dev/null | sort -u)
if [ "$dropin_seen" -eq 0 ]; then
    fail "не нашёл ни одного drop-in — шаблон поиска сломан"
elif [ "$dropin" -eq 0 ]; then
    pass "drop-in в чужих юнитах назван по-нашему (осмотрено: ${dropin_seen})"
else
    fail "drop-in без префикса godns ($dropin)"
fi
if [ "$sysnames" -eq 0 ]; then
    fail "не нашёл ни одного пути системного файла — шаблон поиска сломан"
elif [ "$badname" -eq 0 ]; then
    pass "все ${sysnames} создаваемых файлов систем с префиксом godns"
else
    fail "файл системы без префикса godns ($badname)"
fi

# ── В каталоге нет провайдеров с шаблоном в адресе ─────────────────────
# ГРАБЛИ, из-за которых механизм и удалён: NextDNS требует ID профиля, и
# его адрес в таблице был шаблоном «{id}.dns.nextdns.io». Без ID апстрим
# не собирался, в списке замера показывался как «— ошибка», а весь
# механизм «спросить профиль» жил ради одной строки. Заказчик решил такой
# провайдер не поддерживать.
#
# Инвариант теперь простой: НИ ОДНА строка каталога не содержит шаблона.
# Строка с «{...}» доехала бы до конфига dnsdist и уронила службу
# «недопустимым именем сертификата».
tmpl_checked=0
: > "${WORK}/tmpl.txt"
while IFS= read -r row; do
    case "$row" in ''|'#'*) continue ;; esac
    case "$row" in *'|'*) ;; *) continue ;; esac
    tmpl_checked=$((tmpl_checked + 1))
    case "$row" in
        *'{'*'}'*) printf '    %s\n' "${row%%|*}" >> "${WORK}/tmpl.txt" ;;
    esac
# Границы — настоящие: heredoc PROV_TABLE_EOF. Первая версия искала
# несуществующий маркер, sed возвращал 0 с пустым выводом, запасной grep
# из-за этого не запускался вовсе, и в цикл попадал не тот текст.
done < <(sed -n "/<<'PROV_TABLE_EOF'/,/^PROV_TABLE_EOF\$/p" \
             "$ROOT/lib/providers.sh" 2>/dev/null \
         | grep -E '^[a-z0-9][a-z0-9-]*\|')
if [ "$tmpl_checked" -eq 0 ]; then
    fail "проверка каталога не осмотрела ни одной записи"
elif [ -s "${WORK}/tmpl.txt" ]; then
    fail "в каталоге запись с незамещаемым шаблоном" "$(cat "${WORK}/tmpl.txt")"
else
    pass "в каталоге нет шаблонов (осмотрено записей: ${tmpl_checked})"
fi

# ── Секретов в дереве нет ──────────────────────────────────────────────
# Правило проекта: токен, попавший в файл, СКОМПРОМЕТИРОВАН — его отзывают
# у выдавшего, а не вычёркивают строкой, потому что он остаётся в истории
# git навсегда. Проверять это глазами бесполезно: секрет попадает не в код,
# а в фикстуру теста, куда никто не смотрит. Именно так настоящий токен
# бота и оказался в test_bot.sh.
#
# Заведомо поддельные значения обязаны себя называть: fake / example /
# placeholder внутри секрета. Это дешевле любого списка исключений —
# исключение по имени файла со временем накрывает и настоящие утечки.
secrets_found=0
while IFS= read -r hit; do
    case "$hit" in
        *[Ff]ake*|*FAKE*|*[Ee]xample*|*EXAMPLE*|*[Pp]laceholder*|*PLACEHOLDER*|*XXXX*|*'<'*'>'*) continue ;;
    esac
    secrets_found=$((secrets_found + 1))
    # Печатаем МЕСТО, но не значение: вывод тестов уходит в логи и в чаты.
    printf "        %s\n" "${hit%%:*}:${hit#*:}" | cut -c1-60
done < <(grep -rnE '[0-9]{6,12}:[A-Za-z0-9_-]{30,}|github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
         "$ROOT" --include='*.sh' --include='*.py' --include='*.tsv' \
         --include='*.md' --include='*.json' --include='*.conf' 2>/dev/null \
         | grep -v '/test_structure.sh:')
sec_files=$(find "$ROOT" -type f \( -name '*.sh' -o -name '*.py' -o -name '*.tsv' \
                -o -name '*.md' -o -name '*.json' -o -name '*.conf' \) \
            -not -path '*/.git/*' 2>/dev/null | wc -l | tr -d ' ')
if [ "$sec_files" -lt 20 ]; then
    fail "проверке секретов нечего смотреть (файлов: ${sec_files})"
elif [ "$secrets_found" -eq 0 ]; then
    pass "секретов в дереве нет (осмотрено файлов: ${sec_files})"
else
    fail "похоже на секрет в репозитории ($secrets_found)"
fi

# ── Тесты не трогают живую систему ─────────────────────────────────────
# ГРАБЛИ, стоившие живого бота на рабочем сервере. Песочница в тестах
# закрывала ПУТИ (GODNS_DIR, GODNS_BOT_UNIT_DIR), но не действия над
# системой: godns_bot_remove и webui_remove зовут настоящие
# `systemctl disable --now` и `userdel`. Прогон набора на сервере с
# установленным goDNS остановил работающего бота и удалил его
# пользователя — в auth.log осталось `userdel 'godns-bot'`, а юнит после
# этого падал с 217/USER и не поднимался.
#
# Требование: каждый тест, поднимающий lib/bot.sh или lib/webui.sh,
# обязан объявить заглушки системных команд ДО подключения библиотеки.
#
# ВТОРЫЕ ГРАБЛИ, уже в самой этой проверке: первый её вариант искал
# `$SCRIPT_DIR` без фигурных скобок, а в тестах написано `${SCRIPT_DIR}`.
# Цикл не находил ни одного файла, не проверял ничего и бодро зеленел.
# Поэтому здесь же — счётчик осмотренных файлов: проверка, которой нечего
# смотреть, обязана падать, а не молчать.
sysstub=0
syschecked=0
for tf in "$ROOT"/test_*.sh; do
    # `source` и `.` — одно и то же. Шаблон знал только форму `. "…"`, и
    # новый тест, написанный через `source "${REPO}/lib/bot.sh"`, для стража
    # не существовал: ровно тот путь, которым живого бота уже сносили.
    # Путь к файлу тоже не фиксируем жёстко — важна библиотека, не префикс.
    src_line=$(grep -nE '^[[:space:]]*(source|[.])[[:space:]]+.*/lib/(bot|webui)[.]sh' "$tf" 2>/dev/null \
               | head -1 | cut -d: -f1)
    [ -n "$src_line" ] || continue
    syschecked=$((syschecked + 1))
    for cmd in systemctl userdel useradd; do
        stub_line=$(grep -nE "^${cmd}\(\)" "$tf" | head -1 | cut -d: -f1)
        if [ -z "$stub_line" ] || [ "$stub_line" -gt "$src_line" ]; then
            sysstub=$((sysstub + 1))
            printf "        %s: нет заглушки %s до подключения библиотеки\n" \
                "$(basename "$tf")" "$cmd"
        fi
    done
done
if [ "$syschecked" -eq 0 ]; then
    fail "проверке заглушек нечего смотреть — шаблон поиска сломан"
elif [ "$sysstub" -eq 0 ]; then
    pass "тесты глушат системные команды до подключения библиотек ($syschecked файл(ов))"
else
    fail "тест может тронуть живую систему ($sysstub)"
fi

# ── Адрес панели собирается в ОДНОМ месте ──────────────────────────────
# ГРАБЛИ, поймано живым нажатием кнопки в Telegram: godns-bot/actions.sh
# собирал адрес мини-приложения сам и терял ПОРТ. Получалось
# https://<домен>/<путь>/, то есть 443, а там dnsdist с DoH — он отвечал
# 404 «there is no endpoint configured for this path», и кнопка
# «Мини-приложение» открывала эту строку вместо панели.
#
# Правильная сборка живёт в lib/webui.sh (webui_url). Вторая копия любой
# сборки адреса рано или поздно разойдётся с первой — как разошлись
# помощники межсетевого экрана до того, как их свели вместе.
urlbuild=0
urlseen=0
while IFS= read -r hit; do
    urlseen=$((urlseen + 1))
    case "$hit" in */lib/webui.sh:*) continue ;; esac
    urlbuild=$((urlbuild + 1))
    printf "        %s\n" "$hit"
done < <(grep -rn 'config_get webui_path' \
         "$ROOT"/godns.sh "$ROOT"/lib/*.sh "$ROOT"/godns-bot/*.sh "$ROOT"/godns-web/*.sh 2>/dev/null)
if [ "$urlseen" -eq 0 ]; then
    fail "не нашёл ни одного чтения webui_path — шаблон поиска сломан"
elif [ "$urlbuild" -eq 0 ]; then
    pass "адрес панели собирает только lib/webui.sh (осмотрено мест: ${urlseen})"
else
    fail "адрес панели собирается ещё где-то ($urlbuild)"
fi

# ── Любой путь удаления снимает и бота, и панель ───────────────────────
# ГРАБЛИ: удаление из Telegram снимало движок, но не ПАНЕЛЬ — её юнит
# оставался включённым, а следующий `rm -rf /opt/godns` сносил его
# ExecStart. Получался юнит, падающий вечно, и открытый порт в
# межсетевом экране. Оператор при этом читал «удалено».
#
# Путей удаления два: из SSH-меню (uninstall_godns) и из бота
# (bot_act_uninstall). Оба обязаны звать обе функции снятия.
undel=0
undel_checked=0
for pair in "$ROOT/godns.sh:uninstall_godns" "$ROOT/godns-bot/actions.sh:bot_act_uninstall"; do
    file="${pair%%:*}"; fn="${pair##*:}"
    body=$(sed -n "/^${fn}()/,/^}/p" "$file" 2>/dev/null)
    [ -n "$body" ] || { undel=$((undel + 1)); printf "        %s: функции %s нет\n" "$(basename "$file")" "$fn"; continue; }
    undel_checked=$((undel_checked + 1))
    for need in godns_bot_remove webui_remove; do
        case "$body" in
            *"$need"*) ;;
            *) undel=$((undel + 1))
               printf "        %s: %s не зовёт %s\n" "$(basename "$file")" "$fn" "$need" ;;
        esac
    done
done
if [ "$undel_checked" -eq 0 ]; then
    fail "проверке удаления нечего смотреть — имена функций разъехались"
elif [ "$undel" -eq 0 ]; then
    pass "оба пути удаления снимают бота и панель ($undel_checked)"
else
    fail "путь удаления оставляет надстройку ($undel)"
fi

# ── Проверка снаружи смотрит на DoH ────────────────────────────────────
# ГРАБЛИ: «Доступность снаружи» проверяла ТОЛЬКО порт DoT — а он выключен
# по умолчанию у всех новых установок. Оператор с исправным DoH получал
# вердикт «порт снаружи закрыт» и шёл искать несуществующую поломку.
# DoH — это адрес, которым пользуются клиенты; не проверять его нельзя.
ext=$(sed -n '/^st_chk_external_reach()/,/^}/p' "$ROOT/lib/selftest.sh" 2>/dev/null)
if [ -z "$ext" ]; then
    fail "функции st_chk_external_reach нет — имя разъехалось"
elif ! printf '%s' "$ext" | grep -qE 'st_check_host_net[^|]*\$ST_DOH_PORT|st_check_host_net[^|]*\$\{ST_DOH_PORT\}'; then
    # Именно ВЫЗОВ с портом DoH, а не упоминание строки: первая версия
    # стража ловила `st_note "DoH :${ST_DOH_PORT}"` и зеленела, даже когда
    # проверялся по-прежнему только DoT.
    fail "проверка снаружи не ЗОВЁТ st_check_host_net с портом DoH"
elif ! printf '%s' "$ext" | grep -q 'selftest_dot_on'; then
    fail "проверка снаружи не спрашивает, включён ли DoT"
else
    pass "проверка снаружи смотрит на DoH и учитывает состояние DoT"
fi

# ── The file set under guard ────────────────────────────────────────────
find "$ROOT" -type f -name '*.sh' \
     -not -path '*/.git/*' \
     -not -path '*/.i18n-keys/*' \
     -not -path '*/node_modules/*' \
     | sort > "${WORK}/all.txt"

# The menu-dispatch and secret guards are about the INSTALLER. The test
# suites legitimately do what the installer must not: call helpers bare (they
# do not run under set -e) and name undefined keys on purpose.
grep -v '/test_[^/]*\.sh$' "${WORK}/all.txt" > "${WORK}/prod.txt"
touch "${WORK}/prod.txt"

ALL=()
while IFS= read -r _f; do [ -n "$_f" ] && ALL+=("$_f"); done < "${WORK}/all.txt"
PROD=()
while IFS= read -r _f; do [ -n "$_f" ] && PROD+=("$_f"); done < "${WORK}/prod.txt"

FILE_COUNT=${#ALL[@]}
printf "\n\033[1mgoDNS structural guards\033[0m — %s shell file(s) under %s\n\n" \
    "$FILE_COUNT" "$ROOT"

if [ "$FILE_COUNT" -eq 0 ]; then
    fail "no *.sh files found — is the repo root right?" "$ROOT"
    printf "\n  \033[0;31m✗ 1 guard failed\033[0m\n\n"
    exit 1
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 1 — every .sh parses
# ═══════════════════════════════════════════════════════════════════════
: > "${WORK}/g1.txt"
for f in "${ALL[@]}"; do
    bash -n "$f" 2>>"${WORK}/g1.txt" || echo "  ^ in $f" >> "${WORK}/g1.txt"
done
if [ -s "${WORK}/g1.txt" ]; then
    fail "bash -n: at least one file does not parse" "$(cat "${WORK}/g1.txt")"
else
    pass "bash -n — all ${FILE_COUNT} file(s) parse"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 2 — menu `case` branches go through run_action
#
# The script runs under `set -euo pipefail`. A bare call in a case branch
# that returns non-zero — no certificate, DNS not propagated, dnsdist not
# installed, all ROUTINE outcomes — kills the entire script: the operator's
# terminal just closes, with no message at all.
#
# Method: find the DISPATCH TABLES — a `case … esac` where at least one
# branch already goes through run_action — and flag every sibling branch
# that calls a repo function bare. Inconsistency inside one dispatch table
# is exactly the bug shape: someone added menu item 8 and forgot the wrapper.
#
# Scoping it this way, rather than to every `case` in the tree, is what makes
# the guard usable. Ordinary `case` statements deep inside a helper are
# already covered — that helper was itself reached through run_action, and
# `f || true` disables errexit for the WHOLE call tree beneath it.
#
# Functions that cannot return non-zero (the log family, print_*, t/tf) are
# exempt, otherwise every `*) log_warning …` branch would be noise.
# ═══════════════════════════════════════════════════════════════════════
# _back только ставит флаг «вышли по 0) Назад» и всегда возвращает 0 —
# ронять ему нечем, обёртка была бы шумом в каждой ветке выхода.
SAFE_FNS="log_info log_success log_warning log_error log_dim log_step print_header print_rule t tf _back"

# Опись строим по БОЕВЫМ файлам, а не по всем. Тесты глушат системные
# команды одноимёнными функциями (`sleep() { :; }`), и с ними в описи
# страж принимал `sleep 1` в ветке case за вызов функции проекта —
# ложное срабатывание, которого в проде нет и быть не может.
grep -hE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\(\)' "${PROD[@]}" 2>/dev/null \
  | sed -E 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*\(\).*/\1/' \
  | sort -u > "${WORK}/funcs.txt"
FUNC_COUNT=$(wc -l < "${WORK}/funcs.txt" | tr -d ' ')

cat > "${WORK}/case.awk" <<'AWK'
BEGIN {
    n = split(SAFE, s, " ")
    for (i = 1; i <= n; i++) if (s[i] != "") safe[s[i]] = 1
}
# The function inventory. Matched by NAME, not by FNR==NR: an empty
# inventory file would make that trick swallow the first real source line.
FILENAME == FUNCFILE { fn[$0] = 1; next }

FNR == 1 { depth = 0; nbuf = 0; dispatch = 0; curfn = "" }
{
    line = $0

    # Имя объемлющей функции. Нужно, потому что признак «в этом case уже
    # есть хоть один run_action» слеп ровно к худшей форме бага: подменю,
    # написанное БЕЗ обёртки целиком, диспетчером не считалось и не
    # проверялось вовсе. Меню goDNS узнаётся по имени: main_menu и
    # submenu_*. Такой case — диспетчер по определению, а не по содержимому.
    if (line ~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\(\)/) {
        curfn = line
        sub(/^[[:space:]]*/, "", curfn)
        sub(/[[:space:]]*\(\).*/, "", curfn)
    }

    if (line ~ /(^|[[:space:];(])case[[:space:]].*[[:space:]]in[[:space:]]*(#.*)?$/) {
        depth++
        if (depth == 1) {
            nbuf = 0
            dispatch = 0
            if (curfn == "main_menu" || curfn ~ /^submenu_/) {
                dispatch = 1
                print "DISPATCHER\t" curfn > MENUFILE
            }
        }
        next
    }
    if (line ~ /(^|[[:space:];])esac([[:space:];)]|$)/) {
        if (depth == 1 && dispatch) {
            for (i = 1; i <= nbuf; i++) print buf[i]
        }
        if (depth == 1) { nbuf = 0; dispatch = 0 }
        if (depth > 0) depth--
        next
    }
    if (depth <= 0) next

    s2 = line
    sub(/^[[:space:]]+/, "", s2)
    # A branch label — `1)`, `*)`, `lite|full)` — never contains "(".
    if (s2 ~ /^[^()]*\)/) sub(/^[^()]*\)[[:space:]]*/, "", s2)

    split(s2, a, /[[:space:];&|]+/)
    cmd = a[1]
    if (cmd == "") next
    if (cmd == "run_action") { dispatch = 1; next }
    # `f || { … }`, `f && g`, `if f; then` — errexit cannot bite in a
    # conditional context, and neither can it below f.
    if (line ~ /(\|\||&&)/) next
    if (cmd ~ /^(if|while|until|elif|then|else|for|case|!|\{|\()$/) next
    if (cmd in fn && !(cmd in safe)) buf[++nbuf] = FILENAME ":" FNR ":" line
}
AWK

: > "${WORK}/g2.txt"
if [ ${#PROD[@]} -gt 0 ]; then
    awk -v SAFE="$SAFE_FNS" -v FUNCFILE="${WORK}/funcs.txt" \
        -v MENUFILE="${WORK}/g2menus.txt" \
        -f "${WORK}/case.awk" "${WORK}/funcs.txt" "${PROD[@]}" \
        > "${WORK}/g2.txt" 2>/dev/null
fi
# Счётчик осмотренного. Без него достаточно переименовать подменю, чтобы
# страж вечно зеленел, не проверив ни одного диспетчера, — тот самый
# `${SCRIPT_DIR}`-прецедент из CLAUDE.md.
MENU_COUNT=0
[ -f "${WORK}/g2menus.txt" ] && MENU_COUNT=$(sort -u "${WORK}/g2menus.txt" | wc -l | tr -d ' ')
if [ -s "${WORK}/g2.txt" ]; then
    fail "bare call in a case branch — wrap it: run_action <fn>" "$(cat "${WORK}/g2.txt")"
elif [ "$MENU_COUNT" -lt 5 ]; then
    fail "menu dispatchers not found (${MENU_COUNT}) — the guard checked nothing" \
         "expected main_menu plus the five submenu_* sections"
else
    pass "case branches — every call goes through run_action (${MENU_COUNT} menu dispatcher(s), ${FUNC_COUNT} repo function(s) known)"
    note "test_*.sh excluded: they do not run under set -e"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 3 — log_* must write to stderr
#
# A log line on stdout is captured by every `x=$(fn)` in the codebase and
# silently corrupts the value. It surfaces much later as an unparsable port
# number, or a domain with a checkmark glued to the front.
# ═══════════════════════════════════════════════════════════════════════
cat > "${WORK}/log.awk" <<'AWK'
FNR == 1 { inlog = 0 }
{
    if (inlog == 0 && $0 ~ /^[[:space:]]*log_[A-Za-z0-9_]*\(\)/) inlog = 1
    if (inlog) {
        if ($0 ~ /(echo|printf)[[:space:]]/ && $0 !~ />&2/ && $0 !~ /^[[:space:]]*#/)
            print FILENAME ":" FNR ":" $0
        if ($0 ~ /\}[[:space:]]*$/) inlog = 0
    }
}
AWK
awk -f "${WORK}/log.awk" "${ALL[@]}" > "${WORK}/g3.txt" 2>/dev/null
if [ -s "${WORK}/g3.txt" ]; then
    fail "a log_* function writes to stdout (missing >&2)" "$(cat "${WORK}/g3.txt")"
else
    pass "log_* — every definition redirects to stderr"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 4 — capturing a function that legitimately returns 1
#
# `score=$(dns_verify_cname_public …)` under `set -e` kills the script
# exactly when NO resolver confirmed the record — i.e. in the commonest
# case. Write `x=$(…) || true` and supply a default.
#
# A targeted list, not "everything that can fail": the general rule would
# flag `f=$(godns_mktemp)`, where aborting is precisely what we want.
# Add names here as modules land.
# ═══════════════════════════════════════════════════════════════════════
CAPTURE_FAIL_FNS="first_free_port port_owner get_server_ip os_gate port_in_use"
# Whole families whose job is probing — non-zero is a result, not an error.
CAPTURE_WARN_PREFIXES="cert_ certs_ prov_ bench_ eng_ dns_ doh_ dot_ upstream_"

ASSIGN='^[[:space:]]*(local|export|declare|readonly)?[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=\"?\$\('

: > "${WORK}/g4.txt"
for fn in $CAPTURE_FAIL_FNS; do
    grep -nE "${ASSIGN}${fn}([[:space:]]|\))" "${ALL[@]}" 2>/dev/null \
        | grep -v '||' >> "${WORK}/g4.txt"
done
if [ -s "${WORK}/g4.txt" ]; then
    fail "captured a routinely-failing function without || true" "$(cat "${WORK}/g4.txt")"
else
    pass "no unguarded capture of a known-fallible function"
    note "watched: ${CAPTURE_FAIL_FNS}"
fi

: > "${WORK}/g4w.txt"
if [ ${#PROD[@]} -gt 0 ]; then
    for pfx in $CAPTURE_WARN_PREFIXES; do
        grep -nE "${ASSIGN}${pfx}[A-Za-z0-9_]*[[:space:]]" "${PROD[@]}" 2>/dev/null \
            | grep -v '||' >> "${WORK}/g4w.txt"
    done
    # A pipeline that ends in head/tail: the downstream tool closes the pipe,
    # the upstream one dies on SIGPIPE, and pipefail turns the whole
    # assignment non-zero.
    grep -nE "${ASSIGN}.*\|[[:space:]]*(head|tail)[[:space:]]" "${PROD[@]}" 2>/dev/null \
        | grep -v '||' >> "${WORK}/g4w.txt"
fi
if [ -s "${WORK}/g4w.txt" ]; then
    warn "capture with no || fallback — probe family, or a pipeline ending in head/tail" \
         "$(sort -u "${WORK}/g4w.txt")"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 5 — `tr … | head -c` is a SIGPIPE trap
#
# head closes the pipe as soon as it has its N bytes, tr dies on SIGPIPE,
# and under `set -o pipefail` the substitution returns non-zero. Use
# `cut -c1-N`, which drains its input instead.
# ═══════════════════════════════════════════════════════════════════════
# Comment lines are excluded — this trap is DOCUMENTED in several files
# (that is the point), and test_structure.sh names the pattern in its own
# messages. Only real code counts.
grep -v '/test_structure\.sh$' "${WORK}/all.txt" > "${WORK}/g5in.txt"
G5IN=()
while IFS= read -r _f; do [ -n "$_f" ] && G5IN+=("$_f"); done < "${WORK}/g5in.txt"
: > "${WORK}/g5.txt"
if [ ${#G5IN[@]} -gt 0 ]; then
    grep -nE '\|[[:space:]]*([A-Za-z_]+=[^[:space:]]+[[:space:]]+)*head[[:space:]]+-c' \
        "${G5IN[@]}" 2>/dev/null \
        | awk '{ r = $0; sub(/^[^:]*:[0-9]+:/, "", r); if (r !~ /^[[:space:]]*#/) print }' \
        > "${WORK}/g5.txt"
fi
if [ -s "${WORK}/g5.txt" ]; then
    fail "piping into 'head -c' — use 'cut -c1-N' instead" "$(cat "${WORK}/g5.txt")"
else
    pass "no '… | head -c' SIGPIPE trap"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 6 — no secrets on a curl command line
#
# /proc/<pid>/cmdline is world-readable. Anything passed as an argument is
# visible to every local user for the lifetime of the request. Pass secrets
# through a curl config file with mode 0600 (--config) or on stdin (-d @-).
# ═══════════════════════════════════════════════════════════════════════
: > "${WORK}/curl.txt"
if [ ${#PROD[@]} -gt 0 ]; then
    grep -nE '(^|[[:space:];({`$])curl([[:space:]]|$)' "${PROD[@]}" 2>/dev/null \
        > "${WORK}/curl.txt"
fi
: > "${WORK}/g6.txt"
if [ -s "${WORK}/curl.txt" ]; then
    grep -E '(^|[[:space:]])(-u|--user)[[:space:]]' "${WORK}/curl.txt" >> "${WORK}/g6.txt"
    grep -E '(^|[[:space:]])(-d|--data|--data-raw|--data-binary|--data-urlencode)[[:space:]]+[^@[:space:]]' \
        "${WORK}/curl.txt" >> "${WORK}/g6.txt"
    grep -iE '(-H|--header)[[:space:]]+.*(authorization|bearer|x-auth-token|x-token|api[-_]?key)' \
        "${WORK}/curl.txt" >> "${WORK}/g6.txt"
fi
if [ -s "${WORK}/g6.txt" ]; then
    fail "secret material on a curl command line (readable via /proc/<pid>/cmdline)" \
         "$(sort -u "${WORK}/g6.txt")"
else
    CURL_N=$(wc -l < "${WORK}/curl.txt" | tr -d ' ')
    pass "curl — no credentials passed as arguments (${CURL_N} curl line(s) checked)"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 7 — never `source` os-release or a credentials file
#
# os-release is attacker-controllable inside an image and would clobber our
# own variables under `set -u`; a neighbour's credentials file is a foreign
# format that has already changed shape twice in goVLESS alone. Parse both
# line by line.
# ═══════════════════════════════════════════════════════════════════════
grep -v '/test_structure\.sh$' "${WORK}/all.txt" > "${WORK}/g7in.txt"
G7IN=()
while IFS= read -r _f; do [ -n "$_f" ] && G7IN+=("$_f"); done < "${WORK}/g7in.txt"
: > "${WORK}/g7.txt"
if [ ${#G7IN[@]} -gt 0 ]; then
    grep -nE '(^|[[:space:];&|(])(source|\.)[[:space:]]+[^;|&#]*(os-release|[Cc]redentials)' \
        "${G7IN[@]}" 2>/dev/null > "${WORK}/g7.txt"
fi
if [ -s "${WORK}/g7.txt" ]; then
    fail "sourcing os-release or a credentials file — parse it line by line instead" \
         "$(cat "${WORK}/g7.txt")"
else
    pass "no 'source' of os-release or of any credentials file"
    note "test_structure.sh excluded — it necessarily contains the pattern itself"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 8 — every file carries the shebang and the project header
# ═══════════════════════════════════════════════════════════════════════
: > "${WORK}/g8.txt"
for f in "${ALL[@]}"; do
    first=$(head -n1 "$f")
    case "$first" in
        '#!/bin/bash'|'#!/usr/bin/env bash') ;;
        *) echo "${f}: line 1 is not a bash shebang: ${first}" >> "${WORK}/g8.txt" ;;
    esac
    if ! head -n4 "$f" | grep -q 'goDNS'; then
        echo "${f}: no goDNS header comment in the first 4 lines" >> "${WORK}/g8.txt"
    fi
done
if [ -s "${WORK}/g8.txt" ]; then
    fail "missing shebang or project header" "$(cat "${WORK}/g8.txt")"
else
    pass "every file starts with a bash shebang and the goDNS header"
fi

# ═══════════════════════════════════════════════════════════════════════
# GUARD 9 — the bash 4.0+ guard is where it must be
#
# On bash 3.2 every I18N[key] collapses into index 0 and the UI turns to
# mush without a single error. The guard belongs in lib/i18n.sh (where the
# associative array is) and in godns.sh (the entry point).
# ═══════════════════════════════════════════════════════════════════════
: > "${WORK}/g9.txt"
G9_CHECKED=0
for f in "${ROOT}/lib/i18n.sh" "${ROOT}/godns.sh"; do
    if [ ! -f "$f" ]; then
        note "not written yet, skipped: ${f#$ROOT/}"
        continue
    fi
    G9_CHECKED=$((G9_CHECKED+1))
    if ! grep -q 'BASH_VERSINFO' "$f"; then
        echo "${f}: no BASH_VERSINFO check" >> "${WORK}/g9.txt"
    fi
done
if [ -s "${WORK}/g9.txt" ]; then
    fail "the bash 4.0+ guard is missing" "$(cat "${WORK}/g9.txt")"
else
    pass "bash 4.0+ guard present in all ${G9_CHECKED} file(s) that need it"
fi

# ── Режимы установки не зависят от umask ───────────────────────────────
# ГРАБЛИ: install.sh писал конфиг curl с токеном под `umask 077` и НЕ
# возвращал umask обратно. Его наследовало всё, что создаётся дальше:
# каталог программы выходил 0700, а /usr/local/bin/godns — 0700 на
# чистой машине и 0755 там, где файл уже лежал, потому что `chmod +x`
# добавляет бит к тому, ЧТО УЖЕ ЕСТЬ: поверх 0600 даёт 0700, поверх
# 0644 — 0755. Одна и та же установка получалась разной, и понять по
# машине, что на ней стоит, было нельзя.
#
# Требование: в install.sh нет `chmod +x` (режим зависел бы от
# предыдущего состояния файла) и каждый `umask` заключён в подоболочку.
UMASK_CHECKED=0
: > "${WORK}/g10.txt"
inst="${ROOT}/install.sh"
if [ -f "$inst" ]; then
    UMASK_CHECKED=$((UMASK_CHECKED + 1))
    # `chmod +x` — режим-от-предыдущего-состояния. Нужен явный восьмеричный.
    if grep -nE '^[^#]*chmod[[:space:]]+\+x' "$inst" >/dev/null 2>&1; then
        {
            echo "install.sh: chmod +x — режим зависит от прежнего состояния файла:"
            grep -nE '^[^#]*chmod[[:space:]]+\+x' "$inst" | sed 's/^/    /'
        } >> "${WORK}/g10.txt"
    fi
    # Каждый umask обязан стоять внутри подоболочки: строка перед ним —
    # открывающая скобка. Иначе он течёт на весь остаток установщика.
    while IFS=: read -r ln _; do
        [ -n "$ln" ] || continue
        prev=$(sed -n "$((ln - 1))p" "$inst" | tr -d '[:space:]')
        if [ "$prev" != "(" ]; then
            echo "install.sh:${ln}: umask вне подоболочки — утечёт на весь установщик" \
                >> "${WORK}/g10.txt"
        fi
    done < <(grep -nE '(^|[[:space:];&|])umask[[:space:]]' "$inst" | grep -vE '^[0-9]+:[[:space:]]*#')
fi
if [ "$UMASK_CHECKED" -eq 0 ]; then
    fail "проверка режимов установки не осмотрела ни одного файла"
elif [ -s "${WORK}/g10.txt" ]; then
    fail "режим установки зависит от umask или прежнего состояния" "$(cat "${WORK}/g10.txt")"
else
    pass "режимы установки заданы явно (осмотрено файлов: ${UMASK_CHECKED})"
fi

# ── выход из меню всегда говорит, чем вернуться ────────────────────────
# ГРАБЛИ: установщик печатает «Команда: godns» и тут же делает exec в
# мастер — к концу установки строка уезжает на сотни строк вверх. Люди
# заканчивали настройку, выходили и не знали, чем открыть меню обратно.
# Выходов из main_menu три (пункт 0, Ctrl-D в приглашении, Ctrl-D на
# «нажмите Enter»), и подсказка обязана быть на каждом: забыть одну
# ветку — значит вернуть ровно ту же жалобу.
BYE_CHECKED=0
: > "${WORK}/g11.txt"
main_sh="${ROOT}/godns.sh"
if [ -f "$main_sh" ]; then
    # Тело main_menu: от заголовка функции до закрывающей скобки в первой
    # колонке. Внутри ищем каждый `exit 0` и требуем прощание на той же
    # строке или на предыдущей.
    body=$(sed -n '/^main_menu() {/,/^}/p' "$main_sh")
    if [ -z "$body" ]; then
        echo "godns.sh: не нашёл тело main_menu — проверка не состоялась" >> "${WORK}/g11.txt"
    else
        while IFS=: read -r ln text; do
            [ -n "$ln" ] || continue
            BYE_CHECKED=$((BYE_CHECKED + 1))
            prev=$(printf '%s\n' "$body" | sed -n "$((ln - 1))p")
            # `run_action _say_bye` — та же подсказка, просто через общую
            # обёртку: правило «ветка case не зовёт функцию голой» сильнее.
            case "${text}${prev}" in
                *_say_bye*) ;;
                *) echo "main_menu: выход без подсказки о команде: ${text}" >> "${WORK}/g11.txt" ;;
            esac
        # `exit` без аргумента выходит ровно так же, а страж искал только
        # литерал `exit 0` — мутация `0) exit ;;` проходила незамеченной.
        done < <(printf '%s\n' "$body" | grep -nE '(^|[[:space:];&|])exit([[:space:]]+[0-9]+)?[[:space:]]*(;|$)')
    fi
fi
if [ "$BYE_CHECKED" -eq 0 ]; then
    fail "проверка выхода из меню не осмотрела ни одного выхода"
elif [ -s "${WORK}/g11.txt" ]; then
    fail "выход из меню молчит о том, чем вернуться" "$(cat "${WORK}/g11.txt")"
else
    pass "все ${BYE_CHECKED} выхода(ов) из меню называют команду возврата"
fi

# ── жирность не гасится цветом ─────────────────────────────────────────
# ГРАБЛИ: RED/GREEN/CYAN/BLUE/MAGENTA объявлены как \033[0;NNm, и ведущий
# `0;` СБРАСЫВАЕТ атрибуты. Значит `${BOLD}${CYAN}` — это «жирный, а потом
# не жирный»: текст выходит просто цветным. Так молча теряли жирность
# шапка установщика и заголовки групп самопроверки — годами, потому что
# на глаз разница видна только рядом с настоящим жирным.
#
# Порядок обязан быть обратным: ${CYAN}${BOLD}. WHITE (1;37) и YELLOW
# (1;33) жирные сами — им порядок безразличен.
BOLDORDER_CHECKED=0
: > "${WORK}/g12.txt"
while IFS= read -r f; do
    BOLDORDER_CHECKED=$((BOLDORDER_CHECKED + 1))
    if grep -nE '\$\{BOLD\}\$\{(RED|GREEN|CYAN|BLUE|MAGENTA)\}' "$f" >/dev/null 2>&1; then
        grep -nE '\$\{BOLD\}\$\{(RED|GREEN|CYAN|BLUE|MAGENTA)\}' "$f" \
            | sed "s|^|$(basename "$f"):|" >> "${WORK}/g12.txt"
    fi
done < <(find "$ROOT" -name '*.sh' -not -path '*/.git/*' -not -name 'test_*' 2>/dev/null)
if [ "$BOLDORDER_CHECKED" -eq 0 ]; then
    fail "проверка порядка цвета не осмотрела ни одного файла"
elif [ -s "${WORK}/g12.txt" ]; then
    fail "цвет после \${BOLD} гасит жирность — менять порядок" "$(cat "${WORK}/g12.txt")"
else
    pass "жирность нигде не гасится цветом (осмотрено файлов: ${BOLDORDER_CHECKED})"
fi

# ── iOS-профиль DoH: без ServerAddresses ───────────────────────────────
# ГРАБЛИ, найденные на живом iPhone: поле ServerAddresses выглядит
# безобидной подсказкой, но в com.apple.dnsSettings.managed оно для
# DNSProtocol=TLS (адреса DoT-сервера). Для HTTPS сервер задаётся
# ИСКЛЮЧИТЕЛЬНО ServerURL, а лишнее поле iOS трактует по-своему: профиль
# ставится без единой ошибки, а резолвер не работает — и понять это по
# телефону нечем.
MC_CHECKED=0
: > "${WORK}/g13.txt"
mcf="${ROOT}/lib/selftest.sh"
if [ -f "$mcf" ]; then
    MC_CHECKED=1
    # Ищем в генераторе профиля (между DNSProtocol и записью файла).
    # ГРАБЛИ, стоившие нерабочего iPhone: iOS и macOS ИГНОРИРУЮТ порт в
    # ServerURL и всегда идут на <хост>:443. Профиль с https://домен:4443/…
    # ставится без ошибок, а система стучит на 443 — к соседу. Поэтому в
    # DoH-профиль порт попадать не должен вовсе: он строится только когда
    # DoH уже на 443, иначе генератор отдаёт DoT.
    # Диапазон — ВСЯ функция-генератор, а не только питоновская вставка.
    # ГРАБЛИ стража: он смотрел от GODNS_MC_PROTO= до MCPY, а адрес
    # собирается СТРОКОЙ ВЫШЕ (`url="https://${ST_DOMAIN}/${ST_DOH_PATH}"`)
    # и слова ServerURL не содержит. Порт можно было вернуть туда, и весь
    # набор оставался зелёным — то есть страж не закрывал те самые грабли,
    # ради которых написан.
    mc_body=$(sed -n '/^st_apple_profile_write()/,/^}/p' "$mcf" 2>/dev/null \
        | grep -vE '^[[:space:]]*#') || mc_body=""
    if [ -z "$mc_body" ]; then
        echo "lib/selftest.sh: генератор профиля st_apple_profile_write не найден" \
            >> "${WORK}/g13.txt"
    fi
    # 1) порт не попадает в адрес — ни в ServerURL, ни в переменную, из
    #    которой он собирается.
    if grep -qE '(ServerURL|url)[^#]*\$\{?ST_DOH_PORT' <<< "$mc_body"; then
        echo "lib/selftest.sh: порт в адресе профиля — iOS его игнорирует и идёт на 443" \
            >> "${WORK}/g13.txt"
    fi
    # 2) ServerAddresses в DoH-ветке. Страж назывался «без ServerAddresses»
    #    и сообщал об этом в тексте отказа, но самого поля НЕ ИСКАЛ. Для
    #    DNSProtocol=HTTPS сервер задаётся только ServerURL; лишнее поле
    #    ставится без ошибок и молча не работает.
    if grep -qE '"DNSProtocol":[[:space:]]*"HTTPS".*ServerAddresses' <<< "$mc_body"; then
        echo "lib/selftest.sh: ServerAddresses в DoH-профиле" >> "${WORK}/g13.txt"
    fi
    if awk '/"DNSProtocol":[[:space:]]*"HTTPS"/ { h = 1 }
            /"DNSProtocol":[[:space:]]*"TLS"/   { h = 0 }
            h && /ServerAddresses/              { found = 1 }
            END { exit(found ? 0 : 1) }' <<< "$mc_body"; then
        echo "lib/selftest.sh: ServerAddresses в ветке DoH" >> "${WORK}/g13.txt"
    fi
    # 3) DoH в профиль — только при doh_port=443.
    if ! grep -q 'ST_DOH_PORT" = "443"' "$mcf" 2>/dev/null; then
        echo "lib/selftest.sh: нет гейта «DoH в профиль только на 443»" \
            >> "${WORK}/g13.txt"
    fi
fi
if [ "$MC_CHECKED" -eq 0 ]; then
    fail "проверка iOS-профиля не осмотрела ни одного файла"
elif [ -s "${WORK}/g13.txt" ]; then
    fail "профиль Apple собран неверно" "$(cat "${WORK}/g13.txt")"
else
    pass "профиль Apple: DoH только ServerURL без порта, гейт 443 на месте"
fi

# ── замер: URL не в argv curl ───────────────────────────────────────────
# Страж 6 выше читает bash-строки `curl`, а сборка argv в lib/bench.py —
# python, и он её не видел: QA поймал полный DoH-URL (с путём, который у
# своего апстрима может быть чужим секретом) в /proc/<pid>/cmdline curl на
# время пробы. Требование: ни одна строка, добавляющая в argv, не содержит
# url/get_url — они уезжают конфигом через stdin.
BENCH_ARGV_CHECKED=0
: > "${WORK}/g17.txt"
bench_py="${ROOT}/lib/bench.py"
if [ -f "$bench_py" ]; then
    while IFS= read -r line; do
        BENCH_ARGV_CHECKED=$((BENCH_ARGV_CHECKED + 1))
        case "$line" in
            *'#'*) line="${line%%#*}" ;;
        esac
        if printf '%s' "$line" | grep -qE '(^|[^a-z_])(get_url|url)([^a-z_]|$)'; then
            echo "lib/bench.py: URL в argv curl: ${line}" >> "${WORK}/g17.txt"
        fi
    done < <(grep -E '^[[:space:]]*argv[[:space:]]*(\+=|=)' "$bench_py")
fi
if [ "$BENCH_ARGV_CHECKED" -eq 0 ]; then
    fail "проверка argv curl в bench.py не осмотрела ни одной строки"
elif [ -s "${WORK}/g17.txt" ]; then
    fail "bench.py кладёт URL в argv curl" "$(cat "${WORK}/g17.txt")"
else
    pass "bench.py: URL не в argv curl (осмотрено сборок argv: ${BENCH_ARGV_CHECKED})"
fi

# ── ветка по умолчанию: код и README говорят одно ──────────────────────
# ГРАБЛИ, замеченные только вопросом заказчика «у нас 2 ветки разве?»:
# install.sh просил `tarball/main`, а на GitHub такой ветки нет — она
# осталась локальной и отстала на 69 коммитов. Команда установки из README
# получала 404 и умирала сообщением про сеть и токен. Ни один тест этого
# не видел: README и код разъехались молча.
#
# Проверить существование ветки на удалённом отсюда нельзя (сеть, токен,
# закрытый репозиторий). Проверяем то, что проверяемо: умолчание в коде и
# ветка, названная в README, — одно и то же слово.
REF_CHECKED=0
: > "${WORK}/g16.txt"
inst_ref="${ROOT}/install.sh"
readme="${ROOT}/README.md"
if [ -f "$inst_ref" ] && [ -f "$readme" ]; then
    REF_CHECKED=1
    # Режем по «:-», а не по «:»: дефис входит в класс символов имени
    # ветки, и `sed 's/.*://'` оставлял «-dev».
    code_ref=$(grep -oE 'GODNS_REF:-[A-Za-z0-9._/-]+' "$inst_ref" | head -1 | sed 's/.*:-//')
    doc_ref=$(grep -oE 'GODNS_REF` \(по умолчанию `[A-Za-z0-9._/-]+`' "$readme" \
              | head -1 | sed 's/.*`\([^`]*\)`$/\1/')
    if [ -z "$code_ref" ]; then
        echo "install.sh: не нашёл умолчание GODNS_REF — шаблон поиска сломан" \
            >> "${WORK}/g16.txt"
    elif [ -z "$doc_ref" ]; then
        echo "README.md: не нашёл названную ветку — шаблон поиска сломан" \
            >> "${WORK}/g16.txt"
    elif [ "$code_ref" != "$doc_ref" ]; then
        echo "install.sh ставит «${code_ref}», README обещает «${doc_ref}»" \
            >> "${WORK}/g16.txt"
    fi
fi
if [ "$REF_CHECKED" -eq 0 ]; then
    fail "проверка ветки по умолчанию не осмотрела ни одного файла"
elif [ -s "${WORK}/g16.txt" ]; then
    fail "ветка по умолчанию в коде и в README разная" "$(cat "${WORK}/g16.txt")"
else
    pass "ветка по умолчанию одна и та же в install.sh и README (${code_ref})"
fi

# ── запасная версия панели не отстаёт от настоящей ─────────────────────
# ГРАБЛИ: VERSION_FALLBACK в server.py показывается, когда в config.json нет
# godns_version, и отстал на 27 выпусков — панель сообщала бы «0.5.3» про
# 0.32.1. Ни один прогон этого не видел: значение живое только на пути, куда
# тесты не заходят. Держим равным GODNS_VERSION.
VFB_CHECKED=0
: > "${WORK}/g17.txt"
common_sh="${ROOT}/lib/common.sh"
server_py="${ROOT}/godns-web/server.py"
if [ -f "$common_sh" ] && [ -f "$server_py" ]; then
    VFB_CHECKED=1
    real_ver=$(grep -oE '^GODNS_VERSION="[0-9A-Za-z._-]+"' "$common_sh" \
               | head -1 | sed 's/.*="//; s/"$//')
    web_ver=$(grep -oE '^VERSION_FALLBACK = "[0-9A-Za-z._-]+"' "$server_py" \
              | head -1 | sed 's/.*= "//; s/"$//')
    if [ -z "$real_ver" ]; then
        echo "lib/common.sh: не нашёл GODNS_VERSION — шаблон поиска сломан" >> "${WORK}/g17.txt"
    elif [ -z "$web_ver" ]; then
        echo "godns-web/server.py: не нашёл VERSION_FALLBACK — шаблон поиска сломан" >> "${WORK}/g17.txt"
    elif [ "$real_ver" != "$web_ver" ]; then
        echo "GODNS_VERSION=${real_ver}, а панель показала бы ${web_ver}" >> "${WORK}/g17.txt"
    fi
fi
if [ "$VFB_CHECKED" -eq 0 ]; then
    fail "проверка запасной версии панели не осмотрела ни одного файла"
elif [ -s "${WORK}/g17.txt" ]; then
    fail "запасная версия панели отстала от GODNS_VERSION" "$(cat "${WORK}/g17.txt")"
else
    pass "запасная версия панели совпадает с GODNS_VERSION (${real_ver})"
fi

# ── ключ консоли dnsdist не уходит на экран ────────────────────────────
# Ключ из setKey() — это полный доступ к консоли работающего резолвера.
# Панель и демон бота его вычищали (_scrub_console_key), а SSH-пункт
# «Показать конфигурацию» печатал конфиг как есть: ключ попадал в запись
# терминала, в скриншот для поддержки и просто на глаза соседу по столу.
# Требование: каждый путь, печатающий конфиг движка, чистит setKey().
CKEY_CHECKED=0
: > "${WORK}/g15.txt"
for f in "$ROOT"/lib/engines/*.sh "$ROOT"/godns-web/server.py; do
    [ -f "$f" ] || continue
    # Интересуют только файлы, которые ВООБЩЕ печатают конфиг наружу.
    grep -qE 'show_config|dnsdist\.conf|DNSDIST_CONF' "$f" 2>/dev/null || continue
    CKEY_CHECKED=$((CKEY_CHECKED + 1))
    if ! grep -qE 'setKey.*[*][*][*]' "$f" 2>/dev/null; then
        echo "$(basename "$f"): печатает конфиг, но нигде не маскирует setKey()" \
            >> "${WORK}/g15.txt"
    fi
done
if [ "$CKEY_CHECKED" -eq 0 ]; then
    fail "проверка ключа консоли не осмотрела ни одного файла"
elif [ -s "${WORK}/g15.txt" ]; then
    fail "ключ консоли dnsdist может уйти на экран" "$(cat "${WORK}/g15.txt")"
else
    pass "ключ консоли маскируется во всех ${CKEY_CHECKED} путях показа конфигурации"
fi

# ── у каждой инструкции есть путь назад ────────────────────────────────
# Мы даём человеку команды, меняющие его систему (netsh, opkg, RouterOS).
# Дать команду и не дать обратную — значит оставить его без выхода: он
# попробовал, не понравилось или не заработало, и не знает, как вернуть
# прежний DNS. Требование: каждая функция сниппетов печатает snip_undo.
UNDO_CHECKED=0
: > "${WORK}/g14.txt"
sfile="${ROOT}/lib/selftest.sh"
if [ -f "$sfile" ]; then
    # Список имён был жёстким, и новая функция сниппетов в него не
    # попадала: selftest_snip_windows, подключённая к «Всё сразу», прошла
    # мутацию без единого возражения (и selftest_snip_phone_qr всё это
    # время тоже была вне списка). Берём ВСЕ функции по имени.
    for fn in $(grep -oE '^selftest_snip_[a-z0-9_]+\(\)' "$sfile" 2>/dev/null \
                | sed 's/()//' | sort -u); do
        body=$(sed -n "/^${fn}()/,/^}/p" "$sfile" 2>/dev/null)
        [ -n "$body" ] || { echo "нет функции ${fn}" >> "${WORK}/g14.txt"; continue; }
        # Составитель («Всё сразу») сам инструкций не печатает, он зовёт
        # другие сниппеты — у каждого свой путь назад. Требовать отмену от
        # него значило бы дублировать её на экране четырежды.
        if [ "$(printf '%s' "$body" | grep -cE '^[[:space:]]*(run_action[[:space:]]+)?selftest_snip_[a-z0-9_]+([[:space:]]|$)')" -ge 2 ]; then
            continue
        fi
        UNDO_CHECKED=$((UNDO_CHECKED + 1))
        # Именно ЗАГОЛОВОК snip_undo, а не любой ключ, начинающийся с
        # него: первая версия засчитывала snip_undo_browsers и пропускала
        # мутацию, снявшую сам заголовок.
        printf '%s' "$body" | grep -qE '\$\(t snip_undo\)' \
            || echo "${fn}: даёт инструкции без пути назад (нет snip_undo)" >> "${WORK}/g14.txt"
    done
fi
if [ "$UNDO_CHECKED" -eq 0 ]; then
    fail "проверка «путь назад» не осмотрела ни одной функции"
elif [ -s "${WORK}/g14.txt" ]; then
    fail "инструкция без команды отмены" "$(cat "${WORK}/g14.txt")"
else
    pass "у всех ${UNDO_CHECKED} наборов инструкций есть путь назад"
fi

# ── переменная не используется раньше своего local ─────────────────────
# ГРАБЛИ, сломавшие первичную установку целиком: в certs_issue_interactive
# стояло `certs_lineage_exists "$domain"`, а `local domain` объявлялся на
# пятнадцать строк НИЖЕ. Под `set -u` это фатальная ошибка раскрытия — её
# НЕ гасит ни run_action, ни условный контекст: терминал оператора
# закрывался сразу после заголовка. `bash -n` такого не видит, тесты
# функцию не вызывали, и находка дожила до аудита.
#
# ВТОРЫЕ ГРАБЛИ, уже в самой этой проверке: сначала здесь стояло
# `/^\}/ { fn=""; next }` ПЕРЕД блоком проверки — `next` съедал строку, и
# проверка не выполнялась ни разу. Страж зеленел, не проверив ничего.
# ТРЕТЬИ: awk считал за использование и `$domain` внутри КОММЕНТАРИЯ —
# в том числе в комментарии, описывающем эти самые грабли.
LOCALS_CHECKED=0
: > "${WORK}/g15.txt"
while IFS= read -r f; do
    LOCALS_CHECKED=$((LOCALS_CHECKED + 1))
    awk -v FNAME="$(basename "$f")" '
        /^[a-z_]+\(\) \{/ { fn=$1; sub(/\(\).*/,"",fn); delete seen; delete declared; ln=0; next }
        /^\}/ {
            if (fn != "")
                for (v in declared)
                    if ((v in seen) && seen[v] < declared[v])
                        printf "%s: %s — $%s на строке %d, local на %d\n", FNAME, fn, v, seen[v], declared[v]
            fn=""; next
        }
        fn != "" {
            ln++
            if ($0 ~ /^[[:space:]]*#/) next        # комментарий — не код
            if ($0 ~ /^[[:space:]]*local[[:space:]]/) {
                line=$0; sub(/^[[:space:]]*local[[:space:]]+/,"",line)
                n=split(line, parts, /[[:space:]]+/)
                for (i=1; i<=n; i++) { v=parts[i]; sub(/=.*/,"",v)
                    # ТОЛЬКО первое объявление: `local spin_i` внутри цикла
                    # не отменяет `local sec=0` в начале функции, а без
                    # этого условия страж «переоткрывал» переменную и
                    # объявлял ложное использование-до-объявления.
                    if (v ~ /^[a-z_][a-z0-9_]*$/ && !(v in declared)) declared[v]=ln }
            }
            s=$0
            sub(/[[:space:]]#[^"'"'"']*$/, "", s)  # хвостовой комментарий
            while (match(s, /\$\{?[a-z_][a-z0-9_]*/)) {
                v=substr(s, RSTART, RLENGTH); gsub(/[${]/,"",v)
                if (!(v in seen)) seen[v]=ln
                s=substr(s, RSTART+RLENGTH)
            }
        }
    ' "$f" >> "${WORK}/g15.txt" 2>/dev/null || true
done < <(find "$ROOT/lib" "$ROOT" -maxdepth 1 -name '*.sh' -not -name 'test_*' 2>/dev/null)
if [ "$LOCALS_CHECKED" -eq 0 ]; then
    fail "проверка порядка local не осмотрела ни одного файла"
elif [ -s "${WORK}/g15.txt" ]; then
    fail "переменная используется раньше своего local (падение под set -u)" \
         "$(head -5 "${WORK}/g15.txt")"
else
    pass "нет обращений к переменной раньше её local (файлов: ${LOCALS_CHECKED})"
fi

# ── путь DoH своего апстрима не ездит в argv ───────────────────────────
# /proc/<pid>/cmdline читает любой локальный пользователь, а путь DoH чужого
# резолвера может быть чужим секретом (второй goDNS как апстрим). Поэтому на
# КАЖДОЙ границе процесса он идёт окружением GODNS_CUSTOM_PATH: мост панели
# и фасад бота читают его из окружения (четвёртый параметр lib-функции),
# сервер панели и бот передают через env=, а не позиционным аргументом.
# Четыре файла — четыре обязательных места. Страж считает осмотренные и
# краснеет, если какого-то нет вовсе: «вызовов не нашёл» — это не «чисто»,
# а «границу ещё не написали», и молчать об этом он не должен.
CUPATH_SEEN=0
: > "${WORK}/g17.txt"
: > "${WORK}/g17miss.txt"
for f in "$ROOT/godns-web/bridge.sh" "$ROOT/godns-bot/actions.sh"; do
    rel="${f#$ROOT/}"
    if [ ! -f "$f" ]; then echo "${rel}: файла нет" >> "${WORK}/g17miss.txt"; continue; fi
    hits=$(grep -nE 'webui_custom_(probe|add)[[:space:]]' "$f" 2>/dev/null \
           | grep -vE '^[0-9]+:[[:space:]]*#' || true)
    if [ -z "$hits" ]; then
        echo "${rel}: вызовов webui_custom_probe/webui_custom_add нет" >> "${WORK}/g17miss.txt"
        continue
    fi
    CUPATH_SEEN=$((CUPATH_SEEN + 1))
    printf '%s\n' "$hits" | grep -v 'GODNS_CUSTOM_PATH' | sed "s|^|${rel}:|" >> "${WORK}/g17.txt" || true
done
for f in "$ROOT/godns-web/server.py" "$ROOT/godns-bot/bot.py"; do
    rel="${f#$ROOT/}"
    if [ ! -f "$f" ]; then echo "${rel}: файла нет" >> "${WORK}/g17miss.txt"; continue; fi
    case "$f" in
        *server.py) callre='bridge[(]"custom_(probe|add)"' ;;
        *)          callre='rpc_action[(]"custom_(probe|add)"' ;;
    esac
    # Вызов может занимать несколько строк — копим до баланса скобок и
    # только потом ищем env=. Комментарии — не код.
    res=$(awk -v RE="$callre" '
        /^[[:space:]]*#/ { next }
        grab == 0 && $0 ~ RE { grab = 1; buf = ""; depth = 0 }
        grab {
            buf = buf $0 " "
            line = $0
            o = gsub(/\(/, "(", line); c = gsub(/\)/, ")", line)
            depth += o - c
            if (depth <= 0) {
                found++
                if (buf !~ /env=/) print "BAD " buf
                grab = 0
            }
        }
        END { print "FOUND " found + 0 }' "$f" 2>/dev/null)
    n=$(printf '%s\n' "$res" | awk '/^FOUND /{print $2}')
    printf '%s\n' "$res" | grep '^BAD ' | sed "s|^BAD |${rel}: без env=: |" >> "${WORK}/g17.txt" || true
    if [ "${n:-0}" -gt 0 ]; then
        CUPATH_SEEN=$((CUPATH_SEEN + 1))
    else
        echo "${rel}: вызовов custom_probe/custom_add нет" >> "${WORK}/g17miss.txt"
    fi
done
if [ "$CUPATH_SEEN" -lt 4 ]; then
    fail "путь DoH: осмотрено файлов ${CUPATH_SEEN} из 4 — граница процесса не написана" \
         "$(cat "${WORK}/g17miss.txt")"
elif [ -s "${WORK}/g17.txt" ]; then
    fail "путь DoH своего апстрима попадает в argv" "$(cat "${WORK}/g17.txt")"
else
    pass "путь DoH ездит только окружением GODNS_CUSTOM_PATH (осмотрено файлов: ${CUPATH_SEEN})"
fi

# ═══════════════════════════════════════════════════════════════════════
printf "\n────────────────────────────────────────────────────────────\n"
if [ "$GUARD_FAIL" -eq 0 ]; then
    printf "  \033[0;32m✓ all %d guards passed\033[0m" "$GUARD_PASS"
    if [ "$WARNINGS" -gt 0 ]; then printf " (%d warning(s))" "$WARNINGS"; fi
    printf "\n\n"
    exit 0
fi
printf "  \033[0;31m✗ %d guard(s) failed\033[0m, %d passed, %d warning(s)\n\n" \
    "$GUARD_FAIL" "$GUARD_PASS" "$WARNINGS"
exit 1
