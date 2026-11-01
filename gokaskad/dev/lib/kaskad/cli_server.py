"""``kaskadctl server ...`` — the monitored servers of /etc/kaskad/aliases (T13, SPEC-stage1-rename §4.2).

    server rename <ip> --name=<имя> [--allow-duplicate] [--json]   the name of the server and of the targets of its
                                                                    forwards (one name per IP); "" — no name (the IP)

The note, the country and the provider of the server stay. Exit codes: 0 done, 2 the name did not pass the check or a
bad address, 3 a duplicate without --allow-duplicate.
"""

from . import errors
from .bot import aliases
from .cli import add_json_flag, emit
from .i18n import tr


def cmd_rename(args):
    from .gw import rename as grename
    res = aliases.rename(args.ip, args.name, allow_duplicate=args.allow_duplicate, via=args.via)
    emit(args, res, grename.said(res))
    return errors.OK


def register(subparsers):
    from .gw.cli import rename_args
    p = subparsers.add_parser("server", help=tr("cli.server.help.group"))
    sub = p.add_subparsers(dest="server_cmd", metavar=tr("cli.metavar_command"))
    sub.required = True
    pr = add_json_flag(sub.add_parser("rename", help=tr("cli.server.help.rename")))
    pr.add_argument("ip")
    rename_args(pr)
    pr.set_defaults(func=cmd_rename)
