"""The page of a person — a secret link that shows ONE gateway user the connection settings of his devices
(docs/k3/SPEC-wave3.md §5). Python 3 stdlib only; the page itself is vanilla JS/CSS (``static/user.*``).

The idea comes from goAmnezia pro (``lib/pages_server.py``): the path IS the key, a wrong key tells nothing about
itself, and the first HTML carries no secrets.

Routes (registered at import, ``auth="page"`` — the server only rate-limits; THIS module checks the token):

    GET  /p/<token>                     the shell: the person's name, a button, no keys, a one-time nonce in <meta>
    POST /p/<token>/keys   {nonce}      the settings of the person's devices (``ops.issue``, in-process), QR as path data
    GET  /p/<token>/file/<grant>/<name> one issued file (.conf .ovpn .mobileconfig .ps1 .crt ...) by a 1-hour grant
    GET  /p/<subtoken>/sub              the subscription for apps: base64 of the user's vless://, hysteria2:// and
                                        amneziawg:// lines (below)
    GET  /p/user.css|user.js|user-i18n-*.js|qr-slides.js|qr.js|give.js|give.css|give-i18n-*.js|icons.js   the static
         files of the page (public: no secrets)
    any  /p/...                         everything else — the 404 of an empty site (and a miss for the ban); GET of a
                                        path that looks like a link (a cut one) — the page of a wrong link

Admin side (``auth="admin"``, for the panel): ``GET|POST /api/gw/users/<id>/page``. The same functions serve the
bot and ``kaskadctl page``: ``enable(user) -> url``, ``rotate(user) -> url``, ``disable(user)``, ``url(user)``,
``status(user)``, ``pages()``. The subscription: ``GET|POST /api/gw/users/<id>/sub``, ``kaskadctl page sub``,
``sub_enable``, ``sub_rotate``, ``sub_disable``, ``sub_url``, ``sub_status``, ``subs()``.

Security (what each point defends):
  * the token is 43 chars of base64url (256 bits), kept in ``secrets.json`` as ``user.<id>.page`` (0600); the
    lookup compares it with every stored token in constant time, without an early exit;
  * a link that opens nothing — a wrong, replaced, switched-off or cut token, a deleted user's — ONE and the same
    page «Ссылка больше не действует — попросите новую» (``gone_page()``, 404; nothing in it depends on the token,
    so nothing tells whether it ever existed; stand T-G1b, N8); any other path or method — ONE 404 of an empty site
    (``not_found()``); 10 misses from one address in 5 minutes — 15 minutes of the same answers for everything;
  * the first HTML has no keys (preview robots and mail scanners fetch pages); after a real gesture (the page
    checks ``isTrusted``) it POSTs ``{nonce}`` — one-time, in memory, 5 minutes — and only then gets the settings;
    ``GET .../keys`` is 405; link-preview robots get 404 for ``keys``;
  * files: a grant of 1 hour per (device, inbound), issued with the keys, in memory; the file is built again on
    download (always current); no grant — 404;
  * ``Cache-Control: no-store``, ``X-Robots-Tag: noindex``, ``Referrer-Policy: no-referrer``, CSP
    ``default-src 'none'`` + own scripts/styles/connect only; QR codes are drawn from path data, not HTML;
  * a token is never logged and never put in a URL query; the logs carry names and ids.

QR: ``qrencode -t SVG -m 0 -o -`` through the runner, the text on STDIN (never in argv); the SVG is read into a
module matrix and sent as compact path data (``{"n", "d"}``) — the page draws it in a white tile with a quiet zone.
No qrencode — no QR, the page says so and the other items (link, file) stay; ``qr_status()`` tells the panel how to
install it.

The subscription for apps (v2RayTun, Hiddify, v2rayN, Streisand, incy; docs/k3/SPEC-vless-inbound.md §8): a token of
its own per person (``user.<id>.sub`` in ``secrets.json``, the same strength, never the page's), on/rotate/off apart
from the page. ``GET /p/<subtoken>/sub`` -> 200 ``text/plain``, base64 (standard, padded) of the ``vless://`` and
``hysteria2://`` links of the user's active devices, one per line (``SUB_KINDS``: only links the apps understand; SOCKS5
gives ``socks5://``, not the ``socks://`` of v2rayN — left out), then the .conf of their AmneziaWG and WireGuard as
``amneziawg://<base64url .conf>#<name>`` (``SUB_CONF_KINDS``: the line of incy, as goAmnezia Pro all.txt; the apps that
do not know it skip the line); 3.3.7.15 F13: then the same lines on the IPv6 of «принимать и выдавать» (``_lines6``,
name + « · IPv6»); ``profile-title`` and ``profile-update-interval`` for the apps. The deliberate exception to «no keys by GET»: the token is a secret of its own and opens nothing else. A
paused user or no device with these protocols — 200 with an empty body (the app says «empty»; not a miss); an unknown,
replaced or revoked token, a robot of link previews — the 404 and the miss of the page (one ban).

The coupling with the panel's public mode: ``panel_access()`` reads ``<panel dir>/access.json`` (``public``,
``port``, ``scheme``, ``host``); without it a link is only a path and ``access_note()`` says the page needs the
public mode (WP-P1/integration: write that file when the mode changes, or change ``panel_access`` here).
The generic 404 of the server for unknown paths must be ``not_found()`` — else a wrong token is told apart.
"""

import base64
import collections
import hashlib
import hmac
import html
import ipaddress
import os
import re
import threading
import time
import urllib.parse

from .. import paths, snapshot, state, sysrun, util
from .. import qr as qrmod
from .. import secrets as ksecrets
from . import lang as weblang
from . import portoff
from ..errors import KaskadError, LockedError
from ..i18n import tr
from ..gw import install_apps, issue_os, model, ops, registry
from .routes import HttpError, Response, json_response, route

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
SECRET_KEY = "user.%s.page"                          # secrets.json: the token of the person's page
SECRET_KEY_RE = re.compile(r"^user\.(u-[0-9a-f]{8})\.page$")
SUB_KEY = "user.%s.sub"                              # secrets.json: the token of the person's subscription for apps
SUB_KEY_RE = re.compile(r"^user\.(u-[0-9a-f]{8})\.sub$")
RANDOM_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")       # nonce and grant: 24 random bytes in base64url
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

NONCE_TTL = 300.0           # a nonce of the shell lives 5 minutes
GRANT_TTL = 3600.0          # a file grant — 1 hour
BAN_FAILURES = 10           # misses from one address ...
BAN_WINDOW = 300.0          # ... within 5 minutes ...
BAN_SECONDS = 900.0         # ... — 15 minutes of silence (the same 404)
TABLE_MAX = 4096            # a scanner must not grow the tables of the process
LOCK_WAIT = 4.0             # seconds the page waits for the state lock (a long `gw apply` holds it)
BUILD_WAIT = 5.0            # seconds a request waits for one of the BUILD_PARALLEL places
BUILD_PARALLEL = 3          # keys are built by at most 3 requests at a time
RETRY_AFTER = 3             # seconds, in 503 and the body of busy answers
QR_MAX_SVG = 8 * 1024 * 1024
QR_TIMEOUT = 15
QRENCODE = "qrencode"

FILE_FORMATS = ("conf", "ovpn", "mobileconfig", "ps1")
FILE_TYPES = {".mobileconfig": "application/x-apple-aspen-config", ".crt": "application/x-x509-ca-cert",
              ".ovpn": "application/x-openvpn-profile"}
ASSETS = {"user.css": "text/css; charset=utf-8", "user.js": "application/javascript; charset=utf-8",
          "user-i18n-ru.js": "application/javascript; charset=utf-8",
          "user-i18n-en.js": "application/javascript; charset=utf-8",
          "qr-slides.js": "application/javascript; charset=utf-8",          # the QR of a key in parts (3.3.2 №10)
          "qr.js": "application/javascript; charset=utf-8",                 # the QR of the page's own link (3.3.3 №38)
          # the same «Выдать настройки» as the panel (3.3.2, design k3/design/issue-qr): the component, its words, the icons
          "give.js": "application/javascript; charset=utf-8", "give.css": "text/css; charset=utf-8",
          "give-i18n-ru.js": "application/javascript; charset=utf-8",
          "give-i18n-en.js": "application/javascript; charset=utf-8",
          "icons.js": "application/javascript; charset=utf-8"}

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP,
    "Cross-Origin-Resource-Policy": "same-origin",
}

# The body of a 404 of an empty site: the answer to a wrong token must not differ from the answer to any other path.
# It names no server (docs/k3/SPEC-wave4.md §3.8): behind nginx the site's own 404 is nginx's business.
NOT_FOUND_BODY = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n"
                  b"<center><h1>404 Not Found</h1></center>\r\n"
                  b"</body>\r\n</html>\r\n")

# Link-preview and mail-scanning robots: they get no nonce-bound answers and no name.
BOT_RE = re.compile(
    r"(telegrambot|vkshare|facebookexternalhit|facebot|slackbot|slack-imgproxy|discordbot|twitterbot|"
    r"linkedinbot|skypeuripreview|mail\.ru_bot|googlebot|bingbot|yandex[a-z]*bot|redditbot|pinterestbot|"
    r"embedly|iframely|bot/|crawler|spider)|^whatsapp/", re.I)

_mono = time.monotonic          # tests patch it
_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ── the one 404 and the headers of the real answers ─────────────────────────

def not_found():
    """The 404 of an empty site. ``Server``, ``Date`` and ``Content-Length`` are the server's business."""
    return Response(404, NOT_FOUND_BODY, {"Content-Type": "text/html"})


def _secure(resp):
    resp.headers.update(SECURE_HEADERS)
    return resp


def _json(status, obj):
    return _secure(json_response(obj, status=status))


def _fail(status, reason, text, **extra):
    body = {"ok": False, "reason": reason, "error": text}
    body.update(extra)
    resp = _json(status, body)
    if status == 503:
        resp.headers["Retry-After"] = str(RETRY_AFTER)
    return resp


# ── guards in memory: misses and the ban, nonces, file grants ───────────────

def ip_key(ip):
    """The address a ban is kept for: an IPv4 address as it is, an IPv6 one — its /64 (one host of a /64 has billions
    of addresses to scan from), an IPv4-mapped IPv6 address — the IPv4 one."""
    try:
        a = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return str(ip)
    if a.version == 6:
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return a.exploded[:19] + "::/64"
    return str(a)


class Guards(object):
    """Everything the page keeps in memory (lost on restart — an open tab then says «обновите страницу»)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._misses = {}                        # ip -> [monotonic stamps]
            self._banned = {}                        # ip -> until
            self._nonces = collections.OrderedDict()  # nonce -> (page key, expires)
            self._grants = collections.OrderedDict()  # grant -> (page key, device, inbound, os, expires)
            self._ban_logged = -1e9

    # misses
    def banned(self, ip):
        ip = ip_key(ip)
        with self._lock:
            until = self._banned.get(ip)
            if until is None:
                return False
            if until <= _mono():
                del self._banned[ip]
                return False
            return True

    def miss(self, ip):
        """Count a miss of ``ip``; True when it has just been banned."""
        ip = ip_key(ip)
        now = _mono()
        with self._lock:
            stamps = [t for t in self._misses.get(ip, ()) if now - t < BAN_WINDOW]
            stamps.append(now)
            self._misses[ip] = stamps
            if len(self._misses) > TABLE_MAX:
                for k in [k for k, v in self._misses.items() if not v or now - v[-1] >= BAN_WINDOW]:
                    del self._misses[k]
                while len(self._misses) > TABLE_MAX:
                    self._misses.pop(next(iter(self._misses)))
            for k in [k for k, until in self._banned.items() if until <= now]:
                del self._banned[k]
            while len(self._banned) > TABLE_MAX:
                self._banned.pop(next(iter(self._banned)))
            if len(stamps) >= BAN_FAILURES:
                self._banned[ip] = now + BAN_SECONDS
                self._misses.pop(ip, None)
                return True
            return False

    def ban_log_due(self):
        """True at most once in 10 s: a scan from many addresses must not flood the log."""
        now = _mono()
        with self._lock:
            if now - self._ban_logged < 10.0:
                return False
            self._ban_logged = now
            return True

    # nonces
    @staticmethod
    def _new_random():
        return base64.urlsafe_b64encode(os.urandom(24)).decode("ascii")

    def nonce_issue(self, page):
        nonce = self._new_random()
        now = _mono()
        with self._lock:
            while self._nonces and next(iter(self._nonces.values()))[1] <= now:
                self._nonces.popitem(last=False)
            self._nonces[nonce] = (page, now + NONCE_TTL)
            while len(self._nonces) > TABLE_MAX:
                self._nonces.popitem(last=False)
        return nonce

    def nonce_take(self, page, nonce):
        """True once for a nonce of THIS page that has not expired; the nonce is gone after the call."""
        if not isinstance(nonce, str) or not RANDOM_RE.match(nonce):
            return False
        with self._lock:
            item = self._nonces.get(nonce)
            if item is None or item[0] != page:
                return False                         # not this page's: left alone (its owner may still use it)
            del self._nonces[nonce]
        return item[1] > _mono()

    # file grants
    def grant_issue(self, page, device, inbound, os_name):
        grant = self._new_random()
        now = _mono()
        with self._lock:
            while self._grants and next(iter(self._grants.values()))[4] <= now:
                self._grants.popitem(last=False)
            self._grants[grant] = (page, device, inbound, os_name, now + GRANT_TTL)
            while len(self._grants) > TABLE_MAX:
                self._grants.popitem(last=False)
        return grant

    def grant_get(self, page, grant):
        """(device, inbound, os) of a live grant of THIS page, else None (an expired one is dropped)."""
        if not isinstance(grant, str) or not RANDOM_RE.match(grant):
            return None
        with self._lock:
            item = self._grants.get(grant)
            if item is None:
                return None
            if item[4] <= _mono():
                del self._grants[grant]
                return None
        return (item[1], item[2], item[3]) if item[0] == page else None


GUARDS = Guards()
_BUILD = threading.BoundedSemaphore(BUILD_PARALLEL)


# ── tokens ──────────────────────────────────────────────────────────────────

def new_token():
    """43 characters of base64url — 32 random bytes (what ``token_urlsafe(32)`` gives; the stdlib module
    ``secrets`` is not used here on purpose, like in ``kaskad.secrets``)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def valid_token(value):
    return isinstance(value, str) and bool(TOKEN_RE.match(value))


def page_key(token):
    """What the memory tables hold instead of the token itself."""
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


_tokens_cache = (None, {}, {})      # (the parsed secrets file it was made from, {user id: page}, {user id: sub})


def _all_tokens():
    """({user id: token of the page}, {user id: token of the subscription}) of those that are on. Made from the
    secrets file the snapshot cache holds and kept until that file changes (a scanner's thousand misses do not parse
    a thousand secrets each). Read-only."""
    global _tokens_cache
    doc = snapshot.read_json(paths.secrets_file())
    cached = _tokens_cache
    if doc is not None and cached[0] is doc:
        return cached[1], cached[2]
    values = doc.get("values") if isinstance(doc, dict) else None
    pages, subs = {}, {}
    if isinstance(values, dict):
        for key, val in values.items():
            if not isinstance(key, str) or not valid_token(val):
                continue
            m = SECRET_KEY_RE.match(key)
            if m:
                pages[m.group(1)] = val
                continue
            m = SUB_KEY_RE.match(key)
            if m:
                subs[m.group(1)] = val
    _tokens_cache = (doc, pages, subs)
    return pages, subs


def _stored_tokens():
    """{user id: token} of the pages that are on."""
    return _all_tokens()[0]


def _stored_sub_tokens():
    """{user id: token} of the subscriptions that are on."""
    return _all_tokens()[1]


def find_token(token, sub=False):
    """The id of the user the token belongs to (a page's, or with ``sub`` a subscription's — never the other kind),
    else None. Compares with every stored token in constant time (no early exit): the time says nothing about which
    of them was close."""
    if not valid_token(token):
        return None
    cand = token.encode("ascii")
    found = None
    for uid, stored in (_stored_sub_tokens() if sub else _stored_tokens()).items():
        if hmac.compare_digest(stored.encode("ascii"), cand):
            found = uid
    return found


def _user_by_id(uid):
    st = snapshot.read_json(paths.state())
    for u in model.users(st if isinstance(st, dict) else {}):
        if u.get("id") == uid:
            return u
    return None


def authorize(req, token=None, sub=False):
    """The user the token of the request's path belongs to (``sub`` — a subscription's token), or None — then the
    caller answers ``not_found()``. A banned address gets None without being looked at; a miss is counted and may
    start a ban (one ban for the page and the subscription)."""
    ip = req.client_ip or "-"
    if GUARDS.banned(ip):
        return None
    token = token if token is not None else req.match.group("token")
    uid = find_token(token, sub=sub)
    user = _user_by_id(uid) if uid else None
    if user is None:
        if GUARDS.miss(ip) and GUARDS.ban_log_due():
            util.log_tr("journal.page.banned", ip=ip, min=int(BAN_SECONDS // 60), n=int(BAN_FAILURES))
        return None
    if getattr(req, "listener", None) == "public":
        portoff.mark_port_link()        # someone opens a link of people on the port (one given before 3.3.7.5 too)
    return user


# ── the link: panel access, qrencode, the admin functions ───────────────────

def panel_access():
    """Where the panel can be reached from outside: ``{"public", "scheme", "host", "port"}``. The panel's public
    mode (WP-P1) is written to ``<panel dir>/access.json`` (``public``, ``port``, ``scheme``, optional ``host`` —
    a domain); ``panel.json`` and ``config.json`` of that directory are looked at too. Without it — not public."""
    acc = {"public": False, "scheme": "https", "host": "", "port": None}
    for name in ("access.json", "panel.json", "config.json"):
        doc = snapshot.read_json(os.path.join(paths.panel_dir(), name))
        if not isinstance(doc, dict):
            continue
        flag = next((doc[k] for k in ("public", "public_mode", "open") if k in doc), None)
        if flag is None:
            continue
        acc["public"] = bool(flag)
        port = doc.get("port", doc.get("public_port"))
        acc["port"] = port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else None
        acc["scheme"] = "http" if doc.get("scheme") == "http" else "https"
        acc["host"] = doc["host"].strip() if isinstance(doc.get("host"), str) else ""
        break
    return acc


# The notes are catalog keys (3.3.2: the language changes on the fly): ``tr`` where a document is built. The old
# names ``NOTE_CLOSED`` etc. still give the text in the current language (``__getattr__`` below).
_NOTE_KEYS = {"NOTE_CLOSED": "panel.userpage.noteClosed", "NOTE_NO_HOST": "panel.userpage.noteNoHost",
              "NOTE_NO_QR": "panel.userpage.noteNoQr", "NOTE_SUB_EMPTY": "panel.userpage.noteSubEmpty",
              "NOTE_SUB_PAUSED": "panel.userpage.noteSubPaused"}


def __getattr__(name):
    """``userpage.NOTE_*`` — the text of the note in the current language (PEP 562: looked up on every access)."""
    if name in _NOTE_KEYS:
        return tr(_NOTE_KEYS[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _format_url(acc, host, token):
    scheme = "http" if acc.get("scheme") == "http" else "https"
    port = acc.get("port")
    h = host if not (":" in host and not host.startswith("[")) else "[%s]" % host     # an IPv6 literal
    netloc = h if (not port or port == {"https": 443, "http": 80}[scheme]) else "%s:%d" % (h, port)
    return "%s://%s/p/%s" % (scheme, netloc, token)


def access_note(st=None):
    """Why a link cannot be opened by a person yet ("" — it can): the panel is closed, or the node has no address."""
    acc = panel_access()
    if not acc["public"]:
        return tr("panel.userpage.noteClosed")
    host = acc["host"] or model.public_host(st if st is not None else (snapshot.read_json(paths.state()) or {}))
    return "" if host else tr("panel.userpage.noteNoHost")


def qr_status(runner=None):
    """{"available": bool, "note": str} — whether the node can draw QR codes for the page."""
    runner = runner or sysrun.default_runner()
    ok = bool(runner.which(QRENCODE))
    return {"available": ok, "note": "" if ok else tr("panel.userpage.noteNoQr")}


def _token_of(uid):
    return _stored_tokens().get(uid)


def _resolve(ref):
    """The user (dict of state.json) by id or name; KaskadError when there is none."""
    return model.get_user(state.load(), ref)


def _link(uid, token, st=None):
    """(path, url): the absolute link when the panel is public and the node has an address, else url is None."""
    path = "/p/%s" % token
    acc = panel_access()
    if not acc["public"]:
        return path, None
    host = acc["host"] or model.public_host(st if st is not None else (snapshot.read_json(paths.state()) or {}))
    if not host:
        return path, None
    if acc.get("port") not in (None, {"https": 443, "http": 80}.get(acc.get("scheme"), 443)):
        portoff.mark_port_link()        # 3.3.7.5 круг 3: a link with the port given — the port never goes off by itself
    return path, _format_url(acc, host, token)


def status(ref):
    """What the admin sees for one user: ``{"user": {...}, "enabled", "path", "url", "public", "note", "qr"}``.
    The link carries the token — this is the only place besides ``url``/``enable``/``rotate`` that shows it."""
    st = state.load()
    u = model.get_user(st, ref)
    token = _token_of(u["id"])
    acc = panel_access()
    path, link = _link(u["id"], token, st) if token else (None, None)
    note = access_note(st) if token else ""
    qr = qr_status()
    return {"user": {"id": u["id"], "name": u["name"], "enabled": u.get("enabled", True) is not False},
            "enabled": bool(token), "path": path, "url": link, "public": acc["public"], "note": note,
            "qr": qr["available"], "qr_note": qr["note"]}


def url(ref):
    """The link to give the person: absolute when the panel is public (https://<node>:<port>/p/<token>), else just
    the path ``/p/<token>`` (see ``access_note``); None when the page is off. KaskadError for an unknown user."""
    u = _resolve(ref)
    token = _token_of(u["id"])
    if not token:
        return None
    path, link = _link(u["id"], token)
    return link or path


def _prune(sec, st):
    """Tokens (of pages and subscriptions) of users that are gone leave the file (``gw user del`` removes them too)."""
    alive = set(u.get("id") for u in model.users(st))
    for key in list(sec):
        m = SECRET_KEY_RE.match(key) or SUB_KEY_RE.match(key)
        if m and m.group(1) not in alive:
            sec.delete(key)


def _change(ref, fn):
    """Under the state lock: ``fn(sec, user)`` changes the secrets; saved; the read cache is dropped."""
    with state.locked():
        st = state.load()
        u = model.get_user(st, ref)
        sec = ksecrets.load()
        out = fn(sec, u)
        _prune(sec, st)
        sec.save()
    snapshot.bump()
    return u, out


def enable(ref):
    """Switch the person's page on (a token is made once; on again — the same link). Returns ``url(ref)``."""
    def fn(sec, u):
        key = SECRET_KEY % u["id"]
        if valid_token(sec.get(key)):
            return False
        sec.set(key, new_token())
        return True
    u, made = _change(ref, fn)
    if made:
        util.log_tr("journal.page.on", name=u["name"], id=u["id"])
    return url(u["id"])


def rotate(ref):
    """A new link: the old one stops working at once (404). Switches the page on if it was off."""
    def fn(sec, u):
        sec.set(SECRET_KEY % u["id"], new_token())
    u, _ = _change(ref, fn)
    util.log_tr("journal.page.rotated", name=u["name"], id=u["id"])
    return url(u["id"])


def disable(ref):
    """Switch the page off: the link dies. True if it was on."""
    def fn(sec, u):
        return sec.delete(SECRET_KEY % u["id"])
    u, was = _change(ref, fn)
    if was:
        util.log_tr("journal.page.off", name=u["name"], id=u["id"])
    return bool(was)


def pages():
    """Users with a page, by name: ``[{"user", "name", "enabled", "path", "url"}]`` (``enabled`` — the PERSON is
    enabled; a disabled person's page shows «доступ приостановлен»). Stale tokens of deleted users are skipped."""
    st = snapshot.read_json(paths.state())
    st = st if isinstance(st, dict) else {}
    tokens = _stored_tokens()
    out = []
    for u in model.users(st):
        token = tokens.get(u.get("id"))
        if not token:
            continue
        path, link = _link(u["id"], token, st)
        out.append({"user": u["id"], "name": u.get("name", ""), "enabled": u.get("enabled", True) is not False,
                    "path": path, "url": link})
    out.sort(key=lambda r: (str(r["name"]).casefold(), r["user"]))
    return out


def page_users():
    """{user id: True} for the users whose page is on — the panel adds the flag to its list of people."""
    return dict((uid, True) for uid in _stored_tokens())


# ── the subscription for apps: the admin side ──────────────────────────────

SUB_KINDS = model.SUB_KINDS                  # inbound -> the schemes of its links apps take by a subscription
SUB_CONF_KINDS = model.SUB_CONF_KINDS        # inbounds whose .conf goes in as amneziawg://<base64url .conf>#<name>
SUB_INTERVAL = 12                            # hours: profile-update-interval


def _sub_kinds_of(st, device):
    """The subscription's inbounds of an active device that are installed and enabled."""
    usable = set(model.enabled_kinds(st))
    return [k for k in device.get("access") or [] if k in SUB_KINDS and k in usable and registry.get(k) is not None]


def _sub_conf_kinds_of(st, device):
    """The inbounds of the WireGuard family of a device that go into the subscription as ``amneziawg://`` lines
    (``SUB_CONF_KINDS``: installed, enabled, granted), 3.1 → 2.0 → 1.5 → WireGuard."""
    usable, acc = set(model.enabled_kinds(st)), set(device.get("access") or [])
    return [k for k in SUB_CONF_KINDS if k in acc and k in usable and registry.get(k) is not None]


def _sub_devices(st, uid):
    """Active devices of the user that the subscription carries (no secrets: for the counter of the admin)."""
    return [d for d in model.devices(st, user=uid)
            if model.device_active(st, d) and (_sub_kinds_of(st, d) or _sub_conf_kinds_of(st, d))]


def _sub_link(token, st=None):
    path, link = _link(None, token, st)
    return path + "/sub", (link + "/sub" if link else None)


def sub_status(ref):
    """What the admin sees of the subscription: ``{"user", "enabled", "path", "url", "public", "note", "devices",
    "empty_note", "qr", "qr_note"}``. Like ``status`` — the link carries the token. The same for the panel, CLI
    (``page sub … --json``) and MCP (``page_sub_*``); the apps of the subscription — only in ``admin_sub_doc``."""
    st = state.load()
    u = model.get_user(st, ref)
    token = _stored_sub_tokens().get(u["id"])
    acc = panel_access()
    path, link = _sub_link(token, st) if token else (None, None)
    person_on = u.get("enabled", True) is not False
    count = len(_sub_devices(st, u["id"])) if person_on else 0
    empty = "" if count else tr("panel.userpage.noteSubPaused" if not person_on else "panel.userpage.noteSubEmpty")
    qr = qr_status()
    return {"user": {"id": u["id"], "name": u["name"], "enabled": person_on},
            "enabled": bool(token), "path": path, "url": link, "public": acc["public"],
            "note": access_note(st) if token else "", "devices": count, "empty_note": empty,
            "interval": SUB_INTERVAL, "qr": qr["available"], "qr_note": qr["note"]}


def admin_sub_doc(ref):
    """``sub_status`` + ``apps`` for the panel (GET/POST ``/api/gw/users/<id>/sub``): the apps that take the
    subscription, ``{platform: block}`` of the catalog (``install_apps.sub_blocks``) — the tab «Подписка» of «Выдать
    настройки» lists them as «Установите приложение» of a key. Not in CLI/MCP: ~11 KB of catalog on every call."""
    return dict(sub_status(ref), apps=install_apps.sub_blocks())


def sub_url(ref):
    """The link to add in an app (absolute when the panel is public, else the path); None when it is off."""
    u = _resolve(ref)
    token = _stored_sub_tokens().get(u["id"])
    if not token:
        return None
    path, link = _sub_link(token)
    return link or path


def sub_enable(ref):
    """Switch the subscription on (a token is made once; on again — the same link). Returns ``sub_url(ref)``."""
    def fn(sec, u):
        key = SUB_KEY % u["id"]
        if valid_token(sec.get(key)):
            return False
        sec.set(key, new_token())
        return True
    u, made = _change(ref, fn)
    if made:
        util.log_tr("journal.sub.on", name=u["name"], id=u["id"])
    return sub_url(u["id"])


def sub_rotate(ref):
    """A new link: the old one stops working at once (404). Switches the subscription on if it was off."""
    def fn(sec, u):
        sec.set(SUB_KEY % u["id"], new_token())
    u, _ = _change(ref, fn)
    util.log_tr("journal.sub.rotated", name=u["name"], id=u["id"])
    return sub_url(u["id"])


def sub_disable(ref):
    """Revoke the subscription: the link dies. True if it was on."""
    def fn(sec, u):
        return sec.delete(SUB_KEY % u["id"])
    u, was = _change(ref, fn)
    if was:
        util.log_tr("journal.sub.off", name=u["name"], id=u["id"])
    return bool(was)


def sub_users():
    """{user id: True} for the users whose subscription is on — the mark in the panel's list of people."""
    return dict((uid, True) for uid in _stored_sub_tokens())


def subs():
    """Users with a subscription, by name: ``[{"user", "name", "enabled", "devices"}]`` — no links."""
    st = snapshot.read_json(paths.state())
    st = st if isinstance(st, dict) else {}
    tokens = _stored_sub_tokens()
    out = []
    for u in model.users(st):
        if not tokens.get(u.get("id")):
            continue
        on = u.get("enabled", True) is not False
        out.append({"user": u["id"], "name": u.get("name", ""), "enabled": on,
                    "devices": len(_sub_devices(st, u["id"])) if on else 0})
    out.sort(key=lambda r: (str(r["name"]).casefold(), r["user"]))
    return out


# ── the operating system of the visitor ─────────────────────────────────────

def detect_os(user_agent):
    """A platform of the gateway model (``ios``, ``android``, ``androidtv``, ``windows``, ``macos``, ``linux``) from
    the User-Agent, else ``other``. iPadOS 13+ says «Macintosh» like a Mac — it is told apart on the page (a Mac
    has no touch screen): ``navigator.maxTouchPoints`` upgrades ``macos`` to ``ios`` there."""
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "ios"
    if "android" in ua:
        if any(w in ua for w in (" tv", "smart-tv", "smarttv", "bravia", "shield", "mibox", "googletv")) \
                or re.search(r"\baft[a-z0-9]{1,5}\b", ua):
            return "androidtv"
        return "android"
    if "windows" in ua:
        return "windows"
    if "macintosh" in ua or "mac os x" in ua:
        return "macos"
    if "cros" in ua or "linux" in ua or "x11" in ua:
        return "linux"
    return "other"


def is_bot(user_agent):
    return bool(BOT_RE.search(user_agent or ""))


# ── QR: qrencode -t SVG -> module matrix -> compact path data ───────────────

_RE_VIEWBOX = re.compile(r'viewBox="\s*0\s+0\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*"')
_RE_TAG = re.compile(r"<(rect|use|path|g)\b([^>]*?)/?>", re.S)
_RE_ATTR = re.compile(r'([A-Za-z_:][\w:.-]*)\s*=\s*"([^"]*)"')
_RE_TRANSLATE = re.compile(r"translate\(\s*(-?[\d.]+)\s*[, ]\s*(-?[\d.]+)\s*\)")
_RE_PATH_CMD = re.compile(r"([MmHhVvZz])\s*(-?\d+(?:\.\d+)?)?(?:\s*,\s*|\s+)?(-?\d+(?:\.\d+)?)?")


def _num(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _is_light(fill):
    f = (fill or "").strip().lower()
    return f in ("#fff", "#ffffff", "white", "none", "")


def _path_modules(d, tx, ty, put):
    """Modules of a qrencode ``--svg-path`` style path: ``M x,y h len`` strokes of one module height."""
    x = y = 0.0
    for m in _RE_PATH_CMD.finditer(d):
        cmd, a, b = m.group(1), m.group(2), m.group(3)
        if cmd == "M" and a is not None and b is not None:
            x, y = float(a), float(b)
        elif cmd == "m" and a is not None and b is not None:
            x, y = x + float(a), y + float(b)
        elif cmd == "H" and a is not None:
            put(x + tx, y + ty - 0.5, float(a) - x)
            x = float(a)
        elif cmd == "h" and a is not None:
            put(x + tx, y + ty - 0.5, float(a))
            x += float(a)
        elif cmd == "V" and a is not None:
            y = float(a)
        elif cmd == "v" and a is not None:
            y += float(a)


def parse_qr_svg(svg):
    """``{"n": modules per side, "d": path data}`` from the SVG of ``qrencode -t SVG``, or None when the text is not
    a QR code we can read. Reads the three shapes qrencode has used: ``<rect width=1>`` per module (also ``--rle``
    runs), ``<use>`` of a one-module symbol, one ``<path>`` of ``M x,y h len`` strokes (``--svg-path``). The quiet
    zone is cut off (the page draws its own). The result is checked: a square of 21..177 modules (version 1..40)
    whose top-left finder pattern is intact."""
    if not isinstance(svg, str) or not svg or len(svg) > QR_MAX_SVG:
        return None
    vb = _RE_VIEWBOX.search(svg)
    if not vb or vb.group(1) != vb.group(2):
        return None
    size = _num(vb.group(1))
    side = int(size)
    if size != side or not 21 <= side <= 400:
        return None
    grid = [bytearray(side) for _ in range(side)]

    def put(x, y, w, h=1.0):
        xi, yi, wi, hi = int(round(x)), int(round(y)), int(round(w)), int(round(h))
        for yy in range(max(yi, 0), min(yi + hi, side)):
            row = grid[yy]
            for xx in range(max(xi, 0), min(xi + wi, side)):
                row[xx] = 1

    tx = ty = 0.0
    unit = (1.0, 1.0)
    for tag in _RE_TAG.finditer(svg):
        kind = tag.group(1)
        attrs = dict(_RE_ATTR.findall(tag.group(2)))
        tr = _RE_TRANSLATE.search(attrs.get("transform", ""))
        if kind == "g":
            if tr and attrs.get("id") == "Pattern":
                tx, ty = _num(tr.group(1)), _num(tr.group(2))
        elif kind == "rect":
            if "id" in attrs:                                            # <defs><rect id="p" .../></defs>
                unit = (_num(attrs.get("width"), 1.0), _num(attrs.get("height"), 1.0))
            elif not _is_light(attrs.get("fill", "#000000")) and not (
                    _num(attrs.get("width")) >= side and _num(attrs.get("height")) >= side):
                put(_num(attrs.get("x")) + tx, _num(attrs.get("y")) + ty, _num(attrs.get("width"), 1.0),
                    _num(attrs.get("height"), 1.0))
        elif kind == "use":
            put(_num(attrs.get("x")) + tx, _num(attrs.get("y")) + ty, unit[0], unit[1])
        elif kind == "path" and "d" in attrs:
            ptx, pty = (_num(tr.group(1)), _num(tr.group(2))) if tr else (0.0, 0.0)
            _path_modules(attrs["d"], ptx, pty, put)
    ys = [y for y in range(side) if any(grid[y])]
    xs = [x for x in range(side) if any(grid[y][x] for y in ys)]
    if not ys or not xs:
        return None
    n = xs[-1] - xs[0] + 1
    if ys[-1] - ys[0] + 1 != n or not 21 <= n <= 177 or (n - 17) % 4:
        return None
    rows = [grid[y][xs[0]:xs[0] + n] for y in range(ys[0], ys[0] + n)]
    if any(not rows[0][i] or not rows[6][i] or not rows[i][0] or not rows[i][6] for i in range(7)):
        return None
    return {"n": n, "d": _matrix_path(rows, n)}


def _matrix_path(rows, n):
    """Path data of the dark modules: a horizontal stroke per run, ``M x y.5 h len`` (relative ``m`` inside a row);
    drawn with ``stroke-width: 1`` (the half module centres the stroke on the row)."""
    parts = []
    for y in range(n):
        row, x, end, first = rows[y], 0, 0, True
        while x < n:
            if not row[x]:
                x += 1
                continue
            e = x
            while e < n and row[e]:
                e += 1
            if first:
                parts.append("M%d %d.5h%d" % (x, y, e - x))
                first = False
            else:
                parts.append("m%d 0h%d" % (x - end, e - x))
            end = x = e
    return "".join(parts)


def qr_matrix(runner, text):
    """The QR code of ``text`` as ``{"n", "d"}`` or None (no qrencode, the text does not fit, odd output). The
    text goes to qrencode on stdin — it is a secret and must not appear in the argv of a process."""
    if not text or not isinstance(text, str):
        return None
    # the byte mode (-8) and the level as the panel draws it (qr.version_of: the smallest version, "M" only when it
    # fits the same one) — a code of /p/ is of the very version of the panel and the wizard: the same limit of the
    # camera, not one version less in a mixed mode (3.3.3 №38)
    level = qrmod.version_of(text)[1] or "L"
    res = runner.run([QRENCODE, "-8", "-l", level, "-t", "SVG", "-m", "0", "-o", "-"], input=text, timeout=QR_TIMEOUT)
    if not res.ok:
        return None
    return parse_qr_svg(res.out)


# ── the settings of the person's devices ────────────────────────────────────

def _file_name(item):
    name = item.get("filename")
    return name if isinstance(name, str) and FILE_NAME_RE.match(name) else None


def _item_out(runner, token, grant, item, qr_ok):
    """One entry of an issuance for the page: the format, the title, the notes and — by format — the text
    (``body``), the fields, the QR codes, the file link."""
    fmt = str(item.get("format") or "")
    out = {"format": fmt, "title": str(item.get("title") or ""),
           "notes": [str(n) for n in (item.get("notes") or []) if n]}
    body = item.get("body")
    if fmt == "qr":
        texts = [t for t in (body if isinstance(body, list) else [body]) if isinstance(t, str) and t]
        codes = [qr_matrix(runner, t) for t in texts] if qr_ok else []
        drawn = bool(texts) and len(codes) == len(texts) and all(codes)
        out["qr"] = codes if drawn else None
        # for which app and in which format (the page names it under the code): by the text, as the panel does it
        out["qr_for"] = qrmod.qr_for(texts[0]) if texts else "text"
        if not drawn:
            out["texts"] = texts                       # kept only when it is the one way to the text (_qr_fallbacks)
    elif fmt == "fields":
        out["fields"] = [[str(r[0]), str(r[1])] for r in (body if isinstance(body, list) else [])
                         if isinstance(r, (list, tuple)) and len(r) >= 2]
    else:
        out["body"] = body if isinstance(body, str) else ""
        name = _file_name(item)
        if name and fmt in FILE_FORMATS and out["body"]:
            out["filename"] = name
            out["file"] = "/p/%s/file/%s/%s" % (token, grant, name)
    return out


def _install_out(runner, block, qr_ok):
    """«Установите приложение» for the page: the block of ``ops.issue`` with the QR of every store link (``{"n", "d"}``,
    drawn by qrencode here as the codes of the keys; the page itself draws only the QR of its own address, KaskadQR,
    3.3.3 №38) — scanned from a computer by the phone."""
    if not isinstance(block, dict):
        return None
    out = {"supported": block.get("supported") is not False, "builtin": str(block.get("builtin") or ""),
           "text": str(block.get("text") or ""), "apps": []}
    for a in block.get("apps") or []:
        if not isinstance(a, dict):
            continue
        links = []
        for ln in a.get("links") or []:
            url = str((ln or {}).get("url") or "")
            if not url.startswith("https://"):
                continue
            links.append({"kind": str(ln.get("kind") or ""), "label": str(ln.get("label") or ""), "url": url,
                          "note": str(ln.get("note") or ""), "qr": qr_matrix(runner, url) if qr_ok else None})
        icon = str(a.get("icon") or "")
        out["apps"].append({"id": str(a.get("id") or ""), "name": str(a.get("name") or ""),
                            "recommended": bool(a.get("recommended")), "note": str(a.get("note") or ""), "links": links,
                            # the icon ships with the product: /p/apps/<id>.png, never a store's server
                            "icon": "/p/" + icon if ICON_PAT.match(icon) else "",
                            "fakes": str(a.get("fakes") or ""), "unverified": str(a.get("unverified") or "")})
    return out


def _support_out(sup, alts):
    """The support table for the page (SPEC-issue-fallbacks.md): the lines in the words of the page («Попросите
    администратора включить …» — the person cannot turn a protocol on himself) and the other protocols with state."""
    if not isinstance(sup, dict):
        return None
    def https(u):
        u = str(u or "")
        return u if u.startswith("https://") else ""
    levels = sup.get("levels")
    return {"level": str(sup.get("level") or ""), "text": str(sup.get("text") or ""), "min": str(sup.get("min") or ""),
            "note": str(sup.get("note") or ""), "unverified": str(sup.get("unverified") or ""),
            "guide": https(sup.get("guide")),
            # the level of every protocol on this platform (SPEC п. 4, FIX-332g №5): the page dims the chip of one the
            # platform cannot take and puts a triangle by a workaround, as the panel does
            "levels": dict((str(k), v) for k, v in (levels if isinstance(levels, dict) else {}).items()
                           if v in install_apps.LEVELS),
            "links": [{"label": str(l.get("label") or ""), "url": https(l.get("url"))} for l in sup.get("links") or []
                      if isinstance(l, dict) and https(l.get("url"))],
            "alternatives": [{"proto": str(a.get("proto") or ""), "title": str(a.get("title") or ""),
                              "state": str(a.get("state") or ""), "text": str(a.get("text") or ""),
                              "note": str(a.get("note") or "")}
                             for a in alts or [] if isinstance(a, dict)]}


def _qr_fallbacks(items):
    """A QR item that could not be drawn shows its text instead — but only when that is the one way to it: one text,
    and no other entry of the issuance carries the same (a WireGuard QR is the .conf that is listed beside it; the
    parts of an AmneziaVPN code are no text to paste)."""
    bodies = set(it.get("body") for it in items if it.get("body"))
    for it in items:
        if it.get("format") == "qr" and "texts" in it:
            texts = it.pop("texts")
            if len(texts) == 1 and texts[0] not in bodies:
                it["texts"] = texts


def _order(platform, usable):
    """The device's inbounds, best first: the matrix of its platform (issue_os), then what else it was granted."""
    first = [o["inbound"] for o in issue_os.offers(platform, available=usable)]
    return first + [k for k in usable if k not in first]


def _inbound_title(kind):
    ad = registry.get(kind)
    return issue_os.TITLES.get(kind) or (getattr(ad, "title", "") if ad is not None else "") or kind


def _inbound_shown(st, kind):
    """The title of the card of an inbound with the owner's label (T13: «AmneziaWG 2.0 — для роутеров»): only shown —
    the items, the file names and the links keep the titles of issue_os (SPEC-stage1-rename §3.1)."""
    from ..gw import rename
    lab = rename.inbound_label(st, kind)
    title = _inbound_title(kind)
    return tr("gw_rename.inbound_shown", title=title, label=lab) if lab else title


def build_keys(user, token, runner=None):
    """The settings of every device of ``user`` for the page. Under the state lock (one consistent state and secrets,
    and ``kaskad.gw.addr`` is not safe for two threads at once). Raises LockedError when a change holds the lock."""
    runner = runner or sysrun.default_runner()
    page = page_key(token)
    qr_ok = bool(runner.which(QRENCODE))
    devices = []
    with state.locked(timeout=LOCK_WAIT):
        st = state.load()
        fresh = model.find_user(st, user["id"])
        if fresh is None:
            return None
        usable_kinds = set(model.enabled_kinds(st))
        for d in model.devices(st, user=user["id"]):
            platform = d.get("platform") if d.get("platform") in model.PLATFORMS else "other"
            entry = {"id": d["id"], "name": str(d.get("name") or ""), "kind": str(d.get("kind") or "other"),
                     "platform": platform, "enabled": d.get("enabled", True) is not False, "inbounds": []}
            devices.append(entry)
            if not entry["enabled"]:
                continue
            usable = [k for k in d.get("access") or [] if k in usable_kinds and registry.get(k) is not None]
            for kind in _order(platform, usable):
                inb = {"kind": kind, "title": _inbound_shown(st, kind), "recommended": not entry["inbounds"],
                       "error": None, "howto": [], "items": []}
                entry["inbounds"].append(inb)
                try:
                    # «Показать» shows every device of the user: not «выдали» (П5 С-1) — the mark «ждут новых
                    # настроек» stays; the file of one device and kind (``file_for``) takes it off
                    got = ops.issue(runner, d["id"], inbound=kind, os_name=platform, where="page", mark=False)
                except KaskadError as e:
                    inb["error"] = util.redact(str(e))
                    continue
                except Exception as e:                               # noqa: BLE001 — one inbound must not fail all
                    util.log_tr("journal.page.issue_failed", dev=d["id"], kind=kind, err=type(e).__name__)      # the text of the error may hold a secret
                    inb["error"] = tr("panel.userpage.issueFailed")
                    continue
                items = [it for it in got.get("items") or [] if isinstance(it, dict)]
                grant = None
                if any(_file_name(it) and it.get("format") in FILE_FORMATS for it in items):
                    grant = GUARDS.grant_issue(page, d["id"], kind, platform)
                inb["items"] = [_item_out(runner, token, grant, it, qr_ok) for it in items]
                _qr_fallbacks(inb["items"])
                # «Как подключить» in the words of this page: «Скопировать», «Скачать файл» (issue_os, where="page")
                inb["howto"] = [str(x) for x in got.get("howto") or [] if isinstance(x, str) and x]
                inb["install"] = _install_out(runner, got.get("install"), qr_ok)
                inb["support"] = _support_out(got.get("support"), got.get("alternatives"))
        gateway_on = model.enabled(st)
        host = model.public_host(st)
    return {"ok": True, "user": {"id": fresh["id"], "name": str(fresh.get("name") or "")},
            "gateway": {"enabled": gateway_on, "host": host}, "qr": qr_ok, "devices": devices}


def file_for(grant_rec, name, runner=None):
    """(bytes, content type) of the issued file ``name`` of a grant, built again now; None when there is none. A
    download is «выдали» for this device and kind (``mark=True``: its mark «ждут новых настроек» is taken off)."""
    device, kind, os_name = grant_rec
    runner = runner or sysrun.default_runner()
    with state.locked(timeout=LOCK_WAIT):
        got = ops.issue(runner, device, inbound=kind, os_name=os_name, where="page")
    for it in got.get("items") or []:
        if isinstance(it, dict) and it.get("format") in FILE_FORMATS and _file_name(it) == name \
                and isinstance(it.get("body"), str) and it["body"]:
            ext = os.path.splitext(name)[1].lower()
            return it["body"].encode("utf-8"), FILE_TYPES.get(ext, "application/octet-stream")
    return None


# ── the static files and the shell ──────────────────────────────────────────

_asset_cache = {}


def _asset(name):
    """{"body", "hash"} of a file of ``static/`` (cached by its signature), None when it is missing."""
    path = os.path.join(_STATIC, name)
    sig = snapshot.signature(path)
    if sig is None:
        return None
    hit = _asset_cache.get(name)
    if hit is None or hit["sig"] != sig:
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return None
        hit = {"sig": sig, "body": body, "hash": hashlib.sha256(body).hexdigest()[:10]}
        _asset_cache[name] = hit
    return hit


_SHELL_MARK = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def render_shell(name, nonce, os_hint, paused, state=None, base="", lang=None):
    """The shell: user.html with the marks filled in one pass (a value is never searched for marks again). ``state``
    — the page's state for user.js (``ok``, ``paused``, ``gone``); by default from ``paused``. ``base`` — the prefix
    the page is under behind nginx ("" — the root): its own CSS and JS are loaded from there. ``lang`` — ru|en
    (3.3.2): ``<html lang>`` and the dictionary the page loads (user-i18n-<lang>.js); by default the server's one."""
    lang = weblang.norm(lang) or weblang.server_lang()
    tpl = _asset("user.html")
    if tpl is None:
        raise HttpError(500, tr("panel.userpage.notInstalled"))
    vals = {"NAME": html.escape(name, quote=True), "NONCE": nonce, "OS": os_hint,
            "STATE": state or ("paused" if paused else "ok"), "BASE": html.escape(base or "", quote=True),
            "LANG": lang}
    for key, fname in (("V_CSS", "user.css"), ("V_JS", "user.js"), ("V_I18N", "user-i18n-%s.js" % lang),
                       ("V_QRS", "qr-slides.js"), ("V_QR", "qr.js"), ("V_GIVE", "give.js"), ("V_GCSS", "give.css"),
                       ("V_GI18N", "give-i18n-%s.js" % lang), ("V_ICONS", "icons.js")):
        a = _asset(fname)
        vals[key] = a["hash"] if a else "0"
    text = _SHELL_MARK.sub(lambda m: vals.get(m.group(1), ""), tpl["body"].decode("utf-8"))
    return text.encode("utf-8")


# ── a link that does not open ───────────────────────────────────────────────

# GET of a path that looks like a link (one segment of base64url, a cut one too): the page «Ссылка больше не действует»
GONE_PATH = re.compile(r"^/p/[A-Za-z0-9_-]{16,64}/?$")


def gone_page(base=""):
    """The answer to a link that opens nothing — a replaced, switched-off, cut or mistyped one, the link of a deleted
    person, any link asked for from a banned address: user.html in the state «gone» («Ссылка больше не действует —
    попросите новую у того, кто её прислал») instead of the bare 404 of an empty site (stand T-G1b, N8). One and the
    same for every such link: nothing in it depends on the token, nothing tells whether it ever existed. Status 404
    (the server counts it as a miss), the headers of the real page: its own CSS and JS load under the same CSP.
    Without the template — ``not_found()``."""
    try:
        body = render_shell("", "", "other", False, state="gone", base=base)
    except HttpError:
        return not_found()
    return _secure(Response(404, body, {"Content-Type": "text/html; charset=utf-8"}))


def refused(method, path, base=""):
    """What a request under /p/ that opens nothing gets: GET of a link — ``gone_page()``, anything else — the 404 of
    an empty site (``not_found``). The server answers a banned address with it too."""
    if method in ("GET", "HEAD") and GONE_PATH.match(path or ""):
        return gone_page(base)
    return not_found()


# ── routes of the person's page ─────────────────────────────────────────────

TOKEN_PAT = r"(?P<token>[A-Za-z0-9_-]{43})"


@route("GET", r"/p/(?P<name>user\.css|user\.js|user-i18n-(?:ru|en)\.js|qr-slides\.js|qr\.js|give\.js|give\.css|give-i18n-(?:ru|en)\.js|icons\.js)",
       auth="public")
def static_file(req):
    name = req.match.group("name")
    a = _asset(name)
    if a is None:
        return not_found()
    cache = "public, max-age=31536000, immutable" if req.query.get("v") == a["hash"] else "no-cache"
    return Response(200, a["body"], {"Content-Type": ASSETS[name], "Cache-Control": cache,
                                     "X-Content-Type-Options": "nosniff"})


ICON_PAT = re.compile(r"^apps/[a-z0-9-]{1,40}\.png$")


@route("GET", r"/p/(?P<name>apps/[a-z0-9-]{1,40}\.png)", auth="public")
def app_icon(req):
    """The icon of an app of «Установите приложение» (gw/install_apps.json ``icon``): a file of the product."""
    a = _asset(req.match.group("name"))
    if a is None:
        return not_found()
    return Response(200, a["body"], {"Content-Type": "image/png", "Cache-Control": "public, max-age=86400",
                                     "X-Content-Type-Options": "nosniff"})


@route("GET", r"/p/%s/?" % TOKEN_PAT, auth="page")
def shell(req):
    user = authorize(req)
    if user is None:
        return gone_page(req.base)              # the same page for every link that does not open
    agent = req.headers.get("user-agent", "")
    bot = is_bot(agent)
    token = req.match.group("token")
    paused = user.get("enabled", True) is False
    nonce = "" if (bot or paused) else GUARDS.nonce_issue(page_key(token))
    # the visitor's own language (3.3.2): RU · EN on the page are links «?lang=…» — the choice goes to the cookie of
    # /p/ (a year, the script of the page never touches cookies or storage); else the cookie; else the server's one
    pick = weblang.norm(req.query.get("lang")) if req.query.get("lang") else None
    lang = pick or weblang.cookie_lang(req.headers)
    body = render_shell("" if bot else str(user.get("name") or ""), nonce, detect_os(agent), paused,
                        base=req.base, lang=lang)
    headers = {"Content-Type": "text/html; charset=utf-8"}
    if pick:
        headers["Set-Cookie"] = "%s=%s; Path=%s/p/; Max-Age=31536000; HttpOnly; SameSite=Lax%s" % (
            weblang.COOKIE, pick, req.base or "", "; Secure" if req.scheme == "https" else "")
    return _secure(Response(200, body, headers))


@route("GET", r"/p/%s/keys" % TOKEN_PAT, auth="page")
def keys_wrong_method(req):
    if authorize(req) is None:
        return not_found()
    resp = _fail(405, "method", tr("panel.userpage.postOnly"))
    resp.headers["Allow"] = "POST"
    return resp


@route("POST", r"/p/%s/keys" % TOKEN_PAT, auth="page")
def keys(req):
    user = authorize(req)
    if user is None:
        return not_found()
    h = req.headers
    ctype = h.get("content-type", "").split(";")[0].strip().lower()
    if is_bot(h.get("user-agent", "")) or ctype != "application/json" \
            or h.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        return not_found()                                      # a robot, a form post, another site
    try:
        body = req.json()
    except HttpError:
        body = {}
    token = req.match.group("token")
    page = page_key(token)
    if not GUARDS.nonce_take(page, str(body.get("nonce") or "")):
        return _fail(409, "reveal", tr("panel.userpage.stale"))
    if user.get("enabled", True) is False:
        return _fail(403, "paused", tr("panel.userpage.paused"))
    got = False
    try:
        got = _BUILD.acquire(timeout=BUILD_WAIT)
        if not got:
            return _fail(503, "busy", tr("panel.userpage.busy"), retry_after=RETRY_AFTER,
                         nonce=GUARDS.nonce_issue(page))
        data = build_keys(user, token)
    except LockedError:
        return _fail(503, "busy", tr("panel.userpage.locked"),
                     retry_after=RETRY_AFTER, nonce=GUARDS.nonce_issue(page))
    except KaskadError as e:
        return _fail(500, "error", util.redact(str(e)))
    finally:
        if got:
            _BUILD.release()
    if data is None:
        return not_found()                                      # the person vanished while we were working
    data["nonce"] = GUARDS.nonce_issue(page)
    util.log_tr("journal.page.shown", name=user.get("name"), id=user.get("id"), n=len(data["devices"]))
    return _json(200, data)


@route("GET", r"/p/%s/file/(?P<grant>[A-Za-z0-9_-]{32})/(?P<fname>[A-Za-z0-9][A-Za-z0-9._-]{0,63})" % TOKEN_PAT,
       auth="page")
def issued_file(req):
    user = authorize(req)
    if user is None or user.get("enabled", True) is False:
        return not_found()
    if is_bot(req.headers.get("user-agent", "")):
        return not_found()
    rec = GUARDS.grant_get(page_key(req.match.group("token")), req.match.group("grant"))
    if rec is None:
        return not_found()
    try:
        got = file_for(rec, req.match.group("fname"))
    except (KaskadError, LockedError):
        return not_found()
    if got is None:
        return not_found()
    body, ctype = got
    name = req.match.group("fname")
    return _secure(Response(200, body, {"Content-Type": ctype,
                                        "Content-Disposition": 'attachment; filename="%s"' % name}))


def build_sub(user, runner=None):
    """The links of the subscription of ``user``: ``(fresh user, [link, ...])`` — the ``vless://`` and
    ``hysteria2://`` links (``SUB_KINDS``) of his active devices in their order, then the ``amneziawg://`` lines of
    their AmneziaWG and WireGuard (``SUB_CONF_KINDS``, ``_conf_lines``); no duplicates; None when the user is gone. A
    paused user — no links. Under the state lock, like ``build_keys``; LockedError when a change holds it."""
    runner = runner or sysrun.default_runner()
    links = []
    with state.locked(timeout=LOCK_WAIT):
        st = state.load()
        fresh = model.find_user(st, user["id"])
        if fresh is None:
            return None
        if fresh.get("enabled", True) is False:
            return fresh, links
        devices = _sub_devices(st, fresh["id"])
        for d in devices:
            for kind in _sub_kinds_of(st, d):
                try:
                    got = ops.issue(runner, d["id"], inbound=kind, where="page", mark=False)   # a fetch is not «выдали»
                except KaskadError:
                    continue                                   # not ready (no key yet): the others still go
                except Exception as e:                         # noqa: BLE001 — one device must not fail all
                    util.log_tr("journal.sub.issue_failed", dev=d["id"], kind=kind, err=type(e).__name__)
                    continue
                for it in got.get("items") or []:
                    body = it.get("body") if isinstance(it, dict) else None
                    if it.get("format") == "link" and isinstance(body, str) and body.startswith(SUB_KINDS[kind]) \
                            and "\n" not in body and body not in links:
                        links.append(body)
        # VLESS and Hysteria2 first and as before (the links old apps already hold keep their places); AmneziaWG and
        # WireGuard after them — the apps that do not know amneziawg:// skip the line (v2rayN, Hiddify: owner 24.10)
        for d in devices:
            for line in _conf_lines(runner, st, d):
                if line not in links:
                    links.append(line)
        # 3.3.7.15 F13: «принимать и выдавать» of an IPv6 — its lines after every IPv4 one (the apps without IPv6 skip
        # them by the delay, the mark «IPv6» helps a choice by hand); the effective give at every build (§6.2)
        for line in _lines6(runner, st, devices):
            if line not in links:
                links.append(line)
    return fresh, links


SUB6_MARK = " · IPv6"                                          # the name of an IPv6 line: the name before + the mark


def _lines6(runner, st, devices):
    """F13 (SPEC-R1 §3 F13): the extra lines of the IPv6 ``addresses.give6_effective`` gives now (written ∩ taken ∩ on
    the machine − deprecated − no way out − seen outside as another) — none: nothing. The settings are issued for that
    address (``ops.issue(host=…)``, ``mark=False``), in the order of the IPv4 lines: VLESS Reality (VLESS TLS gives its
    domain — the device reaches IPv6 by AAAA), Hysteria2 while its link names an address, not the domain; then the
    ``amneziawg://`` lines of WireGuard/AmneziaWG. Hysteria2 with the mask and AmneziaWG «программа» answer from the
    source of the IPv6 route only (T-mip-lab Н2, Н3): only when the IPv6 is that one. A line goes only when it holds
    ``[IPv6]`` (a link) or ``Endpoint = [IPv6]:`` (a .conf) — never a name by mistake; its name is the IPv4 one +
    « · IPv6». A failing device or protocol is left out, the others go."""
    from ..gw import addresses, mip
    try:
        m = addresses.load_cache()
        six = addresses.give6_effective(st, m)
        if not six:
            return []
        ip = six[0]
        route_only = set(k for k, _ in mip.reply_kinds(st)) if ip != addresses.route6_src(m) else set()
        tls = registry.get("vless") is not None and registry.get("vless").security(st) == "tls"
    except Exception as e:                                     # noqa: BLE001 — the IPv4 lines never fail for it
        util.log_tr("journal.sub.issue_failed", dev="-", kind="ipv6", err=type(e).__name__)
        return []
    out = []
    for d in devices:
        for kind in _sub_kinds_of(st, d):
            if kind in route_only or (kind == "vless" and tls):
                continue
            for body in _issue6(runner, d, kind, ip):
                if body.get("format") == "link" and isinstance(body.get("body"), str):
                    line = body["body"]
                    if line.startswith(SUB_KINDS[kind]) and "\n" not in line and ("@[%s]:" % ip) in line:
                        head, mark, frag = line.rpartition("#")
                        if not mark:                   # REV-3315-b2 П-1: a link without a name — the mark alone
                            head, frag = line, ""
                        name = urllib.parse.unquote(frag) + SUB6_MARK if frag else SUB6_MARK.strip()
                        out.append("%s#%s" % (head, urllib.parse.quote(name, safe="")))
    for d in devices:
        for kind in _sub_conf_kinds_of(st, d):
            if kind in route_only:
                continue
            conf = next((it.get("body") for it in _issue6(runner, d, kind, ip) if it.get("format") == "conf"
                         and isinstance(it.get("body"), str) and it["body"].startswith("[Interface]")), None)
            if conf and re.search(r"(?m)^Endpoint = \[%s\]:\d+$" % re.escape(ip), conf):
                out.append(amneziawg_link(conf, issue_os.app_label(_conf_title(st, kind), d.get("name")) + SUB6_MARK))
    return out


def _issue6(runner, device, kind, ip):
    """The items of the settings of ``device`` for ``kind`` on ``ip`` (``ops.issue(host=ip, mark=False)``), [] — not
    ready or failing (said in the journal, as the IPv4 lines)."""
    try:
        got = ops.issue(runner, device["id"], inbound=kind, where="page", mark=False, host=ip)
    except KaskadError:
        return []
    except Exception as e:                                     # noqa: BLE001 — one device must not fail all
        util.log_tr("journal.sub.issue_failed", dev=device["id"], kind=kind, err=type(e).__name__)
        return []
    return [it for it in got.get("items") or [] if isinstance(it, dict)]


def _conf_lines(runner, st, device):
    """The ``amneziawg://`` lines of a device: its .conf of every inbound of ``SUB_CONF_KINDS`` it may use. A key not
    ready yet or a failing inbound — that line is left out, the others go. No marks of an issue (``mark=False``): an
    app fetched the .conf, the device may still hold its static key."""
    out = []
    for kind in _sub_conf_kinds_of(st, device):
        try:
            got = ops.issue(runner, device["id"], inbound=kind, where="page", mark=False)
        except KaskadError:
            continue
        except Exception as e:                                 # noqa: BLE001 — one device must not fail all
            util.log_tr("journal.sub.issue_failed", dev=device["id"], kind=kind, err=type(e).__name__)
            continue
        conf = next((it.get("body") for it in got.get("items") or [] if isinstance(it, dict)
                     and it.get("format") == "conf" and isinstance(it.get("body"), str)
                     and it["body"].startswith("[Interface]")), None)     # not the script of RouterOS (WireGuard)
        if conf:
            out.append(amneziawg_link(conf, issue_os.app_label(_conf_title(st, kind), device.get("name"))))
    return out


def _conf_title(st, kind):
    """«AmneziaWG 2.0», «AmneziaWG 1.0» (the line of awg1 the node gives), «WireGuard» — as in the name of the key
    vpn:// (``issue_os.app_label``)."""
    ad = registry.get(kind)
    try:
        line = ad.line(st) if hasattr(ad, "line") else None
    except KaskadError:
        line = None
    return "AmneziaWG %s" % line if line else registry.title(kind)


def amneziawg_link(conf, name):
    """A line of the subscription for Incy: ``amneziawg://<base64url .conf, no «=»>#<name, percent-encoded UTF-8>``
    (docs.incy.cc «subscription format»; the same as goAmnezia Pro ``pages_server.amneziawg_link``): the line decodes
    back to the .conf byte for byte (one line feed at the end). WireGuard goes the same way — a .conf without the
    obfuscation is WireGuard for the app."""
    body = (conf.rstrip("\n") + "\n").encode("utf-8")
    return "%s%s#%s" % (model.SUB_CONF_SCHEME, base64.urlsafe_b64encode(body).decode("ascii").rstrip("="),
                        urllib.parse.quote(str(name or ""), safe=""))


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@route("GET", r"/p/%s/sub" % TOKEN_PAT, auth="page")
def subscription(req):
    """The subscription for apps: 200 text/plain, base64 of the links (an empty body — nothing to give now)."""
    user = authorize(req, sub=True)
    if user is None or is_bot(req.headers.get("user-agent", "")):
        return not_found()                                  # a robot of link previews gets no keys either
    got = False
    try:
        got = _BUILD.acquire(timeout=BUILD_WAIT)
        if not got:
            return _fail(503, "busy", tr("panel.userpage.busy"), retry_after=RETRY_AFTER)
        data = build_sub(user)
    except LockedError:
        return _fail(503, "busy", tr("panel.userpage.locked"),
                     retry_after=RETRY_AFTER)
    finally:
        if got:
            _BUILD.release()
    if data is None:
        return not_found()                                  # the person vanished while we were working
    fresh, links = data
    util.log_tr("journal.sub.served", name=fresh.get("name"), id=fresh.get("id"), n=len(links))
    body = _b64("\n".join(links)).encode("ascii")
    return Response(200, body, {
        "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
        "profile-title": "base64:" + _b64("%s · %s" % (issue_os.BRAND, fresh.get("name") or "")),
        "profile-update-interval": str(SUB_INTERVAL),
        "X-Content-Type-Options": "nosniff", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"})


def _anything_else(req):
    """Everything else under /p/ — one 404 and a miss, unless the path starts with a live token (then no miss:
    the person mistyped a sub-path, not a secret). GET of what looks like a link (a cut one) — the page «Ссылка
    больше не действует» (``refused``)."""
    ip = req.client_ip or "-"
    if GUARDS.banned(ip):
        return refused(req.method, req.path, req.base)
    m = re.match(r"^/p/([A-Za-z0-9_-]{43})(?:/|$)", req.path)
    if m and find_token(m.group(1)) is not None:
        return not_found()
    GUARDS.miss(ip)
    return refused(req.method, req.path, req.base)


for _method in ("GET", "POST", "PUT", "DELETE"):
    route(_method, r"/p(?:/.*)?", auth="page")(_anything_else)


# ── routes for the admin side of the panel ──────────────────────────────────

def _http(e, uid=None):
    """404 when the person or the device is gone (model.NotFound — by the class, never by the words), else 400.
    ``uid`` — kept for the callers, not needed any more."""
    return HttpError(404 if isinstance(e, model.NotFound) else 400, str(e), code=getattr(e, "code", None))


@route("GET", r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/page")
def admin_status(req):
    uid = req.match.group("uid")
    try:
        out = status(uid)
    except KaskadError as e:
        raise _http(e, uid)
    return json_response(dict(out, ok=True))


@route("POST", r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/page")
def admin_change(req):
    uid = req.match.group("uid")
    action = str(req.json().get("action") or "")
    if action not in ("enable", "rotate", "disable"):
        raise HttpError(400, tr("panel.userpage.badAction"))
    try:
        {"enable": enable, "rotate": rotate, "disable": disable}[action](uid)
        out = status(uid)
    except KaskadError as e:
        raise _http(e, uid)
    return json_response(dict(out, ok=True))


@route("GET", r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/sub")
def admin_sub_status(req):
    uid = req.match.group("uid")
    try:
        out = admin_sub_doc(uid)
    except KaskadError as e:
        raise _http(e, uid)
    return json_response(dict(out, ok=True))


@route("POST", r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/sub")
def admin_sub_change(req):
    uid = req.match.group("uid")
    action = str(req.json().get("action") or "")
    if action not in ("enable", "rotate", "disable"):
        raise HttpError(400, tr("panel.userpage.badAction"))
    try:
        {"enable": sub_enable, "rotate": sub_rotate, "disable": sub_disable}[action](uid)
        out = admin_sub_doc(uid)
    except KaskadError as e:
        raise _http(e, uid)
    return json_response(dict(out, ok=True))
