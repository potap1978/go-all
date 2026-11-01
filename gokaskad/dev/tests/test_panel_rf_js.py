"""R4 (SPEC-stage1-rf-mark-builtin §5.1, §9 «JS-контракт»): the tick ⚠РФ on the page, by behaviour, not by a
substring. The real functions of app.js and graph.js (cut out by tests/jsparts.py) run in node on a small fake DOM;
a click on a disabled button does nothing, as in the browser. The server cannot tell the tick of a person from one
the page put in itself, so the page is checked here:

* «Правила», the window of a device, the plaque «Подтвердить», the scheme (the box of the inspector, the window
  «Применить»): the box comes unticked, the button is disabled, a click on it sends nothing; the body carries
  ``confirm_not_rf`` (and ``orgs`` of the block shown) only after the click on the tick;
* the scheme: the tick belongs to the set of «Россия напрямую» it was given under — undo, «Убрать ошибочные», the
  dropping of stale intents, closing the window with the block take it away (A → tick → Ctrl+Z → B: B unticked);
* «Правила» in the mode ``need``: one message (the plaque), in the card — only the mark under the choice;
* leaving «Правила» forgets an unsaved choice and its tick.

Node-less: skipped."""

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


# a fake DOM: elements, attributes, text, events (bubbling), simple selectors (tag, .class, [attr], [attr="v"],
# :not(…), lists with commas); disabled/hidden reflect their attributes; click() of a disabled element — nothing
DOM = r"""
class FakeNode {
  constructor(tag) {
    this.nodeType = tag === "#text" ? 3 : tag === "#frag" ? 11 : 1;
    this.tagName = this.nodeName = tag.toUpperCase();
    this.attrs = {}; this.childNodes = []; this.parentNode = null; this.ls = {}; this._t = "";
    this.style = { setProperty() {} }; this.value = ""; this.checked = false; this.selected = false;
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get isConnected() { return true; }
  get className() { return this.attrs["class"] || ""; }
  set className(v) { this.attrs["class"] = String(v); }
  get classList() {
    const self = this, list = () => self.className.split(/\s+/).filter(Boolean);
    return { contains: (c) => list().includes(c), add: (...cs) => { self.className = list().concat(cs.filter((c) => !list().includes(c))).join(" "); },
      remove: (...cs) => { self.className = list().filter((c) => !cs.includes(c)).join(" "); },
      toggle: (c, on) => { const has = list().includes(c); const want = on === undefined ? !has : !!on; if (want && !has) self.classList.add(c); if (!want && has) self.classList.remove(c); return want; } };
  }
  get dataset() {                                      // n.dataset.find = id ↔ data-find (findAt)
    const self = this, key = (k) => "data-" + String(k).replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
    return new Proxy({}, { get: (_, k) => self.attrs[key(k)], set: (_, k, v) => { self.attrs[key(k)] = String(v); return true; } });
  }
  get disabled() { return "disabled" in this.attrs; }
  set disabled(v) { if (v) this.attrs.disabled = ""; else delete this.attrs.disabled; }
  get hidden() { return "hidden" in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = ""; else delete this.attrs.hidden; }
  get textContent() { return this.nodeType === 3 ? this._t : this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) {
    if (this.nodeType === 3) { this._t = String(v); return; }
    this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = [];
    if (v !== "" && v !== null && v !== undefined) this.appendChild(document.createTextNode(v));
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) {
    if (c.nodeType === 11) { c.childNodes.slice().forEach((x) => this.appendChild(x)); return c; }
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this; this.childNodes.push(c); return c;
  }
  append(...xs) { xs.forEach((x) => this.appendChild(x && typeof x === "object" ? x : document.createTextNode(x))); }
  replaceChildren(...xs) { this.textContent = ""; this.append(...xs); }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  addEventListener(t, f) { (this.ls[t] = this.ls[t] || []).push(f); }
  removeEventListener(t, f) { this.ls[t] = (this.ls[t] || []).filter((g) => g !== f); }
  dispatch(type) {
    let stop = false;
    const ev = { type, target: this, stopPropagation() { stop = true; }, preventDefault() {} };
    for (let x = this; x && !stop; x = x.parentNode) (x.ls[type] || []).slice().forEach((f) => f(Object.assign(ev, { currentTarget: x })));
  }
  click() { if (this.disabled) return; this.dispatch("click"); }
  focus() {} blur() {}
  matches(sel) { return sel.split(",").some((s) => fakeMatch(this, s.trim())); }
  closest(sel) { for (let x = this; x && x.nodeType === 1; x = x.parentNode) if (x.matches(sel)) return x; return null; }
  querySelectorAll(sel) { const out = []; const walk = (n) => n.children.forEach((c) => { if (c.matches(sel)) out.push(c); walk(c); }); walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
function fakeMatch(n, s) {
  const m = s.match(/^([a-zA-Z*]*)(.*)$/);
  if (m[1] && m[1] !== "*" && n.tagName !== m[1].toUpperCase()) return false;
  let rest = m[2], r;
  while (rest) {
    if ((r = rest.match(/^\.([\w-]+)/))) { if (!n.className.split(/\s+/).includes(r[1])) return false; }
    else if ((r = rest.match(/^\[([\w-]+)(?:="([^"]*)")?\]/))) { const v = n.getAttribute(r[1]); if (v === null || (r[2] !== undefined && v !== r[2])) return false; }
    else if ((r = rest.match(/^:not\(([^()]*)\)/))) { if (fakeMatch(n, r[1])) return false; }
    else throw new Error("selector " + s);
    rest = rest.slice(r[0].length);
  }
  return true;
}
const document = {
  createElement: (t) => new FakeNode(t), createElementNS: (ns, t) => new FakeNode(t),
  createTextNode: (s) => { const n = new FakeNode("#text"); n._t = String(s); return n; },
  createDocumentFragment: () => new FakeNode("#frag"),
  body: new FakeNode("body"), activeElement: null, addEventListener() {},
};
const window = { KaskadIcons: null };
// helpers of the checks
const textOf = (n) => n.textContent;
const byText = (root, sel, text) => root.querySelectorAll(sel).filter((b) => b.textContent.trim() === text)[0] || null;
const tick = () => new Promise((r) => setTimeout(r, 0));
"""

APP_NAMES_CONST = ("known", "isObj", "arr", "txt", "AL_GLYPH", "U", "rfOf", "rfAsks", "rfNeedOf", "rfTick", "rfOrgNames",
                   "rfOffNote", "rfOrgsKey", "RULES_FORM", "rulesFormReset", "rulesFormOf", "RU_TRACE", "TRACE_WHO", "rsName")
APP_NAMES_FN = ("el", "icon", "append", "findAt", "rfBox", "rfDialog", "rfAct", "rfPick", "devRulesPick", "rfPlaque",
                "rfMarkedRows", "rulesPart", "ruTraceCard", "ruSitesRow", "ruPickDialog")   # Q6: «Российские сайты»


def app_scope():
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in APP_NAMES_CONST] + [jsparts.function(js, n) for n in APP_NAMES_FN]
    parts.append("const PAGES = {};\n" + jsparts.assign(js, "PAGES.rules"))
    return r"""
const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
const tn = (key, n, vars) => t(key, Object.assign({ n }, vars || {}));
const fmtInt = (n) => String(n), fmtTs = (x) => String(x), listsLabel = () => "lists";
const DASH = "—", kindOf = () => "", layerStack = [];
const TOASTS = [];
const toast = (title, text, tone) => { TOASTS.push([title, text, tone]); }, showError = () => {}, resError = () => "", rulesTableAlert = () => null;
const loadAll = () => Promise.resolve();
// the requests the page makes: CALLS [{name, body}]; REJECT — the next answers are refusals (409 with data)
const CALLS = [], REJECT = [];
const rec = (name) => (b) => { CALLS.push({ name, body: JSON.parse(JSON.stringify(b === undefined ? null : b)) });
  const no = REJECT.shift(); return no ? Promise.reject(no) : Promise.resolve({ ok: true }); };
const API = { rules: { profile: rec("profile"), consent: rec("consent") }, devices: { rules: (id, b) => rec("device:" + id)(b) } };
async function act(fn, o = {}) {
  try { const r = await fn(); return r || {}; }
  catch (e) { if (o.onError && (await o.onError(e)) === true) return null; return null; }
}
// openDialog of the panel: the blocks and the foot of the window (DLG — the windows opened)
const DLG = [];
function openDialog(o) {
  const d = { o, open: true, root: null, close() { d.open = false; }, setFoot(xs) { xs.forEach((x) => d.root.append(x)); } };
  d.root = el("div", { "data-dlg": String(DLG.length) }, o.blocks || []);
  DLG.push(d);
  return d;
}
// blk: the block is built now and again on scheduleRefresh (as the panel does on fresh data)
const BLKS = [];
function blk(compute, build) {
  const holder = el("div", { "data-blk": "" });
  const draw = () => { holder.replaceChildren(); append(holder, [build(compute())]); };
  draw(); BLKS.push(draw); return holder;
}
function scheduleRefresh() { BLKS.forEach((f) => f()); }
const R = { rules: { loaded: true, missing: false, error: null, data: null }, people: { loaded: false, data: null } };
// nav2 r1: «Правила» — части; адрес части — NAV.tab (navState), подменю и шапка — не здесь: каркас кладёт часть в root
let NAV = { tab: "" };
const navState = () => NAV, n2Cur = (sec, n) => (n && n.tab) || "profile", n2Canon = () => {}, n2Spec = () => ({}), n2Label = () => "", n2Head = () => ({});
const partFrame = (root, spec, label, kids) => { kids.filter(Boolean).forEach((k) => root.append(k)); }, partPick = () => null;
const TRACES = [], traceNav = (ref) => TRACES.push(ref);
// the phone (3.3.6, М11): PHONE = true and window.Kit — the page builds the phone sections (a stub of the Kit
// builders: a section and a list of rows are plain holders, the radio, the box and «Сохранить» are the same U parts)
let PHONE = false;
const isPhone = () => PHONE;
const KIT_STUB = {
  msec: (title, body) => el("section", { class: "gx-msec", "aria-label": title || null }, [].concat(body).filter(Boolean)),
  mlist: (rows) => el("div", { class: "gx-mlist" }, (rows || []).filter(Boolean)),
  mkv: (k, v) => el("div", { class: "gx-mrow" }, txt(String(k) + " "), v && v.nodeType ? v : txt(String(v))),
  moreLine: (text) => el("p", { class: "gx-hint", text }),
};
""" + "\n".join(parts)


GRAPH_FN = ("h", "append", "clear", "btn", "rfInfo", "rfAsks", "rfBox", "rfKey", "rfReset", "rfSync", "withRf",
            "pending", "intentKey", "sameAsBase", "copyRoute", "sameRoute", "rebuildDraft", "applyToDraft",
            "addIntent", "keptFail", "inheritedRoute", "resyncHist", "undo", "dropBad", "showDiff", "changeLine",
            "openApply", "doApply", "openDialog", "closeDialog", "onChange", "onClick")


def graph_scope():
    js = read("graph.js")
    return r"""
var OWN = Object.prototype.hasOwnProperty, HIST_MAX = 50;
function t(key) { return key; }
function tn(key, n) { return key + " " + n; }
function icon() { return document.createElement("svg"); }
function toast() {} function structureChanged() {} function updateToolbar() {} function renderInspector() {}
function intentText(it) { return JSON.stringify(it); } function showApplyError(r) { ERRS.push(r); }
function setBase() {} function load() {} function later() {}
var ERRS = [];
// the scheme's call: CALLS [{method, path, body}]; ANSWERS — what the server says next
var CALLS = [], ANSWERS = [];
function call(method, path, body) {
  CALLS.push({ method: method, path: path, body: JSON.parse(JSON.stringify(body)) });
  return Promise.resolve(ANSWERS.shift() || { ok: true, data: {} });
}
var NEED = { need: "rf_consent", block: "BLOCK", consent: "TICK", orgs: ["meta"] };
function user(id, devs) { return { id: id, type: "user", data: { route: null, rules: null, devices: devs.map(function (d) { return { id: d }; }) } }; }
function dev(id) { return { id: id, user: "u1", access: [], route: null, rules: null }; }
var S = {
  base: { profiles: { supported: true, names: ["all_exit", "ru_direct"], titles: {}, rf: { mode: "none", required: true, need: NEED } },
          default_route: { via: "pool", fail: "block" }, gateway: { enabled: true } },
  idx: { users: [user("u1", ["dA", "dB"])], devList: [dev("dA"), dev("dB")], dev: {}, user: {} },
  intents: [], hist: [], draft: null, rfTick: false, rfOrgs: null, rfFor: null, applyCtx: null, dialog: null, busy: false,
  dom: {}
};
S.idx.devList.forEach(function (d) { S.idx.dev[d.id] = d; });
S.idx.users.forEach(function (u) { S.idx.user[u.id] = u.data; });
S.dom.root = document.createElement("div");
S.dom.dlg = document.createElement("div"); S.dom.root.appendChild(S.dom.dlg);
S.dom.insp = document.createElement("div"); S.dom.root.appendChild(S.dom.insp);
""" + "\n".join(jsparts.function(js, n) for n in GRAPH_FN) + r"""
rebuildDraft();
// the box under a choice in the inspector — as devInsp puts it (into the scheme's root: its clicks and changes)
function inspBox() { clear(S.dom.insp); var b = rfBox(); S.dom.insp.appendChild(b); return b.querySelector('[data-act="rf-tick"]'); }
function tickIt(cb) { cb.checked = true; onChange({ target: cb }); }
function press(sel) { var b = S.dom.root.querySelector(sel); if (!b || b.disabled) return false; onClick({ target: b }); return true; }
function dry(n) { return { ok: true, data: { dry_run: true, valid: true, noop: false, version: "v1", changes: [{ op: "~", text: "x" }],
  results: Array.apply(null, Array(n)).map(function (_, i) { return i === 0 ? { i: 0, ok: true, need: NEED } : { i: i, ok: true }; }) } }; }
function rfBodies() { return CALLS.map(function (c) { return (c.body.intents || []).filter(function (it) { return it.confirm_not_rf; }).length; }); }
"""


def run(scope, script):
    """``scope`` + ``script`` in node; the script prints one JSON line."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(DOM + scope + "\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify({crash: String(e && "
                "e.stack || e)})); });\n" % script)
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


RULES = {"default": "all_exit", "ver": "v1", "devices": [], "ru_direct": 0,
         "rf": {"required": True, "mode": "none", "orgs": [{"id": "meta", "name": "Meta"}], "uncovered": [],
                "marked": {"force": []}, "marked_n": {"force": 0}, "scan": None,
                "need": {"need": "rf_consent", "block": "BLOCK", "consent": "TICK", "orgs": ["meta"]}}}


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class RulesPageTest(unittest.TestCase):
    KIND = "card"

    def page(self, script, data=None):
        return run(app_scope(), "R.rules.data = %s;\nconst root = el('div');\nPAGES.rules.build(root);\n"
                   "const radio = (k) => root.querySelectorAll('[role=\"radio\"]')[k === 'ru_direct' ? 1 : 0];\n"
                   "const save = () => byText(root, 'button', 'rf.save');\n"
                   "const box = () => root.querySelector('[data-rf=\"box\"]');\n"
                   "const boxTick = () => box().querySelector('[role=\"checkbox\"]');\n%s"
                   % (json.dumps(data or RULES), script))

    def test_save_waits_for_the_tick(self):
        out = self.page(r"""
radio('ru_direct').click();
const o = { box: !!box(), checked: boxTick().getAttribute('aria-checked'), disabled: save().disabled };
save().click(); await tick();
o.sentBefore = CALLS.length;
boxTick().click();
o.checkedAfter = boxTick().getAttribute('aria-checked'); o.disabledAfter = save().disabled;
boxTick().click();                                    // taken back — held again
o.disabledUntick = save().disabled;
save().click(); await tick();
o.sentUntick = CALLS.length;
boxTick().click(); save().click(); await tick();
o.calls = CALLS;
console.log(JSON.stringify(o));
""")
        self.assertTrue(out["box"])
        self.assertEqual(out["checked"], "false")                          # the box comes unticked
        self.assertTrue(out["disabled"])                                   # «Сохранить» held
        self.assertEqual(out["sentBefore"], 0)                             # a click on it sends nothing
        self.assertEqual((out["checkedAfter"], out["disabledAfter"]), ("true", False))
        self.assertTrue(out["disabledUntick"])
        self.assertEqual(out["sentUntick"], 0)
        self.assertEqual(out["calls"], [{"name": "profile", "body": {"target": "default", "profile": "ru_direct",
                                                                     "ver": "v1", "confirm_not_rf": True,
                                                                     "orgs": ["meta"]}}])

    def test_no_tick_where_none_is_asked(self):
        """The consent is there (``required`` false) or the choice is «всё через выход»: no block, the body has no
        ``confirm_not_rf`` — the page never ticks by itself."""
        data = json.loads(json.dumps(RULES))
        data["rf"]["required"] = False
        out = self.page(r"""
radio('ru_direct').click();
const o = { box: !!box(), disabled: save().disabled };
save().click(); await tick();
R.rules.data.default = 'ru_direct'; RULES_FORM.pick = null; scheduleRefresh();
radio('all_exit').click(); save().click(); await tick();
o.calls = CALLS;
console.log(JSON.stringify(o));
""", data)
        self.assertEqual((out["box"], out["disabled"]), (False, False))
        self.assertEqual([c["body"] for c in out["calls"]],
                         [{"target": "default", "profile": "ru_direct", "ver": "v1"},
                          {"target": "default", "profile": "all_exit", "ver": "v1"}])

    def test_back_to_all_exit_drops_the_tick(self):
        out = self.page(r"""
radio('ru_direct').click(); boxTick().click();
radio('all_exit').click();                            // the choice taken back: no bar, no block
const o = { bar: !!save(), box: !!box() };
radio('ru_direct').click();
o.checked = boxTick().getAttribute('aria-checked'); o.disabled = save().disabled;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"bar": False, "box": False, "checked": "false", "disabled": True})

    def test_leaving_the_page_forgets_the_choice(self):
        """П-3: an unsaved choice and its tick do not wait for the return to «Правила»."""
        out = self.page(r"""
radio('ru_direct').click(); boxTick().click();
PAGES.rules.leave();
scheduleRefresh();
console.log(JSON.stringify({ form: RULES_FORM, bar: !!save(), box: !!box(), on: radio('ru_direct').getAttribute('aria-checked') }));
""")
        self.assertEqual(out, {"form": {"pick": None, "tick": False, "orgs": None}, "bar": False, "box": False, "on": "false"})

    def test_unsaved_choice_is_dirty(self):
        """Ветка выпуска 3.3.7 (R4 ↔ T8): несохранённый выбор «Правил» — правка формы: setPage спрашивает при уходе
        (pageDirty → askLeave, D-077), а не теряет выбор и галочку молча; «Уйти» — discard."""
        out = self.page(r"""
const o = { clean: PAGES.rules.dirty() };
radio('ru_direct').click(); boxTick().click();
o.dirty = PAGES.rules.dirty(); o.kept = Object.assign({}, RULES_FORM);
PAGES.rules.discard();
o.after = { dirty: PAGES.rules.dirty(), form: RULES_FORM };
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"clean": False, "dirty": True, "kept": {"pick": "ru_direct", "tick": True, "orgs": ["meta"]},
                               "after": {"dirty": False, "form": {"pick": None, "tick": False, "orgs": None}}})

    def test_new_orgs_take_the_tick_away(self):
        """REV-s1-rel-2 С-1: the tick is given under the block of [meta]; the data of «Правила» came again and the block
        is [meta, fbk] now — the tick is off, «Сохранить» held, a click sends nothing; the same set — the tick stays
        (a rebuild does not drop it). Ticked again — the body carries the new set."""
        out = self.page(r"""
radio('ru_direct').click(); boxTick().click();
R.rules.data = Object.assign({}, R.rules.data);                       // fresh data, the same set: the tick stays
scheduleRefresh();
const o = { same: boxTick().getAttribute('aria-checked'), sameSave: save().disabled };
R.rules.data = JSON.parse(JSON.stringify(R.rules.data));
R.rules.data.rf.need.orgs = ['meta', 'fbk']; R.rules.data.rf.need.block = 'WIDER';
scheduleRefresh();
o.block = textOf(box()).indexOf('WIDER') >= 0;
o.checked = boxTick().getAttribute('aria-checked'); o.disabled = save().disabled; o.form = Object.assign({}, RULES_FORM);
save().click(); await tick();
o.sent = CALLS.length;
boxTick().click(); save().click(); await tick();
o.calls = CALLS;
console.log(JSON.stringify(o));
""")
        self.assertEqual((out["same"], out["sameSave"]), ("true", False))
        self.assertTrue(out["block"])
        self.assertEqual((out["checked"], out["disabled"]), ("false", True))
        self.assertEqual(out["form"], {"pick": "ru_direct", "tick": False, "orgs": None})
        self.assertEqual(out["sent"], 0)
        self.assertEqual(out["calls"], [{"name": "profile", "body": {"target": "default", "profile": "ru_direct",
                                                                     "ver": "v1", "confirm_not_rf": True,
                                                                     "orgs": ["meta", "fbk"]}}])

    def test_untick_survives_a_refresh(self):
        """REV-s1-rel-2 З-2: ticked, then unticked — fresh data does not tick it again, «Сохранить» stays held."""
        out = self.page(r"""
radio('ru_direct').click(); boxTick().click(); boxTick().click();
R.rules.data = JSON.parse(JSON.stringify(R.rules.data));
scheduleRefresh();
const o = { checked: boxTick().getAttribute('aria-checked'), disabled: save().disabled };
save().click(); await tick();
o.sent = CALLS.length;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"checked": "false", "disabled": True, "sent": 0})

    def test_choice_equal_to_the_node_is_not_dirty(self):
        """REV-s1-rel-2 П-1: выбрали «Россия напрямую», а профиль узла сменили на тот же в другом месте — полосы нет,
        и уход не спрашивает «без сохранения» (сравнение с текущим профилем, не сырой выбор)."""
        out = self.page(r"""
radio('ru_direct').click();
const o = { dirty: PAGES.rules.dirty(), bar: !!save() };
R.rules.data = Object.assign({}, R.rules.data, { default: 'ru_direct' }); scheduleRefresh();
o.dirtyAfter = PAGES.rules.dirty(); o.barAfter = !!save();
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"dirty": True, "bar": True, "dirtyAfter": False, "barAfter": False})

    def test_leave_question_names_no_tab(self):
        """REV-s1-rel-2 П-2: вопрос при уходе общий (у «Правил» вкладок нет) — без слова «вкладка»/«tab»."""
        for name, word in (("i18n-ru.js", "вкладк"), ("i18n-en.js", "tab")):
            line = [x for x in read(name).splitlines() if '"ex.leaveText"' in x]
            self.assertEqual(len(line), 1, name)
            self.assertNotIn(word, line[0].lower(), name)

    def test_need_marks_and_the_find_place(self):
        """REV-s1-rel-2 З-1 (телефон и компьютер), UI-A2 (UI-6 п. 4 — плашка ⚠РФ одна, в «Списках»): в режиме need
        первый узел части «Профиль» — строка «Нужно подтверждение» со ссылкой в «Списки» (to-lists), плашки там нет; у
        выбора профиля место поиска «rules.profile» (там радио); в части «Списки» плашка первой и пометка записей ⚠РФ
        (rfMarkedRows): туда ведёт строка «Сводки» «Нужно подтверждение ⚠РФ» (#rules?tab=lists). «Блокировка» — без неё."""
        data = json.loads(json.dumps(RULES))
        data["rf"]["mode"] = "need"
        data["rf"]["marked"] = {"force": [{"value": "facebook.com", "org": "meta"}]}
        data["rf"]["marked_n"] = {"force": 1}
        out = self.page(r"""
const first = (r) => { const top = r.querySelector('[data-blk]').children.filter((n) => n.nodeType === 1)[0]; return top ? top.getAttribute('data-rf') : null; };
const place = root.querySelector('[data-find="rules.profile"]');
const o = { first: first(root), place: !!place, radio: !!(place && place.querySelector('[role="radio"]')), force: root.querySelectorAll('[data-rf="force"]').length };
for (const tab of ["lists", "block"]) {
  NAV = { tab }; const r = el('div'); PAGES.rules.build(r);
  o[tab] = { first: first(r), force: r.querySelectorAll('[data-rf="force"]').length, radio: r.querySelectorAll('[role="radio"]').length };
}
console.log(JSON.stringify(o));
""", data)
        self.assertEqual(out, {"first": "to-lists", "place": True, "radio": True, "force": 0,
                               "lists": {"first": "plaque-need", "force": 1, "radio": 0},
                               "block": {"first": None, "force": 0, "radio": 0}})

    def test_trace_part_goes_to_the_scheme(self):
        """nav2 r1: часть «Куда пойдёт трафик» — домен или IP (и, по желанию, чьё устройство) → схема с ответом
        (#scheme?trace=<кто>:<что>, как поле телефонной «Схемы»). Пусто или с пробелом — подсказка у поля, перехода нет;
        чужой «кто» (не d-/u- с 8 hex) — «-»."""
        out = self.page(r"""
NAV = { tab: "trace" }; const r = el('div'); PAGES.rules.build(r);
const inp = r.querySelector('input'), go = byText(r, 'button', 'ru.traceGo'), err = () => r.querySelector('[role="alert"]').textContent;
const o = { kind: r.querySelectorAll('.kk-part__form').length ? 'card' : r.querySelectorAll('.gx-msec').length ? 'msec' : '', opts: r.querySelector('select').childNodes.length };
go.click(); o.empty = [err(), inp.getAttribute('aria-invalid'), TRACES.length];
inp.value = "a b"; go.click(); o.space = [err(), TRACES.length];
inp.value = "  example.com "; inp.dispatch("input"); o.cleared = [err(), inp.getAttribute('aria-invalid')];
go.click(); RU_TRACE.who = "d-1234abcd"; go.click(); RU_TRACE.who = "x:evil"; go.click();
o.traces = TRACES;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["kind"], self.KIND)                           # компьютер — карточка-форма, телефон — раздел Kit
        self.assertEqual(out["opts"], 1)                                    # без данных «Людей» — только «новое устройство»
        self.assertEqual(out["empty"], ["ru.traceEmpty", "true", 0])
        self.assertEqual(out["space"], ["ru.traceBad", 0])
        self.assertEqual(out["cleared"], ["", None])
        self.assertEqual(out["traces"], ["-:example.com", "d-1234abcd:example.com", "-:example.com"])

    def test_need_says_it_once(self):
        """З-2: «Россия напрямую» of the node does not act (no consent): the plaque says it, the card only marks the
        choice; without a plaque (no data of rf) — the warning in the card."""
        data = json.loads(json.dumps(RULES))
        data.update({"default": "ru_direct", "default_applied": "all_exit", "default_why": "no_consent"})
        data["rf"]["mode"] = "need"
        out = self.page(r"""
const count = (s) => textOf(root).split(s).length - 1;
const o = { need: count('rf.need'), plaque: !!root.querySelector('[data-rf="to-lists"]') && !root.querySelector('[data-rf="plaque-need"]'),
            note: (root.querySelector('.gx-radio__n') || { textContent: null }).textContent };
R.rules.data.rf = null; scheduleRefresh();
o.needNoPlaque = count('rf.need'); o.noteNoPlaque = !!root.querySelector('.gx-radio__n');
console.log(JSON.stringify(o));
""", data)
        self.assertEqual(out["need"], 1)
        self.assertTrue(out["plaque"])
        self.assertEqual(out["note"], "Rf.off")                              # t() of the test gives the key
        self.assertEqual((out["needNoPlaque"], out["noteNoPlaque"]), (1, False))


    def test_one_plaque_and_names_once(self):
        """UI-A2 (UI-6 п. 4, UX-аудит 20.10 «Meta 4 раза»): плашка ⚠РФ — только в «Списках»; организации названы один
        раз — в блоке сервера: текст плашки и строка «Всегда через выход» их не повторяют (rf.forceN без имён). Без
        плашки (согласие есть) строка называет их сама. «Профиль» — строка со ссылкой: нажатие ведёт в «Списки»."""
        data = json.loads(json.dumps(RULES))
        data["rf"]["mode"] = "legacy"
        data["rf"]["marked"] = {"force": [{"value": "facebook.com", "org": "meta"}]}
        data["rf"]["marked_n"] = {"force": 1}
        out = self.page(r"""
globalThis.GO = []; globalThis.goNav = (p, q) => GO.push([p, q]);
const plaques = (n) => ["need", "legacy", "full"].reduce((a, m) => a + n.querySelectorAll('[data-rf="plaque-' + m + '"]').length, 0);
const o = { profilePlaques: plaques(root), link: !!root.querySelector('[data-rf="to-lists"]') };
byText(root.querySelector('[data-rf="to-lists"]'), 'button', 'rf.toListsBtn').click();
o.go = GO;
NAV = { tab: "lists" }; const r = el('div'); PAGES.rules.build(r);
const txt = textOf(r);
o.lists = { plaques: plaques(r), meta: txt.split('Meta').length - 1, quiet: txt.indexOf('rf.forceN') >= 0 };
R.rules.data = Object.assign({}, R.rules.data, { rf: Object.assign({}, R.rules.data.rf, { mode: "full" }) }); scheduleRefresh();
const t2 = textOf(r);
o.noPlaque = { plaques: plaques(r), meta: t2.split('Meta').length - 1 };
console.log(JSON.stringify(o));
""", data)
        self.assertEqual((out["profilePlaques"], out["link"]), (0, True))
        self.assertEqual(out["go"], [["rules", {"tab": "lists"}]])
        self.assertEqual(out["lists"], {"plaques": 1, "meta": 0, "quiet": True})     # имена — в блоке сервера (BLOCK)
        self.assertEqual(out["noPlaque"], {"plaques": 0, "meta": 1})


class PhoneRulesPageTest(RulesPageTest):
    """3.3.6 ↔ R4 (ветка выпуска 3.3.7): на телефоне «Правила» собираются своими разделами (М11) — тот же выбор до
    «Сохранить», тот же блок с галочкой и та же полоса: «Россия напрямую» без галочки не уходит и отсюда."""
    KIND = "msec"

    def page(self, script, data=None):
        return super().page("PHONE = true; window.Kit = KIT_STUB;\nscheduleRefresh();\n" + script, data)

    def test_phone_sections_are_built(self):
        # nav2 r1: на телефоне — часть своим разделом строк (М11), выбор части — кнопкой над ней (partPick)
        out = self.page(r"""
const o = {};
for (const tab of ["profile", "block", "lists", "trace"]) {
  NAV = { tab }; const r = el('div'); PAGES.rules.phone(r, NAV);
  o[tab] = [r.querySelectorAll('.gx-msec').length, r.querySelectorAll('.gx-mlist').length > 0, r.querySelectorAll('.gx-card').length];
}
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"profile": [1, False, 0], "block": [1, True, 0], "lists": [1, True, 0], "trace": [1, False, 0]})


# nav2 r1: «Обзор» — части; «Сводка» и «Здоровье сервера» — часть ovb (ovbMount, круг 1 Т1), «События» — ovEvents
# (компьютер) и ovMevAll (телефон); каркас части (partFrame, partPick, n2*) — заглушки: проверяется содержимое.
# Старые ovTiles, ovAttCard, ovHost, ovAttention, ovMkpis, ovMevents, ovMhost сняты (REV-it1 №4)
OV_PARTS = ("ovMtu", "ovEvCard", "ovEvents", "ovMevAll")


def overview_scope():
    js = read("app.js")
    return (app_scope() + "\n" + "\n".join("const %s = () => el('div', { 'data-ov': '%s' });" % (n, n) for n in OV_PARTS)
            + "\nconst GO = [], goNav = (p, q) => GO.push([p, q]), setPage = (p) => GO.push([p]), P = { people: {} };"
            + "\nconst enableGateway = () => {}, inboundTitle = (k) => k, awgDetails = () => {}, openBottom = () => {};"
            + "\nconst ovCur = () => 'sum', ovDiagOff = () => {}, ovDiagMount = () => {}; let ovShown = '', ovDiag = null;\n"
            + "const ovbMount = (b, fn) => el('div', { 'data-ov': 'ovb:' + fn });\n"
            + jsparts.const(js, "OV_DIAG") + "\n" + jsparts.function(js, "ovPart") + "\n"
            + jsparts.function(js, "ovIssues") + "\n" + jsparts.assign(js, "PAGES.overview"))


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class OverviewRfTest(unittest.TestCase):
    """UI-6 (аудит 20.10 №4, №5): подтверждение ⚠РФ — только в «Правилах»; на «Обзоре» (компьютер и телефон) — одна
    строка сводки со ссылкой в «Правила». Одинаковые строки сводки схлопываются с числом; выход, который не работает, —
    в сводке. Данные «Правил» «Обзор» читает сам (phoneRes с "rules")."""
    def test_no_plaque_on_overview(self):
        out = run(overview_scope(), r"""
const o = { res: PAGES.overview.res.indexOf("rules") >= 0, phoneRes: PAGES.overview.phoneRes.indexOf("rules") >= 0 };
for (const view of ["build", "phone"]) { const root = el("div"); PAGES.overview[view](root); o[view] = root.querySelectorAll('[data-rf]').length; }
console.log(JSON.stringify(o));
""")
        self.assertEqual(out, {"res": True, "phoneRes": True, "build": 0, "phone": 0})

    def test_summary_lines(self):
        out = run(overview_scope(), r"""
const v = { off: false, problems: ["Протокол AWG 1.5 не работает — служба", "Протокол AWG 1.5 не работает — служба", "Протокол AWG 1.5 не работает — служба"],
  warnings: [], err: "", awg: "offer", awgList: [], down: [{ kind: "awg1", title: "AWG 1.5", why: "служба" }, { kind: "l2tp", title: "L2TP", why: "порт" }],
  dead: [{ id: "e-1", name: "Токио" }], noExit: 2, rf: "need", mtu: [] };
const rows = ovIssues(v);
rows.find((r) => r.key === "rf").go();
P.people.q = "старый поиск"; rows.find((r) => r.key === "noexit").go();
console.log(JSON.stringify({ keys: rows.map((r) => r.key + ":" + r.n), go: GO, q: P.people.q }));
""")
        self.assertEqual(out["keys"], ["p:Протокол AWG 1.5 не работает:3", "ib:l2tp:1", "ex:e-1:1", "noexit:1", "rf:1"])
        # nav2 r1: «Без выхода» — фильтр «Людей» адресом (#people?show=noexit), поиск сброшен — видны все такие люди
        self.assertEqual(out["go"], [["rules", {"tab": "lists"}], ["people", {"show": "noexit"}]])
        self.assertEqual(out["q"], "")


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class DialogAndPlaqueTest(unittest.TestCase):
    def test_device_window_waits_for_the_tick(self):
        out = run(app_scope(), r"""
R.rules.data = %s;
const d = { id: "d-1" };
const o = {};
let p = devRulesPick(d, "inherit", "ru_direct");
await tick();
o.windows = DLG.length; o.sentAtOnce = CALLS.length;
const w = DLG[0].root, ok = byText(w, "button", "rf.enable"), cb = w.querySelector('[role="checkbox"]');
o.checked = cb.getAttribute("aria-checked"); o.disabled = ok.disabled;
ok.click(); await tick();
o.sentBefore = CALLS.length;
cb.click(); o.disabledAfter = ok.disabled;
ok.click(); await p;
o.calls = CALLS.slice();
// the window cancelled — nothing sent
p = devRulesPick(d, "inherit", "ru_direct"); await tick();
byText(DLG[1].root, "button", "c.cancel").click(); await p;
o.afterCancel = CALLS.length;
// the consent is there — no window, no tick in the body
R.rules.data.rf.required = false;
await devRulesPick(d, "inherit", "ru_direct");
o.windowsNoAsk = DLG.length; o.last = CALLS[CALLS.length - 1];
console.log(JSON.stringify(o));
""" % json.dumps(RULES))
        self.assertEqual((out["windows"], out["sentAtOnce"]), (1, 0))       # the window first, nothing sent
        self.assertEqual((out["checked"], out["disabled"]), ("false", True))
        self.assertEqual(out["sentBefore"], 0)
        self.assertFalse(out["disabledAfter"])
        self.assertEqual(out["calls"], [{"name": "device:d-1", "body": {"profile": "ru_direct", "confirm_not_rf": True,
                                                                        "orgs": ["meta"]}}])
        self.assertEqual(out["afterCancel"], 1)
        self.assertEqual(out["windowsNoAsk"], 2)
        self.assertEqual(out["last"], {"name": "device:d-1", "body": {"profile": "ru_direct"}})

    def test_a_refusal_asks_in_a_window(self):
        """409 with the block (an old tab; the block widened): the window with the server's block, the same request
        again only after the tick."""
        out = run(app_scope(), r"""
R.rules.data = %s;
REJECT.push({ status: 409, data: { need: "rf_consent", block: "WIDER", consent: "TICK", orgs: ["meta", "fbk"] } });
const p = devRulesPick({ id: "d-1" }, "ru_direct", "all_exit");
await tick(); await tick();
const w = DLG[0].root, ok = byText(w, "button", "rf.enable");
const o = { block: w.textContent.indexOf("WIDER") >= 0, disabled: ok.disabled, sent: CALLS.length };
w.querySelector('[role="checkbox"]').click(); ok.click(); await p; await tick();
o.calls = CALLS;
console.log(JSON.stringify(o));
""" % json.dumps(RULES))
        self.assertEqual((out["block"], out["disabled"], out["sent"]), (True, True, 1))
        self.assertEqual([c["body"] for c in out["calls"]],
                         [{"profile": "all_exit"}, {"profile": "all_exit", "confirm_not_rf": True, "orgs": ["meta", "fbk"]}])

    def test_device_window_keeps_the_block_it_shows(self):
        """REV-s1-rel-2 С-1 for the window of a device: the window shows the block of [meta]; the data changed to
        [meta, fbk] while it is open — the tick goes with the set shown ([meta]), never with the new one: the server
        refuses (a wider block) and the next window shows [meta, fbk] unticked."""
        out = run(app_scope(), r"""
R.rules.data = %s;
const p = devRulesPick({ id: "d-1" }, "inherit", "ru_direct");
await tick();
R.rules.data = JSON.parse(JSON.stringify(R.rules.data)); R.rules.data.rf.need.orgs = ["meta", "fbk"];
REJECT.push({ status: 409, data: { need: "rf_consent", block: "WIDER", consent: "TICK", orgs: ["meta", "fbk"] } });
const w = DLG[0].root;
w.querySelector('[role="checkbox"]').click(); byText(w, "button", "rf.enable").click();
await tick(); await tick();
const o = { first: CALLS[0].body, windows: DLG.length };
const w2 = DLG[1].root, ok2 = byText(w2, "button", "rf.enable");
o.wider = w2.textContent.indexOf("WIDER") >= 0; o.checked2 = w2.querySelector('[role="checkbox"]').getAttribute("aria-checked");
o.disabled2 = ok2.disabled;
byText(w2, "button", "c.cancel").click(); await p;
o.sent = CALLS.length;
console.log(JSON.stringify(o));
""" % json.dumps(RULES))
        self.assertEqual(out["first"], {"profile": "ru_direct", "confirm_not_rf": True, "orgs": ["meta"]})
        self.assertEqual(out["windows"], 2)
        self.assertTrue(out["wider"])
        self.assertEqual((out["checked2"], out["disabled2"]), ("false", True))
        self.assertEqual(out["sent"], 1)

    def test_plaque_tick_goes_with_a_new_set(self):
        """REV-s1-rel-2 С-1 for the plaque (in a block, as on «Обзор» and «Правила»): ticked under [meta], the data
        brought [meta, fbk] — the plaque is the new block, unticked, «Подтвердить» held; ticked again — the new set."""
        data = json.loads(json.dumps(RULES))
        data["rf"]["mode"] = "legacy"
        out = run(app_scope(), r"""
R.rules.data = %s;
const root = el("div", {}, blk(() => ({ rf: rfOf() }), (v) => [rfPlaque(v.rf)]));
const card = () => root.querySelector('[data-rf="plaque-legacy"]'), ok = () => byText(card(), "button", "rf.confirm");
card().querySelector('[role="checkbox"]').click();
const o = { before: ok().disabled };
R.rules.data = JSON.parse(JSON.stringify(R.rules.data));
R.rules.data.rf.need.orgs = ["meta", "fbk"]; R.rules.data.rf.need.block = "WIDER";
scheduleRefresh();
o.wider = textOf(card()).indexOf("WIDER") >= 0;
o.checked = card().querySelector('[role="checkbox"]').getAttribute("aria-checked"); o.disabled = ok().disabled;
ok().click(); await tick();
o.sent = CALLS.length;
card().querySelector('[role="checkbox"]').click(); ok().click(); await tick(); await tick();
o.calls = CALLS;
console.log(JSON.stringify(o));
""" % json.dumps(data))
        self.assertFalse(out["before"])
        self.assertTrue(out["wider"])
        self.assertEqual((out["checked"], out["disabled"], out["sent"]), ("false", True, 0))
        self.assertEqual(out["calls"], [{"name": "consent", "body": {"confirm_not_rf": True, "orgs": ["meta", "fbk"]}}])

    def test_leave_toast_tells_only_what_is_shown(self):
        """REV-s1-t8m-3 П-1: «Сохранить» в вопросе при уходе — save() вернул false: форма показывает причину, тост
        «причина показана у формы»; save() упал исключением — причины у формы нет: тост «Не сохранено» с текстом ошибки.
        В обоих случаях человек остаётся."""
        out = run(app_scope() + "\n" + jsparts.function(read("app.js"), "askLeave"), r"""
const o = {};
for (const [name, save] of [["false", () => Promise.resolve(false)], ["throws", () => { throw new Error("сбой записи"); }],
                            ["rejects", () => Promise.reject(new Error("нет связи"))]]) {
  TOASTS.length = 0;
  const p = askLeave(save);
  byText(DLG[DLG.length - 1].root, "button", "ex.leaveSave").click();
  o[name] = { go: await p, toasts: TOASTS.slice(), open: DLG[DLG.length - 1].open };
}
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["false"], {"go": False, "toasts": [["ex.leaveNotSaved", "", "warning"]], "open": False})
        self.assertEqual(out["throws"], {"go": False, "toasts": [["ex.leaveFailed", "сбой записи", "warning"]], "open": False})
        self.assertEqual(out["rejects"], {"go": False, "toasts": [["ex.leaveFailed", "нет связи", "warning"]], "open": False})

    def test_plaque_confirm_waits_for_the_tick(self):
        data = json.loads(json.dumps(RULES))
        data["rf"]["mode"] = "legacy"
        out = run(app_scope(), r"""
R.rules.data = %s;
const card = rfPlaque(R.rules.data.rf);
const ok = byText(card, "button", "rf.confirm"), cb = card.querySelector('[role="checkbox"]');
const o = { kind: card.getAttribute("data-rf"), checked: cb.getAttribute("aria-checked"), disabled: ok.disabled };
ok.click(); await tick();
o.sentBefore = CALLS.length;
cb.click(); o.disabledAfter = ok.disabled;
ok.click(); await tick(); await tick();
o.calls = CALLS;
o.none = rfPlaque(Object.assign({}, R.rules.data.rf, { mode: "full" })) === null;
console.log(JSON.stringify(o));
""" % json.dumps(data))
        self.assertEqual(out["kind"], "plaque-legacy")
        self.assertEqual((out["checked"], out["disabled"], out["sentBefore"]), ("false", True, 0))
        self.assertFalse(out["disabledAfter"])
        self.assertEqual(out["calls"], [{"name": "consent", "body": {"confirm_not_rf": True, "orgs": ["meta"]}}])
        self.assertTrue(out["none"])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class SchemeRfTest(unittest.TestCase):
    RU_A = "{ type: 'rules', device: 'dA', profile: 'ru_direct' }"
    RU_B = "{ type: 'rules', device: 'dB', profile: 'ru_direct' }"

    def test_inspector_box_and_the_body(self):
        out = run(graph_scope(), r"""
addIntent(%s, true);
var cb = inspBox();
var o = { checked: !!cb.checked, orgs: cb.getAttribute('data-orgs') };
ANSWERS.push({ ok: true, data: { dry_run: true, valid: true, noop: false, version: 'v1', changes: [], results: [{ i: 0, ok: true }] } });
openApply(); await tick();
o.dryBefore = CALLS[0].body.intents;
tickIt(cb);
ANSWERS.push({ ok: true, data: { dry_run: true, valid: true, noop: false, version: 'v1', changes: [], results: [{ i: 0, ok: true }] } });
closeDialog(); openApply(); await tick();
o.dryAfter = CALLS[1].body.intents;
console.log(JSON.stringify(o));
""" % self.RU_A)
        self.assertFalse(out["checked"])                                   # the box comes unticked
        self.assertEqual(out["orgs"], '["meta"]')
        self.assertEqual(out["dryBefore"], [{"type": "rules", "device": "dA", "profile": "ru_direct"}])
        self.assertEqual(out["dryAfter"], [{"type": "rules", "device": "dA", "profile": "ru_direct",
                                            "confirm_not_rf": True, "orgs": ["meta"]}])

    def test_apply_window_waits_for_the_tick(self):
        out = run(graph_scope(), r"""
addIntent(%s, true);
addIntent({ type: 'route', device: 'dB', via: 'block' }, true);
ANSWERS.push(dry(2));
openApply(); await tick();
var go = S.dom.dlg.querySelector('[data-act="do-apply"]'), cb = S.dom.dlg.querySelector('[data-act="rf-tick"]');
var o = { box: !!cb, checked: !!(cb && cb.checked), disabled: go.disabled };
o.pressed = press('[data-act="do-apply"]'); await tick();
o.sentBefore = CALLS.length;
tickIt(cb);
o.disabledAfter = go.disabled;
ANSWERS.push({ ok: true, data: { ok: true, graph: {}, applied: true, changes: [] } });
press('[data-act="do-apply"]'); await tick();
o.apply = CALLS[1].body.intents;
o.left = { tick: S.rfTick, orgs: S.rfOrgs };
console.log(JSON.stringify(o));
""" % self.RU_A)
        self.assertEqual((out["box"], out["checked"], out["disabled"]), (True, False, True))
        self.assertFalse(out["pressed"])
        self.assertEqual(out["sentBefore"], 1)                             # only the dry run
        self.assertFalse(out["disabledAfter"])
        self.assertEqual(out["apply"], [{"type": "rules", "device": "dA", "profile": "ru_direct",
                                         "confirm_not_rf": True, "orgs": ["meta"]},
                                        {"type": "route", "device": "dB", "via": "block", "fail": "block"}])
        self.assertEqual(out["left"], {"tick": False, "orgs": None})

    def test_undo_takes_the_tick_away(self):
        """З-1: A → «Россия напрямую» → tick → Ctrl+Z → B → «Россия напрямую»: B's box is unticked and the body
        carries no tick."""
        out = run(graph_scope(), r"""
addIntent(%s, true);
tickIt(inspBox());
var o = { ticked: S.rfTick };
undo();
o.afterUndo = S.rfTick;
addIntent(%s, true);
var cb = inspBox();
o.checkedB = !!cb.checked;
ANSWERS.push(dry(1));
openApply(); await tick();
o.bodies = rfBodies();
console.log(JSON.stringify(o));
""" % (self.RU_A, self.RU_B))
        self.assertEqual(out, {"ticked": True, "afterUndo": False, "checkedB": False, "bodies": [0]})

    def test_a_new_choice_asks_again(self):
        """Ticked under A, then B chosen too: the tick was not given under B — asked again."""
        out = run(graph_scope(), r"""
addIntent(%s, true);
tickIt(inspBox());
addIntent(%s, true);
console.log(JSON.stringify({ tick: S.rfTick, checked: !!inspBox().checked, body: withRf(pending()).filter(function (it) { return it.confirm_not_rf; }).length }));
""" % (self.RU_A, self.RU_B))
        self.assertEqual(out, {"tick": False, "checked": False, "body": 0})

    def test_drop_bad_stale_and_closing_take_the_tick_away(self):
        out = run(graph_scope(), r"""
var o = {};
// «Убрать ошибочные»: the ticked choice was the wrong one
addIntent(%s, true);
tickIt(inspBox());
addIntent({ type: 'route', device: 'dB', via: 'block' }, true);
S.applyCtx = { list: pending(), bad: [0], version: 'v1' }; S.dialog = {};
ANSWERS.push({ ok: true, data: { dry_run: true, valid: true, noop: false, version: 'v1', changes: [], results: [{ i: 0, ok: true }] } });
dropBad(); await tick();
o.dropBad = S.rfTick;
// the device gone elsewhere (bot): rebuildDraft after the stale intents dropped
S.intents = []; S.hist = []; rebuildDraft();
addIntent(%s, true);
tickIt(inspBox());
S.intents = S.intents.filter(function (it) { return it.device !== 'dA'; }); rebuildDraft();
o.stale = S.rfTick;
// the window «Применить» with the block: ticked there, then closed
S.intents = []; S.hist = []; rebuildDraft();
addIntent(%s, true);
ANSWERS.push(dry(1));
openApply(); await tick();
tickIt(S.dom.dlg.querySelector('[data-act="rf-tick"]'));
o.inWindow = S.rfTick;
press('[data-act="dlg-close"]');
o.closed = S.rfTick;
console.log(JSON.stringify(o));
""" % (self.RU_A, self.RU_A, self.RU_A))
        self.assertEqual(out, {"dropBad": False, "stale": False, "inWindow": True, "closed": False})

    def test_server_asks_again(self):
        """409 ``need`` on «Применить» (the block widened): the tick is dropped, the window again."""
        out = run(graph_scope(), r"""
addIntent(%s, true);
ANSWERS.push(dry(1));
openApply(); await tick();
tickIt(S.dom.dlg.querySelector('[data-act="rf-tick"]'));
ANSWERS.push({ ok: false, data: { data: { need: 'rf_consent' } } });
ANSWERS.push(dry(1));
press('[data-act="do-apply"]'); await tick(); await tick();
var go = S.dom.dlg.querySelector('[data-act="do-apply"]');
console.log(JSON.stringify({ tick: S.rfTick, disabled: go.disabled, checked: !!S.dom.dlg.querySelector('[data-act="rf-tick"]').checked, bodies: rfBodies() }));
""" % self.RU_A)
        self.assertEqual(out, {"tick": False, "disabled": True, "checked": False, "bodies": [0, 1, 0]})


if __name__ == "__main__":
    unittest.main()
