"""3.3.7.15 F12б (КТ) — «Разнести выходы по адресам» (k3/handoff/SPEC-R1-3315-2026-10-30.md §3 F12б; PLAN-multi-ip-roles
§2.4 п. 1): the exits of the pool (AmneziaWG and xray) without an address of their own get one of the addresses «для
выхода», so that the node is not seen knocking at every foreign exit from one address (ТСПУ, the link «наш адрес →
зарубежный выход»). The sites see the addresses of the exits as before: only the address the node reaches its exits
from changes.

A module of its own, not gw/mip.py: F12б is the point of the checkpoint 03.11 and is taken out by reverting its own
commits. It writes only the field of 3.3.7.13 ``gateway.exits[id].src`` — the one ``mip.set_src`` (``gw addresses exit
ID IP``) writes: no new format, no migration, 3.3.7.13/14 read it and draw it (SNAT of the leg, ``sendThrough``). The
way back — the old ``gw addresses exit ID main`` (``PUT /api/gw/addresses/exit`` with ``"ip": "main"``).

The rules (the same as ``mip.set_src`` checks one by one):
* the candidates — the addresses «для выхода» (``addresses.out``; no choice — every public IPv4 of this machine) that
  are on the machine now;
* an AmneziaWG leg — only an address an answer from which leaves the way of the default route (``mip.route_ok``: else
  the hoster drops the packets of the SNAT); two legs to one server ``E:P`` — one address (``same_endpoint``: the SNAT
  of a leg is drawn by its Endpoint), a leg of such a pair already with an address — the other one takes the same;
* an exit whose key leads to an IPv6 — not touched (an IPv4 to it would break it, F11);
* an exit with an address of its own (by hand or by an earlier spread) — never moved: adding an address moves nothing
  by itself, a spread again places only the exits without one;
* the layout is balanced (the least loaded candidate, the exits with an address of their own counted), a tie broken by
  the rendezvous hash of the exit (``exits.hrw``): the same exits and addresses — the same layout, whatever the order
  of the pool.

    endpoints(pool)               {awg id: (E, P)} — the Endpoint of each leg from its config
    layout(units, load)           {key: ip} — the pure balanced choice
    plan(st, v, pool, eps)        {"cands", "moves": [{"id", "src", "kind", "follow"}], "skipped": [{"id", "why"}]}
    spread_exits(runner, dry, yes, want_ver)   the action of the panel (``POST /api/gw/addresses/spread``); no CLI
                                  and no MCP (SPEC-R1 §1 решение 13)
"""

from .. import state, util
from ..i18n import tr
from . import addresses, mip, model

KINDS = ("awg", "xray")
NAMES_MAX = 8                           # names of exits in one line of the window; the rest — «и ещё N»
WHY = ("family", "no_leg", "same_endpoint", "route")


def endpoints(pool):
    """{id: (E, P)} of the AmneziaWG exits of ``pool`` whose leg config names an IPv4 Endpoint
    (``mip.leg_endpoint``)."""
    out = {}
    for e in pool or []:
        idx = mip._leg_index(e) if e.get("kind") == "awg" else None
        ep = mip.leg_endpoint(idx) if idx is not None else None
        if ep:
            out[e["id"]] = ep
    return out


def layout(units, load):
    """The balanced choice: ``units`` — [{"key", "n" (exits of the unit), "cands": [ip]}], ``load`` — {ip: exits there
    already}. Each unit in turn — the fewest candidates first (the AmneziaWG legs, whose candidates are a part of the
    others'), then by key — goes to the least loaded of its candidates; a tie — the rendezvous hash of its key
    (``exits.hrw``). -> {key: ip}; a unit without candidates is absent. Pure: the same input, the same answer."""
    from . import exits as X
    load = dict(load or {})
    out = {}
    for u in sorted(units, key=lambda u: (len(u["cands"]), u["key"])):
        if not u["cands"]:
            continue
        least = min(load.get(ip, 0) for ip in u["cands"])
        pick = X.hrw(u["key"], [{"id": ip} for ip in u["cands"] if load.get(ip, 0) == least])["id"]
        out[u["key"]] = pick
        load[pick] = load.get(pick, 0) + u["n"]
    return out


def plan(st, v, pool, eps):
    """What a spread would write now (pure: the state ``st``, the view ``v`` of the cache, the ``pool``, ``eps`` of
    ``endpoints``). -> {"cands": [the addresses «для выхода» on the machine], "cands_awg": [those with a way out by the
    default route], "moves": [{"id", "src", "kind", "follow"}] (``follow`` — a leg taking the address of the other leg
    to its server), "skipped": [{"id", "why"}] (``why`` of ``WHY``)}."""
    outs = mip.out_list(st)
    base = outs if outs is not None else mip._ours4(st, v)
    locs = dict((ip, mip.local_of(st, v, ip)) for ip in base)
    cands = [ip for ip in base if locs[ip]]
    cands_awg = [ip for ip in cands if mip.route_ok(v, locs[ip]) is True]
    ours = sorted((e for e in pool or [] if e.get("kind") in KINDS), key=lambda e: e["id"])
    pinned = dict((e["id"], mip.exit_src(st, e["id"])) for e in ours if mip.exit_src(st, e["id"]))
    load = dict((ip, sum(1 for x in pinned.values() if x == ip)) for ip in cands)
    held = {}                                           # E:P -> the addresses of its legs with an address of their own
    for e in ours:
        if e["id"] in pinned and e["id"] in eps:
            held.setdefault(eps[e["id"]], set()).add(pinned[e["id"]])
    moves, skipped, units, legs = [], [], [], {}
    for e in ours:
        eid = e["id"]
        if eid in pinned:
            continue
        if mip.exit_v6(e):
            skipped.append({"id": eid, "why": "family"})
        elif e["kind"] == "xray":
            units.append({"key": eid, "ids": [eid], "n": 1, "cands": cands, "kind": "xray"})
        elif eid not in eps:
            skipped.append({"id": eid, "why": "no_leg"})
        else:
            legs.setdefault(eps[eid], []).append(eid)
    for ep in sorted(legs):
        ids = legs[ep]
        have = held.get(ep)
        if have:                                        # same_endpoint: the address of the other leg, or nothing
            ip = next(iter(have)) if len(have) == 1 else None
            if ip in cands_awg:
                moves += [{"id": x, "src": ip, "kind": "awg", "follow": True} for x in ids]
                load[ip] += len(ids)
            else:
                skipped += [{"id": x, "why": "same_endpoint"} for x in ids]
        elif not cands_awg:
            skipped += [{"id": x, "why": "route"} for x in ids]
        else:
            units.append({"key": ids[0], "ids": ids, "n": len(ids), "cands": cands_awg, "kind": "awg"})
    got = layout(units, load)
    for u in units:
        if u["key"] in got:
            moves += [{"id": x, "src": got[u["key"]], "kind": u["kind"], "follow": False} for x in u["ids"]]
    moves.sort(key=lambda m: m["id"])
    skipped.sort(key=lambda s: s["id"])
    return {"cands": cands, "cands_awg": cands_awg, "moves": moves, "skipped": skipped}


def _names(ids, names):
    shown = [names.get(x, x) for x in ids]
    if len(shown) > NAMES_MAX:
        return tr("gw_mip_spread.more", names=", ".join(shown[:NAMES_MAX]), n=len(shown) - NAMES_MAX)
    return ", ".join(shown)


def _by_address(moves):
    by = {}
    for m in moves:
        by.setdefault(m["src"], []).append(m["id"])
    return [(ip, by[ip]) for ip in sorted(by, key=mip._ipkey)]


def _reconnect(st, v, moves):
    """The legs whose address really changes (not a leg following the other one to its server — the SNAT by Endpoint
    took it already; not an address that is the source of the route — the leg went from it before)."""
    was = (v or {}).get("src") or mip.masq_source(v)
    return [m for m in moves if m["kind"] == "awg" and not m["follow"]
            and (not was or mip.local_of(st, v, m["src"]) != was)]


def _back(st, v):
    """REV-3315-b3 П-1: the way back of an exit to the route address — in the panel (the answer is the panel's). While
    the route address is not «для выхода», «по маршруту системы» is refused (``mip.set_src`` ``not_out``): first
    «Для выхода» on it."""
    outs = mip.out_list(st)
    route = mip.public_of(st, v, v["src"]) if v and v.get("src") else ""
    if outs is not None and route and route not in outs:
        return tr("gw_mip_spread.back_out", ip=route)
    return tr("gw_mip_spread.back")


def _consequences(st, v, p, names):
    items = [tr("gw_mip_spread.lock.exits", ip=ip, names=_names(ids, names)) for ip, ids in _by_address(p["moves"])]
    legs = _reconnect(st, v, p["moves"])
    if legs:
        items.append(tr("gw_mip_spread.lock.awg", names=_names([m["id"] for m in legs], names)))
    items.append(tr("gw_mip_spread.lock.sites"))
    return items


def _checked(st, v, pool, eps):
    """``plan`` or the refusal «разносить некуда»: fewer than two addresses «для выхода» on the machine."""
    p = plan(st, v, pool, eps)
    if len(p["cands"]) < 2:
        raise mip._refuse("gw_mip_spread.err.one", "spread_one", back=tr("gw_mip.back_roles"),
                          ips=", ".join(p["cands"]) or tr("gw_addresses.none"))
    return p


def spread_exits(runner, dry=False, yes=False, want_ver=None):
    """F12б: «Разнести выходы по адресам» (the rules — the docstring of the module). ``dry`` — the plan, nothing written
    (the cache of «Адреса сервера» is looked at anew, as by every write here); else in one ``ops._change``, confirmed
    (``yes``; without it 409 ``need: "confirm"`` with ``consequences``: which exits go from which address, the legs
    that reconnect), then the conntrack of the legs whose address changed is forgotten (``mip.forget``). Nothing to
    place — no write. Refused: fewer than two addresses «для выхода» (409 ``spread_one``); another ``ver`` (409
    ``object_changed``). -> {"ok", "dry", "moves": [{"id", "src", "kind"}], "skipped": [{"id", "why"}],
    "consequences", "flushed", "warnings", "text"}."""
    from . import exits as X
    from . import ops
    addresses.collect(runner, net=False)                # the machine now: never a decision on a gone address
    v = mip.machine()
    pool = [e for e in mip._pool(runner) if e.get("kind") in KINDS]
    eps = endpoints(pool)
    st0 = state.load()
    names = dict((e["id"], X.label(st0, e)) for e in pool)
    mip.check_ver(st0, want_ver)                        # a stale window first (under the lock again, below)
    p = _checked(st0, v, pool, eps)
    box = {"p": p, "said": []}
    if not dry and p["moves"]:
        def mutate(new, sec):
            mip.check_ver(new, want_ver)
            q = _checked(new, v, pool, eps)
            box["p"] = q
            if not q["moves"]:
                return
            box["said"] = mip._confirm(_consequences(new, v, q, names), yes, tr("gw_mip.confirm.panel"))
            recs = model.gw(new)["exits"]
            for m in q["moves"]:
                rec = recs.get(m["id"]) if isinstance(recs.get(m["id"]), dict) else {}
                rec["src"] = m["src"]
                recs[m["id"]] = rec
        _, res = ops._change(runner, mutate)
    else:
        res = None
    p = box["p"]
    done = not dry and bool(p["moves"])
    flushed = 0
    if done:
        flushed = mip.forget(runner, [eps[m["id"]] for m in _reconnect(st0, v, p["moves"]) if m["id"] in eps])
    lines = "; ".join(tr("gw_mip_spread.line", ip=ip, names=_names(ids, names)) for ip, ids in _by_address(p["moves"]))
    back = _back(state.load() if done else st0, v)
    if not p["moves"]:                                  # REV-3315-b3 З-1: the reason as it is
        if not pool:
            text = tr("gw_mip_spread.no_exits")
        elif p["skipped"]:                              # exits without an address there are, none can take one
            text = tr("gw_mip_spread.nothing_skipped")
        else:
            text = tr("gw_mip_spread.nothing", back=back)
    elif dry:
        text = tr("gw_mip_spread.preview", n=len(p["moves"]), lines=lines)
    else:
        text = tr("gw_mip_spread.done", n=len(p["moves"]), lines=lines, back=back)
        util.log_tr("gw_mip_spread.log.done", lines=lines)
    if p["skipped"]:
        text += " " + tr("gw_mip_spread.skipped", lines="; ".join(
            tr("gw_mip_spread.skip_line", name=names.get(s["id"], s["id"]), why=tr("gw_mip_spread.why." + s["why"]))
            for s in p["skipped"]))
    warns = list(box["said"]) + list((res or {}).get("warnings") or [])
    if p["moves"] and not model.enabled(state.load()):
        warns.append(tr("gw_mip.warn.src_gw_off"))
    if any(m["kind"] == "xray" for m in p["moves"]):
        warns.append(tr("gw_mip.src.tg"))               # REV-mip-s34 З-4: the bot's proxy is not the gateway's xray
    return {"ok": True, "dry": bool(dry),
            "moves": [{"id": m["id"], "src": m["src"], "kind": m["kind"]} for m in p["moves"]],
            "skipped": [dict(s) for s in p["skipped"]],
            "consequences": _consequences(st0, v, p, names) if p["moves"] else [],
            "flushed": flushed, "warnings": warns, "text": text}
