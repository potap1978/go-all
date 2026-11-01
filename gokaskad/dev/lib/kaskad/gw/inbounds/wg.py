"""WireGuard inbound (``wg``) and the machinery it shares with the AmneziaWG lines (awg.py).

Kernel WireGuard (package wireguard-tools), interface ``kgw-wg``, node address .1 of the subnet
(10.77.1.0/24), UDP port from ``inbounds.wg.port`` (51820 by default).

Files (0600): /etc/wireguard/kgw-wg.conf (AppArmor on Ubuntu 26.04 lets wg read only /etc/wireguard),
the rest in /etc/kaskad/gw/wg/:
    kgw-wg.conf   for ``wg setconf`` / ``wg syncconf``: [Interface] PrivateKey + ListenPort, one [Peer] per
                  device with ``wg`` in its access: PublicKey, PresharedKey, AllowedIPs = <ip>/32
    env           ADDR=10.77.1.1/24, MTU=<server MTU> — the unit reads it (gw/mtu.py: the budget of the outer
                  packet, a multiple of 16, 1232…the clients' MTU; the clients keep theirs — 1280 by default)
    peers.json    public key -> device id and address (status() works without the state)
    ufw.json      ports allowed in UFW by the last apply

Unit kaskad-gw-wg.service (oneshot): ``ip link add type wireguard`` → ``wg setconf`` → address → MTU, up;
reload = ``wg syncconf``. No wg-quick: routes and DNS of the node are not touched.

apply(): changed peers, port or key → ``wg syncconf`` (sessions of the other devices go on); changed MTU only
(the card of the server, an update — gw/mtu.py) → ``ip link set mtu`` on the live interface, no restart (T-N5 3.2.19:
a restart costs every AmneziaWG client 14–23 s — the client handshakes again only after ≈15 s); changed address,
a unit whose start lines changed → restart, with the reason in /etc/kaskad/gw/restarts.json (``gw status``: «последний
перезапуск входа»); inbound or gateway switched off → the unit stops, the UFW port closes.

Secrets: ``wg.server.private``, ``dev.<id>.wg.private``, ``dev.<id>.wg.psk``. State: ``inbounds.wg.pub``.
Client: Address <ip>/32, DNS = gateway DNS (10.77.0.1), AllowedIPs ``0.0.0.0/0, ::/0`` (IPv6 goes into the
tunnel and dies there — no leak past the node), Endpoint = gateway host:port, PersistentKeepalive 25.
"""

import ipaddress
import json
import os

from kaskad import paths, util
from kaskad.gw import issue_os, registry
from kaskad.gw import mtu as gwmtu
from kaskad.gw.inbounds import tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.i18n import retr, tr, trs
from kaskad.vendor import awgkeys

ACTIVE_SECONDS = 180          # a peer with a handshake this fresh counts as connected (rekey every 120 s)
CLIENT_MTU = gwmtu.CLIENT_MTU  # as the Amnezia apps: safe on mobile networks (inbounds.wg.mtu after a reissue)

_pub_cache = {}


def pubkey(priv):
    if priv not in _pub_cache:
        _pub_cache[priv] = awgkeys.pubkey(priv)
    return _pub_cache[priv]


def env_mtu(text):
    """MTU= of an env file of the unit (int) or None."""
    for ln in (text or "").splitlines():
        k, sep, v = ln.partition("=")
        if sep and k.strip() == "MTU" and v.strip().isdigit():
            return int(v.strip())
    return None


def env_mtu_only(old, new):
    """The new MTU when only MTU= differs between two env files of the unit (comments aside), else None: then the
    MTU goes onto the live interface and the unit is not restarted."""
    def rest(text):
        return [ln.strip() for ln in (text or "").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#") and not ln.strip().startswith("MTU=")]
    if old is None or rest(old) != rest(new):
        return None
    now, was = env_mtu(new), env_mtu(old)
    return now if now is not None and was is not None and now != was else None


class WgFamily(Inbound):
    """WireGuard-style inbound: one interface, peers from devices, setconf/syncconf. Subclasses set
    kind/engine/title and override the tool, the unit and the config renderers."""

    kind = "wg"
    engine = "wireguard"
    title = "WireGuard"
    tool_name = "wg"
    keepalive = 25

    # ── names and paths ──
    @property
    def iface(self):
        return "kgw-" + self.kind

    @property
    def unit(self):
        return "kaskad-gw-%s.service" % self.kind

    def dir(self):
        return tunnel.kind_dir(self.kind)

    def conf_path(self):
        # Ubuntu 26.04 confines /usr/bin/wg with AppArmor (profile «wg»): it may open only
        # /etc/wireguard/** — any other path gives «fopen: Permission denied» even for root (stand,
        # 2026-10-04). Our own awg binary is not confined: the AmneziaWG lines keep /etc/kaskad/gw/<kind>/.
        if self.tool_name == "wg":
            return paths.P("/etc/wireguard/%s.conf" % self.iface)
        return os.path.join(self.dir(), self.iface + ".conf")

    def env_path(self):
        return os.path.join(self.dir(), "env")

    def peers_path(self):
        return os.path.join(self.dir(), "peers.json")

    def server_key(self):
        return "%s.server.private" % self.kind

    def device_key(self, dev_id, what):
        return "dev.%s.%s.%s" % (dev_id, self.kind, what)

    def default_port(self):
        return 51820

    def ifaces(self):
        return [self.iface]

    def addr_kinds(self, st=None):
        """Keys of ``device["ips"]`` this inbound uses."""
        return [self.kind]

    # ── settings from the state ──
    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st):
        return tunnel.port_value(self.cfg(st), "port", self.default_port())

    def network(self, st):
        return ipaddress.ip_network(tunnel.subnet(self.cfg(st), self.kind), strict=False)

    def server_addr(self, st):
        net = self.network(st)
        return "%s/%d" % (net.network_address + 1, net.prefixlen)

    def mtu(self, st):
        """MTU of the issued settings (the client's)."""
        return gwmtu.client_mtu(st, self.kind)

    def server_mtu(self, st):
        """MTU of the interface on the node: the outer packet fits the budget (gw/mtu.py §6.1) — no reissue."""
        return gwmtu.server_for(st, self.kind)

    def tool(self, runner):
        return tunnel.which(runner, "wg", "/usr/bin/wg")

    # ── keys, addresses, parameters ──
    def prepare(self, st, sec, runner=None):
        """Create what is missing: port and subnet in the state, the server key (+ ``pub``), addresses and
        keys of the devices; AmneziaWG — the line and its parameters. Changes the state in place (the
        caller saves it). -> list of what was created (no secrets)."""
        cfg = self.cfg(st)
        done = []
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = self.default_port()
            done.append(trs("gw_in.made.port", port=cfg["port"]))
        if not cfg.get("subnet"):
            cfg["subnet"] = tunnel.subnet(cfg, self.kind)
        priv = tunnel.sget(sec, self.server_key())
        if not priv:
            priv = awgkeys.genkey()
            tunnel.sput(sec, self.server_key(), priv)
            done.append(trs("gw_in_wg.made.server_key"))
        pub = pubkey(priv)
        if cfg.get("pub") != pub:
            cfg["pub"] = pub
        done += self.prepare_line(st, cfg)
        for d in tunnel.devices(st, self.kind):
            _, new_ip = tunnel.ensure_ip(st, d, self.kind)
            new_key = False
            for what, gen in (("private", awgkeys.genkey), ("psk", awgkeys.genpsk)):
                if not tunnel.sget(sec, self.device_key(d["id"], what)):
                    tunnel.sput(sec, self.device_key(d["id"], what), gen())
                    new_key = True
            if new_ip or new_key:
                done.append(trs("gw_in_wg.made.device", device=d["id"]))
        return done

    def prepare_line(self, st, cfg):
        return []

    def peers(self, st, sec):
        """Devices ready for the server config: [{"id", "ip", "pub", "psk"}]. Not prepared yet — skipped."""
        out = []
        for d in tunnel.devices(st, self.kind):
            ip = tunnel.device_ip(d, self.kind)
            priv = tunnel.sget(sec, self.device_key(d["id"], "private"))
            if not ip or not priv:
                continue
            out.append({"id": d["id"], "ip": str(ip).split("/")[0], "pub": pubkey(priv),
                        "psk": tunnel.sget(sec, self.device_key(d["id"], "psk")) or ""})
        return out

    # ── render ──
    def server_conf(self, st, sec, peers):
        priv = tunnel.sget(sec, self.server_key())
        if not priv:
            raise InboundError(tr("gw_in_wg.err.no_server_key", title=self.title))
        lines = ["# Kaskad 3 gateway: %s inbound %s (wg setconf). Written by kaskadctl, edits are lost."
                 % (self.title, self.iface),
                 "[Interface]", "PrivateKey = %s" % priv, "ListenPort = %d" % self.port(st)]
        for p in peers:
            lines += ["", "# device %s" % p["id"], "[Peer]", "PublicKey = %s" % p["pub"]]
            if p["psk"]:
                lines.append("PresharedKey = %s" % p["psk"])
            lines.append("AllowedIPs = %s/32" % p["ip"])
        return "\n".join(lines) + "\n"

    def env_text(self, st):
        return "# Kaskad 3 gateway: settings of %s (written by kaskadctl)\nADDR=%s\nMTU=%d\n" % (
            self.unit, self.server_addr(st), self.server_mtu(st))

    def render(self, st, sec):
        peers = self.peers(st, sec)
        index = {}
        for p in peers:
            index[p["pub"]] = {"device": p["id"], "ip": p["ip"]}
        return {
            self.conf_path(): (self.server_conf(st, sec, peers), 0o600),
            self.env_path(): (self.env_text(st), 0o600),
            self.peers_path(): (json.dumps(index, indent=2, sort_keys=True) + "\n", 0o600),
        }

    # ── units ──
    def unit_texts(self, runner):
        ip = tunnel.which(runner, "ip", "/usr/sbin/ip")
        wg = self.tool(runner)
        conf = self.conf_path()
        text = "\n".join([
            "[Unit]",
            "Description=Kaskad 3 gateway: WireGuard inbound (%s)" % self.iface,
            "After=network-online.target kaskad-net.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            "RemainAfterExit=yes",
            "EnvironmentFile=%s" % self.env_path(),
            "ExecStartPre=-%s link delete dev %s" % (ip, self.iface),
            "ExecStart=%s link add dev %s type wireguard" % (ip, self.iface),
            "ExecStart=%s setconf %s %s" % (wg, self.iface, conf),
            "ExecStart=%s address replace ${ADDR} dev %s" % (ip, self.iface),
            "ExecStart=%s link set dev %s mtu ${MTU} up" % (ip, self.iface),
            "ExecReload=%s syncconf %s %s" % (wg, self.iface, conf),
            "ExecStopPost=-%s link delete dev %s" % (ip, self.iface),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ])
        return {self.unit: text}

    # ── interface of §3 ──
    def requirements(self, runner):
        ok = tunnel.module_available(runner, "wireguard")
        return {"kind": self.kind, "engine": self.engine, "packages": ["wireguard-tools"],
                "modules": ["wireguard"], "ports": [{"proto": "udp", "port": self.default_port()}],
                "supported": ok, "why": "" if ok else tr("gw_in_wg.why.no_module")}

    def install(self, runner):
        tunnel.install_packages(runner, ["wireguard-tools"])
        tunnel.modprobe(runner, ["wireguard"])
        tunnel.ensure_dir(self.dir())
        tunnel.install_units(runner, self.unit_texts(runner))
        tunnel.log_tr(self.kind, "gw_in.log.installed")

    def uninstall(self, runner):
        tunnel.remove_units(runner, [self.unit])
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        conf = self.conf_path()
        if not conf.startswith(self.dir() + os.sep):      # /etc/wireguard/kgw-wg.conf: the server key is in it
            try:
                os.unlink(conf)
            except OSError:
                pass
        tunnel.log_tr(self.kind, "gw_in.log.removed_keys")

    def needs_restart(self, old_conf, new_conf):
        """Kernel WireGuard takes every change through syncconf."""
        return False

    def set_mtu(self, runner, value):
        """The server MTU onto the live interface (kernel WireGuard, the module and amneziawg-go — its TUN follows
        the netlink event). -> None, or the error text (the caller restarts the unit then)."""
        res = runner.run([tunnel.which(runner, "ip", "/usr/sbin/ip"), "link", "set", "dev", self.iface, "mtu", str(value)])
        return None if res.ok else tunnel.cmd_error(res)

    def syncconf(self, runner):
        """Peers and port onto the live interface. -> None, or the error text when it failed (the
        interface is gone or broken: sessions are lost anyway, the caller restarts the unit)."""
        res = runner.run([self.tool(runner), "syncconf", self.iface, self.conf_path()])
        return None if res.ok else tunnel.cmd_error(res)

    def apply(self, st, sec, runner):
        self.cfg(st)
        prepared = self.prepare(st, sec, runner)
        tunnel.ensure_dir(self.dir())
        old_conf = util.read_text(self.conf_path())
        old_env = util.read_text(self.env_path())
        files = self.render(st, sec)
        changed = tunnel.write_files(files)
        texts = self.unit_texts(runner)
        old_unit = util.read_text(tunnel.unit_file(self.unit))
        unit_changed = bool(tunnel.refresh_units(runner, texts))
        # Почему вход надо перезапустить (None — не надо): каждый перезапуск AmneziaWG стоит клиентам 15–25 с.
        why, mtu_now = None, None
        if unit_changed and tunnel.unit_needs_restart(old_unit, texts.get(self.unit)):
            why = "unit"
        if why is None and self.env_path() in changed:
            mtu_now = env_mtu_only(old_env, files[self.env_path()][0])
            if mtu_now is None:
                why = "addr"
        if why is None and self.conf_path() in changed and self.needs_restart(old_conf, files[self.conf_path()][0]):
            why = "params"
        live = None                     # (MTU was, MTU now) put onto the live interface without a restart
        if not tunnel.wanted(st, self.kind):
            action = "stopped" if tunnel.stop_unit(runner, self.unit) else "off"
            tunnel.close_ufw(runner, self.dir(), self.kind)
        else:
            if tunnel.run_unit(runner, self.unit, self.title):
                action = "started"
            elif why is not None:
                self._restart(runner, why, env_mtu(old_env), env_mtu(files[self.env_path()][0]))
                action = "restarted"
            else:
                action = "unchanged"
                if mtu_now is not None:
                    err = self.set_mtu(runner, mtu_now)
                    if err is None:
                        action, live = "mtu", (env_mtu(old_env), mtu_now)
                    else:
                        tunnel.log_tr(self.kind, "gw_in_wg.log.mtu_failed", mtu=mtu_now, error=err)
                        self._restart(runner, "mtu", env_mtu(old_env), mtu_now)
                        action = "restarted"
                if action != "restarted" and self.conf_path() in changed:
                    err = self.syncconf(runner)
                    if err is None:
                        action = "synced"
                    else:
                        tunnel.log_tr(self.kind, "gw_in_wg.log.syncconf_failed", tool=self.tool_name, error=err)
                        self._restart(runner, "syncconf", None, None)
                        action = "restarted"
            tunnel.sync_ufw(runner, self.dir(), self.kind, [(self.port(st), "udp")])
        names = [os.path.basename(p) for p in changed]
        if action != "unchanged" or names or prepared:
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_wg.log.applied", action=action, devices=len(self.peers(st, sec)),
                                         files=", ".join(names) or tr("gw_in.log.no_changes"),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else "",
                                         live=tr("gw_in_wg.log.live_mtu", was=live[0], now=live[1]) if live else ""))
        return {"action": action, "changed": names, "prepared": prepared}

    def _restart(self, runner, why, mtu_was, mtu_now):
        """Restart the unit and remember why (tunnel.note_restart: ``gw status``, the summary of an update)."""
        tunnel.systemctl(runner, "restart", self.unit)
        tunnel.note_restart(self.kind, why, mtu_was, mtu_now)

    def status(self, runner):
        installed = tunnel.unit_installed(self.unit)
        running = installed and tunnel.is_active(runner, self.unit)
        return {"kind": self.kind, "installed": installed, "running": running, "port": self.conf_port(),
                "sessions": self.sessions(runner) if running else [], "units": [self.unit]}

    def conf_port(self):
        for line in (util.read_text(self.conf_path()) or "").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "ListenPort" and v.strip().isdigit():
                return int(v.strip())
        return None

    def sessions(self, runner):
        index = tunnel.read_json(self.peers_path(), {})
        if not isinstance(index, dict):
            index = {}
        tool = self.tool(runner)
        hs = runner.run([tool, "show", self.iface, "latest-handshakes"])
        tr = runner.run([tool, "show", self.iface, "transfer"])
        if not hs.ok:
            return []
        traffic = {}
        for line in (tr.out if tr.ok else "").splitlines():
            f = line.split()
            if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
                traffic[f[0]] = (int(f[1]), int(f[2]))
        out = []
        now = tunnel.now()
        for line in hs.out.splitlines():
            f = line.split()
            if len(f) < 2 or not f[1].isdigit():
                continue
            ts = int(f[1])
            if ts <= 0 or now - ts > ACTIVE_SECONDS:
                continue
            info = index.get(f[0]) or {}
            rx, tx = traffic.get(f[0], (0, 0))
            out.append({"device": info.get("device"), "ip": info.get("ip"), "since": ts, "rx": rx, "tx": tx})
        return sorted(out, key=lambda s: str(s["device"]))

    # ── issuance ──
    def client_data(self, st, sec, device):
        """What a client config needs; InboundError if the device is not on this inbound or not prepared."""
        cfg = self.cfg(st)
        if device.get("id") not in [d["id"] for d in tunnel.devices(st, self.kind)]:
            raise InboundError(tr("gw_in.err.not_user", device=tunnel.device_name(device), title=self.title))
        ip = tunnel.device_ip(device, self.kind)
        priv = tunnel.sget(sec, self.device_key(device["id"], "private"))
        spriv = tunnel.sget(sec, self.server_key())
        if not ip or not priv or not spriv:
            raise InboundError(tr("gw_in_wg.err.no_device_keys", device=tunnel.device_name(device), title=self.title))
        return {"ip": str(ip).split("/")[0], "priv": priv,
                "psk": tunnel.sget(sec, self.device_key(device["id"], "psk")) or "",
                "server_pub": cfg.get("pub") or pubkey(spriv), "host": tunnel.host(st),
                "port": self.port(st), "dns": tunnel.dns(st), "mtu": self.mtu(st)}

    def client_conf(self, data):
        lines = ["[Interface]", "Address = %s/32" % data["ip"], "DNS = %s" % data["dns"],
                 "PrivateKey = %s" % data["priv"], "MTU = %d" % data["mtu"], "",
                 "[Peer]", "PublicKey = %s" % data["server_pub"]]
        if data["psk"]:
            lines.append("PresharedKey = %s" % data["psk"])
        lines += ["AllowedIPs = 0.0.0.0/0, ::/0", "Endpoint = %s" % tunnel.hostport(data["host"], data["port"]),
                  "PersistentKeepalive = %d" % self.keepalive]
        return "\n".join(lines) + "\n"

    def routeros_script(self, device, data):
        """RouterOS 7: WireGuard interface + peer, the address, NAT of the LAN into the tunnel and policy
        routing of the LAN through it (the router's own traffic, the handshake too, stays on main)."""
        c = 'comment="Kaskad"'
        peer = ['/interface wireguard peers add interface=kaskad-wg public-key="%s"' % data["server_pub"]]
        if data["psk"]:
            peer.append('preshared-key="%s"' % data["psk"])
        peer += ["endpoint-address=%s endpoint-port=%d" % (data["host"], data["port"]),
                 "allowed-address=0.0.0.0/0 persistent-keepalive=%ds %s" % (self.keepalive, c)]
        return "\n".join([
            "# Kaskad: WireGuard for MikroTik RouterOS 7, device %s" % _ascii(str(device.get("id"))),
            "# Paste into New Terminal or run /import file-name=kaskad-wg.rsc",
            '/interface wireguard add name=kaskad-wg mtu=%d private-key="%s" %s' % (data["mtu"], data["priv"], c),
            " ".join(peer),
            "/ip address add address=%s/32 interface=kaskad-wg %s" % (data["ip"], c),
            "/ip firewall nat add chain=srcnat out-interface=kaskad-wg action=masquerade %s" % c,
            "/ip firewall mangle add chain=forward out-interface=kaskad-wg protocol=tcp tcp-flags=syn "
            "action=change-mss new-mss=clamp-to-pmtu passthrough=yes %s" % c,
            "/routing table add name=kaskad fib %s" % c,
            "/ip route add dst-address=0.0.0.0/0 gateway=kaskad-wg routing-table=kaskad %s" % c,
            "/ip route add dst-address=%s/32 gateway=kaskad-wg %s" % (data["dns"], c),
            "# LAN through the tunnel: 192.168.88.0/24 is the default MikroTik LAN, put yours",
            "/routing rule add dst-address=192.168.88.0/24 action=lookup-only-in-table table=main %s" % c,
            "/routing rule add src-address=192.168.88.0/24 action=lookup-only-in-table table=kaskad %s" % c,
            "",
        ])

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        data = self.client_data(st, sec, dev)
        conf = self.client_conf(data)
        fname = issue_os.conf_filename("wg", device=dev, platform_name=p)
        hint = tr("gw_in_wg.hint." + p, file=fname) if p in HINT_PLATFORMS else None
        conf_rec = tunnel.record("conf", tr("gw_in_wg.rec.conf"), conf, fname, [hint] if hint else [])
        qr_rec = tunnel.record("qr", tr("gw_in_wg.rec.qr"), [conf], None, [tr("gw_in_wg.qr_note")])
        if p in ("ios", "android"):
            recs = [qr_rec, conf_rec]
        elif p == "mikrotik":
            rsc = tunnel.record("conf", tr("gw_in_wg.rec.rsc"), self.routeros_script(dev, data),
                                "kaskad-wg.rsc", [tr("gw_in_wg.rsc_note")])
            recs = [rsc, conf_rec]
        else:
            recs = [conf_rec, qr_rec]
        return tunnel.result(self.kind, recs)


HINT_PLATFORMS = ("ios", "android", "windows", "macos", "linux", "keenetic", "openwrt", "mikrotik")   # gw_in_wg.hint.*


def _ascii(text):
    """RouterOS scripts stay ASCII (the terminal garbles anything else)."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)


class WgInbound(WgFamily):
    kind = "wg"
    engine = "wireguard"
    title = "WireGuard"


registry.register(WgInbound)
