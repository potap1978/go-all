"""/etc/kaskad/secrets.json — the secrets of the gateway, apart from state.json (docs/k3/SPEC-wave2-gateway.md §2).

Private keys of servers and devices (WireGuard/AmneziaWG), device passwords, the L2TP/IPsec PSK, CA and
keys of OpenVPN/ocserv/IKEv2. state.json keeps only public keys and references. Keys are dotted names::

    wg.server.private        awg2.server.private      dev.<id>.password       dev.<id>.wg.private
    dev.<id>.awg3.private    l2tp.psk                 ovpn.ca.key             ocserv.server.key

File: ``{"schema": 1, "values": {key: value}}``, mode 0600, atomic writes (temp file + rename).
Values are strings (or JSON-able objects for adapters that need them).

Two ways to use it:
  * module functions ``get/set/delete`` — one key, read-modify-write of the file;
  * ``load()`` -> ``Store`` (a dict with ``set/delete/delete_prefix/save``) — what adapters get as ``sec``
    in render/apply/issue; the core saves it once after a change.

Callers that change secrets hold the state lock (``state.locked()``), like every other change.
Nothing here prints or logs a value. The stdlib module ``secrets`` is not used on purpose (this module
shadows its name inside the package): randomness comes from ``random.SystemRandom`` (os.urandom).
"""

import builtins
import json
import random
import re

from . import paths, util
from .errors import StateError
from .i18n import tr

SCHEMA = 1
# letters and digits without look-alikes (0/O/o, 1/l/I): passwords are typed into router web forms
ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LOWER = "abcdefghijkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")

_rng = random.SystemRandom()


def _check_key(key):
    if not isinstance(key, str) or not _KEY.match(key):
        raise StateError(tr("secrets.err.bad_name", name=repr(key)))
    return key


class Store(dict):
    """Secrets in memory. ``dirty`` is set by set/delete; ``save()`` writes the file only when dirty, merged
    with what is on disk (adapters also write single keys with ``kaskad.secrets.set``). Deletions can wait:
    ``save(keep_deleted=True)`` writes the additions only — the core uses it before state.json is saved, so
    a failed operation never loses a secret the saved state still refers to."""

    def __init__(self, values=None, path=None):
        super().__init__(values or {})
        self.path = path or paths.secrets_file()
        self.dirty = False
        self.deleted = builtins.set()   # the module function set() shadows the builtin here

    def set(self, key, value):
        _check_key(key)
        if self.get(key) != value:
            self[key] = value
            self.dirty = True
        self.deleted.discard(key)
        return value

    def delete(self, key):
        if key in self:
            del self[key]
            self.deleted.add(key)
            self.dirty = True
            return True
        return False

    def delete_prefix(self, prefix):
        """Remove every key starting with ``prefix`` (e.g. ``dev.d-1a2b3c4d.`` when a device is deleted)."""
        gone = [k for k in self if k.startswith(prefix)]
        for k in gone:
            del self[k]
            self.deleted.add(k)
        if gone:
            self.dirty = True
        return len(gone)

    def setdefault_new(self, key, factory):
        """The value of ``key``; if missing — ``factory()`` is stored and returned (keys, passwords)."""
        if key not in self:
            self.set(key, factory())
        return self[key]

    def save(self, keep_deleted=False):
        if not self.dirty:
            return False
        try:
            disk = dict(load(self.path))
        except StateError:
            disk = {}
        merged = dict(disk)
        merged.update(self)
        if not keep_deleted:
            for k in self.deleted:
                merged.pop(k, None)
        util.atomic_write(self.path, util.json_text({"schema": SCHEMA, "values": merged}), 0o600)
        if keep_deleted and self.deleted:
            return True                       # still dirty: the deletions are pending
        self.deleted.clear()
        self.dirty = False
        return True


def load(path=None):
    """Secrets from disk; a missing file is empty. A broken file raises StateError (never overwritten silently)."""
    path = path or paths.secrets_file()
    raw = util.read_text(path)
    if raw is None:
        return Store({}, path)
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise StateError(tr("secrets.err.broken", path=path, error=e))
    if not isinstance(doc, dict) or not isinstance(doc.get("values"), dict):
        raise StateError(tr("secrets.err.not_object", path=path))
    if doc.get("schema") != SCHEMA:
        raise StateError(tr("secrets.err.schema", path=path, schema=repr(doc.get("schema"))))
    return Store(doc["values"], path)


def get(key, default=None):
    return load().get(_check_key(key), default)


def set(key, value):            # noqa: A001 — the name is the contract (spec §7)
    s = load()
    s.set(key, value)
    s.save()
    return value


def delete(key):
    s = load()
    gone = s.delete(_check_key(key))
    s.save()
    return gone


def new_password(n=14):
    """Letters and digits without look-alikes, at least one lower, one upper and one digit (router forms)."""
    n = max(int(n), 8)
    while True:
        pw = "".join(_rng.choice(ALPHABET) for _ in range(n))
        if any(c in _LOWER for c in pw) and any(c in _UPPER for c in pw) and any(c in _DIGITS for c in pw):
            return pw


def new_psk(n=24):
    """Pre-shared key for L2TP/IPsec: the same alphabet (typed by hand on phones and routers), 24 chars."""
    return new_password(n)


def new_token(nbytes=16):
    """Random hex (ids, logins)."""
    return "".join("%02x" % _rng.randrange(256) for _ in range(nbytes))
