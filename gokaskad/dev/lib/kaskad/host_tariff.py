"""kaskadctl host tariff — the tariff of the server, only what the health and the price of the schedule take
(k3/handoff/SPEC-node-score.md NS-D34, решение владельца 23.10): the port speed — the health rule «port» (C7), the
traffic a month — «% of the tariff traffic» in the price of the checks (B10). The BINDING of the core
``bench/tariff.py`` (process/BLOCKS.md): state.json, its lock, the queue of the checks, the texts.

    host tariff [show] [--json]   the port and the traffic with their source (typed in / a catalog of the hoster).
                                  ``--json``: {"ok", "tariff": bench.tariff.view + the texts, "dropped"}
    host tariff set [--port MBIT] [--traffic GB|unlimited] [--clear port,traffic|all] [--dry] [--code C] [--json]
                                  a change under the state lock; "" clears a field. ``--dry`` — nothing is
                                  written: the change and its ``code``. ``--by-mcp`` (MCP ``host_tariff_set``) —
                                  only with ``--code`` of ``--dry`` for exactly this change over exactly the stored
                                  values (the confirmation on the server, as T10 ``pin_tofu`` / B7 the price).
                                  The port typed in or changed — a new base of the checks (NS-S17):
                                  ``epoch-<ts>-tariff.json`` in the queue of the checks.

The panel (``panel/host.py``): GET /api/host/tariff = ``show_doc``; POST /api/host/tariff = ``apply(panel=True)``:
``rev`` — the version of the GET is REQUIRED for a write (none — 428 ``rev_required``, as ``If-Match`` of B13; another
window changed it — 409 ``rev_changed``; not 10 hex signs — 400 ``bad_rev``); the CLI of a person writes without it,
the agent (MCP) — by the ``code`` of ``--dry`` (REV-ns-c1 З-3).

The tariff is written with the ``mid`` of the machine (``bench.machine.mid``): the restore of a copy of another machine
drops it (``backup._tariff_of_this_machine``, REV-ns-c1 З-1).
"""

import argparse
import hmac
import re
import time

from . import errors, state, util
from .bench import tariff as T
from .errors import KaskadError
from .i18n import tr

FIELD_FLAGS = (("port_mbit", "--port", "MBIT"), ("traffic_gb", "--traffic", "GB|unlimited"))
_HEX = re.compile(r"[0-9a-f]{%d}" % T._CODE_LEN)        # a rev / a code: compared only in this shape (REV-ns-c1 П-1)


class TariffError(KaskadError):
    """A refusal with its word (``key``) and HTTP status for the panel (``routes.core_error``)."""

    def __init__(self, message, key, status=400, data=None, code=errors.USAGE):
        KaskadError.__init__(self, message, code=code, data=data)
        self.key = key
        self.status = status


def say(m, lang=None):
    from .gw import hostmon
    return hostmon.say(m, lang)


# ── the texts ──────────────────────────────────────────────────────────────

def value_text(field, v, lang=None):
    """A value as words: «1 Гбит/с», «500 Мбит/с», «1000 ГБ», «без лимита»."""
    from .gw import hostmon
    if v is None:
        return tr("tariff.none")
    if field == "port_mbit":
        return say(hostmon.port_text(v), lang)
    if field == "traffic_gb":
        return say(T.msg("tariff.value.unlimited") if v == T.UNLIMITED else T.msg("tariff.value.traffic", gb=v), lang)
    return str(v)


def _said_view(view, lang=None):
    out = dict(view, fields={})
    for f, x in view["fields"].items():
        out["fields"][f] = dict(x, label=tr("tariff.field." + f),
                                text=value_text(f, x["v"], lang) if x["v"] is not None else None,
                                src_text=tr("tariff.src." + x["src"]) if x["src"] else None)
    return out


def _errors_data(errs):
    return [{"field": e["field"], "code": e["code"], "text": say(e["msg"])} for e in errs]


def _errors_text(errs):
    lines = []
    for e in errs:
        label = tr("tariff.field." + e["field"]) if e["field"] in T.SPEC else tr("tariff.field.unknown")
        lines.append(tr("tariff.err.line", field=label, text=say(e["msg"])))
    return "\n".join(lines)


# ── read and write ─────────────────────────────────────────────────────────

def _stored(st):
    from .gw import model
    return model.tariff(st)


def show_doc(st=None):
    """The answer of ``host tariff --json`` and GET /api/host/tariff."""
    st = state.load() if st is None else st
    doc, dropped = _stored(st)
    return {"ok": True, "tariff": _said_view(T.view(doc)), "dropped": dropped}


def _change_lines(old, new):
    out = []
    for f in T.changed(old, new):
        a, b = (old.get(f) or {}).get("v"), (new.get(f) or {}).get("v")
        out.append(tr("tariff.change", field=tr("tariff.field." + f), old=value_text(f, a), new=value_text(f, b)))
    return out


def _hex(x):
    return isinstance(x, str) and _HEX.fullmatch(x) is not None


def apply(values=None, clear=(), dry=False, code=None, need_code=False, rev=None, now=None, panel=False):
    """A change of a person -> the answer of ``host tariff set --json`` / POST /api/host/tariff: {"ok", "dry",
    "changed", "lines", "code", "notes", "warnings", "tariff"}. Every value is checked before anything is
    written; ``need_code`` — the agent: the ``code`` of ``dry`` for this very change is required; ``rev`` — the
    panel: the version it showed; ``panel`` — a write needs ``rev`` (REV-ns-c1 З-3) and the texts speak of the form,
    not of the flags of the CLI."""
    now = int(time.time() if now is None else now)
    if not values and not clear:
        raise TariffError(tr("tariff.err.nothing_panel" if panel else "tariff.err.nothing"), "nothing")
    if rev is not None and not _hex(rev):
        raise TariffError(tr("tariff.err.bad_rev"), "bad_rev")
    if dry:
        st = state.load()
        old = _stored(st)[0]["vals"]
        new, errs = T.merge(old, values, clear)
        if errs:
            raise TariffError(_errors_text(errs), "bad_value", data={"errors": _errors_data(errs)})
        ch = T.changed(old, new)
        doc = {"v": T.V, "at": now, "vals": new}
        return {"ok": True, "dry": True, "changed": ch, "lines": _change_lines(old, new), "code": T.code(old, new),
                "notes": [tr("tariff.note.dry" if ch else "tariff.note.same")], "warnings": [],
                "tariff": _said_view(T.view(doc))}
    with state.locked():
        st = state.load()
        old_doc = _stored(st)[0]
        old = old_doc["vals"]
        if panel and rev is None:
            raise TariffError(tr("tariff.err.rev_needed"), "rev_required", status=428, code=errors.CONFLICT)
        if rev is not None and not hmac.compare_digest(rev, T.rev(old)):
            raise TariffError(tr("tariff.err.rev"), "rev_changed", status=409, code=errors.CONFLICT)
        new, errs = T.merge(old, values, clear)
        if errs:
            raise TariffError(_errors_text(errs), "bad_value", data={"errors": _errors_data(errs)})
        if need_code:
            if not code:
                raise TariffError(tr("tariff.err.code_needed"), "code_needed")
            if not _hex(code) or not hmac.compare_digest(code, T.code(old, new)):
                raise TariffError(tr("tariff.err.code_mismatch"), "code_mismatch", status=409,
                                  code=errors.CONFLICT)
        ch = T.changed(old, new)
        if ch:
            g = st.get("gateway")
            if not isinstance(g, dict):
                g = st["gateway"] = {}
            hm = dict(g["hostmon"]) if isinstance(g.get("hostmon"), dict) else {}
            if new:
                from .bench import machine
                hm["tariff"] = {"v": T.V, "at": now, "vals": new}
                mid = machine.mid()
                if mid:
                    hm["tariff"]["mid"] = mid             # whose tariff it is: a copy of another machine drops it
            else:
                hm.pop("tariff", None)
            g["hostmon"] = hm
            state.save(st)
    notes, warnings = [], []
    lines = _change_lines(old, new)
    if not ch:
        notes.append(tr("tariff.note.same"))
    else:
        notes.append(tr("tariff.note.saved"))
        ep = T.epoch_args(old, new)
        if ep is not None:
            try:
                from .bench import spool
                spool.put_epoch(now, ep)
                notes.append(tr("tariff.note.new_base"))
            except (OSError, ValueError) as e:
                warnings.append(tr("tariff.warn.epoch", err=type(e).__name__))
                util.log_tr("tariff.log.epoch", err=type(e).__name__)
        if "port_mbit" in ch:
            notes.append(tr("tariff.note.port_rule" if new.get("port_mbit") else "tariff.note.port_rule_off"))
    doc = {"v": T.V, "at": now if ch else old_doc["at"], "vals": new}
    return {"ok": True, "dry": False, "changed": ch, "lines": lines, "code": None, "notes": notes,
            "warnings": warnings, "tariff": _said_view(T.view(doc))}


# ── kaskadctl ──────────────────────────────────────────────────────────────

def show_text(doc):
    v = doc["tariff"]
    lines = [tr("tariff.title")]
    for f in T.FIELDS:
        x = v["fields"][f]
        if x["v"] is not None:
            lines.append(tr("tariff.line.value", field=x["label"], value=x["text"], src=x["src_text"]))
        else:
            lines.append(tr("tariff.line.empty", field=x["label"]))
    if doc.get("dropped"):
        lines.append(tr("tariff.line.dropped", fields=", ".join(
            tr("tariff.field." + f) if f in T.SPEC else tr("tariff.field.unknown") for f in doc["dropped"])))
    return lines


def cmd_show(args):
    from .cli import emit
    doc = show_doc()
    emit(args, doc, show_text(doc))
    return errors.OK


ALIASES = dict((flag[2:], f) for f, flag, _mv in FIELD_FLAGS)     # --clear port,traffic: the names of the flags too


def _clear_list(text):
    if text is None:
        return []
    out = [x.strip().lower() for x in str(text).split(",") if x.strip()][:len(T.FIELDS) + 1]
    return [ALIASES.get(x, x) for x in out]


def cmd_set(args):
    from .cli import emit
    values = {}
    for f, flag, _mv in FIELD_FLAGS:
        x = getattr(args, f)
        if x is not None:
            values[f] = x
    if args.by_mcp and not args.dry and not args.code:
        raise TariffError(tr("tariff.err.code_needed"), "code_needed")
    doc = apply(values, _clear_list(args.clear), dry=args.dry, code=args.code, need_code=args.by_mcp)
    text = list(doc["lines"]) + doc["warnings"] + doc["notes"]
    if doc["dry"] and doc["changed"]:
        text.append(tr("tariff.note.code", code=doc["code"]))
    emit(args, doc, text)
    return errors.OK


def register(s, add_json_flag):
    """``host tariff`` under ``kaskadctl host`` (host_cli.register)."""
    p = s.add_parser("tariff", help=tr("tariff.help"))
    add_json_flag(p)
    p.set_defaults(func=cmd_show)
    ts = p.add_subparsers(dest="tariff_cmd")
    add_json_flag(ts.add_parser("show", help=tr("tariff.help.show"))).set_defaults(func=cmd_show)
    sp = add_json_flag(ts.add_parser("set", help=tr("tariff.help.set")))
    for f, flag, mv in FIELD_FLAGS:
        sp.add_argument(flag, dest=f, metavar=mv, help=tr("tariff.help." + f))
    sp.add_argument("--clear", metavar="port,traffic|all", help=tr("tariff.help.clear"))
    sp.add_argument("--dry", action="store_true", help=tr("tariff.help.dry"))
    sp.add_argument("--code", metavar="C", help=tr("tariff.help.code"))
    sp.add_argument("--by-mcp", dest="by_mcp", action="store_true", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_set)
