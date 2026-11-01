"""What the gateway renders from the lists and the model (docs/k3/SPEC-wave3.md §2). Pure renderers plus the copy
of xray's asset files; the gateway modules call them:

    kaskad.gw.net    ``kernel()`` — the ranges of gw_ru (with the sha256 that versions them; ``net.load_ru`` loads
                     them) and ``rd()`` — gw_rd: the addresses of the clients whose profile is ru_direct (static ones
                     of the devices, dynamic ones of IKEv2 from the hooks); ``ensure_kernel()`` — WHEN gw_ru is
                     loaded: after an update of the lists and by kaskad-gw-rules-load.timer (at boot, then every
                     5 min), never in an apply nor by the watchdog; a failed load waits BACKOFF before the next try
    kaskad.gw.dns    ``dnsmasq_lines()`` — ``address=/d/`` of the block group (ads, Windows telemetry; torrents by
                     their switch) for everybody, and — only while some client of the kernel path is ru_direct and
                     dnsmasq has nftset — ``max-cache-ttl=600`` + ``nftset=/d/4#ip#kaskad#gw_force`` (always through
                     the exit) and ``nftset=/d/4#ip#kaskad#gw_ru_dns`` (directly), one line per domain
    kaskad.gw.xray   ``sync_assets()`` copies roscomvpn-geoip.dat / roscomvpn-geosite.dat into the asset directory of
                     the gateway xray (the directory of its binary, /opt/kaskad/gw, 0755: user kaskad-xray reads them),
                     ``xray_rules()`` — rules of the SOCKS5 and VLESS users: block -> always through the exit (to the user's
                     route) -> directly (``ext:roscomvpn-geoip.dat:direct`` and the domain categories) -> the route

Fail-closed: without lists (no store, or a store without the networks) nobody is ru_direct — gw_rd is empty, no
nftset line, no direct rule of xray: everything goes by the route of the device. A profile ru_direct then
behaves as all_exit (``profile()`` tells both: what is asked and what applies). The same without the consent ⚠РФ
(``rfgate.allowed``, the second line of defence, docs/k3/SPEC-stage1-rf-mark-builtin.md §3.5, R2): a ru_direct that
reached state.json past the check of a write (a hand edit, an import in the mode ``need``, future code) applies as
all_exit. Every reader of «Russia directly» goes through ``profile()`` / ``ru_ok()`` (tests/test_rules_rf_guard.py).

Blocking is global: dnsmasq answers every client of the gateway the same way, so the switches «Реклама» and
«Торренты» block for every profile; xray blocks them for every SOCKS5 and VLESS user too (the same behaviour on every
inbound). Profiles decide only «Russia directly».
"""

import hashlib
import json
import os
import re
import shutil
import time

from .. import nft, paths, snapshot, util
from ..gw import model
from ..i18n import tr
from . import lists, rfgate, store

BACKOFF = (300, 900, 3600, 10800)    # s before the next load of the networks after 1, 2, 3, 4+ failures in a row
_now = time.time                     # tests patch this
SET_RU = "gw_ru"
SET_RU_DNS = "gw_ru_dns"
SET_FORCE = "gw_force"
SET_RD = "gw_rd"
SET_RU_VER = "gw_ru_ver"
MAX_CACHE_TTL = 600                  # s: dnsmasq adds to the sets only on answers of the upstream, never from its cache
GEOIP_ASSET = "roscomvpn-geoip.dat"
GEOSITE_ASSET = "roscomvpn-geosite.dat"
MANIFEST = "roscomvpn.json"
SOCKS_KINDS = model.XRAY_KINDS       # inbounds without addresses (SOCKS5, VLESS, Hysteria2): their clients never reach the kernel's sets
# their inbounds in the gateway xray: the users are the device logins (Hysteria2: ``<login>.h2`` — ``xray_user``)
XRAY_PROTOCOLS = ("socks", "vless", "hysteria")
_USER = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")

_cache = {}                          # (path, signature) -> parsed ranges / domains (long-running processes)


# ── the lists and the profiles ─────────────────────────────────────────────

def version():
    return store.active()


def kernel_meta(v=None):
    """{"ready": bool, "count": ranges, "sha256": of ru4.txt or None} — without reading the ranges."""
    v = store.active() if v is None else v
    k = (v.meta.get("kernel") or {}) if v is not None else {}
    sha = v.sha256(store.RANGES) if v is not None else None
    count = k.get("ranges") if isinstance(k.get("ranges"), int) else 0
    return {"ready": bool(sha and count > 0), "count": count if sha else 0, "sha256": sha}


def ready(v=None):
    """The lists can make a client ru_direct: the networks of Russia are stored."""
    return kernel_meta(v)["ready"]


def _cached(path, parse):
    sig = snapshot.signature(path)
    if sig is None:
        return None
    key = (path, sig)
    hit = _cache.get(key)
    if hit is None:
        with open(path, "rb") as f:
            hit = parse(f.read())
        for k in [k for k in _cache if k[0] == path]:
            del _cache[k]
        _cache[key] = hit
    return hit


def _parse_ranges(data):
    out = []
    for line in data.decode("ascii", "replace").splitlines():
        s = line.strip()
        if not s:
            continue
        lists.parse_range(s)                        # nothing that is not an IPv4 range reaches nft text
        out.append(s)
    return out


def kernel(v=None):
    """{"ready", "count", "sha256", "ranges": [``a.b.c.d/n`` | ``a.b.c.d-e.f.g.h``]} — the ranges of gw_ru (cached
    by the file's signature: the collector and the panel do not parse 15 thousand lines again)."""
    v = store.active() if v is None else v
    out = kernel_meta(v)
    out["ranges"] = []
    if out["ready"]:
        try:
            out["ranges"] = _cached(v.file(store.RANGES), _parse_ranges) or []
        except (OSError, ValueError):
            out = {"ready": False, "count": 0, "sha256": None, "ranges": []}
    return out


_ASK = object()                      # ``why`` of profile()/ru_ok() not given: ask the lists and the consent


def _why(st, is_ready=None, v=None):
    """Why «Россия напрямую» cannot apply on this node now: None (it can), ``no_lists`` or ``no_consent``."""
    ok = (ready() if v is None else ready(v)) if is_ready is None else is_ready
    if not ok:
        return "no_lists"
    return None if rfgate.allowed(st) else "no_consent"


def profile(st, device, is_ready=None, why=_ASK):
    """{"profile", "from", "applied", "why"}: what the device asks for (``model.profile_of``) and what applies —
    ru_direct only when the lists are there AND «Россия напрямую» is allowed (``rfgate.allowed``: the consent, a mark
    of an updated node; SPEC-stage1-rf-mark-builtin §3.5, рубеж 2); else all_exit with ``why`` = ``no_lists`` |
    ``no_consent`` (None when it applies as asked). ``is_ready`` — the lists are there (None: ``ready()``); ``why`` —
    the answer of ``_why`` when the caller has it already (one per render, not one per device)."""
    p = model.profile_of(st, device)
    w = None
    if p["profile"] == "ru_direct":
        w = _why(st, is_ready) if why is _ASK else why
    p["applied"] = "all_exit" if w else p["profile"]
    p["why"] = w
    return p


def ru_ok(st, v=None, device=None, is_ready=None, why=_ASK):
    """«Россия напрямую» applies: without ``device`` — on this node at all (the lists ``v`` are there and it is
    allowed); with ``device`` — for that device (its profile is ru_direct and it applies; whether the device is active
    or its route blocks is the caller's). The only reader of a profile besides ``profile()`` (тест-страж)."""
    w = _why(st, is_ready, v) if why is _ASK else why
    if device is None:
        return w is None
    return profile(st, device, why=w)["applied"] == "ru_direct"


def _ip_key(ip):
    try:
        return tuple(int(x) for x in str(ip).split("."))
    except ValueError:
        return (999,)


def rd(st, leases=(), is_ready=None):
    """Addresses of gw_rd: the clients whose applied profile is ru_direct — static addresses of active devices on
    enabled inbounds (``model.live_ips``) and dynamic leases [(ip, device)] (IKEv2). [] without lists. Never a device
    blocked by choice — its route «block», a pause or a revoke (not active): gw_pre no longer checks ``meta mark !=
    0x2ff`` (SPEC-stage1-exits §2.3, Б1) — the fallback block of a group keeps Russia directly, a chosen one does not.
    [] without the consent ⚠РФ too (``ru_ok``, R2): gw_rd and gw_ruonly (``net.ruonly_of`` — a subset of this) are
    empty, the fallback block of a group then closes Russia as well. Only ``ru_via`` «direct» (SPEC-groups §3, Q2): a
    device whose Russia goes through its Russian group is in gw_rx (``rx``), never in both."""
    why = _why(st, is_ready)
    if why:
        return []
    kinds = model.enabled_kinds(st)
    ids = set()
    out = set()
    for d in model.devices(st):
        if not model.device_active(st, d) or not ru_ok(st, device=d, why=why) \
                or model.route_of(st, d)["via"] == "block" \
                or model.ru_via_of(st, d)["via"] != model.RU_VIA_DIRECT:
            continue
        ids.add(d.get("id"))
        out.update(model.live_ips(st, d, kinds))
    for ip, d in leases or ():
        if d.get("id") in ids:
            out.add(ip)
    return sorted(out, key=_ip_key)


def rx(st, decision=None, leases=(), is_ready=None):
    """The map gw_rx (SPEC-groups §3, Q2): {client ip: the mark of ``exits.ru_target``} of the devices whose Russia goes
    through their Russian group — its pinned exit (256+N, 0x300+N), its last step (0x201 «напрямую», 0x2ff block).
    Static addresses of active devices on enabled inbounds and dynamic leases [(ip, device)]. {} without the lists or
    the consent ⚠РФ (as ``rd``); ``ru_via`` «direct» is gw_rd's (``rd``), never here."""
    from ..gw import exits
    why = _why(st, is_ready)
    if why:
        return {}
    kinds = model.enabled_kinds(st)
    marks = {}
    out = {}
    for d in model.devices(st):
        if model.ru_via_of(st, d)["via"] == model.RU_VIA_DIRECT:
            continue
        t = exits.ru_target(st, d, decision, is_ready=is_ready)
        if t is None or t["step"] == "direct":
            continue
        marks[d.get("id")] = t["mark"]
        for ip in model.live_ips(st, d, kinds):
            out[ip] = t["mark"]
    for ip, d in leases or ():
        if d.get("id") in marks:
            out[ip] = marks[d["id"]]
    return out


def load_state_path():
    return os.path.join(paths.gw_run_dir(), "rules-load.json")


def load_state():
    """What the last load of the networks into the table did: {"ver", "ok", "at", "seconds", "elements", "fails",
    "next", "error"} ({} before the first; /run: a reboot forgets it — the first watchdog round loads again)."""
    doc = snapshot.read_json(load_state_path(), {}, copy=True)
    return doc if isinstance(doc, dict) else {}


def _save_load_state(doc):
    try:
        util.atomic_write(load_state_path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)
    except OSError:
        pass


def table_recreated(wanted):
    """The table lost the networks of Russia a load put there (it was recreated: ``nft delete table``, ``nft flush
    ruleset`` and the watchdog's repair): the state of the load says «not loaded» at once — readers that run no
    command (the panel) no longer show the lists of a table that is gone (stand T-G1, D3-3) — and the next
    ``ensure_kernel`` loads without a pause."""
    _save_load_state({"ver": wanted, "ok": False, "at": util.now_iso(), "seconds": 0, "elements": 0, "fails": 0,
                      "recreated": True})


def ensure_kernel(runner, st, force=False, log=None):
    """The networks of Russia in the table (gw_ru + gw_ru_ver) = the lists of the store. Called under the state
    lock by ``kaskadctl rules load`` (kaskad-gw-rules-load.timer: at boot after kaskad-net, then every 5 min) and
    after an update of the lists — never by an apply, a change of people or the watchdog. It reads gw_ru_ver alone
    (never the whole table); a load that fails (or does not fit RU_LOAD_TIMEOUT) leaves the table as it was (one
    transaction), is logged once and is not tried again before the pause BACKOFF[n] (``force`` ignores the pause);
    a new version of the lists is tried at once.

    Returns {"action": "ok" | "loaded" | "failed" | "waiting" | "no_table", "wanted", "loaded", ...}."""
    from ..errors import KaskadError
    from ..gw import net
    log = log or util.log_action
    wanted = net.ru_wanted()
    try:
        have = net.read_ru_version(runner)
    except KaskadError as e:
        return {"action": "no_table", "wanted": wanted, "loaded": None, "error": str(e)}
    out = {"wanted": wanted, "loaded": have}
    if have is None:                                    # the gateway part is not in the table: its repair first
        out["action"] = "no_table"
        return out
    if have == wanted:
        out["action"] = "ok"
        return out
    state = load_state()
    now = _now()
    same = state.get("ver") == wanted and not state.get("ok")
    if not force and same and now < float(state.get("next") or 0):
        out.update(action="waiting", next=state.get("next"), error=state.get("error"))
        return out
    plan = net.ru_plan() if wanted else {"ver": 0, "ranges": []}
    if plan["ver"] != wanted:
        res = {"ok": False, "ver": wanted, "elements": 0, "seconds": 0.0,
               "error": tr("rules.kernel.no_file")}
    else:
        res = net.load_ru(runner, plan)
    if res["ok"]:
        _save_load_state({"ver": wanted, "ok": True, "at": util.now_iso(), "seconds": res["seconds"],
                          "elements": res["elements"], "fails": 0})
        if wanted:
            log(tr("rules.log.loaded", n=res["elements"], sec=res["seconds"]))
        out.update(action="loaded", loaded=wanted, seconds=res["seconds"], elements=res["elements"])
        return out
    fails = int(state.get("fails") or 0) + 1 if same else 1
    pause = BACKOFF[min(fails, len(BACKOFF)) - 1]
    _save_load_state({"ver": wanted, "ok": False, "at": util.now_iso(), "seconds": res["seconds"],
                      "elements": res["elements"], "fails": fails, "next": now + pause, "error": res["error"]})
    if fails == 1:
        log(tr("rules.log.load_failed", error=res["error"], min=pause // 60))
    out.update(action="failed", error=res["error"], seconds=res["seconds"], next=now + pause, fails=fails)
    return out


def kernel_problem(st, live):
    """For ``gw check`` / ``gw status`` without repair: a text when the table holds other lists than the store
    (and the gateway part is there), else None. No command is run."""
    from ..gw import net
    wanted = net.ru_wanted()
    have = net.live_ru_version(live)
    if have is None or have == wanted:
        return None
    state = load_state()
    if not wanted:
        return tr("rules.kernel.stale")
    text = tr("rules.kernel.not_loaded")
    if state.get("ver") == wanted and not state.get("ok") and state.get("error"):
        text += tr("rules.kernel.next", error=state.get("error") or tr("rules.kernel.error"),
                   at=time.strftime("%H:%M", time.localtime(float(state.get("next") or 0))))
    return text


def device_rd(st, device, is_ready=None):
    """A device whose dynamic address goes into gw_rd (``gw hook``) — as ``rd()``: active, ru_direct applies (the
    lists and the consent ⚠РФ), never a route «block» by choice."""
    return bool(model.device_active(st, device) and ru_ok(st, device=device, is_ready=is_ready)
                and model.route_of(st, device)["via"] != "block")


def kernel_clients(st, is_ready=None):
    """Some active device on an enabled inbound of the kernel path (anything but SOCKS5 and VLESS) applies ru_direct: only
    then dnsmasq fills the sets (each answer costs it a call of libnftables)."""
    why = _why(st, is_ready)
    if why:
        return False
    kinds = [k for k in model.enabled_kinds(st) if k not in SOCKS_KINDS]
    for d in model.devices(st):
        if not model.device_active(st, d) or not any(k in kinds for k in d.get("access") or []):
            continue
        if ru_ok(st, device=d, why=why):
            return True
    return False


# ── domains (dnsmasq) ──────────────────────────────────────────────────────

def _parse_domains(data):
    doc = json.loads(data.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("domains.json: not an object")
    out = {}
    for code, rec in doc.items():
        if isinstance(rec, dict) and isinstance(rec.get("domains"), list):
            names = [n for n in (lists.norm_domain(x) for x in rec["domains"]) if n]
            out[str(code).lower()] = dict(rec, domains=names)
    return out


def categories(v=None):
    """{category: {"domains": [...], "keyword": n, ...}} of the stored geosite, None when there is none."""
    v = store.active() if v is None else v
    if v is None or not v.file(store.DOMAINS):
        return None
    try:
        return _cached(v.file(store.DOMAINS), _parse_domains)
    except (OSError, ValueError):
        return None


_groups_memo = {}


def groups(st, v=None):
    """The domain groups under the switches of ``st`` (``lists.groups``), None without a geosite. Kept per
    (lists, switches): the panel asks on every page."""
    v = store.active() if v is None else v
    cats = categories(v)
    if cats is None:
        return None
    conf = model.rules_conf(st)
    key = (v.path if v is not None else None, id(cats), conf["block_ads"], conf["block_torrent"])
    hit = _groups_memo.get(key)
    if hit is None:
        hit = lists.groups(cats, conf["block_ads"], conf["block_torrent"])
        _groups_memo.clear()
        _groups_memo[key] = hit
    return hit


def nftset_target(name):
    return "4#%s#%s#%s" % (nft.TABLE_FAMILY, nft.TABLE_NAME, name)


def dnsmasq_lines(st, nftset=True, v=None):
    """Lines for dnsmasq.conf (docs/k3/research/rules.md «dnsmasq nftset»): [] without lists."""
    v = store.active() if v is None else v
    g = groups(st, v)
    if not g:
        return []
    out = ["# Kaskad: правила RoscomVPN — блок %d, всегда через выход %d, напрямую %d (доменов)"  # i18n: skip — dnsmasq.conf
           % (len(g["block"]), len(g["force"]), len(g["direct"]))]
    out.extend("address=/%s/" % d for d in g["block"])
    if kernel_clients(st, ready(v)):
        if nftset:
            out.append("max-cache-ttl=%d" % MAX_CACHE_TTL)
            out.extend("nftset=/%s/%s" % (d, nftset_target(SET_FORCE)) for d in g["force"])
            out.extend("nftset=/%s/%s" % (d, nftset_target(SET_RU_DNS)) for d in g["direct"])
        else:
            out.append("# dnsmasq без nftset: домены «напрямую» и «всегда через выход» не учитываются, "  # i18n: skip — dnsmasq.conf
                       "напрямую идут только сети России")
    return out


# ── xray of the gateway (SOCKS5) ───────────────────────────────────────────

def asset_dir():
    """Where the gateway xray looks for ext: files — the directory of its binary (xray.run_binary)."""
    return os.path.join(paths.opt(), "gw")


def manifest():
    """What is in the asset directory: {"geoip"|"geosite": {"file", "sha256", "codes": [...]}} — only entries
    whose file is there."""
    doc = snapshot.read_json(os.path.join(asset_dir(), MANIFEST), {})
    out = {}
    if not isinstance(doc, dict):
        return out
    for key, name in (("geoip", GEOIP_ASSET), ("geosite", GEOSITE_ASSET)):
        rec = doc.get(key)
        if isinstance(rec, dict) and rec.get("file") == name and isinstance(rec.get("codes"), list) \
                and os.path.isfile(os.path.join(asset_dir(), name)):
            out[key] = {"file": name, "sha256": rec.get("sha256"), "codes": [str(c).upper() for c in rec["codes"]]}
    return out


def _user(device):
    u = str(device.get("login") or device.get("id") or "")
    return u if _USER.match(u) else None


def _xray_user(ad, device):
    """The xray user of ``device`` in the inbound of ``ad`` (``xray_user``: the login; Hysteria2 — ``<login>.h2``)."""
    fn = getattr(ad, "xray_user", None)
    if not callable(fn):
        return _user(device)
    try:
        u = str(fn(device) or "")
    except Exception:                                   # noqa: BLE001 — adapters are other work packages
        return None
    return u if _USER.match(u) else None


def socks_users(st, sec, adapters):
    """[(inbound tags, [(user, device)])] of the adapters that add user inbounds to xray (SOCKS5, VLESS, Hysteria2) —
    the user as xray knows it (``xray_user`` of the adapter)."""
    out = []
    for kind, ad in adapters:
        try:
            ibs = ad.xray_inbounds(st, sec) or []
        except Exception:                               # noqa: BLE001 — adapters are other work packages
            continue
        tags = sorted(set(ib.get("tag") for ib in ibs if isinstance(ib, dict) and ib.get("protocol") in XRAY_PROTOCOLS
                          and isinstance(ib.get("tag"), str)))
        if not tags:
            continue
        users = []
        for d in model.devices_for(st, kind):
            u = _xray_user(ad, d)
            if u:
                users.append((u, d))
        if users:
            out.append((tags, users))
    return out


def xray_wanted(st, sec, adapters, is_ready=None):
    """The asset files are needed: SOCKS5 or VLESS users exist and either a switch blocks or one of them is ru_direct."""
    why = _why(st, is_ready)
    conf = model.rules_conf(st)
    if conf["block_ads"] or conf["block_torrent"]:
        for _, ad in adapters:                      # VLESS: its block rule stands before its first user (xray_rules)
            try:
                if getattr(ad, "xray_hot", False) and ad.xray_inbounds(st, sec):
                    return True
            except Exception:                       # noqa: BLE001 — adapters are other work packages
                continue
    for _, users in socks_users(st, sec, adapters):
        if conf["block_ads"] or conf["block_torrent"]:
            return True
        if not why and any(ru_ok(st, device=d, why=why) for _, d in users):
            return True
    return False


def _ext(fname, code):
    return "ext:%s:%s" % (fname, code.lower())


def _codes(st, man):
    """(block, force, direct, geoip-direct present) — the codes of the copied files the rules may name."""
    site = set(c.lower() for c in (man.get("geosite") or {}).get("codes") or [])
    ipc = set(c.lower() for c in (man.get("geoip") or {}).get("codes") or [])
    conf = model.rules_conf(st)
    block = [c for c in (lists.BLOCK_ADS if conf["block_ads"] else ()) + (lists.BLOCK_TORRENT if conf["block_torrent"]
                                                                         else ()) if c in site]
    return block, [c for c in lists.FORCE if c in site], [c for c in lists.DIRECT if c in site], \
        lists.GEOIP_DIRECT in ipc


def xray_rules(st, sec, target, adapters, man=None, is_ready=None, chained=(), shared=None):
    """Rules for the SOCKS5 and VLESS users — after xray's block of private addresses, before the adapters' route rules:

        block    domains of category-ads/win-spy (and torrent) for every user (the switches)        -> block
        force    domains of the force group, per route target, for the ru_direct users             -> their route
        direct   domains of the direct group, then ``ext:roscomvpn-geoip.dat:direct``, ru_direct users -> direct

    Only codes present in the copied files are used (xray refuses a config with a missing code); no files —
    no rules (xray must start). ``target(route, device)`` is xray.render's mapping of a route (stage 1: the exit the
    device is pinned to, §2.4). ``chained`` — inbound tags whose users are routed one by one (their users come and go
    through xray's API without a restart): only the block rule here, the lists per user — ``user_chain``; with
    ``shared`` (stage 1 — ``target.shared_lists`` of xray.render, SPEC-stage1-exits §2.4, T0 С0.9) the lists of the
    chained inbounds are SHARED rules instead: one per (list, target), ``inboundTag`` — every chained inbound, ``user``
    — the users with that target, ``ruleTag ls-<hash>``; never a rule with an empty list (З9: a rule without
    ``user`` is a rule for EVERY user of the inbound)."""
    man = manifest() if man is None else man
    ok = _why(st, is_ready) is None                 # the lists and the consent ⚠РФ (R2): else no rule «directly»
    shared = bool(getattr(target, "shared_lists", False)) if shared is None else bool(shared)
    rules = []
    found = socks_users(st, sec, adapters)
    block, force, direct, geoip = _codes(st, man)
    if block:              # a chained inbound blocks ads before its first user too: that user comes without a restart
        have = set(t for tags, _ in found for t in tags)
        found = found + [([t], []) for t in sorted(chained) if t not in have]
    pooled = []
    for tags, users in found:
        if block:
            rules.append({"type": "field", "inboundTag": list(tags),
                          "domain": [_ext(GEOSITE_ASSET, c) for c in block], "outboundTag": "block"})
        if set(tags) & set(chained):
            if shared:
                pooled.extend(users)
            continue
        rules.extend(_list_rules(st, target, list(tags), users, ok, force, direct, geoip))
    if shared and chained:
        seen, users = set(), []
        for u, d in pooled:              # one login — one device (logins are unique); the first one wins
            if u not in seen:
                seen.add(u)
                users.append((u, d))
        for rule in _list_rules(st, target, sorted(set(chained)), users, ok, force, direct, geoip):
            rule["ruleTag"] = "ls-" + hashlib.sha256(json.dumps(rule, sort_keys=True).encode("utf-8")).hexdigest()[:12]
            rules.append(rule)
    return rules


def _list_rules(st, target, tags, users, ok, force, direct, geoip):
    """force (per target) -> direct domains -> networks of Russia for the ru_direct users of ``users``; [] when none.
    ``ok`` — «Россия напрямую» applies on the node (``_why`` is None: the lists and the consent ⚠РФ)."""
    # маршрут «блок» по выбору закрывает всё, и Россию тоже: иначе прямые правила ниже открыли бы российские сайты
    # с адреса узла (стенд T-O, O-8); запасной блок группы («fail-block», §2.2) Россию не закрывает
    ru = [(u, d) for u, d in users if ok and model.device_active(st, d)
          and ru_ok(st, device=d, why=None)
          and target(model.route_of(st, d), d) != {"outboundTag": "block"}
          and _ru_out(target, d) is not None]
    if not ru:
        return []
    rules = []
    if force:
        by = {}
        for u, d in ru:
            t = target(model.route_of(st, d), d)
            key = json.dumps(t, sort_keys=True)
            by.setdefault(key, (t, []))[1].append(u)
        for key in sorted(by):
            t, us = by[key]
            rule = {"type": "field", "inboundTag": list(tags), "user": sorted(us),
                    "domain": [_ext(GEOSITE_ASSET, c) for c in force] + ["domain:" + n for n in lists.FORCE_EXTRA]}
            rule.update(t)
            rules.append(rule)
    for out, names in _by_ru_target(target, ru):
        if direct:
            rules.append({"type": "field", "inboundTag": list(tags), "user": names,
                          "domain": [_ext(GEOSITE_ASSET, c) for c in direct], "outboundTag": out})
        if geoip:
            rules.append({"type": "field", "inboundTag": list(tags), "user": names,
                          "ip": [_ext(GEOIP_ASSET, lists.GEOIP_DIRECT)], "outboundTag": out})
    return rules


def _ru_out(target, device):
    """The outbound of the Russian lists of ``device`` (SPEC-groups §4.1, Q2): ``target.ru`` of xray.render (the
    Russian target by ``exits.ru_target``: «direct», «gr-<id>» or «fail-block»; None — no Russian rule for it); a render
    without it — «direct», as before part B."""
    ru = getattr(target, "ru", None)
    return ru(device) if ru is not None else "direct"


def _by_ru_target(target, users):
    """[(outbound, sorted logins)] of ``users`` [(login, device)] by their Russian target — «direct» first (a node
    without a Russian group draws the rules of part A byte for byte), then the others sorted."""
    by = {}
    for u, d in users:
        by.setdefault(_ru_out(target, d), []).append(u)
    return [(o, sorted(by[o])) for o in sorted(by, key=lambda o: (o != "direct", o))]


def user_chain(st, target, tags, user, device, man=None, is_ready=None):
    """The wave-3 rules of ONE user of a ``chained`` inbound, in the order of ``xray_rules`` (force -> direct domains ->
    networks of Russia); the ads block stays one rule for the whole inbound. [] when the user is not ru_direct, its
    route is «block» (by choice), the lists are not ready or there is no consent ⚠РФ (R2) — and always with the shared
    lists of stage 1 (``target.shared_lists``: they are the rules ``ls-`` of ``xray_rules``, the chain is the route rule
    alone, §2.4)."""
    if getattr(target, "shared_lists", False):
        return []
    man = manifest() if man is None else man
    why = _why(st, is_ready)
    t = target(model.route_of(st, device), device)
    if not (why is None and model.device_active(st, device) and ru_ok(st, device=device, why=why)
            and t != {"outboundTag": "block"}):
        return []
    _, force, direct, geoip = _codes(st, man)
    out = []
    if force:
        rule = {"type": "field", "inboundTag": list(tags), "user": [user],
                "domain": [_ext(GEOSITE_ASSET, c) for c in force] + ["domain:" + n for n in lists.FORCE_EXTRA]}
        rule.update(t)
        out.append(rule)
    ro = _ru_out(target, device)
    if ro is None:
        return []
    if direct:
        out.append({"type": "field", "inboundTag": list(tags), "user": [user],
                    "domain": [_ext(GEOSITE_ASSET, c) for c in direct], "outboundTag": ro})
    if geoip:
        out.append({"type": "field", "inboundTag": list(tags), "user": [user],
                    "ip": [_ext(GEOIP_ASSET, lists.GEOIP_DIRECT)], "outboundTag": ro})
    return out


def _copy(src, dst):
    tmp = dst + ".new"
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o644)
    os.rename(tmp, dst)


def sync_assets(st, sec, adapters, v=None):
    """Copy the stored .dat files into the asset directory of the gateway xray when SOCKS5 needs them and they
    differ (0644 in a 0755 directory: the user kaskad-xray reads them; the config itself stays root's). Returns
    {"wanted", "changed", "geoip", "geosite"}. Files are never removed here: a running xray loaded them at its start
    and an old config may still name them (``xray.remove`` takes them with the gateway)."""
    v = store.active() if v is None else v
    out = {"wanted": xray_wanted(st, sec, adapters, ready(v)), "changed": False, "geoip": False, "geosite": False}
    if not out["wanted"] or v is None:
        m = manifest()
        out["geoip"], out["geosite"] = "geoip" in m, "geosite" in m
        return out
    d = asset_dir()
    util.ensure_dir(d, 0o755)
    raw = snapshot.read_json(os.path.join(d, MANIFEST), {})
    cur = raw if isinstance(raw, dict) else {}
    new = {}
    for key, name, asset in (("geoip", store.GEOIP, GEOIP_ASSET), ("geosite", store.GEOSITE, GEOSITE_ASSET)):
        src = v.file(name)
        codes = (v.meta.get(key) or {}).get("codes")
        if not src or not isinstance(codes, list):
            if isinstance(cur.get(key), dict):
                new[key] = cur[key]                 # an older file stays usable (a text fallback has no geoip.dat)
            continue
        sha = v.sha256(name)
        dst = os.path.join(d, asset)
        have = cur.get(key) if isinstance(cur.get(key), dict) else {}
        try:
            same = have.get("sha256") == sha and os.path.getsize(dst) == os.path.getsize(src)
        except OSError:
            same = False
        if not same:
            _copy(src, dst)
            out["changed"] = True
        new[key] = {"file": asset, "sha256": sha, "codes": sorted(str(c).upper() for c in codes)}
    if new != cur:
        util.atomic_write(os.path.join(d, MANIFEST), util.json_text(new), 0o644)
        out["changed"] = True
    m = manifest()
    out["geoip"], out["geosite"] = "geoip" in m, "geosite" in m
    return out


def remove_assets():
    """The asset files and their manifest (``gw purge``)."""
    gone = False
    for name in (GEOIP_ASSET, GEOSITE_ASSET, MANIFEST, GEOIP_ASSET + ".new", GEOSITE_ASSET + ".new"):
        try:
            os.unlink(os.path.join(asset_dir(), name))
            gone = True
        except OSError:
            pass
    return gone
