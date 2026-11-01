"""Несколько IP: приём на выбранных адресах (PLAN-multi-ip Ш3) и IP выхода (Ш4) — k3/handoff/briefs/
PLAN-multi-ip-2026-10-29.md §3, §4 Ш3–Ш4, §4.1–4.2; живые факты — k3/stand/logs/T-mip-lab.md (Н1–Н6).

The data (``state.gateway``):

    addresses.in      None — every address, as before; {"*": [ip, …]} — the inbounds and the site take connections only
                      on these (public IPv4 of «Адреса сервера»; the key "*" leaves room for a list per inbound later)
    exits[id].src     None — the exit is reached from the address of the system's route; an IP of «Адреса сервера» —
                      from that one (xray exits: ``sendThrough``; AmneziaWG legs: SNAT of the leg in gw_post)
    addresses.in6     3.3.7.15 F11: None — every IPv6 takes connections, as before; {"*": [IPv6, …]} — only these
                      (global IPv6 of this machine, canonical); {"*": []} — none (IPv4 stays). A key of its own, not
                      inside ``in``: 3.3.7.13/14 write ``in`` whole and would wipe IPv6 (PLAN-multi-ip-roles §9.7)
    addresses.give    3.3.7.15 F13: None — no IPv6 line in the subscription, as before; {"items": [{"ip": IPv6, "n"}]}
                      — that IPv6 goes as an extra line (``set_give6``; read by ``addresses.give6_of``/``give6_state``;
                      VLESS Reality listens on IPv6 by it, С-5)
    addresses.out     3.3.7.15 F12а: None — every IPv4 is «для выхода», no choice; {"*": [IPv4, …]} — only these may be
                      the address of «напрямую» and of the exits (``set_out``; ``set_src``/``set_direct`` take only them)
    addresses.direct  3.3.7.15 F12а: None — «напрямую» from the address of the route (as 3.3.7.14); an IPv4 — the traffic
                      «напрямую» of every inbound leaves from it (``plan`` «snat» → SNAT in gw_post + ``sendThrough`` of
                      «direct»; drawn only while it acts: on the machine, «для выхода», ``route_ok`` — §6.2)

What the machine has is read from the cache of «Адреса сервера» (gw/addresses.py, ``/var/lib/kaskad/gw/
addresses.json``) — never a command here: the nft part and xray are rendered from the state and that file, the collector
keeps the file fresh (``watch``) and applies again when the plan of the addresses changes. No file (a test, a node that
never looked) — nothing of this is drawn: the table and xray are those of 3.3.7.12 byte for byte.

    plan(st, pool_exits)   {"in": in_plan | None, "snat": None (Н4: on a choice only), "legs": [(E, P, local, id)],
                            "xsrc": {id: local}, "gone_src": {id: ip}}       (net.plan → p["mip"], xray render)
    in_rule(mp)            the rule of gw_pre (right after ``iifname "lo" return``) or None
    post_rules(mp)         the rules of gw_post before masquerade: SNAT of the legs, then Н4
    pre6_rules(st)         the rule of gw_pre6 of ``ip6 kaskad6`` (F11) or [] — from the state only, the same text for
                           the boot file, an apply, the watchdog's repair (gw/net.py, С-1)

What is never touched (§4.2): the addresses, routes and ip rules of the machine, the SSH port, the forwards, port 80,
foreign ports; SNAT only for ``ip saddr @gw_nets`` (Н4) and for the legs by their Endpoint.
"""

import hashlib
import ipaddress
import json
import os
import re

from .. import paths, state, util
from ..errors import ConflictError, KaskadError, USAGE
from ..i18n import tr
from . import addresses, model

IN_KEY = "*"
NEVER = (("tcp", 80),)                  # Let's Encrypt checks the domain there — never closed on any address
SSH_DEFAULT = 22
SSHD_FILES = ("/etc/ssh/sshd_config",)
SSHD_DIR = "/etc/ssh/sshd_config.d"
# REV-mip-s34 П-3: sshd started by systemd's socket (Ubuntu 22.10+) listens where ListenStream says, not Port
SSH_SOCKETS = ("/usr/lib/systemd/system/ssh.socket", "/lib/systemd/system/ssh.socket", "/etc/systemd/system/ssh.socket")
SSH_SOCKET_DIR = "/etc/systemd/system/ssh.socket.d"
SITE_PORT = 443
BACK = "kaskadctl gw addresses in --all"     # the way back, printed by every refusal and the menu (§3 «Защита доступа» п. 4)
LEG_CONF = ".kawg%d.conf"               # install.sh: $AWG_DIR/.kawgN.conf (awg setconf; Endpoint = the checked IP)
MARKERS = ("fib daddr type local meta l4proto . th dport", "snat to ")      # in kaskad.nft: rules of this module


# ── the state ──────────────────────────────────────────────────────────────

def _ip4(x):
    try:
        return str(ipaddress.IPv4Address(str(x).strip()))
    except (ValueError, TypeError):
        return None


def _ipkey(ip):
    try:
        return (0, int(ipaddress.IPv4Address(ip)))
    except ValueError:
        return (1, 0)


def _ip6(x):
    """A canonical IPv6 (``addresses.canon6``) or None."""
    return addresses.canon6(x) if isinstance(x, str) else None


def _ip6key(ip):
    a = addresses._v6(ip)
    return int(a) if a is not None else 0


def in_list(st):
    """``gateway.addresses.in["*"]`` — the public IPv4 the inbounds take connections on, None — every address. An empty
    or broken list is «every address»: the devices are never left without an inbound because of a record."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    v = a.get("in") if isinstance(a, dict) else None
    lst = v.get(IN_KEY) if isinstance(v, dict) else None
    out = []
    for x in lst if isinstance(lst, list) else []:
        ip = _ip4(x)
        if ip and addresses.is_public4(ip) and ip not in out:
            out.append(ip)
    return out[:addresses.MANUAL_MAX] or None


def out_list(st):
    """F12а: ``gateway.addresses.out["*"]`` — the public IPv4 «для выхода» (the ones «напрямую» and the exits may be
    reached from), None — no choice: every address may, and the exits and «напрямую» go from the address of the route
    (as 3.3.7.14). Read softly: an empty or broken list — None."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    v = a.get("out") if isinstance(a, dict) else None
    lst = v.get(IN_KEY) if isinstance(v, dict) else None
    out = []
    for x in lst if isinstance(lst, list) else []:
        ip = _ip4(x) if isinstance(x, str) else None
        if ip and addresses.is_public4(ip) and ip not in out:
            out.append(ip)
    return out[:addresses.MANUAL_MAX] or None


def direct_of(st):
    """F12а: ``gateway.addresses.direct`` — the public IPv4 the traffic «напрямую» of every inbound leaves from (SNAT of
    the kernel's inbounds + ``sendThrough`` of xray's «direct», Н4 aligned), None — no choice (masquerade and xray as
    3.3.7.14). Read softly: «mix» of a later version or a broken value — None."""
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    ip = _ip4(a.get("direct")) if isinstance(a, dict) and isinstance(a.get("direct"), str) else None
    return ip if ip and addresses.is_public4(ip) else None


def exit_src(st, eid):
    """``gateway.exits[eid].src`` — the public IPv4 the exit is reached from, None — by the system's route."""
    rec = (model.peek(st or {}).get("exits") or {}).get(eid)
    ip = _ip4(rec.get("src")) if isinstance(rec, dict) and rec.get("src") else None
    return ip if ip and addresses.is_public4(ip) else None


def src_map(st):
    return dict((eid, exit_src(st, eid)) for eid in sorted(model.peek(st or {}).get("exits") or {})
                if exit_src(st, eid))


def addresses_ver(st):
    """``ver`` of «Адреса сервера» for the writes of the panel (SPEC-R1 §3 «Общее», З-3: ``panel/api.py _need_ver``):
    ``gateway.addresses`` as it is in the state with the addresses the exits are reached from (``exits[].src`` — the
    page shows and writes them too, ``PUT …/exit``). 12 hex (``policy_ops.ver_of``)."""
    from .policy_ops import ver_of
    g = (st or {}).get("gateway")
    a = g.get("addresses") if isinstance(g, dict) else None
    return ver_of({"addresses": a if isinstance(a, dict) else None, "src": src_map(st)})


def check_ver(st, want_ver):
    """Under the lock of the write: another ``ver`` than ``want_ver`` — 409 ``object_changed`` (None — no check: the
    CLI, MCP, the menu)."""
    if want_ver is not None:
        from .policy_ops import check_ver as _check
        _check(addresses_ver(st), want_ver)


# ── the machine (the cache of «Адреса сервера») ────────────────────────────

def view(m):
    """What the plan needs of the cache ``m`` (addresses.discover): {"here": [local IPv4], "pub": {public ip: local}
    (an address of an interface — itself; a NAT pair — its private one), "dev", "src" (of the default route), "on_dev":
    [IPv4 of that device], "ways": {local: route}, "route": the default route}; None — nothing known."""
    if not isinstance(m, dict) or not isinstance(m.get("addrs"), list):
        return None
    found = [a for a in m["addrs"] if isinstance(a, dict) and a.get("v") == 4 and _ip4(a.get("ip"))]
    here = [a["ip"] for a in found]
    pub = {}
    for a in found:
        if addresses.is_public4(a["ip"]):
            pub[a["ip"]] = a["ip"]
    for p in m.get("nat") or []:
        if isinstance(p, dict) and p.get("local") in here and addresses.is_public4(p.get("ip")):
            pub.setdefault(p["ip"], p["local"])
    route = m.get("route") if isinstance(m.get("route"), dict) else {}
    dev = str(route.get("dev") or "")
    on_dev = [a for a in found if a.get("iface") == dev]
    return {"here": here, "pub": pub, "dev": dev, "src": _ip4(route.get("src")) or "", "route": route,
            "via": _ip4(route.get("via")) or "", "on_dev": [a["ip"] for a in on_dev],
            "dev_addrs": [{"ip": a["ip"], "prefix": a.get("prefix"), "secondary": bool(a.get("secondary"))}
                          for a in on_dev],
            "ways": m.get("ways") if isinstance(m.get("ways"), dict) else {}}


def machine():
    """The view of the cache as it is on disk (no command)."""
    return view(addresses.load_cache())


def local_of(st, v, ip):
    """The address of the machine packets to ``ip`` arrive at (and an answer from ``ip`` leaves from): ``ip`` itself on
    an interface, the private one of a NAT pair, the ``local`` of an address added by hand when it is on the machine;
    None — not on the machine now (gone, or added by hand without a local address)."""
    if v is None or not ip:
        return None
    if ip in v["pub"]:
        return v["pub"][ip]
    for e in addresses.manual_list(st):
        if e["ip"] == ip and e.get("local") in v["here"]:
            return e["local"]
    return None


def public_of(st, v, local):
    """The public address of a local one (the inverse of ``local_of``), else the local one itself."""
    for ip, loc in (v or {}).get("pub", {}).items():
        if loc == local:
            return ip
    for e in addresses.manual_list(st):
        if e.get("local") == local:
            return e["ip"]
    return local


def route_ok(v, local):
    """An answer from ``local`` leaves the way of the default route (the same device and gateway): SNAT to it is safe.
    None — not known (the cache has no answer for it)."""
    way = (v or {}).get("ways", {}).get(local)
    return addresses.same_way(way, (v or {}).get("route")) if way else None


# ── Ш3: the inbounds take connections on the chosen addresses ──────────────

def sshd_ports():
    """The ports of sshd from its config (Port lines), 22 by default — they are never in the set (§4.2)."""
    files = [paths.P(f) for f in SSHD_FILES]
    try:
        files += [os.path.join(paths.P(SSHD_DIR), n) for n in sorted(os.listdir(paths.P(SSHD_DIR)))
                  if n.endswith(".conf")]
    except OSError:
        pass
    out = set()
    for f in files:
        for ln in (util.read_text(f) or "").splitlines():
            tok = ln.split("#", 1)[0].split()
            if len(tok) >= 2 and tok[0].lower() == "port" and tok[1].isdigit() and 0 < int(tok[1]) < 65536:
                out.add(int(tok[1]))
    out |= socket_ports()
    return out or {SSH_DEFAULT}


def socket_ports():
    """The ports of ``ListenStream=`` of ssh.socket and its drop-ins (``22``, ``0.0.0.0:2222``, ``[::]:2222``) — every
    one of every file: a port more in «never» only leaves it open (REV-mip-s34 П-3)."""
    files = [paths.P(f) for f in SSH_SOCKETS]
    try:
        files += [os.path.join(paths.P(SSH_SOCKET_DIR), n) for n in sorted(os.listdir(paths.P(SSH_SOCKET_DIR)))
                  if n.endswith(".conf")]
    except OSError:
        pass
    out = set()
    for f in files:
        for ln in (util.read_text(f) or "").splitlines():
            m = re.match(r"^\s*ListenStream\s*=\s*(?:\S*:)?(\d{1,5})\s*$", ln)
            if m and 0 < int(m.group(1)) < 65536:
                out.add(int(m.group(1)))
    return out


def never_ports(st, env=None):
    """{(proto, port)} the filter never takes: port 80 (Let's Encrypt), sshd and the port of this SSH session, the
    forwards (they work on every address, PLAN §1.1)."""
    out = set(NEVER)
    out.update(("tcp", p) for p in sshd_ports() | {SSH_DEFAULT})
    sc = ((env if env is not None else os.environ).get("SSH_CONNECTION") or "").split()
    if len(sc) == 4 and sc[3].isdigit():
        out.add(("tcp", int(sc[3])))
    for f in (st or {}).get("forwards") or []:
        try:
            nf = state.normalize_forward(f)
        except (KaskadError, state.StateError):
            continue
        out.add((nf["proto"], nf["port"]))
    return out


def site_on_443(st):
    """Kaskad's own nginx holds 443 (the site is on, or the panel behind nginx on the domain): 443 is closed on the
    other addresses together with the inbounds. «xray in front» — 443 is the port of VLESS, in the set anyway."""
    s = (st or {}).get("site")
    if isinstance(s, dict) and s.get("mode") == "site" and s.get("domain"):
        return True
    try:
        from ..panel import conf
        return bool(conf.load()["proxy"]["on"])
    except Exception:                                   # noqa: BLE001 — no panel: not ours
        return False


def _ranges(pairs):
    """{(proto, port)} -> [(proto, lo, hi)]: neighbouring ports of one protocol as one interval (the hop range of
    Hysteria2 — one element, not a hundred)."""
    out = []
    for proto in ("tcp", "udp"):
        ports = sorted(p for pr, p in pairs if pr == proto)
        for p in ports:
            if out and out[-1][0] == proto and out[-1][2] == p - 1:
                out[-1] = (proto, out[-1][1], p)
            else:
                out.append((proto, p, p))
    return out


def inbound_ports(st, env=None):
    """[(proto, lo, hi)] the filter of the chosen addresses closes: the ports of the installed and enabled inbounds
    (``Inbound.ports``: a hop range as it is), 443 of our own nginx — never what ``never_ports`` names."""
    from . import registry
    pairs = set()
    for kind in model.enabled_kinds(st):
        try:
            ad = registry.get(kind)
            got = list(ad.ports(st)) if ad is not None else []
        except Exception:                               # noqa: BLE001 — an adapter that fails is left out
            continue
        for proto, port in got:
            if proto in ("tcp", "udp") and isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
                pairs.add((proto, port))
    if site_on_443(st):
        pairs.add(("tcp", SITE_PORT))
    return _ranges(pairs - never_ports(st, env))


def in_plan(st, v, env=None):
    """None — every address (no list); else {"want": [public], "addrs": [local present], "gone": [public not here],
    "ports": [(proto, lo, hi)]}. The machine unknown (``v`` None) — nothing present: no rule (as every address)."""
    want = in_list(st)
    if not want:
        return None
    present, gone = [], []
    for ip in want:
        loc = local_of(st, v, ip)
        if loc and loc not in present:
            present.append(loc)
        elif not loc:
            gone.append(ip)
    return {"want": want, "addrs": sorted(present, key=_ipkey), "gone": gone, "ports": inbound_ports(st, env)}


def _port_elem(proto, lo, hi):
    return "%s . %d" % (proto, lo) if lo == hi else "%s . %d-%d" % (proto, lo, hi)


def in_rule(mp):
    """The one rule of gw_pre (PLAN §3): packets of the hoster's network (not of a client interface kgw-*) to a port
    of an inbound at an address of this machine other than the chosen ones are dropped — of the connections the other
    side began (``ct direction original``: the new ones and the established alike). The answers to the server's own
    connections are never touched: an outgoing connection of the server (apt, an exit, DNS) whose local port falls into
    an inbound's range (a hop range of Hysteria2 over the ephemeral ports) gets its answers. None when there is nothing
    to draw: no list, the machine not known, every chosen address gone (all of them take connections then — never no
    inbound at all), no inbound port."""
    p = (mp or {}).get("in")
    if not p or not p.get("addrs") or not p.get("ports"):
        return None
    return ('iifname != "%s" ct direction original fib daddr type local meta l4proto . th dport { %s } '
            'ip daddr != { %s } drop' % ("kgw-*", ", ".join(_port_elem(*x) for x in p["ports"]), ", ".join(p["addrs"])))


def in_locals(st, v=None):
    """The local addresses the list lets in (the panel listens on them only), None — no list or none of it here."""
    v = machine() if v is None else v
    p = in_plan(st, v)
    return set(p["addrs"]) if p and p["addrs"] else None


def pre6_rules(st, env=None):
    """F11: the rule of ``gw_pre6`` (``ip6 kaskad6``, prerouting −150) — [] when there is nothing to draw: ``in6`` None
    (no chain: kaskad6 byte for byte as 3.3.7.14) or no port of an inbound. The same form as Ш3 of IPv4, from the state
    only (no cache: IPv6 has no NAT, an address of the list is the address of the interface): the new connections
    from outside (not the loopback, not a client interface) to a port of an inbound at a local IPv6 other than the
    chosen ones are dropped. ``{"*": []}`` — the rule without ``ip6 daddr != {…}`` (nft takes no empty set): no IPv6
    takes them. A chosen IPv6 gone from the machine stays in the set — it is not local, so it lets nothing in, and
    the others stay closed (PLAN-multi-ip-roles §9.2: a gone one never opens the rest); back — it takes connections
    again without an apply. ICMPv6, SSH, port 80 and the forwards are never in the rule (only tcp/udp ports of
    ``inbound_ports``)."""
    want = addresses.in6_list(st)
    if want is None:
        return []
    ports = inbound_ports(st, env)
    if not ports:
        return []
    head = ('iifname != "lo" iifname != "%s" ct direction original fib daddr type local meta l4proto . th dport { %s }'
            % ("kgw-*", ", ".join(_port_elem(*x) for x in ports)))
    if want:
        return [head + " ip6 daddr != { %s } drop" % ", ".join(sorted(want, key=_ip6key))]
    return [head + " drop"]


# ── Ш4: the address an exit is reached from ────────────────────────────────

def leg_endpoint(idx):
    """(IP, port) of the Endpoint of the leg kawg<idx> from its config (written by install.sh with the IP Kaskad
    checked), None — no config or no IP there."""
    text = util.read_text(os.path.join(paths.egress_dir(), "awg", LEG_CONF % idx)) or ""
    m = re.search(r"(?m)^\s*Endpoint\s*=\s*(\d+\.\d+\.\d+\.\d+):(\d{1,5})\s*$", text)
    if not m or not _ip4(m.group(1)) or not 0 < int(m.group(2)) < 65536:
        return None
    return m.group(1), int(m.group(2))


def _leg_index(e):
    m = re.match(r"^kawg(\d+)$", str((e or {}).get("iface") or ""))
    return int(m.group(1)) if m else None


def masq_source(v):
    """The address masquerade takes on the device of the default route — the kernel's inet_select_addr: the first
    primary IPv4 whose network holds the gateway, else the first primary one; None — unknown."""
    cands = [a for a in (v or {}).get("dev_addrs") or [] if not a.get("secondary")]
    via = (v or {}).get("via")
    if via:
        for a in cands:
            try:
                if ipaddress.IPv4Address(via) in ipaddress.IPv4Network("%s/%s" % (a["ip"], a.get("prefix") or 32),
                                                                       strict=False):
                    return a["ip"]
            except ValueError:
                continue
    return cands[0]["ip"] if cands else None


def snat_plan(v):
    """Н4 (T-mip-lab): the kernel's inbounds went out from the address masquerade takes (the first one of the device),
    the xray ones from the source of the route — two addresses for one client when the route names another ``src`` (the
    administrator's choice, or the hoster's file). -> {"dev", "src"} of that difference (what an explicit SNAT of the
    clients to the source of the route would draw), None — one address for both. REV-mip-s34 Б-1: never drawn without
    the administrator's choice (``plan`` keeps ``snat`` None — there is no command of that choice yet, BACKLOG): the
    difference is only said (``status`` «h4», ``gw addresses``, the SSH menu)."""
    if not v or not v.get("dev") or not v.get("src") or len(v.get("on_dev") or []) < 2:
        return None
    if not re.match(r"^[A-Za-z0-9_.@-]{1,15}$", v["dev"]):
        return None                                     # a name nft would not take as written (never quoted around)
    m = masq_source(v)
    if v["src"] not in v["on_dev"] or m is None or m == v["src"]:
        return None
    return {"dev": v["dev"], "src": v["src"]}


DEV_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,15}$")     # a name nft takes as written (never quoted around)


def direct_plan(st, v):
    """F12а (PLAN-multi-ip-roles §2.4 п. 2, §6.2): «напрямую» of the choice ``direct`` as it acts now — {"ip": the
    choice | None, "snat": {"dev", "src": local} | None, "why": None | "gone" (not on the machine) | "not_out" (not
    «для выхода») | "route" (an answer from it leaves another way than the default route — SNAT would go through the
    wrong gateway)}. No choice — nothing drawn (REV-mip-s34 Б-1: the clients go out as in 3.3.7.12)."""
    ip = direct_of(st)
    if ip is None:
        return {"ip": None, "snat": None, "why": None}
    loc = local_of(st, v, ip)
    outs = out_list(st)
    dev = (v or {}).get("dev") or ""
    if loc is None:
        why = "gone"
    elif outs is not None and ip not in outs:
        why = "not_out"
    elif route_ok(v, loc) is not True or not DEV_NAME.match(dev):
        why = "route"
    else:
        return {"ip": ip, "snat": {"dev": dev, "src": loc}, "why": None}
    return {"ip": ip, "snat": None, "why": why}


def plan(st, pool_exits=None, m=None, env=None):
    """{"in", "snat", "legs": [(E, P, local, id)], "xsrc": {id: local}, "gone_src": {id: ip}[, "gone_direct": ip]}
    — what the nft part and xray draw of the addresses now (``gone_direct`` — only while the choice ``direct`` does
    not act: without a choice the plan is that of 3.3.7.14 key for key). ``m`` — the cache (None: read it); the pool — of the caller
    (None: no exit)."""
    v = view(addresses.load_cache() if m is None else m)
    # REV-mip-s34 Б-1: «snat» (Н4) only of the administrator's choice ``direct`` (F12а) — else the clients go out as in
    # 3.3.7.12; the choice not acting now (gone, not «для выхода», another way) — no SNAT, no sendThrough, said (tell)
    dp = direct_plan(st, v)
    out = {"in": in_plan(st, v, env), "snat": dp["snat"], "legs": [], "xsrc": {}, "gone_src": {}}
    if dp["why"]:
        out["gone_direct"] = dp["ip"]
    for e in pool_exits or []:
        ip = exit_src(st, e.get("id"))
        if not ip or e.get("kind") not in ("awg", "xray"):
            continue
        loc = local_of(st, v, ip)
        if not loc:
            out["gone_src"][e["id"]] = ip
            continue
        if e["kind"] == "xray":
            out["xsrc"][e["id"]] = loc
            continue
        idx = _leg_index(e)
        ep = leg_endpoint(idx) if idx is not None else None
        if ep:
            out["legs"].append((ep[0], ep[1], loc, e["id"]))
    seen = {}
    for ep_ip, ep_port, loc, eid in sorted(out["legs"]):
        seen.setdefault((ep_ip, ep_port), set()).add(loc)
    out["legs"] = sorted(x for x in out["legs"] if len(seen[(x[0], x[1])]) == 1)    # one E:P, two addresses: neither
    return out


def leg_map(mp, pool_exits=None):
    """{(E, P): local | None} — the legs with an address of their own as ``mp`` draws them (local) and those whose
    address is gone now (None, their Endpoint read from the config). The collector compares two of them after an apply
    (REV-mip-s34 С-3): a leg whose address changed, came or went is forgotten by conntrack (``forget``)."""
    out = dict(((e, p), loc) for e, p, loc, _ in (mp or {}).get("legs") or [])
    gone = (mp or {}).get("gone_src") or {}
    for e in pool_exits or []:
        if e.get("kind") == "awg" and e.get("id") in gone and _leg_index(e) is not None:
            ep = leg_endpoint(_leg_index(e))
            if ep:
                out.setdefault(ep, None)
    return out


def leg_changes(prev, now_):
    """The Endpoints of ``leg_map`` whose address differs between two maps; ``prev`` None (nothing known of the last
    apply — the first round of a process) — every leg of ``now_``."""
    if prev is None:
        return sorted(now_)
    return sorted(k for k in set(prev) | set(now_) if prev.get(k, False) != now_.get(k, False))


def post_rules(mp):
    """gw_post before masquerade: the legs with their own address (by Endpoint), then Н4."""
    out = ["ip daddr %s udp dport %d snat to %s" % (e, p, loc) for e, p, loc, _ in sorted(set(
        (a, b, c, d) for a, b, c, d in (mp or {}).get("legs") or []))]
    s = (mp or {}).get("snat")
    if s:
        out.append('ip saddr @gw_nets oifname "%s" ip daddr != @gw_nets snat to %s' % (s["dev"], s["src"]))
    return out


def fingerprint(mp):
    """What of ``plan`` the table and xray depend on (not the ports — an inbound change applies anyway): "" — nothing."""
    mp = mp or {}
    doc = {"in": (mp.get("in") or {}).get("addrs") if (mp.get("in") or {}).get("addrs") else None,
           "snat": mp.get("snat"), "legs": [list(x) for x in mp.get("legs") or []], "xsrc": mp.get("xsrc") or {}}
    if not any(doc.values()):
        return ""
    return hashlib.sha1(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()


def xray_outbound(e, mp):
    """The outbound of an xray exit with ``sendThrough`` of its address (never of an AmneziaWG exit: its freedom
    with the mark goes into the leg, the address of the leg's packets is SNAT's business)."""
    ob = dict(e["outbound"])
    loc = (mp or {}).get("xsrc", {}).get(e.get("id"))
    if loc and e.get("kind") == "xray":
        ob["sendThrough"] = loc
    return ob


def direct_send(mp):
    """``sendThrough`` of «direct» (Н4) — the source of the route while SNAT draws it for the kernel's inbounds."""
    s = (mp or {}).get("snat")
    return s["src"] if s else None


# ── the protection of the administrator's access (§3 «Защита доступа») ─────

def reply_kinds(st):
    """Inbounds that answer from the source of the route, not from the address asked (T-mip-lab Н2, Н3): Hysteria2 with
    the Salamander mask and AmneziaWG on the engine «program». On another address the answer comes from the wrong one —
    a client behind NAT drops it. -> [(kind, title)] (F13 gives them an IPv6 line only on the source of the IPv6
    route)."""
    out = []
    kinds = model.enabled_kinds(st)
    if "hy2" in kinds:
        try:
            from . import registry
            if registry.get("hy2").obfs(st):
                out.append(("hy2", tr("gw_mip.limit.hy2")))
        except Exception:                               # noqa: BLE001 — a line for people, never a failure
            pass
    try:
        from . import awgengine
        prog = awgengine.active() != "kernel"
        for it in awgengine.inbounds_of(st):
            if prog or not it.get("module"):
                out.append((it.get("kind"), tr("gw_mip.limit.awg", title=it["title"])))
    except Exception:                                   # noqa: BLE001 — a line for people, never a failure
        pass
    return out


def reply_limits(st):
    """The titles of ``reply_kinds``. -> [title]."""
    return [t for _, t in reply_kinds(st)]


def _site_a(st, name=None):
    """The A records Kaskad knows of the domain ``name`` — those the site's check found, only for the site's own domain
    (REV-mip-s34 З-1: another domain's records are not known); None — not known."""
    s = (st or {}).get("site")
    if not isinstance(s, dict) or (name is not None and str(name).strip().rstrip(".").lower() !=
                                   str(s.get("domain") or "").strip().rstrip(".").lower()):
        return None
    d = s.get("dns")
    a = d.get("a") if isinstance(d, dict) else None
    return [str(x) for x in a] if isinstance(a, list) else None


def _site_aaaa(st, name):
    """The AAAA records Kaskad knows of ``name`` (the site's own domain only, canonical), None — not known."""
    s = (st or {}).get("site")
    if not isinstance(s, dict) or not name or str(name).strip().rstrip(".").lower() != \
            str(s.get("domain") or "").strip().rstrip(".").lower():
        return None
    d = s.get("dns")
    a = d.get("aaaa") if isinstance(d, dict) else None
    return [x for x in (_ip6(y) for y in a) if x] if isinstance(a, list) else None


def _domain_lock(name, st, closed):
    """F11 (PLAN-multi-ip-roles §2.3): the domain of the panel (or of the panel behind nginx) whose A records hold an
    address the list closes (``closed`` — the public IPv4 of this server not in it) — a consequence to confirm; its
    records not known — the same question. -> the words, or None (nothing of it is closed)."""
    if not closed:
        return None
    a = _site_a(st, name)
    if a is None:
        return tr("gw_mip.lock.domain_unknown", domain=name, ips=", ".join(closed))
    hit = [x for x in a if x in closed]
    return tr("gw_mip.lock.domain_out", domain=name, ips=", ".join(hit)) if hit else None


def _names6(st):
    """The names whose AAAA records lead to the inbounds (PLAN-multi-ip-roles §9.2, §9.7 п. 3): the address for clients
    when it is a name, the domain of VLESS TLS and of Hysteria2 while they run on it, the domain of the panel (open
    mode by name, behind nginx). Lower case, each once."""
    out = []

    def add(n):
        n = str(n or "").strip().rstrip(".").lower()
        if n and not _ip4(n) and not _ip6(n) and n not in out:
            out.append(n)
    add(model.public_host(st))
    kinds = model.enabled_kinds(st)
    try:
        from . import registry
        if "vless" in kinds and registry.get("vless").security(st) == "tls":
            add(registry.get("vless").domain(st))
        if "hy2" in kinds:
            from .inbounds import hy2
            add(hy2.domain_ready(st))
    except Exception:                                   # noqa: BLE001 — a name less: the lock is a question, not a rule
        pass
    ph, proxy = _panel_host()
    add(ph)
    add(proxy)
    return out


def locks(st):
    """The addresses «не принимать» needs a confirmation (or another choice) for — the codes of the page (F11):
    {"host4": the address for clients (IPv4), "panel4": the panel's IPv4 and the A records of its domain, "panel6": the
    AAAA records of the panel's domain, "aaaa": the AAAA records of every name the devices get}."""
    out = {"host4": set(), "panel4": set(), "panel6": set(), "aaaa": set()}
    if _ip4(model.public_host(st)):
        out["host4"].add(_ip4(model.public_host(st)))
    ph, proxy = _panel_host()
    for n in (ph, proxy):
        if _ip4(n):
            out["panel4"].add(_ip4(n))
        elif n:
            out["panel4"].update(x for x in _site_a(st, n) or [] if _ip4(x))
            out["panel6"].update(_site_aaaa(st, n) or [])
    for n in _names6(st):
        out["aaaa"].update(_site_aaaa(st, n) or [])
    return out


def _confirm(items, yes, hint):
    """The locks (§2.3): consequences without ``yes`` — a refusal 409 with ``need: "confirm"`` and the list (the panel
    asks and sends ``yes``; the CLI repeats with ``--yes``; MCP, without ``yes``, gets the words of the CLI); with
    ``yes`` — they are the warnings of the write."""
    if not items:
        return []
    if yes:
        return list(items)
    raise ConflictError(tr("gw_mip.err.confirm", what="; ".join(items), how=hint),
                        data={"reason": "confirm", "need": "confirm", "consequences": list(items)})


def _hint(cmd, panel=False):
    return tr("gw_mip.confirm.panel") if panel else tr("gw_mip.confirm.cli", cmd=cmd)


def _domain_warn(name, st, want):
    """A domain (of the clients, the panel) whose A records known to Kaskad are not all chosen: a warning (DNS is not
    ours to change); its records not known — a warning too."""
    a = _site_a(st, name)
    if a is None:
        return tr("gw_mip.warn.domain_unknown", domain=name)
    out = [x for x in a if x not in want]
    return tr("gw_mip.warn.domain_out", domain=name, ips=", ".join(out)) if out else None


def _refuse(key, reason, back=None, **kw):
    back = back or BACK
    return ConflictError(tr(key, back=back, **kw), data=dict({"reason": reason, "back": back}, **kw))


def _panel_host():
    """(host of the panel and the subscriptions in the open mode, the domain of the panel behind nginx) — "" each."""
    try:
        from ..panel import conf
        cfg = conf.load()
    except Exception:                                   # noqa: BLE001 — no panel
        return "", ""
    pub = cfg.get("public") or {}
    host = conf.public_host(cfg) if pub.get("on") else ""
    proxy = cfg["proxy"]["domain"] if (cfg.get("proxy") or {}).get("on") else ""
    return host, proxy


def host_check(st, host):
    """The address for clients is about to become ``host`` (``gw host``, ``--detect``, the menu, MCP, the panel —
    REV-mip-s34 С-1, PLAN §3 «Адрес для клиентов» п. 1–2): with the list of «Приём подключений» set, an IPv4 not in it
    is refused (the devices would connect where nothing is taken) with the way on — add it to the list first, or take
    connections on every address; a domain gives the warning of its A records. -> [warning]."""
    want = in_list(st)
    if not want or not host or ":" in str(host):        # IPv6 is not limited by the list (решение 7)
        return []
    if _ip4(host):
        if _ip4(host) not in want:
            raise _refuse("gw_mip.err.host_not_in", "host_not_in", ip=_ip4(host), ips=", ".join(want),
                          add=",".join(want + [_ip4(host)]))
        return []
    w = _domain_warn(host, st, want)
    return [w] if w else []


def access_check(st, want, v, local=None, env=None, back=None, locks_out=None):
    """The checks before ``in`` is written (one for the panel, the CLI, MCP and the menu — PLAN §3 п. 1–4): raises the
    refusal (ConflictError: data reason, back), returns the warnings. ``local`` — the address of the panel's socket the
    request came to (the open mode), ``env`` — for SSH_CONNECTION. F11: the domain of the panel and the one behind nginx
    are locks (``_domain_lock``) — their consequences go to ``locks_out`` (the writer asks to confirm them)."""
    warns = []
    locks_out = [] if locks_out is None else locks_out
    ours = set((v or {}).get("pub", {})) | set(e["ip"] for e in addresses.manual_list(st) if local_of(st, v, e["ip"]))
    closed = sorted((x for x in ours if x not in want), key=_ipkey)
    for ip in want:
        if not addresses.is_public4(ip):
            raise KaskadError(tr("gw_addresses.err.not_public", value=ip), code=USAGE)
        if local_of(st, v, ip) is None:
            known = sorted(set(x for x in (v or {}).get("pub", {})) |
                           set(e["ip"] for e in addresses.manual_list(st) if local_of(st, v, e["ip"])), key=_ipkey)
            raise _refuse("gw_mip.err.not_here", "address_not_here", back=back, ip=ip,
                          known=", ".join(known) or tr("gw_addresses.none"))
    host = model.public_host(st)
    if _ip4(host):
        if host not in want:
            raise _refuse("gw_mip.err.client_out", "client_out", back=back, ip=host)
    elif host:
        w = _domain_warn(host, st, want)
        if w:
            warns.append(w)
    ph, proxy = _panel_host()
    if _ip4(ph):
        if ph not in want:
            raise _refuse("gw_mip.err.panel_out", "panel_out", back=back, ip=ph)
    elif ph:
        w = _domain_lock(ph, st, closed)
        if w:
            locks_out.append(w)
    if proxy and proxy != ph:
        w = _domain_lock(proxy, st, closed)
        if w and w not in locks_out:
            locks_out.append(w)
    allowed = set(local_of(st, v, x) for x in want)
    if local and _ip4(local) and not ipaddress.IPv4Address(local).is_loopback and local not in allowed:
        raise _refuse("gw_mip.err.via_out", "via_out", back=back, ip=public_of(st, v, local))
    sc = ((env if env is not None else os.environ).get("SSH_CONNECTION") or "").split()
    if len(sc) == 4 and _ip4(sc[2]) and sc[2] not in allowed and not ipaddress.IPv4Address(sc[2]).is_loopback:
        warns.append(tr("gw_mip.warn.ssh_out", ip=public_of(st, v, sc[2])))
    prim = public_of(st, v, (v or {}).get("src")) if (v or {}).get("src") else ""
    lim = reply_limits(st)
    if lim and any(x != prim for x in want):
        warns.append(tr("gw_mip.warn.limits", what=", ".join(lim), primary=prim or "?"))
    return warns


def access6_check(st, want, here6, local=None, env=None, back=None):
    """F11: the checks before ``in6`` is written (PLAN-multi-ip-roles §9.7 «Защита доступа»): every IPv6 of the list is
    one of this machine (``here6`` — ``addresses6`` of the cache; deprecated ones too); the panel opened over an IPv6
    the list closes — a confirmation «the browser moves to IPv4» when the panel's domain has an A record on an IPv4
    that takes connections, else a refusal (as ``via_out``); the IPv6 of this SSH session closed — a warning (the port
    of SSH is never closed); an AAAA record of a name the devices get (or of the panel's) on a closed IPv6 — a
    confirmation; the AAAA records of the panel's domain not known — the same question. -> (locks, warnings)."""
    here = [a["ip"] for a in here6 or [] if isinstance(a, dict) and a.get("ip")]
    for ip in want:
        if ip not in here:
            raise _refuse("gw_mip.err.not_here6", "address_not_here", back=back, ip=ip,
                          known=", ".join(here) or tr("gw_addresses.none"))
    locks_, warns = [], []
    loc = _ip6(local)
    if loc and loc not in want and not addresses._v6(loc).is_loopback:
        ph, proxy = _panel_host()
        name = proxy or (ph if not _ip4(ph) else "")
        acc = in_list(st)
        ok4 = [x for x in (_site_a(st, name) or []) if addresses.is_public4(x) and (acc is None or x in acc)] \
            if name else []
        if not ok4:
            raise _refuse("gw_mip.err.via6_out", "via_out", back=back, ip=loc, domain=name or "—")
        locks_.append(tr("gw_mip.lock.panel6", ip=loc, domain=name, ip4=ok4[0]))
    sc = ((env if env is not None else os.environ).get("SSH_CONNECTION") or "").split()
    if len(sc) == 4 and _ip6(sc[2]) and _ip6(sc[2]) not in want and not addresses._v6(sc[2]).is_loopback:
        warns.append(tr("gw_mip.warn.ssh6_out", ip=_ip6(sc[2])))
    closed = [ip for ip in here if ip not in want]
    for name in _names6(st):
        for ip in [x for x in _site_aaaa(st, name) or [] if x in closed]:
            w = tr("gw_mip.lock.aaaa", domain=name, ip=ip)
            if w not in locks_:
                locks_.append(w)
    # REV-3315-b1 З-2 (PLAN-multi-ip-roles §9.7 п. 3, §2.3): the AAAA records of the domain of the panel (the open mode
    # by name, behind nginx) not known while an IPv6 is closed — the same question as its A records not known; the
    # other names the devices get ask only for a record known to lead to a closed IPv6
    ph, proxy = _panel_host()
    for name in (ph, proxy):
        if closed and name and not _ip4(name) and not _ip6(name) and _site_aaaa(st, name) is None:
            w = tr("gw_mip.lock.aaaa_unknown", domain=name, ips=", ".join(closed))
            if w not in locks_:
                locks_.append(w)
    return locks_, warns


# ── the document for people (gw addresses, the menu) ───────────────────────

def status(st, v=None, pool_exits=None):
    """{"in": {"all", "list", "gone", "active"}, "in6": {"all", "list", "none", "gone"}, "give6": {"ip", "on", "why"}
    (F13, ``addresses.give6_state``), "exits": [{"id", "src", "here"}], "limits": [title], "primary": the public address
    of the route, "multi": the server has more than one public address, "direct": {"ip", "on", "why"} and "out": [ip] |
    None (F12а), "back"}."""
    v = machine() if v is None else v
    want = in_list(st)
    gone = [ip for ip in want or [] if local_of(st, v, ip) is None]
    srcs = []
    for eid, ip in sorted(src_map(st).items()):
        srcs.append({"id": eid, "src": ip, "here": local_of(st, v, ip) is not None})
    prim = public_of(st, v, v["src"]) if v and v.get("src") else ""
    h4 = snat_plan(v)                                   # REV-mip-s34 Б-1: the difference is said, never drawn by itself
    h4 = {"kernel": public_of(st, v, masq_source(v)), "xray": public_of(st, v, h4["src"])} if h4 else None
    dp = direct_plan(st, v)
    if dp["snat"]:
        h4 = None                                       # F12а: «напрямую» of the choice — one address for both
    want6 = addresses.in6_list(st)
    m6 = addresses.load_cache() if want6 or addresses.give6_of(st) else None
    here6 = [a.get("ip") for a in (m6 or {}).get("addresses6") or [] if isinstance(a, dict)] if want6 else []
    return {"in": {"all": want is None, "list": want or [], "gone": gone,
                   "active": bool(want) and len(gone) < len(want)},
            "in6": {"all": want6 is None, "list": want6 or [], "none": want6 == [],
                    "gone": [ip for ip in want6 or [] if ip not in here6]},
            "give6": addresses.give6_state(st, m6 or {}),             # F13: the extra IPv6 line of the subscription
            "exits": srcs, "limits": reply_limits(st), "primary": prim, "multi": len((v or {}).get("pub") or {}) > 1,
            "h4": h4, "direct": {"ip": dp["ip"], "on": bool(dp["snat"]), "why": dp["why"]}, "out": out_list(st),
            "route_out": _route_out(st, prim, pool_exits), "back": BACK}


def _route_out(st, route, pool_exits):
    """REV-3315-b2 Н-1: the address of the route is not «для выхода», yet exits without an address of their own (AWG
    and xray, not to an IPv6) go from it -> {"ip", "ids"}; else None (no choice, no pool given, none such)."""
    outs = out_list(st)
    if outs is None or not route or route in outs:
        return None
    ids = [e["id"] for e in pool_exits or [] if e.get("kind") in ("awg", "xray") and not exit_src(st, e.get("id"))
           and not exit_v6(e)]
    return {"ip": route, "ids": ids} if ids else None


def lines(doc, names=None):
    """Lines for people of ``status``."""
    return in_lines(doc) + src_lines(doc, names)


def in_lines(doc):
    s = doc["in"]
    out = [tr("gw_mip.in.all") if s["all"] else tr("gw_mip.in.only", ips=", ".join(s["list"]))]
    if s["list"] and not s["active"]:
        out.append(tr("gw_mip.in.all_gone", ips=", ".join(s["gone"])))
    elif s["gone"]:
        out.append(tr("gw_mip.in.some_gone", ips=", ".join(s["gone"])))
    s6 = doc.get("in6") or {"all": True}
    if not s6["all"]:                                  # F11: said only when IPv6 has a choice of its own
        if s6.get("none"):
            out.append(tr("gw_mip.in6.none"))
        else:
            out.append(tr("gw_mip.in6.only", ips=", ".join(s6["list"])))
            if s6.get("gone") and len(s6["gone"]) == len(s6["list"]):
                out.append(tr("gw_mip.in6.all_gone", ips=", ".join(s6["gone"])))
    g6 = doc.get("give6") or {}
    if g6.get("ip"):                                   # F13: «IPv6 — дополнительной строкой» (the frame Д3)
        out.append(tr("gw_mip.give6.on", ip=g6["ip"]) if g6.get("on") else
                   tr("gw_mip.give6.idle", ip=g6["ip"], why=tr("gw_mip.give6.why.%s" % g6.get("why")),
                      back=BACK_GIVE))
    if not s["all"] or not s6["all"]:
        out.append(tr("gw_mip.in.back", back=doc["back"]))
    if doc.get("limits") and doc.get("multi"):         # T-mip-lab Н2, Н3: said at the inbounds, not only on a choice
        out.append("! " + tr("gw_mip.warn.limits", what=", ".join(doc["limits"]), primary=doc.get("primary") or "?"))
    return out


def src_lines(doc, names=None):
    out = []
    if doc.get("h4"):                                   # Н4: the kernel and xray go out «directly» from two addresses
        out.append("! " + tr("gw_mip.h4.differ", kernel=doc["h4"]["kernel"], xray=doc["h4"]["xray"]))
    d = doc.get("direct") or {}
    if d.get("ip"):                                     # F12а: «напрямую» of the choice — acting, or why not
        out.append(tr("gw_mip.direct.line", ip=d["ip"], route=doc.get("primary") or "?") if d.get("on") else
                   "! " + tr("gw_mip.direct.idle", ip=d["ip"], why=tr("gw_mip.direct.why.%s" % d.get("why")),
                             route=doc.get("primary") or "?"))
    if doc.get("out"):
        out.append(tr("gw_mip.out.line", ips=", ".join(doc["out"])))
    ro = doc.get("route_out")
    if ro:                                              # REV-3315-b2 Н-1: they still go from the address of the route
        out.append("! " + tr("gw_mip.out.route_loose", ip=ro["ip"],
                             names=", ".join((names or {}).get(x) or x for x in ro["ids"])))
    for x in doc["exits"]:
        name = (names or {}).get(x["id"]) or x["id"]
        out.append(tr("gw_mip.src.line" if x["here"] else "gw_mip.src.gone", name=name, ip=x["src"]))
    if doc["exits"]:
        out.append(tr("gw_mip.src.bench"))              # PLAN §5 вопрос 5: the server check — of the main address
        out.append(tr("gw_mip.src.tg"))                 # REV-mip-s34 З-4: the bot's proxy — from the main address
    return out


# ── the writers (one core for the CLI, MCP, the menu and the panel) ────────

def _exit_id_ok(eid):
    return bool(model._EXIT_ID.match(str(eid or "")))


def set_in(runner, ips=None, every=False, local=None, env=None, back=None, yes=False, want_ver=None, panel=False):
    """``gw addresses in IP… | --all`` (PLAN §3, Ш3). ``every`` — every address again (the way back, never refused; F11:
    IPv6 too — ``in6`` goes with ``in``). The list: public IPv4 of «Адреса сервера» on the machine now; the checks of
    «Защита доступа» (``access_check``) under the lock, before anything is written — F11: the domain of the panel is a
    lock, its consequences need ``yes`` (else 409 ``need: "confirm"``); ``want_ver`` — the ``ver`` of the page (the
    panel, З-3); then the gateway applied (the rule of gw_pre), the panel follows by itself
    (``panel.conf.listen_addresses``). -> {"ok", "in", "warnings", "text", "back"}."""
    from . import ops
    want = None
    if not every:
        want = []
        for x in [y.strip() for z in ips or [] for y in str(z).split(",") if y.strip()]:      # «A,B» as MCP sends it
            ip = _ip4(x)
            if not ip or not addresses.is_public4(ip):
                raise KaskadError(tr("gw_addresses.err.not_public", value=str(x)[:64]), code=USAGE)
            if ip not in want:
                want.append(ip)
        if not want:
            raise KaskadError(tr("gw_mip.err.in_empty", back=back or BACK), code=USAGE)
        if len(want) > addresses.MANUAL_MAX:
            raise KaskadError(tr("gw_addresses.err.too_many", n=addresses.MANUAL_MAX), code=USAGE)
    if want:
        addresses.collect(runner, net=False)            # the machine now: never a decision on a gone address
    v = machine()
    said = []
    cmd = "kaskadctl gw addresses in %s --yes" % " ".join(want or [])

    def mutate(new, sec):
        check_ver(new, want_ver)
        if want:
            lk = []
            said.extend(access_check(new, want, v, local, env, back, locks_out=lk))
            said[:0] = _confirm(lk, yes, _hint(cmd, panel))
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        if want:
            a["in"] = {IN_KEY: list(want)}
        else:
            a.pop("in", None)
            a.pop("in6", None)                          # F11: «на всех адресах» — IPv6 too (PLAN-multi-ip-roles §9.7)
        if a:
            g["addresses"] = a
        else:
            g.pop("addresses", None)
    _, res = ops._change(runner, mutate)
    util.log_tr("gw_mip.log.in", ips=", ".join(want) if want else tr("gw_mip.log.every"))
    warns = said + list((res or {}).get("warnings") or [])
    if want and not model.enabled(state.load()):
        warns.append(tr("gw_mip.warn.gw_off"))
    text = tr("gw_mip.in.set_only", ips=", ".join(want), back=back or BACK) if want else tr("gw_mip.in.set_all")
    return {"ok": True, "in": want, "warnings": warns, "text": text, "back": back or BACK}


def set_in6(runner, ips=None, every=False, none=False, yes=False, local=None, env=None, back=None, want_ver=None,
            panel=False):
    """F11: ``gw addresses in6 IPv6… | --all | --none [--yes]`` — the IPv6 of this server the inbounds take connections
    on: ``every`` — every IPv6 again (``in6`` None, the chain gw_pre6 goes; never refused), ``none`` — no IPv6 (IPv4 as
    chosen), else only these (global IPv6 of this machine, canonical; deprecated ones too). The checks of
    ``access6_check`` under the lock — a lock needs ``yes`` (409 ``need: "confirm"``), a refusal names the way back;
    ``want_ver`` — the ``ver`` of the page (the panel). The table ``ip6 kaskad6`` follows with the apply (gw_pre6).
    -> {"ok", "in6", "warnings", "text", "back"}."""
    from . import ops
    if sum(bool(x) for x in (every, none, bool(ips))) != 1:
        raise KaskadError(tr("gw_mip.err.in6_how", back=back or BACK), code=USAGE)
    want = None
    if none:
        want = []
    elif not every:
        want = []
        for x in [y.strip() for z in ips or [] for y in str(z).split(",") if y.strip()]:
            ip = _ip6(x.strip("[]"))
            if not ip or not addresses.is_public6(ip):
                raise KaskadError(tr("gw_mip.err.not_public6", value=str(x)[:64]), code=USAGE)
            if ip not in want:
                want.append(ip)
        if not want:
            raise KaskadError(tr("gw_mip.err.in6_how", back=back or BACK), code=USAGE)
        if len(want) > addresses.V6_MAX:
            raise KaskadError(tr("gw_addresses.err.too_many", n=addresses.V6_MAX), code=USAGE)
    here6 = []
    if want is not None:
        addresses.collect(runner, net=False)            # the machine now: never a decision on a gone address
        here6 = (addresses.load_cache() or {}).get("addresses6") or []
    said = []
    cmd = "kaskadctl gw addresses in6 %s --yes" % (" ".join(want) if want else "--none")

    def mutate(new, sec):
        check_ver(new, want_ver)
        if want is not None:
            lk, warns = access6_check(new, want, here6, local, env, back)
            said.extend(_confirm(lk, yes, _hint(cmd, panel)) + warns)
            given = addresses.give6_of(new)
            if given and given not in want:             # F13: «принимать и выдавать» — no lines while not taken
                said.append(tr("gw_mip.warn.in6_closes_give", ip=given, back=BACK_GIVE))
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        if want is None:
            a.pop("in6", None)
        else:
            a["in6"] = {addresses.IN6_KEY: sorted(want, key=_ip6key)}
        if a:
            g["addresses"] = a
        else:
            g.pop("addresses", None)
    _, res = ops._change(runner, mutate)
    util.log_tr("gw_mip.log.in6", ips=", ".join(want) if want else tr("gw_mip.log.in6_none") if want == [] else
                tr("gw_mip.log.every"))
    warns = said + list((res or {}).get("warnings") or [])
    if want is not None and not model.enabled(state.load()):
        warns.append(tr("gw_mip.warn.gw_off6"))
    if want is None:
        text = tr("gw_mip.in6.set_all")
    elif want:
        text = tr("gw_mip.in6.set_only", ips=", ".join(sorted(want, key=_ip6key)), back=back or BACK)
    else:
        text = tr("gw_mip.in6.set_none", back=back or BACK)
    return {"ok": True, "in6": want, "warnings": warns, "text": text, "back": back or BACK}


BACK_GIVE = "kaskadctl gw addresses give6 off"


def _reality_on(st):
    """VLESS runs on Reality (its ``listen`` follows ``give``, F13 С-5) in the xray of the gateway — on (REV-3315-b2
    П-6: the gateway off, no xray of it runs — no restart to ask about)."""
    try:
        from . import registry
        return model.enabled(st) and "vless" in model.enabled_kinds(st) and registry.get("vless").security(st) != "tls"
    except Exception:                                   # noqa: BLE001 — not known: no question of a restart
        return False


def _give_items(a, want):
    """``give`` of ``a`` (``gateway.addresses``) with the IPv6 element ``want`` (None — no IPv6 element): an element of
    the same IPv6 keeps its ``n``, a new one takes the smallest number free (the first one — 1); the IPv4 elements and
    the other keys a later version wrote stay as they are (never wiped by a write of this one, §9.7). -> the record,
    None — nothing left."""
    give = a.get("give") if isinstance(a.get("give"), dict) else {}
    old = [it for it in give.get("items") or [] if isinstance(it, dict)] if isinstance(give.get("items"), list) else []
    keep = [dict(it) for it in old if _ip4(it.get("ip"))]
    if want:
        n = next((it.get("n") for it in old if isinstance(it.get("ip"), str) and _ip6(it["ip"]) == want), None)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            used = set(it.get("n") for it in keep)
            n = 1
            while n in used:
                n += 1
        keep.append({"ip": want, "n": n})
    rest = dict((k, v) for k, v in give.items() if k != "items")
    return dict(rest, items=keep) if keep or rest else None


def set_give6(runner, ip=None, yes=False, want_ver=None, panel=False):
    """F13 (SPEC-R1 §3 F13, решение 6): ``gw addresses give6 IPv6|off [--yes]`` — «принимать и выдавать» of an IPv6:
    the subscription gives it as an extra line after the IPv4 ones (VLESS Reality, Hysteria2, WireGuard/AmneziaWG).
    The IPv6: one of this machine, global, not deprecated, with a way out (``route_ok6``), seen outside as itself
    (``echo6``; no answer of the echo — a warning «не проверен снаружи»); a temporary one (SLAAC) may — the subscription
    only. An IPv4 is refused («выдача по адресам IPv4 — в следующем выпуске»). It must take connections: with a list
    ``in6`` without it the same write adds it there. VLESS Reality listens on IPv6 by this record (С-5): a change of it
    restarts xray — a consequence to confirm (``yes``; the panel — 409 ``need: "confirm"``). ``want_ver`` — the
    ``ver`` of the page. -> {"ok", "give6", "warnings", "text", "back"}."""
    from . import ops
    want = None
    if ip not in (None, "", "off"):
        raw = str(ip).strip().strip("[]")
        if _ip4(raw):
            raise KaskadError(tr("gw_mip.err.give4_later"), code=USAGE)
        want = _ip6(raw)
        if not want or not addresses.is_public6(want):
            raise KaskadError(tr("gw_mip.err.not_public6", value=str(ip)[:64]), code=USAGE)
    warns = []
    if want:
        addresses.collect(runner, net=True)             # the machine now, the echo of IPv6 at most once in FRESH s
        m = addresses.load_cache() or {}
        rows = [a for a in m.get("addresses6") or [] if isinstance(a, dict)]
        row = next((a for a in rows if _ip6(a.get("ip")) == want), None)
        if row is None:
            raise _refuse("gw_mip.err.give6_not_here", "address_not_here", back=BACK_GIVE, ip=want,
                          known=", ".join(a.get("ip") for a in rows if a.get("ip")) or tr("gw_addresses.none"))
        if row.get("deprecated") is True:
            raise _refuse("gw_mip.err.give6_deprecated", "deprecated", back=BACK_GIVE, ip=want)
        if row.get("route_ok6") is not True:
            raise _refuse("gw_mip.err.give6_no_route", "no_route6", back=BACK_GIVE, ip=want)
        seen = _ip6(row.get("echo6") or "")
        if seen and seen != want:
            raise _refuse("gw_mip.err.give6_echo_other", "echo_other", back=BACK_GIVE, ip=want, seen=seen)
        if not seen:
            warns.append(tr("gw_mip.warn.give6_unchecked", ip=want))
        if row.get("dynamic") is True and row.get("valid_lft") is not None:
            warns.append(tr("gw_mip.warn.give6_dynamic", ip=want))
        src6 = addresses.route6_src(m)
        lim = [t for _, t in reply_kinds(state.load())]
        if lim and want != src6:
            warns.append(tr("gw_mip.warn.give6_limits", what=", ".join(lim), src=src6 or "?"))
    cmd = "kaskadctl gw addresses give6 %s --yes" % (want or "off")

    def mutate(new, sec):
        check_ver(new, want_ver)
        from kaskad.site import nginx
        before = addresses.give6_of(new)
        if bool(before) != bool(want) and _reality_on(new) and nginx.has_ipv6():
            what = tr("gw_mip.lock.give6_listen_on" if want else "gw_mip.lock.give6_listen_off")
            warns[:0] = _confirm([what], yes, _hint(cmd, panel))
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        acc = addresses.in6_list(new)
        if want and acc is not None and want not in acc:
            a["in6"] = {addresses.IN6_KEY: sorted(acc + [want], key=_ip6key)}   # «принимать и выдавать» takes them
            warns.append(tr("gw_mip.warn.give6_in6", ip=want))
        rec = _give_items(a, want)
        if rec is None:
            a.pop("give", None)
        else:
            a["give"] = rec
        if a:
            g["addresses"] = a
        else:
            g.pop("addresses", None)
    _, res = ops._change(runner, mutate)
    util.log_tr("gw_mip.log.give6", ip=want or tr("gw_mip.log.give6_off"))
    warns += list((res or {}).get("warnings") or [])
    text = tr("gw_mip.give6.set", ip=want, back=BACK_GIVE) if want else tr("gw_mip.give6.unset")
    return {"ok": True, "give6": want, "warnings": warns, "text": text, "back": BACK_GIVE}


def exit_v6(e):
    """The address an exit's key leads to is an IPv6 literal (``vless://…@[2001:db8::1]:443``, an AmneziaWG
    ``Endpoint = [..]:port``) -> that IPv6, else None (an IPv4, a name — not known here)."""
    cands = []
    try:
        from . import exits
        cands.append(exits.outbound_server(e.get("outbound"))[0])
    except Exception:                                   # noqa: BLE001 — no outbound (an AmneziaWG exit)
        pass
    cands.append(e.get("host"))
    for h in cands:
        h = str(h or "").strip()
        if h.startswith("["):
            h = h[1:].split("]", 1)[0]
        elif h.count(":") == 1:
            h = h.split(":", 1)[0]                      # host:port of IPv4 or a name
        ip = _ip6(h)
        if ip:
            return ip
    if e.get("kind") == "awg" and _leg_index(e) is not None:
        text = util.read_text(os.path.join(paths.egress_dir(), "awg", LEG_CONF % _leg_index(e))) or ""
        m = re.search(r"(?m)^\s*Endpoint\s*=\s*\[([0-9A-Fa-f:.]+)\]:\d{1,5}\s*$", text)
        if m and _ip6(m.group(1)):
            return _ip6(m.group(1))
    return None


def _pool(runner):
    from . import exits
    return exits.pool(runner, roles=exits.ROLES)


def group_ids(st, gid, pool_exits):
    """The exits of the group ``gid`` (С3: one address to the whole group)."""
    from . import policy as P
    grp = P.group(P.load(st, pool_exits), gid)
    if grp is None:
        raise KaskadError(tr("gw_mip.err.no_group", group=str(gid)[:40]), code=USAGE)
    return list(P.exits_of(grp))


def set_src(runner, ids, ip=None, group=None, want_ver=None):
    """``gw addresses exit ID|--group G IP|main`` (Ш4): the exits ``ids`` are reached from ``ip`` (None — by the route of
    the system). Refused: not an exit of the pool, an address not on the machine; for an AmneziaWG exit also an answer
    from the address leaving another way than the default route (SNAT would send it through the gateway of the main
    address — ``route_ok``) and another leg to the same Endpoint with another address. After the apply the connections
    of the changed legs are forgotten (``conntrack -D``): keepalive would keep the old address for good."""
    from . import ops
    pool = _pool(runner)
    by = dict((e["id"], e) for e in pool)
    st = state.load()
    if group is not None:
        ids = group_ids(st, group, pool)
    ids = [str(x) for x in ids or []]
    if not ids:
        raise KaskadError(tr("gw_mip.err.no_exits"), code=USAGE)
    for eid in ids:
        if not _exit_id_ok(eid) or eid not in by or by[eid].get("kind") not in ("awg", "xray"):
            raise KaskadError(tr("gw_mip.err.not_exit", id=str(eid)[:40]), code=USAGE)
    want = None
    if ip not in (None, "", "main"):
        want = _ip4(ip)
        if not want or not addresses.is_public4(want):
            raise KaskadError(tr("gw_addresses.err.not_public", value=str(ip)[:64]), code=USAGE)
        addresses.collect(runner, st, net=False)
        v = machine()
        loc = local_of(st, v, want)
        if loc is None:
            raise _refuse("gw_mip.err.not_here", "address_not_here", ip=want,
                          known=", ".join(sorted((v or {}).get("pub", {}), key=_ipkey)) or tr("gw_addresses.none"))
        outs = out_list(st)                             # F12а (§6.5 п. 5): only an address «для выхода»
        if outs is not None and want not in outs:
            raise _refuse("gw_mip.err.not_out", "not_out", back=tr("gw_mip.back_roles"), ip=want)
        legs = [by[x] for x in ids if by[x]["kind"] == "awg"]
        if legs and route_ok(v, loc) is not True:
            raise _refuse("gw_mip.err.route_other", "route_other", ip=want)
        from . import exits as X
        for eid in ids:                                 # F11 (PLAN-multi-ip-roles §9.3): the key leads to an IPv6
            host6 = exit_v6(by[eid])
            if host6:
                raise _refuse("gw_mip.err.src_family", "src_family", ip=want, host=host6, name=X.label(st, by[eid]))
    elif out_list(st) is not None and any(not exit_v6(by[x]) for x in ids):
        # F12а (REV-3315-b2 Н-1, §6.5 п. 5): «по маршруту» is the address of the route — refused while it is not
        # «для выхода», as «напрямую» null (``set_direct`` ``direct_needed``); an exit to an IPv6 goes by the IPv6 route
        addresses.collect(runner, st, net=False)
        v = machine()
        route = public_of(st, v, v["src"]) if v and v.get("src") else ""
        if route and route not in out_list(st):
            raise _refuse("gw_mip.err.src_route_out", "not_out", back=tr("gw_mip.back_roles"), ip=route)
    eps = {}
    for e in pool:
        if e.get("kind") == "awg" and _leg_index(e) is not None:
            ep = leg_endpoint(_leg_index(e))
            if ep:
                eps[e["id"]] = ep
    for eid in ids:
        if eid not in eps:
            continue
        for other, ep in eps.items():
            if other not in ids and ep == eps[eid] and exit_src(st, other) != want:
                raise _refuse("gw_mip.err.same_endpoint", "same_endpoint", ip=want or "-", ep="%s:%d" % ep)

    def mutate(new, sec):
        check_ver(new, want_ver)
        recs = model.gw(new)["exits"]
        for eid in ids:
            rec = recs.get(eid) if isinstance(recs.get(eid), dict) else {}
            if want:
                rec["src"] = want
            else:
                rec.pop("src", None)
            if rec:
                recs[eid] = rec
            else:
                recs.pop(eid, None)
    _, res = ops._change(runner, mutate)
    # REV-mip-s34 П-2: only the legs whose address really changed (the same choice again — no break of the leg)
    flushed = forget(runner, [eps[x] for x in ids if x in eps and exit_src(st, x) != want])
    from . import exits
    names = dict((e["id"], exits.label(st, e)) for e in pool)
    util.log_tr("gw_mip.log.src", exits=", ".join(names.get(x, x) for x in ids), ip=want or tr("gw_mip.log.route"))
    warns = list((res or {}).get("warnings") or [])
    if want and not model.enabled(state.load()):
        warns.append(tr("gw_mip.warn.src_gw_off"))
    if want and any(by[x].get("kind") == "xray" for x in ids):
        warns.append(tr("gw_mip.src.tg"))               # REV-mip-s34 З-4: the bot's proxy is not the gateway's xray
    text = tr("gw_mip.src.set", ip=want, n=len(ids)) if want else tr("gw_mip.src.unset", n=len(ids))
    return {"ok": True, "ids": ids, "src": want, "flushed": flushed, "warnings": warns, "text": text}


# ── F12а: «напрямую» и «для выхода» (PLAN-multi-ip-roles §2.3–2.4, §6.2, §6.5 п. 3, 5) ───

def _ours4(st, v):
    """The public IPv4 of this machine now (the interfaces, the NAT pairs, the manual ones with their local address
    here), in the order of ``_ipkey``."""
    return sorted(set((v or {}).get("pub", {})) | set(e["ip"] for e in addresses.manual_list(st)
                                                       if local_of(st, v, e["ip"])), key=_ipkey)


def _role_ip4(ip):
    """A public IPv4 of the choice of «напрямую» / «для выхода»; an IPv6 — refused in words (§9.3: the devices and
    the exits go online over IPv4), anything else — a usage error."""
    raw = str(ip if ip is not None else "").strip().strip("[]")
    if _ip6(raw):
        raise KaskadError(tr("gw_mip.err.role_family", ip=raw), code=USAGE)
    want = _ip4(raw)
    if not want or not addresses.is_public4(want):
        raise KaskadError(tr("gw_addresses.err.not_public", value=str(ip)[:64]), code=USAGE)
    return want


def set_direct(runner, ip=None, want_ver=None):
    """F12а: «Напрямую — с адреса [выбор]» — one address for the traffic «напрямую» of every inbound (the kernel's —
    SNAT in gw_post, xray's — ``sendThrough`` of «direct»; Н4 aligned). ``ip`` None / "main" — no choice (the address
    of the route, as 3.3.7.14; refused while the address of the route is not «для выхода»: then «напрямую» must go from
    another one). The address: a public IPv4 of this machine, «для выхода», an answer from it leaving the way of the
    default route (``route_ok``: else SNAT would go through the wrong gateway). A refusal names the way back («Сбросить
    выбор» of the page). ``want_ver`` — the ``ver`` of the page. -> {"ok", "direct", "warnings", "text"}."""
    from . import ops
    back = tr("gw_mip.back_roles")
    want = None if ip in (None, "", "main") else _role_ip4(ip)
    addresses.collect(runner, net=False)                # the machine now: never a decision on a gone address
    v = machine()
    route = public_of(state.load(), v, v["src"]) if v and v.get("src") else ""

    def mutate(new, sec):
        check_ver(new, want_ver)
        outs = out_list(new)
        if want:
            loc = local_of(new, v, want)
            if loc is None:
                raise _refuse("gw_mip.err.role_not_here", "address_not_here", back=back, ip=want,
                              known=", ".join(_ours4(new, v)) or tr("gw_addresses.none"))
            if outs is not None and want not in outs:
                raise _refuse("gw_mip.err.not_out", "not_out", back=back, ip=want)
            if route_ok(v, loc) is not True or not DEV_NAME.match((v or {}).get("dev") or ""):
                raise _refuse("gw_mip.err.direct_route", "route_other", back=back, ip=want)
        elif outs is not None and route and route not in outs:
            raise _refuse("gw_mip.err.direct_needed", "direct_needed", back=back, route=route)
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        if want:
            a["direct"] = want
        else:
            a.pop("direct", None)
        if a:
            g["addresses"] = a
        else:
            g.pop("addresses", None)
    _, res = ops._change(runner, mutate)
    util.log_tr("gw_mip.log.direct", ip=want or tr("gw_mip.log.route"))
    warns = list((res or {}).get("warnings") or [])
    if want and not model.enabled(state.load()):
        warns.append(tr("gw_mip.warn.direct_gw_off"))
    text = tr("gw_mip.direct.set", ip=want, route=route or "?") if want else tr("gw_mip.direct.unset")
    return {"ok": True, "direct": want, "warnings": warns, "text": text}


def set_out(runner, ip, on, yes=False, want_ver=None):
    """F12а: «Для выхода: да / нет» of a public IPv4 of this machine (``addresses.out``; None — every address, no
    choice). «Нет» on the address «напрямую» goes from (the choice, or the address of the route while there is none)
    or on the address exits without an address of their own (the route's) or with this one are reached from — §2.3,
    §6.5 п. 3: in the same write, confirmed (``yes``; else 409 ``need: "confirm"`` with the plan «сайты будут видеть
    B; выходы X, Y — с B»), they move to another address «для выхода» with a way out (``route_ok``: the current
    «напрямую» first, else the first one); no such address — refused in words; the last address «для выхода» — refused.
    The traffic of the server itself (updates, the bot) stays on the address of the route. -> {"ok", "out", "direct",
    "moved": [{"id", "src"}], "flushed", "warnings", "text"}."""
    from . import ops
    from . import exits as X
    back = tr("gw_mip.back_roles")
    want = _role_ip4(ip)
    on = bool(on)
    addresses.collect(runner, net=False)
    v = machine()
    st0 = state.load()
    if local_of(st0, v, want) is None and (on or want not in (out_list(st0) or [])):     # REV-3315-b2 П-2: «нет» too
        raise _refuse("gw_mip.err.role_not_here", "address_not_here", back=back, ip=want,
                      known=", ".join(_ours4(st0, v)) or tr("gw_addresses.none"))
    pool = [] if on else [e for e in _pool(runner) if e.get("kind") in ("awg", "xray")]
    names = dict((e["id"], X.label(st0, e)) for e in pool)
    route = public_of(st0, v, v["src"]) if v and v.get("src") else ""
    plan_ = {"direct": None, "moved": [], "said": []}

    def mutate(new, sec):
        check_ver(new, want_ver)
        cur = out_list(new)
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        if on:
            if cur is None or want in cur:
                return                                  # «для выхода» already
            lst = sorted(cur + [want], key=_ipkey)
        else:
            base = cur if cur is not None else _ours4(new, v)
            if want not in base:
                return                                  # not «для выхода» already
            lst = [x for x in base if x != want]
            if not lst:
                raise _refuse("gw_mip.err.out_last", "out_last", back=back, ip=want)
            d = direct_of(new)
            moves_direct = d == want or (d is None and want == route)
            dep = [e["id"] for e in pool if not exit_v6(e) and
                   (exit_src(new, e["id"]) == want or (exit_src(new, e["id"]) is None and want == route))]
            if moves_direct or dep:
                ok_ = [x for x in lst if local_of(new, v, x) and route_ok(v, local_of(new, v, x)) is True]
                if not ok_ or not DEV_NAME.match((v or {}).get("dev") or ""):
                    raise _refuse("gw_mip.err.out_no_other", "out_no_other", back=back, ip=want)
                to = d if d in ok_ and d != want else ok_[0]
                items = []
                if moves_direct:
                    items.append(tr("gw_mip.lock.out_direct", to=to))
                if dep:
                    items.append(tr("gw_mip.lock.out_exits", to=to, names=", ".join(names.get(x, x) for x in dep)))
                items.append(tr("gw_mip.lock.out_own", route=route or "?"))
                plan_["said"] = _confirm(items, yes, tr("gw_mip.confirm.panel"))     # no CLI (решение 13)
                if moves_direct:
                    a["direct"] = to
                    plan_["direct"] = to
                recs = g["exits"]
                for eid in dep:
                    rec = recs.get(eid) if isinstance(recs.get(eid), dict) else {}
                    rec["src"] = to
                    recs[eid] = rec
                    plan_["moved"].append({"id": eid, "src": to})
        a["out"] = {IN_KEY: lst}
        g["addresses"] = a
    _, res = ops._change(runner, mutate)
    eps = {}
    for e in pool:
        if e.get("kind") == "awg" and _leg_index(e) is not None and leg_endpoint(_leg_index(e)):
            eps[e["id"]] = leg_endpoint(_leg_index(e))
    flushed = forget(runner, [eps[x["id"]] for x in plan_["moved"] if x["id"] in eps])
    st = state.load()
    util.log_tr("gw_mip.log.out", ips=", ".join(out_list(st) or []) or tr("gw_mip.log.every"))
    warns = plan_["said"] + list((res or {}).get("warnings") or [])
    text = tr("gw_mip.out.set_on" if on else "gw_mip.out.set_off", ip=want)
    if plan_["direct"] or plan_["moved"]:                # REV-3315-b2 П-3: «напрямую», the exits or both
        key = "gw_mip.out.moved" if plan_["direct"] and plan_["moved"] else \
            "gw_mip.out.moved_direct" if plan_["direct"] else "gw_mip.out.moved_exits"
        text += " " + tr(key, to=plan_["direct"] or plan_["moved"][0]["src"])
    return {"ok": True, "out": out_list(st), "direct": direct_of(st), "moved": plan_["moved"], "flushed": flushed,
            "warnings": warns, "text": text}


def reset_roles(runner, yes=False, want_ver=None):
    """F12а: «Сбросить выбор» (the way back of every refusal of the roles, PLAN-multi-ip-roles §6.4, §6.5 п. 7):
    ``out``, ``direct`` and ``give`` — None (as 3.3.7.14: the exits and «напрямую» from the address of the route, no
    IPv6 line); «Приём» (``in``/``in6``) and the addresses of the exits stay — they have ways back of their own. Never
    refused (but for ``ver``); an IPv6 given while Reality listens on it (F13 С-5: ``listen`` back to ``0.0.0.0`` —
    xray restarts) — asked as ``set_give6`` asks (``yes``; else 409 ``need: "confirm"``). -> {"ok", "warnings",
    "text"}."""
    from . import ops
    from kaskad.site import nginx
    said = []

    def mutate(new, sec):
        check_ver(new, want_ver)
        if addresses.give6_of(new) and _reality_on(new) and nginx.has_ipv6():
            said[:] = _confirm([tr("gw_mip.lock.give6_listen_off")], yes, tr("gw_mip.confirm.panel"))
        g = model.gw(new)
        a = g.get("addresses") if isinstance(g.get("addresses"), dict) else {}
        for k in ("out", "direct", "give"):
            a.pop(k, None)
        if a:
            g["addresses"] = a
        else:
            g.pop("addresses", None)
    _, res = ops._change(runner, mutate)
    util.log_tr("gw_mip.log.reset")
    return {"ok": True, "warnings": said + list((res or {}).get("warnings") or []), "text": tr("gw_mip.reset.done")}


def forget(runner, endpoints):
    """``conntrack -D -p udp -d E --dport P`` per Endpoint: the next packet of the leg makes a new connection with the
    new SNAT. No conntrack — nothing (keepalive keeps the old address until the record times out). -> how many
    Endpoints were asked (conntrack answers 1 when there was no flow to delete — not a failure)."""
    n = 0
    if not runner.which("conntrack"):
        return n
    for e, p in sorted(set(endpoints)):
        if _ip4(e) and 0 < int(p) < 65536:
            runner.run(["conntrack", "-D", "-p", "udp", "-d", e, "--dport", str(int(p))])
            n += 1
    return n


# ── the collector: the machine looked at, the plan followed ────────────────

WATCH_EVERY = 30                        # s between two looks when no address is chosen (each round when one is)


def watch(runner, st, now, last):
    """The light look of the collector: ``ip -o addr show`` + the default route (two commands); a difference from the
    cache — the cache anew (``addresses.machine`` without the network: the NAT pairs of the cache stay). -> True when
    the cache was written, False — the same, None — not looked at this round."""
    if not (in_list(st) or src_map(st) or direct_of(st)) and last is not None and 0 <= now - last < WATCH_EVERY:
        return None
    res = runner.run(["ip", "-o", "addr", "show"])
    if not res.ok:
        return False
    r = runner.run(["ip", "-4", "route", "get", addresses.PROBE_DST])
    found = addresses.parse_addrs(res.out)
    route = addresses.parse_route(r.out) if r.ok else None
    cache = addresses.load_cache()
    if cache is not None and cache.get("addrs") == found and cache.get("route") == route:
        return False
    addresses.machine(runner, net=False)
    return True


def file_has_rules():
    """kaskad.nft (the boot file) has a rule of this module — after a reboot the collector applies once."""
    text = util.read_text(paths.nft_file()) or ""
    return any(m in text for m in MARKERS)


def gone_state(mp):
    """(in gone, every chosen gone, {exit: ip gone}, «напрямую» not acting: ip | "") — the transitions are told in the
    journal."""
    p = (mp or {}).get("in") or {}
    return (tuple(p.get("gone") or ()), bool(p) and not p.get("addrs"), tuple(sorted((mp or {}).get("gone_src", {})
                                                                                    .items())),
            (mp or {}).get("gone_direct") or "")


def tell(prev, now_, names=None, log=None, st=None):
    """Journal lines of what changed between two ``gone_state`` (an address of the list or of an exit gone or back; an
    address taken off the list or off the exit by a person is not «back» — ``st`` says what is chosen now)."""
    log = log or util.log_tr
    if prev is None or prev == now_:
        return []
    out = []
    pg, pall, psrc = prev[:3]
    ng, nall, nsrc = now_[:3]
    pdir, ndir = (prev[3] if len(prev) > 3 else ""), (now_[3] if len(now_) > 3 else "")
    if nall and not pall:
        out.append(("gw_mip.log.in_all_gone", {"ips": ", ".join(ng)}))
    elif set(ng) - set(pg):
        out.append(("gw_mip.log.in_gone", {"ips": ", ".join(sorted(set(ng) - set(pg), key=_ipkey))}))
    chosen = in_list(st) if st is not None else None
    back = sorted((ip for ip in set(pg) - set(ng) if chosen is None and st is None or ip in (chosen or ())), key=_ipkey)
    if back:
        out.append(("gw_mip.log.in_back", {"ips": ", ".join(back)}))
    ps, ns = dict(psrc), dict(nsrc)
    for eid in sorted(set(ns) - set(ps)):
        out.append(("gw_mip.log.src_gone", {"name": (names or {}).get(eid) or eid, "ip": ns[eid]}))
    for eid in sorted(set(ps) - set(ns)):
        if st is not None and exit_src(st, eid) != ps[eid]:
            continue                                    # unset or changed by a person: not «back»
        out.append(("gw_mip.log.src_back", {"name": (names or {}).get(eid) or eid, "ip": ps[eid]}))
    if ndir and ndir != pdir:                           # F12а: «напрямую» of the choice stopped acting (§6.2)
        out.append(("gw_mip.log.direct_gone", {"ip": ndir}))
    elif pdir and not ndir and (st is None or direct_of(st) == pdir):
        out.append(("gw_mip.log.direct_back", {"ip": pdir}))     # back — not when a person changed the choice
    for key, kw in out:
        log(key, **kw)
    return out
