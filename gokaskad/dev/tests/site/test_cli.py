"""``kaskadctl site …``: every command with --json prints exactly one JSON document of the contract; refusals —
``{"ok": false, "reason", "text"}`` and the exit code; without --json — Russian text."""

import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli, errors
from kaskad.site import model, ops
from tests.site.sitecase import DOMAIN, SiteCase


class CliTest(SiteCase):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, env or {}):
            rc = cli.main(["site"] + list(argv))
        return rc, out.getvalue(), err.getvalue()

    def js(self, *argv, rc=0, env=None):
        code, out, err = self.run_cli(*(list(argv) + ["--json"]), env=env)
        self.assertEqual(code, rc, (out, err))
        self.assertEqual(out.count("\n"), 1, out)
        return json.loads(out)

    def test_status(self):
        doc = self.js("status")
        self.assertEqual((doc["ok"], doc["mode"], doc["domain"]), (True, "off", None))
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("Домен: не задан", out)

    def test_the_whole_way(self):
        doc = self.js("domain", DOMAIN)
        self.assertEqual((doc["ok"], doc["site"]["domain"], doc["dns"]["state"], doc["affected"]), (True, DOMAIN, "ok", []))
        self.assertEqual(self.js("dns")["state"], "ok")
        self.assertEqual(self.js("dns", "--wait", "30")["state"], "ok")
        self.assertEqual(self.js("email", "admin@example.com"), {"ok": True, "email": "admin@example.com", "warnings": []})
        self.certbot.pem = "staging"
        doc = self.js("cert", "issue", "--staging")
        self.assertEqual((doc["issued"], doc["kept"], doc["cert"]["kind"]), (True, False, "letsencrypt"))
        self.assertIn("--staging", self.certbot.calls[0])
        doc = self.js("cert", "renew")
        self.assertEqual(sorted(doc), ["cert", "ok", "renewed"])
        doc = self.js("cert-installed", "--quiet")
        self.assertEqual((doc["ok"], doc["changed"]), (True, False))
        doc = self.js("use", "sstp", "on", "--dry-run")
        self.assertEqual((doc["ok"], doc["dry_run"], doc["affected"]), (True, True, []))
        doc = self.js("use", "ocserv", "on")
        self.assertEqual(doc["use"], {"sstp": False, "ocserv": True})
        self.assertEqual(self.js("tick")["action"], "ok")
        doc = self.js("status")
        self.assertEqual(doc["cert"]["staging"], True)
        doc = self.js("domain", "--clear")
        self.assertEqual((doc["ok"], doc["site"]["domain"]), (True, None))
        doc = self.js("remove")
        self.assertEqual(sorted(doc), ["affected", "kept", "ok", "packages"])
        self.assertEqual(doc["packages"], ["certbot"])

    def test_text_mode(self):
        rc, out, _ = self.run_cli("domain", DOMAIN)
        self.assertEqual(rc, 0)
        self.assertIn("Домен vpn.example.com задан", out)
        rc, out, err = self.run_cli("cert", "issue")
        self.assertEqual(rc, 0)
        self.assertIn("Сертификат Let's Encrypt для vpn.example.com выпущен", out)
        self.assertIn("Внимание: Почта", err)
        rc, out, _ = self.run_cli("status")
        self.assertIn("Сертификат: Let's Encrypt, до 30.12.2026 (осталось 83 дн.), издатель R11", out)
        rc, out, _ = self.run_cli("tick", "--quiet")
        self.assertEqual(out, "")

    def test_status_names_only_the_direction_that_is_next(self):
        self.js("domain", DOMAIN)
        self.js("cert", "issue")
        self.js("use", "sstp", "on")
        self.js("use", "ocserv", "on")
        rc, out, err = self.run_cli("status")
        self.assertEqual((rc, err), (0, ""))
        self.assertNotIn("Новые настройки при переводе на домен", out)
        self.assertIn("Новые настройки при возврате на самоподписанный: SSTP — 0, OpenConnect — 0", out)

    def test_no_email_hint_only_in_the_cli(self):
        """Let's Encrypt не шлёт писем об истечении с июня 2025: без почты предупреждение говорит о важных письмах
        (отзыв, условия) и о том, что за сроком следит Kaskad; команда CLI — только в выводе CLI, не в JSON (его
        читают панель и бот)."""
        self.js("domain", DOMAIN)
        doc = self.js("cert", "issue")
        warn = [w for w in doc["warnings"] if w.startswith("Почта для Let's Encrypt")]
        self.assertEqual(warn, [ops.warning(ops.NO_EMAIL)])
        self.assertEqual(doc["warning_codes"], [ops.NO_EMAIL])
        for need in ("отзыв", "условий", "21 день", "Telegram"):
            self.assertIn(need, warn[0])
        self.assertNotIn("о сроке сертификата приходить", warn[0])
        self.assertFalse(any("kaskadctl" in w for w in doc["warnings"]))

    def test_no_email_text_mode_names_the_command(self):
        self.js("domain", DOMAIN)
        rc, out, err = self.run_cli("cert", "issue")
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("Внимание: " + ops.warning(ops.NO_EMAIL), err)
        self.assertIn("Задать почту: kaskadctl site email <почта>", err)

    def test_with_email_no_warning_and_no_hint(self):
        self.js("domain", DOMAIN)
        self.js("email", "admin@example.com")
        rc, out, err = self.run_cli("cert", "issue")
        self.assertEqual(rc, 0, (out, err))
        self.assertNotIn("kaskadctl site email", err)
        self.assertNotIn(ops.warning(ops.NO_EMAIL), err)

    def test_refusal_of_the_issue(self):
        self.js("domain", DOMAIN)
        self.certbot_present()
        self.certbot.mode = "limit"
        doc = self.js("cert", "issue", rc=errors.ERROR)
        self.assertEqual((doc["ok"], doc["reason"], doc["retry_after"], doc["code"]),
                         (False, "limit", "2026-10-08T12:00:00Z", errors.ERROR))
        self.assertEqual(doc["error"], doc["text"])
        self.w.ss = 'tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=812,fd=6))\n'
        doc = self.js("cert", "issue", rc=errors.CONFLICT)
        self.assertEqual(doc["reason"], "port80")
        rc, out, err = self.run_cli("cert", "issue")
        self.assertEqual((rc, out), (errors.CONFLICT, ""))
        self.assertIn("Отказ: Порт 80/tcp занят", err)

    def test_usage_errors(self):
        doc = self.js("domain", rc=errors.USAGE)
        self.assertEqual(doc["ok"], False)
        self.js("domain", "203.0.113.10", rc=errors.USAGE)
        self.js("domain", DOMAIN, "--clear", rc=errors.USAGE)
        self.assertEqual(self.js("email"), {"ok": True, "email": ""})       # no argument — the mail now (T-M6-332b №9)
        self.js("email", "a@example.com", "--clear", rc=errors.USAGE)
        self.js("dns", rc=errors.USAGE)                                # no domain yet
        self.js("use", "sstp", "maybe", rc=errors.USAGE)
        self.js("use", "sstp", "on", rc=errors.USAGE)                  # no domain
        self.js("domain", DOMAIN)
        self.js("use", "sstp", "on", rc=errors.CONFLICT)               # no certificate

    def test_hook_with_a_foreign_lineage(self):
        self.js("domain", DOMAIN)
        doc = self.js("cert-installed", env={"RENEWED_LINEAGE": "/etc/letsencrypt/live/other.example.org"})
        self.assertEqual(doc["skipped"], "lineage")
        rc, out, _ = self.run_cli("cert-installed", "--quiet",
                                  env={"RENEWED_LINEAGE": "/etc/letsencrypt/live/other.example.org"})
        self.assertEqual((rc, out), (0, ""))
        self.assertFalse(os.path.exists(model.fullchain_path()))

    def test_templates_of_c1_are_registered(self):
        parser = cli.build_parser()
        site = parser._subparsers._group_actions[0].choices["site"]
        names = set(site._subparsers._group_actions[0].choices)
        for need in ("status", "domain", "dns", "email", "cert", "cert-installed", "use", "tick", "remove",
                     "templates", "template"):
            self.assertIn(need, names)
