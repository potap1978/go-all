#!/bin/bash
# kmod_client.sh <up|probe|wait|iperf|down> <tag> [args] — постоянный клиент AmneziaWG для живой проверки kmod
# (tests/live/acceptance.py --checks kmod): один и тот же контейнер с одним и тем же ключом живёт через все смены
# движка на сервере — программа → модуль → программа → модуль, — и по нему видно, вернулся ли клиент сам и что ключи не
# перевыдавались. Образ — тот же, что у awg_client_test.sh (GA_CLIENT_IMG: образ Amnezia с awg и amneziawg-go), клиент
# на программе (WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go; на машине-клиенте модуля amneziawg быть не должно),
# своё сетевое пространство: маршруты хоста не трогаются.
#
#   up <tag>                          .conf на stdin (трубой с сервера, нигде не хранится); контейнер ga-kmod-<tag>,
#                                     iperf3 внутри (apk, до туннеля), awg-quick up, ping раз в 2 с в фоне — клиент «на
#                                     связи»; печатает UP <tag> hs=<с> ping=<…> pub=<отпечаток> srv=<отпечаток>
#                                     engine=<tun|amneziawg> iperf=<yes|no> server=<адрес сервера в туннеле>
#   probe <tag>                       PROBE <tag> hs=<эпоха> now=<эпоха> ping=<ok(мс)|fail> pub=<…> srv=<…>
#   wait <tag> <с какого момента> <секунд>   BACK <tag> after=<с> | LOST <tag> — пришло ли рукопожатие не раньше момента
#   iperf <tag> <секунд>              IPERF <tag> up=<Мбит/с> down=<Мбит/с> | IPERF <tag> skip <причина>
#   down <tag>                        контейнер прочь (в нём приватный ключ)
#
# Отпечатки — первые 16 знаков sha256 открытого ключа клиента и сервера: сверка «ключи не менялись» без самих ключей.
# Секреты не печатаются; конфиг живёт только во временном каталоге 0700 до docker cp и в контейнере.
set -uo pipefail
cmd="${1:?usage: kmod_client.sh up|probe|wait|iperf|down <tag> …}"
tag="${2:?tag}"
img="${GA_CLIENT_IMG:-ga-test-client:latest}"
name="ga-kmod-$(printf '%s' "$tag" | tr -c 'a-zA-Z0-9_.-' '-' | cut -c1-40)"
ifc=wgt0
fp() { sha256sum | cut -c1-16; }
srv_ip() { docker exec "$name" sh -c "cat /etc/amnezia/amneziawg/$ifc.addr 2>/dev/null"; }
hs_now() { docker exec "$name" awg show "$ifc" latest-handshakes 2>/dev/null | awk 'NR==1{print $2}'; }
pub_fp() { docker exec "$name" awg show "$ifc" public-key 2>/dev/null | fp; }
srv_fp() { docker exec "$name" awg show "$ifc" peers 2>/dev/null | head -n 1 | fp; }
mbps() { awk '/\[SUM\].*receiver/ { for (i = 1; i <= NF; i++) if ($i == "Mbits/sec") v = $(i - 1) } END { if (v != "") print v }'; }
ping_res() {
    local out avg
    out=$(docker exec "$name" ping -c 3 -W 2 "$(srv_ip)" 2>&1)
    if printf '%s\n' "$out" | grep -q ' 0% packet loss\| [12] packets received'; then
        avg=$(printf '%s\n' "$out" | sed -n 's#.*= [0-9.]*/\([0-9.]*\)/.*#\1#p' | head -1)
        printf 'ok(%sms)' "${avg:-?}"
    else
        printf 'fail'
    fi
}

case "$cmd" in
    up)
        umask 077
        work=$(mktemp -d /root/.kmodc.XXXXXX) || { echo "FAIL $tag mktemp"; exit 1; }
        trap 'rm -rf "$work"' EXIT
        cat >"$work/in.conf"
        tr -d '\r' <"$work/in.conf" >"$work/src.conf"
        grep -q '^[[:space:]]*\[Peer\]' "$work/src.conf" || { echo "FAIL $tag no conf"; exit 1; }
        addr=$(sed -n 's/^[[:space:]]*Address[[:space:]]*=[[:space:]]*//p' "$work/src.conf" | head -1 | cut -d, -f1 | tr -d ' ')
        base="${addr%/*}"; srv="${base%.*}.0"
        sed -e '/^[[:space:]]*DNS[[:space:]]*=/d' -e 's|^[[:space:]]*AllowedIPs[[:space:]]*=.*|AllowedIPs = 0.0.0.0/0|' \
            "$work/src.conf" >"$work/$ifc.conf"
        printf '%s\n' "$srv" >"$work/$ifc.addr"
        docker rm -f "$name" >/dev/null 2>&1
        if ! docker run -d --name "$name" --privileged --device /dev/net/tun --sysctl net.ipv4.conf.all.src_valid_mark=1 \
                -e WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go --entrypoint sh "$img" -c 'sleep 7200' >/dev/null 2>&1; then
            echo "FAIL $tag docker run"; exit 1
        fi
        iperf=no
        docker exec "$name" sh -c 'command -v iperf3 >/dev/null || apk add --no-cache iperf3 >/dev/null 2>&1; command -v iperf3 >/dev/null' \
            && iperf=yes
        docker exec -i "$name" sh -c "umask 077; mkdir -p /etc/amnezia/amneziawg; cat > /etc/amnezia/amneziawg/$ifc.conf" <"$work/$ifc.conf"
        docker exec -i "$name" sh -c "cat > /etc/amnezia/amneziawg/$ifc.addr" <"$work/$ifc.addr"
        rm -f "$work"/*.conf
        if ! docker exec "$name" awg-quick up "$ifc" >/dev/null 2>&1; then echo "FAIL $tag awg-quick up"; exit 1; fi
        t0=$(date +%s); hs=none
        while :; do
            v=$(hs_now)
            if [ -n "$v" ] && [ "$v" -gt 0 ] 2>/dev/null; then hs="$(( $(date +%s) - t0 ))s"; break; fi
            [ $(( $(date +%s) - t0 )) -ge 30 ] && break
            docker exec "$name" ping -c1 -W1 "$srv" >/dev/null 2>&1
        done
        # «на связи»: ping раз в 2 с — сервер видит свежие рукопожатия, клиент сам замечает смену и переподключается
        docker exec -d "$name" sh -c "while :; do ping -c 1 -W 1 $srv >/dev/null 2>&1; sleep 2; done"
        eng=$(docker exec "$name" ip -d link show dev "$ifc" 2>/dev/null | grep -o 'amneziawg\|tun' | head -1)
        echo "UP $tag hs=$hs ping=$(ping_res) pub=$(pub_fp) srv=$(srv_fp) engine=${eng:-?} iperf=$iperf server=$srv"
        ;;
    probe)
        echo "PROBE $tag hs=$(hs_now) now=$(date +%s) ping=$(ping_res) pub=$(pub_fp) srv=$(srv_fp)"
        ;;
    wait)
        since="${3:?since}"; lim="${4:-180}"; t0=$(date +%s)
        while :; do
            v=$(hs_now)
            if [ -n "$v" ] && [ "$v" -ge "$since" ] 2>/dev/null; then echo "BACK $tag after=$(( v - since ))"; exit 0; fi
            [ $(( $(date +%s) - t0 )) -ge "$lim" ] && { echo "LOST $tag"; exit 1; }
            docker exec "$name" ping -c1 -W1 "$(srv_ip)" >/dev/null 2>&1
            sleep 1
        done
        ;;
    iperf)
        secs="${3:-10}"; srv=$(srv_ip)
        docker exec "$name" sh -c 'command -v iperf3' >/dev/null 2>&1 || { echo "IPERF $tag skip no-iperf3"; exit 0; }
        # итог — строка [SUM] … receiver в Мбит/с (-f m): без python и jq на машине-клиенте
        up=$(docker exec "$name" iperf3 -c "$srv" -t "$secs" -P 4 -f m 2>/dev/null | mbps)
        down=$(docker exec "$name" iperf3 -c "$srv" -t "$secs" -P 4 -R -f m 2>/dev/null | mbps)
        if [ -z "$up" ] && [ -z "$down" ]; then echo "IPERF $tag skip no-answer"; exit 0; fi
        echo "IPERF $tag up=${up:-?} down=${down:-?}"
        ;;
    down)
        docker rm -f "$name" >/dev/null 2>&1
        echo "DOWN $tag"
        ;;
    *) echo "usage: kmod_client.sh up|probe|wait|iperf|down <tag> …" >&2; exit 2 ;;
esac
