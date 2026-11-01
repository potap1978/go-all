"""The domain and its certificate of Let's Encrypt (kaskad.site.ops, acme): set/clear, port 80 (a foreign listener,
a forward, UFW), the issue (new, «живой — не перевыпускать», staging -> production, limit/net/dns, apt), the hook and
the renewal file, renew, the watchdog, removal."""

import os
from unittest import mock

from kaskad import conflicts, forwards, state
from kaskad.errors import CONFLICT, KaskadError
from kaskad.gw import sys as gsys
from kaskad.site import acme, certinfo, consumers, dns, model, ops
from tests.fakes_gw import FakeUfw
from tests.helpers import zone
from tests.site.sitecase import DAY, DOMAIN, SiteCase, fixture

NGINX_80 = 'tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=812,fd=6))\n'


class DomainTest(SiteCase):
    def test_set_checks_dns_at_once_and_does_not_issue(self):
        res = ops.set_domain(self.w.runner, "VPN.example.com")
        self.assertTrue(res["ok"])
        self.assertEqual(res["site"]["domain"], DOMAIN)
        self.assertEqual(res["site"]["mode"], "domain")
        self.assertEqual(res["dns"]["state"], "ok")
        self.assertEqual(res["affected"], [])
        self.assertEqual(self.certbot.calls, [])
        self.assertEqual(self.w.pkgs.installs, [])                       # nothing installed by setting a domain
        s = self.site()
        self.assertEqual((s["domain"], s["mode"], s["cert"], s["dns"]["state"]), (DOMAIN, "domain", "", "ok"))
        self.assertIn("SITE: домен vpn.example.com задан", self.log_text())

    def test_idn_is_kept_in_ascii(self):
        res = ops.set_domain(self.w.runner, "впн.пример.рф")
        self.assertEqual(res["site"]["domain"], "xn--b1awf.xn--e1afmkfd.xn--p1ai")
        self.assertEqual(res["site"]["domain_unicode"], "впн.пример.рф")
        self.assertIn(("xn--b1awf.xn--e1afmkfd.xn--p1ai", dns.A), self.dns_asked)

    def test_bad_names(self):
        for bad in ("203.0.113.10", "*.example.com", "localhost"):
            with self.assertRaises(KaskadError):
                ops.set_domain(self.w.runner, bad)
        self.assertIsNone(state.load().get("site"))

    def test_no_key_means_off(self):
        doc = ops.status(self.w.runner)
        self.assertEqual((doc["domain"], doc["mode"], doc["dns"]), (None, "off", None))
        self.assertEqual(doc["use"], {"panel": False, "pages": False, "sstp": False, "ocserv": False})
        self.assertEqual(doc["renew"], {"by": None})
        self.assertEqual(doc["port80"], {"free": True, "who": None})
        self.assertNotIn("site", state.load())                           # reading writes nothing

    def test_status_keys_of_the_contract(self):
        ops.set_domain(self.w.runner, DOMAIN)
        doc = ops.status(self.w.runner)
        for k in ("ok", "domain", "mode", "cert", "dns", "port80", "use", "use_impact", "use_impact_total", "host_is_domain", "renew",
                  "deps", "email"):
            self.assertIn(k, doc)
        self.assertEqual(sorted(doc["use_impact"]), ["ocserv", "sstp"])
        self.assertEqual(doc["deps"], {"certbot": False})
        self.assertFalse(doc["host_is_domain"])
        st = state.load()
        st["gateway"] = {"host": DOMAIN}
        state.save(st)
        self.assertTrue(ops.status(self.w.runner)["host_is_domain"])


class Port80Test(SiteCase):
    UFW = FakeUfw

    def test_foreign_listener_refuses_with_who(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.certbot_present()
        self.w.ss = NGINX_80
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        e = c.exception
        self.assertEqual(e.code, CONFLICT)
        self.assertEqual(e.doc["reason"], "port80")
        self.assertIn("nginx", e.doc["text"])
        self.assertIn("временно занять этот порт", e.doc["text"])
        self.assertIn("Kaskad чужие службы не останавливает", e.doc["text"])
        self.assertNotIn("подключения к нему снаружи", e.doc["text"])
        self.assertEqual(self.certbot.calls, [])
        self.assertEqual(ops.status(self.w.runner)["port80"]["free"], False)

    def test_forward_on_80_refuses(self):
        st = state.load()
        st["engine"] = "nft"
        st["forwards"] = [state.normalize_forward({"proto": "tcp", "port": 80, "to": "198.51.100.20", "to_port": 80})]
        state.save(st)
        ops.set_domain(self.w.runner, DOMAIN)
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        self.assertEqual(c.exception.doc["reason"], "port80")
        self.assertIn("проброс", c.exception.doc["text"])

    def test_ufw_rule_while_the_domain_is_set(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.assertIn(("80/tcp", "kaskad-acme"), self.w.ufw.rules)
        self.assertTrue(self.site()["ufw80"])
        ops.clear(self.w.runner)
        self.assertNotIn("80/tcp", [s for s, _ in self.w.ufw.rules])
        self.assertFalse(self.site()["ufw80"])

    def test_the_owners_own_rule_is_left_alone(self):
        self.w.ufw.rules.append(("80/tcp", ""))
        ops.set_domain(self.w.runner, DOMAIN)
        self.assertEqual(self.w.ufw.rules, [("80/tcp", "")])
        self.assertFalse(self.site()["ufw80"])
        ops.clear(self.w.runner)
        self.assertEqual(self.w.ufw.rules, [("80/tcp", "")])

    def test_ufw_inactive_nothing(self):
        self.w.ufw.active = False
        ops.set_domain(self.w.runner, DOMAIN)
        self.assertEqual(self.w.ufw.rules, [])

    def test_a_new_forward_on_80_warns_that_renewals_stop(self):
        found = conflicts.check("tcp", 80, self.w.runner, env={})
        self.assertEqual(found, [])
        ops.set_domain(self.w.runner, DOMAIN)
        found = conflicts.check("tcp", 80, self.w.runner, env={})
        self.assertEqual(found[0]["kind"], "site")
        self.assertIn("перестанет продлеваться", found[0]["detail"])
        self.assertEqual(conflicts.check("udp", 80, self.w.runner, env={}), [])
        self.assertEqual(conflicts.check("tcp", 8080, self.w.runner, env={}), [])
        st = state.load()
        st["engine"] = "nft"
        state.save(st)
        with self.assertRaises(KaskadError) as c:
            forwards.add_forward("tcp", 80, "198.51.100.20", 80, runner=self.w.runner)
        self.assertEqual(c.exception.code, CONFLICT)
        self.assertIn("перестанет продлеваться", str(c.exception))
        # our own check of port 80 does not count the domain itself
        self.assertTrue(ops.port80(state.load(), self.w.runner)["free"])


class IssueTest(SiteCase):
    def setUp(self):
        super().setUp()
        ops.set_domain(self.w.runner, DOMAIN)

    def test_new_certificate(self):
        res = ops.issue(self.w.runner)
        self.assertEqual(self.w.pkgs.installs, [["certbot"]])                # apt only now
        self.assertEqual(model.read_pkgs(), ["certbot"])
        argv = self.certbot.calls[0]
        self.assertEqual(argv[:13], ["certbot", "certonly", "--standalone", "--preferred-challenges", "http",
                                     "--non-interactive", "--agree-tos", "--keep-until-expiring", "--cert-name",
                                     DOMAIN, "-d", DOMAIN, "--register-unsafely-without-email"])
        self.assertEqual(argv[-2:], ["--deploy-hook", acme.hook_command()])
        self.assertTrue(acme.hook_command().endswith("/opt/kaskad/bin/kaskadctl site cert-installed --quiet"))
        self.assertNotIn("--staging", argv)
        self.assertNotIn("--force-renewal", argv)
        self.assertEqual((res["ok"], res["issued"], res["kept"]), (True, True, False))
        self.assertEqual(res["cert"], {"kind": "letsencrypt", "names": [DOMAIN], "not_after": "2026-12-30T10:00:00Z",
                                       "days_left": 83, "staging": False, "issuer": "R11"})
        self.assertTrue(any("Почта" in w for w in res["warnings"]))
        # the copy for the consumers (made by the hook certbot ran)
        self.assertEqual(self.certbot.hooked, [model.live_dir(DOMAIN)])
        with open(model.fullchain_path()) as f:
            self.assertEqual(f.read(), fixture("le.pem"))
        self.assertEqual(os.stat(model.fullchain_path()).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(model.privkey_path()).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(model.fullchain_path())).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(model.tls_dir()).st_mode & 0o777, 0o755)
        # renewal: no certbot.timer in this system -> ours; the watchdog
        self.assertTrue(os.path.exists(gsys.unit_path(acme.RENEW_TIMER)))
        self.assertTrue(os.path.exists(gsys.unit_path(acme.WATCH_TIMER)))
        self.assertIn("site tick --quiet", self.read("/etc/systemd/system/kaskad-site.service"))
        self.assertIn("certbot renew --quiet", self.read("/etc/systemd/system/kaskad-cert-renew.service"))
        self.assertTrue(self.w.sysd.units[acme.WATCH_TIMER]["enabled"])
        s = self.site()
        self.assertEqual((s["cert"], s["staging"], s["renew_by"]), ("letsencrypt", False, acme.RENEW_TIMER))
        self.assertEqual(acme.hook_state(DOMAIN), "ours")
        doc = ops.status(self.w.runner)
        self.assertEqual(doc["renew"], {"by": acme.RENEW_TIMER})
        self.assertEqual(doc["deps"], {"certbot": True})
        self.assertEqual(doc["cert"]["kind"], "letsencrypt")

    def test_email_goes_to_certbot(self):
        ops.set_email(self.w.runner, "admin@example.com")
        ops.issue(self.w.runner)
        argv = self.certbot.calls[0]
        self.assertEqual(argv[argv.index("--email") + 1], "admin@example.com")
        self.assertNotIn("--register-unsafely-without-email", argv)

    def test_system_certbot_timer_is_used_when_there(self):
        from unittest import mock
        with mock.patch.object(acme, "system_timer_present", lambda runner: True):
            ops.issue(self.w.runner)
        self.assertEqual(self.site()["renew_by"], "certbot.timer")
        self.assertFalse(os.path.exists(gsys.unit_path(acme.RENEW_TIMER)))
        self.assertIn(["systemctl", "enable", "--no-reload", "certbot.timer"], [a for a, _ in self.w.runner.calls])

    def test_live_certificate_is_kept(self):
        self.certbot_present()
        self.put_live("le")
        res = ops.issue(self.w.runner)
        self.assertEqual((res["issued"], res["kept"]), (False, True))
        self.assertIn("не перевыпускаю", res["text"])
        self.assertEqual(self.certbot.calls, [])                             # no run at all: LE limits
        self.assertTrue(os.path.exists(model.fullchain_path()))               # handed out anyway
        self.assertEqual(self.site()["cert"], "letsencrypt")

    def test_live_but_under_30_days_goes_to_certbot(self):
        self.certbot_present()
        self.put_live("le")
        self.clock = certinfo.parse(fixture("le.pem"))["not_after_ts"] - 20 * DAY
        self.certbot.pem = "le2"
        res = ops.issue(self.w.runner)
        self.assertEqual(len(self.certbot.calls), 1)
        self.assertTrue(res["issued"])
        self.assertEqual(res["cert"]["not_after"], "2027-03-01T10:00:00Z")

    def test_staging_then_production_by_force_renewal(self):
        self.certbot_present()
        self.certbot.pem = "staging"
        res = ops.issue(self.w.runner, staging=True)
        self.assertIn("--staging", self.certbot.calls[0])
        self.assertTrue(res["cert"]["staging"])
        self.assertTrue(self.site()["staging"])
        self.assertTrue(any("staging" in w for w in res["warnings"]))
        # staging again: kept (a test certificate is fine for a test run)
        self.assertTrue(ops.issue(self.w.runner, staging=True)["kept"])
        self.assertEqual(len(self.certbot.calls), 1)
        # production: once, with --force-renewal over the test one
        self.certbot.pem = "le"
        res = ops.issue(self.w.runner)
        argv = self.certbot.calls[1]
        self.assertIn("--force-renewal", argv)
        self.assertNotIn("--staging", argv)
        self.assertTrue(res["issued"])
        self.assertFalse(res["cert"]["staging"])
        self.assertFalse(self.site()["staging"])
        # and then it is a live production certificate: kept
        self.assertTrue(ops.issue(self.w.runner)["kept"])
        self.assertEqual(len(self.certbot.calls), 2)

    def refused(self, mode, in_log=False):
        self.certbot_present()
        self.certbot.mode = mode
        self.certbot.in_log = in_log
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        return c.exception.doc

    def test_limit_with_retry_after(self):
        with zone("UTC"):
            doc = self.refused("limit")
        self.assertEqual(doc["reason"], "limit")
        self.assertEqual(doc["retry_after"], "2026-10-08T12:00:00Z")
        self.assertIn("лимит", doc["text"])
        self.assertIn("08.10, 12:00 UTC (через 24 ч)", doc["text"])
        self.assertFalse(os.path.exists(model.fullchain_path()))
        self.assertEqual(self.site()["cert"], "")
        self.assertIn("Let's Encrypt отказал (limit)", self.log_text())

    def test_net_from_the_log_of_certbot(self):
        doc = self.refused("net", in_log=True)
        self.assertEqual(doc["reason"], "net")
        self.assertIn("не достучался до Let's Encrypt", doc["text"])
        self.assertNotIn("retry_after", doc)

    def test_dns_check_failed_at_lets_encrypt(self):
        doc = self.refused("dns")
        self.assertEqual(doc["reason"], "dns")
        self.assertIn("порт 80", doc["text"])

    def test_an_old_failure_in_the_log_is_not_this_one(self):
        p = model.le_log()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("too many certificates (5) already issued, retry after 2026-01-01 00:00:00 UTC\n")
        self.assertEqual(self.refused("dns")["reason"], "dns")

    def test_dns_not_here_refuses_before_certbot(self):
        self.certbot_present()
        self.answers[dns.A] = ("ok", ["198.51.100.7"])
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        self.assertEqual(c.exception.doc["reason"], "dns")
        self.assertIn("198.51.100.7", c.exception.doc["text"])
        self.assertEqual(self.certbot.calls, [])
        self.answers[dns.A] = ("ok", ["104.21.3.4"])
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        self.assertIn("оранжевое облако", c.exception.doc["text"])

    def test_partial_and_aaaa_only_warn(self):
        self.answers[dns.AAAA] = ("ok", ["2001:db8::99"])
        res = ops.issue(self.w.runner)
        self.assertTrue(res["issued"])
        self.assertTrue(any("AAAA" in w for w in res["warnings"]))

    def test_apt_failure(self):
        self.w.pkgs.fail = "E: Unable to locate package certbot\n"
        with self.assertRaises(ops.Refusal) as c:
            ops.issue(self.w.runner)
        self.assertEqual(c.exception.doc["reason"], "apt")
        self.assertIn("certbot", c.exception.doc["text"])
        self.assertEqual(model.read_pkgs(), [])

    def test_without_a_domain(self):
        ops.clear(self.w.runner)
        with self.assertRaises(KaskadError) as c:
            ops.issue(self.w.runner)
        self.assertNotIsInstance(c.exception, ops.Refusal)
        self.assertIn("Домен не задан", str(c.exception))

    def test_foreign_hook_is_left_and_the_watchdog_takes_over(self):
        self.certbot.foreign_hook = "systemctl reload nginx"
        res = ops.issue(self.w.runner)
        self.assertEqual(acme.hook_state(DOMAIN), "foreign")
        self.assertIn("renew_hook = systemctl reload nginx", self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN))
        self.assertTrue(any("сторож" in w for w in res["warnings"]))


class HookTest(SiteCase):
    def test_lineage_of_another_name_is_ignored(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("other", domain="other.example.org")
        res = consumers.cert_installed(self.w.runner, lineage="/etc/letsencrypt/live/other.example.org")
        self.assertEqual(res, {"ok": True, "consumers": [], "changed": False, "skipped": "lineage"})
        self.assertFalse(os.path.exists(model.fullchain_path()))

    def test_without_a_domain_nothing(self):
        res = consumers.cert_installed(self.w.runner, lineage="/etc/letsencrypt/live/%s" % DOMAIN)
        self.assertEqual(res["skipped"], "no_domain")

    def test_a_certificate_of_another_name_in_our_lineage_is_refused(self):
        ops.set_domain(self.w.runner, DOMAIN)
        live = model.live_dir(DOMAIN)
        os.makedirs(live)
        with open(os.path.join(live, "fullchain.pem"), "w") as f:
            f.write(fixture("other.pem"))
        with open(os.path.join(live, "privkey.pem"), "w") as f:
            f.write(fixture("other.key"))
        with self.assertRaises(KaskadError) as c:
            consumers.cert_installed(self.w.runner)
        self.assertIn("не для vpn.example.com", str(c.exception))
        self.assertFalse(os.path.exists(model.fullchain_path()))

    def test_copy_is_made_once(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le")
        self.assertTrue(consumers.cert_installed(self.w.runner)["changed"])
        self.assertFalse(consumers.cert_installed(self.w.runner)["changed"])
        self.put_live("le2")
        self.assertTrue(consumers.cert_installed(self.w.runner)["changed"])
        current = os.readlink(model.current_path())
        self.assertEqual([n for n in os.listdir(model.tls_dir()) if n.startswith(".pair-")], [current])

    def test_legacy_paths_follow_the_new_current_pair(self):
        """Configs issued before 4.0-B still name tls/fullchain.pem and tls/privkey.pem."""
        ops.set_domain(self.w.runner, DOMAIN)
        tls = model.tls_dir()
        os.makedirs(tls, exist_ok=True)
        for name, body in (("fullchain.pem", fixture("other.pem")), ("privkey.pem", fixture("other.key"))):
            with open(os.path.join(tls, name), "w") as f:
                f.write(body)
        self.put_live("le")
        consumers.cert_installed(self.w.runner)
        self.put_live("le2")
        consumers.cert_installed(self.w.runner)
        for name, body in (("fullchain.pem", fixture("le2.pem")), ("privkey.pem", fixture("le2.key"))):
            path = os.path.join(tls, name)
            self.assertTrue(os.path.islink(path))
            self.assertEqual(os.readlink(path), "current/" + name)
            with open(path) as f:
                self.assertEqual(f.read(), body)

    def test_bad_new_key_keeps_the_previous_pair(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le")
        consumers.cert_installed(self.w.runner)
        before = os.readlink(model.current_path())
        with open(os.path.join(model.live_dir(DOMAIN), "privkey.pem"), "w") as f:
            f.write(fixture("other.key"))
        with self.assertRaises(KaskadError):
            consumers.cert_installed(self.w.runner)
        self.assertEqual(os.readlink(model.current_path()), before)
        with open(model.fullchain_path()) as f:
            self.assertEqual(f.read(), fixture("le.pem"))
        self.assertEqual([n for n in os.listdir(model.tls_dir()) if n.startswith(".pair-")], [before])

    def test_failed_pair_switch_keeps_the_previous_pair(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le")
        consumers.cert_installed(self.w.runner)
        before = os.readlink(model.current_path())
        self.put_live("le2")
        with mock.patch.object(consumers.os, "replace", side_effect=OSError("диск заполнен")):
            with self.assertRaises(KaskadError):
                consumers.cert_installed(self.w.runner)
        self.assertEqual(os.readlink(model.current_path()), before)
        with open(model.privkey_path()) as f:
            self.assertEqual(f.read(), fixture("le.key"))
        with open(os.path.join(model.tls_dir(), "privkey.pem")) as f:
            self.assertEqual(f.read(), fixture("le.key"))
        self.assertEqual([n for n in os.listdir(model.tls_dir()) if n.startswith(".pair-")], [before])

    def test_renewal_file_edits(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le", hook=False)
        self.assertEqual(acme.hook_state(DOMAIN), "none")
        self.assertTrue(acme.add_hook(DOMAIN))
        self.assertEqual(acme.hook_state(DOMAIN), "ours")
        self.assertFalse(acme.add_hook(DOMAIN))
        self.assertTrue(acme.set_autorenew(DOMAIN, False))
        text = self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN)
        self.assertLess(text.index("autorenew = False"), text.index("[renewalparams]"))
        self.assertFalse(acme.set_autorenew(DOMAIN, False))
        self.assertTrue(acme.set_autorenew(DOMAIN, True))
        self.assertIn("autorenew = True", self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN))
        self.assertTrue(acme.drop_hook(DOMAIN))
        self.assertEqual(acme.hook_state(DOMAIN), "none")
        self.assertEqual(acme.hook_state("nothing.example.com"), "missing")

    def test_drop_hook_leaves_a_foreign_hook(self):
        ops.set_domain(self.w.runner, DOMAIN)
        self.put_live("le", hook=False)
        path = "/etc/letsencrypt/renewal/%s.conf" % DOMAIN
        self.write(path, "autorenew = True\n[renewalparams]\n"
                   "deploy_hook = systemctl reload nginx\n"
                   "renew_hook = %s\n" % acme.hook_command())
        self.assertEqual(acme.hook_state(DOMAIN), "foreign")
        self.assertTrue(acme.drop_hook(DOMAIN))
        text = self.read(path)
        self.assertIn("deploy_hook = systemctl reload nginx", text)
        self.assertNotIn(acme.HOOK_MARK, text)


class RenewTickTest(SiteCase):
    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")
        self.certbot.calls[:] = []
        self.na = certinfo.parse(fixture("le.pem"))["not_after_ts"]

    def test_renew_not_due(self):
        res = ops.renew(self.w.runner)
        self.assertEqual(self.certbot.calls, [["certbot", "renew", "--cert-name", DOMAIN, "--non-interactive", "--quiet"]])
        self.assertEqual((res["ok"], res["renewed"]), (True, False))

    def test_renew_due(self):
        self.certbot.renew_pem = "le2"
        res = ops.renew(self.w.runner)
        self.assertTrue(res["renewed"])
        self.assertEqual(res["cert"]["not_after"], "2027-03-01T10:00:00Z")
        with open(model.fullchain_path()) as f:
            self.assertEqual(f.read(), fixture("le2.pem"))

    def test_renew_refused(self):
        self.certbot.mode = "net"
        with self.assertRaises(ops.Refusal) as c:
            ops.renew(self.w.runner)
        self.assertEqual(c.exception.doc["reason"], "net")

    def test_tick_more_than_21_days(self):
        res = ops.tick(self.w.runner)
        self.assertEqual(res, {"ok": True, "days_left": 83, "action": "ok"})
        self.assertEqual(self.certbot.calls, [])

    def test_tick_under_21_days_renews(self):
        self.clock = self.na - 20 * DAY
        self.certbot.renew_pem = "le2"
        res = ops.tick(self.w.runner)
        self.assertEqual(res["action"], "renewed")
        self.assertEqual(self.certbot.calls[0][:2], ["certbot", "renew"])
        self.assertEqual(self.notified, [])

    def test_tick_reports_a_failed_handoff_after_renewal(self):
        self.clock = self.na - 20 * DAY
        self.certbot.hook = False
        self.certbot.renew_pem = "le2"
        with mock.patch.object(consumers, "cert_installed", side_effect=KaskadError("ключ не подошёл")):
            res = ops.tick(self.w.runner)
        self.assertEqual(res["action"], "failed")
        self.assertIn("не передал его панели и протоколам", res["text"])
        self.assertEqual(len(self.notified), 1)

    def test_tick_under_21_days_failed_tells_journal_and_bot(self):
        self.clock = self.na - 5 * DAY
        self.certbot.mode = "dns"
        res = ops.tick(self.w.runner)
        self.assertEqual((res["action"], res["days_left"]), ("failed", 5))
        self.assertIn("истекает через 5 дн.", self.log_text())
        self.assertEqual(len(self.notified), 1)
        self.assertIn("vpn.example.com", self.notified[0])

    def test_tick_hands_out_a_certificate_the_hook_did_not(self):
        self.certbot.hook = False
        self.certbot.renew_pem = "le2"
        self.put_live("le2")                                  # renewed by someone, our hook never ran
        res = ops.tick(self.w.runner)
        self.assertEqual(res["action"], "installed")
        with open(model.fullchain_path()) as f:
            self.assertEqual(f.read(), fixture("le2.pem"))

    def test_tick_reports_a_failed_handoff_of_an_existing_certificate(self):
        self.put_live("le2")
        with mock.patch.object(consumers, "cert_installed", side_effect=KaskadError("ключ не подошёл")):
            res = ops.tick(self.w.runner)
        self.assertEqual(res["action"], "failed")
        self.assertIn("не передан панели и протоколам", res["text"])
        self.assertEqual(self.certbot.calls, [])
        self.assertEqual(len(self.notified), 1)

    def test_tick_without_a_certificate(self):
        ops.clear(self.w.runner)
        self.assertEqual(ops.tick(self.w.runner), {"ok": True, "days_left": None, "action": "none"})


class ClearRemoveTest(SiteCase):
    UFW = FakeUfw

    def test_clear(self):
        self.with_domain(issued="le")
        res = ops.clear(self.w.runner)
        self.assertEqual(res["kept"], [model.live_dir(DOMAIN)])
        self.assertEqual(res["affected"], [])
        self.assertIsNone(res["site"]["domain"])
        self.assertFalse(os.path.exists(model.fullchain_path()))             # consumers back on self-signed
        self.assertTrue(os.path.exists(os.path.join(model.live_dir(DOMAIN), "fullchain.pem")))   # LE stays
        text = self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN)
        self.assertIn("autorenew = False", text)
        self.assertNotIn("site cert-installed", text)
        for u in acme.UNITS:
            self.assertFalse(os.path.exists(gsys.unit_path(u)), u)
        self.assertNotIn("80/tcp", [s for s, _ in self.w.ufw.rules])
        s = self.site()
        self.assertEqual((s["domain"], s["mode"], s["cert"], s["dns"]), ("", "off", "", None))
        self.assertEqual(ops.status(self.w.runner)["mode"], "off")

    def test_another_domain_replaces_a_working_one_only_with_its_certificate(self):
        # T-M6-qs330d №1: a working domain (its certificate handed out) is not cleared for a new name first — the new
        # name gets its certificate while the old one serves, then the switch (tests/test_qs330d_domain.py: the rest)
        self.with_domain(issued="le")
        self.certbot.pem = "other"
        res = ops.set_domain(self.w.runner, "other.example.org")
        self.assertEqual((res["site"]["domain"], res["changed_from"]), ("other.example.org", DOMAIN))
        self.assertIn("--standalone", self.certbot.calls[-1])                # no nginx: certbot on the free :80
        self.assertEqual(model.copy_info()["names"], ["other.example.org"])   # consumers on the new certificate
        self.assertIn("autorenew = False", self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN))
        self.assertEqual(self.site()["cert"], "letsencrypt")

    def test_another_domain_clears_the_first_the_old_way(self):
        # the copy of settings restores a node exactly (kaskad.backup): the old way, without a certificate
        self.with_domain(issued="le")
        res = ops.set_domain(self.w.runner, "other.example.org", keep_working=False)
        self.assertEqual(res["site"]["domain"], "other.example.org")
        self.assertFalse(os.path.exists(model.fullchain_path()))
        self.assertIn("autorenew = False", self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN))
        self.assertEqual(self.site()["cert"], "")

    def test_remove(self):
        self.with_domain(issued="le")
        res = ops.remove(self.w.runner)
        self.assertEqual(res, {"ok": True, "kept": [model.live_dir(DOMAIN)], "packages": ["certbot"], "affected": []})
        self.assertEqual(os.listdir(model.site_dir()), ["pkgs"])             # who installed certbot stays (T-M6 №2)
        self.assertEqual(model.read_pkgs(), ["certbot"])
        self.assertNotIn("site", state.load())
        self.assertEqual(ops.status(self.w.runner)["mode"], "off")

    def test_remove_without_a_domain(self):
        res = ops.remove(self.w.runner)
        self.assertEqual(res, {"ok": True, "kept": [], "packages": [], "affected": []})


class EmailTest(SiteCase):
    def test_set_and_clear(self):
        self.assertEqual(ops.set_email(self.w.runner, "admin@example.com")["email"], "admin@example.com")
        self.assertEqual(self.site()["acme_email"], "admin@example.com")
        self.assertEqual(ops.status(self.w.runner)["email"], "admin@example.com")
        self.assertEqual(ops.set_email(self.w.runner, None)["email"], "")
        with self.assertRaises(KaskadError):
            ops.set_email(self.w.runner, "nonsense")

    def test_account_updated_when_certbot_has_one(self):
        self.certbot_present()
        os.makedirs(os.path.join(model.le_dir(), "accounts", "acme-v02"))
        ops.set_email(self.w.runner, "admin@example.com")
        self.assertEqual(self.certbot.calls[-1][:4], ["certbot", "update_account", "--email", "admin@example.com"])
