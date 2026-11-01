"""The state of the quick setup: /etc/kaskad/setup.json (0600) — docs/k3/SPEC-setup.md §2.

    {"schema": 1,
     "status": "off" | "active" | "finished",
     "started", "updated", "finished_at",                       ISO UTC
     "link": {"secret": "<32 знака>", "expires": <epoch>, "bind": bool, "bound_ip": str|null},
     "tokens": [{"hash": sha256, "created", "expires", "used": epoch|null, "revoked": bool}],
     "password_set": bool,                                      the stage «Пароль» done: tokens burn, only the password
     "mode": "node" | "classic" | null,
     "current": "welcome" | <kind of the rail>,
     "stages": {<kind>: "done" | "skipped"},
     "answers": {...},                                          what a person chose (never a secret)
     "steps": {<step>: {"state", "at", "text", "data"}}}         the last result of each step (never a secret)

The secret path is kept in clear (the server compares it; 0600, root only — like the path of the panel behind nginx);
tokens only as SHA-256. The file is changed under its own lock (/etc/kaskad/setup.lock): the panel, the job of a
step and the SSH menu write it.
"""

import contextlib
import copy
import fcntl
import json
import os
import time

from .. import paths, util
from ..i18n import tr
from . import product

SCHEMA = 1
TTL = 24 * 3600                       # the link (and the wizard) lives at most a day from the last «setup link»

_now = time.time

DEFAULT = {"schema": SCHEMA, "status": "off", "started": None, "updated": None, "finished_at": None,
           "link": {"secret": "", "expires": 0, "bind": False, "bound_ip": None},
           "tokens": [], "password_set": False, "mode": None, "current": "welcome", "stages": {}, "answers": {},
           "steps": {}}


def path():
    return paths.P("/etc/kaskad/setup.json")


def lock_path():
    return paths.P("/etc/kaskad/setup.lock")


def load():
    raw = util.read_text(path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    out = copy.deepcopy(DEFAULT)
    for k, v in doc.items():
        if k in out and isinstance(v, type(out[k])) or (k in out and out[k] is None):
            out[k] = v
    if not isinstance(out["link"], dict):
        out["link"] = copy.deepcopy(DEFAULT["link"])
    for k, v in DEFAULT["link"].items():
        out["link"].setdefault(k, v)
    out["tokens"] = [t for t in out["tokens"] if isinstance(t, dict) and isinstance(t.get("hash"), str)]
    if out["status"] not in ("off", "active", "finished"):
        out["status"] = "off"
    return out


def save(doc):
    doc["updated"] = util.now_iso()
    util.ensure_dir(paths.etc(), 0o700)
    util.atomic_write(path(), util.json_text(doc), 0o600)


@contextlib.contextmanager
def locked():
    util.ensure_dir(paths.etc(), 0o700)
    fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def update(fn):
    """``fn(doc)`` under the lock, then saved. -> what ``fn`` returned."""
    with locked():
        doc = load()
        out = fn(doc)
        save(doc)
        return out


def active(doc=None, now=None):
    doc = load() if doc is None else doc
    now = _now() if now is None else now
    return doc["status"] == "active" and bool(doc["link"].get("secret")) and now < float(doc["link"]["expires"] or 0)


def expired(doc=None, now=None):
    doc = load() if doc is None else doc
    now = _now() if now is None else now
    return doc["status"] == "active" and now >= float(doc["link"]["expires"] or 0)


def mode(doc):
    return doc.get("mode") if doc.get("mode") in product.RAIL else product.DEFAULT_MODE


def rail(doc):
    """The stages for the page: [{"kind", "title", "sub", "min", "n", "state": done | current | ahead | skipped}],
    with the totals («Шаг 2 из 6 · ≈ 5 минут осталось»)."""
    kinds = product.RAIL[mode(doc)]
    cur = doc.get("current") or "welcome"
    rows = []
    for n, k in enumerate(kinds, 1):
        st = doc["stages"].get(k)
        if k == cur:
            state = "current"
        elif st in ("done", "skipped"):
            state = st
        else:
            state = "ahead"
        rows.append(dict(product.stage_doc(k), n=n, state=state))
    idx = kinds.index(cur) if cur in kinds else -1
    left = sum(r["min"] for r in rows[max(idx, 0):] if r["state"] != "done") if idx >= 0 else \
        sum(r["min"] for r in rows if r["state"] != "done")
    rest = rows[max(idx, 0):] if idx >= 0 else rows
    lo = sum(r["range"][0] for r in rest if r["state"] != "done")
    hi = sum(r["range"][1] for r in rest if r["state"] != "done")
    welcome = dict(product.stage_doc("welcome"), n=0,
                   state="current" if cur == "welcome" else ("done" if doc["password_set"] else "ahead"))
    return {"welcome": welcome, "stages": rows, "current": cur, "position": idx + 1 if idx >= 0 else 0,
            "total": len(rows), "left_min": left, "left": [lo, hi]}


def set_stage(doc, kind, action):
    """``action``: "open" (make it current; only a stage that is done, skipped, current or the next one), "done"
    (mark and go on), "skip" (mark skipped and go on). Returns the new current kind; ValueError when not allowed."""
    kinds = product.RAIL[mode(doc)]
    if kind == "welcome":
        if action != "open":
            raise ValueError(tr("setup.model.welcomeNoSkip"))
        doc["current"] = "welcome"
        return "welcome"
    if kind not in kinds:
        raise ValueError(tr("setup.model.noSuchStage", kind=kind))
    if not doc["password_set"]:
        raise ValueError(tr("setup.model.passwordFirst"))
    if action == "open":
        cur = doc.get("current")
        reachable = doc["stages"].get(kind) in ("done", "skipped") or kind == cur or _next(kinds, doc) == kind
        if not reachable:
            raise ValueError(tr("setup.model.stageAhead"))
        doc["current"] = kind
        return kind
    if action not in ("done", "skip"):
        raise ValueError(tr("setup.model.badAction"))
    if kind == "done" and action == "skip":
        raise ValueError(tr("setup.model.lastNoSkip"))
    doc["stages"][kind] = "done" if action == "done" else "skipped"
    nxt = _next(kinds, doc, after=kind)
    doc["current"] = nxt or kind
    return doc["current"]


def _next(kinds, doc, after=None):
    """The first stage after ``after`` (or from the start) that is neither done nor skipped."""
    start = kinds.index(after) + 1 if after in kinds else 0
    for k in kinds[start:]:
        if doc["stages"].get(k) not in ("done", "skipped"):
            return k
    return None


def record_step(doc, name, res):
    """The last result of a step, without anything secret (the step itself decides what goes to ``data``)."""
    doc["steps"][name] = {"state": res.get("state"), "at": util.now_iso(), "text": util.redact(res.get("text") or ""),
                          "data": res.get("keep") or {}}


def public_view(doc, now=None):
    """What the page may see of the state: no secret, no token hashes."""
    now = _now() if now is None else now
    return {"status": doc["status"], "active": active(doc, now), "expires": int(doc["link"]["expires"] or 0),
            "expires_in": max(0, int(float(doc["link"]["expires"] or 0) - now)), "bind": bool(doc["link"]["bind"]),
            "password_set": bool(doc["password_set"]), "mode": doc.get("mode"), "rail": rail(doc),
            "answers": doc["answers"], "steps": doc["steps"], "started": doc["started"], "updated": doc["updated"]}
