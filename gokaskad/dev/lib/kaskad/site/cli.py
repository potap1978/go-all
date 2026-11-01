"""``kaskadctl site …`` — the domain, its DNS and the certificate of Let's Encrypt (docs/k3/SPEC-wave4.md §4.1).

    site status [--json]                               STATUS
    site domain <имя> | --clear [--json]               set (name checked, DNS checked at once, no issue) / clear
    site dns [--wait N] [--json]                       DNS check (N ≤ 300 s: again every 15 s until ready)
    site email [<почта> | --clear] [--json]            the address for Let's Encrypt (no argument: the one now)
    site cert issue [--staging] [--json]               certbot when needed, port 80, issue or keep, hand out
    site cert renew [--json]                           certbot renew --cert-name (only when due)
    site cert-installed [--quiet] [--json]             the deploy hook: the copy for the consumers, their reload
    site use sstp|ocserv on|off [--dry-run] [--json]   SSTP / OpenConnect on the certificate of the domain, or back
    site tick [--quiet] [--json]                       the watchdog of the expiry (kaskad-site.timer)
    site mode site [--template ID|--random] [--json]   nginx, cover site and panel at a secret path (no flag: the
                                                       template of the site before, else a random one)
    site mode domain [--json]                          take nginx site down, keep the domain and certificate
    site panel-url [--show] [--json]                   secret path masked unless explicitly shown
    site panel-path rotate [--json]                    replace the path (the old one becomes 404)
    site check [--json]                                curl the site, panel, redirect and certificate locally
    site vhost [--refresh] [--quiet] [--json]          our vhost by this version's template (no flag: only whether
                                                       it is current); nginx -t, the previous file back on a refusal
    site remove [--json]                               the domain away and /etc/kaskad/site but its pkgs (removal)

The templates of the cover site (``site templates``, ``site template …``) come from kaskad.site.templates when it is
there. Every command takes --json (exactly one JSON document). A refusal of ``cert issue``/``cert renew`` prints the
JSON of the contract ``{"ok": false, "reason", "text", "retry_after"?}`` (plus ``error`` and ``code``); exit codes —
kaskad.errors (3: port 80 is taken).
"""

import importlib
import os
import sys

from .. import errors, sysrun, util
from ..cli import add_json_flag, emit
from ..i18n import fmt_date, fmt_datetime, tr
from . import model, ops

_ON = ("on", "yes", "true", "1")
_OFF = ("off", "no", "false", "0")
DNS_STATES = ("ok", "partial", "elsewhere", "none", "unknown")     # the words: tr("site_cli.dns.state.<state>")


def dns_text(state):
    return tr("site_cli.dns.state." + state) if state in DNS_STATES else state


def _yn(v):
    return tr("site_cli.yes") if v else tr("site_cli.no")


def _r():
    return sysrun.default_runner()


def _say(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _warn(doc):
    for w in doc.get("warnings") or []:
        _say(util.core_line("warn", w) or tr("site_cli.warning", text=w))


def _refused(args, e):
    doc = dict(e.doc, error=e.doc["text"], code=e.code)
    if getattr(args, "json", False):
        emit(args, doc)
    else:
        _say(tr("site_cli.refused", text=e.doc["text"]))
    return e.code


def dns_lines(d):
    if not d:
        return [tr("site_cli.dns.never")]
    lines = ["DNS: %s (%s)" % (dns_text(d["state"]), fmt_datetime(d.get("checked_at") or ""))]
    lines.append("  " + tr("site_cli.dns.records", a=", ".join(d.get("a") or []) or tr("site_cli.none"),
                           aaaa=", ".join(d.get("aaaa") or []) or tr("site_cli.none"),
                           own=", ".join(d.get("own") or []) or tr("site_cli.dns.own_unknown")))
    lines.append("  " + tr("site_cli.dns.ready", yn=_yn(d.get("ready"))))
    if d.get("advice"):
        lines.append("  " + d["advice"])
    return lines


def cert_line(c):
    if not c:
        return tr("site_cli.cert.none")
    if c["kind"] == "selfsigned":
        return tr("site_cli.cert.selfsigned", until=fmt_date(c["not_after"][:10]))
    return tr("site_cli.cert.le", staging=tr("site_cli.cert.staging") if c.get("staging") else "",
              until=fmt_date(c["not_after"][:10]), days=int(c["days_left"]), issuer=c.get("issuer") or "?")


def port443_line(p443, mode):
    """Who holds TCP/443 for the site — xray of the gateway counts (3.3.0 T-M6 №15: «свободен» while xray held it)."""
    if not p443.get("free"):
        return tr("site_cli.p443.busy", who=p443.get("who"))
    if p443.get("xray"):
        if p443.get("behind"):
            return tr("site_cli.p443.behind")
        if mode == "site":
            return tr("site_cli.p443.not_behind")
        return tr("site_cli.p443.xray")
    if p443.get("orphan"):
        return tr("site_cli.p443.orphan")
    if mode == "site":
        return tr("site_cli.p443.site")
    return tr("site_cli.p443.free")


def status_lines(doc):
    if not doc.get("domain"):
        lines = [tr("site_cli.status.no_domain")]
    else:
        name = doc["domain_unicode"] if doc.get("domain_unicode") and doc["domain_unicode"] != doc["domain"] \
            else doc["domain"]
        lines = [tr("site_cli.status.domain", name=name,
                    ascii=" (%s)" % doc["domain"] if name != doc["domain"] else "")]
    lines.append(cert_line(doc.get("cert")))
    if doc.get("domain"):
        lines += dns_lines(doc.get("dns"))
    p = doc.get("port80") or {}
    lines.append(tr("site_cli.status.port80", state=tr("site_cli.status.free") if p.get("free") else
                    tr("site_cli.status.busy", who=p.get("who"))))
    site = doc.get("site") or {}
    p443 = site.get("port443") or {}
    lines.append(port443_line(p443, doc.get("mode")))
    if doc.get("mode") == "site":
        lines.append(tr("site_cli.status.site",
                        panel=site.get("panel_url_masked") or tr("site_cli.status.panel_pending")))
    u = doc.get("use") or {}
    imp = doc.get("use_impact") or {}
    use = tr("site_cli.status.use", **{k: _yn(u.get(k)) for k in ("panel", "pages", "sstp", "ocserv")})
    if u.get("hy2") is not None:                         # KT3-5: Hysteria2 takes it by itself when there is a domain
        use += tr("site_cli.status.use_hy2", hy2=_yn(u.get("hy2")))
    lines.append(use)
    if doc.get("domain"):
        pending = [("SSTP", "sstp"), ("OpenConnect", "ocserv")]
        pending = [(title, key) for title, key in pending if not u.get(key)]
        back = [("SSTP", "sstp"), ("OpenConnect", "ocserv")]
        back = [(title, key) for title, key in back if u.get(key)]
        if pending:
            lines.append(tr("site_cli.status.pending", list=", ".join(
                "%s — %d" % (title, imp.get(key, 0)) for title, key in pending)))
        if back:
            lines.append(tr("site_cli.status.back", list=", ".join(
                "%s — %d" % (title, imp.get(key, 0)) for title, key in back)))
        lines.append(tr("site_cli.status.host", yn=tr("site_cli.yes") if doc.get("host_is_domain") else
                        tr("site_cli.status.host_no", domain=doc["domain"])))
    rb = (doc.get("renew") or {}).get("by")
    if rb:
        lines.append(tr("site_cli.status.renew", by=rb))
    lines.append(tr("site_cli.status.certbot",
                    certbot=tr("site_cli.status.certbot_yes") if (doc.get("deps") or {}).get("certbot")
                    else tr("site_cli.status.certbot_no"), email=doc.get("email") or tr("site_cli.email_none")))
    return lines


def cmd_status(args):
    doc = ops.status(_r())
    emit(args, doc, status_lines(doc))
    return errors.OK


def cmd_domain(args):
    if args.clear:
        if args.name:
            raise errors.KaskadError(tr("site_cli.domain.name_or_clear"), code=errors.USAGE)
        res = with_price(args, lambda yes: ops.clear(_r(), way_in=True, yes=yes,
                                                     vless_site=getattr(args, "vless_site", None)))
        lines = [tr("site_cli.domain.cleared")]
        if res.get("kept"):
            lines.append(tr("site_cli.domain.kept", paths=", ".join(res["kept"])))
        if res.get("text"):
            lines.append(res["text"])
        lines += ["! %s" % w for w in res.get("warnings") or []]
        emit(args, res, lines)
        return errors.OK
    if not args.name:
        raise errors.KaskadError(tr("site_cli.domain.need"), code=errors.USAGE)
    say = None if args.json else (lambda key, st, detail="", title=None: _say(
        "%s%s" % ((title + ": ") if title else "", detail)) if st == "wait" else None)
    try:
        res = ops.set_domain(_r(), args.name, say=say)
    except ops.Refusal as e:                    # a working domain kept: the new name not ready (T-M6-qs330d №1)
        return _refused(args, e)
    if res.get("changed_from"):
        lines = [tr("site_cli.domain.changed", old=res["changed_from"], new=res["site"]["domain"],
                    cert=cert_line(res.get("cert")))]
        v = res.get("vless") or {}
        if v.get("text"):
            lines.append(v["text"])
        lines += [util.core_line("warn", w) or tr("site_cli.warning", text=w) for w in res.get("warnings") or []]
        emit(args, res, lines)
        return errors.OK
    lines = [tr("site_cli.domain.set", domain=res["site"]["domain"])]
    lines += dns_lines(res["dns"])
    if res.get("text"):
        lines.append(res["text"])
    lines += [util.core_line("warn", w) or tr("site_cli.warning", text=w) for w in res.get("warnings") or []]
    emit(args, res, lines)
    return errors.OK


def cmd_dns(args):
    say = None if args.json else _say
    doc = ops.check_dns(_r(), args.wait or 0, say=say)
    emit(args, doc, dns_lines(doc))
    return errors.OK


def cmd_email(args):
    if not args.clear and not args.email:               # no argument — the mail now (T-M6-332b №9)
        from .. import state
        e = model.peek(state.load())["acme_email"]
        emit(args, {"ok": True, "email": e}, tr("site_cli.email.current", email=e or tr("site_cli.email_none")))
        return errors.OK
    if args.clear and args.email:
        raise errors.KaskadError(tr("site_cli.email.email_or_clear"), code=errors.USAGE)
    res = ops.set_email(_r(), None if args.clear else args.email)
    _warn(res)
    emit(args, res, tr("site_cli.email.done", email=res["email"] or tr("site_cli.email_none")))
    return errors.OK


def cmd_issue(args):
    try:
        res = ops.issue(_r(), staging=args.staging)
    except ops.Refusal as e:
        return _refused(args, e)
    if not args.json:
        _warn(res)
        if ops.NO_EMAIL in (res.get("warning_codes") or []):
            _say(tr("site_cli.issue.set_email"))
    lines = [res["text"], cert_line(res["cert"])]
    if res.get("consumers"):
        lines.append(tr("site_cli.issue.reloaded", names=", ".join(c["name"] for c in res["consumers"])))
    emit(args, res, lines)
    return errors.OK


def cmd_renew(args):
    try:
        res = ops.renew(_r())
    except ops.Refusal as e:
        return _refused(args, e)
    emit(args, res, [tr("site_cli.renew.done") if res["renewed"] else tr("site_cli.renew.not_due"),
                     cert_line(res["cert"])])
    return errors.OK


def cmd_installed(args):
    res = ops.cert_installed(_r(), lineage=os.environ.get("RENEWED_LINEAGE") or None)
    text = None
    if not args.quiet:
        if res.get("skipped") == "lineage":
            text = tr("site_cli.installed.lineage")
        elif res.get("skipped"):
            text = tr("site_cli.installed.no_domain")
        else:
            text = tr("site_cli.installed.done", names=", ".join(c["name"] for c in res["consumers"])
                      or tr("site_cli.installed.unchanged"))
    emit(args, res, text)
    return errors.OK


def _onoff(value):
    v = str(value).strip().lower()
    if v in _ON:
        return True
    if v in _OFF:
        return False
    raise errors.KaskadError(tr("site_cli.onoff.bad", value=repr(value)), code=errors.USAGE)


def cmd_use(args):
    on = _onoff(args.value)
    res = ops.use(_r(), args.kind, on, dry_run=args.dry_run)
    if not args.json:
        _warn(res)
    title = ops.TITLES[args.kind]
    if res.get("dry_run"):
        lines = [tr("site_cli.use.dry_on" if on else "site_cli.use.dry_off", title=title, n=len(res["affected"]))]
    else:
        lines = [tr("site_cli.use.domain" if res["use"][args.kind] else "site_cli.use.selfsigned", title=title)]
    if res.get("text"):
        lines.append(res["text"])
    emit(args, res, lines)
    return errors.OK


TICK_ACTIONS = ("none", "ok", "installed", "renewed", "missing")    # the words: tr("site_cli.tick.<action>")


def cmd_tick(args):
    res = ops.tick(_r())
    text = None
    if not args.quiet or res["action"] == "failed":
        text = res.get("text") or (tr("site_cli.tick." + res["action"], days=res.get("days_left"))
                                   if res["action"] in TICK_ACTIONS else None)
    emit(args, res, text)
    return errors.OK


def cmd_remove(args):
    res = ops.remove(_r())
    lines = [tr("site_cli.remove.done")]
    if res["kept"]:
        lines.append(tr("site_cli.remove.kept", list=", ".join(res["kept"])))
    if res["packages"]:
        lines.append(tr("site_cli.remove.packages", list=" ".join(res["packages"])))
    emit(args, res, lines)
    return errors.OK


def ask_yes(text, args=None):
    """A person at the terminal (stdin and stderr are a tty, no --json): the price is shown and asked with numbers
    (правило 2026-10-07: «1) Да … 2) Нет … Выбор [2]:»). -> True only for «1». Not a tty — False (the refusal stays)."""
    import sys
    try:
        if getattr(args, "json", False) or not (sys.stdin.isatty() and sys.stderr.isatty()):
            return False
        sys.stderr.write("%s\n%s " % (text, tr("site_cli.ask_yes")))
        sys.stderr.flush()
        return sys.stdin.readline().strip() == "1"
    except (AttributeError, ValueError, OSError):
        return False


def with_price(args, call):
    """``call(yes)`` — an action whose price of new VLESS links the core asks (CONFLICT «vless_change»,
    SPEC-vless-tls-default §3.5–§3.7): ``--yes`` passes it; in a tty without it — the question with numbers; else the
    refusal of the core as it is."""
    yes = bool(getattr(args, "yes", False))
    try:
        return call(yes)
    except errors.ConflictError as e:
        d = getattr(e, "data", None) or {}
        if yes or d.get("need") != "vless_change" or d.get("need_pick") or not ask_yes(d.get("text") or str(e), args):
            raise
        return call(True)


def cmd_mode(args):
    vsite = getattr(args, "vless_site", None)
    try:
        res = with_price(args, lambda yes: ops.mode(_r(), args.mode, template_id=args.template,
                                                    fresh=bool(getattr(args, "random", False)), yes=yes,
                                                    vless_site=vsite, via=getattr(args, "via", None) or "cli"))
    except ops.Refusal as e:
        return _refused(args, e)
    if args.mode == "site":
        t = res.get("template") or {}
        text = [tr("site_cli.mode.on", name=t.get("name") or t.get("id") or tr("site_cli.mode.simple_page"))]
    else:
        text = [tr("site_cli.mode.off")]
    v = res.get("vless") or {}
    if v.get("changed"):
        text.append(tr("site_cli.mode.vless", how=tr("site_cli.mode.vless_tls") if v.get("security") == "tls" else
                       tr("site_cli.mode.vless_reality", port=v.get("port"))))
    emit(args, res, text)
    if not getattr(args, "json", False):
        _warn(res)                         # among them «выданные раньше ссылки VLESS не подключатся» (T-M6 №5)
    return errors.OK


def cmd_vhost(args):
    try:
        res = ops.vhost_refresh(_r(), check=not args.refresh)
    except ops.Refusal as e:
        if args.quiet and not getattr(args, "json", False):
            return e.code
        return _refused(args, e)
    text = None
    if not args.quiet:
        if res.get("skipped"):
            text = tr("site_cli.vhost.foreign" if res["skipped"] == "foreign" else "site_cli.vhost.none")
        elif not args.refresh:
            text = tr("site_cli.vhost.current" if res.get("current") else "site_cli.vhost.outdated")
        elif not res.get("changed"):
            text = tr("site_cli.vhost.unchanged")
        else:       # a stopped nginx is not started: it reads the new file when it starts
            text = tr("site_cli.vhost.refreshed" if res.get("reload") else "site_cli.vhost.written_stopped")
    emit(args, res, text)
    return errors.OK


def cmd_panel_url(args):
    try:
        res = ops.panel_url(show=args.show)
    except ops.Refusal as e:
        return _refused(args, e)
    lines = [tr("site_cli.panel_url", url=res["url"])]
    if res.get("fallback"):                      # 3.3.7.5: the open mode beside the domain — the way in by the port
        lines.append(tr("site_cli.panel_fallback", url=res["fallback"]))
    emit(args, res, lines)
    return errors.OK


def cmd_panel_path(args):
    try:
        res = ops.rotate_panel_path(_r())
    except ops.Refusal as e:
        return _refused(args, e)
    emit(args, res, tr("site_cli.panel_path", url=res["url"]))
    return errors.OK


CHECK_VERDICTS = ("ok", "dns_mismatch", "no_cert", "http_error", "no_site", "cert_expiring", "panel_unreachable")


def cmd_check(args):
    res = ops.check(_r())
    v = res["verdict"]
    lines = [tr("site_cli.check.head", verdict=tr("site_cli.check.verdict." + v) if v in CHECK_VERDICTS else v)]
    if res.get("why"):                             # nginx не открывает сокет панели — почему и что сделать
        lines += [tr("site_cli.check.why", why=res["why"]), res.get("fix") or ""]
    lines += [util.core_line("warn", w) or tr("site_cli.check.warning", text=w)
              for w in res.get("warnings") or []]
    emit(args, res, lines)
    return errors.OK if res.get("ok") else errors.ERROR


def register(subparsers):
    p = subparsers.add_parser("site", help=tr("site_cli.help.site"))
    sub = p.add_subparsers(dest="site_cmd", metavar=tr("site_cli.help.metavar"))
    sub.required = True
    add_json_flag(sub.add_parser("status", help=tr("site_cli.help.status"))).set_defaults(func=cmd_status)
    p1 = add_json_flag(sub.add_parser("domain", help=tr("site_cli.help.domain")))
    p1.add_argument("name", nargs="?", help=tr("site_cli.help.domain_name"))
    p1.add_argument("--clear", action="store_true", help=tr("site_cli.help.domain_clear"))
    p1.add_argument("--yes", action="store_true", help=tr("site_cli.help.vless_yes"))
    p1.add_argument("--vless-site", default=None, metavar="SITE", help=tr("site_cli.help.vless_site"))
    p1.set_defaults(func=cmd_domain)
    p1 = add_json_flag(sub.add_parser("dns", help=tr("site_cli.help.dns")))
    p1.add_argument("--wait", type=int, default=0, metavar="N", help=tr("site_cli.help.dns_wait"))
    p1.set_defaults(func=cmd_dns)
    p1 = add_json_flag(sub.add_parser("email", help=tr("site_cli.help.email")))
    p1.add_argument("email", nargs="?")
    p1.add_argument("--clear", action="store_true", help=tr("site_cli.help.email_clear"))
    p1.set_defaults(func=cmd_email)
    pc = sub.add_parser("cert", help=tr("site_cli.help.cert"))
    csub = pc.add_subparsers(dest="cert_cmd", metavar=tr("site_cli.help.metavar"))
    csub.required = True
    p1 = add_json_flag(csub.add_parser("issue", help=tr("site_cli.help.issue")))
    p1.add_argument("--staging", action="store_true", help=tr("site_cli.help.staging"))
    p1.set_defaults(func=cmd_issue)
    add_json_flag(csub.add_parser("renew", help=tr("site_cli.help.renew"))).set_defaults(func=cmd_renew)
    p1 = add_json_flag(sub.add_parser("cert-installed", help=tr("site_cli.help.installed")))
    p1.add_argument("--quiet", action="store_true", help=tr("site_cli.help.installed_quiet"))
    p1.set_defaults(func=cmd_installed)
    p1 = add_json_flag(sub.add_parser("use", help=tr("site_cli.help.use")))
    p1.add_argument("kind", choices=model.USE_KINDS)
    p1.add_argument("value", help=tr("site_cli.help.use_value"))
    p1.add_argument("--dry-run", action="store_true", help=tr("site_cli.help.use_dry_run"))
    p1.set_defaults(func=cmd_use)
    p1 = add_json_flag(sub.add_parser("tick", help=tr("site_cli.help.tick")))
    p1.add_argument("--quiet", action="store_true", help=tr("site_cli.help.tick_quiet"))
    p1.set_defaults(func=cmd_tick)
    add_json_flag(sub.add_parser("remove", help=tr("site_cli.help.remove"))).set_defaults(func=cmd_remove)
    p1 = add_json_flag(sub.add_parser("mode", help=tr("site_cli.help.mode")))
    p1.add_argument("mode", choices=("site", "domain"))
    g = p1.add_mutually_exclusive_group()
    g.add_argument("--template", default=None, help=tr("site_cli.help.mode_template"))
    g.add_argument("--random", action="store_true",
                   help=tr("site_cli.help.mode_random"))
    p1.add_argument("--yes", action="store_true", help=tr("site_cli.help.vless_yes"))
    p1.add_argument("--vless-site", default=None, metavar="SITE", help=tr("site_cli.help.vless_site"))
    p1.add_argument("--via", default="cli", choices=("cli", "ssh", "mcp"), help=tr("gw_cli.help.vless_mode_via"))
    p1.set_defaults(func=cmd_mode)
    p1 = add_json_flag(sub.add_parser("panel-url", help=tr("site_cli.help.panel_url")))
    p1.add_argument("--show", action="store_true", help=tr("site_cli.help.panel_url_show"))
    p1.set_defaults(func=cmd_panel_url)
    pp = sub.add_parser("panel-path", help=tr("site_cli.help.panel_path"))
    pps = pp.add_subparsers(dest="panel_path_cmd", metavar=tr("site_cli.help.metavar"))
    pps.required = True
    add_json_flag(pps.add_parser("rotate", help=tr("site_cli.help.panel_path_rotate"))).set_defaults(
        func=cmd_panel_path)
    add_json_flag(sub.add_parser("check", help=tr("site_cli.help.check"))).set_defaults(func=cmd_check)
    p1 = add_json_flag(sub.add_parser("vhost", help=tr("site_cli.help.vhost")))
    p1.add_argument("--refresh", action="store_true", help=tr("site_cli.help.vhost_refresh"))
    p1.add_argument("--quiet", action="store_true", help=tr("site_cli.help.vhost_quiet"))
    p1.set_defaults(func=cmd_vhost)
    try:
        templates = importlib.import_module(__package__ + ".templates")
    except ImportError as e:
        # the module of the cover site (C1) is not there yet; a broken import INSIDE it is a real error
        if getattr(e, "name", None) != __package__ + ".templates":
            raise
        templates = None
    if templates is not None and hasattr(templates, "register_cli"):
        templates.register_cli(sub)
