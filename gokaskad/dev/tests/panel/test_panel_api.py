"""The API of the panel (WP-P1, spec §3.2) on the fake node: every endpoint's happy path and an error, reads that
run nothing, the generation after changes, secrets only in the issue."""

import io
import json
import os
import stat
from unittest import mock

from kaskad import errors, paths, snapshot, state, util
from kaskad.gw import exits, live, model
from kaskad.gw.inbounds import pki
from kaskad.panel import api, auth, conf
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, write_pool
from tests.fakes_nft import SS_SSHD, save_state
from tests.panel.panelcase import PanelCase, fixture


class ReadsTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()

    def test_overview_with_the_collector(self):
        self.collect()
        doc = self.ok("GET", "/api/overview")
        self.assertEqual(doc["engine"], "nft")
        g = doc["gateway"]
        self.assertEqual((g["enabled"], g["users"], g["devices"], g["online"], g["host"]),
                         (True, 1, 0, 0, "203.0.113.10"))
        (wg,) = doc["inbounds"]
        self.assertEqual((wg["kind"], wg["running"], wg["sessions"], wg["ports"]), ("wg", True, 0, "51820/udp"))
        self.assertEqual(doc["exits"]["total"], 2)
        self.assertEqual(sorted(x["kind"] for x in doc["exits"]["items"]), ["awg", "xray"])
        self.assertEqual(doc["forwards"]["count"], 1)
        self.assertIsNotNone(doc["forwards"]["packets"])
        self.assertTrue(doc["live"]["fresh"])
        self.assertEqual(doc["warnings"], [])
        self.assertIn("cpu", doc["host"])

    def silent(self, enabled_at="2026-01-01T00:00:00Z"):
        """The gateway was switched on long ago (or ``enabled_at``) and the collector writes nothing."""
        st = state.load()
        st["gateway"]["enabled_at"] = enabled_at
        state.save(st)
        snapshot.forget()

    def test_overview_without_the_collector_says_unknown(self):
        self.silent()
        doc = self.ok("GET", "/api/overview")
        self.assertFalse(doc["live"]["fresh"])
        self.assertNotIn("starting", doc["live"])
        self.assertIsNone(doc["inbounds"][0]["running"])
        self.assertIsNone(doc["gateway"]["online"])
        self.assertIsNone(doc["forwards"]["packets"])
        self.assertIsNone(doc["devices"])
        self.assertIn(api.stale_text(), doc["warnings"])
        self.assertIn("выходов", api.stale_text())

    def test_exits_without_the_collector_are_no_data(self):
        """T-G1b N2: the collector stopped — the tile «Выходы 3 из 3 · все работают» and «работает» of every exit
        stayed, though the exits' state is the collector's too (its watchdog writes exits.json): now «нет данных»
        in the overview, the exits and the gateway view; the key's own problem stays. Fresh data — states again."""
        self.silent()
        doc = self.ok("GET", "/api/overview")
        self.assertEqual((doc["exits"]["total"], doc["exits"]["alive"]), (2, None))
        self.assertEqual([(x["state"], x["alive"], x["usable"]) for x in doc["exits"]["items"]],
                         [(exits.NO_DATA_STATE, None, None)] * 2)
        rows = self.ok("GET", "/api/gw/exits")["exits"]
        self.assertEqual([(r["state"], r["usable"], r["text"]) for r in rows], [("nodata", None, exits.NO_DATA)] * 2)
        self.assertEqual([r["state"] for r in self.ok("GET", "/api/gw")["exits"]], ["nodata"] * 2)
        self.collect()
        doc = self.ok("GET", "/api/overview")
        states = dict((x["id"], x["state"]) for x in doc["exits"]["items"])
        self.assertEqual((doc["exits"]["alive"], states[self.aid]), (1, "alive"))     # VLESS: xray is not asked here
        self.assertNotIn("nodata", states.values())
        texts = dict((r["id"], r["text"]) for r in self.ok("GET", "/api/gw/exits")["exits"])
        self.assertTrue(texts[self.aid].startswith("работает"), texts)
        row = exits.no_data({"id": "e-0123456789", "state": "dead", "problem": "ключ не разобран", "usable": False})
        self.assertEqual(row["state"], "dead")                             # a broken key is told without probes

    def test_right_after_enable_the_collector_is_starting(self):
        """T-G1b O: right after «Включить шлюз» the collector has not made its first round yet — no alarm «Нет
        свежих данных», but live.starting (seconds the page waits calmly); the same just after a boot."""
        doc = self.ok("GET", "/api/overview")                            # gateway() has just switched it on
        self.assertFalse(doc["live"]["fresh"])
        self.assertTrue(0 < doc["live"]["starting"] <= api.START_GRACE + 1, doc["live"])
        self.assertNotIn(api.stale_text(), doc["warnings"])
        self.assertEqual(doc["exits"]["alive"], None)                    # still nothing known about the exits
        self.assertIn("starting", self.ok("GET", "/api/gw")["live"])
        self.assertIsNotNone(live.epoch(state.load()["gateway"]["enabled_at"]))
        self.silent(enabled_at=util.now_iso())
        self.assertIn("starting", self.ok("GET", "/api/overview")["live"])
        self.silent()                                                     # long ago: silence again
        self.assertIn(api.stale_text(), self.ok("GET", "/api/overview")["warnings"])
        self.write("/proc/uptime", "42.00 80.00\n")                      # the node booted 42 s ago
        doc = self.ok("GET", "/api/overview")
        self.assertEqual((doc["live"]["starting"], doc["warnings"]), (api.START_GRACE - 42 + 1, []))
        self.write("/proc/uptime", "4200.00 8000.00\n")
        self.assertIn(api.stale_text(), self.ok("GET", "/api/overview")["warnings"])
        self.collect()
        doc = self.ok("GET", "/api/overview")
        self.assertTrue(doc["live"]["fresh"])
        self.assertNotIn("starting", doc["live"])

    def test_overview_gen_follows_every_change(self):
        """C8: the fallback poll of a page without SSE re-reads its sections when ``gen`` changes — after a change
        of this panel and after one of the bot or the menu (state.json), never without one."""
        self.collect()                       # stage 1: the first round writes the states of health — a real change
        a = self.ok("GET", "/api/overview")["gen"]
        self.assertIsInstance(a, str)
        self.assertEqual(self.ok("GET", "/api/overview")["gen"], a)          # nothing changed
        self.collect()                                                        # live.json is not «the sections»
        self.assertEqual(self.ok("GET", "/api/overview")["gen"], a)
        self.ok("POST", "/api/gw/users", {"name": "Дача"})                    # a change of this panel
        b = self.ok("GET", "/api/overview")["gen"]
        self.assertNotEqual(b, a)
        st = state.load()                                                     # the bot or the menu
        st["gateway"]["users"][0]["name"] = "Бабушка"
        state.save(st)
        c = self.ok("GET", "/api/overview")["gen"]
        self.assertNotEqual(c, b)
        self.ok("PUT", "/api/appearance", {"theme": "dark"})
        self.assertNotEqual(self.ok("GET", "/api/overview")["gen"], c)

    def test_overview_carries_the_devices_for_the_poll(self):
        """C9: without SSE the page (and its scheme) learns who is online from GET overview."""
        did = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "Телефон", "kind": "phone",
                                                  "platform": "android", "access": ["wg"]})["device"]["id"]
        self.ads["wg"].sessions = [{"device": did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}]
        self.collect()
        doc = self.ok("GET", "/api/overview")
        self.assertEqual(doc["devices"][did], {"online": True, "inbound": "wg", "addr": "10.77.1.10",
                                               "since": 1790000000, "rx": 10, "tx": 20})
        self.assertEqual((doc["gateway"]["online"], doc["live"]["ts"]),
                         (1, json.loads(self.read("/run/kaskad/live.json"))["ts"]))

    def test_reads_run_no_command_and_are_cached(self):
        self.collect()
        calls = []
        real = api._overview_core

        def counting(fresh):
            calls.append(fresh)
            return real(fresh)
        before = len(self.w.runner.calls)
        with mock.patch.object(api, "_overview_core", counting):
            for path in ("/api/overview", "/api/overview", "/api/gw", "/api/gw/people", "/api/gw/inbounds",
                         "/api/gw/exits", "/api/forwards", "/api/appearance", "/api/panel", "/api/session",
                         "/api/overview", "/api/gw/offers?platform=ios", "/api/health", "/"):
                self.assertEqual(self.call("GET", path).status, 200, path)
            self.assertEqual(self.w.runner.calls[before:], [])               # reads run nothing
            self.assertEqual(calls, [True])                               # computed once, served from memory
            self.ok("POST", "/api/gw/users", {"name": "Дача"})             # a change: computed anew
            self.ok("GET", "/api/overview")
            self.assertEqual(calls, [True, True])

    def test_broken_state_is_a_500_with_the_core_text(self):
        self.write("/etc/kaskad/state.json", "{broken")
        snapshot.forget()
        doc = self.err(500, "GET", "/api/overview")
        self.assertIn("повреждён", doc["error"])

    def test_gw_view(self):
        self.collect()
        doc = self.ok("GET", "/api/gw")
        self.assertEqual((doc["enabled"], doc["net"], doc["host"]),
                         (True, {"base": "10.77.0.0/16", "dns": "10.77.0.1"}, "203.0.113.10"))
        self.assertEqual(doc["routes"]["default"]["via"], "pool")
        self.assertEqual(doc["services"]["dns"], True)
        rows = dict((r["kind"], r) for r in doc["inbounds"])
        self.assertTrue(rows["wg"]["installed"])
        self.assertEqual(rows["wg"]["record"]["port"], 51820)
        self.assertFalse(rows["awg1"]["installed"])
        self.assertEqual(len(doc["exits"]), 2)

    def test_inbounds_people_exits_offers(self):
        self.collect()
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual((rows["wg"]["running"], rows["wg"]["sessions"]), (True, 0))
        self.assertTrue(all("priv" not in k for r in rows.values() for k in (r["record"] or {})))
        doc = self.ok("GET", "/api/gw/people")
        self.assertEqual([u["name"] for u in doc["users"]], ["Мама"])
        self.assertIn("android", doc["platforms"])
        ex = self.ok("GET", "/api/gw/exits")["exits"]
        self.assertEqual(sorted(e["id"] for e in ex), sorted([self.vid, self.aid]))
        self.assertNotIn("11111111-2222", json.dumps(ex))                    # no key material
        aw = [e for e in ex if e["id"] == self.aid][0]
        self.assertEqual((aw["usable"], aw["text"]), (True, "работает"))
        offers = self.ok("GET", "/api/gw/offers?platform=ios")
        self.assertEqual(offers["platform"], "ios")
        self.err(400, "GET", "/api/gw/offers?platform=amiga")

    def test_exits_without_the_parser(self):
        os.unlink(paths.egress_parser())
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual(doc["exits"], [])
        self.assertIn("разборщика", doc["error"])


class ChangesTest(PanelCase):
    def dver(self):
        return self.ok("GET", "/api/gw/exits")["default_route_ver"]

    def rver(self, oid):
        """``route_ver`` of a person or a device (GET /api/gw/people, T7)."""
        p = self.ok("GET", "/api/gw/people")
        return [x for x in p["users"] + p["devices"] if x["id"] == oid][0]["route_ver"]

    def setUp(self):
        super().setUp()
        self.gateway()

    def gen(self):
        return snapshot.generation()

    def test_forwards(self):
        self.collect()
        (row,) = self.ok("GET", "/api/forwards")["forwards"]
        self.assertEqual((row["proto"], row["port"], row["to"]), ("tcp", 443, "203.0.113.5"))
        self.assertIsNotNone(row["packets"])
        g = self.gen()
        doc = self.ok("POST", "/api/forwards", {"proto": "udp", "port": 51999, "to": "198.51.100.7", "to_port": 51820,
                                                "name": "Амстердам"})
        self.assertEqual(doc["forward"]["name"], "Амстердам")
        self.assertGreater(self.gen(), g)
        self.assertEqual(self.w.nft.forwards()[("udp", 51999)], ("198.51.100.7", 51820))
        self.assertEqual([f["port"] for f in self.ok("GET", "/api/forwards")["forwards"]], [443, 51999])
        self.w.ss = SS_SSHD
        doc = self.err(409, "POST", "/api/forwards", {"proto": "tcp", "port": 22, "to": "198.51.100.7", "to_port": 22})
        self.assertEqual(doc["code"], errors.CONFLICT)
        self.assertEqual(doc["data"]["kind"], "ssh")
        self.assertEqual(self.err(400, "POST", "/api/forwards", {"proto": "tcp", "port": 8080, "to": "localhost",
                                                                 "to_port": 80})["code"], errors.USAGE)
        self.err(400, "POST", "/api/forwards", {"proto": "tcp"})
        self.ok("DELETE", "/api/forwards/udp/51999")
        self.assertEqual(self.err(400, "DELETE", "/api/forwards/udp/51999")["code"], errors.ERROR)

    def test_forwards_on_engine_legacy(self):
        st = state.load()
        st["engine"] = "legacy"
        state.save(st)
        doc = self.ok("GET", "/api/forwards")
        self.assertEqual((doc["engine"], doc["managed"]), ("legacy", False))
        doc = self.err(409, "POST", "/api/forwards", {"proto": "tcp", "port": 8443, "to": "198.51.100.7",
                                                      "to_port": 443})
        self.assertEqual(doc["code"], errors.UNSUPPORTED)

    def test_gateway_host_route_disable_enable(self):
        self.assertEqual(self.ok("PUT", "/api/gw/host", {"host": "vpn.example.org"})["host"], "vpn.example.org")
        self.err(400, "PUT", "/api/gw/host", {"host": "10.0.0.1"})
        self.err(400, "PUT", "/api/gw/host", {})
        ver = self.ok("GET", "/api/gw/exits")["default_route_ver"]                # T7: the route of the node has its ver
        self.assertEqual(self.ok("PUT", "/api/gw/route", {"via": "direct", "ver": ver})["route"],
                         {"via": "direct", "fail": "block"})
        self.assertEqual(self.err(400, "PUT", "/api/gw/route", {"via": "nowhere",
                                                                "ver": self.dver()})["code"], errors.USAGE)
        self.ok("POST", "/api/gw/disable")
        self.assertFalse(self.ok("GET", "/api/gw")["enabled"])
        doc = self.ok("POST", "/api/gw/enable", {})
        self.assertEqual(doc["base"], "10.77.0.0/16")
        self.assertTrue(self.ok("GET", "/api/gw")["enabled"])

    def test_inbounds(self):
        doc = self.ok("POST", "/api/gw/inbounds/awg2/install", {})
        self.assertEqual((doc["kind"], doc["record"]["port"]), ("awg2", 41023))
        self.assertEqual(self.err(400, "POST", "/api/gw/inbounds/nosuch/install", {})["code"], errors.USAGE)
        self.ok("POST", "/api/gw/inbounds/wg/disable")
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual((rows["wg"]["enabled"], rows["wg"]["running"]), (False, False))
        self.ok("POST", "/api/gw/inbounds/wg/enable")
        self.assertEqual(self.ok("PUT", "/api/gw/inbounds/wg/port", {"port": 51999})["port"], 51999)
        self.ok("POST", "/api/forwards", {"proto": "udp", "port": 51998, "to": "198.51.100.7", "to_port": 1})
        self.assertEqual(self.err(409, "PUT", "/api/gw/inbounds/wg/port", {"port": 51998})["code"], errors.CONFLICT)
        self.assertEqual(self.err(400, "PUT", "/api/gw/inbounds/wg/option", {"key": "plain", "value": True})["code"],
                         errors.USAGE)
        self.ok("POST", "/api/gw/inbounds/awg2/uninstall")
        self.err(400, "POST", "/api/gw/inbounds/awg2/uninstall")
        doc = self.ok("POST", "/api/gw/inbounds/awg2/install", {})          # F-2: the same port and keys come back
        self.assertEqual((doc["record"]["port"], doc["previous"]["restored"], doc["previous"]["changed"]),
                         (41023, True, []))
        self.ok("POST", "/api/gw/inbounds/awg2/uninstall")
        doc = self.ok("POST", "/api/gw/inbounds/awg2/install", {"port": 41077})
        self.assertEqual(doc["previous"]["changed"], ["порт 41023 → 41077"])

    def test_people_and_devices(self):
        u = self.ok("POST", "/api/gw/users", {"name": "Дача", "via": "direct"})["user"]
        self.assertEqual(u["route"], {"via": "direct", "fail": "block"})
        self.assertEqual(self.err(400, "POST", "/api/gw/users", {"name": "Дача"})["code"], errors.USAGE)
        self.err(400, "POST", "/api/gw/users", {})
        d = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "Телефон", "kind": "phone",
                                                "platform": "android", "access": ["wg"]})["device"]
        self.assertEqual(d["access"], ["wg"])
        self.collect()
        people = self.ok("GET", "/api/gw/people")
        dev = [x for x in people["devices"] if x["id"] == d["id"]][0]
        self.assertEqual((dev["user_name"], dev["route_effective"]["via"], dev["live"]["online"]),
                         ("Мама", "pool", False))
        did = d["id"]
        self.assertEqual(self.ok("PUT", "/api/gw/devices/%s/access" % did, {"access": []})["device"]["access"], [])
        self.assertEqual(self.ok("PUT", "/api/gw/devices/%s/access" % did, {"kind": "wg", "on": True})["device"]["access"],
                         ["wg"])
        self.err(400, "PUT", "/api/gw/devices/%s/access" % did, {})
        self.assertEqual(self.ok("PUT", "/api/gw/devices/%s/route" % did,
                                 {"via": "block", "ver": self.rver(did)})["device"]["route"]["via"], "block")
        self.assertIsNone(self.ok("PUT", "/api/gw/devices/%s/route" % did,
                                  {"via": "inherit", "ver": self.rver(did)})["device"]["route"])
        self.assertFalse(self.ok("PUT", "/api/gw/devices/%s/enabled" % did, {"enabled": False})["device"]["enabled"])
        self.err(400, "PUT", "/api/gw/devices/%s/enabled" % did, {"enabled": "no"})
        self.assertEqual(self.ok("POST", "/api/gw/devices/%s/reset" % did, {})["kinds"], ["wg"])
        self.assertEqual(self.ok("PUT", "/api/gw/users/%s/route" % self.uid,
                                 {"via": "pool", "ver": self.rver(self.uid)})["user"]["route"]["via"], "pool")
        self.assertFalse(self.ok("PUT", "/api/gw/users/%s/enabled" % self.uid, {"enabled": False})["user"]["enabled"])
        self.assertEqual(self.err(400, "DELETE", "/api/gw/users/%s" % self.uid)["code"], errors.USAGE)
        doc = self.ok("DELETE", "/api/gw/users/%s?force=1" % self.uid)
        self.assertEqual(doc["devices"], [did])
        # gone with its person: a DELETE of it is «уже удалено», not an error (T-M6-qs330b №6)
        self.assertIs(self.ok("DELETE", "/api/gw/devices/%s" % did)["gone"], True)
        self.assertEqual(self.call("DELETE", "/api/gw/users/%D0%9C%D0%B0%D0%BC%D0%B0").status, 404)   # ids only

    def test_generation_rises_after_changes_even_failed_ones(self):
        g = self.gen()
        self.ok("POST", "/api/gw/users", {"name": "Дача"})
        g2 = self.gen()
        self.assertGreater(g2, g)
        self.err(400, "POST", "/api/gw/users", {"name": "Дача"})
        self.assertGreater(self.gen(), g2)

    def test_exits(self):
        """T7 (§7.9, проверка 17, З-4): the name — ``PUT {name}`` without ``rev``; the switch — ``POST …/enabled`` with
        ``rev``; ``enabled`` in ``PUT`` — 400 ``rev_required`` (an old tab), with ``rev`` — 400 ``use_enabled``."""
        self.collect()                                                   # fresh data: the states are known
        doc = self.ok("PUT", "/api/gw/exits/%s" % self.aid, {"name": "Амстердам"})
        self.assertEqual(doc["pref"], {"name": "Амстердам"})
        rev = self.ok("GET", "/api/gw/exits")["rev"]
        self.assertEqual(self.ok("POST", "/api/gw/exits/%s/enabled" % self.aid, {"on": False, "rev": rev})["enabled"],
                         False)
        row = [e for e in self.ok("GET", "/api/gw/exits")["exits"] if e["id"] == self.aid][0]
        self.assertEqual((row["name"], row["enabled"], row["usable"]), ("Амстердам", False, False))
        self.err(400, "PUT", "/api/gw/exits/%s" % self.aid, {})
        self.assertEqual(self.err(400, "PUT", "/api/gw/exits/%s" % self.aid, {"enabled": "yes"})["code"],
                         "rev_required")
        self.assertEqual(self.err(400, "PUT", "/api/gw/exits/%s" % self.aid, {"enabled": True, "rev": rev})["code"],
                         "use_enabled")
        self.assertEqual(self.call("PUT", "/api/gw/exits/nope", {"name": "x"}).status, 404)

    def test_a_repeated_key_is_not_configured(self):
        """C17: the same key twice in the pool — its id carries the line (e-<10 hex>-<N>), the gateway never uses it:
        PUT answers why in JSON (not an HTML 404 that the page shows as «ошибка 404») and changes nothing."""
        write_pool([VLESS_A, awg_line(2), VLESS_A])
        snapshot.forget()
        dup = self.vid + "-3"
        row = [e for e in self.ok("GET", "/api/gw/exits")["exits"] if e["id"] == dup][0]
        self.assertEqual((row["kind"], row["usable"]), ("bad", False))
        self.assertIn("повтор ключа", row["problem"])
        self.assertEqual(row["name"], "Vienna")                 # named as the key above, not «e-…-3» (critic 3.2.21)
        before = self.read("/etc/kaskad/state.json")
        doc = self.err(409, "PUT", "/api/gw/exits/%s" % dup, {"name": "Копия"})
        self.assertEqual((doc["code"], doc["data"]), (errors.CONFLICT, {"exit": self.vid}))
        self.assertEqual(self.err(400, "PUT", "/api/gw/exits/%s" % dup, {"name": "Копия", "enabled": False,
                                                                         "rev": 1})["code"], "use_enabled")
        self.assertIn("повтор ключа", doc["error"])
        self.assertIn("исходный выход", doc["error"])
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.ok("PUT", "/api/gw/exits/%s" % self.vid, {"name": "Вена"})        # the original one: as before


class ExitKeysTest(PanelCase):
    """3.2.21: a key of an exit is added and removed in the panel the way the SSH menu does it — ``gokaskad --egress-add``
    (the key on stdin) and ``--egress-del <line> <hex>``; the key never comes back in an answer or the log. An empty pool
    says where to add a key (the menu number follows the default route) and that the devices are without internet."""

    def setUp(self):
        super().setUp()
        self.gateway()

    def log(self):
        try:
            return self.read("/var/log/kaskad.log")
        except OSError:
            return ""

    def script_calls(self, mode):
        return [(argv, inp) for argv, inp in self.w.runner.calls if argv[1:2] == [mode]]

    def test_add_a_key(self):
        g = snapshot.generation()
        doc = self.ok("POST", "/api/gw/exits", {"key": VLESS_B})
        eid = exits.exit_id(VLESS_B)
        self.assertEqual([a["id"] for a in doc["added"]], [eid])
        self.assertTrue(doc["added"][0]["name"])
        self.assertEqual(doc["warnings"], [])
        calls = self.script_calls("--egress-add")
        self.assertEqual(len(calls), 1)
        argv, inp = calls[0]
        self.assertEqual(argv, [exits._script(None), "--egress-add"])           # the key: stdin only, never argv
        self.assertEqual(inp.strip(), VLESS_B)
        self.assertIn(eid, [e["id"] for e in self.ok("GET", "/api/gw/exits")["exits"]])
        log = self.log()
        self.assertIn("GW exit add: %s" % eid, log)
        secret = VLESS_B.split("@")[0][len("vless://"):]
        for text in (json.dumps(doc), log):
            self.assertNotIn(secret, text)
        self.assertGreater(snapshot.generation(), g)                         # the tabs hear of it (SSE)

    def test_the_same_key_again_is_409(self):
        doc = self.err(409, "POST", "/api/gw/exits", {"key": VLESS_A + "\n"})
        self.assertEqual((doc["code"], doc["error"]), (errors.CONFLICT, "Такой ключ уже есть в пуле."))
        self.assertNotIn(VLESS_A.split("@")[0], json.dumps(doc))

    def test_bad_bodies_are_400_and_run_nothing(self):
        self.err(400, "POST", "/api/gw/exits", {})
        self.err(400, "POST", "/api/gw/exits", {"key": "  \n "})
        self.err(400, "POST", "/api/gw/exits", {"key": ["vless://x"]})
        self.err(400, "POST", "/api/gw/exits", {"key": "vless://" + "a" * (64 * 1024)})
        self.assertEqual(self.script_calls("--egress-add"), [])
        self.err(403, "POST", "/api/gw/exits", {"key": VLESS_B}, csrf=False)          # X-Kaskad-Admin
        self.err(403, "DELETE", "/api/gw/exits/%s" % self.vid, csrf=False)
        self.assertEqual(self.script_calls("--egress-add") + self.script_calls("--egress-del"), [])

    def test_a_failure_says_the_scripts_words_without_the_key(self):
        doc = self.err(400, "POST", "/api/gw/exits", {"key": "не ключ"})
        self.assertEqual(doc["error"], "Не удалось разобрать ключи.")
        # xray refused the config and quoted it: only the script's own Russian words reach the panel, redacted
        self.w.script.add_result = (1, "\x1b[0;31mКонфиг xray невалиден:\x1b[0m\nFailed to start: user id %s is bad\n"
                                       "\x1b[1;33mКлюч не добавлен — пул оставлен прежним.\x1b[0m\n" % VLESS_B, "")
        doc = self.err(400, "POST", "/api/gw/exits", {"key": VLESS_B})
        self.assertEqual(doc["error"], "Конфиг xray невалиден. Ключ не добавлен — пул оставлен прежним.")
        self.assertNotIn("66666666", json.dumps(doc) + self.log())
        self.w.script.add_result = (6, "[!] Пул ключей сейчас меняет другое окно (веб-панель или меню) — повторите.\n", "")
        self.assertEqual(self.err(409, "POST", "/api/gw/exits", {"key": VLESS_B})["code"], errors.LOCKED)

    def test_no_core_and_no_github_key_says_what_to_do(self):
        """Critic 3.2.21: the first AmneziaWG key without the core and without a GitHub key — the panel got the cause and
        lost the advice. Now: the cause, what to do (8 → 6), and data needs_key — the panel opens the GitHub key."""
        from kaskad.gw.inbounds import awg as awg_inbound
        R, N = "\x1b[0;31m", "\x1b[0m"
        self.w.script.add_result = (1, "".join(R + ln + N + "\n" for ln in (
            "Не удалось подготовить ядро AmneziaWG (amneziawg-go + awg) для amd64.",
            "Нужны bin/amneziawg-go-linux-amd64 и bin/awg-linux-amd64 из репозитория anten-ka/kaskad-pro.",
            "Репозиторий приватный: без ключа GitHub он отдаёт 404. Что сделать (любое из):",
            "  1) ключ GitHub — меню 8 → 6 (или переменная GH_TOKEN при запуске gokaskad);",
            "  2) клон kaskad-pro с bin/ в /opt/kaskad-pro;",
            "  3) положить файлы вручную: /etc/kaskad/egress/awg/amneziawg-go и /etc/kaskad/egress/awg/awg.",
            "В пуле только AWG, но ядро AmneziaWG не загрузилось.",
            "Ключ не добавлен — пул оставлен прежним.")), "")
        with mock.patch.object(awg_inbound, "needs_key", lambda: True):
            doc = self.err(400, "POST", "/api/gw/exits", {"key": awg_line(5)})
        e = doc["error"]
        for part in ("ключ GitHub — меню 8 → 6", "Что сделать (любое из):", "В пуле только AWG, но ядро AmneziaWG не загрузилось.",
                     "Ключ не добавлен — пул оставлен прежним."):
            self.assertIn(part, e)
        self.assertLess(e.index("Что сделать"), e.index("В пуле только AWG"))
        self.assertEqual(doc["data"], {"needs_key": True, "key": "none"})
        with mock.patch.object(awg_inbound, "needs_key", lambda: False):    # a key is there (GitHub refused): no window
            self.assertNotIn("needs_key", self.err(400, "POST", "/api/gw/exits", {"key": awg_line(5)}).get("data") or {})

    def test_the_line_is_gone_but_the_rebuild_failed(self):
        self.w.script.del_rebuild = (1, "\x1b[0;31mКонфиг xray невалиден:\x1b[0m\nfailed: bad\n")
        doc = self.err(400, "DELETE", "/api/gw/exits/%s?rev=%d" % (self.vid, self.rev()))
        self.assertEqual(doc["error"], "Ключ удалён из пула, но пересборка не прошла: Конфиг xray невалиден")
        self.assertEqual(exits.pool_lines(), [l for l in exits.pool_lines()])
        self.assertNotIn(self.vid, [i for i, _ in exits.line_ids()])
        self.w.script.del_rebuild, self.w.script.del_rc = None, 1                 # nothing removed: «не удалён»
        self.assertTrue(self.err(400, "DELETE", "/api/gw/exits/%s?rev=%d" % (self.aid, self.rev()))["error"]
                        .startswith("Ключ не удалён"))

    def test_long_runs_of_blanks_do_not_hold_the_panel(self):
        """Critic 3.2.21: the parser splits a .conf with lookaheads over blanks — 64 KB of spaces took minutes."""
        import time
        text = "[Interface]\nPrivateKey = a Endpoint = b" + " " * 60000 + "\n" + VLESS_B
        t0 = time.time()
        doc = self.ok("POST", "/api/gw/exits/preview", {"key": text})
        self.assertLess(time.time() - t0, 5)
        self.assertEqual([x["title"] for x in doc["items"]], ["VLESS"])
        self.ok("POST", "/api/gw/exits", {"key": VLESS_B + " " * 60000 + "\n"})
        (argv, inp), = [c for c in self.script_calls("--egress-add")]
        self.assertEqual(inp, VLESS_B + "\n")                                      # squeezed before the script

    def test_two_previews_at_once_leave_stderr_alone(self):
        import sys
        import threading
        from tests.test_gw_exits import conf_of
        was = sys.stderr
        errs = []

        def run():
            try:
                for _ in range(15):
                    self.ok("POST", "/api/gw/exits/preview", {"key": conf_of("2.0") + "\nvpn://AAAA"})
            except Exception as e:                       # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=run) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertIs(sys.stderr, was)

    def test_warnings_of_the_rebuild_reach_the_panel(self):
        self.w.script.add_result = (0, "\x1b[1;33m⚠ Ключ AmneziaWG 3.1 в зоне потерь #226\x1b[0m\n"
                                       "\x1b[1;33mAWG-ядро недоступно — поднимаю только VLESS-ключи (AWG пропущены).\x1b[0m\n"
                                       "\x1b[0;32mДобавлено ключей: 1.\x1b[0m\n", "")
        doc = self.ok("POST", "/api/gw/exits", {"key": VLESS_B})
        self.assertEqual(doc["warnings"], ["Ключ AmneziaWG 3.1 в зоне потерь #226",
                                           "AWG-ядро недоступно — поднимаю только VLESS-ключи (AWG пропущены)."])

    def rev(self):
        return self.ok("GET", "/api/gw/exits")["rev"]

    def test_delete_a_key_a_repeated_one_and_the_last(self):
        write_pool([VLESS_A, awg_line(2), VLESS_A])
        snapshot.forget()
        dup = self.vid + "-3"
        doc = self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (dup, self.rev()))
        self.assertEqual((doc["id"], doc["left"]), (dup, 2))
        self.assertEqual(self.script_calls("--egress-del")[-1][0][1:], ["--egress-del", "3", self.vid[2:]])
        old = self.rev()
        self.assertEqual(self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (self.vid, old))["left"], 1)
        self.assertEqual(exits.pool_lines(), [awg_line(2)])
        self.assertIn("GW exit del: %s" % self.vid, self.log())
        doc = self.err(404, "DELETE", "/api/gw/exits/%s?rev=%d" % (self.vid, old))    # gone already, the old rev
        self.assertIn("обновите страницу", doc["error"])
        self.assertEqual((doc["code"], doc["data"]["gone"], doc["data"]["exit"]), ("gone", True, self.vid))
        self.assertEqual(self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (self.aid, self.rev()))["left"], 0)
        self.assertEqual(exits.pool_lines(), [])
        r = self.call("DELETE", "/api/gw/exits/nope")
        self.assertEqual(r.status, 404)
        self.assertNotIn(b"gone", r.body)                  # a path that is not one carries no «gone» (§7.9)

    def test_delete_while_the_pool_changed_is_409(self):
        self.w.script.del_rc = 3
        doc = self.err(409, "DELETE", "/api/gw/exits/%s?rev=%d" % (self.vid, self.rev()))
        self.assertEqual((doc["code"], doc["error"]), (errors.CONFLICT, "Пул изменился — обновите страницу."))

    def test_empty_pool_hint_and_no_internet(self):
        write_pool([])
        snapshot.forget()
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual((doc["exits"], doc["add"]["menu"], doc["add"]["item"]), ([], "gokaskad → 17 → 3 → 3", 3))
        self.assertIn("«Выходы» → «Добавить ключ»", doc["add"]["text"])
        self.assertTrue(doc["offline"])
        self.assertEqual(doc["offline_text"], exits.NO_EXITS_OFFLINE)
        self.assertIn(exits.NO_EXITS_OFFLINE, self.ok("GET", "/api/overview")["warnings"])
        self.ok("PUT", "/api/gw/route", {"via": "pool", "fail": "direct", "ver": doc["default_route_ver"]})
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual((doc["offline"], doc["add"]["item"]), (False, 3))
        self.assertNotIn(exits.NO_EXITS_OFFLINE, self.ok("GET", "/api/overview")["warnings"])
        self.ok("PUT", "/api/gw/route", {"via": "direct", "ver": doc["default_route_ver"]})
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual((doc["offline"], doc["add"]["menu"]), (False, "gokaskad → 17 → 3 → 2"))
        self.ok("PUT", "/api/gw/route", {"via": "block", "ver": doc["default_route_ver"]})
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual(doc["add"]["item"], 2)
        self.ok("PUT", "/api/gw/route", {"via": "pool", "fail": "block", "ver": doc["default_route_ver"]})
        self.ok("POST", "/api/gw/disable")
        self.assertFalse(self.ok("GET", "/api/gw/exits")["offline"])            # the gateway off: nobody is cut off


class ExitPreviewTest(ExitKeysTest):
    """3.2.21-P2: a file of Amnezia (or a paste) — what it holds before anything is written, then a result per key."""

    def test_preview_writes_nothing_and_tells_no_secret(self):
        from tests.test_gw_exits import conf_of, vpn_of
        before = self.read("/etc/kaskad/egress/upstream.txt")
        g = snapshot.generation()
        text = "%s\n%s\n%s" % (VLESS_A, vpn_of(conf_of("2.0", 5), "Офис"), conf_of("wg", 6, "198.51.100.36:51820"))
        doc = self.ok("POST", "/api/gw/exits/preview", {"key": text})
        self.assertEqual([(x["title"], x["where"], x["in_pool"]) for x in doc["items"]],
                         [("WireGuard", "198.51.100.36:51820", False), ("AmneziaWG 2.0", "198.51.100.31:51820", False),
                          ("VLESS", "198.51.100.20:443", True)])
        self.assertFalse(doc["subscription"])
        body = json.dumps(doc, ensure_ascii=False)
        for secret in ("PrivateKey", "11111111-2222", "vpn://", "awg://", "vless://", "pbk="):
            self.assertNotIn(secret, body)
        self.assertEqual(self.read("/etc/kaskad/egress/upstream.txt"), before)          # nothing written
        self.assertEqual([c for c, _ in self.w.runner.calls if "--egress-add" in c], [])   # nothing ran
        self.assertEqual(snapshot.generation(), g)
        self.assertNotIn("Офис", self.log())
        sub = self.ok("POST", "/api/gw/exits/preview", {"key": "https://example.org/sub/x\n"})
        self.assertEqual((sub["items"], sub["subscription"], sub["text"]), ([], True, exits.SUBSCRIPTION_TEXT))
        self.assertEqual(self.ok("POST", "/api/gw/exits/preview", {"key": VLESS_A})["text"], "Все ключи уже есть в пуле.")

    def test_preview_refusals(self):
        self.err(403, "POST", "/api/gw/exits/preview", {"key": VLESS_B}, csrf=False)
        self.assertEqual(self.err(400, "POST", "/api/gw/exits/preview", {"key": "привет"})["error"], exits.NO_KEYS_TEXT)
        self.assertEqual(self.err(400, "POST", "/api/gw/exits/preview", {"key": "[Interface]\x00\x00"})["error"],
                         "Это не текстовый файл ключа.")
        self.err(400, "POST", "/api/gw/exits/preview", {"key": "v" * (64 * 1024 + 1)})
        path = self.path("/etc/kaskad/egress/upstream.txt")
        self.assertEqual(self.err(400, "POST", "/api/gw/exits/preview", {"key": path})["error"], "Это путь к файлу, а не ключ.")
        self.assertEqual(self.err(400, "POST", "/api/gw/exits", {"key": path})["error"], "Это путь к файлу, а не ключ.")
        self.assertEqual(self.err(400, "POST", "/api/gw/exits", {"key": "\x00" + VLESS_B})["error"], "Это не текстовый файл ключа.")
        self.assertEqual(self.script_calls("--egress-add"), [])

    def test_a_result_per_key(self):
        doc = self.ok("POST", "/api/gw/exits", {"key": "%s\n%s\n" % (VLESS_A, VLESS_B)})
        self.assertEqual([(r["title"], r["where"], r["result"]) for r in doc["results"]],
                         [("VLESS", "198.51.100.20:443", "in_pool"), ("VLESS", "198.51.100.21:8443", "added")])
        self.assertEqual([a["id"] for a in doc["added"]], [exits.exit_id(VLESS_B)])
        doc = self.err(409, "POST", "/api/gw/exits", {"key": "%s\n%s\n" % (VLESS_A, VLESS_B)})
        self.assertEqual(doc["error"], "Все эти ключи уже есть в пуле.")
        self.assertEqual([r["result"] for r in doc["data"]["results"]], ["in_pool", "in_pool"])
        self.assertNotIn("66666666", json.dumps(doc) + self.log())


class ScopedRunnerTest(PanelCase):
    def test_the_key_modes_run_outside_the_panels_limits_with_stdin(self):
        from kaskad.panel import server
        from kaskad.sysrun import FakeRunner
        inner = FakeRunner(binaries=["systemd-run"])
        inner.on(["systemd-run"], lambda argv, inp: (0, inp, ""))
        inner.on(["/opt/kaskad/bin/gokaskad"], "")
        sr = server.ScopedRunner(inner)
        for mode, extra in (("--egress-add", []), ("--egress-del", ["1", "0123456789"])):
            res = sr.run(["/opt/kaskad/bin/gokaskad", mode] + extra, input="ключ\n")
            argv, inp = inner.calls[-1]
            self.assertEqual(argv[:2], ["systemd-run", "--scope"])
            self.assertEqual(argv[-2 - len(extra):], ["/opt/kaskad/bin/gokaskad", mode] + extra)
            self.assertEqual((inp, res.out), ("ключ\n", "ключ\n"))
        sr.run(["/opt/kaskad/bin/gokaskad", "--egress-awg-heal"])                   # the legs: as before, in place
        self.assertEqual(inner.calls[-1][0], ["/opt/kaskad/bin/gokaskad", "--egress-awg-heal"])


class IssueTest(PanelCase):
    def test_secrets_only_in_the_issue(self):
        self.gateway()
        d = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "Ноутбук", "kind": "laptop",
                                                "platform": "linux", "access": ["wg"]})["device"]
        r = self.call("POST", "/api/gw/devices/%s/issue" % d["id"], {})
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.headers["cache-control"], "no-store, max-age=0")
        doc = r.json()
        self.assertEqual((doc["kind"], doc["os"]), ("wg", "linux"))
        secret = "DEVKEY-%s" % d["id"]
        self.assertIn(secret, doc["items"][0]["body"])
        self.collect()
        for path in ("/api/gw/people", "/api/overview", "/api/gw", "/api/gw/inbounds"):
            self.assertNotIn(secret, self.call("GET", path).text, path)
        self.assertNotIn(secret, self.read("/run/kaskad/live.json"))
        self.assertNotIn(secret, self.read("/var/log/kaskad.log"))
        self.err(403, "POST", "/api/gw/devices/%s/issue" % d["id"], {}, csrf=False)
        self.assertEqual(self.call("GET", "/api/gw/devices/%s/issue" % d["id"]).status, 405)
        self.ok("PUT", "/api/gw/devices/%s/access" % d["id"], {"access": []})
        self.assertIn("нет рабочих протоколов", self.err(400, "POST", "/api/gw/devices/%s/issue" % d["id"], {})["error"])

    def test_the_steps_speak_the_panel(self):
        """T-G1b N4: «Как подключить» of the panel came with the words of the SSH menu (PuTTY, «Сохранить в файлы»,
        gokaskad → 17 → …). The panel asks the core for its own: «Скопировать», «Скачать»."""
        from kaskad.gw import issue_os
        self.gateway()
        d = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "Телефон", "kind": "phone",
                                                "platform": "android", "access": ["wg"]})["device"]
        doc = self.ok("POST", "/api/gw/devices/%s/issue" % d["id"], {})
        self.assertEqual(doc["howto"], issue_os.howto("android", "wg", where="panel"))
        self.assertEqual(doc["howto"][2], "Наведите камеру телефона на QR-код ниже.")
        doc = self.ok("POST", "/api/gw/devices/%s/issue" % d["id"], {"os": "windows"})
        self.assertEqual(doc["howto"][1], "Скопируйте текст файла .conf кнопкой «Скопировать» ниже.")
        for words in (doc["howto"], issue_os.howto("windows", "ikev2", where="panel"),
                      issue_os.howto("android", "awg3", where="panel")):
            text = " ".join(words)
            for menu_word in ("PuTTY", "Сохранить в файлы", "этого экрана", "Termius", "gokaskad"):
                self.assertNotIn(menu_word, text)
        # the menu (kaskadctl gw issue) keeps its words
        self.assertIn("Сохранить в файлы", issue_os.howto("windows", "ikev2")[0])


class AppearanceTest(PanelCase):
    def test_white_list_and_merge(self):
        doc = self.ok("GET", "/api/appearance")
        self.assertEqual((doc["saved"], doc["appearance"]), (False, api.APPEARANCE_DEFAULTS))
        self.assertIn("kaskad", doc["fields"]["enums"]["accent"])
        ap = self.ok("PUT", "/api/appearance", {"theme": "dark", "radius": 12})["appearance"]
        self.assertEqual((ap["theme"], ap["radius"], ap["accent"]), ("dark", 12, "auto"))   # the default since 3.3.0
        ap = self.ok("PUT", "/api/appearance", {"appearance": {"accent": "kaskad"}})["appearance"]
        self.assertEqual((ap["theme"], ap["accent"]), ("dark", "kaskad"))                  # merged with the saved
        doc = self.ok("GET", "/api/appearance")
        self.assertEqual((doc["saved"], doc["appearance"]["theme"]), (True, "dark"))
        self.assertEqual(stat.S_IMODE(os.stat(conf.appearance_path()).st_mode), 0o600)
        for bad, field in (({"theme": "neon"}, "theme"), ({"radius": 99}, "radius"), ({"vibrant": "yes"}, "vibrant"),
                           ({"x": 1}, "x"), ({"radius": True}, "radius")):
            self.assertEqual(self.err(400, "PUT", "/api/appearance", bad)["data"], {"field": field})
        self.assertEqual(self.ok("GET", "/api/appearance")["appearance"]["accent"], "kaskad")


class PanelSettingsTest(PanelCase):
    def test_panel_info_password_link(self):
        doc = self.ok("GET", "/api/panel")
        self.assertEqual(doc["listen"], [{"kind": "local", "addr": "127.0.0.1:%d" % self.port, "scheme": "http"}])
        self.assertEqual((doc["password"], doc["you"]["via"], doc["public"]["on"]), (False, "tunnel", False))
        self.assertTrue(doc["tunnel"].startswith("ssh -L 1980:127.0.0.1:1980 root@"))
        self.assertEqual(self.err(400, "PUT", "/api/panel/password", {"password": "short"})["code"], errors.USAGE)
        self.ok("PUT", "/api/panel/password", {"password": "новый-пароль-123"})         # the tunnel: no current one
        self.assertTrue(auth.verify("admin", "новый-пароль-123"))
        self.login("новый-пароль-123")
        url = self.ok("POST", "/api/panel/link", {"ttl": 600})["url"]
        self.assertIn("/login?t=", url)
        self.err(400, "POST", "/api/panel/link", {"ttl": "long"})

    def test_open_mode_from_the_panel(self):
        save_state()
        st = state.load()
        st["gateway"] = {"host": "203.0.113.10"}
        state.save(st)
        self.node_cert()
        doc = self.err(400, "PUT", "/api/panel/public", {"on": True})        # no password: would lock out
        self.assertIn("пароль", doc["error"])
        auth.set_password("правильный-пароль")
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            doc = self.ok("PUT", "/api/panel/public", {"on": True})
            self.assertTrue(conf.PUBLIC_PORTS[0] <= doc["public"]["port"] <= conf.PUBLIC_PORTS[1])
            self.assertEqual(doc["fingerprint"], pki.sha256_fingerprint(fixture("node.crt")))
            self.assertEqual(doc["public"]["url"], "https://203.0.113.10:%d%s/" % (doc["public"]["port"], conf.proxy_base(conf.load())))
            access = json.loads(self.read("/etc/kaskad/panel/access.json"))
            self.assertEqual(access, {"public": True, "port": doc["public"]["port"], "scheme": "https",
                                      "host": "203.0.113.10"})
            # 203.0.113.10 is not an address of the machine running the tests: the listener says why
            self.assertTrue(self.wait(lambda: "public" in self.panel.errors))
            self.err(401, "PUT", "/api/panel/public", {"on": False})       # open mode: the tunnel logs in too
            tok = self.login("правильный-пароль", tunnel=True)
            self.ok("PUT", "/api/panel/public", {"on": False}, cookie=tok)
            self.assertTrue(self.wait(lambda: "public" not in self.panel.errors))
        self.assertFalse(json.loads(self.read("/etc/kaskad/panel/access.json"))["public"])
        self.assertIn("открытый режим включён", self.read("/var/log/kaskad.log"))
        self.err(400, "PUT", "/api/panel/public", {})


class MtuTest(PanelCase):
    """§6.1: GET gw carries the safe-MTU view (card, budget, server/client per inbound, narrow); POST gw/mtu — a narrow
    card: new settings with a smaller MTU (the devices to give them to) or as it is."""

    def setUp(self):
        super().setUp()
        self.gateway(inbounds=("wg", "awg2"))
        from kaskad.gw import ops
        d = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="android", access=["awg2"])
        self.did = d["device"]["id"]
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": "60", "MTU": "1280"}
            st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1400}}
        self.write("/proc/net/route", "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                                      "eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\n")
        self.write("/sys/class/net/eth0/mtu", "1400\n")
        snapshot.bump()

    def test_view_reissue_keep(self):
        m = self.ok("GET", "/api/gw")["mtu"]
        self.assertEqual((m["nic"], m["budget"], m["ask"]), (1400, 1400, ["awg2"]))
        row = [r for r in m["inbounds"] if r["kind"] == "awg2"][0]
        self.assertEqual((row["server"], row["client"], row["new_client"]), (1248, 1280, 1248))
        self.assertIn("карта сервера 1400 уже", row["text"])
        # T-N5 3.2.19-r2, Н-5: the panel's card has its own headline «AmneziaWG 2.0: сетевая карта сервера уже, чем нужно
        # клиентам.» — its text says neither the title nor the headline again, and «1420 байт» is explained
        self.assertEqual(row["detail"], "Карта 1400, а пакету клиента с MTU 1280 при S4 = 60 нужно 1420 байт (MTU клиента "
                                        "+ 80 + S4). Выдайте новые настройки с MTU 1248 или оставьте как есть.")
        self.err(400, "POST", "/api/gw/mtu", {"kind": "awg2", "action": "drop"})
        self.assertEqual(self.err(400, "POST", "/api/gw/mtu", {"kind": "l2tp", "action": "keep"})["code"], errors.USAGE)
        doc = self.ok("POST", "/api/gw/mtu", {"kind": "awg2", "action": "reissue"})
        self.assertEqual((doc["changed"], doc["was"], doc["client"]), (True, 1280, 1248))
        self.assertEqual([d["id"] for d in doc["devices"]], [self.did])
        self.assertIn("выдайте их заново", doc["text"])
        self.assertEqual(self.ok("GET", "/api/gw")["mtu"]["ask"], [])
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"]["MTU"] = "1280"
        snapshot.bump()
        doc = self.ok("POST", "/api/gw/mtu", {"kind": "awg2", "action": "keep"})
        self.assertEqual((doc["kept"], doc["nic"]), (True, 1400))
        self.assertEqual(self.ok("GET", "/api/gw")["mtu"]["ask"], [])
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"]["MTU"], "1280")

    def test_s4_below_1232(self):
        from kaskad.vendor import awgkeys
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"] = dict(awgkeys.gen_params("2.0"), S4="120")
            st["gateway"]["mtu"]["nic"]["mtu"] = 1500
        self.write("/sys/class/net/eth0/mtu", "1500\n")
        snapshot.bump()
        m = self.ok("GET", "/api/gw")["mtu"]
        row = [r for r in m["inbounds"] if r["kind"] == "awg2"][0]
        self.assertEqual((m["ask"], row["offer"], row["server"], row["new_s4_max"]), (["awg2"], "s4", 1200, 40))
        self.assertEqual(row["devices"], ["Телефон (Мама)"])                 # F-2 before the choice
        self.assertIn("HTTP/3 через этот протокол перейдёт на TCP", row["text"])
        doc = self.ok("POST", "/api/gw/mtu", {"kind": "awg2", "action": "s4"})
        self.assertTrue(doc["changed"])
        self.assertTrue(15 <= doc["s4"] <= 40)
        self.assertEqual([d["id"] for d in doc["devices"]], [self.did])
        m = self.ok("GET", "/api/gw")["mtu"]
        row = [r for r in m["inbounds"] if r["kind"] == "awg2"][0]
        self.assertEqual((m["ask"], row["server"], row["waiting"]), ([], 1280, ["Телефон (Мама)"]))
