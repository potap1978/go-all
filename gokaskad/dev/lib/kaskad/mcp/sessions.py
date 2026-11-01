"""Open SSH sessions of AI agents, and ending them when their key is deleted.

sshd does not drop a connection when its key leaves authorized_keys: an agent that holds one (Claude Desktop keeps it
for days) would go on managing the node after «Ключ удалён». kaskad.mcp.keys.delete calls ``end_for(fp)``.

A session is a ``kaskad-mcp`` process with SSH_CONNECTION in its environment (the forced command of an agent's key:
sshd -> the wrapper /usr/local/bin/kaskad-mcp -> ``exec python3 -I -c '… run_module("kaskad.mcp" …'``). Nothing else
is touched: not the owner's shell, not sshd, not a ``kaskad-mcp`` started without SSH. When the process exits, sshd
closes that connection by itself.

Whose key opened a session — the ``Accepted publickey … from IP port P … SHA256:…`` line of sshd for its
SSH_CONNECTION (kaskad.mcp.seen reads the logs). A session of the deleted key ends; so does a session whose key the
logs do not name (it may be that key): then the answer says so (``unsure``) and the panel writes that the
connections of the agents were cut. A session the logs prove to be another key's stays.

    end_for(fp, runner) -> {"ended": n, "unsure": bool}      fp — one fingerprint or several (a list)
"""

import os
import signal
import time

from .. import paths

GRACE = 2.0                       # s: SIGTERM, then SIGKILL
SLACK = 600.0                     # s: the logs are read from a little before the oldest session started
_kill = os.kill
_sleep = time.sleep
_now = time.time
_SHELLS = ("sh", "bash", "dash", "zsh")


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _argv(pid):
    raw = _read(paths.P("/proc/%d/cmdline" % pid))
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0")[:-1 if raw.endswith(b"\0") else None]] \
        if raw else []


def is_mcp(argv):
    """Is this the command line of kaskad-mcp: the python of the wrapper, the wrapper, or a shell running it."""
    if not argv:
        return False
    base = os.path.basename(argv[0])
    if base == "kaskad-mcp":
        return True
    if base.startswith("python"):
        return any('run_module("kaskad.mcp"' in a for a in argv[1:])
    if base in _SHELLS:
        rest = [a for a in argv[1:] if a != "-c"]
        return bool(rest) and os.path.basename(rest[0].split(" ")[0]) == "kaskad-mcp" and len(rest[0].split()) == 1
    return False


def _ssh(pid):
    """("ip", "port") of SSH_CONNECTION of the process, else None."""
    raw = _read(paths.P("/proc/%d/environ" % pid))
    for item in (raw or b"").split(b"\0"):
        if item.startswith(b"SSH_CONNECTION="):
            f = item[len(b"SSH_CONNECTION="):].decode("ascii", "replace").split()
            if len(f) == 4 and f[1].isdigit():
                return f[0], f[1]
    return None


def _ticks(pid):
    """Field 22 of /proc/<pid>/stat: when the process started, in clock ticks since boot; else None. It does not move
    when the clock is stepped (btime in /proc/stat does) — this is what tells one process from another (R-333 П2‴)."""
    stat = _read(paths.P("/proc/%d/stat" % pid))
    try:
        return int(stat.rsplit(b")", 1)[1].split()[19])
    except (AttributeError, IndexError, ValueError):
        return None


def _started(pid, ticks=None):
    """When the process started (epoch), else None. Only for how far back the logs are read."""
    ticks = _ticks(pid) if ticks is None else ticks
    if ticks is None:
        return None
    sys_stat = _read(paths.P("/proc/stat"))
    try:
        btime = next(int(l.split()[1]) for l in sys_stat.splitlines() if l.startswith(b"btime "))
        return btime + ticks / float(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, IndexError, ValueError, StopIteration, OSError):
        return None


def list_sessions():
    """[{"pid", "ip", "port", "ticks", "started"}] of kaskad-mcp processes started by sshd."""
    out, me = [], {os.getpid(), os.getppid()}
    try:
        names = os.listdir(paths.P("/proc"))
    except OSError:
        return out
    for n in names:
        if not n.isdigit() or int(n) in me:
            continue
        pid = int(n)
        if not is_mcp(_argv(pid)):
            continue
        conn = _ssh(pid)
        if conn:
            ticks = _ticks(pid)
            out.append({"pid": pid, "ip": conn[0], "port": conn[1], "ticks": ticks, "started": _started(pid, ticks)})
    return out


def _same(s):
    """Is the pid still the very session that was listed: kaskad-mcp, the same SSH_CONNECTION, the same start time.
    Between the listing and a signal the logs are read (up to 15 s) and GRACE passes: the pid may have gone to another
    process meanwhile — sshd, a shell, a new session of another key (R-333 П1″). The start time is compared in raw
    ticks since boot, not as an epoch: a clock step between the listing and the signal must not hide the session."""
    pid = s["pid"]
    return is_mcp(_argv(pid)) and _ssh(pid) == (s["ip"], s["port"]) and _ticks(pid) == s["ticks"]


def _end(ss):
    """SIGTERM, then SIGKILL to what is still that session. The number of the processes signalled."""
    sent = []
    for s in ss:
        if not _same(s):                        # gone, or the pid is another process now
            continue
        try:
            _kill(s["pid"], signal.SIGTERM)
            sent.append(s)
        except OSError:                         # gone already
            pass
    until = _now() + GRACE
    while sent and _now() < until and any(_same(s) for s in sent):
        _sleep(0.1)
    for s in sent:
        if _same(s):
            try:
                _kill(s["pid"], signal.SIGKILL)
            except OSError:
                pass
    return len(sent)


def end_for(fp, runner):
    """End the sessions of the key ``fp`` (or of any of the keys, a list) and those whose key is not known.
    {"ended": n, "unsure": bool}."""
    from . import seen
    fps = {fp} if isinstance(fp, str) else set(fp)
    ss = list_sessions()
    if not ss:
        return {"ended": 0, "unsure": False}
    now = _now()
    starts = [s["started"] for s in ss if s["started"] is not None]
    since = (min(starts) if len(starts) == len(ss) else now - seen.DEPTH) - SLACK
    try:
        found, _complete = seen._scan(runner, max(since, now - seen.DEPTH))
    except (OSError, ValueError):
        found = None
    found = found or {}
    mine, unknown = [], []
    for s in ss:
        who = found.get(s["ip"] + " " + s["port"])
        if who in fps:
            mine.append(s)
        elif not who:
            unknown.append(s)
    ended = _end(mine + unknown) if (mine or unknown) else 0
    return {"ended": ended, "unsure": bool(unknown) and ended > 0}
