"""D-136 (B7 круг 2): the last line of «Россия напрямую» — at the point where a direct path is drawn (the config of xray,
the sets gw_rd / gw_ruonly of nft), a direct path to Russia while it is not allowed on the node (``rfgate.allowed``:
no consent ⚠РФ, no mark of an updated node) is never applied, whatever code before it decided otherwise. The point
that caught it closes (xray: ``xray.ru_guard`` → nothing direct is wanted; nft: the sets empty) and writes a note
here; the collector tells the owner once (the event ``ru_guard``, always sent by the bot; the same trip again at
most hourly).

The main proof is behavioural (tests/gw/test_ru_invariant.py: hundreds of random states through the real renderers);
this module is the same rule at run time. It never asks a profile: only the permission of the node."""

import json
import os
import time

from .. import paths, util


def note_path():
    return os.path.join(paths.gw_run_dir(), "ru-guard.json")


def allowed(st):
    """«Россия напрямую» may apply on this node at all (the consent ⚠РФ, the mark of an updated node)."""
    from ..rules import rfgate
    return bool(rfgate.allowed(st))


TELL_S = 3600          # the same trip (where, n) told again at most hourly (REV-s1-b7-2 №1: not every tick)
WHERE = ("xray", "nft")


def _notes():
    """{where: {"n", "at", "told", "told_at"}} — one record per point (xray and nft trip on the same tick, each its
    own).
    The note of круг 2 ({"where", "n", "at", "told"}) is read as its one record."""
    try:
        with open(note_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    if "where" in doc:
        doc = {str(doc.get("where")): doc}
    return dict((w, r) for w, r in doc.items() if w in WHERE and isinstance(r, dict))


def _save(notes):
    try:
        util.ensure_run_dir(paths.gw_run_dir())
        util.atomic_write(note_path(), util.json_text(notes), 0o600)
    except OSError:
        pass


def trip(where, n, now=None):
    """A direct path without the permission was drawn and dropped at ``where`` ("xray" | "nft"): journal + the note
    for the collector. The same trip (``where`` and ``n``) told less than TELL_S ago stays told: a cause that does not
    go trips on every tick (the collector draws again every 10 s, nft on every plan) — one event, then hourly
    (REV-s1-b7-2 №1). Never raises."""
    now = time.time() if now is None else now
    notes = _notes()
    prev = notes.get(where) or {}
    at = prev.get("told_at")
    if prev.get("told") and prev.get("n") == int(n) and isinstance(at, (int, float)) and not isinstance(at, bool) \
            and 0 <= now - at < TELL_S:
        return
    util.log_tr("gw_xray.log.ru_guard", where=where, n=n)
    notes[where] = {"n": int(n), "at": now}
    _save(notes)


def tripped():
    """The first note not told yet: {"where", "n", "at"}, else None."""
    notes = _notes()
    for w in WHERE:
        r = notes.get(w)
        if r is not None and not r.get("told"):
            return dict(r, where=w)
    return None


def told(doc, now=None):
    """The collector told the owner: the note stays (journal of what happened) but is not told again — the same trip
    within TELL_S neither (``trip``)."""
    notes = _notes()
    w = doc.get("where")
    if w not in WHERE:
        return
    rec = dict(doc)
    rec.pop("where", None)
    notes[w] = dict(rec, told=True, told_at=time.time() if now is None else now)
    _save(notes)


def rd(st, ips):
    """gw_rd / gw_ruonly as drawn -> what may go into the table: [] when «Россия напрямую» is not allowed (tripped)."""
    ips = list(ips or [])
    if ips and not allowed(st):
        trip("nft", len(ips))
        return []
    return ips
