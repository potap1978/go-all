#!/bin/bash
# iso_pair_test.sh <каталог с a.conf и b.conf> <метка> — изоляция клиентов одного профиля (SEC-NET-1), на машине-клиенте.
#
# Поднимает ДВА настоящих клиента AmneziaWG одновременно (одноразовые --privileged контейнеры, как awg_client_test.sh,
# полный туннель 0.0.0.0/0) и проверяет: у каждого есть выход в интернет через сервер (ipify = EXPECT_EXIT), и может
# ли каждый достучаться до туннельного адреса другого — ping и TCP (слушатель busybox nc на 18080 в каждом клиенте).
# Каталог с конфигами (0700, файлы 0600) удаляется в конце, контейнеры тоже; ключи не печатаются.
# Одна строка: ISO <метка> hs=<a>/<b> exit=<a>/<b> a->b ping=<ok|blocked> tcp=<ok|blocked> b->a ping=… tcp=…
# Env: GA_CLIENT_IMG, EXPECT_EXIT, HS_TIMEOUT (30).
set -uo pipefail
dir="${1:?каталог}"
tag="${2:?метка}"
img="${GA_CLIENT_IMG:-ga-test-client:latest}"
hs_timeout="${HS_TIMEOUT:-30}"
names=()
cleanup() { for n in "${names[@]}"; do docker rm -f "$n" >/dev/null 2>&1; done; rm -rf "$dir"; }
trap cleanup EXIT

ipify=$(getent ahostsv4 api.ipify.org 2>/dev/null | awk 'NR==1{print $1}')
declare -A ip hs ex
for x in a b; do
    src="$dir/$x.conf"
    [ -s "$src" ] || { echo "ISO $tag FAIL no $x.conf"; exit 1; }
    ip[$x]=$(sed -n 's/^[[:space:]]*Address[[:space:]]*=[[:space:]]*//p' "$src" | head -1 | cut -d, -f1 | tr -d ' ' | cut -d/ -f1)
    sed -e '/^[[:space:]]*DNS[[:space:]]*=/d' -e 's|^[[:space:]]*AllowedIPs[[:space:]]*=.*|AllowedIPs = 0.0.0.0/0|' \
        "$src" > "$dir/$x.wg"
    n="awgiso-$x-$(printf '%s' "$tag" | tr -c 'a-zA-Z0-9_.-' '-' | cut -c1-30)-$$"
    names+=("$n")
    docker run -d --name "$n" --privileged --device /dev/net/tun --sysctl net.ipv4.conf.all.src_valid_mark=1 \
        -e WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go --entrypoint sh "$img" -c 'sleep 900' >/dev/null 2>&1 \
        || { echo "ISO $tag FAIL docker run $x"; exit 1; }
    docker exec -i "$n" sh -c 'umask 077; mkdir -p /etc/amnezia/amneziawg; cat > /etc/amnezia/amneziawg/wgt0.conf' < "$dir/$x.wg"
    rm -f "$dir/$x.wg" "$src"
    docker exec "$n" awg-quick up wgt0 >/dev/null 2>&1 || { echo "ISO $tag FAIL awg-quick up $x"; exit 1; }
    docker exec -d "$n" sh -c 'while true; do echo iso | nc -l -p 18080; done'
done
sleep 1
httpd_ok=1   # TCP-проверка честна, только если свой слушатель у клиента отвечает; иначе tcp=n/a, а не «blocked»
for n in "${names[@]}"; do
    [ "$(docker exec "$n" sh -c 'nc -w 2 127.0.0.1 18080 </dev/null' 2>/dev/null)" = iso ] || httpd_ok=0
done
na="${names[0]}"; nb="${names[1]}"
for x in a b; do
    n="${names[0]}"; [ "$x" = b ] && n="${names[1]}"
    srv="${ip[$x]%.*}.0"; t0=$(date +%s); hs[$x]=none
    while :; do
        v=$(docker exec "$n" awg show wgt0 latest-handshakes 2>/dev/null | awk 'NR==1{print $2}')
        if [ -n "$v" ] && [ "$v" -gt 0 ] 2>/dev/null; then hs[$x]="$(( $(date +%s) - t0 ))s"; break; fi
        [ $(( $(date +%s) - t0 )) -ge "$hs_timeout" ] && break
        docker exec "$n" ping -c1 -W1 "$srv" >/dev/null 2>&1
    done
    ex[$x]=$(docker exec "$n" curl -s -m 10 ${ipify:+--resolve "api.ipify.org:80:$ipify"} http://api.ipify.org 2>/dev/null)
    printf '%s' "${ex[$x]}" | grep -Eq '^[0-9.]+$' || ex[$x]="-"
done
reach() {  # reach <контейнер> <адрес> → "ping=… tcp=…"
    local p=blocked t=blocked
    docker exec "$1" ping -c 2 -W 2 "$2" >/dev/null 2>&1 && p=ok
    if [ "$httpd_ok" != 1 ]; then t=n/a
    elif [ "$(docker exec "$1" sh -c "nc -w 3 $2 18080 </dev/null" 2>/dev/null)" = iso ]; then t=ok; fi
    printf 'ping=%s tcp=%s' "$p" "$t"
}
ab=$(reach "$na" "${ip[b]}"); ba=$(reach "$nb" "${ip[a]}")
echo "ISO $tag hs=${hs[a]}/${hs[b]} exit=${ex[a]}/${ex[b]} a->b $ab b->a $ba"
