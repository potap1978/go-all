"""РФ-группы (29.10, BRIEF-ru-groups-impl): ядро и API под общей карточкой российской группы.

* С-1 (вариант а, REV-plan-ru-groups): у российской группы, как у зарубежной, закреплённые устройства внутри уровня не
  двигаются, пока их ключ работает; второй ключ в уровне 1 получает новых, а уже подключённых раскладывает ночная
  перебалансировка — поле есть и работает у российской группы (assign один на обе роли).
* Ш2 / З-2: ``new_exits_tier`` российской группы — id уровня: туда встают и новый ключ, и «Перенести в группу», и
  «Удалить группу → ключи туда»; «last» — свой уровень (§6.6); уровень, выбранный в поле, удалён — политика не
  принимается (панель сама ставит «last», тест панели — tests/test_ru_groups_card.py).
* Пачка А: «лишняя 409 в консоли» — пробное удаление группы с ``ask=1`` отвечает 200 ``{ask: {code, data}}``."""

import json
import unittest
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError
from kaskad.gw import assign as A, exits, ops, policy as P, policy_ops
from tests.gw.test_s1_ru_pool_ops import HOME, ROUTER, RuCase, ru_key

E1, E2 = "e-00000000a1", "e-00000000a2"
DACHA = ru_key("Dacha", uuid="eeeeeeee-1111-2222-3333-444444444444", host="203.0.113.62")


def ru_doc(*tiers, **kw):
    d = P.default_doc("compat")
    g = P.default_group("g-d0d0d0", "ru", "ru_home", name="Дом")
    g["tiers"] = [P.default_tier("t%d" % (i + 1), list(t)) for i, t in enumerate(tiers)]
    g.update(kw)
    d["groups"].append(g)
    return d


def devs(n):
    return [{"id": "d-%08x" % i} for i in range(n)]


def pins(dec):
    return dict((d, p["exit"]) for d, p in dec["groups"]["g-d0d0d0"]["pins"].items())


class RuBalanceTest(unittest.TestCase):
    """С-1: «Дом» — роутер в уровне 1; добавили VPS в тот же уровень и способ «Поровну»."""

    def test_pinned_devices_stay_new_ones_spread(self):
        ds = devs(30)
        d0 = ru_doc([E1])
        dec0 = A.assign({"g-d0d0d0": ds}, {E1: "ok"}, d0, None, {}, {}, 0)
        self.assertEqual(set(pins(dec0).values()), {E1})
        d1 = ru_doc([E1, E2])                                   # второй ключ — в уровень 1, способ «Поровну»
        dec1 = A.assign({"g-d0d0d0": ds}, {E1: "ok", E2: "ok"}, d1, dec0, {}, {}, 10)
        self.assertEqual(set(pins(dec1).values()), {E1})        # подключённые остаются на роутере
        self.assertEqual(dec1["moves"], [])
        new = devs(330)[30:]
        dec2 = A.assign({"g-d0d0d0": ds + new}, {E1: "ok", E2: "ok"}, d1, dec1, {}, {}, 20)
        got = [pins(dec2)[x["id"]] for x in new]
        self.assertTrue(110 <= got.count(E2) <= 190, got.count(E2))   # новые — поровну

    def test_night_rebalance_spreads_the_pinned_ones(self):
        ds = devs(30)
        dec0 = A.assign({"g-d0d0d0": ds}, {E1: "ok"}, ru_doc([E1]), None, {}, {}, 0)
        d1 = ru_doc([E1, E2], night_rebalance={"enabled": True, "at": "04:00"})
        dec1 = A.assign({"g-d0d0d0": ds}, {E1: "ok", E2: "ok"}, d1, dec0, {}, {"local": ("2026-10-29", "03:59")}, 10)
        self.assertEqual(set(pins(dec1).values()), {E1})
        dec2 = A.assign({"g-d0d0d0": ds}, {E1: "ok", E2: "ok"}, d1, dec1, {}, {"local": ("2026-10-29", "04:01")}, 20)
        home = dict((x["id"], A.hrw(x["id"], [E1, E2])) for x in ds)
        self.assertEqual(pins(dec2), home)                      # ночью — по способу уровня, часть ушла на E2
        self.assertIn(E2, pins(dec2).values())
        self.assertTrue(dec2["moves"] and all(m["why"] == "rebalance" and m["group"] == "g-d0d0d0" for m in dec2["moves"]))
        dec3 = A.assign({"g-d0d0d0": ds}, {E1: "ok", E2: "ok"}, d1, dec2, {}, {"local": ("2026-10-29", "04:05")}, 30)
        self.assertEqual(dec3["moves"], [])                     # раз за ночь

    def test_night_rebalance_is_off_in_home_and_turns_it_custom(self):
        g = P.default_group("g-d0d0d0", "ru", "ru_home")
        self.assertFalse(g["night_rebalance"]["enabled"])
        self.assertEqual(P.preset_of(g), "ru_home")
        g["tiers"][0]["method"] = "best_rtt"                    # способ выбора — не поле набора
        self.assertEqual(P.preset_of(g), "ru_home")
        g["night_rebalance"]["enabled"] = True                  # поведение — поле набора: «Свой»
        self.assertFalse(P.matches_preset(g, "ru_home"))


class NewTierTest(unittest.TestCase):
    """Ш2: куда встаёт новый и перенесённый ключ российской группы."""

    def group(self, where):
        g = P.default_group("g-d0d0d0", "ru", "ru_home")
        g["tiers"] = [P.default_tier("t1", [E1]), P.default_tier("t2", [])]
        g["new_exits_tier"] = where
        return g

    def test_last_gives_a_tier_of_its_own(self):
        g = self.group("last")
        g["tiers"][1]["exits"] = [E2]
        t = P.new_tier(g)
        self.assertEqual((t["id"], len(g["tiers"])), ("t3", 3))

    def test_a_tier_id_and_first(self):
        self.assertEqual(P.new_tier(self.group("t1"))["id"], "t1")
        self.assertEqual(P.new_tier(self.group("first"))["id"], "t1")

    def test_a_tier_id_that_is_gone_is_refused(self):
        d = P.default_doc("compat")
        g = self.group("t2")
        g["tiers"] = g["tiers"][:1]                             # уровень t2 удалили, поле осталось
        d["groups"].append(g)
        errs, _warn = P.validate(d)
        self.assertIn("ep.err.new_tier", [e["code"] for e in errs])


class MovedIntoATierTest(RuCase):
    """Ш2: «Перенести в группу» и «Удалить группу → ключи туда» ставят ключ по ``new_exits_tier`` группы-цели."""

    OFFICE_NAME = "Офис"

    def setUp(self):
        super().setUp()
        ops.exit_add(self.w.runner, ROUTER, group=HOME)
        ops.exit_add(self.w.runner, DACHA, group=HOME)
        self.router, self.dacha = exits.exit_id(ROUTER), exits.exit_id(DACHA)
        self.office = policy_ops.group_add(self.w.runner, self.OFFICE_NAME, "ru", want_rev=policy_ops.current_rev())["id"]

    def office_group(self):
        return P.group(P.load(state.load()), self.office)

    def set_new_tier(self, where, tiers=1):
        with state.transaction() as st:
            doc = P.stored(st) or P.default_doc("compat")
            g = P.group(doc, self.office)
            while len(g["tiers"]) < tiers:
                g["tiers"].append(P.default_tier("t%d" % (len(g["tiers"]) + 1)))
            g["new_exits_tier"] = where
            P.save(st, doc)

    def test_move_into_a_chosen_tier(self):
        self.set_new_tier("t1")
        self.move(self.router, self.office, want_rev=policy_ops.current_rev())
        self.move(self.dacha, self.office, want_rev=policy_ops.current_rev())
        g = self.office_group()
        self.assertEqual([t["exits"] for t in g["tiers"]], [[self.router, self.dacha]])   # оба — в уровень 1

    def test_move_with_last_gives_each_its_tier(self):
        self.move(self.router, self.office, want_rev=policy_ops.current_rev())
        self.move(self.dacha, self.office, want_rev=policy_ops.current_rev())
        g = self.office_group()
        self.assertEqual([t["exits"] for t in g["tiers"]], [[self.router], [self.dacha]])  # «Дом»: цепочка

    def test_delete_group_keys_into_a_chosen_tier(self):
        self.set_new_tier("t2", tiers=2)
        policy_ops.group_delete(self.w.runner, HOME, exits_to=self.office, reassign="direct", want_rev=policy_ops.current_rev())
        g = self.office_group()
        self.assertEqual([t["exits"] for t in g["tiers"]], [[], [self.router, self.dacha]])


class AskTest(RuCase):
    """Пачка А: пробное удаление группы с ``ask=1`` — «чего не хватает» ответом 200, не 409."""

    def setUp(self):
        super().setUp()
        ops.exit_add(self.w.runner, ROUTER, group=HOME)

    def call(self, query):
        from kaskad.panel import api
        req = mock.Mock()
        req.match.group.return_value = HOME
        req.query = dict(query)
        with mock.patch.object(api, "_changed"), mock.patch.object(api, "runner", return_value=self.w.runner):
            r = api.gw_group_delete(req)
        return json.loads(r.body.decode("utf-8"))

    def test_ru_last_and_need_come_as_an_answer(self):
        from kaskad.panel.routes import HttpError
        rev0 = policy_ops.current_rev()
        a = self.call({"dry": "1", "ask": "1"})
        self.assertEqual((a["ok"], a["ask"]["code"]), (True, "ru_last"))
        self.assertEqual(a["ask"]["data"]["exits"], [exits.exit_id(ROUTER)])
        with state.transaction() as st:                         # «Мама» выбрала «Дом»: с ключами — «кого куда»
            st["gateway"]["users"][0]["ru_via"] = HOME
        b = self.call({"dry": "1", "ask": "1", "exits": "drop"})
        self.assertEqual(b["ask"]["code"], "need")
        self.assertEqual(sorted(b["ask"]["data"]), ["refs"])
        c = self.call({"dry": "1", "ask": "1", "exits": "drop", "reassign": "direct"})
        self.assertNotIn("ask", c)                              # всего хватает — обычный ответ прогона
        self.assertEqual(c["drop"], [exits.exit_id(ROUTER)])
        self.assertEqual(policy_ops.current_rev(), rev0)        # прогон ничего не пишет
        with self.assertRaises(HttpError) as e:                 # без ask — как было: 409
            self.call({"dry": "1"})
        self.assertEqual((e.exception.status, e.exception.code), (409, "ru_last"))

    def test_ask_only_with_a_dry_run(self):
        from kaskad.panel.routes import HttpError
        for q in ({"ask": "1", "rev": str(policy_ops.current_rev())}, {"dry": "1", "ask": "yes"}):
            with self.assertRaises(HttpError) as e:
                self.call(q)
            self.assertEqual((e.exception.status, e.exception.code, e.exception.data), (400, "field", {"field": "ask"}), q)
        self.assertIn(HOME, [g["id"] for g in P.load(state.load())["groups"]])

    def test_other_refusals_stay(self):
        from kaskad.panel.routes import HttpError
        with self.assertRaises(HttpError) as e:
            self.call({"dry": "1", "ask": "1", "exits": "move"})
        self.assertEqual((e.exception.status, e.exception.code), (400, "field"))


if __name__ == "__main__":
    unittest.main()
