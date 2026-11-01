"""apt on a fresh server: unattended-upgrades holds the dpkg lock for many minutes after boot, and an
apt-get with a short ``DPkg::Lock::Timeout`` failed with a cryptic message.

    holder()              who holds an apt/dpkg lock now: {"path", "pid", "name"} or None
    wait()                block until no lock is held, up to WAIT (600 s). Before waiting one line WAIT_LINE
                          to stderr — only when a lock is actually held. KaskadError after WAIT naming the holder
    OPTS                  ``-o DPkg::Lock::Timeout=600`` for every apt-get: a lock taken again between wait()
                          and the start of apt-get is waited for by apt itself
    RUN_TIMEOUT           runner timeout of one apt-get: apt's own lock wait plus the work
    failure_text(res)     the clear text of an apt-get that failed on the lock anyway, else None

The probe is ``fcntl(F_GETLK)``: the kernel answers who holds a conflicting lock and nothing is taken — apt,
dpkg and unattended-upgrades (classic fcntl locks) never notice it. A lock file that does not exist or cannot
be opened is free. install.sh has the same probe for its own apt-get calls (k3_apt_holder).

Installs of Kaskad itself (``gw.sys.apt_install``: packages under a temporary /usr/sbin/policy-rc.d that
answers 101, so their services do not start):

    installing()          one install of Kaskad at a time — the menu and the bot may install at once, and the
                          first would take policy-rc.d away in the middle of the second's apt (flock on
                          /run/kaskad/apt.lock, waiting up to INSTALL_WAIT with one line on stderr)
    POLICY_RC_D           our policy-rc.d; POLICY_MARK inside tells it from a foreign one (Docker images)
    policy_leftover()     our policy-rc.d is left behind (an install killed by SIGKILL, OOM, a power cut — its
    policy_cleanup()      ``finally`` never ran): no install of Kaskad runs and apt/dpkg are idle. While it
                          stays, no package service starts on install or upgrade (openssh-server included);
                          the minute watchdog (netcheck) removes it
"""

import contextlib
import fcntl
import os
import re
import struct
import sys
import time

from . import paths, util
from .errors import KaskadError
from .i18n import tr

WAIT = 600
POLL = 2.0
RUN_TIMEOUT = WAIT + 1200
INSTALL_WAIT = RUN_TIMEOUT          # another install of Kaskad: its apt may wait 10 min and work 20
OPTS = ["-o", "DPkg::Lock::Timeout=%d" % WAIT]
# WAIT_LINE, BUSY_LINE (module attributes, see __getattr__): install.sh k3_gw_wait_lines shows them by their start
_LAZY = {"WAIT_LINE": "aptlock.wait_line", "BUSY_LINE": "aptlock.busy_line"}
POLICY_MARK = "Kaskad 3: службы пакетов не запускать при установке"  # i18n: skip — marker in the file: tells ours
POLICY_RC_D = "#!/bin/sh\n# %s (apt под policy-rc.d)\nexit 101\n" % POLICY_MARK  # i18n: skip — file content

_now = time.monotonic                   # tests patch these two
_sleep = time.sleep

# struct flock — Linux: {short l_type, l_whence; off_t l_start, l_len; pid_t l_pid};
# macOS/BSD (the tests run there too): {off_t l_start, l_len; pid_t l_pid; short l_type, l_whence}
_LINUX = sys.platform.startswith("linux")
_FLOCK = "hhqqi" if _LINUX else "qqihh"
_INTERPRETERS = re.compile(r"^(python[0-9.]*|perl[0-9.]*|sh|bash|dash)$")
_LOCK_ERROR = re.compile(r"Could not get lock|Unable to acquire the dpkg frontend lock|Unable to lock "
                         r"(the )?(administration|download) directory|is another process using it")
_HELD_BY = re.compile(r"held by process (\d+) \(([^)]*)\)")


def __getattr__(name):
    """WAIT_LINE, BUSY_LINE in the language of the moment (PEP 562): a text of the module is not fixed at import."""
    if name in _LAZY:
        return tr(_LAZY[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _lock_pid(path):
    """pid of the process holding a lock on ``path`` (0: held by someone unknown), None if free."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOCTTY", 0))
    except OSError:
        return None
    try:
        if _LINUX:
            req = struct.pack(_FLOCK, fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
        else:
            req = struct.pack(_FLOCK, 0, 0, 0, fcntl.F_WRLCK, os.SEEK_SET)
        got = struct.unpack(_FLOCK, fcntl.fcntl(fd, fcntl.F_GETLK, req))
    except (OSError, IOError, struct.error, ValueError):
        return None
    finally:
        os.close(fd)                    # we hold no lock on these files: closing releases nothing of apt's
    kind, pid = (got[0], got[4]) if _LINUX else (got[3], got[2])
    if kind == fcntl.F_UNLCK:
        return None
    return pid if pid > 0 else 0


def _name(pid):
    """What people call the process: «unattended-upgrade», «apt-get», «dpkg» (a script's name, not its
    interpreter's); "" if unknown."""
    if not pid:
        return ""
    try:
        with open(paths.proc(pid, "cmdline"), "rb") as f:
            args = [a for a in f.read().split(b"\0") if a]
    except (OSError, IOError):
        args = []
    names = [os.path.basename(a.decode("utf-8", "replace")) for a in args[:2]]
    if len(args) > 1 and _INTERPRETERS.match(names[0]) and not args[1].startswith(b"-"):
        name = names[1]
    elif names:
        name = names[0]
    else:
        name = (util.read_text(paths.proc(pid, "comm")) or "").strip()
    return re.sub(r"[^\w.+:-]", "", name)[:40]


def holder():
    """{"path", "pid", "name"} of the first held apt/dpkg lock, or None when apt is free."""
    for p in paths.apt_locks():
        pid = _lock_pid(p)
        if pid is not None:
            return {"path": p, "pid": pid, "name": _name(pid)}
    return None


def who(h):
    """«unattended-upgrade (pid 1234)», «pid 1234» or "" (unknown)."""
    if not h:
        return ""
    if h.get("pid"):
        return "%s (pid %d)" % (h["name"], h["pid"]) if h.get("name") else "pid %d" % h["pid"]
    return h.get("name") or ""


def busy_text(h, timeout=None):
    """The error when apt stays busy: who holds it, if known. ``timeout`` — how long we waited (seconds)."""
    if timeout is None:
        lim = ""
    elif timeout == 600:
        lim = tr("aptlock.lim.ten_min")
    else:
        lim = tr("aptlock.lim.sec", sec=int(timeout))
    w = who(h)
    if w:
        return tr("aptlock.busy.who", lim=lim, who=w)
    return tr("aptlock.busy.anon", lim=lim)


def _stderr(text):
    sys.stderr.write((util.core_line("wait", text) or text) + "\n")
    sys.stderr.flush()


def wait(timeout=None, say=None):
    """Block until no apt/dpkg lock is held. Returns the seconds waited (0 when apt was free). Before waiting
    prints WAIT_LINE (stderr, or ``say(text)``) — only when a lock is actually held. KaskadError after
    ``timeout`` (WAIT) seconds, naming the holder. The lock itself is never taken."""
    timeout = WAIT if timeout is None else timeout
    h = holder()
    if h is None:
        return 0
    (say or _stderr)(tr("aptlock.wait_line"))
    t0 = _now()
    while True:
        if _now() - t0 >= timeout:
            with util.journal_lang():
                util.log_action(tr("aptlock.log.gave_up", who=who(h) or tr("aptlock.log.unknown")))
            raise KaskadError(busy_text(h, timeout))
        _sleep(POLL)
        nxt = holder()
        if nxt is None:
            waited = int(_now() - t0)
            util.log_tr("aptlock.log.waited", sec=waited, who=who(h) or "?")
            return waited
        h = nxt


def failure_text(res):
    """A clear Russian text when apt-get failed because the lock was taken anyway (by its message), else None."""
    text = "%s\n%s" % (res.err or "", res.out or "")
    if not _LOCK_ERROR.search(text):
        return None
    m = _HELD_BY.search(text)
    if m:
        pid = int(m.group(1))
        h = {"pid": pid, "name": _name(pid) or re.sub(r"[^\w.+:-]", "", m.group(2))[:40]}
    else:
        h = holder()
    return busy_text(h)


# ── installs of Kaskad: one at a time, policy-rc.d never left behind ───────

def _open_install_lock():
    p = paths.apt_install_lock()
    util.ensure_run_dir(os.path.dirname(p))         # /run/kaskad: 0711, never 0700 (nginx → panel socket)
    return os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)


@contextlib.contextmanager
def installing(say=None):
    """Hold the install lock of Kaskad for the block (flock: the kernel drops it with the process, even on
    SIGKILL). Another install holds it -> one line BUSY_LINE (stderr or ``say``), waiting up to INSTALL_WAIT,
    then KaskadError."""
    fd = _open_install_lock()
    try:
        t0, said = _now(), False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not said:
                    (say or _stderr)(tr("aptlock.busy_line"))
                    said = True
                if _now() - t0 >= INSTALL_WAIT:
                    raise KaskadError(tr("aptlock.install_busy", minutes=INSTALL_WAIT // 60))
                _sleep(POLL)
        yield
    finally:
        os.close(fd)                    # closing the descriptor drops the lock


def policy_is_ours(text):
    return bool(text) and POLICY_MARK in text


def _leftover():
    """With the install lock taken: our policy-rc.d is there and apt/dpkg are idle."""
    return policy_is_ours(util.read_text(paths.policy_rc_d())) and holder() is None


def policy_leftover():
    """Our policy-rc.d is left behind: it carries POLICY_MARK, no install of Kaskad runs (its lock is free — taken
    for a moment, never waited for) and apt/dpkg are idle. Read-only otherwise."""
    if not policy_is_ours(util.read_text(paths.policy_rc_d())):
        return False
    try:
        fd = _open_install_lock()
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False                # an install of Kaskad is running: the file is its own
        return _leftover()
    finally:
        os.close(fd)


def policy_cleanup():
    """Remove a leftover policy-rc.d of Kaskad (see policy_leftover). The install lock is held while it is
    checked and removed, so an install that starts meanwhile waits. True if removed."""
    if not policy_is_ours(util.read_text(paths.policy_rc_d())):
        return False
    try:
        fd = _open_install_lock()
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        if not _leftover():
            return False
        try:
            os.unlink(paths.policy_rc_d())
        except OSError:
            return False
        return True                     # the caller (netcheck) logs it
    finally:
        os.close(fd)
