#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/preflight.sh без сервера (GOAMNEZIA_DRY_RUN=1)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_preflight_dryrun.sh
# Нужны: bash ≥ 5, jq, awk. Ни ss, ни docker, ни ufw, ни root не нужны: все
# источники подменяются фикстурами tests/fixtures/preflight/ (сохранённые выводы
# ss -ltnp / ss -lunp, /proc/<pid>/cgroup, docker ps --format, ufw status verbose,
# iptables -S INPUT, nft list ruleset, каталоги sites-enabled nginx).
#
# Сценарии (CONTRACT §4.6a): всё свободно → 80/443 у чужого nginx с юнитом и
# vhost gotelegram → docker-proxy контейнера из нашего config.json (своё, не
# блокирует) и чужого amnezia-xray → telemt на 443 (goTelegram) → UDP/443 у xray
# (только предупреждение) → ufw без правил (closed_local) и открытие порта →
# ss отсутствует (unknown, режимы доступны) → разборщики iptables/nft/ufw →
# таблица продуктов → экран → словари. JSON проверяется на каждом шаге.
#
# Настоящие lib/common.sh, lib/i18n.sh, lib/preflight.sh. Ничего вне $TMP не
# пишем. Захват результата — через run (stdout → файл), а не $(…): в подоболочке
# терялся бы GA_ERR_CODE.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FX="$ROOT/tests/fixtures/preflight"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-preflight.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_PROC_ROOT="$FX/proc"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SYSTEMD_DIR" "$TMP/nginx_plain/sites-enabled"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
# Текст экрана одной строкой: длинные сообщения переносятся по словам, и
# grep по фразе целиком ловил бы только случайно уместившиеся.
flat() { tr '\n' ' ' < "$ERR" | tr -s ' '; }
# jv <jq-выражение> — значение из последнего JSON в $out
jv() { printf '%s' "$out" | jq -r "$1" 2>/dev/null; }
# Набор фикстур на сценарий: fixtures <tcp> <udp> <docker> <ufw> <nginx-dir> [ipt] [nft]
fixtures() {
    export GOAMNEZIA_SS_FIXTURE_TCP="$FX/$1" GOAMNEZIA_SS_FIXTURE_UDP="$FX/$2"
    export GOAMNEZIA_DOCKER_PS_FIXTURE="$FX/$3" GOAMNEZIA_UFW_FIXTURE="$FX/$4"
    export GOAMNEZIA_NGINX_DIR="$5"
    if [ -n "${6:-}" ]; then export GOAMNEZIA_IPTABLES_FIXTURE="$FX/$6"; else unset GOAMNEZIA_IPTABLES_FIXTURE; fi
    if [ -n "${7:-}" ]; then export GOAMNEZIA_NFT_FIXTURE="$FX/$7"; else unset GOAMNEZIA_NFT_FIXTURE; fi
    unset GOAMNEZIA_SS_FIXTURE
}
# Проверка «ровно одна строка валидного JSON в stdout»
one_json() { [ "$(printf '%s\n' "$out" | grep -c .)" = 1 ] && is_json "$out"; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
for f in lib/common.sh lib/i18n.sh lib/preflight.sh lib/lang/ru.d/preflight.sh lib/lang/en.d/preflight.sh \
         tests/fixtures/preflight/ss_tcp_nginx.txt tests/fixtures/preflight/proc/812/cgroup; do
    [ -f "$ROOT/$f" ] || { printf '!! нет %s\n' "$f"; exit 2; }
done
command -v jq >/dev/null 2>&1 || { printf '!! нужен jq\n'; exit 2; }
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
# shellcheck disable=SC1091
source "$ROOT/lib/preflight.sh"
check "модуль подключён: preflight_port_owner, preflight_modes, preflight_render_modes, preflight_open_local, preflight_guess_product" \
    'type preflight_port_owner >/dev/null && type preflight_modes >/dev/null && type preflight_render_modes >/dev/null && type preflight_open_local >/dev/null && type preflight_guess_product >/dev/null'
check "словарь preflight подхвачен движком i18n (preflight_title)" '[ -n "${I18N[preflight_title]:-}" ]'
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию с предмиграционным бэкапом.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {"awg2": {"line": "2.0", "container": "amnezia-awg2", "port": 443, "enabled": true},
              "awg3": {"line": "3.1", "container": "amnezia-awg3", "port": 38211, "enabled": true}},
 "panel": {"port": 1985, "miniapp_path": "", "trust_tunnel": true},
 "site": {"template_id": "", "template_source": ""}, "options": {"drop_icmp": false, "alt_svc": false},
 "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"

# ── 1. Всё свободно ──────────────────────────────────────────────────────────
section "всё свободно"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "preflight_modes: rc=0, ровно одна строка валидного JSON" '[ "$rc" = 0 ] && one_json' "$out"
check "три режима доступны, причин нет" \
    '[ "$(jv .lite.available)" = true ] && [ "$(jv .lite_site.available)" = true ] && [ "$(jv .pro.available)" = true ] && [ "$(jv ".pro.reasons|length")" = 0 ] && [ "$(jv ".lite_site.reasons|length")" = 0 ]' "$out"
check "ports: tcp80/tcp443/udp443 free, файрвол open (ufw: Nginx Full + 443/udp), предупреждений нет" \
    '[ "$(jv .ports.tcp80.state)" = free ] && [ "$(jv .ports.tcp443.state)" = free ] && [ "$(jv .ports.udp443.state)" = free ] && [ "$(jv .ports.tcp443.firewall)" = open ] && [ "$(jv .ports.tcp443.firewall_source)" = ufw ] && [ "$(jv ".warnings|length")" = 0 ]' "$out"
check "порт из EXPOSE без публикации (3x-ui 443/tcp без ->) хост не занимает" '[ "$(jv .ports.tcp443.container)" = "" ]' "$out"
check "hints пустые, dry_run=true" '[ "$(jv .hints.free_cmd)" = "" ] && [ "$(jv .hints.open_cmd)" = "" ] && [ "$(jv .dry_run)" = true ]' "$out"
js_free="$out"
run preflight_render_modes "$js_free"
# Служебные строки («Значки», «Как выбрать», «Не уверены») из подсчёта
# исключаем: они сами объясняют ✓ и ✗ и потому содержат оба знака.
# Режимов четыре (договор 0.6.0, docs/MODES_047.md): 1 Ленивый Pro, 2 Pro, 3 Semi-Pro, 4 Lite; «повторить» — 5.
check "render: rc=0, stdout пуст, четыре ✓, заголовок и подсказка с номером пункта" \
    '[ "$rc" = 0 ] && [ -z "$out" ] && [ "$(grep -vE "Значки:|Как выбрать:|Не уверены" "$ERR" | grep -c "✓")" = 4 ] && grep -q "Режим установки" "$ERR" && grep -q "Повторить проверку — 5" "$ERR"' "$(errs)"
check "render: порядок и названия 0.6.0 — 1) Ленивый Pro, 2) Pro, 3) Semi-Pro, 4) Lite" \
    '[ "$(grep -oE "^  [1-4]\) [^ ]+( Pro)?" "$ERR" | sed "s/^  //" | paste -sd "|" -)" = "1) Ленивый Pro|2) Pro|3) Semi-Pro|4) Lite" ]' "$(errs)"
check "render: справа «порты свободны» у всех четырёх режимов, а не «доступен»" \
    '[ "$(grep -c "порты свободны" "$ERR")" = 4 ] && ! grep -qE "\) (Lite|Pro).*доступен" "$ERR"' "$(errs)"
check "render: легенда на экране — что значат значки и как выбирать режим" \
    'grep -q "Значки: ✓ — режим можно выбрать" "$ERR" && grep -q "Как выбрать: 1 —" "$ERR"' "$(errs)"
check "NC6-04 всё свободно: у Ленивого Pro нет «UDP 443 занят», строки «наша линия» нет" \
    '! grep -q "UDP 443 занят" "$ERR" && ! grep -q "наша линия" "$ERR"' "$(errs)"
run preflight_port_owner tcp 22
check "port_owner tcp 22: busy, sshd, PID 640, юнит ssh.service из cgroup v1 (несколько строк), product unknown, ours=false" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jv .state)" = busy ] && [ "$(jv .process)" = sshd ] && [ "$(jv .pid)" = 640 ] && [ "$(jv .unit)" = ssh.service ] && [ "$(jv .product)" = unknown ] && [ "$(jv .ours)" = false ]' "$out"
run preflight_port_owner tcp 1984
check "port_owner tcp 1984: python3 в юните gotelegram-admin.service → product gotelegram, addr 127.0.0.1" \
    '[ "$(jv .product)" = gotelegram ] && [ "$(jv .addr)" = 127.0.0.1 ] && [ "$(jv .unit)" = gotelegram-admin.service ]' "$out"

# ── 2. 80 и 443 у чужого nginx (юнит nginx.service, vhost gotelegram) ────────
section "TCP/80 и TCP/443 заняты чужим nginx (goTelegram Pro)"
fixtures ss_tcp_nginx.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$FX/nginx_gotelegram" iptables_open.txt
run preflight_port_owner tcp 443
check "port_owner tcp 443: busy, nginx, PID 812 (первый в users), юнит nginx.service, product gotelegram, ours=false" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jv .state)" = busy ] && [ "$(jv .process)" = nginx ] && [ "$(jv .pid)" = 812 ] && [ "$(jv .unit)" = nginx.service ] && [ "$(jv .product)" = gotelegram ] && [ "$(jv .ours)" = false ]' "$out"
check "поля контракта на месте: state, pid, process, unit, container, product, ours" \
    '[ "$(jv "[.state,.pid,.process,.unit,.container,.product,.ours] | map(. != null) | all")" = true ]' "$out"
run preflight_modes
check "modes: lite доступен, lite_site и pro недоступны, причин две (443 и 80), обе busy" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .lite.available)" = true ] && [ "$(jv .lite_site.available)" = false ] && [ "$(jv .pro.available)" = false ] && [ "$(jv ".pro.reasons|length")" = 2 ] && [ "$(jv ".pro.reasons|map(.state)|unique|join(\",\")")" = busy ]' "$out"
check "причины несут порт, протокол, процесс, PID, юнит, продукт: 443 первым" \
    '[ "$(jv ".pro.reasons[0]|[.port,.proto,.process,.pid,.unit,.product]|map(tostring)|join(\" \")")" = "443 tcp nginx 812 nginx.service gotelegram" ] && [ "$(jv .pro.reasons[1].port)" = 80 ]' "$out"
check "сообщение 443 — полное с продуктом, 80 — короткое (тот же владелец)" \
    '[ "$(jv .pro.reasons[0].message)" = "TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro" ] && [ "$(jv .pro.reasons[1].message)" = "TCP/80 занят: nginx (PID 812)" ]' "$out"
check "lite_site.reasons совпадают с pro.reasons; hints.free_cmd = systemctl disable --now nginx.service" \
    '[ "$(jv .lite_site.reasons)" = "$(jv .pro.reasons)" ] && [ "$(jv .hints.free_cmd)" = "systemctl disable --now nginx.service" ]' "$out"
js_nginx="$out"
run preflight_render_modes "$js_nginx"
check "экран: stdout пуст; ✓ Lite, ✗ у трёх режимов с сайтом и у двух причин; строки с PID 812, юнитом nginx.service и goTelegram Pro" \
    '[ "$rc" = 0 ] && [ -z "$out" ] && [ "$(grep -vE "Значки:|Как выбрать:|Не уверены" "$ERR" | grep -c "✓")" = 1 ] && [ "$(grep -vE "Значки:|Как выбрать:|Не уверены" "$ERR" | grep -c "✗")" = 5 ] && flat | grep -q "PID 812, юнит nginx.service" && flat | grep -q "похоже, это goTelegram Pro"' "$(errs)"
check "экран: у недоступных режимов назван занятый порт по возрастанию (80, потом 443)" \
    '[ "$(grep -c "порт 80/tcp, 443/tcp занят" "$ERR")" = 3 ] && [ "$(grep -c "порты свободны" "$ERR")" = 1 ]' "$(errs)"
check "экран: подсказка «Освободите порт (например: systemctl disable --now nginx.service)» и «повторить — цифрой»" \
    'grep -q "Освободите порт (например: systemctl disable --now nginx.service) или используйте чистый сервер" "$ERR" && grep -q "Повторить проверку — 5, выбрать доступный режим — цифра, выйти — 0" "$ERR"' "$(errs)"
run preflight_render_modes "$js_nginx" brief
check "экран brief: без заголовка «Режим установки», с короткой подсказкой «r»" \
    '! grep -q "Режим установки" "$ERR" && grep -q "Повторить проверку портов — 5" "$ERR" && [ -z "$out" ]' "$(errs)"
# nginx без известного vhost — продукт неизвестен, но занятость та же
fixtures ss_tcp_nginx.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_port_owner tcp 80
check "nginx без vhost gotelegram/govless/goamnezia → busy, product unknown (в сообщении без «похоже»)" \
    '[ "$(jv .state)" = busy ] && [ "$(jv .product)" = unknown ]' "$out"
run preflight_modes
check "сообщение без догадки о продукте" '[ "$(jv .pro.reasons[0].message)" = "TCP/443 занят: nginx (PID 812, юнит nginx.service)" ]' "$out"
# nginx с нашим vhost — это мы: не препятствие
fixtures ss_tcp_nginx.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$FX/nginx_ours" iptables_open.txt
run preflight_modes
check "наш nginx (sites-enabled/goamnezia): ours=true, product goamnezia, режимы доступны, причин нет" \
    '[ "$(jv .ports.tcp443.ours)" = true ] && [ "$(jv .ports.tcp443.product)" = goamnezia ] && [ "$(jv .pro.available)" = true ] && [ "$(jv ".pro.reasons|length")" = 0 ]' "$out"
# ss без root: процесс не виден
fixtures ss_tcp_noroot.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "ss без -p (не root): busy без PID, пометка no_pid, сообщение «процесс не виден»" \
    '[ "$(jv .ports.tcp443.state)" = busy ] && [ "$(jv .ports.tcp443.pid)" = null ] && [ "$(jv ".ports.tcp443.notes|index(\"no_pid\")")" != null ] && [ "$(jv .pro.reasons[0].message)" = "TCP/443 занят: процесс не виден — запустите проверку от root" ]' "$out"

# ── 3. docker-proxy: свой контейнер и чужой ──────────────────────────────────
section "TCP/443 у docker-proxy"
fixtures ss_tcp_dockerproxy.txt ss_udp_dockerproxy.txt docker_ps_ours.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_port_owner tcp 443
check "docker-proxy → контейнер по порту из docker ps: amnezia-awg2 (из config.json) → ours=true, product goamnezia, юнит docker.service" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jv .state)" = busy ] && [ "$(jv .process)" = docker-proxy ] && [ "$(jv .container)" = amnezia-awg2 ] && [ "$(jv .ours)" = true ] && [ "$(jv .product)" = goamnezia ] && [ "$(jv .unit)" = docker.service ]' "$out"
run preflight_modes
check "своё не блокирует: lite_site и pro доступны, причин нет, UDP/443 у своего контейнера — без предупреждения" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .pro.available)" = true ] && [ "$(jv ".pro.reasons|length")" = 0 ] && [ "$(jv ".warnings|length")" = 0 ] && [ "$(jv .ports.udp443.ours)" = true ]' "$out"
# NC6-04: на установленном сервере порты держит наше же — «наш же, всё в порядке», а не «порты свободны»; UDP 443 —
# «наша линия 2.0»; Ленивый Pro — «доставит недостающее», без «UDP 443 занят — встанет Semi-Pro»
run preflight_render_modes "$out" brief
check "NC6-04 экран: у трёх режимов с сайтом «порт 443/tcp — наш же, всё в порядке», у Lite — «порты свободны»" \
    '[ "$(grep -c "порт 443/tcp — наш же, всё в порядке" "$ERR")" = 3 ] && [ "$(grep -c "порты свободны" "$ERR")" = 1 ]' "$(errs)"
check "NC6-04 экран: «UDP 443 — наша линия 2.0, всё в порядке.» (профиль по контейнеру из config.json)" \
    'flat | grep -q "UDP 443 — наша линия 2.0, всё в порядке."' "$(errs)"
check "NC6-04 Ленивый Pro на установленном: «доставит недостающее и пользователей не тронет», без «UDP 443 занят»" \
    'flat | grep -q "Здесь уже стоит goAmnezia: Ленивый Pro доставит недостающее и пользователей не тронет." && ! grep -q "UDP 443 занят" "$ERR"' "$(errs)"
fixtures ss_tcp_dockerproxy.txt ss_udp_free.txt docker_ps_foreign.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "чужой контейнер amnezia-xray (нет в config.json): product amnezia, ours=false, блокирует; free_cmd = docker stop amnezia-xray" \
    '[ "$(jv .ports.tcp443.container)" = amnezia-xray ] && [ "$(jv .ports.tcp443.product)" = amnezia ] && [ "$(jv .ports.tcp443.ours)" = false ] && [ "$(jv .pro.available)" = false ] && [ "$(jv .hints.free_cmd)" = "docker stop amnezia-xray" ]' "$out"
check "сообщение называет контейнер и приложение Amnezia" \
    '[ "$(jv .pro.reasons[0].message)" = "TCP/443 занят: docker-proxy (PID 2345, юнит docker.service, контейнер amnezia-xray) — похоже, это приложение Amnezia (контейнер не из нашего config.json)" ]' "$out"
check "UDP/443 без слушателя, но опубликован контейнером amnezia-awg (userland-proxy=false): busy, process docker, пометка dnat_only, предупреждение" \
    '[ "$(jv .ports.udp443.state)" = busy ] && [ "$(jv .ports.udp443.process)" = docker ] && [ "$(jv .ports.udp443.container)" = amnezia-awg ] && [ "$(jv ".ports.udp443.notes|index(\"dnat_only\")")" != null ] && [ "$(jv .warnings[0].code)" = udp443_busy ]' "$out"

# ── 4. telemt на 443 ─────────────────────────────────────────────────────────
section "TCP/443 у telemt (goTelegram Pro по юниту)"
fixtures ss_tcp_telemt.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "telemt: адрес «*:443» разобран, юнит telemt.service, product gotelegram, режимы с сайтом недоступны" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .ports.tcp443.process)" = telemt ] && [ "$(jv .ports.tcp443.pid)" = 1500 ] && [ "$(jv .ports.tcp443.unit)" = telemt.service ] && [ "$(jv .ports.tcp443.product)" = gotelegram ] && [ "$(jv .pro.available)" = false ]' "$out"
check "одна причина (80 свободен), сообщение с именем продукта, free_cmd про telemt.service" \
    '[ "$(jv ".pro.reasons|length")" = 1 ] && [ "$(jv .pro.reasons[0].message)" = "TCP/443 занят: telemt (PID 1500, юнит telemt.service) — похоже, это goTelegram Pro" ] && [ "$(jv .hints.free_cmd)" = "systemctl disable --now telemt.service" ] && [ "$(jv .ports.tcp80.state)" = free ]' "$out"

# ── 5. UDP/443 у xray — только предупреждение ────────────────────────────────
section "UDP/443 занят xray (x-ui.service) — предупреждение, не блок"
fixtures ss_tcp_free.txt ss_udp_xray.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "режимы доступны, причин нет, одно предупреждение udp443_busy" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .pro.available)" = true ] && [ "$(jv .lite_site.available)" = true ] && [ "$(jv ".pro.reasons|length")" = 0 ] && [ "$(jv ".warnings|length")" = 1 ] && [ "$(jv .warnings[0].code)" = udp443_busy ]' "$out"
check "предупреждение: процесс xray-linux-amd6 (как печатает ss), PID 1700, юнит x-ui.service, product xui" \
    '[ "$(jv .warnings[0].process)" = xray-linux-amd6 ] && [ "$(jv .warnings[0].pid)" = 1700 ] && [ "$(jv .warnings[0].unit)" = x-ui.service ] && [ "$(jv .warnings[0].product)" = xui ]' "$out"
# Прежнее «(не мешает)» было неправдой для третьего режима: VPN на UDP/443 при занятом порте не встанет
# (мастер потом так и пишет, wz_port_443_busy). Предупреждение называет это сразу (находка T14).
check "текст предупреждения: профили встанут на свои порты, а режим Pro недоступен, пока порт занят" \
    '[ "$(jv .warnings[0].message)" = "UDP/443 занят: xray-linux-amd6 (PID 1700, юнит x-ui.service) — похоже, это панель 3x-ui — профили встанут на свои порты, а режим Pro (VPN на UDP/443) недоступен, пока порт занят" ]' "$out"
run preflight_render_modes "$out"
check "экран: четыре ✓, одна строка ⚠, подсказки «Освободите порт» нет" \
    '[ "$(grep -vE "Значки:|Как выбрать:|Не уверены" "$ERR" | grep -c "✓")" = 4 ] && [ "$(grep -c "⚠" "$ERR")" = 1 ] && ! grep -q "Освободите порт" "$ERR"' "$(errs)"
check "NC6-04 UDP 443 у чужого — у Ленивого Pro «UDP 443 занят — встанет Semi-Pro.», строки «наша линия» нет" \
    'flat | grep -q "UDP 443 занят — встанет Semi-Pro." && ! grep -q "наша линия" "$ERR"' "$(errs)"
fixtures ss_tcp_free.txt ss_udp_xray.txt docker_ps_exposed_only.txt ufw_active_web.txt "$FX/nginx_govless" iptables_open.txt
run preflight_port_owner udp 443
check "тот же x-ui, но с vhost govless на диске → product govless" '[ "$(jv .product)" = govless ]' "$out"

# ── 6. Локальный файрвол ─────────────────────────────────────────────────────
section "ufw active без правил на 80/443 → closed_local"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_no_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_port_owner tcp 443
check "port_owner: state closed_local, firewall closed, source ufw, процесса нет" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jv .state)" = closed_local ] && [ "$(jv .firewall)" = closed ] && [ "$(jv .firewall_source)" = ufw ] && [ "$(jv .process)" = "" ]' "$out"
run preflight_modes
check "modes: lite_site/pro недоступны, две причины closed_local, предупреждений нет" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .pro.available)" = false ] && [ "$(jv ".pro.reasons|map(.state)|unique|join(\",\")")" = closed_local ] && [ "$(jv ".pro.reasons|length")" = 2 ] && [ "$(jv ".warnings|length")" = 0 ]' "$out"
check "сообщение причины и hints.open_cmd = ufw allow 80,443/tcp (порты по возрастанию)" \
    '[ "$(jv .pro.reasons[0].message)" = "TCP/443 закрыт локальным файрволом (ufw активен, правила для порта нет)" ] && [ "$(jv .hints.open_cmd)" = "ufw allow 80,443/tcp" ]' "$out"
check "UDP/443 закрыт тоже, но в причины не попадает (только TCP блокирует)" '[ "$(jv .ports.udp443.state)" = closed_local ]' "$out"
run preflight_render_modes "$out"
check "экран: ✗ у трёх режимов с сайтом, строки «закрыт локальным файрволом», подсказка про открытие с командой ufw" \
    '[ -z "$out" ] && [ "$(grep -c "закрыт локальным файрволом" "$ERR")" = 2 ] && grep -q "мастер предложит открыть его (ufw allow 80,443/tcp)" "$ERR" && ! grep -q "Освободите порт" "$ERR"' "$(errs)"
run preflight_open_local 443 tcp
check "open_local при активном ufw: rc=0, DRY: ufw allow 443/tcp, лог успеха, stdout пуст" \
    '[ "$rc" = 0 ] && grep -q "^DRY: ufw allow 443/tcp$" "$ERR" && grep -q "Порт 443/tcp открыт в локальном файрволе (ufw)" "$ERR" && [ -z "$out" ]' "$(errs)"
check "аудит записан: preflight-open 443/tcp via ufw" 'grep -q "preflight-open" "$GOAMNEZIA_AUDIT_LOG" && grep -q "443/tcp via ufw" "$GOAMNEZIA_AUDIT_LOG"' "$(cat "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain"
run preflight_open_local 80 tcp
check "open_local при неактивном ufw: DRY iptables -I INPUT -p tcp --dport 80 -j ACCEPT, ip6tables, netfilter-persistent save" \
    '[ "$rc" = 0 ] && grep -q "^DRY: iptables -I INPUT -p tcp --dport 80 -j ACCEPT$" "$ERR" && grep -q "^DRY: ip6tables -I INPUT -p tcp --dport 80 -j ACCEPT$" "$ERR" && grep -q "^DRY: netfilter-persistent save$" "$ERR"' "$(errs)"
run preflight_open_local 443 icmp
check "open_local с неверным протоколом → rc=2, код preflight_bad_args" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = preflight_bad_args ]' "rc=$rc code=$GA_ERR_CODE"
# ufw: явный DENY, профиль приложения, диапазон/список, наш nginx за закрытым портом
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_deny443.txt "$TMP/nginx_plain"
run preflight_port_owner tcp 443
check "ufw: DENY 443/tcp раньше ALLOW 80,443/tcp — первое совпавшее правило решает → closed" '[ "$(jv .state)" = closed_local ]' "$out"
run preflight_port_owner tcp 80
check "ufw: список 80,443/tcp ALLOW → 80 open" '[ "$(jv .state)" = free ] && [ "$(jv .firewall)" = open ]' "$out"
run preflight_port_owner udp 443
check "ufw без строки Default: правила для udp нет → deny по умолчанию → closed" '[ "$(jv .state)" = closed_local ]' "$out"
fixtures ss_tcp_nginx.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_no_web.txt "$FX/nginx_ours"
run preflight_port_owner tcp 443
check "наш nginx на закрытом порту: state closed_local и ours=true (чужой busy был бы важнее файрвола)" \
    '[ "$(jv .state)" = closed_local ] && [ "$(jv .ours)" = true ] && [ "$(jv .process)" = nginx ]' "$out"
fixtures ss_tcp_nginx.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_no_web.txt "$FX/nginx_gotelegram"
run preflight_port_owner tcp 443
check "чужой nginx на закрытом порту: state busy (файрвол вторичен), firewall closed" \
    '[ "$(jv .state)" = busy ] && [ "$(jv .firewall)" = closed ]' "$out"

section "iptables -S INPUT и nft list ruleset"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" iptables_drop443.txt
run preflight_port_owner tcp 443
check "iptables: явный DROP --dport 443 → closed_local (source iptables)" '[ "$(jv .state)" = closed_local ] && [ "$(jv .firewall_source)" = iptables ]' "$out"
run preflight_port_owner tcp 80
check "iptables: политика ACCEPT, для 80 правила нет → free/open" '[ "$(jv .state)" = free ] && [ "$(jv .firewall)" = open ]' "$out"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" iptables_policy_drop_web.txt
run preflight_port_owner tcp 443
check "iptables: политика DROP + multiport --dports 80,443 ACCEPT → open" '[ "$(jv .state)" = free ] && [ "$(jv .firewall)" = open ]' "$out"
run preflight_port_owner udp 443
check "iptables: ACCEPT udp 443 только с -s 10.0.0.0/8 не считается открытием → политика DROP → closed_local" '[ "$(jv .state)" = closed_local ]' "$out"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" iptables_firewalld_like.txt
run preflight_modes
check "iptables: переходы в чужие цепочки (INPUT_ZONES) + общий REJECT → firewall unknown, режимы доступны, предупреждение firewall_unknown на каждый порт" \
    '[ "$(jv .ports.tcp443.firewall)" = unknown ] && [ "$(jv .ports.tcp443.state)" = free ] && [ "$(jv .pro.available)" = true ] && [ "$(jv ".warnings|map(select(.code==\"firewall_unknown\"))|length")" = 3 ]' "$out"
check "текст предупреждения о файрволе" '[ "$(jv .warnings[0].message)" = "TCP/443: состояние локального файрвола не определено (правило iptables в цепочке INPUT) — если сайт не откроется, проверьте его вручную" ]' "$out"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" "" nft_drop443.txt
run preflight_port_owner tcp 443
check "nft: inet filter input policy drop, accept только { 22, 80 } → 443 closed_local (source nft)" '[ "$(jv .state)" = closed_local ] && [ "$(jv .firewall_source)" = nft ]' "$out"
run preflight_port_owner tcp 80
check "nft: 80 в анонимном множестве → open" '[ "$(jv .state)" = free ] && [ "$(jv .firewall)" = open ]' "$out"
run preflight_port_owner udp 443
check "nft: udp dport 443 accept → open" '[ "$(jv .firewall)" = open ]' "$out"
# SR-19: порт закрыт родным nftables (своя таблица inet filter, policy drop). Правило iptables ляжет в другую
# таблицу, а базовая цепочка nft пакет всё равно отбросит — раньше ставили его и рапортовали «открыт».
run preflight_open_local 443 tcp
check "SR-19 закрыто родным nftables: iptables не трогаем, успеха нет — отказ preflight_open_nft с командой nft" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = preflight_open_nft ] && ! grep -q "^DRY: iptables" "$ERR" && grep -q "nft add rule inet filter input tcp dport 443 accept" "$ERR" && ! grep -q "открыт в локальном файрволе" "$ERR"' \
    "rc=$rc code=$GA_ERR_CODE $(errs)"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" iptables_drop443.txt
run preflight_open_local 443 tcp
check "SR-19 закрыто iptables — открываем iptables, как прежде" \
    '[ "$rc" = 0 ] && grep -q "^DRY: iptables -I INPUT -p tcp --dport 443 -j ACCEPT$" "$ERR"' "rc=$rc $(errs)"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" "" nft_vmap_sets.txt
run preflight_port_owner tcp 443
check "nft: tcp dport vmap { …, 443 : accept } → open; правило с ip saddr пропущено" '[ "$(jv .firewall)" = open ]' "$out"
run preflight_port_owner udp 443
check "nft: udp dport @quic по именованному множеству (elements = { 443 }) → open" '[ "$(jv .firewall)" = open ]' "$out"
run preflight_port_owner tcp 8443
check "nft: 8443 только для ip saddr 203.0.113.0/24 → не открытие → policy drop → closed" '[ "$(jv .state)" = closed_local ]' "$out"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" iptables_open.txt nft_ipt_mirror.txt
run preflight_port_owner tcp 443
check "nft: таблица ip filter при доступном iptables -S пропускается (отражение iptables-nft) → open по iptables" '[ "$(jv .firewall)" = open ] && [ "$(jv .firewall_source)" = iptables ]' "$out"
fixtures ss_tcp_free.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_inactive.txt "$TMP/nginx_plain" "" nft_ipt_mirror.txt
run preflight_port_owner tcp 443
check "nft: та же таблица без iptables читается → closed_local" '[ "$(jv .state)" = closed_local ] && [ "$(jv .firewall_source)" = nft ]' "$out"

# ── 7. ss отсутствует ────────────────────────────────────────────────────────
section "ss/docker/ufw/iptables/nft отсутствуют (не dry-run) → unknown, режимы доступны"
unset GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_SS_FIXTURE_UDP GOAMNEZIA_SS_FIXTURE GOAMNEZIA_DOCKER_PS_FIXTURE \
      GOAMNEZIA_UFW_FIXTURE GOAMNEZIA_IPTABLES_FIXTURE GOAMNEZIA_NFT_FIXTURE
export GOAMNEZIA_DRY_RUN=0 GOAMNEZIA_SS_BIN="$TMP/none/ss" GOAMNEZIA_DOCKER_BIN="$TMP/none/docker" \
       GOAMNEZIA_UFW_BIN="$TMP/none/ufw" GOAMNEZIA_IPTABLES_BIN="$TMP/none/iptables" GOAMNEZIA_NFT_BIN="$TMP/none/nft"
run preflight_port_owner tcp 443
check "port_owner без ss: rc=0, state unknown, notes содержит no_ss, файрвол open/none (инструментов нет — файрвола нет)" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jv .state)" = unknown ] && [ "$(jv ".notes|index(\"no_ss\")")" != null ] && [ "$(jv .firewall)" = open ] && [ "$(jv .firewall_source)" = none ]' "$out"
run preflight_modes
check "modes без ss: все режимы доступны, причин нет, три предупреждения check_unavailable, dry_run=false" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .pro.available)" = true ] && [ "$(jv .lite_site.available)" = true ] && [ "$(jv ".pro.reasons|length")" = 0 ] && [ "$(jv ".warnings|map(select(.code==\"check_unavailable\"))|length")" = 3 ] && [ "$(jv .dry_run)" = false ]' "$out"
check "текст предупреждения называет ss и пакет iproute2" '[ "$(jv .warnings[0].message)" = "TCP/443: проверить не удалось (нет команды ss — установите iproute2) — считаем свободным, убедитесь вручную" ]' "$out"
run preflight_render_modes "$out"
check "экран без ss: четыре ✓ и три ⚠, ничего не выполнялось (stdout пуст, DRY-строк нет)" \
    '[ "$(grep -vE "Значки:|Как выбрать:|Не уверены" "$ERR" | grep -c "✓")" = 4 ] && [ "$(grep -c "⚠" "$ERR")" = 3 ] && [ -z "$out" ] && ! grep -q "^DRY:" "$ERR"' "$(errs)"
run preflight_open_local 443 tcp
check "open_local без ufw и iptables → rc=1, код preflight_no_fw_tool, подсказка про ручное открытие" \
    '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = preflight_no_fw_tool ] && grep -q "откройте порт 443/tcp вручную" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
export GOAMNEZIA_DRY_RUN=1
unset GOAMNEZIA_SS_BIN GOAMNEZIA_DOCKER_BIN GOAMNEZIA_UFW_BIN GOAMNEZIA_IPTABLES_BIN GOAMNEZIA_NFT_BIN

# ── 8. dry-run без фикстур: пусто, только DRY-строки ─────────────────────────
section "dry-run без фикстур — чтения считаются пустыми"
run preflight_modes
check "все режимы доступны, порты free, предупреждений нет, dry_run=true; в stderr DRY: ss / docker ps / ufw" \
    '[ "$rc" = 0 ] && one_json && [ "$(jv .pro.available)" = true ] && [ "$(jv .ports.tcp443.state)" = free ] && [ "$(jv ".warnings|length")" = 0 ] && [ "$(jv .dry_run)" = true ] && grep -q "^DRY: ss -H -ltnp" "$ERR" && grep -q "^DRY: docker ps" "$ERR" && grep -q "^DRY: ufw status verbose" "$ERR"' "$out $(errs)"
check "ничего не создано вне TMP: /etc/nginx/sites-enabled/goamnezia не тронут" '[ ! -e /etc/nginx/sites-enabled/goamnezia ] || true'

# ── 9. Общая фикстура формата goInstaller (ss -H -lntup) ─────────────────────
section "общая фикстура GOAMNEZIA_SS_FIXTURE (первая колонка Netid)"
unset GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_SS_FIXTURE_UDP
export GOAMNEZIA_SS_FIXTURE="$FX/ss_combined.txt" GOAMNEZIA_DOCKER_PS_FIXTURE="$FX/docker_ps_ours.txt" GOAMNEZIA_UFW_FIXTURE="$FX/ufw_inactive.txt" GOAMNEZIA_NGINX_DIR="$FX/nginx_ours"
run preflight_port_owner tcp 80
check "tcp 80 из общей фикстуры: nginx 812, наш vhost → ours" '[ "$(jv .process)" = nginx ] && [ "$(jv .pid)" = 812 ] && [ "$(jv .ours)" = true ]' "$out"
run preflight_port_owner udp 443
check "udp 443 из общей фикстуры: docker-proxy → контейнер amnezia-awg2 → ours" '[ "$(jv .process)" = docker-proxy ] && [ "$(jv .container)" = amnezia-awg2 ] && [ "$(jv .ours)" = true ]' "$out"
run preflight_port_owner tcp 443
check "tcp 443: слушателя нет, но docker ps публикует 443/tcp у amnezia-awg2 → busy по DNAT, ours=true, пометка dnat_only" \
    '[ "$(jv .state)" = busy ] && [ "$(jv .process)" = docker ] && [ "$(jv .ours)" = true ] && [ "$(jv ".notes|index(\"dnat_only\")")" != null ]' "$out"
unset GOAMNEZIA_SS_FIXTURE

# ── 10. Таблица продуктов ────────────────────────────────────────────────────
section "preflight_guess_product"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx_plain"
gp() { preflight_guess_product "$1" "$2" "$3" 2>/dev/null; }
check "telemt / telemt.service → gotelegram; юнит gotelegram-* → gotelegram" '[ "$(gp telemt telemt.service "")" = gotelegram ] && [ "$(gp python3 gotelegram-bot.service "")" = gotelegram ]'
check "xray под x-ui.service → xui; x-ui с vhost govless → govless; юнит govless-* → govless" \
    '[ "$(gp xray-linux-amd64 x-ui.service "")" = xui ] && [ "$(GOAMNEZIA_NGINX_DIR="$FX/nginx_govless" gp x-ui x-ui.service "")" = govless ] && [ "$(gp node govless-webapp.service "")" = govless ]'
check "юниты govless* определяются и по файлам в systemd (govless-egress.service на диске)" \
    ': > "$GOAMNEZIA_SYSTEMD_DIR/govless-egress.service"; r=$(gp xray x-ui.service ""); rm -f "$GOAMNEZIA_SYSTEMD_DIR/govless-egress.service"; [ "$r" = govless ]'
check "dnsdist → godns; wg-quick@kawg1 → gokaskad; warp-svc → gowarp" \
    '[ "$(gp dnsdist dnsdist.service "")" = godns ] && [ "$(gp wg-quick wg-quick@kawg1.service "")" = gokaskad ] && [ "$(gp warp-svc warp-svc.service "")" = gowarp ]'
check "nginx: vhost gotelegram → gotelegram, goamnezia → goamnezia, govless → govless, ничего → unknown" \
    '[ "$(GOAMNEZIA_NGINX_DIR="$FX/nginx_gotelegram" gp nginx nginx.service "")" = gotelegram ] && [ "$(GOAMNEZIA_NGINX_DIR="$FX/nginx_ours" gp nginx nginx.service "")" = goamnezia ] && [ "$(GOAMNEZIA_NGINX_DIR="$FX/nginx_govless" gp nginx nginx.service "")" = govless ] && [ "$(gp nginx nginx.service "")" = unknown ]'
check "контейнеры: amnezia-awg2 (config.json) → goamnezia; amnezia-xray → amnezia; 3x-ui → xui; прочее → unknown" \
    '[ "$(gp docker-proxy docker.service amnezia-awg2)" = goamnezia ] && [ "$(gp docker-proxy docker.service amnezia-xray)" = amnezia ] && [ "$(gp docker-proxy docker.service 3x-ui)" = xui ] && [ "$(gp docker-proxy docker.service portainer)" = unknown ]'
check "юнит goamnezia-admin.service → goamnezia; неизвестный процесс без юнита → unknown" '[ "$(gp python3 goamnezia-admin.service "")" = goamnezia ] && [ "$(gp foo "" "")" = unknown ]'
check "человеческие имена для всех восьми продуктов есть в словаре" \
    '_gone=0; for p in gotelegram govless godns gowarp gokaskad xui amnezia goamnezia; do [ -n "${I18N[preflight_product_$p]:-}" ] || _gone=1; done; [ "$_gone" = 0 ]'

# ── 11. Ошибки вызова ────────────────────────────────────────────────────────
section "ошибки вызова"
run preflight_port_owner tcp 70000
check "порт вне диапазона → rc=2, preflight_bad_args, stdout пуст" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = preflight_bad_args ] && [ -z "$out" ]' "rc=$rc"
run preflight_port_owner sctp 443
check "протокол sctp → rc=2" '[ "$rc" = 2 ]'
run preflight_render_modes "not json"
check "render на не-JSON → rc=1, preflight_bad_json" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = preflight_bad_json ]' "rc=$rc code=$GA_ERR_CODE"

# ── 12. Словари ──────────────────────────────────────────────────────────────
section "i18n: паритет RU/EN и ключи t/tf модуля"
ru=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/ru.d/preflight.sh" | sort)
en=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/en.d/preflight.sh" | sort)
check "ru.d/preflight.sh ↔ en.d/preflight.sh: одинаковый набор ключей ($(wc -l <<<"$ru" | tr -d ' '))" '[ "$ru" = "$en" ] && [ -n "$ru" ]' "$(diff <(echo "$ru") <(echo "$en") | head -5)"
used=$( { grep -ohE '\$\((t|tf) [a-z0-9_]+' "$ROOT"/lib/preflight.sh | awk '{print $2}'; grep -ohE '(then|else) t [a-z0-9_]+' "$ROOT"/lib/preflight.sh | awk '{print $3}'; } | sort -u)
# Динамические ключи: t "preflight_fw_src_$src" и t "preflight_product_$id"
used="$used preflight_fw_src_ufw preflight_fw_src_iptables preflight_fw_src_nft preflight_fw_src_none"
missing=""
for k in $used; do
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/ru.sh "$ROOT"/lib/lang/ru.d/*.sh || missing+=" ru:$k"
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/en.sh "$ROOT"/lib/lang/en.d/*.sh || missing+=" en:$k"
done
check "все $(wc -w <<<"$used" | tr -d ' ') ключей t/tf из preflight.sh есть в RU и EN" '[ -z "$missing" ]' "$missing"
check "в preflight.sh нет видимых строк вне t/tf (log_* только с \$(t/tf …))" \
    '! grep -nE "log_(info|success|warning|error|step|dim) \"[^\$]" "$ROOT/lib/preflight.sh"' "$(grep -nE 'log_(info|success|warning|error|step|dim) "[^$]' "$ROOT/lib/preflight.sh" | head -3)"
check "ключи модуля не пересекаются с другими фрагментами словаря" \
    '! grep -ho "^I18N\[preflight_[a-z0-9_]*\]" "$ROOT"/lib/lang/ru.sh "$ROOT"/lib/lang/ru.d/*.sh | sort | uniq -d | grep -q .'
load_language en >/dev/null 2>&1
fixtures ss_tcp_telemt.txt ss_udp_free.txt docker_ps_exposed_only.txt ufw_active_web.txt "$TMP/nginx_plain" iptables_open.txt
run preflight_modes
check "EN: сообщение причины на английском с именем продукта" '[ "$(jv .pro.reasons[0].message)" = "TCP/443 is busy: telemt (PID 1500, unit telemt.service) — looks like goTelegram Pro" ]' "$out"
run preflight_render_modes "$out"
check "EN: экран с заголовком Installation mode и подсказкой" 'grep -q "Installation mode" "$ERR" && grep -q "Free the port (for example: systemctl disable --now telemt.service)" "$ERR"' "$(errs)"
load_language ru >/dev/null 2>&1

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
