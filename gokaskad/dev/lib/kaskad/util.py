"""Small helpers: atomic writes, timestamps, the shared log in 2.x format, secret redaction."""

import contextlib
import datetime
import errno
import json
import os
import re
import tempfile
import threading
import time

from . import paths


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def now_iso():
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp():
    """Compact UTC timestamp for backup ids: 20261004T120000Z."""
    return _utcnow().strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path, mode=0o700):
    if _in_run_dir(path):
        # its parent /run/kaskad first: os.makedirs would make a missing one by the umask (077 → 0700 — nginx loses the
        # panel's socket); /run/kaskad itself is never chmod'ed to ``mode`` (ensure_run_dir keeps the passage)
        ensure_run_dir()
        if _is_run_dir(path):
            return
    if not os.path.isdir(path):
        os.makedirs(path, mode, exist_ok=True)         # another process may make it between the check and here
    try:
        os.chmod(path, mode)
    except OSError:
        pass


RUN_DIR_MODE = 0o711       # /run/kaskad: nginx (www-data) passes through it to /run/kaskad/panel/proxy.sock


def ensure_run_dir(path=None):
    """/run/kaskad (or ``path``): created 0711; an existing one never loses the passage of group and others (x) — its
    other bits stay as they are. What is inside keeps its own modes (files 0600, panel/ 0710 of www-data). 3.2.21: the
    lock of the pool and the bot made it 0700 — nginx lost the panel's socket, the panel on the domain answered 502.
    Returns the path."""
    d = path or paths.run_dir()
    if not os.path.isdir(d):
        try:
            os.makedirs(d, RUN_DIR_MODE, exist_ok=True)
        except FileExistsError:                        # a dangling link or a file in its place: nothing to fix here
            return d
        try:
            os.chmod(d, RUN_DIR_MODE)                  # the umask of the caller must not take the passage away
        except OSError:
            pass
        return d
    try:
        m = os.stat(d).st_mode & 0o7777
        if m & 0o011 != 0o011:
            os.chmod(d, m | 0o011)
    except OSError:
        pass
    return d


CONFIG_LOCK_WAIT = 10.0     # s: a write of /etc/kaskad/config takes milliseconds; longer — written without the lock
_config_depth = threading.local()


# SEC-3372 П-3: the line breaks Python's ``splitlines`` knows besides «\n» — bash, which sources /etc/kaskad/config,
# splits by «\n» alone; a value with one of them, rewritten through ``splitlines``, became a line of its own (unquoted)
EXTRA_BREAKS = re.compile("[\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")


def config_lines(text):
    """The lines of /etc/kaskad/config the way bash reads them: split by «\\n» only (no trailing empty one)."""
    lines = (text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@contextlib.contextmanager
def config_locked():
    """One read-modify-write of /etc/kaskad/config at a time among the writers of Python (the zone, the language,
    the bot, the restore of a copy): ``flock`` of /run/kaskad/config.lock — another process or thread waits, so no
    line of another writer (``BOT_TOKEN``) is lost (проверка безопасности T7, П-2). Nested in one thread — no second
    lock. Never a failure: no lock in CONFIG_LOCK_WAIT s or no run dir — the write goes on without it, a line in the
    journal."""
    if getattr(_config_depth, "n", 0):
        _config_depth.n += 1
        try:
            yield
        finally:
            _config_depth.n -= 1
        return
    fd = None
    try:
        ensure_run_dir()
        fd = os.open(os.path.join(paths.run_dir(), "config.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        import fcntl
        deadline = time.monotonic() + CONFIG_LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    except OSError as e:
        if fd is not None:
            os.close(fd)
            fd = None
        try:
            log_action("config: written without its lock (%s)" % (e.strerror or type(e).__name__))
        except Exception:                              # noqa: BLE001 — the journal is no reason to lose the write
            pass
    _config_depth.n = 1
    try:
        yield
    finally:
        _config_depth.n = 0
        if fd is not None:
            os.close(fd)


def _is_run_dir(d):
    return os.path.normpath(d) == os.path.normpath(paths.run_dir())


def _in_run_dir(d):
    """/run/kaskad or anything under it."""
    r, d = os.path.normpath(paths.run_dir()), os.path.normpath(d)
    return d == r or d.startswith(r + os.sep)


def atomic_write(path, data, mode=0o600):
    """Write via a temp file in the same directory + fsync + rename. ``data`` is str or bytes."""
    d = os.path.dirname(path) or "."
    if _in_run_dir(d):
        ensure_run_dir()                               # /run/kaskad: never 0700 (nginx → the panel's socket)
    if not os.path.isdir(d):
        os.makedirs(d, 0o700, exist_ok=True)           # the same race as in ``ensure_dir``
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path):
    """File contents or None if it does not exist / is unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, IOError):
        return None


def json_text(obj):
    """The one JSON style for files Kaskad writes (state.json is grepped by bash: ``"engine": "nft"``)."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ── numbers of JSON (REV-33714 С-1) ─────────────────────────────────────────
# Python reads ``1e400`` as inf (parse_float, not parse_constant) and an integer of 400 digits as an int that
# float() cannot take: «limit_gb» × 1e9, round(lim × 10), float(tolerance_ms) gave OverflowError in the policy, the
# collector and the health step. One bound for every number Kaskad takes: far above any real one (bytes, ms, GB; the
# safe integer of a browser is 9·10^15), far below where × 10^9 or × 1024^3 overflows a float.
NUM_MAX = 10 ** 18
_INT_LEN = len(str(-NUM_MAX))                   # an integer literal longer than this is past ±NUM_MAX (JSON: no 0012)


def _float_in(s):
    v = float(s)
    if not -NUM_MAX <= v <= NUM_MAX:            # inf fails too
        raise ValueError("number out of range")
    return v


def _int_in(s):
    if len(s) > _INT_LEN or not -NUM_MAX <= int(s) <= NUM_MAX:     # never int() of 4300 digits (ValueError anyway)
        raise ValueError("number out of range")
    return int(s)


def _const_in(name):
    raise ValueError("not JSON: %s" % name)


def json_in(text):
    """JSON from outside (the API of the panel and of the setup, MCP, a body on stdin): NaN, Infinity, -Infinity
    (RFC 8259: no JSON), a number past ±NUM_MAX (``1e400``, ``-1e999``, ``1e308``, an integer of 400 digits) —
    ValueError, as any broken JSON: the caller answers «not JSON» (400), nothing reaches a check or a file."""
    return json.loads(text, parse_constant=_const_in, parse_float=_float_in, parse_int=_int_in)


def _float_kept(s):
    v = float(s)
    return v if -NUM_MAX <= v <= NUM_MAX else float(NUM_MAX if v > 0 else -NUM_MAX)


def _int_kept(s):
    if len(s) <= _INT_LEN:
        v = int(s)
        if -NUM_MAX <= v <= NUM_MAX:
            return v
    return -NUM_MAX if s.startswith("-") else NUM_MAX


def _const_kept(name):
    return float("nan") if name == "NaN" else float(NUM_MAX if name == "Infinity" else -NUM_MAX)


def json_kept(text):
    """JSON of a file Kaskad wrote (state.json): a number past ±NUM_MAX and ±Infinity (an older version took
    ``1e400`` or an integer of 400 digits into the policy or a quota) are read as ±NUM_MAX — the same «never
    reached» as before, now a finite number every sum and float() takes. NaN stays NaN (it never overflows)."""
    return json.loads(text, parse_constant=_const_kept, parse_float=_float_kept, parse_int=_int_kept)


def log_action(text):
    """Append to /var/log/kaskad.log in the 2.x format: ``[YYYY-mm-dd HH:MM:SS] text``."""
    line = "[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), redact(text))
    p = paths.log()
    try:
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, 0o755)
        new = not os.path.exists(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
        if new:
            os.chmod(p, 0o600)
    except OSError:
        pass


# ── messages kept as codes ─────────────────────────────────────────────────
# A state on disk keeps a message as {"code": <catalog key>, "args": {…}} (+ "n" for a plural), not as text: the text is
# said at the moment of showing, in the language of the reader (k3/handoff/I18N-3.3.2.md). An argument may be a message
# itself or a list of them («; »-joined) — the nested words follow the reader too; anything else is data (str()).

def _plain(v):
    if isinstance(v, BaseException):
        return getattr(v, "msg", None) or str(v)
    if v is None or isinstance(v, (bool, int, float, str, dict, list)):
        return v
    return str(v)


def msg(key, **args):
    """A message to keep: {"code": key, "args": {…}}."""
    return {"code": key, "args": dict((k, _plain(v)) for k, v in args.items())}


def msg_n(key, n, **args):
    """A plural message to keep (i18n.trn): {"code": key, "n": n, "args": {…}}."""
    m = msg(key, **args)
    m["n"] = n
    return m


def said(m):
    """The text of a kept message in the language of the call; a plain string — as it is (an old record, data)."""
    from .i18n import tr, trn
    if isinstance(m, list):
        return "; ".join(said(x) for x in m)
    if not isinstance(m, dict) or not isinstance(m.get("code"), str):
        return "" if m is None else str(m)
    args = dict((k, said(v) if isinstance(v, (dict, list)) else v) for k, v in (m.get("args") or {}).items())
    if "n" in m:
        return trn(m["code"], m["n"], **args)
    return tr(m["code"], **args) if args else tr(m["code"])


def err(cls, key, **args):
    """An exception whose text is the message ``key`` now and which keeps the message itself (``.msg``) for a state
    that says it again later in another language."""
    m = msg(key, **args)
    e = cls(said(m))
    e.msg = m
    return e


def core_line(kind, text):
    """A line of the core for the SSH menu that reads it (it asks with K3_MARKS=1): «@K3:warn|error|wait <text>» —
    the menu looks at the mark, not at a word of a language (k3/handoff/I18N-3.3.2.md, «Обмен меню ↔ ядро»). None
    without K3_MARKS (a person at the console): the caller says it with its word («Внимание: …»)."""
    if os.environ.get("K3_MARKS") == "1":
        return "@K3:%s %s" % (kind, text)
    return None


def journal_lang():
    """Context: the language of the server for the words of a journal line, whatever the language of the request or
    chat that caused it — the journal is one file read by the owner of the server. The language is the one SAVED in
    /etc/kaskad/config (i18n.saved_lang), not the environment: a job of the panel runs with KASKAD_LANG of its request
    (i18n.lang_env), and its journal lines must still be in the server's language. For a line whose arguments are
    words of the catalog too: ``with util.journal_lang(): util.log_action(tr(..., part=tr(...)))``."""
    from .i18n import saved_lang, use_lang
    return use_lang(saved_lang())


def log_tr(key, **kw):
    """log_action of a catalog string in the language of the server (journal_lang). The arguments are evaluated by
    the caller — words of the catalog among them: journal_lang around the whole line."""
    from .i18n import tr
    with journal_lang():
        log_action(tr(key, **kw))


_SECRET_PATTERNS = [            # (regex, replacement template: {hidden} — the localized marker, see redact)
    (re.compile(r"(?i)(vpn://)[A-Za-z0-9_=+/\-]+"), r"\1<{hidden}>"),
    (re.compile(r"(?i)(awg://)[A-Za-z0-9_=+/\-]+"), r"\1<{hidden}>"),
    (re.compile(r"(?i)(vless://)[^@\s]+@"), r"\1<{hidden}>@"),
    # stage 1 (SPEC-stage1-exits §9.7): the keys of the other xray schemes — the password before «@» (hy2/hysteria2:
    # auth, trojan, ss SIP002: base64 «method:password»), ss in the old «all in base64» and vmess (base64 JSON with the
    # id) whole up to «#» or a space, obfs-password of Hysteria2; pcs/pinSHA256 are no secret, «#name» stays. Every
    # scheme in any case (REV-s1-t11 З1: the parser takes «VLESS://», «Trojan://»)
    (re.compile(r"(?i)\b((?:hy2|hysteria2|trojan)://)[^@\s]+@"), r"\1<{hidden}>@"),
    (re.compile(r"(?i)(?<![a-z0-9+./-])(ss://)[^@\s#]+@"), r"\1<{hidden}>@"),
    (re.compile(r"(?i)(?<![a-z0-9+./-])(ss://)(?![^\s#\"'<>]*@)[^\s#\"'<>]+"), r"\1<{hidden}>"),
    (re.compile(r"(?i)\b(vmess://)[^\s#\"'<>]+"), r"\1<{hidden}>"),
    (re.compile(r"(?i)\b(obfs-password=)[^&#\s\"'<>]+"), r"\1<{hidden}>"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<{gh}>"),
    # a bot token also inside a URL of the Bot API (…/bot<token>/getUpdates): no word boundary before it there
    (re.compile(r"(?<![0-9])\d{6,12}:[A-Za-z0-9_\-]{30,}(?![A-Za-z0-9_\-])"), "<{bot}>"),
    (re.compile(r"((?:PrivateKey|PresharedKey|HeaderProtectionKey)\s*=\s*)\S+"), r"\1<{hidden}>"),
    (re.compile(r"(accel-cmd\b[^\n]*?\s-P\s+)\S+"), r"\1<{hidden}>"),                 # accel-ppp CLI password
]


def redact(text):
    """Hide keys/tokens in anything that goes to logs, reports or the terminal."""
    from .i18n import tr
    marks = {"hidden": tr("util.redact.hidden"), "gh": tr("util.redact.github"), "bot": tr("util.redact.bot_token")}
    s = str(text)
    for rx, rep in _SECRET_PATTERNS:
        s = rx.sub(rep.replace("{hidden}", marks["hidden"]).replace("{gh}", marks["gh"]).replace("{bot}", marks["bot"]),
                   s)
    # 3.3.3: words of tr() that remember their key (i18n.Said, a job of the wizard) stay so when nothing was hidden —
    # the page says them again in its language
    from .i18n import Said
    return text if isinstance(text, Said) and s == text else s


def n_bytes(n):
    """«1 байт», «1372 байта», «1360 байт» (T-N5 3.2.20, Н-4: было «1372 байт»)."""
    from .i18n import trn
    return trn("util.bytes", int(n))


def human_bytes(n):
    """Size for people in the language of the call: kaskad.i18n.fmt_bytes (kept for the callers)."""
    from .i18n import fmt_bytes
    return fmt_bytes(n)
