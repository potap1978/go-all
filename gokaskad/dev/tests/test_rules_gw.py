"""Rules «Россия напрямую» in the gateway: gw_ru in both forms (goldens), its version and the migration from 3.1,
gw_rd and the profiles (user -> device -> default, fail-closed without lists), dnsmasq lines, xray rules of SOCKS5
users with and without the files, the IKEv2 hook, ``kaskadctl rules`` (JSON), the panel routes, the timer.

Goldens (tests/fixtures/nft/gw_rules_*.nft) — regenerate after a deliberate change:
    KASKAD_UPDATE_GOLDEN=1 PYTHONPATH=lib:. python3 -m unittest tests.test_rules_gw
"""

import json
import os
from unittest import mock

from kaskad import nft, paths, secrets, state
from kaskad.gw import dns, exits, hooks, model, net, xray
from kaskad.panel import routes
from kaskad.panel.routes import HttpError, Request
from kaskad.rules import apply as rapply
from kaskad.rules import ops as rops
from kaskad.rules import panel as rpanel
from kaskad.rules import source, store, timer
from tests.fakes_gw import register_dummies
from tests.fakes_nft import F443
from tests.rules_helpers import consented, release, seed_small, small_limits
from tests.test_gw_ops import CliGwCase

GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nft")
UPDATE = os.environ.get("KASKAD_UPDATE_GOLDEN") == "1"
SOCKS_IN = {"tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks",
            "settings": {"auth": "password", "udp": True, "accounts": []}}


class RulesGwCase(CliGwCase):
    def setUp(self):
        super().setUp()
        self._limits = small_limits()
        self._limits.start()

    def tearDown(self):
        self._limits.stop()
        super().tearDown()

    def golden(self, name, text):
        p = os.path.join(GOLD, name)
        if UPDATE:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden %s differs" % name)

    def ru_state(self, **kw):
        """The scenario of test_gw_net (wg + awg2, three devices) with lists and user «Мама» (d1, d3) on ru_direct —
        with the consent ⚠РФ (a node of stage 1: without it ru_direct does not apply, R2 — tests/test_rules_rf_gate.py)."""
        st = self.scenario(**kw)
        seed_small()
        st["gateway"]["users"][0]["rules"] = "ru_direct"
        st["gateway"]["rules"] = {"default": "all_exit"}
        consented(st)
        state.save(st)
        return state.load()


class KernelTest(RulesGwCase):
    RANGES_IN = "add element ip kaskad gw_ru {"

    def ranges_sent(self, scripts):
        return [s for s in scripts if self.RANGES_IN in s]

    def test_full_text_concat_and_plain(self):
        st = self.ru_state(forwards=[F443])
        self.w.users.uids[net.XRAY_USER] = 990
        text = nft.render_full(st)
        self.golden("gw_rules_concat.nft", text)
        lines = [ln.strip() for ln in text.splitlines()]
        pre = dict(net.rules(st))["gw_pre"]
        # after the route mark, before the ct mark and TPROXY — exactly where the 3.1 placeholder stood
        i = pre.index("meta mark set ip saddr map @gw_route")
        self.assertEqual(pre[i + 1:i + 5], [
            "meta mark 0x2fe meta mark set 0x2ff",       # B1 (SEC-s1-t45 С-1): the guard of 0x2fe, always, before Russia
            "ip saddr @gw_rd ip daddr . meta l4proto @gw_ru ip daddr != @gw_force meta mark set 0x201",
            "ip saddr @gw_rd ip daddr @gw_ru_dns ip daddr != @gw_force meta mark set 0x201",
            "ct mark set meta mark or 0x4b000000"])          # stage 1 (§2.3, Б1): no «meta mark != 0x2ff»
        self.assertIn("elements = { 10.77.1.10, 10.77.1.12 }", lines)          # gw_rd: the devices of «Мама»
        # the boot file never carries the ranges nor their version: load_ru fills them on its own
        self.assertNotIn(self.RANGES_IN, text)
        self.assertNotIn("add element ip kaskad gw_ru_ver", text)
        self.assertEqual(self.w.nft.run_script(text, False)[0], 0)
        with mock.patch.object(net, "RU_FORM", "plain"):
            text = nft.render_full(st)
            self.golden("gw_rules_plain.nft", text)
            self.assertIn("ip saddr @gw_rd ip daddr @gw_ru ip daddr != @gw_force meta mark set 0x201",
                          [ln.strip() for ln in text.splitlines()])
            self.assertIn("\t\tauto-merge\n", text)
            self.assertEqual(self.w.nft.run_script(text, False)[0], 0)

    def test_no_apply_ever_sends_or_dry_runs_the_ranges(self):
        st = self.ru_state(forwards=[F443])
        nft.apply(st, self.w.runner)
        self.assertEqual(self.w.nft.elements("gw_ru"), [])                 # an apply never loads them
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])   # ... and that is no drift
        with mock.patch.object(net, "RU_CHUNK", 2):
            res = rapply.ensure_kernel(self.w.runner, st)
        self.assertEqual(res["action"], "loaded")
        load = self.w.nft.applied[-1]
        self.assertEqual(load.count(self.RANGES_IN), 2)                    # 3 ranges, chunks of 2, one transaction
        self.assertTrue(load.startswith("flush set ip kaskad gw_ru\n"))
        self.assertEqual(len(self.w.nft.elements("gw_ru")), 3)
        self.assertEqual(len(self.w.nft.elements("gw_ru_ver")), 1)
        ver = self.w.nft.elements("gw_ru_ver")
        # changes of people, gw apply, a forward, the watchdog: the ranges stay, nobody sends or dry-runs them
        st["gateway"]["devices"][1]["rules"] = "ru_direct"
        state.save(st)
        nft.apply(state.load(), self.w.runner)
        self.ok("gw", "device", "route", "d-00000001", "direct")
        self.ok("gw", "apply")
        self.ok("fwd", "add", "--proto", "udp", "--port", "51821", "--to", "198.51.100.7", "--to-port", "51820")
        from kaskad import netcheck
        netcheck.check(self.w.runner, repair=True)
        self.assertEqual(len(self.w.nft.elements("gw_ru")), 3)
        self.assertEqual(self.w.nft.elements("gw_ru_ver"), ver)
        self.assertEqual(self.ranges_sent(self.w.nft.checked), [])         # never in nft -c
        self.assertEqual(len(self.ranges_sent(self.w.nft.applied)), 1)     # once in nft -f: the load
        self.assertNotIn(self.RANGES_IN, self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertEqual(rapply.ensure_kernel(self.w.runner, state.load())["action"], "ok")    # no second load
        self.assertEqual(len(self.ranges_sent(self.w.nft.applied)), 1)
        self.assertNotIn("gw_ru", [s for s in self.w.nft.set_listings if s != "gw_ru_ver"])
        # new lists: loaded again (by the update or the load timer), the old version is replaced
        seed_small(ranges=("2.26.8.0/24", "93.158.0.0/16"))
        self.assertEqual(rapply.ensure_kernel(self.w.runner, state.load())["action"], "loaded")
        self.assertEqual(sorted(self.w.nft.elements("gw_ru")), ["2.26.8.0/24 . 0-255", "93.158.0.0/16 . 0-255"])
        self.assertNotEqual(self.w.nft.elements("gw_ru_ver"), ver)

    def test_a_failed_load_waits_logs_once_and_the_gateway_lives(self):
        st = self.ru_state(forwards=[F443])
        nft.apply(st, self.w.runner)
        logs = []
        self.w.nft.fail_apply = lambda text: "Error: Could not process rule: Cannot allocate memory\n" \
            if self.RANGES_IN in text else None
        now = [1790000000.0]
        with mock.patch.object(rapply, "_now", lambda: now[0]):
            res = rapply.ensure_kernel(self.w.runner, st, log=logs.append)
            self.assertEqual(res["action"], "failed")
            self.assertIn("Cannot allocate memory", res["error"])
            self.assertEqual(len(logs), 1)
            self.assertIn("шлюз и пробросы работают", logs[0])
            self.assertIn("через 5 мин", logs[0])
            # the next 5 minutes: no try at all (no nft -f), no log
            tries = len(self.w.nft.applied)
            now[0] += 120
            self.assertEqual(rapply.ensure_kernel(self.w.runner, st, log=logs.append)["action"], "waiting")
            self.assertEqual(len(self.w.nft.applied), tries)
            # after the pause: tried again, still failing — a longer pause, still one line in the journal
            now[0] += 200
            res = rapply.ensure_kernel(self.w.runner, st, log=logs.append)
            self.assertEqual((res["action"], res["fails"]), ("failed", 2))
            self.assertEqual(res["next"] - now[0], rapply.BACKOFF[1])
            self.assertEqual(len(logs), 1)
            # gw check says it, the minute watchdog stays quiet and does not try
            rc, out, err = self.run_cli("gw", "check")
            self.assertIn("сети России не загружены в таблицу (Error: Could not process rule: Cannot allocate memory",
                          out)
            tries = len(self.w.nft.applied)
            rc, out, err = self.run_cli("gw", "check", "--repair", "--quiet")
            self.assertEqual(len(self.ranges_sent(self.w.nft.applied[tries:])), 0)
            self.assertNotIn("Россия", err)
            # the gateway works meanwhile: a change of people applies at once
            self.ok("gw", "device", "route", "d-00000001", "direct")
            self.assertEqual(self.w.nft.elements("gw_route")["10.77.1.10"], 0x201)
            # --force tries now; the cause gone — loaded, the state says so
            self.w.nft.fail_apply = None
            res = rapply.ensure_kernel(self.w.runner, st, force=True, log=logs.append)
            self.assertEqual(res["action"], "loaded")
            self.assertTrue(rapply.load_state()["ok"])
            self.assertEqual(len(logs), 2)
            self.assertIn("загружены в таблицу — 3 сетей", logs[1])

    def test_a_load_that_does_not_fit_its_timeout(self):
        st = self.ru_state()
        nft.apply(st, self.w.runner)
        orig = self.w.runner.run

        def slow(argv, input=None, check=False, timeout=60):
            if input and self.RANGES_IN in input:
                self.assertEqual(timeout, net.RU_LOAD_TIMEOUT)
                from kaskad.sysrun import Result
                return Result(argv, 124, "", "timeout after %ss" % timeout)
            return orig(argv, input=input, check=check, timeout=timeout)
        with mock.patch.object(self.w.runner, "run", slow):
            res = rapply.ensure_kernel(self.w.runner, st, log=lambda text: None)
        self.assertEqual((res["action"], res["error"]), ("failed", "nft не уложился в 30 с"))
        self.assertEqual(self.w.nft.elements("gw_ru"), [])

    def test_the_load_timer_command(self):
        self.ru_state()
        rc, doc, _ = self.run_json("rules", "load")
        self.assertEqual((rc, doc["action"]), (0, "no_table"))             # the gateway part is not in the table yet
        nft.apply(state.load(), self.w.runner)
        rc, out, err = self.run_cli("rules", "load", "--quiet")
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(len(self.w.nft.elements("gw_ru")), 3)
        rc, doc, _ = self.run_json("rules", "load")
        self.assertEqual(doc["action"], "ok")
        self.w.nft.flush_ruleset()                                          # the table lost and restored by the watchdog
        nft.restore(self.w.runner)
        self.assertEqual(self.w.nft.elements("gw_ru"), [])
        rc, doc, _ = self.run_json("rules", "load")                         # the timer brings the ranges back
        self.assertEqual((rc, doc["action"]), (0, "loaded"))
        self.w.nft.fail_apply = lambda text: "Error: nope\n" if self.RANGES_IN in text else None
        self.w.nft.flush_ruleset()
        nft.restore(self.w.runner)
        rc, out, err = self.run_cli("rules", "load", "--quiet")
        self.assertEqual(rc, 1)
        self.assertIn("не загрузились", out)
        rc, out, _ = self.run_cli("rules", "status")
        self.assertIn("в таблице nftables: НЕ загружены (Error: nope", out)

    def test_migration_from_the_plain_gw_ru_of_31_and_back(self):
        st = self.scenario()
        with mock.patch.object(net, "RU_FORM", "plain"):
            nft.apply(st, self.w.runner)                         # a 3.1-like table: plain gw_ru, no lists
        self.assertEqual(self.w.nft.table()["sets"]["gw_ru"]["type"], "ipv4_addr")
        seed_small()
        nft.apply(state.load(), self.w.runner)
        script = self.w.nft.applied[-1]
        self.assertIn("delete set ip kaskad gw_ru\nadd set ip kaskad gw_ru { type ipv4_addr . inet_proto; "
                      "flags interval; }\nflush set ip kaskad gw_ru_ver", script)
        self.assertLess(script.index("flush chain ip kaskad gw_pre"), script.index("delete set ip kaskad gw_ru"))
        self.assertNotIn(self.RANGES_IN, script)
        self.assertEqual(self.w.nft.table()["sets"]["gw_ru"]["type"], "ipv4_addr . inet_proto")
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        self.assertEqual(rapply.ensure_kernel(self.w.runner, state.load())["action"], "loaded")
        self.assertEqual(len(self.w.nft.elements("gw_ru")), 3)
        with mock.patch.object(net, "RU_FORM", "plain"):
            probs = nft.verify(state.load(), nft.read_live(self.w.runner))
            self.assertTrue(any("другого вида" in p for p in probs), probs)
            nft.apply(state.load(), self.w.runner)                  # the type changes, the version goes with it
            self.assertEqual(self.w.nft.elements("gw_ru"), [])
            self.assertEqual(self.w.nft.elements("gw_ru_ver"), [])
            self.assertEqual(rapply.ensure_kernel(self.w.runner, state.load())["action"], "loaded")
            self.assertEqual(self.w.nft.elements("gw_ru"), ["2.26.8.0/24", "5.8.0.0-5.8.2.255", "77.88.0.0/23"])
            self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])

    def test_type_and_version_are_read_without_guessing(self):
        ok = net._ru_type_ok
        self.assertTrue(ok({"type": ["ipv4_addr", "inet_proto"]}, "concat"))
        self.assertTrue(ok({"type": "ipv4_addr . inet_proto"}, "concat"))
        self.assertFalse(ok({"type": "ipv4_addr"}, "concat"))
        self.assertTrue(ok({"type": "ipv4_addr"}, "plain"))
        self.assertTrue(ok({}, "concat"))                          # printed in a way we do not know: no migration
        live = {"set_info": {"gw_ru_ver": {"elem": ["26.43.60.77"]}}}
        self.assertEqual(net.live_ru_version(live), 0x1a2b3c4d)
        self.assertEqual(net.ru_version_elem(0x1a2b3c4d), "26.43.60.77")
        self.assertEqual(net.live_ru_version({"set_info": {"gw_ru_ver": {"elem": []}}}), 0)
        self.assertIsNone(net.live_ru_version({"set_info": {}}))
        self.assertIsNone(net.live_ru_version({"set_info": {"gw_ru_ver": {"elem": ["1.1.1.1", "2.2.2.2"]}}}))

    def test_without_lists_nobody_goes_directly(self):
        st = self.scenario()
        st["gateway"]["rules"] = {"default": "ru_direct"}
        state.save(consented(st))
        st = state.load()
        nft.apply(st, self.w.runner)
        self.assertEqual(self.w.nft.elements("gw_rd"), [])                 # fail-closed: no lists, no direct
        self.assertEqual(self.w.nft.elements("gw_ru"), [])
        self.assertEqual(self.w.nft.elements("gw_ru_ver"), [])
        self.assertEqual(nft.verify(st, nft.read_live(self.w.runner)), [])
        d = st["gateway"]["devices"][0]
        self.assertEqual(rapply.profile(st, d), {"profile": "ru_direct", "from": "default", "applied": "all_exit",
                                                 "why": "no_lists"})
        seed_small()
        self.assertEqual(rapply.profile(st, d)["applied"], "ru_direct")
        self.assertTrue(net.update_map(self.w.runner, st))                  # the collector brings gw_rd
        self.assertEqual(sorted(self.w.nft.elements("gw_rd")), ["10.77.1.10", "10.77.1.11", "10.77.1.12", "10.77.3.10"])
        self.assertFalse(net.update_map(self.w.runner, st))

    def test_profiles_device_user_default(self):
        st = self.ru_state()
        g = st["gateway"]
        d1, d2, d3 = g["devices"]
        self.assertEqual(model.profile_of(st, d1), {"profile": "ru_direct", "from": "user"})
        self.assertEqual(model.profile_of(st, d2), {"profile": "all_exit", "from": "default"})
        d3["rules"] = "all_exit"
        self.assertEqual(model.profile_of(st, d3), {"profile": "all_exit", "from": "device"})
        d3["rules"] = "inherit"
        self.assertEqual(model.profile_of(st, d3)["from"], "user")
        d3["rules"] = "nonsense"                                            # broken: falls through, reported
        self.assertEqual(model.profile_of(st, d3)["from"], "user")
        self.assertTrue(any("профиль правил" in p for p in model.validate(st)))
        self.assertEqual(model.rules_conf({"gen": 1, "gateway": {}}), {"default": "ru_direct", "block_ads": True,
                                                                       "block_torrent": False})
        # a dict without ``gen`` (not through state.load) is a new node — the safe side (SEC-s1-r2 З1)
        self.assertEqual(model.rules_conf({"gateway": {}})["default"], "all_exit")
        d1["enabled"] = False                                               # a disabled device: not in gw_rd
        self.assertEqual(rapply.rd(st), ["10.77.1.12"])

    def test_ikev2_hook_puts_the_address_into_gw_rd(self):
        st = self.ru_state()
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": True, "subnet": "10.77.8.0/24"}
        st["gateway"]["devices"][0]["access"].append("ikev2")
        state.save(st)
        nft.apply(state.load(), self.w.runner)
        res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertTrue(res["ru_direct"])
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_rd"))
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        hooks.hook("ikev2", "down", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rd"))

    def test_ikev2_hook_on_the_fallback_block_keeps_russia(self):
        """Stage 1 (§2.3, Б1; REV-s1-t4 З5): a lease «up» on the LAST STEP «block» of its group (the exits unfit) — the
        address goes into gw_ruonly (Russia directly, no services of the node) through ``hooks.hook`` itself; a block
        by choice or a device without «Россия напрямую» — never."""
        st = self.ru_state()
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": True, "subnet": "10.77.8.0/24"}
        st["gateway"]["devices"][0]["access"].append("ikev2")
        state.save(st)
        nft.apply(state.load(), self.w.runner)
        for step, want in (("last_block", True), ("chosen", False)):
            tgt = {"mark": exits.MARK_BLOCK, "exit": None, "route": {"via": "pool"}, "group": "main", "step": step}
            with mock.patch.object(exits, "target", lambda *a, **kw: dict(tgt)):
                res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
            self.assertEqual(res["ru_only"], want, step)
            self.assertEqual("10.77.8.10" in self.w.nft.elements("gw_ruonly"), want, step)
            self.assertEqual("10.77.8.10" in self.w.nft.elements("gw_live"), want, step)    # a block: not live
            hooks.hook("ikev2", "down", "telefon-0001", "10.77.8.10", self.w.runner)
            self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_ruonly"))
        st = state.load()                                         # no «Россия напрямую»: no Russia on the block either
        st["gateway"]["devices"][0]["rules"] = "all_exit"
        state.save(st)
        tgt = {"mark": exits.MARK_BLOCK, "exit": None, "route": {"via": "pool"}, "group": "main", "step": "last_block"}
        with mock.patch.object(exits, "target", lambda *a, **kw: dict(tgt)):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertFalse(res["ru_only"])
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_ruonly"))

    def test_probe_checks_the_new_constructs(self):
        ok, err = net.probe(self.w.runner)
        self.assertTrue(ok, err)
        script = self.w.nft.checked[-1]
        self.assertIn("add element ip kaskad_gwprobe gw_ru { 192.0.2.0/24 . 0-255, 198.51.100.0-198.51.100.99 . 0-255 }",
                      script)
        self.assertIn("add element ip kaskad_gwprobe gw_force { 203.0.113.1 }", script)


class DnsTest(RulesGwCase):
    def conf(self):
        return self.read("/etc/kaskad/gw/dnsmasq.conf")

    def test_lines_of_the_rules(self):
        st = self.ru_state()
        dns.apply(st, self.w.runner, via_pool=True)
        conf = self.conf().splitlines()
        self.assertIn("max-cache-ttl=600", conf)
        self.assertIn("nftset=/youtube.com/4#ip#kaskad#gw_force", conf)
        self.assertIn("nftset=/yandex.ru/4#ip#kaskad#gw_ru_dns", conf)
        self.assertIn("nftset=/www.mos.ru/4#ip#kaskad#gw_ru_dns", conf)        # full: — with its subdomains
        self.assertIn("address=/ad.mail.ru/", conf)
        self.assertIn("address=/telemetry.example/", conf)
        self.assertNotIn("nftset=/telemetry.example/4#ip#kaskad#gw_ru_dns", conf)   # blocked wins over direct
        self.assertNotIn("nftset=/music.youtube.com/4#ip#kaskad#gw_ru_dns", conf)   # under a «force» domain
        self.assertFalse([ln for ln in conf if "Bad Name" in ln or "netflix" in ln])  # invalid, keyword
        self.assertFalse([ln for ln in conf if "tracker.example" in ln])           # torrents: off by default
        self.assertTrue(all(len(ln) < 1000 for ln in conf))
        self.assertEqual(dns.saved_features(), {"version": "2.90", "nftset": True,
                                                "at": dns.saved_features()["at"]})
        # nobody of the kernel path on ru_direct: the sets are not filled (dnsmasq spares libnftables), ads still go
        st["gateway"]["users"][0]["rules"] = "all_exit"
        dns.apply(st, self.w.runner, via_pool=True)
        self.assertFalse([ln for ln in self.conf().splitlines() if ln.startswith(("nftset=", "max-cache-ttl"))])
        self.assertIn("address=/ad.mail.ru/", self.conf())
        st["gateway"]["rules"] = {"block_ads": False, "block_torrent": True}
        dns.apply(st, self.w.runner, via_pool=True)
        self.assertNotIn("address=/ad.mail.ru/", self.conf())
        self.assertIn("address=/tracker.example/", self.conf())

    def test_dnsmasq_without_nftset(self):
        st = self.ru_state()
        self.w.dnsmasq.nftset = False
        res = dns.apply(st, self.w.runner, via_pool=True)
        self.assertFalse(res["nftset"])
        conf = self.conf()
        self.assertNotIn("nftset=", conf)
        self.assertIn("dnsmasq без nftset", conf)
        self.assertIn("address=/ad.mail.ru/", conf)
        self.assertEqual(dns.status(self.w.runner)["nftset"], False)
        doc = rops.status(st)
        self.assertIn("nftset: нет", rops.summary(doc))

    def test_without_lists_the_config_is_as_before(self):
        st = self.scenario()
        dns.apply(st, self.w.runner, via_pool=True)
        self.assertNotIn("nftset", self.conf())
        self.assertNotIn("\naddress=/", self.conf())


class XrayTest(RulesGwCase):
    def socks_state(self):
        st = self.ru_state(save=False)
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        g = st["gateway"]
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in g["devices"]:
            d["access"].append("socks5")
        return st

    def test_without_files_no_ext_rules(self):
        st = self.socks_state()
        cfg = xray.render(st, secrets.load(), exits.pool(self.w.runner))
        self.assertFalse([r for r in cfg["routing"]["rules"] if "ext:" in json.dumps(r)])

    def test_rules_with_files(self):
        st = self.socks_state()
        sec = secrets.load()
        res = rapply.sync_assets(st, sec, list(xray._adapters(st)))
        self.assertTrue(res["changed"] and res["geoip"] and res["geosite"])
        d = rapply.asset_dir()
        self.assertEqual(oct(os.stat(os.path.join(d, rapply.GEOIP_ASSET)).st_mode & 0o777), "0o644")
        self.assertFalse(rapply.sync_assets(st, sec, list(xray._adapters(st)))["changed"])
        cfg = xray.render(st, sec, exits.pool(self.w.runner))
        rules = cfg["routing"]["rules"]
        block = [r for r in rules if r.get("outboundTag") == "block" and "domain" in r and "user" not in r
                 and r.get("inboundTag") == ["socks-in"]]
        self.assertEqual(block[0]["domain"], ["ext:roscomvpn-geosite.dat:category-ads",
                                              "ext:roscomvpn-geosite.dat:win-spy"])
        force = [r for r in rules if "ext:roscomvpn-geosite.dat:youtube" in r.get("domain", [])]
        # «Мама»: telefon (pool), noutbuk (its VLESS exit with fail=direct): each «always through the exit» rule goes to
        # the target of its devices. Stage 1 (SPEC-stage1-exits §1.3 отличия 1, 2; §2.4): the target is the exit the
        # device is pinned to by the decision (``exits.target``: an outbound), not a balancer per route (gw-all,
        # b-<id>-direct are gone); the rules are by target, their users — the devices of that target
        dec = exits.decision(st, pool_exits=exits.pool(self.w.runner))
        logins = {"d-00000001": "telefon-0001", "d-00000003": "noutbuk-0003"}
        want = {}
        for dev in st["gateway"]["devices"]:
            if dev["id"] in logins:
                t = exits.target(st, dev, dec)["xray"]
                self.assertNotIn("balancerTag", t)
                want.setdefault(json.dumps(t, sort_keys=True), []).append(logins[dev["id"]])
        got = dict((json.dumps(dict((k, r[k]) for k in ("outboundTag", "balancerTag") if k in r), sort_keys=True),
                    sorted(r["user"])) for r in force)
        self.assertEqual(got, dict((k, sorted(v)) for k, v in want.items()))
        ip = [r for r in rules if r.get("ip") == ["ext:roscomvpn-geoip.dat:direct"]][0]
        self.assertEqual((ip["outboundTag"], ip["user"]), ("direct", ["noutbuk-0003", "telefon-0001"]))
        direct = [r for r in rules if "ext:roscomvpn-geosite.dat:category-ru" in r.get("domain", [])][0]
        self.assertEqual(direct["outboundTag"], "direct")
        self.assertNotIn("ext:roscomvpn-geosite.dat:faceit", direct["domain"])       # not in this file: not named
        # all before the route rules of the users; the block of private addresses stays first
        first_user_route = next(i for i, r in enumerate(rules) if r.get("user") and "domain" not in r and "ip" not in r)
        self.assertLess(rules.index(ip), first_user_route)
        private = next(i for i, r in enumerate(rules) if "127.0.0.0/8" in r.get("ip", []))
        self.assertLess(private, rules.index(block[0]))
        self.assertEqual(self.w.xray.fail, None)
        os.makedirs(os.path.dirname(xray.run_binary()), exist_ok=True)
        self.with_xports()                                     # B3: the table of stage 1 before xray
        res = xray.apply(st, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertTrue(res["changed"])
        xray.remove(self.w.runner)
        self.assertFalse(os.path.exists(os.path.join(d, rapply.GEOIP_ASSET)))

    def test_block_route_closes_russia_too_and_ip_checkers_go_through_the_exit(self):
        # стенд T-O, O-8: устройство SOCKS5 с маршрутом «блок» открывало российские сайты с адреса узла
        st = self.socks_state()
        sec = secrets.load()
        rapply.sync_assets(st, sec, list(xray._adapters(st)))
        cfg = xray.render(st, sec, exits.pool(self.w.runner))
        force = [r for r in cfg["routing"]["rules"] if "ext:roscomvpn-geosite.dat:youtube" in r.get("domain", [])]
        self.assertTrue(force and all("domain:ifconfig.me" in r["domain"] and "domain:ipify.org" in r["domain"] for r in force))
        tel = next(d for d in st["gateway"]["devices"] if d["id"] == "d-00000001")
        tel["route"] = {"via": "block"}
        cfg = xray.render(st, sec, exits.pool(self.w.runner))
        ru_rules = [r for r in cfg["routing"]["rules"] if r.get("outboundTag") == "direct" and r.get("user")]
        self.assertTrue(ru_rules)
        self.assertFalse([r for r in ru_rules if "telefon-0001" in r["user"]])           # блок — без России напрямую
        self.assertTrue(all("noutbuk-0003" in r["user"] for r in ru_rules))


class CliPanelTimerTest(RulesGwCase):
    def test_rules_cli_json(self):
        self.enabled_world()
        with state.transaction() as st:
            st["gen"] = 1                 # an updated node: ru_direct by default as in 3.3.x (a new one — all_exit)
            st["gateway"].pop("rules", None)          # the enable of a new node wrote all_exit (B5 П3); 3.3.x has none
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        seed_small()
        rc, doc, _ = self.run_json("rules", "status")
        self.assertEqual(rc, 0)
        self.assertTrue(doc["ready"])
        self.assertEqual(doc["default_applied"], "ru_direct")
        dev = model.devices(state.load())[0]
        rc, doc, _ = self.run_json("rules", "profile", "device", dev["id"], "all_exit")
        self.assertEqual((rc, doc["profile"]), (0, "all_exit"))
        self.assertEqual(model.find_device(state.load(), dev["id"])["rules"], "all_exit")
        self.assertEqual(self.w.nft.elements("gw_rd"), [])
        rc, doc, _ = self.run_json("rules", "profile", "device", dev["id"], "inherit")
        self.assertEqual(self.w.nft.elements("gw_rd"), [dev["ips"]["wg"]])       # the default: ru_direct
        rc, doc, _ = self.run_json("rules", "default", "all_exit")
        self.assertEqual(doc["rules"]["default"], "all_exit")
        self.assertEqual(self.w.nft.elements("gw_rd"), [])
        rc, doc, _ = self.run_json("rules", "block", "torrent", "on")
        self.assertTrue(doc["rules"]["block_torrent"])
        rc, doc, _ = self.run_json("rules", "profile", "user", "Никто", "ru_direct")
        self.assertEqual(rc, 1)
        rc, out, err = self.run_cli("rules", "block", "ads", "maybe")
        self.assertEqual(rc, 2)
        rc, out, _ = self.run_cli("gw", "status")
        self.assertIn("правила: списки от 04.10", out)                  # fmt_datetime (T-M6-332b №7)

    def test_update_through_the_cli_applies_to_the_gateway(self):
        self.enabled_world()
        with mock.patch.object(source, "Http", lambda: release()):
            rc, doc, err = self.run_json("rules", "update")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["changed"])
        self.assertTrue(doc["apply"]["applied"])
        self.assertEqual(len(self.w.nft.elements("gw_ru")), 1101)
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])

    def test_timer_comes_with_the_gateway(self):
        self.enabled_world()
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), timer.TIMER)))
        self.assertIn("RandomizedDelaySec=2h", self.read("/etc/systemd/system/kaskad-gw-rules.timer"))
        self.assertIn("rules update", self.read("/etc/systemd/system/kaskad-gw-rules.service"))
        self.assertTrue(self.w.sysd.units[timer.TIMER]["enabled"])
        # the load of the networks: at boot after kaskad-net, then every 5 minutes (a no-op while they are there)
        load_timer = self.read("/etc/systemd/system/kaskad-gw-rules-load.timer")
        self.assertIn("OnBootSec=20s", load_timer)
        self.assertIn("OnUnitInactiveSec=5min", load_timer)
        load_svc = self.read("/etc/systemd/system/kaskad-gw-rules-load.service")
        self.assertIn("rules load --quiet", load_svc)
        self.assertIn("After=kaskad-net.service", load_svc)
        self.assertTrue(self.w.sysd.units[timer.LOAD_TIMER]["enabled"])
        starts = [c for c in self.w.sysd.calls if c[:3] == ["systemctl", "start", "--no-block"]]
        self.assertEqual(starts, [["systemctl", "start", "--no-block", timer.SERVICE]])     # first download, once
        self.ok("gw", "apply")
        self.assertEqual(len([c for c in self.w.sysd.calls if c[:3] == ["systemctl", "start", "--no-block"]]), 1)
        self.ok("gw", "disable")
        self.assertFalse(self.w.sysd.units[timer.TIMER]["enabled"])
        self.assertFalse(self.w.sysd.units[timer.LOAD_TIMER]["enabled"])
        seed_small()
        self.ok("gw", "purge", "--yes")
        for name in timer.UNITS:
            self.assertFalse(os.path.exists(os.path.join(paths.systemd_dir(), name)), name)
        self.assertIsNone(store.active())

    def call(self, method, path, body=None):
        r, m = routes.find(method, path)
        self.assertIsNotNone(m, path)
        return r.handler(Request(method, path, body=json.dumps(body).encode() if body is not None else b"", match=m))

    def test_panel_routes(self):
        self.enabled_world()
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        seed_small()
        self.assertIn(("GET", "/api/rules"), [(r.method, r.pattern) for r in routes.ROUTES])
        doc = json.loads(self.call("GET", "/api/rules").body)
        self.assertTrue(doc["ready"])
        self.assertEqual(doc["lists"]["geoip"]["tag"], "202610040942")
        dev = model.devices(state.load())[0]
        resp = self.call("PUT", "/api/rules/profile", {"target": "device", "id": dev["id"], "profile": "all_exit"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body)["status"]["devices"][0]["applied"], "all_exit")
        ver = json.loads(self.call("GET", "/api/rules").body)["ver"]      # the form «Правила» sends its ver (R4)
        resp = self.call("PUT", "/api/rules/profile", {"target": "default", "profile": "all_exit", "ver": ver})
        self.assertEqual(json.loads(resp.body)["rules"]["default"], "all_exit")
        for body, status in (({"target": "device", "id": "d-nothere", "profile": "ru_direct"}, 404),
                             ({"target": "device", "id": dev["id"], "profile": "fast"}, 400),
                             ({"target": "group", "profile": "ru_direct"}, 400),
                             ({"target": "default", "profile": "inherit", "ver": ver}, 400),
                             ({"target": "default", "profile": "all_exit"}, 400)):           # no ver: ver_required
            with self.assertRaises(HttpError) as cm:
                self.call("PUT", "/api/rules/profile", body)
            self.assertEqual(cm.exception.status, status, body)
        resp = self.call("PUT", "/api/rules/blocks", {"ads": False})
        self.assertFalse(json.loads(resp.body)["rules"]["block_ads"])
        with self.assertRaises(HttpError) as cm:
            self.call("PUT", "/api/rules/blocks", {"ads": "yes"})
        self.assertEqual(cm.exception.status, 400)
        with mock.patch.object(source, "Http", lambda: release()):
            resp = self.call("POST", "/api/rules/update", {"force": True})
        self.assertTrue(json.loads(resp.body)["changed"])
        with store.update_lock():
            with self.assertRaises(HttpError) as cm:
                self.call("POST", "/api/rules/update", {})
        self.assertEqual(cm.exception.status, 409)
        self.assertTrue(rpanel.get_rules)

    def test_panel_changes_answer_the_warnings_of_the_apply(self):
        """C7: like every change of kaskad.panel.api, a change of the rules answers ``warnings`` — of the download and
        of the apply to the gateway (xray, DNS, the table); what the system did otherwise stays in the log."""
        from kaskad.gw import ops as gops
        self.enabled_world()
        seed_small()
        real = gops._apply

        def warned(st, sec, runner, probe=True, pre=None):
            res = real(st, sec, runner, probe=probe, pre=pre)
            res.setdefault("warnings", []).append("xray: конфиг не принят — работает прежний (проверка)")
            return res
        with mock.patch.object(gops, "_apply", warned):
            for method, path, body in (("PUT", "/api/rules/blocks", {"ads": False}),
                                       ("PUT", "/api/rules/profile", {"target": "default", "profile": "all_exit"})):
                if path == "/api/rules/profile":
                    body = dict(body, ver=json.loads(self.call("GET", "/api/rules").body)["ver"])
                doc = json.loads(self.call(method, path, body).body)
                self.assertIn("xray: конфиг не принят — работает прежний (проверка)", doc["warnings"], path)
                self.assertNotIn("apply", doc)
                self.assertIn("status", doc)

        def applied(runner=None, lock_timeout=None):
            return {"applied": True, "warnings": ["DNS: dnsmasq не перезапустился (проверка)"], "kernel": {}}
        with mock.patch.object(source, "Http", lambda: release()), mock.patch.object(rops, "apply_system", applied):
            doc = json.loads(self.call("POST", "/api/rules/update", {"force": True}).body)
        self.assertTrue(doc["changed"])
        self.assertIn("DNS: dnsmasq не перезапустился (проверка)", doc["warnings"])
        self.assertNotIn("apply", doc)
