"""The quick setup inside the web panel (docs/k3/SPEC-setup-api.md): the group ``/api/setup/*`` and the page, only
under the secret path ``/setup-<secret>/`` of the listeners «public» (the open mode) and «local» (the SSH tunnel).

kaskad.panel.server asks ``resolve(path, client_ip)`` for every path that starts with /setup-: a wrong secret, a
finished or stopped wizard, another address of a bound link — None, and the server answers the 404 of an unknown path.
Under the prefix only the page, its static files and /api/setup/* answer; nothing of the panel itself.

Sessions are the panel's kind (kaskad.panel.auth.Sessions, a store of their own): the cookie ``kaskad_setup``
(HttpOnly, SameSite=Strict, Secure over HTTPS, Path=/setup-<secret>/). A session made by the one-time link dies when
the password is set — the step «password» gives the browser a session «password» at once; afterwards the wizard
opens only with the password of the panel (``POST /api/setup/login``, the same limits as the panel's login).
Mutations need ``X-Kaskad-Admin: 1`` (the panel's CSRF rule) and no foreign Origin (the server checks it).
"""

import re
import threading
import time

from .. import util
from ..errors import KaskadError
from ..i18n import get_lang, tr
from ..panel import auth, routes, sse
from ..panel.routes import HttpError, json_response, route
from . import jobs, link, model, product, steps

COOKIE = "kaskad_setup"
SESSIONS = auth.Sessions()
LINKS = auth.RateLimit(auth.LINK_ATTEMPTS, auth.WINDOW)
STREAMS_MAX = 4
POLL = 0.5
HEARTBEAT = 15.0

_lock = threading.Lock()
_streams = [0]


# ── the prefix ─────────────────────────────────────────────────────────────

class View(object):
    __slots__ = ("base", "path", "redirect", "expired", "doc")

    def __init__(self, base, path, redirect, expired, doc):
        self.base, self.path, self.redirect, self.expired, self.doc = base, path, redirect, expired, doc


def resolve(path, client_ip):
    """View of a /setup-… path, or None (the server answers 404)."""
    doc = model.load()
    hit = link.match(doc, path)
    if hit is None or not link.allowed_ip(doc, client_ip):
        return None
    base, rest = hit
    return View(base, rest or "/", rest is None, model.expired(doc), doc)


# fonts/<имя>.woff2 — шрифты дизайн-системы: tokens.css панели просит url(fonts/…) и под /setup-<секрет>/ (3.3.0)
ALLOWED = re.compile(r"^/(?:|index\.html|api/setup/.*|static/.*|fonts/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,80}\.woff2|"
                     r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,80}\.(?:js|css|svg|png|woff2|ico|json|webmanifest|txt))$")


def allowed(path):
    return bool(ALLOWED.match(path))


# ── sessions ───────────────────────────────────────────────────────────────

def cookie_token(headers):
    raw = headers.get("cookie") or ""
    for part in raw.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip() == COOKIE:
            v = v.strip().strip('"')
            if re.match(r"^[A-Za-z0-9_-]{20,128}$", v):
                return v
    return ""


def set_cookie(token, req):
    return "%s=%s; HttpOnly; SameSite=Strict; Path=%s/%s" % (COOKIE, token, req.base,
                                                              "; Secure" if req.scheme == "https" else "")


def clear_cookie(req):
    return "%s=; Max-Age=0; HttpOnly; SameSite=Strict; Path=%s/%s" % (COOKIE, req.base,
                                                                       "; Secure" if req.scheme == "https" else "")


def _setup(req):
    """The View of this request; HttpError 404 when the route is reached outside the secret path."""
    v = getattr(req, "setup", None)
    if v is None:
        raise HttpError(404, tr("setup.web.notFound"))
    return v


def session(req):
    tok = cookie_token(req.headers)
    s = SESSIONS.get(tok) if tok else None
    return dict(s, token=tok) if s is not None else None


def has_session(headers):
    """A live session of the wizard in these headers (the server reads a big body — a copy of the settings — only then)."""
    tok = cookie_token(headers)
    return bool(tok) and SESSIONS.get(tok) is not None


def need(req):
    """The session of a request to the wizard: 401 without one, 410 when the link expired."""
    v = _setup(req)
    if v.expired:
        raise HttpError(410, tr("setup.web.linkExpiredAsk", cmd=product.CMD),
                        data={"reason": "expired", "cmd": product.CMD})
    s = session(req)
    if s is None:
        doc = v.doc
        raise HttpError(401, tr("setup.web.signInNeeded") if doc["password_set"] else
                        tr("setup.web.openByLink", cmd=product.CMD),
                        data={"auth": "required", "reason": "password" if doc["password_set"] else "link"})
    return s


# ── the page and the link ──────────────────────────────────────────────────

def entry(req, static_response):
    """GET <base>/ — with ?t=<token>: redeem and 303 to the clean address (the token leaves the address bar); without:
    the page (static/setup/index.html; the panel's index when the page is not there yet)."""
    v = _setup(req)
    t = req.query.get("t")
    if not t:
        try:
            return static_response(req, "setup/index.html")
        except HttpError:
            return routes.Response(200, _stub().encode("utf-8"), {"Content-Type": "text/html; charset=utf-8"})
    h = {"Cache-Control": "no-store", "Location": v.base + "/"}
    key = auth.bucket(req.client_ip)
    allowed_, _, _ = LINKS.begin(key)
    if not allowed_:
        h["Location"] = v.base + "/?link=limit"
        return routes.Response(303, b"", h)
    if v.expired:
        h["Location"] = v.base + "/?link=expired"
        return routes.Response(303, b"", h)
    info = {}
    verdict = model.update(lambda doc: link.redeem(doc, t, req.client_ip, info=info))
    if verdict != "ok" and info.get("by") == "move":
        verdict = "revoked"                            # a stale move token: the page asks the password, nothing else
    if verdict != "ok":
        h["Location"] = v.base + "/?link=" + verdict
        return routes.Response(303, b"", h)
    LINKS.clear(key)
    old = cookie_token(req.headers)
    if old:
        SESSIONS.drop(old)
    tok = SESSIONS.create(info.get("via") or "link", req.client_ip)
    h["Set-Cookie"] = set_cookie(tok, req)
    util.log_tr("journal.setup.login_link", ip=req.client_ip)
    return routes.Response(303, b"", h)


_STUB = ("<!doctype html><html lang=\"%s\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
         "<title>%s</title><body><p>%s</p></body></html>")


def _stub():
    return _STUB % (get_lang(), tr("setup.web.stubTitle"), tr("setup.web.stubText"))


# ── API ────────────────────────────────────────────────────────────────────

@route("GET", r"/api/setup/session", auth="public")
def api_session(req):
    v = _setup(req)
    s = session(req)
    doc = v.doc
    reason = None
    if v.expired:
        reason = "expired"
    elif s is None:
        reason = "password" if doc["password_set"] else "link"
    return json_response({"ok": True, "auth": s is not None and not v.expired, "via": (s or {}).get("via"),
                          "reason": reason, "password_set": bool(doc["password_set"]), "cmd": product.CMD,
                          "ssh": product.ssh(), "name": product.NAME, "login": auth.record().get("login") if s else None,
                          "expires_in": max(0, int(float(doc["link"]["expires"] or 0) - time.time()))})


@route("POST", r"/api/setup/login", auth="public", csrf=True)
def api_login(req):
    v = _setup(req)
    if v.expired:
        raise HttpError(410, tr("setup.web.linkExpired", cmd=product.CMD), data={"reason": "expired"})
    if not v.doc["password_set"]:
        raise HttpError(409, tr("setup.web.noPasswordYet", cmd=product.CMD),
                        data={"reason": "link"})
    b = req.json()
    pw, name = b.get("password"), b.get("login") or ""
    if not isinstance(pw, str) or not isinstance(name, str) or len(pw) > auth.PASSWORD_MAX or len(name) > 64:
        raise HttpError(400, tr("setup.web.passwordString"))
    try:
        good, left = auth.login(req.client_ip, name, pw)
    except auth.LoginRefused as e:
        raise HttpError(e.status, str(e), data={"retry_after": e.retry_after, "auth": "limit"})
    pw = None
    if not good:
        raise HttpError(401, tr("setup.web.wrongPassword"), data={"left": left, "auth": "failed"})
    old = cookie_token(req.headers)
    if old:
        SESSIONS.drop(old)
    tok = SESSIONS.create("password", req.client_ip)
    util.log_tr("journal.setup.login_password", ip=req.client_ip)
    return json_response({"ok": True, "via": "password"}, headers={"Set-Cookie": set_cookie(tok, req)})


@route("POST", r"/api/setup/logout", auth="public", csrf=True)
def api_logout(req):
    _setup(req)
    tok = cookie_token(req.headers)
    if tok:
        SESSIONS.drop(tok)
    return json_response({"ok": True}, headers={"Set-Cookie": clear_cookie(req)})


@route("POST", r"/api/setup/move", auth="public", csrf=True)
def api_move(req):
    """The move of this session to the domain (stage 4): a one-time token and the address of the wizard on the domain
    — asked before the certificate of the domain is issued (afterwards https://<IP>:<порт>/ may not open any more:
    without a certificate on the IP the domain's one is served to it). -> {"url", "clean", "host", "expires_in"}."""
    s = need(req)
    from .. import state
    from ..site import model as smodel
    try:
        host = smodel.domain_of(state.load())
    except KaskadError:
        host = None
    # {"host": the name of the field} — a change of a working domain (T-M6-qs330d №1): the wizard opened on the old
    # domain asks for the move to the new one BEFORE the switch; only to the name the stage has just checked (its A
    # record leads here: answers.domain_next) or to the domain itself
    want = req.json().get("host")
    if isinstance(want, str) and want.strip():
        try:
            want = smodel.parse_domain(want)
        except KaskadError:
            raise HttpError(400, tr("setup.web.hostDomain"), data={"reason": "bad_host"})
        if want != host and want != model.load()["answers"].get("domain_next"):
            raise HttpError(409, tr("setup.web.domainNotChecked", domain=smodel.unicode(want)),
                            data={"reason": "not_checked"})
        host = want
    if not host:
        raise HttpError(409, tr("setup.web.noDomain"), data={"reason": "no_domain"})
    out = {}

    def mut(doc):
        tok = link.issue_move(doc, s.get("via"))
        out.update(url=link.move_url(doc, host, tok), clean=link.move_url(doc, host, None),
                   expires_in=max(0, min(link.MOVE_TTL, int(float(doc["link"]["expires"] or 0) - time.time()))))
    model.update(mut)
    if not out.get("url"):
        raise HttpError(409, tr("setup.web.notPublic"), data={"reason": "not_public"})
    return json_response(dict(out, ok=True, host=host))


@route("GET", r"/api/setup/move-ready", auth="public")
def api_move_ready(req):
    """Before the page goes to the domain by itself (T-M6-qs330b №4): the panel re-reads its certificates now, then
    shakes hands with itself under the domain's name — «ready» only when the domain's certificate answers there.
    The page asks again a few times; without «ready» it stays and offers «Перейти сейчас».
    -> {"ok", "ready", "reason", "host"} (conf.domain_served)."""
    need(req)
    from ..panel import conf, server
    if server.PANEL is not None:
        server.PANEL.tls_refresh()
    return json_response(dict(conf.domain_served(), ok=True))


@route("POST", r"/api/setup/lang", auth="public", csrf=True)
def api_lang(req):
    """The language of the server from the wizard (3.3.2): only with the wizard's session — the screens of the link and
    the password have no switch, a stranger never changes it."""
    need(req)
    from ..panel import lang as weblang
    return json_response(weblang.change(req.json()))


@route("GET", r"/api/setup/state", auth="public")
def api_state(req):
    need(req)
    doc = model.load()
    out = model.public_view(doc)
    out.update(ok=True, job=jobs.localize(jobs.status()), product=product.doc(model.mode(doc)))
    out["site"] = steps.site_now()                   # the site in fact, not the record of its step (T-M6-qs330c №4)
    out["vless"] = steps.vless_now()                 # VLESS for the card «Сайт и панель на домене» (VLESS Ш4); None — no VLESS
    return json_response(out)


@route("GET", r"/api/setup/product", auth="public")
def api_product(req):
    need(req)
    return json_response(dict(product.doc(model.mode(model.load())), ok=True))


@route("GET", r"/api/setup/steps", auth="public")
def api_steps(req):
    need(req)
    return json_response({"ok": True, "steps": [s.doc() for s in steps.STEPS.values()]})


@route("GET", r"/api/setup/net", auth="public")
def api_net(req):
    need(req)
    return json_response({"ok": True, "online": steps.online(force=req.query.get("now") == "1"), "every": 10})


@route("GET", r"/api/setup/ipcert", auth="public")
def api_ipcert(req):
    need(req)
    from ..ipcert import ops as ipops, store as ipstore
    from ..panel import conf
    st = ipstore.status()
    serving = conf.ip_cert() is not None
    domain = conf.site_cert() is not None
    why = ((st.get("last_try") or {}).get("text") or "").rstrip(". ")
    # the page opened by the address gets the IP certificate next to the domain's one (SNI: panel/server.sni_pick)
    by_ip = serving and _by_address(req)
    if domain and not by_ip:
        text = tr("setup.web.certDomain")
    elif serving:
        text = tr("setup.web.certIp", lifetime=tr(ipops.LIFETIME_TEXT))
    else:
        text = tr("setup.web.certIpFailedWhy", why=why) if why else tr("setup.web.certIpFailed")
    st.update(ok=True, serving=serving, domain=domain, text=text,
              fingerprint="" if (serving or domain) else conf.fingerprint())
    return json_response(st)


def _by_address(req):
    """Is the page open by an IP address (Host is an address, not a name)?"""
    import ipaddress
    raw = str((req.headers or {}).get("host") or "").strip().lower()
    host = raw[1:raw.find("]")] if raw.startswith("[") else (raw.split(":", 1)[0] if raw.count(":") == 1 else raw)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@route("GET", r"/api/setup/job", auth="public")
def api_job(req):
    need(req)
    return json_response({"ok": True, "job": jobs.localize(jobs.status())})


@route("POST", r"/api/setup/stage", auth="public", csrf=True)
def api_stage(req):
    need(req)
    b = req.json()
    kind, action = b.get("kind"), b.get("action")
    if not isinstance(kind, str) or action not in ("open", "done", "skip"):
        raise HttpError(400, tr("setup.web.stageArgs"))
    try:
        cur = model.update(lambda doc: model.set_stage(doc, kind, action))
    except ValueError as e:
        raise HttpError(409, str(e), data={"reason": "stage"})
    sse.HUB.poke()
    return json_response({"ok": True, "current": cur, "rail": model.rail(model.load())})


@route("POST", r"/api/setup/step/(?P<name>[a-z0-9-]{2,32})", auth="public", csrf=True)
def api_step(req):
    s = need(req)
    name = req.match.group("name")
    st = steps.STEPS.get(name)
    if st is None:
        raise HttpError(404, tr("setup.web.noSuchStep", name=name), data={"reason": "unknown_step"})
    body = req.json()
    if st.long:
        doc = model.load()
        if st.password and not doc["password_set"]:
            raise HttpError(409, tr("setup.web.passwordFirst"), data={"reason": "password_first"})
        try:
            from ..panel import api as papi
            job = jobs.start(name, body, papi.runner())
        except KaskadError as e:
            raise routes.core_error(e)
        body = None
        return json_response({"ok": True, "job": job, "step": name}, status=202)
    from ..panel import api as papi
    out = steps.run(name, body, papi.runner(), None, by="web")
    body = None
    headers = {}
    if name == "password" and out.get("ok"):
        SESSIONS.drop(s["token"])
        tok = SESSIONS.create("password", req.client_ip)
        headers["Set-Cookie"] = set_cookie(tok, req)
    try:
        papi._changed()
    except Exception:                       # noqa: BLE001 — the panel's SSE is a courtesy
        pass
    return json_response(out, headers=headers)        # the state of a step is in its body (ok, error, offline…)


# «Telegram-бот» and «Ключ ИИ-агента» of the stage «Дополнительно» (3.3.3): the components of the panel (ux.js
# TelegramBot, AgentKey) with the panel's own handlers (kaskad.panel.ux) behind the wizard's session — one logic, no
# copy. The token and the key line come in the body only and never go back (as in the panel).
def _ux():
    from ..panel import ux as pux
    return pux


@route("GET", r"/api/setup/ux/bot", auth="public")
def api_ux_bot(req):
    need(req)
    return _ux().bot_get(req)


@route("POST", r"/api/setup/ux/bot/check", auth="public", csrf=True)
def api_ux_bot_check(req):
    need(req)
    return _ux().bot_check(req)


@route("POST", r"/api/setup/ux/bot/test", auth="public", csrf=True)
def api_ux_bot_test(req):
    need(req)
    return _ux().bot_test(req, journal="journal.setup.bot")


@route("GET", r"/api/setup/ux/mcp", auth="public")
def api_ux_mcp(req):
    need(req)
    return _ux().mcp_get(req)


@route("POST", r"/api/setup/ux/mcp/keys", auth="public", csrf=True)
def api_ux_mcp_add(req):
    need(req)
    return _ux().mcp_key_add(req)


# the page of the wizard sends GET and POST only: the removal is a POST with the fingerprint in the path, as the panel's DELETE
@route("POST", r"/api/setup/ux/mcp/keys/(?P<fp>[A-Za-z0-9%:+/=._-]{8,200})/del", auth="public", csrf=True)
def api_ux_mcp_del(req):
    need(req)
    return _ux().mcp_key_del(req)


@route("POST", r"/api/setup/finish", auth="public", csrf=True)
def api_finish(req):
    need(req)
    from ..panel import conf
    out = steps.run("finish", {}, None, None, by="web")
    tok = auth.SESSIONS.create("password", req.client_ip)       # «Открыть панель» without a second login
    cfg = conf.load()
    # 3.3.7.5: the panel of the open mode lives under its secret prefix (the root of the port is an empty site);
    # through the SSH tunnel — at the root as always. The session cookie only for the panel's path.
    base = conf.proxy_base(cfg) if req.listener == "public" else ""
    cookie = auth.set_cookie(tok, req.scheme == "https")
    if base:
        cookie = auth.cookie_for(cookie, secure=True, path=base + "/")
    headers = {"Set-Cookie": [cookie, clear_cookie(req)]}
    out["panel_url"] = base + "/"
    out["proxy_url"] = conf.proxy_url(cfg)
    return json_response(out, headers=headers)


# ── events ─────────────────────────────────────────────────────────────────

class _Slot(object):
    def __init__(self):
        self.held = True

    def unsubscribe(self):
        with _lock:
            if self.held:
                self.held = False
                _streams[0] = max(0, _streams[0] - 1)


class Stream(sse.Stream):
    """``event: job`` — the whole job document when it changes (rows, state, result); ``event: state`` — setup.json
    changed (``{"updated"}``: re-read /api/setup/state); ``: hb`` every HEARTBEAT s of silence."""

    def __init__(self, slot, alive=None):
        self.hub = slot
        self.alive = alive              # F19b (finding 7): the session and the link of this stream — see ``still_open``
        self.gates = []

    def run(self, write):
        from .. import snapshot
        try:
            last_job, last_state = object(), snapshot.signature(model.path())
            if not self.allowed():
                return
            write(b"retry: 3000\n\n")
            beat = time.monotonic()
            while self.hub.held:
                if not self.allowed():          # every wake, before anything is read: «Выйти», a new password, the
                    return                      # link's time, the wizard closed — the stream ends, sends nothing more
                job_sig = snapshot.signature(jobs.job_path())
                if job_sig != last_job:
                    last_job = job_sig
                    write(sse.event("job", jobs.localize(jobs.status()) or {}))
                    beat = time.monotonic()
                st_sig = snapshot.signature(model.path())
                if st_sig != last_state:
                    last_state = st_sig
                    write(sse.event("state", {"updated": util.now_iso()}))
                    beat = time.monotonic()
                if time.monotonic() - beat >= HEARTBEAT:
                    write(b": hb\n\n")
                    beat = time.monotonic()
                time.sleep(POLL)
        finally:
            self.hub.unsubscribe()


def still_open(base, client_ip, token):
    """F19b (finding 7): may the stream of the session ``token``, opened under ``base``, go on — what ``need`` and
    the server's ``resolve`` let a new request in by: the session is still there (not «Выйти», not a new password,
    not its time), the wizard is open under this very secret path for this address and its link has not expired."""
    if not SESSIONS.alive(token):
        return False
    try:
        doc = model.load()
    except (KaskadError, OSError, ValueError):
        return False
    return link.match(doc, base + "/") is not None and link.allowed_ip(doc, client_ip) and not model.expired(doc)


@route("GET", r"/api/setup/events", auth="public")
def api_events(req):
    s = need(req)
    with _lock:
        if _streams[0] >= STREAMS_MAX:
            raise HttpError(503, tr("setup.web.tooManyTabs"),
                            data={"poll": "/api/setup/job"})
        _streams[0] += 1
    base, ip, tok = req.base, req.client_ip, s["token"]
    return Stream(_Slot(), alive=lambda: still_open(base, ip, tok))
