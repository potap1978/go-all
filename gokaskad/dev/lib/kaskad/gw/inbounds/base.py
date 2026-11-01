"""The interface of an inbound adapter (docs/k3/SPEC-wave2-gateway.md §3, §7) and ``DummyInbound`` for tests.

An adapter is one class per inbound kind, registered with ``kaskad.gw.registry.register(cls)`` when its
module is imported (AmneziaWG registers three classes). The core (work package 4) calls it; the adapter
never touches the table, the routes, DNS or xray, writes only its own paths (``/etc/kaskad/gw/<kind>/…``;
system paths of its daemon only if the daemon cannot read elsewhere) and controls only its own units
``kaskad-gw-<kind>*.service`` — and the UFW rules of its own ports (comment ``kaskad-gw <kind>``).

What the core guarantees when it calls an adapter:
  * ``st`` is the whole state (a dict) and ``sec`` a ``kaskad.secrets.Store`` (a dict); a secret the
    adapter creates goes to ``kaskad.secrets.set`` AND into ``sec``. The inbound record is
    ``st["gateway"]["inbounds"][kind]`` — ``port``/``subnet``/… as in spec §2;
  * ``apply``, ``prepare``, ``render``, ``issue`` run under the state lock on the state being applied
    (``kaskad.gw.addr.using(st)``: ``addr.subnet(kind)`` without ``st`` resolves its base); the core saves
    ``st`` after a successful apply — what ``prepare`` writes into ``st`` (pub, params, line, port, subnet,
    ``device["ips"]``) is kept. ``install``/``uninstall`` run WITHOUT the lock (packages, builds) and are
    idempotent;
  * ``apply`` is called for EVERY installed inbound on every gateway change, also when it must not run
    (inbound disabled, gateway off — ``wanted(st)`` false): then it stops its units and closes its UFW
    ports; it returns {"action", "changed", "prepared"};
  * order of the core's apply: adapters first (they allocate addresses), then the table (gw_route with
    the new addresses), then xray (``xray_inbounds`` / ``xray_routing``), then DNS;
  * the route of a device is the core's business (gw_route, xray); the adapter only brings the client in.

Methods of spec §3 are the contract; the others have working defaults. Agreed additions (WP4 + WP5):
``prepare(st, sec, runner=None)``, ``default_port()`` (a method — AmneziaWG answers a random port),
``addr_kinds(st)``, ``unit_texts(runner)``, ``xray_inbounds(st, sec)``, ``xray_routing(st, sec, target)``,
``port_fields()`` / ``fixed_ports()`` (ports for the core's conflict check, defaults from spec §2), ``dynamic``.

What the core gives an adapter besides ``st``/``sec``: ``model.devices_for(st, kind)`` (its peers),
``addr.allocate(st, device, pool)`` -> address string, ``addr.subnet(kind)`` / ``addr.server_ip(kind)``,
``addr.dns_ip(st)`` (10.77.0.1 — the DNS to hand out), ``st["gateway"]["host"]`` (``model.public_host``:
the address or name clients connect to), ``sec["dev.<id>.password"]`` (one password per device),
``kaskad.gw.sys`` (apt_install under policy-rc.d, write_unit, systemd, modprobe, ufw_allow/ufw_delete,
sysctl_set) and ``kaskadctl gw hook <kind> up|down <identity> <ip>`` for daemons (IKEv2: ``dynamic``).
"""

import os

from ... import paths, util
from ...errors import KaskadError
from ...i18n import tr

# Ports of the inbounds in their state records (spec §2): (field, proto). A field can carry two protocols
# (OpenConnect TCP + DTLS, SOCKS5 TCP + UDP). Fixed ports cannot change.
PORT_FIELDS = {
    "wg": (("port", "udp"),), "awg1": (("port", "udp"),), "awg2": (("port", "udp"),), "awg3": (("port", "udp"),),
    "sstp": (("port", "tcp"),), "openvpn": (("udp_port", "udp"), ("tcp_port", "tcp")),
    "ocserv": (("port", "tcp"), ("port", "udp")), "socks5": (("port", "tcp"), ("port", "udp")),
    "vless": (("port", "tcp"),), "hy2": (("port", "udp"),),
}
FIXED_PORTS = {"l2tp": (("udp", 1701), ("udp", 500), ("udp", 4500)), "pptp": (("tcp", 1723),),
               "ikev2": (("udp", 500), ("udp", 4500))}


class InboundError(KaskadError):
    """An adapter failed: the message is for people (short, in the language of the moment), never with secrets."""


class Inbound(object):
    kind = ""              # wg awg1 awg2 awg3 l2tp pptp sstp ikev2 openvpn ocserv socks5 vless hy2
    engine = ""            # what is installed (a line on the «Протоколы» screen)
    title = ""             # for people
    dynamic = False        # addresses come from the daemon (IKEv2): ``gw hook`` puts them into gw_route
    # stage 1 (SPEC-stage1-exits §2.4, T5) — an inbound of the gateway xray whose users live through its API:
    xray_hot = False       # users and their chains change without a restart (gw/xray.py hot_plan)
    xray_hot_prefix = None  # the ruleTag prefix of its per-user chains ("vl-", "sk-", "h2-"): gw/xray.py knows a hot
    #                         inbound by it — a new hot inbound needs no edit of xray.py
    xray_add_user = None   # how a NEW user comes live: "cli" (``xray api adu``), "grpc" (gw/xraygrpc.py — the inbounds
    #                         ``adu`` does not know, Hysteria2), None — a restart of xray (SOCKS5: «not a UserManager»)

    # ── spec §3 ──────────────────────────────────────────────────────────
    def requirements(self, runner):
        """{"packages": [...], "modules": [...], "ports": [...], "supported": bool, "why": str}."""
        return {"packages": [], "modules": [], "ports": [], "supported": True, "why": ""}

    def install(self, runner):
        """Packages without autostart (``kaskad.gw.sys.apt_install``), builds, units. Idempotent."""

    def uninstall(self, runner):
        """Stop, remove own units, configs and UFW rules. Packages stay."""

    def render(self, st, sec):
        """{path: (text, mode)} — configs from state + secrets. Pure: no commands, no writes."""
        return {}

    def apply(self, st, sec, runner):
        """prepare, write ``render``, run or stop the units (``wanted``). Default: files + ``units()``."""
        prepared = self.prepare(st, sec, runner) or []
        changed = self.write_files(self.render(st, sec))
        if not self.wanted(st):
            stopped = False
            for u in self.units():
                if self.unit_active(runner, u):
                    runner.run(["systemctl", "disable", "--now", u])
                    stopped = True
            return {"action": "stopped" if stopped else "off", "changed": changed, "prepared": prepared}
        action = "unchanged"
        for u in self.units():
            if not self.unit_active(runner, u):
                from .. import sys as gsys              # late: kaskad.gw.sys imports nothing of the adapters
                gsys.enable(runner, u, now=True, check=False)
                action = "started"
            elif changed:
                runner.run(["systemctl", "try-restart", u])
                action = "restarted"
        return {"action": action, "changed": changed, "prepared": prepared}

    def status(self, runner):
        """{"running": bool, "port": int|None, "sessions": [{"device", "ip", "since", "rx", "tx"}], ...}."""
        units = self.units()
        running = bool(units) and all(self.unit_active(runner, u) for u in units)
        return {"kind": self.kind, "running": running, "port": None, "sessions": [], "units": units}

    def issue(self, st, sec, device, os=None):
        """Issue for an OS (spec §4): {"format", "title", "body", "filename", "notes", "inbound", "items"}
        — the preferred record on top, all records in ``items``. The only place secrets are given out."""
        raise InboundError(tr("gw_in.err.no_issue", title=self.title or self.kind))

    def ifaces(self):
        """iifname patterns of the clients' interfaces (``kgw-wg``, ``kgw-oc*``): IPv6 from them is dropped."""
        return []

    # ── agreed additions ─────────────────────────────────────────────────
    def prepare(self, st, sec, runner=None):
        """Fill what is missing (keys, certificates, passwords, addresses, parameters). -> created (no
        secrets). Idempotent; runs under the state lock."""
        return []

    def default_port(self):
        """Port of a new inbound (spec §2); 0 — none of its own (L2TP, PPTP, IKEv2) or chosen by the core."""
        return 0

    def addr_kinds(self, st=None):
        """Keys of ``device["ips"]`` this inbound uses (pools of ``kaskad.gw.addr``)."""
        from .. import addr
        return list(addr.pools_of(self.kind))

    def unit_texts(self, runner):
        return {}

    def port_fields(self):
        """[(record field, proto)] holding the inbound's ports (for the core's conflict check)."""
        return list(PORT_FIELDS.get(self.kind, ()))

    def fixed_ports(self):
        return list(FIXED_PORTS.get(self.kind, ()))

    def defaults(self, st):
        """Fields of a new inbound record besides enabled/port/subnet (e.g. {"line": "2.0"})."""
        return {}

    def xray_inbounds(self, st, sec):
        """Inbounds this adapter adds to kaskad-gw-xray (SOCKS5)."""
        return []

    def xray_routing(self, st, sec, target=None):
        """Routing rules for those inbounds; ``target(route, device)`` -> {"outboundTag": …} or {"balancerTag": …}
        is the core's mapping of a device route (pool / group / exit:<id> / direct / block, with fail) to xray —
        with the device, the exit it is pinned to (stage 1, §2.4); ``device`` may be left out (the route alone)."""
        return []

    def xray_user(self, device):
        """The xray user (``email`` of a client, ``user`` of the rules and the counters) of ``device`` in this inbound:
        the device login; Hysteria2 adds ".h2" — its own, so the traffic of a device on VLESS and Hysteria2 is never
        counted twice (SPEC-stage1-hy2-inbound §2, С5). The default is what the rules always took: the login, else
        the id."""
        d = device or {}
        return str(d.get("login") or d.get("id") or "")

    def xray_account(self, client):
        """(type, bytes) of the protobuf account of one client of the rendered inbound (a dict of its ``clients``) —
        what ``xray_add_user = "grpc"`` sends (gw/xraygrpc.py); None — not supported."""
        return None

    def secret_prefixes(self, device_id):
        """Prefixes of the device's secrets that belong to this inbound alone: ``gw device revoke`` deletes
        them (``dev.<id>.<kind>.`` — OpenVPN keeps its certificate under ``dev.<id>.ovpn.``)."""
        return ["dev.%s.%s." % (device_id, self.kind)]

    def reset_secrets(self, device_id):
        """What ``gw device reset <dev> --inbound <kind>`` deletes so that prepare() makes it new: prefixes
        (ending with ".") and keys. The inbounds on the device's shared password add ``dev.<id>.password``
        (it changes for all of them: SHARED_PASSWORD_KINDS of tunnel.py)."""
        return self.secret_prefixes(device_id)

    # ── helpers ──────────────────────────────────────────────────────────
    def units(self):
        return ["kaskad-gw-%s.service" % self.kind]

    def record(self, st):
        return ((st.get("gateway") or {}).get("inbounds") or {}).get(self.kind)

    def wanted(self, st):
        """Installed, enabled, and the gateway is on."""
        rec = self.record(st)
        return isinstance(rec, dict) and rec.get("enabled", True) is not False \
            and (st.get("gateway") or {}).get("enabled", True) is not False

    def devices(self, st):
        from .. import model
        return model.devices_for(st, self.kind)

    def ports(self, st):
        """[(proto, port)] the inbound listens on: the record's port fields + fixed ports."""
        rec = self.record(st) or {}
        out = []
        for field, proto in self.port_fields():
            p = rec.get(field)
            if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 and (proto, p) not in out:
                out.append((proto, p))
        out.extend(tuple(x) for x in self.fixed_ports() if tuple(x) not in out)
        return out

    def ports_view(self, st):
        """[(proto, "port" | "lo–hi")] for people (the «Порты» line of the overview, the card of the panel): as ``ports``
        by default; an inbound with a range of ports (Hysteria2 port hopping) gives the range as ONE entry — ``ports``
        lists every port of it for the conflict checks."""
        return [(proto, str(port)) for proto, port in self.ports(st)]

    def conf_path(self, *parts):
        return os.path.join(paths.gw_dir(), self.kind, *parts)

    def write_files(self, files):
        """Write {path: (text, mode)} atomically, only what changed. Returns the written paths."""
        changed = []
        for path in sorted(files):
            text, mode = files[path]
            if util.read_text(path) == text:
                try:
                    if (os.stat(path).st_mode & 0o7777) == mode:
                        continue
                except OSError:
                    pass
            util.atomic_write(path, text, mode)
            changed.append(path)
        return changed

    @staticmethod
    def unit_active(runner, unit):
        return runner.run(["systemctl", "is-active", unit]).out.strip() == "active"


class DummyInbound(Inbound):
    """A test double of an adapter: records calls, renders one file per kind, issues a fake config.
    ``DummyInbound(kind="wg")`` — any kind; ``supported=False`` imitates a system without the protocol."""

    engine = "dummy"

    def __init__(self, kind="wg", title=None, proto="udp", default_port=51820, random_port=False,
                 ifaces=None, supported=True, why="", fixed_ports=(), dynamic=False, xray_inbound=None):
        self.kind = kind
        self.title = title or tr("gw_in.dummy.title", kind=kind)
        self.proto = proto
        self._port = 0 if random_port else default_port
        self._fixed = tuple(fixed_ports)
        self.dynamic = dynamic
        self._ifaces = list(ifaces) if ifaces is not None else ["kgw-%s" % kind]
        self._supported = supported
        self._why = why
        self._xray_inbound = xray_inbound
        self.calls = []
        self.sessions = []
        self.running = False

    def default_port(self):
        return self._port

    def port_fields(self):
        return [("port", self.proto)] if self.proto else []

    def fixed_ports(self):
        return list(self._fixed)

    def requirements(self, runner):
        return {"packages": ["dummy-%s" % self.kind], "modules": [], "ports": [],
                "supported": self._supported, "why": self._why}

    def install(self, runner):
        self.calls.append("install")

    def uninstall(self, runner):
        self.calls.append("uninstall")

    def prepare(self, st, sec, runner=None):
        self.calls.append("prepare")
        from .. import addr
        sec.setdefault_new("%s.server.private" % self.kind, lambda: "SERVERKEY-%s" % self.kind)
        for d in self.devices(st):
            for ak in addr.pools_of(self.kind):
                addr.allocate(st, d, ak)
            sec.setdefault_new("dev.%s.%s.private" % (d["id"], self.kind), lambda: "DEVKEY-%s" % d["id"])
        return []

    def render(self, st, sec):
        from .. import addr
        lines = ["# dummy %s" % self.kind]
        for d in self.devices(st):
            ips = [d.get("ips", {}).get(p) for p in addr.pools_of(self.kind)]
            lines.append("%s %s %s" % (d["id"], d.get("login", ""), ",".join(i for i in ips if i)))
        return {self.conf_path("dummy.conf"): ("\n".join(lines) + "\n", 0o600)}

    def apply(self, st, sec, runner):
        self.prepare(st, sec, runner)
        changed = self.write_files(self.render(st, sec))
        if not self.wanted(st):
            self.calls.append("stop")
            self.running = False
            return {"action": "stopped", "changed": changed, "prepared": []}
        self.calls.append("apply")
        self.running = True
        return {"action": "applied", "changed": changed, "prepared": []}

    def status(self, runner):
        return {"kind": self.kind, "running": self.running, "port": None, "sessions": list(self.sessions)}

    def issue(self, st, sec, device, os=None):
        from .. import addr
        ip = ""
        for p in addr.pools_of(self.kind):
            ip = (device.get("ips") or {}).get(p) or ip
        key = sec.get("dev.%s.%s.private" % (device["id"], self.kind), "")
        rec = {"format": "conf", "title": tr("gw_in.dummy.issue_title", title=self.title, os=os),
               "body": "PrivateKey = %s\nAddress = %s\nEndpoint = %s\n" % (key, ip, (st.get("gateway") or {}).get("host")),
               "filename": "%s-%s.conf" % (device.get("login") or device["id"], self.kind), "notes": []}
        return dict(rec, inbound=self.kind, items=[rec])

    def ifaces(self):
        return list(self._ifaces)

    def xray_inbounds(self, st, sec):
        return [dict(self._xray_inbound)] if self._xray_inbound and self.wanted(st) else []

    def xray_routing(self, st, sec, target=None):
        if not self._xray_inbound or not self.wanted(st):
            return []
        from .. import model
        tag = self._xray_inbound["tag"]
        groups = {}
        for d in self.devices(st):
            t = target(model.route_of(st, d), d)         # the device: its pin by the decision (stage 1, §2.2)
            groups.setdefault(tuple(sorted(t.items())), (t, []))[1].append(d.get("login") or d["id"])
        rules = []
        for key in sorted(groups):
            t, users = groups[key]
            rules.append(dict({"type": "field", "inboundTag": [tag], "user": sorted(users)}, **t))
        if rules:
            rules.append({"type": "field", "inboundTag": [tag], "outboundTag": "block"})
        return rules
