#!/bin/sh
# Kaskad 3 gateway: ocserv connect/disconnect -> kaskadctl gw hook ocserv up|down <login> <ip>.
# Session accounting only: never refuses a connection and does not make it wait.
case "$REASON" in
    connect) act=up ;;
    disconnect) act=down ;;
    *) exit 0 ;;
esac
/opt/kaskad/bin/kaskadctl gw hook ocserv "$act" "$USERNAME" "$IP_REMOTE" </dev/null >/dev/null 2>&1 &
exit 0
