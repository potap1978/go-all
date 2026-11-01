"""kaskad.panel.scheme (WP-P3): the graph of the scheme from fixture states (inherited routes, dead and removed
exits), the layout with its version (409 between two tabs), «Применить» — dry run leaves every file byte for
byte, invalid intents change nothing, a valid list is one change of the core, the HTTP handlers."""

import json
import os
import time
from unittest import mock

from kaskad import errors, paths, secrets, snapshot, state
from kaskad.gw import exits, model, ops
from kaskad.panel import routes, scheme
from kaskad.panel.routes import HttpError, Request
from tests.gwcase import GwCase


def _ids(items):
    return set(x["id"] for x in items)


class SchemeCase(GwCase):
    def setUp(self):
        super().setUp()
        snapshot.forget()
        scheme._pool_cache.clear()
        scheme._graph_cache.clear()

    def tearDown(self):
        snapshot.forget()
        scheme._pool_cache.clear()
        scheme._graph_cache.clear()
        super().tearDown()

    def legacy(self):
        """The node is an UPDATED one (gen 1): the default of the rules is ru_direct as in 3.3.x — a new node has
        all_exit (SPEC-stage1-rf-mark-builtin §3.6, D-019)."""
        st = state.load()
        st["gen"] = 1
        state.save(st)

    def graph(self):
        return scheme.build(state.load(), scheme.pool_exits(), scheme.exits_snapshot(), scheme.load_layout(), False)

    def node(self, g, nid):
        return next(n for n in g["nodes"] if n["id"] == nid)

    def edge(self, g, eid):
        return next(e for e in g["edges"] if e["id"] == eid)

    def files(self):
        out = {}
        for p in (paths.state(), paths.secrets_file(), scheme.layout_path(), exits.snapshot_path()):
            if os.path.exists(p):
                with open(p, "rb") as f:
                    out[p] = f.read()
        return out

    def dead(self, *ids, **alive):
        """The watchdog's snapshot: ``ids`` dead (2 failed probes), the rest of the pool alive."""
        snap = exits.load_snapshot()
        for eid in list(snap["exits"]):
            dead = eid in ids
            snap["exits"][eid] = {"alive": not dead, "fails": 2 if dead else 0, "since": self.now,
                                  "detail": "xray: недоступен" if dead else "работает"}
        exits.save_snapshot(snap)


class GraphTest(SchemeCase):
    @mock.patch.object(scheme, "_model_fn", lambda *names: None)      # the model of 3.1: no rules profiles
    def test_nodes_and_edges_of_the_scenario(self):
        self.scenario()
        g = self.graph()
        vid, aid = self.vid, self.aid
        self.assertEqual(_ids(g["nodes"]), {"u-00000001", "u-00000002", "in:wg", "in:awg2", "filter", "rules:all_exit",
                                            "pool", "exit:" + vid, "exit:" + aid, "direct", "block"})
        mama = self.node(g, "u-00000001")
        self.assertEqual([d["id"] for d in mama["data"]["devices"]], ["d-00000001", "d-00000003"])
        self.assertIsNone(mama["position"])
        phone = mama["data"]["devices"][0]
        self.assertEqual((phone["access"], phone["ips"], phone["missing"]), (["wg"], {"wg": "10.77.1.10"}, []))
        self.assertEqual(phone["effective"], {"via": "pool", "fail": "block", "from": "default"})
        self.assertEqual((phone["profile"], phone["profile_from"]), ("all_exit", "default"))
        # the pool: an AmneziaWG exit by the hash of the device, or the balancer of the VLESS exits (not one of them)
        self.assertIn((phone["now"]["target"], phone["now"]["balancer"]), (("exit:" + aid, False), ("pool", "vless")))
        self.assertIn("wg", phone["suits"])                                  # android: WireGuard is offered
        # access: every device -> every installed inbound of its access
        acc = sorted(e["id"] for e in g["edges"] if e["type"] == "access")
        self.assertEqual(acc, ["access:d-00000001:wg", "access:d-00000002:awg2", "access:d-00000002:wg",
                               "access:d-00000003:wg"])
        a = self.edge(g, "access:d-00000002:awg2")
        self.assertEqual((a["source"], a["sourceHandle"], a["target"], a["targetHandle"]),
                         ("u-00000002", "d-00000002", "in:awg2", "in"))
        self.assertEqual(a["data"]["ip"], "10.77.3.10")
        # routes: through the rules node; inherited from the user / the default are flagged
        r1, r2, r3 = (self.edge(g, "route:d-0000000%d" % i) for i in (1, 2, 3))
        self.assertEqual((r1["target"], r1["data"]["from"], r1["data"]["inherited"], r1["data"]["rules"]),
                         ("pool", "default", True, "rules:all_exit"))
        self.assertEqual((r2["target"], r2["data"]["from"], r2["data"]["inherited"]), ("exit:" + aid, "user", True))
        self.assertEqual((r3["target"], r3["data"]["from"], r3["data"]["inherited"], r3["data"]["fail"]),
                         ("exit:" + vid, "device", False, "direct"))
        ur = self.edge(g, "uroute:u-00000002")
        self.assertEqual((ur["sourceHandle"], ur["target"]), ("user", "exit:" + aid))
        self.assertNotIn("uroute:u-00000001", _ids(g["edges"]))
        # the pool and its exits
        pool = self.node(g, "pool")["data"]
        self.assertEqual((pool["exits"], pool["usable"], pool["default"]), ([vid, aid], 2, True))
        vienna = self.node(g, "exit:" + vid)["data"]
        self.assertEqual((vienna["name"], vienna["kind"], vienna["state"], vienna["devices"]), ("Вена", "xray", "alive", 1))
        self.assertNotIn("line", vienna)
        self.assertNotIn("outbound", vienna)
        self.assertEqual(self.node(g, "exit:" + aid)["data"]["name"], "Amsterdam")
        self.assertEqual(self.edge(g, "member:" + vid)["data"]["state"], "alive")
        self.assertEqual(self.node(g, "in:wg")["data"]["devices"], 3)
        self.assertEqual(self.node(g, "in:wg")["data"]["ports"], ["51820/udp"])
        self.assertEqual(self.node(g, "rules:all_exit")["data"]["devices"], 3)
        self.assertEqual(g["default_route"], {"via": "pool", "fail": "block"})
        self.assertFalse(g["profiles"]["supported"])
        self.assertIn("не знает профилей", g["profiles"]["soon"])
        self.assertNotIn("3.2", g["profiles"]["soon"])                       # 3.2 is here: no promise of a version
        self.assertEqual(g["kinds"]["awg3"], "AmneziaWG 3.1")                # no adapter: the title of the matrix
        # nothing secret leaves: no key of the pool, no password
        text = json.dumps(g, ensure_ascii=False)
        self.assertNotIn("vless://", text)
        self.assertNotIn("awg://", text)
        self.assertNotIn("11111111-2222", text)

    def test_dead_exits_and_the_fail_policy(self):
        self.scenario()
        self.dead(self.vid)
        g = self.graph()
        self.assertEqual(self.edge(g, "member:" + self.vid)["data"]["state"], "dead")
        self.assertFalse(self.edge(g, "member:" + self.vid)["data"]["usable"])
        devs = dict((d["id"], d) for n in g["nodes"] if n["type"] == "user" for d in n["data"]["devices"])
        # its exit is dead: stage 1 (SE §2.2, difference 4) — another fit exit of its group, not «fail = direct»
        self.assertEqual(devs["d-00000003"]["now"]["target"], "exit:" + self.aid)
        self.assertIn("не годен", devs["d-00000003"]["now"]["why"])
        self.assertEqual(devs["d-00000001"]["now"]["target"], "exit:" + self.aid)  # the pool: the live one
        self.assertEqual(self.node(g, "pool")["data"]["usable"], 1)
        self.dead(self.vid, self.aid)
        g = self.graph()
        devs = dict((d["id"], d) for n in g["nodes"] if n["type"] == "user" for d in n["data"]["devices"])
        self.assertEqual(devs["d-00000001"]["now"]["target"], "block")          # pool without live exits: block
        self.assertEqual(devs["d-00000002"]["now"]["target"], "block")
        self.assertIn("не работает", devs["d-00000002"]["now"]["why"])

    def test_an_exit_without_a_name_is_called_by_its_kind_and_address(self):
        """T-G1b N1: an AmneziaWG key without a name of its own showed its id (e-9ea6ad7d94) on the node and in «Пул»;
        «Выходы», «Обзор» and the menu call it «AmneziaWG 2.0 · 198.51.100.31» — so does the scheme now."""
        from tests.fakes_gw import VLESS_A, awg_line, write_pool
        st = self.scenario()
        awg = awg_line(2, name="amneziawg")                                 # the parser's placeholder: no name
        write_pool([VLESS_A, awg])
        aid = exits.exit_id(awg)
        st["gateway"]["users"][1]["route"] = {"via": "exit:" + aid, "fail": "block"}
        state.save(st)
        scheme._pool_cache.clear()
        g = self.graph()
        node = self.node(g, "exit:" + aid)["data"]
        where = [r for r in exits.pool(None) if r["id"] == aid][0]["where"]
        self.assertTrue(where.startswith("AmneziaWG "), where)
        self.assertIn("198.51.100.31", where)
        self.assertEqual((node["name"], node["where"], node["named"]), (where, where, False))
        self.assertNotIn(aid, node["name"])
        dacha = self.node(g, "u-00000002")["data"]["devices"][0]
        self.assertIn("«%s»" % where, dacha["now"]["why"])                   # the reason names it the same way
        self.assertEqual(self.node(g, "exit:" + self.vid)["data"]["name"], "Вена")   # a given name still wins

    def test_a_route_to_an_exit_gone_from_the_pool(self):
        st = self.scenario()
        st["gateway"]["users"][0]["route"] = {"via": "exit:e-0123456789", "fail": "block"}
        st["gateway"]["devices"][0]["access"].append("awg3")                 # an inbound that is not installed
        state.save(st)
        g = self.graph()
        ghost = self.node(g, "exit:e-0123456789")
        self.assertTrue(ghost["data"]["missing"])
        self.assertEqual(ghost["data"]["state"], "missing")
        self.assertEqual(self.edge(g, "uroute:u-00000001")["target"], "exit:e-0123456789")
        self.assertEqual(self.edge(g, "route:d-00000001")["target"], "exit:e-0123456789")
        phone = self.node(g, "u-00000001")["data"]["devices"][0]
        # stage 1 (SE §2.2, З1): the exit gone — through «Основная» (the group where it is no more), not the block
        self.assertTrue(phone["now"]["target"].startswith("exit:"), phone["now"])
        self.assertEqual(phone["missing"], ["awg3"])
        self.assertNotIn("access:d-00000001:awg3", _ids(g["edges"]))

    def test_disabled_device_and_version(self):
        st = self.scenario()
        v1 = scheme.graph_version(st)
        self.assertEqual(v1, scheme.graph_version(state.load()))
        st["gateway"]["devices"][1]["enabled"] = False
        state.save(st)
        g = self.graph()
        self.assertNotEqual(g["version"], v1)
        dacha = self.node(g, "u-00000002")["data"]["devices"][0]
        self.assertEqual((dacha["active"], dacha["now"]["target"]), (False, None))
        self.assertFalse(self.edge(g, "route:d-00000002")["data"]["active"])

    @mock.patch.object(scheme, "_model_fn", lambda *names: None)      # the model of 3.1: no rules profiles
    def test_empty_gateway(self):
        g = scheme.build(state.empty(), [], {"exits": {}}, scheme.load_layout(), False)
        self.assertEqual(_ids(g["nodes"]), {"pool", "direct", "block", "filter"})
        self.assertEqual(g["edges"], [])

    def test_profiles_of_the_model_when_wp_r2_adds_them(self):
        self.scenario()
        self.legacy()

        def set_rules(obj, profile):
            obj["rules"] = None if profile in (None, "", "inherit") else profile
            return obj["rules"]
        with mock.patch.object(model, "set_rules", set_rules, create=True), \
                mock.patch.object(model, "RULES_PROFILES", ("inherit", "all_exit", "ru_direct"), create=True):
            st = state.load()
            st["gateway"]["users"][0]["rules"] = "ru_direct"
            state.save(st)
            g = self.graph()
            self.assertTrue(g["profiles"]["supported"])
            ru = self.node(g, "rules:ru_direct")["data"]
            # 3: the node default of WP-R is ru_direct, so the device of user 2 (inherits it) counts too
            self.assertEqual((ru["devices"], ru["in_use"], ru["lists"]), (3, True, False))
            self.assertEqual(self.edge(g, "route:d-00000001")["data"]["rules"], "rules:ru_direct")
            self.assertEqual(self.edge(g, "cat:ru_direct:ru")["target"], "direct")
            filt = self.node(g, "filter")["data"]                              # the filter of the node: for all
            self.assertEqual((filt["ads"], filt["torrent"]), (True, False))
            self.assertFalse([e for e in g["edges"] if e["id"].startswith("filter:")])   # no line to «Блок» (М2)
            phone = self.node(g, "u-00000001")["data"]["devices"][0]
            self.assertEqual((phone["profile"], phone["profile_from"]), ("ru_direct", "user"))
            # a new ru_direct needs the tick ⚠РФ (rf §3.5, R3): without it the scheme is refused, the state stays
            before = self.read("/etc/kaskad/state.json")
            with self.assertRaises(HttpError) as cm:
                scheme.apply({"intents": [{"type": "rules", "device": "d-00000002", "profile": "ru_direct"}]},
                             runner=self.w.runner)
            self.assertEqual(cm.exception.status, 409)
            self.assertEqual(self.read("/etc/kaskad/state.json"), before)
            with state.transaction() as st:                                  # the consent given (R4: the tick)
                st["gateway"]["rf_consent"] = {"at": "2026-10-16T00:00:00Z", "via": "panel", "orgs": ["meta"]}
            res = scheme.apply({"intents": [{"type": "rules", "device": "d-00000002", "profile": "ru_direct"}]},
                               runner=self.w.runner)
            self.assertTrue(res["applied"])
            self.assertEqual(model.get_device(state.load(), "d-00000002")["rules"], "ru_direct")


class RulesListsTest(SchemeCase):
    def test_lists_ready_is_what_get_api_rules_says(self):
        """C3: a failed download leaves status.json and update.lock in /var/lib/kaskad/rules — that is not «the lists
        are there»; the scheme says what GET /api/rules says (ready: the networks of Russia stored)."""
        from kaskad.rules import ops as rules_ops
        from kaskad.rules import store
        self.scenario()
        self.assertFalse(scheme.rules_lists_ready())                       # nothing at all
        with store.update_lock():
            pass
        store.save_status({"at": "2026-10-07T00:00:00Z", "ok": False, "changed": False, "error": "geoip: нет сети"})
        self.assertTrue(os.path.isfile(os.path.join(paths.rules_dir(), "status.json")))
        self.assertFalse(rules_ops.status(state.load())["ready"])
        self.assertFalse(scheme.rules_lists_ready())
        g = scheme.current_graph()
        self.assertFalse(g["profiles"]["lists"])
        if g["profiles"]["supported"]:
            self.assertFalse(self.node(g, "rules:ru_direct")["data"]["lists"])
        store.commit({"ru4.txt": b"5.8.0.0/24\n"}, {"kernel": {"ranges": 1}})   # the lists arrive
        self.assertTrue(rules_ops.status(state.load())["ready"])
        self.assertTrue(scheme.rules_lists_ready())
        g = scheme.current_graph()
        self.assertTrue(g["profiles"]["lists"])
        if g["profiles"]["supported"]:
            self.assertTrue(self.node(g, "rules:ru_direct")["data"]["lists"])


class DefaultProfileTest(SchemeCase):
    def test_a_user_without_a_profile_of_his_own_follows_the_node(self):
        """Б3 (ревизия схемы): a user without his own profile is on the profile of the node — «Россия напрямую» out of
        the box —, not «Всё через выход»: his node says so, his own route goes through that rules node, the rules
        node of the default is marked. «Out of the box» — of an updated node; a new one has all_exit (rf §3.6)."""
        self.scenario()
        self.legacy()
        g = self.graph()
        self.assertEqual(g["profiles"]["default"], "ru_direct")
        dacha = self.node(g, "u-00000002")["data"]
        self.assertEqual((dacha["rules"], dacha["profile"], dacha["profile_from"]), (None, "ru_direct", "default"))
        self.assertEqual(self.edge(g, "uroute:u-00000002")["data"]["rules"], "rules:ru_direct")
        self.assertTrue(self.node(g, "rules:ru_direct")["data"]["default"])
        self.assertFalse(self.node(g, "rules:all_exit")["data"]["default"])
        st = state.load()
        model.set_rules_conf(st, default="all_exit")                     # the node's profile changes
        st["gateway"]["users"][0]["rules"] = "ru_direct"                  # Мама has her own
        state.save(st)
        g = self.graph()
        self.assertEqual(g["profiles"]["default"], "all_exit")
        self.assertEqual(self.edge(g, "uroute:u-00000002")["data"]["rules"], "rules:all_exit")
        mama = self.node(g, "u-00000001")["data"]
        self.assertEqual((mama["rules"], mama["profile"], mama["profile_from"]), ("ru_direct", "ru_direct", "user"))
        phone = mama["devices"][0]
        self.assertEqual((phone["profile"], phone["profile_from"]), ("ru_direct", "user"))
        self.assertTrue(self.node(g, "rules:all_exit")["data"]["default"])

    def test_texts_of_a_removed_own_profile_name_what_follows(self):
        self.scenario(enabled=False)
        self.legacy()
        st = state.load()
        st["gateway"]["devices"][0]["rules"] = "all_exit"
        st["gateway"]["users"][1]["rules"] = "all_exit"
        st["gateway"]["devices"][1]["rules"] = "ru_direct"
        state.save(st)
        res = scheme.apply({"dry_run": True, "intents": [
            {"type": "rules", "device": "d-00000001", "profile": "inherit"},
            {"type": "rules", "device": "d-00000002", "profile": "inherit"},
            {"type": "rules", "user": "u-00000002", "profile": "inherit"}]})
        texts = [r["text"] for r in res["results"]]
        self.assertIn("по умолчанию узла: «Россия напрямую»", texts[0])
        self.assertIn("как у пользователя: «Всё через выход»", texts[1])  # the user's own was still there
        self.assertIn("по умолчанию узла: «Россия напрямую»", texts[2])
        changes = [c["text"] for c in res["changes"] if c["what"] == "rules"]
        self.assertTrue(all("наследуются" not in c for c in changes), changes)


def _lists():
    from kaskad.rules import store
    store.commit({"ru4.txt": b"5.8.0.0/24\n"}, {"kernel": {"ranges": 1}})


class RuStateTest(SchemeCase):
    def test_russia_directly_is_told_by_the_table_of_the_kernel(self):
        """С2: «Россия напрямую» works when the networks of Russia are in the table, not when the lists are stored:
        no lists — no_lists; stored but never loaded (after a reboot) — unknown; the last load failed — not_loaded
        with its error and the next try; loaded (or the previous lists while new ones load) — ok. dnsmasq without
        nftset: the domains do not count. The graph follows each change of these files (its cache)."""
        from kaskad.gw import dns, net
        from kaskad.rules import apply as rules_apply
        from tests.rules_helpers import consented
        self.scenario()
        state.save(consented(state.load()))   # stage 1: without the consent ⚠РФ — «no_consent» (tests/test_scheme_rf.py)
        self.assertEqual(scheme.ru_state()["state"], "no_lists")
        g = scheme.current_graph()
        self.assertEqual(self.node(g, "rules:ru_direct")["data"]["ru"]["state"], "no_lists")
        _lists()
        self.assertEqual(scheme.ru_state()["state"], "unknown")
        wanted = net.ru_wanted()
        self.assertTrue(wanted)
        rules_apply._save_load_state({"ver": wanted, "ok": False, "error": "nft: нет памяти", "next": 1800000000.0,
                                      "fails": 1})
        ru = scheme.ru_state()
        self.assertEqual((ru["state"], ru["error"], ru["next"]), ("not_loaded", "nft: нет памяти", 1800000000.0))
        self.assertEqual(self.node(scheme.current_graph(), "rules:ru_direct")["data"]["ru"]["state"], "not_loaded")
        rules_apply._save_load_state({"ver": wanted, "ok": True, "elements": 1})
        self.assertEqual(scheme.ru_state()["state"], "ok")
        g = scheme.current_graph()
        self.assertEqual(self.node(g, "rules:ru_direct")["data"]["ru"]["state"], "ok")
        self.assertEqual(g["profiles"]["ru"]["state"], "ok")
        rules_apply._save_load_state({"ver": wanted + 1, "ok": True})          # the previous lists are in the table
        self.assertEqual((scheme.ru_state()["state"], scheme.ru_state().get("old")), ("ok", True))
        rules_apply.table_recreated(wanted)                                    # nft flush: the networks are gone
        self.assertEqual(scheme.ru_state()["state"], "not_loaded")
        self.assertIsNone(scheme.ru_state()["domains"])
        dns._save_features({"version": (2, 85), "nftset": False})
        self.assertFalse(scheme.ru_state()["domains"])


class AffectedTest(SchemeCase):
    def test_a_profile_moves_the_path_of_every_device_that_follows_it(self):
        """С8: «Сменится путь» counts the profile that applies: the profile of a user moves both devices of Мама (the
        laptop's own route stays — its profile does not). Without the lists a profile changes nothing that works."""
        self.scenario(enabled=False)
        self.legacy()
        intents = [{"type": "rules", "user": "u-00000001", "profile": "all_exit"}]
        res = scheme.apply({"dry_run": True, "intents": intents})
        self.assertEqual(res["affected"], [])                                # no lists: «Россия напрямую» = all_exit
        _lists()
        res = scheme.apply({"dry_run": True, "intents": intents})
        path = dict((a["device"], a) for a in res["affected"] if a["kind"] == "path")
        self.assertEqual(sorted(path), ["d-00000001", "d-00000003"])
        phone = path["d-00000001"]
        self.assertEqual((phone["profile_before"], phone["profile_after"]), ("ru_direct", "all_exit"))
        self.assertEqual(phone["before"], "правила «Россия напрямую», маршрут: пул (при отказе — блок)")
        self.assertEqual(phone["after"], "правила «Всё через выход», маршрут: пул (при отказе — блок)")
        self.assertNotIn("d-00000002", path)
        # a route alone: the text stays the route
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000002", "via": "block"}]})
        self.assertEqual([(a["device"], a["after"]) for a in res["affected"]], [("d-00000002", "блок")])
        # under «напрямую» or «блок» a profile changes no path; leaving «блок» for the pool with another profile — it does
        st = state.load()
        st["gateway"]["devices"][1]["route"] = {"via": "direct", "fail": "block"}
        state.save(st)
        res = scheme.apply({"dry_run": True, "intents": [{"type": "rules", "device": "d-00000002", "profile": "all_exit"}]})
        self.assertEqual(res["affected"], [])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "rules", "device": "d-00000002", "profile": "all_exit"},
                                                         {"type": "route", "device": "d-00000002", "via": "pool"}]})
        self.assertEqual([(a["before"], a["after"]) for a in res["affected"]],
                         [("напрямую", "правила «Всё через выход», маршрут: пул (при отказе — блок)")])

    def test_inbounds_given_and_taken(self):
        """С8: a given inbound — «выдайте настройки»; a taken one — its keys go, and the device online through it now
        loses the connection; the last one — no other inbound is left."""
        self.scenario(enabled=False)
        self.write("/run/kaskad/live.json", json.dumps({"v": 1, "ts": time.time() - 2, "seq": 3, "devices": {
            "d-00000002": {"online": True, "inbound": "wg", "rx": 1, "tx": 2}}}))
        snapshot.forget()
        res = scheme.apply({"dry_run": True, "intents": [
            {"type": "revoke", "device": "d-00000002", "kind": "wg"},
            {"type": "revoke", "device": "d-00000002", "kind": "awg2"},
            {"type": "revoke", "device": "d-00000003", "kind": "wg"},
            {"type": "grant", "device": "d-00000001", "kind": "awg2"}]})
        acc = dict(((a["device"], a["inbound"]), a) for a in res["affected"] if a["kind"] == "access")
        self.assertIn("сейчас на связи через него, связь оборвётся", acc[("d-00000002", "wg")]["text"])
        self.assertNotIn("сейчас на связи", acc[("d-00000002", "awg2")]["text"])
        self.assertIn("других протоколов у устройства не останется", acc[("d-00000002", "awg2")]["text"])
        self.assertIn("ключи удаляются", acc[("d-00000003", "wg")]["text"])
        self.assertEqual(acc[("d-00000001", "awg2")]["op"], "+")
        self.assertIn("выдайте устройству его настройки", acc[("d-00000001", "awg2")]["text"])


class ConsequencesTest(SchemeCase):
    def test_a_route_past_the_exits_is_said(self):
        """С3: «напрямую» and a fail policy «напрямую» let the traffic out past the exits — «Применить» says so."""
        self.scenario(enabled=False)
        res = scheme.apply({"dry_run": True, "intents": [
            {"type": "route", "device": "d-00000001", "via": "direct"},
            {"type": "route", "user": "u-00000002", "via": "pool", "fail": "direct"},
            {"type": "route", "default": True, "via": "pool", "fail": "direct"},
            {"type": "route", "device": "d-00000002", "via": "block"}]})
        w = res["warnings"]
        self.assertTrue(any("«Телефон» (Мама): маршрут «напрямую» — трафик пойдёт с адреса узла, мимо выходов" in x
                            for x in w), w)
        self.assertTrue(any(x.startswith("Дача: если выходы перестанут работать") for x in w), w)
        self.assertTrue(any(x.startswith("Маршрут по умолчанию: если выходы перестанут работать") for x in w), w)
        self.assertFalse(any("Роутер на даче" in x and "мимо" in x for x in w), w)       # «блок» lets nothing out

    def test_restarts_of_xray_and_dns_for_everybody(self):
        """С5: a change of a SOCKS5 device restarts xray of the gateway (every SOCKS5 and VLESS connection
        reconnects — counted from live.json); «a device may go directly» appearing restarts dnsmasq; a change of
        another device restarts nothing; a gateway that is off — nothing at all."""
        st = self.scenario(enabled=True)
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"][0]["access"] = ["wg", "socks5"]
        st["gateway"]["devices"][2]["route"] = {"via": "exit:" + self.vid, "fail": "block"}   # nobody direct yet
        state.save(st)
        self.write("/run/kaskad/live.json", json.dumps({"v": 1, "ts": time.time() - 2, "seq": 3, "devices": {
            "d-00000001": {"online": True, "inbound": "socks5", "rx": 1, "tx": 2}}}))
        snapshot.forget()
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000001", "via": "block"}]})
        what = dict((x["what"], x) for x in res["disruptions"])
        self.assertEqual(list(what), ["xray"])
        self.assertEqual(what["xray"]["devices"], 1)
        self.assertIn("переподключатся все соединения через SOCKS5, протокол VLESS и выходы VLESS", what["xray"]["text"])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000002", "via": "direct"}]})
        self.assertEqual([x["what"] for x in res["disruptions"]], ["dns"])                # Дача: no SOCKS5
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000002", "via": "block"}]})
        self.assertEqual(res["disruptions"], [])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "revoke", "device": "d-00000001", "kind": "socks5"}]})
        self.assertEqual([x["what"] for x in res["disruptions"]], ["xray"])
        # stage 1 (§2.4, REV-s1-t45 З2): a route of SOCKS5 changes live through xray's API (``hot_plan``) — no restart
        # is promised; was (3.3.5): any change of a SOCKS5 route restarted xray
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000001", "via": "direct"}]})
        self.assertNotIn("xray", [x["what"] for x in res["disruptions"]])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000001",
                                                          "via": "exit:" + self.aid}]})
        self.assertNotIn("xray", [x["what"] for x in res["disruptions"]])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "grant", "device": "d-00000002", "kind": "socks5"}]})
        self.assertIn("xray", [x["what"] for x in res["disruptions"]])                    # a new account: a restart
        st = state.load()
        st["gateway"]["enabled"] = False
        state.save(st)
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000001", "via": "block"}]})
        self.assertEqual(res["disruptions"], [])


class FailPolicyTest(SchemeCase):
    def test_a_new_target_keeps_the_inherited_fail_policy(self):
        """Н1 (T-scheme-3.2.15): the default «пул, при отказе — напрямую»; a person without a route of his own chooses
        «Пул» — the policy stays «напрямую» (it is what he inherited), not a silent «блок»; «Применить» says the policy
        on both sides; going back to «как по умолчанию» from an own «блок» warns «мимо выхода». The same in the core
        (ops: the CLI, the menu, the API) and for the default route itself."""
        st = self.scenario(enabled=False)
        st["gateway"]["routes"]["default"] = {"via": "pool", "fail": "direct"}
        st["gateway"]["users"][1]["route"] = None
        state.save(st)
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "user": "u-00000002", "via": "pool"}]})
        self.assertEqual(res["results"][0]["route"], {"via": "pool", "fail": "direct"})
        self.assertEqual(res["affected"], [])                                # nothing changes for its device
        # Н-7: «было → станет» — свой маршрут, равный унаследованному, путь не меняет: так и сказано, без «мимо выхода»
        self.assertEqual([c["text"] for c in res["changes"]],
                         ["Дача: путь не меняется — пул (при отказе — напрямую, мимо выхода); маршрут закрепляется за пользователем"])
        self.assertEqual(res["warnings"], [])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "user": "u-00000002", "via": "block"}]})
        self.assertEqual([c["text"] for c in res["changes"]],
                         ["Дача: маршрут по умолчанию узла: пул (при отказе — напрямую, мимо выхода) → свой: блок"])
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000003", "via": "inherit"}]})
        self.assertEqual([c["text"] for c in res["changes"]],
                         ["«Ноутбук» (Мама): свой маршрут снят → по умолчанию узла: пул (при отказе — напрямую, мимо выхода)"])
        res = scheme.apply({"intents": [{"type": "route", "user": "u-00000002", "via": "pool"}]}, runner=self.w.runner)
        self.assertEqual(model.get_user(state.load(), "u-00000002")["route"], {"via": "pool", "fail": "direct"})
        # an explicit «блок», then back to «как по умолчанию»: the change of the policy is said, with «мимо выхода»
        scheme.apply({"intents": [{"type": "route", "user": "u-00000002", "via": "pool", "fail": "block"}]},
                     runner=self.w.runner)
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "user": "u-00000002", "via": "inherit"}]})
        a = [x for x in res["affected"] if x["device"] == "d-00000002"][0]
        self.assertEqual((a["before"], a["after"]), ("пул (при отказе — блок)", "пул (при отказе — напрямую, мимо выхода)"))
        self.assertEqual(a["leak"], "fail")
        self.assertTrue(any("«Роутер на даче»: теперь пул (при отказе — напрямую, мимо выхода)" in w
                            for w in res["warnings"]), res["warnings"])
        # the core: a device, a person, the default — without --fail they keep their policy
        ops.device_route(self.w.runner, "d-00000001", "exit:" + self.aid)
        self.assertEqual(model.get_device(state.load(), "d-00000001")["route"]["fail"], "direct")     # inherited
        ops.device_route(self.w.runner, "d-00000003", "pool")
        self.assertEqual(model.get_device(state.load(), "d-00000003")["route"]["fail"], "direct")     # its own
        ops.user_route(self.w.runner, "u-00000002", "exit:" + self.aid)
        self.assertEqual(model.get_user(state.load(), "u-00000002")["route"]["fail"], "block")        # its own
        ops.default_route(self.w.runner, "block")
        ops.default_route(self.w.runner, "pool")
        self.assertEqual(state.load()["gateway"]["routes"]["default"]["fail"], "direct")
        ops.default_route(self.w.runner, "pool", "block")
        self.assertEqual(state.load()["gateway"]["routes"]["default"]["fail"], "block")


class OneAnswerTest(SchemeCase):
    def test_trace_and_the_scheme_say_the_same_for_every_device(self):
        """Н-1 (T-scheme-3.2.16): «куда идёт устройство сейчас» решает одна функция (exits.now_of) — у «Куда пойдёт
        трафик?» и у схемы (now, «закреплено») один ответ для каждого устройства: SOCKS5 (единственный вход) — xray,
        любой живой выход («any»), ни за одним выходом не закреплено; ядро — выход по hash или балансировщик VLESS;
        мёртвые выходы — политика отказа."""
        from kaskad.gw import trace
        st = self.scenario()
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"].append({"id": "d-00000004", "user": "u-00000001", "name": "socks-g3", "kind": "pc",
                                         "platform": "windows", "route": None, "enabled": True, "added": "2026-10-07T00:00:00Z",
                                         "login": "socks-0004", "ips": {}, "access": ["socks5"]})
        st["gateway"]["devices"].append({"id": "d-00000005", "user": "u-00000001", "name": "mixed", "kind": "pc",
                                         "platform": "windows", "route": None, "enabled": True, "added": "2026-10-07T00:00:00Z",
                                         "login": "mixed-0005", "ips": {"wg": "10.77.1.15"}, "access": ["wg", "socks5"]})
        state.save(st)
        for dead in ((), (self.vid,), (self.vid, self.aid)):
            self.dead(*dead)
            st = state.load()
            g = self.graph()
            rows = dict((d["id"], d) for n in g["nodes"] if n["type"] == "user" for d in n["data"]["devices"])
            pinned = dict((n["id"], n["data"]["now_devices"]) for n in g["nodes"] if n["type"] == "exit")
            for did, row in rows.items():
                res = trace.trace(st, did, "8.8.8.8", resolver=lambda name: ([], "нет"))
                hl, now = res["highlight"], row["now"]
                self.assertEqual(hl["balancer"], now["balancer"], (dead, did))
                self.assertEqual(hl["exit"] or None, now["exit"] if not now["balancer"] and (now["target"] or "").startswith("exit:")
                                 and row["effective"]["via"] == "pool" else None, (dead, did))
                if now["balancer"]:
                    self.assertEqual(sorted(hl["exits"]), sorted(now["exits"]), (dead, did))
                if res["verdict"] != "exit":
                    self.assertEqual(hl["target"], now["target"], (dead, did))
            if not dead:
                # stage 1 (SE §2.4): xray takes the pin of the device too — no balancer «any»
                self.assertFalse(rows["d-00000004"]["now"]["balancer"])
                self.assertTrue(rows["d-00000004"]["now"]["exit"])
                self.assertEqual(rows["d-00000005"]["now"]["inbound"], "wg")          # the first given: the kernel
                # «закреплено» at an exit: the SOCKS5 device too (stage 1, §2.4)
                pinned_ids = [d for d, r in rows.items() if r["now"]["exit"]]
                self.assertIn("d-00000004", pinned_ids)
                self.assertEqual(sum(pinned.values()), len(pinned_ids))


class LayoutTest(SchemeCase):
    def test_versions_merge_prune_and_conflict(self):
        self.scenario()
        res = scheme.save_layout({"version": 0, "positions": {"u-00000001": [10, 20], "in:wg": {"x": 300, "y": 40.26},
                                                              "exit:e-ffffffffff": [1, 1]}})
        self.assertEqual(res, {"ok": True, "version": 1})
        doc = json.loads(self.read("/etc/kaskad/panel/layout.json"))
        self.assertEqual(doc["positions"], {"u-00000001": [10.0, 20.0], "in:wg": [300.0, 40.3]})   # unknown id dropped
        self.assertEqual(oct(os.stat(scheme.layout_path()).st_mode & 0o777), "0o600")
        g = scheme.current_graph()
        self.assertEqual(g["layout_version"], 1)
        self.assertEqual(self.node(g, "u-00000001")["position"], {"x": 10.0, "y": 20.0})
        # the second tab saved first: 409 with what is saved now
        with self.assertRaises(HttpError) as cm:
            scheme.save_layout({"version": 0, "positions": {"pool": [5, 5]}})
        self.assertEqual((cm.exception.status, cm.exception.code), (409, errors.CONFLICT))
        self.assertEqual(cm.exception.data["version"], 1)
        self.assertEqual(cm.exception.data["positions"]["u-00000001"], [10.0, 20.0])
        # merged on top of the saved version; the order of device rows too
        scheme.save_layout({"version": 1, "positions": {"pool": [5, 5]},
                            "orders": {"u-00000001": ["d-00000003", "d-00000001", "d-00000099"]}})
        g = scheme.current_graph()
        self.assertEqual(self.node(g, "pool")["position"], {"x": 5.0, "y": 5.0})
        self.assertEqual(self.node(g, "in:wg")["position"], {"x": 300.0, "y": 40.3})
        self.assertEqual([d["id"] for d in self.node(g, "u-00000001")["data"]["devices"]],
                         ["d-00000003", "d-00000001"])
        self.assertEqual(json.loads(self.read("/etc/kaskad/panel/layout.json"))["orders"],
                         {"u-00000001": ["d-00000003", "d-00000001"]})
        scheme.save_layout({"version": 2, "positions": {}, "reset": True})
        self.assertIsNone(self.node(scheme.current_graph(), "pool")["position"])

    def test_bad_input(self):
        self.scenario()
        for body in ({"version": "0", "positions": {}}, {"version": True}, {"version": 0, "positions": []},
                     {"version": 0, "positions": {"../etc/passwd": [1, 2]}},
                     {"version": 0, "positions": {"pool": [float("inf"), 1]}},
                     {"version": 0, "positions": {"pool": [1e9, 1]}},
                     {"version": 0, "positions": {"pool": ["1", 2]}},
                     {"version": 0, "orders": {"u-00000001": ["rm -rf"]}}):
            with self.assertRaises(HttpError) as cm:
                scheme.save_layout(body)
            self.assertEqual(cm.exception.status, 400, body)
        self.assertFalse(os.path.exists(scheme.layout_path()))

    def test_broken_file_is_version_zero(self):
        self.scenario()
        self.write("/etc/kaskad/panel/layout.json", "{oops")
        self.assertEqual(scheme.load_layout(), {"version": 0, "positions": {}, "orders": {}})
        self.assertEqual(scheme.save_layout({"version": 0, "positions": {"pool": [1, 2]}})["version"], 1)


class ApplyTest(SchemeCase):
    INTENTS = [{"type": "grant", "device": "d-00000001", "kind": "awg2"},
               {"type": "revoke", "device": "d-00000002", "kind": "wg"},
               {"type": "route", "device": "d-00000001", "via": "direct"},
               {"type": "route", "user": "u-00000002", "via": "inherit"},
               {"type": "route", "default": True, "via": "block"}]

    def test_dry_run_changes_nothing(self):
        self.scenario(enabled=False)
        secrets.set("dev.d-00000002.wg.private", "KEY")
        before = self.files()
        calls = len(self.w.runner.calls)
        res = scheme.apply({"dry_run": True, "intents": self.INTENTS})
        self.assertTrue(res["dry_run"])
        self.assertTrue(res["valid"], res["results"])
        self.assertFalse(res["noop"])
        self.assertEqual([r["ok"] for r in res["results"]], [True] * 5)
        self.assertEqual(res["results"][0]["ips"], {"awg2": "10.77.3.11"})
        self.assertIn("10.77.1.11", res["results"][1]["text"])               # the address it frees
        texts = [c["text"] for c in res["changes"]]
        self.assertIn("«Телефон» (Мама): + Тестовый вход awg2 (адрес 10.77.3.11)", texts)
        self.assertIn("Маршрут по умолчанию: пул (при отказе — блок) → блок", texts)
        affected = dict((a["device"], a) for a in res["affected"] if a["kind"] == "path")
        self.assertEqual(affected["d-00000001"]["after"], "напрямую")
        self.assertEqual(affected["d-00000002"]["after"], "блок")             # Дача now follows the default
        self.assertNotIn("d-00000003", affected)                              # its own route stays
        access = [(a["device"], a["op"], a["inbound"]) for a in res["affected"] if a["kind"] == "access"]
        self.assertEqual(access, [("d-00000001", "+", "awg2"), ("d-00000002", "-", "wg")])
        self.assertEqual(res["version"], scheme.graph_version(state.load()))
        self.assertEqual(self.files(), before)                                # byte for byte
        self.assertEqual(len(self.w.runner.calls), calls)                     # no command ran
        self.assertEqual(secrets.load()["dev.d-00000002.wg.private"], "KEY")

    def test_invalid_kind_and_inbound_not_installed(self):
        self.scenario(enabled=False)
        bad = [{"type": "grant", "device": "d-00000001", "kind": "awg2"},
               {"type": "grant", "device": "d-00000001", "kind": "foo"},
               {"type": "grant", "device": "d-00000001", "kind": "awg3"},
               {"type": "grant", "device": "d-nope0000", "kind": "wg"},
               {"type": "teleport", "device": "d-00000001"}]
        res = scheme.apply({"dry_run": True, "intents": bad})
        self.assertFalse(res["valid"])
        ok, foo, awg3, nodev, unknown = res["results"]
        self.assertTrue(ok["ok"])
        self.assertEqual((foo["ok"], foo["code"]), (False, errors.USAGE))
        self.assertIn("неизвестный вход", foo["error"])
        self.assertEqual((awg3["ok"], awg3["code"]), (False, errors.CONFLICT))
        self.assertIn("не установлен", awg3["error"])
        self.assertFalse(nodev["ok"])
        self.assertIn("неизвестное изменение", unknown["error"])
        before = self.files()
        with self.assertRaises(HttpError) as cm:
            scheme.apply({"intents": bad}, runner=self.w.runner)
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("неизвестный вход", cm.exception.text)
        self.assertEqual(len(cm.exception.data["results"]), 5)
        self.assertEqual(self.files(), before)                                # nothing at all, not even the 1st

    def test_route_to_an_exit_not_in_the_pool(self):
        self.scenario(enabled=False)
        before = self.files()
        intents = [{"type": "route", "device": "d-00000001", "via": "exit:e-0123456789"}]
        res = scheme.apply({"dry_run": True, "intents": intents})
        self.assertEqual((res["valid"], res["results"][0]["code"]), (False, errors.CONFLICT))
        self.assertIn("нет в пуле", res["results"][0]["error"])
        with self.assertRaises(HttpError) as cm:
            scheme.apply({"intents": intents}, runner=self.w.runner)
        self.assertEqual(cm.exception.status, 409)
        res = scheme.apply({"dry_run": True, "intents": [{"type": "route", "device": "d-00000001", "via": "mars"}]})
        self.assertEqual(res["results"][0]["code"], errors.USAGE)
        self.assertEqual(self.files(), before)

    def test_warnings_dead_exit_and_the_admins_own_device(self):
        self.scenario(enabled=False)
        self.dead(self.vid)
        res = scheme.apply({"dry_run": True, "intents": [
            {"type": "route", "device": "d-00000001", "via": "exit:" + self.vid},
            {"type": "route", "device": "d-00000002", "via": "block"}]}, client_ip="10.77.3.10")
        self.assertTrue(res["valid"])
        self.assertIn("не работает", res["results"][0]["warnings"][0])
        self.assertIn("панель открыта с этого устройства", res["results"][1]["warnings"][-1])

    def test_warnings_a_dead_group(self):
        """Stage 1 (REV-s1-t4 З6): a route ``group:<gid>`` whose every exit is dead — a warning with the name of the
        group and where the traffic goes (its fail policy); a live exit in it — none."""
        from kaskad.gw import policy as P
        self.scenario(enabled=False)
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]
        doc["groups"].append(P.default_group("g-123456", "abroad", "compat", name="Европа", exits=[self.aid]))
        st["gateway"]["exit_policy"] = doc
        state.save(st)
        self.dead(self.aid)
        intents = [{"type": "route", "default": True, "via": "group:g-123456"}]
        res = scheme.apply({"dry_run": True, "intents": intents})
        self.assertTrue(res["valid"], res)
        self.assertIn("в группе «Европа» сейчас нет живых выходов — пока трафик: блок", res["results"][0]["warnings"])
        st = state.load()
        st["gateway"]["exit_policy"]["groups"][1]["fail"] = "direct"     # the fail policy of the group says where
        state.save(st)
        res = scheme.apply({"dry_run": True, "intents": intents})
        self.assertIn("в группе «Европа» сейчас нет живых выходов — пока трафик: напрямую",
                      res["results"][0]["warnings"])
        self.dead(self.vid)                                              # «Вена» dead, the group's exit alive
        res = scheme.apply({"dry_run": True, "intents": intents})
        self.assertFalse([w for w in res["results"][0]["warnings"] if "в группе" in w], res)

    def test_real_apply_is_one_change(self):
        self.scenario(enabled=False)
        secrets.set("dev.d-00000002.wg.private", "KEY")
        secrets.set("dev.d-00000002.awg2.private", "KEY2")
        version = scheme.graph_version(state.load())
        with mock.patch.object(ops, "_change", wraps=ops._change) as change:
            res = scheme.apply({"intents": self.INTENTS, "version": version}, runner=self.w.runner)
        self.assertEqual(change.call_count, 1)
        self.assertTrue(res["applied"])
        self.assertEqual([r["ok"] for r in res["results"]], [True] * 5)
        st = state.load()
        phone, dacha = model.get_device(st, "d-00000001"), model.get_device(st, "d-00000002")
        self.assertEqual((phone["access"], phone["ips"]), (["wg", "awg2"], {"wg": "10.77.1.10", "awg2": "10.77.3.11"}))
        self.assertEqual(phone["route"], {"via": "direct", "fail": "block"})
        self.assertEqual((dacha["access"], dacha["ips"]), (["awg2"], {"awg2": "10.77.3.10"}))
        self.assertIsNone(model.get_user(st, "u-00000002")["route"])
        self.assertEqual(st["gateway"]["routes"]["default"], {"via": "block", "fail": "block"})
        sec = secrets.load()
        self.assertNotIn("dev.d-00000002.wg.private", sec)                    # the revoked inbound's keys
        self.assertEqual(sec["dev.d-00000002.awg2.private"], "KEY2")
        g = res["graph"]
        self.assertIn("access:d-00000001:awg2", _ids(g["edges"]))
        self.assertNotIn("access:d-00000002:wg", _ids(g["edges"]))
        self.assertEqual(g["version"], scheme.graph_version(st))
        log = self.read("/var/log/kaskad.log")
        self.assertIn("GW device grant: Телефон (d-00000001) awg2 — схема панели", log)
        self.assertIn("GW route default -> block — схема панели", log)            # no policy for «блок»
        # the same list again: nothing to change, no apply of the gateway
        with mock.patch.object(ops, "_change") as change:
            res = scheme.apply({"intents": self.INTENTS[:1] + self.INTENTS[2:]}, runner=self.w.runner)
        self.assertFalse(res["applied"])
        self.assertEqual(change.call_count, 0)

    def test_new_target_keeps_the_fail_policy(self):
        self.scenario(enabled=False)
        scheme.apply({"intents": [{"type": "route", "device": "d-00000003", "via": "exit:" + self.aid}]},
                     runner=self.w.runner)
        self.assertEqual(model.get_device(state.load(), "d-00000003")["route"],
                         {"via": "exit:" + self.aid, "fail": "direct"})
        scheme.apply({"intents": [{"type": "route", "device": "d-00000003", "via": "pool", "fail": "block"}]},
                     runner=self.w.runner)
        self.assertEqual(model.get_device(state.load(), "d-00000003")["route"], {"via": "pool", "fail": "block"})

    def test_stale_version_and_a_change_made_meanwhile(self):
        self.scenario(enabled=False)
        old = scheme.graph_version(state.load())
        st = state.load()
        st["gateway"]["devices"][0]["route"] = {"via": "block", "fail": "block"}         # the bot changed it
        state.save(st)
        before = self.files()
        with self.assertRaises(HttpError) as cm:
            scheme.apply({"intents": self.INTENTS[:1], "version": old}, runner=self.w.runner)
        self.assertEqual((cm.exception.status, cm.exception.data["reason"]), (409, "stale"))
        self.assertEqual(self.files(), before)

        # the state changes between the dry pass and the lock: every intent runs again under it and fails there
        def sneaky(runner, mutate, rf=None):
            s = state.load()
            model.del_device(s, "d-00000001")
            state.save(s)
            return real(runner, mutate, rf=rf)
        real = ops._change
        with mock.patch.object(ops, "_change", sneaky):
            with self.assertRaises(HttpError) as cm:
                scheme.apply({"intents": self.INTENTS[:1]}, runner=self.w.runner)
        self.assertEqual(cm.exception.status, 409)
        self.assertTrue(cm.exception.data["results"][0]["error"])

    @mock.patch.object(scheme, "_model_fn", lambda *names: None)      # the model of 3.1: no rules profiles
    def test_rules_wait_for_3_2(self):
        self.scenario(enabled=False)
        before = self.files()
        for dry in (True, False):
            with self.assertRaises(HttpError) as cm:
                scheme.apply({"dry_run": dry, "intents": [{"type": "rules", "device": "d-00000001",
                                                           "profile": "ru_direct"}]}, runner=self.w.runner)
            self.assertEqual((cm.exception.status, cm.exception.code), (409, errors.UNSUPPORTED))
            self.assertIn("не знает профилей правил", cm.exception.text)
        self.assertEqual(self.files(), before)

    def test_bad_requests(self):
        self.scenario(enabled=False)
        for body in ({}, {"intents": []}, {"intents": {}}, {"intents": ["grant"]},
                     {"intents": [{"type": "grant"}] * (scheme.MAX_INTENTS + 1)},
                     {"intents": [{"type": "grant", "device": "d-00000001", "kind": "wg"}], "version": 5}):
            with self.assertRaises(HttpError) as cm:
                scheme.apply(body, runner=self.w.runner)
            self.assertEqual(cm.exception.status, 400, body)

    def test_on_a_running_gateway(self):
        self.scenario(enabled=False)
        self.w.leg(2)
        ops.enable(self.w.runner)
        self.assertNotEqual(self.w.nft.elements("gw_route").get("10.77.1.10"), 0x201)
        writes = len(self.w.nft_writes())
        res = scheme.apply({"intents": [{"type": "route", "device": "d-00000001", "via": "direct"},
                                        {"type": "route", "device": "d-00000003", "via": "block"}]},
                           runner=self.w.runner)
        self.assertTrue(res["gateway_applied"])
        m = self.w.nft.elements("gw_route")
        self.assertEqual((m["10.77.1.10"], m["10.77.1.12"]), (0x201, 0x2ff))
        self.assertEqual(len(self.w.nft_writes()) - writes, 1)               # one transaction for both


class HttpTest(SchemeCase):
    def call(self, method, path, body=None, client_ip="127.0.0.1"):
        r, m = routes.find(method, path)
        self.assertNotIn(r, (None, "method"), path)
        req = Request(method, path, body=json.dumps(body).encode("utf-8") if body is not None else b"", match=m,
                      session={"admin": True}, client_ip=client_ip)
        try:
            resp = r.handler(req)
        except HttpError as e:
            resp = routes.error_response(e)
        return resp.status, json.loads(resp.body.decode("utf-8")), r

    def test_routes(self):
        self.scenario(enabled=False)
        status, doc, r = self.call("GET", "/api/scheme")
        self.assertEqual((status, r.auth, r.csrf), (200, "admin", False))
        self.assertTrue(doc["ok"])
        self.assertIsNone(doc["live"])
        self.write("/run/kaskad/live.json", json.dumps({"v": 1, "ts": time.time() - 2, "seq": 3, "devices": {
            "d-00000001": {"online": True, "inbound": "wg", "rx": 10, "tx": 20}}}))
        status, doc, _ = self.call("GET", "/api/scheme")
        self.assertEqual(doc["live"]["devices"]["d-00000001"]["rx"], 10)
        # the collector stopped long ago: the old snapshot is unknown, not «online» (C2)
        self.write("/run/kaskad/live.json", json.dumps({"v": 1, "ts": time.time() - 3600, "seq": 4, "devices": {
            "d-00000001": {"online": True, "inbound": "wg", "rx": 10, "tx": 20}}}))
        snapshot.forget()
        status, doc, _ = self.call("GET", "/api/scheme")
        self.assertIsNone(doc["live"])
        status, doc, r = self.call("PUT", "/api/scheme/layout", {"version": 0, "positions": {"pool": [1, 2]}})
        self.assertEqual((status, doc, r.csrf), (200, {"ok": True, "version": 1}, True))
        status, doc, _ = self.call("PUT", "/api/scheme/layout", {"version": 0, "positions": {"pool": [1, 2]}})
        self.assertEqual((status, doc["ok"], doc["code"], doc["data"]["version"]), (409, False, errors.CONFLICT, 1))
        status, doc, r = self.call("POST", "/api/scheme/apply", {"dry_run": True, "intents": [
            {"type": "route", "device": "d-00000001", "via": "block"}]})
        self.assertEqual((status, doc["valid"], r.csrf), (200, True, True))
        status, doc, _ = self.call("POST", "/api/scheme/apply", {"intents": [
            {"type": "grant", "device": "d-00000001", "kind": "foo"}]})
        self.assertEqual((status, doc["ok"]), (409, False))
        self.assertIn("неизвестный вход", doc["error"])

    def test_trace(self):
        """«Куда пойдёт трафик?»: POST /api/gw/trace (admin, CSRF) answers like ``kaskadctl gw trace`` and lights
        only what the graph has; nothing on disk changes."""
        from kaskad.gw import net, trace
        from kaskad.rules import apply as rapply
        from tests.rules_helpers import consented, seed_small, small_limits
        with small_limits():
            st = self.scenario()
            st["gateway"]["users"][0]["rules"] = "ru_direct"
            st["gateway"]["rules"] = {"default": "all_exit"}
            state.save(consented(st))      # stage 1: «Россия напрямую» applies with the consent ⚠РФ (R2)
            seed_small()
            rapply._save_load_state({"ver": net.ru_wanted(), "ok": True})
            snapshot.forget()
            before = self.files()
            with mock.patch.object(trace, "resolve", lambda name: (["93.184.216.34"], None)):
                status, doc, r = self.call("POST", "/api/gw/trace", {"who": "d-00000001", "target": "youtube.com"})
                self.assertEqual((status, r.auth, r.csrf), (200, "admin", True))
                self.assertEqual((doc["ok"], doc["verdict"], doc["match"]["group"]), (True, "exit", "force"))
                g = scheme.current_graph()
                nodes, edges = _ids(g["nodes"]), _ids(g["edges"])
                for who, target in (("d-00000001", "youtube.com"), ("d-00000001", "mail.yandex.ru"),
                                    ("d-00000001", "77.88.1.1"), ("d-00000002", "ad.mail.ru"), ("-", "example.com"),
                                    ("u-00000002", "8.8.8.8"), ("d-00000003", "example.com")):
                    status, doc, _ = self.call("POST", "/api/gw/trace", {"who": who, "target": target})
                    self.assertEqual(status, 200, doc)
                    hl = doc["highlight"]
                    self.assertTrue(set(hl["nodes"]) <= nodes, (who, target, set(hl["nodes"]) - nodes))
                    self.assertTrue(set(hl["edges"]) <= edges, (who, target, set(hl["edges"]) - edges))
                    self.assertIn(doc["verdict"], ("block", "direct", "exit"))
                status, doc, _ = self.call("POST", "/api/gw/trace", {"who": "d-00000001", "target": "77.88.1.1"})
                self.assertEqual((doc["verdict"], doc["highlight"]["category"]), ("direct", "ru"))
                self.assertIn("cat:ru_direct:ru", doc["highlight"]["edges"])
                # through a chosen inbound: that one alone; one the device has not — 400
                status, doc, _ = self.call("POST", "/api/gw/trace", {"who": "d-00000002", "target": "8.8.8.8",
                                                                     "inbound": "wg"})
                self.assertEqual((status, doc["who"]["inbound"]), (200, "wg"))
                self.assertIn("access:d-00000002:wg", doc["highlight"]["edges"])
                self.assertNotIn("access:d-00000002:awg2", doc["highlight"]["edges"])
                for bad in ({"who": "d-00000001", "target": "8.8.8.8", "inbound": "awg2"},
                            {"who": "d-00000001", "target": "8.8.8.8", "inbound": 5},
                            {"who": "d-00000001", "target": "8.8.8.8", "inbound": "nope"}):
                    status, doc, _ = self.call("POST", "/api/gw/trace", bad)
                    self.assertEqual(status, 400, bad)
            for body in ({}, {"who": 5, "target": "ya.ru"}, {"who": "-", "target": "not a domain"},
                         {"who": "d-99999999", "target": "ya.ru"}, {"who": "-", "target": "x" * 600}):
                status, doc, _ = self.call("POST", "/api/gw/trace", body)
                self.assertEqual((status, doc["ok"]), (400, False), body)
            self.assertEqual(self.files(), before)
