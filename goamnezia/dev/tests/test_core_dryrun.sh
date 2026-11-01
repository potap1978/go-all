#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон ядра без Docker (GOAMNEZIA_DRY_RUN=1)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_core_dryrun.sh
# Нужны: bash ≥ 5, jq, python3; qrencode — для шагов с QR (без него они
# помечаются как пропущенные, а не проваленные).
#
# Что проверяем во временном GOAMNEZIA_DIR:
#   установка профилей awg2/awg3/awg1 (dry) → конфликт портов → два пользователя →
#   аллокация IP без пересечений и с переиспользованием → users_list/show без
#   секретов → export_conf/export_vpn (vpn-decode возвращает те же параметры) →
#   bundle и QR → выключение/включение меняет active и конфиг → users_quota_tick
#   на подложенном `awg show … dump` считает дельты, отключает превысившего лимит
#   и просроченного, кладёт события в очередь → статус, смена порта, regen,
#   удаление профиля → паритет словарей RU/EN и наличие всех ключей t/tf.
#
# Настоящие lib/common.sh, lib/i18n.sh, lib/awgkeys.py, lib/upstream/* берутся,
# если уже есть; иначе — заглушки из tests/fixtures/. Ничего вне $TMP не пишем.
#
# Захват результата: функции зовём через run (stdout → файл), а не через $(…):
# в подоболочке терялся бы код ошибки GA_ERR_CODE / GA_LAST_ERROR_CODE — та же
# ловушка, о которой предупреждает lib/common.sh.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-core.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
# Аварийный выход (set -u, exit из чужого кода) — показываем последний stderr,
# иначе причина уезжает в удаляемый $TMP.
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR"
# Бот, панель, MCP и vhost nginx — во временном каталоге: по умолчанию это /opt и /etc машины, и на
# сервере с установленным продуктом бэкап читал бы оттуда токен бота и пароль панели, а
# восстановление писало бы туда (раздел 10 и дальше).
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
export NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
# host_sig <путь> — след файла машины: «absent» или контрольная сумма. «Живая система не тронута» —
# это след до прогона и после одинаков, а не «файла нет»: на машине с установленным продуктом он есть
# по праву (Linux-сервер), и проверка краснела бы, хотя прогон ничего не писал.
host_sig() { if [ -e "$1" ]; then cksum <"$1" 2>/dev/null || echo unreadable; else echo absent; fi; }
HOST_BACKUP_TIMER_SIG=$(host_sig /etc/systemd/system/goamnezia-backup.timer)

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

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
# check <описание> <условие для eval> [подсказка при провале]
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
# run <функция> [аргументы…] → rc, out (stdout), $ERR (stderr); коды ошибок остаются в оболочке
run() {
    GA_ERR_CODE=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }

# ── Окружение: настоящие модули или заглушки ─────────────────────────────────
section "окружение"
# GOAMNEZIA_TEST_FORCE_STUBS=1 — прогнать на заглушках даже при наличии настоящих
# модулей: так заглушки не отстают от контракта, пока соседи меняют интерфейсы.
if [ -f "$ROOT/lib/common.sh" ] && [ -z "${GOAMNEZIA_TEST_FORCE_STUBS:-}" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/common.sh"
    # shellcheck disable=SC1091
    [ -f "$ROOT/lib/i18n.sh" ] && source "$ROOT/lib/i18n.sh"
    printf '  lib/common.sh: настоящий\n'
else
    # shellcheck disable=SC1091
    source "$ROOT/tests/fixtures/common_stub.sh"
    printf '  lib/common.sh: заглушка tests/fixtures/common_stub.sh\n'
fi
declare -F load_language >/dev/null 2>&1 && load_language ru >/dev/null 2>&1
# Если движок i18n не подхватил фрагменты *.d — подключаем словари ядра сами,
# чтобы сообщения в выводе теста были читаемыми.
if [ -z "${I18N[docker_err_podman]:-}" ]; then
    for _f in "$ROOT"/lib/lang/ru.d/{docker,amnezia,users,export}.sh; do
        # shellcheck disable=SC1090
        [ -f "$_f" ] && source "$_f"
    done
fi
if [ -f "$ROOT/lib/awgkeys.py" ] && [ -z "${GOAMNEZIA_TEST_FORCE_STUBS:-}" ]; then
    AWGKEYS="$ROOT/lib/awgkeys.py"
    printf '  lib/awgkeys.py: настоящий\n'
else
    export GOAMNEZIA_AWGKEYS="$ROOT/tests/fixtures/awgkeys_stub.py"
    AWGKEYS="$GOAMNEZIA_AWGKEYS"
    printf '  lib/awgkeys.py: заглушка tests/fixtures/awgkeys_stub.py\n'
fi
for _m in docker amnezia users export edgeips; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
# Путь к вендоренным скриптам всегда временный: в проектный каталог не пишем.
UPSTREAM_DIR="$TMP/upstream"
mkdir -p "$UPSTREAM_DIR/awg"
if [ -f "$ROOT/lib/upstream/awg/Dockerfile" ]; then
    cp "$ROOT"/lib/upstream/awg/* "$UPSTREAM_DIR/awg/"
    printf '  lib/upstream/awg: настоящий\n'
else
    printf 'FROM amneziavpn/amneziawg-go:latest\nRUN apk add --no-cache bash dumb-init\nENTRYPOINT ["dumb-init", "/opt/amnezia/start.sh"]\n' >"$UPSTREAM_DIR/awg/Dockerfile"
    printf '#!/bin/bash\nawg-quick up /opt/amnezia/awg/awg0.conf\niptables -C FORWARD -i awg0 -o eth0 -s $AWG_SUBNET_IP/$WIREGUARD_SUBNET_CIDR -j ACCEPT || iptables -A FORWARD -i awg0 -o eth0 -s $AWG_SUBNET_IP/$WIREGUARD_SUBNET_CIDR -j ACCEPT\ntail -f /dev/null\n' >"$UPSTREAM_DIR/awg/start.sh"
    printf '  lib/upstream/awg: минимальная заглушка\n'
fi
if [ -f "$ROOT/lib/upstream/images.json" ] && jq -e '."amneziawg-go".repo' "$ROOT/lib/upstream/images.json" >/dev/null 2>&1; then
    cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json"
    printf '  lib/upstream/images.json: настоящий\n'
else
    _zero=$(printf '0%.0s' $(seq 1 64))
    printf '{"amneziawg-go": {"repo": "amneziavpn/amneziawg-go", "tag": "3.1.20260828", "digest": "sha256:%s", "checked": "2026-09-08"}, "amnezia-wg": {"repo": "amneziavpn/amnezia-wg", "tag": "latest", "digest": "", "checked": "2026-09-08"}, "client_release": "5.0.2.1"}\n' "$_zero" >"$UPSTREAM_DIR/images.json"
    printf '  lib/upstream/images.json: заглушка\n'
fi
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"

# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию, а она делает предмиграционный бэкап —
# лишний архив ломал проверки списка бэкапов.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "options": {"drop_icmp": false}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"
chmod 600 "$USERS_FILE"

# ── 1. Установка профилей ─────────────────────────────────────────────────────
section "установка профилей (dry-run)"
run amnezia_profile_install awg2 40000 default
check "profile-install awg2: rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "stdout — только JSON-сводка" 'is_json "$out"' "$out"
check "сводка: profile=awg2, port=40000, line=2.0" \
    '[ "$(jq -r .profile <<<"$out")" = awg2 ] && [ "$(jq -r .port <<<"$out")" = 40000 ] && [ "$(jq -r .line <<<"$out")" = 2.0 ]' "$out"
check "stderr: DRY docker pull/build/run/network connect" \
    'grep -q "DRY: docker pull amneziavpn/amneziawg-go@sha256:" "$ERR" && grep -q "DRY: docker build -t amnezia-awg2" "$ERR" && grep -q "DRY: docker run -d --log-driver none --restart always --privileged" "$ERR" && grep -q "DRY: docker network connect amnezia-dns-net amnezia-awg2" "$ERR"' "$(grep DRY "$ERR" | head -5)"
check "stderr: DRY первый запуск start.sh как у приложения (exec -d chmod && start.sh)" 'grep -q "DRY: docker exec -d amnezia-awg2 sh -c .chmod a+x /opt/amnezia/start.sh && /opt/amnezia/start.sh" "$ERR"'
check "контекст сборки: FROM закреплён по дайджесту" 'grep -q "^FROM amneziavpn/amneziawg-go@sha256:" "$GOAMNEZIA_BUILD_ROOT/amnezia-awg2/Dockerfile"'
check "config.json: профиль записан, server_pubkey — base64 из 44 символов" \
    '[[ "$(ga_config_get .profiles.awg2.server_pubkey)" =~ ^[A-Za-z0-9+/]{43}=$ ]] && [ -n "$(ga_config_get .profiles.awg2.installed_at)" ]'
# Номер элемента в пути jq. Страж пропускал только .a.b и .a["ключ"], и
# `.client_dns[0]` отвергался наравне с попыткой инъекции: экран выбора DNS
# молча терял строку «Сейчас: …», человек выбирал вслепую. Инъекция при этом
# обязана остаться отвергнутой — проверяем оба конца.
check "путь jq с номером элемента читается, а опасный по-прежнему отвергается" \
    '[ "$(ga_config_get ".client_dns[0]")" = 1.1.1.1 ] && [ "$(ga_config_get ".client_dns[1]")" = 1.0.0.1 ] && ! ga_config_get ".client_dns[0]; rm -rf /" >/dev/null 2>&1 && ! ga_config_get ".a[\$x]" >/dev/null 2>&1' \
    "$(ga_config_get .client_dns 2>&1)"
SD2="$STATE_DIR/amnezia-awg2"
check "state/: awg0.conf, clientsTable, ключи сервера, start.sh" \
    '[ -f "$SD2/awg0.conf" ] && [ -f "$SD2/clientsTable" ] && [ -f "$SD2/wireguard_server_private_key.key" ] && [ -f "$SD2/wireguard_server_public_key.key" ] && [ -f "$SD2/wireguard_psk.key" ] && [ -f "$SD2/start.sh" ]'
check "state/: права 700 на каталог и 600 на файлы" '[ "$(perm "$SD2")" = 700 ] && [ "$(perm "$SD2/awg0.conf")" = 600 ] && [ "$(perm "$SD2/wireguard_server_private_key.key")" = 600 ]'
check "awg0.conf: ListenPort 40000, Address 10.8.2.0/24, приватный ключ из state" \
    'grep -q "^ListenPort = 40000$" "$SD2/awg0.conf" && grep -q "^Address = 10.8.2.0/24$" "$SD2/awg0.conf" && grep -qF "$(cat "$SD2/wireguard_server_private_key.key")" "$SD2/awg0.conf"'
check "server_pubkey в config.json соответствует ключу из state" '[ "$(cat "$SD2/wireguard_server_public_key.key")" = "$(ga_config_get .profiles.awg2.server_pubkey)" ]'
check "start.sh: подсеть подставлена, плейсхолдеров не осталось" 'grep -q "10.8.2.0/24" "$SD2/start.sh" && ! grep -q "AWG_SUBNET_IP" "$SD2/start.sh"'
check "clientsTable без пользователей — пустой массив" '[ "$(jq -c . "$SD2/clientsTable")" = "[]" ]'
check "audit: запись profile-install" 'grep -q "profile-install" "$AUDIT_LOG"' "AUDIT_LOG=$AUDIT_LOG"

run amnezia_profile_install awg3 40001
check "profile-install awg3 (3.1): rc=0, line=3.1" '[ "$rc" = 0 ] && [ "$(jq -r .line <<<"$out")" = 3.1 ]' "$(errs)"
check "awg3: HeaderProtectionKey в серверном конфиге" 'grep -q "^HeaderProtectionKey = " "$STATE_DIR/amnezia-awg3/awg0.conf"'

run amnezia_profile_install awg1 40001
check "порт 40001 занят профилем awg3 → ошибка port_busy, stdout пуст" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_busy ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
check "код ошибки продублирован в GA_LAST_ERROR_CODE (common.sh)" '[ "$GA_LAST_ERROR_CODE" = port_busy ]' "last=$GA_LAST_ERROR_CODE"
run amnezia_profile_install awg2 40005
check "повторная установка awg2 → profile_exists" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = profile_exists ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_install awg9
check "неизвестный профиль → invalid_profile (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_profile ]' "rc=$rc code=$GA_ERR_CODE"
# §1d.2 №9: место под образ и слои сборки проверяется ДО docker, а не после —
# иначе человек видит английскую ошибку docker вместо «мало места».
_df_orig=$(declare -f df 2>/dev/null)
df() { printf 'Filesystem 1M-blocks Used Available Use%% Mounted\n/dev/x 100 99 1 99%% /\n'; }
# «Диск полон» — подменённым df: фикстура места прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на этот вызов снята
GOAMNEZIA_DF_FIXTURE='' run amnezia_profile_install awg1 40007
check "установка профиля на забитом диске → low_disk, docker не звался" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = low_disk ] && ! grep -q "docker build" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs)"
unset -f df
[ -n "$_df_orig" ] && eval "$_df_orig"
run amnezia_profile_install awg1 40002 bogus
check "неизвестный пресет → invalid_preset (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_preset ]' "rc=$rc code=$GA_ERR_CODE"

# Имена amnezia-awg1/2/3 у нас те же, что у контейнеров приложения Amnezia, и
# установка сносила такой «остаток» вместе с ключами людей (приватные ключи
# клиентов хранит приложение — перевыпустить нечего). Контейнер с [Peer] без
# профиля в config.json — отказ foreign_container до ключей и сборки, даже
# остановленный (его конфиг читается через docker cp). Остаток без людей
# пропускается: его, как раньше, снесёт шаг 4.
CFS="$TMP/cfs"
mkdir -p "$CFS/amnezia-awg1/opt/amnezia/awg"
printf '[Interface]\nPrivateKey = x\nListenPort = 40002\n\n[Peer]\nPublicKey = a\nAllowedIPs = 10.8.1.2/32\n\n [peer] \nPublicKey = b\nAllowedIPs = 10.8.1.3/32\n' \
    >"$CFS/amnezia-awg1/opt/amnezia/awg/awg0.conf"
echo exited >"$CFS/amnezia-awg1/.state"
GOAMNEZIA_CONTAINER_FS_DIR="$CFS" run amnezia_profile_install awg1 40002 router
check "чужой контейнер с людьми (остановлен) → foreign_container: ни ключей, ни сборки, ни docker rm" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = foreign_container ] && ! grep -q "docker build\|docker rm\|docker stop\|docker run" "$ERR" && [ ! -e "$STATE_DIR/amnezia-awg1" ] && ! ga_profile_installed awg1' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
check "в отказе — сколько людей и куда идти: импорт" \
    'grep -q "подключений в нём: 2" <<<"$GA_ERR_MSG" && grep -q "Импорт существующей установки Amnezia" <<<"$GA_ERR_MSG"' "$GA_ERR_MSG"
rm -f "$CFS/amnezia-awg1/.state"
GOAMNEZIA_CONTAINER_FS_DIR="$CFS" run ga_amn_foreign_peers amnezia-awg1
check "работающий чужой контейнер: 2 пира" '[ "$out" = 2 ]' "out=$out"
printf '[Interface]\nPrivateKey = x\nListenPort = 40002\n' >"$CFS/amnezia-awg1/opt/amnezia/awg/awg0.conf"
GOAMNEZIA_CONTAINER_FS_DIR="$CFS" run ga_amn_foreign_guard amnezia-awg1
check "конфиг без [Peer] — остаток, страж пропускает" '[ "$rc" = 0 ] && [ -z "$GA_ERR_CODE" ]' "rc=$rc code=$GA_ERR_CODE"
rm -f "$CFS/amnezia-awg1/opt/amnezia/awg/awg0.conf"
GOAMNEZIA_CONTAINER_FS_DIR="$CFS" run ga_amn_foreign_peers amnezia-awg1
check "контейнер без конфига — 0 пиров" '[ "$rc" = 0 ] && [ "$out" = 0 ]' "rc=$rc out=$out"
rm -rf "$CFS"

run amnezia_profile_install awg1 40002 router
check "profile-install awg1 (пресет router): rc=0, line=1.0" '[ "$rc" = 0 ] && [ "$(jq -r .line <<<"$out")" = 1.0 ]' "$(errs)"
check "три профиля установлены" '[ "$(ga_profiles_installed | sort | tr "\n" " ")" = "awg1 awg2 awg3 " ]'
# Порт профиля — из низкого диапазона 1024–9999 (решение владельца 10.09.2026),
# общеизвестные пропускаются; прежние 30000–49999 остались только запасным путём.
run ga_amn_pick_port awg2
check "случайный порт: ga_amn_pick_port даёт 1024–9999" '[[ "$out" =~ ^[0-9]+$ ]] && [ "$out" -ge 1024 ] && [ "$out" -le 9999 ]' "out=$out"
check "порт не из общеизвестных (8080, 3306, 1985…)" '! ga_port_wellknown "$out"' "out=$out"
_lowports_ok=1
for _i in 1 2 3 4 5 6 7 8 9 10; do
    run ga_amn_pick_port awg2
    { [ "$out" -ge 1024 ] && [ "$out" -le 9999 ] && ! ga_port_wellknown "$out"; } || _lowports_ok=0
done
check "десять подряд — все в 1024–9999 и ни одного общеизвестного" '[ "$_lowports_ok" = 1 ]' "out=$out"
check "ga_port_wellknown знает про 443-соседей: 8080 да, 4711 нет" 'ga_port_wellknown 8080 && ! ga_port_wellknown 4711'
run ga_port_random 1024 9999
check "ga_port_random держится в границах" '[ "$out" -ge 1024 ] && [ "$out" -le 9999 ]' "out=$out"

# Регрессия стенда: установка через мост (--action=profile-install) не проходит
# мастера, а qrencode ставил только мастер — на таком сервере user-key
# --format=all отказывал целиком (qrencode_missing). Профиль qr теперь
# доустанавливает и сама установка профиля.
QRMARK="$TMP/qr-deps-called"
deps_install_missing() { printf '%s\n' "$*" >>"$QRMARK"; return 0; }
FAKEBIN="$TMP/fakebin"; mkdir -p "$FAKEBIN"
run ga_amn_ensure_qr_dep
check "ga_amn_ensure_qr_dep в dry-run не трогает apt" '[ "$rc" = 0 ] && [ ! -f "$QRMARK" ]' "$(cat "$QRMARK" 2>/dev/null)"
( GOAMNEZIA_DRY_RUN=0 PATH="$FAKEBIN"; ga_amn_ensure_qr_dep >/dev/null 2>&1 )
check "без qrencode установка профиля просит профиль зависимостей qr" \
    'grep -q -- "--yes qr" "$QRMARK"' "$(cat "$QRMARK" 2>/dev/null)"
: >"$QRMARK"
printf '#!/bin/sh\nexit 0\n' >"$FAKEBIN/qrencode"; chmod +x "$FAKEBIN/qrencode"
( GOAMNEZIA_DRY_RUN=0 PATH="$FAKEBIN"; ga_amn_ensure_qr_dep >/dev/null 2>&1 )
check "с готовым qrencode deps не зовём (нет «ставить нечего» на каждой установке)" \
    '[ ! -s "$QRMARK" ]' "$(cat "$QRMARK" 2>/dev/null)"
unset -f deps_install_missing

# ── 2. Пользователи и IP ─────────────────────────────────────────────────────
section "пользователи"
run users_add anna "Анна"
check "users_add anna (все профили): rc=0, stdout пуст" '[ "$rc" = 0 ] && [ -z "$out" ]' "$(errs)"
check "anna: пиры в awg1, awg2, awg3" '[ "$(jq -r ".users[]|select(.name==\"anna\")|.peers|keys|join(\",\")" "$USERS_FILE")" = "awg1,awg2,awg3" ]'
run users_add bob Bob awg2 1 30
check "users_add bob (awg2, лимит 1 ГБ, 30 дней): rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "stderr при users_add: DRY syncconf через bash с process substitution" 'grep -q "awg syncconf awg0 <(awg-quick strip /opt/amnezia/awg/awg0.conf)" "$ERR"' "$(grep DRY "$ERR" | head -5)"
check "bob: quota_bytes=1073741824, expires через ~30 дней" \
    '[ "$(jq -r ".users[]|select(.name==\"bob\")|.quota_bytes" "$USERS_FILE")" = 1073741824 ] && [[ "$(jq -r ".users[]|select(.name==\"bob\")|.expires" "$USERS_FILE")" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$ ]]'
check "IP: anna awg2=10.8.2.1, bob awg2=10.8.2.2, anna awg3=10.8.3.1" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.ip" "$USERS_FILE")" = 10.8.2.1 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg2.ip" "$USERS_FILE")" = 10.8.2.2 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg3.ip" "$USERS_FILE")" = 10.8.3.1 ]'
run users_alloc_ip awg2
check "users_alloc_ip awg2 → 10.8.2.3" '[ "$out" = 10.8.2.3 ]' "out=$out"
check "users.json: права 0600" '[ "$(perm "$USERS_FILE")" = 600 ]'
check "серверный конфиг awg2: оба пира, PSK у каждого свой" \
    '[ "$(grep -c "^\[Peer\]" "$SD2/awg0.conf")" = 2 ] && [ "$(grep "^PresharedKey" "$SD2/awg0.conf" | sort -u | wc -l | tr -d " ")" = 2 ]'
check "clientsTable awg2: официальный формат, clientName из label" \
    '[ "$(jq -r ".[]|select(.userData.clientName==\"Анна\")|.clientId" "$SD2/clientsTable")" = "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.pubkey" "$USERS_FILE")" ] && [ "$(jq -r ".[1].userData.creationDate" "$SD2/clientsTable")" != null ]'
run users_del bob
check "users_del bob: rc=0, запись исчезла, конфиг без его пира" \
    '[ "$rc" = 0 ] && ! jq -e ".users[]|select(.name==\"bob\")" "$USERS_FILE" >/dev/null 2>&1 && [ "$(grep -c "^\[Peer\]" "$SD2/awg0.conf")" = 1 ]' "$(errs)"
run users_alloc_ip awg2
check "аллокатор переиспользует освободившийся 10.8.2.2" '[ "$out" = 10.8.2.2 ]' "out=$out"
run users_add bob Bob awg2 1 30
run users_add carol Carol awg2,awg3 0 1
check "IP в awg2 без пересечений (3 пользователя, 3 разных адреса)" \
    '[ "$(jq -r ".users[]|.peers.awg2.ip" "$USERS_FILE" | sort -u | wc -l | tr -d " ")" = 3 ]'
run users_add "bad name"
check "users_add с недопустимым именем → invalid_name (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_name ]' "rc=$rc code=$GA_ERR_CODE"
run users_add anna
check "users_add дубликат → user_exists" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = user_exists ]' "rc=$rc code=$GA_ERR_CODE"
run users_add dave Dave awg2 999999
check "лимит выше 100000 ГБ → invalid_number" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_number ]' "rc=$rc code=$GA_ERR_CODE"
run users_add dave Dave awg9
check "users_add в неизвестный профиль → invalid_profile" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_profile ]' "rc=$rc code=$GA_ERR_CODE"

section "список и карточка без секретов"
run users_list
check "users_list: rc=0, чистый JSON-массив из 3 записей" '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq length <<<"$out")" = 3 ]' "$out"
check "users_list: нет privkey и psk, есть pubkey и profiles" \
    '! grep -q "privkey" <<<"$out" && ! grep -q "\"psk\"" <<<"$out" && [ "$(jq -r ".[0].peers.awg2.pubkey|length" <<<"$out")" = 44 ] && [ "$(jq -r ".[0].profiles|length" <<<"$out")" = 3 ]'
run users_show anna
check "users_show anna: JSON без privkey/psk" '[ "$rc" = 0 ] && is_json "$out" && ! grep -q "privkey\|\"psk\"" <<<"$out" && [ "$(jq -r .label <<<"$out")" = "Анна" ]'
# has_key по каждому пиру: приватный ключ наружу не отдаём, но бот и панель
# обязаны отличать пир, по которому ключ выдаётся, от импортированного — иначе
# они рисуют кнопки, которые гарантированно отвечают отказом.
check "users_show anna: у пира has_key=true, приватного ключа в ответе нет" \
    '[ "$(jq -r ".peers.awg2.has_key" <<<"$out")" = true ] && [ "$(jq -r ".peers.awg2.privkey // \"нет\"" <<<"$out")" = "нет" ]' "$(jq -c ".peers.awg2" <<<"$out")"
run users_show nobody
check "users_show неизвестного → user_not_found (rc=3), stdout пуст" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = user_not_found ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
run users_rename anna "Анна Петрова"
check "users_rename: label в users.json и в clientsTable" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.label" "$USERS_FILE")" = "Анна Петрова" ] && jq -e ".[]|select(.userData.clientName==\"Анна Петрова\")" "$SD2/clientsTable" >/dev/null' "$(errs)"

# ── 3. Экспорт ───────────────────────────────────────────────────────────────
section "экспорт ключей"
run export_conf anna awg2
conf="$out"
check "export_conf anna awg2: rc=0, непустой" '[ "$rc" = 0 ] && [ -n "$conf" ]' "$(errs)"
check "conf: [Interface]/[Peer], Endpoint 203.0.113.10:40000, AllowedIPs 0.0.0.0/0" \
    'grep -q "^\[Interface\]" <<<"$conf" && grep -q "^\[Peer\]" <<<"$conf" && grep -q "^Endpoint = 203.0.113.10:40000$" <<<"$conf" && grep -q "^AllowedIPs = 0.0.0.0/0" <<<"$conf"'
check "conf: Address 10.8.2.1/32, DNS, приватный ключ пользователя, PublicKey сервера" \
    'grep -q "^Address = 10.8.2.1/32" <<<"$conf" && grep -q "^DNS = 1.1.1.1, 1.0.0.1" <<<"$conf" && grep -qF "PrivateKey = $(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS_FILE")" <<<"$conf" && grep -qF "PublicKey = $(ga_config_get .profiles.awg2.server_pubkey)" <<<"$conf"'
check "conf awg2 (2.0): Jc, S3, диапазон в H1" 'grep -q "^Jc = " <<<"$conf" && grep -q "^S3 = " <<<"$conf" && grep -qE "^H1 = [0-9]+-[0-9]+$" <<<"$conf"'
run export_conf anna awg3
check "conf awg3 (3.1): HeaderProtectionKey, Endpoint :40001" 'grep -q "^HeaderProtectionKey = " <<<"$out" && grep -q ":40001$" <<<"$out"'
run export_conf anna awg1
check "conf awg1 (1.0, router): без I1, S3 и 3.x-полей" '[ -n "$out" ] && ! grep -q "^I1 = " <<<"$out" && ! grep -q "^S3 = " <<<"$out" && ! grep -q "HeaderProtectionKey" <<<"$out"'
run export_conf bob awg3
check "export_conf без пира в профиле → peer_not_found (rc=3), stdout пуст" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = peer_not_found ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"

run export_vpn anna awg2
vpn="$out"
check "export_vpn: rc=0, одна строка vpn://" '[ "$rc" = 0 ] && [[ "$vpn" == vpn://* ]] && [ "$(wc -l <<<"$vpn" | tr -d " ")" = 1 ]' "$(errs)"
dec=$(python3 "$AWGKEYS" vpn-decode "$vpn" 2>"$ERR"); rc=$?
check "vpn-decode: валидный JSON" '[ "$rc" = 0 ] && is_json "$dec"' "$(errs)"
check "vpn-decode: container amnezia-awg2, hostName 203.0.113.10, port 40000, line 2.0" \
    '[ "$(jq -r .container <<<"$dec")" = amnezia-awg2 ] && [ "$(jq -r .hostName <<<"$dec")" = 203.0.113.10 ] && [ "$(jq -r .port <<<"$dec")" = 40000 ] && [ "$(jq -r .line <<<"$dec")" = 2.0 ]' "$(jq -c "{container,hostName,port,line}" <<<"$dec")"

# ── 3а. Свой адрес сервера у пользователя (несколько адресов, docs/EDGEIPS.md §3.1) ──
section "endpoint_ip: свой адрес сервера в ключе пользователя"
EDGEBIN="$TMP/edgebin"; mkdir -p "$EDGEBIN"
cat >"$EDGEBIN/ip" <<'EOF'
#!/bin/bash
cat "$(dirname "$0")/addrs" 2>/dev/null
EOF
chmod +x "$EDGEBIN/ip"
edge_addrs() { : >"$EDGEBIN/addrs"; local a; for a in "$@"; do printf '2: eth0    inet %s/32 scope global eth0\n' "$a" >>"$EDGEBIN/addrs"; done; }
edge_addrs 203.0.113.10 198.51.100.20
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 198.51.100.20
check "users_set_endpoint_ip anna 198.51.100.20: rc=0, ответ с адресом, поле в users.json" \
    '[ "$rc" = 0 ] && [ "$(jq -r .endpoint_ip <<<"$out")" = 198.51.100.20 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.endpoint_ip" "$USERS_FILE")" = 198.51.100.20 ]' "rc=$rc $out $(errs)"
check "ответ модуля: в ключе теперь 198.51.100.20, прежде общий — resend_required=true, domain_endpoint=false" \
    '[ "$(jq -r .endpoint <<<"$out")" = 198.51.100.20 ] && [ "$(jq -r .previous_endpoint_ip <<<"$out")" = "" ] && [ "$(jq -r .resend_required <<<"$out")" = true ] && [ "$(jq -r .domain_endpoint <<<"$out")" = false ]' "$out"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 198.51.100.20
check "тот же адрес ещё раз: ключ не меняется — resend_required=false" \
    '[ "$rc" = 0 ] && [ "$(jq -r .resend_required <<<"$out")" = false ] && [ "$(jq -r .previous_endpoint_ip <<<"$out")" = 198.51.100.20 ]' "$out"
run users_show anna
check "users_show: endpoint_ip в публичном виде, приватного ключа нет" \
    '[ "$(jq -r .endpoint_ip <<<"$out")" = 198.51.100.20 ] && ! grep -q privkey <<<"$out"' "$out"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "ключ anna: Endpoint — свой адрес, остальные строки байт в байт прежние" \
    '[ "$rc" = 0 ] && grep -q "^Endpoint = 198.51.100.20:40000$" <<<"$out" && [ "$(grep -v "^Endpoint = " <<<"$out")" = "$(grep -v "^Endpoint = " <<<"$conf")" ]' "$(grep Endpoint <<<"$out") $(errs)"
PATH="$EDGEBIN:$PATH" run export_vpn anna awg2
# Своя переменная: общая $dec нужна проверкам vpn-decode ниже, и перезапись её ключом со
# своим адресом роняла их сравнение с исходным .conf.
dec_own=$(python3 "$AWGKEYS" vpn-decode "$out" 2>/dev/null)
check "vpn:// anna: hostName — свой адрес" '[ "$(jq -r .hostName <<<"$dec_own")" = 198.51.100.20 ]' "$(jq -c "{hostName}" <<<"$dec_own" 2>/dev/null)"
ga_config_merge '{"endpoint_host":"vpn.example.com"}' >/dev/null 2>&1
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "общий Endpoint — домен: домен побеждает свой адрес (адрес выбирает DNS)" 'grep -q "^Endpoint = vpn.example.com:40000$" <<<"$out"' "$(grep Endpoint <<<"$out")"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 198.51.100.20
check "при доменном Endpoint: endpoint — домен, domain_endpoint=true, resend_required=false" \
    '[ "$rc" = 0 ] && [ "$(jq -r .endpoint <<<"$out")" = vpn.example.com ] && [ "$(jq -r .domain_endpoint <<<"$out")" = true ] && [ "$(jq -r .resend_required <<<"$out")" = false ]' "$out"
ga_config_merge '{"endpoint_host":""}' >/dev/null 2>&1
# Домен сервера по умолчанию (владелец 17.09.2026: «VPN на 443 под доменом, а файл выдаётся с IP»). В режиме
# 3, Pro, — в ключах домен, пока Endpoint не вписан руками; в остальных режимах IP. Свой адрес пользователя
# побеждает домен по умолчанию: адрес владелец задал сам, а домен никто не выбирал.
cfg_before_domain=$(cat "$CONFIG_FILE")
ga_config_merge '{"mode":"pro","domain":"vpn.example.com","site":{"cert":"letsencrypt"}}' >/dev/null 2>&1
run ga_export_endpoint_host
check "pro, домен с Let's Encrypt, Endpoint не вписан: общий адрес в ключах — домен" '[ "$rc" = 0 ] && [ "$out" = vpn.example.com ]' "rc=$rc out=[$out]"
run ga_export_endpoint_source
check "источник общего Endpoint — domain" '[ "$out" = domain ]' "$out"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "свой адрес пользователя побеждает домен по умолчанию" 'grep -q "^Endpoint = 198.51.100.20:40000$" <<<"$out"' "$(grep Endpoint <<<"$out") $(errs)"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna ""
check "свой адрес снят: в ключе домен, конфиг прислать заново, domain_endpoint=true" \
    '[ "$rc" = 0 ] && [ "$(jq -r .endpoint <<<"$out")" = vpn.example.com ] && [ "$(jq -r .resend_required <<<"$out")" = true ] && [ "$(jq -r .domain_endpoint <<<"$out")" = true ]' "$out"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "ключ без своего адреса: Endpoint — домен, остальные строки прежние" \
    '[ "$rc" = 0 ] && grep -q "^Endpoint = vpn.example.com:40000$" <<<"$out" && [ "$(grep -v "^Endpoint = " <<<"$out")" = "$(grep -v "^Endpoint = " <<<"$conf")" ]' "$(grep Endpoint <<<"$out") $(errs)"
PATH="$EDGEBIN:$PATH" run export_vpn anna awg2
check "vpn:// без своего адреса: hostName — домен" '[ "$(python3 "$AWGKEYS" vpn-decode "$out" 2>/dev/null | jq -r .hostName)" = vpn.example.com ]' "$(errs)"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 198.51.100.20
ga_config_merge '{"site":{"cert":"selfsigned"}}' >/dev/null 2>&1
run ga_export_endpoint_host
check "самоподписанный сертификат: домен не подтверждён — в ключах IP" '[ "$out" = 203.0.113.10 ]' "$out"
ga_config_merge '{"mode":"lite","site":{"cert":"letsencrypt"}}' >/dev/null 2>&1
run ga_export_endpoint_host
check "Lite с доменом от прежнего режима: сайта нет — в ключах IP" '[ "$out" = 203.0.113.10 ]' "$out"
ga_config_merge '{"mode":"lite_site","endpoint_host":"203.0.113.10"}' >/dev/null 2>&1
run ga_export_endpoint_host
check "lite_site, IP вписан руками: в ключах IP, источник host" '[ "$out" = 203.0.113.10 ] && [ "$(ga_export_endpoint_source)" = host ]' "$out"
ga_config_merge '{"endpoint_host":"vpn.example.com"}' >/dev/null 2>&1
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "вписанный домен по-прежнему побеждает свой адрес" 'grep -q "^Endpoint = vpn.example.com:40000$" <<<"$out"' "$(grep Endpoint <<<"$out")"
ga_config_merge '{"endpoint_host":""}' >/dev/null 2>&1
run ga_export_endpoint_host
check "lite_site (VPN на своём порту в стороне от домена), поле пусто: в ключах IP" '[ "$out" = 203.0.113.10 ] && [ "$(ga_export_endpoint_source)" = ip ]' "$out"
ga_config_merge '{"mode":"pro","site":{"cert":""}}' >/dev/null 2>&1
run ga_export_endpoint_host
check "pro у старой установки без site.cert (читается как Let's Encrypt): в ключах домен" '[ "$out" = vpn.example.com ]' "$out"
printf '%s\n' "$cfg_before_domain" >"$CONFIG_FILE" && chmod 600 "$CONFIG_FILE"
edge_addrs 203.0.113.10
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "свой адрес пропал с машины → address_gone, ключа нет (он бы не соединился)" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = address_gone ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
: >"$EDGEBIN/addrs"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "список адресов машины не прочитался — выдача не блокируется догадкой" \
    '[ "$rc" = 0 ] && grep -q "^Endpoint = 198.51.100.20:40000$" <<<"$out"' "rc=$rc $(errs)"
edge_addrs 203.0.113.10 198.51.100.20
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 192.0.2.55
check "адрес, которого нет на машине → address_not_here (rc=2), поле прежнее" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = address_not_here ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.endpoint_ip" "$USERS_FILE")" = 198.51.100.20 ]' "rc=$rc code=$GA_ERR_CODE"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 0.0.0.0
check "0.0.0.0 → invalid_ip: форму проходит, но адресом сервера не бывает" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ip ]' "rc=$rc code=$GA_ERR_CODE"
edge_addrs 10.0.0.5
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 198.51.100.20
check "машина видит только частный адрес (за NAT) → addresses_unknown, поле прежнее" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = addresses_unknown ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.endpoint_ip" "$USERS_FILE")" = 198.51.100.20 ]' "rc=$rc code=$GA_ERR_CODE"
edge_addrs 203.0.113.10 198.51.100.20
jq '.users |= map(if .name == "anna" then .endpoint_ip = "0.0.0.0" else . end)' "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE" && chmod 600 "$USERS_FILE"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "в поле руками записан 0.0.0.0 → выдача отвечает address_gone, а не ключом в никуда" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = address_gone ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
jq '.users |= map(if .name == "anna" then .endpoint_ip = "198.51.100.20" else . end)' "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE" && chmod 600 "$USERS_FILE"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna 999.1.1.1
check "не IPv4 → invalid_ip (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_ip ]' "rc=$rc code=$GA_ERR_CODE"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip nobody 198.51.100.20
check "неизвестный пользователь → user_not_found" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = user_not_found ]' "rc=$rc code=$GA_ERR_CODE"
PATH="$EDGEBIN:$PATH" run users_set_endpoint_ip anna ""
check "адрес снят: в ключе общий 203.0.113.10, прежний свой назван, resend_required=true" \
    '[ "$rc" = 0 ] && [ "$(jq -r .endpoint <<<"$out")" = 203.0.113.10 ] && [ "$(jq -r .previous_endpoint_ip <<<"$out")" = 198.51.100.20 ] && [ "$(jq -r .resend_required <<<"$out")" = true ]' "$out"
PATH="$EDGEBIN:$PATH" run export_conf anna awg2
check "адрес снят (пусто) → поля нет, ключ anna байт в байт прежний" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|has(\"endpoint_ip\")" "$USERS_FILE")" = false ] && [ "$out" = "$conf" ]' "$(diff <(echo "$conf") <(echo "$out") | head -5)"
run amnezia_profile_params awg2
params2="$out"
check "amnezia_profile_params: JSON с Jc/H1/S1" 'is_json "$params2" && [ -n "$(jq -r .Jc <<<"$params2")" ]' "$params2"
check "vpn-decode: параметры Jc/H1/S1/S3 совпадают с профилем" \
    '[ "$(jq -r ".params.Jc" <<<"$dec")" = "$(jq -r .Jc <<<"$params2")" ] && [ "$(jq -r ".params.H1" <<<"$dec")" = "$(jq -r .H1 <<<"$params2")" ] && [ "$(jq -r ".params.S1" <<<"$dec")" = "$(jq -r .S1 <<<"$params2")" ] && [ "$(jq -r ".params.S3" <<<"$dec")" = "$(jq -r .S3 <<<"$params2")" ]' "$(jq -c .params <<<"$dec")"
check "vpn-decode: conf_text — тот же .conf (приватный ключ, Endpoint, PSK)" \
    'grep -qF "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS_FILE")" <<<"$(jq -r .conf_text <<<"$dec")" && grep -qF "203.0.113.10:40000" <<<"$(jq -r .conf_text <<<"$dec")" && [ "$(jq -r .client.psk <<<"$dec")" = "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.psk" "$USERS_FILE")" ]'
check "vpn-decode: описание с label и линейкой, DNS" 'grep -qF "Анна Петрова" <<<"$(jq -r .description <<<"$dec")" && grep -qF "AmneziaWG 2.0" <<<"$(jq -r .description <<<"$dec")" && [ "$(jq -r ".dns|join(\",\")" <<<"$dec")" = "1.1.1.1,1.0.0.1" ]'
run export_bundle anna awg2 bogus
check "export_bundle с неизвестным форматом → invalid_format (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_format ]' "rc=$rc code=$GA_ERR_CODE"
run export_bundle anna awg2 conf
check "export_bundle conf: JSON с conf, пустыми vpn/QR, line 2.0, min_client 4.8.12.9" \
    '[ "$rc" = 0 ] && is_json "$out" && [ -n "$(jq -r .conf <<<"$out")" ] && [ "$(jq -r .vpn <<<"$out")" = "" ] && [ "$(jq -r ".qr_vpn_png_b64|length" <<<"$out")" = 0 ] && [ "$(jq -r .line <<<"$out")" = 2.0 ] && [ "$(jq -r .min_client <<<"$out")" = 4.8.12.9 ]' "$(errs)"
run export_bundle anna awg3 vpn
check "export_bundle awg3 vpn: min_client 5.0.1.5, vpn:// заполнен" '[ "$rc" = 0 ] && [ "$(jq -r .min_client <<<"$out")" = 5.0.1.5 ] && [[ "$(jq -r .vpn <<<"$out")" == vpn://* ]]' "$(errs)"
if command -v qrencode >/dev/null 2>&1; then
    run export_bundle anna awg2 all
    nchunks=$(python3 "$AWGKEYS" qr-chunks "$vpn" | jq length)
    check "export_bundle all: rc=0, JSON" '[ "$rc" = 0 ] && is_json "$out"' "$(errs)"
    check "bundle: qr_conf_png_b64 — PNG (сигнатура после base64)" '[ "$(jq -r .qr_conf_png_b64 <<<"$out" | base64 -d 2>/dev/null | head -c 8 | od -An -tx1 | tr -d " \n")" = 89504e470d0a1a0a ]'
    check "bundle: qr_vpn_png_b64 — по PNG на чанк ($nchunks)" '[ "$(jq -r ".qr_vpn_png_b64|length" <<<"$out")" = "$nchunks" ] && [ "$(jq -r ".qr_vpn_png_b64[0]" <<<"$out" | base64 -d 2>/dev/null | head -c 4 | od -An -tx1 | tr -d " \n")" = 89504e47 ]'
    run export_qr_vpn_pngs anna awg2 "$TMP/qr"
    check "export_qr_vpn_pngs: файлы 01.png… по числу чанков, права 600" \
        '[ "$rc" = 0 ] && [ "$(wc -l <<<"$out" | tr -d " ")" = "$nchunks" ] && [ -f "$TMP/qr/01.png" ] && [ "$(perm "$TMP/qr/01.png")" = 600 ]' "$(errs)"
    run export_qr_png "$conf" "$TMP/conf.png"
    check "export_qr_png: .conf в PNG" '[ "$rc" = 0 ] && [ "$(head -c 4 "$TMP/conf.png" | od -An -tx1 | tr -d " \n")" = 89504e47 ]' "$(errs)"
else
    skipped "bundle all / QR" "нет qrencode"
fi

# ── 4. Выключение и включение ────────────────────────────────────────────────
section "выключение / включение"
puba=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.pubkey' "$USERS_FILE")
run users_disable anna
check "users_disable anna: rc=0, enabled=false, все пиры active=false" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = false ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|[.peers[].active]|all" "$USERS_FILE")" = false ]' "$(errs)"
check "конфиг awg2 без пира anna, clientsTable запись хранит" '! grep -qF "$puba" "$SD2/awg0.conf" && grep -qF "$puba" "$SD2/clientsTable"'
run export_conf anna awg2
check "export_conf выключенного всё ещё работает (ключ прежний)" '[ "$rc" = 0 ] && grep -qF "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS_FILE")" <<<"$out"'
# §1d.2 №1: ключ выключенному выдаём, но НЕ молча. Раньше выдача не смотрела ни
# на .enabled, ни на .active: владелец отдавал QR, человек не подключался, и
# причина не была видна нигде.
check "выдача ключа выключенному говорит об этом вслух" \
    'grep -qF "$(tf export_warn_disabled anna awg2)" "$ERR"' "$(errs)"
run export_bundle anna awg2 conf
check "ответ моста несёт признак disabled=true" \
    '[ "$rc" = 0 ] && [ "$(jq -r .disabled <<<"$out")" = true ]' "out=$out $(errs)"
check "внутри одной выдачи предупреждение звучит ровно один раз" \
    '[ "$(grep -cF "$(tf export_warn_disabled anna awg2)" "$ERR")" = 1 ]' "$(errs)"
run users_enable anna
check "users_enable anna: enabled=true, пиры active=true, пир вернулся в конфиг" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = true ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|[.peers[].active]|all" "$USERS_FILE")" = true ] && grep -qF "$puba" "$SD2/awg0.conf"' "$(errs)"
run export_bundle anna awg2 conf
check "включённому disabled=false, лишних слов нет" \
    '[ "$rc" = 0 ] && [ "$(jq -r .disabled <<<"$out")" = false ] && ! grep -qF "$(tf export_warn_disabled anna awg2)" "$ERR"' "out=$out $(errs)"
run users_add_peer bob awg3
check "users_add_peer bob awg3: пир появился, IP 10.8.3.3" '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.ip" "$USERS_FILE")" = 10.8.3.3 ]' "$(errs)"
run users_add_peer bob awg3
check "повторный users_add_peer → peer_exists" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = peer_exists ]' "rc=$rc code=$GA_ERR_CODE"
# Перевыпуск — ЗАМЕНА, а не пара «снять и выдать»: пир остаётся на месте, ключ и
# открытая часть меняются, а окна, в котором у человека нет доступа, не возникает.
oldpriv=$(jq -r '.users[]|select(.name=="bob")|.peers.awg3.privkey' "$USERS_FILE")
oldpub=$(jq -r '.users[]|select(.name=="bob")|.peers.awg3.pubkey' "$USERS_FILE")
run users_reissue_peer bob awg3
oldip=$(jq -r '.users[]|select(.name=="bob")|.peers.awg3.ip' "$USERS_FILE")
check "users_reissue_peer bob awg3: пир на месте, ключ новый, адрес прежний, старый открытый ключ убран из конфига" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.privkey" "$USERS_FILE")" != "$oldpriv" ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.pubkey" "$USERS_FILE")" != "$oldpub" ] && [ -n "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.privkey" "$USERS_FILE")" ] && ! grep -qF "$oldpub" "$STATE_DIR/amnezia-awg3/awg0.conf" && grep -qF "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.pubkey" "$USERS_FILE")" "$STATE_DIR/amnezia-awg3/awg0.conf" && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.ip" "$USERS_FILE")" = "$oldip" ]' "$(errs)"
run users_reissue_peer bob awg1
check "users_reissue_peer на профиль без пира → peer_not_found, чужого не трогаем" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = peer_not_found ] && [ -n "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3.privkey" "$USERS_FILE")" ]' "rc=$rc code=$GA_ERR_CODE"
run users_del_peer bob awg3
check "users_del_peer bob awg3: пира нет" '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg3" "$USERS_FILE")" = null ]' "$(errs)"

# Регрессия стенда: awk -F'=' резал хвостовой «=» base64-ключа, и сверка с
# users.json / GA_EXPECT_DROP не совпадала никогда — каждая запись конфига
# ругалась «в контейнере чужой пир» на собственных пользователей.
PKA="Pc4lXgUaZpo546Bw5UhMjf11UYuZw0/POtPTm/RwlzQ="
PKB="5RM2OMNCBDT2H1H/VzKIktkwqhvrvxtfNoTGEtzpwkY="
out=$(printf '[Interface]\nPrivateKey = xx\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.8.2.1/32\n\n[Peer]\n  PublicKey=%s\n' "$PKA" "$PKB" | ga_amn_conf_pubkeys)
check "ga_amn_conf_pubkeys отдаёт ключи целиком, вместе с хвостовым =" \
    '[ "$out" = "$PKA
$PKB" ]' "out=$out"

# ── 5. Трафик, лимиты, сроки ─────────────────────────────────────────────────
section "users_quota_tick на подложенном awg show"
pubb=$(jq -r '.users[]|select(.name=="bob")|.peers.awg2.pubkey' "$USERS_FILE")
sed -e "s|@PUB_ANNA@|$puba|" -e "s|@PUB_BOB@|$pubb|" "$ROOT/tests/fixtures/awg_show_sample.txt" >"$TMP/awgshow1.txt"
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/awgshow1.txt"
run amnezia_show awg2
check "amnezia_show: разбор dump → 2 пира, rx/tx числа, endpoint" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq length <<<"$out")" = 2 ] && [ "$(jq -r ".[0].rx" <<<"$out")" = 1000 ] && [ "$(jq -r ".[0].endpoint" <<<"$out")" = "198.51.100.7:51820" ] && [ "$(jq -r ".[1].latest_handshake_ts" <<<"$out")" = 1788868900 ]' "$out"
check "amnezia_show: PSK и приватный ключ наружу не выходят" '! grep -q "UFNLUFNL" <<<"$out" && ! grep -q "cFJJVkFURUtFWVBS" <<<"$out"'
# Регрессия стенда: у awg строка интерфейса длиннее восьми полей (в ней все
# параметры маскировки), и фильтр «NF >= 8» пропускал её в ответ как пира —
# первым «pubkey» уходил ПРИВАТНЫЙ КЛЮЧ СЕРВЕРА (traffic, панель, MCP-инструмент
# traffic_report на уровне read) и портил статистику.
check "amnezia_show: длинная строка интерфейса awg не считается пиром" \
    '[ "$(jq -r "[.[] | select((.allowed_ips | contains(\"/\")) | not)] | length" <<<"$out")" = 0 ]' "$out"
run users_quota_tick
check "tick 1: rc=0" '[ "$rc" = 0 ]' "$(errs)"
check "tick 1: anna rx=1000 tx=2000 (пир и пользователь), last_handshake" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.rx" "$USERS_FILE")" = 1000 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.tx" "$USERS_FILE")" = 2000 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.last_rx" "$USERS_FILE")" = 1000 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.last_handshake" "$USERS_FILE")" = 1788868800 ]'
check "tick 1: bob превысил 1 ГБ → enabled=false, пир убран из конфига" \
    '[ "$(jq -r ".users[]|select(.name==\"bob\")|.enabled" "$USERS_FILE")" = false ] && ! grep -qF "$pubb" "$SD2/awg0.conf"'
check "tick 1: у bob записана причина выключения disabled_reason=quota" \
    '[ "$(jq -r ".users[]|select(.name==\"bob\")|.disabled_reason" "$USERS_FILE")" = quota ]' \
    "$(jq -c '.users[]|select(.name=="bob")|{enabled, disabled_reason}' "$USERS_FILE")"
# Предупреждение ДО поломки: carol заведена на 1 день — срок кончается в ближайшие трое суток.
check "tick 1: carol (срок через сутки) → одно событие expiry_soon с датой и остатком секунд; bob (выключен) без предупреждений" \
    '[ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -s "[.[]|select(.event==\"expiry_soon\")]|length")" = 1 ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"expiry_soon\")|.user")" = carol ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"expiry_soon\")|.expires")" = "$(jq -r ".users[]|select(.name==\"carol\")|.expires" "$USERS_FILE")" ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"expiry_soon\")|.seconds_left")" -gt 0 ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -s "[.[]|select(.user==\"bob\" and .event!=\"quota_exceeded\")]|length")" = 0 ] \
     && [ "$(jq -r ".users[]|select(.name==\"carol\")|.notified.expiry_soon" "$USERS_FILE")" = "$(jq -r ".users[]|select(.name==\"carol\")|.expires" "$USERS_FILE")" ]' \
    "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)"
nfile=$(grep -l '"quota_exceeded"' "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | head -n 1)
nnotify1=$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
check "очередь уведомлений: файл с событием quota_exceeded для bob, одна строка JSON" \
    '[ -n "$nfile" ] && [ "$(wc -l <"$nfile" | tr -d " ")" = 1 ] && [ "$(jq -r .event "$nfile")" = quota_exceeded ] && [ "$(jq -r .user "$nfile")" = bob ] && [ "$(jq -r .used_bytes "$nfile")" = 1181116006 ]' "$(cat "$nfile" 2>/dev/null)"
check "очередь: права 700 на каталог, 600 на файл" '[ "$(perm "$GOAMNEZIA_NOTIFY_DIR")" = 700 ] && [ "$(perm "$nfile")" = 600 ]'
check "tick 1: anna включена (лимита нет)" '[ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = true ]'
# Второй тик: показание anna уменьшилось (рестарт контейнера) → дельта = новое показание; bob исчез из вывода.
printf 'x\ty\t40000\toff\n%s\t(none)\t198.51.100.7:51820\t10.8.2.1/32\t1788869000\t100\t2500\toff\n' "$puba" >"$TMP/awgshow2.txt"
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/awgshow2.txt"
run users_quota_tick
check "tick 2: rx 1000+100=1100 (счётчик сбросился), tx 2000+500=2500, last_rx=100" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.rx" "$USERS_FILE")" = 1100 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.tx" "$USERS_FILE")" = 2500 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.last_rx" "$USERS_FILE")" = 100 ]' "$(errs)"
check "tick 2: bob остаётся выключенным, второго уведомления нет (и expiry_soon для carol не повторяется)" \
    '[ "$(jq -r ".users[]|select(.name==\"bob\")|.enabled" "$USERS_FILE")" = false ] && [ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = "$nnotify1" ]' \
    "было $nnotify1, стало $(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l)"
# Срок: carol просрочена (дату подкладываем прямо в users.json — только для теста).
jq '.users |= map(if .name == "carol" then .expires = "2020-01-01T00:00:00Z" else . end)' "$USERS_FILE" >"$TMP/u.json" && mv "$TMP/u.json" "$USERS_FILE"
run users_quota_tick
check "tick 3: carol просрочена → выключена, событие expired, причина disabled_reason=expiry" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.enabled" "$USERS_FILE")" = false ] && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"expired\")|.user")" = carol ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.disabled_reason" "$USERS_FILE")" = expiry ]' "$(errs)"
pubc2=$(jq -r '.users[]|select(.name=="carol")|.peers.awg2.pubkey' "$USERS_FILE")
pubc3=$(jq -r '.users[]|select(.name=="carol")|.peers.awg3.pubkey' "$USERS_FILE")
check "tick 3: пиры carol убраны из конфигов awg2 и awg3" \
    '! grep -qF "$pubc2" "$SD2/awg0.conf" && ! grep -qF "$pubc3" "$STATE_DIR/amnezia-awg3/awg0.conf"'
# SR-16: пир снят и возвращён (выкл/вкл) — его счётчик в контейнере начался с нуля, и показание после возврата
# (5000) больше прежнего (100). Раньше из него вычиталось прежнее, и 100 байт трафика терялись.
anna_rx0=$(jq -r '.users[]|select(.name=="anna")|.traffic.rx' "$USERS_FILE")
run users_disable anna
run users_enable anna
printf 'x\ty\t40000\toff\n%s\t(none)\t198.51.100.7:51820\t10.8.2.1/32\t1788869100\t5000\t2500\toff\n' "$puba" >"$TMP/awgshow3.txt"
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/awgshow3.txt"
run users_quota_tick
check "SR-16 выкл/вкл, затем показание 5000 больше прежних 100: засчитано целиком (rx +5000), last_rx=5000" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.rx" "$USERS_FILE")" = "$((anna_rx0 + 5000))" ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.last_rx" "$USERS_FILE")" = 5000 ]' \
    "было $anna_rx0, стало $(jq -c '.users[]|select(.name=="anna")|{rx: .traffic.rx, last: .peers.awg2.last_rx}' "$USERS_FILE")"
run users_enable anna
check "SR-16 включение уже включённой не обнуляет показание: пир и так в конфиге, его трафик не засчитается дважды" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.last_rx" "$USERS_FILE")" = 5000 ]' "$(jq -c '.users[]|select(.name=="anna")|.peers.awg2' "$USERS_FILE" | sed 's/"privkey":"[^"]*"//; s/"psk":"[^"]*"//')"
unset GOAMNEZIA_AWG_SHOW_FIXTURE
# Продление, которое правда включает: выключенный таймером за лимит включается сам, как только
# новый лимит выше израсходованного, — тем же путём, что user-enable (пир снова в конфиге).
run users_set_quota bob 5
check "users_set_quota bob 5: лимит выше израсходованного → bob снова включён, пир вернулся в конфиг, причина снята, re_enabled=true" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.quota_bytes" "$USERS_FILE")" = 5368709120 ] \
     && [ "$(jq -r ".users[]|select(.name==\"bob\")|.enabled" "$USERS_FILE")" = true ] \
     && [ "$(jq -r ".users[]|select(.name==\"bob\")|.peers.awg2.active" "$USERS_FILE")" = true ] \
     && grep -qF "$pubb" "$SD2/awg0.conf" \
     && [ "$(jq -r ".users[]|select(.name==\"bob\")|.disabled_reason // \"-\"" "$USERS_FILE")" = "-" ] \
     && [ "$(jq -r .re_enabled <<<"$out")" = true ] && grep -qF "$(tf users_reenabled bob)" "$ERR"' "out=$out $(errs)"
run users_set_expiry carol 0
check "users_set_expiry carol 0: бессрочно → carol снова включена, пиры в awg2 и awg3 вернулись, re_enabled=true" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.expires" "$USERS_FILE")" = "" ] \
     && [ "$(jq -r ".users[]|select(.name==\"carol\")|.enabled" "$USERS_FILE")" = true ] \
     && grep -qF "$pubc2" "$SD2/awg0.conf" && grep -qF "$pubc3" "$STATE_DIR/amnezia-awg3/awg0.conf" \
     && [ "$(jq -r .re_enabled <<<"$out")" = true ] && [ "$(jq -r .enabled <<<"$out")" = true ]' "out=$out $(errs)"
check "аудит: включение после снятия причины записано как user-enable" \
    'grep -q "user-enable.*carol reason=expiry_lifted" "$GOAMNEZIA_AUDIT_LOG" && grep -q "user-enable.*bob reason=quota_lifted" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(tail -n 5 "$GOAMNEZIA_AUDIT_LOG" 2>/dev/null)"

# §1d.2 №8: включение того, кто выбрал лимит, живёт до ближайшего тика (5 мин).
# Раньше это был чистый успех без единого слова, и человек считал, что включил.
# Лимит bob сейчас 5 ГБ, а натикало больше гигабайта — вернём 1 ГБ и включим.
run users_set_quota bob 1
run users_enable bob
check "включение при исчерпанном лимите предупреждает, что откатится тиком" \
    '[ "$rc" = 0 ] && grep -qF "$(tf users_enable_quota_spent bob)" "$ERR"' "$(errs)"
run users_set_quota bob 0
run users_enable bob
check "без лимита включение проходит молча" \
    '[ "$rc" = 0 ] && ! grep -qF "$(tf users_enable_quota_spent bob)" "$ERR"' "$(errs)"
# Срок в прошлом пишем прямо в файл: users_set_expiry считает дни ВПЕРЁД, а
# нужен именно уже истёкший — то состояние, в котором включение и откатывается.
jq -c '.users |= map(if .name == "carol" then .expires = "2020-01-01T00:00:00Z" else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_enable carol
check "включение просроченного предупреждает отдельной строкой" \
    '[ "$rc" = 0 ] && grep -qF "$(tf users_enable_expired carol)" "$ERR"' "$(errs)"
# Возвращаем стенд ровно в то состояние, в котором он был до этих проверок:
# bob с лимитом 5 ГБ и выключен, carol бессрочна и выключена. Иначе следующие
# разделы считают пиров и свободные адреса по другому расположению.
run users_set_expiry carol 0
run users_set_quota bob 5
run users_disable bob
run users_disable carol

# ── Продление, которое правда включает: кого НЕ включаем сами ─────────────────
section "снятие причины выключения: ручное и неизвестное не включаются, порядок снятия неважен"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
check "users_disable ставит причину manual" \
    '[ "$(jq -r ".users[]|select(.name==\"bob\")|.disabled_reason" "$USERS_FILE")" = manual ]' \
    "$(jq -c '.users[]|select(.name=="bob")|{enabled, disabled_reason}' "$USERS_FILE")"
run users_set_quota bob 7
check "выключенный руками при новом лимите остаётся выключенным, подсказка без сырого user-enable, re_enabled=false" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"bob\")|.enabled" "$USERS_FILE")" = false ] \
     && ! grep -qF "$pubb" "$SD2/awg0.conf" && [ "$(jq -r .re_enabled <<<"$out")" = false ] \
     && grep -qF "$(tf users_hint_disabled bob)" "$ERR" && ! grep -q "(user-enable)" "$ERR"' "out=$out $(errs)"
run users_set_quota bob 5
# users.json прежней версии: выключен таймером, но поля причины нет — причина неизвестна, не включаем.
cexp_saved=$(jq -c '.users[]|select(.name=="carol")|{expires, quota_bytes, traffic}' "$USERS_FILE")
jq -c '.users |= map(if .name == "carol" then del(.disabled_reason) | .expires = "2020-01-01T00:00:00Z" else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_set_expiry carol 30
check "без поля disabled_reason (старый users.json) продление НЕ включает" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.enabled" "$USERS_FILE")" = false ] \
     && ! grep -qF "$pubc2" "$SD2/awg0.conf" && [ "$(jq -r .re_enabled <<<"$out")" = false ]' "out=$out $(errs)"
# Выключен за срок, но и лимит исчерпан: продление не включает (лимит держит), причина переходит
# на лимит — и снятие лимита уже включает. Лимит и трафик пишем прямо в файл (только для теста).
jq -c '.users |= map(if .name == "carol" then .disabled_reason = "expiry" | .expires = "2020-01-01T00:00:00Z" | .quota_bytes = 1024 | .traffic.rx = 4096 else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_set_expiry carol 30
check "срок продлён, но лимит исчерпан → carol выключена, причина теперь quota, подсказка про лимит" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.enabled" "$USERS_FILE")" = false ] \
     && [ "$(jq -r ".users[]|select(.name==\"carol\")|.disabled_reason" "$USERS_FILE")" = quota ] \
     && grep -qF "$(tf users_hint_still_quota carol)" "$ERR" && [ "$(jq -r .re_enabled <<<"$out")" = false ]' "out=$out $(errs)"
run users_set_quota carol 0
check "…а снятие лимита включает carol: пиры снова в awg2 и awg3" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.enabled" "$USERS_FILE")" = true ] \
     && grep -qF "$pubc2" "$SD2/awg0.conf" && grep -qF "$pubc3" "$STATE_DIR/amnezia-awg3/awg0.conf" \
     && [ "$(jq -r .re_enabled <<<"$out")" = true ]' "out=$out $(errs)"
jq -c --argjson s "$cexp_saved" '.users |= map(if .name == "carol" then .traffic = $s.traffic else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"

# ── Предупредить до поломки: одно событие на значение, после продления — снова ──
section "предупреждения expiry_soon и quota_80: один раз, после продления — снова"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
nev() { cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -s --arg e "$1" --arg u "$2" '[.[]|select(.event==$e and .user==$u)]|length'; }
run users_set_expiry carol 2
run users_quota_tick
check "срок через 2 дня → одно expiry_soon для carol" '[ "$rc" = 0 ] && [ "$(nev expiry_soon carol)" = 1 ]' "$(errs)"
run users_quota_tick
check "следующий тик: второго expiry_soon нет" '[ "$rc" = 0 ] && [ "$(nev expiry_soon carol)" = 1 ]' "$(errs)"
run users_set_expiry carol 1
check "продление снимает флаг notified.expiry_soon" \
    '[ "$(jq -r ".users[]|select(.name==\"carol\")|.notified.expiry_soon // \"-\"" "$USERS_FILE")" = "-" ]'
run users_quota_tick
check "после продления (снова ≤ 3 дней) цикл предупреждает заново" '[ "$rc" = 0 ] && [ "$(nev expiry_soon carol)" = 2 ]' "$(errs)"
run users_set_expiry carol 30
run users_quota_tick
check "срок через 30 дней — не предупреждаем, флага нет" \
    '[ "$(nev expiry_soon carol)" = 2 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.notified // \"-\"" "$USERS_FILE")" = "-" ]'
atraf_saved=$(jq -c '.users[]|select(.name=="anna")|.traffic' "$USERS_FILE")
run users_set_quota anna 1
jq -c '.users |= map(if .name == "anna" then .traffic.rx = 943718400 | .traffic.tx = 0 else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_quota_tick
check "израсходовано 900 МБ из 1 ГБ (≥ 80 %) → одно quota_80 с used/quota, anna включена" \
    '[ "$rc" = 0 ] && [ "$(nev quota_80 anna)" = 1 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = true ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"quota_80\")|.used_bytes")" = 943718400 ] \
     && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event==\"quota_80\")|.quota_bytes")" = 1073741824 ]' \
    "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null) $(errs)"
run users_quota_tick
check "следующий тик: второго quota_80 нет" '[ "$(nev quota_80 anna)" = 1 ]'
run users_set_quota anna 2
run users_quota_tick
check "лимит подняли до 2 ГБ: 900 МБ — меньше 80 %, предупреждения нет, флаг снят" \
    '[ "$(nev quota_80 anna)" = 1 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.notified // \"-\"" "$USERS_FILE")" = "-" ]'
jq -c '.users |= map(if .name == "anna" then .traffic.rx = 1825361100 else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_quota_tick
check "…а дошли до 85 % нового лимита — предупреждаем снова" '[ "$(nev quota_80 anna)" = 2 ]'
# Стенд — как был: anna без лимита и с прежним трафиком, carol бессрочна и выключена.
run users_set_quota anna 0
jq -c --argjson t "$atraf_saved" '.users |= map(if .name == "anna" then .traffic = $t else . end)' \
    "$USERS_FILE" >"$USERS_FILE.tmp" && mv "$USERS_FILE.tmp" "$USERS_FILE"
run users_set_expiry carol 0
run users_disable carol
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null

# §1d.2 №12: переезд сервера. Поле server_ip писалось только в пустое, обновить
# его было некому, и после переезда каждый новый ключ нёс мёртвый адрес.
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
ip_was=$(ga_config_get .server_ip)
GOAMNEZIA_SERVER_IP="$ip_was" run amnezia_server_ip_check
check "адрес не менялся — ни события, ни записи" \
    '[ "$rc" = 0 ] && [ "$(jq -r .changed <<<"$out")" = false ] && [ -z "$(ls -A "$GOAMNEZIA_NOTIFY_DIR" 2>/dev/null)" ]' "out=$out $(errs)"
GOAMNEZIA_SERVER_IP=198.51.100.7 run amnezia_server_ip_check
check "адрес сменился — новый записан, сказано вслух, событие ушло боту" \
    '[ "$rc" = 0 ] && [ "$(jq -r .changed <<<"$out")" = true ] && [ "$(ga_config_get .server_ip)" = 198.51.100.7 ] \
      && grep -qF "$(tf amnezia_server_ip_changed "$ip_was" 198.51.100.7)" "$ERR" \
      && [ -n "$(grep -l server_ip_changed "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "out=$out $(errs)"
check "переезд записан и в config.json (L-06): server_ip_change {old, new, at}, закрепления не было — endpoint_host_action пуст" \
    '[ "$(ga_config_get .server_ip_change.old)" = "$ip_was" ] && [ "$(ga_config_get .server_ip_change.new)" = 198.51.100.7 ] \
      && [ -n "$(ga_config_get .server_ip_change.at)" ] && [ -z "$(ga_config_get .server_ip_change.endpoint_host_action)" ]' \
    "$(jq -c .server_ip_change "$CONFIG_FILE")"
GOAMNEZIA_SERVER_IP=0.0.0.0 run amnezia_server_ip_check
check "адрес определить не удалось — записанное не затирается" \
    '[ "$(ga_config_get .server_ip)" = 198.51.100.7 ]' "$(errs)"
# Endpoint, вписанный на IP сервера («IP сервера» в боте и панели), едет вместе с адресом; вписанный домен — нет.
ga_config_merge '{"endpoint_host":"198.51.100.7"}' >/dev/null 2>&1
GOAMNEZIA_SERVER_IP=198.51.100.9 run amnezia_server_ip_check
check "Endpoint был вписан на прежний IP — переехал на новый вместе с server_ip" \
    '[ "$(ga_config_get .server_ip)" = 198.51.100.9 ] && [ "$(ga_config_get .endpoint_host)" = 198.51.100.9 ]' "$(errs)"
check "server_ip_change — последний переезд: 198.51.100.7 → 198.51.100.9, endpoint_host прежний, action=moved" \
    '[ "$(ga_config_get .server_ip_change.old)" = 198.51.100.7 ] && [ "$(ga_config_get .server_ip_change.new)" = 198.51.100.9 ] \
      && [ "$(ga_config_get .server_ip_change.endpoint_host)" = 198.51.100.7 ] && [ "$(ga_config_get .server_ip_change.endpoint_host_action)" = moved ]' \
    "$(jq -c .server_ip_change "$CONFIG_FILE")"
ga_config_merge '{"endpoint_host":"vpn.example.com"}' >/dev/null 2>&1
GOAMNEZIA_SERVER_IP=198.51.100.7 run amnezia_server_ip_check
check "вписанный домен при смене адреса не трогается" \
    '[ "$(ga_config_get .server_ip)" = 198.51.100.7 ] && [ "$(ga_config_get .endpoint_host)" = vpn.example.com ]' "$(errs)"
ga_config_merge '{"endpoint_host":""}' >/dev/null 2>&1
ga_config_merge "$(jq -cn --arg ip "$ip_was" '{server_ip: $ip}')" >/dev/null 2>&1
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null

# ── 6. Статус, смена порта, перевыпуск, удаление ─────────────────────────────

# Регрессия стенда: amnezia_state_restore разговаривал с контейнером через
# docker cp и молча не находил цели, когда контейнера НЕТ — а это и есть
# главный сценарий восстановления («снесли контейнер, поднимаем из state/»).
# backup-restore на чистой машине отчитывался «профиль поднять не удалось».
run amnezia_state_restore awg2
check "amnezia_state_restore без контейнера: пересоздаёт его из state/ (pull, build, run)" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker run -d --log-driver none --restart always --privileged" "$ERR" && grep -q "DRY: docker build -t amnezia-awg2" "$ERR"' "$(errs | tail -5)"
check "восстановление вернуло в контейнер конфиг и ключи сервера" \
    'grep -q "DRY: docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -q "DRY: docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/wireguard_server_private_key.key" "$ERR"' "$(errs | tail -8)"
# ACC-4: копию конфига legacy-профиля (wg0) теперь приводят к awg0.conf восстановление, синхронизация и
# миграция. У родного профиля (awg0) приводить нечего — ни имени, ни байта не меняется.
cp -p "$SD2/awg0.conf" "$TMP/acc4-native.conf"
run ga_amn_state_canon "$SD2" awg0
check "ACC-4 родной профиль: приведение копии ничего не трогает, дубля wg0.conf не заводится" \
    '[ "$rc" = 0 ] && [ -z "$GA_AMN_STATE_CANON_DONE" ] && cmp -s "$SD2/awg0.conf" "$TMP/acc4-native.conf" && [ ! -e "$SD2/wg0.conf" ] && [ "$(perm "$SD2/awg0.conf")" = 600 ]' \
    "rc=$rc done=$GA_AMN_STATE_CANON_DONE $(ls "$SD2" | tr "\n" " ")"
run ga_amn_state_canon_all
check "ACC-4 миграция на сервере только с родными профилями ничего не называет и не трогает" \
    '[ "$rc" = 0 ] && [ -z "$out" ] && cmp -s "$SD2/awg0.conf" "$TMP/acc4-native.conf"' "rc=$rc out=$out"

section "статус / порт / regen / удаление"
run amnezia_profile_status awg2
check "profile-status awg2: JSON, state=missing (dry), peers = активные пиры, порт 40000" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r .state <<<"$out")" = missing ] && [ "$(jq -r .peers <<<"$out")" = 1 ] && [ "$(jq -r .port <<<"$out")" = 40000 ] && [ "$(jq -r .image_tag <<<"$out")" != "" ]' "$out $(errs)"
run amnezia_profile_status awg3
check "profile-status awg3: line 3.1, enabled=true" '[ "$(jq -r .line <<<"$out")" = 3.1 ] && [ "$(jq -r .enabled <<<"$out")" = true ]' "$out"
run amnezia_profile_set_port awg2 40010
check "profile-port awg2 → 40010: rc=0, config и ListenPort обновлены, ключ сервера прежний" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.port)" = 40010 ] && grep -q "^ListenPort = 40010$" "$SD2/awg0.conf" && grep -qF "$(cat "$SD2/wireguard_server_private_key.key")" "$SD2/awg0.conf"' "$(errs)"
check "profile-port: контейнер пересоздан из state (DRY rm/run/restore в stderr)" 'grep -q "DRY: docker stop amnezia-awg2" "$ERR" && grep -q "DRY: docker run .* -p 40010:40010/udp" "$ERR" && grep -q "docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/wireguard_server_private_key.key" "$ERR"' "$(grep DRY "$ERR" | head -8)"
run export_conf anna awg2
check "export_conf после смены порта: Endpoint :40010, тот же ключ пользователя" 'grep -q ":40010$" <<<"$out" && grep -qF "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.privkey" "$USERS_FILE")" <<<"$out"'
run amnezia_profile_set_port awg2 40010
check "тот же порт повторно → invalid_port (rc=2)" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_port ]' "rc=$rc code=$GA_ERR_CODE"
# LC-09: «040010» — тот же порт 40010, и в сообщении порт без ведущего нуля
run amnezia_profile_set_port awg2 040010
check "LC-09 порт с ведущим нулём читается десятично: «040010» = 40010 → invalid_port, в сообщении 40010" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_port ] && [[ "$GA_ERR_MSG" == *40010* ]] && [[ "$GA_ERR_MSG" != *040010* ]]' "rc=$rc msg=$GA_ERR_MSG"
run amnezia_profile_set_port awg2 40001
check "порт другого профиля → port_busy" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_busy ]' "rc=$rc code=$GA_ERR_CODE"
oldpub=$(jq -r '.users[]|select(.name=="anna")|.peers.awg3.pubkey' "$USERS_FILE")
run amnezia_profile_params awg3
oldparams="$out"
run amnezia_profile_regen awg3 quic
check "profile-regen awg3 quic: rc=0, params изменились, пресет quic, пиры перевыпущены (IP прежний)" \
    '[ "$rc" = 0 ] && [ "$(amnezia_profile_params awg3 2>/dev/null)" != "$oldparams" ] && [ "$(ga_config_get .profiles.awg3.preset)" = quic ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg3.pubkey" "$USERS_FILE")" != "$oldpub" ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg3.ip" "$USERS_FILE")" = 10.8.3.1 ]' "$(errs)"
check "regen: новый pubkey в конфиге и clientsTable awg3, DRY docker restart" 'np=$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg3.pubkey" "$USERS_FILE"); grep -qF "$np" "$STATE_DIR/amnezia-awg3/awg0.conf" && grep -qF "$np" "$STATE_DIR/amnezia-awg3/clientsTable" && grep -q "DRY: docker restart amnezia-awg3" "$ERR"'
run amnezia_scan_containers
check "scan_containers в dry-run → []" '[ "$rc" = 0 ] && [ "$(jq -c . <<<"$out")" = "[]" ]' "$out"
run amnezia_profile_restart awg1
check "profile-restart awg1: rc=0, DRY docker restart" '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg1" "$ERR"' "$(errs)"
run amnezia_profile_remove awg3
check "profile-remove awg3: rc=0, профиль удалён из config, пиры awg3 удалены у всех, state/ убран" \
    '[ "$rc" = 0 ] && [ -z "$(ga_config_get .profiles.awg3.installed_at)" ] && [ "$(jq -r "[.users[]|.peers.awg3]|map(select(.!=null))|length" "$USERS_FILE")" = 0 ] && [ ! -d "$STATE_DIR/amnezia-awg3" ]' "$(errs)"
check "остальные профили целы" '[ "$(ga_profiles_installed | sort | tr "\n" " ")" = "awg1 awg2 " ] && [ -f "$SD2/awg0.conf" ]'
run amnezia_profile_status awg3
check "статус удалённого профиля → profile_not_installed (rc=3)" '[ "$rc" = 3 ] && [ "$GA_ERR_CODE" = profile_not_installed ]' "rc=$rc code=$GA_ERR_CODE"
check "проектный каталог не тронут: state/ и сборка только в \$TMP" '[ ! -d "$ROOT/state" ] && [ ! -d "$ROOT/lib/upstream/awg/amnezia-awg2" ]'

# ── 6a. Настройки профиля как в приложении: параметры, подсеть, MTU, Endpoint ─
# Сквозной прогон через мост — tests/test_actions_dryrun.sh; здесь то, до чего
# мост не дотягивается: разбор CIDR, аллокатор в /23, перенумерация напрямую,
# список параметров по базовой линейке.
section "настройки профиля (set_param / subnet / mtu / endpoint)"
run ga_cidr_parse 10.8.2.0/24
check "ga_cidr_parse 10.8.2.0/24 → база, 256 адресов, префикс 24" '[ "$rc" = 0 ] && [ "$out" = "168296960 256 24" ]' "out=$out"
run ga_cidr_parse 10.8.2.0/22
check "ga_cidr_parse: невыровненный адрес → код 2" '[ "$rc" = 2 ]' "rc=$rc"
run ga_cidr_parse 10.8.2.0
check "ga_cidr_parse: без маски → код 1" '[ "$rc" = 1 ]' "rc=$rc"
run amnezia_profile_param_list awg2
check "amnezia_profile_param_list awg2: JSON, S3 есть (2.0), HeaderProtectionKey нет, S1 — server, Jc — client" \
    '[ "$rc" = 0 ] && is_json "$out" && jq -e ".[]|select(.key==\"S3\")" <<<"$out" >/dev/null && ! jq -e ".[]|select(.key==\"HeaderProtectionKey\")" <<<"$out" >/dev/null && [ "$(jq -r ".[]|select(.key==\"S1\")|.scope" <<<"$out")" = server ] && [ "$(jq -r ".[]|select(.key==\"Jc\")|.scope" <<<"$out")" = client ]' "$out"
run amnezia_profile_param_list awg1
check "param_list awg1: базовая линейка 1.5 — I1 есть, S3 нет" \
    'jq -e ".[]|select(.key==\"I1\")" <<<"$out" >/dev/null && ! jq -e ".[]|select(.key==\"S3\")" <<<"$out" >/dev/null' "$out"
run amnezia_profile_set_param awg1 I1 "<b 0x0102><r 2>"
check "awg1 (1.0, router) + I1 → линейка становится 1.5, клиентский, reissue_required=false" \
    '[ "$rc" = 0 ] && [ "$(jq -r .line <<<"$out")" = 1.5 ] && [ "$(jq -r .reissue_required <<<"$out")" = false ] && [ "$(ga_config_get .profiles.awg1.line)" = 1.5 ]' "$out $(errs)"
run amnezia_profile_set_param awg1 I1 ""
check "awg1: убрали I1 → снова 1.0" '[ "$rc" = 0 ] && [ "$(jq -r .line <<<"$out")" = 1.0 ]' "$out"
run amnezia_profile_set_param awg1 S3 20
check "awg1: S3 запрещён линейкой → params_invalid (rc=2), stdout пуст" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = params_invalid ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_set_param awg2 Jc "6; rm -rf /"
check "значение вне белого списка символов → invalid_param_value" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_param_value ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_set_param awg2 Nope 1
check "неизвестный ключ → invalid_param" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_param ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_set_mtu awg2 1100
check "MTU 1100 → invalid_mtu" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mtu ]' "rc=$rc code=$GA_ERR_CODE"
# Договор MTU один (ga_valid_mtu), и спрашивать его обязаны ОБА пути. До
# починки profile-set-param держал свой диапазон 576–65535 из awgkeys.py:
# `profile-mtu 9000` отвергался, а `profile-set-param MTU 9000` проходил и
# уезжал в клиентские конфиги.
for _mtu in 1100 9000 576 15000 12a; do
    run amnezia_profile_set_mtu awg2 "$_mtu"
    _rc_mtu=$rc; _code_mtu=$GA_ERR_CODE
    run amnezia_profile_set_param awg2 MTU "$_mtu"
    check "MTU $_mtu отвергается одинаково: set_mtu и set_param → invalid_mtu (rc 2)" \
        '[ "$_rc_mtu" = 2 ] && [ "$_code_mtu" = invalid_mtu ] && [ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_mtu ]' \
        "set_mtu: rc=$_rc_mtu code=$_code_mtu · set_param: rc=$rc code=$GA_ERR_CODE"
done
run amnezia_profile_set_param awg2 MTU 1376
check "MTU 1376 проходит и через set_param" '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.params.MTU)" = 1376 ]' "rc=$rc $(errs)"
run amnezia_profile_set_param awg2 MTU ""
check "пустой MTU через set_param — это «убрать параметр», а не отказ" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
run amnezia_profile_set_mtu awg2 1420
# С 29.09.2026 MTU профиля — и в конфиге сервера (решение владельца): строка MTU = 1420 в копии awg0.conf. Контейнера
# на стенде нет (missing) — пишется только копия в state/, контейнер поднимут из неё.
check "MTU 1420 → config.json params.MTU строкой и строка MTU = 1420 в awg0.conf (контейнера нет — только state/)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.params.MTU)" = 1420 ] && grep -q "^MTU = 1420$" "$SD2/awg0.conf" && ! grep -q "docker cp" "$ERR"' "$(errs)"
run amnezia_profile_set_subnet awg2 10.8.1.0/24
check "подсеть, занятая awg1 → subnet_overlap" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = subnet_overlap ]' "rc=$rc code=$GA_ERR_CODE"
# SR-37: сеть моста Docker (docker0, 172.17.0.0/16) — тоже занята: пиры 172.17.0.x рядом с eth0 контейнера.
run ga_amn_subnet_check awg2 172.17.0.0/24
check "SR-37 подсеть на сети моста Docker (docker0) → subnet_overlap с названием docker0" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = subnet_overlap ] && grep -q docker0 <<<"$GA_ERR_MSG"' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
run amnezia_profile_set_subnet awg2 10.20.0.0/23
check "profile-subnet awg2 → 10.20.0.0/23: rc=0, пиры .1/.2/.3 по порядку, ключи прежние" \
    '[ "$rc" = 0 ] && [ "$(jq -r .renumbered <<<"$out")" = 3 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.ip" "$USERS_FILE")" = 10.20.0.1 ] && [ "$(jq -r ".users[]|select(.name==\"carol\")|.peers.awg2.ip" "$USERS_FILE")" = 10.20.0.3 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.peers.awg2.pubkey" "$USERS_FILE")" = "$puba" ]' "$out $(errs)"
check "awg0.conf: Address 10.20.0.0/23, AllowedIPs 10.20.0.1/32, DRY пересоздание контейнера" \
    'grep -q "^Address = 10.20.0.0/23$" "$SD2/awg0.conf" && grep -q "^AllowedIPs = 10.20.0.1/32$" "$SD2/awg0.conf" && grep -q "DRY: docker stop amnezia-awg2" "$ERR" && grep -q "DRY: docker run .* -p 40010:40010/udp" "$ERR"' "$(grep DRY "$ERR" | head -5)"
# SR-34: контейнер снят, а на новой подсети не поднялся — пользователи и подсеть возвращаются, контейнер
# поднимается на прежней (копия состояния к этому моменту ещё прежняя), повтор той же подсети не запрещён.
sr34_users=$(jq -cS . "$USERS_FILE")
_sr34_run=$(declare -f docker_run_awg)
eval "$(printf '%s\n' "$_sr34_run" | sed '1s/^docker_run_awg/_sr34_real_run/')"
sr34_n=0
docker_run_awg() { sr34_n=$((sr34_n + 1)); if [ "$sr34_n" = 1 ]; then ga_err run_failed "stend: docker run отказал"; return 1; fi; _sr34_real_run "$@"; }
run amnezia_profile_set_subnet awg2 10.30.0.0/24
check "SR-34 сбой после docker rm: отказ subnet_change_failed, подсеть прежняя, users.json байт-в-байт, awg0.conf на прежней" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = subnet_change_failed ] && [ "$(ga_config_get .profiles.awg2.subnet)" = 10.20.0.0/23 ] && [ "$(jq -cS . "$USERS_FILE")" = "$sr34_users" ] && grep -q "^Address = 10.20.0.0/23$" "$SD2/awg0.conf" && [ "$GA_ERR_MSG" = "$(tf amnezia_err_subnet_kept 10.30.0.0/24 awg2 10.20.0.0/23)" ]' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG subnet=$(ga_config_get .profiles.awg2.subnet)"
eval "$_sr34_run"
run amnezia_profile_set_subnet awg2 10.30.0.0/24
check "SR-34 повтор той же подсети после сбоя проходит (а не «уже использует»)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.subnet)" = 10.30.0.0/24 ] && grep -q "^Address = 10.30.0.0/24$" "$SD2/awg0.conf"' "rc=$rc $(errs | tail -3)"
run amnezia_profile_set_subnet awg2 10.20.0.0/23
check "SR-34 обратно на 10.20.0.0/23 для следующих проверок" '[ "$rc" = 0 ]' "rc=$rc $(errs | tail -3)"
# Аллокатор в /23: занимаем .4… до конца первой «четвертушки» не нужно — достаточно
# убедиться, что адреса выходят за .254 и последний адрес сети не раздаётся.
jq '.users |= map(if .name == "bob" then .peers.awg2.ip = "10.20.0.254" else . end)' "$USERS_FILE" >"$TMP/u.json" && mv "$TMP/u.json" "$USERS_FILE"
run users_alloc_ip awg2
check "users_alloc_ip в /23 → 10.20.0.2 (освобождённый после переноса bob на .254)" '[ "$out" = 10.20.0.2 ]' "out=$out"
run users_renumber_profile awg2 10.20.0.0/23
check "users_renumber_profile напрямую: JSON [{name, old_ip, ip}], bob снова .2" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r ".[]|select(.name==\"bob\")|.old_ip" <<<"$out")" = 10.20.0.254 ] && [ "$(jq -r ".[]|select(.name==\"bob\")|.ip" <<<"$out")" = 10.20.0.2 ]' "$out"
run users_renumber_profile awg2 10.20.0.0/33
check "users_renumber_profile с кривой маской → invalid_subnet" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_subnet ]' "rc=$rc code=$GA_ERR_CODE"
run ga_set_endpoint_host vpn.example.com
check "ga_set_endpoint_host vpn.example.com: rc=0, config.json endpoint_host" '[ "$rc" = 0 ] && [ "$(ga_config_get .endpoint_host)" = vpn.example.com ]' "$(errs)"
run export_conf anna awg2
check "export_conf: Endpoint vpn.example.com:40010, Address 10.20.0.1/32, MTU 1420" \
    'grep -q "^Endpoint = vpn.example.com:40010$" <<<"$out" && grep -q "^Address = 10.20.0.1/32$" <<<"$out" && grep -q "^MTU = 1420$" <<<"$out"' "$out"
run ga_set_endpoint_host "evil host"
check "ga_set_endpoint_host с пробелом → invalid_host" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_host ]' "rc=$rc code=$GA_ERR_CODE"
# Проверка адреса по ЗНАЧЕНИЮ октетов, а не по виду. До починки регулярка
# `^([0-9]{1,3}\.){3}[0-9]{1,3}$` пропускала 999.999.999.999 в Endpoint и в DNS
# клиентских конфигов — туннель вставал, а причину искали в сервере.
for _bad in 999.999.999.999 1.2.3.4.5 256.1.1.1 1.2.3 010.1.1.1 1.2.3.4. 0.0.0.0; do
    run ga_set_endpoint_host "$_bad"
    check "endpoint-set $_bad → invalid_host" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_host ]' "rc=$rc code=$GA_ERR_CODE"
done
run ga_set_endpoint_host 203.0.113.10
check "endpoint-set: законный IPv4 проходит" '[ "$rc" = 0 ] && [ "$(ga_config_get .endpoint_host)" = 203.0.113.10 ]' "$(errs)"
run ga_set_endpoint_host node-1.vpn.example.com
check "endpoint-set: законное имя узла проходит (Endpoint бывает доменом)" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .endpoint_host)" = node-1.vpn.example.com ]' "$(errs)"
for _bad in 999.999.999.999 1.2.3.4.5 8.8.8.8.8; do
    run ga_set_client_dns "$_bad"
    check "dns-set $_bad → invalid_dns" '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_dns ]' "rc=$rc code=$GA_ERR_CODE"
done
run ga_set_client_dns 9.9.9.9 999.999.999.999
check "dns-set: битый ВТОРОЙ адрес тоже отвергается, первый не записан" \
    '[ "$rc" = 2 ] && [ "$GA_ERR_CODE" = invalid_dns ] && [ "$(ga_config_get .client_dns | jq -r ".[0]")" != 9.9.9.9 ]' "rc=$rc code=$GA_ERR_CODE"
run ga_set_client_dns 9.9.9.9 149.112.112.112
check "dns-set: законная пара записана в config.json" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .client_dns | jq -c .)" = "[\"9.9.9.9\",\"149.112.112.112\"]" ]' "$(errs)"
run export_conf anna awg2
check "export_conf берёт новые DNS" 'grep -q "^DNS = 9.9.9.9, 149.112.112.112" <<<"$out"' "$out"
# Мусор, попавший в config.json мимо проверки (старая версия, правка руками),
# не должен уехать в ключ: сборка .conf выбрасывает его и предупреждает.
ga_config_merge '{"client_dns":["999.999.999.999","1.1.1.1"]}' >/dev/null 2>&1
run export_conf anna awg2
check "export_conf: негодный DNS из config.json в ключ не попадает" \
    '[ "$rc" = 0 ] && ! grep -q "999.999.999.999" <<<"$out" && grep -q "^DNS = 1.1.1.1$" <<<"$out"' "$out"
ga_set_client_dns 1.1.1.1 1.0.0.1 >/dev/null 2>&1
run ga_set_endpoint_host ""
check "ga_set_endpoint_host '' → пусто (IP сервера)" '[ "$rc" = 0 ] && [ "$(ga_config_get .endpoint_host)" = "" ]' "$(errs)"
run amnezia_profile_install awg3 40001
check "повторная установка awg3 после смены подсетей: подсеть по умолчанию 10.8.3.0/24 свободна" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg3.subnet)" = 10.8.3.0/24 ]' "$(errs)"
run amnezia_profile_set_subnet awg3 10.8.1.0/24
check "awg3 → подсеть awg1 → subnet_overlap" '[ "$GA_ERR_CODE" = subnet_overlap ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_remove awg1
run amnezia_profile_set_subnet awg3 10.8.1.0/24
check "после удаления awg1 его подсеть свободна для awg3" '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg3.subnet)" = 10.8.1.0/24 ]' "$(errs)"
run amnezia_profile_install awg1 40002 router
check "установка awg1 при занятой 10.8.1.0/24 → первая свободная 10.8.2.0/24" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg1.subnet)" = 10.8.2.0/24 ]' "$(errs)"

# ── 7. Словари ───────────────────────────────────────────────────────────────
section "i18n: паритет RU/EN и ключи t/tf"
for m in docker amnezia users export; do
    ru=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/ru.d/$m.sh" | sort)
    en=$(grep -o '^I18N\[[a-z0-9_]*\]' "$ROOT/lib/lang/en.d/$m.sh" | sort)
    check "ru.d/$m.sh ↔ en.d/$m.sh: одинаковый набор ключей ($(wc -l <<<"$ru" | tr -d ' '))" '[ "$ru" = "$en" ] && [ -n "$ru" ]' "$(diff <(echo "$ru") <(echo "$en") | head -5)"
done
used=$(grep -ohE '\$\((t|tf) [a-z0-9_]+' "$ROOT"/lib/{docker,amnezia,users,export}.sh | awk '{print $2}' | sort -u)
missing=""
# Искать надо И в ядре словаря (lib/lang/<язык>.sh), И во фрагментах: общие для
# всего продукта ключи (err_no_jq, err_config_unreadable) живут в ядре, и
# проверка только по .d/ объявляла бы их пропавшими — то есть ловила бы не
# опечатку, а место хранения.
for k in $used; do
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/ru.sh "$ROOT"/lib/lang/ru.d/*.sh || missing+=" ru:$k"
    grep -q "^I18N\[$k\]=" "$ROOT"/lib/lang/en.sh "$ROOT"/lib/lang/en.d/*.sh || missing+=" en:$k"
done
check "все $(wc -w <<<"$used" | tr -d ' ') ключей t/tf из модулей есть в RU и EN" '[ -z "$missing" ]' "$missing"
check "в модулях нет вызовов t/tf с переменной вместо ключа" '! grep -nE "\\\$\((t|tf) \"?\\\$" "$ROOT"/lib/{docker,amnezia,users,export}.sh'

# ── 8. Битые файлы состояния и отказ применения ──────────────────────────────
# Находки охотника за багами 10.09.2026: на неразбираемом users.json продукт
# отвечал «успешно» пустым списком, а следующая мутация записывала его поверх
# файла — приватные ключи всех пользователей исчезали. Отдельно: отказ
# применения к контейнеру выдавался за успех (человека «удалили», а он ходит),
# а неудачный user-add оставлял пользователя с ключом, о котором сервер не знает.
section "битые config.json / users.json и отказ применения"

# Битый config.json: до починки обе записывающие функции подставляли пустой
# СКЕЛЕТ вместо неразбираемого файла, и первая же настройка затирала им режим,
# домен, порты панели, параметры и ключи всех профилей — молча.
config_backup=$(cat "$CONFIG_FILE")
config_broken='{"schema":1,"mode":"pro"'
printf '%s' "$config_broken" >"$CONFIG_FILE"          # обрыв записи

run ga_config_read
check "битый config.json: ga_config_read отказывает и ничего не печатает" \
    '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=$out"
run ga_config_set .mode '"lite"'
check "битый config.json: ga_config_set отказывает кодом config_corrupt" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = config_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
check "отказ громкий: сказано, где копия и как восстановиться" \
    'grep -q "config.json.broken-" "$ERR" && grep -qF "$BACKUP_DIR" "$ERR"' "$(errs)"
check "копия битого файла спасена в бэкапах с правами 600" \
    '[ -n "$(ls "$BACKUP_DIR"/config.json.broken-* 2>/dev/null)" ] && [ "$(perm "$(ls "$BACKUP_DIR"/config.json.broken-* | head -1)")" = 600 ]' \
    "$(ls -l "$BACKUP_DIR" 2>/dev/null | head -5)"
check "копия равна исходному битому файлу — он не стал могилой" \
    '[ "$(cat "$(ls "$BACKUP_DIR"/config.json.broken-* | head -1)")" = "$config_broken" ]'
run ga_config_merge '{"mode":"lite"}'
check "битый config.json: ga_config_merge отказывает кодом config_corrupt" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = config_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
check "битый config.json остался нетронутым (не затёрт скелетом)" \
    '[ "$(cat "$CONFIG_FILE")" = "$config_broken" ]' "$(head -c 120 "$CONFIG_FILE")"
# Копий не должно становиться по одной на каждую попытку записи: имя берёт
# время правки файла, поэтому вторая попытка попадает в ту же копию.
check "повторный отказ не плодит копии (одна на версию файла)" \
    '[ "$(ls "$BACKUP_DIR"/config.json.broken-* 2>/dev/null | wc -l | tr -d " ")" = 1 ]' "$(ls "$BACKUP_DIR" 2>/dev/null)"
# Настройки, которые терялись раньше: убеждаемся, что через битый файл не
# прошла ни одна мутация уровнем выше.
run amnezia_profile_set_mtu awg2 1300
check "битый config.json: profile-mtu не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc $(errs)"
run ga_set_endpoint_host vpn.example.com
check "битый config.json: endpoint-set не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc $(errs)"

printf '%s' "$config_backup" >"$CONFIG_FILE"
run ga_config_read
check "целый config.json: ga_config_read снова отдаёт JSON" '[ "$rc" = 0 ] && is_json "$out"' "rc=$rc"
run ga_config_merge '{"schema":1}'
check "целый config.json: запись снова работает, профили на месте" \
    '[ "$rc" = 0 ] && [ -n "$(ga_config_get .profiles.awg2.server_pubkey)" ]' "$(errs)"
# Файла нет вовсе — это НЕ поломка: скелет здесь законен, иначе первая
# установка на чистом сервере не смогла бы записать ни одной настройки.
mv "$CONFIG_FILE" "$TMP/config.away"
run ga_config_read
check "config.json ещё нет: читается скелет, а не отказ" \
    '[ "$rc" = 0 ] && [ "$(jq -r .mode <<<"$out")" = lite ]' "rc=$rc out=$(head -c 120 <<<"$out")"
mv "$TMP/config.away" "$CONFIG_FILE"

users_backup=$(cat "$USERS_FILE")
printf '{"schema":1,"users":[{"name":"anna"' >"$USERS_FILE"   # обрыв записи

run users_list
check "битый users.json: users_list отказывает кодом users_corrupt" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = users_corrupt ]' "rc=$rc code=$GA_ERR_CODE"

run users_add zoe "" "awg2" 0 0
check "битый users.json: users_add не выполняется" '[ "$rc" != 0 ]' "rc=$rc $(errs)"
check "битый users.json остался нетронутым (не затёрт пустым списком)" \
    '[ "$(cat "$USERS_FILE")" = "{\"schema\":1,\"users\":[{\"name\":\"anna\"" ]' "$(head -c 80 "$USERS_FILE")"

printf '%s' "$users_backup" >"$USERS_FILE"
run users_list
check "целый users.json: users_list снова работает" '[ "$rc" = 0 ] && is_json "$out"' "rc=$rc"

# Отказ применения: убираем копию состояния профиля — записать конфиг некуда
if [ -d "$GOAMNEZIA_DIR/state" ]; then
    run users_add rollo "" "awg2" 0 0
    if [ "$rc" = 0 ]; then
        state_dir=$(ga_config_get .profiles.awg2.container)
        mv "$GOAMNEZIA_DIR/state/$state_dir" "$GOAMNEZIA_DIR/state/.$state_dir.hidden" 2>/dev/null
        run users_add ghost "" "awg2" 0 0
        check "user-add при отказе применения: действие не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc $(errs)"
        check "user-add при отказе применения: запись откачена, ghost не остался" \
            '! ga_users_json | jq -e ".users[]? | select(.name==\"ghost\")" >/dev/null 2>&1' "$(ga_users_json | jq -c '[.users[].name]')"
        run users_del rollo
        check "user-del при отказе применения: не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc $(errs)"
        mv "$GOAMNEZIA_DIR/state/.$state_dir.hidden" "$GOAMNEZIA_DIR/state/$state_dir" 2>/dev/null
        run users_del rollo
    else
        skip "отказ применения: подготовка не удалась ($rc)"
    fi
else
    skip "отказ применения: каталога state/ нет"
fi

# ── 8a. Путь ключей: битый users.json, живучесть записи, откаты (охота 11.09.2026)
# Двенадцать находок §1b, и у большинства цена — приватные ключи людей:
#   1b.1  удаление профиля на битом users.json писало поверх пустой скелет и
#         отвечало «успешно»: исчезали ключи ВСЕХ пользователей;
#   1b.2  тот же проглоченный код возврата в write_server_conf — конфиг с
#         пирами превращался в конфиг с нулём и уезжал в контейнер;
#   1b.3  спасательной копии битого users.json не делалось нигде;
#   1b.4  запись без fsync: выключение питания рождало битый файл, то есть
#         вход в первые три;
#   1b.5  тик лимитов не смотрел код применения: «выключен» в журналах,
#         работающий пир в контейнере;
#   1b.7  подмена каталога состояния отчитывалась успехом после половины работы;
#   1b.8  откат создания пользователя не переприменял успевшие профили;
#   1b.9  глушилка предупреждения о чужих пирах не сбрасывалась на отказе;
#   1b.10 три записи ключей сервера при установке — ни одной проверки кода;
#   1b.11 временные файлы с приватными ключами ложились на диск.
# Проверки считают ИМЕННО КЛЮЧИ: сколько приватных ключей было и сколько
# осталось, сколько пиров в серверном конфиге. «Не упало» тут не значит ничего.
section "путь ключей: битый users.json, fsync, откаты"

# Ключи считаем по СЫРОМУ тексту файла: битый JSON не разберёт ни один jq, а
# ключи в нём никуда не делись — именно это и надо доказать.
nkeys()  { grep -o '"privkey"' "$USERS_FILE" 2>/dev/null | wc -l | tr -d ' '; }
npeers() { grep -c '^\[Peer\]' "$SD2/awg0.conf" 2>/dev/null | tr -d ' '; }
SD1="$STATE_DIR/amnezia-awg1"

keys_before=$(nkeys)
peers_before=$(npeers)
tbl_before=$(jq length "$SD2/clientsTable")
users_good=$(cat "$USERS_FILE")
anna_key=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.privkey' "$USERS_FILE")
check "исходно: приватных ключей $keys_before, пиров в конфиге awg2 $peers_before" \
    '[ "$keys_before" -ge 3 ] && [ "$peers_before" -ge 1 ] && [ -n "$anna_key" ]' \
    "keys=$keys_before peers=$peers_before"

# Обрыв записи на живом файле: в начале лишний байт, содержимое цело. Ровно
# так и выглядит битый users.json — ключи в нём есть, а разобрать его нельзя.
# И ровно поэтому «пусто» и «сломано» обязаны различаться.
nres_before=$(ls "$BACKUP_DIR"/users.json.broken-* 2>/dev/null | wc -l | tr -d ' ')
printf '{%s' "$users_good" >"$USERS_FILE"
broken_sum=$(cksum <"$USERS_FILE")
check "подготовка: файл битый, но все $keys_before ключей в нём на месте" \
    '! jq -e . "$USERS_FILE" >/dev/null 2>&1 && [ "$(nkeys)" = "$keys_before" ] && grep -qF "$anna_key" "$USERS_FILE"'

run amnezia_profile_remove awg1
check "1b.1 битый users.json: profile-remove отказывает кодом users_corrupt" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = users_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
check "1b.1 ключи всех пользователей на месте: было $keys_before, осталось $(nkeys)" \
    '[ "$(nkeys)" = "$keys_before" ] && grep -qF "$anna_key" "$USERS_FILE"' "стало $(nkeys)"
check "1b.1 users.json не переписан (байт в байт тот же битый файл)" \
    '[ "$(cksum <"$USERS_FILE")" = "$broken_sum" ]'
check "1b.1 отказ случился ДО первой разрушительной операции: профиль awg1 цел" \
    '[ -f "$SD1/wireguard_server_private_key.key" ] && [ -n "$(ga_config_get .profiles.awg1.installed_at)" ]' \
    "$(ls "$SD1" 2>/dev/null | tr '\n' ' ')"

run amnezia_write_server_conf awg2
check "1b.2 битый users.json: сборка серверного конфига отказывает" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = users_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
check "1b.2 конфиг awg2 цел: пиров по-прежнему $peers_before, а не ноль" \
    '[ "$(npeers)" = "$peers_before" ]' "стало $(npeers)"
run users_apply_profile awg2
check "1b.2 то же на пути любой мутации (users_apply_profile): отказ, конфиг цел" \
    '[ "$rc" != 0 ] && [ "$(npeers)" = "$peers_before" ]' "rc=$rc peers=$(npeers)"
run users_disable anna
check "1b.2 бытовое действие (user-disable) на битом файле не выдаётся за успех" \
    '[ "$rc" != 0 ] && [ "$(npeers)" = "$peers_before" ]' "rc=$rc $(errs)"
run amnezia_clientstable_write awg2
check "1b.2 таблица клиентов не затирается пустой (было $tbl_before имён)" \
    '[ "$rc" != 0 ] && [ "$(jq length "$SD2/clientsTable")" = "$tbl_before" ]' "rc=$rc"
run amnezia_profile_status awg2
check "1b.2 состояние профиля не рапортует «пиров ноль», а отказывает" \
    '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=$out"

rescue=$(ls -t "$BACKUP_DIR"/users.json.broken-* 2>/dev/null | head -1)
check "1b.3 битый users.json спасён копией 0600 в бэкапах" \
    '[ -n "$rescue" ] && [ "$(perm "$rescue")" = 600 ]' "$(ls "$BACKUP_DIR" 2>/dev/null | tr '\n' ' ')"
check "1b.3 в копии все $keys_before приватных ключей — файл не стал могилой" \
    '[ "$(grep -o "\"privkey\"" "$rescue" | wc -l | tr -d " ")" = "$keys_before" ] && grep -qF "$anna_key" "$rescue"'
check "1b.3 повторные отказы не плодят копии (одна на версию файла)" \
    '[ "$(( $(ls "$BACKUP_DIR"/users.json.broken-* 2>/dev/null | wc -l | tr -d " ") - nres_before ))" = 1 ]' \
    "было $nres_before стало $(ls "$BACKUP_DIR"/users.json.broken-* 2>/dev/null | wc -l | tr -d " ")"
check "1b.3 сказано, где копия и что делать" \
    'grep -q "users.json.broken-" "$ERR" || grep -q "users.json.broken-" "$GOAMNEZIA_LOG_FILE"' "$(errs)"

printf '%s' "$users_good" >"$USERS_FILE"
run users_list
check "целый users.json: список снова читается, ключей столько же" \
    '[ "$rc" = 0 ] && [ "$(nkeys)" = "$keys_before" ]' "rc=$rc"

# Одно правило на весь модуль: подстановка $(ga_users_read) съедает код отказа,
# поэтому её не должно остаться ни одной.
check "правило чтения одно: подстановки \$(ga_users_read) в ядре не осталось" \
    '! grep -q "[^_a-z]\$(ga_users_read" "$ROOT/lib/amnezia.sh" "$ROOT/lib/users.sh"' \
    "$(grep -n '\$(ga_users_read' "$ROOT/lib/amnezia.sh" "$ROOT/lib/users.sh" | head -3)"

# ── 1b.4: запись обязана переживать выключение питания ────────────────────────
run _ga_commit_file "$TMP/nosuch.tmp" "$TMP/nosuch.dst"
check "1b.4 _ga_commit_file на несуществующем файле отказывает" '[ "$rc" != 0 ]' "rc=$rc"
printf 'ключ' >"$TMP/commit.src"
run _ga_commit_file "$TMP/commit.src" "$TMP/commit.dst"
check "1b.4 _ga_commit_file кладёт файл на место и убирает временный" \
    '[ "$rc" = 0 ] && [ "$(cat "$TMP/commit.dst")" = "ключ" ] && [ ! -f "$TMP/commit.src" ]' "rc=$rc"
_orig_commit=$(declare -f _ga_commit_file)
_ga_commit_file() { return 1; }
sum_before=$(cksum <"$USERS_FILE")
run users_rename anna "Аня"
check "1b.4 users.json пишется ЧЕРЕЗ fsync-подтверждение: не легло — отказ" '[ "$rc" != 0 ]' "rc=$rc $(errs)"
check "1b.4 не подтвердилось — файл не подменён, ключи прежние" \
    '[ "$(cksum <"$USERS_FILE")" = "$sum_before" ] && [ "$(nkeys)" = "$keys_before" ]'
eval "$_orig_commit"
run users_rename anna "Анна Петрова"
check "1b.4 после починки запись снова проходит" '[ "$rc" = 0 ]' "$(errs)"

# ── 1b.8: откат создания пользователя переприменяет успевшие профили ──────────
peers1_before=$(grep -c '^\[Peer\]' "$SD1/awg0.conf")
run users_alloc_ip awg1
free_ip="$out"
mv "$SD2" "$STATE_DIR/.awg2.hidden"
run users_add ghosty Ghosty awg1,awg2
ghost_pub=$(jq -r '.users[]?|select(.name=="ghosty")|.peers.awg1.pubkey // empty' "$USERS_FILE")
check "1b.8 отказ применения второго профиля: user-add не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc"
check "1b.8 откат: записи в users.json не осталось" \
    '! jq -e ".users[]|select(.name==\"ghosty\")" "$USERS_FILE" >/dev/null 2>&1' "$(jq -c '[.users[].name]' "$USERS_FILE")"
check "1b.8 откат: в конфиге awg1 нет пира-фантома (пиров по-прежнему $peers1_before)" \
    '[ "$(grep -c "^\[Peer\]" "$SD1/awg0.conf")" = "$peers1_before" ] && [ -z "$ghost_pub" ]' \
    "peers=$(grep -c '^\[Peer\]' "$SD1/awg0.conf")"
mv "$STATE_DIR/.awg2.hidden" "$SD2"
run users_alloc_ip awg1
check "1b.8 откат: адрес фантома снова свободен ($free_ip), а не занят навсегда" \
    '[ "$out" = "$free_ip" ]' "было $free_ip стало $out"

# ── 1b.9: глушилка предупреждения о чужих пирах — локальная ───────────────────
run users_add dropme Dropme awg2
GA_EXPECT_DROP="СТОРОЖ"
mv "$SD2" "$STATE_DIR/.awg2.hidden"
run users_del dropme
check "1b.9 user-del при отказе применения не выдаётся за успех" '[ "$rc" != 0 ]' "rc=$rc"
check "1b.9 GA_EXPECT_DROP не протекла наружу: у вызывающего прежнее значение" \
    '[ "$GA_EXPECT_DROP" = "СТОРОЖ" ]' "GA_EXPECT_DROP=$GA_EXPECT_DROP"
GA_EXPECT_DROP=""
mv "$STATE_DIR/.awg2.hidden" "$SD2"
run users_apply_profile awg2
expect_peers=$(jq '[.users[]|select(.enabled==true)|.peers.awg2|select(.!=null)|select(.active==true)]|length' "$USERS_FILE")
check "1b.9 после починки конфиг снова сходится с users.json ($expect_peers)" \
    '[ "$rc" = 0 ] && [ "$(npeers)" = "$expect_peers" ]' "peers=$(npeers) $(errs)"

# ── 1b.5: тик лимитов не отчитывается успехом, не применив выключение ─────────
run users_set_quota anna 1
puba3=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.pubkey' "$USERS_FILE")
printf 'x\ty\t40010\toff\n%s\t(none)\t198.51.100.7:51820\t10.8.2.1/32\t1788869100\t2000000000\t0\toff\n' "$puba3" >"$TMP/awgshow4.txt"
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/awgshow4.txt"
nnotify=$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
mv "$SD2" "$STATE_DIR/.awg2.hidden"
run users_quota_tick
check "1b.5 тик при отказе применения отдаёт код, а не успех" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = quota_apply_failed ]' "rc=$rc code=$GA_ERR_CODE"
check "1b.5 события владельцу не ушло — «доступ закрыт» не объявляем заранее" \
    '[ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | wc -l | tr -d " ")" = "$nnotify" ]'
# Отката больше нет (SR-07, 25.09.2026): откат возвращал включёнными ВСЕХ нарушителей, и один
# сломанный профиль отменял лимиты и сроки у всех. Выключение остаётся, помечается
# disable_pending, и следующий тик доводит его и шлёт событие.
check "1b.5 выключение не откатывается: anna выключена и помечена disable_pending" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = false ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.disable_pending" "$USERS_FILE")" = quota_exceeded ]' \
    "$(jq -c '.users[]|select(.name=="anna")|{enabled, disable_pending}' "$USERS_FILE")"
check "1b.5 трафик при этом посчитан и не потерян" \
    '[ "$(jq -r ".users[]|select(.name==\"anna\")|.traffic.rx" "$USERS_FILE")" -ge 2000000000 ]' \
    "$(jq -r '.users[]|select(.name=="anna")|.traffic.rx' "$USERS_FILE")"
mv "$STATE_DIR/.awg2.hidden" "$SD2"
run users_quota_tick
check "1b.5 следующий тик после починки: anna выключена, событие ушло, пир убран, пометка снята" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[]|select(.name==\"anna\")|.enabled" "$USERS_FILE")" = false ] \
     && [ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" -gt "$nnotify" ] \
     && ! grep -qF "$puba3" "$SD2/awg0.conf" \
     && [ "$(jq -r ".users[]|select(.name==\"anna\")|.disable_pending // \"-\"" "$USERS_FILE")" = "-" ]' "rc=$rc $(errs)"
unset GOAMNEZIA_AWG_SHOW_FIXTURE
run users_set_quota anna 0
run users_enable anna

# ── 1b.7: подмена каталога состояния — код возврата на каждом перемещении ─────
# Обрываем второе перемещение: копия, собранная из контейнера, пропадает прямо
# перед подменой. Для этого одного шага выходим из dry-run и подменяем разговор
# с докером заглушками.
_orig_cpfrom=$(declare -f docker_cp_from)
_orig_cstate=$(declare -f docker_container_state)
_orig_gadocker=$(declare -f ga_docker)
docker_container_state() { echo running; }
ga_docker() { return 1; }
docker_cp_from() { rm -rf "${3%/}"; return 0; }
GOAMNEZIA_DRY_RUN=0
run amnezia_state_sync awg2
GOAMNEZIA_DRY_RUN=1
eval "$_orig_cpfrom"; eval "$_orig_cstate"; eval "$_orig_gadocker"
check "1b.7 обрыв на середине подмены каталога состояния — это отказ" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = state_failed ]' "rc=$rc code=$GA_ERR_CODE"
check "1b.7 ключ сервера остался на месте, а не под именем с суффиксом" \
    '[ -f "$SD2/wireguard_server_private_key.key" ] && [ ! -d "$SD2.old" ]' \
    "$(ls "$STATE_DIR" | tr '\n' ' ')"

# ── Круг по бэкапам, D5: удаление профиля при недоступном демоне Docker ─────────
# Контейнер не снялся бы, а ключи и запись удалились: контейнер с --restart always продолжал бы обслуживать
# пиров профиля, о котором продукт уже не знает.
_d5_src=$(declare -f docker_container_state)
docker_container_state() { echo "$GA_DOCKER_STATE_UNREACHABLE"; }
run amnezia_profile_remove awg3
eval "$_d5_src"
check "D5 демон Docker не отвечает — удаление профиля отказывает, ключ сервера и запись на месте" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ -f "$STATE_DIR/amnezia-awg3/$GA_AWG_SERVER_PRIV" ] && [ -n "$(ga_config_get .profiles.awg3.port)" ]' "rc=$rc code=$GA_ERR_CODE"

# ── SR-33: перевыпуск и правка параметра при лежащем демоне — отказ ДО первой записи ─────────
# Раньше config.json и ключи всех пользователей менялись, и только потом проверялся демон: контейнер
# оставался со старыми, выдача — с новыми, а мост отвечал безликим «не выполнено».
_sr33_src=$(declare -f docker_container_state)
docker_container_state() { echo "$GA_DOCKER_STATE_UNREACHABLE"; }
sr33_users=$(cat "$USERS_FILE"); sr33_cfg=$(jq -cS . "$CONFIG_FILE")
run amnezia_profile_regen awg2 quic
check "SR-33 regen при лежащем демоне: docker_unreachable, users.json и config.json байт-в-байт" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ "$(cat "$USERS_FILE")" = "$sr33_users" ] && [ "$(jq -cS . "$CONFIG_FILE")" = "$sr33_cfg" ]' "rc=$rc code=$GA_ERR_CODE"
run amnezia_profile_set_param awg2 Jc 7
check "SR-33 set-param при лежащем демоне: docker_unreachable, config.json не тронут" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ "$(jq -cS . "$CONFIG_FILE")" = "$sr33_cfg" ]' "rc=$rc code=$GA_ERR_CODE"
eval "$_sr33_src"

# ── SR-35: новый порт не поднялся после docker rm — профиль возвращается на прежний ──────────
# Контейнер уже снят, а на новом порту не встал (порт занят вне учёта, сбой Docker): профиль лежал
# у всех, а сторож его не поднимал. Теперь — обратно на прежний порт из копии состояния.
sr35_old=$(ga_config_get .profiles.awg2.port)
_sr35_run=$(declare -f docker_run_awg)
eval "$(printf '%s\n' "$_sr35_run" | sed '1s/^docker_run_awg/_sr35_real_run/')"
docker_run_awg() { if [ "$2" = 40555 ]; then ga_err run_failed "stend: порт 40555 не поднялся"; return 1; fi; _sr35_real_run "$@"; }
run amnezia_profile_set_port awg2 40555
check "SR-35 новый порт не поднялся: отказ port_change_failed, порт в конфиге прежний, контейнер поднят на прежнем" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_change_failed ] && [ "$(ga_config_get .profiles.awg2.port)" = "$sr35_old" ] && grep -q "DRY: docker run .* -p ${sr35_old}:${sr35_old}/udp" "$ERR" && [ "$GA_ERR_MSG" = "$(tf amnezia_err_port_kept 40555 awg2 "$sr35_old")" ]' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
docker_run_awg() { ga_err run_failed "stend: Docker отказал"; return 1; }
run amnezia_profile_set_port awg2 40555
check "SR-35 не поднялся и прежний: отказ называет, что профиль лежит, и как его поднять" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_change_failed ] && [ "$GA_ERR_MSG" = "$(tf amnezia_err_port_down awg2 amnezia-awg2)" ] && [ "$(ga_config_get .profiles.awg2.port)" = "$sr35_old" ]' "rc=$rc msg=$GA_ERR_MSG"
eval "$_sr35_run"

# ── 1b.10: три записи ключей сервера при установке — каждая проверяется ───────
# Ломаем ВТОРУЮ: на месте файла открытого ключа оказывается каталог.
awg3_port=$(ga_config_get .profiles.awg3.port)
run amnezia_profile_remove awg3
mkdir -p "$STATE_DIR/amnezia-awg3/$GA_AWG_SERVER_PUB"
run amnezia_profile_install awg3 "$awg3_port" default
check "1b.10 незаписанный открытый ключ сервера — отказ, а не тишина" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = state_failed ]' "rc=$rc code=$GA_ERR_CODE"
check "1b.10 профиль с незаписанными ключами не объявлен установленным" \
    '[ -z "$(ga_config_get .profiles.awg3.installed_at)" ]' "$(ga_config_get .profiles.awg3.installed_at)"
rm -rf "$STATE_DIR/amnezia-awg3"
run amnezia_profile_install awg3 "$awg3_port" default
check "1b.10 после починки профиль ставится как обычно (готовим следующие проверки)" \
    '[ "$rc" = 0 ] && [ -n "$(ga_config_get .profiles.awg3.installed_at)" ]' "rc=$rc $(errs)"

# Шестой круг: 3.1 нельзя правкой параметров превратить в 2.0. По одному снимаем все 3.x-поля — последнее
# снять нельзя (params_invalid), линейка остаётся 3.1; затем профиль пересобирается в своей линейке. С 1.0.13 новый
# 3.1 несёт и ContentPaddingAddition 10-100 (PARAM-04) — 3.x-полей девять.
fam_cleared=0; fam_refused=""
for fam_key in HeaderProtectionKey ContentPaddingAddition RekeyAfterTime RekeyTimeout RejectAfterTime KeepaliveTimeout MaxHandshakeAttempts RandomTrailers DisableCookies; do
    run amnezia_profile_set_param awg3 "$fam_key" ""
    if [ "$rc" = 0 ]; then fam_cleared=$((fam_cleared + 1)); else fam_refused="$fam_key:$GA_ERR_CODE"; break; fi
done
check "3.1 → 2.0 правкой параметров: восемь 3.x-полей сняты, девятое — отказ params_invalid, линейка 3.1" \
    '[ "$fam_cleared" = 8 ] && [ "${fam_refused%%:*}" = DisableCookies ] && [ "${fam_refused#*:}" = params_invalid ] && [ "$(ga_profile_line awg3)" = 3.1 ]' \
    "cleared=$fam_cleared refused=$fam_refused line=$(ga_profile_line awg3) $(errs | tail -2)"
run amnezia_profile_regen awg3 default
check "после проверки awg3 пересобран в своей линейке 3.1 с полным набором 3.x-полей" \
    '[ "$rc" = 0 ] && [ "$(ga_profile_line awg3)" = 3.1 ] && [ -n "$(ga_config_get .profiles.awg3.params.HeaderProtectionKey)" ]' "rc=$rc $(errs | tail -2)"

# ── 1b.11: временные файлы с секретами — в память и с затиранием ──────────────
# Выбор по умолчанию — без каталога стенда из шапки (подоболочка): /dev/shm на Linux, каталог замков на macOS.
check "1b.11 каталог временных секретов — не общий каталог временных файлов" \
    '[ "$(unset GOAMNEZIA_SECRET_TMPDIR; ga_secret_tmpdir)" != "${TMPDIR:-/tmp}" ]' "$(unset GOAMNEZIA_SECRET_TMPDIR; ga_secret_tmpdir)"
mkdir -p "$TMP/mem"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/mem"
check "1b.11 каталог задаётся явно (на сервере это tmpfs: /dev/shm или /run)" \
    '[ "$(ga_secret_tmpdir)" = "$TMP/mem" ]' "$(ga_secret_tmpdir)"
sec=$(ga_mktemp_secret probe)
printf 'SECRETSECRET' >"$sec"
ln "$sec" "$TMP/probe.link"
ga_wipe_file "$sec"
check "1b.11 ga_wipe_file затирает содержимое нулями, а не только снимает имя" \
    '[ ! -e "$sec" ] && [ "$(wc -c <"$TMP/probe.link" | tr -d " ")" = 12 ] && ! grep -q SECRET "$TMP/probe.link"' \
    "$(head -c 20 "$TMP/probe.link" | od -c | head -1)"
rm -f "$TMP/probe.link"
GA_SEEN_WIPE=""
_orig_wipe=$(declare -f ga_wipe_file)
ga_wipe_file() { GA_SEEN_WIPE="$1"; rm -f "$@"; }
ga_capture probe_conf export_conf anna awg2
eval "$_orig_wipe"
check "1b.11 захват вывода с приватным ключом идёт через память и затирается" \
    '[ -n "$GA_SEEN_WIPE" ] && [[ "$GA_SEEN_WIPE" == "$TMP/mem"/* ]] && grep -q PrivateKey <<<"$probe_conf"' \
    "seen=$GA_SEEN_WIPE"
probe_conf=""
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"   # каталог стенда (L1-19), не /dev/shm машины
check "1b.11 после захвата в каталоге секретов не осталось файлов" \
    '[ -z "$(ls -A "$TMP/mem" 2>/dev/null)" ]' "$(ls -A "$TMP/mem" 2>/dev/null | tr '\n' ' ')"

# ── 9. Занятость порта, адрес в ключе, временный файл (охота 11.09.2026) ──────
# Три находки одного дня, каждая — «продукт молча делает не то»:
#   1a.3  проверка занятости порта читала пятое поле вывода ss. При
#         однопротокольном вызове это PEER-адрес («0.0.0.0:*»), цифрой он не
#         кончается — условие не срабатывало никогда, и порт, занятый соседом
#         по серверу, считался свободным;
#   1a.4  определение адреса сервера при неудаче печатает 0.0.0.0 и возвращает
#         единицу. Код выбрасывался, строка непустая — человек получал ключ,
#         который никогда не соединится;
#   1a.14 при неудаче mktemp переменная становилась /dev/null, и его сносили.
section "занятость порта, адрес в ключе, временный файл"

PF="$ROOT/tests/fixtures/preflight"
if [ -f "$ROOT/lib/preflight.sh" ] && [ -d "$PF" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/preflight.sh"
    export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_nginx.txt" GOAMNEZIA_SS_FIXTURE_UDP="$PF/ss_udp_xray.txt"
    run docker_port_free 443 tcp
    check "443/tcp занят чужим nginx → docker_port_free говорит «занят»" '[ "$rc" = 1 ]' "rc=$rc $(errs)"
    run docker_port_free 443 udp
    check "443/udp занят чужим xray → «занят» (до починки оба считались свободными)" '[ "$rc" = 1 ]' "rc=$rc"
    run docker_port_free 22 tcp
    check "22/tcp занят sshd → «занят»" '[ "$rc" = 1 ]' "rc=$rc"
    run docker_port_free 1984 tcp
    check "1984/tcp на 127.0.0.1 → «занят» (локальный слушатель тоже держит порт)" '[ "$rc" = 1 ]' "rc=$rc"
    run docker_port_free 40000 tcp
    check "40000/tcp свободен" '[ "$rc" = 0 ]' "rc=$rc"
    # Peer-адрес в каждой строке фикстуры — «0.0.0.0:*»; читая его, старый разбор
    # не видел ни одного порта. Убеждаемся, что читаем именно локальный адрес.
    export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_telemt.txt"
    run docker_port_free 9091 tcp
    check "9091/tcp у telemt на 127.0.0.1 → «занят»; звёздочка в адресе не мешает" '[ "$rc" = 1 ]' "rc=$rc"
    export GOAMNEZIA_SS_FIXTURE_TCP="$PF/ss_tcp_free.txt" GOAMNEZIA_SS_FIXTURE_UDP="$PF/ss_udp_free.txt"
    run docker_port_free 443 tcp
    check "порт свободен, когда его действительно никто не слушает" '[ "$rc" = 0 ]' "rc=$rc"
    run docker_port_free 70000 udp
    check "порт вне диапазона → rc=2 (неверный ввод)" '[ "$rc" = 2 ]' "rc=$rc"
    unset GOAMNEZIA_SS_FIXTURE_TCP GOAMNEZIA_SS_FIXTURE_UDP
else
    skipped "занятость порта" "нет lib/preflight.sh или фикстур"
fi

# 1a.4. Адрес сервера. 0.0.0.0 — это «не определили», откуда бы он ни пришёл.
ip_backup=$(ga_config_get .server_ip)
run ga_config_set .server_ip '"0.0.0.0"'
run ga_export_endpoint_host
check "server_ip=0.0.0.0: ga_export_endpoint_host отказывает и ничего не печатает" \
    '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=[$out]"
run export_conf anna awg2
check "ключ с адресом 0.0.0.0 не выдаётся: код endpoint_unknown, stdout пуст" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = endpoint_unknown ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=$(head -c 80 <<<"$out")"
run export_bundle anna awg2 conf
check "export_bundle с адресом 0.0.0.0 тоже отказывает" '[ "$rc" != 0 ]' "rc=$rc"
run ga_config_set .server_ip '"999.999.999.999"'
run export_conf anna awg2
check "мусор в server_ip в ключ не уезжает (проверка по значению, §0.8)" '[ "$rc" != 0 ]' "rc=$rc"
# Контрактное поведение get_server_ip: печатает 0.0.0.0 и возвращает 1. Раньше
# код выбрасывался, и эта строка проходила стража выдачи как обычный адрес.
_orig_get_server_ip=$(declare -f get_server_ip)
run ga_config_set .server_ip '""'
get_server_ip() { echo "0.0.0.0"; return 1; }
run ga_export_endpoint_host
check "get_server_ip вернул единицу: код не выброшен, адреса нет" '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=[$out]"
eval "$_orig_get_server_ip"
run ga_config_set .server_ip "\"$ip_backup\""
run export_conf anna awg2
check "адрес вернули — ключ снова выдаётся" '[ "$rc" = 0 ] && grep -q "^Endpoint = $ip_backup:" <<<"$out"' "rc=$rc $(errs)"

# 1a.14. При неудаче mktemp err становился /dev/null — и его удаляли. На
# devtmpfs он сам не вернётся, а без него от root встаёт половина системы.
if [ -f "$ROOT/lib/deps.sh" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/deps.sh"
    mkdir -p "$TMP/fakebin"
    printf '#!/bin/bash\nprintf "apt-get rc=100 %%s\\n" "$*" >>"$GA_DEPS_CALLS"\nexit 100\n' >"$TMP/fakebin/apt-get"
    printf '#!/bin/bash\necho 0\n' >"$TMP/fakebin/id"
    printf '#!/bin/bash\nprintf "mktemp rc=1\\n" >>"$GA_DEPS_CALLS"\nexit 1\n' >"$TMP/fakebin/mktemp"
    printf '#!/bin/bash\nprintf "%%s\\n" "$*" >>"$GA_RM_LOG"\nexec /bin/rm "$@"\n' >"$TMP/fakebin/rm"
    chmod +x "$TMP/fakebin"/*
    export GA_RM_LOG="$TMP/rm.log"; : >"$GA_RM_LOG"
    export GA_DEPS_CALLS="$TMP/deps.calls"; : >"$GA_DEPS_CALLS"
    # На подготовленном Linux все зависимости уже есть: до apt/mktemp дело не
    # доходило. Отсутствующий файл в стенде гарантирует ветку установки, сохраняя
    # настоящие deps_check и _deps_pkg_list; журнал доказывает оба отказа и retry.
    _deps_table_backup=("${DEPS_TABLE[@]}")
    DEPS_TABLE=("core|test-missing|file:$TMP/deps-missing|jq|crit|deps_why_jq")
    cat >"$TMP/deps.expected" <<'CALLS'
mktemp rc=1
apt-get rc=100 update -qq -o DPkg::Lock::Timeout=120
apt-get rc=100 install --no-remove -y -qq -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold jq
apt-get rc=100 update -qq -o DPkg::Lock::Timeout=120
apt-get rc=100 install --no-remove -y -qq -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold jq
CALLS
    _path_backup="$PATH"; PATH="$TMP/fakebin:$PATH"
    GOAMNEZIA_DRY_RUN=0 run deps_install_missing --yes all
    PATH="$_path_backup"
    check "apt не отработал, mktemp тоже: deps_install_missing честно отказывает" '[ "$rc" = 1 ]' "rc=$rc"
    check "mktemp отказал, apt update → install → update → install вызваны и отказали" \
        'cmp -s "$TMP/deps.expected" "$GA_DEPS_CALLS"' "$(cat "$GA_DEPS_CALLS")"
    check "/dev/null не удаляли ни разу" '! grep -q "/dev/null" "$GA_RM_LOG"' "$(cat "$GA_RM_LOG")"
    check "/dev/null на месте и это устройство" '[ -c /dev/null ]'

    # NC-03: вывод apt и dpkg (по-английски, с отчётом needrestart) — не на экран. Первая установка на свежем
    # сервере не удаётся, пока не обновлены списки, — это не тревога: строка повтора без ⚠. Удалась — ни
    # строчки apt; не удалась — хвост его вывода, чтобы было видно почему.
    rm -f "$TMP/fakebin/mktemp"
    : >"$GA_DEPS_CALLS"
    cat >"$TMP/fakebin/apt-get" <<'APT'
#!/bin/bash
printf 'apt-get %s\n' "$*" >>"$GA_DEPS_CALLS"
echo "Selecting previously unselected package libqrencode4:amd64."
echo "Running kernel seems to be up-to-date." >&2
case "$1" in
    update) : >"$GA_DEPS_CALLS.updated"; exit 0 ;;
    install) if [ -f "$GA_DEPS_CALLS.updated" ] && [ -z "${APT_FAIL:-}" ]; then : >"$GA_DEPS_FILE"; exit 0; fi
             echo "E: Unable to locate package jq" >&2; exit 100 ;;
esac
APT
    chmod +x "$TMP/fakebin/apt-get"
    export GA_DEPS_FILE="$TMP/deps-missing"; rm -f "$GA_DEPS_FILE"
    PATH="$TMP/fakebin:$PATH"
    GOAMNEZIA_DRY_RUN=0 run deps_install_missing --yes all
    PATH="$_path_backup"
    check "NC-03: вторая попытка после обновления списков ставит пакет (rc 0, apt вызван трижды)" \
        '[ "$rc" = 0 ] && [ "$(grep -c "^apt-get" "$GA_DEPS_CALLS")" = 2 ]' "rc=$rc $(cat "$GA_DEPS_CALLS") $(errs)"
    check "NC-03: ни строчки apt, dpkg и needrestart на экране" \
        '! grep -qE "Selecting previously|Running kernel|Unable to locate" "$ERR" && ! grep -qE "Selecting previously|Running kernel" <<<"$out"' "$(errs)"
    check "NC-03: повтор — спокойной строкой, без ⚠ и без «не вышло с ходу»" \
        'grep -q "Обновляю список пакетов" "$ERR" && ! grep -q "⚠" "$ERR" && ! grep -qi "с ходу" "$ERR"' "$(errs)"
    rm -f "$GA_DEPS_CALLS.updated" "$GA_DEPS_FILE"; : >"$GA_DEPS_CALLS"
    PATH="$TMP/fakebin:$PATH"
    GOAMNEZIA_DRY_RUN=0 APT_FAIL=1 run deps_install_missing --yes all
    PATH="$_path_backup"
    check "NC-03: не удалось совсем — отказ и хвост вывода apt, чтобы было видно почему" \
        '[ "$rc" = 1 ] && grep -q "Unable to locate package jq" "$ERR"' "rc=$rc $(errs)"
    DEPS_TABLE=("${_deps_table_backup[@]}")
    unset GA_RM_LOG GA_DEPS_CALLS GA_DEPS_FILE _deps_table_backup
else
    skipped "временный файл deps" "нет lib/deps.sh"
fi

# ── 10. Резервные копии: битый источник и битый архив (охота 11.09.2026) ──────
# Две находки одной семьи с безвозвратной потерей ключей:
#   1a.2  копия сохраняла битый config.json как исправный, а в карточку архива
#         подставляла правдоподобные умолчания («режим lite»). Таймер ходит
#         ежесуточно при глубине 30 — за месяц незамеченной порчи ротация
#         выносила все здоровые архивы;
#   1a.1  восстановление клало такой архив поверх живой установки и отвечало
#         «успех»: конфиг не разбирается, приватные ключи всех пиров стёрты,
#         спасать нечего.
# Доказательство строится на настоящей установке этого прогона — с профилями,
# пользователями и их приватными ключами.
section "резервные копии: битый источник, битый архив, снимок перед записью"

if [ -f "$ROOT/lib/backup.sh" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/backup.sh"
    anna_priv=$(jq -r '.users[]|select(.name=="anna")|.peers.awg2.privkey' "$USERS_FILE")
    narch() { ls "$BACKUP_DIR"/*.tar.gz 2>/dev/null | wc -l | tr -d ' '; }
    check "готово к проверке: у anna есть приватный ключ awg2" '[ -n "$anna_priv" ] && [ "$anna_priv" != null ]'

    run create_backup ""
    good_arch="$out"
    check "здоровая копия снимается: rc=0, архив на месте, права 600" \
        '[ "$rc" = 0 ] && [ -f "$good_arch" ] && [ "$(perm "$good_arch")" = 600 ]' "rc=$rc $(errs)"
    mkdir -p "$TMP/unp" && tar xzf "$good_arch" -C "$TMP/unp" 2>/dev/null
    meta=$(cat "$TMP/unp"/*/metadata.json 2>/dev/null)
    check "карточка архива: настоящий режим и оба источника ok" \
        '[ "$(jq -r .mode <<<"$meta")" = lite ] && [ "$(jq -r .sources.config <<<"$meta")" = ok ] && [ "$(jq -r .sources.users <<<"$meta")" = ok ]' "$meta"
    check "в архиве лежат приватные ключи пиров — иначе спасать было бы нечего" \
        'grep -qF "$anna_priv" "$TMP/unp"/*/users.json'

    # ── 1a.2. Битый конфиг в момент снятия копии ──
    n_before=$(narch)
    cp "$CONFIG_FILE" "$TMP/cfg.keep"
    printf '{"schema":1,"mode":"pro"' >"$CONFIG_FILE"      # обрыв записи
    run create_backup ""
    check "битый config.json: копия не снимается, код backup_source_corrupt" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = backup_source_corrupt ] && [ -z "$out" ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
    check "отказ называет файл и спасённую копию" \
        'grep -qF "config.json" "$ERR" && grep -q "config.json.broken-" "$ERR"' "$(errs)"
    # Причина должна доехать до бота и панели человеческим текстом, а не
    # безликое «Действие не выполнено.»: мост читает GA_ERR_MSG.
    check "причина уходит наружу текстом, а не только кодом" \
        '[ -n "$GA_ERR_MSG" ] && grep -qF "config.json" <<<"$GA_ERR_MSG"' "msg=$GA_ERR_MSG"
    check "новых архивов не появилось — ротации нечего съедать" '[ "$(narch)" = "$n_before" ]' "было $n_before стало $(narch)"
    check "копия битого файла спасена в backups/" \
        '[ -n "$(ls "$BACKUP_DIR"/config.json.broken-* 2>/dev/null)" ]' "$(ls "$BACKUP_DIR")"
    # Снимок (режим восстановления) с битым источником сниматься обязан — иначе
    # спасать будет нечего, — но честно помечает, что внутри.
    run create_backup "" "$BACKUP_DIR" 1
    snap_arch="$out"
    check "снимок снимается и с поломанного состояния" '[ "$rc" = 0 ] && [ -f "$snap_arch" ]' "rc=$rc $(errs)"
    mkdir -p "$TMP/unp2" && tar xzf "$snap_arch" -C "$TMP/unp2" 2>/dev/null
    check "карточка снимка не притворяется исправной: sources.config=broken" \
        '[ "$(jq -r .sources.config "$TMP/unp2"/*/metadata.json)" = broken ]' "$(cat "$TMP/unp2"/*/metadata.json)"
    cp "$TMP/cfg.keep" "$CONFIG_FILE"
    rm -f "$snap_arch" "$snap_arch.sha256"
    # SR-25: снимку перед восстановлением — порог по размеру данных, а не общие 512 МБ плановых копий:
    # на почти полном диске (здесь «свободно 100 МБ») восстановление иначе отказывало ровно в той аварии,
    # из которой вытаскивает. Плановая копия при том же месте по-прежнему отказывает с low_disk.
    _sr25_disk=$(declare -f check_disk_space)
    check_disk_space() { [ "${1:-0}" -le 100 ]; }
    run create_backup "" "$BACKUP_DIR" 1
    check "SR-25 снимок при 100 МБ свободно снимается (порог — по размеру данных)" '[ "$rc" = 0 ] && [ -f "$out" ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
    [ -f "$out" ] && rm -f "$out" "$out.sha256"
    run create_backup ""
    check "SR-25 плановая копия при 100 МБ свободно — low_disk, как и была" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = low_disk ]' "rc=$rc code=$GA_ERR_CODE"
    eval "$_sr25_disk"
    # Второй источник проверяется так же: битый users.json — это все приватные
    # ключи пиров, и копия с ним не имеет смысла.
    cp "$USERS_FILE" "$TMP/users.keep"
    printf '{"schema":1,"users":[{"name":"anna"' >"$USERS_FILE"
    n_before=$(narch)
    run create_backup ""
    check "битый users.json: копия тоже не снимается" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = backup_source_corrupt ] && [ "$(narch)" = "$n_before" ]' \
        "rc=$rc code=$GA_LAST_ERROR_CODE"
    check "копия битого users.json спасена" \
        '[ -n "$(ls "$BACKUP_DIR"/users.json.broken-* 2>/dev/null)" ]' "$(ls "$BACKUP_DIR")"
    cp "$TMP/users.keep" "$USERS_FILE"

    # ── 1a.1. Битый архив поверх живой установки ──
    mkdir -p "$TMP/bad/goamnezia_backup_19700101_000000"
    printf '{"schema":1,"mode":"pro"' >"$TMP/bad/goamnezia_backup_19700101_000000/config.json"
    printf '{"schema":1,"users":[]}\n' >"$TMP/bad/goamnezia_backup_19700101_000000/users.json"
    printf '{"mode":"lite","goamnezia_version":"0.2.0"}\n' >"$TMP/bad/goamnezia_backup_19700101_000000/metadata.json"
    bad_arch="$BACKUP_DIR/goamnezia_backup_19700101_000000.tar.gz"
    tar czf "$bad_arch" -C "$TMP/bad" goamnezia_backup_19700101_000000
    n_before=$(narch)
    run restore_backup "$bad_arch" "" yes
    check "битый архив: восстановление отказывает кодом backup_corrupt" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = backup_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
    # Именно сообщение об архиве, а не любое упоминание config.json: до починки
    # в stderr стояло «config.json восстановлен», и слабая проверка прошла бы.
    check "отказ говорит, ЧТО именно в архиве не разбирается" \
        'grep -qF "$(tf backup_archive_corrupt config.json)" "$ERR"' "$(errs)"
    check "тот же текст уходит наружу для бота и панели" \
        '[ "$GA_ERR_MSG" = "$(tf backup_archive_corrupt config.json)" ]' "msg=$GA_ERR_MSG"
    check "живой users.json не тронут: приватный ключ anna на месте" \
        'grep -qF "$anna_priv" "$USERS_FILE"' "$(head -c 120 "$USERS_FILE")"
    check "живой config.json не тронут: профили и режим на месте" \
        '[ "$(jq -r ".profiles|keys|join(\",\")" "$CONFIG_FILE")" = "awg1,awg2,awg3" ] && [ "$(jq -r .mode "$CONFIG_FILE")" = lite ]'
    check "до первой записи не дошло — лишних снимков не наделали" '[ "$(narch)" = "$n_before" ]'

    # Архив без config.json — это не копия установки, а обрезок
    mkdir -p "$TMP/nocfg/goamnezia_backup_19700102_000000"
    printf '{"schema":1,"users":[]}\n' >"$TMP/nocfg/goamnezia_backup_19700102_000000/users.json"
    tar czf "$BACKUP_DIR/goamnezia_backup_19700102_000000.tar.gz" -C "$TMP/nocfg" goamnezia_backup_19700102_000000
    run restore_backup "$BACKUP_DIR/goamnezia_backup_19700102_000000.tar.gz" "" yes
    check "архив без config.json: отказ backup_corrupt, ключи на месте" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = backup_corrupt ] && grep -qF "$anna_priv" "$USERS_FILE"' "rc=$rc code=$GA_LAST_ERROR_CODE"

    # ── Здоровый архив: снимок ДО первой записи ──
    # Меняем живое состояние, чтобы отличить «восстановилось» от «не тронули».
    run users_add zoe Zoe awg2
    zoe_priv=$(jq -r '.users[]|select(.name=="zoe")|.peers.awg2.privkey' "$USERS_FILE")
    n_before=$(narch)
    run restore_backup "$good_arch" "" yes
    check "здоровый архив восстанавливается: rc=0" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
    check "снимок текущего состояния снят до записи (архивов стало больше)" \
        '[ "$(narch)" -gt "$n_before" ]' "было $n_before стало $(narch)"
    check "восстановление вернуло прежний users.json: zoe исчезла, anna на месте" \
        '! jq -e ".users[]|select(.name==\"zoe\")" "$USERS_FILE" >/dev/null 2>&1 && grep -qF "$anna_priv" "$USERS_FILE"'
    snap_last=$(ls -t "$BACKUP_DIR"/*.tar.gz | head -1)
    mkdir -p "$TMP/unp3" && tar xzf "$snap_last" -C "$TMP/unp3" 2>/dev/null
    check "в снимке лежит то, что перезаписали: ключ zoe можно вернуть" \
        '[ -n "$zoe_priv" ] && grep -qF "$zoe_priv" "$TMP/unp3"/*/users.json' "$snap_last"

    # Живое состояние сломано, архив здоров: снимок обязан сняться всё равно
    printf '{"schema":1,"mode":"pro"' >"$CONFIG_FILE"
    n_before=$(narch)
    run restore_backup "$good_arch" "" yes
    check "битый живой конфиг не мешает восстановлению: rc=0" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
    check "снимок сломанного состояния всё равно снят" '[ "$(narch)" -gt "$n_before" ]' "было $n_before стало $(narch)"
    check "config.json восстановлен и снова разбирается" \
        '[ "$(jq -r .mode "$CONFIG_FILE")" = lite ]' "$(head -c 120 "$CONFIG_FILE")"

    # Битый users.json — не тупик. Отказ users_corrupt останавливает ВСЕ записи
    # (§1b.1–1b.3), и единственный выход из этого состояния — восстановление:
    # оно обязано работать именно тогда, когда живой файл уже сломан.
    printf '{%s' "$(cat "$USERS_FILE")" >"$USERS_FILE"
    n_before=$(narch)
    run restore_backup "$good_arch" "" yes
    check "битый users.json не запирает человека: восстановление проходит" \
        '[ "$rc" = 0 ] && jq -e . "$USERS_FILE" >/dev/null 2>&1 && grep -qF "$anna_priv" "$USERS_FILE"' "rc=$rc $(errs)"
    check "снимок сломанного users.json всё равно снят" '[ "$(narch)" -gt "$n_before" ]' "было $n_before стало $(narch)"

    # ── 1d.1 №3: сорвавшееся восстановление не бросает сервер без управления ──
    # Обе ветки отказа копирования уходили голым `return 1`. В остатке: бот и
    # панель выключены (остановили их мы сами), распакованный архив с
    # приватными ключами ВСЕХ клиентов, ключом сервера и токеном бота лежит в
    # общем каталоге временных файлов, а цель уже обрезана — прямой cp сначала
    # обнуляет файл. Ломаем последний шаг атомарной записи («кончилось место»)
    # и смотрим все четыре следствия сразу.
    export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
    SYSCTL_LOG="$TMP/systemctl.log"; : >"$SYSCTL_LOG"
    WIPE_LOG="$TMP/wipe.log"; : >"$WIPE_LOG"
    # L1-20: службы восстановление трогает только через _ga_backup_systemctl — в сухом прогоне это строка DRY.
    # Журнал собирает DRY-строки systemctl («start goamnezia-bot»); настоящий systemctl помечается REAL.
    systemctl() { printf 'REAL %s\n' "$*" >>"$SYSCTL_LOG"; return 0; }
    _dry_echo_src=$(declare -f ga_dry_echo)
    ga_dry_echo() { case "$*" in "systemctl "*) printf '%s\n' "${*#systemctl }" >>"$SYSCTL_LOG" ;; esac; printf 'DRY: %s\n' "$*" >&2; }
    _wipe_src=$(declare -f ga_wipe_dir)
    eval "${_wipe_src/#ga_wipe_dir/_ga_wipe_dir_orig}"
    ga_wipe_dir() { printf '%s\n' "$*" >>"$WIPE_LOG"; _ga_wipe_dir_orig "$@"; }
    _commit_src=$(declare -f _ga_commit_file)
    _ga_commit_file() { return 1; }
    cfg_before=$(cat "$CONFIG_FILE")
    run restore_backup "$good_arch" "" yes
    check "1d.1 №3 отказ записи не уходит в «успех»" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = restore_write_failed ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
    check "1d.1 №3 цель не обрезана: писали рядом и переименовывали, а не поверх" \
        '[ "$(cat "$CONFIG_FILE")" = "$cfg_before" ]' "$(head -c 160 "$CONFIG_FILE")"
    check "1d.1 №3 бот и панель подняты обратно, а не оставлены выключенными" \
        'grep -q "^start goamnezia-bot$" "$SYSCTL_LOG" && grep -q "^start goamnezia-admin$" "$SYSCTL_LOG"' \
        "$(cat "$SYSCTL_LOG")"
    check "D6 HTTP-транспорт MCP на время записи остановлен и поднят обратно" \
        'grep -q "^stop goamnezia-mcp-http.service$" "$SYSCTL_LOG" && grep -q "^start goamnezia-mcp-http.service$" "$SYSCTL_LOG"' \
        "$(cat "$SYSCTL_LOG")"
    check "L1-20 восстановление в сухом прогоне не зовёт настоящий systemctl (стоп, старт, reload nginx)" \
        '! grep -q "^REAL " "$SYSCTL_LOG"' "$(cat "$SYSCTL_LOG")"
    check "1d.1 №3 распакованный архив затёрт нулями (ga_wipe_dir), а не удалён именем" \
        '[ -s "$WIPE_LOG" ]' "$(cat "$WIPE_LOG")"
    check "1d.1 №3 и лежал он в каталоге для секретов (договор §0.7), а не в общем /tmp" \
        'grep -q "^$GOAMNEZIA_SECRET_TMPDIR/" "$WIPE_LOG"' "$(cat "$WIPE_LOG")"
    check "1d.1 №3 после уборки во временном каталоге не осталось ничего от архива" \
        '[ -z "$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)" ]' "$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)"
    eval "$_commit_src"
    # Второе доказательство, уже независимое от способа записи: цель физически
    # недоступна для записи ни cp, ни атомарному писателю. Прежний код на этом
    # месте уходил голым `return 1` — сервер оставался и без данных, и без
    # управления. От root права ничего не запрещают, поэтому там пропускаем.
    if [ "$(id -u)" != 0 ]; then
        : >"$SYSCTL_LOG"; : >"$WIPE_LOG"
        chmod 500 "$GOAMNEZIA_DIR"; chmod 400 "$CONFIG_FILE"
        run restore_backup "$good_arch" "" yes
        chmod 700 "$GOAMNEZIA_DIR"; chmod 600 "$CONFIG_FILE"
        check "1d.1 №3 недоступная для записи цель: отказ, а не «успешно»" \
            '[ "$rc" != 0 ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
        check "1d.1 №3 и здесь бот с панелью подняты обратно" \
            'grep -q "^start goamnezia-bot$" "$SYSCTL_LOG" && grep -q "^start goamnezia-admin$" "$SYSCTL_LOG"' \
            "$(cat "$SYSCTL_LOG")"
        check "1d.1 №3 и здесь распакованный архив с ключами убран из памяти" \
            'grep -q "^$GOAMNEZIA_SECRET_TMPDIR/" "$WIPE_LOG" && [ -z "$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)" ]' \
            "wipe=$(cat "$WIPE_LOG") осталось=$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)"
    else
        skipped "1d.1 №3 недоступная для записи цель" "прогон от root: права на файл ничего не запрещают"
    fi
    eval "$_wipe_src"
    unset -f systemctl _ga_wipe_dir_orig
    eval "$_dry_echo_src"
    export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"   # каталог стенда (L1-19), не /dev/shm машины
    # После починки восстановление обязано снова проходить целиком.
    run restore_backup "$good_arch" "" yes
    check "1d.1 №3 после починки записи восстановление снова проходит" \
        '[ "$rc" = 0 ] && grep -qF "$anna_priv" "$USERS_FILE"' "rc=$rc $(errs)"

    # ── Круг по бэкапам: D2, D3, D4, D6, D7 ──
    # D2: профиль не поднялся — это не «успех».
    _sr_src=$(declare -f amnezia_state_restore)
    amnezia_state_restore() { if [ "$1" = awg3 ]; then ga_err container_not_up "stend: awg3 не поднялся"; return 1; fi; return 0; }
    run restore_backup "$good_arch" "" yes
    eval "$_sr_src"
    check "D2 профиль не поднялся — код restore_profiles_failed, профиль назван в сообщении" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = restore_profiles_failed ] && grep -q awg3 <<<"$GA_ERR_MSG"' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
    check "D2 данные при этом разложены: ключ anna на месте" 'grep -qF "$anna_priv" "$USERS_FILE"'
    # D3: работающий контейнер пересоздаётся из state/.
    _cs_src=$(declare -f docker_container_state)
    docker_container_state() { echo running; }
    run restore_backup "$good_arch" "" yes
    eval "$_cs_src"
    check "D3 работающий контейнер при восстановлении снят и поднят заново из state/" \
        '[ "$rc" = 0 ] && grep -q "DRY: docker stop amnezia-awg2 && docker rm -fv amnezia-awg2" "$ERR" && grep -q "DRY: docker run .*amnezia-awg2" "$ERR"' \
        "rc=$rc $(grep DRY "$ERR" | head -8)"
    # D4: профиль, которого нет в архиве, не остаётся работать без управления.
    rm -rf "$TMP/noawg3" && mkdir -p "$TMP/noawg3" && tar xzf "$good_arch" -C "$TMP/noawg3"
    nd=$(ls -d "$TMP/noawg3"/goamnezia_backup_* | head -1)
    jq 'del(.profiles.awg3)' "$nd/config.json" >"$nd/config.json.tmp" && mv "$nd/config.json.tmp" "$nd/config.json"
    rm -rf "$nd/state/amnezia-awg3"
    tar czf "$TMP/noawg3.tar.gz" -C "$TMP/noawg3" "$(basename "$nd")"
    run restore_backup "$TMP/noawg3.tar.gz" "" yes
    check "D4 профиль вне архива снят: контейнер amnezia-awg3 и его копия состояния, в конфиге его нет" \
        '[ "$rc" = 0 ] && grep -q "DRY: docker stop amnezia-awg3 && docker rm -fv amnezia-awg3" "$ERR" && [ ! -d "$STATE_DIR/amnezia-awg3" ] && [ -z "$(jq -r ".profiles.awg3 // empty" "$CONFIG_FILE")" ]' \
        "rc=$rc $(errs | tail -4)"
    # D6: фаза записи — под замком конфигурации.
    : >"$TMP/locks.log"
    _lock_src=$(declare -f ga_with_config_lock)
    ga_with_config_lock() { printf '%s\n' "$1" >>"$TMP/locks.log"; GA_CONFIG_LOCK_HELD=1 "$@"; }
    run restore_backup "$good_arch" "" yes
    eval "$_lock_src"
    check "D6 фаза записи восстановления — под замком конфигурации, полный архив вернул awg3" \
        '[ "$rc" = 0 ] && grep -qx "_restore_apply_locked" "$TMP/locks.log" && [ -d "$STATE_DIR/amnezia-awg3" ]' "rc=$rc locks=$(tr "\n" " " <"$TMP/locks.log")"
    # D7: архив режима с сайтом на сервере без nginx — подсказка, а не молчаливый режим только в конфиге.
    rm -rf "$TMP/proarch" && mkdir -p "$TMP/proarch" && tar xzf "$good_arch" -C "$TMP/proarch"
    pd=$(ls -d "$TMP/proarch"/goamnezia_backup_* | head -1)
    jq '.mode = "pro" | .domain = "example.com"' "$pd/config.json" >"$pd/config.json.tmp" && mv "$pd/config.json.tmp" "$pd/config.json"
    tar czf "$TMP/proarch.tar.gz" -C "$TMP/proarch" "$(basename "$pd")"
    GA_RESTORE_MODE_HINT=""
    run restore_backup "$TMP/proarch.tar.gz" "" yes
    if command -v nginx >/dev/null 2>&1; then
        skipped "D7 архив pro на сервере без nginx" "на машине прогона nginx есть"
    else
        check "D7 архив pro на сервере без nginx — подсказка про режим в ответе и на экране" \
            '[ "$rc" = 0 ] && [ -n "$GA_RESTORE_MODE_HINT" ] && grep -qF "$GA_RESTORE_MODE_HINT" "$ERR"' "rc=$rc hint=$GA_RESTORE_MODE_HINT"
    fi
    run restore_backup "$good_arch" "" yes
    check "после проверок D2–D7 полный архив снова восстанавливается" '[ "$rc" = 0 ] && [ "$(jq -r .mode "$CONFIG_FILE")" = lite ]' "rc=$rc $(errs | tail -3)"

    # Второй круг, №2: при восстановлении снос работающего контейнера — только после сборки образа.
    _cs_src=$(declare -f docker_container_state)
    _bd_src=$(declare -f docker_build)
    docker_container_state() { echo running; }
    docker_build() { ga_err build_failed "stend: сборка упала"; return 1; }
    run restore_backup "$good_arch" "" yes
    eval "$_bd_src"; eval "$_cs_src"
    check "R2 №2 сборка образа упала — работающий контейнер НЕ снесён, отказ restore_profiles_failed" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = restore_profiles_failed ] && ! grep -q "DRY: docker stop amnezia-awg2" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(grep DRY "$ERR" | head -5)"
    # №3: служба, внутри которой идёт восстановление, не останавливается.
    printf '0::/system.slice/goamnezia-bot.service\n' >"$TMP/cgroup.bot"
    SYSCTL_LOG="$TMP/systemctl2.log"; : >"$SYSCTL_LOG"
    systemctl() { printf 'REAL %s\n' "$*" >>"$SYSCTL_LOG"; return 0; }
    ga_dry_echo() { case "$*" in "systemctl "*) printf '%s\n' "${*#systemctl }" >>"$SYSCTL_LOG" ;; esac; printf 'DRY: %s\n' "$*" >&2; }
    GA_PROC_CGROUP="$TMP/cgroup.bot" run restore_backup "$good_arch" "" yes
    unset -f systemctl
    eval "$_dry_echo_src"
    check "R2 №3 восстановление из бота: юнит бота не останавливается, панель и MCP — да" \
        '[ "$rc" = 0 ] && ! grep -q "^stop goamnezia-bot$" "$SYSCTL_LOG" && grep -q "^stop goamnezia-admin$" "$SYSCTL_LOG" && grep -q "^stop goamnezia-mcp-http.service$" "$SYSCTL_LOG"' \
        "rc=$rc $(tr "\n" ";" <"$SYSCTL_LOG")"
    # №4: замок не взялся — распакованный архив затёрт.
    export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm-lock"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"
    _lock_src=$(declare -f ga_with_config_lock)
    ga_with_config_lock() { return 75; }
    run restore_backup "$good_arch" "" yes
    eval "$_lock_src"
    check "R2 №4 замок не взялся — отказ, и распакованного архива в памяти не осталось" \
        '[ "$rc" = 75 ] && [ -z "$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)" ]' "rc=$rc осталось=$(ls -A "$GOAMNEZIA_SECRET_TMPDIR" 2>/dev/null)"
    export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"   # каталог стенда (L1-19), не /dev/shm машины
    # №6: профиль вне архива при недоступном демоне — ни контейнер, ни копия состояния не трогаются.
    docker_container_state() { echo "$GA_DOCKER_STATE_UNREACHABLE"; }
    run restore_backup "$TMP/noawg3.tar.gz" "" yes
    eval "$_cs_src"
    check "R2 №6 демон не отвечает — копия состояния профиля вне архива оставлена, сказано словами" \
        '[ -d "$STATE_DIR/amnezia-awg3" ] && grep -qF "$(tf backup_profile_drop_skipped awg3 amnezia-awg3)" "$ERR"' "rc=$rc $(errs | tail -3)"
    run restore_backup "$good_arch" "" yes
    check "после проверок второго круга полный архив снова восстанавливается" '[ "$rc" = 0 ] && [ -d "$STATE_DIR/amnezia-awg3" ]' "rc=$rc $(errs | tail -3)"

    # Ротация: непонятное значение глубины не должно означать «удалить всё».
    # Глубину задаёт окружение юнита (GOAMNEZIA_BACKUP_KEEP), править его может
    # кто угодно.
    n_before=$(narch)
    run cleanup_old_backups abc
    check "ротация с нечисловой глубиной ничего не удаляет" '[ "$(narch)" = "$n_before" ]' "было $n_before стало $(narch)"
    run cleanup_old_backups 0
    check "ротация с глубиной 0 ничего не удаляет" '[ "$(narch)" = "$n_before" ]' "было $n_before стало $(narch)"
    run cleanup_old_backups 1
    check "ротация с глубиной 1 оставляет ровно один архив" '[ "$(narch)" = 1 ]' "осталось $(narch)"

    # ── Ночной проход: backup_tick (§1d.2 «ночная копия может молча умереть») ──
    # Раньше вся ночная логика жила одной строкой в ExecStart, и проверить её
    # можно было только по тексту юнита. Теперь это функция — проверяем делом.
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    n_before=$(narch)
    run backup_tick 30
    check "backup_tick: копия снята, ответ JSON с ok=true" \
        '[ "$rc" = 0 ] && is_json "$out" && [ "$(jq -r .ok <<<"$out")" = true ] && [ "$(narch)" -gt "$n_before" ]' \
        "rc=$rc out=$out $(errs)"
    check "backup_tick: исход записан в backup_last.json" \
        '[ "$(jq -r .ok "$GOAMNEZIA_DIR/backup_last.json")" = true ]' "$(cat "$GOAMNEZIA_DIR/backup_last.json" 2>/dev/null)"
    run backup_last_json
    check "backup_last_json отвечает на «когда была последняя копия»" \
        'is_json "$out" && [ -n "$(jq -r .name <<<"$out")" ] && [ -n "$(jq -r .created <<<"$out")" ] && [ "$(jq -r .tick_ok <<<"$out")" = true ]' \
        "$out"

    # ПРОВАЛ тоже обязан быть виден. Проверка выше смотрела только удачу, и
    # поэтому молчала о том, что `.ok // null` в jq отбрасывает и `false`:
    # провалившийся ночной проход превращался в «неизвестно», а красная строка
    # на экране не печаталась никогда. Экран заводился ровно ради этого случая.
    printf '{"ok":false,"code":"low_disk","at":"2026-09-11T03:20:00Z"}' > "$GOAMNEZIA_DIR/backup_last.json"
    run backup_last_json
    check "backup_last_json: провал показан как провал, а не как «неизвестно»" \
        '[ "$(jq -r .tick_ok <<<"$out")" = false ] && [ "$(jq -r .tick_code <<<"$out")" = low_disk ]' "$out"

    # Места нет → ротация идёт ПЕРВОЙ. Иначе состояние самозакрепляется: копия
    # ждёт места, а место ждёт копии, и так каждую ночь до конца жизни сервера.
    # df подменяем функцией: check_disk_space спрашивает именно её, и так
    # «диск полон» проверяется без настоящего заполнения диска.
    _df_orig=$(declare -f df 2>/dev/null)
    # Фикстура места прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на этот блок снята: «диск полон» — подменённым df
    _df_fx="${GOAMNEZIA_DF_FIXTURE:-}"; unset GOAMNEZIA_DF_FIXTURE
    live_arch=$(ls "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -n 1)
    df() { printf 'Filesystem 1M-blocks Used Available Use%% Mounted\n/dev/x 100 99 1 99%% /\n'; }
    run create_backup
    check "create_backup на забитом диске отказывает с кодом low_disk" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = low_disk ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
    run restore_backup "$live_arch" "" yes
    check "restore_backup на забитом диске отказывает ДО распаковки" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = low_disk ]' "rc=$rc code=$GA_ERR_CODE $(errs)"
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    # Установившееся состояние: архивов РОВНО столько, сколько держим. Обычная
    # ротация тут не удаляет ничего (count == keep), и ветка «сначала ротация»
    # не освобождала ни байта — состояние самозакреплялось навсегда.
    n_steady=$(narch)
    run backup_tick "$n_steady"
    check "ротация при нехватке места освобождает и в установившемся состоянии" \
        '[ "$(narch)" -lt "$n_steady" ]' "было $n_steady стало $(narch)"
    # Возвращаем каталог к прежнему числу: следующая проверка считает архивы.
    while [ "$(narch)" -lt "$n_steady" ]; do
        cp "$live_arch" "$BACKUP_DIR/goamnezia_backup_1999010$(narch)_000000.tar.gz" 2>/dev/null || break
    done

    n_before=$(narch)
    run backup_tick 1
    check "backup_tick на забитом диске: сначала ротация, копия всё равно не снята" \
        '[ "$rc" != 0 ] && [ "$(narch)" -le 1 ] && [ "$(narch)" -lt "$n_before" ]' \
        "rc=$rc было $n_before стало $(narch) $(errs)"
    check "backup_tick на забитом диске: отказ виден словами, а не только в журнале" \
        'grep -qF "$(tf backup_tick_failed "")" "$ERR"' "$(errs)"
    check "backup_tick на забитом диске: событие ушло боту" \
        '[ -n "$(grep -l backup_failed "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' \
        "$(ls -1 "$GOAMNEZIA_NOTIFY_DIR" 2>/dev/null)"
    check "backup_tick на забитом диске: отказ записан в память прохода" \
        '[ "$(jq -r .ok "$GOAMNEZIA_DIR/backup_last.json")" = false ] && [ "$(jq -r .code "$GOAMNEZIA_DIR/backup_last.json")" = low_disk ]' \
        "$(cat "$GOAMNEZIA_DIR/backup_last.json" 2>/dev/null)"
    unset -f df
    [ -n "$_df_orig" ] && eval "$_df_orig"
    [ -n "$_df_fx" ] && export GOAMNEZIA_DF_FIXTURE="$_df_fx"
    # После починки места ночной проход снова проходит и память обновляется.
    run backup_tick 30
    check "после появления места ночной проход снова снимает копию" \
        '[ "$rc" = 0 ] && [ "$(jq -r .ok "$GOAMNEZIA_DIR/backup_last.json")" = true ]' "rc=$rc $(errs)"

    # ── Расписание ночной копии (§1d.2 №14) ──────────────────────────────────
    # Менять его было нечем: set_backup_schedule звалась только из первой
    # установки, а текст обещал «настраивается в Обслуживание → Бэкап».
    run set_backup_schedule never
    check "неизвестная периодичность → отказ bad_schedule, юниты не тронуты" \
        '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = bad_schedule ]' "rc=$rc code=$GA_LAST_ERROR_CODE"
    export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"; mkdir -p "$GOAMNEZIA_SYSTEMD_DIR"
    run set_backup_schedule weekly
    check "set_backup_schedule weekly: юнит и таймер написаны, systemctl только напечатан" \
        '[ "$rc" = 0 ] && [ "$out" = weekly ] && grep -q "^OnCalendar=Sun 03:20:00$" "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.timer" \
          && grep -q "DRY: systemctl enable --now goamnezia-backup.timer" "$ERR"' "rc=$rc out=$out $(errs)"
    check "ExecStart зовёт backup_tick, а не голую пару «копия && ротация»" \
        'grep -q "ExecStart=.*backup_tick" "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.service" && ! grep -q "ExecStart=.*create_backup" "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.service"' \
        "$(cat "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.service" 2>/dev/null)"
    run backup_schedule_status
    check "backup_schedule_status отвечает выбранной периодичностью" \
        '[ "$out" = "frequency=weekly calendar=Sun 03:20:00" ]' "out=$out"
    run set_backup_schedule off
    check "off снимает юниты (единственная автоматическая страховка выключается явно)" \
        '[ "$rc" = 0 ] && [ ! -f "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.timer" ] && [ ! -f "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-backup.service" ]' "$(errs)"
    unset GOAMNEZIA_SYSTEMD_DIR
    # Без явного каталога юнитов стенд не должен писать в живую систему вовсе.
    run set_backup_schedule daily
    check "в dry-run без GOAMNEZIA_SYSTEMD_DIR расписание ничего не пишет в /etc" \
        '[ "$rc" = 0 ] && [ "$(host_sig /etc/systemd/system/goamnezia-backup.timer)" = "$HOST_BACKUP_TIMER_SIG" ]' "$(errs)"

    # ── Выход с экрана «Восстановить из бэкапа» (находка живого стенда) ──────
    # У экрана был ровно один вопрос — «Номер бэкапа (или путь к файлу)» — и ни
    # одного способа уйти. «0» разбирался как НОМЕР строки, такой строки нет,
    # и человек, который просто передумал, получал красное «✗ Бэкап не найден»
    # при полном списке живых копий прямо над вопросом: продукт обвинял себя в
    # пропаже копий там, где ничего не пропадало. Пустой ввод вёл туда же.
    check "готово к проверке: копии в списке есть, отказ был бы неправдой" '[ "$(narch)" -ge 1 ]' "архивов $(narch)"
    run interactive_restore <<<"0"
    check "«0» — возврат без ошибки, а не «Бэкап не найден» при полном списке" \
        '[ "$rc" = 0 ] && ! grep -qF "$(t backup_not_found)" "$ERR"' "rc=$rc $(errs)"
    check "«0» отвечает словом из словаря («отменено»), а не молчанием" \
        'grep -qF "$(t cancelled)" "$ERR"' "$(errs)"
    check "выход назван словами прямо в приглашении: строка «0) Назад» и легенда" \
        'grep -qF "$(t back)" "$ERR" && grep -qF "$(t backup_pick_legend)" "$ERR"' "$(errs)"
    run interactive_restore <<<""
    check "пустой ввод (передумал и нажал Enter) — тот же возврат без ошибки" \
        '[ "$rc" = 0 ] && ! grep -qF "$(t backup_not_found)" "$ERR" && grep -qF "$(t cancelled)" "$ERR"' "rc=$rc $(errs)"
    run interactive_restore <<<"   "
    check "пробелы вместо ответа — тоже возврат, а не путь к файлу из пробелов" \
        '[ "$rc" = 0 ] && ! grep -qF "$(t backup_not_found)" "$ERR"' "rc=$rc $(errs)"
    run interactive_restore </dev/null
    check "оборванная SSH-сессия (EOF) — возврат, а не разбор пустоты как номера" \
        '[ "$rc" = 0 ] && ! grep -qF "$(t backup_not_found)" "$ERR"' "rc=$rc $(errs)"
    # Настоящая ошибка обязана остаться ошибкой: «отмена» не должна проглотить
    # промах по номеру — иначе экран будет молча возвращать и на опечатку.
    run interactive_restore <<<"99999"
    check "несуществующий номер по-прежнему честно отказывает «Бэкап не найден»" \
        '[ "$rc" != 0 ] && grep -qF "$(t backup_not_found)" "$ERR"' "rc=$rc $(errs)"
    run interactive_restore <<<"$TMP/нет-такого-архива.tar.gz"
    check "несуществующий путь к файлу тоже отказывает, а не возвращается тихо" \
        '[ "$rc" != 0 ] && grep -qF "$(t backup_not_found)" "$ERR"' "rc=$rc $(errs)"
else
    skipped "резервные копии" "нет lib/backup.sh"
fi

# ── Личность сервера: битый конфиг не должен перевыпускать ключи ────────────
# Самый тяжёлый сценарий продукта (§1c.1): битый config.json уводил в мастер
# первой установки, тот ставил профиль ПОВЕРХ живого состояния и перезаписывал
# приватный ключ сервера, после чего следующее серверное действие уезжало с
# новым ключом в контейнер — и все выданные ключи умирали вместе с вновь
# выдаваемыми. Здесь проверяем не «не упало», а САМИ КЛЮЧИ.
section "битый config.json не перевыпускает ключи сервера"
K_PRIV_BEFORE=$(cat "$SD2/wireguard_server_private_key.key" 2>/dev/null)
K_PUB_BEFORE=$(ga_awgkeys_pubkey "$K_PRIV_BEFORE" 2>/dev/null)
K_CFG_BEFORE=$(ga_config_get '.profiles.awg2.server_pubkey' 2>/dev/null)
check "до опыта: открытый ключ в конфиге выведен из приватника в state/" \
    '[ -n "$K_PUB_BEFORE" ] && [ "$K_PUB_BEFORE" = "$K_CFG_BEFORE" ]' "state=$K_PUB_BEFORE cfg=$K_CFG_BEFORE"

cp "$CONFIG_FILE" "$CONFIG_FILE.ok"
head -c 40 "$CONFIG_FILE.ok" > "$CONFIG_FILE"

run amnezia_profile_install awg2 40000 default
# Конфиг СЛОМАН, значит отличить живой профиль от осиротевшего ключа нельзя —
# и отказ называет именно эту причину, а не выдумывает конфликт. Проверяем не
# конкретное слово, а то, что установка НЕ ПРОШЛА и причина названа.
check "повторная установка при битом конфиге: отказ, причина названа" \
    '[ "$rc" != 0 ] && { [ "$GA_ERR_CODE" = config_corrupt ] || [ "$GA_ERR_CODE" = state_conflict ]; }' "rc=$rc code=$GA_ERR_CODE"

K_PRIV_AFTER=$(cat "$SD2/wireguard_server_private_key.key" 2>/dev/null)
check "ПРИВАТНЫЙ КЛЮЧ СЕРВЕРА НЕ ПЕРЕЗАПИСАН" \
    '[ -n "$K_PRIV_AFTER" ] && [ "$K_PRIV_AFTER" = "$K_PRIV_BEFORE" ]' "было ${K_PRIV_BEFORE:0:12}… стало ${K_PRIV_AFTER:0:12}…"
check "серверный конфиг по-прежнему держит тот же приватник" \
    'grep -qF "$K_PRIV_BEFORE" "$SD2/awg0.conf"'

cp "$CONFIG_FILE.ok" "$CONFIG_FILE"
# Страж обязан различать ДВА состояния, иначе получается замкнутый круг:
# сорвавшаяся установка оставляет ключ сервера на диске (шаг 3 прошёл), но
# записи профиля в настройках нет (до шага 5 не дошло). Повтор говорил «сначала
# удалите профиль», а удаление отвечало «профиль не установлен» — и выхода не
# было ни из одного интерфейса, только rm руками на сервере.
#
# awg1 в этом прогоне уже установлен, поэтому осиротевшее состояние делаем так:
# ключи на диске оставляем, запись профиля из настроек убираем.
SD_ORPH="$STATE_DIR/amnezia-awg1"
K_ORPH_BEFORE=$(cat "$SD_ORPH/wireguard_server_private_key.key" 2>/dev/null || true)
jq 'del(.profiles.awg1)' "$CONFIG_FILE" > "$CONFIG_FILE.orph" && mv "$CONFIG_FILE.orph" "$CONFIG_FILE"
check "подготовка: ключ на диске есть, профиля awg1 в настройках больше нет" \
    '[ -n "$K_ORPH_BEFORE" ] && [ -z "$(ga_config_get ".profiles.awg1.installed_at")" ]' \
    "ключ=${K_ORPH_BEFORE:0:10} профиль=[$(ga_config_get ".profiles.awg1.installed_at")]"

run amnezia_profile_install awg1 40009 default
check "осиротевший ключ не запирает профиль навсегда: не state_conflict" \
    '[ "$GA_ERR_CODE" != state_conflict ]' "rc=$rc code=$GA_ERR_CODE"
check "осиротевший ключ заменён новым" \
    '[ -n "$(cat "$SD_ORPH/wireguard_server_private_key.key" 2>/dev/null || true)" ] && [ "$(cat "$SD_ORPH/wireguard_server_private_key.key" 2>/dev/null || true)" != "$K_ORPH_BEFORE" ]' \
    "было ${K_ORPH_BEFORE:0:10} стало $(cut -c1-10 "$SD_ORPH/wireguard_server_private_key.key" 2>/dev/null || true)"

KJ=$(ga_amn_keys_consistent awg2 2>/dev/null)
check "сверка личности: три источника совпадают, ok=true" \
    '[ "$(printf "%s" "$KJ" | jq -r .ok)" = true ]' "$KJ"

# А теперь развалим личность руками и убедимся, что сверка это ВИДИТ.
cp "$SD2/wireguard_server_private_key.key" "$SD2/priv.ok"
ga_awgkeys genkey > "$SD2/wireguard_server_private_key.key" 2>/dev/null
KJ2=$(ga_amn_keys_consistent awg2 2>/dev/null)
check "подменённый приватник в state/: сверка отдаёт ok=false" \
    '[ "$(printf "%s" "$KJ2" | jq -r .ok)" = false ]' "$KJ2"
cp "$SD2/priv.ok" "$SD2/wireguard_server_private_key.key"; rm -f "$SD2/priv.ok"

# ── Битый users.json: цифры не выдумываются ─────────────────────────────────
# Здесь потери ключей уже нет (писателя прикрывает страж), но отчёты врали:
# статистика писала ноль пользователей там, где их десятки, а карточка меню
# показывала «0 пользователей» — человек решил бы, что ключей не было, и завёл
# бы заново поверх существующих.
if [ -f "$ROOT/lib/stats.sh" ]; then
    section "битый users.json: статистика и карточка меню не показывают ноль"
    # shellcheck source=/dev/null
    . "$ROOT/lib/stats.sh" 2>/dev/null || true
    cp "$USERS_FILE" "$USERS_FILE.ok"
    head -c 25 "$USERS_FILE.ok" > "$USERS_FILE"

    if type stats_tick >/dev/null 2>&1; then
        run stats_tick
        check "stats_tick на битом users.json: отказ, а не запись нуля пользователей" \
            '[ "$rc" != 0 ]' "rc=$rc code=$GA_ERR_CODE"
    fi
    if type users_list >/dev/null 2>&1; then
        run users_list
        check "users_list на битом файле отказывает — карточке есть по чему отличить поломку от пустоты" \
            '[ "$rc" != 0 ]' "rc=$rc code=$GA_ERR_CODE"
    fi
    cp "$USERS_FILE.ok" "$USERS_FILE"
    run users_list
    check "после починки файла список снова читается" '[ "$rc" = 0 ]' "rc=$rc"
fi

# ── 1d.1 №1: битый config.json ослепляет сторож, лимиты и статистику ────────
# Класс тот же, что у users.json выше, только цена выше: список установленных
# профилей читали голым jq через $( ), и подстановка съедала код отказа. Пустой
# список все трое читали как «делать нечего» и отчитывались УСПЕХОМ — сторож не
# сторожил, лимиты и сроки не применялись, статистика замирала, и по ответу это
# было неотличимо от нормального тика. Проверка ровно про это: на битом конфиге
# каждый тик обязан ОТКАЗАТЬ, а не ответить «успешно» с пустотой.
section "битый config.json: сторож, лимиты и статистика отказывают, а не «успешно»"
cp "$CONFIG_FILE" "$CONFIG_FILE.ok"
head -c 30 "$CONFIG_FILE.ok" > "$CONFIG_FILE"

run ga_profiles_installed
check "1d.1 №1 ga_profiles_installed на битом конфиге: код отказа, stdout пуст" \
    '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=[$out]"
run ga_profiles_load _p_list
check "1d.1 №1 ga_profiles_load доносит код до вызывающей оболочки (config_corrupt)" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = config_corrupt ]' "rc=$rc last=$GA_LAST_ERROR_CODE"

if type amnezia_watch_tick >/dev/null 2>&1; then
    # Тик раз в час сверяет адрес сервера с внешней службой (get_server_ip): без GOAMNEZIA_SERVER_IP это
    # три настоящих curl к ipify/icanhazip/ifconfig.me, а без сети — до 15 с ожидания (L1-18).
    GOAMNEZIA_SERVER_IP=203.0.113.10 run amnezia_watch_tick
    check "1d.1 №1 сторож на битом конфиге отказывает, а не отчитывается о нуле профилей" \
        '[ "$rc" != 0 ] && [ "$(jq -r ".checked // empty" <<<"$out" 2>/dev/null)" != 0 ]' \
        "rc=$rc out=$out"
fi
run users_quota_tick
check "1d.1 №1 тик лимитов и сроков на битом конфиге отказывает" \
    '[ "$rc" != 0 ]' "rc=$rc code=$GA_ERR_CODE"
if type stats_tick >/dev/null 2>&1; then
    run stats_tick
    check "1d.1 №1 тик статистики на битом конфиге отказывает" \
        '[ "$rc" != 0 ]' "rc=$rc code=$GA_ERR_CODE"
fi
check "1d.1 №1 спасательная копия сломанного конфига лежит рядом, как у users.json" \
    'ls "$GOAMNEZIA_DIR"/backups/config.json.broken-* >/dev/null 2>&1' \
    "$(ls "$GOAMNEZIA_DIR"/backups 2>/dev/null)"

cp "$CONFIG_FILE.ok" "$CONFIG_FILE"; rm -f "$CONFIG_FILE.ok"
run ga_profiles_installed
check "1d.1 №1 после починки конфига список профилей снова читается" \
    '[ "$rc" = 0 ] && [ -n "$out" ]' "rc=$rc out=[$out]"
run users_quota_tick
check "1d.1 №1 и тик лимитов снова проходит" '[ "$rc" = 0 ]' "rc=$rc $(errs)"

# ── Итог ─────────────────────────────────────────────────────────────────────

# ── SR-24: обрыв восстановления сигналом — службы назад, распакованный архив затёрт ─────────────
# Бот и панель убивают долгий вызов по таймауту, SSH обрывается: раньше бот, панель и MCP оставались
# остановленными, а users.json со всеми приватными ключами лежал в /dev/shm до перезагрузки.
section "SR-24: обрыв восстановления — службы назад, архив затёрт"
sr24_tmp=$(mktemp -d "$TMP/sr24.XXXXXX"); printf 'privkey\n' >"$sr24_tmp/users.json"
sr24_log="$TMP/sr24_systemctl.log"; : >"$sr24_log"
( systemctl() { echo "REAL systemctl $*" >>"$sr24_log"; }
  ga_dry_echo() { echo "$*" >>"$sr24_log"; }
  GA_RESTORE_TMP="$sr24_tmp"; GA_RESTORE_MCP_WAS_ACTIVE=1; _restore_on_signal ) >/dev/null 2>&1; sr24_rc=$?
check "SR-24 ловушка: выход 143, бот, панель и MCP запущены, распакованный архив затёрт" \
    '[ "$sr24_rc" = 143 ] && grep -qx "systemctl start $BOT_SERVICE" "$sr24_log" && grep -qx "systemctl start $ADMIN_SERVICE" "$sr24_log" && grep -q "^systemctl start goamnezia-mcp-http" "$sr24_log" && [ ! -e "$sr24_tmp" ]' \
    "rc=$sr24_rc $(cat "$sr24_log")"
check "SR-24 ловушку ставит само применение восстановления и снимает на обоих выходах" \
    '[ "$(grep -c "trap .*_restore_on_signal. TERM HUP INT" "$ROOT/lib/backup.sh")" = 1 ] && [ "$(grep -c "trap - TERM HUP INT" "$ROOT/lib/backup.sh")" -ge 3 ]'

# ── LC-19, SEC-C1, SR-29: восстановление — архив сноса, что копия не откатывает, смешение состояний ──
# Бот, панель и копии сноса — во временных каталогах стенда: восстановление пишет в BOT_DIR/ADMIN_DIR.
if type restore_backup >/dev/null 2>&1; then
    BOT_DIR="$TMP/botdir"; ADMIN_DIR="$TMP/admindir"
    export GOAMNEZIA_WIPE_HOME="$TMP/roothome"
    mkdir -p "$GOAMNEZIA_WIPE_HOME"
    rb_cfg_keep=$(cat "$CONFIG_FILE"); rb_users_keep=$(cat "$USERS_FILE")
    rb_put_back() {   # вернуть стенд к состоянию до раздела
        printf '%s\n' "$rb_cfg_keep" >"$CONFIG_FILE"; printf '%s\n' "$rb_users_keep" >"$USERS_FILE"
        chmod 600 "$CONFIG_FILE" "$USERS_FILE"
    }
    # repack <архив> <новое имя без .tar.gz> <команда правки каталога> — распаковать копию, поправить, упаковать
    repack() {
        local src="$1" name="$2" fix="$3" d="$TMP/repack.$RANDOM" inner
        mkdir -p "$d" && tar xzf "$src" -C "$d" 2>/dev/null
        inner=$(find "$d" -maxdepth 1 -type d -name 'goamnezia_backup_*' | head -1)
        mv "$inner" "$d/$name"
        ( cd "$d/$name" && eval "$fix" ) >/dev/null 2>&1
        tar czf "$BACKUP_DIR/$name.tar.gz" -C "$d" "$name" && rm -rf "$d"
        printf '%s' "$BACKUP_DIR/$name.tar.gz"
    }

    section "LC-19: архив «Удалить всё» и копии сноса в /root восстанавливаются"
    run create_backup ""
    lc19_bk="$out"
    lc19_wipe="$GOAMNEZIA_WIPE_HOME/goamnezia-wipe-20990101_000000.tar.gz"
    ( umask 077; tar czf "$lc19_wipe" -C / "${GOAMNEZIA_DIR#/}" ) 2>/dev/null
    check "LC-19 готово: архив сноса (tar каталога продукта) снят" '[ -s "$lc19_wipe" ] && [ -f "$lc19_bk" ]' "$(errs)"
    run users_add lc19u "" awg2
    rm -f "$lc19_bk" "$lc19_bk.sha256"
    run restore_backup "$lc19_wipe" "" yes
    check "LC-19 архив сноса восстанавливается: rc=0, пользователь после сноса исчез, прежние на месте" \
        '[ "$rc" = 0 ] && ! jq -e ".users[]|select(.name==\"lc19u\")" "$USERS_FILE" >/dev/null && [ "$(jq -c "[.users[].name]" "$USERS_FILE")" = "$(jq -c "[.users[].name]" <<<"$rb_users_keep")" ]' \
        "rc=$rc code=$GA_ERR_CODE $(errs | tail -5)"
    check "LC-19 карточка называет архив сноса и чего в нём нет" 'grep -qF "$(t backup_wipe_archive_card)" "$ERR"' "$(errs | head -5)"
    check "LC-19 копии, лежавшие в каталоге бэкапов на момент сноса, вернулись туда" '[ -f "$lc19_bk" ] && [ -f "$lc19_bk.sha256" ]' "$(ls "$BACKUP_DIR")"
    cp "$lc19_bk" "$GOAMNEZIA_WIPE_HOME/goamnezia_backup_20990102_000000.tar.gz"
    printf 'x\n' >"$GOAMNEZIA_WIPE_HOME/other.tar.gz"
    run ga_backup_candidates
    check "LC-19 список для восстановления видит копию и архив сноса в /root, чужой файл — нет" \
        'grep -qx "$GOAMNEZIA_WIPE_HOME/goamnezia_backup_20990102_000000.tar.gz" <<<"$out" && grep -qx "$lc19_wipe" <<<"$out" && ! grep -q other.tar.gz <<<"$out"' "$out"
    run backup_list_json
    check "LC-19 backup-list: копии из /root помечены location=wipe, свои — backups" \
        '[ "$(jq -r "[.[]|select(.name==\"goamnezia-wipe-20990101_000000.tar.gz\")|.location]|.[0]" <<<"$out")" = wipe ] && [ "$(jq -r "[.[]|select(.location==\"backups\")]|length" <<<"$out")" -ge 1 ]' "$out"
    run ga_backup_resolve_name goamnezia-wipe-20990101_000000.tar.gz
    check "LC-19 мост находит архив сноса по имени" '[ "$rc" = 0 ] && [ "$out" = "$lc19_wipe" ]' "rc=$rc out=$out"
    run ga_backup_resolve_name other.tar.gz
    check "LC-19 произвольный файл из /root по имени мост не открывает" '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=$out"
    run ga_backup_resolve_name ..
    check "LC-19 «..» и прочие пути — отказ" '[ "$rc" != 0 ]' "rc=$rc"
    lc19_saved="$TMP/lc19_saved"; mkdir -p "$lc19_saved"; mv "$BACKUP_DIR"/*.tar.gz* "$lc19_saved"/ 2>/dev/null
    run interactive_restore <<<"0"
    check "LC-19 пустой каталог бэкапов: экран показывает копии сноса из /root, а не «Бэкапов нет» и выход" \
        'grep -qF "$lc19_wipe" "$ERR" && ! grep -qF "$(t backup_none)" "$ERR"' "$(errs)"
    mv "$lc19_saved"/* "$BACKUP_DIR"/ 2>/dev/null
    rm -f "$GOAMNEZIA_WIPE_HOME"/*
    rb_put_back

    section "SEC-C1: копия не откатывает доступы агентов, пароль панели и админов бота"
    mkdir -p "$ADMIN_DIR" "$BOT_DIR" "$STATE_DIR/agents"
    printf '{"hash":"old"}\n' >"$ADMIN_DIR/password.json"
    printf 'BOT_TOKEN=old\nOWNER_ID=1\nALLOWED_IDS=1,2\n' >"$BOT_DIR/.env"
    printf '%s\n' '{"schema":1,"agents":[{"id":"a-1111","secret":"OLDSECRET","enabled":true,"compromised":false},{"id":"a-3333","secret":"GONE","enabled":true}]}' >"$GOAMNEZIA_DIR/agents.json"
    printf 'chain-old\n' >"$STATE_DIR/agents/a-1111.key"
    run create_backup ""
    sec_bk="$out"
    check "SEC-C1 цепочек одноразовых ключей (state/agents) в копии нет — как обещает MCP_ROTATION §12.5" \
        '[ "$rc" = 0 ] && ! tar tzf "$sec_bk" | grep "/state/agents" >/dev/null' "$(tar tzf "$sec_bk" 2>/dev/null | grep agents)"
    check "SEC-C1 пароль панели и .env бота в копию по-прежнему входят (для чистого сервера)" \
        'lst=$(tar tzf "$sec_bk") && grep -q "/admin/password.json$" <<<"$lst" && grep -q "/bot/.env$" <<<"$lst"'
    # После копии владелец сменил пароль, убрал админа, перевыпустил и заморозил доступ, удалил второй.
    printf '{"hash":"new"}\n' >"$ADMIN_DIR/password.json"
    printf 'BOT_TOKEN=new\nOWNER_ID=1\nALLOWED_IDS=1\n' >"$BOT_DIR/.env"
    printf '%s\n' '{"schema":1,"agents":[{"id":"a-1111","secret":"NEWSECRET","enabled":false,"compromised":true}]}' >"$GOAMNEZIA_DIR/agents.json"
    printf 'chain-new\n' >"$STATE_DIR/agents/a-1111.key"
    run restore_backup "$sec_bk" "" yes
    check "SEC-C1 восстановление прошло: rc=0" '[ "$rc" = 0 ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -5)"
    check "SEC-C1 пароль панели остался действующим" 'grep -q new "$ADMIN_DIR/password.json"' "$(cat "$ADMIN_DIR/password.json")"
    check "SEC-C1 убранный админ бота не вернулся, токен — действующий" \
        'grep -qx "ALLOWED_IDS=1" "$BOT_DIR/.env" && grep -qx "BOT_TOKEN=new" "$BOT_DIR/.env"' "$(cat "$BOT_DIR/.env")"
    check "SEC-C1 доступ a-1111 не откатился к прежнему секрету и не разморозился; удалённый a-3333 не воскрес" \
        '[ "$(jq -r "[.agents[].id]|join(\",\")" "$GOAMNEZIA_DIR/agents.json")" = a-1111 ] && [ "$(jq -r ".agents[0].secret" "$GOAMNEZIA_DIR/agents.json")" = NEWSECRET ] && [ "$(jq -r ".agents[0].compromised" "$GOAMNEZIA_DIR/agents.json")" = true ] && [ "$(jq -r ".agents[0].enabled" "$GOAMNEZIA_DIR/agents.json")" = false ]' \
        "$(cat "$GOAMNEZIA_DIR/agents.json")"
    check "SEC-C1 цепочка ключей доступа — действующая, не из копии" 'grep -qx chain-new "$STATE_DIR/agents/a-1111.key"'
    check "SEC-C1 что оставлено действующим — названо (для моста) и сказано словами" \
        '[[ " $GA_RESTORE_KEPT " == *" bot "* ]] && [[ " $GA_RESTORE_KEPT " == *" panel_password "* ]] && [[ " $GA_RESTORE_KEPT " == *" agents "* ]] && [ "$GA_RESTORE_AGENTS_DROPPED" = a-3333 ] && grep -qF "$(t backup_kept_password)" "$ERR" && grep -qF "a-3333" "$ERR"' \
        "kept=[$GA_RESTORE_KEPT] dropped=[$GA_RESTORE_AGENTS_DROPPED]"
    # Старая копия с цепочкой внутри (прежние версии клали state/ целиком) — цепочку не берём.
    sec_old=$(repack "$sec_bk" goamnezia_backup_20990104_000000 'mkdir -p state/agents && printf "chain-archive\n" >state/agents/a-1111.key')
    run restore_backup "$sec_old" "" yes
    check "SEC-C1 копия прежней версии с цепочкой внутри: цепочка на сервере — прежняя" \
        '[ "$rc" = 0 ] && grep -qx chain-new "$STATE_DIR/agents/a-1111.key"' "rc=$rc $(cat "$STATE_DIR/agents/a-1111.key")"
    # Чистый сервер (переезд): ни доступов, ни пароля, ни бота — берём из копии.
    rm -f "$GOAMNEZIA_DIR/agents.json" "$ADMIN_DIR/password.json" "$BOT_DIR/.env"
    run restore_backup "$sec_bk" "" yes
    check "SEC-C1 чистый сервер: доступы, пароль панели и бот — из копии" \
        '[ "$rc" = 0 ] && [ "$(jq -r "[.agents[].id]|join(\",\")" "$GOAMNEZIA_DIR/agents.json")" = "a-1111,a-3333" ] && grep -q old "$ADMIN_DIR/password.json" && grep -qx "ALLOWED_IDS=1,2" "$BOT_DIR/.env"' \
        "rc=$rc $(cat "$GOAMNEZIA_DIR/agents.json" 2>/dev/null)"
    # Действующий agents.json не читается — что удалено после копии, не узнать: доступы из копии — выключенными.
    printf '{"agents":' >"$GOAMNEZIA_DIR/agents.json"
    run restore_backup "$sec_bk" "" yes
    check "SEC-C1 битый действующий agents.json: доступы из копии вернулись ВЫКЛЮЧЕННЫМИ" \
        '[ "$rc" = 0 ] && [ "$(jq -r "[.agents[].enabled]|unique|join(\",\")" "$GOAMNEZIA_DIR/agents.json")" = false ] && grep -qF "$(t backup_agents_restored_disabled)" "$ERR"' \
        "rc=$rc $(cat "$GOAMNEZIA_DIR/agents.json" 2>/dev/null)"
    rm -rf "$GOAMNEZIA_DIR/agents.json" "$STATE_DIR/agents" "$ADMIN_DIR" "$BOT_DIR"
    rb_put_back

    section "SR-29: восстановление не смешивает копию и живое состояние"
    run create_backup ""
    sr29_bk="$out"
    # s4: копия снята до первого пользователя — users.json в ней нет.
    sr29_nousers=$(repack "$sr29_bk" goamnezia_backup_20990105_000000 'rm -f users.json')
    run restore_backup "$sr29_nousers" "" yes
    check "SR-29 копия без users.json: список пользователей пуст, как в копии, а не живой без пиров в контейнерах" \
        '[ "$rc" = 0 ] && [ "$(jq -r ".users|length" "$USERS_FILE")" = 0 ] && grep -qF "$(t backup_restored_no_users)" "$ERR"' \
        "rc=$rc $(jq -c "[.users[].name]" "$USERS_FILE" 2>/dev/null)"
    rb_put_back
    # s12: запись users.json сорвалась после config.json — оба файла возвращаются живыми.
    sr29_port=$(repack "$sr29_bk" goamnezia_backup_20990106_000000 'jq ".profiles.awg2.port = 40999" config.json >c.tmp && mv c.tmp config.json')
    _sr29_put=$(declare -f ga_put_json_file)
    ga_put_json_file() { [ "${2:-}" = "$USERS_FILE" ] && [[ "${1:-}" != */.live/* ]] && return 1; _ga_write_json "$2" <"$1"; }
    sr29_port_before=$(jq -r .profiles.awg2.port "$CONFIG_FILE")
    run restore_backup "$sr29_port" "" yes
    check "SR-29 сорвалась запись users.json: отказ restore_write_failed, config.json — прежний (порт живой)" \
        '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = restore_write_failed ] && [ "$(jq -r .profiles.awg2.port "$CONFIG_FILE")" = "$sr29_port_before" ] && grep -qF "$(t backup_restore_rolled_back)" "$ERR"' \
        "rc=$rc code=$GA_ERR_CODE port=$(jq -r .profiles.awg2.port "$CONFIG_FILE")"
    eval "$_sr29_put"
    rb_put_back
    # s17: в копии awg1 жил в другом контейнере — живой amnezia-awg1 снимается, а не работает без управления.
    sr29_ren=$(repack "$sr29_bk" goamnezia_backup_20990107_000000 'jq ".profiles.awg1.container = \"amnezia-awg\"" config.json >c.tmp && mv c.tmp config.json && mv state/amnezia-awg1 state/amnezia-awg')
    run restore_backup "$sr29_ren" "" yes
    check "SR-29 контейнер профиля в копии другой: живой amnezia-awg1 снят, профиль поднят из копии" \
        'grep -qF "docker stop amnezia-awg1" "$ERR" && [ ! -d "$STATE_DIR/amnezia-awg1" ] && [ "$(jq -r .profiles.awg1.container "$CONFIG_FILE")" = amnezia-awg ]' \
        "rc=$rc $(errs | grep -i awg1 | head -5)"
    run restore_backup "$sr29_bk" "" yes
    check "SR-29 и обратно: копия с amnezia-awg1 снимает amnezia-awg и поднимает свой" \
        '[ "$rc" = 0 ] && grep -qF "docker stop amnezia-awg " "$ERR" && [ "$(jq -r .profiles.awg1.container "$CONFIG_FILE")" = amnezia-awg1 ] && [ -d "$STATE_DIR/amnezia-awg1" ]' \
        "rc=$rc $(errs | tail -5)"
    rb_put_back
    unset GOAMNEZIA_WIPE_HOME
else
    skipped "LC-19 / SEC-C1 / SR-29" "нет lib/backup.sh"
fi

# ── UI-4c: «Отобрать ключ на профиль» снимает страницы, где ключей не осталось ─────────────────────
section "UI-4c: страницы подключения без единого ключа снимаются вместе с последним профилем"
if [ -f "$ROOT/lib/pages.sh" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/pages.sh"
    export GOAMNEZIA_PAGES_DIR="$TMP/pages"
    mkdir -p "$GOAMNEZIA_PAGES_DIR"
    run users_add ui4u "" awg2,awg3
    ui4_page() {   # <id> <пользователь> <профили JSON>
        mkdir -p "$GOAMNEZIA_PAGES_DIR/$1" && printf 'secret\n' >"$GOAMNEZIA_PAGES_DIR/$1/secret"
        jq -nc --arg id "$1" --arg u "$2" --argjson p "$3" '{id: $id, user: $u, profiles: $p, expires_epoch: 0, forever: true}' \
            >"$GOAMNEZIA_PAGES_DIR/$1/meta.json"
    }
    ui4_page p-aaaa1111 ui4u '["awg3"]'
    ui4_page p-bbbb2222 ui4u '["awg2","awg3"]'
    ui4_page p-cccc3333 anna '["awg3"]'
    run users_del_peer ui4u awg3
    check "UI-4c снят ключ awg3: страница только на awg3 снята, на awg2+awg3 и чужая — на месте" \
        '[ "$rc" = 0 ] && [ ! -e "$GOAMNEZIA_PAGES_DIR/p-aaaa1111" ] && [ -f "$GOAMNEZIA_PAGES_DIR/p-bbbb2222/meta.json" ] && [ -f "$GOAMNEZIA_PAGES_DIR/p-cccc3333/meta.json" ]' \
        "rc=$rc $(ls "$GOAMNEZIA_PAGES_DIR") $(errs | tail -3)"
    check "UI-4c снятие страницы — в аудите (page-revoke) и сказано словами" \
        'grep -q "\"action\":\"page-revoke\".*p-aaaa1111" "$GOAMNEZIA_AUDIT_LOG" && grep -qF "$(tf users_page_revoked_no_keys p-aaaa1111 ui4u)" "$ERR"' \
        "$(grep page-revoke "$GOAMNEZIA_AUDIT_LOG" | tail -2)"
    run users_del_peer ui4u awg2
    check "UI-4c снят последний ключ: и страница на awg2+awg3 снята" \
        '[ "$rc" = 0 ] && [ ! -e "$GOAMNEZIA_PAGES_DIR/p-bbbb2222" ] && [ -f "$GOAMNEZIA_PAGES_DIR/p-cccc3333/meta.json" ]' "rc=$rc $(ls "$GOAMNEZIA_PAGES_DIR")"
    run users_del ui4u
    rm -rf "$GOAMNEZIA_PAGES_DIR"; unset GOAMNEZIA_PAGES_DIR
else
    skipped "UI-4c" "нет lib/pages.sh"
fi

# ── LC-20: контейнер «работает», а порт не опубликован ─────────────────────────────────────────────
section "LC-20: работающий контейнер без опубликованного порта — видно и лечится"
lc20_cfs="$TMP/lc20cfs"; mkdir -p "$lc20_cfs/amnezia-awg2"
touch "$lc20_cfs/amnezia-awg2/.unpublished"
GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run amnezia_profile_status awg2
check "LC-20 profiles-status: state=running, port_published=false" \
    '[ "$(jq -r .state <<<"$out")" = running ] && [ "$(jq -r .port_published <<<"$out")" = false ]' "$out"
GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run ga_amn_wait_up awg2
check "LC-20 «поднят» не засчитывается: ga_amn_wait_up → port_unpublished" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = port_unpublished ]' "rc=$rc code=$GA_ERR_CODE"
GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run amnezia_state_restore awg2
check "LC-20 «Поднять заново» пересоздаёт такой контейнер (docker run), порт опубликован, rc=0" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker run .*amnezia-awg2" "$ERR" && [ ! -f "$lc20_cfs/amnezia-awg2/.unpublished" ]' "rc=$rc $(errs | tail -4)"
check "UP-10 контейнер есть и заменяется намеренно — «пересоздаю», а не «контейнера нет»" \
    'grep -qF "$(tf amnezia_replacing awg2 amnezia-awg2)" "$ERR" && ! grep -qF "$(tf amnezia_recreating awg2 amnezia-awg2)" "$ERR"' "$(errs | head -4)"
touch "$lc20_cfs/amnezia-awg2/.unpublished"
GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run amnezia_profile_restart awg2
check "LC-20 «Перезапустить» не останавливается на docker restart: пересоздаёт из копии состояния" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart amnezia-awg2" "$ERR" && grep -q "DRY: docker run .*amnezia-awg2" "$ERR" && [ ! -f "$lc20_cfs/amnezia-awg2/.unpublished" ]' "rc=$rc $(errs | tail -4)"
touch "$lc20_cfs/amnezia-awg2/.unpublished"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
GA_AMN_WATCH_RECHECK_SECONDS=0 GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run amnezia_watch_tick
check "LC-20 сторож видит неопубликованный порт и пересоздаёт контейнер: action=recreated, ok=true, событие боту" \
    '[ "$(jq -r ".profiles[]|select(.profile==\"awg2\")|.action" <<<"$out")" = recreated ] && [ "$(jq -r ".profiles[]|select(.profile==\"awg2\")|.ok" <<<"$out")" = true ] && [ -n "$(grep -l "\"action\":\"recreated\"" "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' \
    "$out $(errs | tail -3)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
touch "$lc20_cfs/amnezia-awg2/.iface_down"
GA_AMN_WATCH_RECHECK_SECONDS=0 GOAMNEZIA_CONTAINER_FS_DIR="$lc20_cfs" run amnezia_watch_tick
check "SR-38 сторож переспрашивает упавший интерфейс, прежде чем рвать связь (перезапуск после второго отказа)" \
    '[ "$(jq -r ".profiles[]|select(.profile==\"awg2\")|.action" <<<"$out")" = restarted ]' "$out"
rm -rf "$lc20_cfs" "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
check "SR-38 переспрос — функцией с паузой, а не одним вызовом" \
    'grep -q "ga_amn_iface_alive \"\$p\" && { _ga_amn_watch_pause; ! ga_amn_iface_alive \"\$p\"; }" "$ROOT/lib/amnezia.sh"'

# ── SR-30: docker stop прошёл, docker rm — нет ─────────────────────────────────────────────────────
# Контейнер остановлен, а ответ был «образ и скрипты откачены, вмешательства не нужно». Теперь прежний
# запускается снова; не поднялся — снос засчитан, и вызывающий назовёт контейнер и способ его поднять.
section "SR-30: обновление ядра, docker rm не прошёл после остановки"
sr30_cfs="$TMP/sr30cfs"; mkdir -p "$sr30_cfs/amnezia-awg2"
_sr30_rm=$(declare -f docker_rm)
docker_rm() { ga_err rm_failed "stend: docker rm отказал"; return 1; }
printf 'exited\n' >"$sr30_cfs/amnezia-awg2/.state"
GOAMNEZIA_CONTAINER_FS_DIR="$sr30_cfs" run amnezia_core_update awg2 "amneziavpn/amneziawg-go:latest" latest ""
check "SR-30 прежний контейнер запущен снова (docker start: стенд снял «exited»), снос не засчитан — откат честный" \
    '[ "$rc" != 0 ] && [ ! -f "$sr30_cfs/amnezia-awg2/.state" ] && [ "$GA_AMN_CORE_RM_DONE" = 0 ]' "rc=$rc rm_done=$GA_AMN_CORE_RM_DONE $(ls -A "$sr30_cfs/amnezia-awg2") $(errs | tail -3)"
_sr30_start=$(declare -f docker_start)
docker_start() { ga_err start_failed "stend: docker start отказал"; return 1; }
printf 'exited\n' >"$sr30_cfs/amnezia-awg2/.state"
GOAMNEZIA_CONTAINER_FS_DIR="$sr30_cfs" run amnezia_core_update awg2 "amneziavpn/amneziawg-go:latest" latest ""
check "SR-30 прежний не поднялся — снос засчитан: вызывающий скажет, что профиль лежит, а не «откачено»" \
    '[ "$rc" != 0 ] && [ "$GA_AMN_CORE_RM_DONE" = 1 ]' "rc=$rc rm_done=$GA_AMN_CORE_RM_DONE"
eval "$_sr30_start"; eval "$_sr30_rm"
rm -rf "$sr30_cfs"

# ── SR-36: зеркала Alpine — запомненное, затем основной CDN, затем прочие ───────────────────────────
section "SR-36: сборка образа — запомненное зеркало умерло, основной CDN жив"
sr36_dir="$TMP/sr36"; mkdir -p "$sr36_dir"; printf 'FROM alpine:3.19\nRUN apk add --no-cache iptables\n' >"$sr36_dir/Dockerfile"
sr36_log="$TMP/sr36.log"; : >"$sr36_log"
_sr36_build=$(declare -f docker_build)
# Подставная сборка: со строкой зеркала — отказ (зеркало умерло), без неё — удача (основной CDN жив).
docker_build() {
    if grep -q goamnezia-apk-mirror "$2/Dockerfile"; then
        grep -o 'https\?://[A-Za-z0-9._/-]*alpine' "$2/Dockerfile" | head -n 1 >>"$sr36_log"
        ga_err build_failed "stend: зеркало не отвечает"; return 1
    fi
    echo CDN >>"$sr36_log"; return 0
}
mkdir -p "$(dirname "$(ga_amn_apk_mirror_file)")"; printf 'https://mirror.example.org/alpine\n' >"$(ga_amn_apk_mirror_file)"
GOAMNEZIA_DRY_RUN=0 run ga_amn_docker_build amnezia-awg9 "$sr36_dir"
check "SR-36 запомненное зеркало не сработало → следом основной CDN (строки зеркала нет), сборка прошла, мёртвое зеркало забыто" \
    '[ "$rc" = 0 ] && [ "$(sed -n 1p "$sr36_log")" = https://mirror.example.org/alpine ] && [ "$(sed -n 2p "$sr36_log")" = CDN ] && [ ! -f "$(ga_amn_apk_mirror_file)" ] && ! grep -q goamnezia-apk-mirror "$sr36_dir/Dockerfile" && [ -z "$GA_LAST_ERROR_CODE" ]' \
    "log=$(tr '\n' ' ' <"$sr36_log") rc=$rc code=$GA_LAST_ERROR_CODE"
eval "$_sr36_build"
rm -rf "$sr36_dir" "$sr36_log"

# ── SR-45: вписанный адрес после переезда и после восстановления ──────────────────────────────────
section "SR-45: вписанный IP — свой снимается при переезде, намеренно чужой остаётся"
sr45_cfg=$(cat "$CONFIG_FILE")
edge_addrs 203.0.113.10 198.51.100.20
PATH="$EDGEBIN:$PATH" run ga_set_endpoint_host 192.0.2.77
check "SR-45 вписан адрес не этой машины — запомнен как намеренно чужой" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .endpoint_host_foreign)" = true ]' "rc=$rc $(ga_config_get .endpoint_host_foreign)"
PATH="$EDGEBIN:$PATH" run ga_set_endpoint_host 198.51.100.20
check "SR-45 вписан второй адрес этой машины — не чужой" '[ "$(ga_config_get .endpoint_host_foreign)" = false ]' "$(ga_config_get .endpoint_host_foreign)"
edge_addrs 45.9.9.9
PATH="$EDGEBIN:$PATH" run ga_export_endpoint_host_gone
check "SR-45 машина переехала (адрес 45.9.9.9): вписанный 198.51.100.20 — пропавший" '[ "$rc" = 0 ]' "rc=$rc"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
GOAMNEZIA_SERVER_IP=45.9.9.9 PATH="$EDGEBIN:$PATH" run amnezia_server_ip_check
check "SR-45 проверка адреса снимает пропавший свой адрес и называет это в событии боту" \
    '[ "$(jq -r .changed <<<"$out")" = true ] && [ "$(ga_config_get .endpoint_host)" = "" ] && [ "$(jq -r .endpoint_host_action <<<"$out")" = dropped ] && [ "$(jq -r .endpoint_host_action "$(grep -l server_ip_changed "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = dropped ]' \
    "$out $(errs | tail -2)"
printf '%s\n' "$sr45_cfg" >"$CONFIG_FILE"
edge_addrs 203.0.113.10 198.51.100.20
PATH="$EDGEBIN:$PATH" run ga_set_endpoint_host 192.0.2.77
edge_addrs 45.9.9.9
PATH="$EDGEBIN:$PATH" run ga_export_endpoint_host_gone
check "SR-45 намеренно чужой адрес (проброс) пропавшим не считается" '[ "$rc" != 0 ]' "rc=$rc"
GOAMNEZIA_SERVER_IP=45.9.9.9 PATH="$EDGEBIN:$PATH" run amnezia_server_ip_check
check "SR-45 при переезде намеренно чужой адрес остаётся, человеку сказано перенаправить проброс" \
    '[ "$(ga_config_get .endpoint_host)" = 192.0.2.77 ] && [ "$(jq -r .endpoint_host_action <<<"$out")" = kept ] && grep -qF "$(tf amnezia_endpoint_pin_foreign_kept 192.0.2.77 45.9.9.9)" "$ERR"' \
    "$out $(errs | tail -2)"
printf '%s\n' "$sr45_cfg" >"$CONFIG_FILE"
# SR-49: адреса сервера в config.json нет (установка без сети) — часовая проверка его заполняет.
ga_config_merge '{"server_ip": ""}' >/dev/null 2>&1
GOAMNEZIA_SERVER_IP=203.0.113.10 PATH="$EDGEBIN:$PATH" run amnezia_server_ip_check
check "SR-49 пустой server_ip заполняется найденным адресом, а не остаётся пустым навсегда" \
    '[ "$(ga_config_get .server_ip)" = 203.0.113.10 ]' "rc=$rc $(ga_config_get .server_ip) $out"
printf '%s\n' "$sr45_cfg" >"$CONFIG_FILE"
if type restore_backup >/dev/null 2>&1; then
    run create_backup ""
    sr45_bk="$out"
    edge_addrs 45.9.9.9
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
    GOAMNEZIA_SERVER_IP=45.9.9.9 PATH="$EDGEBIN:$PATH" run restore_backup "$sr45_bk" "" yes
    check "SR-45 восстановление на новой машине сразу сверяет адрес: server_ip — новый, событие боту" \
        '[ "$rc" = 0 ] && [ "$(ga_config_get .server_ip)" = 45.9.9.9 ] && [ "$(jq -r .changed <<<"$GA_RESTORE_IP_JSON")" = true ] && [ -n "$(grep -l server_ip_changed "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' \
        "rc=$rc ip=$(ga_config_get .server_ip) json=$GA_RESTORE_IP_JSON $(errs | tail -3)"
    printf '%s\n' "$sr45_cfg" >"$CONFIG_FILE"
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
fi
: >"$EDGEBIN/addrs"

# ── SEC46-02: значения ga_config_set и ga_emit_json — не в argv jq ───────────
# argv любого процесса читает каждый локальный пользователь (/proc/<pid>/cmdline). Путь панели — секрет:
# он идёт через ga_config_set (miniapp-on/rotate и откат прежнего значения), а ответ моста с ним — через
# ga_emit_json (path, url и ссылка в тексте сообщения). Шпион jq пишет свои аргументы в журнал.
if [ -z "${GOAMNEZIA_TEST_FORCE_STUBS:-}" ]; then
    section "SEC46-02: ga_config_set и ga_emit_json не кладут значения в argv jq"
    SPY46="$TMP/spy46"; mkdir -p "$SPY46"; : >"$TMP/argv46.log"
    printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexec "%s" "$@"\n' "$TMP/argv46.log" "$(command -v jq)" >"$SPY46/jq"
    chmod +x "$SPY46/jq"
    sec46_path="app-$(printf 'S%.0s' $(seq 1 40))Sec46Path_x-y"
    sec46_cfg=$(cat "$CONFIG_FILE")
    PATH="$SPY46:$PATH" run ga_config_set .panel.miniapp_path "$sec46_path"
    check "SEC46-02 ga_config_set строкой: rc=0, путь в config.json" \
        '[ "$rc" = 0 ] && [ "$(ga_config_get .panel.miniapp_path)" = "$sec46_path" ]' "rc=$rc $(errs)"
    PATH="$SPY46:$PATH" run ga_config_set .site.panel_path "\"$sec46_path\""
    check "SEC46-02 ga_config_set JSON-строкой (так откатывается прежнее значение): строка без кавычек" \
        '[ "$rc" = 0 ] && [ "$(jq -r .site.panel_path "$CONFIG_FILE")" = "$sec46_path" ]' "rc=$rc $(errs)"
    PATH="$SPY46:$PATH" run ga_config_set .options.sec46 false
    check "SEC46-02 ga_config_set false — по-прежнему булево, не строка" '[ "$(jq -c .options.sec46 "$CONFIG_FILE")" = false ]'
    PATH="$SPY46:$PATH" run ga_emit_json success "Панель: https://example.com/$sec46_path/" "path=$sec46_path" \
        "url=https://example.com/$sec46_path/" "enabled=@json:true" "n=@json:[1,2]" "label=@str:@json:true" \
        "bad=@json:{oops" "empty=@json:"
    sec46_want=$(jq -cn --arg p "$sec46_path" '{status: "success", message: ("Панель: https://example.com/" + $p + "/"),
        path: $p, url: ("https://example.com/" + $p + "/"), enabled: true, n: [1, 2], label: "@json:true", bad: "{oops", empty: ""}')
    check "SEC46-02 ga_emit_json: ответ прежний — строки, @json:, @str:, не-JSON строкой" \
        '[ "$rc" = 0 ] && [ "$(wc -l <"$OUT" | tr -d " ")" = 1 ] && [ "$(jq -c . <<<"$out")" = "$sec46_want" ]' "$out"
    check "SEC46-02 шпион сработал, а путь панели ни разу не прошёл через argv jq" \
        '[ "$(wc -l <"$TMP/argv46.log" | tr -d " ")" -ge 4 ] && ! grep -qF -- "$sec46_path" "$TMP/argv46.log"' \
        "$(grep -F -- "$sec46_path" "$TMP/argv46.log" | head -3 | cut -c1-160)"
    check "SEC46-02 переменные окружения ga_emit_json не остаются в оболочке" '[ -z "${GA_EJ_STATUS+x}" ] && [ -z "${GA_EJ_V1+x}" ]'
    printf '%s\n' "$sec46_cfg" >"$CONFIG_FILE"
fi

# ── SEC-NET-1: изоляция клиентов профиля ─────────────────────────────────────
# `FORWARD -i awg0 -j ACCEPT` из start.sh пропускал и awg0→awg0: люди одного профиля доставали через сервер до
# устройств друг друга. Правило изоляции — первым в FORWARD контейнера; выход в интернет и NAT не трогаются.
# Порядок и идемпотентность — на поддельном iptables (tests/fixtures/fake_iptables.sh): цепочки в файлах,
# -C/-A/-I/-D/-S — как у настоящего.
if [ -z "${GOAMNEZIA_TEST_FORCE_STUBS:-}" ]; then
    section "SEC-NET-1: изоляция клиентов профиля"
    ga_profile_installed awg2 >/dev/null 2>&1 || run amnezia_profile_install awg2 40000 default
    IPTBIN="$TMP/iptbin"; mkdir -p "$IPTBIN"; ln -sf "$ROOT/tests/fixtures/fake_iptables.sh" "$IPTBIN/iptables"
    netns() {   # netns <каталог таблиц> <команды sh> — «сетевое пространство контейнера» на поддельном iptables
        FAKE_IPT_DIR="$1" PATH="$IPTBIN:$PATH" sh -c "$2"
    }
    iso_line="$(ga_amn_isolation_cmd awg0 on) # goamnezia-client-isolation"
    iso_on_exec="docker exec -i amnezia-awg2 sh -c '$(ga_amn_isolation_cmd awg0 on)'"
    iso_off_exec="docker exec -i amnezia-awg2 sh -c '$(ga_amn_isolation_cmd awg0 off)'"
    check "SEC-NET-1 команда изоляции: проверка, иначе вставка ПЕРВЫМ правилом FORWARD; интерфейс — только awgN/wgN" \
        '[ "$(ga_amn_isolation_cmd awg0 on)" = "iptables -C FORWARD -i awg0 -o awg0 -j DROP 2>/dev/null || iptables -I FORWARD 1 -i awg0 -o awg0 -j DROP" ] && ! ga_amn_isolation_cmd "awg0;reboot" on >/dev/null && ! ga_amn_isolation_cmd "" on >/dev/null'
    ss_on=$(ga_amn_render_start_sh awg2)
    check "SEC-NET-1 start.sh своего профиля: строка изоляции одна и стоит перед первым правилом iptables" \
        '[ "$(grep -cF "# goamnezia-client-isolation" <<<"$ss_on")" = 1 ] && [ "$(grep -m1 "^iptables " <<<"$ss_on")" = "$iso_line" ]' \
        "$(grep -n "^iptables\|isolation" <<<"$ss_on" | head -4)"
    ga_config_set .client_isolation false >/dev/null 2>&1
    ss_off=$(ga_amn_render_start_sh awg2)
    ga_config_set .client_isolation true >/dev/null 2>&1
    check "SEC-NET-1 client_isolation=false: в start.sh строки нет, всё остальное — байт в байт как с ней" \
        '! grep -qF "goamnezia-client-isolation" <<<"$ss_off" && [ "$(grep -vF "# goamnezia-client-isolation" <<<"$ss_on")" = "$ss_off" ]'
    check "SEC-NET-1 правка start.sh идемпотентна: повторная вставка и снятие там, где строки нет, ничего не меняют" \
        '[ "$(ga_amn_start_sh_isolation "$ss_on" awg0 on)" = "$ss_on" ] && [ "$(ga_amn_start_sh_isolation "$ss_off" awg0 off)" = "$ss_off" ] && [ "$(ga_amn_start_sh_isolation "$ss_on" awg0 off)" = "$ss_off" ]'
    # Правила start.sh на поддельном iptables — дважды, как при повторном старте: изоляция первой и одна, выход
    # в интернет и NAT на месте, дублей нет.
    IPT1="$TMP/ipt1"; mkdir -p "$IPT1"
    ss_rules=$(grep "^iptables " <<<"$ss_on")
    netns "$IPT1" "$ss_rules"; netns "$IPT1" "$ss_rules"
    fwd1=$(FAKE_IPT_DIR="$IPT1" "$IPTBIN/iptables" -S FORWARD)
    check "SEC-NET-1 после start.sh: DROP awg0→awg0 — первое правило FORWARD и одно; выход в интернет и NAT на месте, без дублей" \
        '[ "$(head -n 1 <<<"$fwd1")" = "-A FORWARD -i awg0 -o awg0 -j DROP" ] && [ "$(grep -c -- "-o awg0 -j DROP" <<<"$fwd1")" = 1 ] && grep -q -- "-A FORWARD -i awg0 -o eth0 -s " <<<"$fwd1" && [ "$(grep -c MASQUERADE "$IPT1/nat")" = 2 ] && [ -z "$(sort "$IPT1/filter" | uniq -d)" ]' "$fwd1"
    # Контейнер приложения: его start.sh ставит свои разрешения (-A, без нашей строки) — правило докладывается
    # командой; повтор ничего не меняет; правила приложения и NAT не тронуты. legacy-контейнер — на wg0.
    for iso_if in awg0 wg0; do
        IPT2="$TMP/ipt2-$iso_if"; mkdir -p "$IPT2"
        netns "$IPT2" "iptables -A INPUT -i $iso_if -j ACCEPT; iptables -A FORWARD -i $iso_if -j ACCEPT; iptables -A OUTPUT -o $iso_if -j ACCEPT; iptables -A FORWARD -i $iso_if -o eth0 -s 10.8.1.0/24 -j ACCEPT; iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT; iptables -t nat -A POSTROUTING -s 10.8.1.0/24 -o eth0 -j MASQUERADE"
        app_rules=$(cat "$IPT2/filter"); app_nat=$(cat "$IPT2/nat")
        netns "$IPT2" "$(ga_amn_isolation_cmd "$iso_if" on)"; netns "$IPT2" "$(ga_amn_isolation_cmd "$iso_if" on)"
        check "SEC-NET-1 контейнер приложения ($iso_if): изоляция первой в FORWARD и одна после повтора; правила приложения и NAT как были" \
            '[ "$(FAKE_IPT_DIR="$IPT2" "$IPTBIN/iptables" -S FORWARD | head -n 1)" = "-A FORWARD -i $iso_if -o $iso_if -j DROP" ] && [ "$(grep -c -- "-o $iso_if -j DROP" "$IPT2/filter")" = 1 ] && [ "$(grep -v -- "-o $iso_if -j DROP" "$IPT2/filter")" = "$app_rules" ] && [ "$(cat "$IPT2/nat")" = "$app_nat" ]' \
            "$(cat "$IPT2/filter")"
        netns "$IPT2" "iptables -I FORWARD 3 -i $iso_if -o $iso_if -j DROP"
        netns "$IPT2" "$(ga_amn_isolation_cmd "$iso_if" off)"
        check "SEC-NET-1 client_isolation=false ($iso_if): сняты все копии правила, правила приложения и NAT не тронуты" \
            '! grep -q -- "-o $iso_if -j DROP" "$IPT2/filter" && [ "$(cat "$IPT2/filter")" = "$app_rules" ] && [ "$(cat "$IPT2/nat")" = "$app_nat" ]' \
            "$(cat "$IPT2/filter")"
    done
    # Применение (dry-run): своему профилю — строка в start.sh (копия в state/ и контейнер) и команда в контейнере.
    SD_ISO="$STATE_DIR/$(ga_profile_field awg2 container)"
    grep -vF "# goamnezia-client-isolation" "$SD_ISO/start.sh" >"$SD_ISO/start.sh.tmp" && mv "$SD_ISO/start.sh.tmp" "$SD_ISO/start.sh"
    run ga_amn_isolation_apply awg2
    check "SEC-NET-1 start.sh прежней версии (без строки) получает её и уходит в контейнер; правило — командой в контейнере" \
        '[ "$rc" = 0 ] && [ "$(grep -cF "# goamnezia-client-isolation" "$SD_ISO/start.sh")" = 1 ] && grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/start.sh" "$ERR" && grep -qF "$iso_on_exec" "$ERR"' "$(errs)"
    run ga_amn_isolation_apply awg2
    check "SEC-NET-1 повтор: start.sh уже со строкой — в контейнер не пишется, команда та же" \
        '[ "$rc" = 0 ] && ! grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/start.sh" "$ERR" && grep -qF "$iso_on_exec" "$ERR"' "$(errs)"
    run amnezia_profile_restart awg2
    check "SEC-NET-1 перезапуск профиля: правило ставится сразу после подъёма интерфейса" \
        '[ "$rc" = 0 ] && grep -qF "$iso_on_exec" "$ERR"' "$(errs | tail -4)"
    # Сторож: контейнер-фикстура awg2 «работает» и здоров — правило докладывается в него каждым тиком.
    iso_cfs="$TMP/isocfs"; mkdir -p "$iso_cfs/amnezia-awg2"
    GA_AMN_WATCH_RECHECK_SECONDS=0 GOAMNEZIA_CONTAINER_FS_DIR="$iso_cfs" run amnezia_watch_tick
    check "SEC-NET-1 тик сторожа докладывает правило в работающий здоровый контейнер" \
        '[ "$rc" = 0 ] && [ "$(jq -r ".profiles[] | select(.profile == \"awg2\") | .action" <<<"$out")" = none ] && grep -qF "$iso_on_exec" "$ERR"' "$(errs | tail -4)"
    run amnezia_profile_status awg2
    check "SEC-NET-1 карточка профиля: client_isolation=true (только показ)" '[ "$(jq -r .client_isolation <<<"$out")" = true ]' "$out"
    ga_config_set .client_isolation false >/dev/null 2>&1
    run ga_amn_isolation_apply awg2
    check "SEC-NET-1 client_isolation=false: строка уходит из start.sh своего профиля, в контейнере правило снимается" \
        '[ "$rc" = 0 ] && ! grep -qF "goamnezia-client-isolation" "$SD_ISO/start.sh" && grep -qF "$iso_off_exec" "$ERR"' "$(errs)"
    run amnezia_profile_status awg2
    check "SEC-NET-1 карточка профиля: client_isolation=false" '[ "$(jq -r .client_isolation <<<"$out")" = false ]' "$out"
    ga_config_set .client_isolation true >/dev/null 2>&1
    run ga_amn_isolation_apply awg2
    check "SEC-NET-1 снова включено: строка вернулась, одна" '[ "$(grep -cF "# goamnezia-client-isolation" "$SD_ISO/start.sh")" = 1 ]'
fi

# ── NC-25: снят ли контейнер — по отсутствию КОНТЕЙНЕРА, а не голым `docker inspect` ──────────────────────
# «Удалить всё» спрашивало `docker inspect amnezia-awg3`, а тот находил одноимённый ОБРАЗ профиля (docker build
# -t amnezia-awg3): снос обрывался «контейнер не снялся» при пустом docker ps -a. Здесь — настоящая ветка (не
# стендовая) против подставного docker: образ amnezia-awg3 есть, контейнера нет; контейнер amnezia-awg2 есть.
if type docker_container_gone >/dev/null 2>&1; then
    section "NC-25: docker_container_gone — образ с именем контейнера не мешает"
    mkdir -p "$TMP/nc25bin"
    cat >"$TMP/nc25bin/docker" <<'DK'
#!/bin/bash
# Подставной docker: образ amnezia-awg3 и контейнер amnezia-awg2. NC25_DOWN=1 — демон не отвечает.
name="${!#}"
if [ -n "${NC25_DOWN:-}" ]; then echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2; exit 1; fi
if [ "$1" = container ] && [ "$2" = inspect ]; then
    [ "$name" = amnezia-awg2 ] && { echo 0123abcd; exit 0; }
    echo "Error response from daemon: No such container: $name" >&2; exit 1
fi
# Голый inspect ищет по всем типам и находит образ amnezia-awg3 — ровно так ошибался снос
[ "$1" = inspect ] && case "$name" in amnezia-awg2|amnezia-awg3) echo '[{}]'; exit 0 ;; esac
exit 1
DK
    chmod +x "$TMP/nc25bin/docker"
    nc25() { ( export GOAMNEZIA_DRY_RUN=0 DOCKER_API_VERSION=1.43 PATH="$TMP/nc25bin:$PATH"; docker_container_gone "$@" ); }
    check "NC-25 контейнера нет, а образ с его именем есть — «снят» (голый inspect сказал бы «не снялся»)" \
        'nc25 amnezia-awg3 && ( PATH="$TMP/nc25bin:$PATH"; docker inspect amnezia-awg3 >/dev/null )'
    check "NC-25 контейнер есть — «не снят»" '! nc25 amnezia-awg2'
    check "NC-25 демон не отвечает — не «снят»: про контейнер ничего не известно" '! NC25_DOWN=1 nc25 amnezia-awg3'
    nc25_src() { sed -n "/^$1() {/,/^}/p" "$2"; }
    check "NC-25 «Удалить всё» и docker_rm судят по docker_container_gone, голого docker inspect на контейнер нет" \
        '! nc25_src _wipe_everything "$ROOT/install.sh" | grep -qF "docker inspect \"\$c\"" && nc25_src _wipe_everything "$ROOT/install.sh" | grep -q docker_container_gone && nc25_src docker_rm "$ROOT/lib/docker.sh" | grep -q docker_container_gone'
    unset -f nc25 nc25_src
else
    skipped "NC-25 docker_container_gone" "нет функции в lib/docker.sh"
fi

# ── NC-11: время на экране — сначала «через 5 минут» / «2 ч назад», часы сервера — в скобках с пометкой ──────
# Раньше ga_human_ts печатал «2026-09-27 23:06 UTC»: часы сервера обычно идут по UTC, и человек в Москве читал это как
# «ещё три часа». Срок страницы, бэкап, сертификат и срок пользователя идут через эту функцию.
section "NC-11: ga_human_ts — относительное время и часы сервера"
nc11_iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }
nc11_clock() { date -d "@$1" '+%Y-%m-%d %H:%M' 2>/dev/null || date -r "$1" '+%Y-%m-%d %H:%M'; }
nc11_now=$(date +%s)
nc11_e=$((nc11_now + 300))
check "NC-11 через 5 минут: «через 5 минут (<часы сервера> по времени сервера)», без UTC" \
    '[ "$(ga_human_ts "$(nc11_iso "$nc11_e")")" = "через 5 минут ($(nc11_clock "$nc11_e") по времени сервера)" ]' \
    "$(ga_human_ts "$(nc11_iso "$nc11_e")")"
nc11_e=$((nc11_now + 86400))
check "NC-11 через сутки: «через 24 ч (…)»" \
    '[ "$(ga_human_ts "$(nc11_iso "$nc11_e")")" = "через 24 ч ($(nc11_clock "$nc11_e") по времени сервера)" ]' \
    "$(ga_human_ts "$(nc11_iso "$nc11_e")")"
nc11_e=$((nc11_now - 7200))
check "NC-11 два часа назад: «2 ч назад (…)» — у бэкапа и последнего визита" \
    '[ "$(ga_human_ts "$(nc11_iso "$nc11_e")")" = "2 ч назад ($(nc11_clock "$nc11_e") по времени сервера)" ]' \
    "$(ga_human_ts "$(nc11_iso "$nc11_e")")"
nc11_e=$((nc11_now + 30 * 86400))
check "NC-11 через месяц: «через 30 дн. (…)»; и без секунд, и с долями секунды" \
    '[ "$(ga_human_ts "$(nc11_iso "$nc11_e")")" = "через 30 дн. ($(nc11_clock "$nc11_e") по времени сервера)" ] && [[ "$(ga_human_ts "$(nc11_iso "$nc11_e" | cut -c1-16)")" == "через 30 дн. ("* ]] && [[ "$(ga_human_ts "$(nc11_iso "$nc11_e" | sed "s/Z$/.123Z/")")" == "через 30 дн. ("* ]]' \
    "$(ga_human_ts "$(nc11_iso "$nc11_e")")"
check "NC-11 минуты склоняются: 1 минуту, 2 минуты, 5 минут, 11 минут, 21 минуту" \
    '[ "$(ga_minutes 1)|$(ga_minutes 2)|$(ga_minutes 5)|$(ga_minutes 11)|$(ga_minutes 21)" = "1 минуту|2 минуты|5 минут|11 минут|21 минуту" ]' \
    "$(ga_minutes 1)|$(ga_minutes 2)|$(ga_minutes 5)|$(ga_minutes 11)|$(ga_minutes 21)"
check "NC-11 не дата — как есть; пусто — прочерк" '[ "$(ga_human_ts бессрочно)" = бессрочно ] && [ "$(ga_human_ts "")" = - ]'
unset -f nc11_iso nc11_clock

# TAIL-12: место на диске — через ga_df_mb. С фикстурой GOAMNEZIA_DF_FIXTURE (сохранённый df -Pm) диск машины прогона
# не спрашивается вовсе: df-ловушка ниже не зовётся; без фикстуры — df, как прежде (его подменяют проверки «диск полон»).
section "TAIL-12: место на диске из фикстуры, а не с машины прогона"
printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/x 20000 19900 100 100%% /\n' >"$TMP/df.full"
printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/x 20000 4000 16000 20%% /\n' >"$TMP/df.roomy"
_t12_df=$(declare -f df 2>/dev/null)
: >"$TMP/df.calls"
df() { echo "df $*" >>"$TMP/df.calls"; printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/y 1000 1 999 1%% /\n'; }
check "TAIL-12 фикстура «свободно 100 МБ»: check_disk_space 512 → low_disk, ga_disk_json — critical, 100 МБ; df не зван" \
    '! GOAMNEZIA_DF_FIXTURE="$TMP/df.full" check_disk_space 512 "$TMP" 2>/dev/null && [ "$(GOAMNEZIA_DF_FIXTURE="$TMP/df.full" ga_disk_json "$TMP" | jq -r "[.level, .free_mb, .total_mb, .used_pct] | join(\" \")")" = "critical 100 20000 100" ] && [ ! -s "$TMP/df.calls" ]' \
    "$(GOAMNEZIA_DF_FIXTURE="$TMP/df.full" ga_disk_json "$TMP") $(cat "$TMP/df.calls")"
check "TAIL-12 фикстура «свободно 16000 МБ»: порог 1024 пройден — при любом месте на машине прогона; df не зван" \
    'GOAMNEZIA_DF_FIXTURE="$TMP/df.roomy" check_disk_space 1024 && [ "$(GOAMNEZIA_DF_FIXTURE="$TMP/df.roomy" ga_df_mb /)" = "20000 4000 16000 20%" ] && [ ! -s "$TMP/df.calls" ]' \
    "$(GOAMNEZIA_DF_FIXTURE="$TMP/df.roomy" ga_df_mb /) $(cat "$TMP/df.calls")"
check "TAIL-12 без фикстуры — df (-Pm, раздел каталога), как прежде" \
    '[ "$(unset GOAMNEZIA_DF_FIXTURE; ga_df_mb "$TMP")" = "1000 1 999 1%" ] && grep -qxF "df -Pm $TMP" "$TMP/df.calls"' "$(cat "$TMP/df.calls")"
unset -f df
[ -n "$_t12_df" ] && eval "$_t12_df"

DONE=1
section "apt_install: свежий сервер без списков пакетов (живой прогон владельца 28.09)"
# Подставной apt-get: install не находит пакет, пока не было update, — как на только что созданном сервере.
apt_stub_dir=$(mktemp -d "${TMPDIR:-/tmp}/ga_aptstub.XXXXXX")
cat >"$apt_stub_dir/apt-get" <<'STUB'
#!/usr/bin/env bash
log="${APT_STUB_LOG:?}"
case " $* " in
    *" update "*)  echo update >>"$log"; : >"$log.updated"; exit 0 ;;
    *" install "*) echo install >>"$log"
                   [ -f "$log.updated" ] && [ -z "${APT_STUB_ALWAYS_FAIL:-}" ] && exit 0
                   echo "E: Unable to locate package docker.io" >&2; exit 100 ;;
esac
exit 0
STUB
chmod +x "$apt_stub_dir/apt-get"
export APT_STUB_LOG="$apt_stub_dir/log"
apt_rc=0; PATH="$apt_stub_dir:$PATH" apt_install docker.io containerd >/dev/null 2>&1 || apt_rc=$?
check "первая попытка не нашла пакет → списки обновлены → вторая попытка прошла" \
    '[ "$apt_rc" = 0 ] && [ "$(tr "\n" " " <"$APT_STUB_LOG")" = "install update install " ]' "rc=$apt_rc; $(tr '\n' ' ' <"$APT_STUB_LOG")"
rm -f "$APT_STUB_LOG" "$APT_STUB_LOG.updated"
apt_rc=0; apt_err=$(APT_STUB_ALWAYS_FAIL=1 PATH="$apt_stub_dir:$PATH" apt_install docker.io 2>&1 >/dev/null) || apt_rc=$?
check "пакета правда нет — ошибка с хвостом apt, без третьей попытки" \
    '[ "$apt_rc" != 0 ] && grep -q "Unable to locate package" <<<"$apt_err" && [ "$(grep -c install "$APT_STUB_LOG")" = 2 ]' "rc=$apt_rc"
check "/dev/null цел" '[ -c /dev/null ]'
rm -rf "$apt_stub_dir"; unset APT_STUB_LOG apt_rc apt_err apt_stub_dir

printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
