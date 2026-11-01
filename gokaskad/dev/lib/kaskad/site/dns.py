"""Where the domain's DNS records lead (docs/k3/SPEC-wave4.md §4, §4.1 п. 7) — the way ``ga_site_dns_state`` of
goAmnezia does it, without new packages: a small DNS client over UDP on the standard library.

    query(name, qtype, servers=None)   -> ("ok", [addresses]) | ("nxdomain", []) | ("fail", [])
                                          A (1) or AAAA (28); a CNAME chain in the answer is followed; servers are
                                          asked in order, each with TIMEOUT and TRIES; the first that answers wins
    own_addresses(st, runner)          -> (IPv4 [..], IPv6 [..]) of this node: gateway.host when it is an IP, the
                                          public IPv4 (as host_auto: the route, else api.ipify.org), the global
                                          addresses of the interfaces (IPv6 without ULA fc00::/7) and «Адреса
                                          сервера» (gw/addresses.py: the external addresses of a NAT cloud, the ones
                                          added by hand — PLAN-multi-ip Ш2, Л7)
    check(domain, st, runner)          -> DNS of the contract:
        {"state": "ok|partial|elsewhere|none|unknown", "a": [...], "aaaa": [...], "own": [...],
         "cloudflare": bool, "aaaa_foreign": bool, "ready": bool, "checked_at": "…Z", "advice": text | None}

States (all A records against all addresses of the node, as sets):
    ok         every A record leads here
    partial    some lead here, some elsewhere (a default record of the registrar, an old server)
    elsewhere  none leads here
    none       no A record (NXDOMAIN, or the name exists without A)
    unknown    no resolver answered, or the node does not know its own addresses (nothing to compare with)
Flags: ``cloudflare`` — every foreign A address is in the networks of Cloudflare (the orange cloud: proxying is on);
``aaaa_foreign`` — an AAAA record not on an IPv6 of the node (Let's Encrypt tries IPv6 first); behind Cloudflare its
own IPv6 is not «foreign». ``ready = ok and not cloudflare and not aaaa_foreign``.
"""

import ipaddress
import random
import socket
import struct

from .. import util
from ..i18n import tr

RESOLVERS = (("1.1.1.1", 53), ("8.8.8.8", 53), ("77.88.8.8", 53))
TIMEOUT = 2.0
TRIES = 2
A, AAAA, CNAME = 1, 28, 5
MAX_CNAME = 8

# cloudflare.com/ips-v4 and ips-v6 — the same lists as goAmnezia (GA_SITE_CF_NETS, GA_SITE_CF_NETS6)
CF_NETS = tuple(ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"))
CF_NETS6 = tuple(ipaddress.ip_network(n) for n in (
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32"))

_rng = random.SystemRandom()


# ── the wire format (RFC 1035) ─────────────────────────────────────────────

def encode_query(name, qtype, qid):
    """A recursive query for ``name`` (ASCII) of ``qtype``."""
    out = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for lab in name.rstrip(".").split("."):
        b = lab.encode("ascii")
        if not 0 < len(b) < 64:
            raise ValueError("label %r" % lab)
        out += bytes([len(b)]) + b
    return out + b"\x00" + struct.pack("!HH", qtype, 1)


def _name(msg, pos, depth=0):
    """(name, position after it) of a (possibly compressed) name at ``pos``."""
    labels = []
    jumped = None
    while True:
        if pos >= len(msg) or depth > 20:
            raise ValueError("name beyond the answer")
        n = msg[pos]
        if n & 0xC0 == 0xC0:
            if pos + 1 >= len(msg):
                raise ValueError("truncated pointer")
            ptr = ((n & 0x3F) << 8) | msg[pos + 1]
            if jumped is None:
                jumped = pos + 2
            pos = ptr
            depth += 1
            continue
        if n == 0:
            pos += 1
            break
        labels.append(msg[pos + 1:pos + 1 + n].decode("ascii", "replace").lower())
        pos += 1 + n
    return ".".join(labels), (jumped if jumped is not None else pos)


def decode_answer(msg, qid, name, qtype):
    """("ok", [addresses]) | ("nxdomain", []) | ("fail", []) of a DNS answer. The addresses are those of ``name``
    or of the names its CNAME chain (in this answer) leads to."""
    if len(msg) < 12:
        return "fail", []
    rid, flags, qd, an, _, _ = struct.unpack("!HHHHHH", msg[:12])
    if rid != qid or not flags & 0x8000:
        return "fail", []
    rcode = flags & 0x000F
    if rcode == 3:
        return "nxdomain", []
    if rcode != 0:
        return "fail", []
    pos = 12
    try:
        for _ in range(qd):
            _, pos = _name(msg, pos)
            pos += 4
        records = []
        for _ in range(an):
            owner, pos = _name(msg, pos)
            rtype, _, _, rdlen = struct.unpack("!HHIH", msg[pos:pos + 10])
            pos += 10
            rdata_at = pos
            pos += rdlen
            if pos > len(msg):
                raise ValueError("truncated record")
            if rtype == CNAME:
                target, _ = _name(msg, rdata_at)
                records.append((owner, CNAME, target))
            elif rtype == A and rdlen == 4:
                records.append((owner, A, str(ipaddress.IPv4Address(msg[rdata_at:pos]))))
            elif rtype == AAAA and rdlen == 16:
                records.append((owner, AAAA, str(ipaddress.IPv6Address(msg[rdata_at:pos]))))
    except (ValueError, struct.error, IndexError):
        return "fail", []
    names = {name.lower().rstrip(".")}
    for _ in range(MAX_CNAME):                          # a chain: name -> cname -> … (in any order in the answer)
        more = set(v for o, t, v in records if t == CNAME and o in names) - names
        if not more:
            break
        names |= more
    found = []
    for o, t, v in records:
        if t == qtype and o in names and v not in found:
            found.append(v)
    return "ok", found


def _ask(server, packet, timeout):
    """One UDP exchange; the answer bytes or None (timeout, unreachable)."""
    host, port = server
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_DGRAM)
    try:
        s.settimeout(timeout)
        s.sendto(packet, (host, port))
        while True:
            data, peer = s.recvfrom(4096)
            if peer[0] == host:
                return data
    except (OSError, socket.timeout):
        return None
    finally:
        s.close()


def query(name, qtype, servers=None, timeout=None, tries=None):
    """See the module doc. A resolver that answers SERVFAIL/REFUSED or garbage counts as silent."""
    servers = RESOLVERS if servers is None else servers
    timeout = TIMEOUT if timeout is None else timeout
    tries = TRIES if tries is None else tries
    for server in servers:
        for _ in range(tries):
            qid = _rng.randint(0, 0xFFFF)
            data = _ask(server, encode_query(name, qtype, qid), timeout)
            if data is None:
                continue
            res = decode_answer(data, qid, name, qtype)
            if res[0] != "fail":
                return res
            break                                       # it answered «fail»: the next resolver, not a retry
    return "fail", []


# ── the node's own addresses ───────────────────────────────────────────────

def own_addresses(st, runner):
    """(IPv4 list, IPv6 list) of this node — see the module doc."""
    from ..gw import model, net
    v4, v6 = set(), set()
    try:
        for a in net.node_addresses(runner, st):
            ip = ipaddress.ip_interface(a).ip
            (v4 if ip.version == 4 else v6).add(ip)
    except Exception:                                   # noqa — a broken tool must not break the check
        pass
    h = model.public_host(st or {})
    try:
        ip = ipaddress.ip_address(h)
        (v4 if ip.version == 4 else v6).add(ip)
    except ValueError:
        pass
    try:
        got, _ = net.detect_host(runner)
        ip = ipaddress.IPv4Address(got) if got else None
        if ip is not None and ip.is_global:
            v4.add(ip)
    except Exception:                                   # noqa — no route, no curl: the other sources stay
        pass
    try:
        # a cloud with 1:1 NAT: the external address of every private one (looked at once in 10 min), added by hand
        from ..gw import addresses
        for x in addresses.known_list(addresses.collect(runner, st)):
            v4.add(ipaddress.IPv4Address(x))
    except Exception:                                   # noqa — a broken source never breaks the check
        pass
    v6 = set(ip for ip in v6 if ip.is_global and ip not in ipaddress.ip_network("fc00::/7"))
    return sorted(str(x) for x in sorted(v4)), sorted(str(x) for x in sorted(v6))


def in_cloudflare(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    nets = CF_NETS if ip.version == 4 else CF_NETS6
    return any(ip in n for n in nets)


# ── the check ──────────────────────────────────────────────────────────────

def evaluate(a_status, a, aaaa, own4, own6):
    """The DNS document from the answers (pure: the tests feed it directly)."""
    own_set = set(own4)
    ours = [x for x in a if x in own_set]
    foreign = [x for x in a if x not in own_set]
    cloudflare = bool(foreign) and all(in_cloudflare(x) for x in foreign)
    if a_status == "fail" or not own4:
        state = "unknown"
    elif not a:
        state = "none"
    elif not ours:
        state = "elsewhere"
    elif foreign:
        state = "partial"
    else:
        state = "ok"
    ok6 = set(own6)
    if cloudflare:
        ok6 |= set(x for x in aaaa if in_cloudflare(x))     # behind the proxy its own IPv6 is not «foreign»
    aaaa_foreign = any(x not in ok6 for x in aaaa)
    return {"state": state, "a": list(a), "aaaa": list(aaaa), "own": list(own4) + list(own6),
            "cloudflare": cloudflare, "aaaa_foreign": aaaa_foreign,
            "ready": state == "ok" and not cloudflare and not aaaa_foreign}


def advice(domain, d):
    """What to do, in words of goAmnezia (Kaskad's), or None when all is well."""
    from .model import unicode
    name = unicode(domain)
    own4 = [x for x in d["own"] if ":" not in x]
    if d["cloudflare"]:
        return tr("site.dns.cloudflare", name=name)
    st = d["state"]
    if st == "none":
        return tr("site.dns.none", name=name, own=", ".join(own4) or tr("site.dns.server_address"))
    if st == "elsewhere":
        return tr("site.dns.elsewhere", name=name, a=", ".join(d["a"]),
                  own=", ".join(own4) or tr("site.dns.own_unknown"))
    if st == "partial":
        foreign = [x for x in d["a"] if x not in d["own"]]
        return tr("site.dns.partial", name=name, foreign=", ".join(foreign), own=", ".join(own4))
    if st == "unknown":
        if not own4:
            return tr("site.dns.no_own", name=name)
        return tr("site.dns.unknown", name=name)
    if d["aaaa_foreign"]:
        own6 = [x for x in d["aaaa"] if x in d["own"]]
        return tr("site.dns.aaaa", name=name, aaaa=", ".join(x for x in d["aaaa"] if x not in own6))
    return None


def check(domain, st, runner, servers=None):
    """DNS of the contract for ``domain`` (ASCII) — see the module doc."""
    a_status, a = query(domain, A, servers)
    aaaa_status, aaaa = query(domain, AAAA, servers)
    own4, own6 = own_addresses(st, runner)
    d = evaluate(a_status, a, aaaa if aaaa_status == "ok" else [], own4, own6)
    d["checked_at"] = util.now_iso()
    d["advice"] = advice(domain, d)
    return d
