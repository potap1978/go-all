#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — жёсткий бюджет логов: не более GODNS_LOG_CAP_MB на всё хозяйство
# goDNS, из них GODNS_LOG_LIVE_MB — живой несжатый слой, остальное — сжатый
# архив с постоянной ротацией.
#
# ── Зачем это вообще ────────────────────────────────────────────────────
# Забитый /var — это не косметика. Когда раздел кончается, sshd и nginx
# встают на записи в лог: принимают TCP и молчат. dnsdist при этом
# продолжает отвечать на уже установленных соединениях, поэтому снаружи
# выглядит как «резолвер жив, а сервер недоступен», и зайти по SSH, чтобы
# разобраться, уже нельзя. Ротация здесь — вопрос доступности, а не гигиены.
#
# ── Три слоя, по убыванию отдачи ────────────────────────────────────────
#   1) резать в источнике  — не писать того, что не нужно (verbose, лог
#      запросов, лог консольных подключений);
#   2) объявленные потолки — logrotate ПО РАЗМЕРУ, а не по дате;
#   3) сторож              — таймер считает ФАКТ в байтах и подрезает.
#
# Третий слой нужен именно потому, что штатный logrotate ходит раз в сутки:
# между запусками всплеск успевает выесть диск, и «объявленный» потолок не
# спасает никого. Отсюда же и собственный часовой таймер ротации: директива
# `size` (как и `maxsize`) проверяется ТОЛЬКО в момент запуска logrotate,
# и раз в сутки этого мало.
#
# ── Почему свой файл, а не journald ─────────────────────────────────────
# У journald потолок `SystemMaxUse` — ОДИН НА ВСЮ МАШИНУ. Сосед goTelegram
# выставляет его глобально (300M) через /etc/systemd/journald.conf.d/.
# Если бы goDNS писал в общий журнал, наши логи и его делили бы один
# бюджет, «200 МБ на goDNS» стало бы невыполнимым обещанием, а наш всплеск
# вытеснял бы его записи (и наоборот). Поэтому:
#
#   • goDNS НЕ ПИШЕТ НИЧЕГО в /etc/systemd/journald.conf.d/ — чужой потолок
#     не трогаем и за него не воюем;
#   • вывод службы уходит в свой файл через drop-in юнита
#     (StandardOutput=append:), а бюджетом занимаемся мы сами.
#
# LogNamespace= (отдельный экземпляр journald со своим SystemMaxUse) тоже
# развязал бы бюджеты, но не годится по трём причинам:
#   — journald не умеет «150 МБ несжатых, остальное сжать»: он жмёт лишь
#     отдельные большие поля, а строки лога маленькие, то есть сжатия по
#     сути нет вовсе, и требование заказчика выразить нечем;
#   — `journalctl -u dnsdist` перестаёт показывать вывод службы (нужен
#     --namespace=godns), и оператор ищет логи в двух местах;
#   — это ещё один процесс journald, без которого юнит не стартует; цена
#     ошибки — не «нет логов», а «нет резолвера».
#
# Требует: lib/common.sh — но НЕ обязательно. Файл сознательно
# самодостаточен: systemd-юнит сторожа делает `. lib/logs.sh` и зовёт
# godns_logs_enforce напрямую, без всего остального goDNS.

# ── i18n и логи: мягкая подстраховка ────────────────────────────────────
# Ровно тот же приём, что в lib/engine.sh. Настоящие определения перекроют
# заглушки, в каком бы порядке ни грузили.
if ! declare -F log_warning >/dev/null 2>&1; then
    log_info()    { echo "  $*" >&2; }
    log_success() { echo "  $*" >&2; }
    log_warning() { echo "  $*" >&2; }
    log_error()   { echo "  $*" >&2; }
    log_dim()     { echo "  $*" >&2; }
    log_step()    { echo "  $*" >&2; }
fi
#
# Заглушка t() здесь УМНЕЕ, чем в engine.sh, и это не прихоть. Сторож
# запускается systemd-юнитом, который грузит только этот файл: полного i18n
# в нём нет, а `lib/i18n.sh` втянуть нельзя — он жёстко выходит на bash 3.2 и
# унёс бы с собой тесты чистой логики на macOS. При этом сообщение про
# кончающееся место читает человек, и «logs_sweep_emergency [512]» вместо
# фразы — ровно тот класс ошибки, который в проекте ловит check-str-usage.
# Поэтому строку достаём прямо из собранного словаря, без движка i18n.
if ! declare -F t >/dev/null 2>&1; then
    _logs_lang_file() {
        local d lang="ru"
        d=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || d=""
        [ -n "$d" ] || return 1
        if [ -r "${GODNS_DIR:-/opt/godns}/.language" ]; then
            lang=$(cat "${GODNS_DIR:-/opt/godns}/.language" 2>/dev/null) || lang="ru"
        fi
        case "$lang" in en) : ;; *) lang="ru" ;; esac
        [ -r "${d}/lang/${lang}.sh" ] || return 1
        printf '%s' "${d}/lang/${lang}.sh"
        return 0
    }
    GODNS_LOGS_LANG_FILE=$(_logs_lang_file) || GODNS_LOGS_LANG_FILE=""

    t() {
        local _v=""
        if [ -n "${GODNS_LOGS_LANG_FILE:-}" ]; then
            _v=$(sed -n "s/^I18N\[$1\]=\"\(.*\)\"\$/\1/p" "$GODNS_LOGS_LANG_FILE" 2>/dev/null) || _v=""
            _v="${_v%%$'\n'*}"
        fi
        if [ -n "$_v" ]; then printf '%s' "$_v"; else printf '%s' "$1"; fi
        return 0
    }
    tf() {
        local _k="$1" _fmt=""; shift
        _fmt=$(t "$_k")
        case "$_fmt" in
            *%*) printf "$_fmt" "$@" ;;
            *)   printf '%s' "$_fmt"
                 if [ "$#" -gt 0 ]; then printf ' [%s]' "$*"; fi ;;
        esac
        return 0
    }
fi

# ── Бюджеты ─────────────────────────────────────────────────────────────
# Требование заказчика: «не более 200 МБ на сервере, 150 не сжатых».
#
# АРИФМЕТИКА, а не «примерно» (проверяется в test_common.sh):
#
#   потоков логов        S = GODNS_LOG_STREAMS = 2   (dnsdist.log, godns.log)
#   размер ротации       F = godns_logs_file_mb      (выводится из LIVE!)
#   поколений            R = godns_logs_rotate_count
#
#   Несжатый слой. При `copytruncate` + `delaycompress` в каждый момент
#   несжатыми существуют РОВНО два файла на поток: живой (≤ F, потому что
#   при F он ротируется) и последний ротированный «.1» (≤ F). Значит
#             несжатое ≤ S × 2 × F,
#   и F выбирается так, чтобы это было ≤ LIVE:  F = LIVE / (2 × S).
#   При LIVE=150 и S=2 это F ≤ 37; берём 35 → несжатое ≤ 140 МБ.
#
#   Сжатый архив получает остаток: CAP − LIVE = 50 МБ. Текст логов жмётся
#   gzip'ом примерно вдесятеро, то есть поколение весит ≈ F/10:
#             R = (CAP − LIVE) × 10 / (F × S) = 500 / 70 = 7.
#   Итого ожидаемо: 140 несжатых + 2 × 6 × 3.5 ≈ 42 сжатых ≈ 182 МБ.
#
#   Множитель 10 — ДОПУЩЕНИЕ, а не гарантия: на несжимаемом мусоре архив
#   раздуется. Гарантию даёт слой 3 — сторож считает реальные байты и сносит
#   самые старые архивы, пока сумма не станет ≤ CAP. Арифметика нужна для
#   того, чтобы сторожу почти никогда не приходилось срабатывать.
GODNS_LOG_CAP_MB="${GODNS_LOG_CAP_MB:-200}"        # потолок на всё goDNS
GODNS_LOG_LIVE_MB="${GODNS_LOG_LIVE_MB:-150}"      # из них несжатый слой
GODNS_LOG_KEEP_FREE_MB="${GODNS_LOG_KEEP_FREE_MB:-1024}"  # аварийный порог
GODNS_LOG_FILE_MB="${GODNS_LOG_FILE_MB:-35}"       # размер ротации файла
GODNS_LOG_STREAMS="${GODNS_LOG_STREAMS:-2}"        # сколько *.log мы ведём
GODNS_LOG_RATIO="${GODNS_LOG_RATIO:-10}"           # ожидаемое сжатие gzip

# ── Наши файлы. Чужих здесь нет и быть не должно ────────────────────────
GODNS_LOGROTATE_CONF="${GODNS_LOGROTATE_CONF:-/etc/logrotate.d/godns}"
GODNS_LOG_TMPFILES="${GODNS_LOG_TMPFILES:-/etc/tmpfiles.d/godns.conf}"
GODNS_LOG_UNIT_DIR="${GODNS_LOG_UNIT_DIR:-/etc/systemd/system}"
GODNS_LOG_ROT_UNIT="${GODNS_LOG_ROT_UNIT:-godns-logrotate}"
GODNS_LOG_SWEEP_UNIT="${GODNS_LOG_SWEEP_UNIT:-godns-logbudget}"
GODNS_LOG_STATE="${GODNS_LOG_STATE:-/run/godns-logbudget.state}"
# Состояние logrotate — СВОЁ. В общий /var/lib/logrotate/status не лезем:
# это чужое хозяйство, и переписывать его ради своих строк незачем.
GODNS_LOGROTATE_STATE="${GODNS_LOGROTATE_STATE:-${GODNS_DIR:-/opt/godns}/logrotate.state}"

# Итоги последнего прогона сторожа — для меню. Обычные переменные, а не
# ассоциативный массив: файл обязан разбираться и работать на bash 3.2,
# иначе чистая логика не проверяется на macOS без контейнера.
GODNS_LOGS_BEFORE=0
GODNS_LOGS_AFTER=0
GODNS_LOGS_GZ=0
GODNS_LOGS_RM=0
GODNS_LOGS_FORCED="no"
GODNS_LOGS_EMERG="no"

# ═══════════════════════════════════════════════════════════════════════
# АРИФМЕТИКА
# ═══════════════════════════════════════════════════════════════════════

# Размер ротации. НЕ берём GODNS_LOG_FILE_MB на веру: если оператор поднял
# его выше того, что вмещает несжатый слой, обещание «150 МБ несжатых»
# сломается молча. Поэтому потолок выводится из LIVE, а константа может
# только уменьшить его.
godns_logs_file_mb() {
    local want="${GODNS_LOG_FILE_MB:-35}"
    local live="${GODNS_LOG_LIVE_MB:-150}"
    local streams="${GODNS_LOG_STREAMS:-2}"
    local max=0
    case "$want"    in ''|*[!0-9]*) want=35 ;;  esac
    case "$live"    in ''|*[!0-9]*) live=150 ;; esac
    case "$streams" in ''|*[!0-9]*|0) streams=1 ;; esac
    max=$(( live / (2 * streams) ))
    if [ "$max" -lt 1 ]; then max=1; fi
    if [ "$want" -gt "$max" ] || [ "$want" -lt 1 ]; then want="$max"; fi
    printf '%s' "$want"
    return 0
}

# Сколько поколений держать. `rotate N` считает ВСЕ ротированные файлы,
# включая несжатый «.1», поэтому сжатых архивов будет N−1 на поток.
godns_logs_rotate_count() {
    local cap="${GODNS_LOG_CAP_MB:-200}"
    local live="${GODNS_LOG_LIVE_MB:-150}"
    local streams="${GODNS_LOG_STREAMS:-2}"
    local ratio="${GODNS_LOG_RATIO:-10}"
    local file n=0
    file=$(godns_logs_file_mb)
    case "$cap"     in ''|*[!0-9]*) cap=200 ;;  esac
    case "$live"    in ''|*[!0-9]*) live=150 ;; esac
    case "$streams" in ''|*[!0-9]*|0) streams=1 ;; esac
    case "$ratio"   in ''|*[!0-9]*|0) ratio=10 ;; esac
    if [ "$cap" -le "$live" ]; then
        printf '3'
        return 0
    fi
    n=$(( (cap - live) * ratio / (file * streams) ))
    if [ "$n" -lt 3 ];  then n=3;  fi
    if [ "$n" -gt 60 ]; then n=60; fi
    printf '%s' "$n"
    return 0
}

# rc0, если объявленные константы действительно укладываются в потолок.
# Держим отдельной функцией, чтобы тест мог доказать арифметику, а не
# поверить комментарию.
godns_logs_budget_ok() {
    local cap="${GODNS_LOG_CAP_MB:-200}" live="${GODNS_LOG_LIVE_MB:-150}"
    local streams="${GODNS_LOG_STREAMS:-2}" ratio="${GODNS_LOG_RATIO:-10}"
    local file rot plain arch
    file=$(godns_logs_file_mb)
    rot=$(godns_logs_rotate_count)
    case "$streams" in ''|*[!0-9]*|0) streams=1 ;; esac
    case "$ratio"   in ''|*[!0-9]*|0) ratio=10 ;; esac
    plain=$(( streams * 2 * file ))
    arch=$(( streams * (rot - 1) * file / ratio ))
    if [ "$plain" -gt "$live" ]; then return 1; fi
    if [ "$(( plain + arch ))" -gt "$cap" ]; then return 1; fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ИЗМЕРЕНИЕ
# ═══════════════════════════════════════════════════════════════════════

# Сумма размеров файлов первого уровня. `du -sb` и `find -printf` — GNU-измы:
# первого нет в busybox, второго нет в BSD/macOS, а этот файл обязан считать
# байты и в тестах чистой логики. Глоб + `wc -c` работает везде.
#
# godns_logs_bytes [каталог] [маска]
godns_logs_bytes() {
    local dir="${1:-${GODNS_LOG_DIR:-/var/log/godns}}"
    local mask="${2:-*}"
    local total=0 f sz
    if [ ! -d "$dir" ]; then printf '0'; return 0; fi
    for f in "$dir"/$mask; do
        [ -f "$f" ] || continue
        sz=$(wc -c < "$f" 2>/dev/null) || sz=0
        sz="${sz// /}"
        case "$sz" in ''|*[!0-9]*) sz=0 ;; esac
        total=$(( total + sz ))
    done
    printf '%s' "$total"
    return 0
}

# Сколько файлов подходит под маску.
godns_logs_count() {
    local dir="${1:-${GODNS_LOG_DIR:-/var/log/godns}}"
    local mask="${2:-*}"
    local n=0 f
    if [ ! -d "$dir" ]; then printf '0'; return 0; fi
    for f in "$dir"/$mask; do
        [ -f "$f" ] || continue
        n=$(( n + 1 ))
    done
    printf '%s' "$n"
    return 0
}

# Свободно мегабайт на разделе с логами. Пусто = определить не удалось;
# вызывающий обязан это различать, иначе «пусто» превратится в «0 МБ
# свободно» и включит аварийный режим на здоровой машине.
godns_logs_free_mb() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}" out=""
    if [ ! -d "$dir" ]; then dir="/var/log"; fi
    if [ ! -d "$dir" ]; then dir="/"; fi
    out=$(df -Pm "$dir" 2>/dev/null | awk 'NR==2 {print $4+0}') || out=""
    out="${out// /}"
    case "$out" in ''|*[!0-9]*) out="" ;; esac
    printf '%s' "$out"
    return 0
}

# Байты → «12.3 МБ». Без bc и python: целочисленно, с одним знаком.
godns_logs_human() {
    local b="${1:-0}" v=0 u=""
    case "$b" in ''|*[!0-9]*) b=0 ;; esac
    if   [ "$b" -ge 1073741824 ]; then v=$(( b * 10 / 1073741824 )); u=$(t logs_unit_gb)
    elif [ "$b" -ge 1048576 ];    then v=$(( b * 10 / 1048576 ));    u=$(t logs_unit_mb)
    elif [ "$b" -ge 1024 ];       then v=$(( b * 10 / 1024 ));       u=$(t logs_unit_kb)
    else
        printf '%s %s' "$b" "$(t logs_unit_b)"
        return 0
    fi
    printf '%s.%s %s' "$(( v / 10 ))" "$(( v % 10 ))" "$u"
    return 0
}

# Время последней ротации: mtime самого свежего ротированного файла.
# `date -r ФАЙЛ` понимают и GNU, и BSD — в отличие от `stat`, который в
# этих двух мирах пишется по-разному.
godns_logs_last_rotation() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}" newest="" f
    if [ ! -d "$dir" ]; then printf ''; return 0; fi
    for f in "$dir"/*.log.[0-9] "$dir"/*.log.[0-9][0-9] "$dir"/*.gz; do
        [ -f "$f" ] || continue
        if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then newest="$f"; fi
    done
    if [ -z "$newest" ]; then printf ''; return 0; fi
    date -r "$newest" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '?'
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СЛОЙ 1 — РЕЗАТЬ В ИСТОЧНИКЕ
# ═══════════════════════════════════════════════════════════════════════
#
# У dnsdist три рычага объёма, и все три мы выключаем в отрендеренном
# конфиге (lib/engines/dnsdist.sh): setVerbose(false),
# setVerboseHealthChecks(false), setConsoleConnectionsLogging(false).
# Четвёртый — LogAction на запросах — не рендерим никогда.
#
# Здесь мы не чиним, а ПРОВЕРЯЕМ живой конфиг: его мог отредактировать
# оператор, и тогда причина «логи растут» видна сразу, а не после часа
# разглядывания графиков.
#
# rc0  — тихо;  rc10 — найдена болтливая директива, нужен engine_apply;
# rc1  — конфига нет.
godns_logs_apply_source_cut() {
    local conf="${1:-${DNSDIST_CONF:-/etc/dnsdist/dnsdist.conf}}"
    local loud=0
    if [ ! -r "$conf" ]; then
        log_dim "$(t logs_cut_noconf)"
        return 1
    fi
    if grep -qE '^[[:space:]]*setVerbose\([[:space:]]*true' "$conf" 2>/dev/null; then
        log_warning "$(tf logs_cut_verbose "$conf")"
        loud=1
    fi
    if grep -qE '^[[:space:]]*addAction\(.*LogAction' "$conf" 2>/dev/null; then
        log_warning "$(tf logs_cut_query "$conf")"
        loud=1
    fi
    if [ "$loud" -eq 1 ]; then
        log_dim "$(t logs_cut_hint)"
        return 10
    fi
    log_success "$(t logs_cut_ok)"
    return 0
}

# Не верим на слово, а меряем: сколько строк в минуту реально пишется.
# Печатает «строк_в_минуту» в stdout.
#
# Ротация в середине окна обнуляет счётчик — отдаём 0, а не отрицательное
# число: иначе оценка «на сколько суток хватит» уедет в бесконечность.
godns_logs_measure_rate() {
    local window="${1:-20}"
    local f="${2:-${GODNS_LOG_DIR:-/var/log/godns}/dnsdist.log}"
    local a=0 b=0
    case "$window" in ''|*[!0-9]*|0) window=20 ;; esac
    if [ ! -r "$f" ]; then printf ''; return 1; fi
    a=$(wc -l < "$f" 2>/dev/null) || a=0
    a="${a// /}"
    sleep "$window"
    b=$(wc -l < "$f" 2>/dev/null) || b=0
    b="${b// /}"
    case "$a" in ''|*[!0-9]*) a=0 ;; esac
    case "$b" in ''|*[!0-9]*) b=0 ;; esac
    if [ "$b" -lt "$a" ]; then printf '0'; return 0; fi
    printf '%s' "$(( (b - a) * 60 / window ))"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СЛОЙ 2 — ОБЪЯВЛЕННЫЕ ПОТОЛКИ
# ═══════════════════════════════════════════════════════════════════════

# Каталог логов. Права 0750 root:adm — как у остального /var/log на Ubuntu:
# adm читает, посторонние нет. Группы adm может не быть (минимальный образ) —
# тогда остаёмся на root:root, это не повод отказываться от логов.
godns_logs_ensure_dir() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}"
    local old="/var/log/godns.log"
    local grp="root"
    if getent group adm >/dev/null 2>&1; then grp="adm"; fi
    if [ ! -d "$dir" ]; then
        install -d -m 0750 -o root -g "$grp" "$dir" 2>/dev/null \
            || mkdir -p "$dir" 2>/dev/null \
            || return 1
    fi
    chmod 0750 "$dir" 2>/dev/null || true
    chgrp "$grp" "$dir" 2>/dev/null || true

    # Переезд со старого места. Пока файл лежал в /var/log/godns.log, он не
    # попадал ни под какую ротацию вообще.
    if [ -f "$old" ] && [ ! -e "${GODNS_LOG:-${dir}/godns.log}" ]; then
        if mv -f "$old" "${GODNS_LOG:-${dir}/godns.log}" 2>/dev/null; then
            log_dim "$(tf logs_install_migrated "$old" "${GODNS_LOG:-${dir}/godns.log}")"
        fi
    fi
    return 0
}

# Текст конфига logrotate в stdout. Отдельной функцией — чтобы тест мог
# прочитать его без root и без записи в /etc.
#
# Почему именно так:
#   size          — ротация ПО РАЗМЕРУ. Это ключевое: при `size` временные
#                   директивы (daily/weekly) игнорируются, и решение
#                   становится БЕЗ СОСТОЯНИЯ. Значит наш часовой таймер и
#                   штатный суточный logrotate не могут «ротировать дважды»:
#                   после первого прогона файл нулевой, и второй ничего не
#                   делает. Именно поэтому конфиг безопасно лежит в общем
#                   /etc/logrotate.d/ — но остаётся нашим файлом.
#   maxsize       — НЕ ставим: под `size` он ничего не добавляет, а вместе
#                   с daily вернул бы состояние и двойную ротацию.
#   copytruncate  — единственный корректный вариант: файл открывает systemd
#                   (StandardOutput=append:), дескриптор держится всё время
#                   работы службы, и после rename dnsdist продолжил бы
#                   писать в переименованный файл. Дырки в файле при этом не
#                   возникает: append: открывает файл с O_APPEND, а под
#                   O_APPEND ядро пересчитывает смещение на конец при каждой
#                   записи. Плата — строки, попавшие в зазор между копией и
#                   усечением; для служебного лога это приемлемо.
#   delaycompress — то, что и создаёт «несжатый слой» сверх живого файла:
#                   самое свежее поколение остаётся под обычным grep/tail.
#   notifempty    — не плодить поколения из пустоты.
#   missingok     — файла может не быть: службу ещё не запускали.
#   su root adm   — каталог не root:root, и без su logrotate ругается на
#                   «insecure permissions» и молча пропускает конфиг.
#   nocreate      — при copytruncate logrotate файл не пересоздаёт, а его
#                   владельца и режим задаёт systemd; `create` тут был бы
#                   ложным обещанием. Задано ЯВНО, хотя это и умолчание:
#                   наш таймер зовёт logrotate с одним нашим файлом, а
#                   штатный суточный прогон — через /etc/logrotate.conf,
#                   где глобальный `create` унаследовался бы в наш блок.
#                   Блок обязан вести себя одинаково в обоих случаях.
#   lastaction    — сразу после ротации зовём сторожа: тогда потолок
#                   соблюдается в тот же момент, а не через 15 минут.
#
# compressoptions/compresscmd НЕ задаём: выигрыш gzip -9 над умолчанием на
# тексте — единицы процентов, а неизвестная директива в logrotate роняет
# разбор ВСЕГО файла, и ротации не станет вовсе. Допущение о степени сжатия
# (GODNS_LOG_RATIO) на это не опирается — за факт отвечает сторож.
godns_logs_render_logrotate() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}"
    local file rot grp="root" sweep
    file=$(godns_logs_file_mb)
    rot=$(godns_logs_rotate_count)
    if getent group adm >/dev/null 2>&1; then grp="adm"; fi
    sweep="${GODNS_INSTALLER_DIR:-/opt/godns-installer}/lib/logs.sh"

    printf '%s\n' "# goDNS Pro ${GODNS_VERSION:-?} — бюджет логов. Файл принадлежит goDNS Pro."
    printf '%s\n' "# Правки будут ПОТЕРЯНЫ: файл перезаписывается целиком."
    printf '%s\n' "# Потолок ${GODNS_LOG_CAP_MB:-200} МБ, из них ${GODNS_LOG_LIVE_MB:-150} МБ несжатыми."
    printf '%s\n' "# Чужие конфиги в /etc/logrotate.d/ goDNS Pro не читает и не трогает."
    printf '%s\n' "${dir}/*.log {"
    printf '    su root %s\n' "$grp"
    printf '    size %sM\n'   "$file"
    printf '    rotate %s\n'  "$rot"
    printf '%s\n' '    missingok'
    printf '%s\n' '    notifempty'
    printf '%s\n' '    nocreate'
    printf '%s\n' '    compress'
    printf '%s\n' '    delaycompress'
    printf '%s\n' '    copytruncate'
    printf '%s\n' '    nomail'
    printf '%s\n' '    lastaction'
    printf '        /bin/bash -c ". %s && godns_logs_enforce" >/dev/null 2>&1 || true\n' "$sweep"
    printf '%s\n' '    endscript'
    printf '%s\n' '}'
    return 0
}

# Записать файл, только если содержимое изменилось. Идемпотентность здесь
# не украшение: без сравнения каждый запуск goDNS дёргал бы daemon-reload и
# рестарт таймеров на ровном месте.
_logs_put() {   # <файл> <текст>
    local f="$1" want="$2" have=""
    if [ -f "$f" ]; then have=$(cat "$f" 2>/dev/null) || have=""; fi
    if [ "$have" = "$want" ]; then return 1; fi
    local d; d=$(dirname "$f")
    mkdir -p "$d" 2>/dev/null || true
    printf '%s\n' "$want" > "$f" 2>/dev/null || return 2
    chmod 0644 "$f" 2>/dev/null || true
    return 0
}

godns_logs_apply_logrotate() {
    local want rc=0
    want=$(godns_logs_render_logrotate) || return 1
    _logs_put "$GODNS_LOGROTATE_CONF" "$want" || rc=$?
    if [ "$rc" -eq 2 ]; then
        # Ключ logs_install_rotate — сообщение об УСПЕХЕ («Правила ротации:
        # …»). Печатать его под восклицательным знаком при провале значило
        # сообщать оператору ровно обратное тому, что произошло.
        log_error "$(tf logs_install_rotate_fail "$GODNS_LOGROTATE_CONF")"
        return 1
    fi
    return 0
}

# systemd-tmpfiles: каталог логов должен пережить перезагрузку и чистку
# /var/log. Без него юнит с StandardOutput=append: не стартует вовсе —
# и мы получим «нет резолвера» вместо «нет логов».
godns_logs_apply_tmpfiles() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}" grp="root" want rc=0
    if getent group adm >/dev/null 2>&1; then grp="adm"; fi
    want="# goDNS Pro — каталог логов. Файл принадлежит goDNS Pro.
d ${dir} 0750 root ${grp} -"
    _logs_put "$GODNS_LOG_TMPFILES" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then
        systemd-tmpfiles --create "$GODNS_LOG_TMPFILES" >/dev/null 2>&1 || true
    fi
    return 0
}

# Свой часовой таймер ротации. Штатный logrotate ходит раз в сутки, а
# ротация по размеру проверяется ТОЛЬКО в момент запуска — без своего
# таймера всплеск за сутки съедает сколько угодно, и «потолок» врёт.
#
# Состояние — своё (GODNS_LOGROTATE_STATE): в общий /var/lib/logrotate/status
# не пишем. Двойной ротации это не создаёт, потому что решение по `size`
# состояния не использует.
godns_logs_install_timers() {
    local changed=0 want f rc=0 lr sweep
    if ! command -v systemctl >/dev/null 2>&1; then
        log_warning "$(t logs_install_no_systemd)"
        return 1
    fi

    # Путь к logrotate берём фактический: ExecStart= требует абсолютного, а
    # прибитый /usr/sbin/logrotate — это отказ юнита на любом образе, где
    # бинарник лежит иначе. К моменту вызова пакет уже поставлен.
    lr=$(command -v logrotate 2>/dev/null) || lr=""
    [ -n "$lr" ] || lr="/usr/sbin/logrotate"
    sweep="${GODNS_INSTALLER_DIR:-/opt/godns-installer}/lib/logs.sh"

    want="[Unit]
Description=goDNS Pro: log rotation by size
ConditionPathExists=${GODNS_LOGROTATE_CONF}

[Service]
Type=oneshot
ExecStart=${lr} --state ${GODNS_LOGROTATE_STATE} ${GODNS_LOGROTATE_CONF}"
    f="${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_ROT_UNIT}.service"
    rc=0; _logs_put "$f" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then changed=1; fi

    want="[Unit]
Description=goDNS Pro: hourly log rotation by size

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
AccuracySec=1min

[Install]
WantedBy=timers.target"
    f="${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_ROT_UNIT}.timer"
    rc=0; _logs_put "$f" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then changed=1; fi

    # ConditionPathExists — чтобы полуснятый goDNS не сыпал отказами юнита
    # каждые 15 минут: без файла таймер просто ничего не делает.
    want="[Unit]
Description=goDNS Pro: log budget watchdog
ConditionPathExists=${sweep}

[Service]
Type=oneshot
ExecStart=/bin/bash -c '. ${sweep} && godns_logs_enforce'"
    f="${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_SWEEP_UNIT}.service"
    rc=0; _logs_put "$f" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then changed=1; fi

    want="[Unit]
Description=goDNS Pro: log budget watchdog every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
AccuracySec=1min

[Install]
WantedBy=timers.target"
    f="${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_SWEEP_UNIT}.timer"
    rc=0; _logs_put "$f" "$want" || rc=$?
    if [ "$rc" -eq 0 ]; then changed=1; fi

    if [ "$changed" -eq 1 ]; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    local ok=0
    systemctl enable --now "${GODNS_LOG_ROT_UNIT}.timer"   >/dev/null 2>&1 || ok=1
    systemctl enable --now "${GODNS_LOG_SWEEP_UNIT}.timer" >/dev/null 2>&1 || ok=1
    if [ "$ok" -ne 0 ]; then
        log_warning "$(t logs_install_timer_fail)"
        return 1
    fi
    return 0
}

godns_logs_timers_active() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl is-active --quiet "${GODNS_LOG_ROT_UNIT}.timer"   2>/dev/null || return 1
    systemctl is-active --quiet "${GODNS_LOG_SWEEP_UNIT}.timer" 2>/dev/null || return 1
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СЛОЙ 3 — СТОРОЖ
# ═══════════════════════════════════════════════════════════════════════

# Принудительная ротация: единственный законный способ уменьшить ЖИВОЙ файл.
# Обрезать его самим нельзя — это дыра в логе и разъехавшееся смещение у
# пишущего процесса. copytruncate внутри logrotate делает то же самое, но
# сначала снимает полную копию.
godns_logs_force_rotate() {
    command -v logrotate >/dev/null 2>&1 || return 1
    [ -f "$GODNS_LOGROTATE_CONF" ] || return 1
    mkdir -p "$(dirname "$GODNS_LOGROTATE_STATE")" 2>/dev/null || true
    logrotate --force --state "$GODNS_LOGROTATE_STATE" "$GODNS_LOGROTATE_CONF" \
        >/dev/null 2>&1 || return 1
    return 0
}

# Подрезать каталог до <бюджет в байтах>. Печатает «дожато удалено».
#
# Порядок ровно такой и не другой:
#   1) дожать ротированное, но ещё не сжатое, КРОМЕ «.1» — «.1» и есть
#      обещанный несжатый слой, его трогать незачем, а сжав его руками, мы
#      бы вдобавок сбили logrotate с delaycompress;
#   2) сносить самые старые архивы;
#   3) живой *.log не трогаем НИКОГДА — им занимается ротация.
godns_logs_trim() {
    local budget="${1:-0}"
    local dir="${2:-${GODNS_LOG_DIR:-/var/log/godns}}"
    local used=0 f="" gz=0 dropped=0
    case "$budget" in ''|*[!0-9]*) budget=0 ;; esac
    if [ ! -d "$dir" ]; then printf '0 0'; return 0; fi

    used=$(godns_logs_bytes "$dir")
    if [ "$used" -le "$budget" ]; then printf '0 0'; return 0; fi

    for f in "$dir"/*.log.[2-9] "$dir"/*.log.[1-9][0-9]; do
        [ -f "$f" ] || continue
        if gzip -9 "$f" 2>/dev/null; then gz=$(( gz + 1 )); fi
    done
    used=$(godns_logs_bytes "$dir")

    while [ "$used" -gt "$budget" ]; do
        # ls -t печатает от свежего к старому; хвост — самый старый.
        # `find -printf '%T@'` тут нельзя: его нет в BSD/macOS, а функция
        # обязана проверяться в тестах чистой логики.
        f=$(ls -t "$dir"/*.gz 2>/dev/null | tail -1) || f=""
        [ -n "$f" ] || break
        [ -f "$f" ] || break
        rm -f "$f" 2>/dev/null || break
        dropped=$(( dropped + 1 ))
        used=$(godns_logs_bytes "$dir")
    done

    printf '%s %s' "$gz" "$dropped"
    return 0
}

_logs_write_state() {
    local d=""
    d=$(date -Iseconds 2>/dev/null) || d=$(date 2>/dev/null) || d="?"
    printf '%s %s %s %s %s %s\n' \
        "$d" "$GODNS_LOGS_BEFORE" "$GODNS_LOGS_AFTER" \
        "$GODNS_LOGS_GZ" "$GODNS_LOGS_RM" "$GODNS_LOGS_FORCED" \
        > "$GODNS_LOG_STATE" 2>/dev/null || true
    return 0
}

# Отчёт сторожа: «время — итог». Пусто, если он ещё не ходил.
godns_logs_state_line() {
    local line=""
    [ -r "$GODNS_LOG_STATE" ] || { printf ''; return 0; }
    line=$(cat "$GODNS_LOG_STATE" 2>/dev/null) || line=""
    printf '%s' "$line"
    return 0
}

# Главная точка слоя 3. Идемпотентна, ничего не спрашивает, годится и для
# systemd-юнита, и для меню.
godns_logs_enforce() {
    local dir cap_b used free half res
    dir="${GODNS_LOG_DIR:-/var/log/godns}"
    local cap="${GODNS_LOG_CAP_MB:-200}"
    case "$cap" in ''|*[!0-9]*) cap=200 ;; esac
    cap_b=$(( cap * 1024 * 1024 ))

    GODNS_LOGS_GZ=0
    GODNS_LOGS_RM=0
    GODNS_LOGS_FORCED="no"
    GODNS_LOGS_EMERG="no"
    GODNS_LOGS_BEFORE=0
    GODNS_LOGS_AFTER=0

    if [ ! -d "$dir" ]; then return 0; fi

    used=$(godns_logs_bytes "$dir")
    GODNS_LOGS_BEFORE="$used"
    GODNS_LOGS_AFTER="$used"

    if [ "$used" -gt "$cap_b" ]; then
        res=$(godns_logs_trim "$cap_b" "$dir") || res="0 0"
        GODNS_LOGS_GZ="${res%% *}"
        GODNS_LOGS_RM="${res##* }"
        used=$(godns_logs_bytes "$dir")
    fi

    # Всплеск между запусками таймера: живой файл сам по себе перерос
    # бюджет, и сносить больше нечего. Отдаём его ротации и подрезаем ещё раз.
    if [ "$used" -gt "$cap_b" ]; then
        if godns_logs_force_rotate; then
            GODNS_LOGS_FORCED="yes"
            res=$(godns_logs_trim "$cap_b" "$dir") || res="0 0"
            GODNS_LOGS_GZ=$(( GODNS_LOGS_GZ + ${res%% *} ))
            GODNS_LOGS_RM=$(( GODNS_LOGS_RM + ${res##* } ))
            used=$(godns_logs_bytes "$dir")
        fi
    fi

    # Аварийный режим. Это важнее любых объявленных потолков: именно
    # кончившееся место кладёт sshd и nginx, а не размер нашего лога.
    free=$(godns_logs_free_mb) || free=""
    if [ -n "$free" ] && [ "$free" -lt "${GODNS_LOG_KEEP_FREE_MB:-1024}" ]; then
        half=$(( cap_b / 2 ))
        godns_logs_force_rotate && GODNS_LOGS_FORCED="yes"
        res=$(godns_logs_trim "$half" "$dir") || res="0 0"
        GODNS_LOGS_GZ=$(( GODNS_LOGS_GZ + ${res%% *} ))
        GODNS_LOGS_RM=$(( GODNS_LOGS_RM + ${res##* } ))
        used=$(godns_logs_bytes "$dir")
        GODNS_LOGS_EMERG="yes"
        log_warning "$(tf logs_sweep_emergency "$free")"
    fi

    GODNS_LOGS_AFTER="$used"
    _logs_write_state
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# УСТАНОВКА / СНЯТИЕ
# ═══════════════════════════════════════════════════════════════════════

# Всё разом. Идемпотентно: повторный вызов ничего не переписывает и не
# дёргает systemd, если содержимое совпало.
godns_logs_apply_all() {
    log_step "$(t logs_install_step)"

    if ! godns_logs_ensure_dir; then
        log_warning "$(tf logs_install_dir_fail "${GODNS_LOG_DIR:-/var/log/godns}")"
        return 1
    fi
    log_dim "$(tf logs_install_dir "${GODNS_LOG_DIR:-/var/log/godns}")"

    if ! command -v logrotate >/dev/null 2>&1; then
        log_dim "$(t logs_install_no_logrotate)"
        if declare -F ensure_packages >/dev/null 2>&1; then
            ensure_packages logrotate >/dev/null 2>&1 || true
        fi
    fi

    godns_logs_apply_tmpfiles || true
    godns_logs_apply_logrotate || true

    if command -v logrotate >/dev/null 2>&1; then
        log_dim "$(tf logs_install_rotate "$GODNS_LOGROTATE_CONF" \
                     "$(godns_logs_file_mb)" "$(godns_logs_rotate_count)")"
    else
        log_warning "$(t logs_install_logrotate_missing)"
    fi
    log_dim "$(tf logs_install_budget "${GODNS_LOG_CAP_MB:-200}" "${GODNS_LOG_LIVE_MB:-150}")"

    if godns_logs_install_timers; then
        log_dim "$(tf logs_install_timer "${GODNS_LOG_ROT_UNIT}.timer" "${GODNS_LOG_SWEEP_UNIT}.timer")"
    fi

    godns_logs_enforce || true
    log_success "$(t logs_install_done)"
    return 0
}

# Снимаем ТОЛЬКО своё. Ни одного чужого файла в /etc/logrotate.d/,
# /etc/tmpfiles.d/ и /etc/systemd/system/ не касаемся.
godns_logs_remove() {
    local removed="" f
    log_step "$(t logs_remove_step)"

    if command -v systemctl >/dev/null 2>&1; then
        for f in "${GODNS_LOG_ROT_UNIT}.timer" "${GODNS_LOG_SWEEP_UNIT}.timer"; do
            systemctl disable --now "$f" >/dev/null 2>&1 || true
        done
        # Таймер снят, но УЖЕ ЗАПУЩЕННЫЙ им сторож дорабатывает и в конце
        # пишет свой state-файл — ПОСЛЕ нашего rm. Ровно так на стенде
        # /run/godns-logbudget.state пережил удаление: таймер стрельнул в
        # ту же секунду, что и uninstall. Гасим сами службы и только потом
        # убираем файлы.
        for f in "${GODNS_LOG_ROT_UNIT}.service" "${GODNS_LOG_SWEEP_UNIT}.service"; do
            systemctl stop "$f" >/dev/null 2>&1 || true
        done
    fi
    for f in "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_ROT_UNIT}.timer" \
             "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_ROT_UNIT}.service" \
             "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_SWEEP_UNIT}.timer" \
             "${GODNS_LOG_UNIT_DIR}/${GODNS_LOG_SWEEP_UNIT}.service" \
             "$GODNS_LOGROTATE_CONF" "$GODNS_LOG_TMPFILES" \
             "$GODNS_LOGROTATE_STATE" "$GODNS_LOG_STATE"; do
        if [ -e "$f" ]; then
            rm -f "$f" 2>/dev/null && removed="${removed}${f} "
        fi
    done
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi
    if [ -n "$removed" ]; then
        log_dim "$(tf logs_remove_done "$removed")"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ОТЧЁТ ДЛЯ МЕНЮ
# ═══════════════════════════════════════════════════════════════════════
# Ширина терминала — 80 колонок. Подпись выравниваем по СИМВОЛАМ:
# ${#s} считает байты, и кириллица разъезжает колонку ровно на свою длину.
_logs_pad() { pad_label "$1" "${2:-14}"; }

_logs_row() {   # <подпись> <значение>
    printf '  %s %b\n' "$(_logs_pad "$1")" "$2"
    return 0
}

# Полоска заполнения на 20 знаков. Ровно та же роль, что у процента, но
# видна боковым зрением.
_logs_bar() {
    local pct="${1:-0}" w=20 full i out=""
    case "$pct" in ''|*[!0-9]*) pct=0 ;; esac
    if [ "$pct" -gt 100 ]; then pct=100; fi
    full=$(( pct * w / 100 ))
    i=0
    while [ "$i" -lt "$w" ]; do
        if [ "$i" -lt "$full" ]; then out="${out}#"; else out="${out}."; fi
        i=$(( i + 1 ))
    done
    printf '%s' "$out"
    return 0
}

godns_logs_report() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}"
    local cap="${GODNS_LOG_CAP_MB:-200}" live="${GODNS_LOG_LIVE_MB:-150}"
    local total gz_b plain_b n ngz pct rot free state

    print_header "$(t logs_rep_title)"
    _logs_row "$(t logs_rep_dir)" "${DIM}${dir}${NC}"

    if [ ! -d "$dir" ]; then
        echo -e "  ${DIM}$(t logs_rep_empty)${NC}"
        print_rule
        return 0
    fi

    total=$(godns_logs_bytes "$dir")
    gz_b=$(godns_logs_bytes "$dir" '*.gz')
    plain_b=$(( total - gz_b ))
    n=$(godns_logs_count "$dir")
    ngz=$(godns_logs_count "$dir" '*.gz')

    if [ "$n" -eq 0 ]; then
        echo -e "  ${DIM}$(t logs_rep_empty)${NC}"
        print_rule
        return 0
    fi

    pct=$(( total / 1048576 * 100 / (cap > 0 ? cap : 1) ))

    _logs_row "$(t logs_rep_files)" "${WHITE}$(tf logs_rep_files_val "$n" "$ngz")${NC}"
    _logs_row "$(t logs_rep_plain)" \
        "${WHITE}$(godns_logs_human "$plain_b")${NC} ${DIM}$(t logs_rep_of) ${live} $(t logs_unit_mb)${NC}"
    _logs_row "$(t logs_rep_arch)" "${WHITE}$(godns_logs_human "$gz_b")${NC}"
    _logs_row "$(t logs_rep_total)" \
        "${WHITE}$(godns_logs_human "$total")${NC} ${DIM}$(t logs_rep_of) ${cap} $(t logs_unit_mb)${NC}  ${DIM}$(_logs_bar "$pct") ${pct}%${NC}"

    rot=$(godns_logs_last_rotation) || rot=""
    if [ -n "$rot" ]; then
        _logs_row "$(t logs_rep_rotated)" "${DIM}${rot}${NC}"
    else
        _logs_row "$(t logs_rep_rotated)" "${DIM}$(t logs_rep_rotated_never)${NC}"
    fi

    free=$(godns_logs_free_mb) || free=""
    if [ -n "$free" ]; then
        _logs_row "$(t logs_rep_free)" \
            "${DIM}$(tf logs_rep_free_val "$free" "${GODNS_LOG_KEEP_FREE_MB:-1024}")${NC}"
    fi

    if [ -f "$GODNS_LOGROTATE_CONF" ]; then
        _logs_row "$(t logs_rep_conf)" \
            "${DIM}$(tf logs_rep_conf_val "$GODNS_LOGROTATE_CONF" "$(godns_logs_file_mb)" "$(godns_logs_rotate_count)")${NC}"
    else
        _logs_row "$(t logs_rep_conf)" "${YELLOW}$(t logs_rep_conf_missing)${NC}"
    fi

    if godns_logs_timers_active; then
        _logs_row "$(t logs_rep_timer)" \
            "${DIM}$(tf logs_rep_timer_val "${GODNS_LOG_ROT_UNIT}.timer" "${GODNS_LOG_SWEEP_UNIT}.timer")${NC}"
    else
        _logs_row "$(t logs_rep_timer)" "${YELLOW}$(t logs_rep_timer_off)${NC}"
    fi

    state=$(godns_logs_state_line) || state=""
    if [ -n "$state" ]; then
        _logs_row "$(t logs_rep_sweep)" "${DIM}${state%% *}${NC}"
    else
        _logs_row "$(t logs_rep_sweep)" "${DIM}$(t logs_rep_sweep_never)${NC}"
    fi

    echo ""
    if [ "$total" -gt "$(( cap * 1024 * 1024 ))" ]; then
        echo -e "  ${YELLOW}$(t logs_rep_over)${NC}"
    else
        echo -e "  ${GREEN}$(t logs_rep_ok)${NC}"
    fi
    echo ""
    echo -e "  ${DIM}$(t logs_rep_why_file)${NC}"
    echo -e "  ${DIM}$(t logs_rep_why_file2)${NC}"
    echo -e "  ${DIM}$(t logs_rep_why_file3)${NC}"
    print_rule
    return 0
}

# Ручная уборка из меню — с отчётом «было / стало».
godns_logs_cleanup_now() {
    local dir="${GODNS_LOG_DIR:-/var/log/godns}"
    local cap="${GODNS_LOG_CAP_MB:-200}" before after

    log_step "$(t logs_sweep_step)"
    if [ ! -d "$dir" ]; then
        log_dim "$(t logs_rep_empty)"
        return 0
    fi

    before=$(godns_logs_bytes "$dir")
    log_dim "$(tf logs_sweep_before "$(godns_logs_human "$before")")"

    godns_logs_enforce || true
    after="$GODNS_LOGS_AFTER"

    if [ "$GODNS_LOGS_FORCED" = "yes" ]; then log_dim "$(t logs_sweep_forced)"; fi
    if [ "${GODNS_LOGS_GZ:-0}" -gt 0 ]; then log_dim "$(tf logs_sweep_gz "$GODNS_LOGS_GZ")"; fi
    if [ "${GODNS_LOGS_RM:-0}" -gt 0 ]; then log_dim "$(tf logs_sweep_rm "$GODNS_LOGS_RM")"; fi

    if [ "$before" = "$after" ] && [ "${GODNS_LOGS_RM:-0}" -eq 0 ]; then
        log_success "$(t logs_sweep_nothing)"
        return 0
    fi

    log_success "$(tf logs_sweep_after "$(godns_logs_human "$after")" "${cap} $(t logs_unit_mb)")"
    if [ "$after" -gt "$(( cap * 1024 * 1024 ))" ]; then
        log_warning "$(tf logs_sweep_still_over "$(godns_logs_human "$after")")"
    fi
    return 0
}

# Замер потока + оценка «на сколько хватит». Показываем ИЗМЕРЕННОЕ, а не
# обещанное: директивы «тихого режима» бывают сняты руками, и увидеть это
# можно только по числу.
godns_logs_rate_report() {
    local window="${1:-20}"
    local f="${GODNS_LOG_DIR:-/var/log/godns}/dnsdist.log"
    local rate="" perday bytes_per_line=120 days live_b

    if [ ! -r "$f" ]; then
        log_warning "$(t logs_rate_nofile)"
        return 1
    fi

    log_dim "$(tf logs_rate_step "$window")"
    rate=$(godns_logs_measure_rate "$window" "$f") || rate=""
    case "$rate" in ''|*[!0-9]*) rate=0 ;; esac

    perday=$(( rate * 60 * 24 ))
    log_info "$(tf logs_rate_result "$rate" "$perday")"

    if [ "$rate" -eq 0 ]; then
        log_success "$(t logs_rate_days_many)"
        return 0
    fi
    live_b=$(( ${GODNS_LOG_LIVE_MB:-150} * 1024 * 1024 ))
    days=$(( live_b / (perday * bytes_per_line + 1) ))
    if [ "$days" -ge 60 ]; then
        log_success "$(t logs_rate_days_many)"
    else
        log_info "$(tf logs_rate_days "$days")"
    fi
    return 0
}
