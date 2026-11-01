# Kaskad: WireGuard for MikroTik RouterOS 7, device d-dacha
# Paste into New Terminal or run /import file-name=kaskad-wg.rsc
/interface wireguard add name=kaskad-wg mtu=1280 private-key="HhIiiYb8j2f35lJ1RMBP/nIUHVQHBEef64cUuSn8E7A=" comment="Kaskad"
/interface wireguard peers add interface=kaskad-wg public-key="eCk8b7t/oX2FkSmXUUMqo26umbqW6KwJ7O/DeK/QkxY=" preshared-key="X+j84ProK/Weggmfmq+aNAq0qcHXHpUe9nn8mZC9Wzk=" endpoint-address=203.0.113.10 endpoint-port=51820 allowed-address=0.0.0.0/0 persistent-keepalive=25s comment="Kaskad"
/ip address add address=10.77.1.11/32 interface=kaskad-wg comment="Kaskad"
/ip firewall nat add chain=srcnat out-interface=kaskad-wg action=masquerade comment="Kaskad"
/ip firewall mangle add chain=forward out-interface=kaskad-wg protocol=tcp tcp-flags=syn action=change-mss new-mss=clamp-to-pmtu passthrough=yes comment="Kaskad"
/routing table add name=kaskad fib comment="Kaskad"
/ip route add dst-address=0.0.0.0/0 gateway=kaskad-wg routing-table=kaskad comment="Kaskad"
/ip route add dst-address=10.77.0.1/32 gateway=kaskad-wg comment="Kaskad"
# LAN through the tunnel: 192.168.88.0/24 is the default MikroTik LAN, put yours
/routing rule add dst-address=192.168.88.0/24 action=lookup-only-in-table table=main comment="Kaskad"
/routing rule add src-address=192.168.88.0/24 action=lookup-only-in-table table=kaskad comment="Kaskad"
