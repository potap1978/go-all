"""``kaskadctl ipcert …`` — the certificate of Let's Encrypt on the IP address of the node (docs/k3/SPEC-setup.md §1).

    ipcert status [--json]                         the certificate, the address, the timer, the last try
    ipcert issue [--ip A] [--staging] [--force] [--json]   issue now (http-01 on TCP/80; a valid one is kept)
    ipcert tick [--quiet] [--json]                 the timer kaskad-ipcert.timer: renew when needed and due
    ipcert remove [--json]                         the timer and the files away

A refusal prints the contract ``{"ok": false, "reason", "text", "retry_after"?}`` (+ ``error``, ``code``); exit code 3
for a taken port 80, 1 otherwise.
"""

import sys

from .. import errors, sysrun
from ..cli import add_json_flag, emit
from ..i18n import fmt_datetime, tr
from . import ops
from .http01 import Refusal


def _say(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _progress(args):
    if getattr(args, "json", False) or getattr(args, "quiet", False):
        return None
    marks = {"ok": "✓", "wait": "…", "warn": "!", "err": "✗"}
    return lambda item, st, detail="": _say("  %s %s" % (marks.get(st, "·"), detail or item))


def _refused(args, e):
    code = e.code if e.code != errors.OK else errors.ERROR
    if getattr(args, "json", False):
        emit(args, dict(e.doc, error=e.text, code=code))
    elif not getattr(args, "quiet", False):
        _say(tr("ipcert.cli.refused", text=e.text))
    return code


_STATES = ("none", "valid", "expiring", "expired", "other_ip")


def _status_lines(doc):
    st = doc["state"]
    word = tr("ipcert.cli.state." + st, ip=doc.get("ip")) if st in _STATES else st
    lines = [tr("ipcert.cli.title", state=word)]
    if doc.get("not_after"):
        lines.append(tr("ipcert.cli.address", ip=doc.get("ip"), until=fmt_datetime(doc["not_after"], utc=True),
                        hours=int(doc.get("hours_left") or 0),
                        staging=tr("ipcert.cli.staging") if doc.get("staging") else ""))
    lines.append(tr("ipcert.cli.needed", needed=tr("ipcert.cli.yes" if doc.get("needed") else "ipcert.cli.needed_no"),
                    timer=tr("ipcert.cli.timer_yes" if doc.get("timer") else "ipcert.cli.no")))
    lt = doc.get("last_try") or {}
    if lt and not lt.get("ok"):
        lines.append(tr("ipcert.cli.last_try", at=fmt_datetime(lt.get("at") or ""), text=lt.get("text")))
    return lines


def cmd_status(args):
    doc = ops.status(sysrun.default_runner())
    emit(args, doc, _status_lines(doc))
    return errors.OK


def cmd_issue(args):
    try:
        res = ops.issue(sysrun.default_runner(), ip=args.ip, staging=args.staging, force=args.force,
                        progress=_progress(args))
    except Refusal as e:
        return _refused(args, e)
    emit(args, res, res["text"])
    return errors.OK


def cmd_tick(args):
    res = ops.tick(sysrun.default_runner())
    if not args.quiet:
        act = res.get("action")
        emit(args, res, res.get("text") or (tr("ipcert.cli.tick." + act) if act in ("none", "renewed", "failed")
                                            else ""))
    return errors.OK if res.get("ok") else errors.ERROR


def cmd_remove(args):
    res = ops.remove(sysrun.default_runner())
    emit(args, res, tr("ipcert.cli.removed" if res["files"] or res["units"] else "ipcert.cli.not_there"))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("ipcert", help=tr("ipcert.cli.help"))
    sub = p.add_subparsers(dest="ipcert_cmd", metavar=tr("ipcert.cli.metavar"))
    sub.required = True
    add_json_flag(sub.add_parser("status", help=tr("ipcert.cli.help_status"))).set_defaults(func=cmd_status)
    p1 = add_json_flag(sub.add_parser("issue", help=tr("ipcert.cli.help_issue")))
    p1.add_argument("--ip", default=None, help=tr("ipcert.cli.help_ip"))
    p1.add_argument("--staging", action="store_true", help=tr("ipcert.cli.help_staging"))
    p1.add_argument("--force", action="store_true", help=tr("ipcert.cli.help_force"))
    p1.set_defaults(func=cmd_issue)
    p1 = add_json_flag(sub.add_parser("tick", help=tr("ipcert.cli.help_tick")))
    p1.add_argument("--quiet", action="store_true")
    p1.set_defaults(func=cmd_tick)
    add_json_flag(sub.add_parser("remove", help=tr("ipcert.cli.help_remove"))).set_defaults(func=cmd_remove)
