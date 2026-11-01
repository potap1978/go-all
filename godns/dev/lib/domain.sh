#!/usr/bin/env bash
# goDNS — срок регистрации домена (WHOIS) отдельно от срока сертификата.
# lib/domain.sh — до какого числа ОПЛАЧЕН домен.
#
# Зачем отдельный модуль. В панели рядом с именем домена стояло
# «осталось дней: 89», и это число считало СЕРТИФИКАТ. Две разные даты
# в одной строке: регистрация домена и срок сертификата живут своей
# жизнью, истекают в разные дни, и последствия у них разные. Просроченный
# сертификат — клиенты не подключаются, чинится за минуту перевыпуском.
# Просроченный домен — имя уходит с делегирования, и чинить его надо у
# регистратора, деньгами и не всегда быстро.
#
# ГРАБЛИ, заложенные в устройство модуля:
#
# WHOIS — это СЕТЬ, а панель рисуется на каждый показ меню. Синхронный
# запрос повесил бы главный экран на секунды, а на сервере с закрытым
# исходящим 43-м портом — на весь таймаут. Поэтому панель читает только
# кэш, а обновление уходит в фон.
#
# Отметка «когда проверяли» пишется ДО запроса, а не после. Иначе
# постоянный отказ (порт закрыт) означал бы новый фоновый процесс на
# каждую перерисовку меню.
#
# Своего сервера WHOIS у зоны мы не знаем и знать не можем: список TLD
# меняется. Спрашиваем IANA, она отвечает адресом сервера зоны.

DOMAIN_CACHE="${DOMAIN_CACHE:-/var/lib/godns/domain-expiry.tsv}"
DOMAIN_CACHE_TTL="${DOMAIN_CACHE_TTL:-604800}"   # 7 суток на удачный ответ
DOMAIN_RETRY_TTL="${DOMAIN_RETRY_TTL:-21600}"    # 6 часов на неудачу

# ── Запрос к WHOIS ─────────────────────────────────────────────────────
# Пишет в кэш строку: домен TAB ISO-дата TAB epoch TAB статус
# Статус: ok | unknown (ответ есть, даты в нём нет) | fail (не ответили)
domain_expiry_refresh() {
    local domain="$1"
    [ -n "$domain" ] || return 1
    mkdir -p "$(dirname "$DOMAIN_CACHE")" 2>/dev/null || return 1

    # Отметиться ДО запроса: даже если сейчас не ответят, следующая
    # попытка будет не раньше чем через DOMAIN_RETRY_TTL.
    _domain_cache_put "$domain" "" "fail"

    local out=""
    out=$(python3 - "$domain" <<'PY' 2>/dev/null
import socket, sys, re

domain = sys.argv[1].strip().lower().rstrip(".")
TIMEOUT = 8

def ask(server, query):
    with socket.create_connection((server, 43), TIMEOUT) as s:
        s.settimeout(TIMEOUT)
        s.sendall((query + "\r\n").encode())
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
            if sum(len(c) for c in chunks) > 262144:
                break
    return b"".join(chunks).decode("utf-8", "replace")

# 1. У какой зоны спрашивать. Жёсткого списка TLD не держим: зоны
#    появляются и исчезают, а IANA знает актуальный ответ.
tld = domain.rsplit(".", 1)[-1]
try:
    ref = ask("whois.iana.org", tld)
except Exception:
    print("fail"); raise SystemExit
m = re.search(r"^whois:\s*(\S+)", ref, re.M | re.I)
if not m:
    print("fail"); raise SystemExit
server = m.group(1)

# 2. Сам домен.
try:
    body = ask(server, domain)
except Exception:
    print("fail"); raise SystemExit

# Тонкий WHOIS (verisign у .com/.net) отдаёт только адрес регистратора,
# а дату держит уже он. Идём по ссылке один раз.
m = re.search(r"^\s*Registrar WHOIS Server:\s*(\S+)", body, re.M | re.I)
if m and m.group(1).lower() not in (server.lower(), ""):
    try:
        deep = ask(m.group(1), domain)
        if len(deep) > 40:
            body = deep + "\n" + body
    except Exception:
        pass

# 3. Дата. Формулировка у каждого регистратора своя.
FIELDS = (
    r"Registry Expiry Date", r"Registrar Registration Expiration Date",
    r"Expiry Date", r"Expiration Date", r"Expiration Time",
    r"expires", r"expire", r"paid-till", r"renewal date", r"valid until",
)
raw = None
for f in FIELDS:
    m = re.search(r"^\s*" + f + r"\s*:\s*(.+?)\s*$", body, re.M | re.I)
    if m:
        raw = m.group(1)
        break
if not raw:
    print("unknown"); raise SystemExit

# Нормализуем к YYYY-MM-DD. Разбираем сами: dateutil на сервере нет.
MON = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
m3 = re.search(r"(\d{1,2})[-\s]([A-Za-z]{3})[a-z]*[-\s](\d{4})", raw)  # 01-Jan-2027
if m3 and m3.group(2).lower() in MON:
    print("ok %s-%02d-%02d" % (m3.group(3), MON[m3.group(2).lower()], int(m3.group(1))))
    raise SystemExit
m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)            # ISO и .ru paid-till
if not m:
    m2 = re.search(r"(\d{2})[./-](\d{2})[./-](\d{4})", raw)  # DD.MM.YYYY
    if m2:
        print("ok %s-%s-%s" % (m2.group(3), m2.group(2), m2.group(1))); raise SystemExit
    print("unknown"); raise SystemExit
print("ok %s-%s-%s" % (m.group(1), m.group(2), m.group(3)))
PY
) || out=""

    case "$out" in
        ok\ *)   _domain_cache_put "$domain" "${out#ok }" "ok" ;;
        unknown) _domain_cache_put "$domain" "" "unknown" ;;
        *)       _domain_cache_put "$domain" "" "fail" ;;
    esac
    return 0
}

_domain_cache_put() {
    local domain="$1" dt="$2" st="$3" tmp=""
    # ГРАБЛИ: `IFS=$'\t' read` СХЛОПЫВАЕТ пустые поля — таб входит в класс
    # пробельных разделителей. При неудачном ответе дата пуста, колонки
    # съезжают, и в `ts` попадает слово «fail». Дальше арифметика возраста
    # даёт ноль, кэш всегда считается протухшим, и панель порождает
    # фоновый процесс на КАЖДУЮ перерисовку меню. Поэтому пустых полей
    # в файле нет: отсутствие даты записывается прочерком.
    [ -n "$dt" ] || dt="-"
    tmp="${DOMAIN_CACHE}.$$"
    { grep -v -- "^${domain}	" "$DOMAIN_CACHE" 2>/dev/null || true
      printf '%s\t%s\t%s\t%s\n' "$domain" "$dt" "$(date +%s)" "$st"
    } > "$tmp" 2>/dev/null || { rm -f "$tmp"; return 1; }
    # ГРАБЛИ проекта: запись при нехватке места завершается успешно и
    # оставляет пустой файл. Пустой кэш стёр бы известную дату.
    [ -s "$tmp" ] || { rm -f "$tmp"; return 1; }
    mv -f "$tmp" "$DOMAIN_CACHE" 2>/dev/null || { rm -f "$tmp"; return 1; }
    chmod 0644 "$DOMAIN_CACHE" 2>/dev/null || true
    return 0
}

# ── Чтение для панели ──────────────────────────────────────────────────
# Печатает: статус TAB дата. Сети не касается. Если кэш протух —
# запускает обновление в фоне и отдаёт то, что есть сейчас.
domain_expiry_cached() {
    local domain="$1" line="" dt="" ts="" st="" age="" ttl=""
    [ -n "$domain" ] || { printf 'fail\t'; return 1; }

    line=$(grep -m1 -- "^${domain}	" "$DOMAIN_CACHE" 2>/dev/null) || line=""
    if [ -n "$line" ]; then
        IFS=$'\t' read -r _ dt ts st <<<"$line"
        [ "$dt" = "-" ] && dt=""
    fi

    if [ "${st:-}" = "ok" ]; then ttl="$DOMAIN_CACHE_TTL"; else ttl="$DOMAIN_RETRY_TTL"; fi
    age=$(( $(date +%s) - ${ts:-0} ))

    if [ -z "$ts" ] || [ "$age" -gt "$ttl" ]; then
        _domain_refresh_bg "$domain"
    fi
    printf '%s\t%s' "${st:-pending}" "${dt:-}"
    return 0
}

# Фоновое обновление. Отвязано от терминала: иначе процесс умрёт вместе
# с меню, а вывод WHOIS высыпется поверх экрана.
_domain_refresh_bg() {
    local domain="$1" lock="${DOMAIN_CACHE}.lock"
    command -v python3 >/dev/null 2>&1 || return 0
    mkdir -p "$(dirname "$DOMAIN_CACHE")" 2>/dev/null || return 0

    # Протухший замок снимаем. ГРАБЛИ: замок — каталог, а EXIT-trap не
    # срабатывает при SIGKILL/OOM/перезагрузке в 8-секундном окне WHOIS.
    # Осиротевший каталог переживал перезагрузку, и mkdir вечно возвращал
    # «уже идёт» — срок домена не обновлялся больше НИКОГДА, панель
    # показывала старую дату молча. Старше двух минут = точно сирота:
    # живой запрос столько не длится (в самом refresh таймаут короче).
    if [ -d "$lock" ]; then
        local age=0 mt=0 now=0
        mt=$(stat -c %Y "$lock" 2>/dev/null || stat -f %m "$lock" 2>/dev/null) || mt=0
        now=$(date +%s 2>/dev/null) || now=0
        age=$((now - mt))
        if [ "$mt" -gt 0 ] && [ "$age" -gt 120 ]; then
            rmdir "$lock" 2>/dev/null || true
        fi
    fi

    mkdir "$lock" 2>/dev/null || return 0   # уже идёт
    (
        trap 'rmdir "$lock" 2>/dev/null' EXIT
        domain_expiry_refresh "$domain"
    ) >/dev/null 2>&1 &
    return 0
}

# Человеческая дата из ISO: 2027-03-12 → 12.03.2027
domain_date_human() {
    local iso="$1"
    case "$iso" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
            printf '%s.%s.%s' "${iso:8:2}" "${iso:5:2}" "${iso:0:4}" ;;
        *) printf '%s' "$iso" ;;
    esac
}

# Дней до даты ISO. Пусто, если разобрать нечего.
domain_days_left() {
    local iso="$1"
    [ -n "$iso" ] || return 1
    python3 -c '
import sys, datetime
try:
    d = datetime.date.fromisoformat(sys.argv[1])
    print((d - datetime.date.today()).days)
except Exception:
    pass
' "$iso" 2>/dev/null
}
