"""``kaskadctl migrate scan|plan|apply|rollback|backups`` (SPEC §4.10).

``--json`` prints exactly one JSON document; without it short Russian text. Errors go to stderr
(``kaskad.cli.main`` turns a KaskadError into its message and exit code). The runner is taken at
call time (``sysrun.default_runner()``) so tests can substitute it.
"""

import sys

from .. import cli as base
from .. import errors, sysrun, util
from ..i18n import tr
from . import apply as apply_mod
from . import plan as plan_mod
from . import rollback as rollback_mod
from . import scan as scan_mod
from . import snapshot

# the words are looked up when used: tr("migrate.edition.<edition>"), tr("migrate.status.<status>")
EDITIONS = ("free", "pro", "k3", "none")
STATUSES = ("import", "duplicate", "shadowed", "skipped")


def edition_word(ed, default=None):
    return tr("migrate.edition." + ed) if ed in EDITIONS else (ed if default is None else default)


def status_word(st):
    return tr("migrate.status." + st) if st in STATUSES else st


def register(sub):
    p = sub.add_parser("migrate", help=tr("migrate.help.migrate"))
    msub = p.add_subparsers(dest="migrate_cmd")

    s = base.add_json_flag(msub.add_parser("scan", help=tr("migrate.help.scan")))
    s.set_defaults(func=cmd_scan)

    pl = base.add_json_flag(msub.add_parser("plan", help=tr("migrate.help.plan")))
    pl.add_argument("--force", action="store_true", help=tr("migrate.help.force"))
    pl.set_defaults(func=cmd_plan)

    a = base.add_json_flag(msub.add_parser("apply", help=tr("migrate.help.apply")))
    a.add_argument("--yes", action="store_true", help=tr("migrate.help.yes"))
    a.add_argument("--force", action="store_true", help=tr("migrate.help.force"))
    a.set_defaults(func=cmd_apply)

    r = base.add_json_flag(msub.add_parser("rollback", help=tr("migrate.help.rollback")))
    r.add_argument("--to-legacy", action="store_true", help=tr("migrate.help.to_legacy"))
    r.add_argument("--yes", action="store_true", help=tr("migrate.help.yes"))
    r.set_defaults(func=cmd_rollback)

    b = base.add_json_flag(msub.add_parser("backups", help=tr("migrate.help.backups")))
    b.set_defaults(func=cmd_backups)

    def _help(args):
        p.print_help(sys.stderr)
        return errors.USAGE
    p.set_defaults(func=_help)


def _runner():
    return sysrun.default_runner()


def _scan_text(inv, pl):
    rep = inv.report()
    L = [tr("migrate.scan.installed", edition=edition_word(inv.edition),
            version=(" " + inv.version) if inv.version else "",
            engine="nftables" if inv.engine == "nft" else "iptables")]
    if inv.mixed:
        L.append(tr("migrate.scan.iptables_mixed", backend=rep["iptables"]["backend"]))
    else:
        L.append("iptables: %s" % rep["iptables"]["backend"])
    if inv.ipt_error:
        L.append(tr("migrate.scan.ipt_error", error=inv.ipt_error))
    sup = inv.support or {}
    L.append(tr("migrate.scan.nft_ok") if sup.get("supported")
             else tr("migrate.scan.nft_no", reason=sup.get("reason") or "?"))
    if inv.cands:
        L.append(tr("migrate.scan.rules_head"))
        for c in inv.cands:
            L.append("  %s :%d → %s  %s, %s" % (c.proto, c.port, ("%s:%d" % (c.to, c.to_port)) if c.to
                                               else c.rule.to_destination, plan_mod.origin_word(c.cls),
                                               status_word(c.status)))
    else:
        L.append(tr("migrate.scan.no_rules"))
    for cf in inv.conflicts:
        L.append(tr("migrate.scan.conflict", what=cf["key"] or cf["kind"], detail=cf["detail"]))
    for w in inv.warnings:
        L.append(util.core_line("warn", w) or tr("migrate.attention", text=w))
    sm = pl.summary()
    L.append(tr("migrate.scan.total_blocked" if sm["blocked"] else "migrate.scan.total",
                n=sm["import"], removed=sm["remove_rules"]))
    return L


def cmd_scan(args):
    inv = scan_mod.collect(_runner())
    pl = plan_mod.build(inv)
    rep = inv.report()
    rep["summary"] = pl.summary()
    base.emit(args, rep, _scan_text(inv, pl))
    return errors.OK


def cmd_plan(args):
    inv = scan_mod.collect(_runner())
    pl = plan_mod.build(inv, force=args.force)
    base.emit(args, pl.to_dict(), pl.preview())
    return errors.OK


def cmd_apply(args):
    if not args.yes:
        sys.stderr.write(tr("migrate.cli.need_yes") + "\n")
        if args.json:
            base.emit(args, {"ok": False, "error": tr("migrate.cli.need_yes_short"), "code": errors.USAGE})
        return errors.USAGE
    res = apply_mod.run(_runner(), force=args.force)
    if res["result"] == "nothing":
        text = [tr("migrate.nothing")]
    else:
        n = len(res.get("imported") or [])
        text = [tr("migrate.cli.done_switch" if res.get("mode") == "switch" else "migrate.cli.done_adopt"),
                tr("migrate.cli.moved", forwards=plan_mod.forwards_word(n), removed=res.get("removed", 0)),
                tr("migrate.backup_at", path=res.get("backup"))]
    if res.get("healed"):
        text.insert(0, res["healed"])
    text += [util.core_line("warn", w) or tr("migrate.attention", text=w) for w in res.get("warnings") or []]
    base.emit(args, res, text)
    return errors.OK


def cmd_rollback(args):
    if not args.to_legacy or not args.yes:
        sys.stderr.write(tr("migrate.cli.need_legacy") + "\n")
        if args.json:
            base.emit(args, {"ok": False, "error": tr("migrate.cli.need_legacy_short"), "code": errors.USAGE})
        return errors.USAGE
    res = rollback_mod.run(_runner())
    if res["result"] == "nothing":
        text = res["message"]
    else:
        text = [tr("migrate.cli.back_in_iptables", forwards=plan_mod.forwards_word(res["forwards"])),
                tr("migrate.backup_at", path=res["backup"])]
        text += [util.core_line("warn", w) or tr("migrate.attention", text=w) for w in res.get("warnings", [])]
        text += [tr("migrate.cli.failed", error=e) for e in res.get("problems", [])]
    base.emit(args, res, text)
    return errors.OK if res.get("ok") else errors.ERROR


def cmd_backups(args):
    items = snapshot.listing()
    if not items:
        text = tr("migrate.cli.no_backups")
    else:
        text = []
        for i in items:
            fr = i.get("from") or {}
            src = " ".join(x for x in (edition_word(fr.get("edition"), fr.get("edition") or ""),
                                       fr.get("version") or "") if x)
            reason = i.get("reason")
            word = tr("migrate.cli.before_" + reason) if reason in ("migrate", "rollback") else (reason or "")
            text.append("%s  %s: %s → %s" % (i["path"], word,
                                              src, i.get("to") or ""))
    base.emit(args, {"ok": True, "backups": items}, text)
    return errors.OK
