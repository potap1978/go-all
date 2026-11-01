"""«Поделиться» проверкой сервера — the safe answer of ``GET /api/host/share`` (k3/handoff/SPEC-node-score-schedule-share.md
§5.1–5.2 and «Сверка 24.10 (Поделиться без балла)»; C9). The clip and the picture are built by the browser of the owner
from this answer only; the server sends nothing anywhere.

Only what the frame of the design r1 shows (``check-share.js``): the raw numbers of four links (download, upload,
latency, loss), «в целом N от обычного» (``rel_total``, 100 — as usual for this node), the colours of the 30 days
(no reasons), the day of the latest good check and the part of the day it ran in. By the switches only (all off by
default): the machine (cores and memory of the inventory of the latest check, the port of the tariff), the country (an
ISO code of the list), the model of the CPU — the one free text, through ``share_text``. Never: addresses, domains,
names of the node, of people, devices or exits, keys, tokens, ports of the node, the zone, the
version. The time of the latest check (``at``, UTC) and the names of the points it measured to (``where``) — yes:
the owner's decision 24.10 (the frame shows them; the addresses of the owner's own anchors never go out).

The CORE (process/BLOCKS.md): ``build``, ``share_text``, ``collect_secrets`` are pure (the inputs as parameters);
``node_share`` is the wiring (the score of the node, the inventory, the country, the tariff, the secrets).

    build(doc, date, inv, port_mbit, country, flags, secrets)  -> the answer (every leaf checked for its type)
    share_text(s, secrets)       -> (text, None) | (None, why)  the model of the CPU after the filter of §5.2
    collect_secrets(st, cfg, hostnames, addrs)  -> [values]     what never goes out (§5.2 п. 4)
    node_share(flags, now=None)  -> the answer of this node
"""

import ipaddress
import json
import math
import re
import unicodedata

V = 1
FLAGS = ("tariff", "country", "cpu")
# the links of the frame: (id of the answer, id of the link of score_doc, the test inside a group or None)
METRICS = (("down", "down", None), ("up", "up", None), ("rtt", "net_q", "rtt"), ("loss", "loss", None))
UNITS = {"down": "Мбит/с", "up": "Мбит/с", "rtt": "мс", "loss": "%"}      # i18n: skip — the units of score.SPEC
COLORS = ("green", "yellow", "red", "grey")
PARTS = ("night", "morning", "day", "evening", "mixed")
WHY = ("no_check", "stale")
STRIP = 30
VALUE_MAX = 1e7                      # a raw number of the frame: finite, 0 … 10^7
REL_MAX = 300                        # score.REL_MAX
CPU_MAX = 48                         # signs of the model of the CPU after the filter
SECRET_MIN = 3                       # shorter values give false matches (§5.2 п. 4)
VCPU_MAX = 1024
RAM_MIN, RAM_MAX = 0.25, 65536.0
PORT_MAX = 100000
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")           # the id of a point of test 8/8b (spool._TARGET)
DNS_OF = {"1.1.1.1": "cloudflare", "8.8.8.8": "google", "77.88.8.8": "yandex"}   # hostping.DEFAULT_ANCHORS -> name
# ISO 3166-1 alpha-2 (the officially assigned codes): a country of the frame is one of them, else nothing
ISO = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC
CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH
KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC
SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split())

# §5.2 п. 3: what drops the free text
_SPLIT = re.compile(r"[\s,;()\[\]<>\"']+")
_IP4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_MAIL = re.compile(r"\S+@\S+\.\S+")
_DOMAIN = re.compile(r"(?i)\b[\w-]{1,63}(?:\.[\w-]{1,63})*\.[^\W\d_]{2,24}\b")
_KEY = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_COMPACT = re.compile(r"\s*([.:])\s*")
# §5.2 п. 4: the keys of state.json whose texts are names or addresses (any depth); every value that is an address too
_SECRET_KEYS = re.compile(r"(?i)(name|host|domain|addr|address|^ip|ip$|ip4|ip6|server|endpoint|login|sni|url|label|"
                          r"title|path|token|key|psk|pass|secret|uuid|^id$|email|mail|phone|comment|note)")


# ── pure ───────────────────────────────────────────────────────────────────

def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or math.isnan(x) or math.isinf(x):
        return None
    return x


def _int_in(x, lo, hi):
    return x if isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi else None


def _is_ip(tok):
    t = tok.strip()
    if not t:
        return False
    if t.startswith("[") and "]" in t:                      # [2001:db8::1]:443
        t = t[1:t.index("]")]
    for cand in (t, t.rsplit(":", 1)[0] if t.count(":") == 1 else t):
        try:
            ipaddress.ip_address(cand)
            return True
        except ValueError:
            continue
    return False


def _variants(s):
    """The value and its other spelling of a domain (idna both ways): «сервер.рф» ↔ «xn--…»."""
    out = {s}
    try:
        out.add(s.encode("idna").decode("ascii"))
    except (UnicodeError, ValueError):
        pass
    if "xn--" in s:
        try:
            out.add(s.encode("ascii").decode("idna"))
        except (UnicodeError, ValueError):
            pass
    return out


def share_text(s, secrets=()):
    """The model of the CPU for the frame (§5.2): NFKC; only printable ASCII 0x20–0x7E (a sign of another script, an
    invisible or a look-alike drops the field — the hypervisor of the hoster writes this string); spaces collapsed;
    ≤ CPU_MAX signs; no address, mail, domain, key, markup or value of ``secrets`` in the text or in its compact copy
    (no spaces around dots and colons, lower case). -> (text, None) | (None, why)."""
    if not isinstance(s, str):
        return None, "type"
    s = unicodedata.normalize("NFKC", s)
    if any(not 0x20 <= ord(c) <= 0x7E for c in s):
        return None, "chars"
    s = " ".join(s.split())
    if not s:
        return None, "empty"
    if len(s) > CPU_MAX:
        return None, "long"
    low = s.lower()
    compact = _COMPACT.sub(r"\1", low)
    for copy in (low, compact):
        if "<" in copy or ">" in copy:
            return None, "markup"
        if "://" in copy or "www." in copy or _MAIL.search(copy):
            return None, "address"
        if _IP4.search(copy) or any(_is_ip(t) for t in _SPLIT.split(copy) if t):
            return None, "address"
        if _DOMAIN.search(copy):
            return None, "domain"
        if _KEY.search(copy):
            return None, "key"
    for v in secrets or ():
        if not isinstance(v, str) or len(v) < SECRET_MIN:
            continue
        for x in _variants(v.lower()):
            if x in low or x in compact or _COMPACT.sub(r"\1", x) in compact:
                return None, "secret"
    return s, None


def collect_secrets(st, cfg=None, hostnames=(), addrs=()):
    """What never goes out (§5.2 п. 4): from state.json — every text under a key of a name or an address at any depth
    (exits, people, devices, the site, the subscriptions, the node) and every text that is an address; the domain,
    the address and the secret prefix of the panel (``panel/conf``); the names of the host; the addresses of the
    node. Values shorter than SECRET_MIN are left out. -> sorted list."""
    out = set()

    def add(v):
        if isinstance(v, str):
            v = v.strip()
            if len(v) >= SECRET_MIN:
                out.update(_variants(v))

    def walk(x, key, depth):
        if depth > 12:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(k, str) and _is_ip(k):
                    add(k)
                walk(v, str(k), depth + 1)
        elif isinstance(x, list):
            for v in x[:5000]:
                walk(v, key, depth + 1)
        elif isinstance(x, str):
            if _SECRET_KEYS.search(key or "") or _is_ip(x):
                add(x)

    walk(st if isinstance(st, dict) else {}, "", 0)
    c = cfg if isinstance(cfg, dict) else {}
    for part in ("proxy", "public"):
        sub = c.get(part) if isinstance(c.get(part), dict) else {}
        for k in ("domain", "path", "host", "bind"):
            add(sub.get(k))
    for v in list(hostnames or ()) + list(addrs or ()):
        add(v)
    return sorted(out)


def _metric(doc, mid, link, test):
    lk = next((x for x in doc.get("links") or () if isinstance(x, dict) and x.get("id") == link), None)
    if lk is None:
        return None
    if test is not None:
        lk = next((p for p in lk.get("parts") or () if isinstance(p, dict) and p.get("test") == test), None)
        if lk is None:
            return None
    v = _num(lk.get("fresh_raw"))
    if v is None or not 0 <= v <= VALUE_MAX:
        return None
    return {"id": mid, "v": round(float(v), 3), "unit": UNITS[mid]}


def _days(doc):
    cols = [d.get("color") if isinstance(d, dict) and d.get("color") in COLORS else "grey"
            for d in (doc.get("days") or ())][-STRIP:]
    return ["grey"] * (STRIP - len(cols)) + cols


def _checked(sm):
    ch = sm.get("checked") if isinstance(sm.get("checked"), dict) else {}
    part = ch.get("part")
    return part if part in PARTS else None


def build(doc, date=None, inv=None, port_mbit=None, country=None, flags=(), secrets=(), people=False, where=None):
    """The answer of /api/host/share from ``doc`` (``hostscore.score_doc``) — every leaf of a type of the list:
    ``ready`` false with ``why`` ``no_check`` (no good check) or ``stale`` (none within the days of the fresh values) —
    nothing else; ``date`` — YYYY-MM-DD of the latest good check (KASKAD_TZ, by the caller), ``at`` — its moment (s, UTC)
    for the times UTC and Moscow next to the date (владелец 24.10); ``inv`` — the inventory of
    that check ({"nproc", "mem_gb", "cpu"}); ``port_mbit`` — the port of the tariff; ``country`` — two letters;
    ``flags`` — the switches (FLAGS); ``people`` — the row «Связь с людьми» has a value; ``where`` — where the numbers
    were measured to (владелец 24.10: «чьи это цифры»): {"down", "up": the id of the point of test 8/8b | None,
    "anchors": [the names of the public DNS of hostping.DEFAULT_ANCHORS in use: cloudflare | google | yandex] | "own"
    | None, "n": the number of the anchors} — names only, no address at all (§5.1)."""
    flags = set(f for f in flags or () if f in FLAGS)
    sm = doc.get("summary") if isinstance(doc, dict) and isinstance(doc.get("summary"), dict) else {}
    last = _num(sm.get("last_at"))
    if not last or last <= 0 or not (isinstance(date, str) and DATE.fullmatch(date)):
        return {"v": V, "ok": True, "ready": False, "why": "no_check"}
    if sm.get("stale") is True:
        return {"v": V, "ok": True, "ready": False, "why": "stale"}
    metrics = [m for m in (_metric(doc, *x) for x in METRICS) if m is not None]
    out = {"v": V, "ok": True, "ready": True, "why": None, "date": date,
           "at": int(last),                                   # секунды UTC той же проверки: время UTC и МСК в кадре
           "where": _where_of(where),
           "checked": {"part": _checked(sm), "people": people is True},
           "metrics": metrics,
           "rel_total": _int_in(sm.get("rel_total"), 0, REL_MAX),
           "rel_n": _int_in(sm.get("rel_n"), 0, 64) or 0,
           "rel_need": _int_in(sm.get("rel_need"), 1, 64) or 3,
           "days": _days(doc),
           "extra": {"tariff": None, "country": None, "cpu": None},
           "dropped": []}
    inv = inv if isinstance(inv, dict) else {}
    if "tariff" in flags:
        vcpu = _int_in(inv.get("nproc"), 1, VCPU_MAX)
        ram = _num(inv.get("mem_gb"))
        ram = round(float(ram), 1) if ram is not None and RAM_MIN <= ram <= RAM_MAX else None
        port = _int_in(port_mbit, 1, PORT_MAX)
        if vcpu is not None or ram is not None or port is not None:
            out["extra"]["tariff"] = {"vcpu": vcpu, "ram_gb": ram, "port_mbit": port}
    if "country" in flags and country is not None:
        if isinstance(country, str) and country in ISO:
            out["extra"]["country"] = country
        else:
            out["dropped"].append("country")
    if "cpu" in flags and inv.get("cpu") is not None:
        text, _why = share_text(inv.get("cpu"), secrets)
        if text is None:
            out["dropped"].append("cpu")
        else:
            out["extra"]["cpu"] = text
    if out["extra"]["cpu"] is not None and leaks(out, secrets):   # the last guard: the only free text goes
        out["extra"]["cpu"] = None
        out["dropped"].append("cpu")
    return out


def _where_of(w):
    """``where`` of the answer: the ids of the points as ``spool`` keeps them, the kind of the anchors — else None."""
    w = w if isinstance(w, dict) else {}
    pt = lambda x: x if isinstance(x, str) and TARGET.fullmatch(x) else None
    a = w.get("anchors")
    if isinstance(a, (list, tuple)):                 # only the names of the public DNS of the defaults, all or nothing
        a = list(a) if a and all(x in DNS_OF.values() for x in a) else None
    elif a != "own":
        a = None
    n = _int_in(w.get("n"), 1, 16) if a else None
    return {"down": pt(w.get("down")), "up": pt(w.get("up")), "anchors": a, "n": n}


def leaks(obj, secrets=()):
    """Any value of ``secrets`` (≥ SECRET_MIN, case-insensitive) in the serialized ``obj``?"""
    text = json.dumps(obj, ensure_ascii=False).lower()
    return any(isinstance(v, str) and len(v) >= SECRET_MIN and v.lower() in text for v in secrets or ())


# ── wiring ─────────────────────────────────────────────────────────────────

def _last_inv(now):
    """(the inventory, ts) of the latest good check (history.sample: the base and the queue)."""
    from . import history
    from .. import score as S
    runs = [r for r in history.sample(now)["runs"] if r.get("status") in S.GOOD_STATUS and r.get("ts") is not None
            and r["ts"] <= now]
    if not runs:
        return {}, None
    r = max(runs, key=lambda x: x["ts"])
    return (r.get("inv") if isinstance(r.get("inv"), dict) else {}), r["ts"]


def _hostnames():
    import socket
    from .. import util
    names = []
    try:
        names.append((util.read_text("/etc/hostname") or "").strip())
    except OSError:
        pass
    try:
        names.append(socket.gethostname())
    except OSError:
        pass
    return [n for n in names if n]


def node_secrets(st):
    """``collect_secrets`` of this node: state.json, the panel's settings, the names of the host, the addresses."""
    from ..gw import hostprobe, model
    try:
        from ..panel import conf
        cfg = conf.load()
    except Exception:                                # noqa: BLE001 — no panel: nothing of it to hide
        cfg = {}
    addrs = list(hostprobe.own_addrs()) + [model.public_host(st)]
    return collect_secrets(st, cfg, _hostnames(), [a for a in addrs if a])


def _where(st, now, runs=5):
    """Where the numbers of the frame were measured to: the points of test 8 (``net_down``/``net_up``) of the latest
    good checks that have them (``history.run`` of ≤ ``runs`` checks) and the anchors of the light probes —
    the list when every one is a public DNS of hostping.DEFAULT_ANCHORS (1.1.1.1, 8.8.8.8, 77.88.8.8), else «own» with
    their number (the addresses of the owner's points never go out)."""
    from . import history
    from .. import score as S
    from ..gw import hostping, model
    out = {"down": None, "up": None, "anchors": None, "n": None}
    try:
        rs = sorted((r for r in history.sample(now)["runs"] if r.get("status") in S.GOOD_STATUS
                     and r.get("ts") is not None and r["ts"] <= now), key=lambda x: -x["ts"])[:runs]
        for r in rs:
            for t in (history.run(r["id"]) or {}).get("tests") or ():
                side = {"net_down": "down", "net_up": "up"}.get(t.get("test"))
                if side and out[side] is None and t.get("value") is not None and t.get("target"):
                    out[side] = t["target"]
            if out["down"] and out["up"]:
                break
    except Exception:                                # noqa: BLE001 — no history: the frame without the points
        pass
    try:
        an = list(model.hostmon(st).get("anchors") or ())
        if an:
            out["anchors"] = [DNS_OF[x] for x in an] if all(x in DNS_OF for x in an) else "own"
            out["n"] = len(an)
    except Exception:                                # noqa: BLE001
        pass
    return out


def node_share(flags=(), now=None):
    """The answer of GET /api/host/share of this node (the panel, ``kaskadctl host share``, MCP)."""
    import time
    from . import geo, hostscore
    from . import tariff as btariff
    from .. import score as S
    from .. import state, tz
    from ..gw import model
    now = int(time.time() if now is None else now)
    st = state.load()
    doc = hostscore.score_doc(S.load_spec(), now=now, rule=model.dev_rule(st))
    flags = set(f for f in flags or () if f in FLAGS)
    inv = _last_inv(now)[0] if flags & {"tariff", "cpu"} else {}
    last = doc["summary"].get("last_at")
    date = tz.day(last) if isinstance(last, int) and not isinstance(last, bool) and last > 0 else None
    port = btariff.port_mbit(model.tariff(st)[0]) if "tariff" in flags else None
    country = geo.country(st).get("country") if "country" in flags else None
    people = (doc.get("rows") or {}).get("people")
    people = isinstance(people, dict) and not people.get("why")
    secrets = node_secrets(st) if "cpu" in flags else ()
    return build(doc, date, inv, port, country, flags, secrets, people, _where(st, now))
