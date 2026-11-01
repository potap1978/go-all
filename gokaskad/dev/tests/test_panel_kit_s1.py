"""T8 (этап 1) — компоненты Kit и графики в node, макет API по контракту SE §7.9.

Kit (kit.js, UX-FOUNDATION §6): чистые модели — impactLines («что изменится» из moves и предупреждений),
confirmActionModel (фазы подтверждения на месте), saveBarModel (полоса «Изменений: N · Что изменится · Отменить ·
Сохранить»), rebaseModel/applyAgain (перекладка формы: объект не менялся — молча, менялся — список «Применить снова»,
удалён — gone), errKind (gone, changed, busy, stale, dup, timeout, net), changedWhat (<что> через «; »).
confirmAction с поддельным DOM: «Всё равно» шлёт rev чтения, запомненную, когда прогон не удался (проверка 20);
409 на кнопке — новый прогон и «пересчитал»; срок прогона — свой таймер.
charts.js: scale/ticks/path.
Макет dev-mock.js: rev_required, 409 policy_changed с data.rev, сухой прогон ничего не пишет и отдаёт те же moves,
что настоящая запись, 404 gone у повтора, ver у «Уведомлений», «Тест скорости» и маршрута узла."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")
HAS_NODE = shutil.which("node") is not None


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run_node(src):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "kit.js")
        with open(path, "w", encoding="utf-8") as f:       # верхний await — внутри async-функции (CommonJS)
            f.write("(async () => {\n%s\n})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });" % src)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode != 0 or not out:
        raise AssertionError("node: %s" % r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


# Поддельный DOM: узлы с детьми, атрибутами, classList и событиями — хватает для Kit.confirmAction/saveBar
SCOPE = r"""
const window = globalThis;
globalThis.console = { error: () => {}, log: (m) => process.stdout.write(m + "\n") };
%(kit)s
const DICT = Object.assign({}, %(ru)s);
const fill = (s, v) => (v ? String(s).replace(/\{(\w+)\}/g, (m, k) => (v[k] !== undefined ? String(v[k]) : m)) : String(s));
const t = (k, v) => fill(DICT[k] !== undefined ? DICT[k] : k, v);
DICT["c.cancel"] = "Отмена"; DICT["err.timeoutChange"] = "не ответил вовремя";
class N {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.hidden = false; this.disabled = false; this.text = ""; this.on = {}; this.parent = null;
    const self = this; this.classList = { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, toggle(c, v) { if (v) this.set.add(c); else this.set.delete(c); }, contains(c) { return this.set.has(c); } }; }
  append(...k) { k.flat().filter(Boolean).forEach((x) => { if (x.parent) x.parent.kids = x.parent.kids.filter((y) => y !== x); x.parent = this; this.kids.push(x); }); }
  replaceChildren(...k) { this.kids.forEach((x) => { x.parent = null; }); this.kids = []; this.append(...k); }
  remove() { if (this.parent) this.parent.kids = this.parent.kids.filter((y) => y !== this); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); } removeAttribute(k) { delete this.attrs[k]; } getAttribute(k) { return this.attrs[k]; }
  addEventListener(n, f) { this.on[n] = f; } focus() {} contains() { return true; }
  get isConnected() { return true; }
  get textContent() { return this.text + this.kids.map((k) => k.textContent).join(" "); }
  all() { return [this].concat(...this.kids.map((k) => k.all())); }
}
const el = (tag, a, ...kids) => { const n = new N(tag); Object.keys(a || {}).forEach((k) => { const v = a[k]; if (v === null || v === undefined || v === false) return; if (k === "text") n.text = String(v); else if (k === "class") n.cls = v; else if (k === "hidden") n.hidden = true; else n.attrs[k] = v; }); n.append(...kids); return n; };
const U = { btn: (label, kind, fn, o) => { const b = el("button", { text: label }); b.kind = kind; b.click = () => { if (!b.disabled && fn) fn({}, b); }; if (o && o.dis) b.disabled = true; return b; },
  input: (o) => el("input", {}), seg: () => el("div"), skel: () => el("div"), empty: () => el("div"), state: () => el("div"), menu: () => el("button") };
const layers = { openDialog() {}, openSheet() {}, openMenu() {}, confirmDialog() {}, typedConfirm() {}, toast() {} };
const plural = (n) => { const a = Math.abs(n) %% 100, b = a %% 10; return a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2; };
const tn = (k, n, v) => { const f = String(DICT[k] !== undefined ? DICT[k] : k).split("|"); return fill(f[Math.min(plural(n), f.length - 1)], Object.assign({ n }, v)); };
Kit.init({ el, t, tn, icon: () => el("span"), isPhone: () => false, U, layers });
// кнопка с подписью: последняя в дереве (кнопка подтверждения, а не та, что его открыла)
const find = (root, txt) => root.all().filter((n) => n.tag === "button" && n.text === txt).pop();
const texts = (root) => root.all().filter((n) => !n.hidden).map((n) => n.text).filter(Boolean);
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const out = {};
"""


def scope():
    ru = read("kit-i18n-ru.js").split('window.KASKAD_I18N_ADD["kit"] = ', 1)[1].rsplit(";", 1)[0]
    return SCOPE % {"kit": read("kit.js"), "ru": ru}


@unittest.skipUnless(HAS_NODE, "no node")
class KitModelsTest(unittest.TestCase):
    def test_models(self):
        out = run_node(scope() + r"""
out.imp = Kit.impactLines({ moves: { total: 5, by_group: { main: { by_tier: { t1: 3, t2: 1 } }, "g-1": { by_tier: { t1: 1 } } } }, warnings: [{ code: "fail_direct" }, { code: "zzz" }] },
  { group: (g) => (g === "main" ? "Основная" : "Стриминг"), warn: (c) => (c === "fail_direct" ? "с IP сервера" : ""), extra: ["xray перезапустится"] });
out.imp0 = Kit.impactLines({ moves: { total: 0, by_group: {} }, warnings: [] });
// ответ сервера (policy_ops.impact и _save, REV-s1-t8-a С-1): последний выход группы выключен — устройства без интернета;
// «напрямую» — своей строкой; предупреждения сервера — строки, показываются как есть
out.impBlock = Kit.impactLines({ moves: { total: 3, by_group: { main: { total: 3, by_tier: {}, last_direct: 0, last_block: 3 } }, direct: 0, block: 0, devices: [] },
  warnings: ["Веса действуют только при способе «по весам»"] }, { group: () => "Основная" });
out.impMix = Kit.impactLines({ moves: { total: 4, by_group: { main: { total: 1, by_tier: { t2: 1 } }, "g-2": { total: 3, by_tier: {}, last_direct: 3 } }, direct: 0, block: 1 } },
  { group: (g) => (g === "main" ? "Основная" : "Стриминг") });
const E = (status, code, data) => ({ status, code, data });
out.kinds = [Kit.errKind(E(404, "gone", { gone: true })), Kit.errKind(E(404, null, { gone: true })), Kit.errKind(E(404, null, { exit: "x" })),
  Kit.errKind(E(409, "policy_changed", { rev: 3 })), Kit.errKind(E(409, "object_changed", {})), Kit.errKind(E(409, 6, null)),
  Kit.errKind(E(400, "rev_required")), Kit.errKind(E(400, "ver_required")), Kit.errKind(E(409, 3, { need: "confirm_duplicate" })),
  Kit.errKind(E(0, "timeout")), Kit.errKind(E(0, "net")), Kit.errKind(E(500, 1))];
const M = (s) => { const v = Kit.confirmActionModel(Object.assign({ label: "В запас", anywayLabel: "в запас" }, s)); return [v.open, v.text, v.primary && v.primary.label, v.primary && v.primary.enabled, v.primary && v.primary.kind]; };
out.ca = { idle: M({ phase: "idle" }), counting: M({ phase: "counting" }), ready: M({ phase: "ready", lines: ["a"] }), recount: M({ phase: "ready", recounted: true }),
  failed: M({ phase: "failed" }), busy: M({ phase: "busy" }), changedAnyway: M({ phase: "changedAnyway" }), gone: M({ phase: "gone", del: true }), goneElse: M({ phase: "gone" }),
  running: M({ phase: "running" }) };
const B = (s) => { const v = Kit.saveBarModel(Object.assign({ count: 2, hasDry: true }, s)); return [v.show, v.count, v.canSave, v.canCancel, v.canDry, v.text, v.retry]; };
out.sb = { idle: B({ phase: "idle" }), none: B({ phase: "idle", count: 0 }), counting: B({ phase: "counting" }), saving: B({ phase: "saving" }), busy: B({ phase: "busy" }),
  recount: B({ phase: "recount" }), invalid: B({ phase: "idle", valid: false }), gone: B({ phase: "gone", count: 0 }), noDry: B({ phase: "idle", hasDry: false }) };
// перекладка (SE §7): ключи формы группы
const KEYS = ["fail", "tiers", "checks.freq"];
const base = { fail: "block", tiers: [{ id: "t1", exits: ["A", "B"] }, { id: "t2", exits: ["C"] }], checks: { freq: { mode: "often" } } };
const draft = JSON.parse(JSON.stringify(base)); draft.fail = "direct"; draft.tiers = [{ id: "t1", exits: ["B", "A"] }, { id: "t2", exits: ["C"] }];
out.dirty = Kit.dirtyKeys(base, draft, KEYS);
const same = JSON.parse(JSON.stringify(base)); same.other = 1;                                       // поле вне формы
out.silent = Kit.rebaseModel(base, draft, same, KEYS);
const fresh = JSON.parse(JSON.stringify(base)); fresh.tiers[1].exits.push("D"); fresh.tiers[0].exits = ["A"];   // D новый, B убран
out.list = Kit.rebaseModel(base, draft, fresh, KEYS);
const tiersEdit = out.list.edits.find((e) => e.key === "tiers");
out.again = Kit.applyAgain(out.list.draft, tiersEdit).tiers;                                        // порядок правки на свежем составе
const already = JSON.parse(JSON.stringify(fresh)); already.fail = "direct";                          // своя запись дошла, ответ — нет
out.already = Kit.rebaseModel(base, draft, already, KEYS).edits.map((e) => e.key);
out.gone = Kit.rebaseModel(base, draft, null, KEYS).mode;
out.what = Kit.changedWhat(out.list.what, (id) => id.toLowerCase());
out.what2 = Kit.changedWhat({ added: ["X", "Y"], removed: ["Z", "W"] });
console.log(JSON.stringify(out));
""")
        self.assertEqual(out["imp"], ["Переедут устройства: 5 (в группе «Основная» — 4; в группе «Стриминг» — 1)", "с IP сервера",
                                      "Сервер предупреждает о последствиях — проверьте после сохранения", "xray перезапустится"])
        self.assertEqual(out["imp0"], ["Устройства останутся на своих выходах"])
        # стенд КТ2, сценарий 5: «переедут» — только на другой выход, не те, кто останется без интернета или пойдёт напрямую
        self.assertEqual(out["impBlock"], ["3 устройства останутся без интернета",
                                           "Веса действуют только при способе «по весам»"])
        self.assertEqual(out["impMix"], ["Переедут устройства: 1",
                                         "1 устройство останется без интернета", "3 устройства пойдут с IP сервера напрямую"])
        self.assertEqual(out["kinds"], ["gone", "gone", "fail", "changed", "changed", "busy", "stale", "stale", "dup", "timeout", "net", "fail"])
        ca = out["ca"]
        self.assertEqual(ca["idle"][0], False)
        self.assertEqual(ca["counting"], [True, "Считаю последствия…", "В запас", False, "run"])     # кнопка неактивна, пока прогон считает
        self.assertEqual(ca["ready"][2:], ["В запас", True, "run"])
        self.assertEqual(ca["recount"][1], "Данные изменились — пересчитал")
        self.assertEqual(ca["failed"][1:], ["Не удалось посчитать последствия", "Всё равно в запас", True, "anyway"])
        self.assertEqual(ca["busy"][2:], ["Повторить", True, "retry"])
        self.assertEqual(ca["changedAnyway"][1:3], ["Данные изменились — обновите и повторите", None])
        self.assertEqual((ca["gone"][1], ca["goneElse"][1]), ("Уже удалено", "Уже удалено в другом месте — обновите страницу"))
        self.assertEqual(ca["running"][3], False)
        sb = out["sb"]
        self.assertEqual(sb["idle"][:5], [True, "Изменений: 2", True, True, True])
        self.assertEqual(sb["none"][0], False)
        self.assertEqual(sb["counting"][2], False)                                       # «Сохранить» ждёт прогона
        self.assertEqual(sb["saving"][2:4], [False, False])
        self.assertEqual((sb["busy"][5], sb["busy"][6]), ("Сервер применяет предыдущее изменение — повторите через минуту", True))
        self.assertEqual((sb["recount"][2], sb["recount"][5]), (False, "Данные изменились — пересчитал"))
        self.assertEqual(sb["invalid"][2], False)
        self.assertEqual(sb["gone"][0], True)
        self.assertEqual(sb["noDry"][4], False)                                          # «Тест скорости»: без прогона
        self.assertEqual(out["dirty"], ["fail", "tiers"])
        self.assertEqual(out["silent"]["mode"], "silent")
        self.assertEqual(out["silent"]["draft"]["fail"], "direct")                       # правки поверх свежих
        self.assertEqual(out["silent"]["draft"]["other"], 1)
        self.assertEqual(out["list"]["mode"], "list")
        self.assertEqual(out["list"]["draft"]["fail"], "block")                          # на экране — свежие данные
        self.assertEqual(sorted(e["key"] for e in out["list"]["edits"]), ["fail", "tiers"])
        self.assertEqual(out["list"]["what"], {"added": ["D"], "removed": ["B"]})
        self.assertEqual(out["again"], [{"id": "t1", "exits": ["A"]}, {"id": "t2", "exits": ["C", "D"]}])
        self.assertEqual(out["already"], ["tiers"])                                      # «fail» уже такой — снят из списка
        self.assertEqual(out["gone"], "gone")
        self.assertEqual(out["what"], "новый выход «d»; выход «b» убран")
        self.assertEqual(out["what2"], "новые выходы «X», «Y»; выходы «Z», «W» убраны")

    def test_confirm_action_anyway_409_rereads(self):
        """REV-s1-t8m З-3 (ТЗ 09-ui-common, строка 24б): 409 на «Всё равно», прогон по-прежнему не удаётся — сначала
        перечитать (o.reread), следующее «Всё равно» идёт со свежей rev чтения; текст «Данные изменились…» виден и в
        «не удалось посчитать»."""
        out = run_node(scope() + r"""
let readRev = 20, runs = [], rereads = 0;
const ca = Kit.confirmAction({ label: "В запас", anywayLabel: "в запас", readRev: () => readRev,
  reread: () => { rereads++; readRev = 21; return Promise.resolve(); },
  dry: () => Promise.reject({ status: 500, code: 1 }),
  run: (rev) => { runs.push(rev); return rev === 21 ? Promise.resolve({ ok: true }) : Promise.reject({ status: 409, code: "policy_changed", data: { rev: 21 } }); },
  onDone: (res, how) => { out.done = (out.done || []).concat(how); } });
ca.open(); await tick(5);
find(ca.el, "Всё равно в запас").click(); await tick(8);
out.after409 = texts(ca.el); out.rereads = rereads;
const b = find(ca.el, "Всё равно в запас"); if (b) b.click(); await tick(8);
out.runs = runs; console.log(JSON.stringify(out));
""")
        self.assertEqual(out["rereads"], 1)
        self.assertIn("Данные изменились — обновите и повторите. Не удалось посчитать последствия", out["after409"])
        self.assertEqual(out["runs"], [20, 21])
        self.assertEqual(out["done"], ["ok"])

    def test_confirm_action_open_keeps_the_reason(self):
        """REV-s1-t8m З-5 (U6): правки появились, пока подтверждение раскрыто, — кнопка действия в нём неактивна с
        причиной и не шлёт запрос; правки ушли — снова активна."""
        out = run_node(scope() + r"""
let why = "", runs = 0;
const ca = Kit.confirmAction({ label: "Включить", dis: () => why, readRev: () => 1,
  dry: () => Promise.resolve({ rev: 1, warnings: [] }), run: () => { runs++; return Promise.resolve({}); } });
ca.open(); await tick(5);
const yes = () => ca.el.all().filter((n) => n.tag === "button" && n.textContent === "Включить").pop();
why = "Сначала сохраните или отмените правки"; ca.refresh();
out.off = [yes().disabled, yes().title];
yes().click(); await tick(5); out.runs = runs;
why = ""; ca.refresh(); out.on = yes().disabled;
console.log(JSON.stringify(out));
""")
        self.assertEqual(out["off"], [True, "Сначала сохраните или отмените правки"])
        self.assertEqual(out["runs"], 0)
        self.assertFalse(out["on"])

    def test_confirm_action_rev_rules(self):
        """«Всё равно» — с rev чтения, запомненной при неудаче прогона, а не на нажатие (проверка 20); 409 на кнопке —
        новый прогон и «пересчитал», действие — новым нажатием с rev нового прогона; gone — «Уже удалено»."""
        out = run_node(scope() + r"""
let readRev = 7, dryRev = 10, runs = [], dryFail = false, runErr = null, dries = 0;
const ca = Kit.confirmAction({ label: "В запас", anywayLabel: "в запас", readRev: () => readRev,
  dry: () => { dries++; return dryFail ? Promise.reject({ status: 500, code: 1 }) : Promise.resolve({ rev: dryRev, moves: { total: 2, by_group: {} }, warnings: [] }); },
  run: (rev) => { runs.push(rev); if (runErr) { const e = runErr; runErr = null; return Promise.reject(e); } return Promise.resolve({ ok: true }); },
  onDone: (res, how) => { out.done = (out.done || []).concat(how); } });
ca.open(); await tick(5);
out.ready = texts(ca.el);
runErr = { status: 409, code: "policy_changed", data: { rev: 11 } }; dryRev = 11;
find(ca.el, "В запас").click(); await tick(5);
out.recount = texts(ca.el);
find(ca.el, "В запас").click(); await tick(5);
out.runs1 = runs.slice(); out.phase1 = ca.phase();
// прогон не удался: «Всё равно» с rev чтения на момент неудачи
dryFail = true; readRev = 20; ca.open(); await tick(5);
readRev = 21;                                                         // чтение обновилось после неудачи
out.failed = texts(ca.el);
find(ca.el, "Всё равно в запас").click(); await tick(5);
out.runs2 = runs.slice();
// «Всё равно» получил 409 — «обновите и повторите» и новый прогон в том же подтверждении (UX §6 ConfirmAction)
dryFail = true; ca.open(); await tick(5); runErr = { status: 409, code: "policy_changed", data: {} };
dryFail = false; const d0 = dries;
find(ca.el, "Всё равно в запас").click(); await tick(5);
out.anyway409 = texts(ca.el); out.anyway409dry = dries - d0;
out.anyway409btn = !!find(ca.el, "В запас") && !find(ca.el, "В запас").disabled;
ca.close();
// отказ сервера (500) — текст под рядом, подтверждение на месте и действие снова активно; повтор — тот же rev прогона
ca.open(); await tick(5); runErr = { status: 500, code: 1, message: "Ошибка сервера" };
find(ca.el, "В запас").click(); await tick(5);
out.err500 = texts(ca.el); out.err500btn = !!find(ca.el, "В запас") && !find(ca.el, "В запас").disabled;
const nRuns = runs.length;
find(ca.el, "В запас").click(); await tick(5);
out.err500again = [runs.length - nRuns, runs[runs.length - 1] === runs[runs.length - 2], ca.phase()];
ca.close();
// занято (код 6) — «Повторить»
dryFail = false; ca.open(); await tick(5); runErr = { status: 409, code: 6 };
find(ca.el, "В запас").click(); await tick(5);
out.busy = texts(ca.el);
find(ca.el, "Повторить").click(); await tick(5);
out.runs3 = runs.slice(-2);
// gone — «Уже удалено в другом месте»
ca.open(); await tick(5); runErr = { status: 404, code: "gone", data: { gone: true } };
find(ca.el, "В запас").click(); await tick(5);
out.gone = texts(ca.el);
// неактивна при несохранённых правках
const off = Kit.confirmAction({ label: "Удалить", dis: () => "Сначала сохраните или отмените правки", dry: () => Promise.resolve({}), run: () => Promise.resolve({}) });
out.dis = [find(off.el, "Удалить").disabled, find(off.el, "Удалить").title];
off.open(); out.disPhase = off.phase();
console.log(JSON.stringify(out));
""")
        self.assertIn("Переедут устройства: 2", out["ready"])
        self.assertIn("Данные изменились — пересчитал", out["recount"])
        self.assertEqual(out["runs1"], [10, 11])                          # rev своего прогона, после 409 — нового
        self.assertEqual(out["phase1"], "idle")
        self.assertIn("Не удалось посчитать последствия", out["failed"])
        self.assertEqual(out["runs2"][-1], 20)                            # rev, запомненная при неудаче прогона
        self.assertIn("Данные изменились — обновите и повторите", out["anyway409"])
        self.assertEqual(out["anyway409dry"], 1)                         # новый прогон, а не тупик «Закрыть»
        self.assertIn("Переедут устройства: 2", out["anyway409"])
        self.assertTrue(out["anyway409btn"])
        self.assertIn("Ошибка сервера", out["err500"])
        self.assertTrue(out["err500btn"])                                  # действие снова активно (REV-s1-t8-a З-5)
        self.assertEqual(out["err500again"], [1, True, "idle"])
        self.assertIn("Сервер применяет предыдущее изменение — повторите через минуту", out["busy"])
        self.assertEqual(out["runs3"][0], out["runs3"][1])                 # «Повторить» — та же запись
        self.assertIn("Уже удалено в другом месте — обновите страницу", out["gone"])
        self.assertEqual(out["done"].count("gone"), 1)
        self.assertEqual(out["dis"], [True, "Сначала сохраните или отмените правки"])
        self.assertEqual(out["disPhase"], "idle")

    def test_dry_run_has_its_own_20_s_timer(self):
        js = read("kit.js")
        self.assertIn("const DRY_MS = 20000;", js)
        self.assertIn("withTimeout(o.dry(), DRY_MS)", js)
        self.assertIn("withTimeout(o.onDry(), DRY_MS)", js)
        out = run_node(scope() + r"""
const r = await Kit.withTimeout(new Promise(() => {}), 30).then(() => "ok", (e) => e.code);
console.log(JSON.stringify({ r }));
""")
        self.assertEqual(out["r"], "dry_timeout")


@unittest.skipUnless(HAS_NODE, "no node")
class ChartsTest(unittest.TestCase):
    def test_scale_ticks_path(self):
        # шкала делений — сетка графиков spark.js (KaskadSpark.grid): charts.js берёт её при вызове
        out = run_node("const window = globalThis;\n" + read("spark.js") + read("charts.js") + r"""
const C = window.KaskadCharts;
const pts = [[0, 10], [10, null], [20, 30], [30, 40]];
console.log(JSON.stringify({ sc: C.scale(pts, 1), empty: C.scale([[0, null]], 1).n, ticks: C.ticks(0, 40, 3), path: C.path(pts, 1, C.scale(pts, 1)).split("M").length - 1 }));
""")
        self.assertEqual(out["sc"], {"min": 0, "max": 40, "ts0": 0, "ts1": 30, "n": 3})
        self.assertEqual(out["empty"], 0)
        self.assertEqual(out["ticks"], [0, 20, 40])
        self.assertEqual(out["path"], 2)                                  # разрыв линии на пропуске


@unittest.skipUnless(HAS_NODE, "no node")
class MockPolicyContractTest(unittest.TestCase):
    """dev-mock.js повторяет контракт §7.9 до T7."""

    def test_contract(self):
        out = run_node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
eval(require("fs").readFileSync(%s, "utf8"));
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" }, body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
const out = {};
const pol = (await call("GET", "/api/gw/policy"))[1];
out.groups = pol.policy.groups.map((g) => [g.id, g.label, g.tiers.map((t) => [t.label, t.exits.length])]);
const ex = (await call("GET", "/api/gw/exits"))[1];
out.exRev = ex.rev === pol.rev; out.drv = typeof ex.default_route_ver;
out.row = (({ health, group, tier, reasons, recovering, unstable, manual_why }) => ({ health, group, tier, reasons, recovering, unstable, manual_why }))(ex.exits.find((e) => e.id === "e-7c2d8e0f13"));
out.norev = await call("POST", "/api/gw/exits/e-41a19b9cbe/manual", { state: "standby" });
out.old = await call("POST", "/api/gw/exits/e-41a19b9cbe/manual", { state: "standby", rev: pol.rev - 1 });
const dry = await call("POST", "/api/gw/exits/e-41a19b9cbe/manual?dry=1", { state: "standby" });
const still = (await call("GET", "/api/gw/policy"))[1].rev;
const real = await call("POST", "/api/gw/exits/e-41a19b9cbe/manual", { state: "standby", rev: pol.rev });
out.dry = [dry[1].dry, dry[1].rev === pol.rev, still === pol.rev, JSON.stringify(dry[1].moves) === JSON.stringify(real[1].moves), real[1].rev === pol.rev + 1];
out.stby = (await call("GET", "/api/gw/exits"))[1].exits.find((e) => e.id === "e-41a19b9cbe").health;
out.putEnabled = [await call("PUT", "/api/gw/exits/e-41a19b9cbe", { enabled: false }), await call("PUT", "/api/gw/exits/e-41a19b9cbe", { enabled: false, rev: 1 })].map((x) => [x[0], x[1].code]);
out.putGone = (await call("PUT", "/api/gw/exits/e-00000000aa", { name: "x" }))[1].code;
const r2 = (await call("GET", "/api/gw/policy"))[1].rev;
out.move = (await call("POST", "/api/gw/exits/e-7c2d8e0f13/move", { group: "g-5f1a2b", rev: r2 }))[1].moves.total;
out.place = (await call("GET", "/api/gw/exits"))[1].exits.find((e) => e.id === "e-7c2d8e0f13").group;
out.rename = (await call("PUT", "/api/gw/groups/g-5f1a2b", { name: "Основная" }))[0];
out.renameOk = (await call("PUT", "/api/gw/groups/g-5f1a2b", { name: "Кино" }))[1].label;
out.delMain = (await call("DELETE", "/api/gw/groups/main?rev=1"))[0];
const r3 = (await call("GET", "/api/gw/policy"))[1].rev;
out.delNeed = (await call("DELETE", "/api/gw/groups/g-5f1a2b?rev=" + r3))[0];
out.del = (await call("DELETE", "/api/gw/groups/g-5f1a2b?rev=" + r3 + "&exits_to=main"))[1].ok;
out.delAgain = (await call("DELETE", "/api/gw/groups/g-5f1a2b?rev=" + r3 + "&exits_to=main"))[1].code;
const nt = (await call("GET", "/api/notify"))[1];
out.ntNoVer = (await call("PUT", "/api/notify", { flap_summary: false }))[1].code;
out.ntOk = (await call("PUT", "/api/notify", { flap_summary: false, ver: nt.ver }))[1].flap_summary;
out.ntOld = (await call("PUT", "/api/notify", { flap_summary: true, ver: nt.ver }))[1].code;
const sp = (await call("GET", "/api/gw/probes/speed"))[1];
out.spExtra = (await call("PUT", "/api/gw/probes/speed", { max_mb: 5, nope: 1, ver: sp.ver }))[1].code;
out.route = (await call("PUT", "/api/gw/route", { via: "pool", fail: "direct" }))[1].code;
out.hist = (({ charts, cols }) => [charts, cols])((await call("GET", "/api/gw/exits/e-41a19b9cbe/history?range=7d"))[1]);
const tr = (await call("GET", "/api/gw/exits/e-41a19b9cbe/transitions?limit=2"))[1];
out.trans = [tr.db, tr.items.length, typeof tr.next];
console.log(JSON.stringify(out));
""" % json.dumps(os.path.join(STATIC, "dev-mock.js")))
        self.assertEqual(out["groups"], [["main", "Основная", [["Уровень 1", 2], ["Запасной", 1]]], ["g-5f1a2b", "Стриминг", [["Уровень 1", 1]]]])
        self.assertTrue(out["exRev"])
        self.assertEqual(out["drv"], "string")
        self.assertEqual(out["row"]["health"], "degraded")
        self.assertEqual((out["row"]["group"], out["row"]["tier"]), ("main", "t1"))
        self.assertEqual((out["norev"][0], out["norev"][1]["code"]), (400, "rev_required"))
        self.assertEqual((out["old"][0], out["old"][1]["code"]), (409, "policy_changed"))
        self.assertIn("rev", out["old"][1]["data"])
        self.assertEqual(out["dry"], [True, True, True, True, True])      # прогон ничего не пишет, moves — как у записи
        self.assertEqual(out["stby"], "standby")
        self.assertEqual(out["putEnabled"], [[400, "rev_required"], [400, "use_enabled"]])
        self.assertEqual(out["putGone"], "gone")
        self.assertEqual(out["move"], 3)
        self.assertEqual(out["place"], "g-5f1a2b")
        self.assertEqual(out["rename"], 409)                              # дубль по показанному имени
        self.assertEqual(out["renameOk"], "Кино")
        self.assertEqual((out["delMain"], out["delNeed"]), (400, 409))
        self.assertTrue(out["del"])
        self.assertEqual(out["delAgain"], "gone")
        self.assertEqual((out["ntNoVer"], out["ntOk"], out["ntOld"]), ("ver_required", False, "object_changed"))
        self.assertEqual(out["spExtra"], 2)
        self.assertEqual(out["route"], "ver_required")
        from kaskad.gw import metrics                     # the columns of the live server (T7), not the mock's own
        self.assertEqual(out["hist"], [True, metrics.history("e-0000000000")["cols"]])
        self.assertEqual(out["trans"], [True, 2, "number"])


if __name__ == "__main__":
    unittest.main()
