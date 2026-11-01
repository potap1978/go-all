"""A subscription link of an exit, downloaded safely for ``gw exit-add --public-only`` (the MCP server).

The link comes from an AI agent and root downloads it, so the download must not reach the node itself, its LAN or the
metadata service of the cloud (blind SSRF; stand T-M6, F1). install.sh downloads a subscription with ``curl -L`` —
redirects anywhere, the host resolved again after any check — so with ``--public-only`` the subscription never
reaches install.sh as a link: it is downloaded here and handed on as its text.

    expand(text)   every line ``http(s)://…`` of the text -> the text of that subscription; other lines as they are
    fetch_sub(url, pin)   the text of a subscription of the exits (gw/exitsubs.py): up to SUB_MAX_BYTES, and a server
                   by IP address over https with a self-signed certificate is taken on its first fingerprint (pinned)

How: the URL is split by ``urllib.parse`` (``user:pass@`` — refused: a browser and curl read the host after «@»);
only http and https; the host is resolved once, EVERY address must be global (not loopback, private, link-local,
CGNAT, reserved, multicast); the connection goes to that checked address (the name only in Host and SNI — no second
lookup, no DNS race); a redirect is followed by hand, up to MAX_REDIRECTS, each target checked the same way; at most
MAX_BYTES, TIMEOUT seconds per request. Lines of the downloaded text that are links themselves are dropped: the
script would download them again without these checks.
"""

import ipaddress
import socket
import ssl
import urllib.parse

from ..errors import USAGE, KaskadError
from ..i18n import tr

MAX_REDIRECTS = 3
MAX_BYTES = 64 * 1024
SUB_MAX_BYTES = 1024 * 1024         # a subscription of the exits: up to 100 keys of vpn:// with long I1–I5
TIMEOUT = 20
# INTERNAL_TEXT, CERT_CHANGED_TEXT, CERT_NAME_TEXT — read through the module's __getattr__ (the template of the language of
# the moment: «{addr}», «{old}»/«{new}», «{domain}»)
_WORDS = {"INTERNAL_TEXT": "gw_subfetch.internal", "CERT_CHANGED_TEXT": "gw_subfetch.cert_changed",
          "CERT_NAME_TEXT": "gw_subfetch.cert_domain"}


def __getattr__(name):
    if name in _WORDS:
        return tr(_WORDS[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def internal_text(host, ip):
    """The refusal of an inner address: «имя → адрес», or the address alone when the link has it already (T-M6-qs330
    №10: «169.254.169.254 → 169.254.169.254»)."""
    host = str(host or "").strip("[]")
    try:
        same = ipaddress.ip_address(host.split("%")[0]) == ipaddress.ip_address(str(ip))
    except ValueError:
        same = False
    return tr("gw_subfetch.internal", addr=str(ip) if same else "%s → %s" % (host, ip))


def _refuse(text, **data):
    raise KaskadError(text, code=USAGE, data=data or None)


def _resolve(host):
    return sorted(set(ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)))


def check_url(url, resolve=None):
    """-> (scheme, host, port, path_with_query, ip): the URL is http(s) without userinfo and EVERY address of its host is
    global; the first one is returned to connect to. KaskadError (USAGE) otherwise."""
    resolve = resolve or _resolve
    try:
        u = urllib.parse.urlsplit(url.strip())
        port = u.port
    except ValueError:
        _refuse(tr("gw_subfetch.unparsed"))
    if u.scheme not in ("http", "https"):
        _refuse(tr("gw_subfetch.scheme"))
    if u.username is not None or u.password is not None or "@" in u.netloc:
        _refuse(tr("gw_subfetch.userinfo"))
    host = u.hostname
    if not host:
        _refuse(tr("gw_subfetch.no_host"))
    port = port or (443 if u.scheme == "https" else 80)
    try:
        addrs = [host] if _is_ip(host) else resolve(host)
    except (OSError, UnicodeError, ValueError):
        _refuse(tr("gw_subfetch.not_found", host=host))
    if not addrs:
        _refuse(tr("gw_subfetch.not_found", host=host))
    for a in addrs:
        ip = ipaddress.ip_address(str(a).split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            _refuse(internal_text(host, ip), host=host)
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return u.scheme, host, port, path, str(addrs[0]).split("%")[0]


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _open(scheme, host, port, path, ip):
    """GET to the checked ``ip`` (Host/SNI — the name) -> (status, location, body bytes)."""
    import http.client
    raw = socket.create_connection((ip, port), timeout=TIMEOUT)
    try:
        if scheme == "https":
            ctx = _ctx()
            sock = ctx.wrap_socket(raw, server_hostname=host)
            conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=ctx)
        else:
            sock = raw
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.sock = sock
        conn.request("GET", path, headers={"Host": host if port in (80, 443) else "%s:%d" % (host, port),
                                           "User-Agent": "kaskad-subscription", "Accept": "*/*"})
        resp = conn.getresponse()
        body = resp.read(MAX_BYTES + 1)
        return resp.status, resp.getheader("Location"), body
    finally:
        raw.close()


def fetch(url, resolve=None, opener=None):
    """The text of a subscription: checks, a pinned address, redirects by hand. KaskadError with the reason in words."""
    opener = opener or _open
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, path, ip = check_url(url, resolve)
        try:
            status, location, body = opener(scheme, host, port, path, ip)
        except ssl.SSLCertVerificationError as e:
            names = _ca_names(ip, port) if scheme == "https" and _is_ip(host) else None
            if names is not None:
                raise cert_name_error(names, ip, resolve)
            raise KaskadError(tr("gw_subfetch.paste.cert", err=type(e).__name__))
        except (OSError, ssl.SSLError, ValueError) as e:
            raise KaskadError(tr("gw_subfetch.paste.failed", err=type(e).__name__))
        if status in (301, 302, 303, 307, 308) and location:
            url = urllib.parse.urljoin(url, location)
            continue
        if status != 200:
            raise KaskadError(tr("gw_subfetch.paste.status", status=status))
        if len(body) > MAX_BYTES:
            raise KaskadError(tr("gw_subfetch.paste.big", kb=MAX_BYTES // 1024))
        return body.decode("utf-8", "replace")
    raise KaskadError(tr("gw_subfetch.redirects", n=MAX_REDIRECTS))


def _is_link(line):
    return line.strip().lower().startswith(("http://", "https://"))


def expand(text, resolve=None, opener=None):
    """Lines of links -> the downloaded subscriptions; the links inside a downloaded one are dropped."""
    out = []
    for line in (text or "").splitlines():
        if _is_link(line):
            got = fetch(line.strip(), resolve, opener)
            out.extend(l for l in got.splitlines() if not _is_link(l))
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")


# ── a subscription of the exits (gw/exitsubs.py): the same checks, a bigger body, a pinned self-signed certificate ──
# A server by IP address over https usually has a self-signed certificate (goAmnezia Lite, 3X-UI without a domain): no CA
# can vouch for it. It is taken on its first fingerprint (SHA-256 of the certificate, «trust on first use») when the
# subscription is added; every later download must show the same one — another certificate is refused (the address may
# have been taken over), the person adds the subscription again. A name (a domain) is always checked by the CAs.

# An address by IP over https whose server shows a certificate a CA vouches for — but issued to a domain (goAmnezia with
# a domain: its subscription link by IP, T-X6). No CA can vouch for the IP, and such a certificate is never taken on
# trust (it changes every 60–90 days: a pin would break at the first renewal). The words name the domain; the address
# itself is never in them (a secret). ``data``: cert_domain, same_server — the domain points at this very address, so
# the same link by the domain is safe to offer (the panel and the menu build it from the address the person typed).

_ctx = ssl.create_default_context      # the CAs of the system (tests give their own)


def _ca_names(ip, port):
    """The DNS names of the certificate at ``ip:port`` when a CA vouches for it (the chain checked, the name not), else
    None — self-signed, or the handshake failed."""
    try:
        ctx = _ctx()
        ctx.check_hostname = False
        raw = socket.create_connection((ip, port), timeout=TIMEOUT)
        try:
            sock = ctx.wrap_socket(raw)
            cert = sock.getpeercert() or {}
        finally:
            raw.close()
    except (OSError, ssl.SSLError, ValueError):
        return None
    names = [v for k, v in cert.get("subjectAltName") or () if k == "DNS" and v]
    if not names:
        names = [v for rdn in cert.get("subject") or () for k, v in rdn if k == "commonName" and v]
    return names


def _cert_domain(names):
    """The name to offer: the first one that is not a wildcard (``*.example.org`` — the base domain), lower case."""
    for n in names or ():
        n = str(n).strip().lower().rstrip(".")
        if n.startswith("*."):
            n = n[2:]
        if n and not _is_ip(n):
            return n
    return ""


def _same_server(domain, ip, resolve=None):
    """The domain points at ``ip`` (one of its addresses): the link by the domain reaches this same server."""
    try:
        return str(ip) in [str(a).split("%")[0] for a in (resolve or _resolve)(domain)]
    except (OSError, UnicodeError, ValueError):
        return False


def cert_name_error(names, ip, resolve=None):
    domain = _cert_domain(names)
    if not domain:
        return KaskadError(tr("gw_subfetch.cert_other"), data={"cert_domain": "", "same_server": False})
    return KaskadError(tr("gw_subfetch.cert_domain", domain=domain),
                       data={"cert_domain": domain, "same_server": _same_server(domain, ip, resolve)})


class SubAnswer(object):
    __slots__ = ("status", "location", "body", "pin")

    def __init__(self, status, location, body, pin=None):
        self.status = status
        self.location = location
        self.body = body
        self.pin = pin                  # SHA-256 hex of a self-signed certificate taken (an IP over https), else None


def _get(scheme, host, port, path, ip, pin=None, limit=SUB_MAX_BYTES, new_pin=False):
    """GET to the checked ``ip``. https: the CAs first; a host that is an IP address with a certificate no CA vouches
    for — its fingerprint must equal ``pin`` (or, with ``new_pin``, is taken). -> SubAnswer."""
    import hashlib
    import http.client
    took = None
    raw = socket.create_connection((ip, port), timeout=TIMEOUT)
    try:
        if scheme == "https":
            try:
                sock = _ctx().wrap_socket(raw, server_hostname=host)
            except ssl.SSLCertVerificationError:
                if not _is_ip(host):
                    raise
                raw.close()
                names = _ca_names(ip, port)             # a certificate a CA vouches for — a domain's (one at a time)
                raw = socket.create_connection((ip, port), timeout=TIMEOUT)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(raw)
                fp = hashlib.sha256(sock.getpeercert(binary_form=True) or b"").hexdigest()
                if not (pin and fp == pin):
                    # not the certificate taken before (or none taken): a domain's is never taken on trust — say so
                    if names is not None:
                        raise cert_name_error(names, ip)
                    if not (pin or new_pin):
                        raise
                if pin and fp != pin:
                    raise KaskadError(tr("gw_subfetch.cert_changed", old=pin[:12], new=fp[:12]),
                                      data={"cert_changed": True})
                took = fp
            conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT)
        else:
            sock = raw
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.sock = sock
        conn.request("GET", path, headers={"Host": host if port in (80, 443) else "%s:%d" % (host, port),
                                           "User-Agent": "kaskad-subscription", "Accept": "*/*"})
        resp = conn.getresponse()
        body = resp.read(limit + 1)
        return SubAnswer(resp.status, resp.getheader("Location"), body, took)
    finally:
        raw.close()


def check_cert(url, resolve=None):
    """The certificate of a subscription the way ``fetch_sub`` adding it checks it, nothing downloaded — the preview
    before «добавить?» (T-M6-qs330b №10: https://<IP> with a certificate of a domain was «найден» there and refused by
    the add). https only: the CAs and the name; an IP address with a certificate a CA vouches for, issued to a domain
    — ``cert_name_error`` (the domain in ``data``); a self-signed one by IP passes (taken on first use when added).
    No connection, a timeout — None (the add says it; the preview stays neutral). -> None; KaskadError otherwise."""
    scheme, host, port, _path, ip = check_url(url, resolve)
    if scheme != "https":
        return None
    try:
        raw = socket.create_connection((ip, port), timeout=TIMEOUT)
    except OSError:
        return None
    try:
        _ctx().wrap_socket(raw, server_hostname=host).close()
    except ssl.SSLCertVerificationError:
        if not _is_ip(host):
            raise KaskadError(tr("gw_subfetch.cert_bad"))
        names = _ca_names(ip, port)
        if names is not None:
            raise cert_name_error(names, ip, resolve)
    except (OSError, ssl.SSLError, ValueError):
        return None
    finally:
        raw.close()
    return None


def fetch_sub(url, pin=None, new_pin=False, resolve=None, getter=None, limit=SUB_MAX_BYTES):
    """The text of a subscription of the exits -> (text, pin): the checks of ``fetch`` (only http/https, no user:pass@,
    every address of the host global, the connection to the checked address, redirects by hand, each checked); ``pin``
    — the fingerprint taken before (a self-signed certificate of an IP address); ``new_pin`` — take one now (adding the
    subscription). KaskadError with the reason in words; the address itself is never in it."""
    getter = getter or _get
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, path, ip = check_url(url, resolve)
        try:
            ans = getter(scheme, host, port, path, ip, pin=pin, limit=limit, new_pin=new_pin)
        except KaskadError:
            raise
        except ssl.SSLCertVerificationError:
            raise KaskadError(tr("gw_subfetch.cert_bad"))
        except socket.timeout:
            raise KaskadError(tr("gw_subfetch.timeout", s=TIMEOUT))
        except ConnectionRefusedError:
            raise KaskadError(tr("gw_subfetch.refused"))
        except (OSError, ssl.SSLError, ValueError) as e:
            raise KaskadError(tr("gw_subfetch.failed", err=type(e).__name__))
        if ans.status in (301, 302, 303, 307, 308) and ans.location:
            url = urllib.parse.urljoin(url, ans.location)
            continue
        if ans.status == 404:
            raise KaskadError(tr("gw_subfetch.gone"))
        if ans.status != 200:
            raise KaskadError(tr("gw_subfetch.status", status=ans.status))
        if len(ans.body) > limit:
            raise KaskadError(tr("gw_subfetch.big", kb=limit // 1024))
        return ans.body.decode("utf-8", "replace"), (ans.pin or pin)
    raise KaskadError(tr("gw_subfetch.redirects", n=MAX_REDIRECTS))
