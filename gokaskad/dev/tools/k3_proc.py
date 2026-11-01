#!/usr/bin/env python3
"""k3_proc.py — a step of tools/k3_test.sh with everything it started (REV-speed-4 С8, З7).

    python3 tools/k3_proc.py <command> [args…]

Runs the command in a session of its own and waits for it; the code is the command's (killed by signal N — 128+N, as
bash says; stopped by a signal — written to $K3_STOP_FILE, see said_stopped). INT, TERM, HUP or QUIT to this process, or its parent gone (a foreign «pkill -f tools/k3_test.sh» killed
the shell that started it), — the command is stopped with every process it started at any depth: those in a group of
their own too (headless Chrome of the layout checks is started with «detached: true» and leaves the group of node),
and the list is taken before anything is killed — a parent gone, its children are not found by it any more.

In a session of its own the command does not get the Ctrl-C of the terminal: it is stopped by this process only, the
whole tree at once (REV-speed-4: node died of Ctrl-C before the trap of k3_test.sh walked the tree, Chrome stayed).
SIGINT and SIGQUIT in the command are by default even when this process got them ignored (a job started with «&» by
a script — REV-speed-3 С6): a handler of Python is reset to the default by exec.

HUP that came ignored («nohup»: the run is to outlive a closed terminal or a dropped SSH) stays ignored here — the
run goes on to its summary (REV-speed-5 З8); in the command it is by default (a test of the SSH drop, test_aptlock).

KILL is not caught (REV-speed-5 З9). A sentinel (python3 k3_proc.py --watch, a session of its own: KILL to the group
of k3_test.sh does not reach it) reads a pipe only this process writes: end of file without «done» — this process is
gone, the sentinel stops the command with everything it started. $K3_TEST_PID (k3_test.sh sets it) gone — a KILL to
k3_test.sh only, while the parent here is a subshell of it — the command is stopped too. Ctrl-Z (TSTP) freezes the
command with its tree before this process stops, and CONT goes on to them after it (the command is in a session of
its own, the TSTP of the terminal does not reach it).

tools/k3_unit_par.py stops its modules by kill_tree() of this file.
"""
import os
import signal
import subprocess
import sys
import time


def snapshot():
    """{pid: (ppid, pgid, zombie)} of every process of the machine (ps; nothing — an empty dict)."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    procs = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 4 and all(x.isdigit() for x in f[:3]):
            procs[int(f[0])] = (int(f[1]), int(f[2]), f[3].startswith("Z"))
    return procs


def members(roots, procs, keep=()):
    """The roots and every process under them by ppid, plus the processes of their groups (a group of its own of a
    process found — Chrome and its helpers). Never this process, its group or the groups in keep."""
    me, mygroup = os.getpid(), os.getpgrp()
    keep = set(keep) | {0, 1, mygroup}
    kids = {}
    for pid, (ppid, _, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    found = set()
    todo = [r for r in roots if r and r > 1]
    while todo:
        pid = todo.pop()
        if pid in found or pid == me:
            continue
        found.add(pid)
        todo.extend(kids.get(pid, ()))
    groups = {procs[p][1] for p in found if p in procs} - keep
    for pid, (_, pgid, _) in procs.items():
        if pgid in groups and pid not in found and pid != me:
            found.add(pid)
            todo.append(pid)
    while todo:                                   # what the processes of those groups started
        pid = todo.pop()
        for c in kids.get(pid, ()):
            if c not in found and c != me:
                found.add(c)
                todo.append(c)
    return found


def _send(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def alive(pids, procs=None):
    """The processes still running (a zombie in procs — gone: it only waits for its parent to reap it)."""
    out = set()
    for pid in pids:
        if procs and (pid not in procs or procs[pid][2]):
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except OSError:
            pass
        out.add(pid)
    return out


def kill_group(pgid):
    """KILL a process group with everything its processes started (their parent may be gone already); the list of
    what was killed."""
    roots = [pid for pid, (_, g, _) in snapshot().items() if g == pgid]
    if not roots:
        return set()
    try:
        os.killpg(pgid, signal.SIGSTOP)           # frozen, the group starts nothing more while it is taken
    except OSError:
        pass
    known = members(roots, snapshot())
    kill_tree(roots, known=known)
    return known


def kill_tree(root, grace=0.0, keep=(), known=None):
    """Stop root with everything it started. grace > 0: TERM to all of them at once first (a test cleans up after
    itself), the rest — KILL after grace seconds. The last step: the processes left are frozen (SIGSTOP — a frozen one
    starts nothing), the tree is taken again until nothing new turns up, then all of them — KILL."""
    roots = list(root) if isinstance(root, (list, tuple, set)) else [root]
    procs = snapshot()
    known = set(known or ()) | members(roots, procs, keep) | set(roots)
    if grace > 0:
        _send(known, signal.SIGTERM)
        end = time.time() + grace
        while time.time() < end:
            procs = snapshot()
            left = alive(known, procs)
            if not left:
                return
            known |= members(left, procs, keep)
            time.sleep(0.1)
    known = alive(known)
    for _ in range(20):
        _send(known, signal.SIGSTOP)
        more = members(known, snapshot(), keep) - known
        if not more:
            break
        known |= more
    _send(known, signal.SIGKILL)


def said_stopped(signum):
    """K3_STOP_FILE (k3_test.sh sets it): «INT 130» — the run was stopped by a signal, also one that k3_test.sh got
    ignored on entry and cannot trap (a run started in the background by a script: Ctrl-C of the group stops the steps
    here, and the run says «ОСТАНОВЛЕН», not «FAILURES»). The first stopped step writes it."""
    path = os.environ.get("K3_STOP_FILE")
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return
    with os.fdopen(fd, "w") as f:
        f.write("%s %d\n" % (signal.Signals(signum).name[3:], 128 + int(signum)))


def gone(pid):
    """No such process, or a zombie (killed, its parent has not reaped it yet). ps not there — not gone."""
    try:
        r = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    out = r.stdout.strip()
    return not out or out.startswith("Z")


def watch(pgid, fd=0):
    """--watch: the sentinel of a command started in a session of its own (its group is pgid). Waits for «done» on fd;
    end of file without it — k3_proc is gone (KILL): the processes of the group and everything they started — TERM,
    the stubborn after 3 s — KILL. The group, not a pid: a pid is not given again while a group of it is there."""
    data = b""
    while True:
        try:
            chunk = os.read(fd, 64)
        except InterruptedError:
            continue
        except OSError:
            chunk = b""
        if not chunk:
            break
        data += chunk
        if b"done" in data:
            return 0
    roots = [pid for pid, (_, g, _) in snapshot().items() if g == pgid]
    if roots:
        kill_tree(roots, grace=3.0)
    return 0


def _child_signals():
    """In the command HUP is by default even when it came ignored here (nohup): test_aptlock checks the SSH drop."""
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    if argv[0] == "--watch" and len(argv) == 2 and argv[1].isdigit():
        return watch(int(argv[1]))
    got = []
    tstp = []

    def note(signum, frame):
        got.append(signum)

    def note_tstp(signum, frame):
        tstp.append(signum)

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        # HUP ignored on entry — «nohup»: the run is to outlive the terminal (REV-speed-5 З8). INT and QUIT ignored —
        # a job started with «&» by a script: Ctrl-C of the group stops it all the same (REV-speed-3 С6)
        if s == signal.SIGHUP and signal.getsignal(s) == signal.SIG_IGN:
            continue
        signal.signal(s, note)
    if signal.getsignal(signal.SIGTSTP) != signal.SIG_IGN:
        signal.signal(signal.SIGTSTP, note_tstp)
    parent = os.getppid()
    try:
        owner = int(os.environ.get("K3_TEST_PID") or 0)
    except ValueError:
        owner = 0
    try:
        p = subprocess.Popen(argv, start_new_session=True, preexec_fn=_child_signals)
    except OSError as e:
        print("k3_proc: не запустить %s: %s" % (argv[0], e), file=sys.stderr)
        return 127
    w = None
    try:
        r, w = os.pipe()                          # not inheritable: the command never has it, the sentinel — as stdin
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch", str(p.pid)], stdin=r,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
        os.close(r)
    except OSError:
        w = None
    try:
        return _wait(p, got, tstp, parent, owner)
    finally:
        if w is not None:
            try:
                os.write(w, b"done")
                os.close(w)
            except OSError:
                pass


def _wait(p, got, tstp, parent, owner):
    lost, check = False, 0.0
    while True:
        try:
            rc = p.wait(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            pass
        if tstp:
            # Ctrl-Z: the command is in a session of its own — frozen here with its tree, then this process stops
            # as by default; CONT (fg, bg) — on to them
            del tstp[:]
            frozen = members([p.pid], snapshot())
            _send(frozen, signal.SIGSTOP)
            os.kill(os.getpid(), signal.SIGSTOP)
            _send(frozen, signal.SIGCONT)
        if owner and owner != parent and time.time() >= check:
            check = time.time() + 1.0                 # k3_test.sh killed while its subshell started this one
            lost = gone(owner)
        if got or lost or os.getppid() != parent:
            # TERM: k3_test.sh traps it itself, and stops a step over its limit by it — that is a failure, not a stop
            if got and got[0] != signal.SIGTERM:
                said_stopped(got[0])
            kill_tree(p.pid, grace=3.0)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            return 128 + int(got[0] if got else signal.SIGHUP)
    return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
