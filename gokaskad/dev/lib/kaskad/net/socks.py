"""SOCKS5 client (RFC 1928) and a small HTTP(S) client on top of ``http.client`` — for the Telegram bot.

The bot reaches api.telegram.org directly or through «Прокси для API Telegram» — ``BOT_PROXY`` in
/etc/kaskad/config, as 2.x curl took it (``--proxy``):

    socks5h://127.0.0.1:10808   SOCKS5, the destination NAME goes to the proxy (what the egress writes)
    socks5://host:port          the same: the name is never resolved here either — on RU servers the local
                                resolver may answer api.telegram.org with a stub address
    http://host:port            an HTTP proxy, CONNECT tunnel (``http.client`` set_tunnel)

Only proxies without a password (no credentials in the URL: refused with a clear message). TLS is always
verified: ``ssl.create_default_context()`` (system CAs, hostname check, SNI = the API host, not the proxy).

    parse_proxy(url)                       -> Proxy or None; ValueError with a Russian text
    socks5_connect(ph, pp, host, port, t)  -> a connected socket (the proxy said «succeeded»)
    connection(scheme, host, port, proxy, timeout, context) -> http.client connection, direct or proxied
    request(url, method, body, headers, proxy, timeout, context) -> Response(status, reason, headers, body)
    multipart(fields, files)               -> (body bytes, content type) for uploads

Every network failure is an ``OSError`` (``ProxyError``/``NetError`` here, ``socket.timeout``, ``ssl.SSLError``),
so callers catch one family. Nothing here logs; messages never contain the URL (the bot token is in its path).
"""

import http.client
import ipaddress
import socket
import ssl
import struct
import time
import uuid
from urllib.parse import urlsplit

from ..i18n import tr

DEFAULT_PORT = 1080             # curl's default proxy port
MAX_BODY = 16 * 1024 * 1024     # a Telegram answer is far smaller; a broken proxy must not fill the memory
SCHEMES = ("socks5", "socks5h", "http")

# REP of a SOCKS5 answer -> the catalog key of its words (tr at the time of the failure: the language is not known
# at import)
REPLIES = dict((code, "net.socks.reply.%d" % code) for code in range(1, 9))


class NetError(OSError):
    """A network failure with a short Russian description (never the URL)."""


class ProxyError(NetError):
    """The proxy refused or broke the connection."""


class Proxy(object):
    __slots__ = ("scheme", "host", "port")

    def __init__(self, scheme, host, port):
        self.scheme = scheme
        self.host = host
        self.port = int(port)

    @property
    def socks(self):
        return self.scheme in ("socks5", "socks5h")

    def __eq__(self, other):
        return isinstance(other, Proxy) and (self.scheme, self.host, self.port) == (other.scheme, other.host, other.port)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.scheme, self.host, self.port))

    def __repr__(self):
        host = "[%s]" % self.host if ":" in self.host else self.host
        return "%s://%s:%d" % (self.scheme, host, self.port)


def parse_proxy(url):
    """``BOT_PROXY`` -> Proxy, or None when it is empty. Raises ValueError (Russian text, without the URL:
    it could carry a password) for an unsupported scheme, credentials or a bad port."""
    s = str(url or "").strip()
    if not s:
        return None
    try:
        parts = urlsplit(s)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError(tr("net.proxy.unparsed"))
    if scheme not in SCHEMES:
        raise ValueError(tr("net.proxy.scheme", scheme=scheme + "://" if scheme else tr("net.proxy.no_scheme")))
    if parts.username is not None or parts.password is not None:
        raise ValueError(tr("net.proxy.credentials"))
    if not host:
        raise ValueError(tr("net.proxy.no_host"))
    return Proxy(scheme, host, port or DEFAULT_PORT)


# ── SOCKS5 ─────────────────────────────────────────────────────────────────

def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProxyError(tr("net.socks.closed"))
        buf += chunk
    return buf


def _address(host):
    """ATYP + DST.ADDR: an IP literal as is, anything else as a domain name (resolved by the proxy)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and ip.version == 4:
        return b"\x01" + ip.packed
    if ip is not None:
        return b"\x04" + ip.packed
    try:
        name = host.encode("idna")
    except UnicodeError:
        raise ProxyError(tr("net.socks.bad_name"))
    if not name or len(name) > 255:
        raise ProxyError(tr("net.socks.bad_name"))
    return b"\x03" + bytes([len(name)]) + name


def socks5_connect(proxy_host, proxy_port, host, port, timeout=20.0):
    """A socket connected to ``host:port`` through the SOCKS5 proxy (RFC 1928, method «no authentication»,
    CMD CONNECT). ``host`` that is not an IP literal goes to the proxy as a domain name (ATYP 3). The socket
    keeps ``timeout``. Raises ProxyError (the proxy refused, wants a password, closed, is not SOCKS5) or
    another OSError (cannot reach the proxy, timeout)."""
    port = int(port)
    if not 0 < port < 65536:
        raise ProxyError(tr("net.socks.bad_port"))
    request = b"\x05\x01\x00" + _address(host) + struct.pack(">H", port)
    try:
        sock = socket.create_connection((proxy_host, int(proxy_port)), timeout=timeout)
    except OSError as e:
        raise ProxyError(tr("net.socks.unreachable", host=proxy_host, port=proxy_port, why=_why(e)))
    try:
        sock.sendall(b"\x05\x01\x00")
        ver, method = _recv_exact(sock, 2)
        if ver != 5:
            raise ProxyError(tr("net.socks.not_socks5"))
        if method == 0xFF:
            raise ProxyError(tr("net.socks.auth"))
        if method != 0:
            raise ProxyError(tr("net.socks.method", method=method))
        sock.sendall(request)
        head = _recv_exact(sock, 4)
        if head[0] != 5:
            raise ProxyError(tr("net.socks.protocol"))
        if head[1] != 0:
            why = tr(REPLIES[head[1]]) if head[1] in REPLIES else tr("net.socks.code", rc=head[1])
            raise ProxyError(tr("net.socks.refused", host=host, port=port, why=why))
        atyp = head[3]
        if atyp == 1:
            _recv_exact(sock, 4 + 2)
        elif atyp == 4:
            _recv_exact(sock, 16 + 2)
        elif atyp == 3:
            _recv_exact(sock, _recv_exact(sock, 1)[0] + 2)
        else:
            raise ProxyError(tr("net.socks.atyp"))
        return sock
    except BaseException:
        sock.close()
        raise


def _why(e):
    if isinstance(e, socket.timeout):
        return tr("net.socks.timeout")
    return e.strerror or type(e).__name__


# ── http.client connections ────────────────────────────────────────────────

class HTTPConnection(http.client.HTTPConnection):
    """Plain HTTP, directly or through a proxy (SOCKS5 or CONNECT of an HTTP proxy)."""

    def __init__(self, host, port=None, proxy=None, timeout=20.0):
        self._kaskad_proxy = proxy
        self._kaskad_dest = (host, port or 80)
        if proxy is not None and not proxy.socks:
            super(HTTPConnection, self).__init__(proxy.host, proxy.port, timeout=timeout)
            self.set_tunnel(host, port or 80)
        else:
            super(HTTPConnection, self).__init__(host, port, timeout=timeout)

    def connect(self):
        p = self._kaskad_proxy
        if p is None or not p.socks:
            return super(HTTPConnection, self).connect()
        self.sock = socks5_connect(p.host, p.port, self._kaskad_dest[0], self._kaskad_dest[1], self.timeout)


class HTTPSConnection(http.client.HTTPSConnection):
    """HTTPS with certificate verification, directly or through a proxy. SNI and the hostname check are the
    destination's (api.telegram.org), never the proxy's."""

    def __init__(self, host, port=None, proxy=None, timeout=20.0, context=None):
        ctx = context if context is not None else ssl.create_default_context()
        self._kaskad_proxy = proxy
        self._kaskad_dest = (host, port or 443)
        if proxy is not None and not proxy.socks:
            super(HTTPSConnection, self).__init__(proxy.host, proxy.port, timeout=timeout, context=ctx)
            self.set_tunnel(host, port or 443)
        else:
            super(HTTPSConnection, self).__init__(host, port, timeout=timeout, context=ctx)

    def connect(self):
        p = self._kaskad_proxy
        if p is None or not p.socks:
            return super(HTTPSConnection, self).connect()
        sock = socks5_connect(p.host, p.port, self._kaskad_dest[0], self._kaskad_dest[1], self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self._kaskad_dest[0])
        except BaseException:
            sock.close()
            raise


def connection(scheme, host, port=None, proxy=None, timeout=20.0, context=None):
    if scheme == "https":
        return HTTPSConnection(host, port, proxy=proxy, timeout=timeout, context=context)
    if scheme == "http":
        return HTTPConnection(host, port, proxy=proxy, timeout=timeout)
    raise ValueError(tr("net.http.scheme", scheme=repr(scheme)))


class Response(object):
    __slots__ = ("status", "reason", "headers", "body")

    def __init__(self, status, reason, headers, body):
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body

    def header(self, name, default=None):
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return default


def _read(resp, limit, deadline):
    chunks = []
    size = 0
    while True:
        if time.monotonic() > deadline:
            raise NetError(tr("net.http.slow"))
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise NetError(tr("net.http.too_big"))
        chunks.append(chunk)


def request(url, method="GET", body=None, headers=None, proxy=None, timeout=20.0, context=None, limit=MAX_BODY):
    """One HTTP(S) exchange on a fresh connection (``Connection: close``). Every socket operation is bounded by
    ``timeout``, the whole answer by 2×``timeout``. Returns Response; raises OSError (NetError, ProxyError,
    socket.timeout, ssl.SSLError) — the message never contains ``url``."""
    parts = urlsplit(url)
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    conn = connection(parts.scheme, parts.hostname, parts.port, proxy=proxy, timeout=timeout, context=context)
    hdrs = {"Connection": "close", "User-Agent": "kaskad"}
    hdrs.update(headers or {})
    deadline = time.monotonic() + 2 * float(timeout)
    try:
        try:
            conn.request(method, target, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = _read(resp, limit, deadline)
        except http.client.HTTPException as e:
            raise NetError(tr("net.http.not_http", error=type(e).__name__))
        return Response(resp.status, resp.reason, resp.getheaders(), data)
    finally:
        conn.close()


# ── multipart/form-data ────────────────────────────────────────────────────

def _quote(value):
    """A name or file name inside ``Content-Disposition`` quotes: no quotes, backslashes or line breaks."""
    out = []
    for ch in str(value):
        out.append("_" if ch in '"\\\r\n' or ord(ch) < 32 else ch)
    return "".join(out)


def multipart(fields, files):
    """``fields`` [(name, str)], ``files`` [(name, filename, bytes, content_type)] -> (body, content type)."""
    boundary = "kaskad-" + uuid.uuid4().hex
    b = boundary.encode("ascii")
    out = []
    for name, value in fields:
        out.append(b"--" + b + b"\r\n")
        out.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % _quote(name)).encode("utf-8"))
        out.append(str(value).encode("utf-8") + b"\r\n")
    for name, filename, data, ctype in files:
        out.append(b"--" + b + b"\r\n")
        out.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                    % (_quote(name), _quote(filename), ctype or "application/octet-stream")).encode("utf-8"))
        out.append(bytes(data) + b"\r\n")
    out.append(b"--" + b + b"--\r\n")
    return b"".join(out), "multipart/form-data; boundary=" + boundary
