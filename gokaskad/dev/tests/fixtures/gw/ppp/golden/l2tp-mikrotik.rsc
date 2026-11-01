# Kaskad: L2TP client for MikroTik RouterOS 7, device d-mt
# Paste into New Terminal or run /import file-name=kaskad-l2tp.rsc
/interface l2tp-client add name=kaskad-l2tp connect-to=203.0.113.10 user="mikrotik-7b7b" password="PassMikrotikX7" use-ipsec=yes ipsec-secret="PskOfTheNode23456789abcd" allow=mschap2 profile=default-encryption add-default-route=no disabled=no comment="Kaskad"
/ip firewall nat add chain=srcnat out-interface=kaskad-l2tp action=masquerade comment="Kaskad"
/ip firewall mangle add chain=forward out-interface=kaskad-l2tp protocol=tcp tcp-flags=syn action=change-mss new-mss=clamp-to-pmtu passthrough=yes comment="Kaskad"
/routing table add name=kaskad fib comment="Kaskad"
/ip route add dst-address=0.0.0.0/0 gateway=kaskad-l2tp routing-table=kaskad comment="Kaskad"
/ip route add dst-address=10.77.0.1/32 gateway=kaskad-l2tp comment="Kaskad"
# LAN through the tunnel: 192.168.88.0/24 is the default MikroTik LAN, put yours
/routing rule add dst-address=192.168.88.0/24 action=lookup-only-in-table table=main comment="Kaskad"
/routing rule add src-address=192.168.88.0/24 action=lookup-only-in-table table=kaskad comment="Kaskad"
