"""Test helpers of WP-R (rules «Россия напрямую»): a tiny protobuf ENCODER to build synthetic .dat lists of Xray
(GeoIPList / GeoSiteList, with unknown fields where a newer format could put them), a fake HTTP for
``kaskad.rules.source``, and a store seeded with such lists. No real list is ever used (they are not shipped)."""

import hashlib
import ipaddress
import struct
from unittest import mock

from kaskad.rules import dat, source

# the synthetic lists are small: the minimal sizes of real files (geoip ~425 KB) would refuse them
SMALL_LIMITS = {"geoip": (1 << 10, 32 << 20), "geosite": (100, 32 << 20), "text": (1 << 10, 32 << 20)}


def small_limits():
    """``with small_limits():`` / ``.start()`` — source.LIMITS for the synthetic lists."""
    return mock.patch.dict(source.LIMITS, SMALL_LIMITS)

# ── protobuf encoder ───────────────────────────────────────────────────────


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def key(field, wt):
    return varint((field << 3) | wt)


def f_varint(field, n):
    return key(field, 0) + varint(n)


def f_len(field, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return key(field, 2) + varint(len(data)) + data


def f_i64(field, n):
    return key(field, 1) + struct.pack("<Q", n)


def f_i32(field, n):
    return key(field, 5) + struct.pack("<I", n)


UNKNOWN = f_varint(7, 12345) + f_i64(9, 1) + f_i32(10, 2) + f_len(15, b"\x01\x02future")


def cidr(net, with_prefix=True, unknown=False):
    """A CIDR message; ``with_prefix=False`` writes no prefix at all (proto3: a /0)."""
    n = ipaddress.ip_network(net, strict=False)
    body = f_len(1, n.network_address.packed)
    if with_prefix and n.prefixlen:
        body += f_varint(2, n.prefixlen)
    if unknown:
        body += UNKNOWN
    return body


def geoip_entry(code, nets, reverse=False, unknown=False, raw_cidrs=()):
    body = (UNKNOWN if unknown else b"") + f_len(1, code)
    for n in nets:
        body += f_len(2, cidr(n, unknown=unknown))
    for raw in raw_cidrs:
        body += f_len(2, raw)
    if reverse:
        body += f_varint(3, 1)
    return body


def geoip_dat(entries, unknown_top=False):
    """``entries``: [(code, [cidr, ...])] or [bytes of geoip_entry]."""
    out = b""
    for e in entries:
        body = e if isinstance(e, bytes) else geoip_entry(*e)
        out += f_len(1, body)
        if unknown_top:
            out += f_varint(5, 1) + f_len(6, b"skip me")
    return out


def domain(typ, value, attrs=()):
    body = (f_varint(1, typ) if typ else b"") + f_len(2, value)
    for k in attrs:
        body += f_len(3, f_len(1, k) + f_varint(2, 1))
    return body


def geosite_dat(cats):
    """``cats``: {code: [(type, value) or "domain"]} (a bare string is a ``domain:`` entry)."""
    out = b""
    for code, items in cats.items():
        body = f_len(1, code)
        for it in items:
            typ, value = (dat.DOMAIN, it) if isinstance(it, str) else it
            body += f_len(2, domain(typ, value))
        out += f_len(1, body)
    return out


# ── synthetic lists that pass the checks of kaskad.rules.ops ───────────────

def ru_nets(n=1100, base="5.0.0.0"):
    """``n`` /24 networks, every second one taken (so ~n ranges after the merge), plus a few adjacent pairs."""
    b = int(ipaddress.IPv4Address(base))
    nets = ["%s/24" % ipaddress.IPv4Address(b + (i * 2 << 8)) for i in range(n)]
    nets += ["77.88.0.0/24", "77.88.1.0/24"]                   # adjacent: one range 77.88.0.0/23
    return nets


GEOSITE_CATS = {
    "CATEGORY-ADS": ["ad.mail.ru", "alt-ad.mail.ru"],
    "WIN-SPY": ["vortex.data.microsoft.com", "telemetry.example"],
    "TORRENT": ["tracker.example", "rutracker.example"],
    "YOUTUBE": ["youtube.com", "googlevideo.com", "ytimg.com", (dat.PLAIN, "youtube")],
    "TELEGRAM": ["telegram.org", "t.me"],
    "GITHUB": ["github.com", (dat.REGEX, r"^gh-[a-z]+\.example$")],
    "GOOGLE-PLAY": ["play.google.com"],
    "CATEGORY-GEOBLOCK-RU": ["openai.com", (dat.PLAIN, "netflix")],
    "CATEGORY-RU": ["yandex.ru", "vk.com", "2ip.ru", "music.youtube.com"],
    "WHITELIST": ["gosuslugi.ru", "sber.ru", (dat.FULL, "www.mos.ru"), "Bad Name!"],
    "PRIVATE": [(dat.FULL, "localhost"), "lan"],
    "APPLE": ["apple.com"],
    "MICROSOFT": ["microsoft.com", "telemetry.example"],
}


def geoip_bytes(n=1100):
    return geoip_dat([("DIRECT", ru_nets(n) + ["2a02:6b8::/32"]), ("PRIVATE", ["10.0.0.0/8"]),
                      ("WHITELIST", ["77.88.8.0/24"])])


def geosite_bytes(cats=None):
    return geosite_dat(GEOSITE_CATS if cats is None else cats)


def sha_text(data, path="release/geoip.dat"):
    return ("%s  %s\n" % (hashlib.sha256(data).hexdigest(), path)).encode("ascii")


class FakeHttp(object):
    """``source.Http`` double: ``files`` {url: bytes or an exception text}; every call is recorded."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.calls = []

    def get(self, url, max_bytes):
        self.calls.append(url)
        got = self.files.get(url)
        if got is None:
            raise source.FetchError("HTTP 404")
        if isinstance(got, str):
            raise source.FetchError(got)
        if len(got) > max_bytes:
            raise source.FetchError("файл больше %d КБ" % (max_bytes >> 10))
        chain = []
        if url.startswith("https://github.com/"):
            chain = [url.replace("/releases/latest/download/", "/releases/download/202610040942/")]
        return {"data": got, "url": url, "chain": chain, "last_modified": "Sun, 04 Oct 2026 09:43:13 GMT"}


def release(http=None, geoip=None, geosite=None, mirror=False):
    """A FakeHttp serving these lists from GitHub (``mirror``: from jsDelivr only)."""
    http = http or FakeHttp()
    geoip = geoip_bytes() if geoip is None else geoip
    geosite = geosite_bytes() if geosite is None else geosite
    ip_url = source.JSD_GEOIP if mirror else source.GITHUB_GEOIP
    site_url = source.JSD_GEOSITE if mirror else source.GITHUB_GEOSITE
    http.files[ip_url] = geoip
    http.files[ip_url + ".sha256"] = sha_text(geoip)
    http.files[site_url] = geosite
    http.files[site_url + ".sha256"] = sha_text(geosite, "release/geosite.dat")
    return http


def seed(http=None, **kw):
    """Lists in the store (``ops.update`` without touching the system). Returns the update result."""
    from kaskad.rules import ops
    return ops.update(http=release(http, **kw), apply_live=False, log=lambda text: None)


SMALL_RANGES = ("2.26.8.0/24", "5.8.0.0-5.8.2.255", "77.88.0.0/23")


def seed_small(ranges=SMALL_RANGES, cats=None, geoip=True):
    """A store version with a few ranges (for goldens), the domain categories of GEOSITE_CATS (or ``cats``) and,
    with ``geoip``, a small geoip.dat — written straight into the store (the checks of an update are skipped)."""
    import json
    from kaskad.rules import lists, store
    cats = GEOSITE_CATS if cats is None else cats
    site = geosite_dat(cats)
    parsed = dict((c.lower(), lists.category(e)) for c, e in dat.geosite(site).items())
    files = {store.RANGES: "".join(r + "\n" for r in ranges).encode("ascii"), store.GEOSITE: site,
             store.DOMAINS: json.dumps(parsed, sort_keys=True).encode("ascii")}
    meta = {"created": "2026-10-05T08:00:00Z", "kernel": {"from": "geoip", "ranges": len(ranges), "cidrs": len(ranges),
                                                          "v6": 0, "wide": 0},
            "geosite": {"source": "github", "tag": "202604152235", "codes": sorted(c.upper() for c in cats)}}
    if geoip:
        files[store.GEOIP] = geoip_dat([("DIRECT", ["2.26.8.0/24"]), ("PRIVATE", ["10.0.0.0/8"])])
        meta["geoip"] = {"source": "github", "tag": "202610040942", "date": "2026-10-04T09:42:00Z",
                         "codes": ["DIRECT", "PRIVATE"]}
    return store.commit(files, meta)


RF_CONSENT = {"at": "2026-10-16T00:00:00Z", "via": "cli", "who": None, "orgs": ["meta"], "registry": None,
              "lang": "ru"}


def consented(st):
    """``st`` with the consent ⚠РФ (``gateway.rf_consent``, R3): «Россия напрямую» applies on a node of stage 1
    (``gen 2``) only with it (рубеж 2, R2) — the fixture of the tests of what ru_direct renders. Returns ``st``."""
    st.setdefault("gateway", {})["rf_consent"] = dict(RF_CONSENT)
    return st
