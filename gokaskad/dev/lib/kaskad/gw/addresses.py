"""«Адреса сервера» — one list of the addresses of this machine (k3/handoff/briefs/PLAN-multi-ip-2026-10-29.md §2.2
С1, §3, Ш1; k3/SPEC-multi-ip.md §5). Only reads the system: Kaskad never adds or removes an address, a route or a
rule of the machine (§4.2).

Sources, in this order (``origin``):

    iface    the global IPv4 of the interfaces (``ip -o addr show``) without the gateway's own (kgw*, kawg*, ppp*,
             vpns*), the loopback and the container networks (docker*, br-*, veth*, …: CONTAINER_IFACES);
    nat      1:1 NAT of a cloud: a private IPv4 of an interface → the public one the IP echo sees from it (``curl -4
             --interface <private> https://api.ipify.org``). Only when no global IPv4 is on the interfaces or more
             than one private one is, at most once in FRESH s (or by ``--refresh``), in parallel, NAT_TIMEOUT s each;
             between the probes the pairs of the cache whose private address is still here;
    manual   ``gateway.addresses.manual`` [{"ip", "local"?}] of the state — what the machine never sees
             (``gw addresses add``); «не проверен» unless its ``local`` is on the machine;
    host     ``gateway.host`` when it is an IPv4 none of the above found: «не найден на сервере» (it went away at the
             hoster, a typo, the copy of another server).

Per address: ``primary`` — the source of the default route (or its NAT pair); ``route_ok`` — ``ip -4 route get
1.1.1.1 from <local>`` leaves by the device and gateway of the default route (False: another way — a source routing of
the administrator; None: not asked); ``in_dns`` — among the A records of the site's domain at its last check (None: no
domain or not checked); ``client`` — it is the address for clients (``gateway.host``).

What the machine has is its own: ``/var/lib/kaskad/gw/addresses.json`` (``cache_path``; never in a copy — backup.py
takes from /var/lib/kaskad/gw only traffic.json). The state holds only the manual list; it goes with a copy to the same
server and not to another one (backup.py, ``_addresses_of_this_machine``).

IPv6 (3.3.7.15 F10, k3/handoff/SPEC-R1-3315-2026-10-30.md; PLAN-multi-ip-roles §9.4): ``addresses6`` of the cache and
of the document — the global IPv6 of the interfaces with ``deprecated`` (kept: it takes connections until its
``valid_lft``), ``dynamic`` and ``valid_lft`` (s, None — forever), ``route_ok6`` (``ip -6 route get`` from it leaves
the way of the default IPv6 route; no such route — False) and ``echo6`` (what ``curl -6 --interface`` sees, at most
once in FRESH s, with the NAT probes); never ``temporary``, ``tentative``, ``dadfailed`` or link-local. The keys
``addresses`` (IPv4) and ``v6`` (strings) are as before: the menu, the bot, MCP and the panel read them.

    collect(runner, st=None, refresh=False, net=True)   -> the document (``kaskadctl gw addresses --json``)
    cached(st)                                          -> the same from the files only (summary: the menu header)
    known(doc, ip)                                      -> ip is an address of this server (found or added by hand)
    listen_extra(st, found)                             -> the manual addresses the panel listens on too
    in6_list(st)                                        -> ``gateway.addresses.in6``: None — every IPv6 takes them
    give6_of(st) / give6_state(st, m)                   -> F13: the IPv6 written to be given / given now (§6.2)
    prefix_changes(prev6, now6)                         -> the events «префикс IPv6 сменился» (the collector)
    page(st, doc)                                       -> the rows of the page «Адреса сервера» with state codes
"""

import calendar
import concurrent.futures
import ipaddress
import json
import os
import re
import time

from .. import paths, state, util
from ..errors import ConflictError, KaskadError
from ..i18n import tr
from . import addr as gaddr

CACHE = "addresses.json"
FRESH = 600                     # s: the NAT probes are not repeated sooner (the interfaces are looked at every time)
NAT_MAX = 8                     # private addresses probed at most (in parallel)
NAT_TIMEOUT = 5                 # s per probe (curl --max-time)
IP_ECHO = "https://api.ipify.org"   # as net.IP_ECHO (detect_host)
IP_ECHO6 = "https://api6.ipify.org"
PROBE_DST = "1.1.1.1"           # the default route is the one to it (as detect_host, panel.conf.detect_addresses)
PROBE_DST6 = "2606:4700:4700::1111"     # the default IPv6 route is the one to it
MANUAL_MAX = 32
V6_MAX = 32                     # IPv6 of the machine kept in the cache (the first ones ``ip`` prints)
# not the server's addresses: containers, bridges of containers and VMs of a host's own network, overlays, VPN clients
CONTAINER_IFACES = ("docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "cilium", "weave", "vxlan", "kube",
                    "lxc", "lxd", "podman", "tailscale", "zt")
# the private networks a cloud maps to public addresses (RFC 1918 and CGNAT) — not the documentation ranges, not the
# other special ones: those are neither the server's public addresses (``is_global``) nor a NAT's
NAT_NETS = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))
ORIGINS = ("iface", "nat", "manual", "host")

_now = time.time                # tests patch it


def cache_path():
    return os.path.join(paths.gw_var_dir(), CACHE)


def skip_iface(dev):
    """The interface holds no address of the server: the loopback, the gateway's own, a container network."""
    return dev == "lo" or dev.startswith(gaddr.OWN_IFACES) or dev.startswith(CONTAINER_IFACES)


def _v4(ip):
    try:
        return ipaddress.IPv4Address(str(ip))
    except ValueError:
        return None


def is_public4(ip):
    a = _v4(ip)
    return a is not None and a.is_global


def _nat_local(ip):
    """A private IPv4 a cloud may map to a public one (NAT_NETS)."""
    a = _v4(ip)
    return a is not None and any(a in n for n in NAT_NETS)


def _v6(ip):
    try:
        return ipaddress.IPv6Address(str(ip).strip())
    except (ValueError, TypeError):
        return None


def is_public6(ip):
    """A global IPv6 (``is_global``: not the loopback, link-local, ULA, multicast or the documentation 2001:db8::/32 —
    the tests patch this one)."""
    a = _v6(ip)
    return a is not None and a.is_global


def canon6(ip):
    """The one written form of an IPv6 (``str(ip_address)``: the registry of the stream mode compares strings), None —
    not an IPv6."""
    a = _v6(ip)
    return str(a) if a is not None else None


def net64(ip):
    """The /64 of an IPv6 as text (``2001:db8:10::/64``), "" — not an IPv6."""
    a = _v6(ip)
    return str(ipaddress.IPv6Network((int(a) >> 64 << 64, 64))) if a is not None else ""


# ── parsing (pure: the tests feed the texts) ───────────────────────────────

def parse_addrs(text):
    """``ip -o addr show`` -> [{"ip", "prefix", "iface", "v": 4|6, "secondary": bool}] of the addresses that may be the
    server's, in the order ``ip`` prints them: no loopback, link-local, multicast, scope host/link, nothing tentative,
    no deprecated or temporary IPv6 (privacy addresses come and go), no interface of ``skip_iface``. A deprecated IPv4
    stays (``preferred_lft 0`` keeps a second address from becoming the source of the route — REV З-4). An alias label
    (``eth0:0``) is the device ``eth0``."""
    out = []
    for line in (text or "").splitlines():
        tok = line.replace("\\", " ").split()
        if len(tok) < 4 or tok[2] not in ("inet", "inet6"):
            continue
        dev = tok[1].rstrip(":").split("@", 1)[0]
        if not dev or skip_iface(dev):
            continue
        if any(f in tok for f in ("tentative", "dadfailed")):
            continue
        if tok[2] == "inet6" and any(f in tok for f in ("deprecated", "temporary")):
            continue
        if "scope" in tok and tok.index("scope") + 1 < len(tok) and tok[tok.index("scope") + 1] in ("host", "link",
                                                                                                   "nowhere"):
            continue
        try:
            itf = ipaddress.ip_interface(tok[3])
        except ValueError:
            continue
        a = itf.ip
        if a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified:
            continue
        out.append({"ip": str(a), "prefix": itf.network.prefixlen, "iface": dev, "v": a.version,
                    "secondary": "secondary" in tok})
    return out


def _lft(tok, key):
    """``valid_lft 86000sec`` -> 86000, ``forever`` (or nothing) -> None."""
    if key not in tok or tok.index(key) + 1 >= len(tok):
        return None
    v = tok[tok.index(key) + 1]
    m = re.match(r"^(\d+)(?:sec)?$", v)
    return int(m.group(1)) if m else None


def parse_addrs6(text):
    """``ip -o addr show`` -> [{"ip", "iface", "prefix", "deprecated", "dynamic", "valid_lft"}] of the global IPv6 of
    the interfaces (``parse_addrs`` and its answer are left as they are — F10): no ``temporary`` (privacy addresses come
    and go), ``tentative``, ``dadfailed``, scope host/link, no interface of ``skip_iface``. ``deprecated`` stays, marked:
    the address takes connections until its ``valid_lft`` ends (the hoster changed the prefix) — a filter of IPv6 must
    not cut the devices sitting on it. ``mngtmpaddr`` is a usual address (the privacy ones are made from it)."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        tok = line.replace("\\", " ").split()
        if len(tok) < 4 or tok[2] != "inet6":
            continue
        dev = tok[1].rstrip(":").split("@", 1)[0]
        if not dev or skip_iface(dev):
            continue
        if any(f in tok for f in ("tentative", "dadfailed", "temporary")):
            continue
        if "scope" in tok and tok.index("scope") + 1 < len(tok) and tok[tok.index("scope") + 1] in ("host", "link",
                                                                                                   "nowhere"):
            continue
        try:
            itf = ipaddress.IPv6Interface(tok[3])
        except ValueError:
            continue
        ip = str(itf.ip)
        if not is_public6(ip) or ip in seen:
            continue
        seen.add(ip)
        out.append({"ip": ip, "iface": dev, "prefix": itf.network.prefixlen, "deprecated": "deprecated" in tok,
                    "dynamic": "dynamic" in tok, "valid_lft": _lft(tok, "valid_lft")})
    return out[:V6_MAX]


def parse_route(text):
    """``ip -4 route get <dst> [from <src>]`` -> {"dev", "via", "src"} ("" for what it does not say), None when it
    names no device."""
    t = text or ""
    dev = re.search(r"\bdev (\S+)", t)
    if not dev:
        return None
    via = re.search(r"\bvia (\S+)", t)
    src = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", t)
    return {"dev": dev.group(1), "via": via.group(1) if via else "", "src": src.group(1) if src else ""}


def route_text(r):
    if not r:
        return ""
    return ("via %s dev %s" % (r["via"], r["dev"])) if r.get("via") else ("dev %s" % r["dev"])


def same_way(r, main):
    """The answer from a local address leaves the way of the default route (the same device and gateway): None when
    either is unknown."""
    if not r or not main:
        return None
    return r.get("dev") == main.get("dev") and (r.get("via") or "") == (main.get("via") or "")


# ── the machine (the cache) ────────────────────────────────────────────────

def load_cache():
    raw = util.read_text(cache_path())
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("addrs"), list):
        return None
    return doc


def save_cache(m):
    try:
        util.atomic_write(cache_path(), json.dumps(m, ensure_ascii=False, sort_keys=True) + "\n", 0o600)
    except OSError:
        pass                                            # not root (a test, a read): the machine is asked again


def _echo(runner, local):
    res = runner.run(["curl", "-s", "-4", "--interface", local, "--max-time", str(NAT_TIMEOUT), IP_ECHO],
                     timeout=NAT_TIMEOUT + 5)
    a = _v4((res.out or "").strip()) if res.ok else None
    return str(a) if a is not None and a.is_global else None


def _echo6(runner, local):
    """What the IP echo of IPv6 sees through ``local`` (canonical), None — no answer."""
    res = runner.run(["curl", "-s", "-6", "--interface", local, "--max-time", str(NAT_TIMEOUT), IP_ECHO6],
                     timeout=NAT_TIMEOUT + 5)
    return canon6((res.out or "").strip()) if res.ok else None


def _probe(runner, nat_locals=(), v6_locals=()):
    """({local: public|None} of the IPv4 echo, {IPv6: seen|None} of the IPv6 echo) — every probe at once, in parallel
    (at most NAT_MAX of each)."""
    jobs = [(_echo, "4", l) for l in list(nat_locals)[:NAT_MAX]] + [(_echo6, "6", l) for l in list(v6_locals)[:NAT_MAX]]
    out = {"4": {}, "6": {}}
    if not jobs:
        return out["4"], out["6"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = dict((ex.submit(fn, runner, l), (fam, l)) for fn, fam, l in jobs)
        for f in concurrent.futures.as_completed(futs):
            fam, l = futs[f]
            try:
                out[fam][l] = f.result()
            except Exception:                           # noqa: BLE001 — a probe that broke: no pair, never a failure
                out[fam][l] = None
    return out["4"], out["6"]


def _probe_nat(runner, locals_):
    """{local: public|None} — the IP echo through each local address, in parallel (at most NAT_MAX)."""
    return _probe(runner, locals_)[0]


def nat_wanted(found):
    """NAT is looked for: no global IPv4 on the interfaces (the cloud's one address is private), or more than one
    private one (a cloud with several addresses). A public address plus one private network of the hoster — no."""
    pub = [a for a in found if a["v"] == 4 and is_public4(a["ip"])]
    priv = [a for a in found if a["v"] == 4 and _nat_local(a["ip"])]
    return bool(priv) and (not pub or len(priv) > 1)


def _route_ok6(way, main6):
    """An answer from an IPv6 leaves the way of the default IPv6 route; no such route (or no answer) — False («IPv6
    есть, маршрута наружу нет»: never given to the devices)."""
    return bool(main6) and bool(way) and same_way(way, main6) is True


def _ways6(runner, found6):
    """(the default IPv6 route, {IPv6: the way an answer from it leaves}) — no IPv6 on the machine, no command."""
    if not found6:
        return None, {}
    r = runner.run(["ip", "-6", "route", "get", PROBE_DST6])
    main6 = parse_route(r.out) if r.ok else None
    if main6 is not None:                               # F13: the source of the IPv6 route (parse_route reads IPv4 only)
        s6 = re.search(r"\bsrc ([0-9A-Fa-f:]+)", r.out or "")
        main6["src"] = (canon6(s6.group(1)) or "") if s6 else ""
    ways = {}
    for a in found6:
        rr = runner.run(["ip", "-6", "route", "get", PROBE_DST6, "from", a["ip"]])
        ways[a["ip"]] = parse_route(rr.out) if rr.ok else None
    return main6, ways


PROC_NET = "/proc/sys/net"              # the sysctl tree of the network (paths.P: tests set KASKAD_ROOT)


def _sysctl_flag(path):
    """A flag of /proc/sys (``disable_ipv6``): True — not 0, False — 0, None — not read (no file, no right, not a
    number)."""
    try:
        with open(path, "r", encoding="ascii", errors="replace") as f:
            return int(f.read(32).strip()) != 0
    except (OSError, ValueError):
        return None


def _iface_name(dev):
    """A name of ``ip`` usable as a directory of /proc/sys/net/ipv6/conf (no separator, not «.»/«..»)."""
    return isinstance(dev, str) and 0 < len(dev) <= 64 and dev not in (".", "..") and "/" not in dev and \
        "\x00" not in dev


def ipv6_off(ifaces):
    """3.3.7.15 F10 С3: IPv6 is turned off in the kernel — the page says «IPv6 выключен в ядре», not «IPv6 нет».
    True — the kernel has no IPv6 (``ipv6.disable=1``: no /proc/sys/net/ipv6 while /proc/sys/net is there), or
    ``disable_ipv6`` is on at every interface of ``ifaces`` (the interfaces of the server's addresses and of the default
    route; an interface whose file is not read — the value of ``all``; no interface — ``all`` alone); False — IPv6 is
    on at one of them at least; None — not read (no /proc/sys/net, no file, no right): nothing is said."""
    net = paths.P(PROC_NET)
    if not os.path.isdir(net):
        return None
    conf = os.path.join(net, "ipv6", "conf")
    if not os.path.isdir(os.path.join(net, "ipv6")):
        return True
    every = _sysctl_flag(os.path.join(conf, "all", "disable_ipv6"))
    vals = []
    for dev in ifaces or ():
        if _iface_name(dev):
            v = _sysctl_flag(os.path.join(conf, dev, "disable_ipv6"))
            vals.append(every if v is None else v)
    vals = vals or [every]
    if False in vals:
        return False
    return True if all(v is True for v in vals) else None


def _ifaces(found, main):
    """The interfaces of the server: of its addresses (``parse_addrs``), then of the default route — once each."""
    out = []
    for dev in [a.get("iface") for a in found] + [(main or {}).get("dev")]:
        if dev and dev not in out and not skip_iface(dev):
            out.append(dev)
    return out


def discover(runner, prev=None, nat=False, echo6=False):
    """The machine now -> the document of the cache: the addresses of the interfaces, the default route, the NAT pairs
    (probed when ``nat`` and ``nat_wanted``, else the ones of ``prev`` whose local address is still here; a local
    address the probe got no answer through keeps its pair of ``prev`` — only an answer replaces a pair, REV С-2) and
    the way an answer from each local address leaves; ``addresses6`` with the way out and the echo of IPv6 (probed when
    ``echo6``, in parallel with the NAT probes, only through the IPv6 with a way out; else — and on no answer — the
    echo of ``prev`` of the same address). Raises KaskadError when ``ip`` does not answer."""
    res = runner.run(["ip", "-o", "addr", "show"])
    if not res.ok:
        raise KaskadError(tr("gw_addresses.err.ip", err=(res.err or res.out or "").strip()[-200:] or "rc %s" % res.rc))
    found = parse_addrs(res.out)
    found6 = parse_addrs6(res.out)
    r = runner.run(["ip", "-4", "route", "get", PROBE_DST])
    main = parse_route(r.out) if r.ok else None
    now = int(_now())
    here = set(a["ip"] for a in found)
    wanted = nat_wanted(found)
    pairs, nat_at = [], None
    if prev and isinstance(prev.get("nat"), list):
        pairs = [p for p in prev["nat"] if isinstance(p, dict) and p.get("local") in here and is_public4(p.get("ip"))]
        nat_at = prev.get("nat_at")
    main6, ways6 = _ways6(runner, found6)
    ok6 = dict((a["ip"], _route_ok6(ways6.get(a["ip"]), main6)) for a in found6)
    e6 = [a["ip"] for a in found6 if ok6[a["ip"]]] if echo6 else []
    cands = []
    if nat and wanted:
        src = (main or {}).get("src")
        cands = [a["ip"] for a in found if a["v"] == 4 and _nat_local(a["ip"])]
        cands.sort(key=lambda ip: (ip != src,))          # the route's own first: its pair is the primary one
    got, seen6 = _probe(runner, cands, e6) if (cands or e6) else ({}, {})
    if nat and wanted:
        pub_here = set(a["ip"] for a in found if a["v"] == 4 and is_public4(a["ip"]))
        old = dict((p["local"], p["ip"]) for p in pairs)
        pairs, seen = [], set(pub_here)
        for local in cands:
            ip = got.get(local) or old.get(local)        # no answer (ipify isn't reached from everywhere): the last
            if ip and ip not in seen:                    # two locals behind one public: the first one keeps it
                pairs.append({"local": local, "ip": ip})
                seen.add(ip)
        nat_at = now
    ways = {}
    locals_ = [a["ip"] for a in found if a["v"] == 4 and is_public4(a["ip"])] + [p["local"] for p in pairs]
    for local in locals_:
        if local in ways:
            continue
        rr = runner.run(["ip", "-4", "route", "get", PROBE_DST, "from", local])
        ways[local] = parse_route(rr.out) if rr.ok else None
    old6 = dict((a.get("ip"), a.get("echo6")) for a in (prev or {}).get("addresses6") or [] if isinstance(a, dict))
    addrs6 = [dict(a, route_ok6=ok6[a["ip"]], echo6=seen6.get(a["ip"]) or canon6(old6.get(a["ip"]) or ""))
              for a in found6]
    e6_at = (prev or {}).get("echo6_at") if prev else None
    if e6:
        e6_at = now
    return {"v": 1, "at": now, "route": main, "addrs": found, "nat": pairs, "nat_at": nat_at, "nat_wanted": wanted,
            "ways": ways, "seen": _seen(prev, found, pairs), "addresses6": addrs6, "route6": main6, "echo6_at": e6_at,
            "ipv6_off": ipv6_off(_ifaces(found, main))}


SEEN_MAX = 32                           # public IPv4 kept in «seen»: the last ones found


def _seen(prev, found, pairs):
    """The public IPv4 ever found on this machine (the interfaces and the NAT pairs; ``prev`` — the cache before): an
    address for clients missing now is «gone» only when it was here once (REV-mip-s12 З-2: an address the server never
    saw — a floating IP behind a NAT anchor, a cloud whose probe does not pass — is not a fault)."""
    now_ = [a["ip"] for a in found if a.get("v") == 4 and is_public4(a["ip"])] + [p["ip"] for p in pairs]
    old = [x for x in (prev or {}).get("seen") or [] if isinstance(x, str) and is_public4(x) and x not in now_]
    out = []
    for ip in old + now_:
        if ip not in out:
            out.append(ip)
    return out[-SEEN_MAX:]


def machine(runner, refresh=False, net=True):
    """The machine now (``discover``): the interfaces and the routes are always looked at again (a few ``ip`` calls — a
    decision is never taken on an address gone since the last look); the NAT probes only with ``net`` and when
    ``refresh`` or the last ones are older than FRESH (never made — too), else the pairs of the cache whose local address
    is still here. Written back to the cache (the menu header reads it, ``cached``)."""
    prev = load_cache()
    now = _now()
    nat_old = prev is None or not prev.get("nat_at") or not 0 <= now - prev["nat_at"] < FRESH
    e6_old = prev is None or not prev.get("echo6_at") or not 0 <= now - prev["echo6_at"] < FRESH
    m = discover(runner, prev, nat=bool(net and (refresh or nat_old)), echo6=bool(net and (refresh or e6_old)))
    save_cache(m)
    return m


# ── the document ───────────────────────────────────────────────────────────

def manual_list(st):
    """``gateway.addresses.manual`` of the state, the valid entries only: [{"ip", "local"?}]."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    lst = a.get("manual") if isinstance(a, dict) else None
    out = []
    for e in lst if isinstance(lst, list) else []:
        if not isinstance(e, dict) or not is_public4(e.get("ip")):
            continue
        rec = {"ip": str(_v4(e["ip"]))}
        if _v4(e.get("local")) is not None:
            rec["local"] = str(_v4(e["local"]))
        if rec["ip"] not in [x["ip"] for x in out]:
            out.append(rec)
    return out[:MANUAL_MAX]


def _client(st):
    g = (st or {}).get("gateway")
    return str(g.get("host") or "") if isinstance(g, dict) else ""


def _host_auto(st):
    g = (st or {}).get("gateway")
    return bool(g.get("host_auto", True)) if isinstance(g, dict) else True


def _dns_a(st):
    """The A records of the site's domain at its last check, None without a domain or a check."""
    s = (st or {}).get("site")
    if not isinstance(s, dict) or not s.get("domain") or not isinstance(s.get("dns"), dict):
        return None
    a = s["dns"].get("a")
    return [str(x) for x in a] if isinstance(a, list) else None


def _dns_aaaa(st):
    """The AAAA records of the site's domain at its last check (canonical), None without a domain or a check."""
    s = (st or {}).get("site")
    if not isinstance(s, dict) or not s.get("domain") or not isinstance(s.get("dns"), dict):
        return None
    a = s["dns"].get("aaaa")
    return [x for x in (canon6(y) for y in a) if x] if isinstance(a, list) else None


def _rows6(m, st):
    """``addresses6`` of the document: the cache's (pure) with ``in_dns`` — the address is among the AAAA records of
    the site's domain (None: no domain or not checked)."""
    aaaa = _dns_aaaa(st)
    out = []
    for a in (m or {}).get("addresses6") or []:
        ip = canon6(a.get("ip")) if isinstance(a, dict) else None
        if not ip:
            continue
        lft = a.get("valid_lft")
        out.append({"ip": ip, "iface": str(a.get("iface") or ""), "prefix": a.get("prefix"),
                    "deprecated": a.get("deprecated") is True, "dynamic": a.get("dynamic") is True,
                    "valid_lft": lft if isinstance(lft, int) and not isinstance(lft, bool) and lft >= 0 else None,
                    "route_ok6": a.get("route_ok6") is True, "echo6": canon6(a.get("echo6") or ""),
                    "in_dns": (ip in aaaa) if aaaa is not None else None})
    return out


def _iso(ts):
    if not ts:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))
    except (TypeError, ValueError, OverflowError):
        return None


def compose(m, st):
    """The document of ``m`` (``discover``/the cache, None — nothing known of the machine) and the state ``st`` (pure):

        {"addresses": [{"ip", "local", "iface", "origin", "found", "primary", "client", "route_ok", "route",
                        "in_dns", "secondary"}],
         "primary": ip|"", "client": gateway.host, "client_found": bool|None, "client_seen": bool|None,
         "client_primary": bool|None,
         "show": ip|"", "more": n,
         "private": [{"ip", "iface"}], "v6": [ip], "nat_wanted": bool, "nat_at": iso|None, "checked_at": iso|None,
         "addresses6": [{"ip", "iface", "prefix", "deprecated", "dynamic", "valid_lft", "route_ok6", "echo6",
                         "in_dns"}], "ipv6_off": bool|None}

    ``show`` — the address to print as «the server's IP» (the menu header, the bot): the address for clients when it is
    one of the server's, else the primary one, else the address for clients anyway; ``more`` — how many other public
    addresses the server has. ``client_found`` — False: the address for clients is an IPv4 the server does not have;
    ``client_seen`` — it was found on the machine once (the cache's ``seen``): only then it is «gone».
    ``client_primary`` — False: the address for clients was determined by itself (``host_auto``) and the server has it,
    but goes online from another one (the hoster made a new address primary, T-mip-lab Н6); None — nothing to say: no
    address for clients, it is not here, the primary one is unknown, or another address was chosen by hand (a choice
    is not a fault, REV З-1).
    ``secondary`` (3.3.7.15, «eth0 · второй адрес») — the local address is a secondary one of its interface (``ip addr``
    says ``secondary``): None — not on an interface or a cache without the mark. ``ipv6_off`` — IPv6 is turned off in
    the kernel (``ipv6_off``; None — not read, a cache before 3.3.7.15)."""
    m = m if isinstance(m, dict) else {}
    found = [a for a in m.get("addrs") or [] if isinstance(a, dict) and a.get("ip")]
    main = m.get("route") if isinstance(m.get("route"), dict) else None
    ways = m.get("ways") if isinstance(m.get("ways"), dict) else {}
    iface_of = dict((a["ip"], a.get("iface")) for a in found)
    sec_of = dict((a["ip"], a["secondary"]) for a in found if isinstance(a.get("secondary"), bool))
    client = _client(st)
    dns = _dns_a(st)
    items, seen = [], set()

    def add(ip, local, origin, is_found):
        if ip in seen:
            return
        seen.add(ip)
        way = ways.get(local) if local else None
        items.append({"ip": ip, "local": local, "iface": iface_of.get(local) if local else None, "origin": origin,
                      "found": is_found, "primary": False, "client": ip == client,
                      "route_ok": same_way(way, main) if local in ways else None,
                      "route": route_text(way) if way else "",
                      "in_dns": (ip in dns) if dns is not None else None,
                      "secondary": sec_of.get(local) if local else None})

    for a in found:
        if a.get("v") == 4 and is_public4(a["ip"]):
            add(a["ip"], a["ip"], "iface", True)
    pairs = [p for p in m.get("nat") or [] if isinstance(p, dict) and p.get("local") in iface_of]
    for p in pairs:
        add(p["ip"], p["local"], "nat", True)
    for e in manual_list(st):
        local = e.get("local")
        add(e["ip"], local, "manual", (local in iface_of) if local else None)
    if is_public4(client) and client not in seen:
        add(client, None, "host", False)
    src = (main or {}).get("src") or ""
    prim = src if src in seen else dict((p["local"], p["ip"]) for p in pairs).get(src, "")
    for it in items:
        it["primary"] = it["ip"] == prim
    items.sort(key=lambda it: not it["primary"])        # stable: the primary first, the rest as found
    mine = [it for it in items if it["origin"] != "host"]
    paired = set(p["local"] for p in pairs)
    private = [{"ip": a["ip"], "iface": a.get("iface")} for a in found
               if a.get("v") == 4 and _nat_local(a["ip"]) and a["ip"] not in paired]
    v6 = [a["ip"] for a in found if a.get("v") == 6 and ipaddress.ip_address(a["ip"]).is_global]
    if is_public4(client):
        client_found = any(it["ip"] == client for it in mine)
        client_seen = client_found or client in (m.get("seen") or [])
    else:
        client_found = client_seen = None
    if client_found and prim:
        client_primary = True if client == prim else (False if _host_auto(st) else None)
    else:
        client_primary = None
    if client_found:
        show = client
    elif prim:
        show = prim
    elif is_public4(client):
        show = client
    else:
        show = mine[0]["ip"] if mine else ""
    return {"addresses": items, "primary": prim, "client": client, "client_found": client_found,
            "client_seen": client_seen, "client_primary": client_primary, "show": show,
            "more": max(0, len(mine) - 1) if any(it["ip"] == show for it in mine) else len(mine),
            "private": private, "v6": v6, "nat_wanted": bool(m.get("nat_wanted")), "nat_at": _iso(m.get("nat_at")),
            "checked_at": _iso(m.get("at")), "addresses6": _rows6(m, st),
            "ipv6_off": m["ipv6_off"] if isinstance(m.get("ipv6_off"), bool) else None}


def collect(runner, st=None, refresh=False, net=True):
    """The document now (``machine`` + the state; ``st`` None — state.json)."""
    st = state.load() if st is None else st
    return compose(machine(runner, refresh=refresh, net=net), st)


def cached(st):
    """The document from the files only (the cache and ``st``), None without the cache — the menu header
    (``kaskadctl summary`` runs nothing)."""
    m = load_cache()
    return compose(m, st) if m is not None else None


def known(doc, ip):
    """``ip`` is an address of this server: found on it (an interface, a NAT pair) or added by hand."""
    return any(it["ip"] == ip and it["origin"] != "host" for it in (doc or {}).get("addresses") or [])


def known_list(doc):
    return [it["ip"] for it in (doc or {}).get("addresses") or [] if it["origin"] != "host"]


def check_known(runner, ip, st=None):
    """A refusal (ConflictError ``address_not_here``) unless ``ip`` is an address of this server — looked at without
    the network first, then once more with the NAT probes (a cloud whose pair was not looked for yet)."""
    st = state.load() if st is None else st
    doc = collect(runner, st, net=False)
    if known(doc, ip):
        return doc
    doc = collect(runner, st, refresh=True)
    if known(doc, ip):
        return doc
    have = ", ".join(known_list(doc)) or tr("gw_addresses.none")
    raise ConflictError(tr("gw_addresses.err.not_here", ip=ip, known=have),
                        data={"reason": "address_not_here", "ip": ip, "known": known_list(doc)})


def listen_extra(st, found):
    """The addresses added by hand that the panel listens on too (PLAN §2.2 С2, REV З4): the ``local`` of a manual
    entry (else its ``ip``) when it is in ``found`` (the local IPv4 of the machine, not of the gateway's own or a
    container network). Never an address the panel was not given by hand."""
    out = []
    for e in manual_list(st):
        a = e.get("local") or e["ip"]
        if a in found and a not in out:
            out.append(a)
    return out


# ── IPv6: «Приём», the prefix, the page (3.3.7.15 F10–F11) ────────────────

IN6_KEY = "*"


def in6_list(st):
    """``gateway.addresses.in6["*"]`` — the IPv6 of this server that take connections: None — every IPv6 (as before,
    no key); [] — none (IPv4 stays: closing IPv6 whole is a choice, PLAN-multi-ip-roles §9.2); else the list (canonical,
    global). Read softly (SPEC-R1 §0а): no key, another type or a list of only unknown entries — None (as 3.3.7.14)."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    v = a.get("in6") if isinstance(a, dict) else None
    lst = v.get(IN6_KEY) if isinstance(v, dict) else None
    if not isinstance(lst, list):
        return None
    out = []
    for x in lst:
        ip = canon6(x) if isinstance(x, str) else None
        if ip and is_public6(ip) and ip not in out:
            out.append(ip)
    if lst and not out:
        return None
    return out[:V6_MAX]


def give6_of(st):
    """F13 (SPEC-R1 §1 решение 6, PLAN-multi-ip-roles §6.1): the IPv6 written to be given as an extra line of the
    subscription — the first IPv6 element of ``gateway.addresses.give["items"]`` (3.3.7.15 writes one at most), None —
    none. Read softly (SPEC-R1 §0а): IPv4 elements, ``mode`` and every other key of a later version are skipped, a
    broken record is «none». The written intention only — never the machine (VLESS Reality ``listen``, С-5)."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    give = a.get("give") if isinstance(a, dict) else None
    items = give.get("items") if isinstance(give, dict) else None
    for it in items if isinstance(items, list) else []:
        ip = canon6(it.get("ip")) if isinstance(it, dict) and isinstance(it.get("ip"), str) else None
        if ip and is_public6(ip):
            return ip
    return None


def route6_src(m):
    """The source of the default IPv6 route in the cache ``m`` (the address an answer from an unbound socket leaves
    from: Hysteria2 with the mask, AmneziaWG «программа» — T-mip-lab Н2, Н3), None — not known."""
    r = (m or {}).get("route6") if isinstance(m, dict) else None
    return canon6(r.get("src") or "") if isinstance(r, dict) else None


def give6_state(st, m=None):
    """F13, the effective give (PLAN-multi-ip-roles §6.2 — at every build of the subscription, not only at the write):
    {"ip": the written IPv6 | None, "on": it goes into the subscription now, "why": None | "off" (not taken by
    ``in6``) | "gone" (not on the machine) | "deprecated" | "no_route6" | "echo_other" (seen outside as another one)}.
    ``m`` — the cache (None: read it). An echo with no answer does not stop it (said at the write)."""
    ip = give6_of(st)
    if ip is None:
        return {"ip": None, "on": False, "why": None}
    acc = in6_list(st)
    if acc is not None and ip not in acc:
        return {"ip": ip, "on": False, "why": "off"}
    m = load_cache() if m is None else m
    row = next((a for a in (m or {}).get("addresses6") or [] if isinstance(a, dict) and canon6(a.get("ip")) == ip),
               None)
    why = None
    if row is None:
        why = "gone"
    elif row.get("deprecated") is True:
        why = "deprecated"
    elif row.get("route_ok6") is not True:
        why = "no_route6"
    elif canon6(row.get("echo6") or "") not in (None, ip):
        why = "echo_other"
    return {"ip": ip, "on": why is None, "why": why}


def give6_effective(st, m=None):
    """[IPv6] the subscription gives an extra line of now (none or one, ``give6_state``)."""
    s = give6_state(st, m)
    return [s["ip"]] if s["on"] else []


def _iid(ip):
    return int(_v6(ip)) & ((1 << 64) - 1)


def prefix_changes(prev6, now6):
    """«Префикс IPv6 сменился» (PLAN-multi-ip-roles §9.4): an IPv6 of ``prev6`` (``addresses6`` of the cache before,
    not deprecated then) is deprecated or gone in ``now6``, its /64 has no live address on that interface any more, and
    a live global IPv6 of another /64 is on the same interface -> [{"iface", "old": /64, "new": /64, "suggest": ip}]
    (``suggest`` — the new address with the same interface identifier first, a new one before an old one). A role is
    never moved by itself (§6.5 п. 6): only said. Only deprecated without a new one — nothing."""
    def rows(lst):
        out = {}
        for a in lst or []:
            ip = canon6(a.get("ip")) if isinstance(a, dict) else None
            if ip:
                out[ip] = {"iface": str(a.get("iface") or ""), "dep": a.get("deprecated") is True}
        return out
    prev, now = rows(prev6), rows(now6)
    out, seen = [], set()
    for ip in sorted(prev, key=_v6):
        p = prev[ip]
        if p["dep"] or (ip in now and not now[ip]["dep"]):
            continue
        old = net64(ip)
        live = [x for x in now if now[x]["iface"] == p["iface"] and not now[x]["dep"]]
        if any(net64(x) == old for x in live):
            continue                                    # the prefix lives on another address of the interface
        cands = [x for x in live if net64(x) != old]
        if not cands:
            continue
        cands.sort(key=lambda x: (x in prev, _iid(x) != _iid(ip), _v6(x)))
        new = net64(cands[0])
        key = (p["iface"], old, new)
        if key not in seen:
            seen.add(key)
            out.append({"iface": p["iface"], "old": old, "new": new, "suggest": cands[0]})
    return out


# the codes of the states of a row of the page «Адреса сервера» (the words are the panel's: adr.* of
# panel/static/pages/addresses.js, RU/EN; П-1 REV-3315-fin — the Python catalog keeps none). F10 fills deprecated, dynamic, no_route6, echo_other, gone; F11 — accept,
# off, lock_panel, lock_host, lock_aaaa; F12 — route_src, direct; F13 — accept_give; reserve — a later release (Р3)
STATE_CODES = ("accept", "accept_give", "reserve", "off", "deprecated", "dynamic", "no_route6", "echo_other", "gone",
               "lock_panel", "lock_host", "lock_aaaa", "route_src", "direct")


def _hours_left(lft, at, now):
    """The hours an IPv6 lives on (``valid_lft`` at the look ``at``), rounded up; None — forever or unknown."""
    if not isinstance(lft, int) or isinstance(lft, bool):
        return None
    try:
        left = lft - max(0, int(now) - int(at or now))
    except (TypeError, ValueError):
        return None
    return max(0, -(-left // 3600))


def page(st, doc, locks=None, now=None):
    """The model of the page «Адреса сервера» (pure): {"v4": [row], "v6": [row], "codes": STATE_CODES}; a row —
    {"ip", "iface", "codes": [code], "hours"?: n} (codes, never words — the panel says them). ``doc`` — ``compose``;
    ``locks`` — ``mip.locks(st)`` (None: asked here). An IPv6 of ``in6`` the machine has not — a row with «gone»."""
    from . import mip
    locks = mip.locks(st) if locks is None else locks
    now = int(_now()) if now is None else now
    at = None
    if doc and doc.get("checked_at"):
        try:
            at = calendar.timegm(time.strptime(doc["checked_at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (TypeError, ValueError, OverflowError):
            at = None
    want4, want6 = mip.in_list(st), in6_list(st)
    give6 = give6_of(st)
    outs, direct = mip.out_list(st), mip.direct_of(st)
    # F12а: «напрямую» of the choice acts (as mip.direct_plan, by the rows of the document) — else the sites see the
    # address of the route
    acts = any(it["ip"] == direct and it.get("found") is True and it.get("route_ok") is True and
               (outs is None or direct in outs) for it in (doc or {}).get("addresses") or [])
    v4 = []
    for it in (doc or {}).get("addresses") or []:
        codes = ["accept" if want4 is None or it["ip"] in want4 else "off"]
        if it.get("origin") == "host" or it.get("found") is False:
            codes.append("gone")
        if it["ip"] in locks.get("panel4", ()):
            codes.append("lock_panel")
        if it.get("client") or it["ip"] in locks.get("host4", ()):
            codes.append("lock_host")
        if it.get("primary") and not acts:
            codes.append("route_src")                   # F12а: «сайты видят этот адрес» (no «напрямую» acting)
        if it["ip"] == direct:
            codes.append("direct")
        v4.append({"ip": it["ip"], "iface": it.get("iface") or "", "codes": codes,
                   "out": outs is None or it["ip"] in outs})       # F12а: «Для выхода: да / нет»
    v6, here6 = [], set()
    for a in (doc or {}).get("addresses6") or []:
        here6.add(a["ip"])
        # IPv6 (SPEC F11, PLAN-multi-ip-roles §9.2): «только принимать» (``reserve``) — the default; «принимать и
        # выдавать» (``accept_give``) — F13, the IPv6 of ``give``; «не принимать» — ``off``. ``accept`` is of IPv4 only
        # (решение 7)
        codes = ["off" if want6 is not None and a["ip"] not in want6 else "accept_give" if a["ip"] == give6 else
                 "reserve"]
        row = {"ip": a["ip"], "iface": a.get("iface") or "", "codes": codes}
        if a.get("deprecated"):
            codes.append("deprecated")
        if a.get("dynamic") and a.get("valid_lft") is not None:
            codes.append("dynamic")
        if a.get("deprecated") or a.get("dynamic"):
            h = _hours_left(a.get("valid_lft"), at, now)
            if h is not None:
                row["hours"] = h
        if not a.get("route_ok6"):
            codes.append("no_route6")
        if a.get("echo6") and a["echo6"] != a["ip"]:
            codes.append("echo_other")
            row["seen_as"] = a["echo6"]
        if a["ip"] in locks.get("panel6", ()):
            codes.append("lock_panel")
        if a["ip"] in locks.get("aaaa", ()):
            codes.append("lock_aaaa")
        v6.append(row)
    for ip in want6 or []:
        if ip not in here6:
            here6.add(ip)
            v6.append({"ip": ip, "iface": "", "codes": ["accept_give" if ip == give6 else "reserve", "gone"]})
    if give6 and give6 not in here6 and want6 is None:
        v6.append({"ip": give6, "iface": "", "codes": ["accept_give", "gone"]})     # F13: given, gone from the machine
    return {"v4": v4, "v6": v6, "codes": list(STATE_CODES)}


# ── the manual list ────────────────────────────────────────────────────────

def _parse_public(value):
    a = _v4(str(value or "").strip())
    if a is None or not a.is_global:
        raise KaskadError(tr("gw_addresses.err.not_public", value=str(value or "")[:64]))
    return str(a)


def add(runner, ip, local=None):
    """Add ``ip`` (a public IPv4) to the manual list, with ``local`` — the address of the machine it is mapped to (NAT;
    it must be on the machine). An address the machine has already on the interface of the default route: nothing to
    add (``added`` False). One on another interface (or the pair of such an address) is written with its ``local``:
    the open mode of the panel listens there only when chosen by hand (PLAN §2.2 С2, REV З-3)."""
    ip = _parse_public(ip)
    if local is not None and str(local).strip():
        la = _v4(str(local).strip())
        if la is None:
            raise KaskadError(tr("gw_addresses.err.local_bad", value=str(local)[:64]))
        local = str(la)
    else:
        local = None
    doc = collect(runner, net=False)
    cache = load_cache() or {}
    here = set(a.get("ip") for a in cache.get("addrs") or [] if isinstance(a, dict))
    if local and local not in here:
        raise ConflictError(tr("gw_addresses.err.local_not_here", local=local,
                               known=", ".join(sorted(here, key=lambda x: (":" in x, x))) or tr("gw_addresses.none")))
    have = [it for it in doc["addresses"] if it["ip"] == ip and it["origin"] in ("iface", "nat")]
    dev = cache["route"].get("dev") if isinstance(cache.get("route"), dict) else None
    other = None
    if have:
        if not dev or have[0].get("iface") == dev:
            return {"ok": True, "added": False, "ip": ip, "text": tr("gw_addresses.already", ip=ip)}
        other = have[0]
        local = other.get("local") or ip
    with state.locked():
        st = state.load()
        g = st.get("gateway")
        if not isinstance(g, dict):
            g = st["gateway"] = {}
        a = g.get("addresses")
        if not isinstance(a, dict):
            a = g["addresses"] = {}
        lst = [e for e in manual_list(st) if e["ip"] != ip]
        if len(lst) >= MANUAL_MAX:
            raise KaskadError(tr("gw_addresses.err.too_many", n=MANUAL_MAX))
        lst.append(dict({"ip": ip}, **({"local": local} if local else {})))
        a["manual"] = lst
        state.save(st)
    util.log_tr("gw_addresses.log.added", ip=ip, local=local or "—")
    if other is not None:
        text = tr("gw_addresses.added_listen", ip=ip, iface=other.get("iface") or "?")
    elif local:
        text = tr("gw_addresses.added_local", ip=ip, local=local)
    else:
        text = tr("gw_addresses.added", ip=ip)
    return {"ok": True, "added": True, "ip": ip, "local": local, "text": text}


def remove(ip):
    """Take ``ip`` off the manual list (what the machine finds itself stays found). Not the address for clients the
    server knows from this list only — it would turn foreign under the devices (REV П-1): another one is chosen
    first."""
    ip_s = str(ip or "").strip()
    with state.locked():
        st = state.load()
        lst = manual_list(st)
        if ip_s not in [e["ip"] for e in lst]:
            raise KaskadError(tr("gw_addresses.err.not_manual", ip=ip_s[:64],
                                 manual=", ".join(e["ip"] for e in lst) or tr("gw_addresses.none")))
        if ip_s == _client(st) and not _found_itself(ip_s):
            raise ConflictError(tr("gw_addresses.err.remove_client", ip=ip_s))
        st["gateway"]["addresses"]["manual"] = [e for e in lst if e["ip"] != ip_s]
        state.save(st)
    util.log_tr("gw_addresses.log.removed", ip=ip_s)
    return {"ok": True, "removed": True, "ip": ip_s, "text": tr("gw_addresses.removed", ip=ip_s)}


def _found_itself(ip):
    """``ip`` is found by the machine itself at the last look (the cache): on an interface or a NAT pair."""
    m = load_cache() or {}
    return any(isinstance(a, dict) and a.get("ip") == ip for a in m.get("addrs") or []) or \
        any(isinstance(p, dict) and p.get("ip") == ip for p in m.get("nat") or [])


# ── words ──────────────────────────────────────────────────────────────────

def item_text(it):
    """One line of an address: «203.0.113.7 — интерфейс eth0 · основной · адрес для клиентов · A-запись ✓»."""
    o = it["origin"]
    if o == "iface":
        where = tr("gw_addresses.where.iface", iface=it.get("iface") or "?")
    elif o == "nat":
        where = tr("gw_addresses.where.nat", local=it.get("local") or "?", iface=it.get("iface") or "?")
    elif o == "manual":
        where = tr("gw_addresses.where.manual_local", local=it["local"]) if it.get("local") else \
            tr("gw_addresses.where.manual")
        if it.get("found") is False:
            where += " — " + tr("gw_addresses.flag.local_gone")
    else:
        where = tr("gw_addresses.where.host_gone")
    flags = []
    if it.get("primary"):
        flags.append(tr("gw_addresses.flag.primary"))
    if it.get("client"):
        flags.append(tr("gw_addresses.flag.client"))
    if it.get("in_dns") is True:
        flags.append(tr("gw_addresses.flag.dns_yes"))
    elif it.get("in_dns") is False:
        flags.append(tr("gw_addresses.flag.dns_no"))
    if it.get("route_ok") is False:
        flags.append(tr("gw_addresses.flag.route_other", route=it.get("route") or "?"))
    return "%s — %s" % (it["ip"], " · ".join([where] + flags))


def lines(doc):
    out = [tr("gw_addresses.title")]
    if not doc["addresses"]:
        out.append("  " + tr("gw_addresses.empty"))
    out += ["  " + item_text(it) for it in doc["addresses"]]
    if doc.get("private"):
        out.append(tr("gw_addresses.private", list=", ".join("%s (%s)" % (p["ip"], p.get("iface") or "?")
                                                            for p in doc["private"])))
    if doc.get("nat_wanted") and not doc.get("nat_at"):
        out.append(tr("gw_addresses.nat_unchecked"))
    if doc.get("v6"):
        out.append(tr("gw_addresses.v6", list=", ".join(doc["v6"])))
    elif doc.get("ipv6_off") is True and not doc.get("addresses6"):
        out.append(tr("gw_addresses.v6_off"))           # 3.3.7.15 С3: why the server has no IPv6
    if doc.get("client_found") is False:
        out.append(tr("gw_addresses.client_gone", ip=doc["client"]))
    if doc.get("client_primary") is False:
        out.append(tr("gw_addresses.client_not_primary", client=doc["client"], primary=doc["primary"]))
    return out


def gone_problem(doc, st):
    """The address for clients is an IPv4 this server does not have (PLAN §5 вопрос 4, решение координатора 29.10:
    a warning and the way to change it — never a move by itself): the text, or None. Only while the gateway is on or
    has devices — the address is in somebody's settings then. An address the server never saw on itself (``client_seen``
    False: an external address of a cloud the probe did not find, a floating IP) is no problem — the line of
    ``gw addresses`` says how to add it (REV-mip-s12 З-2)."""
    if not doc or doc.get("client_found") is not False or not doc.get("client_seen"):
        return None
    g = (st or {}).get("gateway")
    g = g if isinstance(g, dict) else {}
    if not g.get("enabled") and not g.get("devices"):
        return None
    return tr("gw_addresses.problem.client_gone", ip=doc["client"], known=", ".join(known_list(doc))
              or tr("gw_addresses.none"))
