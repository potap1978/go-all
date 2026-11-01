#!/usr/bin/env python3
"""k3_unit_par.py — the unit tests of Kaskad 3, each module in its own process, N at a time.

Usage:
    python3 tools/k3_unit_par.py [-j N] [-v] [--list] [module ...]
      no modules — every tests/**/test*.py the way ``unittest discover -s tests -t .`` finds them
      module     — a dotted name (tests.site.test_names) or a path (tests/site/test_names.py)
      -j N       — N processes (default 4); -j 1 — one module after another, still one process each
      --list     — print the modules (with the serial group, if any) and exit
      --no-map   — do not record the map of what the tests run
      --timeout S — a module (or a test of a split one) longer than S seconds is stopped with every process it started
                   (its own process group: node, Chrome, xray) and counted as failed; default 1800, K3_UNIT_TIMEOUT

What it prints: one line per failed module as soon as it ends, then the summary — tests ran, skipped,
failures, errors (the same numbers ``unittest`` prints), the failed modules with their FAIL/ERROR lines and the
full output of each, the slowest modules, the folder with the log of every module. Exit code: 0 — all green.

Split modules. A module whose tests are long and independent (the Chrome scans of tests/test_qs333_layout.py)
says ``# k3-par: split=test`` — then every test of it runs in its own process (the ids from unittest's loader).

The map of what the tests run. With Python 3.12+ every module runs under sys.monitoring: the files of lib/ and
tests/ (the helpers) whose functions it called (import-time code is not counted) go to ~/.cache/kaskad-tests/test-maps.json — the map
tools/test_select.py picks tests by. The cost is one event per function (DISABLE after the first call). The map is
kept per repository (the git common dir: its worktrees share it, a clone has its own) and only grows — a run adds the
calls it saw to the ones already there (under an flock), so a run of another branch can only make the selection wider.

Serial groups. A module that must not run at the same time as another one (a shared Chrome port, a shared file
outside its own temporary folder) says so in its source with a line

    # k3-par: serial=<group> — <why>

Modules of one group never run together — in this runner (the scheduler skips them) nor across runners and
other tools (an fcntl lock file ``<group>.lock`` in $K3_PAR_LOCKS or the system temp folder).

Order: longest first by the times of the previous runs (~/.cache/kaskad-tests/unit-times.json, this machine
only); a module never timed goes first.
"""

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k3_proc  # noqa: E402 — the tree of a module, detached Chrome included (REV-speed-4 С8)
SERIAL_RE = re.compile(r"^#\s*k3-par:\s*serial=([A-Za-z0-9_.-]+)", re.M)
SPLIT_RE = re.compile(r"^#\s*k3-par:\s*split=test\b", re.M)
# the child: unittest as «python -m unittest -q <id>» does it, plus the map of the lib functions it called
CHILD = r"""
import json, os, signal, sys, unittest
# Ctrl-C as in a terminal: started in the background by a script (k3_test.sh), the module gets SIGINT ignored, and the
# tests of the Ctrl-C protection (test_gw_f2, migrate/test_interrupt) would pass on broken code (REV-speed-3 С6)
signal.signal(signal.SIGINT, signal.default_int_handler)
# HUP and QUIT too: a runner started by «nohup» gave them ignored, and a test of the SSH drop (test_aptlock) failed
for _s in (signal.SIGHUP, signal.SIGQUIT):
    signal.signal(_s, signal.SIG_DFL)
mod, out, root = sys.argv[1], sys.argv[2], sys.argv[3]
watch = tuple(os.path.join(root, d) + os.sep for d in ("lib", "tests"))
seen = set()
mon = getattr(sys, "monitoring", None)
if mon is not None and out:
    tool = mon.COVERAGE_ID
    try:
        mon.use_tool_id(tool, "k3map")
    except ValueError:
        mon = None
if mon is not None and out:
    def start(code, offset):
        f = code.co_filename
        if code.co_flags & 0x02 and f.startswith(watch):   # CO_NEWLOCALS: a function, not a module or class body
            seen.add(f)
        return mon.DISABLE
    mon.register_callback(tool, mon.events.PY_START, start)
    mon.set_events(tool, mon.events.PY_START)
sys.argv = ["python -m unittest"]
prog = unittest.main(module=None, argv=["python -m unittest", "-q", mod], exit=False)
if mon is not None and out:
    mon.set_events(tool, 0)
    with open(out, "w") as f:
        json.dump(sorted(os.path.relpath(x, root) for x in seen), f)
sys.exit(0 if prog.result.wasSuccessful() else 1)
"""
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
TAIL_RE = re.compile(r"^(OK|FAILED|NO TESTS RAN)(?: \(([^)]*)\))?\s*$", re.M)
CASE_RE = re.compile(r"^(FAIL|ERROR|UNEXPECTED SUCCESS): (.+)$", re.M)


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kaskad-tests")


def cache_file():
    return os.path.join(cache_dir(), "unit-times.json")


def map_file():
    return os.path.join(cache_dir(), "test-maps.json")   # v2; the old test-map.json is not read


def repo_key(root=ROOT):
    """The map is kept per repository (its git common dir: every worktree of it shares the key; another clone has its
    own) — the runs of one repository only ever add to it."""
    r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    d = r.stdout.strip() if r.returncode == 0 else ""
    return os.path.realpath(os.path.join(root, d)) if d else os.path.realpath(root)


def load_map_doc(path=None):
    try:
        with open(path or map_file(), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def lock_dir():
    d = os.environ.get("K3_PAR_LOCKS") or os.path.join(tempfile.gettempdir(), "k3-par-locks-%d" % os.getuid())
    os.makedirs(d, exist_ok=True)
    return d


def kill_group(p):
    """Kill a module with everything it started (node, Chrome, xray): its group and the processes under it by ppid —
    the list taken before anything is killed, so headless Chrome started «detached» (a group of its own) goes too
    (REV-speed-4 С8) — and reap the module."""
    k3_proc.kill_tree(p.pid)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def discover(top="tests"):
    """The modules ``unittest discover -s tests -t .`` loads: packages (with __init__.py) only, test*.py."""
    out = []

    def walk(rel):
        full = os.path.join(ROOT, rel)
        for name in sorted(os.listdir(full)):
            path = os.path.join(full, name)
            if os.path.isdir(path):
                if os.path.isfile(os.path.join(path, "__init__.py")):
                    walk(os.path.join(rel, name))
            elif name.startswith("test") and name.endswith(".py") and name[:-3].isidentifier():
                out.append((rel + "/" + name[:-3]).replace("/", "."))

    walk(top)
    return out


def to_module(arg):
    a = arg.strip()
    if a.endswith(".py") or "/" in a:
        a = os.path.relpath(os.path.abspath(a), ROOT)
        if a.endswith(".py"):
            a = a[:-3]
        a = a.replace(os.sep, ".")
    return a


def _src(mod):
    try:
        with open(os.path.join(ROOT, mod.replace(".", os.sep) + ".py"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def serial_group(mod):
    m = SERIAL_RE.search(_src(mod))
    return m.group(1) if m else None


def split_ids(mod, env):
    """The test ids of a ``split=test`` module (None — run it whole)."""
    if not SPLIT_RE.search(_src(mod)):
        return None
    code = ("import sys, unittest\n"
            "def walk(s):\n"
            "    for t in s:\n"
            "        yield from walk(t) if isinstance(t, unittest.TestSuite) else [t]\n"
            "for t in walk(unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])):\n"
            "    print(t.id())\n")
    r = subprocess.run([sys.executable, "-c", code, mod], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    ids = [x for x in r.stdout.split() if x.startswith(mod + ".")]
    # a module that does not load or holds a test of another loader: whole (its error is reported as usual)
    return ids if r.returncode == 0 and ids and "unittest.loader" not in r.stdout else None


def parse(text):
    """(ran, counts, ok) from unittest's output — its last summary (a nested unittest printing into the same output
    does not count twice). «NO TESTS RAN» (3.12+: a module without tests) is green, as «unittest discover» of -j 1."""
    rans = RAN_RE.findall(text)
    ran = int(rans[-1]) if rans else 0
    counts = {"skipped": 0, "failures": 0, "errors": 0, "expected failures": 0, "unexpected successes": 0}
    tails = TAIL_RE.findall(text)
    ok = bool(tails) and tails[-1][0] in ("OK", "NO TESTS RAN")
    if tails:
        for part in (tails[-1][1] or "").split(","):
            k, _, v = part.strip().partition("=")
            if k in counts and v.isdigit():
                counts[k] = int(v)
    return ran, counts, ok


class Runner:
    def __init__(self, mods, jobs, verbose, timeout, use_map=True):
        self.jobs, self.verbose, self.timeout, self.use_map = max(1, jobs), verbose, timeout, use_map
        self.times = {}
        try:
            with open(cache_file(), encoding="utf-8") as f:
                self.times = json.load(f)
        except (OSError, ValueError):
            pass
        self.env = dict(os.environ)
        self.env.pop("K3_LAYOUT", None)          # the layout step runs on its own, not inside the unit tests
        self.env["PYTHONPATH"] = os.pathsep.join([os.path.join(ROOT, "lib"), ROOT])
        self.mods = list(mods)
        self.of = {}                             # job (a module or a test id) -> its module
        for m in self.mods:
            for j in (split_ids(m, self.env) or [m]):
                self.of[j] = m
        # longest first; a job never timed first of all (it may be long)
        self.queue = sorted(self.of, key=lambda j: -self.times.get(j, 1e9))
        self.groups = {m: serial_group(m) for m in self.mods}
        self.busy = set()
        self.cv = threading.Condition()
        self.results = {}
        self.running = {}                        # job -> Popen (killed with its group if the runner is stopped)
        self.stopping = False                    # set under self.cv: no module starts after it
        self.logdir = tempfile.mkdtemp(prefix="k3-unit-")
        self.parent = os.getppid()

    def take(self):
        with self.cv:
            while True:
                if not self.queue:
                    return None
                for i, j in enumerate(self.queue):
                    g = self.groups.get(self.of[j])
                    if g is None or g not in self.busy:
                        if g:
                            self.busy.add(g)
                        return self.queue.pop(i)
                self.cv.wait()

    def release(self, job):
        with self.cv:
            self.busy.discard(self.groups.get(self.of[job]))
            self.cv.notify_all()

    def run_one(self, job):
        g = self.groups.get(self.of[job])
        lockf = None
        if g:
            lockf = open(os.path.join(lock_dir(), g + ".lock"), "w")
            fcntl.flock(lockf, fcntl.LOCK_EX)
        t0 = time.time()
        log = os.path.join(self.logdir, job + ".log")
        mapf = os.path.join(self.logdir, job + ".map.json") if self.use_map else ""
        try:
            with open(log, "w", encoding="utf-8") as out:
                # its own process group: on a timeout node/Chrome/xray of the module go with it. Started and recorded
                # under the lock the stop takes: a module is either never started or killed by the stop (no orphan)
                with self.cv:
                    if self.stopping:
                        return
                    p = subprocess.Popen([sys.executable, "-c", CHILD, job, mapf, ROOT], cwd=ROOT, env=self.env,
                                         stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         start_new_session=True)
                    self.running[job] = p
                try:
                    rc = p.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    kill_group(p)
                    rc = "timeout"
                    out.write("\nk3_unit_par: модуль не уложился в %d с (--timeout, K3_UNIT_TIMEOUT) — остановлен "
                              "вместе со своими процессами\n" % self.timeout)
                finally:
                    with self.cv:
                        self.running.pop(job, None)
        finally:
            if lockf:
                fcntl.flock(lockf, fcntl.LOCK_UN)
                lockf.close()
        secs = time.time() - t0
        if isinstance(rc, int) and rc < 0:
            # killed by a signal: the stop of the run reaches the runner a moment after its modules — «снят», not a
            # failure of the module (REV-speed-4)
            for _ in range(20):
                with self.cv:
                    if self.stopping:
                        print("  снят %s (%.0f с): прогон остановлен" % (job, secs), flush=True)
                        return
                time.sleep(0.1)
        with open(log, encoding="utf-8", errors="replace") as f:
            text = f.read()
        ran, counts, ok = parse(text)
        good = rc == 0 and ok
        cases = [k + ": " + v for k, v in CASE_RE.findall(text)]
        if not good and not cases:
            cases = ["ERROR: модуль не отработал (код %s%s)" % (rc, ", нет итога unittest" if not ok else "")]
        files = None
        if mapf:
            try:
                with open(mapf, encoding="utf-8") as f:
                    files = json.load(f)
            except (OSError, ValueError):
                files = None
        res = {"rc": rc, "secs": secs, "ran": ran, "counts": counts, "good": good, "cases": cases, "log": log,
               "files": files}
        with self.cv:
            self.results[job] = res
            done = len(self.results)
        if not good:
            print("  FAIL %s (%.0f с): %s" % (job, secs, "; ".join(cases[:3])), flush=True)
        elif self.verbose:
            print("  ok   %6.1f с %4d тестов  %s  [%d/%d]" % (secs, ran, job, done, self.total), flush=True)

    def worker(self):
        while True:
            job = self.take()
            if job is None:
                return
            try:
                self.run_one(job)
            except Exception as e:   # the runner itself must not hide a module
                with self.cv:
                    self.results[job] = {"rc": "runner", "secs": 0, "ran": 0, "counts": {}, "good": False,
                                         "cases": ["ERROR: раннер: %r" % (e,)], "log": "", "files": None}
            finally:
                self.release(job)

    def run(self):
        self.total = len(self.queue)
        t0 = time.time()
        threads = [threading.Thread(target=self.worker, daemon=True) for _ in range(min(self.jobs, self.total) or 1)]
        for t in threads:
            t.start()
        try:
            for t in threads:
                while t.is_alive():
                    t.join(0.5)
                    # the parent gone (KILL to k3_proc and the group of k3_test.sh): an orphan would run the whole
                    # queue and hold the locks of the serial groups (REV-speed-5 З9) — stopped as by Ctrl-C
                    if self.parent and os.getppid() != self.parent:
                        raise ParentGone()
        except BaseException:
            # Ctrl-C or SIGTERM: the modules run in their own groups — stop them, or they stay behind
            with self.cv:
                self.stopping = True
                self.queue[:] = []
                procs = list(self.running.values())
            for p in procs:
                kill_group(p)
            raise
        self.wall = time.time() - t0
        self.save_times()
        self.save_map()
        return self.report()

    def save_json(self, path, doc):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=0, sort_keys=True)
            os.replace(tmp, path)
        except OSError:
            pass

    def save_times(self):
        """The times of the modules (the order of the next run): read again and updated under an flock, so parallel
        runners keep each other's."""
        mine = dict((j, round(r["secs"], 2)) for j, r in self.results.items() if r["rc"] != "timeout")
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            lockf = open(cache_file() + ".lock", "w")
        except OSError:
            return
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                with open(cache_file(), encoding="utf-8") as f:
                    times = json.load(f)
                if not isinstance(times, dict):
                    times = {}
            except (OSError, ValueError):
                times = {}
            times.update(mine)
            self.save_json(cache_file(), times)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()

    def save_map(self):
        """Add what these modules called to the map of this repository: a union, never a replacement — a call seen once
        stays (the selection only gets wider); a module counts when it ran whole or all of its tests did. Written under
        an flock: parallel runners of other worktrees do not lose each other's updates."""
        per = {}
        for j, r in self.results.items():
            m = self.of[j]
            if r["files"] is None:
                per[m] = None
            elif per.get(m, ()) is not None:
                per.setdefault(m, set()).update(r["files"])
        per = {m: v for m, v in per.items() if v is not None}
        if not per:
            return
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
        key = repo_key()
        try:
            os.makedirs(cache_dir(), exist_ok=True)
            lockf = open(map_file() + ".lock", "w")
        except OSError:
            return
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            doc = load_map_doc()
            if doc.get("v") != 2:
                doc = {"v": 2, "repos": {}}           # v1 (one map for every branch, replaced): not trusted
            repo = doc.setdefault("repos", {}).setdefault(key, {})
            mods = repo.setdefault("modules", {})
            for m, v in per.items():
                mods[m] = sorted(set(mods.get(m) or ()) | v)
            repo.update({"root": ROOT, "commit": head, "updated": time.strftime("%Y-%m-%d %H:%M")})
            self.save_json(map_file(), doc)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()

    def report(self):
        tot = {"skipped": 0, "failures": 0, "errors": 0, "expected failures": 0, "unexpected successes": 0}
        ran = 0
        bad = []
        bare = 0                                  # failed without a unittest summary of failures (timeout, os._exit …)
        for j in sorted(self.results):
            r = self.results[j]
            ran += r["ran"]
            for k in tot:
                tot[k] += r["counts"].get(k, 0)
            if not r["good"]:
                bad.append(j)
                if not any(r["counts"].get(k) for k in ("failures", "errors", "unexpected successes")):
                    bare += 1
        tot["errors"] += bare
        cpu = sum(r["secs"] for r in self.results.values())
        print("-" * 70)
        for j in bad:
            r = self.results[j]
            print("== %s — код %s, лог %s" % (j, r["rc"], r["log"]))
            try:
                with open(r["log"], encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                text = ""
            # the failure blocks only (ResourceWarnings and the like stay in the log)
            i = text.find("=" * 70)
            sys.stdout.write((text[i:] if i >= 0 else text[-4000:]).rstrip() + "\n")
        slow = sorted(self.results.items(), key=lambda kv: -kv[1]["secs"])[:8]
        print("самые долгие: " + ", ".join("%s %.0f с" % (j.replace("tests.", ""), r["secs"]) for j, r in slow))
        extra = ", ".join("%s=%d" % (k, v) for k, v in tot.items() if v and k != "skipped")
        print("Ran %d tests in %d modules (%d processes), %.0f s (сумма по процессам %.0f s, -j %d)" % (
            ran, len(self.mods), len(self.results), self.wall, cpu, self.jobs))
        if not bad:
            shutil.rmtree(self.logdir, ignore_errors=True)       # all green: nothing to read there
        print("UNIT %s (skipped=%d%s)%s%s" % ("OK" if not bad else "FAILED", tot["skipped"],
                                             (", " + extra) if extra else "",
                                             " · из них %d модулей без итога unittest (errors)" % bare if bare else "",
                                             "" if not bad else " · логи модулей: " + self.logdir))
        if bad:
            print("упали (%d): %s" % (len(bad), " ".join(bad)))
            return 1
        return 0


class ParentGone(KeyboardInterrupt):
    """The process that started the runner is gone."""


def _stop(signum, frame):
    raise KeyboardInterrupt()


def seconds(text, what):
    """A limit in seconds (an integer ≥ 1); else a clear error, not a traceback."""
    try:
        v = int(str(text).strip())
    except ValueError:
        v = 0
    if v < 1:
        print("k3_unit_par: %s ждёт число секунд ≥ 1, а не %r" % (what, text), file=sys.stderr)
        raise SystemExit(2)
    return v


def main(argv):
    timeout = seconds(os.environ.get("K3_UNIT_TIMEOUT") or "1800", "K3_UNIT_TIMEOUT")
    jobs, verbose, listing, use_map, mods = 4, False, False, True, []
    it = iter(argv)
    for a in it:
        if a in ("-j", "--jobs"):
            jobs = int(next(it))
        elif a.startswith("-j") and a[2:].isdigit():
            jobs = int(a[2:])
        elif a in ("-v", "--verbose"):
            verbose = True
        elif a == "--list":
            listing = True
        elif a == "--no-map":
            use_map = False
        elif a == "--timeout":
            timeout = seconds(next(it, ""), "--timeout")
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            mods.append(to_module(a))
    mods = list(dict.fromkeys(mods)) or discover()
    if listing:
        for m in mods:
            g = serial_group(m)
            print(m + ("  [serial=%s]" % g if g else "") + ("  [split=test]" if SPLIT_RE.search(_src(m)) else ""))
        return 0
    # SIGTERM (a killed k3_test.sh, quiet.sh) as Ctrl-C: the modules running are stopped with their groups;
    # SIGINT — by default even when it came ignored (a background job of a script), so the modules inherit it (С6)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        return Runner(mods, jobs, verbose, timeout, use_map).run()
    except ParentGone:
        print("k3_unit_par: остановлен — запустивший процесс пропал; запущенные модули сняты вместе со своими "
              "процессами", file=sys.stderr)
        return 129
    except KeyboardInterrupt:
        print("k3_unit_par: остановлен (Ctrl-C/SIGTERM) — запущенные модули сняты вместе со своими процессами",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
