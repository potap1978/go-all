"""kaskad.net.socks: the SOCKS5 client (domain names go to the proxy), errors, the HTTP(S) client through
SOCKS5 and through an HTTP proxy (CONNECT), TLS verification, multipart uploads; and the Bot API client on top."""

import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from kaskad.bot.telegram import Api, ApiError, NetworkError
from kaskad.net import socks
from tests.bot.fake_socks import FakeSocks, relay
from tests.bot.fake_tg import TOKEN, FakeTelegram
from tests.netports import free_port


class Echo(object):
    """A TCP server that echoes what it gets."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        t = threading.Thread(target=self._run)
        t.daemon = True
        t.start()

    def _run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            data = c.recv(100)
            c.sendall(data)
            c.close()

    def close(self):
        self.sock.close()


class ConnectProxy(object):
    """A minimal HTTP proxy that only does CONNECT (what http:// BOT_PROXY means)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        t = threading.Thread(target=self._run)
        t.daemon = True
        t.start()

    def _run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(1024)
            if not chunk:
                return
            buf += chunk
        line = buf.split(b"\r\n", 1)[0].decode()
        self.requests.append(line)
        host, port = line.split()[1].rsplit(":", 1)
        up = socket.create_connection(self.routes.get((host, int(port)), (host, int(port))))
        c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        relay(c, up)

    def close(self):
        self.sock.close()


class ParseProxyTest(unittest.TestCase):
    def test_forms(self):
        self.assertIsNone(socks.parse_proxy(""))
        self.assertIsNone(socks.parse_proxy("   "))
        p = socks.parse_proxy("socks5h://127.0.0.1:10808")
        self.assertEqual((p.scheme, p.host, p.port, p.socks), ("socks5h", "127.0.0.1", 10808, True))
        self.assertEqual(socks.parse_proxy("SOCKS5://proxy.example").port, 1080)
        p = socks.parse_proxy("http://10.0.0.1:3128")
        self.assertEqual((p.scheme, p.socks), ("http", False))
        self.assertEqual(str(socks.parse_proxy("socks5://[::1]:9050")), "socks5://[::1]:9050")

    def test_refused_forms_say_why_without_the_secret(self):
        with self.assertRaises(ValueError) as cm:
            socks.parse_proxy("socks5://user:S3cretPass@127.0.0.1:1080")
        self.assertIn("логином и паролем", str(cm.exception))
        self.assertNotIn("S3cretPass", str(cm.exception))
        for bad in ("socks4://127.0.0.1:1080", "https://127.0.0.1:1080", "127.0.0.1:1080", "socks5://h:99999",
                    "socks5://:1080"):
            with self.assertRaises(ValueError, msg=bad):
                socks.parse_proxy(bad)


class Socks5Test(unittest.TestCase):
    def setUp(self):
        self.echo = Echo()

    def tearDown(self):
        self.echo.close()

    def proxy(self, **kw):
        p = FakeSocks(routes={("api.telegram.org", 443): ("127.0.0.1", self.echo.port)}, **kw).start()
        self.addCleanup(p.stop)
        return p

    def test_connect_sends_the_name_to_the_proxy(self):
        p = self.proxy()
        s = socks.socks5_connect("127.0.0.1", p.port, "api.telegram.org", 443, timeout=5)
        try:
            s.sendall(b"hello")
            self.assertEqual(s.recv(100), b"hello")
        finally:
            s.close()
        self.assertEqual(p.greetings, [(5, [0])])                    # no authentication offered
        self.assertEqual(p.requests, [(3, "api.telegram.org", 443)])   # ATYP 3: the proxy resolves the name

    def test_ip_literals_go_as_addresses(self):
        p = self.proxy()
        s = socks.socks5_connect("127.0.0.1", p.port, "127.0.0.1", self.echo.port, timeout=5)
        s.close()
        self.assertEqual(p.requests, [(1, "127.0.0.1", self.echo.port)])

    def test_errors(self):
        cases = [({"reply": 5}, "в соединении отказано"), ({"reply": 2}, "запрещено правилами"),
                 ({"method": 0xFF}, "логин и пароль"), ({"close_after_greeting": True}, "закрыл соединение")]
        for kw, text in cases:
            p = self.proxy(**kw)
            with self.assertRaises(socks.ProxyError, msg=kw) as cm:
                socks.socks5_connect("127.0.0.1", p.port, "api.telegram.org", 443, timeout=5)
            self.assertIn(text, str(cm.exception))

    def test_silent_proxy_times_out(self):
        p = self.proxy(silent=True)
        with self.assertRaises(OSError):
            socks.socks5_connect("127.0.0.1", p.port, "api.telegram.org", 443, timeout=0.3)

    def test_unreachable_proxy(self):
        port = free_port()                                           # nothing listens there
        with self.assertRaises(socks.ProxyError) as cm:
            socks.socks5_connect("127.0.0.1", port, "api.telegram.org", 443, timeout=2)
        self.assertIn("недоступен", str(cm.exception))


class ApiThroughProxyTest(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram().start()
        self.addCleanup(self.tg.stop)

    def test_bot_api_through_socks5_with_a_domain_name(self):
        p = FakeSocks(routes={("api.telegram.test", 80): ("127.0.0.1", self.tg.port)}).start()
        self.addCleanup(p.stop)
        api = Api(TOKEN, proxy=socks.parse_proxy(p.url), base="http://api.telegram.test")
        self.assertEqual(api.get_me()["username"], "kaskad_test_bot")
        self.assertEqual(p.requests, [(3, "api.telegram.test", 80)])
        api.send_document(555, "kaskad-wg.conf", b"[Interface]\nPrivateKey = x\n", caption="<b>WireGuard</b>")
        call = self.tg.sent("sendDocument")[0]
        self.assertEqual(call.files["document"][:2], ("kaskad-wg.conf", b"[Interface]\nPrivateKey = x\n"))
        self.assertEqual((call.params["chat_id"], call.params["caption"], call.params["parse_mode"]),
                         ("555", "<b>WireGuard</b>", "HTML"))
        api.send_photo(555, b"\x89PNG\r\n\x1a\nfake", caption="QR")
        self.assertEqual(self.tg.sent("sendPhoto")[0].files["photo"][1], b"\x89PNG\r\n\x1a\nfake")

    def test_bot_api_through_an_http_proxy(self):
        cp = ConnectProxy({("api.telegram.test", 80): ("127.0.0.1", self.tg.port)})
        self.addCleanup(cp.close)
        api = Api(TOKEN, proxy=socks.parse_proxy("http://127.0.0.1:%d" % cp.port), base="http://api.telegram.test")
        self.assertEqual(api.get_me()["id"], 123456789)
        self.assertEqual([r.rsplit(".", 1)[0] for r in cp.requests], ["CONNECT api.telegram.test:80 HTTP/1"])

    def test_errors_never_carry_the_token(self):
        p = FakeSocks(reply=4).start()
        self.addCleanup(p.stop)
        api = Api(TOKEN, proxy=socks.parse_proxy(p.url), base="http://api.telegram.test")
        with self.assertRaises(NetworkError) as cm:
            api.get_me()
        self.assertIn("узел недоступен", str(cm.exception))
        self.assertNotIn(TOKEN.split(":")[1], str(cm.exception))
        self.tg.fail("getMe", 401, "Unauthorized: " + TOKEN)
        api = Api(TOKEN, base=self.tg.base)
        with self.assertRaises(ApiError) as cm:
            api.get_me()
        self.assertEqual(cm.exception.code, 401)
        self.assertNotIn(TOKEN.split(":")[1], str(cm.exception))

    def test_timeout_of_a_late_answer(self):
        self.tg.delay["getMe"] = 1.5
        api = Api(TOKEN, base=self.tg.base, timeout=0.3)
        with self.assertRaises(NetworkError) as cm:
            api.get_me(timeout=0.3)
        self.assertIn("таймаут", str(cm.exception))


def _openssl(*args, cwd=None):
    subprocess.run(["openssl"] + list(args), cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class TlsThroughSocksTest(unittest.TestCase):
    """HTTPS to api.telegram.org through SOCKS5: SNI and the hostname check are the API host's, not the proxy's."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="kaskad-tls-")
        d = cls.dir
        _openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                 "-keyout", "ca.key", "-out", "ca.pem", "-days", "2", "-subj", "/CN=Kaskad test CA",
                 "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 cwd=d)
        for name, host in (("good", "api.telegram.org"), ("other", "other.example")):
            _openssl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                     "-keyout", name + ".key", "-out", name + ".csr", "-subj", "/CN=" + host, cwd=d)
            with open(os.path.join(d, name + ".ext"), "w") as f:
                f.write("subjectAltName=DNS:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                        "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n" % host)
            _openssl("x509", "-req", "-in", name + ".csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
                     "-out", name + ".pem", "-days", "2", "-extfile", name + ".ext", cwd=d)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def server(self, name):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(self.dir, name + ".pem"), os.path.join(self.dir, name + ".key"))
        tg = FakeTelegram(ssl_context=ctx).start()
        self.addCleanup(tg.stop)
        p = FakeSocks(routes={("api.telegram.org", 443): ("127.0.0.1", tg.port)}).start()
        self.addCleanup(p.stop)
        return tg, p

    def client(self, proxy):
        ctx = ssl.create_default_context(cafile=os.path.join(self.dir, "ca.pem"))
        return Api(TOKEN, proxy=socks.parse_proxy(proxy.url), context=ctx, timeout=5)

    def test_verified_https_through_socks(self):
        tg, p = self.server("good")
        api = self.client(p)
        self.assertEqual(api.base, "https://api.telegram.org")
        self.assertEqual(api.get_me()["username"], "kaskad_test_bot")
        self.assertEqual(p.requests, [(3, "api.telegram.org", 443)])
        self.assertEqual(tg.sent("getMe")[0].method, "getMe")

    def test_wrong_certificate_is_refused(self):
        tg, p = self.server("other")
        with self.assertRaises(NetworkError):
            self.client(p).get_me()
        self.assertEqual(tg.sent(), [])                               # nothing reached the "API"

    def test_unknown_ca_is_refused(self):
        tg, p = self.server("good")
        api = Api(TOKEN, proxy=socks.parse_proxy(p.url), context=ssl.create_default_context(), timeout=5)
        with self.assertRaises(NetworkError):
            api.get_me()


if __name__ == "__main__":
    unittest.main()
