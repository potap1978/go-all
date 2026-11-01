"""Settings of the web panel and the status of the running one (docs/k3/SPEC-wave3.md §3.1).

/etc/kaskad/panel/config.json (0600; ``kaskadctl panel …`` and the panel's own settings write it):

    {"schema": 1, "require_password": false,
     "public": {"on": false, "port": 41234, "bind": "10.0.0.5", "host": "203.0.113.10", "cert_host": "203.0.113.10"},
     "proxy": {"on": false, "path": "app-<86 знаков>", "domain": "vpn.example.com"}}

    require_password  the SSH tunnel asks the password too (no admin by loopback alone)
    public            the open mode: HTTPS on ``bind``:``port`` (``bind`` — the source address of the default
                      route: the node's own address, the private one on 1:1 NAT clouds), people type
                      ``https://<host>:<port>/`` (``host`` — gateway.host, else the public address of the node)
    proxy             the panel behind nginx (docs/k3/SPEC-wave4.md §3): a listener of its own, the unix socket
                      /run/kaskad/panel/proxy.sock (0660, group www-data) where the tunnel's admin never is;
                      ``path`` — the secret prefix the panel lives under (``https://<domain>/<path>/``; never in
                      the journal), ``domain`` — the name allowed in Host/X-Forwarded-Host and Origin (besides
                      site.domain of state.json); without any domain only the loopback names pass

/run/kaskad/panel/status.json (0600, written by the running panel at start, on reload and when the number of
sessions changes): {"pid", "started", "listen": [{"kind", "addr", "scheme", "error"?}], "public", "url",
"fingerprint", "sessions", "updated"} — ``kaskadctl panel status`` reads it (sessions live in the panel's memory).

/etc/kaskad/panel/access.json (0600; the contract with the user page, WP-U) — how people reach this panel:
{"public": bool, "port": int, "scheme": "http"|"https", "host": str|null}: the open mode — its port, "https" and
the address people type; otherwise the local port, "http" and null (only through the SSH tunnel). Written
atomically when it changes: at every start and reload of the panel and by every change of the open mode.

The node certificate of the open mode is the one of kaskad.gw.tls (secrets ``tls.server.key``/``tls.server.crt``,
RSA 2048, CN and SAN = the address): ``ensure_cert`` makes it when missing; SSTP takes the same one over later.
With a domain whose certificate of Let's Encrypt is valid (kaskad.site, docs/k3/SPEC-wave4.md §4.1) the open mode
serves that one instead (``panel_cert``) and its addresses — ``public_url``, ``access.json`` ``host`` — name the
domain: ``https://<домен>:<порт>/`` (443 is not printed).
"""

import copy
import fcntl
import ipaddress
import json
import os
import random
import re
import socket
import ssl
import time

from .. import paths, secrets, state, util
from ..errors import USAGE, ConflictError, KaskadError
from ..i18n import tr

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 1980
PUBLIC_PORTS = (30000, 60999)
UNIT = "kaskad-panel.service"
UFW_COMMENT = "kaskad-panel"
DEFAULTS = {"schema": 1, "require_password": False,
            "public": {"on": False, "port": 0, "bind": "", "host": "", "cert_host": ""},
            "proxy": {"on": False, "path": "", "domain": ""}}
RUN_DIR_MODE = 0o710                # /run/kaskad/panel: the group of the proxy socket (www-data) may pass through
PROXY_GROUP = "www-data"
PROXY_SOCK_MODE = 0o660
RELOAD_WAIT = 15.0
RELOAD_POLL = 0.2
# the secret prefix of the panel behind nginx: «app-» + 86 characters of base64url (512 bits), as goAmnezia; by hand
# any 6–160 characters of [A-Za-z0-9_-]
PROXY_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,159}$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")

_rng = random.SystemRandom()
_sleep = time.sleep
_mono = time.monotonic


def config_path():
    return os.path.join(paths.panel_dir(), "config.json")


def status_path():
    return os.path.join(paths.panel_run_dir(), "status.json")


def appearance_path():
    return os.path.join(paths.panel_dir(), "appearance.json")


def intro_path():
    return os.path.join(paths.panel_dir(), "intro.json")       # «Знакомство с панелью» пройдено (3.3.2, №14)


def access_path():
    return os.path.join(paths.panel_dir(), "access.json")


def proxy_sock_path():
    return os.path.join(paths.panel_run_dir(), "proxy.sock")


# keys of the catalog (3.3.2: the language may change at any moment — translated where the answer is built)
FIX_RUN_DIR = "panel.conf.fixRunDir"                # {dir}
FIX_RESTART = "panel.conf.fixRestart"


def fix_restart():
    """The text of FIX_RESTART in the current language: the ``fix`` of most proxy_access_problem() answers."""
    return tr(FIX_RESTART)


def _proxy_gid():
    try:
        import grp
        return grp.getgrnam(PROXY_GROUP).gr_gid
    except (ImportError, KeyError):
        return None


def _answers(sock, timeout=1.0):
    """The panel listens on its socket (a stale file of a fallen panel refuses the connection)."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        return True
    except OSError:
        return False
    finally:
        s.close()


def proxy_access_problem():
    """Why nginx (the group www-data) cannot reach the panel through its socket, or None: {"why", "fix"}. /run/kaskad
    must let others through (3.2.21: the lock of the pool and the bot made it 0700 — the panel on the domain answered
    502), panel/ — its group or others, the socket — read and write for its group (or others), and the panel must
    answer on it."""
    run, pdir, sock = paths.run_dir(), paths.panel_run_dir(), proxy_sock_path()
    gid = _proxy_gid()

    def can(st, need):                              # need: 1 — pass (x), 6 — read and write
        m = st.st_mode & 0o777
        if gid is not None and st.st_gid == gid:
            return (m >> 3) & need == need
        return m & need == need
    try:
        st = os.stat(run)
    except OSError:
        return {"why": tr("panel.conf.noRunDir", dir=run), "fix": fix_restart()}
    if st.st_mode & 0o001 != 0o001:
        return {"why": tr("panel.conf.runDirClosed", dir=run, mode="%04o" % (st.st_mode & 0o7777)),
                "fix": tr(FIX_RUN_DIR, dir=run)}
    try:
        if not can(os.stat(pdir), 1):
            return {"why": tr("panel.conf.dirClosed", dir=pdir), "fix": fix_restart()}
        st = os.stat(sock)
    except OSError:
        return {"why": tr("panel.conf.noSocket", sock=sock), "fix": fix_restart()}
    if not can(st, 6):
        return {"why": tr("panel.conf.socketClosed", sock=sock), "fix": fix_restart()}
    if not _answers(sock):
        return {"why": tr("panel.conf.socketSilent", sock=sock), "fix": fix_restart()}
    return None


def pages_domain(cfg):
    """The domain whose site (nginx on 443, ``site mode site``) passes the pages of people /p/ to the panel: the
    listener for nginx is on for it and the site of state.json is on with the same domain; "" else. 3.3.7.5 (owner
    20.10): then every link of people (pages, subscriptions, QR, the bot, the menu — all from ``access.json``) is
    built on it; the port of the open mode only without it (the old links on the port open while that is on)."""
    prx = (cfg or {}).get("proxy") or {}
    d = str(prx.get("domain") or "").strip().lower()
    if not prx.get("on") or not d:
        return ""
    try:
        from ..site import model as smodel
        s = smodel.peek(state.load())
    except (KaskadError, ImportError, OSError, ValueError):
        return ""
    return d if s["mode"] == "site" and s["domain"].strip().lower().rstrip(".") == d else ""


def access_doc(cfg, local_port=LOCAL_PORT):
    """Where the links of people point (``userpage.panel_access``): the domain of the site (443) when it serves /p/
    (``pages_domain``), else the open mode, else nothing public."""
    pub = cfg["public"]
    d = pages_domain(cfg)
    if d:
        return {"public": True, "port": 443, "scheme": "https", "host": d}
    if pub["on"]:
        return {"public": True, "port": pub["port"], "scheme": "https", "host": public_host(cfg) or None}
    return {"public": False, "port": local_port, "scheme": "http", "host": None}


def write_access(cfg=None, local_port=LOCAL_PORT):
    """access.json for the user page; written only when it differs. -> True if written. The document is made and
    written under one lock (3.3.7.5 П-4): a reload that read state.json before the site was saved cannot put the port
    back over the domain a moment later — whoever writes last made its document last."""
    util.ensure_dir(paths.panel_dir(), 0o700)
    fd = os.open(access_path() + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        cfg = load() if cfg is None else cfg
        text = json.dumps(access_doc(cfg, local_port), sort_keys=True) + "\n"
        if util.read_text(access_path()) == text:
            return False
        util.atomic_write(access_path(), text, 0o600)
        return True
    finally:
        os.close(fd)


def service_state(runner):
    """The short systemd state needed before sending SIGHUP to the panel."""
    res = runner.run(["systemctl", "is-active", UNIT])
    state = res.out.strip()
    return state if state else ("active" if res.ok else "inactive")


def reload_service(runner, wait=RELOAD_WAIT):
    """Reload a ready panel, but never HUP its Python process while it is starting.

    ``Type=notify`` keeps systemd in ``activating`` until ``panel serve`` has listeners and a SIGHUP handler.  A
    simultaneous ``panel public on`` therefore waits for READY=1 instead of killing that process.  An actually down
    panel is started: its new configuration is read during start, no reload is needed.
    """
    status = service_state(runner)
    until = _mono() + max(0.0, wait)
    while status == "activating" and _mono() < until:
        _sleep(RELOAD_POLL)
        status = service_state(runner)
    if status == "active":
        return runner.run(["systemctl", "reload", UNIT]).ok
    if status != "activating":
        return runner.run(["systemctl", "start", UNIT]).ok
    # The start job is still in progress after the bounded wait.  Starting it again cannot make its listeners ready
    # sooner and, importantly, a HUP here is the race this helper prevents.
    return False


def layout_path():
    return os.path.join(paths.panel_dir(), "layout.json")


def _port(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 65535 else 0


def load():
    """The settings with every key present (a broken file — the defaults: local only, no open mode)."""
    raw = util.read_text(config_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    out = copy.deepcopy(DEFAULTS)
    out["require_password"] = doc.get("require_password") is True
    pub = doc.get("public") if isinstance(doc.get("public"), dict) else {}
    out["public"]["on"] = pub.get("on") is True
    out["public"]["port"] = _port(pub.get("port"))
    for k in ("bind", "host", "cert_host"):
        v = pub.get(k)
        out["public"][k] = v if isinstance(v, str) and re.match(r"^[A-Za-z0-9.:-]{0,253}$", v) else ""
    if out["public"]["on"] and not (out["public"]["port"] and out["public"]["bind"]):
        out["public"]["on"] = False                 # an open mode without an address is no open mode
    prx = doc.get("proxy") if isinstance(doc.get("proxy"), dict) else {}
    out["proxy"]["on"] = prx.get("on") is True
    v = prx.get("path")
    out["proxy"]["path"] = v if isinstance(v, str) and PROXY_PATH_RE.match(v) else ""
    v = prx.get("domain")
    v = v.strip().lower().rstrip(".") if isinstance(v, str) else ""
    out["proxy"]["domain"] = v if DOMAIN_RE.match(v) else ""
    return out


def save(cfg):
    util.ensure_dir(paths.panel_dir(), 0o700)
    util.atomic_write(config_path(), util.json_text(cfg), 0o600)


def write_status(doc):
    util.ensure_dir(paths.panel_run_dir(), RUN_DIR_MODE)
    util.atomic_write(status_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", 0o600)


def read_status():
    raw = util.read_text(status_path())
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    return doc if isinstance(doc, dict) else None


def host_for_url(host):
    return "[%s]" % host if ":" in host else host


def local_url(port=LOCAL_PORT):
    return "http://%s:%d/" % (LOCAL_HOST, port)


def public_host(cfg):
    """The name people type for the open mode: the domain when its certificate is valid (docs/k3/SPEC-wave4.md
    §4.1 п. 3 — the panel and the pages /p/ move to it by themselves), else ``host`` (gateway.host), else ``bind``."""
    pub = (cfg or {}).get("public") or {}
    got = site_cert()
    if got is not None:
        return got[0]
    return pub.get("host") or pub.get("bind") or ""


def public_origin(cfg):
    """https://<домен|адрес>:<порт>/ of the open mode (443 is not printed), None when it is off: the origin of the
    pages of people (/p/…) and of the quick setup — NOT the panel's address (3.3.7.5: there the panel is under the
    secret prefix too, ``public_url``)."""
    pub = (cfg or {}).get("public") or {}
    if not pub.get("on") or not pub.get("port"):
        return None
    h = host_for_url(public_host(cfg))
    return "https://%s/" % h if pub["port"] == 443 else "https://%s:%d/" % (h, pub["port"])


def public_url(cfg):
    """https://<домен|адрес>:<порт>/<секрет>/ — the panel in the open mode (3.3.7.5: under the one secret prefix of
    the panel, the same as behind nginx; the bare port answers as an empty site). None when the open mode is off or
    the secret is not made yet (the running panel makes it: ``ensure_secret``)."""
    origin = public_origin(cfg)
    base = proxy_base(cfg)
    return origin + base[1:] + "/" if origin and base else None


def public_url_masked(cfg):
    """``public_url`` with the secret masked (app-AbCd••••WxYz) — what the menu, the CLI and the bot show."""
    origin = public_origin(cfg)
    path = ((cfg or {}).get("proxy") or {}).get("path") or ""
    return origin + mask_path(path) + "/" if origin and path else None


def ensure_secret(cfg=None):
    """The one secret prefix of the panel exists (made and saved when missing): the open mode is never served
    without it (3.3.7.5). -> the settings."""
    cfg = load() if cfg is None else cfg
    if not cfg["proxy"]["path"]:
        cfg["proxy"]["path"] = new_proxy_path()
        save(cfg)
        util.log_tr("journal.panel.secret_made")
    return cfg


def rotate_secret():
    """A new secret prefix of the panel without nginx (no site on the domain: the open mode only; with the site
    ``kaskad.site.ops.rotate_panel_path`` changes nginx too). The old one stops at the panel's reload. -> settings."""
    cfg = load()
    cfg["proxy"]["path"] = new_proxy_path()
    save(cfg)
    util.log_tr("journal.panel.secret_rotated")
    return cfg


def proxy_base(cfg):
    """The prefix of the panel behind nginx: "/<path>", or "" (the panel at the root of the site)."""
    path = ((cfg or {}).get("proxy") or {}).get("path") or ""
    return "/" + path if path else ""


def proxy_url(cfg):
    """https://<domain>/<path>/ — the panel behind nginx; None without the listener or a domain."""
    prx = (cfg or {}).get("proxy") or {}
    if not prx.get("on") or not prx.get("domain"):
        return None
    return "https://%s%s/" % (prx["domain"], proxy_base(cfg))


def mask_path(path):
    """The secret prefix as the menu shows it: app-AbCd••••WxYz."""
    path = str(path or "")
    if len(path) <= 16:
        return "••••" if path else ""
    return path[:8] + "••••" + path[-4:]


def new_proxy_path():
    """«app-» + 86 characters of base64url (64 random bytes)."""
    import secrets as _random
    return "app-" + _random.token_urlsafe(64)[:86]


def set_proxy(on, path=None, domain=None, new_path=False):
    """Switch the listener for nginx (the running panel follows on reload). ``path`` — the secret prefix (a new
    random one when there is none yet or with ``new_path``), ``domain`` — the name nginx serves ("" clears it).
    The path never goes to the journal. -> the new settings."""
    cfg = load()
    prx = cfg["proxy"]
    if path is not None:
        path = str(path).strip().strip("/")
        if not PROXY_PATH_RE.match(path):
            raise KaskadError(tr("panel.conf.badProxyPath"), code=USAGE)
        prx["path"] = path
    elif new_path or (on and not prx["path"]):
        prx["path"] = new_proxy_path()
    if domain is not None:
        d = str(domain).strip().lower().rstrip(".")
        if d and not DOMAIN_RE.match(d):
            raise KaskadError(tr("panel.conf.badDomain"), code=USAGE)
        prx["domain"] = d
    prx["on"] = bool(on)
    save(cfg)
    if not on:
        util.log_tr("journal.panel.listener_off")
    elif prx["domain"]:
        util.log_tr("journal.panel.listener_on_domain", domain=prx["domain"])
    else:
        util.log_tr("journal.panel.listener_on")
    return cfg


def tunnel_command(port=LOCAL_PORT):
    """What the owner runs on his computer to reach the panel over SSH."""
    st = None
    try:
        st = state.load()
    except KaskadError:
        pass
    host = str((((st or {}).get("gateway") or {}).get("host")) or "") or tr("panel.conf.nodeAddress")
    return "ssh -L %d:%s:%d root@%s" % (port, LOCAL_HOST, port, host)


# ── the node certificate (kaskad.gw.tls) ───────────────────────────────────

def node_cert(sec=None):
    """(certificate PEM, key PEM) of the node or (None, None)."""
    from ..gw import tls
    from ..gw.inbounds import tunnel
    if sec is None:
        try:
            sec = secrets.load()
        except KaskadError:
            return None, None
    return tunnel.sget(sec, tls.CRT), tunnel.sget(sec, tls.KEY)


def site_cert():
    """(domain, certificate PEM, key PEM) of the domain of the node when its certificate is valid for it (kaskad.site:
    the copy /etc/kaskad/site/tls/current), else None — then the panel keeps the node certificate."""
    try:
        from ..site import model as smodel
        st = state.load()
        if smodel.active(st) is None:
            return None
        crt, key = util.read_text(smodel.fullchain_path()), util.read_text(smodel.privkey_path())
    except (KaskadError, ImportError, OSError):
        return None
    if not crt or not key:
        return None
    return smodel.domain_of(st), crt, key


def prev_site_cert():
    """(domain, certificate PEM, key PEM) of the domain a change has just replaced (site «prev»: ops.change_domain)
    while its grace lasts and its certificate of Let's Encrypt (left on disk) is still valid for it — the open mode
    answers that name with it, so a wizard opened on the old domain learns the result of the change and moves to the
    new one instead of losing the server (T-M6-qs330d №1); else None."""
    try:
        from ..site import certinfo
        from ..site import model as smodel
        s = smodel.peek(state.load())
        prev = s["prev"]
        if not prev or prev["domain"] == s["domain"] or prev["until"] <= time.time():
            return None
        live = smodel.live_dir(prev["domain"])
        crt = util.read_text(os.path.join(live, "fullchain.pem"))
        key = util.read_text(os.path.join(live, "privkey.pem"))
        if not crt or not key:
            return None
        info = certinfo.parse(crt)
        if not certinfo.covers(info, prev["domain"]) or certinfo.expired(info):
            return None
    except (KaskadError, ImportError, OSError, ValueError):
        return None
    return prev["domain"], crt, key


def ip_cert(cfg=None):
    """(certificate PEM, key PEM) of Let's Encrypt for the IP address people type (kaskad.ipcert, the quick setup:
    docs/k3/SPEC-setup.md §1) while it is valid and real, else None."""
    try:
        from ..ipcert import store as ipstore
        pub = (cfg if cfg is not None else load())["public"]
        ip = ipstore.norm_ip(pub.get("cert_host") or pub.get("host") or pub.get("bind"))
        return ipstore.serving_pair(ip) if ip else None
    except (KaskadError, ImportError, OSError, KeyError):
        return None


def panel_cert():
    """(certificate PEM, key PEM) the open mode serves: the domain's when it is valid, else Let's Encrypt's for the
    IP address when it is valid, else the node's."""
    got = site_cert()
    if got is not None:
        return got[1], got[2]
    got = ip_cert()
    if got is not None:
        return got
    return node_cert()


def sni_ip_cert(cfg=None):
    """(certificate PEM, key PEM) the open mode serves next to the domain's one — for a client that names no host
    (no SNI) or an IP address (https://<IP>:<порт>/: the wizard opened before the domain, old links of /p/): the
    domain's certificate never covers an address. None while there is no valid domain certificate (``panel_cert``
    is the IP one already) or no valid IP certificate (then everybody gets ``panel_cert``, as before)."""
    if site_cert() is None:
        return None
    return ip_cert(cfg)


SERVED_TIMEOUT = 2.0               # s: the handshake of ``domain_served`` with the node itself


def _leaf_der(pem):
    """DER of the first certificate of a PEM chain (what a client sees as the peer certificate), b"" when none."""
    m = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", pem or "", re.S)
    try:
        return ssl.PEM_cert_to_DER_cert(m.group(0) + "\n") if m else b""
    except ValueError:
        return b""


def domain_served(cfg=None, timeout=SERVED_TIMEOUT):
    """Does the open mode answer the domain's name with the domain's certificate — what a browser going to
    https://<домен>:<порт>/ meets (T-M6-qs330b №4: the wizard moved there while the panel still served the IP one).
    A TLS handshake with the node itself: ``bind``:``port``, SNI = the domain, the peer certificate compared with the
    copy of the domain's. -> {"ready": bool, "reason": "" | "not_public" | "no_cert" | "stale" | "no_answer",
    "host": the domain or ""}: «stale» — the handshake passed with another certificate (the panel has not re-read
    it yet), «no_answer» — no handshake at all (nothing to compare: the caller decides)."""
    cfg = load() if cfg is None else cfg
    pub = cfg["public"]
    got = site_cert()
    host = got[0] if got is not None else ""
    if not pub.get("on") or not pub.get("port") or not pub.get("bind"):
        return {"ready": False, "reason": "not_public", "host": host}
    want = _leaf_der(got[1]) if got is not None else b""
    if not want:
        return {"ready": False, "reason": "no_cert", "host": host}
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                 # the certificate is compared byte for byte below
    try:
        with socket.create_connection((pub["bind"], pub["port"]), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True) or b""
    except (OSError, ValueError):
        return {"ready": False, "reason": "no_answer", "host": host}
    if der != want:
        return {"ready": False, "reason": "stale", "host": host}
    return {"ready": True, "reason": "", "host": host}


def fingerprint(sec=None):
    """SHA-256 of the certificate the open mode serves (``sec`` given: of the node certificate in it)."""
    from ..gw.inbounds import pki
    crt, _ = node_cert(sec) if sec is not None else panel_cert()
    if not crt:
        return ""
    try:
        return pki.sha256_fingerprint(crt)
    except (ValueError, IndexError):
        return ""


def ensure_cert(runner, host):
    """The node certificate for ``host`` (made when missing, the way SSTP makes it: kaskad.gw.tls.ensure under the
    state lock). An existing one stays — also when it was made for another address (a new address is a new
    fingerprint the owner would have to check again; SSTP renews it when gateway.host changes). -> fingerprint."""
    from ..gw import tls
    with state.locked():
        st = state.load()
        sec = secrets.load()
        if not tls.ready(sec):
            view = copy.deepcopy(st)
            g = view.setdefault("gateway", {})
            if not g.get("host") and host:
                g["host"] = host
            rec = {}
            tls.ensure(view, sec, runner, rec)
            sec.save()
            util.log_tr("journal.panel.node_cert", host=host) if host else util.log_tr("journal.panel.node_cert_nohost")
        return fingerprint(sec)


# ── the open mode ──────────────────────────────────────────────────────────

def detect_addresses(runner):
    """(bind, host): the source address of the default route (the panel listens there) and the address people
    type (gateway.host, else what the route or api.ipify.org says is public). Raises KaskadError without a route."""
    from ..gw import model, net
    res = runner.run(["ip", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", res.out if res.ok else "")
    if not m:
        raise KaskadError(tr("panel.conf.noExternalAddress"))
    bind = m.group(1)
    try:
        host = model.public_host(state.load())
    except KaskadError:
        host = ""
    if not host:
        host, _ = net.detect_host(runner)
    return bind, host or bind


def _inet_lines(text):
    """[(device, address)] of the IPv4 lines of ``ip -4 -o addr show`` (scope host — loopback — and link-local,
    multicast skipped)."""
    out = []
    for ln in (text or "").splitlines():
        a = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/\d+", ln)
        tok = ln.split()
        if not a or len(tok) < 2 or " scope host" in ln:
            continue
        try:
            ip = ipaddress.ip_address(a.group(1))
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local or ip.is_multicast:
            continue
        out.append((tok[1].rstrip(":").split("@", 1)[0], str(ip)))
    return out


def listen_addresses(runner, bind):
    """[bind] and the other IPv4 addresses of its interface — where the open mode and the pages of people listen
    (k3/SPEC-multi-ip.md §1.4): an extra address of the hoster sits on the same interface, and a domain with A-records
    on two addresses of the node must open through either. Only the interface of the default route: the gateway's own
    interfaces (kgw-*, 10.77.x.1), Docker and the other internal networks never get the panel. ``bind`` is on another
    interface of the machine (a test, a strange setup) or ``ip`` fails — [bind] alone.

    PLAN-multi-ip Ш2: the addresses are read again each time (Л6) — a ``bind`` gone from the machine (the hoster took
    it away) gives its place to the source of the default route and the rest of its interface, instead of a listener
    that never starts; an address of another interface (a private network of the hoster, a second interface) only
    when it was added by hand (``gw addresses add``, «Адреса сервера» — REV З4), after the rest."""
    out = [bind] if bind else []
    if not bind:
        return out
    try:
        res = runner.run(["ip", "-4", "route", "get", "1.1.1.1"])
        m = re.search(r"\bdev (\S+)", res.out if res.ok else "")
        if not m:
            return out
        src = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", res.out)
        res = runner.run(["ip", "-4", "-o", "addr", "show", "dev", m.group(1)])
    except (OSError, KaskadError):
        return out
    found = [ip for _, ip in _inet_lines(res.out if res.ok else "")]
    every = None
    if bind in found:
        out = out + [x for x in found if x not in out]
    else:
        every = _all_inet(runner)
        if every is None or bind in [ip for _, ip in every]:
            return _chosen_only(out + [x for x in _manual_listen(every) if x not in out])
        first = src.group(1) if src and src.group(1) in found else (found[0] if found else "")
        if not first:
            return out
        out = [first] + [x for x in found if x != first]
    return _chosen_only(out + [x for x in _manual_listen(every, runner) if x not in out])


def _chosen_only(addrs):
    """PLAN-multi-ip Ш3: the inbounds take connections only on the chosen addresses (``gateway.addresses.in``) — the
    open mode and the pages of people too («известные локальные адреса ∩ in»; the panel does it itself: it works with
    the gateway off). Nothing of the list here (none chosen, all gone, no cache) — every address as before: the panel
    never closes completely."""
    try:
        from ..gw import mip
        allowed = mip.in_locals(state.load())
    except Exception:                                   # noqa: BLE001 — never a reason not to listen
        return addrs
    if not allowed:
        return addrs
    return [x for x in addrs if x in allowed] or addrs


def _all_inet(runner):
    """[(device, address)] of every IPv4 of the machine (``ip -4 -o addr show``, with the loopback), None on a
    failure."""
    try:
        res = runner.run(["ip", "-4", "-o", "addr", "show"])
    except (OSError, KaskadError):
        return None
    if not res.ok:
        return None
    out = []
    for ln in res.out.splitlines():
        a = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/\d+", ln)
        tok = ln.split()
        if a and len(tok) >= 2:
            out.append((tok[1].rstrip(":").split("@", 1)[0], a.group(1)))
    return out


def _manual_listen(every, runner=None):
    """The addresses added by hand that are on the machine (gw/addresses.listen_extra): never the gateway's own or a
    container network. ``every`` None — looked at with ``runner`` when the state has manual addresses at all."""
    try:
        from ..gw import addresses
        st = state.load()
        if not addresses.manual_list(st):
            return []
        if every is None and runner is not None:
            every = _all_inet(runner)
        here = [ip for dev, ip in every or [] if not addresses.skip_iface(dev)]
        return addresses.listen_extra(st, here)
    except Exception:                                   # noqa: BLE001 — never a reason not to listen on the rest
        return []


def _port_conflicts(runner, port):
    from ..gw import ops
    try:
        st = state.load()
    except KaskadError:
        st = state.empty()
    return ops.port_conflicts(st, runner, "panel", "tcp", port)


def choose_port(runner, port=None, force=False):
    """The port of the open mode: ``port`` (a conflict refuses unless ``force``) or a random free one of
    PUBLIC_PORTS (forwards, inbounds of the gateway, listeners, Docker are checked)."""
    if port is not None:
        p = state.parse_port(port)
        found = [] if force else _port_conflicts(runner, p)
        if found:
            raise ConflictError(tr("panel.conf.portBusy", port=p, what="; ".join(c["detail"] for c in found)),
                                data={"conflicts": found})
        return p
    for _ in range(40):
        p = _rng.randint(*PUBLIC_PORTS)
        if not _port_conflicts(runner, p):
            return p
    raise KaskadError(tr("panel.conf.noFreePort", lo=PUBLIC_PORTS[0], hi=PUBLIC_PORTS[1]))


def _ufw(runner, verb, port):
    """Open/close the port in UFW when UFW is active (our rule: comment kaskad-panel)."""
    from ..gw import sys as gsys
    if not port or not gsys.ufw_active(runner):
        return False
    if verb == "allow":
        return runner.run(["ufw", "allow", "%d/tcp" % port, "comment", UFW_COMMENT]).ok
    res = runner.run(["ufw", "status"])
    if not res.ok or not any(("%d/tcp" % port) in ln and UFW_COMMENT in ln for ln in res.out.splitlines()):
        return False
    return runner.run(["ufw", "delete", "allow", "%d/tcp" % port]).ok


def set_public(runner, on, port=None, force=False):
    """Switch the open mode (the running panel picks it up on reload). -> the new settings."""
    cfg = load()
    pub = cfg["public"]
    if on:
        bind, host = detect_addresses(runner)
        want = port if port is not None else (pub.get("port") or None)
        # the port the panel had keeps (the running panel itself listens there: no conflict check)
        reuse = want is not None and pub.get("port") and state.parse_port(want) == pub["port"]
        p = choose_port(runner, want, force=bool(force or reuse))
        fp = ensure_cert(runner, host)
        if pub.get("port") and pub["port"] != p:
            _ufw(runner, "delete", pub["port"])
        _ufw(runner, "allow", p)
        cfg["public"] = {"on": True, "port": p, "bind": bind, "host": host, "cert_host": host}
        if not cfg["proxy"]["path"]:
            cfg["proxy"]["path"] = new_proxy_path()     # 3.3.7.5: the open mode only under the secret prefix
        save(cfg)
        util.log_tr("journal.panel.public_on", host=host_for_url(host), port=int(p), fp=fp[:23] + "…" if fp else "—")
    else:
        _ufw(runner, "delete", pub.get("port"))
        cfg["public"] = dict(pub, on=False)
        save(cfg)
        util.log_tr("journal.panel.public_off")
    write_access(cfg)
    return cfg


def login_url(token, cfg=None, local_port=LOCAL_PORT):
    """The URL of a one-time link: the panel behind nginx (domain and path) when it is on, else the open mode,
    else the SSH tunnel's local address."""
    cfg = load() if cfg is None else cfg
    return (proxy_url(cfg) or public_url(cfg) or local_url(local_port)) + "login?t=" + token


def set_require_password(on):
    from . import auth
    cfg = load()
    if on and not auth.password_set():
        raise KaskadError(tr("panel.conf.setPasswordFirst"))
    cfg["require_password"] = bool(on)
    save(cfg)
    return cfg
