#!/bin/sh
# Kaskad 3 gateway: strongSwan updown of the IKEv2 inbound -> kaskadctl gw hook ikev2 up|down <identity> <ip>.
# up waits (10 s at most): the client's address must be in gw_route before its traffic flows; down goes
# to the background. Never fails: strongSwan must not care.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
[ "$PLUTO_CONNECTION" = "kaskad-ikev2" ] || exit 0
id="${PLUTO_XAUTH_ID:-$PLUTO_PEER_ID}"
ip="${PLUTO_PEER_SOURCEIP4_1:-$PLUTO_PEER_SOURCEIP}"
[ -n "$ip" ] || exit 0
case "$PLUTO_VERB" in
    up-*) timeout 10 /opt/kaskad/bin/kaskadctl gw hook ikev2 up "$id" "$ip" </dev/null >/dev/null 2>&1 ;;
    down-*) /opt/kaskad/bin/kaskadctl gw hook ikev2 down "$id" "$ip" </dev/null >/dev/null 2>&1 & ;;
esac
exit 0
