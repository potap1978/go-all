"""Addresses of the gateway (docs/k3/SPEC-wave2-gateway.md §1.5).

The base is ``10.77.0.0/16``; if it overlaps routes or addresses of the node, the first free one of
``10.78.0.0/16``, ``10.79.0.0/16``, ``172.27.0.0/16`` (``choose_base``; the choice is kept in
``st["gateway"]["net"]["base"]``). Subnets are fixed /24 offsets inside the base::

    pool     third octet   inbound kinds               interface
    wg       1             wg                          kgw-wg
    awg1-3   2, 3, 4       awg1, awg2, awg3            kgw-awg1..3
    ppp      5             l2tp, pptp, sstp (one pool) ppp* (accel-ppp)
    ikev2    8             ikev2 (dynamic, strongSwan) xfrm, no interface
    ovpn_u   9             openvpn (udp)               kgw-ovpn-u
    ovpn_t   10            openvpn (tcp)               kgw-ovpn-t
    ocserv   11            ocserv                      kgw-oc*

``.1`` of every subnet is the node, the DNS of the gateway is ``.0.1`` (on the dummy ``kgw0``). A device
gets ``.10`` and up once per pool and keeps it: ``device["ips"][pool]`` (spec §2 example: ``"ppp"``,
``"ovpn_u"``). IKEv2 addresses come from strongSwan's pool and reach gw_route through ``gw hook``.

Adapters (work package 5) call ``subnet(kind)`` without the state and ``allocate(st, device, kind)`` with
a pool key (wg, awg1-3, ovpn_u, ovpn_t, ocserv) and get the address string. Without ``st`` the base is
the one of the state being applied (``using(st)`` — the core wraps every adapter call in it), else the
saved state.json.
"""

import contextlib
import ipaddress
import os

from .. import paths
from ..errors import KaskadError, StateError
from ..i18n import tr

DEFAULT_BASE = "10.77.0.0/16"
BASE_CANDIDATES = ("10.77.0.0/16", "10.78.0.0/16", "10.79.0.0/16", "172.27.0.0/16")
POOL_OCTET = {"wg": 1, "awg1": 2, "awg2": 3, "awg3": 4, "ppp": 5, "ikev2": 8, "ovpn_u": 9, "ovpn_t": 10,
              "ocserv": 11}
DYNAMIC_POOLS = ("ikev2",)
KIND_POOLS = {"wg": ("wg",), "awg1": ("awg1",), "awg2": ("awg2",), "awg3": ("awg3",), "l2tp": ("ppp",),
              "pptp": ("ppp",), "sstp": ("ppp",), "ikev2": (), "openvpn": ("ovpn_u", "ovpn_t"),
              "ocserv": ("ocserv",), "socks5": (), "vless": (), "hy2": ()}
KIND_SUBNET = {"wg": "wg", "awg1": "awg1", "awg2": "awg2", "awg3": "awg3", "l2tp": "ppp", "pptp": "ppp",
               "sstp": "ppp", "ikev2": "ikev2", "openvpn": "ovpn_u", "ocserv": "ocserv"}
FIRST_HOST = 10
LAST_HOST = 254
# interfaces (prefixes) of the gateway and the egress legs: their routes are ours, not overlaps
OWN_IFACES = ("kgw", "kawg", "ppp", "vpns")    # kgw-* inbounds (ocserv: kgw-oc*), kawgN legs, accel-ppp
OWN_TABLES = ("199", "200", "220")  # kill switch, TPROXY table, strongSwan's table

_CURRENT = []                       # states being applied (innermost last)
_SAVED = {}                         # (mtime, size) -> base of state.json


@contextlib.contextmanager
def using(st):
    """``with addr.using(st):`` — calls without ``st`` (adapters) resolve the base of ``st``."""
    _CURRENT.append(st)
    try:
        yield st
    finally:
        _CURRENT.pop()


def parse_base(value):
    """A base must be a /16 inside RFC 1918 (it carries the /24 offsets above)."""
    try:
        net = ipaddress.IPv4Network(str(value or "").strip())     # strict by default
    except ValueError:
        raise StateError(tr("gw_addr.err.base_format", value=repr(value)))
    if net.prefixlen != 16 or not net.is_private:
        raise StateError(tr("gw_addr.err.base_private", net=net))
    return net


RFC1918 = (ipaddress.IPv4Network("10.0.0.0/8"), ipaddress.IPv4Network("172.16.0.0/12"),
           ipaddress.IPv4Network("192.168.0.0/16"))
WIDE_HOME = ipaddress.IPv4Network("192.168.0.0/16")     # В10: the home networks of many clients — a warning, ``yes``
# Е2: /24 of the home routers met most often (192.168.0/1/8/31/88/100, 10.0.0/1, 10.1.1, 172.16.0) — a /24 of the
# gateway equal to one of them: the client at home loses its own network or the tunnel (a warning, never a ban)
HOME_NETS = tuple(ipaddress.IPv4Network(n) for n in (
    "192.168.0.0/24", "192.168.1.0/24", "192.168.8.0/24", "192.168.31.0/24", "192.168.88.0/24", "192.168.100.0/24",
    "10.0.0.0/24", "10.0.1.0/24", "10.1.1.0/24", "172.16.0.0/24"))


def rfc1918(net):
    """F6 (SPEC-R1 3.3.7.15): ``net`` (an IPv4Network) lies inside 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16.
    ``parse_base`` takes anything ``is_private`` (127.x, 169.254.x, 198.18.x, 240.x, 0.x too) — kept for reading the
    state as it is; a NEW base (``ops.net_base``, ``gw enable --base``) must pass this as well."""
    return any(net.subnet_of(r) for r in RFC1918)


def new_base(value):
    """A base about to be written: ``parse_base`` and ``rfc1918`` — else StateError with the words of the networks
    that can be chosen."""
    net = parse_base(value)
    if not rfc1918(net):
        raise StateError(tr("gw_addr.err.base_rfc1918", net=net))
    return net


def home_clash(base):
    """Е2: the /24 of the gateway in ``base`` (its DNS .0.x and every pool) equal to a popular home network ->
    [str /24], in the order of the third octet."""
    b = parse_base(base) if not isinstance(base, ipaddress.IPv4Network) else base
    nets = [ipaddress.IPv4Network((int(b.network_address) + o * 256, 24))
            for o in sorted(set([0] + list(POOL_OCTET.values())))]
    return [str(n) for n in nets if n in HOME_NETS]


def _saved_base():
    p = paths.state()
    try:
        s = os.stat(p)
    except OSError:
        return None
    key = (p, s.st_mtime_ns, s.st_size)
    if key not in _SAVED:
        from .. import state
        try:
            raw = (((state.load().get("gateway") or {}).get("net") or {}).get("base"))
        except StateError:
            raw = None
        _SAVED.clear()
        _SAVED[key] = raw
    return _SAVED[key]


def base(st=None):
    """IPv4Network of the gateway's base: of ``st``; without it — of the state being applied
    (``using``), else of the saved state; the default if none says."""
    if st is None and _CURRENT:
        st = _CURRENT[-1]
    if st is None:
        raw = _saved_base()
    else:
        raw = (((st or {}).get("gateway") or {}).get("net") or {}).get("base")
    return parse_base(raw or DEFAULT_BASE)


def _pool_net(pool, st=None):
    if pool not in POOL_OCTET:
        raise KaskadError(tr("gw_addr.err.unknown_pool", pool=repr(pool)))
    b = base(st)
    first = int(b.network_address) + POOL_OCTET[pool] * 256
    return ipaddress.IPv4Network((first, 24))


def pools_of(kind):
    """Address pools a device of this inbound kind holds (``()`` — dynamic or none)."""
    return KIND_POOLS.get(kind, ())


def subnet(kind, st=None):
    """``10.77.1.0/24`` — subnet of an inbound kind (or of a pool key: ``ovpn_t``, ``ppp``)."""
    pool = kind if kind in POOL_OCTET else KIND_SUBNET.get(kind)
    if pool is None:
        raise KaskadError(tr("gw_addr.err.no_subnet", kind=kind))
    return str(_pool_net(pool, st))


def server_ip(kind, st=None):
    """``.1`` of the kind's subnet — the node's address on that inbound."""
    return str(ipaddress.IPv4Network(subnet(kind, st)).network_address + 1)


def dns_ip(st=None):
    """``10.77.0.1`` — the DNS (and dummy kgw0 address) of the gateway."""
    return str(base(st).network_address + 1)


def in_base(st, ip):
    try:
        return ipaddress.IPv4Address(str(ip)) in base(st)
    except ValueError:
        return False


def pool_of_ip(st, ip):
    """Which pool an address belongs to (or None)."""
    try:
        a = ipaddress.IPv4Address(str(ip))
    except ValueError:
        return None
    for pool in POOL_OCTET:
        if a in _pool_net(pool, st):
            return pool
    return None


def valid_client_ip(st, ip, pool=None):
    """A device address: inside the base, in a pool (or the given pool), host .10-.254."""
    try:
        a = ipaddress.IPv4Address(str(ip))
    except ValueError:
        return False
    p = pool_of_ip(st, a)
    if p is None or (pool is not None and p != pool):
        return False
    host = int(a) - int(_pool_net(p, st).network_address)
    return FIRST_HOST <= host <= LAST_HOST


def used_ips(st, pool):
    out = set()
    for d in ((st.get("gateway") or {}).get("devices") or []):
        ip = (d.get("ips") or {}).get(pool)
        if ip:
            out.add(ip)
    return out


def _allocate_pool(st, device, pool):
    ips = device.setdefault("ips", {})
    cur = ips.get(pool)
    if cur and valid_client_ip(st, cur, pool):
        return cur
    net = _pool_net(pool, st)
    used = used_ips(st, pool)
    for host in range(FIRST_HOST, LAST_HOST + 1):
        ip = str(net.network_address + host)
        if ip not in used:
            ips[pool] = ip
            return ip
    raise KaskadError(tr("gw_addr.err.pool_full", net=net, count=LAST_HOST - FIRST_HOST + 1))


def _pools(kind):
    if kind in POOL_OCTET:
        return () if kind in DYNAMIC_POOLS else (kind,)
    return pools_of(kind)


def allocate(st, device, kind):
    """The address of ``device`` in the pool of ``kind`` — a pool key (wg, awg1-3, ppp, ovpn_u, ovpn_t,
    ocserv) or an inbound kind (its first pool; every pool of the kind is filled). ``.10`` and up, the
    lowest free one; an existing address never changes. Returns the address string, None for kinds
    without a pool of their own (ikev2 — dynamic, socks5, vless and hy2 — users of xray)."""
    pools = _pools(kind)
    got = [_allocate_pool(st, device, p) for p in pools]
    return got[0] if got else None


def allocate_kind(st, device, kind):
    """Every pool of an inbound kind -> {pool: ip} (openvpn holds two)."""
    return dict((p, _allocate_pool(st, device, p)) for p in _pools(kind))


def release(st, device, kind):
    """Free the addresses of ``kind``'s pools (an inbound kind or a pool key) that no other inbound in
    ``device["access"]`` still uses (l2tp, pptp and sstp share the pool "ppp"). Returns the addresses."""
    keep = set()
    for other in device.get("access") or []:
        if other != kind:
            keep.update(pools_of(other))
    ips = device.get("ips") or {}
    gone = []
    for pool in _pools(kind):
        if pool not in keep and pool in ips:
            gone.append(ips.pop(pool))
    return gone


def rebase(st, old, new):
    """Move every device address from base ``old`` to ``new`` (same offsets). Used when the base changes."""
    o, n = parse_base(old), parse_base(new)
    for d in ((st.get("gateway") or {}).get("devices") or []):
        ips = d.get("ips") or {}
        for pool, ip in list(ips.items()):
            try:
                a = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if a in o:
                ips[pool] = str(n.network_address + (int(a) - int(o.network_address)))


# ── overlap with the node's own routes ─────────────────────────────────────

_ROUTE_TYPES = ("unicast", "local", "broadcast", "multicast", "throw", "unreachable", "prohibit",
                "blackhole", "nat", "anycast")


def _own_dev(dev):
    return any(dev.startswith(p) for p in OWN_IFACES)


def node_networks(runner):
    """[(network, "where")] of the node: routes of every table and interface addresses, except the
    default routes, loopback, the gateway's own interfaces/tables and the egress legs. The address of a leg kawgN is
    taken (as a /32): the kernel holds it as «local» (3.3.6, SEC-336-awgaddr З-4 — a key of a device of another node
    Kaskad, 10.77.x.y, is a good exit; a gateway enabled later takes another base)."""
    out = []
    res = runner.run(["ip", "-4", "-o", "route", "show", "table", "all"])
    for line in (res.out if res.ok else "").splitlines():
        tok = line.split()
        if not tok:
            continue
        if tok[0] in _ROUTE_TYPES:
            tok = tok[1:]
        if not tok or tok[0] == "default":
            continue
        dev = tok[tok.index("dev") + 1] if "dev" in tok[:-1] else ""
        table = tok[tok.index("table") + 1] if "table" in tok[:-1] else "main"
        if _own_dev(dev) or dev == "lo" or table in OWN_TABLES or (table.isdigit() and 256 <= int(table) <= 511):
            continue
        try:
            net = ipaddress.IPv4Network(tok[0], strict=False)
        except ValueError:
            continue
        if net.prefixlen == 0 or net.is_loopback:
            continue
        why = tr("gw_addr.why.route_dev", net=tok[0], dev=dev) if dev else tr("gw_addr.why.route", net=tok[0])
        out.append((net, why))
    res = runner.run(["ip", "-4", "-o", "addr", "show"])
    for line in (res.out if res.ok else "").splitlines():
        tok = line.split()
        if len(tok) < 4 or "inet" not in tok:
            continue
        dev = tok[1].rstrip(":")
        cidr = tok[tok.index("inet") + 1]
        if dev.startswith("kawg"):
            try:
                ip = ipaddress.IPv4Interface(cidr).ip
            except ValueError:
                continue
            out.append((ipaddress.IPv4Network(ip), tr("gw_addr.why.address", cidr="%s/32" % ip, dev=dev)))
            continue
        if _own_dev(dev) or dev == "lo":
            continue
        try:
            net = ipaddress.IPv4Interface(cidr).network
        except ValueError:
            continue
        out.append((net, tr("gw_addr.why.address", cidr=cidr, dev=dev)))
    return out


def overlaps(runner, net, nets=None):
    """What of the node overlaps ``net`` (list of descriptions; empty — free)."""
    n = parse_base(net) if not isinstance(net, ipaddress.IPv4Network) else net
    nets = node_networks(runner) if nets is None else nets
    return [why for other, why in nets if other.overlaps(n)]


def choose_base(runner, current=None):
    """``current`` if still free, else the first free candidate. Returns (base, [why candidates were
    skipped]). Raises KaskadError if every candidate overlaps."""
    nets = node_networks(runner)
    skipped = []
    order = ([str(parse_base(current))] if current else []) + [c for c in BASE_CANDIDATES if c != current]
    for cand in order:
        why = overlaps(runner, cand, nets)
        if not why:
            return cand, skipped
        skipped.append(tr("gw_addr.why.taken", net=cand, why="; ".join(why[:3])))
    raise KaskadError(tr("gw_addr.err.no_free", why=" | ".join(skipped)))
