"""The lists on the node: /var/lib/kaskad/rules (docs/k3/SPEC-wave3.md §2: «хранить последнюю удачную копию,
атомарная замена, проверка sha256 и разбора до замены»).

    versions/<id>/       one complete set of lists, never changed after it is written:
        geoip.dat        RoscomVPN geoip (absent when the networks came from a text list)
        geosite.dat      RoscomVPN geosite (absent when it could not be had)
        ru4.txt          merged IPv4 ranges of geoip ``direct`` for the kernel, one per line
        domains.json     {category: {"domains": [...], "keyword": n, ...}} for dnsmasq
        meta.json        what it is: sources, release tags, dates, sha256 and sizes of the files, counts
    current  -> versions/<id>     the lists in use (a symlink: switched with one rename)
    previous -> versions/<id>     the last good lists before them (the fallback when current is damaged)
    status.json                   the last update attempt (time, result, error) — written on every attempt
    update.lock                   one update at a time

A new version is written whole into ``versions/.new-<id>`` (every file fsynced, meta.json last), renamed to
``versions/<id>`` and only then made current: a reader sees either the old lists or the new ones. A version is
used only if its meta.json parses and every file it lists is there with its size (``load``); ``active()`` is
current, else previous. Only data that decoded and passed the checks is ever committed: a broken download
leaves the store as it was.
"""

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import shutil

from .. import paths, util
from ..errors import KaskadError, LockedError
from ..i18n import tr

SCHEMA = 1
META = "meta.json"
GEOIP = "geoip.dat"
GEOSITE = "geosite.dat"
RANGES = "ru4.txt"
DOMAINS = "domains.json"
CURRENT = "current"
PREVIOUS = "previous"
VERSIONS = "versions"
STATUS = "status.json"
LOCK = "update.lock"


def root():
    return paths.rules_dir()


def _link(name):
    return os.path.join(root(), name)


class Version(object):
    """One complete set of lists (read-only)."""

    def __init__(self, path, meta):
        self.path = path
        self.meta = meta

    @property
    def id(self):
        return os.path.basename(self.path)

    def file(self, name):
        """Path of a file of this version, None when the version has no such file."""
        if name not in (self.meta.get("files") or {}):
            return None
        return os.path.join(self.path, name)

    def sha256(self, name):
        return ((self.meta.get("files") or {}).get(name) or {}).get("sha256")

    def read(self, name):
        p = self.file(name)
        if p is None:
            return None
        with open(p, "rb") as f:
            return f.read()


def _valid(path):
    """Version at ``path`` or None: meta.json of this schema and every listed file present with its size."""
    raw = util.read_text(os.path.join(path, META))
    if raw is None:
        return None
    try:
        meta = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("schema") != SCHEMA or not isinstance(meta.get("files"), dict):
        return None
    for name, rec in meta["files"].items():
        if name != os.path.basename(name) or not isinstance(rec, dict):
            return None
        try:
            if os.path.getsize(os.path.join(path, name)) != rec.get("size"):
                return None
        except OSError:
            return None
    return Version(path, meta)


def load(name=CURRENT):
    """The version ``current`` or ``previous`` points to, None when it is missing or damaged."""
    link = _link(name)
    try:
        target = os.readlink(link)
    except OSError:
        return None
    path = os.path.normpath(os.path.join(root(), target))
    if os.path.dirname(path) != os.path.join(root(), VERSIONS):
        return None                                   # a link someone pointed elsewhere: not ours
    return _valid(path)


def active():
    """The lists to use: current, else the last good ones (previous), else None."""
    return load(CURRENT) or load(PREVIOUS)


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _swap(name, target):
    """Point the symlink ``name`` at ``target`` atomically (a new link renamed over the old one)."""
    link = _link(name)
    tmp = link + ".new"
    try:
        os.unlink(tmp)
    except OSError:
        pass
    os.symlink(target, tmp)
    os.rename(tmp, link)


def _new_id():
    return "%s-%s" % (util.stamp(), os.urandom(3).hex())


def commit(files, meta):
    """Write a new version (``files`` {name: bytes}, ``meta`` without "files"/"schema") and make it current;
    the version current before becomes previous, older ones go. Returns the new Version."""
    util.ensure_dir(root(), 0o700)
    vroot = os.path.join(root(), VERSIONS)
    util.ensure_dir(vroot, 0o700)
    vid = _new_id()
    tmp = os.path.join(vroot, ".new-" + vid)
    final = os.path.join(vroot, vid)
    meta = dict(meta)
    meta["schema"] = SCHEMA
    meta["files"] = {}
    try:
        os.mkdir(tmp, 0o700)
        for name in sorted(files):
            if name != os.path.basename(name) or name in (META,):
                raise KaskadError(tr("rules.store.bad_name", name=repr(name)))
            data = files[name]
            util.atomic_write(os.path.join(tmp, name), data, 0o600)
            meta["files"][name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        util.atomic_write(os.path.join(tmp, META), util.json_text(meta), 0o600)
        _fsync_dir(tmp)
        os.rename(tmp, final)
        _fsync_dir(vroot)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    old = None
    try:
        old = os.readlink(_link(CURRENT))
    except OSError:
        pass
    rel = os.path.join(VERSIONS, vid)
    if old and _valid(os.path.normpath(os.path.join(root(), old))) is not None:
        _swap(PREVIOUS, old)
    _swap(CURRENT, rel)
    _fsync_dir(root())
    _gc()
    return Version(final, meta)


def _gc():
    """Remove versions nobody points to (and leftovers of interrupted writes)."""
    keep = set()
    for name in (CURRENT, PREVIOUS):
        try:
            keep.add(os.path.basename(os.path.normpath(os.readlink(_link(name)))))
        except OSError:
            pass
    vroot = os.path.join(root(), VERSIONS)
    try:
        names = os.listdir(vroot)
    except OSError:
        return
    for n in names:
        if n not in keep:
            shutil.rmtree(os.path.join(vroot, n), ignore_errors=True)


@contextlib.contextmanager
def update_lock():
    """One update at a time (the timer, the CLI, the panel). Busy -> LockedError at once."""
    util.ensure_dir(root(), 0o700)
    fd = os.open(os.path.join(root(), LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES):
                raise LockedError(tr("rules.store.busy"))
            raise
        yield
    finally:
        os.close(fd)


def load_status():
    raw = util.read_text(os.path.join(root(), STATUS))
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def save_status(doc):
    util.ensure_dir(root(), 0o700)
    util.atomic_write(os.path.join(root(), STATUS), util.json_text(doc), 0o600)


def status_path():
    return os.path.join(root(), STATUS)


def purge():
    """Everything of the lists away (``gw purge``). True if there was something."""
    if not os.path.lexists(root()):
        return False
    shutil.rmtree(root(), ignore_errors=True)
    return True
