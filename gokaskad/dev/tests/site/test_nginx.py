"""Offline contract tests for wave 4.0-C's site core (no nginx or network)."""

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
from unittest import mock

from kaskad import cli, conflicts, i18n, paths, state, sysrun, util
from kaskad.errors import CONFLICT
from kaskad.panel import conf, routes
from kaskad.site import acme, consumers, model, nginx, ops, panel as site_panel  # routes register on import
from tests.helpers import RootCase
from tests.site.sitecase import DOMAIN, SiteCase


class NginxCase(RootCase):
    def setUp(self):
        super().setUp()
        self.r = sysrun.FakeRunner({"nginx"})
        self.nginx_test_rc = 0
        self.nginx_test_err = ""
        self.nginx_dump = "# configuration file /etc/nginx/sites-enabled/kaskad:\n# Kaskad 3: site vhost.\n"
        self.r.on(["nginx", "-v"], (0, "", "nginx version: nginx/1.24.0\n"))
        self.r.on(["nginx", "-t"], lambda argv, input: (self.nginx_test_rc, "syntax is ok\n" if not self.nginx_test_rc else "",
                                                            self.nginx_test_err or ("bad config app-" + "Z" * 86
                                                            if self.nginx_test_rc else "")))
        self.r.on(["nginx", "-T"], lambda argv, input: (0, self.nginx_dump, ""))
        self.r.on(["systemctl", "is-active", "nginx"], (3, "inactive\n", ""))
        self.r.on(["systemctl", "start", "nginx"], (0, "", ""))
        self.r.on(["systemctl", "reload", "nginx"], (0, "", ""))

    def test_vhost_has_acme_redirect_proxy_and_silent_decoy(self):
        secret = "app-" + "A" * 86
        text = nginx.render(DOMAIN, secret, reject_handshake=True,
                            version="1.24.0", ipv6=False)
        self.assertIn("location ^~ /.well-known/acme-challenge/", text)
        self.assertIn("return 301 https://$server_name$request_uri", text)
        self.assertIn("location ^~ /%s/" % secret, text)
        self.assertIn("location ^~ /p/", text)
        self.assertIn("X-Forwarded-Prefix /%s" % secret, text)
        self.assertIn("limit_req zone=kaskad_pages burst=40 nodelay", text)
        self.assertIn("access_log off", text)
        self.assertIn("listen 443 ssl default_server", text)
        self.assertIn("ssl_reject_handshake on", text)
        self.assertIn(model.fullchain_path(), text)
        self.assertIn(model.privkey_path(), text)
        self.assertNotIn("[::]", text)

    def test_vhost_listens_on_ipv6_and_rejects_unknown_sni_by_default(self):
        text = nginx.render(DOMAIN, "app-" + "A" * 86,
                            reject_handshake=True, version="1.24.0", ipv6=True)
        self.assertIn("listen [::]:80;", text)
        self.assertIn("listen [::]:443 ssl http2;", text)
        self.assertIn("listen 443 ssl default_server;", text)
        self.assertIn("listen [::]:443 ssl default_server;", text)

    def test_vhost_text_is_a_complete_nginx_block_contract(self):
        text = nginx.render(DOMAIN, "app-" + "A" * 86, reject_handshake=True,
                            version="1.24.0", ipv6=True)
        depth = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"^(?:server|location)\b", line):
                self.assertTrue(line.endswith("{"), line)
            if line == "}":
                depth -= 1
                self.assertGreaterEqual(depth, 0, line)
                continue
            opens = line.count("{")
            closes = line.count("}")
            if opens or closes:
                depth += opens - closes
                self.assertGreaterEqual(depth, 0, line)
                continue
            self.assertTrue(line.endswith(";"), line)
            self.assertRegex(line, r"^[A-Za-z_][A-Za-z0-9_]*\s+\S.*;$", line)
        self.assertEqual(depth, 0)
        self.assertNotIn("%(", text)

    def test_vhost_passes_real_nginx_test_when_available(self):
        """The real nginx -t (tests/realbin.py: the certificates — the fixture pair; nothing outside its temporary
        folder changes, T-3310b). No nginx — skipped."""
        from tests import realbin
        if not realbin.nginx_path():
            self.skipTest("nginx не установлен")
        vhost = nginx.render(DOMAIN, "app-" + "A" * 86, reject_handshake=True, version="1.24.0", ipv6=False)
        vhost = vhost.replace("listen 80;", "listen 18080;").replace("listen 443 ssl http2;",
                                                                    "listen 18443 ssl http2;")
        ok, out = realbin.nginx_check(vhost)
        self.assertTrue(ok, out)

    def test_behind_xray_the_site_listens_on_the_socket_with_the_proxy_protocol(self):
        """VLESS + TLS on 443 (site.front): nginx gets everything else from xray's fallback over a Unix socket."""
        sock = "/run/kaskad-vless/site.sock"
        text = nginx.render(DOMAIN, "app-" + "A" * 86, reject_handshake=True, version="1.26.0", ipv6=True,
                            reject80=True, behind=sock)
        self.assertIn("listen unix:%s proxy_protocol;" % sock, text)
        for line in ("set_real_ip_from unix:;", "real_ip_header proxy_protocol;", "absolute_redirect off;",
                     "listen 80;", "listen [::]:80;", "location ^~ /p/ {", "limit_req zone=kaskad_pages",
                     "return 444;"):
            self.assertIn(line, text)
        for gone in ("listen 443", "listen [::]:443", "ssl_certificate", "ssl_reject_handshake", "http2 on"):
            self.assertNotIn(gone, text)
        front = nginx.render(DOMAIN, "app-" + "A" * 86, reject_handshake=True, version="1.26.0", ipv6=False)
        self.assertIn("listen 443 ssl;", front)                                 # in front: as before
        self.assertNotIn("unix:/run/kaskad-vless", front)
        depth = 0
        for ch in text:
            depth += {"{": 1, "}": -1}.get(ch, 0)
            self.assertGreaterEqual(depth, 0)
        self.assertEqual(depth, 0)

    def test_behind_vhost_passes_real_nginx_test_when_available(self):
        from tests import realbin
        if not realbin.nginx_path():
            self.skipTest("nginx не установлен")
        with tempfile.TemporaryDirectory(prefix="kaskad-nginx-") as work:
            vhost = nginx.render(DOMAIN, "app-" + "A" * 86, version="1.24.0", ipv6=False,
                                 behind=os.path.join(work, "site.sock"))
            ok, out = realbin.nginx_check(vhost.replace("listen 80;", "listen 18080;"))
            self.assertTrue(ok, out)

    def test_the_browser_test_has_two_exact_locations_and_its_zones_in_both_schemes(self):
        """BC2 (k3/handoff/SPEC-browser-channel.md §4.1, NS-B15): speed/up — the body passed on as it comes with an
        8 MiB window and limit_conn (8 in all, 5 per address, 429); speed/down — not buffered, 64 KiB reads; the
        zones next to kaskad_pages; the rest of the prefix keeps 1m. Plain (http2) and behind xray (front)."""
        secret = "app-" + "A" * 86
        for kw in ({"version": "1.26.0"}, {"version": "1.24.0"},
                   {"version": "1.26.0", "behind": "/run/kaskad-vless/site.sock"}):
            text = nginx.render(DOMAIN, secret, ipv6=False, **kw)
            zones = text.index("limit_req_zone $binary_remote_addr zone=kaskad_pages:10m rate=5r/s;")
            self.assertEqual(text[zones:].splitlines()[1:3],
                             ["limit_conn_zone $server_name zone=kaskad_speed:1m;",
                              "limit_conn_zone $binary_remote_addr zone=kaskad_speed_ip:10m;"])
            self.assertLess(zones, text.index("server {"))
            blocks = {}
            for path in ("api/host/speed/up", "api/host/speed/down"):
                a = text.index("location = /%s/%s {" % (secret, path))
                blocks[path] = [ln.strip() for ln in text[a:text.index("}", a)].splitlines()[1:]]
                self.assertIn("proxy_pass http://unix:%s:/%s;" % (conf.proxy_sock_path(), path), blocks[path])
                self.assertIn("proxy_set_header X-Forwarded-Prefix /%s;" % secret, blocks[path])
                self.assertIn("proxy_http_version 1.1;", blocks[path])
                self.assertEqual(text.count("location = /%s/%s {" % (secret, path)), 1)
            up = blocks["api/host/speed/up"]
            for line in ("client_max_body_size 64m;", "client_body_buffer_size 8m;", "proxy_request_buffering off;",
                         "client_body_timeout 15s;", "limit_conn kaskad_speed 8;", "limit_conn kaskad_speed_ip 5;",
                         "limit_conn_status 429;"):
                self.assertIn(line, up, kw)
            self.assertNotIn("proxy_buffering off;", up)
            down = blocks["api/host/speed/down"]
            for line in ("proxy_buffering off;", "proxy_buffer_size 64k;", "proxy_buffers 8 64k;"):
                self.assertIn(line, down, kw)
            for line in ("client_body_buffer_size 8m;", "limit_conn kaskad_speed 8;", "client_max_body_size 64m;"):
                self.assertNotIn(line, down, kw)
            a = text.index("location ^~ /%s/ {" % secret)
            rest = text[a:text.index("}", a)]
            self.assertIn("client_max_body_size 1m;", rest)
            for line in ("client_body_buffer_size", "limit_conn", "proxy_request_buffering", "proxy_buffering",
                         "proxy_buffer_size", "proxy_buffers"):
                self.assertNotIn(line, rest)
            for line in ("proxy_buffer_size", "proxy_buffers"):
                self.assertNotIn(line, up, kw)
            self.assertEqual(text.count("client_body_buffer_size 8m;"), 1)
            for p in ("preview", "import"):
                a = text.index("location = /%s/api/backup/%s {" % (secret, p))
                self.assertIn("client_max_body_size 20m;", text[a:text.index("}", a)])

    def test_the_buffers_of_every_location_pass_the_check_of_nginx(self):
        """Stand T-3310 B-1: ``proxy_buffer_size 64k`` alone was refused by ``nginx -t`` (1.24, Ubuntu 24.04) even
        with ``proxy_buffering off``, so the refresh kept the old vhost without the speed locations. The rule of
        ngx_http_proxy_merge_loc_conf, offline (no nginx here): size = max(proxy_buffer_size, one of proxy_buffers);
        proxy_busy_buffers_size (default 2 × size) must be ≥ size and ≤ all proxy_buffers minus one; ≥ 2 buffers.
        The defaults — 4k and 8 × 4k (the page; 8k only makes the default pass easier)."""
        unit = {"k": 1024, "m": 1024 * 1024}
        size_of = lambda v: int(v[:-1]) * unit[v[-1]] if v[-1] in unit else int(v)
        secret = "app-" + "A" * 86
        for kw in ({"version": "1.24.0"}, {"version": "1.26.0", "behind": "/run/kaskad-vless/site.sock"}):
            text = nginx.render(DOMAIN, secret, ipv6=False, **kw)
            blocks = re.findall(r"location ([^{]+)\{([^}]*)\}", text)
            self.assertIn("= /%s/api/host/speed/down " % secret, [b[0] for b in blocks])
            for name, body in blocks:
                got = dict(ln.strip().rstrip(";").split(None, 1) for ln in body.splitlines()
                           if ln.strip().startswith("proxy_bu"))
                one = size_of(got.get("proxy_buffer_size", "4k"))
                num, each = got.get("proxy_buffers", "8 4k").split()
                num, each = int(num), size_of(each)
                size = max(one, each)
                busy = size_of(got["proxy_busy_buffers_size"]) if "proxy_busy_buffers_size" in got else 2 * size
                where = "%s %s" % (kw, name.strip())
                self.assertGreaterEqual(num, 2, where)
                self.assertGreaterEqual(busy, size, where)
                self.assertLessEqual(busy, (num - 1) * each, "%s: nginx -t: proxy_busy_buffers_size %d > "
                                     "%d × %d (B-1)" % (where, busy, num - 1, each))

    def test_vhost_with_the_speed_locations_passes_real_nginx_test_when_available(self):
        """B-1 of stand T-3310: the real ``nginx -t`` (tests/realbin.py; $KASKAD_TEST_NGINX or nginx on PATH) on the
        vhost with both speed locations — in front (TLS) and behind xray, the version of this very nginx. No nginx
        (the Mac) — skipped; on a server with nginx — checked."""
        from tests import realbin
        nginx_bin = realbin.nginx_path()
        if not nginx_bin:
            self.skipTest("SKIP нет nginx: vhost с замером из браузера не проверен настоящим nginx -t "
                          "(поставьте nginx или KASKAD_TEST_NGINX)")
        res = subprocess.run([nginx_bin, "-v"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        found = re.search(r"nginx/(\d+\.\d+\.\d+)", res.stdout.decode("utf-8", "replace"))
        version = found.group(1) if found else None
        secret = "app-" + "A" * 86
        with tempfile.TemporaryDirectory(prefix="kaskad-nginx-") as work:
            for behind in (None, os.path.join(work, "site.sock")):
                text = nginx.render(DOMAIN, secret, version=version, ipv6=False, behind=behind)
                for path in ("api/host/speed/up", "api/host/speed/down"):
                    self.assertEqual(text.count("location = /%s/%s {" % (secret, path)), 1, path)
                ok, out = realbin.nginx_check(text)
                self.assertTrue(ok, "nginx %s, %s: nginx -t не принял vhost:\n%s"
                                % (version, "за xray" if behind else "на 443", out[-1500:]))

    def test_ipv6_is_detected_from_the_kernel_table(self):
        self.write("/proc/net/if_inet6", "00000000000000000000000000000001 01 80 10 lo\n")
        self.assertTrue(nginx.has_ipv6())
        self.write("/proc/net/if_inet6", "")
        self.assertFalse(nginx.has_ipv6())

    def test_vhost_uses_current_certificate_paths_and_native_standard_404(self):
        cert = "/etc/kaskad/site/tls/current/fullchain.pem"
        key = "/etc/kaskad/site/tls/current/privkey.pem"
        with mock.patch.object(model, "fullchain_path", return_value=cert), \
                mock.patch.object(model, "privkey_path", return_value=key):
            text = nginx.render(DOMAIN, "app-" + "A" * 86,
                                version="1.25.1", ipv6=True)
        self.assertIn("ssl_certificate %s;" % cert, text)
        self.assertIn("ssl_certificate_key %s;" % key, text)
        self.assertIn("listen [::]:443 ssl;\n    http2 on;", text)
        self.assertNotIn("/etc/letsencrypt/live/", text)
        self.assertNotIn("error_page", text)
        decoy = text.split('    location ~ "^/(app-', 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("access_log off;", decoy)
        self.assertIn("error_log /dev/null crit;", decoy)
        self.assertIn("return 404;", decoy)
        self.assertNotIn("try_files", decoy)
        self.assertNotIn("@", decoy)
        normal = text.rsplit("    location / {\n", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("try_files $uri $uri/ =404;", normal)

    def test_write_is_0600_and_a_bad_nginx_test_restores_the_previous_file(self):
        old = "foreign old file\n"
        self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
        self.write("/etc/nginx/sites-enabled/kaskad", "old link\n", 0o600)
        self.nginx_test_rc = 1
        with self.assertRaises(nginx.NginxTestError) as e:
            nginx.write(self.r, DOMAIN, "app-" + "Z" * 86)
        self.assertNotIn("Z" * 86, str(e.exception))
        self.assertEqual(util.read_text(nginx.vhost_path()), old)
        self.assertEqual(util.read_text(nginx.enabled_path()), "old link\n")
        self.assertEqual(self.r.unmatched, [])

    def test_write_marks_an_error_from_our_vhost_and_restores_it(self):
        old = "foreign old file\n"
        self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
        self.nginx_test_rc = 1
        self.nginx_test_err = 'nginx: [emerg] bad site in /etc/nginx/sites-enabled/kaskad:37\n'
        with self.assertRaises(nginx.NginxTestError) as e:
            nginx.write(self.r, DOMAIN, "app-" + "A" * 86)
        self.assertTrue(e.exception.ours)
        self.assertEqual(util.read_text(nginx.vhost_path()), old)

    def test_write_refuses_a_nginx_config_that_omits_sites_enabled(self):
        old = "foreign old file\n"
        self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
        self.nginx_dump = "# configuration file /etc/nginx/nginx.conf:\nhttp {}\n"
        with self.assertRaises(nginx.NginxTestError) as e:
            nginx.write(self.r, DOMAIN, "app-" + "A" * 86)
        self.assertFalse(e.exception.ours)
        self.assertIn("не подключает", str(e.exception))
        self.assertEqual(util.read_text(nginx.vhost_path()), old)

    def test_nginx_refusal_says_whether_the_failed_file_is_ours(self):
        ours = ops._nginx_test_text(nginx.NginxTestError("/etc/nginx/sites-enabled/kaskad:37", ours=True))
        other = ops._nginx_test_text(nginx.NginxTestError("/etc/nginx/sites-enabled/default:7", ours=False))
        self.assertIn("ошибка в настройках сайта Kaskad — сообщите разработчику, прежний файл возвращён", ours)
        self.assertNotIn("соседа", ours)
        self.assertIn("/etc/nginx/sites-enabled/default:7", other)
        self.assertIn("проверьте конфигурацию nginx соседа", other)

    def test_write_makes_our_vhost_private(self):
        nginx.write(self.r, DOMAIN, "app-" + "A" * 86)
        self.assertTrue(nginx.ours())
        self.assertEqual(stat.S_IMODE(os.stat(nginx.vhost_path()).st_mode), 0o600)
        self.assertTrue(os.path.islink(nginx.enabled_path()))
        self.assertEqual(self.r.unmatched, [])


class RenewalModeTest(SiteCase):
    def test_webroot_and_back_preserve_the_hook_and_restore_exactly(self):
        self.with_domain()
        self.put_live("le")
        before = util.read_text(model.renewal_conf(DOMAIN))
        old = acme.set_renewal_mode(self.w.runner, DOMAIN, "webroot", nginx.acme_root())
        web = util.read_text(model.renewal_conf(DOMAIN))
        self.assertEqual(old, before)
        self.assertIn("authenticator = webroot", web)
        self.assertIn("[[webroot_map]]", web)
        self.assertIn("renew_hook", web)
        acme.set_renewal_mode(self.w.runner, DOMAIN, "standalone")
        self.assertEqual(acme.renewal_mode(DOMAIN), "standalone")
        acme.restore_renewal(DOMAIN, old)
        self.assertEqual(util.read_text(model.renewal_conf(DOMAIN)), before)


class SiteModeTest(SiteCase):
    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def _patch_site(self):
        deployed = {"ok": True, "id": "demo", "name": "Демо", "source": "https://example.invalid/demo",
                    "fallback": False, "root": "/var/www/kaskad-site"}
        return mock.patch.multiple(nginx, ensure=mock.DEFAULT, write=mock.DEFAULT,
                                   can_reject_handshake=mock.DEFAULT, ufw_allow443=mock.DEFAULT,
                                   ufw_delete443=mock.DEFAULT, remove=mock.DEFAULT, ours=mock.DEFAULT), deployed

    def test_transitions_and_the_panel_path_stays_masked(self):
        from kaskad.site import templates
        with mock.patch.object(nginx, "ensure", return_value=[]), \
                mock.patch.object(nginx, "write", return_value="start"), \
                mock.patch.object(nginx, "can_reject_handshake", return_value=True), \
                mock.patch.object(nginx, "ufw_allow443", return_value=False), \
                mock.patch.object(nginx, "ours", return_value=True), \
                mock.patch.object(nginx, "remove", return_value="reload"), \
                mock.patch.object(nginx, "ufw_delete443", return_value=False), \
                mock.patch.object(templates, "deploy", return_value={"ok": True, "id": "demo", "name": "Демо",
                                                                        "source": "repo", "fallback": False}), \
                mock.patch.object(consumers, "_reload_panel", return_value=False):
            got = ops.mode(self.w.runner, "site")
            self.assertEqual(got["site"]["mode"], "site")
            masked = ops.panel_url()["url"]
            self.assertIn("••••", masked)
            self.assertNotIn(conf.load()["proxy"]["path"], masked)
            self.assertEqual(ops.mode(self.w.runner, "domain")["site"]["mode"], "domain")
            self.assertFalse(conf.load()["proxy"]["on"])

    def test_refusals_name_the_reason(self):
        ops.clear(self.w.runner)
        with self.assertRaises(ops.Refusal) as e:
            ops.mode(self.w.runner, "site")
        self.assertEqual((e.exception.doc["reason"], e.exception.code), ("no_domain", CONFLICT))
        ops.set_domain(self.w.runner, DOMAIN)
        with self.assertRaises(ops.Refusal) as e:
            ops.mode(self.w.runner, "site")
        self.assertEqual(e.exception.doc["reason"], "no_cert")


class SiteConflictAndApiTest(SiteCase):
    def test_forward_443_warns_only_while_site_is_enabled(self):
        self.set_site(mode="site")
        found = conflicts.check("tcp", 443, self.w.runner, st=state.load())
        self.assertIn("site", [x["kind"] for x in found])
        self.set_site(mode="domain")
        found = conflicts.check("tcp", 443, self.w.runner, st=state.load())
        self.assertNotIn("site", [x["kind"] for x in found])

    def test_c_face_routes_are_admin_routes_with_the_write_guard(self):
        for method, path in (("PUT", "/api/site/mode"), ("GET", "/api/site/categories"),
                             ("GET", "/api/site/templates"), ("POST", "/api/site/template"),
                             ("GET", "/api/site/panel-url"), ("POST", "/api/site/panel-path/rotate"),
                             ("POST", "/api/site/check"), ("POST", "/api/site/templates/check")):
            route, _ = routes.find(method, path)
            self.assertIsNotNone(route, (method, path))
            self.assertEqual(route.auth, "admin")
            self.assertEqual(route.csrf, method != "GET")

    def test_cli_registers_site_commands_and_refuses_json_without_a_domain(self):
        parser = cli.build_parser()
        site = parser._subparsers._group_actions[0].choices["site"]
        names = set(site._subparsers._group_actions[0].choices)
        self.assertTrue({"mode", "panel-url", "panel-path", "check"}.issubset(names))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["site", "mode", "site", "--json"])
        self.assertEqual(rc, CONFLICT)
        doc = json.loads(out.getvalue())
        self.assertEqual((doc["ok"], doc["reason"]), (False, "no_domain"))


class VhostRefreshTest(NginxCase):
    """BC2a: ``kaskadctl site vhost --refresh`` (k3_post_update) — the vhost of a node with a site gets this version's
    template with the arrangement it has; the same text — no write and no reload; a refused ``nginx -t`` — the previous
    file and no reload; no site — nothing at all."""

    SECRET = "app-" + "Q" * 86

    def setUp(self):
        super().setUp()
        self.active = True
        self.r._handlers.insert(0, (["systemctl", "is-active", "nginx"],
                                    lambda argv, input: (0, "active\n", "") if self.active else (3, "inactive\n", "")))
        self.site({"domain": DOMAIN, "mode": "site", "cert": "letsencrypt"})
        conf.set_proxy(True, path=self.SECRET, domain=DOMAIN)

    def site(self, raw):
        st = state.load()
        st.pop("site", None)
        if raw is not None:
            st["site"] = raw
        state.save(st)

    def old(self, **kw):
        """The vhost as 3.3.7 wrote it: no locations and zones of the browser test."""
        with mock.patch.object(nginx, "EXACT_PATHS", nginx.BACKUP_PATHS), mock.patch.object(nginx, "SPEED_ZONES", ""):
            text = nginx.render(DOMAIN, self.SECRET, version="1.24.0", **kw)
        self.assertNotIn("api/host/speed/up", text)
        self.write("/etc/nginx/sites-available/kaskad", text, 0o600)
        return text

    def nginx_calls(self):
        return [a for a, _ in self.r.calls if a[:2] in (["nginx", "-t"], ["systemctl", "reload"], ["systemctl", "start"])]

    def test_an_old_vhost_gets_the_new_locations_with_its_own_arrangement_then_nothing_more(self):
        for kw in ({"ipv6": True, "reject_handshake": True, "reject80": True},
                   {"ipv6": False, "behind": "/run/kaskad-vless/site.sock", "reject80": True},
                   {"ipv6": False}):
            self.r.calls[:] = []
            old = self.old(**kw)
            self.assertFalse(ops.vhost_refresh(self.r, check=True)["current"], kw)
            self.assertEqual(util.read_text(nginx.vhost_path()), old)                # the check writes nothing
            got = ops.vhost_refresh(self.r)
            self.assertEqual((got["changed"], got["reload"]), (True, "reload"), kw)
            text = util.read_text(nginx.vhost_path())
            self.assertEqual(text, nginx.render(DOMAIN, self.SECRET, version="1.24.0", **kw), kw)
            for path in ("api/host/speed/up", "api/host/speed/down"):
                self.assertEqual(text.count("location = /%s/%s {" % (self.SECRET, path)), 1)
            self.assertEqual(stat.S_IMODE(os.stat(nginx.vhost_path()).st_mode), 0o600)
            self.assertEqual(self.nginx_calls(), [["nginx", "-t"], ["systemctl", "reload", "nginx"]], kw)
            # again, nothing changed: no write, no test, no reload
            self.r.calls[:] = []
            got = ops.vhost_refresh(self.r)
            self.assertEqual((got["changed"], got["reload"]), (False, None))
            self.assertEqual(self.nginx_calls(), [])
            self.assertTrue(ops.vhost_refresh(self.r, check=True)["current"])
        self.assertEqual(self.r.unmatched, [])

    def test_the_check_reads_the_site_once(self):
        # REV-bc4 П-3: «site vhost» без --refresh читал домен и путь дважды — смена между чтениями давала TypeError
        self.old(ipv6=False)
        real, seen = ops.model.peek, []

        def peek(st):
            seen.append(1)
            return real(st) if len(seen) == 1 else dict(real(st), mode="domain")
        with mock.patch.object(ops.model, "peek", peek):
            got = ops.vhost_refresh(self.r, check=True)
        self.assertEqual((got["ok"], got["current"], len(seen)), (True, False, 1))

    def test_a_stopped_nginx_is_not_started(self):
        self.active = False
        self.old(ipv6=False)
        self.assertEqual(ops.vhost_refresh(self.r)["changed"], True)
        self.assertEqual(self.nginx_calls(), [["nginx", "-t"]])

    def test_a_refused_nginx_test_keeps_the_previous_file_and_does_not_reload(self):
        old = self.old(ipv6=False)
        self.nginx_test_rc = 1
        with self.assertRaises(ops.Refusal) as e:
            ops.vhost_refresh(self.r)
        self.assertEqual(e.exception.doc["reason"], "nginx_test")
        self.assertNotIn(self.SECRET, e.exception.doc["text"])
        self.assertEqual(util.read_text(nginx.vhost_path()), old)
        self.assertEqual(self.nginx_calls(), [["nginx", "-t"]])
        # nginx -T does not show our vhost (nginx.conf without sites-enabled): the same
        self.nginx_test_rc = 0
        self.nginx_dump = "# configuration file /etc/nginx/nginx.conf:\nhttp {}\n"
        self.r.calls[:] = []
        with self.assertRaises(ops.Refusal):
            ops.vhost_refresh(self.r)
        self.assertEqual(util.read_text(nginx.vhost_path()), old)
        self.assertNotIn(["systemctl", "reload", "nginx"], self.nginx_calls())

    def test_without_a_site_nothing_happens(self):
        old = self.old(ipv6=False)
        for raw in (None, {"domain": DOMAIN, "mode": "domain", "cert": "letsencrypt"}, {"domain": "", "mode": "site"}):
            self.site(raw)
            self.r.calls[:] = []
            self.assertEqual(ops.vhost_refresh(self.r), {"ok": True, "changed": False, "skipped": "no_site"})
            self.assertEqual(self.r.calls, [])
        self.site({"domain": DOMAIN, "mode": "site", "cert": "letsencrypt"})
        # not our vhost, another domain, another panel path (a change still in progress), no nginx
        for text in ("server { listen 80; }\n", old.replace("server_name %s;" % DOMAIN, "server_name other.example.com;"),
                     old.replace(self.SECRET, "app-" + "R" * 86)):
            self.write("/etc/nginx/sites-available/kaskad", text, 0o600)
            got = ops.vhost_refresh(self.r)
            self.assertFalse(got["changed"])
            self.assertIn(got["skipped"], ("no_site", "foreign"))
            self.assertEqual(util.read_text(nginx.vhost_path()), text)
            self.assertEqual(self.nginx_calls(), [])
        self.r.binaries.discard("nginx")
        self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
        self.assertEqual(ops.vhost_refresh(self.r)["skipped"], "no_site")
        self.assertEqual(util.read_text(nginx.vhost_path()), old)

    def test_cli_vhost_refresh_quiet_and_json(self):
        from kaskad.site import cli as site_cli
        self.old(ipv6=False)
        with mock.patch.object(site_cli, "_r", lambda: self.r):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["site", "vhost", "--refresh", "--quiet"]), 0)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("api/host/speed/up", util.read_text(nginx.vhost_path()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["site", "vhost", "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), {"ok": True, "changed": False, "current": True})
            self.site(None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["site", "vhost", "--refresh", "--quiet"]), 0)
            self.assertEqual(out.getvalue(), "")

    def test_a_failed_reload_puts_the_previous_file_back_and_the_next_refresh_tries_again(self):
        """REV-bc12-2 З-1: ``nginx -t`` passed, ``systemctl reload`` failed — the file on disk stays what the running
        nginx serves (the text «the previous file is kept» is true), and the next refresh writes and reloads again."""
        old = self.old(ipv6=False)
        rc = [1]
        self.r._handlers.insert(0, (["systemctl", "reload", "nginx"],
                                    lambda argv, input: (rc[0], "", "Job for nginx.service failed" if rc[0] else "")))
        with i18n.use_lang("ru"):
            with self.assertRaises(ops.KaskadError) as e:
                ops.vhost_refresh(self.r)
        self.assertNotIsInstance(e.exception, ops.Refusal)
        self.assertIn("прежний файл оставлен", str(e.exception))
        self.assertIn("Job for nginx.service failed", str(e.exception))
        self.assertEqual(util.read_text(nginx.vhost_path()), old)
        self.assertEqual(stat.S_IMODE(os.stat(nginx.vhost_path()).st_mode), 0o600)
        self.assertFalse(ops.vhost_refresh(self.r, check=True)["current"])
        rc[0] = 0
        self.r.calls[:] = []
        got = ops.vhost_refresh(self.r)
        self.assertEqual((got["changed"], got["reload"]), (True, "reload"))
        self.assertIn("api/host/speed/up", util.read_text(nginx.vhost_path()))
        self.assertEqual(self.nginx_calls(), [["nginx", "-t"], ["systemctl", "reload", "nginx"]])

    def test_cli_texts_tell_a_stopped_nginx_and_a_foreign_vhost_apart(self):
        """REV-bc12-2 П-2: no «nginx has reloaded them» when nginx is stopped; another domain or path is not «no site»."""
        from kaskad.site import cli as site_cli

        def run(*argv):
            out = io.StringIO()
            with i18n.use_lang("ru"), mock.patch.object(site_cli, "_r", lambda: self.r), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["site", "vhost"] + list(argv)), 0)
            return out.getvalue()
        self.active = False
        old = self.old(ipv6=False)
        text = run("--refresh")
        self.assertIn("nginx остановлен", text)
        self.assertNotIn("перечитал", text)
        self.write("/etc/nginx/sites-available/kaskad", old.replace(self.SECRET, "app-" + "R" * 86), 0o600)
        for argv in ((), ("--refresh",)):
            text = run(*argv)
            self.assertIn("для другого домена или пути панели", text)
            self.assertNotIn("Сайта на домене нет", text)
        self.active = True
        self.old(ipv6=False)
        self.assertIn("nginx перечитал", run("--refresh"))

    def v3379(self, name):
        """The vhost exactly as 3.3.7.9 (a160bcc6) wrote it — tests/site/vhost_3379.json, rendered by that version's
        nginx.py with placeholders for the paths of this test root."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vhost_3379.json"), encoding="utf-8") as f:
            fx = json.load(f)[name]
        text = fx["text"].replace("@ACME@", nginx.acme_root()).replace("@ROOT@", paths.site_root()) \
            .replace("@CERT@", model.fullchain_path()).replace("@KEY@", model.privkey_path()) \
            .replace("@SOCK@", conf.proxy_sock_path()).replace("domain.placeholder.example", DOMAIN)
        self.assertNotIn("@", text.replace("$remote_addr", "").replace("$host", "").replace("$server_name", ""))
        return fx["kw"], text

    def test_update_from_3379_adds_only_the_speed_lines_by_a_reload(self):
        """Миграция BC2a (правило владельца 24.10): узел 3.3.7.9 после обновления — vhost этой версии; из прежнего не
        уходит ни одной строки (вход в панель, страницы, отказы — те же), добавлены только зоны и два location замера;
        nginx перечитывает файл (reload), а не перезапускается — панель не пропадает."""
        for name in ("v6_reject", "behind", "plain"):
            kw, old = self.v3379(name)
            self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
            self.r.calls[:] = []
            self.assertFalse(ops.vhost_refresh(self.r, check=True)["current"], name)
            got = ops.vhost_refresh(self.r)
            self.assertEqual((got["changed"], got["reload"]), (True, "reload"), name)
            new = util.read_text(nginx.vhost_path())
            self.assertEqual(new, nginx.render(DOMAIN, self.SECRET, version="1.24.0", **kw), name)
            self.assertEqual(self.nginx_calls(), [["nginx", "-t"], ["systemctl", "reload", "nginx"]], name)
            import difflib
            diff = list(difflib.ndiff(old.splitlines(), new.splitlines()))
            self.assertEqual([x for x in diff if x.startswith("- ")], [], name)
            added = "\n".join(x[2:] for x in diff if x.startswith("+ "))
            for path in ("api/host/speed/up", "api/host/speed/down"):
                self.assertIn("location = /%s/%s {" % (self.SECRET, path), added)
            self.assertIn(nginx.SPEED_ZONES, added)
            self.assertNotIn("location ^~", added)                                # ни одного нового общего входа
            self.assertEqual(stat.S_IMODE(os.stat(nginx.vhost_path()).st_mode), 0o600)

    def test_downgrade_to_3379_leaves_a_valid_vhost(self):
        """Откат на 3.3.7.9: её k3_post_update vhost не трогает — новый файл остаётся и работает сам по себе: зоны
        замера объявлены в нём же (до location), их имена нигде больше не нужны; оба location ведут в тот же сокет
        панели, что и общий вход, — старая панель отвечает на них 404 (маршрута нет), остальная панель — как была.
        Любая запись vhost самой 3.3.7.9 (смена пути, режима) кладёт её шаблон без этих строк — и обновление снова
        их добавит."""
        kw, old = self.v3379("plain")
        new = nginx.render(DOMAIN, self.SECRET, version="1.24.0", **kw)
        for zone in ("kaskad_speed", "kaskad_speed_ip"):
            self.assertLess(new.index("zone=%s:" % zone), new.index("limit_conn %s " % zone))
        main = re.search(r"location \^~ /%s/ \{\n    proxy_pass http://unix:([^:]+):/;" % self.SECRET, new)
        self.assertIsNotNone(main)
        for path in ("api/host/speed/up", "api/host/speed/down"):
            m = re.search(r"location = /%s/%s \{\n    proxy_pass http://unix:([^:]+):/%s;" % (self.SECRET, re.escape(path), re.escape(path)), new)
            self.assertIsNotNone(m, path)
            self.assertEqual(m.group(1), main.group(1), path)
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
        users = []
        for dp, _dn, fn in os.walk(lib):
            for f in fn:
                if f.endswith((".py", ".sh", ".conf", ".tmpl")):
                    with open(os.path.join(dp, f), encoding="utf-8", errors="replace") as h:
                        if "kaskad_speed" in h.read():
                            users.append(os.path.relpath(os.path.join(dp, f), lib))
        self.assertEqual(users, [os.path.join("kaskad", "site", "nginx.py")])
        # 3.3.7.9 переписала vhost своим шаблоном — следующее обновление снова добавляет замер
        self.write("/etc/nginx/sites-available/kaskad", old, 0o600)
        got = ops.vhost_refresh(self.r)
        self.assertEqual((got["changed"], got["reload"]), (True, "reload"))
        self.assertIn("api/host/speed/up", util.read_text(nginx.vhost_path()))

    def test_every_write_of_the_site_vhost_takes_the_refresh_lock_and_refresh_reads_the_path_under_it(self):
        """REV-bc12-2 П-4: a new panel path written between a refresh's read and its write would be overwritten by the
        old path — ``_write_site`` takes the same lock, and refresh reads the domain and the path under it."""
        held = []

        @contextlib.contextmanager
        def lock():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        seen = []
        with mock.patch.object(ops, "_vhost_lock", lock), \
                mock.patch.object(nginx, "write", lambda *a, **kw: seen.append(bool(held))), \
                mock.patch.object(nginx, "enable", lambda runner: None), \
                mock.patch.object(ops.front, "xray_fronts", lambda runner: False):
            ops._write_site(self.r, DOMAIN, self.SECRET)
        self.assertEqual(seen, [True])
        # a new path lands while refresh waits for the lock: refresh renders it, not the path it saw before
        self.old(ipv6=False)
        new_path = "app-" + "N" * 86

        @contextlib.contextmanager
        def rotated_meanwhile():
            with mock.patch.object(nginx, "EXACT_PATHS", nginx.BACKUP_PATHS), \
                    mock.patch.object(nginx, "SPEED_ZONES", ""):
                text = nginx.render(DOMAIN, new_path, version="1.24.0", ipv6=False)
            self.write("/etc/nginx/sites-available/kaskad", text, 0o600)
            conf.set_proxy(True, path=new_path, domain=DOMAIN)
            yield
        with mock.patch.object(ops, "_vhost_lock", rotated_meanwhile):
            got = ops.vhost_refresh(self.r)
        self.assertEqual(got["changed"], True)
        self.assertEqual(util.read_text(nginx.vhost_path()),
                         nginx.render(DOMAIN, new_path, version="1.24.0", ipv6=False))
