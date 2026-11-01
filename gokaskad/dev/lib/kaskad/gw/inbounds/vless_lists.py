"""Cover sites of VLESS Reality by the country of the server (docs/k3/SPEC-vless-inbound.md §4.2), as goVLESS Pro
(``lib/reality_domains.sh``): a server in Russia hides behind Russian sites, any other one behind international ones —
a Russian mobile network that lets only «white» sites through still lets a Russian site through, and a foreign server
that «talks» to a Russian site all day looks odd.

    RU, INT                 the two lists (popular sites with TLS 1.3 and HTTP/2 when they were collected; the node checks
                            the chosen one from itself before using it)
    detect(st)              -> {"code": "RU" | "DE" | … | "", "source": "ipinfo" | "ip-api" | "local" | ""}: the
                            country of the node's public IPv4 — ipinfo.io, then ip-api.com, then the ranges of Russia
                            the rules of wave 3 keep on the node (no network at all); "" — unknown (international list)
    list_of(code)           -> ("ru" | "int", [sites])

Nothing secret goes anywhere: the requests carry only the node's own public address (which those services see anyway).
"""

import ipaddress
import urllib.request

from kaskad.gw.inbounds.texts import TrMap

GEO_TIMEOUT = 5.0

# ── Russian sites (popular in Russia, reachable there) ──────────────────────
RU = (
    "ya.ru", "yandex.ru", "vk.com", "ok.ru", "dzen.ru", "mail.ru", "ozon.ru", "wildberries.ru", "avito.ru",
    "market.yandex.ru", "music.yandex.ru", "kinopoisk.ru", "yandex.cloud", "1c.ru", "2gis.ru", "aliexpress.ru",
    "amediateka.ru", "auto.ru", "aviasales.ru", "beeline.ru", "book24.ru", "cdek.ru", "chitai-gorod.ru", "cian.ru",
    "detmir.ru", "dodopizza.ru", "drive2.ru", "edadeal.ru", "eldorado.ru", "flowwow.com", "fontanka.ru", "forbes.ru",
    "hh.ru", "igromania.ru", "ixbt.com", "iz.ru", "joom.com", "kaspersky.ru", "kassir.ru", "kinoteatr.ru",
    "kudago.com", "kuper.ru", "level.travel", "megamarket.ru", "more.tv", "mvideo.ru", "netology.ru", "ngs.ru",
    "nix.ru", "nplus1.ru", "okko.tv", "onlinetrade.ru", "overclockers.ru", "perekrestok.ru", "pikabu.ru",
    "rambler.ru", "rb.ru", "re-store.ru", "regard.ru", "s7.ru", "sostav.ru", "sports.ru", "start.ru", "stopgame.ru",
    "superjob.ru", "sutochno.ru", "tass.ru", "tele2.ru", "tutu.ru", "uchi.ru", "vseinstrumenti.ru", "wink.ru",
    "xcom-shop.ru", "yaklass.ru", "zr.ru", "zvuk.com", "4pda.to", "kp.ru", "lamoda.ru", "ria.ru", "sport-express.ru",
    "gloria-jeans.ru", "mybook.ru", "tripster.ru", "okeydostavka.ru", "aif.ru", "pyaterochka.ru", "pogoda.ru",
    "74.ru", "161.ru", "76.ru", "59.ru", "63.ru", "93.ru", "nn.ru", "ufa1.ru", "msk1.ru", "v1.ru", "chita.ru",
)

# ── International sites ──────────────────────────────────────────────────────
INT = (
    "www.microsoft.com", "dl.google.com", "www.apple.com", "www.samsung.com", "www.nvidia.com", "addons.mozilla.org",
    "gateway.icloud.com", "www.icloud.com", "www.google.com", "www.amazon.com", "aws.amazon.com", "www.cloudflare.com",
    "github.com", "stackoverflow.com", "www.mozilla.org", "www.wikipedia.org", "wikimedia.org", "archive.org",
    "medium.com", "www.notion.so", "www.figma.com", "www.canva.com", "slack.com", "zoom.us", "www.dropbox.com",
    "atlassian.com", "www.jetbrains.com", "www.docker.com", "gitlab.com", "www.npmjs.com", "pypi.org",
    "www.rust-lang.org", "www.yahoo.com", "duckduckgo.com", "brave.com", "www.intel.com", "www.amd.com", "www.hp.com",
    "www.ibm.com", "www.spotify.com", "open.spotify.com", "www.netflix.com", "www.twitch.tv", "www.reddit.com",
    "www.pinterest.com", "www.coursera.org", "www.shopify.com", "cdn.jsdelivr.net", "unpkg.com", "vimeo.com",
    "soundcloud.com", "bandcamp.com", "www.tumblr.com", "wordpress.com", "www.wix.com", "squarespace.com",
    "webflow.com", "vercel.com", "www.digitalocean.com", "www.vultr.com", "hetzner.com", "www.flickr.com",
    "www.behance.net", "dribbble.com", "producthunt.com", "ebay.com", "booking.com", "bbc.com", "nytimes.com",
    "theguardian.com", "adobe.com", "python.org", "debian.org", "kernel.org", "ubuntu.com",
)

TITLES = TrMap({"ru": "gw_in_vless.list.ru", "int": "gw_in_vless.list.int"})   # words of the moment (gw/ops.py too)
SOURCES = ("ipinfo", "ip-api", "local")


def list_of(code):
    """-> ("ru" | "int", the sites): Russia — the Russian list, anything else (unknown too) — the international one."""
    return ("ru", RU) if str(code or "").upper() == "RU" else ("int", INT)


def _public_ipv4(st):
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    try:
        a = ipaddress.ip_address(str((g or {}).get("host") or ""))
    except ValueError:
        return ""
    return str(a) if a.version == 4 and a.is_global else ""


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "kaskad", "Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:            # noqa: S310 — fixed https/http URLs
        return r.read(256).decode("ascii", "replace").strip().strip('"')


def _code(s):
    s = str(s or "").strip().upper()
    return s if len(s) == 2 and s.isalpha() else ""


def _local(ip):
    """The ranges of Russia of wave 3 (rules store, ru4.txt) — "RU" when the node's address is inside, else ""."""
    if not ip:
        return ""
    try:
        from kaskad.rules import lists, store
        v = store.active()
        data = v.read(store.RANGES) if v is not None else None
    except Exception:                                   # noqa: BLE001 — no lists yet
        return ""
    if not data:
        return ""
    n = int(ipaddress.IPv4Address(ip))
    for line in data.decode("ascii", "replace").splitlines():
        try:
            a, b = lists.parse_range(line)
        except ValueError:
            continue
        if a <= n <= b:
            return "RU"
    return ""


def detect(st=None, timeout=GEO_TIMEOUT):
    """See the module doc. No exception. Under KASKAD_ROOT (the offline tests, a test bench) — no network: the local
    ranges only."""
    from kaskad import paths
    ip = _public_ipv4(st)
    online = () if paths.root() else (("ipinfo", "https://ipinfo.io/%scountry" % (ip + "/" if ip else "")),
                                      ("ip-api", "http://ip-api.com/line/%s?fields=countryCode" % ip))
    for source, url in online:
        try:
            code = _code(_get(url, timeout))
        except Exception:                               # noqa: BLE001 — any failure of a lookup: the next source
            code = ""
        if code:
            return {"code": code, "source": source}
    code = _local(ip)
    return {"code": code, "source": "local" if code else ""}


_geo = {"fn": detect}                    # tests put a fake here


def country(st=None):
    got = _geo["fn"](st) or {}
    code = _code(got.get("code"))
    src = got.get("source") if got.get("source") in SOURCES and code else ""
    return {"code": code, "source": src}
