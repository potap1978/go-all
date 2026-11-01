"""Cheap reads of table ``ip kaskad`` for the hot paths of the gateway (F2, R-perf).

``nft -j list table ip kaskad`` prints every element of every set: with the lists «Россия напрямую» gw_ru holds
~15 k ranges — about a megabyte of JSON that nft builds and Python parses. The collector did it every round (10 s)
and ``gw check`` (the minute watchdog) once more, only to look at a few chains, three small sets and the counters
of the forwards. Here they ask nft for exactly that::

    nft -j list chains ip                       the chains of table ip kaskad: names and hooks, no rules
    nft -j list counters table ip kaskad        the forwards' counters (live.json)
    nft -j list map ip kaskad gw_route          the sets/maps a caller compares, one call each: tens of
    nft -j list set ip kaskad gw_live           elements (the clients), never gw_ru
    nft -j list set ip kaskad gw_rd

The result has the shape of ``nft.read_live`` (``nft.parse_live``) restricted to what was read: ``chain_info``
and ``chains`` (no ``rules``), ``counters``, ``set_info``/``sets`` of the asked sets that exist. Enough for
``net.part_present`` and ``net.update_map``; NOT for ``nft.verify`` (rules, gw_ru's type and version) — the
watchdog (netcheck) and the applies keep the full read. None when the table does not exist (as read_live).
A set that is missing is simply not in ``set_info`` (``update_map`` and ``part_present`` read that as before).
"""

import json

from .. import nft, util
from ..errors import KaskadError
from ..i18n import tr
from . import net

HOT_SETS = (net.MAP, net.LIVE, net.RD, net.RUONLY, net.XPORTS)    # what the collector compares every round (stage 1:
# gw_ruonly and gw_xports — net.update_map refills them only when it sees them)
RX_SETS = HOT_SETS + (net.RX,)      # T15 Q2: with a Russian group — gw_rx too (no extra call on a node without one)


def _absent(res):
    return res.rc not in (0, 127) and "No such file or directory" in ((res.err or "") + (res.out or ""))


def _items(runner, args):
    """``nft -j list <args>`` -> its objects; None when the object (or its table) does not exist. KaskadError
    when nft fails otherwise or prints no JSON (as nft.read_live)."""
    res = runner.run([nft.NFT, "-j", "list"] + list(args))
    if not res.ok:
        if _absent(res):
            return None
        detail = tr("gw_peek.no_nft") if res.rc == 127 else util.redact((res.err or res.out or "").strip())
        raise KaskadError(tr("gw_peek.read_failed", what=" ".join(args), detail=detail[:300]))
    try:
        doc = json.loads(res.out)
    except ValueError as e:
        raise KaskadError(tr("gw_peek.not_json", err=e))
    items = doc.get("nftables") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise KaskadError(tr("gw_peek.bad_format"))
    return [it for it in items if isinstance(it, dict)]


def _ours(obj):
    return isinstance(obj, dict) and obj.get("table", nft.TABLE_NAME) == nft.TABLE_NAME \
        and obj.get("family", nft.TABLE_FAMILY) == nft.TABLE_FAMILY


def read(runner, sets=HOT_SETS, counters=True):
    """The part of the live table the hot paths use (module notes) — ``2 + len(sets)`` small calls of nft, never
    the elements of gw_ru. None when table ip kaskad does not exist."""
    chains = [it for it in (_items(runner, ["chains", nft.TABLE_FAMILY]) or []) if _ours(it.get("chain"))]
    items = list(chains)
    if counters:
        got = _items(runner, ["counters", "table", nft.TABLE_FAMILY, nft.TABLE_NAME])
        if got is None and not chains:
            return None                                          # no table at all
        items += [it for it in got or [] if _ours(it.get("counter"))]
    found = bool(chains)
    for name in sets:
        kind = "map" if name in (net.MAP, net.RX) else "set"
        got = _items(runner, [kind, nft.TABLE_FAMILY, nft.TABLE_NAME, name])
        if got is None:
            continue
        found = True
        items += [it for it in got if _ours(it.get(kind))]
    if not counters and not found:
        return None
    return nft.parse_live({"nftables": items})


def part_present(runner):
    """``net.part_present`` of the live table from two small calls (the chains, gw_live) — ``gw check``."""
    return net.part_present(read(runner, sets=(net.LIVE,), counters=False))
