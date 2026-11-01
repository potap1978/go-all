"""``kaskadctl page ...`` — the page of a person: a secret link that shows him the settings of his devices
(docs/k3/SPEC-wave3.md §5, ``kaskad.panel.userpage``).

    page on <user>         switch the page on (the link is made once, on again — the same) and show the link
    page off <user>        switch it off: the link stops working at once
    page rotate <user>     a new link; the old one dies
    page url <user>        show the link
    page list [--urls]     users with a page (the links only with --urls)

    page sub on|url|rotate|off <user>   the subscription for apps (its own secret link .../p/<token>/sub)
    page sub list                       users with a subscription (no links)

``<user>`` is a name or an id (``gw user list``). Every command takes --json (exactly one JSON document). The link
carries the secret: it is printed by on, rotate, url and ``list --urls`` — nowhere else. The page opens from the
internet only while the panel is in the public mode (the note of the command says so).
"""

from .. import errors, util
from ..cli import add_json_flag, emit
from ..errors import KaskadError
from ..i18n import tr


def _up():
    from . import userpage          # late: ``kaskadctl version`` and the other groups need not import the page
    return userpage


def _link(doc):
    return doc.get("url") or doc.get("path") or ""


def _notes(doc):
    return [n for n in (doc.get("note"), doc.get("qr_note")) if n]


def _show(args, doc, head):
    """Text of on/rotate/url: the head line, the link, then the notes that matter now."""
    notes = [util.core_line("warn", n) or tr("panel.userpageCli.attention", note=n) for n in _notes(doc)]
    lines = [head, tr("panel.userpageCli.link", url=_link(doc))] + notes
    emit(args, dict(doc, ok=True), lines)
    return errors.OK


def cmd_on(args):
    up = _up()
    up.enable(args.user)
    doc = up.status(args.user)
    return _show(args, doc, tr("panel.userpageCli.pageOn", name=doc["user"]["name"]))


def cmd_rotate(args):
    up = _up()
    up.rotate(args.user)
    doc = up.status(args.user)
    return _show(args, doc, tr("panel.userpageCli.pageRotated", name=doc["user"]["name"]))


def cmd_url(args):
    doc = _up().status(args.user)
    if not doc["enabled"]:
        raise KaskadError(tr("panel.userpageCli.pageNotOn", name=doc["user"]["name"], ref=args.user))
    return _show(args, doc, tr("panel.userpageCli.pageHead", name=doc["user"]["name"]))


def cmd_off(args):
    up = _up()
    was = up.disable(args.user)
    doc = up.status(args.user)
    text = tr("panel.userpageCli.pageOff" if was else "panel.userpageCli.pageWasOff", name=doc["user"]["name"])
    emit(args, dict(doc, ok=True, was_enabled=bool(was)), text)
    return errors.OK


def cmd_list(args):
    rows = _up().pages()
    shown = []
    for r in rows:
        row = {"user": r["user"], "name": r["name"], "person_enabled": r["enabled"]}
        if args.urls:
            row["path"], row["url"] = r["path"], r["url"]
        shown.append(row)
    lines = []
    for r, row in zip(rows, shown):
        line = tr("panel.userpageCli.listRow" if r["enabled"] else "panel.userpageCli.listRowPaused",
                  name=r["name"], user=r["user"])
        if args.urls:
            line += "\n    %s" % (r["url"] or r["path"])
        lines.append(line)
    emit(args, {"ok": True, "pages": shown}, lines or [tr("panel.userpageCli.listEmpty")])
    return errors.OK


def _sub_text(doc, head):
    lines = [head, tr("panel.userpageCli.link", url=_link(doc))]
    lines += [util.core_line("warn", n) or tr("panel.userpageCli.attention", note=n)
              for n in (doc.get("note"), doc.get("empty_note")) if n]
    lines.append(tr("panel.userpageCli.subHowto", hours=doc.get("interval", 12)))
    return lines


def cmd_sub_on(args):
    up = _up()
    up.sub_enable(args.user)
    doc = up.sub_status(args.user)
    emit(args, dict(doc, ok=True), _sub_text(doc, tr("panel.userpageCli.subOn", name=doc["user"]["name"])))
    return errors.OK


def cmd_sub_rotate(args):
    up = _up()
    up.sub_rotate(args.user)
    doc = up.sub_status(args.user)
    emit(args, dict(doc, ok=True), _sub_text(doc, tr("panel.userpageCli.subRotated", name=doc["user"]["name"])))
    return errors.OK


def cmd_sub_url(args):
    doc = _up().sub_status(args.user)
    if not doc["enabled"]:
        raise KaskadError(tr("panel.userpageCli.subNotOn", name=doc["user"]["name"], ref=args.user))
    emit(args, dict(doc, ok=True), _sub_text(doc, tr("panel.userpageCli.subHead", name=doc["user"]["name"])))
    return errors.OK


def cmd_sub_off(args):
    up = _up()
    was = up.sub_disable(args.user)
    doc = up.sub_status(args.user)
    text = tr("panel.userpageCli.subOff" if was else "panel.userpageCli.subWasOff", name=doc["user"]["name"])
    emit(args, dict(doc, ok=True, was_enabled=bool(was)), text)
    return errors.OK


def cmd_sub_list(args):
    rows = _up().subs()
    lines = [tr("panel.userpageCli.subRow" if r["enabled"] else "panel.userpageCli.subRowPaused",
                name=r["name"], user=r["user"], n=r["devices"]) for r in rows]
    emit(args, {"ok": True, "subs": rows}, lines or [tr("panel.userpageCli.subListEmpty")])
    return errors.OK


def register(sub):
    pp = sub.add_parser("page", help=tr("panel.userpageCli.helpPage"))
    psub = pp.add_subparsers(dest="page_cmd", metavar=tr("panel.userpageCli.metavarCommand"))
    psub.required = True
    for verb, fn, helpkey in (("on", cmd_on, "panel.userpageCli.helpOn"),
                              ("off", cmd_off, "panel.userpageCli.helpOff"),
                              ("rotate", cmd_rotate, "panel.userpageCli.helpRotate"),
                              ("url", cmd_url, "panel.userpageCli.helpUrl")):
        p = add_json_flag(psub.add_parser(verb, help=tr(helpkey)))
        p.add_argument("user", help=tr("panel.userpageCli.helpUser"))
        p.set_defaults(func=fn)
    pl = add_json_flag(psub.add_parser("list", help=tr("panel.userpageCli.helpList")))
    pl.add_argument("--urls", action="store_true", help=tr("panel.userpageCli.helpUrls"))
    pl.set_defaults(func=cmd_list)
    ps = psub.add_parser("sub", help=tr("panel.userpageCli.helpSub"))
    ssub = ps.add_subparsers(dest="sub_cmd", metavar=tr("panel.userpageCli.metavarCommand"))
    ssub.required = True
    for verb, fn, helpkey in (("on", cmd_sub_on, "panel.userpageCli.helpSubOn"),
                              ("off", cmd_sub_off, "panel.userpageCli.helpSubOff"),
                              ("rotate", cmd_sub_rotate, "panel.userpageCli.helpSubRotate"),
                              ("url", cmd_sub_url, "panel.userpageCli.helpSubUrl")):
        p = add_json_flag(ssub.add_parser(verb, help=tr(helpkey)))
        p.add_argument("user", help=tr("panel.userpageCli.helpUser"))
        p.set_defaults(func=fn)
    add_json_flag(ssub.add_parser("list", help=tr("panel.userpageCli.helpSubList"))).set_defaults(func=cmd_sub_list)
