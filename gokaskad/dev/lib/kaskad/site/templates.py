"""Templates of the masquerade site: the catalog of 33 467 static sites under free licences (from goAmnezia Pro) and
their deployment into the web root (docs/k3/SPEC-wave4.md §5, §5.1).

    catalog()                       {"categories": [{"id", "title", "icon", "count"}], "total"}
    search(category, query, page)   {"items": [{"id", "name", "category", "source", "license", "preview", ...}],
                                     "page", "pages", "total"}
    pick_random(exclude=())         an id of the default pool: no «portfolio», no *.github.io, only with a licence,
                                    not known as removed by its author (site.tplhealth)
    deploy(runner, template_id, root)  {"ok", "id", "source", "fallback", "backup", "tries", ...}
    status(root)                    {"id", "source", "deployed_at", "root", ...} — by the markers in the root
    fetch_catalog(runner)           the catalog to /opt/kaskad/assets/templates_catalog.json (sha256 checked)
    prepare(runner)                 the catalog and its index for a screen of templates (apart under systemd)
    build_index()                   the index file next to the catalog (``kaskadctl site templates-index``)
    register_cli(sub)               kaskadctl site templates | site templates-index | site template set|status

The reads (catalog, search, get, by_short_key, pick_random) never parse the 14 MB of JSON: they seek in the index
(see «the index on disk» below) — the panel lives in a MemoryMax (200M then; T-M6-qs330 №3: OOM on «Выбрать вручную»).

The catalog lives in the repository as ``assets/templates_catalog.json.gz`` (not in the payload of install.sh) and
comes to a node only when the site is wanted: from a checkout of the repository on disk, else from GitHub with the
key of the node (``ghkey.open_github``; the repository is private), from the branch the self-update uses. Whatever
the way, the content must have CATALOG_SHA256 — nothing else is ever written to the node.

The page rules are those of goAmnezia ``lib/templates_lib.sh`` (``_tpl_pick_index_dir``, ``_tpl_page_ok``,
``download_template``) one to one — they are what keeps the base good. Changes against goAmnezia (§5):

* the clone has a deadline of CLONE_TIMEOUT (90 s) for all its git calls and a size limit MAX_BYTES (100 MB);
* symlinks of a foreign repository are removed right after the clone — before anything reads the tree;
* the site is built and checked in a directory next to the root and only then put in place by rename (an exchange
  where the kernel can): an error at any step leaves the previous site as it was. The previous site goes to
  site_backup_dir()/previous — one copy;
* service files are removed by exact names (README.md, LICENSE.txt, package.json…): license.html, security.html
  and other pages of the site stay (goAmnezia's ``LICENSE.*`` mask removed them);
* trackers are cut as by goAmnezia ``_ga_site_strip_trackers``; the authors' credits in the footer are not touched;
* random choice (template_id=None): up to MAX_TRIES templates of the pool, then the built-in stub page — the stub
  only when there is no site in the root yet: a working site is never replaced by a stub. Every candidate's repository
  is asked first (``git ls-remote``, at most MAX_TRIES * 3 of them): a removed one is marked and another one is taken;
* «is it still there» (site.tplhealth, 3.2.21): a template known as removed is refused at once, without a clone
  (reason «gone»); a clone refused as «Repository not found» marks it removed, a deployment marks it alive;
* git is installed (apt) only here, in deploy, and written to /etc/kaskad/site/pkgs when Kaskad installed it.
"""

import array
import collections.abc
import contextlib
import errno
import fcntl
import fnmatch
import gzip
import hashlib
import io
import json
import os
import random
import re
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from .. import ghkey, paths, util
from ..errors import KaskadError, LockedError, USAGE
from ..i18n import get_lang, tr
from . import tplhealth

# ── the catalog ─────────────────────────────────────────────────────────────

CATALOG_SHA256 = "ca31e8d0d6a58687a73cef43fc3c13efe0b2d9f9afa1d257dbe85951a03028e1"   # goAmnezia, 14 266 581 bytes
CATALOG_NAME = "templates_catalog.json"
CATALOG_ASSET = "assets/templates_catalog.json.gz"      # in the repository kaskad-pro
CATALOG_MAX = 64 * 1024 * 1024                          # bigger (packed or not) is not our catalog
FETCH_TIMEOUT = 120                                     # s for the download of the packed catalog (~1.4 MB)
# after KASKAD_BRANCH. dev last (k3 before 3.3.4, frozen now): a node installed from it without KASKAD_BRANCH found the
# catalog nowhere (main and test have no assets/ yet — O, 2026-10-08). The sha256 decides, not the branch: any branch
# may give the catalog
REFS = ("main", "test", "dev")
KEY_HINT = "SSH: gokaskad → 8 → 6"                      # where a person sets the GitHub key
UPDATE_HINT = "SSH: gokaskad → 11"                      # where a person updates Kaskad

# ── deployment ──────────────────────────────────────────────────────────────

CLONE_TIMEOUT = 90                      # s for all git calls of one template (as «свой git» of goAmnezia)
MAX_BYTES = 100 * 1024 * 1024           # a working tree bigger than this is refused (.git not counted)
MIN_FREE = 256 * 1024 * 1024            # free space before a clone (goAmnezia GA_SITE_MIN_DISK_MB)
MAX_TRIES = 3                           # random templates before the stub
STUB_ID = "builtin_stub"
MARK_ID = ".kaskad_template_id"
MARK_SOURCE = ".kaskad_template_source"
BACKUP_NAME = "previous"
PKG = "git"
PER_PAGE = 25
QUERY_MAX = 64

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")          # goAmnezia ga_site_template_id_valid / catalog_tool.ID_RE
_REPO_BAD = re.compile(r"[\s;`$()<>|\\&'\"]")
_SPARSE_RE = re.compile(r"^[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")

_now = time.monotonic                   # tests move the clock of a hanging clone
_rng = random.Random()                  # tests seed it

TEXT_CODES = ("unknown_template", "bad_repo", "download_failed", "no_index", "app_shell", "broken_assets", "timeout",
              "too_big", "gone")


def text(code, tid):
    """The words of a template failure ``code`` (TEXT_CODES; another — download_failed) for template ``tid``."""
    if code not in TEXT_CODES:
        code = "download_failed"
    return tr("site_tpl.text." + code, tid=tid, sec=CLONE_TIMEOUT, mb=MAX_BYTES // (1024 * 1024))


class _Texts(collections.abc.Mapping):
    """``TEXTS[code] % tid`` as before 3.3.2 (site/ops.py, tests): the words in the language of the moment of the
    read, with ``%s`` where the id goes. New code calls ``text(code, tid)``."""

    def __getitem__(self, code):
        if code not in TEXT_CODES:
            raise KeyError(code)
        return text(code, "%s")

    def __iter__(self):
        return iter(TEXT_CODES)

    def __len__(self):
        return len(TEXT_CODES)


TEXTS = _Texts()


class CatalogMissing(KaskadError):
    """The catalog is not on this node (or is damaged): ``fetch_catalog`` brings it."""


# ── the catalog: fetch ──────────────────────────────────────────────────────

def catalog_path():
    return os.path.join(paths.assets_dir(), CATALOG_NAME)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


_verified = {}                          # path -> (size, mtime_ns) of a file whose sha256 was right


def _file_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns) if stat.S_ISREG(st.st_mode) else None


def _file_ok(path):
    """The catalog at ``path`` has CATALOG_SHA256 (remembered by size and mtime: 14 MB are not hashed per call)."""
    sig = _file_sig(path)
    if sig is None or sig[0] > CATALOG_MAX:
        return False
    if _verified.get(path) == (sig, CATALOG_SHA256):
        return True
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    ok = h.hexdigest() == CATALOG_SHA256
    if ok:
        _verified[path] = (sig, CATALOG_SHA256)
    return ok


def _gunzip(packed):
    """Unpacked bytes of a gzip, at most CATALOG_MAX (a bomb stops there); None when it is not a whole gzip."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as g:
            data = g.read(CATALOG_MAX + 1)
    except (OSError, EOFError, ValueError):
        return None
    return data if len(data) <= CATALOG_MAX else None


def _install_catalog(raw):
    util.ensure_dir(paths.assets_dir(), 0o755)
    dst = catalog_path()
    util.atomic_write(dst, raw, 0o644)
    _verified[dst] = (_file_sig(dst), CATALOG_SHA256)
    return dst


def _repo_roots():
    """Checkouts of kaskad-pro on disk that may hold assets/ (as gw.inbounds.awg looks for the core):
    KASKAD_ASSETS_ROOTS (tests: "" — none), else KASKAD_SRC_DIR, the checkout of this code, the usual places."""
    env = os.environ.get("KASKAD_ASSETS_ROOTS")
    if env is not None:
        return [r for r in env.split(":") if r]
    here = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.normpath(os.path.join(here, "..", "..", ".."))
    roots = [os.environ.get("KASKAD_SRC_DIR", ""), checkout, "/opt/kaskad-pro", "/opt/gokaskad", "/root/kaskad-pro"]
    return [r for r in roots if r]


def _refs():
    """The branches to try: KASKAD_BRANCH of /etc/kaskad/config (the self-update's own), then main, test (on a 3.x
    node without KASKAD_BRANCH the self-update takes test when main is older) and dev — the sha256 decides anyway."""
    from ..bot import config as bconf
    out = []
    branch = (bconf.parse(util.read_text(paths.config()) or "").get("KASKAD_BRANCH") or "").strip()
    if branch and _BRANCH_RE.match(branch) and ".." not in branch:
        out.append(branch)
    for r in REFS:
        if r not in out:
            out.append(r)
    return out


def _download(key):
    """The packed catalog from GitHub -> (raw, ref). KaskadError (reason nokey | bad | net | missing | sha)."""
    last = None
    for ref in _refs():
        url = "https://raw.githubusercontent.com/%s/%s/%s" % (ghkey.REPO, ref, CATALOG_ASSET)
        req = urllib.request.Request(url, headers={"Authorization": "token " + key, "User-Agent": "kaskad"})
        try:
            with ghkey.open_github(req, FETCH_TIMEOUT) as r:          # the key stays with GitHub
                packed = r.read(CATALOG_MAX + 1)
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            if code == 404:
                last = last or "missing"
                continue
            if code in (401, 403):
                raise KaskadError(tr("site_tpl.fetch.bad_key", code=code, hint=KEY_HINT),
                                  data={"reason": "bad", "needs_key": True})
            last = "net"
            continue
        except (urllib.error.URLError, OSError, ValueError):
            raise KaskadError(tr("site_tpl.fetch.no_net"), data={"reason": "net"})
        raw = _gunzip(packed) if len(packed) <= CATALOG_MAX else None
        if raw is not None and _sha(raw) == CATALOG_SHA256:
            return raw, ref
        last = "sha"
    if last == "sha":
        raise KaskadError(tr("site_tpl.fetch.sha"), data={"reason": "sha"})
    if last == "net":
        raise KaskadError(tr("site_tpl.fetch.net"), data={"reason": "net"})
    # the key works (else 401/403 above): GitHub has no catalog in any branch tried — which ones, and what to do
    raise KaskadError(tr("site_tpl.fetch.missing", refs=", ".join(_refs()), hint=UPDATE_HINT),
                      data={"reason": "missing", "refs": _refs()})


def fetch_catalog(runner=None):
    """Make sure /opt/kaskad/assets/templates_catalog.json is the catalog (sha256 = CATALOG_SHA256): there and right
    — nothing to do; else from a checkout of the repository on disk; else from GitHub with the key of the node.
    -> {"ok": True, "path", "downloaded": bool, "from": "disk" | "repo" | "github", "ref", "sha256"}.
    KaskadError with data["reason"] nokey | bad | net | missing | sha and a text that says what to do.
    ``runner`` is not used (the contract keeps it for the callers)."""
    dst = catalog_path()
    out = {"ok": True, "path": dst, "downloaded": False, "from": "disk", "ref": None, "sha256": CATALOG_SHA256}
    if _file_ok(dst):
        return out
    for root in _repo_roots():
        src = os.path.join(root, CATALOG_ASSET)
        try:
            with open(src, "rb") as f:
                packed = f.read(CATALOG_MAX + 1)
        except OSError:
            continue
        raw = _gunzip(packed)
        if raw is not None and _sha(raw) == CATALOG_SHA256:
            _install_catalog(raw)
            util.log_tr("site_tpl.log.catalog_repo", src=src)
            out.update({"downloaded": True, "from": "repo"})
            return out
    key = ghkey.current()[0]
    if not key:
        raise KaskadError(tr("site_tpl.fetch.no_key", hint=KEY_HINT),
                          data={"reason": "nokey", "needs_key": True})
    raw, ref = _download(key)
    _install_catalog(raw)
    util.log_tr("site_tpl.log.catalog_github", ref=ref)
    out.update({"downloaded": True, "from": "github", "ref": ref})
    return out


# ── the catalog: the index on disk (3.3.0, T-M6-qs330 №3) ───────────────────
#
# The catalog is 14 MB of JSON, 33 467 templates: parsed whole it takes ~190 MB of Python objects — the panel lives in
# MemoryMax=200M and was killed by the OOM killer on «Выбрать вручную». So no long-lived process (panel, bot, MCP)
# ever parses it: the catalog is turned once into an index file next to it, and every read is a few seeks in that
# file. The index is built by ``kaskadctl site templates-index`` — under systemd in a ``systemd-run --scope`` of its
# own (outside the cgroup and the limits of the caller), elsewhere (CLI, tests) in the calling process.
#
# The file (one, replaced by rename: a reader keeps the inode it opened):
#     MAGIC line
#     header — one line of JSON: {"format", "src_sha", "src_size", "src_mtime_ns", "total",
#              "categories": [{"id", "title", "icon", "count", "start"}], "offs", "keys"}
#     records — ``total`` lines in the order of the catalog (the templates of a category are one run), 11 fields by TAB:
#              the 9 of a record (escaped: \\ \t \n \r) + the folded «name\x1fid» and the folded description for search
#     offsets — ``total`` × uint64 LE: where each record starts (from the end of the header)
#     keys    — ``total`` × (12 bytes short_key + uint32 LE record), sorted: get() and by_short_key() by binary search
# The header remembers the catalog it was built from (sha256, size, mtime): another catalog, a new format — rebuilt.

INDEX_NAME = "templates_index.bin"
INDEX_FORMAT = 1
MAGIC = b"KASKAD-TEMPLATES-INDEX\n"
HEADER_MAX = 1 << 20                    # one line of JSON with ~20 categories is ~3 KB
BUILD_TIMEOUT = FETCH_TIMEOUT + 180     # s for the separate process: the download (when asked) and the build
BUILD_APART = None                      # None — by the process: under systemd (INVOCATION_ID) apart; tests set it
_OFF = struct.Struct("<Q")
_KEY = struct.Struct("<12sI")
_SHORT_RE = re.compile(r"^[0-9a-f]{12}$")

# one record: (id, name, category, source, license, preview, description, repo_url, sparse_path)
_I_ID, _I_NAME, _I_CAT, _I_SRC, _I_LIC, _I_PREV, _I_DESC, _I_REPO, _I_SPARSE = range(9)
_build_mutex = threading.Lock()         # threads of one process; processes — flock on the .lock file


def index_path():
    return os.path.join(paths.assets_dir(), INDEX_NAME)


def _s(v):
    return v if isinstance(v, str) else ""


def _fold(s):
    return s.casefold().replace("ё", "е")         # i18n: skip — search folding of the catalog text


_ESC = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
_UNESC = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}
_ESC_RE = re.compile(r"[\\\t\n\r]")
_UNESC_RE = re.compile(r"\\(.)")
_SEP_RE = re.compile(r"[\t\n\r]")


def _esc(s):
    return _ESC_RE.sub(lambda m: _ESC[m.group(0)], s)


def _unesc(s):
    return _UNESC_RE.sub(lambda m: _UNESC.get(m.group(1), m.group(1)), s) if "\\" in s else s


def _search_text(s):
    """Folded text for search: TAB/CR/LF become \\x1f — a query never has them (norm_query squeezes whitespace, and
    \\x1f is whitespace for str.split), so a match is exactly a match in the original text."""
    return _SEP_RE.sub("\x1f", _fold(s))


def _line(rec):
    fields = [_esc(x) for x in rec]
    fields.append(_search_text(rec[_I_NAME]) + "\x1f" + _search_text(rec[_I_ID]))
    fields.append(_search_text(rec[_I_DESC]))
    return ("\t".join(fields) + "\n").encode("utf-8", "surrogatepass")


def _parse(raw):
    """A record line (bytes) -> the 9-tuple; the two search fields are dropped."""
    f = raw.decode("utf-8", "surrogatepass").rstrip("\n").split("\t")
    if len(f) != 11:
        raise ValueError("index record of %d fields" % len(f))
    return tuple(_unesc(x) for x in f[:9])


def _fresh(hdr, sig):
    return (isinstance(hdr, dict) and hdr.get("format") == INDEX_FORMAT and hdr.get("src_sha") == CATALOG_SHA256
            and sig is not None and [hdr.get("src_size"), hdr.get("src_mtime_ns")] == list(sig))


def _read_header(f):
    """(header, base) of an open index file, or (None, 0) when it is not a whole index of ours (the size must be
    exactly what the header says: a file cut short is built again, not read past its end)."""
    try:
        if f.read(len(MAGIC)) != MAGIC:
            return None, 0
        line = f.readline(HEADER_MAX)
        if not line.endswith(b"\n"):
            return None, 0
        hdr = json.loads(line.decode("utf-8"))
        base = f.tell()
        if not isinstance(hdr, dict) or \
                os.fstat(f.fileno()).st_size != base + int(hdr["keys"]) + _KEY.size * int(hdr["total"]):
            return None, 0
        return hdr, base
    except (OSError, ValueError, UnicodeDecodeError, KeyError, TypeError):
        return None, 0


def _missing():
    return CatalogMissing(tr("site_tpl.catalog.missing"), data={"reason": "catalog_missing"})


def _damaged(text=None):
    return CatalogMissing(text or tr("site_tpl.catalog.damaged"), data={"reason": "catalog_damaged"})


class _Index(object):
    """An open index file: its header and the reads of records. A context manager."""

    def __init__(self, f, hdr, base):
        self.f, self.hdr, self.base = f, hdr, base
        self.total = int(hdr.get("total") or 0)
        self.cats = [c for c in hdr.get("categories") or [] if isinstance(c, dict)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.f.close()

    def _offsets(self, i, n):
        self.f.seek(self.base + int(self.hdr["offs"]) + _OFF.size * i)
        data = self.f.read(_OFF.size * n)
        if len(data) != _OFF.size * n:
            raise ValueError("index cut short")
        return [_OFF.unpack_from(data, k * _OFF.size)[0] for k in range(n)]

    def lines(self, start, end):
        """Raw record lines start..end-1 (one seek: a run of records is contiguous)."""
        if end <= start:
            return
        self.f.seek(self.base + self._offsets(start, 1)[0])
        for _ in range(end - start):
            line = self.f.readline()
            if not line.endswith(b"\n"):
                raise ValueError("index cut short")
            yield line

    def record(self, i):
        return _parse(next(self.lines(i, i + 1)))

    def run(self, start, n):
        return [_parse(x) for x in self.lines(start, start + n)]

    def key_at(self, k):
        self.f.seek(self.base + int(self.hdr["keys"]) + _KEY.size * k)
        data = self.f.read(_KEY.size)
        if len(data) != _KEY.size:
            raise ValueError("index cut short")
        return _KEY.unpack(data)

    def by_key(self, key):
        """Record numbers whose short_key is ``key`` (str of 12 hex), in the order of the catalog."""
        want = key.encode("ascii")
        lo, hi = 0, self.total
        while lo < hi:
            mid = (lo + hi) // 2
            if self.key_at(mid)[0] < want:
                lo = mid + 1
            else:
                hi = mid
        out = []
        while lo < self.total:
            k, i = self.key_at(lo)
            if k != want:
                break
            out.append(i)
            lo += 1
        return sorted(out)


def _open_index():
    """The index of the catalog on this node, built when it is not there or is older than the catalog.
    CatalogMissing when there is no catalog (or it is damaged)."""
    sig = _file_sig(catalog_path())
    if sig is None:
        raise _missing()
    for attempt in (0, 1):
        try:
            f = open(index_path(), "rb")
        except OSError:
            f = None
        if f is not None:
            hdr, base = _read_header(f)
            if _fresh(hdr, sig):
                return _Index(f, hdr, base)
            f.close()
        if attempt == 0:
            ensure_index()
            sig = _file_sig(catalog_path())
            if sig is None:
                raise _missing()
    raise _damaged(tr("site_tpl.catalog.index_not_built", hint=UPDATE_HINT))


def index_fresh():
    """The index is there and is of the catalog on disk (no build, no parse)."""
    try:
        with open(index_path(), "rb") as f:
            return _fresh(_read_header(f)[0], _file_sig(catalog_path()))
    except OSError:
        return False


@contextlib.contextmanager
def _build_lock():
    util.ensure_dir(paths.assets_dir(), 0o755)
    with _build_mutex:
        fd = os.open(index_path() + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _compact(pairs):
    """object_pairs_hook of the build: a template becomes a tuple of its 8 strings (no category yet) at once — the
    dicts of 33 467 templates are never all in memory. A category and the top keep their dicts."""
    d = dict(pairs)
    if "templates" in d or "categories" in d or not _s(d.get("id")):
        return d
    return (_s(d.get("id")), _s(d.get("name")), _s(d.get("source")), _s(d.get("license")), _s(d.get("preview_url")),
            _s(d.get("description")), _s(d.get("repo_url")), _s(d.get("sparse_path")))


def _catalog_records(doc):
    """(categories, records) of a parsed catalog as the reads saw it always: a template without an id or seen in an
    earlier category is skipped; a category without the list of templates too. Templates are dicts or the tuples of
    ``_compact``."""
    cats, recs, seen = [], [], set()
    for c in (doc.get("categories") if isinstance(doc, dict) else None) or []:
        if not isinstance(c, dict) or not isinstance(c.get("templates"), list):
            continue
        cid, start = _s(c.get("id")), len(recs)
        for t in c["templates"]:
            if isinstance(t, dict):
                if not _s(t.get("id")):
                    continue
                t = (_s(t.get("id")), _s(t.get("name")), _s(t.get("source")), _s(t.get("license")),
                     _s(t.get("preview_url")), _s(t.get("description")), _s(t.get("repo_url")),
                     _s(t.get("sparse_path")))
            elif not isinstance(t, tuple):
                continue
            if t[0] in seen:
                continue
            seen.add(t[0])
            recs.append((t[0], t[1], cid) + t[2:])
        c["templates"] = None                   # this category is in recs now
        cats.append({"id": cid, "title": _s(c.get("name")) or cid, "icon": _s(c.get("icon")),
                     "count": len(recs) - start, "start": start})
    return cats, recs


def build_index():
    """Build the index of the catalog in THIS process (``kaskadctl site templates-index``; the readers in a
    long-lived process go through ensure_index). Nothing to do when it is fresh. The catalog's sha256 is checked
    first; the file is written next to the index and renamed into place. -> {"built": bool, "total", "categories"}.
    CatalogMissing when the catalog is not there or is damaged."""
    with _build_lock():
        src = catalog_path()
        sig = _file_sig(src)
        if sig is None:
            raise _missing()
        try:
            with open(index_path(), "rb") as f:
                hdr = _read_header(f)[0]
        except OSError:
            hdr = None
        if _fresh(hdr, sig):
            return {"built": False, "total": hdr["total"], "categories": len(hdr["categories"])}
        if not _file_ok(src):
            raise _damaged()
        try:
            with open(src, "rb") as f:
                text = f.read(CATALOG_MAX + 1).decode("utf-8")      # bytes freed before the parse
            doc = json.loads(text, object_pairs_hook=_compact)
            del text
        except (OSError, ValueError, UnicodeDecodeError):
            raise _damaged(tr("site_tpl.catalog.unreadable"))
        cats, recs = _catalog_records(doc)
        del doc
        body, offs, keys, pos = [], [], [], 0
        for i, rec in enumerate(recs):
            line = _line(rec)
            body.append(line)
            offs.append(_OFF.pack(pos))
            keys.append((short_key(rec[_I_ID]).encode("ascii"), i))
            pos += len(line)
        del recs
        keys.sort()
        hdr = {"format": INDEX_FORMAT, "src_sha": CATALOG_SHA256, "src_size": sig[0], "src_mtime_ns": sig[1],
               "total": len(offs), "categories": cats, "offs": pos, "keys": pos + _OFF.size * len(offs)}
        d = paths.assets_dir()
        fd, tmp = tempfile.mkstemp(prefix=".%s." % INDEX_NAME, dir=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(MAGIC)
                f.write(json.dumps(hdr, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                f.writelines(body)
                f.writelines(offs)
                f.writelines(_KEY.pack(k, i) for k, i in keys)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o644)
            os.rename(tmp, index_path())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {"built": True, "total": hdr["total"], "categories": len(cats)}


def _apart():
    return bool(os.environ.get("INVOCATION_ID")) if BUILD_APART is None else bool(BUILD_APART)


def _run_apart(runner, fetch):
    """``kaskadctl site templates-index [--fetch] --json`` as a separate process: under systemd in a scope of its own
    (``systemd-run --scope``: its memory is not the caller's cgroup). Its error comes back as the same KaskadError."""
    from .. import sysrun
    runner = runner or sysrun.default_runner()
    ctl, env = paths.kaskadctl(), {"PYTHONIOENCODING": "utf-8"}          # its JSON is Russian; LC_ALL=C of runners
    if os.path.isfile(ctl):
        argv = [ctl]
    else:                                   # a checkout (tests, a run from the repository)
        lib = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        argv = [sys.executable, "-m", "kaskad"]
        env["PYTHONPATH"] = lib + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
        for k in ("KASKAD_ROOT", "KASKAD_ASSETS_ROOTS"):
            if k in os.environ:
                env[k] = os.environ[k]
    argv += ["site", "templates-index", "--json"] + (["--fetch"] if fetch else [])
    if os.environ.get("INVOCATION_ID") and runner.which("systemd-run"):
        argv = ["systemd-run", "--scope", "--quiet", "--collect",
                "--description=Kaskad 3: site template catalog index"] + argv   # a unit caption: plain English
    res = runner.run(argv, timeout=BUILD_TIMEOUT, env=env)
    doc = None
    for line in reversed((res.out or "").strip().splitlines()):
        try:
            doc = json.loads(line)
            break
        except ValueError:
            continue
    if res.ok and isinstance(doc, dict) and doc.get("ok"):
        return doc
    if isinstance(doc, dict) and doc.get("error"):
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        cls = CatalogMissing if data.get("reason") in ("catalog_missing", "catalog_damaged") else KaskadError
        raise cls(str(doc["error"]), code=doc.get("code") or 1, data=data)
    tail = util.redact(((res.err or "") + " " + (res.out or "")).strip()[-200:])
    raise KaskadError(tr("site_tpl.index.failed", rc=res.rc, tail=(": " + tail) if tail else "", hint=UPDATE_HINT),
                      data={"reason": "index_failed"})


def ensure_index(runner=None):
    """The index is fresh — nothing; else it is built: apart under systemd, here elsewhere. CatalogMissing when the
    catalog is not on the node (nothing is spawned for that)."""
    if index_fresh():
        return
    if _file_sig(catalog_path()) is None:
        raise _missing()
    if _apart():
        _run_apart(runner, fetch=False)
    else:
        build_index()


def prepare(runner=None):
    """The catalog and its index on the node, for a screen that lists templates (panel, wizard, bot): fresh — nothing
    at all; else ``fetch_catalog`` (disk, checkout, GitHub with the key) and the build — apart under systemd, so the
    14 MB of the catalog never pass through the caller. KaskadError of fetch_catalog (reason nokey | bad | net |
    missing | sha) or CatalogMissing."""
    if index_fresh():
        return
    if _apart():
        _run_apart(runner, fetch=True)
    else:
        fetch_catalog(runner)
        build_index()


def iter_records(category=None):
    """Every record (9-tuple, the order of the catalog), of ``category`` only when it is given."""
    with _open_index() as ix:
        start, n = 0, ix.total
        if category:
            c = next((x for x in ix.cats if x.get("id") == category), None)
            start, n = (int(c["start"]), int(c["count"])) if c else (0, 0)
        for line in ix.lines(start, start + n):
            yield _parse(line)


def _item(rec):
    owner = re.match(r"^https://github\.com/([^/]+)/", rec[_I_REPO])
    return {"id": rec[_I_ID], "name": rec[_I_NAME] or rec[_I_ID], "category": rec[_I_CAT], "source": rec[_I_SRC],
            "license": rec[_I_LIC] or None, "preview": rec[_I_PREV] or None, "description": rec[_I_DESC] or None,
            "repo": rec[_I_REPO] or None,
            # whom the preview thanks: the owner of the repository for the common source «github» (goAmnezia)
            "author": (owner.group(1) if owner and rec[_I_SRC] == "github" else rec[_I_SRC]) or None}


def catalog():
    """-> {"categories": [{"id", "title", "icon", "count"}], "total"} — categories with templates, in the order of
    the catalog. CatalogMissing when the catalog is not on the node."""
    with _open_index() as ix:
        cats = [{"id": c.get("id"), "title": c.get("title"), "icon": c.get("icon"), "count": c.get("count")}
                for c in ix.cats if c.get("count")]
        return {"categories": cats, "total": ix.total}


def _find(ix, template_id):
    if not isinstance(template_id, str) or not template_id:
        return None
    for i in ix.by_key(short_key(template_id)):
        rec = ix.record(i)
        if rec[_I_ID] == template_id:
            return rec
    return None


def _record(template_id):
    """The 9-tuple of ``template_id`` or None."""
    if not isinstance(template_id, str) or not template_id:
        return None
    with _open_index() as ix:
        return _find(ix, template_id)


def get(template_id):
    """The record of ``template_id`` (as in ``search``) or None."""
    rec = _record(template_id)
    return None if rec is None else _item(rec)


def short_key(template_id):
    """sha1(id)[:12] — for callback_data of Telegram (64 bytes; ids are up to 120 characters)."""
    return hashlib.sha1(template_id.encode("utf-8")).hexdigest()[:12]


def by_short_key(key):
    """The id of a ``short_key`` or None."""
    if not isinstance(key, str) or not _SHORT_RE.match(key):
        return None
    with _open_index() as ix:
        found = ix.by_key(key)
        return ix.record(found[0])[_I_ID] if found else None


def norm_query(query):
    """A query as search() takes it: spaces squeezed, at most QUERY_MAX characters; '' for none."""
    q = " ".join(str(query or "").split())
    return q[:QUERY_MAX]


def search(category=None, query=None, page=1, per_page=PER_PAGE):
    """Templates of ``category`` (None — all) whose name or id has ``query`` (any case, ё = е), then those whose
    description has it (descriptions are mostly Russian: «ресторан», «портфолио»); pages of ``per_page``.
    A page past the end is the last one (as the menu of goAmnezia). -> {"items", "page", "pages", "total",
    "category", "query"}. KaskadError (USAGE) for an unknown category.
    Reads the index: a page without a query is one seek; a query goes through the lines of the category once and
    keeps only the numbers of the matches."""
    try:
        per = max(1, min(100, int(per_page)))
    except (TypeError, ValueError):
        per = PER_PAGE
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    q = norm_query(query)
    with _open_index() as ix:
        start, n = 0, ix.total
        if category:
            c = next((x for x in ix.cats if x.get("id") == category), None)
            if c is None:
                raise KaskadError(tr("site_tpl.unknown_category", category=category,
                                     categories=", ".join(x["id"] for x in ix.cats if x.get("count"))), code=USAGE,
                                  data={"reason": "unknown_category"})
            start, n = int(c["start"]), int(c["count"])
        if q:
            w = _fold(q).encode("utf-8")
            first, second = array.array("l"), array.array("l")
            for i, line in enumerate(ix.lines(start, start + n), start):
                tail = line.rsplit(b"\t", 2)            # the folded «name\x1fid» and description (utf-8 bytes)
                if w in tail[1]:
                    first.append(i)
                elif w in tail[2]:
                    second.append(i)
            total = len(first) + len(second)
        else:
            total = n
        pages = max(1, (total + per - 1) // per)
        page = max(1, min(page, pages))
        lo, hi = (page - 1) * per, min(total, page * per)
        if q:
            nums = [first[k] if k < len(first) else second[k - len(first)] for k in range(lo, hi)]
            recs = [ix.record(i) for i in nums]
        else:
            recs = ix.run(start + lo, hi - lo)
        items = [_item(r) for r in recs]
    return {"items": items, "page": page, "pages": pages, "per_page": per, "total": total,
            "category": category or None, "query": q or None}


def _github_io(rec):
    """A personal page of GitHub Pages: the repository is named *.github.io (and goAmnezia's rule by the id:
    gh_<user>_<user>_github_io)."""
    repo = rec[_I_REPO].rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if repo.rsplit("/", 1)[-1].lower().endswith(".github.io"):
        return True
    p = rec[_I_ID].split("_")
    return len(p) == 5 and p[1] == p[2] and p[3] == "github" and p[4] == "io"


def in_pool(rec):
    """The default pool (§5): not «portfolio» (people's CVs), not *.github.io (someone's personal page — their name,
    mail and photo on our domain; goAmnezia LC-13) and only with a licence."""
    return rec[_I_CAT] != "portfolio" and bool(rec[_I_LIC]) and not _github_io(rec)


def pick_random(exclude=()):
    """A random id of the default pool, except ``exclude`` and the templates known as removed; None when nothing is
    left."""
    ex = set(exclude or ()) | tplhealth.gone_ids()
    pool = [r[_I_ID] for r in iter_records() if in_pool(r) and r[_I_ID] not in ex]
    return _rng.choice(pool) if pool else None


# ── the page rules of goAmnezia (templates_lib.sh), one to one ─────────────

_FIRST_DIRS = ("dist", "build", ".", "html", "theme", "live", "public_html", "site", "_site")
_SKIP_FIRST = ("*/documentation/*", "*/docs/*", "*/src/*", "*/public/*", "*/node_modules/*", "*/.github/*",
               "*/test/*", "*/tests/*", "*/example*", "*/.git/*")
_SKIP_SECOND = ("*/documentation/*", "*/src/*", "*/node_modules/*", "*/.github/*", "*/.git/*")
_HOME_PAGES = ("home.html", "main.html", "Home.html")


def _is_file(p):
    """A regular file (``find -type f``; symlinks are gone before anything looks)."""
    try:
        return stat.S_ISREG(os.lstat(p).st_mode)
    except OSError:
        return False


def _index_files(base):
    """index.html at depth 2..5 under ``base`` (``find -mindepth 2 -maxdepth 5``), as paths relative to it."""
    out = []
    for d, dirs, files in os.walk(base):
        rel = os.path.relpath(d, base)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(x for x in dirs if x != ".git") if depth < 4 else []
        if depth >= 1 and "index.html" in files and _is_file(os.path.join(d, "index.html")):
            out.append((rel.replace(os.sep, "/") + "/index.html"))
    return out


def pick_index_dir(base):
    """``_tpl_pick_index_dir``: the directory of the page — a known output folder with index.html, else the shallowest
    index.html away from docs, sources and examples, else the same allowing docs/public/tests/examples, else
    home.html|main.html|Home.html copied to index.html. None when there is no page."""
    for d in _FIRST_DIRS:
        p = os.path.normpath(os.path.join(base, d))
        if _is_file(os.path.join(p, "index.html")):
            return p
    found = _index_files(base)
    for skip in (_SKIP_FIRST, _SKIP_SECOND):
        cands = [r for r in found if not any(fnmatch.fnmatchcase("/" + r, pat) for pat in skip)]
        if cands:
            best = min(cands, key=lambda r: (r.count("/"), r))
            return os.path.dirname(os.path.join(base, best))
    for name in _HOME_PAGES:
        if _is_file(os.path.join(base, name)):
            try:
                shutil.copyfile(os.path.join(base, name), os.path.join(base, "index.html"))
            except OSError:
                return None
            return os.path.normpath(base)
    return None


_PUBLIC_URL = b"%PUBLIC_URL%"
_SRC_ENTRY = re.compile(rb'src="\.?/?src/[^"\n]*\.(jsx?|tsx?)"')
_SCRIPT = re.compile(rb"<script[^>]*>.*</script>")
_STYLE = re.compile(rb"<style[^>]*>.*</style>")
_TAG = re.compile(rb"<[^>]*>")
_BLANKS = re.compile(rb"[ \t\n]+")
_MEDIA = re.compile(rb"<img|background(-image)?:", re.I)
_REF = re.compile(rb'(href|src)="[^"#?:\n]+\.(css|js)(\?[^"\n]*)?"', re.I)
TEXT_MIN = 300


def visible_text_len(data):
    """Bytes of the visible text of a page as goAmnezia counts them: per line ``sed`` cuts <script>…</script> and
    <style>…</style> on one line and turns tags into blanks, ``tr -s ' \\t\\n' ' '`` squeezes, ``wc -c``."""
    lines = [_TAG.sub(b" ", _STYLE.sub(b"", _SCRIPT.sub(b"", ln))) for ln in data.split(b"\n")]
    return len(_BLANKS.sub(b" ", b"\n".join(lines)))


def local_refs(data, limit=20):
    """The first ``limit`` local css/js of a page (``grep -oiE '(href|src)="…\\.(css|js)…"'`` without //host)."""
    out = []
    for m in _REF.finditer(data):
        v = m.group(0)
        v = v[v.index(b'="') + 2:-1].split(b"?", 1)[0]
        if v.startswith(b"//"):
            continue
        out.append(v)
        if len(out) >= limit:
            break
    return out


def page_problem(d):
    """``_tpl_page_ok``: None when ``d``/index.html is a ready page, else the reason: no_index | app_shell |
    broken_assets."""
    idx = os.path.join(d, "index.html")
    if not _is_file(idx):
        return "no_index"
    try:
        with open(idx, "rb") as f:
            data = f.read()
    except OSError:
        return "no_index"
    if _PUBLIC_URL in data or _SRC_ENTRY.search(data):
        return "app_shell"
    if visible_text_len(data) < TEXT_MIN and not _MEDIA.search(data):
        return "app_shell"
    refs = local_refs(data)
    base = os.fsencode(d)
    missing = sum(1 for r in refs if not _is_file(os.path.join(base, r[1:] if r.startswith(b"/") else r)))
    if len(refs) >= 2 and missing == len(refs):
        return "broken_assets"
    return None


# ── clean-up of a template (§5) ─────────────────────────────────────────────

_DOC_NAMES = ("readme", "license", "licence", "changelog", "contributing", "security", "code_of_conduct")
_DOC_EXT = ("", ".md", ".markdown", ".txt", ".rst", ".adoc", ".mdx")
_SERVICE_FILES = frozenset([n + e for n in _DOC_NAMES for e in _DOC_EXT] + [n.lower() for n in (
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.json", "composer.lock",
    "gulpfile.js", "gulpfile.babel.js", "Gruntfile.js", "webpack.config.js", "webpack.mix.js", "vite.config.js",
    "tailwind.config.js", "postcss.config.js", ".gitignore", ".gitattributes", ".editorconfig", ".eslintrc",
    ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.yml", ".eslintrc.yaml", ".prettierrc",
    ".prettierrc.json", ".prettierrc.js", ".prettierrc.yml", ".prettierrc.yaml", ".stylelintrc",
    ".stylelintrc.json", ".travis.yml", ".nojekyll", ".htaccess", ".DS_Store", "Thumbs.db")])
_SERVICE_DIRS = (".git", ".github", ".sass-cache", "node_modules", ".vscode", ".idea")


def remove_symlinks(top):
    """Every symlink under ``top`` goes (a foreign repository: «dl.dat -> /etc/kaskad/secrets.json» would be served
    by nginx, and writing through a link would write as root anywhere). -> how many."""
    n = 0
    for d, dirs, files in os.walk(top):
        for name in dirs + files:
            p = os.path.join(d, name)
            if os.path.islink(p):
                os.unlink(p)
                n += 1
    return n


def strip_service_files(root):
    """``strip_template_service_files`` with exact names: README.md, LICENSE, LICENSE.txt, package.json,
    .gitignore… (case-insensitive) and any *.md in the root and one level below; the directories .git, .github,
    node_modules… of the root. Pages (license.html, security.html, changelog.html) stay: the site links to them."""
    removed = []
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        if rel != "." and os.sep in rel:
            dirs[:] = []
            continue
        if rel == ".":
            for name in list(dirs):
                if name in _SERVICE_DIRS:
                    shutil.rmtree(os.path.join(d, name), ignore_errors=True)
                    dirs.remove(name)
                    removed.append(name + "/")
        else:
            dirs[:] = []
        for name in files:
            low = name.lower()
            if low in _SERVICE_FILES or low.endswith(".md"):
                p = os.path.join(d, name)
                if _is_file(p):
                    os.unlink(p)
                    removed.append(os.path.relpath(p, root))
    return removed


# _ga_site_strip_trackers of goAmnezia lib/website.sh, as is
_HOSTS = r'(googletagmanager\.com|google-analytics\.com|mc\.yandex\.ru|connect\.facebook\.net|static\.hotjar\.com|clarity\.ms)'
_CALLS = r'(gtag\(|dataLayer\.push|ga\(\s*[\'"]create|ym\(\s*\d|fbq\(|' + _HOSTS + r')'
_T_SCRIPT_SRC = re.compile(r'<script\b[^>]*\bsrc=["\'][^"\']*' + _HOSTS + r'[^"\']*["\'][^>]*>\s*</script>', re.I)
_T_SCRIPT_INLINE = re.compile(r'<script\b[^>]*>(?:(?!</script>).)*?' + _CALLS + r'(?:(?!</script>).)*?</script>',
                              re.I | re.S)
_T_NOSCRIPT = re.compile(r'<noscript>(?:(?!</noscript>).)*?(googletagmanager\.com|mc\.yandex\.ru|facebook\.com/tr)'
                         r'(?:(?!</noscript>).)*?</noscript>', re.I | re.S)
_T_META_URL = re.compile(r'<(?:link|meta)\b[^>]*(?:rel=["\']canonical["\']|property=["\']og:url["\'])[^>]*>', re.I)


def strip_trackers(root):
    """Counters and links to the original out of the pages: gtag, GTM, analytics.js, Метрика, Facebook Pixel,
    Hotjar, Clarity and their <noscript>; rel=canonical and og:url; sitemap.xml. The rest of the pages (the authors'
    credits in the footer too) stays. -> the files changed or removed."""
    changed = []
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            low = f.lower()
            if low == "sitemap.xml":
                try:
                    os.remove(p)
                    changed.append(os.path.relpath(p, root))
                except OSError:
                    pass
                continue
            if not low.endswith((".html", ".htm")):
                continue
            try:
                with open(p, encoding="utf-8", errors="surrogateescape") as fh:
                    s = fh.read()
            except OSError:
                continue
            t = _T_META_URL.sub("", _T_NOSCRIPT.sub("", _T_SCRIPT_INLINE.sub("", _T_SCRIPT_SRC.sub("", s))))
            if t != s:
                with open(p, "w", encoding="utf-8", errors="surrogateescape") as fh:
                    fh.write(t)
                changed.append(os.path.relpath(p, root))
    return changed


# ── download of one template (download_template of goAmnezia) ──────────────

class _Fail(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def repo_url_ok(u):
    """``_tpl_repo_url_ok``: https://, at most 512 characters, no blanks or shell characters."""
    return isinstance(u, str) and 0 < len(u) <= 512 and u.startswith("https://") and not _REPO_BAD.search(u)


def _git(runner, argv, deadline):
    """A git call within the deadline of the template: GNU ``timeout`` kills the whole group (git-remote-https too),
    the runner's own timeout is the net under it. -> Result; _Fail("timeout") when the time is over."""
    left = int(deadline - _now())
    if left <= 0:
        raise _Fail("timeout")
    cmd = ["env", "GIT_TERMINAL_PROMPT=0", "git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
           "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never"] + argv
    if runner.which("timeout"):
        cmd = ["timeout", "-k", "5", str(left)] + cmd
    res = runner.run(cmd, timeout=left + 15)
    if res.rc in (124, 137) or _now() >= deadline:
        raise _Fail("timeout")
    return res


def _clone(runner, url, dest, deadline, extra=()):
    os.makedirs(dest, 0o700, exist_ok=True)
    return _git(runner, ["clone", "--depth", "1"] + list(extra) + ["--", url, dest], deadline)


def _tree_size(top):
    n = 0
    for d, dirs, files in os.walk(top):
        if d == top:
            dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n


def _err_tail(res):
    lines = [ln.strip() for ln in (res.err or "").splitlines()
             if ln.strip() and not ln.startswith(("Cloning into", "Клонирование"))]  # i18n: skip — git
    return util.redact(" | ".join(lines[-3:]))


def _copy_into(src, dst):
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)


def download(runner, rec, work):
    """Clone the template of ``rec`` into ``work`` and leave its page in ``work``/site. -> that directory.
    _Fail(code) with the codes of goAmnezia (download_failed, no_index, app_shell, broken_assets) and ours (timeout,
    too_big, bad_repo)."""
    tid, source, url, sparse = rec[_I_ID], rec[_I_SRC], rec[_I_REPO], rec[_I_SPARSE]
    if not repo_url_ok(url):
        raise _Fail("bad_repo")
    site = os.path.join(work, "site")
    tmp = os.path.join(work, "clone")
    for p in (site, tmp):
        shutil.rmtree(p, ignore_errors=True)
    os.makedirs(site, 0o700)
    deadline = _now() + CLONE_TIMEOUT
    cloned, err = False, ""

    def settle():
        """After git: no symlinks, the size limit, «did anything come at all» (goAmnezia _tpl_mark_cloned)."""
        remove_symlinks(tmp)
        if _tree_size(tmp) > MAX_BYTES:
            raise _Fail("too_big")
        try:
            return any(n != ".git" for n in os.listdir(tmp))
        except OSError:
            return False

    if source in ("html5up", "learning-zone", "dawidolko"):
        # one big repository with a folder per template: sparse, else a full clone
        if not sparse or not _SPARSE_RE.match(sparse) or ".." in sparse.split("/"):
            raise _Fail("bad_repo")
        res = _clone(runner, url, tmp, deadline, ("--filter=blob:none", "--sparse"))
        if not res.ok:
            err = _err_tail(res)
            shutil.rmtree(tmp, ignore_errors=True)
            res = _clone(runner, url, tmp, deadline)
            if not res.ok:
                err = _err_tail(res) or err
        if os.path.isdir(tmp):
            _git(runner, ["-C", tmp, "sparse-checkout", "set", sparse], deadline)
            cloned = settle()
            if os.path.isdir(os.path.join(tmp, sparse)) and not os.path.islink(os.path.join(tmp, sparse)):
                _copy_into(os.path.join(tmp, sparse), site)
    elif source in ("startbootstrap", "themewagon", "colorlib", "github"):
        # a repository per template: the page at its root or in dist/build/… (pick_index_dir)
        res = _clone(runner, url, tmp, deadline)
        if not res.ok:
            err = _err_tail(res)
        if os.path.isdir(tmp):
            cloned = settle()
            shutil.rmtree(os.path.join(tmp, ".git"), ignore_errors=True)
            d = pick_index_dir(tmp)
            if d:
                _copy_into(d, site)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(os.path.join(site, ".git"), ignore_errors=True)
    if _is_file(os.path.join(site, "index.html")):
        code = page_problem(site)
        if code is None:
            return site
        raise _Fail(code)
    # the page in a subfolder (a non-standard layout)
    d = pick_index_dir(site)
    if d and os.path.normpath(d) != os.path.normpath(site):
        _copy_into(d, site)
        if page_problem(site) is None:
            return site
    # an empty directory — the repository did not come (network, GitHub, removed); files without a page — sources
    raise _Fail("no_index" if cloned else "download_failed", "" if cloned else err)


# ── the root: atomic swap, backup, ownership ───────────────────────────────

_FORBIDDEN_ROOTS = ("/", "/var", "/var/www", "/etc", "/opt", "/root", "/home", "/usr", "/srv", "/tmp", "/boot",
                    "/bin", "/sbin", "/lib", "/dev", "/proc", "/sys", "/run")


def _check_root(root):
    r = os.path.normpath(root or "")
    base = paths.root()
    plain = r[len(base):] if base and r.startswith(base + "/") else r
    if not os.path.isabs(r) or plain in _FORBIDDEN_ROOTS or plain.count("/") < 2:
        raise KaskadError(tr("site_tpl.root.bad", root=root, example=paths.site_root()), code=USAGE)
    if os.path.islink(r):
        raise KaskadError(tr("site_tpl.root.symlink", root=r))
    return r


def _has_site(root):
    return _is_file(os.path.join(root, "index.html"))


def _not_empty(d):
    try:
        return bool(os.listdir(d))
    except OSError:
        return False


def _exchange(a, b):
    """Swap two directories in one step (renameat2 RENAME_EXCHANGE, Linux 3.15+, glibc 2.28+). False — not here."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError, ImportError):
        return False
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    at_fdcwd, rename_exchange = -100, 2
    if fn(at_fdcwd, os.fsencode(a), at_fdcwd, os.fsencode(b), rename_exchange) == 0:
        return True
    e = ctypes.get_errno()
    if e in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
        return False
    raise OSError(e, os.strerror(e), b)


def _swap(new, root, old):
    """Put ``new`` at ``root``; the previous root (if any) ends at ``old``. On failure the root is as it was."""
    if not os.path.lexists(root):
        os.rename(new, root)
        return False
    if _exchange(new, root):
        os.rename(new, old)
        return True
    os.rename(root, old)
    try:
        os.rename(new, root)
    except OSError:
        os.rename(old, root)
        raise
    return True


def _keep_backup(old, warnings):
    """The previous site to site_backup_dir()/previous — one copy: the older one goes only after this one is
    there. -> the path or None."""
    if not _not_empty(old):
        shutil.rmtree(old, ignore_errors=True)
        return None
    bdir = paths.site_backup_dir()
    dst = os.path.join(bdir, BACKUP_NAME)
    try:
        os.makedirs(os.path.dirname(bdir), 0o700, exist_ok=True)
        util.ensure_dir(bdir, 0o700)
        incoming = tempfile.mkdtemp(prefix=".incoming.", dir=bdir)
        os.rmdir(incoming)
        shutil.move(old, incoming)
        if os.path.lexists(dst):
            gone = dst + ".gone"
            shutil.rmtree(gone, ignore_errors=True)
            os.rename(dst, gone)
            shutil.rmtree(gone, ignore_errors=True)
        os.rename(incoming, dst)
        return dst
    except OSError as e:
        warnings.append(tr("site_tpl.backup_failed", dir=bdir, err=e.strerror or e))
        shutil.rmtree(old, ignore_errors=True)
        return None


def _owner():
    """uid, gid of www-data (nginx on other systems) when we are root, else None."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None
    import grp
    import pwd
    for name in ("www-data", "nginx"):
        try:
            return pwd.getpwnam(name).pw_uid, grp.getgrnam(name).gr_gid
        except KeyError:
            continue
    return None


def _finish_tree(top):
    """Directories 0755, files 0644, owner www-data (goAmnezia: chown www-data, chmod -R 755)."""
    own = _owner()
    for d, dirs, files in os.walk(top):
        os.chmod(d, 0o755)
        if own:
            os.chown(d, own[0], own[1], follow_symlinks=False)
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p):
                os.unlink(p)
                continue
            os.chmod(p, 0o644)
            if own:
                os.chown(p, own[0], own[1], follow_symlinks=False)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _domain():
    """The domain of the node (state.json → site.domain, written by 4.0-B) for the title of the stub, or ''."""
    try:
        with open(paths.state(), encoding="utf-8") as f:
            d = ((json.load(f) or {}).get("site") or {}).get("domain") or ""
    except (OSError, ValueError, AttributeError):
        return ""
    d = str(d).strip().lower().rstrip(".")
    return d if _DOMAIN_RE.match(d) else ""


def stub_html(title=""):
    """The built-in stub: one plain page, no outside requests (goAmnezia _ga_site_deploy_stub)."""
    t = title if _DOMAIN_RE.match(title or "") else "Welcome"
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<style>\n"
            "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f4f6f8;"
            "color:#1f2933;font:17px/1.6 -apple-system,\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif}\n"
            "main{max-width:34rem;padding:2rem;text-align:center}h1{font-size:2rem;margin:0 0 .75rem;font-weight:600}\n"
            "p{margin:0;color:#52606d}\n</style>\n</head>\n<body>\n<main>\n<h1>%s</h1>\n"
            "<p>We are preparing something new here. Please check back a little later.</p>\n"
            "</main>\n</body>\n</html>\n") % (t, t)


# ── git and the record of packages ──────────────────────────────────────────

def pkgs_file():
    return os.path.join(paths.etc(), "site", "pkgs")


def record_pkg(name):
    """One line per package Kaskad installed for the site (4.0-B writes certbot here too): the removal reads it."""
    p = pkgs_file()
    lines = [ln.strip() for ln in (util.read_text(p) or "").splitlines() if ln.strip()]
    if name in lines:
        return False
    os.makedirs(os.path.dirname(p), 0o755, exist_ok=True)
    util.atomic_write(p, "\n".join(lines + [name]) + "\n", 0o644)
    return True


def ensure_git(runner):
    """git for the clone — installed from apt only now, when a template is wanted (the owner's rule). -> True when
    Kaskad installed it. KaskadError when apt could not."""
    if runner.which("git"):
        return False
    from ..gw import sys as gsys
    got = gsys.apt_install(runner, [PKG])
    if PKG in got:
        record_pkg(PKG)
        util.log_tr("site_tpl.log.git")
        return True
    return False


# ── deploy ──────────────────────────────────────────────────────────────────

class _RootLock(object):
    """One change of the site at a time (the menu, the panel and the bot may ask at once)."""

    def __enter__(self):
        util.ensure_run_dir()
        self.fd = os.open(os.path.join(paths.run_dir(), "site-template.lock"),
                          os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise LockedError(tr("site_tpl.locked"))
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def _text(code, tid):
    return text(code, tid)


def _free_ok(where):
    try:
        return shutil.disk_usage(where).free >= MIN_FREE
    except OSError:
        return True


def _build(work, src_dir, tid, source, check=True):
    """The new site in ``work``/new from the page in ``src_dir``: clean-up, markers, rights. -> its path.
    ``check`` — the page must still be ready after the clean-up (not for the stub: it is short on purpose)."""
    new = os.path.join(work, "new")
    os.rename(src_dir, new)
    remove_symlinks(new)
    strip_service_files(new)
    strip_trackers(new)
    for name in (MARK_ID, MARK_SOURCE, ".custom_git_source"):
        if os.path.lexists(os.path.join(new, name)):
            os.unlink(os.path.join(new, name))
    _write(os.path.join(new, MARK_ID), tid + "\n")
    if source:
        _write(os.path.join(new, MARK_SOURCE), source + "\n")
    if check and page_problem(new) is not None:       # the clean-up must leave a page
        raise _Fail("no_index")
    _finish_tree(new)
    return new


def _put(work, new, root, warnings):
    """``new`` becomes ``root``; the previous site — to the backup. -> the backup path or None."""
    old = os.path.join(work, "old")
    had = _swap(new, root, old)
    return _keep_backup(old, warnings) if had else None


def _try_one(runner, c, tid, root, parent, warnings):
    """Download, check and put template ``tid`` in place. -> backup path; _Fail on a template error."""
    rec = _record(tid)
    if not _free_ok(parent):
        raise KaskadError(tr("site_tpl.low_disk", mb=MIN_FREE // (1024 * 1024)), data={"reason": "low_disk"})
    work = tempfile.mkdtemp(prefix=".kaskad-site-work.", dir=parent)
    try:
        site = download(runner, rec, work)
        new = _build(work, site, tid, rec[_I_REPO])
        return _put(work, new, root, warnings)
    except OSError as e:
        raise KaskadError(tr("site_tpl.fs", tid=tid, root=root, err=e.strerror or e), data={"reason": "fs"})
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _deploy_stub(root, parent, warnings):
    work = tempfile.mkdtemp(prefix=".kaskad-site-work.", dir=parent)
    try:
        src = os.path.join(work, "site")
        os.makedirs(src, 0o700)
        _write(os.path.join(src, "index.html"), stub_html(_domain()))
        new = _build(work, src, STUB_ID, "", check=False)
        return _put(work, new, root, warnings)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def deploy(runner, template_id=None, root=None):
    """Put a template of the catalog into the web root (``root`` None — paths.site_root()).

    ``template_id`` given: that template, one try; a failure leaves the site as it was ->
    ``{"ok": False, "id", "reason", "text", ...}``. None: a random one of the pool (pick_random), up to MAX_TRIES;
    nothing worked and there is no site in the root yet — the built-in stub (``fallback``: True); a working site is
    never replaced by a stub. The catalog and git are brought here when missing (fetch_catalog, apt).

    -> {"ok", "id", "name", "source", "fallback", "backup", "tries", "errors": [{"id", "reason", "text"}],
        "warnings", "root"}. KaskadError: another change runs (LockedError), a bad root, an unknown or malformed id,
    no catalog or no git for an explicit id, too little disk."""
    root = _check_root(root or paths.site_root())
    parent = os.path.dirname(root)
    if template_id is not None and (not isinstance(template_id, str) or not ID_RE.match(template_id)
                                    or template_id in (".", "..")):
        raise KaskadError(tr("site_tpl.bad_id", tid=template_id), code=USAGE, data={"reason": "unknown_template"})
    out = {"ok": False, "id": template_id, "name": None, "source": None, "fallback": False, "backup": None,
           "tries": 0, "errors": [], "warnings": [], "root": root}
    warnings = out["warnings"]
    with _RootLock():
        os.makedirs(parent, 0o755, exist_ok=True)
        stub_ok = template_id is None and not _has_site(root)
        dead = False
        try:
            prepare(runner)                         # the catalog and its index; parsed apart, never here
            c = True
            if template_id is not None and _record(template_id) is None:
                raise KaskadError(_text("unknown_template", template_id), code=USAGE,
                                  data={"reason": "unknown_template"})
            dead = template_id is not None and tplhealth.is_gone(template_id)
            if not dead:
                ensure_git(runner)
        except KaskadError as e:
            if not stub_ok or e.code == USAGE:
                raise
            warnings.append(str(e))
            c = None
        if dead:                                    # known as removed: no clone, no git — the reason at once
            out["errors"].append({"id": template_id, "reason": "gone", "text": _text("gone", template_id)})
            util.log_tr("site_tpl.log.gone", tid=template_id)
        elif c is not None:
            todo = [template_id] if template_id is not None else []
            tried, skipped, probes = [], [], 0
            while True:
                tid = todo.pop(0) if todo else (pick_random(tried + skipped) if template_id is None else None)
                if tid is None or len(tried) >= (1 if template_id is not None else MAX_TRIES):
                    break
                if template_id is None and probes < MAX_TRIES * 3:
                    # «Случайный»: the repository of the candidate first — a removed one is not cloned at all
                    probes += 1
                    if tplhealth.probe_repo(runner, _item(_record(tid))) == "gone":
                        skipped.append(tid)
                        util.log_tr("site_tpl.log.gone_next", tid=tid)
                        continue
                tried.append(tid)
                out["tries"] += 1
                try:
                    out["backup"] = _try_one(runner, c, tid, root, parent, warnings)
                except _Fail as f:
                    code = f.code
                    if code == "download_failed" and tplhealth.git_gone(f.detail):
                        code = "gone"                   # «Repository not found»: removed (or private — not ours)
                        tplhealth.mark(tid, "gone")
                    err = {"id": tid, "reason": code, "text": _text(code, tid)}
                    if f.detail:
                        err["detail"] = f.detail
                    out["errors"].append(err)
                    util.log_tr("site_tpl.log.failed", tid=tid, code=code)
                    continue
                except KaskadError as e:
                    if not stub_ok:
                        raise
                    warnings.append(str(e))
                    break
                rec = _record(tid)
                tplhealth.mark(tid, "alive")
                out.update({"ok": True, "id": tid, "name": rec[_I_NAME] or tid, "source": rec[_I_REPO] or None})
                util.log_tr("site_tpl.log.deployed", tid=tid, root=root)
                return out
        if stub_ok and not _has_site(root):
            out["backup"] = _deploy_stub(root, parent, warnings)
            out.update({"ok": True, "id": STUB_ID, "name": None, "source": None, "fallback": True})
            util.log_tr("site_tpl.log.stub", root=root)
            return out
    last = out["errors"][-1] if out["errors"] else {"reason": "download_failed", "text": warnings[-1] if warnings
                                                    else _text("download_failed", template_id or "?")}
    out.update({"reason": last["reason"], "text": last["text"]})
    return out


def status(root=None):
    """By the markers in the root -> {"id", "source", "deployed_at", "root", "stub", "site", "backup"}: ``site`` —
    the root has index.html; ``backup`` — the path of the previous site or None."""
    root = os.path.normpath(root or paths.site_root())
    tid = (util.read_text(os.path.join(root, MARK_ID)) or "").strip() or None
    src = (util.read_text(os.path.join(root, MARK_SOURCE)) or "").strip() or None
    at = None
    if tid:
        try:
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.stat(os.path.join(root, MARK_ID)).st_mtime))
        except OSError:
            at = None
    b = os.path.join(paths.site_backup_dir(), BACKUP_NAME)
    return {"id": tid, "source": src, "deployed_at": at, "root": root, "stub": tid == STUB_ID,
            "site": _has_site(root), "backup": b if os.path.isdir(b) else None}


# ── kaskadctl site templates | site template set|status ────────────────────

def _cli():
    from .. import cli
    return cli


def _runner():
    from .. import sysrun
    return sysrun.default_runner()


def _fmt_n(n):
    return "{:,}".format(n) if get_lang() == "en" else "{:,}".format(n).replace(",", " ")


HEALTH_MARKS = ("gone", "no_preview", "unknown")        # the words: tr("site_tpl.health.<state>")


def _health_mark(state):
    return " · " + tr("site_tpl.health." + state) if state in HEALTH_MARKS else ""


def cmd_templates(args):
    cli = _cli()
    prepare(_runner())
    cat = catalog()
    res = search(args.category, args.query, args.page)
    # «жив ли шаблон»: --check — проверка строк этой страницы (сеть, до 40 с), без него — что помнит кэш
    ids = [it["id"] for it in res["items"]]
    health = tplhealth.check(_runner(), ids) if getattr(args, "check", False) else tplhealth.cached(ids)
    for it in res["items"]:
        it["health"] = health.get(it["id"])
    doc = dict(res, ok=True, categories=cat["categories"], catalog_total=cat["total"])
    lines = []
    if not args.category and not res["query"]:
        lines.append(tr("site_tpl.list.catalog", n=_fmt_n(cat["total"])))
        for x in cat["categories"]:
            lines.append("  %-14s %-28s %7s" % (x["id"], x["title"], _fmt_n(x["count"])))
        lines.append(tr("site_tpl.list.how"))
    else:
        head = next((x["title"] for x in cat["categories"] if x["id"] == args.category), None) \
            or tr("site_tpl.list.all")
        if res["query"]:
            head += " · " + tr("site_tpl.list.search", query=res["query"])
        lines.append(tr("site_tpl.list.head", head=head, n=_fmt_n(res["total"]), page=res["page"],
                        pages=res["pages"]))
        if not res["items"]:
            lines.append("  " + tr("site_tpl.list.nothing"))
        for i, it in enumerate(res["items"], (res["page"] - 1) * res["per_page"] + 1):
            st = (it.get("health") or {}).get("state")
            lines.append("%5d) %-40s [%s] %s%s" % (i, it["name"][:40], it["source"], it["license"] or "",
                                                  _health_mark(st)))
            show = it["preview"] and st not in ("gone", "no_preview")
            lines.append("       %s%s" % (it["id"], (" · " + tr("site_tpl.list.preview", url=it["preview"]))
                                             if show else ""))
        if res["page"] < res["pages"]:
            lines.append(tr("site_tpl.list.next", page=res["page"] + 1))
        lines.append(tr("site_tpl.list.install"))
    cli.emit(args, doc, lines)
    return 0


def cmd_templates_index(args):
    """The build of the index in this process — what panel, bot and wizard start apart (systemd-run --scope)."""
    cli = _cli()
    if args.fetch:
        fetch_catalog(_runner())
    r = build_index()
    cli.emit(args, dict(r, ok=True), tr("site_tpl.index.built" if r["built"] else "site_tpl.index.fresh",
                                        n=_fmt_n(r["total"]), cats=r["categories"]))
    return 0


def cmd_template_set(args):
    from .. import errors
    from . import ops
    cli = _cli()
    # In site mode the nginx root stays unchanged, but STATUS must immediately
    # reflect the new atomic deployment.  Outside it this remains a harmless
    # way to prepare a cover before enabling nginx.
    res = ops.template_set(_runner(), None if args.random else args.id)
    doc = dict(res)
    if res["ok"] and res["fallback"]:
        lines = [tr("site_tpl.set.fallback"), tr("site_tpl.set.later")]
        lines += ["  · " + e["text"] for e in res["errors"]] + ["  · " + w for w in res["warnings"]]
    elif res["ok"]:
        it = get(res["id"]) or {}
        lines = [tr("site_tpl.set.done", name=res["name"], root=res["root"])]
        if it.get("author"):
            lines.append(tr("site_tpl.set.thanks", author=it["author"]))
        lines += ["  · " + w for w in res["warnings"]]
    else:
        lines = [tr("site_tpl.set.failed", text=res["text"])]
        if len(res["errors"]) > 1:
            lines += ["  · " + e["text"] for e in res["errors"][:-1]]
    if res.get("backup"):
        lines.append(tr("site_tpl.set.backup", path=res["backup"]))
    cli.emit(args, doc, lines)
    return errors.OK if res["ok"] else errors.ERROR


def cmd_template_status(args):
    cli = _cli()
    st = status()
    if not st["site"]:
        msg = tr("site_tpl.status.empty", root=st["root"])
    elif st["stub"]:
        msg = tr("site_tpl.status.stub", root=st["root"])
    elif st["id"]:
        msg = tr("site_tpl.status.template", id=st["id"], source=st["source"] or tr("site_tpl.status.no_source"),
                 at=st["deployed_at"] or "?", root=st["root"])
    else:
        msg = tr("site_tpl.status.own", root=st["root"])
    cli.emit(args, dict(st, ok=True), msg)
    return 0


def register_cli(sub):
    """Commands of the group ``site`` (``sub`` — its subparsers, made by site/cli.py)."""
    add_json_flag = _cli().add_json_flag
    pt = add_json_flag(sub.add_parser("templates", help=tr("site_tpl.help.templates")))
    pt.add_argument("--category", default=None, help=tr("site_tpl.help.category"))
    pt.add_argument("--query", default=None, help=tr("site_tpl.help.query"))
    pt.add_argument("--page", type=int, default=1, help=tr("site_tpl.help.page"))
    pt.add_argument("--check", action="store_true",
                    help=tr("site_tpl.help.check"))
    pt.set_defaults(func=cmd_templates)
    pi = add_json_flag(sub.add_parser("templates-index", help=tr("site_tpl.help.index")))
    pi.add_argument("--fetch", action="store_true", help=tr("site_tpl.help.fetch"))
    pi.set_defaults(func=cmd_templates_index)

    p = sub.add_parser("template", help=tr("site_tpl.help.template"))
    tsub = p.add_subparsers(dest="template_cmd", metavar=tr("site_tpl.help.metavar"))
    tsub.required = True
    ps = add_json_flag(tsub.add_parser("set", help=tr("site_tpl.help.set")))
    g = ps.add_mutually_exclusive_group(required=True)
    g.add_argument("id", nargs="?", default=None, help=tr("site_tpl.help.id"))
    g.add_argument("--random", action="store_true", help=tr("site_tpl.help.random"))
    ps.set_defaults(func=cmd_template_set)
    pst = add_json_flag(tsub.add_parser("status", help=tr("site_tpl.help.status")))
    pst.set_defaults(func=cmd_template_status)
    return sub
