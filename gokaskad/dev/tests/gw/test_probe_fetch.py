"""``safe_get`` of the unit of probes — SSRF guards, DNS rebinding, redirects, limits, what the journal sees, the limiter
of «Проверить ссылку», SOCKS5 to ``probe-<id>`` (docs/k3/SPEC-stage1-exits §5.3, §5.4, §11.1 «probe/fetch.py»; T6)."""

import json
import os
import socket
import struct
import threading
import time
from unittest import mock

from kaskad.gw.probe import fetch, geo, services, speed
from tests.helpers import RootCase


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeSock(object):
    """A connected TLS socket of a fake server: ``chunks`` — what each recv gives (bytes), a float — that many seconds
    pass first (more than the timeout — socket.timeout)."""

    def __init__(self, clock, chunks, dt=0.0):
        self.clock, self.chunks, self.dt = clock, list(chunks), dt
        self.sent = b""
        self.timeout = None
        self.reset = False
        self.closed = False

    def sendall(self, b):
        self.sent += b

    def settimeout(self, t):
        self.timeout = t

    def recv_into(self, buf, n=0):
        n = n or len(buf)
        while self.chunks and isinstance(self.chunks[0], float):
            wait = self.chunks.pop(0)
            if self.timeout is not None and wait > self.timeout:
                self.clock.t += self.timeout
                raise socket.timeout()
            self.clock.t += wait
        if not self.chunks:
            return 0
        c = self.chunks[0]
        part, rest = c[:n], c[n:]
        if rest:
            self.chunks[0] = rest
        else:
            self.chunks.pop(0)
        buf[:len(part)] = part
        self.clock.t += self.dt
        return len(part)

    def setsockopt(self, level, opt, val):
        if opt == socket.SO_LINGER and struct.unpack("ii", val) == (1, 0):
            self.reset = True

    def close(self):
        self.closed = True


def resp(status=200, body=b"", headers=()):
    head = "HTTP/1.1 %d X\r\n" % status + "".join("%s: %s\r\n" % h for h in headers)
    if not any(k.lower() in ("content-length", "transfer-encoding") for k, _ in headers):
        head += "Content-Length: %d\r\n" % len(body)
    return head.encode("ascii") + b"\r\n" + body


class Base(RootCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.p = [mock.patch.object(fetch, "_mono", self.clock)]
        for p in self.p:
            p.start()
        self.opened = []                                 # (ip, port, host)
        self.socks = []
        self.answers = []                                # per open: list of chunks
        self.dns = {}                                    # host -> [answers, ...] in order
        self.resolved = []

    def tearDown(self):
        for p in self.p:
            p.stop()
        super().tearDown()

    def fake_open(self, via, ip, port, host, lim):
        self.opened.append((ip, port, host))
        s = FakeSock(self.clock, self.answers.pop(0), dt=getattr(self, "dt", 0.0))
        self.socks.append(s)
        return s, 12.5

    def fake_resolve(self, host, via, guard, lim=None):
        self.resolved.append(host)
        seq = self.dns[host]
        ips = seq.pop(0) if len(seq) > 1 else seq[0]
        return [guard.addr(a) for a in ips]

    def get(self, url, guard=None, **kw):
        with mock.patch.object(fetch, "open_tls", self.fake_open), \
                mock.patch.object(fetch, "resolve", self.fake_resolve):
            return fetch.safe_get(url, fetch.Via("awg", mark=257), fetch.limits(**kw), guard or fetch.Guard())


class UrlGuardTest(Base):
    def code(self, fn, *a):
        with self.assertRaises(fetch.FetchError) as cm:
            fn(*a)
        return cm.exception.code

    def test_only_https_without_userinfo_and_bad_ports(self):
        g = fetch.Guard(ports=[10854, 12701])
        self.assertEqual(self.code(g.url, "http://example.com/"), "scheme")
        self.assertEqual(self.code(g.url, "ftp://example.com/"), "scheme")
        self.assertEqual(self.code(g.url, "https://user:pass@example.com/"), "userinfo")
        self.assertEqual(self.code(g.url, "https://user@example.com/"), "userinfo")
        for port in (25, 53, 10854, 12701):
            self.assertEqual(self.code(g.url, "https://example.com:%d/" % port), "port")
        self.assertEqual(self.code(g.url, "https://[2001:db8::1]/"), "ipv6")
        self.assertEqual(self.code(g.url, "https://exa_mple/"), "host")
        self.assertEqual(self.code(g.url, "https://localhost/"), "host")
        self.assertEqual(self.code(g.url, "https://exa mple.com/"), "url")
        self.assertEqual(g.url("https://Example.com/a/b?x=1&y=2#frag"), ("example.com", 443, "/a/b?x=1&y=2"))
        self.assertEqual(g.url("https://example.com:8443"), ("example.com", 8443, "/"))

    def test_addresses_that_are_refused(self):
        g = fetch.Guard(node_addrs=["93.184.216.34/32"], gw_nets=["10.77.0.0/16", "45.67.0.0/24"])
        for a in ("10.1.2.3", "127.0.0.1", "169.254.169.254", "100.64.1.1", "198.18.0.1", "198.19.255.1",
                  "224.0.0.1", "239.1.1.1", "240.0.0.1", "0.1.2.3", "192.168.1.1", "172.16.0.1",
                  "93.184.216.34", "45.67.0.9", "10.77.0.5", "::1", "2001:4860:4860::8888"):
            self.assertEqual(self.code(g.addr, a), "addr", a)
        self.assertEqual(g.addr("8.8.8.8"), "8.8.8.8")
        self.assertEqual(g.addr("93.184.216.35"), "93.184.216.35")

    def test_one_bad_address_of_the_answer_refuses_all(self):
        doc = {"Status": 0, "Answer": [{"type": 1, "data": "8.8.8.8"}, {"type": 1, "data": "10.0.0.1"}]}
        self.answers = [[resp(200, json.dumps(doc).encode())]]
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.resolve("a.example.com", fetch.Via("awg", mark=257), fetch.Guard())
        self.assertEqual(cm.exception.code, "addr")
        self.assertEqual(self.opened, [("1.1.1.1", 443, "1.1.1.1")])     # DoH through the exit, to 1.1.1.1
        self.assertIn(b"GET /dns-query?name=a.example.com&type=A ", self.socks[0].sent)
        self.assertIn(b"Accept: application/dns-json", self.socks[0].sent)

    def test_resolver_answer(self):
        doc = {"Status": 0, "Answer": [{"type": 5, "data": "b.example.net."}, {"type": 1, "data": "8.8.4.4"}]}
        self.answers = [[resp(200, json.dumps(doc).encode())], [resp(200, json.dumps({"Status": 3}).encode())]]
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            self.assertEqual(fetch.resolve("a.example.com", fetch.Via("awg", mark=257), fetch.Guard()), ["8.8.4.4"])
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.resolve("nx.example.com", fetch.Via("awg", mark=257), fetch.Guard())
        self.assertEqual(cm.exception.code, "dns")
        # an IP literal is checked as it is, no DoH
        self.assertEqual(fetch.resolve("8.8.8.8", None, fetch.Guard()), ["8.8.8.8"])
        with self.assertRaises(fetch.FetchError):
            fetch.resolve("169.254.169.254", None, fetch.Guard())


class SafeGetTest(Base):
    def test_connects_to_the_checked_ip_with_the_name_as_host(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[resp(204)]]
        r = self.get("https://a.example.com/generate_204?token=SECRET")
        self.assertEqual(r["status"], 204)
        self.assertEqual(self.opened, [("8.8.8.8", 443, "a.example.com")])   # IP checked, SNI = the name
        sent = self.socks[0].sent.decode()
        self.assertTrue(sent.startswith("GET /generate_204?token=SECRET HTTP/1.1\r\n"))
        self.assertIn("Host: a.example.com\r\n", sent)
        self.assertIn("Accept-Encoding: identity\r\n", sent)
        self.assertIn("User-Agent: Kaskad-speedtest\r\n", sent)
        self.assertEqual(r["shown"], "a.example.com/generate_204")             # no query in what is shown
        self.assertTrue(self.socks[0].reset)

    def test_dns_rebinding_second_answer_is_checked_again(self):
        # the first answer is public; the redirect to the same name gets a private one — refused, never connected
        self.dns = {"a.example.com": [["8.8.8.8"], ["10.0.0.1"]]}
        self.answers = [[resp(302, headers=[("Location", "/next")])]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "addr")
        self.assertEqual(self.opened, [("8.8.8.8", 443, "a.example.com")])
        self.assertEqual(self.resolved, ["a.example.com", "a.example.com"])     # one resolve per hop, no other

    def test_redirects_at_most_three_and_never_to_http(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[resp(302, headers=[("Location", "/r%d" % i)])] for i in range(4)] + [[resp(200, b"ok")]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "redirects")
        self.assertEqual(len(self.opened), 4)
        self.opened, self.answers = [], [[resp(302, headers=[("Location", "/a")])] for _ in range(3)] + \
            [[resp(200, b"ok")]]
        self.assertEqual(self.get("https://a.example.com/")["redirects"], 3)
        self.answers = [[resp(301, headers=[("Location", "http://a.example.com/")])]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "downgrade")
        # a redirect to a refused port / address is checked by 1–3 again
        self.dns["b.example.com"] = [["127.0.0.1"]]
        self.answers = [[resp(302, headers=[("Location", "https://a.example.com:25/")])]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "port")
        self.answers = [[resp(302, headers=[("Location", "https://b.example.com/")])]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "addr")

    def test_byte_limit_stops_with_a_reset(self):
        self.dns = {"speed.example.com": [["8.8.8.8"]]}
        big = b"x" * (3 * 1000 * 1000)
        self.answers = [[resp(200, headers=[("Content-Length", str(len(big)))]) + big[:100000]] +
                        [big[i:i + 100000] for i in range(100000, len(big), 100000)]]
        self.dt = 0.05
        r = self.get("https://speed.example.com/", max_mb=1, max_s=30)
        self.assertEqual(r["stopped"], "max_bytes")
        self.assertLessEqual(r["bytes"], 1000 * 1000)
        self.assertGreaterEqual(r["bytes"], 1000 * 1000 - 100000)
        self.assertTrue(self.socks[0].reset)
        self.assertIsNotNone(r["mbps"])

    def test_time_limit_and_the_speed_after_warmup(self):
        self.dns = {"speed.example.com": [["8.8.8.8"]]}
        chunk = b"y" * 125000                          # 0.1 s each -> 10 Mbit/s
        self.answers = [[resp(200, headers=[("Content-Length", "100000000")])] + [chunk] * 200]
        self.dt = 0.1
        r = self.get("https://speed.example.com/", max_mb=50, max_s=3)
        self.assertEqual(r["stopped"], "max_s")
        self.assertLess(r["bytes"], 50 * 1000 * 1000)
        self.assertAlmostEqual(r["mbps"], 10.0, delta=0.5)
        self.assertFalse(r["short"])
        # a stalled server: the time limit, not a hang
        self.answers = [[resp(200, headers=[("Content-Length", "1000")]) + b"z" * 10, 50.0]]
        r = self.get("https://speed.example.com/", max_mb=1, max_s=5)
        self.assertIn(r["stopped"], ("max_s", "stall"))

    def test_ceilings_of_the_limits(self):
        lim = fetch.limits(max_mb=500, max_s=600, connect_s=60, ttfb_s=60, headers=10 ** 6)
        self.assertEqual((lim["max_bytes"], lim["max_s"], lim["connect_s"], lim["ttfb_s"], lim["headers"]),
                         (50 * 1000 * 1000, 30.0, 5.0, 10.0, 16 * 1024))

    def test_headers_over_16k_and_no_answer_in_time(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[b"HTTP/1.1 200 OK\r\n" + b"X-Pad: " + b"a" * 17000 + b"\r\n\r\n"]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "headers")
        self.answers = [[11.0, resp(200)]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "ttfb")

    def test_chunked_body_is_kept_decoded(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[resp(200, b"5\r\nip=1.\r\na\r\n2.3.4\nloc=\r\n3\r\nDE\n\r\n0\r\n\r\n",
                              headers=[("Transfer-Encoding", "chunked")])]]
        with mock.patch.object(fetch, "open_tls", self.fake_open), \
                mock.patch.object(fetch, "resolve", self.fake_resolve):
            r = fetch.safe_get("https://a.example.com/t", fetch.Via("awg", mark=257), fetch.limits(), fetch.Guard(),
                               keep=4096)
        self.assertEqual(r["body"], b"ip=1.2.3.4\nloc=DE\n")


class ShownTest(Base):
    def test_query_and_fragment_never_shown_nor_logged(self):
        self.assertEqual(fetch.shown("https://h.example.com/p/q?token=SECRET#frag"), "h.example.com/p/q")
        self.assertEqual(fetch.shown("https://h.example.com"), "h.example.com/")
        with mock.patch.object(fetch, "safe_get", side_effect=fetch.FetchError("addr", addr="10.0.0.1")):
            r = speed.run(fetch.Via("awg", mark=257), "https://h.example.com/f?key=SECRET", 1, 5)
        self.assertEqual((r["host"], r["ok"], r["why"]), ("h.example.com/f", False, "addr"))
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertEqual(speed.target("default", 10 ** 7), "https://speed.cloudflare.com/__down?bytes=10000000")
        self.assertEqual(speed.target("https://my.example.com/f.bin", 10 ** 7), "https://my.example.com/f.bin")


class LinkLimiterTest(RootCase):
    def test_once_in_30_s_per_session_and_20_an_hour_per_node(self):
        lim = fetch.LinkLimiter()
        t = 1760000000.0
        self.assertEqual(lim.allow("cookie-a", t), (True, None, 0))
        ok, code, wait = lim.allow("cookie-a", t + 10)
        self.assertEqual((ok, code), (False, "rate_session"))
        self.assertTrue(0 < wait <= 21)
        self.assertTrue(lim.allow("cookie-a", t + 31)[0])
        for i in range(18):
            self.assertTrue(lim.allow("s-%d" % i, t + 40 + i)[0], i)
        ok, code, wait = lim.allow("s-x", t + 100)
        self.assertEqual((ok, code), (False, "rate_node"))
        self.assertTrue(lim.allow("s-x", t + 3601)[0])               # the first try of the hour has gone out
        # lim.path is paths.P("/run/kaskad/probe-link.json") under the temp KASKAD_ROOT of RootCase, not a file of the
        # repository:
        # k3-select: files=!* — reads no file of the repository
        with open(lim.path) as f:
            raw = f.read()
        self.assertNotIn("cookie-a", raw)                             # the session is kept as a hash


class WayTest(RootCase):
    def test_awg_mark_needs_root(self):
        class S(object):
            def setsockopt(self, *a):
                raise PermissionError(1, "Operation not permitted")

            def close(self):
                pass
        with mock.patch.object(fetch.socket, "socket", lambda *a: S()):
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.Via("awg", mark=257).connect("8.8.8.8", 443, 1)
        self.assertEqual(cm.exception.code, "mark")

    def test_via_of_pool_rows(self):
        self.assertEqual((fetch.via_of({"kind": "awg", "mark": 258, "index": 2}).kind,
                          fetch.via_of({"kind": "awg", "mark": 258, "index": 2}).mark), ("awg", 258))
        v = fetch.via_of({"kind": "xray", "index": 3})
        self.assertEqual((v.kind, v.port), ("xray", 12703))
        self.assertEqual(fetch.via_of({"kind": "vless", "index": 4}).port, 12704)      # 3.3.5's kind
        self.assertIsNone(fetch.via_of({"kind": "bad", "index": 5}))

    def test_socks5_connect_by_ipv4(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(2)
        port = srv.getsockname()[1]
        got = {}

        def serve(rep):
            c, _ = srv.accept()
            got["hello"] = c.recv(3)
            c.sendall(b"\x05\x00")
            got["req"] = c.recv(10)
            c.sendall(b"\x05" + bytes([rep]) + b"\x00\x01" + b"\x00" * 6)
            got["conn"] = c
        t = threading.Thread(target=serve, args=(0,))
        t.start()
        s = fetch.Via("xray", port=port).connect("8.8.4.4", 443, 2)
        t.join(5)
        self.assertEqual(got["hello"], b"\x05\x01\x00")
        self.assertEqual(got["req"], b"\x05\x01\x00\x01" + socket.inet_aton("8.8.4.4") + struct.pack("!H", 443))
        s.close()
        got["conn"].close()
        t = threading.Thread(target=serve, args=(5,))                  # REP 5: connection refused by the exit
        t.start()
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.Via("xray", port=port).connect("8.8.4.4", 443, 2)
        t.join(5)
        got["conn"].close()
        srv.close()
        self.assertEqual((cm.exception.code, cm.exception.args_), ("socks", {"rep": 5}))
        self.assertIn("5", cm.exception.text("en"))
        self.assertTrue(os.path.isdir(self.root))


class OnlyFetchErrorTest(Base):
    """REV-s1-t6 С1: nothing but FetchError leaves safe_get; a probe never raises."""

    def svc(self):
        return services.check(fetch.Via("awg", mark=257),
                              {"id": "x", "url": "https://a.example.com/", "expect": [204], "max_ms": 3000})

    def test_broken_location_is_a_fetch_error(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[resp(302, headers=[("Location", "https://[x/")])]]
        with self.assertRaises(fetch.FetchError) as cm:
            self.get("https://a.example.com/")
        self.assertEqual(cm.exception.code, "url")
        self.answers = [[resp(302, headers=[("Location", "https://[x/")])]]
        with mock.patch.object(fetch, "open_tls", self.fake_open), \
                mock.patch.object(fetch, "resolve", self.fake_resolve):
            r = self.svc()
        self.assertEqual((r["ok"], r["why"]), (False, "url"))

    def test_reset_while_sending_is_a_fetch_error(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[resp(204)]]
        orig = self.fake_open

        def fo(*a):
            s, t = orig(*a)

            def boom(b):
                raise ConnectionResetError(104, "reset")
            s.sendall = boom
            return s, t
        with mock.patch.object(fetch, "open_tls", fo), mock.patch.object(fetch, "resolve", self.fake_resolve):
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.safe_get("https://a.example.com/", fetch.Via("awg", mark=257), fetch.limits(), fetch.Guard())
            self.assertEqual((cm.exception.code, cm.exception.args_), ("read", {"what": "ConnectionResetError"}))
            self.answers = [[resp(204)]]
            r = self.svc()
        self.assertEqual((r["ok"], r["why"]), (False, "read"))

    def test_probes_never_raise(self):
        via = fetch.Via("awg", mark=257)
        with mock.patch.object(fetch, "safe_get", side_effect=RuntimeError("bug")):
            r = self.svc()
            self.assertEqual((r["ok"], r["why"], r["why_args"]), (False, "error", {"what": "RuntimeError"}))
            r = geo.trace(via)
            self.assertEqual((r["ok"], r["why"]), (False, "error"))
            r = speed.run(via, "default", 1, 5)
            self.assertEqual((r["ok"], r["why"]), (False, "error"))


class DeadlineTest(Base):
    """REV-s1-t6 С2: one deadline for the whole request; the headers within ttfb_s of the request."""

    def test_headers_trickled_byte_by_byte_are_cut(self):
        clock = self.clock

        class S(object):
            data = b"HTTP/1.1 200 OK\r\nX: " + b"a" * 2000 + b"\r\n\r\n"
            i = 0
            to = None

            def settimeout(self, t):
                self.to = t

            def recv_into(self, buf, n=0):
                if self.to is not None and 9.0 > self.to:
                    clock.t += self.to
                    raise socket.timeout()
                clock.t += 9.0                          # a byte every 9 s: under the 10 s of TTFB each time
                buf[0:1] = self.data[self.i:self.i + 1]
                self.i += 1
                return 1
        start = clock.t
        with self.assertRaises(fetch.FetchError) as cm:
            fetch._head(S(), fetch.limits(max_mb=1, max_s=10), start)
        self.assertEqual(cm.exception.code, "ttfb")
        self.assertLessEqual(clock.t - start, 10.0 + 1e-6)

    def test_one_deadline_over_resolver_and_hops(self):
        # every hop answers in 9 s (under TTFB) and redirects; DoH takes 4 s: the whole request ends by total_s
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        orig = self.fake_resolve

        def slow_resolve(*a, **k):
            self.clock.t += 4.0
            return orig(*a, **k)
        self.answers = [[9.0, resp(302, headers=[("Location", "/r%d" % i)])] for i in range(3)] + \
            [[9.0, resp(200, b"ok")]]
        lim = fetch.limits(max_mb=1, max_s=8)
        start = self.clock.t
        with mock.patch.object(fetch, "open_tls", self.fake_open), mock.patch.object(fetch, "resolve", slow_resolve):
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.safe_get("https://a.example.com/", fetch.Via("awg", mark=257), lim, fetch.Guard())
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLessEqual(self.clock.t - start, fetch.total_s(lim) + 1e-6)
        self.assertEqual(fetch.total_s(lim), 5 + 10 + 8)

    def test_the_callers_deadline(self):
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        self.answers = [[5.0, resp(204)]]
        start = self.clock.t
        with mock.patch.object(fetch, "open_tls", self.fake_open), \
                mock.patch.object(fetch, "resolve", self.fake_resolve):
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.safe_get("https://a.example.com/", fetch.Via("awg", mark=257), fetch.limits(), fetch.Guard(),
                               deadline=start + 3.0)
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLessEqual(self.clock.t - start, 3.0 + 1e-6)

    def test_the_body_is_cut_by_the_deadline_of_the_request(self):
        # REV-s1-t6-2 П-a 1: the headers at once, the body a byte a second — max_s (8 s) is longer than what is left
        # of the caller's deadline (3 s): the body ends by the deadline, not by max_s
        self.dns = {"a.example.com": [["8.8.8.8"]]}
        head = b"HTTP/1.1 200 X\r\nContent-Length: 100\r\n\r\n"
        self.answers = [[0.1, head] + [x for _ in range(20) for x in (1.0, b"x")]]
        start = self.clock.t
        with mock.patch.object(fetch, "open_tls", self.fake_open), \
                mock.patch.object(fetch, "resolve", self.fake_resolve):
            r = fetch.safe_get("https://a.example.com/", fetch.Via("awg", mark=257),
                               fetch.limits(max_mb=1, max_s=8), fetch.Guard(), deadline=start + 3.0)
        self.assertEqual(r["stopped"], "max_s")
        self.assertLessEqual(self.clock.t - start, 3.0 + 1e-6)
        self.assertLess(r["bytes"], 4)


class HandshakeDeadlineTest(RootCase):
    def test_tls_handshake_trickled_is_cut_by_the_deadline(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        stop = threading.Event()

        def serve():
            c, _ = srv.accept()
            try:
                c.sendall(b"\x16\x03\x03\x00\xc8")      # a handshake record of 200 bytes…
                for _ in range(200):                    # …a byte every 0.1 s: 20 s in all
                    if stop.wait(0.1):
                        break
                    c.sendall(b"\x00")
            except OSError:
                pass
            finally:
                c.close()
        t = threading.Thread(target=serve)
        t.start()
        lim = dict(fetch.limits(ttfb_s=5), deadline=fetch._mono() + 1.0)
        t0 = time.monotonic()
        try:
            with self.assertRaises(fetch.FetchError) as cm:
                fetch.open_tls(fetch.Via("direct"), "127.0.0.1", port, "a.example.com", lim)
        finally:
            stop.set()
            t.join(5)
            srv.close()
        self.assertEqual(cm.exception.code, "timeout")
        self.assertLess(time.monotonic() - t0, 3.0)


class BytesAndTimeTest(Base):
    """REV-s1-t6 З1, З2: the time of a service without the resolver; every byte through the exit (DoH too)."""

    def test_rx_counts_doh_and_ms_is_without_it(self):
        doc = json.dumps({"Status": 0, "Answer": [{"type": 1, "data": "8.8.8.8"}]}).encode()
        doh = resp(200, doc)
        target = resp(204)
        self.answers = [[2.0, doh], [0.1, target]]          # DoH answers in 2 s, the service in 0.1 s
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            r = fetch.safe_get("https://a.example.com/", fetch.Via("awg", mark=257), fetch.limits(), fetch.Guard())
        self.assertEqual(r["rx"], len(doh) + len(target))
        self.assertAlmostEqual(r["ms"], 100.0, delta=1)
        self.assertAlmostEqual(r["ms_all"], 2100.0, delta=1)
        self.answers = [[2.0, doh], [0.1, target]]
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            s = services.check(fetch.Via("awg", mark=257),
                               {"id": "x", "url": "https://a.example.com/", "expect": [204], "max_ms": 1000})
        self.assertEqual((s["ok"], s["bytes"]), (True, len(doh) + len(target)))
        self.answers = [[doh], [resp(200, b"ip=1.2.3.4\nloc=DE\n")]]
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            g = geo.trace(fetch.Via("awg", mark=257))
        self.assertEqual((g["loc"], g["bytes"]), ("DE", len(doh) + len(resp(200, b"ip=1.2.3.4\nloc=DE\n"))))
        # a failure after the resolver: what came through the exit is counted too
        self.answers = [[doh], [11.0, resp(204)]]
        with mock.patch.object(fetch, "open_tls", self.fake_open):
            s = services.check(fetch.Via("awg", mark=257),
                               {"id": "x", "url": "https://a.example.com/", "expect": [204], "max_ms": 1000})
        self.assertEqual((s["ok"], s["why"], s["bytes"]), (False, "ttfb", len(doh)))

    def test_socks_reply_in_words(self):
        e = fetch.FetchError("socks", rep="no_inbound")
        self.assertNotIn("no_inbound", e.text("ru"))
        self.assertNotIn("no_inbound", e.text("en"))
        self.assertIn("5", fetch.FetchError("socks", rep=5).text("ru"))
