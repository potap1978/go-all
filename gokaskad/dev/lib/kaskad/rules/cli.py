"""``kaskadctl rules ...`` — rules «Россия напрямую» (docs/k3/SPEC-wave3.md §2, WP-R).

    rules status [--json]                                    the lists, what applies, the last update
    rules update [--force] [--json]                          download now (the timer does it daily)
    rules load [--force] [--quiet] [--json]                  the networks of Russia into gw_ru when the table lacks
                                                             them (kaskad-gw-rules-load.timer; 1 — the load failed)
    rules default <all_exit|ru_direct> [--json]              the profile of the node
    rules profile <user|device> <id> <inherit|all_exit|ru_direct> [--json]
    rules block <ads|torrent> <on|off> [--json]              the switches «Реклама», «Торренты»
    rules ru-via <user|device> <id> <inherit|direct|<gid>> [--yes] [--json]
                                                             «Российские сайты» (T15 Q7, SPEC-groups §8.7): through a
                                                             Russian group of the policy or directly; a group — the
                                                             warning and «1) Да  2) Нет», without a terminal --yes

Every command takes --json (exactly one JSON document). Exit codes — kaskad.errors (6: an update is already
running).
"""

import sys
import time

from .. import errors, state, sysrun, util
from ..cli import add_json_flag, emit
from ..gw import model
from ..i18n import tr
from . import ops, rfgate

PROFILES = ("ru_direct", "all_exit", "inherit")
_ON = ("on", "yes", "true", "1")
_OFF = ("off", "no", "false", "0")


def profile_text(p, target=None):
    """«Россия напрямую» / «всё через выход» / «как у владельца» of a profile (the profile itself when unknown).
    «inherit» of a person is the default («same as default»), of a device — its person («same as the person»; EN —
    never «owner», T-M6-332 №15; RU «как у владельца» for both, as in 3.3.1)."""
    if p == "inherit" and target == "user":
        return tr("rules.profile.inherit_user")
    return tr("rules.profile." + p) if p in PROFILES else p


def _r():
    return sysrun.default_runner()


def _warnings(res):
    for w in ((res or {}).get("warnings") or []) + (((res or {}).get("apply") or {}).get("warnings") or []):
        sys.stderr.write((util.core_line("warn", w) or tr("rules.cli.warning", text=w)) + "\n")


def _num(n):
    return "—" if n is None else "{:,}".format(n).replace(",", tr("rules.cli.thousands"))


def _on_off(v):
    return tr("rules.cli.on") if v else tr("rules.cli.off")


def status_lines(doc):
    lines = [tr("rules.cli.title")]
    lst = doc.get("lists")
    if not lst:
        lines.append(tr("rules.cli.no_lists"))
    else:
        k = lst.get("kernel") or {}
        gi, gs, tx = lst.get("geoip") or {}, lst.get("geosite") or {}, lst.get("text") or {}
        lines.append(tr("rules.cli.lists", label=lst["label"],
                        fallback=tr("rules.cli.lists_fallback") if lst.get("fallback") else ""))
        if gi:
            lines.append(tr("rules.cli.geoip", tag=gi.get("tag") or tr("rules.cli.no_tag"), source=gi.get("source"),
                            sha=str(gi.get("sha256"))[:12]))
        if tx:
            lines.append(tr("rules.cli.text", source=tx.get("source"), license=tx.get("license") or ""))
        if gs:
            lines.append(tr("rules.cli.geosite", tag=gs.get("tag") or tr("rules.cli.no_tag"), source=gs.get("source")))
        lines.append(tr("rules.cli.nets", cidrs=_num(k.get("cidrs")), ranges=_num(k.get("ranges")), v6=_num(k.get("v6"))))
        lines.append(tr("rules.cli.table", state=ops.table_text(doc.get("table") or {})))
        g = doc.get("groups")
        if g:
            lines.append(tr("rules.cli.groups", direct=_num(g["direct"]), force=_num(g["force"]), block=_num(g["block"])))
        dm = lst.get("domains") or {}
        if dm:
            lines.append(tr("rules.cli.skipped", keyword=_num(dm.get("keyword")), regexp=_num(dm.get("regexp"))))
    nftset = (doc.get("dns") or {}).get("nftset")
    lines.append(tr("rules.cli.dns", version=(doc.get("dns") or {}).get("version") or "?",
                    nftset=tr("rules.cli.nftset_yes") if nftset else (tr("rules.cli.nftset_no") if nftset is False
                                                                     else tr("rules.common.unknown"))))
    lines.append(tr("rules.cli.default", profile=profile_text(doc["default"]),
                    note="" if doc["default_applied"] == doc["default"] else (
                        tr("rules.cli.default_no_consent") if doc.get("default_why") == "no_consent"
                        else tr("rules.cli.default_wait"))))
    lines.extend(rfgate.notes(doc.get("rf") or {}))
    lines.append(tr("rules.cli.blocks", ads=tr("rules.common.yes") if doc["block_ads"] else tr("rules.common.no"),
                    torrent=tr("rules.cli.torrent_block") if doc["block_torrent"] else tr("rules.cli.torrent_route")))
    lines.append(tr("rules.cli.ru_devices", n=doc.get("ru_direct", 0), total=len(doc.get("devices") or [])))
    for d in doc.get("devices") or []:
        lines.append("  %s %-20s %s%s" % (d["id"], str(d.get("name") or "")[:20], profile_text(d["applied"]),
                                         "" if d["from"] == "device" else " (%s)" % (tr("rules.cli.from_user")
                                                                                    if d["from"] == "user"
                                                                                    else tr("rules.cli.from_default"))))
    last = doc.get("last") or {}
    if last:
        lines.append(tr("rules.cli.last", at=ops.when_utc(last.get("at")) or "?",
                        result=tr("rules.cli.last_error", error=last.get("error")) if not last.get("ok")
                        else (tr("rules.cli.last_changed") if last.get("changed") else tr("rules.cli.last_same"))))
    t = doc.get("timer")
    if t is not None:
        lines.append(tr("rules.cli.timer", unit="kaskad-gw-rules.timer", state=_on_off(t.get("enabled"))))
    t = doc.get("load_timer")
    if t is not None:
        lines.append(tr("rules.cli.timer", unit="kaskad-gw-rules-load.timer", state=_on_off(t.get("enabled"))))
    lines.append(doc.get("attribution") or "")
    return lines


def cmd_status(args):
    doc = ops.status(state.load(), _r())
    emit(args, doc, status_lines(doc))
    return errors.OK


def cmd_update(args):
    res = ops.update(_r(), force=args.force)
    _warnings(res)
    if res["changed"]:
        text = tr("rules.cli.updated", lists=res["lists"])
    else:
        text = tr("rules.cli.unchanged", lists=res["lists"])
    emit(args, res, text)
    return errors.OK


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def cmd_load(args):
    res = ops.load(_r(), force=args.force)
    act = res.get("action")
    if act == "failed":
        text = tr("rules.cli.load.failed", error=res.get("error"),
                  next=_hm(res["next"]) if res.get("next") else tr("rules.cli.load.later"))
    elif act == "loaded":
        text = tr("rules.cli.load.loaded", n=res.get("elements", 0), sec=res.get("seconds", 0))
    elif act == "ok":
        text = tr("rules.cli.load.ok") if res.get("wanted") else tr("rules.cli.load.nothing")
    elif act == "waiting":
        text = tr("rules.cli.load.waiting", error=res.get("error"), at=_hm(res["next"]))
    elif act == "off":
        text = tr("rules.cli.load.off")
    else:
        text = tr("rules.cli.load.no_table")
    if args.quiet and act != "failed":
        text = None
    emit(args, res, text)
    return errors.OK if res.get("ok") else errors.ERROR


def _orgs(args):
    """``--orgs id,id`` — the organisations of the block the person saw (SSH menu, MCP); None — not given."""
    raw = getattr(args, "orgs", None)
    if raw is None:
        return None
    got = [x.strip() for x in raw.split(",") if x.strip()]
    if rfgate.shown_orgs(got) is None:
        raise errors.KaskadError(tr("rules.cli.orgs_bad"), code=errors.USAGE)
    return got


def _rf(args):
    """The tick ⚠РФ of the command line: ``--confirm-not-rf`` (``--via`` — only a label of the journal), ``--orgs`` —
    what the block showed: the consent then covers no organisation beyond it (SEC-s1-r67 №1). MCP must say it."""
    via = getattr(args, "via", None) or "cli"
    return ops.rf_of(getattr(args, "confirm_not_rf", False), via=via, who="mcp" if via == "mcp" else None,
                     orgs=_orgs(args), must_show=via == "mcp")


def _gated(fn, *a, **kw):
    """A refusal ⚠РФ said with the hint of the command line (§7 п. 5); the data stay (``--json``: ``data.need``)."""
    try:
        return fn(*a, **kw)
    except errors.KaskadError as e:
        if (getattr(e, "data", None) or {}).get("need") != "rf_consent":
            raise
        raise errors.KaskadError("%s\n%s" % (e.data.get("block"), tr("rules.rf.cli_retry")), code=e.code,
                                 data=e.data) from None


def cmd_default(args):
    res = _gated(ops.set_default, _r(), args.profile, rf=_rf(args))
    _warnings(res)
    emit(args, res, tr("rules.cli.default_set", profile=profile_text(res["rules"]["default"])))
    return errors.OK


def cmd_profile(args):
    res = _gated(ops.set_profile, _r(), args.target, args.id, args.profile, rf=_rf(args))
    _warnings(res)
    emit(args, res, tr("rules.cli.profile_set", who=tr("rules.cli.who_user") if args.target == "user"
                       else tr("rules.cli.who_device"), name=res["name"], profile=profile_text(res["profile"], args.target)))
    return errors.OK


def cmd_consent(args):
    res = _gated(ops.consent, _r(), _rf(args))
    _warnings(res)
    if res["granted"]:
        text = tr("rules.rf.consented", names=", ".join(o["name"] for o in res["rf"].get("orgs") or []))
    else:
        text = tr("rules.rf.consent_none")
    emit(args, res, text)
    return errors.OK


def _rf_flags(p):
    p.add_argument("--confirm-not-rf", dest="confirm_not_rf", action="store_true",
                   help=tr("rules.cli.help.confirm_not_rf"))
    p.add_argument("--via", choices=("cli", "ssh", "env", "mcp", "setup"), default="cli",
                   help=tr("rules.cli.help.via"))
    p.add_argument("--orgs", metavar="ID,ID", help=tr("rules.cli.help.orgs"))
    return p


def cmd_block(args):
    v = str(args.value).strip().lower()
    if v not in _ON + _OFF:
        raise errors.KaskadError(tr("rules.cli.block.bad_value", value=repr(args.value)), code=errors.USAGE)
    on = v in _ON
    res = ops.set_blocks(_r(), ads=on if args.what == "ads" else None, torrent=on if args.what == "torrent" else None)
    _warnings(res)
    emit(args, res, tr("rules.cli.block.done", what=tr("rules.cli.block.ads") if args.what == "ads"
                       else tr("rules.cli.block.torrent"),
                       state=tr("rules.cli.block.on") if on else (tr("rules.cli.block.ads_off") if args.what == "ads"
                                                                  else tr("rules.cli.block.torrent_off"))))
    return errors.OK


def _ru_via_group(st, target, ref, via):
    """(the name of the person or device, the shown name of the Russian group its Russian traffic would go through
    after ``via``, or None). ``via`` is checked first (``model.parse_ru_via``: a bad value — USAGE before any
    question); ``inherit`` of a device takes its person's own choice, of a person — the node (always directly in stage
    1). An unknown person or device — (None, None): ``ops.set_ru_via`` refuses it in its own words."""
    from ..gw import policy as P
    v = model.parse_ru_via(st, via)
    obj = model.find_user(st, ref) if target == "user" else model.find_device(st, ref)
    if obj is None:
        return None, None
    if v == model.RU_VIA_INHERIT:
        v = (model.find_user(st, obj.get("user")) or {}).get("ru_via") if target == "device" else None
    if v in (None, "", model.RU_VIA_INHERIT, model.RU_VIA_DIRECT):
        return obj.get("name") or obj.get("id"), None
    grp = P.group(P.stored(st) or {}, v)
    if grp is None or grp.get("role") != "ru":
        return obj.get("name") or obj.get("id"), None
    return obj.get("name") or obj.get("id"), P.label(grp)


def ru_via_text(res):
    """«Российские сайты у «Мама»: через «Дом»» / «… напрямую с сервера» / «… как у пользователя» of an answer of
    ``ops.set_ru_via``; the group by its shown name."""
    from ..gw import policy as P
    v = res.get("ru_via")
    if v == model.RU_VIA_INHERIT:
        how = tr("rules.cli.ru_via.inherit_" + ("device" if res.get("target") == "device" else "user"))
    elif v == model.RU_VIA_DIRECT:
        how = tr("rules.cli.ru_via.direct")
    else:
        grp = P.group(P.stored(state.load()) or {}, v)
        how = tr("rules.cli.ru_via.group", group=P.label(grp) if grp else v)
    return tr("rules.cli.ru_via.done" if res.get("changed") else "rules.cli.ru_via.same", name=res.get("name") or "",
              how=how)


def cmd_ru_via(args):
    """``rules ru-via``: a Russian group asks first (§8.7, Б12) — the warning and «1) Да  2) Нет» on a terminal,
    ``--yes`` elsewhere (MCP passes it: the client asks the person, destructive). ``inherit``/``direct`` never ask."""
    from ..gw import cli_policy
    st = state.load()
    name, group = _ru_via_group(st, args.target, args.id, args.via)
    warn = tr("rules.cli.ru_via.warn", name=name, group=group) if group is not None else None
    if warn is not None:
        if not args.json:
            sys.stderr.write(warn + "\n")
        hint = cli_policy._cmd("rules", "ru-via", args.target, args.id, args.via, **{"--yes": True})   # quoted
        if not args.yes and (args.json or not cli_policy._tty()):          # no --dry here: its own words
            raise errors.KaskadError(tr("rules.cli.ru_via.need_yes", hint=hint), code=errors.USAGE)
        if not cli_policy.confirm(args, tr("rules.cli.ru_via.ask"), tr("rules.cli.ru_via.yes"),
                                  tr("rules.cli.ru_via.no"), hint):
            emit(args, {"ok": True, "cancelled": True}, tr("rules.cli.ru_via.cancelled"))
            return errors.OK
    res = ops.set_ru_via(_r(), args.target, args.id, args.via)
    _warnings(res)
    if warn is not None:
        res["warning"] = warn
    st = state.load()
    obj = model.find_user(st, res["id"]) if args.target == "user" else model.find_device(st, res["id"])
    lines = [ru_via_text(res)]
    if args.target == "device" and obj is not None and res.get("ru_via") != model.RU_VIA_DIRECT:
        from . import apply as rapply
        if not rapply.device_rd(st, obj):
            lines.append(tr("rules.cli.ru_via.not_acting"))
    elif args.target == "user" and res.get("ru_via") not in (model.RU_VIA_DIRECT, model.RU_VIA_INHERIT):
        from . import apply as rapply
        if not any(rapply.device_rd(st, d) for d in model.devices(st) if d.get("user") == res["id"]):
            lines.append(tr("rules.cli.ru_via.not_acting"))
    emit(args, res, lines)
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("rules", help=tr("rules.cli.help.rules"))
    sub = p.add_subparsers(dest="rules_cmd", metavar=tr("rules.cli.help.metavar"))
    sub.required = True
    add_json_flag(sub.add_parser("status", help=tr("rules.cli.help.status"))).set_defaults(func=cmd_status)
    p1 = add_json_flag(sub.add_parser("load", help=tr("rules.cli.help.load")))
    p1.add_argument("--force", action="store_true", help=tr("rules.cli.help.load_force"))
    p1.add_argument("--quiet", action="store_true", help=tr("rules.cli.help.load_quiet"))
    p1.set_defaults(func=cmd_load)
    p1 = add_json_flag(sub.add_parser("update", help=tr("rules.cli.help.update")))
    p1.add_argument("--force", action="store_true", help=tr("rules.cli.help.update_force"))
    p1.set_defaults(func=cmd_update)
    p1 = add_json_flag(sub.add_parser("default", help=tr("rules.cli.help.default")))
    p1.add_argument("profile", choices=model.DEFAULT_PROFILES)
    _rf_flags(p1).set_defaults(func=cmd_default)
    p1 = add_json_flag(sub.add_parser("profile", help=tr("rules.cli.help.profile")))
    p1.add_argument("target", choices=ops.TARGETS)
    p1.add_argument("id")
    p1.add_argument("profile", choices=model.RULE_PROFILES)
    _rf_flags(p1).set_defaults(func=cmd_profile)
    p1 = add_json_flag(sub.add_parser("consent", help=tr("rules.cli.help.consent")))
    _rf_flags(p1).set_defaults(func=cmd_consent)
    p1 = add_json_flag(sub.add_parser("block", help=tr("rules.cli.help.block")))
    p1.add_argument("what", choices=("ads", "torrent"))
    p1.add_argument("value", help=tr("rules.cli.help.block_value"))
    p1.set_defaults(func=cmd_block)
    p1 = add_json_flag(sub.add_parser("ru-via", help=tr("rules.cli.help.ru_via")))
    p1.add_argument("target", choices=ops.TARGETS)
    p1.add_argument("id")
    p1.add_argument("via", help=tr("rules.cli.help.ru_via_value"))
    p1.add_argument("--yes", action="store_true", help=tr("rules.cli.help.ru_via_yes"))
    p1.set_defaults(func=cmd_ru_via)
