"""PLAN-order Ш5: «Уровни» части «Выходы › Группы» в node (pages/exits-groups.js, настоящие функции через
tests/jsparts.py, элементы — заглушки): строки автоуровня — по рядам сервера (11 / 95 / 12 / 12 мс → Р1 = 11, 12, 12;
Р2 = 95), число — то, по которому выбирает сервер; ход в автоуровне ставит «По порядку (вручную)» и порядок «как видел»;
«Вернуть автовыбор» снимает флаг и порядок; перенос между уровнями флаг не трогает; правка уровня — «пересчитаются после
сохранения»; сборщик молчит — «сила неизвестна»; «Что изменится»; 409 — правка порядка едет вместе с уровнями
(Kit.rebaseModel); внутренняя группа — стрелки меняют шаги местами (не сливают, eg.js:509–510 до правки); «сейчас» — по
рядам; RU/EN. Компонент порядка — tests/test_panel_order_kit.py, настоящий Chrome — tests/test_panel_order_chrome.py."""

import re
import shutil
import unittest

from tests import jsparts
from tests.test_ru_groups_card import order_scope, read, run

HAS_NODE = shutil.which("node") is not None


EX = r"""
Object.assign(ROWS, {
  k1: { id: "k1", name: "Ключ 1", kind: "awg", pstate: "ok", enabled: true, rttMs: 10, devices: 3 },
  k2: { id: "k2", name: "Ключ 2", kind: "awg", pstate: "ok", enabled: true, rttMs: 97, devices: 0 },
  k3: { id: "k3", name: "Ключ 3", kind: "awg", pstate: "ok", enabled: true, rttMs: 13, devices: 0 },
  k4: { id: "k4", name: "Ключ 4", kind: "awg", pstate: "ok", enabled: true, rttMs: 12, devices: 0 },
  k5: { id: "k5", name: "Ключ 5", kind: "awg", pstate: "off", enabled: true, rttMs: null, devices: 0 },
});
const RANKS = { t1: { by: "rtt", queue: true, order: "auto", klass: "ok", nodata: false,
  rows: [[{ id: "k1", v: 11, share: 0.333 }, { id: "k3", v: 12, share: 0.333 }, { id: "k4", v: 12, share: 0.333 }], [{ id: "k2", v: 95, share: 0 }]],
  out: [{ id: "k5", why: "off" }] } };
const home = () => grp("main", "abroad", [Object.assign(tier("t1", ["k1", "k2", "k3", "k4", "k5"], "best_rtt")), tier("t2", ["r2"])]);
const rowsOf = (box) => find(box, (x) => x.a && x.a["data-ord-row"]).map((x) => x.a["data-ord-row"]);
const cls = (x, c) => !!(x.a && typeof x.a.class === "string" && x.a.class.split(" ").indexOf(c) >= 0);
const tags = (box) => find(box, (x) => cls(x, "kk-eg-rk")).map((x) => x.a.text);
// Ш6: «нет рядов» — строкой ko-note под шапкой (значок и текст); причины рядов — строками ko-why над рядом
const notes = (box) => find(box, (x) => cls(x, "kk-eg-rkn")).map((x) => x.kids[1].a.text);
const whys = (box) => find(box, (x) => cls(x, "ko-why__t")).map((x) => x.a.text);
const rowEl = (box, id) => find(box, (x) => x.a && x.a["data-ord-row"] === id)[0];
const descOf = (box, id) => find(rowEl(box, id), (x) => x.tag === "button" && x.a.class === "kk-eg-exit__n")[0].kids[1].a.text;
const btnOf = (box, aria) => find(box, (x) => x.o && x.o.aria === aria)[0];
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class TiersOrderTest(unittest.TestCase):
    def scope(self):
        js = read("pages/exits-groups.js")
        return ([jsparts.const(js, n) for n in ("METHODS", "MAX_TIERS", "tierLabel", "methodsOf", "usable", "exitsOfGroup")]
                + [jsparts.function(js, n) for n in ("countExits", "laterHint", "tiersBlock", "newTierId", "ruNow")] + order_scope() + [EX])

    def test_auto_tier_rows_by_strength_and_the_number_of_the_server(self):
        out = run(self.scope(), r"""
form(home()); LIVE = { main: { active_tier: "t1", ranks: RANKS } };
const box = tiersBlock("main");
const ms = find(box, (x) => x.a && x.a.class === "kk-eg-rtt gx-mono").map((x) => x.a.text);
const outh = find(box, (x) => x.a && x.a.class === "kk-eg-outh").length;
const chip5 = find(rowEl(box, "k5"), (x) => x.chip !== undefined)[0];
const bars = find(box, (x) => cls(x, "ko-bar")).map((x) => x.a.class);
LIVE = null; const box2 = tiersBlock("main");
// в уровне выходы обоих видов — сила по отклику к своей норме: «× 1,2» и подсказка, по какому числу выбирает сервер
LIVE = { main: { active_tier: "t1", ranks: { t1: Object.assign({}, RANKS.t1, { by: "x_norm", rows: [[{ id: "k1", v: 1.2, share: 0.5 }]] }) } } };
const xn = find(tiersBlock("main"), (x) => x.a && x.a.class === "kk-eg-rtt gx-mono")[0].a;
console.log(JSON.stringify({ rows: rowsOf(box), tags: tags(box), ms, outh, chip5, bars, whys: whys(box), notes: notes(box), stale: rowsOf(box2), notes2: notes(box2),
  lists: ORDER_LISTS.length, key: ORDER_LISTS[0].key, xn: [xn.text, xn.title] }));
""")
        self.assertEqual(out["rows"][:5], ["k1", "k3", "k4", "k2", "k5"])           # 95 мс — последним из участников
        self.assertEqual(out["tags"][:5], ['ep.ord.R{"n":1}'] * 3 + ['ep.ord.R{"n":2}', "—"])
        self.assertEqual(out["ms"][:4], ['ep.ms{"n":"11"}', 'ep.ms{"n":"12"}', 'ep.ms{"n":"12"}', 'ep.ms{"n":"95"}'])   # медиана, не минута
        self.assertEqual(out["outh"], 1)
        self.assertEqual(out["chip5"], {"chip": "ep.ord.why.off", "tone": "default"})   # Ш6: причина — фишкой вместо полоски
        # у «не участвует» (k5) полоски нет; Р2 — приглушённо; уровень 2 без рядов сервера — пустая дорожка
        self.assertEqual(out["bars"], ["ko-bar"] * 3 + ["ko-bar ko-bar--mute", "ko-bar ko-bar--none"])
        self.assertEqual(out["whys"], ['ep.ord.rw.rtt{"ms":"30"}', 'ep.ord.rw.queue{"n":1}'])
        self.assertEqual(out["notes"], ["ep.ord.note.none"])                   # у уровня 1 ряды есть — строки нет; у 2-го рядов нет
        self.assertEqual(out["stale"][:5], ["k1", "k2", "k3", "k4", "k5"])          # сборщик молчит — сохранённый порядок
        self.assertEqual(out["notes2"][0], "ep.ord.note.none")
        self.assertEqual((out["lists"], out["key"]), (3, "eg:main"))           # список на каждую отрисовку блока
        self.assertEqual(out["xn"], ["× 1.2", "ep.ord.xnT"])

    def test_move_in_auto_tier_turns_manual_with_the_order_as_seen_and_back(self):
        out = run(self.scope(), r"""
form(home()); LIVE = { main: { active_tier: "t1", ranks: RANKS } };
let box = tiersBlock("main");
btnOf(box, 'ep.downName{"name":"Ключ 3"}').fn();                            // k3 ↓ — под k4
const d = forms.main.draft.tiers[0];
const after = { exits: d.exits.slice(), order: d.order, method: d.method };
box = tiersBlock("main");
const al = find(box, (x) => x.al === "info")[0];
const was3 = descOf(box, "k3"), was1 = descOf(box, "k1");
const tagsM = tags(box).slice(0, 5);
const impact = ordImpact(forms.main);
al.btns[0].fn();                                                           // «Вернуть автовыбор»
const back = { exits: forms.main.draft.tiers[0].exits.slice(), order: forms.main.draft.tiers[0].order === undefined ? null : "manual" };
SELECTS.length = 0; tiersBlock("main"); SELECTS[0].on("manual");             // «По порядку (вручную)» из списка — порядок как на экране
const sel = { exits: forms.main.draft.tiers[0].exits.slice(), order: forms.main.draft.tiers[0].order };
SELECTS.length = 0; tiersBlock("main"); SELECTS[0].on("weights");
const off = { order: forms.main.draft.tiers[0].order === undefined ? null : "manual", method: forms.main.draft.tiers[0].method };
console.log(JSON.stringify({ after, alT: al.title, alText: al.text, tagsM, impact, back, sel, off, was3, was1 }));
""")
        self.assertEqual(out["after"], {"exits": ["k1", "k4", "k3", "k2", "k5"], "order": "manual", "method": "best_rtt"})
        self.assertEqual(out["alT"], "ep.ord.manualT")
        self.assertEqual(out["alText"], 'ep.ord.manual{"m":"ep.method.best_rtt"}')
        self.assertEqual(out["tagsM"][:4], ["1", "2", "3", "4"])                 # место в очереди — число
        self.assertTrue(out["was3"].startswith('ep.ord.was{"x":"ep.ord.R{\\"n\\":1}"}'), out["was3"])   # было: Р1
        self.assertTrue(out["was1"].startswith("ep.ord.was"), out["was1"])         # макет П3: «было: Р1» и у первого
        self.assertEqual(out["impact"], ['ep.ord.saveManual{"n":"ep.tierN{\\"n\\":1}"}'])
        self.assertEqual(out["back"], {"exits": ["k1", "k2", "k3", "k4", "k5"], "order": None})   # и порядок, как был
        self.assertEqual(out["sel"], {"exits": ["k1", "k3", "k4", "k2", "k5"], "order": "manual"})
        self.assertEqual(out["off"], {"order": None, "method": "weights"})

    def test_move_to_another_tier_keeps_flags_changed_tier_says_after_save(self):
        out = run(self.scope(), r"""
form(home()); LIVE = { main: { active_tier: "t1", ranks: RANKS } };
const box = tiersBlock("main");
btnOf(box, 'ep.downName{"name":"Ключ 5"}').fn();                            // последняя строка уровня 1 ↓ — в уровень 2
const t = forms.main.draft.tiers;
const res = { t1: [t[0].exits, t[0].order || null], t2: [t[1].exits, t[1].order || null], notes: notes(tiersBlock("main")) };
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["t1"], [["k1", "k3", "k4", "k2"], None])
        self.assertEqual(out["t2"], [["k5", "r2"], None])
        self.assertEqual(out["notes"][0], "ep.ord.note.changed")                  # второго расчёта в браузере нет

    def test_tier_reorder_and_what_changes(self):
        out = run(self.scope(), r"""
const g = home(); g.tiers[1].name = "Запасной"; form(g);
const box = tiersBlock("main");
const menu = find(box, (x) => Array.isArray(x.menu)).find((m) => m.o.label === "Запасной");
menu.menu.find((i) => i.label === "ep.ord.tierUp").on();
const ids = forms.main.draft.tiers.map((x) => x.id);
const imp = ordImpact(forms.main);
forms.main.draft.return_to_primary.enabled = false; const imp2 = ordImpact(forms.main);
forms.main.draft.per_connection = true; const imp3 = ordImpact(forms.main);
const first = find(tiersBlock("main"), (x) => Array.isArray(x.menu))[0].menu.map((i) => i.label);
console.log(JSON.stringify({ ids, imp, imp2, imp3, first }));
""")
        self.assertEqual(out["ids"], ["t2", "t1"])
        self.assertEqual(out["imp"], ['ep.ord.saveTiers{"n":"Запасной","min":"10"}'])
        self.assertEqual(out["imp2"], ['ep.ord.saveTiersNoBack{"n":"Запасной","min":"10"}'])
        self.assertEqual(out["imp3"], ['ep.ord.saveTiersConn{"n":"Запасной","min":"10"}'])
        self.assertEqual(out["first"], ["ep.tierRename", "ep.ord.tierDown"])        # первому — только «опустить»

    def test_per_connection_is_locked(self):
        out = run(self.scope(), r"""
// ряды «по соединениям» (REV-order З-1: ряды от другого способа — «пересчитаются после сохранения»)
const g = home(); g.per_connection = true; form(g); LIVE = { main: { active_tier: "t1", ranks: { t1: Object.assign({}, RANKS.t1, { by: "per_connection", order: "auto" }) } } };
SELECTS.length = 0;
const box = tiersBlock("main");
const grips = find(box, (x) => x.grip && x.grip.charAt(0) !== "@");
const up = btnOf(box, 'ep.upName{"name":"Ключ 2"}'), up1 = btnOf(box, 'ep.upName{"name":"r2"}') || btnOf(box, 'ep.upName{"name":"VPS"}');
console.log(JSON.stringify({ dis: grips.every((x) => x.dis), tip: grips[0].tip, meth: SELECTS[0].select, note: notes(box)[0], upDis: up.disabled, crossDis: up1.disabled }));
""")
        self.assertTrue(out["dis"])
        self.assertEqual(out["tip"], "ep.ord.locked")
        self.assertNotIn("manual", out["meth"])
        self.assertEqual(out["note"], "ep.ord.note.per_connection")
        self.assertTrue(out["upDis"])                                              # внутри уровня — порядок не действует
        self.assertFalse(out["crossDis"])                                          # в другой уровень — можно

    def test_domestic_group_arrows_swap_steps_not_merge(self):
        out = run(self.scope(), r"""
const g = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", ["r2"]), tier("t3", ["r3"])], { new_exits_tier: "last" }); form(g);
let box = tiersBlock("g-a");
btnOf(box, 'ep.upName{"name":"VPS"}').fn();                                   // шаг 2 ↑ — местами с шагом 1
const a = forms["g-a"].draft.tiers.map((x) => x.id + ":" + x.exits.join(","));
box = tiersBlock("g-a");
btnOf(box, 'ep.downName{"name":"VPS"}').fn();
const b = forms["g-a"].draft.tiers.map((x) => x.id + ":" + x.exits.join(","));
console.log(JSON.stringify({ a, b }));
""")
        self.assertEqual(out["a"], ["t2:r2", "t1:r1", "t3:r3"])                  # до правки: t1 = r1, r2 (слияние)
        self.assertEqual(out["b"], ["t1:r1", "t2:r2", "t3:r3"])

    def test_rebase_after_409_carries_the_order(self):
        """Чужая правка политики, пока у нас ручной порядок: уровни — одна правка «tiers», флаг едет вместе с ними."""
        out = run(self.scope(), r"""
const base = home(), draft = copy(base);
draft.tiers[0].exits = ["k2", "k1", "k3", "k4", "k5"]; draft.tiers[0].order = "manual";
const fresh = copy(base); fresh.tiers[0].exits.push("k9"); fresh.fail = "direct";
const KEYS = ["fail", "tiers"];
const r = Kit.rebaseModel(base, draft, fresh, KEYS);
const again = Kit.applyAgain(r.draft, r.edits.find((e) => e.key === "tiers"));
console.log(JSON.stringify({ mode: r.mode, t1: [again.tiers[0].exits, again.tiers[0].order], fail: again.fail }));
""")
        self.assertEqual(out["mode"], "list")
        self.assertEqual(out["t1"], [["k2", "k1", "k3", "k4", "k5", "k9"], "manual"])
        self.assertEqual(out["fail"], "direct")

    def test_now_names_who_gets_new_devices(self):
        out = run(self.scope(), r"""
const g = grp("g-a", "ru", [tier("t1", ["k1", "k2", "k3"], "best_rtt")]);
const one = { by: "rtt", queue: true, rows: [[{ id: "k3", v: 9, share: 1 }], [{ id: "k1", v: 40, share: 0 }, { id: "k2", v: 41, share: 0 }]], out: [] };
LIVE = { "g-a": { active_tier: "t1", ranks: { t1: one } } };
const a = ruNow(g);
ROWS.k1.pstate = "degraded"; ROWS.k2.pstate = "degraded"; ROWS.k3.pstate = "degraded";
LIVE = { "g-a": { active_tier: "t1" } };
const b = ruNow(g);
const sh = { by: "weights", queue: false, rows: [[{ id: "k1", v: 3, share: 0.6 }], [{ id: "k2", v: 1, share: 0.2 }, { id: "k3", v: 1, share: 0.2 }]], out: [] };
LIVE = { "g-a": { active_tier: "t1", ranks: { t1: sh } } };
const c = ruNow(g);
console.log(JSON.stringify({ a: [a.exit, a.n || null], b: [b.exit, b.n || null], c: [c.exit, c.n || null] }));
""")
        self.assertEqual(out["a"], ["k3", None])                                  # первый ряд — один ключ: он, не первый по списку
        self.assertEqual(out["b"], [None, 3])                                      # рабочих нет, рядов нет — уровень, не «первый ключ»
        self.assertEqual(out["c"], [None, 3])                                      # доли — все ряды

    def test_texts_ru_en_and_dyn_keys(self):
        js = read("pages/exits-groups.js")
        ru, en = read("pages/exits-groups-i18n-ru.js"), read("pages/exits-groups-i18n-en.js")
        keys = set(re.findall(r't\("(ep\.(?:ord|method)\.[a-zA-Z_.]+)"', js))
        keys |= set("ep.ord.note." + x for x in ("rtt", "x_norm", "weights", "capacity", "even", "per_connection"))
        keys |= set("ep.ord.why." + x for x in ("degraded", "standby", "off", "unknown", "nometric"))
        keys |= {"ep.method.manual", "ep.methodInfo.manual"}
        for k in sorted(keys):
            if k == "ep.method.":
                continue
            self.assertIn('"%s":' % k, ru, k)
            self.assertIn('"%s":' % k, en, k)
        dyn = js[js.index("const DYN_KEYS"):js.index("const has = ")]
        for k in ("ep.method.manual", "ep.ord.note.rtt", "ep.ord.why.nometric", "ep.ord.note.per_connection"):
            self.assertIn('"%s"' % k, dyn)
        self.assertIn('"ep.ord.R": "Р{n}"', ru)
        self.assertIn('"ep.ord.R": "R{n}"', en)
        self.assertIn('"ep.method.manual": "По порядку (вручную)"', ru)
        self.assertIn('"ep.method.manual": "In order (manual)"', en)



@unittest.skipUnless(HAS_NODE, "нет node")
class OrderViewS6Test(unittest.TestCase):
    """PLAN-order Ш6 — вид по макету Claude Design «Порядок в группах» r1: причина ряда строкой над ним, полоска силы и
    число по способу, «было: …» у правленых строк и способа, шапка колонок, строки «Что изменится» (порядок изменён,
    шаги поменялись местами)."""
    scope = TiersOrderTest.scope

    def test_numbers_and_bars_by_method(self):
        out = run(self.scope(), r"""
const tr = tier("t1", ["k1", "k2", "k3", "k4"], "best_rtt");
const ok = (r) => ({ st: "ok", r }), V = (rk, mode, id, ei) => { const v = ordVal(rk, mode, id, ordCell(rk, mode, id, ei), ROWS[id], 10); return [v.bar === null ? null : Math.round(v.bar * 100), !!v.mute, v.num]; };
const W = ok({ by: "weights", rows: [[{ id: "k1", v: 3, share: 0.6 }], [{ id: "k2", v: 1, share: 0.2 }, { id: "k3", v: 1, share: 0.2 }]], out: [] });
const C = ok({ by: "capacity", rows: [[{ id: "k1", v: 420, share: 0.55 }]], out: [] });
const E = ok({ by: "even", rows: [[{ id: "k1", v: null, share: 0.25 }, { id: "k2", v: null, share: 0.25 }]], out: [] });
const N = ok({ by: "rtt", nodata: true, rows: [[{ id: "k1", v: null, share: 0.5 }, { id: "k2", v: null, share: 0.5 }]], out: [] });
console.log(JSON.stringify({
  rtt1: V(ok(RANKS.t1), "ranked", "k1", 0), rtt2: V(ok(RANKS.t1), "ranked", "k2", 3),
  w1: V(W, "ranked", "k1", 0), w2: V(W, "ranked", "k2", 1), cap: V(C, "ranked", "k1", 0), even: V(E, "ranked", "k2", 1), nod: V(N, "ranked", "k1", 0),
  free: V(ok(RANKS.t1), "free", "k2", 0), none: V({ st: "none", r: null }, "ranked", "k2", 0), changed: V({ st: "changed", r: RANKS.t1 }, "ranked", "k1", 0),
  why: [rowWhy(tr, RANKS.t1, 0), rowWhy(tr, RANKS.t1, 1), rowWhy(tr, W.r, 0), rowWhy(tr, W.r, 1), rowWhy(tr, E.r, 0), rowWhy(tr, N.r, 0),
    rowWhy(tr, { by: "per_connection", rows: [[]] }, 0), rowWhy(tr, Object.assign({}, RANKS.t1, { by: "x_norm" }), 0)],
  tol: tolMs(Object.assign({}, tr, { tolerance_pct: 20 }), { rows: [[{ v: 400 }]] }) }));
""")
        self.assertEqual(out["rtt1"], [100, False, 'ep.ms{"n":"11"}'])                  # лучший / свой
        self.assertEqual(out["rtt2"], [12, True, 'ep.ms{"n":"95"}'])                    # 11 / 95; Р2 — приглушённо
        self.assertEqual(out["w1"], [60, False, "60%"])                                # доля новых, вес — полем рядом
        self.assertEqual(out["w2"], [20, True, "20%"])
        self.assertEqual(out["cap"], [55, False, 'ep.mbps{"n":"420"} · 55%'])
        self.assertEqual(out["even"], [25, False, "≈ 25%"])
        self.assertEqual(out["nod"], [50, False, "≈ 50%"])
        self.assertEqual(out["free"], [10, False, 'ep.ms{"n":"97"}'])                  # ручной: справочно по отклику (10 / 97)
        self.assertEqual(out["none"], [None, False, 'ep.ms{"n":"97"}'])                # рядов нет — пустая дорожка
        self.assertEqual(out["changed"], [None, False, 'ep.ms{"n":"10"}'])             # правка до «Сохранить» — тоже
        self.assertEqual(out["why"], ['ep.ord.rw.rtt{"ms":"30"}', 'ep.ord.rw.queue{"n":1}', "ep.ord.rw.shares", "",
                                      "ep.method.even", "ep.ord.note.nodata_rtt", "ep.ord.rw.conn", ""])
        self.assertEqual(out["tol"], 80)                                                # 20 % от 400 мс больше 30 мс — как ядро

    def test_column_head_by_method_and_role(self):
        out = run(self.scope(), r"""
const txt = (li) => li.kids.map((k) => (k.a && k.a.text) || (k.kids && k.kids[0] && k.kids[0].a && k.kids[0].a.text) || "");
const ok = (r) => ({ st: "ok", r });
console.log(JSON.stringify({ rtt: txt(colHead(false, ok(RANKS.t1), "ranked")), w: txt(colHead(false, ok({ by: "weights", rows: [] }), "ranked"))[3],
  even: txt(colHead(false, ok({ by: "even", rows: [] }), "ranked"))[3], free: txt(colHead(false, ok(RANKS.t1), "free"))[3],
  wChanged: txt(colHead(false, { st: "changed", r: null }, "ranked", tier("t2", [], "weights")))[3],
  ru: txt(colHead(true, ok({ by: "even", rows: [] }), "ranked")) }));
""")
        self.assertEqual(out["rtt"], ["", "ep.ord.col.exit", "ep.ord.col.str", "ep.rtt", "ep.ord.col.dev", "ep.ord.col.tier"])
        self.assertEqual((out["w"], out["even"], out["free"], out["wChanged"]), ("ep.ord.col.w", "ep.ord.col.share", "ep.rtt", "ep.ord.col.w"))
        self.assertEqual(out["ru"], ["", "ep.ru.key", "ep.chStates", "ep.rtt", "ep.ord.col.dev", "ep.ord.col.tier"])   # силы у шагов нет

    def test_was_on_method_and_steps_and_per_connection_reason(self):
        out = run(self.scope(), r"""
form(home()); LIVE = { main: { active_tier: "t1", ranks: RANKS } };
forms.main.draft.tiers[1].method = "weights";                                   // способ уровня 2 поменяли, не сохранили
let box = tiersBlock("main");
const heads = find(box, (x) => x.a && x.a["data-ord-head"]).map((x) => x.a.class);
const was = find(box, (x) => cls(x, "ko-was")).map((x) => x.a.text);
const g = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", ["r2"])]); form(g);
btnOf(tiersBlock("g-a"), 'ep.upName{"name":"VPS"}').fn();                       // шаг 2 ↑ — местами
box = tiersBlock("g-a");
const ru = { vps: descOf(box, "r2"), router: descOf(box, "r1"), dirty: find(box, (x) => cls(x, "is-dirty")).length, why: whys(box), bars: find(box, (x) => cls(x, "ko-bar")).length };
const pc = home(); pc.per_connection = true; form(pc);
LIVE = { main: { active_tier: "t1", ranks: { t1: { by: "per_connection", queue: false, order: "auto", nodata: false, out: [],
  rows: [["k1", "k2", "k3", "k4", "k5"].map((id) => ({ id, v: null, share: 0.2 }))] } } } };
box = tiersBlock("main");
console.log(JSON.stringify({ heads, was, ru, pcWhy: whys(box), pcNotes: notes(box), pcNum: find(box, (x) => cls(x, "kk-eg-rtt"))[0].a.text }));
""")
        self.assertEqual(out["heads"], ["kk-eg-tier__h", "kk-eg-tier__h is-dirty"])
        self.assertEqual(out["was"], ['ep.ord.was{"x":"ep.method.even"}'])
        self.assertTrue(out["ru"]["vps"].startswith('ep.ord.was{"x":"ep.ru.stepN{\\"n\\":2}"}'), out["ru"])
        self.assertTrue(out["ru"]["router"].startswith('ep.ord.was{"x":"ep.ru.stepN{\\"n\\":1}"}'), out["ru"])
        self.assertEqual((out["ru"]["dirty"], out["ru"]["why"], out["ru"]["bars"]), (2, [], 0))  # во внутренней группе силы нет
        self.assertEqual(out["pcWhy"], ["ep.ord.rw.conn"])                                # «поровну по соединениям» — у ряда
        self.assertEqual(out["pcNotes"], ["ep.ord.note.per_connection"])                  # у уровня 2 рядов нет — словами
        self.assertEqual(out["pcNum"], "≈ 20%")

    def test_impact_order_changed_and_steps_swapped(self):
        out = run(self.scope(), r"""
const g = home(); g.tiers[0].order = "manual"; form(g);
forms.main.draft.tiers[0].exits = ["k2", "k1", "k3", "k4", "k5"];
const man = ordImpact(forms.main);
const r = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", ["r2"]), tier("t3", ["r3"])]); form(r);
forms["g-a"].draft.tiers = [forms["g-a"].draft.tiers[1], forms["g-a"].draft.tiers[0], forms["g-a"].draft.tiers[2]];
const swap = ordImpact(forms["g-a"]);
forms["g-a"].draft.tiers = [forms["g-a"].base.tiers[2], forms["g-a"].base.tiers[0], forms["g-a"].base.tiers[1]].map(copy);
const rot = ordImpact(forms["g-a"]);
console.log(JSON.stringify({ man, swap, rot }));
""")
        self.assertEqual(out["man"], ['ep.ord.saveOrder{"n":"ep.tierN{\\"n\\":1}"}'])
        # макет В1 · 2 — «шаги местами»; первым стал другой шаг — и строка о возврате на первый (REV-order П-4: раньше
        # после обмена функция выходила и её не было)
        self.assertEqual(out["swap"][0], 'ep.ord.ruSwap{"g":"«g-a»","a":"1","b":"2"}')
        self.assertEqual(len(out["swap"]), 2, out["swap"])
        self.assertTrue(out["swap"][1].startswith("ep.ord.saveTiers{"), out["swap"])
        self.assertEqual(len(out["rot"]), 1)                                               # не обмен двух — прежняя строка
        self.assertTrue(out["rot"][0].startswith("ep.ord.saveTiers"), out["rot"])

    def test_texts_ru_en(self):
        ru, en = read("pages/exits-groups-i18n-ru.js"), read("pages/exits-groups-i18n-en.js")
        for k, r, e in (("ep.ord.rw.rtt", "равны: до +{ms} мс от лучшего", "equal: within +{ms} ms of the best"),
                        ("ep.ord.rw.queue", "очередь: если Р{n} выпадет", "queue: used if R{n} drops out"),
                        ("ep.ord.rw.shares", "доли — новые идут во все ряды", "shares — new devices go to every row"),
                        ("ep.ord.rw.conn", "поровну по соединениям", "evenly per connection"),
                        ("ep.ord.out", "Не участвуют · {n}", "Not taking part · {n}"),
                        ("ep.ord.was", "было: {x}", "was: {x}"),
                        ("ep.ord.saveOrder", "«{n}»: порядок изменён", "“{n}”: order changed"),
                        ("ep.ord.ruSwap", "«{g}»: шаги {a} и {b} поменялись местами", "“{g}”: steps {a} and {b} swapped")):
            self.assertIn('"%s": "%s"' % (k, r), ru)
            self.assertIn('"%s": "%s"' % (k, e), en)
        js = read("pages/exits-groups.js")
        for k in set(re.findall(r't\("(ep\.ord\.(?:rw|col)\.[a-z_]+|ep\.ord\.msL)"', js)):
            self.assertIn('"%s":' % k, ru, k)
            self.assertIn('"%s":' % k, en, k)


@unittest.skipUnless(HAS_NODE, "нет node")
class OrderReviewFixTest(unittest.TestCase):
    """REV-order (29.10) — правки панели по проверке Ш1–Ш6: ручной уровень с нерабочими выходами (С-3), бросок на ключ
    другого шага внутренней группы — «в один шаг с …» и строка в «Что изменится» (С-2), ряды от прежнего способа после
    «Сохранить» (З-1), фокус после ↑↓ у одноимённых выходов (П-1), ручка «нажата», пока поднята (П-2), ссылка «Тест
    скорости» (П-5). Бросок вне уровней (С-1) — tests/test_panel_order_kit.py и настоящий Chrome."""

    def scope(self):
        js = read("pages/exits-groups.js")
        return TiersOrderTest.scope(self) + [jsparts.const(js, "MORE_OPEN"), jsparts.function(js, "toSpeed")]

    def test_manual_tier_with_an_exit_out_is_a_plain_queue(self):
        """С-3: ручной уровень [рабочий, ухудшен, рабочий] без правок — ни «Не участвуют» посреди очереди, ни «было», ни
        подложки правки; номер места — и у ухудшенного (причина — фишкой). После ↓ у первого — «было» у двух сдвинутых."""
        out = run(self.scope(), r"""
const g = home(); g.tiers[0] = Object.assign(tier("t1", ["k1", "k5", "k3"], "best_rtt"), { order: "manual" }); form(g);
LIVE = { main: { active_tier: "t1", ranks: { t1: { by: "manual", queue: true, order: "manual", klass: "ok", nodata: false,
  rows: [[{ id: "k1", v: 1, share: 1 }], [{ id: "k3", v: 2, share: 0 }]], out: [{ id: "k5", why: "degraded" }] } } } };
const look = (box) => ({ outh: find(box, (x) => cls(x, "kk-eg-outh")).length, dirty: find(box, (x) => cls(x, "is-dirty")).length,
  rows: rowsOf(box).slice(0, 3), tags: tags(box).slice(0, 3), desc: ["k1", "k5", "k3"].map((id) => descOf(box, id)),
  chip5: (find(rowEl(box, "k5"), (x) => x.chip !== undefined)[0] || {}).chip || null });
SELECTS.length = 0;
let box = tiersBlock("main");
const a = look(box), title = SELECTS[0].title;
btnOf(box, 'ep.downName{"name":"Ключ 1"}').fn();
const b = look(tiersBlock("main"));
console.log(JSON.stringify({ a, b, title }));
""")
        a, b = out["a"], out["b"]
        self.assertEqual((a["outh"], a["dirty"]), (0, 0), a)                     # до правки: «Не участвуют · 1», подложка у k5
        self.assertEqual(a["desc"], ["awg", "awg", "awg"])                        # до правки у k5 «было: 2» без правки
        self.assertEqual(a["rows"], ["k1", "k5", "k3"])
        self.assertEqual(a["tags"], ["1", "2", "3"])                              # место в очереди — и у ухудшенного
        self.assertEqual(a["chip5"], "ep.st.degraded")                             # причина — фишкой на месте строки
        self.assertEqual(b["rows"], ["k5", "k1", "k3"])
        self.assertEqual(b["dirty"], 2)
        self.assertEqual(b["desc"], ['ep.ord.was{"x":"1"} · awg', 'ep.ord.was{"x":"2"} · awg', "awg"])   # k1, k5, k3
        self.assertEqual(b["outh"], 0)
        self.assertEqual(out["title"], "ep.method.manual")                         # отклонение 6: полное имя подсказкой

    def test_drop_on_a_key_of_another_step_is_signed_and_listed(self):
        """С-2 (вариант 1, план §4 и §7 п. 4, макет В2): бросок на ключ другого шага — подпись «в один шаг с «…»» и шаг
        подсвечен целиком (target), а не «встанет по силе»; на шапку шага — обмен, без подписи; в «Что изменится» —
        строка о слиянии. У обычной группы бросок в автоуровень — по-прежнему «встанет по силе»."""
        out = run(self.scope(), r"""
const r = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", ["r2"]), tier("t3", ["r3"])]); form(r);
const m = ordModel("g-a");
const onKey = ordTip("g-a", m, { s: 1, i: 0 }, { s: 0, i: 1 }, { s: 0, i: 0, after: true });
const onHead = ordTip("g-a", m, { s: 1, i: 0 }, { gap: 0 }, { s: 0, head: true });
ordMove("g-a", { s: 1, i: 0 }, { s: 0, i: 1 }, "drag");
const tiers = forms["g-a"].draft.tiers.map((x) => x.id + ":" + x.exits.join(","));
const imp = ordImpact(forms["g-a"]);
form(home());
const zone = ordTip("main", ordModel("main"), { s: 0, i: 0 }, { s: 1, i: 0 }, { s: 1, i: 0 });
console.log(JSON.stringify({ onKey, onHead, tiers, imp, zone }));
""")
        self.assertEqual(out["onKey"], {"text": 'ep.ord.oneStep{"name":"Роутер"}', "target": True})   # до правки: byStrength, zone
        self.assertEqual(out["onHead"], "")
        self.assertEqual(out["tiers"], ["t1:r1,r2", "t3:r3"])
        self.assertIn('ep.ord.ruJoin{"g":"«g-a»","a":"VPS","b":"Роутер"}', out["imp"])            # до правки — ни слова
        self.assertEqual(out["zone"], {"text": "ep.ord.byStrength", "zone": True})

    def test_rows_of_the_old_method_are_not_shown_as_fresh(self):
        """З-1: после «Сохранить» база новая, а ряды сервера — до круга сборщика старые: не тот флаг порядка или способ —
        «пересчитаются после сохранения», а не чужие ряды с метками."""
        out = run(self.scope(), r"""
const st = (r, mut) => { const g = home(); if (mut) mut(g); form(g); LIVE = { main: { active_tier: "t1", ranks: { t1: r } } }; return rankOf("main", forms.main.draft.tiers[0]).st; };
const R = RANKS.t1, w = (o) => Object.assign({}, R, o);
const res = { ok: st(R), manualRows: st(w({ by: "manual", order: "manual" })), wantManual: st(R, (g) => { g.tiers[0].order = "manual"; }),
  weights: st(R, (g) => { g.tiers[0].method = "weights"; }), xnorm: st(w({ by: "x_norm" })),
  empty: st({ by: "even", queue: false, order: "auto", klass: null, nodata: false, rows: [], out: [{ id: "k1", why: "off" }] }),
  conn: st(w({ by: "per_connection", order: "auto" }), (g) => { g.per_connection = true; }),
  connFlag: st(w({ by: "per_connection", order: "auto" }), (g) => { g.per_connection = true; g.tiers[0].order = "manual"; }),
  manOk: st(w({ by: "manual", order: "manual" }), (g) => { g.tiers[0].order = "manual"; }) };
form(home()); LIVE = { main: { active_tier: "t1", ranks: { t1: w({ by: "manual", order: "manual" }) } } };
const box = tiersBlock("main");
res.notes = notes(box); res.tags = tags(box).slice(0, 4);
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["ok"], "ok")
        self.assertEqual(out["manualRows"], "changed")                              # до правки — «ok» с рядами «вручную»
        self.assertEqual(out["wantManual"], "changed")
        self.assertEqual(out["weights"], "changed")
        self.assertEqual(out["xnorm"], "ok")                                        # «Лучший отклик» к своей норме — свой
        self.assertEqual(out["empty"], "ok")                                        # участников нет — ядро пишет even
        self.assertEqual((out["conn"], out["connFlag"], out["manOk"]), ("ok", "ok", "ok"))
        self.assertEqual(out["notes"][0], "ep.ord.note.changed")
        self.assertEqual(out["tags"], ["", "", "", ""])                             # без «Р1…Р4» от чужих рядов

    def test_arrow_focus_stays_in_its_own_row_with_twin_names(self):
        """П-1: два выхода «Ключ 1» в разных уровнях — после ↓ фокус на стрелке той строки, которую двигали."""
        out = run(self.scope(), r"""
ROWS.d1 = Object.assign({}, ROWS.k1, { id: "d1" });                            // то же имя «Ключ 1»
const g = grp("main", "abroad", [tier("t1", ["k1", "k3"]), tier("t2", ["d1", "k4"])]); form(g);
const FOCUS = [], order = () => forms.main.draft.tiers.flatMap((x) => x.exits);
const lab = (id) => ['ep.upName{"name":"' + exitName(id) + '"}', 'ep.downName{"name":"' + exitName(id) + '"}'];
const btn = (who) => ({ disabled: false, focus() { FOCUS.push(who); } });
forms.main.card = { querySelector(sel) {
  let m = /^\[data-ord-row="([^"]+)"\] button\[aria-label="(.+)"\]$/.exec(sel);
  if (m) return order().indexOf(m[1]) >= 0 && lab(m[1]).indexOf(m[2]) >= 0 ? btn(m[1]) : null;
  m = /^button\[aria-label="(.+)"\]$/.exec(sel);                               // по всей карточке — первая по порядку
  if (m) { const id = order().find((x) => lab(x).indexOf(m[1]) >= 0); return id ? btn(id) : null; }
  m = /^\[data-ord-grip="(.+)"\]$/.exec(sel);
  return m ? btn("grip:" + m[1]) : null; } };
const downs = find(tiersBlock("main"), (x) => x.o && x.o.aria === 'ep.downName{"name":"Ключ 1"}');
downs[1].fn();                                                                  // ↓ у «Ключ 1» второго уровня (d1)
console.log(JSON.stringify({ n: downs.length, focus: FOCUS, t2: forms.main.draft.tiers[1].exits }));
""")
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["t2"], ["k4", "d1"])
        self.assertEqual(out["focus"], ["d1"])                                      # до правки — «k1» (первая по карточке)

    def test_lifted_grip_is_pressed(self):
        """П-2: ручка поднятой строки и поднятого уровня — aria-pressed (перерисовка после хода сохраняет состояние)."""
        out = run(self.scope(), r"""
const g = home(); g.tiers[1].name = "Запасной"; form(g);
LIFTED = "k3"; let grips = find(tiersBlock("main"), (x) => x.grip !== undefined);
const rows = grips.filter((x) => x.pressed).map((x) => x.grip);
LIFTED = "@t2"; grips = find(tiersBlock("main"), (x) => x.grip !== undefined);
const tiersP = grips.filter((x) => x.pressed).map((x) => x.grip);
LIFTED = null;
console.log(JSON.stringify({ rows, tiersP }));
""")
        self.assertEqual(out["rows"], ["k3"])
        self.assertEqual(out["tiersP"], ["@t2"])

    def test_speed_test_link_next_to_no_speed_test(self):
        """П-5 (макет П6 · 5): «Поровну — нет теста скорости» — рядом «Тест скорости»: раскрывает «Ещё» группы и ведёт к
        переключателю теста скорости группы."""
        out = run(self.scope(), r"""
const g = home(); g.tiers[0].method = "capacity"; form(g);
LIVE = { main: { active_tier: "t1", ranks: { t1: { by: "capacity", queue: false, order: "auto", klass: "ok", nodata: true,
  rows: [["k1", "k2", "k3", "k4"].map((id) => ({ id, v: null, share: 0.25 }))], out: [{ id: "k5", why: "off" }] } } } };
const links = find(tiersBlock("main"), (x) => x.btn === "ep.ord.speedL");
const FOC = [];
forms.main.card = { querySelector: (sel) => (sel === "[data-eg-speed]" ? { scrollIntoView() { FOC.push("scroll"); }, focus() { FOC.push("focus"); } } : null) };
links[0].fn();
LIVE = { main: { active_tier: "t1", ranks: RANKS } }; form(home());
const none = find(tiersBlock("main"), (x) => x.btn === "ep.ord.speedL").length;
console.log(JSON.stringify({ n: links.length, kind: links[0].kind, more: MORE_OPEN.main === true, edits: EDITS.filter((e) => e[0] === "render"), FOC, none }));
""")
        self.assertEqual((out["n"], out["kind"]), (1, "link"))                      # до правки ссылки нет
        self.assertTrue(out["more"])
        self.assertEqual(out["edits"], [["render", "main"]])
        self.assertEqual(out["FOC"], ["scroll", "focus"])
        self.assertEqual(out["none"], 0)                                            # у «Лучшего отклика» — нет


if __name__ == "__main__":
    unittest.main()
