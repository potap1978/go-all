"""Hysteria2 inbound (``hy2``): no daemon of its own — an inbound of the gateway xray (kaskad/gw/xray.py, service
kaskad-gw-xray.service), like VLESS and SOCKS5 (docs/k3/SPEC-stage1-hy2-inbound.md). QUIC over UDP: 443/udp, taken —
8443/udp; the Salamander mask (on by default: to DPI it is random UDP, not QUIC); port hopping — an option, off by
default, up to HOP_MAX ports (xray holds a socket on every port of the range).

    xray_inbounds(st, sec)          -> [inbound]  a client per device with hy2 in its access — its own secret ``auth``,
                                       email = ``<login>.h2`` (its own, never the one of VLESS: the traffic of a device
                                       on both is not counted twice); present while the inbound is on, with no client
                                       too: the first device comes without a restart (gRPC ``AlterInbound``)
    xray_routing(st, sec, target)   -> [rule]     one chain per user tagged ``h2-<login>-<hash>-<n>`` (the route rule;
                                       the lists of stage 1 are the shared rules ``ls-`` of the core)
    xray_stats = True, xray_hot = True, xray_hot_prefix = "h2-", xray_add_user = "grpc"

The certificate: the node has a domain with a certificate of Let's Encrypt — ITS OWN COPY of it in
/opt/kaskad/gw/tls-hy2/domain (VLESS removes the copy it shares with nobody, /opt/kaskad/gw/tls, whenever it is not in
TLS mode); no domain — a self-signed pair (EC P-256, 10 years) kept in secrets.json and written to
/opt/kaskad/gw/tls-hy2/self, its SHA-256 (``pinSHA256``) in the links with ``insecure=1`` (the owner's decision 14.10,
D-020, as SSTP and OpenConnect). The mode is chosen by itself (``cert``: "domain" — the domain when there is one,
"self" — always the self-signed one) and is recorded by ``prepare`` (``cert_mode``): the config and the links always
speak of the same certificate. Directories 0750 root:kaskad-xray, keys 0640, chains 0644.

State record ``gateway.inbounds.hy2``: ``port`` (udp), ``hop`` (None | "20000-20099"), ``obfs`` (bool), ``cert``
("domain" | "self"), ``congestion`` ("bbr"), ``label``, ``pin`` (hex, no secret), ``cert_mode``. Secrets:
``inbound.hy2.obfs`` (the Salamander password), ``inbound.hy2.self.key``/``.crt`` (the self-signed pair),
``dev.<id>.hy2.auth`` (one per device). Nothing secret goes to argv or to the log.

Issuance: ``hysteria2://<auth>@<host>:<port>[,<hop>]/?sni=…&obfs=salamander&obfs-password=…[&insecure=1&pinSHA256=…]
#name``; the same link as a QR code and the fields for manual entry.
"""

import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
from urllib.parse import quote

from kaskad import paths, util
from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import pki, tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.gw.inbounds.vless import VlessInbound
from kaskad.i18n import retr, tr, trs

KIND = "hy2"
TAG = "gw-hy2"
XRAY_UNIT = "kaskad-gw-xray.service"
RULE_PREFIX = "h2-"                 # ruleTag of the per-user chains (gw/xray.py hot_plan knows them by it)
USER_SUFFIX = ".h2"                 # email of a client: <login>.h2 (never the one of VLESS — С5)
PORTS = (443, 8443)                 # udp
HOP_MAX = 100                       # ports of the range at the most: xray opens a socket on each
HOP_RANGE = (1024, 65535)
MIN_XRAY = (26, 3, 27)              # the inbound Hysteria of xray exists since 26.3.27
CONGESTIONS = ("bbr",)              # Brutal — after the stand (it needs honest speeds of the person)
CERT_MODES = ("domain", "self")
ALPN = "h3"
OBFS_KEY = "inbound.hy2.obfs"
SELF_KEY = "inbound.hy2.self.key"
SELF_CRT = "inbound.hy2.self.crt"
TLS_DIR = "/opt/kaskad/gw/tls-hy2"  # its own: the copy of VLESS (/opt/kaskad/gw/tls) is removed by VLESS
ACCOUNT_TYPE = "xray.proxy.hysteria.account.Account"     # protobuf of a user (gRPC AlterInbound — T0 С0.13)
TRAFFIC_FILE = "hy2-traffic.json"
_HOP = re.compile(r"^\s*(\d{1,5})\s*[-:–]\s*(\d{1,5})\s*$")
_AUTH = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def auth_key(device_id):
    return "dev.%s.hy2.auth" % device_id


def new_secret():
    """32 characters of base64url: 24 random bytes."""
    return _secrets.token_urlsafe(24)


def _version(v):
    try:
        return tuple(int(x) for x in str(v).lstrip("v").split(".")[:3])
    except ValueError:
        return ()


def parse_hop(value):
    """The range of port hopping: "20000-20099" (also «20000:20099», «20000–20099») -> "20000-20099"; "", "off",
    None -> None. InboundError: not a range, outside 1024–65535, more than HOP_MAX ports."""
    s = str(value if value is not None else "").strip().lower()
    if s in ("", "off", "no", "none", "0", "false"):
        return None
    m = _HOP.match(s)
    if not m:
        raise InboundError(tr("gw_in_hy2.err.hop_bad", value=repr(value)))
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo > hi or lo < HOP_RANGE[0] or hi > HOP_RANGE[1]:
        raise InboundError(tr("gw_in_hy2.err.hop_range", lo=HOP_RANGE[0], hi=HOP_RANGE[1]))
    if hi - lo + 1 > HOP_MAX:
        raise InboundError(tr("gw_in_hy2.err.hop_many", n=hi - lo + 1, max=HOP_MAX))
    return "%d-%d" % (lo, hi)


def hop_bounds(value):
    """(lo, hi) of a recorded range, None when there is none or it is broken (a hand edit: no range then)."""
    try:
        h = parse_hop(value)
    except InboundError:
        return None
    if not h:
        return None
    lo, hi = h.split("-")
    return int(lo), int(hi)


def pin_of(cert_pem):
    """``pinSHA256`` of the links: SHA-256 of the DER certificate, lower hex without colons."""
    return pki.sha256_fingerprint(cert_pem).replace(":", "").lower()


# ── the certificate ────────────────────────────────────────────────────────

def tls_dir(*parts):
    return os.path.join(paths.P(TLS_DIR), *parts)


def self_files():
    return tls_dir("self", "cert.pem"), tls_dir("self", "key.pem")


def domain_files():
    return tls_dir("domain", "fullchain.pem"), tls_dir("domain", "privkey.pem")


def domain_ready(st):
    """The domain of the node when its certificate of Let's Encrypt can be handed to Hysteria2 (the domain is set, the
    certificate is a real one — not of the staging CA — and the copy of the site is there); else ""."""
    from kaskad.site import model as site_model
    s = site_model.peek(st)
    if not s["domain"] or s["cert"] != "letsencrypt" or s["staging"]:
        return ""
    if not (os.path.isfile(site_model.fullchain_path()) and os.path.isfile(site_model.privkey_path())):
        return ""
    return s["domain"]


def _xray_gid():
    try:
        import grp
        from kaskad.gw import net
        return grp.getgrnam(net.XRAY_USER).gr_gid
    except (KeyError, ImportError):
        return None


def _put(dst, data, mode, gid):
    """``data`` into ``dst`` atomically when it differs; the owner and the mode every time. -> changed."""
    try:
        with open(dst, "rb") as f:
            same = f.read() == data
    except OSError:
        same = False
    if not same:
        util.atomic_write(dst, data, mode)
    os.chmod(dst, mode)
    if gid is not None:
        os.chown(dst, 0, gid)
    return not same


def _dir(path, gid):
    util.ensure_dir(path, 0o750)
    os.chmod(path, 0o750)
    if gid is not None:
        os.chown(path, 0, gid)


def sync_tls(mode, sec=None):
    """The certificate xray of the gateway reads for Hysteria2 (its user may not read /etc/kaskad): ``mode`` "self" —
    the pair of secrets.json into tls-hy2/self (the copy of the domain away); "domain" — the copy of the site's pair
    into tls-hy2/domain; None — everything of tls-hy2 away (the inbound removed). Only its own directory is ever
    removed. -> [names of the files that changed] ("removed" when a copy went). No exception (a log line)."""
    root = paths.P(TLS_DIR)
    if mode is None:
        if os.path.isdir(root):
            shutil.rmtree(root, ignore_errors=True)
            return ["removed"]
        return []
    gid = _xray_gid()
    changed = []
    try:
        _dir(root, gid)
        if mode == "domain":
            from kaskad.site import model as site_model
            _dir(tls_dir("domain"), gid)
            for src, (dst, m) in zip((site_model.fullchain_path(), site_model.privkey_path()),
                                     ((domain_files()[0], 0o644), (domain_files()[1], 0o640))):
                with open(src, "rb") as f:
                    if _put(dst, f.read(), m, gid):
                        changed.append(os.path.basename(dst))
        else:
            crt, key = tunnel.sget(sec, SELF_CRT), tunnel.sget(sec, SELF_KEY)
            if not crt or not key:
                return []
            _dir(tls_dir("self"), gid)
            for data, (dst, m) in zip((crt, key), ((self_files()[0], 0o644), (self_files()[1], 0o640))):
                if _put(dst, data.encode("utf-8"), m, gid):
                    changed.append(os.path.basename(dst))
            # the copy of the domain goes only once the config xray runs no longer names it: removed together with the
            # switch, a refused config (or a pool not read) would leave xray.json naming files that are gone — the next
            # restart of xray would fail for VLESS and SOCKS5 too (SEC-s1-t12 З-1). The next apply takes it away.
            if os.path.isdir(tls_dir("domain")) and not _named_by_xray(tls_dir("domain")):
                shutil.rmtree(tls_dir("domain"), ignore_errors=True)
                changed.append("removed")
    except OSError as e:
        tunnel.log_tr(KIND, "gw_in_hy2.log.tls_failed", error=e)
        return []
    if changed:
        tunnel.log_tr(KIND, "gw_in_hy2.log.tls_synced", mode=mode)
    return changed


def _named_by_xray(path):
    """The config xray of the gateway runs (xray.json) names ``path`` (an unreadable file — it does, to be safe)."""
    from kaskad.gw import xray
    try:
        with open(xray.config_path(), encoding="utf-8") as f:
            return path in f.read()
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _chain_tag(login, chain):
    h = hashlib.sha256(json.dumps(chain, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return ["%s%s-%s-%d" % (RULE_PREFIX, login, h, i) for i in range(len(chain))]


def _field(num, data):
    from kaskad.gw import xraygrpc
    return xraygrpc.field(num, data)


# ── the adapter ────────────────────────────────────────────────────────────

class Hy2Inbound(Inbound):
    kind = KIND
    engine = "xray"
    title = "Hysteria2"
    xray_stats = True
    xray_hot = True                   # users come through the API (gRPC AlterInbound); leaving — a restart (T0)
    xray_hot_prefix = RULE_PREFIX
    xray_add_user = "grpc"            # ``xray api adu`` does not know Hysteria (T0 С0.13)
    counters = VlessInbound.counters  # the per-user counters of xray's StatsService (the same API, its own users)
    traffic = VlessInbound.traffic

    def dir(self):
        return tunnel.kind_dir(KIND)

    def state_path(self):
        return os.path.join(self.dir(), "hy2.json")

    def traffic_path(self):
        return os.path.join(paths.gw_run_dir(), TRAFFIC_FILE)

    def default_port(self):
        return PORTS[0]

    def port_candidates(self, st):
        return list(PORTS)

    def defaults(self, st):
        return {"hop": None, "obfs": True, "cert": "domain", "congestion": CONGESTIONS[0]}

    def ifaces(self):
        return []

    def addr_kinds(self, st=None):
        return []

    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st):
        return tunnel.port_value(self.cfg(st), "port", self.default_port())

    def hop(self, st):
        b = hop_bounds((tunnel.inbound_cfg(st, self.kind) or {}).get("hop"))
        return "%d-%d" % b if b else None

    def obfs(self, st):
        return (tunnel.inbound_cfg(st, self.kind) or {}).get("obfs", True) is not False

    def congestion(self, st):
        c = (tunnel.inbound_cfg(st, self.kind) or {}).get("congestion")
        return c if c in CONGESTIONS else CONGESTIONS[0]

    def cert_pref(self, st):
        c = (tunnel.inbound_cfg(st, self.kind) or {}).get("cert")
        return c if c in CERT_MODES else "domain"

    def want_mode(self, st):
        """The certificate the inbound should take now: the domain's when it prefers it and the node has one."""
        return "domain" if self.cert_pref(st) == "domain" and domain_ready(st) else "self"

    def cert_mode(self, st):
        """The certificate the config and the links speak of: what ``prepare`` recorded (the last apply)."""
        m = (tunnel.inbound_cfg(st, self.kind) or {}).get("cert_mode")
        return m if m in CERT_MODES else "self"

    def ports(self, st):
        """Every port it listens on — the port and EACH port of the range: the neighbours (AWG, OpenVPN-UDP, forwards)
        must see the range in their conflict checks, also while xray is off (the range lives in the state)."""
        out = list(super().ports(st))
        b = hop_bounds((self.record(st) or {}).get("hop"))
        if b:
            out.extend(("udp", p) for p in range(b[0], b[1] + 1) if ("udp", p) not in out)
        return out

    def ports_view(self, st):
        rec = self.record(st) or {}
        out = []
        p = rec.get("port")
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535:
            out.append(("udp", str(p)))
        b = hop_bounds(rec.get("hop"))
        if b:
            out.append(("udp", "%d–%d" % b))
        return out

    def xray_user(self, device):
        lg = tunnel.login(device or {})
        return (lg + USER_SUFFIX) if lg else ""

    def accounts(self, st, sec):
        """[(device, xray user, auth)] of the devices ready to connect."""
        out, seen = [], set()
        for d in tunnel.devices(st, self.kind):
            u = self.xray_user(d)
            a = str(tunnel.sget(sec, auth_key(d["id"])) or "")
            if u and _AUTH.match(a) and u not in seen:
                seen.add(u)
                out.append((d, u, a))
        return out

    def requirements(self, runner):
        from kaskad.gw import xray
        ver = getattr(xray, "XRAY_VERSION", "")
        ok = _version(ver) >= MIN_XRAY
        return {"kind": self.kind, "engine": self.engine, "packages": [], "modules": [],
                "ports": [{"proto": "udp", "port": PORTS[0]}], "supported": ok,
                "why": "" if ok else tr("gw_in_hy2.err.xray_old", need=".".join(str(x) for x in MIN_XRAY),
                                        have=ver or "?"),
                "needs": [XRAY_UNIT]}

    def prepare(self, st, sec, runner=None):
        cfg = self.cfg(st)
        done = []
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = self.default_port()
            done.append(trs("gw_in.made.port", port=cfg["port"]))
        for k, v in self.defaults(st).items():
            cfg.setdefault(k, v)
        if cfg.get("hop") is not None and hop_bounds(cfg.get("hop")) is None:
            cfg["hop"] = None                               # a broken hand edit: no range rather than no xray
        if tunnel.sget(sec, OBFS_KEY) is None:
            tunnel.sput(sec, OBFS_KEY, new_secret())
            done.append(trs("gw_in_hy2.made.obfs"))
        if not tunnel.sget(sec, SELF_KEY) or not tunnel.sget(sec, SELF_CRT):
            if runner is not None:
                try:
                    h = tunnel.host(st)
                except InboundError:
                    h = None
                tunnel.ensure_dir(self.dir())
                key, crt = pki.self_signed(runner, self.dir(), h or "kaskad-gw", h)
                tunnel.sput(sec, SELF_KEY, key)
                tunnel.sput(sec, SELF_CRT, crt)
                done.append(trs("gw_in.made.server_cert"))
        crt = tunnel.sget(sec, SELF_CRT)
        if crt:
            try:
                pin = pin_of(crt)
            except Exception:                               # noqa: BLE001 — a broken pair: made anew next time
                pin = ""
            if cfg.get("pin") != pin:
                cfg["pin"] = pin
        mode = self.want_mode(st)
        if cfg.get("cert_mode") != mode:
            cfg["cert_mode"] = mode
        for d in tunnel.devices(st, self.kind):
            if not tunnel.login(d):
                tunnel.log_tr(self.kind, "gw_in.log.skipped_login", device=d["id"])
                continue
            if not _AUTH.match(str(tunnel.sget(sec, auth_key(d["id"])) or "")):
                tunnel.sput(sec, auth_key(d["id"]), new_secret())
                done.append(trs("gw_in_hy2.made.auth", device=d["id"]))
        return done

    def rekey(self, st, sec, runner):
        """A new self-signed pair (``gw hy2 rekey``): every link given out with the old pin stops connecting."""
        cfg = self.cfg(st)
        try:
            h = tunnel.host(st)
        except InboundError:
            h = None
        tunnel.ensure_dir(self.dir())
        key, crt = pki.self_signed(runner, self.dir(), h or "kaskad-gw", h)
        tunnel.sput(sec, SELF_KEY, key)
        tunnel.sput(sec, SELF_CRT, crt)
        cfg["pin"] = pin_of(crt)
        tunnel.log_tr(self.kind, "gw_in_hy2.log.rekey")
        return [tr("gw_in.made.server_cert")]

    # ── for the gateway xray ──
    def cert_paths(self, st):
        return domain_files() if self.cert_mode(st) == "domain" else self_files()

    def listen_port(self, st):
        """``port`` of the xray inbound: the port, or «443,20000-20099» with the range (xray 26.3.27 takes it)."""
        p, h = self.port(st), self.hop(st)
        return ("%d,%s" % (p, h)) if h else p

    def xray_inbounds(self, st, sec):
        """The inbound while Hysteria2 is on — with no client too (the first device comes through the API)."""
        if not tunnel.wanted(st, self.kind):
            return []
        if self.cert_mode(st) == "self" and not (tunnel.sget(sec, SELF_KEY) and tunnel.sget(sec, SELF_CRT)):
            return []                                       # no pair yet (prepare made none): no inbound
        from kaskad.site import nginx
        clients = [{"auth": a, "level": 0, "email": u} for _, u, a in sorted(self.accounts(st, sec), key=lambda x: x[1])]
        chain, key = self.cert_paths(st)
        fm = {"quicParams": {"congestion": self.congestion(st)}}
        pw = tunnel.sget(sec, OBFS_KEY)
        if self.obfs(st) and pw:
            fm = {"udp": [{"type": "salamander", "settings": {"password": pw}}], "quicParams": fm["quicParams"]}
        return [{
            "tag": TAG,
            "listen": "::" if nginx.has_ipv6() else "0.0.0.0",
            "port": self.listen_port(st),
            "protocol": "hysteria",
            "settings": {"version": 2, "clients": clients},
            "streamSettings": {"network": "hysteria", "hysteriaSettings": {"version": 2}, "security": "tls",
                               "tlsSettings": {"alpn": [ALPN],
                                               "certificates": [{"certificateFile": chain, "keyFile": key}]},
                               "finalmask": fm},
        }]

    def xray_routing(self, st, sec, target=None):
        """One chain per user, as VLESS: its wave-3 rules (``user_chain`` — [] with the shared lists of stage 1), then
        ``inboundTag [gw-hy2], user [<login>.h2] -> target(route, device)``; every rule tagged
        ``h2-<login>-<hash of the chain>-<n>``. No closing ``-> block``: what no chain routes goes to xray's first
        outbound, ``block``."""
        if not tunnel.wanted(st, self.kind) or not self.xray_inbounds(st, sec):
            return []
        from kaskad.gw.inbounds.vless import route_target
        target = target or route_target
        from kaskad.rules import apply as rules_apply
        man, ok = rules_apply.manifest(), rules_apply.ready()
        rules = []
        for d, u, _ in sorted(self.accounts(st, sec), key=lambda a: a[1]):
            chain = rules_apply.user_chain(st, target, [TAG], u, d, man=man, is_ready=ok)
            last = {"type": "field", "inboundTag": [TAG], "user": [u]}
            last.update(target(tunnel.route_of(st, d), d))
            chain.append(last)
            for rule, tag in zip(chain, _chain_tag(tunnel.login(d), chain)):
                rule["ruleTag"] = tag
                rules.append(rule)
        return rules

    def xray_account(self, client):
        """hysteria.account.Account{auth = 1} of one client — what gRPC ``AlterInbound`` adds (gw/xraygrpc.py)."""
        a = str((client or {}).get("auth") or "")
        return (ACCOUNT_TYPE, _field(1, a)) if a else None

    # ── interface of §3 ──
    def render(self, st, sec):
        acc = self.accounts(st, sec)
        info = {"port": self.port(st), "hop": self.hop(st), "obfs": self.obfs(st), "cert": self.cert_mode(st),
                "accounts": len(acc), "users": dict((u, d["id"]) for d, u, _ in acc)}
        return {self.state_path(): (json.dumps(info, sort_keys=True) + "\n", 0o600)}

    def install(self, runner):
        tunnel.ensure_dir(self.dir())
        tunnel.log_tr(self.kind, "gw_in.log.installed_xray")

    def uninstall(self, runner):
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        sync_tls(None)
        try:
            os.unlink(self.traffic_path())
        except OSError:
            pass
        tunnel.log_tr(self.kind, "gw_in.log.removed")

    def ufw_want(self, st):
        """[(port, "udp")] + the range as ONE rule («20000:20099», udp) — never a call of ``ufw`` per port."""
        if not tunnel.wanted(st, self.kind):
            return []
        out = [(self.port(st), "udp")]
        b = hop_bounds(self.cfg(st).get("hop"))
        if b:
            out.append(("%d:%d" % b, "udp"))
        return out

    def apply(self, st, sec, runner):
        self.cfg(st)
        prepared = self.prepare(st, sec, runner)
        tunnel.ensure_dir(self.dir())
        sync_tls(self.cert_mode(st), sec)
        changed = tunnel.write_files(self.render(st, sec))
        want = self.ufw_want(st)
        tunnel.sync_ufw(runner, self.dir(), self.kind, want)
        if changed or prepared:
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_hy2.log.applied", devices=len(self.accounts(st, sec)),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
        return {"action": "xray" if want else "off", "changed": [os.path.basename(p) for p in changed],
                "prepared": prepared}

    def status(self, runner):
        """Running = the gateway xray serves the inbound and its UDP port listens on the node (from outside — not
        checked: UDP cannot be checked without a client). Sessions — the devices whose counters grew."""
        info = tunnel.read_json(self.state_path(), None)
        installed = isinstance(info, dict)
        idle = installed and not info.get("accounts")
        port = info.get("port") if installed else None
        active = installed and tunnel.is_active(runner, XRAY_UNIT)
        listens = _listens_udp(runner, port) if active and isinstance(port, int) else None
        running = bool(active and listens is not False)
        out = {"kind": self.kind, "installed": installed, "running": running, "port": port, "sessions": [],
               "units": [XRAY_UNIT]}
        if installed:
            out.update(hop=info.get("hop"), obfs=info.get("obfs"), cert=info.get("cert"))
        warnings = []
        if installed and not active:
            warnings.append(tr("gw_in_hy2.warn.xray_down"))
        elif installed and listens is False:
            warnings.append(tr("gw_in_hy2.warn.not_listening", port=port))
        if installed and info.get("cert") == "self":
            warnings.append(tr("gw_in_hy2.warn.self_cert"))
        if idle:
            out["idle"] = True
            warnings.append(tr("gw_in_hy2.warn.idle"))
        if warnings:
            out["warnings"] = warnings
        if running and not idle:
            users = info.get("users") if isinstance(info.get("users"), dict) else {}
            got = self.traffic(runner, users)
            if got is not None:
                out["sessions"], out["rx"], out["tx"] = got
        return out

    # ── issuance ──
    def address(self, st):
        """The address in the links: the domain with its certificate, else gateway.host."""
        if self.cert_mode(st) == "domain":
            from kaskad.site import model as site_model
            d = site_model.domain_of(st)
            if d:
                return d
        return tunnel.host(st)

    def link(self, st, sec, device, auth):
        host = self.address(st)
        h = ("[%s]" % host) if ":" in host else host
        q = lambda v: quote(str(v), safe="")      # noqa: E731
        hop = self.hop(st)
        ports = "%d,%s" % (self.port(st), hop) if hop else "%d" % self.port(st)
        params = [("sni", host)]
        pw = tunnel.sget(sec, OBFS_KEY)
        if self.obfs(st) and pw:
            params += [("obfs", "salamander"), ("obfs-password", pw)]
        if self.cert_mode(st) == "self":
            params += [("insecure", "1"), ("pinSHA256", self.cfg(st).get("pin") or "")]
        query = "&".join("%s=%s" % (k, q(v)) for k, v in params)
        name = issue_os.app_label("Hysteria2", device.get("name") if isinstance(device, dict) else None)
        return "hysteria2://%s@%s:%s/?%s#%s" % (q(auth), h, ports, query, q(name))

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        found = [a for a in self.accounts(st, sec) if a[0].get("id") == dev.get("id")]
        if not found:
            raise InboundError(tr("gw_in_hy2.err.not_user", device=tunnel.device_name(dev)))
        _, _, auth = found[0]
        url = self.link(st, sec, dev, auth)
        p = issue_os.platform_of(dev, os)
        apps = issue_os.hy2_apps_note(p) if hasattr(issue_os, "hy2_apps_note") else ""
        self_cert = self.cert_mode(st) == "self"
        notes = [apps or tr("gw_in_hy2.apps"), tr("gw_in_hy2.link_private"), tr("gw_in_hy2.udp_note")]
        if self_cert:
            notes.append(tr("gw_in_hy2.self_note"))
        link = tunnel.record("link", tr("gw_in.rec.link", title="Hysteria2"), url, None, notes)
        qr = tunnel.record("qr", tr("gw_in_hy2.rec.qr"), [url], None, [tr("gw_in_hy2.qr_note")] +
                           ([tr("gw_in_hy2.self_note")] if self_cert else []))
        host = self.address(st)
        rows = [[tr("gw_in.label.server"), host], [tr("gw_in.label.port"), str(self.port(st))]]
        if self.hop(st):
            rows.append([tr("gw_in_hy2.label.hop"), self.hop(st)])
        rows += [[tr("gw_in.label.password"), auth], ["SNI", host], ["ALPN", ALPN]]
        pw = tunnel.sget(sec, OBFS_KEY)
        if self.obfs(st) and pw:
            rows += [[tr("gw_in_hy2.label.obfs"), "salamander"], [tr("gw_in_hy2.label.obfs_pw"), pw]]
        else:
            rows.append([tr("gw_in_hy2.label.obfs"), tr("gw_in_hy2.off")])
        if self_cert:
            rows += [[tr("gw_in_hy2.label.insecure"), tr("gw_in_hy2.yes")],
                     [tr("gw_in_hy2.label.pin"), self.cfg(st).get("pin") or ""]]
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="Hysteria2"), rows, None,
                               [tr("gw_in_hy2.route_note")])
        recs = [qr, link, fields] if p in ("ios", "android") else [link, qr, fields]
        return tunnel.result(self.kind, recs)


def _listens_udp(runner, port):
    """Does something listen on UDP ``port`` (``ss -Hlnup``)? None — not known (no ss, ss failed). ss of one kind
    prints no Netid column — parse_ss is told the kind (KT3-4: «does not work» at a live Hysteria2)."""
    if not runner.which("ss"):
        return None
    res = runner.run(["ss", "-Hlnup"])
    if not res.ok:
        return None
    from kaskad import conflicts
    return any(s["proto"] == "udp" and s["port"] == port for s in conflicts.parse_ss(res.out, proto="udp"))


registry.register(Hy2Inbound)
