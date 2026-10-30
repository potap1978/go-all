#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — тонкая обёртка над lib/bench.py.
# «Показать пинги от сервера до DNS» в меню провайдеров.
#
# Контракт с остальным кодом:
#   • всё, что печатают log_*, идёт в STDERR; в stdout только полезное значение;
#   • ни одной голой подстановки команды, которая штатно возвращает 1
#     (скрипт под `set -euo pipefail`, а «нет ответа» здесь рядовой исход);
#   • последняя команда функции никогда не `[ … ] && …`.
#
# Почему не ping: ICMP идёт другим путём и на другой порт, половина
# DoH/DoT-эндпоинтов его дропает, а у аникаста ближайший по ICMP узел может
# быть вообще не тем, который ответит на 853/443. Меряем сам транспорт.

# Каталог этой библиотеки — bench.py лежит рядом.
if [ -z "${GODNS_BENCH_LIB_DIR:-}" ]; then
    GODNS_BENCH_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || GODNS_BENCH_LIB_DIR=""
    [ -n "$GODNS_BENCH_LIB_DIR" ] || GODNS_BENCH_LIB_DIR="${GODNS_DIR:-/opt/godns}/lib"
fi
GODNS_BENCH_PY="${GODNS_BENCH_PY:-${GODNS_BENCH_LIB_DIR}/bench.py}"

# Параметры замера. Читаются bench.py из окружения.
GODNS_PROBES="${GODNS_PROBES:-5}"
GODNS_TIMEOUT="${GODNS_TIMEOUT:-4}"
GODNS_PARALLEL="${GODNS_PARALLEL:-16}"
GODNS_QNAME="${GODNS_QNAME:-example.com}"

# Аварийная заглушка i18n. В бою lib/i18n.sh уже подгружен и определил t/tf;
# заглушка нужна только чтобы тесты и одиночный запуск не падали на
# «t: command not found». Она печатает КЛЮЧ, а не выдуманный перевод —
# отсутствие перевода должно быть видно, а не замаскировано.
if ! declare -F t >/dev/null 2>&1; then
    t() { printf '%s' "$1"; }
fi
if ! declare -F tf >/dev/null 2>&1; then
    tf() { local k="$1"; shift; printf '%s' "$k${*:+ $*}"; }
fi

# ── Прогон ──────────────────────────────────────────────────────────────
# bench_run <candidates_json_file>  ->  печатает путь к файлу с результатом.
# Возврат: 0 — есть разобранный JSON; 1 — нет.
bench_run() {
    local cands="${1:-}" out="" log="" rc=0

    if [ -z "$cands" ] || [ ! -s "$cands" ]; then
        log_error "$(t bench_err_no_candidates)"
        return 1
    fi
    if [ ! -f "$GODNS_BENCH_PY" ]; then
        log_error "$(tf bench_err_no_engine "$GODNS_BENCH_PY")"
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "$(t bench_err_no_python)"
        return 1
    fi

    if declare -F godns_mktemp >/dev/null 2>&1; then
        out=$(godns_mktemp) || out=""
    fi
    if [ -z "$out" ]; then
        out=$(mktemp "/tmp/godns-res.XXXXXX") || out=""
    fi
    if [ -z "$out" ]; then
        log_error "$(t bench_err_tmp)"
        return 1
    fi

    # Диагностика движка не должна лететь оператору в лицо: складываем её
    # в журнал и достаём только если прогон не удался.
    log="${out}.err"
    : > "$log" 2>/dev/null || log="/dev/null"

    GODNS_PROBES="$GODNS_PROBES" \
    GODNS_TIMEOUT="$GODNS_TIMEOUT" \
    GODNS_PARALLEL="$GODNS_PARALLEL" \
    GODNS_QNAME="$GODNS_QNAME" \
    python3 "$GODNS_BENCH_PY" "$cands" >"$out" 2>"$log" || rc=$?

    if [ "$rc" -ne 0 ] || [ ! -s "$out" ]; then
        log_error "$(tf bench_err_failed "$rc")"
        if [ "$log" != "/dev/null" ] && [ -s "$log" ]; then
            log_dim "$(tail -n 3 "$log" 2>/dev/null || true)"
        fi
        [ "$log" = "/dev/null" ] || rm -f "$log" 2>/dev/null || true
        return 1
    fi
    if [ "$log" != "/dev/null" ] && [ -s "$log" ]; then
        # Прогон удался, но движку было что сказать (перепробы, отсутствие
        # HTTP/2). Оператору это не нужно — кладём в журнал.
        cat "$log" >> "${GODNS_LOG:-/dev/null}" 2>/dev/null || true
    fi
    [ "$log" = "/dev/null" ] || rm -f "$log" 2>/dev/null || true

    if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out" >/dev/null 2>&1; then
        log_error "$(t bench_err_bad_output)"
        return 1
    fi

    printf '%s' "$out"
    return 0
}

# ── Сводка прогона для подвала меню ─────────────────────────────────────
# bench_summary <results_json>  ->  «5 проб по example.com, 4.0 с»
bench_summary() {
    local res="${1:-}" line=""
    [ -s "${res:-/nonexistent}" ] || { printf '%s' ""; return 0; }
    line=$(python3 - "$res" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
c = d.get("config") or {}
sys.stdout.write("\x1f".join([str(c.get("probes", "")), str(c.get("qname", "")),
                               str(d.get("elapsed_s", ""))]))
PY
) || line=""
    [ -n "$line" ] || { printf '%s' ""; return 0; }
    local probes qname elapsed
    IFS=$'\x1f' read -r probes qname elapsed <<< "$line"
    printf '%s' "$(tf bench_summary_line "$probes" "$qname" "$elapsed")"
    return 0
}

# ── Идентификаторы, отсортированные по warm (для меню) ──────────────────
# bench_sorted_ids <results_json>  ->  по одному id в строке, быстрые сверху,
# неотвечающие — в конце.
bench_sorted_ids() {
    local res="${1:-}" ids=""
    [ -s "${res:-/nonexistent}" ] || return 0
    ids=$(python3 - "$res" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
rows = list((d.get("results") or {}).values())
rows.sort(key=lambda r: (r.get("warm_ms") is None,
                         r.get("warm_ms") if r.get("warm_ms") is not None else 0.0,
                         str(r.get("id") or "")))
for r in rows:
    if r.get("id"):
        sys.stdout.write("%s\n" % r["id"])
PY
) || ids=""
    [ -n "$ids" ] && printf '%s\n' "$ids"
    return 0
}

# ── Короткая строка для колонки меню ────────────────────────────────────
# bench_fmt_latency <results_json> <id>
#   " 61 мс"            — всё хорошо
#   "~61 мс"            — число оценочное (degraded)
#   " 61 мс (хол. 306)" — рукопожатие заметно дороже установившегося режима
#   " 61 мс 3/5"        — часть проб не дошла
#   "  — таймаут"       — не отвечает
#   "  — TLS"           — сертификат/SNI: в блокируемой сети это диагноз
#
# Поле числа — ровно три знакоместа. Ширину НЕ задаём через printf '%3s':
# bash считает её в байтах, а «—» весит три байта, и строка отказа теряет
# отступ, разъезжаясь с числовыми. Выравниваем вручную.
bench_fmt_latency() {
    local res="${1:-}" id="${2:-}" line=""

    if [ -z "$id" ] || [ ! -s "${res:-/nonexistent}" ]; then
        printf '  — %s' "$(t bench_err_other)"
        return 0
    fi

    # Один вызов python на кандидата, поля разделены US (0x1f).
    # Грабли: TAB для этого не годится. Табуляция входит в «пробельные»
    # символы IFS, и bash схлопывает подряд идущие разделители и срезает
    # ведущие — а у неотвечающего кандидата первые два поля (warm, cold)
    # как раз пустые, и весь ряд уезжает на две позиции влево: класс отказа
    # теряется, и вместо «таймаут» оператор видит «ошибка».
    line=$(python3 - "$res" "$id" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
r = (d.get("results") or {}).get(sys.argv[2])
if not isinstance(r, dict):
    # Кандидата в результатах нет вовсе (добавлен после замера) — это
    # «не мерялось», а не «ошибка».
    sys.stdout.write("ABSENT")
    raise SystemExit(0)


def num(v):
    if v is None:
        return ""
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return ""


sys.stdout.write("\x1f".join([
    num(r.get("warm_ms")),
    num(r.get("cold_ms")),
    str(r.get("probes_ok", 0)),
    str(r.get("probes", 0)),
    "1" if r.get("degraded") else "0",
    str(r.get("error_class") or ""),
    str(r.get("http_code") or ""),
]))
PY
) || line=""

    if [ -z "$line" ]; then
        printf '  — %s' "$(t bench_err_other)"
        return 0
    fi
    if [ "$line" = "ABSENT" ]; then
        printf '  — %s' "$(t prov_latency_unknown)"
        return 0
    fi

    local warm cold ok probes degraded eclass code
    IFS=$'\x1f' read -r warm cold ok probes degraded eclass code <<< "$line"
    [ -n "$ok" ] || ok=0
    [ -n "$probes" ] || probes=0

    # Полный отказ — печатаем причину, а не выдуманные 9999 мс.
    if [ "$ok" = "0" ] || [ -z "$warm" ]; then
        local reason=""
        case "$eclass" in
            timeout)     reason="$(t bench_err_timeout)" ;;
            tls)         reason="$(t bench_err_tls)" ;;
            resolve)     reason="$(t bench_err_resolve)" ;;
            refused)     reason="$(t bench_err_refused)" ;;
            reset)       reason="$(t bench_err_reset)" ;;
            unreachable) reason="$(t bench_err_unreachable)" ;;
            http)        reason="$(tf bench_err_http "${code:-?}")" ;;
            proto)       reason="$(t bench_err_proto)" ;;
            tool)        reason="$(t bench_err_tool)" ;;
            badcand)     reason="$(t bench_err_badcand)" ;;
            *)           reason="$(t bench_err_other)" ;;
        esac
        printf '  — %s' "$reason"
        return 0
    fi

    # Три знакоместа под число; тильда съедает ведущий пробел, а если числу
    # и так тесно (трёхзначное) — встаёт перед ним.
    local num=""
    num=$(printf '%3s' "$warm")
    # Не `[ … ] && …`: при ложном условии такой список возвращает 1, и в
    # конце функции это стало бы кодом возврата. Явное if/then.
    if [ "$degraded" = "1" ]; then
        if [ "${num:0:1}" = " " ]; then num="~${num:1}"; else num="~${num}"; fi
    fi

    local outstr=""
    outstr="${num} $(t bench_ms)"

    # Холодное число показываем, только когда рукопожатие реально дорогое:
    # иначе колонка превращается в кашу из двух чисел на каждой строке.
    if [ -n "$cold" ] && [ "$cold" -ge $(( warm + 100 )) ] 2>/dev/null; then
        outstr="${outstr} $(tf bench_cold_paren "$cold")"
    fi

    # Часть проб не дошла — это надо видеть, среднее время тут врёт.
    if [ "$ok" -lt "$probes" ] 2>/dev/null; then
        outstr="${outstr} $(tf bench_partial "$ok" "$probes")"
    fi

    printf '%s' "$outstr"
    return 0
}

# ── Сноска под таблицу ──────────────────────────────────────────────────
# Число нужно продать честно: сказать, ЧТО именно измерено. Без этой строки
# «61 мс» читается как «пинг», а это не пинг.
bench_note() {
    printf '%s' "$(tf bench_method_note "$GODNS_QNAME")"
    return 0
}

# ── Сборка списка кандидатов ────────────────────────────────────────────
# bench_candidates_json  — читает со stdin строки вида
#     id|proto|host|port|url|ip
# и печатает путь к файлу с JSON-списком для bench_run.
#
# Зачем отдельная функция: собирать JSON конкатенацией строк в bash — верный
# способ получить сломанную кавычку на первом же имени с апострофом или на
# пустом поле. Экранирование делает python, ему это положено.
#
#   proto  — dot | doh | plain
#   host   — имя для SNI и проверки сертификата (обязательно для dot)
#   port   — порт DoT (по умолчанию 853). Для doh игнорируется:
#            нестандартный порт DoH пишется прямо в URL.
#   url    — для doh; если пуст, соберётся https://<host>/dns-query
#   ip     — необязателен; можно список через запятую, берётся первый.
#            Пин на IP убирает из замера боксовый DNS — а он на этом шаге
#            вполне может быть уже сломан, мы же его и настраиваем.
bench_candidates_json() {
    local out=""
    if declare -F godns_mktemp >/dev/null 2>&1; then
        out=$(godns_mktemp) || out=""
    fi
    if [ -z "$out" ]; then
        out=$(mktemp "/tmp/godns-cand.XXXXXX") || out=""
    fi
    if [ -z "$out" ]; then
        log_error "$(t bench_err_tmp)"
        return 1
    fi

    # Грабли: НЕ `python3 - "$out" <<'PY'`. Такой heredoc подменяет
    # stdin самим текстом программы, и строки кандидатов, переданные нам по
    # конвейеру, до python не доходят — он честно готовит 0 кандидатов.
    # Программу передаём через -c, а stdin оставляем конвейеру.
    local _prog=""
    _prog=$(cat <<'PY'
import json, sys

rows = []
seen = set()
for raw in sys.stdin:
    raw = raw.rstrip("\n")
    if not raw.strip() or raw.lstrip().startswith("#"):
        continue
    f = (raw.split("|") + [""] * 6)[:6]
    cid, proto, host, port, url, ip = (x.strip() for x in f)
    proto = (proto or "dot").lower()
    if not cid or cid in seen:
        continue
    if proto == "dot" and not host:
        continue
    if proto == "doh" and not (url or host):
        continue
    if proto == "plain" and not (ip or host):
        continue
    seen.add(cid)
    c = {"id": cid, "proto": proto}
    if host:
        c["host"] = host
    if url:
        c["url"] = url
    # Поле порта — это ВСЕГДА порт DoT (в реестре провайдеров колонка так и
    # называется). Для doh его не берём ни при каких условиях: приклеить 853
    # к DoH-адресу значит получить https://host:853/dns-query и намерить
    # таймаут там, где сервис жив. Нестандартный порт DoH задаётся прямо
    # в URL (https://host:8443/path) — оттуда он и прочитается.
    if port and port.isdigit() and proto in ("dot", "plain"):
        c["port"] = int(port)
    if ip:
        # Список бутстрап-адресов — берём первый пригодный.
        first = ip.split(",")[0].strip()
        if first:
            c["ip"] = first
    rows.append(c)

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False)
# Отладочную строку в stderr здесь НЕ печатать: stderr уходит на экран
# оператору, и «bench: 19 candidate(s) prepared» торчало по-английски
# посреди русского мастера. Число кандидатов оператору не нужно.
PY
) || _prog=""
    if [ -z "$_prog" ]; then
        log_error "$(t bench_err_bad_input)"
        return 1
    fi
    if ! python3 -c "$_prog" "$out"; then
        log_error "$(t bench_err_bad_input)"
        return 1
    fi

    printf '%s' "$out"
    return 0
}

# ── Строка кандидата для одной записи каталога ──────────────────────────
# Каталог меряется DoT (он есть у всех записей и им же ходят настоящие
# запросы). Свой апстрим меряется СВОИМ протоколом — иначе человек увидел
# бы у своего DoH-резолвера таймаут на 853, где ничего и не слушает.
bench_candidate_line() {  # <id> → «id|proto|host|port|url|ip» на stdout
    local id="${1:-}" proto="" host="" port="" ip="" url=""
    [ -n "$id" ] || return 1
    proto=$(prov_field "$id" proto 2>/dev/null) || proto=""
    [ -n "$proto" ] || proto="dot"
    host=$(prov_field "$id" dot_host 2>/dev/null) || host=""
    port=$(prov_field "$id" dot_port 2>/dev/null) || port=""
    ip=$(prov_bootstrap_ips "$id" all 2>/dev/null) || ip=""
    ip="${ip%%,*}"
    case "$proto" in
        doh)
            url=$(prov_upstream_doh "$id" 2>/dev/null) || url=""
            [ -n "$url" ] || return 1
            printf '%s|doh|%s||%s|%s\n' "$id" "$host" "$url" "$ip" ;;
        plain)
            [ -n "$ip" ] || return 1
            printf '%s|plain|%s|%s||%s\n' "$id" "$ip" "${port:-53}" "$ip" ;;
        *)
            [ -n "$host" ] || return 1
            printf '%s|dot|%s|%s||%s\n' "$id" "$host" "${port:-853}" "$ip" ;;
    esac
    return 0
}

# ── Живой замер ОДНОГО кандидата до записи ──────────────────────────────
# bench_probe_line «id|proto|host|port|url|ip» → строка задержки на stdout
# (как в списке: «~12 мс (хол. 40) 5/5» или «— таймаут»). rc 1 — кандидат
# не ответил ни разу. Нужен экрану «Добавить свой апстрим»: без пробы
# человек добавит мёртвый адрес и узнает об этом из жалоб клиентов.
bench_probe_line() {
    local line="${1:-}" cand="" res="" id="" out="" ok=""
    [ -n "$line" ] || return 1
    id="${line%%|*}"
    cand=$(printf '%s\n' "$line" | bench_candidates_json) || cand=""
    [ -n "$cand" ] && [ -s "$cand" ] || return 1
    res=$(GODNS_PARALLEL=1 bench_run "$cand") || res=""
    rm -f "$cand" 2>/dev/null || true
    [ -n "$res" ] && [ -s "$res" ] || return 1
    out=$(bench_fmt_latency "$res" "$id") || out=""
    ok=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(((d.get("results") or {}).get(sys.argv[2]) or {}).get("probes_ok",0))' "$res" "$id" 2>/dev/null) || ok=0
    rm -f "$res" 2>/dev/null || true
    printf '%s' "$out"
    [ "${ok:-0}" -gt 0 ]
}

# ── Замер всех провайдеров каталога ─────────────────────────────────────
# Меряем DoT: он есть у всех 20 записей реестра, и это тот же транспорт,
# которым пойдут настоящие запросы. Адрес берём литеральным IP — так замер
# не зависит от системного DNS, который мы как раз собираемся заменить.
bench_run_all() {
    local ids id host port ip line=""
    if ! declare -F prov_list_ids >/dev/null 2>&1; then
        log_error "$(t bench_err_no_providers)"
        return 1
    fi
    ids=$(prov_list_ids) || return 1

    local cand=""
    cand=$(
        for id in $ids; do
            bench_candidate_line "$id" || true
        done | bench_candidates_json
    ) || cand=""

    if [ -z "$cand" ] || [ ! -s "$cand" ]; then
        log_error "$(t bench_err_no_candidates)"
        return 1
    fi
    bench_run "$cand"
}
