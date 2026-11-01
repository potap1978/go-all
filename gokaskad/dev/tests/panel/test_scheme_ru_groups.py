"""Q6, экран 6 «Схема» (SPEC-stage1-exit-groups §8.4, §8.3, §8.9): российская группа на схеме.

* узел «rug:<gid>» у каждой российской группы — её ключи по шагам внутри, последний шаг и «сейчас»;
* линия ``ru_via`` устройство → российская группа — ТОЛЬКО при действующем выборе (``exits.ru_target``): группа
  выбрана И «Россия напрямую» для устройства действует (профиль, согласие ⚠РФ, не «блок» по выбору); выбор есть, а
  профиль «Всё через выход» — линии нет;
* намерение ``{"type": "ru_via", "device"|"user", "via"}`` через «Применить» схемы (``gw/ops._change``): чужая
  группа, «main», не строка, неизвестный id — отказ, состояние не меняется; «что изменится» и «кого затронет»;
* узел без российских групп (обновление с 3.3.7.9) — ни одного нового узла и линии."""

import json
import os
import shutil
import time
import unittest

from kaskad import paths, snapshot, state
from kaskad.gw import exits, model
from kaskad.panel import scheme
from kaskad.panel.routes import HttpError
from tests.gw.test_ru_target import D1, D3, HOME, RuWorld

D2 = "d-00000002"
U2 = "u-00000002"
RUG = "rug:" + HOME


class SchemeRuCase(RuWorld):
    def setUp(self):
        super().setUp()
        self._clear()

    def tearDown(self):
        self._clear()
        super().tearDown()

    def _clear(self):
        snapshot.forget()
        for c in (scheme._pool_cache, scheme._ru_pool_cache, scheme._graph_cache):
            c.clear()

    def graph(self):
        self._clear()
        return scheme.current_graph()

    def edges(self, g, typ):
        return dict((e["id"], e) for e in g["edges"] if e["type"] == typ)

    def node(self, g, nid):
        return next((n for n in g["nodes"] if n["id"] == nid), None)

    def row(self, g, did):
        return next(d for n in g["nodes"] if n["type"] == "user" for d in n["data"]["devices"] if d["id"] == did)

    def edit(self, fn):
        st = state.load()
        fn(st)
        state.save(st)
        self._clear()

    def raw(self):
        with open(paths.state(), "rb") as f:
            return f.read()

    def apply(self, intents, dry=False):
        return scheme.apply({"intents": intents, "dry_run": dry}, runner=self.w.runner)


class NodeAndLinesTest(SchemeRuCase):
    def test_group_node_and_lines_where_the_choice_acts(self):
        self.world()                                       # «Мама» (D1, D3): ru_direct, согласие, «Дом»
        g = self.graph()
        n = self.node(g, RUG)
        self.assertEqual((n["type"], n["data"]["group"], n["data"]["name"], n["data"]["fail"]),
                         ("ru_group", HOME, "Дом", "block"))
        self.assertEqual([r["id"] for r in n["data"]["exits"]], self.ru_ids)       # ключи группы — внутри узла
        self.assertEqual(n["data"]["now"]["step"], "exit")
        self.assertIn(n["data"]["now"]["exit"], self.ru_ids)
        lines = self.edges(g, "ru_via")
        self.assertEqual(set(lines), {"ruvia:" + D1, "ruvia:" + D3})
        e = lines["ruvia:" + D1]
        self.assertEqual((e["source"], e["sourceHandle"], e["target"], e["data"]["from"]),
                         ("u-00000001", D1, RUG, "user"))
        self.assertEqual(set(self.edges(g, "ru_link")), {"rulink:" + HOME})
        self.assertEqual(self.edges(g, "ru_link")["rulink:" + HOME]["source"], "rules:ru_direct")
        self.assertEqual((n["data"]["devices"], n["data"]["chosen"]), (2, 2))
        r = self.row(g, D1)
        self.assertEqual((r["ru"]["via"], r["ru"]["from"], r["ru"]["acts"], r["ru"]["now"]["step"]),
                         (HOME, "user", True, "exit"))
        self.assertIsNone(r["ru_via"])                     # своего значения нет — как у пользователя
        self.assertEqual(self.node(g, "u-00000001")["data"]["ru_via"], HOME)
        # Российские ключи — не в пуле и не цели маршрута: ни строки пула, ни узла «exit:»
        pool = self.node(g, "pool")["data"]["exits"]
        for eid in self.ru_ids:
            self.assertNotIn(eid, pool)
            self.assertIsNone(self.node(g, "exit:" + eid))

    def test_chosen_but_all_exit_draws_no_line(self):
        self.world()
        self.edit(lambda st: model.get_user(st, U2).update(ru_via=HOME))     # «Дача»: «Всё через выход»
        g = self.graph()
        self.assertNotIn("ruvia:" + D2, self.edges(g, "ru_via"))
        r = self.row(g, D2)
        self.assertEqual((r["ru"]["via"], r["ru"]["acts"], r["ru"]["now"]), (HOME, False, None))
        n = self.node(g, RUG)["data"]
        self.assertEqual((n["devices"], n["chosen"]), (2, 3))

    def test_no_line_where_russia_direct_does_not_act(self):
        cases = (("own direct", lambda st: model.get_device(st, D1).update(ru_via="direct")),
                 ("block by choice", lambda st: model.get_device(st, D1).update(route={"via": "block", "fail": "block"})),
                 ("paused", lambda st: model.get_device(st, D1).update(enabled=False)),
                 ("profile all_exit", lambda st: model.get_device(st, D1).update(rules="all_exit")))
        for name, fn in cases:
            with self.subTest(name):
                self.world()
                self.edit(fn)
                lines = self.edges(self.graph(), "ru_via")
                self.assertNotIn("ruvia:" + D1, lines)
                self.assertIn("ruvia:" + D3, lines)
        self.world()
        self.edit(lambda st: st["gateway"].pop("rf_consent", None))           # без согласия ⚠РФ — ни у кого
        g = self.graph()
        self.assertEqual(self.edges(g, "ru_via"), {})
        self.assertIsNotNone(self.node(g, RUG))                               # группа на схеме остаётся

    def test_gateway_off_draws_the_configured_lines(self):
        """Шлюз выключен: схема показывает настроенные пути с плашкой «Шлюз выключен: … заработают, когда его
        включат» (renderGwNote) — линии российских групп тоже, как маршруты."""
        self.world()
        self.edit(lambda st: st["gateway"].update(enabled=False))
        g = self.graph()
        self.assertFalse(g["gateway"]["enabled"])
        self.assertEqual(set(self.edges(g, "ru_via")), {"ruvia:" + D1, "ruvia:" + D3})
        self.assertIsNotNone(self.node(g, RUG))

    def test_group_gone_is_direct_and_no_line(self):
        self.world()
        self.edit(lambda st: model.get_device(st, D1).update(ru_via="g-dead00"))
        g = self.graph()
        self.assertNotIn("ruvia:" + D1, self.edges(g, "ru_via"))
        r = self.row(g, D1)
        self.assertEqual((r["ru"]["via"], r["ru"]["from"], r["ru"].get("broken"), r["ru_via"]),
                         ("direct", "device", "g-dead00", "g-dead00"))

    def test_last_step(self):
        self.world(fail="direct")
        snap = exits.load_snapshot()
        for i in self.ru_ids:
            snap["exits"][i] = {"alive": False, "state": "off", "fails": 3, "reasons": []}
        exits.save_snapshot(snap)
        g = self.graph()
        self.assertEqual(self.node(g, RUG)["data"]["now"], {"step": "last_direct", "exit": None, "since": None,
                                                            "empty": False})
        self.assertEqual(self.edges(g, "ru_via")["ruvia:" + D1]["data"]["step"], "last_direct")
        # ключи снова годны, но сборщик ещё держит группу на последнем шаге — как решил сборщик (pins.json)
        snap = exits.load_snapshot()
        for i in self.ru_ids:
            snap["exits"][i] = {"alive": True, "state": "ok", "reasons": []}
        exits.save_snapshot(snap)
        os.makedirs(os.path.dirname(paths.pins_json()), exist_ok=True)
        with open(paths.pins_json(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "saved": int(time.time()),
                       "groups": {HOME: {"last_step": "direct", "pins": {}}}}, f)
        self.assertEqual(self.node(self.graph(), RUG)["data"]["now"]["step"], "last_direct")

    def test_live_part_carries_the_step_of_groups(self):
        doc = {"v": 1, "ts": time.time(), "seq": 3, "devices": {}, "exits": {}, "inbounds": {},
               "groups": {HOME: {"active_tier": None, "last_step": "direct", "since": 1700000000}}}
        os.makedirs(os.path.dirname(paths.live_json()), exist_ok=True)
        with open(paths.live_json(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        snapshot.forget()
        self.assertEqual(scheme.live_part()["groups"], doc["groups"])

    def test_empty_group(self):
        self.world()

        def empty(st):
            for grp in st["gateway"]["exit_policy"]["groups"]:
                if grp["id"] == HOME:
                    grp["tiers"] = [{"id": "t1", "name": "", "exits": []}]
        self.edit(empty)
        n = self.node(self.graph(), RUG)["data"]
        self.assertEqual((n["exits"], n["now"]["step"], n["now"]["empty"]), ([], "last_block", True))

    def test_layout_keeps_the_place_of_the_group(self):
        self.world()
        scheme.save_layout({"version": 0, "positions": {RUG: [120, 340]}})
        self.assertEqual(self.node(self.graph(), RUG)["position"], {"x": 120, "y": 340})


class WithoutGroupsTest(SchemeRuCase):
    """Узел 3.3.7.9 после обновления: российских групп нет — схема как была (правило миграции владельца)."""

    def check(self, g):
        types = set(n["type"] for n in g["nodes"])
        self.assertFalse(types - {"user", "inbound", "filter", "rules", "pool", "exit", "direct", "block"})
        self.assertFalse([n for n in g["nodes"] if n["id"].startswith("rug:")])
        self.assertFalse([e for e in g["edges"] if e["type"] in ("ru_via", "ru_link")])
        self.assertIsNone(self.row(g, D1)["ru"]["acts"])

    def test_no_policy_at_all(self):
        st = self.ru_state()
        st["gateway"].pop("exit_policy", None)
        state.save(st)
        self.check(self.graph())

    def test_policy_without_russian_groups(self):
        self.world(via=None)
        self.edit(lambda st: st["gateway"]["exit_policy"].update(
            groups=[x for x in st["gateway"]["exit_policy"]["groups"] if x.get("role") != "ru"]))
        self.check(self.graph())


class IntentTest(SchemeRuCase):
    def test_dry_run_writes_nothing_and_says_the_change(self):
        self.world(via=None)
        before = self.raw()
        doc = self.apply([{"type": "ru_via", "device": D1, "via": HOME}], dry=True)
        self.assertTrue(doc["valid"])
        r = doc["results"][0]
        self.assertEqual((r["ok"], r["changed"], r["device"], r["ru_via"]), (True, True, D1, HOME))
        self.assertIn("«Дом»", r["text"])
        self.assertEqual([c["what"] for c in doc["changes"]], ["ru_via"])
        ru = [a for a in doc["affected"] if a["kind"] == "ru"]
        self.assertEqual([a["device"] for a in ru], [D1])
        self.assertIn("«Дом»", ru[0]["after"])
        self.assertEqual(self.raw(), before)

    def test_apply_writes_and_draws_the_line(self):
        self.world(via=None)
        self.assertNotIn("ruvia:" + D1, self.edges(self.graph(), "ru_via"))
        doc = self.apply([{"type": "ru_via", "device": D1, "via": HOME}])
        self.assertTrue(doc["applied"])
        self.assertEqual(model.get_device(state.load(), D1).get("ru_via"), HOME)
        self.assertIn("ruvia:" + D1, self.edges(doc["graph"], "ru_via"))
        self.assertNotIn("ruvia:" + D3, self.edges(doc["graph"], "ru_via"))
        doc = self.apply([{"type": "ru_via", "device": D1, "via": "inherit"}])
        self.assertNotIn("ru_via", model.get_device(state.load(), D1))
        self.assertNotIn("ruvia:" + D1, self.edges(doc["graph"], "ru_via"))

    def test_user_value_moves_every_device_that_follows(self):
        self.world(via=None)
        self.edit(lambda st: model.get_device(st, D3).update(ru_via="direct"))
        doc = self.apply([{"type": "ru_via", "user": "u-00000001", "via": HOME}])
        self.assertEqual(model.get_user(state.load(), "u-00000001").get("ru_via"), HOME)
        self.assertEqual(set(self.edges(doc["graph"], "ru_via")), {"ruvia:" + D1})       # у ноутбука своё

    def test_not_acting_choice_is_kept_with_a_warning(self):
        self.world()
        doc = self.apply([{"type": "ru_via", "user": U2, "via": HOME}], dry=True)        # «Дача»: всё через выход
        self.assertTrue(doc["valid"])
        self.assertEqual(len(doc["results"][0]["warnings"]), 1)
        self.assertFalse([a for a in doc["affected"] if a["kind"] == "ru"])            # путь не меняется

    def test_bad_values_change_nothing(self):
        self.world(via=None)
        before = self.raw()
        for name, it in (("foreign main", {"type": "ru_via", "device": D1, "via": "main"}),
                         ("no such group", {"type": "ru_via", "device": D1, "via": "g-ffffff"}),
                         ("not a string", {"type": "ru_via", "device": D1, "via": {"x": 1}}),
                         ("bad form", {"type": "ru_via", "device": D1, "via": "../etc"}),
                         ("unknown device", {"type": "ru_via", "device": "d-ffffffff", "via": HOME}),
                         ("no target", {"type": "ru_via", "via": HOME})):
            with self.subTest(name):
                doc = self.apply([it], dry=True)
                self.assertFalse(doc["valid"])
                self.assertFalse(doc["results"][0]["ok"])
                with self.assertRaises(HttpError) as cm:
                    self.apply([it])
                self.assertIn(cm.exception.status, (400, 409))
                self.assertEqual(self.raw(), before)

    def test_journal(self):
        self.world(via=None)
        self.apply([{"type": "ru_via", "device": D1, "via": HOME}])
        with open(paths.log(), encoding="utf-8") as f:
            self.assertIn("ru_via: %s -> %s" % (D1, HOME), f.read())


# ── клиент (graph.js) на настоящем графе сервера: функции вырезаны из файла (tests/jsparts.py) и идут в node ──
def ru_scope():
    """Область contract-теста схемы (computeView, ruOf, черновик) + поле «Российские сайты» и onChange; поддельный DOM
    добавляет run() test_panel_rf_js."""
    from tests import jsparts
    from tests import test_panel_js_contract as C
    js = C.read("graph.js")
    base = C.graph_scope().split("// the scheme's ctx.api")[0]
    mark = "  return { S: S,"
    assert mark in base, "graph_scope of test_panel_js_contract changed"
    extra = "\n".join(jsparts.function(js, n) for n in ("h", "append", "clear", "field", "lc1", "ruField", "ruDevNow",
                                                         "ruValueText", "ruExitName", "onChange", "addIntent"))
    stubs = ("  var HIST_MAX = 50;\n  function structureChanged() {} function renderInspector() {} function toast() {}\n"
             "  function intentText(it) { return ''; }\n")
    out = ("  return { ruField: ruField, ruOf: ruOf, onChange: onChange, addIntent: addIntent, h: h, S: S,")
    return base.replace(mark, stubs + extra + "\n" + out, 1)


def run_ru(graph, script):
    from tests.test_panel_rf_js import run
    return run(ru_scope() + "\nconst DATA = %s;\n" % json.dumps(graph, ensure_ascii=False), r"""
const S = graph.S;
function load() {
  S.base = DATA; S.idx = graph.indexBase(DATA); S.order = {}; S.intents = []; S.hist = []; S.sim = null;
  S.idx.users.forEach(function (u) { S.order[u.id] = (u.data.devices || []).map(function (d) { return d.id; }); });
  graph.rebuildDraft(); graph.computeView();
}
function line() { var e = S.view.byKey['rulink:""" + HOME + r"""']; return e ? e.devs.slice().sort() : null; }
function fieldOf(kind, id) {
  var b = graph.ruField(kind, id); if (!b) return null;
  var sel = b.querySelector('select'), p = b.querySelector('p');
  return { disabled: sel.disabled, act: sel.getAttribute('data-act'),
           opts: sel.querySelectorAll('option').map(function (o) { return [o.value, !!o.selected]; }),
           notes: b.querySelectorAll('p').map(function (x) { return [x.getAttribute('data-ru'), x.textContent]; }) };
}
function pick(kind, id, v) {
  var b = graph.ruField(kind, id), sel = b.querySelector('select'); sel.value = v; graph.onChange({ target: sel });
  return S.intents[S.intents.length - 1];
}
load();
""" + script)


@unittest.skipUnless(shutil.which("node"), "нет node: функции graph.js не запустить")
class ClientTest(SchemeRuCase):
    def test_line_and_field_where_the_choice_acts(self):
        self.world()
        self.edit(lambda st: model.get_user(st, U2).update(ru_via=HOME))     # «Дача»: выбрана, а «Всё через выход»
        out = run_ru(self.graph(), r"""
var o = { line: line(), groups: S.idx.ruGroups.map(function (n) { return n.id; }),
          d1: graph.ruOf('%s'), d2: graph.ruOf('%s'), f1: fieldOf('dev', '%s'), f2: fieldOf('dev', '%s') };
console.log(JSON.stringify(o));
""" % (D1, D2, D1, D2))
        self.assertEqual(out["line"], sorted([D1, D3]))                       # D2 — нет: выбор не действует
        self.assertEqual(out["groups"], [RUG])
        self.assertEqual(out["d1"], {"via": HOME, "from": "user", "group": True, "acts": True})
        self.assertEqual(out["d2"], {"via": HOME, "from": "user", "group": True, "acts": False})
        f1, f2 = out["f1"], out["f2"]
        self.assertFalse(f1["disabled"])
        self.assertEqual(f1["opts"], [["inherit", True], ["direct", False], [HOME, False]])
        self.assertEqual(f1["notes"][0], ["isp", "graph.ru.ispDev"])            # интернет-провайдер роутера
        self.assertTrue(f2["disabled"])                                          # без «Россия напрямую» — неактивно
        self.assertEqual(f2["notes"], [["off", "graph.ru.onlyRd"]])

    def test_draft_moves_the_line(self):
        """Черновик: «Всё через выход» у D1 — его линии нет; своё «напрямую» у D3 — тоже; «Через «Дом»» у D2 при
        «Россия напрямую» — линия «новая»."""
        self.world()
        out = run_ru(self.graph(), r"""
var o = {};
graph.addIntent({ type: 'rules', device: '%s', profile: 'all_exit' }, true); graph.computeView();
o.allExit = { line: line(), acts: graph.ruOf('%s').acts };
o.it3 = pick('dev', '%s', 'direct'); graph.computeView();
o.direct = line();
graph.addIntent({ type: 'rules', device: '%s', profile: 'ru_direct' }, true);
o.it2 = pick('dev', '%s', '%s'); graph.computeView();
o.d2 = { line: line(), state: S.view.byKey['rulink:%s'].state };
o.user = pick('user', 'u-00000001', 'direct');
console.log(JSON.stringify(o));
""" % (D1, D1, D3, D2, D2, HOME, HOME))
        self.assertEqual(out["allExit"], {"line": [D3], "acts": False})
        self.assertEqual(out["it3"], {"type": "ru_via", "via": "direct", "device": D3})
        self.assertIsNone(out["direct"])                                         # никто не идёт — линии нет
        self.assertEqual(out["it2"], {"type": "ru_via", "via": HOME, "device": D2})
        self.assertEqual(out["d2"], {"line": [D2], "state": "new"})
        # у пользователя «напрямую» — без своего значения (по умолчанию узла это и есть «напрямую»)
        self.assertEqual(out["user"], {"type": "ru_via", "via": "inherit", "user": "u-00000001"})

    def test_group_gone_and_no_groups(self):
        self.world()
        self.edit(lambda st: model.get_device(st, D1).update(ru_via="g-dead00"))
        out = run_ru(self.graph(), "console.log(JSON.stringify({ f: fieldOf('dev', '%s'), line: line() }));" % D1)
        self.assertIn(["", True], out["f"]["opts"])                              # «Группы больше нет — …»
        self.assertEqual(out["line"], [D3])
        self.edit(lambda st: st["gateway"]["exit_policy"].update(
            groups=[x for x in st["gateway"]["exit_policy"]["groups"] if x.get("role") != "ru"]))
        self.edit(lambda st: [model.get_device(st, D1).pop("ru_via", None), model.get_user(st, "u-00000001").pop("ru_via", None)])
        out = run_ru(self.graph(), r"""
console.log(JSON.stringify({ f: fieldOf('dev', '%s'), u: fieldOf('user', 'u-00000001'), groups: S.idx.ruGroups.length,
  ru: S.view.edges.filter(function (e) { return e.type === 'ru'; }).length,
  nodes: S.base.nodes.filter(function (n) { return n.type === 'ru_group'; }).length }));
""" % D1)
        self.assertEqual(out, {"f": None, "u": None, "groups": 0, "ru": 0, "nodes": 0})   # как в 3.3.7.9
