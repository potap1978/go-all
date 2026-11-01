"""/etc/kaskad/state.json — the single source of truth of Kaskad 3.

Schema 1 (docs/k3/SPEC-wave1.md, «state.json»)::

    {
      "schema": 1,
      "engine": "legacy" | "nft",
      "created": "2026-10-04T12:00:00Z", "updated": "...",
      "forwards": [ {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443,
                     "name": "", "origin": "k3|pro|pro-legacy|free", "added": "..."} ],
      "migrations": [ {"id": "...", "at": "...", "from": {"edition": "...", "version": "..."},
                       "to": "3.0.1", "backup": "/var/backups/kaskad/...", "result": "ok|rolled_back|failed",
                       "imported": 3, "skipped": 1} ],
      "gateway": {},
      "gen": 2
    }

``gen`` — the generation of the file (SPEC-stage1-exits §1.3): ``empty()`` of stage 1 and newer writes GEN (2); a file
written by the code before stage 1 has no field and reads as 1 — an UPDATED node, never a new one. ``born_new(st)``
is the one test «new or updated node» (stage 1: «Стандарт» or «Как раньше», the defaults of the rules). Nothing writes
it on an update: ``load`` sets 1 before the missing keys are added and ``save`` keeps it as it is.

The file is written only by this module (atomic rename, mode 0600, ``util.json_text`` style,
so bash can test the engine with ``grep -q '"engine": "nft"'``). Readers that change it must hold
the lock: ``with state.transaction() as st: ...``.
"""

import contextlib
import errno
import fcntl
import ipaddress
import os
import re
import threading
import time

from . import paths, util
from .errors import LockedError, NestedLockError, StateError
from .i18n import tr

SCHEMA = 1
GEN = 2                        # the generation a file born on the code of stage 1 gets (SPEC-stage1-exits §1.3)
ENGINES = ("legacy", "nft")
PROTOS = ("tcp", "udp")
ORIGINS = ("k3", "pro", "pro-legacy", "free")

# ct mark of connections DNATed by Kaskad (top byte "K"); the iptables compat rule matches it.
MARK = 0x4B000000
MARK_MASK = 0xFF000000

_NAME_MAX = 64
_CTRL = re.compile(r"[\x00-\x1f\x7f|]")


def empty():
    t = util.now_iso()
    return {"schema": SCHEMA, "engine": "legacy", "created": t, "updated": t,
            "forwards": [], "migrations": [], "gateway": {}, "gen": GEN}


def born_new(st):
    """A NEW node unless ``gen`` is exactly the integer 1 (an UPDATED one). The only test «new or updated node»
    (SPEC-stage1-exits §1.3). A file without ``gen`` (3.3.3, 3.3.4, 3.3.5, a move from 2.x) is an updated one because
    ``load`` sets 1 for it; a dict that did not come through ``load`` without the field, or a broken field (``"1"``,
    ``true``, ``1.0``, ``0``), is a NEW node — the safe side for ⚠РФ (SEC-s1-r2 З1): no «not migrated yet» legacy,
    no ``rf_legacy``, the default ``all_exit``."""
    g = st.get("gen") if isinstance(st, dict) else None
    return not (isinstance(g, int) and not isinstance(g, bool) and g == 1)


def as_loaded(doc):
    """``doc`` (state.json read PAST ``load``: the read cache of the panel, the bot, the rules page) with ``gen`` as
    ``load`` sets it: no key — 1, an UPDATED node (D-135: a file without ``gen`` on disk is 3.3.x, i.e. updated).
    A shallow copy when the key is added — the cached object is never changed; a present field (even a broken one)
    is kept, so ``born_new`` reads it exactly as after ``load``. Not a dict — returned as is."""
    if not isinstance(doc, dict) or "gen" in doc:
        return doc
    out = dict(doc)
    out["gen"] = 1
    return out


# ── validation ─────────────────────────────────────────────────────────────

def parse_port(value, what=None):
    what = what or tr("state.port")
    try:
        p = int(str(value).strip())
    except (TypeError, ValueError):
        raise StateError(tr("state.bad_port", what=what, value=repr(value)))
    if not 1 <= p <= 65535:
        raise StateError(tr("state.bad_port", what=what, value=repr(value)))
    return p


def parse_proto(value):
    p = str(value or "").strip().lower()
    if p not in PROTOS:
        raise StateError(tr("state.bad_proto", value=repr(value)))
    return p


def parse_target_ip(value):
    """IPv4 address a forward may point to. Hostnames, 0.0.0.0, loopback, multicast,
    broadcast, link-local and 240/4 are refused: DNAT from PREROUTING cannot reach them."""
    s = str(value or "").strip()
    try:
        ip = ipaddress.IPv4Address(s)
    except ValueError:
        raise StateError(tr("state.bad_ip", value=repr(value)))
    if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_reserved \
            or s == "255.255.255.255":
        raise StateError(tr("state.ip_not_allowed", ip=s))
    return str(ip)


def clean_name(value):
    s = _CTRL.sub(" ", str(value or "")).strip()
    return s[:_NAME_MAX]


def normalize_forward(f, default_origin="k3", stamp=False):
    """Validated copy of a forward record with all fields present and canonical types. ``added``: as recorded, ""
    when it never was (a record of an old version) — a read never invents a time (it changed on every load); ``stamp``
    — a record being written now (a new forward, an import) gets the time of now when it has none."""
    if not isinstance(f, dict):
        raise StateError(tr("state.fwd_not_object", value=repr(f)))
    origin = str(f.get("origin") or default_origin)
    if origin not in ORIGINS:
        raise StateError(tr("state.bad_origin", origin=repr(origin)))
    return {
        "proto": parse_proto(f.get("proto")),
        "port": parse_port(f.get("port"), tr("state.port_in")),
        "to": parse_target_ip(f.get("to")),
        "to_port": parse_port(f.get("to_port"), tr("state.port_to")),
        "name": clean_name(f.get("name")),
        "origin": origin,
        "added": str(f.get("added") or (util.now_iso() if stamp else "")),
    }


def fwd_key(proto, port):
    return "%s:%d" % (parse_proto(proto), parse_port(port))


# ── load / save ────────────────────────────────────────────────────────────

def load(path=None):
    """State from disk; a missing file gives ``empty()``. A broken file raises StateError and is
    never overwritten silently."""
    path = path or paths.state()
    raw = util.read_text(path)
    if raw is None:
        return empty()
    try:
        st = util.json_kept(raw)       # ±Infinity, 1e400, an integer of 400 digits of an older version -> ±NUM_MAX (С-1)
    except ValueError as e:
        raise StateError(tr("state.broken", path=path, error=e))
    if not isinstance(st, dict):
        raise StateError(tr("state.not_object", path=path))
    if st.get("schema") != SCHEMA:
        raise StateError(tr("state.bad_schema", path=path, schema=repr(st.get("schema")), need=SCHEMA))
    st.setdefault("gen", 1)            # a file of the code before stage 1: generation 1 — BEFORE the keys of empty()
    base = empty()
    for k, v in base.items():
        st.setdefault(k, v)
    if st["engine"] not in ENGINES:
        raise StateError(tr("state.bad_engine", path=path, engine=repr(st["engine"])))
    seen = set()
    fwds = []
    for f in st["forwards"]:
        nf = normalize_forward(f)
        k = fwd_key(nf["proto"], nf["port"])
        if k in seen:
            raise StateError(tr("state.fwd_twice", path=path, fwd=k))
        seen.add(k)
        fwds.append(nf)
    st["forwards"] = sort_forwards(fwds)
    return st


def save(st, path=None):
    path = path or paths.state()
    st["updated"] = util.now_iso()
    st["forwards"] = sort_forwards(st.get("forwards", []))
    util.atomic_write(path, util.json_text(st), 0o600)


def sort_forwards(fwds):
    return sorted(fwds, key=lambda f: (f["port"], f["proto"]))


def exists(path=None):
    return os.path.exists(path or paths.state())


def engine(path=None):
    """'legacy' or 'nft' — cheap read for callers that only need the engine."""
    try:
        return load(path)["engine"]
    except StateError:
        return "legacy"


# ── locking ────────────────────────────────────────────────────────────────

_HELD = threading.local()      # the thread that holds the lock (the panel is a ThreadingHTTPServer): its pid


def held():
    """This thread holds the state lock now (``locked``/``transaction``)."""
    return getattr(_HELD, "pid", None) == os.getpid()


@contextlib.contextmanager
def locked(timeout=15.0):
    """Exclusive lock for read-modify-write of the state (and of the live table). Taken again by the thread that holds
    it — NestedLockError at once: flock of a second descriptor in one process never passes, the wait would be 15 s for
    nothing (SPEC-stage1-exits §3.1). A forked child is not that thread (the pid is compared)."""
    lp = paths.lock()
    if held():
        raise NestedLockError(tr("state.nested_lock", lock=lp))
    util.ensure_run_dir(os.path.dirname(lp))        # /run/kaskad: 0711, never 0700 (nginx → panel socket)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.time() >= deadline:
                    raise LockedError(tr("state.locked", lock=lp))
                time.sleep(0.1)
        _HELD.pid = os.getpid()
        try:
            yield
        finally:
            _HELD.pid = None
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextlib.contextmanager
def transaction(timeout=15.0):
    """``with transaction() as st:`` — load under the lock, save if the block finishes without an
    exception and the state changed. On exception nothing is written."""
    with locked(timeout):
        st = load()
        before = util.json_text(st)
        yield st
        if util.json_text(st) != before:
            save(st)


# ── forwards ───────────────────────────────────────────────────────────────

def find_forward(st, proto, port):
    k = fwd_key(proto, port)
    for f in st["forwards"]:
        if fwd_key(f["proto"], f["port"]) == k:
            return f
    return None


def upsert_forward(st, f, default_origin="k3"):
    """Insert or replace by (proto, port). Returns the replaced record or None."""
    nf = normalize_forward(f, default_origin, stamp=True)
    old = find_forward(st, nf["proto"], nf["port"])
    if old is not None:
        st["forwards"].remove(old)
    st["forwards"].append(nf)
    st["forwards"] = sort_forwards(st["forwards"])
    return old


def remove_forward(st, proto, port):
    """Remove by (proto, port). Returns the removed record or None."""
    old = find_forward(st, proto, port)
    if old is not None:
        st["forwards"].remove(old)
    return old


def add_migration(st, record):
    st.setdefault("migrations", []).append(record)
    # keep the history short: the backups themselves are pruned to the last few
    st["migrations"] = st["migrations"][-20:]
