"""Stage 1, T11 (SPEC-stage1-exits §9.1): TOFU of a NEW key with consent — gw/tofu.py, in tofu-pins.json of 3.3.4. A
certificate whose chain checks out — «verified» without a question; a self-signed one — pinned only with consent and only
when the second look shows the same fingerprint; the pool line and the id of the exit do not change; the record is seen
by _tls_check without a restart; a subscription without a record skips the key (insecure), with a record takes it;
Hysteria2 — no TOFU; nothing of the key or the fingerprint in the journal."""

import hashlib
import ipaddress
import json
import os
import ssl
import shutil
import tempfile
import unittest
from unittest import mock

from kaskad import paths, util
from kaskad.errors import KaskadError
from kaskad.gw import exits, tofu
from tests.helpers import RootCase
from tests.test_334_tofu import TlsServer, TofuCase, make_ca_cert, make_cert

UUID = "11111111-2222-3333-4444-000000000077"


def trojan(port, extra="&allowInsecure=1", name="TJ"):
    return "trojan://tjSecretT@127.0.0.1:%d?security=tls&sni=test.local&type=tcp%s#%s" % (port, extra, name)


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class ConsentTest(TofuCase):
    def setUp(self):
        super().setUp()
        self.ca, crt, k = make_ca_cert(self.d)
        self.le = TlsServer(crt, k)                                      # «Let's Encrypt»: the chain checks out
        self.addCleanup(self.le.close)
        crt2, k2, self.sha = make_cert(tempfile.mkdtemp(dir=self.d))
        self.self_signed = TlsServer(crt2, k2)
        self.addCleanup(self.self_signed.close)
        self.lock = os.path.join(self.d, "egress-pool.lock")
        self.log = []
        for m in (mock.patch.object(self.p, "_TOFU_CA", self.ca), mock.patch.object(tofu, "_allowed", lambda a, node=(): True),
                  mock.patch.object(tofu, "lock_path", lambda: self.lock),
                  mock.patch.object(util, "log_action", self.log.append)):
            m.start()
            self.addCleanup(m.stop)

    def test_chain_checks_out_verified_without_a_question(self):
        line = trojan(self.le.port)
        self.assertEqual(self.p.link_problem(line), "insecure")
        r = tofu.check(self.p, line)
        self.assertEqual(r, {"status": "verified", "server": "127.0.0.1:%d" % self.le.port})
        rec = self.doc()["servers"]["127.0.0.1:%d|test.local" % self.le.port]
        self.assertEqual((rec["verified"], rec["auto"], rec["by"], "pin" in rec), (True, False, "panel", False))
        self.assertEqual(self.p.link_problem(line), "")                  # _tls_check sees it at once
        self.assertNotIn("pinnedPeerCertSha256", json.dumps(self.p.xray_outbound(line, "t", 0)))
        self.assertIsNone(tofu.check(self.p, line))                      # nothing to ask any more

    def test_self_signed_only_with_consent_and_the_same_fingerprint(self):
        line = trojan(self.self_signed.port)
        r = tofu.check(self.p, line)
        self.assertEqual((r["status"], r["sha256"]), ("ask", self.sha))
        self.assertFalse(os.path.exists(self.store))                     # nothing written without consent
        self.assertEqual(self.p.link_problem(line), "insecure")
        with self.assertRaises(KaskadError) as cm:
            tofu.consent(self.p, line, "0" * 64, "cli")                  # another fingerprint: the certificate changed
        self.assertEqual(cm.exception.data["code"], "bad_pin")
        self.assertFalse(os.path.exists(self.store))
        self.assertEqual(tofu.consent(self.p, line, self.sha.upper(), "mcp")["status"], "pinned")
        rec = self.doc()["servers"]["127.0.0.1:%d|test.local" % self.self_signed.port]
        self.assertEqual((rec["pin"], rec["auto"], rec["by"]), (self.sha, False, "mcp"))
        ob = self.p.xray_outbound(line, "t", 0)
        self.assertEqual(ob["streamSettings"]["tlsSettings"]["pinnedPeerCertSha256"], self.sha)
        self.assertEqual(self.p.link_problem(line), "")
        renamed = line.replace("#TJ", "#TJ%20new")                       # 3.3.5 rename: the same server, the same pin
        self.assertEqual(self.p.link_problem(renamed), "")
        text = " ".join(self.log)
        self.assertNotIn(self.sha, text)
        self.assertNotIn("tjSecretT", text)

    def test_another_name_only_with_consent(self):
        """REV-s1-t11 С4 (§9.1): the chain checks out, but the certificate is for another name — asked like a
        self-signed one, with the name; nothing written without consent; the consent trusts that name."""
        d = tempfile.mkdtemp(dir=self.d)
        ca, crt, k = make_ca_cert(d, name="other.example")
        with open(crt) as f:
            sha = hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()
        srv = TlsServer(crt, k)
        self.addCleanup(srv.close)
        m = mock.patch.object(self.p, "_TOFU_CA", ca)
        m.start()
        self.addCleanup(m.stop)
        line = trojan(srv.port)
        r = tofu.check(self.p, line)
        self.assertEqual(r, {"status": "ask", "server": "127.0.0.1:%d" % srv.port, "sha256": sha,
                             "name": "other.example"})
        self.assertFalse(os.path.exists(self.store))                     # never trusted silently
        self.assertEqual(self.p.link_problem(line), "insecure")
        with self.assertRaises(KaskadError) as cm:
            tofu.consent(self.p, line, "1" * 64, "panel")
        self.assertEqual(cm.exception.data["code"], "bad_pin")
        self.assertEqual(tofu.consent(self.p, line, sha, "cli")["status"], "pinned")
        rec = self.doc()["servers"]["127.0.0.1:%d|test.local" % srv.port]
        self.assertEqual((rec["verified"], rec["name"], rec["auto"], rec["by"], "pin" in rec),
                         (True, "other.example", False, "cli", False))
        self.assertEqual(self.p.link_problem(line), "")
        tls = self.p.xray_outbound(line, "t", 0)["streamSettings"]["tlsSettings"]
        self.assertEqual(tls.get("verifyPeerCertByName"), "other.example")

    def test_vless_and_vmess_too_hysteria2_never(self):
        v = "vless://%s@127.0.0.1:%d?type=tcp&security=tls&sni=test.local&allowInsecure=1#V" % (UUID, self.self_signed.port)
        self.assertEqual(tofu.check(self.p, v)["status"], "ask")
        h = "hy2://auth@127.0.0.1:%d?sni=test.local&insecure=1#H" % self.self_signed.port
        self.assertEqual(tofu.check(self.p, h), {"status": "refused", "code": "insecure"})
        own = trojan(self.self_signed.port, "&pcs=" + self.sha)          # its own pin: the store is not read
        self.assertIsNone(tofu.check(self.p, own))

    def test_preview_asks_only_when_told(self):
        line = trojan(self.self_signed.port)
        row = exits.preview(self.p, line)[0]
        self.assertNotIn("tofu", row)                                     # no network without tofu_by
        self.assertIn("Trojan", row["problem"])
        row = exits.preview(self.p, line, tofu_by="panel")[0]
        self.assertEqual((row["tofu"]["status"], row["tofu"]["sha256"]), ("ask", self.sha))
        row = exits.preview(self.p, trojan(self.le.port), tofu_by="cli")[0]
        self.assertEqual(row["tofu"]["status"], "verified")
        self.assertNotIn("problem", row)

    def test_no_answer_is_an_honest_refusal(self):
        self.self_signed.close()
        r = tofu.check(self.p, trojan(self.self_signed.port))
        self.assertEqual(r["status"], "failed")
        with self.assertRaises(KaskadError) as cm:
            tofu.consent(self.p, trojan(self.self_signed.port), self.sha, "panel")
        self.assertEqual(cm.exception.data["code"], "failed")

    def test_subscription_skips_without_a_record_takes_with_one(self):
        line = trojan(self.self_signed.port)
        good, skipped = self.p.extract_report(line)
        self.assertEqual((good, [x["code"] for x in skipped]), ([], ["insecure"]))
        tofu.consent(self.p, line, self.sha, "panel")
        self.assertEqual(self.p.extract_report(line), ([line], []))


class GlobalOnlyTest(unittest.TestCase):
    def test_private_and_loopback_are_not_probed(self):
        for h in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "[::1]"):
            self.assertFalse(tofu._global(h), h)
        self.assertTrue(tofu._global("8.8.8.8"))

    def probe(self, addrs, node=()):
        """tofu._probe with the name «srv.example.com» resolving to ``addrs`` -> (result or the error, lookups, the
        address the handshake went to)."""
        calls, seen = [], []

        def resolve(host, port):
            calls.append(host)
            return [(0, 0, 0, "", (a, 0)) for a in addrs]

        class P(object):
            def _cert_probe(self, host, port, sni, timeout, alpn=None, sha=False):
                seen.append((host, sni, sha))
                return "a" * 64
        t = ("srv.example.com:443|srv.example.com", "srv.example.com", 443, "srv.example.com", [])
        with mock.patch.object(tofu, "_resolve", resolve):
            try:
                got = tofu._probe(P(), t, node)
            except OSError as e:
                got = e
        return got, calls, seen

    def test_the_probe_checks_every_address_once_and_dials_the_one_checked(self):
        """REV-s1-t11 З2: one lookup; the handshake goes to the checked address (IPv4 first), not to the name again."""
        got, calls, seen = self.probe(["2001:4860:4860::8888", "8.8.4.4"])
        self.assertEqual(got, "a" * 64)
        self.assertEqual(calls, ["srv.example.com"])
        self.assertEqual(seen, [("8.8.4.4", "srv.example.com", True)])

    def test_a_private_address_or_one_of_the_node_is_never_probed(self):
        """REV-s1-t11 З2 (M25): without the check of the address the probe would go there."""
        for addrs in (["127.0.0.1"], ["8.8.4.4", "10.1.2.3"], ["192.0.2.10"]):
            got, _, seen = self.probe(addrs)
            self.assertIsInstance(got, OSError, addrs)
            self.assertEqual(seen, [], addrs)
        node = (ipaddress.ip_network("8.8.4.4/32"),)
        got, _, seen = self.probe(["8.8.4.4"], node)
        self.assertIsInstance(got, OSError)
        self.assertEqual(seen, [])


class SelfEpSecondLineTest(TofuCase):
    """REV-s1-rel-3 П-1: TOFU with consent never probes this node — the second line besides tofu._probe: the server of
    the key goes through the parser's own _host and _server_problem (as tofu_snap), the probe gets the checked host."""

    def setUp(self):
        super().setUp()
        self.seen = []
        m = mock.patch.object(tofu, "_probe", lambda parser, t, node=(): self.seen.append(t) or "a" * 64)
        m.start()
        self.addCleanup(m.stop)
        m = mock.patch.object(tofu, "lock_path", lambda d=self.d: os.path.join(d, "lock"))
        m.start()
        self.addCleanup(m.stop)

    def key(self, host):
        return "trojan://tjSecretT@%s:8443?security=tls&sni=t.example.com&allowInsecure=1#T" % host

    def test_this_node_is_not_probed_even_if_the_probe_would_let_it(self):
        for host in ("localhost", "localhost.", "LOCALHOST\u3002", "169.254.169.254", "[::1]", "0x7f000001"):
            with self.subTest(host=host):
                self.assertIsNone(tofu.check(self.p, self.key(host)), host)
                with self.assertRaises(KaskadError):
                    tofu.consent(self.p, self.key(host), "a" * 64, "panel")
        self.assertEqual(self.seen, [])

    def test_the_probe_gets_the_checked_host(self):
        r = tofu.check(self.p, self.key("SRV.Example.com."))
        self.assertEqual(r["status"], "ask")
        self.assertEqual(self.seen[0][1], "srv.example.com")


class IdKeptTest(RootCase):
    def test_pool_line_and_id_unchanged(self):
        """The record is by server: the consent writes tofu-pins.json, the line of the pool and the id of its exit
        stay as they are (REV-s1-t11 З5: the consent is really made here)."""
        from tests.fakes_gw import install_parser, write_pool
        install_parser()
        p = exits.load_parser(None)
        line = trojan(8443)
        write_pool([line])
        with open(paths.egress_up(), "rb") as f:
            raw = f.read()
        before = [i for i, _ in exits.line_ids()]
        sha = "b" * 64
        lock = os.path.join(self.root, "pool.lock")
        from tests.parser_loopback import allow_loopback
        with mock.patch.object(tofu, "_probe", lambda parser, t, node=(): sha), \
                mock.patch.object(p, "_server_problem", allow_loopback(p)), \
                mock.patch.object(tofu, "lock_path", lambda: lock), mock.patch.object(util, "log_action", lambda *a: None):
            self.assertEqual(tofu.consent(p, line, sha, "cli")["status"], "pinned")
        with open(paths.egress_up(), "rb") as f:
            self.assertEqual(f.read(), raw)
        self.assertEqual([i for i, _ in exits.line_ids()], before)
        with open(paths.egress_tofu()) as f:
            self.assertEqual(json.load(f)["servers"]["127.0.0.1:8443|test.local"]["pin"], sha)

    def test_a_copy_kbk_carries_the_record(self):
        """§9.1 (REV-s1-t11 З5): backup.F_TOFU carries the records of consent — the fingerprint and the name, «auto»
        false; a node restored from the copy has them."""
        from kaskad import backup
        recs = {"198.51.100.21:8443|a.example.com": {"pin": "c" * 64, "at": 5, "auto": False, "by": "cli"},
                "203.0.113.7:443|b.example.com": {"verified": True, "name": "other.example", "at": 6, "auto": False,
                                                  "by": "panel"}}
        os.makedirs(paths.egress_dir(), exist_ok=True)
        util.atomic_write(paths.egress_tofu(), json.dumps({"v": 1, "servers": recs}), 0o600)
        text = backup._tofu_export()
        os.unlink(paths.egress_tofu())
        backup._tofu_import(text)
        with open(paths.egress_tofu()) as f:
            got = json.load(f)["servers"]
        self.assertEqual(got["198.51.100.21:8443|a.example.com"], {"pin": "c" * 64, "at": 5, "auto": False})
        self.assertEqual(got["203.0.113.7:443|b.example.com"],
                         {"verified": True, "name": "other.example", "at": 6, "auto": False})


if __name__ == "__main__":
    unittest.main()
