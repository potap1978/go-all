"""Routes of the web panel for the domain and its certificate (docs/k3/SPEC-wave4.md §4.1) — imported by the server
through ``kaskad.panel.MODULES``; every route is the admin's, changes need ``X-Kaskad-Admin: 1``:

    GET    /api/site            STATUS
    PUT    /api/site/domain     {"domain": "vpn.example.com"}          -> {ok, site: STATUS, dns: DNS, affected}
    DELETE /api/site/domain                                            -> {ok, site: STATUS, affected, kept}
    POST   /api/site/dns        {"wait": 0..300}                       -> DNS (synchronous: up to 5 minutes)
    PUT    /api/site/email      {"email": "a@b.c" | ""}                -> {ok, email}
    POST   /api/site/cert       {"staging": false}                     -> {ok, issued, kept, cert, warnings} (long:
                                                                          apt and certbot — the client waits 10 min)
    POST   /api/site/cert/renew                                        -> {ok, renewed, cert}
    PUT    /api/site/use        {"kind": "sstp"|"ocserv", "on": bool, "dry_run": bool}  -> {ok, use, affected}
    PUT    /api/site/mode       {"mode": "site"|"domain", "template"?: id|"random"}
    GET    /api/site/templates?category&query&page  -> items with «health»: {state, text} from the cache, no network
                                                          (null — not checked or too old); GET /api/site/categories
    POST   /api/site/templates/check {"ids": [id, …]} (≤ 25: the visible page) -> {ok, items: {id: {state, text}}}
                                     — is each template still there (site.tplhealth: git ls-remote and the preview,
                                     8 at once, ≤ 40 s): alive | gone | no_preview | unknown; unknown ids — unknown
    POST   /api/site/template   {"id": id|"random"}; GET /api/site/panel-url; POST /api/site/panel-path/rotate
    POST   /api/site/check

The answers are the JSON of ``kaskadctl site … --json`` (kaskad.site.ops). A refusal of the issue or the renewal is
HTTP 409 with ``{"ok": false, "reason", "text", "retry_after"?, "error", "code"}``; other errors —
``{"ok": false, "error", "code"}`` with 400 (arguments), 409 (busy, conflict), 500.
"""

from .. import errors, snapshot
from ..i18n import tr
from ..panel.routes import HttpError, json_response, route
from . import ops


def _runner():
    from ..panel import api
    return api.runner()


def _http_error(e):
    code = getattr(e, "code", errors.ERROR)
    status = {errors.USAGE: 400, errors.CONFLICT: 409, errors.LOCKED: 409, errors.UNSUPPORTED: 409}.get(code, 500)
    return HttpError(status, str(e), code=code, data=getattr(e, "data", None) or None)


def _refusal(e):
    body = dict(e.doc, error=e.doc["text"], code=e.code)
    return json_response(body, status=409)


def _bool(body, key, default=False):
    v = body.get(key, default)
    if not isinstance(v, bool):
        raise HttpError(400, tr("site.panel.bool", field=key), code=errors.USAGE)
    return v


def _call(fn, *a, **kw):
    try:
        res = fn(*a, **kw)
    except ops.Refusal as e:
        return _refusal(e)
    except errors.KaskadError as e:
        raise _http_error(e)
    snapshot.bump()
    return json_response(res)


@route("GET", r"/api/site", runs=True)                # ss, iptables, systemctl is-active nginx, nginx -v
def get_site(req):
    try:
        return json_response(ops.status(_runner()))
    except errors.KaskadError as e:
        raise _http_error(e)


@route("PUT", r"/api/site/domain")
def put_domain(req):
    body = req.json()
    name = body.get("domain")
    if not isinstance(name, str) or not name.strip():
        raise HttpError(400, tr("site.panel.domain"), code=errors.USAGE)
    return _call(ops.set_domain, _runner(), name)


@route("DELETE", r"/api/site/domain")
def delete_domain(req):
    try:
        body = req.json() or {}
    except (HttpError, ValueError):
        body = {}
    body = body if isinstance(body, dict) else {}
    return _call(ops.clear, _runner(), way_in=True, yes=_bool(body, "yes"), vless_site=_vless_site(body))


def _vless_site(body):
    """``vless_site`` of the body (§3.6 need_pick): a host name of the cover, or None."""
    v = body.get("vless_site")
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip() or len(v) > 253:
        raise HttpError(400, tr("site.panel.template", field="vless_site"), code=errors.USAGE)
    return v.strip()


@route("POST", r"/api/site/dns")
def post_dns(req):
    body = req.json()
    wait = body.get("wait", 0)
    if isinstance(wait, bool) or not isinstance(wait, int) or not 0 <= wait <= ops.WAIT_MAX:
        raise HttpError(400, tr("site.panel.wait", max=ops.WAIT_MAX), code=errors.USAGE)
    return _call(ops.check_dns, _runner(), wait)


@route("PUT", r"/api/site/email")
def put_email(req):
    body = req.json()
    email = body.get("email")
    if email is not None and not isinstance(email, str):
        raise HttpError(400, tr("site.panel.email"), code=errors.USAGE)
    return _call(ops.set_email, _runner(), (email or "").strip() or None)


@route("POST", r"/api/site/cert")
def post_cert(req):
    body = req.json()
    return _call(ops.issue, _runner(), staging=_bool(body, "staging"))


@route("POST", r"/api/site/cert/renew")
def post_renew(req):
    return _call(ops.renew, _runner())


@route("PUT", r"/api/site/use")
def put_use(req):
    body = req.json()
    kind = body.get("kind")
    if kind not in ("sstp", "ocserv"):
        raise HttpError(400, tr("site.panel.kind"), code=errors.USAGE)
    if not isinstance(body.get("on"), bool):
        raise HttpError(400, tr("site.panel.bool", field="on"), code=errors.USAGE)
    return _call(ops.use, _runner(), kind, body["on"], dry_run=_bool(body, "dry_run"))


def _template_id(body):
    got = body.get("id", "random")
    if got == "random":
        return None
    if not isinstance(got, str) or not got:
        raise HttpError(400, tr("site.panel.template", field="id"), code=errors.USAGE)
    return got


@route("PUT", r"/api/site/mode")
def put_mode(req):
    body = req.json()
    got = body.get("mode")
    if got not in ("site", "domain"):
        raise HttpError(400, tr("site.panel.mode"), code=errors.USAGE)
    tid = None
    if got == "site" and "template" in body:
        tid = None if body["template"] == "random" else body["template"]
        if not isinstance(tid, str) and tid is not None:
            raise HttpError(400, tr("site.panel.template", field="template"), code=errors.USAGE)
    from ..rules.panel import who
    # the price of new VLESS links is decided by the core (SPEC-vless-tls-default §3.6/§3.7, З-3): 409 «vless_change»
    # with data.text — the panel shows the dialog «кому выдать заново» and sends the same request with "yes": true
    return _call(ops.mode, _runner(), got, template_id=tid, yes=_bool(body, "yes"), vless_site=_vless_site(body),
                 via="panel", who=who())


@route("GET", r"/api/site/categories")
def get_categories(req):
    try:
        from . import templates
        templates.prepare(_runner())
        return json_response(dict(templates.catalog(), ok=True))
    except errors.KaskadError as e:
        raise _http_error(e)


@route("GET", r"/api/site/templates")
def get_templates(req):
    try:
        from . import templates
        page = int(req.query.get("page", "1"))
    except (TypeError, ValueError):
        raise HttpError(400, tr("site.panel.page"), code=errors.USAGE)
    try:
        templates.prepare(_runner())
        res = templates.search(req.query.get("category"), req.query.get("query"), page)
    except errors.KaskadError as e:
        raise _http_error(e)
    from . import tplhealth
    health = tplhealth.cached([it["id"] for it in res["items"]])
    for it in res["items"]:
        it["health"] = health.get(it["id"])
    return json_response(dict(res, ok=True))


@route("POST", r"/api/site/templates/check")
def post_templates_check(req):
    from . import templates, tplhealth
    ids = req.json().get("ids")
    if not isinstance(ids, list) or not all(isinstance(x, str) and x for x in ids):
        raise HttpError(400, tr("site.panel.ids"), code=errors.USAGE)
    if len(ids) > tplhealth.MAX_IDS:
        raise HttpError(400, tr("site.panel.ids_max", max=tplhealth.MAX_IDS), code=errors.USAGE)
    try:
        templates.prepare(_runner())
        return json_response({"ok": True, "items": tplhealth.check(_runner(), ids)})
    except errors.KaskadError as e:
        raise _http_error(e)


@route("POST", r"/api/site/template")
def post_template(req):
    return _call(ops.template_set, _runner(), _template_id(req.json()))


@route("GET", r"/api/site/panel-url")
def get_panel_url(req):
    try:
        return json_response(ops.panel_url(show=True))
    except errors.KaskadError as e:
        raise _http_error(e)


@route("POST", r"/api/site/panel-path/rotate")
def post_panel_path_rotate(req):
    def go(runner):
        res = dict(ops.rotate_panel_path(runner))
        res["here"] = _here_url(req.listener)
        return res
    return _call(go, _runner())


def _here_url(listener):
    """3.3.7.5: the full new address of the panel for the way this tab came — the old secret dies with the tab (on
    the domain and on the port alike), and the old path can no longer ask for it. The domain's for the listener of
    nginx, the open mode's for the port (the backup entry stays the backup entry: a broken domain is not forced on
    it), None through the SSH tunnel (the tab keeps working). Only an admin's call gets here (the full address is
    his «Показать полностью» anyway)."""
    try:
        got = ops.panel_url(show=True)
    except errors.KaskadError:
        return None
    if listener == "proxy":
        return got["url"] if got["kind"] == "domain" else None
    if listener == "public":
        return got["fallback"] if got["kind"] == "domain" else got["url"]
    return None


@route("POST", r"/api/site/check")
def post_check(req):
    return _call(ops.check, _runner())
