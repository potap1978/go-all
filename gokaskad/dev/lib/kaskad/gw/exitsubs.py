"""Subscriptions of the exits (docs/k3/SPEC-subs-backup.md §1): a link of a subscription is a source of keys of the pool
«Прокси для API Telegram»; once a day (kaskad-exit-sub.timer) and by a button its keys are downloaded again and ONLY the
keys of that subscription change — the new ones come in, the gone ones go out; keys added by hand and keys of other
subscriptions are never touched.

    /etc/kaskad/egress/subs.json   0600 — the addresses are secrets (a subscription link is a password of its keys);
                                   beside the pool (upstream.txt): «Удалить шлюз» (gw purge) leaves both

    {"schema": 1, "next": 3, "subs": [{
        "id": "s-1a2b3c", "n": 1,              n — «подписка 1» for an address by IP (a domain is named by itself)
        "url": "https://…", "auto": true,      auto — the daily timer refreshes it (else only a button)
        "pin": "",                             SHA-256 of a self-signed certificate of an IP over https (subfetch)
        "keys": ["e-…"],                       exits OWNED by the subscription: added by it, removed by it
        "seen": ["e-…"],                       every key of its last good download (owned or not: in the pool before)
        "present": ["e-…"],                    the lines of the pool that carry its keys after that download — its
                                               own, a key added by hand before, the same server under another line
                                               (vpn:// and .conf of one peer): «N ключей в пуле» (T-M6-qs330b №9)
        "added": "<iso>", "last_try": ts, "last_ok": ts, "error": "", "count": n, "full": n,
        "idents": {"e-…": "<16 hex>"},         3.3.5: identity of each key of its last download (exits.identity_hash)
        "places": {"e-…": "<16 hex>"}}],       … and the identity with the address (exits.place_hash): one copy
     "ids": {"e-<text>": "e-<exit>"}}          3.3.5: a key the subscription gives under another line now (another name
                                               after «#», other parameters; the same server and credentials) keeps the
                                               id of its exit — its routes, name and switch (plan, exits.pool_id)

Ownership: a key the subscription brings that is already in the pool (added by hand, or by another subscription) is
not owned — it stays when the subscription drops it. An owned key the subscription dropped but another subscription
still lists (its ``seen``) changes hands instead of leaving.

Excluded (3.3.0, the owner): a key a person removed from the pool by hand («Удалить ключ» in the panel, the menu, the bot,
``gw exit-del``) is remembered as excluded by every subscription that lists it, and no refresh brings it back.
«Вернуть исключённые» (``restore``) forgets them and downloads the subscription again. The removal itself is written
by install.sh (``egress_pool_drop`` — the one place a line leaves the pool by hand): it appends the id to
``subs-excluded.pending`` beside subs.json, and the next change of the subscriptions folds it into ``excluded`` (a
removal never waits for the lock of the subscriptions; ids only, no key).

    "excluded": ["e-…"]                    keys the person removed by hand — never added again (at most EXCLUDED_MAX)
    "excluded_idents": ["<16 hex>"]        3.3.5: … nor under another name (their identity, from «idents»; one
                                           copy of a key the pool holds twice — its «places», the other stays)
    "excluded_was": {"<16 hex>": "e-…"}    3.3.5 (stand T-335-release В1): the id of the exit each of them had …
    "returning": {"<16 hex>": "e-…"}       … «Вернуть исключённые» hands it over here: the key comes back with that id
                                           under any name it has now (``plan``, «ids») — the routes on it work again;
                                           an entry goes once its exit is back in the pool, or once it came back as a
                                           repeat of a key the pool has (its other copy holds the routes); a line the
                                           pool has already (the same text added by hand) keeps its own id

The pool is written by install.sh only: the whole new pool goes to ``gokaskad --egress-replace <hex16>`` on stdin (one
rebuild, its pool lock; the pool changed meanwhile — code 3, computed again). What the parser understands is the
pool's: vless:// (a list, base64), vpn:// (goAmnezia all.vpn, the Amnezia app), amneziawg:// (goAmnezia all.txt) and
.conf (awgNN.conf) — egress_parse.py ≥ 9. A goAmnezia address without a format, of amneziawg:// (all.txt) or of the
profile of Clash (all.yaml) is taken as all.vpn of the same subscription — so «уже добавлена» compares the addresses
AFTER that (all.txt, then all.yaml of one subscription were two subscriptions: T-M6-qs330b №7).

Failures: the download failed, the answer is empty or holds no key — the pool is not touched, the subscription says
«не обновлялась N ч: причина». The limit of the pool (100 keys) — as many as fit, the rest counted in ``full``.
The journal gets the id and the counts — never the address or a key.
"""

import collections
import contextlib
import errno
import fcntl
import ipaddress
import json
import os
import re
import time
import urllib.parse

from .. import paths, util
from ..errors import CONFLICT, USAGE, ConflictError, KaskadError, LockedError
from ..i18n import tr, trn
from . import exits, subfetch
from . import sys as gsys

SCHEMA = 1
POOL_MAX = 100                      # EGRESS_MAX of install.sh
SUBS_MAX = 20
URL_MAX = 2048
PARSER_MIN = exits.PARSER_MIN       # egress_parse.py (PARSER_VERSION): one number for the whole gateway (gw/exits.py)
LOCK_WAIT = 120                     # s: another refresh (the timer, a button) — wait, then «занято»
REPLACE_TIMEOUT = 23 * 60           # the first AmneziaWG key may build the kernel module (as ops.EXIT_ADD_TIMEOUT)
REPLACE_TRIES = 3
STALE_HOURS = 26                    # a daily subscription not refreshed longer than this — a problem
RC_POOL_CHANGED = 3
RC_POOL_FULL = 5
RC_POOL_BUSY = 6
EXCLUDED_MAX = 200                  # remembered removals per subscription (the oldest forgotten first)
SERVICE = "kaskad-exit-sub.service"
TIMER = "kaskad-exit-sub.timer"
_ID = re.compile(r"^s-[0-9a-f]{6}$")
_EXIT_ID = re.compile(r"^e-[0-9a-f]{10}$")
_GA_PATH = re.compile(r"^/s-[A-Za-z0-9_-]{43}(?:/(?P<suffix>[A-Za-z0-9._-]*))?$")
_GA_YAML = ("all.yaml", "mihomo.yaml")
_GA_TXT = "all.txt"                 # amneziawg:// of the same peers: one subscription with all.vpn (T-M6-qs330b №7)

_now = time.time                    # tests patch it

# URL_TEXT, EMPTY_TEXT, NO_KEYS_TEXT, YAML_TEXT, HTML_TEXT, BUSY_TEXT, POOL_BUSY_TEXT — read through the module's
# __getattr__: a tr() in the language of the moment
_WORDS = {"URL_TEXT": "gw_exitsubs.url", "EMPTY_TEXT": "gw_exitsubs.empty", "NO_KEYS_TEXT": "gw_exitsubs.no_keys",
          "YAML_TEXT": "gw_exitsubs.yaml", "HTML_TEXT": "gw_exitsubs.html", "BUSY_TEXT": "gw_exitsubs.busy",
          "POOL_BUSY_TEXT": "gw_exitsubs.pool_busy"}
# the refusals of a download that say «Ключи пула не тронуты» themselves: ``data`` of their KaskadError — the failure of
# a refresh does not say it twice (``_failed``)
UNTOUCHED = {"pool_untouched": True}


def __getattr__(name):
    if name in _WORDS:
        return tr(_WORDS[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# ── the file ───────────────────────────────────────────────────────────────

def path():
    return os.path.join(paths.egress_dir(), "subs.json")


def _lock_path():
    return os.path.join(paths.run_dir(), "exit-subs.lock")


def pending_path():
    """Ids of keys removed from the pool by hand, appended by install.sh (``egress_pool_drop``) — one per line."""
    return os.path.join(paths.egress_dir(), "subs-excluded.pending")


def _read_pending(path=None):
    raw = util.read_text(path or pending_path()) or ""
    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if _EXIT_ID.match(ln) and ln not in out:
            out.append(ln)
    return out


def _exclude(doc, ids, pool=()):
    """Every subscription that lists one of ``ids`` (its own key or one it brought): excluded, not its own any more.
    ``pool`` — the lines of the pool now: while one of them is the same key (another copy — by IP and by domain, a pool
    of 3.3.4), not the key is excluded but this copy (its ``place``: the key and its address) — else the next renaming
    of the subscription would take the copy that stays away too (REV-335-2 Н2). -> the number of marks."""
    n = 0
    held = set(exits.identity_hash(l) for l in pool or ())
    for s in doc["subs"]:
        listed = set(s.get("keys") or []) | set(s.get("seen") or [])
        for i in ids:
            if i in listed:
                if i in s["keys"]:
                    s["keys"].remove(i)
                if i not in s["excluded"]:
                    s["excluded"].append(i)
                    n += 1
                h = (s.get("idents") or {}).get(i)     # 3.3.5: the key itself, whatever name it comes under next
                if h in held:                           # … but only this copy while the pool has the other one
                    h = (s.get("places") or {}).get(i)
                if h and h not in s.setdefault("excluded_idents", []):
                    s["excluded_idents"].append(h)
                if h:                                   # its id: «Вернуть исключённые» gives it back (В1)
                    s.setdefault("excluded_was", {})[h] = i
                    (s.get("returning") or {}).pop(h, None)
        if len(s["excluded"]) > EXCLUDED_MAX:
            s["excluded"] = s["excluded"][-EXCLUDED_MAX:]
        if len(s.get("excluded_idents") or []) > EXCLUDED_MAX:
            s["excluded_idents"] = s["excluded_idents"][-EXCLUDED_MAX:]
        if len(s.get("excluded_was") or {}) > EXCLUDED_MAX:
            s["excluded_was"] = dict((h, s["excluded_was"][h]) for h in s["excluded_idents"] if h in s["excluded_was"])
    return n


def fold_pending(doc):
    """The removals by hand install.sh wrote meanwhile -> ``excluded`` of the subscriptions (under the lock of the
    subscriptions; the caller saves). The file is taken away by a rename first: a removal at this very moment goes to a
    new file, not lost."""
    p = pending_path()
    if not os.path.exists(p):
        return 0
    tmp = "%s.fold.%d" % (p, os.getpid())
    try:
        os.replace(p, tmp)
    except OSError:
        return 0
    try:
        kept = doc.get("ids") or {}                     # install.sh wrote the id of the text (3.3.5: plan, «ids»)
        return _exclude(doc, [kept.get(i, i) for i in _read_pending(tmp)],
                        exits.pool_lines() + exits.pool_lines("ru"))     # T15: the copies in both files
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _empty():
    return {"schema": SCHEMA, "next": 1, "subs": []}


def load():
    """The file; missing or broken — empty (a broken one is never overwritten silently: ``save`` keeps a copy)."""
    raw = util.read_text(path())
    if not raw:
        return _empty()
    try:
        doc = json.loads(raw)
    except ValueError:
        return dict(_empty(), broken=True)
    if not isinstance(doc, dict) or not isinstance(doc.get("subs"), list):
        return dict(_empty(), broken=True)
    subs = []
    for s in doc["subs"]:
        if isinstance(s, dict) and _ID.match(str(s.get("id") or "")) and isinstance(s.get("url"), str):
            for k in ("keys", "seen", "excluded"):
                s[k] = [x for x in s.get(k) or [] if isinstance(x, str)]
            if "excluded_idents" in s:                  # 3.3.5 (absent in a record of an earlier version)
                s["excluded_idents"] = [x for x in s.get("excluded_idents") or [] if isinstance(x, str)]
            for m in ("idents", "places", "excluded_was", "returning"):
                if m in s:
                    s[m] = dict((k, v) for k, v in (s.get(m) if isinstance(s.get(m), dict) else {}).items()
                                if isinstance(v, str))
            if "present" in s:                          # absent in a record of an earlier version (in_pool)
                s["present"] = [x for x in s.get("present") or [] if isinstance(x, str)]
            subs.append(s)
    nxt = doc.get("next")
    nxt = nxt if isinstance(nxt, int) and not isinstance(nxt, bool) and nxt > 0 else 1
    nxt = max([nxt] + [int(s.get("n") or 0) + 1 for s in subs if isinstance(s.get("n"), int)])
    out = {"schema": SCHEMA, "next": nxt, "subs": subs}
    ids = doc.get("ids")
    if isinstance(ids, dict):                           # 3.3.5 (exits.kept_ids)
        out["ids"] = dict((k, v) for k, v in ids.items() if _EXIT_ID.match(str(k)) and _EXIT_ID.match(str(v)))
    return out


def save(doc):
    p = path()
    if load().get("broken") and os.path.exists(p):
        try:
            os.replace(p, p + ".broken")
        except OSError:
            pass
    util.ensure_dir(paths.egress_dir(), 0o700)
    out = {"schema": SCHEMA, "next": doc.get("next") or 1, "subs": doc.get("subs") or []}
    if doc.get("ids"):
        out["ids"] = doc["ids"]
    util.atomic_write(p, util.json_text(out), 0o600)


@contextlib.contextmanager
def _locked(wait=None):
    """One change of the subscriptions at a time (the timer, a button of the panel, the menu). ``wait`` — seconds to
    wait for it (LOCK_WAIT by default; a rename waits less, gw/rename.py)."""
    util.ensure_run_dir()
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = _now() + (LOCK_WAIT if wait is None else wait)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES) or _now() >= deadline:
                    raise LockedError(tr("gw_exitsubs.busy"))
                time.sleep(0.5)
        yield
    finally:
        os.close(fd)


# ── names and words ────────────────────────────────────────────────────────

def _host(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def domain_of(sub):
    """The domain of the address — shown; "" for an address by IP (the IP with the path is the secret)."""
    h = _host(sub.get("url") or "")
    return "" if not h or _is_ip(h) else h


def name_of(sub):
    """The name shown: the label given in Kaskad (T13, gw/rename.py), the domain, else «подписка N»."""
    lab = sub.get("label")
    if isinstance(lab, str) and lab:
        return lab
    return domain_of(sub) or tr("gw_exitsubs.name", n=int(sub.get("n") or 0))


def _title(sub):
    """The name at the start of a sentence: «Подписка 1» capitalised; a domain and a label — as they are."""
    if sub_label_of(sub) or domain_of(sub):
        return name_of(sub)
    return name_of(sub).capitalize()


def sub_label_of(sub):
    lab = sub.get("label")
    return lab if isinstance(lab, str) else ""


def _hours(sec):
    return max(0, int(sec // 3600))


def _ago(sec):
    sec = max(0, int(sec))
    if sec < 3600:
        return tr("gw_exitsubs.ago.min", n=max(1, sec // 60))
    if sec < 2 * 86400:
        return tr("gw_exitsubs.ago.h", n=sec // 3600)
    return trn("gw_exitsubs.ago.days", sec // 86400)


def state_text(sub, now=None):
    """«обновлена 3 ч назад: ключей 4» | «не обновлялась 30 ч: Сервер подписки ответил 404…» | «ещё не обновлялась…»."""
    now = _now() if now is None else now
    ok, tried, err = sub.get("last_ok"), sub.get("last_try"), sub.get("error") or ""
    state = state_of(sub)
    if state == "error":
        since = ok or _iso_ts(sub.get("added")) or tried or now
        h, err = _hours(now - since), err.rstrip(".") + "."
        # "not refreshed for 0 h" (stand T-334): less than an hour — in words
        return tr("gw_exitsubs.state.error", h=h, err=err) if h else tr("gw_exitsubs.state.error_new", err=err)
    if state == "never":
        return tr("gw_exitsubs.state.never")
    text = trn("gw_exitsubs.state.ok", int(sub.get("count") or 0), ago=_ago(now - ok))
    if sub.get("full"):
        text += tr("gw_exitsubs.state.full", max=POOL_MAX, n=int(sub["full"]))
    if sub.get("skipped"):
        text += tr("gw_exitsubs.state.skipped", n=int(sub["skipped"]), why=skip_why(sub.get("skip_codes") or ["other"]))
    if sub.get("dups"):
        text += tr("gw_exitsubs.state.dups", n=int(sub["dups"]))
    return text


def state_of(sub):
    """"error" — the last refresh failed, "never" — not downloaded yet, "ok" — the words of ``state_text``; the menu and
    the pages compare this, not the words."""
    ok, tried, err = sub.get("last_ok"), sub.get("last_try"), sub.get("error") or ""
    if err and (not ok or (tried or 0) > ok):
        return "error"
    return "ok" if ok else "never"


def _iso_ts(text):
    try:
        import calendar
        return calendar.timegm(time.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return None


def stale(sub, now=None):
    """A problem worth a red line: the last refresh failed, or a daily one has not succeeded for STALE_HOURS."""
    now = _now() if now is None else now
    ok, tried = sub.get("last_ok"), sub.get("last_try")
    if sub.get("error") and (not ok or (tried or 0) > ok):
        return True
    return bool(sub.get("auto")) and bool(ok) and now - ok > STALE_HOURS * 3600


def excluded_of(sub, pending=()):
    """The keys of its last download a person removed by hand (``excluded``, and the removals not folded yet)."""
    seen = set(sub.get("seen") or [])
    listed = seen | set(sub.get("keys") or [])
    out = [i for i in sub.get("excluded") or [] if i in seen]
    out += [i for i in pending if i in listed and i not in out]
    return out


def in_pool(sub, pool_ids=None, pending=()):
    """The lines of the pool that carry the keys of the subscription now (``present`` of its last download; a record of
    an earlier version — ``keys`` and ``seen``), without those the pool lost since. Not only its OWN keys: a key that was
    in the pool before the subscription (added by hand, by vpn:// where the subscription gives .conf) is its key too —
    «0 ключей в пуле» at three keys was wrong (T-M6-qs330b №9)."""
    if isinstance(sub.get("present"), list):
        ids = list(sub["present"])
    else:
        ids = list(sub.get("keys") or []) + [i for i in sub.get("seen") or [] if i not in (sub.get("keys") or [])]
    return [k for k in ids if (pool_ids is None or k in pool_ids) and k not in pending]


def public(sub, pool_ids=None, now=None, pending=()):
    """A row without the address: {"id", "n", "name", "domain", "auto", "keys" (its keys in the pool: ``in_pool``),
    "exits" (owned, in the pool), "count" (keys of the last download), "excluded" (of them removed by hand — not brought
    back), "full", "added", "last_ok", "last_try", "error", "stale", "text", "state" (``state_of``: ok | error | never —
    what ``text`` says), "pinned"}."""
    owned = [k for k in sub.get("keys") or [] if (pool_ids is None or k in pool_ids) and k not in pending]
    text = state_text(sub, now)
    excl = len(excluded_of(sub, pending))
    if excl:
        text += tr("gw_exitsubs.state.excluded", n=excl)
    return {"id": sub["id"], "n": int(sub.get("n") or 0), "name": name_of(sub), "domain": domain_of(sub),
            "label": sub.get("label") if isinstance(sub.get("label"), str) else "",
            "auto": bool(sub.get("auto")), "keys": len(in_pool(sub, pool_ids, pending)), "exits": owned,
            "count": int(sub.get("count") or 0),
            "excluded": excl, "full": int(sub.get("full") or 0), "skipped": int(sub.get("skipped") or 0),
            "added": sub.get("added") or "",
            "last_ok": sub.get("last_ok"), "last_try": sub.get("last_try"), "error": sub.get("error") or "",
            "stale": stale(sub, now), "text": text, "state": state_of(sub), "pinned": bool(sub.get("pin"))}


def listing(now=None):
    """Public rows of every subscription (no address, no key)."""
    doc = load()
    try:
        pool_ids = set(i for i, _n, _r in exits.all_line_ids())     # both files (REV-s1-q4 З2)
    except KaskadError:
        pool_ids = None
    kept = doc.get("ids") or {}
    pending = [kept.get(i, i) for i in _read_pending()]     # install.sh wrote the id of the text (3.3.5)
    return [public(s, pool_ids, now, pending) for s in doc["subs"]]


def owners(doc=None):
    """{exit id: {"sub": id, "name": «подписка 1» | domain}} — the mark of the keys of a subscription."""
    doc = load() if doc is None else doc
    out = {}
    for s in doc["subs"]:
        for k in s.get("keys") or []:
            out.setdefault(k, {"sub": s["id"], "name": name_of(s)})
    return out


# ── the address ────────────────────────────────────────────────────────────

def normalize(url):
    """-> (url, note). The address checked for its form (no download): http(s), a host, not too long. A subscription of
    goAmnezia Pro (``/s-<43>/<суффикс>``) without a format or of the profile of Clash -> all.vpn of the same one."""
    if not isinstance(url, str) or not url.strip():
        raise KaskadError(tr("gw_exitsubs.url"), code=USAGE)
    url = url.strip()
    if len(url) > URL_MAX or any(c.isspace() for c in url) or "\n" in url:
        raise KaskadError(tr("gw_exitsubs.url_bad"), code=USAGE)
    try:
        u = urllib.parse.urlsplit(url)
        u.port
    except ValueError:
        raise KaskadError(tr("gw_exitsubs.url_unparsed"), code=USAGE)
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        raise KaskadError(tr("gw_exitsubs.url_scheme"), code=USAGE)
    note = ""
    m = _GA_PATH.match(u.path or "")
    if m:
        suffix = m.group("suffix") or ""
        if suffix in ("", _GA_TXT) or suffix in _GA_YAML:
            base = u.path if suffix == "" else u.path[:-len(suffix)]
            base = base if base.endswith("/") else base + "/"
            note = (tr("gw_exitsubs.note.bare") if suffix == "" else
                    tr("gw_exitsubs.note.txt") if suffix == _GA_TXT else tr("gw_exitsubs.note.clash", suffix=suffix))
            url = urllib.parse.urlunsplit((u.scheme, u.netloc, base + "all.vpn", u.query, ""))
    return url, note


def _canon(url):
    """The address as ``normalize`` makes it — an address kept before (all.txt of an earlier version) compares as
    all.vpn too; an address it refuses stays as it is."""
    try:
        return normalize(url)[0]
    except KaskadError:
        return url


def _same(a, b):
    """One subscription: equal after ``normalize`` (all.txt, all.yaml, the bare address of goAmnezia — all.vpn)."""
    a, b = _canon(a), _canon(b)
    try:
        ua, ub = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    except ValueError:
        return a == b
    return (ua.scheme.lower(), (ua.hostname or "").lower(), ua.port, ua.path, ua.query) == \
           (ub.scheme.lower(), (ub.hostname or "").lower(), ub.port, ub.path, ub.query)


def find_same(url):
    """The subscription already added at this address (the form of ``normalize``) or None; KaskadError when the
    address is not one. Nothing downloaded."""
    url, _ = normalize(url)
    return next((s for s in load()["subs"] if _same(s["url"], url)), None)


# ── the keys of a subscription ─────────────────────────────────────────────

def _parser(runner):
    """egress_parse.py ≥ PARSER_MIN: an older file on disk (a node before this version) is written again by
    ``gokaskad --egress-ensure-parser`` once — exits.load_parser does it for every reader of the pool (3.3.4)."""
    return exits.load_parser(runner)


class _Held(str):
    """A line ``keys_of`` holds, with the code it is held for when that code says more than the line itself
    (``self_ep`` — its server is a name of this node, 3.3.7.14): ``_held_codes`` tells it for a key the pool has not."""
    why = ""

    def __new__(cls, line, code=None):
        h = str.__new__(cls, line)
        h.why = "self_ep" if code == "self_ep" else ""
        return h


def keys_of(text, parser, skipped=None, held=None):
    """The pool lines of a downloaded subscription (the parser of the pool). KaskadError in words when there are none.
    3.3.4: a key xray would refuse as a whole (allowInsecure without pcs…) is left out alone — its code goes to
    ``skipped`` (a list) and the other keys are applied; before, one such key stopped the whole refresh. A key with
    allowInsecure whose server did not answer on the update (tried again — "wait" of extract_report) goes to ``held``
    (a list) instead: ``plan`` keeps it if the pool has it — else it is one more skipped."""
    if not (text or "").strip():
        raise KaskadError(tr("gw_exitsubs.empty"), data=UNTOUCHED)
    report = getattr(parser, "extract_report", None)
    left = []
    with exits._quiet():
        if callable(report):
            lines, left = report(exits.squeeze(text))
        else:
            lines = parser.extract(exits.squeeze(text))
    # stage 1 (§9.5): every xray scheme (vless, hy2, ss, trojan, vmess) and AmneziaWG; a key of another scheme comes back
    # in ``left`` as «unsupported_scheme» with the name of its scheme — «unsupported_scheme:TUIC» (said by skip_why)
    lines = [l.strip() for l in lines or [] if exits.link_proto(l.strip()) or l.strip().startswith("awg://")]
    left = [x for x in left or [] if isinstance(x, dict)]
    # REV-s1-t11 С3 («как раньше»): a VLESS without TLS a subscription gave on 3.3.5 stays — held as a key that waits
    # (plan keeps the line of the pool with its id); the rule «no TLS to a public address — no» is for new lines only
    for x in left:
        if x.get("code") == "no_tls" and exits.link_proto(str(x.get("line") or "").strip()) == "vless":
            x["wait"] = True
        # REV-s1-t11-3 З-2: a line of the pool xray refused in both forms (xray_heal) stays in the pool — off, with its
        # id and the routes on it — as С3: held; a key the pool has not is one more skipped («xray_refused»)
        if x.get("code") == "xray_refused" and exits.link_proto(str(x.get("line") or "").strip()):
            x["wait"] = True
        # 3.3.7.14: the server of an xray key is a name of this node (found by DNS, «dns») — a new key is refused, a key
        # the pool already has (taken by 3.3.7.13 and before) stays, as С3: the rule is for new lines
        if x.get("code") == "self_ep" and x.get("dns") and exits.link_proto(str(x.get("line") or "").strip()):
            x["wait"] = True
    wait = [_Held(str(x.get("line") or "").strip(), x.get("code")) for x in left if x.get("wait") and held is not None]
    wait = [l for l in wait if exits.link_proto(l)]
    codes = [_skip_code(x) for x in left if not (x.get("wait") and held is not None)]
    if skipped is not None:
        skipped.extend(codes)
    if held is not None:
        held.extend(wait)
    if lines or wait:
        return lines
    if codes:
        raise KaskadError(trn("gw_exitsubs.all_skipped", len(codes), why=skip_why(codes)), data=UNTOUCHED)
    low = text.lstrip()[:4096].lower()
    if low.startswith(("<!doctype", "<html")) or "<html" in low:
        raise KaskadError(tr("gw_exitsubs.html"), data=UNTOUCHED)
    if re.search(r"(?m)^proxies:\s*$", text) or "proxy-groups:" in text:
        raise KaskadError(tr("gw_exitsubs.yaml"), data=UNTOUCHED)
    raise KaskadError(tr("gw_exitsubs.no_keys"), data=UNTOUCHED)


def download(runner, sub, new_pin=False, fetcher=None, skipped=None, held=None):
    """-> (lines, pin): the subscription downloaded (subfetch: global addresses only, the address pinned, redirects by
    hand) and parsed. KaskadError with the reason. ``skipped`` (a list) gets the codes of the keys left out, ``held``
    the keys kept if the pool has them (keys_of)."""
    fetcher = fetcher or subfetch.fetch_sub
    text, pin = fetcher(sub["url"], pin=sub.get("pin") or None, new_pin=new_pin)
    try:
        return keys_of(text, _parser(runner), skipped, held), pin
    finally:
        text = None


SKIP_CODES = ("insecure", "bad_pin", "no_tls", "weak_cipher", "bad_link", "unsupported_scheme", "dup_endpoint",
              "xray_refused", "xray_unchecked", "bad_addr", "self_ep", "gw_net")
_SCHEME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 +.-]{0,23}$")


def _skip_code(x):
    """The code of a key left out (extract_report): «unsupported_scheme:<the name of its scheme>» for a scheme the pool
    does not take (the name only — never the key), else the code."""
    code = str(x.get("code") or "")
    if code == "insecure" and exits.link_proto(str(x.get("line") or "").strip()) == "hysteria2":
        return "insecure_quic"          # Н4 (T-KT1): QUIC — its fingerprint can't be pinned by hand, no such advice
    if code == "unsupported_scheme":
        name = " ".join(str(x.get("name") or "").split())
        return code + (":" + name if _SCHEME_NAME.match(name) else "")
    return code


def skip_why(codes):
    """The reasons of the keys left out, short, each once: «без проверки сертификата и без отпечатка pcs»; the keys of
    the schemes the pool does not take — by the name of the scheme and how many (when more than one): «другие протоколы:
    TUIC — 4, AnyTLS»; gw_net — with the network of the gateway of this node (3.3.6 round 3: two nodes Kaskad on one
    base)."""
    out = []
    schemes = collections.OrderedDict()
    for c in codes or []:
        c = str(c)
        if c.split(":", 1)[0] == "unsupported_scheme":
            name = c.partition(":")[2] or "?"
            schemes[name] = schemes.get(name, 0) + 1
            continue
        if c == "gw_net":
            w = tr("gw_exitsubs.skip.gw_net", net=exits.gw_base_text())
        elif c == "insecure_quic":       # the words of the pool for the same key (exits._problem_code)
            w = tr("gw_exits.problem.key_insecure_quic", proto=exits.proto_title("hysteria2"))
        else:
            w = tr("gw_exitsubs.skip." + c) if c in SKIP_CODES else tr("gw_exitsubs.skip.other")
        if w not in out:
            out.append(w)
    if schemes:
        out.append(tr("gw_exitsubs.skip.unsupported_scheme", list=", ".join(
            "%s — %d" % (n, k) if k > 1 else n for n, k in schemes.items())))
    return "; ".join(out)


def _dup_endpoints(p, other_lines=()):
    """§9.1 (З2): a new Hysteria2 key at the «host:port» of another Hysteria2 key — of the new pool or of the other file
    of the pool (``other_lines``: upstream-ru.txt) — is left out with «dup_endpoint» (T0: xray keeps one QUIC client per
    address — the settings of the first key for both). The line a renamed key replaces (``plan`` of 3.3.5) is not in
    the new pool any more — no duplicate; the same key (``peer_key``) is no second key either (``plan`` says it as a
    duplicate). Changes ``p`` in place -> how many were left out."""
    added = set(p.get("added") or [])
    seen, drop = {}, []

    def end(line):
        return exits.key_address(line) if exits.link_proto(line) == "hysteria2" else ""

    for l in list(other_lines) + [l for l in p["pool"] if exits.pool_id(l, p["ids"]) not in added]:
        e = end(l)
        if e:
            seen.setdefault(e, set()).add(exits.peer_key(l))
    for l in p["pool"]:
        i = exits.pool_id(l, p["ids"])
        e = end(l) if i in added else ""
        if not e:
            continue
        k = exits.peer_key(l)
        if e in seen and k not in seen[e]:
            drop.append((i, l))
            continue
        seen.setdefault(e, set()).add(k)
    for i, l in drop:
        p["pool"].remove(l)
        for key in ("added", "keys", "present"):
            if i in (p.get(key) or []):
                p[key].remove(i)
    return len(drop)


def _updated_note(p):
    n = len(p.get("renamed") or {})
    out = tr("gw_exitsubs.log.updated", n=n) if n else ""
    if p.get("dups"):
        out += tr("gw_exitsubs.log.dups", n=len(p["dups"]))
    return out


IDS_SHOWN = 3


def _dups_text(dups, names=None):
    """«этот ключ уже есть как e-… («Аня»), по другому адресу» (one exit) or «эти ключи уже есть в пуле: e-… («Аня», по
    другому адресу), e-… («Борис», записан иначе)» — ``plan`` «dups»: (id, id of the exit, how); each exit once, at most
    IDS_SHOWN; ``names`` — {exit id: its name} (``_exit_names``). At two IPs or two domains — the hint of a server
    restored from a copy of this one, once (exits.same_key_text)."""
    names = names or {}
    how = collections.OrderedDict()
    for d in dups:
        how.setdefault(d[1], d[2] if len(d) > 2 else "text")
    if len(how) == 1:
        (eid, h), = how.items()
        return exits.same_key_text(eid, h, names.get(eid, ""))
    items = []
    for eid, h in list(how.items())[:IDS_SHOWN]:
        word = tr("gw_exits.same_how." + ("address" if h in exits.SAME_AT else "text"))
        name = names.get(eid, "")
        items.append("%s (%s)" % (eid, "%s, %s" % (exits.quoted(name), word) if name and name != eid else word))
    return tr("gw_exitsubs.result.dups_many", list=", ".join(items) + (", …" if len(how) > IDS_SHOWN else "")) \
        + (tr("gw_exits.same_key_restored") if "address" in how.values() else "")


def _exit_names(ids):
    """{exit id: its name for people} of the exits of the pool among ``ids`` — the words of a duplicate name the exit
    the key already is (REV-335-2 Н4: as ``gw exit-add`` and the preview do); none when the pool cannot be read."""
    from .. import state
    want = set(ids or ())
    if not want:
        return {}
    try:
        st = state.load()
        return dict((e["id"], exits.label(st, e)) for e in exits.pool(None, roles=exits.ROLES) if e["id"] in want)
    except Exception:                                   # noqa: BLE001 — the id alone is said then
        return {}


def _skip_note(p):
    """« пропущено 2 (…)» for the journal, "" — none."""
    n = len(p.get("skipped") or [])
    return tr("gw_exitsubs.log.skipped", n=n) if n else ""


# ── the pool ───────────────────────────────────────────────────────────────

def pool_hash(lines):
    import hashlib
    return hashlib.sha1("\n".join(lines).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def plan(cur, sub, lines, others, held=(), ids=None, other=(), max_keys=POOL_MAX, new_to_other=None):
    """The new pool for ``sub`` whose download gave ``lines`` -> dict:
        pool      the new pool lines (the order of the pool kept; new keys at the end, in the order of the subscription)
        keys      the exits the subscription owns after it
        handed    {exit id: id of another subscription} — owned keys it dropped that another one lists
        added, removed: [exit ids]; full: keys that did not fit (POOL_MAX); excluded keys (removed by hand) never come
        renamed   {exit id: id of the text of its new line} — 3.3.5, below; ids — the map ``ids`` with them
    ``others`` — the other subscriptions (their ``seen``). ``held`` (keys_of): keys with allowInsecure whose server is
    tried again — never added; the pool has that server (peer_key: the same account on the same server, whatever the
    name after «#») — its line in the pool is kept as it is and seen (3.3.4: the next refresh does not take an exit that
    waits for its pin away, even when 3x-ui wrote another traffic or term into the name); the others are counted in
    ``held_out`` (one more skipped each).
    3.3.5: a key of its own the subscription now gives under another line — the same server and credentials
    (``exits.identity``), another name after «#» («10GB left» → «9GB left») or other parameters — is the same exit: its
    new line takes the place of the old one in the pool and keeps the id of the exit (``ids``: {id of the text: id of
    the exit}, subs.json «ids», ``exits.pool_id``) — the routes of devices and people, the name and the switch of the
    exit stay. Another uuid or server — a new exit, as before. A key removed by hand stays out under any name
    (``excluded_idents``).
    3.3.5 (stand В1): a key «Вернуть исключённые» brings back (``returning``: its identity -> the id it had) comes back
    with that id under any line it has now — first by its place (the key and its address), then by the key; ``returned``
    — {that id: id of the text of the line}, in ``ids`` too.
    Stage 1 (T15 Q4; SE §3.8 Р2, SPEC-groups §2.9): ``cur`` — the file of the subscription (upstream.txt, or
    upstream-ru.txt of a subscription of a Russian group), ``other`` — the other file. A key of its own a change of role
    moved there is placed already: it is updated IN PLACE in that file (``pool_other``: the other file after it — its
    new line on the place of the old one, the id kept) and goes from there when the subscription drops it; never
    written into ``cur`` again. A line whose key the other file holds not as a key of this subscription (added by hand)
    is a duplicate (``dups``), not written. ``max_keys`` — the room of ``cur`` (100, or 32 for upstream-ru.txt).
    D-068 (SE §3.8 Н4, SPEC-groups §2.9): ``new_to_other`` — the room of the other file (upstream-ru.txt) for a
    subscription of a foreign group: when one of its own keys lies there (a change of role moved it), every NEW key of
    it (not renamed, not found in either file — a key the provider gave instead of a gone one with another uuid too)
    goes THERE, at the end of ``pool_other`` (``standby``: their ids — the caller puts them «в запас» with
    ``sub_confirm`` before the write); never silently into ``cur``."""
    ids = dict(ids or {})
    other = list(other or ())
    cur_all = list(cur) + other

    def idof(l):
        n = exits.exit_id(l)
        return ids.get(n, n)

    returned = {}
    kept_by_hand = []                                   # old ids of «returning» whose key the pool holds by hand already
    back = sub.get("returning") or {}
    if back:
        taken = set(idof(l) for l in cur_all)
        # a line the pool holds already (the same text added by hand while it waited, REV-335-5 №3) keeps its own id:
        # the routes put on it stay; the id it had before is not given back
        in_pool = set(exits.exit_id(l) for l in cur_all)
        for l in lines:
            n = exits.exit_id(l)
            if n in ids:
                continue
            old = back.get(exits.place_hash(l)) or back.get(exits.identity_hash(l))
            if n in in_pool:
                # its wait ends here (REV-336-nits №1): else the entry would fire later — the line by hand removed and the
                # key renamed by the subscription, it would come back under the old id
                if old and old != n:
                    kept_by_hand.append(old)
                continue
            if old and old != n and old not in taken and old not in returned:
                ids[n] = old
                returned[old] = n

    new = []
    seen_new = set()
    cur_by_peer = {}
    for l in cur_all:
        cur_by_peer.setdefault(exits.peer_key(l), l)
    held_in, held_out = [], 0
    for l in held or ():
        c = cur_by_peer.get(exits.peer_key(l))
        if c is None:
            held_out += 1
        elif c not in held_in:
            held_in.append(c)
    # every line of the answer is its key (REV-335-2): the same key twice in one answer (vpn:// and .conf of one peer,
    # by IP and by domain) is not dropped silently here — below it is a pool line kept, or a duplicate said
    for l in list(lines) + held_in:
        i = idof(l)
        if i not in seen_new:
            seen_new.add(i)
            new.append((i, l))
    owned = set(sub.get("keys") or [])
    handed = {}
    for i in sorted(owned - seen_new):
        for o in others:
            if i in (o.get("seen") or []):
                handed[i] = o["id"]
                break
    drop = (owned - seen_new) - set(handed)
    cur_pairs = [(idof(l), l) for l in cur_all]
    in_other = set(i for i, _ in cur_pairs[len(cur):])
    excluded = set(sub.get("excluded") or [])          # removed by hand: never brought back
    hashes = dict((i, exits.identity_hash(l)) for i, l in new)
    places = dict((i, exits.place_hash(l)) for i, l in new)
    gone = set(sub.get("excluded_idents") or [])       # … nor under another name (3.3.5; one copy — its place)
    excluded_more = [i for i, _ in new if i not in excluded and (hashes[i] in gone or places[i] in gone)]
    excluded |= set(excluded_more)
    cur_ids = set(i for i, _ in cur_pairs)
    cand = [(i, l) for i, l in new if i not in cur_ids and i not in excluded]
    renamed, instead = {}, {}
    # an exit of its own whose line the answer no longer has takes a line of the same key: first the one at its own
    # address, then any (REV-335-2 Н1: both copies of one key a pool of 3.3.4 holds — by IP and by domain — keep their
    # ids and routes when the subscription renames both); each line once, each exit once
    for same_place in (True, False):
        for o, l in cur_pairs:
            if o not in drop or o in renamed:
                continue
            h, where = exits.identity_hash(l), exits.key_address(l)
            for i, nl in cand:
                if i in instead or hashes[i] != h or (same_place and exits.key_address(nl) != where):
                    continue
                renamed[o] = i
                instead[i] = o
                ids[exits.exit_id(nl)] = o
                break
    if renamed:
        new = [(instead.get(i, i), l) for i, l in new]
        hashes = dict((instead.get(i, i), h) for i, h in hashes.items())
        places = dict((instead.get(i, i), h) for i, h in places.items())
        drop -= set(renamed)
    line_of = dict(new)
    for o, l in cur_pairs:          # its new line has the id of the exit already (a try before failed): the same swap
        if o in owned and o not in renamed and o not in drop and line_of.get(o, l) != l:
            renamed[o] = exits.exit_id(line_of[o])
    keep_all = [(n < len(cur), line_of[i] if i in renamed else l) for n, (i, l) in enumerate(cur_pairs) if i not in drop]
    keep = [l for own, l in keep_all if own]
    keep_other = [l for own, l in keep_all if not own]
    present = set(idof(l) for _own, l in keep_all)
    room = max(0, max_keys - len(keep))
    peer_of = {}                                        # a key the pool has by another line is not added again
    for _own, l in keep_all:
        peer_of.setdefault(exits.peer_key(l), (idof(l), l))
    foreign = set(idof(l) for l in keep_other) - owned  # the other file holds it, not as a key of this subscription
    # 3.3.5 №2, №4: the keys left out because the pool (or a line above in this answer) has the same key by another
    # line — at another address (by IP and by domain) or written differently: (id, id of that exit, how) — said, not
    # silent
    take, take_other, dups, full = [], [], [], 0
    divert = new_to_other is not None and bool(in_other & owned)
    room_other = max(0, new_to_other - len(keep_other)) if divert else 0
    for i, l in new:
        if i in foreign and i not in excluded:          # the same text in the other file, added by hand (Р2): said
            dups.append((i, i, "text"))
            continue
        if i in present or i in excluded:
            continue
        k = exits.peer_key(l)
        if k in peer_of:
            dups.append((i, peer_of[k][0], exits.same_how(l, peer_of[k][1])))
        elif divert:                                    # D-068: «в запас» in the Russian file, never abroad
            if len(take_other) < room_other:
                peer_of[k] = (i, l)
                take_other.append((i, l))
            else:
                full += 1
        elif len(take) < room:
            peer_of[k] = (i, l)
            take.append((i, l))
        else:
            full += 1
    pool = keep + [l for _, l in take]
    keep_other = keep_other + [l for _, l in take_other]
    after = set(idof(l) for l in pool + keep_other) - foreign
    took = set(x for x, _ in take + take_other)
    keys = [i for i, _ in new if i in after and (i in owned or i in took)]
    removed = [i for i, _ in cur_pairs if i in drop]
    by_peer = {}
    for l in pool + keep_other:
        by_peer.setdefault(exits.peer_key(l), idof(l))
    present = []
    for i, l in new:                                    # its key in the pool: by its line, else by its server
        got = i if i in after else by_peer.get(exits.peer_key(l))
        if got and got not in present:
            present.append(got)
    return {"pool": pool, "pool_other": keep_other, "moved_here": sorted(in_other & owned & after),
            "keys": keys, "handed": handed, "added": [i for i, _ in take + take_other],
            "standby": [i for i, _ in take_other],
            "removed": sorted(set(removed)), "full": full, "seen": [i for i, _ in new], "present": present,
            "held_out": held_out, "renamed": renamed, "ids": ids, "idents": hashes, "places": places,
            "excluded_more": excluded_more, "dups": dups, "returned": returned, "kept_by_hand": kept_by_hand}


def replace_pool(runner, cur, pool, only=None):
    """``gokaskad --egress-replace <hash of cur>`` with ``pool`` on stdin. -> (rc (0 or RC_POOL_CHANGED), warnings,
    notes — the good news of the keys, «проверен — работает со всеми параметрами», REV-s1-b4 П-3); other failures
    raise in words (the script's own). ``only`` — the numbers of the keys whose warnings and news are told one by one
    (ops._script_warnings; None — every key)."""
    from . import ops
    res = runner.run([exits._script(runner), "--egress-replace", pool_hash(cur)],
                     input="".join(l + "\n" for l in pool), timeout=REPLACE_TIMEOUT,
                     env=ops.script_env())                     # 3.3.2: the marks @K3: and the language of the call
    if res.rc in (0, RC_POOL_CHANGED):
        lines = ops._script_lines(res)
        return res.rc, ops._script_warnings(lines, only), ops._script_notes(lines, only)
    lines = ops._script_lines(res)
    if res.rc == RC_POOL_BUSY:
        raise LockedError(tr("gw_exitsubs.pool_busy"))
    if res.rc == RC_POOL_FULL:
        raise ConflictError(tr("gw_exitsubs.pool_full", max=POOL_MAX))
    raise KaskadError(ops._script_error(lines, tr("gw_exitsubs.pool_failed", rc=res.rc)))


RU_MAX = exits.RU_MAX                # upstream-ru.txt: at most 32 keys (EGRESS_RU_MAX of install.sh)


def role_of(sub):
    """"ru" — a subscription of a Russian group (T15, SPEC-groups §2.9): its keys go to upstream-ru.txt and only there.
    Kept in the subscription (``role``) when it is added: a Russian group deleted later never sends its keys — home
    routers — to the pool of the foreign groups (D-016)."""
    return "ru" if isinstance(sub, dict) and sub.get("role") == "ru" else "abroad"


def write_pool(runner, role, cur, pool, only=None):
    """The file of ``role`` from ``cur`` to ``pool``: upstream.txt — ``replace_pool`` (``--egress-replace``: the bot's
    proxy rebuilt), upstream-ru.txt — ``gokaskad --egress-ru-apply <hash of cur>`` (its legs and gw apply, the bot
    untouched; T15 §7). -> (rc (0 or RC_POOL_CHANGED), warnings, notes)."""
    if role != "ru":
        return replace_pool(runner, cur, pool, only)
    from . import ops
    res = runner.run([exits._script(runner), "--egress-ru-apply", pool_hash(cur)],
                     input="".join(l + "\n" for l in pool), timeout=REPLACE_TIMEOUT, env=ops.script_env())
    lines = ops._script_lines(res)
    if res.rc in (0, RC_POOL_CHANGED):
        return res.rc, ops._script_warnings(lines, only), ops._script_notes(lines, only)
    if res.rc == RC_POOL_BUSY:
        raise LockedError(tr("gw_exitsubs.pool_busy"))
    if res.rc == RC_POOL_FULL:
        raise ConflictError(tr("gw_exitsubs.pool_full", max=RU_MAX))
    raise KaskadError(ops._script_error(lines, tr("gw_exitsubs.pool_failed", rc=res.rc)))


def _other_lines():
    """The lines of the other file of the pool (upstream-ru.txt, T15) — the same xray of the gateway takes both: a
    Hysteria2 key of a subscription is a duplicate of one there too (§9.1)."""
    return exits.pool_lines("ru")


def _no_tls(line):
    """A VLESS key without TLS (nor REALITY) — kept by a subscription only while the pool has it (REV-s1-t11 С3)."""
    if exits.link_proto(line) != "vless":
        return False
    q = urllib.parse.parse_qs(str(line).strip().partition("#")[0].partition("?")[2])
    return ((q.get("security") or ["none"])[0] or "none").lower() not in ("tls", "reality")


def _held_codes(cur, held, refused=()):
    """The codes of the held keys the pool has not (``plan`` «held_out»): the code a line was held for (``_Held.why``:
    "self_ep"), "xray_refused" — a line xray refused in both forms (``refused``: those lines), "no_tls" — a VLESS
    without TLS, else "insecure" (allowInsecure, its server waited for)."""
    have = set(exits.peer_key(l) for l in cur)
    refused = set(refused or ())
    return [getattr(l, "why", "") or ("xray_refused" if l in refused else "no_tls" if _no_tls(l) else
                                      "insecure_quic" if exits.link_proto(l) == "hysteria2" else "insecure")
            for l in held or () if exits.peer_key(l) not in have]


def _refused_lines(runner, lines):
    """The lines xray refused in both forms (the parser's ``line_form`` «refused», REV-s1-t11-3 З-2)."""
    if not lines:
        return set()
    try:
        fn = getattr(_parser(runner), "line_form", None)
    except KaskadError:
        return set()
    if not callable(fn):
        return set()
    with exits._quiet():
        return set(l for l in lines if fn(l) == "refused")


def _refused(runner, lines, cur):
    """REV-s1-t11 С2: {new line (not in the pool): "xray_refused" | "xray_unchecked"} — the real xray of the egress
    refuses it, or did not answer in time (REV-s1-t11-2 З-2: not taken now, asked again on the next refresh — the same
    as the script's «vet»). The parser's xray_vet: xray -test of them all, then of halves; a line it takes is marked to
    go to the configs with all its parameters. {} — no xray yet: the new lines are marked «unvetted» (the form of 3.3.5
    until the heal of the next rebuild asks the real xray, REV-s1-t11-3 З-1)."""
    xray = paths.egress_xray()
    have = set(cur)
    new = [l for l in lines if l not in have]
    if not new:
        return {}
    p = _parser(runner)
    fn = getattr(p, "xray_vet", None)
    if not callable(fn):
        return {}
    if not os.access(xray, os.X_OK):                # REV-s1-t11-3 З-1: marked «unvetted» — xray_heal asks it later
        with exits._quiet():
            fn(new, "", None, 30, None)
        return {}
    def run(cfg):
        rc = runner.run([xray, "-test", "-c", cfg], timeout=30).rc
        return None if rc in (124, 127) else rc == 0        # 124 — no answer in time (sysrun), 127 — did not run
    with exits._quiet():
        r = fn(new, xray, None, 30, run)
    out = dict((new[i - 1], "xray_unchecked") for i in r.get("unchecked") or () if 0 < i <= len(new))
    out.update((new[i - 1], "xray_refused") for i in r.get("refused") or () if 0 < i <= len(new))
    return out


def _apply(runner, doc, sub, lines, held=(), skipped=None):
    """The pool for ``sub`` made of ``lines`` (and ``held``, plan), written (again when the pool changed meanwhile).
    -> plan + warnings + skipped (``skipped`` and a code per held key the pool has not, _held_codes). Only held keys and
    none of them in the pool: KaskadError, nothing touched (as keys_of when every key is left out). A new line the real
    xray refuses or did not answer for (_refused) is left out as "xray_refused" / "xray_unchecked"; its key the pool has
    under its line of before (renamed by the subscription) — held: the line of before stays."""
    others = [o for o in doc["subs"] if o["id"] != sub["id"]]
    lines, held, skipped = list(lines), list(held or ()), list(skipped or [])
    vetted, refused = set(), set()
    kept_off = _refused_lines(runner, held)
    role = role_of(sub)                                 # T15: the file of the subscription and the other one (Р2)
    other_role = "abroad" if role == "ru" else "ru"
    for _ in range(REPLACE_TRIES):
        fold_pending(doc)                               # a key removed by hand meanwhile is not brought back
        cur = exits.pool_lines(role)
        oth = exits.pool_lines(other_role)
        bad = _refused(runner, [l for l in lines if l not in vetted], cur + oth)
        vetted.update(lines)
        if bad:
            refused.update(bad)
            peers = set(exits.peer_key(l) for l in cur)
            lines = [l for l in lines if l not in bad]
            held += [l for l in bad if exits.peer_key(l) in peers]
            skipped += [bad[l] for l in bad]
        p = plan(cur, sub, lines, others, held, doc.get("ids"), other=oth,
                 max_keys=RU_MAX if role == "ru" else POOL_MAX, new_to_other=None if role == "ru" else RU_MAX)
        dup = _dup_endpoints(p, p["pool_other"])           # stage 1 (§9.1): Hysteria2 at an address taken — after renamed
        p["skipped"] = skipped + _held_codes(cur + oth, [l for l in held if l not in refused], kept_off) + \
            ["dup_endpoint"] * dup
        if held and not p["seen"]:
            raise KaskadError(trn("gw_exitsubs.all_skipped", len(p["skipped"]), why=skip_why(p["skipped"])),
                              data=UNTOUCHED)
        p["warnings"] = _notls_kept(cur, held, p)
        # SE §3.8 С2: the map «ids» by the lines of BOTH files — an exit a change of role moved to the other file keeps
        # its entry (and so its id) through any refresh
        if p["pool"] == cur and p["pool_other"] == oth:
            doc["ids"] = prune_ids(doc, p["ids"], cur + oth)
            return p
        if p["renamed"] or p["returned"]:               # 3.3.5: the ids kept BEFORE the pool — its rebuild (gw apply)
            doc["ids"] = prune_ids(doc, p["ids"], cur + oth + p["pool"] + p["pool_other"])   # names the new lines
            save(doc)
        if p["standby"]:                                # D-068: «в запас» BEFORE the line is in the pool
            p["standby_group"] = _standby(sub, p["standby"], oth, p["ids"])
        touched = set(p["added"]) | set(p["renamed"])   # 3.3.5: their warnings, not the whole pool's
        only = set(n for n, l in enumerate(p["pool"], 1) if exits.pool_id(l, p["ids"]) in touched)
        rc, warns, notes = 0, [], []
        if p["pool_other"] != oth:                      # a key of its own in the other file, updated there (Р2)
            rc, warns, notes = write_pool(runner, other_role, oth, p["pool_other"], set())
        if rc == 0 and p["pool"] != cur:
            rc, w2, n2 = write_pool(runner, role, cur, p["pool"], only)
            warns, notes = warns + w2, notes + n2
        if rc == 0:
            p["warnings"] = p["warnings"] + warns + _hand_over(runner, _twins(cur, p))
            p["notes"] = notes
            doc["ids"] = prune_ids(doc, p["ids"], p["pool"] + p["pool_other"])
            return p
    raise LockedError(tr("gw_exitsubs.pool_churn"))


def _standby(sub, new_ids, oth, ids):
    """D-068: the new keys ``new_ids`` of a foreign subscription whose own keys lie in upstream-ru.txt — «в запас»
    (``manual = standby``, ``manual_why = sub_confirm``) in the Russian group that holds most of its keys there (a tie —
    the first by the order of the groups; none placed — no hint, the first Russian group by place_new). Written before
    the line enters the pool: never routable unconfirmed. -> the id of that group (None — none)."""
    from .. import state
    from . import model, policy
    mine = set(sub.get("keys") or ()) & set(exits.pool_id(l, ids) for l in oth)
    with state.transaction() as st:
        doc = policy.load(st)
        count = {}
        for eid in mine:
            g = policy.group_of(doc, eid)
            gid = g.get("id") if isinstance(g, dict) else model.exit_pref(st, eid).get("group")
            g = policy.group(doc, gid) if gid else None
            if isinstance(g, dict) and g.get("role") == "ru":
                count[g["id"]] = count.get(g["id"], 0) + 1
        order = [g.get("id") for g in doc.get("groups") or [] if isinstance(g, dict)]
        best = max(count, key=lambda x: (count[x], -order.index(x) if x in order else -len(order))) if count else None
        for eid in new_ids:
            model.set_exit(st, eid, manual="standby", manual_why="sub_confirm", group=best)
    util.log_action("GW sub %s: new keys on standby (sub_confirm) in %s: %s" % (sub.get("id"), best, " ".join(new_ids)))
    return best


def _notls_kept(cur, held, p):
    """REV-s1-t11 С3: the warnings of the VLESS keys without TLS the subscription keeps as they were («как раньше»)."""
    keys = set(exits.peer_key(l) for l in held or () if _no_tls(l))
    if not keys:
        return []
    ids = p.get("ids") or {}
    kept = [exits.pool_id(l, ids) for l in cur if exits.peer_key(l) in keys and l in p["pool"]]
    names = _exit_names(kept)
    return [tr("gw_exitsubs.notls_kept", name=names.get(i) or i) for i in kept]


def _hand_over(runner, moves):
    """A copy the subscription dropped whose key stays in the pool: its routes go to the other copy (REV-335-2) — only
    once the new pool is in place (REV-335-3: a replace that failed leaves them where they were), applied at once.
    Between the two applies (the replace's and this one — seconds) the routes of the copy that went name an exit the
    pool has not: the gateway builds such a route by its fail policy (block or direct: xray.py ``target``, exits.py
    for the inbounds without xray) — kept so on purpose, a failed replace must not touch the routes (REV-335-4 №3,
    docs/k3/SPEC-subs-backup.md).
    -> warnings: the routes that did not move (the copy may not take them, or the move failed)."""
    if not moves:
        return []
    from . import ops
    unfit = []
    try:
        ops.hand_over(moves, runner=runner, unfit=unfit)
    except KaskadError as e:
        return [tr("gw_exitsubs.twin_failed", name=ops._exit_name(b), error=str(e).rstrip(".") + ".")
                for b in sorted(set(moves.values()))]
    return [tr("gw_exitsubs.twin_unfit", name=ops._exit_name(b)) for b in unfit]


def _twins(cur, p):
    """{id of an exit ``plan`` removes: id of a line of the new pool with the same key} — one key twice (by IP and by
    domain, a pool of 3.3.4) and the subscription no longer gives one copy: the routes of that copy go to the other."""
    gone = set(p.get("removed") or ())
    if not gone:
        return {}
    ids = p.get("ids") or {}
    stays = {}
    for l in p["pool"]:
        stays.setdefault(exits.peer_key(l), exits.pool_id(l, ids))
    out = {}
    for l in cur:
        i = exits.pool_id(l, ids)
        to = stays.get(exits.peer_key(l)) if i in gone else None
        if to and to != i:
            out[i] = to
    return out


def prune_ids(doc, ids, lines):
    """The map «ids» (``plan``) without what nothing needs: an entry stays while its line is in ``lines`` (the pool), or
    while its exit is out of the pool but still a key of a subscription — a removal by hand not folded yet
    (subs-excluded.pending holds the id of the text: ``fold_pending`` reads it through this map)."""
    texts = set(exits.exit_id(l) for l in lines)
    there = set(ids.get(n, n) for n in texts)
    owned = set()
    for s in doc.get("subs") or []:
        owned.update(s.get("keys") or [])
    return dict((n, e) for n, e in sorted((ids or {}).items()) if n in texts or (e not in there and e in owned))


def _record(doc, sub, p, now):
    for i, oid in p["handed"].items():
        for o in doc["subs"]:
            if o["id"] == oid and i not in o["keys"]:
                o["keys"].append(i)
    sub["keys"] = p["keys"]
    sub["seen"] = p["seen"]
    sub["present"] = p["present"]
    sub["idents"] = dict((i, h) for i, h in (p.get("idents") or {}).items() if i in p["seen"])
    sub["places"] = dict((i, h) for i, h in (p.get("places") or {}).items() if i in p["seen"])
    for i in p.get("excluded_more") or []:             # removed by hand, now under another name: still excluded
        if i not in sub.setdefault("excluded", []):
            sub["excluded"].append(i)
    if len(sub.get("excluded") or []) > EXCLUDED_MAX:
        sub["excluded"] = sub["excluded"][-EXCLUDED_MAX:]
    if sub.get("returning"):                            # back in the pool under its id: nothing more to give back
        back = set(exits.pool_id(l, p.get("ids") or {}) for l in p["pool"])
        # … nor when it came back as a repeat of a key the pool has (REV-335-5 №4: the other copy of it — by IP and by
        # domain — took its routes when it was removed): no entry waits for that copy to go
        back.update(d[0] for d in p.get("dups") or ())
        back.update(p.get("kept_by_hand") or ())         # … nor when the pool holds its key as a line added by hand
        sub["returning"] = dict((h, o) for h, o in sub["returning"].items() if o not in back)
        if not sub["returning"]:
            del sub["returning"]
    sub["count"] = len(p["seen"])
    sub["full"] = p["full"]
    sub["skipped"] = len(p.get("skipped") or [])
    sub["dups"] = len(p.get("dups") or [])
    sub["skip_codes"] = sorted(set(p.get("skipped") or []))       # codes, not words: said in the reader's language
    sub.pop("skip_why", None)
    sub["last_try"] = sub["last_ok"] = int(now)
    sub["error"] = ""


def _result(sub, p, now):
    out = {"id": sub["id"], "name": name_of(sub), "ok": True, "added": len(p["added"]), "removed": len(p["removed"]),
           "count": len(p["seen"]), "full": p["full"], "warnings": p.get("warnings") or [],
           "notes": p.get("notes") or [],
           "skipped": len(p.get("skipped") or []), "updated": len(p.get("renamed") or {})}
    title = _title(sub)
    if out["added"] or out["removed"]:
        text = tr("gw_exitsubs.result.changed", name=title, added=out["added"], removed=out["removed"],
                  count=out["count"])
        if out["updated"]:
            text += " " + tr("gw_exitsubs.result.updated_too", n=out["updated"])
    elif out["updated"]:
        text = tr("gw_exitsubs.result.updated", name=title, n=out["updated"], count=out["count"])
    else:
        text = tr("gw_exitsubs.result.same", name=title, count=out["count"])
    if p["full"]:
        text += " " + tr("gw_exitsubs.result.full", max=POOL_MAX, n=p["full"])
    if out["skipped"]:
        text += " " + tr("gw_exitsubs.result.skipped", n=out["skipped"], why=skip_why(p.get("skipped")))
    out["dups"] = len(p.get("dups") or [])
    if out["dups"]:
        text += " " + tr("gw_exitsubs.result.dups", n=out["dups"],
                         why=_dups_text(p["dups"], _exit_names(d[1] for d in p["dups"])))
    out["standby"] = list(p.get("standby") or [])
    if out["standby"]:
        text += " " + _standby_text(title, out["standby"], p.get("standby_group"))
    out["text"] = text
    return out


def _standby_text(title, ids, gid):
    """D-068: «Подписка «…»: новый ключ «…» поставлен в запас в группе «Дом» — подтвердите в карточке выхода»."""
    from .. import state
    from . import policy
    gname = ""
    try:
        g = policy.group(policy.load(state.load()), gid) if gid else None
        gname = (g or {}).get("name") or ""
    except Exception:                                   # noqa: BLE001 — the words without the group then
        gname = ""
    if len(ids) == 1:                                   # whole keys: the check of orphan keys sees each call
        key = _exit_names(ids).get(ids[0]) or ids[0]
        if gname:
            return tr("gw_exitsubs.result.standby_group", sub=title, key=key, group=gname)
        return tr("gw_exitsubs.result.standby", sub=title, key=key)
    if gname:
        return tr("gw_exitsubs.result.standby_n_group", sub=title, n=len(ids), group=gname)
    return tr("gw_exitsubs.result.standby_n", sub=title, n=len(ids))


def _failed(sub, err, now):
    sub["last_try"] = int(now)
    sub["error"] = util.redact(str(err))
    title = _title(sub)
    said = bool((getattr(err, "data", None) or {}).get("pool_untouched"))     # the refusal says it itself
    return {"id": sub["id"], "name": name_of(sub), "ok": False, "error": sub["error"], "added": 0, "removed": 0,
            "count": int(sub.get("count") or 0), "full": int(sub.get("full") or 0), "warnings": [],
            "text": tr("gw_exitsubs.result.failed", name=title, err=sub["error"].rstrip(".") + ".")
            + ("" if said else " " + tr("gw_exitsubs.untouched"))}


def _log(key, **kw):
    """A line of the journal «GW exit-sub …» in the language of the server (its words too)."""
    with util.journal_lang():
        util.log_action("GW exit-sub %s" % tr(key, **kw))


# ── the commands ───────────────────────────────────────────────────────────

def _group_role(gid):
    """(gid, role) of the group a new subscription goes to (SPEC-groups §2.9; None — «main», as before): a group of the
    policy, else USAGE (``field`` group)."""
    if gid in (None, ""):
        return None, "abroad"
    from .. import state
    from . import policy, policy_ops
    g = policy.group(policy_ops._base(state.load()), gid)
    if g is None:
        raise policy_ops.bad_field("group", tr("gw_policy_ops.group_target", field="group", value=gid))
    return gid, ("ru" if g.get("role") == "ru" else "abroad")


def add(runner, url, auto=True, fetcher=None, group=None):
    """A subscription: the address checked, downloaded now (a failure — nothing kept), its keys into the pool.
    -> {"ok", "sub": public row, "result": per refresh, "note", "text"}. ``group`` (stage 1, SPEC-groups §2.9): the
    group of its keys (``subs[].group``); a Russian group — the keys go to upstream-ru.txt and only there (``role``)."""
    url, note = normalize(url)
    gid, role = _group_role(group)
    with _locked():
        doc = load()
        fold_pending(doc)
        for s in doc["subs"]:
            if _same(s["url"], url):
                raise ConflictError(tr("gw_exitsubs.add.same", name=name_of(s)), data={"sub": s["id"]})
        if len(doc["subs"]) >= SUBS_MAX:
            raise ConflictError(tr("gw_exitsubs.add.max", n=SUBS_MAX))
        sid = _new_id(doc)
        sub = {"id": sid, "n": doc["next"], "url": url, "auto": bool(auto), "pin": "", "keys": [], "seen": [],
               "excluded": [], "added": util.now_iso(), "last_try": None, "last_ok": None, "error": "", "count": 0, "full": 0}
        if gid:
            sub["group"] = gid
        if role == "ru":
            sub["role"] = "ru"                          # fixed: a group deleted later never moves its keys abroad
        skipped, held = [], []
        lines, pin = download(runner, sub, new_pin=True, fetcher=fetcher, skipped=skipped, held=held)
        sub["pin"] = pin or ""
        doc["next"] += 1
        doc["subs"].append(sub)
        now = _now()
        p = _apply(runner, doc, sub, lines, held, skipped)
        lines = held = None
        _record(doc, sub, p, now)
        save(doc)
        rv = _note("sub add")
        timer_sync(runner, doc)
        res = _result(sub, p, now)
    with util.journal_lang():
        _log("gw_exitsubs.log.add", id=sid, mode=tr("gw_exitsubs.log.daily" if auto else "gw_exitsubs.log.manual"),
             count=res["count"], added=res["added"],
             full=(tr("gw_exitsubs.log.full", n=res["full"]) if res["full"] else "") + _updated_note(p) + _skip_note(p))
    text = tr("gw_exitsubs.add.done", name=name_of(sub))
    if note:
        text += " " + tr("gw_exitsubs.add.note", note=note)
    if sub["pin"]:
        text += " " + tr("gw_exitsubs.add.pinned", fp=sub["pin"][:16])
    return {"ok": True, "sub": public(sub, None), "result": res, "note": note, "text": text + " " + res["text"],
            "warnings": res["warnings"], "notes": res["notes"], "rev": rv}


def _new_id(doc):
    import random
    rng = random.SystemRandom()
    have = set(s["id"] for s in doc["subs"])
    while True:
        sid = "s-%06x" % rng.randrange(1 << 24)
        if sid not in have:
            return sid


def _find(doc, ref):
    ref = str(ref or "").strip()
    for s in doc["subs"]:
        if s["id"] == ref or (ref.isdigit() and int(ref) == s.get("n")) or (domain_of(s) and domain_of(s) == ref.lower()):
            return s
    raise KaskadError(tr("gw_exitsubs.not_found", ref=ref), code=USAGE, data={"gone": True})


def refresh(runner, ref=None, auto_only=False, fetcher=None):
    """Download again and change only the keys of the subscription(s): one (``ref`` — id, number or domain) or all
    (``auto_only`` — those of the daily timer). A failure of one is recorded and said; the others go on.
    -> {"ok": every one ok, "results": [...], "text"}."""
    with _locked():
        doc = load()
        fold_pending(doc)
        subs = [_find(doc, ref)] if ref else [s for s in doc["subs"] if s.get("auto") or not auto_only]
        results = _refresh_locked(runner, doc, subs, fetcher)
        rv = _note("sub refresh") if results else None
    ok = all(r["ok"] for r in results)
    if not results:
        text = tr("gw_exitsubs.none" if not auto_only else "gw_exitsubs.none_daily")
    else:
        text = " ".join(r["text"] for r in results)
    return {"ok": ok, "results": results, "text": text, "rev": rv}


def _refresh_locked(runner, doc, subs, fetcher=None):
    """Download each of ``subs`` again and change only its keys (the lock of the subscriptions is held). -> results."""
    results = []
    for sub in subs:
        now = _now()
        try:
            skipped, held = [], []
            lines, pin = download(runner, sub, fetcher=fetcher, skipped=skipped, held=held)
            p = _apply(runner, doc, sub, lines, held, skipped)
            lines = held = None
            _record(doc, sub, p, now)
            r = _result(sub, p, now)
            with util.journal_lang():
                _log("gw_exitsubs.log.refresh", id=sub["id"], count=r["count"], added=r["added"], removed=r["removed"],
                     full=(tr("gw_exitsubs.log.full", n=r["full"]) if r["full"] else "") + _updated_note(p)
                     + _skip_note(p))
        except LockedError:
            save(doc)
            raise
        except KaskadError as e:
            r = _failed(sub, e, now)
            _log("gw_exitsubs.log.refresh_failed", id=sub["id"], err=r["error"])
        results.append(r)
        save(doc)
    return results


def restore(runner, ref, fetcher=None):
    """«Вернуть исключённые»: the subscription forgets the keys a person removed by hand and is downloaded again — they
    come back into the pool. A failed download keeps the forgetting: they come back with the next refresh.
    -> {"ok", "restored": how many were excluded, "result", "text"}."""
    with _locked():
        doc = load()
        fold_pending(doc)
        sub = _find(doc, ref)
        n = len(sub.get("excluded") or [])
        sub["excluded"] = []
        sub.pop("excluded_idents", None)
        was = sub.pop("excluded_was", None) or {}       # В1: they come back with their ids — their routes work again
        if was:
            sub["returning"] = dict(sub.get("returning") or {}, **was)
        save(doc)
        results = _refresh_locked(runner, doc, [sub], fetcher)
        rv = _note("sub restore")
    r = results[0]
    with util.journal_lang():
        _log("gw_exitsubs.log.restore", id=sub["id"], n=n)
    key = "gw_exitsubs.restore.none" if not n else "gw_exitsubs.restore.done" if r["ok"] else "gw_exitsubs.restore.later"
    text = tr(key, name=name_of(sub), result=r["text"])
    return {"ok": r["ok"], "restored": n, "result": r, "text": text, "rev": rv}


def delete(runner, ref, keep_keys=False):
    """The subscription away. Its keys go out of the pool too (``keep_keys`` — they stay as added by hand); a key that
    another subscription lists changes hands. -> {"ok", "id", "removed", "text"}."""
    with _locked():
        doc = load()
        fold_pending(doc)
        sub = _find(doc, ref)
        removed = []
        if not keep_keys and sub.get("keys"):
            p = _apply(runner, doc, dict(sub, url=""), [])   # the subscription «gave» no keys: its own go
            removed = p["removed"]
            for i, oid in p["handed"].items():
                for o in doc["subs"]:
                    if o["id"] == oid and i not in o["keys"]:
                        o["keys"].append(i)
        doc["subs"] = [s for s in doc["subs"] if s["id"] != sub["id"]]
        save(doc)
        rv = _note("sub delete")                        # REV-SPEC-stage1-fix-21 З2: the pool changed without _record
        timer_sync(runner, doc)
    with util.journal_lang():
        _log("gw_exitsubs.log.delete", id=sub["id"], n=len(removed),
             kept=tr("gw_exitsubs.log.kept") if keep_keys else "")
    text = tr("gw_exitsubs.delete.done", name=name_of(sub))
    text += tr("gw_exitsubs.delete.kept") if keep_keys else (
        tr("gw_exitsubs.delete.removed", n=len(removed)) if removed else ".")
    return {"ok": True, "id": sub["id"], "removed": len(removed), "text": text, "rev": rv}


def _note(what):
    """Step 3 of a write of a subscription (SPEC-stage1-exits §3.1, проверки 19–20; T7): ONE ``rev.note`` after
    ``_record`` and ``save(doc)``, outside the loop over the subscriptions, «по возможности» (a busy state lock — None
    and a line in the journal: the keys are recorded already). No ``rev`` is compared — the background writes them.
    -> rev | None."""
    from . import policy_ops
    return policy_ops.after_pool(what)


def set_auto(runner, ref, auto):
    with _locked():
        doc = load()
        sub = _find(doc, ref)
        sub["auto"] = bool(auto)
        save(doc)
        timer_sync(runner, doc)
    _log("gw_exitsubs.log.auto" if auto else "gw_exitsubs.log.manual_set", id=sub["id"])
    return {"ok": True, "sub": public(sub, None)}


# ── the timer ──────────────────────────────────────────────────────────────

def service_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: exit subscriptions (keys of the Telegram API proxy pool)",  # one text for every language
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s gw exit-sub refresh --auto --quiet" % paths.kaskadctl(),
        "TimeoutStartSec=30min",
        "",
    ])


def timer_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: exit subscriptions once a day",  # one text for every language
        "",
        "[Timer]",
        "OnCalendar=daily",
        "RandomizedDelaySec=1h",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def timer_sync(runner, doc=None):
    """The timer is there while a daily subscription is; none — both units away. Never fails the caller."""
    doc = load() if doc is None else doc
    try:
        if any(s.get("auto") for s in doc["subs"]):
            written = gsys.write_unit(SERVICE, service_text())
            r = gsys.ensure_unit(runner, TIMER, timer_text())
            if written and not r["written"]:
                gsys.systemd(runner, "daemon-reload")
            return True
        for u in (TIMER, SERVICE):
            gsys.remove_unit(runner, u)
    except KaskadError:
        pass
    return False
