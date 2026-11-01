"""Every config of xray Kaskad generates goes through the REAL xray (T-M6-qs330b №1: the rule ``::ffff:0:0/96`` passed
2270 tests and the fake ``xray -test``; the real xray refused the whole config and VLESS never listened on a clean node).

The binary: tests/realbin.py (``bash tools/fetch_test_bins.sh`` puts xray of install.sh's fallback version into the cache;
tools/k3_test.sh does it). No binary — every test here is SKIPPED with the reason in its name, never green silently;
tools/k3_test.sh prints that line too. Besides this module, the fake xray of all the other tests asks the real one
(tests/fakes_gw.FakeXray) — the configs of every scenario are checked, not only these.

    the gateway: the pool (VLESS Reality and WS+TLS exits, AmneziaWG legs), SOCKS5 with the lists of «Россия напрямую»
    (ext:roscomvpn-*.dat), the VLESS inbound — Reality and TLS in front of the site; the proxy of the Telegram bot
    (install.sh, egress_parse.py ``config``) for every kind of key it reads; nginx — the vhost of the site in front of
    and behind xray, when nginx is on this machine."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import secrets
from kaskad.gw import exits, xray
from kaskad.gw.inbounds import vless as vin
from kaskad.rules import apply as rapply
from kaskad.site import ops as site_ops
from tests import realbin
from tests.fakes_gw import VLESS_A, VLESS_B
from tests.test_gw_vless_front import FrontCase
from tests import test_rules_gw
from tests.test_rules_gw import RulesGwCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_XRAY = "SKIP нет xray: конфиги xray не проверены настоящим xray (bash tools/fetch_test_bins.sh)"
NO_NGINX = "SKIP нет nginx: vhost сайта не проверен настоящим nginx -t (поставьте nginx или KASKAD_TEST_NGINX)"
HAS_XRAY = realbin.xray_path() is not None
HAS_NGINX = realbin.nginx_path() is not None


class RealXrayMixin(object):
    def assertXrayAccepts(self, cfg, what):
        got = realbin.xray_check(cfg)
        self.assertIsNotNone(got, NO_XRAY)
        ok, text = got
        self.assertTrue(ok, "%s: настоящий xray не принял конфиг:\n%s" % (what, text[-1500:]))


@unittest.skipUnless(HAS_XRAY, NO_XRAY)
class ValidatorWorksTest(RealXrayMixin, unittest.TestCase):
    def test_the_prefix_of_t_m6_is_refused_and_the_fixed_rule_is_taken(self):
        cfg = {"outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
               "routing": {"rules": [{"type": "field", "ip": ["::ffff:0:0/96"] + xray.blocked_ips(),
                                      "outboundTag": "block"}]}}
        ok, text = realbin.xray_check(cfg)
        self.assertFalse(ok)
        self.assertIn("invalid network mask for router: 96", text)
        cfg["routing"]["rules"][0]["ip"] = xray.blocked_ips(["203.0.113.10/32", "2001:db8::10/128"])
        self.assertXrayAccepts(cfg, "blocked_ips")


@unittest.skipUnless(HAS_XRAY, NO_XRAY)
class GatewayXrayTest(RealXrayMixin, RulesGwCase):
    socks_state = test_rules_gw.XrayTest.socks_state

    def test_pool_with_vless_and_awg_exits(self):
        st = self.scenario(save=False)
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner), ["203.0.113.10/32", "2001:db8::10/128"])
        self.assertTrue([o for o in cfg["outbounds"] if o.get("protocol") == "vless"])
        self.assertXrayAccepts(cfg, "пул шлюза (VLESS Reality, AmneziaWG)")

    def test_socks5_with_the_lists_of_rules(self):
        st = self.socks_state()
        sec = secrets.load()
        self.assertTrue(rapply.sync_assets(st, sec, list(xray._adapters(st)))["changed"])
        cfg = xray.render(st, sec, exits.pool(self.w.runner))
        self.assertIn("ext:roscomvpn-geoip.dat:", json.dumps(cfg))
        self.assertXrayAccepts(cfg, "SOCKS5 и списки правил")


@unittest.skipUnless(HAS_XRAY, NO_XRAY)
class VlessInboundXrayTest(RealXrayMixin, FrontCase):
    def test_reality_then_tls_in_front_of_the_site(self):
        self.assertEqual(self.vless_ib()["streamSettings"]["security"], "reality")
        self.assertXrayAccepts(self.cfg(), "вход VLESS Reality")
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.vless_ib()["streamSettings"]["security"], "tls")
        self.assertXrayAccepts(self.cfg(), "вход VLESS TLS перед сайтом")
        # the fake xray of the scenario asked the real one on every config it was given — and it took all of them
        self.assertTrue(self.w.xray.real)
        self.assertTrue(all(self.w.xray.real), self.w.xray.real)


def _parser(tmp):
    """egress_parse.py of install.sh (egress_ensure_parser into ``tmp``) — as tools/egress_parser_selftest.sh gets it."""
    env = dict(os.environ, EG=tmp, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
    p = subprocess.run(["bash", "-c", 'source "$1" --lib >/dev/null 2>&1 || exit 97; EGRESS_DIR="$EG"; '
                        'EGRESS_PARSE_PY="$EG/egress_parse.py"; egress_ensure_parser', "_",
                        os.path.join(ROOT, "install.sh")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120)
    path = os.path.join(tmp, "egress_parse.py")
    return path if p.returncode == 0 and os.path.exists(path) else None


@unittest.skipUnless(HAS_XRAY, NO_XRAY)
class BotEgressXrayTest(RealXrayMixin, unittest.TestCase):
    def test_every_kind_of_key_of_the_bot_proxy(self):
        pbk = vin.public_of(vin.new_private())
        keys = [
            VLESS_A, VLESS_B,
            "vless://11111111-2222-3333-4444-555555555556@198.51.100.30:443?type=tcp&security=reality&pbk=%s"
            "&sid=0123abcd&sni=www.microsoft.com&fp=chrome&flow=xtls-rprx-vision#reality" % pbk,
            "vless://11111111-2222-3333-4444-555555555557@vpn.example.org:443?type=tcp&security=tls"
            "&sni=vpn.example.org&alpn=http%2F1.1&fp=chrome&flow=xtls-rprx-vision#tls",
            "vless://11111111-2222-3333-4444-555555555558@vpn.example.org:443?type=grpc&security=tls"
            "&serviceName=gs&sni=vpn.example.org#grpc",
            "vless://11111111-2222-3333-4444-555555555559@vpn.example.org:443?type=xhttp&security=tls"
            "&path=%2Fx&host=vpn.example.org&mode=auto&sni=vpn.example.org#xhttp",
            "vless://11111111-2222-3333-4444-55555555555a@198.51.100.31:80?type=ws&security=none&path=%2Fws"
            "&host=cdn.example.org#ws-plain",
        ]
        with tempfile.TemporaryDirectory(prefix="kaskad-egress-") as tmp:
            parser = _parser(tmp)
            self.assertIsNotNone(parser, "install.sh не записал egress_parse.py")
            run = lambda args, text: subprocess.run(["python3", "-I", parser] + args, input=text.encode("utf-8"),
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            got = run(["extract"], "\n".join(keys) + "\n")
            lines = [ln for ln in got.stdout.decode("utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), len(keys), got.stderr.decode("utf-8", "replace"))
            for i, one in enumerate([[ln] for ln in lines] + [lines]):          # one key (no balancer) and the pool
                p = run(["config", "--mss", "1000"], "\n".join(one) + "\n")
                self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
                self.assertXrayAccepts(json.loads(p.stdout.decode("utf-8")),
                                       "прокси бота: %s" % ("весь пул" if len(one) > 1 else keys[i].split("#")[-1]))


@unittest.skipUnless(HAS_NGINX, NO_NGINX)
class NginxVhostTest(FrontCase):
    def test_vhost_alone_and_behind_xray(self):
        for what in ("сайт на 443", "сайт за xray"):
            if what == "сайт за xray":
                site_ops.mode(self.w.runner, "site", yes=True)
            ok, text = realbin.nginx_check(self.vhost())
            self.assertTrue(ok, "%s: nginx -t не принял vhost:\n%s" % (what, text[-1500:]))


# A stand-in for nginx: -V — the version and configure line of $FAKE_NGINX_V; -t — the argv and every file of the
# prefix (-p) copied to $FAKE_NGINX_OUT, rc 0.
_FAKE_NGINX = """#!/bin/sh
if [ "$1" = "-V" ]; then printf '%s\\n' "$FAKE_NGINX_V" >&2; exit 0; fi
printf '%s\\n' "$@" > "$FAKE_NGINX_OUT/argv"
prev=""; for a in "$@"; do [ "$prev" = "-p" ] && cp -R "$a" "$FAKE_NGINX_OUT/prefix"; prev="$a"; done
echo "nginx: configuration file test is successful"; exit 0
"""


class NginxSandboxTest(unittest.TestCase):
    """T-3310b: ``nginx -t`` of root makes the temp paths of the config and chowns them to its user (nobody without
    ``user``); with the paths built in (/var/lib/nginx/body, proxy, fastcgi, uwsgi, scgi) the nginx of the server
    could no longer write its temp files. realbin.nginx_check — every path nginx writes inside its temporary folder.
    A stand-in nginx (no nginx needed): what it was given is checked, not the real chown."""

    def check(self, version_line, *vhosts):
        with tempfile.TemporaryDirectory(prefix="kaskad-fake-nginx-") as work:
            fake, out = os.path.join(work, "nginx"), os.path.join(work, "out")
            os.makedirs(out)
            with open(fake, "w", encoding="utf-8") as f:
                f.write(_FAKE_NGINX)
            os.chmod(fake, 0o755)
            env = {"KASKAD_TEST_NGINX": fake, "FAKE_NGINX_OUT": out, "FAKE_NGINX_V": version_line}
            with mock.patch.dict(os.environ, env):
                ok, text = realbin.nginx_check(*vhosts)
            self.assertTrue(ok, text)
            with open(os.path.join(out, "argv"), encoding="utf-8") as f:
                argv = f.read().split("\n")[:-1]
            folder = argv[argv.index("-p") + 1]
            self.assertFalse(os.path.exists(folder), "временная папка nginx -t не удалена")
            files = {}
            for name in sorted(os.listdir(os.path.join(out, "prefix"))):
                if name.endswith(".conf"):
                    with open(os.path.join(out, "prefix", name), encoding="utf-8") as f:
                        files[name] = f.read()
            return folder, argv, files

    def test_every_path_nginx_writes_is_inside_the_temporary_folder(self):
        vhost = "server {\n    listen 18443 ssl;\n    ssl_certificate /etc/letsencrypt/live/x/fullchain.pem;\n" \
                "    ssl_certificate_key /etc/letsencrypt/live/x/privkey.pem;\n}\n"
        folder, argv, files = self.check("nginx version: nginx/1.24.0 (Ubuntu)\nconfigure arguments: "
                                         "--http-client-body-temp-path=/var/lib/nginx/body", vhost, "server {}\n")
        conf = os.path.join(folder, "nginx.conf")
        self.assertEqual(argv, ["-t", "-p", folder, "-c", conf, "-e", os.path.join(folder, "logs", "error.log")])
        self.assertEqual(sorted(files), ["nginx.conf", "vhost.conf", "vhost1.conf"])
        main = files["nginx.conf"]
        for directive in ("pid", "lock_file", "error_log", "client_body_temp_path", "proxy_temp_path",
                          "fastcgi_temp_path", "uwsgi_temp_path", "scgi_temp_path"):
            self.assertEqual(len(re.findall(r"(?m)^\s*%s %s/\S+;$" % (directive, re.escape(folder)), main)), 1,
                             directive)
        for path in re.findall(r"(/[^\s;]+)", main):
            self.assertTrue(path.startswith(folder + "/"), "nginx.conf проверки: путь вне временной папки: " + path)
        self.assertNotIn("/var/lib/nginx", main)
        self.assertNotIn("/etc/letsencrypt", files["vhost.conf"])               # the fixture pair instead
        self.assertIn("ssl_certificate %s;" % realbin.CERT, files["vhost.conf"])

    def test_an_old_nginx_without_e_and_a_build_without_modules(self):
        folder, argv, files = self.check("nginx version: nginx/1.18.0\nconfigure arguments: "
                                         "--without-http_uwsgi_module --without-http_scgi_module", "server {}\n")
        self.assertNotIn("-e", argv)                                           # -e: nginx 1.19.5 and later
        main = files["nginx.conf"]
        for gone in ("uwsgi_temp_path", "scgi_temp_path"):                    # unknown directive in such a build
            self.assertNotIn(gone, main)
        for kept in ("client_body_temp_path", "proxy_temp_path", "fastcgi_temp_path"):
            self.assertIn("%s %s/" % (kept, folder), main)


if __name__ == "__main__":
    unittest.main()
