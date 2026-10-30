#!/bin/bash
# awg_client_test.sh <conf-file | -> <tag> [endpoint-override]
#
# A real AmneziaWG client connection from a throwaway --privileged container (image ga-test-client:
# the Amnezia app's server_scripts/awg/Dockerfile = amneziavpn/amneziawg-go + awg/awg-quick, lines 1.0–3.1).
# The container has its own network namespace, so the host's routes and SSH are never touched.
#
#   <conf-file>   client .conf; "-" reads it from stdin (pipe it over ssh, never store it anywhere else)
#   <tag>         free label for the result line
#   [override]    replace the Endpoint: "IP" (keeps the port), "IP:port", "[v6]:port" or a bare IPv6
#
# Steps: DNS line removed (no resolvconf in the container); AllowedIPs = 0.0.0.0/0 (full tunnel; ::/0 dropped,
# the container has no IPv6); WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go; awg-quick up; wait for a handshake
# (<= HS_TIMEOUT s); ping the server's tunnel address — the .0 of the client's /24 (Amnezia-app and goAmnezia
# servers both sit on .0, clients start at .1); fetch http://api.ipify.org through the tunnel = the VPN egress IP.
#
# Prints exactly one line:
#   OK|FAIL <tag> Endpoint=<host:port> handshake=<Ns|none> ping=<ok(avg ms)|fail> exit=<ip|->  [note]
# Exit code 0 on OK, 1 on FAIL.
#
# Env: EXPECT_EXIT=<ip>  also FAIL when the egress IP differs
#      PING_TARGET=<ip>  ping this instead of <subnet>.0
#      HS_TIMEOUT=30     seconds to wait for the handshake
#      GA_CLIENT_IMG=ga-test-client:latest
#      KEEP=1            leave the container running for debugging (it still holds the private key — remove it!)
# Secrets: the conf only lives in a 0600 temp dir and inside the container; both are removed; nothing
# key-like is ever printed (log lines are scrubbed of 44-char base64 values).
set -uo pipefail

conf="${1:?usage: awg_client_test.sh <conf|-> <tag> [endpoint-override]}"
tag="${2:?tag}"
ep="${3:-}"
img="${GA_CLIENT_IMG:-ga-test-client:latest}"
hs_timeout="${HS_TIMEOUT:-30}"
ifc=wgt0

umask 077
work=$(mktemp -d /root/.awgc.XXXXXX) || { echo "FAIL $tag Endpoint=? handshake=none ping=- exit=- (mktemp)"; exit 1; }
chmod 700 "$work"
name="awgt-$(printf '%s' "$tag" | tr -c 'a-zA-Z0-9_.-' '-' | cut -c1-40)-$$"
cleanup() {
    if [ "${KEEP:-0}" != 1 ]; then docker rm -f "$name" >/dev/null 2>&1; fi
    rm -rf "$work"
}
trap cleanup EXIT

scrub() { sed -E 's#[A-Za-z0-9+/]{42,43}=#<key>#g' | tr '\n' ' ' | cut -c1-160; }

if [ "$conf" = "-" ]; then cat > "$work/in.conf"; else cat -- "$conf" > "$work/in.conf" 2>/dev/null; fi
tr -d '\r' < "$work/in.conf" > "$work/src.conf"
if ! grep -q '^[[:space:]]*\[Peer\]' "$work/src.conf"; then
    echo "FAIL $tag Endpoint=? handshake=none ping=- exit=- (no conf / no [Peer] section)"
    exit 1
fi

addr=$(sed -n 's/^[[:space:]]*Address[[:space:]]*=[[:space:]]*//p' "$work/src.conf" | head -1 | cut -d, -f1 | tr -d ' ')
base="${addr%/*}"
srv="${PING_TARGET:-${base%.*}.0}"
orig_ep=$(sed -n 's/^[[:space:]]*Endpoint[[:space:]]*=[[:space:]]*//p' "$work/src.conf" | head -1 | tr -d ' ')
port="${orig_ep##*:}"
new_ep="$orig_ep"
note=""
if [ -n "$ep" ]; then
    case "$ep" in
        \[*) new_ep="$ep" ;;                                   # [v6]:port
        *:*:*) new_ep="[$ep]:$port" ;;                         # bare IPv6
        *:*) new_ep="$ep" ;;                                   # host:port
        *) new_ep="$ep:$port" ;;                               # host, keep the port
    esac
    note="(Endpoint override, conf had $orig_ep)"
fi

sed -e '/^[[:space:]]*DNS[[:space:]]*=/d' \
    -e 's|^[[:space:]]*AllowedIPs[[:space:]]*=.*|AllowedIPs = 0.0.0.0/0|' \
    -e "s|^[[:space:]]*Endpoint[[:space:]]*=.*|Endpoint = $new_ep|" \
    "$work/src.conf" > "$work/$ifc.conf"

# api.ipify.org resolved on the host before the tunnel is up: the result does not depend on DNS in the tunnel
ipify=$(getent ahostsv4 api.ipify.org 2>/dev/null | awk 'NR==1{print $1}')

docker rm -f "$name" >/dev/null 2>&1
if ! docker run -d --name "$name" --privileged --device /dev/net/tun \
        --sysctl net.ipv4.conf.all.src_valid_mark=1 \
        -e WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go \
        --entrypoint sh "$img" -c 'sleep 600' >/dev/null 2>"$work/run.err"; then
    echo "FAIL $tag Endpoint=$new_ep handshake=none ping=- exit=- (docker run: $(scrub < "$work/run.err"))"
    exit 1
fi
docker exec -i "$name" sh -c "umask 077; mkdir -p /etc/amnezia/amneziawg; cat > /etc/amnezia/amneziawg/$ifc.conf" < "$work/$ifc.conf"
rm -f "$work/in.conf" "$work/src.conf" "$work/$ifc.conf"

if ! docker exec "$name" awg-quick up "$ifc" > "$work/up.log" 2>&1; then
    echo "FAIL $tag Endpoint=$new_ep handshake=none ping=- exit=- (awg-quick up: $(tail -2 "$work/up.log" | scrub)) $note"
    exit 1
fi

# handshake
t0=$(date +%s)
hs="none"
while :; do
    v=$(docker exec "$name" awg show "$ifc" latest-handshakes 2>/dev/null | awk 'NR==1{print $2}')
    if [ -n "$v" ] && [ "$v" -gt 0 ] 2>/dev/null; then hs="$(( $(date +%s) - t0 ))s"; break; fi
    [ $(( $(date +%s) - t0 )) -ge "$hs_timeout" ] && break
    docker exec "$name" ping -c1 -W1 "$srv" >/dev/null 2>&1   # nudge: traffic starts the handshake
done

# ping the server's tunnel address
ping_res="fail"
if [ "$hs" != "none" ]; then
    pout=$(docker exec "$name" ping -c 3 -W 2 "$srv" 2>&1)
    if printf '%s\n' "$pout" | grep -q ' 0% packet loss\| [12] packets received'; then
        avg=$(printf '%s\n' "$pout" | sed -n 's#.*= [0-9.]*/\([0-9.]*\)/.*#\1#p' | head -1)
        ping_res="ok(${avg:-?}ms)"
    fi
fi

# egress IP through the tunnel
exitip="-"
if [ "$hs" != "none" ]; then
    for _ in 1 2 3; do
        if [ -n "$ipify" ]; then
            out=$(docker exec "$name" curl -s -m 10 --resolve "api.ipify.org:80:$ipify" http://api.ipify.org 2>/dev/null)
        else
            out=$(docker exec "$name" curl -s -m 10 http://api.ipify.org 2>/dev/null)
        fi
        if printf '%s' "$out" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then exitip="$out"; break; fi
        sleep 1
    done
fi

verdict=OK
[ "$hs" = "none" ] && verdict=FAIL
[ "$ping_res" = "fail" ] && verdict=FAIL
[ "$exitip" = "-" ] && verdict=FAIL
if [ -n "${EXPECT_EXIT:-}" ] && [ "$exitip" != "$EXPECT_EXIT" ]; then
    verdict=FAIL
    note="$note (expected exit $EXPECT_EXIT)"
fi
echo "$verdict $tag Endpoint=$new_ep handshake=$hs ping=$ping_res exit=$exitip${note:+ $note}"
[ "$verdict" = OK ]
