"""Круг 1, Т2, решение владельца 8а: «Удалить группу» с ``exits=drop`` — последняя российская группа уходит вместе со
своими ключами одним действием (policy_ops.group_delete -> _group_drop; DELETE /api/gw/groups/<gid>?exits=drop).
Без ``exits=drop`` — как было (409 ``ru_last``, старые клиенты). Каждый тест, кроме «как было», падает на коде до 8а:
там ``exits`` не принимается вовсе."""

import json
import os
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError, LockedError
from kaskad.gw import exits, exitsubs, ops, policy, policy_ops
from tests.fakes_gw import write_ru_pool
from tests.gw.test_s1_ru_pool_ops import HOME, ROUTER, RuCase, ru_key

DACHA = ru_key("Dacha", uuid="eeeeeeee-1111-2222-3333-444444444444", host="203.0.113.62")
OFFICE = "g-0f0f0f"


class DropCase(RuCase):
    def setUp(self):
        super().setUp()
        ops.exit_add(self.w.runner, ROUTER, group=HOME)
        ops.exit_add(self.w.runner, DACHA, group=HOME)
        self.keys = [exits.exit_id(ROUTER), exits.exit_id(DACHA)]
        with state.transaction() as st:                      # «Мама» chose «Дом» for her Russian sites
            st["gateway"]["users"][0]["ru_via"] = HOME
        self.uid = state.load()["gateway"]["users"][0]["id"]

    def rev(self):
        return policy_ops.current_rev()

    def drop(self, **kw):
        kw.setdefault("want_rev", self.rev())
        return policy_ops.group_delete(self.w.runner, HOME, exits="drop", **kw)

    def refused(self, **kw):
        with self.assertRaises(KaskadError) as c:
            self.drop(**kw)
        return c.exception

    def groups(self):
        return [g["id"] for g in policy.load(state.load())["groups"]]

    def ru_via(self):
        return state.load()["gateway"]["users"][0].get("ru_via")

    def untouched(self):
        self.assertEqual(self.ru(), [ROUTER, DACHA])
        self.assertIn(HOME, self.groups())
        self.assertEqual(self.ru_via(), HOME)


class AsBeforeTest(DropCase):
    def test_the_last_russian_group_without_drop_is_ru_last(self):
        """Старый клиент: последняя российская группа с ключами — 409 ``ru_last``, ничего не тронуто."""
        with self.assertRaises(KaskadError) as c:
            policy_ops.group_delete(self.w.runner, HOME, reassign="direct", want_rev=self.rev())
        self.assertEqual((c.exception.key, c.exception.status), ("ru_last", 409))
        self.assertEqual(sorted(c.exception.data["exits"]), sorted(self.keys))
        self.untouched()


class DropTest(DropCase):
    def test_dry_lists_the_keys_and_writes_nothing(self):
        calls = len(self.w.script.calls)
        res = self.drop(reassign="direct", dry=True)
        self.assertEqual((res["ok"], res["dry"]), (True, True))
        self.assertEqual(sorted(res["drop"]), sorted(self.keys))
        self.assertEqual(res["refs"]["users"], [self.uid])
        self.assertEqual(res["rev"], self.rev())
        self.assertIn("ru", res["moves"])
        self.assertEqual(len(self.w.script.calls), calls)            # no script
        self.untouched()

    def test_the_last_group_goes_with_its_keys(self):
        exitsubs.save(exitsubs.load())                                # a node with subscriptions: no comeback
        rev = self.rev()
        res = self.drop(reassign="direct", want_rev=rev)
        self.assertEqual(sorted(res["dropped"]), sorted(self.keys))
        self.assertEqual(self.ru(), [])
        self.assertNotIn(HOME, self.groups())
        self.assertEqual(self.ru_via(), "direct")                    # her Russian sites — from the server directly
        self.assertGreater(res["rev"], rev)
        self.assertEqual(res["rev"], self.rev())
        self.assertIn("moves", res)
        with open(exitsubs.pending_path()) as f:
            self.assertEqual(sorted(f.read().split()), sorted(self.keys))
        self.assertNotIn("--egress-del", self.calls())                 # its own file: the bot untouched
        self.assertEqual(self.calls().count("--egress-ru-apply"), 3)   # two adds + ONE write for both keys
        log = self.journal()
        self.assertIn("GW group drop: keys out (ru): ", log)
        self.assertIn("GW policy: group del %s with 2 keys" % HOME, log)
        self.assertNotIn("203.0.113.6", log)                           # ids only, never a key
        recs = state.load()["gateway"]["exits"]
        self.assertFalse(any(isinstance(r, dict) and r.get("group") == HOME for r in recs.values()))
        e = self.refused(reassign="direct")                            # the same DELETE again — 404 gone
        self.assertEqual((e.key, e.status), ("gone", 404))

    def test_people_need_reassign_nothing_dropped(self):
        e = self.refused()
        self.assertEqual((e.key, e.status), ("need", 409))
        self.assertEqual(e.data["refs"]["users"], [self.uid])
        self.assertNotIn("exits", e.data)                               # a drop needs no exits_to
        self.untouched()

    def test_a_repeat_of_a_key_goes_too(self):
        """Тот же ключ дважды в upstream-ru.txt (повтор «e-…-20N» не в группе): уходят обе строки — повтор не занимает
        место ключа после удаления."""
        write_ru_pool([ROUTER, DACHA, ROUTER])
        res = self.drop(reassign="direct")
        self.assertEqual(sorted(res["dropped"]), sorted(self.keys))
        self.assertEqual(self.ru(), [])

    def test_a_key_of_another_group_stays(self):
        with state.transaction() as st:
            doc = st["gateway"]["exit_policy"]
            doc["groups"].append(policy.default_group(OFFICE, "ru", name="Офис"))
        office_key = ru_key("Office", uuid="cccccccc-1111-2222-3333-444444444444", host="203.0.113.63")
        ops.exit_add(self.w.runner, office_key, group=OFFICE)
        res = self.drop(reassign=OFFICE)                                # not the last: to another Russian group
        self.assertEqual(sorted(res["dropped"]), sorted(self.keys))
        self.assertEqual(self.ru(), [office_key])
        self.assertEqual(self.ru_via(), OFFICE)
        self.assertEqual(self.groups().count(OFFICE), 1)


class RefusalTest(DropCase):
    def test_foreign_input(self):
        for kw in ({"exits": "keep"}, {"exits": "drop", "exits_to": "main"}, {"exits": "DROP"}):
            with self.subTest(kw):
                with self.assertRaises(KaskadError) as c:
                    policy_ops.group_delete(self.w.runner, HOME, reassign="direct", want_rev=self.rev(), **kw)
                self.assertEqual((c.exception.key, c.exception.status, c.exception.data["field"]),
                                 ("field", 400, "exits"))
        self.untouched()

    def test_a_foreign_group(self):
        gid = policy_ops.group_add(self.w.runner, "Стриминг", want_rev=self.rev())["id"]
        with self.assertRaises(KaskadError) as c:
            policy_ops.group_delete(self.w.runner, gid, exits="drop", want_rev=self.rev())
        self.assertEqual((c.exception.key, c.exception.status, c.exception.data["field"]), ("field", 400, "exits"))
        self.assertIn(gid, self.groups())

    def test_main_and_a_gone_group(self):
        with self.assertRaises(KaskadError) as c:
            policy_ops.group_delete(self.w.runner, "main", exits="drop", want_rev=self.rev())
        self.assertEqual((c.exception.key, c.exception.status), ("main", 400))
        with self.assertRaises(KaskadError) as c:
            policy_ops.group_delete(self.w.runner, "g-abcdef", exits="drop", want_rev=self.rev())
        self.assertEqual((c.exception.key, c.exception.status), ("gone", 404))

    def test_a_stale_rev_drops_nothing(self):
        e = self.refused(reassign="direct", want_rev=self.rev() - 1)
        self.assertEqual((e.key, e.status), ("policy_changed", 409))
        self.untouched()

    def test_a_subscription_of_the_group_is_never_moved_nor_deleted(self):
        exitsubs.save({"schema": 1, "next": 2, "subs": [{"id": "s-0a0b0c", "n": 1, "url": "https://example.com/s",
                                                           "group": HOME, "role": "ru", "keys": [], "auto": True}]})
        for kw in ({}, {"subs_to": OFFICE}):
            with self.subTest(kw):
                e = self.refused(reassign="direct", **kw)
                self.assertEqual((e.key, e.status, e.data["uses"]), ("need", 409, ["subs"]))
        self.untouched()
        self.assertEqual(exitsubs.load()["subs"][0]["group"], HOME)


class BrokenOffTest(DropCase):
    def test_the_pool_write_fails_nothing_lost(self):
        self.w.script.ru_rc = 1
        e = self.refused(reassign="direct")
        self.assertNotIn("dropped", getattr(e, "data", None) or {})
        self.untouched()
        self.w.script.ru_rc = None                                     # the repeat finishes it
        self.assertEqual(sorted(self.drop(reassign="direct")["dropped"]), sorted(self.keys))
        self.assertNotIn(HOME, self.groups())

    def test_the_pool_busy_three_times(self):
        self.w.script.ru_rc = 3                                        # «пул изменился» every time
        with self.assertRaises(KaskadError):
            self.drop(reassign="direct")
        self.untouched()

    def test_cut_after_the_write_is_dropped(self):
        """Скрипт записал файл и оборвался (таймаут): строк нет — ключи ушли, ошибка — предупреждение, группа удалена."""
        real = exitsubs.write_pool

        def cut(runner, role, cur, pool, only=None):
            real(runner, role, cur, pool, only)
            raise KaskadError("оборвано")
        with mock.patch.object(exitsubs, "write_pool", cut):
            res = self.drop(reassign="direct")
        self.assertEqual(sorted(res["dropped"]), sorted(self.keys))
        self.assertIn("оборвано", res["warnings"])
        self.assertNotIn(HOME, self.groups())

    def test_step_three_fails_the_repeat_finishes(self):
        """Ключи удалены, запись политики не прошла (занято): группа осталась пустой, в ошибке ``dropped`` и ``rev``;
        тот же DELETE с этим ``rev`` доводит дело, потом — 404 gone."""
        real = policy_ops.run

        def busy(*a, **kw):
            raise LockedError("занято")
        with mock.patch.object(policy_ops, "run", busy):
            e = self.refused(reassign="direct")
        self.assertEqual(sorted(e.data["dropped"]), sorted(self.keys))
        self.assertEqual(e.data["rev"], self.rev())
        self.assertEqual(self.ru(), [])
        self.assertIn(HOME, self.groups())
        self.assertEqual(policy.exits_of(policy.group(policy.load(state.load()), HOME)), [])   # pruned at once
        self.assertIn("broke off, its keys are gone", self.journal())
        self.assertIs(policy_ops.run, real)
        res = self.drop(reassign="direct", want_rev=e.data["rev"])
        self.assertEqual(res["dropped"], [])
        self.assertNotIn(HOME, self.groups())
        self.assertEqual(self.ru_via(), "direct")
        self.assertEqual(self.refused(reassign="direct").key, "gone")

    def test_a_key_the_group_got_meanwhile(self):
        """Между шагами в группе появился ключ (его не было в плане): 409 ``drop_more``, группа на месте, повтор
        удаляет и его."""
        late = ru_key("Late", uuid="bbbbbbbb-1111-2222-3333-444444444444", host="203.0.113.64")
        real = policy_ops._drop_ru_keys

        def and_one_more(runner, ids):
            out = real(runner, ids)
            ops.exit_add(runner, late, group=HOME)
            return out
        with mock.patch.object(policy_ops, "_drop_ru_keys", and_one_more):
            e = self.refused(reassign="direct")
        self.assertEqual((e.key, e.status), ("drop_more", 409))
        self.assertEqual(e.data["exits"], [exits.exit_id(late)])
        self.assertEqual(sorted(e.data["dropped"]), sorted(self.keys))
        self.assertIn(HOME, self.groups())
        res = self.drop(reassign="direct", want_rev=e.data["rev"])
        self.assertEqual(res["dropped"], [exits.exit_id(late)])
        self.assertEqual(self.ru(), [])
        self.assertNotIn(HOME, self.groups())


class ApiTest(DropCase):
    def call(self, query, dry=False):
        from kaskad.panel import api
        req = mock.Mock()
        req.match.group.return_value = HOME
        req.query = dict(query, **({"dry": "1"} if dry else {}))
        with mock.patch.object(api, "_changed"), mock.patch.object(api, "runner", return_value=self.w.runner):
            r = api.gw_group_delete(req)
        return json.loads(r.body.decode("utf-8"))

    def test_the_contract(self):
        from kaskad.panel.routes import HttpError
        d = self.call({"exits": "drop", "reassign": "direct"}, dry=True)
        self.assertEqual(sorted(d["drop"]), sorted(self.keys))
        self.assertTrue(d["dry"])
        with self.assertRaises(HttpError) as c:
            self.call({"exits": "move", "reassign": "direct", "rev": str(self.rev())})
        self.assertEqual((c.exception.status, c.exception.code, c.exception.data), (400, "field", {"field": "exits"}))
        with self.assertRaises(HttpError) as c:
            self.call({"reassign": "direct", "rev": str(self.rev())})
        self.assertEqual((c.exception.status, c.exception.code), (409, "ru_last"))
        out = self.call({"exits": "drop", "reassign": "direct", "rev": str(self.rev())})
        self.assertEqual(sorted(out["dropped"]), sorted(self.keys))
        self.assertEqual(set(out) >= {"ok", "rev", "moves", "dropped", "refs", "warnings"}, True)
        self.assertNotIn(ROUTER, json.dumps(out))
        with self.assertRaises(HttpError) as c:
            self.call({"exits": "drop", "reassign": "direct", "rev": str(self.rev())})
        self.assertEqual((c.exception.status, c.exception.code), (404, "gone"))


if __name__ == "__main__":
    import unittest
    unittest.main()
