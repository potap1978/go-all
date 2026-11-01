#!/usr/bin/env python3
"""k3_install_par.py — tools/k3_install_test.sh in N parts beside each other (R-speed-2, 31.10).

    python3 tools/k3_install_par.py [-j N] [--list]
      -j N    — N parts (default 3; tools/k3_test.sh gives K3_INSTALL_JOBS); -j 1 — the script as it is
      --list  — the parts (sections of each, their weight) and exit

The test is one script: a common start (the fakes, the helpers, the temporary folder — before the first section),
then ~75 sections — each begins with a line «echo "── N. …"» at the start of a line and takes a server root of its
own (use_root) — then the summary. A part = the common start + a run of sections in a row + the summary: every
section in exactly one part, in the order of the file; a part has its own temporary folder (the start makes it), its
own fakes and roots. A part begins only where the test allows it — a line «# k3-install-par: cut» above the
section (the sections below need nothing of the ones above but their functions and values) — and before its own
sections replays the earlier ones' function definitions, plain assignments, «use_root <name>» and the blocks marked
«# k3-install-par: replay» … «# k3-install-par: end» (shared fakes), not their checks (see definitions()). Of the
allowed places it takes those where the weights split evenly — the seconds of each section in the previous runs
(~/.cache/kaskad-tests/install-times.json, this machine), a section never timed — by its number of lines.

Printed: the output of every part in order (the start's lines in each), then one summary line — «ВСЁ ЗЕЛЕНО (N)»,
N the checks of all parts, or «ЕСТЬ ПРОБЛЕМЫ». A part that failed or ended without its summary — red. Exit code: 0
green, 1 not. INT/TERM — the parts are stopped too (tools/k3_test.sh stops the tree anyway: tools/k3_proc.py).

The sum of the checks (REV-speed-2b С7): a green run of the script as it is (-j 1, K3_INSTALL_JOBS=1) keeps its N
under a key — the sha256 of k3_install_test.sh, install.sh and K3_OLD_INSTALL (~/.cache/kaskad-tests/install-count.json);
the parts of the same code must sum to that N, otherwise red («сумма частей M ≠ N»: a check of a part did not run — a
condition without «else» on a state a section before the cut made). No such run of this code — a line saying so.
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(ROOT, "tools", "k3_install_test.sh")
HEAD_RE = re.compile(r'^echo "── ')
ROOT_LINE = 'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"'
SUMMARY_RE = re.compile(r'^if \[ "\$FAILED" = "0" \]; then echo "  ВСЁ ЗЕЛЕНО')
GREEN_RE = re.compile(r"^  ВСЁ ЗЕЛЕНО \((\d+)\)$", re.M)


FUNC_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{")
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
USE_ROOT_RE = re.compile(r"^use_root [A-Za-z0-9_.-]+$")
CUT_RE = re.compile(r"^# k3-install-par: cut\b")
REPLAY_RE = re.compile(r"^# k3-install-par: replay\b")
END_RE = re.compile(r"^# k3-install-par: end\b")


def statements(line):
    """The «;»-separated statements a line starts with, up to the first «&&», «||», «|», «&» or a comment (what comes
    after them runs or not by the result of the ones before) — outside quotes; a line with a quote left open — none."""
    s, out, cur, i, n = line.rstrip("\n"), [], "", 0, len(line.rstrip("\n"))
    while i < n:
        c = s[i]
        if c in "'\"":
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if (c == '"' and s[j] == "\\") else 1
            if j >= n:
                return []
            cur += s[i:j + 1]
            i = j + 1
            continue
        if c == "\\":
            cur += s[i:i + 2]
            i += 2
            continue
        if c == ";" and s[i:i + 2] != ";;":
            out.append(cur.strip())
            cur, i = "", i + 1
            continue
        if s[i:i + 2] in ("&&", "||") or (c == "#" and (not cur or cur[-1].isspace())):
            break                                      # the one before «&&» runs always, the rest — not always
        if c in "&|":
            cur = ""                                   # a pipeline or «&»: a subshell, its assignments go with it
            break
        cur += c
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]
# the common start's own names: a section's line that sets one of them is not replayed (a stray match — a line of a
# quoted script — must not move the folder of the test or its counters)
KEEP = {"W", "S", "ROOT_DIR", "PATH", "HOME", "FAILED", "PASSED", "CUR", "NEWER", "SHA", "SHA12", "CALLS", "FAKE_DIR",
        "IPT_DIR"}


def plain_assignment(line):
    """«A=1 B='x y' C="$W/z" D=(a b)» — words of assignments only, nothing run (no $(…), `…`, no command after)."""
    if "$(" in line or "`" in line:
        return False
    s, i, n, names = line.rstrip("\n"), 0, len(line.rstrip("\n")), []
    while i < n:
        m = NAME_RE.match(s, i)
        if not m:
            return False
        names.append(m.group(0).rstrip("+="))
        i = m.end()
        if i < n and s[i] == "(":                      # an array
            j = s.find(")", i)
            if j < 0:
                return False
            i = j + 1
        while i < n and not s[i].isspace():
            c = s[i]
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    return False
                i = j + 1
            elif c == '"':
                j = i + 1
                while j < n and s[j] != '"':
                    j += 2 if s[j] == "\\" else 1
                if j >= n:
                    return False
                i = j + 1
            elif c == "\\":
                i += 2
            elif c in ";&|<>()":
                return False
            else:
                i += 1
        while i < n and s[i] in " \t":
            i += 1
    return bool(names) and not (set(names) & KEEP)


def bash_ok(text):
    return subprocess.run(["bash", "-n"], input=text, universal_newlines=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def definitions(text):
    """What a later section may take of this one: its function definitions and plain assignments at the top level (a
    line at the start of a line, not inside a here-document) and its «use_root <name>» (the root its assignments are
    made of: KASKAD_ROOT, K3_OPT …), in order — run before the sections of a part («replay»), they leave the shell
    as the earlier sections left it in one run, without their checks."""
    lines, out, i = text.splitlines(True), [], 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if REPLAY_RE.match(line):                      # a block the test marked: shared fakes — as it is
            j = next((k for k in range(i + 1, n) if END_RE.match(lines[k])), None)
            if j is None:
                raise ValueError("k3_install_test.sh: «# k3-install-par: replay» без «# k3-install-par: end»")
            out.append("".join(lines[i:j + 1]))
            i = j + 1
            continue
        if FUNC_RE.match(line):
            j = i
            # its end — the first line with «}» after which the text is whole for «bash -n» (a prefix cannot close
            # the braces early: a here-document opened in it runs to the end of the text)
            while j < n and not ("}" in lines[j] and bash_ok("".join(lines[i:j + 1]))):
                j += 1
            if j >= n:
                raise ValueError("k3_install_test.sh: не нашёл конец функции: " + line.strip()[:80])
            out.append("".join(lines[i:j + 1]))
            i = j + 1
            continue
        m = HEREDOC_RE.search(line)
        if m:                                          # a here-document: its lines are text, not commands
            term, i = m.group(3), i + 1
            while i < n and (lines[i].lstrip("\t") if m.group(1) else lines[i]).rstrip("\n") != term:
                i += 1
            i += 1
            continue
        sts = statements(line)
        if not sts and NAME_RE.match(line):            # «A='…» with the quote closed lines below: the whole value
            for j in range(i + 1, min(n, i + 60)):
                chunk = "".join(lines[i:j + 1])
                if bash_ok(chunk):
                    if plain_assignment(chunk.rstrip("\n")):
                        out.append(chunk)
                        i = j
                    break
        for st in sts:
            if (plain_assignment(st) or USE_ROOT_RE.match(st)) and bash_ok(st + "\n"):
                out.append(st + "\n")
        i += 1
    return "".join(out)


def cache_file():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kaskad-tests", "install-times.json")


def split(text):
    """(start, [(title, text) of every section], summary) of the test script; ValueError — not the shape expected."""
    lines = text.splitlines(True)
    heads = [i for i, x in enumerate(lines) if HEAD_RE.match(x)]
    tail = [i for i, x in enumerate(lines) if SUMMARY_RE.match(x)]
    if not heads or len(tail) != 1 or tail[0] < heads[-1]:
        raise ValueError("k3_install_test.sh: нет разделов «echo \"── …\"» или итога «ВСЁ ЗЕЛЕНО» в конце")
    end = tail[0]
    if end > 0 and lines[end - 1].strip() == 'echo ""':
        end -= 1
    start = "".join(lines[:heads[0]])
    if start.count(ROOT_LINE) != 1:
        raise ValueError("k3_install_test.sh: строка ROOT_DIR=… не та, что ждёт k3_install_par.py")
    secs = []
    for a, b in zip(heads, heads[1:] + [end]):
        title = lines[a].strip()[len('echo "'):].rstrip('"').strip("─ ").strip()
        secs.append((title, "".join(lines[a:b])))
    return start, secs, "".join(lines[end:])


def cuts_of(secs):
    """The sections a part may begin with: the first one and those under a line «# k3-install-par: cut» (the test says
    where the sections below need nothing of the ones above but what the replay gives)."""
    return [0] + [k for k in range(1, len(secs)) if CUT_RE.search(secs[k - 1][1].rstrip("\n").rsplit("\n", 1)[-1])]


def weights(secs):
    try:
        with open(cache_file(), encoding="utf-8") as f:
            times = json.load(f)
        if not isinstance(times, dict):
            times = {}
    except (OSError, ValueError):
        times = {}
    # a section without a time — by its lines, at the seconds per line of the timed ones (or 0.05 s)
    timed = [(times[t], s.count("\n")) for t, s in secs if isinstance(times.get(t), (int, float))]
    per = (sum(a for a, _ in timed) / max(1, sum(b for _, b in timed))) if timed else 0.05
    return [float(times[t]) if isinstance(times.get(t), (int, float)) else per * s.count("\n") for t, s in secs]


def cut(w, n):
    """Runs in a row of ``w`` into ``n`` parts with sums as even as the order lets (the longest part as short)."""
    n = max(1, min(n, len(w)))
    lo, hi = max(w), sum(w)

    def parts_for(limit):
        out, cur, acc = [], [], 0.0
        for i, x in enumerate(w):
            if cur and acc + x > limit:
                out.append(cur)
                cur, acc = [], 0.0
            cur.append(i)
            acc += x
        out.append(cur)
        return out

    for _ in range(60):
        mid = (lo + hi) / 2
        if len(parts_for(mid)) <= n:
            hi = mid
        else:
            lo = mid
    got = parts_for(hi)
    while len(got) < n:                                # fewer runs than asked — split the longest further
        k = max(range(len(got)), key=lambda j: (len(got[j]) > 1, sum(w[i] for i in got[j])))
        if len(got[k]) < 2:
            break
        h = len(got[k]) // 2
        got[k:k + 1] = [got[k][:h], got[k][h:]]
    return got


def count_file():
    return os.path.join(os.path.dirname(cache_file()), "install-count.json")


def count_key():
    """The code the number of checks belongs to: the test, install.sh (its payload is lib/), the old install.sh of
    «old code → new» (K3_OLD_INSTALL: a SKIP without it)."""
    import hashlib
    h = hashlib.sha256()
    for path in (TEST, os.path.join(ROOT, "install.sh"), os.environ.get("K3_OLD_INSTALL") or ""):
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"-")
        h.update(b"\0")
    return h.hexdigest()


def load_count(key):
    try:
        with open(count_file(), encoding="utf-8") as f:
            n = json.load(f).get(key)
        return n if isinstance(n, int) else None
    except (OSError, ValueError, AttributeError):
        return None


def save_count(key, n):
    try:
        os.makedirs(os.path.dirname(count_file()), exist_ok=True)
        import fcntl
        with open(count_file() + ".lock", "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                with open(count_file(), encoding="utf-8") as f:
                    doc = json.load(f)
                if not isinstance(doc, dict):
                    doc = {}
            except (OSError, ValueError):
                doc = {}
            doc.pop(key, None)
            doc[key] = n
            while len(doc) > 40:                       # the oldest keys out
                doc.pop(next(iter(doc)))
            tmp = count_file() + ".%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=0)
            os.replace(tmp, count_file())
    except OSError:
        pass


def run_whole():
    """-j 1: the script as it is, its output as it goes; green — its number of checks kept for the parts (С7)."""
    key = count_key()
    p = subprocess.Popen(["bash", TEST], stdout=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
                         errors="replace")

    def stop(signum, frame):
        try:
            p.terminate()
        except OSError:
            pass
        sys.exit(128 + signum)

    for sg in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sg, stop)
    got = []
    for line in p.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        m = GREEN_RE.match(line.rstrip("\n"))
        if m:
            got.append(int(m.group(1)))
    rc = p.wait()
    if rc == 0 and len(got) == 1:
        save_count(key, got[0])
    return rc


def save_times(new):
    try:
        os.makedirs(os.path.dirname(cache_file()), exist_ok=True)
        import fcntl
        with open(cache_file() + ".lock", "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                with open(cache_file(), encoding="utf-8") as f:
                    times = json.load(f)
                if not isinstance(times, dict):
                    times = {}
            except (OSError, ValueError):
                times = {}
            times.update(new)
            tmp = cache_file() + ".%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(times, f, ensure_ascii=False, indent=0, sort_keys=True)
            os.replace(tmp, cache_file())
    except OSError:
        pass


def main(argv):
    jobs, listing = 3, False
    it = iter(argv)
    for a in it:
        if a in ("-j", "--jobs"):
            a = next(it, "")
            if not a.isdigit() or int(a) < 1:
                print("k3_install_par: -j ждёт число ≥ 1", file=sys.stderr)
                return 2
            jobs = int(a)
        elif a.startswith("-j") and a[2:].isdigit() and int(a[2:]) >= 1:
            jobs = int(a[2:])
        elif a == "--list":
            listing = True
        elif a == "--defs":                            # what is replayed before section N (debugging)
            start, secs, summary = split(open(TEST, encoding="utf-8").read())
            k = int(next(it, "0"))
            sys.stdout.write("".join(definitions(secs[i][1]) for i in range(k)))
            return 0
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print("k3_install_par: непонятный аргумент %s" % a, file=sys.stderr)
            return 2
    with open(TEST, encoding="utf-8") as f:
        text = f.read()
    if jobs == 1 and not listing:
        return run_whole()
    start, secs, summary = split(text)
    w = weights(secs)
    marks = cuts_of(secs)
    blocks = [list(range(a, b)) for a, b in zip(marks, marks[1:] + [len(secs)])]
    parts = [[i for b in grp for i in blocks[b]] for grp in cut([sum(w[i] for i in b) for b in blocks], jobs)]
    if listing:
        for k, p in enumerate(parts):
            print("часть %d/%d: %d разделов, ≈%.0f с — %s … %s" % (k + 1, len(parts), len(p), sum(w[i] for i in p),
                                                                 secs[p[0]][0][:40], secs[p[-1]][0][:40]))
        return 0
    start = start.replace(ROOT_LINE, "ROOT_DIR=%s" % shq(ROOT), 1)
    tmp = tempfile.mkdtemp(prefix="k3-inst-par-")
    procs, outs, times, defs = [], [None] * len(parts), {}, {}

    def stop(signum, frame):
        for p in procs:
            try:
                p.terminate()
            except OSError:
                pass
        sys.exit(128 + signum)

    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, stop)

    def read(k, p):
        buf, last, title = [], time.time(), None
        for line in p.stdout:
            now = time.time()
            if line.startswith("── "):                  # a section began: the one before it ended
                if title is not None:
                    times[title] = round(now - last, 2)
                title = line.strip().strip("─ ").strip()
                last = now
            buf.append(line)
        if title is not None:
            times[title] = round(time.time() - last, 2)
        outs[k] = "".join(buf)

    try:
        threads = []
        for k, p in enumerate(parts):
            path = os.path.join(tmp, "part%d.sh" % k)
            with open(path, "w", encoding="utf-8") as f:
                for i in range(p[0]):
                    if i not in defs:
                        defs[i] = definitions(secs[i][1])
                replay = "".join(defs[i] for i in range(p[0]))
                # the replay without «set -u»: a value made of a variable set by a command it does not run — empty, not
                # the end of the part (a later section sets what it uses itself: the PASS lines of the parts were
                # compared with «-j 1» when the cut marks were put — a new mark needs the same comparison)
                f.write(start + ("# ── k3_install_par: определения разделов до этой части ──\nset +u\n" + replay +
                                 "set -u\n" if replay else "") +
                        "".join(secs[i][1] for i in p) + summary)
            pr = subprocess.Popen(["bash", path], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  universal_newlines=True, encoding="utf-8", errors="replace")
            procs.append(pr)
            t = threading.Thread(target=read, args=(k, pr))
            t.start()
            threads.append(t)
        codes = []
        for pr, t in zip(procs, threads):
            codes.append(pr.wait())
            t.join()
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    total, red = 0, False
    for k, (p, out, rc) in enumerate(zip(parts, outs, codes)):
        print("  ── часть %d/%d: %s … %s ──" % (k + 1, len(parts), secs[p[0]][0][:50], secs[p[-1]][0][:50]))
        sys.stdout.write(out or "")
        m = GREEN_RE.findall(out or "")
        if rc == 0 and len(m) == 1:
            total += int(m[0])
        else:
            red = True
            print("  FAIL часть %d/%d: код %s%s" % (k + 1, len(parts), rc, "" if m else ", без итога «ВСЁ ЗЕЛЕНО»"))
    if not red:
        save_times(times)
    seq = None if red else load_count(count_key())
    print("")
    if seq is not None and seq != total:
        red = True
        print("  FAIL сумма частей %d ≠ %d — столько проверок дал прогон того же кода одним скриптом (K3_INSTALL_JOBS=1):"
              " проверка части не выполнилась (условие без «иначе» на состоянии раздела до пометки cut?) — сверьте строки"
              " PASS частями и с -j 1" % (total, seq))
    if red:
        print("  ЕСТЬ ПРОБЛЕМЫ")
        return 1
    print("  ВСЁ ЗЕЛЕНО (%d) — %d частей рядом, %s" % (total, len(parts), "как одним скриптом" if seq is not None else
          "сверки с прогоном одним скриптом нет (K3_INSTALL_JOBS=1 на этом коде ещё не шёл)"))
    return 0


def shq(s):
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
