"""``kaskadctl update ...`` — a new version of Kaskad, the way menu 11 does it (for the MCP server and the like).

    update check [--json]    is there a newer version: ``gokaskad --update-check`` (branch KASKAD_BRANCH, else main; on
                             main older than 3.x — test, on test older too — dev; the GitHub key of the settings). The
                             program does not change; a chain that came to dev writes KASKAD_BRANCH="dev" (``auto``,
                             ``remembered`` in the answer); a written k3 (frozen at 3.3.3) moves to dev once.
    update units [--quiet] [--json]
                             after an update: the units already on disk get this version's text (kaskad.unit_refresh;
                             a new caption — daemon-reload, no restart); --k3-post-update calls it
    update files [--quiet] [--json]
                             after an update: the files of 2.x a copy (.kbk) may have brought before 3.3.7.2 — the
                             monitors (bash of 2.x read them with ``source``) rewritten checked, bad ones removed; the
                             names of servers cleaned (REV-3371-2 Б-1/Б-2); --k3-post-update calls it
    update run [--json]      update now: ``gokaskad --self-update`` — menu 11 without questions (no GitHub key is asked
                             for; a bot that does not come up on the new version — the old one comes back)

Only install.sh downloads and replaces the program (its checks: syntax, «esac» at the end, no downgrade, the backup
gokaskad.bak, --k3-post-update of the new file, the restart of the bot and the monitor with a rollback). This module
runs it and tells the result: the version before and after, the script's own words. Exit codes — kaskad.errors
(4: the new version was rolled back).
"""

import json
import re

from . import errors, i18n, paths, sysrun, util
from .cli import add_json_flag, emit
from .errors import KaskadError
from .i18n import tr

CHECK_TIMEOUT = 5 * 60
RUN_TIMEOUT = 20 * 60
_VERSION = re.compile(r'^KASKAD_VERSION="([^"]*)"')
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ROLLED = "откат"                                                    # i18n: skip — разбор вывода install.sh
_NOTHING = ("Обновлять нечего", "уже последняя версия")              # i18n: skip — разбор вывода install.sh
# the line of install.sh 3.3.4+ (dev) and of the older ones (k3) — str.startswith takes both
_BRANCH_NOTE = ("Ветка обновлений: dev (тестовые сборки) — записана автоматически",  # i18n: skip — разбор вывода install.sh
                "Ветка обновлений: k3 (тестовые сборки) — записана автоматически")   # i18n: skip — разбор вывода install.sh


def _r():
    return sysrun.default_runner()


def _is_k3(path):
    v = file_version(path)
    return bool(re.match(r"^([3-9]|[1-9][0-9]+)\.", v))


def script():
    """A program of 3.x that knows --update-check/--self-update: the user's command (menu 11 replaces it), else the copy
    the services run. A command of 2.x (goInstaller or a rollback put it back) — a clear refusal, not «code 2»."""
    import os
    for p in (paths.command(), paths.script_copy()):
        if os.path.isfile(p) and _is_k3(p):
            return p
    for p in (paths.command(), paths.script_copy()):
        if os.path.isfile(p):
            raise KaskadError(tr("cli.update.old", version=file_version(p) or tr("cli.update.no_version")),
                              code=errors.UNSUPPORTED)
    raise KaskadError(tr("cli.update.not_found", command=paths.command(), copy=paths.script_copy()))


def _run(runner, argv, timeout, env=None):
    """The script in a process group of its own: a timeout stops it with curl and systemctl under it (sysrun's timeout
    stops bash alone). A fake runner of the tests — as it is. ``env`` — extra environment (K3_MARKS=1)."""
    import os
    import signal
    import subprocess
    if not isinstance(runner, sysrun.SystemRunner):
        return runner.run(argv, timeout=timeout, env=env)
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True, env=dict(runner.env, **(env or {})), start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return sysrun.Result(argv, p.returncode, out, err)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
                p.wait(timeout=5)
                break
            except (OSError, subprocess.TimeoutExpired):
                continue
        return sysrun.Result(argv, 124, "", "timeout after %ss" % timeout)


def file_version(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break
                m = _VERSION.match(line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def _lines(res):
    text = _ANSI.sub("", "%s\n%s" % (res.out or "", res.err or ""))
    return [" ".join(ln.split()) for ln in text.replace("\r", "\n").splitlines() if ln.strip()]


def check(runner):
    # the refusal text («no network or a GitHub key») comes from the script — in the language of the request (stand T-334)
    res = _run(runner, [script(), "--update-check"], CHECK_TIMEOUT, env={"KASKAD_LANG": i18n.get_lang()})
    doc = None
    for line in reversed((res.out or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                doc = json.loads(line)
            except ValueError:
                doc = None
            break
    if not isinstance(doc, dict):
        raise KaskadError(tr("cli.update.check_no_answer", rc=res.rc))
    if not doc.get("ok"):
        raise KaskadError(doc.get("text") or tr("cli.update.check_failed"), data={"reason": doc.get("reason")})
    return doc


MARK = "@K3:"
MARKS_ENV = {"K3_MARKS": "1"}                 # install.sh prints its machine lines «@K3:<code>[:param…]» (3.3.2, D)


def marks(lines):
    """[(code, [params])] of the lines «@K3:code:p1:p2» of install.sh (K3_MARKS=1), in order."""
    out = []
    for ln in lines:
        if ln.startswith(MARK):
            parts = ln[len(MARK):].strip().split(":")
            out.append((parts[0], parts[1:]))
    return out


def _human(lines):
    """The script's lines for a person (the language of the server): no machine lines, no progress «[*] …», no prompt
    of read -p (stdin is /dev/null: it returns at once, its prompt stays on the last line of stderr)."""
    return [util.redact(ln) for ln in lines if not ln.startswith((MARK, "[*]"))]


def _by_marks(mk, lines, before, after):
    """The result of --self-update by the machine lines of install.sh — never by its words."""
    codes = dict((c, p) for c, p in mk)                    # the last of a code wins
    said = _human(lines)
    errs = [ln[len("[ERROR]"):].strip() for ln in said if ln.startswith("[ERROR]")]
    notes = []
    if "update_branch_learned" in codes:
        notes.append(tr("cli.update.branch_note"))
    if "update_awg_restarted" in codes:
        notes.append(tr("cli.update.mark.awg_restarted"))
    if "update_rolled_back" in codes:
        raise KaskadError(tr("cli.update.rolled_back"), code=errors.ROLLED_BACK, data={"from": before, "to": after})
    if "update_done" in codes:
        p = codes["update_done"] + ["", ""]
        ver = p[0] if p[0] and p[0] != "?" else after
        warnings = [tr("cli.update.mark.warn." + w) if i18n.has("cli.update.mark.warn." + w) else w
                    for c, ps in mk if c == "update_warn" for w in ps[:1]]
        text = tr("cli.update.mark.done", version=ver, branch=p[1] or "?")
        if "update_restarted" in codes:
            text += " " + tr("cli.update.mark.restarted", services=", ".join(codes["update_restarted"]))
        out = {"ok": True, "updated": True, "from": before, "to": after or ver, "text": text, "notes": notes,
               "warnings": warnings, "partial": bool(warnings)}
        if warnings:
            out["text"] += " " + tr("cli.update.partial", warnings=" ".join(warnings))
        return out
    for code in ("update_latest", "update_nothing"):
        if code in codes:
            p = codes[code] + ["", ""]
            text = tr("cli.update.mark." + code[len("update_"):], version=p[0] or before, branch=p[1] or "?",
                      current=before or "?")
            if notes:
                text += " " + " ".join(notes)
            return {"ok": True, "updated": False, "from": before, "to": after or before, "text": text}
    if "update_error" in codes or "update_fetch_failed" in codes:
        p = codes.get("update_error") or ["fetch"]
        kind = p[0] or "install"
        branch = (p[1] if len(p) > 1 else "") or (codes.get("update_fetch_failed") or [""])[0] or "main"
        if kind == "fetch" or ("update_fetch_failed" in codes and "update_error" not in codes):
            raise KaskadError(tr("cli.update.fetch", branch=branch), data={"from": before, "reason": "fetch"})
        text = errs[-1] if errs else tr("cli.update.failed", rc="?")
        raise KaskadError(text, data={"from": before, "reason": kind})
    return None


def run(runner):
    path = paths.command()
    before = file_version(path) or file_version(paths.script_copy())
    res = _run(runner, [script(), "--self-update"], RUN_TIMEOUT, env=MARKS_ENV)
    after = file_version(path) or file_version(paths.script_copy())
    lines = _lines(res)
    if res.rc == 124:
        raise KaskadError(tr("cli.update.timeout", minutes=RUN_TIMEOUT // 60))
    mk = marks(lines)
    if mk:
        out = _by_marks(mk, lines, before, after)
        if out is not None:
            return out
        said = _human(lines)
        raise KaskadError(said[-1] if said else tr("cli.update.failed", rc=res.rc), data={"from": before})
    return _legacy(res, lines, before, after)


def _legacy(res, lines, before, after):
    """gokaskad before 3.3.2 prints no machine lines: its Russian words, as before (it speaks only Russian)."""
    from .gw import ops as gwops
    said = [ln for ln in lines if re.search("[А-Яа-яЁё]", ln)  # i18n: skip — слова install.sh (русские строки вывода)
            and not ln.startswith(("[*]", "Нажмите", "Enter"))]  # i18n: skip — подсказка install.sh «Нажмите Enter»
    text = gwops._script_error(said, "")
    if any(_ROLLED in ln and "не поднялся" in ln for ln in lines):  # i18n: skip — разбор вывода install.sh
        raise KaskadError(text or tr("cli.update.rolled_back"), code=errors.ROLLED_BACK,
                          data={"from": before, "to": after})
    done = [ln for ln in lines if ln.startswith(("[OK] Обновлено", "Перезапущено:"))]  # i18n: skip — вывод install.sh
    if (after and before and after != before) or done:             # the same version, another file: a fix of a fix
        # «[!] Ядро Kaskad 3 новой версии не подготовилось — службы остаются на прежней копии» and the like: the file is
        # new, but not everything followed it — said, not lost (critic 3.2.22-mcp №3)
        warnings = [ln[3:].strip() for ln in lines if ln.startswith("[!]")]
        out = {"ok": True, "updated": True, "from": before, "to": after,
               "text": " ".join(ln.replace("[OK] ", "") for ln in done) or tr("cli.update.updated", version=after),
               "notes": [ln for ln in lines if "AmneziaWG" in ln or ln.startswith(_BRANCH_NOTE)], "warnings": warnings,
               "partial": bool(warnings)}
        if warnings:
            out["text"] += " " + tr("cli.update.partial", warnings=" ".join(warnings))
        return out
    nothing = [ln for ln in lines if any(n in ln for n in _NOTHING)]
    if nothing:
        text = nothing[-1]
        if any(ln.startswith(_BRANCH_NOTE) for ln in lines):
            text += " " + tr("cli.update.branch_note")
        return {"ok": True, "updated": False, "from": before, "to": after or before, "text": text}
    if any(ln.startswith("Не удалось скачать") or "Токен не подошёл" in ln for ln in lines):  # i18n: skip — install.sh
        # меню 11 после отказа GitHub спрашивает ключ, и его последние строки — подсказка о ключе, а не причина
        m = [re.search(r"\(ветка ([^)]+)\)", ln) for ln in lines]  # i18n: skip — разбор вывода install.sh
        branch = next((x.group(1) for x in m if x), "main")
        raise KaskadError(tr("cli.update.fetch", branch=branch), data={"from": before, "reason": "fetch"})
    raise KaskadError(text or tr("cli.update.failed", rc=res.rc), data={"from": before})


def branch_label(doc):
    """«dev» / «dev, выбрана сама» — the branch of an answer of --update-check (auto: not written in the settings)."""
    return tr("cli.update.branch_auto", branch=doc.get("branch")) if doc.get("auto") else "%s" % doc.get("branch")


def cmd_check(args):
    doc = check(_r())
    br = branch_label(doc)
    if doc.get("available") and doc.get("latest") == doc.get("current"):
        text = tr("cli.update.same_version", current=doc.get("current"), branch=br)
    elif doc.get("available"):
        text = tr("cli.update.available", latest=doc.get("latest"), current=doc.get("current"), branch=br)
    else:
        text = tr("cli.update.nothing", current=doc.get("current"), branch=br)
    if doc.get("remembered"):
        text += " " + tr("cli.update.branch_note")
    emit(args, doc, text)
    return errors.OK


def cmd_run(args):
    res = run(_r())
    emit(args, res, res["text"])
    return errors.OK


def cmd_units(args):
    from . import unit_refresh
    res = unit_refresh.refresh(_r())
    if getattr(args, "quiet", False) and not getattr(args, "json", False):
        return errors.OK
    lines = [tr("cli.update.units.done", units=", ".join(res["written"])) if res["written"]
             else tr("cli.update.units.none")]
    if res["left"]:
        lines.append(tr("cli.update.units.left", units=", ".join(res["left"])))
    emit(args, res, lines)
    return errors.OK


def cmd_files(args):
    from .bot import aliases, monitors
    res = monitors.clean_dir()
    res["aliases"] = aliases.clean_existing()
    if getattr(args, "quiet", False) and not getattr(args, "json", False):
        return errors.OK
    lines = []
    if res["rewritten"]:
        lines.append(tr("cli.update.files.rewritten", files=", ".join(res["rewritten"])))
    if res["dropped"]:
        lines.append(tr("cli.update.files.dropped", files=", ".join(res["dropped"])))
    if res["aliases"]:
        lines.append(tr("cli.update.files.aliases"))
    emit(args, res, lines or [tr("cli.update.files.none")])
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("update", help=tr("cli.update.help.group"))
    sub = p.add_subparsers(dest="update_cmd", metavar=tr("cli.metavar_command"))
    sub.required = True
    add_json_flag(sub.add_parser("check", help=tr("cli.update.help.check"))).set_defaults(func=cmd_check)
    add_json_flag(sub.add_parser("run", help=tr("cli.update.help.run"))).set_defaults(func=cmd_run)
    pu = add_json_flag(sub.add_parser("units", help=tr("cli.update.help.units")))
    pu.add_argument("--quiet", action="store_true", help=tr("cli.update.help.units_quiet"))
    pu.set_defaults(func=cmd_units)
    pf = add_json_flag(sub.add_parser("files", help=tr("cli.update.help.files")))
    pf.add_argument("--quiet", action="store_true", help=tr("cli.update.help.units_quiet"))
    pf.set_defaults(func=cmd_files)
