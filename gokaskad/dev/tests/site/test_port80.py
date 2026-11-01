"""TCP/80 in every mode of the domain (live check 4.0-C, findings 1–3): Kaskad's own nginx is stopped in domain mode
and loses Debian's «Welcome to nginx!» for the site; someone else's nginx keeps serving Let's Encrypt through our
ACME-only vhost.  A fake nginx answers ``-t``/``-T`` from the real files of the test root, ``ss`` shows it on :80/:443
while it runs, and the fake certbot cannot bind :80 under it (``--standalone``)."""

import os
import re
import shutil
from unittest import mock

from kaskad import conflicts, paths, state, util
from kaskad.errors import CONFLICT
from kaskad.site import acme, consumers, model, nginx, ops
from tests.site.sitecase import DOMAIN, SiteCase

DEBIAN_DEFAULT = """##
# You should look at the following URL's in order to grasp a solid understanding
# of Nginx configuration files in order to fully unleash the power of Nginx.
##
server {
\tlisten 80 default_server;
\tlisten [::]:80 default_server;
\troot /var/www/html;
\tindex index.html index.htm index.nginx-debian.html;
\tserver_name _;
\tlocation / {
\t\ttry_files $uri $uri/ =404;
\t}
}
"""
NEIGHBOUR = "server {\n    listen 80;\n    server_name shop.example.org;\n    root /var/www/shop;\n}\n"
BIND80 = ("Could not bind TCP port 80 because it is already in use by another process on this system (such as a web "
          "server). Please stop the program in question and then try again.\n")


class FakeNginx(object):
    """nginx of the fake node: files are real (under the test root), the service lives in FakeSystemctl."""

    def __init__(self, case):
        self.case = case
        self.broken = None                      # a foreign file name: ``nginx -t`` fails naming it

    @property
    def unit(self):
        return self.case.w.sysd.units.setdefault("nginx", {"enabled": False, "active": False, "packaged": True})

    def running(self):
        return "nginx" in self.case.w.runner.binaries and self.unit.get("active")

    def enabled_files(self):
        """[(production path, text)] of sites-enabled/* and conf.d/*.conf, as nginx would include them."""
        out = []
        for d, pat in (("/etc/nginx/conf.d", r"\.conf$"), ("/etc/nginx/sites-enabled", r"")):
            try:
                names = sorted(os.listdir(paths.P(d)))
            except OSError:
                continue
            for name in names:
                if not re.search(pat, name):
                    continue
                p = os.path.join(paths.P(d), name)
                real = p
                if os.path.islink(p):
                    t = os.readlink(p)
                    real = t if t.startswith(paths.root()) else paths.P(t) if t.startswith("/") else \
                        os.path.join(os.path.dirname(p), t)
                text = util.read_text(real)
                if text is None:
                    continue                    # a dangling link: nginx -t would fail, the tests never make one
                out.append((d + "/" + name, text))
        return out

    def dump(self):
        return "".join("# configuration file %s:\n%s\n" % (n, t) for n, t in self.enabled_files())

    def test(self):
        files = self.enabled_files()
        if self.broken and any(n.endswith("/" + self.broken) for n, _ in files):
            return 1, "", "nginx: [emerg] unknown directive \"oops\" in /etc/nginx/sites-enabled/%s:3\n" % self.broken
        for port in ("80", "443"):
            owners = [n for n, t in files for ln in t.splitlines()
                      if re.match(r"^\s*listen\s+(?:\S*:)?%s\b.*default_server" % port, ln) and "[::]" not in ln]
            if len(owners) > 1:
                return 1, "", "nginx: [emerg] a duplicate default server for 0.0.0.0:%s in %s:2\n" % (port, owners[-1])
        return 0, "", "nginx: configuration file /etc/nginx/nginx.conf test is successful\n"

    def ports(self):
        if not self.running():
            return set()
        got = set()
        for _, t in self.enabled_files():
            for m in re.finditer(r"(?m)^\s*listen\s+(?:\S*:)?(\d+)", t):
                got.add(int(m.group(1)))
        return got

    def ss(self, argv, input):
        lines = ['tcp   LISTEN 0      511          0.0.0.0:%d        0.0.0.0:*    users:(("nginx",pid=901,fd=6))' % p
                 for p in sorted(self.ports())]
        return 0, "\n".join(lines) + ("\n" if lines else ""), ""

    def __call__(self, argv, input):
        if argv[1:2] == ["-v"]:
            return 0, "", "nginx version: nginx/1.24.0 (Ubuntu)\n"
        if argv[1:2] == ["-t"]:
            return self.test()
        if argv[1:2] == ["-T"]:
            rc, out, err = self.test()
            return rc, self.dump() if rc == 0 else "", err
        return 1, "", "fake nginx: %r\n" % (argv,)

    def install(self, running=True):
        """What the Debian package leaves: the default site enabled, the service enabled (and running — Kaskad's
        apt keeps services stopped with policy-rc.d)."""
        self.case.w.runner.binaries.add("nginx")
        self.case.write(nginx.DEFAULT_AVAILABLE, DEBIAN_DEFAULT, 0o644)
        link = nginx.default_enabled_path()
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if not os.path.lexists(link):
            os.symlink(nginx.default_available_path(), link)
        self.unit.update(enabled=True, active=running)


class Port80Case(SiteCase):
    def setUp(self):
        super().setUp()
        self.ng = FakeNginx(self)
        r = self.w.runner
        r._handlers.insert(0, (["ss"], self.ng.ss))          # the listeners follow the fake nginx
        r.on(["nginx"], self.ng)
        apt = self.w.pkgs.apt

        def apt_nginx(argv, input):
            got = apt(argv, input)
            if got[0] == 0 and "nginx" in argv:
                self.ng.install(running=False)
            return got
        r._handlers = [(p, apt_nginx if h == apt else h) for p, h in r._handlers]
        certbot = self.certbot

        def certbot_bind(argv, input):
            if self._standalone(argv) and 80 in self.ng.ports():
                certbot.calls.append(list(argv))
                return 1, "", "Saving debug log to /var/log/letsencrypt/letsencrypt.log\n" + BIND80
            return certbot(argv, input)
        r._handlers = [(p, certbot_bind if h is certbot else h) for p, h in r._handlers]
        self.template = mock.patch("kaskad.site.templates.deploy",
                                   return_value={"ok": True, "id": "demo", "name": "Демо", "source": "repo",
                                                 "fallback": False})
        self.template.start()
        self.addCleanup(self.template.stop)
        self.panel = mock.patch.object(consumers, "_reload_panel", return_value=False)
        self.panel.start()
        self.addCleanup(self.panel.stop)

    def _standalone(self, argv):
        if argv[1:2] == ["certonly"]:
            return "--standalone" in argv
        if argv[1:2] == ["renew"]:
            d = argv[argv.index("--cert-name") + 1] if "--cert-name" in argv else DOMAIN
            return acme.renewal_mode(d) != "webroot"
        if argv[1:2] == ["reconfigure"]:
            return False
        return False

    # ── helpers ──

    def foreign(self, neighbour=False):
        """nginx that was here before Kaskad (not in /etc/kaskad/site/pkgs), running with Debian's default."""
        self.ng.install()
        if neighbour:
            self.write("/etc/nginx/sites-available/shop", NEIGHBOUR, 0o644)
            os.symlink(paths.P("/etc/nginx/sites-available/shop"), paths.P("/etc/nginx/sites-enabled/shop"))

    def renewal(self):
        return acme.renewal_mode(DOMAIN)

    def files(self):
        return dict((k, os.path.lexists(p)) for k, p in (
            ("site", nginx.enabled_path()), ("acme", nginx.acme_enabled_path()),
            ("default", nginx.default_enabled_path()), ("mark", nginx.default_mark_path())))

    def renew_ok(self):
        """``certbot renew`` as certbot.timer runs it: TCP/80 must be answerable by the saved authenticator."""
        self.certbot.renew_pem = "le2"
        try:
            ops.renew(self.w.runner)
            return True
        except ops.Refusal:
            return False
        finally:
            self.certbot.renew_pem = None
            self.put_live("le")                  # back to the first certificate for the next round
            acme.set_autorenew(DOMAIN, True)

    def to_site(self):
        return ops.mode(self.w.runner, "site")

    def to_domain(self):
        return ops.mode(self.w.runner, "domain")


class OwnedNginxTest(Port80Case):
    """nginx installed by Kaskad for the site (the live stand)."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def test_off_domain_site_domain_site(self):
        self.assertEqual(self.renewal(), "standalone")
        self.assertTrue(self.renew_ok())
        self.to_site()                                            # apt installs nginx here
        self.assertIn("nginx", model.read_pkgs())
        self.assertTrue(nginx.owned())
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": False, "mark": True})
        self.assertEqual(self.renewal(), "webroot")
        self.assertTrue(self.ng.unit["active"] and self.ng.unit["enabled"])
        vhost = util.read_text(nginx.vhost_path())
        self.assertIn("listen 80 default_server;", vhost)
        self.assertIn("return 444;", vhost)
        self.assertTrue(self.renew_ok())

        self.to_domain()
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": False, "mark": True})
        self.assertEqual(self.renewal(), "standalone")
        self.assertFalse(self.ng.unit["active"])
        self.assertFalse(self.ng.unit["enabled"])
        self.assertNotIn(80, self.ng.ports())
        self.assertTrue(ops.port80(state.load(), self.w.runner)["free"])
        self.assertTrue(self.renew_ok())                          # finding 1: standalone binds :80 again

        self.to_site()
        self.assertTrue(self.ng.unit["active"] and self.ng.unit["enabled"])
        self.assertEqual(self.renewal(), "webroot")
        self.assertIn("return 444;", util.read_text(nginx.vhost_path()))
        self.assertEqual(self.w.runner.unmatched, [])

    def leftover(self):
        """What 3.2.18 left after ``site mode domain``: Debian's default enabled, nginx enabled and running."""
        os.symlink(nginx.default_available_path(), nginx.default_enabled_path())
        os.unlink(nginx.default_mark_path())
        self.ng.unit.update(active=True, enabled=True)
        self.assertIn(80, self.ng.ports())

    def test_rotate_keeps_444_and_the_handshake_rejection(self):
        self.write("/proc/net/if_inet6", "00000000000000000000000000000001 01 80 10 lo\n")   # IPv6: two listen 443
        self.to_site()
        ops.rotate_panel_path(self.w.runner)
        vhost = util.read_text(nginx.vhost_path())
        self.assertIn("listen [::]:80 default_server;", vhost)
        self.assertIn("return 444;", vhost)
        self.assertIn("ssl_reject_handshake on;", vhost)
        self.assertFalse(os.path.lexists(nginx.default_enabled_path()))

    def test_issue_in_domain_mode_frees_80_of_our_nginx(self):
        self.to_site()
        self.to_domain()
        self.leftover()
        shutil.rmtree(model.live_dir(DOMAIN))
        os.unlink(model.renewal_conf(DOMAIN))
        got = ops.issue(self.w.runner)
        self.assertTrue(got["issued"])
        self.assertFalse(self.ng.unit["active"])
        self.assertIn("--standalone", self.certbot.calls[-1])

    def test_issue_in_domain_mode_after_the_site_uses_standalone_on_a_free_port(self):
        self.to_site()
        self.to_domain()
        shutil.rmtree(model.live_dir(DOMAIN))
        os.unlink(model.renewal_conf(DOMAIN))
        got = ops.issue(self.w.runner)
        self.assertTrue(got["issued"])
        self.assertIn("--standalone", self.certbot.calls[-1])

    def test_repeat_mode_domain_repairs_a_node_left_with_nginx_on_80(self):
        """3.2.x switched the site off but left Kaskad's nginx on :80 with Debian's default."""
        self.to_site()
        self.to_domain()
        self.leftover()
        self.assertFalse(self.renew_ok())
        got = self.to_domain()
        self.assertTrue(got.get("unchanged"))
        self.assertFalse(self.ng.unit["active"])
        self.assertTrue(self.renew_ok())

    def test_tick_frees_80_for_its_renewal(self):
        self.to_site()
        self.to_domain()
        self.leftover()
        self.certbot.renew_pem = "le2"
        self.clock += 70 * 86400                                   # 13 days left: the watchdog renews
        got = ops.tick(self.w.runner)
        self.assertEqual(got["action"], "renewed")
        self.assertFalse(self.ng.unit["active"])

    def test_clear_and_remove_from_site_mode(self):
        self.to_site()
        ops.clear(self.w.runner)
        self.assertEqual(self.site()["mode"], "off")
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": False, "mark": True})
        self.assertFalse(self.ng.unit["active"])
        got = ops.remove(self.w.runner)
        self.assertIn("nginx", got["packages"])
        self.assertTrue(os.path.islink(nginx.default_enabled_path()))
        self.assertEqual(os.readlink(nginx.default_enabled_path()), nginx.default_available_path())
        self.assertEqual(os.listdir(model.site_dir()), ["pkgs"])       # who installed nginx stays (T-M6 №2, №16)

    def test_remove_from_site_mode_brings_the_default_back(self):
        self.to_site()
        ops.remove(self.w.runner)
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})
        self.assertFalse(self.ng.unit["active"])

    def test_clear_from_domain_mode_keeps_nginx_stopped(self):
        self.to_site()
        self.to_domain()
        ops.clear(self.w.runner)
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": False, "mark": True})
        self.assertFalse(self.ng.unit["active"])
        self.with_domain(issued="le")                             # off -> domain again: standalone on a free :80
        self.assertEqual(self.renewal(), "standalone")
        self.assertTrue(self.renew_ok())

    def test_remove_from_domain_mode(self):
        self.to_site()
        self.to_domain()
        ops.remove(self.w.runner)
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})

    def test_remove_keeps_a_default_the_owner_deleted(self):
        self.to_site()
        os.unlink(nginx.default_available_path())
        ops.remove(self.w.runner)
        self.assertFalse(os.path.lexists(nginx.default_enabled_path()))
        self.assertFalse(os.path.exists(nginx.default_mark_path()))

    def test_site_mode_is_not_refused_by_our_own_nginx_on_80(self):
        self.ng.install()                                          # running, Debian's default on :80
        model.add_pkgs(["nginx"])
        self.assertFalse(ops.port80(state.load(), self.w.runner)["free"])
        self.to_site()
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": False, "mark": True})

    def test_a_failed_site_write_brings_the_default_link_back(self):
        self.ng.install()
        model.add_pkgs(["nginx"])
        self.write("/etc/nginx/sites-available/kaskad", "# old\n", 0o600)
        with mock.patch.object(self.ng, "test", return_value=(1, "", "nginx: [emerg] boom in "
                                                                      "/etc/nginx/sites-enabled/kaskad:1\n")):
            with self.assertRaises(ops.Refusal) as e:
                self.to_site()
        self.assertEqual(e.exception.doc["reason"], "nginx_test")
        self.assertTrue(os.path.islink(nginx.default_enabled_path()))
        self.assertFalse(os.path.exists(nginx.default_mark_path()))
        self.assertEqual(self.renewal(), "standalone")

    def test_forward_443_and_80_name_only_the_site(self):
        self.to_site()
        st = state.load()
        f443 = conflicts.check("tcp", 443, self.w.runner, st=st)
        self.assertEqual([c["kind"] for c in f443], ["site"])
        self.assertEqual(conflicts.message("tcp", 443, f443),
                         "Порт 443/tcp занят: на нём открыт сайт Kaskad — сайт перестанет открываться. "
                         "Добавить всё равно — с --force.")
        f80 = conflicts.check("tcp", 80, self.w.runner, st=st)
        self.assertEqual([c["kind"] for c in f80], ["site"])
        self.assertIn("сертификат домена перестанет продлеваться", f80[0]["detail"])

    def test_check_has_no_default_page_warning(self):
        self.to_site()

        def code(runner, url, resolve):
            return 301 if url.startswith("http://") else 404 if "/app-xxx" in url else 200
        with mock.patch.object(ops, "_curl_code", code):
            doc = ops.check(self.w.runner)
        self.assertEqual((doc["verdict"], doc["warnings"]), ("ok", []))

    def test_check_names_a_closed_run_dir(self):
        """3.2.21 on O: /run/kaskad 0700 — nginx without the panel's socket, the panel on the domain answered 502. The
        check names the cause and the fix; with the passage open a 502 stays a plain «панель не отвечает»."""
        from kaskad.panel import conf as pconf
        self.to_site()

        def code(runner, url, resolve):
            if url.startswith("http://"):
                return 301
            if "/app-xxx" in url:
                return 404
            return 502 if "/app-" in url else 200
        os.makedirs(paths.panel_run_dir(), exist_ok=True)
        with open(pconf.proxy_sock_path(), "w"):
            pass
        os.chmod(pconf.proxy_sock_path(), 0o666)
        os.chmod(paths.panel_run_dir(), 0o711)
        os.chmod(paths.run_dir(), 0o700)
        with mock.patch.object(ops, "_curl_code", code), mock.patch.object(pconf, "_proxy_gid", return_value=None):
            doc = ops.check(self.w.runner)
            self.assertEqual(doc["verdict"], "panel_unreachable")
            self.assertIn("закрыт для nginx", doc["why"])
            self.assertIn("chmod 711 %s" % paths.run_dir(), doc["fix"])
            self.assertEqual(self.site()["check"]["why"], doc["why"])
            os.chmod(paths.run_dir(), 0o711)
            doc = ops.check(self.w.runner)                 # the passage is back, the socket file stale: restart
            self.assertEqual(doc["verdict"], "panel_unreachable")
            self.assertIn("не отвечает", doc["why"])
            self.assertIn("systemctl restart kaskad-panel", doc["fix"])
            with mock.patch.object(pconf, "_answers", return_value=True):
                doc = ops.check(self.w.runner)             # the panel answers on its socket: a 502 of another kind
        self.assertEqual(doc["verdict"], "panel_unreachable")
        self.assertNotIn("why", doc)


class ForeignNginxTest(Port80Case):
    """nginx that was on the server before Kaskad (Debian's default on :80, maybe somebody's shop)."""

    def setUp(self):
        super().setUp()
        self.foreign(neighbour=True)
        self.with_domain()                                         # off -> domain: our ACME vhost on their nginx

    def test_off_domain_issue_site_domain_site(self):
        self.assertFalse(nginx.owned())
        self.assertEqual(self.files(), {"site": False, "acme": True, "default": True, "mark": False})
        acme_vhost = util.read_text(nginx.acme_vhost_path())
        self.assertIn("server_name %s;" % DOMAIN, acme_vhost)
        self.assertIn("location ^~ /.well-known/acme-challenge/ {\n        root %s;" % nginx.acme_root(), acme_vhost)
        self.assertIn("return 404;", acme_vhost)
        self.assertNotIn("default_server", acme_vhost)
        self.assertTrue(ops.port80(state.load(), self.w.runner)["free"])
        self.certbot.pem = "le"
        got = ops.issue(self.w.runner)                             # through their nginx, never --standalone
        self.assertTrue(got["issued"])
        self.assertIn("--webroot", self.certbot.calls[-1])
        self.assertEqual(self.renewal(), "webroot")
        self.assertTrue(self.renew_ok())

        self.to_site()
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": True, "mark": False})
        vhost = util.read_text(nginx.vhost_path())
        self.assertNotIn("listen 80 default_server", vhost)       # their :80 vhosts stay theirs
        self.assertTrue(self.ng.unit["active"])
        self.assertTrue(self.renew_ok())

        self.to_domain()
        self.assertEqual(self.files(), {"site": False, "acme": True, "default": True, "mark": False})
        self.assertTrue(self.ng.unit["active"])                   # never stopped: it is not ours
        self.assertEqual(self.renewal(), "webroot")
        self.assertTrue(self.renew_ok())
        self.assertTrue(ops.port80(state.load(), self.w.runner)["free"])

        self.to_site()
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": True, "mark": False})
        self.assertEqual(self.renewal(), "webroot")
        self.assertEqual(self.w.runner.unmatched, [])

    def test_clear_in_each_mode_takes_our_vhosts_away_and_leaves_their_nginx(self):
        ops.issue(self.w.runner)
        ops.clear(self.w.runner)                                   # from domain
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})
        self.assertTrue(self.ng.unit["active"])
        self.with_domain()
        self.assertTrue(self.files()["acme"])
        ops.issue(self.w.runner)                                   # the live certificate is kept, not issued again
        self.to_site()
        ops.clear(self.w.runner)                                   # from site
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})
        self.assertTrue(self.ng.unit["active"])

    def test_remove_from_domain_and_off_modes(self):
        ops.issue(self.w.runner)
        ops.remove(self.w.runner)                                  # from domain
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})
        self.assertTrue(self.ng.unit["active"])
        ops.remove(self.w.runner)                                  # from off: nothing left to do
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})

    def test_another_domain_moves_the_acme_vhost(self):
        ops.set_domain(self.w.runner, "other.example.com")
        text = util.read_text(nginx.acme_vhost_path())
        self.assertIn("server_name other.example.com;", text)
        self.assertNotIn("server_name %s;" % DOMAIN, text)

    def test_remove_from_site_mode(self):
        ops.issue(self.w.runner)
        self.to_site()
        got = ops.remove(self.w.runner)
        self.assertNotIn("nginx", got["packages"])
        self.assertEqual(self.files(), {"site": False, "acme": False, "default": True, "mark": False})
        self.assertTrue(os.path.exists(paths.P("/etc/nginx/sites-enabled/shop")))
        self.assertTrue(self.ng.unit["active"])

    def test_site_mode_domain_refuses_when_their_file_is_broken(self):
        ops.issue(self.w.runner)
        self.to_site()
        self.ng.broken = "shop"
        with self.assertRaises(ops.Refusal) as e:
            self.to_domain()
        self.assertEqual((e.exception.doc["reason"], e.exception.code), ("nginx_test", CONFLICT))
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": True, "mark": False})
        self.assertEqual(self.site()["mode"], "site")
        self.assertEqual(self.renewal(), "webroot")

    def test_check_warns_about_the_default_page(self):
        self.assertEqual(ops._check_warning_codes(self.w.runner), [ops.DEFAULT_PAGE])
        self.assertEqual(ops.warning(ops.DEFAULT_PAGE), "на порту 80 отвечает страница nginx по умолчанию — сервер выдаёт себя")

    def test_site_check_carries_the_warning(self):
        ops.issue(self.w.runner)
        self.to_site()

        def code(runner, url, resolve):
            return 301 if url.startswith("http://") else 404 if "/app-xxx" in url else 200
        with mock.patch.object(ops, "_curl_code", code):
            doc = ops.check(self.w.runner)
        self.assertEqual((doc["ok"], doc["verdict"]), (True, "ok"))
        self.assertEqual(doc["warnings"], [ops.warning(ops.DEFAULT_PAGE)])
        self.assertEqual(doc["warning_codes"], [ops.DEFAULT_PAGE])
        self.assertEqual(self.site()["check"]["warnings"], [ops.warning(ops.DEFAULT_PAGE)])
        from kaskad.i18n import use_lang
        with use_lang("en"):                # stored in Russian, read in English: said again from the code
            got = ops.status(self.w.runner)["site"]["check"]
            self.assertEqual(got["warnings"], [ops.warning(ops.DEFAULT_PAGE)])
            self.assertTrue(got["warnings"][0].startswith("the default nginx page"), got)

    def test_forward_443_keeps_both_reasons_on_their_nginx(self):
        ops.issue(self.w.runner)
        self.to_site()
        kinds = [c["kind"] for c in conflicts.check("tcp", 443, self.w.runner, st=state.load())]
        self.assertEqual(kinds, ["site", "listener"])


class ForeignStoppedAndOwnedButSharedTest(Port80Case):
    def test_a_stopped_foreign_nginx_is_not_started_and_standalone_is_used(self):
        self.foreign()
        self.ng.unit.update(active=False)
        self.with_domain(issued="le")
        self.assertEqual(self.files()["acme"], False)
        self.assertFalse(self.ng.unit["active"])
        self.assertIn("--standalone", self.certbot.calls[-1])

    def test_kaskad_installed_nginx_with_somebodys_vhost_is_foreign(self):
        self.foreign(neighbour=True)
        model.add_pkgs(["nginx"])
        self.assertFalse(nginx.owned())
        self.assertEqual(nginx.foreign_sites(), ["sites-enabled/shop"])
        os.unlink(paths.P("/etc/nginx/sites-enabled/shop"))
        self.assertTrue(nginx.owned())
        self.write("/etc/nginx/conf.d/api.conf", "server {\n    listen 8080;\n}\n", 0o644)
        self.assertFalse(nginx.owned())

    def test_a_default_that_is_a_plain_file_is_foreign(self):
        self.ng.install()
        model.add_pkgs(["nginx"])
        os.unlink(nginx.default_enabled_path())
        self.write(nginx.DEFAULT_ENABLED, DEBIAN_DEFAULT, 0o644)
        self.assertEqual(nginx.foreign_sites(), ["sites-enabled/default"])


class CertbotPort80TextTest(Port80Case):
    """Coordinator: ``certbot renew`` under standalone with :80 taken says so in words, not certbot's output."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")
        self.foreign()
        self.ng.unit.update(active=True)
        # their nginx came after the domain: renewal is still standalone and :80 is taken

    def test_cause_recognises_the_bind_failure(self):
        self.assertEqual(acme.cause(BIND80), "port80")
        self.assertEqual(acme.cause("Problem binding to port 80: Could not bind to IPv4 or IPv6."), "port80")
        self.assertIn("Порт 80/tcp", acme.refusal_text(DOMAIN, "port80"))

    def test_renew_names_who_holds_80(self):
        self.certbot.renew_pem = "le2"
        with self.assertRaises(ops.Refusal) as e:
            ops.renew(self.w.runner)
        self.assertEqual(e.exception.doc["reason"], "port80")
        text = e.exception.doc["text"]
        self.assertIn("Порт 80/tcp занят: его слушает nginx", text)
        self.assertIn("повторите продление", text)
        self.assertNotIn("Could not bind", text)

    def test_tick_tells_the_bot_why(self):
        self.certbot.renew_pem = "le2"
        self.clock += 70 * 86400
        got = ops.tick(self.w.runner)
        self.assertEqual(got["action"], "failed")
        self.assertIn("Порт 80/tcp занят: его слушает nginx", got["text"])
        self.assertNotIn("Could not bind", got["text"])
        self.assertEqual(len(self.notified), 1)


class RealNginxTest(SiteCase):
    """``nginx -t`` of the real binary on the generated files (skipped without nginx)."""

    def test_site_with_444_and_the_acme_vhost_pass_nginx_t(self):
        from tests import realbin
        if not realbin.nginx_path():
            self.skipTest("нет nginx")
        site = nginx.render(DOMAIN, "app-" + "A" * 86, reject_handshake=True, version="1.24.0", ipv6=True,
                            reject80=True)
        acme_vhost = nginx.render_acme("other." + DOMAIN, ipv6=True)
        texts = [re.sub(r"\b80\b", "18080", t).replace("443", "18443") for t in (site, acme_vhost)]
        ok, out = realbin.nginx_check(*texts)                  # the certificates — the fixture pair (tests/realbin.py)
        self.assertTrue(ok, out)

    def test_rendered_444_block(self):
        text = nginx.render(DOMAIN, "app-" + "A" * 86, version="1.24.0", ipv6=True, reject80=True)
        block = text.split("listen 80 default_server;", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("listen [::]:80 default_server;", block)
        self.assertIn("return 444;", block)
        # the IP certificate (kaskad.ipcert): Let's Encrypt asks by the address — only its directory answers
        self.assertIn("location ^~ /.well-known/acme-challenge/ {\n        root %s;" % nginx.acme_root(), block)
        self.assertNotIn(DOMAIN, block)                            # the domain does not leak to IP scanners
        self.assertNotIn("listen 80 default_server", nginx.render(DOMAIN, "app-" + "A" * 86, version="1.24.0",
                                                                  ipv6=False))


class ForeignListenTest(SiteCase):
    def test_our_files_and_skipped_default_do_not_count(self):
        dump = ("# configuration file /etc/nginx/nginx.conf:\nhttp { include sites-enabled/*; }\n"
                "# configuration file /etc/nginx/sites-enabled/kaskad:\n    listen 80;\n    listen 443 ssl;\n"
                "    listen [::]:443 ssl;\n    listen 443 ssl default_server;\n"
                "# configuration file /etc/nginx/sites-enabled/default:\n\tlisten 80 default_server;\n")
        self.w.runner.binaries.add("nginx")
        self.w.runner.on(["nginx", "-T"], (0, dump, ""))
        self.assertTrue(nginx.can_reject_handshake(self.w.runner))  # 3.2.18 counted our own two listen 443 lines
        self.assertFalse(nginx.can_reject80(self.w.runner))
        self.assertTrue(nginx.can_reject80(self.w.runner, skip_default=True))


class SiteRefusalTest(Port80Case):
    """Coordinator: the refusals of ``site mode site`` are uniform JSON with their own ``reason``."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def test_port80_taken_is_reason_port80(self):
        self.w.ss = ""
        self.ng.ss = lambda argv, input: (0, 'tcp   LISTEN 0      5      0.0.0.0:80      0.0.0.0:*    '
                                                 'users:(("python3",pid=77,fd=3))\n', "")
        self.w.runner._handlers[0] = (["ss"], self.ng.ss)
        before = util.read_text(model.renewal_conf(DOMAIN))
        with self.assertRaises(ops.Refusal) as e:
            self.to_site()
        doc = e.exception.doc
        self.assertEqual((doc["reason"], e.exception.code), ("port80", CONFLICT))
        self.assertTrue(doc["text"].startswith("Порт 80/tcp занят: его слушает python3."), doc["text"])
        self.assertNotIn("перестанут доходить", doc["text"])
        self.assertEqual(self.site()["mode"], "domain")
        self.assertEqual(util.read_text(model.renewal_conf(DOMAIN)), before)

    def test_port80_refusal_over_cli_and_api(self):
        from kaskad import cli
        from kaskad.panel import routes
        import contextlib
        import io
        import json
        from kaskad.site import panel as site_panel  # noqa: F401 — routes register on import
        refusal = ops.Refusal("port80", "Порт 80/tcp занят: его слушает python3.", code=CONFLICT)
        with mock.patch.object(ops, "mode", side_effect=refusal):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["site", "mode", "site", "--json"])
            self.assertEqual(rc, CONFLICT)
            doc = json.loads(out.getvalue())
            self.assertEqual((doc["ok"], doc["reason"], doc["code"]), (False, "port80", CONFLICT))
            resp = site_panel._call(ops.mode, None, "site")
            self.assertEqual(resp.status, 409)
            body = json.loads(resp.body)
            self.assertEqual((body["reason"], body["text"]), ("port80", refusal.doc["text"]))
        self.assertIsNotNone(routes.find("PUT", "/api/site/mode")[0])

    def _failed(self, reason, tid="gh_broken"):
        from kaskad.site import templates
        return {"ok": False, "id": tid, "name": None, "source": None, "fallback": False, "backup": None, "tries": 1,
                "errors": [], "warnings": [], "root": "/var/www/kaskad-site", "reason": reason,
                "text": templates.TEXTS[reason] % tid if "%s" in templates.TEXTS[reason] else templates.TEXTS[reason]}

    def test_explicit_template_failure_is_reason_template_and_nothing_changes(self):
        before = util.read_text(model.renewal_conf(DOMAIN))
        for code in ("download_failed", "no_index", "app_shell", "broken_assets", "timeout", "too_big", "bad_repo",
                     "gone"):
            with mock.patch("kaskad.site.templates.deploy", return_value=self._failed(code)):
                with self.assertRaises(ops.Refusal) as e:
                    ops.mode(self.w.runner, "site", template_id="gh_broken")
            doc = e.exception.doc
            self.assertEqual((doc["reason"], doc["template_reason"], e.exception.code), ("template", code, CONFLICT))
            self.assertIn("«gh_broken»", doc["text"])
            self.assertIn("Сайт не включён", doc["text"])
            self.assertEqual(self.site()["mode"], "domain")
            self.assertNotIn("nginx", self.w.runner.binaries)       # nginx was not even installed
            self.assertEqual(util.read_text(model.renewal_conf(DOMAIN)), before)
            self.assertFalse(os.path.lexists(nginx.enabled_path()))

    def test_catalog_errors_become_template_reasons(self):
        from kaskad.errors import KaskadError
        for why, want in (("net", "net"), ("nokey", "nokey"), ("bad", "nokey"), ("sha", "download_failed")):
            err = KaskadError("Нет связи с GitHub.", data={"reason": why})
            with mock.patch("kaskad.site.templates.deploy", side_effect=err):
                with self.assertRaises(ops.Refusal) as e:
                    ops.mode(self.w.runner, "site", template_id="gh_good")
            self.assertEqual((e.exception.doc["reason"], e.exception.doc["template_reason"]), ("template", want))
        with mock.patch("kaskad.site.templates.deploy", side_effect=KaskadError("apt-get: нет git")):
            with self.assertRaises(ops.Refusal) as e:
                ops.mode(self.w.runner, "site", template_id="gh_good")
        self.assertEqual(e.exception.doc["reason"], "apt")

    def test_a_failure_after_nginx_was_installed_leaves_it_off(self):
        with mock.patch.object(nginx, "write", side_effect=nginx.NginxTestError("boom", ours=True)):
            with self.assertRaises(ops.Refusal):
                self.to_site()
        self.assertIn("nginx", model.read_pkgs())
        self.assertFalse(self.ng.unit["enabled"])                  # Debian enabled it; :80 stays free after reboot
        self.assertFalse(self.ng.unit["active"])
        self.assertEqual(self.renewal(), "standalone")
        self.assertTrue(self.renew_ok())


class RandomFallbackTest(Port80Case):
    """``site mode site`` (random): three broken templates on an empty root -> the site is on with the stub."""

    def setUp(self):
        super().setUp()
        if not shutil.which("git"):
            self.skipTest("нет git")
        from kaskad import ghkey
        from kaskad.site import templates as T
        from tests.site import fixtures_templates as fx
        from tests.site.test_templates import shared_repos
        self.template.stop()
        for p in (mock.patch.dict(os.environ, {"KASKAD_ASSETS_ROOTS": "", "GH_TOKEN": "", "GITHUB_TOKEN": "",
                                               "GOINST_TOKEN_FILE": self.path("/nonexistent"),
                                               "KASKAD_TEST_GIT_HOME": shared_repos()}),
                  mock.patch.object(T, "CATALOG_SHA256", fx.catalog_sha()), mock.patch.object(T, "MIN_FREE", 0),
                  mock.patch.object(ghkey, "open_github", side_effect=AssertionError("no network in tests"))):
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(os.path.dirname(T.catalog_path()), exist_ok=True)
        with open(T.catalog_path(), "wb") as f:
            f.write(fx.catalog_bytes())
        clock = [1000.0]
        p = mock.patch.object(T, "_now", lambda: clock[0])
        p.start()
        self.addCleanup(p.stop)
        self.git = fx.GitWorld(shared_repos(), clock)
        real = T.deploy
        p = mock.patch.object(T, "deploy", lambda runner, tid=None, root=None: real(self.git.runner, tid, root))
        p.start()
        self.addCleanup(p.stop)
        order = ["gh_noindex", "gh_appshell", "gh_broken"]

        class Rng(object):
            def choice(self, pool):
                return next((i for i in order if i in pool), pool[0])
        p = mock.patch.object(T, "_rng", Rng())
        p.start()
        self.addCleanup(p.stop)
        self.T = T
        self.with_domain(issued="le")

    def test_three_failures_on_an_empty_root_still_turn_the_site_on(self):
        got = self.to_site()
        self.assertTrue(got["fallback"])
        self.assertEqual(got["site"]["mode"], "site")
        self.assertEqual(got["template"]["id"], self.T.STUB_ID)
        self.assertEqual(len(self.git.clones), 3)
        self.assertTrue(os.path.exists(os.path.join(paths.site_root(), "index.html")))

    def test_an_explicit_broken_template_is_refused(self):
        with self.assertRaises(ops.Refusal) as e:
            ops.mode(self.w.runner, "site", template_id="gh_broken")
        self.assertEqual((e.exception.doc["reason"], e.exception.doc["template_reason"]), ("template", "broken_assets"))
        self.assertEqual(self.site()["mode"], "domain")
        self.assertFalse(os.path.exists(os.path.join(paths.site_root(), "index.html")))
