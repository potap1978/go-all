"""xray of the gateway: ``kaskad-gw-xray.service`` with /etc/kaskad/gw/xray.json (spec §1.3, §1.4, §3.7).

A separate xray (the bot's ``kaskad-egress`` is not touched) built from the same binary as the egress
(/etc/kaskad/egress/xray, copied to /opt/kaskad/gw/xray: /etc/kaskad is root-only). It runs only when
needed: the pool has exits, or an adapter adds an inbound (SOCKS5, VLESS Reality). The kernel decides the path; xray
carries what goes through VLESS and SOCKS5:

    inbounds   tproxy-in   127.0.0.1:12345 dokodemo-door, tcp+udp, sockopt.tproxy, followRedirect, no
                           sniffing (see IPOnDemand below) (mark 0x200 from gw_pre: the pool's VLESS legs)
               tproxy-<id> 127.0.0.1:12400+N, the same, one per VLESS exit (mark 0x300+N: route exit:<id>)
               dns-in      127.0.0.1:10853 dokodemo-door -> 1.1.1.1:53 (dnsmasq's upstream; through the pool)
               metrics-in  127.0.0.1:10852 -> the metrics outbound (/debug/vars: observatory for exits.probe)
               stats-in    127.0.0.1:10854 -> xray's StatsService (only while an adapter with ``xray_stats`` has users:
                           the VLESS inbound reads its per-user counters — sessions and traffic of «Обзор»)
               + what adapters give (``xray_inbounds``: gw-socks5 of the SOCKS5 adapter, gw-vless of VLESS Reality)
    outbounds  gx-<id> per enabled exit: VLESS from egress_parse.vless_outbound, AWG legs as freedom with
               sockopt.mark 256+N (egress_parse.awg_outbound); direct; block
    routing    (domainStrategy IPOnDemand)
               metrics-in -> metrics; gw-dns-int (xray's own DNS) -> balancer gw-all; dns-in -> gw-all
               stats-in -> the StatsService (first: nothing else ever sees it)
               every client inbound (tproxy-*, SOCKS5, VLESS) to this host, private/CGNAT/link-local/multicast
                      networks, ::1, fc00::/7, fe80::/10, the node's own addresses, ``localhost`` -> block
               tproxy-<id> -> gx-<id>
               wave 3 (``kaskad.rules.apply.xray_rules``), SOCKS5 users: ads/telemetry (torrents) -> block for all;
                      ru_direct users: «always through the exit» domains -> their route, then «directly» domains
                      and ``ext:roscomvpn-geoip.dat:direct`` -> direct (files next to the binary, copied by apply;
                      a code the file lacks is never named — xray would not start)
               adapters' rules (``xray_routing(st, sec, target)``: SOCKS5 and VLESS users) — ``target(route)`` maps
                      a device route to pool: gw-all | exit: gx-<id> | direct | block, fail=direct -> a
                      balancer with fallbackTag direct; then adapters' inbounds, anything else -> block
               tproxy-in (the pool's clients the kernel sent through a VLESS leg) -> gw-vless
    balancers  leastPing over the observatory (probe http 204 every 10 s), fallbackTag block (fail-closed)
    dns        tag gw-dns-int, IPv4 answers only: 1.1.1.1 and 1.0.0.1 THROUGH THE POOL (the routing rule
               above); ``localhost`` (the node's resolver) only for the hostnames of the pool's VLESS servers
               and of the observatory's probe — they must resolve before any leg is up; never a fallback
               for other names. Without exits drawn (SOCKS5 alone, all off, all off by the quota) gw-dns-int goes
               direct only when a user of SOCKS5/VLESS goes directly (its route or last step), else block; then
               the other users of SOCKS5/VLESS -> fail-block above the block of addresses (no name of theirs is
               resolved off the node).

Why IPOnDemand (the spec text said IPIfNonMatch): with IPIfNonMatch xray resolves a name only when NO rule
matched it — a SOCKS5 user rule matches every request, so ``evil.example`` -> 127.0.0.1 would never meet
the block rule. IPOnDemand resolves when the first rule with IPs is evaluated: the block rule stands
first, so names pointing at private addresses are blocked too. The lookup goes through gw-dns-int
(through the pool). IPOnDemand resolves the target DOMAIN first — a sniffed one too (routing/dns/
context.go: GetTargetDomain returns the routeOnly RouteTarget) — and checks ITS addresses instead of the
real destination: so the client inbounds have no sniffing (a fake SNI or Host to a private address would
pass, every TLS connection would cost a lookup). TPROXY connections then carry only their real
destination IP: no lookup at all.

The config depends on the pool, the adapters' accounts and the node's addresses — NOT on liveness and NOT
on the routes of TPROXY devices, so a dead or revived leg or a device moved to another exit never
restarts xray (the kernel moves clients by gw_route instead).

Stage 1 (SPEC-stage1-exits §2.2–2.7, §5.2, §5.6; T5) — ``render(…, decision)``, ``apply(…, decision, seq, fp)``: a
user of VLESS / SOCKS5 goes to the exit its device is pinned to (``exits.target(st, device, decision)["xray"]``:
gx-<id>, direct, block by choice, ``fail-block`` — the last step of its group), one rule per user (``vl-``/``sk-``)
after the shared rules of the lists (``ls-``); every xray exit has ``tproxy-<id>`` and ``probe-<id>`` (127.0.0.1:
12700+N) with its own rules (``px-``); ``pool-socks`` 127.0.0.1:12699 -> gw-all; the API and the counters of the
outbounds always. The decision moves, deaths, the pool, the quota go through the API (``hot_plan``/``hot_apply``:
users, chains, ``ado``/``adi``/``rmo``/``rmi``, all the rules anew + ``bo``); a restart only for a block by choice, a
user leaving, a new SOCKS5 account, a changed base. ``render(…)`` without a decision is the config of 3.3.5 as it was.

The service runs as the system user ``kaskad-xray`` (created if missing) with CAP_NET_ADMIN (TPROXY,
SO_MARK of the AWG outbounds) and CAP_NET_BIND_SERVICE only, NoNewPrivileges; the config reaches it as a
systemd credential (``LoadCredential``: a private copy only the service can read — xray.json itself stays
root-only, it holds the SOCKS5 passwords). gw_out drops its sockets to 127.0.0.0/8 (and ::1) except DNS.
ExecStartPre (as root) refuses to start while the gw part of table ip kaskad is missing.

Deviation from spec §1.4 (reported): DNS of the clients goes to 1.1.1.1:53 through the pool as plain DNS
inside the tunnel, not DoH through xray's DNS module — the DNS module would also resolve the hostnames of
VLESS servers and of the observatory probe through the same pool (a cold start with only AWG legs never
comes alive). The node's ISP sees only the tunnel either way.
"""

import contextlib
import copy
import hashlib
import os
import re
import shutil
import threading
import time
from urllib.parse import urlsplit

from .. import conflicts, paths, util
from ..errors import KaskadError
from ..i18n import tr
from ..rules import apply as rules_apply
from . import exits, mip, model, net
from . import sys as gsys

UNIT = exits.XRAY_UNIT
TPROXY_PORT = net.TPROXY_PORT
# F1 (finding 10): 3.1.0 had 5353 — mDNS, avahi-daemon holds *:5353 and xray would not start (VLESS, SOCKS5 and the
# DNS through the pool down). The next ``gw apply`` (install.sh runs it after an update) renders xray.json and
# dnsmasq.conf (``dns.POOL_SERVER``) with this port; the old pair keeps working until then.
DNS_PORT = 10853
METRICS_PORT = 10852
STATS_PORT = 10854                  # the StatsService of the VLESS inbound (inbounds/vless.py STATS_PORT)
STATS_TAG = "gw-stats"
HOT_SERVICES = ("HandlerService", "RoutingService")      # users and their rules without a restart (127.0.0.1 only)
# ruleTag prefixes of the per-user chains: those of the adapters (``xray_hot_prefix``: "vl-" VLESS, "sk-" SOCKS5, "h2-"
# Hysteria2 by its SPEC) — ``hot_prefixes()``; these two are known even when an adapter is not registered (an old file)
HOT_PREFIX = ("vl-", "sk-")
LS_PREFIX = "ls-"                   # the shared rules of the lists with their users (§2.4): a change — all rules anew
PX_PREFIX = "px-"                   # the rules of one exit of the pool (its tproxy-<id>, probe-<id>): come and go with it
API_TIMEOUT = 10                    # s of the process; the call itself — ``-timeout 8`` (API_TIMEOUT - 2)
HOT_MAX_RULES = 200                 # simple rules per call of ``adrules``/``rmrules`` (T0 С0.9: 200 — 18 ms)
HOT_MAX_USERS = 20                  # users per call of ``adu``
HOT_MAX_EXT_RULES = 32              # rules with lists (≈ 100 KB each) — the gRPC limit of 4 MB; more — a restart (Р28)
HOT_MAX_TICK = 1000                 # operations of one apply; more — a restart (§2.5)
HOT_LOCK_S = 20                     # s: the calls of the API under the xray lock; more — a restart instead (§2.5)
RESTART_GAP_S = 60                  # the collector restarts xray once a minute at the most (the storm, §2.5)
AFTER_HOLD_S = 20                   # s: a restart owed to the answer of the panel holds the collector back so long
ANSWER = "answer"                   # ``defer_restart`` of a write of the panel: the restart after its answer
STORM = "storm"                     # ``defer_restart`` of the collector: ``not restart_due()`` under the xray lock
REJECT_RETRY_S = 300                # the same config refused by ``xray -test`` is not tested again sooner (§2.7)
FAIL_BLOCK = "fail-block"           # the fallback block of a group (§2.2): no restart, Russia stays direct
POOL_SOCKS_TAG = "pool-socks"       # 127.0.0.1:12699 -> gw-all («Проверить ссылку» through «Основная», §2.6)
POOL_SOCKS_PORT = 12699
POOL_PREFIXES = ("gx-", "gr-")      # the outbounds of the exits of the pool (exits.tag): they come and go live (§2.7)
PROBE_PORT_BASE = 12700             # probe-<id>: 127.0.0.1:12700+N -> gx-<id> (§5.2; gw/probe/fetch.py)
EGRESS_API_PORT = 10851             # the API of the bot's xray (install.sh EGRESS_API_PORT): root only too (§2.9, П5)
# the listeners of xray must bind 127.0.0.1:<port>: a socket of another program there, on 0.0.0.0/* or on [::]
# (dual stack) takes the port away from it
CLASH_ADDRS = ("127.0.0.1", "0.0.0.0", "*", "", "::", "::ffff:127.0.0.1")
_SS_PID = re.compile(r'\("([^"]+)",pid=(\d+)')
UPSTREAM_DNS = "1.1.1.1"
INT_DNS_TAG = "gw-dns-int"
INT_DNS_SERVERS = ("1.1.1.1", "1.0.0.1")
CREDENTIAL = "xray.json"
USER = net.XRAY_USER
NOLOGIN = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false")
FETCH_RETRY = 600                   # s: a download of xray that failed is not tried again sooner (people changes)
FETCH_TIMEOUT = 420
# 3.3.4: the xray of Kaskad is PINNED (as the cores of AmneziaWG, bin/VERSIONS.txt): the release EGRESS_XVER of install.sh,
# its file checked by SHA-256 (install.sh egress_xray_pin — the same sums; tests/test_334_xray.py keeps them equal). No
# xray of the server (/usr/local/bin, x-ui) and none from PATH: their version is not Kaskad's — a newer xray silently
# breaks keys (allowInsecure) and clients (REALITY minClientVer 26.3.27 cuts mihomo). Another version — only by
# changing these, install.sh and bin/VERSIONS.txt together, after a live check.
XRAY_VERSION = "26.3.27"
XRAY_SHA256 = {                     # uname -m -> SHA-256 of ``xray`` in Xray-linux-<arch>.zip of the release
    "x86_64": "8255dd939c34cf966cc91517b6324dd3c8d0bcf49ffac8beca049a38c46845ed",
    "aarch64": "c2d20a7045250497083afea0d79db0672f6c89a25aaaf37c92de034d6b764b04",
    "armv7l": "b7ea2a82185f0f7a59510b01b24a93cc3c45529dabbf3c97970ad66c49c6b882",
}
_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}
_pinned_cache = {}
_NAME = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}"
                   r"[A-Za-z0-9])?)+$")


def config_path():
    return os.path.join(paths.gw_dir(), "xray.json")


def run_binary():
    """The copy the service runs (a path its user can reach; /etc/kaskad is root-only)."""
    return os.path.join(paths.opt(), "gw", "xray")


def binary(runner):
    """Kaskad's own xray (/etc/kaskad/egress/xray); None if there is none. Never an xray of the server or of PATH."""
    p = paths.egress_xray()
    if os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return None


def arch():
    m = os.uname().machine
    return _ARCH.get(m, m)


def is_pinned(path):
    """The file is the xray of the pinned release for this machine (its SHA-256; cached by size, mtime, inode)."""
    want = XRAY_SHA256.get(arch())
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (path, st.st_size, st.st_mtime_ns, st.st_ino)
    got = _pinned_cache.get(key)
    if got is None:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return False
        got = h.hexdigest()
        _pinned_cache.clear()
        _pinned_cache[key] = got
    return bool(want) and got == want


def _fetch_stamp():
    return os.path.join(paths.gw_run_dir(), "xray-fetch.stamp")


def ensure_binary(runner, force=False):
    """xray for the gateway — SOCKS5 and the VLESS exits need it — even on a server where «Прокси для API
    Telegram» never ran (no pool, no /etc/kaskad/egress): Kaskad's own xray of the pinned release (XRAY_SHA256), else
    the egress downloader ``gokaskad --egress-ensure-xray`` (the pinned release of XTLS/Xray-core, SHA-256 checked;
    into /etc/kaskad/egress/xray; an own xray of another version is replaced only if the new one accepts the current
    settings — else it stays). A download that failed is tried again after FETCH_RETRY (``force``: now). Returns the
    path (an own xray of another version when the pinned one could not come); KaskadError — for people — when there
    is no own xray at all."""
    b = binary(runner)
    if b and is_pinned(b):
        return b
    stamp = _fetch_stamp()
    try:
        age = time.time() - os.stat(stamp).st_mtime
    except OSError:
        age = None
    why = ("gw_xray.why.recent", {}, None)     # (key, kw, words of the script): the journal and the person, each in its own
    if b and not force and age is not None and age < FETCH_RETRY:
        return b                                # tried a moment ago: the own xray of another version goes on
    if force or age is None or age >= FETCH_RETRY:
        res = runner.run([exits._script(runner), "--egress-ensure-xray"], timeout=FETCH_TIMEOUT)
        had, b = b, binary(runner)
        if b and is_pinned(b):
            try:
                os.unlink(stamp)
            except OSError:
                pass
            util.log_tr("gw_xray.log.fetched", path=b)
            return b
        if b:                                   # the own xray stays (no network, or the new one refused the settings)
            try:
                util.atomic_write(stamp, "%d\n" % res.rc, 0o600)
            except OSError:
                pass
            if not had:
                util.log_tr("gw_xray.log.fetched", path=b)
            return b
        lines = [ln.strip() for ln in (res.err + "\n" + res.out).splitlines() if ln.strip()]
        why = (None, None, util.redact(lines[-1][:200])) if lines else ("gw_xray.why.code", {"rc": res.rc}, None)
        try:
            util.atomic_write(stamp, "%d\n" % res.rc, 0o600)
        except OSError:
            pass
        with util.journal_lang():
            util.log_action(tr("gw_xray.log.fetch_failed", why=_words(why)))
    raise KaskadError(tr("gw_xray.no_xray_fetch", why=_words(why)))


def _words(why):
    """The words of a reason (key, kw, words of a program) in the language of the moment."""
    key, kw, raw = why
    return raw if key is None else tr(key, **kw) if kw else tr(key)


def install_binary(runner):
    """Copy the xray binary to /opt/kaskad/gw/xray (0755 root) when it differs (size, mtime). Returns the
    path. A running xray keeps its old file (rename); ``apply`` restarts it on a new copy (``_install_binary``)."""
    return _install_binary(runner)[0]


def _install_binary(runner):
    """-> (path, copied): ``copied`` — the file of the service changed now (the own xray was replaced: 3.3.4 pinned
    version; install.sh tested the current settings with it before)."""
    src = binary(runner)
    if not src:
        raise KaskadError(tr("gw_xray.no_xray"))
    dst = run_binary()
    try:
        s = os.stat(src)
        try:
            d = os.stat(dst)
            if (d.st_size, int(d.st_mtime)) == (s.st_size, int(s.st_mtime)) and (d.st_mode & 0o777) == 0o755:
                return dst, False
        except OSError:
            pass
        had = os.path.exists(dst)
        folder = os.path.dirname(dst)
        if not os.path.isdir(folder):
            os.makedirs(folder, 0o755)
        os.chmod(folder, 0o755)
        tmp = dst + ".new"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o755)
        os.utime(tmp, (s.st_atime, s.st_mtime))
        os.rename(tmp, dst)
    except OSError as e:
        raise KaskadError(tr("gw_xray.copy_failed", path=dst, err=e))
    return dst, had


def ensure_user(runner):
    """The system user ``kaskad-xray`` (no home, no shell); created if missing. Returns its uid."""
    uid = net.xray_uid()
    if uid is not None:
        return uid
    shell = next((s for s in NOLOGIN if os.path.exists(s)), NOLOGIN[0])
    res = runner.run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
                      "--shell", shell, USER])
    uid = net.xray_uid()
    if uid is None:
        raise KaskadError(tr("gw_xray.user_failed", user=USER,
                             err=(res.err or res.out or tr("gw_xray.why.code", rc=res.rc)).strip()[:200]))
    util.log_tr("gw_xray.log.user", user=USER, uid=uid)
    return uid


def _adapters(st):
    from . import registry
    for kind in model.enabled_kinds(st):
        ad = registry.get(kind)
        if ad is not None:
            yield kind, ad


def adapter_inbounds(st, sec):
    """Inbounds that enabled adapters add to xray (SOCKS5, VLESS)."""
    out = []
    for _, ad in _adapters(st):
        out.extend(copy.deepcopy(ad.xray_inbounds(st, sec) or []))
    return out


def stats_wanted(st, sec):
    """An adapter that reads xray's per-user counters (``xray_stats``: VLESS) has an inbound in the config now."""
    return any(getattr(ad, "xray_stats", False) and ad.xray_inbounds(st, sec) for _, ad in _adapters(st))


def hot_tags(st, sec):
    """Tags of the inbounds whose users come and go through xray's API (``xray_hot``: VLESS) — in the config now."""
    out = []
    for _, ad in _adapters(st):
        if getattr(ad, "xray_hot", False):
            out.extend(ib.get("tag") for ib in ad.xray_inbounds(st, sec) or [] if ib.get("tag"))
    return out


def _usable(st, pool_exits):
    return [e for e in pool_exits if exits.parsed(e) and e.get("outbound")
            and model.exit_pref(st, e["id"])["enabled"]]


def dns_via_pool(st, pool_exits):
    """dnsmasq of the node asks xray's dns-in (``dns.apply(via_pool=…)``) only when xray draws dns-in: a usable FOREIGN
    exit (T15 Q4, REV-s1-q4 З1: a node with Russian keys only has no dns-in — the DNS of the node goes as without a
    pool, never to a port nobody listens on)."""
    return any(exits.role_of(e) == "abroad" for e in _usable(st, pool_exits))


def needed(st, sec, pool_exits):
    return bool(_usable(st, pool_exits)) or bool(adapter_inbounds(st, sec))


def _tproxy_inbound(tag, port):
    # no sniffing: with IPOnDemand a sniffed domain (SNI, Host) would be resolved and checked INSTEAD of
    # the real destination — a fake SNI would open a private address, and every TLS connection would
    # cost a DNS lookup. No rule of ours routes TPROXY traffic by domain.
    return {"tag": tag, "listen": net.TPROXY_ADDR, "port": port, "protocol": "dokodemo-door",
            "settings": {"network": "tcp,udp", "followRedirect": True},
            "streamSettings": {"sockopt": {"tproxy": "tproxy"}}}


def _server_name(e):
    """The hostname of an xray exit's server (None for an IP address or anything odd) — ``exits.outbound_server``:
    ``vnext`` (vless, vmess), ``servers`` (trojan, shadowsocks), ``address`` (hysteria)."""
    host, _ = exits.outbound_server(e.get("outbound"))
    if not host:
        return None
    host = host.strip().lower().rstrip(".")
    return host if _NAME.match(host) and not host.replace(".", "").isdigit() else None


def local_names(usable):
    """Names xray resolves with the node's own resolver: the servers of the xray exits and the observatory's probe
    host — before any leg is up they cannot go through the pool."""
    names = set(n for n in (_server_name(e) for e in usable if exits.is_xray(e)) if n)
    probe = (urlsplit(exits.PROBE_URL).hostname or "").lower()
    if probe and _NAME.match(probe):
        names.add(probe)
    return sorted(names)


def dns_config(usable):
    servers = []
    names = local_names(usable) if usable else []
    if names:
        servers.append({"address": "localhost", "domains": ["full:" + n for n in names], "skipFallback": True})
    servers.extend(INT_DNS_SERVERS)
    return {"tag": INT_DNS_TAG, "queryStrategy": "UseIPv4", "disableFallbackIfMatch": True, "servers": servers}


# NAT64 and the unspecified address. No ``::ffff:0:0/96`` here (T-M6-qs330b №1): xray takes an IPv4-mapped prefix for
# IPv4 with the mask 96 and refuses the whole config («invalid network mask for router: 96»). It is not needed either:
# xray turns every IPv4-mapped destination into plain IPv4 before routing (common/net.IPAddress), so
# ``[::ffff:127.0.0.1]`` — the API of xray on the loopback — is caught by 127.0.0.0/8 like 127.0.0.1 itself.
MAPPED6 = ("64:ff9b::/96", "::/128")


def _xray_cidr(c):
    """A CIDR xray's router accepts: no IPv4-mapped IPv6 (see MAPPED6) — xray reads it as IPv4 with a mask over 32."""
    return not str(c).lower().startswith("::ffff:")


def blocked_ips(node_addrs=()):
    """Destinations no client of xray may open: private, CGNAT, link-local, multicast, loopback (v4 and v6; IPv4 in
    IPv6 such as ``[::ffff:127.0.0.1]`` is matched by its IPv4 range — xray unmaps it), NAT64 and the node's own
    addresses."""
    out = list(net.PRIVATE) + list(net.PRIVATE6) + [m for m in MAPPED6 if m not in net.PRIVATE6]
    out += [a for a in node_addrs if a not in out]
    return [a for a in out if _xray_cidr(a)]


def render(st, sec, pool_exits, node_addrs=(), decision=None):
    """The config dict of stage 1 (deterministic for the same input; ``_render_s1``: users pinned to exits, fail-block,
    the API always, probe-<id>, pool-socks, the shared lists ``ls-``). ``decision`` — the Decision of the tick
    (gw/assign.py, the collector); None — ``exits.decision(st)`` (pins.json and the snapshot, §2.4): every caller gets
    the config of stage 1, never the one of 3.3.5 (REV-s1-t5 С1)."""
    if decision is None:
        decision = exits.decision(st, pool_exits=pool_exits)
    return _render_s1(st, sec, pool_exits, node_addrs, decision)


def _render_335(st, sec, pool_exits, node_addrs=()):
    """The config of 3.3.5 — ONLY for the reference of T1 (tests/fixtures/gw/s1_t1_node_335.json: «Как раньше» against
    the code of 3.3.5); no caller of the gateway draws it (REV-s1-t5 С1)."""
    extra = adapter_inbounds(st, sec)
    usable = _usable(st, pool_exits)
    # the exits a route «exit:<id>» of a foreign inbound may name: foreign ones only, as exits.usable of the core does
    # (a Russian exit there is unfit — fail; its own chain is T15); the tag is the record's own (gx-/gr-, §10.1а)
    routable = dict((e["id"], e) for e in usable if exits.role_of(e) == "abroad")
    tags = [e["tag"] for e in usable]
    vless = [e for e in usable if exits.is_xray(e) and e.get("mark")]
    # gw-vless — the pool's balancer of the foreign xray exits only: a Russian exit (T15) never carries the pool
    vless_tags = [e["tag"] for e in vless if exits.role_of(e) == "abroad"]
    inbounds = [_tproxy_inbound("tproxy-in", TPROXY_PORT)]
    inbounds += [_tproxy_inbound(exits.tproxy_tag(e["id"]), exits.tproxy_port(e)) for e in vless]
    if tags:
        inbounds.append({"tag": "dns-in", "listen": "127.0.0.1", "port": DNS_PORT, "protocol": "dokodemo-door",
                         "settings": {"address": UPSTREAM_DNS, "port": 53, "network": "tcp,udp"}})
        inbounds.append({"tag": "metrics-in", "listen": "127.0.0.1", "port": METRICS_PORT,
                         "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}})
    stats = bool(extra) and stats_wanted(st, sec)
    hot = hot_tags(st, sec) if extra else []
    if stats:
        inbounds.append({"tag": "stats-in", "listen": "127.0.0.1", "port": STATS_PORT, "protocol": "dokodemo-door",
                         "settings": {"address": "127.0.0.1"}})
    inbounds.extend(extra)
    extra_tags = [ib.get("tag") for ib in extra if ib.get("tag")]
    clients = ["tproxy-in"] + [exits.tproxy_tag(e["id"]) for e in vless] + extra_tags
    outbounds = [copy.deepcopy(e["outbound"]) for e in usable]
    outbounds += [{"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}},
                  {"tag": "block", "protocol": "blackhole"}]
    if hot:          # xray's default outbound (the first) is block: a hot user between its rules and its chain goes nowhere
        outbounds = outbounds[-1:] + outbounds[:-1]
    balancers = {}

    def bal(name, selector, fallback):
        balancers.setdefault(name, {"tag": name, "selector": list(selector), "strategy": {"type": "leastPing"},
                                    "fallbackTag": fallback})
        return name

    def target(route, device=None):
        """A device route -> where its xray traffic goes (adapters' ``xray_routing`` call it; the device is not
        needed here — the pool is a balancer)."""
        if not isinstance(route, dict):
            route = {"via": route}
        via = str(route.get("via") or "pool")
        fail_out = "direct" if route.get("fail") == "direct" else "block"
        if via in ("block", "direct"):
            return {"outboundTag": via}
        if via.startswith("exit:"):
            eid = via[5:]
            e = routable.get(eid)
            if e is None:
                return {"outboundTag": fail_out}
            if fail_out == "direct":
                return {"balancerTag": bal("b-%s-direct" % eid, [e["tag"]], "direct")}
            return {"outboundTag": e["tag"]}
        if via != "pool" or not tags:
            return {"outboundTag": fail_out if via == "pool" else "block"}
        return {"balancerTag": bal("gw-all" if fail_out == "block" else "gw-all-direct", [exits.ABROAD_PREFIX], fail_out)}

    if hot and tags:           # every balancer a route of a hot user may name, used or not: a new user, a new route of
        target({"via": "pool", "fail": "block"})            # one — no new balancer, no restart (hot_plan)
        target({"via": "pool", "fail": "direct"})
        for e in usable:
            target({"via": "exit:" + e["id"], "fail": "direct"})
    rules = []
    if stats:
        rules.append({"type": "field", "inboundTag": ["stats-in"], "outboundTag": STATS_TAG})
    if tags:
        rules.append({"type": "field", "inboundTag": ["metrics-in"], "outboundTag": "metrics"})
        rules.append({"type": "field", "inboundTag": [INT_DNS_TAG], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX], "block")})
        rules.append({"type": "field", "inboundTag": ["dns-in"], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX], "block")})
    else:
        rules.append({"type": "field", "inboundTag": [INT_DNS_TAG], "outboundTag": "direct"})
    rules.append({"type": "field", "inboundTag": list(clients), "ip": blocked_ips(node_addrs), "outboundTag": "block"})
    rules.append({"type": "field", "inboundTag": list(clients), "domain": ["full:localhost"], "outboundTag": "block"})
    for e in vless:
        rules.append({"type": "field", "inboundTag": [exits.tproxy_tag(e["id"])], "outboundTag": e["tag"]})
    # wave 3, SOCKS5 and VLESS users: block -> always through the exit -> Russia directly, before their route rules (the
    # files roscomvpn-*.dat next to the binary; none there — no such rule, xray still starts)
    rules.extend(rules_apply.xray_rules(st, sec, target, list(_adapters(st)), chained=hot))
    chains = []
    for _, ad in _adapters(st):
        got = copy.deepcopy(ad.xray_routing(st, sec, target) or [])
        (chains if getattr(ad, "xray_hot", False) else rules).extend(got)
    closed = [t for t in extra_tags if t not in hot]
    if closed:                               # whatever an adapter's rules did not route: blocked
        rules.append({"type": "field", "inboundTag": closed, "outboundTag": "block"})
    if vless_tags:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "balancerTag": bal("gw-vless", vless_tags, "block")})
    elif tags:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX], "block")})
    else:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "outboundTag": "block"})
    rules.extend(chains)                     # the per-user chains last: the API appends new ones there too
    routing = {"domainStrategy": "IPOnDemand", "rules": rules}
    if balancers:
        routing["balancers"] = [balancers[k] for k in sorted(balancers)]
    # access "none": no line per connection in the journal — neither the panel's polls of the API every 10 s nor the
    # sites of the clients (T-M6 №9); errors and warnings stay. Nothing reads the access log of xray.
    cfg = {"log": {"loglevel": "warning", "access": "none"}, "dns": dns_config(usable), "inbounds": inbounds, "outbounds": outbounds,
           "routing": routing,
           "policy": {"levels": {"0": {"connIdle": 300, "handshake": 4, "uplinkOnly": 2, "downlinkOnly": 5}}}}
    if tags:
        cfg["observatory"] = {"subjectSelector": [exits.ABROAD_PREFIX], "probeURL": exits.PROBE_URL, "probeInterval": "10s",
                              "enableConcurrency": True}
        cfg["metrics"] = {"tag": "metrics"}
    if stats:                                  # per-user counters (users = devices of VLESS by their login)
        cfg["stats"] = {}
        cfg["api"] = {"tag": STATS_TAG, "services": ["StatsService"] + (list(HOT_SERVICES) if hot else [])}
        cfg["policy"]["levels"]["0"].update({"statsUserUplink": True, "statsUserDownlink": True})
    return cfg


def quota_off(decision):
    """Ids of the exits taken off by their quota with ``action: remove`` — not in the config of xray until the day of
    the reset (§2.7, §5.6, О8): ``decision["quota_off"]`` (the collector fills it from the health, T4)."""
    got = (decision or {}).get("quota_off") if isinstance(decision, dict) else None
    return set(str(x) for x in got or () if x)


def probe_port(e):
    """127.0.0.1:<port> of ``probe-<id>`` — 12700+N by the number of the line of the exit (§5.2)."""
    return PROBE_PORT_BASE + int(e["index"])


def probe_tag(eid):
    return "probe-" + eid


def _socks_inbound(tag, port):
    """A SOCKS inbound of the node itself (probe-<id>, pool-socks): no password (nft lets only root in, §5.2), no UDP."""
    return {"tag": tag, "listen": "127.0.0.1", "port": port, "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False}}


def _rule_tags(prefix, key, rules):
    h = hashlib.sha256(util.json_text(rules).encode("utf-8")).hexdigest()[:10]
    return ["%s%s-%s-%d" % (prefix, key, h, i) for i in range(len(rules))]


def _drawn(st, pool_exits, decision):
    """(usable exits drawn now, xray exits among them with their TPROXY and probe inbounds): the exits of every group
    (gx-/gr-) but those off by the quota (§2.7)."""
    off = quota_off(decision)
    drawn = [e for e in _usable(st, pool_exits) if e["id"] not in off]
    return drawn, [e for e in drawn if exits.is_xray(e) and e.get("mark") and e.get("index") is not None]


def _vless_live(st, pool_exits, decision, xr):
    """«по соединениям»: the tags of the xray exits of «Основная» a new connection may take now — those among the
    candidates of the kernel's spread (``assign.spread_cands``: the active tier, its best class; КТ1 Н7: xray and the
    kernel on one tier). [] — none (AmneziaWG only there, the xray ones dead or in another tier) or not the mode."""
    from . import assign, policy as P
    main = P.group(P.load(st, pool_exits), P.MAIN)
    if not (main and main.get("per_connection")):
        return []
    cands = set(assign.spread_cands((decision or {}).get("states") or {}, main))
    ids = P.exits_of(main)
    return [e["tag"] for e in xr if exits.role_of(e) == "abroad" and e["id"] in ids and e["id"] in cands]


def _vless_selector(st, pool_exits, decision, xr, live=None):
    """The selector of ``gw-vless`` (the balancer of 0x200, tproxy-in): with «по соединениям» (§4.5, SPEC-groups §2.6)
    — the xray exits of «Основная» a new connection may take now (``_vless_live``; all of «Основная» when none is:
    the kernel gives 0x200 to nobody then, the users of xray go to gw-all).
    Without the mode nobody goes through it (§2.8: the devices are pinned, 0x300+N) — the prefix ``gx-``, as gw-all:
    a list of tags here would make every change of the pool a change of the balancers — all the rules anew instead
    of the rules of one exit (§2.7). [] — no foreign xray exit."""
    from . import policy as P
    doc = P.load(st, pool_exits)
    main = P.group(doc, P.MAIN)
    if not any(exits.role_of(e) == "abroad" for e in xr):
        return []
    if not (main and main.get("per_connection")):
        return [exits.ABROAD_PREFIX]
    ids = P.exits_of(main)
    mine = [e for e in xr if exits.role_of(e) == "abroad" and e["id"] in ids]
    got = _vless_live(st, pool_exits, decision, xr) if live is None else live
    return got or [e["tag"] for e in mine] or [exits.ABROAD_PREFIX]


def _goes_direct(rules, balancers):
    """Some rule of the users (an adapter's ``xray_routing``: the route of a device or the last step of its group)
    sends its traffic out from the node itself — ``direct`` or a balancer falling back to it. The lists of «Россия
    напрямую» are not here: they let Russia go directly, never the names of everyone (Р-Г9; dnsmasq alike —
    ``exits.direct_needed``)."""
    for r in rules or []:
        if not isinstance(r, dict) or r.get("domain") or r.get("ip"):
            continue                         # a list of a chain (stage 1: shared, never here) — Russia, not the route
        if r.get("outboundTag") == "direct":
            return True
        b = balancers.get(r.get("balancerTag")) if r.get("balancerTag") else None
        if b and b.get("fallbackTag") == "direct":
            return True
    return False


def _not_direct(rules, balancers):
    """[(inboundTag, users)] of the plain rules of the users (no domain, no ip) that do not go out from the node — the
    users of «всё через выход»: with no exit drawn and someone «напрямую» (``gw-dns-int`` direct) they get a fail-block
    above every rule with IPs, so IPOnDemand never resolves their names past the exits (REV-337-last-sec Д-1)."""
    direct, out = set(), {}
    for r in rules or []:
        if not isinstance(r, dict) or r.get("domain") or r.get("ip") or not r.get("user"):
            continue
        ins = tuple(sorted(str(t) for t in r.get("inboundTag") or []))
        users = set(str(u) for u in r["user"])
        if _goes_direct([r], balancers):
            direct.update((t, u) for t in ins for u in users)
        else:
            out.setdefault(ins, set()).update(users)
    got = []
    for ins in sorted(out):
        users = sorted(u for u in out[ins] if not any((t, u) in direct for t in ins))
        if ins and users:
            got.append((list(ins), users))
    return got


def observatory_selector(drawn):
    """The ``subjectSelector`` of the observatory for the exits ``drawn``: «gx-» while a foreign exit is drawn, else []
    (no observatory). Never «gr-» (T-q круг 3, З-2; SPEC-groups §2.2): its probe is a FOREIGN one (cp.cloudflare.com) —
    a home router whose provider cuts Cloudflare would be «не отвечает» while it carries the Russian sites; «gr-» also
    took every Russian AmneziaWG leg (freedom with its mark) every 10 s and restarted xray on the first and the last
    Russian VLESS. A Russian xray exit is checked by its own probe through ``probe-<id>`` to the Russian targets of its
    group (``collect.probe_plan`` «socks», ``exits.socks_probe``)."""
    return [exits.ABROAD_PREFIX] if any(exits.role_of(e) == "abroad" for e in drawn or ()) else []


def _observatory_s(st, pool_exits):
    from . import policy as P
    return P.observatory_s(P.load(st, pool_exits), pool_exits) or 10


def _mip(st, pool_exits):
    """gw/mip.py ``plan`` (the cache of «Адреса сервера», files only); a failure — nothing of it (the config as before)."""
    try:
        return mip.plan(st, pool_exits)
    except Exception:                                   # noqa: BLE001 — the addresses never break the render
        return {}


def _render_s1(st, sec, pool_exits, node_addrs, decision):
    """The config of stage 1 (SPEC-stage1-exits §2.2–2.7, §5.2, §5.6):

    * a user of a hot inbound (VLESS, SOCKS5) — a chain of one rule to the target of its device:
      ``exits.target(st, device, decision)["xray"]`` — the exit it is pinned to (``gx-<id>``), ``direct``, ``block``
      (by choice) or ``fail-block`` (the last step of its group); no balancer per user (gw-all-direct, b-<eid>-direct
      are gone);
    * the lists of «Россия напрямую» / «всегда через выход» — shared rules ``ls-<hash>`` by target with the users
      (``rules.apply.xray_rules(shared)``), above the chains;
    * ``fail-block`` — a second blackhole before ``block`` (``block`` stays the last: the first of the hot inbounds);
    * per xray exit of any group: its outbound, ``tproxy-<id>``, ``probe-<id>`` (127.0.0.1:12700+N) and their rules
      ``px-<id>-…`` (blocked destinations, then the exit) — an exit comes and goes through the API (§2.7);
      an exit off by its quota (``decision["quota_off"]``) is not drawn at all;
    * ``pool-socks`` 127.0.0.1:12699 -> gw-all; the API (Handler, Routing, Stats) and ``stats`` always with exits or a
      hot inbound; ``statsOutbound*`` for the counters of the quotas (never ``statsInbound*``: T0 С0.13);
    * ``gw-vless`` — the xray exits of «Основная» (narrowed with «по соединениям»); the observatory at the interval of
      the policy (``policy.observatory_s``)."""
    extra = adapter_inbounds(st, sec)
    drawn, xr = _drawn(st, pool_exits, decision)
    tags = [e["tag"] for e in drawn]
    known = set(tags)
    # T15 Q4: the pool (gw-all, pool-socks, the DNS of xray and of the node, the observatory, «tproxy-in») — the foreign
    # exits only; a Russian exit (gr-, upstream-ru.txt) is drawn with its own inbounds but never makes «there are exits
    # of the pool» (SPEC-groups §2.2, §5, §7: DNS and «Проверить ссылку» — «Основная» only)
    pool_tags = [e["tag"] for e in drawn if exits.role_of(e) == "abroad"]
    routable = dict((e["id"], e) for e in drawn if exits.role_of(e) == "abroad")
    # the observatory — the foreign exits only (T-q круг 3, З-2): a Russian xray exit has its own probe (probe-<id>)
    obs_sel = observatory_selector(drawn)
    # the counters (/debug/vars) are read through metrics-in: the observatory's, and the traffic of a Russian xray
    # exit too (its quota, the statistics, the «busy» of «Лениво») — a node with Russian xray exits only (REV-q-r3 З-2)
    metrics_on = bool(obs_sel) or any(exits.role_of(e) == "ru" for e in xr)
    vless_live = _vless_live(st, pool_exits, decision, xr)
    vless_tags = _vless_selector(st, pool_exits, decision, xr, vless_live)
    hot = hot_tags(st, sec) if extra else []
    stats_users = bool(extra) and stats_wanted(st, sec)
    api = bool(tags) or bool(hot) or stats_users
    inbounds = [_tproxy_inbound("tproxy-in", TPROXY_PORT)]
    inbounds += [_tproxy_inbound(exits.tproxy_tag(e["id"]), exits.tproxy_port(e)) for e in xr]
    if pool_tags:
        inbounds.append({"tag": "dns-in", "listen": "127.0.0.1", "port": DNS_PORT, "protocol": "dokodemo-door",
                         "settings": {"address": UPSTREAM_DNS, "port": 53, "network": "tcp,udp"}})
    if metrics_on:
        inbounds.append({"tag": "metrics-in", "listen": "127.0.0.1", "port": METRICS_PORT,
                         "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}})
    if api:
        inbounds.append({"tag": "stats-in", "listen": "127.0.0.1", "port": STATS_PORT, "protocol": "dokodemo-door",
                         "settings": {"address": "127.0.0.1"}})
    if pool_tags:
        inbounds.append(_socks_inbound(POOL_SOCKS_TAG, POOL_SOCKS_PORT))
    inbounds += [_socks_inbound(probe_tag(e["id"]), probe_port(e)) for e in xr]
    inbounds.extend(extra)
    extra_tags = [ib.get("tag") for ib in extra if ib.get("tag")]
    clients = ["tproxy-in"] + ([POOL_SOCKS_TAG] if pool_tags else []) + extra_tags
    mp = _mip(st, pool_exits)                # Ш4: the address of each xray exit (sendThrough), Н4 — of «direct»
    outbounds = [copy.deepcopy(mip.xray_outbound(e, mp)) for e in drawn]
    direct = {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}}
    if mip.direct_send(mp):
        direct["sendThrough"] = mip.direct_send(mp)
    outbounds += [direct,
                  {"tag": FAIL_BLOCK, "protocol": "blackhole"},
                  {"tag": "block", "protocol": "blackhole"}]
    # xray's default outbound (the first) is block: a hot user between its rules and its chain goes nowhere; and never
    # a Russian exit (SEC-s1-q4 П-4: the home router of someone as the default of the gateway — only Russian ones drawn,
    # or the foreign ones off by the quota). A node without Russian exits keeps its first outbound as before.
    if hot or (drawn and exits.role_of(drawn[0]) != "abroad"):
        outbounds = outbounds[-1:] + outbounds[:-1]
    balancers = {}

    def bal(name, selector):
        balancers.setdefault(name, {"tag": name, "selector": list(selector), "strategy": {"type": "leastPing"},
                                    "fallbackTag": "block"})
        return name

    def target(route, device=None):
        """A route (and the device) -> where its xray traffic goes: the pin of the device by the decision (T4:
        ``exits.target``); without a device — the route alone (the pool: gw-all)."""
        if not isinstance(route, dict):
            route = {"via": route}
        via = str(route.get("via") or "pool")
        if via in ("block", "direct"):
            return {"outboundTag": via}
        if isinstance(device, dict) and device.get("id"):
            t = (exits.target(st, device, decision) or {}).get("xray") or {}
            got = t.get("outboundTag")
            if got in ("direct", "block", FAIL_BLOCK) or got in known:
                return {"outboundTag": got}
            if t.get("balancerTag") == "gw-all" and pool_tags:
                return {"balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])}
            if t.get("balancerTag") == exits.VLESS_BALANCER and pool_tags:
                # «по соединениям» (§4.5; КТ1 Н7, REV-s1-kt1a С1): gw-vless only with an xray exit a new connection
                # may take now (the kernel's tier and class); else — AmneziaWG only alive there, the xray ones dead or
                # in a reserve tier — gw-all, put by the collector on the best of «Основная» (§2.6): never a balancer
                # of dead legs falling back to block, never another tier than the kernel's
                if vless_live:
                    return {"balancerTag": bal("gw-vless", vless_tags)}
                return {"balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])}
            return {"outboundTag": FAIL_BLOCK}            # an exit this config does not have: closed, never a leak
        fail = "direct" if route.get("fail") == "direct" else FAIL_BLOCK
        if via.startswith("exit:"):
            e = routable.get(via[5:])
            return {"outboundTag": e["tag"] if e else fail}
        if via.startswith("group:"):         # a group, never block (REV-s1-t5 С1): its own exits drawn, else its fail
            from . import policy as P
            gid, grp, _ = exits.route_group(st, route, decision)
            if (grp or {}).get("fail") == "direct":
                fail = "direct"
            mine = sorted(routable[x]["tag"] for x in P.exits_of(grp or {}) if x in routable)
            if gid == P.MAIN and pool_tags:
                return {"balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])}
            return {"balancerTag": bal("gw-g-" + gid, mine)} if mine else {"outboundTag": fail}
        return {"balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])} if pool_tags else {"outboundTag": fail}

    def ru_out(device):
        """Q2 (SPEC-groups §4.1): the outbound of the Russian lists «напрямую» of ``device`` — ``exits.ru_target``:
        «direct», «gr-<id>» of its Russian exit, «fail-block» on the last step «блок» of its group; an exit this config
        does not have — «fail-block» (closed, never a leak); None — no Russian rule for it."""
        t = exits.ru_target(st, device, decision)
        if t is None:
            return None
        got = t["xray"].get("outboundTag")
        return got if got in ("direct", FAIL_BLOCK) or got in known else FAIL_BLOCK

    target.shared_lists = True
    target.ru = ru_out
    rules = []
    if api:
        rules.append({"type": "field", "inboundTag": ["stats-in"], "outboundTag": STATS_TAG})
    if metrics_on:
        rules.append({"type": "field", "inboundTag": ["metrics-in"], "outboundTag": "metrics"})
    if pool_tags:
        rules.append({"type": "field", "inboundTag": [INT_DNS_TAG], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])})
        rules.append({"type": "field", "inboundTag": ["dns-in"], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])})
    else:
        # no exit drawn (none, all off by the owner, all off by the quota «remove»): xray's own DNS is silent (Р-Г9,
        # REV-337-final-sec З-1/Д-2) — «direct» only when some user of SOCKS5/VLESS really goes directly (below)
        dns_int = {"type": "field", "inboundTag": [INT_DNS_TAG], "outboundTag": "block"}
        rules.append(dns_int)
    blocked = blocked_ips(node_addrs)
    first_ip = len(rules)                    # the fail-block of «всё через выход» goes here (no exit, someone direct)
    rules.append({"type": "field", "inboundTag": list(clients), "ip": blocked, "outboundTag": "block"})
    rules.append({"type": "field", "inboundTag": list(clients), "domain": ["full:localhost"], "outboundTag": "block"})
    for e in xr:                 # one exit — its own rules: it comes and goes through the API without the others
        own = [exits.tproxy_tag(e["id"]), probe_tag(e["id"])]
        px = [{"type": "field", "inboundTag": list(own), "ip": blocked, "outboundTag": "block"},
              {"type": "field", "inboundTag": list(own), "domain": ["full:localhost"], "outboundTag": "block"},
              {"type": "field", "inboundTag": list(own), "outboundTag": e["tag"]}]
        for r, t in zip(px, _rule_tags(PX_PREFIX, e["id"], px)):
            r["ruleTag"] = t
        rules.extend(px)
    if pool_tags:
        rules.append({"type": "field", "inboundTag": [POOL_SOCKS_TAG], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])})
    rules.extend(rules_apply.xray_rules(st, sec, target, list(_adapters(st)), chained=hot))
    chains = []
    users_direct = False
    users_rules = []
    for _, ad in _adapters(st):
        got = copy.deepcopy(ad.xray_routing(st, sec, target) or [])
        users_direct = users_direct or _goes_direct(got, balancers)
        users_rules.extend(got)
        (chains if getattr(ad, "xray_hot", False) else rules).extend(got)
    if not pool_tags and users_direct:            # a route or a last step «напрямую» of a user: its names must resolve
        dns_int["outboundTag"] = "direct"
        # …and only theirs: everyone else is closed before the first rule with IPs (IPOnDemand would resolve the names
        # of their requests through the direct DNS — off the node past the exits; REV-337-last-sec Д-1). Their own
        # rules already end in fail-block or block; a change of the list goes live as a change of the routing
        rules[first_ip:first_ip] = [{"type": "field", "inboundTag": ins, "user": users, "outboundTag": FAIL_BLOCK}
                                    for ins, users in _not_direct(users_rules, balancers)]
    closed = [t for t in extra_tags if t not in hot]
    if closed:                               # whatever an adapter's rules did not route: blocked
        rules.append({"type": "field", "inboundTag": closed, "outboundTag": "block"})
    if vless_tags:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "balancerTag": bal("gw-vless", vless_tags)})
    elif pool_tags:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "balancerTag": bal("gw-all", [exits.ABROAD_PREFIX])})
    else:
        rules.append({"type": "field", "inboundTag": ["tproxy-in"], "outboundTag": "block"})
    rules.extend(chains)                     # the per-user chains last: the API appends new ones there too
    for r in rules:                          # З9: a rule of the lists without users is a rule for EVERY user
        if str(r.get("ruleTag") or "").startswith(LS_PREFIX) and not r.get("user"):
            raise AssertionError("xray rule %s without users" % r.get("ruleTag"))
    routing = {"domainStrategy": "IPOnDemand", "rules": rules}
    if balancers:
        routing["balancers"] = [balancers[k] for k in sorted(balancers)]
    # the names of every usable exit, the ones off by the quota too: their return must not change the DNS of xray (a
    # restart) — a name resolved locally for an exit not drawn costs nothing
    cfg = {"log": {"loglevel": "warning", "access": "none"}, "dns": dns_config(_usable(st, pool_exits)),
           "inbounds": inbounds,
           "outbounds": outbounds, "routing": routing,
           "policy": {"levels": {"0": {"connIdle": 300, "handshake": 4, "uplinkOnly": 2, "downlinkOnly": 5}}}}
    if obs_sel:
        cfg["observatory"] = {"subjectSelector": obs_sel, "probeURL": exits.PROBE_URL,
                              "probeInterval": "%ds" % _observatory_s(st, pool_exits), "enableConcurrency": True}
    if metrics_on:
        cfg["metrics"] = {"tag": "metrics"}
    if api:                                  # the API always (bo of gw-all, the users, the pool), the counters of gx-
        cfg["stats"] = {}
        cfg["api"] = {"tag": STATS_TAG, "services": ["StatsService"] + list(HOT_SERVICES)}
        cfg["policy"]["system"] = {"statsOutboundUplink": True, "statsOutboundDownlink": True}
    if stats_users:                          # per-user counters (users = devices of VLESS by their login)
        cfg["policy"]["levels"]["0"].update({"statsUserUplink": True, "statsUserDownlink": True})
    return cfg


def gate_line(runner):
    """ExecStartPre (as root, ``+``) that fails while chain gw_pre of table ip kaskad is missing: no client
    is let in by a service of the gateway without the gw part of the table (gw/gate.py: the inbounds)."""
    nft_bin = runner.which("nft") or "/usr/sbin/nft"
    return "+/bin/sh -c 'exec %s list chain ip kaskad gw_pre >/dev/null'" % nft_bin


def unit_text(xray, runner=None, gate=None):
    gate = gate or ("+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre >/dev/null'"
                    if runner is None else gate_line(runner))
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: gateway xray (VLESS exits, DNS, SOCKS5)",
        "After=network-online.target kaskad-net.service",
        "Wants=network-online.target",
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        "Type=simple",
        "User=%s" % USER,
        "Group=%s" % USER,
        "AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE",
        "CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE",
        "NoNewPrivileges=yes",
        "LoadCredential=%s:%s" % (CREDENTIAL, config_path()),
        "ExecStartPre=%s" % gate,
        "ExecStart=%s run -c %%d/%s" % (xray, CREDENTIAL),
        "Restart=always",
        "RestartSec=5",
        "LimitNOFILE=1048576",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def apply(st, sec, runner, pool_exits=None, decision=None, seq=None, fp=None, defer_restart=False, lock_wait=60,
          skip_rejected=False, cut_ru=False):
    """Bring kaskad-gw-xray to the config of ``st`` (``render(…, decision)``): through the API when only what the API
    changes differs (``hot_plan``), else ``xray -test`` and a restart; stop the service when xray is not needed.

    Under the xray lock (``paths.xray_apply_lock()``, flock; ``lock_wait`` s — the collector 5, ``ops._apply`` 60): the
    comparison with the file, ``xray -test``, the file, the calls of the API or the restart — two processes never mix
    their changes (§2.5). The state lock is never taken here (its order: the state first, then xray).

    ``seq`` — the number of the render (``next_seq`` under the state lock); lower than the ``seq`` of the applied one
    (``xray.decision``) -> dropped, {"stale": True}: xray runs a fresher one. ``fp`` — the fingerprint of the decision
    the config is drawn by. After an applied change (or none needed) ``xray.decision`` = {"fp", "seq"} (§2.7).
    ``decision`` None — ``exits.decision(st)`` (§2.4; a change under the state lock — ``apply_now``). ``defer_restart`` — a restart is needed but must wait (the storm, §2.5): the file is NOT replaced,
    {"deferred": True}; a later call does it (ANSWER — a write of the panel: only a config ``xray -test`` takes, the
    restart right after its answer, ``after_answer``; STORM — the collector: ``not restart_due()``, asked under the
    xray lock, so a mark set by a write it waited for counts). ``skip_rejected`` — a text ``xray -test`` refused less
    than REJECT_RETRY_S ago is refused again without the test (the collector, §2.7).

    Returns {"needed", "changed", "started", "restarted", "stopped"} + "hot" {kind: n}, "tested", "deferred", "stale",
    "lock_timeout", "xports_missing" (no gw_xports in the live table: the file is not replaced — the table first, REV-s1-b1
    З1), "warnings", "notes", "restart_owed" + "ru_open" (B7: the mark ``pending`` stays — not noted as applied, the next
    call restarts or closes again). Raises KaskadError (nothing replaced) if xray refuses the config or the file cannot be
    written; any failure closes «Россия напрямую» first (``_close_ru``) — one before the xray lock too (the binary,
    the user, the render: ``_failed_before``, круг 2 -sec Н-2), and a config with direct Russia without the permission
    ⚠РФ is never applied (``ru_guard``, D-136: ``data["ru_guard"]``). «Not needed» — the stop under the xray lock too
    (``_stop_locked``)."""
    pool_exits = exits.pool(runner, roles=exits.ROLES) if pool_exits is None else pool_exits
    out = {"needed": needed(st, sec, pool_exits), "changed": False, "started": False, "restarted": False,
           "stopped": False}
    if not out["needed"]:
        return _stop_locked(runner, out, seq, fp, lock_wait)
    cfg = None
    try:                                       # B7 круг 2 (-sec Н-2): a failure before the lock closes too
        xray, copied = _install_binary(runner)
        ensure_user(runner)
        try:                                   # wave 3: the lists of RoscomVPN for SOCKS5 users (before -test)
            out["assets"] = rules_apply.sync_assets(st, sec, list(_adapters(st)))
        except OSError as e:
            out["assets_error"] = str(e)
        if decision is None:                   # one decision for the render and its redraw after a healed pool
            decision = exits.decision(st, pool_exits=pool_exits)
        cfg = render(st, sec, pool_exits, net.node_addresses(runner, st), decision)
        ru_guard(st, cfg)                      # D-136: the last line — no direct Russia without the consent ⚠РФ
        text = util.json_text(cfg)
    except Exception as e:                     # noqa: BLE001
        raise _failed_before(st, sec, runner, pool_exits, decision, cfg, out, e, lock_wait)
    lock = _lock(lock_wait)
    if lock is None:
        out["lock_timeout"] = True             # the table and the state are applied; the collector catches up (§2.5)
        out.setdefault("warnings", []).append(tr("gw_ops.warn.xray_deferred"))
        util.log_tr("gw_xray.log.lock_timeout", s=lock_wait)
        return out
    try:
        done = applied()
        if seq is not None and done.get("seq") is not None and int(seq) < int(done["seq"]):
            out["stale"] = True                # a fresher render is in xray already: draw anew on the next tick
            return out
        ran = {}
        try:
            done = _apply_locked(st, sec, runner, cfg, text, xray, copied, decision, defer_restart, skip_rejected,
                                 out, ran, cut_ru=cut_ru)
        except Exception as e:                 # noqa: BLE001 — B7 (REV-s1-b6 Б1, -sec П-1): ANY failure closes
            # REV-s1-b5 З2: against what RAN before the file was written (``ran["running"]``) as well as the disk — a
            # failure between the rename and the restart (enable, daemon-reload) leaves the old process running; an
            # OSError (ENOSPC, EROFS of the file, the rename) closes as a refusal does
            _drop_tmp()
            n = _close_ru(runner, ran.get("want", cfg), out, ran)
            if isinstance(e, OSError):
                e = KaskadError(tr("gw_xray.write_failed", err=e))
            if n:
                e.data = dict(getattr(e, "data", None) or {}, ru_closed=n)     # said by ops._apply with the error
            elif out.get("ru_open"):
                e.data = dict(getattr(e, "data", None) or {}, ru_open=out["ru_open"])
            raise e
        if not done:
            if out.get("xports_missing"):
                _close_ru(runner, ran.get("want", cfg), out, ran, why="xports")     # REV-s1-b6-sec П-2
            return out
        if "cfg" in ran:                       # the ports xray dropped leave gw_xports only now (REV-s1-t45 П2)
            net.xports_settle(runner, guarded_ports(ran["cfg"]))
        if pending() is not None:
            out["restart_owed"] = True         # B7: what runs is not known to be the file — drawn again next tick
            _narrow(ru_direct_paths(ran.get("cfg", cfg)))
            n = ru_open()
            if n:
                out["ru_open"] = n
        elif fp is not None or seq is not None:
            note_applied(fp, seq)
        return out
    finally:
        _unlock(lock)


def ru_guard(st, cfg):
    """D-136, the last line (B7 круг 2): a config that sends anyone directly by the lists of Russia — or by any other
    matcher (``direct_matched``, REV-s1-b7-sec-2 Н-1) — while «Россия
    напрямую» is not allowed on the node (``rfgate.allowed``: no consent ⚠РФ, no mark of an updated node) is never
    applied — whatever code drew it. Raises KaskadError with ``data["ru_guard"]`` (the number of such paths); the
    caller closes (``_failed_before``: nothing direct is wanted) and the owner gets the event ``ru_guard``."""
    from . import ruguard
    bad = ru_direct_paths(cfg) | direct_matched(cfg)   # any direct rule with a matcher (-sec-2 Н-1), not the lists only
    if bad and not ruguard.allowed(st):
        ruguard.trip("xray", len(bad))
        raise KaskadError(tr("gw_xray.ru_guard", n=len(bad)), data={"ru_guard": len(bad)})


def _failed_before(st, sec, runner, pool_exits, decision, cfg, out, e, lock_wait):
    """A failure before the xray lock (the binary, the user, the decision, the render, the guard of D-136) closes
    «Россия напрямую» as one under the lock does (-sec Н-2): what is wanted is the render when it was drawn and passed
    the guard, else nothing direct. -> the exception to raise (a KaskadError with ``data`` ru_closed / ru_open)."""
    guard = isinstance(getattr(e, "data", None), dict) and e.data.get("ru_guard")
    want = cfg if cfg is not None and not guard else None
    if cfg is None:
        try:                                   # the binary or the user failed: the render still says what is wanted
            want = render(st, sec, pool_exits, net.node_addresses(runner, st), decision)
            ru_guard(st, want)
        except Exception:                      # noqa: BLE001 — not known: nothing direct is wanted
            want = None
    if not isinstance(e, KaskadError):
        e = KaskadError(tr("gw_xray.prepare_failed", err=e))
    lock = _lock(lock_wait)
    if lock is None:                           # another apply holds xray: it closes (it draws the same state)
        return e
    try:
        n = _close_ru(runner, want, out)
    finally:
        _unlock(lock)
    data = dict(getattr(e, "data", None) or {})
    if n:
        data["ru_closed"] = n
    elif out.get("ru_open"):
        data["ru_open"] = out["ru_open"]
    e.data = data
    return e


def _drop_tmp():
    """No ``xray.new.json`` is left by any failure (a test of the next call would see a stranger)."""
    try:
        os.unlink(os.path.join(os.path.dirname(config_path()), "xray.new.json"))
    except OSError:
        pass


def ru_direct_paths(cfg):
    """{(inbound tag, user)} the config ``cfg`` sends DIRECTLY by the lists of Russia (the rules «direct» of
    ``rules.apply`` naming ``ext:roscomvpn-…``: the direct group and the networks of Russia) — or to a RUSSIAN exit
    («gr-<id>», Q2: the home router of a Russian group is «Россия напрямую» for D-136 as the node's own address is;
    SEC-s1-q2 С-1). A route «напрямую» chosen for a device is not here: it names no list."""
    out = set()
    rules = ((cfg or {}).get("routing") or {}).get("rules") if isinstance(cfg, dict) else None
    marks = tuple("ext:%s:" % a for a in (rules_apply.GEOSITE_ASSET, rules_apply.GEOIP_ASSET))
    for r in rules if isinstance(rules, list) else ():
        if not isinstance(r, dict) or not _ru_out_tag(r.get("outboundTag")):
            continue
        names = [x for x in list(r.get("domain") or []) + list(r.get("ip") or []) if isinstance(x, str)]
        if not any(x.startswith(marks) for x in names):
            continue
        users = r.get("user") if isinstance(r.get("user"), list) else [None]
        for tag in r.get("inboundTag") or [None]:
            for u in users:
                out.add((tag, u))
    return out


def _ru_out_tag(t):
    """«direct» or the outbound of a Russian exit («gr-<id>», any protocol): where «Россия напрямую» goes (D-136)."""
    return t == "direct" or (isinstance(t, str) and t.startswith(exits.RU_PREFIX))


def _direct_tags(cfg):
    """The tags of the outbounds that go out from the node itself — ``freedom`` without a mark or an interface (a
    freedom with ``sockopt.mark`` is a leg of an AWG exit) — and every outbound of a RUSSIAN exit («gr-<id>»: the
    freedom with the mark of its AWG leg or a vless; SEC-s1-q2 С-1 — Russia through a home router needs the consent
    ⚠РФ as Russia from the node's address does)."""
    out = set()
    for ob in (cfg or {}).get("outbounds") or [] if isinstance(cfg, dict) else ():
        if isinstance(ob, dict) and isinstance(ob.get("tag"), str) and ob["tag"].startswith(exits.RU_PREFIX):
            out.add(ob["tag"])
            continue
        if not isinstance(ob, dict) or ob.get("protocol") != "freedom" or not isinstance(ob.get("tag"), str):
            continue
        ss = ob.get("streamSettings")
        so = (ss.get("sockopt") or {}) if isinstance(ss, dict) else {}
        if not (isinstance(so, dict) and (so.get("mark") or so.get("interface"))):
            out.add(ob["tag"])
    return out


def direct_matched(cfg):
    """{(inbound tag, user)} of EVERY rule that sends some destinations (any ``domain`` / ``ip`` matcher, not only the
    lists of Russia) straight out of the node (``_direct_tags``) — REV-s1-b7-sec-2 Н-1: the last line knows no form of
    «Россия напрямую» but this. Without the permission ⚠РФ the render draws no such rule at all (the lists rules of
    ``rules.apply`` are the only ones, and the route «напрямую» of a device is a rule without a matcher); ``None`` —
    a rule without ``inboundTag`` / ``user`` (every inbound, every user)."""
    out = set()
    tags = _direct_tags(cfg)
    rules = ((cfg or {}).get("routing") or {}).get("rules") if isinstance(cfg, dict) else None
    for r in rules if isinstance(rules, list) else ():
        if not isinstance(r, dict) or r.get("outboundTag") not in tags:
            continue
        if not (r.get("domain") or r.get("ip")):
            continue
        users = r.get("user") if isinstance(r.get("user"), list) and r.get("user") else [None]
        inb = r.get("inboundTag") if isinstance(r.get("inboundTag"), list) and r.get("inboundTag") else [None]
        for tag in inb:
            for u in users:
                out.add((tag, u))
    return out


def _close_ru(runner, cfg, out, ran=None, why=None):
    """The new config ``cfg`` was NOT put in place (xray refused it, nginx of the site, no gw_xports, a failure between
    the file and the restart) while the config that RUNS (``ran["running"]`` — taken before the file was written,
    REV-s1-b5 З2) or the one on the disk (what a reboot or ``gw check --repair`` starts) sends Russia directly to
    someone ``cfg`` no longer does (the choice «Россия напрямую» or the consent ⚠РФ went, the device or the person is
    off or gone, another route; SEC-s1-r2 З2): xray is stopped and that file goes. SOCKS5 and VLESS stop until xray
    takes a config of the state — never «Россия напрямую» without the choice. Under the xray lock.

    xray still running after ``disable --now`` (REV-s1-b5 П1): ``stop``, then ``kill``. «Stopped» is only a KNOWN
    ``inactive`` / ``failed`` (``gsys.unit_stopped``, REV-s1-b6-sec Н-1); else, or when the file of direct rules could
    not be removed — ``out["ru_open"]`` and an honest warning instead of «stopped». What may run is kept in the mark
    (``pending``, B7): the next call closes again even when the file on the disk already is the new one (REV-s1-b6 №1)
    and the render is not noted as applied. ``why`` "xports" — no gw_xports: one text instead of «not updated …
    catches up» next to «stopped» (П3).
    -> the number of paths closed (0 — nothing was closed)."""
    ran = ran or {}
    want = ru_direct_paths(cfg)
    may = ru_direct_paths(_read_config(config_path())) | (pending() or set())
    if "running" in ran:
        may |= ru_direct_paths(ran["running"])
    gone = may - want
    if not gone:
        _narrow(want)                          # the choice came back: the mark no longer says «open» (придирка 2)
        return 0
    # B7 (REV-s1-b6-sec Н-1): the mark first — what may run stays known to the next call whatever happens below; then
    # the file goes BEFORE the stop, so neither a reboot nor the watchdog (``net check --repair``, ``restart`` of a
    # needed xray without the xray lock, gw/ops.py) starts xray with it in between (LoadCredential fails without it).
    # The mark is no point of failure (круг 2, -sec С-1): no place took it — the file is renamed, its paths the mark
    marked = _note_pending(may, open_n=len(gone))
    left = not _take_file(keep=not marked)
    left = left and bool(ru_direct_paths(_read_config(config_path())) - want)
    stop(runner)
    for argv in (["systemctl", "stop", UNIT], ["systemctl", "kill", "--signal=SIGKILL", UNIT]):
        if gsys.unit_stopped(runner, UNIT):
            break
        runner.run(argv)
    stopped = _wait_stopped(runner)
    if left or not stopped:
        # KNOWN stopped only (inactive / failed): «activating», an unreachable bus, a timeout — Russia may still go
        # directly; the mark stays, the next call closes again and the render is not noted as applied
        out["ru_open"] = len(gone)
        if stopped:
            text = tr("gw_xray.warn.ru_stopped_left")
        else:
            text = tr("gw_xray.warn.ru_open") + (" " + tr("gw_xray.warn.ru_file_left") if left else "")
        out.setdefault("warnings", []).append(text)
        util.log_tr("gw_xray.log.ru_open" if not stopped else "gw_xray.log.ru_left", n=len(gone))
        return 0
    _clear_pending()                           # nothing runs, no file of direct rules: the next start reads the state
    net.xports_settle(runner, ())                  # its ports leave gw_xports only now (REV-s1-t45 П2)
    out["ru_closed"] = len(gone)
    warns = out.setdefault("warnings", [])
    if why == "xports":
        warns[:] = [w for w in warns if w != tr("gw_xray.warn.no_xports")]
        warns.append(tr("gw_xray.warn.ru_closed_xports"))
    else:
        warns.append(tr("gw_xray.warn.ru_closed"))
    util.log_tr("gw_xray.log.ru_closed", n=len(gone))
    return len(gone)


def _take_file(keep=False):
    """The file of the config off the name the unit reads (``LoadCredential``): removed, or renamed to
    ``xray.closed.json`` when ``keep`` (no mark was written: the file is the mark) or when it cannot be removed. -> True
    when no file is left under that name."""
    p = config_path()
    if not os.path.lexists(p):
        return True
    rename = (lambda: os.replace(p, closed_path()))
    remove = (lambda: os.unlink(p))
    hows = (rename, remove) if keep else (remove, rename)
    if os.path.lexists(closed_path()) and \
            ru_direct_paths(_read_config(p)) <= ru_direct_paths(_read_config(closed_path())):
        hows = (remove,)                       # the closed file already says what may run (придирка 1): kept as it is
    for how in hows:
        try:
            how()
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            util.log_tr("gw_xray.log.api_file", err=e)
    return False


KILL_WAIT_S = 5.0                      # ``systemctl kill`` does not wait for the process (придирка 1 REV-s1-b7)


def _wait_stopped(runner, wait=None):
    """The unit KNOWN stopped, asked again for up to KILL_WAIT_S s: after SIGKILL ``ActiveState`` is still ``active`` /
    ``deactivating`` for a moment."""
    end = time.monotonic() + (KILL_WAIT_S if wait is None else wait)
    while True:
        if gsys.unit_stopped(runner, UNIT):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def _stop_locked(runner, out, seq, fp, lock_wait):
    """«xray не нужен» under the xray lock (REV-s1-b1 П2, SEC-s1-b1 П-1): a render older than the applied one never stops
    the xray a fresher one runs (``ops._apply`` started it with exits a moment ago), and gw_xports shrinks only while no
    other process opens its ports."""
    lock = _lock(lock_wait)
    if lock is None:
        out["lock_timeout"] = True             # the collector catches up (§2.5)
        out.setdefault("warnings", []).append(tr("gw_ops.warn.xray_deferred"))
        util.log_tr("gw_xray.log.lock_timeout", s=lock_wait)
        return out
    try:
        if not _fresher(seq):
            out["stale"] = True                # a fresher render is in xray already: it is not stopped
            return out
        out["stopped"] = stop(runner)
        if not gsys.unit_stopped(runner, UNIT):
            # B7 (REV-s1-b6-sec Н-1): not KNOWN stopped — what may run sends Russia directly: stop, kill, the mark
            _close_ru(runner, None, out)
        if gsys.unit_stopped(runner, UNIT):
            _clear_pending()
            net.xports_settle(runner, ())              # its ports leave gw_xports only now (REV-s1-t45 П2)
            if fp is not None or seq is not None:
                note_applied(fp, seq)                  # REV-s1-t45 П1: not drawn again (and stopped) every tick
        return out
    finally:
        _unlock(lock)


def _fresher(seq):
    """``seq`` is not older than the number of the render xray runs (``applied``)."""
    done = applied()
    try:
        return seq is None or done.get("seq") is None or int(seq) >= int(done["seq"])
    except (TypeError, ValueError):
        return True


def applied_ok(res):
    """The result of ``apply`` says xray of the gateway runs the config drawn from its pool now (or is not needed):
    no error, nothing deferred, stale, waiting for its lock or for gw_xports, no restart owed (REV-s1-q4-2 С-1)."""
    return isinstance(res, dict) and not any(res.get(k) for k in (
        "error", "lock_timeout", "stale", "deferred", "xports_missing", "restart_owed", "ru_open"))


def apply_now(st, sec, runner, pool_exits, lock_wait=60):
    """``apply`` of a change made under the STATE lock (``gw/ops._apply``, ``rules/ops.apply``; §2.4, §2.5): the
    decision as the collector left it (``exits.decision``: pins.json and the snapshot — no move is undone), the number
    of the render taken now (the state lock orders the numbers) and its fingerprint (``collect.fingerprint``: the
    collector does not draw the same again). Under ``after_answer`` (a write of the panel) a restart waits for its
    answer (ANSWER; the mark of it is set under the xray lock). -> (the result of ``apply``, the decision)."""
    from . import collect
    dec = exits.decision(st, pool_exits=pool_exits)
    seq = next_seq()
    fp = collect.fingerprint(st, dec, pool_exits)
    ans = getattr(_AFTER, "box", None) is not None
    return apply(st, sec, runner, pool_exits, decision=dec, seq=seq, fp=fp, lock_wait=lock_wait, cut_ru=True,
                 defer_restart=ANSWER if ans else False), dec


def _apply_locked(st, sec, runner, cfg, text, xray, copied, decision, defer_restart, skip_rejected, out, ran=None,
                  cut_ru=False):
    """The steps under the xray lock. -> False when nothing may be noted as applied (deferred). ``ran`` (a dict) gets
    "cfg" — the config xray runs after it (the one on the disk; redrawn when a healed pool changed it)."""
    ran = {} if ran is None else ran
    path = config_path()
    restart = False
    active = gsys.unit_active(runner, UNIT)
    owed = pending() is not None               # B7: what runs is not known to be the file (REV-s1-b6 №1, Б2)
    try:
        if util.read_text(path) != text:
            old = _read_config(path)
            ran["running"] = old               # what runs (and boots) before the file changes (REV-s1-b5 З2)
            tags = hot_tags(st, sec)
            plan = hot_plan(old, cfg, tags, modes=add_modes(st, sec), seen=_hy2_seen(runner)) \
                if active and not owed else None   # owed: the running config is not ``old`` — a restart
            refused = None                     # ANSWER: the refusal of ``xray -test`` — not asked a second time below
            # under the xray lock (П3 З-2); an address it sends from gone — no window of the storm (П6); back — the
            # window as any change (П7, REV-3315-p256 З-1: a flapping address restarted xray every round)
            defer = (not restart_due(sends=sends_changed(old, cfg))) if defer_restart == STORM else defer_restart
            if plan is None and defer and active and not owed and isinstance(old, dict) \
                    and not ru_direct_paths(old) - ru_direct_paths(cfg):
                # never deferred when the new config takes direct rules of Russia away (REV-s1-b5 П4); after the
                # answer of the panel (ANSWER) — only a config xray takes: a refusal goes the usual way, in the answer
                refused = _takes(runner, xray, path, text) if defer == ANSWER else None
                if refused is None:
                    out["deferred"] = True     # file on disk = what runs: the next call restarts once the gap passed
                    if defer == ANSWER:
                        _after_owe(getattr(_AFTER, "box", None))    # under this lock: the collector waiting sees it
                        if leaves(old, cfg, tags):
                            out["notes"] = [tr("gw_xray.cut_note")]     # said in the answer: the restart still comes
                    return False
            pure = plan is not None and _strip(old, tags) == _strip(cfg, tags)
            tmp = os.path.join(os.path.dirname(path), "xray.new.json")      # xray picks the format by extension
            util.atomic_write(tmp, text, 0o600)
            if not pure:                       # the base changed: the second xray with the geo files checks it first
                recent = skip_rejected and _refused_recently(text) is not None
                try:
                    if refused is not None and not isinstance(refused, _NotTested):
                        raise refused          # П3 З-4: this very text was refused a moment ago — straight to healing
                    _test(runner, xray, tmp, text, skip_rejected)
                except KaskadError:
                    if recent or not _healed(runner, xray):  # refused a moment ago: no test, no healing again
                        raise
                    # REV-s1-t11-2 С2-2: a line of the pool xray refuses no longer «poisons» the config — built
                    # again (by the same decision) without it (or in the form of 3.3.5) and asked once more; the API
                    # plan was drawn against the poisoned config — xray restarts instead
                    cfg = render(st, sec, exits.pool(runner, roles=exits.ROLES), net.node_addresses(runner, st), decision)
                    ru_guard(st, cfg)          # D-136: the redrawn config passes the last line too
                    ran["want"] = cfg
                    text = util.json_text(cfg)
                    util.atomic_write(tmp, text, 0o600)
                    plan = None
                    _test(runner, xray, tmp, text, False)
                out["tested"] = True
            try:
                _front().before_xray(runner, cfg)      # nginx of the site behind xray before xray takes 443
            except KaskadError as e:
                os.unlink(tmp)
                raise KaskadError(tr("gw_xray.site_failed", err=e))
            guard = guarded_ports(cfg)
            if guard and not net.xports_add(runner, guard):  # its new ports closed to the node's users BEFORE they open
                # REV-s1-b1 З1, SEC-s1-b1 З-1: no gw_xports in the live table (3.3.5, ``nft.apply`` failed) or nft
                # refused — the config of stage 1 would open pool-socks and probe-<id> to every user of the node: the
                # file stays as it is (and what runs with it), the number stays wanted; the table comes first
                os.unlink(tmp)
                out["xports_missing"] = True
                out.setdefault("warnings", []).append(tr("gw_xray.warn.no_xports"))
                util.log_tr("gw_xray.log.no_xports")
                return False
            # B7: the mark BEFORE the file changes — a failure after the rename (enable, daemon-reload, the restart)
            # leaves the old process running a config the disk no longer has; the next call restarts, not «equal».
            # A mark no place takes is no failure (круг 2, -sec С-1): the closing below has ``ran["running"]``
            if not _note_pending(ru_direct_paths(old)) and ru_direct_paths(old) and not os.path.lexists(closed_path()):
                # REV-s1-b7-2 придирка 1: no place took the mark — the file that RUNS becomes the mark
                # (``xray.closed.json`` = ``old``), not the new one a later closing would rename; an OSError here
                # closes below (``ran["running"]``)
                os.replace(path, closed_path())
            os.rename(tmp, path)
            out["changed"] = True
            out["before"] = True
            if plan is not None:
                plan["adapters"] = hot_adapters(st, sec)
            if plan is not None and hot_apply(runner, xray, plan, bo=(decision or {}).get("gw_all")):
                ran["running"] = cfg           # the running xray took it through the API
                _clear_pending()
                out["hot"] = dict((k, len(v)) for k, v in plan.items() if isinstance(v, list) and v)
                if plan.get("routing") is not None:
                    out["hot"]["routing"] = 1
                _hy2_note(runner, cfg)
                lost = ru_lost(old, cfg) if cut_ru and isinstance(old, dict) else set()
                if lost:                       # T-q Н-5x: what went through the old Russian router goes, as the kernel's
                    out["ru_cut"] = cut_ru_conns(runner, old, lost)
            else:
                restart = True
                if isinstance(old, dict) and leaves(old, cfg, tags):
                    out["notes"] = [tr("gw_xray.cut_note")]
        elif owed:
            restart = True                             # the file is the render, the process may not be: run the file
        if copied and not restart and active:
            restart = True                             # a new xray (the pinned version came): run it, not the old file
            out["binary"] = True
        if not out.pop("before", False):
            _front().before_xray(runner, cfg)          # the same config, xray (re)starting: 443 free for it first
        r = gsys.ensure_unit(runner, UNIT, unit_text(xray, gate=gate_line(runner)), restart=restart)
        ran["cfg"] = cfg
        if r["started"] or r["restarted"]:
            ran["running"] = cfg               # runs the file on the disk now
            _clear_pending()
        out["started"], out["restarted"] = r["started"], r["restarted"]
        if r["started"] or r["restarted"]:
            _restarted(runner, cfg)
    finally:
        w = _front().after_xray(runner)                # nginx follows what xray runs now
        if w:
            out.setdefault("warnings", []).append(w)
    return True


def _takes(runner, xray, path, text):
    """ANSWER (``after_answer``): ``xray -test`` of the config ``text`` (its file removed either way) -> None — taken,
    else the refusal (KaskadError) for the usual path; the file not written — a KaskadError the usual path does not
    reuse (it writes and tests itself)."""
    tmp = os.path.join(os.path.dirname(path), "xray.new.json")
    try:
        util.atomic_write(tmp, text, 0o600)
        _test(runner, xray, tmp, text, False)
        return None
    except KaskadError as e:
        return e
    except OSError as e:
        return _NotTested(str(e))
    finally:
        _drop_tmp()


class _NotTested(KaskadError):
    """``_takes`` could not even write the file: no refusal of xray — the usual path writes and tests again."""


def _refused_recently(text):
    """The record of ``xray.rejected`` when this very text was refused less than REJECT_RETRY_S ago, else None."""
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    rej = rejected_info()
    if rej.get("hash") == h and time.time() - float(rej.get("at") or 0) < REJECT_RETRY_S:
        return rej
    return None


def _test(runner, xray, tmp, text, skip_rejected):
    """``xray -test`` of the new file; a refusal is remembered (``xray.rejected``) and raised for people."""
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    rej = rejected_info()
    recent = _refused_recently(text) if skip_rejected else None
    if recent is not None:
        os.unlink(tmp)
        raise KaskadError(tr("gw_xray.config_refused", err=recent.get("error") or "?"))
    res = runner.run([xray, "-test", "-c", tmp], timeout=30)
    if res.ok:
        return
    try:
        os.unlink(tmp)
    except OSError:
        pass
    lines = [ln.strip() for ln in (res.out + "\n" + res.err).splitlines() if ln.strip()]
    err = util.redact(" | ".join(lines[-3:]) or tr("gw_xray.why.code", rc=res.rc))
    try:
        util.ensure_run_dir(os.path.dirname(paths.xray_rejected()))     # /run/kaskad: 0711, never 0700
        first = rej.get("first_at") if rej.get("hash") == h else None
        util.atomic_write(paths.xray_rejected(), util.json_text(
            {"hash": h, "at": int(time.time()), "first_at": first or int(time.time()), "error": err}), 0o600)
    except OSError:
        pass
    raise KaskadError(tr("gw_xray.config_refused", err=err))


def rejected_info():
    """{"hash", "at", "first_at", "error"} of the last config ``xray -test`` refused ({} — none)."""
    doc = _read_config(paths.xray_rejected())
    return doc if isinstance(doc, dict) else {}


# ── the order of the renders (§2.5, §2.7) ──────────────────────────────────

def read_seq():
    """The number of the last render taken (0 — none yet)."""
    try:
        return int(util.read_text(paths.xray_seq()).strip() or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def next_seq():
    """Take the next number of a render — under the STATE lock (the collector in its tick, ``ops._apply``): the lock
    orders the numbers, a bigger one is drawn by a fresher state."""
    n = read_seq() + 1
    os.makedirs(os.path.dirname(paths.xray_seq()), exist_ok=True)
    util.atomic_write(paths.xray_seq(), "%d\n" % n, 0o600)
    return n


def applied():
    """{"fp", "seq"} of the render xray runs now ({} — not known)."""
    doc = _read_config(paths.xray_decision())
    return doc if isinstance(doc, dict) else {}


def note_applied(fp, seq):
    try:
        util.atomic_write(paths.xray_decision(), util.json_text({"fp": fp, "seq": seq}), 0o600)
    except OSError as e:
        util.log_tr("gw_xray.log.api_file", err=e)
    _after_failed_clear()                      # П3 З-1: xray runs the state now — a failure after an answer is over


def render_wanted(fp):
    """The collector draws anew: another fingerprint than the applied one, or a number taken and not applied (a
    timeout of the lock, a deferred restart, a failure) — §2.5, §2.7."""
    done = applied()
    if done.get("fp") != fp:
        return True
    try:
        return read_seq() > int(done.get("seq") or 0)
    except (TypeError, ValueError):
        return True


def restart_due(now=None, sends=False):
    """The collector may restart xray now: its last restart was RESTART_GAP_S ago or more (the storm, §2.5) and no
    write of the panel owes a restart after its answer (``after_answer``, AFTER_HOLD_S at most). ``sends`` — the
    address an outbound sends from is gone from the render (``sends_changed``: an address of «Адреса сервера» gone from
    the machine — 3.3.7.15 П6, T-3315 п. 2.6): no window of the storm — nft took its SNAT away in the same round, and
    what xray sends from a gone address is dead anyway; the restart owed to the answer of the panel still holds it
    back. An address back keeps the window (П7, REV-3315-p256 З-1): xray sends from the main address meanwhile."""
    if _after_held(now):
        return False
    if sends:
        return True
    try:
        age = (time.time() if now is None else now) - os.stat(_restart_stamp()).st_mtime
    except OSError:
        return True
    return age >= RESTART_GAP_S or age < 0


def _sends(cfg):
    """{tag: sendThrough} of the outbounds of ``cfg`` (Ш4: the xray exits; Н4: «direct»)."""
    return dict((o.get("tag"), o.get("sendThrough")) for o in (cfg or {}).get("outbounds") or []
                if isinstance(o, dict) and o.get("tag"))


def sends_changed(old, new):
    """An outbound of both configs no longer sends from the address it sent from (``sendThrough`` gone or changed) —
    what an address of the machine gone does to the render (``mip.plan``). The address back (no ``sendThrough`` before)
    is not that (3.3.7.15 П7, REV-3315-p256 З-1): xray sends from the main address meanwhile and nothing is cut, so it
    waits for the window of the storm — a flapping address restarts xray once a window, not every round. An outbound
    that comes or goes (an exit added, removed, off by its quota) is not that either: the storm keeps its window."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    a, b = _sends(old), _sends(new)
    return any(a[t] and a[t] != b[t] for t in set(a) & set(b))


def _restart_stamp():
    return os.path.join(paths.gw_run_dir(), "xray-restart.stamp")


# ── a restart after the answer of the panel (3.3.7.15 П3; stand T-3315 п. 1.7) ──
# The panel on the domain on 443 sits behind this xray (VLESS TLS in front of the site: its fallback to nginx): a
# write of the panel that restarted xray inside the request lost its own answer («Empty reply from server», the
# change applied). Under ``after_answer()`` (the thread of that request) a restart its apply needs is deferred the
# way the storm defers it (``defer_restart``: the file is not replaced, nothing noted — anyone's next call draws it
# again), but only after ``xray -test`` took the new config; the mark ``xray-after.json`` {token, at}, set under the
# xray lock, holds the restarts of the collector back for AFTER_HOLD_S at most (``restart_due``, asked by the collector
# under the same lock — STORM); the panel calls ``ops.xray_after_answer`` right after its answer has left
# (``Request.after``). A dead panel — the collector restarts after the hold; a reboot takes the mark away with /run.
# A failure of that call stays as ``xray-after-failed.json`` until a render is applied (``after_failed``).

_AFTER = threading.local()


@contextlib.contextmanager
def after_answer():
    """The scope of one write of the panel (its thread) -> {"token": None | str}: a token — a restart is owed, the
    caller runs ``ops.xray_after_answer(runner, token)`` once its answer has left."""
    prev = getattr(_AFTER, "box", None)
    box = {"token": None}
    _AFTER.box = box
    try:
        yield box
    finally:
        _AFTER.box = prev


def _after_path():
    return os.path.join(paths.gw_run_dir(), "xray-after.json")


def _after_owe(box):
    """The mark of a restart owed to the answer (under the state lock of the write and the xray lock of its apply): a
    new token each write (a later write owns the restart, the call of an earlier one does nothing). A mark that can't
    be written holds nothing back — the call after the answer comes anyway."""
    if box is None:
        return
    box["token"] = os.urandom(8).hex()
    try:
        util.ensure_run_dir(paths.gw_run_dir())
        util.atomic_write(_after_path(), util.json_text({"token": box["token"], "at": int(time.time())}), 0o600)
    except OSError as e:
        util.log_action("GW xray: the mark of the restart after the answer not written (%s)" % type(e).__name__)


def _after_doc():
    doc = _read_config(_after_path())
    return doc if isinstance(doc, dict) else {}


def after_token():
    """The token of the restart a write of the panel owes to its answer now (None — none)."""
    t = _after_doc().get("token")
    return t if isinstance(t, str) else None


def _after_held(now=None):
    at = _after_doc().get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    age = (time.time() if now is None else now) - at
    return 0 <= age < AFTER_HOLD_S


def _after_failed_path():
    return os.path.join(paths.gw_run_dir(), "xray-after-failed.json")


def after_failed_note(err):
    """П3 З-1: the restart after the answer of the panel failed — {at, err} (redacted, short) until a render is applied
    (``note_applied``); ``GET /api/gw/addresses`` says it while it is there."""
    try:
        util.ensure_run_dir(paths.gw_run_dir())
        util.atomic_write(_after_failed_path(), util.json_text({"at": int(time.time()), "err": str(err)[:300]}), 0o600)
    except OSError as e:
        util.log_action("GW xray: the mark of the failed restart after the answer not written (%s)" % type(e).__name__)


def after_failed():
    """{"at", "err"} of the restart after the answer that failed and is not made good yet (None — none)."""
    doc = _read_config(_after_failed_path())
    return doc if isinstance(doc, dict) and isinstance(doc.get("err"), str) else None


def _after_failed_clear():
    if os.path.lexists(_after_failed_path()):
        try:
            os.unlink(_after_failed_path())
        except OSError:
            pass


def after_done(token):
    """The owed restart is made or failed (the collector catches up): the mark of ``token`` goes, another stays.
    Called under the state lock — the only one that writes a mark."""
    if after_token() == token:
        try:
            os.unlink(_after_path())
        except OSError:
            pass


# ── «what runs may not be the file» (B7: REV-s1-b6 №1, Б2; REV-s1-b6-sec Н-1) ──
# A mark in /run (gone with a reboot — and so is every process that could hold an older config): put BEFORE the file
# changes under a running xray and before a closing; taken away only when xray is KNOWN to run the file on the disk
# (started, restarted, took it by the API) or KNOWN to be stopped with no file of direct rules left. While it is there:
# the next ``apply`` restarts even when the file equals the render, its paths count in what ``_close_ru`` closes, the
# render is not noted as applied (the collector draws again next tick), and ``ru_open`` says Russia may go directly.
# The mark is never a point of failure (B7 круг 2, REV-s1-b7 №1, -sec С-1): /run that takes no write — the same mark in
# /etc/kaskad/gw (a reboot leaves it: one restart owed, harmless); neither — the file of a closing is not removed but
# renamed to ``xray.closed.json`` (no space needed) and its paths are the mark. Every reader takes all three.

def _pending_path():
    return os.path.join(paths.gw_run_dir(), "xray-pending.json")


def _pending_paths():
    """The places of the mark: /run first, /etc/kaskad/gw when /run takes no write."""
    return (_pending_path(), os.path.join(paths.gw_dir(), "xray-pending.json"))


def closed_path():
    """The file of a closing renamed out of the way of the unit (LoadCredential reads ``config_path`` only) when no mark
    could be written: it remembers what may run."""
    return os.path.join(os.path.dirname(config_path()), "xray.closed.json")


def _marks():
    """[(path, doc)] of the marks that exist (a doc that cannot be read — {}: still a mark)."""
    out = []
    for p in _pending_paths():
        if os.path.lexists(p):
            doc = _read_config(p)
            out.append((p, doc if isinstance(doc, dict) else {}))
    return out


def _rows(doc):
    out = set()
    rows = doc.get("paths") if isinstance(doc, dict) else None
    for r in rows if isinstance(rows, list) else ():
        if isinstance(r, list) and len(r) == 2:
            out.add((r[0], r[1]))
    return out


def pending():
    """None — xray runs the file on the disk, or nothing runs; else the set of {(inbound tag, user)} the running process
    may still send directly by the lists of Russia (empty — a restart is owed, nothing of Russia known). A mark that
    cannot be read counts as a mark (a restart owed); so does ``xray.closed.json`` (its paths)."""
    marks = _marks()
    closed = os.path.lexists(closed_path())
    if not marks and not closed:
        return None
    out = set()
    for _p, doc in marks:
        out |= _rows(doc)
    if closed:
        out |= ru_direct_paths(_read_config(closed_path()))
    return out


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _note_pending(ru, open_n=None, told=None):
    """Put (or widen) the mark: ``ru`` — the paths the running process may send directly; ``open_n`` — how many of them
    a closing could not stop (None — as the mark says); ``told`` — the number the collector told the owner (None — as
    the mark says: a restarted collector does not tell the same again). /run, else /etc/kaskad/gw. -> True when a
    mark was written; never raises (False — no place took it: the caller renames the file instead)."""
    old = dict(_marks())
    doc = {}
    for d in old.values():
        doc = d or doc
    ru = set(ru) | (pending() or set())
    rows = sorted([[t, u] for t, u in ru], key=lambda r: (str(r[0]), str(r[1])))
    n = _int(doc.get("open")) if open_n is None else _int(open_n)
    t = _int(doc.get("told")) if told is None else _int(told)
    text = util.json_text({"paths": rows, "open": n, "told": t, "at": int(time.time())})
    for p in _pending_paths():
        try:
            if p == _pending_path():
                util.ensure_run_dir(os.path.dirname(p))
            util.atomic_write(p, text, 0o600)
        except OSError as e:
            util.log_tr("gw_xray.log.api_file", err=e)
            continue
        for q in old:                          # one mark: the other place goes (it would say an older number)
            if q != p:
                _unlink(q)
        return True
    return False


def _unlink(p):
    try:
        os.unlink(p)
    except FileNotFoundError:
        pass
    except OSError as e:                       # stays: the next call restarts once more — never the other way round
        util.log_tr("gw_xray.log.api_file", err=e)


def _clear_pending():
    for p in _pending_paths() + (closed_path(),):
        _unlink(p)


def _narrow(want):
    """The mark says how many paths are open WITHOUT the choice: when the choice came back (the consent ⚠РФ, «Россия
    напрямую»), fewer (REV-s1-b7 придирка 2). The paths stay: the process may still hold them."""
    may = pending()
    if may is None:
        return
    n = len(may - set(want))
    if n != ru_open():
        # REV-s1-b7-2 №2: all of it allowed again — the number told goes too: a second opening is told again
        _note_pending(set(), open_n=n, told=0 if n == 0 else None)


def ru_open(runner=None, st=None, sec=None, pool_exits=None):
    """How many paths xray may still send directly to Russia without the choice: the last closing could not stop it
    (0 — none known). For the check of the node and the collector (REV-s1-b6-sec Н-2). No mark but
    ``xray.closed.json`` (no place took the mark) — its paths while the unit is not KNOWN stopped (``runner`` given);
    with the state ``st`` only those the state does not give (REV-s1-b7-2 придирка 2: not the allowed ones too)."""
    marks = _marks()
    if marks:
        return max(_int(d.get("open")) for _p, d in marks)
    if runner is not None and os.path.lexists(closed_path()) and not gsys.unit_stopped(runner, UNIT):
        cfg = _read_config(closed_path())
        if st is None:
            return len(ru_direct_paths(cfg))
        if sec is None:
            from .. import secrets
            sec = secrets.load()
        return len(_unsafe(st, sec, runner, exits.pool(runner, roles=exits.ROLES) if pool_exits is None else pool_exits, cfg))
    return 0


def narrowed(runner=None):
    """The process may still hold direct paths of Russia, but none of them is open WITHOUT the choice any longer: the
    choice or the consent came back (``_narrow``; the mark stays with ``open`` 0) — xray was neither restarted nor
    stopped (REV-s1-b7-2 №2: the words of the closing). No mark: ``xray.closed.json`` while the unit is not KNOWN
    stopped."""
    marks = _marks()
    if marks:
        return any(_rows(d) for _p, d in marks) and not any(_int(d.get("open")) for _p, d in marks)
    return runner is not None and os.path.lexists(closed_path()) and not gsys.unit_stopped(runner, UNIT)


def ru_told():
    """The number of open paths the collector last told the owner (kept in the mark: a restart of the collector does
    not tell it again — REV-s1-b7 придирка 3)."""
    return max([_int(d.get("told")) for _p, d in _marks()] or [0])


def note_told(n):
    if _marks():
        _note_pending(set(), told=n)


def unsafe_file(st, sec, runner, pool_exits):
    """{(inbound tag, user)} the file on the disk sends directly to Russia that the state does not give (no consent ⚠РФ:
    every such path; else what the render of the state does not have; a render that fails — every path). The watchdog
    never starts xray with such a file (B7 круг 2, -sec С-1): the collector draws the file first."""
    return _unsafe(st, sec, runner, pool_exits, _read_config(config_path()))


def _unsafe(st, sec, runner, pool_exits, cfg):
    """The paths of ``cfg`` the state does not give: without the permission every direct one (any matcher —
    ``direct_matched``); with it, the direct paths by the lists the render of the state does not have."""
    from . import ruguard
    if not ruguard.allowed(st):
        return ru_direct_paths(cfg) | direct_matched(cfg)
    have = ru_direct_paths(cfg)
    if not have:
        return set()
    try:
        want = ru_direct_paths(render(st, sec, pool_exits, net.node_addresses(runner, st)))
    except Exception:                          # noqa: BLE001 — not known safe: not started
        return have
    return have - want


def _restarted(runner, cfg):
    try:
        util.ensure_run_dir(paths.gw_run_dir())
        util.atomic_write(_restart_stamp(), "%d\n" % int(time.time()), 0o600)
    except OSError:
        pass
    _hy2_note(runner, cfg, fresh=True)


def _lock(wait):
    """flock of ``paths.xray_apply_lock()`` within ``wait`` s -> the open file, None on a timeout."""
    import fcntl
    p = paths.xray_apply_lock()
    util.ensure_run_dir(os.path.dirname(p))             # /run/kaskad: 0711, never 0700 (nginx → panel socket)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)     # SEC-s1-t45 З-3: 0600 as every lock (umask gave 0644: anyone
    try:                                                # could hold the flock and freeze xray)
        os.fchmod(fd, 0o600)                            # a file of an older version
    except OSError:
        pass
    f = os.fdopen(fd, "r+")
    end = time.monotonic() + max(0.0, float(wait or 0))
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.monotonic() >= end:
                f.close()
                return None
            time.sleep(0.05)


def _unlock(f):
    import fcntl
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


# ── Hysteria2 exits: a server is known to xray by its address (T0) ─────────

def _hy2_path():
    return os.path.join(paths.gw_run_dir(), "xray-hy2.json")


def _hy2_eps(cfg):
    """{"host:port": sha of the outbound} of the Hysteria2 outbounds of a config."""
    out = {}
    for ob in (cfg or {}).get("outbounds") or []:
        if isinstance(ob, dict) and ob.get("protocol") in ("hysteria", "hysteria2"):
            host, port = exits.outbound_server(ob)
            if host:
                out["%s:%s" % (host, port)] = hashlib.sha1(util.json_text(ob).encode("utf-8")).hexdigest()
    return out


def _healed(runner, xray):
    """True — the pool has a line xray refused that its config no longer carries as it did (exits.heal)."""
    r = exits.heal(runner, xray)
    return bool(r and (r.get("new") or r.get("legacy")))


def _hy2_seen(runner):
    """The Hysteria2 servers this run of xray has met ({"host:port": sha}) — the record is of its MainPID."""
    doc = _read_config(_hy2_path()) or {}
    pid = _own_pid(runner)
    return dict(doc.get("eps") or {}) if pid and doc.get("pid") == pid else {}


def _hy2_note(runner, cfg, fresh=False):
    pid = _own_pid(runner)
    if not pid:
        return
    eps = {} if fresh else _hy2_seen(runner)
    eps.update(_hy2_eps(cfg))
    try:
        util.ensure_run_dir(paths.gw_run_dir())
        util.atomic_write(_hy2_path(), util.json_text({"pid": pid, "eps": eps}), 0o600)
    except OSError:
        pass


def _front():
    from ..site import front
    return front


def _read_config(path):
    try:
        import json
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


# ── live changes through the API (HandlerService, RoutingService) ──────────

def hot_prefixes():
    """The ruleTag prefixes of the per-user chains: HOT_PREFIX + ``xray_hot_prefix`` of every adapter (§2.4)."""
    from . import registry
    out = set(HOT_PREFIX)
    for ad in registry.all_adapters().values():
        p = getattr(ad, "xray_hot_prefix", None)
        if p:
            out.add(str(p))
    return tuple(sorted(out))


def add_modes(st, sec):
    """{tag of a hot inbound: "cli" | "grpc" | None} — how a new user of it comes live (``xray_add_user``)."""
    out = {}
    for _, ad in _adapters(st):
        if getattr(ad, "xray_hot", False):
            for ib in ad.xray_inbounds(st, sec) or []:
                if ib.get("tag"):
                    out[ib["tag"]] = getattr(ad, "xray_add_user", None)
    return out


def hot_adapters(st, sec):
    """{tag of a hot inbound: its adapter} — ``xray_account`` of the "grpc" ones."""
    out = {}
    for _, ad in _adapters(st):
        if getattr(ad, "xray_hot", False):
            for ib in ad.xray_inbounds(st, sec) or []:
                if ib.get("tag"):
                    out[ib["tag"]] = ad
    return out


def _user_key(ib):
    """(list, key) of the users of an inbound: ``clients``/``email`` (VLESS, Hysteria2), ``accounts``/``user`` (SOCKS5)."""
    s = ib.get("settings") if isinstance(ib.get("settings"), dict) else {}
    return ("accounts", "user") if "accounts" in s else ("clients", "email")


def _is_chain(rule, prefixes):
    return str((rule or {}).get("ruleTag") or "").startswith(tuple(prefixes))


def _strip(cfg, tags, prefixes=None):
    """The config without what the API changes for the users: the users of the hot inbounds and their chains — equal
    for two configs: only users and chains differ, the base ``xray -test`` checked stays (§2.5)."""
    prefixes = hot_prefixes() if prefixes is None else prefixes
    c = copy.deepcopy(cfg)
    for ib in c.get("inbounds") or []:
        if isinstance(ib, dict) and ib.get("tag") in tags and isinstance(ib.get("settings"), dict):
            ib["settings"][_user_key(ib)[0]] = []
    r = c.get("routing") or {}
    r["rules"] = [x for x in r.get("rules") or [] if not _is_chain(x, prefixes)]
    return c


def _clients(cfg, tags):
    """{(tag, email | user): client} of the hot inbounds — VLESS ``clients`` and SOCKS5 ``accounts`` alike."""
    out = {}
    for ib in cfg.get("inbounds") or []:
        if isinstance(ib, dict) and ib.get("tag") in tags:
            key, name = _user_key(ib)
            for c in (ib.get("settings") or {}).get(key) or []:
                out[(ib["tag"], c.get(name))] = c
    return out


def _chains(cfg, prefixes=None):
    prefixes = hot_prefixes() if prefixes is None else prefixes
    return [x for x in (cfg.get("routing") or {}).get("rules") or [] if _is_chain(x, prefixes)]


def _pool_out(ob):
    return isinstance(ob, dict) and str(ob.get("tag") or "").startswith(POOL_PREFIXES)


def _pool_in(ib):
    t = str((ib or {}).get("tag") or "")
    return (t.startswith("tproxy-") and t != "tproxy-in") or t.startswith("probe-")


def _base(cfg, tags):
    """What only a restart changes: the config without the routing (rules and balancers go live as a whole), the users
    of the hot inbounds and the exits of the pool (their outbounds and inbounds come and go through the API, §2.7)."""
    c = _strip(cfg, tags, ())
    c.pop("routing", None)
    c["outbounds"] = [o for o in c.get("outbounds") or [] if not _pool_out(o)]
    c["inbounds"] = [i for i in c.get("inbounds") or [] if not _pool_in(i)]
    return c


def __getattr__(name):
    if name == "CUT_NOTE":                      # the words of the restart that cuts a device: the language of the moment
        return tr("gw_xray.cut_note")
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _plain_block(cfg):
    """Users a plain rule sends to ``block`` (no domain, no ip): blocked by choice — a route «block», never the lists."""
    out = set()
    for x in (cfg.get("routing") or {}).get("rules") or []:
        if isinstance(x, dict) and x.get("outboundTag") == "block" and "domain" not in x and "ip" not in x:
            out.update(str(u) for u in x.get("user") or [])
    return out


def _known_users(cfg, tags):
    out = set(str(k[1]) for k in _clients(cfg, tags))
    for x in (cfg.get("routing") or {}).get("rules") or []:
        if isinstance(x, dict):
            out.update(str(u) for u in x.get("user") or [])
    return out


def leaves(old, new, tags):
    """A user of a hot inbound leaves (revoked, paused, a new UUID or password) or goes to «block» between ``old`` and
    ``new``: the restart is on purpose — its open connections are cut (``hot_plan`` refuses the API for it)."""
    a, b = _clients(old, tags), _clients(new, tags)
    if any(b.get(k) != a[k] for k in a):
        return True
    return bool((_plain_block(new) - _plain_block(old)) & _known_users(old, tags))


def _ru_routes(cfg):
    """{("user", login) | ("in", inbound tag): {«gr-<id>»}} — who the rules of ``cfg`` send to a Russian exit (an
    ``outboundTag`` «gr-…»): a rule with users — each of them, without — each of its inbounds."""
    out = {}
    for x in (cfg.get("routing") or {}).get("rules") or [] if isinstance(cfg, dict) else ():
        t = x.get("outboundTag") if isinstance(x, dict) else None
        if not (isinstance(t, str) and t.startswith(exits.RU_PREFIX)):
            continue
        keys = [("user", str(u)) for u in x.get("user") or []] or [("in", str(i)) for i in x.get("inboundTag") or []]
        for k in keys:
            out.setdefault(k, set()).add(t)
    return out


def ru_lost(old, new):
    """T-q Н-5x: the Russian exits («gr-<id>») some user or inbound of ``old`` went to and does not in ``new`` —
    ``ru_via`` changed, the exit moved out of its group or was deleted, the consent withdrawn. -> set of tags."""
    a, b = _ru_routes(old), _ru_routes(new)
    out = set()
    for k, tags in a.items():
        out |= tags - b.get(k, set())
    return out


SS_KILL = ["ss", "-K", "-n", "-t", "-u"]
SS_LIST = ["ss", "-H", "-n", "-t", "-u", "-p"]   # the same sockets as ``ss -K`` takes (no listening ones), with owners
CUT_CHUNK = 40                   # local ports in one ``ss -K`` filter (``sport = :a or …``)
RESOLVE_S = 2.5                  # П-4: the name of a router's server under the xray lock — at most this long


def _resolve4(host, port, wait=RESOLVE_S):
    """The IPv4 addresses of ``host`` by the node's resolver, within ``wait`` s (T-q круг 3, П-4: it runs under the
    locks of xray and of the state — a bad DNS of the node must not hold the panel). [] — none or too slow."""
    import socket
    import threading
    got = []

    def ask():
        try:
            got.extend(sorted(set(a[4][0] for a in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM))))
        except (OSError, UnicodeError):
            pass
    th = threading.Thread(target=ask, daemon=True)
    th.start()
    th.join(wait)
    if th.is_alive():
        util.log_action("GW: the server %s of a domestic exit not resolved in %.1f s — its connections not cut"
                        % (host, wait))
        return []
    return list(got)


def _cut_filters(ob):
    """The ``ss`` filters of the sockets xray opened through the outbound ``ob``: an AmneziaWG leg — its
    ``sockopt.mark`` (a freedom), any other — the address and the port of its server (a name — its IPv4 addresses by
    the node's resolver, as xray resolves it, ``_resolve4``). [] — nothing to tell them by; None — the name of the
    server did not resolve (REV-q-r3 П-2: not «nothing to cut» — the connections live on, ``cut_ru_conns``)."""
    ss = (ob or {}).get("streamSettings") if isinstance(ob, dict) else None
    so = (ss.get("sockopt") or {}) if isinstance(ss, dict) else {}
    mark = so.get("mark") if isinstance(so, dict) else None
    if ob.get("protocol") == "freedom":
        if isinstance(mark, int) and not isinstance(mark, bool) and mark > 0:
            return [["fwmark", "=", "0x%x" % mark]]
        return []                       # «direct» and its kin: never cut by this
    host, port = exits.outbound_server(ob)
    if not host or not port:
        return []
    import ipaddress
    try:
        ips = [str(ipaddress.IPv4Address(host))]
    except ValueError:
        ips = _resolve4(host, port)
        if not ips:
            return None
    return [["dst", ip, "and", "dport", "=", ":%d" % int(port)] for ip in ips]


def _ss_sockets(runner, flt, pid):
    """({(netid, local, peer): local port}, anon) — the sockets ``flt`` takes, of the process ``pid`` only (0 — of
    any); ``anon`` — there were sockets and ``ss`` told no owners: taken by the filter alone, any process. None —
    ``ss`` failed."""
    res = runner.run(SS_LIST + flt, timeout=15)
    if not res.ok:
        return None
    out = {}
    lines = [x for x in (res.out or "").splitlines() if len(x.split()) >= 6 and x.split()[0] != "Netid"]
    anon = False
    if not any("users:" in x for x in lines):
        anon = bool(pid and lines)
        pid = 0                         # ``ss`` tells no owners here: the address filter alone, as before
    for line in lines:
        p = line.split()
        if pid and not re.search(r"\bpid=%d\b" % pid, line):
            continue
        port = p[4].rsplit(":", 1)[-1]
        if port.isdigit():
            out[(p[0], p[4], p[5])] = port
    return out, anon


def _cut_one(runner, flt, pid):
    """-> (sockets of xray the filter took before, of them gone after ``ss -K``, ``anon`` of ``_ss_sockets``) or
    None — ``ss`` failed. With ``pid``
    (the MainPID of xray) the kill is narrowed to the local ports of ITS sockets (T-q круг 3, П-1: another program's
    connections to the same address:port stay)."""
    got = _ss_sockets(runner, flt, pid)
    if got is None:
        return None
    before, anon = got
    if not before:
        return 0, 0, anon
    ports = sorted(set(before.values()), key=int)       # exactly the sockets counted (a new one is not taken)
    for i in range(0, len(ports), CUT_CHUNK):
        sport = []
        for j, p in enumerate(ports[i:i + CUT_CHUNK]):
            sport += (["or"] if j else []) + ["sport", "=", ":" + p]
        runner.run(SS_KILL + flt + ["and", "("] + sport + [")"], timeout=15)
    got = _ss_sockets(runner, flt, pid)
    if got is None:
        return None
    return len(before), len(set(before) - set(got[0])), anon


def cut_ru_conns(runner, old, tags):
    """T-q Н-5x: close the open connections of xray through the Russian exits ``tags`` (the outbounds of ``old``) —
    ``ss -K`` on the sockets of xray to that router: the client of VLESS/SOCKS5/Hysteria2 sees its connection end and
    comes again by its new way, as the kernel's devices do (``net.flush_revoked``), xray NOT restarted. The sockets of
    the other users of the same router go too (a socket tells no user) — they come back the same way at once; so do
    the connections of a FOREIGN exit on the same server (one server in two roles: the same address and port —
    SPEC-groups §2.6, a limit of stage 1).

    The result is the FACT (T-q круг 3, З-1): the sockets of xray the filter took before the kill and those of them
    gone after (``ss`` without -K, the same filter) — an old kernel without CONFIG_INET_DIAG_DESTROY makes ``ss -K``
    skip a socket silently (rc 0, no words), so its answer proves nothing. -> the number closed; None — ``ss`` missing
    or failed, or there were sockets and not one of them went, or the name of a router did not resolve: the old
    connections live until they end (journal)."""
    obs = dict((o.get("tag"), o) for o in (old or {}).get("outbounds") or [] if isinstance(o, dict))
    if not runner.which("ss"):
        util.log_action("GW: domestic exits %s left — no ss: the connections of xray not cut"
                        % ", ".join(sorted(tags or ())))
        return None
    pid = _own_pid(runner)
    seen, gone, failed, anon, unresolved = 0, 0, False, False, False
    for t in sorted(tags or ()):
        flts = _cut_filters(obs.get(t)) if obs.get(t) else []
        if flts is None:                # the server's name did not resolve: its connections are not cut (REV-q-r3 П-2)
            unresolved = True
            continue
        for f in flts:
            got = _cut_one(runner, f, pid)
            if got is None:
                failed = True
                continue
            seen += got[0]
            gone += got[1]
            anon = anon or got[2]
    failed = failed or bool(seen and not gone)
    ok = not failed and not unresolved
    how = " (any process: no MainPID)" if not pid else " (any process: ss shows no owners)" if anon else ""
    why = [w for w, on in (("the name of a router not resolved", unresolved),
                           ("ss failed or the kernel cannot: CONFIG_INET_DIAG_DESTROY", failed)) if on]
    why = " — NOT cut (%s)" % "; ".join(why) if why else ""
    util.log_action("GW: domestic exits %s left — %d of %d connections of xray closed%s%s"
                    % (", ".join(sorted(tags or ())), gone, seen, how, why))
    return gone if ok else None


def _ext_rule(x):
    return any("ext:" in str(v) for k in ("domain", "ip") for v in (x.get(k) or []))


def _by_tag(items, pred):
    return dict((i["tag"], i) for i in items or [] if isinstance(i, dict) and i.get("tag") and pred(i))


def hot_plan(old, new, tags, modes=None, seen=None):
    """What the API must do to bring a running xray from ``old`` to ``new`` without a restart, or None (a restart).

    Live: users of the hot inbounds (``tags``) coming (``adu`` — "cli", ``xraygrpc`` — "grpc"; ``modes`` {tag: mode},
    None: by the protocol), their chains (``rmrules`` + ``adrules -append``, whole), the exits of the pool (``ado``/
    ``adi`` + their rules, ``rmrules``/``rmi``/``rmo``), any other change of the routing — all the rules and balancers
    in one ``adrules`` without ``-append`` (then ``bo`` anew: it is lost, T0), when its rules with lists are not more
    than HOT_MAX_EXT_RULES. A restart: the base changed (``_base``), a user leaves or goes to «block» by choice (its open
    connections must go), a new user of an inbound without a live way (SOCKS5), a Hysteria2 exit on a server this run of
    xray met with another key (``seen``), more than HOT_MAX_TICK operations, an xray without the services.

    -> {"add_outbounds", "rm_outbounds", "add_inbounds", "rm_inbounds", "add_rules", "rm_rules", "routing",
        "add_users", "rm_users", "inbounds", "modes"}"""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return None
    for c in (old, new):
        if not set(HOT_SERVICES) <= set((c.get("api") or {}).get("services") or []):
            return None
    if _base(old, tags) != _base(new, tags):
        return None
    prefixes = hot_prefixes()
    a, b = _clients(old, tags), _clients(new, tags)
    if any(b.get(k) != a[k] for k in a):
        # a user leaves (revoked, paused, a new UUID or password): only a restart cuts the connections it has open —
        # the API takes it out of new ones only (and ``rmu`` is never called for it)
        return None
    if (_plain_block(new) - _plain_block(old)) & _known_users(old, tags):
        return None                  # a user moved to «block» by choice: its open connections must go too — a restart
    modes = dict(modes or {})
    proto = dict((ib.get("tag"), ib.get("protocol")) for ib in new.get("inbounds") or [] if isinstance(ib, dict))
    add_users = [(k[0], b[k]) for k in sorted(b, key=lambda k: (str(k[0]), str(k[1]))) if a.get(k) != b[k]]
    for tag, _ in add_users:
        mode = modes.get(tag) if tag in modes else {"vless": "cli", "hysteria": "grpc"}.get(proto.get(tag))
        if mode not in ("cli", "grpc"):
            return None              # SOCKS5: «proxy is not a UserManager» (T0 С0.7) — a new account restarts xray
        modes[tag] = mode
    oo, no = _by_tag(old.get("outbounds"), _pool_out), _by_tag(new.get("outbounds"), _pool_out)
    oi, ni = _by_tag(old.get("inbounds"), _pool_in), _by_tag(new.get("inbounds"), _pool_in)
    add_outbounds = [no[t] for t in sorted(no) if oo.get(t) != no[t]]
    rm_outbounds = sorted(t for t in oo if no.get(t) != oo[t])
    add_inbounds = [ni[t] for t in sorted(ni) if oi.get(t) != ni[t]]
    rm_inbounds = sorted(t for t in oi if ni.get(t) != oi[t])
    hy = dict((k, v) for k, v in (seen or {}).items())
    hy.update(_hy2_eps(old))
    for k, v in _hy2_eps(dict(new, outbounds=add_outbounds)).items():
        if k in hy and hy[k] != v:
            return None              # T0: xray keeps a Hysteria2 client by its address — another key there restarts
    ro, rn = old.get("routing") or {}, new.get("routing") or {}
    live = lambda x: _is_chain(x, prefixes) or _is_chain(x, (PX_PREFIX,))      # noqa: E731
    common_o = [x for x in ro.get("rules") or [] if not live(x)]
    common_n = [x for x in rn.get("rules") or [] if not live(x)]
    routing = None
    add_rules, rm_rules = [], []
    if common_o != common_n or (ro.get("balancers") or []) != (rn.get("balancers") or []) \
            or ro.get("domainStrategy") != rn.get("domainStrategy"):
        if sum(1 for x in rn.get("rules") or [] if _ext_rule(x)) > HOT_MAX_EXT_RULES:
            return None              # Р28: more rules with lists than one gRPC call carries — a restart
        routing = copy.deepcopy(rn)
    else:
        old_tags = set(x["ruleTag"] for x in ro.get("rules") or [] if live(x))
        new_rules = [x for x in rn.get("rules") or [] if live(x)]
        new_tags = set(x["ruleTag"] for x in new_rules)
        add_rules = [x for x in new_rules if x["ruleTag"] not in old_tags]
        rm_rules = sorted(old_tags - new_tags)
        if sum(1 for bt in _batches(add_rules) if any(_ext_rule(x) for x in bt)) > 4:
            return None              # more calls with lists than HOT_LOCK_S carries (T0: 20 s — 4 of them)
    ops = len(add_rules) + len(rm_rules) + len(add_users) + len(add_outbounds) + len(rm_outbounds) + \
        len(add_inbounds) + len(rm_inbounds) + (1 if routing is not None else 0)
    if not ops or ops > HOT_MAX_TICK:
        return None                  # nothing the API does (a restart re-reads the file) / a mass move over the limit
    return {"add_outbounds": add_outbounds, "rm_outbounds": rm_outbounds, "add_inbounds": add_inbounds,
            "rm_inbounds": rm_inbounds, "add_rules": add_rules, "rm_rules": rm_rules, "routing": routing,
            "add_users": add_users, "rm_users": [], "modes": modes,
            "inbounds": dict((ib["tag"], ib) for ib in new.get("inbounds") or [] if ib.get("tag") in tags)}


def _batches(rules):
    """Calls of ``adrules``: HOT_MAX_RULES rules, of them HOT_MAX_EXT_RULES with lists at the most."""
    out, cur, ext = [], [], 0
    for x in rules:
        e = 1 if _ext_rule(x) else 0
        if cur and (len(cur) >= HOT_MAX_RULES or ext + e > HOT_MAX_EXT_RULES):
            out.append(cur)
            cur, ext = [], 0
        cur.append(x)
        ext += e
    if cur:
        out.append(cur)
    return out


def _chunks(items, n):
    items = list(items)
    return [items[i:i + n] for i in range(0, len(items), n)] if items else []


_mono = time.monotonic                 # tests move the clock of HOT_LOCK_S


def hot_apply(runner, xray_bin, plan, bo=None):
    """Run ``plan`` through ``xray api`` on 127.0.0.1 — in batches, within HOT_LOCK_S: new exits first (outbounds,
    then inbounds), the routing (all of it — then ``bo`` of gw-all anew — or the new rules), the users, the old rules,
    the old exits; no moment when a user or a TPROXY inbound has no rule. Secrets (UUIDs, passwords, auth) go in files
    (0600, root-only directory) or through stdin, never in argv. ``bo`` — the target of gw-all (``decision.gw_all``).
    -> True when every call succeeded (False: the caller restarts xray — the file is already the new one)."""
    import json
    server = "--server=127.0.0.1:%d" % STATS_PORT
    env = {"XRAY_LOCATION_ASSET": rules_apply.asset_dir()}         # adrules reads the ext: lists itself (§2.4)
    tmp = []
    t0 = _mono()

    def put(doc):
        p = os.path.join(paths.gw_dir(), ".xray-api-%d.json" % len(tmp))
        tmp.append(p)
        util.atomic_write(p, json.dumps(doc), 0o600)
        return p

    def call(args):
        if _mono() - t0 > HOT_LOCK_S:
            util.log_tr("gw_xray.log.api_slow", s=HOT_LOCK_S)
            return False                       # the lock is held too long: a restart instead (§2.5)
        args = args[:1] + ["-timeout", str(API_TIMEOUT - 2)] + args[1:]
        res = runner.run([xray_bin, "api"] + args, timeout=API_TIMEOUT, env=env)
        if not res.ok:
            lines = [ln.strip() for ln in (res.err + "\n" + res.out).splitlines() if ln.strip()]
            with util.journal_lang():
                util.log_action(tr("gw_xray.log.api_refused", cmd=args[0], err=util.redact(
                    " | ".join(lines[-2:]) or tr("gw_xray.why.code", rc=res.rc))))
        return res.ok

    def grpc(tag, client):
        from . import xraygrpc
        ad = (plan.get("adapters") or {}).get(tag)
        acc = ad.xray_account(client) if ad is not None else None
        if not acc or _mono() - t0 > HOT_LOCK_S:
            return False
        ok = xraygrpc.add_user("127.0.0.1:%d" % STATS_PORT, tag, client.get("email"), acc,
                               timeout=API_TIMEOUT - 2)
        if not ok:
            with util.journal_lang():
                util.log_action(tr("gw_xray.log.api_refused", cmd="AlterInbound", err=tag))
        return ok

    try:
        return _hot_calls(plan, server, put, call, grpc, bo)
    except OSError as e:
        util.log_tr("gw_xray.log.api_file", err=e)
        return False
    finally:
        for p in tmp:
            try:
                os.unlink(p)
            except OSError:
                pass


def _hot_calls(plan, server, put, call, grpc=None, bo=None):
    changed_o = [o["tag"] for o in plan.get("add_outbounds") or [] if o["tag"] in (plan.get("rm_outbounds") or [])]
    changed_i = [i["tag"] for i in plan.get("add_inbounds") or [] if i["tag"] in (plan.get("rm_inbounds") or [])]
    if changed_i and not call(["rmi", server] + changed_i):
        return False
    if changed_o and not call(["rmo", server] + changed_o):
        return False
    if plan.get("add_outbounds") and not call(["ado", server, put({"outbounds": plan["add_outbounds"]})]):
        return False
    if plan.get("add_inbounds") and not call(["adi", server, put({"inbounds": plan["add_inbounds"]})]):
        return False
    if plan.get("routing") is not None:
        if not call(["adrules", server, put({"routing": plan["routing"]})]):
            return False
        # the override is lost with the rules (T0) — put again; no gw-all in the new routing (no exit drawn: all off,
        # none) — nothing to put: xray answers «cannot find tag» and the live change became a restart (KT3-7)
        if bo and _has_bal(plan["routing"], "gw-all") and not call(["bo", server, "-b", "gw-all", str(bo)]):
            return False
    for batch in _batches(plan.get("add_rules") or []):
        if not call(["adrules", server, "-append", put({"routing": {"rules": batch}})]):
            return False
    by_tag = {}
    for tag, c in plan.get("add_users") or []:
        by_tag.setdefault(tag, []).append(c)
    modes = plan.get("modes") or {}
    for tag in sorted(by_tag):
        if modes.get(tag) == "grpc":
            for c in by_tag[tag]:
                if grpc is None or not grpc(tag, c):
                    return False
            continue
        for part in _chunks(by_tag[tag], HOT_MAX_USERS):
            ib = copy.deepcopy(plan["inbounds"][tag])
            ib["settings"][_user_key(ib)[0]] = part
            ib["settings"].pop("fallbacks", None)
            if not call(["adu", server, put({"inbounds": [ib]})]):
                return False
    rm = {}
    for tag, email in plan.get("rm_users") or []:
        rm.setdefault(tag, []).append(email)
    for tag in sorted(rm):
        if not call(["rmu", server, "-tag=%s" % tag] + rm[tag]):
            return False
    for part in _chunks(plan.get("rm_rules") or [], HOT_MAX_RULES):
        if not call(["rmrules", server] + part):
            return False
    gone_i = [t for t in plan.get("rm_inbounds") or [] if t not in changed_i]
    gone_o = [t for t in plan.get("rm_outbounds") or [] if t not in changed_o]
    if gone_i and not call(["rmi", server] + gone_i):
        return False
    if gone_o and not call(["rmo", server] + gone_o):
        return False
    util.log_tr("gw_xray.log.hot", au=len(plan.get("add_users") or []), ru=len(plan.get("rm_users") or []),
                ar=len(plan.get("add_rules") or []), rr=len(plan.get("rm_rules") or []))
    return True


def _has_bal(routing, tag):
    return any(isinstance(b, dict) and b.get("tag") == tag for b in (routing or {}).get("balancers") or [])


_bal_seen = {"key": None, "tags": None}


def has_balancer(tag="gw-all"):
    """Is the balancer ``tag`` in the config on the disk (what the gateway xray runs: the hot path writes the file
    before the API, a restart reads it)? None — not known (no file, unreadable). Read again only when the file
    changed (the collector asks every tick; KT3-8: ``bo`` of an absent gw-all — «cannot find tag» every 5–10 s)."""
    p = config_path()
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    if _bal_seen["key"] != key:
        import json
        try:
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return None
        routing = cfg.get("routing") if isinstance(cfg, dict) else None
        _bal_seen.update(key=key, tags=set(b.get("tag") for b in (routing or {}).get("balancers") or []
                                           if isinstance(b, dict)))
    return tag in _bal_seen["tags"]


def balancer_override(runner, target=None, balancer="gw-all", port=STATS_PORT, xray_bin=None):
    """``xray api bo``: the balancer ``balancer`` sends every new connection to ``target`` (a tag: gx-<id>, block,
    direct — T0 С0.1–С0.2); None — the override removed (``-r``: leastPing again). ``port`` — 10851 for the bot's
    xray. -> bool."""
    xray_bin = xray_bin or run_binary()
    args = ["bo", "-timeout", str(API_TIMEOUT - 2), "--server=127.0.0.1:%d" % port, "-b", balancer]
    args += ["-r"] if target is None else [str(target)]
    res = runner.run([xray_bin, "api"] + args, timeout=API_TIMEOUT)
    if not res.ok:
        with util.journal_lang():
            util.log_action(tr("gw_xray.log.api_refused", cmd="bo", err=util.redact(
                (res.err or res.out or "").strip()[-200:] or tr("gw_xray.why.code", rc=res.rc))))
    return res.ok


_OVERRIDE = re.compile(r"Selecting\s+Override:\s*(\S+)", re.I)


def balancer_info(runner, balancer="gw-all", port=STATS_PORT, xray_bin=None):
    """``xray api bi``: the override of the balancer now — the tag, "" (none) or None (the API did not answer).
    xray prints ``Selecting Override: <tag>`` (T0)."""
    xray_bin = xray_bin or run_binary()
    res = runner.run([xray_bin, "api", "bi", "-timeout", str(API_TIMEOUT - 2), "--server=127.0.0.1:%d" % port,
                      balancer], timeout=API_TIMEOUT)
    if not res.ok:
        return None
    m = _OVERRIDE.search(res.out or "")
    return m.group(1) if m else ""


def stop(runner):
    """Stop and disable the unit if it exists (the file stays). True if it was running. The site behind xray (VLESS +
    TLS on 443) comes back on 443 (site.front)."""
    if not os.path.exists(gsys.unit_path(UNIT)):
        return False
    was = gsys.unit_active(runner, UNIT)
    runner.run(["systemctl", "disable", "--now", UNIT])
    _front().after_xray(runner, stopped=True)
    return was


def remove(runner):
    """The unit, the config, the binary copy — and /opt/kaskad/gw when it is empty then (gw purge)."""
    gone = gsys.remove_unit(runner, UNIT)
    _front().after_xray(runner, stopped=True)
    _front().drop_sock_dir()
    for p in (config_path(), run_binary(), run_binary() + ".new"):
        try:
            os.unlink(p)
        except OSError:
            pass
    rules_apply.remove_assets()                         # roscomvpn-*.dat next to the binary (wave 3)
    try:
        os.rmdir(os.path.dirname(run_binary()))         # only if empty: never someone else's files
    except OSError:
        pass
    return gone


def status(runner):
    return {"active": gsys.unit_active(runner, UNIT), "enabled": gsys.unit_enabled(runner, UNIT),
            "config": os.path.exists(config_path()), "user": net.xray_uid() is not None}


# ── the internal ports (F1, finding 10) ────────────────────────────────────

def internal_ports(st=None, pool_exits=None, runner=None, decision=None):
    """[(proto, port, what)] xray of the gateway listens on at 127.0.0.1: TPROXY of the pool, the DNS of dnsmasq,
    the metrics of the observatory, the TPROXY of each VLESS exit of the pool (when it can be read) and the node's own
    inbounds of the config of stage 1 (§5.2) — exactly those ``render`` draws (``guarded_ports``): the API 10854 by the
    same condition as the config (exits, a hot inbound or the counters of users — REV-s1-t5 З2), the pool inbound 12699,
    ``probe-<id>`` 12700+N of each xray exit drawn. ``decision`` None — ``exits.decision(st)`` (§2.4)."""
    out = [(pr, TPROXY_PORT, tr("gw_xray.port.tproxy")) for pr in ("tcp", "udp")]
    out += [(pr, DNS_PORT, tr("gw_xray.port.dns")) for pr in ("tcp", "udp")]
    out += [("tcp", METRICS_PORT, tr("gw_xray.port.metrics"))]
    try:
        if st is None:
            from .. import state
            st = state.load()
        pool_exits = exits.pool(runner, roles=exits.ROLES) if pool_exits is None else pool_exits
        if "vless" in model.installed_kinds(st):      # as 3.3.5: the API of the VLESS inbound, even while it is off
            out.append(("tcp", STATS_PORT, tr("gw_xray.port.stats")))
        for e in exits.xray_exits(st, pool_exits):
            out += [(pr, exits.tproxy_port(e), tr("gw_xray.port.vless_exit", id=e["id"])) for pr in ("tcp", "udp")]
        from .. import secrets
        sec = secrets.load()
        if needed(st, sec, pool_exits):
            cfg = render(st, sec, pool_exits, (), decision)
            names = dict((ib.get("port"), str(ib.get("tag") or "")) for ib in cfg.get("inbounds") or []
                         if isinstance(ib, dict))
            for port in guarded_ports(cfg):
                if ("tcp", port) in [(pr, p) for pr, p, _ in out]:
                    continue
                tag = names.get(port, "")
                if tag == "stats-in":
                    what = tr("gw_xray.port.stats")
                elif tag == POOL_SOCKS_TAG:
                    what = tr("gw_xray.port.pool_socks")
                else:
                    what = tr("gw_xray.port.probe", id=tag[len("probe-"):])
                out.append(("tcp", port, what))
    except (KaskadError, OSError, ValueError, KeyError, TypeError):
        pass                                           # no state or pool yet: the fixed ports are checked
    return out


def guarded_ports(cfg):
    """TCP ports of the node's own inbounds of a config of xray that only root may open (nft ``gw_out``, §5.2): the
    API, the metrics, the pool inbound, ``probe-<id>`` — exactly those drawn (never the whole range; not the DNS
    10853 dnsmasq goes to)."""
    out = set()
    for ib in (cfg or {}).get("inbounds") or []:
        if not isinstance(ib, dict) or ib.get("listen") != "127.0.0.1":
            continue
        t = str(ib.get("tag") or "")
        if t in ("stats-in", "metrics-in", POOL_SOCKS_TAG) or t.startswith("probe-"):
            try:
                out.add(int(ib.get("port")))
            except (TypeError, ValueError):
                continue
    return sorted(out)


def _own_pid(runner):
    res = runner.run(["systemctl", "show", "-p", "MainPID", "--value", UNIT])
    try:
        return int((res.out or "").strip() or 0) if res.ok else 0
    except ValueError:
        return 0


def port_conflicts(runner, st=None, pool_exits=None, decision=None):
    """[text] — internal ports of xray held by another program (``ss -Hltnup``; xray of the gateway itself — the
    MainPID of its unit — is no conflict): what holds the port and what to do. ``gw probe`` / ``gw enable`` refuse
    with it (redsocks takes 12345 by default)."""
    if not runner.which("ss"):
        return []
    res = runner.run(["ss", "-Hltnup"])
    if not res.ok:
        return []
    want = dict(((pr, port), what) for pr, port, what in internal_ports(st, pool_exits, runner, decision))
    own = _own_pid(runner)
    out = []
    for line in res.out.splitlines():
        got = conflicts.parse_ss(line)
        if not got or (got[0]["proto"], got[0]["port"]) not in want or got[0]["addr"] not in CLASH_ADDRS:
            continue
        procs = [(name, int(pid)) for name, pid in _SS_PID.findall(line)]
        if own and any(pid == own for _, pid in procs):
            continue
        who = ", ".join("%s (pid %d)" % p for p in procs) or tr("gw_xray.port.unknown")
        text = tr("gw_xray.port.busy", addr=got[0]["addr"] or "*", port=got[0]["port"], proto=got[0]["proto"],
                  what=want[(got[0]["proto"], got[0]["port"])], who=who)
        if text not in out:
            out.append(text)
    return out
