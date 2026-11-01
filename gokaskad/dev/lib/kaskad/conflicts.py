"""Port conflicts of a new forward (docs/k3/SPEC-wave1.md §3.6). Read-only.

A Kaskad forward takes every connection to proto/port addressed to this server (DNAT at priority
-101, before iptables nat and before local delivery). So before adding one we look for what it
would cut off:

    ssh           sshd (or dropbear) listens on the port, or the port of the current SSH session
                  ($SSH_CONNECTION): the forward would cut SSH to this server
    listener      a local process listens on the port on an address reachable from outside
                  (0.0.0.0, ::, *, a public IPv4) — e.g. xray or nginx on 443. Loopback-only
                  listeners (127.0.0.1:10808 of kaskad-egress) and IPv6-only addresses are not
                  affected by an IPv4 forward and are not conflicts.
    docker        the port is published by a container: a DNAT in ``iptables -t nat -S DOCKER``
                  (not bound to 127.0.0.0/8), or a docker-proxy listener
    foreign_dnat  a DNAT/REDIRECT on that proto/port in ``iptables -t nat -S PREROUTING`` without a
                  ``kaskad:`` comment (our priority -101 would override it)
    gateway       an inbound of the gateway (wave 2) listens on proto/port (from state: installed and
                  enabled inbounds, their adapters' ports) — the forward would take its clients away
    site          tcp/80 while the node has a domain (state ``site.domain``, docs/k3/SPEC-wave4.md §4.1 п. 4):
                  Let's Encrypt checks the domain there — a forward would take the check and the certificate
                  of the domain would stop renewing; tcp/443 while the cover site is on. In site mode the nginx
                  of the site that Kaskad owns (``site.nginx.owned``) is not listed again as a listener

Missing tools (no ``ss``, no ``iptables``) mean no conflict of that kind. Rules are split with
shlex, never eval'ed.
"""

import ipaddress
import os
import re
import shlex

from . import state
from .i18n import tr

KINDS = ("ssh", "gateway", "site", "listener", "docker", "foreign_dnat")
SSH_PROCS = ("sshd", "dropbear")
DOCKER_PROCS = ("docker-proxy",)
IPT_WAIT = ["-w", "5"]
# what a new forward breaks, by the cause: «<cause> — <effect>» (tr("conflicts.effect.<name>"))
EFFECTS = ("gateway", "site_cert", "site_page", "listener", "ssh", "foreign_dnat")

_USER = re.compile(r'\("([^"]+)",pid=\d+')
_PID = re.compile(r'\("[^"]+",pid=(\d+)')


def parse_ss(text, proto=None):
    """``ss -Hltnup`` output -> [{"proto", "addr", "port", "procs": [names], "pids": [numbers]}]. Lines that are not
    tcp/udp sockets (a header of an old ss without -H) are skipped. ss asked for ONE kind of sockets (``ss -Hlnup``)
    prints no Netid column (``UNCONN 0 0 *:443 *:* users:…``, iproute2 of Ubuntu 24.04 — KT3-4): such lines are read
    when ``proto`` ("tcp"/"udp") says what was asked."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] in ("tcp", "udp"):
            kind, local = parts[0], parts[4]
        elif proto in ("tcp", "udp") and len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
            kind, local = proto, parts[3]
        else:
            continue
        addr, sep, port = local.rpartition(":")
        if not sep or not port.isdigit():
            continue
        if addr.startswith("[") and addr.endswith("]"):
            addr = addr[1:-1]
        addr = addr.split("%", 1)[0]
        procs = []
        for name in _USER.findall(line):
            if name not in procs:
                procs.append(name)
        pids = []
        for pid in _PID.findall(line):
            if int(pid) not in pids:
                pids.append(int(pid))
        out.append({"proto": kind, "addr": addr, "port": int(port), "procs": procs, "pids": pids})
    return out


def exposed(addr):
    """Does a listener on ``addr`` receive IPv4 connections from outside (what a forward takes)?"""
    if addr in ("", "*", "0.0.0.0", "::"):
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if ip.version == 6:
        if ip.ipv4_mapped is None:
            return False
        ip = ip.ipv4_mapped
    return not ip.is_loopback


def _is_ssh(lst):
    if any(p in SSH_PROCS for p in lst["procs"]):
        return True
    # socket-activated sshd (ssh.socket on Ubuntu 22.10+): before the first login only systemd holds it
    return lst["port"] == 22 and lst["procs"] == ["systemd"]


def _port_spec_has(spec, port):
    """``--dport``/``--dports`` value: "443", "8000:8010", "80,443,8000:8010"."""
    for part in str(spec).split(","):
        a, sep, b = part.partition(":")
        try:
            lo = int(a) if a else 0
            hi = int(b) if sep and b else (65535 if sep else lo)
        except ValueError:
            return True             # unknown form: assume it may match
        if lo <= port <= hi:
            return True
    return False


def parse_rule(line):
    """One ``iptables -S`` line -> dict, or None for non ``-A`` lines. Keys: chain, proto,
    dports, target, comment, to, dst (None = no such condition). A negated condition is kept with
    a leading "!" (``! -p udp`` -> proto "!udp")."""
    try:
        tok = shlex.split(line)
    except ValueError:
        return None
    if len(tok) < 2 or tok[0] != "-A":
        return None
    r = {"chain": tok[1], "proto": None, "dports": None, "target": None, "comment": "", "to": "", "dst": None}
    neg = False
    i = 2
    while i < len(tok):
        t = tok[i]
        nxt = tok[i + 1] if i + 1 < len(tok) else ""
        if t == "!":
            neg = True
            i += 1
            continue
        key = {"-p": "proto", "--protocol": "proto", "--dport": "dports", "--destination-port": "dports",
               "--dports": "dports", "--destination-ports": "dports", "-d": "dst", "--destination": "dst"}.get(t)
        if key:
            r[key] = ("!" if neg else "") + nxt.lower()
            i += 2
        elif t in ("-j", "--jump"):
            r["target"] = nxt
            i += 2
        elif t == "--comment":
            r["comment"] = nxt
            i += 2
        elif t == "--to-destination":
            r["to"] = nxt
            i += 2
        elif t == "--to-ports":
            r["to"] = ":" + nxt
            i += 2
        else:
            i += 1
        neg = False
    return r


def rule_matches(r, proto, port):
    """Can this rule catch proto/port? Conditions we do not model count as "may match"."""
    p = r.get("proto")
    if p:
        if p.startswith("!"):
            if p[1:] == proto:
                return False
        elif p not in (proto, "all", "0"):
            return False
    d = r.get("dports")
    if d:
        if d.startswith("!"):
            if _port_spec_has(d[1:], port):
                return False
        elif not _port_spec_has(d, port):
            return False
    return True


def _loopback_dst(r):
    d = r.get("dst") or ""
    if not d or d.startswith("!"):
        return False
    try:
        return ipaddress.ip_network(d, strict=False).subnet_of(ipaddress.ip_network("127.0.0.0/8"))
    except (ValueError, TypeError):
        return False


def _ipt_rules(runner, chain):
    res = runner.run(["iptables"] + IPT_WAIT + ["-t", "nat", "-S", chain])
    if not res.ok:                  # no such chain (no Docker) or iptables error: nothing to report
        return []
    return [r for r in (parse_rule(ln) for ln in res.out.splitlines()) if r]


def _procs_text(procs):
    return ", ".join(procs) if procs else tr("conflicts.procs_unnamed")


def effect(name):
    """« — what a new forward breaks» after a cause (``name`` of EFFECTS), in the language of the call. Who reuses the
    causes for something else than a forward (an inbound of the gateway, certbot on :80) cuts these tails off by
    ``effects()`` — not by the Russian words, which are only one of the languages."""
    return " — " + tr("conflicts.effect." + name)


def effects():
    """Every tail ``effect(name)`` can add, in the language of the call."""
    return [effect(n) for n in EFFECTS]


def gateway_ports(st=None):
    """{(proto, port): title} of the gateway's installed and enabled inbounds ({} without the gateway)."""
    try:
        st = state.load() if st is None else st
        from .gw import model, registry
    except (ImportError, state.StateError):
        return {}
    out = {}
    for kind in model.enabled_kinds(st):
        ad = registry.get(kind)
        if ad is None:
            continue
        for pr, p in ad.ports(st):
            out[(pr, p)] = registry.title(kind)          # a name for people, never «VLESS (vless)»
    return out


def site_domain(st=None):
    """The domain of the node (state ``site.domain``) or ""."""
    try:
        st = state.load() if st is None else st
    except state.StateError:
        return ""
    site = (st or {}).get("site")
    d = site.get("domain") if isinstance(site, dict) else None
    return d if isinstance(d, str) else ""


def site_mode(st=None):
    """``off`` | ``domain`` | ``site``; missing/old state is safely ``off``."""
    try:
        st = state.load() if st is None else st
    except state.StateError:
        return "off"
    site = (st or {}).get("site")
    mode = site.get("mode") if isinstance(site, dict) else "off"
    return mode if mode in ("domain", "site") else ("domain" if site_domain(st) else "off")


def _site_nginx_owned():
    """nginx is Kaskad's alone (installed by Kaskad, no foreign vhosts): its listener is the site itself."""
    try:
        from .site import nginx
        return nginx.owned()
    except Exception:                                   # noqa — a conflict check never fails on inspection
        return False


def _probe(runner):
    """What ``check`` reads from the system once: (listeners of ``ss`` or None, DOCKER rules, PREROUTING rules)."""
    rows = None
    if runner.which("ss"):
        res = runner.run(["ss", "-Hltnup"])
        if res.ok:
            rows = parse_ss(res.out)
    ipt = runner.which("iptables")
    return rows, (_ipt_rules(runner, "DOCKER") if ipt else []), (_ipt_rules(runner, "PREROUTING") if ipt else [])


def check(proto, port, runner, env=None, st=None):
    """Conflicts of a new forward proto/port: ``[{"kind": ..., "detail": "..."}]``, most severe
    first (ssh, gateway, site, listener, docker, foreign_dnat), at most one entry per kind. ``env`` defaults
    to os.environ (for $SSH_CONNECTION); ``st`` to the saved state (for the gateway's inbounds and the domain)."""
    return _check(proto, port, env, st, _probe(runner))


def check_many(proto, ports, runner, env=None, st=None, local=False):
    """``check`` of many ports of one protocol (a range of Hysteria2 port hopping) with ONE ``ss`` and one read of
    the iptables chains: {port: [conflicts]} of the busy ports only. ``local`` — a listener on a loopback address
    counts too (an inbound bound to every address cannot take a port a local service holds: xray would not start)."""
    probe = _probe(runner)
    out = {}
    for p in ports:
        found = _check(proto, p, env, st, probe, local)
        if found:
            out[p] = found
    return out


def _check(proto, port, env, st, probe, local=False):
    proto = state.parse_proto(proto)
    port = state.parse_port(port)
    env = os.environ if env is None else env
    rows, docker_rules, pre_rules = probe
    found = {}
    held = {"procs": [], "pids": []}                  # who holds it (kind «listener»): the data, not the words

    def add(kind, detail):
        if kind not in found:
            found[kind] = []
        if detail not in found[kind]:
            found[kind].append(detail)

    title = gateway_ports(st).get((proto, port))
    if title:
        add("gateway", tr("conflicts.cause.gateway", title=title) + effect("gateway"))
    if proto == "tcp" and port == 80:
        d = site_domain(st)
        if d:
            add("site", tr("conflicts.cause.site_cert", domain=d) + effect("site_cert"))
    if proto == "tcp" and port == 443 and site_mode(st) == "site":
        add("site", tr("conflicts.cause.site_page") + effect("site_page"))

    listeners = [l for l in rows or [] if l["proto"] == proto and l["port"] == port and (local or exposed(l["addr"]))]
    if proto == "tcp" and port in (80, 443) and site_mode(st) == "site" and _site_nginx_owned():
        # Kaskad's own nginx of the site: «сайт перестанет открываться» above already says what breaks
        listeners = [l for l in listeners if "nginx" not in l["procs"]]
    for l in listeners:
        if proto == "tcp" and _is_ssh(l):
            add("ssh", tr("conflicts.cause.ssh_listen", procs=_procs_text(l["procs"])))
        elif any(p in DOCKER_PROCS for p in l["procs"]):
            add("docker", tr("conflicts.cause.docker_proxy"))
        else:
            add("listener", tr("conflicts.cause.listener", procs=_procs_text(l["procs"])) + effect("listener"))
            for key in ("procs", "pids"):
                held[key].extend(x for x in l.get(key) or [] if x not in held[key])
    sc = (env.get("SSH_CONNECTION") or "").split()
    if proto == "tcp" and len(sc) == 4 and sc[3] == str(port):
        add("ssh", tr("conflicts.cause.ssh_session"))

    for r in docker_rules:
        if r["target"] == "DNAT" and rule_matches(r, proto, port) and not _loopback_dst(r):
            add("docker", tr("conflicts.cause.docker_dnat", to=r["to"] or "?"))
    for r in pre_rules:
        if r["target"] in ("DNAT", "REDIRECT") and not r["comment"].startswith("kaskad:") \
                and rule_matches(r, proto, port):
            add("foreign_dnat", tr("conflicts.cause.foreign_dnat", target=r["target"], to=r["to"] or "?")
                + effect("foreign_dnat"))
    # причины SSH (слушает sshd, идёт текущее подключение) — через «;», а чем грозит — один раз в конце (стенд T-M6)
    # «listener» also says who holds the port as data (``procs``, ``pids``) — the wizard builds its command for SSH from
    # them, not from the words of ``detail`` (R-333 С1)
    return [dict({"kind": k, "detail": "; ".join(found[k]) + (effect("ssh") if k == "ssh" else "")},
                 **(held if k == "listener" else {})) for k in KINDS if k in found]


def message(proto, port, found):
    """The refusal text for ConflictError."""
    return tr("conflicts.message", port=int(port), proto=proto, details="; ".join(c["detail"] for c in found))
