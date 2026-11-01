"""Kaskad 3.3.0, live check №2 (k3/stand/logs/T-M6-qs330b.md §1) — the domain and the site:

    №2   nginx of the site stays enabled: every way the site is (re)written ends with ``enable --now``; ``site remove``
         keeps the record that Kaskad installed nginx; the minute watchdog and «Проверка сервера» see nginx off or not
         enabled (and the watchdog repairs it), the same for the gateway's own services
    №16  ``site remove`` and the site again: Debian's «Welcome to nginx!» goes away again
    №15  ``site status``: 443 held by xray of the gateway is said so, not «свободен»
    №5   ``site mode`` switching VLESS (TLS <-> Reality) says to give the VLESS links again (CLI, panel, wizard)
    №19  ``backup undo`` on the same server names only what did not come back
"""

import os

from kaskad import backup, netcheck, state, summary
from kaskad.panel import conf
from kaskad.site import cli as site_cli
from kaskad.site import model, nginx, ops
from kaskad.site import ops as site_ops
from tests.site.sitecase import DOMAIN
from tests.site.test_port80 import Port80Case
from tests.test_gw_vless_front import FrontCase

REISSUE = "выданные раньше ссылки VLESS не подключатся"


class NginxStaysEnabledTest(Port80Case):
    """№2 and №16 on nginx installed by Kaskad (and on someone else's stopped nginx)."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def test_remove_then_the_site_again_enables_nginx_and_drops_the_default(self):
        self.to_site()
        ops.remove(self.w.runner)
        self.assertFalse(self.ng.unit["enabled"] or self.ng.unit["active"])      # domain gone: nginx off for good
        self.assertTrue(os.path.lexists(nginx.default_enabled_path()))          # Debian's default back
        self.assertEqual(os.listdir(model.site_dir()), ["pkgs"])                 # only who installed what stays
        self.assertIn("nginx", model.read_pkgs())

        self.with_domain(issued="le")
        self.to_site()
        self.assertTrue(nginx.owned())
        self.assertTrue(self.ng.unit["enabled"] and self.ng.unit["active"])      # №2: survives a reboot
        self.assertEqual(self.files(), {"site": True, "acme": False, "default": False, "mark": True})    # №16

    def test_domain_mode_reissue_then_the_site_enables_nginx(self):
        self.to_site()
        self.to_domain()
        self.assertFalse(self.ng.unit["enabled"])                                # domain mode: off for good
        os.unlink(model.renewal_conf(DOMAIN))
        import shutil
        shutil.rmtree(model.live_dir(DOMAIN))
        self.assertTrue(ops.issue(self.w.runner)["issued"])                      # standalone on a free :80
        self.to_site()
        self.assertTrue(self.ng.unit["enabled"] and self.ng.unit["active"])

    def test_every_rewrite_of_the_site_enables_nginx(self):
        self.to_site()
        self.ng.unit["enabled"] = False                                          # what 3.3.0 left on M6
        ops.rotate_panel_path(self.w.runner)
        self.assertTrue(self.ng.unit["enabled"])
        self.assertIn(["systemctl", "enable", "--now", "nginx"], self.w.sysd.calls)

    def test_the_watchdog_sees_nginx_not_enabled_and_repairs_it(self):
        self.to_site()
        self.assertEqual(ops.nginx_problems(self.w.runner), [])
        self.ng.unit["enabled"] = False
        got = ops.nginx_problems(self.w.runner)
        self.assertEqual([p["kind"] for p in got], ["site_nginx"])
        self.assertIn("не в автозапуске", got[0]["detail"])
        self.assertIn(DOMAIN, got[0]["detail"])
        self.assertFalse(self.ng.unit["enabled"])                                # a report changes nothing
        self.assertIn(ops.NGINX_BOOT, ops._check_warning_codes(self.w.runner))
        got = ops.nginx_problems(self.w.runner, repair=True)
        self.assertTrue(got[0]["repaired"])
        self.assertIn(["systemctl", "enable", "--now", "--no-block", "nginx"], self.w.sysd.calls)
        self.assertTrue(self.ng.unit["enabled"])
        self.assertEqual(ops.nginx_problems(self.w.runner), [])

        self.ng.unit.update(active=False)
        got = ops.nginx_problems(self.w.runner)
        self.assertIn("не работает", got[0]["detail"])

    def test_no_site_no_nginx_problem(self):
        self.to_site()
        self.to_domain()                                                         # nginx off on purpose
        self.assertEqual(ops.nginx_problems(self.w.runner), [])


class ForeignStoppedNginxTest(Port80Case):
    def test_the_site_on_someone_elses_stopped_nginx_enables_it(self):
        self.foreign()
        self.ng.unit.update(enabled=False, active=False)
        self.with_domain(issued="le")
        self.to_site()
        self.assertFalse(nginx.owned())
        self.assertTrue(self.ng.unit["enabled"] and self.ng.unit["active"])


class WatchdogAndServerCheckTest(FrontCase):
    """№2: the minute watchdog (``net check``) and «Проверка сервера» (``summary --check``)."""

    def test_nginx_of_the_site_not_enabled(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertNotIn("site_nginx", [p["kind"] for p in netcheck.check(self.w.runner)["problems"]])
        self.w.sysd.units["nginx"]["enabled"] = False
        res = netcheck.check(self.w.runner, repair=False)
        self.assertIn("site_nginx", [p["kind"] for p in res["problems"]])
        doc = summary.summary(check=True, runner=self.w.runner)
        p = [x for x in doc["problems"] if x["kind"] == "site_nginx"][0]
        self.assertEqual(p["item"], 19)
        self.assertIn("не в автозапуске", p["detail"])
        netcheck.check(self.w.runner, repair=True)
        self.assertTrue(self.w.sysd.units["nginx"]["enabled"])
        self.assertNotIn("site_nginx", [p["kind"] for p in netcheck.check(self.w.runner)["problems"]])

    def test_a_service_of_the_gateway_running_but_not_enabled(self):
        from kaskad.gw import xray
        self.assertEqual([p for p in netcheck.check(self.w.runner)["problems"] if p["kind"] == "gw_service"], [])
        self.w.sysd.units[xray.UNIT]["enabled"] = False
        probs = [p for p in netcheck.check(self.w.runner)["problems"] if p["kind"] == "gw_service"]
        self.assertEqual(len(probs), 1)
        self.assertIn("не в автозапуске", probs[0]["detail"])
        self.assertIn(xray.UNIT, probs[0]["detail"])
        netcheck.check(self.w.runner, repair=True)
        self.assertTrue(self.w.sysd.units[xray.UNIT]["enabled"])


class ModeTellsToReissueVlessTest(FrontCase):
    """№5: ``site mode`` switching VLESS (TLS <-> Reality) — the answer (CLI, panel, wizard) says to give links again."""

    def test_both_ways_and_only_when_vless_changed(self):
        got = site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(got["vless"]["security"], "tls")
        w = [x for x in got["warnings"] if REISSUE in x]
        self.assertEqual(len(w), 1, got["warnings"])
        self.assertEqual(w[0], got["vless"]["text"])                            # the very line of gw.ops._reissue
        self.assertIn("Телефон (Мама)", w[0])

        rc, out, err = self.run_cli("site", "mode", "domain")                   # Ш2: the price first (§3.6)
        self.assertEqual(rc, 3, out + err)
        self.assertIn("Телефон", err)
        rc, out, err = self.run_cli("site", "mode", "domain", "--yes")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("VLESS: Reality", out)
        self.assertIn(REISSUE, err)
        self.assertIn("Телефон (Мама)", err)

        rc, out, err = self.run_cli("site", "mode", "domain")                   # nothing changed: nothing to say
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn(REISSUE, out + err)

    def test_no_devices_no_line(self):
        self.ok("gw", "device", "del", self.dev["id"])
        got = site_ops.mode(self.w.runner, "site", yes=True)
        self.assertTrue(got["vless"]["changed"])
        self.assertFalse([x for x in got.get("warnings") or [] if REISSUE in x])


class Port443StatusTest(FrontCase):
    """№15: 443 held by xray of the gateway."""

    def test_status_says_xray_holds_443(self):
        doc = site_ops.status(self.w.runner)                                    # domain mode, VLESS Reality on 443
        p = doc["site"]["port443"]
        self.assertTrue(p["free"] and p["xray"])
        self.assertFalse(p["behind"])
        self.assertIn("держит xray шлюза (VLESS); сайт при включении встанет за ним",
                      site_cli.port443_line(p, doc["mode"]))

        site_ops.mode(self.w.runner, "site", yes=True)
        rc, out, err = self.run_cli("site", "status")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Порт 443/tcp: держит xray шлюза (VLESS + TLS), сайт работает за ним.", out)
        self.assertNotIn("443/tcp для сайта: свободен", out)

    def test_status_without_xray(self):
        self.assertEqual(site_cli.port443_line({"free": True}, "domain"), "Порт 443/tcp для сайта: свободен")
        self.assertEqual(site_cli.port443_line({"free": True}, "site"), "Порт 443/tcp: на нём сайт (nginx).")
        self.assertIn("занят — nginx", site_cli.port443_line({"free": False, "who": "nginx"}, "domain"))


class UndoOnTheSameServerTest(FrontCase):
    """№19: ``backup undo`` on the server the copy is of — the panel's open mode, the certificate, the site and TLS of
    VLESS are all here: no «включите здесь», «выпустите сертификат», «пока на Reality»."""

    def snapshot(self, public):
        site_ops.mode(self.w.runner, "site", yes=True)
        cfg = conf.load()
        cfg["public"]["on"] = public
        cfg["public"]["port"] = 41234
        cfg["public"]["bind"] = cfg["public"]["host"] = "203.0.113.10"
        conf.save(cfg)
        self.assertEqual(conf.load()["public"]["on"], public)
        return backup.take_snapshot(self.w.runner)

    def test_nothing_to_redo_here(self):
        snap = self.snapshot(True)
        res = backup.undo(self.w.runner, path=snap)
        self.assertTrue(conf.load()["public"]["on"])
        notes = " | ".join(res["notes"])
        self.assertNotIn("меню 18", notes)
        self.assertNotIn("выпустите сертификат", notes)
        self.assertNotIn("VLESS на старом сервере", notes)
        self.assertEqual(model.peek(state.load())["mode"], "site")
        self.assertEqual(self.rec()["security"], "tls")

    def test_only_what_did_not_come_back(self):
        snap = self.snapshot(True)
        cfg = conf.load()
        cfg["public"]["on"] = False                                              # switched off after the copy
        conf.save(cfg)
        res = backup.undo(self.w.runner, path=snap)
        notes = " | ".join(res["notes"])
        self.assertIn("открытый доступ к панели был включён (порт 41234)", notes)
        self.assertNotIn("выпустите сертификат", notes)
