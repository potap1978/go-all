"""T15 Q3 (SPEC-groups §6.4, SE §4.6, §13.1 «collect.py»): the network of the node and the Russian groups.

* the direct probe — when EVERY exit of one Russian group is silent (another group answering does not hide it);
* the table of SE §4.6 row by row, both sides independently, a node without foreign exits by its Russian side;
* ``node.ru_offline``: the event ``node_ru_offline`` once, the Russian groups frozen (no ``group_last_step``, no moves),
  the first good direct Russian probe — ``node_ru_online`` (since, until, minutes) and the windows of the Russian exits
  anew; a whole outage of the node keeps the Russian one open; a restarted collector takes the open one from live.json;
* the feed: a closed ``node_ru_offline`` is no line of its own, an open one is never pruned.
"""

import json
from unittest import mock

from kaskad import paths, state, util
from kaskad.gw import collect, exits, live
from kaskad.gw import policy as P
from tests.gw.test_ru_target import HOME, RuWorld

OFFICE = "g-0d0e0f"


def direct(ru, abroad):
    """``exits.direct_probe`` of a node: the Russian target answers ``ru``, the foreign one ``abroad``."""
    calls = []

    def probe(targets, *a, **kw):
        calls.append(list(targets))
        return ru if list(targets) == list(exits.probe_targets("ru")["direct"]) else abroad
    return probe, calls


class MemCase(RuWorld):
    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()


class NodeCheckRuTest(MemCase):
    def two_groups(self):
        """«Дом» — the AmneziaWG «Роутер», «Офис» — the VLESS key."""
        st = self.world()
        doc = st["gateway"]["exit_policy"]
        awg = [r["id"] for r in self.ru_rows if r["kind"] == "awg"][0]
        vl = [r["id"] for r in self.ru_rows if exits.is_xray(r)][0]
        P.group(doc, HOME)["tiers"][0]["exits"] = [awg]
        office = P.default_group(OFFICE, "ru", "ru_home", name="Офис", exits=[vl])
        office["fail"] = "block"
        doc["groups"].append(office)
        state.save(st)
        st = state.load()
        return st, awg, vl

    def check(self, pool, probed, pol, ru=True, abroad=True):
        fn, calls = direct(ru, abroad)
        with mock.patch.object(exits, "direct_probe", side_effect=fn):
            out = collect.node_check(pool, probed, pol, {"only": set(probed), "idle": set(), "off": set(),
                                                         "socks": dict((i, (1, [], "ya.ru")) for i in probed)})
        return out, calls

    def test_one_silent_russian_group_asks_the_node(self):
        """SE §4.6: «на узле есть российская группа, и ВСЕ её выходы не ответили» — «Дом» silent, «Офис» answers:
        the direct probe is made (before Q3 the Russian side as a whole answered and nobody asked)."""
        st, awg, vl = self.two_groups()
        pool = self.pool()
        pol = P.load(st, pool)
        out, calls = self.check(pool, {awg: {"alive": False}, vl: {"alive": True}}, pol)
        self.assertTrue(out["checked"])
        self.assertEqual(len(calls), 2)
        self.assertEqual((out["offline"], out["ru_offline"]), (False, False))   # both answer: the routers of «Дом»
        out, calls = self.check(pool, {awg: {"alive": True}, vl: {"alive": True}}, pol)
        self.assertEqual((out["checked"], calls), (False, []))                   # everything answers: nothing asked

    def test_the_table_of_the_direct_probe(self):
        """SE §4.6, the four rows (the Russian and the foreign side independently)."""
        st, awg, vl = self.two_groups()
        pool = self.pool()
        pol = P.load(st, pool)
        silent = {awg: {"alive": False}, vl: {"alive": False}}
        rows = [((False, False), (True, False)),      # both silent — the node offline, not «ru_offline»
                ((False, True), (False, True)),       # the Russian silent, the foreign answers — ru_offline
                ((True, False), (False, False)),      # the foreign silent — the usual machine
                ((True, True), (False, False))]       # both answer — the exits themselves
        for (ru, ab), want in rows:
            with self.subTest(ru=ru, abroad=ab):
                out, _ = self.check(pool, silent, pol, ru=ru, abroad=ab)
                self.assertTrue(out["checked"])
                self.assertEqual((out["offline"], out["ru_offline"]), want)
                self.assertEqual(out["direct"], {"abroad": ab, "ru": ru})

    def test_a_node_without_foreign_exits_by_its_russian_side(self):
        """SE §4.6: a node with no foreign exit checks its network by the Russian side only."""
        st, awg, vl = self.two_groups()
        pool = [r for r in self.pool() if r["role"] == "ru"]
        pol = P.load(st, pool)
        out, calls = self.check(pool, {awg: {"alive": False}, vl: {"alive": False}}, pol, ru=False, abroad=True)
        self.assertTrue(out["ru_offline"] and calls)


class RuOutageTest(MemCase):
    def node(self, ru_offline, checked=True):
        return {"checked": checked, "offline": False, "ru_offline": ru_offline}

    def test_the_outage_and_its_summary(self):
        out = {"events": []}
        collect._ru_node(self.node(True), 1000.0, out)
        self.assertEqual(out["events"], [{"kind": "node_ru_offline", "since": 1000}])
        self.assertTrue(collect.MEM.node["ru_offline"])
        out = {"events": []}
        collect._ru_node(self.node(True), 1010.0, out)                     # goes on: no second start
        self.assertEqual(out["events"], [])
        out = {"events": []}
        collect._ru_node(self.node(False), 1430.0, out)
        self.assertEqual(out["events"], [{"kind": "node_ru_online", "since": 1000, "until": 1430, "minutes": 7}])
        self.assertTrue(out["ru_back"])
        self.assertFalse(collect.MEM.node["ru_offline"])
        out = {"events": []}
        collect._ru_node(self.node(False, checked=False), 1440.0, out)     # nothing silent, nothing asked
        self.assertEqual(out["events"], [])

    def test_a_whole_outage_keeps_the_russian_one(self):
        """The node lost its whole network inside a Russian outage: nothing told of the Russian side then (frozen
        with the rest); its end — when the Russian addresses answer again, ``since`` the first silence."""
        collect._ru_node(self.node(True), 1000.0, {"events": []})
        collect.MEM.node["offline"] = True
        out = {"events": []}
        collect._ru_node({"checked": True, "offline": True, "ru_offline": False}, 1100.0, out)
        self.assertEqual(out["events"], [])
        self.assertTrue(collect.MEM.node["ru_offline"])
        collect.MEM.node["offline"] = False
        out = {"events": []}
        collect._ru_node(self.node(False), 1300.0, out)
        self.assertEqual([(e["kind"], e["since"], e["until"]) for e in out["events"]], [("node_ru_online", 1000, 1300)])

    def test_a_restarted_collector_takes_the_open_outage(self):
        """REV-s1-t4 З1 for the Russian side: a ``node_ru_offline`` left open in live.json — its ``since`` stays, the
        end is told with ``restart`` (the last round of that process: it may have come back meanwhile)."""
        doc = {"v": live.V, "ts": 1200, "events": [
            {"id": 1, "ts": 1000, "kind": "node_ru_offline", "key": "gw_live.event.node_ru_offline",
             "args": {"since": 990}}]}
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)
        self.assertEqual(live.ru_outage_open(), {"since": 990, "last": 1200})
        collect._restore_ru_node()
        self.assertTrue(collect.MEM.node["ru_offline"])
        out = {"events": []}
        collect._ru_node(self.node(False), 5000.0, out)
        ev = out["events"][0]
        self.assertEqual((ev["kind"], ev["since"], ev["until"], ev.get("restart")), ("node_ru_online", 990, 1200, True))
        doc["events"].append({"id": 2, "ts": 1300, "kind": "node_ru_online", "args": {}})
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)
        self.assertIsNone(live.ru_outage_open())                            # closed: nothing to take


class RuTickTest(MemCase):
    """The tick (``exits_tick``) with the network of the node measured before it (``probe_round`` → ``node``)."""

    def pr(self, alive, node):
        pool = self.pool()
        probed = dict((e["id"], {"alive": alive(e), "detail": "", "handshake": None}) for e in pool)
        return {"pool": pool, "probed": probed, "legs": None, "plan": {}, "node": node, "counters": {}}

    def tick(self, st, alive, node):
        self.now += 15
        with mock.patch.object(collect, "_now", lambda: self.now), mock.patch.object(collect, "_mono", lambda: self.now):
            return collect.exits_tick(st, self.w.runner, probed=self.pr(alive, node))

    def test_frozen_while_the_node_cannot_reach_russia(self):
        """§6.4, SE §4.6: the routers of «Дом» silent and the Russian target silent from the node itself — no
        ``group_last_step`` of «Дом», no device moved, the Russian exits keep their state; back — ``node_ru_online``
        and the windows of the Russian exits anew (the foreign ones keep theirs)."""
        self.now = 100000.0
        st = self.world(fail="direct")
        ok = {"checked": False, "offline": False, "ru_offline": False}
        for _ in range(4):
            self.tick(st, lambda e: True, ok)
        ru = set(self.ru_ids)
        off = {"checked": True, "offline": False, "ru_offline": True}
        events = []
        for _ in range(12):
            res = self.tick(st, lambda e: e["role"] != "ru", off)
            events += res["events"]
            self.assertTrue(res["node_ru_offline"])                             # Q6: live.policy.node_ru_offline
            self.assertFalse([m for m in res.get("moves") or [] if m.get("group") == HOME])
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds.count("node_ru_offline"), 1)
        self.assertNotIn("group_last_step", kinds)
        self.assertFalse([e for e in events if e.get("exit") in ru])           # frozen: no event of a Russian exit
        self.assertEqual(set(collect.MEM.health[i]["state"] for i in ru), {"ok"})
        tries_ab = dict((i, list(h.get("tries") or [])) for i, h in collect.MEM.health.items() if i not in ru)
        self.assertTrue(any(tries_ab.values()))
        res = self.tick(st, lambda e: True, {"checked": True, "offline": False, "ru_offline": False})
        self.assertFalse(res["node_ru_offline"])
        on = [e for e in res["events"] if e["kind"] == "node_ru_online"]
        self.assertEqual(len(on), 1)
        self.assertEqual(on[0]["minutes"], 3)
        for i in ru:
            self.assertLessEqual(len(collect.MEM.health[i]["tries"]), 1)        # anew (this tick's probe at most)
        kept = [i for i, before in tries_ab.items() if len(before) > 1]         # the foreign windows go on
        self.assertTrue(kept)
        for i in kept:
            self.assertGreater(len(collect.MEM.health[i]["tries"]), 1, i)


class FeedTest(MemCase):
    def test_a_closed_russian_outage_is_its_summary(self):
        evs = [{"kind": "exit_dead", "id": 1}, {"kind": "node_ru_offline", "id": 2}, {"kind": "exit_alive", "id": 3},
               {"kind": "node_ru_online", "id": 4}, {"kind": "node_ru_offline", "id": 5}]
        self.assertEqual([e["id"] for e in live.feed_events(evs)], [1, 3, 4, 5])
        both = [{"kind": "node_offline", "id": 1}, {"kind": "node_ru_offline", "id": 2}, {"kind": "node_online", "id": 3},
                {"kind": "node_ru_online", "id": 4}]
        self.assertEqual([e["id"] for e in live.feed_events(both)], [3, 4])

    def test_the_layer_writes_one_start_and_a_summary_from_it(self):
        lv = live.Live()
        lv.publish([{"kind": "node_ru_offline", "since": 1000}], {}, {}, {}, 1000)
        lv.publish([{"kind": "node_ru_offline", "since": 1300}], {}, {}, {}, 1300)   # a restarted collector
        lv.publish([{"kind": "node_ru_online", "since": 1300, "until": 1600, "minutes": 5}], {}, {}, {}, 1600)
        kinds = [(e["kind"], e["args"].get("since"), e["args"].get("minutes")) for e in lv.events]
        self.assertEqual(kinds, [("node_ru_offline", 1000, None), ("node_ru_online", 1000, 10)])

    def test_the_open_start_is_never_pruned(self):
        lv = live.Live()
        lv.publish([{"kind": "node_ru_offline", "since": 1000}], {}, {}, {}, 1000)
        for i in range(live.EVENTS_MAX + 5):
            lv._event("exit_alive", "gw_live.event.exit_alive", {"name": "x"}, 2000 + i)
        lv._prune(2000 + live.EVENTS_MAX + 10)
        self.assertEqual(len(lv.events), live.EVENTS_MAX)
        self.assertIn("node_ru_offline", [e["kind"] for e in lv.events])


class LiveGroupsTest(MemCase):
    """T15 Q6 (SPEC-groups §8.9): ``live.groups`` — the step of every group from the decision of the tick, which the
    panel paints («сейчас: через …» / «с сервера напрямую — выходы недоступны с 12:03»)."""

    def test_the_step_of_each_group(self):
        dec = {"groups": {"main": {"active_tier": "t1", "last_step": None, "last_since": None, "steps": {}},
                          HOME: {"active_tier": None, "last_step": "direct", "last_since": 1000.7,
                                 "steps": {"d-1": "last_direct"}},
                          "g-0ff1ce": {"active_tier": None, "last_step": "block", "last_since": None}}}
        self.assertEqual(live.groups_now(dec), {
            "main": {"active_tier": "t1", "last_step": None, "since": None},
            HOME: {"active_tier": None, "last_step": "direct", "since": 1000},
            "g-0ff1ce": {"active_tier": None, "last_step": "block", "since": None}})
        self.assertEqual(live.groups_now(None), {})
        self.assertEqual(live.groups_now({"groups": {"main": {"last_step": "weird", "last_since": 5}}}),
                         {"main": {"active_tier": None, "last_step": None, "since": None}})
