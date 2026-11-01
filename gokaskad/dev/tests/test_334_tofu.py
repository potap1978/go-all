"""3.3.4: the update does not break an exit that worked with allowInsecure. The keys of the pool with allowInsecure and no
fingerprint get the certificate of their server pinned once (TOFU, egress_parse.py «tofu» -> tofu-pins.json next to the
parser); the parser puts that pin into pinnedPeerCertSha256 (the pool line is not changed: the ids of the exits keep).
A server that did not answer — the key stays in the pool with a warning, tried again not more often than hourly; a key
with allowInsecure that came after the first run is not pinned; a subscription with the same server keeps its pin.
The bash side — tools/k3_install_test.sh («старый 3.3.3 с ключом allowInsecure → 3.3.4»)."""

import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from kaskad import paths
from kaskad.gw import exits
from tests import netports, realbin
from tests.parser_loopback import allow_loopback
from tests.fakes_gw import VLESS_B, install_parser, write_pool
from tests.test_334_xray import _PARSER, PIN, Quiet, key, parser, tls_of
from tests.test_gw_exitsubs import IP_URL, SubsCase, b64

UUID = "11111111-2222-3333-4444-000000000001"
LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parser_loopback.py")   # разборщик подпроцессом: 127.0.0.1


def make_cert(d, cn="test.local"):
    """A self-signed certificate (openssl) -> (cert, key, sha256 hex of the DER)."""
    crt, k = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=" + cn,
                    "-keyout", k, "-out", crt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with open(crt) as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return crt, k, hashlib.sha256(der).hexdigest()


class TlsServer:
    """A TLS server on 127.0.0.1 with a self-signed certificate: handshakes and closes (the SNI it got is kept)."""

    def __init__(self, crt, k):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(crt, k)
        self.snis = []
        self.ctx.sni_callback = lambda s, name, c: self.snis.append(name)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            try:
                c.settimeout(5)
                with self.ctx.wrap_socket(c, server_side=True) as t:
                    t.recv(1)
            except (OSError, ssl.SSLError):
                pass
            finally:
                c.close()

    def close(self):
        self.sock.close()


def free_port():
    return netports.free_port()


def local_key(port, extra="&allowInsecure=1", sni="test.local", name="Self", uuid=UUID):
    return "vless://%s@127.0.0.1:%d?type=tcp&security=tls&sni=%s%s#%s" % (uuid, port, sni, extra, name)


class TofuCase(Quiet):
    """The parser module with its tofu-pins.json in a temporary folder."""

    def setUp(self):
        super().setUp()
        self.p = parser()
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.store = os.path.join(self.d, "tofu-pins.json")
        m = mock.patch.object(self.p, "_TOFU_FILE", self.store)
        m.start()
        self.addCleanup(m.stop)
        # 3.3.6 круг 3: свои TLS-сервер и xray — на петле 127.x; разборщик пропускает её только здесь
        m = mock.patch.object(self.p, "_server_problem", allow_loopback(self.p))
        m.start()
        self.addCleanup(m.stop)
        self.p._tofu_cache.clear()

    def put(self, doc):
        with open(self.store, "w") as f:
            json.dump(doc, f)
        self.p._tofu_cache.clear()

    def doc(self):
        with open(self.store) as f:
            return json.load(f)


class PinFromStoreTest(TofuCase):
    def test_stored_pin_goes_to_pinned_peer_cert(self):
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": PIN, "at": 1, "auto": True}}})
        line = key(1, "&allowInsecure=1")
        ob = self.p.vless_outbound(line, "x")
        self.assertEqual(tls_of(ob)["pinnedPeerCertSha256"], PIN)
        self.assertNotIn("allowInsecure", json.dumps(ob))
        self.assertEqual(self.p.vless_problem(line), "")
        good, skipped = self.p.extract_report(line)
        self.assertEqual((good, skipped), ([line], []))

    def test_only_the_same_server_port_and_sni(self):
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": PIN}}})
        other_port = key(1, "&allowInsecure=1").replace(":8443", ":9443")
        other_sni = key(1, "&allowInsecure=1").replace("sni=a.example.com", "sni=b.example.com")
        other_host = key(2, "&allowInsecure=1")
        for line in (other_port, other_sni, other_host):
            self.assertEqual(self.p.vless_problem(line), "insecure", line)
            self.assertIsNone(self.p.vless_outbound(line, "x"))

    def test_own_pin_of_the_key_wins(self):
        own = "ab" * 32
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": PIN}}})
        ob = self.p.vless_outbound(key(1, "&allowInsecure=1&pcs=" + own), "x")
        self.assertEqual(tls_of(ob)["pinnedPeerCertSha256"], own)

    def test_broken_store_is_no_store(self):
        with open(self.store, "w") as f:
            f.write("{not json")
        self.p._tofu_cache.clear()
        self.assertEqual(self.p.vless_problem(key(1, "&allowInsecure=1")), "insecure")
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": "not-a-sha"}}})
        self.assertEqual(self.p.vless_problem(key(1, "&allowInsecure=1")), "insecure")

    def test_store_read_again_when_it_changes(self):
        line = key(1, "&allowInsecure=1")
        self.assertEqual(self.p.vless_problem(line), "insecure")
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": PIN}}})
        os.utime(self.store, ns=(time.time_ns() + 10 ** 9,) * 2)
        self.assertEqual(self.p.vless_problem(line), "")


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class SnapTest(TofuCase):
    def setUp(self):
        super().setUp()
        self.crt, self.k, self.sha = make_cert(self.d)
        self.srv = TlsServer(self.crt, self.k)
        self.addCleanup(self.srv.close)

    def test_first_run_pins_the_self_signed_certificate(self):
        line = local_key(self.srv.port)
        self.assertEqual(self.p.vless_problem(line), "insecure")
        out = self.p.tofu_snap([line, VLESS_B, key(3, "&pcs=" + PIN)], first=True, timeout=5)
        srv = "127.0.0.1:%d|test.local" % self.srv.port
        self.assertEqual(out, [("pinned", srv)])
        self.assertIn("test.local", self.srv.snis)                         # the SNI of the key
        rec = self.doc()["servers"][srv]
        self.assertEqual((rec["pin"], rec["auto"]), (self.sha, True))
        self.assertNotIn("pending", self.doc())
        self.assertEqual(stat.S_IMODE(os.stat(self.store).st_mode), 0o600)
        ob = self.p.vless_outbound(line, "x")
        self.assertEqual(tls_of(ob)["pinnedPeerCertSha256"], self.sha)
        self.assertEqual(self.p.vless_problem(line), "")
        # done: the next run asks nothing
        self.assertEqual(self.p.tofu_snap([line], first=False, fetch=self.fail_fetch), [])

    def fail_fetch(self, *a):
        raise AssertionError("no handshake expected")

    def test_cli_prints_the_server_not_the_key(self):
        line = local_key(self.srv.port)
        env = dict(os.environ)
        d2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d2, True)
        shutil.copy(_PARSER["path"], os.path.join(d2, "egress_parse.py"))
        r = subprocess.run([sys.executable, LOOP, os.path.join(d2, "egress_parse.py"), "tofu", "--first", "--timeout", "5"],
                           input=line + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "pinned\t127.0.0.1:%d\n" % self.srv.port)
        self.assertNotIn(UUID, r.stdout + r.stderr)
        self.assertNotIn(self.sha, r.stdout + r.stderr)
        with open(os.path.join(d2, "tofu-pins.json")) as f:
            self.assertIn(self.sha, f.read())
        # the parser of that folder now builds the key with the pin
        r = subprocess.run([sys.executable, LOOP, os.path.join(d2, "egress_parse.py"), "config"], input=line + "\n",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertIn('"pinnedPeerCertSha256": "%s"' % self.sha, r.stdout)
        self.assertNotIn("allowInsecure", r.stdout)

    def test_server_down_key_stays_with_a_warning_the_rest_applies(self):
        dead = local_key(free_port(), name="Dead", uuid="11111111-2222-3333-4444-000000000002")
        up = local_key(self.srv.port)
        out = dict((s, st) for st, s in self.p.tofu_snap([dead, up, VLESS_B], first=True, timeout=2, now=1000))
        self.assertEqual(sorted(out.values()), ["failed", "pinned"])
        doc = self.doc()
        dsrv = [s for s, st in out.items() if st == "failed"][0]
        self.assertEqual((doc["pending"][dsrv]["try"], doc["pending"][dsrv]["since"]), (1000, 1000))
        self.assertEqual(self.p.vless_problem(dead), "tofu_wait")             # the warning of the pool: tried again
        cfg, n = self.p.build_config([dead, up, VLESS_B], 10808, "http://x/", "leastPing", "10s", 100, 10851, 0)
        self.assertEqual(n, 2)                                                 # the rest of the pool is applied
        # within the hour — not asked again; after it — asked, and pinned when the server answers
        self.assertEqual(self.p.tofu_snap([dead], now=2000, fetch=self.fail_fetch), [("wait", dsrv)])
        got = self.p.tofu_snap([dead], now=1000 + 3601, fetch=lambda *a: "cd" * 32)
        self.assertEqual(got, [("pinned", dsrv)])
        self.assertNotIn("pending", self.doc())
        self.assertEqual(self.p.vless_problem(dead), "")

    def test_a_key_that_came_after_the_first_run_is_not_pinned(self):
        self.put({"v": 1, "servers": {}})
        line = local_key(self.srv.port)
        self.assertEqual(self.p.tofu_snap([line], first=False, fetch=self.fail_fetch), [])
        self.assertEqual(self.p.vless_problem(line), "insecure")

    def test_pending_of_a_key_gone_from_the_pool_is_dropped(self):
        self.put({"v": 1, "servers": {}, "pending": {"203.0.113.9:443|x.example.com": {"try": 1, "n": 1}}})
        self.p.tofu_snap([VLESS_B], first=False, fetch=self.fail_fetch)
        self.assertNotIn("pending", self.doc())

    def test_a_new_pin_is_written_when_pending_is_unchanged(self):
        """§5в №6: the file had «servers» and no «pending» before and after — the new pin is still written (tofu_snap
        once changed the dict it read in place, and «new == d» skipped the write)."""
        old = {"pin": PIN, "at": 1, "auto": True}
        self.put({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": old}})
        line = local_key(self.srv.port)
        srv = "127.0.0.1:%d|test.local" % self.srv.port
        self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("pinned", srv)])
        doc = self.doc()
        self.assertEqual(doc["servers"]["198.51.100.21:8443|a.example.com"], old)
        self.assertEqual(doc["servers"][srv]["pin"], self.sha)
        self.assertNotIn("pending", doc)
        self.p._tofu_cache.clear()
        self.assertEqual(self.p.vless_problem(line), "")

    def test_one_server_of_two_keys_is_asked_once(self):
        calls = []
        a = local_key(self.srv.port, name="A")
        b = local_key(self.srv.port, name="B", uuid="11111111-2222-3333-4444-000000000003")
        self.p.tofu_snap([a, b], first=True, fetch=lambda *x: calls.append(x) or "ef" * 32)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.p.vless_problem(b), "")


class SubscriptionKeepsThePinTest(SubsCase):
    def setUp(self):
        super().setUp()
        from kaskad import paths
        self.store = os.path.join(os.path.dirname(paths.egress_parser()), "tofu-pins.json")

    def test_same_server_keeps_its_pin(self):
        line = key(1, "&allowInsecure=1", "Marzban")
        with open(self.store, "w") as f:
            json.dump({"v": 1, "servers": {"198.51.100.21:8443|a.example.com": {"pin": PIN, "auto": True}}}, f)
        other = key(4, "&allowInsecure=1", "New")
        res = self.add(IP_URL, b64([line, other, key(3, "&pcs=" + PIN)]))
        self.assertEqual(self.pool(), [line, key(3, "&pcs=" + PIN)])         # the pinned one stays, the new one — not
        self.assertEqual(res["result"]["skipped"], 1)
        r = self.refresh()["results"][0]
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.pool(), [line, key(3, "&pcs=" + PIN)])
        es = exits.pool(self.w.runner)
        self.assertEqual(es[0]["kind"], "xray")                                # an exit of the gateway too
        self.assertEqual(es[0]["outbound"]["streamSettings"]["tlsSettings"]["pinnedPeerCertSha256"], PIN)
        self.assertEqual(es[0]["id"], exits.exit_id(line))                      # the line and its id kept


class _XrayRun(TofuCase):
    """The pinned xray (26.3.27) as a VLESS server and as the client of the parser's config."""

    def start(self, cfg, name):
        f = os.path.join(self.d, name + ".json")
        with open(f, "w") as fh:
            json.dump(cfg, fh)
        log = open(os.path.join(self.d, name + ".log"), "w")
        self.addCleanup(log.close)
        p = subprocess.Popen([realbin.xray_path(), "run", "-c", f], stdout=log, stderr=subprocess.STDOUT)
        self.addCleanup(lambda: (p.terminate(), p.wait(10)))
        return p

    def wait_port(self, port, sec=10):
        end = time.time() + sec
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                return
            except OSError:
                time.sleep(0.1)
        self.fail("port %d did not open" % port)

    def through(self, socks):
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8",
                            "--socks5-hostname", "127.0.0.1:%d" % socks, "http://127.0.0.1:%d/" % self.web],
                           stdout=subprocess.PIPE, universal_newlines=True)
        return r.stdout

    def web_and_server(self, crt, k):
        """A web page behind a VLESS+TLS server with ``crt`` -> the port of the VLESS server."""
        import http.server
        httpd = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        self.web = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        vport = free_port()
        self.start({"log": {"loglevel": "warning"},
                    "inbounds": [{"listen": "127.0.0.1", "port": vport, "protocol": "vless",
                                  "settings": {"clients": [{"id": UUID}], "decryption": "none"},
                                  "streamSettings": {"network": "tcp", "security": "tls",
                                                     "tlsSettings": {"certificates": [{"certificateFile": crt,
                                                                                       "keyFile": k}]}}}],
                    "outbounds": [{"protocol": "freedom"}]}, "server")
        self.wait_port(vport)
        return vport

    def client(self, line, name, ca=None):
        """The parser's config for ``line`` run by xray -> the HTTP code through it. ``ca`` — trusted as the roots of
        the system are on a server (a local CA is not in the roots of this machine)."""
        socks = free_port()
        cfg, n = self.p.build_config([line], socks, "http://127.0.0.1:1/", "leastPing", "10s", 100, free_port(), 0)
        self.assertEqual(n, 1)
        if ca:
            for ob in cfg["outbounds"]:
                tls = (ob.get("streamSettings") or {}).get("tlsSettings")
                if tls is not None:
                    tls["certificates"] = [{"usage": "verify", "certificateFile": ca}]
        p = self.start(cfg, name)
        self.wait_port(socks)
        try:
            return self.through(socks)
        finally:
            p.terminate()
            p.wait(10)


@unittest.skipUnless(realbin.xray_path() and shutil.which("openssl") and shutil.which("curl"),
                     "нет xray (bash tools/fetch_test_bins.sh), openssl или curl")
class RealXrayTest(_XrayRun):
    """The pin taken by «tofu» lets the pinned xray connect to a VLESS server with a self-signed certificate; another
    pin does not (pinnedPeerCertSha256 really checks)."""

    def test_exit_alive_with_the_taken_pin(self):
        crt, k, sha = make_cert(self.d)
        import http.server
        httpd = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
        self.web = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        vport = free_port()
        self.start({"log": {"loglevel": "warning"},
                    "inbounds": [{"listen": "127.0.0.1", "port": vport, "protocol": "vless",
                                  "settings": {"clients": [{"id": UUID}], "decryption": "none"},
                                  "streamSettings": {"network": "tcp", "security": "tls",
                                                     "tlsSettings": {"certificates": [{"certificateFile": crt,
                                                                                       "keyFile": k}]}}}],
                    "outbounds": [{"protocol": "freedom"}]}, "server")
        self.wait_port(vport)
        line = local_key(vport)
        self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("pinned", "127.0.0.1:%d|test.local" % vport)])
        self.assertEqual(self.doc()["servers"]["127.0.0.1:%d|test.local" % vport]["pin"], sha)
        for pin_ok in (True, False):
            if not pin_ok:
                self.put({"v": 1, "servers": {"127.0.0.1:%d|test.local" % vport: {"pin": "00" * 32}}})
            socks = free_port()
            cfg, n = self.p.build_config([line], socks, "http://127.0.0.1:1/", "leastPing", "10s", 100, free_port(), 0)
            self.assertEqual(n, 1)
            p = self.start(cfg, "client-%s" % pin_ok)
            self.wait_port(socks)
            code = self.through(socks)
            if pin_ok:
                self.assertEqual(code, "200", "the exit is dead with the taken pin")
            else:
                self.assertNotEqual(code, "200", "a wrong pin connected — pinnedPeerCertSha256 is not checked")
            p.terminate()
            p.wait(10)


@unittest.skipUnless(realbin.xray_path() and shutil.which("openssl") and shutil.which("curl"),
                     "нет xray (bash tools/fetch_test_bins.sh), openssl или curl")
class RealXrayByNameTest(_XrayRun):
    """§5в №2 on the pinned xray 26.3.27: a key by IP with no sni, the certificate (good chain) for the domain — «tofu»
    takes the name of the certificate, the parser gives it as verifyPeerCertByName; xray connects through the IP
    with the full check (the chain and that name); another name, or a chain it does not trust, — no connection."""

    def test_exit_alive_by_the_name_of_the_certificate(self):
        ca, crt, k = make_ca_cert(self.d)
        vport = self.web_and_server(crt, k)
        line = "vless://%s@127.0.0.1:%d?type=tcp&security=tls&allowInsecure=1#ByIP" % (UUID, vport)
        srv = "127.0.0.1:%d|127.0.0.1" % vport
        with mock.patch.object(self.p, "_TOFU_CA", ca):
            self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("verified", srv, "test.local")])
        tls = tls_of(self.p.vless_outbound(line, "x"))
        self.assertEqual((tls["serverName"], tls["verifyPeerCertByName"]), ("127.0.0.1", "test.local"))
        self.assertNotIn("pinnedPeerCertSha256", tls)
        self.assertEqual(self.client(line, "by-name", ca), "200", "the exit is dead with the name of the certificate")
        self.assertNotEqual(self.client(line, "untrusted", None), "200", "a chain xray does not trust connected")
        self.put({"v": 1, "servers": {srv: {"verified": True, "name": "other.test", "auto": True}}})
        self.assertNotEqual(self.client(line, "other-name", ca), "200", "verifyPeerCertByName is not checked")


# ── the review of the TOFU (3.3.4, R-334-merge §5б) ─────────────────────────

def make_ca_cert(d, name="test.local", san=True):
    """A local CA and a certificate for ``name`` signed by it (subjectAltName; ``san`` False — the CN alone) ->
    (ca, cert, key)."""
    ca, cak = os.path.join(d, "ca.pem"), os.path.join(d, "ca.key")
    crt, k, csr, ext = (os.path.join(d, n) for n in ("leaf.pem", "leaf.key", "leaf.csr", "leaf.ext"))
    q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Kaskad Test CA",
                    "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout", cak, "-out", ca], **q)
    subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + name, "-keyout", k, "-out", csr], **q)
    with open(ext, "w") as f:
        f.write(("subjectAltName=DNS:%s\n" % name if san else "") +
                "basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
    subprocess.run(["openssl", "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", cak, "-CAcreateserial", "-days", "2",
                    "-extfile", ext, "-out", crt], **q)
    return ca, crt, k


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class VerifiedChainTest(TofuCase):
    """A certificate whose chain checks out (Let's Encrypt on the server of a 3x-ui key: IP, sni=domain, allowInsecure=1)
    is not pinned: «verified», xray checks the chain itself — the renewal in 60–90 days does not kill the exit."""

    def setUp(self):
        super().setUp()
        self.ca, crt, k = make_ca_cert(self.d)
        self.srv = TlsServer(crt, k)
        self.addCleanup(self.srv.close)
        m = mock.patch.object(self.p, "_TOFU_CA", self.ca)          # the local CA in place of the system's roots
        m.start()
        self.addCleanup(m.stop)

    def test_chain_ok_verified_without_a_pin(self):
        line = local_key(self.srv.port)
        srv = "127.0.0.1:%d|test.local" % self.srv.port
        self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("verified", srv)])
        rec = self.doc()["servers"][srv]
        self.assertEqual((rec.get("verified"), rec.get("auto"), "pin" in rec), (True, True, False))
        self.assertEqual(self.p.vless_problem(line), "")
        tls = tls_of(self.p.vless_outbound(line, "x"))
        self.assertNotIn("pinnedPeerCertSha256", tls)
        self.assertNotIn("allowInsecure", json.dumps(tls))
        self.assertEqual(tls["serverName"], "test.local")

    def test_self_signed_still_pinned(self):
        crt, k, sha = make_cert(tempfile.mkdtemp(dir=self.d))
        other = TlsServer(crt, k)
        self.addCleanup(other.close)
        line = local_key(other.port)
        self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("pinned", "127.0.0.1:%d|test.local" % other.port)])
        self.assertEqual(tls_of(self.p.vless_outbound(line, "x"))["pinnedPeerCertSha256"], sha)

    def test_another_name_is_checked_by_the_name_of_the_certificate(self):
        """§5в №2: the chain is good, the name is not the SNI (3x-ui: a key by IP with no sni, Let's Encrypt for the
        domain) — not pinned (the renewal would kill it): verified with the name of the certificate, which xray checks
        the chain by (verifyPeerCertByName)."""
        for line, sni in ((local_key(self.srv.port, sni="other.test"), "other.test"),
                          ("vless://%s@127.0.0.1:%d?type=tcp&security=tls&allowInsecure=1#ByIP" % (UUID, self.srv.port),
                           "127.0.0.1")):
            srv = "127.0.0.1:%d|%s" % (self.srv.port, sni)
            self.assertEqual(self.p.tofu_snap([line], first=True, timeout=5), [("verified", srv, "test.local")])
            rec = self.doc()["servers"][srv]
            self.assertEqual((rec["verified"], rec["name"], "pin" in rec), (True, "test.local", False))
            self.assertEqual(self.p.vless_problem(line), "")
            tls = tls_of(self.p.vless_outbound(line, "x"))
            self.assertEqual((tls["serverName"], tls["verifyPeerCertByName"]), (sni, "test.local"))
            self.assertNotIn("pinnedPeerCertSha256", tls)
            self.assertNotIn("allowInsecure", json.dumps(tls))

    def test_no_dns_name_is_pinned(self):
        """A good chain, but the certificate has the CN alone: xray (Go) does not check the CN — the fingerprint."""
        ca, crt, k = make_ca_cert(tempfile.mkdtemp(dir=self.d), "cn.test.local", san=False)
        with mock.patch.object(self.p, "_TOFU_CA", ca):
            other = TlsServer(crt, k)
            self.addCleanup(other.close)
            line = local_key(other.port, sni="other.test")
            out = self.p.tofu_snap([line], first=True, timeout=5)
        self.assertEqual(out, [("pinned", "127.0.0.1:%d|other.test" % other.port)])
        self.assertIn("pinnedPeerCertSha256", tls_of(self.p.vless_outbound(line, "x")))

    def test_the_chain_alone_is_tried_only_on_a_wrong_name(self):
        """§5в-3 №1: self-signed — the name, then the fingerprint (2 handshakes, no «chain» between); the chain good
        and the name wrong (a domain, a key by IP) — the name, then the chain (2)."""
        crt, k, _ = make_cert(tempfile.mkdtemp(dir=self.d))
        selfsigned = TlsServer(crt, k)
        self.addCleanup(selfsigned.close)
        self.assertEqual(self.p.tofu_snap([local_key(selfsigned.port)], first=True, timeout=5)[0][0], "pinned")
        self.assertEqual(len(selfsigned.snis), 2)
        self.assertEqual(self.p.tofu_snap([local_key(self.srv.port, sni="other.test")], first=True, timeout=5)[0][0],
                         "verified")
        self.assertEqual(self.srv.snis, ["other.test"] * 2)
        calls = []
        real = ssl.SSLContext.wrap_socket
        def spy(ctx, *a, **kw):
            if not kw.get("server_side"):                     # the test server wraps its side too
                calls.append((ctx.check_hostname, ctx.verify_mode))
            return real(ctx, *a, **kw)
        by_ip = "vless://%s@127.0.0.1:%d?type=tcp&security=tls&allowInsecure=1#ByIP" % (UUID, self.srv.port)
        with mock.patch.object(ssl.SSLContext, "wrap_socket", spy):
            out = self.p.tofu_snap([by_ip], first=True, timeout=5)
        self.assertEqual(out, [("verified", "127.0.0.1:%d|127.0.0.1" % self.srv.port, "test.local")])
        self.assertEqual(calls, [(True, ssl.CERT_REQUIRED), (False, ssl.CERT_REQUIRED)])    # 64: the IP mismatch

    def test_a_bad_name_in_the_store_pins_nothing(self):
        srv = "198.51.100.21:8443|a.example.com"
        for rec in ({"verified": True, "name": "bad name/"}, {"verified": True, "name": 5}):
            self.put({"v": 1, "servers": {srv: rec}})
            self.assertEqual(self.p.vless_problem(key(1, "&allowInsecure=1")), "insecure")

    def test_cli_says_the_name(self):
        d2 = tempfile.mkdtemp(dir=self.d)
        with open(_PARSER["path"], encoding="utf-8") as f:
            src = f.read().replace("_TOFU_CA = None", "_TOFU_CA = %r" % self.ca, 1)
        with open(os.path.join(d2, "egress_parse.py"), "w", encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run([sys.executable, LOOP, os.path.join(d2, "egress_parse.py"), "tofu", "--first", "--timeout", "5"],
                           input=local_key(self.srv.port, sni="other.test") + "\n", stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.stdout, "verified\t127.0.0.1:%d\ttest.local\n" % self.srv.port, r.stderr)

    def test_cli_says_verified(self):
        d2 = tempfile.mkdtemp(dir=self.d)
        with open(_PARSER["path"], encoding="utf-8") as f:
            src = f.read().replace("_TOFU_CA = None", "_TOFU_CA = %r" % self.ca, 1)
        with open(os.path.join(d2, "egress_parse.py"), "w", encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run([sys.executable, LOOP, os.path.join(d2, "egress_parse.py"), "tofu", "--first", "--timeout", "5"],
                           input=local_key(self.srv.port) + "\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.stdout, "verified\t127.0.0.1:%d\n" % self.srv.port, r.stderr)


class SlowTlsServer(TlsServer):
    """Answers each handshake ``delay`` seconds late (a slow TLS on a far server)."""

    def __init__(self, crt, k, delay):
        self.delay = delay
        super().__init__(crt, k)

    def _run(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        try:
            time.sleep(self.delay)
            c.settimeout(5)
            with self.ctx.wrap_socket(c, server_side=True) as t:
                t.recv(1)
        except (OSError, ssl.SSLError):
            pass
        finally:
            c.close()


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class SlowServerTest(TofuCase):
    def test_a_slow_self_signed_server_is_pinned(self):
        """§5в-3 №1: a handshake of 0.85×timeout each. Self-signed — two handshakes (the name, the fingerprint), not
        three: it fits in the wait of tofu_snap (it was «failed» with three in 2×timeout+2)."""
        crt, k, sha = make_cert(self.d)
        srv = SlowTlsServer(crt, k, 3.4)
        self.addCleanup(srv.close)
        line = local_key(srv.port)
        t0 = time.time()
        out = self.p.tofu_snap([line], first=True, timeout=4)
        self.assertEqual(out, [("pinned", "127.0.0.1:%d|test.local" % srv.port)], "%.1fs" % (time.time() - t0))
        self.assertEqual(self.doc()["servers"]["127.0.0.1:%d|test.local" % srv.port]["pin"], sha)
        self.assertEqual(len(srv.snis), 2)


class RetryTest(TofuCase):
    def test_retries_stop_after_seven_days(self):
        dead = local_key(free_port(), name="Dead")
        srv = "127.0.0.1:%d|test.local" % int(dead.split(":")[2].split("?")[0])
        fail = lambda *a: None
        self.assertEqual(self.p.tofu_snap([dead], first=True, now=1000, fetch=fail), [("failed", srv)])
        day = 86400
        self.assertEqual(self.p.tofu_snap([dead], now=1000 + 6 * day, fetch=fail), [("failed", srv)])
        self.assertEqual(self.doc()["pending"][srv]["since"], 1000)
        self.assertEqual(self.p.tofu_snap([dead], now=1000 + 7 * day + 1, fetch=fail), [("gave_up", srv)])
        self.assertNotIn("pending", self.doc())
        self.assertEqual(self.p.vless_problem(dead), "insecure")             # the warning: update the key
        self.assertEqual(self.p.tofu_snap([dead], now=1000 + 30 * day, fetch=self.no_fetch), [])

    def no_fetch(self, *a):
        raise AssertionError("no handshake expected")

    def test_broken_try_and_n_are_no_crash(self):
        srv = "198.51.100.21:8443|a.example.com"
        self.put({"v": 1, "servers": {}, "pending": {srv: {"try": "x", "n": None, "since": "?"}}})
        out = self.p.tofu_snap([key(1, "&allowInsecure=1")], now=5000, fetch=lambda *a: None)
        self.assertEqual(out, [("failed", srv)])
        self.assertEqual(self.doc()["pending"][srv], {"try": 5000, "n": 1, "since": 5000})

    def test_broken_servers_pin_nothing(self):
        """§5в №5: «servers» of the wrong type (a hand edit, a broken copy) — the key is «insecure», never good."""
        line = key(1, "&allowInsecure=1")
        for servers in (["198.51.100.21:8443|a.example.com"], "x", 5):
            self.put({"v": 1, "servers": servers})
            self.assertEqual(self.p.vless_problem(line), "insecure", servers)
            good, skipped = self.p.extract_report(line + "\n" + VLESS_B)
            self.assertEqual((good, [x["code"] for x in skipped]), ([VLESS_B], ["insecure"]))
        self.put({"v": 1, "servers": {}, "pending": ["198.51.100.21:8443|a.example.com"]})
        self.assertEqual(self.p.vless_problem(line), "insecure")
        self.assertEqual(self.p.tofu_snap([line], now=5000, fetch=self.no_fetch), [])    # not pending: not tried

    def test_prune_drops_the_pending_of_a_key_gone(self):
        """§5в №5: a waited key removed from the pool leaves «pending» (no retries for it; a key of its server that
        comes later is «insecure», not «waits»); the time of the last try is kept."""
        gone, kept = key(1, "&allowInsecure=1"), local_key(free_port(), name="Kept")
        s1 = "198.51.100.21:8443|a.example.com"
        s2 = "127.0.0.1:%s|test.local" % kept.split(":")[2].split("?")[0]
        rec = {"try": 1, "n": 1, "since": 1}
        self.put({"v": 1, "servers": {}, "pending": {s1: rec, s2: rec}})
        t = time.time() - 7200
        os.utime(self.store, (t, t))
        self.assertEqual(self.p.vless_problem(gone), "tofu_wait")
        self.assertEqual(self.p.tofu_prune([kept, VLESS_B]), 1)
        self.p._tofu_cache.clear()
        self.assertEqual(list(self.doc()["pending"]), [s2])
        self.assertAlmostEqual(os.stat(self.store).st_mtime, t, delta=1)
        self.assertEqual(stat.S_IMODE(os.stat(self.store).st_mode), 0o600)
        self.assertEqual(self.p.vless_problem(gone), "insecure")
        self.assertEqual(self.p.tofu_prune([kept]), 0)
        self.assertEqual(self.p.tofu_prune([VLESS_B]), 1)
        self.assertNotIn("pending", self.doc())

    def test_extract_cli_names_the_waited_key(self):
        """§5в №5: the menu's import and add say «the server did not answer» of a waited key (code tofu_wait); the
        report of the gateway keeps «insecure» with «wait»."""
        d2 = tempfile.mkdtemp(dir=self.d)
        shutil.copy(_PARSER["path"], os.path.join(d2, "egress_parse.py"))
        with open(os.path.join(d2, "tofu-pins.json"), "w") as f:
            json.dump({"v": 1, "servers": {}, "pending": {"198.51.100.21:8443|a.example.com": {"try": 1}}}, f)
        r = subprocess.run([sys.executable, "-I", os.path.join(d2, "egress_parse.py"), "extract"],
                           input=key(1, "&allowInsecure=1", "Wait") + "\n" + key(2, "&allowInsecure=1", "New") + "\n",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual((r.stdout, r.stderr), ("", "K3SKIP\ttofu_wait\tWait\nK3SKIP\tinsecure\tNew\n"))

    def test_extract_report_marks_the_waited_key(self):
        srv = "198.51.100.21:8443|a.example.com"
        self.put({"v": 1, "servers": {}, "pending": {srv: {"try": 1, "n": 1, "since": 1}}})
        line, new = key(1, "&allowInsecure=1"), key(2, "&allowInsecure=1")
        good, skipped = self.p.extract_report(line + "\n" + new + "\n" + VLESS_B)
        self.assertEqual(good, [VLESS_B])
        self.assertEqual([(x["code"], x.get("wait", False)) for x in skipped], [("insecure", True), ("insecure", False)])


class SubscriptionKeepsTheWaitedKeyTest(SubsCase):
    """A key of a subscription whose server did not answer on the update stays in the pool on the next refresh (and the
    one after): the parser skips it as insecure, the subscription keeps it as seen — its pending is not dropped."""

    def setUp(self):
        super().setUp()
        from kaskad import paths
        self.store = os.path.join(os.path.dirname(paths.egress_parser()), "tofu-pins.json")

    def put(self, doc):
        with open(self.store, "w") as f:
            json.dump(doc, f)
        exits.load_parser(self.w.runner)._tofu_cache.clear()

    def test_refresh_keeps_it_and_its_pending(self):
        from tests.fakes_gw import write_pool
        from kaskad.gw import exitsubs
        bad, good = key(1, "&allowInsecure=1", "Down"), key(3, "&pcs=" + PIN)
        srv = "198.51.100.21:8443|a.example.com"
        write_pool([bad, good])                                      # 3.3.3: the subscription put both into the pool
        exitsubs.save({"next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": IP_URL, "auto": True, "pin": "",
                                            "keys": [exits.exit_id(bad), exits.exit_id(good)],
                                            "seen": [exits.exit_id(bad), exits.exit_id(good)], "added": "x",
                                            "last_ok": 1, "last_try": 1, "error": "", "count": 2, "full": 0}]})
        now = int(time.time())
        self.put({"v": 1, "servers": {}, "pending": {srv: {"try": now - 7200, "n": 1, "since": now - 7200}}})
        self.feed.bodies[IP_URL] = b64([bad, good])
        for _ in range(2):
            r = self.refresh()["results"][0]
            self.assertTrue(r["ok"], r)
            self.assertEqual(self.pool(), [bad, good])
            self.assertEqual(r["removed"], 0)
            self.assertEqual(r["skipped"], 0)
            self.assertIn(exits.exit_id(bad), exitsubs.load()["subs"][0]["keys"])
        p = exits.load_parser(self.w.runner)
        self.assertEqual(p.tofu_snap(self.pool(), now=now, fetch=lambda *a: None), [("failed", srv)])
        self.assertIn(srv, self.doc()["pending"])
        es = exits.pool(self.w.runner)
        self.assertEqual(es[0]["problem_code"], "problem.vless_tofu_wait")
        self.assertIn("повторит сам", es[0]["problem"])

    def doc(self):
        with open(self.store) as f:
            return json.load(f)

    def test_a_new_name_of_the_waited_key_keeps_its_line(self):
        """§5в №1: 3x-ui writes the traffic left or the term into the name — the line of the waited key changes on
        each refresh. It is matched to the pool by its server (peer_key), not by the whole line: the line of the pool
        is kept as it is (its id, its routes) and seen, its pending stays."""
        from tests.fakes_gw import write_pool
        from kaskad.gw import exitsubs
        old, good = key(1, "&allowInsecure=1", "Down-10GB-left"), key(3, "&pcs=" + PIN)
        srv = "198.51.100.21:8443|a.example.com"
        write_pool([old, good])
        ids = [exits.exit_id(old), exits.exit_id(good)]
        exitsubs.save({"next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": IP_URL, "auto": True, "pin": "",
                                            "keys": list(ids), "seen": list(ids), "added": "x", "last_ok": 1,
                                            "last_try": 1, "error": "", "count": 2, "full": 0}]})
        now = int(time.time())
        self.put({"v": 1, "servers": {}, "pending": {srv: {"try": now - 7200, "n": 1, "since": now - 7200}}})
        for name in ("Down-9GB-left", "Down-8GB-left"):
            self.feed.bodies[IP_URL] = b64([key(1, "&allowInsecure=1", name), good])
            r = self.refresh()["results"][0]
            self.assertTrue(r["ok"], r)
            self.assertEqual(self.pool(), [old, good])
            self.assertEqual((r["removed"], r["skipped"]), (0, 0))
            sub = exitsubs.load()["subs"][0]
            self.assertEqual((sorted(sub["keys"]), sorted(sub["seen"])), (sorted(ids), sorted(ids)))
        p = exits.load_parser(self.w.runner)
        self.assertEqual(p.tofu_snap(self.pool(), now=now, fetch=lambda *a: None), [("failed", srv)])
        self.assertIn(srv, self.doc()["pending"])

    def test_a_new_key_of_a_waited_server_is_not_added(self):
        bad = key(1, "&allowInsecure=1", "Down")
        self.put({"v": 1, "servers": {}, "pending": {"198.51.100.21:8443|a.example.com": {"try": 1, "n": 1, "since": 1}}})
        res = self.add(IP_URL, b64([bad, key(3, "&pcs=" + PIN)]))
        self.assertEqual(self.pool(), [key(3, "&pcs=" + PIN)])
        self.assertEqual(res["result"]["skipped"], 1)
        with self.assertRaises(Exception):
            self.add("http://198.51.100.10/sub/x", b64([bad]))            # only that key: nothing to add, untouched
        self.assertEqual(self.pool(), [key(3, "&pcs=" + PIN)])


class WaitWordsTest(Quiet):
    """tofu_wait has its own words (RU and EN) in the panel and the core; a key being added is «insecure»."""

    def test_panel_marks_and_preview(self):
        from kaskad.gw import ops
        from kaskad.i18n import use_lang
        with use_lang("ru"):
            w = ops._script_warnings(["⚠ Ключ #2: …", "@K3:egress_warn:vless_key:2:tofu_wait"])
            self.assertIn("сервер не ответил при обновлении", w[0])
            self.assertIn("не чаще раза в час", w[0])
        with use_lang("en"):
            w = ops._script_warnings(["@K3:egress_warn:vless_key:2:tofu_wait"])
            self.assertIn("didn’t answer during the update", w[0])
        p = mock.Mock(vless_problem=lambda line: "tofu_wait")
        self.assertEqual(exits._vless_problem(p, "vless://x", "unparsed"), "tofu_wait")
        self.assertEqual(exits._vless_problem(p, "vless://x", "unparsed", wait=False), "insecure")


class OldParserOnDiskTest(SubsCase):
    """Stand T-334 (54b3d65): a node updated from 3.3.3 with no allowInsecure keys kept the parser of 3.3.3 (v14) —
    nothing rewrote it, gw apply built the exit of a key with pcs without its pinned certificate, the exit was dead.
    Now every reader of the pool with a runner (gw apply, the collector, subscriptions) asks
    ``gokaskad --egress-ensure-parser`` once for a file older than PARSER_MIN. The bash side (--k3-post-update writes
    the parser first) — tools/k3_install_test.sh «парсер пула 3.3.3»."""

    def setUp(self):
        super().setUp()
        exits._refreshed.clear()
        self.addCleanup(exits._refreshed.clear)
        self.line = key(3, "&pcs=" + PIN, "Pinned")
        write_pool([self.line])

    def old_parser(self):
        """The parser as 3.3.3 left it: PARSER_VERSION 14, pcs unknown (no pinnedPeerCertSha256)."""
        p = install_parser()
        with open(p, encoding="utf-8") as f:
            text = f.read()
        text = re.sub(r"^PARSER_VERSION = \d+", "PARSER_VERSION = 14", text, count=1, flags=re.M)
        text += ("\n_vo_new = vless_outbound\n"
                 "def vless_outbound(*a, **kw):\n"
                 "    ob = _vo_new(*a, **kw)\n"
                 "    (ob.get('streamSettings') or {}).get('tlsSettings', {}).pop('pinnedPeerCertSha256', None)\n"
                 "    return ob\n")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(p, (1, 1))
        return p

    def ensure_calls(self):
        return [c for c in self.w.script.calls if c == ["--egress-ensure-parser"]]

    def test_gateway_rewrites_an_old_parser_and_pins(self):
        self.old_parser()
        self.w.script.calls.clear()
        es = exits.pool(self.w.runner)
        self.assertEqual(len(self.ensure_calls()), 1)
        self.assertGreaterEqual(exits.load_parser(self.w.runner).PARSER_VERSION, exits.PARSER_MIN)
        self.assertEqual(es[0]["kind"], "xray")
        self.assertEqual(tls_of(es[0]["outbound"])["pinnedPeerCertSha256"], PIN)
        exits.pool(self.w.runner)
        self.assertEqual(len(self.ensure_calls()), 1)                     # a current parser — no more calls

    def test_a_parser_that_stays_old_is_asked_once(self):
        self.old_parser()
        self.w.runner._handlers.insert(0, ([paths.script_copy(), "--egress-ensure-parser"],
                                           lambda argv, inp: (self.w.script.calls.append(argv[1:]) or 0, "", "")))
        self.w.script.calls.clear()
        for _ in range(3):                                                # the collector reads the pool every few s
            es = exits.pool(self.w.runner)
        self.assertEqual(len(self.ensure_calls()), 1)
        self.assertEqual(es[0]["kind"], "xray")                          # still read, with what is on disk

    def test_no_runner_no_call(self):
        self.old_parser()
        self.w.script.calls.clear()
        exits.pool(None)                                                  # the panel and the summary read only
        self.assertEqual(self.ensure_calls(), [])


class CollectorKickTest(unittest.TestCase):
    """The gateway's collector looks once an hour for the pins put off on the update (no restart of the services)."""

    def setUp(self):
        from kaskad import paths
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.tofu, self.done = os.path.join(self.d, "tofu-pins.json"), os.path.join(self.d, ".tofu-done")
        for name, val in (("egress_tofu", self.tofu), ("egress_tofu_done", self.done)):
            m = mock.patch.object(paths, name, lambda v=val: v)
            m.start()
            self.addCleanup(m.stop)
        from kaskad.gw import collect
        self.c = collect
        m = mock.patch.object(collect, "_tofu_at", [0.0])
        m.start()
        self.addCleanup(m.stop)
        self.runner = mock.Mock()

    def write(self, doc, age):
        with open(self.tofu, "w") as f:
            json.dump(doc, f)
        t = time.time() - age
        os.utime(self.tofu, (t, t))

    def test_kick_once_an_hour_when_due(self):
        open(self.done, "w").close()
        self.write({"v": 1, "servers": {}, "pending": {"h:1|s": {"try": 1}}}, 7200)
        now = time.time()
        self.assertTrue(self.c.tofu_kick(self.runner, now))
        self.assertEqual(self.runner.run.call_args[0][0][1], "--egress-tofu-kick")
        self.assertFalse(self.c.tofu_kick(self.runner, now + 60))             # within the hour: not looked at
        self.assertEqual(self.runner.run.call_count, 1)

    def test_nothing_due_nothing_run(self):
        now = time.time()
        self.write({"v": 1, "servers": {}, "pending": {"h:1|s": {"try": 1}}}, 7200)
        self.assertFalse(self.c.tofu_waiting(now))                            # first run not done yet
        open(self.done, "w").close()
        self.write({"v": 1, "servers": {}, "pending": {"h:1|s": {"try": 1}}}, 60)
        self.assertFalse(self.c.tofu_waiting(now))                            # tried less than an hour ago
        self.write({"v": 1, "servers": {"h:1|s": {"pin": "ab" * 32}}}, 7200)
        self.assertFalse(self.c.tofu_kick(self.runner, now))
        self.runner.run.assert_not_called()


from tests.test_backup import PW, BackupCase   # noqa: E402


class BackupTofuTest(BackupCase):
    """The pins taken on the update ride in the copy (servers only, no pending): the import writes them before the pool,
    keeps the pins of this node and does not run the first pass again."""

    def store(self):
        from kaskad import paths
        return paths.egress_tofu(), paths.egress_tofu_done()

    def test_export_and_import(self):
        from kaskad import backup, util
        from tests.fakes_gw import install_parser, write_pool
        install_parser()
        line = key(1, "&allowInsecure=1", "Marzban")
        write_pool([line, VLESS_B])
        tofu, done = self.store()
        mine, theirs = "198.51.100.21:8443|a.example.com", "203.0.113.7:443|b.example.com"
        util.atomic_write(tofu, json.dumps({"v": 1, "servers": {mine: {"pin": PIN, "at": 5, "auto": True},
                                                                  theirs: {"verified": True, "at": 5, "auto": True}},
                                            "pending": {"203.0.113.8:443|c": {"try": 1, "n": 1, "since": 1}}}), 0o600)
        util.atomic_write(done, "", 0o600)
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        got = json.loads(doc["files"][backup.F_TOFU])
        self.assertEqual(sorted(got["servers"]), sorted([mine, theirs]))
        self.assertNotIn("pending", got)
        # a fresh node: no pins, no first pass yet — the copy's pins land before the pool, the first pass is marked done
        os.unlink(tofu)
        os.unlink(done)
        write_pool([])
        backup.apply(self.w.runner, doc)
        self.assertEqual(exits.pool_lines(), [line, VLESS_B])
        with open(tofu) as f:
            here = json.load(f)
        self.assertEqual(here["servers"][mine]["pin"], PIN)
        self.assertTrue(here["servers"][theirs]["verified"])
        self.assertTrue(os.path.exists(done))
        self.assertEqual(stat.S_IMODE(os.stat(tofu).st_mode), 0o600)
        es = exits.pool(self.w.runner)
        self.assertEqual(es[0]["outbound"]["streamSettings"]["tlsSettings"]["pinnedPeerCertSha256"], PIN)
        # a node with its own pin of that server: kept; its pending of a server the copy pinned — gone
        own = "cd" * 32
        util.atomic_write(tofu, json.dumps({"v": 1, "servers": {mine: {"pin": own, "auto": True}},
                                            "pending": {theirs: {"try": 1, "n": 1, "since": 1}}}), 0o600)
        backup.apply(self.w.runner, doc)
        with open(tofu) as f:
            here = json.load(f)
        self.assertEqual(here["servers"][mine]["pin"], own)
        self.assertTrue(here["servers"][theirs]["verified"])
        self.assertNotIn("pending", here)

    def test_no_pins_no_file_in_the_copy(self):
        """§5в №3: 3.3.3 refuses a copy with a file it does not know — the file rides only when there are pins (most
        nodes have none: the first pass done, nothing pinned, or only «pending»)."""
        from kaskad import backup, util
        from tests.fakes_gw import install_parser, write_pool
        install_parser()
        write_pool([VLESS_B])
        tofu, done = self.store()
        util.atomic_write(done, "", 0o600)
        for doc in ({"v": 1, "servers": {}}, {"v": 1, "servers": {}, "pending": {"203.0.113.8:443|c": {"try": 1}}},
                    {"v": 1, "servers": {"203.0.113.8:443|c": {"pin": "bad"}}}, None):
            if doc is None:
                if os.path.exists(tofu):
                    os.unlink(tofu)
            else:
                util.atomic_write(tofu, json.dumps(doc), 0o600)
            got = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
            self.assertNotIn(backup.F_TOFU, got["files"], doc)
        util.atomic_write(tofu, json.dumps({"v": 1, "servers": {"203.0.113.7:443|b.example.com": {
            "verified": True, "name": "b.example.com", "at": 5, "auto": True}}}), 0o600)
        got = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.assertEqual(json.loads(got["files"][backup.F_TOFU])["servers"],
                         {"203.0.113.7:443|b.example.com": {"verified": True, "name": "b.example.com", "at": 5,
                                                            "auto": True}})
        self.assertEqual(backup._tofu_servers({"servers": {"203.0.113.7:443|b": {"verified": True, "name": "a b"}}}),
                         {})

    def test_no_first_pass_no_file_in_the_copy(self):
        from kaskad import backup
        from tests.fakes_gw import install_parser, write_pool
        install_parser()
        write_pool([VLESS_B])
        for p in self.store():
            if os.path.exists(p):
                os.unlink(p)
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.assertNotIn(backup.F_TOFU, doc["files"])
        backup.apply(self.w.runner, doc)
        self.assertFalse(os.path.exists(self.store()[1]))                     # an old copy: the first pass will run


    def test_an_old_copy_on_a_working_node_keeps_the_key_refused(self):
        """§5в-3 №2: a copy without the file (before 3.3.4) on a node that has done its first pass: nothing is pinned
        here, the key with allowInsecure stays refused — it is to be issued again with pcs (README)."""
        from kaskad import backup, util
        from tests.fakes_gw import install_parser, write_pool
        install_parser()
        line = key(1, "&allowInsecure=1", "Old")
        write_pool([line, VLESS_B])
        tofu, done = self.store()
        for p in (tofu, done):
            if os.path.exists(p):
                os.unlink(p)
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.assertNotIn(backup.F_TOFU, doc["files"])
        write_pool([])
        util.atomic_write(done, "", 0o600)                                    # a working node of 3.3.4
        backup.apply(self.w.runner, doc)
        self.assertTrue(os.path.exists(done))
        self.assertFalse(os.path.exists(tofu))                                # nothing pinned by the import
        self.assertEqual(exits.pool_lines(), [line, VLESS_B])
        es = exits.pool(self.w.runner)
        self.assertIsNone(es[0].get("outbound"))                              # the key is not built: refused
        self.assertIsNotNone(es[1].get("outbound"))


if __name__ == "__main__":
    unittest.main()


from tests import test_gw_ops as _gw_ops   # noqa: E402 — the module, not its classes: their tests are not run twice


class CollectorTickKicksTest(_gw_ops.CliGwCase):
    """§5в №4: a round of the gateway's collector (tick) asks for the pins put off on the update once an hour."""

    def test_tick_calls_the_kick(self):
        from kaskad import paths
        from kaskad.gw import collect
        self.enabled_world()
        with open(paths.egress_tofu(), "w") as f:
            json.dump({"v": 1, "servers": {}, "pending": {"198.51.100.21:8443|a.example.com": {"try": 1}}}, f)
        open(paths.egress_tofu_done(), "w").close()
        t = collect._now() - 7200                                              # the clock of the world
        os.utime(paths.egress_tofu(), (t, t))
        kicks = lambda: [c for c in self.w.runner.calls if "--egress-tofu-kick" in c[0]]
        with mock.patch.object(collect, "_tofu_at", [0.0]):
            self.w.runner.calls = []
            self.assertTrue(collect.tick(self.w.runner)["enabled"])
            self.assertEqual(len(kicks()), 1, self.w.runner.calls)
            collect.tick(self.w.runner)                                         # the same hour: not again
            self.assertEqual(len(kicks()), 1)
