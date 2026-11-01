"""D-136 (B7 круг 2) — the main proof of R2 is behavioural: for hundreds of random states the REAL renderers draw what
the node would apply — the sets of nft (``net.plan``: gw_rd, gw_ruonly; ``rd_addresses``: the text of the table), the
config of xray (``xray.render``), the choice of dnsmasq (``rules.apply.kernel_clients``) — and the invariant is checked
on the result, not on the code that decides:

* without the permission ⚠РФ (no consent, no mark of an updated node, not «as before» on an updated node not yet
  migrated) NO device has a direct path to Russia — nowhere;
* with it, only an active device whose profile is ``ru_direct`` (by the device, its person or the node) and whose
  route is not «block» by choice (the last step of a group is no choice) has one — in xray and in the kernel alike
  (REV-s1-b7-2 №3, -sec-2 Н-2).

A direct path of xray is seen by the oracle's own reading of the config (REV-s1-b7-sec-2 Н-1), not by
``xray.ru_direct_paths``: ANY rule to an outbound that leaves from the node itself (``freedom`` without a mark) with a
``domain`` / ``ip`` matcher — the lists of Russia or anything else (``domain:ru``, ``geoip:ru``…). Without the
permission there is no such rule at all; with it, each one names users that may have it (the profile, active, not
«block») or whose route is «напрямую» anyway.

The permission, the profile, the person of a device (by id, else by a unique name), its login and its route here are an
oracle of their own, written from the rules of SPEC-stage1-rf-mark-builtin §3.5–3.6, SPEC §3.1 and D-019/D-135 — not
the functions of the code. The syntactic guard (tests/test_rules_rf_guard.py) is a hint
on obvious mistakes only.

States: people (on/off, a profile or none or a broken one, a route), devices (on/off, a profile, access SOCKS5 and
VLESS Reality, the person by id or by name, a route «pool», «block», «direct», an exit, a group, a broken group), the
default of the node (none, all_exit, ru_direct, broken), ``gen`` (1 — an updated node of 3.3.x, 2, none — a new node,
broken), the consent (the form of ``grant``, none, broken forms), ``rf_legacy`` (good,
broken), ``rf_gate``, a group whose exits are all down (its last step «block»: gw_ruonly)."""

import copy
import os
import random
import re
from unittest import mock

from kaskad import secrets, state
from kaskad.gw import dns, exits, model, net, xray
from kaskad.gw import registry
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.gw import policy as P
from kaskad.rules import apply as rapply, lists
from tests.gw.test_s1_b7 import B7Case
import os

from kaskad import util
from kaskad.errors import KaskadError
from tests.gw.test_ru_target import D1 as _D1, HOME as _HOME
from tests.test_rules_apply_ru_exit import _XrayCase as _RuXrayCase

GID = "g-aaaaaa"                      # the second group of random_groups
N = 400                                # random states per run (seeded: the same every run)
HY2_CRT = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw", "tunnel",
                            "ocserv.crt"), encoding="utf-8").read()
SEED = 136
RU, ALL = "ru_direct", "all_exit"
VIAS = ("panel", "scheme", "ssh", "cli", "mcp", "setup", "env")   # who writes a consent (rfgate.VIAS, checked)


def good_consent():
    return {"at": "2026-10-17T10:00:00Z", "via": "panel", "who": "owner", "orgs": ["Meta"], "lang": "ru"}


def oracle_allowed(st):
    """The permission of «Россия напрямую» by the spec, not by rfgate: a consent of the form ``grant`` writes; on an
    UPDATED node (gen exactly the integer 1) a mark ``rf_legacy`` from gen1, or — not migrated yet (no rf_gate) — the
    choice of ru_direct somewhere (it worked so before the update)."""
    g = st.get("gateway") or {}
    c = g.get("rf_consent")
    consent = isinstance(c, dict) and isinstance(c.get("at"), str) and bool(c["at"].strip()) \
        and c.get("via") in VIAS and isinstance(c.get("orgs"), list) and all(isinstance(o, str) for o in c["orgs"])
    gen = st.get("gen")
    updated = isinstance(gen, int) and not isinstance(gen, bool) and gen == 1
    lg = g.get("rf_legacy")
    legacy = updated and isinstance(lg, dict) and isinstance(lg.get("at"), str) and bool(lg["at"].strip()) \
        and lg.get("from") == "gen1"
    chosen = oracle_default(st) == RU or any(isinstance(o, dict) and o.get("rules") == RU
                                             for o in (g.get("users") or []) + (g.get("devices") or []))
    return consent or legacy or (updated and not g.get("rf_gate") and chosen)


def oracle_default(st):
    gen = st.get("gen")
    updated = isinstance(gen, int) and not isinstance(gen, bool) and gen == 1
    raw = (st.get("gateway") or {}).get("rules")
    if isinstance(raw, dict) and raw.get("default") in (RU, ALL):
        return raw["default"]
    return RU if updated else ALL


def oracle_person(st, d):
    """The person of a device: by id, else by a name that only one person has (SPEC §3.1); None — none or ambiguous."""
    users = (st.get("gateway") or {}).get("users") or []
    ref = d.get("user")
    if ref is None:
        return None
    by_id = [x for x in users if x.get("id") == ref]
    if by_id:
        return by_id[0]
    for same in (lambda a, b: a == b, lambda a, b: str(a).casefold() == str(b).casefold()):
        found = [x for x in users if x.get("name") is not None and same(x.get("name"), ref)]
        if found:
            return found[0] if len(found) == 1 else None
    return None


def oracle_profile(st, d):
    """device.rules -> person.rules -> the default of the node; a broken value (or inherit) falls through."""
    u = oracle_person(st, d) or {}
    for v in (d.get("rules"), u.get("rules")):
        if v in (RU, ALL):
            return v
    return oracle_default(st)


def oracle_active(st, d):
    u = oracle_person(st, d)
    return d.get("enabled", True) is not False and u is not None and u.get("enabled", True) is not False


def oracle_route(st, d):
    """(via, fail) of the route chosen for a device (SPEC §3.1): the device, its person, the default of the node; a
    route to a group the policy does not have is broken — the next level; none — the pool, fail «block»."""
    g = st.get("gateway") or {}
    groups = set(["main"]) | set(x.get("id") for x in ((g.get("exit_policy") or {}).get("groups") or [])
                                 if isinstance(x, dict) and x.get("role", "abroad") == "abroad")
    for obj in (d.get("route"), (oracle_person(st, d) or {}).get("route"), (g.get("routes") or {}).get("default")):
        via = obj.get("via") if isinstance(obj, dict) else obj if isinstance(obj, str) else None
        if not via:
            continue
        if via.startswith("group:") and via[len("group:"):] not in groups:
            continue
        return via, (obj.get("fail") if isinstance(obj, dict) else None) or "block"
    return "pool", "block"


def oracle_via(st, d):
    """The route chosen for a device (``oracle_route``). «block» here is the choice of the owner, never the last step
    of a group (that is its group's, not a route)."""
    return oracle_route(st, d)[0]


def oracle_group_fails_direct(st, via):
    """The last step of the group a route resolves to is «напрямую» (SPEC-stage1-exits §2.2): ``pool`` and
    ``group:main`` — «Основная», ``group:G`` — G, ``exit:E`` — the foreign group listing E (else «Основная»); no policy
    stored — «Основная» by default (its fail «block»)."""
    groups = [x for x in ((st.get("gateway") or {}).get("exit_policy") or {}).get("groups") or [] if isinstance(x, dict)]
    foreign = [x for x in groups if x.get("role", "abroad") == "abroad"]
    main = next((x for x in foreign if x.get("id") == "main"), {})
    grp = main
    if via.startswith("group:"):
        grp = next((x for x in foreign if x.get("id") == via[len("group:"):]), main)
    elif via.startswith("exit:"):
        eid = via[len("exit:"):]
        grp = next((x for x in foreign if any(eid in (t.get("exits") or []) for t in x.get("tiers") or []
                                              if isinstance(t, dict))), main)
    return grp.get("fail") == "direct"


def oracle_may_go_direct(st, d):
    """A device may leave from the node itself by its route (not by the lists of Russia): active, not «block» by
    choice, and its route «напрямую», or its fail «напрямую», or the last step of its group «напрямую»."""
    via, fail = oracle_route(st, d)
    return oracle_active(st, d) and via != "block" and (
        via == "direct" or fail == "direct" or oracle_group_fails_direct(st, via))


XRAY_ACCESS = ("socks5", "vless", "hy2")


def oracle_someone_direct(st, xray_side):
    """Some device of xray (``xray_side``: SOCKS5 / VLESS — its DNS is xray's own) or of the kernel (the other
    inbounds — the DNS is dnsmasq) may go out directly: only then may that DNS leave from the node (Р-Г9)."""
    for d in (st.get("gateway") or {}).get("devices") or []:
        acc = [k for k in d.get("access") or [] if (k in XRAY_ACCESS) == xray_side]
        if acc and oracle_may_go_direct(st, d):
            return True
    return False


NUMGEN_RE = re.compile(r"numgen random mod (\d+) map \{ ([^}]*) \}")


def oracle_numgen_cells(pre):
    """The marks of the numgen rules of gw_pre (its text, as nft gets it — the oracle's own reading), all rules."""
    out = []
    for text in pre or []:
        for m in NUMGEN_RE.finditer(text):
            out += [int(x.split(":")[1].strip(), 0) for x in m.group(2).split(",")]
    return out


def oracle_login(d):
    """The user of a device in xray (SOCKS5 and VLESS): its login, else its id."""
    return str(d.get("login") or d.get("id") or "")


def oracle_goes(st):
    """``goes_direct(user)`` of ``oracle_names_past_exits``: the xray user (the login of SOCKS5/VLESS, «<login>.h2» of
    Hysteria2 — ``oracle_users``) belongs to a device that may go directly. By the login alone a user of Hysteria2 was
    never «direct», and the oracle flagged its names that rightly resolve off the node (3.3.7.1 Д-1)."""
    users = oracle_users(st["gateway"]["devices"])
    return lambda u: u in users and oracle_may_go_direct(st, users[u])


def oracle_users(devs):
    """{xray user: device} — the login of SOCKS5 and VLESS, «<login>.h2» of Hysteria2 (SPEC-stage1-hy2-inbound §2: its
    own email, never the one of VLESS)."""
    out = {}
    for d in devs:
        out[oracle_login(d)] = d
        out[oracle_login(d) + ".h2"] = d
    return out


def oracle_direct(cfg):
    """{(inbound tag, user)} of every rule of ``cfg`` that sends what it matches (any ``domain`` / ``ip``) to an outbound
    leaving from the node itself — ``freedom`` without a mark (a freedom with ``sockopt.mark`` is a leg of an AWG exit) —
    or to a Russian exit («gr-…», any protocol: a home router is «Россия напрямую» for R2 too, SEC-s1-q2 С-1).
    None — a rule without inbounds / users (all of them). Read here, not by ``xray.ru_direct_paths`` (-sec-2 Н-1)."""
    own = set()
    for ob in cfg.get("outbounds") or []:
        so = (ob.get("streamSettings") or {}).get("sockopt") or {}
        if ob.get("protocol") == "freedom" and not so.get("mark") and not so.get("interface") or \
                str(ob.get("tag") or "").startswith("gr-"):
            own.add(ob.get("tag"))
    out = set()
    for r in (cfg.get("routing") or {}).get("rules") or []:
        if r.get("outboundTag") not in own:
            continue
        ins = r.get("inboundTag") or []
        eid = r["outboundTag"][3:] if str(r["outboundTag"]).startswith("gr-") else None
        if eid and ins and set(ins) <= {"tproxy-" + eid, "probe-" + eid} and not (r.get("domain") or r.get("ip")):
            continue                                 # the exit's own TPROXY and probe (the kernel's path: gw_rx)
        if r.get("domain") or r.get("ip") or not r.get("user") and not (ins and set(ins) <= set(DNS_INS)):
            # a list, or a rule for EVERY user of its inbounds (by a port, UDP, nothing — REV-337-final-sec П-1); the
            # DNS of xray and of dnsmasq is ``oracle_dns_route``'s
            for tag in ins or [None]:
                for user in r.get("user") or [None]:
                    out.add((tag, user))
    return out


def oracle_route_direct(cfg):
    """{(inbound tag, user)} of the rules of one user each to the node's own outbound without a list — the route of
    a device (or the last step of its group) «напрямую»: allowed without the permission, only for a device that may."""
    own = oracle_own(cfg)
    out = set()
    for r in (cfg.get("routing") or {}).get("rules") or []:
        if r.get("outboundTag") in own and r.get("user") and not (r.get("domain") or r.get("ip")):
            for tag in r.get("inboundTag") or [None]:
                for user in r["user"]:
                    out.add((tag, user))
    return out


POOL_DNS = "127.0.0.1#10853"            # dnsmasq's only upstream through the exits (xray dns-in); anything else leaves the node


def oracle_exit_hosts(cfg):
    """The hostnames xray must resolve before any leg is up — the servers of its outbounds (read from the config itself:
    ``vnext`` of VLESS/VMess, ``servers`` of Trojan/Shadowsocks, ``address`` of Hysteria2 — REV-s1-kt1a-2 П-2) and the
    host of the probe: the only names its DNS may ask the node's resolver."""
    hosts = set()
    for ob in cfg.get("outbounds") or []:
        st = ob.get("settings") or {}
        for v in (st.get("vnext") or []) + (st.get("servers") or []) + [st]:
            a = v.get("address") if isinstance(v, dict) else None
            if isinstance(a, str) and a:
                hosts.add(a.lower())
    from urllib.parse import urlsplit
    hosts.add((urlsplit(exits.PROBE_URL).hostname or "").lower())
    return hosts


def oracle_ru_dns(cfg):
    """The names xray's DNS resolves by the node itself, past the pool (REV-s1-kt1a-sec С-4: read in ``cfg["dns"]``,
    not by a function of the code): a server ``localhost`` / ``…+local`` for every name, or for names other than the
    hostnames of the exits and of the probe. [] — none (Р-Г9: with the exits down the DNS of the node is silent)."""
    out = []
    hosts = oracle_exit_hosts(cfg)
    for s in (cfg.get("dns") or {}).get("servers") or []:
        addr = s.get("address") if isinstance(s, dict) else s
        if not (str(addr) == "localhost" or "+local" in str(addr)):
            continue
        domains = s.get("domains") if isinstance(s, dict) else None
        if not domains:
            out.append("%s: every name" % addr)
            continue
        out.extend("%s: %s" % (addr, d) for d in domains
                   if not (str(d).startswith("full:") and str(d)[len("full:"):].lower() in hosts))
    return out


def oracle_own(cfg):
    """The tags of the outbounds that leave from the node itself: ``freedom`` without a mark or an interface (a freedom
    with ``sockopt.mark`` is a leg of an AWG exit)."""
    own = set()
    for ob in cfg.get("outbounds") or []:
        so = (ob.get("streamSettings") or {}).get("sockopt") or {}
        if ob.get("protocol") == "freedom" and not so.get("mark") and not so.get("interface"):
            own.add(ob.get("tag"))
    return own


DNS_INS = ("gw-dns-int", "dns-in")      # xray's own DNS and the pool DNS of dnsmasq (127.0.0.1#10853)


MATCHERS = ("domain", "ip", "port", "sourcePort", "network", "source", "sourceIP", "localIP", "localPort", "protocol",
            "attrs", "vlessRoute", "process")


def oracle_dns_route(cfg, direct_ok=False):
    """Where the DNS of xray and of dnsmasq goes by ROUTING (REV-s1-kt1a-2 З-1: «gw-dns-int → direct» moves every name
    past the pool with the servers unchanged). Every rule that may take a query of ``gw-dns-int`` / ``dns-in`` counts —
    without inbounds or with that one, no user (a query has none), down to the first of them without a matcher (the
    rest is never reached; REV-337 П-1: ``{"port": "53", "outboundTag": "direct"}`` above is one), none — the first
    outbound. With an exit in the config each goes to an exit, a balancer of exits only (selector — the tags of the
    exits, its fallback not the node) or a blackhole — never the node's own outbound. Without exits drawn (none, all
    off, all off by the quota — REV-337-final-sec З-1, Д-2) only ``gw-dns-int`` may go to the node and only with
    ``direct_ok`` (``oracle_someone_direct``: a user of xray goes directly); else a blackhole. [] — fine, else the faults."""
    own = oracle_own(cfg)
    outs = [ob.get("tag") for ob in cfg.get("outbounds") or []]
    holes = set(ob.get("tag") for ob in cfg.get("outbounds") or [] if ob.get("protocol") == "blackhole")
    # the exits of the POOL: foreign ones only (T15 Q4) — a Russian exit (gr-, a home router) carries no DNS of the node
    # nor of xray (SPEC-groups §5): a DNS rule to «gr-» is a fault, and «gr-» alone is «no exits» for the DNS
    exits_ = [t for t in outs if t not in own and str(t).startswith("gx-")]
    bals = dict((b.get("tag"), b) for b in (cfg.get("routing") or {}).get("balancers") or [])
    rules = (cfg.get("routing") or {}).get("rules") or []
    bad = []
    # REV-337-last-sec П-1: xray's own DNS is routed by ITS tag (cfg["dns"]["tag"]), not by the name the code uses —
    # no tag, and its queries meet no rule of an inbound (the first outbound, whatever it is)
    own_dns = (cfg.get("dns") or {}).get("tag") if isinstance(cfg.get("dns"), dict) else None
    if cfg.get("dns") is not None and not own_dns:
        bad.append("dns: no tag — xray's own DNS is routed by no rule")
    for tag in ([own_dns] if own_dns else []) + ["dns-in"]:
        if tag == "dns-in" and not any(i.get("tag") == "dns-in" for i in cfg.get("inbounds") or []):
            continue
        mine = []
        for r in rules:
            ins = r.get("inboundTag") or []
            if r.get("user") or ins and tag not in ins:
                continue
            mine.append(r)
            if not any(r.get(k) for k in MATCHERS):
                break
        node_ok = not exits_ and tag == own_dns and direct_ok
        if not mine or any(r.get(k) for k in MATCHERS for r in mine[-1:]):
            if exits_:                                   # with exits its own rule to them, never «whatever comes first»
                bad.append("%s: no rule (the first outbound %s)" % (tag, outs[:1]))
            mine.append({"outboundTag": outs[0] if outs else None, "_first": True})     # nothing took it: xray's first
        for r in mine:                                   # every rule that may take it: none to the node
            if r.get("outboundTag") is not None or r.get("_first"):
                t = r.get("outboundTag")
                if t in own and not node_ok or t not in own and t not in exits_ and t not in holes:
                    bad.append("%s -> %s%s" % (tag, t, " (no rule: the first outbound)" if r.get("_first") else ""))
                continue
            b = bals.get(r.get("balancerTag"))
            if not b:
                bad.append("%s -> no balancer %s" % (tag, r.get("balancerTag")))
                continue
            sel = [t for t in outs if any(str(t).startswith(x) for x in b.get("selector") or [])]
            if not sel or set(sel) - set(exits_) or b.get("fallbackTag") in own or (
                    b.get("fallbackTag") not in (None, "block") and b.get("fallbackTag") not in exits_):
                bad.append("%s -> %s %s (fallback %s)" % (tag, b.get("tag"), sel, b.get("fallbackTag")))
    return bad


def oracle_hot_users(cfg):
    """{(inbound tag, user)} of the users of the inbounds of xray with users (VLESS ``clients``, SOCKS5 ``accounts``)."""
    out = set()
    for ib in cfg.get("inbounds") or []:
        sett = ib.get("settings") or {}
        for c in sett.get("clients") or []:
            out.add((ib.get("tag"), str(c.get("email") or "")))
        for c in sett.get("accounts") or []:
            out.add((ib.get("tag"), str(c.get("user") or "")))
    return out


def oracle_names_checked(cfg, goes_direct):
    """{inbound tag} whose users the oracle ``names_past`` checks: xray's own DNS routed to the node and a user of that
    inbound who does not go directly (REV-338 З-2: the counters of VLESS and SOCKS5 apart)."""
    own = oracle_own(cfg)
    rules = (cfg.get("routing") or {}).get("rules") or []
    tag = (cfg.get("dns") or {}).get("tag") if isinstance(cfg.get("dns"), dict) else None
    if not any(tag in (r.get("inboundTag") or []) and r.get("outboundTag") in own and not r.get("user")
               for r in rules if tag):
        return set()
    return set(ib for ib, user in oracle_hot_users(cfg) if not goes_direct(user))


def oracle_names_past_exits(cfg, goes_direct):
    """REV-337-last-sec Д-1: xray's own DNS routed to the node (no exit drawn, someone «напрямую») — then a request of
    a user who does NOT go directly (``goes_direct(user)`` False) must end in a blackhole before the first rule with
    IPs it meets: IPOnDemand resolves the name of the request there, through that DNS, off the node past the exits.
    [] — fine, else the users."""
    holes = set(ob.get("tag") for ob in cfg.get("outbounds") or [] if ob.get("protocol") == "blackhole")
    rules = (cfg.get("routing") or {}).get("rules") or []
    if not oracle_names_checked(cfg, goes_direct):
        return []
    bad = []
    for ib, user in sorted(oracle_hot_users(cfg)):
        if goes_direct(user):
            continue
        for r in rules:
            ins, users = r.get("inboundTag") or [], r.get("user") or []
            if ins and ib not in ins or users and user not in users:
                continue
            if r.get("ip"):
                bad.append("%s %s: its names resolved off the node (%s)" % (ib, user, r.get("outboundTag")))
                break
            if not any(r.get(k) for k in MATCHERS):
                if r.get("outboundTag") not in holes:
                    bad.append("%s %s: -> %s with the DNS of the node" % (ib, user, r.get("outboundTag")))
                break
    return bad


def oracle_dnsmasq_ru(text, direct_ok=False):
    """The lines of dnsmasq's config (the text written on the disk) that send some names past the pool —
    ``server=/<names>/<upstream>`` with an upstream other than the pool (``#``: the default servers); a shared
    ``server=<upstream>`` other than the pool when no device of the kernel may go directly (``direct_ok`` —
    ``oracle_someone_direct``; with one it is the backlog REV-s1-kt1a-sec Д-1); no ``no-resolv`` (the resolver of the
    node for every name — REV-337 П-1)."""
    out = []
    lines = text.splitlines()
    if "no-resolv" not in lines:
        out.append("no no-resolv")
    for ln in lines:
        if ln.startswith("server=/"):
            up = ln.rsplit("/", 1)[1]
            if up != POOL_DNS:
                out.append(ln)
        elif ln.startswith("server=") and ln[len("server="):] != POOL_DNS and not direct_ok:
            out.append(ln)
    return out


DNAT_TO = re.compile(r"\bdnat to [0-9.]+:(\d+)\b")
MARK_IS = re.compile(r"\bmeta mark (0x[0-9a-f]+)\b(?! set)")


def oracle_dns_dnat(rules, direct_port):
    """The chain gw_dns as nft gets it (its text — the oracle's own reading; 3.3.7.2, SEC-3371 Б-2): a query DNATed to
    the dnsmasq with the direct upstream (``direct_port``) only by a rule that matches the mark 0x201 — and that mark
    only as the map gw_route gave it (the rule setting the mark from the map, nothing else setting it before). Any other
    way a name of a person «всё через выход» meets 1.1.1.1. [] — fine, else the faults."""
    out = []
    marked_by_map = False
    for text in rules or []:
        m = DNAT_TO.search(text)
        if m and int(m.group(1)) == direct_port:
            got = MARK_IS.findall(text)
            if got != ["0x%x" % exits.MARK_DIRECT]:
                out.append("gw_dns: to the direct DNS without the mark 0x201: %s" % text)
            elif not marked_by_map:
                out.append("gw_dns: the mark 0x201 not from gw_route: %s" % text)
        if "meta mark set" in text and not m:
            marked_by_map = bool(re.search(r"\bmeta mark set ip saddr map @%s$" % net.MAP, text)) \
                and text.count("meta mark set") == 1
    return out


def oracle_dns_ports_closed(pre, dns_ip, ports):
    """gw_pre as nft gets it (SEC-3372 З-1): a client asking a dnsmasq of the gateway on its own port (``ports``: 5354,
    5364 — kaskad-gw-dnsdirect is the direct upstream) past port 53 is dropped — a rule naming the address and every
    port, after the clients are told from the node (``ip saddr != @gw_nets return``) and before the first rule that
    lets a client reach the node (``fib daddr type local``). [] — fine."""
    start = pre.index("ip saddr != @gw_nets return") if "ip saddr != @gw_nets return" in pre else None
    local = [n for n, r in enumerate(pre) if r.startswith("fib daddr type local") or " fib daddr type local " in r
             and not r.endswith(" drop")]
    for n, r in enumerate(pre):
        m = re.match(r"^ip daddr ([0-9.]+) meta l4proto \{ tcp, udp \} th dport \{ ([0-9, ]+) \} drop$", r)
        if m and m.group(1) == dns_ip and set(int(x) for x in m.group(2).split(", ")) >= set(ports) \
                and start is not None and start < n and (not local or n < local[0]):
            return []
    return ["gw_pre: the ports %s of the gateway's dnsmasq open to the clients past port 53" % (sorted(ports),)]


class SocksWithAccounts(DummyInbound):
    """SOCKS5 of the invariant with the logins of its devices in ``settings.accounts``, as the real adapter has them
    (gw/inbounds/socks5.py): the stub of socks-in had none, so ``names_past`` never saw a user of SOCKS5 (REV-338 З-2)."""
    def xray_inbounds(self, st, sec):
        out = super().xray_inbounds(st, sec)
        for ib in out:
            ib["settings"] = dict(ib.get("settings") or {}, accounts=[
                {"user": d.get("login") or d["id"], "pass": "x"} for d in self.devices(st)])
        return out


class InvariantTest(B7Case):
    def setUp(self):
        super().setUp()
        self.socks_world()
        socks = registry.get("socks5")
        registry.register(SocksWithAccounts(kind="socks5", proto="tcp", default_port=1080, ifaces=[],
                                            xray_inbound=socks._xray_inbound))
        # Hysteria2 next to them (T12: its users «<login>.h2» go by the same rules); its self-signed pair given, no openssl
        secrets.set("inbound.hy2.self.crt", HY2_CRT)
        secrets.set("inbound.hy2.self.key", "-----BEGIN PRIVATE KEY-----\nTESTKEY\n-----END PRIVATE KEY-----\n")
        with state.transaction() as st:                   # VLESS Reality next to SOCKS5 (REV-s1-b7-2 придирка 3)
            st["gateway"]["inbounds"]["vless"] = {"enabled": True, "port": 8443, "sni": "www.microsoft.com",
                                                  "dest": "www.microsoft.com:443"}
            st["gateway"]["inbounds"]["hy2"] = {"enabled": True, "port": 8443}
            for d in st["gateway"]["devices"]:
                d["access"].append("vless")
                d["access"].append("hy2")
        self.ok("gw", "apply")
        self.base = state.load()
        self.sec = secrets.load()
        self.pool = exits.pool(self.w.runner)
        self.ids = [e["id"] for e in self.pool]

    def random_state(self, rnd):
        st = copy.deepcopy(self.base)
        g = st["gateway"]
        prof = (None, "inherit", RU, ALL, "RU_DIRECT", 7)
        vias = ["pool", "block", "direct", "group:main", "group:g-gone"] + ["exit:" + i for i in self.ids]
        rnd.choice((lambda: st.__setitem__("gen", 1), lambda: st.__setitem__("gen", 2), lambda: st.pop("gen", None),
                    lambda: st.__setitem__("gen", "1"), lambda: st.__setitem__("gen", True)))()
        c = rnd.choice(("good", "good", "none", "empty", "no_at", "bad_via", "bad_orgs"))
        g.pop("rf_consent", None)
        if c != "none":
            g["rf_consent"] = {"good": good_consent(), "empty": {}, "no_at": dict(good_consent(), at=None),
                               "bad_via": dict(good_consent(), via="web"),
                               "bad_orgs": dict(good_consent(), orgs="Meta")}[c]
        lg = rnd.choice((None, {"at": "2026-10-01T00:00:00Z", "from": "gen1"}, {"at": "", "from": "gen1"},
                         {"at": "x", "from": "gen2"}))
        g.pop("rf_legacy", None)
        if lg is not None:
            g["rf_legacy"] = lg
        g.pop("rf_gate", None)
        if rnd.random() < 0.5:
            g["rf_gate"] = {"at": "2026-10-01T00:00:00Z"}
        d = rnd.choice((None, ALL, RU, "broken"))
        g["rules"] = dict(g.get("rules") or {})
        g["rules"].pop("default", None)
        if d is not None:
            g["rules"]["default"] = d
        if rnd.random() < 0.3:
            g.pop("rules")
        for o in g["users"] + g["devices"]:
            o["enabled"] = rnd.random() > 0.2
            v = rnd.choice(prof)
            o.pop("rules", None)
            if v is not None:
                o["rules"] = v
            r = rnd.choice([None] + vias)
            o.pop("route", None)
            if r is not None:
                o["route"] = {"via": r, "fail": rnd.choice(("block", "direct"))}
        if rnd.random() < 0.5:
            self.random_groups(rnd, g)
        if rnd.random() < 0.25:                          # the owner turned exits off (REV-337-final-sec Д-2): all or some
            off = list(self.ids) if rnd.random() < 0.6 else rnd.sample(self.ids, rnd.randint(1, len(self.ids)))
            for eid in off:
                g.setdefault("exits", {}).setdefault(eid, {})["enabled"] = False
        names = dict((u["id"], u.get("name")) for u in g["users"])
        for dv in g["devices"]:
            dv["access"] = [k for k in ("wg", "awg2", "socks5", "vless", "hy2") if k in dv.get("ips", {}) or k in XRAY_ACCESS
                            if rnd.random() > 0.25] or [rnd.choice(XRAY_ACCESS)]
            if rnd.random() < 0.15 and names.get(dv.get("user")):
                dv["user"] = names[dv["user"]]           # the person by name, as a hand edit or an import has it
        return st

    def hand_state(self):
        """One state set by hand (REV-3371 П-1), not drawn: the plants of the fail-block need «no exit turned on,
        someone «напрямую», someone else not» WITH the permission — among the random ones that came once in 120, and any
        shift of the random stream hid it. No exit turned on; «Дача» «напрямую»; «Мама» «всё через выход», the consent
        ⚠РФ given; her devices on VLESS, SOCKS5 and Hysteria2 (and WireGuard), their own route none."""
        st = copy.deepcopy(self.base)
        g = st["gateway"]
        g["rf_consent"] = good_consent()
        g["rules"] = {"default": ALL}
        g["routes"] = {"default": {"via": "pool", "fail": "block"}}
        for eid in self.ids:
            g.setdefault("exits", {}).setdefault(eid, {})["enabled"] = False
        g.pop("exit_policy", None)
        for u in g["users"]:
            u["enabled"] = True
            u.pop("route", None)
            u.pop("rules", None)
            if u["id"] == "u-00000002":
                u["route"] = {"via": "direct", "fail": "direct"}
            else:
                u["rules"] = ALL
                u["route"] = {"via": "pool", "fail": "block"}
        for d in g["devices"]:
            d["enabled"] = True
            d.pop("route", None)
            d.pop("rules", None)
            if d["user"] == "u-00000001":
                d["access"] = ["wg", "socks5", "vless", "hy2"]
        return st

    def random_groups(self, rnd, g):
        """The policy of the exits as the panel writes it since T7 (проверка безопасности T7, П-3): a second foreign
        group, a random ``fail`` of both, the exits split between them, the bot's proxy on it, routes ``group:g-…`` of
        people, devices and the node."""
        doc = P.default_doc(rnd.choice(("compat", "standard")))
        main = doc["groups"][0]
        main["fail"] = rnd.choice(("block", "direct"))
        main["per_connection"] = rnd.random() < 0.4         # «по соединениям» (T8 turns it on; КТ1 Н7, S26)
        other = P.default_group(GID, "abroad", rnd.choice(("standard", "compat")), name="Стрим")
        other["fail"] = rnd.choice(("block", "direct"))
        ids = list(self.ids)
        rnd.shuffle(ids)
        k = rnd.randint(0, len(ids))
        main["tiers"][0]["exits"] = ids[:k]
        other["tiers"][0]["exits"] = ids[k:]
        doc["groups"].append(other)
        if rnd.random() < 0.3:
            doc["tg_proxy_group"] = GID
        g["exit_policy"] = doc
        for o in g["users"] + g["devices"]:
            if rnd.random() < 0.35:
                o["route"] = {"via": "group:" + GID, "fail": rnd.choice(("block", "direct"))}
        if rnd.random() < 0.3:
            g.setdefault("routes", {})["default"] = {"via": "group:" + GID, "fail": rnd.choice(("block", "direct"))}

    @staticmethod
    def quota_removed(dec, ids):
        """The decision with ``ids`` off by the quota «remove» (exits.decision reads it so from the snapshot)."""
        return dict(dec, quota_off=list(ids), states=dict(dec.get("states") or {}, **dict((i, "off") for i in ids)))

    def drawn(self, st, rnd):
        """What the node would apply: (xray paths, raw gw_rd, raw gw_ruonly, final gw_rd, final gw_ruonly, dnsmasq)."""
        alive = None if rnd.random() < 0.6 else set()            # a group whose exits are all down: its last step
        dec = exits.decision(st, pool_exits=self.pool, **({} if alive is None else {"alive": alive}))
        q = rnd.random()                         # off by the quota «remove» (REV-337-final-sec З-1): all, some, none
        if q < 0.25:                             # as the snapshot has them: «off» with the reason «quota» (exits.quota_off)
            dec = self.quota_removed(dec, list(self.ids) if q < 0.15 else rnd.sample(self.ids,
                                                                                     rnd.randint(1, len(self.ids))))
        cfg = xray.render(st, self.sec, self.pool, (), dec)
        p = net.plan(st, self.w.runner, decision=dec)

        goes = oracle_goes(st)
        usable = bool(xray._usable(st, self.pool))
        direct = exits.direct_needed(st, dec.get("policy"))
        text, _ = dns.text_of(st, via_pool=usable, direct=direct)                      # as gw/ops.py calls dns.apply
        dtext = dns.direct_text_of(st, usable) if direct else ""          # 3.3.7.2: the second dnsmasq — apply alike
        gw_dns = dict(net.rules(st, p))["gw_dns"]
        drawn_exits = [o["tag"] for o in cfg.get("outbounds") or [] if str(o.get("tag")).startswith(("gx-", "gr-"))]
        dns_int = [r for r in cfg["routing"]["rules"] if "gw-dns-int" in (r.get("inboundTag") or [])]
        return {"xray": xray.ru_direct_paths(cfg), "direct": oracle_direct(cfg), "ru_dns": oracle_ru_dns(cfg),
                "route_direct": oracle_route_direct(cfg),
                "dns_route": oracle_dns_route(cfg, oracle_someone_direct(st, True)),
                "names_past": oracle_names_past_exits(cfg, goes), "names_checked": oracle_names_checked(cfg, goes),
                "no_exits": not drawn_exits, "dns_int": (dns_int[0].get("outboundTag") if dns_int else None),
                # 3.3.7.2 (SEC-3371 Б-2): kaskad-dns — the pool only, with or without a device «напрямую»; the direct
                # upstream only in kaskad-gw-dnsdirect, and gw_dns sends there by the mark 0x201 of gw_route alone
                "dnsmasq_ru": oracle_dnsmasq_ru(text, False)
                + (oracle_dnsmasq_ru(dtext, oracle_someone_direct(st, False)) if dtext else [])
                + oracle_dns_dnat(gw_dns, dns.PORT_DIRECT)
                + oracle_dns_ports_closed(dict(net.rules(st, p))["gw_pre"], net.addr.dns_ip(st),
                                          (dns.PORT, dns.PORT_DIRECT)),
                "dns_split": bool(dtext) and any(m != exits.MARK_DIRECT for m in p["elems"].values()),
                "rd": list(p["rd"] or []), "ruonly": list(p["ruonly"] or []),
                "rx": dict(p.get("rx") or {}), "rx_text": net.rx_elements(st, p),     # Q2: gw_rx
                "rd_text": net.rd_addresses(st, p), "ruonly_text": net.ruonly_addresses(st, p),
                "dns": rapply.kernel_clients(st), "targets": exits.targets(st, dec),
                # the kernel (SEC-s1-kt2a З-1): the cells of numgen as the full apply draws them, the marks of gw_route
                "numgen": oracle_numgen_cells(dict(net.rules(st, p))["gw_pre"]), "elems": dict(p["elems"])}

    def violations(self, st, got):
        """What breaks R2 in what the node would apply for ``st`` ([] — nothing)."""
        bad = []
        if not got["xray"] <= got["direct"]:                       # the oracle reads at least what the code does
            bad.append("xray paths unseen by the oracle: %s" % (got["xray"] - got["direct"]))
        # Р-Г9 (REV-s1-kt1a-2 З-1): the DNS of the node is silent with or without the permission — no name past the pool
        # by the node's resolver (xray: only the servers of the exits and the probe; dnsmasq: only the pool) and the
        # DNS of xray and dnsmasq routed to the exits. «Россия напрямую» lets Russia go directly — not the names of
        # everyone on that path (a foreign name of a ru_direct person goes through the exit), so no name at all
        for k in ("ru_dns", "dnsmasq_ru", "dns_route", "names_past"):
            if got[k]:
                bad.append("DNS past the pool — %s: %s" % (k, got[k][:3]))
        by_login = oracle_users(st["gateway"]["devices"])
        # the kernel (SEC-s1-kt2a З-1), with or without the permission: numgen spreads over the exits only — 0x200
        # (gw-vless) and the marks of AmneziaWG legs, never 0x201 (out directly), 0x202, 0, 0x2fe, 0x2ff; 0x201 in
        # gw_route only for a device whose route lets it go out directly («напрямую», its fail or its group's last step)
        legs = set(e["mark"] for e in self.pool if not exits.is_xray(e) and e.get("mark"))
        for c in got.get("numgen", ()):
            if c != exits.MARK_XRAY and c not in legs:
                bad.append("numgen cell %s: not an exit" % hex(c))
        by_ip = dict((ip, d) for d in st["gateway"]["devices"] for ip in (d.get("ips") or {}).values())
        for ip, m in sorted((got.get("elems") or {}).items()):
            if m == exits.MARK_DIRECT and (by_ip.get(ip) is None or not oracle_may_go_direct(st, by_ip[ip])):
                bad.append("gw_route %s: 0x201 without «напрямую» (route %s)" % (
                    ip, oracle_route(st, by_ip[ip]) if by_ip.get(ip) else None))
        for tag, user in sorted(got["route_direct"], key=str):    # a route «напрямую»: with or without the permission
            d = by_login.get(user)
            if d is None or not oracle_may_go_direct(st, d):
                bad.append("xray %s: «напрямую» by its route, route %s, active %s" % (
                    user, oracle_route(st, d) if d else None, oracle_active(st, d) if d else None))
        if not oracle_allowed(st):
            for k in ("direct", "rd", "ruonly", "rd_text", "ruonly_text", "dns", "rx", "rx_text"):
                if got[k]:
                    bad.append("no permission — %s: %s" % (k, got[k]))
            return bad
        devs = st["gateway"]["devices"]
        login = oracle_users(devs)

        def may(d):
            return oracle_profile(st, d) == RU and oracle_active(st, d) and oracle_via(st, d) != "block"
        for tag, user in sorted(got["direct"], key=str):
            d = login.get(user)
            if d is None:
                bad.append("xray %s: a direct rule of no device (%s)" % (user, tag))
            elif not may(d) and oracle_via(st, d) != "direct":
                bad.append("xray %s: profile %s, active %s, route %s" % (
                    user, oracle_profile(st, d), oracle_active(st, d), oracle_via(st, d)))
        ips = dict((ip, d) for d in devs for ip in (d.get("ips") or {}).values())
        for ip in got["rd"] + got["ruonly"] + sorted(got.get("rx") or {}):
            if not may(ips[ip]):
                d = ips[ip]
                bad.append("gw_rd %s: profile %s, active %s, route %s" % (
                    ip, oracle_profile(st, d), oracle_active(st, d), oracle_via(st, d)))
        return bad

    def test_no_direct_russia_without_the_permission(self):
        rnd = random.Random(SEED)
        seen = {"allowed_direct": 0, "denied": 0, "ruonly": 0, "vless": 0, "hy2": 0, "block": 0, "by_name": 0, "group": 0,
                "per_conn": 0, "no_exits_block": 0, "no_exits_direct": 0, "numgen": 0, "elem_direct": 0, "dns_split": 0,
                "names_closed_vless": 0, "names_closed_socks": 0, "names_closed_hy2": 0}
        for i in range(N):
            st = self.random_state(rnd)
            got = self.drawn(st, rnd)
            where = "state %d: %s (gen %r)" % (i, {k: st["gateway"].get(k) for k in ("rf_consent", "rf_legacy",
                                                                                    "rf_gate", "rules")}, st.get("gen"))
            self.assertEqual(self.violations(st, got), [], where)
            seen["numgen"] += bool(got["numgen"])
            seen["elem_direct"] += exits.MARK_DIRECT in got["elems"].values()
            seen["dns_split"] += got["dns_split"]      # the second dnsmasq and a device of the kernel not «напрямую»
            if got["no_exits"]:          # REV-337-final-sec З-1, Д-2: no exit drawn — the DNS of xray by its users
                seen["no_exits_" + ("direct" if got["dns_int"] == "direct" else "block")] += 1
                # Д-1: the DNS goes directly and someone of xray does not — the names of that one stay closed; the
                # oracle checked a user of VLESS and of SOCKS5 each (REV-338 З-2: SOCKS5 was never seen)
                if got["dns_int"] == "direct":
                    seen["names_closed_vless"] += "gw-vless" in got["names_checked"]
                    seen["names_closed_socks"] += "socks-in" in got["names_checked"]
                    seen["names_closed_hy2"] += "gw-hy2" in got["names_checked"]        # 3.3.7.1 Д-1
            if not oracle_allowed(st):
                seen["denied"] += 1
                continue
            seen["allowed_direct"] += bool(got["xray"] or got["rd"])
            seen["per_conn"] += bool(((st["gateway"].get("exit_policy") or {}).get("groups") or [{}])[0]
                                     .get("per_connection") and (got["xray"] or got["rd"]))
            seen["ruonly"] += bool(got["ruonly"])
            seen["vless"] += any(t == "gw-vless" for t, _ in got["xray"])
            seen["hy2"] += any(t == "gw-hy2" for t, _ in got["xray"])
            seen["block"] += any(oracle_via(st, d) == "block" and oracle_profile(st, d) == RU and oracle_active(st, d)
                                 for d in st["gateway"]["devices"])
            seen["by_name"] += any(not str(d.get("user", "")).startswith("u-") for d in st["gateway"]["devices"])
            seen["group"] += any(((d.get("route") or {}).get("via") == "group:" + GID) and oracle_profile(st, d) == RU
                                 for d in st["gateway"]["devices"])
        # not vacuous: both sides are reached, a direct path is drawn with the permission (VLESS too), the last step,
        # a device on ru_direct whose route is «block», a person named by name
        self.assertGreater(seen["denied"], N // 5, seen)
        self.assertGreater(seen["allowed_direct"], N // 10, seen)
        for k in ("ruonly", "vless", "hy2", "block", "by_name", "group", "per_conn", "no_exits_block", "no_exits_direct",
                  "numgen", "elem_direct", "dns_split", "names_closed_vless", "names_closed_socks", "names_closed_hy2"):
            self.assertGreater(seen[k], 0, (k, seen))

    def test_the_oracle_of_the_permission_is_the_node_s(self):
        # the oracle and rfgate agree — a change of either is seen (the spec moved: change both, and D-136)
        from kaskad.rules import rfgate
        self.assertEqual(tuple(sorted(rfgate.VIAS)), tuple(sorted(VIAS)))
        rnd = random.Random(SEED + 1)
        for i in range(N):
            st = self.random_state(rnd)
            self.assertEqual(bool(rfgate.allowed(st)), oracle_allowed(st), "state %d" % i)

    def leaks(self, patch, seed, allowed=False, n=60):
        """States (of ``n``) whose drawing breaks R2 with ``patch`` — among those without the permission, or with it."""
        rnd = random.Random(seed)
        caught = 0
        with patch:
            for _ in range(n):
                st = self.random_state(rnd)
                if oracle_allowed(st) == allowed:
                    caught += bool(self.violations(st, self.drawn(st, rnd)))
        return caught

    def test_dns_of_the_kernel_past_the_exits_is_caught(self):
        """3.3.7.2 (SEC-3371 Б-2, REV-s1-kt1a-sec Д-1): the forms of «the names of a device of the kernel «всё через
        выход» meet 1.1.1.1 because someone else goes directly» are red here — the direct upstream back in kaskad-dns
        (the code before), the direct dnsmasq for every client, the mark of the rule not from gw_route."""
        real_text, real_rules = dns.text_of, net.dns_dnat_rules

        def shared(st, via_pool, direct=None, filter_aaaa=True, nftset=True):     # the code before 3.3.7.2
            rl = rapply.dnsmasq_lines(st, nftset=nftset)
            return dns.conf_text(st, via_pool, direct, filter_aaaa, rl), rl

        def all_direct(st):                       # every client to the dnsmasq with 1.1.1.1
            return [r.replace(":%d" % dns.PORT, ":%d" % dns.PORT_DIRECT) for r in real_rules(st)]

        def no_mark(st):                          # the rule of the direct dnsmasq without its mark
            return [re.sub(r"meta mark 0x201 ", "", r) for r in real_rules(st)]

        def own_mark(st):                         # 0x201 put by the chain itself, not by gw_route
            rs = real_rules(st)
            return [rs[0].replace("meta mark set ip saddr map @%s" % net.MAP, "meta mark set 0x201")] + rs[1:]
        real_net_rules = net.rules

        def open_ports(st, p=None, spoof=None):   # SEC-3372 З-1: 10.77.0.1:5364 asked past port 53
            return [(c, [r for r in rs if " th dport { " not in r] if c == "gw_pre" else rs)
                    for c, rs in real_net_rules(st, p, spoof)]
        for plant in (mock.patch.object(dns, "text_of", shared), mock.patch.object(net, "dns_dnat_rules", all_direct),
                      mock.patch.object(net, "rules", open_ports),
                      mock.patch.object(net, "dns_dnat_rules", no_mark),
                      mock.patch.object(net, "dns_dnat_rules", own_mark)):
            self.assertGreater(self.leaks(plant, SEED + 11, allowed=False) + self.leaks(plant, SEED + 12, allowed=True),
                               0, plant)
        self.assertEqual(self.leaks(mock.patch.object(dns, "text_of", real_text), SEED + 11), 0)

    def test_a_planted_leak_is_caught(self):
        # the invariant is not blind: the forms the syntactic guard misses (REV-s1-b7 №3, -sec Н-1) are red here by
        # what they draw — the consent forgotten (read past ``ru_ok``), a rule «direct» built past ``rules.apply``
        forgot = mock.patch.object(rapply, "_why", lambda st, is_ready=None, v=None: None)
        self.assertGreater(self.leaks(forgot, SEED + 2), 0)

        real = rapply.xray_rules

        def emit(st, *a, **kw):                    # a rule «direct» by the lists built past rules.apply's checks
            return real(st, *a, **kw) + [{"type": "field", "inboundTag": ["socks-in"], "user": ["telefon-0001"],
                                          "domain": ["ext:%s:category-ru" % rapply.GEOSITE_ASSET],
                                          "outboundTag": "direct"}]
        self.assertGreater(self.leaks(mock.patch.object(rapply, "xray_rules", emit), SEED + 3), 0)

        def plain(st, *a, **kw):                   # -sec-2 Н-1 (L8): Russia directly by a plain matcher, no list at all
            users = sorted(set(oracle_login(d) for d in st["gateway"]["devices"]))
            return real(st, *a, **kw) + [{"type": "field", "inboundTag": ["socks-in"], "user": users,
                                          "domain": ["domain:ru", "domain:xn--p1ai"], "outboundTag": "direct"}]
        self.assertGreater(self.leaks(mock.patch.object(rapply, "xray_rules", plain), SEED + 4), 0)

    def test_a_planted_dns_leak_is_caught(self):
        # REV-s1-kt1a-sec С-4: the names of Russia resolved by the node past the pool — read in the configs written,
        # red without the permission and with it (someone on that path is not ru_direct), xray and dnsmasq alike
        ext = "ext:%s:category-ru" % rapply.GEOSITE_ASSET
        real_dns = xray.dns_config

        def xray_ru(usable):                      # опыт A: the direct group by the node's resolver for every user
            c = real_dns(usable)
            c["servers"].insert(0, {"address": "localhost", "domains": [ext], "skipFallback": True})
            return c

        def xray_full(usable):                    # a plain name of Russia as if it were an exit's server
            c = real_dns(usable)
            c["servers"].insert(0, {"address": "localhost", "domains": ["full:yandex.ru"], "skipFallback": True})
            return c

        def xray_fallback(usable):                # the node's resolver as the last server: every name
            c = real_dns(usable)
            c["servers"].append("localhost")
            return c
        real_conf = dns.conf_text

        def dnsmasq_ru(*a, **kw):                 # прямой DNS без ru_direct-клиента (the plant of -sec С-4)
            return real_conf(*a, **kw) + "server=/yandex.ru/1.1.1.1\n"

        def dnsmasq_default(*a, **kw):            # «#»: the default servers — past the pool once a direct route is
            return real_conf(*a, **kw) + "server=/yandex.ru/#\n"
        def xray_untagged(usable):                # REV-337-last-sec П-1: xray's DNS without its tag — routed by no rule
            c = real_dns(usable)
            c.pop("tag", None)
            return c

        def xray_foreign_tag(usable):             # …or by a tag no rule names (the first outbound takes it)
            return dict(real_dns(usable), tag="dns-x")
        for plant, name in ((xray_ru, "dns_config"), (xray_full, "dns_config"), (xray_fallback, "dns_config"),
                            (xray_untagged, "dns_config"), (xray_foreign_tag, "dns_config"),
                            (dnsmasq_ru, "conf_text"), (dnsmasq_default, "conf_text")):
            mod = xray if name == "dns_config" else dns
            for allowed, seed in ((False, SEED + 6), (True, SEED + 7)):
                self.assertGreater(self.leaks(mock.patch.object(mod, name, plant), seed, allowed=allowed), 0,
                                   (plant.__name__, allowed))
        self.assertEqual(self.leaks(mock.patch.object(xray, "dns_config", real_dns), SEED + 7, allowed=True), 0)

    def test_the_probe_a_of_the_review_is_red(self):
        # опыт A проверяльщика (REV-s1-kt1a-sec С-1): the consent, everybody «всё через выход», the exits down — a server
        # of the direct group by the node's resolver is a leak for every one of them
        st = copy.deepcopy(self.base)
        st["gateway"]["rf_consent"] = good_consent()
        for o in st["gateway"]["users"] + st["gateway"]["devices"]:
            o["rules"] = ALL
        self.assertTrue(oracle_allowed(st))
        real_dns = xray.dns_config

        def xray_ru(usable):
            c = real_dns(usable)
            c["servers"].insert(0, {"address": "localhost", "skipFallback": True,
                                    "domains": ["ext:%s:%s" % (rapply.GEOSITE_ASSET, c_) for c_ in lists.DIRECT
                                                if c_ != "private"]})
            return c
        quiet = random.Random(0)
        quiet.random = lambda: 0.99                                       # drawn(): alive = set() — the exits down
        self.assertEqual(self.violations(st, self.drawn(st, quiet)), [])
        with mock.patch.object(xray, "dns_config", xray_ru):
            bad = self.violations(st, self.drawn(st, quiet))
        self.assertTrue([b for b in bad if b.startswith("DNS past the pool — ru_dns")], bad)

    def everyone_ru(self):
        """The permission, every person and device «Россия напрямую», on, their routes the pool (REV-s1-kt1a-2 З-1)."""
        st = copy.deepcopy(self.base)
        st["gateway"]["rf_consent"] = good_consent()
        for o in st["gateway"]["users"] + st["gateway"]["devices"]:
            o["rules"] = RU
            o["enabled"] = True
            o.pop("route", None)
        self.assertTrue(oracle_allowed(st))
        return st

    def test_any_name_past_the_pool_is_red_even_when_everyone_is_ru(self):
        # REV-s1-kt1a-2 З-1: the plants of the review gave violations=[] with the permission and everyone ru_direct —
        # «Россия напрямую» is Russia only; a foreign name (twitter.com, api.telegram.org of the bot) by the node's
        # resolver is a leak for each of them, so is the DNS of xray routed to «direct» (without the permission too)
        st = self.everyone_ru()
        real_dns, real_conf, real_render = xray.dns_config, dns.conf_text, xray.render

        def xray_every(usable):
            c = real_dns(usable)
            c["servers"].append("localhost")              # EVERY name by the node's resolver
            return c

        def xray_foreign(usable):
            c = real_dns(usable)
            c["servers"].insert(0, {"address": "localhost", "domains": ["domain:twitter.com", "geosite:google"],
                                    "skipFallback": True})
            return c

        def xray_force_code(usable):                       # a code of «всегда через выход» hides ifconfig.me & co
            c = real_dns(usable)
            c["servers"].insert(0, {"address": "localhost", "skipFallback": True,
                                    "domains": ["ext:%s:category-ru" % rapply.GEOSITE_ASSET]})
            return c

        def dm_foreign(*a, **kw):
            return real_conf(*a, **kw) + "server=/twitter.com/1.1.1.1\nserver=/netflix.com/1.1.1.1\n"

        def dm_all(*a, **kw):
            return real_conf(*a, **kw) + "server=/com/1.1.1.1\nserver=/net/1.1.1.1\n"

        def routed(tag, how):
            def rend(*a, **kw):
                cfg = real_render(*a, **kw)
                for r in cfg["routing"]["rules"]:
                    if tag in (r.get("inboundTag") or []):
                        r.pop("balancerTag", None)
                        r.pop("outboundTag", None)
                        r.update(how(cfg))
                return cfg
            return rend

        def leaky_balancer(cfg):                           # gw-all with the node as its fallback
            cfg["routing"].setdefault("balancers", []).append({"tag": "x-leak", "selector": ["gx-"],
                                                                "fallbackTag": "direct"})
            return {"balancerTag": "x-leak"}

        def no_rule(tag):
            def rend(*a, **kw):
                cfg = real_render(*a, **kw)
                cfg["routing"]["rules"] = [r for r in cfg["routing"]["rules"] if tag not in (r.get("inboundTag") or [])]
                return cfg
            return rend
        quiet = random.Random(0)
        quiet.random = lambda: 0.99                                       # drawn(): alive = set()
        self.assertEqual(self.violations(st, self.drawn(st, quiet)), [])
        plants = [(xray, "dns_config", f) for f in (xray_every, xray_foreign, xray_force_code)]
        plants += [(dns, "conf_text", f) for f in (dm_foreign, dm_all)]
        for tag in DNS_INS:
            plants += [(xray, "render", routed(tag, lambda cfg: {"outboundTag": "direct"})),
                       (xray, "render", routed(tag, leaky_balancer)), (xray, "render", no_rule(tag))]
        for mod, name, plant in plants:
            with self.subTest(plant=(name, getattr(plant, "__name__", "")), ):
                with mock.patch.object(mod, name, plant):
                    bad = self.violations(st, self.drawn(st, quiet))
                self.assertTrue([b for b in bad if b.startswith("DNS past the pool")], bad)
        st2 = copy.deepcopy(self.base)                                    # without the permission: routing alone
        st2["gateway"].pop("rf_consent", None)
        st2["gen"] = 2
        self.assertFalse(oracle_allowed(st2))
        with mock.patch.object(xray, "render", routed("gw-dns-int", lambda cfg: {"outboundTag": "direct"})):
            self.assertTrue(self.violations(st2, self.drawn(st2, quiet)))
        for allowed, seed in ((False, SEED + 8), (True, SEED + 9)):   # and in the random states
            self.assertGreater(self.leaks(mock.patch.object(xray, "render", routed(
                "gw-dns-int", lambda cfg: {"outboundTag": "direct"})), seed, allowed=allowed), 0, allowed)

    def test_the_exit_servers_of_trojan_and_ss_are_no_leak(self):
        # REV-s1-kt1a-2 П-2: «full:<server>» of a Trojan / Shadowsocks exit (settings.servers) is the name xray must
        # resolve before any leg is up — not a leak
        cfg = {"outbounds": [{"tag": "gx-t", "protocol": "trojan", "settings": {"servers": [
            {"address": "tj.example.com", "port": 443}]}}, {"tag": "gx-s", "protocol": "shadowsocks", "settings": {
                "servers": [{"address": "SS.example.com", "port": 8388}]}}],
            "dns": {"servers": [{"address": "localhost", "domains": ["full:tj.example.com", "full:ss.example.com"]}]}}
        self.assertEqual(oracle_ru_dns(cfg), [])
        cfg["dns"]["servers"][0]["domains"].append("full:twitter.com")
        self.assertEqual(oracle_ru_dns(cfg), ["localhost: full:twitter.com"])

    def test_a_planted_leak_past_the_block_is_caught(self):
        # REV-s1-b7-2 №3, -sec-2 Н-2 (L1): the lists of Russia for a device whose route is «block» by choice (the filter
        # of ``_list_rules`` forgotten) — red with the permission too, by the oracle's own route
        class Unblocked:
            def __getattr__(self, name):
                return getattr(model, name)

            @staticmethod
            def route_of(st, d):
                r = model.route_of(st, d)
                return dict(r, via="pool") if r.get("via") == "block" else r
        self.assertGreater(self.leaks(mock.patch.object(rapply, "model", Unblocked()), SEED + 5, allowed=True), 0)
        self.assertEqual(self.leaks(mock.patch.object(rapply, "model", model), SEED + 5, allowed=True), 0)

    def test_no_exit_drawn_keeps_the_dns_of_xray_silent(self):
        # REV-337-final-sec З-1 / Д-2: every exit off by the quota «remove», or off by the owner — the DNS of xray goes
        # nowhere unless a user of SOCKS5/VLESS goes directly (its route or the last step «напрямую»)
        def dns_int(st, dec):
            cfg = xray.render(st, self.sec, self.pool, (), dec)
            self.assertFalse([o for o in cfg["outbounds"] if str(o.get("tag")).startswith(("gx-", "gr-"))])
            return [r for r in cfg["routing"]["rules"] if "gw-dns-int" in (r.get("inboundTag") or [])], cfg

        st = copy.deepcopy(self.base)
        for o in st["gateway"]["users"] + st["gateway"]["devices"]:
            o["rules"], o["enabled"], o["route"] = ALL, True, {"via": "pool", "fail": "block"}
        quota = self.quota_removed(exits.decision(st, pool_exits=self.pool), self.ids)
        off = copy.deepcopy(st)
        for eid in self.ids:
            off["gateway"].setdefault("exits", {}).setdefault(eid, {})["enabled"] = False
        for s_, dec in ((st, quota), (off, exits.decision(off, pool_exits=self.pool))):
            rules, cfg = dns_int(s_, dec)
            self.assertEqual([r.get("outboundTag") for r in rules], ["block"])
            self.assertEqual(oracle_dns_route(cfg, False), [])
            leaky = copy.deepcopy(cfg)                      # the code before the fix: red by the oracle
            for r in leaky["routing"]["rules"]:
                if "gw-dns-int" in (r.get("inboundTag") or []):
                    r["outboundTag"] = "direct"
            self.assertTrue(oracle_dns_route(leaky, oracle_someone_direct(s_, True)))
            for route in ({"via": "direct", "fail": "block"}, {"via": "pool", "fail": "direct"}):
                s2 = copy.deepcopy(s_)
                s2["gateway"]["devices"][0]["route"] = route
                self.assertTrue(oracle_someone_direct(s2, True))
                rules, cfg = dns_int(s2, dict(dec, policy=exits.decision(s2, pool_exits=self.pool)["policy"]))
                self.assertEqual([r.get("outboundTag") for r in rules], ["direct"], route)
                self.assertEqual(oracle_dns_route(cfg, True), [])

    def test_the_dns_rule_changes_in_a_running_xray(self):
        # REV-337-last-sec З-1: no exit drawn, «кто-то напрямую» ↔ «никого» — the rule of xray's DNS (by its tag) and the
        # fail-block of «всё через выход» reach the running xray: a restart (None) or the whole routing through the API,
        # never the chains alone (the plant m4: the DNS rule left out of the comparison of the common rules)
        def dns_rules(cfg):
            tag = cfg["dns"]["tag"]
            return [r for r in cfg["routing"]["rules"] if tag in (r.get("inboundTag") or []) or
                    r.get("outboundTag") == xray.FAIL_BLOCK and r.get("user") and not r.get("ruleTag")]

        checked = 0
        for access, others in ((["vless"], False), (["vless"], True), (["socks5", "vless"], True)):
            st = copy.deepcopy(self.base)
            for o in st["gateway"]["users"] + st["gateway"]["devices"]:
                o["rules"], o["enabled"], o["route"] = ALL, True, {"via": "pool", "fail": "block"}
            for i, dv in enumerate(st["gateway"]["devices"]):
                dv["access"] = list(access)
                dv["enabled"] = others or i == 0             # alone: only the DNS rule differs between the two
            for eid in self.ids:
                st["gateway"].setdefault("exits", {}).setdefault(eid, {})["enabled"] = False
            direct = copy.deepcopy(st)
            direct["gateway"]["devices"][0]["route"] = {"via": "direct", "fail": "block"}
            cfgs = [xray.render(x, self.sec, self.pool, (), exits.decision(x, pool_exits=self.pool))
                    for x in (st, direct)]
            self.assertNotEqual(dns_rules(cfgs[0]), dns_rules(cfgs[1]), access)
            for a, b in ((0, 1), (1, 0)):
                plan = xray.hot_plan(cfgs[a], cfgs[b], xray.hot_tags((st, direct)[b], self.sec))
                if plan is None:
                    continue                                 # a restart re-reads the whole file
                self.assertIsNotNone(plan["routing"], (access, others, a, b, sorted(k for k, v in plan.items() if v)))
                self.assertEqual(dns_rules(plan["routing"] and dict(cfgs[b], routing=plan["routing"])),
                                 dns_rules(cfgs[b]))
                checked += 1
        self.assertGreater(checked, 0)                       # the API way is reached, not only restarts

    @staticmethod
    def running(old, new, plan):
        """The config the running xray has after ``hot_plan(old, new)`` is applied: a restart (None) re-reads ``new``;
        the routing through the API replaces the rules; else the old rules less ``rm_rules`` and ``add_rules`` appended
        (``adrules -append``) — the inbounds as ``new`` has them (adu/rmu)."""
        if plan is None:
            return new
        if plan.get("routing") is not None:
            return dict(new, routing=plan["routing"])
        gone = set(plan.get("rm_rules") or [])
        rules = [r for r in old["routing"]["rules"] if r.get("ruleTag") not in gone] + list(plan.get("add_rules") or [])
        return dict(new, routing=dict(new["routing"], rules=rules))

    def test_the_fail_block_list_changes_in_a_running_xray(self):
        # REV-338-sec З-2: no exit drawn, the neighbour stays «напрямую» (gw-dns-int → direct both ways) and only the list
        # of fail-block changes — the second person «напрямую» ↔ «пул», a new user of VLESS. The oracle reads the rules
        # the running xray has after hot_plan (the plant m2: fail-block left out of the comparison of the common rules)
        def fb(cfg):
            return [r for r in cfg["routing"]["rules"] if r.get("outboundTag") == xray.FAIL_BLOCK and r.get("user")
                    and not r.get("ruleTag")]

        goes = oracle_goes

        api = 0
        for access in (["vless"], ["socks5", "vless"]):
            st = copy.deepcopy(self.base)
            for o in st["gateway"]["users"] + st["gateway"]["devices"]:
                o["rules"], o["enabled"], o["route"] = ALL, True, {"via": "pool", "fail": "block"}
            for dv in st["gateway"]["devices"]:
                dv["access"] = list(access)
            for eid in self.ids:
                st["gateway"].setdefault("exits", {}).setdefault(eid, {})["enabled"] = False
            st["gateway"]["devices"][0]["route"] = {"via": "direct", "fail": "block"}       # the neighbour, both ways
            second = copy.deepcopy(st)
            second["gateway"]["devices"][2]["route"] = {"via": "direct", "fail": "block"}
            fresh = copy.deepcopy(st)
            fresh["gateway"]["devices"][2]["access"] = [a for a in access if a != "vless"] or ["socks5"]
            for name, a_st, b_st in (("second direct -> pool", second, st), ("second pool -> direct", st, second),
                                     ("a new user of VLESS", fresh, st), ("a user of VLESS gone", st, fresh)):
                a, b = [xray.render(x, self.sec, self.pool, (), exits.decision(x, pool_exits=self.pool))
                        for x in (a_st, b_st)]
                where = (access, name)
                self.assertEqual([r.get("outboundTag") for r in a["routing"]["rules"]
                                  if a["dns"]["tag"] in (r.get("inboundTag") or [])], ["direct"], where)
                self.assertEqual([r for r in a["routing"]["rules"] if a["dns"]["tag"] in (r.get("inboundTag") or [])],
                                 [r for r in b["routing"]["rules"] if b["dns"]["tag"] in (r.get("inboundTag") or [])],
                                 where)                                  # the DNS rule is the same: only the list moves
                self.assertNotEqual(fb(a), fb(b), where)
                self.assertEqual(oracle_names_past_exits(b, goes(b_st)), [], where)
                plan = xray.hot_plan(a, b, xray.hot_tags(b_st, self.sec))
                live = self.running(a, b, plan)
                self.assertEqual(oracle_names_past_exits(live, goes(b_st)), [], where)
                self.assertEqual(fb(live), fb(b), where)
                api += plan is not None
        self.assertGreater(api, 0)                           # the API way is reached, not only restarts

    def test_the_plants_of_the_final_review_are_red(self):
        # REV-337-final-sec П-1 and the review of the core pack: a rule without a matcher above the DNS rule, a rule for
        # every user of an inbound, a shared upstream of dnsmasq, its «no-resolv» gone, direct_needed always «yes»,
        # «gw-dns-int → direct» with no exit drawn and nobody direct — each red in the random states
        real_render, real_conf = xray.render, dns.conf_text

        def first(rule):
            def rend(*a, **kw):
                cfg = real_render(*a, **kw)
                cfg["routing"]["rules"].insert(0, dict(rule))
                return cfg
            return rend

        def above_dns(*a, **kw):                  # just above gw-dns-int (the plant of the core pack review)
            cfg = real_render(*a, **kw)
            rules = cfg["routing"]["rules"]
            i = next(i for i, r in enumerate(rules) if "gw-dns-int" in (r.get("inboundTag") or []))
            rules.insert(i, {"type": "field", "port": "53", "outboundTag": "direct"})
            return cfg

        def dns_direct_always(*a, **kw):          # the code before REV-337-final-sec З-1
            cfg = real_render(*a, **kw)
            if not [o for o in cfg["outbounds"] if str(o.get("tag")).startswith(("gx-", "gr-"))]:
                for r in cfg["routing"]["rules"]:
                    if "gw-dns-int" in (r.get("inboundTag") or []):
                        r.pop("balancerTag", None)
                        r["outboundTag"] = "direct"
            return cfg

        def no_socks_fb(*a, **kw):
            cfg = real_render(*a, **kw)
            cfg["routing"]["rules"] = [r for r in cfg["routing"]["rules"] if not (
                r.get("outboundTag") == xray.FAIL_BLOCK and r.get("user") and "socks-in" in (r.get("inboundTag") or []))]
            return cfg

        def no_hy2_fb(*a, **kw):                  # 3.3.7.1 Д-1: the fail-block above the ips forgets Hysteria2
            cfg = real_render(*a, **kw)
            cfg["routing"]["rules"] = [r for r in cfg["routing"]["rules"] if not (
                r.get("outboundTag") == xray.FAIL_BLOCK and r.get("user") and not r.get("ruleTag")
                and "gw-hy2" in (r.get("inboundTag") or []))]
            return cfg

        def dm(extra="", drop=None):
            def conf(*a, **kw):
                text = real_conf(*a, **kw)
                if drop:
                    text = "\n".join(ln for ln in text.splitlines() if ln != drop) + "\n"
                return text + extra
            return conf

        plants = [("port 53", mock.patch.object(xray, "render", first({"type": "field", "port": "53",
                                                                        "outboundTag": "direct"}))),
                  ("udp", mock.patch.object(xray, "render", first({"type": "field", "network": "udp",
                                                                    "outboundTag": "direct"}))),
                  ("socks-in", mock.patch.object(xray, "render", first({"type": "field", "inboundTag": ["socks-in"],
                                                                         "outboundTag": "direct"}))),
                  ("above dns", mock.patch.object(xray, "render", above_dns)),
                  ("dns direct always", mock.patch.object(xray, "render", dns_direct_always)),
                  ("server=8.8.8.8", mock.patch.object(dns, "conf_text", dm("server=8.8.8.8\n"))),
                  ("no no-resolv", mock.patch.object(dns, "conf_text", dm(drop="no-resolv"))),
                  ("direct_needed yes", mock.patch.object(exits, "direct_needed", lambda st, *a, **kw: True)),
                  # REV-337-last-sec Д-1: no fail-block of «всё через выход» above the block of addresses
                  ("no fail-block above the ips", mock.patch.object(xray, "_not_direct", lambda *a, **kw: [])),
                  # REV-338 З-2: the fail-block of VLESS only — SOCKS5 forgotten (the stub had no accounts: unseen)
                  ("fail-block forgets socks", mock.patch.object(xray, "render", no_socks_fb)),
                  ("fail-block forgets hy2", mock.patch.object(xray, "render", no_hy2_fb))]
        # REV-3371 П-1: the plants of the fail-block are red on the state set by hand too — not only when the random
        # stream happens to draw «no exit, someone direct, someone not» with the permission
        hand = self.hand_state()
        for name, patch in plants:
            with self.subTest(plant=name):
                # 120 states: «no exit drawn, someone direct, someone not» comes rarely among those with the permission
                # (with hy2 in the states 60 of SEED + 22 had none — the fail-block plants passed unseen)
                caught = [self.leaks(patch, seed, allowed=allowed, n=120) for allowed, seed in ((False, SEED + 21),
                                                                                               (True, SEED + 22))]
                self.assertTrue(all(caught), (name, caught))
                if name.startswith(("no fail-block", "fail-block forgets")):
                    with patch:
                        self.assertTrue(self.violations(hand, self.drawn(hand, random.Random(SEED))), name)
        self.assertEqual([self.leaks(mock.patch.object(xray, "render", real_render), SEED + 21, allowed=a, n=120)
                          for a in (False, True)], [0, 0])

    def test_the_state_by_hand_is_green_and_reaches_the_fail_block(self):
        """REV-3371 П-1: the state of ``hand_state`` — with the permission, no exit drawn, the DNS of xray directly
        (someone «напрямую»), the names of «Мама» checked closed on VLESS, SOCKS5 and Hysteria2 — and nothing broken."""
        st = self.hand_state()
        self.assertTrue(oracle_allowed(st))
        for seed in range(5):                                  # the draws of drawn (alive, quota) change nothing here
            got = self.drawn(st, random.Random(SEED + seed))
            self.assertEqual(self.violations(st, got), [], seed)
            self.assertEqual((got["no_exits"], got["dns_int"]), (True, "direct"), seed)
            self.assertLessEqual({"gw-vless", "socks-in", "gw-hy2"}, set(got["names_checked"]))

class RuExitLastLineTest(_RuXrayCase):
    """SEC-s1-q2 С-1: Q2 sends the lists of a person of a Russian group to «gr-<id>» (her home router: a vless outbound,
    or a freedom with the mark of an AWG leg). The last line of D-136 (``ru_guard``, ``_close_ru``, ``unsafe_file``,
    ``ru_open``) takes that path as «Россия напрямую»: without the consent ⚠РФ it is never applied, never started from
    the file, and a running config that has it while the new one does not is closed."""

    def cfg(self, st, eid):
        dec = self.dec(st)
        dec["groups"] = {_HOME: {"pins": {_D1: {"exit": eid, "tier": "t1", "why": "new"}}}}
        return xray.render(st, secrets.load(), self.pool(), (), dec)

    def test_both_kinds_of_russian_exit_are_seen(self):
        st = self.socks(self.world(save=False))
        for eid in self.ru_ids:                     # kawg201 (freedom + mark) and the VLESS key 202
            with self.subTest(exit=eid):
                cfg = self.cfg(st, eid)
                tag = exits.tag(eid, "ru")
                self.assertTrue(self.lists([r for r in cfg["routing"]["rules"] if r.get("domain") or r.get("ip")],
                                           tag))
                users = set(u for _t, u in xray.ru_direct_paths(cfg))
                self.assertIn("telefon-0001", users)
                self.assertIn(tag, xray._direct_tags(cfg))
                no = copy.deepcopy(st)
                no["gateway"].pop("rf_consent", None)
                self.assertIn("telefon-0001", set(u for _t, u in xray._unsafe(no, secrets.load(), self.w.runner,
                                                                                self.pool(), cfg)))
                with self.assertRaises(KaskadError) as e:
                    xray.ru_guard(no, cfg)
                self.assertTrue(e.exception.data.get("ru_guard"))
                xray.ru_guard(st, cfg)              # with the consent: it passes

    def test_a_running_home_router_path_is_closed(self):
        st = self.socks(self.world(save=False))
        old = self.cfg(st, self.ru_ids[1])
        util.ensure_dir(os.path.dirname(xray.config_path()), 0o700)
        util.atomic_write(xray.config_path(), util.json_text(old), 0o600)
        no = copy.deepcopy(st)
        no["gateway"].pop("rf_consent", None)
        new = xray.render(no, secrets.load(), self.pool(), (), self.dec(no))
        self.assertFalse(xray.ru_direct_paths(new))
        out = {}
        n = xray._close_ru(self.w.runner, new, out)
        self.assertTrue(n or out.get("ru_open"), out)
        self.assertFalse(xray.ru_direct_paths(xray._read_config(xray.config_path())))
