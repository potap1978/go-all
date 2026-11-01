"""BC1 (k3/handoff/SPEC-browser-channel.md §4.1, §4.2, §7, §8.1): the routes of the browser test on the real server of
the panel — the order of the checks of the new body path (every refusal before the body is read), the answers without
a login (the same as any /api/*), HEAD, the bytes of ``down`` and keep-alive, ``up`` without memory and with the
buckets of its time, a client that goes away, the binding of a lease to its session, ``via``."""

import hashlib
import json
import os
import socket
import threading
import time
import tracemalloc
from unittest import mock

from kaskad import state, sysrun
from kaskad.panel import auth, host as phost, routes, server, speed
from kaskad.i18n import tr
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_proxy import ProxyCase

MIB = 1 << 20
MB = 1000 * 1000
DEFAULT = json.dumps([{"dst": "default", "gateway": "203.0.113.1", "dev": "eth0"}])


def parse(raw):
    """(status, {lower name: value}, body) of one HTTP answer in ``raw``."""
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 0
    h = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        h[k.strip().lower()] = v.strip()
    return status, h, body


class SpeedCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.logged = []
        self.runner = sysrun.FakeRunner()
        self.runner.on(["ip", "-j", "route", "show", "default"], (0, DEFAULT, ""))
        self.runner.on(["ip", "-j", "route", "get"], (0, json.dumps([{"dst": "x", "dev": "eth0"}]), ""))
        for p in (mock.patch.object(speed, "SPEED", speed.Speed(cpu=lambda: None)),
                  mock.patch.object(phost.sysrun, "default_runner", lambda: self.runner),
                  mock.patch.object(server, "log_t", lambda key, **kw: self.logged.append(key))):
            p.start()
            self.addCleanup(p.stop)

    # ── helpers ──
    def start(self, **kw):
        return self.ok("POST", "/api/host/speed/start", {}, **kw)

    def lease(self):
        return speed.SPEED._lease

    def sock(self, port=None, timeout=5):
        return socket.create_connection(("127.0.0.1", port or self.port), timeout=timeout)

    def head_only(self, method, path, headers, port=None, body=b"", timeout=5):
        """Send the request line and the headers (and ``body``, usually nothing) and read the whole answer: the
        server must answer WITHOUT the rest of the body (else the read times out) and close."""
        s = self.sock(port, timeout)
        try:
            h = {"Host": "127.0.0.1:%d" % (port or self.port)}
            h.update(headers)
            req = "%s %s HTTP/1.1\r\n" % (method, path) + "".join("%s: %s\r\n" % kv for kv in h.items()) + "\r\n"
            s.sendall(req.encode("latin-1") + body)
            out = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
            return parse(out)
        finally:
            s.close()

    def drain_to_close(self, s, timeout=5):
        """Read ``s`` until the server closes it -> the bytes; a timeout (the server kept it open) fails the test."""
        s.settimeout(timeout)
        out = bytearray()
        try:
            while True:
                chunk = s.recv(1 << 16)
                if not chunk:
                    return bytes(out)
                out += chunk
        except socket.timeout:
            self.fail("the server kept the connection open (keep-alive after a short answer)")
        except ConnectionResetError:                                    # closed with bytes it did not read
            return bytes(out)

    def quiet(self):
        """No «internal error» line in the journal of the server."""
        self.assertEqual([k for k in self.logged if k in ("journal.srv.handler_error", "journal.srv.request_error")],
                         [])


class AnswersWithoutLoginTest(SpeedCase):
    def test_every_route_without_login_answers_like_any_api(self):
        health = self.call("GET", "/api/host/health", tunnel=False)
        self.assertEqual(health.status, 401)
        for path in ("/api/host/speed/ping?id=%s" % ("a" * 32), "/api/host/speed/down?id=x&bytes=1",
                     "/api/host/speed/budget"):
            r = self.call("GET", path, tunnel=False)
            self.assertEqual((r.status, r.json()), (health.status, health.json()), path)
        hh = self.call("HEAD", "/api/host/health", tunnel=False)
        for path in ("/api/host/speed/ping", "/api/host/speed/down", "/api/host/speed/budget"):
            r = self.call("HEAD", path, tunnel=False)
            self.assertEqual((r.status, r.body), (hh.status, hh.body), path)        # 401, never 405 without login
            self.assertEqual(r.status, 401)
        users = self.call("POST", "/api/gw/users", raw_body=b"0123456789", tunnel=False)
        for path in ("/api/host/speed/up?id=x", "/api/host/speed/start", "/api/host/speed/finish"):
            r = self.call("POST", path, raw_body=b"0123456789", tunnel=False)
            self.assertEqual((r.status, r.json()), (users.status, users.json()), path)
            self.assertEqual(r.status, 401)

    def test_up_without_login_and_64_mib_is_the_413_of_any_api_unread_and_closed(self):
        hdr = {"Content-Length": str(64 * MIB), "X-Kaskad-Admin": "1", "X-Forwarded-For": "198.51.100.9"}
        ref = self.head_only("POST", "/api/gw/users", hdr)
        got = self.head_only("POST", "/api/host/speed/up?id=x", hdr)
        self.assertEqual(got[0], 413)
        self.assertEqual((got[0], json.loads(got[2])), (ref[0], json.loads(ref[2])))
        self.assertEqual(got[1].get("connection"), "close")
        self.quiet()

    def raw_post(self, length, pieces, gap):
        """POST without a login, ``Content-Length: length``, then ``pieces`` of the body ``gap`` s apart -> the
        socket (the answer is not read here)."""
        s = self.sock(timeout=10)
        s.sendall(("POST /api/host/speed/up?id=x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Kaskad-Admin: 1\r\n"
                   "X-Forwarded-For: 198.51.100.9\r\nConnection: close\r\nContent-Length: %d\r\n\r\n"
                   % (self.port, length)).encode())
        for p in pieces:
            time.sleep(gap)
            try:
                s.sendall(p)
            except OSError:
                break
        return s

    def test_a_slow_body_without_login_is_cut_by_one_deadline_for_the_whole_body(self):
        # SEC-bc12 З-1: a byte now and then (each read well within SOCKET_TIMEOUT) must not hold the thread
        with mock.patch.object(server, "BODY_S", 1.0):
            t0 = time.monotonic()
            s = self.raw_post(MIB, [b"x"] * 8, 0.25)
            try:
                out = self.drain_to_close(s, timeout=5)
            finally:
                s.close()
            self.assertLess(time.monotonic() - t0, 3.5)
            self.assertEqual(out, b"")                                   # no answer: the body did not come in time
            # in time, in pieces: the usual answer of any /api/* without a login
            s = self.raw_post(30, [b"0123456789"] * 3, 0.1)
            try:
                st, h, body = parse(self.drain_to_close(s))
            finally:
                s.close()
            self.assertEqual(st, 401)
        self.quiet()

    def test_two_content_lengths_are_400_and_closed(self):
        # SEC-bc12 Б-1 (RFC 9112 §6.3): never "the last one" — the body would be read as a second request
        second = b"GET /api/host/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        for path in ("/api/gw/users", "/api/host/speed/up?id=x"):
            s = self.sock()
            try:
                s.sendall(("POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Kaskad-Admin: 1\r\n"
                           "Content-Length: %d\r\ncontent-length: 0\r\n\r\n"
                           % (path, self.port, len(second))).encode() + second)
                out = self.drain_to_close(s)
            finally:
                s.close()
            st, h, body = parse(out)
            self.assertEqual((st, h.get("connection")), (400, "close"), path)
            self.assertEqual(out.count(b"HTTP/1."), 1, path)            # one answer: the body is not a request
        self.quiet()

    def test_two_content_lengths_on_the_open_port_are_a_plain_400(self):
        # the port of dev-speed onto 3.3.7.5: the bare open port tells nothing of Kaskad — no JSON, no Server
        import ssl
        port = self.public_on()
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        for path in ("/anything", self.pb + "/api/host/speed/up?id=x"):
            s = ctx.wrap_socket(self.sock(port), server_hostname="127.0.0.1")
            try:
                s.sendall(("POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 0\r\ncontent-length: 0\r\n\r\n"
                           % (path, port)).encode())
                out = self.drain_to_close(s)
            finally:
                s.close()
            st, h, body = parse(out)
            self.assertEqual((st, h.get("connection")), (400, "close"), path)
            self.assertNotIn("server", h, path)
            self.assertTrue(body.startswith(b"<html>"), (path, body[:60]))
        self.quiet()


    def test_silence_after_the_headers_is_cut_by_the_body_deadline_not_by_the_socket_timeout(self):
        # REV-bc12-2 З-2 (M1): every read waits only what is left of BODY_S — not SOCKET_TIMEOUT per read
        with mock.patch.object(server, "BODY_S", 1.0):
            t0 = time.monotonic()
            s = self.raw_post(MIB, [], 0)
            try:
                out = self.drain_to_close(s, timeout=5)
            finally:
                s.close()
            self.assertLess(time.monotonic() - t0, 2.5)
            self.assertEqual(out, b"")
        self.quiet()

    def test_after_a_body_in_time_the_socket_gets_its_usual_timeout_back(self):
        # REV-bc12-2 З-2 (M2): keep-alive after a body read by the deadline — the next request waits SOCKET_TIMEOUT,
        # not the rest of BODY_S
        seen = []
        orig = server.Handler._read_body

        def spy(handler, *a, **kw):
            try:
                return orig(handler, *a, **kw)
            finally:
                seen.append(handler.connection.gettimeout())
        with mock.patch.object(server, "BODY_S", 3.0), mock.patch.object(server.Handler, "_read_body", spy):
            s = self.raw_post(30, [b"0123456789"] * 3, 0.05)
            try:
                st, h, body = parse(self.drain_to_close(s))
            finally:
                s.close()
        self.assertEqual(st, 401)
        self.assertEqual(seen, [server.SOCKET_TIMEOUT])
        self.quiet()

    def test_big_bodies_of_the_backup_with_a_login_are_not_cut_by_the_body_deadline(self):
        # REV-bc12-2 З-2 (M8): BODY_S is only for bodies ≤ MAX_BODY; a copy of 20 MB with a session reads as before
        auth.set_password("секрет-замера-4")
        cookie = self.login("секрет-замера-4")
        body = b"x" * 4096
        with mock.patch.object(server, "BODY_S", 1.0):
            s = self.sock(timeout=10)
            try:
                s.sendall(("POST /api/backup/preview HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Kaskad-Admin: 1\r\n"
                           "X-Forwarded-For: 198.51.100.9\r\nCookie: %s=%s\r\nConnection: close\r\n"
                           "Content-Length: %d\r\n\r\n" % (self.port, auth.COOKIE, cookie, len(body))).encode())
                s.sendall(body[:100])
                time.sleep(1.5)
                s.sendall(body[100:])
                out = self.drain_to_close(s, timeout=8)
            finally:
                s.close()
        st, h, doc = parse(out)
        self.assertTrue(out.startswith(b"HTTP/1."), out[:80])          # an answer, not a cut connection
        self.assertNotIn(st, (0, 401, 408))
        self.quiet()


class OrderOfChecksTest(SpeedCase):
    """§4.1 п. 1–5 with a session: 411/400/413 by the headers → (401 only without a session) → 403 Origin →
    403 X-Kaskad-Admin → the handler: 403/410 lease → 429 streams → 429 budget. Each before the body, each closes."""

    UP = "/api/host/speed/up?id=%s&r=1"

    def refused(self, headers, status, lid=None, word=None):
        h = {"X-Kaskad-Admin": "1"}
        h.update(headers)
        st, hd, body = self.head_only("POST", self.UP % (lid or "0" * 32), dict((k, v) for k, v in h.items()
                                                                               if v is not None))
        self.assertEqual(st, status, (headers, body))
        self.assertEqual(hd.get("connection"), "close", headers)
        doc = json.loads(body)
        self.assertIs(doc["ok"], False)
        if word:
            self.assertEqual(doc.get("code"), word)
        return doc

    def test_header_refusals_come_first(self):
        self.refused({"Transfer-Encoding": "chunked"}, 411)
        self.refused({"Transfer-Encoding": "chunked", "Content-Length": "10"}, 411)
        self.refused({}, 411)                                                           # no length at all
        self.refused({"Content-Length": "12a"}, 400)
        self.refused({"Content-Length": "²"}, 400)                                 # «²»: isdigit, not a number
        self.refused({"Content-Length": str(64 * MIB + 1)}, 413)
        self.refused({"Content-Length": "9" * 5000}, 413)                              # never int() of 5000 digits
        # the same class on the usual path of any route: «²» and 5000 digits are a refusal, not a 500
        for cl, status in (("\u00b2", 400), ("9" * 5000, 413)):
            got = self.head_only("POST", "/api/gw/users", {"X-Kaskad-Admin": "1", "Content-Length": cl})
            self.assertEqual(got[0], status, cl[:5])
        # the header checks stand before Origin and X-Kaskad-Admin
        self.refused({"Content-Length": str(64 * MIB + 1), "X-Kaskad-Admin": None}, 413)
        self.refused({"Transfer-Encoding": "chunked", "Origin": "https://evil.example.com"}, 411)
        self.quiet()

    def test_origin_and_the_admin_header_before_the_handler(self):
        lid = self.start()["id"]
        self.refused({"Content-Length": str(MIB), "Origin": "https://evil.example.com"}, 403, lid)
        self.refused({"Content-Length": str(MIB), "Sec-Fetch-Site": "cross-site"}, 403, lid)
        doc = self.refused({"Content-Length": str(MIB), "X-Kaskad-Admin": None}, 403, lid)
        self.assertEqual(doc["error"], self.call("POST", "/api/gw/users", {}, csrf=False).json()["error"])
        self.assertEqual(self.lease().open["up"], 0)                                     # the handler never ran
        self.assertEqual(self.lease().left["up"], speed.DEFAULTS["side_bytes"])

    def test_the_lease_then_streams_then_budget_all_before_the_body(self):
        self.refused({"Content-Length": str(MIB)}, 403, "f" * 32, "lease")             # no lease at all
        lid = self.start()["id"]
        self.refused({"Content-Length": str(MIB)}, 403, "e" * 32, "lease")             # another id
        held = []
        try:
            for _ in range(4):
                s = self.sock()
                s.sendall(("POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Kaskad-Admin: 1\r\nContent-Length: %d"
                           "\r\n\r\n" % (self.UP % lid, self.port, MIB)).encode() + b"x" * 10)
                held.append(s)
            self.wait(lambda: self.lease().open["up"] == 4)
            doc = self.refused({"Content-Length": str(MIB)}, 429, lid, "streams")
            self.assertEqual(doc["data"], {"reason": "streams"})
        finally:
            for s in held:
                s.close()
        self.wait(lambda: self.lease().open["up"] == 0)
        left = self.lease().left["up"]
        self.assertEqual(left, speed.DEFAULTS["side_bytes"] - 40)                        # 4 × 10 bytes received
        self.lease().left["up"] = MIB - 1
        doc = self.refused({"Content-Length": str(MIB)}, 429, lid, "budget")
        self.assertEqual(doc["data"], {"reason": "budget", "left": MIB - 1})
        self.lease().last = time.monotonic()
        self.lease().mono0 -= 50                                                          # 45 s are over
        self.refused({"Content-Length": str(MIB)}, 410, lid, "expired")
        self.quiet()

    def test_head_with_login_is_405_after_the_login_and_before_the_handler(self):
        lid = self.start()["id"]
        for path in ("/api/host/speed/ping?id=%s" % lid, "/api/host/speed/down?id=%s&bytes=1048576" % lid,
                     "/api/host/speed/budget"):
            r = self.call("HEAD", path)
            self.assertEqual(r.status, 405, path)
        self.assertEqual((self.lease().open["down"], self.lease().left["down"]), (0, speed.DEFAULTS["side_bytes"]))
        self.assertEqual(self.call("HEAD", "/api/host/health").status, 200)               # other routes: as before
        self.assertEqual(self.call("HEAD", "/api/host/speed/start").status, 405)          # a POST route: as any

    def test_a_route_without_body_reads_it_and_stream_needs_admin_post(self):
        r = routes.Route("GET", "/", lambda req: None, "public", False)                   # as _setup_entry() does
        self.assertEqual((r.body, r.body_max, r.head), ("read", 0, True))
        for kw in ({"auth": "public"}, {"body_max": 0}, {"body_max": True}, {"method": "GET"}):
            args = dict({"method": "POST", "auth": "admin", "body_max": 5}, **kw)
            with self.assertRaises(ValueError, msg=kw):
                routes.route(args["method"], r"/x", auth=args["auth"], body="stream", body_max=args["body_max"])
        with self.assertRaises(ValueError):
            routes.route("POST", r"/x", body="file")
        up = [x for x in routes.ROUTES if x.pattern == r"/api/host/speed/up"][0]
        self.assertEqual((up.body, up.body_max, up.auth, up.csrf), ("stream", 64 * MIB, "admin", True))
        self.assertEqual(sorted(x.pattern for x in routes.ROUTES if x.pattern.startswith(r"/api/host/speed/")
                                and x.head), [])


class LeaseRoutesTest(SpeedCase):
    def test_start_contract_409_and_429_with_retry_after(self):
        out = self.start()
        self.assertEqual(sorted(out), ["id", "left_day_bytes", "limits", "ok", "tests_left_day", "tests_left_hour",
                                       "until_s", "via", "vpn"])
        self.assertEqual((out["via"], out["vpn"], out["until_s"]), ("tunnel", False, 45))
        self.assertEqual(out["limits"]["down_bytes"], 800 * MB)
        self.assertEqual(self.runner.commands(["ip", "-j"]), [])                         # the tunnel: no route asked
        r = self.call("POST", "/api/host/speed/start", {})
        self.assertEqual(r.status, 409)
        self.assertEqual(r.json()["data"]["busy"], True)
        self.assertEqual(r.headers["retry-after"], str(r.json()["data"]["retry_after"]))
        self.ok("POST", "/api/host/speed/finish", {"id": out["id"]})
        doc = speed.read_counts()
        doc["starts"] = [time.time() - 10] * 6
        with open(speed.store_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        for lang, word in (("ru", "Предел проверок за час"), ("en", "hourly test limit")):
            r = self.call("POST", "/api/host/speed/start", {}, headers={"X-Kaskad-Lang": lang})
            self.assertEqual(r.status, 429)
            d = r.json()
            self.assertEqual((d["code"], d["data"]["reason"], d["data"]["limit"], d["data"]["used"]),
                             ("hour", "hour", 6, 6))
            self.assertIn(word, d["error"])
            self.assertIn("6", d["error"])
            self.assertTrue(int(r.headers["retry-after"]) > 3500)

    def test_limits_from_the_state_are_checked(self):
        with state.locked():
            st = state.load()
            st.setdefault("gateway", {}).setdefault("hostmon", {})["speed"] = {"side_bytes": 10 ** 15,
                                                                               "tests_hour": "5"}
            state.save(st)
        out = self.start()
        self.assertEqual(out["limits"]["down_bytes"], 2 * 1000 * MB)
        self.assertEqual(out["tests_left_hour"], 5)
        b = self.ok("GET", "/api/host/speed/budget")
        self.assertEqual(b["caps"]["side_bytes"], 2 * 1000 * MB)
        self.assertEqual((b["tests_hour"], b["tests_day"], b["today_bytes"]), (1, 1, 4 * 1000 * MB))

    def test_cookie_and_tunnel_are_different_keys(self):
        auth.set_password("секрет-замера-1")
        cookie = self.login("секрет-замера-1")
        lid = self.ok("POST", "/api/host/speed/start", {}, cookie=cookie, tunnel=False)["id"]
        self.assertEqual(self.call("GET", "/api/host/speed/ping?id=%s" % lid).status, 403)       # the tunnel
        r = self.call("GET", "/api/host/speed/ping?id=%s&r=1" % lid, cookie=cookie, tunnel=False)
        self.assertEqual(r.status, 204)
        self.assertRegex(r.headers["server-timing"], r"^app;dur=[0-9.]+$")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(self.lease().key, hashlib.sha256(cookie.encode()).hexdigest())
        r = self.call("POST", "/api/host/speed/finish", {"id": lid})
        self.assertEqual(r.status, 403)
        self.ok("POST", "/api/host/speed/finish", {"id": lid}, cookie=cookie, tunnel=False)

    def test_finish_contract_and_a_repeat(self):
        lid = self.start()["id"]
        self.assertEqual(len(self.call("GET", "/api/host/speed/down?id=%s&bytes=2000000" % lid).body), 2000000)
        r = self.call("POST", "/api/host/speed/up?id=%s" % lid, raw_body=b"u" * 300000)
        self.assertEqual(r.json(), {"ok": True, "bytes": 300000})
        res = self.ok("POST", "/api/host/speed/finish", {"id": lid})
        self.assertEqual((res["bytes_down"], res["bytes_up"]), (2000000, 300000))
        self.assertEqual(res["cpu_limited"], {"down": None, "up": None})
        self.assertIsNone(res["up"]["bps"])                                              # far under 2 s
        self.assertEqual(self.ok("POST", "/api/host/speed/finish", {"id": lid}), res)
        self.assertEqual(self.call("GET", "/api/host/speed/ping?id=%s" % lid).status, 403)
        self.assertEqual(self.call("POST", "/api/host/speed/finish", {"id": "x"}).status, 403)
        self.assertEqual(self.call("POST", "/api/host/speed/finish", raw_body=b"[1]").status, 400)
        self.assertNotIn("127.0.0.1", json.dumps(speed.read_counts()))


class BytesTest(SpeedCase):
    def test_down_exact_bytes_headers_and_keep_alive(self):
        import http.client
        lid = self.start()["id"]
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            n = 3 * MIB + 5
            c.request("GET", "/api/host/speed/down?id=%s&bytes=%d&r=7" % (lid, n), headers={"Host": "127.0.0.1"})
            r = c.getresponse()
            data = r.read()
            sock = c.sock
            self.assertEqual((r.status, len(data), int(r.getheader("Content-Length"))), (200, n, n))
            self.assertEqual(r.getheader("Content-Type"), "application/octet-stream")
            self.assertEqual(r.getheader("Cache-Control"), "no-store")
            self.assertEqual(r.getheader("X-Accel-Buffering"), "no")
            self.assertIsNone(r.getheader("Content-Encoding"))
            self.assertRegex(r.getheader("Server-Timing"), r"^app;dur=[0-9.]+$")
            self.assertIsNone(r.getheader("Connection"))
            self.assertFalse(r.will_close)
            self.assertEqual(data[:MIB], data[MIB:2 * MIB])
            for step in (1 << 12, 1 << 16, 1 << 19):
                self.assertNotEqual(data[:step], data[step:2 * step])
            self.assertNotEqual(data[:4096], b"\0" * 4096)
            c.request("GET", "/api/host/speed/ping?id=%s" % lid, headers={"Host": "127.0.0.1"})
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 204)
            self.assertIs(c.sock, sock)                                                  # the same connection
            body = b"z" * (2 * MIB)
            c.request("POST", "/api/host/speed/up?id=%s" % lid, body=body,
                      headers={"Host": "127.0.0.1", "X-Kaskad-Admin": "1"})
            r = c.getresponse()
            self.assertEqual(json.loads(r.read()), {"ok": True, "bytes": 2 * MIB})
            self.assertFalse(r.will_close)
            self.assertIs(c.sock, sock)
        finally:
            c.close()
        self.assertEqual(self.lease().moved, {"down": n, "up": 2 * MIB})
        for q, status in (("bytes=%d" % (256 * MIB + 1), 413), ("bytes=0", 400), ("bytes=abc", 400), ("", 400),
                          ("bytes=-5", 400), ("bytes=1e6", 400), ("bytes=10000000000000", 413),
                          ("bytes=" + "9" * 5000, 413), ("bytes=00", 400), ("bytes=%C2%B2", 400)):
            r = self.call("GET", "/api/host/speed/down?id=%s&%s" % (lid, q))
            self.assertEqual(r.status, status, q)
        self.assertEqual(self.lease().open, {"down": 0, "up": 0})
        self.quiet()

    def test_a_client_that_goes_away_is_quiet_and_gives_everything_back(self):
        lid = self.start()["id"]
        s = self.sock()
        s.sendall(("GET /api/host/speed/down?id=%s&bytes=%d HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                   % (lid, 64 * MIB)).encode())
        got = 0
        while got < 300000:
            got += len(s.recv(65536))
        s.close()
        self.wait(lambda: self.lease().open["down"] == 0)
        L = self.lease()
        self.assertGreater(L.left["down"], speed.DEFAULTS["side_bytes"] - 64 * MIB)    # the unsent part came back
        self.assertEqual(L.left["down"] + L.moved["down"], speed.DEFAULTS["side_bytes"])
        # the same with an upload cut in the middle
        s = self.sock()
        s.sendall(("POST /api/host/speed/up?id=%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                   "Content-Length: %d\r\n\r\n" % (lid, 8 * MIB)).encode() + b"q" * 500000)
        self.wait(lambda: self.lease().moved["up"] == 500000)
        s.close()
        self.wait(lambda: self.lease().open["up"] == 0)
        self.assertEqual(self.lease().left["up"], speed.DEFAULTS["side_bytes"] - 500000)
        self.assertEqual(self.call("GET", "/api/host/speed/ping?id=%s" % lid).status, 204)  # the lease lives
        self.quiet()

    def test_a_client_that_does_not_read_is_cut_by_the_deadline(self):
        lid = self.start()["id"]
        with mock.patch.object(speed, "STREAM_S", 1.0):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            s.connect(("127.0.0.1", self.port))
            try:
                t0 = time.monotonic()
                s.sendall(("GET /api/host/speed/down?id=%s&bytes=%d HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                           % (lid, 256 * MIB)).encode())
                self.wait(lambda: self.lease().open["down"] == 1, timeout=3)
                self.assertTrue(self.wait(lambda: self.lease().open["down"] == 0, timeout=6))
                self.assertLess(time.monotonic() - t0, 4.0)
                self.drain_to_close(s)                                   # short of Content-Length: closed
            finally:
                s.close()
        self.assertEqual(self.lease().left["down"] + self.lease().moved["down"], speed.DEFAULTS["side_bytes"])
        self.quiet()

    def test_a_lease_closed_in_the_middle_of_down_closes_the_connection(self):
        # REV-bc12 З-1: an answer shorter than its Content-Length must never keep the connection alive
        lid = self.start()["id"]
        s = self.sock(timeout=5)
        try:
            s.sendall(("GET /api/host/speed/down?id=%s&bytes=%d HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                       % (lid, 64 * MIB)).encode())
            got = 0
            while got < 300000:
                got += len(s.recv(65536))
            self.ok("POST", "/api/host/speed/finish", {"id": lid})
            got += len(self.drain_to_close(s))
        finally:
            s.close()
        self.assertLess(got, 64 * MIB)
        self.quiet()

    def test_a_lease_closed_in_the_middle_of_up_stops_it_and_the_day_gets_only_what_came(self):
        # REV-bc12 З-1 and С-1: finish before the server sees the end of the upload; the unused reserve goes back
        lid = self.start()["id"]
        s = self.sock(timeout=5)
        try:
            s.sendall(("POST /api/host/speed/up?id=%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       "Content-Length: %d\r\n\r\n" % (lid, 8 * MIB)).encode() + b"q" * 500000)
            self.wait(lambda: self.lease().moved["up"] == 500000)
            L = self.lease()
            self.ok("POST", "/api/host/speed/finish", {"id": lid})
            s.sendall(b"q" * 1000)                                       # one read: nothing left unread, a clean close
            st, h, body = parse(self.drain_to_close(s))
        finally:
            s.close()
        got = json.loads(body)["bytes"]
        self.assertEqual((st, h.get("connection")), (200, "close"))
        self.assertTrue(500000 <= got <= 501000, got)
        self.wait(lambda: L.open["up"] == 0)
        day = [v for k, v in speed.read_counts().items() if k not in ("starts", "refunds")]
        self.assertEqual([(d["rx"], d["tx"]) for d in day], [(got, 0)])
        self.quiet()

    def test_up_of_32_mib_keeps_the_memory_and_fills_the_buckets(self):
        lid = self.start()["id"]
        body = b"m" * (32 * MIB)
        head = ("POST /api/host/speed/up?id=%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                "Content-Length: %d\r\n\r\n" % (lid, len(body))).encode()
        s = self.sock(timeout=20)
        tracemalloc.start()
        try:
            s.sendall(head)
            s.sendall(memoryview(body))
            out = b""
            while b"\r\n\r\n" not in out or not out.endswith(b"}"):
                out += s.recv(4096)
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            s.close()
        st, h, b = parse(out)
        self.assertEqual((st, json.loads(b)), (200, {"ok": True, "bytes": 32 * MIB}))
        self.assertNotEqual(h.get("connection"), "close")
        self.assertLess(peak, MIB, peak)
        self.assertEqual(sum(self.lease().buckets), 32 * MIB)


class SlowSenderTest(SpeedCase):
    """A slow upload (1 and 5 Mbit/s): the buckets are even, the speed by ``up_rate`` is within 5 %, the first byte is
    seen at once — this catches ``readinto`` (which waits for the whole 64 KiB) in place of ``readinto1``."""

    def send(self, rate_bps, seconds=2.5, tick=0.02):
        lid = self.start()["id"]
        piece = int(rate_bps / 8 * tick)
        n = int(seconds / tick)
        s = self.sock(timeout=20)
        try:
            s.sendall(("POST /api/host/speed/up?id=%s HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Kaskad-Admin: 1\r\n"
                       "Content-Length: %d\r\n\r\n" % (lid, piece * n)).encode())
            time.sleep(0.05)
            L = self.lease()
            t0 = time.monotonic()
            for i in range(n):
                delay = t0 + i * tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                s.sendall(b"s" * piece)
            out = b""
            while not out.endswith(b"}"):
                out += s.recv(4096)
        finally:
            s.close()
        self.assertEqual(json.loads(parse(out)[2])["bytes"], piece * n)
        first = L.mono0 + L.up_first / 1000.0
        self.assertLess(first - t0, 0.1, rate_bps)
        res = self.ok("POST", "/api/host/speed/finish", {"id": lid})
        return res["up"], piece * 1000 / (tick * 1000) / 10                     # bytes in 100 ms

    def test_one_and_five_mbit(self):
        with mock.patch.object(speed, "UP_SKIP_MS", 300), mock.patch.object(speed, "UP_RAMP_MS", 0), \
                mock.patch.object(speed, "UP_MIN_MS", 1000):
            for rate in (1e6, 5e6):
                up, per_bucket = self.send(rate)
                self.assertIsNotNone(up["bps"], rate)
                self.assertLess(abs(up["bps"] - rate) / rate, 0.05, (rate, up))
                even = [b for b in up["buckets"] if 0.4 * per_bucket <= b <= 1.6 * per_bucket]
                self.assertGreaterEqual(len(even), 0.9 * len(up["buckets"]), (rate, up["buckets"]))


class ViaRoutesTest(SpeedCase):
    def test_public_is_direct(self):
        port = self.public_on()
        auth.set_password("секрет-замера-2")
        # 3.3.7.5: the panel of the open mode lives only under its secret prefix (``self.pb``)
        r = self.call("POST", self.pb + "/api/login", {"login": "admin", "password": "секрет-замера-2"}, tunnel=False,
                      port=port, https=True)
        cookie = self.cookie_of(r)
        out = self.ok("POST", self.pb + "/api/host/speed/start", {}, cookie=cookie, tunnel=False, port=port,
                      https=True)
        self.assertEqual(out["via"], "direct")
        self.assertEqual(out["vpn"], "self")            # the peer 127.0.0.1 is the address of the open mode itself
        self.assertEqual(self.runner.commands(["ip", "-j"]), [])     # an own address: the route is not asked


class ServerCheckTest(SpeedCase):
    """R-bc5 (SPEC §15 п. 6, HANDOFF r1 «409 и проверка сервера»; решение владельца 24.10): проверка сервера r1 и замер
    из браузера не идут вместе — оба грузят канал сервера."""

    RID = "0123456789"

    def busy(self, text):
        self.units = text

    def setUp(self):
        super().setUp()
        self.units = ""
        self.runner.on(["systemctl", "list-units"], lambda argv, inp: (0, self.units, ""))

    def meta(self, doc):
        from kaskad.bench import run as brun
        os.makedirs(brun.work_dir(self.RID), exist_ok=True)
        path = os.path.join(brun.work_dir(self.RID), "meta.json")
        if doc is None:
            if os.path.exists(path):
                os.remove(path)
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_a_running_server_check_is_409_busy_with_the_rest_of_it(self):
        self.busy("kaskad-bench-%s.service loaded active running Kaskad 3: server check\n" % self.RID)
        self.meta({"mode": "full", "ts": int(time.time()) - 25})
        r = self.call("POST", "/api/host/speed/start", {})
        doc = r.json()
        self.assertEqual((r.status, doc["code"], doc["data"]["busy"], doc["data"]["what"]), (409, "busy", True, "check"))
        self.assertEqual(doc["error"], tr("panel.speed.err.check"))                     # М-5: not «another tab»
        self.assertNotIn("вкладке", doc["error"])
        self.assertTrue(98 <= doc["data"]["retry_after"] <= 101, doc)                    # 125 − 25 с
        self.assertEqual(r.headers.get("retry-after"), str(doc["data"]["retry_after"]))
        self.assertIsNone(self.lease())
        self.assertEqual(speed.read_counts().get("starts") or [], [])                   # not counted as a test
        # no meta yet (the check waits for «свободен» before its first step): the estimate of a quick one
        self.meta(None)
        self.assertEqual(self.call("POST", "/api/host/speed/start", {}).json()["data"]["retry_after"], 65)
        # a check far over its estimate still waits a little, never 0
        self.meta({"mode": "quick", "ts": int(time.time()) - 600})
        self.assertEqual(self.call("POST", "/api/host/speed/start", {}).json()["data"]["retry_after"], 5)
        # the check is over: the test starts
        self.busy("")
        self.assertTrue(self.start()["id"])

    def test_the_server_check_from_the_panel_waits_for_a_live_lease(self):
        lid = self.start()["id"]
        r = self.call("POST", "/api/host/bench", {"mode": "quick", "confirm": "0" * 8})
        doc = r.json()
        self.assertEqual((r.status, doc["code"]), (409, "speed_test"))
        self.assertTrue(1 <= doc["retry_after"] <= speed.IDLE_S, doc)                    # a quiet lease ends in 10 s
        self.assertIn("замер от вашего устройства", doc["error"])
        self.ok("POST", "/api/host/speed/finish", {"id": lid})
        r = self.call("POST", "/api/host/bench", {"mode": "quick", "confirm": "0" * 8})
        self.assertNotEqual(r.json().get("code"), "speed_test")                          # the lease is closed

    def test_budget_says_the_way_in_without_a_command(self):
        n = len(self.runner.calls)
        b = self.ok("GET", "/api/host/speed/budget")
        self.assertEqual(b["via"], "tunnel")
        self.assertEqual(self.runner.calls[n:], [])
        self.assertEqual(b["caps"]["side_bytes"], speed.DEFAULTS["side_bytes"])

class FromOldDataTest(SpeedCase):
    """Правило владельца 24.10 «старые данные → новая»: узел 3.3.7.9 после обновления — state.json без
    gateway.hostmon.speed и без файла счётчика замеров. budget — нули и пределы по умолчанию, файла не создаёт;
    первый замер создаёт его 0600 и считает; обратно (откат на 3.3.7.9) файл ей не мешает — она его не читает."""

    def test_no_speed_key_and_no_counter_file(self):
        from kaskad import state
        st = state.load()
        self.assertNotIn("speed", (st.get("gateway") or {}).get("hostmon") or {})
        self.assertFalse(os.path.exists(speed.store_path()))
        b = self.ok("GET", "/api/host/speed/budget")
        self.assertEqual((b["today_bytes"], b["tests_hour"], b["tests_day"]), (0, 0, 0))
        self.assertEqual(b["caps"]["side_bytes"], speed.DEFAULTS["side_bytes"])
        self.assertEqual(b["caps"]["tests_hour"], speed.DEFAULTS["tests_hour"])
        self.assertFalse(os.path.exists(speed.store_path()))                    # чтение ничего не пишет
        lid = self.start()["id"]
        self.assertEqual(oct(os.stat(speed.store_path()).st_mode & 0o777), "0o600")
        self.assertEqual(self.ok("GET", "/api/host/speed/budget")["tests_hour"], 1)
        self.ok("POST", "/api/host/speed/finish", {"id": lid})


class ProxyViaTest(ProxyCase):
    def setUp(self):
        super().setUp()
        self.runner = sysrun.FakeRunner()
        self.dev = "eth0"
        self.runner.on(["ip", "-j", "route", "show", "default"], (0, DEFAULT, ""))
        self.runner.on(["ip", "-j", "route", "get"],
                       lambda argv, inp: (0, json.dumps([{"dst": argv[-1], "dev": self.dev}]), ""))
        self.fronts = False
        for p in (mock.patch.object(speed, "SPEED", speed.Speed(cpu=lambda: None)),
                  mock.patch.object(phost.sysrun, "default_runner", lambda: self.runner),
                  mock.patch.object(phost, "_fronts", lambda: self.fronts)):
            p.start()
            self.addCleanup(p.stop)

    def test_proxy_front_and_the_vpn_of_this_node(self):
        self.proxy_on()
        auth.set_password("секрет-замера-3")
        cookie = self.cookie_of(self.pcall("POST", "/api/login", {"login": "admin", "password": "секрет-замера-3"}))
        out = self.pcall("POST", "/api/host/speed/start", {}, cookie=cookie).json()
        self.assertEqual((out["via"], out["vpn"]), ("proxy", False))
        self.assertIn(["ip", "-j", "route", "get", "198.51.100.7"], self.runner.commands())
        self.pcall("POST", "/api/host/speed/finish", {"id": out["id"]}, cookie=cookie)
        self.fronts, self.dev = True, "awg0"
        out = self.pcall("POST", "/api/host/speed/start", {}, cookie=cookie).json()
        self.assertEqual((out["via"], out["vpn"]), ("front", "self"))
        # no address of the client in the counter, the answer or the journal of the test
        self.assertNotIn("198.51.100.7", json.dumps(speed.read_counts()))
