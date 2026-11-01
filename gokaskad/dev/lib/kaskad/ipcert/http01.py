"""Who answers Let's Encrypt on TCP/80 for the IP certificate (http-01): ``GET /.well-known/acme-challenge/<token>``
with Host = the address must return the key authorization.

    choose(runner, st)   -> Webroot | Standalone, or Refusal("port80") with who holds the port
        Webroot      the nginx of the site of Kaskad (``site mode site``) serves the challenge directory for any name —
                     its default server on :80 too (site.nginx._reject80): the file goes to /var/www/kaskad-acme
        Standalone   nothing listens on :80: our own listener in a thread, only for the minutes of the check; it
                     answers the tokens of this issue and 404 to anything else, logs nothing
    A foreign program on :80 (nginx or Apache of another owner, a forward of TCP/80, Docker) — never stopped: the
    refusal names it, the quick setup keeps the node certificate.

UFW: an active firewall gets ``allow 80/tcp`` (comment kaskad-ipcert) for the check and loses it afterwards — only
the rule we added.
"""

import errno
import http.server
import os
import socket
import threading

from .. import conflicts, state, util
from ..errors import CONFLICT, KaskadError
from ..i18n import tr

PORT = 80
UFW_COMMENT = "kaskad-ipcert"
PREFIX = "/.well-known/acme-challenge/"


class Refusal(KaskadError):
    """Why there is no certificate, in the contract {"ok": false, "reason", "text", "retry_after"?}."""

    def __init__(self, reason, text, retry_after=None, code=None):
        KaskadError.__init__(self, text, code=code)
        self.reason = reason
        self.text = text
        self.retry_after = retry_after

    @property
    def doc(self):
        d = {"ok": False, "reason": self.reason, "text": self.text}
        if self.retry_after:
            d["retry_after"] = self.retry_after
        return d


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "kaskad"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        body = None
        if path.startswith(PREFIX):
            body = self.server.tokens.get(path[len(PREFIX):])
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = body.encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_HEAD = do_GET


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        socket.socket.bind(self.socket, self.server_address)
        self.server_address = self.socket.getsockname()


class Standalone(object):
    kind = "standalone"

    def __init__(self, runner, port=PORT, host="0.0.0.0"):
        self.runner = runner
        self.port = port
        self.host = host
        self.srv = None
        self.thread = None
        self.ufw = False

    def start(self):
        try:
            self.srv = _Server((self.host, self.port), _Handler)
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, errno.EACCES):
                raise Refusal("port80", tr("ipcert.port80.other"), code=CONFLICT)
            raise
        self.srv.tokens = {}
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.2},
                                       name="kaskad-ipcert-http01", daemon=True)
        self.thread.start()
        self.ufw = _ufw_open(self.runner)
        return self

    def put(self, token, key_auth):
        self.srv.tokens[token] = key_auth

    def stop(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None
        if self.ufw:
            _ufw_close(self.runner)
            self.ufw = False


class Webroot(object):
    kind = "webroot"

    def __init__(self, root):
        self.root = root
        self.files = []

    def start(self):
        util.ensure_dir(os.path.join(self.root, ".well-known", "acme-challenge"), 0o755)
        os.chmod(os.path.join(self.root, ".well-known"), 0o755)
        return self

    def put(self, token, key_auth):
        p = os.path.join(self.root, ".well-known", "acme-challenge", token)
        util.atomic_write(p, key_auth, 0o644)
        self.files.append(p)

    def stop(self):
        for p in self.files:
            try:
                os.unlink(p)
            except OSError:
                pass
        self.files = []


def _ufw_open(runner):
    from ..gw import sys as gsys
    if not gsys.ufw_active(runner):
        return False
    res = runner.run(["ufw", "status"])
    if res.ok and any(ln.split()[:1] in (["80/tcp"], ["80"]) and "ALLOW" in ln for ln in res.out.splitlines()):
        return False                                    # open already (the site, the owner): not ours to close
    return runner.run(["ufw", "allow", "80/tcp", "comment", UFW_COMMENT]).ok


def _ufw_close(runner):
    runner.run(["ufw", "delete", "allow", "80/tcp"])


def holders(st, runner):
    """Who holds TCP/80 now (texts), the domain of the node itself excepted."""
    from ..gw import ops as gops
    found = [c for c in gops.port_conflicts(st, runner, "ipcert", "tcp", PORT) if c.get("kind") != "site"]
    out = []
    for c in found:
        text = str(c.get("detail") or c.get("kind"))
        for tail in conflicts.effects():                # the tails of a forward, in the language they were said in
            if text.endswith(tail):
                text = text[:-len(tail)]
        out.append(text)
    return out


def choose(runner, st=None):
    from ..site import nginx
    if nginx.ours():
        return Webroot(nginx.acme_root())
    if st is None:
        try:
            st = state.load()
        except KaskadError:
            st = state.empty()
    who = holders(st, runner)
    if who:
        raise Refusal("port80", tr("ipcert.port80.busy", who="; ".join(who)), code=CONFLICT)
    return Standalone(runner)
