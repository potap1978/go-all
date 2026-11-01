"""РФ-группы, Ш1–Ш2 и пачка А п. 3 (29.10, BRIEF-ru-groups-impl): российская группа — общей карточкой «Групп»
(pages/exits-groups.js в node, настоящие функции через tests/jsparts.py, элементы и кнопки — заглушки).

* Ш1: в уровне — колонка «Устройств» (у обеих ролей); у российской — «Добавить ключ в «<группа>»» с её gid (REV-plan
  С-3), подсказка «группа или уровень»; «По скорости» — только при замере скорости группы (сохранённый — как есть);
  способ выбора — не поле набора: «Дом» остаётся «Домом»; «сейчас: …» при двух годных ключах уровня — «уровень · 2
  ключа» (З-5); факты группы — в карточке, «Добавить условие» кладёт условие «Страна не та» в черновик; переход к
  российской группе — раскрыть её карточку (focus), а не скрытый редактор.
* С-1 (вариант а): ночная перебалансировка у российской группы — на виду, у зарубежной — под «Ещё»; подсказка про
  уже подключённые устройства — когда в уровне сменили способ или добавили ключ.
* Ш2 / З-2: «Куда встают новые» — last | first | уровень; убрали выбранный уровень — поле снова «last»; форма не
  сохраняет политику с несуществующим уровнем.
* Пачка А п. 3: редкие переключатели «Поведения» — под «Ещё»; «Ещё» раскрыто само, если там что-то включено."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


STUB = r"""
const LOG = [];
const t = (k, v) => k + (v ? JSON.stringify(v) : "");
const mk = (tag, a, kids) => ({ tag, a: a || {}, kids: kids.filter((k) => k !== null && k !== undefined && k !== false), on: {},
  addEventListener(ev, fn) { this.on[ev] = fn; }, setAttribute(k, v) { this.a[k] = v; }, classList: { add() {}, toggle() {} },
  dataset: {}, append(...k) { this.kids.push(...k.filter((x) => x !== null && x !== undefined)); }, querySelector: () => null, isConnected: true });
const el = (tag, a, ...kids) => mk(tag, a, kids);
const SELECTS = [];
const U = {
  select: (items, v, on, o) => { const s = { select: items.map((x) => x.v), value: v, on, o }; SELECTS.push(s); return s; },
  menu: (items, o) => ({ menu: items, o }),
  btn: (label, kind, fn, o) => ({ btn: label, kind, fn, o: o || {}, dataset: {}, setAttribute() {} }),
  input: (o) => ({ input: o, addEventListener() {} }),
  chip: (text, tone) => ({ chip: text, tone }),
};
let LIVE = null;
const K = { icon: (n) => ({ icon: n }), tn: (k, n) => k + "#" + n, fmtInt: String, fmtNum1: String, liveMark: (k) => (k === "groups" ? LIVE : null),
  goNav: (...a) => LOG.push(["goNav"].concat(a)), addKey: (o) => LOG.push(["addKey", o.group]) };
const CSS = { escape: String };
const ROWS = {
  r1: { id: "r1", name: "Роутер", kind: "awg", pstate: "ok", enabled: true, rttMs: 12, devices: 2 },
  r2: { id: "r2", name: "VPS", kind: "vless", pstate: "ok", enabled: true, rttMs: 30, devices: 0 },
  r3: { id: "r3", name: "Дача", kind: "awg", pstate: "off", enabled: true, rttMs: null, devices: 0 },
};
const rowOf = (id) => ROWS[id] || null;
const exitName = (id) => (ROWS[id] ? ROWS[id].name : id);
const kindText = (r) => r.kind;
const stateChip = (r) => ({ chip: r.pstate });
const methodInfo = () => ({ info: true });
const numIn = () => ({ num: true });
const groupLabel = (gid) => "«" + gid + "»";
const unsavedWhy = () => "";
const addExitDialog = (gid) => LOG.push(["addExit", gid]);
const addKey = (gid) => (K.addKey ? K.addKey({ group: gid }) : null);
const openExit = () => {}, moveDialog = () => {}, removeDialog = () => {}, moveIn = () => {}, moveTo = () => {}, tierRename = () => {}, byProto = () => {};
const tier = (id, exits, method) => ({ id, name: "", exits, method: method || "even", weights: {}, tolerance_ms: 30, tolerance_pct: 20 });
const grp = (id, role, tiers, extra) => Object.assign({ id, role, preset: role === "ru" ? "ru_home" : "standard", tiers, new_exits_tier: "last", fail: role === "ru" ? "direct" : "block",
  evict_degraded: true, return_to_primary: { enabled: true, hold_min: 10 }, night_rebalance: { enabled: false, at: "04:00" }, drop_on_degrade: false, per_connection: false, flap_standby: true,
  rules: [], checks: { freq: { mode: "often" }, speed: { enabled: false, every_h: 6 }, services: [], geo: { enabled: true, every_h: 24, expect: role === "ru" ? "RU" : null } } }, extra || {});
const copy = (v) => JSON.parse(JSON.stringify(v));
const forms = {};
const form = (g) => { forms[g.id] = { base: copy(g), draft: copy(g), list: [], card: el("section", {}) }; return forms[g.id]; };
const EDITS = [];
const edited = (gid) => EDITS.push(["edited", gid]), changed = (gid) => EDITS.push(["changed", gid]);
const renderCard = (gid) => EDITS.push(["render", gid]);
const focusIn = () => {};
const sub = (title) => ({ sub: title });
const touchPreset = (g) => { if (g.preset !== "custom") g.preset = "custom"; };
const scnBlock = () => null;
const swLive = (title, desc, get, set) => ({ sw: title, desc, get, set });
const walk = (n, fn) => { if (!n || typeof n !== "object") return; fn(n); (n.kids || []).forEach((k) => walk(k, fn)); };
const find = (n, pred) => { const out = []; walk(n, (x) => { if (pred(x)) out.push(x); }); return out; };
"""


def run(parts, script):
    src = STUB + "\n" + "\n".join(parts) + "\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify(" \
        "{crash: String(e && e.stack || e)})); });\n" % script
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


# PLAN-order Ш4–Ш5: «Уровни» зовут порядок (Kit.orderMove … настоящие из kit.js; ручка и поведение списка — заглушки:
# ORDER_LISTS — что передали Kit.orderList, LIFTED — какой ключ «поднят»)
ORDER_CONSTS = ("perConn", "tierMode", "sameSet", "ORD_SHOWN", "ordHeld", "ordOpts", "OUT_TONE", "BY_OF")
ORDER_FUNCS = ("ranksFit", "rankOf", "shownOrder", "ordModel", "ordApply", "ordMove", "ordArrow", "setMethod", "autoBack", "ordNote", "ordImpact",
               "ordCell", "ordTip", "tolMs", "rowWhy", "ordVal", "ordBar", "wasOf", "colHead")


def order_scope():
    js = read("pages/exits-groups.js")
    return (["globalThis.window = globalThis;", read("kit.js"),
             "const ORDER_LISTS = []; let LIFTED = null;"
             "const Kit = Object.assign({}, window.Kit, { orderGrip: (o) => ({ grip: o.key, dis: !!o.dis, tip: o.tip, pressed: !!o.pressed }),"
             " orderList: (o) => { ORDER_LISTS.push(o); return {}; }, orderLifted: () => LIFTED });"
             "U.al = (tone, title, text, o) => ({ al: tone, title, text, btns: (o && o.btns) || [], classList: { add() {} } });"
             "const tk = (p, c, v) => t(p + c, v);"
             "const ordInfo = (by) => ({ info: by });"
             # 3.3.7.15 F14 «Разложить заново»: строка «Уровни» с кнопкой (rgHead) и подсветка «Устройств» после
             # раскладки (rgUpdOf) — заглушки: строка — как раньше, подзаголовок; подсветки нет
             "const rgHead = (gid, subEl) => subEl, rgUpdOf = () => false;"]
            + [jsparts.const(js, n) for n in ORDER_CONSTS] + [jsparts.function(js, n) for n in ORDER_FUNCS])


@unittest.skipUnless(shutil.which("node"), "нет node")
class RuCardTest(unittest.TestCase):
    def scope(self):
        js = read("pages/exits-groups.js")
        return ([jsparts.const(js, n) for n in ("METHODS", "MAX_TIERS", "tierLabel", "methodsOf", "RARE_OPEN", "rareOn", "newTierValid",
                                                "usable", "exitsOfGroup", "ruNowChip")]
                + [jsparts.function(js, n) for n in ("countExits", "laterHint", "tiersBlock", "newTierId", "newTierRow", "behaviourBlock",
                                                     "ruNow", "ruNowText")] + order_scope())

    def test_tier_rows_have_devices_and_ru_adds_its_own_key(self):
        out = run(self.scope(), r"""
const home = grp("g-home", "ru", [tier("t1", ["r1", "r2"])]), office = grp("g-office", "ru", [tier("t1", [])]), main = grp("main", "abroad", [tier("t1", ["r2"])]);
[home, office, main].forEach(form);
const res = {};
for (const g of [home, office, main]) {
  const box = tiersBlock(g.id);
  const devs = find(box, (x) => x.a && x.a.class === "kk-eg-dev").map((x) => x.a.text);
  const adds = find(box, (x) => typeof x.btn === "string" && /addKeyTo|addExit/.test(x.btn));
  adds.forEach((b) => b.fn());
  const hints = find(box, (x) => x.tag === "p").map((x) => (x.kids.find((k) => k.a && k.a.text) || x).a.text);
  res[g.id] = { devs, adds: adds.map((b) => b.btn), hints };
}
res.log = LOG;
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["g-home"]["devs"], ["ep.devicesN#2", "ep.devicesN#0"])
        self.assertEqual(out["main"]["devs"], ["ep.devicesN#0"])                 # колонка — у обеих ролей
        self.assertEqual(out["g-home"]["adds"], ['ep.ru.addKeyTo{"g":"«g-home»"}'])
        self.assertEqual(out["g-office"]["adds"], ['ep.ru.addKeyTo{"g":"«g-office»"}'])
        self.assertEqual(out["main"]["adds"], ["ep.addExit"])
        self.assertEqual(out["log"], [["addKey", "g-home"], ["addKey", "g-office"], ["addExit", "main"]])   # своя группа у каждой
        self.assertIn("ep.groupVsTier", out["g-home"]["hints"])
        self.assertIn("ep.ru.emptyGroup", out["g-office"]["hints"])

    def test_by_speed_only_with_the_speed_check_and_method_keeps_the_preset(self):
        out = run(self.scope(), r"""
const a = grp("g-a", "ru", [tier("t1", ["r1", "r2"])]);
const b = grp("g-b", "ru", [tier("t1", ["r1"])]); b.checks.speed.enabled = true;
const c = grp("g-c", "ru", [tier("t1", ["r1"], "capacity")]);
const m = grp("main", "abroad", [tier("t1", ["r2"])]);
[a, b, c, m].forEach(form);
const res = {};
for (const g of [a, b, c, m]) { SELECTS.length = 0; tiersBlock(g.id); res[g.id] = SELECTS[0].select; }
SELECTS.length = 0; tiersBlock("g-a"); SELECTS[0].on("best_rtt");
res.after = { method: forms["g-a"].draft.tiers[0].method, preset: forms["g-a"].draft.preset };
console.log(JSON.stringify(res));
""")
        # «По порядку (вручную)» (PLAN-order Ш5) — последним, у всех уровней, кроме «по соединениям» и шага внутренней
        # группы из одного ключа (порядка там нет, REV-order П-3)
        self.assertEqual(out["g-a"], ["even", "best_rtt", "weights", "manual"])
        self.assertEqual(out["g-b"], ["even", "best_rtt", "weights", "capacity"])
        self.assertEqual(out["g-c"], ["even", "best_rtt", "weights", "capacity"])   # сохранённый «По скорости» — как есть
        self.assertEqual(out["main"], ["even", "best_rtt", "weights", "capacity", "manual"])
        self.assertEqual(out["after"], {"method": "best_rtt", "preset": "ru_home"})  # способ — не поле набора

    def test_dropping_the_chosen_tier_resets_where_new_keys_go(self):
        out = run(self.scope(), r"""
const g = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", [])], { new_exits_tier: "t2" });
form(g);
const box = tiersBlock("g-a");
const menus = find(box, (x) => Array.isArray(x.menu));
const drop = menus.map((m) => m.menu.find((i) => i.label === "ep.tierDrop")).find(Boolean);
const before = newTierValid(forms["g-a"].draft);
drop.on();
const d = forms["g-a"].draft;
const stale = Object.assign(copy(d), { new_exits_tier: "t9" });
console.log(JSON.stringify({ tiers: d.tiers.map((x) => x.id), nt: d.new_exits_tier, before, after: newTierValid(d), stale: newTierValid(stale) }));
""")
        self.assertEqual(out["tiers"], ["t1"])
        self.assertEqual(out["nt"], "last")
        self.assertEqual((out["before"], out["after"], out["stale"]), (True, True, False))

    def test_later_hint_when_a_tier_gets_a_second_key(self):
        out = run(self.scope(), r"""
const g = grp("g-a", "ru", [tier("t1", ["r1"]), tier("t2", ["r2"])]);
form(g);
const hint = () => find(tiersBlock("g-a"), (x) => x.a && x.a.class === "gx-hint kk-eg-later").map((x) => x.a.text);
const h0 = hint();
const d = forms["g-a"].draft; d.tiers[0].exits.push("r2"); d.tiers[1].exits = [];
const h1 = hint();
d.night_rebalance.enabled = true; d.night_rebalance.at = "03:30";
const h2 = hint();
console.log(JSON.stringify({ h0, h1, h2 }));
""")
        self.assertEqual(out["h0"], [])
        self.assertEqual(out["h1"], ["ep.laterNoNight"])
        self.assertEqual(out["h2"], ['ep.laterNight{"at":"03:30"}'])

    def test_no_later_hint_for_main_per_connection(self):
        """REV-3312 П-1: у «Основной» с «По соединениям» закреплений нет — подсказки «уже подключённые останутся» нет;
        без «По соединениям» — есть."""
        out = run(self.scope(), r"""
const m = grp("main", "abroad", [tier("t1", ["r1"])]);
form(m);
const hint = () => find(tiersBlock("main"), (x) => x.a && x.a.class === "gx-hint kk-eg-later").map((x) => x.a.text);
forms.main.draft.tiers[0].exits.push("r2");
const off = hint();
forms.main.draft.per_connection = true;
const on = hint();
console.log(JSON.stringify({ off, on }));
""")
        self.assertEqual(out["off"], ["ep.laterNoNight"])
        self.assertEqual(out["on"], [])

    def test_behaviour_night_in_sight_for_ru_rare_under_more(self):
        out = run(self.scope(), r"""
const ru = grp("g-a", "ru", [tier("t1", ["r1"])]), ab = grp("g-b", "abroad", [tier("t1", ["r2"])]), mn = grp("main", "abroad", [tier("t1", ["r2"])]);
const on = grp("g-c", "abroad", [tier("t1", ["r2"])], { drop_on_degrade: true });
[ru, ab, mn, on].forEach(form);
const res = {};
for (const gid of ["g-a", "g-b", "main", "g-c"]) {
  const b = behaviourBlock(gid);
  const rare = find(b, (x) => x.a && x.a.class === "kk-eg-rare__b")[0];
  const inRare = new Set(); walk(rare, (x) => inRare.add(x));
  const sw = find(b, (x) => typeof x.sw === "string");
  res[gid] = { seen: sw.filter((x) => !inRare.has(x)).map((x) => x.sw), rare: sw.filter((x) => inRare.has(x)).map((x) => x.sw),
    nt: find(rare, (x) => x.a && x.a["data-eg-nt"]).length, hidden: !!rare.a.hidden };
}
SELECTS.length = 0; behaviourBlock("g-a");
res.ntItems = SELECTS[SELECTS.length - 1].select;
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["g-a"]["seen"], ["ep.evict", "ep.return", "ep.night", "ep.flap"])     # С-1: ночная — на виду
        self.assertEqual(out["g-a"]["rare"], ["ep.drop"])
        self.assertEqual(out["g-b"]["seen"], ["ep.evict", "ep.return", "ep.flap"])                 # макет Г2: три на виду
        self.assertEqual(out["g-b"]["rare"], ["ep.night", "ep.drop"])
        self.assertEqual(out["main"]["rare"], ["ep.night", "ep.drop", "ep.perConn"])
        self.assertTrue(all(out[g]["nt"] == 1 for g in ("g-a", "g-b", "main")))
        self.assertTrue(out["g-b"]["hidden"])                                                        # свёрнуто
        self.assertFalse(out["g-c"]["hidden"])                                                       # включённое не прячется
        self.assertEqual(out["ntItems"], ["last", "first", "t1"])

    def test_now_with_two_good_keys_in_the_tier(self):
        out = run(self.scope(), r"""
const two = grp("g-a", "ru", [tier("t1", ["r1", "r2"])]), one = grp("g-b", "ru", [tier("t1", ["r1", "r3"])]);
const a = ruNow(two), b = ruNow(one);
LIVE = { "g-a": { active_tier: "t1" } };
const c = ruNow(two);
console.log(JSON.stringify({ a: [a.exit, a.n, ruNowText(two, a), ruNowChip(two, a).tone], b: [b.exit, ruNowText(one, b)], c: [c.exit, c.n] }));
""")
        self.assertEqual(out["a"], [None, 2, 'ep.ru.nowTier{"n":"ep.tierN{\\"n\\":1}","k":"ep.ru.keysN#2"}', "success"])
        self.assertEqual(out["b"], ["r1", 'ep.ru.nowVia{"x":"Роутер"}'])
        self.assertEqual(out["c"], [None, 2])


@unittest.skipUnless(shutil.which("node"), "нет node")
class RuFactsAndFocusTest(unittest.TestCase):
    def scope(self):
        js = read("pages/exits-groups.js")
        return ([jsparts.const(js, n) for n in ("RULE_TPL", "whenConds", "toGroups", "ruWhoText", "ruPick")]
                + [jsparts.function(js, n) for n in ("cond", "rule", "expectedCountry", "ruFacts", "addCountryRule", "ruChecks", "ruCountry",
                                                     "ruWho", "ruWhoNames", "focusGroup")])

    def test_facts_in_the_card_and_add_condition_adds_it(self):
        out = run(self.scope() + ["const tk = (p, c) => p + c; const pol = () => ({ forecast: {} }); const open = {}; const RULE_OPEN = {}; K.R = {};",
                                  "const groupOf = (id) => (id === 'main' ? { id: 'main', fail: 'block' } : null);",
                                  "const exitsOfGroup = (g) => [].concat(...g.tiers.map((tr) => tr.exits));"], r"""
const g = grp("g-a", "ru", [tier("t1", ["r1"])]);
form(g);
const f = ruFacts("g-a");
const keys = find(f, (x) => x.a && x.a.class === "kk-rukv__k").map((x) => x.a.text);
const btns = find(f, (x) => typeof x.btn === "string");
const add = btns.find((b) => b.btn === "ep.ru.addCond");
add.fn();
const d = forms["g-a"].draft;
console.log(JSON.stringify({ keys, btns: btns.map((b) => b.btn), rules: d.rules.map((r) => r.when.conds[0].metric + r.when.conds[0].op + r.when.conds[0].value),
  preset: d.preset, open: open["g-a"], ruleOpen: RULE_OPEN["g-a"] === d.rules[0].id, base: forms["g-a"].base.rules.length }));
""")
        self.assertEqual(out["keys"], ["ep.ru.fWho", "ep.ru.fChecks", "ep.ru.fDns", "ep.ru.fCountry"])   # DNS — на месте
        self.assertEqual(out["btns"], ["ep.ru.list", "ep.ru.addCond"])
        self.assertEqual(out["rules"], ["country!=RU"])
        self.assertEqual((out["preset"], out["open"], out["ruleOpen"], out["base"]), ("custom", True, True, 0))   # в черновик

    def test_focus_opens_the_russian_card(self):
        out = run(self.scope() + ["const open = {}; let ruBox = null, ruCardsBox = null, cardsBox = null;",
                                  "const GROUPS = { 'g-a': { id: 'g-a', role: 'ru' }, 'g-b': { id: 'g-b', role: 'abroad' } };",
                                  "const groupOf = (id) => GROUPS[id] || null; const renderAll = () => LOG.push('renderAll'); const renderRu = () => LOG.push('renderRu');",
                                  "const exitsOfGroup = (g) => [].concat(...g.tiers.map((tr) => tr.exits));"], r"""
toGroups("g-a"); toGroups("g-b");
const away = LOG.splice(0);
ruBox = { isConnected: true };
const card = { classList: { add: (c) => LOG.push("found:" + c), remove() {} }, setAttribute() {}, scrollIntoView: () => LOG.push("scroll"), focus() {} };
ruCardsBox = { isConnected: true, querySelector: (q) => (q.indexOf("exits.group.g-a") >= 0 ? card : null) };
toGroups("g-a");
console.log(JSON.stringify({ away, here: LOG, open: open["g-a"] }));
""")
        self.assertEqual(out["away"], [["goNav", "exits", {"tab": "ru", "focus": "g-a"}], ["goNav", "exits", {"tab": "groups", "focus": "g-b"}]])
        self.assertEqual(out["here"], ["renderRu", "found:is-found", "scroll"])
        self.assertTrue(out["open"])


@unittest.skipUnless(shutil.which("node"), "нет node")
class RuUnloadTest(unittest.TestCase):
    """REV-3312 З-1: закрытие или обновление вкладки с несохранённой карточкой российской группы — окно браузера.
    На «Российских» карточки в ruCardsBox, а cardsBox = null (leave() при переходе с «Групп»): прежний страж
    «cardsBox && anyDirty()» молчал."""

    def test_dirty_russian_card_asks_before_unload(self):
        js = read("pages/exits-groups.js")
        out = run([jsparts.function(js, "unloadGuard"), "let cardsBox = null, ruCardsBox = null, dirty = false;",
                   "const anyDirty = () => dirty;"], r"""
const ask = () => { const ev = { prevented: false, returnValue: undefined, preventDefault() { this.prevented = true; } };
  unloadGuard(ev); return ev.prevented && ev.returnValue === ""; };
const res = {};
ruCardsBox = {}; dirty = true; res.ru = ask();              // «Российские», правка в карточке
dirty = false; res.ruClean = ask();                         // без правок — не спрашивает
ruCardsBox = null; cardsBox = {}; dirty = true; res.groups = ask();   // «Группы» — как раньше
cardsBox = null; res.away = ask();                          // ушли из части (leave) — спрашивает ядро, не часть
console.log(JSON.stringify(res));
""")
        self.assertEqual(out, {"ru": True, "ruClean": False, "groups": True, "away": False})


if __name__ == "__main__":
    unittest.main()
