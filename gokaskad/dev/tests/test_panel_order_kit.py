"""PLAN-order Ш4: компонент порядка в node — Kit.orderMove / orderStep / orderTarget (kit.js, чистые) и Kit.orderList на
поддельном DOM: ход внутри ряда и через ряд (автоуровень становится ручным), в другой уровень (режимы не меняются), «по
соединениям» — внутри отказ, наружу можно; внутренняя группа «по ключу на шаг» — стрелки меняют шаги местами, ключ из
шага с несколькими — своим шагом рядом, бросок на шаг — явно в него; перестановка уровней; цель указателя; клавиатура:
пробел — взять, стрелки — ход, Esc — всё как было, пробел — положить. «Уровни» групп — tests/test_panel_order.py,
настоящий Chrome — tests/test_panel_order_chrome.py."""

import re
import shutil
import unittest

from tests.test_ru_groups_card import read, run

HAS_NODE = shutil.which("node") is not None


def kit_run(script):
    """Только kit.js (window.Kit) в node; script печатает JSON последней строкой."""
    return run(["globalThis.window = globalThis;", read("kit.js")], script)


@unittest.skipUnless(HAS_NODE, "нет node")
class OrderModelTest(unittest.TestCase):
    def test_moves(self):
        out = kit_run(r"""
const M = (steps, ...secs) => ({ steps, sections: secs.map(([id, mode, items]) => ({ id, mode, items })) });
const ids = (r) => r.model.sections.map((s) => s.id + ":" + s.mode + ":" + s.items.join(","));
const K2 = window.Kit, res = {};
const auto = M(false, ["t1", "ranked", ["k1", "k3", "k4", "k2"]], ["t2", "ranked", ["x1"]]);
let r = K2.orderMove(auto, { s: 0, i: 2 }, { s: 0, i: 1 });                 // внутри ряда Р1: k4 выше k3
res.inRow = [r.ok, ids(r), r.manual];
r = K2.orderMove(auto, { s: 0, i: 3 }, { s: 0, i: 0 });                     // через ряд: k2 (Р2) — первым
res.across = [r.ok, ids(r), r.manual];
r = K2.orderMove(auto, { s: 0, i: 0 }, { s: 1, i: 1 });                     // в другой уровень: режимы как были
res.toTier = [r.ok, ids(r), r.manual];
res.same = K2.orderMove(auto, { s: 0, i: 1 }, { s: 0, i: 2 }).why;           // «перед следующим» = на месте
const conn = M(false, ["t1", "locked", ["a", "b"]], ["t2", "ranked", ["c"]]);
res.locked = K2.orderMove(conn, { s: 0, i: 1 }, { s: 0, i: 0 }).why;
r = K2.orderMove(conn, { s: 0, i: 0 }, { s: 1, i: 0 });
res.lockedOut = [r.ok, ids(r)];
r = K2.orderMove(auto, { s: 1 }, { gap: 0 });                               // уровень целиком — первым
res.tierUp = [r.ok, r.model.sections.map((s) => s.id)];
res.tierSame = K2.orderMove(auto, { s: 0 }, { gap: 1 }).why;
const steps = M(true, ["t1", "ranked", ["r1"]], ["t2", "ranked", ["r2"]], ["t3", "ranked", ["r3", "r4"]]);
const st = (at, dir) => { const to = K2.orderStep(steps, at, dir); return to ? [to, ids(K2.orderMove(steps, at, to, { max: 10, newId: () => "t9" }))] : null; };
res.stepUp = st({ s: 1, i: 0 }, -1);                                         // шаг 2 ↑ — шаги местами
res.stepDown = st({ s: 0, i: 0 }, 1);
res.stepTop = st({ s: 0, i: 0 }, -1);
res.multiUp = st({ s: 2, i: 0 }, -1);                                        // ключ из шага с двумя — своим шагом
res.multiIn = st({ s: 2, i: 1 }, -1);                                        // внутри шага — местами
res.join = ids(K2.orderMove(steps, { s: 0, i: 0 }, { s: 2, i: 2 }));         // бросок на шаг — явно в него, пустой шаг уходит
res.joinDropped = K2.orderMove(steps, { s: 0, i: 0 }, { s: 2, i: 2 }).dropped;
res.full = K2.orderMove(steps, { s: 2, i: 0 }, { gap: 2 }, { max: 3, newId: () => "t9" }).why;
const flat = M(false, ["t1", "ranked", ["a", "b"]], ["t2", "free", ["c"]]);
res.arrowsFlat = [K2.orderStep(flat, { s: 0, i: 1 }, 1), K2.orderStep(flat, { s: 1, i: 0 }, -1), K2.orderStep(flat, { s: 0, i: 0 }, -1), K2.orderStep(flat, { s: 1, i: 0 }, 1)];
res.target = [K2.orderTarget(flat, { s: 0, i: 0 }, { s: 1, i: 0, after: true }), K2.orderTarget(flat, { s: 0, i: 0 }, { s: 1, head: true }),
  K2.orderTarget(flat, { s: 0, i: 0 }, { end: true }), K2.orderTarget(steps, { s: 0, i: 0 }, { s: 2, head: true }), K2.orderTarget(flat, { s: 1 }, { s: 0, after: false })];
res.at = [K2.orderAt(flat, "c"), K2.orderAt(flat, "@t2"), K2.orderAt(flat, "zz")];
// REV-order С-1: вне списка уровней — ход {out: true}, orderMove отказывает «outside» (и строке, и уровню целиком)
res.out = [K2.orderTarget(flat, { s: 0, i: 0 }, { out: true }), K2.orderMove(flat, { s: 0, i: 0 }, { out: true }).why,
  K2.orderMove(flat, { s: 1 }, K2.orderTarget(flat, { s: 1 }, { out: true })).why, K2.orderMove(steps, { s: 0, i: 0 }, { out: true }).ok];
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["inRow"], [True, ["t1:free:k1,k4,k3,k2", "t2:ranked:x1"], ["t1"]])
        self.assertEqual(out["across"], [True, ["t1:free:k2,k1,k3,k4", "t2:ranked:x1"], ["t1"]])
        self.assertEqual(out["toTier"], [True, ["t1:ranked:k3,k4,k2", "t2:ranked:x1,k1"], []])
        self.assertEqual(out["same"], "same")
        self.assertEqual(out["locked"], "locked")
        self.assertEqual(out["lockedOut"], [True, ["t1:locked:b", "t2:ranked:a,c"]])
        self.assertEqual(out["tierUp"], [True, ["t2", "t1"]])
        self.assertEqual(out["tierSame"], "same")
        self.assertEqual(out["stepUp"], [{"gap": 0}, ["t2:ranked:r2", "t1:ranked:r1", "t3:ranked:r3,r4"]])
        self.assertEqual(out["stepDown"], [{"gap": 2}, ["t2:ranked:r2", "t1:ranked:r1", "t3:ranked:r3,r4"]])
        self.assertIsNone(out["stepTop"])
        self.assertEqual(out["multiUp"], [{"gap": 2}, ["t1:ranked:r1", "t2:ranked:r2", "t9:ranked:r3", "t3:ranked:r4"]])
        self.assertEqual(out["multiIn"], [{"s": 2, "i": 0}, ["t1:ranked:r1", "t2:ranked:r2", "t3:free:r4,r3"]])
        self.assertEqual(out["join"], ["t2:ranked:r2", "t3:ranked:r3,r4,r1"])
        self.assertEqual(out["joinDropped"], ["t1"])
        self.assertEqual(out["full"], "full")
        self.assertEqual(out["arrowsFlat"], [{"s": 1, "i": 0}, {"s": 0, "i": 2}, None, None])   # на краю уровня — в соседний
        self.assertEqual(out["target"], [{"s": 1, "i": 1}, {"s": 1, "i": 0}, {"s": 1, "i": 1}, {"gap": 2}, {"gap": 0}])
        self.assertEqual(out["at"], [{"s": 1, "i": 0}, {"s": 1}, None])
        self.assertEqual(out["out"], [{"out": True}, "outside", "outside", False])      # до правки: end — в последний уровень

    def test_keyboard_sequence_and_cancel(self):
        """Пробел — взять, ↑ ↑ — два хода (каждый — onMove, данные перерисованы), Esc — данные до подъёма; снова взять,
        ↓, пробел — положить; диктор слышит место; «по соединениям» — ручку не взять."""
        out = kit_run(r"""
const SAID = [];
globalThis.document = { body: { append(n) { n.isConnected = true; } }, addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [] };
const mkEl = (tag, a, ...kids) => { const n = { tag, a: a || {}, kids, isConnected: false };
  Object.defineProperty(n, "textContent", { set(v) { if (v) SAID.push(v); }, get() { return ""; } }); return n; };
window.Kit.init({ el: mkEl, t: (k, v) => k + (v ? JSON.stringify(v) : ""), tn: (k, n) => k + n, icon: () => null, isPhone: () => false, U: {},
  layers: { openDialog() {}, openSheet() {}, openMenu() {}, confirmDialog() {}, typedConfirm() {}, toast() {} } });
let data = { steps: false, sections: [{ id: "t1", mode: "ranked", items: ["a", "b", "c"] }] };
const MOVES = [];
const cls = () => ({ set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); } });
const row = { classList: cls() };
const PRESSED = {};
const grip = (key, dis) => ({ getAttribute: (k) => (k === "data-ord-grip" ? key : k === "aria-disabled" ? (dis ? "true" : null) : null), title: "нельзя",
  setAttribute: (k, v) => { if (k === "aria-pressed") PRESSED[key] = v; }, closest: (sel) => (sel === "[data-ord-grip]" ? grip(key, dis) : row) });
const L = {}; const root = { setAttribute() {}, addEventListener(n, f) { L[n] = f; }, contains: () => true };
window.Kit.orderList({ root, key: "eg:main", model: () => data, name: (k) => k, where: (m, at) => "уровень " + (at.s + 1),
  onMove: (from, to) => { const r = window.Kit.orderMove(data, from, to); MOVES.push([from, to]); data = r.model; },
  snapshot: () => JSON.parse(JSON.stringify(data)), restore: (s) => { data = s; } });
const key = (k, g) => { let stopped = false; L.keydown({ key: k, target: g || grip("c"), preventDefault() { stopped = true; } }); return stopped; };
const order = () => data.sections[0].items.join("") + ":" + data.sections[0].mode;
const res = {};
key(" "); res.lifted = [window.Kit.orderLifted("eg:main"), row.classList.set.has("is-lifted"), PRESSED.c];
const g0 = window.Kit.orderGrip({ key: "a", name: "A" }), g1 = window.Kit.orderGrip({ key: "b", name: "B", pressed: true });
res.grips = [g0.a["aria-pressed"], g1.a["aria-pressed"]];
key("ArrowUp"); key("ArrowUp"); res.afterUp = order();
res.top = key("ArrowUp") && order();                                         // дальше некуда: ход не сделан
key("Escape"); res.cancel = [order(), window.Kit.orderLifted("eg:main")];
key(" "); key("ArrowUp"); key("Enter"); res.drop = [order(), window.Kit.orderLifted("eg:main")];
key(" ", grip("a")); key("End", grip("a")); key(" ", grip("a")); res.end = order();
key(" ", grip("b", true)); res.dis = window.Kit.orderLifted("eg:main");
res.moves = MOVES.length; res.said = SAID;
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["lifted"], ["c", True, "true"])                # REV-order П-2: поднята — aria-pressed
        self.assertEqual(out["grips"], ["false", "true"])
        self.assertEqual(out["afterUp"], "cab:free")                       # автоуровень стал ручным на первом ходе
        self.assertEqual(out["top"], "cab:free")
        self.assertEqual(out["cancel"], ["abc:ranked", None])               # Esc — всё как было, и флаг тоже
        self.assertEqual(out["drop"], ["acb:free", None])
        self.assertEqual(out["end"], "cba:free")
        self.assertIsNone(out["dis"])
        self.assertEqual(out["moves"], 4)
        said = out["said"]
        self.assertTrue(said[0].startswith("kit.ord.lifted") and "уровень 1" in said[0] and '\\"i\\":3' in said[0], said[0])
        self.assertIn("kit.ord.cancel", " ".join(said))
        self.assertIn("kit.ord.dropped", " ".join(said))
        self.assertEqual(said[-1], "нельзя")                                 # «по соединениям»: подсказка ручки

    def test_texts_ru_en(self):
        for lang in ("ru", "en"):
            src = read("kit-i18n-%s.js" % lang)
            for k in ("kit.ord.grip", "kit.ord.role", "kit.ord.tip", "kit.ord.at", "kit.ord.lifted", "kit.ord.dropped", "kit.ord.cancel",
                      "kit.ord.no", "kit.ord.full"):
                self.assertIn('"%s":' % k, src, (lang, k))
        kit = read("kit.js")
        for k in re.findall(r'h\.t\("(kit\.ord\.[a-zA-Z]+)"', kit):
            self.assertIn('"%s":' % k, read("kit-i18n-ru.js"), k)
        self.assertIn('"grip": [F, ', read("icons.js"))


if __name__ == "__main__":
    unittest.main()
