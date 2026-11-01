"""``kaskadctl setup …`` — the quick setup in the browser (docs/k3/SPEC-setup.md, SPEC-setup-api.md).

    setup start [--bind-ip] [--no-cert] [--staging] [--json]
            the panel (installed, open from the internet), the certificate of Let's Encrypt on the IP when it can be
            had (else the node certificate and its fingerprint), a new link — what the installer runs on «1»
    setup link [--bind-ip | --no-bind] [--new-path] [--json]
            a new one-time link (the old token burns only when used; the path stays unless --new-path); after the
            password — the address without a token (only the password opens it)
    setup status [--json]           the state for people (no secret, no token)
    setup step <name> [--stdin] [--job ID] [--json]
            one step (docs/k3/SPEC-setup-api.md §3); --stdin — its body as one JSON object (keys, tokens, passwords
            go only this way); --job — the step of a job of the panel (the body is in its 0600 file)
    setup stage <kind> open|done|skip [--json]   the rail: open a stage, mark it done or skipped
    setup steps [--json]            the list of steps
    setup finish [--json]           the wizard closed: its address answers 404
    setup off [--json]              the same without «done» (the owner stops the wizard from SSH)

The URL with the token is printed (people copy it), never logged; ``--json`` gives {"url", "expires", …}.
"""

import argparse
import contextlib
import os
import sys

from .. import errors, i18n, sysrun, util
from ..cli import add_json_flag, emit
from ..errors import KaskadError
from ..i18n import fmt_datetime, tr
from . import jobs, link, model, product, steps


def _say(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _r():
    return sysrun.default_runner()


def _when(epoch):
    """«Oct 12, 02:40» / «12.10, 02:40» on the server's clock (kaskad.i18n.fmt_datetime: the year only when not this one)."""
    return fmt_datetime(epoch)


def ensure_panel(runner):
    """The panel installed and open from the internet (the wizard lives in it). -> its config."""
    from ..gw import sys as gsys
    from ..panel import cli as pcli, conf
    if not os.path.exists(gsys.unit_path(conf.UNIT)):
        gsys.ensure_unit(runner, conf.UNIT, pcli.unit_text())
        util.log_tr("journal.panel.unit_by_setup")
    cfg = conf.load()
    if not cfg["public"]["on"]:
        cfg = conf.set_public(runner, True)
        conf.reload_service(runner)
    return cfg


def new_link(bind=None, new_path=False, by="cli"):
    """A new link (under the lock). -> {"url", "token"?, "expires", "password": bool}."""
    out = {}

    def mut(doc):
        tok = link.issue(doc, by=by, bind=bind, new_path=new_path)
        out.update(token=tok, expires=doc["link"]["expires"], password=doc["password_set"], bind=doc["link"]["bind"])
        out["url"] = link.url(doc, tok)
    model.update(mut)
    util.log_tr("journal.setup.link_bind" if out["bind"] else "journal.setup.link", by=by)
    return out


def link_lines(doc):
    lines = []
    if not doc.get("url"):
        lines.append(tr("setup.cli.notPublic"))
        return lines
    if doc.get("password"):
        lines.append(tr("setup.cli.linkPassword"))
    else:
        lines.append(tr("setup.cli.linkOpen"))
    lines.append(doc["url"])
    lines.append(tr("setup.cli.validUntilBound" if doc.get("bind") else "setup.cli.validUntil",
                    when=_when(doc["expires"])))
    return lines


def cmd_start(args):
    runner = _r()
    from ..ipcert import ops as ipops
    from ..ipcert.http01 import Refusal
    from ..panel import conf
    cfg = ensure_panel(runner)
    cert = {"state": "skipped", "text": "", "reason": None}
    if not args.no_cert:
        if not args.json:
            _say(tr("setup.cli.certIssuing"))
        try:
            r = ipops.issue(runner, staging=args.staging, progress=None if args.json else (
                lambda i, s, d="": _say("  %s %s" % ({"ok": "✓", "wait": "…"}.get(s, "·"), d or i))))
            cert = {"state": "ok", "text": r["text"], "reason": None, "not_after": r["cert"].get("not_after")}
        except Refusal as e:
            cert = {"state": "fallback", "text": e.text, "reason": e.reason, "retry_after": e.retry_after}
        conf.reload_service(runner)
    out = new_link(bind=True if args.bind_ip else None, by="install")
    dom = conf.site_cert()
    if dom is not None:
        # the link names the domain (conf.public_host) — what covers it is the domain's certificate, not the IP one
        # (T-M6-qs330 №14: «Сертификат … на IP» was printed under a link to the domain)
        cert = dict(cert, state="domain", ip_state=cert["state"], host=dom[0],
                    text=tr("setup.cli.certDomain", host=dom[0]))
    fp = "" if cert["state"] in ("ok", "domain") else conf.fingerprint()
    doc = {"ok": True, "url": out["url"], "expires": out["expires"], "password": out["password"], "cert": cert,
           "fingerprint": fp, "port": cfg["public"]["port"], "ssh": product.ssh()}
    lines = []
    if cert["state"] == "domain":
        lines.append(cert["text"])
    elif cert["state"] == "fallback":
        lines.append(tr("setup.cli.certIpFailed", text=cert["text"]))
        lines.append(tr("setup.cli.certWarnOnce", fp=fp))
    lines += link_lines(out)
    lines.append(tr("setup.cli.openPort", port=cfg["public"]["port"]))
    emit(args, doc, lines)
    return errors.OK


def cmd_link(args):
    bind = True if args.bind_ip else (False if args.no_bind else None)
    out = new_link(bind=bind, new_path=args.new_path)
    doc = {"ok": bool(out["url"]), "url": out["url"], "expires": out["expires"], "password": out["password"],
           "bind": out["bind"]}
    emit(args, doc, link_lines(out))
    return errors.OK if out["url"] else errors.ERROR


def cmd_status(args):
    from ..ipcert import store as ipstore
    doc = model.load()
    view = model.public_view(doc)
    view.update(ok=True, url=link.url(doc) if model.active(doc) else None, path=link.secret_masked(doc),
                job=jobs.localize(jobs.status()), ipcert=ipstore.status())
    rl = view["rail"]
    lines = [tr({"off": "setup.cli.statusOff", "active": "setup.cli.statusActive",
                 "finished": "setup.cli.statusFinished"}[doc["status"]])]
    if doc["status"] == "active":
        lines.append(tr("setup.cli.stagePwSet" if doc["password_set"] else "setup.cli.stagePwUnset",
                        stage=rl["current"]))
        lines.append(tr("setup.cli.linkValid" if model.active(doc) else "setup.cli.linkExpired",
                        when=_when(doc["link"]["expires"])))
    emit(args, view, lines)
    return errors.OK


def _body(args):
    if args.job:
        return jobs.take_input(args.job)
    if not args.stdin:
        return {}
    raw = sys.stdin.read(1 << 20)
    try:
        body = util.json_in(raw) if raw.strip() else {}       # NaN, 1e400, an integer of 400 digits — no JSON (С-1)
    except ValueError:
        raise KaskadError(tr("setup.cli.stdinJson"), code=errors.USAGE)
    if not isinstance(body, dict):
        raise KaskadError(tr("setup.cli.stdinJson"), code=errors.USAGE)
    return body


def cmd_step(args):
    if args.name not in steps.STEPS:
        raise KaskadError(tr("setup.cli.noSuchStep", name=args.name), code=errors.USAGE)
    body = _body(args)
    prog = jobs.Progress(args.job, args.name, echo=None if (args.json or args.job) else _say)
    # a job of the wizard: its words keep their keys (3.3.3) — the page says them in its own language
    with (i18n.keeping() if args.job else contextlib.nullcontext()):
        out = steps.run(args.name, body, _r(), prog, by="job" if args.job else "cli")
    body = None
    prog.finish(out)
    if args.job:
        # A job of the panel (unit kaskad-setup-<id>): the result is in job.json, the wizard reads it there. A refusal
        # for people (a duplicate key, garbage, an internal address) is an answer, not a failure of the unit — code 0,
        # else each one leaves «Failed with result 'exit-code'» in the journal (T-M6 №9). Non-zero — the step crashed.
        return errors.ERROR if (out.get("error") or {}).get("code") == "internal" else errors.OK
    emit(args, out, [out.get("text") or tr("setup.cli.stepOk" if out.get("ok") else "setup.cli.stepFailed")])
    return errors.OK if out.get("ok") else errors.ERROR


def cmd_stage(args):
    def mut(doc):
        return model.set_stage(doc, args.kind, args.action)
    try:
        cur = model.update(mut)
    except ValueError as e:
        raise KaskadError(str(e), code=errors.USAGE)
    emit(args, {"ok": True, "current": cur, "rail": model.rail(model.load())}, tr("setup.cli.stageNow", stage=cur))
    return errors.OK


def cmd_steps(args):
    rows = [s.doc() for s in steps.STEPS.values()]
    long_ = " (%s)" % tr("setup.cli.stepLong")
    emit(args, {"ok": True, "steps": rows}, ["%-16s %-10s%s" % (r["name"], r["stage"], long_ if r["long"] else "")
                                             for r in rows])
    return errors.OK


def _close(status):
    def mut(doc):
        doc["status"] = status
        doc["finished_at"] = util.now_iso() if status == "finished" else doc.get("finished_at")
        link.revoke_all(doc)
    model.update(mut)
    util.log_tr("journal.setup.finished" if status == "finished" else "journal.setup.stopped")


def cmd_finish(args):
    _close("finished")
    emit(args, {"ok": True}, tr("setup.cli.finished"))
    return errors.OK


def cmd_off(args):
    _close("off")
    emit(args, {"ok": True}, tr("setup.cli.stopped"))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("setup", help=tr("setup.cli.helpSetup"))
    sub = p.add_subparsers(dest="setup_cmd", metavar=tr("setup.cli.metavarCommand"))
    sub.required = True
    p1 = add_json_flag(sub.add_parser("start", help=tr("setup.cli.helpStart")))
    p1.add_argument("--bind-ip", action="store_true", help=tr("setup.cli.helpBindIpStart"))
    p1.add_argument("--no-cert", action="store_true", help=tr("setup.cli.helpNoCert"))
    p1.add_argument("--staging", action="store_true", help=tr("setup.cli.helpStaging"))
    p1.set_defaults(func=cmd_start)
    p1 = add_json_flag(sub.add_parser("link", help=tr("setup.cli.helpLink")))
    g = p1.add_mutually_exclusive_group()
    g.add_argument("--bind-ip", action="store_true", help=tr("setup.cli.helpBindIp"))
    g.add_argument("--no-bind", action="store_true", help=tr("setup.cli.helpNoBind"))
    p1.add_argument("--new-path", action="store_true", help=tr("setup.cli.helpNewPath"))
    p1.set_defaults(func=cmd_link)
    add_json_flag(sub.add_parser("status", help=tr("setup.cli.helpStatus"))).set_defaults(func=cmd_status)
    p1 = add_json_flag(sub.add_parser("step", help=tr("setup.cli.helpStep")))
    p1.add_argument("name")
    p1.add_argument("--stdin", action="store_true", help=tr("setup.cli.helpStdin"))
    p1.add_argument("--job", default=None, help=argparse.SUPPRESS)
    p1.set_defaults(func=cmd_step)
    p1 = add_json_flag(sub.add_parser("stage", help=tr("setup.cli.helpStage")))
    p1.add_argument("kind")
    p1.add_argument("action", choices=("open", "done", "skip"))
    p1.set_defaults(func=cmd_stage)
    add_json_flag(sub.add_parser("steps", help=tr("setup.cli.helpSteps"))).set_defaults(func=cmd_steps)
    add_json_flag(sub.add_parser("finish", help=tr("setup.cli.helpFinish"))).set_defaults(func=cmd_finish)
    add_json_flag(sub.add_parser("off", help=tr("setup.cli.helpOff"))).set_defaults(func=cmd_off)
