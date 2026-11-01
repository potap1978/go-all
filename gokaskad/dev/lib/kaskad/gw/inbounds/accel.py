"""accel-ppp engine of the PPP inbounds (work package 6): L2TP (l2tp.py), PPTP (pptp.py) and SSTP (sstp.py)
share one daemon. Not an adapter — registers nothing; the three adapters call it.

Build (``ensure_build``). accel-ppp is in no repository: it is built on the node from the GitHub tarball of a
fixed release whose size and sha256 are checked before anything is unpacked (a mismatch deletes the file and
refuses):

    /opt/kaskad/pkg/accel-ppp-1.14.0.tar.gz   the verified tarball (kept: a rebuild needs no download)
    /opt/kaskad/pkg/accel-ppp-1.14.0/         stage/ (``make install DESTDIR=stage``) + build.json (version,
                                              sha256, cmake flags, patches); a stage whose build.json matches
                                              is copied again instead of compiling (src/ and build/ are removed)
    /opt/kaskad/accel-ppp/                    the install: sbin/accel-pppd, bin/accel-cmd, lib/accel-ppp/*.so,
                                              share/accel-ppp/l2tp/dictionary, .kaskad-build.json

The install prefix is /opt/kaskad/accel-ppp itself (staged with DESTDIR, then copied): the module path, the
L2TP dictionary and the RPATH of libtriton compiled into the binaries point at the install, so an accel-ppp of
the system in /usr/lib*/accel-ppp can never be loaded instead. The unit still gives the config, the module
path and LD_LIBRARY_PATH explicitly. Build packages (kept): build-essential cmake libpcre2-dev libssl-dev
liblua5.1-0-dev pkg-config, curl and ca-certificates for the download. ~40 s packages + ~30 s compile on
1 vCPU. The patches of lib/kaskad/vendor (PATCHES, ``patch -p1``; patch comes with build-essential) go onto the
unpacked sources before cmake: accel-ppp-l2tp-peer-restart.patch (PPP-1) — an SCCRQ from the address and
port of an existing L2TP tunnel replaces that tunnel (``peer-restart=1`` in [l2tp]), so Windows and routers
reconnect at once instead of 30 s to minutes later. The marker and build.json carry the sha256 of each
patch: an install of an earlier build is built again by ``apply`` (``upgrade``) and the unit restarted.

Kernel: ppp_generic (CONFIG_PPP) and the modules of each inbound — l2tp_ppp (L2TP), pptp + gre + ppp_mppe
(PPTP), ppp_async (SSTP: PPP over a pty). A kernel without them (cloud kernels) is refused with a clear
message; Kaskad never switches kernels.

Service kaskad-gw-accel.service: ``accel-pppd -c /etc/kaskad/gw/accel/accel-ppp.conf`` in the foreground.
Files in /etc/kaskad/gw/accel/ (0600, scripts 0755):
    accel-ppp.conf   [modules] of the enabled PPP inbounds; chap-secrets is the only source of logins;
                     MS-CHAPv2 (+ MS-CHAPv1 and CHAP-MD5 for routers); [dns] the gateway DNS; MTU/MRU 1400;
                     interfaces kgw-l2tp*, kgw-pptp*, kgw-sstp* (apart from a system's own ppp*);
                     [client-ip-range] everything but the gateway's own net (no tunnel inside a tunnel);
                     one session per login (a reconnect replaces the old one); [cli] tcp=127.0.0.1:2001
                     with ``password=`` (secret ``accel.cli.password``, made once): every ``accel-cmd``
                     call gives it with ``-P`` (accel-cmd masks its argv at once; it is sent as the first
                     line); nothing on the loopback (SOCKS5 clients, xray) can list or cut sessions. A new
                     password is a reload (SIGUSR1 re-reads [cli]), not a restart
    chap-secrets     ``login * "password" 10.77.5.x`` — ONE address (pool "ppp") per device, whatever PPP
                     inbounds it has; two accounts at most: L2TP and SSTP — the device login and its shared
                     password (``dev.<id>.password``, also IKEv2 and OpenConnect: TLS or IPsec around it); PPTP —
                     ``<login>-pptp`` and a password of its own (``dev.<id>.pptp.password``): MS-CHAPv2 without
                     TLS is breakable, a password caught there opens nothing else. Both accounts carry the same
                     address: with ``check-ip=1`` the second one online at the same time is refused (IPCP:
                     «address already assigned») — one device uses one protocol at a time
    allow/<kind>     logins given that inbound: chap-secrets cannot tell L2TP, PPTP and SSTP apart, so
                     ip-pre-up refuses a login on an inbound it was not given (allow/pptp holds <login>-pptp)
    ip-pre-up, ip-up, ip-down   pppd-compat scripts; up/down → ``kaskadctl gw hook <kind> up|down <login> <ip>``
                     in the background (accounting never delays or refuses a session)
    l2tp-guard.sh    L2TP only through IPsec: an xfrm policy makes the kernel drop UDP to port 1701 that did
                     not come through an IPsec transport SA — unless plain L2TP is on; the unit sets it
                     before accel-ppp starts and removes it when it stops
    env              MODULES for the unit; accel.json (kinds, SSTP port, guard) and users.json (login → device)
                     let status() work without the state

apply(): chap-secrets and allow/* are read at every login — device changes need no reload at all; other
settings → ``systemctl reload`` (SIGUSR1: accel-ppp re-reads its config, the SSTP certificate too, sessions
stay); modules, ports, the CLI or the env changed, or accel-ppp built again (``upgrade``) → restart. Sessions
of removed devices, of a login that lost the inbound, got another address or another password (``gw device
reset``) are cut one by one (``accel-cmd terminate if <iface> soft``) — the other sessions stay.

Secrets: ``dev.<id>.password`` (shared by L2TP, SSTP, IKEv2, OpenConnect), ``dev.<id>.pptp.password``; SSTP's
certificate is kaskad.gw.tls. The kinds installed on this node are remembered in kinds.json (install/uninstall
run without the state): the unit and the configs go away with the last of them.
"""

import hashlib
import ipaddress
import json
import os
import shutil

from kaskad import paths, util, vendor
from kaskad.gw import issue_os, tls
from kaskad.gw.inbounds import native, tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.i18n import retr, tr, trs

VERSION = "1.14.0"
URL = "https://github.com/accel-ppp/accel-ppp/archive/refs/tags/%s.tar.gz" % VERSION
SHA256 = "9386c0b34ae9dd35e61c254980fd9f4e64c510f38fc97fe939b51625418f89f7"
SIZE = 1925929
PREFIX = "/opt/kaskad/accel-ppp"        # compiled into the binaries: the production path, never under KASKAD_ROOT
PKG = "/opt/kaskad/pkg"
MARKER = ".kaskad-build.json"
BUILD_DEPS = ["build-essential", "cmake", "libpcre2-dev", "libssl-dev", "liblua5.1-0-dev", "pkg-config", "curl",
              "ca-certificates"]
CMAKE_FLAGS = ["-DBUILD_IPOE_DRIVER=FALSE", "-DBUILD_VLAN_MON_DRIVER=FALSE", "-DCMAKE_INSTALL_PREFIX=" + PREFIX,
               "-DLIB_SUFFIX=", "-DCMAKE_BUILD_TYPE=Release", "-DLUA=TRUE", "-DSHAPER=TRUE", "-DRADIUS=TRUE"]
# lib/kaskad/vendor, onto the unpacked sources with patch -p1 before cmake; the file and the text that prove it is in
PATCHES = ("accel-ppp-l2tp-peer-restart.patch",)
PATCHED = ("accel-pppd/ctrl/l2tp/l2tp.c", "conf_peer_restart")
REBUILD_FAILED = "rebuild-failed.json"   # in PKG: the last failed ``upgrade`` — not tried again for a day
REBUILD_RETRY = 24 * 3600
UNIT = "kaskad-gw-accel.service"
KINDS = ("l2tp", "pptp", "sstp")
TITLES = {"l2tp": "L2TP", "pptp": "PPTP", "sstp": "SSTP"}
ADDR_KIND = "ppp"
CLI_HOST, CLI_PORT = "127.0.0.1", 2001
CLI_SECRET = "accel.cli.password"
IFACE = {"l2tp": "kgw-l2tp", "pptp": "kgw-pptp", "sstp": "kgw-sstp"}
FIXED_PORTS = {"l2tp": (1701, "udp"), "pptp": (1723, "tcp")}
SSTP_PORT = 4433
MTU = 1400
# kernel modules an inbound cannot work without (ppp_generic for all); ppp_mppe is loaded when present
MODULES = {"l2tp": ("l2tp_ppp",), "pptp": ("pptp", "gre", "ppp_mppe"), "sstp": ("ppp_async",)}
FOREIGN = {"l2tp": ("xl2tpd.service",), "pptp": ("pptpd.service",), "sstp": ()}
GUARD_PRIORITY = 4000000000              # below every policy strongSwan installs (higher number = later)
SESSION_COLUMNS = "ifname,username,ip,type,state,uptime-raw,rx-bytes-raw,tx-bytes-raw"
PPTP_SUFFIX = "-pptp"                    # the PPTP login of a device: <login>-pptp


# ── names ──────────────────────────────────────────────────────────────────

def conf_dir():
    return tunnel.kind_dir("accel")


def cpath(name):
    return os.path.join(conf_dir(), name)


def conf_path():
    return cpath("accel-ppp.conf")


def chap_path():
    return cpath("chap-secrets")


def allow_dir():
    return cpath("allow")


def guard_path():
    return cpath("l2tp-guard.sh")


def install_dir():
    return paths.P(PREFIX)


def accel_pppd():
    return os.path.join(install_dir(), "sbin", "accel-pppd")


def accel_cmd():
    return os.path.join(install_dir(), "bin", "accel-cmd")


def lib_dir():
    return os.path.join(install_dir(), "lib", "accel-ppp")


def pkg_dir():
    return paths.P(PKG)


def tarball_path():
    return os.path.join(pkg_dir(), "accel-ppp-%s.tar.gz" % VERSION)


def build_dir():
    return os.path.join(pkg_dir(), "accel-ppp-%s" % VERSION)


def stage_root():
    """Where ``make install DESTDIR=stage`` puts the prefix."""
    return os.path.join(build_dir(), "stage", PREFIX.lstrip("/"))


# ── settings of the state ──────────────────────────────────────────────────

def l2tp_modes(st):
    """(ipsec, plain) of the L2TP inbound: L2TP/IPsec by default, plain L2TP only when switched on."""
    cfg = tunnel.inbound_cfg(st, "l2tp") or {}
    return cfg.get("ipsec", True) is not False, cfg.get("plain", False) is True


def wanted(st, kind):
    """The inbound should accept sessions: installed, enabled, the gateway on (L2TP: IPsec or plain on)."""
    if not tunnel.wanted(st, kind):
        return False
    if kind == "l2tp":
        return any(l2tp_modes(st))
    return True


def kinds_wanted(st):
    return [k for k in KINDS if wanted(st, k)]


def kinds_present(st):
    return [k for k in KINDS if tunnel.inbound_cfg(st, k) is not None]


def guard_on(st):
    ipsec, plain = l2tp_modes(st)
    return wanted(st, "l2tp") and ipsec and not plain


SSL_PROTOCOL = {"1.2-1.3": "tls1.2,tls1.3", "1.3": "tls1.3", "1.2": "tls1.2"}   # tunnel.tls_versions -> [sstp] (F4)


def sstp_port(st):
    return tunnel.port_value(tunnel.inbound_cfg(st, "sstp") or {}, "port", SSTP_PORT)


def sstp_cert(st):
    """(certificate, key) paths of SSTP: the copy of the domain's certificate of Let's Encrypt after ``kaskadctl site
    use sstp on`` (kaskad.site, docs/k3/SPEC-wave4.md §4.1), else the node's self-signed one (kaskad.gw.tls)."""
    from kaskad.site import model as smodel
    return smodel.tls_pair(st, "sstp") or (tls.cert_path(), tls.key_path())


def server_ip():
    net = ipaddress.ip_network(tunnel.subnet(None, ADDR_KIND), strict=False)
    return str(net.network_address + 1)


def client_ranges(st):
    """[client-ip-range]: the addresses tunnels may come FROM — every IPv4 address but the gateway's own net.
    accel-ppp checks both sides (iprange.c): a tunnel from outside the range is dropped, and a PPP address
    INSIDE it is never assigned ("to avoid kernel soft lockup"). So 0.0.0.0/0 would refuse every session,
    and no range at all refuses every tunnel; the gateway's net is the hole for 10.77.5.x."""
    raw = (tunnel.gateway(st).get("net") or {}).get("base") or "10.77.0.0/16"
    try:
        base = ipaddress.ip_network(str(raw), strict=False)
    except ValueError:
        base = ipaddress.ip_network("10.77.0.0/16")
    return [str(n) for n in sorted(ipaddress.ip_network("0.0.0.0/0").address_exclude(base))]


def _password_ok(pw):
    return isinstance(pw, str) and pw and all(32 <= ord(ch) < 127 for ch in pw)


def pptp_key(device_id):
    return "dev.%s.pptp.password" % device_id


def account_of(device_id, login, kind):
    """(login, secret key) of a device's account on a PPP inbound."""
    if kind == "pptp":
        return login + PPTP_SUFFIX, pptp_key(device_id)
    return login, tunnel.shared_password_key(device_id)


def users(st, sec, kinds):
    """PPP accounts of the given inbounds that can log in: [(device, login, [kinds], password)], sorted by
    login. A device has up to two: its login with the shared password (L2TP, SSTP) and ``<login>-pptp`` with
    the PPTP password; both on the device's one address. A login held by two devices goes to the first (by
    id); an account without a usable login, password or address is left out."""
    by_id = {}
    for kind in kinds:
        for d in tunnel.devices(st, kind):
            by_id.setdefault(d["id"], (d, []))[1].append(kind)
    out, seen = [], set()
    for dev_id in sorted(by_id):
        d, ks = by_id[dev_id]
        base = tunnel.login(d)
        if not base or not tunnel.device_ip(d, ADDR_KIND):
            continue
        accounts = {}
        for k in ks:
            lg, key = account_of(dev_id, base, k)
            accounts.setdefault((lg, key), []).append(k)
        for (lg, key), aks in sorted(accounts.items()):
            pw = tunnel.sget(sec, key)
            if lg in seen or not _password_ok(pw):
                continue
            seen.add(lg)
            out.append((d, lg, aks, pw))
    return sorted(out, key=lambda x: x[1])


# ── keys, addresses ────────────────────────────────────────────────────────

def prepare(st, sec, runner=None):
    """Addresses (pool "ppp", one per device) and passwords of the devices of every installed PPP inbound
    (the shared one for L2TP and SSTP, PPTP's own for PPTP); SSTP: its port and the node's TLS certificate.
    Changes the state in place. -> created (no secrets)."""
    done = []
    by_id, skipped = {}, []
    for kind in kinds_present(st):
        for d in tunnel.devices(st, kind):
            by_id.setdefault(d["id"], (d, set()))[1].add(kind)
    for dev_id in sorted(by_id):
        d, ks = by_id[dev_id]
        if not tunnel.login(d):
            skipped.append(dev_id)
            continue
        _, new_ip = tunnel.ensure_ip(st, d, ADDR_KIND)
        if new_ip:
            done.append(trs("gw_in.made.addr", device=dev_id))
        if ks - {"pptp"} and tunnel.ensure_password(sec, d)[1]:
            done.append(trs("gw_in.made.password", device=dev_id))
        if "pptp" in ks and tunnel.ensure_secret(sec, pptp_key(dev_id))[1]:
            done.append(trs("gw_in_accel.made.pptp_password", device=dev_id))
    if skipped:
        tunnel.log_tr("ppp", "gw_in_accel.log.skipped", devices=", ".join(skipped))
    if kinds_present(st) and not _password_ok(tunnel.sget(sec, CLI_SECRET)):
        tunnel.sput(sec, CLI_SECRET, tunnel.secrets.new_password(24))
        done.append(trs("gw_in_accel.made.cli_password"))
    cfg = tunnel.inbound_cfg(st, "sstp")
    if cfg is not None:
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = SSTP_PORT
            done.append(trs("gw_in_accel.made.sstp_port", port=SSTP_PORT))
        done += tls.ensure(st, sec, runner, cfg)
    return done


# ── render ─────────────────────────────────────────────────────────────────

def conf_text(st, kinds, cli_password=None):
    lines = ["# Kaskad 3 gateway: accel-ppp of the PPP inbounds (%s). Written by kaskadctl, edits are lost."
             % (", ".join(TITLES[k] for k in kinds) or "none"),
             "[modules]",
             "path=%s" % lib_dir(),
             "log_syslog",
             "chap-secrets",
             "auth_mschap_v2",
             "auth_mschap_v1",
             "auth_chap_md5",
             "pppd_compat"]
    lines += list(kinds)
    lines += ["",
              "[core]",
              "log-error=/var/log/kaskad-gw-accel/core.log",
              "",
              "[common]",
              "single-session=replace",
              "check-ip=1",
              "",
              "[ppp]",
              "verbose=0",
              "min-mtu=1280",
              "mtu=%d" % MTU,
              "mru=%d" % MTU,
              "ipv4=require",
              "ipv6=deny",
              "lcp-echo-interval=20",
              "lcp-echo-failure=3",
              ""]
    if "l2tp" in kinds:
        lines += ["[l2tp]",
                  "verbose=0",
                  "dictionary=%s/share/accel-ppp/l2tp/dictionary" % install_dir(),
                  "hello-interval=60",
                  "# Kaskad's patch (vendor/%s): an SCCRQ from the address of a tunnel replaces it" % PATCHES[0],
                  "peer-restart=1",
                  "mppe=allow",
                  "ppp-max-mtu=%d" % MTU,
                  "ifname=%s%%d" % IFACE["l2tp"],
                  ""]
    if "pptp" in kinds:
        lines += ["[pptp]",
                  "verbose=0",
                  "echo-interval=30",
                  "mppe=require",
                  "ppp-max-mtu=%d" % MTU,
                  "ifname=%s%%d" % IFACE["pptp"],
                  ""]
    if "sstp" in kinds:
        lines += ["[sstp]",
                  "verbose=0",
                  "port=%d" % sstp_port(st),
                  "accept=ssl",
                  "ssl-protocol=%s" % SSL_PROTOCOL[tunnel.tls_versions(tunnel.inbound_cfg(st, "sstp"))],
                  "ssl-prefer-server-ciphers=1",
                  "ssl-pemfile=%s" % sstp_cert(st)[0],
                  "ssl-keyfile=%s" % sstp_cert(st)[1],
                  "cert-hash-proto=sha1,sha256",
                  "ppp-max-mtu=%d" % MTU,
                  "ifname=%s%%d" % IFACE["sstp"],
                  ""]
    lines += ["[dns]",
              "dns1=%s" % tunnel.dns(st),
              "",
              "[client-ip-range]"] + client_ranges(st) + [
              "",
              "[chap-secrets]",
              "gw-ip-address=%s" % server_ip(),
              "chap-secrets=%s" % chap_path(),
              "",
              "[pppd-compat]",
              "verbose=0",
              "ip-pre-up=%s" % cpath("ip-pre-up"),
              "ip-up=%s" % cpath("ip-up"),
              "ip-down=%s" % cpath("ip-down"),
              "",
              "[log]",
              "syslog=kaskad-gw-accel,daemon",
              "level=3",
              "",
              "[cli]",
              "verbose=0",
              "tcp=%s:%d" % (CLI_HOST, CLI_PORT)]
    if _password_ok(cli_password):
        lines.append("password=%s" % cli_password)
    lines.append("")
    return "\n".join(lines)


def _quote(value):
    """A chap-secrets field in double quotes (accel-ppp: a backslash escapes the next character)."""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def chap_text(st, sec, kinds):
    lines = ["# Kaskad 3 gateway: logins of L2TP, PPTP and SSTP, one address per device. Written by kaskadctl.",
             "# client\tserver\tsecret\tIP"]
    for d, lg, _, pw in users(st, sec, kinds):
        lines += ["# device %s" % d["id"],
                  "%s\t*\t%s\t%s" % (lg, _quote(pw), tunnel.device_ip(d, ADDR_KIND))]
    return "\n".join(lines) + "\n"


def _case_kind(fallback):
    lines = ['case "$1" in']
    for k in KINDS:
        lines.append("    %s*) kind=%s ;;" % (IFACE[k], k))
    lines.append("    *) exit %d ;;" % fallback)
    lines.append("esac")
    return lines


def pre_up_text():
    return "\n".join([
        "#!/bin/sh",
        "# Kaskad 3 gateway: accel-ppp ip-pre-up. chap-secrets is shared by L2TP, PPTP and SSTP, so here the",
        "# login must have THIS inbound (allow/<kind>); a non-zero exit refuses the session before it is up.",
        "# accel-ppp runs it as <script> <ifname> none 0 <local-ip> <peer-ip> <calling-sid>, PEERNAME=<login>.",
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        "export PATH",
    ] + _case_kind(1) + [
        '[ -n "$PEERNAME" ] || exit 1',
        'exec grep -qxF -e "$PEERNAME" "%s/$kind"' % allow_dir(),
        "",
    ])


def hook_text(action):
    return "\n".join([
        "#!/bin/sh",
        "# Kaskad 3 gateway: accel-ppp ip-%s -> kaskadctl gw hook <kind> %s <login> <ip>. Session accounting"
        % (action, action),
        "# only: in the background, never waits, never fails. Arguments: <ifname> none 0 <local-ip> <peer-ip>",
        "# <calling-sid>; PEERNAME=<login>; accel-ppp passes no PATH.",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "export PATH",
    ] + _case_kind(0) + [
        '%s gw hook "$kind" %s "$PEERNAME" "$5" </dev/null >/dev/null 2>&1 &' % (paths.kaskadctl(), action),
        "exit 0",
        "",
    ])


def guard_text(on):
    return "\n".join([
        "#!/bin/sh",
        "# Kaskad 3 gateway: L2TP only through IPsec. The xfrm policy below makes the kernel drop UDP to port",
        "# 1701 that did not come through an IPsec transport SA (plain L2TP); IPsec clients pass it or the",
        "# more specific policies of strongSwan. GUARD=0 when plain L2TP is on or L2TP is off. Run by",
        "# kaskad-gw-accel.service (start: set; stop: \"off\") and by kaskadctl when the setting changes.",
        "GUARD=%d" % (1 if on else 0),
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
        'sel="src 0.0.0.0/0 dst 0.0.0.0/0 proto udp dport 1701"',
        "ip xfrm policy delete dir in $sel 2>/dev/null",
        '[ "$1" = off ] && exit 0',
        '[ "$GUARD" = 1 ] || exit 0',
        "exec ip xfrm policy add dir in $sel priority %d tmpl proto esp mode transport level required"
        % GUARD_PRIORITY,
        "",
    ])


def modules_for(kinds):
    """Kernel modules the unit loads for these inbounds (ppp_mppe whenever there is PPP: MPPE on request)."""
    out = []
    for m in ["ppp_generic"] + [m for k in kinds for m in MODULES[k]] + (["ppp_mppe"] if kinds else []):
        if m not in out:
            out.append(m)
    return out


def render(st, sec):
    """Every file of the engine from the state (the enabled PPP inbounds) and the secrets."""
    kinds = kinds_wanted(st)
    if "sstp" in kinds and not tls.ready(sec):
        raise InboundError(tr("gw_in_accel.err.no_tls"))
    us = users(st, sec, kinds)
    info = {"kinds": kinds, "sstp_port": sstp_port(st) if "sstp" in kinds else None, "guard": guard_on(st),
            "l2tp_ipsec": "l2tp" in kinds and l2tp_modes(st)[0], "l2tp_plain": "l2tp" in kinds and l2tp_modes(st)[1]}
    files = {
        conf_path(): (conf_text(st, kinds, tunnel.sget(sec, CLI_SECRET)), 0o600),
        chap_path(): (chap_text(st, sec, kinds), 0o600),
        cpath("ip-pre-up"): (pre_up_text(), 0o755),
        cpath("ip-up"): (hook_text("up"), 0o755),
        cpath("ip-down"): (hook_text("down"), 0o755),
        guard_path(): (guard_text(guard_on(st)), 0o755),
        cpath("env"): ("# Kaskad 3 gateway: settings of %s (written by kaskadctl)\nMODULES=%s\n"
                       % (UNIT, " ".join(modules_for(kinds))), 0o600),
        cpath("accel.json"): (json.dumps(info, sort_keys=True) + "\n", 0o600),
        cpath("users.json"): (json.dumps(dict((lg, d["id"]) for d, lg, _, _ in us), indent=2, sort_keys=True) + "\n",
                              0o600),
    }
    for k in KINDS:
        allowed = sorted(lg for _, lg, ks, _ in us if k in ks)
        files[os.path.join(allow_dir(), k)] = ("".join(lg + "\n" for lg in allowed), 0o600)
    if "sstp" in kinds:
        files.update(tls.files(sec))
    return files


def unit_texts(runner):
    text = "\n".join([
        "[Unit]",
        "Description=Kaskad 3 gateway: accel-ppp (L2TP, PPTP, SSTP)",
        "After=network-online.target kaskad-net.service",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        "EnvironmentFile=-%s" % cpath("env"),
        "Environment=LD_LIBRARY_PATH=%s" % lib_dir(),
        "LogsDirectory=kaskad-gw-accel",
        "ExecStartPre=-%s -a $MODULES" % tunnel.which(runner, "modprobe", "/sbin/modprobe"),
        "ExecStartPre=-/bin/sh %s" % guard_path(),
        "ExecStart=%s -c %s" % (accel_pppd(), conf_path()),
        "ExecReload=/bin/kill -USR1 $MAINPID",
        "ExecStopPost=-/bin/sh %s off" % guard_path(),
        # сессии ушли вместе со службой, а ip-down их не отметил: иначе «в сети» навсегда (стенд T-O, O-9)
        "ExecStopPost=-%s gw hook-reset l2tp pptp sstp" % paths.kaskadctl(),
        "Restart=on-failure",
        "RestartSec=5s",
        "LimitNOFILE=65536",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    return {UNIT: text}


# ── build and install ──────────────────────────────────────────────────────

def patch_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(vendor.__file__)), name)


def build_info():
    return {"version": VERSION, "sha256": SHA256, "flags": list(CMAKE_FLAGS),
            "patches": [[name, _sha_of(patch_path(name))] for name in PATCHES]}


def _same_build(path):
    doc = tunnel.read_json(path, None)
    return isinstance(doc, dict) and all(doc.get(k) == v for k, v in build_info().items())


def install_ok():
    return _same_build(os.path.join(install_dir(), MARKER)) and os.path.isfile(accel_pppd())


def stage_ok():
    return _same_build(os.path.join(build_dir(), "build.json")) \
        and os.path.isfile(os.path.join(stage_root(), "sbin", "accel-pppd"))


def tarball_ok(path):
    """Size and sha256 are the ones checked by hand on the release (pure Python, no tool trusted)."""
    try:
        if os.path.getsize(path) != SIZE:
            return False
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest() == SHA256
    except OSError:
        return False


def _sha_of(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return "?"


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _step(runner, argv, what, timeout):
    res = runner.run(argv, timeout=timeout)
    if not res.ok:
        raise InboundError(tr("gw_in_accel.err.step", version=VERSION, what=what, error=tunnel.cmd_error(res)))
    return res


def fetch(runner):
    """The verified tarball: the kept one, else a download. A tarball that differs is deleted, the build
    refused."""
    path = tarball_path()
    if os.path.isfile(path):
        if tarball_ok(path):
            return path
        _unlink(path)
    if not os.path.isdir(pkg_dir()):
        os.makedirs(pkg_dir(), 0o755)
    part = path + ".part"
    _unlink(part)
    res = runner.run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "20", "--max-time", "600",
                      "-o", part, URL], timeout=700)
    if not res.ok:
        _unlink(part)
        raise InboundError(tr("gw_in_accel.err.download", url=URL, error=tunnel.cmd_error(res)))
    if not tarball_ok(part):
        got = _sha_of(part)
        size = os.path.getsize(part) if os.path.exists(part) else 0
        _unlink(part)
        raise InboundError(tr("gw_in_accel.err.sha", url=URL, got=got[:12], size=util.n_bytes(size),
                              want=SHA256[:12], want_size=util.n_bytes(SIZE)))
    os.rename(part, path)
    return path


def build(runner):
    """Packages, the verified tarball, the patches, cmake, make, ``make install`` into the stage, build.json."""
    tunnel.install_packages(runner, BUILD_DEPS)
    tarball = fetch(runner)
    bdir = build_dir()
    if os.path.isdir(bdir):
        shutil.rmtree(bdir)
    src_parent = os.path.join(bdir, "src")
    os.makedirs(src_parent, 0o755)
    _step(runner, ["tar", "-xzf", tarball, "-C", src_parent], tr("gw_in_accel.step.unpack"), 300)
    src = os.path.join(src_parent, "accel-ppp-%s" % VERSION)
    if not os.path.isfile(os.path.join(src, "CMakeLists.txt")):
        raise InboundError(tr("gw_in_accel.err.no_sources", version=VERSION))
    for name in PATCHES:
        _step(runner, ["patch", "-d", src, "-p1", "-N", "-s", "-i", patch_path(name)], tr("gw_in_accel.step.patch", name=name), 120)
    if PATCHED[1] not in (util.read_text(os.path.join(src, PATCHED[0])) or ""):
        raise InboundError(tr("gw_in_accel.err.patch", version=VERSION, patch=PATCHES[0], file=PATCHED[0]))
    obj = os.path.join(bdir, "build")
    _step(runner, ["cmake", "-S", src, "-B", obj] + CMAKE_FLAGS, "cmake", 600)
    _step(runner, ["make", "-C", obj, "-j%d" % max(1, min(os.cpu_count() or 1, 4))], "make", 3600)
    _step(runner, ["make", "-C", obj, "install", "DESTDIR=" + os.path.join(bdir, "stage")], "make install", 600)
    if not os.path.isfile(os.path.join(stage_root(), "sbin", "accel-pppd")):
        raise InboundError(tr("gw_in_accel.err.no_daemon", version=VERSION))
    shutil.rmtree(src_parent, ignore_errors=True)
    shutil.rmtree(obj, ignore_errors=True)
    util.atomic_write(os.path.join(bdir, "build.json"), json.dumps(build_info(), indent=2) + "\n", 0o644)


def copy_stage():
    """The stage → /opt/kaskad/accel-ppp: a full copy next to it, then a rename (a running accel-pppd keeps
    its open files)."""
    dst = install_dir()
    parent = os.path.dirname(dst)
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o755)
    tmp, old = dst + ".new", dst + ".old"
    for p in (tmp, old):
        if os.path.lexists(p):
            shutil.rmtree(p, ignore_errors=True)
    shutil.copytree(stage_root(), tmp, symlinks=True)
    util.atomic_write(os.path.join(tmp, MARKER), json.dumps(build_info(), indent=2) + "\n", 0o644)
    if os.path.lexists(dst):
        os.rename(dst, old)
    os.rename(tmp, dst)
    shutil.rmtree(old, ignore_errors=True)


def ensure_build(runner):
    """accel-ppp in /opt/kaskad/accel-ppp: kept if it is this build, copied from a matching stage, else built.
    -> "уже стоит" | "из готовой сборки" | "собран" — words of the moment (gw_in_accel.build.*: kept, stage, built)."""
    if install_ok():
        return tr("gw_in_accel.build.kept")
    if stage_ok():
        how = "gw_in_accel.build.stage"
    else:
        build(runner)
        how = "gw_in_accel.build.built"
    copy_stage()
    res = runner.run([accel_pppd(), "-V"])
    if not res.ok:
        raise InboundError(tr("gw_in_accel.err.not_runs", version=VERSION, error=tunnel.cmd_error(res)))
    return tr(how)


def build_packages():
    """Packages the install of a PPP inbound still needs: the build tools, unless accel-ppp is built already."""
    return [] if install_ok() or stage_ok() else list(BUILD_DEPS)


def upgrade(runner):
    """accel-ppp installed by an earlier build (another version, flags or patches: e.g. before the PPP-1 patch) is
    built again — from the kept tarball with the build tools the install left: no apt and no download in apply. The
    running daemon keeps the old files until the unit restarts (apply does it). A build that fails leaves the old
    one in place, goes to the log and is not tried again for a day (``gw inbound install`` builds at once).
    -> ("", "") nothing to do, ("built", "") or ("failed", <why: words of the moment>)."""
    if install_ok() or not os.path.isfile(accel_pppd()):
        return "", ""
    mark = os.path.join(pkg_dir(), REBUILD_FAILED)
    last = tunnel.read_json(mark, None)
    if isinstance(last, dict) and last.get("build") == build_info() \
            and 0 <= tunnel.now() - (last.get("at") or 0) < REBUILD_RETRY:
        return "", ""
    why = ""
    if not stage_ok():
        missing = [p for p in BUILD_DEPS if not tunnel.pkg_installed(runner, p)]
        if missing:
            why = tr("gw_in_accel.why.no_tools", packages=", ".join(missing))
        elif not tarball_ok(tarball_path()):
            why = tr("gw_in_accel.why.no_tarball", path=tarball_path())
    if not why:
        try:
            ensure_build(runner)
        except InboundError as e:
            why = str(e)
    if why:
        tunnel.ensure_dir(pkg_dir())
        util.atomic_write(mark, json.dumps({"build": build_info(), "at": int(tunnel.now()), "why": why}) + "\n",
                          0o600)
        why += tr("gw_in_accel.why.build_now", kind=(installed_kinds() or ["l2tp"])[0])
        tunnel.log_tr("ppp", "gw_in_accel.log.not_rebuilt", version=VERSION, why=why)
        return "failed", why
    _unlink(mark)
    tunnel.log_tr("ppp", "gw_in_accel.log.rebuilt", version=VERSION, patches=", ".join(PATCHES))
    return "built", ""


def wanted_modules(kinds):
    want = ["ppp_generic"] + [m for k in kinds for m in MODULES[k] if m != "ppp_mppe"]
    if "pptp" in kinds:
        want.append("ppp_mppe")
    return want


def missing_modules(runner, kinds):
    return [m for m in wanted_modules(kinds) if not tunnel.module_available(runner, m)]


def kernel_refusal(kinds, missing):
    return tr("gw_in_accel.err.kernel", modules=", ".join(missing), kinds=", ".join(TITLES[k] for k in kinds))


# ── modules of the kernel that runs now and of the next one (F2, finding 7) ─
#
# Ubuntu splits the modules of a kernel: linux-modules-<release> and linux-modules-extra-<release> — l2tp_ppp is in
# the extras. Which meta package keeps the extras for the NEXT kernel depends on the flavor (``uname -r`` suffix):
#     generic + linux-generic / linux-image-generic (-hwe-24.04)  nothing: linux-image-generic depends on the extras
#     generic + linux-virtual (cloud images, most VPS)           linux-image-extra-virtual (-hwe-24.04): it depends on
#                                                                linux-image-generic — the extras AND linux-firmware
#                                                                (~0.8 GB installed), the only official way
#     aws, azure, gcp                                            linux-modules-extra-<flavor>
#     others (oracle ...)                                        none known: the extras of this kernel only
# (24.04 has no -kvm flavor any more: linux-kvm is a transitional package of linux-virtual.) Debian's kernels carry
# every module; its cloud kernel (-cloud-amd64) has no PPP at all — there ``gw check`` says to boot linux-image-amd64.
# unattended-upgrades installs a new kernel without the extras when no meta package pulls them: after the reboot
# accel-ppp runs and L2TP/PPTP do not — ``module_problems`` makes ``gw check`` say so, with the command.

EXTRA_META = {"aws": "linux-modules-extra-aws", "azure": "linux-modules-extra-azure", "gcp": "linux-modules-extra-gcp"}
GENERIC_METAS = ("linux-generic", "linux-image-generic", "linux-generic-hwe-24.04", "linux-image-generic-hwe-24.04")
VIRTUAL_HWE = "linux-image-virtual-hwe-24.04"
EXTRA_PREFIX = "linux-modules-extra-"


def _os_id():
    for line in (util.read_text(paths.P("/etc/os-release")) or "").splitlines():
        if line.startswith("ID="):
            return line.split("=", 1)[1].strip().strip("\"'").lower()
    return ""


def kernel_release(runner):
    res = runner.run(["uname", "-r"])
    return res.out.strip() if res.ok else ""


def flavor(release):
    """``6.8.0-45-generic`` -> "generic", ``6.8.0-1015-aws`` -> "aws"."""
    return release.rsplit("-", 1)[-1] if "-" in release else ""


def _from_extra(runner, module):
    """The module of the running kernel is a file of linux-modules-extra-* (``dpkg-query -S``)."""
    res = runner.run(["modinfo", "-n", module])
    path = res.out.strip() if res.ok else ""
    if not path.startswith("/"):
        return False
    q = runner.run(["dpkg-query", "-S", path])
    return q.ok and q.out.startswith(EXTRA_PREFIX)


def extra_meta(runner, release):
    """The meta package that brings linux-modules-extra to the next kernels of this flavor; None when none is needed
    (linux-image-generic is installed) or known."""
    fl = flavor(release)
    if fl in EXTRA_META:
        return EXTRA_META[fl]
    if fl != "generic" or any(tunnel.pkg_installed(runner, p) for p in GENERIC_METAS):
        return None
    if tunnel.pkg_installed(runner, VIRTUAL_HWE):
        return "linux-image-extra-virtual-hwe-24.04"
    return "linux-image-extra-virtual"


def extra_packages(runner, kinds):
    """Ubuntu: what to install so that the modules of ``kinds`` are there for this kernel AND the next ones —
    linux-modules-extra-<release> when one is missing, the meta package of the flavor when one is missing or comes
    from the extras. [] elsewhere (Debian) or when nothing is needed."""
    if _os_id() != "ubuntu":
        return []
    release = kernel_release(runner)
    if not release:
        return []
    missing = missing_modules(runner, kinds)
    if not missing and not any(_from_extra(runner, m) for m in wanted_modules(kinds)):
        return []
    pkgs = [EXTRA_PREFIX + release] if missing else []
    meta = extra_meta(runner, release)
    if meta:
        pkgs.append(meta)
    return [p for p in pkgs if not tunnel.pkg_installed(runner, p)]


def fix_text(runner, release):
    """What the owner runs to get the PPP modules of the running kernel back."""
    if _os_id() == "ubuntu" and release:
        pkgs = [EXTRA_PREFIX + release]
        meta = extra_meta(runner, release)
        if meta:
            pkgs.append(meta)
        return tr("gw_in_accel.fix.ubuntu", packages=" ".join(pkgs), unit=UNIT)
    if "-cloud-" in release:
        return tr("gw_in_accel.fix.cloud")
    return tr("gw_in_accel.fix.distro")


def module_problems(st, runner, repair=False, log=None):
    """``gw check``: the enabled PPP inbounds whose modules the running kernel lacks (a new kernel booted without
    linux-modules-extra: accel-ppp runs, L2TP/PPTP do not). Never repaired — apt is not for the minute watchdog: in
    repair mode the line goes to the log (the watchdog logs a kind once an hour), otherwise it is a problem
    ``{"kind": "gw_kernel", ...}`` with the command to run."""
    kinds = [k for k in KINDS if tunnel.wanted(st, k)]
    if not kinds:
        return []
    missing = missing_modules(runner, kinds)
    if not missing:
        return []
    release = kernel_release(runner)
    hit = [TITLES[k] for k in kinds if any(m in missing for m in wanted_modules([k]))]

    def detail():
        return tr("gw_in_accel.kernel_problem", inbounds=", ".join(hit), release=release or "?",
                  modules=", ".join(missing), fix=fix_text(runner, release))
    if repair:
        if log is not None:
            with util.journal_lang():
                log("gw_kernel", "GW check: %s" % detail())
        return []
    return [{"kind": "gw_kernel", "detail": detail(), "repaired": False}]


def foreign_units(runner, kind):
    """Services of other PPP servers that hold the ports now or will at the next boot (accel-ppp of the
    system, xl2tpd, pptpd)."""
    return [u for u in ("accel-ppp.service",) + FOREIGN[kind]
            if tunnel.is_active(runner, u) or tunnel.is_enabled(runner, u)]


def kinds_file():
    return cpath("kinds.json")


def installed_kinds():
    doc = tunnel.read_json(kinds_file(), [])
    return [k for k in KINDS if isinstance(doc, list) and k in doc]


def _mark(kind, on):
    have = installed_kinds()
    want = sorted(set(have + [kind])) if on else [k for k in have if k != kind]
    if want != have:
        tunnel.ensure_dir(conf_dir())
        util.atomic_write(kinds_file(), json.dumps(sorted(want)) + "\n", 0o600)
    return want


def install(runner, kind):
    """accel-ppp for one more PPP inbound: kernel check, build (or reuse), modules, the unit. Idempotent. On Ubuntu
    the extra modules of the kernel and the meta package of its flavor come first (``extra_packages``)."""
    extra = extra_packages(runner, [kind])
    if extra:
        tunnel.install_packages(runner, extra)
    missing = missing_modules(runner, [kind])
    if missing:
        raise InboundError(kernel_refusal([kind], missing))
    busy = foreign_units(runner, kind)
    if busy:
        raise InboundError(tr("gw_in_accel.err.busy", units=", ".join(busy), title=TITLES[kind], args=" ".join(busy)))
    how = ensure_build(runner)
    tunnel.modprobe(runner, modules_for([kind]))
    tunnel.ensure_dir(conf_dir())
    tunnel.ensure_dir(allow_dir())
    tunnel.install_units(runner, unit_texts(runner))
    _mark(kind, True)
    tunnel.log_tr(kind, "gw_in_accel.log.installed", version=VERSION, how=how)
    return how


def uninstall(runner, kind):
    """Forget one PPP inbound; with the last one the unit and the configs go (the build stays for a
    reinstall). -> True if the engine was removed."""
    left = _mark(kind, False)
    if left:
        return False
    tunnel.remove_units(runner, [UNIT])
    runner.run(["ip", "xfrm", "policy", "delete", "dir", "in", "src", "0.0.0.0/0", "dst", "0.0.0.0/0",
                "proto", "udp", "dport", "1701"])                  # the guard, if the unit left it behind
    tunnel.remove_tree(conf_dir())
    tunnel.log_tr("ppp", "gw_in_accel.log.removed", dir=PREFIX)
    return True


# ── apply ──────────────────────────────────────────────────────────────────

_RESTART_SECTIONS = ("modules", "core", "cli")
# ``ssl-protocol`` (3.3.7.15 F4): a restart, not a reload — whether SIGUSR1 gives the open socket new versions is not
# checked, and «only 1.3» written while 1.2 is still taken is worse than a few seconds of a break
_RESTART_KEYS = {"l2tp": ("bind", "port"), "pptp": ("bind", "port"), "sstp": ("bind", "port", "accept", "ssl-protocol")}


def restart_key(text):
    """What a reload cannot change: the modules, the core, the CLI (but its password: SIGUSR1 re-reads
    it — cli.c load_config is an EV_CONFIG_RELOAD handler), listening addresses and ports."""
    out, section = [], None
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        if section == "cli" and s.split("=", 1)[0] == "password":
            continue
        if section in _RESTART_SECTIONS or s.split("=", 1)[0] in _RESTART_KEYS.get(section, ()):
            out.append((section, s))
    return out


def chap_entries(text):
    """{login: (secret field, ip)} of a chap-secrets text written by chap_text (the secret as written — only
    compared, never shown)."""
    out = {}
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) >= 4:
            out[f[0]] = ("\t".join(f[2:-1]), f[-1].strip())
    return out


def _allowed_on_disk():
    out = {}
    for k in KINDS:
        out[k] = set(x for x in (util.read_text(os.path.join(allow_dir(), k)) or "").splitlines() if x)
    return out


def cli_password():
    """The password of accel-ppp's CLI (secrets.json), None before the first apply made it."""
    try:
        pw = tunnel.secrets.get(CLI_SECRET)
    except Exception:                           # noqa: BLE001 — a broken secrets file: try without
        return None
    return pw if _password_ok(pw) else None


def cli(runner, *args):
    """``accel-cmd -H 127.0.0.1 -p 2001 [-P <password>] <args>`` — the password never reaches a log."""
    argv = [accel_cmd(), "-H", CLI_HOST, "-p", str(CLI_PORT)]
    pw = cli_password()
    if pw:
        argv += ["-P", pw]
    return runner.run(argv + list(args), timeout=15)


def apply(st, sec, runner):
    """Configs from the state; the unit runs while a PPP inbound is enabled (reload or restart as needed);
    sessions that lost their right are cut. -> {"action", "changed", "prepared", "cut", "warnings"}."""
    prepared = prepare(st, sec, runner)
    tunnel.ensure_dir(conf_dir())
    tunnel.ensure_dir(allow_dir())
    old_conf = util.read_text(conf_path())
    old_chap = chap_entries(util.read_text(chap_path()))
    old_allow = _allowed_on_disk()
    files = render(st, sec)
    changed = tunnel.write_files(files)
    kinds = kinds_wanted(st)
    cut, warnings = [], []
    # a unit written by an older version is rewritten; no restart for it alone (it would cut every PPP
    # session) — the new text applies at the next start
    changed += [tunnel.unit_file(u) for u in tunnel.refresh_units(runner, unit_texts(runner))]
    # accel-ppp of an earlier build (PPP-1: without the patch of L2TP reconnects): built again, then restarted
    rebuilt, why = upgrade(runner) if kinds else ("", "")
    if rebuilt == "failed":
        warnings.append(tr("gw_in_accel.warn.not_built", why=why))
    if not kinds:
        action = "stopped" if tunnel.stop_unit(runner, UNIT) else "off"
    elif tunnel.run_unit(runner, UNIT, "accel-ppp"):
        action = "started"
    elif rebuilt == "built" or cpath("env") in changed \
            or restart_key(old_conf) != restart_key(files[conf_path()][0]):
        tunnel.systemctl(runner, "restart", UNIT)
        action = "restarted"
    else:
        reload_on = [conf_path(), tls.cert_path(), tls.key_path()]
        if any(p in changed for p in reload_on):
            tunnel.systemctl(runner, "reload", UNIT)
            action = "reloaded"
        else:
            action = "unchanged"
        if guard_path() in changed:
            res = runner.run(["/bin/sh", guard_path()])
            if not res.ok:
                warnings.append(tr("gw_in_accel.warn.guard", error=tunnel.cmd_error(res)))
        new_chap = chap_entries(files[chap_path()][0])
        new_allow = dict((k, set(x for x in files[os.path.join(allow_dir(), k)][0].splitlines() if x))
                         for k in KINDS)
        moved = set(lg for lg, entry in old_chap.items() if new_chap.get(lg) != entry)      # gone, new address
        if moved or any(old_allow[k] - new_allow[k] for k in KINDS):                        # or password
            cut = cut_sessions(runner, kinds, new_allow, new_chap, moved)
    names = [os.path.relpath(p, conf_dir()) if p.startswith(conf_dir()) else p for p in changed]
    if names or prepared or action not in ("unchanged", "off") or cut:
        with util.journal_lang():
            tunnel.log("ppp", tr("gw_in_accel.log.applied", action=action, kinds=", ".join(kinds) or tr("gw_in.log.none"),
                                 files=", ".join(names) or tr("gw_in.log.no_changes"),
                                 cut=tr("gw_in.log.cut", items=", ".join(cut)) if cut else "",
                                 created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
    return {"action": action, "changed": names, "prepared": prepared, "cut": cut, "warnings": warnings}


def cut_sessions(runner, kinds, allowed, chap, changed=()):
    """Terminate sessions whose inbound is gone, whose login lost it, got another address or another password
    (``changed``: logins whose chap-secrets line changed)."""
    out = []
    for s in sessions(runner):
        k, lg = s.get("type"), s.get("login")
        if k in kinds and lg in allowed.get(k, ()) and (chap.get(lg) or ("", None))[1] == s.get("ip") \
                and lg not in changed:
            continue
        if s.get("ifname"):
            cli(runner, "terminate", "if", s["ifname"], "soft")
            out.append("%s %s" % (k, lg))
    return out


# ── status ─────────────────────────────────────────────────────────────────

def parse_sessions(text):
    """``accel-cmd show sessions <columns>`` -> [{column: value}] (a table: header, a dashes line, rows).
    Lines before the header (no "|": e.g. "invalid command" — a daemon that has not re-read its password
    yet takes the password line for a command) are skipped."""
    rows, header = [], None
    for line in (text or "").replace("\r", "").splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("|")]
        if header is None:
            if len(cells) > 1:
                header = cells
            continue
        if set(line.strip()) <= set("-+"):
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _num(value):
    v = str(value or "").strip()
    return int(v) if v.isdigit() else 0


def sessions(runner):
    """Sessions of accel-ppp: [{"ifname", "login", "device", "ip", "type", "since", "rx", "tx"}]."""
    res = cli(runner, "show", "sessions", SESSION_COLUMNS)
    if not res.ok:
        return []
    owners = tunnel.read_json(cpath("users.json"), {})
    if not isinstance(owners, dict):
        owners = {}
    now = int(tunnel.now())
    out = []
    for r in parse_sessions(res.out):
        if not r.get("ifname"):
            continue
        up = _num(r.get("uptime-raw"))
        out.append({"ifname": r["ifname"], "login": r.get("username") or None,
                    "device": owners.get(r.get("username")), "ip": r.get("ip") or None, "type": r.get("type"),
                    "state": r.get("state"), "since": (now - up) if up else None,
                    "rx": _num(r.get("rx-bytes-raw")), "tx": _num(r.get("tx-bytes-raw"))})
    return out


def info():
    doc = tunnel.read_json(cpath("accel.json"), {})
    return doc if isinstance(doc, dict) else {}


def status(runner, kind):
    """Status of one PPP inbound (the engine's unit + whether the running config serves the kind)."""
    installed = tunnel.unit_installed(UNIT) and kind in installed_kinds()
    doc = info()
    running = installed and tunnel.is_active(runner, UNIT) and kind in (doc.get("kinds") or [])
    port = FIXED_PORTS[kind][0] if kind in FIXED_PORTS else doc.get("sstp_port")
    out = []
    if running:
        for s in sessions(runner):
            if s["type"] == kind:
                out.append(dict((k, s[k]) for k in ("device", "login", "ip", "since", "rx", "tx", "ifname")))
    return {"kind": kind, "installed": installed, "running": running, "port": port, "sessions": out,
            "units": [UNIT]}


# ── what the three PPP adapters share ──────────────────────────────────────

def client(st, sec, device, kind):
    """(login, password) of a device on a PPP inbound; InboundError if it cannot log in there."""
    for d, lg, _, pw in users(st, sec, [kind]):
        if d.get("id") == device.get("id"):
            return lg, pw
    raise InboundError(tr("gw_in.err.not_user_pw", device=tunnel.device_name(device), title=TITLES[kind]))


class PppInbound(Inbound):
    """L2TP, PPTP and SSTP: one accel-ppp (kaskad-gw-accel.service), one address per device (pool "ppp"),
    the device's login and password. Subclasses set kind/title and say which ports of their own UFW opens."""

    kind = ""
    engine = "accel-ppp"
    title = ""

    def dir(self):
        """Own directory of the inbound: only the memory of its UFW rules (ufw.json)."""
        return tunnel.kind_dir(self.kind)

    def ifaces(self):
        return [IFACE[self.kind] + "*"]

    def addr_kinds(self, st=None):
        return [ADDR_KIND]

    def default_port(self):
        return 0

    def defaults(self, st):
        return {}

    def units(self):
        return [UNIT]

    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def unit_texts(self, runner):
        return unit_texts(runner)

    def reset_secrets(self, device_id):
        """L2TP and SSTP log in with the device's shared password (PPTP overrides: its own)."""
        return [tunnel.shared_password_key(device_id)]

    def own_ports(self, st):
        """[(port, proto)] UFW opens for this inbound while it runs (port None: a whole protocol)."""
        port, proto = FIXED_PORTS[self.kind]
        return [(port, proto)]

    def warning(self, st=None):
        return ""

    def requirements(self, runner):
        missing = missing_modules(runner, [self.kind])
        busy = foreign_units(runner, self.kind)
        extra = extra_packages(runner, [self.kind])
        if missing and any(p.startswith(EXTRA_PREFIX) and p[len(EXTRA_PREFIX):][:1].isdigit() for p in extra):
            missing = []                                    # Ubuntu: the extras of this kernel bring them
        why = kernel_refusal([self.kind], missing) if missing else ""
        if busy and not why:
            why = tr("gw_in_accel.why.busy", units=", ".join(busy))
        mods = ["ppp_generic"] + list(MODULES[self.kind])
        ports = [{"proto": "tcp", "port": SSTP_PORT}] if self.kind == "sstp" else \
            [{"proto": FIXED_PORTS[self.kind][1], "port": FIXED_PORTS[self.kind][0]}]
        out = {"kind": self.kind, "engine": self.engine, "packages": build_packages() + extra, "modules": mods,
               "ports": ports, "supported": not why, "why": why,
               # only when the install will really build it (stand T-G2b, N-6): a kept build is copied in seconds
               "build": "" if install_ok() or stage_ok() else tr("gw_in_accel.build_note", version=VERSION)}
        virt = [p for p in extra if p.startswith("linux-image-extra-virtual")]
        if self.warning():
            out["warning"] = self.warning()
        elif virt:
            out["warning"] = tr("gw_in_accel.warn.extra_virtual", package=virt[0])
        return out

    def install(self, runner):
        install(runner, self.kind)

    def uninstall(self, runner):
        tunnel.close_ufw(runner, self.dir(), self.kind)
        uninstall(runner, self.kind)
        tunnel.remove_tree(self.dir())

    def prepare(self, st, sec, runner=None):
        return prepare(st, sec, runner)

    def render(self, st, sec):
        return render(st, sec)

    def apply(self, st, sec, runner):
        self.cfg(st)
        res = apply(st, sec, runner)
        tunnel.sync_ufw(runner, self.dir(), self.kind, self.own_ports(st) if wanted(st, self.kind) else [])
        return res

    def status(self, runner):
        out = status(runner, self.kind)
        if self.warning():
            out["warnings"] = [self.warning()]
        return out

    # ── issuance helpers ──
    def fields(self, host, login, password, extra=()):
        return [[tr("gw_in.label.server"), host]] + [list(x) for x in extra] + [[tr("gw_in.label.login"), login],
                                                                                 [tr("gw_in.label.password"), password]]

    def routeros(self, st, dev, data):
        return tunnel.record("conf", tr("gw_in.rec.rsc", title=self.title), native.routeros_ppp(self.kind, data),
                             "kaskad-%s.rsc" % self.kind, issue_os.steps("mikrotik", self.kind))
