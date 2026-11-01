#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — соседи профиля без сервера (GOAMNEZIA_DRY_RUN=1): goWARP PRO и контейнеры в сети профиля
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_neighbours_dryrun.sh
#
# Жалоба 29.09.2026 (goAmnezia и goWARP на одном сервере): warp-socks остался привязан к удалённому amnezia-awg2 и
# прокси на 1080 не работал; из awg0.conf пропала строка MTU = 1360 (интерфейс 1420, в настройках 1360).
#
#   WRP-01 подставной сосед warp-socks-test (NetworkMode=container:<ID amnezia-awg2>): смена порта профиля
#          (пересоздание) → пересоздан из своего docker inspect в сети нового контейнера с той же конфигурацией и без
#          полей, которые Docker запрещает в чужой сети; docker restart профиля (тот же ID, новое пространство) →
#          перезапущен; повтор — ничего; чужой сосед не тронут; служба изоляции (событие старта) и сторож (тик) делают
#          то же; остановленный с ошибкой входа в пропавшую сеть — пересоздан и запущен, остановленный руками —
#          пересоздан, но не запущен; обновление ядра — тоже пересоздание; удаление профиля называет соседей.
#          goWARP: /opt/warp и /opt/warp-tools из копии состояния — обратно в контейнер (инструменты — исполняемыми),
#          блок WARP-MANAGER переезжает в start.sh нового релиза, после подъёма — его сторож gowarp-awg-watch --once.
#   WRP-02 MTU 1360: строка в awg0.conf и ip link set на живом интерфейсе; переживает пересборку конфига (новый
#          пользователь), смену порта, profile-regen и обновление ядра; MTU в настройках нет — строка живого конфига
#          сохраняется; убрали MTU из настроек — строка уходит; миграция приводит копию конфига к настройке один раз.
#   WRP-05 копия состояния снята до установки goWARP (или со старым блоком): «Поднять заново» у работающего профиля,
#          пересоздание из копии и обновление ядра остановленного контейнера берут блок WARP-MANAGER и /opt/warp из
#          самого контейнера до записи копии обратно; блока нет или он битый, файл не читается — копия как есть.
#   WRP-04 goWARP ставят после goAmnezia: имена наших контейнеров под маской goWARP ^amnezia-awg[0-9]*$; с деревом
#          goWARP рядом (GOWARP_SH=<путь к warp.sh>) — что маска и запасной путь к awg0.conf в его коде прежние.
#   WRP-06 «Удалить всё» (настоящий _wipe_everything) → реестр прежних ID — в хранилище вне каталога продукта
#          (GOAMNEZIA_NETDEPS_STASH_DIR), снос обещает возврат соседей → новая установка профиля пересоздаёт
#          warp-socks-test в сети нового контейнера, хранилище снимается; висящий сосед без записи не тронут, о нём —
#          в конце установки, в «Диагностике» (поле quality и строка экрана) и сторожем один раз (журнал и журнал действий);
#          goWARP стоит, а в новом контейнере /opt/warp нет — строка «откройте goWARP, пункт 1».
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-neigh.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
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
export LIB_DIR="$ROOT/lib"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
export GOAMNEZIA_DOCKER_NETDEPS_FIXTURE="$TMP/netdeps.tsv"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"
export GA_AMN_WATCH_RECHECK_SECONDS=0
# Сторож goWARP — подставной: сухой прогон его только называет («DRY: … --once»), но зовёт лишь исполняемый файл.
export GOAMNEZIA_GOWARP_WATCH="$TMP/bin/gowarp-awg-watch"
unset DOCKER_HOST DOCKER_CONTEXT GOAMNEZIA_AWG_SHOW_FIXTURE
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR" "$GOAMNEZIA_CONTAINER_FS_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$TMP/bin"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
printf '#!/bin/sh\nexit 0\n' >"$GOAMNEZIA_GOWARP_WATCH"; chmod +x "$GOAMNEZIA_GOWARP_WATCH"
# systemctl машины прогону не нужен: подставной пишет вызов и отвечает отказом (как машина без systemd).
printf '#!/bin/sh\nexit 1\n' >"$TMP/bin/systemctl"; chmod +x "$TMP/bin/systemctl"
export PATH="$TMP/bin:$PATH"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f) — у GNU «-f» печатает сведения о файловой системе.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
audit_has() {
    jq -R -e -n --arg a "$1" --arg d "${2:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ((.details // "") | startswith($d)))] | length > 0' \
        "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}
# dep <имя> <поле 2..5> — поле строки соседа в фикстуре (2 ID, 3 NetworkMode, 4 running, 5 пространство).
dep() { awk -F'\t' -v n="$1" -v f="$2" '$1 == n { print $f }' "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"; }
hostid() { tr -d '[:space:]' <"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.id"; }
hostns() { tr -d '[:space:]' <"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.netns"; }
# bump_host — «docker restart руками»: у хозяина новое сетевое пространство, ID тот же.
bump_host() { printf 'net:[%s]\n' "$1" >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.netns"; }

section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
run amnezia_profile_install awg2 40000 default
check "профиль awg2 поставлен (dry)" '[ "$rc" = 0 ]' "$(errs | tail -5)"
SD2="$STATE_DIR/amnezia-awg2"
CFS="$GOAMNEZIA_CONTAINER_FS_DIR"
check "WRP-02 свежая установка: MTU профиля (1280 по умолчанию) — строкой в awg0.conf сервера" \
    'grep -q "^MTU = 1280$" "$SD2/awg0.conf" && [ "$(ga_config_get .profiles.awg2.params.MTU)" = 1280 ]' "$(cat "$SD2/awg0.conf")"

# Контейнер профиля «работает» (фикстура), у него ID и сетевое пространство. goWARP стоит: /opt/warp в контейнере.
mkdir -p "$CFS/amnezia-awg2/opt/warp" "$CFS/warp-socks-test" "$CFS/socks-stopped" "$CFS/socks-off"
ID0=$(_ga_hex64 "amnezia-awg2-first")
printf '%s\n' "$ID0" >"$CFS/amnezia-awg2/.id"
printf 'net:[4026531900]\n' >"$CFS/amnezia-awg2/.netns"
printf '[Interface]\nPrivateKey = x\nAddress = 172.16.0.2/32\nMTU = 1280\nTable = off\n' >"$CFS/amnezia-awg2/opt/warp/warp.conf"
OTHER=$(_ga_hex64 "someone-else")
printf 'warp-socks-test\t%s\tcontainer:%s\ttrue\tnet:[4026531900]\nother-dep\t%s\tcontainer:%s\ttrue\tnet:[4026539999]\n' \
    "$(_ga_hex64 dep1)" "$ID0" "$(_ga_hex64 dep2)" "$OTHER" >"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
# docker inspect соседа — как у настоящего `docker run -d --name warp-socks-test --network container:amnezia-awg2 …`:
# имя узла и открытые порты Docker дописал сам (в чужой сети он их запрещает), том без имени — от VOLUME образа.
cat >"$CFS/warp-socks-test/.inspect.json" <<EOF
[{"Id": "$(_ga_hex64 dep1)", "Name": "/warp-socks-test", "Image": "sha256:$(_ga_hex64 img)",
  "State": {"Status": "running", "Running": true, "Pid": 4242, "Error": ""},
  "Config": {"Hostname": "6a65fb328b0c", "Domainname": "", "User": "", "ExposedPorts": {"1080/tcp": {}},
             "Env": ["SOCKS_PORT=1080", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
             "Cmd": ["sh", "-c", "exec socks --port 1080"], "Image": "ga-socks:1", "Entrypoint": null,
             "Labels": {"role": "warp-socks"}, "MacAddress": "02:42:ac:11:00:02"},
  "HostConfig": {"NetworkMode": "container:$ID0", "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
                 "Binds": ["/srv/socks:/etc/socks:ro"], "PortBindings": {}, "Dns": [], "DnsOptions": [], "DnsSearch": [],
                 "ExtraHosts": null, "Links": null, "PublishAllPorts": false, "CapAdd": ["NET_ADMIN"], "Mounts": null},
  "Mounts": [{"Type": "bind", "Source": "/srv/socks", "Destination": "/etc/socks", "RW": false},
             {"Type": "volume", "Name": "0f1e2d3c4b5a", "Source": "/var/lib/docker/volumes/0f1e2d3c4b5a/_data",
              "Destination": "/data", "RW": true}]}]
EOF
printf 'ok\n' >"$TMP/ok"

# ── WRP-02: MTU 1360 в настройках → конфиг сервера и живой интерфейс ─────────
section "WRP-02 MTU из настроек — в awg0.conf и на интерфейс"
run amnezia_profile_set_mtu awg2 1360
check "profile-mtu 1360: rc=0, params.MTU=1360" '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.params.MTU)" = 1360 ]' "$(errs | tail -5)"
check "в awg0.conf (копия state/) — одна строка MTU = 1360" \
    'grep -q "^MTU = 1360$" "$SD2/awg0.conf" && [ "$(grep -c "^MTU" "$SD2/awg0.conf")" = 1 ]' "$(cat "$SD2/awg0.conf")"
check "конфиг ушёл в контейнер и MTU поставлен на живой интерфейс (ip link set dev awg0 mtu 1360), без перезапуска" \
    'grep -q "DRY: docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/awg0.conf" "$ERR" && grep -q "ip link set dev awg0 mtu 1360" "$ERR" && ! grep -q "DRY: docker restart amnezia-awg2" "$ERR"' \
    "$(grep DRY "$ERR" | head -8)"
run users_add anna "Анна"
check "новый пользователь (пересборка конфига сервера): MTU = 1360 на месте, пир добавлен" \
    '[ "$rc" = 0 ] && grep -q "^MTU = 1360$" "$SD2/awg0.conf" && [ "$(grep -c "^\[Peer\]" "$SD2/awg0.conf")" = 1 ]' "$(errs | tail -5)"
run export_conf anna awg2
check "клиентский конфиг anna — тоже MTU = 1360" 'grep -q "^MTU = 1360$" <<<"$out"' "$out"

# ── WRP-01: пересоздание контейнера профиля — смена порта ────────────────────
section "WRP-01 смена порта: сосед warp-socks-test пересоздан в сети нового amnezia-awg2"
: >"$GOAMNEZIA_AUDIT_LOG"
run amnezia_profile_set_port awg2 40010
ID1=$(hostid)
check "profile-port 40010: rc=0, контейнер пересоздан (новый ID)" \
    '[ "$rc" = 0 ] && [ "$ID1" != "$ID0" ] && grep -q "DRY: docker stop amnezia-awg2" "$ERR" && grep -q "DRY: docker run .* -p 40010:40010/udp" "$ERR"' \
    "rc=$rc id0=$ID0 id1=$ID1 $(errs | tail -4)"
check "сосед пересоздан из своего inspect: rename → create в сети amnezia-awg2 → прежний снят → запуск" \
    'grep -q "DRY: docker rename warp-socks-test warp-socks-test-ga-old; POST /containers/create?name=warp-socks-test (NetworkMode container:amnezia-awg2); docker rm -f warp-socks-test-ga-old; docker start warp-socks-test" "$ERR"' \
    "$(grep -i "warp-socks" "$ERR" | head -5)"
check "сосед теперь в сети нового контейнера: ссылка на новый ID, работает, то же пространство" \
    '[ "$(dep warp-socks-test 3)" = "container:$ID1" ] && [ "$(dep warp-socks-test 4)" = true ] && [ "$(dep warp-socks-test 5)" = "$(hostns)" ]' \
    "$(cat "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE")"
BODY="$CFS/warp-socks-test/.create.json"
check "тело создания: NetworkMode=container:amnezia-awg2; имени узла, открытых портов, MAC нет" \
    'jq -e ".HostConfig.NetworkMode == \"container:amnezia-awg2\" and (has(\"Hostname\") | not) and (has(\"ExposedPorts\") | not) and (has(\"MacAddress\") | not)" "$BODY"' \
    "$(cat "$BODY" 2>/dev/null)"
check "тело создания: та же конфигурация — образ по имени, окружение, команда, метки, политика перезапуска, права, bind" \
    'jq -e ".Image == \"ga-socks:1\" and (.Env | index(\"SOCKS_PORT=1080\")) != null and .Cmd == [\"sh\",\"-c\",\"exec socks --port 1080\"] and .Labels.role == \"warp-socks\" and .HostConfig.RestartPolicy.Name == \"unless-stopped\" and .HostConfig.CapAdd == [\"NET_ADMIN\"] and .HostConfig.Binds == [\"/srv/socks:/etc/socks:ro\"]" "$BODY"' \
    "$(cat "$BODY" 2>/dev/null)"
check "тело создания: том без имени (VOLUME образа) — тем же томом в Mounts, bind не задвоен" \
    'jq -e "([.HostConfig.Mounts[] | select(.Type == \"volume\" and .Source == \"0f1e2d3c4b5a\" and .Target == \"/data\" and .ReadOnly == false)] | length == 1) and ([.HostConfig.Mounts[] | select(.Target == \"/etc/socks\")] | length == 0)" "$BODY"' \
    "$(jq -c .HostConfig.Mounts "$BODY" 2>/dev/null)"
check "тело создания: полей, которые Docker запрещает в чужой сети, в HostConfig нет" \
    'jq -e ".HostConfig | (has(\"PortBindings\") or has(\"Dns\") or has(\"ExtraHosts\") or has(\"Links\") or has(\"PublishAllPorts\")) | not" "$BODY"'
check "чужой сосед (сеть другого контейнера) не тронут" \
    '[ "$(dep other-dep 3)" = "container:$OTHER" ] && [ "$(dep other-dep 5)" = "net:[4026539999]" ] && ! grep -q "other-dep" "$ERR"'
check "реестр: прежний ID снят (на него больше никто не ссылается), записан нынешний — соседи в сети нового" \
    '[ "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids")" = "$ID1" ] && [ "$(perm "$STATE_DIR/.netdeps/amnezia-awg2.ids")" = 600 ]' \
    "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1) id0=$ID0 id1=$ID1"
check "журнал: человеку — «пересоздан», в журнал действий — netdep-recreated" \
    'grep -q "Контейнер warp-socks-test был привязан к прежнему amnezia-awg2 — пересоздан" "$ERR" && audit_has netdep-recreated warp-socks-test' "$(errs | grep -i warp | head -3)"
check "goWARP: после подъёма позван его сторож gowarp-awg-watch --once" 'grep -qF "DRY: $GOAMNEZIA_GOWARP_WATCH --once" "$ERR"'
check "goWARP: перед сносом WARP снят из самого контейнера (свежий ключ goWARP, а не копия прошлой синхронизации)" \
    'grep -q "DRY: docker cp amnezia-awg2:/opt/warp/. .*/warp/ ; docker cp amnezia-awg2:/opt/warp-tools/usr" "$ERR"'
check "WRP-02 смена порта: MTU = 1360 в конфиге сервера на месте" 'grep -q "^MTU = 1360$" "$SD2/awg0.conf"'

run ga_amn_neighbours_up awg2
check "повтор: сосед уже в сети — ни перезапуска, ни пересоздания" \
    '! grep -q "docker restart warp-socks-test" "$ERR" && ! grep -q "docker rename warp-socks-test" "$ERR"' "$(grep DRY "$ERR" | head -5)"

# ── WRP-01: перезапуск контейнера профиля — ID тот же, пространство новое ────
section "WRP-01 перезапуск профиля, событие старта и тик сторожа"
run amnezia_profile_restart awg2
check "profile-restart: ID прежний, пространство новое, сосед перезапущен и снова в нём" \
    '[ "$rc" = 0 ] && [ "$(hostid)" = "$ID1" ] && grep -q "DRY: docker restart warp-socks-test" "$ERR" && [ "$(dep warp-socks-test 5)" = "$(hostns)" ] && [ "$(dep warp-socks-test 3)" = "container:$ID1" ]' \
    "$(grep -E "DRY: docker restart|warp-socks" "$ERR" | head -5) fixture=$(cat "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE")"
bump_host 4026535001
run ga_amn_isolation_on_start amnezia-awg2
check "служба изоляции (docker restart руками → событие старта): сосед перезапущен сразу" \
    'grep -q "DRY: docker restart warp-socks-test" "$ERR" && [ "$(dep warp-socks-test 5)" = "net:[4026535001]" ]' "$(errs | tail -5)"
run ga_amn_isolation_on_start warp-socks-test
check "событие старта самого соседа (не профиль) — ничего" '! grep -q "DRY: docker" "$ERR"' "$(errs | head -3)"
bump_host 4026535002
run amnezia_watch_tick
check "сторож: контейнер здоров, сосед в прежнем пространстве — перезапущен тиком" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker restart warp-socks-test" "$ERR" && [ "$(dep warp-socks-test 5)" = "net:[4026535002]" ]' "$(errs | tail -5)"

# ── WRP-01: остановленные соседи с прежним ID ────────────────────────────────
section "WRP-01 остановленные соседи со ссылкой на прежний ID"
OLDX=$(_ga_hex64 "amnezia-awg2-gone")
mkdir -p "$STATE_DIR/.netdeps"; printf '%s\n' "$OLDX" >"$STATE_DIR/.netdeps/amnezia-awg2.ids"
printf 'socks-stopped\t%s\tcontainer:%s\tfalse\t\nsocks-off\t%s\tcontainer:%s\tfalse\t\n' \
    "$(_ga_hex64 dep3)" "$OLDX" "$(_ga_hex64 dep4)" "$OLDX" >>"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
printf '[{"Image": "sha256:1", "State": {"Running": false, "Error": "joining network namespace of container: No such container: %s"}, "Config": {"Image": "ga-socks:1"}, "HostConfig": {"NetworkMode": "container:%s", "RestartPolicy": {"Name": "unless-stopped"}}}]\n' \
    "$OLDX" "$OLDX" >"$CFS/socks-stopped/.inspect.json"
printf '[{"Image": "sha256:1", "State": {"Running": false, "Error": ""}, "Config": {"Image": "ga-socks:1"}, "HostConfig": {"NetworkMode": "container:%s", "RestartPolicy": {"Name": "no"}}}]\n' \
    "$OLDX" >"$CFS/socks-off/.inspect.json"
run ga_amn_neighbours_up awg2
check "упал на входе в пропавшую сеть → пересоздан в сети amnezia-awg2 и запущен" \
    'grep -q "POST /containers/create?name=socks-stopped (NetworkMode container:amnezia-awg2); docker rm -f socks-stopped-ga-old; docker start socks-stopped" "$ERR" && [ "$(dep socks-stopped 4)" = true ] && [ "$(dep socks-stopped 3)" = "container:$ID1" ]' \
    "$(grep socks- "$ERR" | head -4)"
check "остановлен руками (без ошибки, без --restart always) → пересоздан, но не запущен" \
    'grep -q "POST /containers/create?name=socks-off (NetworkMode container:amnezia-awg2); docker rm -f socks-off-ga-old$" "$ERR" && [ "$(dep socks-off 4)" = false ] && [ "$(dep socks-off 3)" = "container:$ID1" ]' \
    "$(grep socks-off "$ERR" | head -3)"
check "реестр: ссылок на прежний ID не осталось — снят, в реестре только нынешний" \
    '[ "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids")" = "$ID1" ]' "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1)"
# Контейнер профиля снесли мимо продукта (docker rm руками, чистка диска) и подняли заново: соседи держат ссылку на ID,
# записанный при прошлом подключении, — «Поднять заново» узнаёт их по нему.
ID1B=$(_ga_hex64 "amnezia-awg2-by-hand")
printf '%s\n' "$ID1B" >"$CFS/amnezia-awg2/.id"; bump_host 4026536001
run ga_amn_neighbours_up awg2
check "контейнер снесли руками и подняли снова: соседи узнаны по записанному ID и пересозданы в сети нового" \
    'grep -q "POST /containers/create?name=warp-socks-test (NetworkMode container:amnezia-awg2)" "$ERR" && [ "$(dep warp-socks-test 3)" = "container:$ID1B" ] && [ "$(dep socks-stopped 3)" = "container:$ID1B" ] && [ "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids")" = "$ID1B" ]' \
    "$(grep -E "warp-socks|socks-" "$ERR" | head -4) reg=$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1)"
printf '%s\n' "$ID1" >"$CFS/amnezia-awg2/.id"
run ga_amn_neighbours_up awg2

# ── goWARP: файлы и блок в start.sh через пересоздание ───────────────────────
section "WRP-01 goWARP: /opt/warp, /opt/warp-tools и блок WARP-MANAGER"
mkdir -p "$SD2/warp" "$SD2/warp-tools/usr/bin"
printf 'x\n' >"$SD2/warp/warp.conf"; printf 'x\n' >"$SD2/warp-tools/usr/bin/wg"; printf 'x\n' >"$SD2/warp-tools/usr/bin/wg-quick"
run amnezia_profile_repair awg2
check "«Поднять заново»: /opt/warp и /opt/warp-tools из копии — в контейнер, инструменты — исполняемыми" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker cp $SD2/warp/. amnezia-awg2:/opt/warp/" "$ERR" && grep -q "DRY: docker cp $SD2/warp-tools/usr amnezia-awg2:/opt/warp-tools/" "$ERR" && grep -q "chmod 755 /opt/warp-tools/usr/bin/\*" "$ERR"' \
    "$(grep -E "warp" "$ERR" | head -6)"
BLOCK=$'# --- WARP-MANAGER BEGIN ---\n(\n  flock -x 9 || exit 1\nif [ -f \x27/opt/warp/warp.conf\x27 ]; then\n  ip link show warp >/dev/null 2>&1 || wg-quick up /opt/warp/warp.conf 9>&-\nfi\n) 9>/tmp/gowarp-runtime.lock\n# --- WARP-MANAGER END ---'
printf '#!/bin/bash\nawg-quick up /opt/amnezia/awg/awg0.conf\niptables -A INPUT -i awg0 -j ACCEPT\n%s\ntail -f /dev/null\n' "$BLOCK" >"$TMP/old_start.sh"
NEWT=$'#!/bin/bash\necho new-release\niptables -A INPUT -i awg0 -j ACCEPT\ntail -f /dev/null'
run ga_amn_start_sh_carry_warp "$NEWT" "$TMP/old_start.sh"
check "блок goWARP переезжает в start.sh нового релиза — перед tail -f /dev/null, один раз" \
    'grep -q "^echo new-release$" <<<"$out" && [ "$(grep -c "WARP-MANAGER BEGIN" <<<"$out")" = 1 ] && [ "$(awk "/WARP-MANAGER END/ {e=NR} /^tail -f/ {t=NR} END {print (e && t == e + 1)}" <<<"$out")" = 1 ] && [ "$(tail -n 1 <<<"$out")" = "tail -f /dev/null" ]' "$out"
run ga_amn_start_sh_carry_warp "$out" "$TMP/old_start.sh"
check "повтор: блок уже есть — не задваивается" '[ "$(grep -c "WARP-MANAGER BEGIN" <<<"$out")" = 1 ]' "$out"
printf '#!/bin/bash\ntail -f /dev/null\n' >"$TMP/plain_start.sh"
run ga_amn_start_sh_carry_warp "$NEWT" "$TMP/plain_start.sh"
check "в прежнем блока нет — новый как есть" '[ "$out" = "$NEWT" ]' "$out"
# Обновление ядра со сменой скриптов (upstream-update: start.sh из нового шаблона) — блок из живого start.sh.
cp "$TMP/old_start.sh" "$SD2/start.sh"
_zero=$(printf '0%.0s' $(seq 1 64))
run amnezia_core_update awg2 "amneziavpn/amneziawg-go@sha256:$_zero" "9.9.9" "sha256:$_zero" template
ID2=$(hostid)
check "обновление ядра (start.sh из шаблона): rc=0, в новом start.sh — строки шаблона и блок goWARP" \
    '[ "$rc" = 0 ] && grep -q "WARP-MANAGER BEGIN" "$SD2/start.sh" && grep -q "Container startup" "$SD2/start.sh" && [ "$(grep -c "WARP-MANAGER BEGIN" "$SD2/start.sh")" = 1 ]' \
    "rc=$rc $(errs | tail -4) $(cat "$SD2/start.sh" 2>/dev/null | head -30)"
check "обновление ядра — тоже пересоздание: сосед пересоздан в сети нового контейнера" \
    '[ "$ID2" != "$ID1" ] && [ "$(dep warp-socks-test 3)" = "container:$ID2" ] && [ "$(dep warp-socks-test 4)" = true ] && [ "$(dep socks-stopped 3)" = "container:$ID2" ]' \
    "$(cat "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE")"
check "WRP-02 обновление ядра: MTU = 1360 в конфиге сервера на месте" 'grep -q "^MTU = 1360$" "$SD2/awg0.conf"'

# ── WRP-05: копия состояния старше goWARP — его части из контейнера ─────────
section "WRP-05 «Поднять заново» и пересоздание: блок goWARP и /opt/warp — из контейнера, не из старой копии"
mkdir -p "$CFS/amnezia-awg2/opt/amnezia"
LIVE="$CFS/amnezia-awg2/opt/amnezia/start.sh"
# same <копия> — start.sh копии совпал с сохранённым; что дописывает подъём сам — строку изоляции клиентов (SEC-NET-1),
# блок MSS и блок движка AmneziaWG (модуль ядра, этап Б), — не считаем.
_ours_strip() {
    awk '/# goamnezia-client-isolation$/ { next }
         /^# --- goamnezia-(mss|engine) BEGIN ---$/ { s = 1 }
         s { if ($0 ~ /^# --- goamnezia-(mss|engine) END ---$/) s = 0; next }
         { print }' "$1"
}
same() { cmp -s <(_ours_strip "$SD2/start.sh") <(_ours_strip "$1"); }
BLOCK2=$'# --- WARP-MANAGER BEGIN ---\n# gowarp-block 2: clients by key\n(\n  flock -x 9 || exit 1\nif [ -f \x27/opt/warp/warp.conf\x27 ]; then\n  ip link show warp >/dev/null 2>&1 || wg-quick up /opt/warp/warp.conf 9>&-\nfi\n) 9>/tmp/gowarp-runtime.lock\n# --- WARP-MANAGER END ---'
BLOCK1=${BLOCK2/gowarp-block 2: clients by key/gowarp-block 1: clients by address}
printf '#!/bin/bash\necho live-copy\n%s\ntail -f /dev/null\n' "$BLOCK2" >"$LIVE"
# Копия снята до установки goWARP: блока в ней нет.
printf '#!/bin/bash\necho state-copy\niptables -A INPUT -i awg0 -j ACCEPT\ntail -f /dev/null\n' >"$SD2/start.sh"
run amnezia_profile_repair awg2
check "работающий профиль, копия до установки goWARP: в копии — блок контейнера, один, перед tail -f; прочее — копии" \
    '[ "$rc" = 0 ] && grep -q "^echo state-copy$" "$SD2/start.sh" && ! grep -q "live-copy" "$SD2/start.sh" && [ "$(grep -c "WARP-MANAGER BEGIN" "$SD2/start.sh")" = 1 ] && grep -q "gowarp-block 2" "$SD2/start.sh" && [ "$(tail -n 1 "$SD2/start.sh")" = "tail -f /dev/null" ] && [ "$(perm "$SD2/start.sh")" = 600 ]' \
    "rc=$rc $(cat "$SD2/start.sh")"
_r=$(grep -nF "docker exec -i amnezia-awg2 cat $GA_START_SH" "$ERR" | head -1 | cut -d: -f1)
_w=$(grep -nF "docker cp <stdin> amnezia-awg2:$GA_START_SH" "$ERR" | head -1 | cut -d: -f1)
check "start.sh контейнера прочитан до записи копии в контейнер" '[ -n "$_r" ] && [ -n "$_w" ] && [ "$_r" -lt "$_w" ]' "r=$_r w=$_w"
_r=$(grep -nF "docker cp amnezia-awg2:$GA_WARP_DIR/. $SD2/warp/" "$ERR" | head -1 | cut -d: -f1)
_w=$(grep -nF "docker cp $SD2/warp/. amnezia-awg2:$GA_WARP_DIR/" "$ERR" | head -1 | cut -d: -f1)
check "/opt/warp и /opt/warp-tools сняты с контейнера до записи копии обратно — ключ WARP не откатывается" \
    '[ -n "$_r" ] && [ -n "$_w" ] && [ "$_r" -lt "$_w" ]' "r=$_r w=$_w"
cp "$SD2/start.sh" "$TMP/copy1"
run amnezia_profile_repair awg2
check "повтор — копия не меняется" '[ "$rc" = 0 ] && same "$TMP/copy1"' "$(diff "$TMP/copy1" "$SD2/start.sh")"
printf '#!/bin/bash\necho state-copy\n%s\ntail -f /dev/null\n' "$BLOCK1" >"$SD2/start.sh"
run amnezia_profile_repair awg2
check "в копии блок прежней версии goWARP — заменён блоком контейнера" \
    '[ "$rc" = 0 ] && grep -q "gowarp-block 2" "$SD2/start.sh" && ! grep -q "gowarp-block 1" "$SD2/start.sh" && [ "$(grep -c "WARP-MANAGER BEGIN" "$SD2/start.sh")" = 1 ] && grep -q "^echo state-copy$" "$SD2/start.sh"' \
    "$(cat "$SD2/start.sh")"
cp "$SD2/start.sh" "$TMP/copy2"
printf '#!/bin/bash\necho live-copy\ntail -f /dev/null\n' >"$LIVE"
run amnezia_profile_repair awg2
check "в контейнере блока нет — копия как есть" '[ "$rc" = 0 ] && same "$TMP/copy2"'
printf '#!/bin/bash\n# --- WARP-MANAGER BEGIN ---\n(\ntail -f /dev/null\n' >"$LIVE"
run amnezia_profile_repair awg2
check "в контейнере блок без метки конца — копия как есть" '[ "$rc" = 0 ] && same "$TMP/copy2"'
printf '#!/bin/bash\necho live-copy\n%s\ntail -f /dev/null\n' "$BLOCK2" >"$LIVE"
printf '#!/bin/bash\necho state-copy\n# --- WARP-MANAGER BEGIN ---\n(\ntail -f /dev/null\n' >"$SD2/start.sh"
cp "$SD2/start.sh" "$TMP/copy3"
run amnezia_profile_repair awg2
check "у копии блок без метки конца — копия как есть: вырезка унесла бы tail -f" '[ "$rc" = 0 ] && same "$TMP/copy3"' \
    "rc=$rc $(diff "$TMP/copy3" "$SD2/start.sh") $(errs | tail -3)"
mv "$LIVE" "$TMP/live.sh"
printf '#!/bin/bash\necho state-copy\ntail -f /dev/null\n' >"$SD2/start.sh"; cp "$SD2/start.sh" "$TMP/copy4"
run amnezia_profile_repair awg2
check "start.sh в контейнере не читается — копия как есть" '[ "$rc" = 0 ] && same "$TMP/copy4"' \
    "rc=$rc $(diff "$TMP/copy4" "$SD2/start.sh") $(errs | tail -3)"
mv "$TMP/live.sh" "$LIVE"
# Пересоздание из копии (сторож, порт не опубликован, восстановление из архива): снимок — до сноса.
run amnezia_state_restore awg2 recreate
check "пересоздание из копии: блок контейнера — в копии до сноса, в новый контейнер уходит с ним" \
    '[ "$rc" = 0 ] && grep -q "gowarp-block 2" "$SD2/start.sh" && grep -q "^echo state-copy$" "$SD2/start.sh"' "rc=$rc $(errs | tail -3)"
# Обновление ядра остановленного контейнера (start.sh из шаблона): синхронизации нет, блок для нового start.sh берётся
# из копии до сноса — копия сначала из контейнера.
printf '#!/bin/bash\necho state-copy\ntail -f /dev/null\n' >"$SD2/start.sh"
printf 'exited\n' >"$CFS/amnezia-awg2/.state"
run amnezia_core_update awg2 "amneziavpn/amneziawg-go@sha256:$_zero" "9.9.9" "sha256:$_zero" template
rm -f "$CFS/amnezia-awg2/.state"
_r=$(grep -nF "docker exec -i amnezia-awg2 cat $GA_START_SH" "$ERR" | head -1 | cut -d: -f1)
_b=$(grep -nF "DRY: docker build -t amnezia-awg2" "$ERR" | head -1 | cut -d: -f1)
check "обновление ядра остановленного контейнера: блок снят с контейнера до сборки и сноса, он в новом start.sh" \
    '[ -n "$_r" ] && [ -n "$_b" ] && [ "$_r" -lt "$_b" ] && grep -q "gowarp-block 2" "$SD2/start.sh" && grep -q "Container startup" "$SD2/start.sh"' \
    "rc=$rc r=$_r b=$_b $(errs | tail -3)"

# ── WRP-02: MTU через перегенерацию и чужая строка MTU ───────────────────────
section "WRP-02 MTU: profile-regen, чужая строка, снятие, миграция"
run amnezia_profile_regen awg2
check "profile-regen (новые параметры маскировки): MTU 1360 остался и в настройках, и в awg0.conf" \
    '[ "$rc" = 0 ] && [ "$(ga_config_get .profiles.awg2.params.MTU)" = 1360 ] && grep -q "^MTU = 1360$" "$SD2/awg0.conf"' "$(errs | tail -4)"
run amnezia_profile_set_param awg2 MTU ""
check "MTU убрали из настроек (profile-set-param MTU \"\") — строка MTU из awg0.conf ушла" \
    '[ "$rc" = 0 ] && ! grep -q "^MTU" "$SD2/awg0.conf"' "$(cat "$SD2/awg0.conf")"
mkdir -p "$CFS/amnezia-awg2/opt/amnezia/awg"
{ printf '[Interface]\nPrivateKey = x\nAddress = 10.8.2.0/24\nListenPort = 40010\nMTU = 1400\n'; } >"$CFS/amnezia-awg2/opt/amnezia/awg/awg0.conf"
run users_add bob Bob awg2
check "MTU в настройках нет, в живом awg0.conf чужая строка MTU = 1400 — пересборка её сохраняет" \
    '[ "$rc" = 0 ] && grep -q "^MTU = 1400$" "$SD2/awg0.conf" && [ -z "$(ga_config_get .profiles.awg2.params.MTU)" ]' "$(errs | tail -4)"
rm -f "$CFS/amnezia-awg2/opt/amnezia/awg/awg0.conf"
ga_config_merge '{"profiles": {"awg2": {"params": {"MTU": "1380"}}}}' >/dev/null
run ga_amn_mtu_sync_all
check "миграция: копия конфига расходится с настройкой — пересобрана, «awg2=1380», MTU на интерфейс" \
    '[ "$rc" = 0 ] && [ "$out" = "awg2=1380" ] && grep -q "^MTU = 1380$" "$SD2/awg0.conf" && grep -q "ip link set dev awg0 mtu 1380" "$ERR"' "out=[$out] $(errs | tail -3)"
run ga_amn_mtu_sync_all
check "миграция повторно — сошлось, ничего не пишет" '[ "$rc" = 0 ] && [ -z "$out" ]' "out=[$out]"

# ── Соседи профиля, пересозданного прежней версией ───────────────────────────
section "WRP-01 соседи в сети контейнера, которого больше нет"
LEG=$(_ga_hex64 "recreated-by-1.0.3")
printf 'legacy-socks\t%s\tcontainer:%s\ttrue\tnet:[4026530001]\n' "$(_ga_hex64 dep5)" "$LEG" >>"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
run docker_netdeps_dangling
check "сосед со ссылкой на пропавший, нигде не записанный ID назван (миграция скажет человеку), свои — нет" \
    '[ "$(printf "%s" "$out" | sort | paste -sd, -)" = "legacy-socks,other-dep" ]' "out=[$out]"
run ga_amn_neighbours_up awg2
check "такого соседа сами не трогаем: чей он, не записано — ни перезапуска, ни пересоздания" \
    '! grep -q "legacy-socks" "$ERR" && [ "$(dep legacy-socks 3)" = "container:$LEG" ]' "$(errs | grep legacy | head -3)"

# ── Удаление профиля: соседи без сети ────────────────────────────────────────
section "WRP-01 удаление профиля"
ID_RM=$(hostid)
run amnezia_profile_remove awg2
check "удаление профиля называет соседей, которые остаются без сети" \
    '[ "$rc" = 0 ] && grep -q "В сети контейнера amnezia-awg2 живут другие контейнеры (warp-socks-test,socks-stopped,socks-off)" "$ERR"' "$(errs | grep -i "сети" | head -3)"
check "реестр: ID снесённого контейнера запомнен — соседи вернутся к профилю, если его поставят снова" \
    'grep -qx "$ID_RM" "$STATE_DIR/.netdeps/amnezia-awg2.ids"' "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1)"

# ── WRP-06: «Удалить всё» → новая установка ──────────────────────────────────
# Живая проверка 03.10.2026: после сноса и новой установки warp-socks остался в сети удалённого amnezia-awg2 — реестр
# прежних ID жил в state/.netdeps и уходил вместе с каталогом продукта, а продукт молчал. Снос — настоящий
# _wipe_everything из install.sh (стендовая ветка: копии в GOAMNEZIA_WIPE_HOME, машину не трогает), хранилище реестра —
# в TMP (GOAMNEZIA_NETDEPS_STASH_DIR вместо /var/lib/goamnezia-netdeps).
section "WRP-06 «Удалить всё» → новая установка: сосед возвращается в сеть нового контейнера сам"
export GOAMNEZIA_NETDEPS_STASH_DIR="$TMP/netdeps-stash"
export GOAMNEZIA_WIPE_HOME="$TMP/roothome"
mkdir -p "$GOAMNEZIA_WIPE_HOME"
STASH="$GOAMNEZIA_NETDEPS_STASH_DIR"
# shellcheck disable=SC1091
source "$ROOT/lib/backup.sh"
# Функции сноса — из install.sh как есть (его целиком не подключить: в конце он зовёт main).
for _f in _cfg _profile_installed _wipe_everything _wipe_restore_hint; do
    eval "$(sed -n "/^$_f() {/,/^}/p" "$ROOT/install.sh")"
done
eval "$(grep -m1 '^_ui_w() {' "$ROOT/install.sh")"
check "функции сноса взяты из install.sh" 'type _wipe_everything >/dev/null 2>&1 && type _ui_w >/dev/null 2>&1'
# Чистая картина: прежний реестр снят, соседи — висящий legacy-socks (чей — не записано) и warp-socks-test в сети профиля.
rm -rf "$STATE_DIR/.netdeps"
printf 'legacy-socks\t%s\tcontainer:%s\ttrue\tnet:[4026530001]\n' "$(_ga_hex64 dep5)" "$LEG" >"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
run amnezia_profile_install awg2 40050 default
IDW=$(hostid)
printf 'warp-socks-test\t%s\tcontainer:%s\ttrue\t%s\n' "$(_ga_hex64 dep1)" "$IDW" "$(hostns)" >>"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
run ga_amn_neighbours_up awg2
check "профиль awg2 стоит, warp-socks-test в его сети, ID записан в реестр state/.netdeps" \
    '[ "$(dep warp-socks-test 3)" = "container:$IDW" ] && [ "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>/dev/null)" = "$IDW" ]' \
    "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1)"
# Снос: подтверждения — «да»; сухой docker rm фикстуру не трогает — контейнер «снят» пометкой .state.
_confirm_saved=$(declare -f confirm confirm_phrase)
confirm() { return 0; }
confirm_phrase() { return 0; }
printf 'missing\n' >"$CFS/amnezia-awg2/.state"
run _wipe_everything
eval "$_confirm_saved"
check "снос прошёл: rc=0, «goAmnezia удалён.»" '[ "$rc" = 0 ] && grep -qF "$(t se_wipe_done)" "$ERR"' "rc=$rc $(errs | tail -5)"
check "снос называет соседа и обещает: вернётся в сеть профиля при новой установке" \
    'grep -q "warp-socks-test" "$ERR" && [ "$(tr -s " \n" "  " <"$ERR" | grep -c "вернутся в сеть профиля при новой установке goAmnezia")" = 1 ]' \
    "$(errs | grep -A2 -i "warp-socks" | head -6)"
check "реестр — в хранилище вне каталога продукта: ID снесённого контейнера, каталог 0700, файл 0600" \
    '[ "$(cat "$STASH/amnezia-awg2.ids" 2>/dev/null)" = "$IDW" ] && [ "$(perm "$STASH")" = 700 ] && [ "$(perm "$STASH/amnezia-awg2.ids")" = 600 ]' \
    "$(ls -la "$STASH" 2>&1)"
# Остальное снос делает только на сервере: каталог продукта стирается целиком — здесь руками. Контейнера профиля больше
# нет (.state missing, ID стенда — не прежний: его сменит docker run новой установки), новый поднимется без goWARP.
_ns=$(cat "$CFS/amnezia-awg2/.netns")
rm -rf "$GOAMNEZIA_DIR" "$CFS/amnezia-awg2"
mkdir -p "$STATE_DIR" "$CFS/amnezia-awg2"; chmod 700 "$STATE_DIR"
_ga_hex64 "amnezia-awg2-not-yet" >"$CFS/amnezia-awg2/.id"
printf '%s\n' "$_ns" >"$CFS/amnezia-awg2/.netns"; printf 'missing\n' >"$CFS/amnezia-awg2/.state"
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
check "до новой установки сосед в сети снесённого контейнера, но он не «висящий без записи» — запись в хранилище" \
    '[ "$(dep warp-socks-test 3)" = "container:$IDW" ] && [ "$(docker_netdeps_dangling | paste -sd, -)" = legacy-socks ]' \
    "$(docker_netdeps_dangling 2>&1 | paste -sd, -)"
GA_AMN_NETDEPS_LOG=""     # новая установка — новый процесс
run amnezia_profile_install awg2 40060 default
rm -f "$CFS/amnezia-awg2/.state"     # docker run стенда .state не трогает: контейнер теперь есть
IDN=$(hostid)
check "новая установка: rc=0, контейнер amnezia-awg2 создан заново (docker run, новый ID), остатка сносить не пришлось" \
    '[ "$rc" = 0 ] && [ -n "$IDN" ] && [ "$IDN" != "$IDW" ] && [ "$IDN" != "$(_ga_hex64 amnezia-awg2-not-yet)" ] && ! grep -q "DRY: docker stop amnezia-awg2" "$ERR"' \
    "rc=$rc $(errs | tail -4)"
check "сосед пересоздан из своего inspect в сети нового amnezia-awg2: ссылка на новый ID, работает, то же пространство" \
    'grep -q "POST /containers/create?name=warp-socks-test (NetworkMode container:amnezia-awg2)" "$ERR" && [ "$(dep warp-socks-test 3)" = "container:$IDN" ] && [ "$(dep warp-socks-test 4)" = true ] && [ "$(dep warp-socks-test 5)" = "$(hostns)" ]' \
    "$(grep -i "warp-socks" "$ERR" | head -4) $(cat "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE")"
check "хранилище очищено (ссылок на прежний ID больше нет), в реестре новой установки — нынешний ID" \
    '[ ! -e "$STASH" ] && [ "$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>/dev/null)" = "$IDN" ]' \
    "$(ls -la "$STASH" 2>&1) reg=$(cat "$STATE_DIR/.netdeps/amnezia-awg2.ids" 2>&1)"
check "висящий legacy-socks (чей — не записано) не тронут" \
    '! grep -q "legacy-socks" "$ERR" && [ "$(dep legacy-socks 3)" = "container:$LEG" ]' "$(errs | grep legacy | head -3)"
check "журнал действий: netdep-recreated warp-socks-test" 'audit_has netdep-recreated warp-socks-test'

section "WRP-06 конец установки: кто вернулся, висящие соседи, goWARP без WARP"
run ga_amn_install_notes
_notes=$(tr -s ' \n' '  ' <"$ERR")
check "итог установки: warp-socks-test снова в сети amnezia-awg2" \
    'grep -qF "Контейнер warp-socks-test снова в сети amnezia-awg2" <<<"$_notes"' "$(errs)"
check "итог установки: висящий legacy-socks назван один раз, с ручным способом (--network container:amnezia-awgN)" \
    '[ "$(grep -o "Контейнеры legacy-socks живут в сети контейнера, которого больше нет" <<<"$_notes" | wc -l | tr -d " ")" = 1 ] && grep -qF -- "--network container:amnezia-awgN" <<<"$_notes"' "$(errs)"
check "итог установки: goWARP стоит, а в новом контейнере /opt/warp нет — строка «откройте goWARP… пункт 1»" \
    'grep -qF "goWARP PRO стоит, но в новых контейнерах WARP нет" <<<"$_notes" && grep -qF "пункт 1 «Установить / переустановить WARP»" <<<"$_notes"' "$(errs)"
mkdir -p "$CFS/amnezia-awg2/opt/warp"; printf 'x\n' >"$CFS/amnezia-awg2/opt/warp/warp.conf"
run ga_amn_install_notes
check "WARP в контейнере есть (goWARP уже подключён) — строки про goWARP нет" '! grep -q "goWARP PRO стоит" "$ERR"' "$(errs)"
rm -rf "$CFS/amnezia-awg2/opt/warp"; chmod -x "$GOAMNEZIA_GOWARP_WATCH"
run ga_amn_install_notes
check "goWARP на машине нет (сторожа gowarp-awg-watch нет) — строки про goWARP нет" '! grep -q "goWARP PRO стоит" "$ERR"' "$(errs)"
chmod +x "$GOAMNEZIA_GOWARP_WATCH"
_iw=$(sed -n '/^install_wizard() {/,/^}/p' "$ROOT/install.sh"); _ls=$(sed -n '/^_lazy_summary() {/,/^}/p' "$ROOT/install.sh")
check "мастер и Ленивый Pro зовут итог в конце установки" \
    'grep -q "ga_amn_install_notes" <<<"$_iw" && grep -q "ga_amn_install_notes" <<<"$_ls"'

section "WRP-06 сторож: висящий сосед — один раз, а не каждый тик; «Диагностика» — строкой"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
run amnezia_watch_tick
check "первый тик: предупреждение в журнал с ручным способом и одно событие боту netdeps_dangling {containers}" \
    '[ "$rc" = 0 ] && grep -q "Контейнеры legacy-socks живут в сети контейнера" "$ERR" && [ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -sc "[.[] | select(.event == \"netdeps_dangling\") | .containers]")" = "[[\"legacy-socks\"]]" ]' \
    "out=$out $(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | head -c 300) $(errs | tail -3)"
check "отметка «сказано» — в state/.netdeps (0600), сосед не тронут" \
    '[ "$(cat "$STATE_DIR/.netdeps/dangling.reported" 2>/dev/null)" = legacy-socks ] && [ "$(perm "$STATE_DIR/.netdeps/dangling.reported")" = 600 ] && [ "$(dep legacy-socks 3)" = "container:$LEG" ] && ! grep -q "docker rename legacy-socks" "$ERR"'
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
run amnezia_watch_tick
check "второй тик: ни строки, ни события" \
    '[ "$rc" = 0 ] && ! grep -q "legacy-socks" "$ERR" && ! ls "$GOAMNEZIA_NOTIFY_DIR"/*.json >/dev/null 2>&1 && [ "$(jq -r .events <<<"$out")" = 0 ]' "out=$out $(errs | tail -3)"
_na=$(jq -R -n '[inputs | fromjson? // empty | select(.action == "netdeps_dangling" and .details == "containers=legacy-socks")] | length' \
    "$GOAMNEZIA_AUDIT_LOG" 2>&1)
check "журнал действий: netdeps_dangling containers=legacy-socks — одна запись на два тика" '[ "$_na" = 1 ]' "записей: $_na"
# «Диагностика»: поле quality через настоящий мост и строка экрана.
_q=$("${BASH5:-$BASH}" "$ROOT/install.sh" --action=quality --json 2>/dev/null | tail -n 1)
check "quality (мост): netdeps_dangling = [\"legacy-socks\"]" '[ "$(jq -c .quality.netdeps_dangling <<<"$_q")" = "[\"legacy-socks\"]" ]' "$_q"
eval "$(sed -n '/^_diagnose_screen() {/,/^}/p' "$ROOT/install.sh")"
eval "$(grep -m1 '^_dq() {' "$ROOT/install.sh")"
_screen_head() { :; }; _site_domain_ready() { return 1; }; _ga_quality_json() { jq -c .quality <<<"$_q"; }
run _diagnose_screen
check "«Диагностика»: строка «Контейнеры без сети (legacy-socks)» с ручным способом" \
    'tr -s " \n" "  " <"$ERR" | grep -qF "Контейнеры без сети (legacy-socks)" && tr -s " \n" "  " <"$ERR" | grep -qF -- "--network container:amnezia-awgN"' \
    "$(cat "$OUT" "$ERR" | head -30)"
unset -f _screen_head _site_domain_ready _ga_quality_json _diagnose_screen _dq
# Висящих не осталось — отметка снимается; появились снова — сторож скажет снова.
grep -v "^legacy-socks" "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE" >"$TMP/nd.tmp"; cp "$TMP/nd.tmp" "$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
run amnezia_watch_tick
check "висящих не осталось — отметка снята" '[ ! -e "$STATE_DIR/.netdeps/dangling.reported" ]'
printf 'legacy-socks\t%s\tcontainer:%s\ttrue\tnet:[4026530001]\n' "$(_ga_hex64 dep5)" "$LEG" >>"$GOAMNEZIA_DOCKER_NETDEPS_FIXTURE"
run amnezia_watch_tick
check "висящий появился снова — сторож говорит снова (один раз)" 'grep -q "Контейнеры legacy-socks живут в сети контейнера" "$ERR"' "$(errs | tail -3)"
unset GOAMNEZIA_NETDEPS_STASH_DIR GOAMNEZIA_WIPE_HOME

# ── WRP-04: goWARP после goAmnezia ───────────────────────────────────────────
section "WRP-04 goWARP после goAmnezia находит наши контейнеры"
for _p in awg1 awg2 awg3; do
    _c=$(ga_profile_default "$_p" container)
    check "контейнер профиля $_p ($_c) — под маской goWARP ^amnezia-awg[0-9]*\$" '[[ "$_c" =~ ^amnezia-awg[0-9]*$ ]]'
done
check "конфиг наших контейнеров — /opt/amnezia/awg/awg0.conf (goWARP ищет его вторым после wg0.conf)" \
    '[ "$(ga_amn_conf_path awg3)" = /opt/amnezia/awg/awg0.conf ] && [ "$GA_AWG_CLIENTS_TABLE" = /opt/amnezia/awg/clientsTable ] && [ "$GA_START_SH" = /opt/amnezia/start.sh ]'
if [ -n "${GOWARP_SH:-}" ] && [ -f "$GOWARP_SH" ]; then
    check "goWARP (${GOWARP_SH##*/}): маска контейнеров прежняя" "grep -qF \"grep -E '^amnezia-awg[0-9]*\$'\" \"\$GOWARP_SH\""
    check "goWARP: запасной путь к awg0.conf и start.sh прежние" \
        'grep -qF "/opt/amnezia/awg/awg0.conf" "$GOWARP_SH" && grep -qF "AWG_START_SH=\"/opt/amnezia/start.sh\"" "$GOWARP_SH"'
else
    skipped "маска и пути в коде goWARP" "GOWARP_SH не задан (путь к warp.sh дерева goWARP PRO)"
fi

# ── Словари ──────────────────────────────────────────────────────────────────
section "словари"
for _k in amnezia_netdep_restarted amnezia_netdep_recreated amnezia_netdep_failed amnezia_warn_netdeps_remove amnezia_warn_mtu_live timer_busy_skipped mig_server_mtu mig_netdeps_dangling \
          amnezia_netdep_back amnezia_warn_netdeps_dangling amnezia_gowarp_missing se_wipe_netdeps se_wipe_netdeps_stash_failed diag_netdeps_dangling; do
    check "ключ $_k — в ru и en" 'grep -rq "^I18N\[$_k\]=" "$ROOT/lib/lang/ru.d" "$ROOT/lib/lang/ru.sh" && grep -rq "^I18N\[$_k\]=" "$ROOT/lib/lang/en.d" "$ROOT/lib/lang/en.sh"'
done

DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
