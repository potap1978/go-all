"""kaskadctl — command line of Kaskad 3 (``python3 -m kaskad``).

Sub-command groups live in their own modules and register themselves here, so independent
work packages never edit the same file:

    kaskad.cli_fwd      kaskadctl fwd ...       (work package 1)
    kaskad.cli_net      kaskadctl net ...       (work package 1)
    kaskad.migrate.cli  kaskadctl migrate ...   (work package 2)
    kaskad.gw.cli       kaskadctl gw ...        (work package 4, the gateway)
    kaskad.rules.cli    kaskadctl rules ...     (wave 3, WP-R1)
    kaskad.panel.cli    kaskadctl panel ...     (wave 3, WP-P1)
    kaskad.bot.cli      kaskadctl bot ...       (wave 3, WP-B)
    kaskad.site.cli     kaskadctl site ...      (wave 4, 4.0-B: domain, DNS, certificate)
    kaskad.cli_update   kaskadctl update ...    (a new version the way menu 11 does it; for the MCP server)
    kaskad.mcp.keys     kaskadctl mcp key ...   (SSH keys of AI agents that run only kaskad-mcp)
    kaskad.summary      kaskadctl summary       (the node at a glance: the header of the SSH menu, «Что не так»)
    kaskad.cli_backup   kaskadctl backup ...    (a copy of the settings in one file: export, import, undo)
    kaskad.i18n.cli     kaskadctl lang ...      (3.3.2: the language of the server, get | set ru|en)
    kaskad.host_cli     kaskadctl host ...      (the light health of the server: health | set | probes-hint)
    kaskad.tz_cli       kaskadctl tz ...        (stage 1: the time zone of the node, get | set <IANA>)
    kaskad.cli_server   kaskadctl server ...    (T13: the name of a monitored server — server rename)

Each module exposes ``register(subparsers)``; every leaf parser sets ``func=handler`` where
``handler(args) -> int`` returns an exit code from ``kaskad.errors``. Output conventions:
``--json`` prints exactly one JSON document to stdout; otherwise short Russian text for people.
Errors go to stderr. ``KaskadError`` raised from a handler is turned into its message + code here.
"""

import argparse
import importlib
import json
import sys

from . import __version__, errors, util
from .i18n import tr

GROUPS = ("kaskad.cli_fwd", "kaskad.cli_net", "kaskad.migrate.cli", "kaskad.gw.cli",
          "kaskad.rules.cli", "kaskad.panel.cli", "kaskad.panel.userpage_cli",
          "kaskad.bot.cli", "kaskad.site.cli", "kaskad.cli_update", "kaskad.mcp.keys", "kaskad.summary", "kaskad.cli_backup",
          "kaskad.ipcert.cli", "kaskad.setup.cli", "kaskad.i18n.cli", "kaskad.host_cli", "kaskad.tz_cli", "kaskad.cli_server")                                               # wave 3: may not exist yet


def safe_stdout(stream=None):
    """stdout of kaskadctl (SEC-3371 П-1): a text that is not UTF-8 (an old name read with ``surrogateescape``) is
    printed as «?» — never a raw byte 0x80–0x9F (C1 of an 8-bit terminal: CSI, OSC) under the locale C, never a
    UnicodeEncodeError under UTF-8. The menu's filters (k3_term_filter, k3_e) cut only C1 in UTF-8 (C2 80–9F): a lone
    byte of that range is a continuation of UTF-8 to sed and cannot be told apart cheaply there."""
    s = sys.stdout if stream is None else stream
    try:
        s.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):           # not a TextIOWrapper (a test's StringIO)
        pass


def emit(args, obj, text=None):
    """Print the result: JSON with --json, otherwise ``text`` (str or list of lines)."""
    if getattr(args, "json", False):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    elif text is not None:
        if isinstance(text, (list, tuple)):
            text = "\n".join(text)
        if text:
            sys.stdout.write(text.rstrip("\n") + "\n")


def add_json_flag(p):
    p.add_argument("--json", action="store_true", help=tr("cli.json_help"))
    return p


def _version(args):
    emit(args, {"version": __version__}, "Kaskad %s" % __version__)
    return errors.OK


def build_parser():
    parser = argparse.ArgumentParser(prog="kaskadctl", description="Kaskad 3")
    sub = parser.add_subparsers(dest="group")
    pv = add_json_flag(sub.add_parser("version", help=tr("cli.version_help")))
    pv.set_defaults(func=_version)
    for modname in GROUPS:
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            # a group that is not written yet must not break the others; a broken import
            # INSIDE an existing group module is a real error and must surface
            if e.name not in (modname, modname.rsplit(".", 1)[0]):
                raise
            continue
        mod.register(sub)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return errors.USAGE
    try:
        rc = func(args)
        return errors.OK if rc is None else int(rc)
    except errors.KaskadError as e:
        if getattr(args, "json", False):
            emit(args, {"ok": False, "error": str(e), "code": e.code, "data": e.data})
        else:
            sys.stderr.write((util.core_line("error", str(e)) or tr("cli.error", error=e)) + "\n")
        return e.code
    except KeyboardInterrupt:
        sys.stderr.write(tr("cli.interrupted") + "\n")
        return errors.ERROR
