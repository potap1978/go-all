"""kaskadctl tz — the time zone of the node (stage 1, SPEC-stage1-exits §3.7, §7.4, §7.7; T10).

    tz get [--json]            {"tz", "os_tz", "now", "offset", "ntp", "warn"?, "ver"} (the list of zones — in the panel)
    tz set <IANA> [--json]     the zone of the node (KASKAD_TZ): Europe/Moscow, UTC, Europe/Berlin …
The panel (GET/PUT /api/settings/time), MCP (tz_get, tz_set) and the SSH menu call the same gw.policy_ops.tz_get/tz_set.
"""

from . import errors
from .i18n import tr


def _get(args):
    from .cli import emit
    from .gw import policy_ops as pops
    doc = pops.tz_get()
    out = dict((k, v) for k, v in doc.items() if k != "zones")      # ≈ 400 names: the panel's list, not here
    out["ok"] = True
    lines = [tr("gw_cli.tz.now", tz=doc["tz"], os=doc.get("os_tz") or "—")]
    if doc.get("warn"):
        lines.append(str(doc["warn"]))
    emit(args, out, lines)
    return errors.OK


def _set(args):
    from .cli import emit
    from .gw import policy_ops as pops
    doc = pops.tz_set(args.name)
    emit(args, {"ok": True, "tz": doc["tz"], "ver": doc.get("ver")}, tr("gw_cli.tz.set", tz=doc["tz"]))
    return errors.OK


def register(sub):
    from .cli import add_json_flag
    p = sub.add_parser("tz", help=tr("gw_cli.help.tz"))
    s = p.add_subparsers(dest="tz_cmd", metavar=tr("cli.metavar_command"))
    s.required = True
    add_json_flag(s.add_parser("get", help=tr("gw_cli.help.tz_get"))).set_defaults(func=_get)
    p1 = add_json_flag(s.add_parser("set", help=tr("gw_cli.help.tz_set")))
    p1.add_argument("name", metavar="IANA")
    p1.set_defaults(func=_set)
