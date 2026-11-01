"""The traffic plane of the gateway (docs/k3/SPEC-wave2-gateway.md §1): the ``gw_*`` part of table
``ip kaskad``, the table ``ip6 kaskad6``, ip rules, route tables 200 (TPROXY) and 199 (the kill switch)
and the dummy ``kgw0``.

The part is a SECTION of kaskad.nft: ``nft.render_full``, ``nft.render_update``, ``nft.apply``, ``nft.verify``
and ``nft.restore`` include it when the gateway is enabled (``SECTION.active``), so forwards (wave 1) and
the gateway share one table, one transaction and one boot file. Wave-1 code paths (``fwd add``) re-render
the gateway part too — they never drop it; with the gateway disabled the section adds nothing and leaves
foreign ``gw_*`` objects alone (``gw disable`` removes them with ``teardown_script``).

The text — lines marked V were verified on Debian 12 / Ubuntu 24.04 by k3/stand/gwcheck.sh and are kept
as they are; the others close leaks and isolate clients (``rules()``, the notes below)::

    set gw_nets  { type ipv4_addr; flags interval; elements = { 10.77.0.0/16 } }                       V
    set gw_ru    { type ipv4_addr . inet_proto; flags interval; }   # wave 3: networks of Russia (load_ru)
    set gw_live  { type ipv4_addr; elements = { 10.77.1.10, ... } }   # addresses allowed out now
    set gw_priv  { type ipv4_addr; flags interval; elements = { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10,
                   127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/3 } }
    set gw_ru_dns { type ipv4_addr; flags timeout; timeout 6h; }      # wave 3: dnsmasq nftset, «directly»
    set gw_force  { type ipv4_addr; flags timeout; timeout 6h; }      # wave 3: dnsmasq nftset, «through the exit»
    set gw_rd    { type ipv4_addr; elements = { 10.77.1.10 } }        # wave 3: clients with profile ru_direct
    set gw_ru_ver { type ipv4_addr; }                                 # wave 3: which lists gw_ru holds (load_ru)
    set gw_ruonly { type ipv4_addr; }    # stage 1 (Б1): in gw_live only for Russia — a fallback block + ru_direct
    map gw_route { type ipv4_addr : mark; elements = { 10.77.1.10 : 0x102, ... } }                     V
    map gw_rx    { type ipv4_addr : mark; elements = { 10.77.1.12 : 0x1c9 } }  # T15 Q2: only with a Russian group —
                                                                     the client -> the mark of its Russian target
    chain gw_pre { type filter hook prerouting priority -150; policy accept;                          V
        iifname "lo" return                                                  the node's own packets
        iifname "kgw-*" ip saddr != @gw_nets drop                            client interfaces carry clients
        iifname "kgw-*" fib saddr . iif oif missing drop                     ... each with its own address
        ip saddr 10.77.1.0/24 iifname != "kgw-wg" drop                       a subnet only from its inbound
        ip saddr 10.77.5.0/24 iifname != "kgw-l2tp*" iifname != "kgw-sstp*" drop     (PPP: one pool)
        ip saddr 10.77.8.0/24 meta ipsec missing drop                        IKEv2: only out of IPsec
        ip daddr @gw_nets iifname != "kgw-*" meta ipsec missing drop         the base is closed from outside
        ip saddr != @gw_nets return                                                                   V
        ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport 53 ct mark set ct mark or 0x4b000000 return
        ip daddr 10.77.0.1 icmp type echo-request ct mark set ct mark or 0x4b000000 return    ping of the gateway
        ip saddr != @gw_live drop                                            route «block»: DNS and ping above only
        ip saddr @gw_ruonly fib daddr type local drop                        stage 1: Russia only, not the node
        fib daddr type local meta mark set 0x202 ct mark set ct mark or 0x202 return    the node itself
        ip daddr @gw_nets return                                             other clients: gw_fwd drops
        ct state established,related ct mark and 0xffff == 0x200 meta l4proto udp meta mark set 0x200
            tproxy to 127.0.0.1:12345 accept
        ct state established,related ct mark and 0xffff == 0x301 meta l4proto udp meta mark set 0x301
            tproxy to 127.0.0.1:12401 accept                                 (one per VLESS exit)
        ct state established,related meta mark set ct mark and 0xffff return                          V
        meta mark set 0x2ff
        meta mark set ip saddr map @gw_route                                                          V
        meta mark 0x2fe meta mark set numgen random mod 3 map { 0 : 0x101, ... }    «по соединениям» only (§4.5)
        meta mark 0x2fe meta mark set 0x2ff                  always (B1, SEC-s1-t45 С-1): 0x2fe never goes on
        ip saddr @gw_rd ip daddr . meta l4proto @gw_ru ip daddr != @gw_force meta mark set 0x201
                                                                             wave 3: Russia directly
        ip saddr @gw_rd ip daddr @gw_ru_dns ip daddr != @gw_force meta mark set 0x201
        ip daddr . meta l4proto @gw_ru ip daddr != @gw_force meta mark set ip saddr map @gw_rx
                                     T15 Q2: Russia through the Russian group (a miss keeps the mark of the route)
        ip daddr @gw_ru_dns ip daddr != @gw_force meta mark set ip saddr map @gw_rx
        ct mark set meta mark or 0x4b000000                                                           V
        meta mark 0x200 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12345                           V
        meta mark 0x301 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12401 }  (one per VLESS exit)
    chain gw_fwd { type filter hook forward priority -4; policy accept;                               V
        ip saddr @gw_nets meta mark 0x2ff drop                                                        V
        ip saddr @gw_nets ct mark and 0xffff == 0x2ff drop
        ip saddr @gw_nets ct mark and 0xffff == 0x200 drop
        ip saddr @gw_nets ct mark and 0xff00 == 0x300 drop
        ip saddr @gw_nets ct mark and 0xff00 == 0x100 oifname != "kawg*" drop
        ip saddr @gw_nets ip daddr != @gw_nets ct mark and 0xffff == 0x0 drop
        ip saddr @gw_nets ct mark and 0xffff == 0x2fe drop                  always (B1, С-1): no ip rule for 0x2fe
        ip daddr 10.77.0.1 drop
        ip saddr @gw_nets ip daddr @gw_nets drop
        ip saddr @gw_nets ip daddr @gw_priv ct mark and 0xffff != 0x202 drop
        ip saddr @gw_nets tcp flags syn tcp option maxseg size set rt mtu                             V
        iifname "kgw-awg2" tcp flags syn tcp option maxseg size > 1176 tcp option maxseg size set 1176
        oifname "kgw-awg2" ...   (each WireGuard/AmneziaWG inbound and each leg kawgN: its MTU − 40, gw/mtu.py)
        iifname "kawg*" iifname != "kawg1" ... > 1192 ... set 1192 }   a leg the plan does not know yet
    chain gw_out { type filter hook output priority -4; policy accept;
        meta mark and 0xffffff00 == 0x100 oifname != "kawg*" drop
        meta skuid 990 ip daddr 127.0.0.0/8 ct direction original meta l4proto { tcp, udp } th dport != 53 drop
        ip daddr 127.0.0.1 tcp dport { 10851, 10852, 10854, 12699, 12701 } meta skuid != 0 reject }
                                                     stage 1 (§5.2): the node's own inbounds of xray — root only
    chain gw_post { type nat hook postrouting priority 100; policy accept;                            V
        ip saddr @gw_nets oifname != "lo" ip daddr != @gw_nets masquerade }                           V
    table ip6 kaskad6 {
        chain gw_fwd6 { type filter hook forward priority -4; policy accept;
            iifname "kgw-wg" drop ...                       # one rule per iface pattern of the inbounds
            meta ipsec exists drop }                        # F8: IKEv2 on — IPv6 out of IPsec has no kgw-* iface
        chain gw_out6 { type filter hook output priority -4; policy accept;
            meta skuid 990 ip6 daddr ::1 ct direction original meta l4proto { tcp, udp } th dport != 53 drop }
        chain gw_pre6 { type filter hook prerouting priority -150; policy accept;      only with addresses.in6 (F11)
            iifname != "lo" iifname != "kgw-*" ct direction original fib daddr type local
                meta l4proto . th dport { … } ip6 daddr != { chosen IPv6 } drop } }

Why:
  * ``gw_live`` + ``ip saddr != @gw_live drop``: an address that is not allowed out NOW (unknown, its
    device or user disabled or deleted, its route «block» or its exits dead with fail=block) is cut also
    for connections that are already established — ct mark keeps the path of a live connection, but a
    blocked client must not keep one. It stands BEFORE the returns to the node and to other clients: a
    blocked device has DNS 10.77.0.1:53 and the ping of 10.77.0.1 and nothing else (not the node's services, not
    wave-1 forwards, not other clients). The ping (T-N5 3.2.19, №12): without a live exit the device still sees that
    the gateway is there — «нет интернета», not «нет связи»; only echo-request to the DNS address, nothing else
    of the node. ``meta mark set 0x2ff`` before the lookup: a lookup miss leaves the block mark whatever mark
    the packet came with (the verified ``meta mark 0 ...`` assumed 0).
  * spoofing: a client interface (``kgw-*`` — every inbound names its interfaces so) carries only
    addresses of the base, each from the interface its route points to (strict reverse path: a PPP
    client cannot take the address — and the route — of another PPP client); a subnet of an inbound comes
    only from that inbound's interfaces (IKEv2: only out of IPsec); the base is not reachable from any
    other interface (the hoster's network) unless the packet came out of IPsec (IKEv2 clients arrive on
    the external interface). A broken adapter (no interface names) gets ``ip saddr <subnet> drop``.
  * the node itself (``fib daddr type local``): its services and the wave-1 forwards. Mark 0x202 lets such
    packets pass the kill switch (a DNATed forward is routed after prerouting) and marks the connection
    for gw_fwd (a forward to a private target stays allowed, see gw_priv); no 0x4b byte — compat-in does
    not open the node's services to clients (UFW decides, spec §1.2).
  * UDP through xray: established packets returned before the tproxy statement are routed to the local
    stack by mark 0x200 and land in xray's unconnected "fake" UDP socket bound to the original destination
    (it only writes replies) — every UDP packet after the first reply would be lost (QUIC, calls). The
    tproxy statement on established UDP (as xray's own nft example does for every packet) assigns them to
    the listener again. TCP needs nothing: the accepted socket is found by the normal lookup (verified).
    The same pair of rules for each VLESS exit (mark 0x300+N, port 12400+N): a device routed to one VLESS
    exit is an element of gw_route, never an xray restart.
  * backstops on the ct mark (set by gw_pre on the first packet, kept by conntrack; another table's
    mangle chain cannot change it the way it can change meta mark): a forwarded packet of the xray path
    means table 200 is missing; a fast-path connection (256+N) leaving through anything but a kawg leg
    means its ``ip rule`` is gone (the egress removes the rules of a stopped or deleted leg; at boot the
    legs come up after the table) — both would go out DIRECTLY, now they are dropped. A client connection
    with no route bits at all was established while gw_pre was missing (``nft flush ruleset``, a failed
    boot load): dropped too (the watchdog also flushes such connections from conntrack when it puts the
    part back). The same for the xray of the gateway itself (gw_out: its AmneziaWG outbounds are sockets
    with mark 256+N). The DNS address of the gateway is never forwarded (kgw0 missing would send client
    queries to the provider).
  * clients never reach each other (base -> base is never forwarded; the node's own addresses are local)
    nor private, CGNAT, link-local or multicast destinations (the hoster's network and its gateway, cloud
    metadata) — only a wave-1 forward of the node may point there (mark 0x202).
  * gw_out/gw_out6: sockets of xray's user (``kaskad-xray``) open nothing on the loopback but DNS — the
    node's own services (xray's metrics, accel-ppp's CLI, ...) stay closed to SOCKS5 clients even if xray
    let such a destination through (defense in depth for xray's own block of private destinations). Replies
    of xray's listeners (dnsmasq -> 127.0.0.1:5353, the metrics) are the reply direction: not matched.
  * ``tproxy to`` (not ``tproxy ip to`` of the spec text): the form verified by gwcheck.sh in an ``ip`` table.
  * «Russia directly» (wave 3, docs/k3/SPEC-wave3.md §2; the lists — ``kaskad.rules``): after the route mark is
    chosen and before TPROXY, a NEW connection of a client in gw_rd (profile ru_direct, lists present) to a
    Russian network (gw_ru — geoip ``direct`` of RoscomVPN, ~15 k ranges) or to an address dnsmasq resolved for a
    «direct» domain (gw_ru_dns) goes directly (0x201) — unless the address belongs to an «always through the
    exit» domain (gw_force: YouTube on a Russian cache, Telegram, GitHub ...). A device blocked by choice is never
    in gw_rd (``rules.apply.rd``); stage 1 dropped the old ``meta mark != 0x2ff``: the fallback block of a group (its
    exits unfit) keeps Russia directly on the kernel's inbounds too, as on xray's (§2.3, Б1); such a device is in
    gw_live only for that (gw_ruonly: no node services). Established connections keep their mark (ct mark).
    No lists -> gw_rd is empty, nothing goes directly (fail-closed).
  * gw_ru's type is ``RU_FORM``, measured on the stand (Ubuntu 24.04, kernel 6.8, nft 1.0.9, 1 vCPU,
    tools/rules_nft_bench.sh, 14 963 ranges): "concat" — ``ipv4_addr . inet_proto`` with intervals, elements
    ``a.b.c.d/n . 0-255``: load 0.7 s, dnsmasq's ``add element`` 4.9 ms (as without the set), but ``nft -c`` of
    the ranges 100 s; "plain" — ``ipv4_addr``, interval, auto-merge: load 0.16 s, but every ``add element`` 53 ms
    (libnftables dumps every plain interval set before it: each DNS answer of a listed domain would cost that,
    dnsmasq is single-threaded). So concat, and its ranges never meet ``nft -c``.
  * The ranges never go into an apply: render_update and render_full (the boot file) only declare gw_ru and
    gw_ru_ver; ``load_ru`` fills them in a transaction of their own (``nft -f`` with RU_LOAD_TIMEOUT, no dry run;
    flush + chunks of RU_CHUNK + gw_ru_ver, the version of the lists) — when ``kaskad.rules.apply.ensure_kernel``
    sees gw_ru_ver differ from the store: after an update of the lists and from kaskad-gw-rules-load.timer (at
    boot after kaskad-net, then every 5 min: a no-op while they match, a pause after a failure). Device changes,
    ``gw apply`` and the watchdog (``net check --repair``) never touch them. Until a load succeeds gw_ru is empty:
    the gateway and the forwards work, nobody goes directly by network (fail-closed). A gw_ru of another type (3.1
    had a plain one) is deleted and created again by render_update, after the chain that uses it is flushed.
  * gw_ru_dns / gw_force are filled by dnsmasq only (``nftset=/<domain>/4#ip#kaskad#gw_ru_dns``); an apply never
    flushes them (they would empty until clients ask again); their elements age out after 6 h.

Routing (``ensure_routing``, not persistent — the watchdog restores it at boot and every minute; the
collector every 10 s): ``local 0.0.0.0/0 dev lo`` + ``throw <base>`` in table 200, ``ip link add kgw0 type
dummy`` + ``10.77.0.1/32`` + up, and ONE BLOCK of ip rules with pinned prefs (``place_rules``; 4000.. unless
a foreign rule pushes it, see the notes there), the kill switch among them — it does not depend on nft::

    4000: fwmark 0x10000/0x10000 lookup main            replies to the clients (and their reverse path check)
    4001: to 10.77.0.0/16 lookup main                   the node / replies -> clients, the clients' source check
    4002: fwmark 0x200 lookup 200                       the pool through xray (TPROXY)
    4003: fwmark 0x300/0xff00 lookup 200                the VLESS exits (TPROXY)
    4004: from 10.77.0.0/16 fwmark 0x0/0xffff lookup 199     an UNMARKED client packet ...
          (table 199: unreachable default)                  ... never leaves the node
    4004+N: fwmark 256+N lookup 256+N                   the rule of AmneziaWG leg N (install.sh), moved here

gw_pre marks every client packet that leaves the node (route marks 0x1xx/0x2xx/0x3xx, 0x202 for the node
and its forwards); one it did not mark — the gw part is missing — finds only ``unreachable``. Packets to
the node (local table, pref 0) and to addresses of the base are not affected; marked packets skip the kill
switch. Tables 256+N and the rules' contents belong to the legs (``gokaskad --egress-awg-up``; kept whole by
``--egress-awg-heal``); the watchdog only moves a leg's rule into its slot. First of all
systemd-networkd's drop-in 90-kaskad.conf (``sys.networkd_keep_foreign``): without it every restart of
networkd (each upgrade of the systemd package) removes the rules 0x200/0x300, the kill switch and the
rules of the legs; ``release_networkd`` takes it away with the last of our policy routing.

Nothing user-typed reaches the nft text: addresses are validated IPv4 inside the base, marks come from
``exits`` constants, interface patterns of adapters are checked against ``_IFACE``; a broken adapter or
broken state is skipped (that address is blocked), never allowed to stop the table — wave-1 forwards live
in it too (``bad_data`` tells ``gw check``).
"""

import hashlib
import ipaddress
import json
import os
import pwd
import re
import time

from .. import nft, paths, util
from ..errors import KaskadError
from ..i18n import tr, trn
from ..rules import apply as rules_apply
from . import mtu as gwmtu
from . import addr, exits, model, ruguard
from . import sys as gsys

CHAINS = ("gw_pre", "gw_fwd", "gw_out", "gw_post", "gw_dns")          # F2: gw_dns — dns_dnat_rules
RU = rules_apply.SET_RU             # networks of Russia (static, from the lists; loaded by load_ru)
RU_DNS = rules_apply.SET_RU_DNS     # addresses of «direct» domains (dnsmasq)
FORCE = rules_apply.SET_FORCE       # addresses of «always through the exit» domains (dnsmasq)
RD = rules_apply.SET_RD             # clients with profile ru_direct
RU_VER = rules_apply.SET_RU_VER     # which lists gw_ru holds (one element)
RUONLY = "gw_ruonly"                # stage 1 (§2.3, Б1): in gw_live only for Russia (fallback block + «Россия напрямую»)
XPORTS = "gw_xports"                # stage 1 (§5.2): the ports of xray's own inbounds root alone may open (gw_out)
SETS = ("gw_nets", RU, "gw_live", "gw_priv", RU_DNS, FORCE, RD, RU_VER, RUONLY, XPORTS)
MAP = "gw_route"
RX = "gw_rx"                        # Q2 (SPEC-groups §3): client -> the mark of its Russian target; only with a Russian group
LIVE = "gw_live"
PRIV = "gw_priv"
RU_FORMS = ("concat", "plain")
RU_FORM = "concat"                  # the stand (module notes): concat keeps dnsmasq's add element at 5 ms
RU_SPECS = {"concat": "type ipv4_addr . inet_proto; flags interval;",
            "plain": "type ipv4_addr; flags interval; auto-merge;"}
RU_TYPES = {"concat": ["ipv4_addr", "inet_proto"], "plain": "ipv4_addr"}       # as ``nft -j`` prints them
RU_CHUNK = 5000                     # elements per ``add element`` line (one transaction all the same)
RU_LOAD_TIMEOUT = 30                # s for load_ru (the stand: 0.7 s for 15 k ranges on 1 vCPU)
DYN_TIMEOUT = "6h"                  # elements of gw_ru_dns / gw_force (dnsmasq gives none of its own)
HOOKS = {
    "gw_pre": "type filter hook prerouting priority -150; policy accept;",
    "gw_fwd": "type filter hook forward priority -4; policy accept;",
    "gw_out": "type filter hook output priority -4; policy accept;",
    "gw_post": "type nat hook postrouting priority 100; policy accept;",
    "gw_dns": "type nat hook prerouting priority -102; policy accept;",      # F2: dns_dnat_rules
}
SET_SPECS = {"gw_nets": "type ipv4_addr; flags interval;", RU: RU_SPECS[RU_FORM],
             "gw_live": "type ipv4_addr;", "gw_priv": "type ipv4_addr; flags interval;",
             RU_DNS: "type ipv4_addr; flags timeout; timeout %s;" % DYN_TIMEOUT,
             FORCE: "type ipv4_addr; flags timeout; timeout %s;" % DYN_TIMEOUT,
             RD: "type ipv4_addr;", RU_VER: "type ipv4_addr;", RUONLY: "type ipv4_addr;",
             XPORTS: "type inet_service;"}
# destinations no client may reach (gw_fwd) and xray opens for nobody (xray.py): this host, private
# networks, CGNAT, link-local (cloud metadata), multicast and the reserved rest
PRIVATE = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
           "192.168.0.0/16", "224.0.0.0/3")
PRIVATE6 = ("::1/128", "fc00::/7", "fe80::/10")
LOOPBACK = "127.0.0.0/8"
LOOPBACK6 = "::1"
DNS_PORT = 53
TABLE6_FAMILY = "ip6"
TABLE6_NAME = "kaskad6"
TABLE6 = TABLE6_FAMILY + " " + TABLE6_NAME
CHAIN6 = "gw_fwd6"
HOOK6 = "type filter hook forward priority -4; policy accept;"
CHAIN6_OUT = "gw_out6"
HOOK6_OUT = "type filter hook output priority -4; policy accept;"
CHAIN6_PRE = "gw_pre6"              # 3.3.7.15 F11: «Приём» by IPv6 (gw/mip.py pre6_rules) — only while in6 is chosen
HOOK6_PRE = "type filter hook prerouting priority -150; policy accept;"
TPROXY_ADDR = "127.0.0.1"
TPROXY_PORT = 12345
ROUTE_TABLE = 200
KILL_TABLE = 199                    # the kill switch: ``unreachable default`` for unmarked client packets
# Replies of the clients' connections (from the internet, the AWG legs) carry REPLY_MARK (gw_pre) and go to
# main by the first rule of our block (``place_rules``). Needed because of reverse path filtering (Ubuntu:
# rp_filter=2): the kernel validates the source of a reply with the lookup «from <client> to <source>», which
# would hit the kill switch and be dropped (stand, Ubuntu 26.04: route direct and the AWG exits were dead).
# src_valid_mark=1 makes that lookup carry the packet's mark.
REPLY_MARK = 0x10000
SRC_VALID_MARK = "net.ipv4.conf.all.src_valid_mark"
# With src_valid_mark=1 the source check of a CLIENT packet marked for TPROXY (0x200, 0x3xx) looks up table 200
# too; ``local default`` there makes the client's own address «local» -> a martian, dropped. ``throw <base>``
# sends that lookup on to main (the route to the client). Stand, Ubuntu 26.04: the VLESS exits went dead.
DUMMY = "kgw0"
PROBE_TABLE = "kaskad_gwprobe"
PROBE_TABLE6 = "kaskad6_gwprobe"
LEG_IFACES = "kawg*"
CLIENT_IFACES = "kgw-*"             # every inbound names its client interfaces kgw-...
IPSEC_KINDS = ("ikev2",)            # clients without an interface: their packets come out of IPsec
XRAY_USER = "kaskad-xray"           # the system user kaskad-gw-xray.service runs as (xray.ensure_user)
# «по соединениям» (stage 1, §4.5): a device of «Основная» carries this mark in gw_route; numgen spreads each NEW
# connection over the good exits of the active tier — AmneziaWG by their marks, xray by 0x200 (gw-vless). Not among
# _ALLOWED_MARKS: a gw_route element with it is dropped (blocked) until the plan says the mode is on (T4, ``spread``)
MARK_SPREAD = 0x2fe
_IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}\*?$")       # IFNAMSIZ - 1
_ALLOWED_MARKS = set([exits.MARK_XRAY, exits.MARK_DIRECT, exits.MARK_BLOCK] +
                     list(range(exits.AWG_MARK_BASE + 1, exits.AWG_MARK_BASE + exits.MAX_AWG_INDEX + 1)) +
                     list(range(exits.VLESS_MARK_BASE + 1, exits.VLESS_MARK_BASE + exits.MAX_VLESS_INDEX + 1)))

# gw_rx (Q2): «напрямую» of the last step, the block of the last step, the marks of the Russian exits — nothing else
RX_MARKS = frozenset([exits.MARK_DIRECT, exits.MARK_BLOCK] +
                     [exits.AWG_MARK_BASE + exits.RU_INDEX_BASE + i for i in range(1, exits.RU_MAX + 1)] +
                     [exits.VLESS_MARK_BASE + exits.RU_INDEX_BASE + i for i in range(1, exits.RU_MAX + 1)])

_getpwnam = pwd.getpwnam            # tests patch this


# ── nft spelling of the gateway part ───────────────────────────────────────

def _hex(n):
    return "0x%x" % n


def xray_uid():
    """uid of the system user xray of the gateway runs as, None while there is no such user."""
    try:
        uid = int(_getpwnam(XRAY_USER).pw_uid)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return uid if uid > 0 else None


def plan(st, runner=None, ctx=None, decision=None, ports=True):
    """What the gateway part depends on besides the state: {"elems": {client ip: mark} (gw_route),
    "vless": [(mark, port)] (VLESS exits with their own TPROXY), "uid": xray's uid or None, "rd": [client ip]
    (gw_rd: profile ru_direct, lists present), "ruonly": [client ip] (stage 1, §2.3, Б1: in gw_live only for Russia —
    the fallback block of its group and «Россия напрямую»)} + "spread" (the marks of «по соединениям», §4.5; only while
    a device has it) and "xray_ports" (the node's own inbounds of xray, root only — §5.2; with the xray of stage 1, T5);
    the networks of Russia are not part of it (``load_ru``). Never raises because of the pool (an unreadable pool = no
    live exits). ``decision`` — the decision of the collector's tick in memory (§2.7); None — ``exits.decision``
    (pins.json and the snapshot). ``ctx`` (one nft.apply) keeps the first answer, so the script, its verification and
    the boot file see the same. ``ports`` False — no ``xray_ports`` (``update_map``: only the sets change)."""
    if ctx is not None and "gw_plan" in ctx:
        return ctx["gw_plan"]
    from . import hooks
    pool_exits, snap = exits.current(st, runner)
    leases = hooks.leases_for(st)
    dec = exits.decision(st, snap, pool_exits) if decision is None else decision
    tg = exits.targets(st, dec, leases=leases)
    rd = rules_apply.rd(st, leases)
    p = {"elems": dict((ip, t["mark"]) for ip, t in tg.items()),
         "vless": sorted((e["mark"], exits.tproxy_port(e)) for e in exits.xray_exits(st, pool_exits)),
         "uid": xray_uid(), "rd": rd, "ruonly": ruonly_of(tg, rd)}
    if ru_groups(dec.get("policy")):                    # Q2 (§3): a node without a Russian group — no gw_rx at all
        rx = rules_apply.rx(st, dec, leases)
        p["rx"] = rx
        p["rx_home"] = rx_home(st, dec.get("policy"), pool_exits, leases)    # REV-s1-q2-2 З-1: ``ru_revoked``
        p["ruonly"] = sorted(set(p["ruonly"]) | set(rx_ruonly(tg, rx)), key=_ip_sort)
    if any(t["mark"] == exits.MARK_SPREAD for t in tg.values()):
        p["spread"] = spread_marks(st, dec, pool_exits)
    if ports:
        got = _xray_ports(st, pool_exits, dec)
        if got is not None:
            p["xray_ports"] = got
    p["mip"] = mip_plan(st, pool_exits)
    if ctx is not None:
        ctx["gw_plan"] = p
    return p


def mip_plan(st, pool_exits):
    """PLAN-multi-ip Ш3/Ш4 (gw/mip.py): the chosen addresses of the inbounds, SNAT of the legs and of Н4 — from the state
    and the cache of «Адреса сервера» (files only). A failure — nothing of it (the table of 3.3.7.12), never a failure
    of the plan."""
    from . import mip
    try:
        return mip.plan(st, pool_exits)
    except Exception:                                   # noqa: BLE001 — the addresses never break the gateway part
        return {}


def ru_groups(policy):
    """The policy has a Russian group (role «ru») — only then the table has gw_rx and its two rules (Q2, §3)."""
    return any(isinstance(g, dict) and g.get("role") == "ru" for g in (policy or {}).get("groups") or [])


def _ip_sort(ip):
    try:
        return (0, int(ipaddress.IPv4Address(ip)))
    except ValueError:
        return (1, str(ip))


def rx_ruonly(targets, rx):
    """The part of gw_ruonly of gw_rx (§3, SE §2.3): an address whose ROUTE is the block of the last step of its group
    (its foreign exits unfit, never a block by choice — those are not in gw_rx) while its Russian target lets it out
    (an exit, «напрямую») — in gw_live for Russia alone, without the node's services."""
    out = []
    for ip, m in sorted((rx or {}).items()):
        t = (targets or {}).get(ip)
        if m != exits.MARK_BLOCK and t is not None and t.get("mark") == exits.MARK_BLOCK \
                and t.get("step") == "last_block":
            out.append(ip)
    return out


def ruonly_of(targets, rd):
    """gw_ruonly (§2.3, Б1): the addresses of gw_rd whose route mark is the block of the LAST STEP of their group (its
    exits unfit) — never a block by choice (a route «block», a pause, a device or person off: those are not even in
    gw_rd, ``rules.apply.rd``; checked here too). They are in gw_live for Russia alone, without the node's services."""
    out = []
    for ip in rd or []:
        t = (targets or {}).get(ip)
        if t is not None and t.get("mark") == exits.MARK_BLOCK and t.get("step") in ("last_block",):
            out.append(ip)
    return out


def spread_marks(st, dec, pool_exits):
    """«по соединениям» of «Основная» (§4.5): the marks of its good exits of the active tier — an AmneziaWG one by its
    mark, an xray one as 0x200 (gw-vless), once per exit; none good — [] (the device is blocked)."""
    from . import assign, policy as P
    pol = dec.get("policy") or P.load(st, pool_exits)
    grp = P.group(pol, P.MAIN)
    cands = assign.spread_cands(dec.get("states") or {}, grp)
    rows = dict((e["id"], e) for e in pool_exits)
    out = []
    for eid in cands:
        e = rows.get(eid)
        if e is None or not e.get("mark"):
            continue
        out.append(exits.MARK_XRAY if exits.is_xray(e) else e["mark"])
    return out


def _xray_ports(st, pool_exits, dec):
    """The ports of the node's own inbounds of xray (``xray.guarded_ports`` of the config ``xray.render`` draws by the
    same decision, T5) — not of the file on the disk: ``nft.apply`` runs before ``xray.apply`` writes it, and the rule
    of gw_out would lag the config (``verify``: «в цепочке gw_out правил 2, а нужно 3»). None — no xray needed here
    (``xray.needed``: no rule); no config to draw (the secrets unreadable, a render failing) — ``xports_closed``
    (SEC-s1-t45 З-1)."""
    from . import xray
    try:
        from .. import secrets
        if not xray.needed(st, secrets.load(), pool_exits):
            return None
        cfg = xray.render(st, secrets.load(), pool_exits, (), dec)
    except (KaskadError, OSError, ValueError, KeyError, TypeError):
        return xports_closed()                         # SEC-s1-t45 З-1: no render — every port of xray closed
    return xray.guarded_ports(cfg) if isinstance(cfg, dict) else xports_closed()


def xports_closed():
    """Every port an xray of stage 1 may open on 127.0.0.1 for itself (§5.2): the metrics, the API, the pool inbound and
    the whole range of ``probe-<id>`` (12700 + 0…MAX_VLESS_INDEX) — gw_xports when the config cannot be drawn (the
    secrets unreadable, a render failing): xray goes on with the config on the disk, its ports stay closed to the
    users of the node (SEC-s1-t45 З-1), never shrunk to the API of the bot alone."""
    from . import xray
    return sorted(set([xray.METRICS_PORT, xray.STATS_PORT, xray.POOL_SOCKS_PORT]) |
                  set(range(xray.PROBE_PORT_BASE, xray.PROBE_PORT_BASE + exits.MAX_VLESS_INDEX + 1)))


# ── «Russia directly» (wave 3): gw_ru, gw_rd, gw_ru_ver ──────────────────────

def ru_form():
    return RU_FORM if RU_FORM in RU_FORMS else "concat"


def set_spec(name):
    """The declaration of a set (gw_ru follows RU_FORM)."""
    return RU_SPECS[ru_form()] if name == RU else SET_SPECS[name]


def ru_version(sha, form=None):
    """The element of gw_ru_ver for the ranges file ``sha`` in the form ``form``: never 0 (0 = nothing loaded)."""
    return int(hashlib.sha256(("%s:%s" % (form or ru_form(), sha)).encode("ascii")).hexdigest()[:8], 16) or 1


def ru_wanted():
    """The version the store asks for (0 without lists) — from the meta of the lists, the ranges are not read."""
    k = rules_apply.kernel_meta()
    return ru_version(k["sha256"]) if k["ready"] else 0


def ru_plan():
    """{"form", "ver", "ranges"} of the lists now (ver 0 and no ranges without lists or when the ranges file does
    not read)."""
    k = rules_apply.kernel()
    return {"form": ru_form(), "ver": ru_version(k["sha256"]) if k["ready"] else 0,
            "ranges": list(k["ranges"]) if k["ready"] else []}


def ru_rules(form=None):
    """The two rules of gw_pre: a NEW connection of a ru_direct client to a Russian network or to an address of a
    «direct» domain goes directly, unless the address is of an «always through the exit» domain. No ``meta mark !=
    0x2ff`` since stage 1 (§2.3, Б1): a device blocked by choice is never in gw_rd (``rules.apply.rd``), and the
    fallback block of a group (exits unfit) must not close Russia — on the kernel's inbounds as on xray's."""
    match = ("ip daddr . meta l4proto @%s" if (form or ru_form()) == "concat" else "ip daddr @%s") % RU
    tail = "ip daddr != @%s meta mark set %s" % (FORCE, _hex(exits.MARK_DIRECT))
    return ["ip saddr @%s %s %s" % (RD, match, tail), "ip saddr @%s ip daddr @%s %s" % (RD, RU_DNS, tail)]


def rx_rules(p, form=None):
    """The two rules of gw_rx (Q2, §3), right after those of gw_rd: a NEW connection to a Russian network or to an
    address of a «direct» domain (never of «always through the exit») takes the mark of the Russian target of its
    client. A client out of gw_rx keeps the mark of its route (a miss of the map ends the rule, as for gw_route). No
    ``meta mark != 0x2ff`` on purpose (Б1, D-039). [] without gw_rx in the plan (no Russian group)."""
    if (p or {}).get("rx") is None:
        return []
    match = ("ip daddr . meta l4proto @%s" if (form or ru_form()) == "concat" else "ip daddr @%s") % RU
    tail = "ip daddr != @%s meta mark set ip saddr map @%s" % (FORCE, RX)
    return ["%s %s" % (match, tail), "ip daddr @%s %s" % (RU_DNS, tail)]


def rx_elements(st, p, check=True):
    """gw_rx of the plan, validated: [(ip, mark)] — IPv4 inside the base, a mark of RX_MARKS, sorted; nothing while
    «Россия напрямую» is not allowed (D-136, the last line — as gw_rd). ``check`` False — the syntax probe."""
    b = addr.base(st)
    got = {}
    for ip, m in ((p or {}).get("rx") or {}).items():
        try:
            a = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if a in b and m in RX_MARKS:
            got[str(a)] = m
    ips = sorted(got, key=_ip_sort)
    ips = ruguard.rd(st, ips) if check else ips
    return [(ip, got[ip]) for ip in ips]


def rx_text(st, p, check=True):
    return ", ".join("%s : %s" % (ip, _hex(m)) for ip, m in rx_elements(st, p, check))


def _rx_refill(T, st, p):
    out = ["flush map %s %s" % (T, RX)]
    t = rx_text(st, p)
    if t:
        out.append("add element %s %s { %s }" % (T, RX, t))
    return out


def ru_element(rng, form=None):
    """One range of the lists as an element of gw_ru."""
    return ("%s . 0-255" % rng) if (form or ru_form()) == "concat" else rng


def rd_addresses(st, p, check=True):
    """gw_rd of the plan, validated: IPv4 inside the base, sorted (nothing else reaches the nft text). ``check`` False
    — the syntax probe of a twin table (``probe``: a sample address, never applied) skips the last line of D-136."""
    b = addr.base(st)
    out = set()
    for ip in (p or {}).get("rd") or []:
        try:
            a = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if a in b:
            out.add(a)
    # D-136 (B7 круг 2): the last line — no address in gw_rd / gw_ruonly while «Россия напрямую» is not allowed
    ips = [str(a) for a in sorted(out)]
    return ruguard.rd(st, ips) if check else ips


def _ru_type_ok(info, form):
    """The live gw_ru has the type of ``form``. A type printed in a form we do not know counts as right: a
    migration is never started on a guess."""
    t = (info or {}).get("type")
    if isinstance(t, list):
        t = " . ".join(str(x) for x in t)
    if not isinstance(t, str):
        return True
    want = RU_TYPES[form]
    return " ".join(t.split()) == (" . ".join(want) if isinstance(want, list) else want)


def ru_version_elem(ver):
    """gw_ru_ver holds the version as an IPv4 address (``type ipv4_addr``: the element form of ``nft -j`` the gateway
    already reads — gw_live): 0x1a2b3c4d -> 26.43.60.77."""
    return str(ipaddress.IPv4Address(ver))


def _version_of(elems):
    got = set(_addr_text(x) for x in elems or [])
    if not got:
        return 0
    if len(got) != 1:
        return None
    try:
        return int(ipaddress.IPv4Address(next(iter(got))))
    except ValueError:
        return None


def live_ru_version(live):
    """The version gw_ru_ver holds in a ``nft.read_live`` result: an int, 0 when the set is empty, None when it is
    missing or holds something else."""
    info = ((live or {}).get("set_info") or {}).get(RU_VER)
    return None if info is None else _version_of(info.get("elem"))


def read_ru_version(runner):
    """The version gw_ru_ver holds, read alone (``nft -j list set``: never the whole table, never gw_ru's 15 k
    elements): int, 0 when empty, None when the set or the table is missing. KaskadError when nft fails otherwise."""
    res = runner.run([nft.NFT, "-j", "list", "set", nft.TABLE_FAMILY, nft.TABLE_NAME, RU_VER])
    if not res.ok:
        if res.rc != 127 and "No such file or directory" in (res.err or "") + (res.out or ""):
            return None
        raise KaskadError(tr("gw_net.err.set_read", set=RU_VER, error=(res.err or res.out or "").strip()[:200]))
    try:
        doc = json.loads(res.out)
    except ValueError:
        raise KaskadError(tr("gw_net.err.not_json"))
    for it in doc.get("nftables") or [] if isinstance(doc, dict) else []:
        s = it.get("set") if isinstance(it, dict) else None
        if isinstance(s, dict) and s.get("name") == RU_VER:
            return _version_of(s.get("elem"))
    return None


def ruonly_addresses(st, p):
    """gw_ruonly of the plan (``p["ruonly"]`` — T4: in gw_live only for Russia), validated as gw_rd."""
    return rd_addresses(st, {"rd": (p or {}).get("ruonly") or []})


def _ruonly_refill(T, ips):
    out = ["flush set %s %s" % (T, RUONLY)]
    if ips:
        out.append("add element %s %s { %s }" % (T, RUONLY, ", ".join(ips)))
    return out


def xports_of(p):
    """The elements of gw_xports (§5.2, REV-s1-t5 З3): the ports of the node's own inbounds of xray drawn by the plan's
    decision (``p["xray_ports"]`` — ``xray.guarded_ports``) and the API of the bot's xray (10851) — the whole range
    12700–12955 only when no config can be drawn (``xports_closed``, SEC-s1-t45 З-1), never 10853 (dnsmasq). A set,
    not a rule text: a hot change of the pool adds to it in the transaction of gw_route/gw_live (``update_map``) BEFORE
    xray opens a new ``probe-<id>``; a port leaves it only after xray dropped its inbound (``xports_settle``)."""
    from . import xray
    got = (p or {}).get("xray_ports") or []
    return sorted(set(int(x) for x in got if isinstance(x, int) and not isinstance(x, bool) and 0 < x < 65536)
                  | {xray.EGRESS_API_PORT})


def _xports_refill(T, ports):
    out = ["flush set %s %s" % (T, XPORTS)]
    if ports:
        out.append("add element %s %s { %s }" % (T, XPORTS, ", ".join(str(x) for x in ports)))
    return out


def _xports_add(T, ports):
    """gw_xports is only ADDED to by the table (``update_lines``, ``update_map``): a port goes out of it only after
    xray dropped its inbound (``xports_settle`` of ``xray.apply``) — «сначала закрыть, потом открыть» (REV-s1-t45 П2);
    an extra port in it is closed to the users of the node, nothing more."""
    return ["add element %s %s { %s }" % (T, XPORTS, ", ".join(str(x) for x in ports))] if ports else []


def _xports_live(runner):
    """The elements of gw_xports in the live table (one small listing) — None: no such set (a table before stage 1, no
    table) or nft failed."""
    res = runner.run([nft.NFT, "-j", "list", "set", nft.TABLE_FAMILY, nft.TABLE_NAME, XPORTS])
    if not res.ok:
        return None
    try:
        live = nft.parse_live(json.loads(res.out))
    except (KaskadError, ValueError, TypeError, AttributeError):
        return None
    return set_elems(live, XPORTS) if XPORTS in (live.get("set_info") or {}) else None


def _xports_set(runner, ports, exact):
    want = xports_of({"xray_ports": list(ports or [])})       # + the API of the bot, validated
    have = _xports_live(runner)
    if have is None:
        return False
    if exact:
        if have == set(str(x) for x in want):
            return True
        lines = _xports_refill(nft.TABLE, want)
    else:
        lines = _xports_add(nft.TABLE, [x for x in want if str(x) not in have])
        if not lines:
            return True
    return runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(lines) + "\n").ok


def xports_add(runner, ports):
    """The ports of a config xray is about to run into gw_xports (``xray.apply``, under the xray lock, BEFORE the new
    config): closed before they open. Written only when one is missing. False — no such set (a table before stage 1)
    or nft failed."""
    return _xports_set(runner, ports, False)


def xports_settle(runner, ports):
    """gw_xports = exactly the ports of the config xray runs now (+ the API of the bot) — ``xray.apply`` after it
    applied one (or stopped xray: ``ports`` empty), under the xray lock: the ports xray dropped leave the set only now
    (REV-s1-t45 П2). Written only when it differs. False — no such set or nft failed (the set stays wider: closed)."""
    return _xports_set(runner, ports, True)


def _rd_refill(T, ips):
    out = ["flush set %s %s" % (T, RD)]
    if ips:
        out.append("add element %s %s { %s }" % (T, RD, ", ".join(ips)))
    return out


def ru_load_lines(table, ru):
    """The load of the networks of Russia: gw_ru emptied and filled (chunks of RU_CHUNK), then gw_ru_ver says which
    lists it holds — one transaction."""
    els = [ru_element(r, ru.get("form")) for r in ru["ranges"]]
    out = ["flush set %s %s" % (table, RU)]
    out += ["add element %s %s { %s }" % (table, RU, ", ".join(els[i:i + RU_CHUNK]))
            for i in range(0, len(els), RU_CHUNK)]
    out.append("flush set %s %s" % (table, RU_VER))
    if ru["ver"]:
        out.append("add element %s %s { %s }" % (table, RU_VER, ru_version_elem(ru["ver"])))
    return out


def load_ru(runner, ru=None, timeout=RU_LOAD_TIMEOUT):
    """Fill gw_ru and gw_ru_ver from the store in a transaction of their own — ``nft -f`` only, never ``nft -c``
    (a dry run of 15 k concatenated ranges took 100 s on the stand, the load 0.7 s; the transaction is atomic: a
    refused one changes nothing). Never part of an apply. Returns {"ok", "ver", "elements", "seconds", "error"};
    ``kaskad.rules.apply.ensure_kernel`` decides when to try and logs."""
    ru = ru_plan() if ru is None else ru
    t0 = time.monotonic()
    res = runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(ru_load_lines(nft.TABLE, ru)) + "\n",
                     timeout=timeout)
    out = {"ok": res.ok, "ver": ru["ver"], "elements": len(ru["ranges"]),
           "seconds": round(time.monotonic() - t0, 2), "error": ""}
    if not res.ok:
        out["error"] = tr("gw_net.err.nft_timeout", sec=timeout) if res.rc == 124 else \
            ((res.err or res.out or tr("gw_net.code", rc=res.rc)).strip().splitlines() or [""])[0][:300]
    return out


def _vless(p):
    """(mark, port) of the VLESS exits, validated: marks 0x301..0x3ff, ports 1..65535."""
    out = []
    for mark, port in (p or {}).get("vless") or []:
        if isinstance(mark, int) and exits.VLESS_MARK_BASE < mark <= exits.VLESS_MARK_BASE + exits.MAX_VLESS_INDEX \
                and isinstance(port, int) and 0 < port < 65536:
            out.append((mark, port))
    return sorted(set(out))


def _uid(p):
    uid = (p or {}).get("uid")
    return uid if isinstance(uid, int) and not isinstance(uid, bool) and uid > 0 else None


def _subnet_sources(st):
    """{subnet: {"ifaces": set of patterns, "ipsec": bool}} of the enabled inbounds that hand out addresses."""
    names = {}
    for kind, name in _adapter_ifaces(st):
        names.setdefault(kind, []).append(name)
    out = {}
    for kind in model.enabled_kinds(st):
        if kind not in addr.KIND_SUBNET:
            continue                                   # SOCKS5: a proxy, no addresses
        for pool in addr.KIND_POOLS.get(kind) or (addr.KIND_SUBNET[kind],):
            try:
                net = addr.subnet(pool, st)
            except KaskadError:
                continue
            rec = out.setdefault(net, {"ifaces": set(), "ipsec": False})
            if kind in IPSEC_KINDS:
                rec["ipsec"] = True
            for n in names.get(kind, []):
                if n is not None and _IFACE.match(str(n)):
                    rec["ifaces"].add(str(n))
    return out


def spoof_rules(st):
    """``ip saddr <subnet> iifname != <its interfaces>... [meta ipsec missing] drop`` per subnet of the
    enabled inbounds; a subnet whose interfaces are unknown (a broken adapter) is dropped whole."""
    srcs = _subnet_sources(st)
    out = []
    for net in sorted(srcs, key=lambda s: ipaddress.IPv4Network(s)):
        rec = srcs[net]
        conds = ['iifname != "%s"' % n for n in sorted(rec["ifaces"])]
        if rec["ipsec"]:
            conds.append("meta ipsec missing")
        out.append(" ".join(["ip saddr %s" % net] + conds + ["drop"]))
    return out


def dns_dnat_rules(st):
    """F2. The chain gw_dns (nat prerouting, priority -102 — before the forwards' fwd_pre at -101 and iptables nat
    at -100: a forward or a foreign DNAT of port 53 never takes the clients' DNS): a query of a client (source in
    the base) to the gateway's DNS, 10.77.0.1:53 udp and tcp, goes to dnsmasq of the gateway on its own port
    (gw/dns.py PORT); conntrack gives the answers port 53 back. A foreign resolver on *:53 of the node neither
    stops kaskad-dns nor answers the clients. No interface match: PPP, tunnels and IKEv2 (out of IPsec on the
    external interface) alike; gw_pre (-150) has already dropped spoofed sources and marked the connection for
    compat-in. Queries of the node itself (output) are not translated.
    3.3.7.2 (SEC-3371 Б-2, REV-s1-kt1a-sec Д-1): which dnsmasq — by the mark of the client in gw_route NOW. The first
    rule takes the mark of the map (no element — the packet keeps its own, 0: a client of the kernel arrives unmarked);
    0x201 (out directly at this moment — drawn only for a device whose route lets it, the invariant) ->
    kaskad-gw-dnsdirect (the pool first, then 1.1.1.1), anything else -> kaskad-dns (the pool only: its names never pass the exits). The
    mark is put back to 0 in the same rule as the DNAT: the query is delivered to the node, its mark means nothing
    there."""
    from . import dns
    ip = addr.dns_ip(st)
    out = ["ip saddr @gw_nets ip daddr %s meta l4proto { tcp, udp } th dport %d meta mark set ip saddr map @%s"
           % (ip, DNS_PORT, MAP)]
    out += ["ip saddr @gw_nets ip daddr %s meta mark %s %s dport %d meta mark set 0x0 dnat to %s:%d"
            % (ip, _hex(exits.MARK_DIRECT), proto, DNS_PORT, ip, dns.PORT_DIRECT) for proto in ("udp", "tcp")]
    out += ["ip saddr @gw_nets ip daddr %s %s dport %d meta mark set 0x0 dnat to %s:%d"
            % (ip, proto, DNS_PORT, ip, dns.PORT) for proto in ("udp", "tcp")]
    return out


SPREAD_GUARD_PRE = "meta mark %s meta mark set %s" % (_hex(MARK_SPREAD), _hex(exits.MARK_BLOCK))
SPREAD_GUARD_FWD = "ip saddr @gw_nets ct mark %s 0xffff == %s drop" % (nft.AND, _hex(MARK_SPREAD))


def spread_rules(p):
    """«по соединениям» (§4.5): ``p["spread"]`` (T4) — None: the mode is off; a list — the marks of the good exits of the
    active tier of «Основная» (an xray exit as 0x200, once per exit: the share of AmneziaWG and xray by their numbers).
    A device marked MARK_SPREAD gets one of them per NEW connection (``numgen random``); none good — the block mark
    (the mark 0x2fe has no ip rule: it must never reach the routing, a forward would go out directly). The rule «0x2fe →
    block» is drawn ALWAYS, the mode on or off (SEC-s1-t45 С-1): an element 0x2fe put by ``update_map`` or a hook
    between two full applies is blocked, never out directly; numgen — only with the marks of the plan."""
    marks = spread_want(p)
    out = []
    if marks:
        out.append("meta mark %s meta mark set numgen random mod %d map { %s }"
                   % (_hex(MARK_SPREAD), len(marks), ", ".join("%d : %s" % (i, _hex(m)) for i, m in enumerate(marks))))
    out.append(SPREAD_GUARD_PRE)
    return out


def root_only_rules(p):
    """gw_out (§5.2, С3; §2.9, П5): the node's own inbounds of xray — the API (no password), the metrics, the pool
    inbound, ``probe-<id>`` — and the API of the bot's xray (10851) open for root only: the ports of the set gw_xports
    (``xports_of``: exactly those of the config, REV-s1-t5 З3 — one rule, its set refilled with the pool)."""
    return ["ip daddr %s tcp dport @%s meta skuid != 0 reject" % (TPROXY_ADDR, XPORTS)]


def rules(st, p=None, spoof=None):
    """[(chain, [rule texts])] of the gateway part. ``p`` — ``plan()``; ``spoof`` replaces the anti-spoofing
    rules of the enabled inbounds (the probe of the constructs)."""
    from . import dns as dnsd                          # lazily, as dns_dnat_rules: gw/dns.py imports nothing of here
    p = plan(st) if p is None else p
    dns = addr.dns_ip(st)
    K = nft.MARK
    X, N, B, A, V = (_hex(exits.MARK_XRAY), _hex(exits.MARK_NODE), _hex(exits.MARK_BLOCK),
                     _hex(exits.AWG_MARK_BASE), _hex(exits.VLESS_MARK_BASE))
    C = CLIENT_IFACES
    tproxies = [(exits.MARK_XRAY, TPROXY_PORT)] + _vless(p)
    from . import mip
    pre = ["ct direction reply ct mark %s 0xffff != 0x0 meta mark set meta mark %s %s"
           % (nft.AND, nft.OR, _hex(REPLY_MARK)),
           'iifname "lo" return']
    in_rule = mip.in_rule(p.get("mip"))                # Ш3: the inbounds only on the chosen addresses (PLAN §3)
    if in_rule:
        pre.append(in_rule)
    pre += ['iifname "%s" ip saddr != @gw_nets drop' % C,
           'iifname "%s" fib saddr . iif oif missing drop' % C]
    pre += spoof_rules(st) if spoof is None else list(spoof)
    pre += ['ip daddr @gw_nets iifname != "%s" meta ipsec missing drop' % C,
            "ip saddr != @gw_nets return",
            "ip daddr %s meta l4proto { tcp, udp } th dport %d ct mark set ct mark %s %s return"
            % (dns, DNS_PORT, nft.OR, K),
            "ip daddr %s icmp type echo-request ct mark set ct mark %s %s return" % (dns, nft.OR, K),
            # SEC-3372 З-1: the own ports of the two dnsmasq are not asked past port 53 — a device «всё через выход»
            # would ask kaskad-gw-dnsdirect itself (the direct upstream). Before DNAT (gw_dns is nat -102, this is
            # -150): a query gw_dns sends there is still seen here with port 53, so it never meets this rule
            "ip daddr %s meta l4proto { tcp, udp } th dport { %d, %d } drop" % (dns, dnsd.PORT, dnsd.PORT_DIRECT),
            "ip saddr != @%s drop" % LIVE,
            "ip saddr @%s fib daddr type local drop" % RUONLY,       # Б1: only Russia — no services of the node
            "fib daddr type local meta mark set %s ct mark set ct mark %s %s return" % (N, nft.OR, N),
            "ip daddr @gw_nets return"]
    pre += ["ct state established,related ct mark %s 0xffff == %s meta l4proto udp meta mark set %s "
            "tproxy to %s:%d accept" % (nft.AND, _hex(m), _hex(m), TPROXY_ADDR, port) for m, port in tproxies]
    pre += ["ct state established,related meta mark set ct mark %s 0xffff return" % nft.AND,
            "meta mark set %s" % B,
            "meta mark set ip saddr map @%s" % MAP]
    pre += spread_rules(p)                             # «по соединениям» (§4.5): a mark per NEW connection
    pre += ru_rules()                                  # wave 3: Russia directly (after the route, before TPROXY)
    pre += rx_rules(p)                                 # Q2: Russia through the Russian group (gw_rx)
    pre += ["ct mark set meta mark %s %s" % (nft.OR, K)]
    pre += ["meta mark %s meta l4proto { tcp, udp } tproxy to %s:%d" % (_hex(m), TPROXY_ADDR, port)
            for m, port in tproxies]
    fwd = [
        "ip saddr @gw_nets meta mark %s drop" % B,
        "ip saddr @gw_nets ct mark %s 0xffff == %s drop" % (nft.AND, B),
        "ip saddr @gw_nets ct mark %s 0xffff == %s drop" % (nft.AND, X),
        "ip saddr @gw_nets ct mark %s 0xff00 == %s drop" % (nft.AND, V),
        'ip saddr @gw_nets ct mark %s 0xff00 == %s oifname != "%s" drop' % (nft.AND, A, LEG_IFACES),
        "ip saddr @gw_nets ip daddr != @gw_nets ct mark %s 0xffff == 0x0 drop" % nft.AND,
        SPREAD_GUARD_FWD,                              # С-1: always — 0x2fe never goes out directly
        "ip daddr %s drop" % dns,
        "ip saddr @gw_nets ip daddr @gw_nets drop",
        "ip saddr @gw_nets ip daddr @%s ct mark %s 0xffff != %s drop" % (PRIV, nft.AND, N),
        "ip saddr @gw_nets tcp flags syn tcp option maxseg size set rt mtu",
    ]
    # §6.1: MSS по итоговому MTU каждого интерфейса — входа WireGuard/AmneziaWG и ноги kawgN — в обе стороны: SYN
    # клиента (сервер выше шлёт сегменты не больше серверного MTU входа) и SYN-ACK к нему (клиент шлёт не больше);
    # SYN-ACK с ноги несёт MSS 1460 интернета (стенд T-G1, C2-2). Для транзита вход → нога выходит min из двух.
    tunnels = {}
    for kind, name in _adapter_ifaces(st):
        if kind in gwmtu.KINDS and name is not None and _IFACE.match(str(name)) and not str(name).endswith("*"):
            tunnels.setdefault(kind, []).append(str(name))
    fwd += gwmtu.mss_rules(st, tunnels)
    out = ['meta mark %s 0xffffff00 == %s oifname != "%s" drop' % (nft.AND, A, LEG_IFACES)]
    uid = _uid(p)
    if uid is not None:
        out.append("meta skuid %d ip daddr %s ct direction original meta l4proto { tcp, udp } th dport != %d drop"
                   % (uid, LOOPBACK, DNS_PORT))
    out += root_only_rules(p)
    # Ш4: the legs with their own address (SNAT by Endpoint), Н4 — the clients from the source of the route; then as before
    post = mip.post_rules(p.get("mip")) + ['ip saddr @gw_nets oifname != "lo" ip daddr != @gw_nets masquerade']
    return [("gw_pre", pre), ("gw_fwd", fwd), ("gw_out", out), ("gw_post", post),
            ("gw_dns", dns_dnat_rules(st))]                                    # F2


def _adapter_ifaces(st):
    """[(kind, name)] of the enabled adapters; an adapter that fails here is left out (reported by
    ``bad_data``) — the table must not depend on adapter code."""
    from . import registry
    out = []
    for kind in model.enabled_kinds(st):
        try:
            ad = registry.get(kind)
            names = list(ad.ifaces()) if ad is not None else []
        except Exception:                       # noqa: BLE001 — adapters are other work packages
            out.append((kind, None))
            continue
        out.extend((kind, n) for n in names)
    return out


def ifaces(st):
    """iifname patterns of the installed and enabled inbounds (adapters' ``ifaces()``). A name that is not
    a plain interface pattern is SKIPPED (never reaches nft text; ``bad_data`` reports it)."""
    out = []
    for _, name in _adapter_ifaces(st):
        if name is not None and _IFACE.match(str(name)) and name not in out:
            out.append(name)
    return out


IPSEC6_DROP = "meta ipsec exists drop"
IPSEC6_NAME = "IPsec"               # how read_ip6/ip6_problems name that rule next to the interface patterns


def ipsec_on(st):
    """An inbound of IPSEC_KINDS (IKEv2) is enabled: its clients come out of IPsec, not out of a kgw-* interface."""
    return any(k in IPSEC_KINDS for k in model.enabled_kinds(st))


def rules6(st, names=None):
    """gw_fwd6: no IPv6 of the clients is forwarded — one drop per interface pattern of the inbounds and (F8, PLAN-n13
    §8.0), while IKEv2 is on, the IPv6 that comes out of IPsec: such a packet has no kgw-* interface (it arrives on the
    external one), so ``iifname "kgw-*" drop`` never sees it."""
    names = ifaces(st) if names is None else names
    out = ['iifname "%s" drop' % n for n in names]
    if ipsec_on(st):
        out.append(IPSEC6_DROP)
    return out


def rules6_out(uid):
    """gw_out6: xray's sockets open nothing on ::1 but DNS (as gw_out for 127.0.0.0/8)."""
    if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
        return []
    return ["meta skuid %d ip6 daddr %s ct direction original meta l4proto { tcp, udp } th dport != %d drop"
            % (uid, LOOPBACK6, DNS_PORT)]


def _is_mark_match(e, left, value):
    m = e.get("match") if isinstance(e, dict) else None
    return isinstance(m, dict) and m.get("op") in ("==", None) and m.get("left") == left and m.get("right") == value


def spread_guarded(live):
    """«по соединениям» (§4.5) is closed in the LIVE table (SEC-s1-t45 С-1): gw_pre turns 0x2fe into the block mark
    (``meta mark 0x2fe meta mark set 0x2ff``) and gw_fwd drops a connection still carrying 0x2fe. Only then a gw_route
    element with MARK_SPREAD may go in between two full applies (``update_map``, ``set_element``); a table without them
    (3.3.5, a table drawn before B1) — the element is left out: blocked, never out directly (0x2fe has no ip rule)."""
    mm = {"meta": {"key": "mark"}}
    ctm = {"&": [{"ct": {"key": "mark"}}, 0xffff]}
    rules_ = (live or {}).get("rules") or {}
    pre = any(len(r) == 2 and _is_mark_match(r[0], mm, MARK_SPREAD) and isinstance(r[1], dict)
              and (r[1].get("mangle") or {}).get("key") == mm
              and (r[1].get("mangle") or {}).get("value") == exits.MARK_BLOCK
              for r in rules_.get("gw_pre") or [] if isinstance(r, list))
    fwd = any(any(_is_mark_match(e, ctm, MARK_SPREAD) for e in r) and any(isinstance(e, dict) and "drop" in e for e in r)
              for r in rules_.get("gw_fwd") or [] if isinstance(r, list))
    return pre and fwd


def _chain_items(runner, chain, memo=None):
    """(items, why) of ``nft -j list chain`` of a chain of the table: its dicts, or None and the reason (the listing
    refused, not JSON). ``memo`` (a dict of one tick of ``update_map``) keeps the answer: gw_pre is listed once per
    tick, for ``spread_ok`` and ``spread_plan`` alike (REV-s1-kt2a П-2)."""
    if memo is not None and chain in memo:
        return memo[chain]
    res = runner.run([nft.NFT, "-j", "list", "chain", nft.TABLE_FAMILY, nft.TABLE_NAME, chain])
    got = None, "nft -j list chain %s: %s" % (chain, (res.err or res.out or "").strip()[:200])
    if res.ok:
        try:
            doc = json.loads(res.out)
            got = [it for it in (doc.get("nftables") if isinstance(doc, dict) else None) or []
                   if isinstance(it, dict)], None
        except ValueError:
            got = None, "nft -j list chain %s: not JSON" % chain
    if memo is not None:
        memo[chain] = got
    return got


def spread_ok(runner, live=None, memo=None):
    """``spread_guarded`` of the live table: the rules of ``live`` when it has them (``nft.read_live``), else two small
    listings of gw_pre and gw_fwd (``peek.read`` has no rules; ``memo`` — ``_chain_items``). False when they cannot be
    read."""
    got = (live or {}).get("rules") or {}
    if "gw_pre" in got and "gw_fwd" in got:
        return spread_guarded(live)
    items = []
    for chain in ("gw_pre", "gw_fwd"):
        part, _ = _chain_items(runner, chain, memo)
        if part is None:
            return False
        items += part
    try:
        return spread_guarded(nft.parse_live({"nftables": items}))
    except (KaskadError, ValueError, TypeError, AttributeError):
        return False


def _without_spread(elems):
    return dict((ip, m) for ip, m in (elems or {}).items() if m != MARK_SPREAD)


# ── «по соединениям» between two full applies (КТ2 К2-4) ──────────────────
# The numgen rule of gw_pre carries the marks of the good exits as they were at the last full apply; an exit dying or
# coming back changes no pool and so brings no full apply: VLESS dead, AmneziaWG alive — half of the new connections
# of the kernel's devices went on to 0x200 (gw-vless, nothing alive in it: block). ``update_map`` brings the rule to
# ``spread_rules(plan)`` in the transaction of gw_route: replaced, put before the guard, or deleted, by its handle.

# the rule «по соединениям» between two ticks (one process — the collector): ``err`` — not in sync, retried next tick
# (a refusal of nft, gw_pre unreadable); ``told`` — the last reason told to the journal (once per reason, REV-s1-kt2a
# З-1); ``seen`` — (the rule as read right after the tick wrote it, its cells) when its map is not recognized: the same
# rule later — the same cells, not rewritten every tick
SPREAD_FAIL = {"err": None, "told": None, "seen": None}
SPREAD_UNRECOGNIZED = "unrecognized"


def spread_reset():
    SPREAD_FAIL.update(err=None, told=None, seen=None)


def spread_want(p):
    """The marks of the numgen rule ``spread_rules(p)`` draws (in its order), [] — no numgen rule."""
    got = (p or {}).get("spread") or []
    return [m for m in got if isinstance(m, int) and not isinstance(m, bool) and m in _ALLOWED_MARKS
            and m not in (exits.MARK_BLOCK, exits.MARK_DIRECT)]


def _numgen_marks(exprs):
    """The marks of a numgen rule of «по соединениям» in gw_pre (``meta mark 0x2fe meta mark set numgen random mod N
    map { … }``), by its numbers 0…N-1; None — not that rule; [] — that rule, its map unreadable (``spread_plan``)."""
    mm = {"meta": {"key": "mark"}}
    if not (isinstance(exprs, list) and len(exprs) == 2 and _is_mark_match(exprs[0], mm, MARK_SPREAD)):
        return None
    mg = exprs[1].get("mangle") if isinstance(exprs[1], dict) else None
    if not isinstance(mg, dict) or mg.get("key") != mm or not isinstance(mg.get("value"), dict):
        return None
    mp = mg["value"].get("map")
    if not isinstance(mp, dict) or "numgen" not in (mp.get("key") or {}):
        return []
    ng = mp["key"]["numgen"]
    if not isinstance(ng, dict):                       # REV-s1-kt2a П-3: never a failed tick
        return []
    data = (mp.get("data") or {}).get("set") if isinstance(mp.get("data"), dict) else None
    try:
        n = _num(ng.get("mod"))
        cells = dict((_num(k), _num(v)) for k, v in data)
        off = _num(ng.get("offset") or 0)
    except (TypeError, ValueError):
        return []
    # the size first (SEC-s1-kt2a П-2): ``mod`` of the live table never makes a list of its own size
    if ng.get("mode") != "random" or off != 0 or n != len(cells) or len(cells) != len(data) \
            or sorted(cells) != list(range(n)):
        return []
    return [cells[i] for i in range(n)]


def _num(v):
    """A number of nft -j: an int, or its text ("0x200", "512"); else ValueError."""
    if isinstance(v, bool):
        raise ValueError(v)
    return v if isinstance(v, int) else int(str(v), 0)


def _pre_rules(runner, memo=None):
    """([(handle, exprs)] of gw_pre in the live table, None); (None, why) — it cannot be read (the listing, JSON, a
    rule without an integer handle)."""
    items, why = _chain_items(runner, "gw_pre", memo)
    if items is None:
        return None, why
    out = []
    for it in items:
        r = it.get("rule")
        if isinstance(r, dict) and r.get("chain") == "gw_pre":
            h = r.get("handle")
            if not isinstance(h, int) or isinstance(h, bool):
                return None, "gw_pre: a rule without an integer handle (%r)" % (h,)
            out.append((h, [e for e in (r.get("expr") or []) if isinstance(e, dict)]))
    return out, None


def _canon(exprs):
    return json.dumps(exprs, sort_keys=True)


def spread_lines(runner, p):
    """The lines of one transaction that make the numgen rule of the live gw_pre ``spread_rules(p)`` (КТ2 К2-4): the
    marks of the good exits now. [] — it is so already, or there is no guard in this table (a table before B1: its
    0x2fe elements are left out by ``update_map``, nothing to spread); None — gw_pre cannot be read (left as it is)."""
    return spread_plan(runner, p)[0]


def spread_plan(runner, p, memo=None):
    """(``spread_lines``, why): why — None; the reason gw_pre cannot be read (lines None); ``SPREAD_UNRECOGNIZED`` —
    the map of the numgen rule is not read (the live form of nft -j unlike ours): rewritten when the tick has not seen
    it as it wrote it (``SPREAD_FAIL["seen"]``) or the good exits changed since, else left (REV-s1-kt2a З-1 (б))."""
    pre, why = _pre_rules(runner, memo)
    if pre is None:
        return None, why
    mm = {"meta": {"key": "mark"}}
    guard = [h for h, ex in pre if len(ex) == 2 and _is_mark_match(ex[0], mm, MARK_SPREAD)
             and (ex[1].get("mangle") or {}).get("key") == mm and (ex[1].get("mangle") or {}).get("value") == exits.MARK_BLOCK]
    if not guard:
        return [], None
    exprs = dict(pre)
    have = [(h, _numgen_marks(ex)) for h, ex in pre]
    have = [(h, m) for h, m in have if m is not None]
    want = spread_want(p)
    T = "%s gw_pre" % nft.TABLE
    if not want:
        return ["delete rule %s handle %d" % (T, h) for h, _ in have], None
    text = spread_rules(p)[0]
    unrec = SPREAD_UNRECOGNIZED if any(m == [] for _, m in have) else None
    if len(have) == 1 and sorted(have[0][1]) == sorted(want):
        return [], None                                # the same cells (their order changes no share)
    if len(have) == 1 and unrec and SPREAD_FAIL["seen"] == (_canon(exprs[have[0][0]]), sorted(want)):
        return [], unrec                               # the rule the tick wrote for these cells, unchanged since
    out = ["delete rule %s handle %d" % (T, h) for h, _ in have[1:]]
    if have:
        out.append("replace rule %s handle %d %s" % (T, have[0][0], text))
    else:
        out.append("insert rule %s handle %d %s" % (T, guard[0], text))    # right before the guard, as ``rules``
    return out, unrec


def _spread_tell(why, key, **kw):
    """One journal line per reason (not every tick)."""
    if SPREAD_FAIL["told"] == why:
        return
    SPREAD_FAIL["told"] = why
    util.log_tr(key, **kw)


def _spread_failed(err):
    """A refusal of nft of the numgen rule (the map went on without it): retried next tick, told once per text."""
    SPREAD_FAIL["err"] = err
    _spread_tell(err, "gw_net.log.spread_failed", error=err)


def _spread_seen(runner, p):
    """After the tick wrote a rule whose map it does not recognize: the rule as nft now lists it, for these cells."""
    pre, _ = _pre_rules(runner)
    ng = [ex for _, ex in pre or [] if _numgen_marks(ex) is not None]
    SPREAD_FAIL["seen"] = (_canon(ng[0]), sorted(spread_want(p))) if len(ng) == 1 and _numgen_marks(ng[0]) == [] \
        else None


def _check_elements(st, elems, loud=False):
    """Sorted [(ip, mark)]: addresses inside the base, marks of exits.* only. Invalid entries raise with
    ``loud`` (hooks: bad input), else they are skipped — that address stays out of gw_route/gw_live and
    so is blocked."""
    out = []
    b = addr.base(st)
    for ip, mark in elems.items():
        try:
            a = ipaddress.IPv4Address(ip)
            if a not in b:
                raise KaskadError(tr("gw_net.err.outside_base", ip=ip, base=b))
            if mark not in _ALLOWED_MARKS and mark != MARK_SPREAD:     # 0x2fe: its guard is in every table drawn
                raise KaskadError(tr("gw_net.err.bad_mark", mark=repr(mark), ip=ip))
        except (KaskadError, ValueError) as e:
            if loud:
                raise KaskadError(str(e))
            continue
        out.append((int(a), str(a), mark))
    return [(ip, mark) for _, ip, mark in sorted(out)]


def elements_text(st, elems, loud=False):
    return ", ".join("%s : %s" % (ip, _hex(mark)) for ip, mark in _check_elements(st, elems, loud))


def live_ips(st, elems, ruonly=()):
    """gw_live (§2.3): the addresses whose mark lets them out (everything but block) and those of gw_ruonly (a fallback
    block with «Россия напрямую»), validated; sorted as gw_route."""
    ru = set(rd_addresses(st, {"rd": ruonly or []}))
    return [ip for ip, mark in _check_elements(st, elems) if mark != exits.MARK_BLOCK or ip in ru]


def live_text(st, elems, ruonly=()):
    """Elements of gw_live: the addresses whose mark lets them out (everything but block) + gw_ruonly."""
    return ", ".join(live_ips(st, elems, ruonly))


def bad_data(st):
    """What the renderer had to skip: invalid interface patterns of adapters (or adapters failing),
    interfaces not named kgw-* (their clients would have no DNS and no node: the base is closed to other
    interfaces), device addresses outside their pools, duplicates (``model.validate``). For ``gw check``."""
    probs = []
    for kind, name in _adapter_ifaces(st):
        if name is None:
            probs.append(tr("gw_net.bad.no_ifaces", kind=kind))
        elif not _IFACE.match(str(name)):
            probs.append(tr("gw_net.bad.iface_name", kind=kind, name=repr(name)))
        elif not str(name).startswith(CLIENT_IFACES.rstrip("*")):
            probs.append(tr("gw_net.bad.iface_prefix", kind=kind, name=name))
    return probs + model.validate(st)


def elements(st, runner=None, ctx=None):
    """{client ip: mark} wanted in gw_route now (``plan()["elems"]``): devices of state + dynamic leases,
    marks from the exits snapshot."""
    return plan(st, runner, ctx)["elems"]


def _set_block(prefix, name, elements_line):
    spec = [x.strip() for x in set_spec(name).split(";") if x.strip()]
    out = [prefix + "set %s {" % name] + [prefix + "\t" + x for x in spec]
    if elements_line:
        out.append(prefix + "\telements = { %s }" % elements_line)
    out.append(prefix + "}")
    return out


def _table_lines(st, p, prefix="\t", spoof=None, check=True):
    elems = p["elems"]
    out = _set_block(prefix, "gw_nets", str(addr.base(st)))
    out += _set_block(prefix, RU, "")                 # empty, in the boot file too: load_ru fills it on its own
    out += _set_block(prefix, LIVE, live_text(st, elems, p.get("ruonly")))
    out += _set_block(prefix, PRIV, ", ".join(PRIVATE))
    out += _set_block(prefix, RU_DNS, "")             # dnsmasq fills them
    out += _set_block(prefix, FORCE, "")
    out += _set_block(prefix, RD, ", ".join(rd_addresses(st, p, check)))
    out += _set_block(prefix, RU_VER, "")             # load_ru says here which lists gw_ru holds
    out += _set_block(prefix, RUONLY, ", ".join(ruonly_addresses(st, p)))
    out += _set_block(prefix, XPORTS, ", ".join(str(x) for x in xports_of(p)))
    out += [prefix + "map %s {" % MAP, prefix + "\ttype ipv4_addr : mark"]
    et = elements_text(st, elems)
    if et:
        out.append(prefix + "\telements = { %s }" % et)
    out.append(prefix + "}")
    if p.get("rx") is not None:                        # Q2: only with a Russian group (§3)
        out += [prefix + "map %s {" % RX, prefix + "\ttype ipv4_addr : mark"]
        xt = rx_text(st, p, check)
        if xt:
            out.append(prefix + "\telements = { %s }" % xt)
        out.append(prefix + "}")
    for chain, rs in rules(st, p, spoof):
        out.append(prefix + "chain %s {" % chain)
        out.append(prefix + "\t" + HOOKS[chain])
        out.extend(prefix + "\t" + r for r in rs)
        out.append(prefix + "}")
    return out


IPV6_DISABLE = "/sys/module/ipv6/parameters/disable"


def ipv6_off():
    """The kernel booted with ipv6.disable=1 (F1, finding 4): there is no IPv6 to close, and a table ip6 whose rules use
    conntrack (gw_out6: ``ct direction``) is refused without IPv6 — in kaskad.nft it took the whole file, the forwards
    with it, down at every boot. Then the gateway renders no ip6 table at all."""
    return (util.read_text(paths.P(IPV6_DISABLE)) or "").strip() == "1"


def _table6_text(name, rs, rs_out=(), pre6=()):
    """The whole ``ip6 kaskad6`` (re-created): gw_fwd6, gw_out6 and — only with rules of «Приём» by IPv6 (``pre6``,
    F11) — gw_pre6; without them the text is the one of 3.3.7.14 byte for byte."""
    t = "%s %s" % (TABLE6_FAMILY, name)
    out = ["table " + t, "delete table " + t, "table %s {" % t, "\tchain %s {" % CHAIN6, "\t\t" + HOOK6]
    out.extend("\t\t" + r for r in rs)
    out += ["\t}", "\tchain %s {" % CHAIN6_OUT, "\t\t" + HOOK6_OUT]
    out.extend("\t\t" + r for r in rs_out)
    out.append("\t}")
    if pre6:
        out += ["\tchain %s {" % CHAIN6_PRE, "\t\t" + HOOK6_PRE]
        out.extend("\t\t" + r for r in pre6)
        out.append("\t}")
    out.append("}")
    return "\n".join(out) + "\n"


def pre6_rules(st):
    """The rules of gw_pre6 (``mip.pre6_rules``) — one function for the boot file, an apply, the repair of the watchdog
    and its check, so they never disagree (С-1); a failure — none (as Ш3 of IPv4: the addresses never break the
    gateway part)."""
    try:
        from . import mip
        return mip.pre6_rules(st)
    except Exception:                                   # noqa: BLE001
        return []


def _pre6_live(ctx):
    """The chain gw_pre6 is in the live ``ip6 kaskad6`` — asked once per apply through the runner ``nft.apply`` puts
    into ``ctx`` (no runner — a script only shown: not asked, nothing deleted)."""
    if not isinstance(ctx, dict):
        return False
    if "pre6_live" not in ctx:
        r = ctx.get("runner")
        info = read_ip6(r) if r is not None else None
        ctx["pre6_live"] = bool(info and info.get("pre_hook") is not None)
    return ctx["pre6_live"]


def _refill(st, elems, ruonly=None):
    """flush + add of gw_route and gw_live (and gw_ruonly when ``ruonly`` — its addresses — is given): one transaction,
    atomic for packets."""
    T = nft.TABLE
    out = ["flush map %s %s" % (T, MAP)]
    et = elements_text(st, elems)
    if et:
        out.append("add element %s %s { %s }" % (T, MAP, et))
    out.append("flush set %s %s" % (T, LIVE))
    lt = live_text(st, elems, ruonly or ())
    if lt:
        out.append("add element %s %s { %s }" % (T, LIVE, lt))
    if ruonly is not None:
        out.append("flush set %s %s" % (T, RUONLY))
        ro = rd_addresses(st, {"rd": ruonly})
        if ro:
            out.append("add element %s %s { %s }" % (T, RUONLY, ", ".join(ro)))
    return out


def update_lines(st, live, p=None, pre6_live=False):
    """Script lines that bring the live gateway part to ``st`` in the same transaction as the forwards:
    objects created if missing, chains flushed and refilled, gw_nets, gw_priv, gw_live, gw_route and gw_rd
    refilled (atomic for packets), the ip6 table refilled in place. The networks of Russia (gw_ru, gw_ru_ver) are
    never filled or flushed here — ``load_ru`` does it on its own, so no apply sends (or dry-runs) their 15 k
    ranges; gw_ru_dns and gw_force are never flushed — dnsmasq fills them at run time. A gw_ru of another type
    (3.1 had a plain interval set) is deleted and created again empty, after the chain that uses it is flushed."""
    T = nft.TABLE
    p = plan(st) if p is None else p
    info = (live or {}).get("set_info") or {}
    migrate = RU in info and not _ru_type_ok(info[RU], ru_form())
    out = ["add set %s %s { %s }" % (T, name, set_spec(name)) for name in SETS if not (migrate and name == RU)]
    out.append("add map %s %s { type ipv4_addr : mark; }" % (T, MAP))
    if p.get("rx") is not None:
        out.append("add map %s %s { type ipv4_addr : mark; }" % (T, RX))
    for chain in CHAINS:
        out.append("add chain %s %s { %s }" % (T, chain, HOOKS[chain]))
    for chain in CHAINS:
        out.append("flush chain %s %s" % (T, chain))
    if p.get("rx") is None and RX in info:             # the last Russian group gone: its map after its rules
        out.append("delete map %s %s" % (T, RX))
    if migrate:                                     # its version goes too: load_ru fills the new one
        out += ["delete set %s %s" % (T, RU), "add set %s %s { %s }" % (T, RU, set_spec(RU)),
                "flush set %s %s" % (T, RU_VER)]
    out.append("flush set %s gw_nets" % T)
    out.append("add element %s gw_nets { %s }" % (T, addr.base(st)))
    out.append("flush set %s %s" % (T, PRIV))
    out.append("add element %s %s { %s }" % (T, PRIV, ", ".join(PRIVATE)))
    out.extend(_refill(st, p["elems"], ruonly=None))
    if p.get("ruonly"):                                # gw_live with the addresses of gw_ruonly (the set itself: T5)
        lt = live_text(st, p["elems"], p["ruonly"])
        out.append("flush set %s %s" % (T, LIVE))
        if lt:
            out.append("add element %s %s { %s }" % (T, LIVE, lt))
    out.extend(_rd_refill(T, rd_addresses(st, p)))
    if p.get("rx") is not None:
        out.extend(_rx_refill(T, st, p))
    out.extend(_ruonly_refill(T, ruonly_addresses(st, p)))
    out.extend(_xports_add(T, xports_of(p)))           # only added to: xray.apply drops what it closed (П2)
    for chain, rs in rules(st, p):
        out.extend("add rule %s %s %s" % (T, chain, r) for r in rs)
    if ipv6_off():                                     # F1: no IPv6 in the kernel, no ip6 table
        return out
    out += ["add table " + TABLE6, "add chain %s %s { %s }" % (TABLE6, CHAIN6, HOOK6),
            "add chain %s %s { %s }" % (TABLE6, CHAIN6_OUT, HOOK6_OUT),
            "flush chain %s %s" % (TABLE6, CHAIN6), "flush chain %s %s" % (TABLE6, CHAIN6_OUT)]
    out.extend("add rule %s %s %s" % (TABLE6, CHAIN6, r) for r in rules6(st))
    out.extend("add rule %s %s %s" % (TABLE6, CHAIN6_OUT, r) for r in rules6_out(_uid(p)))
    pre = pre6_rules(st)
    if pre:                                            # F11: «Приём» by IPv6 — the chain made and refilled in place
        out += ["add chain %s %s { %s }" % (TABLE6, CHAIN6_PRE, HOOK6_PRE), "flush chain %s %s" % (TABLE6, CHAIN6_PRE)]
        out.extend("add rule %s %s %s" % (TABLE6, CHAIN6_PRE, r) for r in pre)
    elif pre6_live:                                    # nothing chosen any more: the chain goes (flushed first)
        out += ["flush chain %s %s" % (TABLE6, CHAIN6_PRE), "delete chain %s %s" % (TABLE6, CHAIN6_PRE)]
    return out


def teardown_script(live):
    """Remove the gateway part from the live table (``gw disable``): only objects that exist; chains are
    flushed before they are deleted, sets after the chains that use them; the ip6 table goes whole."""
    T = nft.TABLE
    out = []
    have = set((live or {}).get("chains") or [])
    chains = [c for c in CHAINS if c in have]
    out += ["flush chain %s %s" % (T, c) for c in chains]
    out += ["delete chain %s %s" % (T, c) for c in chains]
    info = (live or {}).get("set_info") or {}
    for name in (MAP, RX) + SETS:
        if name in info:
            out.append("delete %s %s %s" % ("map" if info[name].get("kind") == "map" else "set", T, name))
    if not ipv6_off():
        out += ["add table " + TABLE6, "delete table " + TABLE6]
    return "\n".join(out) + "\n"


def map_script(st, elems, rd=None, ruonly=None, rx=None):
    """gw_route + gw_live (and gw_rd when ``rd`` — its validated addresses — is given, gw_ruonly when ``ruonly`` is,
    gw_rx when ``rx`` — a plan with its «rx» — is): one transaction (SPEC-groups §3: gw_rx with gw_route and gw_rd)."""
    lines = _refill(st, elems, ruonly)
    if rd is not None:
        lines += _rd_refill(nft.TABLE, rd)
    if rx is not None:
        lines += _rx_refill(nft.TABLE, st, rx)
    return "\n".join(lines) + "\n"


# ── reading the live part (lenient: the JSON of sets is not pinned by a real fixture yet) ──

def _unwrap(x):
    if isinstance(x, dict) and isinstance(x.get("elem"), dict):
        return x["elem"].get("val")
    return x


def _addr_text(x):
    x = _unwrap(x)
    if isinstance(x, str):
        try:
            return str(ipaddress.IPv4Network(x, strict=False)) if "/" in x else str(ipaddress.IPv4Address(x))
        except ValueError:
            return x
    if isinstance(x, dict) and isinstance(x.get("prefix"), dict):
        p = x["prefix"]
        try:
            return str(ipaddress.IPv4Network("%s/%s" % (p.get("addr"), p.get("len")), strict=False))
        except ValueError:
            return repr(x)
    if isinstance(x, dict) and isinstance(x.get("range"), list) and len(x["range"]) == 2:
        return "%s-%s" % tuple(x["range"])
    return repr(x)


def _mark_value(v):
    v = _unwrap(v)
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 16) if v.lower().startswith("0x") else int(v)
        except ValueError:
            return None
    return None


def set_elems(live, name):
    info = ((live or {}).get("set_info") or {}).get(name) or {}
    return set(_addr_text(x) for x in (info.get("elem") or []))


def map_elems(live, name=MAP):
    info = ((live or {}).get("set_info") or {}).get(name) or {}
    out = {}
    for x in info.get("elem") or []:
        x = _unwrap(x) if not isinstance(x, list) else x
        if isinstance(x, (list, tuple)) and len(x) == 2:
            out[_addr_text(x[0])] = _mark_value(x[1])
    return out


def _has_stmt(live, chain, key):
    for exprs in ((live or {}).get("rules") or {}).get(chain) or []:
        if any(isinstance(e, dict) and key in e for e in exprs):
            return True
    return False


def _hook_of(spec):
    m = re.match(r"^type (\w+) hook (\w+) priority (-?\d+);", spec)
    return (m.group(1), m.group(2), int(m.group(3)))


def part_present(live):
    """The gateway part is in the live table — its chains and the set of live clients: what an inbound
    needs before it lets clients in (the watchdog does not start one without it)."""
    if live is None:
        return False
    return all(c in (live.get("chain_info") or {}) for c in CHAINS if c != "gw_dns") \
        and LIVE in (live.get("set_info") or {})          # F2: a table of 3.1.0 (no gw_dns) is not «missing»


def verify(st, live, elems=None, ctx=None):
    """Differences of the live gateway part from ``st`` (empty = in sync): chains with their hooks and
    rule counts, TPROXY and masquerade present, gw_nets = the base, gw_priv = the private ranges, gw_route
    and gw_live = the wanted elements."""
    if live is None:
        return [tr("gw_net.verify.no_table")]
    p = plan(st, ctx=ctx)
    if elems is not None:
        p = dict(p, elems=elems)
    probs = []
    info = live.get("chain_info") or {}
    for chain, rs in rules(st, p):
        ci = info.get(chain)
        if ci is None:
            probs.append(tr("gw_net.verify.no_chain", chain=chain))
            continue
        if (ci.get("type"), ci.get("hook"), ci.get("prio")) != _hook_of(HOOKS[chain]):
            probs.append(tr("gw_net.verify.chain_hook", chain=chain, type=ci.get("type"), hook=ci.get("hook"),
                            prio=ci.get("prio")))
        n = len(((live.get("rules") or {}).get(chain)) or [])
        if n != len(rs):
            probs.append(tr("gw_net.verify.chain_rules", chain=chain, n=n, want=len(rs)))
    if "gw_pre" in info and not _has_stmt(live, "gw_pre", "tproxy"):
        probs.append(tr("gw_net.verify.no_tproxy"))
    if "gw_post" in info and not _has_stmt(live, "gw_post", "masquerade"):
        probs.append(tr("gw_net.verify.no_masquerade"))
    sets = live.get("set_info") or {}
    for name in SETS + (MAP,):
        if name not in sets:
            probs.append(tr("gw_net.verify.no_set", set=name))
    if "gw_nets" in sets:
        got = set_elems(live, "gw_nets")
        want = set([str(addr.base(st))])
        if got != want:
            probs.append(tr("gw_net.verify.nets", got=", ".join(sorted(got)) or tr("gw_net.empty"),
                            want=", ".join(sorted(want))))
    if PRIV in sets and set_elems(live, PRIV) != set(PRIVATE):
        probs.append(tr("gw_net.verify.priv", got=", ".join(sorted(set_elems(live, PRIV))) or tr("gw_net.empty")))
    checked = _check_elements(st, p["elems"])
    if MAP in sets:
        want = dict(checked)
        got = map_elems(live)
        if got != want:
            diff = sorted(set(got.items()) ^ set(want.items()))
            probs.append(tr("gw_net.verify.route", diffs=trn("gw_net.diffs", len(diff)),
                            examples=", ".join("%s→%s" % (ip, _hex(m) if isinstance(m, int) else m)
                                               for ip, m in diff[:3])))
    if p.get("rx") is None and RX in sets:             # no Russian group: a gw_rx left behind (a failed apply) still
        probs.append(tr("gw_net.verify.rx_left"))      # leads its addresses home — the repair draws the table anew
    if p.get("rx") is not None:                        # Q2 (§3): the map of the Russian targets
        if RX not in sets:
            probs.append(tr("gw_net.verify.no_set", set=RX))
        elif map_elems(live, RX) != dict(rx_elements(st, p)):
            got, want = map_elems(live, RX), dict(rx_elements(st, p))
            probs.append(tr("gw_net.verify.rx", diffs=trn("gw_net.diffs", len(set(got.items()) ^ set(want.items())))))
    if LIVE in sets:
        want = set(live_ips(st, p["elems"], p.get("ruonly") or ()))
        got = set_elems(live, LIVE)
        if got != want:
            probs.append(tr("gw_net.verify.live", diffs=trn("gw_net.diffs", len(got ^ want))))
    if XPORTS in sets:                                 # by the content, not by the number of rules (REV-s1-t5 З3)
        want = set(str(x) for x in xports_of(p))
        got = set_elems(live, XPORTS)
        if not want <= got:                            # a missing port is open; an extra one is closed (П2: until
            # xray.apply settles the set after it dropped the inbound) — not a drift
            probs.append(tr("gw_net.verify.xports", got=", ".join(sorted(got, key=_port_key)) or tr("gw_net.empty"),
                            want=", ".join(sorted(want, key=_port_key))))
    probs.extend(_verify_rules(st, live, p))
    return probs


def _verify_rules(st, live, p):
    """Wave 3: gw_ru of the type of RU_FORM, gw_rd = the ru_direct clients. Which lists gw_ru holds (gw_ru_ver) is
    NOT a drift of the table: no apply loads them, so nft.apply could not repair it — kaskad-gw-rules-load.timer
    does (``kaskad.rules.apply.ensure_kernel``, with a pause after a failure); ``gw check`` reports it apart."""
    probs = []
    sets = live.get("set_info") or {}
    if RU in sets and not _ru_type_ok(sets[RU], ru_form()):
        probs.append(tr("gw_net.verify.ru_type", got=sets[RU].get("type"), want=RU_TYPES[ru_form()]))
    if RD in sets:
        want = set(rd_addresses(st, p))
        got = set_elems(live, RD)
        if got != want:
            probs.append(tr("gw_net.verify.rd", diffs=trn("gw_net.diffs", len(got ^ want))))
    return probs


# ── the section object nft.py calls ────────────────────────────────────────

def _with_live_xports(p, live):
    """The plan with the ports of gw_xports of ``live`` too — the full text ``nft.apply`` falls back to recreates the
    table: a port of an exit just removed stays closed until xray dropped its inbound (``xports_settle``), as with the
    script (``_xports_add``; SEC-s1-b1 П-2: xray refused the new config or its restart waits — the old ``probe-<id>``
    runs on). No live table or no such set — the plan as it is."""
    if not live or XPORTS not in (live.get("set_info") or {}):
        return p
    have = [int(x) for x in set_elems(live, XPORTS) if str(x).isdigit()]
    return dict(p, xray_ports=sorted(set(p.get("xray_ports") or []) | set(have)))


def _with_disk_xports(p):
    """The plan with the guarded ports of the config of xray on disk too (REV-s1-b3 П1, SEC П-2 to the end of the
    class): the full text — the boot file kaskad.nft and a recreate — keeps closed every port the config xray starts
    with after a reboot opens (a removed exit whose new config xray refused: its ``probe-<id>`` is still in the old
    config on disk, and the collector does not bring the port back — ``update_map`` adds the plan's only). No config
    or a broken one — the plan as it is (the plan is a superset of what a new config may open)."""
    from . import xray
    cfg = xray._read_config(xray.config_path())
    disk = xray.guarded_ports(cfg) if cfg else []
    if not disk:
        return p
    return dict(p, xray_ports=sorted(set(p.get("xray_ports") or []) | set(disk)))


class _Section(object):
    name = "gateway"

    def active(self, st):
        return model.enabled(st)

    def full_lines(self, st, ctx=None):
        return _table_lines(st, _with_live_xports(_with_disk_xports(plan(st, ctx=ctx)), (ctx or {}).get("live")))

    def full_extra(self, st, ctx=None):
        if ipv6_off():                     # ranges of gw_ru are loaded apart (rules.apply), not in this file
            return ""
        return _table6_text(TABLE6_NAME, rules6(st), rules6_out(_uid(plan(st, ctx=ctx))), pre6_rules(st))

    def update_lines(self, st, live, ctx=None):
        return update_lines(st, live, plan(st, ctx=ctx), pre6_live=False if ipv6_off() or pre6_rules(st)
                            else _pre6_live(ctx))

    def verify(self, st, live, ctx=None):
        return verify(st, live, ctx=ctx)

    def extra_tables(self):
        return [(TABLE6_FAMILY, TABLE6_NAME)]


SECTION = _Section()


# ── the live map without touching the rest (exits watchdog, hooks) ─────────

def _port_key(x):
    return (0, int(x)) if str(x).isdigit() else (1, str(x))


def update_map(runner, st, live=None, elems=None, decision=None, ports=False):
    """Make gw_route and gw_live equal the wanted elements in one transaction (and gw_rd the ru_direct clients, gw_ruonly
    the addresses in gw_live only for Russia, when the table has them — §2.3). Returns True if it changed. No table (or
    no gateway part) -> False (the caller restores the table). ``decision`` — the decision of the collector's tick in
    memory (§2.7: the plan of the marks from it, not from the file); ``elems`` — the elements as they are (no gw_ruonly
    then). ``ports`` — gw_xports too (the collector, when it applies a render of xray this round: a new ``probe-<id>``
    is closed to the users of the node BEFORE xray opens it, REV-s1-t5 З3); a plan without ports leaves the set."""
    plain = elems is None                              # the plan's own elements: its numgen rule too (К2-4)
    if elems is None:
        p = plan(st, runner, decision=decision, ports=ports)
        elems = p["elems"]
    else:
        from . import hooks
        p = {"rd": rules_apply.rd(st, hooks.leases_for(st)), "ruonly": None}
    if live is None:
        live = nft.read_live(runner)
    if live is None or MAP not in (live.get("set_info") or {}) or LIVE not in (live.get("set_info") or {}):
        return False
    sets = live.get("set_info") or {}
    memo = {}                                          # gw_pre listed once per tick (REV-s1-kt2a П-2)
    if MARK_SPREAD in elems.values() and not spread_ok(runner, live, memo):
        elems = _without_spread(elems)                 # С-1: no guard of 0x2fe in this table — blocked, not direct
    ruonly = p.get("ruonly") if RUONLY in sets and p.get("ruonly") is not None else None
    checked = _check_elements(st, elems)
    same = map_elems(live) == dict(checked) and set_elems(live, LIVE) == set(live_ips(st, elems, ruonly or ()))
    if ruonly is not None and set_elems(live, RUONLY) != set(rd_addresses(st, {"rd": ruonly})):
        same = False
    rd = None
    if RD in sets:
        want = rd_addresses(st, p)
        if set_elems(live, RD) != set(want):
            rd = want
    rx = None                                          # Q2: gw_rx with gw_route and gw_rd, one transaction (§3)
    if RX in sets and p.get("rx") is not None and map_elems(live, RX) != dict(rx_elements(st, p)):
        rx = p
        same = False
    after = (set(rd) if rd is not None else set_elems(live, RD) if RD in sets else set(),
             dict(rx_elements(st, rx)) if rx is not None else map_elems(live, RX) if RX in sets else {},
             dict(checked))
    revoked = ru_revoked(ru_sides(live), after, p.get("rx_home")) if rd is not None or rx is not None else {}
    xp = None
    if ports and XPORTS in sets and p.get("xray_ports") is not None:
        have = set_elems(live, XPORTS)
        want = [x for x in xports_of(p) if str(x) not in have]     # only added: xray.apply drops the rest (П2)
        if want:
            xp = want
    # КТ2 К2-4: the numgen rule follows the good exits (an exit dying or back brings no full apply) — with a device
    # «по соединениям» in the plan or still in the table (its last tick there: the rule goes with it), or a refusal of
    # nft to retry
    sp = why = None
    if plain and ("spread" in p or MARK_SPREAD in map_elems(live).values() or SPREAD_FAIL["err"] is not None):
        sp, why = spread_plan(runner, p, memo)
        if sp is None:                                 # gw_pre unreadable: told once, retried next tick (З-1 (а))
            SPREAD_FAIL["err"] = why
            _spread_tell("unread:" + why, "gw_net.log.spread_unread", error=why)
        elif sp == []:
            SPREAD_FAIL["err"] = None                  # in sync: nothing left to retry
            if why is None:
                SPREAD_FAIL["told"] = SPREAD_FAIL["seen"] = None
            else:
                SPREAD_FAIL["told"] = why              # the rule the tick wrote (told then), unchanged
        elif why == SPREAD_UNRECOGNIZED:               # rewritten once, then only when the good exits change (З-1 (б))
            _spread_tell(why, "gw_net.log.spread_unrecognized")
        sp = sp or None
    if same and rd is None and xp is None and sp is None:
        return False
    if not same:
        script = map_script(st, elems, rd, ruonly, rx)
    else:
        script = "\n".join(_rd_refill(nft.TABLE, rd)) + "\n" if rd is not None else ""
    if xp is not None:
        script += "\n".join(_xports_add(nft.TABLE, xp)) + "\n"
    err = None
    if sp is not None:
        res = runner.run([nft.NFT, "-f", nft.STDIN], input=script + "\n".join(sp) + "\n")
        if res.ok:
            SPREAD_FAIL["err"] = None
            if why == SPREAD_UNRECOGNIZED:
                _spread_seen(runner, p)
            else:
                SPREAD_FAIL["told"] = SPREAD_FAIL["seen"] = None
            if not same:
                flush_direct_dns(runner, st, dict(checked))
            flush_revoked(runner, revoked)
            return True
        err = (res.err or res.out).strip()[:300]       # the map goes on alone; the rule — the next tick
        SPREAD_FAIL["err"] = err
        if not script.strip():
            _spread_failed(err)
            return False
    res = runner.run([nft.NFT, "-f", nft.STDIN], input=script)
    if not res.ok:                                     # the map refused too: its error, not the rule's (П-1)
        raise KaskadError(tr("gw_net.err.map_update", error=(res.err or res.out).strip()[:300]))
    if err is not None:
        _spread_failed(err)
    if not same:
        flush_direct_dns(runner, st, dict(checked))
    flush_revoked(runner, revoked)                     # SEC-s1-q2 Б-2: Russia taken away — its open connections too
    return True


def set_element(runner, st, ip, mark, ruonly=False):
    """One address in gw_route (+ gw_live unless the mark blocks; a block with ``ruonly`` — the last step of its group
    and «Россия напрямую», §2.3 — in gw_live and gw_ruonly) — hooks. Replaces an element with another mark. True on
    success. Raises KaskadError for an address outside the base or a mark that is not a route mark.
    T4 П1 (B4): gw_ruonly and gw_live of a fallback block go in ONE transaction, gw_ruonly first — the address is never
    in gw_live without gw_ruonly (that would give it the node's services). No gw_ruonly in the table (before stage 1)
    or nft refused it, or no permission of «Россия напрямую» (D-136) — a plain block (out of gw_live), False. Out of
    gw_ruonly — after the new mark is in."""
    et = elements_text(st, {ip: mark}, loud=True)
    a = _check_elements(st, {ip: mark}, loud=True)[0][0]
    if mark == MARK_SPREAD and not spread_ok(runner):
        del_element(runner, st, ip)                    # С-1: no guard of 0x2fe in the table — blocked, not direct
        return False
    T = nft.TABLE
    # D-136 (REV-s1-b7-sec-2 П-1): gw_ruonly of a hook passes the last line too — without the permission a plain block
    ru = bool(ruonly and mark == exits.MARK_BLOCK)
    dropped = ru and not ruguard.rd(st, [a])
    ru = ru and not dropped
    head = ["add element %s %s { %s }" % (T, MAP, et)]
    block = head + ["add element %s %s { %s }" % (T, LIVE, a),   # B1: out of gw_live in the same transaction — it may
                    "delete element %s %s { %s }" % (T, LIVE, a)]  # be there from its mark before (the node's services
    if mark != exits.MARK_BLOCK:                                   # and, from gw_rd, Russia)
        lines = head + ["add element %s %s { %s }" % (T, LIVE, a)]
    elif ru:
        lines = head + ["add element %s %s { %s }" % (T, RUONLY, a), "add element %s %s { %s }" % (T, LIVE, a)]
    else:
        lines = block
    if _replace_element(runner, T, a, lines):
        if not ru:
            _set_ruonly(runner, a, False)
        return not dropped                             # a plain block instead of the asked one, as without gw_ruonly
    if ru and _replace_element(runner, T, a, block):
        _set_ruonly(runner, a, False)                  # out of gw_live already: harmless, and none is left behind
        return False
    return False


def _replace_element(runner, T, a, lines):
    """``lines`` (the element in gw_route and its sets) as one transaction; the address there with another mark
    (element clash) — replaced in one transaction, gw_live of it too. -> True when nft took one of them."""
    if runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(lines) + "\n").ok:
        return True
    script = ["delete element %s %s { %s }" % (T, MAP, a)] + lines
    if runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(script) + "\n").ok:
        return True
    script = ["delete element %s %s { %s }" % (T, MAP, a), "delete element %s %s { %s }" % (T, LIVE, a)] + lines
    return runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(script) + "\n").ok


def _set_ruonly(runner, a, on):
    """The address into gw_ruonly or out of it — on its own (the set may be absent: a table before stage 1)."""
    runner.run([nft.NFT, "-f", nft.STDIN], input="%s element %s %s { %s }\n" % ("add" if on else "delete", nft.TABLE,
                                                                               RUONLY, a))


def del_element(runner, st, ip):
    """The address out of gw_route, gw_live and gw_ruonly — gw_live first and in ONE transaction with gw_route
    (SEC-s1-t45 П-1: an address in gw_live without gw_route got 0x2ff and, in gw_rd, 0x201 to Russia). A set lacks
    the address (a blocked device is not in gw_live) — ``add`` + ``delete`` of it in the same transaction never fails;
    gw_ruonly may be absent (a table before stage 1) — then without it. No element in gw_route — gw_live and gw_ruonly
    are still cleared (gw_live first), False."""
    a = ipaddress.IPv4Address(ip)
    if a not in addr.base(st):
        raise KaskadError(tr("gw_net.err.outside", ip=a))
    T = nft.TABLE

    def gone(name):                                    # a set: out of it whether the address is there or not
        return ["add element %s %s { %s }" % (T, name, a), "delete element %s %s { %s }" % (T, name, a)]
    route = ["delete element %s %s { %s }" % (T, MAP, a)]
    for lines in (gone(LIVE) + route + gone(RUONLY), gone(LIVE) + route):
        if runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(lines) + "\n").ok:
            return True                                # the second: no gw_ruonly in this table
    runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(gone(LIVE)) + "\n")   # no element in gw_route
    _set_ruonly(runner, a, False)
    return False


def set_rd(runner, st, ip, on):
    """A dynamic address (IKEv2, ``gw hook``) into gw_rd or out of it. True when nft did it (adding an element
    that is there is fine; removing one that is not — False). Raises KaskadError for an address outside the base."""
    a = _check_elements(st, {ip: exits.MARK_DIRECT}, loud=True)[0][0]
    if on and not ruguard.rd(st, [a]):         # D-136: the last line (the hook's own test is ``device_rd``)
        on = False
    script = "%s element %s %s { %s }\n" % ("add" if on else "delete", nft.TABLE, RD, a)
    return runner.run([nft.NFT, "-f", nft.STDIN], input=script).ok


def set_ru(runner, st, ip, rd_on, rx_mark, rx=True):
    """The Russian side of a dynamic address (``gw hook``) — gw_rd («Россия напрямую») and gw_rx (its Russian group) in
    ONE transaction (SEC-s1-q2 П-1, REV-s1-q2 П-1): the hook calls it BEFORE the address goes into gw_route / gw_live, so
    a new owner of an address never gets the gw_rx entry of the old one (a «down» that never came) for a moment, and a
    failed write is seen. ``rx`` False — the policy has no Russian group: no gw_rx in the table, no call for it (REV П-2).
    The last line of D-136 (``ruguard.rd``; a mark outside RX_MARKS — none). A table without gw_rd (before wave 3) — gw_rx
    alone.

    -> False only when gw_rx exists and may still hold ANOTHER mark for the address (neither the new mark nor its
    removal went in), or gw_rd refused the write and holds the address of another owner (REV-q-int-2 Н-1/Н-2; read
    back, unreadable — False): the hook keeps the address out of gw_live then (closed until the collector, never
    somebody else's home router). Raises KaskadError for an address outside the base."""
    a = _check_elements(st, {ip: exits.MARK_DIRECT}, loud=True)[0][0]
    T = nft.TABLE
    if rd_on and not ruguard.rd(st, [a]):
        rd_on = False
    if rx_mark is not None and (rx_mark not in RX_MARKS or not ruguard.rd(st, [a])):
        rx_mark = None
    rd = ["add element %s %s { %s }" % (T, RD, a)] + ([] if rd_on else ["delete element %s %s { %s }" % (T, RD, a)])

    def run(lines):
        return bool(lines) and runner.run([nft.NFT, "-f", nft.STDIN], input="\n".join(lines) + "\n").ok
    if not rx:                                     # REV-q-int-2: gw_rd refused — True only without another's entry
        return run(rd) or _rx_free(runner, a, rd_on, rx=False)
    gone = "delete element %s %s { %s }" % (T, RX, a)
    if rx_mark is None:
        # out of gw_rx; refused — it was not there (or there is no gw_rx): gw_rd alone; a table without gw_rd — gw_rx alone
        if run(rd + [gone]):
            return True
        if not run(rd) and run([gone]):
            return _rx_free(runner, a, rd_on, rx=False)    # REV-q-int-2 Н-2: gw_rd refused — not another owner's left
        return _rx_free(runner, a, rd_on)          # REV-s1-q2-2 З-2, REV-q-int-2 Н-2: a refusal is not «not there»
    put = "add element %s %s { %s : %s }" % (T, RX, a, _hex(rx_mark))
    for lines in ([put], [gone, put]):             # another mark there (Б10: the old owner's) — replaced at once
        if run(rd + lines):
            return True
        if run(lines):                             # a table without gw_rd — or gw_rd refused (REV-q-int-2 Н-1)
            return _rx_free(runner, a, rd_on, rx=False)
    if run(rd + [gone]):                           # the new mark refused: at least none of another (Russia goes the
        return True                                # foreign route of the address — never a home router)
    if run([gone]):
        return _rx_free(runner, a, rd_on, rx=False)
    return _rx_free(runner, a, rd_on)              # REV-q-int П-1, REV-q-int-2 Н-1: unreadable or stale gw_rd — closed


def _rx_free(runner, a, rd_on=False, rx=True):
    """REV-s1-q2-2 З-2, REV-q-int-2 Н-1/Н-2: read back after refused writes of ``set_ru`` — True only when gw_rx holds
    no mark for ``a`` (or the table has no gw_rx) AND gw_rd holds no entry of another owner for it (``a`` there while
    ``rd_on`` is False — «Россия напрямую» of the old owner, a «down» that never came; or the table has no gw_rd).
    ``rx`` False — gw_rx is known to be right (written or not used), gw_rd alone. Either unreadable — False: the hook
    keeps the address out of gw_live (closed until the collector)."""
    from . import peek
    try:
        live = peek.read(runner, sets=(RX, RD) if rx else (RD,), counters=False)
    except Exception:                                  # noqa: BLE001 — not known: closed
        return False
    if live is None:
        return False
    info = live.get("set_info") or {}          # a set nft has not (peek: «No such file») is not here; any other
    if rx and RX in info and a in map_elems(live, RX):     # failure of the read raised above — closed
        return False
    return rd_on or RD not in info or a not in set_elems(live, RD)


# ── ip6 table ──────────────────────────────────────────────────────────────

def _iifname_of(r):
    for e in r.get("expr") or []:
        m = e.get("match") if isinstance(e, dict) else None
        if isinstance(m, dict) and (m.get("left") or {}).get("meta", {}).get("key") == "iifname":
            return m.get("right")
    return None


def _is_ipsec6_drop(r):
    """The rule is exactly ``meta ipsec exists drop`` (F8)."""
    ex = r.get("expr") or []
    return (len(ex) == 2 and _is_mark_match(ex[0], {"meta": {"key": "ipsec"}}, True)
            and isinstance(ex[1], dict) and "drop" in ex[1])


def read_ip6(runner):
    """{"rules": [iifname patterns of the drop rules of gw_fwd6; IPSEC6_NAME for ``meta ipsec exists drop``], "hook":
    (type, hook, prio), "out_rules": number of rules of gw_out6, "out_hook": (...)} or None if the table is absent."""
    res = runner.run([nft.NFT, "-j", "list", "table", TABLE6_FAMILY, TABLE6_NAME])
    if not res.ok:
        return None
    try:
        doc = json.loads(res.out)
    except ValueError:
        return None
    out = {"rules": [], "hook": None, "out_rules": 0, "out_hook": None, "pre_hook": None, "pre_rules": []}
    for it in doc.get("nftables") or []:
        ch = it.get("chain") if isinstance(it, dict) else None
        if isinstance(ch, dict) and ch.get("name") == CHAIN6:
            out["hook"] = (ch.get("type"), ch.get("hook"), ch.get("prio"))
        if isinstance(ch, dict) and ch.get("name") == CHAIN6_OUT:
            out["out_hook"] = (ch.get("type"), ch.get("hook"), ch.get("prio"))
        if isinstance(ch, dict) and ch.get("name") == CHAIN6_PRE:
            out["pre_hook"] = (ch.get("type"), ch.get("hook"), ch.get("prio"))
        r = it.get("rule") if isinstance(it, dict) else None
        if isinstance(r, dict) and r.get("chain") == CHAIN6:
            out["rules"].append(IPSEC6_NAME if _is_ipsec6_drop(r) else _iifname_of(r))
        if isinstance(r, dict) and r.get("chain") == CHAIN6_OUT:
            out["out_rules"] += 1
        if isinstance(r, dict) and r.get("chain") == CHAIN6_PRE:
            out["pre_rules"].append(_daddr6_of(r))
    return out


def _daddr6_of(rule):
    """The IPv6 of ``ip6 daddr != { … }`` of a rule of gw_pre6 (sorted, canonical), None — the rule has none (no IPv6
    takes connections)."""
    for e in rule.get("expr") or []:
        m = e.get("match") if isinstance(e, dict) else None
        left = (m or {}).get("left") if isinstance(m, dict) else None
        pl = left.get("payload") if isinstance(left, dict) else None
        if isinstance(pl, dict) and pl.get("protocol") == "ip6" and pl.get("field") == "daddr" and m.get("op") == "!=":
            right = m.get("right")
            items = right.get("set") if isinstance(right, dict) else [right]
            got = []
            for x in items if isinstance(items, list) else []:
                try:
                    got.append(str(ipaddress.IPv6Address(str(x))))
                except ValueError:
                    continue
            return sorted(got, key=lambda a: int(ipaddress.IPv6Address(a)))
    return None


def ip6_problems(st, runner):
    if ipv6_off():
        return []
    live = read_ip6(runner)
    if live is None:
        return [tr("gw_net.ip6.no_table")]
    probs = []
    if live["hook"] != _hook_of(HOOK6):
        probs.append(tr("gw_net.ip6.chain_hook", chain=CHAIN6))
    want = ifaces(st) + ([IPSEC6_NAME] if ipsec_on(st) else [])
    if sorted(n for n in live["rules"] if n) != sorted(want) or len(live["rules"]) != len(want):
        probs.append(tr("gw_net.ip6.rules_for", chain=CHAIN6, got=", ".join(str(n) for n in live["rules"]) or "—",
                        want=", ".join(want) or "—"))
    want_out = len(rules6_out(xray_uid()))
    if live["out_hook"] != _hook_of(HOOK6_OUT) or live["out_rules"] != want_out:
        probs.append(tr("gw_net.ip6.out_rules", chain=CHAIN6_OUT, n=live["out_rules"], want=want_out))
    p6 = _pre6_problem(st, live)
    if p6:
        probs.append(p6)
    return probs


def _pre6_problem(st, live):
    """F11, С-1: gw_pre6 of the live table against ``in6`` — no chain while IPv6 has a choice (a reboot with an old boot
    file, a table re-created by hand), a chain while there is none (a rollback left it), another hook or other
    addresses: the words of the problem (the repair ``apply_ip6`` draws the right one), None — as it should be."""
    from . import addresses
    want = pre6_rules(st)
    has = live.get("pre_hook") is not None
    if not want and not has:
        return None
    in6 = addresses.in6_list(st) if want else None
    exp = sorted(in6, key=lambda a: int(ipaddress.IPv6Address(a))) if in6 else None   # None: the rule with no list
    got = live.get("pre_rules") or []
    if want and live.get("pre_hook") == _hook_of(HOOK6_PRE) and len(got) == len(want) and got[0] == exp:
        return None

    def said(ips):
        return ", ".join(ips) if ips else tr("gw_mip.ip6.none6")
    return tr("gw_mip.ip6.pre6", got=("; ".join(said(g) for g in got) or "—") if has else tr("gw_mip.ip6.no_chain"),
              want=said(exp) if want else tr("gw_mip.ip6.no_chain"))


def apply_ip6(runner, st):
    if ipv6_off():
        return
    res = runner.run([nft.NFT, "-f", nft.STDIN], input=_table6_text(TABLE6_NAME, rules6(st), rules6_out(xray_uid()),
                                                                    pre6_rules(st)))
    if not res.ok:
        raise KaskadError(tr("gw_net.err.ip6_apply", error=(res.err or res.out).strip()[:300]))


# ── can the gateway part work here? ────────────────────────────────────────

def probe(runner, st=None):
    """``nft -c`` of twin tables with every construct of the gateway part (one map element, one VLESS exit,
    the rule of xray's user, an anti-spoofing rule with interfaces and IPsec, an ip6 rule of an interface and the ip6
    rule of IPsec (F8); wave 3: gw_ru of RU_FORM with the load of two ranges and gw_ru_ver, gw_rd, and an ``add
    element`` into the timeout set the way dnsmasq adds). Returns (ok, error text). Read-only."""
    st = st or {"gateway": {"enabled": True}}
    b = addr.base(st)
    client = str(ipaddress.IPv4Address(b.network_address + 266))
    sample = {"elems": {client: exits.AWG_MARK_BASE + 1},
              "vless": [(exits.VLESS_MARK_BASE + 1, exits.VLESS_PORT_BASE + 1)], "uid": 1, "rd": [client],
              # Ш3/Ш4: the rule of the chosen addresses (a range of ports as in a hop of Hysteria2), SNAT of a leg and Н4
              "mip": {"in": {"addrs": ["192.0.2.1", "192.0.2.2"],
                             "ports": [("tcp", 443, 443), ("udp", 20000, 20099), ("udp", 51820, 51820)]},
                      "legs": [("198.51.100.1", 51820, "192.0.2.2", "e-0000000000")],
                      "snat": {"dev": "kgw-probe", "src": "192.0.2.1"}}}
    ru = {"form": ru_form(), "ver": 1, "ranges": ["192.0.2.0/24", "198.51.100.0-198.51.100.99"]}
    sub = ipaddress.IPv4Network((int(b.network_address) + 256, 24))
    spoof = ['ip saddr %s iifname != "kgw-probe" iifname != "kgw-prob*" meta ipsec missing drop' % sub]
    t = "ip %s" % PROBE_TABLE
    text = "\n".join(["table " + t, "delete table " + t, "table %s {" % t] + _table_lines(st, sample, spoof=spoof, check=False)
                     + ["}"] + ru_load_lines(t, ru) + ["add element %s %s { 203.0.113.1 }" % (t, FORCE)])
    if not ipv6_off():
        text += "\n" + _table6_text(PROBE_TABLE6, ['iifname "kgw-probe" drop', IPSEC6_DROP], rules6_out(1))
    return nft.check(text, runner)


# ── the address clients connect to (Endpoint of WireGuard/AmneziaWG, server of PPP/IKEv2/...) ──

_HOSTNAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}"
                       r"[A-Za-z0-9])?)+$")
IP_ECHO = "https://api.ipify.org"


def parse_host(value):
    """``st["gateway"]["host"]``: a public IPv4 address or a DNS name (the domain mode of wave 4)."""
    s = str(value or "").strip().lower().rstrip(".")
    try:
        a = ipaddress.IPv4Address(s)
    except ValueError:
        if _HOSTNAME.match(s) and not s.replace(".", "").isdigit():
            return s
        raise KaskadError(tr("gw_net.err.host", value=repr(value)))
    if not a.is_global:
        raise KaskadError(tr("gw_net.err.host_private", ip=a))
    return str(a)


def detect_host(runner):
    """(address, how): the source address of the default route when it is public, else what
    api.ipify.org answers (1:1 NAT clouds; as get_my_ip of install.sh), else the route's address anyway
    (how = "route-private")."""
    res = runner.run(["ip", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", res.out if res.ok else "")
    src = m.group(1) if m else ""
    if src and ipaddress.IPv4Address(src).is_global:
        return src, "route"
    res = runner.run(["curl", "-s", "-4", "--max-time", "5", IP_ECHO], timeout=10)
    got = (res.out or "").strip()
    try:
        if res.ok and ipaddress.IPv4Address(got).is_global:
            return got, "echo"
    except ValueError:
        pass
    return src, "route-private"


def node_addresses(runner, st=None):
    """The node's own global addresses (``a.b.c.d/32``, ``x::y/128``) outside the private ranges (blocked
    anyway) and outside the gateway's interfaces, plus ``gateway.host`` when it is a public IPv4 — the
    destinations xray opens for no client (spec §1.3: the node's services are not reachable through SOCKS5)."""
    res = runner.run(["ip", "-o", "addr", "show"])
    found = set()
    priv = [ipaddress.IPv4Network(n) for n in PRIVATE]
    for line in (res.out if res.ok else "").splitlines():
        tok = line.split()
        if len(tok) < 4 or tok[2] not in ("inet", "inet6") or "temporary" in tok or "deprecated" in tok:
            continue                                   # IPv6 privacy addresses come and go: not the node's name
        dev = tok[1].rstrip(":").split("@", 1)[0]
        if dev == "lo" or dev.startswith(addr.OWN_IFACES):
            continue
        try:
            a = ipaddress.ip_interface(tok[3]).ip
        except ValueError:
            continue
        if a.version == 4 and any(a in n for n in priv):
            continue
        if a.version == 6 and (a.is_link_local or a.is_private or a.is_loopback or a.is_multicast):
            continue
        found.add(a)
    try:
        h = ipaddress.IPv4Address(model.public_host(st or {}))
        if h.is_global:
            found.add(h)
    except ValueError:
        pass
    return ["%s/%d" % (a, a.max_prefixlen) for a in sorted(found, key=lambda x: (x.version, int(x)))]


# ── routing: ip rules (one block of pinned prefs), tables 200 and 199, kgw0 ──
#
# F1 (finding 2 of the 3.1.0 review). A rule added without a pref gets «the pref of the second rule of the list
# minus 1» (fib_default_rule_pref) and goes on top of everything: wg-quick's full tunnel (``not fwmark 51820
# lookup 51820`` and ``lookup main suppress_prefixlength 0``, started after kaskad-net) stood above the rules of
# 3.1.0 (31993–31999, 0x200/0x300 without a pref) and took the clients' marked packets (TPROXY, the legs) and
# the unmarked ones (past the kill switch) into wg0; Tailscale's exit node (5210–5270, ``lookup 52``) does the
# same with fixed prefs. So:
#   * our rules stand as ONE BLOCK of pinned prefs: block + 0 replies, + 1 ``to <base> lookup main``, + 2 the
#     pool (0x200), + 3 the VLESS exits (0x300/0xff00), + 4 the kill switch, + 4 + N the rule of leg N
#     (``fwmark 256+N lookup 256+N`` — install.sh adds it without a pref; the watchdog only moves it into its
#     slot, below ``to <base>``: the reverse path check of a client packet marked for a leg then finds the
#     client's own interface even under strict rp_filter). The block starts at RULE_HOME (above Tailscale) or,
#     when foreign rules stand up there, right above the topmost of them;
#   * pref 0 right after «local» (where every later pref-less rule would land BELOW us) was rejected: such
#     rules would all get pref 0 and be appended in the order they come — wg-quick adds ``not fwmark`` before
#     ``suppress_prefixlength 0``, its order would flip and the node's own LAN/Docker routes would go into
#     the tunnel;
#   * a pref-less foreign rule still lands above the block. The watchdog checks what the kernel decides — ``ip
#     route get 1.1.1.1 from <client> iif kgw0 mark M`` per class (0x200, 0x301 -> local, table 200; 0 ->
#     unreachable; 0x10000 -> main; 256+N -> table 256+N or unreachable when the leg is down) — and when a
#     FOREIGN table answers for one of our classes, the block moves right above the topmost foreign rule (the
#     new rules first, then the old ones go: the kill switch never disappears), says in /var/log/kaskad.log
#     which rule was in the way, and ``gw check`` shows it. When the foreign rules above go away, the block
#     goes back towards RULE_HOME (no ratchet). While the decisions are right nothing is touched.

RULE_HOME = 4000                    # the first pref of the block when nothing foreign stands above it
SLOTS = ("reply", "base", "xray", "vless", "kill")
LEG_SLOT0 = 4                       # the rule of leg N: block + LEG_SLOT0 + N (N = 1..MAX_AWG_INDEX)
LEGACY_BASE_PREF = 31998            # 3.1.0: from <base> to <base> lookup main (``to <base>`` took its place)
PROBE_DST = "1.1.1.1"               # «an external address» of the decisions (any public address would do)
CLASSES = ("reply", "base", "kill", "xray", "vless")     # the classes of our rules with a text (gw_net.class.*)
_RULE_LINE = re.compile(r"^(\d+):\s+(.*?)\s*$")
_RX_PROTO = re.compile(r" proto \S+$")
_RX_REPLY = re.compile(r"^(?:from all )?fwmark 0x%x/0x%x lookup main$" % (REPLY_MARK, REPLY_MARK))
_RX_BASE = re.compile(r"^(?:from all )?to (\S+) lookup main$")
_RX_KILL_BASE = re.compile(r"^from (\S+) to (\S+) lookup main$")
_RX_KILL = re.compile(r"^from (\S+) fwmark 0(?:x0+)?/0xffff lookup %d$" % KILL_TABLE)
_RX_XRAY = re.compile(r"^(?:from all )?fwmark 0x%x lookup %d$" % (exits.MARK_XRAY, ROUTE_TABLE))
_RX_VLESS = re.compile(r"^(?:from all )?fwmark 0x%x/0xff00 lookup %d$" % (exits.VLESS_MARK_BASE, ROUTE_TABLE))
_RX_LEG = re.compile(r"^(?:from all )?fwmark 0x([0-9a-f]+) lookup (\d+)$")
_RX_LOOKUP = re.compile(r"\b(?:lookup|table) (\S+)")
_RX_PRIVATE16 = re.compile(r"^(?:10\.\d+\.0\.0|172\.(?:1[6-9]|2\d|3[01])\.0\.0|192\.168\.0\.0)/16$")
REPLY_RULE = ["fwmark", "0x%x/0x%x" % (REPLY_MARK, REPLY_MARK), "lookup", "main"]
VLESS_RULE = ["fwmark", "%s/0xff00" % _hex(exits.VLESS_MARK_BASE), "lookup", str(ROUTE_TABLE)]


def _run(runner, argv):
    res = runner.run(argv)
    if not res.ok:
        raise KaskadError("%s: %s" % (" ".join(argv),
                                      (res.err or res.out).strip()[:200] or tr("gw_net.code", rc=res.rc)))
    return res


def _rule_lines(runner):
    """[(pref, text)] of ``ip -4 rule show`` in the kernel's order (whitespace normalized)."""
    res = runner.run(["ip", "-4", "rule", "show"])
    out = []
    for ln in (res.out if res.ok else "").splitlines():
        m = _RULE_LINE.match(ln.strip())
        if m:
            out.append((int(m.group(1)), " ".join(m.group(2).split())))
    return out


def kill_spec(base):
    """[(slot, ip rule selector + action)] of the kill switch for a base network: ``to <base>`` and the rule
    of unmarked client packets."""
    b = str(base)
    return [("base", ["to", b, "lookup", "main"]),
            ("kill", ["from", b, "fwmark", "0x0/0xffff", "lookup", str(KILL_TABLE)])]


def rule_args(slot, base):
    """``ip rule`` selector + action of one of our rules: a slot of SLOTS or ``leg:N``."""
    if slot.startswith("leg:"):
        mark = exits.AWG_MARK_BASE + int(slot[4:])
        return ["fwmark", _hex(mark), "lookup", str(mark)]
    if slot == "reply":
        return list(REPLY_RULE)
    if slot == "xray":
        return ["fwmark", _hex(exits.MARK_XRAY), "lookup", str(ROUTE_TABLE)]
    if slot == "vless":
        return list(VLESS_RULE)
    return dict(kill_spec(base))[slot]


def _classify(pref, text, base):
    """(id, ip rule args) of one of our rules — id: a slot, ``leg:N``, or ``stale:...`` (the kill switch or the
    ``to`` rule of a former base, the base rule of 3.1.0) — or None for a foreign rule (``text`` normalized)."""
    t = _RX_PROTO.sub("", text)
    if _RX_REPLY.match(t):
        return "reply", list(REPLY_RULE)
    if _RX_XRAY.match(t):
        return "xray", rule_args("xray", base)
    if _RX_VLESS.match(t):
        return "vless", list(VLESS_RULE)
    m = _RX_KILL.match(t)
    if m:
        args = ["from", m.group(1), "fwmark", "0x0/0xffff", "lookup", str(KILL_TABLE)]
        return ("kill" if m.group(1) == base else "stale:kill:" + m.group(1)), args
    m = _RX_BASE.match(t)
    if m:
        args = ["to", m.group(1), "lookup", "main"]
        if m.group(1) == base:
            return "base", args
        return ("stale:base:" + m.group(1), args) if m.group(1) in addr.BASE_CANDIDATES else None
    m = _RX_KILL_BASE.match(t)
    if m and m.group(1) == m.group(2) and (pref == LEGACY_BASE_PREF or m.group(1) == base) \
            and _RX_PRIVATE16.match(m.group(1)):
        return "stale:from:" + m.group(1), ["from", m.group(1), "to", m.group(1), "lookup", "main"]
    m = _RX_LEG.match(t)
    if m:
        mark, table = int(m.group(1), 16), int(m.group(2))
        if mark == table and exits.AWG_MARK_BASE < mark <= exits.AWG_MARK_BASE + exits.MAX_AWG_INDEX:
            return "leg:%d" % (mark - exits.AWG_MARK_BASE), ["fwmark", _hex(mark), "lookup", str(mark)]
    return None


def rule_view(runner, st, lines=None):
    """The ip rules split into ours and the foreign ones: {"lines": [(pref, text)], "ours": [{"i", "pref",
    "id", "args", "text"}], "foreign": [{"i", "pref", "text"}] (the «local» rule at pref 0 left out), "legs":
    [N with a rule], "anchor": the block's first pref by the rules there (None: none of the five)}. ``st`` None —
    no current base: every kill switch is «stale» (remove_routing)."""
    base = str(addr.base(st)) if st is not None else None
    lines = _rule_lines(runner) if lines is None else lines
    kills = set(m.group(1) for m in (_RX_KILL.match(_RX_PROTO.sub("", t)) for _, t in lines) if m)
    out = {"lines": lines, "ours": [], "foreign": [], "legs": [], "anchor": None}
    for i, (pref, text) in enumerate(lines):
        got = _classify(pref, text, base)
        if got is None:
            m = _RX_BASE.match(_RX_PROTO.sub("", text))
            if m and m.group(1) in kills:              # «to» of a former base whose kill switch is still there
                got = ("stale:base:" + m.group(1), ["to", m.group(1), "lookup", "main"])
        if got is not None:
            out["ours"].append({"i": i, "pref": pref, "id": got[0], "args": got[1], "text": text})
            if got[0].startswith("leg:") and int(got[0][4:]) not in out["legs"]:
                out["legs"].append(int(got[0][4:]))
        elif not (pref == 0 and _lookup_of(text) == "local"):
            out["foreign"].append({"i": i, "pref": pref, "text": text})
    out["legs"].sort()
    for slot in ("reply", "kill", "xray", "vless", "base"):
        prefs = [r["pref"] for r in out["ours"] if r["id"] == slot]
        if prefs:
            out["anchor"] = min(prefs) - SLOTS.index(slot)
            break
    return out


def _lookup_of(text):
    m = _RX_LOOKUP.search(text)
    return m.group(1) if m else None


def _span(legs):
    return LEG_SLOT0 + 1 + (max(legs) if legs else 0)


def block_start(view, hijacked=False):
    """Where the block goes. ``hijacked`` (a foreign rule decides one of our classes): above every foreign rule
    with a pref >= 1. Else it stays where it is — RULE_HOME when it is not in the band yet (a fresh node, the
    prefs of 3.1.0) — moved only to get out of a foreign rule standing inside its prefs (right above it), or
    back towards RULE_HOME when nothing foreign stands above that any more (no ratchet)."""
    span = _span(view["legs"])
    prefs = [f["pref"] for f in view["foreign"] if f["pref"] >= 1]   # nothing stands above a pref-0 rule
    above_all = RULE_HOME if not prefs else max(1, min(RULE_HOME, min(prefs) - span))
    if hijacked:
        return above_all
    start = view["anchor"]
    if start is None or not 1 <= start <= RULE_HOME:
        start = RULE_HOME
    while start > 1:
        inside = [p for p in prefs if start <= p < start + span]
        if not inside:
            break
        start = max(1, min(inside) - span)
    return max(start, above_all)


def rule_plan(view, st, start):
    """(adds [(pref, id, args)], deletions [our rule dicts]) that put our rules into the block at ``start``:
    the five rules of the gateway and the rules of the legs that have one (install.sh makes them)."""
    want = [(start + i, slot, rule_args(slot, addr.base(st))) for i, slot in enumerate(SLOTS)]
    want += [(start + LEG_SLOT0 + n, "leg:%d" % n, rule_args("leg:%d" % n, None)) for n in view["legs"]]
    have = set((r["pref"], r["id"]) for r in view["ours"])
    keep = set((p, slot) for p, slot, _ in want)
    return ([w for w in want if (w[0], w[1]) not in have],
            [r for r in view["ours"] if (r["pref"], r["id"]) not in keep])


def _probe_addrs(st):
    """A client-like source of the base no device has (base + 2) and another address of it (base + 3)."""
    b = addr.base(st)
    return str(b.network_address + 2), str(b.network_address + 3)


def route_decision(runner, dst, src, iif, mark=0):
    """``ip -4 route get <dst> from <src> iif <iif> [mark M]`` — the kernel's decision for a packet that came in
    on ``iif``: {"ok", "type" (local, unicast, ...; unreachable / xdev / error when it refused), "table" (main
    when ``ip`` prints none), "dev", "text"}."""
    argv = ["ip", "-4", "route", "get", dst, "from", src, "iif", iif]
    if mark:
        argv += ["mark", _hex(mark)]
    res = runner.run(argv)
    if not res.ok:
        err = " ".join((res.err or res.out or "").split())
        low = err.lower()
        kind = "unreachable" if "unreachable" in low else "xdev" if "cross-device" in low else "error"
        return {"ok": False, "type": kind, "table": None, "dev": None, "text": err}
    first = (res.out or "").strip().splitlines()[0] if (res.out or "").strip() else ""
    tok = first.split()
    kind = tok[0] if tok and tok[0] in ("local", "unreachable", "blackhole", "prohibit", "broadcast", "throw") \
        else "unicast"
    table = tok[tok.index("table") + 1] if "table" in tok[:-1] else "main"
    dev = tok[tok.index("dev") + 1] if "dev" in tok[:-1] else None
    return {"ok": True, "type": kind, "table": table, "dev": dev, "text": " ".join(tok)}


def _culprits(view, our_i, table):
    """Foreign rules standing above our rule (index ``our_i``): the ones that look up ``table`` first."""
    above = [f for f in view["foreign"] if f["i"] < our_i]
    hit = [f for f in above if _lookup_of(f["text"]) == table]
    return hit or above[:3]


def hijacks(runner, st, view=None):
    """Classes of our rules whose packets a FOREIGN rule decides: [{"id", "decision", "culprits": [foreign rule
    dicts]}]. Each class with its rule present is asked through kgw0 (``route_decision``); a refusal (no route,
    the leg is down, rp_filter) is never a hijack — those are told by the other checks."""
    view = rule_view(runner, st) if view is None else view
    src, peer = _probe_addrs(st)
    asks = [("reply", PROBE_DST, REPLY_MARK, "main"), ("base", peer, 0, "main"), ("kill", PROBE_DST, 0, None),
            ("xray", PROBE_DST, exits.MARK_XRAY, str(ROUTE_TABLE)),
            ("vless", PROBE_DST, exits.VLESS_MARK_BASE + 1, str(ROUTE_TABLE))]
    asks += [("leg:%d" % n, PROBE_DST, exits.AWG_MARK_BASE + n, str(exits.AWG_MARK_BASE + n)) for n in view["legs"]]
    out = []
    for cls, dst, mark, want in asks:
        ours = [r["i"] for r in view["ours"] if r["id"] == cls]
        if not ours:
            continue                                   # a missing rule is told by routing_problems
        d = route_decision(runner, dst, src, DUMMY, mark)
        if d["ok"] and (want is None or d["table"] != want):
            out.append({"id": cls, "decision": d, "culprits": _culprits(view, min(ours), d["table"])})
    return out


def _class_text(cls):
    if cls.startswith("leg:"):
        n = int(cls[4:])
        return tr("gw_net.class.leg", n=n, mark=_hex(exits.AWG_MARK_BASE + n))
    return tr("gw_net.class." + cls) if cls in CLASSES else cls


def hijack_text(h):
    """«чужое правило «3999: not from all fwmark 0xca6c lookup 51820» стоит выше правил Kaskad: пакеты пути через
    xray (метка 0x200) уходят в таблицу 51820 (dev wg0)»."""
    d = h["decision"]
    where = tr("gw_net.hijack.table", table=d["table"]) if d["table"] != "main" else tr("gw_net.hijack.main")
    if d["dev"]:
        where += " (dev %s)" % d["dev"]
    rules = ", ".join(tr("gw_net.quoted_rule", pref=f["pref"], text=f["text"]) for f in h["culprits"])
    who = tr("gw_net.hijack.single", rules=rules) if len(h["culprits"]) == 1 else \
        tr("gw_net.hijack.several", rules=rules) if rules else tr("gw_net.hijack.none")
    return tr("gw_net.hijack.text", who=who, what=_class_text(h["id"]), where=where)


def _rule_problems(st, view, hij):
    """What ``place_rules`` would fix: rules missing, out of their block (a leg above ``to <base>``, the prefs of
    3.1.0), left from a former base or from 3.1.0, decided by a foreign rule while the block can still go above
    it. A decision the block cannot win any more is told by ``routing_conflicts`` (the collector repairs what
    this returns every 10 s and logs each repair: nothing here may stay for good)."""
    probs = []
    base = addr.base(st)
    have = set(r["id"] for r in view["ours"])
    args = {"reply": {"rule": " ".join(REPLY_RULE)}, "base": {"base": base},
            "kill": {"base": base, "table": KILL_TABLE}}
    for slot in SLOTS:
        if slot not in have:
            probs.append(tr("gw_net.rule.missing." + slot, **args.get(slot, {})))
    for r in view["ours"]:
        if r["id"].startswith("stale:"):
            probs.append(tr("gw_net.rule.stale", pref=r["pref"], text=r["text"],
                            source="Kaskad 3.1.0" if r["id"].startswith("stale:from:")
                            else tr("gw_net.rule.former_base", base=r["id"].split(":", 2)[2])))
    start = block_start(view)
    moved = sorted(set(r["id"] for r in rule_plan(view, st, start)[1] if not r["id"].startswith("stale:")))
    if moved:
        probs.append(tr("gw_net.rule.moved", ids=", ".join(moved), start=start,
                        end=start + _span(view["legs"]) - 1))
    if hij and any(rule_plan(view, st, block_start(view, True))):
        probs.extend(hijack_text(h) for h in hij)
    return probs


def routing_conflicts(runner, st, status=None):
    """What the watchdog cannot fix — ``gw check`` shows it, nothing fights it: tables 199/200 used by someone else
    (``table_conflicts``); a foreign rule that decides one of our classes while our block already stands above every
    foreign rule with a pref >= 1 (a rule at pref 0, or no room left above it)."""
    s = routing_status(runner, st) if status is None else status
    out = table_conflicts(runner, st, s)
    view = s["rules"]
    if not s["hijacks"] or any(rule_plan(view, st, block_start(view, True))):
        return out
    top = block_start(view, True)
    end = top + _span(view["legs"]) - 1
    return out + [tr("gw_net.rule.cannot_rise", hijack=hijack_text(h), top=top, end=end) for h in s["hijacks"]]


def place_rules(runner, st, view=None, hijacked=None):
    """Put our rules into their block: the missing and the moved ones are added at their prefs FIRST, then the
    copies at other prefs, the rules of a former base and of 3.1.0 go (no moment without the kill switch).
    ``hijacked`` — the classes a foreign rule decided (``hijacks``): the block goes above the topmost foreign
    rule, and the log says which rule was in the way. Returns the list of changes."""
    view = rule_view(runner, st) if view is None else view
    start = block_start(view, bool(hijacked))
    adds, dels = rule_plan(view, st, start)
    done = []
    for pref, slot, args in adds:
        _run(runner, ["ip", "rule", "add", "pref", str(pref)] + args)
        done.append("ip rule %d %s" % (pref, " ".join(args)))
    for r in dels:
        if runner.run(["ip", "rule", "del", "pref", str(r["pref"])] + r["args"]).ok:
            done.append(tr("gw_net.done.rule_removed", pref=r["pref"], text=r["text"]))
    if hijacked and done:
        with util.journal_lang():                      # the journal speaks the language of the server
            line = tr("gw_net.log.rules_raised", hijacks="; ".join(hijack_text(h) for h in hijacked), start=start,
                      end=start + _span(view["legs"]) - 1)
        util.log_action(line)
    return done


RT_TABLES = ("/etc/iproute2/rt_tables", "/usr/share/iproute2/rt_tables", "/usr/lib/iproute2/rt_tables")


def _route_ours(table, tok):
    """What a route of table 199/200 (``ip route show`` tokens) is of ours: "local" (``local default dev lo``),
    "throw" (``throw <a private /16>``: the base, now or before), "unreachable" (``unreachable default`` of 199) —
    or None: a route Kaskad does not put there (F1, finding 10: never replaced, never deleted)."""
    if len(tok) < 2:
        return None
    if table == ROUTE_TABLE and tok[0] == "throw":
        return "throw" if _RX_PRIVATE16.match(tok[1]) else None
    if tok[1] not in ("default", "0.0.0.0/0"):
        return None
    if table == ROUTE_TABLE and tok[0] == "local" and "dev" in tok[:-1] and tok[tok.index("dev") + 1] == "lo":
        return "local"
    if table == KILL_TABLE and tok[0] == "unreachable":
        return "unreachable"
    return None


def table_names():
    """{199 | 200: (name, file)} — the tables named in rt_tables (whoever named one, uses it)."""
    files = [paths.P(f) for f in RT_TABLES]
    for d in RT_TABLES:
        try:
            files += [os.path.join(paths.P(d + ".d"), n) for n in sorted(os.listdir(paths.P(d + ".d")))
                      if n.endswith(".conf")]
        except OSError:
            pass
    root = paths.P("/").rstrip("/")
    out = {}
    for f in files:
        for ln in (util.read_text(f) or "").splitlines():
            tok = ln.split("#", 1)[0].split()
            try:
                n = int(tok[0], 0) if len(tok) >= 2 else None
            except ValueError:
                continue
            if n in (ROUTE_TABLE, KILL_TABLE) and n not in out:
                out[n] = (tok[1], f[len(root):] if root and f.startswith(root) else f)
    return out


def _table_conflicts(s):
    """{table: [what someone else has there]} of tables 199 and 200: routes that are not ours, foreign ip rules
    looking them up, a name in rt_tables."""
    out = {}
    for table, routes in sorted(s["foreign_routes"].items()):
        out.setdefault(table, []).append(tr("gw_net.table.foreign_routes", table=table,
                                            routes=", ".join(tr("gw_net.quoted", text=r) for r in routes[:3])))
    for f in s["rules"]["foreign"]:
        t = _lookup_of(f["text"])
        if t in (str(ROUTE_TABLE), str(KILL_TABLE)):
            out.setdefault(int(t), []).append(tr("gw_net.table.foreign_rule", table=t, pref=f["pref"],
                                                 text=f["text"]))
    for table, (name, f) in sorted(table_names().items()):
        out.setdefault(table, []).append(tr("gw_net.table.named", table=table, name=name, file=f))
    return out


def table_conflicts(runner, st=None, status=None):
    """[text] — who else uses tables 199/200 (F1, finding 10): what was found and what to do. Kaskad never puts its
    routes into such a table and never takes anything out of it; ``gw probe``/``gw enable`` refuse."""
    s = routing_status(runner, st) if status is None else status
    found = [x for table in sorted(s["conflicts"]) for x in s["conflicts"][table]]
    if not found:
        return []
    return [tr("gw_net.table.conflict", found="; ".join(found), kill=KILL_TABLE, route=ROUTE_TABLE)]


def preflight(runner, st=None):
    """``gw probe`` / ``gw enable``: what stands in the way on this node — tables 199/200 used by someone else, an
    internal port of xray held by another program (``xray.port_conflicts``). "" when nothing."""
    from . import xray
    return "; ".join(table_conflicts(runner, st) + xray.port_conflicts(runner, st))


def routing_status(runner, st):
    """{"rules": rule_view, "hijacks": [...] (only when kgw0 is up — it carries the questions), "local",
    "kill_route", "src_valid_mark", "throw": bool, "throw_stale": [net], "dummy", "dummy_up", "dummy_strict": bool
    (a strict rp_filter on kgw0 would refuse every question), "dummy_addrs": [cidr], "addr_ok": bool}."""
    out = {"local": False, "kill_route": False, "dummy": False, "dummy_up": False, "dummy_addrs": [],
           "addr_ok": False, "src_valid_mark": False, "throw": False, "throw_stale": [], "hijacks": [],
           "dummy_strict": False, "foreign_routes": {}}
    base = str(addr.base(st))
    out["src_valid_mark"] = gsys.sysctl_get(runner, SRC_VALID_MARK) == "1"
    out["rules"] = rule_view(runner, st)
    for table in (ROUTE_TABLE, KILL_TABLE):
        res = runner.run(["ip", "-4", "route", "show", "table", str(table)])
        for ln in (res.out if res.ok else "").splitlines():
            tok = ln.split()
            what = _route_ours(table, tok)
            if what is None and tok:
                out["foreign_routes"].setdefault(table, []).append(" ".join(tok))
            elif what == "local":
                out["local"] = True
            elif what == "unreachable":
                out["kill_route"] = True
            elif what == "throw" and tok[1] == base:
                out["throw"] = True
            elif what == "throw":
                out["throw_stale"].append(tok[1])            # the base before a change of the network
    out["conflicts"] = _table_conflicts(out)
    res = runner.run(["ip", "-o", "link", "show", "dev", DUMMY])
    if res.ok and res.out.strip():
        out["dummy"] = True
        m = re.search(r"<([^>]*)>", res.out)
        out["dummy_up"] = bool(m and "UP" in m.group(1).split(","))
        res = runner.run(["ip", "-4", "-o", "addr", "show", "dev", DUMMY])
        for ln in (res.out if res.ok else "").splitlines():
            tok = ln.split()
            if "inet" in tok[:-1]:
                out["dummy_addrs"].append(tok[tok.index("inet") + 1])
    out["addr_ok"] = out["dummy_addrs"] == ["%s/32" % addr.dns_ip(st)]
    if out["dummy"]:
        out["dummy_strict"] = bool(rpf_status(runner, [DUMMY])["strict"])
    if out["dummy"] and out["dummy_up"]:
        out["hijacks"] = hijacks(runner, st, out["rules"])
    return out


def routing_problems(runner, st):
    s = routing_status(runner, st)
    base = addr.base(st)
    probs = []
    busy = s["conflicts"]                              # tables someone else uses: told by routing_conflicts
    if not s["src_valid_mark"]:
        probs.append(tr("gw_net.routing.src_valid_mark", key=SRC_VALID_MARK))
    if not s["kill_route"] and KILL_TABLE not in busy:
        probs.append(tr("gw_net.routing.no_unreachable", table=KILL_TABLE))
    probs.extend(_rule_problems(st, s["rules"], s["hijacks"]))
    if not s["local"] and ROUTE_TABLE not in busy:
        probs.append(tr("gw_net.routing.no_local"))
    if not s["throw"] and ROUTE_TABLE not in busy:
        probs.append(tr("gw_net.routing.no_throw", base=base))
    if not s["dummy"]:
        probs.append(tr("gw_net.routing.no_dummy", iface=DUMMY, ip=addr.dns_ip(st)))
    else:
        if not s["addr_ok"]:
            probs.append(tr("gw_net.routing.dummy_addr", iface=DUMMY, got=", ".join(s["dummy_addrs"]) or "—",
                            want=addr.dns_ip(st)))
        if not s["dummy_up"]:
            probs.append(tr("gw_net.routing.dummy_down", iface=DUMMY))
        if s["dummy_strict"]:
            probs.append(tr("gw_net.routing.dummy_strict", iface=DUMMY))
    return probs


def ensure_routing(runner, st):
    """Create what is missing (idempotent), the kill switch first. Returns the list of changes. Raises
    KaskadError. Before everything: systemd-networkd's drop-in (written only if networkd is installed and the
    file is missing or different; networkd itself is never restarted). Last: what the kernel decides for each
    class of our rules — a foreign rule that decides one of them moves our block above it (``place_rules``)."""
    done = []
    if gsys.networkd_keep_foreign():
        done.append(tr("gw_net.done.networkd", file=os.path.basename(paths.networkd_dropin())))
    s = routing_status(runner, st)
    # replies first: the kill switch must never catch the reverse path check of a reply (rp_filter)
    if not s["src_valid_mark"]:
        gsys.sysctl_set(runner, SRC_VALID_MARK, 1)
        done.append("%s=1" % SRC_VALID_MARK)
    # ``ip route add``, never ``replace``: replace takes the place of a route with the same prefix and metric
    # WHATEVER its type (an admin's ``default via`` in table 200 of a second uplink). A table someone else uses
    # gets nothing (routing_conflicts tells it; gw_fwd drops what would go astray)
    busy = s["conflicts"]
    if not s["kill_route"] and KILL_TABLE not in busy:
        _run(runner, ["ip", "route", "add", "unreachable", "default", "table", str(KILL_TABLE)])
        done.append(tr("gw_net.done.unreachable", table=KILL_TABLE))
    done.extend(place_rules(runner, st, s["rules"]))
    if not s["local"] and ROUTE_TABLE not in busy:
        _run(runner, ["ip", "route", "add", "local", "0.0.0.0/0", "dev", "lo", "table", str(ROUTE_TABLE)])
        done.append("local 0.0.0.0/0 dev lo table 200")
    base = str(addr.base(st))
    if not s["throw"] and ROUTE_TABLE not in busy:
        _run(runner, ["ip", "route", "add", "throw", base, "table", str(ROUTE_TABLE)])
        done.append("throw %s table 200" % base)
    for b in s["throw_stale"]:
        runner.run(["ip", "route", "del", "throw", b, "table", str(ROUTE_TABLE)])
        done.append(tr("gw_net.done.throw_removed", base=b))
    if not s["dummy"]:
        _run(runner, ["ip", "link", "add", DUMMY, "type", "dummy"])
        done.append(tr("gw_net.done.created", iface=DUMMY))
        s["dummy_addrs"] = []
    want = "%s/32" % addr.dns_ip(st)
    for a in s["dummy_addrs"]:
        if a != want:
            runner.run(["ip", "addr", "del", a, "dev", DUMMY])
            done.append(tr("gw_net.done.addr_removed", iface=DUMMY, addr=a))
    if want not in s["dummy_addrs"]:
        _run(runner, ["ip", "addr", "add", want, "dev", DUMMY])
        done.append("%s: %s" % (DUMMY, want))
    if not s["dummy_up"]:
        _run(runner, ["ip", "link", "set", DUMMY, "up"])
        done.append(tr("gw_net.done.up", iface=DUMMY))
    done.extend(ensure_rp(runner, st))                 # kgw0 too: the questions below come in on it
    view = rule_view(runner, st)
    hij = hijacks(runner, st, view)
    if hij:
        done.extend(place_rules(runner, st, view, hij))
    return done


def remove_routing(runner):
    """Undo ensure_routing (gw disable, net teardown): our rules — of any base, at any pref, those of 3.1.0 too —
    but not the rules of the legs (install.sh owns them: «Прокси для API Telegram» lives without the gateway),
    our routes in tables 200 and 199, kgw0, the patterns of rp_filter (RPF_FILE). Never fails if absent; other routes
    of table 200 stay."""
    n = 0
    for r in rule_view(runner, None)["ours"]:
        if not r["id"].startswith("leg:") and \
                runner.run(["ip", "rule", "del", "pref", str(r["pref"])] + r["args"]).ok:
            n += 1
    runner.run(["ip", "route", "del", "local", "0.0.0.0/0", "dev", "lo", "table", str(ROUTE_TABLE)])
    res = runner.run(["ip", "-4", "route", "show", "table", str(ROUTE_TABLE)])
    for ln in (res.out if res.ok else "").splitlines():
        tok = ln.split()
        if _route_ours(ROUTE_TABLE, tok) == "throw":
            runner.run(["ip", "route", "del", "throw", tok[1], "table", str(ROUTE_TABLE)])
    runner.run(["ip", "route", "del", "unreachable", "default", "table", str(KILL_TABLE)])
    gone = runner.run(["ip", "link", "del", DUMMY]).ok
    release_rp()
    return {"rules": n, "dummy": gone}


_FWMARK_RULE = re.compile(r"\bfwmark (0x[0-9a-fA-F]+)(?:/0x[0-9a-fA-F]+)?\b.*\blookup (\d+)\b")


def policy_routing_left(runner):
    """Policy routing of Kaskad still on the node: the gateway's ``fwmark 0x200 lookup 200`` or an egress leg
    of «Прокси для API Telegram» (an interface kawgN, a rule ``fwmark 256+N lookup 256+N``). An ``ip`` that
    does not answer counts as «left» (the drop-in stays — it is harmless)."""
    res = runner.run(["ip", "-4", "rule", "show"])
    if not res.ok:
        return True
    for ln in res.out.splitlines():
        if re.search(r"\blookup %d\b" % KILL_TABLE, ln):           # the kill switch of the gateway
            return True
        m = _FWMARK_RULE.search(ln)
        if not m:
            continue
        mark, table = int(m.group(1), 16), int(m.group(2))
        if table == ROUTE_TABLE and (mark == exits.MARK_XRAY or mark & 0xff00 == 0x300) or (
                exits.AWG_MARK_BASE < mark <= exits.AWG_MARK_BASE + exits.MAX_AWG_INDEX and mark == table):
            return True
    return any(re.match(r"^kawg\d+$", n) for n in exits.links(runner))


def release_networkd(runner):
    """systemd-networkd's drop-in goes when nothing of ours needs it any more (gw disable, net teardown): no
    gateway rule, no egress leg. True if removed."""
    if not os.path.lexists(paths.networkd_dropin()) or policy_routing_left(runner):
        return False
    return gsys.networkd_drop()


# ── reverse path filtering (F1, finding 6 of the 3.1.0 review) ─────────────
#
# A strict rp_filter (1: what CIS asks of net.ipv4.conf.all) silently kills the AmneziaWG exits: a reply that comes
# in on kawgN (mark 0x10000) is checked against main, where the route to its source goes through eth0; a client
# packet marked 256+N would be checked against table 256+N (kawgN, not the client's interface). The VLESS exits,
# «directly» and the pool live on, the exit looks alive (its handshake is fresh) — only IPReversePathFilter grows.
# The kernel takes max(all, <interface>): the gateway sets 2 (loose) on ITS interfaces only — kgw-* (the clients),
# kgw0 (the watchdog's questions to the kernel come in on it), kawg* (the legs) — and never touches all/default.
# New interfaces: systemd-sysctl applies the patterns of RPF_FILE to an interface as it appears (udev,
# 99-systemd.rules); a PPP interface that accel-ppp renames (ppp0 -> kgw-l2tp0) may miss them — the watchdog sets it
# within a minute, and the clients' own check does not need it: «to <base> lookup main» stands above the rules of
# the legs (``place_rules``), so the reverse lookup of a client packet finds the client's interface whatever its
# mark. IKEv2 needs nothing: the kernel does not check packets that came out of IPsec (secpath).

RPF_FILE = "/etc/sysctl.d/90-kaskad-gw-rpf.conf"
RPF_LOOSE = 2
RPF_ALL = "net.ipv4.conf.all.rp_filter"
RPF_TEXT = "\n".join([
    "# Kaskad 3, шлюз: нестрогая проверка источника (rp_filter=2) на интерфейсах шлюза — клиентов (kgw-*), kgw0",  # i18n: skip — sysctl file on disk, compared byte for byte (rpf_status)
    "# и ногах AmneziaWG (kawg*). Строгая (1, её требует CIS для net.ipv4.conf.all) молча режет выходы AmneziaWG:",  # i18n: skip — sysctl file on disk, compared byte for byte (rpf_status)
    "# ответ с kawgN проверяется по основной таблице, где маршрут к его источнику идёт через внешний интерфейс.",  # i18n: skip — sysctl file on disk, compared byte for byte (rpf_status)
    "# Ядро берёт max(all, интерфейс): общая настройка (all, default) не меняется. systemd-sysctl применяет эти",  # i18n: skip — sysctl file on disk, compared byte for byte (rpf_status)
    "# шаблоны и к новым интерфейсам (правило udev 99-systemd.rules). Файл пишет kaskadctl, убирает gw disable.",  # i18n: skip — sysctl file on disk, compared byte for byte (rpf_status)
    "net.ipv4.conf.kgw-*.rp_filter = 2",
    "net.ipv4.conf.%s.rp_filter = 2" % DUMMY,
    "net.ipv4.conf.kawg*.rp_filter = 2",
    "",
])


def rpf_ifaces(runner):
    """Our interfaces now: kgw0, kgw-* (clients), kawgN (legs) — sorted."""
    return sorted(n for n in exits.links(runner)
                  if n == DUMMY or (n.startswith(CLIENT_IFACES.rstrip("*")) and _IFACE.match(n))
                  or re.match(r"^kawg\d+$", n))


def _sysctl_int(runner, key):
    try:
        return int(gsys.sysctl_get(runner, key))
    except (TypeError, ValueError):
        return None


def rpf_status(runner, ifaces=None):
    """{"all": net.ipv4.conf.all.rp_filter, "strict": [our interfaces where max(all, own) == 1], "file": RPF_FILE
    as it should be}."""
    a = _sysctl_int(runner, RPF_ALL) or 0
    strict = []
    for n in rpf_ifaces(runner) if ifaces is None else ifaces:
        v = _sysctl_int(runner, "net.ipv4.conf.%s.rp_filter" % n)
        if v is not None and max(a, v) == 1:
            strict.append(n)
    return {"all": a, "strict": strict, "file": util.read_text(paths.P(RPF_FILE)) == RPF_TEXT}


def rp_problems(runner, st=None):
    """``gw check``: a strict reverse path check on an interface of the gateway, RPF_FILE missing."""
    s = rpf_status(runner)
    probs = []
    if s["strict"]:
        probs.append(tr("gw_net.rp.strict", all=", %s=1" % RPF_ALL if s["all"] == 1 else "",
                        ifaces=", ".join(s["strict"])))
    if not s["file"]:
        probs.append(tr("gw_net.rp.no_file", file=RPF_FILE))
    return probs


def ensure_rp(runner, st=None, ifaces=None):
    """RPF_FILE (written only when missing or different) and 2 on each of our interfaces where the check is strict
    now (``ifaces``: only those). Never all/default. Returns the list of changes; raises KaskadError."""
    done = []
    p = paths.P(RPF_FILE)
    if util.read_text(p) != RPF_TEXT:
        try:
            util.atomic_write(p, RPF_TEXT, 0o644)
        except OSError as e:
            raise KaskadError(tr("gw_net.err.write", file=RPF_FILE, error=e))
        done.append(tr("gw_net.done.written", file=RPF_FILE))
    for n in rpf_status(runner, ifaces)["strict"]:
        gsys.sysctl_set(runner, "net.ipv4.conf.%s.rp_filter" % n, RPF_LOOSE, persist=False)
        done.append("net.ipv4.conf.%s.rp_filter=%d" % (n, RPF_LOOSE))
    return done


def release_rp():
    """RPF_FILE goes with the gateway (gw disable). The values set stay on the interfaces that are left (the legs
    of «Прокси для API Telegram» keep working). True if removed."""
    try:
        os.unlink(paths.P(RPF_FILE))
        return True
    except OSError:
        return False


# ── connections made while the gateway part was missing ───────────────────

CT_KASKAD = 0x4b000000              # the high byte of the ct mark of every Kaskad connection (gw_pre)


def flush_marks(runner, marks):
    """``conntrack -D --mark 0x4b00XXXX/0xff00ffff`` per route mark: forget the clients' connections that went
    out with a mark whose line of the pool now holds another exit (``exits.renumbered``) — the client
    reconnects and gw_pre gives it the right mark. Returns the number of deleted entries, or None when
    conntrack(8) is not installed."""
    marks = sorted(set(int(m) for m in marks or []))
    if not marks:
        return 0
    if not runner.which("conntrack"):
        return None
    n = 0
    for m in marks:
        res = runner.run(["conntrack", "-D", "--mark", "0x%x/0xff00ffff" % (CT_KASKAD | m)], timeout=30)
        got = re.search(r"(\d+) flow entr", (res.err or "") + (res.out or ""))
        n += int(got.group(1)) if got else 0
    return n


def flush_devices(runner, addrs, mark):
    """``conntrack -D -s <addr> --mark 0x4b00XXXX/0xff00ffff`` per address of the devices moved off an exit (§2.3: «снят»;
    «ухудшен»/«в запас» only with ``drop_on_degrade``): their open connections through it are forgotten — the next
    packet takes the new mark in gw_pre. -> the number of deleted entries, None without conntrack(8)."""
    addrs = sorted(set(str(a) for a in addrs or []))
    if not addrs or not isinstance(mark, int):
        return 0
    if not runner.which("conntrack"):
        return None
    n = 0
    for a in addrs:
        try:
            ipaddress.IPv4Address(a)
        except ValueError:
            continue
        res = runner.run(["conntrack", "-D", "-s", a, "--mark", "0x%x/0xff00ffff" % (CT_KASKAD | mark)], timeout=30)
        got = re.search(r"(\d+) flow entr", (res.err or "") + (res.out or ""))
        n += int(got.group(1)) if got else 0
    return n


_CT_SRC = re.compile(r"\bsrc=(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def flush_direct_dns(runner, st, elems):
    """SEC-3372 П-1: the DNAT of gw_dns is decided once per connection — a device moved off «напрямую» (0x201 -> an
    exit, a block) that keeps asking from the same source port would go on to kaskad-gw-dnsdirect (the direct
    upstream) until conntrack forgets the flow. After every change of gw_route: the flows to the direct dnsmasq
    (``conntrack -L -p udp|tcp --reply-port-src 5364``) of a source whose mark in ``elems`` ({ip: mark} — the map as
    it is now) is not 0x201 are deleted; its next query takes the dnsmasq of its mark. -> deleted, None without
    conntrack(8)."""
    from . import dns as dnsd
    if not os.path.exists(gsys.unit_path(dnsd.UNIT_DIRECT)):   # no direct dnsmasq: nothing to ask it, no conntrack run
        return 0
    if not runner.which("conntrack"):
        return None
    b = addr.base(st)
    n = 0
    for proto in ("udp", "tcp"):
        res = runner.run(["conntrack", "-L", "-p", proto, "--reply-port-src", str(dnsd.PORT_DIRECT)], timeout=10)
        stale = set()
        for ln in (res.out or "").splitlines():
            m = _CT_SRC.search(ln)                     # the first src= — the original direction, the client
            if not m:
                continue
            try:
                a = ipaddress.IPv4Address(m.group(1))
            except ValueError:
                continue
            if a in b and (elems or {}).get(str(a)) != exits.MARK_DIRECT:
                stale.add(str(a))
        for a in sorted(stale):
            res = runner.run(["conntrack", "-D", "-s", a, "-p", proto, "--reply-port-src", str(dnsd.PORT_DIRECT)],
                             timeout=10)
            got = re.search(r"(\d+) flow entr", (res.err or "") + (res.out or ""))
            n += int(got.group(1)) if got else 0
    return n


def ru_sides(live):
    """(gw_rd addresses, {address: mark} of gw_rx, {address: mark} of gw_route) of a live table (``read_live`` or
    ``peek.read``); a set the table does not have — empty."""
    sets = (live or {}).get("set_info") or {}
    return (set_elems(live, RD) if RD in sets else set(), map_elems(live, RX) if RX in sets else {},
            map_elems(live) if MAP in sets else {})


def ru_live(runner):
    """``ru_sides`` of the live table from small calls (no gw_ru) — None when it cannot be read."""
    from . import peek
    try:
        live = peek.read(runner, sets=(MAP, RD, RX), counters=False)
    except Exception:                                  # noqa: BLE001 — not known: nothing is flushed
        return None
    return ru_sides(live) if live is not None else None


def rx_home(st, policy, pool_exits, leases=None):
    """REV-s1-q2-2 З-1: {client ip: [the marks of the exits in the Russian group its device goes through now]} — the
    devices with a ``ru_via`` group of ``policy`` (the effective policy or the stored one), static addresses on enabled
    inbounds and the leases [(ip, device)] (None — ``hooks.leases_for``). A group without exits — an empty list. No
    exit with a mark (the pool unreadable or empty) — None: not known, ``ru_revoked`` takes nothing by it."""
    from . import hooks
    marks = dict((e.get("id"), e.get("mark")) for e in pool_exits or () if isinstance(e, dict) and e.get("mark"))
    if not marks:
        return None
    homes = {}
    for g in (policy or {}).get("groups") or []:
        if isinstance(g, dict) and g.get("role") == "ru":
            homes[g.get("id")] = sorted(set(marks[x] for t in g.get("tiers") or [] if isinstance(t, dict)
                                            for x in t.get("exits") or [] if x in marks))
    if not homes:
        return {}
    kinds = model.enabled_kinds(st)
    out = {}
    via = {}
    for d in model.devices(st):
        v = model.ru_via_of(st, d)["via"]
        if v in homes:
            via[d.get("id")] = homes[v]
            for ip in model.live_ips(st, d, kinds):
                out[ip] = homes[v]
    for ip, d in (hooks.leases_for(st) if leases is None else leases) or ():
        if d.get("id") in via:
            out[ip] = via[d["id"]]
    return out


def ru_revoked(before, after, home=None):
    """SEC-s1-q2 Б-2: {mark: {address}} of the Russian paths the table took away — an address out of gw_rd (0x201) or out
    of gw_rx (its old mark): the consent ⚠РФ withdrawn, «Россия напрямую» off, ``ru_via`` changed, the device off. Their
    open connections keep the ct mark (gw_pre returns them before gw_rd / gw_rx) — so they are forgotten
    (``flush_revoked``). Not when the gw_route mark of the address is that same mark (the connection goes the same way
    anyway), not 0x2ff (nothing goes out under it). ``home`` (``rx_home`` of the table after, REV-s1-q2-2 З-1): an
    address left in gw_rx under another mark loses its old EXIT mark too when that exit is not in the Russian group
    of its device now — ``ru_via`` moved to another group, the exit moved out of the group. Another exit of the same
    group (a step, a failover) stays the collector's ``_flush_moved``; ``home`` None — the address is never taken."""
    if not before or not after:
        return {}
    rd0, rx0, _ = before
    rd1, rx1, route1 = after
    out = {}
    for a in rd0 - rd1:
        if route1.get(a) != exits.MARK_DIRECT:
            out.setdefault(exits.MARK_DIRECT, set()).add(a)
    for a, m in rx0.items():
        if not isinstance(m, int) or m == exits.MARK_BLOCK or route1.get(a) == m:
            continue
        if a in rx1:
            if home is None or rx1[a] == m or m == exits.MARK_DIRECT or m in (home.get(a) or ()):
                continue
            out.setdefault(m, set()).add(a)
            continue
        if m == exits.MARK_DIRECT and a in rd1:
            continue
        out.setdefault(m, set()).add(a)
    return out


def flush_revoked(runner, revoked):
    """``flush_devices`` of ``ru_revoked`` -> the number of entries forgotten, None without conntrack(8)."""
    n = 0
    for mark, ips in sorted((revoked or {}).items()):
        got = flush_devices(runner, ips, mark)
        if got is None:
            return None
        n += got
    return n


def flush_conntrack(runner, st):
    """``conntrack -D -s <base>``: forget the connections of the clients — after the gateway part was
    missing some carry no route (gw_fwd drops them; the client reconnects through gw_pre). Returns the
    number of deleted entries, or None when conntrack(8) is not installed (package conntrack)."""
    if not runner.which("conntrack"):
        return None
    res = runner.run(["conntrack", "-D", "-s", str(addr.base(st))], timeout=30)
    m = re.search(r"(\d+) flow entr", (res.err or "") + (res.out or ""))
    return int(m.group(1)) if m else 0
