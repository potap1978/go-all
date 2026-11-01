"""Login to the web panel (docs/k3/SPEC-wave3.md §3.1). The rules are the audited ones of goAmnezia pro (its
server.py: sessions, password, CSRF, Host), the code is ours.

Password: /etc/kaskad/panel/password.json, 0600 — {"schema": 1, "login", "algo": "scrypt", "n", "r", "p", "salt",
"hash", "updated"}; the password itself is stored nowhere (``kaskadctl panel password`` reads it from stdin,
never from argv). Parameters are read from the record, so a change of the defaults keeps old passwords valid;
a record with absurd parameters is refused (an n of a million would eat the memory on every try). Python without
``hashlib.scrypt`` falls back to PBKDF2-SHA256.

    * a login answer takes at least FLOOR s whatever failed; the KDF runs for an unknown login too (a dummy
      record): «no such login» and «wrong password» cost the same time;
    * at most KDF_SLOTS KDF at once (16 MB each), a request waits for a slot at most KDF_WAIT s (then «занято»);
    * ATTEMPTS tries per WINDOW s per address (IPv6: per /64); the slot is taken BEFORE the check, so a burst of
      parallel requests gets no more than ATTEMPTS tries; a good login clears the address.
    * one-time links have a count of their own (``LINKS``: LINK_ATTEMPTS wrong or spent links per WINDOW s): wrong
      passwords never close the way in by a link — over the SSH tunnel every request comes from 127.0.0.1, and a
      few typos left the owner with no way in for 15 minutes, the fresh link from the menu included (stand T-G1b,
      N5). A good link clears its count.

Sessions: in memory (a restart logs everybody out): a random token in the cookie ``kaskad_session``
(``HttpOnly; SameSite=Strict; Path=/`` and ``Secure`` over HTTPS); IDLE s without a request and it is gone,
MAX_AGE at most. Only the SHA-256 of a token is kept. A session made with a password or a link dies when the
password changes (the signature of password.json differs from the one at login) — the first password too: a link
session opened before any password remembers «no password» (``NO_PASSWORD``), not «nothing to check».

One-time links (``kaskadctl panel link``, the bot, ``POST /api/panel/link``): ``/login?t=<token>``, LINK_TTL s,
single use. /etc/kaskad/panel/links.json (0600) keeps only SHA-256 of the tokens and their expiry; issue and
redeem run under a file lock shared by every process (the CLI and the bot write, the panel redeems). The
panel answers 303 to "/" at once: the token leaves the address bar, Referrer-Policy no-referrer keeps it out of
Referer, and no request log keeps it anywhere. The bot calls ``login_link(ttl)`` -> {"url", "token", "ttl"}.

Tunnel: a request to the loopback listener from a loopback address without any X-Forwarded-*/Forwarded/
X-Real-IP header is the owner who came over SSH (``ssh -L 1980:127.0.0.1:1980``) — admin without a password,
unless the panel is in open mode or the setting ``require_password`` is on. The risk «any local process of the
node is an admin» is accepted (spec §3.1; that process is root's anyway). The listener for nginx ("proxy",
docs/k3/SPEC-wave4.md §3) is never the tunnel, whatever headers come: nginx connects from the node itself.

``Misses`` — the limit of the user page (spec §5): 10 wrong links per 5 minutes from an address — 15 minutes ban.
"""

import contextlib
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets as _random
import threading
import time

from .. import paths, util
from ..errors import USAGE, KaskadError
from ..i18n import tr

LOGIN_DEFAULT = "admin"
LOGIN_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
PASSWORD_MIN = 10
PASSWORD_MAX = 200
SCRYPT_N, SCRYPT_R, SCRYPT_P = 1 << 14, 8, 1
SCRYPT_N_RANGE = (1 << 10, 1 << 17)
SCRYPT_MAXMEM = 64 * 1024 * 1024
PBKDF2_ROUNDS = 200000
DKLEN = 32
FLOOR = 0.35
KDF_SLOTS = 2
KDF_WAIT = 10.0
ATTEMPTS = 5
LINK_ATTEMPTS = 10                  # wrong or spent one-time links per WINDOW from an address (their own count)
WINDOW = 900
IDLE = 12 * 3600
MAX_AGE = 7 * 24 * 3600
LINK_TTL = 3600
LINK_MAX = 50                       # unredeemed links kept at most (the oldest go first)
COOKIE = "kaskad_session"
PROXY_HEADERS = ("forwarded", "x-real-ip")
PAGE_MISSES, PAGE_WINDOW, PAGE_BAN = 10, 300, 900

_now = time.time                    # tests patch it; the floor is measured with time.monotonic (real time)
_kdf = threading.BoundedSemaphore(KDF_SLOTS)


class LoginRefused(KaskadError):
    """A login that is not even checked: the address used its tries (``retry_after``) or the KDF is busy."""

    def __init__(self, text, retry_after=0, status=429):
        KaskadError.__init__(self, text)
        self.retry_after = int(retry_after)
        self.status = status


# ── addresses ──────────────────────────────────────────────────────────────

def _ip(value):
    try:
        a = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped is not None:
        return a.ipv4_mapped
    return a


def is_loopback(value):
    a = _ip(value)
    return a is not None and a.is_loopback


def bucket(value):
    """The rate-limit key of an address: IPv4 itself, IPv6 its /64 (one host has the whole /64)."""
    a = _ip(value)
    if a is None:
        return str(value or "?")
    if a.version == 6:
        return str(ipaddress.IPv6Network((int(a) >> 64 << 64, 64)))
    return str(a)


def proxied(headers):
    """A reverse proxy was in the way (any of its headers, whatever the value)."""
    return any(h.startswith("x-forwarded-") or h in PROXY_HEADERS for h in headers)


def tunnel(headers, client_ip, listener_local, public=False, require_password=False):
    """Admin without a password: the loopback listener, a loopback peer, no proxy header, not the open mode,
    the setting «always ask the password» off."""
    if public or require_password or not listener_local:
        return False
    return not proxied(headers) and is_loopback(client_ip)


# ── password ───────────────────────────────────────────────────────────────

def password_path():
    return os.path.join(paths.panel_dir(), "password.json")


def _algo():
    return "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2"


def _derive(password, rec):
    """The KDF with the parameters OF THE RECORD."""
    salt = bytes.fromhex(str(rec.get("salt") or ""))
    raw = str(password).encode("utf-8")
    if rec.get("algo") == "scrypt":
        return hashlib.scrypt(raw, salt=salt, n=int(rec["n"]), r=int(rec["r"]), p=int(rec["p"]), dklen=DKLEN,
                              maxmem=SCRYPT_MAXMEM).hex()
    return hashlib.pbkdf2_hmac("sha256", raw, salt, int(rec.get("rounds") or PBKDF2_ROUNDS), dklen=DKLEN).hex()


def _params():
    if _algo() == "scrypt":
        return {"algo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}
    return {"algo": "pbkdf2", "rounds": PBKDF2_ROUNDS}


def record():
    """The password record from the file (read every time: the CLI changes it from outside) or {}."""
    raw = util.read_text(password_path())
    try:
        rec = json.loads(raw) if raw else None
    except ValueError:
        rec = None
    if not isinstance(rec, dict):
        return {}
    login, algo = str(rec.get("login") or ""), str(rec.get("algo") or "")
    salt, digest = str(rec.get("salt") or ""), str(rec.get("hash") or "")
    if not LOGIN_RE.match(login) or algo not in ("scrypt", "pbkdf2"):
        return {}
    if not re.match(r"^(?:[0-9a-f]{2}){8,64}$", salt) or not re.match(r"^(?:[0-9a-f]{2}){16,64}$", digest):
        return {}
    out = {"login": login, "algo": algo, "salt": salt, "hash": digest, "updated": str(rec.get("updated") or "")}
    try:
        if algo == "scrypt":
            n, r, p = int(rec.get("n")), int(rec.get("r")), int(rec.get("p"))
            if not (SCRYPT_N_RANGE[0] <= n <= SCRYPT_N_RANGE[1] and n & (n - 1) == 0 and 1 <= r <= 32
                    and 1 <= p <= 16):
                return {}
            out.update(n=n, r=r, p=p)
        else:
            rounds = int(rec.get("rounds"))
            if not 10000 <= rounds <= 5000000:
                return {}
            out["rounds"] = rounds
    except (TypeError, ValueError):
        return {}
    return out


def password_set():
    return bool(record())


def check_new_password(password):
    if not isinstance(password, str):
        raise KaskadError(tr("panel.auth.passwordNotString"), code=USAGE)
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise KaskadError(tr("panel.auth.passwordLength", min=PASSWORD_MIN, max=PASSWORD_MAX), code=USAGE)
    if password != password.strip() or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise KaskadError(tr("panel.auth.passwordSpaces"), code=USAGE)
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise KaskadError(tr("panel.auth.passwordChars"), code=USAGE)
    return password


def set_password(password, login=None):
    """Write the login and the hash of ``password`` (0600, atomically). Returns {"login", "updated"}."""
    login = str(login or "").strip() or (record().get("login") or LOGIN_DEFAULT)
    if not LOGIN_RE.match(login):
        raise KaskadError(tr("panel.auth.loginFormat"), code=USAGE)
    check_new_password(password)
    rec = dict(_params(), login=login, schema=1, salt=_random.token_bytes(16).hex(), updated=util.now_iso())
    rec["hash"] = _derive(password, rec)
    util.ensure_dir(paths.panel_dir(), 0o700)
    util.atomic_write(password_path(), json.dumps(rec, sort_keys=True) + "\n", 0o600)
    return {"login": login, "updated": rec["updated"]}


def generate_password():
    """A random password for the menu and the bot: 16 letters and digits without look-alikes."""
    from .. import secrets as ksecrets
    return ksecrets.new_password(16)


_DUMMY_SALT = _random.token_bytes(16).hex()
_DUMMY_HASH = _random.token_bytes(DKLEN).hex()


def _dummy():
    """A record no password matches (random salt and hash), with the current parameters: an unknown login
    or a panel without a password costs the same KDF as a real check."""
    return dict(_params(), login="", salt=_DUMMY_SALT, hash=_DUMMY_HASH)


def verify(login, password):
    """Do login and password match; the time does not depend on what failed (the dummy KDF)."""
    rec = record()
    given = str(login or LOGIN_DEFAULT).encode("utf-8", "ignore")
    matched = bool(rec) and hmac.compare_digest(given, str(rec.get("login") or "").encode("utf-8", "ignore"))
    target = rec if matched else _dummy()
    try:
        calc = _derive(str(password or ""), target)
    except (ValueError, MemoryError, UnicodeEncodeError):
        return False
    return bool(hmac.compare_digest(calc, str(target.get("hash") or "")) and matched)


# ── tries per address ──────────────────────────────────────────────────────

class RateLimit(object):
    """``limit`` tries per ``window`` s per bucket; a sliding window."""

    def __init__(self, limit=ATTEMPTS, window=WINDOW):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._hist = {}

    def _sweep(self, now):
        for b in [b for b, h in self._hist.items() if not h or now - h[-1] >= self.window]:
            self._hist.pop(b, None)

    def begin(self, key, now=None):
        """Take a try BEFORE the check. -> (allowed, seconds until unblocked, tries left after this one)."""
        now = _now() if now is None else now
        with self._lock:
            hist = [t for t in self._hist.get(key, []) if now - t < self.window]
            if len(hist) >= self.limit:
                self._hist[key] = hist
                return False, max(1, int(self.window - (now - hist[-self.limit]))), 0
            hist.append(now)
            self._sweep(now)
            self._hist[key] = hist
            return True, 0, max(0, self.limit - len(hist))

    def clear(self, key):
        with self._lock:
            self._hist.pop(key, None)


class Misses(object):
    """``limit`` misses in ``window`` s -> the address is banned for ``ban`` s (the user page, spec §5)."""

    def __init__(self, limit=PAGE_MISSES, window=PAGE_WINDOW, ban=PAGE_BAN):
        self.limit, self.window, self.ban = limit, window, ban
        self._lock = threading.Lock()
        self._hist = {}
        self._banned = {}

    def banned(self, key, now=None):
        now = _now() if now is None else now
        with self._lock:
            until = self._banned.get(key)
            if until is not None and until <= now:
                self._banned.pop(key, None)
                until = None
            return until is not None

    def miss(self, key, now=None):
        now = _now() if now is None else now
        with self._lock:
            hist = [t for t in self._hist.get(key, []) if now - t < self.window] + [now]
            if len(hist) >= self.limit:
                self._banned[key] = now + self.ban
                hist = []
            for k in [k for k, h in self._hist.items() if k != key and (not h or now - h[-1] >= self.window)]:
                self._hist.pop(k, None)
            for k in [k for k, u in self._banned.items() if u <= now]:
                self._banned.pop(k, None)
            if hist:
                self._hist[key] = hist
            else:
                self._hist.pop(key, None)


LIMIT = RateLimit()                 # passwords: the login form and «Текущий пароль» of the password change
LINKS = RateLimit(LINK_ATTEMPTS, WINDOW)    # one-time links (GET /login?t=…): never closed by wrong passwords
PAGES = Misses()


def _pace(started):
    rest = FLOOR - (time.monotonic() - started)
    if rest > 0:
        time.sleep(rest)


def login(client_ip, login_name, password):
    """Check a login form. -> (ok, tries left). Raises LoginRefused when the address used its tries (429) or
    no KDF slot is free in time (503). The answer takes at least FLOOR s (unless refused before any check)."""
    started = time.monotonic()
    key = bucket(client_ip)
    allowed, retry, left = LIMIT.begin(key)
    if not allowed:
        raise LoginRefused(tr("panel.auth.tooManyTries", min=max(1, (retry + 59) // 60)), retry_after=retry)
    if not _kdf.acquire(timeout=KDF_WAIT):
        _pace(started)
        raise LoginRefused(tr("panel.auth.kdfBusy"), retry_after=60,
                           status=503)
    try:
        ok = verify(login_name, password)
    finally:
        _kdf.release()
    _pace(started)
    if ok:
        LIMIT.clear(key)
    return ok, left


# ── sessions ───────────────────────────────────────────────────────────────

def _hash(token):
    return hashlib.sha256(str(token).encode("utf-8", "ignore")).hexdigest()


def _password_sig():
    try:
        st = os.stat(password_path())
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


NO_PASSWORD = "none"


def _password_mark():
    """What a password/link session remembers of the password at login: the signature of password.json, or
    NO_PASSWORD when there is none yet (F19b: a session opened by a link before the first password dies when that
    password is set — from the SSH menu, the CLI, the wizard or the panel — like after any other change)."""
    sig = _password_sig()
    return NO_PASSWORD if sig is None else sig


class Sessions(object):
    """Admin sessions in memory: sha256(token) -> {"via", "ip", "created", "seen", "pw"}."""

    def __init__(self):
        self._lock = threading.Lock()
        self._s = {}
        self.on_change = None           # callable(count): the server rewrites its status file

    def _changed(self):
        cb = self.on_change
        if cb is not None:
            try:
                cb(self.count())
            except Exception:           # noqa: BLE001 — a status file must not break a login
                pass

    def _gc(self, now):
        gone = [k for k, s in self._s.items() if now - s["seen"] > IDLE or now - s["created"] > MAX_AGE]
        for k in gone:
            self._s.pop(k, None)
        return bool(gone)

    def create(self, via, client_ip=""):
        token = _random.token_urlsafe(32)
        now = _now()
        with self._lock:
            self._gc(now)
            self._s[_hash(token)] = {"via": via, "ip": str(client_ip or ""), "created": now, "seen": now,
                                     "pw": _password_mark() if via in ("password", "link") else None}
        self._changed()
        return token

    def get(self, token):
        """The session of a cookie token (its idle clock restarts) or None."""
        if not token:
            return None
        now = _now()
        pw = _password_mark()
        changed = False
        with self._lock:
            changed = self._gc(now)
            key = _hash(token)
            s = self._s.get(key)
            if s is not None and s.get("pw") is not None and s["pw"] != pw:
                self._s.pop(key, None)          # the password changed since this login
                s, changed = None, True
            if s is not None:
                s["seen"] = now
                out = dict(s, expires_in=int(IDLE))
            else:
                out = None
        if changed:
            self._changed()
        return out

    def alive(self, token):
        """Is the session of a cookie token still there (not dropped by «Выйти», not expired, the password the same)?
        Unlike ``get`` its idle clock does not restart: an open event stream must not keep a session alive."""
        if not token:
            return False
        now = _now()
        pw = _password_mark()
        with self._lock:
            s = self._s.get(_hash(token))
            if s is None or now - s["seen"] > IDLE or now - s["created"] > MAX_AGE:
                return False
            return s.get("pw") is None or s["pw"] == pw

    def drop(self, token):
        with self._lock:
            gone = self._s.pop(_hash(token), None) is not None if token else False
        if gone:
            self._changed()
        return gone

    def drop_all(self, keep=None):
        keep_key = _hash(keep) if keep else None
        with self._lock:
            n = len(self._s)
            self._s = dict((k, v) for k, v in self._s.items() if k == keep_key)
            gone = n - len(self._s)
        if gone:
            self._changed()
        return gone

    def drop_remote(self):
        """3.3.7.5: the secret path of the panel changed — every session made from outside (the domain, the port) ends
        at once, not only the cookie of the old path; those of the SSH tunnel (a loopback address) stay. -> how many."""
        def local(s):
            a = _ip(s.get("ip"))
            return a is not None and a.is_loopback
        with self._lock:
            n = len(self._s)
            self._s = dict((k, v) for k, v in self._s.items() if local(v))
            gone = n - len(self._s)
        if gone:
            self._changed()
        return gone

    def count(self):
        with self._lock:
            self._gc(_now())
            return len(self._s)


SESSIONS = Sessions()


def cookie_token(headers):
    """The session token of the Cookie header (``headers`` with lower-case names) or ""."""
    raw = headers.get("cookie") or ""
    for part in raw.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip() == COOKIE:
            v = v.strip().strip('"')
            if re.match(r"^[A-Za-z0-9_-]{20,128}$", v):
                return v
    return ""


def set_cookie(token, secure):
    return "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" % (COOKIE, token, "; Secure" if secure else "")


def clear_cookie(secure):
    return "%s=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/%s" % (COOKIE, "; Secure" if secure else "")


def cookie_for(header, secure, path="/"):
    """Our ``Set-Cookie`` with the attributes of the listener (behind nginx: always ``Secure``, ``Path`` — the prefix
    of the panel); a cookie of another name stays as it is."""
    parts = [p.strip() for p in str(header).split(";")]
    if parts[0].split("=", 1)[0].strip() != COOKIE:
        return header
    attrs = [p for p in parts[1:] if p and p.split("=", 1)[0].strip().lower() not in ("path", "secure")]
    attrs.append("Path=%s" % path)
    if secure:
        attrs.append("Secure")
    return "; ".join([parts[0]] + attrs)


# ── one-time links ─────────────────────────────────────────────────────────

def links_path():
    return os.path.join(paths.panel_dir(), "links.json")


@contextlib.contextmanager
def _links_lock():
    util.ensure_dir(paths.panel_dir(), 0o700)
    fd = os.open(os.path.join(paths.panel_dir(), "links.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _links_load(now):
    raw = util.read_text(links_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    rows = doc.get("links") if isinstance(doc, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and re.match(r"^[0-9a-f]{64}$", str(r.get("hash") or "")) \
                and isinstance(r.get("expires"), (int, float)) and r["expires"] > now:
            out.append(r)
    return out


def _links_save(rows):
    util.atomic_write(links_path(), json.dumps({"schema": 1, "links": rows}, sort_keys=True) + "\n", 0o600)


def issue_link(by="cli", ttl=LINK_TTL):
    """A new one-time login token (the caller builds the URL: ``kaskad.panel.cli.login_url``)."""
    ttl = max(60, min(int(ttl), 24 * 3600))
    token = _random.token_urlsafe(32)
    now = _now()
    with _links_lock():
        rows = _links_load(now)
        rows.append({"hash": _hash(token), "created": int(now), "expires": int(now + ttl), "by": str(by)[:16]})
        _links_save(rows[-LINK_MAX:])
    return token


def login_link(ttl=LINK_TTL, by="bot"):
    """A one-time login link for the bot and the SSH menu (the contract with WP-B):
    {"url": str|null, "token": str, "ttl": int}. ``url`` — https://<host>:<port>/login?t=… in the open mode,
    http://127.0.0.1:1980/login?t=… (after the SSH tunnel) otherwise; null when the panel is not installed
    (nothing would answer). ``ttl`` — seconds it lives (60 s … 24 h)."""
    from . import conf
    ttl = max(60, min(int(ttl), 24 * 3600))
    token = issue_link(by=by, ttl=ttl)
    installed = os.path.exists(os.path.join(paths.systemd_dir(), conf.UNIT))
    return {"url": conf.login_url(token) if installed else None, "token": token, "ttl": ttl}


def redeem_link(token):
    """True once for a valid token (it burns at once, under the lock: two requests with one link — one login)."""
    if not token or not re.match(r"^[A-Za-z0-9_-]{20,128}$", str(token)):
        return False
    h = _hash(token)
    now = _now()
    with _links_lock():
        rows = _links_load(now)
        keep = [r for r in rows if not hmac.compare_digest(str(r["hash"]), h)]
        hit = len(keep) != len(rows)
        if hit:
            _links_save(keep)
    return hit
