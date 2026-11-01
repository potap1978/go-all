"""«Куда пойдёт трафик?» — the path of one connection through the gateway, without a command (the panel's
``POST /api/gw/trace``, ``kaskadctl gw trace``, menu 17 → 9 and the bot share this one function).

Who: a device (``d-…``, its login or name), a person (``u-…`` or the name: a device of theirs WITHOUT settings of
its own — the person's route and profile), or nobody (``-``: a new device with the defaults). Where: a domain
(a link is fine: only its name is taken) or an IPv4 address. Through: ONE inbound of the device (``inbound``; by
default the first enabled one in the order they were given) — a connection comes through one inbound, and that is
the only one the answer names and lights: SOCKS5 and VLESS are decided by xray, every other inbound by nft and dnsmasq.

The decision repeats the data path of the gateway (kaskad.gw.net «gw_pre», kaskad.gw.dns, kaskad.rules.apply):

    off       the device or its person is off — not in gw_live: no traffic at all
    block     the domain is in the block group (reklama/telemetry ``block_ads``, torrents ``block_torrent``):
              dnsmasq answers it the same way for every client — global, whatever the profile; the group is
              found by the LONGEST suffix among block / force / direct (dnsmasq's rule; the groups are minimal,
              so a name is in one group only), the category by an index of ``rules.apply.categories()``
    route     the mark of the device (``exits.target``: device -> person -> default; pool, exit, direct, block;
              dead exits -> the fail policy). A block mark stays a block: «Россия напрямую» never opens it
    ru        the applied profile ru_direct (lists stored) and a mark that is not block: a force domain stays on
              the route (gw_force) — even on a Russian address; a direct domain goes directly (gw_ru_dns);
              otherwise the address (given, or the domain's addresses from the node's resolver, in a thread with
              a timeout, cached) in the networks of Russia (ru4.txt of the store, binary search, cached per
              version of the lists) goes directly — only while those networks are loaded into the table
              (``rules.ops._table_status``: the state of the last load, no ``nft`` call)
    exit      what the route gives now: the exit (by the watchdog's liveness), the balancer of the VLESS exits

Domains of dnsmasq need its ``nftset`` (``dns.saved_features``): without it force/direct domains are not marked
and only the networks of Russia count. SOCKS5 is decided by xray from the same lists (plus ``keyword:`` and
``regexp:`` of the geosite, which dnsmasq cannot say) — the answer says so. Nothing here changes anything:
the resolver is the only outside call (read-only, never the gateway's dnsmasq: an answer of it would fill the sets).

The answer: ``verdict`` (block | direct | exit), ``summary`` (one line for people), ``steps`` [{text, note, tone}],
``notes`` (limits of this answer), ``rf`` ({org, name}: the domain is of an organisation of the registry ⚠РФ —
``rules.rfmark``, docs/k3/SPEC-stage1-rf-mark-builtin.md §5.1, R5; else None), ``ru`` (the Russian group of the
device when «Россия напрямую» acts and the target is Russian: {group, name, step, exit, tier, since, offline, chain} —
SPEC-stage1-exit-groups §9, Q7; else None), ``exit`` and ``highlight`` — ONE chain the scheme lights up (``chain()``):

    device row -> access:<dev>:<kind> -> in:<kind> -> flow:<kind> -> filter
        (its row «реклама» | «торренты»: the path ends there — dropped by the DNS of the gateway, for everybody)
        -> pass:<profile> -> rules:<profile> -> cat:<profile>:ru -> direct       («Россия напрямую»)
                                             -> route:<profile>:<via> -> <via> [-> member:<exit> -> exit:<exit>]
                                             -> fail:<profile>:<via>:<fall> -> block | direct  (dead exits: the fail policy)

``highlight``: ``nodes``, ``edges`` (these ids — kaskad.panel.scheme and graph.js draw the same), ``rows`` ({node id:
the row lit in it}), ``devices``, ``inbound``, ``profile``, ``category`` (ads | torrent | ru | rest), ``via`` (the
route), ``target`` (where it goes now), ``exit``, ``balancer`` («xray» — the kernel sends it to xray's balancer of
the xray exits of the pool ("vless" before stage 1), «any» — SOCKS5: xray's balancer among every live exit; one of ``exits``).
"""

import bisect
import ipaddress
import re
import socket
import threading
import time

from ..errors import USAGE, KaskadError
from ..i18n import tr
from . import exits, model

RESOLVE_TIMEOUT = 3.0                # s: the node's resolver; a hanging one must not hang the panel
RESOLVE_TTL = 120                    # s an answer is reused
RESOLVE_MAX = 8                      # addresses of a domain looked at
_now = time.time                     # tests patch it

SOCKS = "socks5"
XRAY_IN = model.XRAY_KINDS           # inbounds xray decides for (SOCKS5, VLESS)
# ping через VLESS-выход (T-M6-qs330c №3): xray выхода передаёт TCP и UDP, ICMP — нет. Это не поломка: сайты и
# приложения открываются, а 100 % потерь ping человек принимал за «всё сломано». ``icmp: False`` в ответе — то же для
# страниц; проверки здоровья выходов ping не используют (xray — запросом через выход, AmneziaWG — рукопожатием).
# KERNEL_HINT, VLESS_PING_NOTE, PROFILE_TITLES, FROM_TEXT, GROUP_TEXT — texts of the language of the reader: module
# ``__getattr__`` below (constants would freeze the language of the import).

_lock = threading.Lock()
_dns_cache = {}                      # name -> (until, ips, error code: _RESOLVE_ERR — said in the reader's language)
_ranges_cache = {}                   # sha256 of ru4.txt -> (starts, ends)
_index_cache = {}                    # id of the categories -> {domain: [categories]}
_sets_cache = {}                     # the groups of rules.apply -> the same with sets


# ── input ──────────────────────────────────────────────────────────────────

def parse_target(text):
    """«youtube.com», «https://www.YouTube.com/watch?v=1», «ya.ru.», «8.8.8.8», «яндекс.рф» ->
    {"input", "kind": "domain"|"ip", "value"}. KaskadError (USAGE) when it is neither."""
    raw = str(text or "").strip()
    if not raw:
        raise KaskadError(tr("gw_trace.err.empty"), code=USAGE)
    if len(raw) > 2048:
        raise KaskadError(tr("gw_trace.err.too_long"), code=USAGE)
    s = raw
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", s)      # схема ссылки
    s = re.split(r"[/?#\s]", s, maxsplit=1)[0]                       # путь, запрос
    s = s.rsplit("@", 1)[-1]                                # user@host
    if s.startswith("["):
        raise KaskadError(tr("gw_trace.err.ipv6"), code=USAGE)
    if s.count(":") == 1:
        s = s.split(":", 1)[0]                              # порт
    s = s.strip().rstrip(".").lower()
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.version == 6:
            raise KaskadError(tr("gw_trace.err.ipv6"), code=USAGE)
        return {"input": raw, "kind": "ip", "value": str(ip)}
    if re.match(r"^[0-9.]+$", s):
        raise KaskadError(tr("gw_trace.err.not_ipv4", value=s[:64]), code=USAGE)
    name = s
    if any(ord(c) > 127 for c in name):
        try:
            name = name.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            name = ""
    from ..rules import lists
    norm = lists.norm_domain(name)
    if not norm or "." not in norm:
        raise KaskadError(tr("gw_trace.err.not_target", value=raw[:64]), code=USAGE)
    return {"input": raw, "kind": "domain", "value": norm}


def resolve_who(st, ref):
    """{"kind": "device"|"user"|"new", "device": dict (a real one or the one the answer imagines), "user": dict|None}.
    ``ref``: None / "" / "-" — a new device with the defaults; ``d-…``, login or name of a device; ``u-…`` or the name
    of a person (a device of theirs without settings of its own)."""
    r = str(ref or "").strip()
    if r in ("", "-", "new", "default"):
        return {"kind": "new", "device": {"id": None, "user": None, "route": None, "rules": None, "enabled": True,
                                          "access": []}, "user": None}
    d = None if r.startswith("u-") else model.find_device(st, r)
    if d is not None:
        return {"kind": "device", "device": d, "user": model.find_user(st, d.get("user"))}
    u = None if r.startswith("d-") else model.find_user(st, r)
    if u is not None:
        return {"kind": "user", "device": {"id": None, "user": u.get("id"), "route": None, "rules": None,
                                           "enabled": True, "access": []}, "user": u}
    raise KaskadError(tr("gw_trace.err.no_who", ref=r[:64]), code=USAGE)


# ── the lists ──────────────────────────────────────────────────────────────

def _category_index(cats):
    """{domain: [categories]} of the stored geosite — which category a suffix came from."""
    key = id(cats)
    with _lock:
        hit = _index_cache.get(key)
        if hit is not None and hit[0] is cats:
            return hit[1]
    idx = {}
    for code in sorted(cats or {}):
        for name in (cats[code] or {}).get("domains") or []:
            idx.setdefault(name, []).append(code)
    with _lock:
        _index_cache.clear()
        _index_cache[key] = (cats, idx)
    return idx


def match_domain(name, groups, cats=None):
    """The group a name falls into, as dnsmasq decides — the longest suffix among block / force / direct:
    {"group", "suffix", "categories": [...]} or None."""
    if not groups:
        return None
    sets = {}
    for g in ("block", "force", "direct"):
        sets[g] = groups.get("%s_set" % g) or set(groups.get(g) or ())
    parts = name.split(".")
    for i in range(len(parts)):
        suffix = ".".join(parts[i:])
        for g in ("block", "force", "direct"):
            if suffix in sets[g]:
                found = _category_index(cats).get(suffix, []) if cats else []
                return {"group": g, "suffix": suffix, "categories": _relevant(g, found)}
    return None


def _with_sets(groups):
    """The groups with a set of each (``match_domain`` looks a suffix up), kept while the groups are the same."""
    if groups is None:
        return None
    with _lock:
        hit = _sets_cache.get("g")
        if hit is not None and hit[0] is groups:
            return hit[1]
    out = dict(groups)
    for g in ("block", "force", "direct"):
        out["%s_set" % g] = set(groups.get(g) or ())
    with _lock:
        _sets_cache["g"] = (groups, out)
    return out


def _relevant(group, codes):
    """Categories of the group itself first (a name can be in category-ru AND in the blocked category-ads)."""
    from ..rules import lists
    own = {"block": lists.BLOCK_ADS + lists.BLOCK_TORRENT, "force": lists.FORCE, "direct": lists.DIRECT}[group]
    mine = [c for c in codes if c in own]
    return mine or list(codes)


def _ranges(v):
    """(starts, ends) of the networks of Russia of the lists ``v`` — parsed once per version (sha256)."""
    from ..rules import apply as rapply
    from ..rules import lists
    k = rapply.kernel(v)
    if not k["ready"]:
        return None
    sha = k["sha256"]
    with _lock:
        hit = _ranges_cache.get(sha)
    if hit is not None:
        return hit
    spans = []
    for text in k["ranges"]:
        try:
            spans.append(lists.parse_range(text))
        except ValueError:
            continue
    spans.sort()
    hit = ([a for a, _ in spans], [b for _, b in spans])
    with _lock:
        _ranges_cache.clear()
        _ranges_cache[sha] = hit
    return hit


def in_ranges(ip, ranges):
    if not ranges:
        return False
    n = int(ipaddress.IPv4Address(ip))
    i = bisect.bisect_right(ranges[0], n) - 1
    return i >= 0 and ranges[1][i] >= n


# ── the resolver ───────────────────────────────────────────────────────────

def _getaddrinfo(name):
    infos = socket.getaddrinfo(name, None, socket.AF_INET, socket.SOCK_STREAM)
    out = []
    for info in infos:
        ip = info[4][0]
        if ip not in out:
            out.append(ip)
    return out


def resolve(name, timeout=RESOLVE_TIMEOUT):
    """IPv4 addresses of ``name`` from the node's resolver: (ips, error). A thread with a timeout (getaddrinfo
    has none), answers cached for RESOLVE_TTL."""
    now = _now()
    with _lock:
        hit = _dns_cache.get(name)
        if hit is not None and hit[0] > now:
            return list(hit[1]), _resolve_err(hit[2])
    box = {}

    def run():
        try:
            box["ips"] = _getaddrinfo(name)
        except (OSError, UnicodeError) as e:
            box["error"] = e
    t = threading.Thread(target=run, name="kaskad-trace-dns", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return [], tr("gw_trace.dns.timeout", sec=int(timeout))
    if "error" in box:
        e = box["error"]
        err = "nxdomain" if isinstance(e, socket.gaierror) and e.errno in (
            getattr(socket, "EAI_NONAME", -2), getattr(socket, "EAI_NODATA", -5)) else "no_answer"
        ips = []
    else:
        ips = [ip for ip in box.get("ips") or [] if _v4(ip)][:RESOLVE_MAX]
        err = None if ips else "no_v4"
    with _lock:
        if len(_dns_cache) > 512:
            _dns_cache.clear()
        _dns_cache[name] = (now + RESOLVE_TTL, list(ips), err)
    return ips, _resolve_err(err)


def _resolve_err(code):
    """The text of an error code of ``resolve`` (the cache keeps codes: readers speak different languages)."""
    return tr("gw_trace.dns." + code) if code else None


def _v4(ip):
    try:
        return ipaddress.ip_address(ip).version == 4
    except ValueError:
        return False


# ── the context (what the panel and the CLI read differently) ──────────────

def context(st, pool=None, snap=None, v=None, table=None, dns_feats=None, socks_files=None):
    """Everything the decision reads, gathered without a command. ``pool``/``snap`` — the exits (the panel passes
    its public cache), else ``exits.current``; the rest — from the store, the state of the last load into the table
    and the last apply of dnsmasq."""
    from ..rules import apply as rapply
    from ..rules import ops as rops
    if pool is None or snap is None:
        p, s = exits.current(st)
        pool = p if pool is None else pool
        snap = s if snap is None else snap
    v = rapply.version() if v is None else v
    if table is None:
        try:
            table = rops._table_status(None)
        except Exception:                               # noqa: BLE001 — a broken state file: unknown
            table = {"loaded": None}
    if dns_feats is None:
        from . import dns
        dns_feats = dns.saved_features()
    if socks_files is None:
        socks_files = rapply.manifest()
    cats = rapply.categories(v)
    groups = _with_sets(rapply.groups(st, v)) if cats is not None else None
    return {"pool": pool, "snap": snap, "alive": exits.alive_ids(snap), "v": v, "ready": rapply.ready(v),
            "decision": exits.decision(st, snap, pool),               # stage 1 (§2.2): the pins of the collector
            "cats": cats, "groups": groups, "table": table or {}, "nftset": (dns_feats or {}).get("nftset"),
            "socks_files": socks_files or {}, "conf": model.rules_conf(st), "live": _live_ru()}


def _live_ru():
    """What the collector said of the Russian side (Q7, SPEC-groups §9), files only: {"no_exit": {device: {step,
    group, since}} of the Russian groups (``no_exit_ru`` of a fresh live.json; stale — {}), "ru_offline": {"since"}
    of an open outage of the Russian addresses (``live.ru_outage_open``) or None}."""
    from . import live
    out = {"no_exit": {}, "ru_offline": None}
    try:
        doc, age = live.read()
        if live.fresh(age) and isinstance(doc, dict) and isinstance(doc.get("no_exit_ru"), dict):
            out["no_exit"] = doc["no_exit_ru"]
        out["ru_offline"] = live.ru_outage_open()
    except (OSError, ValueError, TypeError, AttributeError):    # a broken file: nothing known, the path as it is
        pass
    return out


# ── the chain the scheme lights ────────────────────────────────────────────

FILTER = "filter"


def chain(user=None, device=None, inbound=None, profile="all_exit", category="rest", via=None, target=None,
          exit_id=None, balancer=False, exits=()):
    """The ids of ONE path on the scheme (module doc): {"nodes", "edges", "rows"}. ``category`` — what decided it
    (ads | torrent: the filter of the node; ru: «Россия напрямую»; rest: the route; off: nothing goes), ``via`` — the
    route, ``target`` — where it goes now (``via``, or the fail policy's direct | block), ``exit_id`` — the exit now
    (a route through the pool: its row in the pool), ``balancer`` — xray picks among ``exits``: «xray» (the kernel
    hands it to the balancer of the xray exits: their group row «vless») or «any» (SOCKS5: every live exit). Then the line
    branches at its END only, where xray chooses: ``alts`` — the links pool -> each candidate («выберет xray»)."""
    nodes, edges, rows, alts = [], [], {}, []

    def n(x):
        if x and x not in nodes:
            nodes.append(x)

    def e(x):
        if x and x not in edges:
            edges.append(x)
    n(user)
    if category == "off":
        return {"nodes": nodes, "edges": edges, "rows": rows, "alts": alts}
    if device and inbound:
        e("access:%s:%s" % (device, inbound))
        n("in:" + inbound)
        e("flow:" + inbound)
    n(FILTER)
    if category in ("ads", "torrent"):
        # отсеивает сам фильтр (DNS шлюза отвечает «нет адреса»): путь кончается его строкой, не в узле «Блок» — тот
        # про маршрут «блок» и отказ «блок» (ревизия М2)
        rows[FILTER] = category
        return {"nodes": nodes, "edges": edges, "rows": rows, "alts": alts}
    rows[FILTER] = "pass"
    rules = "rules:" + (profile or "all_exit")
    e("pass:" + (profile or "all_exit"))
    n(rules)
    if category == "ru":
        rows[rules] = "ru"
        e("cat:%s:ru" % (profile or "all_exit"))
        # T-q Н-trace: a Russian exit (``exit:<id>``) or the last step «блок» of the Russian group, else the node
        n(target if target == "block" or str(target or "").startswith("exit:") else "direct")
        return {"nodes": nodes, "edges": edges, "rows": rows, "alts": alts}
    rows[rules] = "rest"
    via = via or "pool"
    target = target or via
    if target != via and target in ("direct", "block"):
        e("fail:%s:%s:%s" % (profile or "all_exit", via, target))
        n(target)
        return {"nodes": nodes, "edges": edges, "rows": rows, "alts": alts}
    e("route:%s:%s" % (profile or "all_exit", via))
    n(via)
    if via == "pool":
        if balancer:
            # the group row of the xray exits (its id in the scheme); ``exits`` here is the list, not the module
            if balancer in ("xray", "vless"):
                rows["pool"] = "vless"
            for x in exits or ():
                if "member:" + x not in alts:
                    alts.append("member:" + x)
        elif exit_id:
            rows["pool"] = exit_id
            e("member:" + exit_id)
            n("exit:" + exit_id)
    return {"nodes": nodes, "edges": edges, "rows": rows, "alts": alts}


def path_inbound(device, enabled, live=None, wanted=None):
    """The ONE inbound of a device's path: ``wanted`` (when the device has it, enabled), else the one it is online
    through now (``live``: its row of live.json), else the first enabled one in the order they were given; None —
    nothing to connect with."""
    acc = [k for k in (device or {}).get("access") or [] if isinstance(k, str) and k in enabled]
    if wanted and wanted in acc:
        return wanted
    lv = live if isinstance(live, dict) else {}
    if lv.get("online") and lv.get("inbound") in acc:
        return lv["inbound"]
    return acc[0] if acc else None


# ── texts ──────────────────────────────────────────────────────────────────

_PROFILES = ("ru_direct", "all_exit")
_FROMS = ("device", "user", "default")
_GROUPS = ("block", "force", "direct")


def _profile_title(p):
    return tr("gw_trace.profile." + p) if p in _PROFILES else p


def _from_text(f):
    return tr("gw_trace.from." + f) if f in _FROMS else f


def __getattr__(name):
    """The texts that were module constants before 3.3.2 — now in the language of the reader."""
    if name == "KERNEL_HINT":
        return tr("gw_trace.kernel_hint")
    if name == "VLESS_PING_NOTE":
        return tr("gw_trace.note.vless_ping")
    if name == "PROFILE_TITLES":
        return dict((p, _profile_title(p)) for p in _PROFILES)
    if name == "FROM_TEXT":
        return dict((f, _from_text(f)) for f in _FROMS)
    if name == "GROUP_TEXT":
        return dict((g, tr("gw_trace.group." + g)) for g in _GROUPS)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _exit_name(st, eid, pool):
    e = next((x for x in pool if x.get("id") == eid), None)
    if e is not None:
        return exits.label(st, e)
    return model.exit_pref(st, eid)["name"] or eid


def _via_text(st, via, pool):
    if via in ("pool", "direct", "block"):
        return tr("gw_trace.via." + via)
    gid = model.group_of_route({"via": via})
    if gid:                                            # stage 1: «group:<gid>» — its shown name (§3.1)
        from . import policy as P
        return tr("gw_trace.via.group", name=_group_label({"policy": P.load(st)}, gid))
    return tr("gw_trace.via.exit", name=_exit_name(st, model.exit_of({"via": via}), pool))


def _exit_row(st, eid, ctx):
    e = next((x for x in ctx["pool"] if x.get("id") == eid), None)
    if e is None:
        return {"id": eid, "name": _exit_name(st, eid, ctx["pool"]), "state": "missing",
                "text": tr("gw_trace.exit_missing")}
    row = exits.public(e, st, ctx["snap"])
    row["usable"] = bool(exits.parsed(e) and row["enabled"] and eid in ctx["alive"])
    text = exits.state_text(row)
    return {"id": eid, "name": row["name"], "state": row.get("state"), "text": text, "kind": row.get("kind"),
            "proto": row.get("proto") or ""}


def _server(st):
    return model.public_host(st)


def _cats_text(cats):
    return ", ".join(cats)


# ── the decision ───────────────────────────────────────────────────────────

def trace(st, who_ref, target, ctx=None, resolver=None, inbound=None):
    """The path of a connection of ``who_ref`` to ``target`` through ``inbound`` (see the module doc; None — the
    first enabled inbound of the device). Raises KaskadError (USAGE) for a bad target, an unknown device / person,
    an inbound the device has not or that is off."""
    q = parse_target(target) if isinstance(target, str) else target
    who = resolve_who(st, who_ref)
    ctx = context(st) if ctx is None else ctx
    resolver = resolver or resolve
    enabled = set(model.enabled_kinds(st))
    dev = who["device"]
    user = who["user"]
    did = dev.get("id")
    uid = (user or {}).get("id")
    pool = ctx["pool"]
    steps, notes = [], []
    hl = {"nodes": [], "edges": [], "devices": [], "category": None, "via": None, "rows": {}}

    def node(nid):
        if nid and nid not in hl["nodes"]:
            hl["nodes"].append(nid)

    def edge(eid):
        if eid and eid not in hl["edges"]:
            hl["edges"].append(eid)

    def step(text, note="", tone=""):
        steps.append({"text": text, "note": note, "tone": tone})

    # кто: устройство, его входы (путь идёт через ОДИН из них), профиль
    access = [k for k in dev.get("access") or [] if isinstance(k, str)]
    on = [k for k in access if k in enabled]
    if inbound not in (None, ""):
        inbound = str(inbound).strip()
        if who["kind"] != "device":
            raise KaskadError(tr("gw_trace.err.inbound_new"), code=USAGE)
        if inbound not in access:
            raise KaskadError(tr("gw_trace.err.inbound_missing", device=dev.get("name") or did,
                                 inbound=_kind_title(inbound),
                                 have=", ".join(_kind_title(k) for k in access) or tr("gw_trace.none")),
                              code=USAGE)
        if inbound not in on:
            raise KaskadError(tr("gw_trace.err.inbound_off", inbound=_kind_title(inbound),
                                 device=dev.get("name") or did), code=USAGE)
        chosen = inbound
    else:
        chosen = on[0] if on else None
    socks = chosen in XRAY_IN
    xname = _kind_title(chosen) if socks else ""
    kernel = [chosen] if chosen and not socks else []
    active = True if who["kind"] != "device" else model.device_active(st, dev)
    from ..rules import apply as rapply
    prof = rapply.profile(st, dev, ctx["ready"])   # рубеж 2 (R2): without the lists or the consent ⚠РФ — all_exit
    applied = prof["applied"]
    route = model.route_of(st, dev)
    if did:
        hl["devices"].append(did)
    hl.update(inbound=chosen, profile=prof["profile"], user=uid, device=did)
    ptitle = _profile_title(prof["profile"])
    if who["kind"] == "device":
        who_text = tr("gw_trace.who.device", name=dev.get("name") or did,
                      user=(" (%s)" % user.get("name")) if user else "")
    elif who["kind"] == "user":
        who_text = tr("gw_trace.who.user", name=user.get("name") or uid)
    else:
        who_text = tr("gw_trace.who.new")
    who_note = []
    if who["kind"] == "device":
        if chosen:
            others = [k for k in on if k != chosen]
            if others:
                who_note.append(tr("gw_trace.who.via_others" if len(others) > 1 else "gw_trace.who.via_other",
                                   inbound=_kind_title(chosen), others=", ".join(_kind_title(k) for k in others)))
            else:
                who_note.append(tr("gw_trace.who.via", inbound=_kind_title(chosen)))
        elif access:
            who_note.append(tr("gw_trace.who.inbounds_off"))
        else:
            who_note.append(tr("gw_trace.who.no_inbounds"))
    else:
        who_note.append(tr("gw_trace.who.any_inbound", hint=tr("gw_trace.kernel_hint")))
    if prof["why"]:
        who_note.append(tr("gw_trace.who.no_consent" if prof["why"] == "no_consent" else "gw_trace.who.no_lists"))
    step(tr("gw_trace.step.who", who=who_text, profile=ptitle, source=_from_text(prof["from"])), "; ".join(who_note),
         "warn" if (who["kind"] == "device" and not on) else "")
    if not model.enabled(st):
        notes.append(tr("gw_trace.note.gw_off"))
    # SOCKS5 и VLESS решает xray шлюза (те же списки из файлов, плюс keyword и regexp); входы на ядре — dnsmasq и таблица
    socks_only = xname if socks and who["kind"] == "device" else False
    other_x = [k for k in on if k in XRAY_IN]
    if socks_only:
        notes.append(tr("gw_trace.note.xray_decides", inbound=xname))
    elif other_x and who["kind"] == "device":
        notes.append(tr("gw_trace.note.xray_would", inbound=_kind_title(other_x[0])))
    domains_ok = bool((ctx["socks_files"] or {}).get("geosite")) if socks_only else ctx["nftset"] is not False

    out = {"ok": True, "query": q, "who": {"kind": who["kind"], "id": did, "name": dev.get("name") if did else None,
                                           "user": ({"id": uid, "name": user.get("name")} if user else None),
                                           "active": active, "inbounds": on, "inbound": chosen, "socks": socks,
                                           "kernel": bool(kernel) or who["kind"] != "device"},
           "profile": dict(prof, applied=applied), "route": route, "match": None, "addresses": [],
           "resolve": None, "exit": None, "exits": [], "server": _server(st), "rf": None, "ru": None}

    def finish(verdict, reason, summary, category, via_now=None, exit_id=None, balancer=False, exits_=()):
        out.update(verdict=verdict, reason=reason, summary=summary, steps=steps, notes=notes)
        # с какого адреса сайт увидит этот трафик — сразу в итоге (T-scheme-3.2.16, «непонятно новичку»)
        if verdict == "direct":
            # адрес сервера, а не страна: узел может стоять где угодно (стенд T-M6, F14 — «Россия» про Париж)
            srv = _server(st)
            out["origin"] = tr("gw_trace.origin.direct", server=(" (%s)" % srv) if srv else "")
        elif verdict == "block":
            out["origin"] = tr("gw_trace.origin.block")
        out.setdefault("origin", "")
        target_now = FILTER if category in ("ads", "torrent") else (via_now or route["via"])
        c = chain(user=uid, device=did, inbound=chosen, profile=prof["profile"], category=category,
                  via=route["via"], target=target_now, exit_id=exit_id, balancer=balancer, exits=exits_)
        hl.update(category=category, via=route["via"], target=target_now, exit=exit_id, balancer=balancer or False,
                  exits=list(exits_), rows=c["rows"], alts=c["alts"])
        for x in c["nodes"]:
            node(x)
        for x in c["edges"]:
            edge(x)
        if balancer:
            for eid in exits_:
                node("exit:" + eid)                     # the candidates of the balancer: lit, no line to each
        out["highlight"] = hl
        return out

    def ru_way(ru):
        """T-q Н-trace, Q7 (SPEC-groups §9): where «Россия напрямую» of this device really goes — ``exits.ru_target``
        (the kernel and xray take the same): its Russian exit, the last step «блок» of its Russian group -> the result;
        the last step «напрямую» -> its step, None (the node, as ``ru_via`` direct) — the caller ends at the node.
        ``out["ru"]`` — {group, name, step, exit, tier, since, offline, chain} (``_ru_chain``) for the panel, the bot,
        the SSH menu, the CLI and MCP."""
        rt = exits.ru_target(st, dev, ctx.get("decision")) if dev is not None else None
        if not rt:
            return None
        info = _ru_info(st, dev, rt, ctx)
        out["ru"] = info
        if rt.get("step") == "direct":
            return None
        grp = info["name"]
        live_ru = ctx.get("live") or {}
        off = live_ru.get("ru_offline") if isinstance(live_ru, dict) else None
        if info["offline"]:
            when = _clock((off or {}).get("since"))
            step(tr("gw_trace.step.ru_offline_since", since=when) if when else tr("gw_trace.step.ru_offline"),
                 tr("gw_trace.step.ru_offline_note"), "warn")
        if rt["step"] == "exit":
            r = _exit_row(st, rt["exit"], ctx)
            step(tr("gw_trace.step.ru_exit", group=grp, name=r["name"], state=r["text"]), _ru_note(info), "ok")
            out["exits"], out["exit"] = [r], r
            res = finish("exit", ru["reason"], tr("gw_trace.sum.ru_exit", name=r["name"]), "ru", "exit:" + rt["exit"])
            res["origin"] = tr("gw_trace.origin.exit_named", name=r["name"])
            return res
        if rt["step"] == "last_block":
            step(tr("gw_trace.step.ru_last_block", group=grp), _ru_note(info), "bad")
            return finish("block", "ru_last_block", tr("gw_trace.sum.ru_last_block", group=grp), "ru", "block")
        step(tr("gw_trace.step.ru_last_direct", group=grp), _ru_note(info), "warn")
        return None

    # выключено — трафика нет вовсе
    if not active:
        step(tr("gw_trace.step.off"), tr("gw_trace.step.off_note"), "bad")
        return finish("block", "off", tr("gw_trace.sum.off"), "off", "block")
    if who["kind"] == "device" and not chosen:
        step(tr("gw_trace.step.no_inbound"), tr("gw_trace.step.no_inbound_note"), "bad")
        return finish("block", "no_inbound", tr("gw_trace.sum.no_inbound"), "off", "block")

    # куда маршрут ведёт сейчас — до доменов: заблокированному устройству «напрямую» не обещаем (T-N5 3.2.19, №12:
    # «ya.ru — список «напрямую» … напрямую с адреса сервера» рядом с «Заблокировано: в пуле нет живых выходов»)
    t = _route_now(st, dev, route, ctx, socks_only)
    # домен: в каком он списке (самый длинный суффикс, как у dnsmasq)
    match = None
    if q["kind"] == "domain":
        if ctx["groups"] is None:
            step(tr("gw_trace.step.no_lists", name=q["value"]), tr("gw_trace.step.no_lists_note"), "warn")
        else:
            match = match_domain(q["value"], ctx["groups"], ctx["cats"])
            out["match"] = match
            if match is None:
                step(tr("gw_trace.step.not_listed", name=q["value"]), tr("gw_trace.step.not_listed_note"))
            else:
                note = _match_note(match, applied, domains_ok)
                if match["group"] == "force" and t.get("group_label") and route["via"] not in ("direct", "block"):
                    note = tr("gw_trace.match.note_force_group", group=t["group_label"])
                if match["group"] != "block" and applied == "ru_direct" and t["mark"] == "block" \
                        and not (match["group"] == "direct" and _ru_fail_open(route, t)):
                    note = tr("gw_trace.match.blocked_now", why=t["why"])
                step(_match_text(q["value"], match), note)
        # ⚠РФ (§5.1; решение координатора 18.10): пометка — по реестру организаций (rfmark.org_of, WhatsApp — exclude), а не
        # по попаданию в список: это факт об организации — видна и до скачивания списков, и для домена вне встроенных групп;
        # путь не меняет. Выключенное устройство и устройство без входа выходят раньше (трафика нет) — у них пометки нет.
        mark = _rf_mark(q["value"])
        if mark:
            out["rf"] = mark
            step(tr("gw_trace.step.rf", name=mark["name"]), tr("gw_trace.step.rf_note"), "warn")

    # блок — для всех, какой бы ни был профиль (dnsmasq один на всех)
    if match and match["group"] == "block":
        torrent = any(c == "torrent" for c in match["categories"])
        why = tr("gw_trace.cat.torrent" if torrent else "gw_trace.cat.ads")
        if kernel or who["kind"] != "device":
            notes.append(tr("gw_trace.note.block_doh"))
        if socks and not (ctx["socks_files"] or {}).get("geosite"):
            notes.append(tr("gw_trace.note.xray_no_lists", inbound=xname))
        return finish("block", "torrent" if torrent else "ads", tr("gw_trace.sum.blocked", why=why),
                      "torrent" if torrent else "ads", None)

    # маршрут устройства и куда он ведёт сейчас (t — выше)
    rtext = tr("gw_trace.step.route", via=_via_text(st, route["via"], pool), source=_from_text(route["from"]))
    rnote = "" if route["via"] in ("direct", "block") else \
        tr("gw_trace.step.fail_direct" if exits.fail_of(st, route, ctx.get("decision")) == "direct"
           else "gw_trace.step.fail_block")              # the route's own policy or its group's (REV-s1-b2 З1)

    if t["mark"] == "block":
        # «Россия напрямую» при запасном блоке (§1.3 отличие 5, §2.3; REV-s1-t4 З2): the exits of the group unfit and
        # its last step a block — Russian sites go directly, as in nft (gw_ruonly) and xray (the fallback block of T5)
        if applied == "ru_direct" and _ru_fail_open(route, t):
            ru = _russia(q, match, ctx, socks_only, domains_ok, resolver, out, notes)
            if ru["direct"]:
                node(route["via"])
                step(rtext, rnote)
                step(tr("gw_trace.step.now_block", why=_cap(t["why"])), tr("gw_trace.step.now_block_ru_note"), "warn")
                _exit_dead(t, node)
                if ru["step"]:
                    step(ru["step"], ru.get("note", ""))
                got = ru_way(ru)
                if got is not None:
                    return got
                node("direct")
                return finish("direct", ru["reason"], _direct_summary(out["server"]), "ru", "direct")
            if ru["step"]:
                step(ru["step"], ru.get("note", ""), ru.get("tone", ""))
        node(route["via"])
        step(rtext, rnote)
        if route["via"] == "block":
            step(tr("gw_trace.step.route_block"), tr("gw_trace.step.route_block_note"), "bad")
            return finish("block", "route_block", tr("gw_trace.sum.route_block"), "rest", "block")
        step(tr("gw_trace.step.now_block", why=_cap(t["why"])), tr("gw_trace.step.now_block_note"), "bad")
        _exit_dead(t, node)
        return finish("block", "fail_block", tr("gw_trace.sum.blocked", why=t["why"]), "rest", "block")

    # «Россия напрямую»
    if applied == "ru_direct" and t["mark"] != "direct":
        ru = _russia(q, match, ctx, socks_only, domains_ok, resolver, out, notes)
        if ru["direct"]:
            if ru["step"]:
                step(ru["step"], ru.get("note", ""))
            got = ru_way(ru)
            if got is not None:
                return got
            node("direct")
            return finish("direct", ru["reason"], _direct_summary(out["server"]), "ru", "direct")
        if ru["step"]:
            step(ru["step"], ru.get("note", ""), ru.get("tone", ""))
    step(rtext, rnote)
    if t["mark"] == "direct":
        node(route["via"])
        if route["via"] == "direct":
            node("direct")
            return finish("direct", "route_direct", _direct_summary(out["server"]), "rest", "direct")
        step(tr("gw_trace.step.now_direct", why=_cap(t["why"])), tr("gw_trace.step.now_direct_note"), "warn")
        _exit_dead(t, node)
        return finish("direct", "fail_direct", _direct_summary(out["server"]), "rest", "direct")

    # через выход
    rows = [_exit_row(st, eid, ctx) for eid in t["exits"]]
    out["exits"] = rows
    out["exit"] = rows[0] if len(rows) == 1 else None
    ids = list(t.get("exits") or []) or list(t.get("pool_live") or [])
    if route["via"] != "pool":                          # a route to one exit: that exit is the target itself
        one, bal, cands = None, False, ()
    elif t["balancer"]:                                 # «xray»: the xray exits; «any»: SOCKS5 — every live exit
        one, bal, cands = None, t["balancer"], ids
    elif len(ids) == 1:
        one, bal, cands = ids[0], False, ()
    else:
        one, bal, cands = None, False, ()
        for eid in ids:
            node("exit:" + eid)                         # a new device: one of these, unknown which
    names = ", ".join(tr("gw_trace.quoted", name=r["name"]) for r in rows)
    if t["balancer"] == "any":
        step(tr("gw_trace.step.any", names=names), t.get("note", "") or tr("gw_trace.step.fastest"), "ok")
        summary = tr("gw_trace.sum.any")
        out["origin"] = (tr("gw_trace.origin.one_of_exits", names=names) if len(rows) > 1
                         else tr("gw_trace.origin.exit", names=names))
    elif t["balancer"]:
        step(tr("gw_trace.step.vless", names=names), tr("gw_trace.step.fastest"), "ok")
        summary = tr("gw_trace.sum.vless")
        out["origin"] = (tr("gw_trace.origin.one_of_them", names=names) if len(rows) > 1
                         else tr("gw_trace.origin.exit", names=names))
    elif t["exits"]:
        r0 = rows[0]
        step(tr("gw_trace.step.exit_now", name=r0["name"], state=r0["text"]), t.get("note", ""), "ok")
        summary = tr("gw_trace.sum.exit", name=r0["name"])
        out["origin"] = tr("gw_trace.origin.exit_named", name=r0["name"])
    else:
        step(tr("gw_trace.step.pool_pick"), t.get("note", ""), "ok")
        summary = tr("gw_trace.sum.pool")
        out["origin"] = tr("gw_trace.origin.pool")
    if not socks and (t["balancer"] in (exits.XRAY, exits.OLD_XRAY) or (rows and all(exits.is_xray(r) for r in rows))):
        notes.append(tr("gw_trace.note.vless_ping"))   # a note, not a warning: the path works
        out["icmp"] = False
    return finish("exit", "route", summary, "rest", None, exit_id=one, balancer=bal, exits_=cands)


def _rf_mark(name):
    """⚠РФ of a domain (R5): {"org", "name"} when the registry marks it (``rfmark.org_of``: a domain of an
    organisation — never under its ``exclude``, WhatsApp), else None. A broken registry marks nothing here: the status
    and the rules page say it (``rules.rf.bad``); the path of the traffic is told all the same."""
    try:
        from ..rules import rfmark
        reg = rfmark.default()
        org = reg.org_of(name)
        return {"org": org, "name": reg.names([org])} if org else None
    except (KaskadError, OSError, ValueError):
        return None


def _kind_title(kind):
    from . import registry
    return registry.title(kind)


def _match_text(name, m):
    cats = _cats_text(m["categories"])
    where = name if m["suffix"] == name else tr("gw_trace.match.as", name=name, suffix=m["suffix"])
    catn = tr("gw_trace.match.category", cats=cats) if cats else ""
    if m["group"] == "block":
        return tr("gw_trace.match.block", where=where,
                  what=tr("gw_trace.cat.torrent" if "torrent" in m["categories"] else "gw_trace.cat.ads"), cat=catn)
    title = tr("gw_trace.match.list_force" if m["group"] == "force" else "gw_trace.match.list_direct")
    from ..rules import lists
    if not cats and m["group"] == "force" and m["suffix"] in lists.FORCE_EXTRA:
        return tr("gw_trace.match.list_ipcheck", where=where, title=title)
    return tr("gw_trace.match.list", where=where, title=title, cat=catn)


def _match_note(m, applied, domains_ok=True):
    if m["group"] == "block":
        return tr("gw_trace.match.note_block")
    if applied != "ru_direct":
        return tr("gw_trace.match.note_profile", profile=_profile_title(applied))
    if not domains_ok:
        return tr("gw_trace.match.note_no_domains")
    if m["group"] == "direct":
        return tr("gw_trace.match.note_direct")
    return tr("gw_trace.match.note_force")


def _cap(s):
    return s[:1].upper() + s[1:]


def _direct_summary(server):
    return tr("gw_trace.sum.direct", server=server) if server else tr("gw_trace.sum.direct_this")


def _russia(q, match, ctx, socks_only, domains_ok, resolver, out, notes):
    """«Россия напрямую» for a device whose profile applies it and whose mark is not block: {"direct", "reason",
    "step", "note", "tone"}. ``socks_only`` — the title of the inbound when xray decides (its files instead of dnsmasq
    and the table: SOCKS5, VLESS), else False."""
    files = ctx["socks_files"] or {}
    if match and match["group"] in ("force", "direct") and not domains_ok:
        notes.append(tr("gw_trace.note.no_nftset") if not socks_only else
                     tr("gw_trace.note.xray_no_domains", inbound=socks_only))
    if match and match["group"] == "force" and domains_ok:
        return {"direct": False, "step": "", "reason": "force"}
    if match and match["group"] == "direct" and domains_ok:
        return {"direct": True, "reason": "ru_domain", "step": ""}
    # адреса: введённый или адреса домена (DNS узла)
    if q["kind"] == "ip":
        ips = [q["value"]]
    else:
        ips, err = resolver(q["value"])
        out["resolve"] = {"ok": bool(ips), "error": err}
        if not ips:
            return {"direct": False, "reason": "no_address", "tone": "warn",
                    "step": tr("gw_trace.ru.no_address", name=q["value"], error=err or tr("gw_trace.dns.no_answer")),
                    "note": tr("gw_trace.ru.no_address_note")}
    out["addresses"] = list(ips)
    ranges = _ranges(ctx["v"])
    ru = [ip for ip in ips if in_ranges(ip, ranges)]
    out["ru_addresses"] = ru
    shown = ", ".join(ips[:3]) + (tr("gw_trace.ru.and_more", n=len(ips) - 3) if len(ips) > 3 else "")
    head = shown if q["kind"] == "ip" else "%s: %s" % (q["value"], shown)
    if not ru:
        return {"direct": False, "reason": "not_ru", "step": tr("gw_trace.ru.not_ru", head=head),
                "note": tr("gw_trace.ru.by_route")}
    loaded = (ctx["table"] or {}).get("loaded")
    if socks_only:
        loaded = bool(files.get("geoip"))
    part = len(ru) < len(ips)
    if not loaded:
        if socks_only:
            why = tr("gw_trace.ru.xray_no_geoip")
        elif loaded is None:
            why = tr("gw_trace.ru.load_unknown")
        else:
            why = tr("gw_trace.ru.not_loaded")
        if loaded is None and not socks_only:
            notes.append(tr("gw_trace.note.ru_unknown"))
            return {"direct": True, "reason": "ru_ip", "step": tr("gw_trace.ru.in_ru", head=head), "note": why}
        return {"direct": False, "reason": "ru_not_loaded", "tone": "warn",
                "step": tr("gw_trace.ru.in_ru", head=head), "note": why}
    if part:
        notes.append(tr("gw_trace.note.partly_ru", ru=len(ru), total=len(ips)))
        if ips[0] not in ru:
            return {"direct": False, "reason": "partly_ru", "step": tr("gw_trace.ru.first_not_ru", head=head),
                    "note": tr("gw_trace.ru.in_ru_list", ips=", ".join(ru[:3])), "tone": "warn"}
    return {"direct": True, "reason": "ru_ip", "step": tr("gw_trace.ru.in_ru", head=head),
            "note": tr("gw_trace.match.note_direct")}


def _ru_fail_open(route, t):
    """The block of ``t`` is the last step of the group (its exits unfit), not a block by choice: «Россия напрямую»
    still goes directly (§1.3 отличие 5, §2.3: ``net.ruonly_of``)."""
    return route["via"] not in ("direct", "block") and t.get("mark") == "block" and t.get("step") == "last_block"


def _route_now(st, dev, route, ctx, socks_only):
    """{"mark": "exit"|"direct"|"block", "exits": [ids], "balancer": False|"xray"|"any", "why", "note"} — where the
    route of ``dev`` leads now: ``exits.now_of`` (one answer with the scheme and its counters, Н-1) with the texts."""
    pool = ctx["pool"]
    dec = ctx.get("decision")
    if dec is None:                         # a context made by hand: its alive exits (the form before stage 1)
        dec = exits.decision(st, pool_exits=pool, alive=ctx["alive"]) if ctx.get("alive") is not None \
            else exits.decision(st, ctx.get("snap"), pool)
    n = exits.now_of(st, dev, pool, socks=bool(socks_only), decision=dec)
    via = route["via"]
    glabel = _group_label(dec, n.get("group"))
    if via == "block":
        return {"mark": "block", "exits": [], "balancer": False, "why": tr("gw_trace.why.route_block")}
    if via == "direct":
        return {"mark": "direct", "exits": [], "balancer": False, "why": tr("gw_trace.why.route_direct")}
    if n["dead"] and n["target"] in ("direct", "block"):
        return {"mark": n["target"], "exits": list(n["dead"]), "dead": True, "balancer": False,
                "why": tr("gw_trace.why.exit_dead", name=_exit_name(st, n["dead"][0], pool)), "group_label": glabel,
                "step": n.get("step")}
    if n["target"] in ("direct", "block"):
        why = tr("gw_trace.why.pool_dead") if n.get("group") in (None, "main") else \
            tr("gw_trace.why.group_dead", group=glabel)
        return {"mark": n["target"], "exits": [], "balancer": False, "why": why, "group_label": glabel,
                "step": n.get("step")}
    if n["balancer"] == "any":
        return {"mark": "exit", "exits": list(n["exits"]), "balancer": "any", "why": "",
                "note": tr("gw_trace.why.xray_any", inbound=socks_only or "SOCKS5")}
    if n["balancer"] in (exits.XRAY, exits.OLD_XRAY):
        return {"mark": "exit", "exits": list(n["exits"]), "balancer": exits.XRAY, "why": ""}
    if n["pool_live"] and not n["exit"]:
        return {"mark": "exit", "exits": [], "pool_live": list(n["pool_live"]), "balancer": False, "why": "",
                "note": tr("gw_trace.why.pool_live", n=len(n["pool_live"]))}
    note = ""
    if n.get("missing"):                    # «exit:X» with X gone from the pool (§2.2, З1; §7.3)
        note = tr("gw_trace.why.missing", group=glabel)
    elif n["dead"]:                         # «exit:E» with E unfit: another exit of its group (§2.2)
        note = tr("gw_trace.why.evicted", name=_exit_name(st, n["dead"][0], pool), group=glabel)
    elif n.get("pinned"):
        note = tr("gw_trace.why.pinned", group=glabel)
    return {"mark": "exit", "exits": [n["exit"]], "balancer": False, "why": "", "note": note, "group_label": glabel}


def _group_label(dec, gid):
    """The shown name of a group (``policy.label``: «Основная»/«Main» of «main» without a name, §3.1)."""
    from . import policy as P
    grp = P.group((dec or {}).get("policy") or {}, gid or P.MAIN)
    return P.label(grp) if grp else P.label({"id": gid or P.MAIN})


def _clock(ts):
    """HH:MM of ``ts`` on the server's clock (``live._clock``), "" without a time."""
    from . import live
    return live._clock(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0 else ""


def _ru_info(st, dev, rt, ctx):
    """``trace()["ru"]`` (Q7, SPEC-groups §9) of the answer ``rt`` of ``exits.ru_target``: {"group", "name" (shown,
    ``policy.label``), "step" (direct | exit | last_direct | last_block), "exit", "tier" (the shown name of its tier),
    "since" (the device is on the last step since — live.json), "offline" (the node does not reach the Russian
    addresses: the group is frozen), "chain" — the exits of the group in the order of its tiers and its last step:
    [{"kind": "exit", "id", "name", "tier", "state", "now"}, …, {"kind": "last", "step": "direct" | "block",
    "now"}]}. ``ru_via`` direct — group None and an empty chain."""
    from . import policy as P
    live_ru = ctx.get("live") if isinstance(ctx.get("live"), dict) else {}
    info = {"group": rt.get("group"), "name": None, "step": rt.get("step"), "exit": rt.get("exit"), "tier": None,
            "since": None, "offline": bool(live_ru.get("ru_offline")) and rt.get("group") is not None, "chain": []}
    gid = rt.get("group")
    if not gid:
        return info
    dec = ctx.get("decision") or {}
    grp = P.group(dec.get("policy") or P.load(st), gid) or {"id": gid}
    info["name"] = P.label(grp)
    states = dec.get("states") or {}
    chain = []
    for i, t in enumerate(grp.get("tiers") or []):
        if not isinstance(t, dict):
            continue
        for eid in t.get("exits") or []:
            chain.append({"kind": "exit", "id": eid, "name": _exit_name(st, eid, ctx["pool"]),
                          "tier": P.tier_label(grp, i), "state": states.get(eid, "unknown"),
                          "now": rt.get("step") == "exit" and eid == rt.get("exit")})
            if chain[-1]["now"]:
                info["tier"] = chain[-1]["tier"]
    last = grp.get("fail") if grp.get("fail") in ("direct", "block") else "block"
    chain.append({"kind": "last", "step": last, "now": rt.get("step") in ("last_direct", "last_block")})
    info["chain"] = chain
    ne = (live_ru.get("no_exit") or {}).get((dev or {}).get("id") or "")
    if info["step"] in ("last_direct", "last_block") and isinstance(ne, dict) and ne.get("group") == gid:
        s = ne.get("since")
        info["since"] = s if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 else None
    return info


def _ru_note(info):
    """The line under the Russian step (§9): «Уровень 1. Если недоступен: «Дача» → с сервера напрямую» — the exits
    after the current one that are not «снят» and the last step; on the last step — since when the exits of the
    group are down."""
    chain = info.get("chain") or []
    if info.get("step") == "exit":
        cur = next((i for i, c in enumerate(chain) if c.get("now")), None)
        rest = [c for c in chain[(cur + 1 if cur is not None else 0):] if c["kind"] == "last" or c.get("state") != "off"]
        words = [tr("gw_trace.quoted", name=c["name"]) if c["kind"] == "exit" else
                 tr("gw_trace.ru.last_" + c["step"]) for c in rest]
        parts = []
        if info.get("tier") and (cur is None or info["tier"] != chain[cur].get("name")):
            parts.append(tr("gw_trace.ru.tier", tier=info["tier"]))
        if words:
            parts.append(tr("gw_trace.ru.else", chain=" → ".join(words)))
        return " ".join(parts)
    when = _clock(info.get("since"))
    return tr("gw_trace.ru.down_since", group=info.get("name") or "", since=when) if when else ""


def _exit_dead(t, node):
    """The dead exit of a route that failed over: lit as a node (the reason), no line to it — the traffic does not
    go there."""
    for eid in t.get("exits") or []:
        node("exit:" + eid)


# ── text for the CLI, the menu and the bot ─────────────────────────────────

def lines(res):
    """The answer as lines for people (the CLI, menu 17 folds them to the window)."""
    out = [tr("gw_trace.lines.summary", summary=res["summary"])]
    if res.get("origin"):
        out.append("      %s" % res["origin"])
    for i, s in enumerate(res["steps"], 1):
        out.append("%d. %s" % (i, s["text"]))
        if s.get("note"):
            out.append("   %s" % s["note"])
    for n in res.get("notes") or []:
        out.append("! %s" % n)
    return out


def text_html(res, esc):
    """The answer for Telegram (HTML): ``esc`` escapes."""
    icon = {"block": "⛔", "direct": "🏠", "exit": "🌐"}.get(res["verdict"], "•")
    q = (res.get("query") or {}).get("value") or ""
    out = ["%s <code>%s</code> → <b>%s</b>" % (icon, esc(q), esc(res["summary"]))]
    if res.get("origin"):
        out.append("<i>%s</i>" % esc(res["origin"]))
    out.append("")
    for i, s in enumerate(res["steps"], 1):
        out.append("%d. %s" % (i, esc(s["text"])))
        if s.get("note"):
            out.append("    <i>%s</i>" % esc(s["note"]))
    for n in res.get("notes") or []:
        out.append("❕ %s" % esc(n))
    return "\n".join(out)
