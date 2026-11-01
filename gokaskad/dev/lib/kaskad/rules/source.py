"""Downloads of the RoscomVPN lists (docs/k3/SPEC-wave3.md §2, docs/k3/research/rules.md) — on the node itself,
plain HTTPS (not through the exits: the lists are public and the pool may be dead exactly when they are needed),
never shipped in the package.

    geoip.dat     1. GitHub release «latest»:  .../roscomvpn-geoip/releases/latest/download/geoip.dat (+ .sha256)
                  2. jsDelivr mirror:           cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/geoip.dat
                     (+ .sha256)
    geosite.dat   the same pair for roscomvpn-geosite
    text          only when there is no geoip.dat at all (nothing downloaded now, nothing stored before): the
                  networks for the kernel from release/text/direct.txt (jsDelivr), then runetfreedom
                  russia-blocked-geoip ``release`` text/ru.txt (raw.githubusercontent.com, jsDelivr) — no xray file then

Each .dat comes with its ``.sha256`` (``<sha256>  release/geoip.dat`` — the first field counts) from the SAME
source (the mirror may lag GitHub by hours): the small .sha256 is fetched first; when it equals the sha256 of
the stored file nothing else is downloaded (daily checks cost two requests of ~90 bytes). A file whose sha256
differs, that is too small or too big, or that does not decode (the caller checks) is refused, the next source
is tried. Timeouts: 20 s per socket operation, 120 s per file; sizes are capped while reading. The release tag
(``202610040942``) comes from GitHub's redirect to ``/releases/download/<tag>/``.
"""

import hashlib
import http.client
import re
import time
import urllib.error
import urllib.request

from .. import __version__, util

TIMEOUT = 20                         # s, every socket operation
DEADLINE = 120                       # s, one file
CHUNK = 64 << 10
SHA_MAX = 1024
LIMITS = {                           # (min, max) bytes
    "geoip": (50 << 10, 32 << 20),   # RoscomVPN: ~425 KB
    "geosite": (5 << 10, 32 << 20),  # ~68 KB
    "text": (10 << 10, 32 << 20),    # direct.txt ~587 KB, runetfreedom ru.txt ~446 KB
}
USER_AGENT = "Kaskad/%s (rules)" % __version__

GITHUB_GEOIP = "https://github.com/hydraponique/roscomvpn-geoip/releases/latest/download/geoip.dat"
GITHUB_GEOSITE = "https://github.com/hydraponique/roscomvpn-geosite/releases/latest/download/geosite.dat"
JSD_GEOIP = "https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/geoip.dat"
JSD_GEOSITE = "https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geosite/release/geosite.dat"

SOURCES = {
    "geoip": [{"name": "github", "url": GITHUB_GEOIP, "sha": GITHUB_GEOIP + ".sha256"},
              {"name": "jsdelivr", "url": JSD_GEOIP, "sha": JSD_GEOIP + ".sha256"}],
    "geosite": [{"name": "github", "url": GITHUB_GEOSITE, "sha": GITHUB_GEOSITE + ".sha256"},
                {"name": "jsdelivr", "url": JSD_GEOSITE, "sha": JSD_GEOSITE + ".sha256"}],
}
TEXT_SOURCES = [
    {"name": "roscomvpn-text", "url": "https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/text/direct.txt",
     "license": "RoscomVPN geoip (direct)"},
    {"name": "runetfreedom", "license": "GPL-3.0",
     "url": "https://raw.githubusercontent.com/runetfreedom/russia-blocked-geoip/release/text/ru.txt"},
    {"name": "runetfreedom-jsdelivr", "license": "GPL-3.0",
     "url": "https://cdn.jsdelivr.net/gh/runetfreedom/russia-blocked-geoip@release/text/ru.txt"},
]
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TAG = re.compile(r"/releases/download/([A-Za-z0-9._-]{1,64})/")


class SourceError(Exception):
    """No source gave the file; the text says what each one answered (for people, Russian)."""


class FetchError(Exception):
    pass


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, chain):
        self.chain = chain

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(newurl)
        if len(self.chain) > 10:
            raise util.err(FetchError, "rules.fetch.redirects")
        return urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)


def _reason_msg(e):
    """A short reason of a download failure, kept as a message (util.msg) or data."""
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %d" % e.code
    if isinstance(e, urllib.error.URLError):
        r = e.reason
        if isinstance(r, OSError) and "timed out" in str(r):
            return util.msg("rules.fetch.timeout")
        return util.msg("rules.fetch.no_connection", reason=str(r)[:120])
    if isinstance(e, (TimeoutError,)) or "timed out" in str(e):
        return util.msg("rules.fetch.timeout")
    if isinstance(e, FetchError):
        return getattr(e, "msg", None) or str(e)
    return "%s: %s" % (type(e).__name__, str(e)[:120])


def _reason(e):
    """A short text of a download failure, in the language of the call."""
    return util.said(_reason_msg(e))


def _kept(cls, m):
    """An exception of the text of the kept message ``m`` (a message or a list of them), keeping ``m`` (.msg)."""
    e = cls(util.said(m))
    e.msg = m
    return e


def _why(e):
    """What a check of the caller said (DatError, ValueError of rules.ops …): its kept message, or its text."""
    return getattr(e, "msg", None) or str(e)[:120] or type(e).__name__


class Http(object):
    """urllib with the redirect chain, timeouts, a size cap and a deadline. Tests replace ``get``."""

    def __init__(self, timeout=TIMEOUT, deadline=DEADLINE):
        self.timeout = timeout
        self.deadline = deadline

    def get(self, url, max_bytes):
        """{"data": bytes, "url": final url, "chain": [redirects], "last_modified": header or None}.
        Raises FetchError (with a short reason)."""
        chain = []
        opener = urllib.request.build_opener(_Redirects(chain))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        start = time.monotonic()
        try:
            with opener.open(req, timeout=self.timeout) as r:
                size = r.headers.get("Content-Length")
                if size and size.isdigit() and int(size) > max_bytes:
                    raise util.err(FetchError, "rules.fetch.too_big", kb=max_bytes >> 10)
                out = bytearray()
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out += chunk
                    if len(out) > max_bytes:
                        raise util.err(FetchError, "rules.fetch.too_big", kb=max_bytes >> 10)
                    if time.monotonic() - start > self.deadline:
                        raise util.err(FetchError, "rules.fetch.deadline", sec=self.deadline)
                return {"data": bytes(out), "url": r.geturl(), "chain": list(chain),
                        "last_modified": r.headers.get("Last-Modified")}
        except FetchError:
            raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            raise _kept(FetchError, _reason_msg(e))


def parse_sha(data):
    """The first field of a ``.sha256`` file (``<hex>  release/geoip.dat``), lower-case; FetchError if it is not
    a sha256."""
    try:
        text = data.decode("ascii", "replace").strip()
    except AttributeError:
        text = str(data).strip()
    first = text.split()[0].lower() if text.split() else ""
    if not _SHA.match(first):
        raise util.err(FetchError, "rules.fetch.no_sha")
    return first


def tag_of(chain):
    for url in chain or []:
        m = _TAG.search(url)
        if m:
            return m.group(1)
    return None


def tag_date(tag):
    """``202610040942`` -> ``2026-10-04T09:42:00Z`` (the release tags of RoscomVPN are UTC times), else None."""
    m = re.match(r"^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)$", str(tag or ""))
    if not m:
        return None
    return "%s-%s-%sT%s:%s:00Z" % m.groups()


def fetch_dat(kind, http=None, known_sha=None, force=False, check=None):
    """The newest ``kind`` ("geoip" | "geosite") from the first source that gives a good one.

    Returns {"unchanged": True, "sha256", "source"} when the source's sha256 equals ``known_sha`` (not with
    ``force``), else {"data", "sha256", "source", "url", "tag", "date", "size"}. ``check(data)`` — the caller's
    test of the content (decoding): an exception refuses the file and the next source is tried. Raises SourceError
    naming what every source answered."""
    http = http or Http()
    lo, hi = LIMITS[kind]
    errors = []
    for src in SOURCES[kind]:
        try:
            want = parse_sha(http.get(src["sha"], SHA_MAX)["data"])
            if known_sha and want == known_sha and not force:
                return {"unchanged": True, "sha256": want, "source": src["name"]}
            got = http.get(src["url"], hi)
            data = got["data"]
            if len(data) < lo:
                raise util.err(FetchError, "rules.fetch.too_small", size=util.msg_n("util.bytes", len(data)))
            have = hashlib.sha256(data).hexdigest()
            if have != want:
                raise util.err(FetchError, "rules.fetch.sha_mismatch")
            if check is not None:
                check(data)
            tag = tag_of(got.get("chain"))
            return {"data": data, "sha256": have, "source": src["name"], "url": src["url"], "tag": tag,
                    "date": tag_date(tag) or got.get("last_modified"), "size": len(data)}
        except FetchError as e:
            errors.append(util.msg("rules.fetch.src", source=src["name"], error=e))
        except Exception as e:                           # noqa: BLE001 — check() of the caller (DatError, ...)
            errors.append(util.msg("rules.fetch.bad_file", source=src["name"], error=_why(e)))
    raise _kept(SourceError, errors)


def fetch_text(http=None, check=None):
    """A text list of Russian networks (the last resort for the kernel ranges) — {"data", "source", "url",
    "license", "date", "size"}. ``check(data)`` refuses a list (too few networks...). Raises SourceError."""
    http = http or Http()
    lo, hi = LIMITS["text"]
    errors = []
    for src in TEXT_SOURCES:
        try:
            got = http.get(src["url"], hi)
            data = got["data"]
            if len(data) < lo:
                raise util.err(FetchError, "rules.fetch.too_small", size=util.msg_n("util.bytes", len(data)))
            if check is not None:
                check(data)
            return {"data": data, "source": src["name"], "url": src["url"], "license": src["license"],
                    "date": got.get("last_modified"), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        except FetchError as e:
            errors.append(util.msg("rules.fetch.src", source=src["name"], error=e))
        except Exception as e:                           # noqa: BLE001 — check() of the caller
            errors.append(util.msg("rules.fetch.bad_list", source=src["name"], error=_why(e)))
    raise _kept(SourceError, errors)
