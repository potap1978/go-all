#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — движок dnsdist (PowerDNS dnsdist 2.0.x из repo.powerdns.com).
#
# ═══════════════════════════════════════════════════════════════════════
# ПОЧЕМУ ИМЕННО ЭТО, И ПОЧЕМУ НЕ ИЗ РЕПОЗИТОРИЯ UBUNTU
# ═══════════════════════════════════════════════════════════════════════
# Пакет dnsdist из штатного репозитория Ubuntu НЕПРИГОДЕН для goDNS:
#   • 22.04 (jammy) — 1.6.1. Исходящие DoT/DoH появились только в 1.7.0,
#     то есть «апстримы только по шифрованному каналу» физически невозможны.
#   • 24.04 (noble) — 1.8.3, собран с libnghttp2, но БЕЗ libh2o-evloop.
#     В ветке 1.8.x входящий DoH делается через h2o (на nghttp2 входящий DoH
#     переехал только в 1.9.0), так что входящего DoH там, скорее всего,
#     нет вовсе.
# Отсюда: ставим из repo.powerdns.com, линия dnsdist-20 (2.0.x). Она есть
# и для jammy, и для noble, собрана с nghttp2 + OpenSSL + GnuTLS.
# Линию 21 (2.1.x) сознательно не берём: 2.1.0 несовместимо изменила
# контракт Lua-политик балансировки.
#
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ, КОТОРЫЕ ЗДЕСЬ УЖЕ ОБОЙДЕНЫ
# ═══════════════════════════════════════════════════════════════════════
# 1. setACL. По умолчанию ACL у dnsdist — loopback + RFC1918 + link-local,
#    и он распространяется НА DoH И DoT ТОЖЕ. Без setACL({'0.0.0.0/0','::/0'})
#    всё выглядит рабочим (служба поднялась, порт слушает, TLS жмёт руку),
#    а любой внешний клиент молча получает отказ.
# 2. Сертификаты. dnsdist не работает от root с версии 1.5.0; служебный
#    пользователь — _dnsdist, и privkey.pem от certbot он прочитать не может.
#    Права выдаёт lib/certs.sh (certs_grant_read_access) — здесь мы её ТОЛЬКО
#    вызываем и не дублируем.
# 3. Конфиг проверяется ДО установки: рендер во временный файл →
#    `dnsdist --check-config -C tmp` → и только потом mv. Рабочий конфиг
#    никогда не затирается заведомо битым.
# 4. dnsdist не умеет перечитывать конфигурацию на лету: SIGHUP только
#    переоткрывает логи. Поэтому engine_reload = проверка + restart, а
#    «на лету» умеет ровно одно — reloadAllCertificates() через консоль.
# 5. Своего systemd-юнита не пишем. Пакет приносит свой; всё, что нам надо,
#    кладём drop-in'ом в /etc/systemd/system/dnsdist.service.d/10-godns.conf.

# ── Пути и константы ────────────────────────────────────────────────────
DNSDIST_BIN="${DNSDIST_BIN:-dnsdist}"
DNSDIST_ETC="${DNSDIST_ETC:-/etc/dnsdist}"
DNSDIST_CONF="${DNSDIST_CONF:-${DNSDIST_ETC}/dnsdist.conf}"
DNSDIST_UNIT="${DNSDIST_UNIT:-dnsdist}"
DNSDIST_DROPIN_DIR="${DNSDIST_DROPIN_DIR:-/etc/systemd/system/dnsdist.service.d}"
DNSDIST_DROPIN="${DNSDIST_DROPIN:-${DNSDIST_DROPIN_DIR}/10-godns.conf}"
DNSDIST_USER="${DNSDIST_USER:-_dnsdist}"

# Вёрстка экрана наблюдения — в python3 (lib/monitor.py): printf в bash
# добивает поля БАЙТАМИ, и кириллические подписи разъезжают колонки.
if [ -z "${DNSDIST_MON_PY:-}" ]; then
    if [ -n "${GODNS_LIB_DIR:-}" ] && [ -r "${GODNS_LIB_DIR}/monitor.py" ]; then
        DNSDIST_MON_PY="${GODNS_LIB_DIR}/monitor.py"
    else
        DNSDIST_MON_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/monitor.py"
    fi
fi

# Транзитный юнит, который сам выключит подробный лог запросов.
DNSDIST_QLOG_UNIT="${DNSDIST_QLOG_UNIT:-godns-querylog-off}"
DNSDIST_QLOG_MINUTES="${DNSDIST_QLOG_MINUTES:-15}"

# Куда служба пишет свой вывод. Пакетный юнит запускает dnsdist с
# --disable-syslog, то есть весь лог идёт в stdout → journald. Мы уводим его
# в свой файл: у journald потолок SystemMaxUse ОДИН НА ВСЮ МАШИНУ, и сосед
# goTelegram выставляет его глобально — «200 МБ на goDNS» в общем журнале
# было бы обещанием, которое нечем обеспечить. Подробности — в lib/logs.sh.
DNSDIST_LOG="${DNSDIST_LOG:-${GODNS_LOG_DIR:-/var/log/godns}/dnsdist.log}"

DNSDIST_APT_LIST="${DNSDIST_APT_LIST:-/etc/apt/sources.list.d/godns-dnsdist.list}"
DNSDIST_APT_PIN="${DNSDIST_APT_PIN:-/etc/apt/preferences.d/godns-dnsdist}"
DNSDIST_APT_KEYRING="${DNSDIST_APT_KEYRING:-/etc/apt/keyrings/godns-dnsdist-pub.asc}"
DNSDIST_REPO_HOST="${DNSDIST_REPO_HOST:-repo.powerdns.com}"
DNSDIST_REPO_BASE="${DNSDIST_REPO_BASE:-https://repo.powerdns.com/ubuntu}"
# Линия репозитория: 20 = dnsdist 2.0.x. Переопределяется ключом
# конфига dnsdist_line (19 — максимально консервативно, 1.9.16).
DNSDIST_LINE_DEFAULT="20"

# Заполняются dnsdist_probe_capabilities().
DNSDIST_VERSION=""
DNSDIST_VER_MAJOR="0"
DNSDIST_VER_MINOR="0"
DNSDIST_FEATURES=""
DNSDIST_HAS_DOH="no"
DNSDIST_HAS_DOT="no"
DNSDIST_HAS_DOQ="no"
DNSDIST_HAS_CONSOLE="no"
DNSDIST_HAS_LAZY_HC="no"

# ═══════════════════════════════════════════════════════════════════════
# ВОЗМОЖНОСТИ
# ═══════════════════════════════════════════════════════════════════════
dnsdist_engine_init() {
    # Проверяемые фактами возможности dnsdist:
    #  • свой путь DoH обеспечивается addDOHLocal(..., {'/secret'}) —
    #    exactPathMatching включён по умолчанию с 1.5.0, чужой путь → 404;
    #  • roundrobin — настоящий встроенный round-robin, не «случайный»;
    #  • reloadAllCertificates() через консоль — без обрыва соединений.
    engine_cap_set can_enforce_doh_path   yes
    engine_cap_set can_true_roundrobin    yes
    engine_cap_set can_reload_certs_live  yes
    engine_cap_set has_console            yes
    engine_cap_set needs_third_party_repo yes
    # DoQ — не украшение: показывать пункт меню, который не сработает,
    # хуже, чем не показывать вовсе. Поэтому «нет», пока установленный
    # бинарник не скажет обратного.
    engine_cap_set can_doq                no
    # Живой экран у dnsdist работает БЕЗ подробного лога: кольцевые буферы
    # (grepq) включены по умолчанию и хранят клиента, имя, апстрим, задержку
    # и rcode. Подробный лог — отдельная возможность: setVerbose(true)
    # переключается на лету через ту же консоль.
    engine_cap_set can_live_monitor       yes
    engine_cap_set can_query_log          yes
    dnsdist_refresh_caps || true
    return 0
}

# Уточнить возможности по реально установленному бинарнику.
dnsdist_refresh_caps() {
    dnsdist_probe_capabilities >/dev/null 2>&1 || true
    if [ "$DNSDIST_HAS_DOQ" = "yes" ]; then
        engine_cap_set can_doq yes
    else
        engine_cap_set can_doq no
    fi
    if [ "$DNSDIST_HAS_CONSOLE" = "yes" ]; then
        engine_cap_set has_console yes
    fi
    return 0
}

dnsdist_name() {
    printf 'dnsdist %s' "${DNSDIST_VERSION:-(PowerDNS)}"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# РАЗБОР `dnsdist --version`
# ═══════════════════════════════════════════════════════════════════════
# Вывод выглядит так:
#   dnsdist 2.0.8 (Lua 5.4.6 [LuaJIT ...])
#   Enabled features: cdb dns-over-https(nghttp2) dns-over-quic dns-over-tls(gnutls openssl) ...
#
# rc 0 — бинарник пригоден для goDNS (>= 1.9 И есть dns-over-https И dns-over-tls).
# rc 1 — бинарника нет или он непригоден.
# ── Проверка живости апстримов ──────────────────────────────────────────
# ГРАБЛИ, найденные по журналу на живом сервере: настроек проверки не было
# вовсе, поэтому действовали умолчания dnsdist — проверка КАЖДУЮ СЕКУНДУ и
# пометка «down» после ОДНОЙ неудачи. Для DoT это новое TLS-рукопожатие к
# каждому апстриму ежесекундно; любая потеря пакета роняла апстрим, а
# следующая проверка поднимала. В логе накопилось 10 721 строка «Marking
# downstream …», по 12 метаний в минуту, и все четыре апстрима падали
# ОДНОВРЕМЕННО — то есть подводила сама проверка, а не резолверы. Когда
# внизу оказывались все, запросам было некуда идти: отсюда SERVFAIL у
# клиентов на исправных апстримах.
#
# Ленивая проверка (dnsdist 1.8+) сделана ровно для этого случая: пока
# настоящий трафик идёт нормально, апстрим не опрашивается вовсе, а
# проверки начинаются только когда доля неудач переходит порог. Для
# шифрованных апстримов это единственный разумный режим — активная проба
# по TLS дороже самого запроса.
#
# Числа: смотрим последние 100 ответов, реагируем не раньше десятого
# (иначе одна неудача на пустом сервере = 100%), порог 30%, при
# срабатывании опрашиваем раз в 15 с. Плюс терпимость и на активном пути:
# три неудачи подряд на «down», два успеха на «up», таймаут 2 с — DoT к
# далёкому резолверу в секунду укладывается не всегда.
dnsdist_healthcheck_opts() {
    if [ "$DNSDIST_HAS_LAZY_HC" = "yes" ]; then
        printf "%s" \
            "healthCheckMode='lazy', lazyHealthCheckSampleSize=100, " \
            "lazyHealthCheckMinSampleCount=10, lazyHealthCheckThreshold=30, " \
            "lazyHealthCheckFailedInterval=15, "
    fi
    printf "%s" "checkInterval=15, checkTimeout=2000, maxCheckFailures=3, rise=2, "
}

dnsdist_probe_capabilities() {
    DNSDIST_VERSION=""; DNSDIST_VER_MAJOR="0"; DNSDIST_VER_MINOR="0"
    DNSDIST_FEATURES=""
    DNSDIST_HAS_DOH="no"; DNSDIST_HAS_DOT="no"
    DNSDIST_HAS_DOQ="no"; DNSDIST_HAS_CONSOLE="no"

    command -v "$DNSDIST_BIN" >/dev/null 2>&1 || return 1

    local out=""
    # --version печатает в stderr на части сборок — забираем оба потока.
    # Присваивание из подстановки, которая может вернуть !=0, обязано
    # заканчиваться `|| true`, иначе set -e убьёт весь скрипт.
    out=$("$DNSDIST_BIN" --version 2>&1) || true
    [ -n "$out" ] || return 1

    DNSDIST_VERSION=$(printf '%s\n' "$out" \
        | sed -n -E 's/^[[:space:]]*dnsdist[[:space:]]+([0-9]+(\.[0-9]+)*([^[:space:]]*)).*$/\1/p' \
        | head -n1) || true
    DNSDIST_FEATURES=$(printf '%s\n' "$out" \
        | sed -n -E 's/^[[:space:]]*Enabled features:[[:space:]]*(.*)$/\1/p' \
        | head -n1) || true

    [ -n "$DNSDIST_VERSION" ] || return 1

    local maj min
    maj="${DNSDIST_VERSION%%.*}"
    min="${DNSDIST_VERSION#*.}"; min="${min%%.*}"
    case "$maj" in ''|*[!0-9]*) maj=0 ;; esac
    case "$min" in ''|*[!0-9]*) min=0 ;; esac
    DNSDIST_VER_MAJOR=$((10#$maj))
    DNSDIST_VER_MINOR=$((10#$min))

    case "$DNSDIST_FEATURES" in
        *dns-over-https*) DNSDIST_HAS_DOH="yes" ;;
    esac
    case "$DNSDIST_FEATURES" in
        *dns-over-tls*)   DNSDIST_HAS_DOT="yes" ;;
    esac
    case "$DNSDIST_FEATURES" in
        *dns-over-quic*)  DNSDIST_HAS_DOQ="yes" ;;
    esac
    # Шифрованная консоль требует libsodium (в новых сборках — встроенной
    # криптографии, тогда в features светится «sodium» или «crypto»).
    case "$DNSDIST_FEATURES" in
        *sodium*|*crypto*) DNSDIST_HAS_CONSOLE="yes" ;;
    esac

    # Ленивая проверка живости появилась в 1.8, но в списке features её нет
    # — спрашиваем сам бинарник пробным конфигом. По версии судить нельзя:
    # сборки распространителей отличаются, а ошибка здесь стоит дорого:
    # на 2.0.2 незнакомый параметр внутри newServer() НЕ роняет проверку,
    # а молча выбрасывается («Unknown key … ignored», код 0) — и апстрим
    # поднялся бы без ленивой проверки живости, не сказав об этом.
    DNSDIST_HAS_LAZY_HC="no"
    local probe=""
    probe=$(godns_mktemp) || probe=""
    if [ -n "$probe" ]; then
        printf "%s\n" \
            "newServer({address='127.0.0.1:5399', name='probe', healthCheckMode='lazy', lazyHealthCheckSampleSize=100, lazyHealthCheckMinSampleCount=10, lazyHealthCheckThreshold=30, lazyHealthCheckFailedInterval=15, checkInterval=15, checkTimeout=2000, maxCheckFailures=3, rise=2})" \
            > "$probe" 2>/dev/null
        # Не «код 0», а «код 0 И ни одного Unknown key»: сборка без ленивой
        # проверки не падает на её параметрах, а молча их выбрасывает.
        local probe_out=""
        if probe_out=$(dnsdist --check-config -C "$probe" 2>&1) \
           && ! printf '%s' "$probe_out" | grep -q 'Unknown key'; then
            DNSDIST_HAS_LAZY_HC="yes"
        fi
        rm -f "$probe" 2>/dev/null || true
    fi

    if [ "$DNSDIST_VER_MAJOR" -lt 1 ]; then return 1; fi
    if [ "$DNSDIST_VER_MAJOR" -eq 1 ] && [ "$DNSDIST_VER_MINOR" -lt 9 ]; then return 1; fi
    if [ "$DNSDIST_HAS_DOH" != "yes" ] || [ "$DNSDIST_HAS_DOT" != "yes" ]; then return 1; fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# УСТАНОВКА
# ═══════════════════════════════════════════════════════════════════════

# Кодовые имена, под которыми repo.powerdns.com может нас обслужить.
# Первым — родное; дальше — деградация вниз по LTS: 26.04 в репозитории
# может ещё не появиться, а пакет для noble на ней ставится.
dnsdist_repo_suites() {
    local native="${OS_CODENAME:-}"
    case "${OS_VERSION_ID:-}" in
        22.04) native="jammy" ;;
        24.04) native="noble" ;;
    esac
    local s seen=""
    for s in "$native" noble jammy; do
        [ -n "$s" ] || continue
        case " $seen " in *" $s "*) continue ;; esac
        seen="$seen $s"
        printf '%s\n' "$s"
    done
    return 0
}

dnsdist_repo_line() {
    local line
    line=$(config_get dnsdist_line "$DNSDIST_LINE_DEFAULT") || true
    case "$line" in
        ''|*[!0-9]*) line="$DNSDIST_LINE_DEFAULT" ;;
    esac
    printf '%s' "$line"
}

# Скачать и проверить ключ репозитория. Ничего не пишем в /etc, пока не
# убедились, что это действительно PGP-ключ, а не страница ошибки прокси.
dnsdist_fetch_repo_key() {
    local dest="$1" tmp="" url="" ok=1
    tmp=$(godns_mktemp) || return 1

    for url in \
        "https://${DNSDIST_REPO_HOST}/FD380FBB-pub.asc" \
        "https://${DNSDIST_REPO_HOST}/CBC8B383-pub.asc"
    do
        : > "$tmp"
        if curl -fsSL --max-time 30 --retry 2 -o "$tmp" "$url" 2>/dev/null; then
            # ГРАБЛИ: `head -n1 f | grep -q X` под pipefail умеет вернуть !=0
            # даже при совпадении — grep -q выходит первым и head ловит
            # SIGPIPE. Читаем строку в переменную и сравниваем case'ом.
            local first=""
            first=$(head -n1 "$tmp" 2>/dev/null) || first=""
            case "$first" in
                *"BEGIN PGP PUBLIC KEY BLOCK"*)
                    ok=0
                    log_dim "$(tf eng_dnsdist_key_ok "$url")"
                    break
                    ;;
            esac
        fi
    done

    if [ "$ok" -ne 0 ]; then
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi

    install -d -m 0755 "$(dirname "$dest")" 2>/dev/null || true
    install -m 0644 "$tmp" "$dest" || { rm -f "$tmp" 2>/dev/null || true; return 1; }
    rm -f "$tmp" 2>/dev/null || true
    return 0
}

# Обновить индекс ТОЛЬКО нашего списка. Полный apt update на чужой
# сломанный сторонний репозиторий не должен валить нашу установку.
dnsdist_apt_update_own() {
    apt_q update \
        -o Dir::Etc::sourcelist="$DNSDIST_APT_LIST" \
        -o Dir::Etc::sourceparts="-" \
        -o APT::Get::List-Cleanup="0" >/dev/null 2>&1
}

dnsdist_write_repo() {
    local suite="$1" line="$2"
    printf 'deb [signed-by=%s] %s %s-dnsdist-%s main\n' \
        "$DNSDIST_APT_KEYRING" "$DNSDIST_REPO_BASE" "$suite" "$line" \
        > "$DNSDIST_APT_LIST" || return 1
    chmod 0644 "$DNSDIST_APT_LIST" 2>/dev/null || true

    # Пин нужен, чтобы версия из PowerDNS выиграла у ubuntu/universe,
    # но ТОЛЬКО для dnsdist — остальную систему сторонний репозиторий
    # трогать не должен.
    cat > "$DNSDIST_APT_PIN" <<'PIN' || return 1
Package: dnsdist*
Pin: origin repo.powerdns.com
Pin-Priority: 600
PIN
    chmod 0644 "$DNSDIST_APT_PIN" 2>/dev/null || true
    return 0
}

dnsdist_repo_cleanup() {
    rm -f "$DNSDIST_APT_LIST" "$DNSDIST_APT_PIN" 2>/dev/null || true
    return 0
}

# Запасная лестница: репозиторий недоступен → пробуем уже установленный
# бинарник; не годится — внятный отказ с инструкцией, а не «apt failed».
# Что apt может поставить ПРЯМО СЕЙЧАС, без нашего вмешательства.
# На чистой машине это ровно пакет дистрибутива; на машине, где мы уже
# добавляли repo.powerdns.com, — новейшее из обоих, и это тоже верный
# ответ на вопрос «нужно ли лезть к третьей стороне».
# Печатает версию, rc 1 — пакета нет.
dnsdist_distro_candidate() {
    local v=""
    v=$(apt-cache policy dnsdist 2>/dev/null \
        | sed -n 's/^[[:space:]]*Candidate:[[:space:]]*//p' | head -n1) || v=""
    case "$v" in ''|'(none)'|'*') return 1 ;; esac
    printf '%s' "$v"
    return 0
}

# Поставить пакет дистрибутива, если он не ниже 1.9, и принять его ТОЛЬКО
# после разбора `dnsdist --version`.
#
# ЗАЧЕМ: до 26.04 пакет дистрибутива не годился никогда — jammy даёт 1.6.1
# (исходящих DoT/DoH не существовало до 1.7.0), noble 1.8.3 собран без
# libh2o, а входящий DoH на nghttp2 появился только в 1.9.0. Поэтому мы
# шли в repo.powerdns.com всегда. Но в 26.04 (resolute) лежит 2.0.2 с
# dns-over-https и dns-over-tls — он подходит целиком, а ветки resolute в
# repo.powerdns.com ещё нет: мы деградировали до noble, пакет не вставал
# по несовместимым зависимостям, и установка падала на машине, где всё
# нужное лежало в штатном репозитории.
#
# Версии мало: noble 1.8.3 собран без нужного, поэтому порог отсекает его
# заранее, а всё, что прошло порог, проверяется по списку возможностей.
dnsdist_try_distro_package() {
    local cand maj min
    cand=$(dnsdist_distro_candidate) || return 1
    maj="${cand%%.*}"
    min="${cand#*.}"; min="${min%%.*}"
    case "$maj" in ''|*[!0-9]*) return 1 ;; esac
    case "$min" in ''|*[!0-9]*) min=0 ;; esac
    if [ "$((10#$maj))" -lt 1 ] \
       || { [ "$((10#$maj))" -eq 1 ] && [ "$((10#$min))" -lt 9 ]; }; then
        log_dim "$(tf eng_dnsdist_distro_too_old "$cand")"
        return 1
    fi
    log_dim "$(tf eng_dnsdist_distro_try "$cand")"
    if ! run_with_progress "$(t eng_dnsdist_apt_installing)" \
            apt_q install --no-install-recommends dnsdist; then
        log_dim "$(t eng_dnsdist_distro_install_failed)"
        return 1
    fi
    if ! dnsdist_probe_capabilities; then
        log_dim "$(tf eng_dnsdist_distro_lacks "${DNSDIST_FEATURES:-—}")"
        return 1
    fi
    log_success "$(tf eng_dnsdist_distro_ok "$DNSDIST_VERSION")"
    log_dim "$(tf eng_dnsdist_features "$DNSDIST_FEATURES")"
    dnsdist_refresh_caps || true
    return 0
}

# Причину называем СВОИМ ключом, а не одним на все случаи: раньше здесь
# всегда печаталось «repo.powerdns.com недоступен», в том числе когда
# репозиторий прекрасно открывался, а пакет просто не встал по
# зависимостям. Оператор шёл проверять сеть вместо настоящей причины.
dnsdist_install_fallback() {   # [ключ-причина]
    log_warning "$(t "${1:-eng_dnsdist_repo_unreachable}")"
    if dnsdist_probe_capabilities; then
        log_warning "$(tf eng_dnsdist_using_preinstalled "$DNSDIST_VERSION")"
        log_dim "$(tf eng_dnsdist_features "$DNSDIST_FEATURES")"
        dnsdist_refresh_caps || true
        return 0
    fi
    if [ -n "$DNSDIST_VERSION" ]; then
        log_error "$(tf eng_dnsdist_too_old "$DNSDIST_VERSION")"
        log_dim "$(tf eng_dnsdist_features "${DNSDIST_FEATURES:-—}")"
    else
        log_error "$(t eng_dnsdist_absent)"
    fi
    log_error "$(t eng_dnsdist_howto_1)"
    log_dim   "$(tf eng_dnsdist_howto_2 "$DNSDIST_REPO_HOST")"
    log_dim   "$(t eng_dnsdist_howto_3)"
    log_dim   "$(t eng_dnsdist_howto_4)"
    return 1
}

dnsdist_install() {
    require_root || return 1

    # Уже стоит подходящая версия — ничего не трогаем. Установка обязана
    # быть идемпотентной: её зовут и из мастера, и из «переустановить».
    if dnsdist_probe_capabilities; then
        log_success "$(tf eng_dnsdist_already "$DNSDIST_VERSION")"
        dnsdist_refresh_caps || true
        return 0
    fi

    wait_for_cloud_init
    apt_wait_ready || return 1
    ensure_packages curl ca-certificates || return 1

    log_step "$(t eng_dnsdist_install_step)"

    # Достижим ли репозиторий вообще? Отдельная проверка нужна, чтобы
    # отличить «нет сети до PowerDNS» от «apt сломан».
    # Пакет самого дистрибутива — ПЕРЕД третьей стороной. Если он годен,
    # чужой репозиторий не нужен вовсе.
    if dnsdist_try_distro_package; then
        return 0
    fi

    if ! curl -fsS --max-time 20 -o /dev/null "${DNSDIST_REPO_BASE}/dists/" 2>/dev/null; then
        dnsdist_install_fallback eng_dnsdist_repo_unreachable
        return $?
    fi

    if ! dnsdist_fetch_repo_key "$DNSDIST_APT_KEYRING"; then
        log_error "$(t eng_dnsdist_key_failed)"
        dnsdist_install_fallback eng_dnsdist_reason_key
        return $?
    fi

    local line suite chosen=""
    line=$(dnsdist_repo_line)
    while IFS= read -r suite; do
        [ -n "$suite" ] || continue
        log_dim "$(tf eng_dnsdist_try_suite "$suite" "$line")"
        if ! dnsdist_write_repo "$suite" "$line"; then
            log_error "$(t eng_dnsdist_repo_write_failed)"
            return 1
        fi
        if dnsdist_apt_update_own; then
            chosen="$suite"
            break
        fi
    done < <(dnsdist_repo_suites)

    if [ -z "$chosen" ]; then
        dnsdist_repo_cleanup
        log_error "$(t eng_dnsdist_no_suite)"
        dnsdist_install_fallback eng_dnsdist_reason_no_suite
        return $?
    fi
    log_success "$(tf eng_dnsdist_suite_ok "$chosen" "$line")"

    # Полный update нужен для индекса зависимостей из основных репозиториев
    # на «чистой» машине, где apt update ещё ни разу не запускали. Падение
    # тут не фатально: наш собственный список уже обновлён.
    run_with_progress "$(t eng_dnsdist_apt_updating)" apt_q update \
        || log_warning "$(t eng_dnsdist_apt_update_partial)"

    if ! run_with_progress "$(t eng_dnsdist_apt_installing)" \
            apt_q install --no-install-recommends dnsdist; then
        log_error "$(t eng_dnsdist_apt_install_failed)"
        # Репозиторий БЫЛ доступен — мы только что успешно прочитали его
        # индекс. Значит дело не в сети: чаще всего пакет собран под другой
        # выпуск и не встаёт по зависимостям.
        dnsdist_install_fallback eng_dnsdist_reason_deps
        return $?
    fi

    if ! dnsdist_probe_capabilities; then
        log_error "$(tf eng_dnsdist_bad_after_install "${DNSDIST_VERSION:-?}" "${DNSDIST_FEATURES:-—}")"
        return 1
    fi

    dnsdist_refresh_caps || true
    log_success "$(tf eng_dnsdist_installed "$DNSDIST_VERSION")"
    log_dim "$(tf eng_dnsdist_features "$DNSDIST_FEATURES")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ЧТЕНИЕ НАСТРОЕК
# ═══════════════════════════════════════════════════════════════════════
dnsdist_cfg() {  # <key> <default>
    local v=""
    v=$(config_get "$1" "${2:-}") || true
    [ -n "$v" ] || v="${2:-}"
    printf '%s' "$v"
    return 0
}

# ── Лимит запросов на клиента ───────────────────────────────────────────
# Запросов в секунду с одного адреса; 0 — лимит выключен. Умолчание 200:
# квартира за роутером в пике даёт десятки запросов в секунду, сканер —
# сотни и тысячи. Ведро (burst) вдвое больше: страница со всеми её именами
# открывается разом, а не размазывается по секундам. Значение сверх
# DNSDIST_RATELIMIT_MAX прижимается — в Lua оно уедет как есть.
DNSDIST_RATELIMIT_DEFAULT="${DNSDIST_RATELIMIT_DEFAULT:-200}"
DNSDIST_RATELIMIT_MAX=100000
dnsdist_ratelimit_qps() {
    local v
    v=$(dnsdist_cfg ratelimit_qps "$DNSDIST_RATELIMIT_DEFAULT")
    case "$v" in ''|*[!0-9]*) v="$DNSDIST_RATELIMIT_DEFAULT" ;; esac
    # ГРАБЛИ (ревью 0.32.0): «08» валил $((08 * 2)) в рендере — bash читает
    # ведущий ноль как восьмеричное, а «08» — фатальная ошибка раскрытия, и
    # применение падало на КАЖДОЙ попытке, пока значение лежит в config.json.
    # «0200» уезжало в Lua как 200, а ведро считалось от восьмеричного 128.
    # Двадцать знаков ломали `-gt` (rc 2), и лимит молча исчезал из конфига.
    # Длина — ДО арифметики, 10# снимает ведущие нули.
    if [ "${#v}" -gt 6 ]; then v="$DNSDIST_RATELIMIT_MAX"; fi
    v=$((10#$v))
    if [ "$v" -gt "$DNSDIST_RATELIMIT_MAX" ]; then v="$DNSDIST_RATELIMIT_MAX"; fi
    printf '%s' "$v"
    return 0
}

# Совпадает ли живой конфиг с тем, что отрендерил бы ЭТОТ код. Сравниваются
# только строки Lua: заголовок несёт дату и версию, а поведение службы
# задают не комментарии. rc 0 — совпадает, перезапуск не нужен; rc 1 —
# отличается или сравнить нечем. Нужно обновлению поверх: перерисовка при
# каждом обновлении не должна рвать соединения клиентов ради правки README.
dnsdist_config_is_current() {
    [ -r "$DNSDIST_CONF" ] || return 1
    local tmp=""
    tmp=$(godns_mktemp) || return 1
    if ! dnsdist_render_config "$tmp" >/dev/null 2>&1; then
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi
    local rc=1
    if cmp -s <(grep -v '^--' "$tmp") <(grep -v '^--' "$DNSDIST_CONF"); then rc=0; fi
    rm -f "$tmp" 2>/dev/null || true
    return "$rc"
}

# Адреса, с которых лимит не считается: loopback и адреса этой машины, по
# одному CIDR в строке. С них приходит трафик туннеля — много честных
# клиентов сразу, — а подделать источник по TCP нельзя. Без `ip` (macOS,
# песочница тестов) — только loopback: рендер обязан оставаться
# детерминированным и не ходить в сеть.
# Известная граница: на VPS за NAT 1:1 (Oracle, AWS, GCP) публичного адреса
# на интерфейсе нет — здесь окажется приватный, а трафик туннеля к своему
# публичному адресу может вернуться с адреса шлюза. Адреса снимаются в
# момент рендера и в отпечаток не входят: сменился IP — примените заново.
dnsdist_own_addresses() {
    printf '127.0.0.0/8\n::1/128\n'
    command -v ip >/dev/null 2>&1 || return 0
    local a
    while IFS= read -r a; do
        a="${a%%/*}"
        if valid_ip4 "$a"; then printf '%s/32\n' "$a"; fi
    done <<< "$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}')"
    while IFS= read -r a; do
        a="${a%%/*}"
        if valid_ip6 "$a"; then printf '%s/128\n' "$a"; fi
    done <<< "$(ip -o -6 addr show scope global 2>/dev/null | awk '{print $4}')"
    return 0
}

# Одно значение из dumpStats() по имени счётчика. rc 1 — консоли нет.
dnsdist_stat_value() {  # <имя>
    dnsdist_is_running || return 1
    local rc=0
    rc=0; dnsdist_console 'dumpStats()' || rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$DNSDIST_CONSOLE_OUTPUT" ]; then return 1; fi
    local v=""
    v=$(printf '%s\n' "$DNSDIST_CONSOLE_OUTPUT" | awk -v k="$1" '
        { for (i = 1; i < NF; i++) if ($i == k && $(i+1) ~ /^-?[0-9]+(\.[0-9]+)?$/) { print $(i+1); exit } }') || v=""
    [ -n "$v" ] || return 1
    printf '%s' "$v"
    return 0
}

# Подпись лимита для «Состояния»: «200 зап/с на адрес · отброшено: N».
# Пока лимита не было видно нигде, роутеры целых квартир молча сидели под
# баном — и никто не мог этого увидеть со стороны сервера.
dnsdist_ratelimit_label() {
    local qps dropped=""
    qps=$(dnsdist_ratelimit_qps)
    if [ "$qps" = "0" ]; then
        printf '%s' "${YELLOW}$(t eng_ratelimit_off)${NC}"
        return 0
    fi
    dropped=$(dnsdist_stat_value rule-drop) || dropped="-"
    printf '%s' "${WHITE}$(tf eng_ratelimit_val "$qps" "$dropped")${NC}"
    return 0
}

dnsdist_cfg_bool() {  # <key> <default yes|no> -> печатает yes|no
    local v
    v=$(dnsdist_cfg "$1" "${2:-no}")
    case "$v" in
        yes|true|1|on|Y|y) printf 'yes' ;;
        *) printf 'no' ;;
    esac
    return 0
}

# Секретный путь DoH. Если его нет — генерируем и запоминаем: путь и есть
# единственный контроль доступа к DoH-эндпоинту, пустым он быть не может.
# ГРАБЛИ: раньше сюда записывался путь СО слэшем, а wizard_ports пишет БЕЗ.
# Панель склеивала "…:порт/" + значение и выдавала то «//путь», то «портпуть»
# — оператор копировал нерабочий адрес, а dnsdist сверяет путь ТОЧНО и отдаёт
# 404. Уговор: в config.json путь хранится БЕЗ ведущего слэша, слэш живёт
# только в отрендеренном Lua.
dnsdist_cfg_doh_path() {
    local p
    p=$(dnsdist_cfg doh_path "")
    p="${p#/}"
    if [ -z "$p" ]; then
        p=$(random_path 14) || p=""
        [ -n "$p" ] || p="dns-query"
        if config_set doh_path "$p" 2>/dev/null; then
            log_info "$(tf eng_doh_path_generated "$p")"
        else
            log_warning "$(tf eng_doh_path_not_saved "$p")"
        fi
    fi
    case "$p" in /*) : ;; *) p="/$p" ;; esac
    printf '%s' "$p"
    return 0
}

# Пути сертификата. ГРАБЛИ ИНТЕГРАЦИИ: lib/certs.sh НЕ копирует ничего в
# GODNS_CERT_DIR — он отдаёт линию Let's Encrypt как есть,
# /etc/letsencrypt/live/<домен>/{fullchain,privkey}.pem. Поэтому умолчание
# выводим из домена, а GODNS_CERT_DIR остаётся запасным вариантом для
# сертификата, положенного руками.
dnsdist_cert_file() {
    local v dom
    # ГРАБЛИ: движок читал cert_fullchain, а мастер пишет cert_path — их не
    # писал НИКТО, и движок всегда уходил в запасной путь по домену. Если
    # сертификат лежит не в /etc/letsencrypt/live/<домен>/ (например путь из
    # базы 3X-UI), панель показывала один файл, а dnsdist брал другой.
    v=$(dnsdist_cfg cert_path "")
    [ -n "$v" ] || v=$(dnsdist_cfg cert_fullchain "")
    if [ -n "$v" ] && [ -f "$v" ]; then printf '%s' "$v"; return 0; fi
    dom=$(dnsdist_cfg domain "")
    if [ -n "$dom" ] && [ -f "${LE_ROOT}/live/${dom}/fullchain.pem" ]; then
        printf '%s' "${LE_ROOT}/live/${dom}/fullchain.pem"
        return 0
    fi
    printf '%s' "${GODNS_CERT_DIR}/fullchain.pem"
    return 0
}

dnsdist_key_file() {
    local v dom
    v=$(dnsdist_cfg key_path "")
    [ -n "$v" ] || v=$(dnsdist_cfg cert_key "")
    if [ -n "$v" ] && [ -f "$v" ]; then printf '%s' "$v"; return 0; fi
    dom=$(dnsdist_cfg domain "")
    if [ -n "$dom" ] && [ -f "${LE_ROOT}/live/${dom}/privkey.pem" ]; then
        printf '%s' "${LE_ROOT}/live/${dom}/privkey.pem"
        return 0
    fi
    printf '%s' "${GODNS_CERT_DIR}/privkey.pem"
    return 0
}

# Выдать службе право читать privkey.pem. Реализация — в lib/certs.sh,
# здесь только вызов. СИГНАТУРА: certs_grant_read_access <домен> <пользователь>.
dnsdist_grant_cert_access() {
    local dom user
    if ! declare -F certs_grant_read_access >/dev/null 2>&1; then
        log_warning "$(t eng_certs_module_missing)"
        return 1
    fi
    dom=$(dnsdist_cfg domain "")
    if [ -z "$dom" ]; then
        log_warning "$(t eng_cert_grant_no_domain)"
        return 1
    fi
    user=$(dnsdist_service_user)
    if ! certs_grant_read_access "$dom" "$user"; then
        log_warning "$(t eng_cert_grant_failed)"
        return 1
    fi
    return 0
}

dnsdist_valid_doh_path() {
    local p="$1"
    case "$p" in
        /*) : ;;
        *) return 1 ;;
    esac
    [ "${#p}" -le 128 ] || return 1
    if [[ "$p" =~ ^/[A-Za-z0-9._~%/-]*$ ]]; then return 0; fi
    return 1
}

# ═══════════════════════════════════════════════════════════════════════
# АПСТРИМЫ
# ═══════════════════════════════════════════════════════════════════════
# КОНТРАКТ С lib/providers.sh:
#   ключ конфига `upstreams` — список записей, разделитель ';' или перевод
#   строки. Запись:
#           id|proto|host|port|sni|path
#   proto ∈ dot | doh | plain   (plain — только свой, добавленный оператором:
#           открытый :53 без шифрования; у каталога такого нет по построению)
#   host  — ЛИТЕРАЛЬНЫЙ IP (v4 или v6). Мы намеренно прибиваем апстрим к IP
#           и валидируем имя через subjectName: тогда для старта резолвера
#           не нужен никакой открытый :53 «на разогрев» — курица и яйцо
#           решаются без plaintext-запроса.
#   port  — по умолчанию 853 (dot) / 443 (doh)
#   sni   — имя из сертификата апстрима; если пусто, берём host
#   path  — только для doh, по умолчанию /dns-query
#
# Допускается укороченная форма из 5 полей (без path).
# На stdout — нормализованный TSV: id \t proto \t address \t sni \t path
dnsdist_upstreams_raw() {
    local raw=""
    raw=$(config_get upstreams "") || true
    if [ -z "$raw" ]; then
        raw="${GODNS_UPSTREAMS:-}"
    fi
    printf '%s' "$raw"
    return 0
}

dnsdist_host_is_v6() {
    case "$1" in
        *:*:*) return 0 ;;
    esac
    return 1
}

dnsdist_addr() {  # <host> <port>
    if dnsdist_host_is_v6 "$1"; then
        printf '[%s]:%s' "$1" "$2"
    else
        printf '%s:%s' "$1" "$2"
    fi
    return 0
}

dnsdist_upstreams_parse() {
    local raw n=0 rec
    raw=$(dnsdist_upstreams_raw)
    [ -n "$raw" ] || return 1

    # ';' → перевод строки, дальше единый разбор построчно.
    while IFS= read -r rec; do
        rec="${rec#"${rec%%[![:space:]]*}"}"   # ltrim
        rec="${rec%"${rec##*[![:space:]]}"}"   # rtrim
        [ -n "$rec" ] || continue
        case "$rec" in '#'*) continue ;; esac

        local id proto host port sni path rest
        IFS='|' read -r id proto host port sni path rest <<< "$rec"
        id="${id:-}"; proto="${proto:-}"; host="${host:-}"
        port="${port:-}"; sni="${sni:-}"; path="${path:-}"

        case "$proto" in
            dot|DoT|DOT) proto="dot" ;;
            doh|DoH|DOH) proto="doh" ;;
            plain|udp|do53) proto="plain" ;;
            *) log_warning "$(tf eng_up_bad_proto "${id:-?}" "${proto:-—}")"; continue ;;
        esac

        if [ -z "$id" ] || [[ ! "$id" =~ ^[A-Za-z0-9_.-]{1,40}$ ]]; then
            log_warning "$(tf eng_up_bad_id "${id:-—}")"
            continue
        fi
        if [ -z "$host" ] || [[ ! "$host" =~ ^[A-Za-z0-9.:_-]{1,255}$ ]]; then
            log_warning "$(tf eng_up_bad_host "$id" "${host:-—}")"
            continue
        fi
        # Контракт выше говорит «ЛИТЕРАЛЬНЫЙ IP», а регэксп пропускал имя.
        # Каталог всегда даёт IP, и граница спала; свой апстрим по имени её
        # разбудил бы — dnsdist в address имена не разрешает. Требуем IP
        # здесь, на границе, а не надеемся на вызывающего.
        if ! valid_ip "$host"; then
            log_warning "$(tf eng_up_host_not_ip "$id" "$host")"
            continue
        fi

        if [ -z "$port" ]; then
            case "$proto" in dot) port="853" ;; doh) port="443" ;; *) port="53" ;; esac
        fi
        if ! valid_port "$port"; then
            log_warning "$(tf eng_up_bad_port "$id" "$port")"
            continue
        fi

        if [ "$proto" = "plain" ]; then
            sni=""   # TLS нет — имени для сертификата не бывает
        else
            [ -n "$sni" ] || sni="$host"
            if [[ ! "$sni" =~ ^[A-Za-z0-9.:_-]{1,255}$ ]]; then
                log_warning "$(tf eng_up_bad_sni "$id" "$sni")"
                continue
            fi
        fi

        if [ "$proto" = "doh" ]; then
            [ -n "$path" ] || path="/dns-query"
            case "$path" in /*) : ;; *) path="/$path" ;; esac
            if ! dnsdist_valid_doh_path "$path"; then
                log_warning "$(tf eng_up_bad_path "$id" "$path")"
                continue
            fi
        else
            path=""
        fi

        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$id" "$proto" "$(dnsdist_addr "$host" "$port")" "$sni" "$path"
        n=$((n + 1))
    done <<< "$(printf '%s' "$raw" | tr ';' '\n')"

    if [ "$n" -eq 0 ]; then return 1; fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# РЕНДЕР КОНФИГА
# ═══════════════════════════════════════════════════════════════════════

# Lua-строка в одинарных кавычках. Экранируем обратный слэш и кавычку и
# выбрасываем перевод строки — иначе одно кривое значение из config.json
# превращается в исполняемый Lua.
dnsdist_lua_q() {
    local s="${1:-}"
    # ГРАБЛИ: `s="${s//\'/\\\'}"` выглядит правильно и ДАЁТ ДВА обратных
    # слэша (`a\\'b`) — внутри двойных кавычек `\'` не схлопывается, и в
    # строку замены попадает лишний слэш. В Lua это уже не экранированная
    # кавычка, а конец строки. Поэтому символы задаём переменными.
    local q="'" bs='\'
    s="${s//$'\n'/}"
    s="${s//$'\r'/}"
    s="${s//\\/\\\\}"
    s="${s//$q/$bs$q}"
    printf "'%s'" "$s"
    return 0
}

# dnsdist_render_config <outfile> [with_console: yes|no] [with_extras: yes|no]
# Необязательные аргументы нужны лестнице в dnsdist_apply. Смысл лестницы:
# резолвер не должен оставаться неустановленным из-за одной директивы,
# которую конкретная сборка dnsdist не переварила.
#   • without console — сборка без криптографии не принимает setKey();
#   • without extras  — на случай, если сборка не знает какую-то из
#     необязательных настроек в хвосте файла.
# Обязательная часть (ACL, апстримы, политика, слушатели) не отключается
# никогда: без неё это уже не goDNS.
dnsdist_render_config() {
    local out="${1:-}"
    local want_console="${2:-}"
    local want_extras="${3:-yes}"
    if [ -z "$out" ]; then
        log_error "$(t eng_render_no_out)"
        return 1
    fi

    local domain doh_port dot_port doh_path policy
    local cert key cache cache_size cache_ttl_max cache_ttl_min
    local acl plain_listen console console_addr webserver

    domain=$(dnsdist_cfg domain "")
    doh_port=$(dnsdist_cfg doh_port "8443")
    dot_port=$(dnsdist_cfg dot_port "8853")
    local dot_enabled; dot_enabled=$(dnsdist_cfg dot_enabled "no")
    local rl_qps; rl_qps=$(dnsdist_ratelimit_qps)
    [ "$dot_enabled" = "yes" ] || dot_enabled="no"
    doh_path=$(dnsdist_cfg_doh_path)
    policy=$(dnsdist_cfg policy "fastest")
    cert=$(dnsdist_cert_file)
    key=$(dnsdist_key_file)
    cache=$(dnsdist_cfg_bool cache yes)
    cache_size=$(dnsdist_cfg cache_size "100000")
    cache_ttl_max=$(dnsdist_cfg cache_ttl_max "86400")
    cache_ttl_min=$(dnsdist_cfg cache_ttl_min "0")
    acl=$(dnsdist_cfg acl "0.0.0.0/0,::/0")
    plain_listen=$(dnsdist_cfg plain_listen "127.0.0.1:53")
    webserver=$(dnsdist_cfg_bool webserver no)
    console_addr=$(dnsdist_cfg console_addr "127.0.0.1:5199")
    if [ -n "$want_console" ]; then
        console="$want_console"
    else
        console=$(dnsdist_cfg_bool console yes)
    fi

    # ── валидация до единого байта записи ───────────────────────────────
    if ! valid_port "$doh_port"; then log_error "$(tf eng_bad_doh_port "$doh_port")"; return 1; fi
    if ! valid_port "$dot_port"; then log_error "$(tf eng_bad_dot_port "$dot_port")"; return 1; fi
    if [ "$dot_enabled" = "yes" ] && [ "$doh_port" = "$dot_port" ]; then
        log_error "$(tf eng_ports_clash "$doh_port")"; return 1
    fi
    if ! dnsdist_valid_doh_path "$doh_path"; then log_error "$(tf eng_bad_doh_path "$doh_path")"; return 1; fi
    case "$cache_size"    in ''|*[!0-9]*) cache_size="100000" ;; esac
    case "$cache_ttl_max" in ''|*[!0-9]*) cache_ttl_max="86400" ;; esac
    case "$cache_ttl_min" in ''|*[!0-9]*) cache_ttl_min="0" ;; esac

    case "$policy" in
        rr|roundrobin|round-robin|round_robin|circle) policy="roundrobin" ;;
        *) policy="fastest" ;;
    esac

    # Апстримы читаем ДО открытия выходного файла: пустой список — отказ,
    # а не пустой конфиг, которым мы затрём рабочий.
    local parsed=""
    parsed=$(dnsdist_upstreams_parse) || parsed=""
    if [ -z "$parsed" ]; then
        log_error "$(t eng_no_upstreams)"
        return 1
    fi

    local -a recs=()
    local ln
    while IFS= read -r ln; do
        if [ -n "$ln" ]; then recs+=("$ln"); fi
    done <<< "$parsed"
    if [ "${#recs[@]}" -eq 0 ]; then
        log_error "$(t eng_no_upstreams)"
        return 1
    fi

    # ACL: "a,b" или "a b" → Lua-список.
    local acl_lua="" a
    for a in $(printf '%s' "$acl" | tr ',' ' '); do
        [ -n "$a" ] || continue
        if [ -n "$acl_lua" ]; then acl_lua="${acl_lua}, "; fi
        acl_lua="${acl_lua}$(dnsdist_lua_q "$a")"
    done
    [ -n "$acl_lua" ] || acl_lua="'0.0.0.0/0', '::/0'"

    local cert_q key_q path_q
    cert_q=$(dnsdist_lua_q "$cert")
    key_q=$(dnsdist_lua_q "$key")
    path_q=$(dnsdist_lua_q "$doh_path")

    # umask НЕ трогаем: common.sh поставил 077 глобально, и подъём его до 027
    # здесь протёк бы во весь остальной скрипт. Права ставим chmod'ом в конце.
    : > "$out" || { log_error "$(tf eng_render_write_failed "$out")"; return 1; }

    {
    cat <<HDR
-- ═══════════════════════════════════════════════════════════════════
--  /etc/dnsdist/dnsdist.conf — GENERATED by goDNS Pro ${GODNS_VERSION:-?}
--  $(date -u '+%Y-%m-%d %H:%M:%S UTC')
--
--  Manual edits will be LOST the next time settings are applied.
--  Edit ${GODNS_CONFIG} and run goDNS Pro instead.
--
--  Resolver domain: ${domain:-—}
-- ═══════════════════════════════════════════════════════════════════

HDR

    cat <<'ACLC'
-- ───────────────────────────────────────────────────────────────────
-- ACL — THE MOST IMPORTANT LINE IN THIS ENTIRE FILE.
--
-- By default dnsdist accepts queries only from loopback, RFC1918 and
-- link-local. And that ACL applies TO DoH AND DoT AS WELL, not just
-- to plain UDP/TCP 53. Without the line below everything looks
-- perfect — the service is running, the port is listening, the TLS
-- handshake succeeds — yet every external client gets a silent
-- refusal, and the logs stay empty.
-- The classic case of "everything seems fine, but nothing works".
-- ───────────────────────────────────────────────────────────────────
ACLC
    printf 'setACL({%s})\n\n' "$acl_lua"

    # Ограничение частоты — ПОШТУЧНЫЙ лимит на адрес, а не бан.
    # Резолвер, открытый интернету, обязан уметь отбиваться (на приёмке 200
    # запросов подряд с одного адреса когда-то прошли все 200), и адрес
    # клиента здесь настоящий: DoH и DoT идут поверх TCP.
    #
    # ГРАБЛИ (0.32.0): здесь стоял dynBlockRulesGroup():setQueryRate(80, 10,
    # …, 60) — больше 800 запросов за 10 с с одного адреса, и адрес
    # выключался ЦЕЛИКОМ на минуту, а бан продлевался, пока клиент
    # продолжал слать. «Порог щедрый» считался из расчёта «один адрес —
    # одна квартира». Роутер на OpenWrt с Forkop/Podkop собирает DNS всей
    # квартиры в один поток (кэш dnsmasq у него выключен под FakeIP), а с
    # включённым «DNS через прокси» все такие роутеры приходят с адреса
    # самого VPS. В вечерний час порог брался, и у людей «пропадал
    # YouTube» — до выключения goDNS. Воспроизведено на dnsdist 2.1.1:
    # DoH получал HTTP 403, DoT — разрыв, UDP — тишину; при 100 qps бан не
    # снимался вовсе (12 % ответов).
    #
    # MaxQPSIPRule — token bucket по адресу (/32 и /64): отбрасывается
    # только то, что сверх ведра, и ровно в ту секунду; замолчал — отвечаем
    # сразу. Что видит клиент на лишний запрос: DoH — HTTP 403 на него одного,
    # соединение живёт; DoT — dnsdist на Drop ЗАКРЫВАЕТ это соединение
    # (dnsdist-tcp.cc, terminateClientConnection), клиент открывает новое и
    # повторяет запросы в полёте. Проверено на стенде: 300 qps по DoT —
    # 77 % ответов и 5 разрывов из 8 соединений, сразу после — 100 %.
    # Свои адреса (loopback и адреса этой машины) исключены: с них
    # приходит туннель, то есть много честных клиентов разом. NotRule стоит
    # в AndRule ПЕРВЫМ: AndRule останавливается на первом ложном, и ведро
    # для своих адресов даже не заводится. 0 — лимита нет вовсе.
    printf -- '-- Per-client query limit: a token bucket per address, NOT a ban.\n'
    printf -- '-- Only the excess is dropped, and only in the second it exceeds the bucket:\n'
    printf -- '-- DoH gets HTTP 403 for that query, a DoT connection is closed and reopened by the client.\n'
    printf -- "-- The server's own addresses carry tunnel traffic and are exempt.\n"
    if [ "$rl_qps" -gt 0 ] 2>/dev/null; then
        printf 'local godns_own = newNMG()\n'
        local own
        while IFS= read -r own; do
            [ -n "$own" ] || continue
            printf "godns_own:addMask('%s')\n" "$own"
        done <<< "$(dnsdist_own_addresses)"
        printf "addAction(AndRule({NotRule(NetmaskGroupRule(godns_own)), MaxQPSIPRule(%s, 32, 64, %s)}), DropAction(), {name='godns-ratelimit'})\n\n" \
            "$rl_qps" "$((rl_qps * 2))"
    else
        printf -- '-- Per-client query limit disabled by goDNS Pro setting (ratelimit_qps=0).\n\n'
    fi

    cat <<'UPH'
-- ───────────────────────────────────────────────────────────────────
-- Upstreams. The catalogue is encrypted ONLY (DoT/DoH); a plaintext :53
-- server appears here solely when the operator added it by hand, and it
-- is marked as such below.
-- Every upstream is pinned to a literal IP, with the certificate name
-- set via subjectName. So the resolver needs no "warm-up" open DNS to
-- learn its own upstream's address: the chicken-and-egg problem is
-- solved by the address, while trust comes from subjectName +
-- validateCertificates.
-- ───────────────────────────────────────────────────────────────────
UPH

    local rec id proto address sni path
    for rec in "${recs[@]}"; do
        IFS=$'\t' read -r id proto address sni path <<< "$rec"
        if [ "$proto" = "plain" ]; then
            printf -- '-- %s — plain DNS, operator-added: NOT encrypted\n' "$id"
            printf 'newServer({address=%s, %sname=%s, useClientSubnet=false})\n' \
                "$(dnsdist_lua_q "$address")" "$(dnsdist_healthcheck_opts)" \
                "$(dnsdist_lua_q "$id")"
        elif [ "$proto" = "doh" ]; then
            printf -- '-- %s — DoH\n' "$id"
            printf 'newServer({address=%s, tls=%s, subjectName=%s, dohPath=%s, validateCertificates=true, %sname=%s, useClientSubnet=false})\n' \
                "$(dnsdist_lua_q "$address")" "'openssl'" \
                "$(dnsdist_lua_q "$sni")" "$(dnsdist_lua_q "$path")" \
                "$(dnsdist_healthcheck_opts)" \
                "$(dnsdist_lua_q "$id")"
        else
            printf -- '-- %s — DoT\n' "$id"
            printf 'newServer({address=%s, tls=%s, subjectName=%s, validateCertificates=true, %sname=%s, useClientSubnet=false})\n' \
                "$(dnsdist_lua_q "$address")" "'openssl'" \
                "$(dnsdist_lua_q "$sni")" "$(dnsdist_healthcheck_opts)" \
                "$(dnsdist_lua_q "$id")"
        fi
    done
    printf '\n'

    cat <<'POLH'
-- ───────────────────────────────────────────────────────────────────
-- Upstream selection policy.
--   leastOutstanding — "whoever is faster". Picks the server with the
--     fewest queries in flight, ties broken by the lowest MEASURED
--     latency, averaged over the last 128 responses. For a lightly
--     loaded resolver this is "the fast one wins" in practice.
--   roundrobin — a true circle: every next query goes to the next
--     live server.
-- ───────────────────────────────────────────────────────────────────
POLH
    if [ "$policy" = "roundrobin" ]; then
        cat <<'POLRR'
setServerPolicy(roundrobin)
-- Without this, roundrobin keeps picking a server even when ALL of
-- them are down, and the client gets a timeout instead of an honest
-- "no server available".
setRoundRobinFailOnNoServer(true)
POLRR
    else
        printf 'setServerPolicy(leastOutstanding)\n'
    fi
    printf '\n'

    if [ "$cache" = "yes" ]; then
        cat <<'CACHH'
-- ───────────────────────────────────────────────────────────────────
-- Packet cache. An honest caveat: with the cache enabled NONE of the
-- policies above is observable per query — a cache hit never reaches
-- the upstream. To see rotation, query unique labels: <random>.example.
-- ───────────────────────────────────────────────────────────────────
CACHH
        printf 'godns_pc = newPacketCache(%s, {maxTTL=%s, minTTL=%s, temporaryFailureTTL=60, staleTTL=60, dontAge=false})\n' \
            "$cache_size" "$cache_ttl_max" "$cache_ttl_min"
        printf "getPool(''):setCache(godns_pc)\n\n"
    else
        printf -- '-- Packet cache disabled by goDNS Pro setting (cache=no).\n\n'
    fi

    cat <<'LSTH'
-- ───────────────────────────────────────────────────────────────────
-- Inbound listeners.
--
-- DoH: the path list is matched EXACTLY (exactPathMatching is on by
-- default since 1.5.0), any other path gets a 404. This is what makes
-- the "own path" a real protection rather than a decoration.
-- ───────────────────────────────────────────────────────────────────
LSTH
    printf "addDOHLocal('0.0.0.0:%s', %s, %s, { %s }, { reusePort=true })\n" \
        "$doh_port" "$cert_q" "$key_q" "$path_q"
    # DoT поднимается ТОЛЬКО по явному включению и по умолчанию выключен.
    #
    # Причина: у DoT нет и не может быть секретного пути — протокол его не
    # предусматривает. Значит единственная преграда для чужих — ACL, а он у
    # нас открыт намеренно: клиенты туннеля приходят с любых адресов. На
    # приёмочном стенде посторонний клиент начал пользоваться резолвером в
    # первые минуты после установки. DoH такой дырой не является: путь
    # сверяется точно, и без него отдаётся 404.
    #
    # DoT нужен ровно для одного: Android «Частный DNS» умеет ТОЛЬКО DoT,
    # только по имени хоста и только порт 853. Кому это не нужно — тому DoT
    # не нужен вовсе.
    if [ "$dot_enabled" = "yes" ]; then
        printf "addTLSLocal('0.0.0.0:%s', %s, %s, { reusePort=true })\n\n" \
            "$dot_port" "$cert_q" "$key_q"
    else
        printf -- '-- DoT disabled by goDNS Pro setting (dot_enabled=no).\n'
        printf -- '-- DoT has no secret path, so once enabled it is open to everyone.\n\n'
    fi

    if [ -n "$plain_listen" ] && [ "$plain_listen" != "none" ]; then
        cat <<'PLH'
-- Plain DNS. dnsdist keeps at least one Do53 listener no matter what;
-- unless told explicitly, that is 127.0.0.1:53. We pin it to loopback
-- so that the open ACL above does NOT turn the server into an open
-- DNS resolver (a DDoS amplifier). Only DoH and DoT face the outside.
PLH
        printf 'setLocal(%s)\n\n' "$(dnsdist_lua_q "$plain_listen")"
    fi

    if [ "$webserver" = "yes" ]; then
        local ws_addr ws_pass
        ws_addr=$(dnsdist_cfg webserver_addr "127.0.0.1:8083")
        ws_pass=$(dnsdist_cfg webserver_password "")
        printf 'webserver(%s)\n' "$(dnsdist_lua_q "$ws_addr")"
        if [ -n "$ws_pass" ]; then
            printf 'setWebserverConfig({password=%s, acl=%s})\n\n' \
                "$(dnsdist_lua_q "$ws_pass")" "'127.0.0.1, ::1'"
        else
            printf '\n'
        fi
    else
        printf -- '-- Web interface disabled: a needless open surface on a DNS server.\n\n'
    fi

    if [ "$console" = "yes" ]; then
        local ckey
        ckey=$(dnsdist_cfg console_key "")
        if [ -z "$ckey" ]; then
            ckey=$(openssl rand -base64 32 2>/dev/null | tr -d '\n') || ckey=""
            if [ -z "$ckey" ]; then
                ckey=$(python3 -c 'import base64,os,sys;sys.stdout.write(base64.b64encode(os.urandom(32)).decode())' 2>/dev/null) || ckey=""
            fi
            if [ -n "$ckey" ]; then
                config_set console_key "$ckey" 2>/dev/null || true
            fi
        fi
        if [ -n "$ckey" ]; then
            cat <<'CONH'
-- ───────────────────────────────────────────────────────────────────
-- Control console — loopback only. Needed for showServers() and for
-- reloadAllCertificates(): reloading certificates without dropping
-- connections is possible ONLY from here.
-- ───────────────────────────────────────────────────────────────────
CONH
            printf 'setKey(%s)\n' "$(dnsdist_lua_q "$ckey")"
            printf 'controlSocket(%s)\n' "$(dnsdist_lua_q "$console_addr")"
            printf "setConsoleACL({'127.0.0.1', '::1'})\n"
            printf 'setConsoleConnectionsLogging(false)\n\n'
        else
            printf -- '-- Console requested, but key generation failed.\n\n'
        fi
    else
        printf -- '-- Control console disabled.\n\n'
    fi

    if [ "$want_extras" = "yes" ]; then
        cat <<'TAIL'
-- ───────────────────────────────────────────────────────────────────
-- Extras (optional — the ladder in dnsdist_apply drops them when a
-- particular dnsdist build does not know one of these directives).
-- ───────────────────────────────────────────────────────────────────
setMaxTCPClientThreads(24)
setMaxTCPConnectionsPerClient(50)
setMaxUDPOutstanding(65535)

-- Log budget, layer 1 (lib/logs.sh): do not write what is not needed.
-- setVerbose(true) and health-check logging are the main sources of
-- log volume in dnsdist: a single flapping upstream can produce
-- gigabytes per day. The default here is "false" anyway, but it is
-- WRITTEN OUT explicitly so the intent stays visible both in the
-- config and in a diff after someone else's edit.
setVerbose(false)
setVerboseHealthChecks(false)

-- Drop what the resolver must not answer: ANY queries are the classic
-- amplification vector.
addAction(QTypeRule(DNSQType.ANY), RCodeAction(DNSRCode.REFUSED))
TAIL
    else
        printf -- '-- Optional settings dropped: this dnsdist build did not accept them.\n'
    fi
    } >> "$out" || { log_error "$(tf eng_render_write_failed "$out")"; return 1; }

    chmod 0640 "$out" 2>/dev/null || true
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ПРОВЕРКА И УСТАНОВКА КОНФИГА
# ═══════════════════════════════════════════════════════════════════════
DNSDIST_CHECK_OUTPUT=""

# Показать первые N строк текста через log_dim.
# ГРАБЛИ: `printf … | head -n N | while read` под `set -o pipefail` даёт
# ненулевой код (head закрывает трубу, printf получает SIGPIPE) — ровно тот
# же класс, что `tr … | head -c N`. Считаем строки сами, без head.
dnsdist_dump_lines() {
    local text="${1:-}" max="${2:-25}" n=0 ln
    [ -n "$text" ] || return 0
    while IFS= read -r ln; do
        log_dim "$ln"
        n=$((n + 1))
        if [ "$n" -ge "$max" ]; then break; fi
    done <<< "$text"
    return 0
}

dnsdist_dump_journal() {
    local max="${1:-20}" text=""
    text=$(journalctl -u "$DNSDIST_UNIT" -n "$max" --no-pager 2>/dev/null) || text=""
    dnsdist_dump_lines "$text" "$max"
    return 0
}

# Последние N строк вывода СЛУЖБЫ, откуда бы он ни шёл: свой файл, если
# drop-in увёл его туда (обычный случай), иначе журнал systemd.
#
# Существует отдельно от dnsdist_logs, потому что тем, кто разбирает вывод
# (отчёт по апстримам), нужен чистый stdout, а не показ через log_dim.
dnsdist_recent_lines() {
    local max="${1:-200}"
    if [ -r "$DNSDIST_LOG" ] && [ -s "$DNSDIST_LOG" ]; then
        tail -n "$max" "$DNSDIST_LOG" 2>/dev/null || true
        return 0
    fi
    journalctl -u "$DNSDIST_UNIT" -n "$max" --no-pager 2>/dev/null || true
    return 0
}

dnsdist_check_config() {  # <file>
    local f="$1" rc=0
    DNSDIST_CHECK_OUTPUT=""
    if ! command -v "$DNSDIST_BIN" >/dev/null 2>&1; then
        DNSDIST_CHECK_OUTPUT="dnsdist not installed"
        return 2
    fi
    DNSDIST_CHECK_OUTPUT=$("$DNSDIST_BIN" --check-config -C "$f" 2>&1) || rc=$?
    # ГРАБЛИ, снятые с dnsdist 2.0.2: неизвестный параметр внутри
    # newServer() он НЕ считает ошибкой — печатает «Unknown key 'x' given -
    # ignored» и заканчивает «Configuration OK!» с кодом 0. Опечатка в
    # validateCertificates прошла бы ворота и молча выключила проверку
    # сертификата апстрима. Ворота обязаны читать вывод, а не только код.
    if [ "$rc" -eq 0 ] && printf '%s' "$DNSDIST_CHECK_OUTPUT" | grep -q 'Unknown key'; then
        rc=1
    fi
    return "$rc"
}

# Пользователь службы: берём из юнита, если он есть; иначе — умолчание.
dnsdist_service_user() {
    local u=""
    u=$(systemctl show -p User --value "$DNSDIST_UNIT" 2>/dev/null) || true
    [ -n "$u" ] || u="$DNSDIST_USER"
    printf '%s' "$u"
    return 0
}

# systemd умеет StandardOutput=append: (O_APPEND) начиная с 240. На Ubuntu
# 22.04 стоит 249, но проверять всё равно надо: юнит с неизвестной директивой
# не стартует, и ценой была бы не «нет логов», а «нет резолвера».
dnsdist_systemd_ver() {
    local out="" v=""
    out=$(systemctl --version 2>/dev/null) || out=""
    v="${out#systemd }"
    v="${v%%[!0-9]*}"
    case "$v" in ''|*[!0-9]*) v="0" ;; esac
    printf '%s' "$v"
    return 0
}

# rc0 — можно уводить вывод службы в файл.
dnsdist_can_log_to_file() {
    local dir
    dir=$(dirname "$DNSDIST_LOG")
    [ "$(dnsdist_systemd_ver)" -ge 240 ] 2>/dev/null || return 1
    [ -d "$dir" ] || return 1
    [ -w "$dir" ] || return 1
    return 0
}

# systemd drop-in вместо собственного юнита: пакет dnsdist приносит свой,
# и конкурирующий файл — прямой путь к «почему запускается старый конфиг».
dnsdist_write_dropin() {
    local doh_port dot_port need_caps="no" body=""
    doh_port=$(dnsdist_cfg doh_port "8443")
    dot_port=$(dnsdist_cfg dot_port "8853")
    local dot_enabled; dot_enabled=$(dnsdist_cfg dot_enabled "no")
    [ "$dot_enabled" = "yes" ] || dot_enabled="no"
    if [ "$doh_port" -lt 1024 ] 2>/dev/null; then need_caps="yes"; fi
    if [ "$dot_port" -lt 1024 ] 2>/dev/null; then need_caps="yes"; fi

    # Каталог логов должен существовать ДО старта юнита: systemd открывает
    # файл сам, и на отсутствующем каталоге юнит падает целиком.
    if declare -F godns_logs_ensure_dir >/dev/null 2>&1; then
        godns_logs_ensure_dir || true
    else
        install -d -m 0750 "$(dirname "$DNSDIST_LOG")" 2>/dev/null || true
    fi

    body="[Service]"$'\n'
    # Вывод службы — в свой файл, а не в общий журнал systemd. Если систему
    # не уговорить (старый systemd, каталог не создался), молча остаёмся на
    # journald: логи в неудобном месте лучше, чем неработающий резолвер.
    if dnsdist_can_log_to_file; then
        body="${body}StandardOutput=append:${DNSDIST_LOG}"$'\n'
        body="${body}StandardError=append:${DNSDIST_LOG}"$'\n'
    fi
    # Сертификаты Let's Encrypt отдаются группе ssl-cert (это делает
    # lib/certs.sh). Служба обязана состоять в ней, иначе _dnsdist не
    # прочитает privkey.pem и dnsdist не стартует вовсе.
    if getent group ssl-cert >/dev/null 2>&1; then
        body="${body}SupplementaryGroups=ssl-cert"$'\n'
    fi
    if [ "$need_caps" = "yes" ]; then
        body="${body}AmbientCapabilities=CAP_NET_BIND_SERVICE"$'\n'
    fi
    body="${body}Restart=on-failure"$'\n'
    body="${body}RestartSec=5"$'\n'
    # ExecReload: у пакетного юнита его нет вовсе, поэтому `systemctl reload`
    # всегда падал, и deploy-хук certbot скатывался в restart — с обрывом
    # всех клиентских соединений при каждом перевыпуске сертификата.
    # Консольный reloadAllCertificates() перечитывает сертификат живьём.
    # ExecReload исполняется от пользователя юнита (_dnsdist): конфиг он
    # читает по группе, консоль на 127.0.0.1 доступна. Если консоль в
    # конфиге не настроена — reload честно вернёт ошибку, и хук, как и
    # раньше, откатится на restart.
    # Абсолютный путь: systemd ищет не-абсолютные Exec* лишь с v239 и по
    # своему фиксированному списку — надёжнее разрешить самим.
    local reload_bin=""
    reload_bin=$(command -v "$DNSDIST_BIN" 2>/dev/null) || reload_bin=""
    if [ -n "$reload_bin" ]; then
        body="${body}ExecReload=${reload_bin} -c -C ${DNSDIST_CONF} -e reloadAllCertificates()"$'\n'
    fi

    install -d -m 0755 "$DNSDIST_DROPIN_DIR" 2>/dev/null || true
    printf '# goDNS Pro — не редактировать, файл перезаписывается.\n%s' "$body" > "$DNSDIST_DROPIN" || return 1
    chmod 0644 "$DNSDIST_DROPIN" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
    return 0
}

# ПОРЯДОК ВАЖЕН. Сертификаты должны быть на месте и доступны _dnsdist
# Кто держит addr:port на loopback — под Do53 нужны ОБА протокола: dnsdist
# биндит и TCP, и UDP, а `ss -t` в одиночку не видит dnsmasq, слушающий
# только UDP. Wildcard-слушатель (0.0.0.0:53) тоже конфликт: при занятом
# wildcard не привяжется НИ ОДИН конкретный адрес этого порта.
# Печатает «имя (pid=N)» или ничего.
_dnsdist_do53_owner() {
    local addr="$1" port="$2"
    ss -Hlnptu 2>/dev/null | awk -v a="${addr}:${port}" \
        -v w1="0.0.0.0:${port}" -v w2="*:${port}" \
        '$5 == a || $5 == w1 || $5 == w2 { print; exit }' \
        | sed -n 's/.*users:((\("[^"]*"\),pid=\([0-9]*\).*/\1 (pid=\2)/p' | tr -d '"'
}

# Разрулить адрес плоского Do53-слушателя ДО рендера конфига.
#
# ГРАБЛИ (отчёт пользователя, Unbound на 127.0.0.1:53): `--check-config`
# сокеты НЕ открывает, поэтому проверка проходила, а служба падала уже на
# старте — «Fatal error: binding socket to 127.0.0.1:53: Address already
# in use». Автоустановка не завершалась, хотя DoH/DoT были полностью
# исправны. Плоский слушатель — удобство для самого сервера, а не суть
# продукта, и ронять из-за него установку нельзя.
#
# Порядок решения:
#   • адрес задан оператором в config.json → уважаем; занят чужим —
#     громко предупреждаем (старт, скорее всего, упадёт), но не меняем;
#   • умолчание 127.0.0.1:53 свободно или занято НАШИМ dnsdist → оставляем;
#   • занято чужим (Unbound, dnsmasq, AdGuard Home…) → идём по
#     127.0.0.2…127.0.0.9, первый свободный СОХРАНЯЕМ в config.json —
#     иначе следующий engine_apply вернул бы конфликт;
#   • чужой wildcard 0.0.0.0:53 → не привяжется ничто, слушатель честно
#     выключаем (plain_listen=none) и называем виновника.
dnsdist_resolve_plain_listen() {
    local explicit="" cur="" addr="" port="" owner="" ourpid="" cand=""
    explicit=$(config_get plain_listen "") || explicit=""
    cur="${explicit:-127.0.0.1:53}"
    [ "$cur" = "none" ] && return 0

    addr="${cur%:*}"; port="${cur##*:}"
    case "$port" in ''|*[!0-9]*) port="53" ;; esac

    owner=$(_dnsdist_do53_owner "$addr" "$port") || owner=""
    [ -z "$owner" ] && return 0

    ourpid=$(systemctl show -p MainPID --value "$DNSDIST_UNIT" 2>/dev/null) || ourpid=""
    if dnsdist_port_is_ours "$owner" "$ourpid"; then
        return 0
    fi

    if [ -n "$explicit" ]; then
        log_warning "$(tf eng_do53_explicit_busy "$cur" "$owner")"
        return 0
    fi

    # Чужой wildcard закрывает ВЕСЬ порт — переезд по loopback не поможет.
    if ss -Hlnptu 2>/dev/null | awk -v w1="0.0.0.0:${port}" -v w2="*:${port}" \
            '$5 == w1 || $5 == w2 { found=1; exit } END { exit !found }'; then
        log_warning "$(tf eng_do53_wildcard "$owner")"
        config_set plain_listen "none" || true
        return 0
    fi

    local n
    for n in 2 3 4 5 6 7 8 9; do
        cand="127.0.0.${n}"
        if [ -z "$(_dnsdist_do53_owner "$cand" "$port")" ]; then
            log_warning "$(tf eng_do53_busy "$cur" "$owner")"
            log_info "$(tf eng_do53_moved "${cand}:${port}")"
            config_set plain_listen "${cand}:${port}" || true
            return 0
        fi
    done
    log_warning "$(tf eng_do53_none_free "$owner")"
    config_set plain_listen "none" || true
    return 0
}

# ДО --check-config: dnsdist в режиме проверки реально открывает cert/key,
# создавая TLS-фронтенды, и на отсутствующем файле честно падает.
dnsdist_apply() {
    require_root || return 1

    local cert key
    cert=$(dnsdist_cert_file)
    key=$(dnsdist_key_file)

    if [ ! -r "$cert" ] || [ ! -r "$key" ]; then
        log_error "$(tf eng_cert_missing "$cert" "$key")"
        log_dim   "$(t eng_cert_hint)"
        return 1
    fi

    # Права на чтение выдаёт lib/certs.sh — здесь мы её только зовём.
    # ПОРЯДОК: до --check-config и до рестарта. dnsdist работает от _dnsdist
    # и privkey.pem от certbot без этого не прочитает.
    dnsdist_grant_cert_access || true

    log_step "$(t eng_apply_step)"

    # До рендера: рендер читает plain_listen из config.json, и решение о
    # переезде должно успеть туда записаться.
    dnsdist_resolve_plain_listen || true

    local tmp=""
    tmp=$(godns_mktemp) || { log_error "$(t eng_tmp_failed)"; return 1; }

    if ! dnsdist_render_config "$tmp"; then
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi

    local rc=0
    dnsdist_check_config "$tmp" || rc=$?

    # Лестница. Резолвер не должен остаться неустановленным из-за одной
    # директивы, которую эта сборка dnsdist не переварила.
    # Ступень 1: сборка без криптографии не примет setKey() — снимаем консоль.
    local console_off="no"
    if [ "$rc" -ne 0 ] && [ "$(dnsdist_cfg_bool console yes)" = "yes" ]; then
        log_warning "$(t eng_check_retry_no_console)"
        if dnsdist_render_config "$tmp" "no"; then
            rc=0
            dnsdist_check_config "$tmp" || rc=$?
            if [ "$rc" -eq 0 ]; then
                console_off="yes"
                config_set console no 2>/dev/null || true
                engine_cap_set has_console no || true
                log_warning "$(t eng_console_disabled)"
            fi
        fi
    fi
    # Ступень 2: снимаем необязательный хвост. Обязательную часть — ACL,
    # апстримы, политику, слушатели — не трогаем: без неё ставить нечего.
    if [ "$rc" -ne 0 ]; then
        local keep_console="yes"
        if [ "$console_off" = "yes" ] || [ "$(dnsdist_cfg_bool console yes)" = "no" ]; then
            keep_console="no"
        fi
        log_warning "$(t eng_check_retry_no_extras)"
        if dnsdist_render_config "$tmp" "$keep_console" "no"; then
            rc=0
            dnsdist_check_config "$tmp" || rc=$?
            if [ "$rc" -eq 0 ]; then
                log_warning "$(t eng_extras_dropped)"
            fi
        fi
    fi

    if [ "$rc" -ne 0 ]; then
        log_error "$(t eng_check_failed)"
        dnsdist_dump_lines "$DNSDIST_CHECK_OUTPUT" 25
        log_warning "$(tf eng_check_kept_old "$DNSDIST_CONF")"
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi
    log_success "$(t eng_check_ok)"

    install -d -m 0755 "$DNSDIST_ETC" 2>/dev/null || true
    if [ -f "$DNSDIST_CONF" ]; then
        cp -a "$DNSDIST_CONF" "${DNSDIST_CONF}.godns.bak" 2>/dev/null || true
    fi

    # Конфиг содержит ключ консоли — не должен быть читаем всему миру.
    # Служба работает от _dnsdist, значит нужна группа.
    if ! install -m 0640 "$tmp" "$DNSDIST_CONF"; then
        log_error "$(tf eng_install_conf_failed "$DNSDIST_CONF")"
        rm -f "$tmp" 2>/dev/null || true
        return 1
    fi
    rm -f "$tmp" 2>/dev/null || true

    local svc_user svc_group
    svc_user=$(dnsdist_service_user)
    svc_group=$(systemctl show -p Group --value "$DNSDIST_UNIT" 2>/dev/null) || true
    [ -n "$svc_group" ] || svc_group="$svc_user"
    # ГРАБЛИ: запасным вариантом здесь стоял chmod 0644 — то есть конфиг с
    # КЛЮЧОМ КОНСОЛИ и секретным путём DoH становился читаем любому
    # локальному пользователю, ровно вопреки комментарию выше. Группы
    # службы нет только когда пользователя _dnsdist нет вовсе — а тогда
    # dnsdist работает от root и читает 0640 root:root без послаблений.
    if getent group "$svc_group" >/dev/null 2>&1; then
        chown "root:${svc_group}" "$DNSDIST_CONF" 2>/dev/null || true
    fi
    chmod 0640 "$DNSDIST_CONF" 2>/dev/null || true

    dnsdist_write_dropin || log_warning "$(t eng_dropin_failed)"

    # Предупреждаем о ЧУЖОМ хозяине порта ДО рестарта: иначе оператор
    # получит невнятное «job failed» вместо «порт занят nginx».
    #
    # ГРАБЛИ: при повторном применении (смена пути, смена апстримов) порт
    # держит НАШ же работающий dnsdist. Без проверки владельца оператор
    # видел «Порт 4443 уже занят» рядом с «Конфигурация применена» и не
    # мог понять, всё запустилось или нет. Своего не считаем занятым.
    local p owner ourpid
    ourpid=$(systemctl show -p MainPID --value "$DNSDIST_UNIT" 2>/dev/null) || ourpid=""
    for p in "$(dnsdist_cfg doh_port 8443)" "$(dnsdist_cfg dot_port 8853)"; do
        port_in_use "$p" || continue
        owner=$(port_owner "$p") || owner=""
        if dnsdist_port_is_ours "$owner" "$ourpid"; then continue; fi
        log_warning "$(tf eng_port_busy "$p" "${owner:-?}")"
    done

    systemctl enable "$DNSDIST_UNIT" >/dev/null 2>&1 || true
    if ! systemctl restart "$DNSDIST_UNIT" >/dev/null 2>&1; then
        log_error "$(tf eng_restart_failed "$DNSDIST_UNIT")"
        dnsdist_dump_journal 20
        return 1
    fi

    # Служба может «стартовать» и через секунду упасть — проверяем факт.
    sleep 1
    if ! dnsdist_is_running; then
        log_error "$(tf eng_not_running_after_start "$DNSDIST_UNIT")"
        dnsdist_dump_journal 20
        return 1
    fi

    log_success "$(t eng_applied)"
    return 0
}

# dnsdist НЕ перечитывает конфигурацию на лету: SIGHUP только переоткрывает
# логи. Поэтому «перезагрузка» здесь — это проверка + честный рестарт.
dnsdist_reload() {
    require_root || return 1
    if [ ! -f "$DNSDIST_CONF" ]; then
        log_error "$(tf eng_conf_absent "$DNSDIST_CONF")"
        return 1
    fi
    local rc=0
    dnsdist_check_config "$DNSDIST_CONF" || rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "$(t eng_check_failed)"
        dnsdist_dump_lines "$DNSDIST_CHECK_OUTPUT" 25
        return 1
    fi
    log_dim "$(t eng_reload_is_restart)"
    if ! systemctl restart "$DNSDIST_UNIT" >/dev/null 2>&1; then
        log_error "$(tf eng_restart_failed "$DNSDIST_UNIT")"
        return 1
    fi
    log_success "$(t eng_reloaded)"
    return 0
}

# ── Консоль ─────────────────────────────────────────────────────────────
dnsdist_console_configured() {
    [ -r "$DNSDIST_CONF" ] || return 1
    grep -q '^[[:space:]]*controlSocket(' "$DNSDIST_CONF" 2>/dev/null || return 1
    grep -q '^[[:space:]]*setKey(' "$DNSDIST_CONF" 2>/dev/null || return 1
    return 0
}

DNSDIST_CONSOLE_OUTPUT=""
dnsdist_console() {  # <lua>
    local lua="$1" rc=0
    DNSDIST_CONSOLE_OUTPUT=""
    dnsdist_console_configured || return 2
    DNSDIST_CONSOLE_OUTPUT=$("$DNSDIST_BIN" -c -C "$DNSDIST_CONF" -e "$lua" 2>&1) || rc=$?
    return "$rc"
}

# ── Срез счётчиков для lib/stats.sh ─────────────────────────────────────
# ОДИН вызов консоли (dumpStats): команды не батчатся, а каждый вызов
# стоит сотни миллисекунд — второй команды этот срез не имеет права
# стоить. dumpStats печатает пары «имя значение» по две на строку, поэтому
# разбор — попарный обход токенов, а не позиции и не split по колонкам.
# Точное сравнение имени обязательно: рядом живут rdqueries, empty-queries,
# frontend-servfail и rule-servfail.
#
# Метка старта службы (ActiveEnterTimestampMonotonic) уезжает в срез,
# чтобы lib/stats.py мог отличить перезапуск службы (счётчики законно
# начались с нуля) от необъяснимого отката счётчика.
dnsdist_stats_snapshot() {
    dnsdist_is_running || return 1
    local rc=0
    rc=0; dnsdist_console 'dumpStats()' || rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$DNSDIST_CONSOLE_OUTPUT" ]; then
        return 1
    fi

    local parsed=""
    parsed=$(printf '%s\n' "$DNSDIST_CONSOLE_OUTPUT" | awk '
        {
            for (i = 1; i < NF; i++) {
                if ($(i+1) !~ /^-?[0-9]+(\.[0-9]+)?$/) continue
                if ($i == "queries")            q = $(i+1)
                if ($i == "cache-hits")         h = $(i+1)
                if ($i == "servfail-responses") f = $(i+1)
            }
        }
        END {
            if (q == "") exit 1
            printf "%d %d %d", q, h, f
        }') || parsed=""
    [ -n "$parsed" ] || return 1

    local mark=""
    mark=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$DNSDIST_UNIT" 2>/dev/null) \
        || mark=""
    case "$mark" in ''|*[!0-9]*) mark="-" ;; esac

    printf '%s %s' "$mark" "$parsed"
    return 0
}

# Живая перезагрузка сертификатов. Порядок деградации:
#   1) консоль → reloadAllCertificates() — без обрыва соединений;
#   2) systemctl reload — если юнит объявил ExecReload;
#   3) restart — соединения рвутся, но сертификат подхватывается.
dnsdist_reload_certs() {
    require_root || return 1

    dnsdist_grant_cert_access || true

    if ! dnsdist_is_running; then
        log_warning "$(t eng_not_running_start_instead)"
        if systemctl start "$DNSDIST_UNIT" >/dev/null 2>&1; then
            log_success "$(t eng_certs_reloaded_restart)"
            return 0
        fi
        log_error "$(tf eng_restart_failed "$DNSDIST_UNIT")"
        return 1
    fi

    local rc=0
    dnsdist_console 'reloadAllCertificates()' || rc=$?
    if [ "$rc" -eq 0 ]; then
        log_success "$(t eng_certs_reloaded_live)"
        return 0
    fi
    if [ "$rc" -eq 2 ]; then
        log_dim "$(t eng_console_absent)"
    else
        log_dim "$(tf eng_console_failed "${DNSDIST_CONSOLE_OUTPUT:-—}")"
    fi

    if systemctl reload "$DNSDIST_UNIT" >/dev/null 2>&1; then
        log_success "$(t eng_certs_reloaded_reload)"
        return 0
    fi

    log_warning "$(t eng_certs_restart_needed)"
    if systemctl restart "$DNSDIST_UNIT" >/dev/null 2>&1; then
        log_success "$(t eng_certs_reloaded_restart)"
        return 0
    fi
    log_error "$(tf eng_restart_failed "$DNSDIST_UNIT")"
    return 1
}

dnsdist_is_running() {
    systemctl is-active --quiet "$DNSDIST_UNIT" 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════════════
# ОТЧЁТЫ (stdout — это отчёт для человека, не лог)
# ═══════════════════════════════════════════════════════════════════════
dnsdist_status() {
    print_header "$(t eng_status_title)"

    dnsdist_probe_capabilities >/dev/null 2>&1 || true
    if [ -n "$DNSDIST_VERSION" ]; then
        echo -e "  ${DIM}$(t eng_status_version)${NC} ${WHITE}${DNSDIST_VERSION}${NC}"
        echo -e "  ${DIM}$(t eng_status_features)${NC} ${DIM}${DNSDIST_FEATURES:-—}${NC}"
    else
        echo -e "  ${DIM}$(t eng_status_version)${NC} ${RED}$(t eng_status_absent)${NC}"
    fi

    local active enabled
    active=$(systemctl is-active "$DNSDIST_UNIT" 2>/dev/null) || true
    enabled=$(systemctl is-enabled "$DNSDIST_UNIT" 2>/dev/null) || true
    if [ "$active" = "active" ]; then
        kv_row "$(t eng_status_service)" "${GREEN}${active}${NC} / ${enabled:-?}" 9
    else
        kv_row "$(t eng_status_service)" "${RED}${active:-unknown}${NC} / ${enabled:-?}" 9
    fi

    local doh_port dot_port doh_path policy domain
    domain=$(dnsdist_cfg domain "—")
    doh_port=$(dnsdist_cfg doh_port "8443")
    dot_port=$(dnsdist_cfg dot_port "8853")
    local dot_enabled; dot_enabled=$(dnsdist_cfg dot_enabled "no")
    [ "$dot_enabled" = "yes" ] || dot_enabled="no"
    doh_path=$(dnsdist_cfg doh_path "—")
    policy=$(dnsdist_cfg policy "fastest")
    case "$policy" in
        rr|roundrobin|round-robin|round_robin|circle) policy=$(t eng_policy_rr) ;;
        *) policy=$(t eng_policy_fastest) ;;
    esac

    echo ""
    kv_row "$(t eng_status_domain)" "${WHITE}${domain}${NC}" 9
    # ГРАБЛИ: адрес склеивался здесь вручную и терял слэш — «Состояние»
    # печатало «домен:портпуть». А именно туда идут за адресом, когда
    # что-то не работает, и оттуда копируют в браузер. Склейка — одна
    # на весь продукт, в godns_doh_url.
    # Шесть подписей — шесть разных колонок: отступы были подобраны на
    # глаз под русские слова и разъезжались в обоих языках. kv_row.
    kv_row "$(t eng_status_doh)"    "${WHITE}$(godns_doh_url)${NC}"                9
    # ГРАБЛИ: адрес DoT печатался ЗДЕСЬ безусловно — единственное место без
    # проверки. Панель и сводка её имели, а «Состояние» нет, и оператор,
    # выключивший DoT, видел на этом экране живой адрес несуществующей
    # службы. Хуже того, он бы его кому-нибудь выдал.
    if [ "$(dnsdist_cfg dot_enabled "no")" = "yes" ]; then
        kv_row "$(t eng_status_dot)" "${WHITE}tls://${domain}:${dot_port}${NC}" 9
    else
        kv_row "$(t eng_status_dot)" "${RED}$(t dash_dot_off)${NC}" 9
    fi
    kv_row "$(t eng_status_policy)" "${WHITE}${policy}${NC}"                       9
    kv_row "$(t eng_status_ratelimit)" "$(dnsdist_ratelimit_label)"                9
    kv_row "$(t eng_status_conf)"   "${DIM}${DNSDIST_CONF}${NC}"                   9

    echo ""
    echo -e "  ${DIM}$(t eng_status_listeners)${NC}"
    local seen=0 ln
    while IFS= read -r ln; do
        [ -n "$ln" ] || continue
        echo -e "    ${DIM}${ln}${NC}"
        seen=1
    done < <(ss -Hlntp 2>/dev/null | grep -F 'dnsdist' | awk '{print $4}' | sort -u)
    if [ "$seen" -eq 0 ]; then
        echo -e "    ${DIM}$(t eng_status_no_listeners)${NC}"
    fi

    echo ""
    echo -e "  ${DIM}$(t eng_status_caps)${NC}"
    while IFS= read -r ln; do
        echo -e "    ${DIM}${ln}${NC}"
    done < <(engine_cap_list)

    print_rule
    return 0
}

# «Кто из апстримов что обслужил». Без этого политика — слово на бумаге.
dnsdist_upstream_report() {
    print_header "$(t eng_upreport_title)"

    local rc=0
    dnsdist_console 'showServers()' || rc=$?

    if [ "$rc" -eq 0 ] && [ -n "$DNSDIST_CONSOLE_OUTPUT" ]; then
        # showServers() печатает 15 колонок на ~170 знаков: в терминале 80
        # каждая строка ложится в три, и таблица нечитаема. Пересобираем в
        # узкие колонки живого экрана (имя, адрес, сост., запросы, задержка).
        # Формат не распознали — показываем сырьё: длинно, но честно.
        #
        # Подписи колонок кириллические, а printf в awk и bash считает
        # БАЙТЫ, поэтому заголовок собираем через str_width-паддинг.
        local formatted=""
        formatted=$(printf '%s\n' "$DNSDIST_CONSOLE_OUTPUT" | awk '
            $2 == "Name" && $3 == "Address" { next }
            $1 == "All" {
                printf "  %-20s %-24s %-5s %9s\n", "All", "", "", $3
                next
            }
            $1 ~ /^[0-9]+$/ && NF >= 13 {
                n = $2
                if (length(n) > 20) n = substr(n, 1, 18) ".."
                a = $3
                if (length(a) > 24) a = substr(a, 1, 22) ".."
                lat = $12
                if (lat == "-") lat = $13
                printf "  %-20s %-24s %-5s %9s %12s\n", n, a, $4, $9, lat
            }') || formatted=""
        if [ -n "$formatted" ]; then
            # Ширина колонки задержки — 12, а не 7: подпись «задержка, мс»
            # ровно столько и занимает, и в семь знаков не влезала.
            # Строка целиком: 2 + 21 + 25 + 6 + 10 + 12 = 76 колонок.
            #
            # Числовые колонки в данных прижаты ВПРАВО (%9s, %12s), поэтому
            # и подписи к ним прижимаем вправо: подпись, выровненная влево
            # над правыми числами, стоит не над своей колонкой.
            local hdr="" col
            for col in "$(t mon_sv_name):20" "$(t mon_sv_addr):24" \
                       "$(t mon_sv_state):5" ">$(t mon_sv_queries):9" \
                       ">$(tf eng_upreport_lat_ms "$(t mon_sv_lat)"):12"; do
                local txt="${col%:*}" w="${col##*:}" pad=0 right="no"
                if [ "${txt:0:1}" = ">" ]; then right="yes"; txt="${txt:1}"; fi
                pad=$(( w - $(str_width "$txt") ))
                if [ "$right" = "yes" ]; then
                    while [ "$pad" -gt 0 ]; do hdr="${hdr} "; pad=$((pad - 1)); done
                    hdr="${hdr}${txt}"
                else
                    hdr="${hdr}${txt}"
                    while [ "$pad" -gt 0 ]; do hdr="${hdr} "; pad=$((pad - 1)); done
                fi
                hdr="${hdr} "
            done
            echo -e "  ${DIM}${hdr}${NC}"
            printf '%s\n' "$formatted"
        else
            local ln
            while IFS= read -r ln; do
                echo "  $ln"
            done <<< "$DNSDIST_CONSOLE_OUTPUT"
        fi
    else
        if [ "$rc" -eq 2 ]; then
            echo -e "  ${YELLOW}$(t eng_upreport_no_console)${NC}"
        else
            echo -e "  ${YELLOW}$(t eng_upreport_console_failed)${NC}"
            if [ -n "$DNSDIST_CONSOLE_OUTPUT" ]; then
                echo -e "  ${DIM}${DNSDIST_CONSOLE_OUTPUT}${NC}"
            fi
        fi
        echo -e "  ${DIM}$(t eng_upreport_journal)${NC}"
        local found=0 ln
        while IFS= read -r ln; do
            [ -n "$ln" ] || continue
            echo -e "    ${DIM}${ln}${NC}"
            found=1
        done < <(dnsdist_recent_lines 400 \
                 | grep -Ei 'downstream|marking|health' | tail -n 15)
        if [ "$found" -eq 0 ]; then
            echo -e "    ${DIM}$(t eng_upreport_journal_empty)${NC}"
        fi
        echo -e "  ${DIM}$(t eng_upreport_enable_console)${NC}"
    fi

    # Честная оговорка. Без неё оператор решит, что политика не работает.
    echo ""
    if [ "$(dnsdist_cfg_bool cache yes)" = "yes" ]; then
        echo -e "  ${YELLOW}$(t eng_upreport_cache_caveat_1)${NC}"
        echo -e "  ${DIM}$(t eng_upreport_cache_caveat_2)${NC}"
        echo -e "  ${DIM}$(t eng_upreport_cache_caveat_3)${NC}"
    else
        echo -e "  ${DIM}$(t eng_upreport_nocache)${NC}"
    fi

    print_rule
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ЖИВОЕ НАБЛЮДЕНИЕ
# ═══════════════════════════════════════════════════════════════════════
# ЧТО dnsdist ОТДАЁТ НА САМОМ ДЕЛЕ (проверено прогоном dnsdist 2.1.1)
# ----------------------------------------------------------------------
# Поштучные запросы dnsdist в лог НЕ пишет — но они лежат в его КОЛЬЦЕВЫХ
# БУФЕРАХ, и это ровно то, что нужно оператору. Кольца включены всегда
# (setRingBuffersSize по умолчанию 10000) и не зависят от verbose.
#
#   grepq('', N)   — последние N записей ОБОИХ колец: запросов и ответов.
#                    В строке ответа есть клиент, имя, тип, АДРЕС АПСТРИМА,
#                    ЗАДЕРЖКА в мс и rcode. Именно отсюда берётся вся лента.
#   showServers()  — по каждому апстриму: состояние, счётчик запросов,
#                    средняя задержка, сколько запросов «в воздухе».
#   dumpStats()    — счётчики: queries, responses, cache-hits/misses,
#                    downstream-timeouts, servfail-responses.
#
# ТРИ ГРАБЛИ, НА КОТОРЫХ ЭТО ЛОМАЕТСЯ, И ВСЕ ТРИ ПРОВЕРЕНЫ ЖИВЬЁМ
# ----------------------------------------------------------------------
# 1. Селектор grepq — ПУСТАЯ СТРОКА. Очевидное `grepq({'0.0.0.0/0','::/0'})`
#    печатает заголовок и НИ ОДНОЙ строки: сетевая маска в селекторе
#    отбирает не то, что кажется. Замерено: `grepq('', 8)` — 8 записей,
#    `grepq({'0.0.0.0/0','::/0'}, 8)` — ноль.
# 2. Команды НЕ БАТЧАТСЯ. `-e 'showVersion();showSecurityStatus()'` вернул
#    вывод только ПОСЛЕДНЕЙ: буфер вывода перезаписывается, а не копится.
#    Поэтому три вызова консоли на кадр, а не один. Замер: ~30 мс на вызов.
# 3. У goDNS ВСЕ апстримы DoT/DoH, то есть TCP, а колонка `Lat` в
#    showServers считает только UDP-ответы и всегда показывает «-».
#    Число живёт в колонке `TCP`. Разбор учитывает это (lib/monitor.py).
#
# ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ
# ----------------------------------------------------------------------
# Отдельного «времени апстрима» dnsdist не записывает. Задержка в кольце —
# это время от прихода запроса клиента до прихода ответа апстрима, то есть
# НАШЕ время ответа, в котором апстрим — почти всё. Разделить «сколько
# думали мы» и «сколько думал апстрим» поштучно нельзя; зато видно главное:
# ответ из кэша (апстрим не опрашивали, ~0 мс) против похода наверх.

dnsdist_mon_width() {
    local w=""
    w=$(tput cols 2>/dev/null) || w=""
    case "$w" in ''|*[!0-9]*) w=80 ;; esac
    if [ "$w" -lt 60 ]; then w=60; fi
    if [ "$w" -gt 200 ]; then w=200; fi
    printf '%s' "$w"
    return 0
}

# Сколько строк ленты влезет. Шапка, счётчики, таблица апстримов и подвал
# съедают постоянные ~16 строк плюс по строке на апстрим.
dnsdist_mon_rows() {  # <число апстримов>
    local nup="${1:-3}" h="" r=0
    h=$(tput lines 2>/dev/null) || h=""
    case "$h" in ''|*[!0-9]*) h=24 ;; esac
    case "$nup" in ''|*[!0-9]*) nup=3 ;; esac
    r=$(( h - 16 - nup ))
    if [ "$r" -lt 4 ]; then r=4; fi
    if [ "$r" -gt 40 ]; then r=40; fi
    printf '%s' "$r"
    return 0
}

dnsdist_mon_upstream_count() {
    local parsed="" n=0
    parsed=$(dnsdist_upstreams_parse 2>/dev/null) || parsed=""
    if [ -n "$parsed" ]; then
        n=$(printf '%s\n' "$parsed" | grep -c '[^[:space:]]') || n=0
    fi
    case "$n" in ''|*[!0-9]*) n=0 ;; esac
    printf '%s' "$n"
    return 0
}

# Подписи экрана уезжают в python3 отдельной секцией: так i18n остаётся в
# bash (единственный источник строк), а вёрстка — в python3, где ширина
# считается в символах, а не в байтах.
dnsdist_mon_labels() {  # <интервал>
    local iv="${1:-2}"
    # ГРАБЛИ: цвета в lib/common.sh — это ЛИТЕРАЛЬНЫЕ строки '\033[2m', а не
    # управляющие последовательности: весь проект печатает их через `echo -e`,
    # который разворачивает escape'ы сам. С `printf '%s'` в python3 уезжает
    # текст «\033[2m», он честно печатается на экране, и вся вёрстка
    # рассыпается — колонки считаются по видимым символам, а их вчетверо
    # больше ожидаемого. Разворачиваем ЗДЕСЬ, через `%b`, и только для цветов:
    # для подписей %b развернул бы обратный слэш внутри перевода.
    printf 'c_dim=%b\n'    "$DIM"
    printf 'c_nc=%b\n'     "$NC"
    printf 'c_white=%b\n'  "$WHITE"
    printf 'c_green=%b\n'  "$GREEN"
    printf 'c_red=%b\n'    "$RED"
    printf 'c_yellow=%b\n' "$YELLOW"
    printf 'c_cyan=%b\n'   "$CYAN"
    printf 'c_bold=%b\n'   "$BOLD"
    printf 'title=%s\n'        "$(t mon_title)"
    printf 'hint=%s\n'         "$(tf mon_hint "$iv")"
    printf 'st_queries=%s\n'   "$(t mon_st_queries)"
    printf 'st_responses=%s\n' "$(t mon_st_responses)"
    printf 'st_cache=%s\n'     "$(t mon_st_cache)"
    printf 'st_timeouts=%s\n'  "$(t mon_st_timeouts)"
    printf 'st_servfail=%s\n'  "$(t mon_st_servfail)"
    printf 'st_nxdomain=%s\n'  "$(t mon_st_nxdomain)"
    printf 'sec_upstreams=%s\n' "$(t mon_sec_upstreams)"
    printf 'sec_feed=%s\n'     "$(t mon_sec_feed)"
    printf 'sv_name=%s\n'      "$(t mon_sv_name)"
    printf 'sv_addr=%s\n'      "$(t mon_sv_addr)"
    printf 'sv_state=%s\n'     "$(t mon_sv_state)"
    printf 'sv_queries=%s\n'   "$(t mon_sv_queries)"
    printf 'sv_lat=%s\n'       "$(t mon_sv_lat)"
    printf 'sv_out=%s\n'       "$(t mon_sv_out)"
    printf 'q_time=%s\n'       "$(t mon_q_time)"
    printf 'q_client=%s\n'     "$(t mon_q_client)"
    printf 'q_proto=%s\n'      "$(t mon_q_proto)"
    printf 'q_name=%s\n'       "$(t mon_q_name)"
    printf 'q_type=%s\n'       "$(t mon_q_type)"
    printf 'q_up=%s\n'         "$(t mon_q_up)"
    printf 'q_ms=%s\n'         "$(t mon_q_ms)"
    printf 'q_rcode=%s\n'      "$(t mon_q_rcode)"
    printf 'cache=%s\n'        "$(t mon_cache_word)"
    printf 'noanswer=%s\n'     "$(t mon_noanswer)"
    printf 'inflight=%s\n'     "$(t mon_inflight)"
    printf 'ms=%s\n'           "$(t mon_ms)"
    printf 'feed_empty=%s\n'   "$(t mon_feed_empty)"
    printf 'no_servers=%s\n'   "$(t mon_no_servers)"
    printf 'note=%s\n'         "$(t mon_note)"
    printf 'note2=%s\n'        "$(t mon_note2)"
    printf 'note3=%s\n'        "$(t mon_note3)"
    return 0
}

# Один кадр экрана в stdout. Ошибки консоли не валят кадр: пустая секция
# нарисуется как «нет данных», а не как оборванный экран.
dnsdist_mon_frame() {  # <ширина> <строк ленты> <интервал>
    local w="${1:-78}" rows="${2:-10}" iv="${3:-2}"
    local servers="" stats="" feed="" want=0 rc=0

    rc=0; dnsdist_console 'showServers()' || rc=$?
    if [ "$rc" -eq 0 ]; then servers="$DNSDIST_CONSOLE_OUTPUT"; fi

    rc=0; dnsdist_console 'dumpStats()' || rc=$?
    if [ "$rc" -eq 0 ]; then stats="$DNSDIST_CONSOLE_OUTPUT"; fi

    # grepq(sel, N) отдаёт до N записей КАЖДОГО кольца, а запрос и ответ
    # склеиваются в одну строку — просим с запасом.
    want=$(( rows + 6 ))
    if [ "$want" -gt 200 ]; then want=200; fi
    rc=0; dnsdist_console "grepq('', ${want})" || rc=$?
    if [ "$rc" -eq 0 ]; then feed="$DNSDIST_CONSOLE_OUTPUT"; fi

    {
        printf '@@GODNS labels\n'
        dnsdist_mon_labels "$iv"
        printf '@@GODNS servers\n%s\n' "$servers"
        printf '@@GODNS stats\n%s\n'   "$stats"
        printf '@@GODNS feed\n%s\n'    "$feed"
    } | python3 "$DNSDIST_MON_PY" --width "$w" --rows "$rows" || return 1
    return 0
}

dnsdist_mon_restore_tty() {
    printf '\033[?25h\033[?1049l'
    return 0
}

# Готовность к наблюдению: движок, консоль, служба, python3, помощник.
# Проверяем ДО альтернативного экрана — сообщение, напечатанное поверх
# него, исчезнет вместе с ним, и оператор увидит пустой терминал.
dnsdist_mon_preflight() {
    local rc=0
    if ! dnsdist_is_installed; then
        log_error "$(t mon_no_engine)"
        return 1
    fi
    if ! dnsdist_is_running; then
        log_warning "$(t mon_not_running)"
        return 1
    fi
    if ! dnsdist_console_configured; then
        log_error "$(t mon_no_console)"
        log_dim   "$(t mon_no_console_fix)"
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "$(t mon_no_python)"
        return 1
    fi
    if [ ! -r "$DNSDIST_MON_PY" ]; then
        log_error "$(tf mon_no_helper "$DNSDIST_MON_PY")"
        return 1
    fi
    dnsdist_console 'showVersion()' || rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "$(t mon_console_failed)"
        dnsdist_dump_lines "${DNSDIST_CONSOLE_OUTPUT:-}" 6
        return 1
    fi
    return 0
}

DNSDIST_MON_STOP=0

dnsdist_live_monitor() {
    local iv="" nup=0 w=0 rows=0 tin="" key="" rc=0

    dnsdist_mon_preflight || return 1

    iv=$(dnsdist_cfg monitor_interval "2")
    case "$iv" in ''|*[!0-9]*) iv=2 ;; esac
    if [ "$iv" -lt 1 ];  then iv=1;  fi
    if [ "$iv" -gt 30 ]; then iv=30; fi

    nup=$(dnsdist_mon_upstream_count)

    # Не терминал (запуск из скрипта, вывод в файл) — один снимок и выход.
    # Иначе `read -t` получает EOF мгновенно и цикл крутится вхолостую:
    # ровно та ошибка, которую в goTelegram ловили на экране статистики.
    if [ ! -t 1 ] || [ ! -t 0 ]; then
        dnsdist_mon_frame 78 12 "$iv" || return 1
        return 0
    fi

    tin=/dev/tty
    [ -r "$tin" ] || tin=/dev/stdin

    # ВАЖНО: ловушку EXIT не трогаем — на ней висит cleanup_temp_files из
    # godns.sh, и подмена оставила бы временные файлы. Ctrl-C ловим сами и
    # выходим в меню, а не убиваем скрипт: это ещё и лучше по UX.
    DNSDIST_MON_STOP=0
    trap 'DNSDIST_MON_STOP=1' INT
    printf '\033[?1049h\033[?25l'

    while true; do
        w=$(dnsdist_mon_width)
        rows=$(dnsdist_mon_rows "$nup")
        printf '\033[H'
        dnsdist_mon_frame "$w" "$rows" "$iv" || true
        printf '\033[J'

        key=""
        rc=0
        read -t "$iv" -r -n 1 key < "$tin" 2>/dev/null || rc=$?
        if [ "$DNSDIST_MON_STOP" = "1" ]; then break; fi
        # rc > 128 — истёк таймаут, рисуем следующий кадр. Всё остальное
        # (нажатая клавиша, EOF, ошибка чтения) — выход.
        if [ "$rc" -gt 128 ]; then continue; fi
        break
    done

    trap - INT
    dnsdist_mon_restore_tty
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ПОДРОБНЫЙ ЛОГ ЗАПРОСОВ
# ═══════════════════════════════════════════════════════════════════════
# Живой экран выше подробного лога НЕ ТРЕБУЕТ: кольца работают всегда.
# Этот режим — для разбора: он кладёт в лог по две строки НА КАЖДЫЙ запрос
# («Packet cache miss» и «Got answer from backend» с backend.name,
# dns.response.latency_us и dns.response.rcode — формат dnsdist 2.x
# структурированный, key="value»).
#
# Поэтому он опасен ровно так же, как болтливый telemt в goTelegram: при
# 50 запросах в секунду это около 200 МБ в час, и забитый /var/log — это
# не «неаккуратно», а сервер, на который уже не зайти по SSH. Отсюда три
# обязательных свойства, и все три реализованы ниже:
#   • выключается тем же пунктом меню (переключатель);
#   • объём предъявляется ДО включения, посчитанный по текущему qps;
#   • сам выключается по таймеру, чтобы забытый режим не съел диск.
#
# Ничего в конфиг не пишем: setVerbose живёт только до перезапуска службы.
# Это тоже защита — рестарт гасит забытый режим.

dnsdist_query_log_state() {
    local rc=0
    dnsdist_console 'tostring(getVerbose())' || rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'unknown'
        return 1
    fi
    case "$DNSDIST_CONSOLE_OUTPUT" in
        *true*)  printf 'on';  return 0 ;;
        *false*) printf 'off'; return 0 ;;
    esac
    printf 'unknown'
    return 1
}

# Оценка потока: печатает «<запросов в секунду> <МБ в час>».
#
# Показываем ОБА числа, а не одни мегабайты. Причина: на простаивающем
# резолвере qps = 0, «примерно 0 МБ в час» читается как «бесплатно», и
# оператор включает режим перед наплывом трафика. Пара «сейчас столько
# запросов — будет столько мегабайт» такого прочтения не допускает.
#
# Ничего не печатаем, если qps не прочитался: выдуманное число здесь хуже
# отсутствующего.
dnsdist_query_log_volume() {
    local qps="" rc=0
    dnsdist_console 'showServers()' || rc=$?
    if [ "$rc" -ne 0 ]; then return 1; fi
    qps=$(printf '%s\n' "$DNSDIST_CONSOLE_OUTPUT" | awk '$1=="All"{print $2; exit}') || qps=""
    case "$qps" in ''|*[!0-9.]*) return 1 ;; esac
    # Две строки на запрос, ~550 байт каждая — замерено на dnsdist 2.1.1:
    # «Packet cache miss» и «Got answer from backend» в структурированном
    # формате key="value" со всеми полями запроса и ответа.
    awk -v q="$qps" 'BEGIN{ printf "%s %.0f", q, q * 2 * 550 * 3600 / 1000000 }'
    return 0
}

# Транзитный таймер, который выключит режим сам.
# СЕКРЕТ НЕ УХОДИТ В АРГУМЕНТЫ: ключ консоли dnsdist читает из своего
# конфига, в командной строке его нет — /proc/<pid>/cmdline безопасен.
dnsdist_query_log_arm_timeout() {  # <минут>
    local mins="${1:-15}"
    command -v systemd-run >/dev/null 2>&1 || return 1
    dnsdist_query_log_disarm_timeout
    systemd-run --quiet --collect \
        --unit="$DNSDIST_QLOG_UNIT" \
        --on-active="${mins}min" \
        --description="goDNS Pro: auto-off verbose query log" \
        "$DNSDIST_BIN" -c -C "$DNSDIST_CONF" -e 'setVerbose(false)' \
        >/dev/null 2>&1 || return 1
    return 0
}

dnsdist_query_log_disarm_timeout() {
    systemctl stop "${DNSDIST_QLOG_UNIT}.timer"   >/dev/null 2>&1 || true
    systemctl stop "${DNSDIST_QLOG_UNIT}.service" >/dev/null 2>&1 || true
    systemctl reset-failed "${DNSDIST_QLOG_UNIT}.service" >/dev/null 2>&1 || true
    return 0
}

# Куда смотреть после включения: свой файл, если вывод службы уведён туда
# (это делает drop-in), иначе журнал systemd.
dnsdist_query_log_where() {
    if [ -r "$DNSDIST_LOG" ]; then
        printf 'tail -f %s' "$DNSDIST_LOG"
    else
        printf 'journalctl -u %s -f' "$DNSDIST_UNIT"
    fi
    return 0
}

dnsdist_query_log_enable() {
    local rc=0 mb="" state="" mins="$DNSDIST_QLOG_MINUTES"

    if ! dnsdist_is_running; then
        log_warning "$(t mon_not_running)"
        return 1
    fi
    if ! dnsdist_console_configured; then
        log_error "$(t mon_no_console)"
        log_dim   "$(t mon_no_console_fix)"
        return 1
    fi

    state=$(dnsdist_query_log_state) || true
    if [ "$state" = "on" ]; then
        log_info "$(t qlog_already_on)"
    fi

    # Объём — ДО включения и числом, а не общими словами.
    mb=$(dnsdist_query_log_volume) || mb=""
    if [ -n "$mb" ]; then
        # shellcheck disable=SC2086
        log_warning "$(tf qlog_warn_volume $mb)"
    else
        log_warning "$(t qlog_warn_volume_unknown)"
    fi

    # Оценку объёма показали — теперь спрашиваем. Раньше пункт включал
    # подробный лог сразу: человек, зашедший прочитать «сколько это будет»,
    # уже получал ~200 МБ в час на диск. Умолчание «нет».
    if ! confirm "$(t qlog_confirm)" n; then
        log_info "$(t startup_cancelled)"
        return 0
    fi

    rc=0
    dnsdist_console 'setVerbose(true)' || rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "$(t qlog_failed)"
        dnsdist_dump_lines "${DNSDIST_CONSOLE_OUTPUT:-}" 6
        return 1
    fi

    if dnsdist_query_log_arm_timeout "$mins"; then
        log_success "$(tf qlog_enabled "$mins")"
    else
        log_success "$(t qlog_enabled_no_timer)"
        log_warning "$(t qlog_autooff_failed)"
    fi
    log_dim "$(t qlog_survives_restart)"
    log_dim "$(tf qlog_where "$(dnsdist_query_log_where)")"
    log_dim "$(t qlog_what)"
    return 0
}

dnsdist_query_log_disable() {
    local rc=0

    dnsdist_query_log_disarm_timeout

    if ! dnsdist_is_running; then
        log_warning "$(t mon_not_running)"
        return 1
    fi
    if ! dnsdist_console_configured; then
        log_error "$(t mon_no_console)"
        return 1
    fi

    dnsdist_console 'setVerbose(false)' || rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "$(t qlog_failed)"
        dnsdist_dump_lines "${DNSDIST_CONSOLE_OUTPUT:-}" 6
        return 1
    fi
    log_success "$(t qlog_disabled)"
    return 0
}

# Хвост лога в реальном времени. Источник тот же, что у dnsdist_logs:
# свой файл, если вывод службы уведён туда, иначе журнал systemd.
dnsdist_logs_follow() {
    log_dim "$(t logs_follow_hint)"
    if [ -r "$DNSDIST_LOG" ] && [ -s "$DNSDIST_LOG" ]; then
        log_dim "$DNSDIST_LOG"
        # ГРАБЛИ: `|| true` снимал только КОД возврата tail, а сигнал
        # прилетает всей группе процессов переднего плана — включая сам
        # godns.sh. Экран честно писал «Ctrl-C — вернуться в меню», а
        # Ctrl-C закрывал программу целиком: оператор терял меню и,
        # по ssh, соединение. Ловушка на INT — как в живом экране.
        # EXIT не трогаем: на нём висит cleanup_temp_files из godns.sh.
        trap ':' INT
        tail -n 30 -f "$DNSDIST_LOG" || true
        trap - INT
        echo ""
        return 0
    fi
    if ! command -v journalctl >/dev/null 2>&1; then
        log_error "$(t logs_follow_no_source)"
        return 1
    fi
    trap ':' INT
    journalctl -u "$DNSDIST_UNIT" -n 30 -f --no-pager || true
    trap - INT
    echo ""
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ТОЧКИ, КОТОРЫЕ ДЁРГАЕТ ДИСПЕТЧЕР
# ═══════════════════════════════════════════════════════════════════════
dnsdist_is_installed() {
    command -v "$DNSDIST_BIN" >/dev/null 2>&1 && return 0
    [ -x "/usr/bin/${DNSDIST_BIN}" ] && return 0
    return 1
}

# Свой файл — основной источник, журнал systemd — запасной.
#
# Оба нужны, и это не перестраховка: собственный вывод dnsdist уходит в файл,
# а сообщения самого systemd («Started dnsdist», «Failed with result») пишет
# PID 1, и они остаются в журнале. Когда служба не поднялась, файла может не
# быть вовсе, а причина — как раз в журнале.
dnsdist_logs() {
    local n="${1:-40}" text=""
    if [ -r "$DNSDIST_LOG" ] && [ -s "$DNSDIST_LOG" ]; then
        log_dim "$DNSDIST_LOG"
        text=$(tail -n "$n" "$DNSDIST_LOG" 2>/dev/null) || text=""
        dnsdist_dump_lines "$text" "$n"
        return 0
    fi
    dnsdist_dump_journal "$n"
    return 0
}

dnsdist_show_config() {
    if [ ! -r "$DNSDIST_CONF" ]; then
        log_warning "$(tf eng_conf_absent "$DNSDIST_CONF")"
        return 0
    fi
    log_dim "$DNSDIST_CONF"
    # Ключ консоли на экран не выводим. Панель и демон бота его уже чистят
    # (_scrub_console_key), а SSH печатал конфиг как есть — то есть самый
    # прямой путь к чужому dnsdist показывался любому, кто заглянул через
    # плечо, попал в запись терминала или в скриншот для поддержки.
    # Показать сам ключ есть кому: он лежит в конфиге, читаемом root.
    # Две подстановки без обратной ссылки: `\1` в ERE — расширение GNU,
    # BSD sed на macOS его молча не применяет, и «чистка» ничего не чистила.
    dnsdist_dump_lines "$(sed -E \
        -e 's/setKey\("[^"]*"\)/setKey("***")/' \
        -e "s/setKey\('[^']*'\)/setKey('***')/" \
        "$DNSDIST_CONF" 2>/dev/null)" 400
    return 0
}

# Снимаем ТОЛЬКО то, что положили сами.
#
# Пакет dnsdist оставляем: его мог поставить не goDNS, а оператор или другой
# инструмент, и удалять чужой пакет по «удалить goDNS» — ровно тот сорт
# самодеятельности, из-за которого goVLESS сносит /var/www/html целиком.
# Сертификат не трогаем никогда: он принадлежит соседу.
dnsdist_uninstall() {
    log_info "$(t eng_uninstall_start)"

    systemctl disable --now "$DNSDIST_UNIT" >/dev/null 2>&1 || true

    # Транзитный таймер автовыключения подробного лога мог остаться взведённым.
    dnsdist_query_log_disarm_timeout

    if [ -f "$DNSDIST_DROPIN" ]; then
        rm -f "$DNSDIST_DROPIN" 2>/dev/null || true
        rmdir "$DNSDIST_DROPIN_DIR" 2>/dev/null || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        log_dim "$(tf eng_uninstall_removed "$DNSDIST_DROPIN")"
    fi

    # Конфиг dnsdist переписывали мы — но оставляем копию, чтобы оператор
    # мог посмотреть, что там было, если удаление оказалось ошибкой.
    if [ -f "$DNSDIST_CONF" ]; then
        mv -f "$DNSDIST_CONF" "${DNSDIST_CONF}.godns-removed" 2>/dev/null \
            && log_dim "$(tf eng_uninstall_kept "${DNSDIST_CONF}.godns-removed")"
    fi
    # Рабочая копия предыдущего конфига (её кладёт engine_apply перед каждой
    # заменой) и справочная копия из восстановления архива — тоже наши.
    # Молча оставленный файл после «удаления» читается как «не доудалилось»;
    # оператору хватает .godns-removed.
    rm -f "${DNSDIST_CONF}.godns.bak" "${DNSDIST_CONF}.from-backup" 2>/dev/null || true

    # Репозиторий PowerDNS добавляли мы — убираем, если ставили сами.
    local f
    for f in "$DNSDIST_APT_LIST" "$DNSDIST_APT_PIN" "$DNSDIST_APT_KEYRING"; do
        if [ -f "$f" ]; then
            rm -f "$f" 2>/dev/null && log_dim "$(tf eng_uninstall_removed "$f")"
        fi
    done

    log_dim "$(t eng_uninstall_pkg_kept)"
    return 0
}

# Сброс пакетного кэша через консоль.
#
# Нужен проверкам: с живым кэшем запросы 2..N до апстрима не доходят, и
# тест наблюдает не резолвер, а собственный кэш. На этом ровно и погорела
# проверка DNSSEC — один ответ непроверяющего апстрима осел в кэше и
# раздавался весь TTL, из-за чего исправный резолвер выглядел дырявым.
dnsdist_cache_flush() {
    dnsdist_console_configured || return 1
    dnsdist_console 'getPool(""):getCache():expunge(0)' >/dev/null 2>&1 || return 1
    return 0
}

# Порт держит наш же процесс? Сверяем и по PID, и по имени: PID пуст, пока
# служба остановлена, а имя остаётся верным признаком в обоих случаях.
dnsdist_port_is_ours() {
    local owner="${1:-}" ourpid="${2:-}" pid=""
    [ -n "$owner" ] || return 1
    case "$owner" in
        *"$DNSDIST_BIN"*|*dnsdist*) : ;;
        *) return 1 ;;
    esac
    if [ -n "$ourpid" ] && [ "$ourpid" != "0" ]; then
        pid=$(printf '%s' "$owner" | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p')
        if [ -n "$pid" ] && [ "$pid" != "$ourpid" ]; then return 1; fi
    fi
    return 0
}
