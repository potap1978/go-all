"""``kaskadctl summary`` — the header of the SSH menu and «Что не так — проверка сервера» (docs/k3/SPEC-ssh-menu.md):
files only (nothing runs), the core's own rules of problems (collector, watchdog, the red line of menu 17), where
a person fixes each, no secrets."""

import json
import os
import time
from unittest import mock

from kaskad import compat, netcheck, paths, summary
from kaskad.gw import collect, exits, live, xray
from kaskad.panel import conf as pconf
from kaskad.site import model as sm
from tests.fakes_nft import F443, save_state
from tests.helpers import RootCase
from tests.test_gw_ops import CliGwCase
from tests.test_gw_vless import VlessCase


class SummaryGatewayTest(CliGwCase):
    def setUp(self):
        super().setUp()
        self._p = mock.patch.object(live, "_now", lambda: self.now)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def world(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        compat.ensure(self.w.runner)

    def head(self, *extra):
        before = len(self.w.runner.calls)
        rc, doc, err = self.run_json("summary", *extra)
        self.assertEqual(rc, 0, err)
        return doc, self.w.runner.calls[before:]

    def test_files_only_and_what_the_header_needs(self):
        self.world()
        collect.tick(self.w.runner)
        doc, calls = self.head()
        self.assertEqual(calls, [])                                     # the menu draws it: nothing runs
        self.assertEqual((doc["engine"], doc["forwards"]), ("nft", 1))
        g = doc["gateway"]
        self.assertEqual((g["setup"], g["enabled"], g["users"], g["devices"]), (True, True, 1, 1))
        self.assertEqual([(i["kind"], i["state"]) for i in g["inbounds"]], [("wg", "run")])
        self.assertEqual((doc["exits"]["total"], doc["exits"]["alive"], doc["exits"]["via"]), (2, 2, "pool"))
        self.assertTrue(doc["live"]["fresh"])
        self.assertEqual(doc["problems"], [])

    def test_inbound_down_is_a_problem_with_the_item(self):
        self.world()
        self.ads["wg"].running = False
        collect.tick(self.w.runner)
        doc, calls = self.head()
        self.assertEqual(calls, [])
        self.assertEqual(doc["gateway"]["inbounds"][0]["state"], "down")
        p, = doc["problems"]
        self.assertEqual((p["kind"], p["item"], p["source"]), ("gw_inbound", 17, "live"))
        self.assertEqual(p["detail"], "Протокол Тестовый вход wg не работает")
        self.assertIn("17 → 5", p["advice"])

    def test_no_exits_is_the_menu_17_rule_and_replaces_the_collectors_line(self):
        self.world()
        self.w.kill_leg(2)
        self.w.curl.observatory = {"gx-" + self.vid: False}
        for _ in range(4):
            collect.tick(self.w.runner)
            self.now += 10
        lv = json.loads(self.read("/run/kaskad/live.json"))
        self.assertIn(live.NO_POOL_TEXT, lv["gateway"]["problems"])
        self.assertIn({"kind": "exits_dead", "detail": live.NO_POOL_TEXT}, lv["gateway"]["issues"])
        self.now -= 10
        doc, _ = self.head()
        kinds = [p["kind"] for p in doc["problems"]]
        self.assertEqual(kinds[0], "no_exits")
        self.assertNotIn("exits_dead", kinds)
        self.assertEqual(doc["exits"]["alive"], 0)
        self.assertIn("Нет интернета у устройств", doc["problems"][0]["detail"])
        rc, chk, _ = self.run_json("gw", "check")                       # the same rule as gateway_check
        self.assertIn("no_exits", [p["kind"] for p in chk["problems"]])

    def test_no_pool_with_the_xray_exit_alone(self):
        """REV-s1-t1 З-2 (live.py, no_pool): the only exit the gateway may use is of the kind "xray" and dead — «нет
        выхода» is said; a check of the kind ("awg", "vless") would not see the exit and stay silent."""
        from kaskad import state
        from kaskad.gw import model
        self.world()
        with state.transaction() as st:
            model.set_exit(st, self.aid, enabled=False)                   # the AWG exit is off for the gateway
        self.w.curl.observatory = {"gx-" + self.vid: False}
        for _ in range(4):
            collect.tick(self.w.runner)
            self.now += 10
        lv = json.loads(self.read("/run/kaskad/live.json"))
        self.assertEqual(lv["exits"][self.vid]["kind"], "xray")
        self.assertIn(live.NO_POOL_TEXT, lv["gateway"]["problems"])

    def test_live_json_gives_the_protocol(self):
        """REV-s1-t1 З-5: live.json names the protocol of an xray exit — the bot's way round (the pool not readable)
        labels it by it, not «VLESS» for any xray exit."""
        from kaskad.bot import gateway as bot_gw
        from kaskad.errors import KaskadError
        self.world()
        collect.tick(self.w.runner)
        lv = json.loads(self.read("/run/kaskad/live.json"))
        self.assertEqual((lv["exits"][self.vid]["kind"], lv["exits"][self.vid]["proto"]), ("xray", "vless"))
        self.assertEqual(lv["exits"][self.aid]["proto"], "")
        lv["exits"][self.vid]["proto"] = "hysteria2"                    # a protocol of T11
        with open(paths.live_json(), "w", encoding="utf-8") as f:
            json.dump(lv, f)

        class NoPool(object):
            @staticmethod
            def exits_view(runner, st):
                raise KaskadError("no parser")

        class Ctx(object):
            runner = self.w.runner
        with mock.patch.object(bot_gw, "_ops", lambda: NoPool):
            rows = dict((r["id"], r) for r in bot_gw.exit_rows(Ctx()))
        self.assertEqual(rows[self.vid]["title"], "Hysteria2")

    def test_silent_collector_is_unknown_not_dead(self):
        self.world()
        collect.tick(self.w.runner)
        self.now += live.STALE + 5
        doc, _ = self.head()
        self.assertFalse(doc["live"]["fresh"])
        self.assertEqual(doc["gateway"]["inbounds"][0]["state"], "unknown")
        self.assertIsNone(doc["exits"]["alive"])
        self.assertEqual(doc["problems"], [])

    def test_watchdog_report_unrepaired_only_and_fresh_only(self):
        self.world()
        collect.tick(self.w.runner)
        with mock.patch.object(netcheck, "_now", lambda: self.now):
            netcheck.write_report([{"kind": "drift", "detail": "таблица не совпадает", "repaired": True},
                                   {"kind": "gw_routing", "detail": "нет правила ip rule", "repaired": False,
                                    "error": "RTNETLINK"}], self.now)
            doc, _ = self.head()
            self.assertEqual([(p["kind"], p["item"], p["source"]) for p in doc["problems"]],
                             [("gw_routing", 17, "watch")])
            self.now += summary.WATCH_FRESH + 1
            self._p.stop()
            self._p = mock.patch.object(live, "_now", lambda: self.now - summary.WATCH_FRESH)
            self._p.start()
            doc, _ = self.head()
            self.assertEqual(doc["problems"], [])

    def test_net_check_repair_writes_the_report(self):
        self.world()
        rc, _, err = self.run_cli("net", "check", "--repair", "--quiet")
        self.assertEqual(rc, 0, err)
        rep, age = netcheck.read_report()
        self.assertEqual(rep, [])
        self.assertIsNotNone(age)
        self.assertEqual(oct(os.stat(paths.watch_json()).st_mode & 0o777), "0o600")

    def test_check_runs_the_watchdog_check_now(self):
        self.world()
        self.ads["wg"].running = False
        collect.tick(self.w.runner)
        doc, calls = self.head("--check")
        self.assertTrue(calls)
        self.assertTrue(doc["checked"])
        p, = doc["problems"]                                            # one line, not the collector's and the check's
        self.assertEqual((p["kind"], p["source"], p["item"]), ("gw_inbound", "check", 17))
        self.assertFalse(self.ads["wg"].running)                          # a check repairs nothing

    def test_unfinished_install_of_the_gateway(self):
        self.world()
        self.ok("gw", "disable")
        self.write("/etc/kaskad/gw-wizard.unfinished", "")
        doc, _ = self.head()
        self.assertEqual([(p["kind"], p["item"]) for p in doc["problems"]], [("gw_unfinished", 17)])
        self.assertEqual(doc["gateway"]["inbounds"][0]["state"], "off")
        self.assertIsNone(doc["exits"])


class SummaryFilesTest(RootCase):
    def run_summary(self, **kw):
        return summary.summary(**kw)

    def test_no_state_legacy_and_broken_state(self):
        doc = self.run_summary()
        self.assertEqual((doc["engine"], doc["forwards"], doc["problems"]), ("legacy", None, []))
        self.assertFalse(doc["gateway"]["setup"])
        self.write("/etc/kaskad/state.json", "{broken")
        doc = self.run_summary()
        p, = doc["problems"]
        self.assertEqual((p["kind"], p["item"]), ("state", 16))

    def test_domain_cert_and_masked_panel_without_secrets(self):
        save_state([F443])
        st = json.loads(self.read("/etc/kaskad/state.json"))
        st["site"] = {"domain": "xn--d1acufc.xn--p1ai", "mode": "site", "cert": "letsencrypt"}
        self.write("/etc/kaskad/state.json", json.dumps(st))
        secret = "app-" + "A1b2C3d4" * 10 + "XyZw"
        self.write("/etc/kaskad/panel/config.json", json.dumps({"schema": 1, "proxy": {"on": True, "path": secret,
                                                                                       "domain": "x"}}))
        now = time.time()
        info = {"names": ["xn--d1acufc.xn--p1ai"], "not_after_ts": now + 40 * 86400 + 60, "staging": False}
        with mock.patch.object(sm, "copy_info", lambda: info):
            doc = self.run_summary()
        self.assertEqual((doc["site"]["name"], doc["site"]["mode"]), ("домен.рф", "site"))
        self.assertEqual(doc["site"]["cert"]["days_left"], 40)
        self.assertEqual(doc["problems"], [])
        self.assertEqual(doc["panel"]["masked"], "https://xn--d1acufc.xn--p1ai/%s/" % pconf.mask_path(secret))
        self.assertNotIn(secret[8:-4], json.dumps(doc))
        info["not_after_ts"] = now + 5 * 86400 + 60
        with mock.patch.object(sm, "copy_info", lambda: info):
            doc = self.run_summary()
        p, = doc["problems"]
        self.assertEqual((p["kind"], p["item"]), ("site_cert", 19))
        self.assertIn("через 5 дн.", p["detail"])
        info["not_after_ts"] = now - 86400
        with mock.patch.object(sm, "copy_info", lambda: info):
            doc = self.run_summary()
        self.assertIn("истёк", doc["problems"][0]["detail"])

    def test_update_cache(self):
        self.write("/etc/kaskad/update.json", json.dumps({"at": 1790000000, "latest": "3.2.99", "branch": "k3"}))
        self.assertEqual(self.run_summary()["update"], {"latest": "3.2.99", "branch": "k3", "at": 1790000000})
        self.write("/etc/kaskad/update.json", json.dumps({"latest": "3.2.99; rm -rf /", "branch": "k3"}))
        self.assertIsNone(self.run_summary()["update"])

    def test_empty_pool_with_a_silent_collector_is_the_panels_warning(self):
        save_state([F443])
        st = json.loads(self.read("/etc/kaskad/state.json"))
        st["gateway"] = {"enabled": True, "routes": {"default": {"via": "pool", "fail": "block"}}}
        self.write("/etc/kaskad/state.json", json.dumps(st))
        doc = self.run_summary()
        p, = doc["problems"]
        self.assertEqual((p["kind"], p["detail"]), ("no_exits", exits.NO_EXITS_OFFLINE))
        self.assertEqual(doc["exits"]["total"], 0)

    def test_dedupe(self):
        a = summary.problem("gw_inbound", "Вход L2TP/IPsec не работает", "live")
        b = summary.problem("gw_inbound", "вход L2TP/IPsec не работает", "watch")
        c = summary.problem("gw_service", "xray шлюза (kaskad-gw-xray.service) не работает", "watch")
        d = summary.problem("gw_service", "xray шлюза (kaskad-gw-xray.service) не работает: SOCKS5 и выходы VLESS "
                                          "недоступны", "live")
        self.assertEqual(summary.dedupe([a, b, c, d]), [a, d])

    def test_every_kind_has_a_menu_item(self):
        for kind in ("table_missing", "drift", "ip_forward", "gw_routing", "gw_ip6", "gw_dns", "no_exits",
                     "command", "site_cert", "gw_unfinished"):
            item, text = summary.advice(kind)
            self.assertIn(item, (11, 16, 17, 19), kind)
            self.assertTrue(text.endswith("."), kind)


class SummaryVlessTest(VlessCase):
    """VLESS Reality (3.3, docs/k3/SPEC-vless-inbound.md) is an inbound of the header and of «Что не так» like the
    others: the real adapter, its state from the collector (xray of the gateway serves it)."""

    def setUp(self):
        super().setUp()
        self.now = 1791190000.0
        self._p = mock.patch.object(live, "_now", lambda: self.now)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_vless_in_the_header_and_in_the_problems(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "vless")
        self.ok("gw", "user", "add", "Мама")
        self.add_device()
        self.w.sysd.units[xray.UNIT]["active"] = True
        collect.tick(self.w.runner)
        rc, doc, err = self.run_json("summary")
        self.assertEqual(rc, 0, err)
        ib = {i["kind"]: i for i in doc["gateway"]["inbounds"]}
        self.assertEqual((ib["vless"]["title"], ib["vless"]["short"], ib["vless"]["state"]), ("VLESS", "VLESS", "run"))
        self.assertNotIn("vless", [p["detail"] for p in doc["problems"]])
        self.w.sysd.units[xray.UNIT]["active"] = False
        self.now += 60
        collect.tick(self.w.runner)
        rc, doc, err = self.run_json("summary")
        self.assertEqual(rc, 0, err)
        ib = {i["kind"]: i for i in doc["gateway"]["inbounds"]}
        self.assertEqual(ib["vless"]["state"], "down")
        p = [p for p in doc["problems"] if p["kind"] == "gw_inbound"]
        self.assertEqual([(x["detail"], x["item"]) for x in p], [("Протокол VLESS не работает", 17)])
        self.assertTrue(any("протокол VLESS" in x["detail"] for x in doc["problems"] if x["kind"] == "gw_service"),
                        doc["problems"])
