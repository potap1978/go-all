"""The open mode with a domain certificate chooses the certificate by SNI (T-M6-qs330 №2): the domain's name gets the
domain's certificate, no name or an IP address — the IP certificate while it is valid; without one — the domain's for
everybody, as before. Unit: ``sni_pick`` / ``sni_callback`` / ``conf.sni_ip_cert``; live: real TLS handshakes on
127.0.0.1 against the running panel (the pages of people /p/… on the same listener), renewal picked up without a
restart; the IP certificate keeps renewing next to the domain's (``ipcert.ops.needed``)."""

import hashlib
import os
import socket
import ssl
import unittest
from unittest import mock

from kaskad.ipcert import ops as ipops
from kaskad.panel import conf, server
from tests.netports import free_port
from tests.panel.panelcase import PanelCase

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "site")
DOMAIN = "vpn.example.com"


def site_fixture(name):
    with open(os.path.join(SITE, name), encoding="utf-8") as f:
        return f.read()


def leaf_sha(pem):
    """SHA-256 of the first certificate of a PEM chain (what a client sees as the peer certificate)."""
    der = ssl.PEM_cert_to_DER_cert(pem.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
    return hashlib.sha256(der).hexdigest()


class PickTest(unittest.TestCase):
    def test_pick_by_name(self):
        for name in (None, "", "127.0.0.1", "198.51.100.53", "2001:db8::1", "[2001:db8::1]"):
            self.assertEqual(server.sni_pick(name), "ip", name)
        for name in (DOMAIN, "other.example.org", "localhost", "1.2.3.4.nip.io"):
            self.assertEqual(server.sni_pick(name), "name", name)

    def test_callback_switches_only_for_an_address(self):
        ip_ctx = object()
        cb = server.sni_callback(ip_ctx)

        class Sock(object):
            context = "domain"
        for name, want in ((None, ip_ctx), ("203.0.113.7", ip_ctx), (DOMAIN, "domain")):
            s = Sock()
            self.assertIsNone(cb(s, name, None))
            self.assertIs(s.context, want, name)

    def test_callback_never_raises(self):
        class Bad(object):
            @property
            def context(self):
                return None

            @context.setter
            def context(self, v):
                raise ValueError("нет")
        self.assertIsNone(server.sni_callback(object())(Bad(), None, None))

    def test_sni_ip_cert_only_next_to_a_domain(self):
        pair = ("IPCRT", "IPKEY")
        with mock.patch.object(conf, "ip_cert", return_value=pair):
            with mock.patch.object(conf, "site_cert", return_value=None):
                self.assertIsNone(conf.sni_ip_cert())             # no domain: panel_cert is the IP one already
            with mock.patch.object(conf, "site_cert", return_value=(DOMAIN, "D", "K")):
                self.assertEqual(conf.sni_ip_cert(), pair)
        with mock.patch.object(conf, "ip_cert", return_value=None), \
                mock.patch.object(conf, "site_cert", return_value=(DOMAIN, "D", "K")):
            self.assertIsNone(conf.sni_ip_cert())                 # no valid IP certificate: the domain's, as before


class LiveBase(PanelCase):
    """A real panel, real handshakes: le.pem plays the domain's certificate, other.pem the IP one."""

    def setUp(self):
        super().setUp()
        self.domain = (DOMAIN, site_fixture("le.pem"), site_fixture("le.key"))
        self.ip = (site_fixture("other.pem"), site_fixture("other.key"))
        self.p_site = mock.patch.object(conf, "site_cert", side_effect=lambda: self.domain)
        self.p_ip = mock.patch.object(conf, "ip_cert", side_effect=lambda cfg=None: self.ip)
        self.p_site.start()
        self.p_ip.start()
        self.addCleanup(self.p_ip.stop)
        self.addCleanup(self.p_site.stop)
        self.port = self.public_on()

    def fresh(self):
        """Files changed (renewal): the next connection re-reads them — the 5-s check forced."""
        self.panel._tls_checked = 0.0
        self.panel._tls_sig = None

    def peer(self, sni, path=None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            with ctx.wrap_socket(s, server_hostname=sni) as tls:
                der = tls.getpeercert(binary_form=True)
                status = None
                if path:
                    tls.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                                 % (path, self.port)).encode("ascii"))
                    status = tls.recv(64).split(b"\r\n")[0]
        return hashlib.sha256(der).hexdigest(), status


class LiveTest(LiveBase):
    def test_domain_name_gets_the_domain_ip_or_no_name_gets_the_ip_one(self):
        dom, ip = leaf_sha(self.domain[1]), leaf_sha(self.ip[0])
        self.assertNotEqual(dom, ip)
        self.assertEqual(self.peer(DOMAIN)[0], dom)
        self.assertEqual(self.peer(None)[0], ip)                   # a browser on https://<IP>:<порт>/ sends no SNI
        self.assertEqual(self.peer("other.example.org")[0], dom)   # any other name: the main one, as before
        # the fingerprint the panel shows stays the one of the main certificate (the domain's)
        self.assertEqual(self.panel.fingerprint, conf.fingerprint())

    def test_pages_of_people_by_ip_answer_over_the_ip_certificate(self):
        sha, status = self.peer(None, "/p/nonexistent-page-token-0000")
        self.assertEqual(sha, leaf_sha(self.ip[0]))
        self.assertTrue(status.startswith(b"HTTP/1."), status)     # the handshake passed, /p/ answered (its 404)

    def test_without_an_ip_certificate_everybody_gets_the_domain_one(self):
        self.ip = None
        self.fresh()
        dom = leaf_sha(self.domain[1])
        self.assertEqual(self.peer(None)[0], dom)
        self.assertEqual(self.peer(DOMAIN)[0], dom)

    def test_renewal_is_picked_up_without_a_restart(self):
        self.ip = (site_fixture("le2.pem"), site_fixture("le2.key"))          # the IP certificate renewed
        self.fresh()
        self.assertEqual(self.peer(None)[0], leaf_sha(self.ip[0]))
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))  # the domain's renewed
        self.fresh()
        self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(self.domain[1]))
        self.assertEqual(self.peer(None)[0], leaf_sha(self.ip[0]))
        self.domain = None                                        # the domain cleared: the IP one for all
        self.fresh()
        self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(self.ip[0]))


class PrevDomainTest(LiveBase):
    """T-M6-qs330d №1: after a change of the domain the old name gets its own (still valid) certificate for a while —
    a wizard opened on it learns the result and moves; its Host is still a name of the open mode."""

    def test_old_name_gets_its_certificate_and_host_while_the_grace_lasts(self):
        self.domain = ("other.example.org", site_fixture("other.pem"), site_fixture("other.key"))
        prev = [(DOMAIN, site_fixture("le.pem"), site_fixture("le.key"))]
        with mock.patch.object(conf, "prev_site_cert", side_effect=lambda: prev[0]):
            self.fresh()
            self.panel.tls_refresh()
            self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(site_fixture("le.pem")))
            self.assertEqual(self.peer("other.example.org")[0], leaf_sha(site_fixture("other.pem")))
            self.assertEqual(self.peer(None)[0], leaf_sha(self.ip[0]))
            names = self.panel.servers["public"][0].names
            self.assertIn(DOMAIN, names)
            self.assertIn("other.example.org", names)
            prev[0] = None                                         # the grace ran out
            self.fresh()
            self.panel.tls_refresh()
            self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(site_fixture("other.pem")))
            self.assertNotIn(DOMAIN, self.panel.servers["public"][0].names)


class RefreshTest(LiveBase):
    """T-M6-qs330b №4: an issued certificate is served from the next handshake after SIGHUP / the move check — not
    after TLS_CHECK_EVERY (the wizard moved to the domain on the 6th second and met the IP certificate)."""

    def test_stale_within_the_check_window_then_fresh_after_refresh(self):
        self.peer(DOMAIN)                                          # the context is built and checked just now
        old = leaf_sha(self.domain[1])
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))   # issued: the copy changed
        self.panel._tls_sig = None                                 # (the mock does not touch the files)
        self.assertEqual(self.peer(DOMAIN)[0], old)                # within TLS_CHECK_EVERY: the old context
        self.panel.tls_refresh()
        self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(self.domain[1]))   # at once, no 5-s wait

    def test_reload_sighup_refreshes_the_certificate(self):
        self.peer(DOMAIN)
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))
        self.panel.reload()                                        # what SIGHUP does (site.consumers._reload_panel)
        self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(self.domain[1]))

    def test_a_broken_pair_is_looked_at_again_at_once(self):
        self.peer(DOMAIN)
        good = self.domain
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("le.key"))    # half-written: wrong key
        self.panel._tls_checked = 0.0
        self.panel._tls_sig = None
        with self.assertRaises(OSError):
            self.peer(DOMAIN)                                      # the handshake is refused, not the old context
        self.assertEqual(self.panel._tls_checked, 0.0)             # the next connection looks again
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))
        self.panel._tls_sig = None
        self.assertEqual(self.peer(DOMAIN)[0], leaf_sha(self.domain[1]))
        self.domain = good

    def test_domain_served_compares_the_certificate_of_a_real_handshake(self):
        self.assertEqual(conf.domain_served(), {"ready": True, "reason": "", "host": DOMAIN})
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))
        self.assertEqual(conf.domain_served()["reason"], "stale")   # the panel has not re-read it yet
        self.panel.tls_refresh()
        self.assertTrue(conf.domain_served()["ready"])
        self.domain = None
        self.assertEqual(conf.domain_served()["reason"], "no_cert")
        cfg = conf.load()
        cfg["public"]["on"] = False
        self.assertEqual(conf.domain_served(cfg)["reason"], "not_public")

    def test_domain_served_without_an_answer(self):
        cfg = conf.load()
        cfg["public"]["port"] = free_port()                       # nobody listens there
        self.assertEqual(conf.domain_served(cfg, timeout=1)["reason"], "no_answer")

    def test_cert_step_waits_for_the_domain_certificate(self):
        """The step «cert» (another process: SIGHUP through systemd) says «ok» only after the handshake shows the
        domain's certificate; still the old one — the panel is told once more, then False."""
        from kaskad.setup import steps
        self.assertIs(steps.cert_served(None, wait=0.5), True)
        self.domain = (DOMAIN, site_fixture("idn.pem"), site_fixture("idn.key"))
        told = []
        with mock.patch("kaskad.site.consumers._reload_panel", side_effect=lambda r: told.append(1) or
                        self.panel.tls_refresh()):
            self.assertIs(steps.cert_served(None, wait=3.0), True)
        self.assertEqual(told, [1])
        self.domain = (DOMAIN, site_fixture("le2.pem"), site_fixture("le2.key"))
        with mock.patch("kaskad.site.consumers._reload_panel", side_effect=lambda r: told.append(2)), \
                mock.patch.object(steps, "CERT_SERVED_POLL", 0.05):
            self.assertIs(steps.cert_served(None, wait=0.3), False)
        self.domain = None
        self.assertIsNone(steps.cert_served(None, wait=0.3))     # nothing to wait for: no domain certificate


class RenewNextToDomainTest(unittest.TestCase):
    def test_ip_certificate_needed_while_the_panel_is_open_domain_or_not(self):
        with mock.patch.object(conf, "load", return_value={"public": {"on": True}}):
            with mock.patch.object(conf, "site_cert", return_value=(DOMAIN, "D", "K")):
                self.assertTrue(ipops.needed())
            with mock.patch.object(conf, "site_cert", return_value=None):
                self.assertTrue(ipops.needed())
        with mock.patch.object(conf, "load", return_value={"public": {"on": False}}):
            self.assertFalse(ipops.needed())


if __name__ == "__main__":
    unittest.main()
