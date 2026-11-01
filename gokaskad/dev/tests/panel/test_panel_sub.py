"""The subscription for apps through the real server (docs/k3/SPEC-vless-inbound.md §8): behind nginx (the proxy
listener, the site mode: «location ^~ /p/» passes the path as it is) and on the tunnel; the token never reaches the
journal of the panel; a 404 of the subscription is a miss of the one ban of /p/."""

import base64
import io
import time
from unittest import mock

from kaskad.panel import auth, userpage
from tests.panel.test_panel_proxy import DOMAIN, PREFIX, ProxyCase


class SubscriptionServerTest(ProxyCase):
    def setUp(self):
        super().setUp()
        userpage.GUARDS.reset()
        self.ok("POST", "/api/gw/users", {"name": "Мама"})
        userpage.sub_enable("Мама")
        self.tok = list(userpage._stored_sub_tokens().values())[0]

    def test_behind_nginx_at_the_root_of_the_site(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        r = self.pcall("GET", "/p/%s/sub" % self.tok, headers={"User-Agent": "v2raytun/ios"})
        self.assertEqual(r.status, 200, r.body[:200])
        self.assertEqual(r.body, b"")                                       # no device with VLESS: empty, honest
        self.assertEqual(r.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["profile-update-interval"], "12")
        self.assertEqual(base64.b64decode(r.headers["profile-title"][7:]).decode("utf-8"), "goKaskad PRO · Мама")
        r = self.pcall("GET", "/%s/p/%s/sub" % (PREFIX, self.tok))          # under the prefix too
        self.assertEqual(r.status, 200)

    def test_an_unknown_token_is_the_404_and_a_miss(self):
        self.proxy_on()
        now = [time.time()]
        with mock.patch.object(auth, "_now", lambda: now[0]):
            for i in range(auth.PAGE_MISSES):
                r = self.pcall("GET", "/p/%s/sub" % ("S%042d" % i), ip="198.51.100.70")
                self.assertEqual((r.status, r.body), (404, userpage.NOT_FOUND_BODY))
            self.assertTrue(auth.PAGES.banned("198.51.100.70", now=now[0]))
            r = self.pcall("GET", "/p/%s/sub" % self.tok, ip="198.51.100.70")
            self.assertEqual(r.status, 404)                                 # banned: the right one too
            self.assertEqual(self.pcall("GET", "/p/%s/sub" % self.tok, ip="198.51.100.71").status, 200)

    def test_the_token_is_not_in_the_journal(self):
        self.proxy_on()
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf), \
                mock.patch.object(userpage, "build_sub", mock.Mock(side_effect=RuntimeError("сломалось"))):
            r = self.pcall("GET", "/p/%s/sub" % self.tok)
            self.call("GET", "/p/%s/sub" % self.tok, tunnel=False)
        self.assertEqual(r.status, 500)
        log = buf.getvalue()
        self.assertIn("RuntimeError", log)
        self.assertNotIn(self.tok, log)

    def test_on_the_tunnel_listener(self):
        r = self.call("GET", "/p/%s/sub" % self.tok, tunnel=False)
        self.assertEqual((r.status, r.body), (200, b""))
