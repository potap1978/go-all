"""T-3379 Н-3, М-1, М-2 (шаг Н-3, 27.10): a repeated key of the pool («e-<hex>-201» — the same line in both files while
``ops.exit_role_move`` moves it) is no exit of its own: never placed in a group (``policy.place_new``), one stored by
3.3.7.6–3.3.7.10 is never read and goes out of the stored policy (``policy.load``, ``prune_gone``), no point of the
charts (``collect``, ``metrics``). ``gw exits`` shows the Russian exits (М-1); an exit event of the feed carries the
name of its exit (М-2). Each test fails on 44cee282 (3.3.7.10)."""

import io
import json
import sys
import time
from unittest import mock

from kaskad import cli, state
from kaskad.gw import collect, exits, live, metrics, policy, policy_ops
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool, write_ru_pool
from tests.gw.test_s1_ru_pool_ops import HOME, RuCase
from kaskad.i18n import use_lang


def _all_ids(doc):
    return [e for g in doc.get("groups") or [] for t in g.get("tiers") or [] for e in t.get("exits") or []]


def _stored():
    return state.load()["gateway"]["exit_policy"]


class RoleMoveWithCollectorTest(RuCase):
    """Н-3 as on the stand: the collector places the pool while the line is in both files (between the two writes of
    ``exit_role_move``) — on 3.3.7.10 it put «e-…-201» into «Дом» and the move ended «Wrong exit id»."""

    def setUp(self):
        super().setUp()
        self.ticks = 0
        real = self.w.script.egress_ru_apply

        def ru_apply(want, text):
            res = real(want, text)
            self.tick()                         # gw apply / the tick of the collector right after the file changed
            return res
        self.w.script.egress_ru_apply = ru_apply

    def tick(self):
        """The placement of a tick of the collector (``collect._place``) on the files as they are now."""
        self.ticks += 1
        with state.locked():
            st = state.load()
            collect._place(st, exits.pool(self.w.runner, roles=exits.ROLES))

    def test_move_into_a_russian_group_keeps_the_id_and_no_repeat_is_placed(self):
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        self.tick()
        self.assertEqual(self.group_of(eid), policy.MAIN)
        res = self.move(eid, HOME)                              # 3.3.7.10: «Wrong exit id: 'e-…-201'»
        self.assertGreaterEqual(self.ticks, 2)                  # the collector did see the line in both files
        self.assertEqual(res["role"]["to"], "ru")
        self.assertEqual(self.ru(), [VLESS_A])
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(self.ru_ids(), [eid])
        self.assertEqual(self.group_of(eid), HOME)
        self.assertEqual([i for i in _all_ids(_stored()) if policy.repeat_id(i)], [])
        self.tick()
        self.assertEqual([i for i in _all_ids(_stored()) if policy.repeat_id(i)], [])
        # and back abroad: the same id, no repeat in «Основная»
        self.move(eid, policy.MAIN)
        self.assertEqual(self.ru(), [])
        self.assertEqual(self.group_of(eid), policy.MAIN)
        self.assertEqual([i for i in _all_ids(_stored()) if policy.repeat_id(i)], [])


class PlaceNewTest(RuCase):
    def test_a_repeated_key_is_placed_nowhere(self):
        """A pool with the same line in both files (and twice in upstream.txt): the repeats are no exits of their own."""
        doc = policy.default_doc("standard")
        doc["groups"].append(policy.default_group(HOME, "ru", name="Дом"))
        a = exits.exit_id(VLESS_A)
        pool = [{"id": a, "role": "abroad"}, {"id": a + "-2", "role": "abroad", "dup_of": a},
                {"id": a + "-201", "role": "ru", "dup_of": a}]
        placed = policy.place_new(doc, pool)
        self.assertEqual(placed, [(a, policy.MAIN, "t1")])
        self.assertEqual(_all_ids(doc), [a])
        # the ids alone (``rev.pool_ids``, ``tgproxy``): the same
        doc2 = policy.default_doc("standard")
        doc2["groups"].append(policy.default_group(HOME, "ru", name="Дом"))
        self.assertEqual(policy.place_new(doc2, {"abroad": [a, a + "-2"], "ru": [a + "-201"]}), [(a, policy.MAIN, "t1")])
        doc3 = policy.default_doc("standard")
        self.assertEqual(policy.place_new(doc3, [a, a + "-2"]), [(a, policy.MAIN, "t1")])


class MigrationTest(RuCase):
    """3.3.7.10 → this version: «Дом» stored with «e-…-201» (the same key put into upstream-ru.txt by hand through the
    SSH menu: the repeat stays in the files, so ``known_ids`` holds it and 3.3.7.10 never pruned it — every write of
    the policy was refused «Wrong exit id»)."""

    def old_data(self):
        write_pool([VLESS_A, VLESS_B])
        write_ru_pool([VLESS_A])
        a, b = exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)
        with state.transaction() as st:
            doc = st["gateway"]["exit_policy"]
            main = policy.group(doc, policy.MAIN)
            main["tiers"][0]["exits"] = [a, b]
            home = policy.group(doc, HOME)
            home["tiers"][0]["exits"] = [a + "-201"]
            home["tiers"][0]["weights"] = {a + "-201": 3}
        self.assertIn(a + "-201", [i for i, _ in exits.line_ids(role="ru", taken=[a])])
        return a, b

    def test_the_repeat_is_never_read_and_goes_from_the_stored_policy(self):
        a, b = self.old_data()
        before = json.dumps(_stored(), sort_keys=True)
        self.assertNotIn(a + "-201", _all_ids(policy.load(state.load())))
        self.assertEqual(self.group_of(a), policy.MAIN)
        # a write of the policy goes (3.3.7.10: «Wrong exit id: 'e-…-201'»)
        res = policy_ops.exit_move(self.w.runner, b, policy.MAIN)
        self.assertTrue(res["ok"])
        stored = _stored()
        self.assertEqual([i for i in _all_ids(stored) if policy.repeat_id(i)], [])
        self.assertNotIn(a + "-201", json.dumps(stored))        # its weight too
        self.assertEqual(policy.group(stored, policy.MAIN)["tiers"][0]["exits"][0], a)
        self.assertNotEqual(before, json.dumps(stored, sort_keys=True))

    def test_the_collector_takes_it_out_and_never_puts_it_back(self):
        a, _b = self.old_data()
        with state.locked():
            st = state.load()
            got = collect._place(st, exits.pool(self.w.runner, roles=exits.ROLES))
        self.assertIn((a + "-201", HOME), got)
        stored = _stored()
        self.assertEqual([i for i in _all_ids(stored) if policy.repeat_id(i)], [])
        self.assertEqual(policy.group(stored, HOME)["tiers"][0].get("weights") or {}, {})
        with state.locked():
            st = state.load()
            self.assertEqual(collect._place(st, exits.pool(self.w.runner, roles=exits.ROLES)), [])
        self.assertEqual([i for i in _all_ids(_stored()) if policy.repeat_id(i)], [])

    def test_a_node_without_a_russian_group_keeps_its_policy(self):
        """No repeat stored, no Russian group: the stored policy and the view are the same as before."""
        with state.transaction() as st:
            st["gateway"]["exit_policy"] = policy.default_doc("standard")
        write_pool([VLESS_A, VLESS_B])
        with state.locked():
            st = state.load()
            collect._place(st, exits.pool(self.w.runner, roles=exits.ROLES))
        stored = json.dumps(_stored(), sort_keys=True)
        with state.locked():
            st = state.load()
            self.assertEqual(collect._place(st, exits.pool(self.w.runner, roles=exits.ROLES)), [])
        self.assertEqual(json.dumps(_stored(), sort_keys=True), stored)
        self.assertEqual(_all_ids(policy.load(state.load())), [exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)])


class ChartsTest(RuCase):
    """No point of the charts for a repeated key; the ones 3.3.7.6–3.3.7.10 wrote are not shown."""

    def setUp(self):
        super().setUp()
        collect.MEM.reset()
        self.addCleanup(collect.MEM.reset)

    def test_no_point_for_a_repeat(self):
        a = exits.exit_id(VLESS_A)
        pool = [{"id": a, "role": "abroad", "kind": "awg"},
                {"id": a + "-201", "role": "ru", "kind": "bad", "dup_of": a}]
        collect._samples(pool, {a: {"alive": True, "rtt": 12.0}, a + "-201": {"alive": False, "rtt": None}})
        now = time.time()
        collect._minute(None, state.load(), {"probe_counters": {}}, pool, {"states": {a: "ok", a + "-201": "off"}}, now)
        w = collect._writer()
        self.assertTrue(w)
        got = sorted(r[0] for r in w.conn.execute("SELECT DISTINCT exit FROM m1").fetchall())
        self.assertEqual(got, [a])

    def test_old_points_of_a_repeat_are_not_shown(self):
        a = exits.exit_id(VLESS_A)
        w = metrics.open_writer()
        self.assertIsNotNone(w)
        now = int(time.time()) // 60 * 60
        pt = {"rtt_med": 10.0, "rtt_p90": 12.0, "loss": 0.0, "jitter": 1.0, "ok": 5, "n": 5, "rx": 100, "tx": 100,
              "devices": 1, "state": "ok"}
        bad = dict(pt, rtt_med=None, rtt_p90=None, loss=1.0, ok=0, n=1, rx=None, tx=None, devices=0, state="off")
        w.write_minute(now - 120, {a: pt, a + "-201": bad})
        w.close()
        s = metrics.exit_summary(now)
        self.assertIn(a, s["exits"])
        self.assertNotIn(a + "-201", s["exits"])
        h = metrics.history(a + "-201", "1d", now)
        self.assertTrue(h["charts"])
        self.assertEqual((h["points"], h["states"]), ([], []))
        self.assertTrue(metrics.history(a, "1d", now)["points"])


class ExitsCliTest(RuCase):
    """М-1: ``gw exits`` shows the Russian exits — apart: ``ru_exits`` of ``--json`` (MCP exits_list) and their own
    line in the text; ``exits`` stays the foreign pool (the SSH menu builds routes «только через выход», on/off and the
    wizard from it — a Russian exit there would be offered as a target of a foreign route)."""

    def test_russian_exits_are_listed_apart(self):
        write_pool([VLESS_A])
        write_ru_pool([VLESS_B])
        a, b = exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["gw", "exits", "--json"]), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual([(r["id"], r["role"]) for r in doc["exits"]], [(a, "abroad")])
        self.assertEqual([(r["id"], r["role"], r["index"]) for r in doc["ru_exits"]], [(b, "ru", 201)])
        self.assertNotIn("line", doc["ru_exits"][0])                   # public rows: no key
        self.assertNotIn(VLESS_B.split("@")[0], out.getvalue())
        for lang, head in (("ru", "Внутренние выходы"), ("en", "Domestic exits")):
            out = io.StringIO()
            with use_lang(lang), mock.patch.object(sys, "stdout", out):
                self.assertEqual(cli.main(["gw", "exits"]), 0)
            lines = out.getvalue().splitlines()
            at = [i for i, ln in enumerate(lines) if ln.startswith(head)]
            self.assertEqual(len(at), 1, out.getvalue())
            self.assertTrue(lines[at[0]].endswith(": 1"), lines[at[0]])
            self.assertTrue(lines[at[0] + 1].startswith(b + " 201 "), out.getvalue())
            self.assertLess([i for i, ln in enumerate(lines) if ln.startswith(a)][0], at[0])

    def test_no_russian_exit_nothing_new(self):
        write_pool([VLESS_A])
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["gw", "exits", "--json"]), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["ru_exits"], [])
        self.assertEqual([r["id"] for r in doc["exits"]], [exits.exit_id(VLESS_A)])
        out = io.StringIO()
        with use_lang("en"), mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["gw", "exits"]), 0)
        self.assertNotIn("Domestic exits", out.getvalue())


class CountryEventNameTest(RuCase):
    """М-2: «Russian exit “”» — exit_country_changed (and the other exit events outside the windows) had no name."""

    def test_the_name_of_the_exit_is_in_the_event(self):
        eid = "e-0000000000a1"
        names, gl = {eid: "Роутер"}, {HOME: "Дом"}
        for role, word in (("ru", "Роутер"), ("abroad", "Роутер")):
            lv = live.Live()
            lv.publish([dict(kind="exit_country_changed", exit=eid, group=HOME if role == "ru" else "main", role=role,
                             old="RU", new="NL", all_exits=False)], names, dict(gl, main="Основная"), {}, time.time())
            ev = [e for e in lv.events if e.get("kind") == "exit_country_changed"]
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["args"].get("name"), "Роутер")
            with use_lang("en"):
                text = live.event_text(ev[0])
            self.assertIn("“%s”" % word, text)
            self.assertNotIn("“”", text)
