"""The push «a check of the server ended» (``bench_done``, SPEC-node-score §5 B, §6.2 B7; круг 2: З-4, П-1).

* A check that ended — ``ok``/``partial``/``failed`` of any ``by`` but ``auto`` (an automatic repeat has its own news,
  ``bench_drop``). Its words (``gw.live.bench_key``/``bench_args``): the end with «N от обычного» (``rel_total`` of
  the node after the check, NS-D31, ``with_rel`` = ``hostscore.node_doc``) or without a number, «не все тесты прошли»
  of a partial check, the failure with its reason. No absolute score, band or verdict (NS-D34).
* The mark ``/var/lib/kaskad/bench/done-push.json`` = ``{"ts", "id"}`` (0600, atomic) — «the bot decided about this
  check and every one before it» (the checks never overlap: ``run.start`` + the flock of the runner, so their ``ts``
  go in order). ONLY the bot writes it, after the push (or the switch off — decided too). The mark only grows.
* With the gateway the collector gives the event when it takes the check from the queue (``spool.drain``) and only
  when the check is later than the mark: a file of the queue that could not be removed is taken again every minute,
  the bot has written the mark by then — no second push (П-1). Without the gateway (the same path as the push of the
  skips, B11): the monitor of the bot reads the queue itself (``bench.history``) — the checks later than the mark
  that ended within ``MAX_AGE`` (``to_tell``); no mark — only the last ``MAX_AGE`` is news (an update never pushes
  the old checks).
"""

import json
import os
import time

from .. import paths, util

from .spool import DONE_STATUS as DONE      # ok / partial / failed

KIND = "bench_done"
MAX_AGE = 3600                       # older — no news (the bot was down, the gateway was removed)


def path():
    return os.path.join(paths.bench_dir(), "done-push.json")


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def mark(now=None):
    """(ts, id) of the mark, or None — none or broken. A mark later than tomorrow (the clock ran ahead once) is no mark:
    it would silence the push till then (as skipday.mark, SEC П-2)."""
    try:
        doc = json.loads(util.read_text(path()) or "{}")
    except (ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or _int(doc.get("ts")) is None or not isinstance(doc.get("id"), str):
        return None
    now = time.time() if now is None else now
    return None if doc["ts"] > now + 86400 else (doc["ts"], doc["id"])


def told(ts, rid, m=None):
    """Is the check (``ts``, ``rid``) decided already (not later than the mark)? A check without ``ts`` — yes."""
    if _int(ts) is None:
        return True
    m = mark() if m is None else m
    return m is not None and (ts, str(rid or "")) <= m


def write(ts, rid):
    """The mark — only when the check is later than the written one (it only grows). -> written."""
    if told(ts, rid):
        return False
    util.ensure_dir(paths.bench_dir(), 0o700)
    util.atomic_write(path(), json.dumps({"ts": ts, "id": str(rid or "")}, sort_keys=True), 0o600)
    return True


def ended(r):
    """A row of the history (or a run of the queue) that gives the news: it ended, not an automatic repeat."""
    return r.get("status") in DONE and r.get("by") != "auto" and _int(r.get("ts")) is not None


def fresh(r, now=None):
    """The check ended within ``MAX_AGE`` (``end_ts``, else ``ts``): older — no news on any path (REV-ns-b7-2 З-7)."""
    now = time.time() if now is None else now
    end = _int(r.get("end_ts")) or _int(r.get("ts"))
    return end is not None and now - end <= MAX_AGE


def args_of(r):
    """The args of the event of a check: {ts, id, mode, by, status, why} (``rel_total`` — ``with_rel``)."""
    return {"ts": r.get("ts"), "id": r.get("id"), "mode": r.get("mode"), "by": r.get("by"),
            "status": r.get("status"), "why": r.get("why")}


def with_rel(a):
    """``a`` + ``rel`` — ``summary.rel_total`` of the node after the check (100 — as usual for this node, NS-D31;
    None — too little history) + ``worse`` (``worse_of``: a confirmed ▼ after the check — the push «Сервер заметно
    хуже, чем обычно», the design r1, «Сверка 24.10» п. 6). Not read — raises (the caller says the news without it)."""
    from . import hostscore
    doc = hostscore.node_doc()
    out = dict(a, rel=doc["summary"].get("rel_total"))
    w = worse_of(doc)
    if w is not None:
        out["worse"] = w
    return out


def _fin(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf") \
        else None


def worse_of(doc):
    """The first link of ``doc`` (``hostscore.score_doc``; one-test links before groups) with a confirmed ▼ (``dev`` =
    down, NS-D25 «two»):
    {"link", "test", "now", "usual"} (the numbers of its hint; None — the hint has none), or None."""
    from .. import score as S
    tests = S.tests_of(S.SPEC)
    links = [lk for lk in doc.get("links") or () if isinstance(lk, dict)]
    # a link of one test first («Скачивание» rather than its group «Канал»), then the groups, in the order of the screen
    links.sort(key=lambda lk: lk.get("id") in GROUPS)
    for lk in links:
        if lk.get("dev") != "down" or lk.get("id") not in LINKS:
            continue
        hint = lk.get("hint") if isinstance(lk.get("hint"), dict) else {}
        test = hint.get("test") if hint.get("test") in tests else None
        now, usual = _fin(hint.get("now")), _fin(hint.get("usual"))
        if test is None or now is None or usual is None:
            test = now = usual = None
        return {"link": lk["id"], "test": test, "now": now, "usual": usual}
    return None


LINKS = ("net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto")     # score.SPEC links (score.link.*)
GROUPS = ("net_bw", "net_q", "cpu", "stab")


def to_tell(runs, now=None):
    """Without the gateway: the checks of ``runs`` (rows of ``history.runs``) that ended within ``MAX_AGE`` and are
    later than the mark, oldest first."""
    now = time.time() if now is None else now
    m = mark(now)
    out = []
    for r in runs:
        if not ended(r):
            continue
        if not fresh(r, now) or told(r["ts"], r.get("id"), m or (0, "")):
            continue
        out.append(r)
    out.sort(key=lambda r: (r["ts"], str(r.get("id") or "")))
    return out
