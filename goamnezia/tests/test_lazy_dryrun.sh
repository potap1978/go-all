#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — «Ленивый Pro» и линия на UDP 443 (lib/modes.sh, docs/MODES_047.md с «Уточнениями» 28.09) без
# Docker и без сети (GOAMNEZIA_DRY_RUN=1), через НАСТОЯЩИЙ install.sh: мост lazy-check / lazy-setup / pro-line —
# процессом на шаг, как у бота и панели, — и запуск без меню `install.sh --lazy --domain=… [--keys=N]` (goInstaller).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_lazy_dryrun.sh
# Нужны: bash ≥ 5, jq, python3, openssl.
#
# Сценарии — каждый на своём чистом стенде (свой GOAMNEZIA_DIR, nginx, сайт, letsencrypt, systemd):
#   A. чистый сервер: lazy-check — поля договора; lazy-setup — Pro, 2.0 на UDP 443 и 3.1 на случайном, сайт, панель
#      на домене, ключи со случайными именами и страницей каждому, поля договора; повтор на установленном — ничего не
#      ломает (те же ключи, страницы, порты); pro-line — ответ договора, 3.1 на 443, 2.0 на случайный, повтор, обратно;
#      вне Pro — mode_not_pro; --lazy на установленном — «уже установлен», rc 0, состояние байт в байт;
#   B. punycode: кириллица, https://, путь и точка в конце — domain в xn--, domain_display кириллицей, сайт и
#      страницы — на punycode;
#   C. домен смотрит не сюда (и A-записи нет вовсе): blocked с причиной, lazy-setup — dns_not_here до любых
#      изменений; --lazy — повтор DNS и отмена без терминала, ничего не установлено;
#   D. Cloudflare: не отказ, ключи — на IP сервера (endpoint_host), warnings — обойдённая причина;
#   E. UDP 443 занят чужим: semi_pro → Semi-Pro, профили на случайных портах; порт освободился — повтор на
#      установленном: обе линии на своих портах, их не двигаем (no_line_for_443), ключей не прибавилось;
#   F. TCP 443 занят чужим nginx: blocked tcp443_busy с владельцем, lazy-setup — port_busy, ничего не поставлено;
#   G. TCP 443 держит контейнер приложения Amnezia (Xray): blocked tcp443_app, контейнеры не тронуты;
#   H. сервер от приложения Amnezia: подхват без вопроса, порт 2.0 не тронут (ACC-2), недостающая 3.1 — на UDP 443,
#      новые ключи к людям приложения, 1.x (wg0) им не выдан; pro-line без согласия — adopted_keyless с profile;
#      контейнер приложения 2.0 сам стоит на UDP 443 — он там и останется (udp443_line = 2.0);
#   I. ни один случайный шаблон не встал — встроенная заглушка (template_fallback);
#   J. Let's Encrypt отказал: на чистом — запасной самоподписанный (cert_fallback, page = null, panel_off); на
#      установленном с живым сайтом — отказ, прежний домен и сайт на месте (модуль как библиотека, отказ подставлен);
#   K. install.sh --lazy без терминала на чистом: сводка, пароль панели один раз, бот — «позже»; повтор — no-op;
#   L. (LZ-02) сервер приложения, где amnezia-awg2 — это 3.1: линия по версии, а не по месту — на 443 встаёт
#      недостающая 2.0 (на месте awg3), второй 3.1 нет, сводка говорит правду;
#   M. (SUBNET-01) старый amnezia-awg и amnezia-awg2 приложения на одной 10.8.1.0/24 (старый в docker ps первым):
#      оба подхвачены как есть (раньше — adopt_failed всего сценария), 3.1 — на своей подсети, warnings subnet_overlap;
#   LZ-03 — в D: за прокси Cloudflare его IPv6 в AAAA не «чужие», message говорит «ключи на IP»; LZ-05 — в A: ports.*.ours
#      («занят нами») и keys_have / keys_new.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-lazy.XXXXXX") || exit 2
DONE=""
OUT="$TMP/stdout.log"
ERR="$TMP/stderr.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_EDGE_IPS_STUB=""
unset DOCKER_HOST DOCKER_CONTEXT GOAMNEZIA_LE_STAGING
# systemctl машины прогону не нужен: подставной отвечает отказом, как машина без systemd (см. test_actions_dryrun.sh).
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nexit 1\n' >"$HOSTBIN/systemctl"
chmod +x "$HOSTBIN/systemctl"
export PATH="$HOSTBIN:$PATH"
FX="$ROOT/tests/fixtures"

pass=0; fail=0; rc=0; last=""; nlines=0
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
jget() { jq -r "$1" <<<"$last" 2>/dev/null; }
errs() { tail -n 8 "$ERR" 2>/dev/null; }
cfgv() { jq -r "$1" "$GOAMNEZIA_DIR/config.json" 2>/dev/null; }
# nprof — сколько профилей в config.json (файла нет — ноль: чистый сервер так и выглядит)
nprof() { jq -r '.profiles // {} | length' "$GOAMNEZIA_DIR/config.json" 2>/dev/null || echo 0; }
# Состояние стенда одной строкой — для «повтор ничего не поменял»
sig() { cat "$GOAMNEZIA_DIR/config.json" "$GOAMNEZIA_DIR/users.json" 2>/dev/null | cksum; }
npages() { find "$GOAMNEZIA_DIR/pages" -mindepth 1 -maxdepth 1 -type d -name 'p-*' 2>/dev/null | wc -l | tr -d ' '; }

# stand <имя> — чистый сервер: всё продукта — во временном каталоге, фикстуры прежних сценариев сняты. Каталоги бота,
# панели и MCP не заводим: их появление значило бы «компонент уже стоит» (auto_update_components скопировал бы его).
stand() {
    local s="$TMP/$1"
    mkdir -p "$s"/{goamnezia,run,nginx,site,acme,letsencrypt,systemd,shm,build,upstream}
    cp -R "$ROOT/lib/upstream/." "$s/upstream/"
    export GOAMNEZIA_DIR="$s/goamnezia" GOAMNEZIA_LOCK_DIR="$s/run" GOAMNEZIA_LOG_FILE="$s/goamnezia.log"
    export GOAMNEZIA_AUDIT_LOG="$s/audit.log" GOAMNEZIA_NOTIFY_DIR="$s/run/notify" GOAMNEZIA_BUILD_ROOT="$s/build"
    export UPSTREAM_DIR="$s/upstream" GOAMNEZIA_DOCKER_SOCK="$s/nosuch.sock" GOAMNEZIA_SECRET_TMPDIR="$s/shm"
    export BOT_DIR="$s/bot" ADMIN_DIR="$s/admin" MCP_DIR="$s/mcp"
    export NGINX_SITE_CONF="$s/nginx/sites-available/goamnezia" NGINX_SITE_LINK="$s/nginx/sites-enabled/goamnezia"
    export GOAMNEZIA_NGINX_DIR="$s/nginx" GOAMNEZIA_SITE_ROOT="$s/site" GOAMNEZIA_ACME_ROOT="$s/acme"
    export GOAMNEZIA_LE_DIR="$s/letsencrypt" GOAMNEZIA_SYSTEMD_DIR="$s/systemd"
    export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$FX/site_min"
    export GOAMNEZIA_DNS_STUB="203.0.113.10"
    export GOAMNEZIA_LAZY_DNS_INTERVAL=1 GOAMNEZIA_LAZY_DNS_TIMEOUT=2
    unset GOAMNEZIA_DNS_STUB_AAAA GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_SS_FIXTURE_UDP GOAMNEZIA_SS_FIXTURE
    unset GOAMNEZIA_DOCKER_PS_FIXTURE GOAMNEZIA_CONTAINER_FS_DIR
    printf 'ru\n' >"$GOAMNEZIA_DIR/.language"
}

# act <действие> [--k=v …] — новый процесс install.sh, как у бота; last — последняя строка stdout.
act() {
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR" </dev/null; rc=$?
    last=$(tail -n 1 "$OUT")
    nlines=$(wc -l <"$OUT" | tr -d ' ')
    return 0
}
expect() {
    local what="$1" status="$2" code="${3:-}"
    if [ "$nlines" = 1 ] && is_json "$last" && [ "$(jget .status)" = "$status" ] \
       && { [ -z "$code" ] || [ "$(jget .code)" = "$code" ]; }; then
        ok "$what"
    else
        bad "$what" "rc=$rc lines=$nlines last=${last:0:400} | $(errs | tr '\n' ' ' | head -c 400)"
    fi
}
# Поля ответа сверх status/message — ровно договорные (docs/MODES_047.md).
LC_FIELDS='["a","aaaa","app_containers","cloudflare_proxied","domain","domain_display","keys_have","keys_new","plan","points_here","ports","reasons","server_ips","udp443_line"]'
LS_FIELDS='["domain","keys","mode","panel_on_domain","profiles","site","udp443","warnings"]'
PL_FIELDS='["mode","profiles","udp443"]'
fields_are() { jq -e --argjson f "$1" '(keys - ["status", "message"]) == ($f | sort)' <<<"$last" >/dev/null 2>&1; }
has_reason() { jq -e --arg r "$1" '.reasons | index($r) != null' <<<"$last" >/dev/null 2>&1; }
has_warning() { jq -e --arg r "$1" '.warnings | index($r) != null' <<<"$last" >/dev/null 2>&1; }
keys_sorted() { jget '[.keys[].name] | sort | join(",")'; }

# lazy <аргументы> — install.sh --lazy без терминала: stdin пуст, конец ввода — отмена.
lazy() {
    "$BASH5" "$INSTALL" --lazy "$@" >"$OUT" 2>&1 </dev/null; rc=$?
    return 0
}
flat() { tr -s '[:space:]' ' ' <"$OUT"; }

# lib_run <сценарий bash> — модуль как библиотека: install.sh без последней строки (main "$@"), подставленные функции.
LIBDIR="$TMP/lib"; mkdir -p "$LIBDIR"
ln -sfn "$ROOT/lib" "$LIBDIR/lib"
sed '$d' "$INSTALL" >"$LIBDIR/install.sh"
lib_run() {
    "$BASH5" -c 'source "$1" >/dev/null 2>&1 || exit 9
        eval "$2"' _ "$LIBDIR/install.sh" "$1" >"$OUT" 2>"$ERR" </dev/null; rc=$?
    last=$(tail -n 1 "$OUT")
    return 0
}

# ── окружение ────────────────────────────────────────────────────────────────
section "окружение"
printf '  bash: %s (%s)\n' "$BASH5" "$("$BASH5" -c 'echo $BASH_VERSION')"
check "lib/modes.sh на месте и подключается install.sh" '[ -f "$ROOT/lib/modes.sh" ] && grep -q "modes.sh" "$INSTALL"'

# ══ A. Чистый сервер ═════════════════════════════════════════════════════════
section "A. чистый сервер: lazy-check → lazy-setup → повтор → pro-line"
stand A
act lazy-check --domain=example.com
expect "lazy-check: success, одна JSON-строка" success
check "lazy-check: ровно поля договора" 'fields_are "$LC_FIELDS"' "$last"
check "lazy-check: plan=pro, points_here, a и server_ips — адрес сервера, без Cloudflare, причин нет" \
    '[ "$(jget .plan)" = pro ] && [ "$(jget .points_here)" = true ] && [ "$(jget ".a|join(\",\")")" = 203.0.113.10 ] && [ "$(jget ".server_ips|join(\",\")")" = 203.0.113.10 ] && [ "$(jget .cloudflare_proxied)" = false ] && [ "$(jget ".reasons|length")" = 0 ] && [ "$(jget ".app_containers|length")" = 0 ]' "$last"
check "lazy-check: ports — tcp80, tcp443, udp443 ровно {free, by, ours}, все свободны, by у свободного — null, не наши" \
    'jq -e ".ports | (keys == [\"tcp443\",\"tcp80\",\"udp443\"]) and ([.[] | keys == [\"by\",\"free\",\"ours\"]] | all) and ([.[] | .free] | all) and ([.[] | .by == null] | all) and ([.[] | .ours == false] | all)" <<<"$last"' "$last"
check "lazy-check: ключей прошлых заходов нет — keys_have 0, keys_new 5 (по умолчанию)" \
    '[ "$(jget .keys_have)" = 0 ] && [ "$(jget .keys_new)" = 5 ]' "$last"
check "lazy-check: udp443_line — на UDP 443 встанет 2.0 (строкой)" '[ "$(jq -c .udp443_line <<<"$last")" = "\"2.0\"" ]' "$last"
check "lazy-check: domain=domain_display=example.com, message — итог словами" \
    '[ "$(jget .domain)" = example.com ] && [ "$(jget .domain_display)" = example.com ] && grep -q "Ленивый Pro встанет" <<<"$(jget .message)"' "$last"
check "lazy-check ничего не записал" '[ "$(cfgv .installed)" != true ] && [ "$(nprof)" = 0 ]'
act lazy-check
expect "lazy-check без --domain → missing_arg" error missing_arg
act lazy-check --domain='a b'
expect "lazy-check с пробелом в домене → bad_arg ещё в диспетчере" error bad_arg
act lazy-check --domain=1.2.3.4
expect "lazy-check с IP → domain_is_ip" error domain_is_ip
act lazy-setup --domain=example.com --keys=21
expect "lazy-setup --keys=21 → bad_arg (1–20)" error bad_arg
act lazy-check --domain=example.com --keys=3
check "lazy-check --keys=3: keys_new 3" '[ "$(jget .keys_new)" = 3 ] && [ "$(jget .keys_have)" = 0 ]' "$last"
act lazy-check --domain=example.com --keys=21
expect "lazy-check --keys=21 → bad_arg (1–20)" error bad_arg

act lazy-setup --domain=example.com --keys=3
expect "lazy-setup example.com --keys=3: success" success
check "lazy-setup: ровно поля договора" 'fields_are "$LS_FIELDS"' "$last"
check "lazy-setup: mode=pro, udp443 = awg2 (2.0), domain, warnings пусты (обходить было нечего)" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .udp443.profile)" = awg2 ] && [ "$(jget .udp443.line)" = 2.0 ] && [ "$(jget .domain)" = example.com ] && [ "$(jget ".warnings|length")" = 0 ]' "$last"
check "lazy-setup: profiles — 3.1 на случайном 1024–9999, 2.0 на 443, у каждого {id, line, port}" \
    'jq -e "(.profiles | map(.id) | sort) == [\"awg2\",\"awg3\"] and ([.profiles[] | keys == [\"id\",\"line\",\"port\"]] | all) and (.profiles[] | select(.id == \"awg2\") | .port) == 443 and ((.profiles[] | select(.id == \"awg3\") | .port) as \$p | \$p >= 1024 and \$p <= 9999)" <<<"$last"' "$last"
check "lazy-setup: site.template из каталога (не заглушка), panel_on_domain=true (булево)" \
    '[ -n "$(jget .site.template)" ] && [ "$(jget .site.template)" != builtin_stub ] && [ "$(jq -c .panel_on_domain <<<"$last")" = true ] && jq -e ".site | keys == [\"template\"]" <<<"$last"' "$last"
check "lazy-setup: три ключа {name, label, page}, имя латиницей, подпись есть, страница на домене" \
    'jq -e "(.keys | length) == 3 and ([.keys[] | keys == [\"label\",\"name\",\"page\"] and (.name | test(\"^[A-Za-z0-9_-]{1,32}$\")) and (.label | length > 0) and (.page | test(\"^https://example\\\\.com/p-[A-Za-z0-9_-]{43}/$\"))] | all)" <<<"$last"' "$last"
check "config.json: installed, pro, домен, site.cert=letsencrypt, путь панели, lazy.keys — те же три имени" \
    '[ "$(cfgv .installed)" = true ] && [ "$(cfgv .mode)" = pro ] && [ "$(cfgv .domain)" = example.com ] && [ "$(cfgv .site.cert)" = letsencrypt ] && [ -n "$(cfgv .panel.miniapp_path)" ] && [ "$(cfgv ".lazy.keys|sort|join(\",\")")" = "$(keys_sorted)" ]'
check "users.json: три человека, у каждого обе линии (awg2 и awg3)" \
    '[ "$(jq "[.users[] | select((.peers|has(\"awg2\")) and (.peers|has(\"awg3\")))] | length" "$GOAMNEZIA_DIR/users.json")" = 3 ]'
check "страницы подключения: три, по одной на пользователя" '[ "$(npages)" = 3 ]'
# Шаги сценария идут под спиннером (ga_step): их DRY-строки в stderr моста не выходят — сверяем итог и журнал действий.
check "vhost сайта на домене, сайт включён модулем сайта (журнал: change-mode pro domain=example.com), index.html развёрнут" \
    'grep -qF "server_name example.com;" "$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia" && grep -q "\"action\":\"change-mode\",.*pro domain=example.com" "$GOAMNEZIA_AUDIT_LOG" && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ]' "$(grep change-mode "$GOAMNEZIA_AUDIT_LOG" | head -3)"
check "панель поставлена службой, пароль ждёт показа в SSH (password_pending)" '[ "$(cfgv .panel.password_pending)" = true ]' "$(cfgv .panel)"
check "журнал действий: lazy-setup; адреса страниц (секрет) в него не попали" \
    'grep -q "\"action\":\"lazy-setup\"" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$(jget .keys[0].page)" "$GOAMNEZIA_AUDIT_LOG"'

keys_a=$(keys_sorted); ports_a="$(cfgv .profiles.awg2.port)/$(cfgv .profiles.awg3.port)"
# Порты держим мы сами: наш nginx на TCP 80/443 (vhost goamnezia) и наш awg2 на UDP 443 (порт опубликован docker).
export GOAMNEZIA_SS_FIXTURE_TCP="$FX/preflight/ss_tcp_nginx.txt"
printf 'amnezia-awg2 0.0.0.0:443->443/udp, :::443->443/udp\n' >"$TMP/A/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/A/docker_ps.txt"
act lazy-check --domain=example.com
check "lazy-check на установленном: наш nginx и наши профили — «свободно», plan=pro, причин нет" \
    '[ "$(jget .plan)" = pro ] && [ "$(jget ".reasons|length")" = 0 ] && [ "$(jget .ports.tcp443.free)" = true ]' "$last"
check "LZ-05: наши порты — free и ours (людям «занят нами»), by — null; на 443 — наша 2.0" \
    'jq -e "([.ports[] | .free and .ours and .by == null] | all) and .udp443_line == \"2.0\"" <<<"$last"' "$last"
check "LZ-05: три ключа прошлого захода засчитываются — keys_have 3, keys_new 2 из пяти" \
    '[ "$(jget .keys_have)" = 3 ] && [ "$(jget .keys_new)" = 2 ]' "$last"
act lazy-check --domain=example.com --keys=3
check "LZ-05: --keys=3 — новых не будет (keys_new 0)" '[ "$(jget .keys_new)" = 0 ]' "$last"
unset GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_DOCKER_PS_FIXTURE
act lazy-setup --domain=example.com --keys=3
expect "повтор lazy-setup на установленном (из «Режим и сайт»): success" success
check "повтор ничего не ломает: те же ключи и страницы, людей и страниц не прибавилось, порты прежние" \
    '[ "$(keys_sorted)" = "$keys_a" ] && [ "$(jq ".users | length" "$GOAMNEZIA_DIR/users.json")" = 3 ] && [ "$(npages)" = 3 ] && [ "$(cfgv .profiles.awg2.port)/$(cfgv .profiles.awg3.port)" = "$ports_a" ] && jq -e "[.keys[].page | test(\"^https://example\")] | all" <<<"$last"' "$last"
before=$(sig)
lazy --domain=example.com
check "install.sh --lazy на установленном: rc 0, «уже установлен», состояние не тронуто" \
    '[ "$rc" = 0 ] && flat | grep -q "уже установлен" && [ "$(sig)" = "$before" ]' "rc=$rc $(tail -5 "$OUT")"

act pro-line --profile=awg3
expect "pro-line awg3: success" success
check "pro-line: ровно поля договора {mode, udp443, profiles}, mode=pro" 'fields_are "$PL_FIELDS" && [ "$(jget .mode)" = pro ]' "$last"
check "pro-line: 3.1 на 443, 2.0 уехал на случайный порт (не 443); message называет обе линии и новый порт" \
    '[ "$(jget .udp443.profile)" = awg3 ] && [ "$(jget .udp443.line)" = 3.1 ] && [ "$(cfgv .profiles.awg3.port)" = 443 ] && p2=$(cfgv .profiles.awg2.port) && [ "$p2" != 443 ] && grep -q "AmneziaWG 3.1" <<<"$(jget .message)" && grep -q "AmneziaWG 2.0" <<<"$(jget .message)" && grep -q "$p2" <<<"$(jget .message)" && [ "$(jq -r ".profiles[] | select(.id == \"awg2\") | .port" <<<"$last")" = "$p2" ]' "$last"
act pro-line --profile=awg3
expect "pro-line awg3 ещё раз: success" success
check "повтор: «уже на UDP 443», порты прежние" 'grep -q "уже на UDP 443" <<<"$(jget .message)" && [ "$(cfgv .profiles.awg3.port)" = 443 ]' "$last"
act lazy-check --domain=example.com
check "lazy-check, когда UDP 443 уже держит наша 3.1: plan=pro, udp443_line=3.1 (она там и останется)" \
    '[ "$(jget .plan)" = pro ] && [ "$(jget .udp443_line)" = 3.1 ]' "$last"
act pro-line --profile=awg2
expect "pro-line awg2 — обратно: success" success
check "обратно: 2.0 на 443, 3.1 на случайном" '[ "$(cfgv .profiles.awg2.port)" = 443 ] && [ "$(cfgv .profiles.awg3.port)" != 443 ]' "$last"
act pro-line --profile=awg9
expect "pro-line --profile=awg9 → bad_arg" error bad_arg
act change-mode --mode=lite_site
expect "change-mode lite_site (Semi-Pro): success" success
act pro-line --profile=awg3
expect "pro-line вне Pro → mode_not_pro" error mode_not_pro
check "mode_not_pro называет режим по-новому (Semi-Pro)" 'grep -q "Semi-Pro" <<<"$(jget .message)"' "$last"

# ══ B. punycode ══════════════════════════════════════════════════════════════
section "B. кириллица → punycode"
stand B
act lazy-check --domain='https://Пример.РФ/путь?x=1.'
expect "lazy-check с https://, кириллицей и путём: success" success
check "domain — punycode, domain_display — кириллицей" \
    '[ "$(jget .domain)" = xn--e1afmkfd.xn--p1ai ] && [ "$(jget .domain_display)" = "пример.рф" ] && [ "$(jget .plan)" = pro ]' "$last"
act lazy-setup --domain='пример.рф.' --keys=1
expect "lazy-setup пример.рф: success" success
check "сайт и сертификат — на punycode, страница — на punycode, config.json domain в xn--" \
    '[ "$(jget .domain)" = xn--e1afmkfd.xn--p1ai ] && [ "$(cfgv .domain)" = xn--e1afmkfd.xn--p1ai ] && grep -qF "server_name xn--e1afmkfd.xn--p1ai;" "$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia" && grep -q "pro domain=xn--e1afmkfd.xn--p1ai" "$GOAMNEZIA_AUDIT_LOG" && jq -e ".keys[0].page | startswith(\"https://xn--e1afmkfd.xn--p1ai/p-\")" <<<"$last"' "$last"
act lazy-setup --domain=xn--e1afmkfd.xn--p1ai --keys=1
expect "повтор тем же доменом в punycode (так его шлют панель и бот): success, ключ прежний" success

# ══ C. Домен не сюда ═════════════════════════════════════════════════════════
section "C. домен смотрит не сюда"
stand C
export GOAMNEZIA_DNS_STUB="198.51.100.7"
act lazy-check --domain=example.com
expect "lazy-check: success (проверка — это ответ, а не ошибка)" success
check "plan=blocked, points_here=false, a — чужой адрес, причина dns_elsewhere, в message — куда указывает" \
    '[ "$(jget .plan)" = blocked ] && [ "$(jget .points_here)" = false ] && [ "$(jget ".a|join(\",\")")" = 198.51.100.7 ] && has_reason dns_elsewhere && grep -q "198.51.100.7" <<<"$(jget .message)"' "$last"
check "blocked: udp443_line = null (ставить ничего не будем)" '[ "$(jq -c .udp443_line <<<"$last")" = null ]' "$last"
act lazy-setup --domain=example.com
expect "lazy-setup → dns_not_here" error dns_not_here
check "отказ несёт plan=blocked и reasons; ничего не установлено и не записано" \
    '[ "$(jget .plan)" = blocked ] && has_reason dns_elsewhere && [ "$(cfgv .installed)" != true ] && [ "$(nprof)" = 0 ] && [ ! -f "$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia" ]' "$last"
export GOAMNEZIA_DNS_STUB=""
act lazy-check --domain=example.com
check "A-записи нет вовсе: blocked, dns_none, в message — какой адрес вписать" \
    '[ "$(jget .plan)" = blocked ] && has_reason dns_none && grep -q "203.0.113.10" <<<"$(jget .message)"' "$last"
export GOAMNEZIA_DNS_STUB="198.51.100.7 203.0.113.10"
act lazy-check --domain=example.com
check "часть A-записей чужая: blocked, dns_partial, чужой адрес назван" \
    '[ "$(jget .plan)" = blocked ] && has_reason dns_partial && grep -q "198.51.100.7" <<<"$(jget .message)"' "$last"
export GOAMNEZIA_DNS_STUB="198.51.100.7"
lazy --domain=example.com
check "--lazy: домен не сюда — повтор проверки раз в N с до срока, потом отмена без терминала; rc 1" \
    '[ "$rc" = 1 ] && flat | grep -q "указывает не сюда: 198.51.100.7" && flat | grep -q "Проверка 2" && flat | grep -q "так и не указал сюда" && flat | grep -q "Ленивый Pro отменён"' "rc=$rc $(tail -8 "$OUT")"
check "--lazy: ничего не установлено" '[ "$(cfgv .installed)" != true ] && [ "$(nprof)" = 0 ]'
lazy
check "--lazy без --domain и без терминала: вопрос → конец ввода → отмена, rc 1" \
    '[ "$rc" = 1 ] && flat | grep -q "Ленивый Pro отменён"' "rc=$rc $(tail -5 "$OUT")"
lazy --domain=example.com --keys=0
check "--lazy --keys=0 → rc 2 и слова «от 1 до 20»" '[ "$rc" = 2 ] && flat | grep -q "от 1 до 20"' "rc=$rc $(tail -3 "$OUT")"

# ══ D. Cloudflare ════════════════════════════════════════════════════════════
section "D. домен за прокси Cloudflare"
stand D
export GOAMNEZIA_DNS_STUB="104.16.132.229 172.67.1.2"
# У домена за прокси AAAA-записи — IPv6 самого Cloudflare (2606:4700::/32, 2a06:98c0::/29): не «чужие» (LZ-03).
export GOAMNEZIA_DNS_STUB_AAAA="2606:4700:3030::6815:1234 2a06:98c1:3120::3"
act lazy-check --domain=example.com
check "не отказ: plan=pro, cloudflare_proxied=true, points_here=false, причина cloudflare_proxied" \
    '[ "$(jget .plan)" = pro ] && [ "$(jget .cloudflare_proxied)" = true ] && [ "$(jget .points_here)" = false ] && has_reason cloudflare_proxied' "$last"
check "LZ-03: IPv6 Cloudflare в AAAA — не aaaa_foreign; aaaa показаны как есть" \
    '! has_reason aaaa_foreign && [ "$(jget ".aaaa|length")" = 2 ]' "$last"
check "LZ-03: message моста говорит, что ключи ведут на IP сервера" \
    'grep -q "Ключи ведут прямо на IP сервера 203.0.113.10" <<<"$(jget .message)"' "$(jget .message)"
act lazy-setup --domain=example.com --keys=1
expect "lazy-setup за Cloudflare: success" success
check "ключи ведут на IP: endpoint_host = адрес сервера; warnings — обойдённая причина cloudflare_proxied; сайт на домене" \
    '[ "$(cfgv .endpoint_host)" = 203.0.113.10 ] && has_warning cloudflare_proxied && [ "$(cfgv .domain)" = example.com ] && [ "$(jget .mode)" = pro ]' "$last"
check "LZ-03: и message lazy-setup — «ключи ведут на IP»; aaaa_foreign среди предупреждений нет" \
    'grep -q "Ключи ведут прямо на IP сервера 203.0.113.10" <<<"$(jget .message)" && ! has_warning aaaa_foreign' "$last"
export GOAMNEZIA_DNS_STUB_AAAA="2001:db8::1"
act lazy-check --domain=example.com
check "LZ-03: AAAA не из сетей Cloudflare при том же прокси — aaaa_foreign, как и было" 'has_reason aaaa_foreign' "$last"
unset GOAMNEZIA_DNS_STUB_AAAA
lib_run 'for ip in 2606:4700:3030::6815:1234 2a06:98c7::1 2a06:98c8::1 2400:cb00:2049::1 2001:db8::1 2606::1 1.2.3.4; do
        if ga_site_ip6_is_cloudflare "$ip"; then echo "$ip yes"; else echo "$ip no"; fi; done'
check "ga_site_ip6_is_cloudflare: сети /32 и /29 — да, соседние и чужие — нет" \
    '[ "$(tr "\n" " " <"$OUT")" = "2606:4700:3030::6815:1234 yes 2a06:98c7::1 yes 2a06:98c8::1 no 2400:cb00:2049::1 yes 2001:db8::1 no 2606::1 no 1.2.3.4 no " ]' "$(cat "$OUT")"

# ══ E. UDP 443 занят чужим ═══════════════════════════════════════════════════
section "E. UDP 443 занят чужим → Semi-Pro; потом порт свободен — повтор ничего не двигает"
stand E
export GOAMNEZIA_SS_FIXTURE_UDP="$FX/preflight/ss_udp_xray.txt"
act lazy-check --domain=example.com
check "plan=semi_pro, udp443_busy, ports.udp443 {free:false, by: процесс}, в message — Semi-Pro" \
    '[ "$(jget .plan)" = semi_pro ] && has_reason udp443_busy && [ "$(jget .ports.udp443.free)" = false ] && grep -q "xray" <<<"$(jget .ports.udp443.by)" && grep -q "Semi-Pro" <<<"$(jget .message)"' "$last"
check "semi_pro (UDP 443 занят): udp443_line = null" '[ "$(jq -c .udp443_line <<<"$last")" = null ]' "$last"
act lazy-setup --domain=example.com --keys=2
expect "lazy-setup при занятом UDP 443: success" success
check "итог Semi-Pro: mode=lite_site, udp443=null, профили не на 443, warnings — udp443_busy" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .udp443)" = null ] && [ "$(cfgv .profiles.awg2.port)" != 443 ] && [ "$(cfgv .profiles.awg3.port)" != 443 ] && has_warning udp443_busy' "$last"
keys_e=$(keys_sorted); ports_e="$(cfgv .profiles.awg2.port)/$(cfgv .profiles.awg3.port)"
unset GOAMNEZIA_SS_FIXTURE_UDP
act lazy-check --domain=example.com
check "UDP 443 свободен, но обе линии стоят на своих портах с людьми — semi_pro, no_line_for_443, udp443_line = null" \
    '[ "$(jget .plan)" = semi_pro ] && has_reason no_line_for_443 && [ "$(jq -c .udp443_line <<<"$last")" = null ]' "$last"
act lazy-setup --domain=example.com --keys=2
expect "повтор на установленном: success" success
check "порты профилей не двинуты, режим прежний, ключи те же" \
    '[ "$(cfgv .profiles.awg2.port)/$(cfgv .profiles.awg3.port)" = "$ports_e" ] && [ "$(jget .mode)" = lite_site ] && [ "$(keys_sorted)" = "$keys_e" ] && has_warning no_line_for_443' "$last"

# ══ F. TCP 443 занят чужим ═══════════════════════════════════════════════════
section "F. TCP 80/443 занят чужим nginx"
stand F
export GOAMNEZIA_SS_FIXTURE_TCP="$FX/preflight/ss_tcp_nginx.txt"
cp -R "$FX/preflight/nginx_gotelegram/." "$GOAMNEZIA_NGINX_DIR/"
act lazy-check --domain=example.com
check "plan=blocked, tcp443_busy и tcp80_busy, by — nginx с PID и соседом по серии" \
    '[ "$(jget .plan)" = blocked ] && has_reason tcp443_busy && has_reason tcp80_busy && [ "$(jget .ports.tcp443.free)" = false ] && grep -q "nginx (PID 812" <<<"$(jget .ports.tcp443.by)" && grep -q "goTelegram" <<<"$(jget .ports.tcp443.by)"' "$last"
act lazy-setup --domain=example.com
expect "lazy-setup → port_busy (чужое не останавливаем)" error port_busy
check "в отказе — кто держит; ничего не поставлено" \
    'grep -q "nginx" <<<"$(jget .message)" && [ "$(nprof)" = 0 ] && [ "$(cfgv .installed)" != true ]' "$last"

# ══ G. TCP 443 держит контейнер приложения ═══════════════════════════════════
section "G. TCP 443 держит контейнер приложения Amnezia (Xray)"
stand G
export GOAMNEZIA_DOCKER_PS_FIXTURE="$FX/import/docker_ps.txt" GOAMNEZIA_CONTAINER_FS_DIR="$TMP/G/cfs"
cp -R "$FX/import/fs/." "$GOAMNEZIA_CONTAINER_FS_DIR/"
act lazy-check --domain=example.com
check "plan=blocked, tcp443_app; app_containers — {name, line, port}: 2.0 на 38123, Xray без линии" \
    '[ "$(jget .plan)" = blocked ] && has_reason tcp443_app && jq -e "([.app_containers[] | keys == [\"line\",\"name\",\"port\"]] | all) and (.app_containers[] | select(.name == \"amnezia-awg2\") | .line == \"2.0\" and .port == 38123) and (.app_containers[] | select(.name == \"amnezia-xray\") | .line == \"\")" <<<"$last"' "$last"
conf_before=$(cksum <"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf")
act lazy-setup --domain=example.com
expect "lazy-setup → app_container_busy" error app_container_busy
check "пользователей молча не ломаем: ничего не подхвачено и не установлено, контейнер не тронут, в тексте — amnezia-xray" \
    'grep -q "amnezia-xray" <<<"$(jget .message)" && [ "$(nprof)" = 0 ] && [ "$(cksum <"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf")" = "$conf_before" ] && ! grep -qE "docker (rm|stop) .*amnezia" "$ERR"' "$last"

# ══ H. Сервер от приложения ══════════════════════════════════════════════════
section "H. сервер от приложения Amnezia: подхват без вопроса"
stand H
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/H/cfs"
cp -R "$FX/import/fs/." "$GOAMNEZIA_CONTAINER_FS_DIR/"
rm -rf "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-xray"
grep -E '^amnezia-awg2? ' "$FX/import/docker_ps.txt" >"$TMP/H/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/H/docker_ps.txt"
act lazy-check --domain=example.com
check "plan=pro, 2.0 приложения и 1.x — в app_containers, причин нет; 2.0 на своём порту — на UDP 443 встанет 3.1" \
    '[ "$(jget .plan)" = pro ] && [ "$(jget ".reasons|length")" = 0 ] && [ "$(jget "[.app_containers[].name]|sort|join(\",\")")" = "amnezia-awg,amnezia-awg2" ] && [ "$(jget .udp443_line)" = 3.1 ]' "$last"
act lazy-setup --domain=example.com --keys=2
expect "lazy-setup на сервере от приложения: success" success
check "подхвачены 2.0 (порт 38123 не тронут) и 1.x; недостающая 3.1 — на UDP 443; warnings — adopted" \
    '[ "$(cfgv .profiles.awg2.preset)" = imported ] && [ "$(cfgv .profiles.awg2.port)" = 38123 ] && [ "$(cfgv .profiles.awg1.container)" = amnezia-awg ] && [ "$(cfgv .profiles.awg3.port)" = 443 ] && [ "$(jget .udp443.profile)" = awg3 ] && has_warning adopted' "$last"
check "контейнер приложения 2.0 не пересобран и не снесён" '! grep -qE "docker (rm|build) .*amnezia-awg2( |$)" "$ERR"' "$(grep -E 'docker (rm|build)' "$ERR" | head -3)"
check "люди приложения на месте, два новых ключа — с 2.0 и 3.1, без 1.x (wg0)" \
    'jq -e "([.users[].name] | index(\"iPhone_Anny\")) != null" "$GOAMNEZIA_DIR/users.json" && [ "$(jq --argjson k "$(jget "[.keys[].name]")" "[.users[] | select(.name as \$n | \$k | index(\$n)) | select((.peers|has(\"awg2\")) and (.peers|has(\"awg3\")) and ((.peers|has(\"awg1\"))|not))] | length" "$GOAMNEZIA_DIR/users.json")" = 2 ]' "$(jq -c '[.users[] | {name, p: (.peers|keys)}]' "$GOAMNEZIA_DIR/users.json" 2>/dev/null)"
act pro-line --profile=awg2
expect "pro-line awg2 (подхвачен, люди без ключа) без согласия → adopted_keyless" error adopted_keyless
check "отказ называет профиль полем profile (как у profile-port); порты не тронуты — 2.0 на 38123, 3.1 на 443" \
    '[ "$(jget .profile)" = awg2 ] && [ "$(cfgv .profiles.awg2.port)" = 38123 ] && [ "$(cfgv .profiles.awg3.port)" = 443 ]' "$last"
act pro-line --profile=awg2 --adopted-confirm=awg2
expect "pro-line awg2 --adopted-confirm=awg2: success" success
check "с согласием: 2.0 на 443, 3.1 уехал на случайный порт" '[ "$(cfgv .profiles.awg2.port)" = 443 ] && [ "$(cfgv .profiles.awg3.port)" != 443 ] && [ "$(jget .udp443.profile)" = awg2 ]' "$last"
act lazy-check --domain=example.com
check "lazy-check после переноса: на UDP 443 подхваченная 2.0 — udp443_line=2.0" '[ "$(jget .plan)" = pro ] && [ "$(jget .udp443_line)" = 2.0 ]' "$last"

# Контейнер приложения 2.0 сам стоит на UDP 443 (порт опубликован docker, слушателя на хосте нет — userland-proxy выключен):
# его подхватим, и он останется на 443 со своими людьми — линия на 443 будет его, 2.0.
stand H2
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/H2/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$FX/import/fs/amnezia-awg2" "$GOAMNEZIA_CONTAINER_FS_DIR/"
conf2="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
sed 's/^ListenPort = 38123$/ListenPort = 443/' "$conf2" >"$conf2.new" && mv "$conf2.new" "$conf2"
printf 'amnezia-awg2 0.0.0.0:443->443/udp, :::443->443/udp\n' >"$TMP/H2/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/H2/docker_ps.txt"
act lazy-check --domain=example.com
check "UDP 443 у контейнера приложения 2.0: не причина, plan=pro, udp443_line=2.0, контейнер — в app_containers на 443" \
    '[ "$(jget .plan)" = pro ] && ! has_reason udp443_busy && [ "$(jget .udp443_line)" = 2.0 ] && jq -e "(.app_containers[] | select(.name == \"amnezia-awg2\") | .port) == 443" <<<"$last"' "$last"

# ══ L. Приложение держит 3.1 в amnezia-awg2 (LZ-02) ══════════════════════════
section "L. сервер приложения, где amnezia-awg2 — это 3.1: линия по версии — на 443 недостающая 2.0"
stand L
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/L/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$FX/import/fs/amnezia-awg2" "$GOAMNEZIA_CONTAINER_FS_DIR/"
cp "$FX/import/awg2_conf_line31.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
grep -E '^amnezia-awg2 ' "$FX/import/docker_ps.txt" >"$TMP/L/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/L/docker_ps.txt"
act lazy-check --domain=example.com
check "план pro; amnezia-awg2 приложения — линия 3.1 на 38123; на UDP 443 встанет недостающая 2.0" \
    '[ "$(jget .plan)" = pro ] && jq -e "(.app_containers[] | select(.name == \"amnezia-awg2\") | .line == \"3.1\" and .port == 38123) and .udp443_line == \"2.0\"" <<<"$last"' "$last"
act lazy-setup --domain=example.com --keys=2
expect "lazy-setup на сервере приложения с 3.1 в awg2: success" success
check "3.1 приложения подхвачена в awg2 и стоит на 38123; 2.0 — наша, на месте awg3 и на UDP 443; второй 3.1 нет" \
    '[ "$(cfgv .profiles.awg2.preset)" = imported ] && [ "$(cfgv .profiles.awg2.line)" = 3.1 ] && [ "$(cfgv .profiles.awg2.port)" = 38123 ] && [ "$(cfgv .profiles.awg3.line)" = 2.0 ] && [ "$(cfgv .profiles.awg3.port)" = 443 ] && [ "$(cfgv .profiles.awg3.container)" = amnezia-awg3 ]' "$(jq -c .profiles "$GOAMNEZIA_DIR/config.json" 2>/dev/null)"
check "ответ — настоящие линии: udp443 = {awg3, 2.0}, profiles — по одной 2.0 и 3.1" \
    '[ "$(jget .udp443.profile)" = awg3 ] && [ "$(jget .udp443.line)" = 2.0 ] && [ "$(jget "[.profiles[].line] | sort | join(\",\")")" = "2.0,3.1" ]' "$last"
check "новые ключи — с обеими линиями (awg2 — 3.1, awg3 — 2.0)" \
    '[ "$(jq --argjson k "$(jget "[.keys[].name]")" "[.users[] | select(.name as \$n | \$k | index(\$n)) | select((.peers|has(\"awg2\")) and (.peers|has(\"awg3\")))] | length" "$GOAMNEZIA_DIR/users.json")" = 2 ]' "$(jq -c '[.users[] | {name, p: (.peers|keys)}]' "$GOAMNEZIA_DIR/users.json" 2>/dev/null)"
lib_run 'amnezia_profile_install awg1 "" default "" 2.0; echo "rc=$? code=$GA_ERR_CODE"'
check "линию по просьбе 1.x не подменить: awg1 с 2.0 — invalid_line" 'grep -q "rc=2 code=invalid_line" "$OUT"' "$(cat "$OUT") $(errs)"
# SSH: сводка --lazy на таком же сервере говорит правду — 2.0 на 443, 3.1 подхвачена и стоит на своём порту
stand L2
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/L2/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$FX/import/fs/amnezia-awg2" "$GOAMNEZIA_CONTAINER_FS_DIR/"
cp "$FX/import/awg2_conf_line31.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/L/docker_ps.txt"
lazy --domain=example.com --keys=1
check "сводка SSH: «На UDP 443 — AmneziaWG 2.0 …, 3.1 подхвачена у приложения и остаётся на своём порту 38123»" \
    '[ "$rc" = 0 ] && flat | grep -q "На UDP 443 — AmneziaWG 2.0: её понимает больше всего клиентов, включая роутеры Keenetic. 3.1 подхвачена у приложения и остаётся на своём порту 38123" && ! flat | grep -q "2.0 подхвачена"' "rc=$rc $(tail -25 "$OUT")"
unset GOAMNEZIA_DOCKER_PS_FIXTURE GOAMNEZIA_CONTAINER_FS_DIR

# ══ M. Оба контейнера приложения на одной подсети (SUBNET-01) ══════════════════
# Приложение Amnezia ставит и старый amnezia-awg, и amnezia-awg2 на 10.8.1.0/24. Было: старый (первый в docker ps)
# подхвачен, amnezia-awg2 с людьми — отказ «подсеть пересекается», и весь сценарий падал adopt_failed. Теперь оба
# подхвачены как есть, недостающая линия — на свободной подсети, предупреждение subnet_overlap с выходом.
section "M. сервер приложения: старый и 2.0 на одной 10.8.1.0/24 — подхват обоих, новая линия на своей подсети"
stand M
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/M/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$FX/import/fs/amnezia-awg" "$FX/import/fs/amnezia-awg2" "$GOAMNEZIA_CONTAINER_FS_DIR/"
wg0m="$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg/opt/amnezia/awg/wg0.conf"
sed 's#10\.8\.5\.#10.8.1.#g' "$wg0m" >"$wg0m.new" && mv "$wg0m.new" "$wg0m"
printf 'amnezia-awg 0.0.0.0:51820->51820/udp\namnezia-awg2 0.0.0.0:38123->38123/udp, :::38123->38123/udp\n' >"$TMP/M/docker_ps.txt"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$TMP/M/docker_ps.txt"
act lazy-setup --domain=example.com --keys=1
expect "lazy-setup: success, а не adopt_failed" success
check "оба подхвачены на своей 10.8.1.0/24 (старый — awg1, 2.0 — awg2), 3.1 — на подсети не 10.8.1.0/24; warnings: adopted и subnet_overlap" \
    '[ "$(cfgv .profiles.awg1.container)" = amnezia-awg ] && [ "$(cfgv .profiles.awg1.subnet)" = 10.8.1.0/24 ] && [ "$(cfgv .profiles.awg2.preset)" = imported ] && [ "$(cfgv .profiles.awg2.subnet)" = 10.8.1.0/24 ] && [ -n "$(cfgv ".profiles.awg3.subnet // empty")" ] && [ "$(cfgv .profiles.awg3.subnet)" != 10.8.1.0/24 ] && has_warning adopted && has_warning subnet_overlap' \
    "$last $(jq -c '.profiles | map_values({subnet, container})' "$GOAMNEZIA_DIR/config.json" 2>/dev/null)"
check "контейнеры приложения не пересобраны и не сняты" '! grep -qE "docker (rm|build) .*amnezia-awg2?( |$)" "$ERR"' "$(grep -E 'docker (rm|build)' "$ERR" | head -3)"
check "в ходе сценария сказано, кого перенести: AmneziaWG 2.0 · порт 38123 · из приложения на свободную 10.8.2.0/24" \
    'grep -qF "перенесите AmneziaWG 2.0 · порт 38123 · из приложения на свободную подсеть 10.8.2.0/24" "$ERR"' "$(grep -i подсет "$ERR" | head -3)"
unset GOAMNEZIA_DOCKER_PS_FIXTURE GOAMNEZIA_CONTAINER_FS_DIR

# ══ I. Встроенная заглушка ═══════════════════════════════════════════════════
section "I. ни один случайный шаблон не встал — встроенная заглушка"
stand I
mkdir -p "$TMP/I/notpl"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$TMP/I/notpl"
act lazy-setup --domain=example.com --keys=1
expect "lazy-setup с неработающим каталогом шаблонов: success" success
check "три попытки, потом builtin_stub: site.template, warnings template_fallback, index.html с доменом в заголовке" \
    '[ "$(jget .site.template)" = builtin_stub ] && has_warning template_fallback && grep -q "<title>example.com</title>" "$GOAMNEZIA_SITE_ROOT/index.html"' "$last $(head -c 300 "$GOAMNEZIA_SITE_ROOT/index.html" 2>/dev/null)"

# ══ J. Let's Encrypt отказал ═════════════════════════════════════════════════
section "J. Let's Encrypt отказал"
stand J
lib_run 'website_enable_pro() { ga_err cert_failed "stub: refused"; return 1; }
    lazy_setup example.com 1'
check "чистый сервер: сценарий дошёл до конца, rc 0, JSON" '[ "$rc" = 0 ] && is_json "$last"' "rc=$rc $(errs)"
check "сайт на запасном самоподписанном (site.cert=selfsigned), домен сохранён, Pro; warnings cert_fallback и panel_off" \
    '[ "$(cfgv .site.cert)" = selfsigned ] && [ "$(cfgv .domain)" = example.com ] && [ "$(jget .mode)" = pro ] && has_warning cert_fallback && has_warning panel_off && [ "$(jq -c .panel_on_domain <<<"$last")" = false ]' "$last"
check "ключ заведён, страницы нет: page = null" '[ "$(jget ".keys|length")" = 1 ] && [ "$(jq -c ".keys[0].page" <<<"$last")" = null ]' "$last"
check "L-26 причина отказа неизвестна — полей cert_cause и cert_retry_after в ответе нет" \
    '[ "$(jq -c "has(\"cert_cause\") or has(\"cert_retry_after\")" <<<"$last")" = false ]' "$last"
stand J1
lib_run 'website_enable_pro() { GA_CERT_CAUSE=limit; GA_CERT_RETRY_AFTER="2026-09-27 10:00"; ga_err cert_failed "stub: rate limited"; return 1; }
    lazy_setup example.com 1'
check "L-26 запасной сертификат из-за лимита: рядом с cert_fallback причина словом и срок (cert_cause, cert_retry_after)" \
    '[ "$rc" = 0 ] && has_warning cert_fallback && [ "$(jget .cert_cause)" = limit ] && [ "$(jget .cert_retry_after)" = "2026-09-27 10:00" ]' "rc=$rc $last"
stand J2
act lazy-setup --domain=old.example.com --keys=1
expect "установленный сервер с сайтом на old.example.com: success" success
before=$(sig)
lib_run 'website_enable_pro() { ga_err cert_failed "stub: refused, the domain is back to old.example.com"; return 1; }
    lazy_setup new.example.com 1'
check "живой сайт при отказе Let's Encrypt не уходит на запасной: rc≠0, cert_failed, домен и сертификат прежние" \
    '[ "$rc" != 0 ] && [ "$(cfgv .domain)" = old.example.com ] && [ "$(cfgv .site.cert)" = letsencrypt ] && grep -q "cert_failed\|refused" "$ERR"' "rc=$rc $(errs)"
# L-26: тот же отказ через мост — ответ несёт причину словом (бот говорит своим текстом, что случилось и что делать)
lib_run 'website_enable_pro() { GA_CERT_CAUSE=net; ga_err cert_failed "stub: no route"; return 1; }
    ARG_domain=new.example.com; ARG_keys=1; _action_dispatch_locked lazy-setup'
check "L-26 мост lazy-setup: отказ на живом сайте — cert_failed, cert_cause=net, cert_retry_after пуст, домен прежний" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = cert_failed ] && [ "$(jget .cert_cause)" = net ] && [ "$(jq -c .cert_retry_after <<<"$last")" = "\"\"" ] && [ "$(cfgv .domain)" = old.example.com ]' "rc=$rc $last"
# change-mode и прочие ветки зовут модуль сайта через _action_run: причина — только у отказа Let's Encrypt
lib_run 'enable_stub() { GA_CERT_CAUSE=dns; ga_err cert_failed "stub: A record"; return 1; }
    _action_run enable_failed enable_stub'
check "L-26 _action_run (change-mode): cert_failed с cert_cause=dns" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = cert_failed ] && [ "$(jget .cert_cause)" = dns ]' "$last"
lib_run 'GA_CERT_CAUSE=net; other_stub() { ga_err nginx_conf_invalid "stub: nginx -t"; return 1; }
    _action_run enable_failed other_stub'
check "L-26 _action_run: прочий отказ — без полей причины, прежняя причина не просачивается" \
    '[ "$(jget .code)" = nginx_conf_invalid ] && [ "$(jq -c "has(\"cert_cause\")" <<<"$last")" = false ]' "$last"

# ══ K. Без терминала: install.sh --lazy на чистом сервере ═══════════════════
section "K. install.sh --lazy --domain=… --keys=2 без терминала (goInstaller)"
stand K
lazy --domain=https://Example.com/ --keys=2
check "rc 0; домен взят из адреса целиком; сводка: сайт, панель на домене, пароль один раз, два ключа со страницами" \
    '[ "$rc" = 0 ] && flat | grep -q "Беру домен: example.com" && flat | grep -q "Готово: Ленивый Pro" && flat | grep -q "Сайт: https://example.com/" && flat | grep -qE "Веб-панель: https://example.com/app-[A-Za-z0-9_-]{86}/" && flat | grep -q "DRY: admin-web/install_admin.sh --reset-password" && [ "$(grep -cE "^ +https://example.com/p-[A-Za-z0-9_-]{43}/$" "$OUT")" = 2 ]' "rc=$rc $(tail -30 "$OUT")"
check "почему 2.0 на 443 — сказано; бот — «позже» (конец ввода — не согласие)" \
    'flat | grep -q "На UDP 443 — AmneziaWG 2.0" && flat | grep -q "Бота можно привязать позже"' "$(tail -12 "$OUT")"
check "установлено: installed, pro, два ключа; пароль панели выдан — password_pending снят" \
    '[ "$(cfgv .installed)" = true ] && [ "$(cfgv .mode)" = pro ] && [ "$(jq ".users | length" "$GOAMNEZIA_DIR/users.json")" = 2 ] && [ "$(cfgv .panel.password_pending)" = false ]' "$(cfgv .panel)"
before=$(sig)
lazy --domain=example.com --keys=2
check "повторный запуск: rc 0, «уже установлен», ничего не поменял" '[ "$rc" = 0 ] && flat | grep -q "уже установлен" && [ "$(sig)" = "$before" ]' "rc=$rc $(tail -4 "$OUT")"

DONE=1
printf '\nИТОГ: пройдено %s, не прошло %s\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
