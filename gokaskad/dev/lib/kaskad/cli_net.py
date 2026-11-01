"""``kaskadctl net ...`` — the traffic engine itself (docs/k3/SPEC-wave1.md §3.7).

    net status                   engine, support, table, compat rule, services, nft version
    net probe                    nft.probe_support (code 5 if unsupported)
    net apply [--dry-run]        apply state to the table; --dry-run prints the script
    net render [--full]          the update script, or the full table text
    net restore                  like at boot: nft -f /etc/kaskad/net/kaskad.nft
    net check [--repair] [--quiet]   the watchdog (netcheck.py); code 1 if problems remain
    net save-counters [--quiet]  counters -> kaskad.nft (no-op on engine legacy)
    net install-units / remove-units
    net teardown                 table, compat rule and units away (rollback/uninstall); when the
                                 gateway is on — its services, ip rules, kgw0 and compat-in too

Every command takes --json (exactly one JSON document). Changes run under state.locked().
"""

import sys

from . import compat, errors, netcheck, nft, paths, state, sysrun, units, util
from .cli import add_json_flag, emit
from .errors import KaskadError, LockedError, UnsupportedError
from .i18n import tr

QUIET_LOCK_TIMEOUT = 5.0


def _need_nft(st):
    if st.get("engine") != "nft":
        raise UnsupportedError(tr("cli.net.need_nft"))


def _unit_text(s):
    return "%s, %s" % (tr("cli.net.unit.enabled") if s["enabled"] else tr("cli.net.unit.disabled"),
                       tr("cli.net.unit.active") if s["active"] else tr("cli.net.unit.inactive"))


# ── status / probe ─────────────────────────────────────────────────────────

def cmd_status(args):
    runner = sysrun.default_runner()
    try:
        st = state.load()
        engine, st_error = st["engine"], ""
    except errors.StateError as e:
        st, engine, st_error = None, "", str(e)
    sup = nft.probe_support(runner)
    table = {"present": False, "forwards": 0, "in_sync": False, "error": ""}
    try:
        live = nft.read_live(runner) if sup["nft"] else None
    except KaskadError as e:
        live, table["error"] = None, str(e)
    if live is not None:
        table["present"] = True
        table["forwards"] = len(live["forwards"])
        table["in_sync"] = bool(st) and not nft.verify(st, live)
    comp = {"available": compat.available(runner), "present": False}
    if comp["available"]:
        comp["present"] = compat.present(runner)
    services = units.status(runner)
    doc = {"ok": True, "engine": engine, "state_error": st_error, "support": sup, "table": table,
           "compat": comp, "services": services, "nft": sup["nft"]}

    eng = {"nft": "nftables", "legacy": tr("cli.net.engine_legacy")}.get(engine)
    lines = [tr("cli.net.status.engine", engine=eng or tr("cli.net.status.engine_unknown", error=st_error))]
    lines.append(tr("cli.net.status.support", value=tr("cli.yes") if sup["supported"]
                    else tr("cli.net.status.no_reason", reason=sup["reason"])))
    lines.append(tr("cli.net.status.os", os=sup["os"]["pretty"] or tr("cli.unknown")))
    lines.append("nft: %s" % (sup["nft"] or tr("cli.no")))
    lines.append(tr("cli.net.status.kernel", kernel=sup["kernel"] or "?"))
    lines.append("iptables: %s" % sup["backend"])
    if table["present"]:
        lines.append(tr("cli.net.status.table_present", n=table["forwards"], sync=tr(
            "cli.net.status.in_sync" if table["in_sync"] else "cli.net.status.not_in_sync")))
    else:
        lines.append(tr("cli.net.status.table_absent", error=" (%s)" % table["error"] if table["error"] else ""))
    if not comp["available"]:
        lines.append(tr("cli.net.status.compat_not_needed"))
    else:
        lines.append(tr("cli.net.status.compat", value=tr("cli.net.status.present") if comp["present"] else tr("cli.no")))
    for name in units.UNITS:
        if name == units.WATCH_SERVICE:
            # oneshot started by the timer for a second a minute: «включена, не работает» misled
            lines.append("%s: %s" % (name, tr("cli.net.unit.by_timer") if services[name]["enabled"]
                                     else tr("cli.net.unit.not_installed")))
        else:
            lines.append("%s: %s" % (name, _unit_text(services[name])))
    emit(args, doc, lines)
    return errors.OK


def cmd_probe(args):
    sup = nft.probe_support(sysrun.default_runner())
    if sup["supported"]:
        text = tr("cli.net.probe.ok", nft=sup["nft"], kernel=sup["kernel"], os=sup["os"]["pretty"])
    else:
        text = tr("cli.net.probe.no", reason=sup["reason"])
    emit(args, sup, text)
    return errors.OK if sup["supported"] else errors.UNSUPPORTED


# ── apply / render / restore ───────────────────────────────────────────────

def cmd_apply(args):
    runner = sysrun.default_runner()
    if args.dry_run:
        st = state.load()
        _need_nft(st)
        script = nft.render_update(st, nft.read_live(runner))
        emit(args, {"ok": True, "dry_run": True, "script": script}, script)
        return errors.OK
    with state.locked():
        st = state.load()
        _need_nft(st)
        res = nft.apply(st, runner)
    for w in res.get("warnings") or []:
        sys.stderr.write((util.core_line("warn", w) or tr("cli.warning", text=w)) + "\n")
    text = tr("cli.net.applied", added=len(res["added"]), removed=len(res["removed"]), changed=len(res["changed"]))
    emit(args, res, text)
    return errors.OK


def cmd_render(args):
    runner = sysrun.default_runner()
    st = state.load()
    if args.full:
        try:
            live = nft.read_live(runner)
        except KaskadError:
            live = None
        counters = live["counters"] if live is not None else nft.saved_counters()
        script = nft.render_full(st, counters)
    else:
        script = nft.render_update(st, nft.read_live(runner))
    emit(args, {"ok": True, "full": bool(args.full), "script": script}, script)
    return errors.OK


def cmd_restore(args):
    runner = sysrun.default_runner()
    with state.locked():
        st = state.load()
        _need_nft(st)
        if not nft.restore(runner):
            raise KaskadError(tr("cli.net.restore_failed", file=paths.nft_file()))
        util.log_tr("cli.net.log.restore", file=paths.nft_file())
        diffs = nft.verify(st, nft.read_live(runner))
    lines = [tr("cli.net.restored", file=paths.nft_file())]
    if diffs:
        lines.append(tr("cli.net.restore_diffs", diffs="; ".join(diffs[:5])))
    emit(args, {"ok": True, "in_sync": not diffs, "diffs": diffs}, lines)
    return errors.OK


# ── watchdog ───────────────────────────────────────────────────────────────

def cmd_check(args):
    res = netcheck.check(sysrun.default_runner(), repair=args.repair)
    if args.json:
        emit(args, res)
    elif args.quiet:
        for p in res["problems"]:
            if not p["repaired"] and args.repair:
                sys.stderr.write(tr("cli.net.check.not_repaired_line", detail=p["detail"],
                                    error=(" (%s)" % p["error"]) if p.get("error") else "") + "\n")
    else:
        if res["skipped"]:
            lines = [tr("cli.net.check.skipped")]
        elif res["engine"] != "nft" and not res["problems"]:
            lines = [tr("cli.net.check.legacy")]
        elif not res["problems"]:
            lines = [tr("cli.net.check.ok")]
        else:
            lines = [tr("cli.net.check.problems")]
            for p in res["problems"]:
                mark = ""
                if args.repair:
                    mark = tr("cli.net.check.repaired") if p["repaired"] else tr(
                        "cli.net.check.not_repaired", error=(": %s" % p["error"]) if p.get("error") else "")
                lines.append("  • %s%s" % (p["detail"], mark))
        emit(args, res, lines)
    return errors.OK if res["ok"] else errors.ERROR


def cmd_save_counters(args):
    runner = sysrun.default_runner()
    try:
        with state.locked(timeout=QUIET_LOCK_TIMEOUT if args.quiet else 15.0):
            st = state.load()
            if st["engine"] != "nft":
                emit(args, {"ok": True, "saved": 0, "skipped": "engine"},
                     None if args.quiet else tr("cli.net.counters_legacy"))
                return errors.OK
            counters = nft.save_counters(runner)
    except LockedError:
        if not args.quiet:
            raise
        emit(args, {"ok": True, "saved": 0, "skipped": "locked"})
        return errors.OK
    emit(args, {"ok": True, "saved": len(counters), "counters": counters},
         None if args.quiet else tr("cli.net.counters_saved", n=len(counters)))
    return errors.OK


# ── units / teardown ───────────────────────────────────────────────────────

def cmd_install_units(args):
    runner = sysrun.default_runner()
    res = units.install(runner)
    res["started"] = units.activate(runner)
    emit(args, dict(res, ok=True), tr("cli.net.units_installed", net=units.NET_UNIT, timer=units.WATCH_TIMER))
    return errors.OK


def cmd_remove_units(args):
    res = units.remove(sysrun.default_runner())
    emit(args, dict(res, ok=True), tr("cli.net.units_removed"))
    return errors.OK


def _gateway_teardown(runner):
    """The gateway (wave 2) goes down with the engine: services, ip rules, kgw0, compat-in. State stays."""
    try:
        enabled = bool((state.load().get("gateway") or {}).get("enabled"))
    except errors.StateError:
        enabled = False
    if not enabled:
        return False
    from .gw import ops
    return ops.teardown(runner)


def cmd_teardown(args):
    runner = sysrun.default_runner()
    with state.locked():
        _gateway_teardown(runner)
        table = nft.delete_table(runner)
        rules = compat.remove(runner)
        res = units.remove(runner)
    with util.journal_lang():
        util.log_action(tr("cli.net.log.teardown",
                           table=tr("cli.net.log.table_deleted" if table else "cli.net.log.table_none"),
                           rules=rules, units=len(res["removed"])))
    emit(args, {"ok": True, "table_deleted": table, "compat_removed": rules, "units_removed": res["removed"]},
         tr("cli.net.teardown_done"))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("net", help=tr("cli.net.help.group"))
    sub = p.add_subparsers(dest="net_cmd", metavar=tr("cli.metavar_command"))
    sub.required = True

    add_json_flag(sub.add_parser("status", help=tr("cli.net.help.status"))).set_defaults(func=cmd_status)
    add_json_flag(sub.add_parser("probe", help=tr("cli.net.help.probe"))).set_defaults(func=cmd_probe)

    pa = add_json_flag(sub.add_parser("apply", help=tr("cli.net.help.apply")))
    pa.add_argument("--dry-run", action="store_true", help=tr("cli.net.help.dry_run"))
    pa.set_defaults(func=cmd_apply)

    pr = add_json_flag(sub.add_parser("render", help=tr("cli.net.help.render")))
    pr.add_argument("--full", action="store_true", help=tr("cli.net.help.full"))
    pr.set_defaults(func=cmd_render)

    add_json_flag(sub.add_parser("restore", help=tr("cli.net.help.restore"))).set_defaults(func=cmd_restore)

    pc = add_json_flag(sub.add_parser("check", help=tr("cli.net.help.check")))
    pc.add_argument("--repair", action="store_true", help=tr("cli.net.help.repair"))
    pc.add_argument("--quiet", action="store_true", help=tr("cli.net.help.quiet"))
    pc.set_defaults(func=cmd_check)

    ps = add_json_flag(sub.add_parser("save-counters", help=tr("cli.net.help.save_counters")))
    ps.add_argument("--quiet", action="store_true")
    ps.set_defaults(func=cmd_save_counters)

    add_json_flag(sub.add_parser("install-units", help=tr("cli.net.help.install_units"))).set_defaults(func=cmd_install_units)
    add_json_flag(sub.add_parser("remove-units", help=tr("cli.net.help.remove_units"))).set_defaults(func=cmd_remove_units)
    add_json_flag(sub.add_parser("teardown", help=tr("cli.net.help.teardown"))).set_defaults(
        func=cmd_teardown)
