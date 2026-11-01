"""П4-в (6C «Сейчас»), сокращение оценки (NS-D30–NS-D35), UI-C (макет «Проверка сервера r1»): провязка вкладки
«Сейчас» раздела «Диагностика» — весь pages/diag.js в node на поддельном DOM (как tests/test_panel_diag_screen.py),
сервер — поддельные K.API.host.score и bench.start с ответами panel/host.py (контракт — summary.rel_total,
links[].rel/fresh_raw/usual_raw/parts/dev*/hint, series, days[].m/checks):

* итог — «в целом 96 от обычного» и «100 — как обычно у этого сервера»; числа нет — «Сравнивать пока не с чем» и
  сколько набралось (звеньев с историей N из 3 или история N из 3 дней, NS-D35); история есть, а свежих нет — «—»;
  число ниже 100 — тоже число, без пояса словом;
* таблица звеньев макета: звено · сейчас · обычно · от обычного · отметка · 30 дней · замеров; строки — скачивание,
  отдача, отклик, дрожание, потери, шифрование, процессор (тест группы — свои числа и только свои отметки); единицы
  ядра — словами панели; ноль в знаменателе — «—»; звезды, балла 0–1000, поясов и «слабого звена» нет;
* отметки: ▲/▼ (два дня подряд), ждёт подтверждения, резкое падение (ждёт повтора), падение подтвердилось, мало
  истории, не измерено — коротко в строке, полный текст — подсказкой строки; строка отметок под итогом;
* линия 30 дней (KaskadSpark, своя отрисовка): точки — к «обычно», выше — лучше, у отклика — наоборот; серый день —
  пунктир; коридор p25…p75; точка «сейчас» справа — цветом отметки;
* давно не проверялся — серым, дата из summary.last_at; у тестов свежих нет — «—»;
* полоса 30 дней: 30 дней, точка — день с полным тестом, подсказка дня (наведение, фокус, нажатие закрепляет,
  повторное — снимает, Esc и «×»), в ней — цифры дня (days[].m) и проверки дня (days[].checks), «Открыть в истории»;
* шапка: «Проверить сейчас» (окно цены), «Поделиться» — недоступна до выпуска; «Последняя проверка» раскрывается,
  уход из раздела её сворачивает; «Расписание выключено» / «Следующая — …»;
* телефон — список звеньев вместо таблицы, без легенды;
* окно цены: цена обоих видов — одно чтение GET /api/host/bench/price (без 4xx в консоли, T-3377 Н-15), запуск с ключом; галочка программ (без неё «Проверить» выключена),
  «без программ» — install: false; 409 need_install / running, 429 limit — тексты; во время запроса второй POST не уходит;
* плашка расписания: «Проверить сейчас» / «Установить» открывают окно цены; сводка — «до N проверок в день».

Node-less: skipped."""

import json
import os
import subprocess
import tempfile
import unittest

from tests.test_panel_diag_screen import HAS_NODE, run, scope
from tests.test_panel_rf_js import DOM, read

SETUP = r"""
U.radio = (items) => el("div", { "data-radio": "" }, items.filter(Boolean).map((it) => {
  const b = el("button", { type: "button", "data-mode": it.title, "aria-checked": String(!!it.on) }, it.title, " ", it.note || "");
  if (it.dis) b.disabled = true;
  b.addEventListener("click", () => it.pick && it.pick());
  return b;
}));
U.check = (items) => el("div", { "data-check": "" }, items.filter(Boolean).map((it) => {
  const b = el("button", { type: "button", role: "checkbox", "aria-checked": String(!!it.on) }, it.title);
  if (it.dis) b.disabled = true;
  b.addEventListener("click", () => it.pick && it.pick(!it.on, b));
  return b;
}));
U.al = (tone, title, text, o) => el("div", { "data-al": tone }, title, " ", text || "", ((o && o.btns) || []).filter(Boolean));
// GET /api/host/score (NS-D30): часть звена — тест [test, свежее, обычно, единица ядра, rel]; o.young — у тестов «обычно»
// не набралось (2 из 3), «от обычного» только у двух звеньев проб; o.stale — у тестов свежих нет (давно не проверялся);
// o.few — ни у одного звена rel нет, проверок 1 из 3; o.low — итог 7 (числа итога — сервера: stale — 97 по пробам)
const P = (test, f, u, unit, rel, n) => ({ test, fresh_raw: f, usual_raw: u, usual_n: n === undefined ? 12 : n, unit, rel });
function linksOf(o) {
  const tn = o.few ? 1 : o.young ? 2 : 12, pn = o.few ? 1 : 12;
  const tf = (v) => (o.stale ? null : v), tu = (v) => (tn >= 3 ? v : null), tr = (v) => (o.stale || tn < 3 ? null : v), pr = (v) => (pn < 3 ? null : v);
  const one = (id, source, p, dev) => ({ id, source, rel: p.rel, test: p.test, fresh_raw: p.fresh_raw, fresh_n: 3, unit: p.unit, usual_raw: p.usual_raw,
    usual_n: p.usual_n, usual_mode: null, p25_raw: null, p75_raw: null, now_id: null, now_day: null, dev: dev || null, dev_test: dev ? p.test : null,
    dev_pending: null, dev_pending_kind: null, parts: [p], hint: null });
  const group = (id, source, rel, parts) => ({ id, source, rel, test: null, fresh_raw: null, fresh_n: 3, unit: null, usual_raw: null,
    usual_n: Math.max(...parts.map((p) => p.usual_n)), usual_mode: null, p25_raw: null, p75_raw: null, now_id: null, now_day: null, dev: null,
    dev_test: null, dev_pending: null, dev_pending_kind: null, parts, hint: null });
  const down = P("net_down", tf(675), tu(690), "Мбит/с", tr(98), tn), up = P("net_up", tf(120), tu(300), "Мбит/с", tr(40), tn);
  const c1 = P("cpu_1t", tf(1180), tu(1200), "событий/с", tr(98), tn), mt = P("cpu_mt", tf(1530), tu(1540), "событий/с", tr(99), tn);
  const aes = P("aes_1t", tf(2900), tu(2950), "МБ/с", tr(98), tn);
  const dl = one("down", "tests", down);
  if (tu(1) !== null) { dl.p25_raw = 660; dl.p75_raw = 720; }
  const ul = one("up", "tests", up, o.stale || tn < 3 ? null : "down");
  if (ul.dev) ul.hint = { test: "net_up", now: 120, usual: 300, unit: "Мбит/с", days: [["2026-10-19", 130], ["2026-10-20", 120]] };
  return [
    group("net_bw", "tests", tr(70), [down, up]),
    group("net_q", "probes", o.few ? null : 97, [P("rtt", 38, pr(36), "мс", pr(95), pn), P("jitter", 1.2, pr(1.1), "мс", pr(92), pn), P("loss", 0.06, pr(0.05), "%", pr(83), pn)]),
    group("cpu", "tests", tr(98), [c1, mt]),
    group("stab", "probes", o.few ? null : 101, [P("net_cv", 6, pr(7), "%", pr(101), pn)]),
    dl, ul,
    one("loss", "probes", P("loss", 0.06, pr(0), "%", null, pn)),                         // «обычно» 0 — ноль в знаменателе: «—»
    one("crypto", "tests", aes),
  ];
}
// 30 дней по 2026-10-20: [26] — жёлтый с причиной, [1] — серый (нет данных), [29] и [15] — полный тест; у дня — цифры
// проб (m) и проверки (checks): в [29] ещё проверка, которая не завершилась, в [27] — пропущенная
const days = () => { const out = []; for (let i = 29; i >= 0; i -= 1) {
  const day = new Date(Date.UTC(2026, 9, 20) - i * 86400000).toISOString().slice(0, 10), grey = i === 28, test = i % 2 === 0 && !grey;
  const checks = test ? [{ hhmm: "04:10", mode: i === 0 || i === 14 ? "full" : "quick", by: "schedule", status: "ok", down: 760, up: 495 }] : [];
  if (i === 0) checks.push({ hhmm: "12:30", mode: "quick", by: "panel", status: "failed", down: null, up: null });
  if (i === 2) checks.push({ hhmm: "13:00", mode: "quick", by: "schedule", status: "skipped", down: null, up: null });
  out.push({ day, color: i === 3 ? "yellow" : grey ? "grey" : "green", reason: i === 3 ? { code: "warn", args: {}, msg: "Отдача хуже обычного: 120 Мбит/с, обычно 300" } : null,
    test, full: i === 0 || i === 14, partial: false, checks,
    m: grey ? { rtt: null, rtt_p90: null, loss: null, loss_peak: null, jit: null, off: null }
      : { rtt: 8.2, rtt_p90: 14, loss: i === 3 ? 0.41 : 0.05, loss_peak: i === 3 ? 3.1 : 0.2, jit: 1.1, off: i === 3 ? 12 : 0 } }); } return out; };
// линия: проверки — ts (04:10 по Москве), пробы — дни
const TS = (d, hh, mm) => Date.UTC(2026, 9, d, hh - 3, mm) / 1000;
const SERIES = { net_down: [[TS(19, 4, 10), 690], [TS(20, 4, 10), 675]], net_up: [], rtt: [["2026-10-19", 36], ["2026-10-20", 72]],
  jitter: [], loss: [], chacha_1t: [], aes_1t: [[TS(6, 4, 11), 2950]], cpu_mt: [] };
// stale: годной проверки нет 45 дней — в полосе 30 дней дней с проверкой нет, дата — summary.last_at (З-1)
const AUG29 = Date.UTC(2026, 7, 29, 12) / 1000;
function scoreOf(o) {
  o = o || {};
  const stale = !!o.stale;
  const links = linksOf(o);
  const rels = links.map((l) => l.rel).filter((x) => x !== null);
  return { ok: true, charts: true, dev_rule: { pct: 15, use_spread: true }, links, rows: { people: null, load: null }, stab: null, series: clone(SERIES),
    days: stale ? days().map((d) => Object.assign(d, { test: false, full: false, checks: [] })) : days(),
    summary: { rel_total: o.low ? 7 : stale ? 97 : rels.length >= 3 ? 96 : null, rel_n: rels.length, rel_need: 3, last_id: "r1",
      last_at: stale ? AUG29 : Date.UTC(2026, 9, 20, 4) / 1000, fresh_from: null, first: false, stale,
      checked: stale ? null : { part: "night", hhmm: "04:10" },
      retry: stale ? null : { at: "15:40", links: ["up"], link: "up", test: "net_up", value: 5, unit: "Мбит/с" }, base_since: [] } };
}
let SCORE = scoreOf();
const K0 = window.KaskadPanel;
K0.API.host.score = () => Promise.resolve(clone(SCORE));
const NAVS = [];
K0.goNav = (id, o) => { NAVS.push([id, clone(o)]); };
// окно цены: SRVB — ответы POST /api/host/bench по телу (как host_bench_cli.panel_start)
const POSTS = [];
const PRICE = { quick: { rx_mb: 250, tx_mb: 250, cpu_min: 0.9, basis: "caps", max_min: 6, confirm: "0a0a0a0a" },
  full: { rx_mb: 2000, tx_mb: 2000, cpu_min: 1, basis: "caps", max_min: 12, confirm: "f0f0f0f0" } };
const PK = { missing: ["sysbench", "fio"], available: true, install: ["fio", "sysbench"], kib: 2380, question: "?" };
const NOPK = { missing: [], available: true, install: [], kib: 0, question: null };
const refuse = (status, code, more) => Promise.reject({ status, code, message: "e:" + code, body: Object.assign({ ok: false, code, error: "Сервер: " + code }, more || {}) });
let PKGS = PK, LIMIT = null, RUNNING = false, HOLD = false, NEED_INSTALL_ONCE = false, UPQ = null;
API.start = (b) => {
  POSTS.push(clone(b));
  if (RUNNING) return refuse(409, "running", { id: "fedcba9876" });
  if (LIMIT && (LIMIT === "all" || b.mode === "full")) return refuse(429, "limit", { limits: { per_day: 6, full_per_day: 2 }, today: { all: LIMIT === "all" ? 6 : 2, full: 2 } });
  const pr = PRICE[b.mode];
  // как panel_start: программы того, что пойдёт — у полной с замером отдачи набор upload_ru.pkgs (С2)
  const P = b.mode === "full" && UPQ && UPQ.pkgs && b.iperf !== false ? UPQ.pkgs : PKGS;
  if (b.confirm !== pr.confirm) return refuse(409, b.confirm ? "confirm_changed" : "need_confirm", { price: pr, pkgs: clone(P) });
  if (NEED_INSTALL_ONCE) { NEED_INSTALL_ONCE = false; PKGS = PK; return refuse(409, "need_install", { price: pr, pkgs: clone(PK) }); }
  if (P.missing.length && typeof b.install !== "boolean") return refuse(409, "need_install", { price: pr, pkgs: clone(P) });
  if (HOLD) return new Promise(() => {});
  return Promise.resolve({ ok: true, id: "abcdef0123", mode: b.mode, missing: [], price: pr });
};
// чтение цены окна — как host_bench_cli.panel_price: «уже идёт» без цен; вид за пределом дня — без цены, с limit
const READS = [];
API.price = () => {
  READS.push(1);
  if (RUNNING) return Promise.resolve({ ok: true, running: "fedcba9876", modes: {}, limit: {}, pkgs: null });
  const modes = {}, limit = {};
  ["quick", "full"].forEach((m) => {
    if (LIMIT && (LIMIT === "all" || m === "full")) limit[m] = { text: "Сервер: limit", limits: { per_day: 6, full_per_day: 2 }, today: { all: LIMIT === "all" ? 6 : 2, full: 2 } };
    else modes[m] = clone(PRICE[m]);
  });
  return Promise.resolve({ ok: true, running: null, modes, limit, pkgs: Object.keys(modes).length ? clone(PKGS) : null, upload_ru: clone(UPQ) });
};
const btnIn = (root, label) => root.querySelectorAll("button").filter((b) => b.textContent.trim() === label)[0] || null;
const startsWith = (root, label) => root.querySelectorAll("button").filter((b) => b.textContent.trim().indexOf(label) === 0)[0] || null;
async function openCostWin() {
  btnIn(SEC, "Проверить сейчас").click(); await wait(10);
  return lastOpen();
}
const foot = (d) => d.box.querySelector("[data-foot]");
const sp = (s) => String(s).replace(/[\u00a0\u202f]/g, "");
const nb = (s) => String(s).replace(/[\u00a0\u202f]/g, " ");          // в подсказке дня — пробелом          // разряды Intl (неразрывные пробелы) — без них
// строки «Звеньев» (круг 1 Т1, вариант Б): ячейки через «|» (звено и отметка|линия|сейчас|обычно|от обычного); title — полный текст отметки и «В базе: N»
const lrows = () => SEC.querySelectorAll(".kb-lk__r").map((x) => sp(x.children.map((c) => c.textContent).join("|")));
const ltips = () => SEC.querySelectorAll(".kb-lk__r").map((x) => sp(x.getAttribute("title")));
const relN = () => { const n = SEC.querySelector(".gx-rel__n"); return n ? { n: n.textContent, grey: (n.getAttribute("class") || "").indexOf("is-grey") >= 0 } : null; };
const relText = () => (SEC.querySelector(".gx-chk__main") || { textContent: "" }).textContent;
const marks = () => (SEC.querySelector(".gx-rel__mk") || { textContent: "" }).textContent;
const dayBtns = () => SEC.querySelectorAll(".gx-day");
const tipBox = () => SEC.querySelector(".gx-daytip");
"""


# фокус как в браузере: blur/focusout у прежнего, focus/focusin у нового (в поддельном DOM события всплывают)
FOCUS = r"""
FakeNode.prototype.focus = function () {
  const a = document.activeElement;
  if (a === this || this.disabled) return;
  if (a) { a.dispatch("blur"); a.dispatch("focusout"); }
  document.activeElement = this;
  this.dispatch("focus"); this.dispatch("focusin");
};
"""


def go(script):
    return run(SETUP + script)


def go_spark(script):
    """Как ``go``, но со spark.js до pages/diag.js: линия звена рисуется KaskadSpark (своя отрисовка paint) в ячейке
    300×24 (поддельный размер ячейки)."""
    head = ("FakeNode.prototype.getClientRects = function () { return [1]; };\n"
            "FakeNode.prototype.insertBefore = function (n, ref) { if (n.parentNode) n.parentNode.removeChild(n); "
            "const i = ref ? this.childNodes.indexOf(ref) : -1; n.parentNode = this; if (i < 0) this.childNodes.push(n); "
            "else this.childNodes.splice(i, 0, n); return n; };\n"
            "window.requestAnimationFrame = (f) => setTimeout(f, 0);\n"
            "FakeNode.prototype.getBoundingClientRect = function () { return { top: 0, left: 0, width: 300, height: 24 }; };\n")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "diag.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(DOM + read("spark.js") + "\n" + scope() + "\n" + head + "\n(async () => {\n" + SETUP + script +
                    "\n})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });")
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode != 0 or not out:
        raise AssertionError("node: %s" % r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


ABSOLUTE = ("балл", "звезд", "слабое звено", "почти не работает", "очень плохой", "базовый", "хороший", "0–1000", "в оценку")


@unittest.skipUnless(HAS_NODE, "no node")
class NowScreenTest(unittest.TestCase):
    def test_the_total_the_links_and_the_strip(self):
        r = go("""
await mount([], { tab: "now" });
const ds = dayBtns();
console.log(JSON.stringify({ text: SEC.textContent, main: relText(), rel: relN(), of: SEC.querySelector(".gx-rel__u").textContent, n: ds.length,
  full: ds.filter((b) => (b.getAttribute("class") || "").indexOf("is-test") >= 0).length, fullAria: ds[29].getAttribute("aria-label"),
  warn: (ds[26].getAttribute("class") || "").indexOf("gx-day--warn") >= 0, grey: (ds[1].getAttribute("class") || "").indexOf("gx-day--none") >= 0,
  svg: SEC.querySelectorAll("svg").length, rows: lrows(), tips: ltips(), marks: marks(),
  head: SEC.querySelector(".kb-lk__h").children.map((c) => c.textContent), aria: SEC.querySelector(".kb-lk").getAttribute("aria-label") }));
""")
        self.assertEqual(r["rel"], {"n": "96", "grey": False})
        self.assertEqual(r["of"], "от обычного")
        # круг 1 Т1: правая колонка карточки «Балл сервера» — когда проверено, затем «от обычного»
        self.assertTrue(r["main"].startswith("проверено ночью, 04:1096от обычного100 — как обычно у этого сервера"), r["main"])
        for s in ("проверено ночью, 04:10", "Проверю ещё раз около 15:40", "точка — полная проверка", "30 дней",
                  "ровно — 28 · предупреждения — 1 · тревога или сбой — 0 · нет данных — 1", "Проверить сейчас", "Поделиться",
                  "Последняя проверка"):
            self.assertIn(s, r["text"])
        self.assertEqual(r["head"], ["Звено", "30 дней", "Сейчас", "Обычно", "От обычного"])
        self.assertEqual(r["aria"], "Звенья: сейчас и обычно")
        self.assertEqual(r["rows"], [
            "Скачивание||675Мбит/с|690Мбит/с|98",
            "Отдача▼ хуже обычного||120Мбит/с|300Мбит/с|40",               # подтверждённая отметка — у своего звена
            "Отклик||38мс|36мс|95",                                       # тест группы «Сеть» — свои числа
            "Дрожание||1,2мс|1,1мс|92",
            "Потери||0,06%|0%|—",                                         # ноль в знаменателе — без числа
            "Шифрование||2900МБ/с|2950МБ/с|98",                           # crypto — числа своего теста
            "Процессор||1530событий/с|1540событий/с|99"])                 # «Процессор» — cpu_mt группы
        self.assertEqual(r["tips"][1], "Два последних дня с проверкой: 130 и 120 Мбит/с — ▼ хуже обычного · обычно 300 Мбит/с")
        self.assertEqual(r["tips"][0], "В обычных пределах")
        self.assertEqual(r["marks"], "Хуже или лучше обычного: отдача ▼")
        self.assertEqual(r["svg"], 0)                                     # без spark.js линий нет; звезды нет (NS-D34)
        for w in ABSOLUTE:
            self.assertNotIn(w, r["text"].lower(), w)
        self.assertEqual(r["n"], 30)
        self.assertEqual(r["full"], 2)
        self.assertTrue(r["warn"])
        self.assertTrue(r["grey"])
        self.assertIn("полный тест", r["fullAria"])

    def test_units_are_the_words_of_the_panel(self):
        """Единицы ядра (score.py: «Мбит/с», «событий/с», «МБ/с», «мс») — ключи dg.u.* панели: на английском — свои слова."""
        r = go("""
DICT["dg.u.mbps"] = "MBPS"; DICT["dg.u.evs"] = "EVS"; DICT["dg.u.mbs"] = "MBS"; DICT["dg.u.ms"] = "MS";
await mount([], { tab: "now" });
console.log(JSON.stringify({ text: SEC.textContent, rows: lrows(), tips: ltips() }));
""")
        self.assertIn("Отдача▼ хуже обычного||120MBPS|300MBPS|40", r["rows"])
        self.assertIn("Отклик||38MS|36MS|95", r["rows"])
        self.assertIn("Процессор||1530EVS|1540EVS|99", r["rows"])
        self.assertIn("Шифрование||2900MBS|2950MBS|98", r["rows"])
        self.assertIn("130 и 120 MBPS", r["tips"][1])
        for u in ("Мбит/с", "событий/с", "МБ/с"):
            self.assertNotIn(u, r["text"] + "".join(r["tips"]))

    def test_a_low_number_is_still_a_number(self):
        """NS-D34: ниже 100 — тоже число «от обычного», без пояса словом и без «слабого звена»."""
        r = go("""
SCORE = scoreOf({ low: true });
await mount([], { tab: "now" });
console.log(JSON.stringify({ rel: relN(), text: SEC.textContent }));
""")
        self.assertEqual(r["rel"], {"n": "7", "grey": False})
        for w in ABSOLUTE:
            self.assertNotIn(w, r["text"].lower(), w)

    def test_no_number_while_few_links_have_history(self):
        """rel_total нет (звеньев с «от обычного» 2 из 3): «Сравнивать пока не с чем: звеньев с историей 2 из 3»; у
        тестов «обычно» 2 из 3 — (Д2 r3 F) вместо «— —» и отметки «мало истории» одна ячейка «"обычно" появится, когда наберётся история»
        (полный текст строки — «история 2 из 3 дней»), свежее — как есть."""
        r = go("""
SCORE = scoreOf({ young: true });
await mount([], { tab: "now" });
console.log(JSON.stringify({ rel: relN(), main: relText(), rows: lrows(), tips: ltips(), marks: marks() }));
""")
        self.assertIsNone(r["rel"])
        self.assertIn("Сравнивать пока не с чем: звеньев с историей 2 из 3", r["main"])
        self.assertIn("Число появится, когда «обычно» будет хотя бы у трёх звеньев", r["main"])
        self.assertNotIn("100 — как обычно", r["main"])
        self.assertIn("Отдача||120Мбит/с|«обычно» появится, когда наберётся история", r["rows"])         # было «Отдачамало истории||120Мбит/с|—|—»
        self.assertIn("Отклик||38мс|36мс|95", r["rows"])
        self.assertEqual(r["tips"][1], "Сравнивать пока не с чем: история 2 из 3 дней")
        self.assertEqual(r["marks"], "Для отметок пока мало истории: скачивание, отдача, шифрование, процессор")

    def test_no_number_while_few_checks(self):
        """«Мало истории» (NS-D35): ни у одного звена rel нет — «Сравнивать пока не с чем: история 1 из 3 дней», ниже —
        сырые цифры; у всех звеньев — «"обычно" появится, когда наберётся история» (Д2 r3 F), строка отметок — одна фраза."""
        r = go("""
SCORE = scoreOf({ few: true });
await mount([], { tab: "now" });
console.log(JSON.stringify({ rel: relN(), main: relText(), rows: lrows(), marks: marks() }));
""")
        self.assertIsNone(r["rel"])
        self.assertIn("Сравнивать пока не с чем: история 1 из 3 дней", r["main"])
        self.assertIn("Число появится после третьего дня истории; ниже — сырые цифры.", r["main"])
        self.assertEqual(r["rows"][0], "Скачивание||675Мбит/с|«обычно» появится, когда наберётся история")   # было «…мало истории||675Мбит/с|—|—»
        self.assertEqual(r["rows"][2], "Отклик||38мс|«обычно» появится, когда наберётся история")
        self.assertEqual(r["marks"], "Для отметок ▲ ▼ пока мало истории")

    def test_history_but_no_fresh_value_is_a_dash(self):
        """REV-score-cut З-1: история есть (usual_n ≥ 3), а свежих значений нет (тесты не прошли) — «—» в итоге и у
        звеньев, не «3 из 3»; отметка «не измерено» с полным текстом подсказкой."""
        r = go("""
SCORE = scoreOf({});
SCORE.summary.rel_total = null; SCORE.summary.rel_n = 0;
SCORE.links.forEach((l) => { l.rel = null; l.usual_n = 6; l.fresh_raw = null; l.dev = null; l.dev_test = null; l.hint = null;
  (l.parts || []).forEach((p) => { p.rel = null; p.usual_n = 6; p.fresh_raw = null; }); });
await mount([], { tab: "now" });
const dim = SEC.querySelectorAll(".kb-lk__r").filter((x) => (x.getAttribute("class") || "").indexOf("is-dim") >= 0).length;
console.log(JSON.stringify({ rel: relN(), main: relText(), rows: lrows(), tips: ltips(), marks: marks(), dim }));
""")
        self.assertEqual(r["rel"], {"n": "—", "grey": True})
        self.assertNotIn("из 3", r["main"] + " ".join(r["rows"]))
        self.assertNotIn("Сравнивать пока не с чем", r["main"])
        # Л3 №8: у звена без значения — только причина, без «—» и «обычно» в колонках (было «не измерено||—|690Мбит/с|—»)
        self.assertEqual(r["rows"][0], "Скачивание||не измерено")
        self.assertEqual(r["tips"][0], "Свежего значения нет: тест не завершился или давно не запускался")
        self.assertEqual(r["dim"], 7)
        self.assertTrue(r["marks"].startswith("Не измерено: скачивание, отдача"), r["marks"])

    def test_a_link_without_data_says_only_why(self):
        """Л3 №8 (владелец: «почему нет отдачи, нет отклика — незаполненные данные»): узел в России, отдача не меряется,
        истории мало. Было: «Отдача · мало истории | — | в России… | — | —». Стало: причина одной ячейкой на месте
        «сейчас · обычно · от обычного», без прочерков и без отметки у имени; на телефоне — так же. Полный текст сервера —
        подсказкой строки, в подписи линии — причина."""
        r = go("""
const one = () => {
  const sc = scoreOf({});
  const ul = sc.links.find((l) => l.id === "up");
  Object.assign(ul, { fresh_raw: null, rel: null, usual_n: 1, usual_raw: null, dev: null, dev_test: null, hint: null, why: "no_upload_ru",
    why_text: "Не измерено: сервер в России, а точки для замера отдачи нет" });
  return sc;
};
const row = () => SEC.querySelectorAll(".kb-lk__r")[1];
const cells = () => row().children.map((c) => [c.getAttribute("class"), c.getAttribute("aria-colspan"), sp(c.textContent)]);
SCORE = one();
await mount([], { tab: "now" });
const pc = { cells: cells(), tip: sp(row().getAttribute("title")), label: row().querySelector(".gx-cl").getAttribute("aria-label") };
PAGE.leave(); SEC.replaceChildren(); K0.isPhone = () => true; SCORE = one();
await mount([], { tab: "now" });
console.log(JSON.stringify({ pc, ph: cells() }));
""")
        pc = r["pc"]
        self.assertEqual(pc["cells"], [["kb-lk__n", None, "Отдача"], ["kb-lk__c", None, ""],
                                       ["kb-lk__v kb-lk__why", "3", "в России не меряется"]])
        self.assertTrue(pc["tip"].startswith("Не измерено: сервер в России, а точки для замера отдачи нет. "), pc["tip"])
        self.assertEqual(pc["label"], "Отдача: в России не меряется")
        self.assertEqual(r["ph"], pc["cells"])
        self.assertNotIn("—", json.dumps(pc["cells"] + r["ph"], ensure_ascii=False))

    def test_speed_card_from_its_part_under_the_screen_once(self):
        """BC5: карточка «Канал до вас» — из части «speed» (K.loadPart), последней в «Сейчас»: и пока оценка грузится,
        и под готовым экраном; часть грузится один раз, перерисовки и повторный вход берут тот же узел. Часть не
        загрузилась — экран без карточки, без падения; следующий вход пробует снова."""
        r = go("""
const CALLS_PART = [];
let FAIL = true;
let open = null;                                                       // часть приходит, когда тест скажет
LOAD_PART = (name) => { if (name !== "speed") return new Promise(() => {}); CALLS_PART.push(name); return FAIL ? Promise.reject(new Error("net")) : new Promise((ok) => { open = () =>
  ok({ card: () => { const c = el("div", { class: "gx-spd" }, "Канал до вас"); c.mark = CALLS_PART.length; return c; } }); }); };
const err = console.error; const ERRS = []; console.error = (e) => { if (e instanceof Error) ERRS.push(e.message); };
const real = K0.API.host.score;
K0.API.host.score = () => new Promise(() => {});                     // оценка ещё грузится
const look = () => { const now = SEC.querySelector(".gx-now"), st = SEC.querySelector("[data-state]"), last = now && now.childNodes[now.childNodes.length - 1];
  return { spd: SEC.querySelectorAll(".gx-spd").length, last: last ? last.className : null, state: st ? st.getAttribute("data-state") : null,
    text: SEC.textContent.includes("Сравнивать пока не с чем"), node: SEC.querySelector(".gx-spd") }; };
const again = async () => { SEC.replaceChildren(); await mount([], { tab: "now" }); await wait(); };   // раздел заново, как при входе
await again();
const failed = look(); delete failed.node; failed.errs = [...new Set(ERRS)]; failed.tries = CALLS_PART.length;
FAIL = false;
await again();
const before = look().spd;
open(); await wait();                                                  // пришла — экран перерисован сам
const loading = look(); loading.before = before; const node = loading.node; delete loading.node;
SCORE = { ok: true, charts: false };
K0.API.host.score = real;
await again();
const ready = look(); ready.same = ready.node === node; ready.mark = node && node.mark; delete ready.node;
console.error = err;
console.log(JSON.stringify({ failed, loading, ready, calls: CALLS_PART }));
""")
        tries = r["failed"].pop("tries")
        self.assertEqual(r["failed"], {"spd": 0, "last": "", "state": "loading", "text": False, "errs": ["net"]})
        self.assertEqual(r["loading"], {"spd": 1, "last": "gx-spd", "state": "loading", "text": False, "before": 0})
        self.assertEqual(r["ready"], {"spd": 1, "last": "gx-spd", "state": None, "text": True, "same": True, "mark": tries + 1})
        self.assertEqual(r["calls"], ["speed"] * (tries + 1))      # после отказа — снова; после успеха — больше нет

    def test_never_checked(self):
        """Проверок не было (charts: false, ни звеньев, ни summary): «Сравнивать пока не с чем: история 0 из 3 дней»."""
        r = go("""
SCORE = { ok: true, charts: false };
await mount([], { tab: "now" });
console.log(JSON.stringify({ text: SEC.textContent, links: !!SEC.querySelector(".kb-lk"), last: !!btnIn(SEC, "Последняя проверка") }))
""")
        self.assertIn("Сравнивать пока не с чем: история 0 из 3 дней", r["text"])
        self.assertIn("Данных о днях пока нет.", r["text"])
        self.assertFalse(r["links"])
        self.assertFalse(r["last"])

    def test_stale_grey_and_no_fresh_of_the_tests(self):
        r = go("""
SCORE = scoreOf({ stale: true });
await mount([], { tab: "now" });
console.log(JSON.stringify({ rel: relN(), main: relText(), rows: lrows() }));
""")
        self.assertEqual(r["rel"], {"n": "97", "grey": True})
        self.assertIn("давно не проверялся · последняя проверка 29 августа", r["main"])   # З-1: из last_at, не из полосы
        self.assertIn("100 — как обычно у этого сервера · тесты — на 29 августа", r["main"])
        self.assertNotIn("Проверю ещё раз", r["main"])
        self.assertIn("Отдача||не измерено", r["rows"])                  # у тестов свежих нет — причина, без «—»
        self.assertIn("Отклик||38мс|36мс|95", r["rows"])                # пробы идут всегда: свежее остаётся

    def test_marks_of_a_drop_a_confirmed_drop_and_a_pending_day(self):
        """Сценарии макета «разовый провал» и «падение подтвердилось»: резкое падение ждёт повтора (retry_wait), потери
        за сутки ждут подтверждения (day), повтор подтвердил (retry_confirmed) — коротко в строке, полный текст —
        подсказкой; у теста группы — только своя отметка (jitter ▲ не попадает в отклик)."""
        r = go("""
const dip = scoreOf({});
const ul = dip.links.find((l) => l.id === "up"), ll = dip.links.find((l) => l.id === "loss"), q = dip.links.find((l) => l.id === "net_q");
Object.assign(ul, { dev: null, dev_test: null, dev_pending: "down", dev_pending_kind: "retry_wait", hint: { test: "net_up", now: 5, usual: 300, unit: "Мбит/с", days: null } });
Object.assign(ll, { dev_pending: "down", dev_pending_kind: "day", hint: { test: "loss", now: 0.4, usual: 0.05, unit: "%", days: [["2026-10-20", 0.4]] } });
Object.assign(q, { dev: "up", dev_test: "jitter" });
SCORE = dip;
await mount([], { tab: "now" });
const a = { rows: lrows(), tips: ltips(), marks: marks(), wait: SEC.querySelector(".kb-lk").querySelectorAll(".gx-mk--wait").length, up: SEC.querySelector(".kb-lk").querySelectorAll(".gx-mk--up").length };
const conf = clone(dip);
Object.assign(conf.links.find((l) => l.id === "up"), { dev_pending_kind: "retry_confirmed", hint: { test: "net_up", now: 6, usual: 300, unit: "Мбит/с", days: null } });
conf.summary.retry = null;
SCORE = conf; PAGE.leave(); SEC.replaceChildren(); await mount([], { tab: "now" });
console.log(JSON.stringify({ a, b: { rows: lrows(), main: relText(), marks: marks() } }));
""")
        a, b = r["a"], r["b"]
        self.assertEqual(a["rows"][1], "Отдачарезкое падение||120Мбит/с|300Мбит/с|40")
        self.assertEqual(a["tips"][1], "Резкое падение в последней проверке: 5 Мбит/с. Проверю ещё раз около 15:40")
        self.assertEqual(a["rows"][4], "Потериждёт подтверждения||0,06%|0%|—")
        self.assertEqual(a["tips"][4], "Хуже обычного за последние сутки: 0,4%. Отметка ▼ появится, если это повторится в другой день")
        self.assertEqual(a["rows"][3], "Дрожание▲ лучше обычного||1,2мс|1,1мс|92")
        self.assertEqual(a["rows"][2], "Отклик||38мс|36мс|95")       # отметка дрожания — не у отклика
        self.assertEqual((a["wait"], a["up"]), (2, 1))
        self.assertEqual(a["marks"], "Хуже или лучше обычного: дрожание ▲ · Ждёт подтверждения: потери · Ждёт повторной проверки: отдача")
        self.assertEqual(b["rows"][1], "Отдачападение подтвердилось||120Мбит/с|300Мбит/с|40")
        self.assertIn("Повторная проверка подтвердила падение: 6 Мбит/с — день отмечен жёлтым", b["main"])
        self.assertNotIn("Проверю ещё раз", b["main"])
        self.assertIn("Падение подтверждено: отдача", b["marks"])

    def test_the_line_of_a_link_against_usual(self):
        """Линия 30 дней (HANDOFF r1): точки — к «обычно» (равно — середина высоты), выше — лучше: отдача упала —
        ниже середины, отклик вырос вдвое — тоже ниже; серый день — пунктир; коридор p25…p75 — полосой; точка «сейчас»
        справа — цветом подтверждённой отметки; подпись ячейки — звено, сейчас и обычно."""
        r = go_spark("""
await mount([], { tab: "now" });
await wait(10);
const cells = SEC.querySelector(".kb-lk").querySelectorAll(".gx-cl");
const alt = SEC.querySelectorAll(".gx-mct--alt").length;
const pick = (k) => { const s = cells[k], v = s.querySelector("svg"); if (!v) return null;
  const at = (cls) => { const n = v.children.find((c) => (c.getAttribute("class") || "").split(" ").indexOf(cls) >= 0); return n ? n.attrs : null; };
  const pts = []; String((at("gx-cl__pt") || {}).d || "").replace(/M(-?[\\d.]+) (-?[\\d.]+)a/g, (m, x, y) => { pts.push([+x, +y]); return m; });
  return { label: s.getAttribute("aria-label"), band: at("gx-cl__band"), nd: (at("gx-cl__nd") || {}).d, dot: at("gx-cl__dot"), pts, line: (at("gx-cl__l") || {}).d }; };
console.log(JSON.stringify({ n: cells.length, alt, down: pick(0), up: pick(1), rtt: pick(2), enc: pick(5) }));
""")
        self.assertEqual(r["n"], 7)
        self.assertEqual(r["alt"], 0)            # круг 1 Т1: одна таблица «Звеньев» (вариант Б), запасного списка нет
        down, up, rtt, enc = r["down"], r["up"], r["rtt"], r["enc"]
        self.assertEqual(down["label"], "Скачивание: 675 Мбит/с, обычно 690 Мбит/с")
        self.assertEqual(len(down["pts"]), 2)
        self.assertEqual(down["pts"][0][1], 12)                              # 690 = обычно — середина (H 24)
        self.assertTrue(12 < down["pts"][1][1] < 13.5, down["pts"])          # 675 — чуть ниже
        self.assertIsNotNone(down["band"])                                   # коридор p25…p75 есть у звена
        self.assertTrue(float(down["band"]["y"]) < 12 < float(down["band"]["y"]) + float(down["band"]["height"]))
        self.assertTrue(down["nd"].startswith("M11.5 12H18.5"), down["nd"])   # серый день [1] — пунктир по середине
        self.assertEqual(down["dot"]["cx"], "300")
        self.assertEqual(down["dot"]["class"], "gx-cl__dot")
        self.assertTrue(down["line"].startswith("M"), down["line"])
        self.assertEqual(rtt["pts"][0][1], 12)
        self.assertGreater(rtt["pts"][1][1], 18)                            # отклик 72 при обычно 36 — хуже: ниже
        self.assertIsNone(rtt["band"])                                       # у теста группы коридора нет
        self.assertEqual(up["pts"], [])                                      # точек нет — только «сейчас»
        self.assertEqual(up["dot"]["class"], "gx-cl__dot gx-cl__dot--dn")    # подтверждённая ▼
        self.assertIn("H300", enc["nd"])                                     # тест давно не запускался — пунктир до края

    def test_the_tip_of_a_day_hover_pin_unpin_and_the_figures(self):
        r = go("""
await mount([], { tab: "now" });
const ds = dayBtns(), tip = tipBox();
ds[26].dispatch("mouseenter"); await wait();
const hover = nb(tip.textContent), hidden1 = tip.hidden, hoverBtns = tip.querySelectorAll("button").length;
ds[26].dispatch("mouseleave"); await wait(4);
const hidden2 = tip.hidden;
ds[29].click(); await wait();
ds[29].dispatch("mouseleave"); await wait(4);
const pinned = !tip.hidden, pressed = ds[29].getAttribute("aria-pressed"), close = !!btnIn(tip, "Закрыть");
const pinText = nb(tip.textContent), cls = tip.getAttribute("class");
ds[29].click(); await wait();
const unpinned = tip.hidden, pressed2 = ds[29].getAttribute("aria-pressed");
ds[1].click(); await wait();
const grey = tip.textContent, greyHist = !!btnIn(tip, "Открыть в истории");
ds[27].click(); await wait();
const even = nb(tip.textContent);
btnIn(tip, "Закрыть").click(); await wait();
console.log(JSON.stringify({ hover, hidden1, hoverBtns, hidden2, pinned, pressed, close, pinText, cls, unpinned, pressed2, grey, greyHist, even, closed: tip.hidden }));
""")
        self.assertIn("Отдача хуже обычного: 120 Мбит/с, обычно 300", r["hover"])
        self.assertIn("17 октября, суббота", r["hover"])
        self.assertIn("Потери0,41% · пик 3,1%", r["hover"])
        self.assertIn("Без сети12 мин", r["hover"])
        self.assertFalse(r["hidden1"])
        self.assertEqual(r["hoverBtns"], 0)                # наведение — только смотреть
        self.assertTrue(r["hidden2"])                      # уход курсора — подсказка закрыта
        self.assertTrue(r["pinned"])                       # нажатие — закреплена
        self.assertEqual(r["pressed"], "true")
        self.assertTrue(r["close"])
        self.assertIn("is-pin", r["cls"])
        self.assertIn("gx-daytip--r", r["cls"])            # у правого края — к правому краю
        for s in ("Весь день ровно", "Отклик8,2 мс · худшие 10%: 14 мс", "Дрожание1,1 мс",
                  "Полная 04:10скачивание 760 · отдача 495 Мбит/с", "Быстрая 12:30не завершилась", "Открыть в истории"):
            self.assertIn(s, r["pinText"])
        self.assertNotIn("балл", r["pinText"])             # в подсказке дня балла нет (NS-D34)
        self.assertTrue(r["unpinned"])                     # повторное нажатие — снять
        self.assertEqual(r["pressed2"], "false")
        self.assertIn("Нет данных", r["grey"])
        self.assertFalse(r["greyHist"])                    # проверки не было — в историю не ведёт
        self.assertIn("Быстрая 13:00пропущена: сервер был занят", r["even"])
        self.assertTrue(r["closed"])

    def test_open_in_history_goes_to_the_day(self):
        r = go("""
await mount([], { tab: "now" });
dayBtns()[29].click(); await wait();
btnIn(tipBox(), "Открыть в истории").click(); await wait();
console.log(JSON.stringify({ navs: NAVS, hidden: tipBox().hidden }));
""")
        self.assertEqual(r["navs"], [["diag", {"tab": "history", "focus": "2026-10-20"}]])
        self.assertTrue(r["hidden"])

    def test_pinned_tip_takes_the_focus_and_esc_from_it_returns_to_the_day(self):
        """REV-ns-p4d П-2/П-3: закрепление переводит фокус в подсказку («Открыть в истории»); Esc из самой подсказки
        закрывает её и возвращает фокус на день — без новой подсказки по этому фокусу."""
        r = go(FOCUS + """
await mount([], { tab: "now" });
const ds = dayBtns(), tip = tipBox();
ds[29].focus(); await wait();
ds[29].click(); await wait();
const a = document.activeElement, inTip = tip.contains(a), label = a ? a.textContent.trim() : "";
tip.ls.keydown[0]({ key: "Escape", preventDefault() {} }); await wait(4);
console.log(JSON.stringify({ inTip, label, closed: tip.hidden, back: document.activeElement === ds[29] }));
""")
        self.assertTrue(r["inTip"])
        self.assertEqual(r["label"], "Открыть в истории")
        self.assertTrue(r["closed"])
        self.assertTrue(r["back"])

    def test_keyboard_focus_shows_the_tip_and_esc_closes(self):
        """REV-ns-p4b П-1: фокус на дне — подсказка, уход фокуса — закрыть; Esc закрывает и закреплённую."""
        r = go("""
await mount([], { tab: "now" });
const ds = dayBtns(), tip = tipBox();
ds[26].dispatch("focus"); await wait();
const shown = !tip.hidden, text = tip.textContent;
ds[26].dispatch("blur"); await wait(4);
const gone = tip.hidden;
ds[29].click(); await wait();
const pinned = !tip.hidden;
ds[29].ls.keydown[0]({ key: "Escape", preventDefault() {} }); await wait();
console.log(JSON.stringify({ shown, text, gone, pinned, closed: tip.hidden }));
""")
        self.assertTrue(r["shown"])
        self.assertIn("Отдача хуже обычного", r["text"])
        self.assertTrue(r["gone"])
        self.assertTrue(r["pinned"])
        self.assertTrue(r["closed"])

    def test_phone_list_instead_of_the_table(self):
        """Телефон: те же строки «Звеньев» (круг 1 Т1, вариант Б: раскладку меняет ovb.css), без легенды;
        наведение не показывает подсказку дня, нажатие — показывает (во всю ширину, без смещения)."""
        r = go("""
K0.isPhone = () => true;
await mount([], { tab: "now" });
const ds = dayBtns(), tip = tipBox();
ds[26].dispatch("mouseenter"); await wait();
const hover = !tip.hidden;
ds[26].click(); await wait();
console.log(JSON.stringify({ table: !!SEC.querySelector(".kb-lk"), rows: SEC.querySelectorAll(".kb-lk__r").map((x) => sp(x.textContent)),
  legend: !!SEC.querySelector(".gx-legend"), ax: SEC.querySelector(".gx-days__ax").children.length, hover, shown: !tip.hidden, cls: tip.getAttribute("class") }));
""")
        self.assertTrue(r["table"])
        self.assertEqual(len(r["rows"]), 7)
        self.assertEqual(r["rows"][1], "Отдача▼ хуже обычного120Мбит/с300Мбит/с40")
        self.assertFalse(r["legend"])
        self.assertEqual(r["ax"], 2)
        self.assertFalse(r["hover"])
        self.assertTrue(r["shown"])
        self.assertEqual(r["cls"], "gx-daytip is-pin")

    def test_the_last_check_opens_and_leave_folds_it(self):
        """«Последняя проверка» — раскрывается (её строки нет в списке — читается GET /api/host/bench/<id>); уход из
        раздела сворачивает (REV-ns-p4b П-3)."""
        r = go("""
RUN.r1 = { id: "r1", ts: Date.UTC(2026, 9, 20, 1, 10) / 1000, mode: "quick", by: "schedule", status: "ok", tests: [], dur_s: 300 };
let READ = 0;
const run0 = API.run; API.run = (id) => { READ += 1; return run0(id); };
await mount([], { tab: "now" });
const b = btnIn(SEC, "Последняя проверка"), e1 = b.getAttribute("aria-expanded");
b.click(); await wait(10);
const box = SEC.querySelector(".gx-chklast"), text = box ? box.textContent : "", e2 = btnIn(SEC, "Последняя проверка").getAttribute("aria-expanded");
PAGE.leave(); SEC.replaceChildren(); await mount([], { tab: "now" });
console.log(JSON.stringify({ e1, e2, text, read: READ, after: btnIn(SEC, "Последняя проверка").getAttribute("aria-expanded"), box2: !!SEC.querySelector(".gx-chklast") }));
""")
        self.assertEqual((r["e1"], r["e2"], r["after"]), ("false", "true", "false"))
        self.assertIn("20 октября, 04:10 · быстрая · по расписанию", r["text"])
        self.assertEqual(r["read"], 1)
        self.assertFalse(r["box2"])

    def test_share_is_live_and_the_next_check(self):
        """«Поделиться» (C10) — живая кнопка у «в целом» (подсказка — что в ролике); нажатие грузит pages/diag-share.js
        и открывает окно с «Проверить сейчас» раздела; расписание выключено — так и сказано; включено и повтора нет —
        «Следующая — <день> ≈ <время>»."""
        r = go("""
const SCRIPTS = [], OPENED = [];
window.KaskadPanel.loadScript = (n) => { SCRIPTS.push(n); return Promise.resolve(); };
window.KaskadShare = { open: (K, o) => { OPENED.push(K === window.KaskadPanel && typeof o.onCheck === "function"); } };
await mount([], { tab: "now" });
const sh = btnIn(SEC, "Поделиться"), off = relText();
sh.click(); await wait(10);
SCORE = scoreOf({}); SCORE.summary.retry = null;
const sched0 = API.schedule;
API.schedule = () => sched0().then((d) => Object.assign(d, { next: [{ key: "k", id: "a1", mode: "quick", ts: Math.floor(Date.now() / 1000) + 3 * 86400 }] }));
PAGE.leave(); SEC.replaceChildren(); await mount([E("a1", "04:10")], { tab: "now" });
console.log(JSON.stringify({ dis: sh.disabled, tip: sh.getAttribute("aria-label"), scripts: SCRIPTS, opened: OPENED, off, on: relText() }));
""")
        self.assertFalse(r["dis"])
        self.assertEqual(r["tip"], "Ролик на 15 секунд или картинка: цифры последней проверки без IP, домена и имени сервера")
        self.assertEqual((r["scripts"], r["opened"]), (["pages/diag-share.js"], [True]))
        self.assertIn("Расписание выключено — Настройки › Проверка сервера", r["off"])
        self.assertRegex(r["on"], r"Следующая — \d+ [а-я]+ ≈ \d\d:\d\d")
        self.assertNotIn("Расписание выключено", r["on"])


@unittest.skipUnless(HAS_NODE, "no node")
class CostWindowTest(unittest.TestCase):
    def test_the_tick_of_the_programs_and_the_start_with_the_key(self):
        r = go("""
await mount([], { tab: "now" });
const d = await openCostWin();
const asked = { posts: clone(POSTS), reads: READS.length };
const go1 = btnIn(foot(d), "Проверить").disabled;
const why = d.box.textContent;
btnIn(d.box, "Разрешаю поставить sysbench, fio").click(); await wait();
const go2 = btnIn(foot(d), "Проверить").disabled;
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ asked, go1, go2, why, last: POSTS[POSTS.length - 1], open: d.open, toast: TOASTS.map((x) => x[0]), text: d.box.textContent }));
""")
        self.assertEqual(r["asked"], {"posts": [], "reads": 1})     # цена обоих видов — одно чтение, без POST (Н-15)
        self.assertTrue(r["go1"])                        # без галочки «Проверить» выключена
        self.assertIn("Отметьте согласие, чтобы начать", r["why"])
        self.assertFalse(r["go2"])
        self.assertEqual(r["last"], {"mode": "quick", "confirm": "0a0a0a0a", "install": True})
        self.assertFalse(r["open"])
        self.assertEqual(r["toast"], ["Проверка началась"])

    def test_the_upload_in_russia_tick_on_by_default(self):
        """D-144 (владелец 29.10): у полной проверки сервера, который может быть в России (``upload_ru`` цены), —
        галочка «Замерить отдачу …» со строкой «точка увидит адрес», включена: в теле запуска ``iperf: true``; снятая —
        ``iperf: false``; у быстрой галочки нет и ключа нет; без ``upload_ru`` (сервер за рубежом) — ни галочки, ни ключа."""
        r = go("""
PKGS = NOPK; UPQ = { text: "?", choices: [[1, "Замерить отдачу"], [2, "Не замерять отдачу"]], default: 1 };
await mount([], { tab: "now" });
let d = await openCostWin();
const quick = d.box.textContent.indexOf("Замерить отдачу до серверов") >= 0;
startsWith(d.box, "Полная").click(); await wait();
const full = d.box.textContent;
btnIn(foot(d), "Проверить").click(); await wait(10);
const on = POSTS[POSTS.length - 1];
d = await openCostWin();
startsWith(d.box, "Полная").click(); await wait();
startsWith(d.box, "Замерить отдачу до серверов").click(); await wait();
btnIn(foot(d), "Проверить").click(); await wait(10);
const off = POSTS[POSTS.length - 1];
d = await openCostWin();
btnIn(foot(d), "Проверить").click(); await wait(10);
const q = POSTS[POSTS.length - 1];
UPQ = null;
d = await openCostWin();
startsWith(d.box, "Полная").click(); await wait();
const abroad = d.box.textContent.indexOf("Замерить отдачу до серверов") >= 0;
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ quick, full, on, off, q, abroad, ab: POSTS[POSTS.length - 1] }));
""")
        self.assertFalse(r["quick"])
        self.assertIn("Замерить отдачу до серверов замера скорости российских провайдеров (iperf3)", r["full"])
        for lang in ("ru", "en"):                        # строка «точка увидит адрес» — описание галочки (desc)
            with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib/kaskad/panel/static/pages/diag-i18n-%s.js" % lang), encoding="utf-8") as f:
                self.assertRegex(f.read(), r'"dg\.upRuX": "[^"]*(увидит адрес этого сервера|will see the address of this server)')
        self.assertEqual(r["on"], {"mode": "full", "confirm": "f0f0f0f0", "iperf": True})     # без правки: без iperf
        self.assertEqual(r["off"], {"mode": "full", "confirm": "f0f0f0f0", "iperf": False})
        self.assertEqual(r["q"], {"mode": "quick", "confirm": "0a0a0a0a"})
        self.assertFalse(r["abroad"])
        self.assertEqual(r["ab"], {"mode": "full", "confirm": "f0f0f0f0"})

    def test_iperf3_only_with_the_upload_tick(self):
        """С2 REV-score-rename: iperf3 — только в наборе полной проверки с галочкой отдачи (``upload_ru.pkgs``): у
        быстрой и со снятой галочкой нет ни вопроса о программах, ни ``install``; без одного iperf3 предупреждение — об
        отдаче, а не «по расписанию не начнётся»; согласие на набор снимается, когда набор меняется галочкой."""
        r = go("""
PKGS = NOPK; UPQ = { text: "?", choices: [[1, "Замерить отдачу"], [2, "Не замерять отдачу"]], default: 1,
  pkgs: { missing: ["iperf3"], available: true, install: ["iperf3"], kib: 330, question: "?" } };
await mount([], { tab: "now" });
let d = await openCostWin();
const quick = d.box.textContent;
btnIn(foot(d), "Проверить").click(); await wait(10);
const q = POSTS[POSTS.length - 1];
d = await openCostWin();
startsWith(d.box, "Полная").click(); await wait();
const full = d.box.textContent;
btnIn(d.box, "Разрешаю поставить iperf3").click(); await wait();
startsWith(d.box, "Замерить отдачу до серверов").click(); await wait();
const off = d.box.textContent;
btnIn(foot(d), "Проверить").click(); await wait(10);
const offb = POSTS[POSTS.length - 1];
d = await openCostWin();
startsWith(d.box, "Полная").click(); await wait();
btnIn(d.box, "Разрешаю поставить iperf3").click(); await wait();
startsWith(d.box, "Замерить отдачу до серверов").click(); await wait();
startsWith(d.box, "Замерить отдачу до серверов").click(); await wait();
const again = !!btnIn(d.box, "Разрешаю поставить iperf3") && btnIn(foot(d), "Проверить").disabled;
btnIn(d.box, "Разрешаю поставить iperf3").click(); await wait();
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ quick, q, full, off, offb, again, on: POSTS[POSTS.length - 1] }));
""")
        self.assertNotIn("iperf3", r["quick"])                                   # без правки: просьба поставить iperf3
        self.assertNotIn("по расписанию", r["quick"])
        self.assertEqual(r["q"], {"mode": "quick", "confirm": "0a0a0a0a"})
        self.assertIn("Разрешаю поставить iperf3", r["full"])
        self.assertIn("Без iperf3 отдача до серверов российских провайдеров не будет измерена.", r["full"])
        self.assertNotIn("Без программ проверка по расписанию не начнётся.", r["full"])
        self.assertNotIn("Разрешаю поставить", r["off"])
        self.assertEqual(r["offb"], {"mode": "full", "confirm": "f0f0f0f0", "iperf": False})
        self.assertTrue(r["again"])                                              # набор сменился — согласие снято
        self.assertEqual(r["on"], {"mode": "full", "confirm": "f0f0f0f0", "install": True, "iperf": True})

    def test_without_programs_install_false_and_the_warning(self):
        r = go("""
await mount([], { tab: "now" });
const d = await openCostWin();
const text = d.box.textContent;
startsWith(d.box, "Полная").click(); await wait();
btnIn(d.box, "Проверить без программ").click(); await wait(10);
console.log(JSON.stringify({ text, last: POSTS[POSTS.length - 1] }));
""")
        self.assertIn("Без программ проверка по расписанию не начнётся.", r["text"])
        self.assertEqual(r["last"], {"mode": "full", "confirm": "f0f0f0f0", "install": False})

    def test_need_install_asks_the_tick(self):
        r = go("""
PKGS = NOPK; NEED_INSTALL_ONCE = true;
await mount([], { tab: "now" });
const d = await openCostWin();
const before = !!btnIn(d.box, "Разрешаю поставить sysbench, fio");
btnIn(foot(d), "Проверить").click(); await wait(10);
const text = d.box.textContent, dis = btnIn(foot(d), "Проверить").disabled;
btnIn(d.box, "Разрешаю поставить sysbench, fio").click(); await wait();
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ before, text, dis, bodies: POSTS, open: d.open }));
""")
        self.assertFalse(r["before"])
        self.assertIn("Нужно ваше решение о программах.", r["text"])
        self.assertIn("Сервер: need_install", r["text"])
        self.assertTrue(r["dis"])
        self.assertEqual(r["bodies"], [{"mode": "quick", "confirm": "0a0a0a0a"},
                                       {"mode": "quick", "confirm": "0a0a0a0a", "install": True}])
        self.assertFalse(r["open"])

    def test_running_and_limits_are_said(self):
        r = go("""
RUNNING = true;
await mount([], { tab: "now" });
const d1 = await openCostWin();
const run1 = { text: d1.box.textContent, n: POSTS.length, reads: READS.length, dis: btnIn(foot(d1), "Проверить").disabled };
d1.close();
RUNNING = false; LIMIT = "full"; PKGS = NOPK; POSTS.length = 0;
const d2 = await openCostWin();
const fullDis = startsWith(d2.box, "Полная").disabled, quickDis = startsWith(d2.box, "Быстрая").disabled;
const text2 = d2.box.textContent;
LIMIT = "all";
btnIn(foot(d2), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ run1, fullDis, quickDis, text2, text3: d2.box.textContent, open: d2.open }));
""")
        self.assertIn("Уже идёт проверка.", r["run1"]["text"])
        self.assertEqual([r["run1"]["n"], r["run1"]["reads"]], [0, 1])     # одно чтение, запуска нет
        self.assertTrue(r["run1"]["dis"])
        self.assertTrue(r["fullDis"])
        self.assertFalse(r["quickDis"])
        self.assertIn("на сегодня предел проверок", r["text2"])
        self.assertIn("Сервер: limit", r["text3"])
        self.assertIn("Сегодня из панели: 6, полных — 2.", r["text3"])
        self.assertTrue(r["open"])

    def test_one_post_while_the_request_goes(self):
        r = go("""
PKGS = NOPK; HOLD = true;
await mount([], { tab: "now" });
const d = await openCostWin();
const b = btnIn(foot(d), "Проверить");
b.click(); b.click(); await wait();
console.log(JSON.stringify({ n: POSTS.length, dis: btnIn(foot(d), "Проверить").disabled }));
""")
        self.assertEqual(r["n"], 1)                      # цена — чтением, один запуск
        self.assertTrue(r["dis"])


    def test_another_set_of_programs_drops_the_tick(self):
        """REV-ns-p4b З-2: галочка дана на «sysbench»; сервер ответил confirm_changed с другим набором — галочка снята,
        «Проверить» выключена, без новой галочки install: true не уходит."""
        r = go("""
PKGS = { missing: ["sysbench"], available: true, install: ["sysbench"], kib: 900, question: "?" };
await mount([], { tab: "now" });
const d = await openCostWin();
btnIn(d.box, "Разрешаю поставить sysbench").click(); await wait();
const on1 = btnIn(d.box, "Разрешаю поставить sysbench").getAttribute("aria-checked");
PRICE.quick = Object.assign({}, PRICE.quick, { confirm: "1b1b1b1b" }); PKGS = PK;
btnIn(foot(d), "Проверить").click(); await wait(10);
const tick = btnIn(d.box, "Разрешаю поставить sysbench, fio");
const n = POSTS.length;
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ on1, on2: tick.getAttribute("aria-checked"), dis: btnIn(foot(d), "Проверить").disabled, sent: POSTS.length - n,
  text: d.box.textContent }));
""")
        self.assertEqual(r["on1"], "true")
        self.assertEqual(r["on2"], "false")
        self.assertTrue(r["dis"])
        self.assertEqual(r["sent"], 0)
        self.assertIn("Отметьте согласие, чтобы начать", r["text"])

    def test_the_same_set_keeps_the_tick(self):
        """З-2: цена изменилась, набор тот же — галочка остаётся (человек согласился именно на эти программы)."""
        r = go("""
await mount([], { tab: "now" });
const d = await openCostWin();
btnIn(d.box, "Разрешаю поставить sysbench, fio").click(); await wait();
PRICE.quick = Object.assign({}, PRICE.quick, { confirm: "1b1b1b1b" }); PKGS = { missing: ["fio", "sysbench"], available: true, install: [], kib: 2380 };
btnIn(foot(d), "Проверить").click(); await wait(10);
console.log(JSON.stringify({ on: btnIn(d.box, "Разрешаю поставить fio, sysbench").getAttribute("aria-checked") }));
""")
        self.assertEqual(r["on"], "true")

    def test_keyboard_focus_stays_in_the_window(self):
        """REV-ns-p4b З-3: Пробел на галочке перерисовывает окно — фокус на новой галочке, а не вне окна;
        выбор вида — на той же карточке."""
        r = go("""
FakeNode.prototype.focus = function () { document.activeElement = this; };
await mount([], { tab: "now" });
const d = await openCostWin();
const tick = () => btnIn(d.box, "Разрешаю поставить sysbench, fio");
tick().focus(); tick().click(); await wait();
const a1 = document.activeElement, same1 = a1 === tick(), live1 = d.box.contains(a1);
const full = () => startsWith(d.box, "Полная");
full().focus(); full().click(); await wait();
const a2 = document.activeElement;
console.log(JSON.stringify({ same1, live1, same2: a2 === full(), checked: full().getAttribute("aria-checked") }));
""")
        self.assertTrue(r["same1"])
        self.assertTrue(r["live1"])
        self.assertTrue(r["same2"])
        self.assertEqual(r["checked"], "true")

@unittest.skipUnless(HAS_NODE, "no node")
class SchedulePlaqueTest(unittest.TestCase):
    def test_buttons_of_the_plaque_and_max_per_day(self):
        r = go("""
const COST = { week: { quick: 7, full: 2 }, day: { rx_mb: 1, tx_mb: 1, cpu_min: 1 }, month: { rx_mb: 1, tx_mb: 1, cpu_min: 1 }, basis: "caps", max_per_day: 3, per_day_even: false, notes: [] };
let WHY = "no_pkgs", EVEN = false;
const sched0 = API.schedule;
API.schedule = () => sched0().then((d) => Object.assign(d, { armed: false, armed_why: WHY, cost: Object.assign({}, COST, { per_day_even: EVEN }) }));
await mount([E("a1", "02:00")], { tab: "schedule" });
const sumText = () => SEC.querySelectorAll(".gx-sched__sum").slice(-1)[0].textContent;
const t1 = sumText();
btnIn(SEC, "Установить и проверить").click(); await wait(10);
const win1 = lastOpen().o.title;
lastOpen().close();
WHY = "no_manual"; EVEN = true;
await mount([E("a1", "02:00")], { tab: "schedule" });
const t2 = sumText();
btnIn(SEC, "Проверить сейчас").click(); await wait(10);
console.log(JSON.stringify({ t1, t2, win1, win2: lastOpen().o.title }));
""")
        self.assertIn("до 3 проверки в день", r["t1"])   # подделка tn берёт первую форму
        self.assertEqual(r["win1"], "Проверить сервер")
        self.assertNotIn("до 3", r["t2"])
        self.assertIn("3 проверка в день", r["t2"])
        self.assertEqual(r["win2"], "Проверить сервер")


if __name__ == "__main__":
    unittest.main()
