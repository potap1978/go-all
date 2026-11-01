"""T15 Q4 (SPEC-groups §2.2, §2.4, §2.9; SE §3.8 С2, Р2; §13.1 «ключ в двух файлах», «смена роли», «смена #имени»):
the Russian file of keys through the core — ``exit_add`` into a Russian group, a subscription of a Russian group, the
map ``ids`` by both files, ``plan`` that finds its own keys in both files and updates them in place, the change of role
that keeps the id of the exit and resets what §2.2 resets, the removal of a Russian key. Every test fails on the code
before Q4: there the core never reads upstream-ru.txt, ``exit_add --group <ru>`` is refused and the change of role
moves no line."""

import json
import os

from kaskad import paths, state
from kaskad.errors import ConflictError
from kaskad.gw import exits, exitsubs, ops, pins as PN, policy, policy_ops
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool, write_ru_pool
from tests.test_335_rename import RenameCase, key
from tests.test_gw_exitsubs import IP_URL, b64

HOME = "g-0a0b0c"
RU_URL = "http://203.0.113.51:2096/sub/0123456789abcdef"
ROUTER = ("vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.60:443?type=tcp&security=reality"
          "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Router")


def ru_key(name, uuid="dddddddd-1111-2222-3333-444444444444", host="203.0.113.61"):
    return ("vless://%s@%s:443?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54"
            "&sni=www.microsoft.com&fp=chrome#%s" % (uuid, host, name))


class RuCase(RenameCase):
    def setUp(self):
        super().setUp()
        write_ru_pool([])
        with state.transaction() as st:
            doc = policy.default_doc("standard")
            doc["groups"].append(policy.default_group(HOME, "ru", name="Дом"))
            st["gateway"]["exit_policy"] = doc

    def ru(self):
        return exits.pool_lines("ru")

    def ru_ids(self):
        return [i for i, _ in exits.line_ids(role="ru")]

    def group_of(self, eid):
        g = policy.group_of(policy.load(state.load()), eid)
        return g and g["id"]

    def move(self, eid, group, **kw):
        return policy_ops.exit_move(self.w.runner, eid, group, confirm_role=True, **kw)

    def calls(self):
        return [c[0] for c in self.w.script.calls]


class AddRuTest(RuCase):
    def test_key_into_a_russian_group_goes_to_its_file_only(self):
        write_pool([VLESS_A])
        res = ops.exit_add(self.w.runner, ROUTER, group=HOME)
        rid = exits.exit_id(ROUTER)
        self.assertEqual([a["id"] for a in res["added"]], [rid])
        self.assertEqual(self.ru(), [ROUTER])
        self.assertEqual(self.pool(), [VLESS_A])                     # the bot's pool untouched
        self.assertIn("--egress-ru-apply", self.calls())
        self.assertNotIn("--egress-add", self.calls())
        self.assertNotIn("--egress-replace", self.calls())
        self.assertEqual(self.group_of(rid), HOME)                     # the hint before the line, placed after
        row = [e for e in exits.pool(self.w.runner, roles=exits.ROLES) if e["id"] == rid][0]
        self.assertEqual((row["role"], row["index"], row["tag"]), ("ru", 201, "gr-" + rid))
        self.assertEqual(exits.pool(self.w.runner), [e for e in exits.pool(self.w.runner) if e["role"] == "abroad"])

    def test_a_key_of_the_other_file_is_refused_both_ways(self):
        write_pool([VLESS_A])
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, VLESS_A.replace("#Vienna", "#Home"), group=HOME)   # the same server
        self.assertEqual(c.exception.data["code"], "key_in_other_role")
        self.assertIn("зарубежных", str(c.exception))
        self.assertEqual(self.ru(), [])
        write_ru_pool([ROUTER])
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, ROUTER)                        # into «main»: refused before the script
        self.assertEqual(c.exception.data["code"], "key_in_other_role")
        self.assertIn("внутренних", str(c.exception))
        self.assertNotIn("--egress-add", self.calls())
        self.assertEqual(self.pool(), [VLESS_A])

    def test_again_and_full(self):
        ops.exit_add(self.w.runner, ROUTER, group=HOME)
        with self.assertRaises(ConflictError):
            ops.exit_add(self.w.runner, ROUTER, group=HOME)
        write_ru_pool([ru_key("k%d" % i, uuid="dddddddd-1111-2222-3333-%012d" % i) for i in range(32)])
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, ru_key("one more", uuid="eeeeeeee-1111-2222-3333-444444444444"), group=HOME)
        self.assertIn("32", str(c.exception))

    def test_foreign_group_for_a_russian_key_is_refused(self):
        """A key whose group is «main» never lands in upstream-ru.txt; an unknown group — USAGE."""
        with self.assertRaises(Exception):
            ops.exit_add(self.w.runner, ROUTER, group="nope")
        self.assertEqual(self.ru(), [])

    def test_delete_a_russian_key(self):
        ops.exit_add(self.w.runner, ROUTER, group=HOME)
        rid = exits.exit_id(ROUTER)
        exitsubs.save(exitsubs.load())                                  # a node with subscriptions
        res = ops.exit_del(self.w.runner, rid)
        self.assertEqual((res["ok"], res["role"]), (True, "ru"))
        self.assertEqual(self.ru(), [])
        with open(exitsubs.pending_path()) as f:
            self.assertEqual(f.read().split(), [rid])
        self.assertNotIn("--egress-del", self.calls())


class SubsRuTest(RuCase):
    def test_subscription_of_a_russian_group_writes_its_file(self):
        res = exitsubs.add(self.w.runner, RU_URL, fetcher=self.feed_for(RU_URL, b64([ru_key("A"), ROUTER])), group=HOME)
        self.assertTrue(res["ok"])
        self.assertEqual(self.ru(), [ru_key("A"), ROUTER])
        self.assertEqual(self.pool(), [])
        sub = exitsubs.load()["subs"][0]
        self.assertEqual((sub["group"], sub["role"]), (HOME, "ru"))
        # renamed by the provider: in place, the id kept, still only in upstream-ru.txt
        eid = self.ru_ids()[0]
        self.feed.bodies[RU_URL] = b64([ru_key("A 2"), ROUTER])
        self.refresh()
        self.assertEqual(self.ru(), [ru_key("A 2"), ROUTER])
        self.assertEqual(self.ru_ids()[0], eid)
        self.assertEqual(self.pool(), [])
        # the group deleted later: the role stays — its keys never go abroad
        sub = exitsubs.load()
        sub["subs"][0]["group"] = "g-0d0e0f"
        exitsubs.save(sub)
        self.feed.bodies[RU_URL] = b64([ru_key("A 3"), ROUTER, ru_key("B", uuid="ffffffff-1111-2222-3333-444444444444")])
        self.refresh()
        self.assertEqual(len(self.ru()), 3)
        self.assertEqual(self.pool(), [])

    def feed_for(self, url, body):
        self.feed.bodies[exitsubs.normalize(url)[0]] = body
        return self.feed

    def test_a_line_the_other_file_has_by_hand_is_a_duplicate(self):
        """Р2: a line of the subscription whose key upstream-ru.txt holds as a key added by hand — in «dups», not
        written into upstream.txt, not silent."""
        write_ru_pool([key("Дом")])
        res = self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(self.ru(), [key("Дом")])
        self.assertEqual(res["result"]["dups"], 1)


class RoleMoveTest(RuCase):
    def renamed_once(self):
        """A key of a subscription renamed once: the map «ids» holds it (its id is not the id of its text)."""
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(self.ids()[0], eid)
        self.assertNotEqual(exits.exit_id(key("9GB left")), eid)
        return eid

    def test_move_to_russia_keeps_the_id_through_any_refresh_and_back(self):
        eid = self.renamed_once()
        res = self.move(eid, HOME)
        self.assertEqual(res["role"]["to"], "ru")
        self.assertEqual(self.ru(), [key("9GB left")])                 # the line as it is
        self.assertEqual(self.pool(), [VLESS_B])                       # exactly one file
        self.assertEqual(self.ru_ids(), [eid])                         # the id of the exit, not of the text
        self.assertEqual(self.group_of(eid), HOME)
        self.assertIn("--egress-ru-apply", self.calls())
        self.assertIn("--egress-replace", self.calls())                # the bot's pool rebuilt (the line left it)
        # another subscription refreshes: the entry of the map stays (prune by both files, С2)
        other = "http://203.0.113.52:2096/sub/fedcba9876543210"
        self.add(other, b64([key("x", uuid="99999999-2222-3333-4444-555555555555")]))
        self.refresh()
        self.assertEqual(self.ru_ids(), [eid])
        self.assertIn(eid, exitsubs.load()["ids"].values())
        # its own subscription refreshes with the same text: not written into upstream.txt again (Р2)
        self.refresh()
        self.assertEqual(self.pool().count(key("9GB left")), 0)
        self.assertEqual(self.ru(), [key("9GB left")])
        # the provider renames it: updated in place in upstream-ru.txt, the id kept
        self.feed.bodies[IP_URL] = b64([key("8GB left"), VLESS_B])
        r = self.refresh()["results"][0]
        self.assertEqual(self.ru(), [key("8GB left")])
        self.assertEqual(self.ru_ids(), [eid])
        self.assertNotIn(key("8GB left"), self.pool())
        self.assertEqual(r["added"], 0)
        # and back: abroad again with the same id
        self.move(eid, policy.MAIN)
        self.assertEqual(self.ru(), [])
        self.assertIn(key("8GB left"), self.pool())
        self.assertIn(eid, self.ids())
        self.refresh()
        self.assertIn(eid, self.ids())

    def test_dropped_by_the_subscription_goes_from_the_russian_file(self):
        eid = self.renamed_once()
        self.move(eid, HOME)
        self.feed.bodies[IP_URL] = b64([VLESS_B])
        self.refresh()
        self.assertEqual(self.ru(), [])
        self.assertEqual(self.pool(), [VLESS_B])

    def test_no_confirm_no_move(self):
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        with self.assertRaises(policy_ops.Refused) as c:
            policy_ops.exit_move(self.w.runner, eid, HOME)
        self.assertEqual(c.exception.key, "confirm_role")
        self.assertEqual(self.pool(), [VLESS_A])
        self.assertEqual(self.ru(), [])
        dry = policy_ops.exit_move(self.w.runner, eid, HOME, confirm_role=True, dry=True)
        self.assertTrue(dry["dry"])
        self.assertEqual((self.pool(), self.ru()), ([VLESS_A], []))

    def test_reset_and_kept(self):
        """§2.2: the state, windows, expected country and the pins of the old group reset; the quota and its counters
        kept, the last reading of the counters reset (П3)."""
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        quota = {"limit_gb": 100, "period": "month", "reset_day": 1, "warn_pct": 80, "action": "remove"}
        with state.transaction() as st:
            st["gateway"]["exits"][eid] = {"enabled": True, "name": "Вена", "quota": quota,
                                           "country": {"expected": "AT", "accepted_at": 1, "notified": []}}
        snap = exits.load_snapshot()
        snap.setdefault("exits", {})[eid] = {"alive": True, "state": "degraded", "reasons": ["loss"], "off_until": 9}
        exits.save_snapshot(snap)
        PN._write(paths.pins_json(), {"version": PN.VERSION, "saved": 1, "gw_all": None, "groups": {
            "main": {"pins": {"d-00000001": {"exit": eid, "tier": "t1", "since": 1, "why": "new"},
                              "d-00000002": {"exit": exits.exit_id(VLESS_B), "tier": "t1", "since": 1, "why": "new"}}}}})
        PN._write(paths.traffic_json(), {"version": PN.VERSION, "exits": {eid: {
            "day": "2026-10-19", "day_bytes": 5, "period_start": "2026-10-01", "period_bytes": 777,
            "last": {"rx": 1000, "tx": 2000}, "warned": {}}}})
        res = self.move(eid, HOME)
        self.assertEqual(self.ru_ids(), [eid])
        self.assertEqual(set(res["role"]["reset"]) >= {"country", "state", "pins", "traffic_last"}, True)
        p = state.load()["gateway"]["exits"][eid]
        self.assertNotIn("country", p)
        self.assertEqual(p["quota"]["limit_gb"], 100)                  # the tariff of the same server
        rec = (exits.load_snapshot().get("exits") or {}).get(eid) or {}
        self.assertNotIn("reasons", rec)
        self.assertNotIn("off_until", rec)
        pins = json.load(open(paths.pins_json()))
        mine = [d for g in pins["groups"].values() for d, x in g["pins"].items() if x["exit"] == eid]
        self.assertEqual(mine, [])
        self.assertIn("d-00000002", pins["groups"]["main"]["pins"])    # another exit's pin stays
        t = PN.traffic_read()["exits"][eid]
        self.assertEqual((t["period_bytes"], t["last"]), (777, {"rx": 0, "tx": 0}))

    def test_the_same_peer_never_in_both_files_after_a_move(self):
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        self.move(eid, HOME)
        rows = exits.pool(self.w.runner, roles=exits.ROLES)
        self.assertEqual([(e["id"], e["role"]) for e in rows], [(eid, "ru")])
        self.assertFalse(os.path.exists(exitsubs.pending_path()))     # a move is no removal by hand


class PlanPortsTest(RuCase):
    def test_the_probe_of_a_russian_xray_exit_is_guarded(self):
        """The plan of the kernel (``exits.current`` — both files): the probe port of a Russian VLESS exit (12700+202)
        is in gw_xports — no local program reaches a home router through it — and its TPROXY port is in the plan, as in
        the config the collector draws (one view, no flip-flop of xray)."""
        from kaskad.gw import net
        write_pool([VLESS_A])
        write_ru_pool([key("Дом", uuid="dddddddd-1111-2222-3333-444444444444"), ROUTER])
        st = state.load()
        p = net.plan(st, self.w.runner)
        self.assertIn(12902, p["xray_ports"])
        self.assertIn((0x300 + 202, 12602), p["vless"])
        rows = exits.current(st, self.w.runner)[0]
        self.assertEqual(sorted(e["index"] for e in rows), [1, 201, 202])
        for t in exits.targets(st, exits.decision(st, pool_exits=rows)).values():
            self.assertNotIn(t["mark"], (457, 458, 0x300 + 201, 0x300 + 202))      # no device on a Russian leg
