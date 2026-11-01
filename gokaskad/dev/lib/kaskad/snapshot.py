"""Read cache shared by the web panel and the bot (docs/k3/SPEC-wave3.md §1).

Long-running readers (kaskad-panel, kaskad-bot) must not re-read and re-parse the same files on every request:

* ``read_json(path)`` keeps the parsed value until the file's signature (inode, mtime_ns, size) changes; the
  signature is taken BEFORE reading, so a write that lands during the read is seen on the next call;
* ``generation()`` / ``bump()`` — a counter raised after every successful change made by this process
  (panel/bot mutations); readers compare it to drop derived caches;
* ``SingleFlight`` — identical concurrent computations run once, the others wait for that result
  (bounded: at most ``limit`` distinct keys in flight, then the caller computes alone);
* ``derived(key, paths, fn)`` — a value computed from files (a view of the panel, the overview) kept until
  one of the files changes or the generation rises; identical concurrent misses compute it once.

Never caches secrets beyond the process (no disk), never returns a shared mutable object: callers get a deep
copy only when they ask (``copy=True``) — the panel serializes straight to JSON and does not mutate. A
``derived`` value is shared as well: callers never change it. Secrets (issued settings) never go through
``derived``.
"""

import copy as _copy
import os
import threading

from . import util

_lock = threading.Lock()
_cache = {}          # path -> (signature, value)
_gen = [0]


def signature(path):
    """(inode, mtime_ns, size) of ``path`` or None when it does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def read_json(path, default=None, copy=False):
    """Parsed JSON of ``path`` (cached by signature); ``default`` when missing or unreadable."""
    sig = signature(path)
    if sig is None:
        return default
    with _lock:
        hit = _cache.get(path)
        if hit is not None and hit[0] == sig:
            return _copy.deepcopy(hit[1]) if copy else hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            value = util.json_kept(f.read())    # a number past ±NUM_MAX as state.load reads it (REV-33714 С-1)
    except (OSError, ValueError):
        return default
    with _lock:
        _cache[path] = (sig, value)
    return _copy.deepcopy(value) if copy else value


def forget(path=None):
    """Drop the cached value of ``path`` (all paths and every derived value when None)."""
    with _lock:
        if path is None:
            _cache.clear()
            _derived.clear()
        else:
            _cache.pop(path, None)


def generation():
    return _gen[0]


def bump():
    """A change was made by this process: raise the generation, drop parsed files."""
    with _lock:
        _gen[0] += 1
        _cache.clear()
        return _gen[0]


class SingleFlight(object):
    """``do(key, fn)``: one ``fn()`` per key at a time; concurrent callers with the same key wait and get the
    same result (or the same exception). At most ``limit`` keys in flight; beyond it the caller runs ``fn``."""

    def __init__(self, limit=32):
        self.limit = limit
        self._lock = threading.Lock()
        self._calls = {}      # key -> [event, result, exception]

    def do(self, key, fn):
        with self._lock:
            call = self._calls.get(key)
            if call is None:
                if len(self._calls) >= self.limit:
                    call = None
                    leader = False
                else:
                    call = [threading.Event(), None, None]
                    self._calls[key] = call
                    leader = True
            else:
                leader = False
        if call is None:
            return fn()
        if not leader:
            call[0].wait()
            if call[2] is not None:
                raise call[2]
            return call[1]
        try:
            call[1] = fn()
            return call[1]
        except BaseException as e:            # noqa: BLE001 — handed to the waiters as is
            call[2] = e
            raise
        finally:
            with self._lock:
                self._calls.pop(key, None)
            call[0].set()


_derived = {}        # key -> (stamp, value)
_flight = SingleFlight()


def stamp(files):
    """(generation, (path, signature) of every file of ``files``) — what a derived value depends on. Taken
    BEFORE the files are read: a file written during the computation makes the stored value stale at once."""
    return (generation(),) + tuple((p, signature(p)) for p in files)


def derived(key, files, fn):
    """``fn()`` kept until a file of ``files`` changes (signature) or the generation rises. Identical
    concurrent misses run ``fn`` once and share its result or its exception (SingleFlight; the stamp is a
    part of the flight key, so a request made after a change never joins a computation started before it).
    A value computed while the generation moved on is returned but not stored."""
    st = stamp(files)
    with _lock:
        hit = _derived.get(key)
        if hit is not None and hit[0] == st:
            return hit[1]

    def compute():
        value = fn()
        with _lock:
            if _gen[0] == st[0]:
                _derived[key] = (st, value)
        return value
    return _flight.do((key,) + st, compute)
