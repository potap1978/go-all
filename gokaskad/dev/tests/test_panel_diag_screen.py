"""B15 круг 2 (REV-ns-b15 С-1, З-1, З-2, З-3, З-5, З-6, П-1–П-3, П-5): провязка экрана «Диагностика» — весь
pages/diag.js в node на маленьком поддельном DOM (tests/test_panel_rf_js.DOM), ядро панели — заглушка K/U, сервер —
поддельный API с etag (PUT с чужим etag — 409 ``stale``, как panel/host.py). Проверяется поведение экрана, а не строки:

* лист после 409 и «Обновить» берёт новый список и etag: правка листа уходит поверх записей другого окна, с новым
  If-Match; удалённая в другом окне запись листа — лист закрыт; удаление из листа — тоже от нового списка (С-1);
* «Сохранить» листа — ровно по ``errors`` ответа предпросмотра (нет своего правила «нет дней»; есть ошибка — выключена);
* включение переключателем — сначала предпросмотр, с ошибкой PUT нет и у строки «Не включилась»; удачная запись снимает
  «Не включилась» (З-1);
* битая запись (time: null): в строке «—», барабан и черновик — одно время (З-2);
* сбой предпросмотра снимает старые ошибки полей и aria-busy (П-2); барабан — aria-activedescendant (З-6);
* «+» не выключается числом записей (П-5); подписи «кто запустил» — из карты BY (З-5); сравнение (NS-D33) — первой
  строкой «от обычного» обеих проверок и ▲/▼ по ``better`` сервера, у тестов — разница со знаком и % от прежней;
  старшая проверка уходит в запрос первой; балла, звезды и звеньев в истории нет (NS-D34).

Node-less: skipped."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts
from tests.test_panel_rf_js import DOM, read

HAS_NODE = shutil.which("node") is not None

SCOPE = r"""
FakeNode.prototype.scrollTo = function (o) { this.scrollTop = o && o.top; };
FakeNode.prototype.getBoundingClientRect = function () { return { top: 0 }; };
FakeNode.prototype.scrollIntoView = function () {};
const _st = setTimeout;
globalThis.setTimeout = (f) => _st(f, 0);                 // 300 мс предпросмотра — сразу
globalThis.requestAnimationFrame = (f) => _st(f, 0);
const wait = async (n = 6) => { for (let i = 0; i < n; i += 1) await new Promise((r) => _st(r, 0)); };
const clone = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));
%(app)s
%(dict)s
const DICT = window.KASKAD_I18N_ADD.diag;
const fill = (s, v) => (v ? String(s).replace(/\{(\w+)\}/g, (m, k) => (v[k] !== undefined ? String(v[k]) : m)) : String(s));
const t = (k, v) => fill(DICT[k] !== undefined ? DICT[k] : k, v);
const tn = (k, n, v) => fill(String(DICT[k] !== undefined ? DICT[k] : k).split("|")[0], Object.assign({ n }, v || {}));
// окна панели: DLG — открытые (box — их узел, isConnected — пока не закрыто)
const DLG = [], TOASTS = [], CONFIRMS = [];
let CONFIRM_OK = true;
function openDialog(o) {
  let open = true;
  const box = el("div", { "data-dlg": o.title || "" }, o.blocks || []);
  const foot = el("div", { "data-foot": "" });
  box.append(foot);
  Object.defineProperty(box, "isConnected", { get: () => open });
  const d = { o, box, foot, get open() { return open; }, close() { open = false; }, setFoot(xs) { foot.replaceChildren(); append(foot, xs); } };
  DLG.push(d);
  return d;
}
const lastOpen = () => DLG.filter((d) => d.open).slice(-1)[0] || null;
const btnOf = (root, label) => root.querySelectorAll("button").filter((b) => b.textContent.trim() === label)[0] || null;
const U = {
  btn(label, kind, on, o = {}) {
    const b = el("button", { type: "button", "data-kind": kind, "aria-label": o.tip || null }, label || o.tip || "");
    if (o.dis) b.disabled = true;
    b.addEventListener("click", () => { if (on) on(); });
    return b;
  },
  sw(title, sub, on, onChange, o = {}) {
    const n = el("label", { "data-sw": title || "", "aria-checked": String(!!on) });
    n.flip = (v) => onChange(v);
    if (o.dis) n.disabled = true;
    return n;
  },
  al: (tone, title, text) => el("div", { "data-al": tone }, title, " ", text || ""),
  p: (x) => el("p", null, x),
  chip: (text, tone) => el("span", { "data-chip": tone || "" }, text),
  table(head, rows) {
    const cell = (c) => el("span", c && typeof c === "object" && c.tip ? { title: c.tip } : null, c && typeof c === "object" ? String(c.t) : String(c));
    return el("div", { "data-table": "" }, el("div", { "data-head": "" }, head.map(cell)), rows.map((r) => el("div", { "data-row": "" }, r.cells.map(cell))));
  },
  kv: (rows) => el("div", { "data-kv": "" }, rows.filter(Boolean).map((r) => el("div", { "data-kvrow": "" }, el("span", null, r.k), el("span", null, String(r.v))))),
  seg: (items) => el("div", { "data-seg": "" }, items.map((it) => { const b = el("button", { type: "button", "aria-pressed": String(!!it.on) }, it.label); b.addEventListener("click", it.pick); return b; })),
  empty: () => el("div"), field: () => el("div"), menu: () => el("div"), select: () => el("div"),
};
// поддельный сервер: список, etag (меняется при каждой записи), предпросмотр — PREVIEW(body)
const SRV = { list: [], ver: 1, nid: 0, tz: "Europe/Moscow" };
const docOf = () => ({ ok: true, schedule: clone(SRV.list), retry: SRV.retry !== false, armed: true, armed_why: null, pkgs: true, tz: SRV.tz, tz_warn: null,
  next: [], cost: null, limits: { jitter_min: 15, total: 1 }, timer: { applied: true, active: true }, etag: '"e' + SRV.ver + '"' });
const CALLS = [];
let PREVIEW = (b) => ({ ok: true, schedule: b.schedule, errors: [], cost: null, next: [] });
function serverPut(list) {                              // «другое окно» или запись этого
  SRV.list = list.map((e) => Object.assign({}, e, { id: e.id || "n" + String(++SRV.nid) }));
  SRV.ver += 1;
}
let RUNS = [], RUN = {}, CMP = null;
const API = {
  schedule: () => { CALLS.push({ name: "schedule" }); return Promise.resolve(docOf()); },
  preview: (b) => { CALLS.push({ name: "preview", body: clone(b) }); const r = PREVIEW(b); return r && r.reject ? Promise.reject(r.reject) : Promise.resolve(r); },
  save: (b, etag) => {
    CALLS.push({ name: "save", body: clone(b), etag });
    if (etag !== docOf().etag) return Promise.reject({ code: "stale", body: { ok: false }, message: "stale" });
    serverPut(b.schedule);
    return Promise.resolve(docOf());
  },
  runs: () => Promise.resolve({ runs: clone(RUNS), more: false }),
  run: (id) => Promise.resolve(clone(RUN[id])),
  compare: (a, b) => { CALLS.push({ name: "compare", a, b }); return Promise.resolve(clone(CMP)); },
  exportFile: () => Promise.resolve(null),
};
let PAGE = null, NAV = { tab: "schedule" };
// часть «speed» (карточка «Канал до вас», BC5): по умолчанию не грузится; тест подставляет свою LOAD_PART
let LOAD_PART = null;
window.KaskadPanel = { el, t, tn, U, lang: "ru", API: { host: { bench: API } },
  loadPart: (name) => (LOAD_PART ? LOAD_PART(name) : new Promise(() => {})),
  Tpl: { tabs: () => el("div", { "data-tabs": "" }), state: (k, o) => el("div", { "data-state": k }, (o && o.text) || "") },
  icon: () => el("span"), fmtInt: (n) => String(Math.round(Number(n))), fmtNum1: (n) => String(n), fmtBytes: (n) => String(n) + " B",
  fmtDur: (n) => String(n) + " s", openDialog, confirmDialog: (o) => { CONFIRMS.push(o); return Promise.resolve(CONFIRM_OK); },
  toast: (a, b, c) => { TOASTS.push([a, b, c]); }, page: (id, def) => { PAGE = def; }, navState: () => NAV, goNav: () => {}, loadPage: () => {} };
%(diag)s
const SEC = el("section");
async function mount(list, nav) {
  SRV.list = clone(list || []); SRV.ver = 1; SRV.retry = true; NAV = nav || { tab: "schedule" };
  // круг 1 Т1: расписание — часть «Настройки › Проверка сервера» (PAGE.schedule), «Сейчас» и «История» — раздел
  if (NAV.tab === "schedule") { if (PAGE.leave) PAGE.leave(); PAGE.schedule(SEC); } else PAGE.build(SEC);
  await wait();
}
const rows = () => SEC.querySelectorAll(".gx-sched__row");
const rowOpen = (k) => rows()[k].querySelector(".gx-sched__open");
const rowSw = (k) => rows()[k].querySelector("[data-sw]");
const saves = () => CALLS.filter((c) => c.name === "save");
const E = (id, time, more) => Object.assign({ id, time, mode: "quick", days: [1, 2, 3, 4, 5, 6, 7], on: true, quiet: true, monthly: false }, more || {});
"""


def scope():
    app = read("app.js")
    parts = [jsparts.const(app, n) for n in ("known", "isObj", "arr", "txt")]
    parts += [jsparts.function(app, n) for n in ("append", "el")]
    return SCOPE % {"app": "\n".join(parts), "dict": read("pages/diag-i18n-ru.js"), "diag": read("pages/diag.js")}


def run(script):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "diag.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(DOM + scope() + "\n(async () => {\n" + script +
                    "\n})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });")
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode != 0 or not out:
        raise AssertionError("node: %s" % r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


LIST = "[E('a1', '02:00'), E('a2', '09:00'), E('a3', '21:00')]"


@unittest.skipUnless(HAS_NODE, "no node")
class SheetAfterStaleTest(unittest.TestCase):
    def test_reload_rebuilds_the_sheet_on_the_new_list_and_etag(self):
        """С-1: другое окно удалило 21:00 и добавило 16:00; в листе 02:00 → 03:00 «Сохранить» → 409 → «Обновить» →
        «Сохранить»: уходит новый список (16:00 есть, 21:00 нет) с правкой листа и новым etag."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
const hours = sheet.box.querySelectorAll('[role="listbox"]')[0];
hours.ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait();
serverPut([E("a1", "02:00"), E("a2", "09:00"), E("b9", "16:00")]);          // другое окно
btnOf(sheet.box, "Сохранить").click(); await wait();
const stale = lastOpen();
const staleText = stale.box.textContent;
btnOf(stale.box, "Обновить").click(); await wait(10);
const sheetOpen = sheet.open;
btnOf(sheet.box, "Сохранить").click(); await wait(10);
const s = saves();
console.log(JSON.stringify({ n: s.length, first: s[0].etag, second: s[1].etag, times: s[1].body.schedule.map((e) => e.time),
  ids: s[1].body.schedule.map((e) => e.id), server: SRV.list.map((e) => e.time), sheetOpen, closed: !sheet.open, staleText }));
""" % LIST)
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["first"], '"e1"')
        self.assertEqual(r["second"], '"e2"')                     # новый etag — и новый список под ним
        self.assertEqual(r["times"], ["03:00", "09:00", "16:00"])
        self.assertEqual(r["ids"], ["a1", "a2", "b9"])
        self.assertEqual(r["server"], ["03:00", "09:00", "16:00"])
        self.assertTrue(r["sheetOpen"])
        self.assertTrue(r["closed"])
        self.assertIn("ваша правка останется", r["staleText"])

    def test_without_reload_the_old_list_never_goes_with_a_new_etag(self):
        """С-1: «Отменить» в окне 409, раздел перечитал список сам — лист остаётся на своём etag (не на новом S.doc);
        второй «Сохранить» снова получает 409, список другого окна не затёрт."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
serverPut([E("a1", "02:00"), E("b9", "16:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Отменить").click(); await wait();
PAGE.build(SEC); await wait(10);                      // раздел сам перечитал список (S.doc новее листа)
const fresh = CALLS.filter((c) => c.name === "schedule").length;
btnOf(sheet.box, "Сохранить").click(); await wait();
console.log(JSON.stringify({ etags: saves().map((c) => c.etag), server: SRV.list.map((e) => e.time), fresh }));
""" % LIST)
        self.assertEqual(r["fresh"], 2)
        self.assertEqual(r["etags"], ['"e1"', '"e1"'])
        self.assertEqual(r["server"], ["02:00", "16:00"])

    def test_the_entry_deleted_elsewhere_closes_the_sheet(self):
        """С-1: запись листа удалили в другом окне — после «Обновить» лист закрыт, сказано почему, записи нет."""
        r = run("""
await mount(%s);
rowOpen(2).click(); await wait();
const sheet = lastOpen();
serverPut([E("a1", "02:00"), E("a2", "09:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
console.log(JSON.stringify({ open: sheet.open, toasts: TOASTS.map((x) => x[0]), saves: saves().length, server: SRV.list.map((e) => e.time) }));
""" % LIST)
        self.assertFalse(r["open"])
        self.assertIn("Эту запись удалили в другом окне", r["toasts"])
        self.assertEqual(r["saves"], 1)
        self.assertEqual(r["server"], ["02:00", "09:00"])

    def test_delete_from_the_sheet_after_reload_keeps_the_other_windows_entries(self):
        """С-1: удаление из листа после «Обновить» — от нового списка (новая запись другого окна остаётся)."""
        r = run("""
await mount(%s);
rowOpen(1).click(); await wait();
const sheet = lastOpen();
serverPut([E("a1", "02:00"), E("a2", "09:00"), E("a3", "21:00"), E("b9", "23:00")]);
btnOf(sheet.box, "Удалить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
btnOf(sheet.box, "Удалить").click(); await wait(10);
console.log(JSON.stringify({ server: SRV.list.map((e) => e.time), last: saves().slice(-1)[0].etag, open: sheet.open }));
""" % LIST)
        self.assertEqual(r["server"], ["02:00", "21:00", "23:00"])
        self.assertEqual(r["last"], '"e2"')
        self.assertFalse(r["open"])


@unittest.skipUnless(HAS_NODE, "no node")
class SheetByTheServerTest(unittest.TestCase):
    def test_save_follows_only_the_errors_of_the_preview(self):
        """§11.1 п. 25, ТЗ §8 B15: без дней и без errors в ответе — «Сохранить» активна (своего правила нет); ошибка
        сервера у времени — выключена и текст у поля."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
sheet.box.querySelectorAll(".gx-days__d").filter((b) => b.getAttribute("aria-pressed") === "true").forEach((b) => b.click());
await wait(10);
const noDays = { days: CALLS.filter((c) => c.name === "preview").slice(-1)[0].body.schedule[0].days, dis: btnOf(sheet.box, "Сохранить").disabled };
PREVIEW = (b) => ({ ok: true, errors: [{ code: "gap", args: { ia: "a1", ib: "a2" }, text: "Слишком близко" }], cost: null, next: [] });
sheet.box.querySelectorAll(".gx-days__d")[0].click(); await wait(10);
const bad = { dis: btnOf(sheet.box, "Сохранить").disabled, err: sheet.box.querySelectorAll(".gx-sheet-err")[0].textContent };
console.log(JSON.stringify({ noDays, bad }));
""" % LIST)
        self.assertEqual(r["noDays"], {"days": [], "dis": False})
        self.assertEqual(r["bad"], {"dis": True, "err": "Слишком близко"})

    def test_a_failed_preview_drops_the_old_errors(self):
        """П-2: сбой предпросмотра — старые ошибки полей сняты, aria-busy «false», сказано «Не удалось посчитать»."""
        r = run("""
await mount(%s);
PREVIEW = (b) => ({ ok: true, errors: [{ code: "time", args: { n: 1 }, text: "Ошибка времени" }], cost: null, next: [] });
rowOpen(0).click(); await wait(10);
const sheet = lastOpen();
const before = sheet.box.querySelectorAll(".gx-sheet-err")[0].textContent;
PREVIEW = () => ({ reject: { message: "сеть" } });
sheet.box.querySelectorAll(".gx-days__d")[0].click(); await wait(10);
const save = btnOf(sheet.box, "Сохранить");
console.log(JSON.stringify({ before, after: sheet.box.querySelectorAll(".gx-sheet-err")[0].textContent, busy: save.getAttribute("aria-busy"),
  form: sheet.box.querySelectorAll(".gx-sheet-err").slice(-1)[0].textContent }));
""" % LIST)
        self.assertEqual(r["before"], "Ошибка времени")
        self.assertEqual(r["after"], "")
        self.assertEqual(r["busy"], "false")
        self.assertIn("Не удалось посчитать", r["form"])

    def test_a_broken_entry_shows_and_sends_one_time(self):
        """З-2: битая запись (time: null) — в строке «—»; барабан на 04:00 и в предпросмотр уходит 04:00."""
        r = run("""
await mount([E("a1", null, { on: false, off_why: "time" }), E("a2", "09:00")]);
const shown = rowOpen(0).querySelector(".gx-sched__time").textContent;
rowOpen(0).click(); await wait(10);
const sheet = lastOpen();
const sel = sheet.box.querySelectorAll('[role="listbox"]').map((b) => b.querySelector('[aria-selected="true"]').textContent);
console.log(JSON.stringify({ shown, sel, sent: CALLS.filter((c) => c.name === "preview").slice(-1)[0].body.schedule[0].time }));
""")
        self.assertEqual(r, {"shown": "—", "sel": ["04", "00"], "sent": "04:00"})

    def test_the_wheel_says_its_value(self):
        """З-6: у столбца барабана aria-activedescendant — id выбранного варианта; стрелка его меняет. Тип — радио
        с одной остановкой Tab, стрелка меняет выбор."""
        r = run("""
await mount(%s);
rowOpen(1).click(); await wait();
const sheet = lastOpen();
const col = sheet.box.querySelectorAll('[role="listbox"]')[0];
const ad = () => { const id = col.getAttribute("aria-activedescendant"); const o = col.querySelectorAll('[role="option"]').filter((x) => x.getAttribute("id") === id)[0]; return o ? o.textContent : null; };
const a = ad();
col.ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait();
const b = ad();
const radios = () => sheet.box.querySelectorAll('[role="radio"]').map((x) => [x.getAttribute("aria-checked"), x.getAttribute("tabindex")]);
const r0 = radios();
sheet.box.querySelector('[role="radiogroup"]').ls.keydown[0]({ key: "ArrowRight", preventDefault() {} }); await wait();
console.log(JSON.stringify({ a, b, r0, r1: radios() }));
""" % LIST)
        self.assertEqual(r["a"], "09")
        self.assertEqual(r["b"], "10")
        self.assertEqual(r["r0"], [["true", "0"], ["false", "-1"]])
        self.assertEqual(r["r1"], [["false", "-1"], ["true", "0"]])


@unittest.skipUnless(HAS_NODE, "no node")
class RowsTest(unittest.TestCase):
    def test_switch_on_goes_through_the_preview(self):
        """§2.6: включение — сначала предпросмотр; ошибка — PUT нет, у строки «Не включилась»; удачная запись
        другим путём снимает это (З-1); без ошибки — PUT."""
        r = run("""
await mount([E("a1", "02:00"), E("a2", "09:00", { on: false })]);
PREVIEW = () => ({ ok: true, errors: [{ code: "gap", args: { ia: "a1", ib: "a2" }, text: "Проверки близко" }], cost: null, next: [] });
rowSw(1).flip(true); await wait(10);
const refused = { saves: saves().length, msg: rows()[1].textContent, preview: CALLS.filter((c) => c.name === "preview").length };
SEC.querySelector('[data-sw="' + t("dg.retryT") + '"]').flip(false); await wait(10);
const cleared = rows()[1].textContent.indexOf("Не включилась") < 0;
PREVIEW = (b) => ({ ok: true, errors: [], cost: null, next: [] });
rowSw(1).flip(true); await wait(10);
const ok = { saves: saves().length, on: SRV.list[1].on };
console.log(JSON.stringify({ refused, cleared, ok }));
""")
        self.assertEqual(r["refused"]["saves"], 0)
        self.assertEqual(r["refused"]["preview"], 1)
        self.assertIn("Не включилась: Проверки близко", r["refused"]["msg"])
        self.assertTrue(r["cleared"])
        self.assertEqual(r["ok"], {"saves": 2, "on": True})

    def test_add_is_not_limited_by_the_panel(self):
        """П-5: «+» не выключается числом записей (limits.total поддельного сервера = 1) — решает сервер."""
        r = run("""
await mount(%s);
const add = SEC.querySelector('button[aria-label="' + t("dg.add") + '"]');
console.log(JSON.stringify({ dis: add.disabled, zone: SEC.textContent.split("Время сервера").length - 1 }));
""" % LIST)
        self.assertFalse(r["dis"])
        self.assertEqual(r["zone"], 1)                            # П-4: время сервера — один раз


@unittest.skipUnless(HAS_NODE, "no node")
class SheetFieldsAfterStaleTest(unittest.TestCase):
    """REV-ns-b15-2 Н-1, Н-2, П-1, П-2: после «Обновить» поверх новой записи — только поля, которые человек менял;
    правимая запись — на новом месте списка; согласие на «много трафика» не теряет базу листа."""

    def test_reload_keeps_the_other_windows_switch_and_days(self):
        """Н-1: лист 02:00 → 03:00; другое окно выключило эту запись и сделало Пн+Вт → «Обновить» → «Сохранить»:
        запись остаётся выключенной и с Пн+Вт, время — из листа."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
sheet.box.querySelectorAll('[role="listbox"]')[0].ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait();
serverPut([E("a1", "02:00", { on: false, days: [1, 2], quiet: false }), E("a2", "09:00"), E("a3", "21:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
const pressed = sheet.box.querySelectorAll(".gx-days__d").map((b) => b.getAttribute("aria-pressed"));
btnOf(sheet.box, "Сохранить").click(); await wait(10);
console.log(JSON.stringify({ a1: SRV.list[0], pressed }));
""" % LIST)
        self.assertEqual(r["a1"]["time"], "03:00")
        self.assertIs(r["a1"]["on"], False)
        self.assertEqual(r["a1"]["days"], [1, 2])
        self.assertIs(r["a1"]["quiet"], False)
        self.assertEqual(r["pressed"], ["true", "true", "false", "false", "false", "false", "false"])

    def test_reload_keeps_the_fields_the_person_changed(self):
        """Н-1: человек менял только дни; другое окно поменяло время той же записи → после «Обновить» уходит время
        другого окна и дни листа; барабан показывает новое время."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
sheet.box.querySelectorAll(".gx-days__d")[6].click(); await wait();          // Вс выключено
serverPut([E("a1", "05:30"), E("a2", "09:00"), E("a3", "21:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
const wheel = sheet.box.querySelectorAll('[role="listbox"]').map((n) => n.getAttribute("aria-activedescendant").split("-").pop());
btnOf(sheet.box, "Сохранить").click(); await wait(10);
console.log(JSON.stringify({ a1: SRV.list[0], wheel }));
""" % LIST)
        self.assertEqual(r["a1"]["time"], "05:30")
        self.assertEqual(r["a1"]["days"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(r["wheel"], ["5", "30"])

    def test_monthly_of_the_person_over_the_days_of_the_other_window(self):
        """П-2 REV-ns-p4a: человек переключил только повтор на «раз в месяц» (один день), другое окно сделало Пн+Вт →
        после «Обновить» в листе один день, «Сохранить» уходит без ошибки «раз в месяц — один день»."""
        r = run("""
await mount([E("a1", "02:00", { days: [1] }), E("a2", "09:00"), E("a3", "21:00")]);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
btnOf(sheet.box, DICT["dg.repMonth"]).click(); await wait();
serverPut([E("a1", "02:00", { days: [1, 2] }), E("a2", "09:00"), E("a3", "21:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
const last = CALLS.filter((c) => c.name === "preview").slice(-1)[0];
btnOf(sheet.box, "Сохранить").click(); await wait(10);
console.log(JSON.stringify({ a1: SRV.list[0], sent: last.body.schedule[0] }));
""")
        self.assertIs(r["a1"]["monthly"], True)
        self.assertEqual(r["a1"]["days"], [1])
        self.assertEqual(r["sent"]["days"], [1])

    def test_an_entry_put_first_elsewhere_moves_the_sheets_place(self):
        """Н-2: другое окно вставило запись в начало; лист второй записи (09:00 → 10:00) после «Обновить» пишет в её
        новое место, соседи целы; «Удалить» из такого листа удаляет её, а не ту, что теперь на старом месте."""
        r = run("""
await mount(%s);
rowOpen(1).click(); await wait();
const sheet = lastOpen();
sheet.box.querySelectorAll('[role="listbox"]')[0].ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait();
serverPut([E("b0", "00:30"), E("a1", "02:00"), E("a2", "09:00"), E("a3", "21:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
btnOf(sheet.box, "Сохранить").click(); await wait(10);
const saved = SRV.list.map((e) => e.id + ":" + e.time);
rowOpen(2).click(); await wait();
const sheet2 = lastOpen();
serverPut([E("b1", "00:10")].concat(clone(SRV.list)));
btnOf(sheet2.box, "Удалить").click(); await wait();
btnOf(lastOpen().box, "Обновить").click(); await wait(10);
btnOf(sheet2.box, "Удалить").click(); await wait(10);
console.log(JSON.stringify({ saved, left: SRV.list.map((e) => e.id) }));
""" % LIST)
        self.assertEqual(r["saved"], ["b0:00:30", "a1:02:00", "a2:10:00", "a3:21:00"])
        self.assertEqual(r["left"], ["b1", "b0", "a1", "a3"])

    def test_consent_to_heavy_then_stale_keeps_the_sheet(self):
        """Н-2: большая запись → 409 need_confirm → «Да» → 409 stale (другое окно успело) → окно говорит про правку
        листа; «Обновить» пересобирает лист, второй «Сохранить» → снова согласие → запись на новом списке."""
        r = run("""
await mount(%s);
const save0 = API.save;
API.save = (b, etag) => {
  if (!b.confirm) { CALLS.push({ name: "save", body: clone(b), etag }); return Promise.reject({ code: "need_confirm", body: { ok: false, cost: { notes: [] } } }); }
  return save0(b, etag);
};
rowOpen(0).click(); await wait();
const sheet = lastOpen();
sheet.box.querySelectorAll('[role="listbox"]')[0].ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait();
serverPut([E("a1", "02:00"), E("a2", "09:00"), E("b9", "16:00")]);
btnOf(sheet.box, "Сохранить").click(); await wait(10);
const stale = lastOpen();
const staleText = stale.box.textContent;
btnOf(stale.box, "Обновить").click(); await wait(10);
btnOf(sheet.box, "Сохранить").click(); await wait(10);
API.save = save0;
console.log(JSON.stringify({ staleText, confirms: CONFIRMS.length, server: SRV.list.map((e) => e.time), closed: !sheet.open,
  etags: saves().map((c) => c.etag + (c.body.confirm ? "!" : "")) }));
""" % LIST)
        self.assertIn("ваша правка останется", r["staleText"])
        self.assertEqual(r["confirms"], 2)
        self.assertEqual(r["server"], ["03:00", "09:00", "16:00"])
        self.assertTrue(r["closed"])
        self.assertEqual(r["etags"], ['"e1"', '"e1"!', '"e2"', '"e2"!'])

    def test_the_sheets_preview_is_of_its_own_list(self):
        """Н-2: раздел перечитал расписание (другое окно выключило автоповтор) — предпросмотр листа считает цену того,
        что лист сохранит: его база (retry: true), а не новый ответ."""
        r = run("""
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
SRV.retry = false; serverPut(clone(SRV.list));
PAGE.build(SEC); await wait(10);
sheet.box.querySelectorAll('[role="listbox"]')[0].ls.keydown[0]({ key: "ArrowDown", preventDefault() {} }); await wait(10);
const pv = CALLS.filter((c) => c.name === "preview").slice(-1)[0].body;
console.log(JSON.stringify({ retry: pv.retry, n: pv.schedule.length }));
""" % LIST)
        self.assertIs(r["retry"], True)
        self.assertEqual(r["n"], 3)

    def test_switch_on_keeps_the_list_it_was_made_of(self):
        """Н-2: включение ждёт предпросмотра; раздел за это время перечитал список другого окна — PUT уходит со
        старым etag (409), список другого окна не затёрт."""
        r = run("""
await mount([E("a1", "02:00"), E("a2", "09:00", { on: false })]);
let hold = null;
PREVIEW = (b) => new Promise((res) => { hold = () => res({ ok: true, errors: [], cost: null, next: [] }); });
rowSw(1).flip(true); await wait();
serverPut([E("a1", "02:00"), E("a2", "09:00", { on: false }), E("b9", "16:00")]);
PAGE.build(SEC); await wait(10);
hold(); await wait(10);
console.log(JSON.stringify({ etags: saves().map((c) => c.etag), server: SRV.list.map((e) => e.id + ":" + e.on) }));
""")
        self.assertEqual(r["etags"], ['"e1"'])
        self.assertEqual(r["server"], ["a1:true", "a2:false", "b9:true"])

    def test_delete_after_stale_says_delete(self):
        """П-2: 409 при «Удалить» из листа — текст про удаление, а не «сохраните ещё раз»."""
        r = run("""
await mount(%s);
rowOpen(1).click(); await wait();
const sheet = lastOpen();
serverPut([E("a1", "02:00"), E("a2", "09:00"), E("a3", "21:00"), E("b9", "23:00")]);
btnOf(sheet.box, "Удалить").click(); await wait();
console.log(JSON.stringify({ text: lastOpen().box.textContent }));
""" % LIST)
        self.assertIn("запись ещё не удалена", r["text"])
        self.assertNotIn("сохраните ещё раз", r["text"])

    def test_keyboard_focus_stays_on_the_day(self):
        """П-1: Пробел на «Ср» перерисовывает дни — фокус на той же кнопке нового ряда, не в начале страницы."""
        r = run("""
FakeNode.prototype.focus = function () { document.activeElement = this; };
await mount(%s);
rowOpen(0).click(); await wait();
const sheet = lastOpen();
const days = () => sheet.box.querySelectorAll(".gx-days__d");
days()[2].focus(); days()[2].click(); await wait();
const a = document.activeElement;
const quiet = sheet.box.querySelector('[data-sw="' + t("dg.quiet") + '"]');
console.log(JSON.stringify({ same: a === days()[2], live: sheet.box.contains(a), pressed: days()[2].getAttribute("aria-pressed") }));
""" % LIST)
        self.assertTrue(r["same"])
        self.assertTrue(r["live"])
        self.assertEqual(r["pressed"], "false")


RUN_A = {"id": "r1", "ts": 1760900000, "mode": "full", "by": "auto", "status": "ok", "drops": [],
         "tests": [{"test": "net_down", "value": 90, "unit": "Mbit/s", "ok": 1}]}
# GET /api/host/bench/compare (NS-D33): a — старшая (r2), b — новее (r1); delta = b − a, delta_pct — от a
CMP = {"a": dict(RUN_A, id="r2", ts=1760800000), "b": RUN_A, "mode_differs": False,
       "rows": [{"test": "net_down", "a": 80, "b": 90, "delta": 10, "delta_pct": 12.5, "unit": "Mbit/s"},
                {"test": "disk_seq", "a": 5, "b": 4, "delta": -1, "delta_pct": -20, "unit": "MB/s"},
                {"test": "rtt", "a": None, "b": 30, "delta": None, "delta_pct": None, "unit": "ms"}],
       "rel_a": 90, "rel_b": 96, "better": "b"}
COMPARE = r"""
RUNS = [%(a)s, Object.assign({}, %(a)s, { id: "r2", by: "panel", ts: 1760800000 }), Object.assign({}, %(a)s, { id: "r3", by: "cli", ts: 1760700000 })];
RUN = { r1: %(a)s };
CMP = %(cmp)s;
await mount([], { tab: "history" });
const cmpOf = async (i, j) => {
  await PAGE.nav({ tab: "history" });
  btnOf(SEC, t("dg.compare")).click(); await wait();
  SEC.querySelectorAll(".gx-hrow")[i].click(); await wait(); SEC.querySelectorAll(".gx-hrow")[j].click(); await wait(10);
  return SEC.querySelector(".gx-hist2__det").querySelectorAll("[data-row]").map((n) => n.textContent);
};
"""


@unittest.skipUnless(HAS_NODE, "no node")
class HistoryTest(unittest.TestCase):
    def test_labels_and_the_detail_without_a_score(self):
        """З-5: ``auto`` — «повтор после резкого падения», ``panel`` — «из панели», ``cli`` — своей подписью (карта BY).
        NS-D34, UI-C: в строке и детали проверки нет балла и звезды; справа в строке — «N от обычного» (runs[].rel
        сервера), нет числа — ничего; деталь — «в целом N от обычного» и таблица «звено · значение · обычно · от
        обычного» (rel.tests сервера: к «обычно» на время проверки)."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
RUNS[0].rel = 96; RUNS[1].rel = null;
RUN.r1 = Object.assign(clone(RUN.r1), { rel: { rel_total: 96, tests: { net_down: { usual: 88, usual_n: 12, rel: 102 } } } });
RUN.r1.tests.push({ test: "rtt", value: null, unit: "ms", ok: 0 });
PAGE.leave(); SEC.replaceChildren(); await mount([], { tab: "history" });
const rels = SEC.querySelectorAll(".gx-hrow").map((n) => (n.querySelector(".gx-hrow__rel") || { textContent: "" }).textContent);
const subs = SEC.querySelectorAll(".gx-hrow__sub").map((n) => n.textContent);
const row0 = SEC.querySelectorAll(".gx-hrow")[0].textContent;
SEC.querySelectorAll(".gx-hrow")[0].click(); await wait(10);
const det = SEC.querySelector(".gx-hist2__det");
console.log(JSON.stringify({ subs, row0, head: det.querySelectorAll("[data-head]").map((n) => n.textContent),
  trow: det.querySelectorAll("[data-row]").map((n) => n.textContent), svg: SEC.querySelectorAll("svg").length, text: det.textContent,
  rels, total: (det.querySelector(".gx-rel__n") || { textContent: "" }).textContent }));
""")
        self.assertEqual(r["subs"], ["Полная · повтор после резкого падения", "Полная · из панели", "Полная · из командной строки"])
        self.assertEqual(r["rels"], ["96от обычного", "", ""])
        self.assertEqual(r["total"], "96")
        self.assertIn("100 — как обычно у этого сервера", r["text"])
        self.assertEqual(r["head"], ["ЗвеноЗначениеОбычноОт обычного"])
        self.assertEqual(r["trow"], ["Скачивание90 Мбит/с88 Мбит/с102", "Откликне измерено——"])
        self.assertEqual(r["svg"], 0)
        for w in ("балл", "Балл", "Звенья", "хороший"):
            self.assertNotIn(w, r["text"] + r["row0"])

    def test_a_test_left_out_by_plan_says_why_in_words(self):
        """D-144 (владелец 29.10: «если отдачу выключили — в истории причина словами, а не „не измерено“»): тест без
        значения по плану (выключили при запуске, не входит в проверку, в России не меряется) — короткая причина;
        сбой замера — «не измерено», как было."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
RUN.r1 = clone(RUN.r1);
RUN.r1.tests.push({ test: "net_up", value: null, unit: "Mbit/s", ok: 0, why: "upload_off" },
  { test: "load_retr", value: null, unit: "%", ok: 0, why: "upload_off" }, { test: "rtt", value: null, unit: "ms", ok: 0, why: "tool_failed" },
  { test: "disk_fsync", value: null, unit: "ms", ok: 0, why: "excluded" });
PAGE.leave(); SEC.replaceChildren(); await mount([], { tab: "history" });
SEC.querySelectorAll(".gx-hrow")[0].click(); await wait(10);
console.log(JSON.stringify({ trow: SEC.querySelector(".gx-hist2__det").querySelectorAll("[data-row]").map((n) => n.textContent) }));
""")
        rows = r["trow"]
        self.assertIn("выключено при запуске", rows[1])                     # без правки: «не измерено»
        self.assertIn("выключено при запуске", rows[2])
        self.assertIn("не измерено", rows[3])
        self.assertIn("выключено в настройках", rows[4])

    def test_8c_over_a_short_window_says_so_in_the_hint(self):
        """Н-1 (3.3.7.13): «под нагрузкой» посчитано по короткому окну (``win`` истории — отдача быстро дошла до предела
        трафика) — значение как есть, в подсказке ячейки — что окно короткое и число менее надёжно; без ``win`` и у
        «не измерено» подсказки нет."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
RUN.r1 = clone(RUN.r1);
RUN.r1.tests.push({ test: "load_retr", value: 0.2, unit: "%", ok: 1, win: 1.42 }, { test: "load_rtt", value: 6, unit: "ms", ok: 1 },
  { test: "net_up", value: null, unit: "Mbit/s", ok: 0, why: "tool_failed", win: 1.42 });
PAGE.leave(); SEC.replaceChildren(); await mount([], { tab: "history" });
SEC.querySelectorAll(".gx-hrow")[0].click(); await wait(10);
const det = SEC.querySelector(".gx-hist2__det");
console.log(JSON.stringify({ trow: det.querySelectorAll("[data-row]").map((n) => n.textContent),
  tips: det.querySelectorAll("[title]").map((n) => [n.textContent, n.getAttribute("title")]) }));
""")
        self.assertEqual(r["trow"][1:], ["Повторная отправка под нагрузкой0,2%——", "Рост отклика под нагрузкой6 мс——",
                                         "Отдачане измерено——"])
        self.assertEqual(r["tips"], [["0,2%", "Посчитано по короткому отрезку — 1.4 с: отдача быстро дошла до предела "
                                      "трафика проверки, поэтому число менее надёжно"]])   # без правки: подсказки нет

    def test_compare_of_usual_and_signed_differences(self):
        """NS-D33: старшая проверка — в запросе первой (выбраны новее, потом старше); первая строка — «от обычного»
        обеих и ▲ на разницу (новее лучше по ``better``). UI-C: у тестов — разница в единицах и доля от прежней
        («+10 Мбит/с · 13%»); без ``dir`` — знаком, без числа — «—»."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
const cmp = await cmpOf(0, 1);
console.log(JSON.stringify({ cmp, calls: CALLS.filter((c) => c.name === "compare"), legend: SEC.querySelector(".gx-hist2__legend").textContent }));
""")
        self.assertEqual(r["calls"], [{"name": "compare", "a": "r2", "b": "r1"}])
        self.assertEqual(r["cmp"][0], "В целом96 от обычного90 от обычного▲ 6")
        self.assertEqual(r["cmp"][1], "Скачивание90 Мбит/с80 Мбит/с+10 Мбит/с · 13%")
        self.assertEqual(r["cmp"][2], "Диск, поток4 МБ/с5 МБ/с−1 МБ/с · 20%")
        self.assertEqual(r["cmp"][3], "Отклик30 мсне измерено—")
        self.assertIn("Новее: Полная", r["legend"])
        self.assertIn("Раньше: Полная", r["legend"])

    def test_compare_without_better_or_with_the_newer_first(self):
        """``better`` нет (у одной проверки нет числа) — «—» без стрелки; «same» — «=». Ответ, где новее — a: разница
        «новее минус раньше» (знак наоборот), доля — от прежней (90 → 80: 11%)."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
const base = clone(CMP);
CMP = Object.assign(clone(base), { rel_a: null, better: undefined });
const none = await cmpOf(0, 1);
CMP = Object.assign(clone(base), { rel_a: 96, better: "same" });
const same = await cmpOf(0, 1);
CMP = Object.assign(clone(base), { a: base.b, b: base.a, rel_a: 96, rel_b: 90, better: "a" });
const flip = await cmpOf(0, 1);
console.log(JSON.stringify({ none, same, flip }));
""")
        self.assertEqual(r["none"][0], "В целом96 от обычного——")
        self.assertEqual(r["same"][0], "В целом96 от обычного96 от обычного=")
        self.assertEqual(r["flip"][0], "В целом96 от обычного90 от обычного▲ 6")
        self.assertEqual(r["flip"][1], "Скачивание80 Мбит/с90 Мбит/с−10 Мбит/с · 11%")

    def test_compare_arrows_by_the_direction_and_almost_the_same(self):
        """UI-C: ``dir`` сервера (compare.rows[].dir: higher | lower) — ▲ лучше, ▼ хуже, в единицах; отклик вырос — ▼;
        разница меньше 2 % — «почти без разницы», ноль — «без разницы»."""
        r = run(COMPARE % {"a": json.dumps(RUN_A), "cmp": json.dumps(CMP)} + """
const base = clone(CMP);
CMP = Object.assign(clone(base), { rows: [
  { test: "net_down", a: 80, b: 90, delta: 10, delta_pct: 12.5, unit: "Mbit/s", dir: "higher" },
  { test: "rtt", a: 30, b: 36, delta: 6, delta_pct: 20, unit: "ms", dir: "lower" },
  { test: "cpu_1t", a: 1000, b: 1010, delta: 10, delta_pct: 1, unit: "events/s", dir: "higher" },
  { test: "aes_1t", a: 2900, b: 2900, delta: 0, delta_pct: 0, unit: "MB/s", dir: "higher" }] });
const cmp = await cmpOf(0, 1);
console.log(JSON.stringify({ cmp }));
""")
        self.assertEqual(r["cmp"][1], "Скачивание90 Мбит/с80 Мбит/с▲ 10 Мбит/с · 13%")
        self.assertEqual(r["cmp"][2], "Отклик36 мс30 мс▼ 6 мс · 20%")
        self.assertTrue(r["cmp"][3].endswith("почти без разницы"), r["cmp"][3])
        self.assertTrue(r["cmp"][4].endswith("без разницы") and "почти" not in r["cmp"][4], r["cmp"][4])


if __name__ == "__main__":
    unittest.main()
