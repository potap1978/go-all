"""A real web panel on 127.0.0.1 (a free port) in this process, the fake node of GwCase underneath (WP-P1)."""

import http.client
import json
import os
import socket
import ssl
import time
from unittest import mock

from kaskad import secrets, snapshot, state
from kaskad.gw import collect, exits, live, ops
from kaskad.panel import auth, conf, server, sse
from tests.fakes_gw import VLESS_A, awg_line, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase
from tests.netports import free_port

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "panel")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class Resp(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = {}
        self.cookies = []
        for k, v in headers:
            if k.lower() == "set-cookie":
                self.cookies.append(v)
            self.headers[k.lower()] = v
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def __repr__(self):
        return "Resp(%d, %s)" % (self.status, self.body[:300])


class PanelCase(GwCase):
    """``self.call(method, path, body)`` — a request to the panel (Host 127.0.0.1:<port>, the CSRF header on
    changes unless ``csrf=False``); without ``tunnel=False`` it comes over the «SSH tunnel» (admin)."""

    def setUp(self):
        super().setUp()
        self._pp = [mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(auth, "FLOOR", 0.0),
                    mock.patch.object(auth, "LIMIT", auth.RateLimit()), mock.patch.object(auth, "PAGES", auth.Misses()),
                    mock.patch.object(auth, "LINKS", auth.RateLimit(auth.LINK_ATTEMPTS, auth.WINDOW)),
                    mock.patch.object(auth, "SESSIONS", auth.Sessions()),
                    mock.patch.object(sse, "HEARTBEAT", 0.6), mock.patch.object(sse, "POLL", 0.05),
                    mock.patch.object(sse, "IDLE_EXIT", 0.2), mock.patch.object(collect, "LIVE", live.Live()),
                    mock.patch.object(server, "SHUTDOWN_POLL", 0.05)]
        for p in self._pp:
            p.start()
        snapshot.forget()
        self.panel = server.make(("127.0.0.1", 0))
        self.port = self.panel.servers["local"][0].server_address[1]

    def tearDown(self):
        self.panel.stop()
        server.PANEL = None
        for p in reversed(self._pp):
            p.stop()
        snapshot.forget()
        super().tearDown()

    # ── requests ──
    def call(self, method, path, body=None, headers=None, csrf=True, tunnel=True, cookie=None, host=None,
             port=None, https=False, raw_body=None):
        h = {"Host": host if host is not None else "127.0.0.1:%d" % (port or self.port)}
        if method in ("POST", "PUT", "DELETE") and csrf:
            h["X-Kaskad-Admin"] = "1"
        if not tunnel:
            h["X-Forwarded-For"] = "198.51.100.9"
        if cookie:
            h["Cookie"] = "%s=%s" % (auth.COOKIE, cookie)
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        if https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=ctx)
        else:
            c = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=10)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            return Resp(r.status, r.getheaders(), r.read())
        finally:
            c.close()

    def ok(self, method, path, body=None, **kw):
        r = self.call(method, path, body, **kw)
        self.assertEqual(r.status, 200, "%s %s -> %r" % (method, path, r))
        return r.json()

    def err(self, status, method, path, body=None, **kw):
        r = self.call(method, path, body, **kw)
        self.assertEqual(r.status, status, "%s %s -> %r" % (method, path, r))
        doc = r.json()
        self.assertIs(doc["ok"], False)
        self.assertTrue(doc["error"])
        return doc

    def raw(self, data, port=None):
        """Bytes straight to the socket; the whole answer back (the server closes)."""
        s = socket.create_connection(("127.0.0.1", port or self.port), timeout=10)
        try:
            s.sendall(data)
            out = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return out
                out += chunk
        finally:
            s.close()

    def login(self, password, login_name="admin", tunnel=False):
        r = self.call("POST", "/api/login", {"login": login_name, "password": password}, tunnel=tunnel)
        self.assertEqual(r.status, 200, r)
        return self.cookie_of(r)

    @staticmethod
    def cookie_of(r):
        for c in r.cookies:
            name, _, rest = c.partition("=")
            if name == auth.COOKIE:
                return rest.split(";", 1)[0]
        return None

    # ── the node ──
    def gateway(self, inbounds=("wg",)):
        """Engine nft with a forward, the pool (VLESS + AmneziaWG line 2), the gateway on (dummy wg + awg2), the
        inbounds installed; one collector round wrote live.json."""
        self.ads = self.dummies()
        save_state([F443])
        install_parser()
        awg = awg_line(2)
        write_pool([VLESS_A, awg])
        self.vid, self.aid = exits.exit_id(VLESS_A), exits.exit_id(awg)
        self.w.leg(2)
        ops.enable(self.w.runner)
        with state.locked():                    # A5 (§10.1 п. 22): the owner said «не напоминать» — no hint of the
            st = state.load()                   # probes in the feed of these tests, no probe
            st["gateway"]["hostmon"] = {"probes": False}
            state.save(st)
        for k in inbounds:
            ops.inbound_install(self.w.runner, k)
        self.uid = ops.user_add(self.w.runner, "Мама")["id"]
        snapshot.bump()
        return state.load()

    def collect(self):
        res = collect.tick(self.w.runner)
        snapshot.forget()
        return res

    def node_cert(self):
        """The fixture certificate as the node certificate (kaskad.gw.tls secrets)."""
        secrets.set("tls.server.crt", fixture("node.crt"))
        secrets.set("tls.server.key", fixture("node.key"))

    def public_on(self, port=None):
        """The open mode on 127.0.0.1:<free port> (the panel reloads)."""
        self.node_cert()
        if port is None:
            port = free_port()
        cfg = conf.load()
        cfg["public"] = {"on": True, "port": port, "bind": "127.0.0.1", "host": "127.0.0.1", "cert_host": "127.0.0.1"}
        conf.save(cfg)
        self.panel.reload()
        self.assertIn("public", self.panel.servers, self.panel.errors)
        self.pb = conf.proxy_base(conf.load())      # 3.3.7.5: the panel of the open mode — only under its secret prefix
        self.assertTrue(self.pb.startswith("/app-"))
        return port

    def wait(self, fn, timeout=5.0, step=0.02):
        end = time.time() + timeout
        while time.time() < end:
            got = fn()
            if got:
                return got
            time.sleep(step)
        return fn()
