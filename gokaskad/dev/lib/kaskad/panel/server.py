"""The HTTP server of the web panel (docs/k3/SPEC-wave3.md §3): ``kaskadctl panel serve`` (kaskad-panel.service).

One process, ``ThreadingHTTPServer`` (daemon threads, at most MAX_CONN connections at once), handlers of
``kaskad.panel.MODULES`` dispatched by ``kaskad.panel.routes``. Listeners:

    local   http://127.0.0.1:1980 — always; the SSH tunnel (``ssh -L 1980:127.0.0.1:1980 root@node``)
    public  https://<external address>:<30000–60999> — the open mode (conf.py): the node certificate
            (kaskad.gw.tls), TLS 1.2+, the handshake in the connection's own thread; with a domain certificate the
            certificate is chosen by SNI (``sni_callback``): the domain's name — the domain's one, no name or an IP
            address — the IP certificate while it is valid (conf.sni_ip_cert)
    proxy   unix:/run/kaskad/panel/proxy.sock (0660, group www-data) — the panel behind nginx (conf ``proxy``,
            docs/k3/SPEC-wave4.md §3): never the tunnel's admin; X-Real-IP, X-Forwarded-Proto, X-Forwarded-Host
            and X-Forwarded-Prefix are believed here and only here (``proxy_view``); the secret prefix is cut off
            the path, the session cookie gets ``Secure`` and ``Path=<prefix>/``, ``Location`` the prefix
            (``proxy_headers``); outside the prefix only the pages of people (/p/…) answer
    3.3.7.5: the public listener serves the panel only under the very same secret prefix (``public_view``): the
            root, /login, /api/…, the static files and any other path outside it — the plain 404 of an empty site
            (``_send_plain``: no Server, no headers of ours), as a wrong token of /p/ and a foreign Host there;
            the pages of people (/p/…) and the quick setup (/setup-<its secret>/) stay at the root

Every request:
    1. Host allow-list (DNS rebinding): the local listener answers only to 127.0.0.1 / localhost / [::1] (any
       port: ``ssh -L 8080:…`` works), the public one only to its own address and the configured host, the proxy
       one only to the domain (X-Forwarded-Host);
    2. the route — an unknown path gets ``not_found()`` (the user page's own 404 when it is there: the same
       answer as for an unknown path under /p/), a wrong method 405; the body (Content-Length only, one of it, at most
       MAX_BODY within BODY_S; chunked — 411);
    3. auth of the route: "admin" — a session (cookie) or the tunnel (auth.tunnel); "public" — nobody is asked
       (the login, static files, and the user page of WP-U with its own nonce); "page" — banned addresses (10
       misses in 5 min, auth.Misses) get what the page says to a link that opens nothing (``page_refused``), a 404
       of the handler counts as a miss;
    4. CSRF of admin mutations (POST/PUT/DELETE): the header ``X-Kaskad-Admin: 1`` (a page of another origin
       cannot send it without a CORS preflight nobody answers) — "public"/"page" routes keep their own CSRF;
       any POST/PUT/DELETE with an ``Origin`` of another origin or ``Sec-Fetch-Site: cross-site`` is refused;
    5. the handler; ``HttpError`` -> its JSON, ``KaskadError`` -> routes.core_error, anything else -> 500 «внутренняя
       ошибка» (the traceback, redacted, goes to the journal — never the body of the request);
    6. security headers on every answer (CSP, X-Frame-Options DENY, Referrer-Policy no-referrer, nosniff, …),
       ``Cache-Control: no-store`` unless the answer says otherwise. No access log.

Static files (lib/kaskad/panel/static, loaded once into memory, gzip made once): ``/``, ``/index.html``,
``/static/<file>``, ``/<file>.js|css|…``, ``/fonts/<file>.woff2`` (the fonts of the design system: tokens.css asks
for ``fonts/…`` next to itself), ``/pages/<file>.js`` (the sections app.js loads lazily). Every ``.html`` gets ``__KASKAD_V__`` replaced by ``<version>-<sha10>``
(sha256 of all static files); ``?v=`` equal to it -> ``Cache-Control: public, max-age=31536000, immutable``,
anything else -> ``no-cache`` with an ETag (304 on If-None-Match).

Mutations of the panel run heavy commands (apt-get, make, kaskadctl, the rebuild of the pool, nft with the networks of
Russia — kaskad.sysrun.heavy) through ``systemd-run --scope`` (ScopedRunner): outside the unit's CPUQuota and MemoryMax,
so an OOM kill never lands in the middle of dpkg — nor on the panel (T-M6-qs330b №3).
"""

import errno
import gzip
import hashlib
import hmac
import http.server
import importlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import stat
import ssl
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse

from .. import __version__, paths, sysrun, util
from ..errors import KaskadError
from . import MODULES, auth, conf, routes, sse
from . import lang as weblang
from ..i18n import tr

MAX_BODY = 1 << 20
CL_RE = re.compile(r"^[0-9]+$")            # Content-Length: ASCII digits only (str.isdigit takes «²» of Latin-1)
# A copy of the settings (kaskad.backup: up to FILE_MAX = 16 MB, in a JSON body) — 20 MB, only for its two routes and
# only for a request with a session (an unknown client never makes the panel read 20 MB). Behind nginx — the same limit
# for the same two paths (kaskad.site.nginx).
BACKUP_BODY_MAX = 20 << 20
BIG_BODY_PATHS = ("/api/backup/preview", "/api/backup/import")
# The quick setup opens the same copy on its stage 1 (kaskad.setup.steps «backup-open»): the same 20 MB, only with a
# live session of the wizard (kaskad.setup.web.has_session) under its secret path.
SETUP_BIG_BODY_PATHS = ("/api/setup/step/backup-open",)
SOCKET_TIMEOUT = 20
BODY_S = 20                 # the whole body of ≤ MAX_BODY (any request, a login or not) — one deadline, not one per read
HANDSHAKE_TIMEOUT = 10
MAX_CONN = 64
TLS_CHECK_EVERY = 5.0
ADDR_EVERY = 30             # s: the addresses of the node are looked at again (an extra IPv4 added or taken away)
SHUTDOWN_POLL = 1.0         # s: how often a listener looks whether it must stop (an idle wake-up, nothing else)
AFTER_S = 1.0               # s after the answer has left: ``Request.after`` runs — nginx and the xray in front of the
                            # site (the panel on the domain on 443) hand its last bytes to the browser first (П3)
AFTER_STOP_S = 30.0         # s ``Panel.stop`` lets what the answers owed finish (a restart of xray, П3) at most
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)
LOCAL_NAMES = ("127.0.0.1", "localhost", "::1")
STATIC_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "static")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8",
         ".woff2": "font/woff2", ".json": "application/json; charset=utf-8", ".png": "image/png",
         ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}
ZIP = (".html", ".js", ".css", ".svg", ".txt", ".json", ".webmanifest")
# keys of the catalog (3.3.2): translated in send_error, in the language of that moment
_ERROR_TEXTS = {400: "panel.server.http400", 404: "panel.server.http404", 405: "panel.server.http405",
                408: "panel.server.http408", 411: "panel.server.http411", 413: "panel.server.http413",
                414: "panel.server.http414", 431: "panel.server.http431", 500: "panel.server.http500",
                501: "panel.server.http501", 503: "panel.server.http503", 505: "panel.server.http505"}


def log(text):
    """A line to the journal (stderr of the unit): no request data, secrets redacted."""
    try:
        sys.stderr.write("kaskad-panel: %s\n" % util.redact(text))
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def log_t(key, **kw):
    """A catalog line to the journal in the language of the server (saved KASKAD_LANG, util.journal_lang), not of the
    request or the environment of the unit (T-67-332 №7: «настройки перечитаны» with the server in English)."""
    with util.journal_lang():
        text = tr(key, **kw)
    log(text)


# ── static files ───────────────────────────────────────────────────────────

class Static(object):
    """The files of lib/kaskad/panel/static in memory: name -> (body, gzipped body or None, type, ETag)."""

    def __init__(self, root=STATIC_DIR):
        self.root = root
        self.entries = {}
        self.version = __version__
        self.load()

    def load(self):
        files = {}
        if os.path.isdir(self.root):
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d != "__pycache__")
                for fn in sorted(filenames):
                    ext = os.path.splitext(fn)[1].lower()
                    if fn.startswith(".") or ext not in TYPES:
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, self.root).replace(os.sep, "/")
                    try:
                        with open(full, "rb") as f:
                            files[rel] = f.read()
                    except OSError:
                        continue
        h = hashlib.sha256()
        for rel in sorted(files):
            h.update(rel.encode("utf-8") + b"\0" + files[rel] + b"\0")
        self.version = "%s-%s" % (__version__, h.hexdigest()[:10])
        entries = {}
        for rel, data in files.items():
            ext = os.path.splitext(rel)[1].lower()
            if ext == ".html":
                data = data.replace(b"__KASKAD_V__", self.version.encode("ascii"))
            gz = None
            if ext in ZIP and len(data) > 256:
                gz = gzip.compress(data, 9, mtime=0)
                if len(gz) >= len(data):
                    gz = None
            entries[rel] = (data, gz, TYPES[ext], '"%s"' % hashlib.sha256(data).hexdigest()[:20])
        self.entries = entries
        return self


STATIC = Static()


def _accepts_gzip(value):
    """Accept-Encoding allows gzip (``gzip``, ``x-gzip`` or ``*`` with q > 0)."""
    for part in (value or "").lower().split(","):
        bits = [b.strip() for b in part.split(";")]
        if bits[0] not in ("gzip", "x-gzip", "*"):
            continue
        q = 1.0
        for b in bits[1:]:
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        return q > 0
    return False


def static_response(req, name):
    e = STATIC.entries.get(name)
    if e is None:
        raise routes.HttpError(404, tr("panel.server.noSuchFile"))
    data, gz, ctype, etag = e
    if name.endswith(".html") and b"__KASKAD_LANG__" in data:
        # the language of the server in <html lang> (3.3.2): the first frame is already in it; it may change at any
        # moment (kaskadctl lang set, «Настройки»), so it is filled in at every answer and is part of the ETag
        lang = weblang.server_lang()
        data = data.replace(b"__KASKAD_LANG__", lang.encode("ascii"))
        gz = gzip.compress(data, 9, mtime=0) if gz is not None else None
        etag = etag[:-1] + "-" + lang + '"'
    h = {"Content-Type": ctype, "ETag": etag, "Vary": "Accept-Encoding"}
    if not name.endswith(".html") and req.query.get("v") == STATIC.version:
        h["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        h["Cache-Control"] = "no-cache"
    inm = req.headers.get("if-none-match")
    if inm and (inm.strip() == "*" or etag in [x.strip() for x in inm.split(",")]):
        return routes.Response(304, b"", h)
    if gz is not None and _accepts_gzip(req.headers.get("accept-encoding")):
        h["Content-Encoding"] = "gzip"
        data = gz
    return routes.Response(200, data, h)


_NAME = r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,80}"


@routes.route("GET", r"/(?:index\.html)?", auth="public")
def _index(req):
    return static_response(req, "index.html")


@routes.route("GET", r"/static/(?P<name>%s(?:/%s)?)" % (_NAME, _NAME), auth="public")
def _static(req):
    return static_response(req, req.match.group("name"))


@routes.route("GET", r"/(?P<name>%s\.(?:js|css|svg|txt|woff2|png|ico|json|webmanifest))" % _NAME, auth="public")
def _static_root(req):
    return static_response(req, req.match.group("name"))


@routes.route("GET", r"/fonts/(?P<name>%s\.woff2)" % _NAME, auth="public")
def _static_fonts(req):
    return static_response(req, "fonts/" + req.match.group("name"))


@routes.route("GET", r"/pages/(?P<name>%s\.js)" % _NAME, auth="public")
def _static_pages(req):
    # the sections of the panel app.js loads lazily (K.loadPage: pages/<id>.js and pages/<id>-i18n-*.js next to
    # index.html, under the secret prefix too); one flat name, no subfolders (UX-FOUNDATION §3.3, F0)
    return static_response(req, "pages/" + req.match.group("name"))


SETUP_PREFIX = "/setup-"                 # the quick setup (kaskad.setup.web): /setup-<secret>/…
_setup_mod = []
_setup_route = []


def _setup_web():
    if not _setup_mod:
        _setup_mod.append(importlib.import_module("kaskad.setup.web"))
    return _setup_mod[0]


def _setup_entry():
    """The route of «<base>/»: the one-time link (?t=) or the page of the wizard."""
    if not _setup_route:
        _setup_route.append(routes.Route("GET", "/", lambda req: _setup_web().entry(req, static_response), "public",
                                         False))
    return _setup_route[0]


def _setup_file():
    if len(_setup_route) < 2:
        _setup_entry()
        _setup_route.append(routes.Route("GET", "/", lambda req: static_response(req, "setup" + req.path), "public",
                                         False))
    return _setup_route[1]


USERPAGE = "kaskad.panel.userpage"
_userpage = []              # [module or None] once looked up


def _userpage_not_found(name="not_found"):
    if not _userpage:
        try:
            _userpage.append(importlib.import_module(USERPAGE))
        except ModuleNotFoundError as e:
            if e.name != USERPAGE:
                raise
            _userpage.append(None)
    fn = getattr(_userpage[0], name, None)
    return fn if callable(fn) else None


def page_refused(method, path, base=""):
    """The answer to a banned address on a route of the user page: what the page itself says to a link that opens
    nothing (``userpage.refused``: «Ссылка больше не действует» for GET of a link, the 404 of an empty site for the
    rest) — a ban tells nothing either. ``base`` — the prefix the page is under behind nginx."""
    fn = _userpage_not_found("refused")
    if fn is None:
        return not_found()
    try:
        return fn(method, path, base) if base else fn(method, path)
    except routes.HttpError as e:
        return routes.error_response(e)


def not_found():
    """The 404 of an unknown path (and of an address the user page banned): ``kaskad.panel.userpage.not_found()``
    when the user page is there — one and the same answer for an unknown token and an unknown path, nothing to
    enumerate — else our JSON 404."""
    fn = _userpage_not_found()
    if fn is not None:
        try:
            resp = fn()
        except routes.HttpError as e:
            return routes.error_response(e)
        if isinstance(resp, routes.Response):
            return resp
    return routes.error_response(routes.HttpError(404, tr("panel.server.notFound")))


def load_modules():
    """Import every module of kaskad.panel.MODULES (their routes register). A module that is not written yet
    is skipped; a broken import inside an existing one is a real error."""
    loaded = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as e:
            parent = name.rsplit(".", 1)[0]
            if e.name not in (name, parent):
                raise
            continue
        loaded.append(name)
    return loaded


# ── runner of mutations ────────────────────────────────────────────────────

# Heavy commands (apt-get, kaskadctl, gokaskad --egress-add|del|replace, nft with the networks of Russia, …) in a
# transient ``systemd-run --scope`` — outside the panel's MemoryMax; the rule is one, kaskad.sysrun.heavy.
ScopedRunner = sysrun.ScopedRunner


class CountingRunner(sysrun.Runner):
    """Counts the commands this panel runs (``GET /api/health`` -> "commands"; tools/panel_load.py): reads keep
    it still — only changes run commands."""

    def __init__(self, inner):
        self.inner = inner
        self.count = 0
        self._lock = threading.Lock()

    def which(self, name):
        return self.inner.which(name)

    def run(self, argv, input=None, check=False, timeout=60, env=None):
        with self._lock:
            self.count += 1
        if env:
            return self.inner.run(argv, input=input, check=check, timeout=timeout, env=env)
        return self.inner.run(argv, input=input, check=check, timeout=timeout)


# ── the request handler ────────────────────────────────────────────────────

class _HeadOnly(Exception):
    """A HEAD of a RawStream: the headers went out, ``write_fn`` must stop at its first write."""


class _Done(Exception):
    """The answer is sent (or the connection is gone): nothing more to do with this request."""


def _host_name(raw):
    v = (raw or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        return v[1:end] if end > 0 else v
    if v.count(":") == 1:
        return v.split(":", 1)[0]
    return v.rstrip(".")


# ── behind nginx: the listener "proxy" (docs/k3/SPEC-wave4.md §3) ───────────

PREFIX_RE = re.compile(r"^(?:/[A-Za-z0-9_-][A-Za-z0-9._~-]{0,159}){1,4}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.:\[\]-]{1,262}$")
PAGE_PATH = re.compile(r"^/p(?:/|$)")
SERVER_ADDR_HEADER = "x-kaskad-server-addr"     # REV-mip-s34 С-2: written by our nginx only (site/nginx.py)


class ProxyView(object):
    """What a request to the listener "proxy" is — nginx is trusted there, and only there (``proxy_view``)."""

    __slots__ = ("base", "path", "host", "scheme", "client_ip", "redirect", "outside", "server_addr")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _header_ip(value):
    a = auth._ip((value or "").strip())
    return str(a) if a is not None else ""


def _secret_eq(a, b):
    """The secret prefix compared in constant time (3.3.7.5): no answer time tells how much of it a guess got."""
    return hmac.compare_digest(str(a).encode("utf-8", "replace"), str(b).encode("utf-8", "replace"))


def _prefix_match(prefix, path):
    """0 — ``path`` is not under the secret ``prefix``, 1 — it is the bare prefix, 2 — under it ("<prefix>/…")."""
    n = len(prefix)
    if not prefix or len(path) < n or not _secret_eq(path[:n], prefix):
        return 0
    if len(path) == n:
        return 1
    return 2 if path[n] == "/" else 0


def proxy_view(cfg, path, headers):
    """The request as nginx saw it (``headers`` — lower-case names):

      base, path  the prefix of the panel and the path without it: the configured prefix when the path starts with
                  it (nginx passed the path as it is), else ``X-Forwarded-Prefix`` (nginx cut it off; with a prefix
                  configured only that very one counts), else "";
      host        ``X-Forwarded-Host`` (its first value), else ``Host``;
      scheme      ``X-Forwarded-Proto`` (http|https), else "https": nginx ends the TLS;
      client_ip   ``X-Real-IP``, else the last address of ``X-Forwarded-For`` (the one nginx added), else "";
      server_addr ``X-Kaskad-Server-Addr`` — the address of the server the visitor came to, set by our vhost only
                  (``proxy_set_header`` replaces the visitor's own header of that name; site/nginx.py), else "";
      redirect    the bare prefix without its slash -> 301 to "<prefix>/" (relative addresses of the page need it);
      outside     a prefix is configured, the request is neither under it nor a page of a person (/p/…) -> the
                  404 of an empty site: under the secret path only, whatever nginx forwards by mistake."""
    prefix = conf.proxy_base(cfg)
    base, rest, redirect = "", path, False
    how = _prefix_match(prefix, path)
    if how == 1:
        base, rest, redirect = prefix, "/", True
    elif how == 2:
        base, rest = prefix, path[len(prefix):]
    else:
        fwd = (headers.get("x-forwarded-prefix") or "").strip().rstrip("/")
        if fwd and PREFIX_RE.match(fwd) and (not prefix or _secret_eq(fwd, prefix)):
            base = fwd
    host = (headers.get("x-forwarded-host") or "").split(",", 1)[0].strip() or (headers.get("host") or "").strip()
    if not _HOST_RE.match(host):
        host = ""
    proto = (headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    ip = _header_ip(headers.get("x-real-ip"))
    if not ip:
        ip = _header_ip((headers.get("x-forwarded-for") or "").split(",")[-1])
    return ProxyView(base=base, path=rest, host=host, scheme=proto if proto in ("http", "https") else "https",
                     client_ip=ip, redirect=redirect,
                     outside=bool(prefix) and not base and not PAGE_PATH.match(rest),
                     server_addr=_header_ip(headers.get(SERVER_ADDR_HEADER)))


def public_view(cfg, path):
    """The open mode (3.3.7.5): (base, path, redirect, outside) — the panel only under the secret prefix of the panel
    (conf ``proxy.path``, one for nginx and the open mode); outside it only the pages of people (/p/…) and the
    quick setup (/setup-…, a secret of its own) — everything else, the bare port included, is ``outside``. No
    secret yet — nothing of the panel answers there."""
    prefix = conf.proxy_base(cfg)
    how = _prefix_match(prefix, path)
    if how == 1:
        return prefix, "/", True, False
    if how == 2:
        return prefix, path[len(prefix):], False, False
    return "", path, False, not (PAGE_PATH.match(path) or path.startswith(SETUP_PREFIX) or _setup_shared(path))


# 3.3.7.6 (a user, 21.10): the page of the quick setup lives under /setup-<secret>/ and takes the shared files of the
# panel by «../…» — from the root of the port (index.html, qs.css, the English of setup.js, app icons of give.js).
# With the root closed (3.3.7.5) the wizard came without its look. While the wizard is active — and only then — these
# few files answer at the root; the panel's page, its API and anything else stay a path nobody knows.
_SETUP_SHARED = frozenset(("/boot.js", "/tokens.css", "/components.css", "/give.css", "/ux.css", "/icons.js", "/qr.js",
                           "/qr-slides.js", "/give-i18n-ru.js", "/give-i18n-en.js", "/give.js", "/ux-i18n-ru.js",
                           "/ux-i18n-en.js", "/ux.js"))
_SETUP_SHARED_RE = re.compile(r"^/(?:fonts/%s\.woff2|static/apps/%s\.png)$" % (_NAME, _NAME))    # REV-3376 1–2: only app icons


def _setup_shared(path):
    """A shared file the page of an active quick setup asks for at the root of the open port (fail closed)."""
    if path not in _SETUP_SHARED and not _SETUP_SHARED_RE.match(path):
        return False
    try:
        # REV-3376 3: as the page of the wizard itself (link.match) — an active wizard with a secret; a link past its
        # time still shows the page «ask for a new link», and it gets its look too
        doc = importlib.import_module("kaskad.setup.model").load()
        return doc["status"] == "active" and bool(doc["link"].get("secret"))
    except Exception:                                   # noqa: BLE001 — no wizard, a broken file: the root stays shut
        return False


def _same_public_view(cfg, path, seen):
    """May a stream of the open mode let in as ``seen`` = (base, path) go on: the secret prefix did not change
    (``kaskadctl panel proxy --new-path``, «Сменить секретный адрес») — as SEC-f19b 4 behind nginx."""
    base, rest, _, outside = public_view(cfg, path)
    return not outside and (base, rest) == seen


def _same_proxy_view(cfg, path, headers, seen):
    """May a stream let in behind nginx as ``seen`` = (base, path) go on by the settings ``cfg`` of now: the request
    is not outside the secret path and is seen under the very same prefix and path (SEC-f19b 4)."""
    pv = proxy_view(cfg, path, headers)
    return not pv.outside and (pv.base, pv.path) == seen


def proxy_headers(headers, base):
    """The headers of an answer behind nginx: the session cookie always ``Secure`` and with ``Path=<base>/``,
    a ``Location`` of an absolute path under the prefix (the login by a link brings back to the panel)."""
    out = dict(headers)
    loc = out.get("Location")
    if isinstance(loc, str) and loc.startswith("/") and not loc.startswith("//") and base:
        out["Location"] = base + loc
    ck = out.get("Set-Cookie")
    if ck is not None:
        out["Set-Cookie"] = [auth.cookie_for(c, secure=True, path=base + "/")
                             for c in (ck if isinstance(ck, (list, tuple)) else [ck])]
    return out


_AFTER_LIVE = set()         # the threads of ``Request.after`` not finished yet (``after_wait``)
_AFTER_LOCK = threading.Lock()


def _run_after(fns, method, path):
    """``Request.after`` of a request whose answer has left (or could not: the client went away, the handler failed) —
    AFTER_S later in a thread of its own, one after another; a failure of one is a line in the journal, the next
    runs."""
    def run():
        try:
            for fn in fns:
                try:
                    fn()
                except Exception:                     # noqa: BLE001 — the answer is gone: the journal only
                    log_t("journal.srv.handler_error", method=method, route=path + " (after)",
                          err=traceback.format_exc())
        finally:
            with _AFTER_LOCK:
                _AFTER_LIVE.discard(t)
    t = threading.Timer(AFTER_S, run)
    t.daemon = True
    with _AFTER_LOCK:
        _AFTER_LIVE.add(t)
    t.start()


def after_wait(timeout=AFTER_STOP_S):
    """What the answers owed (``Request.after``) finishes first (≤ ``timeout`` s): the stop of the panel never cuts a
    restart of xray half way; what is still not done — the collector catches up (П3)."""
    end = time.monotonic() + max(0.0, timeout)
    with _AFTER_LOCK:
        live = list(_AFTER_LIVE)
    for t in live:
        t.join(max(0.0, end - time.monotonic()))


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "kaskad-panel"
    sys_version = ""
    timeout = SOCKET_TIMEOUT

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):        # no access log (spec §3.1)
        pass

    def send_error(self, code, message=None, explain=None):
        """http.server's own refusals (bad request line, too long, unknown method): JSON, our headers; on the public
        listener — a plain page with no sign of the panel (3.3.7.5: the secret prefix is not known yet there)."""
        self.close_connection = True
        if getattr(self.server, "kind", "") == "public":
            reason = self.responses.get(code, ("Error",))[0]
            line = ("%d %s" % (code, reason)).encode("ascii", "replace")
            body = (b"<html>\r\n<head><title>" + line + b"</title></head>\r\n<body>\r\n<center><h1>" + line +
                    b"</h1></center>\r\n</body>\r\n</html>\r\n")
            try:
                self._send_plain(routes.Response(code, body, {}), head=self.command == "HEAD")
            except OSError:
                pass
            return
        body = json.dumps({"ok": False, "error": tr(_ERROR_TEXTS.get(code, "panel.server.httpOther"))},
                          ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD" and code >= 200 and code not in (204, 304):
                self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # T-3375 Н-1: without handlers http.server answered these with its own 501 page — a fingerprint on the open port;
    # through _dispatch they get the answer of an unknown path (under the secret path — 405, like any other method)
    def do_OPTIONS(self):
        self._dispatch("OPTIONS")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_TRACE(self):
        self._dispatch("TRACE")

    # ── one request ──
    def _dispatch(self, method):
        self._after = None                            # ``Request.after`` of this request (П3)
        try:
            self._dispatch_one(method)
        finally:
            if self._after:
                _run_after(list(self._after), method, str(self._log_path)[:120])

    def _dispatch_one(self, method):
        self._body_left = True
        self._base = None                             # behind nginx / open mode: the prefix of the panel ("" — none)
        self._plain = False                           # open mode outside the prefix: a 404 goes out plain (3.3.7.5)
        self._log_path = "-"                          # the path for the journal: never the secret prefix
        try:
            # the page's language (X-Kaskad-Lang, 3.3.2): every tr() of this request — refusals of the server too —
            # answers in it; without the header — the server's language
            with weblang.use(weblang.request_lang(dict((k.lower(), v) for k, v in self.headers.items()))):
                self._handle(method)
        except _Done:
            pass
        except (OSError, ssl.SSLError):
            self.close_connection = True              # the client went away
        except Exception:                             # noqa: BLE001 — never a traceback to the client
            log_t("journal.srv.request_error", method=method, path=str(self._log_path)[:120], err=traceback.format_exc())
            try:
                self._send(routes.error_response(routes.HttpError(500, tr("panel.server.internal"))))
            except (OSError, ssl.SSLError):
                self.close_connection = True

    def _local_ip(self, srv, pv=None):
        """The address of the server the request came to (PLAN-multi-ip Ш3 «Защита доступа» п. 3): the open mode — the
        address of its socket; behind nginx — ``X-Kaskad-Server-Addr`` of our vhost (REV-mip-s34 С-2: the Unix socket
        has no address; the header is read on that listener only, never on the open mode or the tunnel, where a
        visitor could send it himself); the local listener is the SSH tunnel — ""."""
        kind = getattr(srv, "kind", "")
        if kind == "proxy":
            return (pv.server_addr or "") if pv is not None else ""
        if kind != "public":
            return ""
        try:
            return _header_ip(self.connection.getsockname()[0])
        except (OSError, AttributeError, IndexError, TypeError):
            return ""

    def _cut_if_closed(self):
        """F19b (finding 4): the listener of this connection is closed (``Panel._stop``) — a request that still came
        over a kept-alive connection gets no answer at all, the connection ends: an open mode switched off leaves no
        way in through the connections it had accepted."""
        if self.server.closed:
            self.close_connection = True
            raise _Done()

    def _fail(self, status, text, data=None):
        self._send(routes.error_response(routes.HttpError(status, text, data=data)))
        raise _Done()

    def _read_body(self, headers, limit=MAX_BODY, total=None):
        """The whole body (≤ ``limit``). ``total`` — seconds for all of it (SEC-bc12 З-1: a slow body without a login
        must not hold a thread and a ``limit_conn`` place of nginx for long); None — SOCKET_TIMEOUT per read (the big
        bodies of the backup, only with a session). Not all of it in time — the connection closes, no answer."""
        if "transfer-encoding" in headers:
            self.close_connection = True
            self._fail(411, tr("panel.server.noChunked"))
        cl = headers.get("content-length")
        if cl is None:
            self._body_left = False
            return b""
        n = self._length(cl, limit)
        if not n:
            data = b""
        elif total is None:
            data = self.rfile.read(n)
        else:
            data = self._read_by(n, time.monotonic() + total)
        if len(data) < n:
            self.close_connection = True
            raise _Done()
        self._body_left = False
        return data

    def _read_by(self, n, end):
        """Up to ``n`` bytes of the body by the monotonic time ``end`` (shorter — the client was late or went away)."""
        buf = bytearray()
        try:
            while len(buf) < n:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._wait(left)
                chunk = self.rfile.read1(min(n - len(buf), 1 << 16))
                if not chunk:
                    break
                buf += chunk
        except OSError:                               # the deadline, a reset, a TLS error: what came is all
            pass
        finally:
            self._wait(None)
        return bytes(buf)

    def _length(self, cl, limit):
        """The Content-Length ``cl`` as a number: not ASCII digits — 400, more than ``limit`` — 413 (both close)."""
        cl = cl.strip()
        if not CL_RE.match(cl):
            self.close_connection = True
            self._fail(400, tr("panel.server.badContentLength"))
        n = int(cl) if len(cl) <= 19 else limit + 1        # 20+ digits: too big (int() of 4300+ digits raises)
        if n > limit:
            self.close_connection = True
            self._fail(413, tr("panel.server.bodyTooBig", mb=limit >> 20))
        return n

    def _stream_length(self, headers, limit):
        """``body="stream"`` (SPEC-browser-channel §4.1 п. 2): the body is NOT read, only its headers are checked —
        chunked or no length 411, not digits 400, more than the route's ``body_max`` 413; every refusal closes the
        connection (``_body_left`` stays True)."""
        if "transfer-encoding" in headers or headers.get("content-length") is None:
            self.close_connection = True
            self._fail(411, tr("panel.server.noChunked"))
        return self._length(headers["content-length"], limit)

    def _wait(self, timeout):
        """The socket's timeout of the next operation of a streamed body or answer: ``timeout`` s, never longer than
        SOCKET_TIMEOUT (None — SOCKET_TIMEOUT)."""
        t = SOCKET_TIMEOUT if timeout is None else max(0.05, min(float(timeout), SOCKET_TIMEOUT))
        self.connection.settimeout(t)

    def _handle(self, method):
        srv = self.server
        panel = srv.panel
        self._cut_if_closed()
        u = urllib.parse.urlsplit(self.path)
        headers = dict((k.lower(), v) for k, v in self.headers.items())
        public = srv.kind == "public"
        if public and (u.scheme or u.netloc or not u.path.startswith("/")):
            self._send_plain(not_found(), head=method == "HEAD")
            raise _Done()
        if len(self.headers.get_all("Content-Length") or ()) > 1:
            self.close_connection = True              # two lengths (RFC 9112 §6.3): an error, never "the last one"
            if public:
                self.send_error(400)                  # 3.3.7.5: the bare port — a plain page, nothing of the panel
                raise _Done()
            self._fail(400, tr("panel.server.badContentLength"))
        if u.scheme or u.netloc or not u.path.startswith("/"):
            self._fail(400, tr("panel.server.badPath"))
        cfg = panel.cfg
        path, scheme, base = u.path, srv.scheme, ""
        if public:
            # 3.3.7.5: the panel only under its secret prefix; anything else — as a path nobody knows (no 403, no
            # JSON, no Server: the bare port tells nothing of Kaskad)
            pub_base, path, pub_redirect, outside = public_view(cfg, u.path)
            if outside:
                self._send_plain(not_found(), head=method == "HEAD")
                raise _Done()
            base = pub_base
            if base:
                self._base = base
            else:
                self._plain = True                    # /p/… and /setup-…: their 404 is the plain one as well
            if not srv.host_allowed(headers.get("host")) and not base:
                self._send_plain(not_found(), head=method == "HEAD")
                raise _Done()
            if pub_redirect:
                self._send(routes.Response(301, b"", {"Location": "/" + ("?" + u.query if u.query else "")}),
                           head=method == "HEAD")
                raise _Done()
        client_ip = (self.client_address[0] if self.client_address else "") or ""
        if srv.kind == "proxy":
            # behind nginx: its headers are the truth here (and only here); the secret prefix is cut off
            pv_headers = dict(headers)                # as nginx sent them: the stream's gate looks at them again
            pv = proxy_view(cfg, u.path, pv_headers)
            path, scheme, base, client_ip = pv.path, pv.scheme, pv.base, pv.client_ip
            self._base = base
            headers["host"] = pv.host
        # the journal gets the path without the secret prefix and without the token of a person's page
        self._log_path = "/p/…" if PAGE_PATH.match(path) else path
        setup = None
        if path.startswith(SETUP_PREFIX):
            self._log_path = SETUP_PREFIX + "…"            # the secret of the quick setup never goes to the journal
        if not srv.host_allowed(headers.get("host")):
            self._fail(403, tr("panel.server.badHost"))
        if path.startswith(SETUP_PREFIX) and srv.kind in ("public", "local") and not base:
            # the quick setup (kaskad.setup.web): a wrong secret, a closed wizard, another address of a bound link —
            # the very 404 of an unknown path; under the prefix only its page, static files and /api/setup/*
            setup = _setup_web().resolve(path, client_ip)
            if setup is None or not _setup_web().allowed(setup.path):
                self._send(not_found(), head=method == "HEAD")
                raise _Done()
            if setup.redirect:
                self._send(routes.Response(301, b"", {"Location": setup.base + "/" + ("?" + u.query if u.query else "")}),
                           head=method == "HEAD")
                raise _Done()
            path = setup.path
            self._log_path = SETUP_PREFIX + "…" + path
        elif path.startswith("/api/setup/"):
            self._send(not_found(), head=method == "HEAD")    # the wizard's API exists only under its secret path
            raise _Done()
        if srv.kind == "proxy":
            if pv.outside:
                self._send(not_found(), head=method == "HEAD")
                raise _Done()
            if pv.redirect:
                self._send(routes.Response(301, b"", {"Location": "/" + ("?" + u.query if u.query else "")}),
                           head=method == "HEAD")
                raise _Done()
        r, m = routes.find(method, path)
        if setup is not None and method in ("GET", "HEAD"):
            if path in ("/", "/index.html"):
                r, m = _setup_entry(), None
            elif ("setup" + path) in STATIC.entries:          # the page's own files: <base>/setup.js -> setup/setup.js
                r, m = _setup_file(), None
        if r is None:
            self._send(not_found(), head=method == "HEAD")
            raise _Done()
        if r == "method":
            self._fail(405, tr("panel.server.badMethod"))
        token = auth.cookie_token(headers)
        session = auth.SESSIONS.get(token) if token else None
        if session is not None:
            session = dict(session, token=token)
        elif auth.tunnel(headers, client_ip, srv.kind == "local", public=cfg["public"]["on"],
                         require_password=cfg["require_password"]):
            session = {"via": "tunnel", "ip": client_ip}
        big = method == "POST" and path in BIG_BODY_PATHS and session is not None and r.auth == "admin"
        if setup is not None and method == "POST" and path in SETUP_BIG_BODY_PATHS:
            big = not setup.expired and _setup_web().has_session(headers)
        # the browser test (SPEC-browser-channel §4.1 п. 1–2): an "admin" route with body="stream" and a session — the
        # body is not read here, only its headers; without a session the usual path (the answer of any /api/*)
        stream = r.body == "stream" and session is not None and r.auth == "admin"
        if stream:
            length, body = self._stream_length(headers, r.body_max), b""
        else:
            length = None
            body = self._read_body(headers, BACKUP_BODY_MAX if big else MAX_BODY, None if big else BODY_S)
        page_key = auth.bucket(client_ip) if r.auth == "page" else None
        if r.auth == "admin" and session is None:
            self._fail(401, tr("panel.server.loginRequired"), data={"auth": "required"})
        if method == "HEAD" and not r.head:
            self._fail(405, tr("panel.server.badMethod"))     # §4.1 п. 5: after the login (without it — 401)
        if page_key is not None and auth.PAGES.banned(page_key):
            self._send(page_refused(method, path, base), head=method == "HEAD")
            raise _Done()
        # a change, or a GET that runs commands (Route.runs, REV-3377-stand-fix 1): not for a foreign page
        if method in ("POST", "PUT", "DELETE") or r.runs:
            origin = (headers.get("origin") or "").strip()
            if origin and (origin != "null" or r.runs) and origin.lower() != "%s://%s" % (scheme, (headers.get("host") or "").lower()):
                self._fail(403, tr("panel.server.foreignOrigin"))
            if (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
                self._fail(403, tr("panel.server.foreignOrigin"))
            if r.csrf and (headers.get("x-kaskad-admin") or "").strip() != "1":
                self._fail(403, tr("panel.server.noAdminHeader"))
        query = dict((k, v[0]) for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items())
        reader = routes.BodyReader(self.rfile, length, self._wait) if stream else None
        req = routes.Request("GET" if method == "HEAD" else method, path, query, headers, body, m, session,
                             client_ip, scheme, base=setup.base if setup is not None else base, listener=srv.kind,
                             setup=setup, rfile=reader, length=length,
                             local_ip=self._local_ip(srv, pv if srv.kind == "proxy" else None))
        self._after = req.after
        self._cut_if_closed()                         # the listener closed while the body was read: no change
        try:
            try:
                resp = r.handler(req)
            finally:
                if reader is not None:
                    self._wait(None)
                    if reader.left > 0:
                        self.close_connection = True          # not read to its end: no keep-alive (§4.1 п. 4)
                    else:
                        self._body_left = False
        except routes.HttpError as e:
            resp = routes.error_response(e)
            retry = (e.data or {}).get("retry_after") if isinstance(e.data, dict) else None
            if retry:
                resp.headers["Retry-After"] = str(int(retry))
        except KaskadError as e:
            resp = routes.error_response(routes.core_error(e))
        except Exception:                             # noqa: BLE001 — a bug of a handler: 500, details to the journal
            log_t("journal.srv.handler_error", method=method, route=r.pattern, err=traceback.format_exc())
            resp = routes.error_response(routes.HttpError(500, tr("panel.server.internal")))
        if page_key is not None and getattr(resp, "status", 200) == 404:
            auth.PAGES.miss(page_key)
        if isinstance(resp, sse.Stream):
            # F19b: a stream of a listener that is closed (the open mode off, nginx's socket off) ends at once; its
            # name (Host) no longer one of the listener's (the domain of the open mode changed) — as a new request
            host = headers.get("host")
            resp.gate(lambda: not srv.closed and srv.host_allowed(host))
            if srv.kind == "proxy":
                # SEC-f19b 4: the secret path of nginx changed (``kaskadctl panel proxy --new-path``) — the stream
                # under the old one ends, as its REST (404 there): the request must still be seen as it was let in
                raw_path, seen = u.path, (pv.base, pv.path)
                resp.gate(lambda: _same_proxy_view(panel.cfg, raw_path, pv_headers, seen))
            elif public:
                # 3.3.7.5: the secret prefix changed — the stream under the old one ends as its REST (404 there)
                raw_pub, seen_pub = u.path, (base, path)
                resp.gate(lambda: _same_public_view(panel.cfg, raw_pub, seen_pub))
            if method == "HEAD":
                resp.hub.unsubscribe()
                self._send(routes.Response(200, b"", dict(resp.headers)), head=True)
                return
            self._stream(resp)
            return
        if isinstance(resp, routes.RawStream):
            self._raw(resp, r.pattern, head=method == "HEAD")
            return
        if not isinstance(resp, routes.Response):
            raise TypeError("обработчик %s вернул %r" % (r.pattern, type(resp).__name__))
        self._send(resp, head=method == "HEAD")

    # ── answers ──
    def _headers(self, extra):
        for k, v in SECURITY_HEADERS:
            if k not in extra:
                self.send_header(k, v)
        for k, v in extra.items():
            for one in (v if isinstance(v, (list, tuple)) else [v]):
                self.send_header(k, str(one))

    def _send_plain(self, resp, head=False):
        """The 404 of an empty site with nothing of ours: no Server, no security headers, no Cache-Control — what the
        open mode answers outside the secret prefix (3.3.7.5)."""
        if getattr(self, "_body_left", False):
            self.close_connection = True
        self.send_response_only(resp.status)
        self.send_header("Date", self.date_time_string())
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(resp.body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head and resp.body:
            self.wfile.write(resp.body)

    def _send(self, resp, head=False):
        if getattr(self, "_plain", False) and resp.status == 404 and resp.body == not_found().body:
            self._send_plain(resp, head=head)
            return
        if self._body_left:
            self.close_connection = True              # the body of the request was not read: no keep-alive
        hdrs = dict(resp.headers) if self._base is None else proxy_headers(resp.headers, self._base)
        hdrs.setdefault("Cache-Control", "no-store")
        if resp.body or resp.status not in (204, 304):
            hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
        if getattr(self, "_plain", False):
            # 3.3.7.5: the open mode outside the panel's prefix (/p/…, /setup-…) names no server either
            self.send_response_only(resp.status)
            self.send_header("Date", self.date_time_string())
        else:
            self.send_response(resp.status)
        self._headers(hdrs)
        if resp.status not in (204, 304):
            self.send_header("Content-Length", str(len(resp.body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head and resp.body and resp.status not in (204, 304):
            self.wfile.write(resp.body)

    def _raw(self, resp, pattern, head=False):
        """A ``routes.RawStream`` (SPEC-browser-channel §4.1): the headers with ``Content-Length``, then the handler's
        ``write_fn`` writes — called exactly once whatever happens (it gives back what it took in its ``finally``).
        Keep-alive stays only when exactly ``length`` bytes went out. A client gone (a broken pipe, a reset, a TLS
        error, a timeout) is quiet; a bug of ``write_fn`` — a journal line and the connection closed (a 500 only while
        no header went out)."""
        if self._body_left:
            self.close_connection = True
        hdrs = dict(resp.headers) if self._base is None else proxy_headers(resp.headers, self._base)
        hdrs.setdefault("Cache-Control", "no-store")
        hdrs.setdefault("Content-Type", "application/octet-stream")
        out = {"head": False, "sent": 0}

        def headers():
            out["head"] = True
            self.send_response(resp.status)
            self._headers(hdrs)
            self.send_header("Content-Length", str(resp.length))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()

        def write(data, timeout=None):
            if not out["head"]:
                headers()
            if head:
                raise _HeadOnly()
            self._wait(timeout)
            self.wfile.write(data)
            out["sent"] += len(data)

        try:
            try:
                resp.write_fn(write)
                if not out["head"]:
                    headers()
            finally:
                self._wait(None)
        except _HeadOnly:
            pass
        except (OSError, ssl.SSLError):
            self.close_connection = True              # the client went away: quiet
            return
        except Exception:                             # noqa: BLE001 — a bug of the handler's writer
            self.close_connection = True
            log_t("journal.srv.handler_error", method="GET", route=pattern, err=traceback.format_exc())
            if not out["head"]:
                self._send(routes.error_response(routes.HttpError(500, tr("panel.server.internal"))))
            raise _Done()
        if head:
            return
        if out["sent"] != resp.length:
            self.close_connection = True

    def _stream(self, stream):
        self.close_connection = True
        running = False
        try:
            self.send_response(stream.status)
            self._headers(dict(stream.headers))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()

            def write(data):
                self.wfile.write(data)
                self.wfile.flush()
            running = True
            stream.run(write)                         # gives its slot back itself
        finally:
            if not running:
                stream.hub.unsubscribe()


# ── listeners ──────────────────────────────────────────────────────────────

class Listener(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, panel, kind, addr, tls=None, names=()):
        self.panel = panel
        self.kind = kind
        self.tls = tls                                 # callable -> ssl.SSLContext, or None (plain HTTP)
        self.scheme = "https" if tls is not None else "http"
        self.names = set(n.lower() for n in names)
        self._conn = 0
        self._conn_lock = threading.Lock()
        self._socks = set()                            # the accepted connections (F19b: closed with the listener)
        self.closed = False
        if isinstance(addr, str):                      # a unix socket (the listener for nginx)
            self.address_family = socket.AF_UNIX
        else:
            self.address_family = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
        http.server.HTTPServer.__init__(self, addr, Handler)

    @property
    def unix(self):
        return self.address_family == socket.AF_UNIX

    def server_bind(self):
        if self.unix:
            self._bind_unix()
            return
        socketserver.TCPServer.server_bind(self)      # without getfqdn(): no DNS lookup at start
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port

    def _bind_unix(self):
        """The socket file: a stale one of the previous run goes (anything else in its place is an error), 0660,
        the group www-data (nginx) when it exists; its directory 0710 of the same group — nginx passes through
        and sees nothing else there."""
        path = self.server_address
        d = os.path.dirname(path)
        util.ensure_run_dir(os.path.dirname(d))        # /run/kaskad: nginx (www-data) must pass through it
        util.ensure_dir(d, conf.RUN_DIR_MODE)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None:
            if not stat.S_ISSOCK(st.st_mode):
                raise OSError(errno.EEXIST, "на месте сокета другой файл", path)
            os.unlink(path)
        self.socket.bind(path)
        os.chmod(path, conf.PROXY_SOCK_MODE)
        gid = _group_id(conf.PROXY_GROUP)
        if gid is not None:
            try:
                os.chown(path, -1, gid)
                os.chown(d, -1, gid)
            except OSError as e:
                log_t("journal.srv.proxy_group", group=conf.PROXY_GROUP, err=e.strerror or e)
        self.server_name, self.server_port = "unix", 0

    def server_close(self):
        http.server.HTTPServer.server_close(self)
        if self.unix:
            try:
                if stat.S_ISSOCK(os.lstat(self.server_address).st_mode):
                    os.unlink(self.server_address)
            except OSError:
                pass

    def host_allowed(self, raw):
        if raw is None or raw.strip() == "":
            return self.kind == "local"               # HTTP/1.0 tools; a browser always sends Host
        return _host_name(raw) in self.names

    def process_request(self, request, client_address):
        with self._conn_lock:
            busy = self._conn >= MAX_CONN
            if not busy:
                self._conn += 1
        if busy:
            self.shutdown_request(request)
            return
        try:
            socketserver.ThreadingMixIn.process_request(self, request, client_address)
        except Exception:                              # noqa: BLE001 — no thread: give the slot back
            with self._conn_lock:
                self._conn -= 1
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            self._track(request)
            if self.tls is not None:
                try:
                    request.settimeout(HANDSHAKE_TIMEOUT)
                    wrapped = self.tls().wrap_socket(request, server_side=True)
                except (OSError, ValueError, NoCertificate):
                    try:
                        request.close()
                    except OSError:
                        pass
                    return
                self._untrack(request)
                request = wrapped
                self._track(request)
            socketserver.ThreadingMixIn.process_request_thread(self, request, client_address)
        finally:
            self._untrack(request)
            with self._conn_lock:
                self._conn -= 1

    def _track(self, sock):
        with self._conn_lock:
            self._socks.add(sock)
            closed = self.closed
        if closed:                                     # accepted just before the listener closed
            _cut(sock)

    def _untrack(self, sock):
        with self._conn_lock:
            self._socks.discard(sock)

    def close_connections(self):
        """F19b (finding 4): the listener is closed — every connection it accepted ends now (kept-alive REST, event
        streams, a request half-read): the threads blocked on them wake with an EOF, ``Handler`` answers nothing more
        (``closed``). Without it a browser of the open mode kept full access after «open mode off» while it sent a
        request at least every SOCKET_TIMEOUT s."""
        with self._conn_lock:
            self.closed = True
            socks = list(self._socks)
        for sock in socks:
            _cut(sock)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (OSError, ssl.SSLError)):
            return                                     # a client that went away
        log_t("journal.srv.conn_error", err=traceback.format_exc())


def _cut(sock):
    """Shut a connection down both ways from another thread (TLS too: the socket call, not ``SSLSocket.shutdown``,
    which would unwrap under the feet of the thread reading it); its owner closes it."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, ValueError):
        pass


def _group_id(name):
    try:
        import grp
        return grp.getgrnam(name).gr_gid
    except (ImportError, KeyError):
        return None


class NoCertificate(Exception):
    """The open mode is on, but the node certificate is not in secrets.json."""


def tls_context(crt, key):
    """An SSLContext of the certificate and key PEM (TLS 1.2+). The key goes through a 0700 temp directory in
    /run/kaskad/panel only for the moment ``load_cert_chain`` reads it."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    util.ensure_dir(paths.panel_run_dir(), conf.RUN_DIR_MODE)
    d = tempfile.mkdtemp(prefix=".tls-", dir=paths.panel_run_dir())
    try:
        cp, kp = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
        util.atomic_write(cp, crt, 0o600)
        util.atomic_write(kp, key, 0o600)
        ctx.load_cert_chain(cp, kp)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return ctx


def _is_address(name):
    try:
        ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    return True


def sni_pick(name):
    """Which certificate a ClientHello asks for: "ip" — no SNI or an IP address in it (browsers send none for an
    address; a few tools send the address itself), "name" — a host name (the domain or anything else: the main
    certificate, the domain's)."""
    return "ip" if not name or _is_address(str(name)) else "name"


def sni_callback(ip_ctx, names=None):
    """``SSLContext.sni_callback`` of the main (domain) context: an address or no name switches the connection to the
    context of the IP certificate (``ip_ctx``, None — keep the main one); a name of ``names`` ({name: context} — the
    domain a change has just replaced, conf.prev_site_cert) to its own. Never raises — a failed choice keeps the main
    certificate."""
    names = dict((k.lower(), v) for k, v in (names or {}).items())

    def pick(sock, name, _ctx):
        try:
            if sni_pick(name) == "ip":
                if ip_ctx is not None:
                    sock.context = ip_ctx
            elif names and str(name).lower().rstrip(".") in names:
                sock.context = names[str(name).lower().rstrip(".")]
        except Exception:                                  # noqa: BLE001 — the handshake goes on with the domain's
            pass
        return None
    return pick


def _load_cfg():
    """The settings; the open mode on and no secret prefix yet (a node updated from before 3.3.7.5) — it is made
    now: the open mode never serves the panel at the bare port."""
    cfg = conf.load()
    if cfg["public"]["on"] and not cfg["proxy"]["path"]:
        try:
            cfg = conf.ensure_secret(cfg)
        except OSError as e:
            log_t("journal.srv.secret_failed", err=e)   # without a secret public_view lets nothing of the panel in
    return cfg


class Panel(object):
    """The running panel: its settings, listeners, the TLS context of the open mode, the status file."""

    def __init__(self, local=(conf.LOCAL_HOST, conf.LOCAL_PORT), proxy_sock=None):
        self.local_addr = local
        self.proxy_sock = proxy_sock                  # None: conf.proxy_sock_path() (tests give a short path)
        self.cfg = _load_cfg()
        self.started = time.time()
        self.servers = {}                 # kind -> (Listener, thread)
        self.errors = {}                  # kind -> text
        self.fingerprint = ""
        self._lock = threading.RLock()
        self._tls = None
        self._tls_crt = None
        self._tls_pair = None             # (main certificate PEM, IP certificate PEM for SNI or None) of self._tls
        self._tls_sig = None
        self._tls_checked = 0.0
        self._addrs = None                # the addresses of the open mode (conf.listen_addresses), None — look again
        auth.SESSIONS.on_change = self._sessions_changed

    def _sessions_changed(self, n):
        """A session came or went: the status file, and every open event stream looks whether its session is still
        there (F19: «Выйти» ends the live data of the other tabs too, not in HEARTBEAT seconds)."""
        try:
            sse.HUB.kick()
        finally:
            self.write_status()

    # ── TLS of the open mode ──
    def tls(self):
        """The current SSLContext; the certificate is re-read when secrets.json changed (SSTP renewed the node
        certificate), the copy of the domain's certificate changed (``kaskadctl site cert-installed``: issued,
        renewed, removed) or state.json did (the domain set or cleared) — the domain's one wins while it is valid
        (conf.panel_cert). Next to the domain's certificate the IP one (conf.sni_ip_cert) answers a ClientHello
        without a name or with an address (``sni_callback``): https://<IP>:<порт>/ keeps opening without a warning
        after the domain got its certificate (the wizard on stage 4, old links of /p/)."""
        now = time.monotonic()
        if self._tls is not None and now - self._tls_checked < TLS_CHECK_EVERY:
            return self._tls
        with self._lock:
            self._tls_checked = now
            from .. import snapshot
            from ..ipcert import store as ipstore
            from ..site import certinfo, model as smodel
            # the hour: the domain's certificate stops being served within an hour of its expiry; the IP certificate
            # (kaskad.ipcert) is renewed by its timer and re-read here without a restart
            sig = (snapshot.signature(paths.secrets_file()), snapshot.signature(smodel.fullchain_path()),
                   snapshot.signature(smodel.privkey_path()), snapshot.signature(paths.state()),
                   snapshot.signature(ipstore.fullchain_path()), snapshot.signature(ipstore.meta_path()),
                   int(certinfo._now() // 3600), self._prev_live())
            if self._tls is not None and sig == self._tls_sig:
                return self._tls
            crt, key = conf.panel_cert()
            if not crt or not key:
                if self._tls is None:
                    raise NoCertificate(tr("panel.server.noCertificate"))
                return self._tls
            ipp = conf.sni_ip_cert()
            if ipp is not None and ipp[0] == crt:
                ipp = None
            prev = conf.prev_site_cert()                 # the replaced domain while its grace lasts
            pair = (crt, ipp[0] if ipp is not None else None, prev[1] if prev is not None else None)
            if pair != self._tls_pair:
                try:
                    ctx = tls_context(crt, key)
                except (OSError, ValueError, ssl.SSLError):
                    # a pair caught half-written (the copies of fullchain and privkey are two files): the next
                    # connection looks again at once — not the old certificate for TLS_CHECK_EVERY more
                    self._tls_checked = 0.0
                    raise
                ip_ctx, extra = None, {}
                if ipp is not None:
                    try:
                        ip_ctx = tls_context(*ipp)
                    except (OSError, ValueError, ssl.SSLError) as e:
                        log_t("journal.srv.ip_cert", err=e)
                if prev is not None:
                    try:
                        extra[prev[0]] = tls_context(prev[1], prev[2])
                    except (OSError, ValueError, ssl.SSLError) as e:
                        log_t("journal.srv.old_cert", err=e)
                if ip_ctx is not None or extra:
                    ctx.sni_callback = sni_callback(ip_ctx, extra)
                self._tls = ctx
                renamed = pair[2:] != (self._tls_pair or (None, None, None))[2:]
                self._tls_pair = pair
            else:
                renamed = False
            if crt != self._tls_crt or renamed:
                self._tls_crt = crt
                self.fingerprint = conf.fingerprint()
                for key in self._public_keys():
                    self.servers[key][0].names = self._public_names()   # the domain is a name of the open mode now (or not)
            self._tls_sig = sig
            return self._tls

    def tls_refresh(self):
        """Re-read the certificates now, not within TLS_CHECK_EVERY (T-M6-qs330b №4): SIGHUP (``kaskadctl site
        cert-installed`` after an issue tells the panel through systemd) and the move check of the quick setup —
        the next handshake already gets the domain's certificate. -> the context or None (no open mode, no
        certificate, a broken pair: the reason is logged, the listeners keep what they had)."""
        with self._lock:
            self._tls_checked = 0.0
            self._tls_sig = None                       # the signature of the files is not trusted here: read them
            if not self._public_keys():
                return None
            try:
                return self.tls()
            except (OSError, ValueError, ssl.SSLError, NoCertificate) as e:
                log_t("journal.srv.public_cert", err=e)
                return None

    # ── listeners ──
    def _start(self, kind, addr, tls=None, names=(), key=None):
        """A listener of ``kind`` (local | public | proxy); ``key`` — its name among the listeners (the open mode on an
        extra address of the node: «public@203.0.113.7»), else the kind."""
        key = key or kind
        srv = Listener(self, kind, addr, tls=tls, names=names)
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": SHUTDOWN_POLL},
                             name="kaskad-panel-%s" % key, daemon=True)
        t.start()
        self.servers[key] = (srv, t)
        self.errors.pop(key, None)
        return srv

    def _stop(self, kind):
        got = self.servers.pop(kind, None)
        if got is not None:
            srv, t = got
            srv.shutdown()
            srv.close_connections()                    # F19b: what it accepted ends with it (keep-alive, streams)
            srv.server_close()
            t.join(5)
            sse.HUB.kick()

    def start(self):
        with self._lock:
            if "local" not in self.servers:
                try:
                    self._start("local", self.local_addr, names=LOCAL_NAMES)
                except OSError as e:
                    self.errors["local"] = str(e)
                    raise KaskadError(tr("panel.server.notListening", host=self.local_addr[0], port=self.local_addr[1],
                                          why=e.strerror or e))
            self._reconcile()
        self.write_status()
        self.write_access()
        self._old_links()
        return self

    @staticmethod
    def _old_links():
        """3.3.7.5 круг 3: once per node — links of people given before this code were on the port (portoff (c))."""
        try:
            from . import portoff, userpage
            portoff.check_old_links(lambda: bool(userpage._stored_tokens() or userpage._stored_sub_tokens()))
        except Exception as e:                       # noqa: BLE001 — never stops the panel's start
            log_t("journal.srv.access_json", err=e)

    def write_access(self):
        """access.json for the user page (WP-U): written at start and reload, only when it differs."""
        try:
            local = self.servers.get("local")
            conf.write_access(self.cfg, local[0].server_address[1] if local else conf.LOCAL_PORT)
        except OSError as e:
            log_t("journal.srv.access_json", err=e)

    def _public_keys(self):
        return [k for k in self.servers if k == "public" or k.startswith("public@")]

    def _listen_addrs(self):
        """The addresses of the open mode: ``bind`` and the other IPv4 addresses of its interface (conf.listen_addresses;
        k3/SPEC-multi-ip.md §1.4 — a domain with A-records on two addresses of the node opens through either)."""
        pub = self.cfg["public"]
        if not pub["on"] or not pub.get("bind"):
            return []
        if self._addrs is None:
            try:
                self._addrs = conf.listen_addresses(sysrun.default_runner(), pub["bind"])
            except Exception:                         # noqa: BLE001 — never a reason not to listen on bind
                self._addrs = [pub["bind"]]
        return list(self._addrs)

    def refresh_addresses(self):
        """An address of the node came or went (every ADDR_EVERY s from ``serve``): the listeners follow."""
        with self._lock:
            old = self._addrs
            self._addrs = None
            if self._listen_addrs() == (old or []) or not self.cfg["public"]["on"]:
                return False
            self._reconcile()
        log_t("journal.srv.public_addrs", addrs=", ".join(self._addrs or []))
        self.write_status()
        return True

    @staticmethod
    def _prev_live():
        """The replaced domain whose grace has not run out (conf.prev_site_cert reads the rest), or None — a part of
        the signature of the TLS files: the end of the grace takes the old name away within TLS_CHECK_EVERY."""
        try:
            from .. import state
            from ..site import model as smodel
            prev = smodel.peek(state.load())["prev"]
        except (KaskadError, ImportError, OSError, ValueError):
            return None
        return prev["domain"] if prev and prev["until"] > time.time() else None

    def _public_names(self):
        pub = self.cfg["public"]
        names = set(x.lower() for x in (pub.get("bind"), pub.get("host"), conf.public_host(self.cfg)) if x)
        got = conf.prev_site_cert()                      # the replaced domain while its grace lasts
        if got is not None:
            names.add(got[0].lower())
        names.update(a.lower() for a in (self._addrs or []))     # an extra address of the node typed by hand
        try:
            from .. import state
            from ..gw import model
            st = state.load()
            h = model.public_host(st)
            if h:
                names.add(h.lower())
        except KaskadError:
            st = None
        names.update(self._nat_names(st))
        return names

    def _nat_names(self, st):
        """The external addresses of a NAT cloud (and the ones added by hand) whose local address the open mode listens
        on — «Адреса сервера» from their cache (PLAN-multi-ip Ш2, Л7: https://<второй внешний>:<порт>/ opens too)."""
        if st is None:
            return set()
        try:
            from ..gw import addresses
            doc = addresses.cached(st)
        except Exception:                                # noqa: BLE001 — a broken cache: the names without it
            return set()
        here = set(self._addrs or [])
        return set(it["ip"] for it in (doc or {}).get("addresses") or []
                   if it["origin"] in ("nat", "manual") and (it.get("local") or it["ip"]) in here)

    def _reconcile(self):
        """The listeners of the open mode — one per address (``_listen_addrs``: the primary one is «public», an extra
        address «public@<адрес>»), all with the same port, certificate, names and checks; then the one for nginx."""
        pub = self.cfg["public"]
        want = {}
        for a in self._listen_addrs():
            want["public" if a == pub["bind"] else "public@" + a] = (a, pub["port"])
        for key in self._public_keys():
            if key not in want or self.servers[key][0].server_address[:2] != want[key]:
                self._stop(key)
        names = self._public_names()
        for key, addr in sorted(want.items()):
            cur = self.servers.get(key)
            if cur is not None:
                cur[0].names = names
                continue
            try:
                self._tls_checked = 0.0
                self.tls()
                self._start("public", addr, tls=self.tls, names=names, key=key)
            except (OSError, NoCertificate) as e:
                self.errors[key] = "%s:%d — %s" % (addr[0], addr[1], getattr(e, "strerror", None) or e)
                log_t("journal.srv.public_failed", err=self.errors[key])
        for key in [k for k in self.errors if (k == "public" or k.startswith("public@")) and k not in want]:
            self.errors.pop(key, None)
        self._reconcile_proxy()

    def _proxy_names(self):
        """Host/X-Forwarded-Host of the listener for nginx: the domain of the settings and the domain of state.json
        (``site.domain``, docs/k3/SPEC-wave4.md §4.1; ``gateway.site.domain`` of the first drafts is read too);
        without any — only the loopback names (a check on the node itself, a local proxy)."""
        names = set()
        d = self.cfg["proxy"].get("domain")
        if d:
            names.add(d.lower())
        try:
            from .. import state
            st = state.load()
            for site in (st.get("site"), (st.get("gateway") or {}).get("site")):
                d = site.get("domain") if isinstance(site, dict) else None
                if isinstance(d, str) and conf.DOMAIN_RE.match(d.strip().lower().rstrip(".")):
                    names.add(d.strip().lower().rstrip("."))
        except (KaskadError, AttributeError):
            pass
        return names or set(LOCAL_NAMES)

    def _reconcile_proxy(self):
        want = (self.proxy_sock or conf.proxy_sock_path()) if self.cfg["proxy"]["on"] else None
        cur = self.servers.get("proxy")
        if cur is not None and (want is None or cur[0].server_address != want):
            self._stop("proxy")
            cur = None
        if want is not None and cur is None:
            try:
                self._start("proxy", want, names=self._proxy_names())
            except OSError as e:
                self.errors["proxy"] = "unix:%s — %s" % (want, getattr(e, "strerror", None) or e)
                log_t("journal.srv.proxy_failed", err=self.errors["proxy"])
        elif cur is not None:
            cur[0].names = self._proxy_names()
        if want is None:
            self.errors.pop("proxy", None)

    def reload(self):
        """Settings changed (SIGHUP, ``kaskadctl panel …``, the panel's own settings): the open mode follows."""
        with self._lock:
            old = (self.cfg.get("proxy") or {}).get("path") or ""
            self.cfg = _load_cfg()
            new = self.cfg["proxy"]["path"] or ""
            if old and new and old != new:
                # the secret path changed («Сменить секретный адрес», ``panel proxy --new-path``): the sessions from
                # outside end on the server too, whatever cookie path they had (3.3.7.5)
                auth.SESSIONS.drop_remote()
            self._addrs = None                         # the open mode may have moved: its addresses looked at again
            self._reconcile()
            self.tls_refresh()                         # a certificate issued or renewed: served from the next handshake
        self.write_status()
        self.write_access()
        sse.HUB.poke()
        sse.HUB.kick()                                 # F19b: the tunnel's streams look again at its right (settings)

    def stop(self):
        sse.HUB.stop()
        with self._lock:
            for kind in list(self.servers):
                self._stop(kind)
        after_wait()                                    # no new answer now: what the last ones owed finishes (П3)

    # ── status ──
    def listen(self):
        out = []
        for kind, (srv, _) in sorted(self.servers.items()):
            if srv.unix:
                out.append({"kind": kind, "addr": "unix:%s" % srv.server_address, "scheme": srv.scheme})
                continue
            host, port = srv.server_address[:2]
            out.append({"kind": kind, "addr": "%s:%d" % (conf.host_for_url(host), port), "scheme": srv.scheme})
        for kind, err in sorted(self.errors.items()):
            out.append({"kind": kind, "error": err})
        return out

    def status(self):
        pub = self.cfg["public"]
        if pub["on"] and not self.fingerprint:
            self.fingerprint = conf.fingerprint()
        return {"pid": os.getpid(), "started": int(self.started), "listen": self.listen(), "public": pub["on"],
                "url": conf.public_url(self.cfg) if "public" in self.servers else None,
                "local_url": conf.local_url(self.servers["local"][0].server_address[1]) if "local" in self.servers
                else None,
                "fingerprint": self.fingerprint if pub["on"] else "", "sessions": auth.SESSIONS.count(),
                "require_password": self.cfg["require_password"], "version": STATIC.version,
                "proxy": self.cfg["proxy"]["on"],
                "updated": util.now_iso()}

    def write_status(self):
        try:
            conf.write_status(self.status())
        except OSError as e:
            log_t("journal.srv.status_json", err=e)


PANEL = None


def make(local=(conf.LOCAL_HOST, conf.LOCAL_PORT), proxy_sock=None):
    """Load the modules and start a Panel (tests use port 0)."""
    global PANEL
    load_modules()
    sse.HUB.reset()
    PANEL = Panel(local, proxy_sock).start()
    return PANEL


def _take_notify_socket():
    """NOTIFY_SOCKET out of the environment: only this (main) process tells systemd it is ready. Children inherit
    os.environ — apt, certbot, xray, gokaskad — and any of them speaking sd_notify made systemd log «Got notification
    message from PID …, but reception only permitted for main PID» (T-M6 №9: ~9 % of the journal). NotifyAccess=all
    would hide the line but let any child change the state of the unit (READY, STOPPING, MAINPID)."""
    return os.environ.pop("NOTIFY_SOCKET", None)


def _notify_ready(addr=None):
    """Send systemd READY=1 without importing an optional sd-notify package."""
    if addr is None:
        addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]                       # systemd abstract AF_UNIX address
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(addr)
        sock.sendall(b"READY=1")
        return True
    except OSError as e:
        log_t("journal.srv.ready", err=e.strerror or e)
        return False
    finally:
        if sock is not None:
            sock.close()


RUN_DIR_EVERY = 10                  # s: the panel looks that nginx still passes through /run/kaskad


def _run_dir_ok():
    """/run/kaskad must let nginx (www-data) through to the panel's socket: whatever made it 0700 (3.2.21: the lock of
    the pool, the bot) — the passage comes back at the start and within RUN_DIR_EVERY s, and it is logged once."""
    d = paths.run_dir()
    try:
        before = os.stat(d).st_mode & 0o7777 if os.path.isdir(d) else None
        util.ensure_run_dir(d)
        if before is not None and before & 0o011 != 0o011:
            log_t("journal.srv.run_dir_fixed", dir=d, mode="%04o" % before)
    except OSError as e:
        log_t("journal.srv.run_dir_unchecked", dir=d, err=e.strerror or e)


def serve(port=None):
    """``kaskadctl panel serve``: until SIGTERM/SIGINT; SIGHUP re-reads the settings."""
    notify = _take_notify_socket()                   # before any command runs: children must not inherit it
    # This must precede module loading and every potentially slow startup operation: systemd may receive a reload
    # immediately after ``panel install`` followed by ``panel public on``.
    stop, hup, wake = threading.Event(), threading.Event(), threading.Event()
    # wake: a signal ends the wait of the loop at once — the SIGHUP after an issued certificate is served within
    # milliseconds, not at the next tick (T-M6-qs330b №4)
    signal.signal(signal.SIGTERM, lambda s, f: (stop.set(), wake.set()))
    signal.signal(signal.SIGINT, lambda s, f: (stop.set(), wake.set()))
    signal.signal(signal.SIGHUP, lambda s, f: (hup.set(), wake.set()))
    from . import api
    base = sysrun.default_runner()
    if os.environ.get("INVOCATION_ID"):             # under systemd: long steps outside the unit's limits
        base = ScopedRunner(base)
    counting = CountingRunner(base)
    sysrun._default = counting                       # every command of the core in this process is counted
    api.RUNNER = counting
    _run_dir_ok()
    panel = make((conf.LOCAL_HOST, port or conf.LOCAL_PORT))
    for item in panel.listen():
        if "addr" in item:
            log_t("journal.srv.listening", scheme=item["scheme"], addr=item["addr"])
        else:
            log_t("journal.srv.not_listening", kind=item["kind"], err=item["error"])
    _notify_ready(notify)                             # Type=notify: all configured listeners and status are ready
    ticks, tick_at = 0, time.monotonic() + 1.0
    try:
        while not stop.is_set():
            wake.wait(max(0.0, tick_at - time.monotonic()))
            wake.clear()
            if stop.is_set():
                break
            if time.monotonic() >= tick_at:               # a tick (once a second), not a signal between ticks
                tick_at = time.monotonic() + 1.0
                ticks += 1
                if ticks % RUN_DIR_EVERY == 0:
                    _run_dir_ok()
                if ticks % ADDR_EVERY == 0:
                    try:
                        panel.refresh_addresses()
                    except Exception as e:            # noqa: BLE001 — the listeners stay as they are
                        log_t("journal.srv.addrs_failed", err=e)
            if hup.is_set():
                hup.clear()
                panel.reload()
                log_t("journal.srv.reloaded")
    finally:
        panel.stop()
        try:
            os.unlink(conf.status_path())
        except OSError as e:
            if e.errno != errno.ENOENT:
                pass
    return 0
