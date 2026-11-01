"""``kaskadctl panel …`` (WP-P1): password from stdin, one-time links, status, the unit, the open mode."""

import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli, errors, paths, secrets, state
from kaskad.gw.inbounds import pki
from kaskad.panel import auth, conf
from kaskad.panel import cli as pcli
from tests.gwcase import GwCase
from tests.panel.panelcase import fixture


class PanelCliTest(GwCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(auth, "SCRYPT_N", 1 << 10)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_password_from_stdin_never_argv(self):
        rc, out, err = self.run_cli("panel", "password", stdin="очень-секретный-1\n")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("очень-секретный-1", out + err)
        self.assertTrue(auth.verify("admin", "очень-секретный-1"))
        self.assertNotIn("очень-секретный-1", self.read("/var/log/kaskad.log"))
        rc, out, err = self.run_cli("panel", "password", "--login", "boss", "--generate", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["login"]), (0, "boss"))
        self.assertTrue(auth.verify("boss", doc["password"]))
        rc, out, err = self.run_cli("panel", "password", stdin="short\n")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("от 10 до 200 символов", err)

    def test_link_and_login_link(self):
        rc, out, err = self.run_cli("panel", "link", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["expires_in"], doc["public"]), (0, 3600, False))
        self.assertTrue(doc["url"].startswith("http://127.0.0.1:1980/login?t="))
        token = doc["url"].split("t=", 1)[1]
        self.assertTrue(auth.redeem_link(token))
        self.assertFalse(auth.redeem_link(token))
        rc, out, err = self.run_cli("panel", "link")
        self.assertIn("ssh -L 1980:127.0.0.1:1980 root@", out)
        got = auth.login_link(ttl=600)
        self.assertEqual((got["url"], got["ttl"]), (None, 600))                 # no unit: nowhere to go
        self.write("/etc/systemd/system/kaskad-panel.service", pcli.unit_text(), 0o644)
        got = auth.login_link(ttl=5)
        self.assertEqual(got["ttl"], 60)
        self.assertEqual(got["url"], "http://127.0.0.1:1980/login?t=" + got["token"])
        self.assertTrue(auth.redeem_link(got["token"]))

    def test_install_status_remove(self):
        rc, out, err = self.run_cli("panel", "status", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["installed"], doc["active"], doc["mode"], doc["sessions"]),
                         (0, False, False, "local", None))
        rc, out, err = self.run_cli("panel", "install")
        self.assertEqual(rc, 0, err)
        text = self.read("/etc/systemd/system/kaskad-panel.service")
        for line in ("Type=notify", "NotifyAccess=main", "ExecStart=%s panel serve" % paths.kaskadctl(), "Restart=on-failure", "Nice=10",
                     "CPUQuota=25%", "MemoryMax=384M", "NoNewPrivileges=yes", "ExecReload=/bin/kill -HUP $MAINPID"):
            self.assertIn(line + "\n", text)
        self.assertEqual(self.w.sysd.units["kaskad-panel.service"], {"enabled": True, "active": True})
        conf.write_status({"pid": 4242, "listen": [{"kind": "local", "addr": "127.0.0.1:1980", "scheme": "http"}],
                           "sessions": 2})
        rc, out, err = self.run_cli("panel", "status", "--json")
        doc = json.loads(out)
        self.assertEqual((doc["installed"], doc["active"], doc["sessions"], doc["pid"]), (True, True, 2, 4242))
        rc, out, err = self.run_cli("panel", "status")
        self.assertIn("Панель работает: http://127.0.0.1:1980.", out)
        self.assertIn("Пароль: не задан.", out)
        rc, out, err = self.run_cli("panel", "remove", "--purge")
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(self.path("/etc/systemd/system/kaskad-panel.service")))
        self.assertFalse(os.path.exists(paths.panel_dir()))

    def test_unit_refresh_rewrites_only_a_different_installed_unit(self):
        path = "/etc/systemd/system/kaskad-panel.service"
        self.write(path, "[Service]\nType=simple\nExecStart=/old/panel\n", 0o644)
        rc, out, err = self.run_cli("panel", "unit", "--refresh", "--quiet")
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.read(path), pcli.unit_text())
        calls = [argv for argv, _ in self.w.runner.calls if argv[0] == "systemctl"]
        self.assertEqual(calls, [["systemctl", "daemon-reload"]])
        rc, out, err = self.run_cli("panel", "unit", "--refresh", "--quiet")
        self.assertEqual((rc, out, err), (0, "", ""))
        calls = [argv for argv, _ in self.w.runner.calls if argv[0] == "systemctl"]
        self.assertEqual(calls, [["systemctl", "daemon-reload"]])

    def test_reload_waits_for_the_notify_start_then_hups(self):
        """A public-mode change immediately after install must not HUP Python before its handler exists."""
        self.write("/etc/systemd/system/" + conf.UNIT, pcli.unit_text(), 0o644)
        self.w.sysd.units[conf.UNIT] = {"enabled": True, "active": False, "activating": True}

        def ready(_seconds):
            self.w.sysd.units[conf.UNIT].pop("activating")
            self.w.sysd.units[conf.UNIT]["active"] = True

        with mock.patch.object(conf, "_sleep", ready):
            self.assertTrue(conf.reload_service(self.w.runner))
        calls = [argv for argv, _ in self.w.runner.calls if argv[0] == "systemctl"]
        self.assertEqual(calls[-3:], [["systemctl", "is-active", conf.UNIT],
                                      ["systemctl", "is-active", conf.UNIT],
                                      ["systemctl", "reload", conf.UNIT]])
        self.assertNotIn(["systemctl", "start", conf.UNIT], calls)

    def test_reload_starts_an_inactive_panel(self):
        self.write("/etc/systemd/system/" + conf.UNIT, pcli.unit_text(), 0o644)
        self.w.sysd.units[conf.UNIT] = {"enabled": True, "active": False}
        self.assertTrue(conf.reload_service(self.w.runner))
        self.assertIn(["systemctl", "start", conf.UNIT], self.w.sysd.calls)

    def test_open_mode_on_and_off(self):
        st = state.empty()
        st["gateway"] = {"host": "203.0.113.10"}
        state.save(st)
        secrets.set("tls.server.crt", fixture("node.crt"))
        secrets.set("tls.server.key", fixture("node.key"))
        rc, out, err = self.run_cli("panel", "public", "on", "--json")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        port = doc["public"]["port"]
        self.assertTrue(30000 <= port <= 60999)
        self.assertEqual((doc["public"]["bind"], doc["public"]["host"]), ("203.0.113.10", "203.0.113.10"))
        self.assertEqual(doc["fingerprint"], pki.sha256_fingerprint(fixture("node.crt")))
        self.assertFalse(doc["reloaded"])                                    # the panel does not run here
        self.assertEqual(json.loads(self.read("/etc/kaskad/panel/access.json")),
                         {"public": True, "port": port, "scheme": "https", "host": "203.0.113.10"})
        rc, out, err = self.run_cli("panel", "link", "--json")
        self.assertTrue(json.loads(out)["url"].startswith("https://203.0.113.10:%d%s/login?t=" % (port, conf.proxy_base(conf.load()))))
        rc, out, err = self.run_cli("panel", "public", "on", "--json")       # again: the same port
        self.assertEqual(json.loads(out)["public"]["port"], port)
        rc, out, err = self.run_cli("panel", "status")
        self.assertIn("Отпечаток сертификата SHA-256: %s" % pki.sha256_fingerprint(fixture("node.crt")), out)
        rc, out, err = self.run_cli("panel", "public", "off")
        self.assertEqual(rc, 0, err)
        self.assertIn("Открытый режим выключен", out)
        self.assertFalse(conf.load()["public"]["on"])
        self.assertFalse(json.loads(self.read("/etc/kaskad/panel/access.json"))["public"])

    def test_the_menu_shows_menu_words_not_commands(self):
        """T-G1b N7: menu 18 → 3 (``panel public on``) and 18 → 1 of a fresh node (``panel install``) print these lines
        to the person at the menu: the way in is «меню gokaskad → 18 → 1», a password — «18 → 2», no kaskadctl."""
        st = state.empty()
        st["gateway"] = {"host": "203.0.113.10"}
        state.save(st)
        secrets.set("tls.server.crt", fixture("node.crt"))
        secrets.set("tls.server.key", fixture("node.key"))
        rc, out, err = self.run_cli("panel", "public", "on")
        self.assertEqual(rc, 0, err)
        self.assertIn("Вход — по паролю или одноразовой ссылке (меню gokaskad → 18 → 1).", out)
        self.assertIn("задать пароль — меню gokaskad → 18 → 2.", out)                   # no password yet
        self.assertNotIn("kaskadctl", out + err)
        rc, out, err = self.run_cli("panel", "install")
        self.assertEqual(rc, 0, err)
        self.assertIn("задайте его (меню gokaskad → 18 → 2)", out)
        self.assertNotIn("kaskadctl", out + err)
        rc, out, err = self.run_cli("panel", "require-password", "on")                 # menu 18 → 4 without one
        self.assertEqual(rc, errors.ERROR)
        self.assertNotIn("kaskadctl", out + err)

    def test_open_mode_makes_the_node_certificate_when_missing(self):
        st = state.empty()
        st["gateway"] = {"host": "203.0.113.10"}
        state.save(st)
        made = []

        def fake_self_signed(runner, workdir, cn, san=None, key="ec"):
            made.append((cn, san, key))
            return fixture("node.key"), fixture("node.crt")
        with mock.patch("kaskad.gw.inbounds.pki.self_signed", fake_self_signed):
            rc, out, err = self.run_cli("panel", "public", "on", "--port", "45678", "--json")
        self.assertEqual(rc, 0, err)
        self.assertEqual(made, [("203.0.113.10", "203.0.113.10", "rsa")])
        self.assertEqual(json.loads(out)["public"]["port"], 45678)
        sec = secrets.load()
        self.assertEqual(sec["tls.server.crt"], fixture("node.crt"))
        # SSTP installed later takes the certificate made for this very address over (the fixture's CN is
        # 127.0.0.1): no new certificate, the fingerprint the owner checked stays
        from kaskad.gw import tls
        st = state.load()
        st["gateway"]["host"] = "127.0.0.1"
        rec = {}
        self.assertEqual(tls.ensure(st, sec, self.w.runner, rec), [])
        self.assertEqual(rec, {"cert_host": "127.0.0.1"})
        self.assertEqual(self.w.runner.commands(["openssl"]), [])

    def test_require_password(self):
        rc, out, err = self.run_cli("panel", "require-password", "on")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("Сначала задайте пароль", err)
        auth.set_password("очень-секретный-1")
        rc, out, err = self.run_cli("panel", "require-password", "on", "--json")
        self.assertEqual((rc, json.loads(out)["require_password"]), (0, True))
        self.assertTrue(conf.load()["require_password"])
