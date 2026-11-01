"""DNS of the gateway: ``kaskad-dns.service`` — dnsmasq on 10.77.0.1, port 5354 (spec §1.4).

    /etc/kaskad/gw/dnsmasq.conf (the ONLY config of this instance: ``--conf-file=``, the distribution's
    /etc/dnsmasq.conf and /etc/dnsmasq.d are not read):
        port=5354               dnsmasq's own port. The clients keep DNS 10.77.0.1 (port 53): the chain gw_dns
                                of table ip kaskad (nat prerouting, ``net.dns_dnat_rules``) sends every query of
                                a client (source in the base, udp and tcp) to 10.77.0.1:5354; conntrack gives
                                the answers port 53 back. So a foreign resolver on *:53 of the node — AdGuard
                                Home, Pi-hole, dnsmasq or unbound with their default configs (a TCP listener on
                                the wildcard takes 10.77.0.1:53 too), bind9 picking up kgw0's address at boot —
                                neither stops kaskad-dns (EADDRINUSE every 5 s for ever, as in 3.1.0) nor
                                answers the clients past the pool and without filter-AAAA
        bind-interfaces + listen-address=10.77.0.1   only the gateway's address on kgw0 (no 127.0.0.1, no
                                                     other address) — no clash with systemd-resolved
                                                     (127.0.0.53) or a foreign DNS
        no-resolv, no-hosts, cache-size=1000, domain-needed, bogus-priv
        filter-AAAA             no IPv6 answers (dnsmasq 2.89+): the gateway carries IPv4 only, a client with
                                IPv6 of its own (PPP, IKEv2, OpenConnect) would go round the tunnel. An older
                                dnsmasq goes without it (``status`` says so)
        server=127.0.0.1#10853  when the pool has exits (dns-in of kaskad-gw-xray -> 1.1.1.1:53 through the pool)
        neither                 no upstream at all: DNS fails closed (nothing goes out in clear)
    kaskad-dns has NO direct upstream any more (3.3.7.2, SEC-3371 Б-2 / REV-s1-kt1a-sec Д-1): one dnsmasq for every
    client of the kernel gave 1.1.1.1 to all of them as soon as one device could go directly — the names of the people
    «всё через выход» left the node past the exits whenever the pool was silent. The direct upstream lives in a second
    dnsmasq, kaskad-gw-dnsdirect.service (/etc/kaskad/gw/dnsmasq-direct.conf, port PORT_DIRECT), the same config with
        strict-order + server=127.0.0.1#10853 (with the pool) + the node's own resolvers (``system_servers``:
        «напрямую» = as the server itself; КТ3-2 — a node in Russia may not reach 1.1.1.1 at all) + 1.1.1.1 / 1.0.0.1
        as the reserve
    and only while some device may go out directly (``exits.direct_needed``: route «direct», its fail policy or its
    group's last step «direct»). Which client asks which dnsmasq — the chain gw_dns by the mark of the client in
    gw_route NOW (``net.dns_dnat_rules``): 0x201 (out directly at this moment) -> PORT_DIRECT, anything else (an exit,
    «по соединениям», block, no element) -> PORT. The mark 0x201 is drawn only for a device whose route lets it go out
    directly (the invariant of tests/gw/test_ru_invariant.py), so the names of the others never meet a direct upstream.
    The unit runs ``dnsmasq --conf-file=...`` as ``Type=forking`` with ``PIDFile=``: dnsmasq binds its sockets
    BEFORE it forks (bind-interfaces), so a port it cannot take fails ``systemctl start`` itself — ``apply``
    says why (who holds the port, the error line of dnsmasq) instead of «started» and a unit that restarts
    every 5 s (Type=simple of 3.1.0 reported success, the watchdog saw it «active» between two restarts). After
    kaskad-net.service (whose ExecStartPost creates kgw0 with 10.77.0.1), restarts on failure.

Nothing on the node itself asks 10.77.0.1:53: xray's DNS goes to 1.1.1.1/1.0.0.1 (gw/xray.py), the settings
of the devices only carry the address. A query of the node's own (``dig @10.77.0.1``) is not DNATed (prerouting
only) and reaches whatever listens on :53.

Update 3.1.0 -> this version: ``gw apply`` (install.sh runs it after an update) puts the chain gw_dns into the
table and rewrites the config and the unit (restart). If it did not run, the minute watchdog (``check``, called
by ``gw check --repair``) finds the config or the unit of 3.1.0 and moves dnsmasq to the port the table sends
the queries to.

The binary comes from the package ``dnsmasq-base`` (no packaged service at all). Deviation from the
spec wording "dnsmasq.service выключена и замаскирована": with dnsmasq-base there is no such service to
start; a dnsmasq.service that already exists (the owner's own) is NOT masked — it is not ours.

Wave 3 (rules «Россия напрямую», ``kaskad.rules.apply.dnsmasq_lines``): ``address=/<domain>/`` of the block group
(ads, Windows telemetry, torrents by their switch: NXDOMAIN for every client); while some client of the kernel
path is ru_direct — ``max-cache-ttl=600`` and one ``nftset=/<domain>/4#ip#kaskad#gw_force`` (always through the
exit) or ``.../gw_ru_dns`` (directly) line per domain. nftset needs dnsmasq built with it (``Compile time options:
... nftset``, Debian 12/13 and Ubuntu 24.04+ are): without it no nftset line is written (the config would not
pass ``--test``), the networks of Russia still go directly (gw_ru), ``status`` says so. What ``--version``
said is kept in /run/kaskad/gw/dnsmasq.json for readers that must not fork (the panel). dnsmasq fills the sets
when it answers a client — whatever port the query reached it on.
"""

import ipaddress
import json
import os
import re

from .. import conflicts, paths, snapshot, util
from ..errors import KaskadError
from ..i18n import tr
from ..rules import apply as rules_apply
from . import addr
from . import sys as gsys

UNIT = "kaskad-dns.service"
PACKAGE = "dnsmasq-base"
DNSMASQ_FALLBACK = "/usr/sbin/dnsmasq"
DIRECT_SERVERS = ("1.1.1.1", "1.0.0.1")        # the reserve of kaskad-gw-dnsdirect, after the node's own resolvers
# КТ3-2 (T-KT3): where the node's own resolvers are named. systemd-resolved's list of the real upstreams first:
# /etc/resolv.conf of such a node names only its stub 127.0.0.53 — a loop through the node's own resolver (never taken)
RESOLV_FILES = ("/run/systemd/resolve/resolv.conf", "/etc/resolv.conf")
SYSTEM_MAX = 3                                    # dnsmasq tries them in order (strict-order): a few are enough
POOL_SERVER = "127.0.0.1#10853"           # = xray.DNS_PORT (F1: 5353 is mDNS; a test keeps them equal)
FILTER_AAAA_SINCE = (2, 89)
NFTSET_SINCE = (2, 87)
PORT = 5354                         # dnsmasq's own port: the clients' 10.77.0.1:53 is DNATed here (gw_dns)
# 3.3.7.2 (SEC-3371 Б-2): the dnsmasq with the direct upstream — only for the clients whose mark is 0x201 now (gw_dns).
# Not 5355 (LLMNR of systemd-resolved listens on the wildcard there), not 5353 (mDNS)
UNIT_DIRECT = "kaskad-gw-dnsdirect.service"
PORT_DIRECT = 5364
PID_FILE_DIRECT = "/run/kaskad-gw-dnsdirect.pid"
CLIENT_PORT = 53                    # what the clients ask
PID_FILE = "/run/kaskad-dns.pid"
JOURNAL_LINES = 30
_DNSMASQ_ERROR = re.compile(r"(?:^|\s)dnsmasq(?:\[\d+\])?: .*(?:fail|error|cannot|unable|bad|unknown|denied|in use)",
                            re.I)


def conf_path():
    return os.path.join(paths.gw_dir(), "dnsmasq.conf")


def conf_path_direct():
    return os.path.join(paths.gw_dir(), "dnsmasq-direct.conf")


def features_path():
    return os.path.join(paths.gw_run_dir(), "dnsmasq.json")


def binary(runner):
    return runner.which("dnsmasq")


def version(runner, dnsmasq=None):
    """(major, minor) of dnsmasq (``dnsmasq --version``), None if unknown."""
    return features(runner, dnsmasq)["version"]


def features(runner, dnsmasq=None):
    """{"version": (major, minor) or None, "nftset": True | False | None} from ``dnsmasq --version`` — nftset
    from its «Compile time options» line (``nftset`` / ``no-nftset``; None when the line is missing)."""
    dnsmasq = dnsmasq or binary(runner)
    if not dnsmasq:
        return {"version": None, "nftset": None}
    res = runner.run([dnsmasq, "--version"])
    text = (res.out or "") + (res.err or "")
    m = re.search(r"version (\d+)\.(\d+)", text)
    ver = (int(m.group(1)), int(m.group(2))) if m else None
    opts = re.search(r"Compile time options:(.*)", text)
    nftset = None
    if opts:
        words = opts.group(1).split()
        nftset = "nftset" in words and "no-nftset" not in words
        if nftset and ver is not None and ver < NFTSET_SINCE:
            nftset = False
    return {"version": ver, "nftset": nftset}


def saved_features():
    """What the last apply learned from ``dnsmasq --version`` ({"version": "2.90", "nftset": bool|None}) — read
    without a fork (the panel); {} before the first apply."""
    doc = snapshot.read_json(features_path(), {})
    return doc if isinstance(doc, dict) else {}


def _save_features(feats):
    ver = feats.get("version")
    doc = {"version": ".".join(str(x) for x in ver) if ver else None, "nftset": feats.get("nftset"),
           "at": util.now_iso()}
    try:
        util.atomic_write(features_path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)
    except OSError:
        pass


def filter_aaaa_ok(ver):
    return ver is not None and tuple(ver) >= FILTER_AAAA_SINCE


# The header of the config and the Description of the unit stay as they are in every language: they are files on disk,
# compared byte for byte (``apply`` rewrites a changed config and restarts dnsmasq; ``stale`` takes a unit other in its
# hard lines for one of 3.1.0) — the language of one request must not rewrite them. The Description is plain English
# (3.3.2 №3: ``journalctl`` of an English server showed it in Russian).
HEADER = ("# Kaskad 3: DNS шлюза (kaskad-dns.service). Файл пишет kaskadctl, правки руками затрутся.\n"  # i18n: skip — config file on disk, compared byte for byte
          "# Клиенты спрашивают адрес шлюза, порт 53: nftables (цепочка gw_dns) переводит запросы на порт %d."  # i18n: skip — config file on disk
          % PORT)


_ZONE_RE = re.compile(r"[A-Za-z0-9._-]+")


def _system_ok(ip, st):
    """A resolver of the node that may be the direct upstream: not the loopback (the stub of systemd-resolved, a
    local dnsmasq — a loop), not a multicast or reserved address, not an address of the gateway's own nets (10.77.0.1
    is this very dnsmasq). IPv6 only global (so no fe80:: — that one would need the interface); its zone («%eth0»)
    only of ``[A-Za-z0-9._-]`` — anything else (``%x@eth0``: «@eth0» is a word of dnsmasq) and the address is dropped
    (REV-3372-2 П-2). IPv4 link-local (169.254.x.x) is kept: a cloud resolver such as 169.254.169.254 / .253 is
    reached by the plain route, no interface needed (REV-3372-2 П-3)."""
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    if ip.version == 6:
        zone = getattr(ip, "scope_id", None)              # Python 3.9+; 3.8 does not read «%…» at all
        if zone is not None and not _ZONE_RE.fullmatch(zone):
            return False
        return ip.is_global
    try:
        return not addr.in_base(st, str(ip))
    except (KaskadError, ValueError):
        return True


def system_servers(st=None):
    """КТ3-2: the node's own upstream resolvers (``RESOLV_FILES`` — the first file that names a usable one), at most
    ``SYSTEM_MAX``, as dnsmasq ``server=`` values (``ipaddress`` reads them: ASCII digits only, nothing else reaches
    the config). [] — none (then the reserve 1.1.1.1 / 1.0.0.1 alone)."""
    for path in RESOLV_FILES:
        found = []
        for ln in (util.read_text(paths.P(path)) or "").split("\n"):
            f = ln.split("#", 1)[0].split(";", 1)[0].split()
            if len(f) != 2 or f[0] != "nameserver":
                continue
            try:
                ip = ipaddress.ip_address(f[1])
            except ValueError:
                continue
            if _system_ok(ip, st) and str(ip) not in found:
                found.append(str(ip))
        if found:
            return found[:SYSTEM_MAX]
    return []


def direct_servers(st=None):
    """The direct upstream of kaskad-gw-dnsdirect in order: the node's own resolvers, then the reserve."""
    out = system_servers(st)
    return out + [s for s in DIRECT_SERVERS if s not in out]


# REV-3372 П-3: the config of kaskad-gw-dnsdirect names its own service and port (not «kaskad-dns … 5354»)
HEADER_DIRECT = ("# Kaskad 3: DNS шлюза для устройств «напрямую» (kaskad-gw-dnsdirect.service). Файл пишет kaskadctl, правки руками затрутся.\n"  # i18n: skip — config file on disk, compared byte for byte
                 "# nftables (цепочка gw_dns) переводит сюда, на порт %d, запросы устройств, которые сейчас идут напрямую."  # i18n: skip — config file on disk
                 % PORT_DIRECT)


def conf_text(st, via_pool, direct=None, filter_aaaa=True, rules=None, port=PORT, pid=PID_FILE):
    """``via_pool`` — the pool has exits (xray's dns-in); ``direct`` — the direct upstream in this config (since
    3.3.7.2 only the config of kaskad-gw-dnsdirect: ``port``/``pid`` of it; None: as before item 8 — direct only
    without a pool); ``rules`` — the lines of the rules «Россия напрямую» (``kaskad.rules.apply.dnsmasq_lines``)."""
    if direct is None:
        direct = not via_pool
    lines = (HEADER_DIRECT if port == PORT_DIRECT else HEADER).split("\n") + [
        "port=%d" % port,
        "bind-interfaces",
        "listen-address=%s" % addr.dns_ip(st),
        "no-resolv",
        "no-hosts",
        "domain-needed",
        "bogus-priv",
        "cache-size=1000",
        "pid-file=%s" % pid]
    if filter_aaaa:
        lines.append("filter-AAAA")
    if via_pool and direct:
        lines.append("strict-order")
    if via_pool:
        lines.append("server=%s" % POOL_SERVER)
    if direct:
        lines.extend("server=%s" % s for s in direct_servers(st))
    if not via_pool and not direct:
        lines.append("# нет выходов в пуле и маршрутов «напрямую»: верхнего DNS нет, запросы не уходят")  # i18n: skip — config file on disk
    lines.extend(rules or [])
    return "\n".join(lines) + "\n"


def unit_text(dnsmasq, direct=False):
    """The unit of kaskad-dns, or (``direct``) of kaskad-gw-dnsdirect — the dnsmasq with the direct upstream."""
    desc = ("Kaskad 3: gateway DNS for the devices going out directly (dnsmasq on the gateway address, port %d)"
            % PORT_DIRECT) if direct else "Kaskad 3: gateway DNS (dnsmasq on the gateway address, port %d)" % PORT
    return "\n".join([
        "[Unit]",
        "Description=%s" % desc,
        "After=network.target kaskad-net.service",
        "",
        "[Service]",
        "Type=forking",
        "PIDFile=%s" % (PID_FILE_DIRECT if direct else PID_FILE),
        "ExecStart=%s --conf-file=%s" % (dnsmasq, conf_path_direct() if direct else conf_path()),
        "ExecReload=/bin/kill -HUP $MAINPID",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def ensure_installed(runner):
    """dnsmasq-base if there is no dnsmasq binary (apt under policy-rc.d). Returns installed packages."""
    if binary(runner):
        return []
    return gsys.apt_install(runner, [PACKAGE])


def _write_conf(runner, dnsmasq, text, path=None):
    """The config through ``dnsmasq --test`` (a temp file, then rename). Raises KaskadError if refused."""
    path = path or conf_path()
    tmp = path + ".new"
    util.atomic_write(tmp, text, 0o644)
    res = runner.run([dnsmasq, "--test", "--conf-file=%s" % tmp])
    if not res.ok:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise KaskadError(tr("gw_dns.conf_refused", error=(res.err or res.out).strip()[:200]))
    os.rename(tmp, path)


def _start(runner, dnsmasq, restart, direct=False):
    """ensure_unit; a unit that does not start -> KaskadError saying why (``why_down``)."""
    unit = UNIT_DIRECT if direct else UNIT
    try:
        return gsys.ensure_unit(runner, unit, unit_text(dnsmasq, direct), restart=restart)
    except KaskadError as e:
        why = why_down(runner, direct=direct)
        raise KaskadError(tr("gw_dns.not_started", unit=unit, why=why or str(e)))


def text_of(st, via_pool, direct=None, filter_aaaa=True, nftset=True):
    """(the text of the config of kaskad-dns ``apply`` writes, the lines of the rules in it) — one composition for
    ``apply`` and for the invariant of «Россия напрямую» (tests/gw/test_ru_invariant.py reads what goes on the disk,
    REV-s1-kt1a-sec С-4). ``direct`` (some device may go out directly) does not reach this config since 3.3.7.2: the
    pool only, or nothing — ``direct_text_of`` is the config of kaskad-gw-dnsdirect."""
    rl = rules_apply.dnsmasq_lines(st, nftset=nftset)
    return conf_text(st, via_pool, False, filter_aaaa, rl), rl


def direct_text_of(st, via_pool, filter_aaaa=True, nftset=True):
    """The config of kaskad-gw-dnsdirect (3.3.7.2): the same rules, its port and pid, the pool first (strict-order)
    then the node's own resolvers and the reserve 1.1.1.1 / 1.0.0.1 (``direct_servers``, КТ3-2) — for the clients
    gw_dns sends there (mark 0x201: out directly now)."""
    rl = rules_apply.dnsmasq_lines(st, nftset=nftset)
    return conf_text(st, via_pool, True, filter_aaaa, rl, port=PORT_DIRECT, pid=PID_FILE_DIRECT)


def apply(st, runner, via_pool, direct=None):
    """Write the config (``dnsmasq --test`` first) and the unit; restart when something changed (SIGHUP does not
    re-read nftset lines). Returns {"changed", "started", "restarted", "filter_aaaa", "servers", "nftset",
    "rules", "port"} (``rules`` — how many lines the rules added). A dnsmasq that cannot start (its port is
    taken) raises KaskadError naming the reason."""
    dnsmasq = binary(runner)
    if not dnsmasq:
        raise KaskadError(tr("gw_dns.no_dnsmasq", package=PACKAGE))
    feats = features(runner, dnsmasq)
    _save_features(feats)
    fa = filter_aaaa_ok(feats["version"])
    if direct is None:
        direct = not via_pool
    text, rl = text_of(st, via_pool, direct, fa, bool(feats["nftset"]))
    changed = util.read_text(conf_path()) != text
    if changed:
        _write_conf(runner, dnsmasq, text)
    r = _start(runner, dnsmasq, changed)
    out = {"changed": changed, "started": r["started"], "restarted": r["restarted"], "filter_aaaa": fa,
           "servers": _servers(text), "direct_servers": [],
           "nftset": feats["nftset"], "rules": len([ln for ln in rl if not ln.startswith("#")]), "port": PORT}
    # the second dnsmasq: only while some device may go out directly (by the settings — not touched when an exit falls)
    if direct:
        dtext = direct_text_of(st, via_pool, fa, bool(feats["nftset"]))
        dchanged = util.read_text(conf_path_direct()) != dtext
        if dchanged:
            _write_conf(runner, dnsmasq, dtext, conf_path_direct())
        dr = _start(runner, dnsmasq, dchanged, direct=True)
        out["direct_servers"] = _servers(dtext)
        out["changed"] = out["changed"] or dchanged
        out["direct"] = {"changed": dchanged, "started": dr["started"], "restarted": dr["restarted"],
                         "port": PORT_DIRECT}
    elif _remove_direct(runner):
        out["changed"] = True
    return out


def _servers(text):
    return [ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("server=")]


def _remove_direct(runner):
    """No device may go out directly: kaskad-gw-dnsdirect away (its unit and config). True if there was one."""
    had = os.path.exists(gsys.unit_path(UNIT_DIRECT)) or os.path.exists(conf_path_direct())
    if os.path.exists(gsys.unit_path(UNIT_DIRECT)):
        gsys.remove_unit(runner, UNIT_DIRECT)
    try:
        os.unlink(conf_path_direct())
    except OSError:
        pass
    return had


def stop(runner):
    if os.path.exists(gsys.unit_path(UNIT_DIRECT)):
        runner.run(["systemctl", "disable", "--now", UNIT_DIRECT])
    if not os.path.exists(gsys.unit_path(UNIT)):
        return False
    was = gsys.unit_active(runner, UNIT)
    runner.run(["systemctl", "disable", "--now", UNIT])
    return was


def remove(runner):
    _remove_direct(runner)
    gone = gsys.remove_unit(runner, UNIT)
    try:
        os.unlink(conf_path())
    except OSError:
        pass
    return gone


def status(runner):
    """{"active", "enabled", "port", "filter_aaaa": bool, "note"?: why AAAA answers are not filtered, "nftset": what
    the last apply learned (bool or None), "rules_lines": nftset/address lines in the config}."""
    out = {"active": gsys.unit_active(runner, UNIT), "enabled": gsys.unit_enabled(runner, UNIT), "port": PORT}
    text = util.read_text(conf_path()) or ""
    out["filter_aaaa"] = "\nfilter-AAAA\n" in text
    if text and not out["filter_aaaa"]:
        ver = version(runner)
        out["note"] = tr("gw_dns.note_old",
                         version=".".join(str(x) for x in ver) if ver else tr("gw_dns.version_unknown"))
    out["nftset"] = saved_features().get("nftset")
    out["rules_lines"] = {"nftset": text.count("\nnftset=/"), "address": text.count("\naddress=/")}
    if os.path.exists(gsys.unit_path(UNIT_DIRECT)):          # 3.3.7.2: the DNS of the devices going out directly
        out["direct"] = {"active": gsys.unit_active(runner, UNIT_DIRECT), "port": PORT_DIRECT,
                         "servers": _servers(util.read_text(conf_path_direct()) or "")}
    return out


# ── why the DNS of the gateway does not work (F2) ──────────────────────────

def _wildcard(a):
    return a in ("", "*", "0.0.0.0", "::")


def holders(runner, port=PORT, st=None):
    """Listeners that take ``port`` of the gateway's address: on 10.77.0.1 itself or on a wildcard (``ss -Hltnup``)
    -> [{"proto", "addr", "port", "procs"}]. Our own dnsmasq is among them when kaskad-dns runs."""
    if not runner.which("ss"):
        return []
    res = runner.run(["ss", "-Hltnup"])
    ip = addr.dns_ip(st)
    return [l for l in (conflicts.parse_ss(res.out) if res.ok else [])
            if l["port"] == port and (_wildcard(l["addr"]) or l["addr"] == ip)]


def _holders_text(found):
    out = []
    for l in found:
        who = ", ".join(l["procs"]) or tr("gw_dns.process_unknown")
        item = "%s (%s:%d/%s)" % (who, l["addr"] or "*", l["port"], l["proto"])
        if item not in out:
            out.append(item)
    return "; ".join(out)


def _journal_error(runner, unit=UNIT):
    """The last error line dnsmasq left in the journal of kaskad-dns (``failed to create listening socket for
    10.77.0.1: Address already in use``), or ""."""
    res = runner.run(["journalctl", "-u", unit, "-n", str(JOURNAL_LINES), "--no-pager", "-o", "cat"], timeout=10)
    if not res.ok:
        return ""
    found = [ln.strip() for ln in (res.out or "").splitlines() if _DNSMASQ_ERROR.search(ln)]
    return util.redact(found[-1][:200]) if found else ""


def why_down(runner, st=None, direct=False):
    """Why kaskad-dns (``direct``: kaskad-gw-dnsdirect) does not run, for people: who holds its port on the gateway's
    address (it is not running, so nobody there is ours), else the last error line of dnsmasq; "" when unknown."""
    port = PORT_DIRECT if direct else PORT
    found = holders(runner, port, st)
    if found:
        return tr("gw_dns.port_busy", port=port, ip=addr.dns_ip(st), holders=_holders_text(found))
    return _journal_error(runner, UNIT_DIRECT if direct else UNIT)


def probe(runner, st=None):
    """``gw probe`` (read-only): can dnsmasq of the gateway take its port here — {"port", "busy": [listeners
    that are not our running kaskad-dns], "warning"}. Port 53 of the node does not matter any more (DNAT)."""
    found = holders(runner, PORT, st)
    if found and gsys.unit_active(runner, UNIT):
        found = [l for l in found if l["procs"] and set(l["procs"]) != set(["dnsmasq"])]
    out = {"port": PORT, "busy": found, "warning": ""}
    if found:
        out["warning"] = tr("gw_dns.probe_busy", port=PORT, holders=_holders_text(found))
    # REV-3372 П-4: the port of kaskad-gw-dnsdirect too — taken, the devices «напрямую» would have no names
    fd = holders(runner, PORT_DIRECT, st)
    if fd and gsys.unit_active(runner, UNIT_DIRECT):
        fd = [l for l in fd if l["procs"] and set(l["procs"]) != set(["dnsmasq"])]
    if fd:
        out["busy_direct"] = fd
        out["warning"] = " ".join(x for x in (out["warning"], tr("gw_dns.probe_busy", port=PORT_DIRECT,
                                                                  holders=_holders_text(fd))) if x)
    return out


def _with_port(text):
    """A config of 3.1.0 (dnsmasq on :53) with this version's header and port; the rest as it was."""
    body = [ln for ln in (text or "").splitlines()
            if not ln.startswith("port=") and not ln.startswith("# Kaskad 3: DNS шлюза")  # i18n: skip — the header of our own config file
            and not ln.startswith("# Клиенты спрашивают адрес шлюза")]  # i18n: skip — the header of our own config file
    return "\n".join(HEADER.split("\n") + ["port=%d" % PORT] + body) + "\n"


def stale(runner):
    """What of kaskad-dns is still of 3.1.0 (dnsmasq on :53 while the table sends the queries to PORT): ["config"],
    ["unit"], both or none. Nothing before the first apply wrote them."""
    out = []
    text = util.read_text(conf_path())
    if text is not None and ("\nport=%d\n" % PORT) not in text:
        out.append("config")
    unit = util.read_text(gsys.unit_path(UNIT))
    dnsmasq = binary(runner)
    # a unit of 3.1.0 differs in its lines that matter (Type=, ExecStart=…); another Description= alone (3.3.2: the
    # caption in English) is not «old» — the next apply rewrites it without a restart of dnsmasq
    if unit is not None and dnsmasq and gsys.unit_needs_restart(unit, unit_text(dnsmasq)):
        out.append("unit")
    return out


def migrate(runner):
    """Move a kaskad-dns of 3.1.0 to PORT: the config keeps its upstreams and rule lines (``dnsmasq --test``), the
    unit becomes Type=forking; restart. Raises KaskadError (``_start``: the reason when it does not start)."""
    dnsmasq = binary(runner)
    if not dnsmasq:
        raise KaskadError(tr("gw_dns.no_dnsmasq_short", package=PACKAGE))
    text = util.read_text(conf_path())
    if text is not None and ("\nport=%d\n" % PORT) not in text:
        _write_conf(runner, dnsmasq, _with_port(text))
    return _start(runner, dnsmasq, True)


def check(st, runner, repair=False, log=None):
    """The DNS part of ``gw check`` (ops.check, after the services; never during boot): a config or unit of 3.1.0
    (``stale``) is moved to PORT with ``repair``; a kaskad-dns that does not run gets a problem saying why (who
    holds its port, the error of dnsmasq) — the restart itself is the services' check. Returns problems
    ``{"kind": "gw_dns", "detail", "repaired", "error"?}``."""
    if not os.path.exists(gsys.unit_path(UNIT)):
        return []
    log = log or (lambda kind, text: util.log_action(text))
    probs = []
    old = stale(runner)
    if old:
        p = {"kind": "gw_dns", "repaired": False, "detail": _stale_detail(old)}
        probs.append(p)
        if repair:
            try:
                migrate(runner)
                p["repaired"] = True
                with util.journal_lang():                    # the journal speaks the language of the server
                    line = tr("gw_dns.log.moved", detail=_stale_detail(old), port=PORT)
                log("gw_dns", line)
            except KaskadError as e:
                p["error"] = str(e)
            return probs
    if not gsys.unit_active(runner, UNIT):
        why = why_down(runner, st)
        if why:
            probs.append({"kind": "gw_dns", "repaired": False,
                          "detail": tr("gw_dns.down", unit=UNIT, why=why)})
    if os.path.exists(gsys.unit_path(UNIT_DIRECT)) and not gsys.unit_active(runner, UNIT_DIRECT):
        why = why_down(runner, st, direct=True)
        if why:
            probs.append({"kind": "gw_dns", "repaired": False,
                          "detail": tr("gw_dns.down", unit=UNIT_DIRECT, why=why)})
    return probs


def _stale_detail(old):
    """«DNS шлюза в прежнем виде (конфиг и служба): …» of ``stale``."""
    return tr("gw_dns.stale", what=tr("gw_dns.and").join(tr("gw_dns.part." + x) for x in old), port=PORT)
