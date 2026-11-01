"""The private nginx part of the cover site (wave 4.0-C).

Our files: ``sites-available/kaskad`` (the site; it contains the secret panel
prefix, therefore mode 0600) and ``sites-available/kaskad-acme`` (domain mode
on someone else's nginx: only ``/.well-known/acme-challenge/`` of our domain on
:80), each with its link in ``sites-enabled``.  Every change of them is one
transaction: the previous files and links come back when ``nginx -t`` fails.

nginx is «owned» (``owned()``) when Kaskad installed it (``/etc/kaskad/site/pkgs``)
and nothing but Debian's stock ``default`` and our files is enabled.  Only then
Kaskad stops it in domain mode (certbot ``--standalone`` needs :80), unlinks
``sites-enabled/default`` for the site (marker ``/etc/kaskad/site/nginx-default``,
the link comes back on ``site remove``) and answers unknown names on :80 with 444.
Every system command uses a runner, so the same code is exercised by the
offline tests.
"""

import os
import re
import shutil

from .. import paths, util
from ..errors import KaskadError
from ..gw import sys as gsys
from ..i18n import tr
from . import model

VHOST = "/etc/nginx/sites-available/kaskad"
ENABLED = "/etc/nginx/sites-enabled/kaskad"
ACME_VHOST = "/etc/nginx/sites-available/kaskad-acme"
ACME_ENABLED = "/etc/nginx/sites-enabled/kaskad-acme"
DEFAULT_AVAILABLE = "/etc/nginx/sites-available/default"
DEFAULT_ENABLED = "/etc/nginx/sites-enabled/default"
SITES_ENABLED = "/etc/nginx/sites-enabled"
CONF_D = "/etc/nginx/conf.d"
ACME_ROOT = "/var/www/kaskad-acme"
MARK = "# Kaskad 3: site vhost."
ACME_MARK = "# Kaskad 3: ACME vhost."
OUR_NAMES = ("kaskad", "kaskad-acme")
SITE_UFW_COMMENT = "kaskad-site"

_VERSION = re.compile(r"nginx/(\d+)\.(\d+)\.(\d+)")
_NEIGHBOURS = (("goamnezia", "goAmnezia"), ("gotelegram", "goTelegram"),
               ("govless", "goVLESS"), ("3x-ui", "3x-ui"), ("x-ui", "3x-ui"))


def vhost_path():
    return paths.P(VHOST)


def enabled_path():
    return paths.P(ENABLED)


def acme_root():
    return paths.P(ACME_ROOT)


def acme_vhost_path():
    return paths.P(ACME_VHOST)


def acme_enabled_path():
    return paths.P(ACME_ENABLED)


def default_enabled_path():
    return paths.P(DEFAULT_ENABLED)


def default_available_path():
    return paths.P(DEFAULT_AVAILABLE)


def default_mark_path():
    """Our record that Kaskad unlinked Debian's ``sites-enabled/default`` (its link target inside)."""
    return os.path.join(model.site_dir(), "nginx-default")


def _version(runner):
    if not runner.which("nginx"):
        return None
    res = runner.run(["nginx", "-v"])
    got = _VERSION.search((res.err or "") + "\n" + (res.out or ""))
    return got.group(0).split("/", 1)[1] if got else None


def _new_http2(version):
    try:
        return tuple(int(x) for x in version.split(".")) >= (1, 25, 1)
    except (AttributeError, ValueError):
        return False


def has_ipv6():
    """Whether nginx must accept the site's AAAA traffic too.

    A non-empty kernel table is the same inexpensive check used by goAmnezia.
    ``paths.P`` keeps the decision controllable in the offline filesystem.
    """
    try:
        return os.path.getsize(paths.P("/proc/net/if_inet6")) > 0
    except OSError:
        return False


def _escape(value):
    """All inputs come from validators; this keeps malformed state harmless."""
    return str(value).replace("\\", "").replace("\n", "").replace("\r", "").replace(";", "")


# The two routes that take a copy of the settings (kaskad.panel.server BIG_BODY_PATHS) — 20 MB, the rest of the panel 1 MB.
BACKUP_PATHS = ("api/backup/preview", "api/backup/import")
BACKUP_BODY = "20m"

# The browser test (k3/handoff/SPEC-browser-channel.md §4.1, NS-B15): the upload is passed on as it comes (no temp
# file: else nginx is measured, not the path) with an HTTP/2 window of 8 MiB per stream; that buffer is taken before
# the panel checks the login, so the requests of this location at once are limited — 8 in all (the zone is keyed by
# the server name) and 5 per address; the body timeout is a pause between two reads (the panel cuts the whole body
# at 15 s). The download is not buffered either and nginx reads the panel's socket in 64 KiB pieces; ``proxy_buffers
# 8 64k`` is only for the check of nginx -t, which holds even unbuffered: the busy buffers (2 × proxy_buffer_size)
# must be less than all proxy_buffers minus one, and the default 8 × 4k refuses the whole file (stand T-3310 B-1,
# nginx 1.24 of Ubuntu 24.04). Unbuffered nginx takes none of them — one 64 KiB buffer per request.
SPEED_UP, SPEED_DOWN = "api/host/speed/up", "api/host/speed/down"
SPEED_ZONES = ("limit_conn_zone $server_name zone=kaskad_speed:1m;\n"
               "limit_conn_zone $binary_remote_addr zone=kaskad_speed_ip:10m;")
_EXACT = {SPEED_UP: ["    client_max_body_size 64m;", "    client_body_buffer_size 8m;",
                     "    proxy_request_buffering off;", "    client_body_timeout 15s;",
                     "    limit_conn kaskad_speed 8;", "    limit_conn kaskad_speed_ip 5;",
                     "    limit_conn_status 429;"],
          SPEED_DOWN: ["    proxy_buffering off;", "    proxy_buffer_size 64k;", "    proxy_buffers 8 64k;"]}
EXACT_PATHS = BACKUP_PATHS + (SPEED_UP, SPEED_DOWN)


def _proxy_block(prefix, pages=False, exact=None, addr="$server_addr"):
    """The panel under its secret prefix (or the pages of people, ``pages``); ``exact`` — one path of the panel
    («api/backup/import», «api/host/speed/up») in its own exact location with its own body limit (``_EXACT``; the
    copies of the settings — 20 MB). ``addr`` — the nginx variable of the address of the server the visitor came to
    (``$proxy_protocol_server_addr`` behind xray): ``X-Kaskad-Server-Addr``, set here so that the visitor's own header
    of that name never reaches the panel (proxy_set_header replaces it) — «Защита доступа» of «Приём подключений» reads
    it (REV-mip-s34 С-2)."""
    from ..panel import conf
    if exact:
        loc = "location = /%s/%s {" % (_escape(prefix), _escape(exact))
        extra = list(_EXACT.get(exact) or ["    client_max_body_size %s;" % BACKUP_BODY])
        uri = ":/%s" % _escape(exact)
    else:
        loc = "location ^~ /p/ {" if pages else "location ^~ /%s/ {" % _escape(prefix)
        extra = ["    limit_req zone=kaskad_pages burst=40 nodelay;", "    limit_req_status 404;"] if pages else [
            "    client_max_body_size 1m;"]
        uri = ":" if pages else ":/"
    return "\n".join([loc, "    proxy_pass http://unix:%s%s;" % (conf.proxy_sock_path(), uri),
                      "    proxy_set_header Host $host;", "    proxy_set_header X-Real-IP $remote_addr;",
                      "    proxy_set_header X-Forwarded-For $remote_addr;",
                      "    proxy_set_header X-Forwarded-Proto https;",
                      "    proxy_set_header X-Forwarded-Host $host;",
                      "    proxy_set_header X-Kaskad-Server-Addr %s;" % addr,
                      "    proxy_set_header X-Forwarded-Prefix /%s;" % _escape(prefix) if not pages else
                      "    proxy_set_header X-Forwarded-Prefix /;", "    proxy_http_version 1.1;",
                      "    proxy_read_timeout 30m;", "    proxy_send_timeout 30m;"] + extra +
                     ["    access_log off;", "    error_log /dev/null crit;", "}"])


def _reject80(ipv6):
    """Unknown names on :80 get no answer at all (444) instead of a redirect to our domain — except the checks of
    Let's Encrypt for the IP certificate of the node (kaskad.ipcert: Host is the address, the file is in our webroot)."""
    return ("\nserver {\n    listen 80 default_server;" + ("\n    listen [::]:80 default_server;" if ipv6 else "") +
            "\n    server_name _;\n    access_log off;"
            "\n    location ^~ /.well-known/acme-challenge/ {\n        root %s;\n    }"
            "\n    location / {\n        return 444;\n    }\n}\n" % acme_root())


def _tls_lines(cert, key):
    return """ssl_certificate %s;
    ssl_certificate_key %s;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305';
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:kaskad_ssl:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;""" % (cert, key)


def _behind_lines(sock):
    """The site behind xray of the gateway (VLESS + TLS on 443, site.front): plain HTTP on a Unix socket, the PROXY
    protocol from xray's fallback gives the visitor's address (bans and limits of /p/ by it), redirects stay relative
    (nginx would write http:// — the visitor is on https)."""
    return """listen unix:%s proxy_protocol;
    set_real_ip_from unix:;
    real_ip_header proxy_protocol;
    absolute_redirect off;""" % _escape(sock)


def render(domain, panel_path, reject_handshake=False, version=None, ipv6=None, reject80=False, behind=None):
    """Return our complete vhost.  The caller writes it atomically and tests it.  ``behind`` — the path of the Unix
    socket when xray of the gateway holds 443 (VLESS + TLS) and hands the site everything else (site.front); nginx
    listens on no TCP 443 then and the TLS lines go (xray terminates TLS)."""
    domain, panel_path = _escape(domain), _escape(panel_path)
    cert, key = model.fullchain_path(), model.privkey_path()
    ipv6 = has_ipv6() if ipv6 is None else bool(ipv6)
    listen80 = "listen 80;" + ("\n    listen [::]:80;" if ipv6 else "")
    if behind:
        h2 = _behind_lines(behind)
        reject_handshake = False
    elif _new_http2(version):
        h2 = "listen 443 ssl;" + ("\n    listen [::]:443 ssl;" if ipv6 else "") + "\n    http2 on;"
    else:
        h2 = "listen 443 ssl http2;" + ("\n    listen [::]:443 ssl http2;" if ipv6 else "")
    tls = "" if behind else _tls_lines(cert, key)
    addr = "$proxy_protocol_server_addr" if behind else "$server_addr"     # behind xray: the PROXY protocol's
    reject = ""
    if reject_handshake:
        reject = "\nserver {\n    listen 443 ssl default_server;"
        if ipv6:
            reject += "\n    listen [::]:443 ssl default_server;"
        reject += "\n    ssl_reject_handshake on;\n}\n"
    if reject80:
        reject += _reject80(ipv6)
    return """# Kaskad 3: site vhost.  Contains the private panel path; mode 0600.
# This file is included from nginx http {}, so the named zone is valid here.
limit_req_zone $binary_remote_addr zone=kaskad_pages:10m rate=5r/s;
%(speed_zones)s

server {
    %(listen80)s
    server_name %(domain)s;
    location ^~ /.well-known/acme-challenge/ {
        root %(acme)s;
    }
    location / {
        return 301 https://$server_name$request_uri;
    }
}

server {
    %(h2)s
    server_name %(domain)s;
    root %(root)s;
    index index.html;
    %(tls)s
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    server_tokens off;
    disable_symlinks on from=$document_root;

%(panel)s

%(pages)s

    # Guesses of a panel path look exactly like a missing page and are not logged.
    location ~ "^/(app-[A-Za-z0-9_-]{86}|app-[0-9a-f]{12,128})(/|$)" {
        access_log off;
        error_log /dev/null crit;
        return 404;
    }
    location ~ /\\. {
        return 404;
    }
    location / {
        try_files $uri $uri/ =404;
    }
}
%(reject)s""" % {"domain": domain, "acme": acme_root(), "h2": h2, "root": paths.site_root(),
               "tls": tls, "listen80": listen80,
               "panel": "\n".join([_proxy_block(panel_path, addr=addr)] +
                                  [_proxy_block(panel_path, exact=x, addr=addr) for x in EXACT_PATHS]),
               "pages": _proxy_block(panel_path, pages=True, addr=addr), "reject": reject, "speed_zones": SPEED_ZONES}


def render_acme(domain, ipv6=None):
    """Domain mode on someone else's nginx: only the HTTP-01 answers of our domain on :80, nothing else. ``domain`` —
    one name or several (a change of the domain: the old name and the new one while the new one gets its
    certificate — ops.change_domain)."""
    domain = " ".join(_escape(x) for x in ([domain] if isinstance(domain, str) else domain))
    ipv6 = has_ipv6() if ipv6 is None else bool(ipv6)
    listen = "listen 80;" + ("\n    listen [::]:80;" if ipv6 else "")
    return """%(mark)s  Only Let's Encrypt checks of our domain on :80.
server {
    %(listen)s
    server_name %(domain)s;
    location ^~ /.well-known/acme-challenge/ {
        root %(acme)s;
    }
    location / {
        return 404;
    }
}
""" % {"mark": ACME_MARK, "listen": listen, "domain": domain, "acme": acme_root()}


def ours():
    return MARK in (util.read_text(vhost_path()) or "")


def acme_ours():
    """Our ACME vhost is written and enabled: nginx answers Let's Encrypt on :80 for our domain."""
    return ACME_MARK in (util.read_text(acme_vhost_path()) or "") and os.path.lexists(acme_enabled_path())


def _stock_default(path):
    """``sites-enabled/default`` as the Debian package ships it: a link to ``sites-available/default``."""
    if not os.path.islink(path):
        return False
    target = os.readlink(path)
    return os.path.basename(target) == "default" and \
        os.path.basename(os.path.dirname(target.rstrip("/")) or "sites-available") == "sites-available"


_SERVER = re.compile(r"(?m)^\s*(?:server\s*\{|listen\s)")


def foreign_sites():
    """Enabled configurations that are neither ours nor Debian's stock ``default`` (names, for the journal)."""
    found = []
    d = paths.P(SITES_ENABLED)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    for name in names:
        if name in OUR_NAMES or (name == "default" and _stock_default(os.path.join(d, name))):
            continue
        found.append("sites-enabled/" + name)
    c = paths.P(CONF_D)
    try:
        names = sorted(os.listdir(c))
    except OSError:
        names = []
    for name in names:
        if name.endswith(".conf") and _SERVER.search(util.read_text(os.path.join(c, name)) or ""):
            found.append("conf.d/" + name)
    return found


def owned():
    """nginx is Kaskad's alone: installed by Kaskad and serving nothing but Debian's default and our files."""
    return "nginx" in model.read_pkgs() and not foreign_sites()


def default_page():
    """Debian's «Welcome to nginx!» is enabled (it answers http://<IP>/ and every unknown name on :80)."""
    return os.path.lexists(default_enabled_path())


def _snapshot(path):
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        return ("link", os.readlink(path), 0)
    try:
        with open(path, "rb") as f:
            return ("file", f.read(), os.stat(path).st_mode & 0o777)
    except OSError:
        return None


def _restore(path, old):
    try:
        if os.path.lexists(path):
            os.unlink(path)
        if old is None:
            return
        if old[0] == "link":
            os.symlink(old[1], path)
        else:
            util.atomic_write(path, old[1], old[2] or 0o600)
    except OSError:
        pass


def _tail(res, panel_path=""):
    lines = [ln.strip() for ln in (res.err or res.out or "").splitlines() if ln.strip()]
    text = " | ".join(lines[-3:]) or tr("site.nginx.no_detail")
    hidden = tr("site.nginx.hidden_path")
    if panel_path:                      # "".replace("", x) puts x between every two characters
        text = text.replace(panel_path, hidden)
    return re.sub(r"app-[A-Za-z0-9_-]{12,160}", lambda m: "app-" + hidden, text)[:500]


def active(runner):
    return bool(runner.which("nginx")) and runner.run(["systemctl", "is-active", "nginx"]).out.strip() == "active"


def status(runner):
    installed = bool(runner.which("nginx"))
    return {"installed": installed, "active": active(runner) if installed else False,
            "version": _version(runner) if installed else None, "ours": ours(),
            "owned": installed and owned(), "acme": acme_ours(), "default_page": installed and default_page()}


def ensure(runner):
    """Install nginx only on demand; return packages installed by Kaskad."""
    if runner.which("nginx"):
        return []
    got = gsys.apt_install(runner, ["nginx"])
    if "nginx" in got:
        model.add_pkgs(["nginx"])
        util.log_tr("site.nginx.log_installed")
    return got


def test(runner):
    return runner.run(["nginx", "-t"], timeout=60)


def _included(runner, names=(VHOST, ENABLED, MARK)):
    """Whether nginx's actual ``http {}`` configuration loaded our vhost.

    A successful ``nginx -t`` alone is not proof when a custom nginx.conf does
    not include sites-enabled.  ``-T`` prints every file it read, including the
    path or, on some builds, the vhost's marker.
    """
    res = runner.run(["nginx", "-T"], timeout=60)
    text = (res.out or "") + "\n" + (res.err or "")
    return res.ok and any(n in text for n in names)


def _error_is_ours(res):
    """The output of nginx -t names either the source vhost or its symlink."""
    text = (res.out or "") + "\n" + (res.err or "")
    return any(p in text for p in (VHOST, ENABLED, vhost_path(), enabled_path(),
                                   ACME_VHOST, ACME_ENABLED, acme_vhost_path(), acme_enabled_path()))


def reload(runner, start=True):
    """reload a running nginx; a stopped one is started only when ``start`` (a removal never starts it)."""
    if active(runner):
        verb = "reload"
    elif start:
        verb = "start"
    else:
        return None
    res = runner.run(["systemctl", verb, "nginx"])
    if not res.ok:
        raise KaskadError(tr("site.nginx.reload_failed" if verb == "reload" else "site.nginx.start_failed",
                             error=_tail(res)))
    return verb


def _put(path, want):
    """``want``: None — absent; ("file", text) — a private file; ("link", target) — a symlink."""
    if want is None:
        if os.path.lexists(path):
            os.unlink(path)
        return
    util.ensure_dir(os.path.dirname(path), 0o755)
    if want[0] == "file":
        util.atomic_write(path, want[1], 0o600)
        return
    if os.path.lexists(path):
        if os.path.islink(path) and os.readlink(path) == want[1]:
            return
        os.unlink(path)
    os.symlink(want[1], path)


def _apply(runner, changes, secret="", start=True, rollback=True, include=None, undo_failed_reload=False):
    """One transaction over our nginx files: write all, ``nginx -t``, then reload (or start when ``start``).

    ``changes`` — [(path, want)] for ``_put``.  On a failed test every path gets back exactly what it was (``rollback``)
    and NginxTestError is raised; ``rollback=False`` keeps the change and skips the reload (a removal must not be
    blocked by someone else's broken file).  ``include`` — names one of which ``nginx -T`` must print (proof that
    nginx.conf reads sites-enabled).  ``undo_failed_reload`` — a failed reload/start after a passed test also puts every
    path back (the files on disk stay what the running nginx serves, so the next attempt sees the difference and
    tries again) and its KaskadError is raised.  -> "reload" | "start" | None.
    """
    olds = [(p, _snapshot(p)) for p, _ in changes]

    def undo():
        for p, old in reversed(olds):
            _restore(p, old)
    try:
        for p, want in changes:
            _put(p, want)
    except OSError as e:
        undo()
        raise KaskadError(tr("site.nginx.write_failed", error=e))
    res = test(runner)
    if not res.ok:
        if not rollback:
            util.log_tr("site.nginx.log_test_after_remove", error=_tail(res))
            return None
        undo()
        raise NginxTestError(_tail(res, secret), ours=_error_is_ours(res))
    if include and not _included(runner, include):
        undo()
        raise NginxTestError(tr("site.nginx.not_included", path=SITES_ENABLED))
    try:
        return reload(runner, start=start)
    except KaskadError:
        if undo_failed_reload:
            undo()
        raise


def _ours_away():
    return [(vhost_path(), None), (enabled_path(), None), (acme_vhost_path(), None), (acme_enabled_path(), None)]


def write(runner, domain, panel_path, reject_handshake=False, reject80=False, drop_default=False, behind=None):
    """The site: our vhost (and no ACME-only vhost), tested; exact old files restored on failure.

    ``drop_default`` unlinks Debian's ``sites-enabled/default`` in the same transaction and records it in
    ``default_mark_path()`` (only for nginx that Kaskad owns; ``restore_default`` puts the link back). ``behind`` — the
    socket when xray holds 443 (``render``)."""
    changes = [(vhost_path(), ("file", render(domain, panel_path, reject_handshake, _version(runner),
                                               reject80=reject80, behind=behind))),
               (enabled_path(), ("link", vhost_path())), (acme_enabled_path(), None), (acme_vhost_path(), None)]
    mark = None
    if drop_default and os.path.lexists(default_enabled_path()):
        changes.append((default_enabled_path(), None))
        mark = os.readlink(default_enabled_path()) if os.path.islink(default_enabled_path()) else DEFAULT_AVAILABLE
    had_mark = os.path.exists(default_mark_path())
    if mark and not had_mark:
        util.ensure_dir(model.site_dir(), 0o755)
        util.atomic_write(default_mark_path(), mark + "\n", 0o644)
    try:
        return _apply(runner, changes, secret=panel_path, start=True, include=(VHOST, ENABLED, MARK))
    except KaskadError:
        if mark and not had_mark:
            _restore(default_mark_path(), None)
        raise


_BEHIND_RE = re.compile(r"(?m)^\s*listen unix:(\S+) proxy_protocol;")


def refresh(runner, domain, panel_path, dry=False):
    """BC2a: our site vhost again by this version's template, with the arrangement it has now (behind xray on the
    socket or on 443, the TLS handshake rejection, the 444 on :80, IPv6 — read from the file, nothing is decided
    anew), so that an update brings new ``location`` blocks to a node with a site. Only our vhost of this very site
    (its ``server_name`` and panel prefix are in it — a change of either still in progress is not touched). The same
    text — nothing is written and nginx is not reloaded. Else one transaction: write, ``nginx -t``, reload of a running
    nginx (a stopped one is not started); a failed test — the previous file back, no reload (NginxTestError); a failed
    reload after a passed test — the previous file back too (KaskadError), so the next refresh tries again.
    ``dry`` — only the comparison: {"changed": False, "would": bool}.
    -> {"changed": bool, "reload": "reload" | None, "skipped": "none" | "foreign" (only when nothing was done)}."""
    old = util.read_text(vhost_path())
    if not old or MARK not in old:
        return {"changed": False, "reload": None, "skipped": "none"}
    d, p = _escape(domain or ""), _escape(panel_path or "")
    if not d or not p or ("server_name %s;" % d) not in old or ("location ^~ /%s/ {" % p) not in old:
        return {"changed": False, "reload": None, "skipped": "foreign"}
    m = _BEHIND_RE.search(old)
    new = render(domain, panel_path, reject_handshake="ssl_reject_handshake on;" in old, version=_version(runner),
                 ipv6="listen [::]:80" in old, reject80="return 444;" in old, behind=m.group(1) if m else None)
    if dry or new == old:
        return {"changed": False, "reload": None, "would": new != old}
    verb = _apply(runner, [(vhost_path(), ("file", new))], secret=panel_path, start=False,
                  include=(VHOST, ENABLED, MARK), undo_failed_reload=True)
    return {"changed": True, "reload": verb}


def write_acme(runner, domain):
    """Domain mode on someone else's nginx: the site vhost away, the ACME-only vhost in, one test.  A stopped nginx
    is not started (it is not ours)."""
    util.ensure_dir(acme_root(), 0o755)
    changes = [(acme_vhost_path(), ("file", render_acme(domain))), (acme_enabled_path(), ("link", acme_vhost_path())),
               (enabled_path(), None), (vhost_path(), None)]
    return _apply(runner, changes, start=False, include=(ACME_VHOST, ACME_ENABLED, ACME_MARK))


def acme_extra(runner, names):
    """A change of the domain (ops.change_domain): the ACME-only vhost answers Let's Encrypt for ``names`` on :80 NEXT
    to whatever serves now — the site vhost of the old domain stays as it is (nothing else of nginx changes), one
    test, a reload of a running nginx. -> undo(): exactly the previous ACME files back (and a reload); NginxTestError
    when nginx refused it (the previous files are back already)."""
    util.ensure_dir(acme_root(), 0o755)
    paths_ = (acme_vhost_path(), acme_enabled_path())
    olds = [(p, _snapshot(p)) for p in paths_]
    _apply(runner, [(acme_vhost_path(), ("file", render_acme(list(names)))), (acme_enabled_path(),
                                                                                ("link", acme_vhost_path()))],
           start=False, include=(ACME_VHOST, ACME_ENABLED, ACME_MARK))

    def undo():
        for p, old in olds:
            _restore(p, old)
        try:
            if test(runner).ok:
                reload(runner, start=False)
        except KaskadError as e:
            util.log_tr("site.nginx.log_acme_reload", error=e)
    return undo


def remove(runner, rollback=True):
    """Our vhosts (site and ACME) away only after nginx accepted the result; a stopped nginx stays stopped."""
    if not any(os.path.lexists(p) for p, _ in _ours_away()):
        return None
    if not runner.which("nginx"):
        for p, _ in _ours_away():
            _restore(p, None)
        return None
    return _apply(runner, _ours_away(), start=False, rollback=rollback)


def stop(runner):
    """Kaskad's own nginx off for good — domain mode or no domain (``certbot --standalone`` renews on :80, now and
    after a reboot): ``systemctl disable --now nginx``.  Never a pause around one certbot run: the site going on again
    brings it back with ``enable`` (3.3.0 T-M6 №2)."""
    res = runner.run(["systemctl", "disable", "--now", "nginx"])
    if not res.ok:
        raise KaskadError(tr("site.nginx.stop_failed", error=_tail(res)))
    return True


def enable(runner):
    """nginx serves our site: running now AND with the system (``systemctl enable --now nginx``) — a site that only
    ``start``-ed nginx left it disabled since domain mode and died with the next reboot (3.3.0 T-M6 №2).
    -> True when systemctl agreed."""
    res = runner.run(["systemctl", "enable", "--now", "nginx"])
    if not res.ok:
        util.log_tr("site.nginx.log_enable_failed", error=_tail(res))
    return res.ok


def restore_default(runner):
    """``site remove``: Debian's ``sites-enabled/default`` back when Kaskad unlinked it and the file is still there.
    -> True if the link came back."""
    mark = default_mark_path()
    if not os.path.exists(mark):
        return False
    target = (util.read_text(mark) or "").strip() or DEFAULT_AVAILABLE
    back = False
    if not os.path.lexists(default_enabled_path()) and os.path.exists(default_available_path()):
        try:
            os.symlink(target, default_enabled_path())
            back = True
        except OSError:
            back = False
    _restore(mark, None)
    if back and runner.which("nginx") and active(runner):
        if test(runner).ok:
            reload(runner, start=False)
        else:
            _restore(default_enabled_path(), None)
            back = False
    return back


class NginxTestError(KaskadError):
    """A failed nginx configuration test, with the source when nginx named it."""

    def __init__(self, message, ours=False):
        KaskadError.__init__(self, message)
        self.ours = bool(ours)


def neighbours(runner=None):
    """Known projects whose vhost owns 443.  We never change their files."""
    found = []
    d = paths.P("/etc/nginx/sites-enabled")
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for name in names:
        low = name.lower() + "\n" + (util.read_text(os.path.join(d, name)) or "").lower()
        for needle, title in _NEIGHBOURS:
            if needle in low and title not in found:
                found.append(title)
    if runner is not None and runner.which("nginx"):
        res = runner.run(["nginx", "-T"], timeout=60)
        low = (res.out or "").lower()
        for needle, title in _NEIGHBOURS:
            if needle in low and title not in found:
                found.append(title)
    return found


_CONF_FILE = re.compile(r"^# configuration file (.+):\s*$")


def _sections(text):
    """``nginx -T`` -> {file: [lines]} (lines before the first header under None)."""
    out, cur = {}, None
    for ln in (text or "").replace("\r", "").splitlines():
        m = _CONF_FILE.match(ln)
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(ln)
    return out


def _foreign_listen(runner, port, skip_default=False):
    """Does any file but ours (and Debian's default when ``skip_default``) listen on ``port``?  An inspection that
    failed counts as «yes»: a default_server is never seized blindly."""
    if runner is None or not runner.which("nginx"):
        return False
    res = runner.run(["nginx", "-T"], timeout=60)
    if not res.ok:
        return True
    skip = set((VHOST, ENABLED, ACME_VHOST, ACME_ENABLED, vhost_path(), enabled_path(), acme_vhost_path(),
                acme_enabled_path()))
    if skip_default:
        skip.update((DEFAULT_ENABLED, default_enabled_path()))
    pat = re.compile(r"^\s*listen\s+(?:\S*:)?%d(?:[\s;]|$)" % port)
    for name, lines in _sections(res.out).items():
        if name in skip:
            continue
        if any(pat.match(ln) for ln in lines if not ln.lstrip().startswith("#")):
            return True
    return False


def _has_other_443(runner):
    if neighbours(runner):
        return True
    return _foreign_listen(runner, 443)


def can_reject_handshake(runner):
    return not _has_other_443(runner)


def can_reject80(runner, skip_default=False):
    """The 444 default server on :80 only when no other vhost listens there (``skip_default``: Debian's default goes
    away in the same transaction)."""
    return not _foreign_listen(runner, 80, skip_default=skip_default)


def ufw_allow443(runner):
    return gsys.ufw_allow(runner, 443, "tcp", SITE_UFW_COMMENT)


def ufw_delete443(runner):
    return gsys.ufw_delete(runner, 443, "tcp", SITE_UFW_COMMENT)


def remove_roots():
    """The exact two Kaskad web roots, never a configurable user path."""
    for p in (paths.site_root(), acme_root()):
        shutil.rmtree(p, ignore_errors=True)
