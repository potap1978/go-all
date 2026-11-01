"""Живая «Сводка» и «Здоровье сервера» (часть static/pages/ovb.js, круг 1 Т1, вариант Б) — отрисовка в node на
FakeNode-DOM из tests/test_panel_rf_js с настоящими U, el и blk панели и настоящей связкой ядра OVB_CTX (выходы —
exAbRows + exitWord). Сюда переехали по смыслу проверки старого «Обзора» (плитки ovTiles, светофор ovHost/ovMhost),
снятого как недостижимый (REV-it1 №4): «все выходы выключены» — не «выходов нет», элементы Сводки ведут в свой
раздел, неизвестное — «—», а не 0; светофор здоровья — состояние словами, «с …» только у сказанного уровня, пометка
«без истории», находка с советом. И вердикт Сводки (REV-it1 №3) — на экране, а не только в verdictOf."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts
from tests.test_panel_rf_js import DOM, app_scope, read

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OVB = os.path.join(ROOT, "lib", "kaskad", "panel", "static", "pages", "ovb.js")


def scope():
    js = read("app.js")
    with open(OVB, encoding="utf-8") as f:
        ovb = f.read()
    # ядро: связка OVB_CTX и слово состояния выхода — настоящие; остальное ядра — заглушки с записью вызовов
    core = "\n".join([jsparts.const(js, "EXIT_CHIP"), jsparts.const(js, "exitOwn"), jsparts.const(js, "exitGwOff"),
                      jsparts.const(js, "exAbRows"), jsparts.const(js, "exRuRows"), jsparts.function(js, "exitWord"), jsparts.const(js, "OVB_CTX"),
                      jsparts.function(js, "domMark")])   # значок внутренней группы — настоящий (3.3.7.13)
    return app_scope() + r"""
let GW = true, ISS = [], HV = null;
const GO = [], OPENED = [];
const gwEnabled = () => GW, ovIssuesOf = () => null, ovIssues = () => ISS, ovbHealth = () => HV;
const openExitDetail = (id) => OPENED.push(id), ovEvCard = () => el("section", { "data-ev": "" }), hhmm = String;
const S = { seenAt: 0 };
R.exits = { loaded: true, data: { rows: [] } }; R.overview = { data: { version: "3.3.7" } };
""" + core + r"""
// данные маршрутов части: synth / score / history / health / exits.hour
const API_DATA = { synth: null, score: null, hist: null, health: null, hour: null };
let DEF = null;
window.KaskadPanel = {
  el, t, tn, U, lang: "ru", blk: (c, b) => blk(c, b), goNav: (p, q) => GO.push([p, q || null]),
  API: { host: { synth: () => Promise.resolve(API_DATA.synth), score: () => Promise.resolve(API_DATA.score),
                 history: () => Promise.resolve(API_DATA.hist), health: () => Promise.resolve(API_DATA.health) },
         exits: { hour: () => Promise.resolve(API_DATA.hour) } },
  fmtNum: String, fmtNum1: String, fmtBytes: String, fmtInt: String, fmtPct: String, fmtDur: (s) => "D" + s, fmtTs: (v) => "T" + v,
  refresh: () => scheduleRefresh(), part: (n, d) => { DEF = d; return true; }, openSheet: () => ({ close() {} }),
  liveMark: () => null, isPhone: () => PHONE, loadPart: () => Promise.resolve(DEF), icon: () => el("i"), domMark,
};
""" + ovb + r"""
// сводка в коробку, как ovbMount; данные маршрутов приходят — блоки перерисовываются
async function summary() { const box = el("div"); DEF.summary(box, OVB_CTX); await tick(); await tick(); scheduleRefresh(); return box; }
async function health() { const box = el("div"); DEF.health(box, OVB_CTX); await tick(); await tick(); scheduleRefresh(); return box; }
const txtOf = (root, sel) => { const n = root.querySelector(sel); return n ? n.textContent : null; };
const exitRow = (id, name, o) => Object.assign({ id, name, role: "abroad", enabled: true, state: "alive", problem: false }, o || {});
"""


def run(script, data=None):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(DOM + scope() + "\nconst DATA = %s;\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify("
                "{crash: String(e && e.stack || e)})); });\n" % (json.dumps(data or {}, ensure_ascii=False), script))
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


@unittest.skipUnless(shutil.which("node"), "нет node: живую Сводку не отрисовать")
class SummaryLiveTest(unittest.TestCase):
    def test_exits_all_off_is_not_no_exits(self):
        """Было: плитка «Выходы» (ovTiles) — «0 · все выключены», а не «выходов нет» (test_3373_pre, test_ui2_layout).
        Живая Сводка: «Выходы · час» показывает выключенные выходы словом состояния («выключен»), «Выходов нет» — только
        когда их нет; строка — окно выхода, «Все выходы» — список; без данных часа — «—». Российские — после
        зарубежных с меткой РФ (владелец 28.10, решение 6; стенд it1 Н-2: «Дом» не был виден); только российские —
        тоже не «Выходов нет»."""
        out = run(r"""
const o = {};
R.exits.data.rows = [exitRow("e-1", "Токио", { enabled: false }), exitRow("e-2", "Осло", { enabled: false, state: "dead" }),
  exitRow("e-3", "Москва", { role: "ru" })];
let box = await summary();
const card = () => box.querySelector('section[aria-label="ovb.xnT"]');
o.allOff = card().querySelectorAll("button.kb-xn__r").map((b) => b.textContent);
o.allOffEmpty = txtOf(card(), ".kb-empty");
card().querySelectorAll("button.kb-xn__r")[1].click();
byText(card(), "button", "ovb.xnAll").click();
o.opened = OPENED; o.go = GO;
R.exits.data.rows = [exitRow("e-3", "Москва", { role: "ru" })]; scheduleRefresh();
o.none = [card().querySelectorAll("button.kb-xn__r").length, txtOf(card(), ".kb-empty"), !!byText(card(), "button", "ovb.xnAll")];
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["allOff"][:2], ["Токиоex.offForGw—", "Ослоex.offForGw—"])  # имя · «выключен» · «—», не 0
        self.assertTrue(out["allOff"][2].startswith("Москва"), out["allOff"])            # без правки: строки нет
        self.assertIsNone(out["allOffEmpty"])                                            # не «Выходов нет»
        self.assertEqual(out["opened"], ["e-2"])
        self.assertEqual(out["go"], [["exits", {"tab": "list"}]])
        self.assertEqual(out["none"], [1, None, True])                                   # только российский — он виден

    def test_russian_exit_keeps_a_row(self):
        """Решение 6: строк не больше шести; зарубежных семь — последняя строка отдана первому российскому, метка РФ
        только у него."""
        out = run(r"""
R.exits.data.rows = [1, 2, 3, 4, 5, 6, 7].map((i) => exitRow("e-" + i, "Выход " + i)).concat([exitRow("e-r", "Роутер", { role: "ru" })]);
const box = await summary();
const rows = box.querySelector('section[aria-label="ovb.xnT"]').querySelectorAll("button.kb-xn__r");
const mk = (r) => r.querySelector(".kb-xn__rf");
console.log(JSON.stringify({ names: rows.map((r) => r.textContent), rf: rows.map((r) => !!mk(r)),
  mark: [mk(rows[5]).getAttribute("role"), mk(rows[5]).getAttribute("aria-label"), mk(rows[5]).getAttribute("title"), mk(rows[5]).textContent] }));
""")
        want = ["Выход 1", "Выход 2", "Выход 3", "Выход 4", "Выход 5", "Роутер"]
        self.assertEqual(len(out["names"]), 6)
        for got, name in zip(out["names"], want):
            self.assertTrue(got.startswith(name), (got, name))
        self.assertEqual(out["rf"], [False] * 5 + [True])
        # 3.3.7.13: значок роутера вместо метки «РФ» — без текста, диктору «Внутренняя группа», подсказка своя
        self.assertEqual(out["mark"], ["img", "ex.domMark", "ovb.xnRfT", ""])

    def test_verdict_on_screen(self):
        """REV-it1 №3 на экране: выключенный шлюз — «Шлюз выключен» (точка не зелёная) и строка с «Включить шлюз»;
        проверки здоровья выключены — не «Работает нормально»; зелёное здоровье без строк — «Работает нормально»."""
        out = run(r"""
const o = {}, ON = [];
const hero = (box) => [txtOf(box, ".kb-hero__t"), box.querySelector(".kb-hero__k").querySelector("i").className];
ISS = [{ key: "gwoff", tone: "accent", title: "ov.gwOff", sub: "", n: 1, act: { label: "ov.gwEnable", on: () => ON.push("enable"), primary: true } }];
HV = { view: { level: "ok" } };
let box = await summary();
o.off = hero(box);
byText(box.querySelector(".kb-iss"), "button", "ov.gwEnable").click(); o.on = ON;
ISS = []; HV = { view: { level: "off" } }; scheduleRefresh(); o.nohc = hero(box);
HV = { view: { level: "ok" } }; scheduleRefresh(); o.ok = hero(box);
HV = null; scheduleRefresh(); o.wait = hero(box);
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["off"], ["ovb.v.off", "is-none"])
        self.assertEqual(out["on"], ["enable"])
        self.assertEqual(out["nohc"], ["ovb.v.nohc", "is-none"])
        self.assertEqual(out["ok"], ["ovb.v.ok", ""])
        self.assertEqual(out["wait"], ["ovb.v.wait", "is-none"])

    def test_stand_it1_radar_and_exit_line(self):
        """Стенд it1 Н-1: у «Отдачи» fresh=null во всех проверках — радар Сводки рисовал её как 0 (точка многоугольника
        в центре). Теперь многоугольник по 7 измеренным звеньям, на оси «Отдачи» — пунктир и пустая отметка, в подписи
        радара — «нет данных». Н-2: HTTP-проба AmneziaWG раз в 2 мин (rtt в каждой второй минуте, выход «работает») —
        линия «Выходы · час» одна, а не 30 точек; минута «не работает» рвёт её честно."""
        out = run(r"""
const o = {};
const L = (id, fresh) => ({ id, fresh, usual: fresh, lo: fresh === null ? null : fresh - 20, hi: fresh === null ? null : fresh + 20, n: fresh === null ? 0 : 9 });
API_DATA.synth = { calibrated: true, last_at: 1, fresh: { total: 808, weak: "net_q" }, totals: { usual: 817 }, checks: [],
  links: [L("net_bw", 820), L("net_q", 712), L("cpu", 900), L("stab", 870), L("down", 830), L("up", null), L("loss", 860), L("crypto", 840)] };
const alt = Array.from({ length: 60 }, (_x, i) => (i % 2 ? null : 110));
const down = alt.slice(); down[30] = null;
const st2 = Array(60).fill(1); st2[30] = 3;
API_DATA.hour = { charts: true, step: 60, n: 60, exits: { "e-1": { rtt: alt, st: Array(60).fill(1), loss: Array(60).fill(0) },
  "e-2": { rtt: down, st: st2, loss: Array(60).fill(0) } } };
R.exits.data.rows = [exitRow("e-1", "Вена"), exitRow("e-2", "Осло")];
const box = await summary();
const st = box.querySelector(".kb-st");
const now = st.querySelector("polygon.kb-st__now").getAttribute("points").split(" ");
const c = (() => { const ax = st.querySelectorAll("line.kb-st__ax")[0]; return ax.getAttribute("x1") + "," + ax.getAttribute("y1"); })();
o.now = [now.length, now.indexOf(c) >= 0];
o.med = st.querySelector("polygon.kb-st__med").getAttribute("points").split(" ").indexOf(c) >= 0;
o.band = !!st.querySelector("path.kb-st__band");
o.nd = [st.querySelectorAll("circle.kb-st__nd").length, st.querySelectorAll("line.kb-st__ax--nd").length,
  st.querySelectorAll("line.kb-st__ax")[5].getAttribute("class")];
o.aria = st.querySelector("svg").getAttribute("aria-label");
const rows = box.querySelector('section[aria-label="ovb.xnT"]').querySelectorAll("button.kb-xn__r");
o.lines = rows.map((r) => (r.querySelector("path.gx-spark__l").getAttribute("d").match(/M/g) || []).length);
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["now"], [7, False])            # было [8, True]: «Отдача» — точка в центре
        self.assertFalse(out["med"])
        self.assertTrue(out["band"])                        # было: полоса «обычно» пропадала целиком
        self.assertEqual(out["nd"], [1, 1, "kb-st__ax kb-st__ax--nd"])
        self.assertIn("ovb.ln.up: ovb.nd", out["aria"])
        self.assertEqual(out["lines"], [1, 2])              # было [30, 30]: точки вместо линии

    def test_the_reason_of_a_link_without_data(self):
        """Л3 №8: у «Отдачи» нет балла, потому что узел в России (why no_upload_ru у звена /api/host/synth). Подпись
        оси — по-прежнему «нет данных», а aria-label радара и подсказка сектора в карточке балла — точная причина
        («в России не меряется»). Звено без причины — «нет данных», как было."""
        out = run(r"""
const o = {};
const L = (id, fresh, why) => ({ id, fresh, usual: fresh, lo: null, hi: null, n: 0, why: why || null });
API_DATA.synth = { calibrated: true, last_at: 1, fresh: { total: 700, weak: "net_q" }, totals: {}, checks: [],
  links: [L("net_bw", 820), L("net_q", 712), L("cpu", 900), L("stab", 870), L("down", 830), L("up", null, "no_upload_ru"),
    L("loss", null), L("crypto", null, "evil")] };
const box = await summary();
o.aria = box.querySelector(".kb-st").querySelector("svg").getAttribute("aria-label");
const card = DEF.scoreCard({});
o.tips = card.querySelectorAll("path.kb-st__hit").map((p) => p.querySelector("title").textContent);
o.labs = card.querySelectorAll("text.kb-st__lab--nd").map((x) => x.textContent);
console.log(JSON.stringify(o));
""")
        self.assertIn("ovb.ln.up: ovb.ndw.inRu", out["aria"])           # без правки: «ovb.ln.up, …: ovb.nd»
        self.assertIn("ovb.ln.loss: ovb.nd", out["aria"])
        self.assertIn("ovb.ln.crypto: ovb.nd", out["aria"])             # неизвестный код — без слов сервера
        self.assertEqual(out["tips"][5], "Ovb.ln.up: ovb.ndw.inRu")
        self.assertTrue(out["tips"][6].startswith("ovb.tipNo "), out["tips"][6])
        self.assertEqual(out["labs"], ["ovb.nd"] * 3)                   # подпись оси не меняется

    def test_the_weak_link_on_the_radar_and_the_why_line(self):
        """Владелец 29.10 (D-143): слабое звено видно на радаре — вершина (``kb-st__dot--weak``) и подпись тем же
        предупреждающим цветом, что «хуже обычного» (``kb-st__lab--warn``), со строкой «слабое звено»; подсказка
        сектора называет правило с порогом из ответа сервера (``weak_gap``). «Почему итог не выше» — одна строка и
        только когда предел держит итог (``limited``); слабого звена нет — ни вершины, ни слова; итога нет — строка
        «итога пока нет» без этого заголовка."""
        out = run(r"""
const o = {};
const L = (id, fresh) => ({ id, fresh, usual: fresh, lo: null, hi: null, n: 0, calib: true });
const links = (up) => [L("net_bw", 820), L("net_q", 760), L("cpu", 900), L("stab", 870), L("down", 830), L("up", up), L("loss", 880), L("crypto", 880)];
const syn = (fresh, up) => ({ calibrated: true, last_at: 1, weak_gap: 30, totals: { usual: 800 }, checks: [], fresh, links: links(up) });
const look = (card) => ({
  weak: card.querySelectorAll("text.kb-st__lab--warn").map((x) => x.textContent),
  dots: card.querySelectorAll("circle.kb-st__dot--weak").length,
  tip: card.querySelectorAll("path.kb-st__hit").map((p) => p.querySelector("title").textContent)[5],
  why: card.querySelectorAll(".kb-why").map((x) => x.textContent),
  mut: card.querySelectorAll("p.kb-mut").map((x) => x.textContent),
  aria: card.querySelector("svg").getAttribute("aria-label") });
await summary();
API_DATA.synth = syn({ total: 346, raw: 790, cap: 346, weak: "up", limited: true }, 77);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.held = look(DEF.scoreCard({}));
API_DATA.synth = syn({ total: 790, raw: 790, cap: 862, weak: "up", limited: false }, 512);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.free = look(DEF.scoreCard({}));
API_DATA.synth = syn({ total: 820, raw: 820, cap: null, weak: null, limited: false }, 700);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.none = look(DEF.scoreCard({}));
API_DATA.synth = syn({ total: null, raw: 790, cap: null, weak: null, limited: false, why: "incomplete" }, 700);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.inc = look(DEF.scoreCard({}));
// телефон: у верхней оси (Канал) третьей строке нет места — «слабое звено» в строке значения; у боковой — своей строкой
PHONE = true;
API_DATA.synth = syn({ total: 500, raw: 790, cap: 500, weak: "net_bw", limited: true }, 700);
API_DATA.synth.links[0].fresh = 150;
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.phTop = look(DEF.scoreCard({})).weak;
API_DATA.synth = syn({ total: 346, raw: 790, cap: 346, weak: "up", limited: true }, 77);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.phSide = look(DEF.scoreCard({})).weak;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["held"]["weak"], ["ovb.ln.up", "77", "ovb.weak"])          # без правки: только точка
        self.assertEqual(out["held"]["dots"], 1)
        self.assertTrue(out["held"]["tip"].endswith(' · ovb.weakTip {"gap":"30"}'), out["held"]["tip"])
        self.assertIn("ovb.weak: ovb.ln.up 77", out["held"]["aria"])
        self.assertEqual(out["held"]["why"], ['ovb.whyovb.why.lim {"name":"ovb.ln.up","v":"77","cap":"346","raw":"790"}'])
        self.assertEqual(out["free"]["weak"], ["ovb.ln.up", "512", "ovb.weak"])         # звено видно, предел не держит
        self.assertEqual(out["free"]["why"], [])                                        # без правки: «ovb.why.free …»
        self.assertEqual((out["none"]["weak"], out["none"]["dots"], out["none"]["why"]), ([], 0, []))
        self.assertNotIn("ovb.weak", out["none"]["aria"])
        self.assertEqual(out["inc"]["why"], [])
        self.assertIn("ovb.why.incomplete", out["inc"]["mut"])
        self.assertEqual(out["phTop"], ["ovb.ln.net_bw", "150 · ovb.weak"])
        self.assertEqual(out["phSide"], ["ovb.ln.up", "77", "ovb.weak"])

    def test_provisional_total_and_links(self):
        """Владелец 28.10, решения 4 и 5. Итог первых часов нового узла (``provisional``) — «предварительно» везде, где
        он виден: Сводка, окно балла (шапка и «Почему итог не выше»), карточка «Проверка сервера»; часы — из ответа
        сервера (правило кода), не числом в тексте. Звенья без эталонов (``calib`` false) — «≈» у значения радара,
        подпись «≈ — предварительно», слово в подсказке сектора; выверенные — без пометки. «Как считается» — какие
        звенья выверены, без самих эталонов (владелец 29.10, D-142: ни хостера, ни тарифа, ни процессора, ни города —
        даже если ответ прежнего сервера их принёс). Прежней плашки «якоря без калибровки» нет нигде."""
        out = run(r"""
const o = {};
const L = (id, fresh, calib, why) => ({ id, fresh, usual: fresh, lo: null, hi: null, n: 0, calib, why: why || null });
// ответ сервера до D-142 (refs с хостером и тарифом) — окно их не показывает
const REFS = [{ anchor: 500, scope: "all", hoster: "Aeza", tariff: "MSKs-1", vcpu: 1, dedicated: false, cpu: "AMD Ryzen 9 9950X", city: "msk", date: "2026-10-28" },
  { anchor: 700, scope: "all", hoster: "Aeza", tariff: "MSKs-2", vcpu: 2, dedicated: false, cpu: "AMD Ryzen 9 9950X", city: "msk", date: "2026-10-28" },
  { anchor: 900, scope: "core", hoster: "Aeza", tariff: "MSK-1", vcpu: 1, dedicated: true, cpu: "AMD Ryzen 9 9950X", city: "msk", date: "2026-10-23" }];
const doc = (prov, allCal) => ({ calibrated: !!allCal, last_at: 1, refs: REFS, provisional: prov, net_window_h: 12, totals: {}, checks: [{ id: "a", ts: 1, mode: "full", total: 700, links: {} }],
  fresh: { total: 700, raw: 720, cap: 900, weak: "up", limited: false },
  links: [L("net_bw", 820, !!allCal), L("net_q", null, false, prov ? "young" : null), L("cpu", 790, true), L("stab", 870, !!allCal), L("down", 830, !!allCal),
    L("up", 550, !!allCal), L("loss", null, false, prov ? "young" : null), L("crypto", 880, true)] });
API_DATA.synth = doc({ why: "net_young", have_h: 5, need_h: 12 }, false);
const box = await summary();
const need = (root) => root.querySelectorAll(".gx-need").map((x) => [x.textContent, x.getAttribute("title")]);
o.hero = need(box);
const card = DEF.scoreCard({});
o.card = need(card);
o.vals = card.querySelectorAll("text.kb-st__lab--v").map((x) => x.textContent);
o.cap = card.querySelectorAll(".kb-st__cap").map((x) => x.textContent);
o.tips = card.querySelectorAll("path.kb-st__hit").map((p) => p.querySelector("title").textContent);
o.why = card.querySelectorAll(".kb-why__prov").map((x) => x.textContent);
let head = null;
const body = el("div");
window.KaskadPanel.openSheet = () => ({ setHead: (x) => { head = x; }, closeBtn: () => el("button"), body, el: body });
DEF.openScore();
o.drwHead = head ? head[0].textContent : null;
const det = body.querySelector("details");
o.how = det ? det.querySelectorAll("p.kb-mut").map((x) => x.textContent) : null;
o.refs = det ? det.querySelectorAll("li").length : null;
o.drwText = body.textContent;
o.drwCap = body.querySelectorAll(".kb-st__cap").length;
// всё выверено и узел не молодой — ни пометки, ни «≈», ни подписи
await tick(); await tick();
API_DATA.synth = doc(null, true);
DEF.reload(); await tick(); await tick(); await tick(); scheduleRefresh();
o.hero2 = need(box);
const card2 = DEF.scoreCard({});
o.card2 = [need(card2).length, card2.querySelectorAll(".kb-st__cap").length, card2.querySelectorAll(".kb-why__prov").length,
  card2.querySelectorAll("text.kb-st__lab--v").map((x) => x.textContent)];
o.old = JSON.stringify(o).indexOf("noCalib") >= 0;
console.log(JSON.stringify(o));
""")
        prov_x = 'ovb.provX {"have":"5","need":"12"}'                          # часы — из ответа сервера
        self.assertEqual(out["hero"], [["ovb.prov", prov_x]])                   # без правки: «ovb.noCalib»
        self.assertEqual(out["card"], [["ovb.prov", prov_x]])
        self.assertEqual(out["vals"], ["≈820", "790", "≈870", "≈830", "≈550", "880"])  # процессор и шифрование — без «≈»
        self.assertEqual(out["cap"], ["ovb.provCap"])
        self.assertTrue(out["tips"][0].endswith(" · ovb.prov"), out["tips"][0])
        self.assertFalse(out["tips"][2].endswith("ovb.prov"), out["tips"][2])
        self.assertEqual(out["tips"][1], "Ovb.ln.net_q: ovb.ndw.young")
        self.assertEqual(out["why"], [prov_x])
        self.assertTrue(out["drwHead"].endswith(" · ovb.prov"), out["drwHead"])
        self.assertEqual([x.split(" ")[0] for x in out["how"]], ["ovb.dr.howX", "ovb.provHow", "ovb.calib"])   # без «Эталонов»
        self.assertEqual(out["how"][1], 'ovb.provHow {"need":"12"}')                 # правило первых часов — часы из ответа
        self.assertEqual(json.loads(out["how"][2].split(" ", 1)[1]),
                         {"ok": "ovb.ln.cpu, ovb.ln.crypto", "prov": "ovb.ln.net_bw, ovb.ln.net_q, ovb.ln.stab, ovb.ln.down, "
                                                                     "ovb.ln.up, ovb.ln.loss"})
        for w in ("Aeza", "MSKs", "MSK-1", "Ryzen", "9950X", "msk", "ovb.ref", "ovb.city"):       # D-142: без правки — есть
            self.assertNotIn(w, out["drwText"], w)
        self.assertEqual((out["refs"], out["drwCap"]), (0, 1))
        self.assertEqual(out["hero2"], [])
        self.assertEqual(out["card2"], [0, 0, 0, ["820", "790", "870", "830", "550", "880"]])
        self.assertFalse(out["old"])

    def test_health_tiles_lead_to_the_health_screen(self):
        """Было: плитка «Сервер» и плитки «Сводки» — кнопки в свой раздел. Живая Сводка: четыре плитки здоровья —
        кнопки в «Здоровье сервера», состояние словами («Проверки здоровья выключены» и нет данных — не «норма»)."""
        out = run(r"""
const o = {};
HV = { view: { level: "crit" }, raw: { checks: { cpu: { shown: "ok", value: 12 }, disk: { shown: "crit", value: 98 } } }, host: { cpu: 12 } };
let box = await summary();
const card = () => box.querySelector('[data-find="overview.health"]');
o.tiles = card().querySelectorAll("button.kb-hl__c").map((b) => b.className);
card().querySelectorAll("button.kb-hl__c")[2].click(); byText(card(), "button", "ovb.hlMore").click();
o.go = GO;
o.chip = card().querySelector(".kb-h").textContent;
HV = { view: { level: "off" }, raw: { checks: {} }, host: null }; scheduleRefresh();
o.offChip = card().querySelector(".kb-h").textContent;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["tiles"], ["kb-hl__c", "kb-hl__c", "kb-hl__c is-crit", "kb-hl__c"])
        self.assertEqual(out["go"], [["overview", {"tab": "health"}]] * 2)
        self.assertIn("ovb.lv.crit", out["chip"])
        self.assertIn("ovb.lv.wait", out["offChip"])
        self.assertNotIn("ovb.lv.ok", out["offChip"])


DISK = {"id": "disk", "level": "crit", "text": "Диск / почти заполнен", "advice": "Совет: почистить журналы"}


@unittest.skipUnless(shutil.which("node"), "нет node: «Здоровье сервера» не отрисовать")
class HealthLiveTest(unittest.TestCase):
    """Было: светофор старого «Обзора» (hostHealthView в ovHost/ovMhost, test_panel_js_contract.HostTileTest). Живой
    экран «Здоровье сервера» рисует тот же ответ (OVB_CTX.health → hostHealthOf): состояние словами без числа, «с …»
    только у сказанного уровня, «без истории» у мгновенного ответа, у метрики с находкой — её текст и совет."""

    def test_every_level_in_words(self):
        out = run(r"""
const o = {};
let box = null;
for (const lv of ["ok", "warn", "crit", "wait", "off"]) {
  HV = { view: { level: lv, since: 1791180000, noHistory: lv === "crit" }, raw: { level: lv, top: lv === "crit" ? [DATA.disk] : [],
    checks: lv === "crit" ? { disk: { shown: "crit", value: 98 } } : {} }, host: null };
  if (!box) box = await health(); else scheduleRefresh();
  o[lv] = { st: txtOf(box, ".kk-hh__st"), al: box.querySelector(".kk-hh__al").className, note: txtOf(box, ".kk-hh__note"),
    disk: box.querySelectorAll(".kk-hh__r")[2].querySelector(".kk-hh__do").textContent };
}
console.log(JSON.stringify(o));
""", {"disk": DISK})
        self.assertEqual(out["ok"]["st"], 'ovb.hh.okovb.hh.since {"t":"T1791180000"}')
        self.assertEqual(out["warn"]["al"], "kk-hh__al is-warn")
        self.assertEqual(out["crit"]["al"], "kk-hh__al is-crit")
        for lv in ("wait", "off"):                                         # «с …» — только у сказанного уровня
            self.assertEqual(out[lv]["st"], "ovb.hh." + lv)
        self.assertEqual(out["crit"]["note"], "ovb.hh.instant")            # мгновенный ответ — «без истории»
        self.assertIsNone(out["ok"]["note"])
        self.assertEqual(out["crit"]["disk"], "ovb.hh.do" + DISK["text"] + ". " + DISK["advice"])
        self.assertEqual(out["ok"]["disk"], "ovb.hh.normovb.hh.okDisk")
        for lv in out:                                                     # без числа замечаний (владелец 28.10)
            self.assertNotIn("healthMore", json.dumps(out[lv]))


if __name__ == "__main__":
    unittest.main()
