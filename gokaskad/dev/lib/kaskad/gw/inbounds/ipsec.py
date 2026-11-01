"""strongSwan engine of L2TP/IPsec (l2tp.py) and IKEv2 (ikev2.py): one charon on UDP 500/4500 serves both.
Not an adapter — registers nothing; the two adapters call it.

Packages of the distribution (strongSwan 5.9.8 on Debian 12, 5.9.13 on Ubuntu 24.04): charon-systemd,
strongswan-swanctl, libstrongswan-standard-plugins (openssl: ECP groups, AES-GCM), libcharon-extauth-plugins
(eap-mschapv2), libcharon-extra-plugins (eap-identity) — listed one by one, Kaskad installs without
recommends. strongswan.service starts on install and takes UDP 500/4500: apt runs under policy-rc.d (101)
and strongswan.service is disabled and masked when Kaskad installed the package.

Own unit kaskad-gw-ipsec.service instead of the distribution's strongswan.service, with the SAME binaries
and paths (/usr/sbin/charon-systemd, /usr/sbin/swanctl, /etc/swanctl, /run/charon.vici): the AppArmor
profiles of Debian and Ubuntu confine swanctl to /etc/swanctl/** and the vici socket, so the config must
live in /etc/swanctl/conf.d/kaskad.conf, but the service is Kaskad's — it runs while L2TP/IPsec or IKEv2
is enabled, ``gw disable`` and uninstall stop it, status and the watchdog see it under its own name, and a
strongswan.service of the system is never started or reconfigured behind the administrator's back. One IKE
daemon per node: an active strongswan.service, strongswan-starter.service or ipsec.service (libreswan) that
Kaskad did not install refuses the install.

Files:
    /etc/swanctl/conf.d/kaskad.conf   (0600) connections kaskad-l2tp (IKEv1, PSK, transport mode, UDP 1701
                                      only) and kaskad-ikev2 (the node's certificate + EAP-MSCHAPv2 with the
                                      device's login and password, pool 10.77.8.10-254, the gateway DNS);
                                      secrets: the PSK and the EAP passwords (hex)
    /etc/swanctl/x509ca/kaskad-ca.pem, x509/kaskad-ikev2.pem, private/kaskad-ikev2.pem   the IKEv2 CA and the
                                      server certificate (RSA 2048; EKU serverAuth + ikeIntermediate; SAN and
                                      identity = gateway.host) with its key (0600)
    /etc/kaskad/gw/ipsec/updown.sh    IKEv2 updown → ``kaskadctl gw hook ikev2 up|down <EAP identity> <vip>``
    /etc/kaskad/gw/ipsec/             ipsec.json (kinds served), users.json (login → device), kinds.json
                                      (installed kinds), ufw.json

apply(): ``swanctl --load-all`` loads what changed and unloads what is gone; SAs that run are not touched,
except the ones of a device that lost IKEv2 or got another password (``gw device reset``: strongSwan keeps an
established SA whatever the new secret) and of a connection switched off — those are terminated one by one. UFW: UDP 500, UDP 4500 and ESP under the single comment ``kaskad-gw ipsec`` while L2TP/IPsec or IKEv2 is
enabled — the shared ports are never closed under the other inbound (UDP 1701 is the L2TP adapter's own).

Secrets: ``l2tp.psk``, ``ikev2.ca.key``/``.crt``, ``ikev2.server.key``/``.crt``, ``dev.<id>.password``.

The IKEv2 CA goes into the trusted roots of the clients (Windows LocalMachine\\Root, the Apple profile), so it
may vouch for this node only: nameConstraints on both name forms (``IP:<host>/32`` + ``DNS:<host>``, or
``DNS:<host>`` + ``IP:0.0.0.0/32`` — pki.py), the EKU of IKE on the CA too (no code signing, no S/MIME through
it on Windows), ``pathlen:0``, a CN of its own per node (``Kaskad IKEv2 CA <host> <random>``). A CA
made by an earlier build has no constraints: it is kept (devices that trust it keep working), ``status`` and
the issuance say so and ``kaskadctl gw pki rotate ikev2`` replaces it. A constrained CA that does not cover a new
gateway.host is replaced at once (the devices need new settings for a new address anyway).
"""

import ipaddress
import json
import os
import secrets as _random

from kaskad import paths, secrets, util
from kaskad.gw.inbounds import accel, pki, tunnel
from kaskad.gw.inbounds.base import InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import retr, tr, trs

UNIT = "kaskad-gw-ipsec.service"
KINDS = ("l2tp", "ikev2")
PACKAGES = ["charon-systemd", "strongswan-swanctl", "libstrongswan-standard-plugins", "libcharon-extauth-plugins",
            "libcharon-extra-plugins", "openssl"]
DISTRO_UNITS = {"charon-systemd": ["strongswan.service"]}
FOREIGN = ("strongswan.service", "strongswan-starter.service", "ipsec.service")
MODULES = ("esp4", "xfrm_user")
SWANCTL = "/etc/swanctl"
CONF = SWANCTL + "/conf.d/kaskad.conf"
CA_FILE = SWANCTL + "/x509ca/kaskad-ca.pem"
CERT_FILE = SWANCTL + "/x509/kaskad-ikev2.pem"
KEY_FILE = SWANCTL + "/private/kaskad-ikev2.pem"
CONN_L2TP = "kaskad-l2tp"
CONN_IKEV2 = "kaskad-ikev2"
POOL = "kaskad-ikev2"
CA_CN = "Kaskad IKEv2 CA"           # the CN of a CA of an earlier build; new ones: «Kaskad IKEv2 CA <host> <random>»
PSK = "l2tp.psk"
UFW_KIND = "ipsec"
RETIRED_KEEP = 5                    # thumbprints of replaced IKEv2 CAs the Windows script removes from Root
# NO_HOST, LEGACY_CA: words of the moment, read as ipsec.NAME
__getattr__ = lazy_texts(__name__, {"NO_HOST": "gw_in_ikev2.err.no_host", "LEGACY_CA": "gw_in_ipsec.legacy_ca"})

# IKEv2. strongSwan as a responder picks the first of OUR proposals that the client also offers.
IKEV2_PROPOSALS = [
    "aes256-sha256-modp2048",           # Windows with the issued script (Set-VpnConnectionIPsecConfiguration),
                                        # macOS/iOS .mobileconfig, Android 12+ built-in client
    "aes256gcm16-prfsha256-modp2048",   # Android 12+ and the strongSwan app (AEAD)
    "aes256-sha256-ecp256",             # macOS/iOS configured by hand, the strongSwan app
    "aes256gcm16-prfsha256-ecp256",
    "aes128-sha256-modp2048",
    "aes256-sha1-modp2048",
    "aes256-sha1-modp1024",             # Windows 10/11 out of the box (DH group 2) — only so that a connection
    "aes128-sha1-modp1024",             # made by hand works; the issued script moves Windows to group 14
]
# Without a group: the CHILD_SA made in IKE_AUTH; with a group: rekeys of clients that use PFS.
IKEV2_ESP = ["aes256gcm16", "aes128gcm16", "aes256-sha256", "aes128-sha256", "aes256-sha1", "aes128-sha1",
             "aes256gcm16-modp2048", "aes256-sha256-modp2048", "aes256gcm16-ecp256", "aes256-sha256-ecp256"]
# L2TP/IPsec (IKEv1 main mode, PSK).
L2TP_PROPOSALS = [
    "aes256-sha256-modp2048",           # macOS/iOS (iOS 26 / macOS 26 dropped 3DES, SHA1 and DH 2)
    "aes128-sha256-modp2048",
    "aes256-sha1-modp2048",             # MikroTik, Keenetic
    "aes128-sha1-modp2048",
    "aes256-sha1-modp1024",             # Windows L2TP/IPsec (DH group 2)
    "aes128-sha1-modp1024",
    "3des-sha1-modp1024",               # old routers (TP-Link, Huawei) know nothing better
]
L2TP_ESP = ["aes256-sha256", "aes128-sha256", "aes256-sha1", "aes128-sha1", "3des-sha1"]


# ── names ──────────────────────────────────────────────────────────────────

def own_dir():
    return tunnel.kind_dir("ipsec")


def opath(name):
    return os.path.join(own_dir(), name)


def conf_file():
    return paths.P(CONF)


def managed_files():
    return [paths.P(p) for p in (CA_FILE, CERT_FILE, KEY_FILE)]


# Плагины charon, которые шлюзу не нужны и дорого стоят: dhcp и farp держат AF_PACKET-сокет на внешнем
# интерфейсе и копируют в userspace КАЖДЫЙ пакет узла, forecast — широковещание. На 1 vCPU charon съедал
# 15–50 % ядра при любом трафике шлюза, даже WG/OpenVPN/SOCKS5 (стенд T-O, O-10: WG 336 Мбит/с — 70,6 % ЦП с
# ними, 23,5 % без). Адреса IKEv2 даёт пул swanctl, L2TP — в транспортном режиме: эти плагины не нужны.
PLUGINS_DROPIN = "/etc/strongswan.d/kaskad-gw.conf"
PLUGINS_OFF = ("dhcp", "farp", "forecast")


def plugins_dropin():
    return paths.P(PLUGINS_DROPIN)


def plugins_text():
    lines = ["# Kaskad 3 gateway: plugins charon of the gateway does not load (written by kaskadctl, edits are lost).",
             "# They copy every packet of the node to userspace (AF_PACKET) — see lib/kaskad/gw/inbounds/ipsec.py.",
             "charon-systemd {", "    plugins {"]
    lines += ["        %s {\n            load = no\n        }" % n for n in PLUGINS_OFF]
    lines += ["    }", "}", ""]
    return "\n".join(lines)


def swanctl_bin(runner):
    return tunnel.which(runner, "swanctl", "/usr/sbin/swanctl")


# ── settings of the state ──────────────────────────────────────────────────

def l2tp_on(st):
    """L2TP/IPsec is served: the L2TP inbound is enabled with IPsec on."""
    return accel.wanted(st, "l2tp") and accel.l2tp_modes(st)[0]


def ikev2_on(st):
    return tunnel.wanted(st, "ikev2")


def kinds_wanted(st):
    return [k for k, on in (("l2tp", l2tp_on(st)), ("ikev2", ikev2_on(st))) if on]


def ikev2_subnet(st):
    return tunnel.subnet(tunnel.inbound_cfg(st, "ikev2"), "ikev2")


def pool_range(st):
    """``10.77.8.10-10.77.8.254``: .1 is the node, devices start at .10 as everywhere in the gateway."""
    net = ipaddress.ip_network(ikev2_subnet(st), strict=False)
    return "%s-%s" % (net.network_address + 10, net.broadcast_address - 1)


def _host(st):
    try:
        return tunnel.host(st)
    except InboundError:
        return ""


def eap_users(st, sec):
    """IKEv2 devices that can log in: [(device, login, password)], sorted by login (a login held by two
    devices goes to the first by id)."""
    out, seen = [], set()
    for d in tunnel.devices(st, "ikev2"):
        lg = tunnel.login(d)
        pw = tunnel.sget(sec, "dev.%s.password" % d["id"])
        if not lg or lg in seen or not accel._password_ok(pw):
            continue
        seen.add(lg)
        out.append((d, lg, pw))
    return sorted(out, key=lambda x: x[1])


# ── keys and certificates ──────────────────────────────────────────────────

def ca_scope(ca_pem):
    """Names the IKEv2 CA may vouch for (``["IP:203.0.113.10/255.255.255.255"]``), None for a CA without
    nameConstraints (an earlier build) or none at all."""
    if not ca_pem:
        return None
    try:
        return pki.name_constraints(ca_pem)
    except (ValueError, IndexError, TypeError):
        return None


def ca_legacy(sec):
    """There is an IKEv2 CA and it has no nameConstraints: trusted by the devices for ANY name."""
    ca = tunnel.sget(sec, "ikev2.ca.crt")
    return bool(ca) and ca_scope(ca) is None


def ca_cn_of(ca_pem):
    """The CN of the IKEv2 CA (the .mobileconfig names it: ServerCertificateIssuerCommonName)."""
    try:
        return pki.subject_cn(ca_pem) or CA_CN
    except (ValueError, IndexError, TypeError):
        return CA_CN


def new_ca_cn(host):
    """«Kaskad IKEv2 CA 203.0.113.10 7f3a1c»: a name of its own per node (the random tail stays within 64)."""
    return "%s %s %s" % (CA_CN, str(host)[:40], _random.token_hex(3))


def _covers(scope, host):
    return pki.scope_name(host) in (scope or [])


def ensure_pki(st, sec, runner, cfg, rotate=False):
    """The IKEv2 CA and the server certificate for the current gateway.host (again when it changes: clients
    compare the address with the certificate). A new CA is constrained to the host (nameConstraints). A CA of
    an earlier build (no constraints) is kept — rotation is the owner's choice (``rotate``: ``gw pki rotate``);
    a constrained CA that does not cover a new host is replaced. While gateway.host is unknown (the install
    wizard puts the inbounds in before ``gw enable`` finds the address) nothing is made yet: the next prepare
    with the address makes the CA and the certificate (render refuses meanwhile). -> created (no secrets)."""
    host = _host(st)
    ca = tunnel.sget(sec, "ikev2.ca.crt")
    scope = ca_scope(ca)
    have_ca = bool(tunnel.sget(sec, "ikev2.ca.key") and ca)
    moved = have_ca and scope is not None and bool(host) and not _covers(scope, host)
    need_ca = rotate or not have_ca or moved
    need_srv = need_ca or not tunnel.sget(sec, "ikev2.server.key") or not tunnel.sget(sec, "ikev2.server.crt") \
        or str(cfg.get("cert_host") or "") != host
    if not need_srv:
        return []
    if not host:
        if rotate:
            raise InboundError(tr("gw_in_ikev2.err.no_host"))
        return []                          # made when the address is known: gw enable finds it, gw host sets it
    if runner is None:
        raise InboundError(tr("gw_in_ipsec.err.certs_on_apply"))
    tunnel.ensure_dir(own_dir())
    done = []
    if need_ca:
        if have_ca:
            # the replaced CA: the next Windows script takes it out of LocalMachine\\Root (Apple: the new
            # profile has the same PayloadUUID and replaces the old root itself)
            try:
                retired = [t for t in cfg.get("retired_ca") or [] if isinstance(t, str)] + [pki.thumbprint(ca)]
                cfg["retired_ca"] = retired[-RETIRED_KEEP:]
            except (ValueError, TypeError):
                pass
        key, crt = pki.new_ca(runner, own_dir(), new_ca_cn(host), key="rsa", scope=host, eku="ike")
        tunnel.sput(sec, "ikev2.ca.key", key)
        tunnel.sput(sec, "ikev2.ca.crt", crt)
        if moved and not rotate:
            done.append(trs("gw_in_ipsec.made.ca_moved", host=host))
        else:
            done.append(trs("gw_in_ipsec.made.ca", host=host))
    key, crt = pki.issue(runner, own_dir(), tunnel.sget(sec, "ikev2.ca.key"), tunnel.sget(sec, "ikev2.ca.crt"),
                         host, "ike", san=host, key="rsa")
    tunnel.sput(sec, "ikev2.server.key", key)
    tunnel.sput(sec, "ikev2.server.crt", crt)
    cfg["cert_host"] = host
    done.append(trs("gw_in_ipsec.made.server_cert", host=host))
    return done


def rotate(st, sec, runner):
    """``gw pki rotate ikev2``: a new constrained CA and server certificate now. -> created."""
    cfg = tunnel.require_cfg(st, "ikev2", "IKEv2")
    if runner is None:
        raise InboundError(tr("gw_in_ipsec.err.certs_need_system"))
    tunnel.log_tr("ikev2", "gw_in_ipsec.log.rotate")
    return ensure_pki(st, sec, runner, cfg, rotate=True)


def prepare(st, sec, runner=None):
    """The PSK of L2TP/IPsec; for IKEv2 the subnet, the PKI and the passwords of its devices. -> created."""
    done = []
    if tunnel.inbound_cfg(st, "l2tp") is not None and accel.l2tp_modes(st)[0] and not tunnel.sget(sec, PSK):
        tunnel.sput(sec, PSK, secrets.new_psk())
        done.append(trs("gw_in_ipsec.made.psk"))
    cfg = tunnel.inbound_cfg(st, "ikev2")
    if cfg is not None:
        if not cfg.get("subnet"):
            cfg["subnet"] = tunnel.subnet(cfg, "ikev2")
        for d in tunnel.devices(st, "ikev2"):
            if tunnel.login(d) and tunnel.ensure_password(sec, d)[1]:
                done.append(trs("gw_in.made.password", device=d["id"]))
        done += ensure_pki(st, sec, runner, cfg)
    return done


# ── render ─────────────────────────────────────────────────────────────────

def _hex(value):
    """A secret of swanctl.conf as hex: no quoting rules to get wrong."""
    return "0x" + value.encode("utf-8").hex()


def _list(values):
    return ",".join(values)


def l2tp_conn():
    return [
        "    %s {" % CONN_L2TP,
        "        version = 1",
        "        proposals = %s" % _list(L2TP_PROPOSALS),
        "        rekey_time = 0s",
        "        dpd_delay = 30s",
        "        local {",
        "            auth = psk",
        "        }",
        "        remote {",
        "            auth = psk",
        "        }",
        "        children {",
        "            %s {" % CONN_L2TP,
        "                mode = transport",
        "                local_ts = dynamic[udp/1701]",
        "                remote_ts = dynamic[udp]",            # any source port (iOS, Android); [udp/%any] is refused by strongSwan 6
        "                esp_proposals = %s" % _list(L2TP_ESP),
        "                rekey_time = 0s",
        "                dpd_action = clear",
        "            }",
        "        }",
        "    }",
    ]


def ikev2_conn(st):
    return [
        "    %s {" % CONN_IKEV2,
        "        version = 2",
        "        proposals = %s" % _list(IKEV2_PROPOSALS),
        "        rekey_time = 0s",
        "        dpd_delay = 30s",
        "        pools = %s" % POOL,
        "        send_cert = always",
        "        send_certreq = no",
        "        fragmentation = yes",
        "        unique = replace",
        "        local {",
        "            auth = pubkey",
        "            certs = %s" % os.path.basename(CERT_FILE),
        "            id = %s" % _host(st),
        "        }",
        "        remote {",
        "            auth = eap-mschapv2",
        "            eap_id = %any",
        "        }",
        "        children {",
        "            %s {" % CONN_IKEV2,
        "                local_ts = 0.0.0.0/0",
        "                esp_proposals = %s" % _list(IKEV2_ESP),
        "                rekey_time = 0s",
        "                dpd_action = clear",
        "                updown = %s" % opath("updown.sh"),
        "            }",
        "        }",
        "    }",
    ]


def conf_text(st, sec):
    l2, ik = l2tp_on(st), ikev2_on(st)
    lines = ["# Kaskad 3 gateway: strongSwan of L2TP/IPsec and IKEv2 (swanctl). Written by kaskadctl, edits are lost.",
             "connections {"]
    if l2:
        lines += l2tp_conn()
    if ik:
        lines += ikev2_conn(st)
    lines += ["}", "", "pools {"]
    if ik:
        lines += ["    %s {" % POOL,
                  "        addrs = %s" % pool_range(st),
                  "        dns = %s" % tunnel.dns(st),
                  "    }"]
    lines += ["}", "", "secrets {"]
    if l2:
        lines += ["    ike-%s {" % CONN_L2TP,
                  "        secret = %s" % _hex(tunnel.sget(sec, PSK)),
                  "    }"]
    if ik:
        for d, lg, pw in eap_users(st, sec):
            lines += ["    # device %s" % d["id"],
                      "    eap-%s {" % d["id"],
                      "        id = %s" % lg,
                      "        secret = %s" % _hex(pw),
                      "    }"]
    lines += ["}", ""]
    return "\n".join(lines)


def updown_text():
    return "\n".join([
        "#!/bin/sh",
        "# Kaskad 3 gateway: strongSwan updown of the IKEv2 inbound -> kaskadctl gw hook ikev2 up|down <identity> <ip>.",
        "# up waits (10 s at most): the client's address must be in gw_route before its traffic flows; down goes",
        "# to the background. Never fails: strongSwan must not care.",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "export PATH",
        '[ "$PLUTO_CONNECTION" = "%s" ] || exit 0' % CONN_IKEV2,
        'id="${PLUTO_XAUTH_ID:-$PLUTO_PEER_ID}"',
        'ip="${PLUTO_PEER_SOURCEIP4_1:-$PLUTO_PEER_SOURCEIP}"',
        '[ -n "$ip" ] || exit 0',
        'case "$PLUTO_VERB" in',
        '    up-*) timeout 10 %s gw hook ikev2 up "$id" "$ip" </dev/null >/dev/null 2>&1 ;;' % paths.kaskadctl(),
        '    down-*) %s gw hook ikev2 down "$id" "$ip" </dev/null >/dev/null 2>&1 & ;;' % paths.kaskadctl(),
        "esac",
        "exit 0",
        "",
    ])


def render(st, sec):
    if l2tp_on(st) and not tunnel.sget(sec, PSK):
        raise InboundError(tr("gw_in_ipsec.err.no_psk"))
    files = {
        conf_file(): (conf_text(st, sec), 0o600),
        plugins_dropin(): (plugins_text(), 0o644),
        opath("updown.sh"): (updown_text(), 0o755),
        opath("ipsec.json"): (json.dumps({"kinds": kinds_wanted(st)}, sort_keys=True) + "\n", 0o600),
        opath("users.json"): (json.dumps(dict((lg, d["id"]) for d, lg, _ in eap_users(st, sec)) if ikev2_on(st)
                                         else {}, indent=2, sort_keys=True) + "\n", 0o600),
    }
    if ikev2_on(st):
        need = ("ikev2.ca.crt", "ikev2.server.crt", "ikev2.server.key")
        if any(not tunnel.sget(sec, k) for k in need):
            raise InboundError(tr("gw_in_ikev2.err.no_host") if not _host(st) else tr("gw_in_ipsec.err.no_certs"))
        ca, crt, key = managed_files()
        files[ca] = (tunnel.sget(sec, "ikev2.ca.crt"), 0o644)
        files[crt] = (tunnel.sget(sec, "ikev2.server.crt"), 0o644)
        files[key] = (tunnel.sget(sec, "ikev2.server.key"), 0o600)
    return files


def unit_texts(runner):
    swanctl = swanctl_bin(runner)
    text = "\n".join([
        "[Unit]",
        "Description=Kaskad 3 gateway: strongSwan (L2TP/IPsec, IKEv2)",
        "After=network-online.target kaskad-net.service",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=notify",
        "ExecStart=%s" % tunnel.which(runner, "charon-systemd", "/usr/sbin/charon-systemd"),
        # «-»: one connection that does not load must not take the daemon (and the other inbound) down;
        # apply() runs --load-all itself and reports what failed
        "ExecStartPost=-%s --load-all --noprompt" % swanctl,
        "ExecReload=%s --load-all --noprompt" % swanctl,
        "Restart=on-failure",
        "RestartSec=5s",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    return {UNIT: text}


# ── install ────────────────────────────────────────────────────────────────

def kinds_file():
    return opath("kinds.json")


def installed_kinds():
    doc = tunnel.read_json(kinds_file(), [])
    return [k for k in KINDS if isinstance(doc, list) and k in doc]


def _mark(kind, on):
    have = installed_kinds()
    want = sorted(set(have + [kind])) if on else [k for k in have if k != kind]
    if want != have:
        tunnel.ensure_dir(own_dir())
        util.atomic_write(kinds_file(), json.dumps(sorted(want)) + "\n", 0o600)
    return want


def _ours(pkg):
    rec = tunnel.read_json(paths.P(tunnel.PACKAGES_FILE), {})
    return isinstance(rec, dict) and isinstance(rec.get(pkg), dict) and bool(rec[pkg].get("by_kaskad"))


def foreign_units(runner):
    """IKE daemons of the system that hold UDP 500/4500 now or will at the next boot (strongswan.service
    counts only if Kaskad did not install it: then install masks it)."""
    out = []
    for u in FOREIGN:
        if u == "strongswan.service" and _ours("charon-systemd"):
            continue
        if tunnel.is_active(runner, u) or tunnel.is_enabled(runner, u):
            out.append(u)
    return out


def missing_modules(runner):
    return [m for m in MODULES if not tunnel.module_available(runner, m)]


def install(runner, kind):
    """strongSwan for L2TP/IPsec or IKEv2: the kernel's IPsec, no other IKE daemon, packages, our unit."""
    missing = missing_modules(runner)
    if missing:
        raise InboundError(tr("gw_in_ipsec.err.no_ipsec", modules=", ".join(missing)))
    busy = foreign_units(runner)
    if busy:
        raise InboundError(tr("gw_in_ipsec.err.busy", units=", ".join(busy), args=" ".join(busy)))
    tunnel.install_packages(runner, PACKAGES, DISTRO_UNITS)
    tunnel.modprobe(runner, MODULES)
    tunnel.ensure_dir(own_dir())
    tunnel.install_units(runner, unit_texts(runner))
    _mark(kind, True)
    tunnel.log_tr(kind, "gw_in_ipsec.log.installed", unit=UNIT)


def uninstall(runner, kind):
    """Forget one IPsec inbound; with the last one the unit, our swanctl files, the UFW rules of IPsec and
    the masks of the packaged unit go (packages stay). -> True if the engine was removed."""
    if _mark(kind, False):
        return False
    tunnel.remove_units(runner, [UNIT])
    tunnel.close_ufw(runner, own_dir(), UFW_KIND)
    for p in [conf_file(), plugins_dropin()] + managed_files():
        try:
            os.unlink(p)
        except OSError:
            pass
    tunnel.remove_tree(own_dir())
    tunnel.unmask_packages(runner, list(DISTRO_UNITS))
    tunnel.log_tr("ipsec", "gw_in_ipsec.log.removed")
    return True


# ── apply ──────────────────────────────────────────────────────────────────

def swanctl(runner, *args):
    return runner.run([swanctl_bin(runner)] + list(args), timeout=60)


def parse_sas(text):
    """``swanctl --list-sas --pretty`` -> [{"name": <connection>, <key>: <value>, <list>: [...],
    "children": [{...}]}] — one dict per IKE_SA."""
    out = []
    stack = []               # [name, dict] of the open sections
    cur_list = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if cur_list is not None:
            if line == "]":
                cur_list = None
            else:
                cur_list.append(line)
            continue
        if line.endswith(" {"):
            node = {"name": line[:-2].strip(), "children": []}
            depth = len(stack)
            if depth == 1:
                out.append(node)                       # an IKE_SA of a "list-sa event"
            elif depth == 3 and stack[-1][0] == "child-sas":
                stack[-2][1]["children"].append(node)  # a CHILD_SA
            stack.append([node["name"], node])
            continue
        if line == "}":
            if stack:
                stack.pop()
            continue
        if line.endswith(" = ["):
            cur_list = []
            if stack:
                stack[-1][1][line[:-4].strip()] = cur_list
            continue
        key, sep, value = line.partition(" = ")
        if sep and stack:
            stack[-1][1][key.strip()] = value.strip()
    return out


def list_sas(runner):
    res = swanctl(runner, "--list-sas", "--pretty")
    return parse_sas(res.out) if res.ok else []


def terminate(runner, sa):
    return swanctl(runner, "--terminate", "--ike-id", str(sa.get("uniqueid")), "--force").ok


def eap_secrets(text):
    """{login: secret as written} of the EAP secrets of a kaskad.conf (compared only, never shown)."""
    out = {}
    login = None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("id = "):
            login = s[5:].strip()
        elif s.startswith("secret = ") and login is not None:
            out[login] = s[9:].strip()
            login = None
        elif s.endswith("{") or s == "}":
            login = None
    return out


def cut_sas(runner, kinds, logins, rekeyed=()):
    """Terminate IKE_SAs of a connection switched off and IKEv2 SAs of logins that lost the inbound or got
    another password (``rekeyed``)."""
    out = []
    for sa in list_sas(runner):
        name = sa.get("name")
        eap = sa.get("remote-eap-id")
        if name == CONN_L2TP and "l2tp" not in kinds:
            pass
        elif name == CONN_IKEV2 and ("ikev2" not in kinds or eap not in logins or eap in rekeyed):
            pass
        else:
            continue
        if sa.get("uniqueid") and terminate(runner, sa):
            out.append("%s %s" % (name, sa.get("remote-eap-id") or sa.get("remote-host") or ""))
    return out


def apply(st, sec, runner):
    """Configs from the state, the unit while an IPsec inbound is enabled, ``swanctl --load-all`` for
    changes, SAs that lost their right terminated, UFW for IPsec. -> {"action", "changed", "prepared", "cut"}."""
    prepared = prepare(st, sec, runner)
    tunnel.ensure_dir(own_dir())
    old_info = tunnel.read_json(opath("ipsec.json"), {})
    old_users = tunnel.read_json(opath("users.json"), {})
    old_secrets = eap_secrets(util.read_text(conf_file()))
    files = render(st, sec)
    changed = tunnel.write_files(files)
    for p in managed_files():
        if p not in files and os.path.exists(p):
            os.unlink(p)
            changed.append(p)
    kinds = kinds_wanted(st)
    cut = []
    # a unit written by an older version is rewritten; the running daemon is not restarted (that would
    # drop every SA) — the new text applies at its next start
    changed += [tunnel.unit_file(u) for u in tunnel.refresh_units(runner, unit_texts(runner))]
    plugins_changed = plugins_dropin() in changed
    if not kinds:
        action = "stopped" if tunnel.stop_unit(runner, UNIT) else "off"
        tunnel.close_ufw(runner, own_dir(), UFW_KIND)
    else:
        if tunnel.run_unit(runner, UNIT, "strongSwan"):
            action = "started"
        elif plugins_changed and runner.run(["systemctl", "restart", UNIT]).ok:
            # плагины charon грузятся только при старте: один перезапуск, сессии IPsec переподключатся сами
            action = "restarted"
        else:
            if any(p == conf_file() or p in managed_files() for p in changed):
                res = swanctl(runner, "--load-all", "--noprompt")
                if not res.ok:
                    raise InboundError(tr("gw_in_ipsec.err.load", conf=CONF, error=tunnel.cmd_error(res)))
                action = "loaded"
            else:
                action = "unchanged"
            new_users = json.loads(files[opath("users.json")][0])
            old_kinds = (old_info.get("kinds") or []) if isinstance(old_info, dict) else []
            gone = [k for k in old_kinds if k not in kinds]
            lost = [lg for lg in (old_users if isinstance(old_users, dict) else {}) if lg not in new_users]
            new_secrets = eap_secrets(files[conf_file()][0])
            rekeyed = set(lg for lg, v in old_secrets.items() if lg in new_secrets and new_secrets[lg] != v)
            if gone or lost or rekeyed:
                cut = cut_sas(runner, kinds, new_users, rekeyed)
        tunnel.sync_ufw(runner, own_dir(), UFW_KIND, [(500, "udp"), (4500, "udp"), (None, "esp")])
    names = [os.path.basename(p) for p in changed]
    if names or prepared or action not in ("unchanged", "off") or cut:
        with util.journal_lang():
            tunnel.log("ipsec", tr("gw_in_ipsec.log.applied", action=action, kinds=", ".join(kinds) or tr("gw_in.log.none"),
                                   files=", ".join(names) or tr("gw_in.log.no_changes"),
                                   cut=tr("gw_in.log.cut", items=", ".join(cut)) if cut else "",
                                   created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
    return {"action": action, "changed": names, "prepared": prepared, "cut": cut}


# ── status ─────────────────────────────────────────────────────────────────

def _num(value):
    v = str(value or "").strip()
    return int(v) if v.isdigit() else 0


def ikev2_sessions(runner):
    owners = tunnel.read_json(opath("users.json"), {})
    if not isinstance(owners, dict):
        owners = {}
    now = int(tunnel.now())
    out = []
    for sa in list_sas(runner):
        if sa.get("name") != CONN_IKEV2 or sa.get("state") != "ESTABLISHED":
            continue
        ident = sa.get("remote-eap-id") or sa.get("remote-id")
        vips = sa.get("remote-vips") or []
        est = _num(sa.get("established"))
        out.append({"device": owners.get(ident), "login": ident, "ip": vips[0] if vips else None,
                    "since": (now - est) if est else None,
                    "rx": sum(_num(c.get("bytes-in")) for c in sa.get("children") or []),
                    "tx": sum(_num(c.get("bytes-out")) for c in sa.get("children") or [])})
    return sorted(out, key=lambda s: str(s["login"]))


def status(runner, kind):
    installed = tunnel.unit_installed(UNIT) and kind in installed_kinds()
    doc = tunnel.read_json(opath("ipsec.json"), {})
    kinds = (doc.get("kinds") or []) if isinstance(doc, dict) else []
    running = installed and tunnel.is_active(runner, UNIT) and kind in kinds
    sessions = ikev2_sessions(runner) if running and kind == "ikev2" else []
    out = {"kind": kind, "installed": installed, "running": running, "sessions": sessions, "units": [UNIT]}
    if kind == "ikev2" and installed:
        ca = util.read_text(paths.P(CA_FILE))
        if ca and "-----BEGIN CERTIFICATE-----" in ca and ca_scope(ca) is None:
            out["warnings"] = [tr("gw_in_ipsec.legacy_ca")]
            out["ca_legacy"] = True
    return out
