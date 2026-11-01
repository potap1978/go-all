"""The run of the server check (SPEC-node-score §6.2 B1; SPEC-node-score-schedule-share NS-S8, NS-S9, §2.5, §3.1–3.2;
RESEARCH-node-score §2, §6.1).

``start`` puts the check into a transient unit and returns at once; the unit does the work (``worker``)::

    systemd-run --unit=kaskad-bench-<id> --collect --no-block
        -p CPUWeight=1 -p IOWeight=1 -p CPUSchedulingPolicy=idle -p Nice=19 -p IOSchedulingClass=idle
        -p MemoryMax=256M -p RuntimeMaxSec=600 -p IPAccounting=yes -p CPUAccounting=yes -p TimeoutStopSec=30
        /usr/bin/python3 -I -c "<runpy of kaskad.bench>" worker --id <id> --mode quick|full --by <who> …

Rules:

* root only; every argument of the command is ours (the id — 10 hex of ``secrets``, the mode, who, the id of a
  schedule entry or of a check by ``fullmatch``) — nothing a person typed goes into a command.
* One check at a time: a unit ``kaskad-bench-<10 hex>`` already active — ``start`` refuses (``running``). The unit
  holds ``/run/kaskad/speedtest.lock`` (the one lock with the speed test of the exits) for the WHOLE check; it is
  taken without waiting — held by someone — the check is ``skipped``/``locked``, no test. Its mtime = the end of the
  check (the light probes skip the seconds around it, gw/hostping.py).
* «Свободен» (NS-S8, ``bench.quiet``): a manual check first looks for ``PRECHECK_S`` (10 s; the schedule — B11 —
  waits its own 5 min before the start and passes 0); busy (people ≥ 2 Mbit/s or the CPU of others ≥ 20 %) —
  ``skipped``/``busy`` with the numbers, unless ``force`` (a person chose «run anyway»); the disk waits (iowait of
  the same 10 s ≥ 20 %, RS §2) — ``skipped``/``busy_disk``. During the check a sample every 10 s, the moving average
  of 5 min (a check shorter than that — over all of it): the threshold crossed — ``under_load: 1``; no sample with
  the accounting of the unit — ``under_load: null`` and ``why_args.load = "unknown"`` (``load_unknown``).
* The lock held by another check of the server (two starts at once) — ``skipped``/``running``; by the speed test of
  the exits — ``skipped``/``locked``.
* Limits: ``MemoryMax=256M``, ``RuntimeMaxSec=600`` of the unit; the worker itself ends by ``DEADLINE_S`` (6 min:
  a step that does not fit — «не измерено: время вышло»), every command has its own timeout.
* Stop: ``cancel(id)`` = ``systemctl stop`` of the unit; SIGTERM reaches the worker and the tools (KillMode of the
  cgroup); the worker writes what it measured with ``status: cancelled`` (``why``: ``stopped``, or ``timeout`` at
  the end of ``RuntimeMaxSec``), removes its files and lets the lock go.
* Files: the work directory ``/var/lib/kaskad/bench/work-<id>`` (0700; the fio file is only there) is removed at the
  end; the fio file of a check killed by SIGKILL or OOM goes at once (``ExecStopPost`` of the unit), of one cut by a
  reboot — at the boot (``/etc/tmpfiles.d/kaskad-bench.conf``, written by ``start``); the rest of the directory of
  such a check is removed by the next check UNDER the lock (no other check can run then; and only when ``systemctl
  list-units`` shows our own unit — an empty or failed answer is not «no unit»), and its check gets a record
  ``failed``/``killed`` with its ``sched_id``/``retry_of`` and the time of its last mark. The result — ``/var/lib/kaskad/bench/spool/<id>.json`` (0600, atomically,
  ``{"v": 1, "run": {bench_run}, "tests": [{bench}], "series": …}``) — the ONE way into the history (the writer of
  the base is the collector, B12); ``results.json`` — the last measured one for a quick read. Without the gateway the queue is
  the history: the start keeps it to 400 days and 3000 files.
* Marks (§2.1): a manual check that measured something besides the inventory sets ``first_manual_at``; the answer
  about the packages — ``pkgs``, and a manual start with sysbench and fio already there — ``pkgs: yes``
  (``bench.marks`` over ``bench.machine``: written only when it changes; a failed write — a line of the journal, the
  check starts and its result is kept). The schedule (``by = schedule``, B11) never installs packages.

    start(mode, by, sched_id=None, retry_of=None, force=False, install=None, runner=None, iperf=False, skip=(),
          only=())
        -> {"id", "unit", "missing"}; ``iperf`` — test 8b (a manual check only), ``skip`` ⊆ ``bench.channel.SKIP``
        (8b, 9, up, down) — the extension point of the schedule and the repeat (B11); a check not by a person
        (the schedule, the repeat) always skips 8b and 9 (NS-S5, SS §2.8 п. 2); ``only`` ⊆ ``bench.suite.LINKS`` —
        the links a repeat measures again (its steps alone, ``suite.only_plan``; the repeat only)
    cancel(rid, runner=None) -> bool
    status(rid, runner=None) -> {"state": "running" | "done" | "unknown", "progress" | "result"}
    worker(rid, mode, by, …) -> the document written to the queue
"""

import json
import os
import re
import secrets
import shutil
import signal
import string
import time

from .. import i18n, paths, state, sysrun, util
from ..errors import KaskadError
from ..i18n import tr
from . import marks, pkgs, quiet, spool, suite


MODES = ("quick", "full")
BY = ("schedule", "panel", "bot", "cli", "mcp", "ssh", "auto")
MANUAL = ("panel", "bot", "cli", "mcp", "ssh")
# 8b: a check by a person (D-144, владелец 29.10: a full one runs it by default, the person may turn it off — the line
# «the point sees the address of the server» is beside that choice in the panel, the SSH menu, the CLI and the
# description of MCP); never the schedule or the repeat (NS-S5)
IPERF_BY = ("panel", "bot", "cli", "ssh", "mcp")
AUTO_SKIP = ("8b", "9")             # never by the schedule or the repeat (NS-S5, SS §2.8 п. 2): in their argv always
INV_TESTS = ("mem_gb",)             # the rows of the inventory: not «measured» for the marks and results.json
PROPS = ("CPUWeight=1", "IOWeight=1", "CPUSchedulingPolicy=idle", "Nice=19", "IOSchedulingClass=idle",
         "MemoryMax=256M", "RuntimeMaxSec=600", "IPAccounting=yes", "CPUAccounting=yes", "TimeoutStopSec=30")
RUNTIME_MAX_S = 600
DEADLINE_S = 360                    # the check ends itself well before RuntimeMaxSec
PRECHECK_S = 10                     # «свободен» before a manual check
V = 1
PYTHON = "/usr/bin/python3"
ID = re.compile(r"[0-9a-f]{10}")
TMPFILES = "/etc/tmpfiles.d/kaskad-bench.conf"
TMPFILES_TEXT = ("# Kaskad 3: the file of the disk test of the server check, left by a check cut by a reboot\n"
                 "r! /var/lib/kaskad/bench/work-*/%s\n")       # «!»: only at the boot, never a «--remove» of a live check
_SAFE_PATH = re.compile(r"/[A-Za-z0-9/._-]+")
SCHED_ID = re.compile(r"[0-9a-z]{1,32}")
UNIT = re.compile(r"kaskad-bench-([0-9a-f]{10})\.service")

_euid = os.geteuid                  # tests replace these
_now = time.time
_mono = time.monotonic
_sleep = time.sleep


# ── paths ───────────────────────────────────────────────────────────────────

def bench_dir():
    return paths.P("/var/lib/kaskad/bench")


def spool_dir():
    return os.path.join(bench_dir(), "spool")


def results_path():
    return os.path.join(bench_dir(), "results.json")


def work_dir(rid):
    return os.path.join(bench_dir(), "work-%s" % rid)


def lock_path():
    return paths.P("/run/kaskad/speedtest.lock")


def tmpfiles_path():
    return paths.P(TMPFILES)


def unit_name(rid):
    return "kaskad-bench-%s.service" % rid


def new_id():
    return secrets.token_hex(5)


def _root():
    if _euid() != 0:
        raise KaskadError(tr("bench_run.err.root"))


def _check_id(rid, what="id"):
    if not (isinstance(rid, str) and ID.fullmatch(rid)):
        raise KaskadError(tr("bench_run.err.bad_arg", what=what))
    return rid


def _skip(skip, by=None):
    """The tests of the channel a check leaves out (``bench.channel.SKIP``: 8b, 9, up, down) — sorted, checked; a
    check not by a person (``by`` the schedule or the repeat) — 8b and 9 always (``AUTO_SKIP``)."""
    from . import channel
    skip = set(skip or ())
    if any(x not in channel.SKIP for x in skip):
        raise KaskadError(tr("bench_run.err.bad_arg", what="skip"))
    if by is not None and by not in MANUAL:
        skip |= set(AUTO_SKIP)
    return sorted(skip)


def _only(only):
    """The links of a repeat (``bench.suite.LINKS``) — sorted, checked."""
    from . import suite
    only = sorted(set(only or ()))
    if any(x not in suite.LINKS for x in only):
        raise KaskadError(tr("bench_run.err.bad_arg", what="only"))
    return only


def _check(mode, by, sched_id, retry_of, iperf=False, skip=(), only=()):
    _skip(skip)
    if _only(only) and by != "auto":        # «only these» — the repeat after a sharp drop alone (SS §2.8 п. 2)
        raise KaskadError(tr("bench_run.err.bad_arg", what="only"))
    if iperf and by not in IPERF_BY:        # 8b: by the button and with its line of consent only (NS-S5)
        raise KaskadError(tr("bench_run.err.bad_arg", what="iperf"))
    if mode not in MODES:
        raise KaskadError(tr("bench_run.err.bad_arg", what="mode"))
    if by not in BY:
        raise KaskadError(tr("bench_run.err.bad_arg", what="by"))
    if by == "schedule":
        if not (isinstance(sched_id, str) and SCHED_ID.fullmatch(sched_id)):
            raise KaskadError(tr("bench_run.err.bad_arg", what="sched_id"))
    elif sched_id is not None:
        raise KaskadError(tr("bench_run.err.bad_arg", what="sched_id"))
    if by == "auto":
        _check_id(retry_of, "retry_of")
    elif retry_of is not None:
        raise KaskadError(tr("bench_run.err.bad_arg", what="retry_of"))


# the shorter texts of a reason, in order (SS §2.5): a text whose numbers are not all known gives way to the next
# one; the last one of every chain has no numbers — a raw «{cpu}» never reaches a person
_SHORTER = {"busy": ("busy_net", "busy_plain"), "busy_net": ("busy_plain",), "busy_disk": ("busy_disk_plain",),
            "missed": ("missed_one",)}
_FIELD = string.Formatter()


def _fields(text):
    try:
        return set(f for _l, f, _s, _c in _FIELD.parse(text) if f is not None)
    except ValueError:
        return {"?"}


def why_text(code, args=None, lang=None):
    """The text of a reason (``why`` of a check or of a test) for a person, RU or EN; numbers as the language writes
    them; an unknown code — the code itself. A number that is not known (None, missing, not a number) picks the
    shorter text without it (``_SHORTER``); no text of the chain fits — the words before the numbers."""
    kw = {}
    for k, v in (args or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf"):
            kw[k] = i18n.fmt_num(v, lang, digits=0 if float(v).is_integer() else 1)
    last = None
    for c in (code,) + _SHORTER.get(code, ()):
        key = "bench_run.why.%s" % c
        text = i18n.lookup(key, lang)
        if text is None:
            continue
        last = text
        need = _fields(text)
        if need <= set(kw):
            return tr(key, lang, **dict((k, kw[k]) for k in need)) if need else tr(key, lang)
    if last is None:
        return code
    cut = last.split("{", 1)[0].rstrip(" :—-–,(")
    return cut or code


def geo_text(geo, lang=None):
    """The note of test 8 for a person (REV-ns-b3-2 З-1): ``bench.extra["geo"]`` -> its text, or None."""
    from . import spool
    key = spool.GEO_TEXT.get(geo) if isinstance(geo, str) else None
    return tr(key, lang) if key else None


def shown(doc, lang=None):
    """The file of a check as a person reads it (CLI ``status``): a row with ``extra.geo`` gets ``geo_text``. The
    texts are made at reading, in the language of the node now; the file itself never holds a phrase."""
    for t in (doc.get("tests") if isinstance(doc, dict) else None) or ():
        ex = t.get("extra") if isinstance(t, dict) else None
        text = geo_text(ex.get("geo"), lang) if isinstance(ex, dict) else None
        if text:
            t["geo_text"] = text
    return doc


# ── start, cancel, status ───────────────────────────────────────────────────

def active(runner):
    """The ids of the checks whose units run now ([] when ``systemctl`` failed too: see ``_active``)."""
    return _active(runner) or []


def _active(runner):
    """The ids of the checks whose units run now; None — ``systemctl list-units`` failed."""
    res = runner.run(["systemctl", "list-units", "--type=service", "--state=active,activating,deactivating",
                      "--no-legend", "--plain", "--no-pager", "kaskad-bench-*"], timeout=20)
    if not res.ok:
        return None
    out = []
    for line in (res.out or "").splitlines():
        f = line.split()
        m = UNIT.fullmatch(f[0]) if f else None
        if m:
            out.append(m.group(1))
    return out


def _exec_argv(rid, mode, by, sched_id, retry_of, force, precheck, iperf=False, skip=(), only=()):
    code = ("import runpy, sys; sys.path.insert(0, '%s'); "
            "runpy.run_module('kaskad.bench', run_name='__main__', alter_sys=True)" % paths.lib_current())
    argv = [PYTHON, "-I", "-c", code, "worker", "--id", rid, "--mode", mode, "--by", by, "--precheck", str(precheck)]
    if sched_id is not None:
        argv += ["--sched", sched_id]
    if retry_of is not None:
        argv += ["--retry-of", retry_of]
    if force:
        argv.append("--force")
    if iperf:
        argv.append("--iperf")
    skip = _skip(skip, by)
    if skip:
        argv += ["--skip", ",".join(skip)]
    if only:
        argv += ["--only", ",".join(_only(only))]
    return argv


def _stop_post(rid):
    """``ExecStopPost`` of the unit: the fio file goes even after SIGKILL or OOM (a «trap» of RS §2 that survives
    them). Only a plain path (letters, digits, «/._-»): else none — the boot and the next check remove it."""
    path = os.path.join(work_dir(rid), suite.FIO_FILE)
    return ["ExecStopPost=/bin/rm -f " + path] if _SAFE_PATH.fullmatch(path) else []


def command(rid, mode, by, sched_id=None, retry_of=None, force=False, precheck=PRECHECK_S, lang=None, iperf=False,
            skip=(), only=()):
    """The argv of ``systemd-run`` for the check ``rid`` (all of it ours: checked before)."""
    _check_id(rid)
    _check(mode, by, sched_id, retry_of, iperf, skip, only)
    return (["systemd-run", "--unit=" + unit_name(rid), "--description=Kaskad 3: server check",
             "--collect", "--no-block", "--quiet"]
            + ["--property=" + p for p in PROPS + tuple(_stop_post(rid))] + i18n.setenv_args(lang)
            + _exec_argv(rid, mode, by, sched_id, retry_of, force, int(precheck), iperf, skip, only))


def iperf_consent(lang=None):
    """The line of test 8b beside its choice (RS §2, A.3; D-144: on by default in a full check, the person may turn it
    off): the chosen point of a provider — and the next one of the list when it does not answer — sees the address of
    the server (the points are tried one by one, REV-ns-b3 С-1)."""
    return tr("bench_run.channel.iperf_consent", lang)


def _mark(rid, key="bench_run.log.mark_failed", **kw):
    """A mark of §2.1 (``marks.record``) that never stops a check: a broken state.json, a busy lock, no right to
    write — a line of the journal (REV-ns-b1-2 придирка 3): ``key`` — its text (the consent at the start: «the check
    goes on»; the first manual check at the end: «the result is saved» — REV-ns-b1-3 П-4)."""
    try:
        marks.record(**kw)
    except Exception as e:                  # noqa: BLE001 — a mark never stops or loses a check
        util.log_tr(key, id=rid, why=type(e).__name__)


def start(mode="quick", by="cli", sched_id=None, retry_of=None, force=False, install=None, runner=None, lang=None,
          iperf=False, skip=(), only=(), with_iperf3=None):
    """Start a check. ``install`` — the answer to the question about the packages (``bench.pkgs.question``): True —
    install the missing ones first (a manual check only), False — the answer «no» is remembered, None — not asked.
    ``iperf`` — test 8b: the person agreed to the line «the point of a provider sees the address of the server» (a
    manual check only; iperf3 is installed by the same answer ``install``). ``skip`` — the tests of the channel to
    leave out (``bench.channel.SKIP``: 8b, 9, up, down; the schedule and the repeat — 8b and 9 always, B11).
    ``only`` — the links a repeat measures again (``bench.suite.LINKS``; by ``auto`` only, SS §2.8 п. 2).
    ``with_iperf3`` — iperf3 is one of the programs of the answer ``install``: None — when 8b runs (``iperf``); a bool —
    exactly as the price showed it (D-144, С2 REV-score-rename: a full check of a node that may be in Russia with the
    upload on — iperf3; the quick kind, the upload off, a node abroad — never: 8b allowed by default installs nothing
    it was not asked about).
    -> {"id", "unit", "missing": [packages still missing]}. KaskadError — not root, a bad argument, another check
    runs (``data.kind = running``), apt or systemd-run failed."""
    _root()
    _check(mode, by, sched_id, retry_of, iperf, skip, only)
    runner = runner or sysrun.default_runner()
    busy = active(runner)
    if busy:
        raise KaskadError(tr("bench_run.err.running"), data={"kind": "running", "id": busy[0]})
    want3 = bool(iperf) if with_iperf3 is None else bool(with_iperf3)
    miss = pkgs.missing(runner, iperf=want3)
    had = not [p for p in miss if p in pkgs.NEED]
    if install is True and miss:
        pkgs.install(runner, miss, by)
        miss = pkgs.missing(runner, iperf=want3)
    rid = new_id()
    if by in MANUAL and (had or install is not None):
        _mark(rid, pkgs="yes" if had or install else "no")      # the programs are there: nothing to ask (NS-S4)
    ensure_tmpfiles()
    precheck = PRECHECK_S if by in MANUAL else 0
    res = runner.run(command(rid, mode, by, sched_id, retry_of, force, precheck, lang, iperf, skip, only), timeout=60)
    if not res.ok:
        raise KaskadError(tr("bench_run.err.start", why=(res.err or res.out or "").strip()[:200]))
    util.log_tr("bench_run.log.started", id=rid, mode=mode, by=by)
    return {"id": rid, "unit": unit_name(rid), "missing": miss}


def cancel(rid, runner=None):
    """Stop the check ``rid`` (its unit; the worker writes ``cancelled``). -> True when a stop was asked."""
    _root()
    _check_id(rid)
    runner = runner or sysrun.default_runner()
    if rid not in active(runner):
        return False
    res = runner.run(["systemctl", "stop", "--no-block", unit_name(rid)], timeout=30)
    if res.ok:
        util.log_tr("bench_run.log.cancel", id=rid)
    return res.ok


def ensure_tmpfiles():
    """The line of systemd-tmpfiles that removes the fio file of a check cut by a reboot at the boot (only the file:
    ``meta.json`` stays for the record ``killed``). Never the reason a check does not start."""
    text = TMPFILES_TEXT % suite.FIO_FILE
    try:
        if util.read_text(tmpfiles_path()) != text:
            util.ensure_dir(os.path.dirname(tmpfiles_path()), 0o755)
            util.atomic_write(tmpfiles_path(), text, 0o644)
    except OSError:
        pass


def _json(path):
    try:
        doc = json.loads(util.read_text(path) or "null")
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def status(rid, runner=None):
    _check_id(rid)
    runner = runner or sysrun.default_runner()
    if rid in active(runner):
        return {"state": "running", "progress": _json(os.path.join(work_dir(rid), "progress.json"))}
    doc = _json(os.path.join(spool_dir(), rid + ".json"))
    if doc is None:
        last = _json(results_path())
        doc = last if last and (last.get("run") or {}).get("id") == rid else None
    return {"state": "done", "result": shown(doc)} if doc else {"state": "unknown"}


# ── the worker (inside the unit) ────────────────────────────────────────────

class _Stop(suite.Cancelled):          # a BaseException: no «except Exception» of a step swallows the stop
    pass


def _on_term(signum, frame):
    raise _Stop()


def _ensure_dirs():
    util.ensure_dir(bench_dir(), 0o700)
    util.ensure_dir(spool_dir(), 0o700)


def prune_spool(now=None):
    """The one cleaning of the queue (REV-ns-b12-2, the coordinator: one writer, one cleaning) — ``spool.prune``:
    checks older than 400 days by ``run.ts``, at most 3000, the tariff files too, a broken file to bad/. Runs at
    every start of a check and from the timer, with the gateway or without (the tariff files never pile up)."""
    try:
        return spool.prune(now)
    except OSError:
        return 0


def measured(doc):
    """The check measured a test besides the inventory (``ok`` | ``partial`` with such a row)."""
    return doc["run"].get("status") in ("ok", "partial") and any(
        t.get("ok") and t.get("test") not in INV_TESTS for t in doc.get("tests") or ())


def write_doc(doc):
    """The queue file of the check (0600, atomically) and, for a check that measured (``measured``),
    ``results.json`` — the last numbers for a quick read: a skipped, stopped or empty check never hides them."""
    _check_id(doc["run"]["id"])
    _ensure_dirs()
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        spool.put(doc)                                 # the one writer of the queue (SS §3.2: checked, 0600, atomic)
    except ValueError as e:                            # never: B1 writes by the contract; a line, not a lost check
        util.log_tr("bench_run.log.failed", id=doc["run"]["id"], why="queue: %s" % (str(e)[:80] or "?"))
    if measured(doc):
        util.atomic_write(results_path(), text + "\n", 0o600)


def _run_doc(rid, mode, by, sched_id, retry_of, ts):
    return {"id": rid, "ts": int(ts), "end_ts": None, "mode": mode, "by": by, "sched_id": sched_id,
            "retry_of": retry_of, "inv": None, "status": None, "why": None, "why_args": None, "rx": None, "tx": None,
            "dur_s": None, "cpu_s": None, "under_load": None, "pkgs": None, "score": None, "scale_version": None,
            "links": None}


def _bench_rows(rid, mode, rows, under):
    out = []
    for r in rows:
        row = {"run": rid, "test": r["test"], "ts": r["ts"], "value": r["value"], "unit": r["unit"],
               "scale_version": None, "mode": mode, "under_load": under, "ok": r["ok"], "why": r["why"], "score": None,
               "value_corr": r.get("value_corr"), "people": r.get("people"), "ca": r.get("ca")}
        if r.get("extra"):
            row["extra"] = dict(r["extra"])  # the channel: {"budget": true} — the cap ended the try (B12 extra.budget)
        if r.get("dns_src"):
            row["dns_src"] = r["dns_src"]   # what the DNS test asked: «kaskad» | «system» (REV-ns-b1-2 З-2, B12 reads it)
        out.append(row)
    return out


def _last_mark(path, meta):
    """The time of the last mark of a killed check: the newest mtime of its files (progress, meta), else its start."""
    best = None
    for n in ("progress.json", "meta.json"):
        try:
            t = os.stat(os.path.join(path, n)).st_mtime
        except OSError:
            continue
        best = t if best is None else max(best, t)
    return int(best if best is not None else (meta.get("ts") or _now()))


def clean_stale(runner, mine):
    """Work directories of checks that do not run (a killed one: OOM, SIGKILL, a reboot) away; such a check without
    a queue file gets one: ``failed``/``killed``. Called ONLY under the lock (no other check runs then). A directory
    whose unit is alive is never touched, and nothing at all is when ``systemctl list-units`` failed or did not show
    our own unit ``mine`` (an empty answer is not «no unit»)."""
    try:
        names = os.listdir(bench_dir())
    except OSError:
        return []
    names = [n for n in names if re.fullmatch(r"work-([0-9a-f]{10})", n) and n != "work-" + mine]
    if not names:
        return []
    live = _active(runner)
    if live is None or mine not in live:
        return []
    gone = []
    for n in names:
        rid = n[len("work-"):]
        if rid in live:
            continue
        path = os.path.join(bench_dir(), n)
        meta = _json(os.path.join(path, "meta.json")) or {}
        end = _last_mark(path, meta)
        shutil.rmtree(path, ignore_errors=True)
        gone.append(rid)
        if not os.path.exists(os.path.join(spool_dir(), rid + ".json")) and meta.get("mode") in MODES:
            by = meta.get("by") if meta.get("by") in BY else "cli"
            sid, rof = meta.get("sched_id"), meta.get("retry_of")
            sid = sid if isinstance(sid, str) and SCHED_ID.fullmatch(sid) else None
            rof = rof if isinstance(rof, str) and ID.fullmatch(rof) else None
            ts = meta.get("ts") if type(meta.get("ts")) is int else end
            run = _run_doc(rid, meta["mode"], by, sid, rof, ts)
            run.update(status="failed", why="killed", end_ts=max(end, int(ts)))
            try:
                write_doc({"v": V, "run": run, "tests": []})
            except (OSError, KaskadError):
                pass
    return gone


def _flock(path):
    import fcntl
    util.ensure_run_dir(os.path.dirname(path))
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _unflock(fd, path):
    import fcntl
    try:
        os.utime(path)                      # the end of the check (gw/hostping.speed_busy)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def worker(rid, mode, by, sched_id=None, retry_of=None, force=False, precheck=PRECHECK_S, runner=None, meter=None,
           iperf=False, skip=(), only=()):
    """The check itself (the ExecStart of its unit). Always writes the queue file (but for a bad argument)."""
    _root()
    _check_id(rid)
    _check(mode, by, sched_id, retry_of, iperf, skip, only)
    runner = runner or sysrun.default_runner()
    t0, m0 = _now(), _mono()
    run = _run_doc(rid, mode, by, sched_id, retry_of, t0)
    watch, fd, ctx = None, None, None
    old = signal.signal(signal.SIGTERM, _on_term)
    work = work_dir(rid)
    try:
        try:
            _ensure_dirs()
            prune_spool()
            fd = _flock(lock_path())
            if fd is None:
                others = [x for x in active(runner) if x != rid]
                run.update(status="skipped", why="running" if others else "locked")
            else:
                clean_stale(runner, rid)
                os.mkdir(work, 0o700)
                util.atomic_write(os.path.join(work, "meta.json"),
                                  json.dumps({"mode": mode, "by": by, "ts": int(t0), "sched_id": sched_id,
                                              "retry_of": retry_of}), 0o600)
                meter = meter or quiet.Meter(unit_name(rid), runner)
                seed = []
                d = quiet.check(meter, precheck, out=seed) if precheck > 0 else None
                args, why = None, None
                if d is not None and (not d["free"] or d.get("disk_busy")):
                    args = {"mbps": d["people_mbps"], "thr_mbps": quiet.THR_MBPS, "cpu": d["cpu_pct"],
                            "thr_cpu": quiet.THR_CPU, "io": d.get("io_pct"), "thr_io": quiet.THR_IO}
                    why = "busy" if not d["free"] else "busy_disk"
                if args and not force:
                    run.update(status="skipped", why=why, why_args=args)
                else:
                    if args:
                        run["why_args"] = {"forced": args}
                    watch = quiet.Watch(meter, seed=seed[-1:]).start()
                    ctx = suite.Ctx(runner, mode, work, suite._mono() + DEADLINE_S - (_mono() - m0), st=_state(),
                                    progress=_progress(work, rid, lambda: getattr(ctx, "stage", None)), meter=meter,
                                    opts={"skip": _skip(skip, by), "iperf": bool(iperf), "manual": by in MANUAL,
                                          "only": _only(only)})
                    suite.run_all(ctx)
                    run["status"] = "ok" if ctx.rows and all(r["ok"] for r in ctx.rows) else "partial"
        except suite.Cancelled:
            run.update(status="cancelled", why="timeout" if _mono() - m0 >= RUNTIME_MAX_S - 30 else "stopped")
        except Exception as e:              # noqa: BLE001 — every end of a check is a record
            run.update(status="failed", why="error", why_args={"type": type(e).__name__})
            util.log_tr("bench_run.log.failed", id=rid, why=type(e).__name__)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)   # the record is written whole; systemd kills after 30 s
        doc = _finish(run, watch, t0, ctx)
        if fd is not None:                  # REV З-7: the measuring is over — the lock of the speed tests goes before
            _unflock(fd, lock_path())       # the long count of the drops and of the repeat (the test of the exits runs)
            fd = None
        retry_after(doc, runner)
        return doc
    finally:
        if fd is not None:
            _unflock(fd, lock_path())
        shutil.rmtree(work, ignore_errors=True)
        signal.signal(signal.SIGTERM, old if old is not None else signal.SIG_DFL)


def retry_after(doc, runner):
    """B11, after the record is on the disk and the lock is let go (REV З-7): ONE replay of ``score.drop_events`` over
    the history with this check (``drops_of``): its events bench_drop go into its queue file again (SS §3.2: the runner
    works them out, the one writer stores them; the file written twice is harmless — the rows are replaced whole), and
    for a good check that is not a repeat — the links that fell (``score.retry_wanted`` from the state before it, §2.8,
    NS-S15) -> the repeat in 60–120 min within the limits (``sched_run.plan_retry``); not armed — a line «check again
    in an hour or two». «Остановить» works here: SIGTERM — no drops, no repeat, the record stays. Never fails the
    check. -> the answer of ``plan_retry`` | None."""
    r = doc.get("run") or {}
    if r.get("status") not in ("ok", "partial"):
        return None
    old = signal.signal(signal.SIGTERM, _on_term)
    try:
        links = drops_of(doc)
    except suite.Cancelled:
        util.log_tr("bench_run.log.after_stopped", id=r["id"])
        return None
    except Exception as e:                  # noqa: BLE001 — the drops and the repeat are extras: the check is written
        util.log_tr("bench.log.retry_not_set", why=type(e).__name__)
        return None
    finally:
        signal.signal(signal.SIGTERM, old if old is not None else signal.SIG_DFL)   # in the worker: SIG_IGN again
    if doc.get("drops"):
        try:
            spool.put(doc)
        except (OSError, ValueError) as e:
            util.log_tr("bench_run.log.failed", id=r["id"], why="drops: %s" % type(e).__name__)
    if not links:
        return None
    fell, links = links, suite.repeat_links(links, _state())   # what the repeat can measure again (NS-S5)
    if not links:
        util.log_tr("bench_run.log.retry_cannot", id=r["id"], links=",".join(fell))
        return None
    try:
        from . import sched_run
        res = sched_run.plan_retry(runner, r, links, now=int(r.get("end_ts") or _now()))
    except Exception as e:                  # noqa: BLE001
        util.log_tr("bench.log.retry_not_set", why=type(e).__name__)
        return None
    if res.get("blocked") == "not_armed":
        util.log_tr("bench_run.log.retry_manual", id=r["id"])
    elif res.get("set"):
        util.log_tr("bench_run.log.retry_set", id=r["id"], min=int(res["set"]) // 60, links=",".join(links))
    return res


def drops_of(doc):
    """The events bench_drop of this check (into ``doc["drops"]``, the fields of ``spool.DROP_KEYS``) and the links of
    its repeat — one replay over ``history.sample`` with this check (its file is on the disk: the new base of its
    inventory is in ``epochs`` too). -> [links] (none for a repeat: a repeat sets no repeat)."""
    from .. import score
    from . import history
    r = doc["run"]
    smp = history.sample(int(r.get("end_ts") or _now()) + 1)
    mine = next((x for x in smp["runs"] if x.get("id") == r["id"]), None)
    if mine is None:
        return []
    spec = score.load_spec()
    pre = {"ts": mine["ts"]}
    evs = score.drop_events(spec, smp["runs"], epochs=smp["epochs"], _pre=pre)[0]
    own = [dict((f, e[f]) for f in spool.DROP_KEYS if e.get(f) is not None) for e in evs if e.get("run") == r["id"]]
    if own:
        doc["drops"] = own
    if r.get("by") == "auto":
        return []
    return score.retry_wanted(spec, smp["runs"], mine, epochs=smp["epochs"], _state=pre.get("state"))


def _state():
    """The state for the steps (the probes' flags, the engines); a broken file is no reason to lose the check."""
    try:
        return state.load()
    except Exception:                       # noqa: BLE001
        return {}


def _progress(work, rid, stage=None):
    """``stage()`` -> (k, m) of the step of ``suite.run_all`` (the ctx is made after this writer: read at the call)."""
    def progress(step, i, n):
        k, m = (stage() if stage else None) or (None, None)
        try:
            util.atomic_write(os.path.join(work, "progress.json"),
                              json.dumps({"id": rid, "step": step, "i": i, "n": n, "k": k, "m": m, "ts": int(_now())}),
                              0o600)
        except OSError:
            pass
    return progress


def _finish(run, watch, t0, ctx):
    """Close the record: what was measured, the load, the counters of the unit, the time; the queue file; marks."""
    rows, series = [], None
    if ctx is not None:
        suite.remove_files(ctx)
        rows, series, run["inv"] = ctx.rows, ctx.series, ctx.inv or None
    if watch is not None:
        watch.stop()
        w = watch.result()
        run["under_load"] = None if w["under_load"] is None else (1 if w["under_load"] else 0)
        if (run.get("why_args") or {}).get("forced"):
            run["under_load"] = 1               # busy before the start and run anyway: under load whatever came after
        load = {"people_max": w["people_max"], "cpu_max": w["cpu_max"]}
        if not w["known"]:
            load["load"] = "unknown"
        run["why_args"] = dict(run.get("why_args") or {}, **load)
        u = watch.last_unit() or {}
        run["rx"], run["tx"] = u.get("rx"), u.get("tx")
        run["cpu_s"] = round(u["cpu_ns"] / 1e9, 3) if u.get("cpu_ns") is not None else None
    run["end_ts"] = int(_now())
    run["dur_s"] = round(max(0.0, _now() - t0), 1)
    have = ctx.have if ctx is not None else {}
    run["pkgs"] = 1 if have.get("sysbench") and have.get("fio") else (0 if have else None)
    doc = {"v": V, "run": run, "tests": _bench_rows(run["id"], run["mode"], rows, run["under_load"])}
    if series:
        doc["series"] = series
    write_doc(doc)
    if run["by"] in MANUAL and measured(doc):
        _mark(run["id"], "bench_run.log.mark_failed_end", first_manual=True, now=run["end_ts"])
    util.log_tr("bench_run.log.done", id=run["id"], status=run["status"], why=run["why"] or "-")
    return doc
