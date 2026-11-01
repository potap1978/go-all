"""OpenConnect inbound (``ocserv``): ocserv of the distribution, login and password of the device.

Port ``inbounds.ocserv.port`` (4443 by default; 443 only in the domain mode of wave 4) on TCP and UDP
(DTLS), subnet 10.77.11.0/24, tun devices ``kgw-oc0``, ``kgw-oc1``… (prefix ``kgw-oc``, not the package
default ``vpns``, so a server's own ocserv keeps its interfaces apart), service kaskad-gw-ocserv.service
with ``ocserv -c /etc/kaskad/gw/ocserv/ocserv.conf``.

Files in /etc/kaskad/gw/ocserv/ (0600, hook.sh 0755):
    ocserv.conf             auth = "plain[passwd=…/ocpasswd]", route = default, dns = gateway DNS,
                            config-per-user, connect/disconnect script
    ocpasswd                ``login:*:$6$…`` — SHA-512 crypt by ``openssl passwd -6`` (Python 3.13 has no
                            crypt); the salt is an HMAC of the device and its password with a node secret,
                            so the file only changes when a password does
    config-per-user/<login> ``explicit-ipv4 = <ip>`` — a fixed address per device
    server-cert.pem, server-key.pem   self-signed EC P-256, the node address in the SAN
    hook.sh                 ocserv connect/disconnect → ``kaskadctl gw hook ocserv up|down <login> <ip>``
                            in the background, always exit 0: accounting never refuses a connection

apply(): passwords and per-user files are read by ocserv at login — no restart; removed devices, changed
addresses and changed passwords (``gw device reset``) are cut with ``occtl disconnect user``; ocserv.conf
changed → reload (SIGHUP, sessions stay); port, certificate or TLS versions (3.3.7.15 F4) changed → restart. After ``kaskadctl site use ocserv
on`` (docs/k3/SPEC-wave4.md §4.1) ``server-cert``/``server-key`` name the copy of the domain's certificate of Let's
Encrypt (/etc/kaskad/site/tls/current): switching is a restart (the paths are not reloadable), a renewal — a reload (ocserv
re-reads the files on SIGHUP since 0.11.0); the issuance then names the domain and carries no pin (a renewed
certificate has a new key — a pin would break every client at each renewal).

Secrets: ``ocserv.server.key`` + ``.crt``, ``ocserv.pepper``, ``dev.<id>.password`` (shared by every
password inbound), ``dev.<id>.ocserv.hash``. Issuance: server, login, password, ``pin-sha256:`` of the
certificate; OpenConnect GUI / Cisco Secure Client steps; the openconnect command for Linux.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets as _random

from kaskad import paths, util
from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import pki, tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.i18n import retr, tr, trs

UNIT = "kaskad-gw-ocserv.service"
DEVICE = "kgw-oc"
ADDR_KIND = "ocserv"
RUN = "/run/kaskad-gw-ocserv"
_CRYPT64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


TLS_PRIORITIES = "NORMAL:%SERVER_PRECEDENCE:%COMPAT:-VERS-SSL3.0:-VERS-TLS1.0:-VERS-TLS1.1"
TLS_CUT = {"1.2-1.3": "", "1.3": ":-VERS-TLS1.2", "1.2": ":-VERS-TLS1.3"}     # 3.3.7.15 F4


def tls_priorities(versions):
    """``tls-priorities`` of ocserv.conf for ``tunnel.tls_versions`` (F4): «1.2 и 1.3» — the line as before, «только
    1.3» / «только 1.2» — the other version cut. A change is a restart (``_ports``): what a SIGHUP gives the listening
    socket is not checked."""
    return TLS_PRIORITIES + TLS_CUT.get(versions, "")


def salt_for(pepper, dev_id, password):
    mac = hmac.new(pepper.encode("utf-8"), ("%s\0%s" % (dev_id, password)).encode("utf-8"), hashlib.sha256).digest()
    return "".join(_CRYPT64[b % 64] for b in bytearray(mac[:16]))


def parse_users(text):
    """``occtl --json show users`` -> [{"login", "ip", "since", "rx", "tx"}]."""
    try:
        rows = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue

        def num(*keys):
            for k in keys:
                v = r.get(k)
                if isinstance(v, int):
                    return v
                if isinstance(v, str) and v.strip().isdigit():
                    return int(v.strip())
            return 0
        out.append({"login": r.get("Username"), "ip": r.get("IPv4") or None,
                    "since": num("raw_connected_at") or None, "rx": num("RX", "raw_rx"), "tx": num("TX", "raw_tx")})
    return out


class OcservInbound(Inbound):
    kind = "ocserv"
    engine = "ocserv"
    title = "OpenConnect"

    # ── names ──
    def dir(self):
        return tunnel.kind_dir("ocserv")

    def path(self, name):
        return os.path.join(self.dir(), name)

    def users_dir(self):
        return self.path("config-per-user")

    def run_path(self, name):
        return paths.P(RUN + "/" + name)

    def default_port(self):
        return 4443

    def ifaces(self):
        return [DEVICE + "*"]

    def addr_kinds(self, st=None):
        return [ADDR_KIND]

    # ── settings ──
    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st):
        return tunnel.port_value(self.cfg(st), "port", self.default_port())

    def network(self):
        return ipaddress.ip_network(tunnel.subnet(None, ADDR_KIND), strict=False)

    def occtl(self, runner):
        return tunnel.which(runner, "occtl", "/usr/bin/occtl")

    def users(self, st):
        """Devices of the inbound with a usable login: [(device, login)]."""
        out, seen = [], set()
        for d in tunnel.devices(st, self.kind):
            lg = tunnel.login(d)
            if lg and lg not in seen:
                seen.add(lg)
                out.append((d, lg))
        return out

    # ── keys, passwords ──
    def prepare(self, st, sec, runner=None):
        cfg = self.cfg(st)
        done = []
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = self.default_port()
            done.append(trs("gw_in.made.port", port=cfg["port"]))
        users = self.users(st)
        kept = set(d["id"] for d, _ in users)
        skipped = [d["id"] for d in tunnel.devices(st, self.kind) if d["id"] not in kept]
        if skipped:
            tunnel.log_tr(self.kind, "gw_in_ocserv.log.skipped", devices=", ".join(skipped))
        for d, _ in users:
            _, new = tunnel.ensure_ip(st, d, ADDR_KIND)
            if new:
                done.append(trs("gw_in.made.addr", device=d["id"]))
        if not tunnel.sget(sec, "ocserv.pepper"):
            tunnel.sput(sec, "ocserv.pepper", _random.token_hex(32))
        pepper = tunnel.sget(sec, "ocserv.pepper")
        todo = []
        for d, _ in users:
            pw, new = tunnel.ensure_password(sec, d)
            if new:
                done.append(trs("gw_in.made.password", device=d["id"]))
            h = tunnel.sget(sec, "dev.%s.ocserv.hash" % d["id"])
            if not h or not h.startswith("$6$%s$" % salt_for(pepper, d["id"], pw)):
                todo.append((d, pw))
        need_cert = not tunnel.sget(sec, "ocserv.server.key") or not tunnel.sget(sec, "ocserv.server.crt")
        if (todo or need_cert) and runner is None:
            raise InboundError(tr("gw_in_ocserv.err.on_apply"))
        if need_cert:
            try:
                host = tunnel.host(st)
            except InboundError:
                host = None
            tunnel.ensure_dir(self.dir())
            key, crt = pki.self_signed(runner, self.dir(), host or "kaskad-gw", host)
            tunnel.sput(sec, "ocserv.server.key", key)
            tunnel.sput(sec, "ocserv.server.crt", crt)
            done.append(trs("gw_in.made.server_cert"))
        for d, pw in todo:
            tunnel.sput(sec, "dev.%s.ocserv.hash" % d["id"], self.hash_password(runner, pw, salt_for(pepper, d["id"], pw)))
            done.append(trs("gw_in_ocserv.made.hash", device=d["id"]))
        return done

    def hash_password(self, runner, password, salt):
        """SHA-512 crypt through openssl; the password goes on stdin, never on the command line."""
        res = runner.run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input=password + "\n")
        out = (res.out or "").strip()
        if not res.ok or not out.startswith("$6$%s$" % salt):
            raise InboundError("openssl passwd: %s" % (tunnel.cmd_error(res) if not res.ok else tr("gw_in_ocserv.unexpected")))
        return out

    # ── render ──
    def cert_paths(self, st):
        """(certificate, key) of ocserv.conf: the copy of the domain's certificate of Let's Encrypt after
        ``kaskadctl site use ocserv on`` (docs/k3/SPEC-wave4.md §4.1), else the self-signed one of this inbound."""
        from kaskad.site import model as smodel
        return smodel.tls_pair(st, self.kind) or (self.path("server-cert.pem"), self.path("server-key.pem"))

    def conf_text(self, st):
        net = self.network()
        port = self.port(st)
        return "\n".join([
            "# Kaskad 3 gateway: OpenConnect (ocserv) inbound. Written by kaskadctl, edits are lost.",
            'auth = "plain[passwd=%s]"' % self.path("ocpasswd"),
            "tcp-port = %d" % port,
            "udp-port = %d" % port,
            "run-as-user = nobody",
            "run-as-group = daemon",
            "socket-file = %s" % self.run_path("ocserv.sock"),
            "occtl-socket-file = %s" % self.run_path("occtl.sock"),
            "use-occtl = true",
            "server-cert = %s" % self.cert_paths(st)[0],
            "server-key = %s" % self.cert_paths(st)[1],
            "isolate-workers = true",
            "max-clients = 256",
            "max-same-clients = 2",
            "rate-limit-ms = 100",
            "keepalive = 32400",
            "dpd = 90",
            "mobile-dpd = 300",
            "switch-to-tcp-timeout = 25",
            "try-mtu-discovery = false",
            'tls-priorities = "%s"' % tls_priorities(tunnel.tls_versions(tunnel.inbound_cfg(st, self.kind))),
            "auth-timeout = 240",
            "min-reauth-time = 300",
            "max-ban-score = 80",
            "ban-reset-time = 1200",
            "cookie-timeout = 300",
            "deny-roaming = false",
            "rekey-time = 172800",
            "rekey-method = ssl",
            "connect-script = %s" % self.path("hook.sh"),
            "disconnect-script = %s" % self.path("hook.sh"),
            "device = %s" % DEVICE,
            "predictable-ips = true",
            "ipv4-network = %s" % net.network_address,
            "ipv4-netmask = %s" % net.netmask,
            "tunnel-all-dns = true",
            "dns = %s" % tunnel.dns(st),
            "ping-leases = false",
            "route = default",
            "config-per-user = %s/" % self.users_dir(),
            "cisco-client-compat = true",
            "dtls-legacy = true",
            "",
        ])

    def hook_text(self):
        return "\n".join([
            "#!/bin/sh",
            "# Kaskad 3 gateway: ocserv connect/disconnect -> kaskadctl gw hook ocserv up|down <login> <ip>.",
            "# Session accounting only: never refuses a connection and does not make it wait.",
            'case "$REASON" in',
            "    connect) act=up ;;",
            "    disconnect) act=down ;;",
            "    *) exit 0 ;;",
            "esac",
            '%s gw hook ocserv "$act" "$USERNAME" "$IP_REMOTE" </dev/null >/dev/null 2>&1 &' % paths.kaskadctl(),
            "exit 0",
            "",
        ])

    def render(self, st, sec):
        key, crt = tunnel.sget(sec, "ocserv.server.key"), tunnel.sget(sec, "ocserv.server.crt")
        if not key or not crt:
            raise InboundError(tr("gw_in_ocserv.err.no_cert"))
        files = {
            self.path("ocserv.conf"): (self.conf_text(st), 0o600),
            self.path("server-cert.pem"): (crt, 0o600),
            self.path("server-key.pem"): (key, 0o600),
            self.path("hook.sh"): (self.hook_text(), 0o755),
        }
        passwd = []
        for d, lg in sorted(self.users(st), key=lambda x: x[1]):
            h = tunnel.sget(sec, "dev.%s.ocserv.hash" % d["id"])
            ip = tunnel.device_ip(d, ADDR_KIND)
            if not h or not ip:
                continue
            passwd.append("%s:*:%s" % (lg, h))
            files[os.path.join(self.users_dir(), lg)] = ("# device %s\nexplicit-ipv4 = %s\n" % (d["id"], ip), 0o600)
        files[self.path("ocpasswd")] = ("".join(line + "\n" for line in passwd), 0o600)
        return files

    def unit_texts(self, runner):
        exe = tunnel.which(runner, "ocserv", "/usr/sbin/ocserv")
        text = "\n".join([
            "[Unit]",
            "Description=Kaskad 3 gateway: OpenConnect inbound (ocserv)",
            "After=network-online.target kaskad-net.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "RuntimeDirectory=kaskad-gw-ocserv",
            "RuntimeDirectoryMode=0755",
            "ExecStart=%s --foreground --pid-file %s --config %s" % (exe, self.run_path("ocserv.pid"),
                                                                      self.path("ocserv.conf")),
            "ExecReload=/bin/kill -HUP $MAINPID",
            "Restart=on-failure",
            "RestartSec=5s",
            "PrivateTmp=true",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ])
        return {UNIT: text}

    # ── interface of §3 ──
    def requirements(self, runner):
        ok = tunnel.module_available(runner, "tun") or os.path.exists(paths.P("/dev/net/tun"))
        port = self.default_port()
        return {"kind": self.kind, "engine": self.engine, "packages": ["ocserv", "openssl"], "modules": ["tun"],
                "ports": [{"proto": "tcp", "port": port}, {"proto": "udp", "port": port}], "supported": ok,
                "why": "" if ok else tr("gw_in.why.no_tun")}

    def install(self, runner):
        tunnel.install_packages(runner, ["ocserv", "openssl"], {"ocserv": ["ocserv.service"]})
        tunnel.modprobe(runner, ["tun"])
        tunnel.ensure_dir(self.dir())
        tunnel.install_units(runner, self.unit_texts(runner))
        tunnel.log_tr(self.kind, "gw_in.log.installed")

    def uninstall(self, runner):
        tunnel.remove_units(runner, [UNIT])
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        tunnel.unmask_packages(runner, ["ocserv"])
        tunnel.log_tr(self.kind, "gw_in_ocserv.log.removed")

    @staticmethod
    def _logins(passwd_text):
        return set(line.split(":", 1)[0] for line in (passwd_text or "").splitlines() if ":" in line)

    @staticmethod
    def _hashes(passwd_text):
        """{login: hash} of an ocpasswd text (compared only)."""
        out = {}
        for line in (passwd_text or "").splitlines():
            f = line.split(":", 2)
            if len(f) == 3:
                out[f[0]] = f[2]
        return out

    def reset_secrets(self, device_id):
        """OpenConnect logs in with the device's shared password; its hash is made again."""
        return [tunnel.shared_password_key(device_id), "dev.%s.ocserv." % device_id]

    @staticmethod
    def _ports(conf_text):
        """What only a restart changes: the ports and the PATHS of the certificate (``[scope: vhost
        (non-reloadable)]``; the files themselves ocserv re-reads on SIGHUP — a renewal is a reload), the TLS versions
        (``tls-priorities``, F4)."""
        return [line for line in (conf_text or "").splitlines()
                if line.startswith(("tcp-port", "udp-port", "server-cert", "server-key", "tls-priorities"))]

    def disconnect(self, runner, logins):
        for lg in logins:
            runner.run([self.occtl(runner), "-s", self.run_path("occtl.sock"), "disconnect", "user", lg])

    def apply(self, st, sec, runner):
        self.cfg(st)
        prepared = self.prepare(st, sec, runner)
        tunnel.ensure_dir(self.dir())
        tunnel.ensure_dir(self.users_dir())
        old_conf = util.read_text(self.path("ocserv.conf"))
        old_passwd = util.read_text(self.path("ocpasswd"))
        old_users = self._logins(old_passwd)
        files = self.render(st, sec)
        changed = tunnel.write_files(files)
        unit_changed = bool(tunnel.refresh_units(runner, self.unit_texts(runner)))
        removed = tunnel.prune(self.users_dir(), [p for p in files if os.path.dirname(p) == self.users_dir()])
        new_users = self._logins(files[self.path("ocpasswd")][0])
        moved = [os.path.basename(p) for p in changed if os.path.dirname(p) == self.users_dir()
                 and os.path.basename(p) in old_users]
        old_h, new_h = self._hashes(old_passwd), self._hashes(files[self.path("ocpasswd")][0])
        rekeyed = [lg for lg in old_h if lg in new_h and new_h[lg] != old_h[lg]]
        cut = sorted((old_users - new_users) | set(removed) | set(moved) | set(rekeyed))
        if not tunnel.wanted(st, self.kind):
            action = "stopped" if tunnel.stop_unit(runner, UNIT) else "off"
            tunnel.close_ufw(runner, self.dir(), self.kind)
        else:
            if tunnel.run_unit(runner, UNIT, self.title):
                action = "started"
            elif unit_changed or any(self.path(n) in changed for n in ("server-cert.pem", "server-key.pem")) or \
                    self._ports(old_conf) != self._ports(files[self.path("ocserv.conf")][0]):
                tunnel.systemctl(runner, "restart", UNIT)
                action = "restarted"
            else:
                if self.path("ocserv.conf") in changed:
                    tunnel.systemctl(runner, "reload", UNIT)
                    action = "reloaded"
                else:
                    action = "unchanged"
                if cut:                                  # a reload keeps sessions: cut the removed ones
                    self.disconnect(runner, cut)
                    action += ", cut %s" % ",".join(cut)
            port = self.port(st)
            tunnel.sync_ufw(runner, self.dir(), self.kind, [(port, "tcp"), (port, "udp")])
        names = [os.path.relpath(p, self.dir()) for p in changed]
        if names or prepared or action not in ("unchanged", "off"):
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_ocserv.log.applied", action=action, users=len(new_users),
                                         files=", ".join(names) or tr("gw_in.log.no_changes"),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
        return {"action": action, "changed": names, "prepared": prepared}

    def status(self, runner):
        installed = tunnel.unit_installed(UNIT)
        running = installed and tunnel.is_active(runner, UNIT)
        port = None
        for line in self._ports(util.read_text(self.path("ocserv.conf"))):
            v = line.split("=", 1)[1].strip()
            if v.isdigit():
                port = int(v)
        sessions = []
        if running:
            res = runner.run([self.occtl(runner), "--json", "-s", self.run_path("occtl.sock"), "show", "users"])
            owners = {}
            d = self.users_dir()
            for n in (os.listdir(d) if os.path.isdir(d) else []):
                first = (util.read_text(os.path.join(d, n)) or "").split("\n", 1)[0]
                if first.startswith("# device "):
                    owners[n] = first[len("# device "):].strip()
            for u in parse_users(res.out if res.ok else ""):
                sessions.append({"device": owners.get(u["login"]), "login": u["login"], "ip": u["ip"],
                                 "since": u["since"], "rx": u["rx"], "tx": u["tx"]})
        return {"kind": self.kind, "installed": installed, "running": running, "port": port,
                "sessions": sessions, "units": [UNIT]}

    # ── issuance ──
    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        lg = tunnel.login(dev)
        if dev.get("id") not in [d["id"] for d, _ in self.users(st)] or not lg:
            raise InboundError(tr("gw_in.err.not_user", device=tunnel.device_name(dev), title="OpenConnect"))
        pw = tunnel.sget(sec, "dev.%s.password" % dev["id"])
        crt = tunnel.sget(sec, "ocserv.server.crt")
        if not pw or not crt or not tunnel.sget(sec, "dev.%s.ocserv.hash" % dev["id"]):
            raise InboundError(tr("gw_in_ocserv.err.no_password", device=tunnel.device_name(dev)))
        from kaskad.site import model as smodel
        domain = smodel.use_for(st, self.kind)          # «kaskadctl site use ocserv on»: Let's Encrypt, the domain
        host, port = domain or tunnel.host(st), self.port(st)
        server = "https://" + (host if port == 443 and ":" not in host else tunnel.hostport(host, port))
        if domain:
            fields = tunnel.record("fields", tr("gw_in.rec.fields", title="OpenConnect"),
                                   [[tr("gw_in.label.server"), server], [tr("gw_in.label.login"), lg],
                                    [tr("gw_in.label.password"), pw]], None,
                                   [tr("gw_in_ocserv.le_note", domain=domain)])
            guide = tunnel.record("guide", tr("gw_in_ocserv.rec.guide"), "\n".join([
                tr("gw_in_ocserv.gui_le", server=server),
                tr("gw_in_ocserv.cisco_le", server=server),
                "Linux: sudo openconnect --protocol=anyconnect --user=%s %s" % (lg, server),
            ]), None, [])
            command = tunnel.record("guide", tr("gw_in.rec.linux_cmd", title="OpenConnect"),
                                    "sudo openconnect --protocol=anyconnect --user=%s %s" % (lg, server),
                                    None, [tr("gw_in_ocserv.linux_note")])
            recs = [command, fields, guide] if p == "linux" else [fields, guide]
            return tunnel.result(self.kind, recs)
        pin = pki.spki_pin(crt)
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="OpenConnect"),
                               [[tr("gw_in.label.server"), server], [tr("gw_in.label.login"), lg],
                                [tr("gw_in.label.password"), pw], [tr("gw_in_ocserv.label.fingerprint"), pin]],
                               None, [tr("gw_in_ocserv.self_signed")])
        guide = tunnel.record("guide", tr("gw_in_ocserv.rec.guide"), "\n".join([
            tr("gw_in_ocserv.gui_self", server=server, pin=pin),
            tr("gw_in_ocserv.cisco_self", server=server),
            "Linux: sudo openconnect --protocol=anyconnect --user=%s --servercert %s %s" % (lg, pin, server),
        ]), None, [])
        command = tunnel.record("guide", tr("gw_in.rec.linux_cmd", title="OpenConnect"),
                                "sudo openconnect --protocol=anyconnect --user=%s --servercert %s %s" % (lg, pin, server),
                                None, [tr("gw_in_ocserv.linux_note")])
        recs = [command, fields, guide] if p == "linux" else [fields, guide]
        return tunnel.result(self.kind, recs)


registry.register(OcservInbound)
