"""Kaskad 3.3.6: small things of the panel from the checks of 3.3.5.

- REV-335-7 Т1, П3, П4: a question in place (app.js and ux.js confirmLife) — on a touch screen pointerup comes before
  mousedown and the move of the focus (pointerdown → pointerup → mousedown → focusin → mouseup → click), so «pressed»
  lasts until the click or TOUCH_DOWN_MS; only the main button presses (a right click elsewhere closes at once). The real
  code of both files over a fake document — the same sequence of events as Chrome gives.
- The stand 3.3.5 П7: «Только этот выход выбран в маршруте: устройства «a», устройства «b»» — the word once per kind.

The Chrome side (touch, a long press over a redraw, the right button, the focus back on «Перезапустить», Esc, an exit
deleted elsewhere, Enter in a field, the menu «⋯» of a subscription) — tests/test_335_hold.py (tools/k3_hold_check.mjs)."""
# k3-select: dirs=lib/kaskad/panel/static
import json
import re
import unittest

from tests import jsparts
from tests.test_panel_f0 import APP, HAS_NODE, dict_scope, read, run_node, window_with

UX = read("ux.js")

# a fake document: listeners by type (capture or not — the same here), activeElement, CustomEvent
DOC = """
const L = {};
const document = {
  activeElement: null,
  addEventListener: (k, fn) => { (L[k] = L[k] || []).push(fn); },
  removeEventListener: (k, fn) => { L[k] = (L[k] || []).filter((x) => x !== fn); },
  dispatchEvent: (ev) => { (L[ev.type] || []).slice().forEach((fn) => fn(ev)); return true; },
};
globalThis.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
const node = (name, kids) => ({ name, isConnected: true, kids: kids || [], contains(x) { return x === this || this.kids.some((k) => k === x || k.contains(x)); } });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fire = (type, target, o) => document.dispatchEvent(Object.assign({ type, target, button: 0, pointerType: "" }, o || {}));
// tap — как касание в Chrome: палец отпущен раньше, чем ушёл фокус
const tap = (target) => { fire("pointerdown", target, { pointerType: "touch" }); fire("pointerup", target, { pointerType: "touch" }); fire("mousedown", target); document.activeElement = target; fire("focusin", target); };
"""

CASES = """
(async () => {
  const r = {};
  const run = async (name, body) => {
    const yes = node("yes"), box = node("box", [yes]), b = node("B");
    const closed = [];
    const off = confirmLife(box, (had) => { closed.push(had); box.isConnected = false; });
    document.activeElement = yes;
    await body(b, box);
    r[name] = closed.length;
    off();
  };
  // касание корзины плитки ниже: фокус ушёл, пока «нажато» — не закрывает; закрывает click
  await run("touchFocus", async (b) => { tap(b); });
  await run("touchClick", async (b) => { tap(b); fire("mouseup", b); fire("click", b); });
  // касание без click (палец повёл — прокрутка): «нажато» снимается таймером, следующий уход фокуса закрывает
  await run("touchLate", async (b) => { fire("pointerdown", b, { pointerType: "touch" }); fire("pointerup", b, { pointerType: "touch" }); await sleep(TOUCH_DOWN_MS + 150); fire("focusin", b); });
  // мышь: нажата — уход фокуса не закрывает, отпущена — закрывает (как раньше)
  await run("mouseDown", async (b) => { fire("pointerdown", b, { pointerType: "mouse" }); fire("focusin", b); });
  await run("mouseUp", async (b) => { fire("pointerdown", b, { pointerType: "mouse" }); fire("pointerup", b, { pointerType: "mouse" }); fire("focusin", b); });
  // правая кнопка (П3) — не «нажато»: фокус на чужой кнопке закрывает сразу
  await run("rightFocus", async (b) => { fire("pointerdown", b, { pointerType: "mouse", button: 2 }); fire("focusin", b); });
  // ctrl+щелчок на Mac — контекстное меню, pointerup может не прийти (REV-336 П-3): не «нажато», уход фокуса закрывает
  await run("ctrlFocus", async (b) => { fire("pointerdown", b, { pointerType: "mouse", ctrlKey: true }); fire("focusin", b); });
  // касание внутри (на «Удалить») — «нажато» снято щелчком: следующий уход фокуса закрывает
  await run("insideThenAway", async (b, box) => { tap(box.kids[0]); fire("click", box.kids[0]); fire("focusin", b); });
  console.log(JSON.stringify(r));
})();
"""

WANT = {"touchFocus": 0, "touchClick": 1, "touchLate": 1, "mouseDown": 0, "mouseUp": 1, "rightFocus": 1, "ctrlFocus": 1, "insideThenAway": 1}


@unittest.skipUnless(HAS_NODE, "no node")
class ConfirmLifeTouchTest(unittest.TestCase):
    def test_constants(self):
        self.assertIn("const TOUCH_DOWN_MS = 700;", APP)
        # REV-336 П-3: нажатие, которое держит блок, — только основная кнопка без ctrl (ctrl+щелчок на Mac — меню)
        # REV-336-2 П-3: ctrl+щелчок — меню только на Mac; на Windows и Linux это обычный щелчок, он держит блок
        self.assertIn('document.addEventListener("pointerdown", (ev) => { if (ev.button !== 0 || ctrlMenu(ev)) return;', APP)
        self.assertIn("const ctrlMenu = (ev) => MAC && ev.ctrlKey;", APP)
        self.assertIn("!(MAC && ev.ctrlKey)", UX)
        self.assertIn("var CONFIRM_IDLE_MS = 30000, TOUCH_DOWN_MS = 700;", UX)

    def test_panel(self):
        fn = jsparts.function(APP, "confirmLife")
        for mac, want in ((True, WANT), (False, dict(WANT, ctrlFocus=0))):
            out = run_node(DOC + "const CONFIRM_IDLE_MS = 30000, TOUCH_DOWN_MS = 700;\nconst MAC = %s;\nconst ctrlMenu = (ev) => MAC && ev.ctrlKey;\n" % ("true" if mac else "false") + fn
                           + "\nconst confirmLife1 = confirmLife;\n" + CASES.replace("confirmLife(box,", "confirmLife1([box],"))
            self.assertEqual(out, want, "MAC=%s" % mac)

    def test_components(self):
        """ux.js — то же условие «нажато» (REV-335-7 П4: без него ProtocolCard закрывалась на нажатии)."""
        fn = jsparts.function(UX, "confirmLife")
        for mac, want in ((True, WANT), (False, dict(WANT, ctrlFocus=0))):
            out = run_node(DOC + "var CONFIRM_IDLE_MS = 30000, TOUCH_DOWN_MS = 700;\nvar MAC = %s;\n" % ("true" if mac else "false") + fn + CASES)
            self.assertEqual(out, want, "MAC=%s" % mac)


@unittest.skipUnless(HAS_NODE, "no node")
class PinnedNamesTest(unittest.TestCase):
    """exitPinned (app.js) с настоящими словарями: «устройств «a», «b»», а не «устройства «a», устройства «b»»."""

    def run_lang(self, lang):
        files = ["i18n-ru.js"] + (["i18n-en.js"] if lang == "en" else [])
        dev = lambda name, via: {"name": name, "route": {"via": via}}
        people = {"users": [dev("Аня", "exit:A"), dev("Боря", "exit:A"), dev("Вера", "exit:B")],
                  "devices": [dev("a", "exit:A"), dev("b", "exit:A"), dev("c", "exit:A"), dev("d", "exit:B"), dev("e", "direct")]}
        return run_node(window_with(lang, files) + dict_scope() + """
const R = { people: { loaded: true, data: %s }, exits: { data: { defaultRoute: { via: "exit:B" } } }, gw: { data: null } };
%s
console.log(JSON.stringify({ A: exitPinned("A"), B: exitPinned("B"), C: exitPinned("C"), AB: exitPinned(["A", "B"]) }));
""" % (json.dumps(people, ensure_ascii=False), jsparts.function(APP, "exitPinned")))

    def test_ru(self):
        out = self.run_lang("ru")
        self.assertEqual(out["A"], ["пользователей «Аня», «Боря»", "устройств «a», «b», «c»"])
        self.assertEqual(out["B"], ["по умолчанию — для всех, у кого маршрут не задан", "пользователя «Вера»", "устройства «d»"])
        self.assertEqual(out["C"], [])
        # REV-336 З-1: подписка из двух ключей — люди и устройства всех ключей одним перечислением, слово один раз
        self.assertEqual(out["AB"], ["по умолчанию — для всех, у кого маршрут не задан", "пользователей «Аня», «Боря», «Вера»",
                                     "устройств «a», «b», «c», «d»"])

    def test_en(self):
        out = self.run_lang("en")
        self.assertEqual(out["A"], ["users “Аня”, “Боря”", "devices “a”, “b”, “c”"])
        self.assertEqual(out["B"][1:], ["user “Вера”", "device “d”"])

    def test_forms(self):
        """REV-336 З-2: форма — «одно имя / несколько», не по числу: две формы в обоих языках."""
        for name in ("i18n-ru.js", "i18n-en.js"):
            src = read(name)
            for key in ("ex.pinUser", "ex.pinDevice"):
                v = re.search(r'"%s": "([^"]*)"' % re.escape(key), src).group(1)
                self.assertEqual(len(v.split("|")), 2, (name, key))
                self.assertTrue(all("{list}" in f for f in v.split("|")), (name, key))

    def test_one_name_or_many_not_the_number(self):
        """REV-336 З-2: 21 человек — «людей …» (Intl.PluralRules дал бы «one» → «человека»); одинаковые имена у разных
        людей не схлопываются (REV-336-nits №4)."""
        dev = lambda name: {"name": name, "route": {"via": "exit:A"}}
        people = {"users": [dev("u%d" % i) for i in range(21)], "devices": [dev("Телефон"), dev("Телефон")]}
        out = run_node(window_with("ru", ["i18n-ru.js"]) + dict_scope() + """
const R = { people: { loaded: true, data: %s }, exits: { data: null }, gw: { data: null } };
%s
console.log(JSON.stringify(exitPinned("A")));
""" % (json.dumps(people, ensure_ascii=False), jsparts.function(APP, "exitPinned")))
        self.assertTrue(out[0].startswith("пользователей «u0», "), out[0])
        self.assertEqual(out[1], "устройств «Телефон», «Телефон»")


ROW_FOCUS = """
const HOLD_TYPE = "input", HOLD_ROW = "[data-hold-row]", FOCUSABLE = "button, [href], input";
const SENT = new WeakSet();
globalThis.CSS = { escape: (x) => x };
let lost = 0;
const focusLost = () => { lost++; document.activeElement = null; };
const fieldKey = (x) => x.id || "";
const document = { activeElement: null, body: { name: "body" } };
const mk = (tag, label, kids, key) => {
  const n = { tagName: tag, label, kids: kids || [], key: key || null, isConnected: true, parent: null,
    getAttribute(a) { return a === "aria-label" ? null : a === "data-hold-row" ? this.key : null; },
    get textContent() { return this.label || ""; },
    attrs: {}, hasAttribute(a) { return a in this.attrs; }, setAttribute(a, v) { this.attrs[a] = String(v); },
    matches(sel) { return sel === "input" ? this.tagName === "INPUT" : sel === HOLD_ROW ? !!this.key : sel === FOCUSABLE ? this.tagName === "BUTTON" : false; },
    closest(sel) { let x = this; while (x) { if (x.matches(sel)) return x; x = x.parent; } return null; },
    contains(x) { while (x) { if (x === this) return true; x = x.parent; } return false; },
    all() { return this.kids.reduce((a, k) => a.concat([k], k.all()), []); },
    querySelectorAll(sel) { return this.all().filter((k) => (sel === "BUTTON" ? k.tagName === "BUTTON" : k.matches(sel))); },
    querySelector(sel) { const m = /data-hold-row="([^"]+)"/.exec(sel); return this.all().find((k) => k.key === (m && m[1])) || null; },
    focus() { document.activeElement = this; } };
  n.kids.forEach((k) => { k.parent = n; });
  return n;
};
const tile = (label) => { const b = mk("BUTTON", label); return { b, row: mk("DIV", "", [mk("BUTTON", "Изменить"), b], "exit:A") }; };
const r = {};
// подпись сменилась: «Выключить» → «Включить» — фокус на первую кнопку того же ряда, не на заголовок раздела
{ const a = tile("Выключить"), node = mk("DIV", "", [a.row]); document.activeElement = a.b;
  const back = rowFocus(node); a.b.isConnected = false; node.kids = []; document.activeElement = null;
  const n2 = tile("Включить"); node.kids = [n2.row]; n2.row.parent = node; back();
  r.renamed = document.activeElement === n2.row.kids[0]; r.lost1 = lost; }
// подпись та же — та же кнопка
{ const a = tile("Выключить"), node = mk("DIV", "", [a.row]); document.activeElement = a.b;
  const back = rowFocus(node); a.b.isConnected = false; document.activeElement = null;
  const n2 = tile("Выключить"); node.kids = [n2.row]; n2.row.parent = node; back();
  r.same = document.activeElement === n2.b; }
// ряда больше нет — заголовок раздела (focusLost)
{ const a = tile("Выключить"), node = mk("DIV", "", [a.row]); document.activeElement = a.b;
  const back = rowFocus(node); a.b.isConnected = false; node.kids = []; document.activeElement = null; back();
  r.gone = lost === 1; }
// в фокусе сам ряд (плитка или строка из поиска, tabindex -1; REV-336-int З-2) — новый ряд с тем же ключом, и он
// получает tabindex -1; без своей ветки фокус ушёл бы на заголовок раздела (кнопки с подписью ряда в нём нет)
{ const lost0 = lost, row = mk("DIV", "", [mk("BUTTON", "Изменить")], "exit:A"), node = mk("DIV", "", [row]);
  row.attrs = { tabindex: "-1" }; document.activeElement = row;
  const back = rowFocus(node); row.isConnected = false; node.kids = []; document.activeElement = null;
  const nr = mk("DIV", "", [mk("BUTTON", "Изменить")], "exit:A"); nr.attrs = {}; node.kids = [nr]; nr.parent = node; back();
  r.self = document.activeElement === nr && nr.attrs.tabindex === "-1" && lost === lost0; }
// сам ряд в фокусе, а нового ряда с тем же ключом нет — focusLost
{ const lost0 = lost, row = mk("DIV", "", [], "exit:A"), node = mk("DIV", "", [row]);
  row.attrs = { tabindex: "-1" }; document.activeElement = row;
  const back = rowFocus(node); row.isConnected = false; node.kids = [mk("DIV", "", [], "exit:B")]; node.kids[0].parent = node;
  document.activeElement = null; back();
  r.selfGone = lost === lost0 + 1; }
console.log(JSON.stringify(r));
"""


@unittest.skipUnless(HAS_NODE, "no node")
class RowFocusTest(unittest.TestCase):
    """REV-336 П-2: ряд на месте, но подпись кнопки сменилась — фокус в ряд (первая кнопка), а не на заголовок раздела.
    REV-336-int З-2: в фокусе сам ряд (плитка из поиска) — фокус на новый ряд с тем же ключом, нет его — focusLost."""

    def test_row_focus(self):
        out = run_node(ROW_FOCUS.replace("const r = {};", jsparts.function(APP, "rowFocus") + "\nconst r = {};"))
        self.assertEqual(out, {"renamed": True, "lost1": 0, "same": True, "gone": True, "self": True, "selfGone": True})


FW_FIND = r"""
const P = { forwards: { q: "Вен" } };
const r = {};
const go = (n) => { P.forwards.q = "Вен"; fwFindClear(n); return P.forwards.q; };
r.fw = go({ page: "forwards", focus: "fw-tcp-443" });
r.udp = go({ page: "forwards", focus: "fw-udp-53" });
r.none = go({ page: "forwards", focus: null });
r.noNav = go(undefined);
r.other = go({ page: "forwards", focus: "forwards.add" });
console.log(JSON.stringify(r));
"""


@unittest.skipUnless(HAS_NODE, "no node")
class ForwardFindClearTest(unittest.TestCase):
    """REV-336-int З-1: проброс из поиска (?focus=fw-<proto>-<порт>) сбрасывает поле «Пробросов» — набранное раньше не
    прячет выбранную строку (как «Люди»: P.people.q при nav.focus); прочий вход раздела поле не трогает. Оба вида
    раздела (компьютер и телефон) зовут сброс до чтения поля. Живьём — k3_find_check («поле заполнено до выбора»)."""

    def test_clear_on_forward_focus_only(self):
        out = run_node(FW_FIND.replace("const r = {};", jsparts.function(APP, "fwFindClear") + "\nconst r = {};"))
        self.assertEqual(out, {"fw": "", "udp": "", "none": "Вен", "noNav": "Вен", "other": "Вен"})

    def test_both_views_clear_before_the_field(self):
        i = APP.index("  PAGES.forwards = {")
        desk = APP[APP.index("    build(root, n) {", i):]
        phone = APP[APP.index("  PAGES.forwards.phone = function (root, n) {"):]
        for part in (desk, phone):
            self.assertLess(part.index("fwFindClear(n);"), part.index("value: P.forwards.q"))


def css_files():
    import os
    from tests.test_panel_f0 import STATIC
    out = []
    for d, _, names in os.walk(STATIC):
        out += [os.path.join(d, n) for n in sorted(names) if n.endswith(".css")]
    return out


def text_of(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def top_commas(v):
    depth, n = 0, 0
    for ch in v:
        depth += ch == "("
        depth -= ch == ")"
        n += ch == "," and depth == 0
    return n


class ShadowListTest(unittest.TestCase):
    """REV-336 С-1: «box-shadow: 0 0 0 3px var(--accent), var(--card-sh)» — где токен тени бывает none (тёмная тема,
    карточки border/flat), список «…, none» недопустим и правило отбрасывается целиком: подсветки не видно. Ни одна тень
    из нескольких не ссылается на токен, который где-то равен none (с учётом цепочек var(--a) → var(--b) → none)."""

    def test_no_list_with_a_shadow_that_may_be_none(self):
        defs = {}
        files = css_files()
        self.assertTrue(files)
        for f in files:
            for name, val in re.findall(r"(--[\w-]+)\s*:\s*([^;}]+)", text_of(f)):
                defs.setdefault(name, []).append(val.strip())
        none = {k for k, vs in defs.items() if "none" in vs}
        while True:      # --a: var(--b) или var(--b, запас) — «none», если --b «none» хоть где-то
            more = {k for k, vs in defs.items() if k not in none
                    and any(re.fullmatch(r"var\((--[\w-]+)(,.*)?\)", v) and re.match(r"var\((--[\w-]+)", v).group(1) in none
                            for v in vs)}
            if not more:
                break
            none |= more
        self.assertIn("--card-sh", none)                   # проверка что-то доказывает
        bad = []
        for f in files:
            for val in re.findall(r"box-shadow\s*:\s*([^;}]+)", text_of(f)):
                if top_commas(val) and set(re.findall(r"var\((--[\w-]+)", val)) & none:
                    bad.append("%s: %s" % (f.rsplit("/", 1)[-1], val.strip()))
        self.assertEqual(bad, [])

    def test_flash_is_one_ring(self):
        css = read("styles.css") + read("graph.css")
        for sel in ("[data-find].is-flash", ".gx-card.is-flash", ".kg-lr.is-flash"):
            m = re.search(re.escape(sel) + r" \{ box-shadow: 0 0 0 3px var\(--(kg-)?accent\); \}", css)
            self.assertTrue(m, sel)
        # «Люди» (UI-B 23.10): карточек .kk-user больше нет — строка таблицы подсвечена одной внутренней рамкой,
        # строка телефона — фоном (без тени: одна тень на узел, REV-336 С-1)
        self.assertIn(".gx-xt__r.is-flash { box-shadow: inset 0 0 0 2px var(--accent); }", read("xt.css"))
        self.assertIn(".kk-prow.is-flash { background: var(--accent-soft); }", read("people.css"))

    def test_old_people_cards_gone(self):
        # UI-4 №8 / UI-B: прежние карточки людей и устройств удалены вместе со стилями — ни правил, ни классов в коде
        old = re.compile(r"\.kk-users?(?![\w-])|\.kk-user__|\.kk-devs?(?![\w-])|\.kk-dev__|\.kk-nodev(?![\w-])")
        bad = [f.rsplit("/", 1)[-1] for f in css_files() if old.search(text_of(f))]
        self.assertEqual(bad, [])
        self.assertNotRegex(APP, r"[\"' ]kk-(users?|devs?|nodev)(__\w+)?[\"' ]")


if __name__ == "__main__":
    unittest.main()
