"""The gateway of Kaskad 3 (wave 2, docs/k3/SPEC-wave2-gateway.md): a node in Russia that takes simple
VPN protocols from routers, phones and PCs and sends their traffic abroad through the exits of the pool.

The core (work package 4) — this package:

    model      users, devices, accesses, routes on the dict state (state.json -> "gateway")
    addr       10.77.0.0/16 (or a free alternative), subnets of the inbounds, addresses of devices
    net        the gw_* part of table ``ip kaskad`` (rendered into nft.render_full/apply), ip rules,
               table 200, dummy kgw0 10.77.0.1, the ip6 block, the probe of the constructs
    exits      the pool of /etc/kaskad/egress/upstream.txt through egress_parse.py, liveness of the legs,
               the route mark of every device (rendezvous hash)
    xray       kaskad-gw-xray.service: TPROXY 127.0.0.1:12345, DNS 127.0.0.1:10853, the inbounds SOCKS5 and VLESS
               Reality of the adapters (docs/k3/SPEC-vless-inbound.md)
    dns        kaskad-dns.service: dnsmasq on 10.77.0.1
    hooks      ``kaskadctl gw hook <kind> up|down <identity> <ip>`` (dynamic addresses, sessions)
    collect    kaskad-gw-collect.service: exits watchdog every 10 s, sessions
    sys        apt under policy-rc.d, units, ufw, modprobe, sysctl
    ops        enable / disable / apply / check / status — what the CLI and the menu call
    cli        ``kaskadctl gw ...``
    registry   adapters of the inbounds; ``inbounds/base.py`` — their interface (+ DummyInbound)

Protocol adapters (wg, awg, openvpn, ocserv, socks5 — work package 5; accel-ppp, l2tp, pptp, sstp,
ikev2 — work package 6; vless — VLESS Reality) live in ``kaskad.gw.inbounds`` and plug in through ``registry``.
"""
