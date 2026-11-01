"""Routing contract of the web panel (docs/k3/SPEC-wave3.md §3.2): handlers register at import time, the server
dispatches. Shared by WP-P1 (server, API), WP-P3 (scheme), WP-U (user page), WP-R2 (rules).

    from kaskad.panel.routes import route, json_response, HttpError

    @route("GET", r"/api/gw/people")
    def people(req):
        return json_response({"ok": True, ...})

``auth``:
    "admin"  — a logged-in admin session is required (default);
    "public" — no session (login form, static files, health);
    "page"   — the user page: the server only rate-limits; the handler checks the token from the path itself.
``csrf``: POST/PUT/DELETE of "admin" routes require the header ``X-Kaskad-Admin: 1`` (default True for them).
``runs``: a GET that runs commands on the node (reads keep it still — these few do not: ``journalctl``, ``systemctl``,
``apt-get -s``…) is refused to a foreign page as a change is — ``Origin`` not the panel's own (``null`` too: the panel's
page sends none on a GET) or ``Sec-Fetch-Site: cross-site`` -> 403 (REV-3377-stand-fix 1: over the SSH tunnel an admin
needs no cookie, any open page could make the node run them). HEAD goes by the GET route, the same check.
``body``: "read" — the server reads the body (≤ 1 MiB) before the handler (default); "stream" — an "admin" route with a
session gets the body unread: the server checks only its headers (Content-Length, no chunked, ≤ ``body_max``) and
the login, Origin and ``X-Kaskad-Admin``, then the handler reads ``req.rfile`` itself (k3/handoff/SPEC-browser-channel.md
§4.1). Without a session the body path is the usual one (the same answer as any ``/api/*``).
``head``: False — HEAD gets 405 after the login check, before the handler (the routes of the browser test, §4.1 п. 5).
A handler returns a ``Response`` or raises ``HttpError``; anything else raised becomes 500 without details
(the server logs the traceback without request bodies).
"""

import json
import re

from .. import util
from ..i18n import tr

ROUTES = []


class Route(object):
    __slots__ = ("method", "rx", "handler", "auth", "csrf", "pattern", "runs", "body", "body_max", "head")

    def __init__(self, method, pattern, handler, auth, csrf, runs=False, body="read", body_max=0, head=True):
        # ``runs``/``body``/``body_max``/``head`` have defaults: server._setup_entry()/_setup_file() build a Route
        # by position
        self.method = method
        self.pattern = pattern
        self.rx = re.compile("^" + pattern + "$")
        self.handler = handler
        self.auth = auth
        self.csrf = csrf
        self.runs = runs
        self.body = body
        self.body_max = body_max
        self.head = head


def route(method, pattern, auth="admin", csrf=None, runs=False, body="read", body_max=0, head=True):
    """Register ``handler(req) -> Response`` for ``method`` and the regex ``pattern`` (anchored). ``body="stream"`` —
    only for "admin" POST/PUT with ``body_max`` > 0 (the biggest Content-Length the server lets through to the
    handler)."""
    if method not in ("GET", "POST", "PUT", "DELETE"):
        raise ValueError("method %r" % (method,))
    if auth not in ("admin", "public", "page"):
        raise ValueError("auth %r" % (auth,))
    if runs and (method != "GET" or auth != "admin"):
        raise ValueError("runs: only an admin GET (changes are checked anyway)")
    if body not in ("read", "stream"):
        raise ValueError("body %r" % (body,))
    if body == "stream" and (auth != "admin" or method not in ("POST", "PUT") or not isinstance(body_max, int)
                             or isinstance(body_max, bool) or body_max <= 0):
        raise ValueError("body stream: admin POST/PUT with body_max > 0")
    if csrf is None:
        csrf = auth == "admin" and method != "GET"

    def deco(fn):
        ROUTES.append(Route(method, pattern, fn, auth, csrf, runs, body, body_max, bool(head)))
        return fn
    return deco


def find(method, path):
    """(Route, match) for the request, (None, None) when nothing matches, ("method", None) when only the method
    is wrong (405)."""
    wrong_method = False
    for r in ROUTES:
        m = r.rx.match(path)
        if m is None:
            continue
        if r.method == method or (method == "HEAD" and r.method == "GET"):
            return r, m
        wrong_method = True
    return ("method", None) if wrong_method else (None, None)


class Request(object):
    """What a handler sees. ``query`` — dict of the first values; ``headers`` — lower-case names;
    ``session`` — dict of the admin session or None; ``match`` — the route's regex match. Behind nginx (the
    listener "proxy", docs/k3/SPEC-wave4.md §3) ``path`` is without the panel's prefix, ``base`` is that prefix
    ("/app-…" or ""), ``client_ip``, ``scheme`` and the Host header are the ones nginx passed. ``after`` — callables
    the server runs once the answer has left (``server.AFTER_S`` later, its own thread; also when the handler failed
    or the client went away): what the answer must not wait for and must not be cut by (3.3.7.15 П3: a restart of the
    xray the panel is reached through)."""

    __slots__ = ("method", "path", "query", "headers", "body", "match", "session", "client_ip", "scheme", "base",
                 "listener", "setup", "rfile", "length", "local_ip", "after")

    def __init__(self, method, path, query=None, headers=None, body=b"", match=None, session=None,
                 client_ip="", scheme="http", base="", listener="local", setup=None, rfile=None, length=None,
                 local_ip=""):
        self.local_ip = local_ip        # the address of the server the request came to (the open mode; PLAN-multi-ip Ш3)
        self.after = []                 # run by the server after the answer (3.3.7.15 П3)
        self.setup = setup              # the quick setup: kaskad.setup.web.View under /setup-<secret>/, else None
        self.rfile = rfile              # body="stream": a BodyReader of the unread body (``length`` bytes), else None
        self.length = length
        self.base = base
        self.listener = listener
        self.method = method
        self.path = path
        self.query = query or {}
        self.headers = headers or {}
        self.body = body or b""
        self.match = match
        self.session = session
        self.client_ip = client_ip
        self.scheme = scheme

    def json(self):
        """The JSON body as a dict; HttpError 400 when it is not a JSON object. NaN, Infinity and -Infinity are no JSON
        (RFC 8259) — refused too: ``limit_gb: Infinity`` gave OverflowError deep in the policy (REV-3313-final); so is
        a number past ±util.NUM_MAX — ``1e400`` (read as inf), ``1e308``, an integer of 400 digits (REV-33714 С-1)."""
        if not self.body:
            return {}
        try:
            obj = util.json_in(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, tr("panel.routes.notJson"))
        if not isinstance(obj, dict):
            raise HttpError(400, tr("panel.routes.notJsonObject"))
        return obj


class Response(object):
    __slots__ = ("status", "headers", "body")

    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers = dict(headers or {})


class BodyReader(object):
    """The unread body of a ``body="stream"`` request: never past its Content-Length. ``readinto1(mv, timeout)`` — one
    read of the socket (what has come, at most ``len(mv)`` and ``left``; 0 — the client went away or nothing is left);
    ``timeout`` — seconds this read may wait at most (the socket's own timeout otherwise: never longer); ``left`` —
    the bytes not read yet: the server keeps the connection alive only when it is 0."""

    __slots__ = ("_f", "_wait", "left")

    def __init__(self, rfile, length, wait=None):
        self._f = rfile
        self._wait = wait               # callable(seconds | None): the socket's timeout of the next operation
        self.left = int(length)

    def readinto1(self, mv, timeout=None):
        if self.left <= 0:
            return 0
        if len(mv) > self.left:
            mv = mv[:self.left]
        if self._wait is not None:
            self._wait(timeout)
        n = self._f.readinto1(mv) or 0
        self.left -= n
        return n


class RawStream(object):
    """An answer written by the handler itself: ``write_fn(write)`` calls ``write(bytes|memoryview, timeout=None)``
    until it has written ``length`` bytes (or stops early: the server then closes the connection); ``timeout`` — the
    seconds one write may wait at most. The server calls ``write_fn`` exactly once, whatever happens (a HEAD, a client
    gone before the headers): its ``finally`` is the one place to give back what the handler took. ``Content-Length`` is
    ``length``; keep-alive stays only when exactly ``length`` bytes went out. A client that went away (a broken pipe,
    a reset, a TLS error) is quiet: no journal line. ``write_fn`` gives back whatever it took itself (``finally``)."""

    __slots__ = ("status", "length", "headers", "write_fn")

    def __init__(self, status, length, headers, write_fn):
        self.status = status
        self.length = int(length)
        self.headers = dict(headers or {})
        self.write_fn = write_fn


class HttpError(Exception):
    """An error with a user-facing Russian text; ``code`` — kaskad.errors code when it comes from the core."""

    def __init__(self, status, text, code=None, data=None):
        Exception.__init__(self, text)
        self.status = status
        self.text = text
        self.code = code
        self.data = data


def json_response(obj, status=200, headers=None, cache=False):
    """JSON body; ``Cache-Control: no-store`` unless ``cache`` (secrets never cached)."""
    h = {"Content-Type": "application/json; charset=utf-8"}
    if not cache:
        h["Cache-Control"] = "no-store"
    h.update(headers or {})
    return Response(status, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"), h)


def error_response(err):
    """Response of an HttpError: ``{"ok": false, "error": text, "code": code}``."""
    body = {"ok": False, "error": err.text}
    if err.code is not None:
        body["code"] = err.code
    if err.data is not None:
        body["data"] = err.data
    return json_response(body, status=err.status)


def core_error(e):
    """HttpError of a ``kaskad.errors.KaskadError`` (helper, WP-P1): the core's Russian text and its code;
    HTTP 409 for a conflict, «not here» and «busy» (codes 3, 5, 6), 500 for a broken state.json, 400 otherwise.
    The server applies it to a KaskadError a handler did not catch.

    Stage 1 (T7): a refusal of the exit policy (``gw.policy_ops.Refused``) — its own status and word (``code``:
    ``policy_changed``, ``gone``, ``rev_required`` …, SPEC-stage1-exits §7.9); the state lock taken again by the thread
    that holds it (``NestedLockError``) — an error of the code: 500 and a line in the journal with the place of the
    call, never 409 «занято» with «Повторить» that would only repeat it (§3.1, проверка 20, З-2)."""
    from ..errors import CONFLICT, LOCKED, UNSUPPORTED, NestedLockError, StateError
    from ..util import redact
    code = getattr(e, "code", None)
    key = getattr(e, "key", None)
    if isinstance(key, str) and isinstance(getattr(e, "status", None), int):
        return HttpError(e.status, redact(str(e)), code=key, data=getattr(e, "data", None) or None)
    if isinstance(e, NestedLockError):
        _nested_logged(e)
        return HttpError(500, redact(str(e)), code=code, data=None)
    if isinstance(e, StateError):
        status = 500
    elif code in (CONFLICT, UNSUPPORTED, LOCKED):
        status = 409
    else:
        status = 400
    return HttpError(status, redact(str(e)), code=code, data=getattr(e, "data", None) or None)


def _nested_logged(e):
    """The journal line of a nested state lock: the text and the place of the call (the frames of kaskad that took the
    lock again), never the bodies of the request."""
    import traceback
    from ..util import log_action, redact
    tb = traceback.extract_tb(e.__traceback__) if e.__traceback__ is not None else []
    where = ["%s:%d %s" % (f.filename.rsplit("/kaskad/", 1)[-1], f.lineno, f.name) for f in tb
             if "/kaskad/" in f.filename][-4:]
    log_action("PANEL: nested state lock — %s (%s)" % (redact(str(e)), " <- ".join(reversed(where)) or "?"))
