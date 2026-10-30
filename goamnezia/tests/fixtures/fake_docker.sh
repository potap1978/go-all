#!/bin/sh
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — подставной `docker` для регрессии «версии CLI и демона разошлись».
#
# Ведёт себя ровно как стенд в ночь на 10.09.2026: unattended-upgrades обновил
# пакет docker.io 24.0.7 → 29.1.3, демон при этом НЕ перезапускался. Новый
# клиент просит API 1.52, живой старый демон понимает максимум 1.43, и каждая
# команда падает — при том, что контейнеры работают и VPN раздаёт трафик.
#
# Кладётся первым в PATH под именем `docker`. Режим — FAKE_DOCKER_MODE:
#   mismatch — как на стенде: без DOCKER_API_VERSION всё падает, с 1.43 работает;
#   down     — демон не отвечает вовсе (сокета нет, служба лежит);
#   ok       — версии совпадают, поведение обычное (проверка «не сломали норму»).
# FAKE_DOCKER_AWG_DUMP — файл с сохранённым `awg show <iface> dump`: его отдаёт
# `docker exec … dump`, на нём считает дельты тик квот.
# FAKE_DOCKER_MISSING — имена контейнеров через пробел, которых нет: демон жив, а
# `docker inspect` отвечает «No such object» (бюджет журналов, L3-14 / TAIL-11).
# Под именем `dockerd` (копия рядом) — бинарь демона на диске (DK-02): `dockerd
# --version` печатает версию ПАКЕТА — ту же, что у клиента. В режиме mismatch
# пакет 29.1.3, а работает демон 24.0.7: служба старая; в ok версии совпадают.
# ══════════════════════════════════════════════════════════════════════════════
mode="${FAKE_DOCKER_MODE:-ok}"
daemon_api=1.43
daemon_ver=24.0.7
client_api=1.52
client_ver=29.1.3
if [ "$mode" = ok ]; then
    client_api="$daemon_api"
    client_ver="$daemon_ver"
fi

if [ "${0##*/}" = dockerd ]; then
    [ "${1:-}" = --version ] || exit 1
    echo "Docker version ${client_ver}, build ${client_ver}-0ubuntu3~24.04.2"
    exit 0
fi

# Отвечает ли демон нам. В режиме mismatch — только когда версию API закрепили
# ровно на той, что демон понимает.
daemon_ok=0
case "$mode" in
    ok) daemon_ok=1 ;;
    mismatch)
        if [ -n "${DOCKER_API_VERSION:-}" ] && [ "$DOCKER_API_VERSION" = "$daemon_api" ]; then
            daemon_ok=1
        fi
        ;;
esac

refuse() {
    if [ "$mode" = down ]; then
        echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2
    else
        echo "Error response from daemon: client version ${DOCKER_API_VERSION:-$client_api} is too new. Maximum supported API version is ${daemon_api}" >&2
    fi
    exit 1
}

cmd="${1:-}"

# `docker version` печатает секцию Client ВСЕГДА и падает уже на секции Server —
# на этом стоит и определение версии клиента, и запасной разбор текста ошибки.
if [ "$cmd" = version ]; then
    if [ "${2:-}" = --format ]; then
        case "${3:-}" in
            *Client.APIVersion*) echo "$client_api" ;;
            *Client.Version*)    echo "$client_ver" ;;
            *Server*)            [ "$daemon_ok" = 1 ] || refuse
                                 echo "$daemon_ver"
                                 exit 0 ;;
        esac
    else
        printf 'Client:\n Version:           %s\n API version:       %s\n' "$client_ver" "$client_api"
    fi
    [ "$daemon_ok" = 1 ] || refuse
    exit 0
fi

if [ "$cmd" = --version ]; then
    echo "Docker version ${client_ver}, build ${client_ver}-0ubuntu3~24.04.2"
    exit 0
fi

[ "$daemon_ok" = 1 ] || refuse

case "$cmd" in
    inspect)
        # docker inspect -f '{{.State.Status}}' <имя> — имя всегда последнее.
        # Отдельно разбираем {{.LogPath}}: его спрашивает сторож бюджета логов
        # (lib/logbudget.sh), и путь задаётся через FAKE_DOCKER_LOGPATH.
        name=""
        fmt=""
        for a in "$@"; do
            case "$a" in *LogPath*) fmt=logpath ;; esac
            name="$a"
        done
        case " ${FAKE_DOCKER_MISSING:-} " in
            *" $name "*) echo "Error: No such object: $name" >&2; exit 1 ;;
        esac
        case "$name" in
            amnezia-*) if [ "$fmt" = logpath ]; then
                           echo "${FAKE_DOCKER_LOGPATH:-}"
                       else
                           echo running
                       fi ;;
            *)         echo "Error: No such object: $name" >&2; exit 1 ;;
        esac
        ;;
    exec)
        # Нужен только `<утилита> show <интерфейс> dump`: остальное — проверки
        # живости, им достаточно нулевого кода.
        for a in "$@"; do
            if [ "$a" = dump ]; then
                if [ -n "${FAKE_DOCKER_AWG_DUMP:-}" ] && [ -r "$FAKE_DOCKER_AWG_DUMP" ]; then
                    cat "$FAKE_DOCKER_AWG_DUMP"
                fi
                exit 0
            fi
        done
        ;;
    ps)
        # Формат `{{.Names}} {{.Ports}}` — как у приложения Amnezia.
        echo "amnezia-awg2 0.0.0.0:40000->40000/udp, :::40000->40000/udp"
        ;;
    *) : ;;
esac
exit 0
