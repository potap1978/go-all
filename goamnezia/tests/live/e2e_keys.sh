#!/bin/bash
# Сквозная проверка ключей goAmnezia на сервере: настоящий клиент AmneziaWG в отдельном контейнере на
# образе того же профиля. Туннель — только в подсеть профиля (интернет контейнера через него не идёт),
# пинг адреса сервера в туннеле, рукопожатие. Вторым кругом — ключи с доменом в Endpoint (endpoint-set),
# после чего прежний endpoint_host возвращается. Запуск: ssh root@сервер 'bash -s' < e2e_keys.sh
set -uo pipefail
export DOCKER_API_VERSION="${DOCKER_API_VERSION:-1.43}"
cd /opt/goamnezia || exit 2
USER_NAME="${E2E_USER:-ga-e2e}"
PROFILES="${E2E_PROFILES:-awg1 awg2 awg3}"
WORK=/root/ga-e2e
mkdir -p "$WORK" && chmod 700 "$WORK"
fail=0

bridge() { local a="$1"; shift; GOAMNEZIA_WHO=e2e bash install.sh "--action=$a" --json "$@" 2>/dev/null | tail -1; }

get_conf() {   # get_conf <профиль> <файл>
    bridge user-key "--name=$USER_NAME" "--profile=$1" --format=conf | jq -r '.conf // empty' > "$2"
    chmod 600 "$2"
    [ -s "$2" ]
}

run_client() {   # run_client <метка> <профиль> <файл конфига>
    local label="$1" p="$2" conf="$3" img cname subnet srv loss hs tr ep dir
    img=$(docker inspect -f '{{.Config.Image}}' "$(jq -r --arg p "$p" '.profiles[$p].container' config.json)" 2>/dev/null)
    subnet=$(jq -r --arg p "$p" '.profiles[$p].subnet' config.json)
    srv="${subnet%/*}"
    cname="ga-e2e-client-$label"
    dir="$WORK/$label"
    docker rm -f "$cname" >/dev/null 2>&1
    mkdir -p "$dir" && chmod 700 "$dir"
    sed -E "/^DNS[[:space:]]*=/d; s#^AllowedIPs[[:space:]]*=.*#AllowedIPs = $subnet#" "$conf" > "$dir/e2e.conf"
    chmod 600 "$dir/e2e.conf"
    if ! docker run -d --name "$cname" --cap-add NET_ADMIN --device /dev/net/tun -v "$dir:/etc/e2e" \
            --entrypoint sh "$img" -c 'sleep 900' >/dev/null 2>"$dir/run.err"; then
        echo "✗ $label: клиентский контейнер не запустился: $(tail -2 "$dir/run.err" | tr '\n' ' ')"
        fail=$((fail + 1)); rm -rf "$dir"; return
    fi
    if ! docker exec "$cname" awg-quick up /etc/e2e/e2e.conf >"$dir/up.log" 2>&1; then
        echo "✗ $label: awg-quick up отказал: $(tail -3 "$dir/up.log" | tr '\n' ' ')"
        fail=$((fail + 1)); docker rm -f "$cname" >/dev/null 2>&1; rm -rf "$dir"; return
    fi
    # Сначала рукопожатие (у 2.0 и 3.1 с мусорными пакетами оно дольше), и только потом пинг: иначе
    # первый пакет теряется, пока рукопожатие идёт, и это ошибка проверки, а не продукта.
    local waited=0 t_hs
    docker exec "$cname" ping -c 1 -W 1 "$srv" >/dev/null 2>&1
    while [ "$waited" -lt 20 ]; do
        hs=$(docker exec "$cname" awg show e2e latest-handshakes 2>/dev/null | awk '{print $2}')
        [ -n "$hs" ] && [ "$hs" != 0 ] && break
        sleep 1; waited=$((waited + 1))
    done
    t_hs=$waited
    loss=$(docker exec "$cname" ping -c 10 -i 0.3 -W 3 "$srv" 2>&1 | awk -F', ' '/packet loss/ {print $3}')
    hs=$(docker exec "$cname" awg show e2e latest-handshakes 2>/dev/null | awk '{print $2}')
    tr=$(docker exec "$cname" awg show e2e transfer 2>/dev/null | awk '{print $2 "/" $3}')
    ep=$(docker exec "$cname" awg show e2e endpoints 2>/dev/null | awk '{print $2}')
    docker exec "$cname" awg-quick down /etc/e2e/e2e.conf >/dev/null 2>&1
    docker rm -f "$cname" >/dev/null 2>&1
    rm -rf "$dir"
    if [ -n "$hs" ] && [ "$hs" != 0 ] && [[ "$loss" == 0%* ]]; then
        echo "✓ $label: рукопожатие за ${t_hs} с, пинг $srv без потерь, принято/отдано $tr байт, сервер $ep"
    else
        echo "✗ $label: рукопожатие=${hs:-нет} потери=${loss:-?} трафик=${tr:-?} сервер=${ep:-?}"
        fail=$((fail + 1))
    fi
}

echo "режим $(jq -r '.mode' config.json), домен «$(jq -r '.domain // ""' config.json)», Endpoint «$(jq -r '.endpoint_host // ""' config.json)»"
for p in $PROFILES; do
    c="$WORK/$p.conf"
    if ! get_conf "$p" "$c"; then echo "✗ $p: ключ не выдан"; fail=$((fail + 1)); continue; fi
    echo "· $p (линейка $(jq -r --arg p "$p" '.profiles[$p].line // "?"' config.json)): Endpoint $(awk -F' = ' '/^Endpoint/ {print $2}' "$c")"
    run_client "$p" "$p" "$c"
    rm -f "$c"
done

dom=$(jq -r '.domain // ""' config.json)
prev=$(jq -r '.endpoint_host // ""' config.json)
if [ "${E2E_DOMAIN:-1}" = 1 ] && [ -n "$dom" ]; then
    restore() { bridge endpoint-set "--host=$prev" >/dev/null; }
    trap restore EXIT
    echo "· endpoint-set $dom: $(bridge endpoint-set "--host=$dom" | jq -r '.status + " " + (.code // "")')"
    for p in $PROFILES; do
        c="$WORK/$p-domain.conf"
        if ! get_conf "$p" "$c"; then echo "✗ $p-домен: ключ не выдан"; fail=$((fail + 1)); continue; fi
        ep=$(awk -F' = ' '/^Endpoint/ {print $2}' "$c")
        if [[ "$ep" != "$dom:"* ]]; then echo "✗ $p-домен: в ключе Endpoint $ep, ждали $dom"; fail=$((fail + 1)); fi
        run_client "$p-domain" "$p" "$c"
        rm -f "$c"
    done
    restore
    trap - EXIT
    echo "· endpoint_host возвращён: «$(jq -r '.endpoint_host // ""' config.json)»"
fi
rmdir "$WORK" 2>/dev/null
echo "ИТОГ e2e: провалено $fail"
