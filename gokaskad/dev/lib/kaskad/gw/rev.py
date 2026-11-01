"""The version of the exit policy — ``gateway.policy_rev`` (SPEC-stage1-exits §3.1 «Кто поднимает rev», §7.9; T1).

The panel, CLI and MCP send the ``rev`` they read; a write with an older one gets 409 (T7). ``policy_rev`` grows by 1
when the FINGERPRINT changes — what the forms and the buttons over an object change:

* the ids of the exits of BOTH files of the pool (``upstream.txt`` and ``upstream-ru.txt``; the second one — T15), in
  the order of their lines, without names;
* ``exit_policy`` without ``hint_seen``, without the names of the groups and tiers and without ``probes.speed`` (the
  speed test has its own ``ver``, it never changes the path of the traffic);
* ``exit_pref`` (``gateway.exits[*]``) — only ``enabled``, ``manual``, ``manual_why``, ``speed_skip``, ``quota`` (no
  ``name``, no hint ``group``, no ``country``).

Names, routes and ``hint_seen`` are outside it. ``gateway.policy_sig`` — the fingerprint last counted.

Three entries (the state lock never nests: ``state.locked`` -> NestedLockError):

* ``note(st)`` — INSIDE a lock already taken (``state.transaction``): a new fingerprint -> ``policy_rev`` + 1 and the new
  ``policy_sig`` written into the same ``st`` (the caller's transaction saves it). Takes no lock. -> rev.
* ``current(st)`` — OUTSIDE the lock (the reads): the fingerprint is compared without the lock; the same -> (rev, st);
  another -> its own ``state.transaction`` with ``note`` on the fresh state -> (rev, fresh st). Never under the lock.
* ``bump(st, before=None)`` — the restore of a ``.kbk`` (``backup``): ``policy_rev`` = max(this node's, the copy's) + 1,
  never the copy's alone; inside the caller's lock, like ``note``.

A node of 3.3.x has neither field: ``policy_rev`` = 0, no fingerprint — the first count gives 1.

``seed(st)`` — ``gw purge`` with the people (and so the exact restore of a copy without the gateway) empties
``gateway``: the count then starts not from 0 but from a random 31-bit number kept OUTSIDE ``gateway`` (``st[BASE_KEY]``,
not in a ``.kbk``) — a tab opened before the purge never passes the check by chance (REV-s1-t1 З-6, decision of the
coordinator 15.10; SPEC-stage1-exits §3.1).
"""

import hashlib
import json
import os
import random

from .. import paths, state, util
from ..errors import NestedLockError
from ..i18n import tr

# the Russian pool (T15): paths.egress_up_ru (Q4; SPEC-stage1-exits §10.3, «gw/rev.py»)
RU_POOL_NAME = "upstream-ru.txt"
PREF_KEYS = ("enabled", "manual", "manual_why", "speed_skip", "quota")
# the defaults of exit_pref (SPEC-stage1-exits §3.1, the record of an exit): a field written equal to its default counts
# as absent — writing «no change» explicitly (manual: null, speed_skip: false …) never raises the rev (REV-s1-t1 З-3)
PREF_DEFAULT = {"enabled": True, "manual": None, "manual_why": None, "speed_skip": False, "quota": None}


BASE_KEY = "policy_rev_base"              # top level of state.json: where the count goes on after gw purge
BASE_MAX = 2 ** 31 - 1


def ru_pool_path():
    return paths.egress_up_ru()


def _lines(path):
    raw = util.read_text(path)
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


def pool_ids():
    """{"abroad": [ids], "ru": [ids]} — the ids of the lines of both files of the pool, in their order (no parser)."""
    from . import exits
    rows = exits.all_line_ids()                         # the ids of ``pool``: Russian numbers 201+ (T15 Q4)
    return {"abroad": [eid for eid, _n, r in rows if r == "abroad"], "ru": [eid for eid, _n, r in rows if r == "ru"]}


def _policy(doc):
    """``exit_policy`` without what is outside the fingerprint."""
    if not isinstance(doc, dict):
        return doc
    out = dict((k, v) for k, v in doc.items() if k != "hint_seen")
    probes = out.get("probes")
    if isinstance(probes, dict):
        out["probes"] = dict((k, v) for k, v in probes.items() if k != "speed")
    groups = out.get("groups")
    if isinstance(groups, list):
        gs = []
        for g in groups:
            if isinstance(g, dict):
                g = dict((k, v) for k, v in g.items() if k not in ("name", "label"))
                tiers = g.get("tiers")
                if isinstance(tiers, list):
                    g["tiers"] = [dict((k, v) for k, v in t.items() if k not in ("name", "label"))
                                  if isinstance(t, dict) else t for t in tiers]
            gs.append(g)
        out["groups"] = gs
    return out


def _pref_value(key, p):
    """The value of ``key`` in the record ``p`` brought to one form: ``enabled`` as model.exit_pref reads it, ``speed_skip``
    a bool, an empty ``manual``/``manual_why``/``quota`` ("", {}, false) — None, the default."""
    if key == "enabled":
        return bool(p.get("enabled", True))
    v = p.get(key)
    if key == "speed_skip":
        return bool(v)
    if v is None or v is False or v == "" or v == {} or v == []:
        return None
    return v


def _prefs(exits_doc):
    """{id: {the keys of PREF_KEYS that differ from PREF_DEFAULT}} — a record equal to the default (only a name, say,
    or the defaults written explicitly) is left out."""
    out = {}
    for eid, p in sorted((exits_doc or {}).items()) if isinstance(exits_doc, dict) else []:
        if not isinstance(p, dict):
            continue
        rec = {}
        for k in PREF_KEYS:
            v = _pref_value(k, p)
            if v != PREF_DEFAULT[k]:
                rec[k] = v
        if rec:
            out[str(eid)] = rec
    return out


def fingerprint(st, ids=None):
    """The fingerprint of ``st`` (sha256 of the canonical JSON); ``ids`` — ``pool_ids()`` read before, else now."""
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    g = g if isinstance(g, dict) else {}
    doc = {"pool": ids if ids is not None else pool_ids(),
           "policy": _policy(g.get("exit_policy")),
           "exits": _prefs(g.get("exits"))}
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def rev_of(st):
    """``policy_rev`` of ``st``; none — the base left by ``gw purge`` (``seed``); neither — 0 (a node of 3.3.x)."""
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    v = _count(g.get("policy_rev") if isinstance(g, dict) else None)
    return v or (_count(st.get(BASE_KEY)) if isinstance(st, dict) else 0)


def seed(st, rng=None):
    """``gw purge`` with the people, inside its lock: the next count starts from a random 31-bit number, not from 0
    (kept at the top level of ``st``: the empty ``gateway`` stays empty). -> the base."""
    base = (rng or random.SystemRandom()).randint(1, BASE_MAX - 1)
    st[BASE_KEY] = base
    return base


def _set(st, rev, sig):
    g = _gw(st)
    g["policy_rev"] = rev
    g["policy_sig"] = sig
    st.pop(BASE_KEY, None)                   # the gateway has its own count now


def _sig_of(st):
    g = (st or {}).get("gateway") if isinstance(st, dict) else None
    return str((g or {}).get("policy_sig") or "") if isinstance(g, dict) else ""


def _gw(st):
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = st["gateway"] = {}
    return g


def note(st):
    """Inside a lock already taken: count a changed fingerprint into ``st`` (``policy_rev`` + 1, ``policy_sig``).
    Takes no lock; the caller's transaction saves ``st``. -> ``policy_rev``."""
    sig = fingerprint(st)
    rev = rev_of(st)
    if sig != _sig_of(st):
        rev += 1
        _set(st, rev, sig)
    return rev


def current(st):
    """Outside the lock (a read): -> (rev, st) — the version and the data of ONE read. The fingerprint the same — the
    ``st`` given; another — counted in its own ``state.transaction`` on the fresh state, and that fresh ``st``. Called
    under the lock it is an error of the code: NestedLockError at once (SPEC-stage1-exits §3.1)."""
    if state.held():
        raise NestedLockError(tr("state.nested_lock", lock=paths.lock()))
    if fingerprint(st) == _sig_of(st) and _sig_of(st):
        return rev_of(st), st
    with state.transaction() as fresh:
        rev = note(fresh)
    return rev, fresh


def bump(st, before=None):
    """The restore of a copy (``backup``), inside the caller's lock: ``policy_rev`` = max(the node's before the restore —
    ``before``, a state dict or a number; the one in ``st`` — of the copy) + 1, and the fingerprint of ``st`` — the next
    count adds nothing for the restore itself. A copy never brings its own ``rev`` back: a form opened before the
    restore gets 409. -> ``policy_rev``."""
    had = before if isinstance(before, int) and not isinstance(before, bool) else rev_of(before)
    rev = max(max(had, 0), rev_of(st)) + 1
    _set(st, rev, fingerprint(st))
    return rev


def resign(st):
    """Inside a lock already taken, right after ``note``: the fingerprint of ``st`` taken as the one counted, ``rev``
    unchanged — only for a change of the FORM of the policy that the readers already saw so (the first policy of an
    updated node written as the view «Как раньше» with every exit placed; ``policy_ops._base_w``, проверка T7, С-1/П-2).
    -> ``policy_rev``."""
    rev = rev_of(st)
    _set(st, rev, fingerprint(st))
    return rev
