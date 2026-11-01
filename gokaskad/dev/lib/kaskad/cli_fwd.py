"""``kaskadctl fwd ...`` — forwards on engine nft (docs/k3/SPEC-wave1.md §3.7).

    fwd list [--traffic]     proto|port|to:to_port per line (2.x get_rules_list format);
                             --traffic adds |packets|bytes
    fwd add --proto P --port N --to IP --to-port M [--name S] [--force] [--new-only]
                             tcp: :443 -> 203.0.113.5:443     (code 3 on a port conflict)
    fwd del --proto P --port N                                (code 1 if there is no such forward)
    fwd flush
    fwd rename <tcp|udp> <port> --name=<имя> [--allow-duplicate]   the name only (T13), "" — no name
    fwd stats                counters from the live table

Every command takes --json (exactly one JSON document). Engine legacy -> code 5.
The runner is taken at call time (sysrun.default_runner) so tests can replace it.
"""

import sys

from . import errors, forwards, nft, state, sysrun, util
from .cli import add_json_flag, emit
from .i18n import tr


def _warn(result):
    for w in ((result or {}).get("apply") or {}).get("warnings") or []:
        sys.stderr.write((util.core_line("warn", w) or tr("cli.warning", text=w)) + "\n")


def cmd_list(args):
    rows = forwards.list_forwards(state.load(), sysrun.default_runner(), traffic=args.traffic)
    emit(args, {"ok": True, "forwards": rows}, [forwards.legacy_line(f, args.traffic) for f in rows])
    return errors.OK


def cmd_add(args):
    res = forwards.add_forward(args.proto, args.port, args.to, args.to_port, name=args.name,
                               force=args.force, runner=sysrun.default_runner(),
                               new_only=getattr(args, "new_only", False))
    _warn(res)
    emit(args, res, forwards.describe(res["forward"]))
    return errors.OK


def cmd_del(args):
    res = forwards.delete_forward(args.proto, args.port, runner=sysrun.default_runner())
    _warn(res)
    emit(args, res, tr("cli.fwd.deleted"))
    return errors.OK


def cmd_flush(args):
    res = forwards.flush_forwards(runner=sysrun.default_runner())
    _warn(res)
    emit(args, res, tr("cli.fwd.flushed", n=res["removed"]))
    return errors.OK


def cmd_stats(args):
    st = state.load()
    forwards.require_nft(st)
    live = nft.read_live(sysrun.default_runner())
    counters = (live or {}).get("counters") or {}
    rows = []
    for f in st["forwards"]:
        c = counters.get(nft.counter_name(f["proto"], f["port"])) or {}
        rows.append({"proto": f["proto"], "port": f["port"], "to": f["to"], "to_port": f["to_port"],
                     "name": f["name"], "packets": int(c.get("packets", 0)), "bytes": int(c.get("bytes", 0))})
    lines = []
    if live is None:
        lines.append(tr("cli.fwd.stats.no_table"))
    if not rows:
        lines.append(tr("cli.fwd.stats.none"))
    else:
        lines.append("%-13s %-22s %12s %10s  %s" % tuple(tr("cli.fwd.stats.col." + c)
                                                          for c in ("forward", "to", "packets", "traffic", "name")))
        for r in rows:
            lines.append("%-13s %-22s %12d %10s  %s" % (
                "%s :%d" % (r["proto"], r["port"]), "%s:%d" % (r["to"], r["to_port"]),
                r["packets"], util.human_bytes(r["bytes"]), r["name"]))
    emit(args, {"ok": True, "table": live is not None, "stats": rows}, lines)
    return errors.OK


def cmd_rename(args):
    """``fwd rename <tcp|udp> <port> --name=<имя>`` (T13): the name only — no nft, no apply; "" — no name."""
    from .gw import rename as grename
    res = forwards.rename(args.proto, args.port, args.name, allow_duplicate=args.allow_duplicate, via=args.via)
    emit(args, res, grename.said(res))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("fwd", help=tr("cli.fwd.help.group"))
    sub = p.add_subparsers(dest="fwd_cmd", metavar=tr("cli.metavar_command"))
    sub.required = True

    pl = add_json_flag(sub.add_parser("list", help=tr("cli.fwd.help.list", fmt="proto|port|ip:port")))
    pl.add_argument("--traffic", action="store_true", help=tr("cli.fwd.help.traffic", sep="|"))
    pl.set_defaults(func=cmd_list)

    pa = add_json_flag(sub.add_parser("add", help=tr("cli.fwd.help.add")))
    pa.add_argument("--proto", required=True, help=tr("cli.fwd.help.proto"))
    pa.add_argument("--port", required=True, help=tr("cli.fwd.help.port"))
    pa.add_argument("--to", required=True, help=tr("cli.fwd.help.to"))
    pa.add_argument("--to-port", dest="to_port", required=True, help=tr("cli.fwd.help.to_port"))
    pa.add_argument("--name", default="", help=tr("cli.fwd.help.name"))
    pa.add_argument("--force", action="store_true", help=tr("cli.fwd.help.force"))
    pa.add_argument("--new-only", action="store_true", help=tr("cli.fwd.help.new_only"))
    pa.set_defaults(func=cmd_add)

    pd = add_json_flag(sub.add_parser("del", help=tr("cli.fwd.help.del")))
    pd.add_argument("--proto", required=True)
    pd.add_argument("--port", required=True)
    pd.set_defaults(func=cmd_del)

    pr = add_json_flag(sub.add_parser("rename", help=tr("cli.fwd.help.rename")))
    pr.add_argument("proto", choices=state.PROTOS)
    pr.add_argument("port")
    from .gw.cli import rename_args
    rename_args(pr)
    pr.set_defaults(func=cmd_rename)

    pf = add_json_flag(sub.add_parser("flush", help=tr("cli.fwd.help.flush")))
    pf.set_defaults(func=cmd_flush)

    ps = add_json_flag(sub.add_parser("stats", help=tr("cli.fwd.help.stats")))
    ps.set_defaults(func=cmd_stats)
