"""The choice of an exit for a device and the pins (SPEC-stage1-exits §4.4–4.5, §2.2, §2.6; SPEC-exit-groups §2.7;
T2). A pure function: no command, no file, no clock.

    assign(devices_by_group, states, policy, pins, metrics, node, now) -> Decision
    pick_new(device_id, gid, states, policy, metrics) -> exit id | None   the same rule for processes outside the
                                                                          collector (hooks, ops._apply, the panel)

``devices_by_group`` {gid: [{"id": device id, "fail": route.fail, "exit": E (a route «exit:E» whose E is unfit)}]} —
the active devices whose route resolves into the group (``pool``/``group:G``; ``exit:E`` when E of G is unfit);
``states``  {eid: "ok"|"degraded"|"standby"|"off"|"unknown"} or {eid: ExitHealth};
``policy``  the effective exit policy (``policy.load``);
``pins``    the document of ``pins.json`` ({"version", "groups": {gid: {pins, tier_good_since, active_tier,
            last_step}}, "gw_all"}) or None;
``metrics`` {eid: {"kind": "awg"|"xray", "rtt": ms, "x_norm": rtt / norm, "speed_mbps", "load": 0..1,
            "quota": bool}} — what the methods other than «even» need;
``node``    {"offline": bool, "ru_offline": bool, "local": ("YYYY-MM-DD", "HH:MM") — the time of the node (§3.7) for
            the night rebalance, "regroup": {gid} — «Разложить заново» of these groups this round (F14)};
``now``     wall seconds (``since``, ``tier_good_since`` — clamped to ≤ now on read, §3.4).

Decision::

    {"version": 1, "groups": {gid: {"pins": {dev: {"exit", "tier", "since", "why"}}, "active_tier": tid | None,
                                    "tier_good_since": {tid: ts}, "last_step": None | "direct" | "block",
                                    "last_since": ts | None, "night_on": date | None, "told_tier": tid | None,
                                    "steps": {dev: "exit" | "last_direct" | "last_block"},
                                    "ranks": {tid: rows(tier)}}},
     "states": {eid: state}, "moves": [{"device", "group", "from", "to", "why"}],
     "gw_all": "gx-<id>" | "direct" | "block" | None, "gw_all_exit": eid | None, "dns_via": "main" | gid | "direct"
     | "none", "events": [...]}

Inside a group: the active tier — the first tier with a good («ok») exit; none — the first with a degraded one; then
one «в запасе»; nothing — the last step of the group (``fail``: block | direct). «Снят» is never used. The hash of
«even» is the one of 3.3.3 (``exits.hrw``): with the weights 1 the first choice after the update is that of 3.3.3.
"""

import copy
import hashlib
import math

from . import exits, policy as P

CLASSES = ("ok", "degraded", "standby")        # B2: good -> degraded -> in reserve
NIGHT_WINDOW_MIN = 15                           # the night rebalance runs once in [at, at + 15 min) of the node


def _state(states, eid):
    s = (states or {}).get(eid)
    if isinstance(s, dict):
        s = s.get("state")
    return s if s in ("ok", "degraded", "standby", "off", "unknown") else "off"


def _seen(states, eid):
    """The state an exit counts as for what it already has (§4.2): «unknown» — as its last known state (``shown`` of
    ExitHealth; a bare «unknown» or one never known — «unknown»); the rest — itself. New devices go by ``_state``:
    an «unknown» exit never gets them."""
    s = _state(states, eid)
    if s == "unknown":
        rec = (states or {}).get(eid)
        shown = rec.get("shown") if isinstance(rec, dict) else None
        if shown in ("ok", "degraded", "standby", "off"):
            return shown
    return s


def _score(device_id, eid, w):
    """−w / ln(h) with h = (x + 1) / (2^64 + 1) of the hash of 3.3.3 — strictly inside (0, 1)."""
    x = int(hashlib.sha1(("%s|%s" % (device_id, eid)).encode("utf-8")).hexdigest()[:16], 16)
    h = (x + 1) / float(2 ** 64 + 1)
    return -float(w) / math.log(h)


def hrw(key, ids, weights=None):
    """The weighted rendezvous choice among ``ids`` for ``key`` (ties — the smaller id, as ``exits.hrw``)."""
    best = None
    for eid in sorted(ids):
        w = (weights or {}).get(eid, 1)
        s = _score(key, eid, w if w and w > 0 else 1)
        if best is None or s > best[0]:
            best = (s, eid)
    return best[1] if best else None


def _tiers(grp):
    return [t for t in (grp or {}).get("tiers") or [] if isinstance(t, dict)]


def _klass(states, grp, seen=False):
    """(index of the active tier, its class) — the first tier with an exit of the best class; (None, None) —
    nothing usable in the group (the last step). ``seen`` — «unknown» as its last known state (``_seen``): what the
    group stands on; without — what a new device can get."""
    tiers = _tiers(grp)
    of = _seen if seen else _state
    for k in CLASSES:
        for i, t in enumerate(tiers):
            if any(of(states, e) == k for e in t.get("exits") or []):
                return i, k
    return None, None


def _cands(states, grp, i, k):
    if i is None:
        return []
    return [e for e in _tiers(grp)[i].get("exits") or [] if _state(states, e) == k]


def spread_cands(states, grp):
    """«по соединениям» (§4.5): the exits a new connection of the group may take now — those of the best class in the
    first tier that has it (``_klass`` without ``seen``: never an «unknown» one). One answer for the kernel
    (``net.spread_marks``) and for xray (``xray._vless_live``): both on the same tier (КТ1 Н7)."""
    i, k = _klass(states, grp) if grp else (None, None)
    return _cands(states, grp, i, k) if grp else []


def _kind(metrics, eid):
    k = ((metrics or {}).get(eid) or {}).get("kind")
    return "xray" if k in ("xray", "vless") else (k or "awg")


def _num(v):
    """A finite number (NaN or infinity of a measure — none: the rows below always end)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _tol(best, tier, use):
    """The tolerance of «equal» responses from ``best``: max(tolerance_ms, tolerance_pct of it) by ``rtt``; pct only by
    ``x_norm`` (a tier of both kinds)."""
    tol_pct = best * float(tier.get("tolerance_pct", 20) or 0) / 100.0
    return max(float(tier.get("tolerance_ms", 30) or 0), tol_pct) if use == "rtt" else tol_pct


def _capacity_w(cands, metrics):
    out = {}
    for e in cands:
        m = (metrics or {}).get(e) or {}
        sp, ld = m.get("speed_mbps"), m.get("load")
        if isinstance(sp, (int, float)) and sp > 0:
            ld = ld if isinstance(ld, (int, float)) and 0 <= ld <= 1 else 0
            out[e] = max(sp * (1 - ld), 1e-6)
    return out


def manual(tier):
    """«По порядку (вручную)» (PLAN-order §3): the tier's ``order`` is «manual» — new devices go to the first exit of
    the list among those of the best class; ``method`` (and its weights, tolerance) stays for the way back."""
    return isinstance(tier, dict) and tier.get("order") == "manual"


def _row(ids, v=None, share=None):
    return [{"id": e, "v": (v or {}).get(e), "share": (share or {}).get(e, 0.0)} for e in ids]


def _ranked(cands, tier, metrics, per_conn=False):
    """The rows of «strength» of ``cands`` (the exits of one class, in the order of the tier) — ONE computation for
    what the panel shows and what ``_choose`` picks (PLAN-order §2):

    * ``by``: «even» | «rtt» | «x_norm» | «weights» | «capacity» | «manual» | «per_connection» — what ``v`` is;
    * ``queue``: True — only the first row gets new devices (best_rtt, manual); False — shares over every row;
    * ``rows``: [[{"id", "v", "share"}…]…] — equal exits in one row, in the order of the tier inside a row;
    * ``nometric``: ids without the measure the method needs (best_rtt: never chosen while another has one);
    * ``nodata``: the method needs measures and none has them — one row, chosen as «even» (as before).

    best_rtt — row 1: the least response and all within ``_tol`` of it (``equal`` of 3.3.7.13), row 2: the same among
    the rest from their least, … (the queue if row 1 drops out); weights — by the weight (``hrw``: not above 0 — 1),
    equal weights one row; capacity — by speed × (1 − load), within tolerance_pct of the leader of the row one row;
    the exits without a speed test (weight 1, as ``hrw`` takes them) — the last row, ``v`` None."""
    ids = []
    for e in cands:
        if e not in ids:
            ids.append(e)
    tier = tier if isinstance(tier, dict) else {}
    res = {"by": "even", "queue": False, "rows": [], "nometric": [], "nodata": False}
    if not ids:
        return res
    n = float(len(ids))
    even = dict((e, 1 / n) for e in ids)
    if per_conn:
        return dict(res, by="per_connection", rows=[_row(ids, share=even)])
    if manual(tier):
        return dict(res, by="manual", queue=True,
                    rows=[_row([e], {e: i + 1}, {e: 1.0 if i == 0 else 0.0}) for i, e in enumerate(ids)])
    m = tier.get("method", "even")
    pos = dict((e, i) for i, e in enumerate(ids))
    if m == "best_rtt":
        kinds = set(_kind(metrics, e) for e in ids)
        use = "rtt" if len(kinds) == 1 else "x_norm"
        vals = dict((e, ((metrics or {}).get(e) or {}).get(use)) for e in ids)
        vals = dict((e, v) for e, v in vals.items() if _num(v))
        if not vals:
            return dict(res, by=use, queue=True, nodata=True, rows=[_row(ids, share=even)])
        rest = sorted(vals, key=lambda e: (vals[e], pos[e]))
        rows = []
        while rest:
            best = vals[rest[0]]
            tol = _tol(best, tier, use)
            row = rest[:1] + [e for e in rest[1:] if vals[e] <= best + tol]
            rest = [e for e in rest if e not in row]
            row.sort(key=lambda e: pos[e])
            share = dict((e, 1.0 / len(row)) for e in row) if not rows else {}
            rows.append(_row(row, vals, share))
        return dict(res, by=use, queue=True, rows=rows, nometric=[e for e in ids if e not in vals])
    if m == "weights":
        ws = tier.get("weights") or {}
        ws = ws if isinstance(ws, dict) else {}
        w = dict((e, ws.get(e, 1)) for e in ids)
        w = dict((e, v if _num(v) and v > 0 else 1) for e, v in w.items())
        tot = float(sum(w.values()))
        rows = []
        for v in sorted(set(w.values()), reverse=True):
            rows.append(_row([e for e in ids if w[e] == v], w, dict((e, w[e] / tot) for e in ids)))
        return dict(res, by="weights", rows=rows)
    if m == "capacity":
        cw = _capacity_w(ids, metrics)
        if not cw:
            return dict(res, by="capacity", nodata=True, rows=[_row(ids, share=even)])
        w = dict((e, cw.get(e, 1)) for e in ids)
        tot = float(sum(w.values()))
        share = dict((e, w[e] / tot) for e in ids)
        pct = float(tier.get("tolerance_pct", 20) or 0) / 100.0
        rest = sorted(cw, key=lambda e: (-cw[e], pos[e]))
        rows = []
        while rest:
            lead = cw[rest[0]]
            row = rest[:1] + [e for e in rest[1:] if cw[e] >= lead * (1 - pct)]
            rest = [e for e in rest if e not in row]
            row.sort(key=lambda e: pos[e])
            rows.append(_row(row, cw, share))
        bare = [e for e in ids if e not in cw]
        if bare:
            rows.append(_row(bare, None, share))
        return dict(res, by="capacity", rows=rows)
    return dict(res, rows=[_row(ids, share=even)])


def _pick(key, r, tier):
    """The exit for ``key`` by the rows ``r`` of ``_ranked``: manual — the first; a queue — HRW over the first row;
    shares — HRW over every row with the weights of the method (the hash and weights of 3.3.7.13)."""
    rows = r["rows"]
    if not rows:
        return None
    if r["by"] == "manual":
        return rows[0][0]["id"]
    if r["queue"]:
        return hrw(key, [x["id"] for x in rows[0]])
    ids = [x["id"] for row in rows for x in row]
    if r["by"] == "weights":
        return hrw(key, ids, tier.get("weights") or {})
    if r["by"] == "capacity" and not r["nodata"]:
        return hrw(key, ids, dict((x["id"], x["v"] if x["v"] is not None else 1) for row in rows for x in row))
    return hrw(key, ids)


def _best_rtt(key, cands, tier, metrics):
    """The exits of one kind with the least response, those within max(tolerance_ms, tolerance_pct) of it equal ->
    HRW among them; a tier of both kinds — by x_norm (pct only). No data — HRW among all."""
    t = dict(tier or {}, method="best_rtt")
    t.pop("order", None)
    return _pick(key, _ranked(cands, t, metrics), t)


def _choose(key, cands, tier, metrics):
    if not cands:
        return None
    return _pick(key, _ranked(cands, tier, metrics), tier)


def rows(tier, states, metrics, per_conn=False):
    """The rows of «strength» of a tier for the panel, the CLI and MCP (PLAN-order §2.1) — the same ``_ranked`` the
    choice goes by. Taking part — the exits of the best class present in the tier («ok», else «degraded», else «в
    запасе»; for the active tier — exactly the candidates of ``_cands``); the others — ``out`` with the reason: their
    state, or «nometric» (no measure while the method needs one). ``per_conn`` — «по соединениям» (one row, the flag
    of the order is not looked at). -> {"by", "queue", "order": "auto"|"manual", "klass", "rows", "out", "nodata"}."""
    tier = tier if isinstance(tier, dict) else {}
    ex = []
    for e in tier.get("exits") or []:
        if isinstance(e, str) and e not in ex:
            ex.append(e)
    k = next((c for c in CLASSES if any(_state(states, e) == c for e in ex)), None)
    cands = [e for e in ex if _state(states, e) == k] if k else []
    r = _ranked(cands, tier, metrics, per_conn)
    out = []
    for e in ex:
        if e not in cands:
            out.append({"id": e, "why": _state(states, e)})
        elif e in r["nometric"]:
            out.append({"id": e, "why": "nometric"})
    return {"by": r["by"], "queue": r["queue"], "order": "manual" if manual(tier) and not per_conn else "auto",
            "klass": k, "rows": r["rows"], "out": out, "nodata": r["nodata"]}


def pick_new(device_id, gid, states, policy, metrics=None, prefer=None):
    """The exit a new device of the group ``gid`` gets now: the method of the active tier over its exits of the
    active class; ``prefer`` — the exit of a route «exit:E» (kept when it is one of them, П4). None — the last step."""
    grp = P.group(policy, gid)
    if grp is None:
        return None
    i, k = _klass(states, grp)
    cands = _cands(states, grp, i, k)
    if prefer in cands:
        return prefer
    return _choose(device_id, cands, _tiers(grp)[i], metrics) if cands else None


def _tier_index(grp, eid):
    for i, t in enumerate(_tiers(grp)):
        if eid in (t.get("exits") or []):
            return i
    return None


def _last_step(grp, fail):
    return "last_direct" if (grp or {}).get("fail") == "direct" or fail == "direct" else "last_block"


def _empty_group():
    return {"pins": {}, "active_tier": None, "tier_good_since": {}, "last_step": None, "last_since": None,
            "night_on": None, "told_tier": None, "steps": {}}


def _clamp_group(gp, now):
    gp = copy.deepcopy(gp) if isinstance(gp, dict) else {}
    out = _empty_group()
    out.update(dict((k, v) for k, v in gp.items() if k in out))
    if "told_tier" not in gp:                       # a file before T-3379 З-1: the feed said the tier it stood on
        out["told_tier"] = out["active_tier"]
    for k in ("pins", "tier_good_since", "steps"):
        if not isinstance(out[k], dict):
            out[k] = {}
    out["tier_good_since"] = dict((t, min(v, now)) for t, v in out["tier_good_since"].items()
                                  if isinstance(v, (int, float)))
    for dev, p in list(out["pins"].items()):
        if not isinstance(p, dict) or not p.get("exit"):
            del out["pins"][dev]
            continue
        if isinstance(p.get("since"), (int, float)):
            p["since"] = min(p["since"], now)
    return out


def _night(grp, gp, node):
    nr = grp.get("night_rebalance") or {}
    loc = (node or {}).get("local")
    if not nr.get("enabled") or not loc or len(loc) != 2:
        return False
    day, hm = loc
    at = nr.get("at") or "04:00"
    try:
        cur = int(hm[:2]) * 60 + int(hm[3:5])
        want = int(at[:2]) * 60 + int(at[3:5])
    except (TypeError, ValueError):
        return False
    if not 0 <= cur - want < NIGHT_WINDOW_MIN or gp.get("night_on") == day:
        return False
    gp["night_on"] = day
    return True


def _group(gid, grp, devs, states, metrics, gp, node, now, moves, events):
    tiers = _tiers(grp)
    si, sk = _klass(states, grp, seen=True)           # what the group stands on («unknown» as it was, §4.2)
    i, k = _klass(states, grp)                         # what a new device gets (never an «unknown» exit)
    if i is None and si is not None:
        i, k = si, sk                                  # only «unknown» there: the tier stays, no candidates
    tid = tiers[si]["id"] if si is not None else None
    good = set(e for t in tiers for e in t.get("exits") or [] if _seen(states, e) == "ok")
    for t in tiers:                                 # tier_good_since: continuously with an «ok» exit
        if any(e in good for e in t.get("exits") or []):
            gp["tier_good_since"].setdefault(t["id"], now)
        else:
            gp["tier_good_since"].pop(t["id"], None)
    for t in list(gp["tier_good_since"]):
        if t not in [x["id"] for x in tiers]:
            del gp["tier_good_since"][t]
    was_tier, was_last = gp.get("active_tier"), gp.get("last_step")
    cands = _cands(states, grp, i, k)
    rtp = grp.get("return_to_primary") or {}
    hold_s = 60 * int(rtp.get("hold_min") or 10)
    # F14 «Разложить заново» (SPEC-R1 §1 решения 2–4): the night rebalance now for this group — the same rule, without
    # ``night_rebalance.enabled`` and without ``night_on`` (the real night goes as usual)
    night = _night(grp, gp, node) or gid in ((node or {}).get("regroup") or ())
    per_conn = bool(grp.get("per_connection")) and gid == P.MAIN
    old_pins = gp["pins"]
    pins, steps, back_moved = {}, {}, 0
    for d in devs:
        dev = d["id"]
        own = d.get("exit")
        p = old_pins.get(dev)
        on_own = bool(p and p.get("exit") == own)
        if own and _tier_index(grp, own) is not None and not per_conn and \
                (_seen if on_own else _state)(states, own) in ("ok", "degraded"):
            if not on_own:
                p_new = {"exit": own, "tier": tiers[_tier_index(grp, own)]["id"], "since": now, "why": "new"}
                if p:                               # «exit:E» on E again (E is back, the route changed): a move
                    moves.append({"device": dev, "group": gid, "from": p.get("exit"), "to": own, "why": "new"})
                p = p_new
            pins[dev] = p
            steps[dev] = "exit"
            continue
        if si is None:
            steps[dev] = _last_step(grp, d.get("fail"))
            if dev in old_pins:
                moves.append({"device": dev, "group": gid, "from": old_pins[dev]["exit"], "to": None,
                              "why": "evict"})
            continue
        steps[dev] = "exit"
        if per_conn:
            continue                                # «по соединениям»: no pins, the tier decides (§4.5)
        why = None
        if p:
            e = p["exit"]
            st = _seen(states, e)
            ti = _tier_index(grp, e)
            if ti is None or st == "off":
                why = "evict"                       # out of the group (the pool, a move) or «снят»
            elif st == "standby" and sk != "standby":
                why = "evict"                       # in reserve while the group has better ones
            elif st == "degraded" and grp.get("evict_degraded", True) and good:
                why = "evict"
            elif rtp.get("enabled", True) and ti > si and i == si and sk == "ok" and st in ("ok", "degraded") and \
                    gp["tier_good_since"].get(tid) is not None and now - gp["tier_good_since"][tid] >= hold_s:
                why = "return"                      # the primary tier good for hold_min (a fall below — at once, п. 1);
                                                    # only onto the tier it stands on (not «unknown» there: REV-s1-t2-2 Н1)
            elif night and ti == si == i and st == "ok":
                home = _choose(dev, cands, tiers[i], metrics)
                if home and home != e:
                    why = "rebalance"               # the night: back to its «home» exit inside the tier
            if why is None:
                pins[dev] = p                       # stays: never moves inside a tier while its exit is good
                continue
        target = None
        if own in cands:
            target = own                            # «exit:E» in reserve: E itself before another reserve (П4)
        if target is None:
            target = _choose(dev, cands, tiers[i], metrics)
        if target is None:
            if p and why in ("return", "rebalance"):
                pins[dev] = p                       # nowhere better to go now (only «unknown» there): stays
                continue
            steps[dev] = _last_step(grp, d.get("fail"))
            if p:
                moves.append({"device": dev, "group": gid, "from": p.get("exit"), "to": None, "why": why or "evict"})
            continue
        if p and p.get("exit") == target:
            pins[dev] = p                           # the same exit (a «return» while its tier is «unknown»): stays
            continue
        if why is None and own and target != own:
            why = "evict"                           # «exit:E», E unfit: another exit of its group (§2.2)
        pins[dev] = {"exit": target, "tier": tiers[i]["id"], "since": now, "why": why or "new"}
        if p and p.get("exit") != target:
            moves.append({"device": dev, "group": gid, "from": p.get("exit"), "to": target, "why": why or "new"})
            if why == "return":
                back_moved += 1
    gp["pins"] = pins
    gp["steps"] = steps
    gp["active_tier"] = tid
    if si is None:
        step = "direct" if grp.get("fail") == "direct" else "block"
        n = len([s for s in steps.values() if s.startswith("last_")])
        ev = {"kind": "group_last_step", "group": gid, "role": grp.get("role", "abroad"), "step": step,
              "devices": n, "direct": len([s for s in steps.values() if s == "last_direct"])}
        if gp.get("last_since") is None and n:      # told once there are devices on it (none — nothing to tell)
            gp["last_since"] = now
            events.append(ev)
        elif n and was_last in ("block", "direct") and was_last != step:
            # already on the last step, the owner changed «при отказе» (block <-> direct): the feed tells the step now
            # (KT3-3, К2-6 З-2) — the last line never says «нет интернета» while the devices go out directly
            ev["why"] = "fail_changed"
            events.append(ev)
        gp["last_step"] = step
    else:
        if was_last is not None and gp.get("last_since") is not None:
            since = gp.get("last_since")
            events.append({"kind": "group_restored", "group": gid, "role": grp.get("role", "abroad"),
                           "exit": cands[0] if cands else None,
                           "after_s": int(now - since) if isinstance(since, (int, float)) else None})
        gp["last_step"] = None
        gp["last_since"] = None
    _failover_events(gid, grp, tiers, gp, was_tier, tid, moves, back_moved, hold_s, now, events, per_conn)
    if gid in ((node or {}).get("regroup") or ()):
        events.append(regrouped(gid, grp, len([m for m in moves if m["group"] == gid and m["why"] == "rebalance"])))


def regrouped(gid, grp, moved, frozen=None, late=False):
    """F14: the event ``group_regrouped`` of the feed — the result of «Разложить заново» (``moved`` — the devices
    taken back to their «home» exits this round); ``frozen`` — «offline» | «ru_offline»: the group was frozen at the
    round, nothing moved; ``late`` — the request waited over ``collect.REGROUP_MAX_AGE`` s (the collector stood) and was
    dropped (П-4)."""
    ev = {"kind": "group_regrouped", "group": gid, "role": (grp or {}).get("role", "abroad"), "moved": int(moved)}
    if frozen:
        ev["frozen"] = frozen
    if late:
        ev["late"] = True
    return ev


def _failover_events(gid, grp, tiers, gp, was_tier, tid, moves, back_moved, hold_s, now, events, per_conn=False):
    """``exit_failover`` against the tier the feed last SAID (``told_tier``), not against ``active_tier`` — the tier
    the group stands on by health this round (T-3379 З-1). The primary tier good again goes back to ``active_tier`` at
    once, while the devices wait for ``return_to_primary.hold_min``: compared with ``active_tier``, a flap of the
    primary inside the hold told the same «t1 → t2» again with nobody moved (and a group without devices never heard
    of its return). Now:

    * down — a tier below the told one (from the told one), or devices moved down from ``active_tier`` this round
      (a new device on the primary while the others still wait below);
    * back — the devices moved back (``back_moved``, as before); or nobody below this tier is left to come back and the
      tier has been good for the hold (``moved`` 0: «снова основной уровень», a group without devices);
    * the last step (no tier) forgets the told tier: ``group_last_step`` / ``group_restored`` say it;
    * «по соединениям» (``per_conn``: no pins, the tier IS the route, §4.5) — as before: down against ``active_tier``
      of the last round, no return of its own (the traffic is back at once)."""
    order = [t["id"] for t in tiers]
    if per_conn:
        gp["told_tier"] = told = tid if tid in order else None
        if told is not None and was_tier in order and order.index(tid) > order.index(was_tier):
            events.append(dict({"kind": "exit_failover", "group": gid, "role": grp.get("role", "abroad"),
                                "from_tier": was_tier, "to_tier": tid, "back": False,
                                "moved": len([m for m in moves if m["group"] == gid])},
                               **_tier_said(tiers, was_tier, "from_"), **_tier_said(tiers, tid, "to_")))
        return
    told = gp.get("told_tier")
    if told not in order:
        told = was_tier if was_tier in order else None  # a level the owner removed: as before, from the active one
    if tid is None or tid not in order:
        gp["told_tier"] = None
        return
    if told is None:
        gp["told_tier"] = tid                       # first sight, or back from the last step (group_restored)
        return
    ti, role = order.index(tid), grp.get("role", "abroad")
    moved = len([m for m in moves if m["group"] == gid])
    down = told if ti > order.index(told) else \
        was_tier if was_tier in order and ti > order.index(was_tier) and moved else None
    if down is not None:
        events.append(dict({"kind": "exit_failover", "group": gid, "role": role, "from_tier": down, "to_tier": tid,
                            "back": False, "moved": moved},
                           **_tier_said(tiers, down, "from_"), **_tier_said(tiers, tid, "to_")))
        told = tid
    if back_moved:
        events.append(dict({"kind": "exit_failover", "group": gid, "role": role, "from_tier": None, "to_tier": tid,
                            "back": True, "moved": back_moved}, **_tier_said(tiers, tid, "to_")))
        told = tid
    elif down is None and ti < order.index(told):
        below = any(p.get("tier") in order and order.index(p["tier"]) > ti for p in (gp.get("pins") or {}).values())
        good = gp["tier_good_since"].get(tid)
        if not below and good is not None and now - good >= hold_s:
            events.append(dict({"kind": "exit_failover", "group": gid, "role": role, "from_tier": None,
                                "to_tier": tid, "back": True, "moved": 0}, **_tier_said(tiers, tid, "to_")))
            told = tid
    gp["told_tier"] = told


def _tier_said(tiers, tid, side):
    """T15 Q5: the words of a level for the push of ``exit_failover`` — the owner's name of the level (``<side>label``)
    and its exits (``<side>exits``, ids: the layer of events names them), so a push never says the id «t2» (in a
    Russian group each key is its own level, the push names the key)."""
    t = next((x for x in tiers or [] if isinstance(x, dict) and x.get("id") == tid), None) or {}
    return {side + "label": str(t.get("name") or "").strip(), side + "exits": [e for e in t.get("exits") or [] if e]}


def _best_of(grp, states, metrics, skip_quota=False):
    """The best exit of the active tier of a group for the node itself (``gw-all``): its method; «even» — the least
    response of its kind with the tolerance (§2.6, §4.4)."""
    i, k = _klass(states, grp)
    cands = _cands(states, grp, i, k)
    if skip_quota:
        cands = [e for e in cands if not ((metrics or {}).get(e) or {}).get("quota")]
    if not cands:
        return None
    t = _tiers(grp)[i]
    if t.get("method", "even") == "even" and not manual(t):
        return _best_rtt("node", cands, t, metrics)
    return _choose("node", cands, t, metrics)


def gw_all(policy, states, metrics=None, prev=None):
    """(target, exit, dns_via) of ``gw-all`` (§2.6, С4): the best of «main»; «main» on its last step — «direct» when
    its fail is direct, else the best of another foreign group (in the order of the list, no exit with a quota), else
    «block». ``prev`` — the ``gw_all`` of the last decision: its exit of «main» that is «unknown» now and was usable
    is kept (§4.2: «unknown» as its last state — no block for the tick the observatory is silent after a restart)."""
    main = P.group(policy, P.MAIN)
    e = _best_of(main, states, metrics) if main else None
    if e:
        return exits.tag(e, "abroad"), e, P.MAIN
    gx = exits.ABROAD_PREFIX
    pe = prev[len(gx):] if isinstance(prev, str) and prev.startswith(gx) else None
    if pe and main and _tier_index(main, pe) is not None and _state(states, pe) == "unknown" and \
            _seen(states, pe) in ("ok", "degraded"):
        return prev, pe, P.MAIN
    if (main or {}).get("fail") == "direct":
        return "direct", None, "direct"
    for g in (policy or {}).get("groups") or []:
        if not isinstance(g, dict) or g.get("id") == P.MAIN or g.get("role", "abroad") != "abroad":
            continue
        e = _best_of(g, states, metrics, skip_quota=True)
        if e:
            return exits.tag(e, "abroad"), e, g["id"]
    return "block", None, "none"


def assign(devices_by_group, states, policy, pins, metrics, node, now):
    """One decision for every group (§4.4). ``node.offline`` — the pins and ``gw_all`` as they were in every group;
    ``node.ru_offline`` — in the Russian groups."""
    node = node or {}
    pins = pins if isinstance(pins, dict) else {}
    old_groups = pins.get("groups") if isinstance(pins.get("groups"), dict) else {}
    out = {"version": 1, "groups": {}, "states": dict((e, _state(states, e)) for e in (states or {})), "moves": [],
           "gw_all": pins.get("gw_all"), "gw_all_exit": None, "dns_via": None, "events": []}
    for grp in (policy or {}).get("groups") or []:
        if not isinstance(grp, dict):
            continue
        gid = grp.get("id")
        gp = _clamp_group(old_groups.get(gid), now)
        frozen = node.get("offline") or (node.get("ru_offline") and grp.get("role") == "ru")
        if frozen and gid in (node.get("regroup") or ()):
            out["events"].append(regrouped(gid, grp, 0, "offline" if node.get("offline") else "ru_offline"))
        if frozen:
            # SEC-s1-q2 С-3: frozen — the pins as they were, but never one on an exit that is no longer in this group
            # (moved to another group meanwhile: a Russian one is somebody else's home router)
            for dev, p in list(gp["pins"].items()):
                if _tier_index(grp, p.get("exit")) is None:
                    del gp["pins"][dev]
                    gp["steps"].pop(dev, None)
        else:
            devs = [d if isinstance(d, dict) else {"id": d} for d in (devices_by_group or {}).get(gid) or []]
            _group(gid, grp, devs, states, metrics, gp, node, now, out["moves"], out["events"])
        # the rows of «strength» of each tier (PLAN-order Ш2): in memory only — live.json shows them (``_clamp_group``
        # never carries them over, pins.json never keeps them: ``collect._pins_doc``, ``pins.normalize``)
        per_conn = bool(grp.get("per_connection")) and gid == P.MAIN
        gp["ranks"] = dict((t["id"], rows(t, states, metrics, per_conn)) for t in _tiers(grp)
                           if isinstance(t.get("id"), str))
        out["groups"][gid] = gp
    if not node.get("offline"):
        tgt, e, via = gw_all(policy, states, metrics, pins.get("gw_all"))
        out["gw_all"], out["gw_all_exit"], out["dns_via"] = tgt, e, via
        for ev in out["events"]:
            if ev["kind"] == "group_last_step" and ev["group"] == P.MAIN:
                ev["dns_via"] = via if via != P.MAIN else "none"
    return out
