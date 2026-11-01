"""The channel of the server check — the portable block ``bench.channel`` (B3: tests 8, 8b, 8c; NS-S5, NS-S16,
NS-D27; A.1 channel_confirm). Offline and without the rest of Kaskad (k3/handoff/process/BLOCKS.md п. 6): a fake clock
or a short one, a local HTTP server on 127.0.0.1, a fake iperf3, the samples in tests/fixtures/bench/channel/ (the
iperf3 texts are made by the format of iperf3 3.x, NOT taken from a node: to the stand)."""

import http.client
import http.server
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from kaskad import score
from kaskad.bench import channel as C

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "bench", "channel")
FAST = C.Timing(run_s=1.0, warm_s=0.2, min_s=0.5, tick_s=0.05, io_s=2.0, grace_s=1.0, bg_s=0.01)


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def snap(sent, retr=0, dsack=0, mss=1448, rtt=30000, min_rtt=20000):
    return {"snd_mss": mss, "rtt": rtt, "min_rtt": min_rtt, "bytes_sent": sent, "bytes_retrans": retr,
            "dsack_dups": dsack, "bytes_acked": sent}


# ── TCP_INFO ────────────────────────────────────────────────────────────────

class TcpInfoTest(unittest.TestCase):
    def offsets(self):
        size = {"__u8": 1, "__u16": 2, "__u32": 4, "__u64": 8}
        off, at = 0, {}
        for line in fx("tcp_info_members.txt").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            t, name = line.split()
            n = size[t]
            off = (off + n - 1) // n * n
            at[name] = off
            off += n
        return at

    def test_offsets_are_those_of_the_header(self):
        at = self.offsets()
        for k, (off, fmt) in C.TI.items():
            self.assertEqual(at["tcpi_" + k], off, k)
        # the same table as the one of «Связь с людьми» (gw/hostpeople.py, NS-D26)
        from kaskad.gw import hostpeople as H
        self.assertEqual((C.TI["snd_mss"][0], C.TI["rtt"][0], C.TI["min_rtt"][0], C.TI["bytes_sent"][0],
                          C.TI["bytes_retrans"][0], C.TI["dsack_dups"][0]),
                         (H.TI_SND_MSS, H.TI_RTT, H.TI_MIN_RTT, H.TI_BYTES_SENT, H.TI_BYTES_RETRANS,
                          H.TI_DSACK_DUPS))

    def pack(self, length, **vals):
        buf = bytearray(max(length, C.TI_LEN))
        for k, v in vals.items():
            off, fmt = C.TI[k]
            struct.pack_into("=" + fmt, buf, off, v)
        return bytes(buf[:length])

    def test_fields_and_short_answers(self):
        full = self.pack(232, snd_mss=1448, rtt=31000, bytes_acked=5, min_rtt=20000, bytes_sent=10 ** 7,
                         bytes_retrans=6000, dsack_dups=2)
        self.assertEqual(C.tcp_fields(full), {"snd_mss": 1448, "rtt": 31000, "bytes_acked": 5, "min_rtt": 20000,
                                              "bytes_sent": 10 ** 7, "bytes_retrans": 6000, "dsack_dups": 2})
        # a kernel older than 4.19 (the answer ends before bytes_sent): no counters of 8c
        old = C.tcp_fields(full[:192])
        self.assertNotIn("bytes_sent", old)
        self.assertIn("min_rtt", old)
        self.assertEqual(C.load_calc({(0, 1): old}, {(0, 1): old})["why"], "no_tcp_info")
        # a broken answer: u64 above 2^63-1, u32 at its maximum
        bad = self.pack(232, bytes_sent=2 ** 64 - 1, dsack_dups=0xFFFFFFFF, snd_mss=1448)
        got = C.tcp_fields(bad)
        self.assertNotIn("bytes_sent", got)
        self.assertNotIn("dsack_dups", got)

    def test_load_line_by_the_difference_after_warm_up(self):
        a = {(0, 1): snap(10 ** 6, retr=500000), (1, 2): snap(2 * 10 ** 6, retr=0)}
        b = {(0, 1): snap(6 * 10 ** 6, retr=530000, dsack=10, rtt=40000, min_rtt=21000),
             (1, 2): snap(7 * 10 ** 6, retr=40000, rtt=36000, min_rtt=19000)}
        got = C.load_calc(a, b)
        # the warm-up's 500 000 never count; Δ retrans 30 000 + 40 000 − 10 × 1448 dups; Δ sent 10^7
        self.assertAlmostEqual(got["retr"], 100.0 * (70000 - 14480) / 10 ** 7, places=4)
        self.assertAlmostEqual(got["rtt"], (38000 - 19000) / 1000.0)      # median at the end − the least min_rtt
        self.assertIsNone(got["why"])
        # a stream of the end not in the start (a new socket) never counts; more dups than repeats — never below 0
        b2 = dict(b)
        b2[(2, 3)] = snap(10 ** 9, retr=10 ** 8)
        self.assertEqual(C.load_calc(a, b2), got)
        self.assertEqual(C.load_calc({(0, 1): snap(0)}, {(0, 1): snap(10 ** 6, retr=0, dsack=50)})["retr"], 0.0)
        self.assertEqual(C.load_calc({}, {})["why"], "no_tcp_info")

    def test_the_true_reason_of_not_measured(self):
        """REV З-2: «the kernel is older than 4.19» only when its answer is short."""
        short = dict((k, v) for k, v in snap(10 ** 6).items() if k != "dsack_dups")     # an old kernel
        self.assertEqual(C.load_calc({(0, 1): short}, {(0, 1): short})["why"], "no_tcp_info")
        # every stream opened a new connection between the samples: the fields are there — reconnect
        got = C.load_calc({(0, 111): snap(10 ** 6)}, {(0, 222): snap(2 * 10 ** 6)})
        self.assertEqual((got["retr"], got["why"]), (None, "reconnect"))

    def test_not_linux_reads_nothing(self):
        with mock.patch.object(C.sys, "platform", "darwin"), socket.socket() as s:
            self.assertIsNone(C.read_tcp_info(s))
            self.assertIsNone(C.read_ca(s))

    def test_ca_is_read_never_set(self):
        class Sock(object):
            def __init__(self):
                self.calls = []

            def getsockopt(self, *a):
                self.calls.append(a)
                return b"cubic\0\0\0\0\0\0\0\0\0\0\0"

            def setsockopt(self, *a):
                raise AssertionError("the test never sets the congestion control")
        s = Sock()
        with mock.patch.object(C.sys, "platform", "linux"):
            self.assertEqual(C.read_ca(s), "cubic")
        self.assertEqual(s.calls, [(socket.IPPROTO_TCP, C.TCP_CONGESTION, 16)])


# ── the window ──────────────────────────────────────────────────────────────

class WindowTest(unittest.TestCase):
    def test_by_the_clock(self):
        s = [(t / 4.0, int(t / 4.0 * 12.5e6)) for t in range(1, 41)]      # 100 Mbit/s for 10 s
        v, lower = C.window(s, 2.0, 5.0, 10 ** 9, s[-1][1], False)
        self.assertAlmostEqual(v, 100.0, places=3)
        self.assertFalse(lower)

    def test_warm_up_is_dropped(self):
        s = [(0.25 * i, int(1e6 * i)) for i in range(1, 9)] + [(2.0 + 0.25 * i, 8e6 + 12.5e6 * 0.25 * i)
                                                             for i in range(1, 33)]
        v, _ = C.window(s, 2.0, 5.0, 10 ** 9, s[-1][1], False)
        self.assertAlmostEqual(v, 100.0, places=3)

    def test_cap_before_5_s_is_a_lower_bound(self):
        s = [(0.25 * i, int(250e6 * i / 12)) for i in range(1, 13)]      # 250 MB in 3 s
        v, lower = C.window(s, 2.0, 5.0, 250 * 10 ** 6, 250 * 10 ** 6, True)
        self.assertTrue(lower)
        self.assertAlmostEqual(v, 250e6 * 8 / 3.0 / 1e6, places=3)        # all the bytes ÷ all the time
        # the cap after 5 s: the window 2 s … the end, not a lower bound
        s = [(0.25 * i, int(10e6 * 0.25 * i)) for i in range(1, 29)]
        v, lower = C.window(s, 2.0, 5.0, 70 * 10 ** 6, 70 * 10 ** 6, True)
        self.assertFalse(lower)
        self.assertAlmostEqual(v, 80.0, places=3)

    def test_nothing_after_warm_up(self):
        self.assertEqual(C.window([(0.25, 100), (0.5, 200)], 2.0, 5.0, 10 ** 9, 200, False), (None, False))
        self.assertEqual(C.window([], 2.0, 5.0, 10 ** 9, 0, False), (None, False))


# ── targets and addresses ───────────────────────────────────────────────────

class TargetsTest(unittest.TestCase):
    def test_the_list_of_the_block(self):
        t = C.load_targets()
        self.assertTrue(t["version"])
        self.assertEqual([x["id"] for x in t["http"]["down"]["ru"]], ["selectel", "yandex"])
        self.assertEqual([x["id"] for x in t["http"]["down"]["abroad"]], ["cloudflare", "ovh"])
        self.assertEqual(t["http"]["up"]["ru"], [])                   # a node in Russia: only the allowed point
        self.assertEqual(len(t["iperf3"]["ru"]), 14)
        cf = t["http"]["down"]["abroad"][0]
        self.assertLess(cf["max_req"], 10 ** 8)                      # Cloudflare: 403 from 10^8 bytes
        for side in ("down", "up"):
            for region in ("ru", "abroad"):
                for x in t["http"][side][region]:
                    self.assertTrue(x["url"].startswith("https://"))
                    self.assertNotIn("cloudflare", x["url"] if region == "ru" else "")   # RS §2: not a target in RU

    def test_caps_are_those_of_the_price(self):
        from kaskad.bench import cost
        self.assertEqual(dict((m, v // 10 ** 6) for m, v in C.SIDE_CAP.items()), cost.SIDE_CAP_MB)

    def test_bad_entries_are_dropped(self):
        for url in ("http://example.com/x", "https://user@example.com/x", "https://example.com:0/x",
                    "https://exa mple.com/", "https://../x", "https://example.com/x\n", "ftp://example.com/"):
            self.assertIsNone(C.http_target({"id": "a", "url": url}), url)
        self.assertIsNone(C.http_target({"id": "Bad Id", "url": "https://example.com/"}))
        self.assertEqual(C.http_target("https://example.com:8443/up")["port"], 8443)
        self.assertIsNone(C.iperf_point({"id": "a", "host": "-x; rm", "port": 5201}))
        self.assertIsNone(C.iperf_point({"id": "a", "host": "example.com", "port": "5201"}))
        self.assertIsNone(C.iperf_point({"id": "a", "host": "example.com", "port": 70000}))

    def test_no_address_literal_in_the_list(self):
        """SEC З-3: the resolver takes these as addresses — a list of NAMES only."""
        for host in ("127.0.0.1", "10.8.0.2", "0", "169.254.169.254", "127.1", "0x7f.1", "2130706433", "localhost",
                     "1.2.3.4.", "example.0x1", "localhost.", "foo.localhost", "foo.localhost.", "localhost.example.com"):
            self.assertIsNone(C.iperf_point({"id": "a", "host": host, "port": 5201}), host)
            self.assertIsNone(C.http_target({"id": "a", "url": "https://%s/x" % host}), host)
        self.assertEqual(C.iperf_point({"id": "a", "host": "st.spb.example.com", "port": 5203})["host"],
                         "st.spb.example.com")

    def test_a_broken_file_is_no_targets(self):
        """SEC П-4: [], null, a number — «no targets», never an exception."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for text in ("[]", "null", "5", '"x"', '{"http": [], "iperf3": 7}', '{"http": {"down": {"ru": 3}}}', "{"):
            p = os.path.join(d, "t.json")
            with open(p, "w") as f:
                f.write(text)
            t = C.load_targets(p)
            self.assertEqual(t["http"]["down"]["ru"], [], text)
            self.assertEqual(t["iperf3"]["ru"], [], text)

    def test_the_abroad_points_of_8b_are_gone(self):
        self.assertEqual(C.load_targets()["iperf3"]["abroad"], [])    # REV П-2: abroad the upload is HTTP

    def test_connect_addr_checks_the_peer(self):
        """REV-ns-b3-2 З-2 (M19): the address of the connection itself is checked — a name that leads to a local
        address gives no point (the second check in ``iperf_try`` is not the only one)."""
        class Sock(object):
            def __init__(self, peer):
                self.peer, self.closed = peer, False

            def getpeername(self):
                return (self.peer, 5201)

            def close(self):
                self.closed = True
        for peer, want in (("127.0.0.1", None), ("10.0.0.5", None), ("::1", None), ("77.88.8.8", "77.88.8.8")):
            sock = Sock(peer)
            with mock.patch.object(C.socket, "create_connection", lambda *a, **k: sock):
                got = C.connect_addr("spd.example.com", 5201)
            self.assertEqual(got[0], want, peer)
            self.assertEqual(got[1] is None, want is None, peer)
            self.assertTrue(sock.closed, peer)

    def test_only_global_addresses(self):
        for a in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.1.1", "::1", "fe80::1", "fc00::1",
                  "::ffff:127.0.0.1", "0.0.0.0", "100.64.0.1", "bad", None,
                  # SEC П-2: multicast; an IPv4 of such a kind inside an IPv6 address
                  "224.0.0.1", "239.1.2.3", "ff02::1", "ff0e::1", "::7f00:1", "::808:808", "64:ff9b::7f00:1",
                  "64:ff9b::a08:2", "2002:7f00:1::1", "2002:a00:1::1"):
            self.assertFalse(C.is_global(a), a)
        for a in ("1.1.1.1", "2606:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808", "2002:808:808::1"):
            self.assertTrue(C.is_global(a), a)

    def test_https_connect_sends_nothing_to_a_local_address(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        got = []

        def accept():
            c, _ = srv.accept()
            c.settimeout(2)
            try:
                got.append(c.recv(100))
            except OSError:
                got.append(b"?")
            c.close()
        th = threading.Thread(target=accept, daemon=True)
        th.start()
        with self.assertRaises(ConnectionError):
            C.https_connect("127.0.0.1", srv.getsockname()[1], 2)
        th.join(3)
        srv.close()
        self.assertEqual(got, [b""])                                   # no TLS hello (no name) went out

    def test_user_agent_is_plain(self):
        self.assertRegex(C.UA, r"^Python-urllib/3\.[0-9]+$")
        self.assertNotIn("kaskad", C.UA.lower())

    def test_ru_by_rtt(self):
        self.assertTrue(C.ru_by_rtt(3.0, 40.0))
        self.assertFalse(C.ru_by_rtt(40.0, 3.0))
        self.assertIsNone(C.ru_by_rtt(None, 3.0))
        # SEC З-5: «nearer» only with a margin — twice and by 10 ms; else unknown
        self.assertIsNone(C.ru_by_rtt(1.3, 1.2))
        self.assertIsNone(C.ru_by_rtt(1.0, 1.0))
        self.assertIsNone(C.ru_by_rtt(4.0, 12.0))                     # 3 times, but by 8 ms
        self.assertIsNone(C.ru_by_rtt(30.0, 50.0))                    # by 20 ms, but not twice
        self.assertFalse(C.ru_by_rtt(25.0, 12.0))                     # twice and by 13 ms


# ── one try over HTTP: a real reader against a local server ─────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []
    file_size = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).seen.append(("GET", self.path, dict(self.headers)))
        n = type(self).file_size
        if n is None:
            n = int(self.path.split("bytes=", 1)[1]) if "bytes=" in self.path else 10 ** 6
        self.send_response(200)
        self.send_header("Content-Length", str(n))
        self.end_headers()
        chunk = b"x" * 65536
        left = n
        try:
            while left > 0:
                k = min(left, len(chunk))
                self.wfile.write(chunk[:k])
                left -= k
        except OSError:
            pass

    answer = 0                          # bytes of the body of the answer to a POST
    slow = 0                            # s after every 64 KB of a POST read: an upload of a known rate (Н-1)

    def do_POST(self):
        type(self).seen.append(("POST", self.path, dict(self.headers), self.client_address[1]))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            while n > 0:
                b = self.rfile.read(min(n, 65536))
                if not b:
                    return
                n -= len(b)
                if type(self).slow:
                    time.sleep(type(self).slow)
        except OSError:
            return
        self.send_response(200)
        self.send_header("Content-Length", str(type(self).answer))
        self.end_headers()
        try:
            left = type(self).answer
            while left > 0:
                self.wfile.write(b"y" * min(left, 65536))
                left -= 65536
        except OSError:
            pass


class LocalHttpTest(unittest.TestCase):
    def setUp(self):
        _Handler.seen = []
        _Handler.file_size = None
        _Handler.answer = 0
        _Handler.slow = 0
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def connect(self, host, port, timeout):
        c = http.client.HTTPConnection(host, port, timeout=timeout)
        c.connect()
        c.auto_open = 0
        return c

    def target(self, path):
        t = C.http_target({"id": "local", "url": "https://local.test%s" % path})
        return dict(t, host="127.0.0.1", port=self.port)

    def test_download_by_the_clock_and_the_headers(self):
        cap = 400 * 10 ** 6
        r = C.http_try("down", self.target("/__down?bytes={bytes}"), cap, FAST, connect=self.connect)
        self.assertIsNone(r["why"])
        self.assertGreater(r["value"], 0)
        self.assertLessEqual(r["bytes"], cap)
        self.assertEqual(r["family"], 4)
        gets = [s for s in _Handler.seen if s[0] == "GET"]
        self.assertTrue(gets)
        for _, path, h in gets:
            self.assertEqual(h.get("User-Agent"), C.UA)
            self.assertFalse(any("kaskad" in (k + v).lower() for k, v in h.items()))
            self.assertLessEqual(int(path.split("bytes=")[1]), C.DOWN_REQ)
            self.assertLessEqual(int(path.split("bytes=")[1]), cap // C.STREAMS)

    def test_a_small_cap_is_a_lower_bound(self):
        cap = 4 * 10 ** 6
        r = C.http_try("down", self.target("/__down?bytes={bytes}"), cap, FAST, connect=self.connect)
        self.assertTrue(r["budget"])
        self.assertTrue(r["lower"])
        self.assertEqual(r["bytes"], cap)                              # never a byte above the cap
        self.assertIsNone(r["load"])                                   # a lower bound gets no correction

    def test_a_fixed_file_is_asked_again(self):
        _Handler.file_size = 300000                                    # ends long before the cap: asked again
        r = C.http_try("down", self.target("/100MB"), 40 * 10 ** 6, FAST, connect=self.connect)
        self.assertGreater(len([s for s in _Handler.seen if s[0] == "GET"]), C.STREAMS)
        self.assertLessEqual(len([s for s in _Handler.seen if s[0] == "GET"]), C.STREAMS * C.REQ_MAX)
        self.assertGreater(r["bytes"], 300000 * C.STREAMS)

    def test_upload_and_the_samples_of_the_load(self):
        probes = []
        t0 = C._mono()
        r = C.http_try("up", self.target("/__up"), 40 * 10 ** 9, FAST, connect=self.connect,
                       probe=lambda: probes.append(C._mono() - t0) or len(probes))
        self.assertIsNone(r["why"])
        self.assertGreater(r["value"], 0)
        self.assertEqual(r["load"], (1, 2))                            # at the 2nd s and at the end of the window
        # REV З-5 (M12): the first sample is taken after the warm-up, never at the first tick
        self.assertGreaterEqual(probes[0], FAST.warm_s)
        self.assertGreaterEqual(probes[1], FAST.run_s)
        self.assertNotIn("win", r)                                     # no cap: the window of 8c as it was (Н-1)
        posts = [s for s in _Handler.seen if s[0] == "POST"]
        self.assertTrue(posts)
        for _, _, h, _ in posts:
            self.assertEqual(h.get("Content-Type"), "application/octet-stream")
            self.assertLessEqual(int(h["Content-Length"]), C.UP_REQ)
            self.assertEqual(h.get("User-Agent"), C.UA)

    def test_upload_to_the_cap_before_min_s_has_8c_over_its_window(self):
        """Н-1 over HTTP: the upload reaches its cap before ``min_s`` (here 60 MB at ≈ 60 MB/s, ``min_s`` 2,5 s) — the
        samples of 8c are the first tick at which the cap comes early and the end; ``win`` — their seconds. The
        ``TCP_INFO`` of the sockets is a fake by the clock (1 % sent again, 30 ms against the least 10 ms): a Mac has
        none."""
        _Handler.slow = 0.004
        t0 = time.monotonic()

        def ti(sock):
            if sock is None:
                return None
            sent = int((time.monotonic() - t0) * 10 ** 8)
            return {"snd_mss": 1448, "rtt": 30000, "min_rtt": 10000, "bytes_sent": sent, "bytes_retrans": sent // 100,
                    "dsack_dups": 0, "bytes_acked": 10 ** 15}
        tm = C.Timing(run_s=3.0, warm_s=2.0, min_s=2.5, tick_s=0.05, io_s=2.0, grace_s=1.0, load_min_s=0.2)
        with mock.patch.object(C, "read_tcp_info", ti):
            r = C.http_try("up", self.target("/__up"), 60 * 10 ** 6, tm, connect=self.connect)
        self.assertEqual((r["budget"], r["lower"], r["why"]), (True, True, None))
        self.assertGreaterEqual(r["win"], tm.load_min_s)               # без правки: окна нет
        a, b = r["tcp"]
        self.assertEqual(sorted(k[0] for k in b), [0, 1, 2, 3])
        lc = C.load_calc(a, b)
        self.assertEqual((lc["retr"], lc["rtt"], lc["why"]), (1.0, 20.0, None))
        # the least window above what it had: «не измерено», as before
        with mock.patch.object(C, "read_tcp_info", ti):
            r = C.http_try("up", self.target("/__up"), 60 * 10 ** 6, C.Timing(
                run_s=3.0, warm_s=2.0, min_s=2.5, tick_s=0.05, io_s=2.0, grace_s=1.0, load_min_s=2.4),
                connect=self.connect)
        self.assertTrue(r["lower"])
        self.assertNotIn("win", r)

    def test_a_long_answer_to_a_post_is_never_read(self):
        """SEC П-1: at most RESP_MAX bytes of an answer; a longer one closes the connection (no 100 MB in memory)."""
        _Handler.answer = 4 * C.RESP_MAX
        reads = []
        real = http.client.HTTPResponse.read

        def read(resp, amt=None):
            reads.append(amt)
            return real(resp, amt)
        with mock.patch.object(http.client.HTTPResponse, "read", read):
            r = C.http_try("up", dict(self.target("/__up"), max_req=500000), 8 * 10 ** 6, FAST, connect=self.connect)
        self.assertGreater(r["bytes"], 0)
        self.assertTrue(reads)
        self.assertNotIn(None, reads)                                  # never «read it all»
        self.assertTrue(all(a <= C.RESP_MAX for a in reads))
        posts = [s for s in _Handler.seen if s[0] == "POST"]
        self.assertGreater(len(posts), C.STREAMS)
        self.assertEqual(len(set(s[3] for s in posts)), len(posts))    # each POST — a new connection

    def test_a_stream_closes_as_soon_as_its_share_is_read(self):
        """SEC З-1 (HTTP): the share read — the connection goes at once, the peer stops sending; never at the end of
        the try (the kernel would read its buffer full above the cap while the other streams go on)."""
        closed = []

        def connect(host, port, timeout):
            c = self.connect(host, port, timeout)
            real = c.close

            def close():
                closed.append(1)
                real()
            c.close = close
            return c
        _Handler.file_size = 50 * 10 ** 6                              # a fixed big file: no {bytes} in its URL
        st = C._Stream(0, "down", self.target("/1GB"), 10 ** 6, C._mono() + 30, threading.Event(), connect, FAST)
        st.run()                                                       # the stream alone, the try not over
        self.assertEqual(st.n, 10 ** 6)
        self.assertEqual(closed, [1])
        self.assertIsNone(st.conn)
        r = C.http_try("down", self.target("/1GB"), 2 * 10 ** 6, FAST, connect=connect)
        self.assertEqual(r["bytes"], 2 * 10 ** 6)

    def test_no_answer_is_no_target(self):
        def refused(host, p, timeout):
            raise ConnectionRefusedError()
        r = C.http_try("down", self.target("/x"), 10 ** 6, FAST, connect=refused)
        self.assertEqual((r["value"], r["why"]), (None, "no_target"))

    def test_status_not_200(self):
        class H404(_Handler):
            def do_GET(self):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
        self.srv.RequestHandlerClass = H404
        r = C.http_try("down", self.target("/x"), 10 ** 6, FAST, connect=self.connect)
        self.assertEqual(r["why"], "no_target")


# ── one try over iperf3 ─────────────────────────────────────────────────────

class FakeProc(object):
    def __init__(self, text, argv):
        self.argv = argv
        self.stdout = iter(text.splitlines(True))
        self.killed = self.terminated = False
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


class IperfTest(unittest.TestCase):
    POINT = {"id": "hostkey-msk", "host": "spd-rudp.hostkey.ru", "port": 5201, "addr": "77.88.8.8"}

    def popen(self, text):
        self.procs = []

        def f(argv, **kw):
            self.assertEqual(kw["stdin"], C.subprocess.DEVNULL)
            self.assertEqual(kw["cwd"], "/")
            self.assertEqual(sorted(kw["env"]), ["LANG", "LC_ALL", "PATH"])     # nothing of the worker's environment
            p = FakeProc(text, argv)
            self.procs.append(p)
            return p
        return f

    def run_iperf(self, text, cap=10 ** 9, side="up", point=None, **kw):
        kw.setdefault("drop", [])
        return C.iperf_try(side, point or self.POINT, cap, FAST, popen=self.popen(text), **kw)

    def test_argv_is_fixed(self):
        self.assertEqual(C.iperf_argv(self.POINT, False),
                         ["iperf3", "-c", "77.88.8.8", "-p", "5201", "-P", "4", "-t", "10", "-O", "2", "-i",
                          "0.25", "--forceflush"])
        self.assertEqual(C.iperf_argv(self.POINT, True)[-1], "-R")

    def test_the_checked_address_never_the_name(self):
        """SEC З-3: iperf3 gets the address the connection checked; no address — no run."""
        self.run_iperf(fx("iperf3-up-P4.txt"))
        self.assertIn("77.88.8.8", self.procs[0].argv)
        self.assertNotIn("spd-rudp.hostkey.ru", self.procs[0].argv)
        for addr in (None, "spd-rudp.hostkey.ru", "127.0.0.1", "10.8.0.2", "::ffff:10.0.0.1", "ff02::1"):
            pt = dict(self.POINT, addr=addr)
            r = self.run_iperf(fx("iperf3-up-P4.txt"), point=pt)
            self.assertEqual(r["why"], "tool_failed", addr)
            self.assertEqual(self.procs, [], addr)                     # never started

    def test_nobody_never_root(self):
        """SEC З-6: iperf3 reads the answers of servers of others — never as root."""
        sp = C.drop_argv(which=lambda n: "/usr/bin/setpriv", euid=0)
        self.assertEqual(sp, ["/usr/bin/setpriv", "--reuid=65534", "--regid=65534", "--clear-groups",
                              "--no-new-privs", "--inh-caps=-all"])
        self.assertIsNone(C.drop_argv(which=lambda n: None, euid=0))
        self.assertEqual(C.drop_argv(which=lambda n: "/usr/bin/setpriv", euid=1000), [])
        self.run_iperf(fx("iperf3-up-P4.txt"), drop=sp)
        self.assertEqual(self.procs[0].argv[:7], sp + ["iperf3"])
        with mock.patch.object(C, "drop_argv", return_value=None):
            r = C.iperf_try("up", self.POINT, 10 ** 9, FAST, popen=self.popen(fx("iperf3-up-P4.txt")))
        self.assertEqual((r["why"], self.procs), ("tool_failed", []))  # root without setpriv: no run

    def test_8c_over_8b_from_the_sockets_of_iperf3(self):
        """D-144 (владелец 29.10: «Повторная отправка» и «Рост отклика под нагрузкой» — из тех же потоков): the upload
        of 8b takes two samples of the sockets of iperf3 (``tcp(pid, port)`` of the product) — at its first measured
        line and when 1 s of the window is left; its control connection (under STREAM_MIN sent) is left out; the
        download takes none; a sampler that fails — no 8c, the value stays."""
        calls = []

        def tcp(pid, port):
            calls.append((pid, port))
            k = len(calls) - 1
            out = dict((("s", i), {"snd_mss": 1448, "rtt": 20000 + 10000 * k, "min_rtt": 10000,
                                   "bytes_sent": 10 ** 6 + k * 50 * 10 ** 6, "bytes_retrans": k * 500000,
                                   "dsack_dups": 0}) for i in range(4))
            out["ctl"] = {"snd_mss": 1448, "rtt": 900, "min_rtt": 900, "bytes_sent": 300 + k * 1000,
                          "bytes_retrans": 0, "dsack_dups": 0}
            return out

        def popen(argv, **kw):
            p = FakeProc(fx("iperf3-up-P4.txt"), argv)
            p.pid = 4242
            return p
        r = C.iperf_try("up", self.POINT, 2 * 10 ** 9, C.T, popen=popen, drop=[], tcp=tcp)
        self.assertIsNotNone(r["value"])
        self.assertEqual(calls, [(4242, 5201), (4242, 5201)])           # без правки: ни одного
        a, b = r["tcp"]
        self.assertEqual(sorted(b), [("s", i) for i in range(4)])       # the control connection is not a stream
        lc = C.load_calc(a, b)
        self.assertEqual((lc["retr"], lc["rtt"], lc["why"]), (1.0, 20.0, None))   # 2 MB of 200 MB; 30 − 10 ms
        calls[:] = []
        r = C.iperf_try("down", self.POINT, 2 * 10 ** 9, C.T, popen=popen, drop=[], tcp=tcp)
        self.assertEqual((calls, r["tcp"]), ([], None))
        r = C.iperf_try("up", self.POINT, 2 * 10 ** 9, C.T, popen=popen, drop=[], tcp=lambda pid, port: 1 / 0)
        self.assertEqual((r["tcp"], r["value"] is not None), (None, True))
        self.assertEqual(C.streams_of({"x": {"bytes_sent": 5}}, {"x": {"bytes_sent": 5 + C.STREAM_MIN}}),
                         ({"x": {"bytes_sent": 5}}, {"x": {"bytes_sent": 5 + C.STREAM_MIN}}))
        self.assertEqual(C.streams_of({"x": {"bytes_sent": 5}}, {"y": {"bytes_sent": 10 ** 9}}), ({}, {}))

    def test_lines(self):
        self.assertEqual(C.iperf_line("[SUM]   0.00-0.25   sec  10.0 MBytes   336 Mbits/sec    0             (omitted)"),
                         (0.25, 10 * 1048576, True))
        self.assertEqual(C.iperf_line("[SUM]   1.75-2.00   sec  1.50 GBytes  51.5 Gbits/sec")[1], int(1.5 * 1024 ** 3))
        self.assertIsNone(C.iperf_line("[SUM]   0.00-10.00  sec   400 MBytes   336 Mbits/sec    0             sender"))
        self.assertIsNone(C.iperf_line("[  5]   0.00-0.25   sec  2.50 MBytes  83.9 Mbits/sec    0    512 KBytes"))
        self.assertIsNone(C.iperf_line("garbage"))

    def test_the_omitted_seconds_never_count(self):
        r = self.run_iperf(fx("iperf3-up-P4.txt"), 2 * 10 ** 9)
        self.assertAlmostEqual(r["value"], 10 * 1048576 * 8 / 0.25 / 1e6, places=2)
        self.assertEqual((r["how"], r["lower"], r["budget"]), ("iperf3", False, False))
        self.assertEqual(r["bytes"], 48 * 10 * 1048576)
        self.assertNotIn("-R", self.procs[0].argv)

    def test_the_reader_stops_iperf3_at_the_cap(self):
        r = self.run_iperf(fx("iperf3-up-P4.txt"), 50 * 1048576, side="down")
        self.assertTrue(r["budget"])
        self.assertTrue(self.procs[0].terminated)
        self.assertTrue(r["lower"])                                    # the cap ended in the omitted seconds
        self.assertLessEqual(r["bytes"], 50 * 1048576)
        self.assertIn("-R", self.procs[0].argv)

    def test_busy_and_an_old_iperf3(self):
        r = self.run_iperf(fx("iperf3-busy.txt"))
        self.assertEqual((r["value"], r["why"]), (None, "points_busy"))
        r = self.run_iperf(fx("iperf3-no-forceflush.txt"))
        self.assertEqual(r["why"], "tool_failed")
        r = self.run_iperf("iperf3: error - unable to connect to server: Connection refused\n")
        self.assertEqual(r["why"], "no_target")

    def test_an_output_we_do_not_know_is_not_measured(self):
        """SEC З-2: no line of the sums — «не измерено» (tool_failed), never a point after it with the full cap."""
        text = fx("iperf3-up-P4.txt").replace("MBytes", "MiBytes")
        r = self.run_iperf(text)
        self.assertEqual((r["value"], r["why"], r["budget"]), (None, "tool_failed", False))

    def growing(self, start, step):
        """The counters of a node: ``start`` (a real /proc/net/dev holds gigabytes since the boot) and still before
        iperf3 starts (no people), then + ``step`` a sample."""
        n = [0]

        def count(side):
            if not getattr(self, "procs", None):
                return start
            n[0] += 1
            return start + step * n[0]
        return count

    def test_the_counters_stop_it_whatever_the_text_says(self):
        """SEC З-2: the cap rests on the counters of the interfaces too, from where they stood at the start. The text
        far below them (REV-ns-b3-3 З-1, the coordinator 19.10): the value of the text — no ``budget``, no «не меньше»;
        only no line of the sums is tool_failed (REV-ns-b3-2 С-1)."""
        r = self.run_iperf(fx("iperf3-up-P4.txt"), 200 * 10 ** 6, counter=self.growing(10 ** 12, 10 * 10 ** 6))
        self.assertTrue(self.procs[0].terminated)
        self.assertEqual((r["budget"], r["lower"]), (False, False))
        self.assertIsNone(r["why"])                                    # measured, by the text
        self.assertIsNotNone(r["value"])
        self.assertGreaterEqual(r["bytes"], 200 * 10 ** 6)             # the shared cap of the direction knows it
        self.assertLess(r["bytes"], 260 * 10 ** 6)                     # from the start, never the counter itself (M8)

    def test_net_iperf_has_the_counters(self):
        """REV-ns-b3-2 З-2 (M15, M8): the path of the product (``Net().iperf``) stops iperf3 by the counters of the
        interfaces — a text far below the cap does not hold it — and counts them from the start, not from zero."""
        text = fx("iperf3-up-P4.txt").replace("10.0 MBytes", "0.10 MBytes")   # the sums: 4,8 MB in all

        def fake(side, path="/proc/net/dev", names=None):
            got.append(names)
            return count(side)
        for start in (0, 10 ** 12):
            got = []
            count = self.growing(start, 20 * 10 ** 6)
            with mock.patch.object(C, "net_counters", fake), mock.patch.object(C, "drop_argv", return_value=[]), \
                    mock.patch.object(C, "counted_ifaces", return_value=["eth0"]), \
                    mock.patch.object(C.subprocess, "Popen", self.popen(text)):
                r = C.Net(FAST).iperf("up", self.POINT, 200 * 10 ** 6)
            self.assertTrue(r["budget"], start)                        # the counters stopped it (M15: never)
            self.assertTrue(self.procs[0].terminated, start)           # before the first line of the sums: «not
            self.assertIsNotNone(r["value"], start)                    # less than» by the counters (no text yet)
            self.assertLess(r["bytes"], 300 * 10 ** 6, start)          # M8: 10^12 «passed» at the first look
            self.assertEqual(set(map(tuple, got)), {("eth0",)})        # the interfaces of the try, once

    def test_the_background_of_people(self):
        """REV-ns-b3-2 С-1: two samples ``bg_s`` apart before the start (N13); people of the samples are taken off —
        but never more than the cap: a burst in the samples never lets the counters pass more than twice the cap."""
        slept = []
        with mock.patch.object(C, "_sleep", lambda t: slept.append(t)):
            self.run_iperf(fx("iperf3-up-P4.txt"), counter=lambda side: 10 ** 9)
            self.assertEqual(slept, [FAST.bg_s])
            del slept[:]
            self.run_iperf(fx("iperf3-up-P4.txt"))
            self.assertEqual(slept, [])                                # no counters — no wait
        text = fx("iperf3-up-P4.txt").replace("10.0 MBytes", "0.10 MBytes")   # the text far below the cap
        n, burst = [0], [10 ** 12]

        def count(side):
            if not getattr(self, "procs", None):
                burst[0] += 10 ** 12                                   # a terabyte in the samples: a «huge» rate
                return burst[0]
            n[0] += 1
            return burst[0] + 20 * 10 ** 6 * n[0]
        self.procs = []
        r = self.run_iperf(text, 200 * 10 ** 6, counter=count)
        self.assertTrue(self.procs[0].terminated)
        self.assertGreaterEqual(r["bytes"], 200 * 10 ** 6)
        self.assertLessEqual(20 * 10 ** 6 * n[0], 2 * 200 * 10 ** 6 + 40 * 10 ** 6)

    def test_a_silent_point(self):
        """REV-ns-b3-2 П-3: the point took the connection and is silent — «the points did not answer», no next
        point; lines of the intervals of the streams but no sums — an output we do not know (tool_failed). The lines
        «connected» of the streams (iperf3 prints them before the test runs) are not intervals (REV-ns-b3-3 П-1)."""
        class Silent(FakeProc):
            def __init__(self, text, argv):
                FakeProc.__init__(self, "", argv)
                self.ev = threading.Event()
                self.stdout = self.lines(text)

            def lines(self, text):
                for line in text.splitlines(True):
                    yield line
                self.ev.wait(5)

            def terminate(self):
                FakeProc.terminate(self)
                self.ev.set()
        quick = C.Timing(run_s=1.0, warm_s=0.2, min_s=0.5, tick_s=0.05, io_s=2.0, grace_s=1.0, first_s=0.2,
                         bg_s=0.01)
        lines = fx("iperf3-up-P4.txt").splitlines(True)
        for text, why, end in (("Connecting to host 77.88.8.8, port 5201\n", "no_target", True),
                               ("", "no_target", True),
                               ("".join(lines[:6]), "no_target", True),           # «connected» ×4 and the head
                               (lines[1] * 2, "no_target", True),
                               ("".join(lines[:9]), "tool_failed", None),         # intervals of streams, no sums
                               (lines[6].replace("MBytes", "MiBytes"), "tool_failed", None)):
            procs = []

            def popen(argv, **kw):
                procs.append(Silent(text, argv))
                return procs[-1]
            r = C.iperf_try("up", self.POINT, 10 ** 9, quick, popen=popen, drop=[])
            self.assertEqual((r["value"], r["why"], r.get("end")), (None, why, end), text)
            self.assertTrue(procs[0].terminated)

    def test_a_busy_point_that_falls_silent_is_not_the_end(self):
        """REV-ns-b3-3 П-2 (R5): «the server is busy» and then silence — the next point (``points_busy``), never
        ``end``."""
        class Silent(FakeProc):
            def __init__(self, text, argv):
                FakeProc.__init__(self, "", argv)
                self.ev = threading.Event()
                self.stdout = self.lines(text)

            def lines(self, text):
                for line in text.splitlines(True):
                    yield line
                self.ev.wait(5)

            def terminate(self):
                FakeProc.terminate(self)
                self.ev.set()
        quick = C.Timing(run_s=1.0, warm_s=0.2, min_s=0.5, tick_s=0.05, io_s=2.0, grace_s=1.0, first_s=0.2,
                         bg_s=0.01)
        text = "iperf3: error - the server is busy running a test. try again later\n"
        r = C.iperf_try("up", self.POINT, 10 ** 9, quick, popen=lambda argv, **kw: Silent(text, argv), drop=[])
        self.assertEqual((r["value"], r["why"], r.get("end")), (None, "points_busy", None))

    def test_the_background_is_never_negative(self):
        """REV-ns-b3-3 П-2 (R6): the counters went DOWN between the two samples (an interface came back, a counter
        wrapped) — no background, never a «negative» one that would make the own bytes huge and stop iperf3 at once."""
        base = self.run_iperf(fx("iperf3-up-P4.txt"))
        seen = [10 ** 16, 10 ** 12]

        def count(side):
            return seen.pop(0) if seen else 10 ** 12 + 1
        r = self.run_iperf(fx("iperf3-up-P4.txt"), counter=count)
        self.assertEqual((r["value"], r["budget"], r["lower"]), (base["value"], False, False))

    def test_no_program(self):
        def nope(argv, **kw):
            raise FileNotFoundError()
        self.assertEqual(C.iperf_try("up", self.POINT, 10 ** 9, FAST, popen=nope, drop=[])["why"], "no_iperf3")

    def test_net_counters(self):
        """REV-ns-b3-2 С-1: the interfaces of the default routes as ``quiet`` counts people — never a tunnel (a byte
        of a person through the node is on wg0 AND on eth0), never lo, never an interface without a default route."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p, r4, r6 = os.path.join(d, "dev"), os.path.join(d, "route"), os.path.join(d, "ipv6_route")
        with open(p, "w") as f:
            f.write("Inter-|   Receive                                                |  Transmit\n"
                    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop "
                    "fifo colls carrier compressed\n"
                    "    lo: 9000 1 0 0 0 0 0 0 9000 1 0 0 0 0 0 0\n"
                    "  eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n"
                    "  eth1: 50 1 0 0 0 0 0 0 60 1 0 0 0 0 0 0\n"
                    "   wg0: 300 1 0 0 0 0 0 0 400 1 0 0 0 0 0 0\n"
                    "kawg1: 70 1 0 0 0 0 0 0 80 1 0 0 0 0 0 0\n")
        with open(r4, "w") as f:
            f.write("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                    "eth0\t00000000\t0102000A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
                    "wg0\t00000000\t00000000\t0001\t0\t0\t200\t00000000\t0\t0\t0\n"
                    "eth1\t0000A8C0\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n")
        self.assertEqual(C.counted_ifaces(r4, r6, p), ["eth0"])
        names = C.counted_ifaces(r4, r6, p)
        # REV-ns-b3-3 П-2 (R4): the v6 default through another interface counts too; a v6 default on a tunnel — never
        with open(r6, "w") as f:
            f.write("%s 00 %s 00 fe800000000000000000000000000001 00000400 00000001 00000000 00000003     eth1\n"
                    "%s 00 %s 00 %s 00000500 00000001 00000000 00000001      wg0\n" % (("0" * 32,) * 5))
        self.assertEqual(C.counted_ifaces(r4, r6, p), ["eth0", "eth1"])
        self.assertEqual(C.net_counters("up", p, C.counted_ifaces(r4, r6, p)), 2060)
        os.unlink(r6)
        self.assertEqual(C.net_counters("down", p, names), 1000)
        self.assertEqual(C.net_counters("up", p, names), 2000)
        # no default route read: every interface but lo and the tunnels
        none = os.path.join(d, "none")
        self.assertEqual(C.counted_ifaces(none, none, p), ["eth0", "eth1"])
        self.assertEqual(C.net_counters("up", p, ["eth0", "eth1"]), 2060)
        self.assertIsNone(C.net_counters("up", none))
        self.assertIsNone(C.net_counters("up", p, ["eth9"]))


FAKE = os.path.join(FIX, "fake_iperf3.py")


class IperfCapInRealTimeTest(unittest.TestCase):
    """SEC З-1: a fake iperf3 that runs in real time at 1 and 10 Gbit/s; at the stop it writes how many bytes went
    by. ``counter`` — the counters of the interfaces as the node has them (here: the rate × the time of the fake).
    Before the fix: 1 Gbit/s +28 %, 10 Gbit/s +153 % above the cap."""

    def run_fake(self, gbit, cap, counter=True, unit="MBytes"):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rate = gbit * 1e9 / 8

        def popen(argv, **kw):
            self.assertEqual(argv[:3], ["iperf3", "-c", "77.88.8.8"])
            return subprocess.Popen([sys.executable, "-I", FAKE, str(rate), d, unit], **kw)

        def count(side):
            try:
                with open(os.path.join(d, "start")) as f:
                    t0 = float(f.read())
            except (OSError, ValueError):
                return 0
            return int(rate * (time.time() - t0))
        r = C.iperf_try("up", IperfTest.POINT, cap, C.T, popen=popen, counter=count if counter else None, drop=[])
        with open(os.path.join(d, "passed")) as f:
            return r, float(f.read())

    def test_1_gbit(self):
        cap = C.SIDE_CAP["quick"]
        for counter in (True, False):                                  # the text alone holds it too
            r, passed = self.run_fake(1, cap, counter)
            self.assertTrue(r["budget"], counter)
            self.assertLessEqual(passed, cap * 1.05, (counter, passed / cap))
            self.assertGreater(passed, cap * 0.8, (counter, passed / cap))

    def test_10_gbit(self):
        for mode, most in (("quick", 1.25), ("full", 1.05)):
            cap = C.SIDE_CAP[mode]
            r, passed = self.run_fake(10, cap)
            self.assertTrue(r["budget"], mode)
            self.assertLessEqual(passed, cap * most, (mode, passed / cap))
        # without the counters the first line of 0,25 s (312 MB) is above the quick cap by itself: one line at most
        r, passed = self.run_fake(10, C.SIDE_CAP["quick"], counter=False)
        self.assertLessEqual(passed, 10e9 / 8 * 0.25 * 1.2)

    def test_people_never_stop_it_early(self):
        """REV-ns-b3-2 С-1: a node with people (200 and 250 Mbit/s on the counters, which hold gigabytes since the
        boot): 8b gives its value — never «не измерено» — and still stops at its cap. Before the fix: 250 Mbit/s of
        people — tool_failed after 22 MB of 50."""
        cap = 50 * 10 ** 6
        for people in (200, 250):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d, True)
            rate, prate, t_start = 200e6 / 8, people * 1e6 / 8, time.time()

            def popen(argv, **kw):
                return subprocess.Popen([sys.executable, "-I", FAKE, str(rate), d], **kw)

            def count(side):
                try:
                    with open(os.path.join(d, "start")) as f:
                        own = rate * (time.time() - float(f.read()))
                except (OSError, ValueError):
                    own = 0
                return int(5 * 10 ** 11 + prate * (time.time() - t_start) + own)
            r = C.iperf_try("up", IperfTest.POINT, cap, C.T, popen=popen, counter=count, drop=[])
            with open(os.path.join(d, "passed")) as f:
                passed = float(f.read())
            self.assertIsNone(r["why"], people)
            self.assertGreater(r["value"], 160, (people, r))           # 200 Mbit/s of iperf3, not of the people
            self.assertLessEqual(passed, cap * 1.05, (people, passed / cap))
            self.assertGreater(passed, cap * 0.8, (people, passed / cap))
            # REV-ns-b3-3 П-2 (R3): ``bytes`` (the shared cap of the direction, ``used``) — of iperf3, not of people
            self.assertLessEqual(r["bytes"], cap * 1.3, (people, r["bytes"] / cap))

    def people_run(self, rate, text_rate, prate, cap):
        """A fake iperf3 that moves ``rate`` bytes/s but prints ``text_rate``; people ``prate`` bytes/s all along. ->
        (the result, the bytes of iperf3 by the counters until the stop)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        t_start = time.time()

        def popen(argv, **kw):
            return subprocess.Popen([sys.executable, "-I", FAKE, str(text_rate), d], **kw)

        def count(side):
            try:
                with open(os.path.join(d, "start")) as f:
                    own = rate * (time.time() - float(f.read()))
            except (OSError, ValueError):
                own = 0
            return int(5 * 10 ** 11 + prate * (time.time() - t_start) + own)
        r = C.iperf_try("up", IperfTest.POINT, cap, C.T, popen=popen, counter=count, drop=[])
        with open(os.path.join(d, "passed")) as f:
            moved = float(f.read()) / text_rate * rate
        return r, moved

    def test_people_faster_than_iperf3_give_the_value_of_the_text(self):
        """REV-ns-b3-3 З-1 (the coordinator, 19.10): people (600 Mbit/s) pass more than the cap during the try and
        stop iperf3 (200 Mbit/s) by the counters while its text is far from the cap — the value of the text, no
        ``budget``, no «не меньше»; ``bytes`` is the cap (no more tries of the direction)."""
        cap = 50 * 10 ** 6
        r, moved = self.people_run(200e6 / 8, 200e6 / 8, 600e6 / 8, cap)
        self.assertIsNone(r["why"])
        self.assertEqual((r["budget"], r["lower"]), (False, False), r)
        self.assertGreater(r["value"], 160, r)
        self.assertLess(r["value"], 240, r)
        self.assertLess(moved, cap * 0.9)
        self.assertGreaterEqual(r["bytes"], cap)

    def test_steady_people_and_a_text_below_the_truth_stop_at_one_cap(self):
        """REV-ns-b3-3 П-2 (R8): people steady at a quarter of iperf3, the text 10 times below what moves — the
        counters stop iperf3 at about ONE cap of its own bytes (the background taken off as it is, not ×3: then
        1,6 caps)."""
        cap = 50 * 10 ** 6
        rate = 200e6 / 8
        r, moved = self.people_run(rate, rate / 10, rate / 4, cap)
        self.assertLessEqual(moved, cap * 1.25, moved / cap)
        self.assertGreater(moved, cap * 0.8, moved / cap)
        self.assertEqual((r["budget"], r["lower"]), (False, False), r)  # the text far from the cap: its value

    def burst_run(self, gbit, ovh, pgbit, cap):
        """iperf3 at ``gbit`` with the counters ``ovh`` × its bytes (the headers), people ``pgbit`` from the start of
        iperf3 on (not in the background before it). -> (the result, the bytes of iperf3 until the stop)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rate, prate = gbit * 1e9 / 8, pgbit * 1e9 / 8

        def popen(argv, **kw):
            return subprocess.Popen([sys.executable, "-I", FAKE, str(rate), d], **kw)

        def count(side):
            try:
                with open(os.path.join(d, "start")) as f:
                    t = time.time() - float(f.read())
            except (OSError, ValueError):
                return 0
            return int(ovh * rate * t + prate * t)
        r = C.iperf_try("up", IperfTest.POINT, cap, C.T, popen=popen, counter=count, drop=[])
        with open(os.path.join(d, "passed")) as f:
            return r, float(f.read())

    def test_a_quiet_node_keeps_the_budget_with_the_headers(self):
        """REV-ns-join С-1: no people, the counters 3–5 % above the text (the headers of L2/IP/TCP), 0,8–1 Гбит/с —
        the counters reach the quick cap a quarter of a second before the text. 8b stopped by its own cap: ``budget``
        and «не меньше» stay (before the fix: ``budget: False, lower: False`` — «people did it»)."""
        cap = C.SIDE_CAP["quick"]
        for gbit, ovh in ((0.8, 1.03), (0.9, 1.03), (1.0, 1.03), (1.0, 1.05)):
            r, passed = self.burst_run(gbit, ovh, 0, cap)
            self.assertIsNone(r["why"], (gbit, ovh, r))
            self.assertEqual((r["budget"], r["lower"]), (True, True), (gbit, ovh, r))
            self.assertLessEqual(passed, cap * 1.05, (gbit, ovh, passed / cap))

    def test_people_above_the_share_of_the_cap_give_the_value_of_the_text(self):
        """REV-ns-join С-1, the other side of the border: iperf3 at 0,8 Гбит/с, the headers +3 %, people (0,55 Гбит/с)
        come with it and pass about 0,4 of the cap until the counters stop it — about 0,28 of the cap beyond the text
        with its margins, above ``PEOPLE_SHARE``: the value of the text, no ``budget``, no «не меньше»."""
        cap = C.SIDE_CAP["quick"]
        r, passed = self.burst_run(0.8, 1.03, 0.55, cap)
        self.assertIsNone(r["why"], r)
        self.assertEqual((r["budget"], r["lower"]), (False, False), r)
        self.assertGreater(r["value"], 640, r)
        self.assertLess(r["value"], 960, r)
        self.assertLess(passed, cap * 0.8, passed / cap)
        self.assertGreaterEqual(r["bytes"], cap)

    def test_an_unknown_output_stops_in_first_s(self):
        r, passed = self.run_fake(1, C.SIDE_CAP["full"], counter=False, unit="MiBytes")
        self.assertEqual((r["value"], r["why"]), (None, "tool_failed"))
        self.assertLessEqual(passed, 1e9 / 8 * (C.T.first_s + 0.5))   # 1,5 s, not 12 s (SEC: 1539 MB)


class ShortWindowOf8cTest(unittest.TestCase):
    """Н-1 стенда 3.3.7.13 (решение координатора 29.10): узел отдаёт ≈ 9,4 Гбит/с, полная проверка упирается в предел
    2000 МБ за ≈ 1,7 с — 8c «под нагрузкой» считается по тому окну, которое было: от среза после первого интервала
    (≥ 0,25 с) до упора в предел; короче ``load_min_s`` — «не измерено», ``cap_early``; при упоре после 5 с и без упора —
    как было, побайтно. Поддельный iperf3 идёт в реальном времени (fake_iperf3.py); срезы сокетов — по его часам."""

    TT = C.Timing(bg_s=0.01)            # the clock of a node, the background of the counters short

    def fake(self, gbit, then=None, after=0):
        """(popen, counter, tcp, calls) of a fake iperf3 at ``gbit`` (``then`` Gbit/s after ``after`` lines — a link
        that speeds up): the sampler gives 4 streams of the bytes by the clock of the fake — 0,2 % of them sent again
        plus 5 MB of the start that never count (they are before the window), the delay 3 ms growing to 8 ms by its
        1st second, the least 2 ms."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rate = gbit * 1e9 / 8
        rate2 = rate if then is None else then * 1e9 / 8
        calls = []

        def popen(argv, **kw):
            return subprocess.Popen([sys.executable, "-I", FAKE, str(rate), d, "MBytes", str(rate2), str(after)], **kw)

        def moved(e):
            return rate * min(e, after * 0.25) + rate2 * max(0.0, e - after * 0.25)

        def since():
            try:
                with open(os.path.join(d, "start")) as f:
                    return time.time() - float(f.read())
            except (OSError, ValueError):
                return None

        def count(side):
            e = since()
            return 0 if e is None else int(moved(e))

        def tcp(pid, port):
            e = since()
            calls.append(e)
            if e is None:
                return {}
            sent = int(moved(e) / 4)
            return dict((("s", i), {"snd_mss": 1448, "rtt": int(3000 + 5000 * min(1.0, e)), "min_rtt": 2000,
                                    "bytes_sent": sent, "bytes_retrans": 5 * 10 ** 6 + sent // 500, "dsack_dups": 0})
                        for i in range(4))
        return popen, count, tcp, calls

    def net(self, gbit, **kw):
        test = self

        class N(object):
            tcp = None

            def __init__(self):
                self.popen, self.count, self.tcp_of, self.calls = test.fake(gbit, **kw)
                self.tries = []

            def http(self, side, t, cap, probe=None):
                return {"value": 900.0, "lower": False, "budget": False, "bytes": 10 ** 6, "family": 4, "how": "http",
                        "tcp": None, "load": None, "ca": None, "why": None, "target": t["id"]}

            def iperf(self, side, t, cap):
                r = C.iperf_try(side, t, cap, test.TT, popen=self.popen, counter=self.count, drop=[], tcp=self.tcp_of)
                self.tries.append(r)
                return r

            def reach(self, point):
                return dict(point, addr="77.88.8.8")
        return N()

    def env(self):
        return C.Env(ru=True, iperf=True, have_iperf=True, manual=True)

    def test_9_4_gbit_measures_8c_over_the_window_it_had(self):
        net = self.net(9.4)
        rows = by(C.measure("full", self.env(), net))
        r = net.tries[-1]
        self.assertEqual((r["lower"], r["budget"]), (True, True))      # the cap of the full check in ≈ 1,7 s
        self.assertGreaterEqual(r["win"], C.LOAD_MIN_S)                # без правки: нет окна, 8c — cap_early
        self.assertLessEqual(r["win"], 1.75)                           # from ≈ 0,25 s to the cap, never the 2nd second
        self.assertEqual(len(net.calls), 2)                            # the start of the window and the stop
        self.assertLess(net.calls[0], 0.75)
        self.assertEqual(sorted(r["tcp"][1]), [("s", i) for i in range(4)])
        retr, rtt = rows["load_retr"], rows["load_rtt"]
        self.assertAlmostEqual(retr["value"], 0.2, places=2)           # the 5 MB of the start never count
        self.assertAlmostEqual(rtt["value"], 6.0, places=3)            # 8 ms at the end − 2 ms the least
        self.assertEqual((retr["why"], rtt["why"]), (None, None))
        self.assertEqual(retr["extra"], {"win": r["win"]})
        self.assertEqual(rtt["extra"], {"win": r["win"]})
        # 8b is never cut: the value of the upload is the one of a run without the samples
        bare = C.iperf_try("up", dict(IperfTest.POINT), C.SIDE_CAP["full"], self.TT, popen=net.popen,
                           counter=net.count, drop=[])
        self.assertTrue(bare["lower"])
        self.assertEqual(rows["net_up"]["value"], bare["value"])
        self.assertEqual(rows["net_up"]["extra"]["budget"], True)

    def test_a_window_under_the_least_is_not_measured(self):
        """Канал разгоняется: первая четверть секунды — 4 Гбит/с (до предела по этой скорости больше 1 с — срез начала
        окна взят), потом 30 Гбит/с — предел через ≈ 0,75 с, окно ≈ 0,5 с короче 1 с. 20 Гбит/с ровно: 2000 МБ за
        ≈ 0,8 с — до предела меньше 1 с уже на первой строке, среза нет вовсе; быстрая проверка на 9,4 Гбит/с (250 МБ)
        упирается раньше первой строки. Все — «не измерено» с причиной ``cap_early``, как было."""
        net = self.net(4, then=30, after=1)
        rows = by(C.measure("full", self.env(), net))
        r = net.tries[-1]
        self.assertTrue(r["lower"])
        self.assertEqual(len(net.calls), 2)                            # без правки: ни одного среза — окно не смотрели
        self.assertLess(net.calls[1] - net.calls[0], C.LOAD_MIN_S)
        self.assertNotIn("win", r)
        self.assertEqual((rows["load_retr"]["value"], rows["load_retr"]["why"]), (None, "cap_early"))
        self.assertEqual((rows["load_rtt"]["value"], rows["load_rtt"]["why"]), (None, "cap_early"))
        self.assertNotIn("extra", rows["load_retr"])
        net = self.net(20)
        rows = by(C.measure("full", self.env(), net))
        self.assertEqual((net.tries[-1]["lower"], net.calls), (True, []))   # the cap within 1 s: no sample of sock_diag
        self.assertEqual(rows["load_rtt"]["why"], "cap_early")
        popen, count, tcp, calls = self.fake(9.4)
        r = C.iperf_try("up", dict(IperfTest.POINT, addr="77.88.8.8"), C.SIDE_CAP["quick"], self.TT, popen=popen,
                        counter=count, drop=[], tcp=tcp)
        self.assertEqual((r["lower"], calls), (True, []))
        self.assertNotIn("win", r)

    def test_the_window_and_its_least(self):
        a, b = {"x": 1}, {"x": 2}
        self.assertEqual(C.short_window([(5.0, a)], b, 6.0, 1.0), (a, 1.0))
        self.assertIsNone(C.short_window([(5.0, a)], b, 5.99, 1.0))
        self.assertEqual(C.short_window([(5.4, {"y": 1}), (5.0, a)], b, 6.5, 1.0), (a, 1.5))   # the earlier start
        self.assertEqual(C.short_window([(None, a), (5.0, {})], b, 9.0, 1.0), None)              # no sample
        self.assertIsNone(C.short_window([(5.0, a)], {}, 9.0, 1.0))
        self.assertEqual(C.Timing().load_min_s, C.LOAD_MIN_S)
        self.assertEqual(C.LOAD_MIN_S, 1.0)
        # at its rate the upload reaches the cap before min_s: 400 MB/s × 5 s = the full cap
        self.assertTrue(C.early(10 ** 8, 0.25, 4 * 10 ** 8, 2 * 10 ** 9))
        self.assertFalse(C.early(10 ** 8, 0.25, 3.9 * 10 ** 8, 2 * 10 ** 9))
        self.assertFalse(C.early(10 ** 8, 5.0, 10 ** 12, 2 * 10 ** 9))                          # min_s is over
        # ... but never sooner than load_min_s: 20 Gbit/s at 0,25 s — 1375 MB left, 0,55 s at the rate
        self.assertFalse(C.early(625 * 10 ** 6, 0.25, 2.5 * 10 ** 9, 2 * 10 ** 9))
        self.assertTrue(C.early(625 * 10 ** 6, 0.25, 1.375 * 10 ** 9, 2 * 10 ** 9))             # 1 s left: the least

    def test_as_before_when_the_cap_comes_after_5_s(self):
        """3 Гбит/с (2000 МБ за 5,3 с) и 1 Гбит/с (без упора): строки побайтно те же, что у кода до правки
        (a4b91186), срезов — два, как было (поддельный ``tcp`` считает вызовы)."""
        def text(gbit):
            mib = gbit * 1e9 / 8 * 0.25 / 1048576.0
            out = ["Connecting to host 203.0.113.5, port 5201"]
            for k in range(48):
                j = k if k < 8 else k - 8
                out.append("[SUM] %6.2f-%-6.2f sec  %6.1f MBytes  %5.2f Gbits/sec    0%s"
                           % (j * 0.25, (j + 1) * 0.25, mib, gbit, "       (omitted)" if k < 8 else ""))
            return "\n".join(out) + "\n"

        def rows_of(gbit):
            calls = []

            def tcp(pid, port):
                calls.append(port)
                k = len(calls) - 1
                return dict((("s", i), {"snd_mss": 1448, "rtt": 20000 + 7000 * k, "min_rtt": 9000,
                                        "bytes_sent": 10 ** 6 + k * 70 * 10 ** 6, "bytes_retrans": k * 350000,
                                        "dsack_dups": k}) for i in range(4))

            def popen(argv, **kw):
                p = FakeProc(text(gbit), argv)
                p.pid = 4242
                return p

            class N(object):
                def http(self, side, t, cap, probe=None):
                    return {"value": 900.0, "lower": False, "budget": False, "bytes": 10 ** 6, "family": 4,
                            "how": "http", "tcp": None, "load": None, "ca": None, "why": None, "target": t["id"]}

                def iperf(self, side, t, cap):
                    return C.iperf_try(side, t, cap, C.T, popen=popen, drop=[], tcp=tcp)

                def reach(self, point):
                    return dict(point, addr="77.88.8.8")
            return C.measure("full", C.Env(ru=True, iperf=True, have_iperf=True, manual=True), N()), len(calls)
        load = {"retr": {"test": "load_retr", "value": 0.4979, "why": None, "unit": "%"},
                "rtt": {"test": "load_rtt", "value": 18.0, "why": None, "unit": "ms"}}
        # a4b91186: python3 golden script of the report R-3313-fix-n1 over the code before the fix — the same JSON
        self.assertEqual(rows_of(3.0), ([
            {"test": "net_down", "value": 900.0, "why": None, "unit": "Mbit/s", "extra": {"target": "selectel"}},
            {"test": "net_up", "value": 2999.766, "why": None, "unit": "Mbit/s",
             "extra": {"budget": True, "target": "hostkey-msk"}}, load["retr"], load["rtt"]], 2))
        rows, n = rows_of(1.0)
        self.assertEqual((rows[2:], n), ([load["retr"], load["rtt"]], 2))
        self.assertNotIn("budget", rows[1].get("extra") or {})


# ── the tests of a direction: targets, confirm, Russia, 8b, skip, NS-S16 ────

class Net(object):
    """``vals`` — {(how, target id): [values of the tries]} (None — failed, or a reason); the calls in ``calls``;
    ``live`` — the ids of the iperf3 points that answer (None — all); the checks of the points in ``reached``."""

    def __init__(self, vals=None, default=640.0, live=None, nbytes=10 ** 6):
        self.vals, self.default, self.live, self.nbytes = vals or {}, default, live, nbytes
        self.calls, self.reached = [], []

    def _try(self, how, side, t, cap, probe):
        key = (how, side, t["id"])
        n = sum(1 for c in self.calls if c[:3] == key)
        vs = self.vals.get(key, [self.default])
        v = vs[min(n, len(vs) - 1)]
        self.calls.append(key + (cap,))
        why = None
        if isinstance(v, str):
            v, why = None, v
        elif v is None:
            why = "points_busy" if how == "iperf3" else "no_target"
        return {"value": v, "lower": False, "budget": False, "bytes": self.nbytes, "family": 6, "how": how,
                "tcp": ({(0, 1): snap(10 ** 6)}, {(0, 1): snap(11 * 10 ** 6, retr=100000, rtt=50000)})
                if how == "http" and side == "up" else None,
                "load": ("a", "b") if probe and v is not None else None,
                "ca": "bbr" if side == "up" and how == "http" else None,
                "why": why, "target": t["id"]}

    def http(self, side, t, cap, probe=None):
        return self._try("http", side, t, cap, probe)

    def iperf(self, side, t, cap):
        assert t.get("addr"), "iperf3 without a checked address"
        return self._try("iperf3", side, t, cap, None)

    def reach(self, point):
        self.reached.append(point["id"])
        if self.live is not None and point["id"] not in self.live:
            return None
        return dict(point, addr="77.88.8.8")


def by(rows):
    return dict((r["test"], r) for r in rows)


class SideTest(unittest.TestCase):
    def setUp(self):
        self.slept = []
        p = mock.patch.object(C, "_sleep", lambda s: self.slept.append(s))
        p.start()
        self.addCleanup(p.stop)

    def env(self, **kw):
        return C.Env(**kw)

    def test_abroad_both_sides_and_8c(self):
        net = Net()
        rows = by(C.measure("quick", self.env(ru=False), net))
        self.assertEqual(list(rows), list(C.TESTS))
        self.assertEqual((rows["net_down"]["value"], rows["net_down"]["unit"]), (640.0, "Mbit/s"))
        self.assertEqual(rows["net_up"]["ca"], "bbr")
        self.assertNotIn("ca", rows["net_down"])
        self.assertEqual([c[:3] for c in net.calls], [("http", "down", "cloudflare"), ("http", "up", "cloudflare")])
        self.assertEqual(net.calls[0][3], C.SIDE_CAP["quick"])
        self.assertAlmostEqual(rows["load_retr"]["value"], 1.0)
        self.assertAlmostEqual(rows["load_rtt"]["value"], 30.0)
        self.assertEqual((rows["load_retr"]["unit"], rows["load_rtt"]["unit"], rows["load_rtt"]["ca"]), ("%", "ms", "bbr"))
        self.assertEqual(C.load_line(list(rows.values())), ("bench_run.load.line", {"retr": 1.0, "rtt": 30}))

    def test_unknown_country(self):
        """REV З-3 = SEC З-5 (the coordinator 19.10): unknown — «Russia» (no upload but to the allowed point), with a
        line «страна не определена — меряю как для России»; П3-а after B9: by the button too — one rule. A full check
        by the button without 8b — the person turned it off (D-144): ``upload_off``, not «в России не меряется»."""
        for kw in ({}, {"manual": False}, {"manual": True}):
            net, lines = Net(), []
            rows = by(C.measure("full", self.env(ru=None, log=lambda k, **a: lines.append(k), **kw), net))
            self.assertEqual([c[:3] for c in net.calls], [("http", "down", "selectel")])
            self.assertEqual(rows["net_up"]["why"], "upload_off" if kw.get("manual") else "no_upload_ru", kw)
            self.assertEqual(lines[0], "bench_run.log.country_ru")
        net = Net()
        C.measure("full", self.env(ru=None, up_url="https://up.example.com/u", up_cap_mb=300), net)
        self.assertEqual(net.calls[1][2:], ("allowed", 300 * 10 ** 6))   # the allowed point and its cap
        net = Net()
        C.measure("full", self.env(ru=None, manual=True, up_url="https://up.example.com/u", up_cap_mb=300), net)
        self.assertEqual(net.calls[1][2:], ("allowed", 300 * 10 ** 6))   # by the button — the same point and cap
        # unknown by the button with the line of consent of 8b: as in Russia — the upload by iperf3 (no HTTP point)
        net = Net()
        C.measure("full", self.env(ru=None, manual=True, iperf=True, have_iperf=True), net)
        self.assertEqual([c[:3] for c in net.calls], [("http", "down", "selectel"), ("iperf3", "up", "hostkey-msk")])
        self.assertFalse([c for c in net.calls if c[2] == "cloudflare"])
        # a known country writes no such line
        for ru in (True, False):
            lines = []
            C.measure("quick", self.env(ru=ru, log=lambda k, **a: lines.append(k)), Net())
            self.assertNotIn("bench_run.log.country_ru", lines)

    def test_a_target_that_fails_gives_the_next(self):
        net = Net({("http", "down", "cloudflare"): [None]})
        rows = by(C.measure("quick", self.env(ru=False), net))
        self.assertEqual(rows["net_down"]["value"], 640.0)
        self.assertEqual([c[2] for c in net.calls if c[1] == "down"], ["cloudflare", "ovh"])
        net = Net({("http", "down", "cloudflare"): [None], ("http", "down", "ovh"): [None]})
        rows = by(C.measure("quick", self.env(ru=False), net))
        self.assertEqual((rows["net_down"]["value"], rows["net_down"]["why"]), (None, "no_target"))

    def test_confirm_below_the_anchor_at_another_target(self):
        net = Net({("http", "down", "cloudflare"): [120.0], ("http", "down", "ovh"): [280.0]})
        rows = by(C.measure("quick", self.env(ru=False), net))
        self.assertEqual(rows["net_down"]["value"], 280.0)               # the best of the two
        down = [c for c in net.calls if c[1] == "down"]
        self.assertEqual([c[2] for c in down], ["cloudflare", "ovh"])
        self.assertEqual(down[1][3], C.SIDE_CAP["quick"] - 10 ** 6)      # one cap for both tries
        self.assertEqual(self.slept, [])
        # above the anchor — one try only
        net = Net({("http", "down", "cloudflare"): [300.0]})
        C.measure("quick", self.env(ru=False), net)
        self.assertEqual(len([c for c in net.calls if c[1] == "down"]), 1)

    def test_confirm_at_the_same_target_after_a_wait(self):
        net = Net({("http", "up", "cloudflare"): [150.0, 120.0]})
        rows = by(C.measure("quick", self.env(ru=False), net))
        self.assertEqual(rows["net_up"]["value"], 150.0)
        self.assertEqual([c[2] for c in net.calls if c[1] == "up"], ["cloudflare", "cloudflare"])
        self.assertEqual(self.slept, [20.0])                           # RS §2 «до +20 с» (REV З-7)

    def test_no_confirm_without_time_or_cap(self):
        net = Net({("http", "up", "cloudflare"): [150.0]})
        C.measure("quick", self.env(ru=False, left=lambda: 60.0), net)       # 20 s wait + a try + the series B
        self.assertEqual(len([c for c in net.calls if c[1] == "up"]), 1)
        net = Net({("http", "down", "cloudflare"): [150.0]})
        with mock.patch.object(C, "RETRY_MIN_SHARE", 0.999):
            C.measure("quick", self.env(ru=False), net)
        self.assertEqual(len([c for c in net.calls if c[1] == "down"]), 1)
        rows = by(C.measure("quick", self.env(ru=False, left=lambda: 5.0), Net()))
        self.assertEqual(rows["net_down"]["why"], "time")

    def test_every_try_leaves_time_for_the_series_b(self):
        """REV З-1: the first tries too keep TAIL_S for cpu_b (25 s of the step TAIL), not only the confirm."""
        self.assertGreaterEqual(C.TAIL_S, 25)
        self.assertEqual(C.NEED_S, C.TRY_NEED_S + C.TAIL_S)
        left = [C.TRY_NEED_S + C.TAIL_S + 15.0]

        class Slow(Net):                                               # every try takes 20 s, then fails
            def _try(self, *a):
                left[0] -= 20.0
                r = Net._try(self, *a)
                return dict(r, value=None, why="no_target")
        net = Slow()
        rows = by(C.measure("quick", self.env(ru=False, left=lambda: left[0]), net))
        self.assertEqual(len(net.calls), 1)                            # the second try would eat the series B
        self.assertEqual(rows["net_down"]["why"], "time")
        self.assertGreaterEqual(left[0], 25)

    def test_one_cap_for_all_the_tries(self):
        """SEC З-2: the bytes of a failed try count against the cap of the direction; no try with the cap spent."""
        net = Net({("http", "down", "cloudflare"): [None], ("http", "down", "ovh"): [None]},
                  nbytes=C.SIDE_CAP["quick"])
        C.measure("quick", self.env(ru=False), net)
        self.assertEqual(len([c for c in net.calls if c[1] == "down"]), 1)
        net = Net({("http", "down", "cloudflare"): [None]}, nbytes=C.SIDE_CAP["quick"] // 2)
        C.measure("quick", self.env(ru=False), net)
        down = [c for c in net.calls if c[1] == "down"]
        self.assertEqual(down[1][3], C.SIDE_CAP["quick"] // 2)

    def test_russia_without_a_point_never_uploads(self):
        net = Net()
        rows = by(C.measure("quick", self.env(ru=True), net))
        self.assertEqual(rows["net_down"]["value"], 640.0)
        self.assertEqual([c[:3] for c in net.calls], [("http", "down", "selectel")])
        self.assertEqual((rows["net_up"]["value"], rows["net_up"]["why"]), (None, "no_upload_ru"))
        self.assertEqual(rows["load_retr"]["why"], "no_upload")
        # the schedule: no 8b whatever is asked of the env (iperf False); and never Cloudflare from Russia
        self.assertFalse([c for c in net.calls if c[2] == "cloudflare"])

    def test_russia_with_the_allowed_point(self):
        net = Net()
        rows = by(C.measure("full", self.env(ru=True, up_url="https://up.example.com/__up", up_cap_mb=500), net))
        self.assertEqual(net.calls[1][:3], ("http", "up", "allowed"))
        self.assertEqual(net.calls[1][3], 500 * 10 ** 6)                 # the cap of the point, below 2 GB
        self.assertEqual(rows["net_up"]["value"], 640.0)
        rows = by(C.measure("quick", self.env(ru=True, up_url="http://up.example.com/x"), Net()))
        self.assertEqual(rows["net_up"]["why"], "no_upload_ru")          # not https — not a point
        self.assertNotIn("extra", rows["net_up"])                        # the country is known: no mark

    def test_unknown_country_is_seen_by_the_person(self):
        """REV-ns-b3-2 З-1: «страна не определена» is on both rows of test 8 (bench.extra.geo), not only in the
        journal — by the button and by the schedule (as Russia both, П3-а); a known country — no mark."""
        class Budget(Net):
            def _try(self, *a):
                return dict(Net._try(self, *a), budget=True)
        rows = by(C.measure("quick", self.env(ru=None, manual=True, up_url="https://up.example.com/u"), Budget()))
        self.assertEqual(rows["net_down"]["extra"], {"geo": "unknown_ru", "budget": True, "target": "selectel"})
        self.assertEqual(rows["net_up"]["extra"], {"geo": "unknown_ru", "budget": True, "target": "allowed"})
        rows = by(C.measure("quick", self.env(ru=None, manual=False), Net()))
        self.assertEqual(rows["net_down"]["extra"], {"geo": "unknown_ru", "target": "selectel"})
        self.assertEqual((rows["net_up"]["why"], rows["net_up"]["extra"]), ("no_upload_ru", {"geo": "unknown_ru"}))
        self.assertNotIn("extra", rows["load_retr"])
        # REV-ns-b3-3 П-3: «исключено» (skip) — nothing measured there: no «страна не определена» beside it
        for skip, test in ((("up",), "net_up"), (("down",), "net_down")):
            rows = by(C.measure("quick", self.env(ru=None, manual=True, skip=skip), Net()))
            self.assertEqual(rows[test]["why"], "excluded", skip)
            self.assertNotIn("extra", rows[test], skip)
        for ru in (True, False):
            rows = by(C.measure("quick", self.env(ru=ru), Net()))
            self.assertNotIn("geo", rows["net_down"].get("extra") or {}, ru)            # the country is known: no mark

    def test_8c_comes_from_the_upload_of_8b(self):
        """D-144: the rows «под нагрузкой» of a node in Russia come from the streams of 8b (before — «no_upload»)."""
        class Net8c(Net):
            def iperf(self, side, t, cap):
                r = Net.iperf(self, side, t, cap)
                if side == "up" and r["value"] is not None:
                    r["tcp"] = ({(0, 1): snap(10 ** 6)}, {(0, 1): snap(11 * 10 ** 6, retr=100000, rtt=50000)})
                return r
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True, manual=True), Net8c()))
        self.assertEqual(rows["net_up"]["value"], 640.0)
        self.assertAlmostEqual(rows["load_retr"]["value"], 1.0)
        self.assertAlmostEqual(rows["load_rtt"]["value"], 30.0)

    def test_the_upload_turned_off_is_said_in_words(self):
        """D-144: a full check by the button with 8b turned off — ``upload_off`` on the upload and on 8c (the history
        says «выключено при запуске»); the quick one, the schedule and the repeat — ``no_upload_ru`` as before."""
        rows = by(C.measure("full", self.env(ru=True, manual=True, have_iperf=True), Net()))
        self.assertEqual([rows[t]["why"] for t in ("net_up", "load_retr", "load_rtt")], ["upload_off"] * 3)
        rows = by(C.measure("full", self.env(ru=True, manual=True, iperf=True, have_iperf=True, skip=("8b",)), Net()))
        self.assertEqual(rows["net_up"]["why"], "upload_off")
        for mode, manual in (("quick", True), ("full", False)):
            rows = by(C.measure(mode, self.env(ru=True, manual=manual, have_iperf=True), Net()))
            self.assertEqual((rows["net_up"]["why"], rows["load_retr"]["why"]), ("no_upload_ru", "no_upload"), mode)
        rows = by(C.measure("full", self.env(ru=False, manual=True), Net()))       # abroad: HTTP, nothing to turn off
        self.assertEqual(rows["net_up"]["value"], 640.0)

    def test_8b_by_the_button_in_russia(self):
        net = Net({("iperf3", "up", "hostkey-msk"): [None], ("iperf3", "up", "mts-msk"): [150.0, 190.0]})
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual([c[:3] for c in net.calls if c[1] == "up"],
                         [("iperf3", "up", "hostkey-msk"), ("iperf3", "up", "mts-msk"), ("iperf3", "up", "mts-msk")])
        self.assertEqual(rows["net_up"]["value"], 190.0)                 # busy -> next; below 200 -> the same point
        self.assertEqual(self.slept, [C.RETRY_WAIT])                     # ... after 20 s
        # REV С-1 = SEC З-4: the points one by one, until the first live — never a connection to all 14
        self.assertEqual(net.reached, ["hostkey-msk", "mts-msk"])
        self.assertNotIn("ca", rows["net_up"])
        self.assertEqual(rows["load_rtt"]["why"], "no_tcp_info")         # 8c over 8b: no sampler of the sockets here
        self.assertEqual(net.calls[0][:3], ("http", "down", "selectel"))  # the download stays HTTP
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=False), Net()))
        self.assertEqual(rows["net_up"]["why"], "no_iperf3")
        net = Net(live=("ertelecom-spb",))
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual(net.reached, ["hostkey-msk", "mts-msk", "beeline-vrn", "beeline-kgd", "ertelecom-spb"])
        self.assertEqual([c[2] for c in net.calls if c[0] == "iperf3"], ["ertelecom-spb"])
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), Net(live=())))
        self.assertEqual(rows["net_up"]["why"], "points_busy")
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True, skip=("8b",)), Net()))
        self.assertEqual(rows["net_up"]["why"], "no_upload_ru")

    def test_8b_never_on_blind(self):
        """SEC З-2: a broken iperf3 ends the direction (no next point with the full cap); busy points — 3 runs."""
        net = Net({("iperf3", "up", "hostkey-msk"): ["tool_failed"]})
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual([c[2] for c in net.calls if c[0] == "iperf3"], ["hostkey-msk"])
        self.assertEqual(rows["net_up"]["why"], "tool_failed")
        net = Net(dict((("iperf3", "up", p["id"]), [None]) for p in C.load_targets()["iperf3"]["ru"]))
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual(len([c for c in net.calls if c[0] == "iperf3"]), C.IPERF_RUNS)
        self.assertEqual(len(net.reached), C.IPERF_RUNS)
        self.assertEqual(rows["net_up"]["why"], "points_busy")

    def test_the_confirm_is_inside_the_runs(self):
        """REV-ns-b3-2 П-1: busy, busy, 100 Mbit/s — 3 runs; the confirm at the same point would be the 4th."""
        net = Net({("iperf3", "up", "hostkey-msk"): [None], ("iperf3", "up", "mts-msk"): [None],
                   ("iperf3", "up", "beeline-vrn"): [100.0, 150.0]})
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual([c[2] for c in net.calls if c[0] == "iperf3"], ["hostkey-msk", "mts-msk", "beeline-vrn"])
        self.assertEqual(rows["net_up"]["value"], 100.0)
        self.assertEqual(self.slept, [])

    def test_a_silent_point_ends_the_direction(self):
        """REV-ns-b3-2 П-3: the point took the connection and is silent — no next point, «точки не ответили»."""
        class Silent(Net):
            def iperf(self, side, t, cap):
                return dict(Net.iperf(self, side, t, cap), value=None, why="no_target", end=True)
        net = Silent()
        rows = by(C.measure("full", self.env(ru=True, iperf=True, have_iperf=True), net))
        self.assertEqual([c[2] for c in net.calls if c[0] == "iperf3"], ["hostkey-msk"])
        self.assertEqual(net.reached, ["hostkey-msk"])
        self.assertEqual(rows["net_up"]["why"], "no_target")

    def test_the_journal_never_writes_a_dash_for_a_number(self):
        lines = []
        C.measure("quick", self.env(ru=True, log=lambda k, **a: lines.append((k, a))), Net())
        na = [a for k, a in lines if k == "bench_run.log.channel_na"]
        self.assertEqual(na, [{"test": "net_up", "tries": 0, "why": "no_upload_ru"}])

    def test_8c_says_its_true_reason(self):
        """REV З-2: the upload stopped by the cap before 5 s — cap_early, never «the kernel is older than 4.19»."""
        class Low(Net):
            def _try(self, *a):
                return dict(Net._try(self, *a), lower=True, budget=True)
        rows = by(C.measure("quick", self.env(ru=False), Low()))
        self.assertEqual((rows["load_retr"]["why"], rows["load_rtt"]["why"]), ("cap_early", "cap_early"))

    def test_abroad_8b_is_not_needed(self):
        net = Net()
        C.measure("full", self.env(ru=False, iperf=True, have_iperf=True), net)
        self.assertEqual({c[0] for c in net.calls}, {"http"})

    def test_skip_up(self):
        net = Net()
        rows = by(C.measure("quick", self.env(ru=False, skip=("up",)), net))
        self.assertEqual([c[1] for c in net.calls], ["down"])
        self.assertEqual([rows[t]["why"] for t in ("net_up", "load_retr", "load_rtt")], ["excluded"] * 3)

    def test_people_and_value_corr(self):
        seen = []

        def people(a, b, side):
            seen.append((a, b, side))
            return 180.0 if side == "down" else 0.0
        levels = []

        def level(test, mode, ts):
            levels.append((test, mode))
            return 600.0
        net = Net({("http", "down", "cloudflare"): [420.0]})
        rows = by(C.measure("full", self.env(ru=False, probe=lambda: 1, people=people, level=level, port=550), net))
        self.assertEqual(seen, [("a", "b", "down"), ("a", "b", "up")])
        self.assertEqual(levels, [("net_down", "full"), ("net_up", "full")])
        d = rows["net_down"]
        self.assertEqual((d["value"], d["people"], d["value_corr"]), (420.0, 180.0, 550.0))   # min(600, L 600, port 550)
        self.assertEqual(d["value_corr"], score.load_correct(420.0, 180.0, 600.0, 550))
        u = rows["net_up"]
        self.assertEqual((u["people"], u["value_corr"]), (0.0, 550.0))
        # no L — «под нагрузкой, не годен»: people kept, no value_corr; unknown people — neither
        rows = by(C.measure("full", self.env(ru=False, probe=lambda: 1, people=people, level=lambda *a: None), Net()))
        self.assertEqual((rows["net_down"]["people"], rows["net_down"].get("value_corr")), (180.0, None))
        rows = by(C.measure("full", self.env(ru=False, probe=lambda: 1, people=lambda *a: None, level=level), Net()))
        self.assertNotIn("people", rows["net_down"])
        self.assertNotIn("value_corr", rows["net_down"])
        # a probe that raises is no correction, never the end of the test
        rows = by(C.measure("full", self.env(ru=False, probe=lambda: 1, people=lambda *a: 1 / 0, level=level), Net()))
        self.assertEqual(rows["net_down"]["value"], 640.0)

    def test_family_and_the_journal(self):
        fam, lines = [], []
        C.measure("quick", self.env(ru=False, family=fam.append, log=lambda k, **kw: lines.append((k, kw))), Net())
        self.assertEqual(fam, [6, 6])
        self.assertEqual([k for k, _ in lines], ["bench_run.log.channel"] * 2)
        self.assertEqual(lines[0][1]["target"], "cloudflare")

    def test_budget_mark(self):
        class B(Net):
            def _try(self, *a):
                r = Net._try(self, *a)
                r["budget"] = True
                return r
        rows = by(C.measure("quick", self.env(ru=False), B()))
        self.assertEqual(rows["net_down"]["extra"], {"budget": True, "target": "cloudflare"})


class StepsTest(unittest.TestCase):
    def test_the_step_and_the_extension_point(self):
        st = C.steps("quick")
        self.assertEqual([s[0] for s in st], ["net"])
        self.assertEqual(st[0][3]("quick"), C.TESTS)
        with self.assertRaises(ValueError):
            C.steps("quick", skip=("disk",))
        got = []

        class Ctx(object):
            mode = "full"
            rows = []

            def channel_env(self, skip, iperf):
                got.append((sorted(skip), iperf))
                return C.Env(ru=False, skip=skip, iperf=iperf)

            def row(self, test, value, why=None, unit=None, **more):
                self.rows.append((test, value, why, unit, more))
        c = Ctx()
        with mock.patch.object(C, "Net", lambda: Net()):
            C.steps("full", skip=("8b", "up"), iperf=True)[0][1](c)
        self.assertEqual(got, [(["8b", "up"], False)])                  # 8b skipped wins over the button
        self.assertEqual([r[0] for r in c.rows], list(C.TESTS))
        self.assertEqual(c.rows[0][3], "Mbit/s")


if __name__ == "__main__":
    unittest.main()


# ── B9: the upload «no_target» with a live target (П3-а) ───────────────────
class SlowFirstConnectionTest(unittest.TestCase):
    """B9 (T-B9 п. 4): the first TLS connection to Cloudflare from a Russian network hangs now and then. The clock of
    the window starts at the first connection of the try — a slow connection no longer eats the 10 s, and a try whose
    connection never comes ends in ``conn_s``, never leaving a connection open."""
    setUp, tearDown = LocalHttpTest.setUp, LocalHttpTest.tearDown
    connect, target = LocalHttpTest.connect, LocalHttpTest.target

    def slow(self, delay, opened=None):
        def connect(host, port, timeout):
            time.sleep(delay)
            c = self.connect(host, port, timeout)
            if opened is not None:
                opened.append(c)
            return c
        return connect

    def test_a_slow_connection_is_not_in_the_window(self):
        tm = C.Timing(run_s=1.0, warm_s=0.2, min_s=0.5, tick_s=0.05, io_s=2.0, grace_s=1.0, bg_s=0.01, conn_s=3.0)
        for side, path in (("up", "/__up"), ("down", "/__down?bytes={bytes}")):
            t0 = time.monotonic()
            r = C.http_try(side, self.target(path), 40 * 10 ** 9, tm, connect=self.slow(1.3))  # > run_s
            self.assertIsNotNone(r["value"], (side, r))
            self.assertIsNone(r["why"])
            self.assertGreater(time.monotonic() - t0, 1.3 + 0.9)       # the whole window after the connection

    def test_no_connection_in_conn_s_is_no_target_and_closed(self):
        tm = C.Timing(run_s=1.0, warm_s=0.2, min_s=0.5, tick_s=0.05, io_s=2.0, grace_s=0.1, bg_s=0.01, conn_s=0.3)
        opened = []
        t0 = time.monotonic()
        r = C.http_try("up", self.target("/__up"), 10 ** 9, tm, connect=self.slow(0.8, opened))
        self.assertLess(time.monotonic() - t0, 0.8)                   # conn_s + the grace, not run_s
        self.assertEqual((r["value"], r["why"], r["bytes"]), (None, "no_target", 0))
        time.sleep(1.0)                                                # the late connections came — and went
        self.assertEqual(len(opened), C.STREAMS)
        self.assertTrue(all(c.sock is None for c in opened), "a late connection left open")
        self.assertEqual(_Handler.seen, [])                            # not a byte was sent

    def test_the_default_clock(self):
        self.assertEqual(C.T.conn_s + C.T.run_s + C.T.grace_s, C.TRY_NEED_S)


class AddrOrderTest(unittest.TestCase):
    """B9: a target's addresses — the families in turn, ``ADDR_S`` per address (a broken IPv6 costs one ADDR_S)."""

    def infos(self, *fams):
        return [(f, socket.SOCK_STREAM, 6, "", ("::1" if f == socket.AF_INET6 else "127.0.0.1", 443 + i))
                for i, f in enumerate(fams)]

    def test_families_in_turn(self):
        v6, v4 = socket.AF_INET6, socket.AF_INET
        got = [i[4][1] for i in C.addr_order(self.infos(v6, v6, v4, v4))]
        self.assertEqual(got, [443, 445, 444, 446])
        self.assertEqual([i[4][1] for i in C.addr_order(self.infos(v4, v6))], [443, 444])
        self.assertEqual(C.addr_order([]), [])

    def test_a_hanging_ipv6_costs_one_addr_s(self):
        tried = []

        class Sock(object):
            def __init__(self, fam, kind, proto):
                self.fam, self.t = fam, None

            def settimeout(self, t):
                self.t = t if self.t is None else self.t

            def connect(self, addr):
                tried.append((self.fam, self.t))
                if self.fam == socket.AF_INET6:
                    raise socket.timeout("timed out")

            def close(self):
                pass
        v6, v4 = socket.AF_INET6, socket.AF_INET
        with mock.patch.object(C.socket, "socket", Sock):
            s = C.tcp_connect("speed.example.com", 443, 5.0, getaddrinfo=lambda *a: self.infos(v6, v6, v4))
        self.assertEqual(s.fam, v4)
        self.assertEqual(tried, [(v6, C.ADDR_S), (v4, C.ADDR_S)])     # the second IPv6 never waited for
        with mock.patch.object(C.socket, "socket", Sock):
            with self.assertRaises(OSError):
                C.tcp_connect("speed.example.com", 443, 5.0, getaddrinfo=lambda *a: self.infos(v6))


class FailedTargetsOnceMoreTest(unittest.TestCase):
    """B9: every target of a direction failed with ``no_target`` — the first one once more after ``FAIL_WAIT``; the
    upload abroad (one target) gets its value and the load 8c with it."""
    setUp, env = SideTest.setUp, SideTest.env

    def test_upload_second_try(self):
        net = Net({("http", "up", "cloudflare"): [None, 520.0]})
        rows = by(C.measure("full", self.env(ru=False), net))
        self.assertEqual([c[:3] for c in net.calls if c[1] == "up"], [("http", "up", "cloudflare")] * 2)
        self.assertEqual(rows["net_up"]["value"], 520.0)
        self.assertIn(C.FAIL_WAIT, self.slept)
        self.assertIsNotNone(rows["load_retr"]["value"])
        self.assertIsNotNone(rows["load_rtt"]["value"])

    def test_download_both_failed_first_again_once(self):
        net = Net({("http", "down", "cloudflare"): [None, None], ("http", "down", "ovh"): [None]})
        rows = by(C.measure("full", self.env(ru=False, skip=("up",)), net))
        self.assertEqual([c[2] for c in net.calls], ["cloudflare", "ovh", "cloudflare"])
        self.assertEqual(rows["net_down"]["why"], "no_target")

    def test_once_more_in_the_cap_left_and_the_steps_counted(self):
        """REV-ns-p3a З-3 (SEC З-2): the cap of a direction is one for every try — the try once more gets what the
        tries before it left (cap − bytes), not the whole cap again. П-1: «шаг 3 из 3», never «3 из 2»."""
        mb = 10 ** 6
        cap = C.SIDE_CAP["full"]
        steps = []
        net = Net({("http", "down", "cloudflare"): [None, None], ("http", "down", "ovh"): [None]}, nbytes=cap * 3 // 10)
        C.measure("full", self.env(ru=False, skip=("up",), progress=lambda t, i, n: steps.append((t, i, n))), net)
        self.assertEqual([c[2] for c in net.calls], ["cloudflare", "ovh", "cloudflare"])
        self.assertEqual([c[3] for c in net.calls], [cap, cap - net.nbytes, cap - 2 * net.nbytes])
        self.assertEqual(steps, [("net_down", 1, 2), ("net_down", 2, 2), ("net_down", 3, 3)])
        steps[:] = []                                         # a success after a failed target: its confirm is the 3rd
        net = Net({("http", "down", "cloudflare"): [None], ("http", "down", "ovh"): [1.0, 2.0]}, nbytes=mb)
        C.measure("full", self.env(ru=False, skip=("up",), progress=lambda t, i, n: steps.append((t, i, n))), net)
        self.assertEqual([c[2] for c in net.calls], ["cloudflare", "ovh", "ovh"])      # the confirm: ovh again
        self.assertEqual(steps, [("net_down", 1, 2), ("net_down", 2, 2), ("net_down", 3, 3)])
        self.assertTrue(all(i <= n for _t, i, n in steps))

    def test_no_second_try_when_not_no_target_or_no_time(self):
        net = Net({("http", "up", "cloudflare"): ["tool_failed"]})
        C.measure("full", self.env(ru=False, skip=("down",)), net)
        self.assertEqual(len(net.calls), 1)
        left = [C.TRY_NEED_S + C.TAIL_S + 1.0]
        net = Net({("http", "up", "cloudflare"): [None, 500.0]})
        rows = by(C.measure("full", self.env(ru=False, skip=("down",), left=lambda: left[0]), net))
        self.assertEqual(len(net.calls), 1)                            # FAIL_WAIT + a try would eat the series B
        self.assertEqual(rows["net_up"]["why"], "no_target")
        self.assertNotIn(C.FAIL_WAIT, self.slept)
