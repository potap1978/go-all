#!/usr/bin/env bash
# goAmnezia pro — проверки, которым не нужен сервер (docs/CONTRACT.md §7).
#
#   bash -n            синтаксис всех .sh
#   статический анализ    shellcheck -S warning -x
#   python3 -m unittest        юнит-тесты tests/test_*.py
#   check_i18n_ssh.py          словари: дубли, паритет ru/en, все ключи на месте
#   check_actions.py           actions.json ↔ диспетчер ↔ MCP
#   check_bootstrap_files.py   список файлов bootstrap.sh ↔ репозиторий
#   check_core_read.py         места ядра, которые повторяет быстрый путь панели (lib/core_read.py), не менялись
#                              после сверки — иначе какие и дифф, и как сверить и обновить отпечаток
#
# Продукт пишется по спринтам, поэтому файлов, которых ещё нет (install.sh,
# модули других спринтов), не хватает не по ошибке. Такие проверки помечаются
# SKIP и не считаются провалом: иначе набор красный с первого дня и перестаёт
# что-либо значить. Отсутствие ИНСТРУМЕНТА (shellcheck, python3) тоже SKIP —
# но с громкой строкой, чтобы это не прошло незамеченным на CI.
#
# Параллельный прогон. Последовательно набор шёл 26 минут, из них двадцать — pty-мастер
# одним процессом. Теперь тяжёлые независимые части — shellcheck, юнит-тесты по модулям
# (pty-мастер — несколькими частями, tests/unit_shard.py), сверки check_*.py, selftest и
# сквозные наборы — идут одновременно, до GA_TEST_JOBS заданий сразу (по умолчанию —
# половина ядер + 1: при всех ядрах сразу мигали тесты со временем, TAIL-25), долгие первыми. Каждое задание — отдельный процесс в своей сессии, со своим
# TMPDIR и своим журналом. Отчёт печатается в прежнем порядке и прежними строками, раздел
# за разделом по мере готовности; у раздела — длительность, в конце — общее время.
# Журналы проваленных заданий остаются на диске, путь назван в отчёте.
# GA_TEST_JOBS=1 — прежний последовательный прогон: задания по одному, юнит-тесты одним
# discover.
#
# На macOS bash 5 лежит в /opt/homebrew/bin/bash; на сервере — /bin/bash.
# Запуск: bash tests/run_static.sh   (или BASH5=/opt/homebrew/bin/bash bash …)
# Сам раннер обходится возможностями bash 3.2 (/bin/bash на macOS): без wait -n,
# mapfile и ассоциативных массивов, пустые массивы не разворачиваются под set -u.
set -u
cd "$(dirname "$0")/.." || exit 2

BASH5="${BASH5:-bash}"
# Экспорт — для сквозных прогонов ниже: они запускают install.sh тем же
# интерпретатором, и без переменной на macOS взяли бы /bin/bash 3.2.
export BASH5
# Локаль — явно (TEST-FLAKE-11). На macOS при пустых LANG и LC_ALL bash из Homebrew берёт локаль у системы
# через libintl → CoreFoundation. Подоболочка $(…), которая сама меняет LANG/LC_ALL и потом возвращает их
# к пустым («LANG=C apt-get …» в $(…), «local LC_ALL=C» в функции), зовёт CoreFoundation уже после fork, и под
# нагрузкой та падает SIGSEGV (код 139): вывод подстановки есть, а код — ошибка, и проверки краснеют в разных
# местах. С непустой локалью libintl в систему не ходит. C.UTF-8 — как на сервере: символы UTF-8, точка в
# дробях, сортировка C. Заданную человеком локаль не трогаем; на Linux пустая локаль — просто C, её не меняем.
if [ -z "${LC_ALL:-}${LANG:-}" ] && [ "$(uname -s 2>/dev/null)" = Darwin ]; then
    export LANG=C.UTF-8
fi
SELF="$PWD/tests/run_static.sh"
fail=0
skip=0

# hdr <заголовок> [длительность]
hdr()  { if [ -n "${2:-}" ]; then printf '\n════ %s  (%s)\n' "$1" "$2"; else printf '\n════ %s\n' "$1"; fi; }
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }
skp()  { printf '  • SKIP: %s\n' "$1"; skip=$((skip+1)); }

# ut_skip_count <журнал> — сколько тестов пропущено. Число берём из итоговой
# строки самого unittest, а не считаем строки со словом «skipped»: счёт строк
# врал в обе стороны (см. подробности у раздела юнит-тестов ниже).
#
# sed -E, а не BRE с «\|»: альтернативы в базовых регулярках нет у BSD sed
# (macOS), и выражение там молча ничего не находило — самопроверка ниже поймала
# это на первом же прогоне.
ut_skip_count() { sed -n -E 's/^(OK|FAILED).*skipped=([0-9]+).*/\2/p' "$1" | tail -n 1; }
# ut_ran_count <журнал> — сколько тестов прогнано (строка «Ran N tests in …»).
ut_ran_count() { sed -n -E 's/^Ran ([0-9]+) tests? in .*/\1/p' "$1" | tail -n 1; }
# ut_skip_total / ut_ran_total <журнал>… — то же, сложенное по журналам всех частей.
ut_skip_total() { local f n t=0; for f in "$@"; do n=$(ut_skip_count "$f"); t=$((t + ${n:-0})); done; echo "$t"; }
ut_ran_total()  { local f n t=0; for f in "$@"; do n=$(ut_ran_count "$f");  t=$((t + ${n:-0})); done; echo "$t"; }
# ut_skip_reasons <журнал>… — строка «пропущено N: причина» на каждую причину, по
# убыванию, сразу по всем журналам. Одно число прятало за «обычно нет
# python-telegram-bot» и пропуск без dig или openssl. Причины — из строк «… skipped
# 'причина'» unittest -v (кавычки двойные, если в причине есть одинарная; начало
# строки мог занять вывод теста).
ut_skip_reasons() {
    sed -n -E -e "s/.*skipped '(.*)'\$/\1/p" -e 's/.*skipped "(.*)"$/\1/p' "$@" \
        | sort | uniq -c | sort -rn | sed -E 's/^ *([0-9]+) (.*)$/    пропущено \1: \2/'
}
# unit_shard_gaps <журнал>… — части одного модуля (tests/unit_shard.py) обязаны вместе
# покрыть все его тесты: каждая печатает «unit_shard: <модуль> <часть>/<всего>: тестов X из
# Y, …». Печатает модули, где сумма X по частям не равна Y или частей не хватает; пусто —
# всё сошлось. Страж от потери проверок при делении: без него пропавший класс выглядел бы
# как «юнит-тесты пройдены».
unit_shard_gaps() {
    awk '/^unit_shard: / { split($3, pk, "/"); m = $2; got[m] += $5; all[m] = $7 + 0; parts[m]++; want[m] = pk[2] + 0 }
         END { for (m in got) if (got[m] != all[m] || parts[m] != want[m])
                   printf "%s: тестов %d из %d, частей %d из %d\n", m, got[m], all[m], parts[m], want[m] }' "$@"
}
# keep_log <журнал> — журнал провалившегося прогона НЕ удаляем и говорим, где он.
# /dev/null означает «временный файл создать не вышло» — обещать журнал нельзя.
keep_log() {
    if [ "$1" = /dev/null ]; then
        printf '    %s\n' "журнал не сохранён: не удалось создать временный файл"
    else
        printf '    %s\n' "полный журнал: $1"
    fi
}

# now_ms — время в миллисекундах. EPOCHREALTIME есть с bash 5 (разделитель дробной части в
# нём — по локали, поэтому оставляем одни цифры: микросекунды); в bash 3.2 — date, до секунды.
now_ms() {
    if [ -n "${EPOCHREALTIME:-}" ]; then
        local us=${EPOCHREALTIME//[!0-9]/}
        echo $((us / 1000))
    else
        echo $(( $(date +%s) * 1000 ))
    fi
}
# fmt_dur <мс> — «0,4 с», «42 с», «3 мин 05 с».
fmt_dur() {
    local ms=$1 s
    if [ "$ms" -lt 10000 ]; then
        printf '%d,%d с' $((ms / 1000)) $((ms % 1000 / 100))
    elif [ "$ms" -lt 59500 ]; then
        printf '%d с' $(((ms + 500) / 1000))
    else
        s=$(((ms + 500) / 1000))
        printf '%d мин %02d с' $((s / 60)) $((s % 60))
    fi
}

# ── Список скриптов: только то, что реально существует ───────────────────────
scripts=()
for f in install.sh bootstrap.sh lib/*.sh lib/lang/*.sh lib/lang/*.d/*.sh \
         lib/upstream/*/*.sh tests/*.sh tests/fixtures/*.sh \
         goamnezia-bot/*.sh admin-web/*.sh; do
    [ -f "$f" ] && scripts+=("$f")
done
# Вендоренный upstream shellcheck не проверяет: это чужой код, который мы обязаны держать
# байт-в-байт близким к оригиналу (отличие только в идемпотентных iptables).
#
# Словари идут отдельной пачкой с -e SC2154. Причина: файл словаря состоит
# из строк I18N[ключ]="…", и без `declare -gA I18N` в этом же файле
# анализатор считает I18N обычным массивом, а ключ — переменной, и выдаёт
# «ключ используется, но не задан» на КАЖДУЮ строку. Это не находка, а
# свойство разбора; настоящие опечатки в коде SC2154 при этом ловит как
# прежде — на файлах ниже он включён.
own=() dicts=()
for f in ${scripts[@]+"${scripts[@]}"}; do
    case "$f" in
        lib/upstream/*) continue ;;
        lib/lang/*)     dicts+=("$f") ;;
        *)              own+=("$f") ;;
    esac
done

# Окружение с python-telegram-bot, если оно заведено рядом (.venv): без этой
# библиотеки класс экранов бота (117 проверок) молча пропускается, и набор
# уверенно пишет «юнит-тесты пройдены» про непроверенного бота. Заводится один
# раз: python3 -m venv .venv && .venv/bin/pip install 'python-telegram-bot>=21,<22'
PYBIN=python3
[ -x ".venv/bin/python" ] && PYBIN="$PWD/.venv/bin/python"   # cd в корень уже сделан выше

# ── Режим задания: bash tests/run_static.sh --job <задание> ─────────────────
# Так раннер запускает каждое задание отдельным процессом; вывод и TMPDIR ему задаёт
# вызывающий. Руками это нужно разве что для повтора одного задания из отчёта.
if [ "${1:-}" = --job ]; then
    job=${2:-}
    case "$job" in
        shellcheck)
            rc=0
            if [ "${#own[@]}" -gt 0 ]; then
                shellcheck -S warning -x "${own[@]}" || rc=1
            fi
            if [ "${#dicts[@]}" -gt 0 ]; then
                shellcheck -S warning -x -e SC2154 "${dicts[@]}" || rc=1
            fi
            exit "$rc" ;;
        unit)       exec "$PYBIN" -m unittest discover -s tests -p 'test_*.py' -v ;;
        unit:*:*:*) IFS=: read -r _ mod part total <<<"$job"
                    exec "$PYBIN" tests/unit_shard.py "$mod" "$part" "$total" ;;
        unit:*)     exec "$PYBIN" -m unittest discover -s tests -p "${job#unit:}.py" -v ;;
        check:*)    exec python3 "${job#check:}" . ;;
        selftest)   exec "$BASH5" lib/selftest.sh ;;
        e2e:*)      exec "$BASH5" "${job#e2e:}" ;;
        *)          printf 'run_static.sh: неизвестное задание «%s»\n' "$job" >&2; exit 2 ;;
    esac
fi

T_START=$(now_ms)

# ── Сколько заданий одновременно ─────────────────────────────────────────────
ncpu=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
case "$ncpu" in ''|*[!0-9]*|0) ncpu=1 ;; esac
# По умолчанию — половина ядер + 1: на всех ядрах сразу тесты, завязанные на время (pty-мастер,
# цикл привязки бота, ключи MCP), изредка не укладывались в свои ожидания (TAIL-25).
jobs_default=$(( ncpu / 2 + 1 ))
JOBS=${GA_TEST_JOBS:-$jobs_default}
case "$JOBS" in
    ''|*[!0-9]*|0) printf 'GA_TEST_JOBS=«%s» — нужно целое ≥ 1; беру %s\n' "$JOBS" "$jobs_default"; JOBS=$jobs_default ;;
esac
JOBS=$((10#$JOBS))
# pty-мастер одним процессом — двадцать минут; делится на столько частей (tests/unit_shard.py).
PTY_PARTS=8

# ── Каталог прогона: журналы заданий и их TMPDIR ─────────────────────────────
tmp_base=${TMPDIR:-/tmp}
RUN=$(mktemp -d "${tmp_base%/}/goamnezia-static.XXXXXX" 2>/dev/null) \
    || RUN=$(mktemp -d /tmp/goamnezia-static.XXXXXX 2>/dev/null) || RUN=""
if [ -z "$RUN" ]; then
    printf '  ✗ %s\n' "негде создать каталог прогона (TMPDIR=${TMPDIR:-/tmp}): ни журналов, ни TMPDIR заданиям"
    exit 1
fi
mkdir -p "$RUN/tmp"
KEPT=()          # журналы проваленных заданий — остаются на диске
# Место на диске — из фикстуры с запасом (GOAMNEZIA_DF_FIXTURE, ga_df_mb в lib/common.sh, TAIL-12): пороги
# продукта (1 ГБ под образ, 512 МБ под копию, 1 ГБ запаса зеркалу) — константы, и на маленьком /tmp или корне
# CI сухие прогоны отказывали бы «мало места». Проверки «диск полон» подменяют df сами и снимают фикстуру на свой
# вызов. Заданная снаружи фикстура главнее.
if [ -z "${GOAMNEZIA_DF_FIXTURE:-}" ]; then
    printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/stand 102400 2048 100352 2%% /\n' >"$RUN/df.roomy"
    export GOAMNEZIA_DF_FIXTURE="$RUN/df.roomy"
fi

# Запуск задания в фоне. Асинхронной команде неинтерактивная оболочка ставит SIGINT и
# SIGQUIT в «игнорировать» (POSIX), игнор наследуется через exec, а bash, получивший SIGINT
# игнорируемым, перехватить его уже не может. Так Ctrl-C в pty-тесте окна привязки бота
# (test_pairing_ctrl_c_cancels_and_closes_the_window) ничего не делал, и тест краснел только
# в параллельном прогоне. Поэтому задание запускается через python3: сигналы — в исходное
# состояние, своя сессия (setsid), и только потом exec. Своя сессия — это ещё и «без
# управляющего терминала», как у прогона из CI: фоновое задание не читает терминал
# человека (продукт открывает /dev/tty, если stdin не терминал), stdin — /dev/null.
LAUNCH_PY='import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGQUIT, signal.SIG_DFL)
try:
    os.setsid()
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])'
if command -v python3 >/dev/null 2>&1; then
    LAUNCH=(python3 -c "$LAUNCH_PY")
else
    LAUNCH=(env)
fi

# ── Задания ──────────────────────────────────────────────────────────────────
# Параллельные массивы по номеру задания (ассоциативных в bash 3.2 нет).
JOB_ID=()    # что запускать: shellcheck, unit, unit:<модуль>, unit:<модуль>:<часть>:<всего>, check:<файл>, selftest, e2e:<файл>
JOB_TAG=()   # имя файлов задания в $RUN (журнал <тег>.log, TMPDIR tmp/<тег>)
JOB_ST=()    # q — ждёт, r — идёт, d — готово
JOB_T0=()    # мс запуска
JOB_MS=()    # длительность, мс
JOB_RC=()    # код выхода
job_add() {
    local t=$1
    JOB_ID+=("$1"); JOB_TAG+=("${t//[!A-Za-z0-9_.-]/_}"); JOB_ST+=(q); JOB_T0+=(0); JOB_MS+=(0); JOB_RC+=("")
}
job_log() { printf '%s/%s.log' "$RUN" "${JOB_TAG[$1]}"; }
job_index() { local i; for i in ${JOB_ID[@]+"${!JOB_ID[@]}"}; do [ "${JOB_ID[$i]}" = "$1" ] && { echo "$i"; return 0; }; done; return 1; }
job_label() {
    local m p k
    case "$1" in
        unit)        echo "unittest discover" ;;
        unit:*:*:*)  IFS=: read -r _ m p k <<<"$1"; echo "$m $p/$k" ;;
        unit:*)      echo "${1#unit:}" ;;
        check:*|e2e:*) m=${1#*:}; echo "${m##*/}" ;;
        *)           echo "$1" ;;
    esac
}
# job_weight <задание> — примерная длительность в секундах (замер 27.09.2026 на Mac, 18 ядер).
# Только для очереди запуска — долгие первыми, чтобы не они одни досиживали в конце; на
# состав и результат не влияет. Новое задание без строки здесь встаёт в середину очереди.
job_weight() {
    case "$1" in
        unit:test_wizard_pty:*)             echo 240 ;;
        e2e:tests/test_actions_dryrun.sh)   echo 180 ;;
        unit)                               echo 1500 ;;
        e2e:tests/test_core_dryrun.sh)      echo 105 ;;
        e2e:tests/test_lazy_dryrun.sh)      echo 100 ;;
        e2e:tests/test_panel_key_dryrun.sh) echo 20 ;;
        unit:test_bot_features)             echo 80 ;;
        unit:test_pages_layout)             echo 90 ;;
        e2e:tests/test_website_dryrun.sh)   echo 50 ;;
        shellcheck)                         echo 45 ;;
        e2e:tests/test_pages_dryrun.sh)     echo 40 ;;
        e2e:tests/test_subs_dryrun.sh)      echo 35 ;;
        e2e:tests/test_short_dryrun.sh)     echo 50 ;;
        unit:test_core_read)                echo 70 ;;
        unit:test_admin_http)               echo 30 ;;
        unit:test_paste_pty)                echo 20 ;;
        unit:test_live_ssh)                 echo 5 ;;
        e2e:tests/test_share_dryrun.sh|e2e:tests/test_import_dryrun.sh) echo 29 ;;
        e2e:tests/test_upstream_dryrun.sh)  echo 23 ;;
        e2e:tests/test_agents_dryrun.sh)    echo 19 ;;
        unit:test_connect|e2e:tests/test_timers_dryrun.sh|e2e:tests/test_edgeips_dryrun.sh) echo 17 ;;
        unit:test_bootstrap_archive|check:tests/check_menu.py) echo 16 ;;
        e2e:tests/test_mirror_dryrun.sh)    echo 13 ;;
        unit:test_pages_server)             echo 11 ;;
        unit:test_admin_features)           echo 9 ;;
        e2e:tests/test_preflight_dryrun.sh) echo 6 ;;
        e2e:tests/test_neighbours_dryrun.sh) echo 12 ;;
        e2e:tests/test_kmod_dryrun.sh)       echo 35 ;;
        e2e:tests/test_s4fix_dryrun.sh)      echo 30 ;;
        e2e:tests/test_kmodb_dryrun.sh)      echo 60 ;;
        e2e:tests/test_params_dryrun.sh)     echo 40 ;;
        e2e:tests/test_update_dryrun.sh)    echo 75 ;;
        unit:test_mcp|unit:test_mcp_http|unit:test_awgkeys) echo 3 ;;
        unit:*|check:*|selftest)            echo 1 ;;
        *)                                  echo 20 ;;
    esac
}

running=0
QUEUE=()     # номера заданий в порядке запуска
qpos=0

# job_start <номер> — в фоне, через LAUNCH; по завершении пишет «код мс» в <тег>.done.
job_start() {
    local i=$1 tag=${JOB_TAG[$1]}
    mkdir -p "$RUN/tmp/$tag"
    JOB_T0[$i]=$(now_ms)
    JOB_ST[$i]=r
    running=$((running + 1))
    (
        TMPDIR="$RUN/tmp/$tag" "${LAUNCH[@]}" "$BASH" "$SELF" --job "${JOB_ID[$i]}" \
            </dev/null >"$RUN/$tag.log" 2>&1 &
        echo "$!" >"$RUN/$tag.pid"
        wait "$!"
        printf '%s %s\n' "$?" "$(now_ms)" >"$RUN/$tag.done.tmp" && mv -f "$RUN/$tag.done.tmp" "$RUN/$tag.done"
    ) &
}
# job_run_fg <номер> — GA_TEST_JOBS=1: на переднем плане, как прежде, без своей сессии и TMPDIR.
job_run_fg() {
    local i=$1 rc=0
    JOB_T0[$i]=$(now_ms)
    "$BASH" "$SELF" --job "${JOB_ID[$i]}" >"$(job_log "$i")" 2>&1 || rc=$?
    JOB_RC[$i]=$rc
    JOB_MS[$i]=$(( $(now_ms) - JOB_T0[$i] ))
    JOB_ST[$i]=d
}
# job_pump — отметить законченные задания и занять освободившиеся места очередью.
job_pump() {
    local i tag r t
    for i in ${JOB_ID[@]+"${!JOB_ID[@]}"}; do
        [ "${JOB_ST[$i]}" = r ] || continue
        tag=${JOB_TAG[$i]}
        [ -f "$RUN/$tag.done" ] || continue
        read -r r t <"$RUN/$tag.done"
        JOB_RC[$i]=$r
        JOB_MS[$i]=$((t - JOB_T0[$i]))
        JOB_ST[$i]=d
        running=$((running - 1))
    done
    while [ "$running" -lt "$JOBS" ] && [ "$qpos" -lt "${#QUEUE[@]}" ]; do
        job_start "${QUEUE[$qpos]}"
        qpos=$((qpos + 1))
    done
}
# job_wait <номер>… — дождаться заданий; очередь при этом продолжает запускаться.
job_wait() {
    local i busy
    if [ "$JOBS" -eq 1 ]; then
        for i in "$@"; do [ "${JOB_ST[$i]}" = q ] && job_run_fg "$i"; done
        return 0
    fi
    while :; do
        job_pump
        busy=0
        for i in "$@"; do [ "${JOB_ST[$i]}" = d ] || busy=1; done
        [ "$busy" = 0 ] && return 0
        sleep 0.2
    done
}
# job_finish <номер> [keep] — журнал прошедшего задания удаляем, проваленного (или с keep) — оставляем.
job_finish() {
    local log
    log=$(job_log "$1")
    if [ "${JOB_RC[$1]}" = 0 ] && [ -z "${2:-}" ]; then
        rm -f "$log"
    else
        KEPT+=("$log")
    fi
}
# Ctrl-C или kill раннера: задания живут в своих сессиях, сами они сигнала не получат.
# Гасим группы только незавершённых заданий: номер закончившегося мог уже достаться чужому.
on_abort() {
    trap - INT TERM
    local f
    printf '\n  прервано — останавливаю задания\n'
    for f in "$RUN"/*.pid; do
        [ -f "$f" ] && [ ! -f "${f%.pid}.done" ] || continue
        kill -TERM -- "-$(cat "$f")" 2>/dev/null || kill -TERM "$(cat "$f")" 2>/dev/null
    done
    rm -rf "$RUN/tmp"
    printf '  журналы заданий: %s\n' "$RUN"
    exit 130
}
trap on_abort INT TERM

# ── 0. самопроверка отчёта ───────────────────────────────────────────────────
# Этот набор больше никто не проверяет, а врал он молча: счётчик пропусков
# показывал семнадцать вместо пятнадцати, и журнал провалившегося прогона
# удалялся сразу после двадцати строк отказа — разбирать мигающую проверку было
# нечем. Образец ниже держит обе ловушки: ПРОШЕДШИЙ тест со словом skipped в
# названии и итоговую строку, которые счёт строк принимал за пропуски.
# С параллельным прогоном юнит-тесты идут частями, и отчёт складывает журналы: второй
# образец — ещё одна часть; проверяются сложение, сверка частей с полным списком и то, что
# фоновое задание получает Ctrl-C (без этого краснел pty-тест окна привязки бота).
hdr "самопроверка отчёта"
self_log="$RUN/selfcheck-1.log"
self_log2="$RUN/selfcheck-2.log"
cat >"$self_log" <<'SAMPLE'
test_trailing_garbage_is_skipped (test_admin_features.BridgeParseTests) ... ok
test_bot_commands_localized (test_bot_features.KeyboardTests) ... skipped "нет python-telegram-bot"
skipped 'нет python-telegram-bot'
----------------------------------------------------------------------
Ran 458 tests in 339.165s

FAILED (failures=1, skipped=15)
SAMPLE
cat >"$self_log2" <<'SAMPLE'
unit_shard: test_wizard_pty 2/2: тестов 1 из 3, классов 1 из 2
test_x (test_wizard_pty.WizardLite) ... skipped 'нет jq'
setUpModule (test_admin_http) ... skipped 'нет python-telegram-bot'
----------------------------------------------------------------------
Ran 1 test in 0.500s

OK (skipped=2)
SAMPLE
got=$(ut_skip_count "$self_log")
if [ "${got:-нет}" = 15 ]; then
    ok "счётчик пропусков считает пропуски, а не строки со словом skipped"
else
    bad "счётчик пропусков: ожидали 15, получили «${got:-нет}»"
fi
got=$(ut_skip_reasons "$self_log")
if [ "$got" = "    пропущено 2: нет python-telegram-bot" ]; then
    ok "причины пропусков названы; прошедший тест со словом skipped и итог — не причины"
else
    bad "причины пропусков: ожидали «пропущено 2: нет python-telegram-bot», получили «${got:-нет}»"
fi
if keep_log "$self_log" >/dev/null && [ -s "$self_log" ]; then
    ok "журнал провалившегося прогона остаётся на диске, путь к нему назван"
else
    bad "журнал провалившегося прогона не сохранён"
fi
got="$(ut_skip_total "$self_log" "$self_log2")/$(ut_ran_total "$self_log" "$self_log2")"
want=$(printf '%s\n' "    пропущено 3: нет python-telegram-bot" "    пропущено 1: нет jq")
if [ "$got" = 17/459 ] && [ "$(ut_skip_reasons "$self_log" "$self_log2")" = "$want" ]; then
    ok "пропуски, их причины и число тестов складываются по журналам всех частей"
else
    bad "сложение по частям: ожидали 17/459 и «пропущено 3 / 1», получили $got и «$(ut_skip_reasons "$self_log" "$self_log2" | tr '\n' ' ')»"
fi
printf 'unit_shard: test_wizard_pty 1/2: тестов 2 из 3, классов 1 из 2\n' >"$RUN/selfcheck-3.log"
if [ -z "$(unit_shard_gaps "$self_log2" "$RUN/selfcheck-3.log")" ] && [ -n "$(unit_shard_gaps "$self_log2")" ]; then
    ok "части модуля сверяются с полным списком: потерянная часть не проходит молча"
else
    bad "сверка частей: полный набор $(unit_shard_gaps "$self_log2" "$RUN/selfcheck-3.log" | head -n 1), неполный «$(unit_shard_gaps "$self_log2" | head -n 1)»"
fi
if command -v python3 >/dev/null 2>&1; then
    ( "${LAUNCH[@]}" python3 -c 'import signal, sys; sys.exit(signal.getsignal(signal.SIGINT) is signal.SIG_IGN)' \
          </dev/null >/dev/null 2>&1 ) &
    if wait "$!"; then
        ok "фоновое задание получает Ctrl-C: SIGINT не унаследован игнорируемым"
    else
        bad "фоновое задание запускается с игнорируемым SIGINT — pty-тест Ctrl-C покраснеет"
    fi
else
    skp "нет python3 — запуск заданий без сброса сигналов не проверить"
fi
got="$(fmt_dur 400)|$(fmt_dur 42300)|$(fmt_dur 185000)"
if [ "$got" = "0,4 с|42 с|3 мин 05 с" ]; then
    ok "длительность печатается по-человечески"
else
    bad "длительность: ожидали «0,4 с|42 с|3 мин 05 с», получили «$got»"
fi
rm -f "$self_log" "$self_log2" "$RUN/selfcheck-3.log"

# ── Состав прогона ───────────────────────────────────────────────────────────
HAVE_PY=0;   command -v python3 >/dev/null 2>&1 && HAVE_PY=1
HAVE_B5=0;   command -v "$BASH5" >/dev/null 2>&1 && HAVE_B5=1
HAVE_SC=0;   command -v shellcheck >/dev/null 2>&1 && HAVE_SC=1
HAVE_UT=0;   ls tests/test_*.py >/dev/null 2>&1 && HAVE_UT=1
CHECKS="tests/check_i18n_ssh.py tests/check_actions.py tests/check_bootstrap_files.py
        tests/check_menu.py tests/check_i18n_bot.py tests/check_core_read.py"
E2E="tests/test_core_dryrun.sh tests/test_actions_dryrun.sh tests/test_website_dryrun.sh tests/test_agents_dryrun.sh
     tests/test_upstream_dryrun.sh tests/test_import_dryrun.sh tests/test_timers_dryrun.sh
     tests/test_preflight_dryrun.sh tests/test_share_dryrun.sh tests/test_edgeips_dryrun.sh
     tests/test_pages_dryrun.sh tests/test_mirror_dryrun.sh tests/test_update_dryrun.sh tests/test_lazy_dryrun.sh
     tests/test_panel_key_dryrun.sh tests/test_neighbours_dryrun.sh tests/test_subs_dryrun.sh tests/test_short_dryrun.sh
     tests/test_kmod_dryrun.sh tests/test_s4fix_dryrun.sh tests/test_kmodb_dryrun.sh tests/test_params_dryrun.sh
     tests/test_dupline_dryrun.sh tests/test_critics_fix_dryrun.sh"

[ "$HAVE_SC" = 1 ] && job_add shellcheck
UNIT=()
if [ "$HAVE_PY" = 1 ] && [ "$HAVE_UT" = 1 ]; then
    if [ "$JOBS" -eq 1 ]; then
        job_add unit
    else
        # По модулю на задание: новый tests/test_*.py попадает в прогон сам. Модуль из
        # одного процесса, который дольше всего остального, — частями (tests/unit_shard.py).
        for f in tests/test_*.py; do
            m=${f##*/}; m=${m%.py}
            if [ "$m" = test_wizard_pty ]; then
                p=1
                while [ "$p" -le "$PTY_PARTS" ]; do job_add "unit:$m:$p:$PTY_PARTS"; p=$((p + 1)); done
            else
                job_add "unit:$m"
            fi
        done
    fi
    for i in "${!JOB_ID[@]}"; do case "${JOB_ID[$i]}" in unit|unit:*) UNIT+=("$i") ;; esac; done
fi
if [ "$HAVE_PY" = 1 ]; then
    for t in $CHECKS; do [ -f "$t" ] && job_add "check:$t"; done
fi
[ -f lib/selftest.sh ] && [ "$HAVE_B5" = 1 ] && job_add selftest
if [ "$HAVE_B5" = 1 ]; then
    for t in $E2E; do [ -f "$t" ] && job_add "e2e:$t"; done
fi
# Очередь запуска: долгие первыми (при равенстве — в порядке отчёта).
if [ "${#JOB_ID[@]}" -gt 0 ]; then
    while read -r _ i; do QUEUE+=("$i"); done < <(
        for i in "${!JOB_ID[@]}"; do printf '%s %s\n' "$(job_weight "${JOB_ID[$i]}")" "$i"; done | sort -k1,1nr -k2,2n)
fi

# ── 2a. py_compile и node --check ────────────────────────────────────────────
# Бот, панель, MCP и python-помощники импортируются только на сервере; здесь
# ловим хотя бы синтаксис, иначе опечатка доезжала бы до юнита и падала там.
# Считается ДО запуска заданий, а печатается на своём месте после shellcheck: чистка
# __pycache__ ниже не должна идти, пока python заданий пишет туда свои .pyc.
sec_pycompile() {
    local t0 pyfiles=() f
    t0=$(now_ms)
    {
        if [ "$HAVE_PY" = 0 ]; then
            skp "python3 не установлен"
        else
            for f in lib/awgkeys.py lib/catalog_tool.py lib/core_read.py lib/share_server.py goamnezia-bot/bot.py goamnezia-bot/i18n.py \
                     goamnezia-mcp/server.py admin-web/server.py; do
                [ -f "$f" ] && pyfiles+=("$f")
            done
            if [ "${#pyfiles[@]}" -gt 0 ] && python3 -m py_compile "${pyfiles[@]}"; then
                ok "py_compile: ${#pyfiles[@]} файлов"
            elif [ "${#pyfiles[@]}" -gt 0 ]; then
                bad "py_compile"
            fi
            # __pycache__ рядом с исходниками в репозитории не нужен
            find lib goamnezia-bot goamnezia-mcp admin-web -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
        fi
        if [ -f admin-web/static/app.js ]; then
            if command -v node >/dev/null 2>&1; then
                node --check admin-web/static/app.js && ok "node --check app.js" || bad "node --check app.js"
            else
                skp "node не установлен — синтаксис app.js не проверен"
            fi
        fi
    } >"$RUN/sec-pycompile.out" 2>&1
    PYC_MS=$(( $(now_ms) - t0 ))
}
sec_pycompile

# Задания пошли (при GA_TEST_JOBS=1 — нет: каждое запустит свой раздел, по очереди).
[ "$JOBS" -gt 1 ] && job_pump

# ── 1. bash -n ───────────────────────────────────────────────────────────────
t0=$(now_ms)
{
    if [ "$HAVE_B5" = 0 ]; then
        skp "нет интерпретатора ${BASH5} — задайте BASH5=/path/to/bash"
    else
        n=0
        for f in ${scripts[@]+"${scripts[@]}"}; do
            # Вендоренные скрипты Amnezia (lib/upstream/*) держат $ПЕРЕМЕННЫЕ и
            # синтаксис sh — проверяем их тем же bash -n, но не удивляемся
            # предупреждениям shellcheck ниже: чинить чужой upstream мы не вправе.
            if "$BASH5" -n "$f" 2>/dev/null; then
                n=$((n+1))
            else
                bad "синтаксис: $f"
                "$BASH5" -n "$f" 2>&1 | sed 's/^/      /'
            fi
        done
        [ "$n" -gt 0 ] && ok "проверено файлов: $n"
        [ -f install.sh ] || skp "install.sh ещё не написан"
    fi
} >"$RUN/sec-bashn.out" 2>&1
hdr "bash -n (${BASH5})" "$(fmt_dur $(( $(now_ms) - t0 )))"
cat "$RUN/sec-bashn.out"

# ── 2. shellcheck ────────────────────────────────────────────────────────────
if [ "$HAVE_SC" = 0 ]; then
    hdr "shellcheck -S warning -x"
    skp "shellcheck не установлен (brew install shellcheck / apt install shellcheck)"
else
    i=$(job_index shellcheck)
    job_wait "$i"
    hdr "shellcheck -S warning -x" "$(fmt_dur "${JOB_MS[$i]}")"
    cat "$(job_log "$i")"
    if [ "${JOB_RC[$i]}" = 0 ]; then
        ok "чисто: код ${#own[@]}, словари ${#dicts[@]}"
    else
        bad "shellcheck нашёл замечания"
        keep_log "$(job_log "$i")"
    fi
    job_finish "$i"
fi

# ── 2a (печать). py_compile / node --check ───────────────────────────────────
hdr "python3 -m py_compile / node --check" "$(fmt_dur "$PYC_MS")"
cat "$RUN/sec-pycompile.out"

# ── 3. юнит-тесты python ─────────────────────────────────────────────────────
# unit_times <номер>… — «задание длительность» по убыванию, строками до ~100 знаков.
unit_times() {
    local i line="" item
    while read -r _ i; do
        item="$(job_label "${JOB_ID[$i]}") $(fmt_dur "${JOB_MS[$i]}")"
        if [ -z "$line" ]; then
            line="    задания: $item"
        elif [ $(( ${#line} + ${#item} )) -gt 100 ]; then
            printf '%s,\n' "$line"; line="      $item"
        else
            line="$line, $item"
        fi
    done < <(for i in "$@"; do printf '%s %s\n' "${JOB_MS[$i]}" "$i"; done | sort -k1,1nr)
    [ -n "$line" ] && printf '%s\n' "$line"
}
UT_TITLE="$PYBIN -m unittest discover -s tests -p 'test_*.py'"
if [ "$HAVE_PY" = 0 ]; then
    hdr "$UT_TITLE"
    skp "нет python3"
elif [ "$HAVE_UT" = 0 ]; then
    hdr "$UT_TITLE"
    skp "юнит-тестов tests/test_*.py ещё нет"
else
    job_wait "${UNIT[@]}"
    # Длительность раздела — от запуска первой части до конца последней.
    ut_logs=() ut_failed=() t_first="" t_last=0
    for i in "${UNIT[@]}"; do
        ut_logs+=("$(job_log "$i")")
        [ "${JOB_RC[$i]}" = 0 ] || ut_failed+=("$i")
        if [ -z "$t_first" ] || [ "${JOB_T0[$i]}" -lt "$t_first" ]; then t_first=${JOB_T0[$i]}; fi
        if [ $((JOB_T0[i] + JOB_MS[i])) -gt "$t_last" ]; then t_last=$((JOB_T0[i] + JOB_MS[i])); fi
    done
    ut_gaps=$(unit_shard_gaps "${ut_logs[@]}")
    ut_ran=$(ut_ran_total "${ut_logs[@]}")
    hdr "$UT_TITLE" "$(fmt_dur $((t_last - t_first)))"
    # Пропущенные тесты раньше нигде не были видны: строка «юнит-тесты пройдены»
    # одинаково означала и «все прошли», и «треть класса молча пропущена». Так
    # целый класс экранов бота (101 проверка) был тёмным, и в нём жили три
    # отставших ожидания, которые выглядели бы как дефекты продукта. Теперь
    # число пропусков видно всегда, а причина названа.
    if [ "${#ut_failed[@]}" -eq 0 ] && [ -z "$ut_gaps" ]; then
        # Число пропусков берём из итоговой строки самого unittest («OK (skipped=15)»),
        # а не считаем строки со словом «skipped». Счёт строк врал в обе стороны:
        # в него попадали сама итоговая строка и ПРОШЕДШИЙ тест, у которого это слово
        # в названии (test_trailing_garbage_is_skipped), а настоящие пропуски, у
        # которых вывод теста перебил строку отчёта, наоборот терялись. Частей
        # несколько — числа и причины складываются по всем их журналам.
        ut_skipped=$(ut_skip_total "${ut_logs[@]}")
        if [ "${ut_skipped:-0}" -gt 0 ]; then
            ok "юнит-тесты пройдены (пропущено: $ut_skipped)"
            ut_reasons=$(ut_skip_reasons "${ut_logs[@]}")
            [ -n "$ut_reasons" ] && printf '%s\n' "$ut_reasons" | head -n 10
            if grep -q 'python-telegram-bot' <<<"$ut_reasons"; then
                printf '    %s\n' "поставьте python-telegram-bot в .venv, чтобы экраны бота проверялись тоже (tests/README.md)"
            fi
        else
            ok "юнит-тесты пройдены"
        fi
    else
        bad "юнит-тесты"
        if [ -n "$ut_gaps" ]; then
            # Сбой деления, а не тестов: журналы всех частей остаются целиком.
            printf '      %s\n' "части модуля покрыли не все его тесты (tests/unit_shard.py):"
            printf '%s\n' "$ut_gaps" | sed 's/^/        /'
            printf '    %s\n' "журналы всех частей: $RUN"
        fi
        for i in ${ut_failed[@]+"${ut_failed[@]}"}; do
            log=$(job_log "$i")
            out=$(grep -E "^(FAIL|ERROR):|^AssertionError|^Ran |^FAILED" "$log" | tail -n 20)
            [ -n "$out" ] || out=$(tail -n 5 "$log")
            printf '%s\n' "$out" | sed 's/^/      /'
            # Журнал при провале ОСТАВЛЯЕМ: в двадцати строках отказа не видно ни
            # порядка тестов, ни вывода упавшего — а разбирать мигающую проверку
            # больше не по чему, повторный прогон её может и не воспроизвести.
            keep_log "$log"
        done
    fi
    printf '    тестов: %s' "$ut_ran"
    [ "${#UNIT[@]}" -gt 1 ] && printf ' (заданий: %s)' "${#UNIT[@]}"
    printf '\n'
    [ "${#UNIT[@]}" -gt 1 ] && unit_times "${UNIT[@]}"
    for i in "${UNIT[@]}"; do job_finish "$i" ${ut_gaps:+keep}; done
fi

# ── 4. статические сверки ────────────────────────────────────────────────────
for t in $CHECKS; do
    if [ "$HAVE_PY" = 0 ]; then
        hdr "$t"; skp "нет python3"
    elif [ ! -f "$t" ]; then
        hdr "$t"; skp "$t ещё не написан"
    else
        i=$(job_index "check:$t")
        job_wait "$i"
        hdr "$t" "$(fmt_dur "${JOB_MS[$i]}")"
        cat "$(job_log "$i")"
        if [ "${JOB_RC[$i]}" = 0 ]; then
            ok "$(basename "$t")"
        else
            bad "$(basename "$t")"
            keep_log "$(job_log "$i")"
        fi
        job_finish "$i"
    fi
done

# ── 5. автопроверка библиотек (--selftest без сервера) ───────────────────────
if [ ! -f lib/selftest.sh ]; then
    hdr "lib/selftest.sh"; skp "lib/selftest.sh ещё не написан"
elif [ "$HAVE_B5" = 0 ]; then
    hdr "lib/selftest.sh"; skp "нет интерпретатора ${BASH5}"
else
    i=$(job_index selftest)
    job_wait "$i"
    hdr "lib/selftest.sh" "$(fmt_dur "${JOB_MS[$i]}")"
    cat "$(job_log "$i")"
    if [ "${JOB_RC[$i]}" = 0 ]; then
        ok "selftest"
    else
        bad "selftest"
        keep_log "$(job_log "$i")"
    fi
    job_finish "$i"
fi

# ── 6. сквозные прогоны без Docker (GOAMNEZIA_DRY_RUN=1) ─────────────────────
# test_core_dryrun.sh зовёт функции модулей напрямую, test_actions_dryrun.sh —
# настоящий install.sh --action=… --json процессом на каждый шаг, как бот и
# MCP. Вывод прячем в файл: на успехе достаточно строки «ИТОГ», при провале
# показываем хвост, где стоят ✗ с подсказками.
for t in $E2E; do
    if [ ! -f "$t" ]; then
        hdr "$t"; skp "$t ещё не написан"
    elif [ "$HAVE_B5" = 0 ]; then
        hdr "$t"; skp "нет интерпретатора ${BASH5}"
    else
        i=$(job_index "e2e:$t")
        job_wait "$i"
        hdr "$t" "$(fmt_dur "${JOB_MS[$i]}")"
        e2e_log=$(job_log "$i")
        if [ "${JOB_RC[$i]}" = 0 ]; then
            ok "$(grep 'ИТОГ' "$e2e_log" | tail -1)"
        else
            bad "$t"
            grep -E '✗|!!|ИТОГ' "$e2e_log" | tail -n 30 | sed 's/^/      /'
            # Журнал при провале не удаляем — см. юнит-тесты выше: тридцати строк
            # мало, чтобы понять, на каком шаге стенд свернул не туда.
            keep_log "$e2e_log"
        fi
        job_finish "$i"
    fi
done

wait    # фоновые обёртки заданий уже закончились; собрать их коды
trap - INT TERM
rm -rf "$RUN/tmp" "$RUN"/*.pid "$RUN"/*.done "$RUN"/sec-*.out
[ "${#KEPT[@]}" -eq 0 ] && rm -rf "$RUN"

printf '\n──────────────────────────────────────────\n'
printf 'время: %s (заданий: %s, одновременно до %s — GA_TEST_JOBS)\n' \
    "$(fmt_dur $(( $(now_ms) - T_START )))" "${#JOB_ID[@]}" "$JOBS"
if [ "${#JOB_ID[@]}" -gt 0 ]; then
    top=$(for i in "${!JOB_ID[@]}"; do printf '%s %s\n' "${JOB_MS[$i]}" "$i"; done | sort -k1,1nr | head -n 5 |
          while read -r _ i; do printf '%s %s, ' "$(job_label "${JOB_ID[$i]}")" "$(fmt_dur "${JOB_MS[$i]}")"; done)
    [ -n "$top" ] && printf 'дольше всех: %s\n' "${top%, }"
fi
if [ "$fail" -eq 0 ]; then
    printf 'все проверки пройдены'
else
    printf 'НЕ ПРОШЛО проверок: %s' "$fail"
fi
[ "$skip" -gt 0 ] && printf ' (пропущено: %s)' "$skip"
printf '\n'
exit "$fail"
