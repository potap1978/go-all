"""OpenVPN inbound (``openvpn``): OpenVPN 2.6 of the distribution, own CA, UDP and an optional TCP instance.

    UDP  kaskad-gw-ovpn-udp.service  dev kgw-ovpn-u  10.77.9.0/24   inbounds.openvpn.udp_port (1194; 0 = off)
    TCP  kaskad-gw-ovpn-tcp.service  dev kgw-ovpn-t  10.77.10.0/24  inbounds.openvpn.tcp_port (0 = off)

PKI with openssl (pki.py, EC P-256, no easy-rsa): CA ``ovpn.ca.key`` + ``ovpn.ca.crt``; server
``ovpn.server.key`` + ``.crt`` (CN kaskad-gw-openvpn, serverAuth); per device ``dev.<id>.ovpn.key`` + ``.crt``
(CN = device id, clientAuth); ``tls-crypt`` key from ``openvpn --genkey secret`` (``ovpn.tls_crypt``). The
CA key never leaves secrets.json.

Files in /etc/kaskad/gw/openvpn/ (0700 root, files 0600): udp.conf, tcp.conf, ca.crt, server.crt, server.key,
tc.key — read at start, as root. What the server reads AFTER it dropped to ``user nobody`` / ``group
nogroup`` lives in /etc/openvpn/kaskad/ (0755, files 0644 — nothing secret; /etc/kaskad is root-only):

    ccd-udp/<id>, ccd-tcp/<id>   ``ifconfig-push <ip> <mask>`` (a fixed address per device), read at every
                                 connect; ``ccd-exclusive``: a certificate without its ccd file (device removed,
                                 disabled, access revoked) is refused
    crl.pem                      the CRL of the CA (``crl-verify``): OpenVPN re-reads it at the next TLS
                                 handshake when it changed (2.4+) — no restart

The CRL (``openssl ca -gencrl``, pki.crl, valid as long as the CA) lists every certificate of a device that is
not its certificate any more: the device was deleted, lost OpenVPN or got a new one (``gw device reset``). The
book ``ovpn.crl.book`` (secrets: CA fingerprint, device -> serial of its certificate, revoked serials) notices
that at the next prepare; a new CA starts a new book. Without the CRL a reset device's old .ovpn would still
connect: its ccd file (the device id) is there. ``persist-key``/``persist-tun``: a soft restart (SIGUSR1)
neither re-reads the keys nor reopens the tun device; the status file and the management socket are opened
before the privileges go.

Server: ``server`` + ``topology subnet``, ``data-ciphers AES-256-GCM:CHACHA20-POLY1305``, ``tls-crypt``,
``dh none``; pushes ``redirect-gateway def1 ipv6`` (IPv6 into the tunnel) and the gateway DNS. ``block-ipv6`` is
in the client's .ovpn (it answers the IPv6 that came into the tunnel with «no route»): OpenVPN 2.6 clients
refuse it pushed («option 'block-ipv6' cannot be used in this context ([PUSH-OPTIONS])», stand 2026-10-04).
Status file and management socket in /run/kaskad-gw-ovpn-<udp|tcp>/.

apply(): devices added or removed → only ccd files change (read at connect time, no restart); a device
that lost its ccd file, got a new address or a new certificate while connected is cut through the management
socket (``kill <id>``) — the others stay; server config or certificates changed → restart of that instance
(clients reconnect by themselves); a new CRL — nothing (re-read at the next handshake).

Issuance: one .ovpn with inline ca/cert/key/tls-crypt; with TCP on — a second ``remote`` over TCP.
"""

import ipaddress
import json
import os
import shutil
import socket
import tempfile

from kaskad import paths, util
from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import pki, tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import retr, tr, trs

SERVER_CN = "kaskad-gw-openvpn"
CIPHERS = "AES-256-GCM:CHACHA20-POLY1305"
PUB_DIR = "/etc/openvpn/kaskad"         # what the server reads as nobody: ccd-udp/, ccd-tcp/, crl.pem
RUN_USER, RUN_GROUP = "nobody", "nogroup"
MH_MARK = b"MH/PKTINFO"                 # in the title of a build that answers from the address it was asked on
__getattr__ = lazy_texts(__name__, {"MH_WARNING": "gw_in_openvpn.mh_warning"})     # openvpn.MH_WARNING: of the moment
HINT_PLATFORMS = ("ios", "android", "windows", "macos", "linux", "asus", "openwrt", "mikrotik")  # gw_in_openvpn.hint.*
_mh_cache = {}


def multihome_supported(path):
    """The binary was built with IP_PKTINFO (``openvpn --version`` names it «[MH/PKTINFO]»): True | False, None — no
    binary. Read from the file itself (no command; the collector asks every round), cached by mtime and size."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _mh_cache:
        found = False
        try:
            with open(path, "rb") as f:
                tail = b""
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    if MH_MARK in tail + chunk:
                        found = True
                        break
                    tail = chunk[-len(MH_MARK):]
        except OSError:
            return None
        _mh_cache.clear()
        _mh_cache[key] = found
    return _mh_cache[key]
CRL = "ovpn.crl"
BOOK = "ovpn.crl.book"
CRL_REFRESH = 365 * 86400               # a CRL valid for 10 years is made again once a year anyway
# instance -> (interface, address kind of device["ips"], state key of the port, proto in the configs)
INSTANCES = (
    ("udp", "kgw-ovpn-u", "ovpn_u", "udp_port", "udp"),
    ("tcp", "kgw-ovpn-t", "ovpn_t", "tcp_port", "tcp-server"),
)
_INST = dict((i[0], i) for i in INSTANCES)


def management(sock_path, commands, timeout=3.0):
    """Send commands to an OpenVPN management socket (unix). -> reply line per command."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    buf = [b""]

    def readline():
        while b"\n" not in buf[0]:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf[0] += chunk
        line, _, rest = buf[0].partition(b"\n")
        buf[0] = rest
        return line.decode("utf-8", "replace").strip()

    try:
        s.connect(sock_path)
        readline()                                   # >INFO:OpenVPN Management Interface ...
        out = []
        for cmd in commands:
            s.sendall((cmd + "\n").encode("utf-8"))
            line = readline()
            while line.startswith(">"):              # real-time notifications
                line = readline()
            out.append(line)
        s.sendall(b"quit\n")
        return out
    finally:
        s.close()


def parse_status(text):
    """status-version 2 file -> [{"device", "ip", "since", "rx", "tx"}] (columns by the HEADER line)."""
    cols = None
    out = []
    for line in (text or "").splitlines():
        f = line.split(",")
        if f[0] == "HEADER" and len(f) > 2 and f[1] == "CLIENT_LIST":
            cols = f[2:]
        elif f[0] == "CLIENT_LIST" and cols:
            row = dict(zip(cols, f[1:]))

            def num(key):
                v = (row.get(key) or "").strip()
                return int(v) if v.isdigit() else 0
            out.append({"device": row.get("Common Name"), "ip": row.get("Virtual Address") or None,
                        "since": num("Connected Since (time_t)") or None,
                        "rx": num("Bytes Received"), "tx": num("Bytes Sent")})
    return out


class OpenVpnInbound(Inbound):
    kind = "openvpn"
    engine = "openvpn"
    title = "OpenVPN"
    management = staticmethod(management)

    # ── names ──
    def dir(self):
        return tunnel.kind_dir("openvpn")

    def path(self, name):
        return os.path.join(self.dir(), name)

    def unit(self, inst):
        return "kaskad-gw-ovpn-%s.service" % inst

    def units(self):
        return [self.unit(i[0]) for i in INSTANCES]

    def run_dir(self, inst):
        return paths.P("/run/kaskad-gw-ovpn-%s" % inst)

    def conf_path(self, inst):
        return self.path("%s.conf" % inst)

    def pub_dir(self):
        return paths.P(PUB_DIR)

    def ccd_dir(self, inst):
        return os.path.join(self.pub_dir(), "ccd-%s" % inst)

    def old_ccd_dir(self, inst):
        """Where an earlier build kept the ccd files (root-only: unreadable for the server as nobody)."""
        return self.path("ccd-%s" % inst)

    def crl_path(self):
        return os.path.join(self.pub_dir(), "crl.pem")

    def default_port(self):
        return 1194

    def ifaces(self):
        return [i[1] for i in INSTANCES]

    # ── settings ──
    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st, inst):
        cfg = self.cfg(st)
        key = _INST[inst][3]
        if inst == "udp" and key not in cfg:
            return self.default_port()
        return tunnel.port_value(cfg, key, 0)

    def instances(self, st):
        return [i[0] for i in INSTANCES if self.port(st, i[0])]

    def addr_kinds(self, st=None):
        if st is None:
            return [i[2] for i in INSTANCES]
        return [_INST[i][2] for i in self.instances(st)]

    def network(self, inst):
        return ipaddress.ip_network(tunnel.subnet(None, _INST[inst][2]), strict=False)

    def openvpn_bin(self, runner):
        return tunnel.which(runner, "openvpn", "/usr/sbin/openvpn")

    def secret_prefixes(self, device_id):
        """The device's certificate and key live under ``dev.<id>.ovpn.`` (not ``.openvpn.``)."""
        return ["dev.%s.ovpn." % device_id]

    def devices(self, st):
        """Devices of the inbound whose id can be a certificate CN and a ccd file name as it is."""
        out, bad = [], []
        for d in tunnel.devices(st, self.kind):
            (out if tunnel.safe_id(d) else bad).append(d)
        if bad:
            tunnel.log_tr(self.kind, "gw_in_openvpn.log.skipped_id", devices=", ".join(str(d["id"]) for d in bad))
        return out

    # ── keys and certificates ──
    def prepare(self, st, sec, runner=None):
        """CA, server certificate, tls-crypt key; addresses and certificates of the devices. -> created."""
        cfg = self.cfg(st)
        done = []
        if "udp_port" not in cfg:
            cfg["udp_port"] = self.default_port()
        if "tcp_port" not in cfg:
            cfg["tcp_port"] = 0
        devices = self.devices(st)
        for d in devices:
            for ak in self.addr_kinds(st):
                _, new = tunnel.ensure_ip(st, d, ak)
                if new:
                    done.append(trs("gw_in_openvpn.made.addr", pool=ak, device=d["id"]))

        def need(*keys):
            return any(not tunnel.sget(sec, k) for k in keys)

        if runner is None and (need("ovpn.ca.key", "ovpn.ca.crt", "ovpn.server.key", "ovpn.server.crt",
                                    "ovpn.tls_crypt")
                               or any(need("dev.%s.ovpn.key" % d["id"], "dev.%s.ovpn.crt" % d["id"]) for d in devices)):
            raise InboundError(tr("gw_in_openvpn.err.certs_on_apply"))
        tunnel.ensure_dir(self.dir())
        new_ca = False
        if runner is None and self.crl_needed(sec, devices):
            raise InboundError(tr("gw_in_openvpn.err.crl_on_apply"))
        if need("ovpn.ca.key", "ovpn.ca.crt"):
            key, crt = pki.new_ca(runner, self.dir(), "Kaskad OpenVPN CA")
            tunnel.sput(sec, "ovpn.ca.key", key)
            tunnel.sput(sec, "ovpn.ca.crt", crt)
            new_ca = True
            done.append(trs("gw_in_openvpn.made.ca"))
        ca_key, ca_crt = tunnel.sget(sec, "ovpn.ca.key"), tunnel.sget(sec, "ovpn.ca.crt")
        if new_ca or need("ovpn.server.key", "ovpn.server.crt"):
            key, crt = pki.issue(runner, self.dir(), ca_key, ca_crt, SERVER_CN, "server")
            tunnel.sput(sec, "ovpn.server.key", key)
            tunnel.sput(sec, "ovpn.server.crt", crt)
            done.append(trs("gw_in.made.server_cert"))
        if need("ovpn.tls_crypt"):
            tunnel.sput(sec, "ovpn.tls_crypt", self.genkey_tls_crypt(runner))
            done.append(trs("gw_in_openvpn.made.tls_crypt"))
        for d in devices:
            if new_ca or need("dev.%s.ovpn.key" % d["id"], "dev.%s.ovpn.crt" % d["id"]):
                key, crt = pki.issue(runner, self.dir(), ca_key, ca_crt, d["id"], "client")
                tunnel.sput(sec, "dev.%s.ovpn.key" % d["id"], key)
                tunnel.sput(sec, "dev.%s.ovpn.crt" % d["id"], crt)
                done.append(trs("gw_in_openvpn.made.cert", device=d["id"]))
        done += self.ensure_crl(sec, runner, devices, fresh=new_ca)
        return done

    # ── the CRL ──
    @staticmethod
    def book(sec):
        """{"ca": CA fingerprint, "issued": {device id: serial}, "revoked": {serial: {"device", "at"}},
        "made": epoch of the CRL} from the secrets (a JSON string), {} if none or broken."""
        try:
            doc = json.loads(tunnel.sget(sec, BOOK) or "{}")
        except ValueError:
            doc = {}
        return doc if isinstance(doc, dict) else {}

    @staticmethod
    def _serial(crt):
        try:
            return pki.serial_hex(crt) if crt else None
        except (ValueError, IndexError, TypeError):
            return None

    def _next_book(self, sec, devices, fresh=False):
        """The book after this prepare: (book, revoked_changed). ``fresh`` — the CA was just made: a new book
        (the serials of the old CA mean nothing to it)."""
        ca_crt = tunnel.sget(sec, "ovpn.ca.crt")
        fp = pki.sha256_fingerprint(ca_crt) if ca_crt else ""
        old = self.book(sec)
        book = {"ca": fp, "issued": {}, "revoked": {}, "made": old.get("made") or 0}
        if old.get("ca") == fp and not fresh:
            book["issued"] = dict((k, v) for k, v in (old.get("issued") or {}).items() if isinstance(v, str))
            book["revoked"] = dict((k, v) for k, v in (old.get("revoked") or {}).items() if isinstance(v, dict))
        before = set(book["revoked"])
        now = int(tunnel.now())
        for dev_id, serial in sorted(book["issued"].items()):
            if self._serial(tunnel.sget(sec, "dev.%s.ovpn.crt" % dev_id)) != serial:
                book["revoked"][serial] = {"device": dev_id, "at": now}      # deleted, revoked or replaced
                del book["issued"][dev_id]
        for d in devices:
            serial = self._serial(tunnel.sget(sec, "dev.%s.ovpn.crt" % d["id"]))
            if serial:
                book["issued"][d["id"]] = serial
                book["revoked"].pop(serial, None)
        return book, set(book["revoked"]) != before or old.get("ca") != fp or fresh

    def crl_needed(self, sec, devices):
        if not tunnel.sget(sec, "ovpn.ca.crt"):
            return False
        book, changed = self._next_book(sec, devices)
        return changed or not tunnel.sget(sec, CRL) or int(tunnel.now()) - int(book.get("made") or 0) > CRL_REFRESH

    def ensure_crl(self, sec, runner, devices, fresh=False):
        """The book follows the certificates; the CRL is made again when a certificate was revoked, the CA is
        new, or it is a year old. -> created."""
        book, changed = self._next_book(sec, devices, fresh)
        done = []
        now = int(tunnel.now())
        if changed or not tunnel.sget(sec, CRL) or now - int(book.get("made") or 0) > CRL_REFRESH:
            revoked = [(s, int((r or {}).get("at") or now)) for s, r in sorted(book["revoked"].items())]
            crl = pki.crl(runner, self.dir(), tunnel.sget(sec, "ovpn.ca.key"), tunnel.sget(sec, "ovpn.ca.crt"), revoked)
            tunnel.sput(sec, CRL, crl)
            book["made"] = now
            done.append(trs("gw_in_openvpn.made.crl", n=len(revoked)))
        text = json.dumps(book, sort_keys=True)
        if text != (tunnel.sget(sec, BOOK) or ""):
            tunnel.sput(sec, BOOK, text)
        return done

    def genkey_tls_crypt(self, runner):
        d = tempfile.mkdtemp(prefix=".tc-", dir=self.dir())
        try:
            os.chmod(d, 0o700)
            p = os.path.join(d, "tc.key")
            res = runner.run([self.openvpn_bin(runner), "--genkey", "secret", p])
            text = util.read_text(p) or ""
            if not res.ok or "BEGIN OpenVPN Static key V1" not in text:
                raise InboundError("openvpn --genkey secret: %s" % (tunnel.cmd_error(res) if not res.ok else tr("gw_in_openvpn.no_key")))
            return text
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ── render ──
    def server_conf(self, st, inst):
        _, dev, _, _, proto = _INST[inst]
        net = self.network(inst)
        run = self.run_dir(inst)
        lines = [
            "# Kaskad 3 gateway: OpenVPN %s inbound (%s). Written by kaskadctl, edits are lost." % (inst.upper(), dev),
            "port %d" % self.port(st, inst),
            "proto %s" % proto,
            "dev %s" % dev,
            "dev-type tun",
            "topology subnet",
            "server %s %s" % (net.network_address, net.netmask),
            "client-config-dir %s" % self.ccd_dir(inst),
            "ccd-exclusive",
            "ca %s" % self.path("ca.crt"),
            "cert %s" % self.path("server.crt"),
            "key %s" % self.path("server.key"),
            "dh none",
            "tls-crypt %s" % self.path("tc.key"),
            "crl-verify %s" % self.crl_path(),
            "tls-version-min 1.2",
            "data-ciphers %s" % CIPHERS,
            "keepalive 10 60",
            "user %s" % RUN_USER,
            "group %s" % RUN_GROUP,
            "persist-key",
            "persist-tun",
        ]
        if inst == "udp":
            # several addresses on the node (k3/SPEC-multi-ip.md §1.2): the answer goes from the address the client
            # wrote to (IP_PKTINFO), not from the one of the default route — else the client's NAT drops it
            lines += ["explicit-exit-notify 1", "multihome"]
        lines += [
            'push "redirect-gateway def1 ipv6"',
            'push "dhcp-option DNS %s"' % tunnel.dns(st),
            "status %s/status 10" % run,
            "status-version 2",
            "management %s/mgmt.sock unix" % run,
            "verb 3",
        ]
        return "\n".join(lines) + "\n"

    def render(self, st, sec):
        need = ("ovpn.ca.crt", "ovpn.server.crt", "ovpn.server.key", "ovpn.tls_crypt", CRL)
        if any(not tunnel.sget(sec, k) for k in need):
            raise InboundError(tr("gw_in_openvpn.err.no_certs"))
        files = {
            self.path("ca.crt"): (tunnel.sget(sec, "ovpn.ca.crt"), 0o600),
            self.path("server.crt"): (tunnel.sget(sec, "ovpn.server.crt"), 0o600),
            self.path("server.key"): (tunnel.sget(sec, "ovpn.server.key"), 0o600),
            self.path("tc.key"): (tunnel.sget(sec, "ovpn.tls_crypt"), 0o600),
            self.crl_path(): (tunnel.sget(sec, CRL), 0o644),            # public: read by the server as nobody
        }
        devices = [d for d in self.devices(st) if tunnel.sget(sec, "dev.%s.ovpn.crt" % d["id"])]
        for inst in self.instances(st):
            files[self.conf_path(inst)] = (self.server_conf(st, inst), 0o600)
            ak = _INST[inst][2]
            mask = self.network(inst).netmask
            for d in devices:
                ip = tunnel.device_ip(d, ak)
                if ip:
                    files[os.path.join(self.ccd_dir(inst), d["id"])] = ("ifconfig-push %s %s\n" % (ip, mask), 0o644)
        return files

    def unit_texts(self, runner):
        exe = self.openvpn_bin(runner)
        out = {}
        for inst, dev, _, _, _ in INSTANCES:
            out[self.unit(inst)] = "\n".join([
                "[Unit]",
                "Description=Kaskad 3 gateway: OpenVPN %s inbound (%s)" % (inst.upper(), dev),
                "After=network-online.target kaskad-net.service",
                "Wants=network-online.target",
                "",
                "[Service]",
                "Type=notify",
                "RuntimeDirectory=kaskad-gw-ovpn-%s" % inst,
                "RuntimeDirectoryMode=0700",
                "WorkingDirectory=%s" % self.dir(),
                "ExecStart=%s --suppress-timestamps --config %s" % (exe, self.conf_path(inst)),
                "Restart=on-failure",
                "RestartSec=5s",
                "CapabilityBoundingSet=CAP_IPC_LOCK CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW CAP_SETGID "
                "CAP_SETUID CAP_SYS_CHROOT CAP_DAC_OVERRIDE CAP_AUDIT_WRITE",
                "LimitNPROC=10",
                "DeviceAllow=/dev/null rw",
                "DeviceAllow=/dev/net/tun rw",
                "ProtectSystem=true",
                "ProtectHome=true",
                "KillMode=process",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ])
        return out

    # ── interface of §3 ──
    def requirements(self, runner):
        ok = tunnel.module_available(runner, "tun") or os.path.exists(paths.P("/dev/net/tun"))
        return {"kind": self.kind, "engine": self.engine, "packages": ["openvpn", "openssl"], "modules": ["tun"],
                "ports": [{"proto": "udp", "port": self.default_port()}], "supported": ok,
                "why": "" if ok else tr("gw_in.why.no_tun")}

    def install(self, runner):
        tunnel.install_packages(runner, ["openvpn", "openssl"], {"openvpn": ["openvpn.service"]})
        tunnel.modprobe(runner, ["tun"])
        tunnel.ensure_dir(self.dir())
        tunnel.install_units(runner, self.unit_texts(runner))
        tunnel.log_tr(self.kind, "gw_in.log.installed")

    def uninstall(self, runner):
        tunnel.remove_units(runner, self.units())
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        tunnel.remove_tree(self.pub_dir())
        tunnel.unmask_packages(runner, ["openvpn"])
        tunnel.log_tr(self.kind, "gw_in.log.removed_keys")

    def kick(self, inst, cns):
        """Cut connected clients of removed devices (best effort: the instance may be restarting)."""
        sock = os.path.join(self.run_dir(inst), "mgmt.sock")
        if not cns or not os.path.exists(sock):
            return []
        try:
            return self.management(sock, ["kill %s" % cn for cn in cns])
        except (OSError, socket.error) as e:
            tunnel.log_tr(self.kind, "gw_in_openvpn.log.kick_failed", devices=", ".join(cns), error=e)
            return []

    def public_dir(self, path):
        """A directory the server reads as nobody: 0755 (the parent /etc/openvpn too, if we make it)."""
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            self.public_dir(parent)
        util.ensure_dir(path, 0o755)

    def apply(self, st, sec, runner):
        self.cfg(st)
        issued_before = dict(self.book(sec).get("issued") or {})
        prepared = self.prepare(st, sec, runner)
        issued_after = self.book(sec).get("issued") or {}
        rekeyed = sorted(dev for dev, serial in issued_before.items()       # a new certificate (gw device reset):
                         if issued_after.get(dev) not in (None, serial))     # the old one is in the CRL, kill it
        tunnel.ensure_dir(self.dir())
        self.public_dir(self.pub_dir())
        before = {}
        for inst, _, _, _, _ in INSTANCES:
            d = self.ccd_dir(inst)
            before[inst] = set(os.listdir(d)) if os.path.isdir(d) else set()
            tunnel.remove_tree(self.old_ccd_dir(inst))  # an earlier build's, root-only: the new config moves away
        for inst in self.instances(st):
            self.public_dir(self.ccd_dir(inst))         # openvpn refuses to start without it, even empty
        files = self.render(st, sec)
        changed = tunnel.write_files(files)
        units_changed = set(tunnel.refresh_units(runner, self.unit_texts(runner)))
        core = [self.path(n) for n in ("ca.crt", "server.crt", "server.key", "tc.key")]
        on = tunnel.wanted(st, self.kind)
        actions = {}
        for inst, _, _, _, proto in INSTANCES:
            unit = self.unit(inst)
            ccd = self.ccd_dir(inst)
            keep = [p for p in files if os.path.dirname(p) == ccd]
            removed = tunnel.prune(ccd, keep)
            if inst not in self.instances(st):
                actions[inst] = "stopped" if tunnel.stop_unit(runner, unit) else "off"
                if os.path.exists(self.conf_path(inst)):
                    os.unlink(self.conf_path(inst))
                tunnel.remove_tree(ccd)
                continue
            if not on:
                actions[inst] = "stopped" if tunnel.stop_unit(runner, unit) else "off"
                continue
            if tunnel.run_unit(runner, unit, self.title):
                actions[inst] = "started"
            elif unit in units_changed or self.conf_path(inst) in changed or any(p in changed for p in core):
                tunnel.systemctl(runner, "restart", unit)
                actions[inst] = "restarted"
            else:
                moved = [os.path.basename(p) for p in changed
                         if os.path.dirname(p) == ccd and os.path.basename(p) in before[inst]]
                cut = sorted(set(removed) | set(moved) | set(rekeyed))
                self.kick(inst, cut)
                actions[inst] = ("cut %s" % ",".join(cut)) if cut else "unchanged"
        want = []
        if on:
            for inst in self.instances(st):
                want.append((self.port(st, inst), "udp" if inst == "udp" else "tcp"))
        tunnel.sync_ufw(runner, self.dir(), self.kind, want)
        names = [os.path.relpath(p, self.pub_dir() if p.startswith(self.pub_dir() + os.sep) else self.dir())
                 for p in changed]
        if names or prepared or any(a not in ("unchanged", "off") for a in actions.values()):
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_openvpn.log.applied",
                                         actions=", ".join("%s %s" % (k, v) for k, v in sorted(actions.items())),
                                         files=", ".join(names) or tr("gw_in.log.no_changes"),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
        return {"action": actions, "changed": names, "prepared": prepared}

    def status(self, runner):
        inst_status = {}
        sessions = []
        for inst, _, _, _, _ in INSTANCES:
            unit = self.unit(inst)
            installed = tunnel.unit_installed(unit)
            running = installed and tunnel.is_active(runner, unit)
            port = None
            for line in (util.read_text(self.conf_path(inst)) or "").splitlines():
                f = line.split()
                if len(f) == 2 and f[0] == "port" and f[1].isdigit():
                    port = int(f[1])
            inst_status[inst] = {"running": running, "port": port, "unit": unit}
            if running:
                for s in parse_status(util.read_text(os.path.join(self.run_dir(inst), "status"))):
                    s["transport"] = inst
                    sessions.append(s)
        installed = any(tunnel.unit_installed(u) for u in self.units())
        main = inst_status["udp"] if inst_status["udp"]["port"] else inst_status["tcp"]
        out = {"kind": self.kind, "installed": installed,
               "running": any(v["running"] for v in inst_status.values()), "port": main["port"],
               "instances": inst_status, "sessions": sessions, "units": self.units()}
        if inst_status["udp"]["running"] and multihome_supported(paths.P("/usr/sbin/openvpn")) is False:
            out["warnings"] = [tr("gw_in_openvpn.mh_warning")]
        return out

    # ── issuance ──
    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        if dev.get("id") not in [d["id"] for d in self.devices(st)]:
            raise InboundError(tr("gw_in.err.not_user", device=tunnel.device_name(dev), title="OpenVPN"))
        blocks = {"ca": tunnel.sget(sec, "ovpn.ca.crt"), "cert": tunnel.sget(sec, "dev.%s.ovpn.crt" % dev["id"]),
                  "key": tunnel.sget(sec, "dev.%s.ovpn.key" % dev["id"]), "tls-crypt": tunnel.sget(sec, "ovpn.tls_crypt")}
        if not all(blocks.values()):
            raise InboundError(tr("gw_in_openvpn.err.no_device_cert", device=tunnel.device_name(dev)))
        host = tunnel.host(st)
        remotes = ["remote %s %d %s" % (host, self.port(st, inst), "udp" if inst == "udp" else "tcp")
                   for inst in self.instances(st)]
        if not remotes:
            raise InboundError(tr("gw_in_openvpn.err.no_ports"))
        lines = ["# Kaskad · %s · OpenVPN" % tunnel.device_name(dev).replace("\n", " "),
                 "client", "dev tun", "nobind", "persist-key", "persist-tun", "resolv-retry infinite"]
        lines += remotes
        if len(remotes) > 1:
            lines.append("server-poll-timeout 10")
        lines += ["remote-cert-tls server", "verify-x509-name %s name" % SERVER_CN,
                  "ignore-unknown-option data-ciphers", "data-ciphers %s" % CIPHERS,
                  "tls-version-min 1.2",
                  # IPv6 the server pushes into the tunnel (redirect-gateway ipv6) is answered here with «no
                  # route»: no leak past the tunnel. OpenVPN 2.6 refuses it pushed — so it is the client's own
                  "ignore-unknown-option block-ipv6", "block-ipv6", "verb 3"]
        if p == "windows":
            lines.append("block-outside-dns")
        for tag in ("ca", "cert", "key", "tls-crypt"):
            lines += ["<%s>" % tag, blocks[tag].strip(), "</%s>" % tag]
        hint = tr("gw_in_openvpn.hint." + p) if p in HINT_PLATFORMS else None
        notes = [hint] if hint else []
        if len(remotes) > 1:
            notes.append(tr("gw_in_openvpn.udp_then_tcp"))
        rec = tunnel.record("ovpn", tr("gw_in_openvpn.rec.ovpn"), "\n".join(lines) + "\n", "kaskad-openvpn.ovpn", notes)
        return tunnel.result(self.kind, [rec])


registry.register(OpenVpnInbound)
