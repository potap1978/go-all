#!/bin/sh
# Kaskad 3 gateway: L2TP only through IPsec. The xfrm policy below makes the kernel drop UDP to port
# 1701 that did not come through an IPsec transport SA (plain L2TP); IPsec clients pass it or the
# more specific policies of strongSwan. GUARD=0 when plain L2TP is on or L2TP is off. Run by
# kaskad-gw-accel.service (start: set; stop: "off") and by kaskadctl when the setting changes.
GUARD=1
PATH=/usr/sbin:/usr/bin:/sbin:/bin
sel="src 0.0.0.0/0 dst 0.0.0.0/0 proto udp dport 1701"
ip xfrm policy delete dir in $sel 2>/dev/null
[ "$1" = off ] && exit 0
[ "$GUARD" = 1 ] || exit 0
exec ip xfrm policy add dir in $sel priority 4000000000 tmpl proto esp mode transport level required
