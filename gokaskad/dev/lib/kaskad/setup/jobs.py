"""Long steps of the quick setup run apart from the panel (docs/k3/SPEC-setup.md §2.4): the build of the kernel
module, apt, the certificate, the DNS wait must survive a closed tab and a restart of the panel, and must not run
inside its CPUQuota/MemoryMax.

    start(step, body)   one job at a time: the body (it may hold a key or a token) goes to a 0600 file in
                        /run/kaskad/setup, the step runs as ``kaskadctl setup step <step> --job <id>`` — a transient
                        unit (``systemd-run --unit kaskad-setup-<id> --collect``) under systemd, else a detached
                        process; the child reads the file and deletes it at once (never argv, never the journal)
    status()            /run/kaskad/setup/job.json: {"id", "step", "state": starting | running | done | failed,
                        "started", "updated", "items": [{"item", "title", "state", "detail", "since"}], "result"}
                        — a running job whose process is gone becomes «failed» with the reason in words
    Progress(job)       what a step reports: ``item(key, state, detail, title=None)`` rewrites job.json atomically

Rows follow the design (handoff §5): state wait | ok | warn | err | idle, ``since`` — epoch seconds of the state (the
page counts the timer from it).
"""

import json
import os
import secrets as _random
import subprocess
import sys
import threading
import time

from .. import i18n, paths, util
from ..errors import ConflictError, KaskadError
from ..i18n import tr

STARTING_MAX = 60.0            # s: a job that never reported its process is failed then
LAUNCH = None                  # tests: callable(job_id, step) instead of a process

_now = time.time


def run_dir():
    return paths.P("/run/kaskad/setup")


def job_path():
    return os.path.join(run_dir(), "job.json")


def input_path(job_id):
    return os.path.join(run_dir(), "in-%s.json" % job_id)


def read():
    raw = util.read_text(job_path())
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    return doc if isinstance(doc, dict) else None


def write(doc):
    util.ensure_dir(run_dir(), 0o700)
    doc["updated"] = _now()
    util.atomic_write(job_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", 0o600)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def status():
    """The current (or last) job; a dead running one is marked failed here."""
    doc = read()
    if doc is None:
        return None
    if doc.get("state") in ("starting", "running"):
        gone = (doc.get("state") == "running" and doc.get("pid") and not _alive(doc["pid"])) or \
            (doc.get("state") == "starting" and _now() - float(doc.get("started") or 0) > STARTING_MAX)
        if gone:
            doc["state"] = "failed"
            doc["result"] = {"ok": False, "state": "error", "text": tr("setup.jobs.interrupted"),
                             "text_m": {"k": "setup.jobs.interrupted", "a": {}}, "error": {"code": "interrupted"}}
            write(doc)
    return doc


# What a person is told while a long step runs (T-M6-qs330 №7: «Сейчас идёт шаг «exits-add»…» — the inner name):
# what the wizard is doing now, in words, and that it is worth waiting. A step not named here — the general words.
# Values are keys of the catalog (3.3.2): busy_text() gives the words of the current language.
BUSY = {
    "import": "setup.jobs.busyImport",
    "import-undo": "setup.jobs.busyImportUndo",
    "import-file": "setup.jobs.busyImportFile",
    "protocols": "setup.jobs.busyProtocols",
    "exits-add": "setup.jobs.busyExitsAdd",
    "cert": "setup.jobs.busyCert",
    "site": "setup.jobs.busySite",
    "template": "setup.jobs.busyTemplate",
    "templates": "setup.jobs.busyTemplates",
    "vless-mode": "setup.jobs.busyVlessMode",
}
BUSY_OTHER = "setup.jobs.busyOther"


def busy_text(step):
    return tr(BUSY.get(str(step or "")) or BUSY_OTHER)


def busy():
    doc = status()
    return doc if doc is not None and doc.get("state") in ("starting", "running") else None


def start(step, body, runner=None):
    """Start ``step`` with ``body`` (dict). ConflictError when another job runs. -> the job document."""
    cur = busy()
    if cur is not None:
        raise ConflictError(busy_text(cur.get("step")),
                            data={"job": cur.get("id"), "step": cur.get("step")})
    job_id = _random.token_hex(6)
    util.ensure_dir(run_dir(), 0o700)
    util.atomic_write(input_path(job_id), json.dumps(body or {}, ensure_ascii=False), 0o600)
    doc = {"id": job_id, "step": step, "state": "starting", "started": _now(), "items": [], "result": None,
           "pid": None}
    write(doc)
    try:
        _launch(job_id, step, runner)
    except Exception as e:                      # noqa: BLE001 — the job must not stay «starting»
        try:
            os.unlink(input_path(job_id))
        except OSError:
            pass
        doc.update(state="failed", result={"ok": False, "state": "error",
                                           "text": tr("setup.jobs.launchFailed", error=e),
                                           "error": {"code": "launch"}})
        write(doc)
        raise KaskadError(tr("setup.jobs.launchFailed", error=e))
    return doc


def _launch(job_id, step, runner):
    if LAUNCH is not None:
        LAUNCH(job_id, step)
        return
    ctl = paths.kaskadctl()
    argv = [ctl, "setup", "step", step, "--job", job_id] if os.path.isfile(ctl) else \
        [sys.executable, "-m", "kaskad", "setup", "step", step, "--job", job_id]
    if os.environ.get("INVOCATION_ID") and runner is not None and runner.which("systemd-run"):
        # the language of the request that started the job goes with it (3.3.2: KASKAD_LANG of the unit)
        res = runner.run(["systemd-run", "--unit", "kaskad-setup-%s" % job_id, "--collect", "--quiet",
                          "--description=Kaskad 3: a step of the quick setup"]  # a unit caption: plain English (3.3.2 №3)
                         + i18n.setenv_args() + argv, timeout=30)
        if not res.ok:
            raise KaskadError(util.redact((res.err or res.out).strip()[:200]) or "systemd-run %d" % res.rc)
        return
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, env=i18n.lang_env())


def take_input(job_id):
    """The child: the body of its job, the file deleted at once."""
    p = input_path(job_id)
    raw = util.read_text(p)
    try:
        os.unlink(p)
    except OSError:
        pass
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


class Progress(object):
    """Rows of a step. Without a job (``kaskadctl setup step`` in SSH) — lines on stderr instead of job.json."""

    MARKS = {"ok": "✓", "wait": "…", "warn": "!", "err": "✗", "idle": "·"}

    def __init__(self, job_id=None, step=None, echo=None):
        self.job_id = job_id
        self.step = step
        self.echo = echo
        self.rows = []
        self._lock = threading.Lock()
        if job_id:
            doc = read() or {}
            if doc.get("id") == job_id:
                doc.update(state="running", pid=os.getpid())
                write(doc)

    def item(self, key, state, detail="", title=None):
        with self._lock:
            row = next((r for r in self.rows if r["item"] == key), None)
            if row is None:
                row = {"item": key, "title": title or key, "state": None, "detail": "", "since": _now()}
                self.rows.append(row)
            if title:
                row["title"] = title
                _keep(row, "title_m", i18n.said_doc(title, util.redact))
            if row["state"] != state:
                row["since"] = _now()
            row["state"] = state
            row["detail"] = util.redact(detail or "")
            _keep(row, "detail_m", i18n.said_doc(detail, util.redact))
            self._flush()
        if self.echo is not None:
            self.echo("  %s %s%s" % (self.MARKS.get(state, "·"), row["title"], (" — " + row["detail"]) if row["detail"]
                                     else ""))

    def __call__(self, key, state, detail=""):
        self.item(key, state, detail)

    def _flush(self):
        if not self.job_id:
            return
        doc = read() or {}
        if doc.get("id") != self.job_id:
            return
        doc["items"] = [dict(r) for r in self.rows]
        doc["state"] = "running"
        write(doc)

    def finish(self, result):
        if not self.job_id:
            return
        doc = read() or {}
        if doc.get("id") != self.job_id:
            return
        doc["items"] = [dict(r) for r in self.rows]
        doc["state"] = "done" if result.get("state") not in ("error",) else "failed"
        doc["result"] = _keep_result(result)
        write(doc)


def _keep(row, field, said):
    if said is None:
        row.pop(field, None)
    else:
        row[field] = said


def _keep_result(result):
    """The result with what its words were said from (3.3.3), said again in the page's language when read: ``text_m``
    for the text; ``said_m`` — [{"p": path, "m": said_doc}] for every other text of tr() inside (``error.text``,
    ``data.failed[].text``, ``data.warnings[]``…). The rows of the progress (``items``) carry their own
    ``title_m``/``detail_m`` (a field with ``<field>_m`` beside it is not walked again)."""
    if not isinstance(result, dict):
        return result
    out = dict(result)
    _keep(out, "text_m", i18n.said_doc(result.get("text"), util.redact))
    said = []
    for f, v in result.items():
        if f != "text" and not f.endswith("_m"):
            _said_paths(v, [f], said)
    out.pop("said_m", None)
    if said:
        out["said_m"] = said
    return out


def _said_paths(v, path, out):
    if isinstance(v, i18n.Said):
        m = i18n.said_doc(v, util.redact)
        if m is not None:
            out.append({"p": path, "m": m})
    elif isinstance(v, dict):
        for k, x in v.items():
            if isinstance(k, str) and not k.endswith("_m") and k + "_m" not in v:
                _said_paths(x, path + [k], out)
    elif isinstance(v, (list, tuple)):
        for i, x in enumerate(v):
            _said_paths(x, path + [i], out)


def _say(m, written, lang):
    """The words of ``m`` in ``lang``; those it was written with when a key is unknown here or the record is broken."""
    try:
        return i18n.said_text(m, lang) if i18n.said_known(m) else written
    except Exception:                                # a broken record: keep the words it was written with
        return written


def _rows(items, lang):
    rows = []
    for r in items or []:
        if not isinstance(r, dict):
            rows.append(r)
            continue
        r = dict(r)
        for f in ("title", "detail"):
            m = r.pop(f + "_m", None)
            if m is not None:
                r[f] = _say(m, r.get(f), lang)
        rows.append(r)
    return rows


def _put(doc, path, m, lang):
    """Say again the text at ``path`` inside ``doc`` (copied along the way — job.json as read stays as it is)."""
    cur = doc
    for i, k in enumerate(path):
        try:
            nxt = cur[k]
        except (KeyError, IndexError, TypeError):
            return
        if i == len(path) - 1:
            if isinstance(nxt, str):
                cur[k] = _say(m, nxt, lang)
            return
        if isinstance(nxt, dict):
            nxt = dict(nxt)
        elif isinstance(nxt, list):
            nxt = list(nxt)
        else:
            return
        cur[k] = nxt
        cur = nxt


def localize(doc, lang=None):
    """A copy of job.json in ``lang`` (None: the language of the request — X-Kaskad-Lang of the page): the rows of the
    progress, and in the result its text, its rows and every text of tr() it keeps (refusals, warnings) said again
    from their keys (3.3.3 — the owner saw «в очереди», «Модуль ядра AmneziaWG» in the English wizard; after the end
    of the job too, and after a reload). A text without keys (from outside: an error of a tool) stays as written, and
    so does one whose key this version does not know. The keys themselves do not go out."""
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    out["items"] = _rows(doc.get("items"), lang)
    res = doc.get("result")
    if isinstance(res, dict):
        res = dict(res)
        m = res.pop("text_m", None)
        if m is not None:
            res["text"] = _say(m, res.get("text"), lang)
        if "items" in res:
            res["items"] = _rows(res.get("items"), lang)
        for s in res.pop("said_m", None) or []:
            if isinstance(s, dict) and isinstance(s.get("p"), list) and s["p"]:
                _put(res, s["p"], s.get("m"), lang)
        out["result"] = res
    return out
