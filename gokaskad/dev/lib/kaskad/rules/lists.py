"""The groups of the rules (docs/k3/SPEC-wave3.md §2, as the DEFAULT profile of RoscomVPN) — checked in this
order: block -> always through the exit -> Russia directly -> the route of the device.

    block      category-ads, win-spy (switch «Блокировать рекламу», on by default); torrent — its own switch,
               off by default (then torrents follow the route of the device)
    force      youtube, telegram, github, google-play, twitch-ads, category-geoblock-ru, google-deepmind —
               sites that cut Russian addresses themselves: through the exit even when they resolve to a Russian
               address (a cache of YouTube inside a Russian ISP)
    direct     geoip ``direct`` (most of it: Russian networks, ~35.6 k CIDR -> ~15 k ranges) and the domains of
               private, category-ru, whitelist, apple, microsoft, epicgames, riot, steam, escapefromtarkov,
               twitch, pinterest, faceit

Domains for dnsmasq: ``domain:`` and ``full:`` entries (dnsmasq cannot say «this name only»: ``full:`` also
takes its subdomains — harmless here); ``keyword:`` and ``regexp:`` cannot be said at all — dropped for the
kernel path and counted (xray uses them from the file). A name that is not a plain DNS name (letters, digits,
``-``, ``_``, dots) is dropped too: nothing unvalidated reaches dnsmasq.conf.

Order without surprises: a domain of a later group that equals a domain of an earlier group, or lies under one,
is dropped (``ads.example`` blocked -> ``x.ads.example`` is not «direct»); inside a group a name under another
name of the same group is redundant and dropped. Then dnsmasq's «the longest suffix wins» gives exactly the
order above, and xray (rules in this order, first match wins) agrees with it.

IPv4 ranges for the kernel: CIDRs merged into maximal ranges (``a.b.c.d/n`` when a range is exactly a prefix,
else ``a.b.c.d-e.f.g.h``), IPv6 dropped (the gateway carries IPv4 only) and counted, prefixes shorter than
/8 dropped (no country is a /7: such an entry is a broken list, not «everything directly»).
"""

import ipaddress
import re

from ..i18n import tr

GEOIP_DIRECT = "direct"
BLOCK_ADS = ("category-ads", "win-spy")
BLOCK_TORRENT = ("torrent",)
FORCE = ("youtube", "telegram", "github", "google-play", "twitch-ads", "category-geoblock-ru", "google-deepmind")
DIRECT = ("private", "category-ru", "whitelist", "apple", "microsoft", "epicgames", "riot", "steam",
          "escapefromtarkov", "twitch", "pinterest", "faceit")
# Зарубежные сайты проверки адреса — всегда через выход: при «Россия напрямую» они иначе показывают адрес узла,
# и человек решает, что VPN не работает (стенд T-G1, C2-1). Российские (2ip.ru, yandex.ru/internet) идут
# напрямую по замыслу профиля — так и пишет экран «Правила».
FORCE_EXTRA = ("ifconfig.me", "ifconfig.co", "ipify.org", "icanhazip.com", "checkip.amazonaws.com", "ip.sb",
               "ipapi.is", "ipinfo.io", "ip-api.com", "myip.com", "whatismyipaddress.com", "browserleaks.com",
               "whoer.net", "dnsleaktest.com")
CATEGORIES = BLOCK_ADS + BLOCK_TORRENT + FORCE + DIRECT
MIN_PREFIX4 = 8
_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_DOMAIN = re.compile(r"^(?=.{1,253}$)%s(?:\.%s)*$" % (_LABEL, _LABEL))


# ── IPv4 ranges ────────────────────────────────────────────────────────────

def merge(nets, min_prefix=MIN_PREFIX4):
    """[(network int, prefix)] of IPv4 -> (sorted maximal ranges [(start, end)], dropped too-wide count)."""
    spans = []
    wide = 0
    for net, prefix in nets:
        if prefix < min_prefix:
            wide += 1
            continue
        size = 1 << (32 - prefix)
        start = net & ~(size - 1) & 0xFFFFFFFF
        spans.append((start, start + size - 1))
    spans.sort()
    out = []
    for start, end in spans:
        if out and start <= out[-1][1] + 1:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return [(a, b) for a, b in out], wide


def _ip(n):
    return str(ipaddress.IPv4Address(n))


def range_text(start, end):
    """``a.b.c.d/n`` when the range is exactly one prefix (as nft prints it), else ``a.b.c.d-e.f.g.h``."""
    size = end - start + 1
    if size > 0 and size & (size - 1) == 0 and start % size == 0:
        return "%s/%d" % (_ip(start), 32 - (size.bit_length() - 1))
    return "%s-%s" % (_ip(start), _ip(end))


def ranges_text(ranges):
    return [range_text(a, b) for a, b in ranges]


def parse_range(text):
    """``a.b.c.d/n`` or ``a.b.c.d-e.f.g.h`` (or a bare address) -> (start, end); ValueError if not IPv4."""
    s = text.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        start, end = int(ipaddress.IPv4Address(a.strip())), int(ipaddress.IPv4Address(b.strip()))
        if end < start:
            raise ValueError(tr("rules.lists.empty_range", text=repr(text)))
        return start, end
    n = ipaddress.IPv4Network(s, strict=False)
    return int(n.network_address), int(n.broadcast_address)


def text_cidrs(text):
    """A text list (one CIDR or address per line, ``#`` comments) -> ([(network int, prefix)] of IPv4,
    {"v6": n, "bad": n})."""
    out = []
    counts = {"v6": 0, "bad": 0}
    for line in (text or "").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        try:
            n = ipaddress.ip_network(s, strict=False)
        except ValueError:
            counts["bad"] += 1
            continue
        if n.version == 6:
            counts["v6"] += 1
            continue
        out.append((int(n.network_address), n.prefixlen))
    return out, counts


# ── domains ────────────────────────────────────────────────────────────────

def norm_domain(value):
    """A plain DNS name, lower-case, without the trailing dot; None when it is anything else."""
    s = str(value or "").strip().lower().rstrip(".")
    return s if _DOMAIN.match(s) else None


def category(entries):
    """[(type, value)] of a GeoSite -> {"domains": [names], "keyword": n, "regexp": n, "invalid": n, "full": n,
    "other": n}: names of ``domain:`` and ``full:`` for dnsmasq, the rest counted."""
    from .dat import DOMAIN, FULL, PLAIN, REGEX
    names = set()
    rec = {"keyword": 0, "regexp": 0, "invalid": 0, "full": 0, "other": 0}
    for typ, value in entries:
        if typ == PLAIN:
            rec["keyword"] += 1
            continue
        if typ == REGEX:
            rec["regexp"] += 1
            continue
        if typ not in (DOMAIN, FULL):
            rec["other"] += 1
            continue
        name = norm_domain(value)
        if name is None:
            rec["invalid"] += 1
            continue
        if typ == FULL:
            rec["full"] += 1
        names.add(name)
    rec["domains"] = sorted(names)
    return rec


def _suffixes(name):
    parts = name.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _covered(name, taken):
    return any(s in taken for s in _suffixes(name))


def _minimal(names, earlier):
    """Names not covered by ``earlier`` (equal or a parent there) nor by a shorter name of their own list."""
    own = set()
    out = []
    for name in sorted(set(names), key=lambda n: (n.count("."), n)):       # parents before children
        if _covered(name, earlier) or _covered(name, own):
            continue
        own.add(name)
        out.append(name)
    return sorted(out)


def groups(cats, block_ads=True, block_torrent=False):
    """{code: {"domains": [...], ...}} (``category`` of every code, lower-case keys) -> {"block": [...],
    "force": [...], "direct": [...], "missing": [codes the list does not have]}."""
    def names(codes):
        out = []
        for c in codes:
            out.extend((cats.get(c) or {}).get("domains") or [])
        return out
    block_codes = (BLOCK_ADS if block_ads else ()) + (BLOCK_TORRENT if block_torrent else ())
    block = _minimal(names(block_codes), set())
    taken = set(block)
    force = _minimal(names(FORCE) + list(FORCE_EXTRA), taken)
    taken.update(force)
    direct = _minimal(names(DIRECT), taken)
    missing = [c for c in CATEGORIES if c not in cats]
    return {"block": block, "force": force, "direct": direct, "missing": missing}
