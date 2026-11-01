"""``kaskadctl backup ...`` — a copy of the settings in one file (kaskad.backup, docs/k3/SPEC-subs-backup.md §2).

    backup export [--file F | --save | --stdout] [--parts a,b | --without a,b]   the copy of this node, encrypted
                     with a password: KASKAD_BACKUP_PASSWORD or the first line of stdin (never argv); --file — there
                     (default /root/kaskad-<адрес>-<дата>.kbk), --save — /var/backups/kaskad/exports/ (the MCP server:
                     the text never comes back), --stdout — the text itself (with --json: the field ``text``)
    backup import FILE [--parts a,b] [--yes]   without --yes — the preview only (what comes, what it replaces, the
                     address of the devices); --yes — applied, a snapshot first, a rollback on failure. FILE «-» —
                     the text on stdin, the password then only in KASKAD_BACKUP_PASSWORD
    backup undo [--yes]                         the snapshot taken before the last import, back
    backup summary [--parts a,b]                what a copy of this node would hold — counts, no secret

Every command takes --json. Exit codes — kaskad.errors (2: a wrong password, a damaged or foreign file).
"""

import getpass
import os
import sys

from . import backup, errors, paths, sysrun, util
from .cli import add_json_flag, emit
from .errors import KaskadError
from .i18n import fmt_datetime, tr


def _r():
    return sysrun.default_runner()


def _parts(args):
    parts = backup._parts(getattr(args, "parts", None))
    without = getattr(args, "without", None)
    if without:
        drop = backup._parts(without)
        parts = tuple(p for p in parts if p not in drop)
    if not parts:
        raise KaskadError(tr("cli_backup.err.no_parts"), code=errors.USAGE)
    return parts


def _password(twice=False, stdin_free=True):
    """The password: KASKAD_BACKUP_PASSWORD, else the terminal (getpass, twice for a new copy), else the first line of
    stdin. Never argv."""
    pw = os.environ.get(backup.PASS_ENV)
    if pw:
        return pw
    if not stdin_free:
        raise KaskadError(tr("cli_backup.err.stdin_busy", env=backup.PASS_ENV), code=errors.USAGE)
    try:
        tty = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        tty = False
    if tty:
        pw = getpass.getpass(tr("cli_backup.prompt.password"))
        if twice and getpass.getpass(tr("cli_backup.prompt.again")) != pw:
            raise KaskadError(tr("cli_backup.err.mismatch"), code=errors.USAGE)
        return pw
    if sys.stdin is None:
        raise KaskadError(tr("cli_backup.err.no_stdin", env=backup.PASS_ENV), code=errors.USAGE)
    return (sys.stdin.readline() or "").rstrip("\r\n")


def _say(text):
    sys.stderr.write("[*] %s\n" % text)
    sys.stderr.flush()


def _summary_lines(summary):
    out = []
    for p, s in summary.items():
        out.append("  %s: %s" % (backup.SHORT[p], ", ".join("%s=%s" % (k, v if not isinstance(v, list) else
                                                                         ",".join(v) or "—") for k, v in s.items())))
    return out


def cmd_export(args):
    parts = _parts(args)
    pw = _password(twice=True)
    path = None
    if args.save:
        d = os.path.join(paths.backups(), "exports")
        util.ensure_dir(paths.backups(), 0o700)
        util.ensure_dir(d, 0o700)
        path = d
    elif not args.stdout:
        path = args.file or paths.P("/root")
    res = backup.export(_r(), pw, parts, path=path)
    pw = None
    text = res.pop("text")
    if args.stdout:
        if args.json:
            res["text"] = text
        else:
            sys.stdout.write(text)
            return errors.OK
    emit(args, res, [tr("cli_backup.saved", file=res.get("file") or "—", size=util.human_bytes(res["size"]))] +
         _summary_lines(res["summary"]))
    return errors.OK


def _read_file(arg):
    if arg == "-":
        return sys.stdin.read(backup.FILE_MAX + 1), False
    if os.path.isdir(arg):
        raise KaskadError(tr("cli_backup.err.is_dir", path=arg), code=errors.USAGE)
    if not os.path.exists(arg):
        raise KaskadError(tr("cli_backup.err.no_file", path=arg), code=errors.USAGE)
    if os.path.getsize(arg) > backup.FILE_MAX:
        raise KaskadError(tr("backup.err.too_big", mb=backup.FILE_MAX // 2 ** 20), code=errors.USAGE)
    text = util.read_text(arg)
    if text is None:
        raise KaskadError(tr("cli_backup.err.unreadable", path=arg), code=errors.USAGE)
    return text, True


def cmd_import(args):
    text, stdin_free = _read_file(args.file)
    pw = _password(stdin_free=stdin_free)
    runner = _r()
    doc = backup.unseal(runner, text, pw)
    text = pw = None
    parts = backup._parts(args.parts) if args.parts else None
    if not args.yes:
        pv = backup.preview(runner, doc, parts, agents=args.agents)
        emit(args, {"ok": pv["ok"], "applied": False, "preview": pv},
             [tr("cli_backup.preview.head", created=fmt_datetime(pv["created"]) or "—", version=pv["version"] or "—")] + pv["text"] +
             [tr("cli_backup.preview.apply")])
        return errors.OK if pv["ok"] else errors.CONFLICT
    res = backup.apply(runner, doc, parts, say=_say, agents=args.agents)   # ход — в stderr и с --json (меню показывает его)
    emit(args, dict(res, applied=True), res["text"])
    return errors.OK


def cmd_undo(args):
    path = backup.last_snapshot()
    if not path:
        raise errors.ConflictError(tr("backup.err.no_undo"), data={"gone": True})
    if not args.yes:
        doc = backup.read_snapshot(path)
        gone = backup.undo_keys_gone(path)                   # their agents are cut off: said before, not only after
        emit(args, {"ok": True, "applied": False, "from": path, "created": doc["manifest"].get("created"),
                     "parts": doc["manifest"]["parts"], "agent_keys_gone": gone},
             [tr("cli_backup.undo.ask", path=path)] + ([tr("cli_backup.undo.agents", n=gone)] if gone else []))
        return errors.OK
    res = backup.undo(_r(), say=_say, path=path)
    emit(args, dict(res, applied=True), [tr("cli_backup.undo.done", path=path)] + res["text"][1:])
    return errors.OK


def cmd_summary(args):
    res = backup.summary_doc(_r(), _parts(args))
    emit(args, res, [tr("cli_backup.summary.head")] + _summary_lines(res["summary"]))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("backup", help=tr("cli_backup.help.backup"))
    sub = p.add_subparsers(dest="backup_cmd", metavar=tr("cli_backup.help.metavar"))
    sub.required = True
    pe = add_json_flag(sub.add_parser("export", help=tr("cli_backup.help.export", env=backup.PASS_ENV)))
    g = pe.add_mutually_exclusive_group()
    g.add_argument("--file", default=None, help=tr("cli_backup.help.file"))
    g.add_argument("--save", action="store_true", help=tr("cli_backup.help.save"))
    g.add_argument("--stdout", action="store_true", help=tr("cli_backup.help.stdout"))
    pe.add_argument("--parts", default=None, help=tr("cli_backup.help.parts", parts=",".join(backup.PARTS)))
    pe.add_argument("--without", default=None, help=tr("cli_backup.help.without"))
    pe.set_defaults(func=cmd_export)
    pi = add_json_flag(sub.add_parser("import", help=tr("cli_backup.help.import")))
    pi.add_argument("file", help=tr("cli_backup.help.import_file"))
    pi.add_argument("--parts", default=None, help=tr("cli_backup.help.import_parts"))
    pi.add_argument("--yes", action="store_true", help=tr("cli_backup.help.import_yes"))
    pi.add_argument("--agents", action="store_true", help=tr("cli_backup.help.import_agents"))   # SEC-3372 З-3
    pi.set_defaults(func=cmd_import)
    pu = add_json_flag(sub.add_parser("undo", help=tr("cli_backup.help.undo")))
    pu.add_argument("--yes", action="store_true")
    pu.set_defaults(func=cmd_undo)
    ps = add_json_flag(sub.add_parser("summary", help=tr("cli_backup.help.summary")))
    ps.add_argument("--parts", default=None)
    ps.set_defaults(func=cmd_summary)
