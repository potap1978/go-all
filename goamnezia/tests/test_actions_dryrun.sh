#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон моста --action= через НАСТОЯЩИЙ install.sh
# без Docker (GOAMNEZIA_DRY_RUN=1).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_actions_dryrun.sh
# Нужны: bash ≥ 5, jq, python3; qrencode — для user-key --format=all (без него
# эти проверки помечаются пропущенными, а не проваленными).
#
# В отличие от tests/test_core_dryrun.sh (функции модулей напрямую) здесь
# каждый шаг — отдельный процесс `install.sh --action=… --json`, то есть ровно
# то, что делают бот, панель и MCP. Проверяем контракт моста (§0.3): последняя
# строка stdout — валидный JSON, кроме неё в stdout ничего нет, status и code
# как ожидается, приватных ключей нет нигде, кроме user-key. Сценарий:
#   status → profile-install awg2:443 → user-add anna → user-list/show без
#   секретов → user-key all (conf, vpn, QR) → profile-set-param Jc (клиентский,
#   reissue_required=false) и S1 (серверный, reissue_required=true, рестарт) →
#   ошибки валидатора и dispatcher'а → profile-subnet 10.9.0.0/24 (Анна получает
#   10.9.0.1, конфиг переписан, контейнер пересоздан) → profile-mtu →
#   endpoint-set vpn.example.com (Endpoint с доменом в новом .conf) и обратно →
#   dns-set → user-disable/enable → бэкап → user-del → profiles-status →
#   Pro-режим (lib/website.sh, стенд как в tests/test_website_dryrun.sh):
#   change-mode pro → status с блоком site → change-template → miniapp-on/rotate/
#   status/off → site-alt-svc → site-check → три режима по кругу на договоре
#   «pro и lite_site — один и тот же сайт на домене, разница только в месте
#   AmneziaWG»: pro → lite_site (certbot не звался, vhost байт-в-байт прежний,
#   tls/ не заводился) → панель, Alt-Svc, шаблон и site-check во втором режиме →
#   lite (site=null, отказы needs_site/not_pro) → lite_site (домен из config.json,
#   Let's Encrypt) → pro → lite_site. Затем запасной путь: домен в config.json
#   стёрт, и lite_site поднимается на самоподписанном по IP — права tls/,
#   CN и SAN на IP, отказ панели с cert=selfsigned, бэкап с tls/ и восстановление
#   на удалённый tls/ с тем же отпечатком (PRO.md §3.6), возвращение домена →
#   Let's Encrypt → предполётная проверка портов (lib/preflight.sh):
#   preflight (в dry-run без фикстур всё свободно) → change-mode lite_site/pro
#   при занятом 443 чужим nginx (фикстуры GOAMNEZIA_SS_FIXTURE_TCP и
#   sites-enabled/gotelegram) → port_busy с полями владельца → ufw без правил →
#   closed_local с командой открытия → наш nginx (ours) → успех → status.ports
#   по профилям и quality.public_ports → lang-set en/ru → status.lang →
#   доступы агентов (lib/agents.sh): mcp-http-on в lite → needs_site, в lite_site
#   → успех с отпечатком, location в vhost и юнит; agent-add → адрес-ключ,
#   agent-list без секрета, agent-show, agent-rotate, agent-disable/enable,
#   agent-del, mcp-http-off.
#
# Ничего вне $TMP не пишем: GOAMNEZIA_DIR, замок, логи, контекст сборки и копия
# lib/upstream — во временном каталоге. IP сервера задан через GOAMNEZIA_SERVER_IP
# (документационный 203.0.113.10), чтобы тест не ходил в сеть.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
# install.sh гоняем тем же интерпретатором, что и тест: на macOS /bin/bash — 3.2,
# и «bash install.sh» там упал бы на первом же declare -A.
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-actions.XXXXXX") || exit 2
DONE=""
OUT="$TMP/stdout.log"
ERR="$TMP/stderr.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export UPSTREAM_DIR="$TMP/upstream"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
# Машина, где идёт прогон, не решает исход и не трогается: на Linux-сервере или раннере CI у неё
# свои docker, nginx и, бывает, установленный продукт. Docker спрашивают только через подставной
# сокет, а DOCKER_HOST (rootless, dind) не уводит вопросы к настоящему демону. Бот, панель и vhost
# nginx по умолчанию — /opt и /etc: бэкап читал бы оттуда токен бота и пароль панели, а
# восстановление писало бы туда. MCP_DIR не трогаем: его путь проверяется в юните (раздел 13).
unset DOCKER_HOST DOCKER_CONTEXT
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin"
export NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
# Есть ли nginx у машины — тем же поиском, что у install.sh (он дописывает к PATH системные каталоги):
# продукт ставит nginx, только когда его нет, — на раннере GitHub он есть.
HOST_NGINX=0
( PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"; command -v nginx ) >/dev/null 2>&1 && HOST_NGINX=1
# host_sig <путь> — след файла машины: «absent» или контрольная сумма. «Живая система не тронута» —
# это след до прогона и после одинаков, а не «файла нет»: на машине с установленным продуктом он есть
# по праву (Linux-сервер), и проверка краснела бы, хотя прогон ничего не писал.
host_sig() { if [ -e "$1" ]; then cksum <"$1" 2>/dev/null || echo unreadable; else echo absent; fi; }
HOST_VHOST_SIG=$(host_sig /etc/nginx/sites-available/goamnezia)
HOST_SITE_SIG=$(host_sig /var/www/goamnezia-site/.goamnezia_template_id)

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
# systemctl машины прогону не нужен (L1-20): восстановление из бэкапа в продукте зовёт его напрямую,
# мимо сухого прогона, — на сервере от root это остановило бы и подняло настоящих бота, панель и MCP и
# перечитало nginx, а без root могло повиснуть на запросе пароля polkit. Подставной systemctl пишет
# вызов в журнал и отвечает отказом — как машина без systemd (macOS). Заглушки-функции в разделах,
# где вызовы проверяются, по-прежнему впереди него.
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexit 1\n' "$TMP/host-systemctl.calls" >"$HOSTBIN/systemctl"
chmod +x "$HOSTBIN/systemctl"
export PATH="$HOSTBIN:$PATH"

pass=0; fail=0; skip=0; rc=0; last=""; nlines=0
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
# check <описание> <условие для eval> [подсказка при провале]
# Условие считаем в подоболочке: часть проверок написана циклом «… || exit 1», и
# без неё такой exit убивал не проверку, а весь прогон — прямо посреди файла,
# молча (перенаправление >/dev/null действует и на trap EXIT). Один красный
# check прятал все разделы после себя; теперь он просто красный.
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }

# act <действие> [--k=v …] → rc, last (последняя строка stdout), nlines (строк в
# stdout), $OUT, $ERR. Каждый вызов — новый процесс install.sh, как у бота.
act() {
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR"; rc=$?
    last=$(tail -n 1 "$OUT")
    nlines=$(wc -l <"$OUT" | tr -d ' ')
    return 0
}
# jget <jq-выражение> — поле из последнего ответа.
jget() { jq -r "$1" <<<"$last" 2>/dev/null; }
errs() { tail -n 8 "$ERR" 2>/dev/null; }
# Ответ моста: ровно одна строка в stdout и это валидный JSON с ожидаемым status.
expect() {
    local what="$1" status="$2" code="${3:-}"
    if [ "$nlines" = 1 ] && is_json "$last" && [ "$(jget .status)" = "$status" ] \
       && { [ -z "$code" ] || [ "$(jget .code)" = "$code" ]; }; then
        ok "$what"
    else
        bad "$what" "rc=$rc lines=$nlines last=${last:0:300} | $(errs | tr '\n' ' ' | head -c 300)"
    fi
}

# Журнал действий — построчный JSON одного формата на весь продукт (CONTRACT
# §7.1). Проверки разбирают его полями, а не ищут табуляции: строка чужого
# формата пропускается, а не роняет разбор целиком.
audit_has() {   # audit_has <действие> [подробности целиком]
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($d == "" or (.details // "") == $d))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}
audit_who() {   # audit_who <действие> → кто сделал последнее такое действие
    jq -R -r -n --arg a "$1" \
        '[inputs | fromjson? // empty | select(.action == $a)] | last | .who // ""' \
        "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null
}

CFG="$GOAMNEZIA_DIR/config.json"
USERS="$GOAMNEZIA_DIR/users.json"
SD2="$GOAMNEZIA_DIR/state/amnezia-awg2"

# Конфиг «после мастера»: режим, IP, DNS записаны, профилей ещё нет. Так мост
# стартует на живом сервере после первой установки.
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию, а она делает предмиграционный бэкап —
# лишний архив ломал проверки списка бэкапов. Тест про саму миграцию — в selftest.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CFG" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}}
EOF
chmod 600 "$CFG"

# ── 0. Окружение ─────────────────────────────────────────────────────────────
section "окружение"
printf '  bash: %s (%s)\n' "$BASH5" "$("$BASH5" -c 'echo $BASH_VERSION')"
"$BASH5" "$INSTALL" --version >"$OUT" 2>"$ERR"; rc=$?
check "install.sh --version → goAmnezia pro <версия>" '[ "$rc" = 0 ] && grep -q "^goAmnezia pro [0-9]" "$OUT"' "$(cat "$OUT" "$ERR")"

# ── 1. status до установки профилей ──────────────────────────────────────────
section "status"
act status
expect "status: success, одна JSON-строка" success
check "status: installed=true, mode=lite, server_ip из конфига, users=0, profiles=[]" \
    '[ "$(jget .installed)" = true ] && [ "$(jget .mode)" = lite ] && [ "$(jget .server_ip)" = 203.0.113.10 ] && [ "$(jget .users)" = 0 ] && [ "$(jget ".profiles|length")" = 0 ]' "$last"
act nope
expect "неизвестное действие → error unknown_action" error unknown_action
check "неизвестное действие: rc=2" '[ "$rc" = 2 ]' "rc=$rc"
act profile-install
expect "profile-install без --profile → missing_arg" error missing_arg
act profile-install --profile=awg9
expect "profile-install --profile=awg9 → bad_arg (enum из actions.json)" error bad_arg

# ── 2. Установка профиля awg2 на UDP/443 ─────────────────────────────────────
section "profile-install awg2 :443 (dry-run)"
act profile-install --profile=awg2 --port=443
expect "profile-install awg2 --port=443: success" success
check "ответ: profile=awg2, result.line=2.0, result.port=443, container amnezia-awg2" \
    '[ "$(jget .profile)" = awg2 ] && [ "$(jget .result.line)" = 2.0 ] && [ "$(jget .result.port)" = 443 ] && [ "$(jget .result.container)" = amnezia-awg2 ]' "$last"
check "stderr: DRY docker pull по дайджесту, build, run с -p 443:443/udp, network connect" \
    'grep -q "DRY: docker pull amneziavpn/amneziawg-go@sha256:" "$ERR" && grep -q "DRY: docker build -t amnezia-awg2" "$ERR" && grep -q -- "-p 443:443/udp" "$ERR" && grep -q "DRY: docker network connect amnezia-dns-net amnezia-awg2" "$ERR"' "$(grep DRY "$ERR" | head -4)"
check "config.json: профиль awg2, подсеть 10.8.2.0/24, порт 443, params с Jc/S1/H1" \
    '[ "$(jq -r .profiles.awg2.subnet "$CFG")" = 10.8.2.0/24 ] && [ "$(jq -r .profiles.awg2.port "$CFG")" = 443 ] && [ -n "$(jq -r .profiles.awg2.params.Jc "$CFG")" ] && [ -n "$(jq -r .profiles.awg2.params.H1 "$CFG")" ]'
check "state/: awg0.conf (ListenPort 443, Address 10.8.2.0/24), ключи сервера, права 700/600" \
    'grep -q "^ListenPort = 443$" "$SD2/awg0.conf" && grep -q "^Address = 10.8.2.0/24$" "$SD2/awg0.conf" && [ -f "$SD2/wireguard_server_private_key.key" ] && [ "$(perm "$SD2")" = 700 ] && [ "$(perm "$SD2/awg0.conf")" = 600 ]'
check "проектный каталог не тронут (контекст сборки только в \$TMP)" '[ -d "$GOAMNEZIA_BUILD_ROOT/amnezia-awg2" ] && [ ! -d "$ROOT/state" ]'
act profile-install --profile=awg2 --port=40000
expect "повторная установка awg2 → код модуля profile_exists (а не общий install_failed)" error profile_exists

# Регрессия стенда: installed=true ставил только мастер, а через мост его нет.
# Сервер оставался installed=false → таймеры продукта (лимиты, сторож,
# статистика, обновления) не вешались вовсе, а меню предлагало первую установку
# на живом сервере.
jq '.installed = false' "$CFG" > "$CFG.tmp" && mv "$CFG.tmp" "$CFG"
TMRDIR="$TMP/systemd-mark"; mkdir -p "$TMRDIR"
act profile-install --profile=awg3 --port=40003 --subnet=10.8.2.0/24
expect "profile-install awg3 в подсеть awg2 → subnet_overlap" error subnet_overlap
check "после отказа awg3 не установлен и таймеры не повешены" \
    '[ -z "$(jq -r ".profiles.awg3.installed_at // empty" "$CFG")" ] && [ ! -f "$TMRDIR/goamnezia-watch.timer" ]' "$(jq -c .profiles.awg3 "$CFG")"
act profile-install --profile=awg3 --port=40003 --subnet=8.8.8.0/24
expect "profile-install с публичной подсетью → bad_arg (шаблон actions.json)" error bad_arg
GOAMNEZIA_SYSTEMD_DIR="$TMRDIR" act profile-install --profile=awg3 --port=40003 --subnet=10.18.3.0/24
expect "profile-install awg3: success" success
check "своя подсеть при установке: config.json awg3 — 10.18.3.0/24" '[ "$(jq -r .profiles.awg3.subnet "$CFG")" = 10.18.3.0/24 ]' "$(jq -c .profiles.awg3 "$CFG")"
check "мост отметил установку состоявшейся (installed=true)" '[ "$(jq -r .installed "$CFG")" = true ]' "installed=$(jq -r .installed "$CFG")"
check "и повесил таймеры продукта (сторож, лимиты, статистика)" \
    '[ -f "$TMRDIR/goamnezia-watch.timer" ] && [ -f "$TMRDIR/goamnezia-quota.timer" ] && [ -f "$TMRDIR/goamnezia-stats.timer" ]' "$(ls "$TMRDIR" 2>/dev/null | tr "\n" " ")"
# Пустой список профилей при ДВУХ установленных — ровно то место, где панель
# отдавала «во все»: человек снял все галочки и получал ключи и в awg2, и в
# awg3, ничего об этом не узнав (DEFECTS §1.4). Пустой аргумент и отсутствующий
# — разные ответы: пустой отвергаем, отсутствующий по-прежнему значит «во все».
act user-add --name=zoe --profiles=
expect "user-add с пустым --profiles= → no_profiles_picked" error no_profiles_picked
check "отказ назвал причину словами, а пользователя не завёл ни в одном профиле" \
    '[ "$rc" = 2 ] && [ -n "$(jget .message)" ] && [ "$(jget .name)" = zoe ] && { [ ! -f "$USERS" ] || [ "$(jq -r ".users|length" "$USERS")" = 0 ]; }' "$last"
# §1d.2 №10: починка пропавшего контейнера. Была во всём продукте, кроме
# кнопки: и сторож, и бот советовали восстановление из бэкапа — самое
# разрушительное действие продукта там, где хватает поднятия из state/.
act profile-repair --profile=awg2
expect "profile-repair awg2: success" success
check "profile-repair поднял контейнер из state/ и вернул в него конфиг и ключи сервера" \
    'grep -q "docker run" "$ERR" && grep -q "awg0.conf" "$ERR"' "$(errs)"
act profile-remove --profile=awg3
expect "awg3 убран (состояние теста не поехало)" success
act profile-repair --profile=awg3
expect "profile-repair снятого профиля → profile_not_installed" error profile_not_installed
act profile-port --profile=awg2 --port=443
expect "profile-port на тот же порт → invalid_port из модуля" error invalid_port
# LC-09: «0443» — тот же порт 443: мост нормализует ввод до проверок и сообщений
act profile-port --profile=awg2 --port=0443
expect "LC-09 profile-port «0443» — тот же 443 → invalid_port, а не новый порт" error invalid_port
act profile-port --profile=awg2 --port=05191
expect "LC-09 profile-port «05191»: success" success
check "LC-09 ответ: port — число 5191, в сообщении 5191 без ведущего нуля" \
    '[ "$(jget .port)" = 5191 ] && [ "$(jget ".port|type")" = number ] && ! grep -q "05191" <<<"$(jget .message)"' "$last"
act profile-port --profile=awg2 --port=443
expect "LC-09 назад на 443: success" success
act profiles-status
expect "profiles-status: success" success
check "profiles-status: awg2, state=missing (dry), port 443, line 2.0, peers 0" \
    '[ "$(jget ".profiles[0].profile")" = awg2 ] && [ "$(jget ".profiles[0].state")" = missing ] && [ "$(jget ".profiles[0].port")" = 443 ] && [ "$(jget ".profiles[0].line")" = 2.0 ] && [ "$(jget ".profiles[0].peers")" = 0 ]' "$last"

# ── 3. Пользователь ──────────────────────────────────────────────────────────
section "user-add / user-list / user-show"
act user-add --name=anna --label=Анна
expect "user-add anna: success" success
check "ответ: карточка без ключей — profiles=[awg2], peers.awg2.ip=10.8.2.1, pubkey есть, privkey/psk нет" \
    '[ "$(jget ".result.profiles|join(\",\")")" = awg2 ] && [ "$(jget .result.peers.awg2.ip)" = 10.8.2.1 ] && [ "$(jget ".result.peers.awg2.pubkey|length")" = 44 ] && ! grep -q "privkey" <<<"$last" && ! grep -q "\"psk\"" <<<"$last"' "$last"
check "users.json: anna с приватным ключом пира awg2, права 0600" \
    '[ "$(jq -r ".users[0].name" "$USERS")" = anna ] && [ "$(jq -r ".users[0].peers.awg2.privkey|length" "$USERS")" = 44 ] && [ "$(perm "$USERS")" = 600 ]'
check "серверный конфиг: [Peer] Анны с AllowedIPs 10.8.2.1/32, DRY syncconf" \
    '[ "$(grep -c "^\[Peer\]" "$SD2/awg0.conf")" = 1 ] && grep -q "^AllowedIPs = 10.8.2.1/32$" "$SD2/awg0.conf" && grep -q "awg syncconf awg0" "$ERR"'
act user-add --name=anna
expect "дубликат → user_exists из модуля" error user_exists
act user-add --name="bad name"
expect "недопустимое имя → bad_arg ещё в диспетчере" error bad_arg
act user-list
expect "user-list: success" success
check "user-list: массив из 1, без privkey/psk, с pubkey" \
    '[ "$(jget ".users|length")" = 1 ] && ! grep -q "privkey" <<<"$last" && ! grep -q "\"psk\"" <<<"$last" && [ "$(jget ".users[0].peers.awg2.pubkey|length")" = 44 ]' "$last"
act user-show --name=anna
expect "user-show anna: success" success
check "user-show: label Анна, без privkey/psk" '[ "$(jget .user.label)" = "Анна" ] && ! grep -q "privkey\|\"psk\"" <<<"$last"' "$last"
act user-show --name=nobody
expect "user-show неизвестного → user_not_found" error user_not_found
# Перевыпуск ключа одним действием. Пара «снять и выдать» жила в трёх
# интерфейсах, и между её шагами человек оставался без доступа, если вызывающий
# обрывался. Проверяем, что ключ сменился, а пир остался на месте.
oldpub=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.pubkey' "$USERS")
oldip=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.ip' "$USERS")
act user-reissue-peer --name=anna --profile=awg2
expect "user-reissue-peer: success" success
check "ключ сменился, адрес и пир остались, ответ называет имя и профиль" \
    '[ "$(jget .name)" = anna ] && [ "$(jget .profile)" = awg2 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.pubkey" "$USERS")" != "$oldpub" ] && [ -n "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS")" ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.ip" "$USERS")" = "$oldip" ]' "$last"
act user-reissue-peer --name=nobody --profile=awg2
expect "user-reissue-peer неизвестному → user_not_found" error user_not_found

# ── 4. Ключи ─────────────────────────────────────────────────────────────────
section "user-key"
act user-key --name=anna --profile=awg2 --format=conf
expect "user-key conf: success" success
conf=$(jget .conf)
check "conf: Endpoint 203.0.113.10:443, Address 10.8.2.1/32, MTU 1280, приватный ключ Анны, PublicKey сервера" \
    'grep -q "^Endpoint = 203.0.113.10:443$" <<<"$conf" && grep -q "^Address = 10.8.2.1/32$" <<<"$conf" && grep -q "^MTU = 1280$" <<<"$conf" && grep -qF "PrivateKey = $(jq -r ".users[0].peers.awg2.privkey" "$USERS")" <<<"$conf" && grep -qF "PublicKey = $(jq -r .profiles.awg2.server_pubkey "$CFG")" <<<"$conf"' "$conf"
check "conf: line 2.0, min_client 4.8.12.9, vpn и QR пустые в формате conf" \
    '[ "$(jget .line)" = 2.0 ] && [ "$(jget .min_client)" = 4.8.12.9 ] && [ "$(jget .vpn)" = "" ] && [ "$(jget ".qr_vpn_png_b64|length")" = 0 ]' "$last"
if command -v qrencode >/dev/null 2>&1; then
    act user-key --name=anna --profile=awg2 --format=all
    expect "user-key all: success" success
    check "all: conf, vpn://, qr_conf_png_b64 (PNG), qr_vpn_png_b64 — по PNG на чанк" \
        '[ -n "$(jget .conf)" ] && [[ "$(jget .vpn)" == vpn://* ]] && [ "$(jget .qr_conf_png_b64 | base64 -d 2>/dev/null | head -c 8 | od -An -tx1 | tr -d " \n")" = 89504e470d0a1a0a ] && [ "$(jget ".qr_vpn_png_b64|length")" -ge 1 ] && [ "$(jget ".qr_vpn_png_b64[0]" | base64 -d 2>/dev/null | head -c 4 | od -An -tx1 | tr -d " \n")" = 89504e47 ]' "$(jq -c "{line, min_client, n: (.qr_vpn_png_b64|length)}" <<<"$last")"
    check "all: vpn-decode даёт тот же контейнер, хост и порт" \
        'dec=$(python3 "$ROOT/lib/awgkeys.py" vpn-decode "$(jget .vpn)") && [ "$(jq -r .container <<<"$dec")" = amnezia-awg2 ] && [ "$(jq -r .hostName <<<"$dec")" = 203.0.113.10 ] && [ "$(jq -r .port <<<"$dec")" = 443 ]'
else
    skipped "user-key all (QR)" "нет qrencode"
fi
act user-key --name=anna --profile=awg3 --format=conf
expect "user-key для неустановленного профиля → profile_not_installed" error profile_not_installed
# SR-03: выдача приватного ключа — событие журнала действий (кто, кому, профиль, формат), без самого ключа.
GOAMNEZIA_WHO="bot:4242" act user-key --name=anna --profile=awg2 --format=vpn
check "SR-03 выдача ключа из бота — в журнале: who=bot:4242, user-key, anna, профиль и формат; ключа там нет" \
    'jq -R -e -n "[inputs | fromjson? // empty | select(.action == \"user-key\" and .who == \"bot:4242\" and .details == \"anna profile=awg2 format=vpn\")] | length == 1" "$GOAMNEZIA_AUDIT_LOG" >/dev/null && ! grep -qF "$(jq -r ".users[0].peers.awg2.privkey" "$USERS")" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(grep user-key "$GOAMNEZIA_AUDIT_LOG" | tail -2)"

# ── 5. Один параметр: клиентский и серверный ─────────────────────────────────
section "profile-set-param"
act profile-params --profile=awg2
expect "profile-params: success" success
jc_old=$(jget .params.Jc); s1_old=$(jget .params.S1)
check "profile-params: Jc и S1 заданы, MTU 1280, S3/S4 есть (линейка 2.0)" \
    '[[ "$jc_old" =~ ^[0-9]+$ ]] && [[ "$s1_old" =~ ^[0-9]+$ ]] && [ "$(jget .params.MTU)" = 1280 ] && [ -n "$(jget .params.S3)" ]' "$last"
act profile-set-param --profile=awg2 --key=Jc --value=6
expect "set-param Jc=6 (клиентский): success" success
check "ответ: reissue_required=false (булево), result.scope=client, old_value=$jc_old, line 2.0" \
    '[ "$(jq -c .reissue_required <<<"$last")" = false ] && [ "$(jget .result.scope)" = client ] && [ "$(jget .result.old_value)" = "$jc_old" ] && [ "$(jget .result.line)" = 2.0 ]' "$last"
check "config.json Jc=6, awg0.conf Jc = 6, syncconf без рестарта контейнера" \
    '[ "$(jq -r .profiles.awg2.params.Jc "$CFG")" = 6 ] && grep -q "^Jc = 6$" "$SD2/awg0.conf" && grep -q "awg syncconf awg0" "$ERR" && ! grep -q "docker restart" "$ERR"' "$(grep DRY "$ERR" | head -4)"
# S1 — 40 или ближайшее большее, при котором размеры пакетов попарно различны (148+S1 ≠ 92+S2, 64+S3, 32+S4): S2…S4
# у профиля случайные, и с S2 = 96 «S1 = 40» честно отклонялось бы валидатором — тест мигал.
s1_new=$(jq -r --argjson p "$(jq -c .profiles.awg2.params "$CFG")" -n '
    [$p.S2, $p.S3, $p.S4] | map(select(. != null) | tonumber) as $o
    | [92, 64, 32] as $b | first(range(40; 200) | select(. as $v | [range(0; $o | length)] | all(148 + $v != $b[.] + $o[.])))')
act profile-set-param --profile=awg2 --key=S1 --value="$s1_new"
expect "set-param S1=$s1_new (серверный): success" success
check "ответ: reissue_required=true, result.scope=server, old_value=$s1_old" \
    '[ "$(jq -c .reissue_required <<<"$last")" = true ] && [ "$(jget .result.scope)" = server ] && [ "$(jget .result.old_value)" = "$s1_old" ]' "$last"
check "awg0.conf S1 = $s1_new, контейнер перезапущен (DRY docker restart), state обновлён" \
    'grep -q "^S1 = $s1_new$" "$SD2/awg0.conf" && grep -q "DRY: docker restart amnezia-awg2" "$ERR"' "$(grep DRY "$ERR" | head -4)"
act user-key --name=anna --profile=awg2 --format=conf
check "новый .conf уже с Jc = 6 и S1 = $s1_new, ключ Анны прежний" \
    'grep -q "^Jc = 6$" <<<"$(jget .conf)" && grep -q "^S1 = $s1_new$" <<<"$(jget .conf)" && grep -qF "$(jq -r ".users[0].peers.awg2.privkey" "$USERS")" <<<"$(jget .conf)"'
act profile-set-param --profile=awg2 --key=Jmin --value=2000
expect "Jmin=2000 → params_invalid (Jmin < Jmax) с причиной валидатора в message" error params_invalid
check "причина валидатора в message" 'grep -q "Jmin" <<<"$(jget .message)"' "$last"
check "неудачная попытка не тронула config.json (Jmin прежний)" '[ "$(jq -r .profiles.awg2.params.Jmin "$CFG")" != 2000 ]'
act profile-set-param --profile=awg2 --key=HeaderProtectionKey --value=on
expect "3.x-поле в awg2 → params_invalid" error params_invalid
act profile-set-param --profile=awg2 --key=Foo --value=1
expect "неизвестный ключ → bad_arg (enum param_key)" error bad_arg
act profile-set-param --profile=awg2 --key=I2 "--value=<b 0x0a0b><r 4>"
expect "I2=<b 0x0a0b><r 4> (сигнатурный пакет с пробелом, <b> первым — KEENETIC-I1): success" success
check "awg0.conf: I2 закомментирован как у приложения (# I2 = <b 0x0a0b><r 4>)" 'grep -q "^# I2 = <b 0x0a0b><r 4>$" "$SD2/awg0.conf"'
act profile-set-param --profile=awg2 --key=I2 --value=
expect "I2 с пустым --value= — убрать параметр (пустое ≠ отсутствующее): success" success
check "config.json: I2 пуст, в awg0.conf строки I2 нет" '[ "$(jq -r .profiles.awg2.params.I2 "$CFG")" = "" ] && ! grep -q "I2 = " "$SD2/awg0.conf"'
act profile-set-param --profile=awg2 --key=Jc
expect "без --value вовсе → missing_arg" error missing_arg

# ── 6. Подсеть ───────────────────────────────────────────────────────────────
section "profile-subnet"
puba=$(jq -r '.users[0].peers.awg2.pubkey' "$USERS")
# goWARP PRO дописывает свой запуск в start.sh контейнера: смена подсети обязана сохранить эти строки и
# поменять подсеть и в них, а не пересобрать файл из шаблона.
printf '# goWARP PRO\nwarp-up --subnet 10.8.2.0/24\n' >>"$SD2/start.sh"
# TEST-FLAKE-01: в awg0.conf — цифры, похожие на старую подсеть, при верных адресах. Случайные H1–H4 профиля давали их
# сами примерно в одном прогоне из 170 («H1 = 100802000-…»), и «старой подсети не осталось» краснело при верных
# файлах; здесь они есть всегда — строкой «# I2 = <b 0x100802>» (I2 — клиентский, в конфиге сервера комментарием), и
# проверка ниже обязана отличать адрес от цифр. В конце раздела I2 снова пуст.
act profile-set-param --profile=awg2 --key=I2 "--value=<b 0x100802>"
expect "I2=<b 0x100802> — цифры «100802» в awg0.conf до смены подсети: success" success
act profile-subnet --profile=awg2 --subnet=10.9.0.0/24
expect "profile-subnet 10.9.0.0/24: success" success
check "start.sh: строки goWARP сохранены, подсеть в них новая, старой подсети в файле не осталось" \
    'grep -q "^# goWARP PRO$" "$SD2/start.sh" && grep -q "^warp-up --subnet 10.9.0.0/24$" "$SD2/start.sh" && ! grep -qF "10.8.2.0/24" "$SD2/start.sh"' "$(tail -4 "$SD2/start.sh")"
check "ответ: old_subnet 10.8.2.0/24, renumbered=1, peers[0]: anna 10.8.2.1 → 10.9.0.1" \
    '[ "$(jget .result.old_subnet)" = 10.8.2.0/24 ] && [ "$(jget .result.renumbered)" = 1 ] && [ "$(jget ".result.peers[0].name")" = anna ] && [ "$(jget ".result.peers[0].old_ip")" = 10.8.2.1 ] && [ "$(jget ".result.peers[0].ip")" = 10.9.0.1 ]' "$last"
check "users.json: пир Анны 10.9.0.1, ключи не менялись" \
    '[ "$(jq -r ".users[0].peers.awg2.ip" "$USERS")" = 10.9.0.1 ] && [ "$(jq -r ".users[0].peers.awg2.pubkey" "$USERS")" = "$puba" ]'
# Адресов старой подсети не осталось — строкой «10.8.2.» (grep -F: точка — это точка). Регулярка «10.8.2» (точка —
# любой знак) совпадала со случайными H1–H4 профиля: «H1 = 100802000-…» — это «10», «0», «8», «0», «2», и проверка
# краснела примерно в одном прогоне из 170 при верных config.json и awg0.conf (TEST-FLAKE-01; нагрузка ни при чём —
# мост отвечает после всех записей). Точки в awg0.conf бывают только в адресах: H — цифры и «-», ключи — base64.
check "config.json subnet 10.9.0.0/24; awg0.conf: Address 10.9.0.0/24, AllowedIPs 10.9.0.1/32" \
    '[ "$(jq -r .profiles.awg2.subnet "$CFG")" = 10.9.0.0/24 ] && grep -q "^Address = 10.9.0.0/24$" "$SD2/awg0.conf" && grep -q "^AllowedIPs = 10.9.0.1/32$" "$SD2/awg0.conf" && ! grep -qF "10.8.2." "$SD2/awg0.conf"' \
    "subnet=$(jq -r .profiles.awg2.subnet "$CFG") | $(grep -nE "^(Address|AllowedIPs) |10\.8\.2\." "$SD2/awg0.conf" | tr "\n" " ")"
check "start.sh с новой подсетью; контейнер пересоздан из state (DRY stop/run/cp ключа)" \
    'grep -q "10.9.0.0/24" "$SD2/start.sh" && grep -q "DRY: docker stop amnezia-awg2" "$ERR" && grep -q "DRY: docker run .* -p 443:443/udp" "$ERR" && grep -q "docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/wireguard_server_private_key.key" "$ERR"' "$(grep DRY "$ERR" | head -6)"
act user-key --name=anna --profile=awg2 --format=conf
check "новый .conf: Address 10.9.0.1/32, тот же приватный ключ" \
    'grep -q "^Address = 10.9.0.1/32$" <<<"$(jget .conf)" && grep -qF "$(jq -r ".users[0].peers.awg2.privkey" "$USERS")" <<<"$(jget .conf)"'
act profile-subnet --profile=awg2 --subnet=10.9.0.0/24
expect "та же подсеть → invalid_subnet" error invalid_subnet
act profile-subnet --profile=awg2 --subnet=172.29.172.0/24
expect "сеть amnezia-dns-net → subnet_overlap" error subnet_overlap
act profile-subnet --profile=awg2 --subnet=10.8.2.0/22
expect "невыровненный адрес /22 → invalid_subnet" error invalid_subnet
act profile-subnet --profile=awg2 --subnet=8.8.8.0/24
expect "публичный диапазон → bad_arg в диспетчере" error bad_arg
act user-add --name=bob --label=Bob
check "новый пользователь после смены подсети получает 10.9.0.2" '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg2.ip" "$USERS")" = 10.9.0.2 ]' "$last"
act profile-set-param --profile=awg2 --key=I2 --value=
check "I2 снова пуст (как до раздела): в awg0.conf строки I2 нет" \
    '[ "$(jget .status)" = success ] && [ "$(jq -r .profiles.awg2.params.I2 "$CFG")" = "" ] && ! grep -q "I2 = " "$SD2/awg0.conf"' "$last"

# ── 7. MTU и Endpoint ────────────────────────────────────────────────────────
section "profile-mtu / endpoint-set / dns-set"
act profile-mtu --profile=awg2 --mtu=1376
expect "profile-mtu 1376: success" success
# С 29.09.2026 MTU профиля — и строкой в конфиге сервера (решение владельца: настройка и awg0.conf не расходятся).
check "config.json params.MTU=1376 строкой; в awg0.conf строка MTU = 1376, одна" \
    '[ "$(jq -r .profiles.awg2.params.MTU "$CFG")" = 1376 ] && [ "$(jq -r ".profiles.awg2.params.MTU|type" "$CFG")" = string ] && grep -q "^MTU = 1376$" "$SD2/awg0.conf" && [ "$(grep -c "^MTU" "$SD2/awg0.conf")" = 1 ]'
act profile-mtu --profile=awg2 --mtu=9000
expect "MTU 9000 → bad_arg (границы из actions.json)" error bad_arg
act endpoint-set --host=vpn.example.com
expect "endpoint-set vpn.example.com: success" success
check "config.json endpoint_host=vpn.example.com" '[ "$(jq -r .endpoint_host "$CFG")" = vpn.example.com ]'
act dns-set --dns1=9.9.9.9 --dns2=149.112.112.112
expect "dns-set: success" success
act user-key --name=anna --profile=awg2 --format=conf
check "новый .conf: Endpoint vpn.example.com:443, MTU 1376, DNS 9.9.9.9" \
    'grep -q "^Endpoint = vpn.example.com:443$" <<<"$(jget .conf)" && grep -q "^MTU = 1376$" <<<"$(jget .conf)" && grep -q "^DNS = 9.9.9.9, 149.112.112.112$" <<<"$(jget .conf)"' "$(jget .conf)"
# ── dns-set идёт через модуль, а не собирает JSON руками (§1c.2) ────────────
# Раньше ветка собирала JSON сама: адрес не проверялся по значению октетов,
# аудит не писался вовсе, а панель и бот потом показывали адрес, которого у
# клиентов нет — последний рубеж выдачи мусор молча отбрасывает.
act dns-set --dns1=999.999.999.999
check "dns-set с несуществующим адресом: отказ, а не «успешно»" \
    '[ "$(jget .status)" = error ]' "$last"
check "config.json не тронут — в нём прежняя пара" \
    '[ "$(jq -r ".client_dns[0]" "$CFG")" = 9.9.9.9 ]' "$(jq -c .client_dns "$CFG")"
act dns-set --dns1=1.1.1.1 --dns2=1.0.0.1
expect "dns-set с верной парой: success" success
check "аудит записан (раньше это была единственная мутация без записи)" \
    'grep -q "dns-set" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -2 "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)"
# SR-48: один адрес — «DNS клиентов: 9.9.9.9.», а не «…, .» с пустым вторым; в stderr модуля — без «, -».
act dns-set --dns1=9.9.9.9
check "SR-48 dns-set с одним адресом: сообщение без пустого второго, client_dns из одного адреса" \
    '[ "$(jget .status)" = success ] && [ "$(jget .message)" = "DNS клиентов: 9.9.9.9." ] && [ "$(jget ".client_dns|length")" = 1 ] && ! grep -q "9.9.9.9, -" "$ERR"' "$last $(errs)"
act dns-set --dns1=9.9.9.9 --dns2=149.112.112.112

act endpoint-set --host=
expect "endpoint-set с пустым --host= — вернуть IP сервера: success" success
check "config.json endpoint_host пуст, в ответе effective_host = IP сервера" '[ "$(jq -r .endpoint_host "$CFG")" = "" ] && [ "$(jget .effective_host)" = 203.0.113.10 ]' "$last"
act user-key --name=anna --profile=awg2 --format=conf
check ".conf снова с Endpoint 203.0.113.10:443" 'grep -q "^Endpoint = 203.0.113.10:443$" <<<"$(jget .conf)"'
act endpoint-set --host=Bad_Host
expect "недопустимый хост → bad_arg" error bad_arg
act status
check "status: endpoint_host пуст, users=2, profiles[0].peers=2" \
    '[ "$(jget .endpoint_host)" = "" ] && [ "$(jget .users)" = 2 ] && [ "$(jget ".profiles[0].peers")" = 2 ]' "$last"
check "status в Lite: endpoint_effective и endpoint_auto — IP сервера" \
    '[ "$(jget .endpoint_effective)" = 203.0.113.10 ] && [ "$(jget .endpoint_auto)" = 203.0.113.10 ]' "$last"
# Домен сервера по умолчанию: в режиме Pro — в ключах домен, и status называет его
# (владелец 17.09.2026: файл по ссылке приходил с IP на сервере «VPN на 443 под доменом»).
cfg_before_domain=$(cat "$CFG")
jq '.mode = "pro" | .domain = "vpn.example.com" | .site = ((.site // {}) + {"cert": "letsencrypt"})' "$CFG" >"$TMP/cfg.tmp" && mv "$TMP/cfg.tmp" "$CFG" && chmod 600 "$CFG"
act status
check "status pro с доменом: endpoint_effective и endpoint_auto — домен, endpoint_host пуст" \
    '[ "$(jget .endpoint_effective)" = vpn.example.com ] && [ "$(jget .endpoint_auto)" = vpn.example.com ] && [ "$(jget .endpoint_host)" = "" ]' "$last"
act user-key --name=anna --profile=awg2 --format=conf
check ".conf в Pro: Endpoint vpn.example.com:443" 'grep -q "^Endpoint = vpn.example.com:443$" <<<"$(jget .conf)"' "$(jget .conf)"
act endpoint-set --host=203.0.113.10
check "IP вписан руками: effective_host — IP" '[ "$(jget .effective_host)" = 203.0.113.10 ]' "$last"
act user-key --name=anna --profile=awg2 --format=conf
check ".conf с вписанным IP: Endpoint 203.0.113.10:443" 'grep -q "^Endpoint = 203.0.113.10:443$" <<<"$(jget .conf)"' "$(jget .conf)"
act endpoint-set --host=
check "пустой host в Pro: effective_host — домен" '[ "$(jget .effective_host)" = vpn.example.com ]' "$last"
printf '%s\n' "$cfg_before_domain" >"$CFG" && chmod 600 "$CFG"
# SR-44: бот, панель и агент получают ту же проверку, что SSH, — ответом: чужой IP, домен без A-записи сюда,
# свои адреса людей, которые вписанный домен заменит. Значение пишется (успех с предупреждением).
act endpoint-set --host=192.0.2.200
check "SR-44 чужой IP: success, warning «не адрес этого сервера»" \
    '[ "$(jget .status)" = success ] && [ -n "$(jget .warning)" ] && grep -qF "192.0.2.200" <<<"$(jget .warning)"' "$last"
jq '.users |= map(if .name == "anna" then .endpoint_ip = "203.0.113.10" else . end)' "$USERS" >"$TMP/u44.json" && cat "$TMP/u44.json" >"$USERS"
GOAMNEZIA_DNS_STUB="198.51.100.77" act endpoint-set --host=vpn.example.org
check "SR-44 домен с A-записью не сюда и свой адрес у anna: warning называет и DNS, и anna" \
    '[ "$(jget .status)" = success ] && grep -qF "vpn.example.org" <<<"$(jget .warning)" && grep -qF "anna" <<<"$(jget .warning)"' "$last"
jq '.users |= map(del(.endpoint_ip))' "$USERS" >"$TMP/u44.json" && cat "$TMP/u44.json" >"$USERS"
act endpoint-set --host=
check "SR-44 пустой host (по умолчанию) — без предупреждения" '[ "$(jget .status)" = success ] && [ -z "$(jget .warning)" ]' "$last"
# SR-47: пустое значение обязательного выбора из списка — отказ, а не «по умолчанию» (снимало ночные копии).
act backup-schedule --frequency=
expect "SR-47 backup-schedule --frequency= (пусто) → bad_arg, расписание не тронуто" error bad_arg
# BOT-8 (сторона моста): ведущие нули у целых — не восьмеричное. «010» ГБ — это 10, а не 8; в ответе число.
act user-quota --name=anna --quota_gb=010
check "BOT-8 user-quota «010»: лимит 10 ГБ (не 8), в ответе quota_gb — число 10" \
    '[ "$(jget .status)" = success ] && [ "$(jget .quota_gb)" = 10 ] && [ "$(jget ".quota_gb|type")" = number ] && [ "$(jq -r ".users[] | select(.name==\"anna\") | .quota_bytes" "$USERS")" = 10737418240 ]' "$last"
act user-quota --name=anna --quota_gb=0000000000000000000001
expect "BOT-8 число длиннее 18 цифр → bad_arg, а не переполнение арифметики" error bad_arg
act user-quota --name=anna --quota_gb=0

# ── 7а. Свой адрес сервера у пользователя (docs/EDGEIPS.md §3.1) ─────────────
# У машины два внешних адреса — подставной ip в PATH: на macOS его нет вовсе, а на Linux настоящий
# назвал бы адреса машины, на которой идёт прогон.
section "user-set-address"
ADDRBIN="$TMP/addrbin"; mkdir -p "$ADDRBIN"
cat >"$ADDRBIN/ip" <<'EOF'
#!/bin/bash
cat "$(dirname "$0")/addrs" 2>/dev/null
EOF
chmod +x "$ADDRBIN/ip"
addrs_on() { : >"$ADDRBIN/addrs"; local a; for a in "$@"; do printf '2: eth0    inet %s/32 scope global eth0\n' "$a" >>"$ADDRBIN/addrs"; done; }
anna_ip() { jq -r '.users[]|select(.name=="anna")|if has("endpoint_ip") then .endpoint_ip else "<нет>" end' "$USERS"; }
addrs_on 203.0.113.10 198.51.100.20
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna
expect "без --ip вовсе → missing_arg (пустой и отсутствующий — разные вещи)" error missing_arg
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=198.51.100.020
expect "не IPv4 по значению (ведущий ноль) → bad_arg по шаблону actions.json" error bad_arg
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=192.0.2.55
expect "адреса нет на машине → address_not_here" error address_not_here
check "после отказа поля endpoint_ip у anna нет" '[ "$(anna_ip)" = "<нет>" ]' "$(anna_ip)"
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=0.0.0.0
expect "0.0.0.0 → invalid_ip (форму проходит, адресом сервера не бывает)" error invalid_ip
addrs_on 10.0.0.5
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=203.0.113.77
expect "машина видит только частный адрес (облако за NAT) → addresses_unknown, а не любой адрес в ключ" error addresses_unknown
check "после addresses_unknown поля у anna нет" '[ "$(anna_ip)" = "<нет>" ]' "$(anna_ip)"
addrs_on 203.0.113.10 198.51.100.20
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=198.51.100.20
expect "user-set-address anna 198.51.100.20: success" success
check "ответ: endpoint_ip и endpoint — новый адрес, previous пуст, resend_required=true, domain_endpoint=false" \
    '[ "$(jget .endpoint_ip)" = 198.51.100.20 ] && [ "$(jget .endpoint)" = 198.51.100.20 ] && [ "$(jget .previous_endpoint_ip)" = "" ] && [ "$(jget .resend_required)" = true ] && [ "$(jget .domain_endpoint)" = false ]' "$last"
check "сообщение велит выдать ключ заново" 'grep -q "выдайте ключ заново" <<<"$(jget .message)"' "$(jget .message)"
check "users.json: endpoint_ip=198.51.100.20; аудит user-set-address с адресом" \
    '[ "$(anna_ip)" = 198.51.100.20 ] && audit_has user-set-address "anna ip=198.51.100.20"' "$(anna_ip) $(tail -1 "$GOAMNEZIA_AUDIT_LOG")"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf
check ".conf anna: Endpoint 198.51.100.20:443, приватный ключ прежний" \
    'grep -q "^Endpoint = 198.51.100.20:443$" <<<"$(jget .conf)" && grep -qF "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS")" <<<"$(jget .conf)"' "$(jget .conf | grep Endpoint)"
act user-show --name=anna
check "user-show: endpoint_ip в публичной карточке, приватных ключей нет" \
    '[ "$(jget .user.endpoint_ip)" = 198.51.100.20 ] && ! grep -q privkey <<<"$last"' "$last"
act user-list
check "user-list: у bob endpoint_ip есть и пуст (общий адрес)" \
    '[ "$(jget ".users[]|select(.name==\"bob\")|has(\"endpoint_ip\")")" = true ] && [ "$(jget ".users[]|select(.name==\"bob\")|.endpoint_ip")" = "" ]' "$last"
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=198.51.100.20
expect "тот же адрес ещё раз: success" success
check "тот же адрес: resend_required=false — ключ не изменился, отправлять нечего" \
    '[ "$(jget .resend_required)" = false ] && [ "$(jget .previous_endpoint_ip)" = 198.51.100.20 ] && grep -q "не нужно" <<<"$(jget .message)"' "$(jget .message)"
act endpoint-set --host=vpn.example.com
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=203.0.113.10
expect "при доменном Endpoint: success" success
check "домен: endpoint=vpn.example.com, domain_endpoint=true, resend_required=false, адрес записан" \
    '[ "$(jget .endpoint)" = vpn.example.com ] && [ "$(jget .domain_endpoint)" = true ] && [ "$(jget .resend_required)" = false ] && [ "$(anna_ip)" = 203.0.113.10 ]' "$last"
act endpoint-set --host=
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=198.51.100.20
addrs_on 203.0.113.10
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf
expect "адрес пропал с машины → user-key отвечает address_gone, а не ключом, который не соединится" error address_gone
check "отказ называет пропавший адрес" 'grep -qF 198.51.100.20 <<<"$(jget .message)"' "$(jget .message)"
addrs_on 203.0.113.10 198.51.100.20
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=
expect "пустой --ip= — вернуть общий адрес: success" success
check "общий: endpoint_ip пуст, endpoint=203.0.113.10, previous=198.51.100.20, resend_required=true, поля в users.json нет" \
    '[ "$(jget .endpoint_ip)" = "" ] && [ "$(jget .endpoint)" = 203.0.113.10 ] && [ "$(jget .previous_endpoint_ip)" = 198.51.100.20 ] && [ "$(jget .resend_required)" = true ] && [ "$(anna_ip)" = "<нет>" ]' "$last"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf
check ".conf anna снова с общим Endpoint 203.0.113.10:443" 'grep -q "^Endpoint = 203.0.113.10:443$" <<<"$(jget .conf)"' "$(jget .conf | grep Endpoint)"
# SR-49: свой адрес, равный общему, — «как у всех»: пусто, а не застывшая копия (сменят общий — у человека старый).
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=203.0.113.10
check "SR-49 свой адрес = общему (203.0.113.10): записано пусто, человек идёт за общим адресом" \
    '[ "$(jget .status)" = success ] && [ "$(jget .endpoint_ip)" = "" ] && [ "$(anna_ip)" = "<нет>" ]' "$last"
act user-set-address --name=nobody --ip=
expect "неизвестный пользователь → user_not_found" error user_not_found
PATH="$ADDRBIN:$PATH" act edge-status
expect "edge-status: success (чтение, два адреса у машины)" success
check "edge-status: count=2, строка на каждый адрес с вердиктом, пользователями и пакетами" \
    '[ "$(jget .count)" = 2 ] && [ "$(jget "[.ips[].ip] | sort | join(\",\")")" = "198.51.100.20,203.0.113.10" ] && [ "$(jget ".ips[0] | has(\"verdict\") and has(\"packets_15m\") and has(\"users\")")" = true ]' "$last"

# ── 7б. Ключ на другой адрес сервера (разовый Endpoint, docs/EDGEIPS.md §3.4) ─
# Тот же ключ (пара, порт профиля) с другим адресом машины внутри; за пользователем ничего не пишется.
section "user-key --endpoint (ключ на другой адрес)"
check "edge-status: keys_host=203.0.113.10, key_choices — оба адреса, «как обычно» первым" \
    '[ "$(jget .keys_host)" = 203.0.113.10 ] && [ "$(jget "[.key_choices[].host] | join(\",\")")" = "203.0.113.10,198.51.100.20" ] && [ "$(jget ".key_choices[0].default")" = true ] && [ "$(jget ".key_choices[1] | .kind + \"/\" + (.default|tostring)")" = "ip/false" ]' "$last"
# sha256sum (Linux) или shasum (macOS): shasum идёт с perl, и на минимальном Linux его нет — тогда
# пустая сумма «до» совпадала с пустой «после», и проверка ниже проходила вхолостую.
users_sum() { { sha256sum "$USERS" 2>/dev/null || shasum -a 256 "$USERS"; } | cut -d" " -f1; }
us_before=$(users_sum)
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf
base_conf=$(jget .conf)
check "без --endpoint: key_host=203.0.113.10, key_endpoint=203.0.113.10:443, key_host_override=false" \
    '[ "$(jget .key_host)" = 203.0.113.10 ] && [ "$(jget .key_endpoint)" = 203.0.113.10:443 ] && [ "$(jget .key_host_override)" = false ]' "$last"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=198.51.100.20
expect "user-key --endpoint=198.51.100.20: success" success
check "разовый адрес: Endpoint 198.51.100.20:443, key_host/key_endpoint/override" \
    'grep -q "^Endpoint = 198.51.100.20:443$" <<<"$(jget .conf)" && [ "$(jget .key_host)" = 198.51.100.20 ] && [ "$(jget .key_endpoint)" = 198.51.100.20:443 ] && [ "$(jget .key_host_override)" = true ]' "$last"
check "кроме строки Endpoint .conf байт в байт прежний (та же пара ключей, порт, параметры)" \
    '[ "$(grep -v "^Endpoint" <<<"$(jget .conf)")" = "$(grep -v "^Endpoint" <<<"$base_conf")" ]'
check "users.json не изменился (за пользователем ничего не сохраняется)" '[ -n "$us_before" ] && [ "$(users_sum)" = "$us_before" ]'
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=
check "пустой --endpoint= — выдача как обычно, .conf байт в байт" '[ "$(jget .conf)" = "$base_conf" ] && [ "$(jget .key_host_override)" = false ]' "$last"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=192.0.2.55
expect "чужой IPv4 → address_not_here" error address_not_here
check "отказ называет, какие адреса можно" 'grep -qF 203.0.113.10 <<<"$(jget .message)" && grep -qF 198.51.100.20 <<<"$(jget .message)"' "$(jget .message)"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=0.0.0.0
expect "0.0.0.0 → invalid_ip" error invalid_ip
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=evil.example.org
expect "домен, который серверу не настроен → address_not_here (произвольный хост в ключ не пускаем)" error address_not_here
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint='1.2.3.4;id'
expect "мусор в адресе → bad_arg по шаблону actions.json" error bad_arg
if command -v qrencode >/dev/null 2>&1; then
    PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=vpn --endpoint=198.51.100.20
    vdec=$(python3 "$ROOT/lib/awgkeys.py" vpn-decode "$(jget .vpn)" 2>/dev/null)
    check "vpn:// с разовым адресом: hostName и подпись с адресом" \
        '[ "$(jq -r ".hostName" <<<"$vdec")" = 198.51.100.20 ] && jq -r ".description" <<<"$vdec" | grep -qF 198.51.100.20' "$(jq -c "{hostName, description}" <<<"$vdec" 2>/dev/null)"
else
    skipped "vpn:// с разовым адресом" "нет qrencode"
fi
act user-show --name=anna
check "user-show: key_host=203.0.113.10, key_host_gone=false" '[ "$(jget .key_host)" = 203.0.113.10 ] && [ "$(jget .key_host_gone)" = false ]' "$last"
PATH="$ADDRBIN:$PATH" act status
check "status: key_hosts — оба адреса машины" '[ "$(jget "(.key_hosts | index(\"198.51.100.20\")) != null and (.key_hosts | index(\"203.0.113.10\")) != null")" = true ]' "$last"
# Закреплённый адрес пропал — обычная выдача отказывает, разовая на живой адрес выручает.
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=198.51.100.20
addrs_on 203.0.113.10
PATH="$ADDRBIN:$PATH" act user-show --name=anna
check "user-show: закреплённый адрес пропал → key_host_gone=true" '[ "$(jget .key_host_gone)" = true ]' "$last"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=203.0.113.10
expect "закреплённый адрес пропал, разовый живой → success" success
check "ключ на живой адрес 203.0.113.10:443" 'grep -q "^Endpoint = 203.0.113.10:443$" <<<"$(jget .conf)"' "$(jget .conf | grep Endpoint)"
addrs_on 203.0.113.10 198.51.100.20
PATH="$ADDRBIN:$PATH" act user-set-address --name=anna --ip=
# Вписанный домен: разовый IP побеждает его, сам домен тоже в вариантах.
act endpoint-set --host=vpn.example.com
PATH="$ADDRBIN:$PATH" act edge-status
check "домен в ключах: key_choices — домен «как обычно» первым, потом оба адреса" \
    '[ "$(jget "[.key_choices[] | .host + \":\" + .kind] | join(\",\")")" = "vpn.example.com:domain,203.0.113.10:ip,198.51.100.20:ip" ] && [ "$(jget ".key_choices[0].default")" = true ]' "$last"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=198.51.100.20
check "при доменном Endpoint разовый IP побеждает: 198.51.100.20:443" 'grep -q "^Endpoint = 198.51.100.20:443$" <<<"$(jget .conf)"' "$(jget .conf | grep Endpoint)"
PATH="$ADDRBIN:$PATH" act user-key --name=anna --profile=awg2 --format=conf --endpoint=vpn.example.com
check "настроенный домен разово: vpn.example.com:443" 'grep -q "^Endpoint = vpn.example.com:443$" <<<"$(jget .conf)" && [ "$(jget .key_host)" = vpn.example.com ]' "$(jget .conf | grep Endpoint)"
act endpoint-set --host=
check "users.json после всех разовых выдач — без следов адресов" '! grep -q endpoint_ip "$USERS" || [ "$(jq -r "[.users[] | select((.endpoint_ip // \"\") != \"\")] | length" "$USERS")" = 0 ]'

# ── 8. Выключение и включение ────────────────────────────────────────────────
section "user-disable / user-enable"
act user-disable --name=anna
expect "user-disable anna: success" success
check "users.json: enabled=false, active=false; пир убран из awg0.conf" \
    '[ "$(jq -r ".users[0].enabled" "$USERS")" = false ] && [ "$(jq -r ".users[0].peers.awg2.active" "$USERS")" = false ] && ! grep -qF "$puba" "$SD2/awg0.conf"'
act profiles-status
check "profiles-status: peers=1 (только bob активен)" '[ "$(jget ".profiles[0].peers")" = 1 ]' "$last"
act user-enable --name=anna
expect "user-enable anna: success" success
check "пир вернулся в awg0.conf" 'grep -qF "$puba" "$SD2/awg0.conf"'

# ── 9. Бэкап, трафик, диагностика ────────────────────────────────────────────
section "backup / traffic / quality / заглушки"
# 1c.9. Действующее лицо в аудите. Модуль копий читал GA_AUDIT_WHO — имя, которого
# не выставляет никто во всём продукте, — и любая копия значилась «ssh», то есть
# снятой владельцем руками. Здесь мост зовётся так же, как его зовёт панель.
export GOAMNEZIA_WHO="panel"
act backup-create
expect "backup-create: success" success
check "аудит backup-create: действующее лицо panel, а не ssh" \
    '[ "$(audit_who backup-create)" = panel ]' \
    "$(grep -F '"backup-create"' "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
unset GOAMNEZIA_WHO
bfile=$(jget .file)
check "ответ: file — имя архива, path — путь в backups/, архив есть и 0600" \
    '[[ "$bfile" == goamnezia_backup_*.tar.gz ]] && [ -f "$GOAMNEZIA_DIR/backups/$bfile" ] && [ "$(perm "$GOAMNEZIA_DIR/backups/$bfile")" = 600 ]' "$last"
act backup-list
expect "backup-list: success" success
check "backup-list: массив с этим архивом (name, size, created)" '[ "$(jget ".backups[0].name")" = "$bfile" ] && [ "$(jget ".backups[0].size")" -gt 0 ]' "$last"
act backup-restore --file=nope.tar.gz
expect "backup-restore несуществующего → backup_not_found" error backup_not_found
# §1d.2 №13-14: «когда была последняя копия» и расписание ночной копии из моста.
act backup-last
expect "backup-last: success" success
check "backup-last: имя, дата, число архивов — ответ на «когда была последняя копия»" \
    '[ "$(jget .name)" = "$bfile" ] && [ -n "$(jget .created)" ] && [ "$(jget .count)" -ge 1 ]' "$last"
act backup-schedule --frequency=never
expect "backup-schedule с неизвестной периодичностью → bad_arg (enum из actions.json)" error bad_arg
act traffic
expect "traffic: success (dry: пиров нет)" success
check "traffic: profiles[0].profile=awg2, users — массив без privkey" \
    '[ "$(jget ".profiles[0].profile")" = awg2 ] && [ "$(jget ".users|type")" = array ] && ! grep -q privkey <<<"$last"' "$last"
check "HM-1 traffic: поле hours есть и это список" '[ "$(jget ".hours|type")" = array ]' "$last"
# HM-1: почасовые корзины из stats.json — последние 168 часов, {hour: …:00Z, rx, tx, active}, по возрастанию.
hm_stats="$GOAMNEZIA_DIR/stats.json"; hm_keep=""
[ -f "$hm_stats" ] && hm_keep=$(cat "$hm_stats")
printf '%s' '{"schema":1,"updated":"","last":{},"days":{},"hours":{"2026-09-27T07":{"rx":100,"tx":50,"active":1},"2026-09-27T06":{"rx":1800,"tx":2900,"active":2},"2026-09-10T06":{"rx":9,"tx":9,"active":9}}}' >"$hm_stats"
GOAMNEZIA_STATS_NOW=1790492460 act traffic
check "HM-1 traffic.hours: две корзины окна 168 часов по возрастанию, старая за окном не попала" \
    '[ "$(jget ".hours|length")" = 2 ] && [ "$(jget ".hours[0].hour")" = 2026-09-27T06:00Z ] && [ "$(jget ".hours[0].active")" = 2 ] && [ "$(jget ".hours[1].hour")" = 2026-09-27T07:00Z ] && [ "$(jget ".hours[1].rx")" = 100 ] && [ "$(jget ".hours[1].tx")" = 50 ]' "$last"
if [ -n "$hm_keep" ]; then printf '%s' "$hm_keep" >"$hm_stats"; else rm -f "$hm_stats"; fi
# Docker машины здесь спрашивается по-настоящему (diagnostics, CONTRACT) — поэтому свой docker, который
# не отвечает: на раннере GitHub настоящий отвечает, и было бы docker=true.
NODOCKER="$TMP/nodocker"; mkdir -p "$NODOCKER"
printf '#!/bin/sh\necho "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2\nexit 1\n' >"$NODOCKER/docker"
chmod +x "$NODOCKER/docker"
PATH="$NODOCKER:$PATH" act quality
expect "quality: success" success
check "quality: docker=false (нет Docker), ports[0].profile=awg2" '[ "$(jget .quality.docker)" = false ] && [ "$(jget ".quality.ports[0].profile")" = awg2 ]' "$last"
act agent-list
expect "agent-list до выдачи доступов: success, agents=[] (сам модуль — в разделе 13)" success
check "agent-list: agents пуст, transport.enabled=false" '[ "$(jget ".agents|length")" = 0 ] && [ "$(jget .transport.enabled)" = false ]' "$last"
act upstream-check
expect "upstream-check без сети (dry-run без фикстур) → unreachable с текущими версиями профилей (сам модуль — в разделе 14)" error unreachable
check "upstream-check unreachable: profiles[0].profile=awg2 с current_tag, updates_available=false" \
    '[ "$(jget ".profiles[0].profile")" = awg2 ] && [ "$(jget ".profiles[0].current_tag")" = 3.1.20260828 ] && [ "$(jget .updates_available)" = false ]' "$last"
act upstream-check --cached=on
expect "upstream-check --cached=on до первой суточной проверки → no_cached_check (L-07)" error no_cached_check
act status
check "status.upstream до первой суточной проверки — null; переездов не было — server_ip_change=null (L-06, L-07)" \
    '[ "$(jget .upstream)" = null ] && [ "$(jget .server_ip_change)" = null ]' "$last"
# Раньше здесь проверялось «pro без домена → no_domain». По уточнённому замыслу
# владельца домена может не быть законно: сайт тогда поднимается на запасном
# самоподписанном сертификате (это проверяется в разделе 11a, где есть стенд
# nginx). Здесь остаётся то, что состояние сервера точно не меняет, — отказ на
# заведомо негодном домене.
act change-mode --mode=pro --domain=not..a..domain
expect "change-mode pro с негодным доменом → bad_arg (сам Pro — в разделе 11)" error bad_arg

# ── 9а. Привязка старого ключа через мост ────────────────────────────────────
# Ключ человека приходит мосту ТОЛЬКО каналом (stdin): в argv его видно в ps.
# Стенд: выданный Анне .conf сохраняем, а приватный ключ из users.json убираем —
# так выглядит человек, подхваченный из приложения Amnezia.
section "user-attach-key (ключ через stdin)"
act user-key --name=anna --profile=awg2 --format=conf
ANNA_CONF=$(jget .conf)
ANNA_PRIV=$(jq -r '.users[] | select(.name=="anna") | .peers.awg2.privkey' "$USERS")
jq '.users |= map(if .name == "anna" then .peers.awg2.privkey = "" else . end)' "$USERS" >"$TMP/users.swap" && cat "$TMP/users.swap" >"$USERS"
act user-key --name=anna --profile=awg2 --format=conf
expect "ключа нет на сервере → user-key: no_private_key" error no_private_key
check "в отказе — что ключ в приложении и как быть" 'grep -q "Привяжите старый ключ" <<<"$(jget .message)"' "$(jget .message)"
act user-attach-key --name=anna --config=abc
expect "--config в командной строке → bad_arg (ключ только каналом)" error bad_arg
act user-attach-key --name=anna </dev/null
expect "пустой канал → no_config" error no_config
# TK-10: предел 64 КБ — в байтах. Ключ в начале и дальше 80 КБ кириллицы (~40 тыс. символов): в UTF-8 ${#…}
# насчитывал символы, предел не срабатывал, и мост молча привязывал ключ из обрезанного текста.
printf '%s\n' "$ANNA_CONF" >"$TMP/attach_big.txt"
python3 -c 'import sys; sys.stdout.write("я" * 40000)' >>"$TMP/attach_big.txt"
LC_ALL=C.UTF-8 act user-attach-key --name=anna <"$TMP/attach_big.txt"
expect "TK-10 больше 64 КБ байтами (кириллица в UTF-8) → config_unreadable, ничего не привязано" error config_unreadable
check "TK-10 ключ не привязан" '[ -z "$(jq -r ".users[] | select(.name==\"anna\") | .peers.awg2.privkey" "$USERS")" ]'
rm -f "$TMP/attach_big.txt"
act user-attach-key --name=anna <<<"$ANNA_CONF"
expect "ключ Анны каналом → success" success
check "ответ: name=anna, profile=awg2 найден сам, line 2.0; ключа в ответе нет" \
    '[ "$(jget .name)" = anna ] && [ "$(jget .profile)" = awg2 ] && [ "$(jget .line)" = 2.0 ] && ! grep -qF "$ANNA_PRIV" "$OUT"' "$last"
act user-key --name=anna --profile=awg2 --format=conf
expect "после привязки user-key снова выдаёт ключ" success
check "в .conf тот же приватный ключ, что был у Анны" 'grep -qF "PrivateKey = $ANNA_PRIV" <<<"$(jget .conf)"'
ANNA_CONF=""; ANNA_PRIV=""

# ── 10. Удаление ─────────────────────────────────────────────────────────────
section "user-del / profiles-status"
act user-del --name=anna
expect "user-del anna: success" success
check "users.json без anna, awg0.conf без её пира, clientsTable без записи" \
    '! jq -e ".users[]|select(.name==\"anna\")" "$USERS" >/dev/null 2>&1 && ! grep -qF "$puba" "$SD2/awg0.conf" && ! grep -qF "$puba" "$SD2/clientsTable"'
act user-del --name=anna
expect "повторное удаление → user_not_found" error user_not_found
act user-list
check "user-list: остался bob" '[ "$(jget ".users|length")" = 1 ] && [ "$(jget ".users[0].name")" = bob ]' "$last"
act profiles-status
expect "profiles-status: success" success
check "profiles-status: awg2 port 443, line 2.0, peers=1" \
    '[ "$(jget ".profiles[0].port")" = 443 ] && [ "$(jget ".profiles[0].line")" = 2.0 ] && [ "$(jget ".profiles[0].peers")" = 1 ]' "$last"
check "аудит: profile-install, user-add, profile-set-param, profile-subnet, endpoint-set, user-del" \
    '_gone=0; for a in profile-install user-add profile-set-param profile-subnet endpoint-set user-del; do grep -q "$a" "$GOAMNEZIA_AUDIT_LOG" || _gone=1; done; [ "$_gone" = 0 ]'
check "аудит и логи без приватных ключей" '! grep -qF "$(jq -r ".users[0].peers.awg2.privkey" "$USERS")" "$GOAMNEZIA_AUDIT_LOG" "$GOAMNEZIA_LOG_FILE" 2>/dev/null'

# ── 11. Pro-режим: домен, сайт-маскировка, панель на домене (lib/website.sh) ─
# Стенд модуля — как в tests/test_website_dryrun.sh: nginx, сайт, letsencrypt и
# systemd во временных каталогах, шаблон — из локальной фикстуры через тот же
# хук GOAMNEZIA_TEMPLATE_LOCAL_DIR (проверка страницы выполняется, клона нет).
# Сертификат и nginx в dry-run — только строки «DRY: …». grep по vhost — с -F:
# BSD grep читает «^» внутри BRE как якорь, и «location ^~ /…» не находился.
section "Pro-режим: change-mode / change-template / miniapp-* / site-alt-svc / site-check"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx" GOAMNEZIA_SITE_ROOT="$TMP/site" GOAMNEZIA_ACME_ROOT="$TMP/acme"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt" GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
VHOST="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
act change-mode --mode=pro --domain=Example.com
expect "change-mode pro с заглавной буквой в домене → bad_arg (pattern домена — строчные)" error bad_arg
act change-mode --mode=pro --domain=example.com
expect "change-mode pro example.com: success" success
# Панель на домене включается сама вместе с сайтом (владелец 17.09.2026): в ответе и в status site.panel=true,
# а vhost сразу несёт её локацию.
check "ответ: mode=pro, domain, template.deployed=true, site.domain=example.com, site.panel=true (панель включилась сама)" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .domain)" = example.com ] && [ "$(jget .template.deployed)" = true ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.panel)" = true ]' "$last"
# «DRY: apt-get install -y nginx» — только когда nginx у машины нет (HOST_NGINX); есть — ставить нечего.
check "stderr: DRY apt nginx, certbot certonly -d example.com, nginx -t, reload, таймер автопродления" \
    '{ if [ "$HOST_NGINX" = 1 ]; then ! grep -q "DRY: apt-get install -y nginx" "$ERR"; else grep -q "DRY: apt-get install -y nginx" "$ERR"; fi; } && grep -q -- "DRY: certbot certonly .* -d example.com" "$ERR" && grep -q "DRY: nginx -t" "$ERR" && grep -q "DRY: systemctl reload nginx" "$ERR" && grep -q "goamnezia-cert-renew.timer" "$ERR"' "$(grep DRY "$ERR" | head -6)"
check "config.json: mode=pro, domain, site.template_id; vhost и симлинк записаны; index.html развёрнут без README.md" \
    '[ "$(jq -r .mode "$CFG")" = pro ] && [ "$(jq -r .domain "$CFG")" = example.com ] && [ -n "$(jq -r ".site.template_id // empty" "$CFG")" ] && [ -f "$VHOST" ] && [ -L "$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia" ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ ! -f "$GOAMNEZIA_SITE_ROOT/README.md" ]'
check "vhost: server_name example.com, listen 443 ssl, сертификат из GOAMNEZIA_LE_DIR, локация панели есть, Alt-Svc нет" \
    'grep -qF "server_name example.com;" "$VHOST" && grep -q "listen 443 ssl" "$VHOST" && grep -qF "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" "$VHOST" && grep -qF "proxy_pass http://127.0.0.1:1985/" "$VHOST" && ! grep -qF "add_header Alt-Svc" "$VHOST"' "$(grep -n 'Alt-Svc\|1985' "$VHOST")"
check "проектный каталог не тронут: /etc/nginx и /var/www не создавались и не менялись" '[ "$(host_sig /etc/nginx/sites-available/goamnezia)" = "$HOST_VHOST_SIG" ] && [ "$(host_sig /var/www/goamnezia-site/.goamnezia_template_id)" = "$HOST_SITE_SIG" ]'
act status
expect "status в Pro: success" success
check "status: mode=pro, site.domain, site.template_id, site.panel=true, site.alt_svc=false, site.index_present=true" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .site.domain)" = example.com ] && [ -n "$(jget .site.template_id)" ] && [ "$(jget .site.panel)" = true ] && [ "$(jget .site.alt_svc)" = false ] && [ "$(jget .site.index_present)" = true ]' "$last"
act change-template --template=h5up_aerial
expect "change-template h5up_aerial (фикстура): success" success
check "ответ: template.template_id=h5up_aerial, name=Aerial, deployed=true; config.json site.template_id; маркер в корне" \
    '[ "$(jget .template.template_id)" = h5up_aerial ] && [ "$(jget .template.name)" = Aerial ] && [ "$(jget .template.deployed)" = true ] && [ "$(jq -r .site.template_id "$CFG")" = h5up_aerial ] && [ "$(cat "$GOAMNEZIA_SITE_ROOT/.goamnezia_template_id")" = h5up_aerial ]' "$last"
act change-template --template='bad id'
expect "change-template 'bad id' → bad_arg ещё в диспетчере" error bad_arg
export GOAMNEZIA_TEMPLATE_LOCAL_DIR=""
act change-template --template=gh_no_such_template_zzz
expect "id не из каталога (без локального хука) → unknown_template из модуля, сеть не нужна" error unknown_template
export GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
# SEC46-02: путь панели — секрет, а argv любого процесса читает каждый локальный пользователь.
# Шпион jq пишет свои аргументы; miniapp-on и miniapp-rotate ниже идут через него.
SPY46="$TMP/spy46"; mkdir -p "$SPY46"; : >"$TMP/argv46.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv46.log" "$(command -v jq)" >"$SPY46/jq"
chmod +x "$SPY46/jq"
PATH="$SPY46:$PATH" act miniapp-on
expect "miniapp-on: success" success
ppath=$(jget .path)
# Панель уже включилась сама при включении сайта: miniapp-on путь не меняет (changed=false), чтобы не
# обесценивать ссылку, которую человек уже открыл.
check "ответ: path app-<base64url>, url=https://example.com/<path>/, enabled=true, changed=false (включена сама раньше), hint" \
    '[[ "$ppath" =~ ^app-[A-Za-z0-9_-]{86}$ ]] && [ "$(jget .url)" = "https://example.com/$ppath/" ] && [ "$(jget .enabled)" = true ] && [ "$(jget .changed)" = false ] && [ -n "$(jget .hint)" ]' "$last"
check "config.json: panel.miniapp_path и site.panel_path = путь; vhost: location ^~ /<path>/ → 127.0.0.1:1985" \
    '[ "$(jq -r .panel.miniapp_path "$CFG")" = "$ppath" ] && [ "$(jq -r .site.panel_path "$CFG")" = "$ppath" ] && grep -qF "location ^~ /$ppath/ {" "$VHOST" && grep -qF "proxy_pass http://127.0.0.1:1985/;" "$VHOST"' "$(grep -n 1985 "$VHOST")"
act miniapp-on
expect "повторный miniapp-on: success" success
check "повторный вызов путь не меняет: changed=false, тот же path" '[ "$(jget .changed)" = false ] && [ "$(jget .path)" = "$ppath" ]' "$last"
PATH="$SPY46:$PATH" act miniapp-rotate
expect "miniapp-rotate: success" success
ppath2=$(jget .path)
check "путь сменился: новый app-<hex12>, url с новым путём; в vhost старой локации нет, новая есть; config.json обновлён" \
    '[[ "$ppath2" =~ ^app-[A-Za-z0-9_-]{86}$ ]] && [ "$ppath2" != "$ppath" ] && [ "$(jget .url)" = "https://example.com/$ppath2/" ] && ! grep -qF "/$ppath/" "$VHOST" && grep -qF "location ^~ /$ppath2/ {" "$VHOST" && [ "$(jq -r .panel.miniapp_path "$CFG")" = "$ppath2" ]' "$last"
check "SEC46-02 путь панели (miniapp-on и miniapp-rotate: запись в config.json, ответ, текст сообщения) не прошёл через argv jq" \
    '[ "$(wc -l <"$TMP/argv46.log" | tr -d " ")" -gt 10 ] && [ -n "$ppath" ] && [ -n "$ppath2" ] && ! grep -qF -- "$ppath" "$TMP/argv46.log" && ! grep -qF -- "$ppath2" "$TMP/argv46.log"' \
    "$(grep -F -e "$ppath" -e "$ppath2" "$TMP/argv46.log" | head -3 | cut -c1-200)"
act miniapp-status
expect "miniapp-status: success" success
check "miniapp-status: enabled=true, mode=pro, domain, panel_port=1985, site.verdict=dry_run; секретного пути в ответе нет" \
    '[ "$(jget .enabled)" = true ] && [ "$(jget .mode)" = pro ] && [ "$(jget .domain)" = example.com ] && [ "$(jget .panel_port)" = 1985 ] && [ "$(jget .site.verdict)" = dry_run ] && ! grep -qF "$ppath2" <<<"$last"' "$last"
act status
check "status: site.panel=true, пути в ответе нет" '[ "$(jget .site.panel)" = true ] && ! grep -qF "$ppath2" <<<"$last"' "$last"
act site-alt-svc --state=on
expect "site-alt-svc on: success" success
check "ответ: alt_svc=true (булево), applied=true; config.json options.alt_svc=true; vhost: add_header Alt-Svc" \
    '[ "$(jq -c .alt_svc <<<"$last")" = true ] && [ "$(jget .applied)" = true ] && [ "$(jq -c .options.alt_svc "$CFG")" = true ] && grep -qF "add_header Alt-Svc" "$VHOST"' "$last"
act site-alt-svc --state=maybe
expect "site-alt-svc maybe → bad_arg (enum on|off)" error bad_arg
act site-check
expect "site-check: success (readonly, без замка)" success
check "site-check: site.domain, site.mode=pro, site.dry_run=true, verdict=dry_run, index_present=true, cert_days_left=null, checked_at" \
    '[ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.mode)" = pro ] && [ "$(jget .site.dry_run)" = true ] && [ "$(jget .verdict)" = dry_run ] && [ "$(jget .site.index_present)" = true ] && [ "$(jget .site.cert_days_left)" = null ] && [ -n "$(jget .site.checked_at)" ]' "$last"
act miniapp-off
expect "miniapp-off: success" success
check "ответ enabled=false; config.json: оба поля пути пусты; vhost без панели, якорь остался" \
    '[ "$(jget .enabled)" = false ] && [ "$(jq -r .panel.miniapp_path "$CFG")" = "" ] && [ "$(jq -r .site.panel_path "$CFG")" = "" ] && ! grep -qF "proxy_pass http://127.0.0.1:1985/" "$VHOST" && grep -qF "# GOAMNEZIA_PANEL_LOCATION" "$VHOST"' "$last"
act miniapp-rotate
expect "miniapp-rotate у выключенной панели → panel_disabled" error panel_disabled
act site-alt-svc --state=off
expect "site-alt-svc off: success" success
check "options.alt_svc=false (булево, не строка); заголовка в vhost нет" '[ "$(jq -c .options.alt_svc "$CFG")" = false ] && ! grep -qF "add_header Alt-Svc" "$VHOST"'
# ── 11a. Три режима по кругу: pro → lite_site → lite → lite_site → pro → lite_site ──
#          и запасной путь (домена нет) — самоподписанный сертификат на IP
#
# Договор о режимах (владелец уточнил замысел, код уже переделан):
#   lite      — только VPN, сайта нет;
#   lite_site — ДОМЕН, сайт-заглушка и панель на TCP/443 с настоящим
#               сертификатом Let's Encrypt, AmneziaWG — на своём отдельном порту;
#   pro       — то же самое, но AmneziaWG стоит на UDP/443 рядом с сайтом.
# То есть два режима с сайтом отличаются ТОЛЬКО местом AmneziaWG, а сайт, домен,
# сертификат и панель у них общие. Отсюда главное, что проверяет раздел: переход
# pro ↔ lite_site НИЧЕГО не делает с сайтом — не дёргает certbot (лимиты Let's
# Encrypt), не переписывает vhost, не заводит запасной сертификат.
#
# Вид сертификата теперь живёт в config.json (site.cert) и не выводится из
# режима: самоподписанный остался запасным путём ровно для двух случаев —
# домена ещё нет или A-запись ещё не указывает на сервер. Этот путь проверяется
# отдельной веткой ниже (домен в конфиге стирается): без неё правило PRO.md §3.6
# (tls/ в бэкапе, иначе агенты MCP теряют доверенный отпечаток) осталось бы без
# теста, потому что при живом домене каталог tls/ вообще не создаётся.
TLS="$GOAMNEZIA_DIR/tls"
FP_RE='^([0-9A-F]{2}:){31}[0-9A-F]{2}$'
VHOST_BEFORE="$TMP/vhost_pro_before.conf"

# ── A. pro → lite_site: сайт остаётся на домене, сертификат не трогается ─────
cp "$VHOST" "$VHOST_BEFORE"
act change-mode --mode=lite_site
expect "change-mode lite_site из pro: success" success
check "ответ: mode=lite_site, previous_mode=pro, домен на месте, site.cert=letsencrypt, site.domain и url на домене, отпечатка нет" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .previous_mode)" = pro ] && [ "$(jget .domain)" = example.com ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.url)" = "https://example.com/" ] && [ "$(jget .site.cert_fingerprint_sha256)" = "" ]' "$last"
# LC-18: мост порт не двигает, но о расхождении режима и порта говорит — полем и словами (панель молчала).
check "LC-18 «VPN и сайт», а профиль остался на UDP/443: mode_port_mismatch=true, профиль назван, в сообщении — что перенести" \
    '[ "$(jget .mode_port_mismatch)" = true ] && [ -n "$(jget .vpn_443_profile)" ] && grep -q "UDP/443" <<<"$(jget .message)"' "$last"
# Главная проверка нового договора: смена режима — это одно слово в config.json.
# Отсюда и запрет на certbot: перевыпуск при каждом переносе VPN с 443 съедал бы
# лимит Let's Encrypt, а запасной tls/ подменял бы доверенный сертификат.
check "переход сайта не тронул: certbot не звался, tls/ не заводился, vhost байт-в-байт прежний" \
    '! grep -q certbot "$ERR" && [ ! -e "$TLS" ] && cmp -s "$VHOST" "$VHOST_BEFORE"' "$(grep DRY "$ERR" | head -4)"
check "vhost по-прежнему на домене: server_name example.com, сертификат Let's Encrypt, ACME-локация, HSTS и редирект с 80" \
    'grep -qF "server_name example.com;" "$VHOST" && grep -qF "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" "$VHOST" && grep -q "acme-challenge" "$VHOST" && grep -q "Strict-Transport" "$VHOST" && grep -q "return 301" "$VHOST"' "$(grep -nE "server_name|ssl_certificate |acme|301" "$VHOST")"
check "config.json: mode=lite_site, site.cert=letsencrypt, domain сохранён, сайт и шаблон на диске" \
    '[ "$(jq -r .mode "$CFG")" = lite_site ] && [ "$(jq -r .site.cert "$CFG")" = letsencrypt ] && [ "$(jq -r .domain "$CFG")" = example.com ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ "$(cat "$GOAMNEZIA_SITE_ROOT/.goamnezia_template_id")" = h5up_aerial ]'
check "профиль awg2 и пользователь bob не тронуты переходом" \
    '[ "$(jq -r .profiles.awg2.port "$CFG")" = 443 ] && [ "$(jq -r ".users[0].name" "$USERS")" = bob ]'
act status
expect "status в lite_site: success" success
check "status: mode=lite_site, site.cert=letsencrypt, домен и url, панель выключена, шаблон прежний, отпечатка нет" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.url)" = "https://example.com/" ] && [ "$(jget .site.panel)" = false ] && [ "$(jget .site.template_id)" = h5up_aerial ] && [ "$(jget .site.cert_fingerprint_sha256)" = "" ]' "$last"
# Ярлык режима сменился, а AmneziaWG как стоял на UDP/443, так и стоит: мост
# порт не двигает. Раньше об этом не говорил никто, и «VPN и сайт» в боте
# означало то же самое, что «Сайт и VPN на 443». Теперь расхождение видно.
check "status: mode_port_mismatch=true — режим lite_site, а профиль на UDP/443" \
    '[ "$(jget .mode_port_mismatch)" = true ]' "$last"
act site-check
expect "site-check в lite_site: success" success
check "site-check: site.mode=lite_site, cert=letsencrypt, домен и url на домене, verdict=dry_run, http_code — число" \
    '[ "$(jget .site.mode)" = lite_site ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.url)" = "https://example.com/" ] && [ "$(jget .verdict)" = dry_run ] && [ "$(jget ".site.http_code|type")" = number ]' "$last"
check "SR-48 site-check называет развёрнутый шаблон (site.template_id) — бот больше не пишет «не развёрнут»" \
    '[ "$(jget .site.template_id)" = h5up_aerial ]' "$(jget .site)"
act change-template --template=h5up_aerial
expect "change-template в lite_site: success (сайт есть)" success
check "vhost после смены шаблона по-прежнему на домене" 'grep -qF "server_name example.com;" "$VHOST"'
# Панель на домене во ВТОРОМ режиме. Раньше здесь ждали not_pro, потому что
# домена в lite_site не было вовсе; теперь домен и доверенный сертификат те же,
# что в pro, и панель работает — _action_require_pro спрашивает про сертификат и
# домен, а не про название режима.
act miniapp-on
expect "miniapp-on в lite_site: success — панели нужен домен с настоящим сертификатом, а не режим pro" success
spath=$(jget .path)
check "ответ: path app-<hex12>, url на домене, enabled=true, changed=true" \
    '[[ "$spath" =~ ^app-[A-Za-z0-9_-]{86}$ ]] && [ "$(jget .url)" = "https://example.com/$spath/" ] && [ "$(jget .enabled)" = true ] && [ "$(jget .changed)" = true ]' "$last"
check "config.json: путь панели записан; vhost: location ^~ /<path>/ → 127.0.0.1:1985" \
    '[ "$(jq -r .panel.miniapp_path "$CFG")" = "$spath" ] && [ "$(jq -r .site.panel_path "$CFG")" = "$spath" ] && grep -qF "location ^~ /$spath/ {" "$VHOST" && grep -qF "proxy_pass http://127.0.0.1:1985/;" "$VHOST"' "$(grep -n 1985 "$VHOST")"
act status
check "status в lite_site: site.panel=true, секретного пути в ответе нет" \
    '[ "$(jget .site.panel)" = true ] && ! grep -qF "$spath" <<<"$last"' "$last"
act miniapp-status
check "miniapp-status в lite_site: enabled=true, mode=lite_site, domain, panel_port=1985, site.verdict=dry_run" \
    '[ "$(jget .status)" = success ] && [ "$(jget .enabled)" = true ] && [ "$(jget .mode)" = lite_site ] && [ "$(jget .domain)" = example.com ] && [ "$(jget .panel_port)" = 1985 ] && [ "$(jget .site.verdict)" = dry_run ]' "$last"
act miniapp-rotate
expect "miniapp-rotate в lite_site: success" success
spath2=$(jget .path)
check "путь сменился: url с новым путём, в vhost старой локации нет, новая есть" \
    '[[ "$spath2" =~ ^app-[A-Za-z0-9_-]{86}$ ]] && [ "$spath2" != "$spath" ] && [ "$(jget .url)" = "https://example.com/$spath2/" ] && ! grep -qF "/$spath/" "$VHOST" && grep -qF "location ^~ /$spath2/ {" "$VHOST"' "$last"
act site-alt-svc --state=on
check "site-alt-svc on в lite_site: applied=true, заголовок в vhost на домене" \
    '[ "$(jget .status)" = success ] && [ "$(jget .applied)" = true ] && [ "$(jget .mode)" = lite_site ] && grep -qF "add_header Alt-Svc" "$VHOST" && grep -qF "server_name example.com;" "$VHOST"' "$last"
act site-alt-svc --state=off
expect "site-alt-svc off в lite_site: success" success
check "заголовка в vhost нет, панель на месте" '! grep -qF "add_header Alt-Svc" "$VHOST" && grep -qF "location ^~ /$spath2/ {" "$VHOST"'

# ── B. lite_site → lite: сайта нет, файлы и сертификат остаются ──────────────
act change-mode --mode=lite
expect "change-mode lite из lite_site: success" success
check "ответ: mode=lite, previous_mode=lite_site, site=null, domain сохранён; vhost и симлинк убраны; файлы сайта на диске" \
    '[ "$(jget .mode)" = lite ] && [ "$(jget .previous_mode)" = lite_site ] && [ "$(jq -c .site <<<"$last")" = null ] && [ "$(jget .domain)" = example.com ] && [ ! -e "$VHOST" ] && [ ! -e "$GOAMNEZIA_NGINX_DIR/sites-enabled/goamnezia" ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ]' "$last"
act status
check "status в Lite: mode=lite, site=null" '[ "$(jget .mode)" = lite ] && [ "$(jq -c .site <<<"$last")" = null ]' "$last"
act change-template --template=h5up_aerial
expect "change-template в Lite → needs_site" error needs_site
act miniapp-on
expect "miniapp-on в Lite → not_pro (сайта нет вовсе)" error not_pro
check "отказ называет причину машинно: reason=no_site" '[ "$(jget .reason)" = no_site ]' "$last"
# Путь панели пережил смену режима — и раньше состояние бодро отвечало
# «включено» там, где включать нечего (DEFECTS §1.8). Включение и состояние
# теперь отвечают на один и тот же вопрос одним и тем же словом.
check "путь панели в конфиге ещё жив — проверяем именно это состояние" \
    '[ -n "$(jq -r ".panel.miniapp_path // empty" "$CFG")" ]' "$(jq -c .panel "$CFG")"
act miniapp-status
expect "miniapp-status в Lite: success" success
check "enabled=false (включать нечего), configured=true (запись жива), available=false, reason=no_site, hint не пуст" \
    '[ "$(jget .enabled)" = false ] && [ "$(jget .configured)" = true ] && [ "$(jget .available)" = false ] && [ "$(jget .reason)" = no_site ] && [ -n "$(jget .hint)" ]' "$last"
check "и по-прежнему не выдаёт секретный путь" '! grep -qF "$spath2" <<<"$last"' "$last"
act site-check
check "site-check в Lite: site.mode=lite, cert пуст, success" '[ "$(jget .status)" = success ] && [ "$(jget .site.mode)" = lite ] && [ "$(jget .site.cert)" = "" ]' "$last"
act miniapp-off
expect "miniapp-off в Lite: success (модуль пишет только config.json)" success
check "путь панели стёрт в обоих полях" '[ "$(jq -r .panel.miniapp_path "$CFG")" = "" ] && [ "$(jq -r .site.panel_path "$CFG")" = "" ]'

# ── C. lite → lite_site: домен из config.json, сертификат Let's Encrypt ──────
# Раньше отсюда поднимался сайт-заглушка по IP; по новому договору второй режим
# — это домен, и мост берёт его из config.json (мастер или прежний режим уже
# сохранили), а certbot запрашивает сертификат так же, как для pro.
act change-mode --mode=lite_site
expect "change-mode lite_site из lite: success" success
check "ответ: previous_mode=lite, site.cert=letsencrypt, домен и url на домене, шаблон прежний; DRY certbot -d example.com; tls/ не заводился" \
    '[ "$(jget .previous_mode)" = lite ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.url)" = "https://example.com/" ] && [ "$(jget .template.template_id)" = h5up_aerial ] && grep -q -- "DRY: certbot certonly .* -d example.com" "$ERR" && [ ! -e "$TLS" ]' "$last"
check "vhost снова на домене: server_name, сертификат Let's Encrypt, ACME-локация" \
    'grep -qF "server_name example.com;" "$VHOST" && grep -qF "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" "$VHOST" && grep -q "acme-challenge" "$VHOST"' "$(grep -nE "server_name|ssl_certificate " "$VHOST")"
act change-mode --mode=lite_site
expect "повторный change-mode lite_site (уже в нём): success — ремонт, а не ошибка" success
check "повтор: mode=lite_site, previous_mode=lite_site, сертификат тот же и certbot не звался" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .previous_mode)" = lite_site ] && [ "$(jget .site.cert)" = letsencrypt ] && ! grep -q certbot "$ERR"' "$last"

# ── D. lite_site → pro и обратно: сайт и сертификат общие ────────────────────
cp "$VHOST" "$VHOST_BEFORE"
act change-mode --mode=pro
expect "change-mode pro из lite_site без --domain (домен из config.json): success" success
check "ответ: mode=pro, previous_mode=lite_site, site.cert=letsencrypt, шаблон прежний; certbot не звался, vhost байт-в-байт прежний" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .previous_mode)" = lite_site ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .template.template_id)" = h5up_aerial ] && ! grep -q certbot "$ERR" && cmp -s "$VHOST" "$VHOST_BEFORE"' "$last"
act status
check "status в Pro: site.cert=letsencrypt, url на домене, профиль и пользователь на месте" \
    '[ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.url)" = "https://example.com/" ] && [ "$(jget ".profiles[0].port")" = 443 ] && [ "$(jget .users)" = 1 ]' "$last"
act change-mode --mode=lite_site
expect "pro → lite_site ещё раз: success" success
check "круг замкнулся: mode=lite_site, cert=letsencrypt, vhost прежний, tls/ так и не появился" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jq -r .site.cert "$CFG")" = letsencrypt ] && cmp -s "$VHOST" "$VHOST_BEFORE" && [ ! -e "$TLS" ]' "$last"

# ── F. Запасной путь: домена нет — сайт на IP с самоподписанным сертификатом ─
# Домен стираем прямо в config.json: это состояние сервера, у которого домена
# ещё нет (мастер прошли без него), а действия моста домен только записывают.
act change-mode --mode=lite
expect "перед запасным путём: change-mode lite: success" success
jq '.domain = ""' "$CFG" >"$TMP/cfg.tmp" && mv "$TMP/cfg.tmp" "$CFG" && chmod 600 "$CFG"
check "домен в config.json стёрт — дальше проверяем сервер, у которого домена ещё нет" '[ "$(jq -r .domain "$CFG")" = "" ]'
act change-mode --mode=lite_site
expect "change-mode lite_site без домена: success — запасной путь, а не отказ" success
fp=$(jget .site.cert_fingerprint_sha256)
check "ответ: mode=lite_site, previous_mode=lite, site.cert=selfsigned, отпечаток SHA-256, site.domain пуст, site.url=https://IP/; certbot не звался" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .previous_mode)" = lite ] && [ "$(jget .site.cert)" = selfsigned ] && [[ "$fp" =~ $FP_RE ]] && [ "$(jget .site.domain)" = "" ] && [ "$(jget .site.url)" = "https://203.0.113.10/" ] && ! grep -q certbot "$ERR"' "$last"
check "сертификат: $TLS/site.{key,crt}, каталог 700, файлы 600, CN и SAN = 203.0.113.10" \
    '[ "$(perm "$TLS")" = 700 ] && [ "$(perm "$TLS/site.key")" = 600 ] && [ "$(perm "$TLS/site.crt")" = 600 ] && openssl x509 -noout -text -in "$TLS/site.crt" | grep -q "IP Address:203\.0\.113\.10" && openssl x509 -noout -subject -in "$TLS/site.crt" | grep -q "203\.0\.113\.10"' "$(ls -la "$TLS" 2>&1)"
check "vhost на IP: server_name _, наши ssl_certificate site.crt/site.key, listen 80 и 443 ssl, без ACME-локации, домена, HSTS и редиректа" \
    'grep -qF "server_name _;" "$VHOST" && grep -qF "ssl_certificate     $TLS/site.crt;" "$VHOST" && grep -qF "ssl_certificate_key $TLS/site.key;" "$VHOST" && grep -q "listen 80;" "$VHOST" && grep -q "listen 443 ssl" "$VHOST" && ! grep -q "acme-challenge" "$VHOST" && ! grep -qF "example.com" "$VHOST" && ! grep -q "Strict-Transport" "$VHOST" && ! grep -q "return 301" "$VHOST"' "$(grep -nE "server_name|ssl_certificate|listen|acme|301" "$VHOST")"
check "config.json: mode=lite_site, site.cert=selfsigned, domain пуст; сайт и шаблон прежние" \
    '[ "$(jq -r .mode "$CFG")" = lite_site ] && [ "$(jq -r .site.cert "$CFG")" = selfsigned ] && [ "$(jq -r .domain "$CFG")" = "" ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ] && [ "$(cat "$GOAMNEZIA_SITE_ROOT/.goamnezia_template_id")" = h5up_aerial ]'
act status
expect "status на запасном пути: success" success
check "status: site.cert=selfsigned, тот же отпечаток, url по IP, cert_days_left ≥ 3640, site.domain пуст, панель выключена" \
    '[ "$(jget .site.cert)" = selfsigned ] && [ "$(jget .site.cert_fingerprint_sha256)" = "$fp" ] && [ "$(jget .site.url)" = "https://203.0.113.10/" ] && [ "$(jget .site.cert_days_left)" -ge 3640 ] && [ "$(jget .site.domain)" = "" ] && [ "$(jget .site.panel)" = false ]' "$last"
check "status.domain (корень ответа) тоже пуст — домена у сервера пока нет" '[ "$(jget .domain)" = "" ]' "$last"
act site-check
expect "site-check на запасном пути: success" success
check "site-check: cert=selfsigned, отпечаток, url по IP, domain пуст, verdict=dry_run, http_code — число" \
    '[ "$(jget .site.mode)" = lite_site ] && [ "$(jget .site.cert)" = selfsigned ] && [ "$(jget .site.cert_fingerprint_sha256)" = "$fp" ] && [ "$(jget .site.url)" = "https://203.0.113.10/" ] && [ "$(jget .site.domain)" = "" ] && [ "$(jget .verdict)" = dry_run ] && [ "$(jget ".site.http_code|type")" = number ]' "$last"
# Панель на запасном пути не работает: браузер самоподписанному сертификату не
# доверяет, и мини-апп по такой ссылке не откроется. Код отказа остался not_pro
# (его разбирают бот, панель и MCP), но поля говорят настоящую причину.
act miniapp-on
expect "miniapp-on на самоподписанном → not_pro (доверенного сертификата нет)" error not_pro
check "отказ несёт причину: mode=lite_site, cert=selfsigned, domain пуст" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .cert)" = selfsigned ] && [ "$(jget .domain)" = "" ]' "$last"
act miniapp-rotate
expect "miniapp-rotate на самоподписанном → not_pro" error not_pro
act miniapp-status
check "miniapp-status: enabled=false, mode=lite_site, domain пуст, site.verdict=dry_run" \
    '[ "$(jget .status)" = success ] && [ "$(jget .enabled)" = false ] && [ "$(jget .mode)" = lite_site ] && [ "$(jget .domain)" = "" ] && [ "$(jget .site.verdict)" = dry_run ]' "$last"
act site-alt-svc --state=on
check "site-alt-svc on на запасном пути: applied=true, заголовок в vhost на IP" \
    '[ "$(jget .status)" = success ] && [ "$(jget .applied)" = true ] && grep -qF "add_header Alt-Svc" "$VHOST" && grep -qF "server_name _;" "$VHOST"' "$last"
act site-alt-svc --state=off
expect "site-alt-svc off на запасном пути: success" success
act change-template --template=h5up_aerial
expect "change-template на запасном пути: success (сайт есть)" success
check "vhost после смены шаблона по-прежнему на IP" 'grep -qF "server_name _;" "$VHOST"'
# Бэкап с самоподписанным сертификатом и восстановление на удалённый tls/: без
# tls/ в архиве восстановление на чистом сервере выпустило бы новый сертификат —
# с новым отпечатком, который агентам MCP пришлось бы доверять заново (PRO.md §3.6).
act backup-create
expect "backup-create на запасном пути: success" success
bfile2=$(jget .file)
check "архив содержит tls/site.key и tls/site.crt" \
    'lst=$(tar tzf "$GOAMNEZIA_DIR/backups/$bfile2") && grep -q "/tls/site.key$" <<<"$lst" && grep -q "/tls/site.crt$" <<<"$lst"' "$(tar tzf "$GOAMNEZIA_DIR/backups/$bfile2" 2>/dev/null | tr "\n" " " | head -c 600)"
rm -rf "$TLS"
# Восстановление — самое разрушительное действие продукта: имя того, кто его
# запустил, обязано быть настоящим (1c.9).
export GOAMNEZIA_WHO="bot:42"
act backup-restore --file="$bfile2"
expect "backup-restore после удаления tls/: success" success
check "аудит backup-restore: действующее лицо bot:42, а не ssh" \
    '[ "$(audit_who backup-restore)" = "bot:42" ]' \
    "$(grep -F '"backup-restore"' "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
unset GOAMNEZIA_WHO
check "tls/ восстановлен: каталог 700, ключ и сертификат 600, отпечаток прежний" \
    '[ "$(perm "$TLS")" = 700 ] && [ "$(perm "$TLS/site.key")" = 600 ] && [ "$(perm "$TLS/site.crt")" = 600 ] && [ "$(openssl x509 -noout -fingerprint -sha256 -in "$TLS/site.crt" | sed "s/^.*=//")" = "$fp" ]' "$(ls -la "$TLS" 2>&1; errs)"
check "восстановление не тронуло режим, профиль и пользователя" \
    '[ "$(jq -r .mode "$CFG")" = lite_site ] && [ "$(jq -r .profiles.awg2.port "$CFG")" = 443 ] && [ "$(jq -r ".users[0].name" "$USERS")" = bob ]'
act change-mode --mode=lite
expect "запасной путь → lite: success" success
check "ответ: site=null, домена по-прежнему нет; vhost убран, сертификат и сайт на диске" \
    '[ "$(jq -c .site <<<"$last")" = null ] && [ "$(jget .domain)" = "" ] && [ ! -e "$VHOST" ] && [ -f "$TLS/site.crt" ] && [ -f "$GOAMNEZIA_SITE_ROOT/index.html" ]' "$last"
act change-mode --mode=lite_site
expect "lite → lite_site без домена ещё раз: success" success
check "сертификат не перевыпущен (отпечаток прежний), шаблон прежний, vhost снова на IP" \
    '[ "$(jget .site.cert_fingerprint_sha256)" = "$fp" ] && [ "$(jget .template.template_id)" = h5up_aerial ] && grep -qF "server_name _;" "$VHOST"' "$last"
# Домен появился: запасной путь сменяется настоящим сертификатом, файлы
# самоподписанного остаются на диске (отпечаток не пропадает из бэкапов).
act change-mode --mode=pro --domain=example.com
expect "домен появился: change-mode pro --domain=example.com из запасного пути: success" success
check "ответ: mode=pro, site.cert=letsencrypt, отпечатка нет, домен и url; DRY certbot -d example.com" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .previous_mode)" = lite_site ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.cert_fingerprint_sha256)" = "" ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.url)" = "https://example.com/" ] && grep -q -- "DRY: certbot certonly .* -d example.com" "$ERR"' "$last"
check "vhost Pro: server_name example.com, Let's Encrypt, ACME; site.crt в конфиге nginx нет; самоподписанный остался на диске" \
    'grep -qF "server_name example.com;" "$VHOST" && grep -qF "$GOAMNEZIA_LE_DIR/live/example.com/fullchain.pem" "$VHOST" && grep -q "acme-challenge" "$VHOST" && ! grep -q "site\.crt" "$VHOST" && [ -f "$TLS/site.crt" ]' "$(grep -nE "server_name|ssl_certificate " "$VHOST")"

# ── 1d.1 №8: пустое --mode= снимало живой сайт и отвечало «успешно» ─────────
# Ветка *) диспетчера читала «не pro и не lite_site» как «иди в lite»: пустое
# значение (и любая опечатка) сносило домен, сертификат и панель на 443, а мост
# при этом отвечал success. Единственная дыра из 43 проверенных пустых
# аргументов: у остальных страж стоит в модуле, а переход в lite мост делает
# сам, и спросить «а это вообще режим?» было не у кого.
vhost_before_mode=$(cat "$VHOST")
act change-mode --mode=
expect "1d.1 №8 пустое --mode= → invalid_mode, а не «успешно»" error invalid_mode
check "1d.1 №8 живой сайт не снят: mode=pro, vhost байт-в-байт прежний" \
    '[ "$(jq -r .mode "$CFG")" = pro ] && [ "$(cat "$VHOST")" = "$vhost_before_mode" ]' "$last"
# Непустая опечатка отсеивается раньше — общим стражем enum в диспетчере
# (bad_arg). Дыра была именно у ПУСТОГО значения: пустое поле этот страж
# пропускает намеренно (у других действий «--value=» значит «убрать»).
act change-mode --mode=prro
expect "1d.1 №8 опечатка в режиме — отказ bad_arg общим стражем enum" error bad_arg
check "1d.1 №8 и после опечатки сайт на месте" \
    '[ "$(jq -r .mode "$CFG")" = pro ] && [ "$(cat "$VHOST")" = "$vhost_before_mode" ]' "$last"
check "1d.1 №8 отказ по пустому режиму не попал в аудит как смена режима" \
    '! grep -q "	change-mode	 (" "$GOAMNEZIA_AUDIT_LOG"' "$(grep change-mode "$GOAMNEZIA_AUDIT_LOG" | tail -n 3)"

# ── SR-54: запасной путь — только когда сайта на Let's Encrypt ещё нет ──────────
# Правка SR-02 (DNS не готов → сайт на самоподписанном) срабатывала на ЛЮБОЙ вызов с
# --domain: смена домена живого сайта из бота/панели/MCP при неготовом DNS сносила
# рабочий сайт на самоподписанный по IP вместе с панелью. Теперь живой сайт идёт через
# website_enable_pro (при отказе certbot — откат на прежний домен), а запасной путь —
# только из «Только VPN», с запасного самоподписанного или при пропавшем vhost.
# DNS на стенде — подставной (GOAMNEZIA_DNS_STUB): чужой адрес = «A-запись не сюда».
export GOAMNEZIA_DNS_STUB="198.51.100.77"
act change-mode --mode=pro --domain=new.example.com
expect "SR-54 живой сайт на Let's Encrypt, новый домен с чужой A-записью: success" success
check "SR-54 ответ: запасного пути нет (dns_fallback=false), сертификат letsencrypt, certbot -d new.example.com" \
    '[ "$(jget .dns_fallback)" = false ] && [ "$(jget .site.cert)" = letsencrypt ] && grep -q -- "DRY: certbot certonly .* -d new.example.com" "$ERR"' "$last | $(errs)"
act change-mode --mode=lite
expect "SR-54 → lite перед подъёмом с чужой A-записью: success" success
act change-mode --mode=lite_site --domain=fresh.example.net
expect "SR-54 из lite, A-запись не сюда: success на запасном пути" success
check "SR-54 из lite: dns_fallback=true, cert=selfsigned, домен сохранён, в сообщении — как выйти с запасного" \
    '[ "$(jget .dns_fallback)" = true ] && [ "$(jget .site.cert)" = selfsigned ] && [ "$(jq -r .domain "$CFG")" = fresh.example.net ] && grep -q "повторите этот переход с тем же доменом" <<<"$(jget .message)"' "$last"
export GOAMNEZIA_DNS_STUB="203.0.113.10"
act change-mode --mode=lite_site --domain=fresh.example.net
expect "SR-54 A-запись дошла: тот же переход с доменом с запасного: success" success
check "SR-54 с запасного на настоящий: cert=letsencrypt, dns_fallback=false, certbot -d fresh.example.net" \
    '[ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .dns_fallback)" = false ] && grep -q -- "DRY: certbot certonly .* -d fresh.example.net" "$ERR"' "$last | $(errs)"
unset GOAMNEZIA_DNS_STUB
act change-mode --mode=pro --domain=example.com
expect "SR-54 назад на example.com в pro: success" success

# ── Финал раздела ───────────────────────────────────────────────────────────
act change-mode --mode=lite
expect "и снова lite: success" success
act status
check "финал: mode=lite, site=null, domain сохранён, профиль awg2 и bob на месте" \
    '[ "$(jget .mode)" = lite ] && [ "$(jq -c .site <<<"$last")" = null ] && [ "$(jget .domain)" = example.com ] && [ "$(jget ".profiles[0].port")" = 443 ] && [ "$(jget .users)" = 1 ]' "$last"
# Подробности аудита — ровно те, что пишет lib/website.sh: «(site kept, from …)»
# у перехода между двумя режимами с сайтом (сайт не трогали), «domain=…» у
# включения на домене, «selfsigned» у запасного пути, «(site off)» у выхода в
# Lite. Перечислены цепочкой, а не циклом с exit: exit внутри eval в check
# оборвал бы весь прогон, спрятав всё, что идёт после раздела.
check "аудит: переходы (site kept / domain= / selfsigned / site off), change-template, miniapp-on (set и rotated), miniapp-off, site-alt-svc" \
    'audit_has change-mode "lite_site (site kept, from pro)" && audit_has change-mode "pro (site kept, from lite_site)" && audit_has change-mode "lite_site domain=example.com (from lite)" && jq -R -e -n "[inputs | fromjson? // empty | select(.action == \"change-mode\" and ((.details // \"\") | startswith(\"lite_site selfsigned\")))] | length > 0" "$GOAMNEZIA_AUDIT_LOG" >/dev/null && audit_has change-mode "lite (site off)" && audit_has change-template && audit_has miniapp-on "path set" && audit_has miniapp-on "path rotated" && audit_has miniapp-off && audit_has site-alt-svc' "$(grep change-mode "$GOAMNEZIA_AUDIT_LOG" | tail -n 14)"
check "секретных путей панели в аудите нет (ни из pro, ни из lite_site)" \
    '! grep -qF "$ppath" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$ppath2" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$spath" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "$spath2" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 12 "$GOAMNEZIA_AUDIT_LOG")"
check "аудит и логи без приватного ключа сертификата" '! grep -q "BEGIN PRIVATE KEY" "$GOAMNEZIA_AUDIT_LOG" "$GOAMNEZIA_LOG_FILE" "$ERR" 2>/dev/null'
unset GOAMNEZIA_NGINX_DIR GOAMNEZIA_SITE_ROOT GOAMNEZIA_ACME_ROOT GOAMNEZIA_LE_DIR GOAMNEZIA_SYSTEMD_DIR GOAMNEZIA_TEMPLATE_LOCAL_DIR

# ── 12. Предполётная проверка портов (lib/preflight.sh), lang-set, status.ports ──
# Источники preflight подменяются фикстурами tests/fixtures/preflight/ (как в
# tests/test_preflight_dryrun.sh); каталоги nginx копируем в $TMP — модуль сайта
# при успешном переходе пишет туда vhost, а фикстуры трогать нельзя. Стенд
# сайта — тот же, что в разделе 11: без него website_enable_site_selfsigned
# полез бы в /var/www и /etc/nginx.
section "preflight / change-mode → port_busy | closed_local | ours / status.ports / quality / lang-set"
PF="$ROOT/tests/fixtures/preflight"
export GOAMNEZIA_SITE_ROOT="$TMP/site" GOAMNEZIA_ACME_ROOT="$TMP/acme" GOAMNEZIA_LE_DIR="$TMP/letsencrypt"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd" GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
act preflight
expect "preflight: success (readonly)" success
check "preflight в dry-run без фикстур: три режима доступны, причин и предупреждений нет, флаги и hint в корне, dry_run=true" \
    '[ "$(jget .modes.lite.available)" = true ] && [ "$(jget .modes.lite_site.available)" = true ] && [ "$(jget .modes.pro.available)" = true ] && [ "$(jget ".modes.pro.reasons|length")" = 0 ] && [ "$(jget ".modes.warnings|length")" = 0 ] && [ "$(jget .lite_site_available)" = true ] && [ "$(jget .pro_available)" = true ] && [ "$(jget .hint)" = "" ] && [ "$(jget .modes.dry_run)" = true ]' "$last"
check "preflight: ports tcp80/tcp443/udp443 free; в stderr DRY ss (чтения без фикстур считаются пустыми)" \
    '[ "$(jget ".modes.ports|keys|join(\",\")")" = "tcp443,tcp80,udp443" ] && [ "$(jget .modes.ports.tcp443.state)" = free ] && grep -q "DRY: ss -H -ltnp" "$ERR"' "$(errs)"
# 443 и 80 у чужого nginx (PID 812, юнит nginx.service, vhost gotelegram)
cp -R "$PF/nginx_gotelegram" "$TMP/nginx_gt"
export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_nginx.txt" GOAMNEZIA_SS_FIXTURE_UDP="$PF/ss_udp_free.txt"
export GOAMNEZIA_PROC_ROOT="$PF/proc" GOAMNEZIA_DOCKER_PS_FIXTURE="$PF/docker_ps_exposed_only.txt"
export GOAMNEZIA_UFW_FIXTURE="$PF/ufw_active_web.txt" GOAMNEZIA_NGINX_DIR="$TMP/nginx_gt"
act preflight
check "preflight при занятом 443: lite_site и pro недоступны, причина с продуктом goTelegram, hint = systemctl disable --now nginx.service" \
    '[ "$(jget .status)" = success ] && [ "$(jget .lite_site_available)" = false ] && [ "$(jget .pro_available)" = false ] && [ "$(jget .modes.pro.reasons[0].product)" = gotelegram ] && [ "$(jget .hint)" = "systemctl disable --now nginx.service" ]' "$last"
act change-mode --mode=lite_site
expect "change-mode lite_site при занятом 443 чужим nginx → port_busy" error port_busy
check "port_busy: mode, port=443, proto=tcp, process=nginx, pid=812, unit=nginx.service, product=gotelegram, hint, reasons[1].port=80, message с продуктом" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .port)" = 443 ] && [ "$(jget .proto)" = tcp ] && [ "$(jget .process)" = nginx ] && [ "$(jget .pid)" = 812 ] && [ "$(jget .unit)" = nginx.service ] && [ "$(jget .container)" = "" ] && [ "$(jget .product)" = gotelegram ] && [ "$(jget .hint)" = "systemctl disable --now nginx.service" ] && [ "$(jget ".reasons|length")" = 2 ] && [ "$(jget ".reasons[1].port")" = 80 ] && grep -q "goTelegram Pro" <<<"$(jget .message)"' "$last"
check "отказ ничем не тронул сервер: mode=lite, vhost не писался, аудит без change-mode lite_site после отказа" \
    '[ "$(jq -r .mode "$CFG")" = lite ] && [ ! -e "$TMP/nginx_gt/sites-available/goamnezia" ] && [ ! -e "$TMP/nginx_gt/sites-enabled/goamnezia" ]'
act change-mode --mode=pro
expect "change-mode pro (домен из конфига) при занятом 443 → тот же port_busy" error port_busy
check "port_busy для pro: mode=pro, port=443, product=gotelegram; сертификат не запрашивался" \
    '[ "$(jget .mode)" = pro ] && [ "$(jget .port)" = 443 ] && [ "$(jget .product)" = gotelegram ] && ! grep -q "certbot" "$ERR"' "$last"
act change-mode --mode=lite
expect "change-mode lite при занятом 443: success — переход в lite портов не требует" success
# Порты свободны, но ufw активен без правил на 80/443 → closed_local
export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_free.txt" GOAMNEZIA_UFW_FIXTURE="$PF/ufw_active_no_web.txt"
act change-mode --mode=pro
expect "change-mode pro при ufw без правил → closed_local" error closed_local
check "closed_local: port=443, proto=tcp, pid=null, process пуст, hint = ufw allow 80,443/tcp, обе причины closed_local" \
    '[ "$(jget .port)" = 443 ] && [ "$(jget .proto)" = tcp ] && [ "$(jq -c .pid <<<"$last")" = null ] && [ "$(jget .process)" = "" ] && [ "$(jget .hint)" = "ufw allow 80,443/tcp" ] && [ "$(jget ".reasons|map(.state)|unique|join(\",\")")" = closed_local ]' "$last"
act change-mode --mode=lite_site
expect "change-mode lite_site при ufw без правил → closed_local" error closed_local
check "режим по-прежнему lite" '[ "$(jq -r .mode "$CFG")" = lite ]'
# Тот же занятый 443, но nginx наш (sites-enabled/goamnezia) — переустановка, не конфликт
mkdir -p "$TMP/nginx_ours/sites-enabled"; : > "$TMP/nginx_ours/sites-enabled/goamnezia"
export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_nginx.txt" GOAMNEZIA_UFW_FIXTURE="$PF/ufw_active_web.txt" GOAMNEZIA_NGINX_DIR="$TMP/nginx_ours"
export GOAMNEZIA_SS_FIXTURE_UDP="$PF/ss_udp_dockerproxy.txt" GOAMNEZIA_DOCKER_PS_FIXTURE="$PF/docker_ps_ours.txt"
act preflight
check "preflight: наш nginx → ours=true, режимы доступны; UDP/443 у контейнера amnezia-awg2 из config.json — без предупреждения" \
    '[ "$(jget .modes.ports.tcp443.ours)" = true ] && [ "$(jget .modes.ports.tcp443.product)" = goamnezia ] && [ "$(jget .lite_site_available)" = true ] && [ "$(jget .pro_available)" = true ] && [ "$(jget ".modes.warnings|length")" = 0 ] && [ "$(jget .modes.ports.udp443.container)" = amnezia-awg2 ]' "$last"
act change-mode --mode=lite_site
expect "change-mode lite_site при нашем nginx на 443: success" success
# Домен в config.json сохранился с раздела 11a, поэтому второй режим поднимается
# на нём и на настоящем сертификате (site.cert=letsencrypt, отпечатка нет —
# отпечаток есть только у запасного самоподписанного). Раньше здесь ждали
# самоподписанный: это был старый договор, где lite_site означал сайт по IP.
check "ответ: mode=lite_site, previous_mode=lite, домен и Let's Encrypt; vhost записан в стендовый каталог" \
    '[ "$(jget .mode)" = lite_site ] && [ "$(jget .previous_mode)" = lite ] && [ "$(jget .site.cert)" = letsencrypt ] && [ "$(jget .site.domain)" = example.com ] && [ "$(jget .site.cert_fingerprint_sha256)" = "" ] && [ -f "$TMP/nginx_ours/sites-available/goamnezia" ]' "$last"
act status
expect "status с портами профилей: success" success
check "status.ports[0]: awg2, port 443, proto udp, listening=true, ours=true, container amnezia-awg2, state busy; status.lang=ru" \
    '[ "$(jget ".ports|length")" = 1 ] && [ "$(jget ".ports[0].profile")" = awg2 ] && [ "$(jget ".ports[0].port")" = 443 ] && [ "$(jget ".ports[0].proto")" = udp ] && [ "$(jget ".ports[0].listening")" = true ] && [ "$(jget ".ports[0].ours")" = true ] && [ "$(jget ".ports[0].container")" = amnezia-awg2 ] && [ "$(jget ".ports[0].state")" = busy ] && [ "$(jget .lang)" = ru ]' "$last"
act quality
expect "quality с публичными портами: success" success
check "quality: public_ports.tcp443 ours, modes.lite_site/pro=true, port_warnings=[]; прежние поля на месте" \
    '[ "$(jget .quality.public_ports.tcp443.ours)" = true ] && [ "$(jget .quality.modes.lite_site)" = true ] && [ "$(jget .quality.modes.pro)" = true ] && [ "$(jget ".quality.port_warnings|length")" = 0 ] && [ "$(jget ".quality.ports[0].profile")" = awg2 ] && [ "$(jget ".quality.disk_free_mb|type")" = number ]' "$last"
# Слушателя нет и контейнер порт не публикует (в docker ps только EXPOSE) —
# иначе модуль честно считал бы порт занятым правилом DNAT (userland-proxy=false)
export GOAMNEZIA_SS_FIXTURE_UDP="$PF/ss_udp_free.txt" GOAMNEZIA_DOCKER_PS_FIXTURE="$PF/docker_ps_exposed_only.txt"
act status
check "status.ports без слушателя и без публикации: listening=false, state=free (контейнер не слушает свой порт)" \
    '[ "$(jget ".ports[0].listening")" = false ] && [ "$(jget ".ports[0].state")" = free ]' "$last"
# Язык ядра: lang-set меняет config.json и маркер, status отдаёт lang
act lang-set --lang=en
expect "lang-set en: success" success
check "ответ: lang=en, сообщение уже на английском; config.json lang=en, маркер .language=en" \
    '[ "$(jget .lang)" = en ] && [ "$(jget .message)" = "Language saved: en." ] && [ "$(jq -r .lang "$CFG")" = en ] && [ "$(cat "$GOAMNEZIA_DIR/.language")" = en ]' "$last"
act status
check "status.lang=en" '[ "$(jget .lang)" = en ]' "$last"
act lang-set --lang=de
expect "lang-set de → bad_arg (enum ru|en)" error bad_arg
act lang-set
expect "lang-set без --lang → missing_arg" error missing_arg
check "неудачные вызовы язык не тронули" '[ "$(jq -r .lang "$CFG")" = en ]'
act lang-set --lang=ru
expect "lang-set ru: success" success
check "обратно: config.json lang=ru, сообщение по-русски, status.lang=ru" \
    '[ "$(jq -r .lang "$CFG")" = ru ] && [ "$(jget .message)" = "Язык сохранён: ru." ]'
act status
check "status.lang=ru" '[ "$(jget .lang)" = ru ]' "$last"
act change-mode --mode=lite
expect "финал раздела: change-mode lite из lite_site: success" success
check "аудит: lang-set (en и ru), backup-restore; секретов нет" \
    '[ "$(grep -c "lang-set" "$GOAMNEZIA_AUDIT_LOG")" -ge 2 ] && grep -q "backup-restore" "$GOAMNEZIA_AUDIT_LOG" && ! grep -q "BEGIN PRIVATE KEY" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -n 6 "$GOAMNEZIA_AUDIT_LOG")"
unset GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_SS_FIXTURE_UDP GOAMNEZIA_PROC_ROOT GOAMNEZIA_DOCKER_PS_FIXTURE GOAMNEZIA_UFW_FIXTURE
unset GOAMNEZIA_NGINX_DIR GOAMNEZIA_SITE_ROOT GOAMNEZIA_ACME_ROOT GOAMNEZIA_LE_DIR GOAMNEZIA_SYSTEMD_DIR GOAMNEZIA_TEMPLATE_LOCAL_DIR

# ── 13. Доступы агентов к MCP по HTTP (lib/agents.sh, PLAN §6.10, CONTRACT §5.1) ──
# Стенд сайта — как в разделе 11 (nginx и systemd во временных каталогах): в
# lite_site модуль включает location в vhost и пишет юнит goamnezia-mcp-http.
# Секрет доступа — только в ответах agent-add / agent-show / agent-rotate;
# agent-list, status и аудит его не содержат.
section "agent-* / mcp-http-*: needs_site в lite → lite_site с отпечатком → выдача, показ, перевыпуск, отзыв → выключение"
export GOAMNEZIA_NGINX_DIR="$TMP/nginx_ag" GOAMNEZIA_SITE_ROOT="$TMP/site" GOAMNEZIA_ACME_ROOT="$TMP/acme"
export GOAMNEZIA_LE_DIR="$TMP/letsencrypt" GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd" GOAMNEZIA_TEMPLATE_LOCAL_DIR="$ROOT/tests/fixtures/site_min"
AGENTS="$GOAMNEZIA_DIR/agents.json"
UNIT="$GOAMNEZIA_SYSTEMD_DIR/goamnezia-mcp-http.service"
VHOST_AG="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
check "начало раздела: режим lite, agents.json нет, юнита нет" '[ "$(jq -r .mode "$CFG")" = lite ] && [ ! -e "$AGENTS" ] && [ ! -e "$UNIT" ]'
act mcp-http-status
expect "mcp-http-status в lite (readonly): success" success
check "mcp-http-status: enabled=false, mode=lite, listen=none, agents=0, hint не пуст, секретов и base_url нет" \
    '[ "$(jget .enabled)" = false ] && [ "$(jget .mode)" = lite ] && [ "$(jget .listen)" = none ] && [ "$(jget .agents)" = 0 ] && [ -n "$(jget .hint)" ] && [ "$(jget .base_url)" = "" ]' "$last"
act mcp-http-on
expect "mcp-http-on в lite без --port → needs_site" error needs_site
check "отказ ничего не включил: mcp.http_enabled не true, юнита нет" '[ "$(jq -c .mcp.http_enabled "$CFG")" != true ] && [ ! -e "$UNIT" ]'
act agent-add --label="Claude Code"
expect "agent-add в lite без транспорта и порта → needs_site (адреса нет)" error needs_site
check "agents.json не создан" '[ ! -e "$AGENTS" ]'
act mcp-http-on --port=99999
expect "mcp-http-on --port=99999 → bad_arg (границы port из actions.json)" error bad_arg
act change-mode --mode=lite_site
expect "change-mode lite_site (стенд): success" success
act mcp-http-on
expect "mcp-http-on в lite_site: success" success
# Дефект 1.1 закрыт: адрес доступа выводится из ga_site_domain_ready, а не из
# названия режима. Во втором режиме сайт стоит на домене с настоящим
# сертификатом, значит и агенту выдаётся домен, а отпечаток пуст — сверять
# нечего, цепочку доверия проверит любой клиент сам.
check "ответ: enabled=true, listen=nginx, via=domain, base_url=https://example.com, tls=false, port=1986, nginx_location=true, reachable=true, unit=goamnezia-mcp-http.service" \
    '[ "$(jget .enabled)" = true ] && [ "$(jget .listen)" = nginx ] && [ "$(jget .via)" = domain ] && [ "$(jget .base_url)" = "https://example.com" ] && [ "$(jget .tls)" = false ] && [ "$(jget .port)" = 1986 ] && [ "$(jget .nginx_location)" = true ] && [ "$(jget .reachable)" = true ] && [ "$(jget .unit)" = goamnezia-mcp-http.service ]' "$last"
fp_ag=$(jget .cert_fingerprint_sha256)
check "ответ: отпечаток пуст — сертификат доверенный, сверять вручную нечего" '[ -z "$fp_ag" ]' "$fp_ag"
check "config.json: mcp.http_enabled=true (булево), site.mcp_location=true; vhost: location ~ \"^/[ar]-[A-Za-z0-9_-]{43}/\" → 127.0.0.1:1986 с access_log off" \
    '[ "$(jq -c .mcp.http_enabled "$CFG")" = true ] && [ "$(jq -c .site.mcp_location "$CFG")" = true ] && grep -qF "location ~ \"^/[ar]-[A-Za-z0-9_-]{43}/\" {" "$VHOST_AG" && grep -qF "proxy_pass http://127.0.0.1:1986;" "$VHOST_AG"' "$(grep -n "a-\[" "$VHOST_AG")"
check "юнит в стендовом systemd: ExecStart python3 /opt/goamnezia-mcp/server.py --http --bind 127.0.0.1 --port 1986, Restart=always; stderr: DRY systemctl enable --now" \
    '[ -f "$UNIT" ] && grep -qF "ExecStart=/usr/bin/env python3 /opt/goamnezia-mcp/server.py --http --bind 127.0.0.1 --port 1986" "$UNIT" && grep -q "^Restart=always" "$UNIT" && grep -q "DRY: systemctl enable --now goamnezia-mcp-http.service" "$ERR"' "$(grep ExecStart "$UNIT"; grep DRY "$ERR")"
act agent-add --label="Claude Code" --scope=write --days=30 --allow-ips=203.0.113.0/24
expect "agent-add в lite_site: success" success
aid=$(jget .id); aurl=$(jget .url)
check "ответ: id a-<hex>, url=https://example.com/a-<43 символа>/mcp, scope=write, enabled=true, via=domain, отпечаток тот же, hint с claude mcp add --transport http, warning" \
    '[[ "$aid" =~ ^a-[a-f0-9]{4,8}$ ]] && [[ "$aurl" =~ ^https://example\.com/a-[A-Za-z0-9_-]{43}/mcp$ ]] && [ "$(jget .scope)" = write ] && [ "$(jget .enabled)" = true ] && [ "$(jget .via)" = domain ] && [ "$(jget .cert_fingerprint_sha256)" = "$fp_ag" ] && grep -qF "claude mcp add --transport http goamnezia $aurl" <<<"$(jget .hint)" && [ -n "$(jget .warning)" ]' "$last"
asecret="${aurl#*/a-}"; asecret="${asecret%/mcp}"
check "ответ: expires через 30 дней, allow_ips=[203.0.113.0/24], calls=0, message с id" \
    '[[ "$(jget .expires)" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]] && [ "$(jget ".allow_ips[0]")" = 203.0.113.0/24 ] && [ "$(jget .calls)" = 0 ] && grep -qF "$aid" <<<"$(jget .message)"' "$last"
check "agents.json: 0600, одна запись, секрет из url; stderr без секрета" \
    '[ "$(perm "$AGENTS")" = 600 ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ] && [ "$(jq -r ".agents[0].secret" "$AGENTS")" = "$asecret" ] && ! grep -qF -- "$asecret" "$ERR"'
act agent-add --label="Кто-то" --scope=root
expect "agent-add --scope=root → bad_arg (enum scope ещё в диспетчере)" error bad_arg
act agent-add --scope=read
expect "agent-add без --label → missing_arg" error missing_arg
act agent-add --label="Только чтение" --scope=read
expect "второй доступ read: success" success
aid2=$(jget .id)
# Доступ через переходник: у него нет ни адреса, ни токена, и ответ моста обязан
# говорить про код привязки, а не отправлять искать пустые поля url и token.
act agent-add --label="Переходник" --scope=read --mode=connect
expect "agent-add --mode=connect: success" success
cid=$(jget .id)
check "ответ connect: код привязки GA-…, url и token пусты, сообщение называет enroll_code, а не url" \
    '[[ "$(jget .enroll_code)" =~ ^GA-([A-HJKMNP-TV-Z2-9]{5}-){3}[A-HJKMNP-TV-Z2-9]{5}$ ]] && [ -z "$(jget .url)" ] && [ -z "$(jget .token)" ] && grep -qF "enroll_code" <<<"$(jget .message)" && ! grep -qE "поле url|url field" <<<"$(jget .message)"' "$last"
act agent-show --id="$cid"
expect "agent-show connect: success" success
check "agent-show connect: кода повторно нет, сообщение про переходник, а не про адрес" \
    '[ "$(jget .enroll_code)" = null ] && grep -qiE "переходник|connector" <<<"$(jget .message)"' "$last"
act agent-rotate --id="$cid"
expect "agent-rotate connect: success" success
check "agent-rotate connect: новый код и сообщение про новую привязку" \
    '[[ "$(jget .enroll_code)" =~ ^GA- ]] && grep -qiE "переходник|connector" <<<"$(jget .message)"' "$last"
act agent-del --id="$cid"
expect "agent-del connect: success" success
act agent-list
expect "agent-list (readonly): success" success
check "agent-list: agents[2] без поля secret и без секрета текстом; state=active; transport.enabled=true с отпечатком" \
    '[ "$(jget ".agents|length")" = 2 ] && [ "$(jget "[.agents[] | has(\"secret\")] | any")" = false ] && ! grep -qF -- "$asecret" <<<"$last" && [ "$(jget "[.agents[] | .state] | unique | join(\",\")")" = active ] && [ "$(jget .transport.enabled)" = true ] && [ "$(jget .transport.cert_fingerprint_sha256)" = "$fp_ag" ]' "$last"
act status
check "status: секрета доступа нет нигде, site.mcp_location=true" '! grep -qF -- "$asecret" <<<"$last" && [ "$(jget .site.mcp_location)" = true ]' "$last"
act agent-show --id="$aid"
expect "agent-show: success" success
check "agent-show: тот же url, отпечаток, transport_enabled=true" '[ "$(jget .url)" = "$aurl" ] && [ "$(jget .cert_fingerprint_sha256)" = "$fp_ag" ] && [ "$(jget .transport_enabled)" = true ]' "$last"
act agent-show --id=a-ffff
expect "agent-show несуществующего → agent_not_found" error agent_not_found
act agent-show --id=zzz
expect "agent-show --id=zzz → bad_arg (pattern agent_id)" error bad_arg
act agent-rotate --id="$aid"
expect "agent-rotate: success" success
aurl2=$(jget .url)
check "agent-rotate: id прежний, url новый; в agents.json старого секрета нет" \
    '[ "$(jget .id)" = "$aid" ] && [ "$aurl2" != "$aurl" ] && [[ "$aurl2" =~ ^https://example\.com/a-[A-Za-z0-9_-]{43}/mcp$ ]] && ! grep -qF -- "$asecret" "$AGENTS"' "$last"
# ── Продление срока через мост ──────────────────────────────────────────────
# Модуль был написан, а действия в описании не существовало: кнопки в боте и
# панели ушли бы в никуда. Продление важно тем, что адрес и токен не меняются,
# то есть клиента агента перенастраивать не надо.
exp_before=$(jget .expires)
act agent-extend --id="$aid" --days=30
expect "agent-extend: success" success
check "срок сдвинулся, прежний назван, секрет в agents.json не тронут" \
    '[ "$(jget .expires)" != "$exp_before" ] && [ "$(jget .old_expires)" = "$exp_before" ] && [ "$(jget .days)" = 30 ] && grep -qF "$(jq -r --arg i "$aid" ".agents[]|select(.id==\$i)|.secret" "$AGENTS")" <<<"$aurl2"' "$last"
act agent-extend --id="$aid" --days=0
expect "agent-extend --days=0: success (срок снят)" success
check "срок снят" '[ -z "$(jget .expires)" ]' "$last"
act agent-extend --id=a-ffff --days=30
expect "agent-extend несуществующего → agent_not_found" error agent_not_found
act agent-extend --id="$aid"
expect "agent-extend без --days → missing_arg" error missing_arg

act agent-disable --id="$aid"
expect "agent-disable: success" success
check "agent-disable: enabled=false; agent-list → state=disabled" '[ "$(jget .enabled)" = false ]' "$last"
act agent-list
check "agent-list после disable: state=disabled у $aid, второй active; transport.agents_active=1" \
    '[ "$(jget ".agents[] | select(.id == \"$aid\") | .state")" = disabled ] && [ "$(jget ".agents[] | select(.id == \"$aid2\") | .state")" = active ] && [ "$(jget .transport.agents_active)" = 1 ]' "$last"
act agent-enable --id="$aid"
expect "agent-enable: success" success
check "agent-enable: enabled=true" '[ "$(jget .enabled)" = true ]' "$last"
act agent-del --id="$aid2"
expect "agent-del: success" success
check "agent-del: deleted=true; в файле одна запись" '[ "$(jget .deleted)" = true ] && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ]' "$last"
act agent-del --id="$aid2"
expect "повторный agent-del → agent_not_found" error agent_not_found
act mcp-http-off
expect "mcp-http-off: success" success
check "mcp-http-off: enabled=false; config mcp.http_enabled=false, site.mcp_location=false; юнит удалён; маршрута в vhost нет, якорь остался; доступ сохранён" \
    '[ "$(jget .enabled)" = false ] && [ "$(jq -c .mcp.http_enabled "$CFG")" = false ] && [ "$(jq -c .site.mcp_location "$CFG")" = false ] && [ ! -e "$UNIT" ] && ! grep -qF "^/[ar]-[A-Za-z0-9_-]{43}/" "$VHOST_AG" && grep -qF "# GOAMNEZIA_MCP_LOCATION" "$VHOST_AG" && [ "$(jq -r ".agents|length" "$AGENTS")" = 1 ]' "$last"
act agent-show --id="$aid"
check "agent-show при выключенном транспорте: url есть, transport_enabled=false, reachable=false" \
    '[ "$(jget .status)" = success ] && [ "$(jget .url)" = "$aurl2" ] && [ "$(jget .transport_enabled)" = false ] && [ "$(jget .reachable)" = false ]' "$last"
act change-mode --mode=lite
expect "финал раздела: change-mode lite: success" success
check "аудит: agent-add/show/rotate/enable/disable/del и mcp-http-on/off с id, без секретов" \
    '_gone=0; for a in agent-add agent-show agent-rotate agent-enable agent-disable agent-del mcp-http-on mcp-http-off; do audit_has "$a" || _gone=1; done; [ "$_gone" = 0 ] && ! grep -qF -- "$asecret" "$GOAMNEZIA_AUDIT_LOG" && ! grep -qF "${aurl2#*/a-}" "$GOAMNEZIA_AUDIT_LOG"' "$(grep agent "$GOAMNEZIA_AUDIT_LOG" | tail -n 8)"
unset GOAMNEZIA_NGINX_DIR GOAMNEZIA_SITE_ROOT GOAMNEZIA_ACME_ROOT GOAMNEZIA_LE_DIR GOAMNEZIA_SYSTEMD_DIR GOAMNEZIA_TEMPLATE_LOCAL_DIR

# ── 14. Спринт 4: импорт установки Amnezia, обновления из upstream, тики таймеров ──
# Контейнеры приложения — фикстуры tests/fixtures/import (docker ps + файлы
# контейнеров), GitHub/Docker Hub — tests/fixtures/upstream. Наш профиль awg2
# живёт в amnezia-awg2, поэтому одноимённый контейнер приложения числится
# «нашим» (managed), а legacy amnezia-awg подхватывается в awg1.
section "import-scan / import-adopt / import-reconcile / upstream-* / core-update / *-tick"
export GOAMNEZIA_DOCKER_PS_FIXTURE="$ROOT/tests/fixtures/import/docker_ps.txt"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR" && cp -R "$ROOT/tests/fixtures/import/fs/." "$GOAMNEZIA_CONTAINER_FS_DIR/"
unset GOAMNEZIA_UPSTREAM_FIXTURE_DIR
SRV_PRIV_FX=$(awk -F' = ' '/^PrivateKey/ {print $2; exit}' "$ROOT/tests/fixtures/import/fs/amnezia-awg2/opt/amnezia/awg/awg0.conf")
act import-scan
expect "import-scan (readonly): success" success
check "import-scan: 3 контейнера amnezia-*, amnezia-awg2 managed → awg2 (reason managed), adoptable=[amnezia-awg], amnezia-xray не поддерживается; секретов нет" \
    '[ "$(jget ".containers|length")" = 3 ] && [ "$(jget ".containers[0].managed")" = true ] && [ "$(jget ".containers[0].profile")" = awg2 ] && [ "$(jget ".adoptable | join(\",\")")" = amnezia-awg ] && [ "$(jget ".containers[2].reason")" = unsupported_container ] && [ "$(jget ".containers[2].kind")" = unsupported ] && ! grep -qF "$SRV_PRIV_FX" <<<"$last"' "$last"
act import-adopt --container=amnezia-awg
expect "import-adopt amnezia-awg (legacy 1.x): success" success
check "ответ: profile awg1, line 1.0, iface wg0, port 51820, users_added 1, users=[Keenetic], hint про перевыпуск; config awg1.container=amnezia-awg" \
    '[ "$(jget .profile)" = awg1 ] && [ "$(jget .line)" = 1.0 ] && [ "$(jget .iface)" = wg0 ] && [ "$(jget .port)" = 51820 ] && [ "$(jget .users_added)" = 1 ] && [ "$(jget ".users[0]")" = Keenetic_doma ] && [ -n "$(jget .hint)" ] && [ "$(jq -r .profiles.awg1.container "$CFG")" = amnezia-awg ] && [ "$(jq -r .profiles.awg1.imported "$CFG")" = true ]' "$last"
check "контейнер не пересоздавался (в stderr нет docker rm/run)" '! grep -q "docker rm\|docker run" "$ERR"'
act user-list
check "user-list: Keenetic с imported=true, без privkey/psk" '[ "$(jget ".users[] | select(.name == \"Keenetic_doma\") | .imported")" = true ] && [ "$(jget ".users[] | select(.name == \"Keenetic_doma\") | .peers.awg1.imported")" = true ] && ! grep -q "privkey\|\"psk\"" <<<"$last"' "$last"
act user-key --name=Keenetic_doma --profile=awg1 --format=conf
expect "user-key импортированному → no_private_key (ключ хранит приложение)" error no_private_key
act import-reconcile --profile=awg1
expect "import-reconcile awg1: success (in_sync)" success
check "in_sync=true, new=[], gone=[]" '[ "$(jget .in_sync)" = true ] && [ "$(jget ".new|length")" = 0 ]' "$last"
act import-reconcile --profile=awg2
expect "import-reconcile awg2 (наш профиль, в контейнере приложения два чужих пира): success" success
check "in_sync=false, new=2 (iPhone Анны, office-router), applied=false, без секретов" \
    '[ "$(jget .in_sync)" = false ] && [ "$(jget ".new|length")" = 2 ] && [ "$(jget .applied)" = false ] && ! grep -q "\"psk\"" <<<"$last"' "$last"
act import-reconcile --profile=awg2 --apply=on
expect "import-reconcile awg2 --apply=on: success" success
check "applied=true, added=2; users.json: iPhone и office-router с пирами awg2 imported" \
    '[ "$(jget .applied)" = true ] && [ "$(jget .added)" = 2 ] && [ "$(jq -r ".users[] | select(.name == \"iPhone_Anny\") | .peers.awg2.imported" "$USERS")" = true ]' "$last"
act import-adopt --container=amnezia-awg
expect "повторный import-adopt → already_managed" error already_managed
act import-adopt --container=amnezia-xray
expect "import-adopt amnezia-xray → unsupported_container" error unsupported_container
act import-adopt --container=nginx-proxy
expect "import-adopt nginx-proxy → bad_arg (pattern container ещё в диспетчере)" error bad_arg
act import-reconcile --profile=awg3
expect "import-reconcile неустановленного → profile_not_installed" error profile_not_installed
# ACC-2: у подхваченного awg1 человек приложения без ключа на сервере — перенос порта (кнопка «Сменить порт» /
# «Занять UDP/443» в боте и панели, инструмент агента) без согласия отклоняется с объяснением.
act profile-port --profile=awg1 --port=40111
expect "ACC-2 profile-port подхваченного awg1 с человеком без ключа → adopted_keyless" error adopted_keyless
check "ACC-2 порт не сменён; в тексте — сколько людей, почему им не переслать и как подтвердить" \
    '[ "$(jq -r .profiles.awg1.port "$CFG")" = 51820 ] && grep -qF "(1)" <<<"$(jget .message)" && grep -qF "adopted_confirm=awg1" <<<"$(jget .message)"' "$last"
act profile-port --profile=awg1 --port=40111 --adopted-confirm=awg2
expect "ACC-2 согласие именем другого профиля не принимается" error adopted_keyless
act profile-port --profile=awg1 --port=40111 --adopted-confirm=awg9
expect "ACC-2 adopted_confirm не из списка профилей → bad_arg ещё в диспетчере" error bad_arg
act profile-port --profile=awg1 --port=40111 --adopted-confirm=awg1
expect "ACC-2 с --adopted-confirm=awg1: success" success
check "ACC-2 порт сменён" '[ "$(jq -r .profiles.awg1.port "$CFG")" = 40111 ] && [ "$(jget .port)" = 40111 ]' "$last"
act profile-port --profile=awg1 --port=51820 --adopted-confirm=awg1
expect "ACC-2 и обратно на 51820: success" success

act upstream-check
expect "upstream-check без сети → unreachable" error unreachable
check "unreachable: profiles с awg2 и awg1 (текущие теги), updates_available=false" '[ "$(jget ".profiles|length")" = 2 ] && [ "$(jget .updates_available)" = false ]' "$last"
export GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$ROOT/tests/fixtures/upstream"
act upstream-check
expect "upstream-check с фикстурой Docker Hub/GitHub: success" success
NEW_TAG=3.2.20260915
check "updates_available=true, релиз 5.0.2.1 → 5.0.3.0, awg2 available $NEW_TAG, unsupported содержит CONTENT_PADDING_MAX, hint про upstream-update" \
    '[ "$(jget .updates_available)" = true ] && [ "$(jget .client_release.tag)" = 5.0.3.0 ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .available_tag")" = $NEW_TAG ] && [ "$(jget "[.unsupported[].name] | unique | join(\",\")")" = CONTENT_PADDING_MAX ] && grep -q "upstream-update" <<<"$(jget .message)"' "$last"
check "awg1 (legacy, образ amnezia-wg): available_tag latest, image_key amnezia-wg" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg1\") | .image_key")" = amnezia-wg ] && [ "$(jget ".profiles[] | select(.profile == \"awg1\") | .available_tag")" = latest ]' "$last"
act upstream-update --profile=awg2
expect "upstream-update awg2: success" success
check "ответ: image_tag $NEW_TAG, previous_tag 3.1.20260828, client_release 5.0.3.0, scripts.changed 3, backup; config.json и images.json обновлены; ключи Анны... пользователей прежние" \
    '[ "$(jget .image_tag)" = $NEW_TAG ] && [ "$(jget .previous_tag)" = 3.1.20260828 ] && [ "$(jget .client_release)" = 5.0.3.0 ] && [ "$(jget ".scripts.changed|length")" = 3 ] && [[ "$(jget .backup)" == goamnezia_backup_* ]] && [ "$(jq -r .profiles.awg2.image_tag "$CFG")" = $NEW_TAG ] && [ "$(jq -r .profiles.awg2.previous.image_tag "$CFG")" = 3.1.20260828 ] && [ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = $NEW_TAG ] && grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf"' "$last"
check "stderr обновления: DRY pull по дайджесту, rm/run с портом профиля, cp ключей, awg show; приватных ключей нет" \
    'grep -q "DRY: docker pull amneziavpn/amneziawg-go@sha256:" "$ERR" && grep -q "DRY: docker stop amnezia-awg2" "$ERR" && grep -q -- "-p $(jq -r .profiles.awg2.port "$CFG"):" "$ERR" && grep -q "awg show awg0" "$ERR" && ! grep -q "PrivateKey" "$ERR"' "$(grep DRY "$ERR" | head -6)"
act upstream-update --profile=awg2
expect "повторный upstream-update → already_current" error already_current
act upstream-update --profile=awg2 --tag="bad tag"
expect "upstream-update --tag с пробелом → bad_arg" error bad_arg
act upstream-update --profile=awg2 --tag=9.9.9
expect "upstream-update неизвестный тег → tag_not_found" error tag_not_found
act upstream-rollback --profile=awg2
expect "upstream-rollback awg2: success" success
check "откат: image_tag 3.1.20260828, scripts_restored=true; config previous=null; images.json tag 3.1.20260828, client_release 5.0.2.1; template.conf без нового плейсхолдера" \
    '[ "$(jget .image_tag)" = 3.1.20260828 ] && [ "$(jget .scripts_restored)" = true ] && [ "$(jq -c ".profiles.awg2.previous" "$CFG")" = null ] && [ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = 3.1.20260828 ] && [ "$(jq -r .client_release "$UPSTREAM_DIR/images.json")" = 5.0.2.1 ] && ! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf"' "$last"
act upstream-rollback --profile=awg2
expect "повторный откат → no_previous" error no_previous
act core-update --profile=awg2
expect "core-update awg2 (закреплённый образ, ремонт): success" success
check "core-update: repair=true, image_tag 3.1.20260828" '[ "$(jget .repair)" = true ] && [ "$(jget .image_tag)" = 3.1.20260828 ]' "$last"
act quota-tick
expect "quota-tick: success" success
check "quota-tick: tick=quota, users — число" '[ "$(jget .tick)" = quota ] && [[ "$(jget .users)" =~ ^[0-9]+$ ]]' "$last"
act watch-tick
expect "watch-tick: success" success
check "watch-tick: awg2 (контейнер-фикстура) running/none; awg1 (amnezia-awg) running" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .state")" = running ] && [ "$(jget ".profiles[] | select(.profile == \"awg1\") | .state")" = running ]' "$last"
act stats-tick
expect "stats-tick: success" success
act upstream-tick
expect "upstream-tick: success" success
check "upstream-tick: updates_available=true, notified=true, событие upstream_available в очереди" \
    '[ "$(jget .updates_available)" = true ] && [ "$(jget .notified)" = true ] && ls "$GOAMNEZIA_NOTIFY_DIR"/*.json >/dev/null 2>&1 && grep -lq upstream_available "$GOAMNEZIA_NOTIFY_DIR"/*.json' "$last"
# L-07: итог суточной проверки — в status и полным отчётом без сети
act status
check "status.upstream: checked, reachable=true, updates_available=true, awg2 с available_tag $NEW_TAG, релиз 5.0.3.0" \
    '[ -n "$(jget .upstream.checked)" ] && [ "$(jget .upstream.reachable)" = true ] && [ "$(jget .upstream.updates_available)" = true ] && [ "$(jget ".upstream.profiles[] | select(.profile == \"awg2\") | .available_tag")" = "$NEW_TAG" ] && [ "$(jget .upstream.client_release.latest)" = 5.0.3.0 ]' "$last"
up_checked=$(jget .upstream.checked)
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/nowhere" act upstream-check --cached=on
expect "upstream-check --cached=on: success без похода в сеть" success
check "cached=true, отчёт той же проверки (checked совпадает), summary = status.upstream" \
    '[ "$(jget .cached)" = true ] && [ "$(jget .checked)" = "$up_checked" ] && [ "$(jget .summary.checked)" = "$up_checked" ] && [ "$(jget ".profiles|length")" -ge 1 ] && ! grep -q "DRY: curl" "$ERR"' "$last"
act traffic
check "traffic: today/month объекты, days массив" '[ "$(jget ".today|type")" = object ] && [ "$(jget ".month|type")" = object ] && [ "$(jget ".days|type")" = array ]' "$last"
act quality
check "quality.timers: девять таймеров — свои пять и чужие четыре (owned=false)" \
    '[ "$(jget ".quality.timers|length")" = 9 ] && [ "$(jget "[.quality.timers[] | select(.owned)] | length")" = 5 ] && [ "$(jget "[.quality.timers[] | select(.name == \"backup\")] | length")" = 1 ]' "$last"
check "аудит спринта 4: import-adopt, import-reconcile, upstream-update, upstream-rollback, core-update" \
    '_gone=0; for a in import-adopt import-reconcile upstream-update upstream-rollback core-update; do audit_has "$a" || _gone=1; done; [ "$_gone" = 0 ]'

# ── 15. Линейка профиля — из его параметров, а не из имени (docs/archive/AUDIT.md §15 п.5) ────
# Приложение Amnezia ставит контейнер amnezia-awg2 любой линейки. Подхваченный
# профиль обязан жить в линейке своего конфига: 3.x-параметры контейнера
# редактируются, а тот же ключ на свежем awg2 (2.0) отвергается как прежде.
section "profile-set-param у подхваченного профиля: линейка из config.json"
act profile-remove --profile=awg2 --confirm=awg2
expect "profile-remove awg2 (освобождаем слот под подхват): success" success
cp "$ROOT/tests/fixtures/import/awg2_conf_line31.txt" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg/awg0.conf"
act import-adopt --container=amnezia-awg2
expect "import-adopt amnezia-awg2 с 3.x-конфигом: success" success
check "ответ и config.json: profile awg2, line 3.1 (а не 2.0 по имени), preset imported" \
    '[ "$(jget .profile)" = awg2 ] && [ "$(jget .line)" = 3.1 ] && [ "$(jq -r .profiles.awg2.line "$CFG")" = 3.1 ] && [ "$(jq -r .profiles.awg2.preset "$CFG")" = imported ]' "$last"
act profiles-status
check "profiles-status показывает у awg2 линейку 3.1" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .line")" = 3.1 ]' "$last"
HPK31=$(python3 "$ROOT/lib/awgkeys.py" pubkey "$(python3 "$ROOT/lib/awgkeys.py" genkey)")
act profile-set-param --profile=awg2 --key=HeaderProtectionKey --value="$HPK31"
expect "set-param HeaderProtectionKey у подхваченного awg2 (3.1): success" success
check "ответ: line 3.1, scope server, reissue_required=true; ключ дошёл до awg0.conf" \
    '[ "$(jget .result.line)" = 3.1 ] && [ "$(jget .result.scope)" = server ] && [ "$(jq -c .reissue_required <<<"$last")" = true ] && grep -q "^HeaderProtectionKey = $HPK31$" "$SD2/awg0.conf"' "$last"
act profile-set-param --profile=awg2 --key=RandomTrailers --value=off
expect "set-param RandomTrailers=off (тоже 3.x) у подхваченного awg2: success" success
act profile-regen --profile=awg2 --preset=default --confirm=awg2
expect "profile-regen подхваченного awg2: success" success
check "regen оставил профиль в 3.1 (пресет работает в фактической линейке)" \
    '[ "$(jq -r .profiles.awg2.line "$CFG")" = 3.1 ] && [ -n "$(jq -r .profiles.awg2.params.HeaderProtectionKey "$CFG")" ]' "$(jq -c .profiles.awg2.line "$CFG")"

# Свежая установка не изменилась: awg2 — это 2.0, и 3.x-поля в него не проходят.
act profile-remove --profile=awg2 --confirm=awg2
expect "profile-remove подхваченного awg2: success" success
# На сервере profile-remove удаляет и контейнер, а установка ставит новый; стенд
# каталог-фикстуру не трогает. Убираем её на время установки — иначе страж
# увидел бы «чужой контейнер с людьми» (foreign_container) — и возвращаем.
mv "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2" "$TMP/awg2.removed"
act profile-install --profile=awg2 --port=40002
mv "$TMP/awg2.removed" "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2"
expect "profile-install awg2 заново: success" success
check "свежий awg2 снова в линейке 2.0" '[ "$(jget .result.line)" = 2.0 ] && [ "$(jq -r .profiles.awg2.line "$CFG")" = 2.0 ]' "$last"
act profile-set-param --profile=awg2 --key=HeaderProtectionKey --value="$HPK31"
expect "тот же 3.x-ключ на свежем awg2 → params_invalid (как раньше)" error params_invalid

# Старая установка без поля line: линейку выводим по фактическим параметрам.
jq '.profiles.awg2.line = ""' "$CFG" >"$TMP/cfg.tmp" && mv "$TMP/cfg.tmp" "$CFG" && chmod 600 "$CFG"
act profiles-status
check "профиль без поля line: линейка выведена по params (2.0), а не потеряна" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .line")" = 2.0 ]' "$last"
act user-add --name=lena --profiles=awg2
expect "user-add lena в awg2 без поля line: success" success
act user-key --name=lena --profile=awg2 --format=conf
check "ключ выдаётся в выведенной линейке 2.0 (min_client 4.8.12.9)" \
    '[ "$(jget .line)" = 2.0 ] && [ "$(jget .min_client)" = 4.8.12.9 ]' "$last"
unset GOAMNEZIA_DOCKER_PS_FIXTURE GOAMNEZIA_CONTAINER_FS_DIR GOAMNEZIA_UPSTREAM_FIXTURE_DIR

# ── Логи: отказ обязан отличаться от пустого лога (DEFECTS §3.8) ─────────────
#
# Раньше мост на любую беду отвечал success с log="": «контейнера нет» и «логи
# у контейнера выключены при запуске» выглядели ровно как «записей не было».
# Демона подменяем стоящим в PATH скриптом — ответы у него дословно те, что
# печатает настоящий docker.
section "логи: причина вместо пустоты"
LOGBIN="$TMP/logbin"; mkdir -p "$LOGBIN"
PATH_BEFORE_LOGS="$PATH"
PATH="$LOGBIN:$PATH"
fake_docker() { printf '#!/bin/sh\ncase "$1" in\n  logs) printf "%%s\\n" %s >&2; exit %s ;;\n  *) exit 0 ;;\nesac\n' "$1" "${2:-1}" >"$LOGBIN/docker"; chmod +x "$LOGBIN/docker"; }

fake_docker "'Error response from daemon: No such container: amnezia-awg2'"
act logs-tail --component=awg2
expect "контейнера нет → error logs_no_container" error logs_no_container
check "… и причина названа словами, а не кодом" \
    '[ -n "$(jget .message)" ] && [ "$(jget .message)" != null ] && [ "$(jget .message)" != ok ]' "$last"

fake_docker "'Error response from daemon: configured logging driver does not support reading'"
act logs-tail --component=awg2
expect "логи контейнера выключены (--log-driver none) → error logs_driver_unreadable" error logs_driver_unreadable

fake_docker "'Cannot connect to the Docker daemon at unix:///var/run/docker.sock'"
act logs-tail --component=awg2
expect "демон не отвечает → error logs_docker_down" error logs_docker_down

# Живой лог: только тут success — и строки на месте.
printf '#!/bin/sh\ncase "$1" in logs) printf "%%s\\n" "wg0: peer added" ;; *) exit 0 ;; esac\n' >"$LOGBIN/docker"
chmod +x "$LOGBIN/docker"
act logs-tail --component=awg2 --lines=10
expect "лог прочитан → success" success
check "… строки дошли до ответа" '[ "$(jget .log)" = "wg0: peer added" ]' "$last"

# Служба systemd: «юнита нет» — тоже не пустота.
printf '#!/bin/sh\nexit 1\n' >"$LOGBIN/systemctl"; chmod +x "$LOGBIN/systemctl"
printf '#!/bin/sh\nexit 0\n' >"$LOGBIN/journalctl"; chmod +x "$LOGBIN/journalctl"
act logs-tail --component=bot
expect "службы бота на сервере нет → error logs_no_unit" error logs_no_unit
printf '#!/bin/sh\nexit 0\n' >"$LOGBIN/systemctl"
printf '#!/bin/sh\nprintf "%%s\\n" "bot started"\n' >"$LOGBIN/journalctl"
act logs-tail --component=bot
expect "служба есть, журнал читается → success" success
check "… строка журнала дошла до ответа" '[ "$(jget .log)" = "bot started" ]' "$last"
printf '#!/bin/sh\necho "Failed to add match: Invalid argument" >&2\nexit 1\n' >"$LOGBIN/journalctl"
act logs-tail --component=system
expect "журнал не читается → error logs_read_failed" error logs_read_failed

# Журнал действий. Обычно его заводит init_dirs при первом же запуске, поэтому
# «файла нет» бывает там, где его нельзя создать (каталога не существует, прав
# нет) — и это тоже не пустота.
GOAMNEZIA_AUDIT_LOG="$TMP/nodir/audit.log" act logs-tail --component=mcp
expect "журнал действий завести негде → error logs_absent" error logs_absent
printf '%s\tssh\tuser-add\tanna\n' "2026-09-12T00:00:00Z" >"$TMP/audit-lines.log"
GOAMNEZIA_AUDIT_LOG="$TMP/audit-lines.log" act logs-tail --component=mcp
expect "журнал действий есть → success" success
check "… строка журнала дошла до ответа" '[ -n "$(jget .log)" ] && [ "$(jget .log)" != null ]' "$last"

# Чужое имя отсекает сам мост, до чтения: список имён один и живёт в actions.json.
act logs-tail --component=kernel
expect "неизвестный компонент → error bad_arg (enum моста)" error bad_arg

PATH="$PATH_BEFORE_LOGS"
rm -rf "$LOGBIN"

# ── logs-status: участки диска, а не компоненты (DEFECTS §3.9) ───────────────
act logs-status
if [ "$(jget .status)" = success ]; then
    check "logs-status отдаёт areas[] с полем area, а не components[]" \
        '[ "$(jget ".areas | type")" = array ] && [ "$(jget .components)" = null ]' "$last"
    check "имена участков не путаются с источниками логов logs-tail" \
        '[ -z "$(jget ".areas[].area" | grep -x -e awg1 -e awg2 -e awg3 -e bot -e admin -e mcp -e system)" ]' "$last"
else
    skipped "logs-status: участки диска" "модуль logbudget недоступен в этом окружении"
fi

# ── Строгость моста: чужой аргумент и пропущенный обязательный ───────────────
#
# Мост молча глотал неизвестные имена (`--bogus=1` → success), и опечатка в
# имени параметра тихо теряла значение, тогда как MCP такое отвергал всегда
# (DEFECTS §5.2).
section "строгость аргументов"
act quality --bogus=1
expect "чужой аргумент → error bad_arg" error bad_arg
check "… в причине названы и действие, и аргумент" \
    '[[ "$(jget .message)" == *quality* ]] && [[ "$(jget .message)" == *bogus* ]]' "$last"
act quality --confirm=quality
expect "confirm разрешён любому действию" success
act endpoint-set
expect "endpoint-set без host → error missing_arg" error missing_arg
act endpoint-set --host=
expect "endpoint-set с ПУСТЫМ host → success (сброс на IP сервера)" success
# Бюджет логов не знает сухого прогона (работает по /var/log, /run и журналу машины): от root на
# сервере он бы подрезал настоящие журналы. Пороги — такие, что резать нечего, состояние — в $TMP.
LOGB_COMPONENT_MB=999999 LOGB_LIVE_MB=999999 LOGB_KEEP_FREE_MB=0 LOGB_STATE="$TMP/logbudget.state" act logs-enforce
expect "logs-enforce: бюджет применён руками → success" success
# L1-02: сухой прогон меряет, но не режет — и меряет каталоги стенда, а не /var/log машины прогона.
l102="$TMP/l102"; mkdir -p "$l102/varlog" "$l102/nginx" "$l102/journal"
head -c 2200000 /dev/zero >"$l102/varlog/goamnezia-audit.log.1.gz"
head -c 2200000 /dev/zero >"$l102/nginx/access.log.2.gz"
LOGB_COMPONENT_MB=1 LOGB_VAR_LOG_DIR="$l102/varlog" LOGB_NGINX_LOG_DIR="$l102/nginx" LOGB_JOURNAL_DIR="$l102/journal" \
    LOGB_STATE="$l102/state" act logs-enforce
check "L1-02 logs-enforce в сухом прогоне: DRY-строки подрезки каталогов стенда, файлы на месте, состояние не записано" \
    '[ "$(jget .status)" = success ] && grep -q "DRY: _logb_trim_dir $l102/varlog goamnezia\*" "$ERR" && grep -q "DRY: _logb_trim_dir $l102/nginx access.log\* error.log\*" "$ERR" && [ -s "$l102/varlog/goamnezia-audit.log.1.gz" ] && [ -s "$l102/nginx/access.log.2.gz" ] && [ ! -e "$l102/state" ] && ! grep -q "_logb_trim_dir /var/log" "$ERR"' \
    "$last $(grep DRY "$ERR" | head -5)"
act logs-status
if [ "$(jget .status)" = success ]; then
    check "свободное место отдельным полем free_mb, а не строкой в areas" \
        '[ "$(jget ".areas[]? | select(.area == \"free\") | .area")" = "" ]' "$last"
fi

# ── Стражи образца: экраны не печатают человеку чужой машинный вывод ─────────
section "стражи образца"
# `systemctl status` — английский текст с CGroup, Tasks и Memory. Человеку,
# который нажал «Статус службы», нужна карточка (_svc_card), а не дамп демона
# (DEFECTS §3.12).
# Строки комментариев не в счёт: про сам образец в коде написано словами.
raw_systemctl() { grep -nE '^[^#]*systemctl status' "$ROOT/install.sh"; }
check "ни один экран не печатает сырой systemctl status" \
    '! raw_systemctl' "$(raw_systemctl | head -3)"
# Голое `>"$путь" 2>/dev/null` гасит stderr КОМАНДЫ, но не ошибку самого
# ПЕРЕНАПРАВЛЕНИЯ: её печатает оболочка до запуска команды, и человек получает
# сырую строку «Permission denied» посреди экрана. Лечится порядком (2>/dev/null
# раньше редиректа) или скобками вокруг всей конструкции. Страж стоял только на
# lib/share.sh — теперь на всём коде (DEFECTS §5.1).
naked_file_redir() {
    "${PY:-python3}" - "$ROOT" <<'PYEOF'
import io, re, sys, glob, os
root = sys.argv[1]
pat = re.compile(r'(?<!<)(?<!<<)[<>]"\$[A-Za-z_][^"]*" +2>/dev/null')
bad = 0
for p in sorted(glob.glob(os.path.join(root, 'lib', '*.sh'))) + [os.path.join(root, 'install.sh')]:
    for i, l in enumerate(io.open(p, encoding='utf-8').read().split('\n'), 1):
        if l.strip().startswith('#') or '<<<' in l:
            continue
        if pat.search(l):
            print('%s:%d %s' % (os.path.relpath(p, root), i, l.strip()[:100]))
            bad += 1
sys.exit(1 if bad else 0)
PYEOF
}
check "ни одного файлового перенаправления с голым 2>/dev/null во всём коде" \
    'naked_file_redir' "$(naked_file_redir | head -3)"
# `${массив[*]}` склеивает ПЕРВЫМ знаком IFS: «IFS=', '» давало «a,b» без
# пробела, и список в предупреждении читался как одно слово.
ifs_join() { grep -nE "IFS=., .;.*\\[\\*\\]" "$ROOT/install.sh"; }
check "нет склейки списка через IFS с двузнаковым разделителем" \
    '! ifs_join' "$(ifs_join | head -3)"

# ── 15. Миграция после обновления (UP-1, UP-2, UP-4, UP-7, UP-8, UP-9, SR-04, SR-31, SR-38, LC-17) ──
# Первым пишущим действием моста (как тик сторожа) — со старой версией в config.json.
section "миграция после обновления: мало места, выключенные прежней версией, отметка установки"
MIG_CFG=$(cat "$CFG"); MIG_USERS=$(cat "$GOAMNEZIA_DIR/users.json" 2>/dev/null)
[ -n "$MIG_USERS" ] || MIG_USERS='{"schema":1,"users":[]}'
mig_reset() {   # <версия> — config.json «от старой версии», без отметок прошлой миграции
    jq --arg v "$1" '.version = $v' <<<"$MIG_CFG" >"$CFG"; chmod 600 "$CFG"
    rm -f "$GOAMNEZIA_DIR"/.migrated_* "$GOAMNEZIA_DIR"/.preupgrade_* "$GOAMNEZIA_DIR/.changelog_pending"
}
MIGBIN="$TMP/migbin"; mkdir -p "$MIGBIN"
# Свободно 100 МБ: меньше прежнего порога снимка (512 МБ), но снимку в единицы мегабайт хватает. Фикстура места
# прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на эти вызовы снята — место говорит подменённый df.
cat >"$MIGBIN/df" <<'EOS'
#!/bin/sh
printf 'Filesystem 1M-blocks Used Available Use%% Mounted on\n/dev/vda 20000 19900 100 99%% /\n'
EOS
chmod +x "$MIGBIN/df"
mig_reset 0.4.2
# Выключен лимитом ещё прежней версией: срок истёк, disabled_reason нет. И отметки установки нет, хотя профили есть.
jq '.users += [{name: "oldtick", label: "oldtick", created: "2026-01-01T00:00:00Z", enabled: false, quota_bytes: 0,
     expires: "2026-02-01T00:00:00Z", traffic: {rx: 0, tx: 0, last_rx: 0, last_tx: 0, updated: ""}, peers: {}}]' \
    <<<"$MIG_USERS" >"$GOAMNEZIA_DIR/users.json"; chmod 600 "$GOAMNEZIA_DIR/users.json"
jq '.installed = false' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"; chmod 600 "$CFG"
mig_bk_before=$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
GOAMNEZIA_DF_FIXTURE='' PATH="$MIGBIN:$PATH" act user-add --name=migtick
check "UP-1 миграция проходит при 100 МБ свободного: версия в config.json новая" \
    '[ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [ -f "$GOAMNEZIA_DIR/.migrated_$GA_VER" ]' "version=$(jq -r .version "$CFG") $(errs)"
mig_bk=$(ls -t "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | head -1)
# Карточку читаем распаковкой в каталог, а не `tar -O --wildcards`: у bsdtar (macOS) такого ключа нет,
# и проверка краснела на Mac при исправном снимке.
rm -rf "$TMP/mig_unp"; mkdir -p "$TMP/mig_unp"; tar xzf "$mig_bk" -C "$TMP/mig_unp" 2>/dev/null
mig_meta=$(cat "$TMP/mig_unp"/*/metadata.json 2>/dev/null)
check "UP-8 снимок перед обновлением подписан версией СОСТОЯНИЯ (0.4.2) и помечен kind=preupgrade" \
    '[ "$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz | wc -l | tr -d " ")" -gt "$mig_bk_before" ] && jq -e ".goamnezia_version == \"0.4.2\" and .kind == \"preupgrade\"" <<<"$mig_meta" >/dev/null' \
    "$mig_meta"
check "LC-17 путь снимка назван человеку" 'grep -qF "Снимок перед обновлением: $mig_bk" "$ERR"' "$(errs)"
check "UP-9 «Что нового» отмечено только после удавшейся миграции" \
    '[ "$(cat "$GOAMNEZIA_DIR/.changelog_pending" 2>/dev/null)" = 0.4.2 ]' "$(cat "$GOAMNEZIA_DIR/.changelog_pending" 2>/dev/null)"
check "UP-2 выключенному прежней версией проставлена причина по действующему лимиту (expiry)" \
    '[ "$(jq -r ".users[] | select(.name == \"oldtick\") | .disabled_reason" "$GOAMNEZIA_DIR/users.json")" = expiry ]' \
    "$(jq -c ".users[] | select(.name == \"oldtick\")" "$GOAMNEZIA_DIR/users.json")"
check "UP-4 профили есть, отметки установки не было — поставлена" '[ "$(jq -r .installed "$CFG")" = true ]' "$(jq -c "{installed}" "$CFG")"
act user-expiry --name=oldtick --days=30
check "UP-2 продление включает такого человека само (re_enabled)" \
    '[ "$(jget .re_enabled)" = true ] && [ "$(jq -r ".users[] | select(.name == \"oldtick\") | .enabled" "$GOAMNEZIA_DIR/users.json")" = true ]' "$last $(errs)"
check "UP-7 в отметке миграции — отпечаток кода" '[[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]]'
# Код сменился при той же версии (обновление легло не целиком, пока тикал таймер) — идемпотентные шаги ещё раз.
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
mig_bk_before=$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
act user-add --name=migtick2
check "UP-7 код на диске другой, чем при миграции: отметка переписана новым отпечатком, снимок не повторялся" \
    '[[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]] && [ "$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz | wc -l | tr -d " ")" = "$mig_bk_before" ] && ! grep -qF "Миграция состояния" "$ERR"' "$(errs)"
# Сорвавшаяся миграция: причина — битый users.json, а не «освободите место»; «Что нового» не отмечено.
mig_reset 0.4.2
cp "$GOAMNEZIA_DIR/users.json" "$TMP/users.mig.ok"; head -c 20 "$TMP/users.mig.ok" >"$GOAMNEZIA_DIR/users.json"
act user-add --name=migfail
check "SR-31 снимок не снялся из-за битого users.json — названа причина, версия прежняя, «Что нового» не отмечено" \
    '[ "$(jq -r .version "$CFG")" = 0.4.2 ] && [ ! -f "$GOAMNEZIA_DIR/.changelog_pending" ] && grep -qF "Причина:" "$ERR" && ! grep -qF "Освободите место" "$ERR"' "$(errs)"
check "SR-04 без вопроса «Продолжить обновление?» — миграция не держит замок в ожидании ответа" \
    '! grep -q "mig_plan_ask\|confirm \"\$(t mig_plan_ask)\"" "$INSTALL"'
# UP-6: корень сайта бывает ссылкой на каталог владельца — find по нему без -mindepth 1 удалял саму ссылку.
up6_fn=$(awk '/^_migrate_site_vhost\(\)/,/^}/' "$INSTALL")
check "UP-6 уборка ссылок при миграции: корень разворачивается (readlink -f), сам не удаляется (-mindepth 1), число названо" \
    'grep -q "readlink -f" <<<"$up6_fn" && grep -q -- "-mindepth 1 -type l -delete" <<<"$up6_fn" && ! grep -q "find \"\$root\" -type l -delete" <<<"$up6_fn" && grep -q mig_site_symlinks_removed <<<"$up6_fn"'
cp "$TMP/users.mig.ok" "$GOAMNEZIA_DIR/users.json"; chmod 600 "$GOAMNEZIA_DIR/users.json"
# SR-38: несуществующее действие миграцию не запускает.
mig_bk_before=$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
act no-such-action
check "SR-38 опечатка в имени действия — отказ, без миграции и снимка" \
    '[ "$rc" != 0 ] && [ "$(jq -r .version "$CFG")" = 0.4.2 ] && [ "$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d " ")" = "$mig_bk_before" ]' "rc=$rc $last"
# SEC-NET-1: обновление с 0.4.5 сразу ставит изоляцию клиентов в работающие контейнеры профилей (не дожидаясь
# сторожа) и говорит об этом человеку; status отдаёт настройку только для показа.
mig_reset 0.4.5
mig_cfs="$TMP/migcfs"; mkdir -p "$mig_cfs"
for _c in $(jq -r '.profiles[].container' "$CFG"); do mkdir -p "$mig_cfs/$_c"; done
GOAMNEZIA_CONTAINER_FS_DIR="$mig_cfs" GOAMNEZIA_DF_FIXTURE='' PATH="$MIGBIN:$PATH" act lang-set --lang=ru
iso_mig_n=$(grep -c "sh -c 'iptables -C FORWARD -i [a-z]*0 -o [a-z]*0 -j DROP" "$ERR")
check "SEC-NET-1 миграция 0.4.5 → $GA_VER: правило изоляции — в каждый работающий контейнер профиля, человеку сказано" \
    '[ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [ "$iso_mig_n" -gt 0 ] && [ "$iso_mig_n" = "$(jq -r ".profiles | length" "$CFG")" ] && grep -qE "(Изоляция клиентов|Client isolation)[^(]*\($iso_mig_n\)" "$ERR"' \
    "n=$iso_mig_n profiles=$(jq -r ".profiles | keys | join(\",\")" "$CFG") $(grep "DROP" "$ERR" | head -4)"
GOAMNEZIA_CONTAINER_FS_DIR="$mig_cfs" act status
check "SEC-NET-1 status: client_isolation=true — и в корне, и в карточке каждого профиля" \
    '[ "$(jget .client_isolation)" = true ] && [ "$(jget "[.profiles[].client_isolation] | all")" = true ]' "$last"
# ACC-4: у подхваченного legacy-профиля (wg0) прежние версии держали копию конфига в state/ под wg0.conf, а
# восстановление, «Поднять контейнер заново» и обновление ядра ищут awg0.conf. Миграция переименовывает её
# один раз (0600, байт в байт) — и при переходе версии, и при смене кода той же версии (UP-7: серверы, где
# 0.4.6 стоял до починки). Legacy на стенде — awg2 с iface wg0 в config.json. Строку человеку ищем по куску,
# одинаковому в ru и en («awg0.conf (awg2)»): словаря в оболочке прогона нет, и "$(tf …)" здесь пуст — а
# grep по пустому образцу совпал бы с любым выводом.
acc4_sd="$GOAMNEZIA_DIR/state/$(jq -r .profiles.awg2.container "$CFG")"
cp -p "$acc4_sd/awg0.conf" "$TMP/acc4.conf"
mig_reset 0.4.5
jq '.profiles.awg2.iface = "wg0"' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"; chmod 600 "$CFG"
mv "$acc4_sd/awg0.conf" "$acc4_sd/wg0.conf"
GOAMNEZIA_DF_FIXTURE='' PATH="$MIGBIN:$PATH" act lang-set --lang=ru
check "ACC-4 миграция 0.4.5 → $GA_VER: state/…/wg0.conf стала awg0.conf (0600, байт в байт), человеку сказано" \
    '[ -f "$acc4_sd/awg0.conf" ] && [ ! -e "$acc4_sd/wg0.conf" ] && cmp -s "$acc4_sd/awg0.conf" "$TMP/acc4.conf" && [ "$(perm "$acc4_sd/awg0.conf")" = 600 ] && grep -qF "awg0.conf (awg2)" "$ERR"' \
    "$(ls "$acc4_sd" | tr "\n" " ") $(errs)"
mv "$acc4_sd/awg0.conf" "$acc4_sd/wg0.conf"
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
act lang-set --lang=ru
check "ACC-4 UP-7: та же версия, код другой — копия под wg0.conf тоже приводится к awg0.conf" \
    '[ -f "$acc4_sd/awg0.conf" ] && [ ! -e "$acc4_sd/wg0.conf" ] && cmp -s "$acc4_sd/awg0.conf" "$TMP/acc4.conf" && [ "$(perm "$acc4_sd/awg0.conf")" = 600 ] && grep -qF "awg0.conf (awg2)" "$ERR"' \
    "$(ls "$acc4_sd" | tr "\n" " ") $(errs)"
act lang-set --lang=ru
check "ACC-4 повторный вход: приводить нечего — ни строки человеку, копия на месте" \
    '! grep -qF "awg0.conf (awg2)" "$ERR" && [ ! -e "$acc4_sd/wg0.conf" ] && cmp -s "$acc4_sd/awg0.conf" "$TMP/acc4.conf"' "$(errs)"
# M2: откат — код МЛАДШЕ состояния (переезд на ветку с версией ниже, dev → main). Снимок снимается заново, хотя
# отметка снимка этой версии уже есть, отметки прочих версий сняты (следующее обновление вверх тоже со снимком),
# «Что нового» не отмечается (и прежняя отметка убрана), человеку сказано, что это откат. Строку ищем по номеру
# версии — он одинаков в ru и en.
mig_reset 99.0.0
touch "$GOAMNEZIA_DIR/.preupgrade_${GA_VER}_done" "$GOAMNEZIA_DIR/.preupgrade_99.0.0_done"
printf '0.4.2\n' >"$GOAMNEZIA_DIR/.changelog_pending"
mig_bk_before=$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
GOAMNEZIA_DF_FIXTURE='' PATH="$MIGBIN:$PATH" act lang-set --lang=ru
check "M2 откат 99.0.0 → $GA_VER: версия состояния — версия кода, снимок снят заново, «Что нового» нет, сказано «откат»" \
    '[ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [ "$(ls "$GOAMNEZIA_DIR/backups"/*.tar.gz | wc -l | tr -d " ")" -gt "$mig_bk_before" ] && [ ! -f "$GOAMNEZIA_DIR/.changelog_pending" ] && [ ! -e "$GOAMNEZIA_DIR/.preupgrade_99.0.0_done" ] && [ -f "$GOAMNEZIA_DIR/.preupgrade_${GA_VER}_done" ] && grep -qF "99.0.0" "$ERR"' \
    "version=$(jq -r .version "$CFG") pending=$(cat "$GOAMNEZIA_DIR/.changelog_pending" 2>/dev/null) $(errs)"
printf '%s\n' "$MIG_CFG" >"$CFG"; chmod 600 "$CFG"
printf '%s\n' "$MIG_USERS" >"$GOAMNEZIA_DIR/users.json"; chmod 600 "$GOAMNEZIA_DIR/users.json"

# ── 15а. Длина подписи — в символах при любой локали (SR-23) ─────────────────
# Бот, панель и страницы зовут мост без LANG. В локали C bash считал байты: 30 русских букв (57 байт)
# отвергались шаблоном {1,48}, а из SSH (UTF-8) проходили.
section "SR-23: подпись из 30 русских букв из службы без LANG"
lbl30="Телефонмашиныдлядомаистарики"; lbl30="${lbl30}ЁЁ"
lbl49=$(printf 'я%.0s' $(seq 1 49))
LC_ALL=C LANG='' act user-add --name=lbl30 --label="$lbl30" --profiles=awg2
expect "SR-23 30 русских букв в локали C → success (48 — символы, а не байты)" success
LC_ALL=C LANG='' act user-rename --name=lbl30 --label="$lbl49"
expect "SR-23 49 русских букв → bad_arg, как и из SSH" error bad_arg
# TAIL-8 (L1-15): ровно на пределе — 48 букв кириллицей в локали C проходят, 49 — отказ; и без C.UTF-8
# (её нет в урезанных образах): длину считает ga_char_len по начальным байтам, а верхняя граница шаблона
# «одна скобка на всё значение» уходит в _RE_MAXLEN.
lbl48=$(printf 'ж%.0s' $(seq 1 48))
LC_ALL=C LANG='' act user-rename --name=lbl30 --label="$lbl48"
check "TAIL-8 48 русских букв в локали C → success, подпись сохранена целиком" \
    '[ "$(jget .status)" = success ] && [ "$(jq -r ".users[] | select(.name == \"lbl30\") | .label" "$GOAMNEZIA_DIR/users.json")" = "$lbl48" ]' "$last"
LIBINST="$TMP/libinst"; mkdir -p "$LIBINST"; ln -sfn "$ROOT/lib" "$LIBINST/lib"; sed '$d' "$INSTALL" >"$LIBINST/install.sh"
tail8=$("$BASH5" -c 'source "$1" >/dev/null 2>&1 || exit 9
    _GA_UTF8_LC_DONE=1; GA_UTF8_LC=""; LC_ALL=C; LANG=""
    _action_re_normalize "^[^\\x00-\\x1f\\x7f\\\\]{1,48}$"
    r48=0; _action_value_fits "$2" || r48=$?
    r49=0; _action_value_fits "$3" || r49=$?
    printf "%s|%s|%s|%s|%s|%s|%s|%s|%s|%s" "$_RE_MAXLEN" "${_RE_PAT: -5}" "$r48" "$r49" "$(ga_char_len "$2")" \
        "$(ga_users_label_valid "$2" && echo ok || echo no)" "$(ga_users_label_valid "$3" && echo ok || echo no)" \
        "$(ga_agents_label_valid "$2" && echo ok || echo no)" "$(ga_agents_label_valid "$3" && echo ok || echo no)" \
        "$(ga_cell "Туманный Лондон" 8)"' \
    _ "$LIBINST/install.sh" "$lbl48" "$lbl49" 2>/dev/null)
check "TAIL-8 без C.UTF-8 в локали C: предел 48 символов, шаблон с «{1,}», 48 букв — проходят, 49 — нет (мост, подписи пользователей и агентов); ячейка таблицы режется по буквам" \
    '[ "$tail8" = "48|{1,}\$|0|1|48|ok|no|ok|no|Туманны…" ]' "tail8=$tail8"
# SR-38: подпись — свободный текст; «@json:true» раньше уходило в ответ булевым true.
act user-rename --name=lbl30 --label='@json:true'
check "SR-38 подпись «@json:true» в ответе user-rename — строка, как её ввели" \
    '[ "$(jget .status)" = success ] && [ "$(jget ".label|type")" = string ] && [ "$(jget .label)" = "@json:true" ]' "$last"
LC_ALL=C LANG='' act user-del --name=lbl30

# ── 16. Секреты не в аргументах процессов (SR-17) ─────────────────────────────
# argv любого процесса виден каждому локальному пользователю в /proc/*/cmdline. Шпионы jq и python3 пишут
# свои аргументы в журнал; после выдачи, перевыпуска и доступа агента в нём не должно быть ни приватного
# ключа клиента, ни PSK, ни ключа сервера, ни секрета и токена агента.
section "SR-17: приватные ключи, PSK и секреты агентов не в argv"
SPYBIN="$TMP/spybin"; mkdir -p "$SPYBIN"; : >"$TMP/argv.log"
for _t in jq python3; do
    _real=$(command -v "$_t")
    printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv.log" "$_real" >"$SPYBIN/$_t"
    chmod +x "$SPYBIN/$_t"
done
PATH="$SPYBIN:$PATH" act user-add --name=spyuser --profiles=awg2
spy_add_ok=$(jget .status)
PATH="$SPYBIN:$PATH" act user-key --name=spyuser --profile=awg2 --format=vpn
spy_priv1=$(jq -r '.users[] | select(.name == "spyuser") | .peers.awg2.privkey // ""' "$USERS")
spy_psk1=$(jq -r '.users[] | select(.name == "spyuser") | .peers.awg2.psk // ""' "$USERS")
PATH="$SPYBIN:$PATH" act user-reissue-peer --name=spyuser --profile=awg2
spy_priv2=$(jq -r '.users[] | select(.name == "spyuser") | .peers.awg2.privkey // ""' "$USERS")
spy_srv=$(head -n 1 "$SD2/wireguard_server_private_key.key" 2>/dev/null)
PATH="$SPYBIN:$PATH" act agent-add --label=spyagent
spy_ag=$(jq -r '[.agents[]? | select(.label == "spyagent") | .secret, .token] | map(select(. != "")) | .[]' "$GOAMNEZIA_DIR/agents.json" 2>/dev/null)
spy_leak=""
for _s in "$spy_priv1" "$spy_psk1" "$spy_priv2" "$spy_srv" $spy_ag; do
    [ -n "$_s" ] && [ "${#_s}" -ge 16 ] && grep -qF -- "$_s" "$TMP/argv.log" && spy_leak+="${_s:0:6}… "
done
check "SR-17 шпионы сработали: вызовы jq и python3 записаны, пользователь заведён" \
    '[ "$spy_add_ok" = success ] && [ -n "$spy_priv1" ] && [ "$(wc -l <"$TMP/argv.log" | tr -d " ")" -gt 20 ] && grep -q "qr-chunks\|vpn-encode\|genkey" "$TMP/argv.log"' \
    "add=$spy_add_ok priv=${spy_priv1:0:4} lines=$(wc -l <"$TMP/argv.log")"
check "SR-17 ни ключ клиента, ни PSK, ни ключ сервера, ни секрет агента не прошли через argv" '[ -z "$spy_leak" ]' "утекло: $spy_leak"
PATH="$SPYBIN:$PATH" act user-key --name=spyuser --profile=awg2 --format=all
if [ "$(jget .status)" = success ]; then
    check "SR-17 QR-чанки ссылки (user-key all) — через stdin, ключ не в argv" \
        '! grep -qF -- "$spy_priv2" "$TMP/argv.log" && grep -q "qr-chunks -" "$TMP/argv.log"' "$(grep -c qr-chunks "$TMP/argv.log")"
else
    skipped "SR-17 QR-чанки" "нет qrencode"
fi

# ── 17. «🎲 Случайное имя»: name-suggest (docs/MODES_047.md «Имена ключей», C47-1) ──
# Только чтение: вариант «прилагательное + столица» из lib/names_capitals.json, id латиницей, подпись на языке
# установки (config.json lang=ru) или --lang; не совпадает с id и подписью заведённых людей и с --exclude.
section "name-suggest: язык, исключение, занятые имена, нет набора"
NAMES_ALL="$ROOT/lib/names_capitals.json"
users_sig=$(host_sig "$USERS")
act name-suggest
expect "name-suggest: success, одна JSON-строка" success
check "name-suggest без --lang: id из набора, подпись — его русский вариант (язык установки), lang=ru" \
    'id=$(jget .id); [[ "$id" =~ ^[a-z0-9_]{1,32}$ ]] && [ "$(jget .lang)" = ru ] && [ -n "$(jget .label)" ] && [ "$(jq -r --arg i "$id" ".[] | select(.id == \$i) | .ru" "$NAMES_ALL")" = "$(jget .label)" ]' "$last"
act name-suggest --lang=en
expect "name-suggest --lang=en: success" success
check "name-suggest --lang=en: подпись — английский вариант того же id" \
    'id=$(jget .id); [ "$(jget .lang)" = en ] && [ "$(jq -r --arg i "$id" ".[] | select(.id == \$i) | .en" "$NAMES_ALL")" = "$(jget .label)" ]' "$last"
act name-suggest --lang=de
expect "name-suggest --lang=de → bad_arg (только ru и en)" error bad_arg
act name-suggest --exclude="bad name"
expect "name-suggest --exclude с недопустимым id → bad_arg" error bad_arg
sugg_seen=""
for _i in 1 2 3 4 5 6 7 8 9 10; do act name-suggest; sugg_seen+="$(jget .id) "; done
check "name-suggest случаен: за 10 вызовов больше одного варианта" \
    '[ "$(tr " " "\n" <<<"$sugg_seen" | grep -c . )" = 10 ] && [ "$(tr " " "\n" <<<"$sugg_seen" | grep . | sort -u | wc -l | tr -d " ")" -gt 1 ]' "$sugg_seen"
check "name-suggest ничего не пишет: users.json байт-в-байт прежний" '[ "$(host_sig "$USERS")" = "$users_sig" ]'
# Набор из трёх: занятый id (sunny_manila) и занятая подпись («Снежная Оттава» у человека с другим id) не
# предлагаются никогда; остаётся один вариант, а с --exclude его же — свободных нет.
NAMES3="$TMP/names3.json"
cat >"$NAMES3" <<'EOF'
[{"id": "foggy_london", "ru": "Туманный Лондон", "en": "Foggy London", "city_ru": "Лондон", "city_en": "London"},
 {"id": "sunny_manila", "ru": "Солнечная Манила", "en": "Sunny Manila", "city_ru": "Манила", "city_en": "Manila"},
 {"id": "snowy_ottawa", "ru": "Снежная Оттава", "en": "Snowy Ottawa", "city_ru": "Оттава", "city_en": "Ottawa"}]
EOF
GOAMNEZIA_NAMES_JSON="$NAMES3" act name-suggest --exclude=foggy_london
check "набор из трёх, --exclude=foggy_london: вариант — один из двух других" \
    '[ "$(jget .status)" = success ] && [[ " sunny_manila snowy_ottawa " == *" $(jget .id) "* ]]' "$last"
act user-add --name=sunny_manila --profiles=awg2
act user-add --name=ottawa_fan --label="Снежная Оттава" --profiles=awg2
sugg_seen=""
for _i in 1 2 3 4 5 6; do GOAMNEZIA_NAMES_JSON="$NAMES3" act name-suggest; sugg_seen+="$(jget .id) "; done
check "занятые id (sunny_manila) и подпись («Снежная Оттава») не предлагаются: 6 раз подряд foggy_london" \
    '[ "$sugg_seen" = "foggy_london foggy_london foggy_london foggy_london foggy_london foggy_london " ]' "$sugg_seen"
GOAMNEZIA_NAMES_JSON="$NAMES3" act name-suggest --exclude=foggy_london --lang=en
expect "свободный остался один, и он исключён → names_exhausted" error names_exhausted
act user-del --name=sunny_manila
act user-del --name=ottawa_fan
GOAMNEZIA_NAMES_JSON="$TMP/names-missing.json" act name-suggest
expect "набора нет → error names_missing" error names_missing
check "… в причине назван путь к набору" '[[ "$(jget .message)" == *names-missing.json* ]]' "$last"
printf 'not json\n' >"$TMP/names-broken.json"
GOAMNEZIA_NAMES_JSON="$TMP/names-broken.json" act name-suggest
expect "набор испорчен → error names_missing" error names_missing

# ── 17а. Несколько сразу: users-add-bulk (просьба владельца 03.10) ───────────
# Тот же путь, что user-add, всех — под одним замком, к контейнеру — пачками по 10 (до десяти — одно применение).
# Отказы до изменений: число вне 1–100, префикс, нет мест в подсети, мало случайных имён. Сбой на середине —
# заведённые остаются (partial); контейнер не принял первую пачку — сняты все заведённые этим вызовом; прерывание
# (SIGTERM) — заведённые внесены, остановка названа.
section "users-add-bulk: имена, одно применение, места в подсети, сбой на середине, откат"
SDB="$GOAMNEZIA_DIR/state/$(jq -r .profiles.awg2.container "$CFG")"
nusers() { jq '.users | length' "$USERS"; }
bulk_n0=$(nusers)
act users-add-bulk --count=0
expect "count=0 → bad_arg (1–100 по каталогу)" error bad_arg
act users-add-bulk --count=101
expect "count=101 → bad_arg" error bad_arg
act users-add-bulk --count=2 --prefix="bad prefix"
expect "префикс с пробелом → bad_arg" error bad_arg
act users-add-bulk --count=2 --profiles=
expect "пустой --profiles= → no_profiles_picked, как у user-add" error no_profiles_picked
check "отказы никого не завели" '[ "$(nusers)" = "$bulk_n0" ]' "$(nusers) vs $bulk_n0"
act users-add-bulk --count=3 --prefix=team- --profiles=awg2
expect "users-add-bulk 3 с префиксом team-: success" success
check "имена team-01…03 (хвостовой дефис снят), подпись = имя; count, requested, partial=false, failed=null, profiles=[awg2]" \
    '[ "$(jget "[.added[].name] | join(\",\")")" = "team-01,team-02,team-03" ] && [ "$(jget "[.added[].label] | join(\",\")")" = "team-01,team-02,team-03" ] && [ "$(jget .count)" = 3 ] && [ "$(jget .requested)" = 3 ] && [ "$(jget .partial)" = false ] && [ "$(jget .failed)" = null ] && [ "$(jget ".profiles | join(\",\")")" = awg2 ]' "$last"
check "к контейнеру — одно применение на всех: один DRY syncconf за вызов" \
    '[ "$(grep -c "awg syncconf" "$ERR")" = 1 ]' "syncconf: $(grep -c "awg syncconf" "$ERR")"
check "каждый — с приватным ключом в users.json и пиром в серверном конфиге, адреса разные" \
    'for n in team-01 team-02 team-03; do jq -e --arg n "$n" ".users[] | select(.name == \$n) | .peers.awg2.privkey | length == 44" "$USERS" >/dev/null || exit 1; grep -qF "$(jq -r --arg n "$n" ".users[] | select(.name == \$n) | .peers.awg2.pubkey" "$USERS")" "$SDB/awg0.conf" || exit 1; done; [ "$(jq -r "[.users[] | select(.name | startswith(\"team-\")) | .peers.awg2.ip] | unique | length" "$USERS")" = 3 ]' \
    "$(jq -c "[.users[] | select(.name | startswith(\"team-\")) | {name, ip: .peers.awg2.ip}]" "$USERS")"
check "в ответе нет ключей" '! grep -q "privkey\|\"psk\"" <<<"$last"' "$last"
check "журнал действий: одна строка users-add-bulk с числом, профилями и именами" \
    'audit_has users-add-bulk "count=3/3 profiles=awg2 prefix=team quota_gb=0 days=0 names=team-01,team-02,team-03"' \
    "$(grep users-add-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
act users-add-bulk --count=2 --prefix=team --profiles=awg2 --quota-gb=5 --days=30
check "тот же префикс ещё раз — номера дальше (team-04, team-05), лимит и срок у каждого" \
    '[ "$(jget "[.added[].name] | join(\",\")")" = "team-04,team-05" ] && [ "$(jq -r ".users[] | select(.name == \"team-05\") | .quota_bytes" "$USERS")" = 5368709120 ] && [ -n "$(jq -r ".users[] | select(.name == \"team-05\") | .expires" "$USERS")" ]' "$last"
act users-add-bulk --count=2 --profiles=awg2
expect "без префикса: success" success
check "без префикса — разные случайные «прилагательное + столица» из набора, подпись — русский вариант (язык установки)" \
    'ids=$(jget "[.added[].name] | join(\" \")"); [ "$(jget .count)" = 2 ] && [ "$(tr " " "\n" <<<"$ids" | sort -u | wc -l | tr -d " ")" = 2 ] && for i in $ids; do [ "$(jq -r --arg i "$i" ".[] | select(.id == \$i) | .ru" "$NAMES_ALL")" = "$(jq -r --arg i "$i" ".added[] | select(.name == \$i) | .label" <<<"$last")" ] || exit 1; done' "$last"
GOAMNEZIA_CALL_LANG=en act users-add-bulk --count=1 --profiles=awg2
check "язык вызова en: подпись — английский вариант, слова ответа — по-английски" \
    'i=$(jget ".added[0].name"); [ "$(jq -r --arg i "$i" ".[] | select(.id == \$i) | .en" "$NAMES_ALL")" = "$(jget ".added[0].label")" ] && [[ "$(jget .message)" == "Users added: 1." ]]' "$last"
bulk_n1=$(nusers)
GOAMNEZIA_NAMES_JSON="$NAMES3" act users-add-bulk --count=4 --profiles=awg2
expect "случайных имён в наборе меньше, чем людей → names_exhausted" error names_exhausted
# Свободных в наборе из трёх — сколько не заняли случайные имена выше (они из полного набора, где есть и эти три)
names3_free=$(jq -r --slurpfile u "$USERS" '[.[] | select((.id as $i | $u[0].users | any(.name == $i) | not) and (.ru as $l | $u[0].users | any(.label == $l) | not))] | length' "$NAMES3")
check "… до изменений: никого не завели, в причине — сколько свободно" \
    '[ "$(nusers)" = "$bulk_n1" ] && [[ "$(jget .message)" == *"осталось $names3_free, а пользователей 4"* ]]' "$last free=$names3_free"
# Места: в копии конфига профиля все адреса подсети заняты пирами «приложения» — аллокатор считает их занятыми
bulk_pre=$(jq -r .profiles.awg2.subnet "$CFG"); bulk_pre=${bulk_pre%.*}
cp -p "$SDB/awg0.conf" "$TMP/bulk-awg0.conf"
for _i in $(seq 1 254); do printf '\n[Peer]\nPublicKey = FAKE%s\nAllowedIPs = %s.%s/32\n' "$_i" "$bulk_pre" "$_i"; done >>"$SDB/awg0.conf"
act users-add-bulk --count=1 --profiles=awg2
expect "мест в подсети профиля нет → subnet_full" error subnet_full
check "… до изменений: никого не завели, в причине — свободно 0 из 254 и как быть" \
    '[ "$(nusers)" = "$bulk_n1" ] && [[ "$(jget .message)" == *"свободно мест: 0 из 254"* ]] && [[ "$(jget .message)" == *"никого не добавлено"* ]]' "$last"
cp -p "$TMP/bulk-awg0.conf" "$SDB/awg0.conf"
# Сбой на середине: генератор ключей отказывает с третьего ключа — двое заведены и остаются, третий назван
cat >"$TMP/awgkeys_flaky.py" <<EOF
import os, subprocess, sys
c = "$TMP/awgkeys_flaky.count"
n = 0
if sys.argv[1:2] == ["genkey"]:
    n = (int(open(c).read() or 0) if os.path.exists(c) else 0) + 1
    open(c, "w").write(str(n))
if n > 2:
    sys.exit(2)
sys.exit(subprocess.call([sys.executable, "$ROOT/lib/awgkeys.py"] + sys.argv[1:]))
EOF
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_flaky.py" act users-add-bulk --count=4 --prefix=half --profiles=awg2
expect "сбой на третьем человеке — success: двое уже заведены" success
check "partial=true, count=2 из 4, failed.name=half-03 с причиной; в сообщении — на ком остановилось" \
    '[ "$(jget .partial)" = true ] && [ "$(jget .count)" = 2 ] && [ "$(jget .requested)" = 4 ] && [ "$(jget .failed.name)" = half-03 ] && [ -n "$(jget .failed.message)" ] && [[ "$(jget .message)" == *half-03* ]]' "$last"
check "half-01 и half-02 — в users.json и в конфиге контейнера, half-03 и дальше — нет" \
    '[ "$(jq -r "[.users[].name | select(startswith(\"half-\"))] | join(\",\")" "$USERS")" = "half-01,half-02" ] && grep -qF "$(jq -r ".users[] | select(.name == \"half-02\") | .peers.awg2.pubkey" "$USERS")" "$SDB/awg0.conf"' \
    "$(jq -c "[.users[].name | select(startswith(\"half-\"))]" "$USERS")"
check "журнал: count=2/4, на ком остановилось" \
    'grep users-add-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1 | grep -q "count=2/4 .*stopped=half-03"' "$(grep users-add-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
# Контейнер не принял: копии состояния профиля нет — заведённые этим вызовом сняты, как у одиночного user-add
bulk_n2=$(nusers)
mv "$SDB" "$TMP/bulk-state.hidden"
act users-add-bulk --count=2 --prefix=ghost --profiles=awg2
mv "$TMP/bulk-state.hidden" "$SDB"
check "применение не прошло → отказ, ghost-* сняты обратно, в причине — «никого не добавлено»" \
    '[ "$(jget .status)" = error ] && [ "$(nusers)" = "$bulk_n2" ] && ! jq -e ".users[] | select(.name | startswith(\"ghost-\"))" "$USERS" >/dev/null && [[ "$(jget .message)" == *"никого не добавлено"* ]]' "$last"
check "журнал: apply=failed rollback=done" \
    'audit_has users-add-bulk "count=0/2 profiles=awg2 apply=failed rollback=done"' "$(grep users-add-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
# Пачки: 12 человек — два применения (10 + 2), а не одно в самом конце и не двенадцать
act users-add-bulk --count=12 --prefix=pack --profiles=awg2
check "12 человек: success, все в users.json и в конфиге, к контейнеру — два применения (пачки по 10)" \
    '[ "$(jget .count)" = 12 ] && [ "$(jget .partial)" = false ] && [ "$(grep -c "awg syncconf" "$ERR")" = 2 ] && for n in pack-01 pack-10 pack-12; do grep -qF "$(jq -r --arg n "$n" ".users[] | select(.name == \$n) | .peers.awg2.pubkey" "$USERS")" "$SDB/awg0.conf" || exit 1; done' \
    "count=$(jget .count) syncconf=$(grep -c "awg syncconf" "$ERR")"
# Прерывание посреди партии (Ctrl+C в SSH, остановка службы бота или панели): заведённые внесены в контейнер, итог
# называет остановку — в users.json нет людей, которых нет в конфиге контейнера. Фоновому процессу SIGINT не
# доставить (асинхронные команды его игнорируют), SIGTERM ловится тем же обработчиком.
"$BASH5" "$INSTALL" --action=users-add-bulk --json --count=60 --prefix=stopme --profiles=awg2 >"$TMP/bulk_term.out" 2>"$TMP/bulk_term.err" &
bulk_pid=$!
for _i in $(seq 1 200); do jq -e '[.users[].name | select(startswith("stopme-"))] | length >= 3' "$USERS" >/dev/null 2>&1 && break; sleep 0.1; done
kill -TERM "$bulk_pid" 2>/dev/null
wait "$bulk_pid"; bulk_rc=$?
bulk_last=$(tail -n 1 "$TMP/bulk_term.out")
bulk_names=$(jq -r '[.users[].name | select(startswith("stopme-"))] | .[]' "$USERS")
check "SIGTERM посреди 60: ответ — success, partial, failed.code=interrupted, добавлено меньше 60" \
    '[ "$bulk_rc" = 0 ] && [ "$(jq -r .status <<<"$bulk_last")" = success ] && [ "$(jq -r .partial <<<"$bulk_last")" = true ] && [ "$(jq -r .failed.code <<<"$bulk_last")" = interrupted ] && [ "$(jq -r .count <<<"$bulk_last")" -lt 60 ]' \
    "rc=$bulk_rc $bulk_last $(tail -3 "$TMP/bulk_term.err")"
check "… каждый заведённый stopme-* — и в конфиге контейнера (невнесённых нет), их число = count ответа" \
    '[ -n "$bulk_names" ] && [ "$(grep -c . <<<"$bulk_names")" = "$(jq -r .count <<<"$bulk_last")" ] && while IFS= read -r n; do grep -qF "$(jq -r --arg n "$n" ".users[] | select(.name == \$n) | .peers.awg2.pubkey" "$USERS")" "$SDB/awg0.conf" || exit 1; done <<<"$bulk_names"' \
    "names=$(grep -c . <<<"$bulk_names") count=$(jq -r .count <<<"$bulk_last")"

# ── 17б. Несколько сразу: users-del-bulk (просьба владельца 03.10) ───────────
# Ровно один способ выбора (имена, префикс как у массового добавления, состояние), подтверждение — числом, сверка с
# выбранным в момент удаления. Отказы — до изменений: не тот способ, не то число, никого, больше 100, имени нет.
# Порядок одиночного удаления (SR-06): выключить → применить → стереть, пачками по 10; контейнер не принял — пачка
# остаётся выключенной; прерывание (SIGTERM) — пачка доделывается, остальные не тронуты; страницы удалённых сняты.
section "users-del-bulk: выбор, отказы до изменений, порядок «выключить → применить → стереть», сбой, прерывание"
db_audit() { grep -c '"action":"users-del-bulk"' "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null; }
db_names() { jq -r --arg re "$1" '[.users[].name | select(test($re))] | join(",")' "$USERS"; }
db_pub() { jq -r --arg n "$1" '.users[] | select(.name == $n) | .peers.awg2.pubkey // empty' "$USERS"; }
db_users_sig=$(host_sig "$USERS"); db_audit0=$(db_audit)
act users-del-bulk --confirm=1
expect "ни одного способа выбора → invalid_selection" error invalid_selection
act users-del-bulk --confirm=1 --prefix=pack --state=expired
expect "два способа сразу → invalid_selection" error invalid_selection
act users-del-bulk --prefix=pack
expect "без --confirm → missing_arg (каталог: обязателен)" error missing_arg
act users-del-bulk --confirm=0 --prefix=pack
expect "--confirm=0 → bad_arg (1–100 по каталогу)" error bad_arg
act users-del-bulk --confirm=101 --prefix=pack
expect "--confirm=101 → bad_arg" error bad_arg
act users-del-bulk --confirm=1 --state=bogus
expect "--state не из списка → bad_arg" error bad_arg
act users-del-bulk --confirm=1 --prefix="bad prefix"
expect "префикс с пробелом → bad_arg" error bad_arg
act users-del-bulk --confirm=11 --prefix=pack
expect "--confirm=11 при 12 выбранных → confirm_mismatch" error confirm_mismatch
check "… в ответе matched=12, в причине — оба числа" \
    '[ "$(jget .matched)" = 12 ] && [[ "$(jget .message)" == *"Подтверждено 11, а выбрано сейчас 12"* ]]' "$last"
act users-del-bulk --confirm=1 --prefix=nosuchprefix
expect "никто не попал под выбор → nothing_selected" error nothing_selected
check "… matched=0, в причине — каким был выбор" '[ "$(jget .matched)" = 0 ] && [[ "$(jget .message)" == *nosuchprefix* ]]' "$last"
act users-del-bulk --confirm=2 --names=pack-01,ghost-zz
expect "имени из списка нет → user_not_found" error user_not_found
check "… в причине названо, кого нет" '[[ "$(jget .message)" == *ghost-zz* ]] && [[ "$(jget .message)" != *pack-01* ]]' "$last"
act users-del-bulk --confirm=100 --names="$(seq -f 'x%03g' 1 101 | paste -sd, -)"
expect "101 имя в списке → too_many" error too_many
cp -p "$USERS" "$TMP/db-users.json"
jq '.users += [range(1; 102) | {name: ("many-" + (. | tostring)), label: "m", created: "2026-01-01T00:00:00Z", enabled: true,
     quota_bytes: 0, expires: "", traffic: {rx: 0, tx: 0, last_rx: 0, last_tx: 0, updated: ""}, peers: {}}]' \
    "$TMP/db-users.json" >"$USERS"
act users-del-bulk-preview --prefix=many
check "предпросмотр 101 совпадения: too_many=true, count=101, max=100, все названы" \
    '[ "$(jget .status)" = success ] && [ "$(jget .too_many)" = true ] && [ "$(jget .count)" = 101 ] && [ "$(jget .max)" = 100 ] && [ "$(jget ".selected | length")" = 101 ]' "${last:0:300}"
act users-del-bulk --confirm=100 --prefix=many
expect "101 совпадение → too_many (не «первые сто»)" error too_many
check "… matched=101, никого не удалено" '[ "$(jget .matched)" = 101 ] && [ "$(jq "[.users[] | select(.name | startswith(\"many-\"))] | length" "$USERS")" = 101 ]' "$last"
cp -p "$TMP/db-users.json" "$USERS"
check "отказы ничего не меняют: users.json байт-в-байт прежний, в журнале действий users-del-bulk нет" \
    '[ "$(host_sig "$USERS")" = "$db_users_sig" ] && [ "$(db_audit)" = "$db_audit0" ]' "audit $(db_audit) vs $db_audit0"

# Предпросмотр: префикс — только <префикс>-<число>, как их заводит добавление; повторы имён — один раз
act user-add --name=pack-boss --profiles=awg2
act users-del-bulk-preview --prefix=pack-
expect "users-del-bulk-preview --prefix=pack-: success" success
check "префикс pack-: хвостовой дефис снят, выбраны pack-01…12 по порядку списка, pack-boss — нет" \
    '[ "$(jget .mode)" = prefix ] && [ "$(jget .value)" = pack ] && [ "$(jget .count)" = 12 ] && [ "$(jget .too_many)" = false ] && [ "$(jget "[.selected[].name] | join(\",\")")" = "$(seq -f "pack-%02g" 1 12 | paste -sd, -)" ]' "$last"
act users-del-bulk-preview --names=pack-02,pack-01,pack-02,ghost-zz
check "имена: повтор — один раз, missing — ghost-zz, порядок — как в списке пользователей" \
    '[ "$(jget .mode)" = names ] && [ "$(jget .count)" = 2 ] && [ "$(jget "[.selected[].name] | join(\",\")")" = pack-01,pack-02 ] && [ "$(jget ".missing | join(\",\")")" = ghost-zz ]' "$last"
check "в предпросмотре нет ключей" '! grep -q "privkey\|\"psk\"\|pubkey" <<<"$last"' "$last"

# Состояния: выключен руками (и без причины, как у прежних версий) — disabled; выключен таймером по лимиту — quota,
# срок вышел — expired. Сверяем с тем, что было до подготовки: на стенде могли остаться люди тех же состояний.
db_base_dis=$(act users-del-bulk-preview --state=disabled; jget "[.selected[].name] | join(\",\")")
db_base_exp=$(act users-del-bulk-preview --state=expired; jget "[.selected[].name] | join(\",\")")
db_base_quo=$(act users-del-bulk-preview --state=quota; jget "[.selected[].name] | join(\",\")")
act users-add-bulk --count=4 --prefix=dst --profiles=awg2
act user-disable --name=dst-01
jq '.users |= map(if .name == "dst-02" then .enabled = false | .disabled_reason = "quota" | .quota_bytes = 1073741824 | .traffic.rx = 2147483648
                  elif .name == "dst-03" then .expires = "2026-01-01T00:00:00Z"
                  elif .name == "dst-04" then .enabled = false | del(.disabled_reason) else . end)' "$USERS" >"$TMP/db-u.json" \
    && cat "$TMP/db-u.json" >"$USERS"
db_join() { printf '%s\n' "$@" | grep . | paste -sd, -; }
act user-list
check "user-list отдаёт причину выключения строкой (панель называет по ней выключенного таймером): manual, quota, пусто" \
    '[ "$(jget "[.users[] | select(.name | startswith(\"dst-\")) | .disabled_reason] | join(\",\")")" = "manual,quota,," ] && ! grep -q "privkey\|\"psk\"" <<<"$last"' \
    "$(jget "[.users[] | select(.name | startswith(\"dst-\")) | {name, enabled, disabled_reason}] | tostring")"
act users-del-bulk-preview --state=disabled
check "state=disabled: выключенный руками dst-01 и без причины dst-04; выключенный таймером dst-02 и истёкший dst-03 — нет" \
    '[ "$(jget "[.selected[].name] | join(\",\")")" = "$(db_join "$db_base_dis" dst-01 dst-04)" ]' "$last base=$db_base_dis"
act users-del-bulk-preview --state=quota
check "state=quota: лимит исчерпан у dst-02 (выключен таймером) — выбран" \
    '[ "$(jget "[.selected[].name] | join(\",\")")" = "$(db_join "$db_base_quo" dst-02)" ]' "$last base=$db_base_quo"
act users-del-bulk-preview --state=expired
check "state=expired: срок вышел у включённого dst-03 — выбран" \
    '[ "$(jget "[.selected[].name] | join(\",\")")" = "$(db_join "$db_base_exp" dst-03)" ]' "$last base=$db_base_exp"
db_n=$(jget .count)
act users-del-bulk --state=expired --confirm="$db_n"
check "удаление state=expired: success, dst-03 удалён, dst-01, dst-02, dst-04 на месте" \
    '[ "$(jget .status)" = success ] && [ "$(jget .count)" = "$db_n" ] && [ "$(db_names "^dst-")" = dst-01,dst-02,dst-04 ]' "$last"
act users-del-bulk-preview --state=disabled
act users-del-bulk --state=disabled --confirm="$(jget .count)"
check "удаление state=disabled: dst-01 и dst-04 удалены, dst-02 (выключен таймером) на месте" \
    '[ "$(jget .status)" = success ] && [ "$(db_names "^dst-")" = dst-02 ]' "$last"
act users-del-bulk --names=dst-02 --confirm=1
check "удаление по имени: dst-02 удалён" '[ "$(jget .status)" = success ] && [ -z "$(db_names "^dst-")" ]' "$last"

# Порядок и пачки: 12 человек — две пачки (10 + 2), два применения. Шпион на сборке конфига сервера записывает
# users.json в момент применения: пачка в нём уже выключена, но ещё не стёрта (SR-06), следующая — не тронута.
PAGESD="$GOAMNEZIA_DIR/pages"
for _p in "p-dbb1:pack-03" "p-dbb2:pack-11" "p-dbb3:pack-boss"; do
    mkdir -p "$PAGESD/${_p%%:*}"
    printf '{"id": "%s", "user": "%s", "profiles": ["awg2"], "expires": "forever"}\n' "${_p%%:*}" "${_p#*:}" >"$PAGESD/${_p%%:*}/meta.json"
    printf 'Q%.0s' {1..43} >"$PAGESD/${_p%%:*}/secret"
done
db_pk01=$(db_pub pack-01); db_pk10=$(db_pub pack-10); db_pk12=$(db_pub pack-12); db_pkboss=$(db_pub pack-boss)
cat >"$TMP/awgkeys_spy.py" <<EOF
import json, subprocess, sys
if sys.argv[1:2] == ["server-conf"]:
    u = json.load(open("$USERS"))
    snap = [{"name": x["name"], "enabled": x.get("enabled")} for x in u.get("users", []) if x.get("name", "").startswith("pack-") and x["name"][5:].isdigit()]
    open("$TMP/db-spy.log", "a").write(json.dumps(snap) + "\n")
sys.exit(subprocess.call([sys.executable, "$ROOT/lib/awgkeys.py"] + sys.argv[1:]))
EOF
rm -f "$TMP/db-spy.log"
db_audit1=$(db_audit)
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_spy.py" act users-del-bulk --prefix=pack --confirm=12
expect "users-del-bulk --prefix=pack --confirm=12: success" success
check "ответ: requested=12, count=12, deleted pack-01…12, kept пуст, partial=false, failed=null, mode/value" \
    '[ "$(jget .requested)" = 12 ] && [ "$(jget .count)" = 12 ] && [ "$(jget ".deleted | join(\",\")")" = "$(seq -f "pack-%02g" 1 12 | paste -sd, -)" ] && [ "$(jget ".kept | length")" = 0 ] && [ "$(jget .partial)" = false ] && [ "$(jget .failed)" = null ] && [ "$(jget .mode)" = prefix ] && [ "$(jget .value)" = pack ]' "$last"
check "в ответе нет ключей" '! grep -q "privkey\|\"psk\"\|pubkey" <<<"$last"' "$last"
check "к контейнеру — два применения (пачки 10 + 2), а не двенадцать" '[ "$(grep -c "awg syncconf" "$ERR")" = 2 ]' "syncconf: $(grep -c "awg syncconf" "$ERR")"
check "порядок SR-06: при первом применении pack-01…10 ещё в списке и выключены, pack-11…12 включены" \
    '[ "$(sed -n 1p "$TMP/db-spy.log" | jq -c "[.[] | select(.enabled == false) | .name] | length")" = 10 ] && [ "$(sed -n 1p "$TMP/db-spy.log" | jq -r "[.[] | select(.enabled == true) | .name] | join(\",\")")" = pack-11,pack-12 ]' \
    "$(cat "$TMP/db-spy.log" 2>/dev/null)"
check "… при втором — первой пачки в списке уже нет, pack-11 и pack-12 выключены" \
    '[ "$(sed -n 2p "$TMP/db-spy.log" | jq -c "[.[] | {name, enabled}]")" = "[{\"name\":\"pack-11\",\"enabled\":false},{\"name\":\"pack-12\",\"enabled\":false}]" ]' \
    "$(sed -n 2p "$TMP/db-spy.log" 2>/dev/null)"
check "записей pack-01…12 нет, pack-boss на месте; их ключей нет в конфиге контейнера, ключ pack-boss — есть" \
    '[ "$(db_names "^pack-")" = pack-boss ] && ! grep -qF "$db_pk01" "$SDB/awg0.conf" && ! grep -qF "$db_pk10" "$SDB/awg0.conf" && ! grep -qF "$db_pk12" "$SDB/awg0.conf" && grep -qF "$db_pkboss" "$SDB/awg0.conf"' \
    "$(db_names "^pack-")"
check "clientsTable приложения Amnezia — без удалённых, pack-boss в ней есть" \
    '! grep -q "\"pack-0[0-9]\"\|\"pack-1[0-2]\"" "$SDB/clientsTable" && grep -q "\"pack-boss\"" "$SDB/clientsTable"' "$(head -c 400 "$SDB/clientsTable")"
check "страницы подключения удалённых сняты с аудитом page-revoke, страница pack-boss на месте (SR-05)" \
    '[ ! -d "$PAGESD/p-dbb1" ] && [ ! -d "$PAGESD/p-dbb2" ] && [ -f "$PAGESD/p-dbb3/meta.json" ] && audit_has page-revoke "id=p-dbb1 user=pack-03 reason=revoked" && audit_has page-revoke "id=p-dbb2 user=pack-11 reason=revoked"' \
    "$(ls "$PAGESD" 2>&1)"
check "журнал действий: одна строка users-del-bulk — число, способ и имена" \
    '[ "$(db_audit)" = $((db_audit1 + 1)) ] && audit_has users-del-bulk "count=12/12 by=prefix:pack names=$(seq -f "pack-%02g" 1 12 | paste -sd, -)"' \
    "$(grep users-del-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
rm -rf "$PAGESD/p-dbb3"

# Контейнер не принял вторую пачку: первая удалена, вторая оставлена в списке выключенной — итог это называет
act users-add-bulk --count=12 --prefix=fl --profiles=awg2
cat >"$TMP/awgkeys_conf_flaky.py" <<EOF
import os, subprocess, sys
c = "$TMP/awgkeys_conf_flaky.count"
if sys.argv[1:2] == ["server-conf"]:
    n = (int(open(c).read() or 0) if os.path.exists(c) else 0) + 1
    open(c, "w").write(str(n))
    if n > 1:
        sys.exit(2)
sys.exit(subprocess.call([sys.executable, "$ROOT/lib/awgkeys.py"] + sys.argv[1:]))
EOF
rm -f "$TMP/awgkeys_conf_flaky.count"
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_conf_flaky.py" act users-del-bulk --prefix=fl --confirm=12
expect "вторая пачка не вошла в контейнер — success: первая уже удалена" success
check "partial=true, count=10 из 12, kept fl-11 и fl-12, failed — user_del_failed на fl-11, в сообщении названо" \
    '[ "$(jget .partial)" = true ] && [ "$(jget .count)" = 10 ] && [ "$(jget .requested)" = 12 ] && [ "$(jget ".kept | join(\",\")")" = fl-11,fl-12 ] && [ "$(jget .failed.code)" = user_del_failed ] && [ "$(jget .failed.name)" = fl-11 ] && [[ "$(jget .message)" == *fl-11* ]]' "$last"
check "fl-01…10 стёрты; fl-11 и fl-12 в списке, выключены, пиры неактивны — удаление можно повторить" \
    '[ "$(db_names "^fl-")" = fl-11,fl-12 ] && [ "$(jq -r "[.users[] | select(.name == \"fl-11\" or .name == \"fl-12\") | select(.enabled == false and .peers.awg2.active == false)] | length" "$USERS")" = 2 ]' \
    "$(jq -c "[.users[] | select(.name | startswith(\"fl-\")) | {name, enabled}]" "$USERS")"
check "журнал: count=10/12, kept и на ком встало" \
    'grep users-del-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1 | grep -q "count=10/12 by=prefix:fl names=fl-01.*fl-10 kept=fl-11,fl-12 stopped=fl-11 code=user_del_failed"' \
    "$(grep users-del-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
act users-del-bulk --names=fl-11,fl-12 --confirm=2
check "повтор по именам: оставшиеся выключенными удалены" '[ "$(jget .status)" = success ] && [ -z "$(db_names "^fl-")" ]' "$last"

# Контейнер не принял первую же пачку: никого не удалено — отказ, люди в списке выключены (SR-06)
act users-add-bulk --count=2 --prefix=gh --profiles=awg2
mv "$SDB" "$TMP/db-state.hidden"
act users-del-bulk --prefix=gh --confirm=2
mv "$TMP/db-state.hidden" "$SDB"
expect "применение не прошло → отказ user_del_failed" error user_del_failed
check "… в причине — никого не удалено и сколько оставлено выключенными; gh-01 и gh-02 в списке выключены" \
    '[[ "$(jget .message)" == *"Никого не удалено"* ]] && [[ "$(jget .message)" == *"2 оставлены"* ]] && [ "$(jq -r "[.users[] | select(.name | startswith(\"gh-\")) | select(.enabled == false)] | length" "$USERS")" = 2 ]' "$last"
check "журнал: count=0/2, kept, на ком встало" \
    'audit_has users-del-bulk "count=0/2 by=prefix:gh names= kept=gh-01,gh-02 stopped=gh-01 code=user_del_failed"' \
    "$(grep users-del-bulk "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
act users-del-bulk --prefix=gh --confirm=2
check "Docker снова на месте — повтор удаляет оставленных" '[ "$(jget .status)" = success ] && [ -z "$(db_names "^gh-")" ]' "$last"

# Прерывание посреди партии (Ctrl+C в SSH, остановка службы): пачка доделывается, остальные не тронуты — нет ни
# стёртого с пиром в контейнере, ни выключенного в списке, который ещё ходит. Применение замедлено, чтобы сигнал
# пришёл посреди партии.
act users-add-bulk --count=40 --prefix=tm --profiles=awg2
jq -r '.users[] | select(.name | test("^tm-[0-9]+$")) | "\(.name) \(.peers.awg2.pubkey)"' "$USERS" >"$TMP/db-tm.keys"
cat >"$TMP/awgkeys_slow.py" <<EOF
import subprocess, sys, time
if sys.argv[1:2] == ["server-conf"]:
    time.sleep(0.4)
sys.exit(subprocess.call([sys.executable, "$ROOT/lib/awgkeys.py"] + sys.argv[1:]))
EOF
GOAMNEZIA_AWGKEYS="$TMP/awgkeys_slow.py" "$BASH5" "$INSTALL" --action=users-del-bulk --json --prefix=tm --confirm=40 \
    >"$TMP/db_term.out" 2>"$TMP/db_term.err" &
db_pid=$!
for _i in $(seq 1 300); do jq -e '[.users[].name | select(test("^tm-[0-9]+$"))] | length <= 30' "$USERS" >/dev/null 2>&1 && break; sleep 0.05; done
kill -TERM "$db_pid" 2>/dev/null
wait "$db_pid"; db_rc=$?
db_last=$(tail -n 1 "$TMP/db_term.out")
db_left=$(db_names "^tm-[0-9]+$")
check "SIGTERM посреди 40: ответ — success, partial, failed.code=interrupted, удалено целыми пачками и меньше 40" \
    '[ "$db_rc" = 0 ] && [ "$(jq -r .status <<<"$db_last")" = success ] && [ "$(jq -r .partial <<<"$db_last")" = true ] && [ "$(jq -r .failed.code <<<"$db_last")" = interrupted ] && n=$(jq -r .count <<<"$db_last") && [ "$n" -ge 10 ] && [ "$n" -lt 40 ] && [ $((n % 10)) = 0 ] && [ "$(jq -r ".kept | length" <<<"$db_last")" = 0 ]' \
    "rc=$db_rc $db_last $(tail -3 "$TMP/db_term.err")"
check "… удалённые стёрты и их ключей нет в контейнере; оставшиеся включены и их ключи в контейнере (не тронуты)" \
    'gone=$(jq -r ".deleted[]" <<<"$db_last"); [ "$(tr "," "\n" <<<"$db_left" | grep -c .)" = $((40 - $(jq -r .count <<<"$db_last"))) ] && while read -r n k; do if grep -qx "$n" <<<"$gone"; then ! jq -e --arg n "$n" ".users[] | select(.name == \$n)" "$USERS" >/dev/null && ! grep -qF "$k" "$SDB/awg0.conf" || exit 1; else jq -e --arg n "$n" ".users[] | select(.name == \$n and .enabled == true)" "$USERS" >/dev/null && grep -qF "$k" "$SDB/awg0.conf" || exit 1; fi; done <"$TMP/db-tm.keys"' \
    "left=$db_left"
act users-del-bulk --prefix=tm --confirm="$(tr "," "\n" <<<"$db_left" | grep -c .)"
check "остаток после прерывания удаляется повтором" '[ "$(jget .status)" = success ] && [ -z "$(db_names "^tm-")" ]' "$last"
act user-del --name=pack-boss

# ── 18. Все профили разом: profiles-restart (L-12) ───────────────────────────
section "profiles-restart: все установленные профили одним действием"
rs_exp=""
for _p in awg3 awg2 awg1; do jq -e --arg p "$_p" '.profiles[$p]' "$CFG" >/dev/null 2>&1 && rs_exp+="${_p},"; done
rs_exp="${rs_exp%,}"
act profiles-restart
expect "profiles-restart: success" success
check "restarted — установленные профили в порядке меню ($rs_exp), failed пуст, DRY docker restart у каждого" \
    '[ -n "$rs_exp" ] && [ "$(jget ".restarted|join(\",\")")" = "$rs_exp" ] && [ "$(jget ".failed|length")" = 0 ] && [ "$(grep -c "DRY: docker restart amnezia-" "$ERR")" = "$(tr "," "\n" <<<"$rs_exp" | grep -c .)" ]' \
    "$last $(grep "DRY: docker restart" "$ERR" | head -3)"
check "бот, панель и nginx этим действием не трогаются" '! grep -q "restart goamnezia-bot\|restart goamnezia-admin\|nginx" "$ERR"' "$(grep -i "restart" "$ERR" | head -5)"
mkdir -p "$TMP/noprof"
printf '{"schema": 1, "installed": true, "mode": "lite", "lang": "ru", "server_ip": "203.0.113.10", "profiles": {}}\n' \
    >"$TMP/noprof/config.json"
GOAMNEZIA_DIR="$TMP/noprof" act profiles-restart
expect "профилей нет → error no_profiles" error no_profiles

# ── 18а. DNS нового домена до смены режима (L-09) ────────────────────────────
section "domain-check: A и AAAA нового домена против адресов сервера, ничего не меняя"
cfg_sig=$(host_sig "$CFG")
GOAMNEZIA_EDGE_IPS_STUB="" GOAMNEZIA_DNS_STUB="203.0.113.10" GOAMNEZIA_DNS_STUB_AAAA="" act domain-check --domain=new.example.com
expect "domain-check: A-запись на этот сервер → success" success
check "state=ok, ready=true, a=[203.0.113.10], ours совпадает, foreign пуст; message — «DNS в порядке»" \
    '[ "$(jget .state)" = ok ] && [ "$(jget .ready)" = true ] && [ "$(jget ".a|join(\",\")")" = 203.0.113.10 ] && [ "$(jget ".foreign|length")" = 0 ] && [ "$(jget .domain)" = new.example.com ] && [ -n "$(jget .message)" ]' "$last"
GOAMNEZIA_EDGE_IPS_STUB="" GOAMNEZIA_DNS_STUB="203.0.113.10 192.0.2.9" GOAMNEZIA_DNS_STUB_AAAA="2001:db8::9" act domain-check --domain=new.example.com
check "одна A-запись чужая и AAAA на чужой IPv6: state=partial, ready=false, foreign и aaaa_foreign названы, hint словами" \
    '[ "$(jget .state)" = partial ] && [ "$(jget .ready)" = false ] && [ "$(jget ".foreign|join(\",\")")" = 192.0.2.9 ] && [ "$(jget ".aaaa_foreign|join(\",\")")" = 2001:db8::9 ] && [ -n "$(jget .hint)" ] && [ "$(jget .message)" = "$(jget .hint)" ]' "$last"
GOAMNEZIA_EDGE_IPS_STUB="" GOAMNEZIA_DNS_STUB="" act domain-check --domain=new.example.com
check "A-записей нет: state=none, ready=false" '[ "$(jget .state)" = none ] && [ "$(jget .ready)" = false ]' "$last"
act domain-check --domain="Bad_Domain"
expect "недопустимый домен → bad_arg (pattern домена)" error bad_arg
check "domain-check ничего не пишет: config.json прежний" '[ "$(host_sig "$CFG")" = "$cfg_sig" ]'

# ── 19. Переезд на другой IP в status (L-06) ─────────────────────────────────
section "status.server_ip_change: последний переезд — было, стало, когда"
jq 'del(.server_ip_change)' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG" && chmod 600 "$CFG"
act status
check "переезда в config.json нет — server_ip_change=null" '[ "$(jget .server_ip_change)" = null ]' "$last"
jq '. + {server_ip_change: {old: "203.0.113.10", new: "198.51.100.7", at: "2026-09-27T10:00:00Z",
         endpoint_host: "203.0.113.10", endpoint_host_action: "moved", junk: "<b>x</b>"}}' "$CFG" >"$CFG.tmp" \
    && mv "$CFG.tmp" "$CFG" && chmod 600 "$CFG"
act status
check "status.server_ip_change: old, new, at, endpoint_host, endpoint_host_action — и ничего сверх (белый список)" \
    '[ "$(jget ".server_ip_change|keys|join(\",\")")" = "at,endpoint_host,endpoint_host_action,new,old" ] && [ "$(jget .server_ip_change.new)" = 198.51.100.7 ] && [ "$(jget .server_ip_change.endpoint_host_action)" = moved ]' "$last"
jq 'del(.server_ip_change)' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG" && chmod 600 "$CFG"

# ── 20. Язык ответа на один вызов (L-24) ─────────────────────────────────────
section "GOAMNEZIA_CALL_LANG: слова ответа на языке того, кто нажал, — только для этого вызова"
act user-show --name=nobody
ru_msg=$(jget .message)
GOAMNEZIA_CALL_LANG=en act user-show --name=nobody
check "GOAMNEZIA_CALL_LANG=en на русской установке: код тот же, слова — по-английски" \
    '[ "$(jget .code)" = user_not_found ] && [ "$(jget .message)" != "$ru_msg" ] && [[ "$(jget .message)" == *"not found"* ]]' "$last"
GOAMNEZIA_CALL_LANG=de act user-show --name=nobody
check "незнакомый язык вызова — язык установки" '[ "$(jget .message)" = "$ru_msg" ]' "$last"
act user-show --name=nobody
check "следующий вызов без переменной — снова язык установки (config.json не тронут)" \
    '[ "$(jget .message)" = "$ru_msg" ] && [ "$(jq -r .lang "$CFG")" = ru ]' "$last"
GOAMNEZIA_CALL_LANG=en act status
check "status.lang — язык установки, а не язык вызова" '[ "$(jget .lang)" = ru ]' "$last"

# ── Итог ─────────────────────────────────────────────────────────────────────
DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
