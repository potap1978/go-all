"""VLESS inbound (``vless``): no daemon of its own — an inbound of the gateway xray (kaskad/gw/xray.py, service
kaskad-gw-xray.service), like SOCKS5 (docs/k3/SPEC-vless-inbound.md). Two modes («маскировка», as goVLESS Pro):

    reality   only an IP (or a domain without the site): VLESS + Reality + ``xtls-rprx-vision``, the node's X25519 key,
              one shortId, a cover site by the country of the server (vless_lists: Russia — Russian sites, else
              international ones; checked from the node — TLS 1.3), or the node's own site while it is on 443 and
              VLESS is not
    tls       the domain with its site (``site mode site``, a live certificate of Let's Encrypt): VLESS + TLS on 443
              with the certificate of the domain, ``rejectUnknownSni``, ALPN http/1.1, ``xtls-rprx-vision``; everything
              that is not VLESS goes to the site through xray's fallback (nginx behind xray on a Unix socket with the
              PROXY protocol — site/front.py). The renewed certificate: the copy for xray (/opt/kaskad/gw/tls) is
              replaced by the deploy hook (site.consumers.reload) and xray re-reads it by itself (the hourly hot reload of
              the certificate files, no restart; no ``ocspStapling`` — Let's Encrypt has no OCSP server any more)

    xray_inbounds(st, sec)          -> [inbound]  a client per device with vless in its access — its own UUID, email
                                       = the device login; no sniffing; present while the inbound is on, with no
                                       client too: the first device comes without a restart
    xray_routing(st, sec, target)   -> [rule]     per user, one chain each (the wave-3 rules of the user, then its
                                       route), every rule tagged ``vl-<login>-<hash>-<n>``: the core adds and removes
                                       users and their chains through xray's API (HandlerService, RoutingService) —
                                       no restart (gw/xray.py ``hot_plan``); nothing matched — xray's first outbound,
                                       ``block``
    xray_stats = True, xray_hot = True

State record ``gateway.inbounds.vless``: ``port`` (tcp), ``security`` ("reality" | "tls"), ``sni`` / ``dest`` / ``mask``
(list | own | custom) / ``mask_check`` (the cover site of Reality and its last TLS 1.3 check), ``mask_list`` ("ru" |
"int"), ``country`` ({"code", "source"}), ``short_id``, ``pub``, ``fp``. Secrets: ``vless.server.private`` (the node's
private key) and ``dev.<id>.vless.uuid`` (one per device). Nothing secret goes to argv or to the log. The mode follows
the site — ``site.front.sync``: TLS with the site, Reality without it; a switch lists whom to give the links again.

status(): running = the gateway xray serves the inbound (no device yet — idle, not broken). Sessions come from xray's
per-user traffic counters (``xray api statsquery``): a device whose counters grew within ACTIVE seconds is online
(/run/kaskad/gw/vless-traffic.json keeps the previous counters between calls; a restart of xray zeroes the counters —
a smaller value starts again from it).

Issuance: Reality — ``vless://<uuid>@<host>:<port>?…&security=reality&pbk=…&sid=…&sni=…&fp=chrome&flow=…#name``;
TLS — ``vless://<uuid>@<domain>:443?…&security=tls&sni=<domain>&alpn=http/1.1&fp=chrome&flow=…#name``; the same link
as a QR code and the fields for manual entry (v2RayTun, Hiddify, Streisand, v2rayN, NekoBox, v2rayNG).
"""

import base64
import hashlib
import json
import os
import random
import re
import secrets as _secrets
import shutil
import socket
import ssl
import time
import uuid
from urllib.parse import quote

from kaskad import paths, util
from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import tunnel, vless_lists
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.i18n import retr, tr, trs
from kaskad.vendor import awgkeys

TAG = "gw-vless"
XRAY_UNIT = "kaskad-gw-xray.service"
FLOW = "xtls-rprx-vision"
FINGERPRINT = "chrome"
SERVER_KEY = "vless.server.private"
STATS_PORT = 10854                  # xray's StatsService of the gateway (gw/xray.py STATS_PORT — the same number)
ACTIVE = 120                        # s: a device whose counters grew this recently is online
TRAFFIC_FILE = "vless-traffic.json"
PORTS = (443, 8443)                 # the port of a new inbound: 443 when free and no cover site of ours holds it
CHECK_TIMEOUT = 5.0                 # s per site of the TLS 1.3 check
PICK_TRIES = 8                      # sites of the list checked from the node when one is picked automatically
SECURITIES = ("reality", "tls")
ALPN_TLS = "http/1.1"               # VLESS + TLS: ALPN of the inbound and of the links (as goVLESS Pro over TCP)
RULE_PREFIX = "vl-"                 # ruleTag of the per-user chains (gw/xray.py hot_plan removes them by it)
TLS_DIR = "/opt/kaskad/gw/tls"      # the copy of the domain's certificate xray of the gateway reads (its user may)
# Cover sites («маскировка»): by the country of the server (vless_lists, as goVLESS Pro). The node checks the chosen
# one (TLS 1.3 from here) before it is used: what answers from a test bench may not from a node.
MASKS = tuple((s, s) for s in vless_lists.INT[:8])      # compatibility: the first international sites
MASK_KINDS = ("list", "own", "custom")
_SHORT_ID = re.compile(r"^[0-9a-f]{2,16}$")
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}"
                   r"[A-Za-z0-9])?)+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_now = time.time                    # tests patch it
_rng = random.Random()              # tests seed it: the order sites of a list are tried in


def uuid_key(device_id):
    return "dev.%s.vless.uuid" % device_id


# ── keys ───────────────────────────────────────────────────────────────────

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _raw(key):
    """32 bytes of a base64url key without padding (xray's x25519 form); InboundError otherwise."""
    s = str(key or "").strip()
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (ValueError, TypeError):
        raw = b""
    if len(raw) != 32:
        raise InboundError(tr("gw_in_vless.err.bad_key"))
    return raw


def new_private():
    """A Reality private key: X25519 with clamping (as ``xray x25519``), base64url without padding."""
    return _b64url(base64.b64decode(awgkeys.genkey()))


def public_of(private):
    """The public key of a Reality private key (base64url without padding) — what the clients put into ``pbk``."""
    return _b64url(awgkeys.x25519_base(_raw(private)))


def new_short_id():
    return _secrets.token_hex(8)


def valid_host(name):
    """A host name (lower case) or "": a pasted link is fine — only its name is taken (https://site/path -> site)."""
    s = str(name or "").strip().lower()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].rstrip(".")
    if s.count(":") == 1:
        s = s.split(":", 1)[0]
    return s if _HOST.match(s) and not s.replace(".", "").isdigit() else ""


# ── the cover site («маскировка») ──────────────────────────────────────────

def check_tls13(host, port=443, timeout=CHECK_TIMEOUT):
    """Does ``host:port`` answer TLS 1.3 with a valid certificate for ``host``, from this node? Reality hands the
    connections that are not ours to it and copies its handshake: without TLS 1.3 the clients cannot connect at all.
    -> {"ok", "tls", "alpn", "h2", "why"} (``why`` — for people when not ok, "" else). No exception."""
    out = {"ok": False, "tls": "", "alpn": "", "h2": False, "why": ""}
    try:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.set_alpn_protocols(["h2", "http/1.1"])
    except (AttributeError, ValueError, ssl.SSLError) as e:
        out["why"] = tr("gw_in_vless.why.no_python_tls13", error=e)
        return out
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                out["tls"] = s.version() or ""
                out["alpn"] = s.selected_alpn_protocol() or ""
    except ssl.SSLCertVerificationError:
        out["why"] = tr("gw_in_vless.why.bad_cert")
        return out
    except ssl.SSLError:
        out["why"] = tr("gw_in_vless.why.no_tls13")
        return out
    except socket.timeout:
        out["why"] = tr("gw_in_vless.why.timeout", sec=int(timeout))
        return out
    except OSError as e:
        out["why"] = tr("gw_in_vless.why.unreachable", error=e.strerror or e.__class__.__name__)
        return out
    out["h2"] = out["alpn"] == "h2"
    out["ok"] = out["tls"] == "TLSv1.3"
    if not out["ok"]:
        out["why"] = tr("gw_in_vless.why.old_tls", tls=out["tls"] or tr("gw_in_vless.why.no_tls"))
    return out


_checker = {"fn": check_tls13}      # tests put a fake here


def checked(host, now=None):
    """The check of a cover site as the record keeps it: {"ok", "tls", "alpn", "h2", "why", "at"}."""
    got = dict(_checker["fn"](host))
    got["at"] = util.now_iso() if now is None else now
    return dict((k, got.get(k)) for k in ("ok", "tls", "alpn", "h2", "why", "at"))


def own_domain(st):
    """The domain of the node when its cover site of Kaskad serves 443 (``site mode site``): Reality may hand foreign
    connections to it — the node «borrows» its own site. "" otherwise."""
    try:
        from kaskad.site import model as site_model
        s = site_model.peek(st)
    except Exception:                               # noqa: BLE001 — no site part, an older state
        return ""
    return s["domain"] if s.get("mode") == "site" and s.get("domain") else ""


def country_of(st, detect=True):
    """{"code", "source"} of the server: the one the record keeps, else detected now (``detect``: network, before the
    lock), else unknown ("")."""
    cfg = tunnel.inbound_cfg(st, "vless") or {}
    c = cfg.get("country") if isinstance(cfg.get("country"), dict) else None
    if c and vless_lists._code(c.get("code")):
        return {"code": vless_lists._code(c.get("code")), "source": c.get("source") or ""}
    return vless_lists.country(st) if detect else {"code": "", "source": ""}


def masks(st, check=False, country=None):
    """The cover sites to choose from — the list by the country of the server (``country``: {"code", …}; None — the
    record's or detected), the node's own first when it has its site and VLESS is not on 443: [{"sni", "title",
    "mask", "check"}]; ``check`` — each one checked from here (TLS 1.3), else ``check`` None."""
    country = country_of(st) if country is None else country
    _, sites = vless_lists.list_of(country.get("code"))
    rows = []
    own = own_domain(st)
    cfg = tunnel.inbound_cfg(st, "vless") or {}
    if own and cfg.get("port") != 443:
        rows.append({"sni": own, "title": tr("gw_in_vless.own_site"), "mask": "own"})
    rows.extend({"sni": s, "title": s, "mask": "list"} for s in sites if s != own)
    got = check_many([r["sni"] for r in rows]) if check else {}
    for r in rows:
        r["check"] = got.get(r["sni"]) if check else None
    return rows


CHECK_WORKERS = 16                  # the whole list (≈100 sites) in about CHECK_TIMEOUT × 7 at worst


def check_many(hosts):
    """{host: checked(host)} — several at a time (each one is a TLS handshake of up to CHECK_TIMEOUT)."""
    from concurrent.futures import ThreadPoolExecutor
    hosts = list(dict.fromkeys(hosts))
    if not hosts:
        return {}
    with ThreadPoolExecutor(max_workers=min(CHECK_WORKERS, len(hosts))) as pool:
        return dict(zip(hosts, pool.map(checked, hosts)))


def _listed(host):
    return host in vless_lists.RU or host in vless_lists.INT


def pick_mask(st, country=None):
    """The cover site of Reality: the node's own site (on 443 while VLESS is not), else up to PICK_TRIES sites of the
    country's list in a random order (not every node behind the same site) — the first with TLS 1.3 and HTTP/2, else
    the first with TLS 1.3. None answers (no internet yet) — the first tried, with its failed check (the card says
    so). -> {"sni", "dest", "mask", "mask_check", "mask_list", "country"}."""
    country = country_of(st) if country is None else country
    lst, sites = vless_lists.list_of(country.get("code"))
    extra = {"mask_list": lst, "country": dict(country)}
    rows = masks(st, country=country)
    own = [r for r in rows if r["mask"] == "own"]
    if own:
        c = checked(own[0]["sni"])
        if c["ok"]:
            return dict(sni=own[0]["sni"], dest="%s:443" % own[0]["sni"], mask="own", mask_check=c, **extra)
    order = list(sites)
    _rng.shuffle(order)
    first = tls13 = None
    for host in order[:PICK_TRIES]:
        c = checked(host)
        rec = dict(sni=host, dest="%s:443" % host, mask="list", mask_check=c, **extra)
        first = first or rec
        if c["ok"] and c.get("h2"):
            return rec
        if c["ok"] and tls13 is None:
            tls13 = rec
    return tls13 or first


def mask_for_mode(st, value, check=True):
    """The cover of Reality for ``gw vless mode reality`` / the site going off (SPEC-vless-tls-default §3.5 T→R п.2):
    as ``mask_fields``, but VLESS may be on TLS now (it goes to Reality right after) and ``own`` is fine while VLESS
    holds 443 — the site keeps 443 and Reality steps off it."""
    return mask_fields(st, value, check=check, for_mode=True)


def mask_fields(st, value, check=True, for_mode=False):
    """``gw vless mask <value>``: ``own`` (the node's site), ``auto`` (pick_mask), a site of the lists or any host name
    (``custom``). Checked from here unless ``check`` is False; a site that fails the check is refused.
    -> {"sni", "dest", "mask", "mask_check", "mask_list", "country"}."""
    v = str(value or "").strip().lower()
    country = country_of(st)
    extra = {"mask_list": vless_lists.list_of(country.get("code"))[0], "country": dict(country)}
    cfg = tunnel.inbound_cfg(st, "vless") or {}
    if cfg.get("security") == "tls" and not for_mode:
        raise InboundError(tr("gw_in_vless.err.tls_mode"))
    if v == "auto":
        rec = pick_mask(st, country=country)
        if not rec["mask_check"]["ok"] and check:
            raise InboundError(tr("gw_in_vless.err.none_tls13", why=rec["mask_check"]["why"]),
                               data={"kind": "mask_check"})
        return rec
    if v == "own":
        host, kind = own_domain(st), "own"
        if not host:
            raise InboundError(tr("gw_in_vless.err.no_own_site"))
        if cfg.get("port") == 443 and not for_mode:
            raise InboundError(tr("gw_in_vless.err.own_on_443"))
    else:
        host = valid_host(v)
        if not host:
            raise InboundError(tr("gw_in_vless.err.bad_host", value=repr(value)))
        kind = "list" if _listed(host) else "custom"
    c = checked(host) if check else {"ok": None, "tls": "", "alpn": "", "h2": False, "why": tr("gw_in_vless.why.unchecked"),
                                     "at": util.now_iso()}
    if check and not c["ok"]:
        raise InboundError(tr("gw_in_vless.err.unfit", host=host, why=c["why"]), data={"kind": "mask_check"})
    return dict(sni=host, dest="%s:443" % host, mask=kind, mask_check=c, **extra)


# ── TLS: the certificate of the domain for xray ────────────────────────────

def tls_dir():
    return paths.P(TLS_DIR)


def tls_files():
    return os.path.join(tls_dir(), "fullchain.pem"), os.path.join(tls_dir(), "privkey.pem")


def sync_tls(want=True):
    """The copy of the domain's certificate xray of the gateway reads (its user may not read /etc/kaskad): the chain
    0644, the key 0640 root:kaskad-xray in a 0750 directory; replaced atomically when it differs (xray re-reads it
    within the hour). ``want`` False — the copy away. -> [names of the files that changed] ("removed" when the copy
    went). No exception (a log line)."""
    from kaskad.site import model as site_model
    d = tls_dir()
    if not want:
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            return ["removed"]
        return []
    src = (site_model.fullchain_path(), site_model.privkey_path())
    try:
        import grp
        gid = grp.getgrnam(tunnel_xray_user()).gr_gid
    except (KeyError, ImportError):
        gid = None
    changed = []
    try:
        util.ensure_dir(d, 0o750)
        if gid is not None:
            os.chown(d, 0, gid)
        for (s_path, mode), dst in zip(((src[0], 0o644), (src[1], 0o640)), tls_files()):
            with open(s_path, "rb") as f:
                data = f.read()
            try:
                with open(dst, "rb") as f:
                    same = f.read() == data
            except OSError:
                same = False
            if not same:
                util.atomic_write(dst, data, mode)
                changed.append(os.path.basename(dst))
            os.chmod(dst, mode)                         # the owner and the mode — every time (a hand-made copy too)
            if gid is not None:
                os.chown(dst, 0, gid)
    except OSError as e:
        tunnel.log_tr("vless", "gw_in_vless.log.tls_copy_failed", error=e)
        return []
    if changed:
        tunnel.log_tr("vless", "gw_in_vless.log.tls_copied")
    return changed


def tunnel_xray_user():
    from kaskad.gw import net
    return net.XRAY_USER


def _tls_mode(st):
    from kaskad.site import front
    return front.tls_wanted(st)


def _chain_tag(login, chain):
    h = hashlib.sha256(json.dumps(chain, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return ["%s%s-%s-%d" % (RULE_PREFIX, login, h, i) for i in range(len(chain))]


# ── the adapter ────────────────────────────────────────────────────────────

def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    s = str(v or "").strip()
    return int(s) if s.isdigit() else None


def _listens(runner, port):
    """Does something listen on TCP ``port`` (``ss -Hltnup``, as conflicts reads it)? None — not known (no ss, ss
    failed): the caller trusts the unit then."""
    if not runner.which("ss"):
        return None
    res = runner.run(["ss", "-Hltnup"])
    if not res.ok:
        return None
    from kaskad import conflicts
    return any(s["proto"] == "tcp" and s["port"] == port for s in conflicts.parse_ss(res.out))


class VlessInbound(Inbound):
    kind = "vless"
    engine = "xray"
    title = "VLESS"
    xray_stats = True
    xray_hot = True                   # users come and go through xray's API (gw/xray.py hot_plan), no restart
    xray_hot_prefix = RULE_PREFIX     # its chains (stage 1, §2.4: gw/xray.py collects the prefixes of the adapters)
    xray_add_user = "cli"             # a new user: ``xray api adu``

    def dir(self):
        return tunnel.kind_dir("vless")

    def state_path(self):
        return os.path.join(self.dir(), "vless.json")

    def traffic_path(self):
        return os.path.join(paths.gw_run_dir(), TRAFFIC_FILE)

    def default_port(self):
        return PORTS[0]

    def port_candidates(self, st):
        """Ports of a new inbound in the order to try (the core takes the first free one, else a random free one):
        443, then 8443. With the node's site on 443 (``site mode site``): 443 too when VLESS can go in front of it (TLS,
        site.front), else 8443 alone."""
        if own_domain(st) and not self._front_ready(st):
            return [PORTS[1]]
        return list(PORTS)

    @staticmethod
    def _front_ready(st):
        from kaskad.site import front
        ok, _ = front.site_ready(st)
        from kaskad.site import model as site_model
        return ok and os.path.isfile(site_model.fullchain_path()) and os.path.isfile(site_model.privkey_path())

    def port_shared(self, st, proto, port, conflict):
        """A conflict on ``port`` that is no conflict for VLESS: 443 of the node's own site when VLESS goes in front of
        it (xray on 443, nginx behind it — site.front)."""
        if proto != "tcp" or port != PORTS[0] or not own_domain(st) or not self._front_ready(st):
            return False
        return conflict.get("kind") == "site" or (conflict.get("kind") == "listener"
                                                   and "nginx" in str(conflict.get("detail") or "").lower())

    def pre_install(self, runner, st):
        """Before the lock (network): the mode and, for Reality, the cover site of a new inbound by the country of the
        server, checked from the node. -> (fields, notes)."""
        from kaskad.site import front
        country = vless_lists.country(st)
        lst = vless_lists.list_of(country.get("code"))[0]
        # a record made by this version wants TLS with the node's domain (SPEC-vless-tls-default §3.2): Reality now is
        # the fallback — no domain, no certificate yet, 443 not free, the site off (it goes on after the install)
        if own_domain(st) and self._front_ready(st) and not front._foreign_443(runner, st):
            d = own_domain(st)
            rec = {"security": "tls", "country": country, "mask_list": lst, "want": front.WANT_TLS}
            return rec, [tr("gw_in_vless.note.tls_mode", domain=d)]
        rec = pick_mask(st, country=country)
        rec.update(security="reality", want=front.WANT_TLS)
        notes = []
        c = rec["mask_check"]
        where = tr("gw_in_vless.note.country", code=country["code"] or tr("gw_in_vless.note.country_unknown"),
                   list=vless_lists.TITLES[lst])
        if c["ok"]:
            notes.append(tr("gw_in_vless.note.mask_ok", sni=rec["sni"], h2=", HTTP/2" if c.get("h2") else "", where=where))
        else:
            notes.append(tr("gw_in_vless.note.mask_bad", sni=rec["sni"], why=c.get("why") or tr("gw_in_vless.note.no_answer")))
        from kaskad.site import model as site_model
        if not site_model.domain_of(st):
            notes.append(tr("gw_in_vless.note.no_domain"))
        elif not front.site_ready(st, ignore_mode=True)[0]:
            notes.append(tr("gw_in_vless.note.wait_cert", domain=site_model.unicode(site_model.domain_of(st))))
        return rec, notes

    def ifaces(self):
        return []

    def addr_kinds(self, st=None):
        return []

    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st):
        return tunnel.port_value(self.cfg(st), "port", self.default_port())

    def security(self, st):
        """The mode the inbound runs in now: "tls" only while site.front allows it, else "reality"."""
        return "tls" if _tls_mode(st) else "reality"

    def sni(self, st):
        return valid_host(self.cfg(st).get("sni")) or MASKS[0][0]

    def reality_sni(self, st):
        """The cover site of Reality as xray gets it: the node's own site cannot cover VLESS sitting on 443 itself (it
        would hand foreign connections to itself) — the first international site then, until ``site.front.sync``
        picks one by the country."""
        host = self.sni(st)
        if self.port(st) == PORTS[0] and (self.cfg(st).get("mask") == "own" or host == own_domain(st)):
            return MASKS[0][0]
        return host

    def domain(self, st):
        from kaskad.site import model as site_model
        return site_model.domain_of(st)

    def dest(self, st):
        if self.reality_sni(st) != self.sni(st):
            return "%s:443" % self.reality_sni(st)
        d = str(self.cfg(st).get("dest") or "").strip()
        host, _, port = d.rpartition(":")
        if valid_host(host) and port.isdigit() and 1 <= int(port) <= 65535:
            return "%s:%d" % (valid_host(host), int(port))
        return "%s:443" % self.sni(st)

    def short_id(self, st):
        sid = str(self.cfg(st).get("short_id") or "").lower()
        return sid if _SHORT_ID.match(sid) and len(sid) % 2 == 0 else ""

    def fingerprint(self, st):
        fp = str(self.cfg(st).get("fp") or "")
        return fp if re.match(r"^[a-z0-9]{2,16}$", fp) else FINGERPRINT

    def accounts(self, st, sec):
        """[(device, login, uuid)] of the devices ready to connect."""
        out, seen = [], set()
        for d in tunnel.devices(st, self.kind):
            lg = tunnel.login(d)
            u = str(tunnel.sget(sec, uuid_key(d["id"])) or "").lower()
            if lg and _UUID.match(u) and lg not in seen:          # xray keys the users by email = login
                seen.add(lg)
                out.append((d, lg, u))
        return out

    def prepare(self, st, sec, runner=None):
        cfg = self.cfg(st)
        done = []
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = self.default_port()
            done.append(trs("gw_in.made.port", port=cfg["port"]))
        if not valid_host(cfg.get("sni")):
            cfg["sni"], cfg["dest"], cfg["mask"] = MASKS[0][0], "%s:443" % MASKS[0][0], "list"
            done.append(trs("gw_in_vless.made.mask", sni=cfg["sni"]))
        if not cfg.get("dest"):
            cfg["dest"] = "%s:443" % cfg["sni"]
        if cfg.get("mask") not in MASK_KINDS:
            cfg["mask"] = "list" if _listed(cfg["sni"]) else "custom"
        if cfg.get("security") not in SECURITIES:
            cfg["security"] = "reality"
        if not cfg.get("fp"):
            cfg["fp"] = FINGERPRINT
        priv = tunnel.sget(sec, SERVER_KEY)
        if priv is None:
            priv = new_private()
            tunnel.sput(sec, SERVER_KEY, priv)
            done.append(trs("gw_in_vless.made.key"))
        pub = public_of(priv)
        if cfg.get("pub") != pub:
            cfg["pub"] = pub
        if not self.short_id(st):
            cfg["short_id"] = new_short_id()
            done.append("shortId")
        for d in tunnel.devices(st, self.kind):
            if not tunnel.login(d):
                tunnel.log_tr(self.kind, "gw_in.log.skipped_login", device=d["id"])
                continue
            u = str(tunnel.sget(sec, uuid_key(d["id"])) or "").lower()
            if not _UUID.match(u):
                tunnel.sput(sec, uuid_key(d["id"]), str(uuid.uuid4()))
                done.append("UUID VLESS %s" % d["id"])
        return done

    def rekey(self, st, sec):
        """New key and shortId of the node (``gw vless rekey``): every issued link stops connecting. -> created."""
        cfg = self.cfg(st)
        tunnel.sput(sec, SERVER_KEY, new_private())
        cfg["pub"] = public_of(tunnel.sget(sec, SERVER_KEY))
        cfg["short_id"] = new_short_id()
        tunnel.log_tr(self.kind, "gw_in_vless.log.rekey")
        return [tr("gw_in_vless.made.key"), "shortId"]

    # ── for the gateway xray ──
    def xray_inbounds(self, st, sec):
        """The inbound while VLESS is on — with no client too (the first device comes through the API, no restart):
        TLS with the domain's certificate and the fallback to the site (site.front) or Reality."""
        if not tunnel.wanted(st, self.kind):
            return []
        acc = self.accounts(st, sec)
        clients = [{"id": u, "email": lg, "flow": FLOW} for _, lg, u in sorted(acc, key=lambda a: a[1])]
        if self.security(st) == "tls":
            from kaskad.site import front, nginx
            chain, key = tls_files()
            d = self.domain(st)
            return [{
                "tag": TAG,
                "listen": "::" if nginx.has_ipv6() else "0.0.0.0",
                "port": PORTS[0],
                "protocol": "vless",
                "settings": {"clients": clients, "decryption": "none",
                             "fallbacks": [{"dest": front.sock_path(), "xver": 1}]},
                "streamSettings": {"network": "tcp", "security": "tls",
                                   # F4: «только 1.3» closes TLS 1.2 to the site behind VLESS (Vision is on 1.3)
                                   "tlsSettings": {"serverName": d, "rejectUnknownSni": True, "minVersion":
                                                   "1.3" if tunnel.tls_versions(tunnel.inbound_cfg(st, self.kind))
                                                   == "1.3" else "1.2",
                                                   "alpn": [ALPN_TLS],
                                                   # no ``ocspStapling``: Let's Encrypt has no OCSP any more (a
                                                   # warning at every start); xray re-reads the files hourly anyway
                                                   "certificates": [{"certificateFile": chain, "keyFile": key}]}},
            }]
        priv = tunnel.sget(sec, SERVER_KEY)
        sid = self.short_id(st)
        if not priv or not sid:
            return []
        from kaskad.gw import addresses
        from kaskad.site import nginx
        # 3.3.7.15 F13 (С-5): IPv6 — by the written «принимать и выдавать» of an IPv6 only, never by the cache of the
        # machine (its flicker would restart xray for everyone); no IPv6 given — the config of 3.3.7.14 byte for byte
        six = addresses.give6_of(st) is not None and nginx.has_ipv6()
        return [{
            "tag": TAG,
            "listen": "::" if six else "0.0.0.0",
            "port": self.port(st),
            "protocol": "vless",
            "settings": {"clients": clients, "decryption": "none"},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"show": False, "dest": self.dest(st), "xver": 0,
                                                   "serverNames": [self.reality_sni(st)], "privateKey": priv,
                                                   "shortIds": [sid]}},
        }]

    def xray_routing(self, st, sec, target=None):
        """One chain per user — its wave-3 rules (``rules.apply.user_chain``), then its route — every rule tagged
        ``vl-<login>-<hash of the chain>-<n>``: a changed chain is a new set of tags, the core swaps it live. The chains
        of different users never meet (each names its own user), so their order in xray does not matter; within one
        chain it does — it is replaced whole. Nothing matched — xray's first outbound, ``block`` (gw/xray.py). Stage 1
        (§2.4): the lists are shared rules ``ls-`` of the core — the chain is the route rule alone (``user_chain`` -> [])."""
        if not tunnel.wanted(st, self.kind) or not self.xray_inbounds(st, sec):
            return []
        target = target or route_target
        from kaskad.rules import apply as rules_apply
        man, ok = rules_apply.manifest(), rules_apply.ready()
        rules = []
        for d, lg, _ in sorted(self.accounts(st, sec), key=lambda a: a[1]):
            chain = rules_apply.user_chain(st, target, [TAG], lg, d, man=man, is_ready=ok)
            last = {"type": "field", "inboundTag": [TAG], "user": [lg]}
            last.update(target(tunnel.route_of(st, d), d))
            chain.append(last)
            for rule, tag in zip(chain, _chain_tag(lg, chain)):
                rule["ruleTag"] = tag
                rules.append(rule)
        return rules

    # ── interface of §3 ──
    def render(self, st, sec):
        acc = self.accounts(st, sec)
        info = {"port": self.port(st), "sni": self.sni(st), "accounts": len(acc), "security": self.security(st),
                "users": dict((lg, d["id"]) for d, lg, _ in acc)}
        return {self.state_path(): (json.dumps(info, sort_keys=True) + "\n", 0o600)}

    def requirements(self, runner):
        return {"kind": self.kind, "engine": self.engine, "packages": [], "modules": [],
                "ports": [{"proto": "tcp", "port": PORTS[0]}], "supported": True, "why": "", "needs": [XRAY_UNIT]}

    def install(self, runner):
        tunnel.ensure_dir(self.dir())
        tunnel.log_tr(self.kind, "gw_in.log.installed_xray")

    def uninstall(self, runner):
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        sync_tls(False)
        try:
            os.unlink(self.traffic_path())
        except OSError:
            pass
        tunnel.log_tr(self.kind, "gw_in.log.removed")

    def apply(self, st, sec, runner):
        self.cfg(st)
        prepared = self.prepare(st, sec, runner)
        tunnel.ensure_dir(self.dir())
        sync_tls(self.security(st) == "tls")             # the copy of the domain's certificate xray reads (TLS mode)
        changed = tunnel.write_files(self.render(st, sec))
        want = [(self.port(st), "tcp")] if tunnel.wanted(st, self.kind) else []
        tunnel.sync_ufw(runner, self.dir(), self.kind, want)
        if changed or prepared:
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_vless.log.applied", devices=len(self.accounts(st, sec)),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
        return {"action": "xray" if want else "off", "changed": [os.path.basename(p) for p in changed],
                "prepared": prepared}

    def status(self, runner):
        """Running = the gateway xray serves the inbound; with no device yet it is idle, not broken. Sessions —
        the devices whose traffic counters grew within ACTIVE seconds."""
        info = tunnel.read_json(self.state_path(), None)
        installed = isinstance(info, dict)
        idle = installed and not info.get("accounts")
        port = _int(info.get("port")) if installed else None
        active = installed and tunnel.is_active(runner, XRAY_UNIT)
        # by the fact (T-M6-qs330b №1): xray of the gateway runs AND the port of VLESS listens — not by state.json
        listens = _listens(runner, port) if active and port else None
        running = bool(active and listens is not False)
        out = {"kind": self.kind, "installed": installed, "running": running,
               "port": info.get("port") if installed else None, "sessions": [], "units": [XRAY_UNIT]}
        warnings = []
        if installed and not active:
            warnings.append(tr("gw_in_vless.warn.xray_down"))
        elif installed and listens is False:
            warnings.append(tr("gw_in_vless.warn.not_listening", port=port))
        if idle:
            out["idle"] = True
            warnings.append(tr("gw_in_vless.warn.idle"))
        if warnings:
            out["warnings"] = warnings
        if running and not idle:
            users = info.get("users") if isinstance(info.get("users"), dict) else {}
            got = self.traffic(runner, users)
            if got is not None:
                out["sessions"], out["rx"], out["tx"] = got
        return out

    # ── traffic and sessions (xray's StatsService) ──
    def counters(self, runner):
        """{login: (uplink, downlink)} of xray's per-user counters, None when xray does not answer."""
        xray = os.path.join(paths.opt(), "gw", "xray")
        if not os.path.exists(xray):
            return None
        res = runner.run([xray, "api", "statsquery", "--server=127.0.0.1:%d" % STATS_PORT, "-timeout", "3",
                          "-pattern", "user>>>"], timeout=6)
        if not res.ok:
            return None
        try:
            doc = json.loads(res.out or "{}")
        except ValueError:
            return None
        out = {}
        for s in (doc.get("stat") or []) if isinstance(doc, dict) else []:
            parts = str((s or {}).get("name") or "").split(">>>")
            if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
                continue
            v = _int((s or {}).get("value", 0)) or 0                # protobuf JSON: an int64 as a string, 0 omitted
            up, down = out.get(parts[1], (0, 0))
            out[parts[1]] = (up + v, down) if parts[3] == "uplink" else (up, down + v)
        return out

    def traffic(self, runner, users, now=None):
        """(sessions, rx, tx) from the counters: rx — what the clients sent (uplink), tx — what they got. The
        previous counters are kept in /run (a growth within ACTIVE s = online; ``since`` — the first growth after a
        pause, ``last`` — the last growth: only it proves a connection after a change of the port, gw/reissue.fresh).
        None when xray does not answer."""
        now = _now() if now is None else now
        got = self.counters(runner)
        if got is None:
            return None
        prev = tunnel.read_json(self.traffic_path(), {})
        prev = prev if isinstance(prev, dict) else {}
        keep, sessions = {}, []
        rx = tx = 0
        for lg, (up, down) in sorted(got.items()):
            if lg not in users:
                continue                                   # SOCKS5 accounts share the counters of xray
            rx, tx = rx + up, tx + down
            p = prev.get(lg) if isinstance(prev.get(lg), dict) else {}
            pu, pd = _int(p.get("up")), _int(p.get("down"))
            if pu is None or pd is None or up < pu or down < pd:
                grew = up > 0 or down > 0                      # first sight or xray restarted: counters from 0
                pu = pd = 0
            else:
                grew = up > pu or down > pd
            last = p.get("active") if isinstance(p.get("active"), (int, float)) else None
            since = p.get("since") if isinstance(p.get("since"), (int, float)) else None
            if grew:
                if last is None or now - last > ACTIVE or since is None:
                    since = now
                last = now
            keep[lg] = {"up": up, "down": down, "active": last, "since": since}
            if last is not None and now - last <= ACTIVE:
                sessions.append({"device": users[lg], "ip": None, "since": int(since or now), "last": int(last),
                                 "rx": up, "tx": down})
        try:
            util.ensure_dir(os.path.dirname(self.traffic_path()), 0o700)
            util.atomic_write(self.traffic_path(), json.dumps(keep, sort_keys=True) + "\n", 0o600)
        except OSError:
            pass
        return sessions, rx, tx

    # ── issuance ──
    def address(self, st):
        """The address in the links: the domain in TLS mode (its certificate names it), else gateway.host."""
        return self.domain(st) if self.security(st) == "tls" else tunnel.host(st)

    def link(self, st, device, uid):
        host, port = self.address(st), self.port(st)
        h = ("[%s]" % host) if ":" in host else host
        q = lambda v: quote(str(v), safe="")      # noqa: E731
        if self.security(st) == "tls":
            params = (("encryption", "none"), ("flow", FLOW), ("security", "tls"), ("sni", host), ("alpn", ALPN_TLS),
                      ("fp", self.fingerprint(st)), ("type", "tcp"), ("headerType", "none"))
        else:
            params = (("encryption", "none"), ("flow", FLOW), ("security", "reality"), ("sni", self.reality_sni(st)),
                      ("fp", self.fingerprint(st)), ("pbk", self.cfg(st).get("pub") or ""), ("sid", self.short_id(st)),
                      ("type", "tcp"), ("headerType", "none"))
        query = "&".join("%s=%s" % (k, q(v)) for k, v in params)
        # the name in the apps and in the subscription: brand · protocol · device (owner, T-M6-332c №9)
        name = issue_os.app_label("VLESS", device.get("name") if isinstance(device, dict) else None)
        return "vless://%s@%s:%d?%s#%s" % (uid, h, port, query, q(name))

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        found = [a for a in self.accounts(st, sec) if a[0].get("id") == dev.get("id")]
        tls = self.security(st) == "tls"
        if not found or (not tls and (not self.cfg(st).get("pub") or not self.short_id(st))):
            raise InboundError(tr("gw_in_vless.err.not_user", device=tunnel.device_name(dev)))
        _, _, uid = found[0]
        url = self.link(st, dev, uid)
        p = issue_os.platform_of(dev, os)
        apps = issue_os.vless_apps_note(p)
        what = "VLESS TLS" if tls else "VLESS Reality"
        link = tunnel.record("link", tr("gw_in.rec.link", title=what), url, None,
                             [apps or tr("gw_in_vless.apps"), tr("gw_in_vless.link_private")])
        qr = tunnel.record("qr", tr("gw_in_vless.rec.qr", title=what), [url], None, [tr("gw_in_vless.qr_note")])
        addr_l, port_l, enc_l = tr("gw_in_vless.label.address"), tr("gw_in.label.port"), tr("gw_in.label.encryption")
        tr_l, sec_l = tr("gw_in_vless.label.transport"), tr("gw_in_vless.label.security")
        if tls:
            rows = [[addr_l, self.address(st)], [port_l, str(self.port(st))], ["UUID", uid], ["Flow", FLOW],
                    [enc_l, "none"], [tr_l, "tcp"], [sec_l, "tls"], ["SNI", self.address(st)],
                    ["ALPN", ALPN_TLS], ["Fingerprint", self.fingerprint(st)]]
        else:
            rows = [[addr_l, tunnel.host(st)], [port_l, str(self.port(st))], ["UUID", uid], ["Flow", FLOW],
                    [enc_l, "none"], [tr_l, "tcp"], [sec_l, "reality"],
                    ["SNI", self.reality_sni(st)], ["Fingerprint", self.fingerprint(st)],
                    [tr("gw_in_vless.label.pbk"), self.cfg(st).get("pub") or ""], ["shortId (sid)", self.short_id(st)]]
        fields = tunnel.record("fields", tr("gw_in_vless.rec.manual", title=what), rows, None,
                               [tr("gw_in_vless.route_note")])
        recs = [qr, link, fields] if p in ("ios", "android") else [link, qr, fields]
        return tunnel.result(self.kind, recs)


def route_target(route, device=None):
    """Route ({"via": …, "fail": …} or "pool"/"direct"/"block"/"exit:<id>") -> xray rule target (the core passes its
    own ``target(route, device)`` with the pins and the fail policy; this one is for the adapter alone)."""
    via = route.get("via") if isinstance(route, dict) else route
    via = str(via or "pool")
    if via == "pool":
        return {"balancerTag": "pool"}
    if via in ("direct", "block"):
        return {"outboundTag": via}
    if via.startswith("exit:") and len(via) > 5:
        return {"outboundTag": "exit-" + via[5:]}
    return {"outboundTag": "block"}


registry.register(VlessInbound)
