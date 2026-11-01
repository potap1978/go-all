"""``kaskadctl panel …`` — the web panel (docs/k3/SPEC-wave3.md §3.1).

    panel serve [--port N]                    the server (kaskad-panel.service runs it)
    panel install                             the unit kaskad-panel.service: written, enabled, started
    panel unit --refresh [--quiet]            update an installed unit without restarting the panel
    panel remove [--purge]                    stopped, disabled, deleted; --purge: also /etc/kaskad/panel
                                              (password, «Вид», settings) and the UFW rule of the open mode
    panel status [--json]                     listening addresses, mode, fingerprint, sessions
    panel password [--login L] [--generate]   a new password from stdin (one line; asked twice at a terminal) —
                                              never from argv; --generate makes one and prints it once
    panel link [--ttl S] [--json]             a one-time login URL for the SSH menu and the bot (1 hour)
    panel public on|off [--port N] [--force]  the open mode: HTTPS on the external address, a random port
                                              30000–60999, the node certificate (fingerprint SHA-256)
    panel require-password on|off             the SSH tunnel asks the password too
    panel proxy on|off [--path P] [--new-path] [--domain D] [--show]
                                              the listener for nginx (unix:/run/kaskad/panel/proxy.sock): the panel
                                              at https://<domain>/<path>/, never the tunnel's admin; the secret
                                              path is printed masked unless --show (docs/k3/SPEC-wave4.md §3)

The bot builds the same link in its process: ``kaskad.panel.auth.login_link(ttl)`` -> {"url", "token", "ttl"}.
A running panel picks up ``public`` / ``require-password`` on reload (SIGHUP: ``systemctl reload kaskad-panel``);
a new password ends the sessions made with the old one by itself.
"""

import getpass
import os
import shutil
import sys

from .. import errors, paths, sysrun, util
from ..cli import add_json_flag, emit
from ..i18n import fmt_date, tr
from . import auth, conf

UNIT = conf.UNIT
# The menu shows what these commands print (gokaskad → 18: «1) Ссылка для входа», «2) Пароль: новый»): people read
# the words of the menu there, not commands (stand T-G1b, N7). Catalog keys (3.3.2): translated where printed.
MENU_LINK = "panel.cli.menuLink"
MENU_PASSWORD = "panel.cli.menuPassword"


# The memory of the panel's cgroup (T-M6-qs330b №3: 200M — the OOM killer on «Обновить» of a subscription). Measured
# (tools/panel_mem.py, offline: the real HTTP server over a node of 40 people / 123 devices, the catalog of 33 467
# templates; 4 tabs polling every page and searching templates + 15 device add/delete meanwhile): the Python of the panel 28 MB fresh, 106 MB idle
# after every page was opened, 111 MB peak under load (the stand saw the same 111 MB). Heavy children no longer count
# (kaskad.sysrun.heavy -> systemd-run --scope: kaskadctl ~66 MB, the pool rebuild, nft with 35 602 networks); what stays
# in the cgroup is the light ones (ip, systemctl, xray api ≈30 MB, bash of gokaskad), the page cache of the files it
# reads, and a backup in a request decoded in place (a 19 MB body: +70 MB peak, measured). 111 + 70 + 40 ≈ 220 MB at
# worst: 384M keeps that with a margin and is still a fifth of a 2 GB node.
MEMORY_MAX = "384M"


def unit_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: web panel",
        "After=network-online.target kaskad-net.service",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=notify",
        "NotifyAccess=main",
        "ExecStart=%s panel serve" % paths.kaskadctl(),
        "ExecReload=/bin/kill -HUP $MAINPID",
        "Restart=on-failure",
        "RestartSec=5",
        "Nice=10",
        "CPUQuota=25%",
        "MemoryMax=%s" % MEMORY_MAX,
        "NoNewPrivileges=yes",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def _r():
    return sysrun.default_runner()


def login_url(token, cfg=None):
    """The URL of a one-time link: the open mode when it is on, else the SSH tunnel's local address."""
    return conf.login_url(token, cfg)


def _active(runner):
    from ..gw import sys as gsys
    return gsys.unit_active(runner, UNIT)


def _reload(runner):
    """The panel re-reads settings only after its listeners and SIGHUP handler are ready."""
    return conf.reload_service(runner)


def _site_cert_line():
    """The friendly Let's Encrypt line used by the menu and the web face, or None for the node certificate."""
    try:
        from ..site import certinfo, model
        got = conf.site_cert()
        if got is None:
            return None
        domain, crt, _ = got
        end = certinfo.parse(crt)["not_after"][:10]                       # the UTC date of the certificate
        return tr("panel.cli.siteCert", domain=model.unicode(domain), date=fmt_date(end))
    except (ImportError, ValueError, KeyError):
        return None


def cmd_serve(args):
    from . import server
    return server.serve(args.port)


def cmd_install(args):
    from ..gw import sys as gsys
    runner = _r()
    res = gsys.ensure_unit(runner, UNIT, unit_text())
    util.log_tr("journal.panel.unit_installed", unit=UNIT)
    lines = [tr("panel.cli.installRunning", url=conf.local_url()),
             tr("panel.cli.installTunnel", cmd=conf.tunnel_command())]
    if not auth.password_set():
        lines.append(tr("panel.cli.installNoPassword", menu=tr(MENU_PASSWORD)))
    emit(args, dict(res, ok=True, unit=UNIT, url=conf.local_url(), tunnel=conf.tunnel_command()), lines)
    return errors.OK


def cmd_unit(args):
    """Refresh only an existing unit: an update must not start or restart the panel."""
    from ..gw import sys as gsys
    installed = os.path.exists(gsys.unit_path(UNIT))
    written = False
    if installed and args.refresh:
        written = gsys.write_unit(UNIT, unit_text())
        if written:
            gsys.systemd(_r(), "daemon-reload")
    if not args.quiet:
        emit(args, {"ok": True, "installed": installed, "written": written},
             tr("panel.cli.unitUpdated" if written else "panel.cli.unitUnchanged"))
    return errors.OK


def cmd_remove(args):
    from ..gw import sys as gsys
    runner = _r()
    gone = gsys.remove_unit(runner, UNIT)
    purged = False
    if args.purge:
        cfg = conf.load()
        conf._ufw(runner, "delete", cfg["public"].get("port"))
        for d in (paths.panel_dir(), paths.panel_run_dir()):
            shutil.rmtree(d, ignore_errors=True)
        purged = True
    util.log_tr("journal.panel.unit_removed_purged" if purged else "journal.panel.unit_removed", unit=UNIT)
    emit(args, {"ok": True, "removed": gone, "purged": purged},
         tr("panel.cli.removedPurged" if purged else "panel.cli.removedKept"))
    return errors.OK


def status_doc(runner, show=False):
    """``url`` — the open mode's address (3.3.7.5: under the secret prefix), masked unless ``show``."""
    from ..gw import sys as gsys
    cfg = conf.load()
    installed = os.path.exists(gsys.unit_path(UNIT))
    active = installed and _active(runner)
    st = conf.read_status() if active else None
    rec = auth.record()
    pub = cfg["public"]
    return {"ok": True, "installed": installed, "active": active, "mode": "public" if pub["on"] else "local",
            "listen": (st or {}).get("listen") or [], "local_url": conf.local_url(),
            "url": conf.public_url(cfg) if show else conf.public_url_masked(cfg),
            "url_masked": conf.public_url_masked(cfg),
            "public": dict(pub), "fingerprint": conf.fingerprint() if pub["on"] else "",
            "sessions": (st or {}).get("sessions") if st else None, "pid": (st or {}).get("pid") if st else None,
            "password": bool(rec), "login": rec.get("login"), "require_password": cfg["require_password"],
            "tunnel": conf.tunnel_command(), "proxy": proxy_doc(cfg, show),
            # 3.3.7.5 П-4: the panel and the links of people on the domain — the one condition (menu 18 reads it)
            "pages_domain": conf.pages_domain(cfg) or None}


def cmd_status(args):
    doc = status_doc(_r(), show=getattr(args, "show", False))
    if not doc["installed"]:
        state_line = tr("panel.cli.statusNotInstalled")
    elif not doc["active"]:
        state_line = tr("panel.cli.statusInactive", unit=UNIT)
    else:
        addrs = ", ".join("%s://%s" % (x["scheme"], x["addr"]) for x in doc["listen"] if "addr" in x)
        state_line = tr("panel.cli.statusRunning", addrs=addrs or tr("panel.cli.addrUnknown"))
    lines = [state_line, tr("panel.cli.statusTunnel", cmd=doc["tunnel"], url=doc["local_url"])]
    for x in doc["listen"]:
        if "error" in x:
            lines.append(tr("panel.cli.notListening", kind=x["kind"], error=x["error"]))
    # 3.3.7.5: one address of the panel — the domain's (nginx) is the main one, the open mode beside it is the way in
    # by the port; both under the one secret prefix, masked unless --show
    if doc["proxy"]["on"]:
        lines.append(tr("panel.cli.viaNginx", domain=doc["proxy"]["domain"] or tr("panel.cli.domainPlaceholder"),
                        path=doc["proxy"]["path"]))
    if doc["mode"] == "public":
        lines.append(tr("panel.cli.publicFallback" if doc["pages_domain"]
                        else "panel.cli.publicMode", url=doc["url"] or tr("panel.cli.addrUnknown")))
        lines.append(_site_cert_line() or tr("panel.cli.certFingerprint",
                                             fp=doc["fingerprint"] or tr("panel.cli.noCertYet")))
    else:
        lines.append(tr("panel.cli.publicModeOff"))
    if not getattr(args, "show", False) and (doc["mode"] == "public" or doc["proxy"]["on"]):
        lines.append(tr("panel.cli.showHint"))
    pw_key = {(True, False): "panel.cli.statusPasswordSet", (True, True): "panel.cli.statusPasswordSetTunnel",
              (False, False): "panel.cli.statusPasswordUnset", (False, True): "panel.cli.statusPasswordUnsetTunnel"}
    lines.append(tr(pw_key[(bool(doc["password"]), bool(doc["require_password"]))], login=doc["login"]))
    if doc["sessions"] is not None:
        lines.append(tr("panel.cli.sessions", n=doc["sessions"]))
    emit(args, doc, lines)
    return errors.OK


def _read_password():
    if sys.stdin is None:
        cmd = "echo '%s' | kaskadctl panel password" % tr("panel.cli.passwordWord")
        raise errors.KaskadError(tr("panel.cli.passwordFromStdin", cmd=cmd), code=errors.USAGE)
    if sys.stdin.isatty():
        first = getpass.getpass(tr("panel.cli.passwordPrompt"))
        if getpass.getpass(tr("panel.cli.passwordAgain")) != first:
            raise errors.KaskadError(tr("panel.cli.passwordsMismatch"), code=errors.USAGE)
        return first
    return sys.stdin.readline().rstrip("\r\n")


def cmd_password(args):
    pw = auth.generate_password() if args.generate else _read_password()
    res = auth.set_password(pw, args.login)
    util.log_tr("journal.panel.password_set_cli", login=res["login"])
    doc = {"ok": True, "login": res["login"], "updated": res["updated"]}
    lines = [tr("panel.cli.passwordDone", login=res["login"])]
    if args.generate:
        doc["password"] = pw
        lines.append(tr("panel.cli.passwordShown", password=pw))
        lines.append(tr("panel.cli.passwordSaveIt"))
    emit(args, doc, lines)
    return errors.OK


def cmd_link(args):
    cfg = conf.load()
    token = auth.issue_link(by="cli", ttl=args.ttl)
    url = login_url(token, cfg)
    ttl = max(60, min(int(args.ttl), 86400))
    outside = cfg["public"]["on"] or conf.proxy_url(cfg) is not None
    doc = {"ok": True, "url": url, "expires_in": ttl, "public": cfg["public"]["on"],
           "tunnel": None if outside else conf.tunnel_command()}
    lines = [tr("panel.cli.linkHead", min=ttl // 60), url]
    if not outside:
        lines.append(tr("panel.cli.linkTunnel", cmd=conf.tunnel_command()))
    emit(args, doc, lines)
    return errors.OK


def cmd_public(args):
    runner = _r()
    on = args.state == "on"
    cfg = conf.set_public(runner, on, port=args.port, force=args.force)
    reloaded = _reload(runner)
    url = conf.public_url(cfg) if args.show else conf.public_url_masked(cfg)
    doc = {"ok": True, "public": dict(cfg["public"], url=url, url_masked=conf.public_url_masked(cfg)),
           "reloaded": reloaded, "fingerprint": conf.fingerprint() if on else ""}
    if on:
        lines = [tr("panel.cli.publicMode", url=url),
                 tr("panel.cli.certFingerprint", fp=doc["fingerprint"]),
                 tr("panel.cli.publicSelfSigned"),
                 tr("panel.cli.publicSignIn", menu=tr(MENU_LINK))]
        if not auth.password_set():
            lines.append(tr("panel.cli.publicNoPassword", menu=tr(MENU_PASSWORD)))
        lines.append(tr("panel.cli.hostingFirewall", port=cfg["public"]["port"]))
    else:
        lines = [tr("panel.cli.publicOffDone", cmd=conf.tunnel_command())]
    if not reloaded:
        lines.append(tr("panel.cli.notRunning"))
    emit(args, doc, lines)
    return errors.OK


def proxy_doc(cfg, show=False):
    """The listener for nginx as the CLI and the menu show it: the secret path masked unless ``show``."""
    prx = cfg["proxy"]
    return {"on": prx["on"], "domain": prx["domain"], "socket": conf.proxy_sock_path(),
            "path": prx["path"] if show else conf.mask_path(prx["path"]),
            "url": (conf.proxy_url(cfg) if show else None)}


def cmd_proxy(args):
    on = args.state == "on"
    cfg = conf.set_proxy(on, path=args.path, domain=args.domain, new_path=args.new_path)
    reloaded = _reload(_r())
    doc = {"ok": True, "proxy": proxy_doc(cfg, args.show), "reloaded": reloaded}
    prx = cfg["proxy"]
    if on:
        lines = [tr("panel.cli.proxyOn", sock=conf.proxy_sock_path()),
                 tr("panel.cli.proxyPath" if args.show else "panel.cli.proxyPathMasked", path=doc["proxy"]["path"]),
                 tr("panel.cli.proxyDomain", domain=prx["domain"]) if prx["domain"]
                 else tr("panel.cli.proxyNoDomain")]
        if not auth.password_set():
            lines.append(tr("panel.cli.proxyNoPassword", menu=tr(MENU_PASSWORD)))
    else:
        lines = [tr("panel.cli.proxyOff")]
    if not reloaded:
        lines.append(tr("panel.cli.notRunning"))
    emit(args, doc, lines)
    return errors.OK


def cmd_require_password(args):
    cfg = conf.set_require_password(args.state == "on")
    reloaded = _reload(_r())
    util.log_tr("journal.panel.tunnel_password_on" if cfg["require_password"] else "journal.panel.tunnel_password_off")
    emit(args, {"ok": True, "require_password": cfg["require_password"], "reloaded": reloaded},
         tr("panel.cli.requirePasswordOn" if cfg["require_password"] else "panel.cli.requirePasswordOff"))
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("panel", help=tr("panel.cli.helpPanel"))
    sub = p.add_subparsers(dest="panel_cmd", metavar=tr("panel.cli.metavarCommand"))
    sub.required = True
    p1 = sub.add_parser("serve", help=tr("panel.cli.helpServe"))
    p1.add_argument("--port", type=int, default=None, help=tr("panel.cli.helpServePort", port=conf.LOCAL_PORT))
    p1.set_defaults(func=cmd_serve)
    add_json_flag(sub.add_parser("install", help=tr("panel.cli.helpInstall"))).set_defaults(func=cmd_install)
    p1 = add_json_flag(sub.add_parser("unit", help=tr("panel.cli.helpUnit")))
    p1.add_argument("--refresh", action="store_true", required=True, help=tr("panel.cli.helpRefresh"))
    p1.add_argument("--quiet", action="store_true", help=tr("panel.cli.helpQuiet"))
    p1.set_defaults(func=cmd_unit)
    p1 = add_json_flag(sub.add_parser("remove", help=tr("panel.cli.helpRemove")))
    p1.add_argument("--purge", action="store_true", help=tr("panel.cli.helpPurge"))
    p1.set_defaults(func=cmd_remove)
    p1 = add_json_flag(sub.add_parser("status", help=tr("panel.cli.helpStatus")))
    p1.add_argument("--show", action="store_true", help=tr("panel.cli.helpShow"))
    p1.set_defaults(func=cmd_status)
    p1 = add_json_flag(sub.add_parser("password", help=tr("panel.cli.helpPassword")))
    p1.add_argument("--login", default=None, help=tr("panel.cli.helpLogin"))
    p1.add_argument("--generate", action="store_true", help=tr("panel.cli.helpGenerate"))
    p1.set_defaults(func=cmd_password)
    p1 = add_json_flag(sub.add_parser("link", help=tr("panel.cli.helpLink")))
    p1.add_argument("--ttl", type=int, default=auth.LINK_TTL, help=tr("panel.cli.helpTtl"))
    p1.set_defaults(func=cmd_link)
    p1 = add_json_flag(sub.add_parser("public", help=tr("panel.cli.helpPublic")))
    p1.add_argument("state", choices=("on", "off"))
    p1.add_argument("--port", default=None, help=tr("panel.cli.helpPublicPort"))
    p1.add_argument("--force", action="store_true", help=tr("panel.cli.helpForce"))
    p1.add_argument("--show", action="store_true", help=tr("panel.cli.helpShow"))
    p1.set_defaults(func=cmd_public)
    p1 = add_json_flag(sub.add_parser("proxy", help=tr("panel.cli.helpProxy")))
    p1.add_argument("state", choices=("on", "off"))
    p1.add_argument("--path", default=None, help=tr("panel.cli.helpPath"))
    p1.add_argument("--new-path", action="store_true", help=tr("panel.cli.helpNewPath"))
    p1.add_argument("--domain", default=None, help=tr("panel.cli.helpDomain"))
    p1.add_argument("--show", action="store_true", help=tr("panel.cli.helpShow"))
    p1.set_defaults(func=cmd_proxy)
    p1 = add_json_flag(sub.add_parser("require-password", help=tr("panel.cli.helpRequirePassword")))
    p1.add_argument("state", choices=("on", "off"))
    p1.set_defaults(func=cmd_require_password)
