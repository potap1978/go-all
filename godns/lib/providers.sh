#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — Каталог публичных резолверов (DoT/DoH).
#
# ═══════════════════════════════════════════════════════════════════════
# ЧТО ЗДЕСЬ ЛЕЖИТ И ПОЧЕМУ ИМЕННО ТАК
# ═══════════════════════════════════════════════════════════════════════
#
# Таблица провайдеров — это ДАННЫЕ, а не код. Она живёт одним текстовым
# блоком PROV_TABLE (поля через `|`), который на загрузке разбирается в
# ассоциативный массив PROVIDERS с ключами вида "<id>.<поле>".
#
# Почему текстом, а не двумя сотнями строк `PROVIDERS[x.y]="z"`:
#   • таблицу можно проверить внешним скриптом (питон/awk) без bash 4;
#   • опечатку в числе полей ловит загрузчик, а не оператор в проде;
#   • диффы читаемы — одна строка = один провайдер.
#
# ВСЕ обращения к ассоциативному массиву идут ровно через три функции —
# _prov_set / _prov_get / _prov_has. Это не украшение: так модуль можно
# прогнать на bash 3.2 (macOS), подменив только эти три тела заглушками.
#
# ───────────────────────────────────────────────────────────────────────
# ЖИВАЯ ПРОВЕРКА 2026-08-26 (каждая точка проверена дважды)
# ───────────────────────────────────────────────────────────────────────
# DoH: RFC 8484 GET, base64url без padding, `accept: application/dns-message`.
#      Все адреса вернули HTTP 200 и настоящий wire-format (flags 0x8180,
#      ancount=2) — не просто 200, а разобранный ответ.
# DoT: openssl s_client -verify_hostname -verify_return_error + реальный
#      запрос с 2-байтовым префиксом длины. Все хосты: Verification OK,
#      rcode=0, ancount=2.
#
# ГРАБЛИ, найденные этой проверкой:
#
#   • dns0.eu МЁРТВ. Закрыт в октябре 2025, живой тест: HTTP 000 (таймаут
#     соединения). В таблице его НЕТ и быть не должно. Слот «европейский
#     суверенный резолвер» занимает DNS4EU (unfiltered.joindns4.eu),
#     на который указывает прощальная страница самого dns0.eu.
#
#   • dot.sb работает ТОЛЬКО по TLS 1.3. Попытка TLS 1.2 получает
#     alert 70 (protocol version) и рвёт соединение. Если движок где-то
#     прижмёт максимум до 1.2 — DNS.SB отвалится, и выглядеть это будет
#     как «провайдер лежит». Отмечено флагом tls13only.
#
#   • LibreDNS с блокировкой рекламы — НЕСТАНДАРТНЫЙ порт 854 на том же
#     dot.libredns.gr. Проверено: на :854 doubleclick.net отдаётся как
#     0.0.0.0, на :853 — нормальный адрес. Сертификат CN=libredns.gr
#     с SAN *.libredns.gr, строгая проверка имени проходит на обоих портах.
#
#   • Строгая проверка сертификата работает У ВСЕХ точек. Никакого
#     «отключить проверку» в этом модуле нет и не появится: сбой проверки
#     на DoT — это ровно тот сигнал перехвата, ради которого всё затевалось,
#     и он обязан падать громко.
#
#   • Bootstrap-адреса нужны всем. dns.comss.one и doh.dns.sb гео-роутятся,
#     а системный резолвер — это как раз то, что мы настраиваем: без
#     bootstrap получается курица и яйцо. Все bootstrap-IP из таблицы
#     проверены на доступность TCP/853.
#
#   • Comss.one не публикует ни одного AAAA и не публикует политику
#     логирования. Для RU-дефолта это дыра, и оператор должен её видеть —
#     флаги no_ipv6 + undocumented.
#
#   • Провайдеров, требующих учётной записи, в таблице НЕТ. NextDNS был
#     единственным: он требует ID профиля, без которого его адрес — шаблон
#     с плейсхолдером, и в списке он выглядел сломанным, а не
#     ненастроенным. Заказчик решил его не поддерживать. Вместе с ним ушёл
#     и весь механизм «нужен профиль» (prov_requires_account,
#     prov_needs_input, prov_set_nextdns_id, подстановка {id}): держать
#     живой на вид, но никем не используемый код — тот самый класс, за
#     которым охотится check-unreachable.
#
# Требует: lib/common.sh (log_*, config_get). i18n (t) — опционально:
# без него _prov_t отдаёт сам ключ, и модуль остаётся тестируемым.
# ═══════════════════════════════════════════════════════════════════════

# bash 4.0+ — ассоциативный массив. На bash 3.2 `PROVIDERS[ключ]=…` молча
# пишет в индекс 0 и превращает каталог в один мусорный элемент.
if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "goDNS Pro: lib/providers.sh требует bash 4.0+ (найден ${BASH_VERSION:-unknown})" >&2
    echo "goDNS Pro: lib/providers.sh requires bash 4.0+ (found ${BASH_VERSION:-unknown})" >&2
    return 1 2>/dev/null || exit 1
fi

# ── Хранилище ───────────────────────────────────────────────────────────
declare -gA PROVIDERS=()
declare -ga PROVIDER_IDS=()

# Порядок полей в PROV_TABLE. Загрузчик сверяет с ним каждую строку.
declare -ga PROV_FIELDS=(
    id name_ru name_en dot_host dot_port doh_url
    ipv4 ipv6 filter privacy account region flags
)

# Классы фильтрации и регионы — закрытые множества, проверяются в prov_selftest.
declare -ga PROV_FILTER_CLASSES=(none ads malware ads_malware custom)
declare -ga PROV_REGIONS=(ru eu global own)


# Кэш геоподсказки на время процесса.
PROV_GEO_HINT="${PROV_GEO_HINT:-}"

# ── Три единственные точки доступа к ассоциативному массиву ─────────────
# Тесты на bash 3.2 подменяют ровно эти тела — больше нигде синтаксиса
# ассоциативных массивов в файле нет.
_prov_set() { PROVIDERS["$1"]="$2"; return 0; }
_prov_get() { printf '%s' "${PROVIDERS[$1]-}"; return 0; }
_prov_has() { [ -n "${PROVIDERS[$1]+x}" ]; }

# ── i18n-обёртка ────────────────────────────────────────────────────────
# t() приходит из lib/i18n.sh. Если модуль поднят в одиночку (тесты,
# отладка) — отдаём сам ключ, чтобы ничего не падало и было видно, какой
# ключ понадобился.
_prov_t() {
    local key="${1:-}" out=""
    [ -n "$key" ] || { printf '%s' ""; return 0; }
    if declare -F t >/dev/null 2>&1; then
        out=$(t "$key" 2>/dev/null) || out=""
    fi
    [ -z "$out" ] && out="$key"
    printf '%s' "$out"
    return 0
}

_prov_tf() {
    local key="${1:-}"; shift || true
    local out=""
    [ -n "$key" ] || { printf '%s' ""; return 0; }
    if declare -F tf >/dev/null 2>&1; then
        out=$(tf "$key" "$@" 2>/dev/null) || out=""
    fi
    [ -z "$out" ] && out="$key"
    printf '%s' "$out"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ТАБЛИЦА
# ═══════════════════════════════════════════════════════════════════════
# id|name_ru|name_en|dot_host|dot_port|doh_url|ipv4|ipv6|filter|privacy|account|region|flags
#
# ipv6 может быть пустым (Comss.one, NextDNS) — поле сохраняется пустым.
# flags никогда не пустой: «нет флагов» пишется как `-`.
# privacy — это КЛЮЧ i18n, а не текст: сама формулировка переводится.
# Адреса, хосты и URL НЕ переводятся и живут прямо здесь.
#
# Строки не отбивать пробелами — IFS='|' сохранит их внутри поля.

read -r -d '' PROV_TABLE <<'PROV_TABLE_EOF' || true
cloudflare|Cloudflare|Cloudflare|one.one.one.one|853|https://cloudflare-dns.com/dns-query|1.1.1.1,1.0.0.1|2606:4700:4700::1111,2606:4700:4700::1001|none|prov_priv_cloudflare|no|global|-
cloudflare-security|Cloudflare Security|Cloudflare Security|security.cloudflare-dns.com|853|https://security.cloudflare-dns.com/dns-query|1.1.1.2,1.0.0.2|2606:4700:4700::1112,2606:4700:4700::1002|malware|prov_priv_cloudflare|no|global|-
google|Google Public DNS|Google Public DNS|dns.google|853|https://dns.google/dns-query|8.8.8.8,8.8.4.4|2001:4860:4860::8888,2001:4860:4860::8844|none|prov_priv_google|no|global|-
quad9|Quad9 с защитой|Quad9 secured|dns.quad9.net|853|https://dns.quad9.net/dns-query|9.9.9.9,149.112.112.112|2620:fe::fe,2620:fe::9|malware|prov_priv_quad9|no|global|-
quad9-unfiltered|Quad9 без фильтрации|Quad9 unfiltered|dns10.quad9.net|853|https://dns10.quad9.net/dns-query|9.9.9.10,149.112.112.10|2620:fe::10,2620:fe::fe:10|none|prov_priv_quad9|no|global|-
adguard|AdGuard DNS|AdGuard DNS|dns.adguard-dns.com|853|https://dns.adguard-dns.com/dns-query|94.140.14.14,94.140.15.15|2a10:50c0::ad1:ff,2a10:50c0::ad2:ff|ads|prov_priv_adguard|no|global|-
adguard-unfiltered|AdGuard без фильтрации|AdGuard non-filtering|unfiltered.adguard-dns.com|853|https://unfiltered.adguard-dns.com/dns-query|94.140.14.140,94.140.14.141|2a10:50c0::1:ff,2a10:50c0::2:ff|none|prov_priv_adguard|no|global|-
mullvad|Mullvad DNS|Mullvad DNS|dns.mullvad.net|853|https://dns.mullvad.net/dns-query|194.242.2.2|2a07:e340::2|none|prov_priv_mullvad|no|eu|-
mullvad-adblock|Mullvad Adblock|Mullvad Adblock|adblock.dns.mullvad.net|853|https://adblock.dns.mullvad.net/dns-query|194.242.2.3|2a07:e340::3|ads|prov_priv_mullvad|no|eu|-
dnssb|DNS.SB (xTom, Германия)|DNS.SB (xTom, Germany)|dot.sb|853|https://doh.dns.sb/dns-query|185.222.222.222,45.11.45.11|2a09::,2a11::|none|prov_priv_dnssb|no|eu|tls13only
opendns|OpenDNS (Cisco)|OpenDNS (Cisco)|dns.opendns.com|853|https://doh.opendns.com/dns-query|208.67.222.222,208.67.220.220|2620:119:35::35,2620:119:53::53|malware|prov_priv_opendns|no|global|-
yandex|Яндекс DNS — базовый|Yandex DNS — basic|common.dot.dns.yandex.net|853|https://common.dot.dns.yandex.net/dns-query|77.88.8.8,77.88.8.1|2a02:6b8::feed:0ff,2a02:6b8:0:1::feed:0ff|none|prov_priv_yandex|no|ru|ru_jurisdiction
yandex-safe|Яндекс DNS — безопасный|Yandex DNS — safe|safe.dot.dns.yandex.net|853|https://safe.dot.dns.yandex.net/dns-query|77.88.8.88,77.88.8.2|2a02:6b8::feed:bad,2a02:6b8:0:1::feed:bad|malware|prov_priv_yandex|no|ru|ru_jurisdiction
comss|Comss.one DNS|Comss.one DNS|dns.comss.one|853|https://dns.comss.one/dns-query|83.220.169.155,212.109.195.93,195.133.25.16||ads_malware|prov_priv_comss|no|ru|ru_jurisdiction no_ipv6 undocumented
digitalegesellschaft|Digitale Gesellschaft, CH|Digitale Gesellschaft, CH|dns.digitale-gesellschaft.ch|853|https://dns.digitale-gesellschaft.ch/dns-query|185.95.218.42,185.95.218.43|2a05:fc84::42,2a05:fc84::43|none|prov_priv_digitalegesellschaft|no|eu|volunteer
libredns|LibreDNS (Греция)|LibreDNS (Greece)|dot.libredns.gr|853|https://doh.libredns.gr/dns-query|116.202.176.26|2a01:4f8:1c0c:8274::1|none|prov_priv_libredns|no|eu|volunteer
libredns-noads|LibreDNS NoAds|LibreDNS NoAds|dot.libredns.gr|854|https://doh.libredns.gr/noads|116.202.176.26|2a01:4f8:1c0c:8274::1|ads|prov_priv_libredns|no|eu|volunteer nonstd_port
controld-p0|Control D free p0|Control D free p0|p0.freedns.controld.com|853|https://freedns.controld.com/p0|76.76.2.0,76.76.10.0|2606:1a40::0,2606:1a40:1::0|none|prov_priv_controld|no|global|-
dns4eu|DNS4EU без фильтрации|DNS4EU unfiltered|unfiltered.joindns4.eu|853|https://unfiltered.joindns4.eu/dns-query|86.54.11.100,86.54.11.200|2a13:1001::86:54:11:100,2a13:1001::86:54:11:200|none|prov_priv_dns4eu|no|eu|-
PROV_TABLE_EOF

# ── Наборы по умолчанию ─────────────────────────────────────────────────
# RU: Яндекс и Comss физически в РФ (минимальный RTT, никакого DPI на
# трансграничном плече), плюс AdGuard без фильтрации как самый надёжный
# не-российский запасной — у него есть московская точка присутствия.
# Cloudflare и Google в RU-дефолт СОЗНАТЕЛЬНО не входят: с августа 2026
# российские операторы (Ростелеком, Дом.ru, Таттелеком, SkyNet) рвут к ним
# и DoH, и DoT — TCP встаёт, TLS-рукопожатие умирает. Это по-операторски,
# не повсеместно, поэтому провайдеры остаются в списке выбираемыми, а
# порядок обязан править замер с самого сервера, а не эта константа.
#
# EU: две независимые крупные anycast-сети плюс один оператор со строгим
# «не логируем». Три штуки, операционно не зависящие друг от друга.
#
# Везде, где есть выбор, берётся вариант БЕЗ фильтрации. Фильтрующий
# апстрим молча отдаёт NXDOMAIN на домены, которые реально запросил
# клиент за туннелем, и выглядит это не как фильтр, а как сломанный
# туннель — диагностировать такое оператор будет часами.
PROV_DEFAULTS_RU="yandex comss adguard-unfiltered"
PROV_DEFAULTS_EU="cloudflare quad9 mullvad"
# Если оператор хочет только европейскую юрисдикцию — Cloudflare меняется
# на DNS4EU, Quad9 остаётся (швейцарский фонд).
PROV_DEFAULTS_EU_SOVEREIGN="dns4eu quad9 mullvad"

# ═══════════════════════════════════════════════════════════════════════
# ЗАГРУЗЧИК
# ═══════════════════════════════════════════════════════════════════════
_prov_load() {
    local line="" n=0 bad=0
    local id name_ru name_en dot_host dot_port doh_url
    local ipv4 ipv6 filter privacy account region flags extra

    PROVIDERS=()
    PROVIDER_IDS=()

    # Herestring, не пайп: цикл обязан идти в ТЕКУЩЕЙ оболочке, иначе
    # PROVIDER_IDS наполнится в подоболочке и до вызывающего не дойдёт.
    while IFS='|' read -r id name_ru name_en dot_host dot_port doh_url \
                          ipv4 ipv6 filter privacy account region flags extra; do
        case "$id" in ''|'#'*) continue ;; esac

        # Лишний `|` в строке уедет в extra — ловим сразу, а не в проде.
        if [ -n "${extra:-}" ]; then
            _prov_warn "providers: лишние поля в записи '${id}'"
            bad=$((bad + 1))
            continue
        fi
        if [ -z "$name_en" ] || [ -z "$dot_host" ] || [ -z "$dot_port" ] \
           || [ -z "$doh_url" ] || [ -z "$ipv4" ] || [ -z "$filter" ] \
           || [ -z "$privacy" ] || [ -z "$account" ] || [ -z "$region" ] \
           || [ -z "$flags" ]; then
            _prov_warn "providers: неполная запись '${id}'"
            bad=$((bad + 1))
            continue
        fi

        _prov_set "${id}.id"       "$id"
        _prov_set "${id}.name_ru"  "${name_ru:-$name_en}"
        _prov_set "${id}.name_en"  "$name_en"
        _prov_set "${id}.dot_host" "$dot_host"
        _prov_set "${id}.dot_port" "$dot_port"
        _prov_set "${id}.doh_url"  "$doh_url"
        _prov_set "${id}.ipv4"     "$ipv4"
        _prov_set "${id}.ipv6"     "$ipv6"
        _prov_set "${id}.filter"   "$filter"
        _prov_set "${id}.privacy"  "$privacy"
        _prov_set "${id}.account"  "$account"
        _prov_set "${id}.region"   "$region"
        _prov_set "${id}.flags"    "$flags"

        PROVIDER_IDS+=("$id")
        n=$((n + 1))
    done <<< "$PROV_TABLE"

    if [ "$n" -eq 0 ]; then
        _prov_warn "providers: каталог пуст — таблица не разобрана"
        return 1
    fi
    if [ "$bad" -ne 0 ]; then
        return 1
    fi
    return 0
}

# log_warning приходит из common.sh; если модуль поднят в одиночку —
# пишем сами, но всё равно в stderr: stdout здесь чужой.
_prov_warn() {
    if declare -F log_warning >/dev/null 2>&1; then
        log_warning "$*"
    else
        printf '  ! %s\n' "$*" >&2
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ДОСТУП К ЗАПИСЯМ
# ═══════════════════════════════════════════════════════════════════════

# rc0 — строка похожа на id провайдера.
#
# Зачем вообще проверять. id приходит не только из наших констант — его
# набирает оператор в меню и читает config_get из файла, который кто-то
# мог поправить руками. Ключ вида `a;b$(cmd)` не должен доезжать до
# индексации массива ни здесь, ни в тестовых заглушках: сегодня это просто
# промах по ключу, а завтра кто-нибудь заведёт lookup через eval и получит
# исполнение команды из имени резолвера. Множество символов закрыто здесь
# один раз, чтобы дальше про это можно было не думать.
_prov_valid_id() {
    case "${1:-}" in
        ''|*[!a-z0-9-]*) return 1 ;;
        -*|*-)           return 1 ;;
    esac
    return 0
}

# rc0 — провайдер есть в каталоге.
prov_exists() {
    local id="${1:-}"
    _prov_valid_id "$id" || return 1
    if _prov_has "${id}.id"; then return 0; fi
    return 1
}

# Значение поля. ВСЕГДА rc 0 и всегда что-то печатает (возможно пустое) —
# это осознанно: типичное применение `x=$(prov_field …)` под set -e,
# и ненулевой код здесь убил бы весь скрипт на несуществующем поле.
prov_field() {
    local id="${1:-}" field="${2:-}" val=""
    if [ -z "$field" ] || ! _prov_valid_id "$id"; then
        printf '%s' ""
        return 0
    fi
    case "$field" in
        *[!a-z0-9_]*) printf '%s' ""; return 0 ;;
    esac
    val=$(_prov_get "${id}.${field}") || val=""
    printf '%s' "$val"
    return 0
}

# Все id в порядке отображения, по одному в строке.
prov_list_ids() {
    local id=""
    for id in "${PROVIDER_IDS[@]:-}"; do
        if [ -n "$id" ]; then printf '%s\n' "$id"; fi
    done
    return 0
}

prov_count() {
    local id="" n=0
    for id in "${PROVIDER_IDS[@]:-}"; do
        if [ -n "$id" ]; then n=$((n + 1)); fi
    done
    printf '%s' "$n"
    return 0
}

# Отображаемое имя по текущему языку интерфейса.
prov_name() {
    local id="${1:-}" lang="${LANG_CODE:-ru}" out=""
    if [ "$lang" = "en" ]; then
        out=$(prov_field "$id" name_en) || out=""
    else
        out=$(prov_field "$id" name_ru) || out=""
    fi
    [ -z "$out" ] && out=$(prov_field "$id" name_en)
    [ -z "$out" ] && out="$id"
    printf '%s' "$out"
    return 0
}

# rc0 — у провайдера есть указанный флаг.
prov_flag_has() {
    local id="${1:-}" want="${2:-}" flags="" f=""
    [ -n "$want" ] || return 1
    flags=$(prov_field "$id" flags) || flags=""
    for f in $flags; do
        if [ "$f" = "$want" ]; then return 0; fi
    done
    return 1
}





# ═══════════════════════════════════════════════════════════════════════
# ДЕСКРИПТОРЫ АПСТРИМОВ
# ═══════════════════════════════════════════════════════════════════════

# "tls://host:port". Порт печатается ВСЕГДА, даже штатный 853: у LibreDNS
# NoAds это 854, и запись без порта — самая дешёвая возможность промахнуться.
prov_upstream_dot() {
    local id="${1:-}" host="" port=""
    prov_exists "$id" || { printf '%s' ""; return 0; }
    host=$(prov_field "$id" dot_host) || host=""
    port=$(prov_field "$id" dot_port) || port=""
    [ -n "$port" ] || port="853"
    printf 'tls://%s:%s' "$host" "$port"
    return 0
}

# URL DoH как есть (у LibreDNS NoAds путь /noads, а не /dns-query).
prov_upstream_doh() {
    local id="${1:-}" url=""
    prov_exists "$id" || { printf '%s' ""; return 0; }
    url=$(prov_field "$id" doh_url) || url=""
    printf '%s' "$url"
    return 0
}

# Имя хоста без схемы и порта — для проверки сертификата (SNI/subjectName).
prov_dot_hostname() {
    local id="${1:-}" host=""
    host=$(prov_field "$id" dot_host) || host=""
    host=$host || host=""
    printf '%s' "$host"
    return 0
}

# Хост из DoH-URL — тоже нужен для bootstrap и для SNI.
prov_doh_hostname() {
    local url="" host=""
    url=$(prov_upstream_doh "${1:-}") || url=""
    host="${url#*://}"
    host="${host%%/*}"
    host="${host%%:*}"
    printf '%s' "$host"
    return 0
}

# Bootstrap-адреса, через запятую. Второй аргумент: v4 (по умолчанию) | v6 | all.
#
# Без них — курица и яйцо: имя апстрима надо во что-то разрешить, а
# системный резолвер — это ровно то, что мы сейчас настраиваем. Плюс
# dns.comss.one и doh.dns.sb гео-роутятся, так что «оно и так разрешится»
# здесь не работает.
prov_bootstrap_ips() {
    local id="${1:-}" want="${2:-v4}" v4="" v6="" out=""
    prov_exists "$id" || { printf '%s' ""; return 0; }
    v4=$(prov_field "$id" ipv4) || v4=""
    v6=$(prov_field "$id" ipv6) || v6=""
    case "$want" in
        v6)  out="$v6" ;;
        all)
            out="$v4"
            if [ -n "$v6" ]; then
                if [ -n "$out" ]; then out="${out},${v6}"; else out="$v6"; fi
            fi
            ;;
        *)   out="$v4" ;;
    esac
    printf '%s' "$out"
    return 0
}

# Первый bootstrap-адрес — когда нужен ровно один.
prov_bootstrap_first() {
    local list=""
    list=$(prov_bootstrap_ips "${1:-}" "${2:-v4}") || list=""
    printf '%s' "${list%%,*}"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ВЫБОРКИ
# ═══════════════════════════════════════════════════════════════════════

# Предвыбранные id для региона. Печатает их через пробел одной строкой.
prov_default_set() {
    # Заказчик: по умолчанию всегда эти три, всё остальное — на усмотрение
    # оператора. Все три без фильтрации: фильтрующий апстрим молча отдаёт
    # NXDOMAIN на домены клиентов туннеля, и это выглядит как сломанный
    # туннель, а не как фильтр. Регион на выбор больше не влияет — довод
    # «в РФ ниже задержка у Яндекса» проигрывает доводу «Яндекс не проверяет
    # подписи DNSSEC», а цифры задержки оператор и так видит в списке.
    printf '%s' "cloudflare google quad9-unfiltered"
    return 0
}


# id по региону, по одному в строке.
prov_ids_by_region() {
    local want="${1:-}" id="" r=""
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        r=$(prov_field "$id" region) || r=""
        if [ "$r" = "$want" ]; then printf '%s\n' "$id"; fi
    done
    return 0
}

# id без фильтрации — то, что вообще стоит предлагать под туннель.
prov_ids_unfiltered() {
    local id="" f=""
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        f=$(prov_field "$id" filter) || f=""
        if [ "$f" = "none" ]; then printf '%s\n' "$id"; fi
    done
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ТЕКСТЫ (через i18n)
# ═══════════════════════════════════════════════════════════════════════
prov_filter_text() {
    local c=""
    # У своей записи в этой колонке — протокол: класса фильтрации у неё
    # нет (мы его не знаем), а вот DoT/DoH/открытый :53 оператору важен.
    if [ "$(prov_field "${1:-}" custom)" = "yes" ]; then
        c=$(prov_field "${1:-}" proto) || c=""
        _prov_t "prov_custom_${c:-dot}"
        return 0
    fi
    c=$(prov_field "${1:-}" filter) || c=""
    [ -n "$c" ] || c="none"
    _prov_t "prov_filter_${c}"
    return 0
}

prov_region_text() {
    local r=""
    r=$(prov_field "${1:-}" region) || r=""
    [ -n "$r" ] || r="global"
    _prov_t "prov_region_${r}"
    return 0
}

prov_privacy_text() {
    local k=""
    k=$(prov_field "${1:-}" privacy) || k=""
    [ -n "$k" ] || { printf '%s' ""; return 0; }
    _prov_t "$k"
    return 0
}

# Флаги одной строкой через « · ». `-` означает «флагов нет».
prov_flags_text() {
    local flags="" f="" out="" piece=""
    flags=$(prov_field "${1:-}" flags) || flags=""
    for f in $flags; do
        [ "$f" = "-" ] && continue
        piece=$(_prov_t "prov_flag_${f}") || piece=""
        [ -n "$piece" ] || continue
        if [ -n "$out" ]; then out="${out} · ${piece}"; else out="$piece"; fi
    done
    printf '%s' "$out"
    return 0
}

# Предупредить оператора, если среди выбранных есть фильтрующий апстрим.
# Не запрещаем — предупреждаем: это его сервер.
prov_warn_if_filtered() {
    local id="" f="" hit=0
    for id in "$@"; do
        f=$(prov_field "$id" filter) || f=""
        case "$f" in
            none|'') ;;
            *) hit=1
               _prov_warn "$(_prov_tf prov_warn_filtered "$(prov_name "$id")")"
               ;;
        esac
    done
    # Пояснение — ОДИН раз после цикла, а не на каждый фильтрующий апстрим:
    # повторённое трижды, оно перестаёт читаться.
    if [ "$hit" -eq 1 ]; then
        _prov_warn "$(_prov_t prov_warn_filtered_2)"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ОТРИСОВКА СТРОКИ МЕНЮ
# ═══════════════════════════════════════════════════════════════════════

# Ширина строки в СИМВОЛАХ, независимо от локали.
#
# Грабли: `${#s}` считает символы только в multibyte-локали. На сервере
# сплошь и рядом LANG вообще не выставлен или равен C/POSIX (systemd-юниты,
# `sudo -i` на минимальном образе), и тогда `${#s}` возвращает БАЙТЫ:
# «Яндекс DNS — базовый» — это 20 символов, но 35 байт. Меню с русскими
# названиями разъезжается на пол-экрана, причём только на части хостов,
# то есть воспроизводится через раз.
#
# Считаем сами: в UTF-8 каждый символ — ровно один ведущий байт плюс
# сколько-то продолжающих (0x80–0xBF). Выкидываем продолжающие и меряем
# остаток под LC_ALL=C — получаем число символов при любой локали.
# Присваивание LC_ALL bash отрабатывает специально (зовёт setlocale),
# а `local` вернёт прежнее значение на выходе из функции.
_prov_width() {
    local LC_ALL=C
    local s="${1:-}"
    s="${s//[$'\x80'-$'\xbf']/}"
    printf '%s' "${#s}"
    return 0
}

# Дополнить строку пробелами до заданной ширины.
# Дополняет ДО ширины и обрезает, если длиннее. Без обрезки одно длинное
# имя («Digitale Gesellschaft (Швейцария)») сдвигало всю строку вправо, и
# колонка задержек переставала быть колонкой.
_prov_pad() {
    local s="${1:-}" w="${2:-20}" n=0
    n=$(_prov_width "$s") || n=0
    # ГРАБЛИ: условие было `-gt`, то есть строка РОВНО в ширину колонки не
    # обрезалась и не дополнялась ничем — и слипалась со следующей колонкой:
    # «Digitale Gesellschaft (CH)без фильтрации». Последнее знакоместо ячейки
    # всегда отдаём под разделитель, поэтому `-ge` и обрезка на два символа.
    if [ "$n" -ge "$w" ]; then
        # Режем по символам, а не по байтам: ${s:0:N} в bash работает с
        # символами при UTF-8-локали, чего нам и надо.
        printf '%s… ' "${s:0:$((w - 2))}"
        return 0
    fi
    printf '%s' "$s"
    while [ "$n" -lt "$w" ]; do
        printf ' '
        n=$((n + 1))
    done
    return 0
}

# Одна строка меню в идиоме goVLESS. Номер пункта печатает ВЫЗЫВАЮЩИЙ:
# нумерация — дело меню, а не каталога.
#
#   [✓] Cloudflare                без фильтрации     12 мс
#   [ ] LibreDNS NoAds            реклама            84 мс ⚠
#   [ ] NextDNS (нужен ID)        свой профиль        — не мерялось
#
# Почему в строке НЕТ адреса апстрима. Он не влезает: самый длинный —
# `tls://dns.digitale-gesellschaft.ch:853`, 38 символов, и вместе с именем
# (до 33 символов у русских названий), классом фильтрации и задержкой
# строка уезжает за 90 колонок. print_header в этом проекте рисует линейку
# в 55 символов — строка меню втрое шире линейки выглядит поломкой, а не
# таблицей. Адрес, порт, bootstrap-IP и политику логирования показывает
# prov_render_detail, и именно туда за ними идти.
#
# `⚠` — короткая пометка «не для боевого сервера»: доброволец на одном
# хосте без резерва (LibreDNS, Digitale Gesellschaft) или оператор без
# опубликованной политики логирования (Comss.one). Один символ вместо
# второй строки: 20 провайдеров по две строки не помещаются на экран.
# Аргументы: id, номер пункта меню, отмечен(yes|no), текст задержки.
prov_render_row() {
    local id="${1:-}" num="${2:-}" sel="${3:-no}" lat="${4:-}"
    local mark="" name="" filt="" warn="" pfx=""

    prov_exists "$id" || return 0

    if [ "$sel" = "yes" ]; then
        mark="${GREEN:-}[✓]${NC:-}"
    else
        mark="${DIM:-}[ ]${NC:-}"
    fi

    name=$(prov_name "$id") || name="$id"
    filt=$(prov_filter_text "$id") || filt=""
    [ -n "$lat" ] || lat=$(_prov_t prov_latency_unknown)

    # ГРАБЛИ: здесь стояла ещё и метка «нужен профиль» в хвосте строки.
    # Она дублировала имя («NextDNS (нужен ID)») и уводила строку за 80
    # колонок: 10 служебных + 26 имени + 19 фильтра + до 23 задержки уже
    # 78, а метка добавляла ещё четырнадцать. Хвост уезжал в колонку 0.
    # Про учётную запись говорит имя, а подробности — prov_render_detail.
    if prov_flag_has "$id" volunteer || prov_flag_has "$id" undocumented; then
        warn=" ${YELLOW:-}⚠${NC:-}"
    fi

    if [ -n "$num" ]; then
        pfx="$(printf '%2s' "$num")${CYAN:-})${NC:-} "
    else
        pfx="   "
    fi
    # Бюджет строки — 80 колонок, ни знаком больше: перенос уводит хвост
    # в колонку 0 и список перестаёт читаться как таблица.
    #   2 отступ + 4 «NN) » + 3 «[✓]» + 1 = 10
    #   26 имя + 19 фильтрация                = 55
    #   до 23 задержки («~123 мс (хол. 4000) 3/5»)
    #   2 на « ⚠»                             = 80
    echo -e "  ${pfx}${mark} ${WHITE:-}$(_prov_pad "$name" 26)${NC:-}$(_prov_pad "$filt" 19)${DIM:-}${lat}${NC:-}${warn}"
    return 0
}

# Развёрнутая карточка — когда оператор хочет подробностей по одному пункту.
prov_render_detail() {
    local id="${1:-}" v=""
    prov_exists "$id" || return 0
    echo -e "  ${BOLD:-}${WHITE:-}$(prov_name "$id")${NC:-}"
    echo -e "    ${DIM:-}DoT :${NC:-} $(prov_upstream_dot "$id")"
    echo -e "    ${DIM:-}DoH :${NC:-} $(prov_upstream_doh "$id")"
    v=$(prov_bootstrap_ips "$id" all) || v=""
    echo -e "    ${DIM:-}IP  :${NC:-} ${v}"
    echo -e "    ${DIM:-}$(_prov_t prov_lbl_filter):${NC:-} $(prov_filter_text "$id")"
    echo -e "    ${DIM:-}$(_prov_t prov_lbl_region):${NC:-} $(prov_region_text "$id")"
    echo -e "    ${DIM:-}$(_prov_t prov_lbl_privacy):${NC:-} $(prov_privacy_text "$id")"
    v=$(prov_flags_text "$id") || v=""
    if [ -n "$v" ]; then
        echo -e "    ${DIM:-}$(_prov_t prov_lbl_notes):${NC:-} ${v}"
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ГЕОПОДСКАЗКА
# ═══════════════════════════════════════════════════════════════════════
# Дешёвая догадка ru / eu. Ни от чего не зависит жёстко, не блокирует
# дольше ~3 секунд и НИКОГДА не роняет вызывающего: всегда rc 0 и всегда
# что-то печатает.
#
# Порядок: явное указание → запомненное в конфиге → часовой пояс (бесплатно,
# без сети) → короткий сетевой запрос страны → «eu».
#
# Часовой пояс сначала не случайно: на сервере в РФ его почти всегда
# выставляют, а сетевой запрос как раз может упереться в то самое
# блокирование, от которого мы и пляшем.

_prov_timezone() {
    local tz=""
    if [ -r /etc/timezone ]; then
        tz=$(sed -n '1p' /etc/timezone 2>/dev/null) || tz=""
    fi
    if [ -z "$tz" ] && [ -L /etc/localtime ]; then
        tz=$(readlink /etc/localtime 2>/dev/null) || tz=""
        tz="${tz##*/zoneinfo/}"
    fi
    tz="${tz%%[[:space:]]*}"
    printf '%s' "$tz"
    return 0
}

# rc0 — часовой пояс из «российского» набора (плюс Минск и Казахстан:
# там Яндекс и Comss тоже ближайшие).
_prov_tz_is_ru() {
    case "${1:-}" in
        Europe/Moscow|Europe/Kaliningrad|Europe/Kirov|Europe/Volgograd|\
        Europe/Saratov|Europe/Astrakhan|Europe/Samara|Europe/Ulyanovsk|\
        Europe/Simferopol|Europe/Minsk|\
        Asia/Yekaterinburg|Asia/Omsk|Asia/Novosibirsk|Asia/Barnaul|\
        Asia/Tomsk|Asia/Novokuznetsk|Asia/Krasnoyarsk|Asia/Irkutsk|\
        Asia/Chita|Asia/Yakutsk|Asia/Khandyga|Asia/Vladivostok|\
        Asia/Ust-Nera|Asia/Magadan|Asia/Sakhalin|Asia/Srednekolymsk|\
        Asia/Kamchatka|Asia/Anadyr|\
        Asia/Almaty|Asia/Aqtau|Asia/Aqtobe|Asia/Atyrau|Asia/Oral|\
        Asia/Qostanay|Asia/Qyzylorda|W-SU)
            return 0 ;;
    esac
    return 1
}

# Один запрос с жёстким потолком по времени.
#
# Грабли: полагаться на curl --max-time здесь нельзя. Мы находимся ровно
# в той ситуации, когда резолвер сломан или ещё не настроен — это и есть
# повод ставить goDNS. Если curl подменён обёрткой, собран без поддержки
# дробных таймаутов или встал в резолве раньше, чем таймер начал тикать,
# обещание «не дольше трёх секунд» превращается в полминуты тишины на
# пустом экране. Проверено: подставная `curl`, игнорирующая свои флаги,
# держала prov_geo_hint 30 секунд.
#
# timeout(1) тоже не годится в качестве единственной страховки: это
# coreutils, на Ubuntu он есть, но проверять поведение приходится там,
# где его нет. Поэтому потолок делается своими руками — фоновой запуск
# и опрос, без единой внешней зависимости.
_prov_fetch_capped() {
    local url="${1:-}" out="" tmp="" pid=0 waited=0 limit=20
    tmp=$(mktemp "${TMPDIR:-/tmp}/godns.geo.XXXXXX" 2>/dev/null) || {
        printf '%s' ""
        return 0
    }

    curl -fsS --connect-timeout 1 --max-time 2 "$url" >"$tmp" 2>/dev/null &
    pid=$!

    # 20 × 0.1 с = 2 с. sleep с дробью понимают и GNU, и BSD; если вдруг
    # не понял — падаем на целую секунду, потолок станет грубее, но останется.
    while [ "$waited" -lt "$limit" ]; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1 2>/dev/null || sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.2 2>/dev/null || true
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true

    out=$(cat "$tmp" 2>/dev/null) || out=""
    rm -f "$tmp" 2>/dev/null || true
    printf '%s' "$out"
    return 0
}

# Двухбуквенный код страны или пусто. Общий бюджет — 3 секунды на все
# попытки вместе, считается по SECONDS, а не по числу попыток.
#
# Проверять «сколько уже потрачено» недостаточно: после первой попытки на
# счётчике 2 секунды, 2 < 3, и цикл честно уходит во вторую — итого пять.
# Спрашивать надо «влезет ли ещё одна попытка целиком», то есть сравнивать
# с бюджетом потраченное ПЛЮС стоимость следующей попытки. Тогда вторая
# точка опрашивается только если первая отвалилась мгновенно.
_prov_country_code() {
    local url="" cc="" started=0 budget=3 attempt_cost=3
    command -v curl >/dev/null 2>&1 || { printf '%s' ""; return 0; }
    started=$SECONDS
    for url in "https://ipinfo.io/country" "https://ifconfig.co/country-iso"; do
        if [ $((SECONDS - started + attempt_cost)) -gt "$budget" ]; then break; fi
        cc=$(_prov_fetch_capped "$url") || cc=""
        cc="${cc//[$'\t\r\n ']/}"
        case "$cc" in
            [A-Za-z][A-Za-z])
                printf '%s' "$cc"
                return 0
                ;;
        esac
    done
    printf '%s' ""
    return 0
}

prov_geo_hint() {
    local v="" tz="" cc=""

    if [ -n "$PROV_GEO_HINT" ]; then
        printf '%s' "$PROV_GEO_HINT"
        return 0
    fi

    # 1. Явное указание оператора — сильнее любой догадки.
    v="${GODNS_GEO:-}"
    case "$v" in
        ru|eu) PROV_GEO_HINT="$v"; printf '%s' "$v"; return 0 ;;
    esac

    # 2. Уже запомненное. config_get может отсутствовать (модуль в одиночку).
    if declare -F config_get >/dev/null 2>&1; then
        v=$(config_get geo_hint "") || v=""
        case "$v" in
            ru|eu) PROV_GEO_HINT="$v"; printf '%s' "$v"; return 0 ;;
        esac
    fi

    # 3. Часовой пояс — бесплатно и без сети.
    tz=$(_prov_timezone) || tz=""
    if [ -n "$tz" ] && _prov_tz_is_ru "$tz"; then
        PROV_GEO_HINT="ru"
        printf '%s' "ru"
        return 0
    fi

    # 4. Короткий сетевой запрос страны.
    cc=$(_prov_country_code) || cc=""
    case "$cc" in
        RU|ru|BY|by|KZ|kz) PROV_GEO_HINT="ru" ;;
        '')                PROV_GEO_HINT="eu" ;;
        *)                 PROV_GEO_HINT="eu" ;;
    esac

    printf '%s' "$PROV_GEO_HINT"
    return 0
}

# Запомнить подсказку в конфиге — отдельным вызовом, чтобы prov_geo_hint
# оставалась чистой и её можно было звать сколько угодно раз.
prov_geo_remember() {
    local v="${1:-}"
    case "$v" in
        ru|eu) ;;
        *) return 1 ;;
    esac
    PROV_GEO_HINT="$v"
    if declare -F config_set >/dev/null 2>&1; then
        config_set geo_hint "$v" || return 1
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# САМОПРОВЕРКА КАТАЛОГА
# ═══════════════════════════════════════════════════════════════════════
# Запускается тестами на целевом хосте. Ловит опечатки в таблице до того,
# как они станут пустым апстримом в конфиге dnsdist.
prov_selftest() {
    local id="" err=0 seen="" v="" c="" ok=0

    if [ "$(prov_count)" -lt 15 ]; then
        _prov_warn "selftest: в каталоге меньше 15 записей"
        err=$((err + 1))
    fi

    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        # Свои записи — не каталог: у них нет DoH-URL и bootstrap по
        # построению, их проверяет prov_custom_add при добавлении.
        [ "$(prov_field "$id" custom)" = "yes" ] && continue

        case " $seen " in
            *" $id "*) _prov_warn "selftest: дубль id '$id'"; err=$((err + 1)) ;;
        esac
        seen="$seen $id"

        # dns0.eu закрыт в октябре 2025 — его здесь быть не может.
        case "$id" in
            dns0*) _prov_warn "selftest: dns0.eu закрыт, запись должна быть удалена"
                   err=$((err + 1)) ;;
        esac

        v=$(prov_field "$id" dot_port) || v=""
        case "$v" in
            ''|*[!0-9]*) _prov_warn "selftest: '$id' порт '$v' не число"; err=$((err + 1)) ;;
        esac

        v=$(prov_field "$id" doh_url) || v=""
        case "$v" in
            https://*) ;;
            *) _prov_warn "selftest: '$id' DoH URL не https: '$v'"; err=$((err + 1)) ;;
        esac

        v=$(prov_field "$id" filter) || v=""
        ok=0
        for c in "${PROV_FILTER_CLASSES[@]}"; do
            if [ "$c" = "$v" ]; then ok=1; fi
        done
        if [ "$ok" -ne 1 ]; then
            _prov_warn "selftest: '$id' неизвестный класс фильтрации '$v'"
            err=$((err + 1))
        fi

        v=$(prov_field "$id" region) || v=""
        ok=0
        for c in "${PROV_REGIONS[@]}"; do
            if [ "$c" = "$v" ]; then ok=1; fi
        done
        if [ "$ok" -ne 1 ]; then
            _prov_warn "selftest: '$id' неизвестный регион '$v'"
            err=$((err + 1))
        fi

        v=$(prov_field "$id" account) || v=""
        case "$v" in
            yes|no) ;;
            *) _prov_warn "selftest: '$id' account='$v' (нужно yes|no)"; err=$((err + 1)) ;;
        esac

        v=$(prov_bootstrap_ips "$id" v4) || v=""
        if [ -z "$v" ]; then
            _prov_warn "selftest: '$id' без bootstrap IPv4"
            err=$((err + 1))
        fi
    done

    # Дефолты обязаны существовать и не требовать аккаунта.
    for c in ru eu eu-sovereign; do
        v=$(prov_default_set "$c") || v=""
        if [ -z "$v" ]; then
            _prov_warn "selftest: пустой набор по умолчанию для '$c'"
            err=$((err + 1))
            continue
        fi
    done

    if [ "$err" -ne 0 ]; then
        _prov_warn "selftest: ошибок — $err"
        return 1
    fi
    return 0
}

# ── Порядок для меню ────────────────────────────────────────────────────
# Сначала НЕФИЛЬТРУЮЩИЕ, потом фильтрующие. Требующие аккаунт не
# показываем вовсе: их нельзя выбрать одним нажатием — нужен ID профиля,
# а половина пунктов меню, которые «просто так не работают», хуже, чем
# их отсутствие.
#
# Почему нефильтрующие первыми: фильтрующий апстрим молча отдаёт NXDOMAIN
# на домены, которые запросили клиенты туннеля. Со стороны это выглядит
# как сломанный туннель, а не как фильтр, и разбираться оператор будет
# долго.
# ГРАБЛИ, оставленные здесь как урок: раньше в этом цикле стояло
# `prov_requires_account && continue`, и провайдер с профилем не попадал в
# список ВООБЩЕ, хотя требование было обратным — показывать, но не
# предвыбирать. Скрытие делало недостижимыми сразу две функции (их и
# поймала check-unreachable). Сегодня провайдеров с профилем в таблице нет
# вовсе, но правило остаётся: не скрывать строку каталога из списка —
# скрытая строка превращается в мёртвый груз, о котором никто не помнит.
# Три группы: каталог без фильтрации, каталог с фильтрацией, свои. Свои —
# последними и отдельно: у них нет ни класса фильтрации, ни региона, и
# перемешивать их с каталогом значило бы выдавать чужую ответственность за
# нашу.
prov_menu_ids() {
    local id="" f=""
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] && continue
        f=$(prov_field "$id" filter) || f=""
        [ "$f" = "none" ] && printf '%s\n' "$id"
    done
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] && continue
        f=$(prov_field "$id" filter) || f=""
        [ "$f" = "none" ] || printf '%s\n' "$id"
    done
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] && printf '%s\n' "$id"
    done
    return 0
}

# Индекс, с которого начинаются фильтрующие — чтобы меню могло вставить
# заголовок группы ровно в этом месте.
# Позиция, с которой начинается группа «с фильтрацией».
prov_menu_split() {
    local id="" f="" n=0
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] && continue
        f=$(prov_field "$id" filter) || f=""
        [ "$f" = "none" ] && n=$((n + 1))
    done
    printf '%s' "$n"
    return 0
}
# Позиция, с которой начинается группа «свои» (= число записей каталога).
prov_menu_split_custom() {
    local id="" n=0
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] || n=$((n + 1))
    done
    printf '%s' "$n"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СВОИ АПСТРИМЫ
# ═══════════════════════════════════════════════════════════════════════
# Живут в config.json под ключом custom_upstreams, записи через ';':
#         id|proto|ip|port|sni|path|name
#   proto ∈ dot | doh | plain;  ip — ЛИТЕРАЛ (имя разрешается при
#   добавлении);  sni — имя для проверки сертификата (dot/doh);  path —
#   только doh;  name — как показывать.
#
# На загрузке они вливаются в ТУ ЖЕ таблицу PROVIDERS с пометкой
# custom=yes, а не живут отдельной веткой кода: prov_exists (панель),
# список с галочками, отчёт, замер и сборка записей движка тогда работают
# без единой правки. Второй путь для «своих» рано или поздно разошёлся бы с
# первым — так уже расходились помощники межсетевого экрана.
#
# Проверка сертификата у своих DoT/DoH ВСЕГДА включена — как у каталога.
# Самоподписанный сертификат здесь не заведётся, и это намеренно.
PROV_CUSTOM_MAX=8

prov_custom_list() {
    local id=""
    for id in "${PROVIDER_IDS[@]:-}"; do
        [ -n "$id" ] || continue
        [ "$(prov_field "$id" custom)" = "yes" ] && printf '%s\n' "$id"
    done
    return 0
}
prov_custom_count() { prov_custom_list | grep -c . || true; }

# Имя для DoH-URL / SNI: домен без схемы и пути.
_prov_valid_sni() {
    [[ "${1:-}" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]
}
_prov_valid_path() {
    [[ "${1:-}" =~ ^/[A-Za-z0-9._~%/-]{0,127}$ ]]
}
# Отображаемое имя: без разделителей записи и без управляющих символов.
_prov_clean_name() {
    local n="${1:-}"
    n="${n//|/ }"; n="${n//;/ }"
    n=$(printf '%s' "$n" | tr -d '\000-\037' | cut -c1-40)
    n="${n#"${n%%[![:space:]]*}"}"; n="${n%"${n##*[![:space:]]}"}"
    printf '%s' "$n"
}

# prov_custom_add <proto> <ip> <port> <sni> <path> <name>
# stdout: id новой записи; тот же id — в PROV_CUSTOM_LAST_ID. rc 1 — отказ,
# причина уже напечатана.
#
# ГРАБЛИ, пойманные песочницей: вызов через `id=$(prov_custom_add …)` идёт
# в ПОДОБОЛОЧКЕ — config на диске записан, а таблица перечитана только
# там, и у вызывающего каталог остаётся старым: движок тут же отвечал «нет
# адреса IPv4» на только что добавленную запись, а проверка дубля смотрела
# в устаревшую память и пропускала повтор. Поэтому: дубли сверяем по ДИСКУ,
# id отдаём ещё и переменной, а вызывающий зовёт функцию напрямую и
# перечитывает таблицу сам (prov_reload).
PROV_CUSTOM_LAST_ID=""
prov_custom_add() {
    PROV_CUSTOM_LAST_ID=""
    local proto="${1:-}" ip="${2:-}" port="${3:-}" sni="${4:-}" path="${5:-}" name="${6:-}"
    if ! declare -F config_set >/dev/null 2>&1; then
        _prov_warn "$(_prov_t prov_custom_no_config)"; return 1
    fi
    case "$proto" in dot|doh|plain) ;; *) _prov_warn "$(_prov_tf prov_custom_bad_proto "$proto")"; return 1 ;; esac
    if ! declare -F valid_ip >/dev/null 2>&1 || ! valid_ip "$ip"; then
        _prov_warn "$(_prov_tf prov_custom_not_ip "$ip")"; return 1
    fi
    if [ -z "$port" ]; then
        case "$proto" in dot) port=853 ;; doh) port=443 ;; *) port=53 ;; esac
    fi
    if ! declare -F valid_port >/dev/null 2>&1 || ! valid_port "$port"; then
        _prov_warn "$(_prov_tf prov_custom_bad_port "$port")"; return 1
    fi
    if [ "$proto" = "plain" ]; then
        sni=""; path=""
    else
        # Имя, не адрес: по IP сертификат не проверить, а проверка у своих
        # DoT/DoH включена всегда. Меню это тоже ловит, но граница обязана
        # сама — прямой вызов из панели или теста пройдёт мимо меню.
        if ! _prov_valid_sni "$sni" || { declare -F valid_ip >/dev/null 2>&1 && valid_ip "$sni"; }; then
            _prov_warn "$(_prov_tf prov_custom_bad_sni "$sni")"; return 1
        fi
        if [ "$proto" = "doh" ]; then
            [ -n "$path" ] || path="/dns-query"
            case "$path" in /*) ;; *) path="/$path" ;; esac
            if ! _prov_valid_path "$path"; then
                _prov_warn "$(_prov_tf prov_custom_bad_path "$path")"; return 1
            fi
        else
            path=""
        fi
    fi
    name=$(_prov_clean_name "${name:-${sni:-$ip}}")
    [ -n "$name" ] || name="${sni:-$ip}"
    # Фигурная скобка — незамещённый шаблон, а не адрес; тот же страж, что
    # у каталога: такая запись уронила бы dnsdist.
    case "${sni}${path}${name}" in *'{'*|*'}'*) _prov_warn "$(_prov_t prov_custom_braces)"; return 1 ;; esac

    # Всё ниже сверяется с ДИСКОМ, не с памятью: см. грабли выше.
    local raw="" rec="" o_id o_proto o_ip o_port o_name o_rest n_have=0 taken=""
    raw=$(config_get custom_upstreams "") || raw=""
    local o_sni=""
    while IFS='|' read -r o_id o_proto o_ip o_port o_sni o_rest; do
        [ -n "${o_id:-}" ] || continue
        n_have=$((n_have + 1))
        taken="${taken} ${o_id}"
        # Дубль — тот же протокол и порт И (тот же адрес ИЛИ то же имя).
        # ГРАБЛИ с живого сервера: dns.google в первый раз разрешился в
        # 8.8.4.4, во второй — в 8.8.8.8 (round-robin у резолвера), и
        # сверка по одному IP пропустила второй dns.google как новую
        # запись. У DoT/DoH апстрим — это имя в сертификате, не адрес.
        if [ "${o_proto:-}" = "$proto" ] && [ "${o_port:-}" = "$port" ] \
           && { [ "${o_ip:-}" = "$ip" ] || { [ -n "$sni" ] && [ "${o_sni:-}" = "$sni" ]; }; }; then
            o_name="${o_rest##*|}"
            _prov_warn "$(_prov_tf prov_custom_exists "${o_name:-$o_id}")"; return 1
        fi
    done <<< "$(printf '%s' "$raw" | tr ';' '\n')"
    if [ "$n_have" -ge "$PROV_CUSTOM_MAX" ]; then
        _prov_warn "$(_prov_tf prov_custom_too_many "$PROV_CUSTOM_MAX")"; return 1
    fi

    # id: custom-<slug>, уникальный среди записей на диске и в каталоге.
    local base="" id="" k=2
    base=$(printf '%s' "${sni:-$ip}" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9' '-' | tr -s '-' | cut -c1-28)
    base="${base#-}"; base="${base%-}"
    [ -n "$base" ] || base="upstream"
    id="custom-${base}"
    while _prov_has "${id}.id" || [[ "$taken" == *" ${id}"* ]]; do
        id="custom-${base}-${k}"; k=$((k + 1))
    done

    rec="${id}|${proto}|${ip}|${port}|${sni}|${path}|${name}"
    config_set custom_upstreams "${raw}${raw:+;}${rec}" || return 1
    prov_reload || true
    PROV_CUSTOM_LAST_ID="$id"
    printf '%s' "$id"
    return 0
}

prov_custom_remove() {
    local id="${1:-}" raw="" rec="" out="" found=0
    [ -n "$id" ] || return 1
    declare -F config_set >/dev/null 2>&1 || return 1
    raw=$(config_get custom_upstreams "") || raw=""
    while IFS= read -r rec; do
        [ -n "$rec" ] || continue
        if [ "${rec%%|*}" = "$id" ]; then found=1; continue; fi
        out="${out}${out:+;}${rec}"
    done <<< "$(printf '%s' "$raw" | tr ';' '\n')"
    [ "$found" -eq 1 ] || return 1
    config_set custom_upstreams "$out" || return 1
    prov_reload || true
    return 0
}

# Перечитать и каталог, и свои — после любой записи в config.
prov_reload() {
    _prov_load || true
    _prov_load_custom || true
    return 0
}

_prov_load_custom() {
    declare -F config_get >/dev/null 2>&1 || return 0
    local raw="" id proto ip port sni path name rest url
    raw=$(config_get custom_upstreams "" 2>/dev/null) || raw=""
    [ -n "$raw" ] || return 0
    while IFS='|' read -r id proto ip port sni path name rest; do
        id="${id:-}"; proto="${proto:-}"; ip="${ip:-}"; port="${port:-}"
        sni="${sni:-}"; path="${path:-}"; name="${name:-}"
        [ -n "$id" ] || continue
        case "$id" in custom-*) ;; *) _prov_warn "custom: чужой id '$id' пропущен"; continue ;; esac
        _prov_valid_id "$id" || { _prov_warn "custom: плохой id '$id'"; continue; }
        if _prov_has "${id}.id"; then _prov_warn "custom: дубль '$id'"; continue; fi
        case "$proto" in dot|doh|plain) ;; *) _prov_warn "custom: '$id' протокол '$proto'"; continue ;; esac
        if declare -F valid_ip >/dev/null 2>&1 && ! valid_ip "$ip"; then
            _prov_warn "custom: '$id' не IP: '$ip'"; continue
        fi
        if [ -z "$port" ]; then
            case "$proto" in dot) port=853 ;; doh) port=443 ;; *) port=53 ;; esac
        fi
        url=""
        if [ "$proto" = "doh" ]; then
            url="https://${sni}"
            [ "$port" = "443" ] || url="${url}:${port}"
            url="${url}${path:-/dns-query}"
        fi
        _prov_set "${id}.id"       "$id"
        _prov_set "${id}.name_ru"  "${name:-${sni:-$ip}}"
        _prov_set "${id}.name_en"  "${name:-${sni:-$ip}}"
        _prov_set "${id}.dot_host" "${sni:-$ip}"
        _prov_set "${id}.dot_port" "$port"
        _prov_set "${id}.doh_url"  "$url"
        case "$ip" in
            *:*) _prov_set "${id}.ipv4" ""; _prov_set "${id}.ipv6" "$ip" ;;
            *)   _prov_set "${id}.ipv4" "$ip"; _prov_set "${id}.ipv6" "" ;;
        esac
        _prov_set "${id}.filter"   "none"
        _prov_set "${id}.privacy"  "prov_priv_custom"
        _prov_set "${id}.account"  "no"
        _prov_set "${id}.region"   "own"
        _prov_set "${id}.flags"    "custom"
        _prov_set "${id}.proto"    "$proto"
        _prov_set "${id}.custom"   "yes"
        PROVIDER_IDS+=("$id")
    done <<< "$(printf '%s' "$raw" | tr ';' '\n')"
    return 0
}

# ── Загрузить каталог при подключении модуля ────────────────────────────
_prov_load || _prov_warn "providers: каталог загружен с ошибками"
_prov_load_custom || true

return 0 2>/dev/null || true

