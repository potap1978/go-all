"""Stand-in of kaskad/gw/addr.py (WP4): the subnets of SPEC §1.5, addresses .10 and up."""

from kaskad.gw.inbounds.base import InboundError

NETS = {"wg": 1, "awg1": 2, "awg2": 3, "awg3": 4, "ppp": 5, "ikev2": 8, "ovpn_u": 9, "ovpn_t": 10, "ocserv": 11}
CALLS = []


def subnet(kind):
    return "10.77.%d.0/24" % NETS[kind]


def server_ip(kind):
    return "10.77.%d.1" % NETS[kind]


def allocate(st, device, kind):
    CALLS.append((device.get("id"), kind))
    ips = device.setdefault("ips", {})
    if ips.get(kind):
        return ips[kind]
    used = set()
    for d in st.get("gateway", {}).get("devices", []):
        used.add((d.get("ips") or {}).get(kind))
    for host in range(10, 255):
        ip = "10.77.%d.%d" % (NETS[kind], host)
        if ip not in used:
            ips[kind] = ip
            return ip
    raise InboundError("нет свободного адреса")


def release(st, device, kind):
    (device.get("ips") or {}).pop(kind, None)
