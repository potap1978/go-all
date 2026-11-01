"""UI-3 круг 2 (REV-ui3): поведение в node вместо сверки строк исходника.

З1 — раскрытая карточка (KaskadUX.foldGrid): шапка остаётся на своём месте в ряду, тело — полосой сразу после последней
карточки её ряда; порядок DOM (он же порядок Tab) — шапки ряда, затем тела; смена числа колонок переставляет только полосы;
полосу, подменённую удержанием ряда, раскладка берёт. П1/П4 — числа в шапке раскрытой спрятаны, подпись шапки для диктора.
С1 — движок AmneziaWG без карточки AWG: на компьютере «Движок: …» и «Импорт AWG» у заголовка первого раздела (чистый узел —
тоже), на телефоне строка движка; поиск ведёт к ним. З2 — предупреждения движка красной строкой в строке AWG на телефоне.
С2 — телефон: поиск «модуль ядра» → деталь AWG по адресу #inbounds?focus=<awg…> (dropFocus его не снимает).
З3 — «Можно установить»: описание без повтора имени, нажатие на строку — её кнопка.
Т3 (вариант Б r3): «у заголовка первого раздела» — в шапке схемы «Устройства → Сервер → Выходы»; телефон — тот же вид
(строки раскрываются на месте, детали во весь экран нет): строка движка — после списка, поиск раскрывает строку."""
import shutil
import unittest

from tests import jsparts
from tests.test_333d_ux import prelude, read
from tests.test_332f_give import run_node

APP = read("app.js")

# insertBefore и previousElementSibling — в учебном DOM их нет
DOM_PLUS = r"""
Node_.prototype.insertBefore = function (n, ref) { if (!ref) return this.appendChild(n); if (n.parentNode) n.parentNode.removeChild(n);
  const i = this.children.indexOf(ref); n.parentNode = this; this.children.splice(i, 0, n); return n; };
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class FoldGridTest(unittest.TestCase):
    def run_grid(self, body):
        return run_node(prelude() + DOM_PLUS + r"""
const KINDS = ["wg", "awg2", "l2tp", "socks5", "vless", "hy2"];
const opts = (o) => Object.assign({ lang: "ru", collapsible: true, onToggle() {}, onPort() {}, onLog() {}, onRestart: () => Promise.resolve({}), onDelete: () => Promise.resolve({}), onOption() {} }, o || {});
const card = (k) => { const c = X.protocolCard({ kind: k, title: k.toUpperCase(), engine: "e", desc: "d", enabled: true, devices: 2, sessions: 0, ports: ["443/tcp"], running: true }, opts());
  c.setAttribute("data-hold-row", "ib:" + k); return c; };
let COLS = 3;
const cards = KINDS.map(card);
const grid = X.foldGrid(cards, { cols: () => COLS });
document.body.appendChild(grid);
const head = (c) => byCls(c, "ux-pc__tog")[0];
const seq = () => grid.children.filter((x) => !x.hidden).map((x) => x.classList.contains("ux-pc--drawer") ? "D:" + x.getAttribute("data-drawer") : x.getAttribute("data-kind"));
const all = () => grid.children.map((x) => x.classList.contains("ux-pc--drawer") ? "d:" + x.getAttribute("data-drawer") : x.getAttribute("data-kind"));
const byKind = (k) => cards[KINDS.indexOf(k)];
""" + body)

    def test_open_stays_in_place_body_after_row(self):
        out = self.run_grid(r"""
const r = { start: seq() };
head(byKind("vless")).click();
r.vless = seq();
r.slot = grid.children.filter((x) => x.classList.contains("ux-pc--fold")).indexOf(byKind("vless"));   // место VLESS среди карточек
r.drawer = byKind("vless").uxDrawer.hidden === false && byKind("vless").uxDrawer.getAttribute("data-hold-row");
head(byKind("socks5")).click();
r.two = seq();
COLS = 2; grid.uxLayout();
r.cols2 = seq();
head(byKind("vless")).click(); head(byKind("socks5")).click();
r.closed = seq();
r.allClosed = all();
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["start"], ["wg", "awg2", "l2tp", "socks5", "vless", "hy2"])
        # на месте VLESS — VLESS; соседи ряда на местах; тело — сразу после ряда (так же идёт и Tab)
        self.assertEqual(out["vless"], ["wg", "awg2", "l2tp", "socks5", "vless", "hy2", "D:vless"])
        self.assertEqual(out["slot"], 4)
        self.assertEqual(out["drawer"], "ib:vless:open")
        self.assertEqual(out["two"], ["wg", "awg2", "l2tp", "socks5", "vless", "hy2", "D:socks5", "D:vless"])
        # 2 колонки: SOCKS5 — в ряду 2 (l2tp, socks5), VLESS — в ряду 3 (vless, hy2)
        self.assertEqual(out["cols2"], ["wg", "awg2", "l2tp", "socks5", "D:socks5", "vless", "hy2", "D:vless"])
        self.assertEqual(out["closed"], ["wg", "awg2", "l2tp", "socks5", "vless", "hy2"])
        # закрытые полосы скрыты и стоят после своего ряда (2 колонки: wg, awg2 | d:wg, d:awg2 | l2tp …)
        self.assertEqual(out["allClosed"][:5], ["wg", "awg2", "d:wg", "d:awg2", "l2tp"])

    def test_first_row_and_held_drawer(self):
        out = self.run_grid(r"""
const r = {};
head(byKind("wg")).click();
r.first = seq();
// удержание ряда (открытое «⋯» в теле): новая сетка получает прежнюю полосу на место новой
const old = byKind("wg").uxDrawer, held = document.createElement("div");
held.className = "ux-pc ux-pc--drawer is-open"; held.setAttribute("data-drawer", "wg"); held.setAttribute("data-hold-row", "ib:wg:open");
grid.replaceChild(held, old);
COLS = 2; grid.uxLayout();
r.held = byKind("wg").uxDrawer === held;
r.seq = all();
// фокус в полосе (подтверждение на месте): раскладка её не переносит — двигает соседей
const inner = document.createElement("button"); held.appendChild(inner); inner.focus();
const moved = []; const ins = grid.insertBefore.bind(grid), app = grid.appendChild.bind(grid);
grid.insertBefore = (x, ref) => { moved.push(x); return ins(x, ref); }; grid.appendChild = (x) => { moved.push(x); return app(x); };
COLS = 3; grid.uxLayout();
r.focusMoved = moved.includes(held);
r.seq3 = all();
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["first"], ["wg", "awg2", "l2tp", "D:wg", "socks5", "vless", "hy2"])
        self.assertTrue(out["held"])
        self.assertEqual(out["seq"][:3], ["wg", "awg2", "d:wg"])
        self.assertEqual(out["seq"].count("d:wg"), 1)
        self.assertFalse(out["focusMoved"])
        self.assertEqual(out["seq3"][:4], ["wg", "awg2", "l2tp", "d:wg"])

    def test_old_grid_does_not_take_back_held_drawer(self):
        out = self.run_grid(r"""
head(byKind("wg")).click();
const held = byKind("wg").uxDrawer;
// перерисовка с удержанием: новая сетка, в ней прежняя полоса вместо новой; прежняя сетка уходит со страницы
const cards2 = KINDS.map(card), byKind2 = (k) => cards2[KINDS.indexOf(k)];
head(byKind2("wg")).click();
const grid2 = X.foldGrid(cards2, { cols: () => COLS });
grid2.replaceChild(held, byKind2("wg").uxDrawer);
document.body.removeChild(grid); document.body.appendChild(grid2);
grid.uxLayout();                                       // наблюдатель прежней сетки
grid2.uxLayout();
console.log(JSON.stringify({ inNew: held.parentNode === grid2, own: byKind2("wg").uxDrawer === held, count: grid2.children.filter((x) => x.getAttribute("data-drawer") === "wg").length }));
""")
        self.assertEqual(out, {"inNew": True, "own": True, "count": 1})

    def test_aria_label_and_sum(self):
        out = self.run_grid(r"""
console.log(JSON.stringify({ aria: head(byKind("vless")).getAttribute("aria-label") }));
""")
        self.assertEqual(out["aria"], "VLESS, работает, 443/tcp, 2 устр.")
        css = read("ux.css")
        self.assertIn(".ux-pc--fold.is-open .ux-pc__sum { display: none; }", css)
        self.assertNotIn("dense", css.split(".ux-pgrid--fold {")[1].split("}")[0])


APP_STUBS = r"""
const LOG = [];
Node_.prototype.append = function (...k) { k.forEach((c) => { if (c) this.appendChild(c); }); };
const t = (k, p) => k + (p ? JSON.stringify(p) : ""), tn = (k, n) => k + ":" + n;
function el(tag, attrs, ...kids) { const n = document.createElement(tag); Object.entries(attrs || {}).forEach(([k, v]) => { if (v === null || v === undefined || v === false) return;
  if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v); });
  kids.flat().forEach((c) => { if (c) n.appendChild(c); }); return n; }
const U = { btn: (label, kind, fn, o) => { const b = el("button", { "data-tip": (o && o.tip) || "" }); b.textContent = label; b.addEventListener("click", fn); return b; },
  skel: () => el("i"), state: () => el("i"), empty: () => el("i") };
let R = { inbounds: { loaded: true, data: [] }, awg: { loaded: true, data: { active: "program", warnings: [], mix: [], hasAwg: false, text: "" } } };
const row = (kind, installed) => ({ kind, title: kind.toUpperCase(), installed, available: true, enabled: true, devices: 1, options: {} });
const ibUxCard = (r) => el("article", { class: "ux-pc ux-pc--fold", "data-kind": r.kind }), ibRow = (r) => el("article", { class: "kk-irow", "data-kind": r.kind }), ibCard = ibRow;
window.KaskadUX = { foldGrid: (cs) => el("div", { class: "ux-pgrid--fold" }, cs) };
// схема и список «Протоколов» (Т3) — tests/test_it1_t3_proto.py; здесь — куда попадают кнопки движка
const ibFlow = (inst, tools) => el("section", { class: "kb2-card" }, el("div", { class: "kb2-h" }, tools)), ibList = (inst) => el("div", { class: "kb2-pl" }, inst.map(ibUxCard));
const fmtInt = (n) => String(n), txt = (s) => document.createTextNode(s);
let ON_PHONE = false;
let ibWantAwg = false; const ibSetOpen = (k) => LOG.push("open:" + k), loadAll = () => {}, isPhone = () => ON_PHONE;
const awgImport = () => LOG.push("import"), awgEngine = () => el("div", { class: "kk-awgeng" });
const ibInstOrder = (rows) => rows.filter((r) => r.installed);                     // порядок протоколов (круг 1 П2) — tests/test_it1_logic_panel.py
const openBottom = (o) => LOG.push("bottom:" + o.title), openDialog = (o) => LOG.push("dialog:" + o.title);
const btnsOf = (n) => n ? n.all((x) => x.tagName === "BUTTON").map((b) => b.textContent) : [];
const view = () => ({ st: "ok", rows: R.inbounds.data.map((r) => ({ kind: r.kind, installed: r.installed, available: r.available })) });
"""


def app_parts(*names):
    out = []
    for n in names:
        try:
            out.append(jsparts.function(APP, n))
        except KeyError:
            out.append(jsparts.const(APP, n))
    return "\n".join(out)


@unittest.skipUnless(shutil.which("node"), "нет node")
class AwgWithoutCardTest(unittest.TestCase):
    """С1: движок, экспорт и импорт AmneziaWG — и без установленного протокола AWG."""

    def run_app(self, body):
        return run_node(prelude() + APP_STUBS + app_parts("isAwgKind", "awgFocusKind", "awgWarnOf", "ibAwgTools", "openAwgEngine", "ibAwgPhone", "ibGridView") + body)

    def test_clean_node_has_import(self):
        out = self.run_app(r"""
R.inbounds.data = [row("wg", false), row("awg2", false), row("vless", false)];
const out = ibGridView(view());
const bar = out.children[0];
const r = { first: out.className + ">" + bar.className, btns: btnsOf(bar), find: bar.all((x) => x.getAttribute && x.getAttribute("data-find") === "inbounds.awg").length };
btnsOf(bar).length && bar.all((x) => x.tagName === "BUTTON")[1].click();
bar.all((x) => x.tagName === "BUTTON")[0].click();
r.log = LOG;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["first"], "kb2>kb2-card")                      # схема — первой, кнопки движка — в её шапке
        self.assertEqual(out["btns"], ['awg.engineIs{"e":"awg.program"}', "awg.importAwg"])
        self.assertEqual(out["find"], 1)                                    # поиск «движок / импорт» ведёт сюда
        self.assertEqual(out["log"], ["import", "dialog:awg.title"])

    def test_awg_in_exits_only_and_with_card(self):
        out = self.run_app(r"""
R.awg.data = { active: "kernel", warnings: ["модуль не собран"], mix: [], hasAwg: true };
R.inbounds.data = [row("wg", true), row("awg2", false)];
const a = ibGridView(view()).children;
const r = { exitsOnly: btnsOf(a[0]), tip: a[0].all((x) => x.tagName === "BUTTON")[0].getAttribute("data-tip"), list: a[1].children.map((x) => x.getAttribute("data-kind")) };
R.inbounds.data = [row("wg", true), row("awg2", true)];
const b = ibGridView(view()).children;
r.withCard = btnsOf(b[0]).length + ":" + b[1].children.map((x) => x.getAttribute("data-kind")).join(",");
R.awg = { loaded: true, data: null };
R.inbounds.data = [row("wg", true)];
r.noData = btnsOf(ibGridView(view()).children[0]).length;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["exitsOnly"], ['awg.engineIs{"e":"awg.kernel"}', "awg.importAwg"])
        self.assertEqual(out["tip"], "модуль не собран")
        self.assertEqual(out["list"], ["wg"])
        self.assertEqual(out["withCard"], "0:wg,awg2")                      # движок — в строке AWG
        self.assertEqual(out["noData"], 0)

    def test_search_record_needs_only_engine_data(self):
        need = APP[APP.index("const FIND_NEED = {"):]
        need = need[:need.index("};")]
        self.assertIn("awg: () => !!(R.awg.loaded && R.awg.data),", need)
        for lang in ("ru", "en"):
            syn = read("i18n-%s.js" % lang)
            syn = syn[syn.index('"find.awgSyn"'):]
            self.assertIn("import", syn[:200])


PHONE_STUBS = r"""
window.Kit = { mrow: (o) => Object.assign(el("div", { class: "gx-mrow", "data-find": o.find || null }), { o }), mlist: (rows) => el("div", { class: "gx-mlist" }, rows) };
const ibDesc = (r) => r.kind.toUpperCase() + ": описание";
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class PhoneTest(unittest.TestCase):
    """Т3: телефон — тот же вид, что на компьютере (ibGridView): схема, строки, строка движка AWG после списка."""

    def run_phone(self, body):
        return run_node(prelude() + APP_STUBS + PHONE_STUBS + app_parts(
            "isAwgKind", "awgFocusKind", "awgWarnOf", "ibAwgTools", "openAwgEngine", "ibAwgPhone", "ibGridView", "ibRowDesc", "ibTapRow") + body)

    def test_engine_row_and_red_awg_line(self):
        out = self.run_phone(r"""
ON_PHONE = true;
const kids = (v) => v.children.map((x) => x.className.split(" ")[0]);
R.inbounds.data = [row("wg", false), row("awg2", false)];
const clean = ibGridView(view());
const eng = byCls(clean, "gx-mrow")[0].o;
const r = { clean: kids(clean), flowBtns: btnsOf(clean.children[0]).length, engSub: eng.sub, engTone: eng.subTone, find: eng.find };
eng.onClick();
R.awg.data = { active: "kernel", warnings: ["модуль не собран"], mix: ["смешано"], hasAwg: true };
R.inbounds.data = [row("wg", true), row("awg2", false)];
r.exitsOnly = kids(ibGridView(view()));
r.warn = byCls(ibGridView(view()), "gx-mrow")[0].o;
R.inbounds.data = [row("wg", true), row("awg2", true)];
r.card = kids(ibGridView(view()));
// предупреждения движка — красной строкой под строкой AWG (ux.js, строка списка: o.alertLine)
const awg = X.protocolCard({ kind: "awg2", title: "AmneziaWG 2.0", engine: "", desc: "d", enabled: true, devices: 1, sessions: 0, ports: ["51821/udp"], running: true },
  { lang: "ru", row: true, alertLine: awgWarnOf(R.awg.data), onToggle() {}, onPort() {}, onLog() {}, onRestart: () => Promise.resolve({}), onDelete: () => Promise.resolve({}) });
const al = byCls(awg, "kb2-pl__al")[0];
r.awg = [al.className, al.textContent, al.getAttribute("title")];
r.log = LOG;
console.log(JSON.stringify(r));
""")
        # чистый узел: схема, строка движка (установленных нет — сразу после схемы), «Можно установить»
        self.assertEqual(out["clean"], ["kb2-card", "gx-mlist", "kk-psec", "kk-igrid"])
        self.assertEqual(out["flowBtns"], 0)                                # на телефоне кнопок в шапке схемы нет
        self.assertEqual((out["engSub"], out["engTone"], out["find"]), ("awg.program · awg.exportImport", "", "inbounds.awg"))
        self.assertEqual(out["log"], ["bottom:awg.title"])                  # окно движка — листом снизу
        # AWG только в выходах: строка движка — после установленных, предупреждение — красным
        self.assertEqual(out["exitsOnly"], ["kb2-card", "kb2-pl", "gx-mlist", "kk-psec", "kk-igrid"])
        self.assertEqual((out["warn"]["sub"], out["warn"]["subTone"]), ("модуль не собран", "danger"))
        # AWG установлен: строки движка нет, предупреждения движка — красной строкой AWG
        self.assertEqual(out["card"], ["kb2-card", "kb2-pl"])              # всё установлено — «Можно установить» нет
        self.assertEqual(out["awg"], ["kb2-pl__al ux-pc__alert", "модуль не собран+1", "модуль не собран\nсмешано"])

    def test_install_rows(self):
        out = self.run_phone(r"""
const rows = [Object.assign(row("pptp", false), { title: "PPTP" }), Object.assign(row("ocserv", false), { title: "OpenConnect" })];
console.log(JSON.stringify({ sub: rows.map(ibRowDesc) }));
""")
        # имя в начале описания не повторяется, если описание начинается с него
        self.assertEqual(out["sub"], ["Описание", "OCSERV: описание"])
        row = jsparts.function(APP, "ibRow")
        self.assertIn("ibRowDesc(r)", row)
        self.assertIn("return r.available ? ibTapRow(row) : row;", row)

    def test_tap_row_presses_its_button(self):
        out = run_node(prelude() + APP_STUBS + app_parts("ibTapRow") + r"""
const n = el("article", {}, el("span", { text: "x" }), U.btn("Установить", "tertiary", () => LOG.push("btn")));
ibTapRow(n);
fire(n.children[0], "click");                          // мимо кнопки — та же кнопка
fire(n.all((x) => x.tagName === "BUTTON")[0], "click"); // сама кнопка — один раз
console.log(JSON.stringify({ log: LOG, cls: n.className }));
""")
        self.assertEqual(out["log"], ["btn", "btn"])
        self.assertIn("kk-tap", out["cls"])


@unittest.skipUnless(shutil.which("node"), "нет node")
class PhoneSearchAddressTest(unittest.TestCase):
    """С2 (Т3): поиск на телефоне — тот же вид: «модуль ядра» раскрывает строку AWG (build: ibWantAwg), протокол из поиска
    и «Обзора» (#inbounds?focus=<протокол>) — свою строку; в память браузера — только известный протокол."""

    def run_addr(self, body):
        return run_node(prelude() + APP_STUBS + r"""
const PAGES = { inbounds: { build: (root, n) => LOG.push("build:" + (n ? n.focus : "")) } };
const PROTO_CANON = ["wg", "awg2", "awg3", "vless", "hy2", "socks5", "l2tp", "ikev2", "awg1", "pptp", "sstp", "openvpn", "ocserv"];
const ibPicker = () => {};
""" + jsparts.assign(APP, "PAGES.inbounds.phoneHead") + "\n" + jsparts.assign(APP, "PAGES.inbounds.phone") + body)

    def test_addresses(self):
        out = self.run_addr(r"""
const go = (f) => { LOG.length = 0; PAGES.inbounds.phone(el("div"), { focus: f }); return LOG.slice(); };
const r = { awg: go("inbounds.awg"), vless: go("vless"), proto: go("constructor"), junk: go("x y"), none: go(undefined) };
LOG.length = 0; PAGES.inbounds.phone(el("div"), null); r.nil = LOG.slice();
const h = PAGES.inbounds.phoneHead();
r.head = [h.act.label, h.act.icon, h.act.on === ibPicker];
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["awg"], ["build:inbounds.awg"])               # строку AWG раскрывает build (ibWantAwg)
        self.assertEqual(out["vless"], ["open:vless", "build:vless"])
        for k in ("proto", "junk", "none"):
            self.assertEqual(len(out[k]), 1, k)                              # чужое и пустое — без записи в память
        self.assertEqual(out["nil"], ["build:"])
        self.assertEqual(out["head"], ["m.proto", "plus", True])

    def test_find_on_phone(self):
        # подсветка объекта из поиска на телефоне — как на компьютере (buildPage: view !== "phone" || p.findPhone)
        self.assertIn("PAGES.inbounds.findPhone = true;", APP)
        self.assertIn('(view !== "phone" || p.findPhone) && p.findObj', APP)
        self.assertIn("findObj: (kind) => $('section[data-page=\"inbounds\"] [data-kind=\"' + kind + '\"]')", APP)
        build = APP[APP.index("PAGES.inbounds = {"):]
        self.assertIn('if (n && n.focus === "inbounds.awg") ibWantAwg = true;', build[:1500])


if __name__ == "__main__":
    unittest.main()
