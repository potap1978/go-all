"""Is a template of the masquerade site still there (3.2.21): its repository and its preview, checked before a person
picks it — the catalog is a snapshot, and authors remove repositories (themewagon removed hundreds).

    check(runner, ids)      {id: {"state", "text"}} — the visible page only (≤ MAX_IDS); one ``ls-remote`` per
                            repository of the page (dawidolko keeps 141 templates in one); at most WORKERS checks at
                            once in the whole process (pages of the panel, the bot — one pool), the batch within
                            BATCH_DEADLINE s, a hard term (what did not finish — «unknown»; the threads are daemons:
                            ``kaskadctl … --check`` does not wait for a late git); fresh answers of the cache stay
    cached(ids)             {id: {"state", "text"} | None} — the cache only, no network (GET of the panel, the bot)
    gone_ids() / is_gone(id)  the templates known as removed (pick_random skips them, deploy refuses them at once)
    probe_repo(runner, item)  the repository of one candidate of «Случайный» (marks a removed one)
    mark(id, state)         what the deployment learned: the clone said «Repository not found» — gone; a template that
                            deployed — alive by its repository
    git_gone(text)          the error text of git says the repository is not there (removed, or private: the clone
                            is anonymous, so a private one cannot be installed either)

States: alive — the repository is there (and the preview, or the catalog has none); gone — no repository (or an empty
one); no_preview — the repository is there, the preview is not (the template installs); unknown — the network did not
answer (picking is allowed).

The repository: ``git ls-remote --exit-code -h <url>`` — anonymous (GIT_TERMINAL_PROMPT=0, no credential helper, no
config of the node, only https), GNU ``timeout`` around it and the runner's own timeout under it. Not the REST API of
GitHub: it allows 60 requests an hour. Without git on the node (it comes only with the first deployment) — the same
question over https: ``<url>/info/refs?service=git-upload-pack``, the very request of ls-remote. The preview: HEAD with
redirects (http and https only, never to a loopback, private or link-local address — by the literal or by DNS),
GET of at most 2 KB when HEAD is not allowed. The key of GitHub never goes here.

The cache: paths.site_tpl_health() (0600 in a 0700 directory), written atomically under a lock; alive and no_preview
are kept TTL_ALIVE, gone TTL_GONE, unknown never; old entries are dropped on every write.
"""

import fcntl
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import paths, util
from ..i18n import tr

STATES = ("alive", "gone", "no_preview", "unknown")
TEXTS = dict((s, "site.tplhealth." + s) for s in STATES)      # state -> catalog key (the text — tr() at use)
LS_TIMEOUT = 8                  # s for git ls-remote of one repository
HTTP_TIMEOUT = 6                # s for the preview (and the repository over https without git)
BATCH_DEADLINE = 40             # s for a whole page
WORKERS = 8                     # templates checked at once
MAX_IDS = 25                    # a page of the panel and of the menu
TTL_ALIVE = 7 * 86400
TTL_GONE = 30 * 86400
READ_MAX = 2048                 # bytes of a GET body

_clock = time.time              # tests move the time of the cache
_mono = time.monotonic

_SLOTS = threading.BoundedSemaphore(WORKERS)     # checks at once in this process, every batch together
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"]*")
_NET = ("could not resolve", "failed to connect", "timed out", "connection reset", "connection refused",
        "network is unreachable", "no route to host", "ssl", "tls", "gnutls", "operation too slow")
_GONE = ("not found", "could not read username", "could not read password", "returned error: 404",
         "returned error: 410")


def doc(state):
    s = state if state in TEXTS else "unknown"
    return {"state": s, "text": tr(TEXTS[s])}


# ── the cache ───────────────────────────────────────────────────────────────

def _ttl(rec):
    return TTL_GONE if rec.get("s") == "gone" else TTL_ALIVE


def _fresh(rec, now):
    try:
        at = float(rec.get("at"))
    except (TypeError, ValueError):
        return False
    return rec.get("s") in ("alive", "gone", "no_preview") and at <= now + 60 and now - at < _ttl(rec)


def _read():
    """{id: {"s", "at", "pv"}} of the file as it is (stale ones too); {} when there is none or it is damaged."""
    try:
        with open(paths.site_tpl_health(), encoding="utf-8") as f:
            items = (json.load(f) or {}).get("items")
    except (OSError, ValueError, AttributeError):
        return {}
    return dict((k, v) for k, v in items.items() if isinstance(k, str) and isinstance(v, dict)) \
        if isinstance(items, dict) else {}


def _update(changes):
    """Merge ``changes`` ({id: record}) into the file under its lock; stale entries go."""
    p = paths.site_tpl_health()
    d = os.path.dirname(p)
    os.makedirs(os.path.dirname(d), 0o755, exist_ok=True)
    util.ensure_dir(d, 0o700)
    fd = os.open(p + ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        items = _read()
        items.update(changes)
        now = _clock()
        items = dict((k, v) for k, v in items.items() if _fresh(v, now))
        util.atomic_write(p, json.dumps({"v": 1, "items": items}, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")), 0o600)
    finally:
        os.close(fd)


def _safe_update(changes):
    try:
        _update(changes)
    except OSError as e:                # a read-only or full disk must not break a list or a deployment
        util.log_tr("site.tplhealth.log_cache", error=e.strerror or e)


def cached(ids):
    """{id: doc | None} from the cache only (None — not checked or too old)."""
    items, now = _read(), _clock()
    out = {}
    for tid in ids:
        rec = items.get(tid) if isinstance(tid, str) else None
        out[tid] = doc(rec["s"]) if rec and _fresh(rec, now) else None
    return out


def gone_ids():
    now = _clock()
    return set(k for k, v in _read().items() if v.get("s") == "gone" and _fresh(v, now))


def is_gone(tid):
    rec = _read().get(tid) if isinstance(tid, str) else None
    return bool(rec and rec.get("s") == "gone" and _fresh(rec, _clock()))


def mark(tid, state):
    """What the deployment learned: «gone» (the clone was refused as not found) or «alive» (deployed: the repository is
    there; a known «no_preview» stays — the preview was not looked at)."""
    if not isinstance(tid, str) or state not in ("alive", "gone"):
        return
    rec = {"s": state, "at": _clock(), "pv": state == "gone"}
    if state == "alive":
        old = _read().get(tid)
        if old and old.get("s") == "no_preview" and _fresh(old, _clock()):
            rec = dict(old, at=_clock())
    _safe_update({tid: rec})


# ── the repository ──────────────────────────────────────────────────────────

def git_gone(text):
    """The text of a failed git call says «no such repository» (not the network)."""
    t = _URL.sub(" ", str(text or "")).lower()        # «…/classless-site» is not «ssl»: words, not the address
    if any(m in t for m in _NET):
        return False
    return any(m in t for m in _GONE)


def _classify(rc, err):
    if rc == 0:
        return "ok"
    if rc == 2:                         # --exit-code: no branches — an empty repository, nothing to install
        return "gone"
    if rc in (124, 137, 127):
        return "unknown"
    return "gone" if git_gone(err) else "unknown"


def _url_ok(url):
    from . import templates
    return templates.repo_url_ok(url)


def repo_state(runner, url, timeout=LS_TIMEOUT):
    """"ok" | "gone" | "unknown" for the repository ``url``."""
    if not _url_ok(url):
        return "unknown"
    t = max(1, int(math.ceil(min(timeout, LS_TIMEOUT))))
    if not runner.which("git"):
        return _repo_http(url, min(timeout, HTTP_TIMEOUT))
    cmd = ["env", "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=", "SSH_ASKPASS=", "GIT_CONFIG_NOSYSTEM=1",
           "GIT_CONFIG_GLOBAL=/dev/null", "git", "-c", "credential.helper=", "-c", "protocol.allow=never",
           "-c", "protocol.https.allow=always", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never",
           "ls-remote", "--exit-code", "-h", url]
    if runner.which("timeout"):
        cmd = ["timeout", "-k", "2", str(t)] + cmd
    res = runner.run(cmd, timeout=t + 5)
    return _classify(res.rc, res.err)


def _repo_http(url, timeout):
    """ls-remote without git: the smart-http answer of ``<url>/info/refs`` (anonymous, https only)."""
    u = url.rstrip("/") + "/info/refs?service=git-upload-pack"
    code, body = _http(u, "GET", timeout, ("https",), read=READ_MAX)
    if code is None:
        return "unknown"
    if code in (401, 404, 410):         # GitHub answers a missing repository as a private one: 401
        return "gone"
    if not 200 <= code < 300:
        return "unknown"
    if b"capabilities^{}" in body:      # an empty repository
        return "gone"
    return "ok"


# ── the preview ─────────────────────────────────────────────────────────────

class _Redirect(urllib.request.HTTPRedirectHandler):
    """Redirects only to ``schemes``; HEAD stays HEAD."""

    def __init__(self, schemes):
        self.schemes = schemes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme not in self.schemes:
            raise urllib.error.HTTPError(newurl, code, "redirect to %s" % (scheme or "?"), headers, fp)
        check_target(newurl)
        new = urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if new is not None and req.get_method() == "HEAD":
            new.method = "HEAD"
        return new


_resolve = socket.getaddrinfo          # tests replace it


def _public(ip):
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
                or a.is_unspecified)


def check_target(url):
    """A preview (or a redirect of it) must lead to a public address: not 127.0.0.1, 10/8, 169.254.169.254, ::1… —
    by the literal and by every address of the name. URLError otherwise (the check says «не удалось проверить»)."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host:
        raise urllib.error.URLError("no host")
    try:
        ips = [host] if _is_ip(host) else sorted(set(x[4][0] for x in _resolve(host, None, 0, socket.SOCK_STREAM)))
    except (OSError, UnicodeError) as e:
        raise urllib.error.URLError(e)
    if not ips or not all(_public(ip) for ip in ips):
        raise urllib.error.URLError("not a public address: %s" % host)


def _is_ip(host):
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _open(req, timeout, schemes):
    """The only place that goes to the network (tests replace it). No key, no auth handler, no cookies; only public
    addresses (check_target), redirects too."""
    check_target(req.full_url)
    return urllib.request.build_opener(_Redirect(schemes)).open(req, timeout=timeout)


def _http(url, method, timeout, schemes, read=0):
    """-> (status, first ``read`` bytes) or (None, b"") on a network error."""
    if urllib.parse.urlsplit(url).scheme.lower() not in schemes:
        return None, b""
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0 (compatible; kaskad)",
                                                              "Accept": "*/*"})
    try:
        with _open(req, timeout, schemes) as r:
            body = r.read(read) if read else b""
            return getattr(r, "status", None) or r.getcode(), body
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            e.close()
        except Exception:               # noqa: BLE001 — a fake response may have nothing to close
            pass
        return code, b""
    except Exception:                   # noqa: BLE001 — URLError, timeouts, TLS, a broken answer: «не проверить»
        return None, b""


def preview_state(url, timeout=HTTP_TIMEOUT):
    """"ok" | "gone" | "unknown" for a preview (http/https only)."""
    t = min(timeout, HTTP_TIMEOUT)
    code, _ = _http(url, "HEAD", t, ("http", "https"))
    if code in (405, 501):
        code, _ = _http(url, "GET", t, ("http", "https"), read=READ_MAX)
    if code is None:
        return "unknown"
    if 200 <= code < 300:
        return "ok"
    return "gone" if code in (404, 410) else "unknown"


# ── one template, a page of them ────────────────────────────────────────────

def _slot(end):
    """A place among the WORKERS checks of the process, waited for within the term of the batch."""
    left = end - _mono()
    return left >= 1 and _SLOTS.acquire(timeout=left - 0.5)


def _repo_task(runner, url, end):
    if not _slot(end):
        return "unknown"
    try:
        left = end - _mono()
        return repo_state(runner, url, left) if left >= 1 else "unknown"
    finally:
        _SLOTS.release()


def _preview_task(url, end):
    if not _slot(end):
        return "unknown"
    try:
        left = end - _mono()
        return preview_state(url, left) if left >= 1 else "unknown"
    finally:
        _SLOTS.release()


def _run(tasks, end):
    """{key: result} of ``tasks`` ({key: fn}) run in daemon threads until ``end``; what did not finish is missing.
    The threads are not waited for past the term (a late git ends by its own timeout)."""
    out, lock, threads = {}, threading.Lock(), []

    def one(key, fn):
        try:
            res = fn()
        except Exception:               # noqa: BLE001 — one broken check is «не проверить», not the page
            res = ("unknown", False)
        with lock:
            out[key] = res
    for key, fn in tasks.items():
        t = threading.Thread(target=one, args=(key, fn), name="kaskad-tplcheck")
        t.daemon = True
        t.start()
        threads.append(t)
    for t in threads:
        left = end - _mono()
        if left <= 0:
            break
        t.join(left)
    with lock:
        return dict(out)


class _Once(object):
    """The answer about one repository for every template of the page that lives in it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = False
        self.done = threading.Event()
        self.value = "unknown"


def _item_task(runner, item, end, once):
    """-> (state, preview_checked) of one template: its repository (asked once per address), then its preview."""
    with once.lock:
        first, once.started = not once.started, True
    if first:
        try:
            once.value = _repo_task(runner, item.get("repo") or "", end)
        finally:
            once.done.set()
    else:
        once.done.wait(max(0, end - _mono()))
    repo = once.value if once.done.is_set() else "unknown"
    if repo == "gone":
        return "gone", True
    if repo != "ok":
        return "unknown", False
    if not item.get("preview"):
        return "alive", True
    pv = _preview_task(item["preview"], end)
    return ("no_preview", True) if pv == "gone" else ("alive", pv == "ok")


def _parallel(runner, items, deadline):
    """-> {id: (state, preview_checked)} of what finished within ``deadline``: a thread per template, one ls-remote per
    repository address, at most WORKERS git or HTTP calls at once in the whole process (_SLOTS)."""
    end = _mono() + deadline
    onces = {}
    for it in items:
        onces.setdefault(it.get("repo") or "", _Once())
    return _run(dict((it["id"], (lambda it=it: _item_task(runner, it, end, onces[it.get("repo") or ""])))
                     for it in items), end)


def check(runner, ids, deadline=BATCH_DEADLINE, force=False):
    """{id: doc} for ``ids`` (at most MAX_IDS of them are looked at; unknown ids — «unknown»). Fresh answers of the
    cache stay (a «alive» deployment mark without a look at the preview is asked again); new ones go to the cache."""
    from . import templates
    out, todo, seen = {}, [], set()
    items, now = _read(), _clock()
    for tid in list(ids)[:MAX_IDS]:
        if not isinstance(tid, str) or tid in seen:
            continue
        seen.add(tid)
        it = templates.get(tid)
        if it is None:
            out[tid] = doc("unknown")
            continue
        rec = items.get(tid)
        if not force and rec and _fresh(rec, now) and (rec.get("s") == "gone" or rec.get("pv")):
            out[tid] = doc(rec["s"])
            continue
        todo.append(it)
    if todo:
        got = _parallel(runner, todo, deadline)
        changes, at = {}, _clock()
        for it in todo:
            state, pv = got.get(it["id"], ("unknown", False))
            out[it["id"]] = doc(state)
            if state != "unknown":
                changes[it["id"]] = {"s": state, "at": at, "pv": bool(pv)}
        if changes:
            _safe_update(changes)
    return out


def probe_repo(runner, item):
    """The repository of a candidate of «Случайный»: "ok" | "gone" | "unknown"; a removed one is marked."""
    st = repo_state(runner, item.get("repo") or "")
    if st == "gone":
        mark(item.get("id"), "gone")
    return st
