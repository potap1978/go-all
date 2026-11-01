// Круг 1 Т1 (вариант Б «Обзора»): чистые помощники части static/pages/ovb.js — пояса итога, вердикт Сводки, полоса
// 30 дней, трафик ведрами, засечки оси, линия выхода за час, «что изменилось», «почему итог не выше», геометрия радара.
// Офлайн, только node. Запуск: node tests/panel/test_ovb.mjs -> «OVB OK: N …»; обёртка — tests/panel/test_panel_ovb.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../lib/kaskad/panel/static");
const box = { window: {}, console, document: {}, Intl, Date, Math };
let part = null;
box.window.KaskadPanel = { el: () => null, t: (k) => k, tn: (k) => k, U: {}, lang: "ru",
  part: (name, def) => { part = { name, def }; return true; } };
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "pages/ovb.js"), "utf8"), box, { filename: "pages/ovb.js" });

let n = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const plain = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const eq = (a, b, m) => { n++; assert.deepStrictEqual(plain(a), plain(b), m || "eq"); };
const ok = (v, m) => { n++; assert.ok(v, m); };
const P = () => part.def.pure;

test("the part registers itself as «ovb» with the screens and the pure helpers", () => {
  eq(part.name, "ovb");
  eq(["build", "summary", "health", "openScore", "scoreCard", "ver"].map((k) => typeof part.def[k]), Array(6).fill("function"));
  eq(typeof part.def.pure, "object");
});

test("bandOf: 0–99 · 100–299 · 300–599 · 600–799 · 800+ (RESEARCH «Главное» п. 10); no number — -1", () => {
  const f = P().bandOf;
  eq([0, 99, 100, 299, 300, 599, 600, 799, 800, 999].map(f), [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]);
  eq([null, undefined, "700", NaN, Infinity].map(f), [-1, -1, -1, -1, -1]);
});

test("verdictOf (владелец 28.10: «замечания не пишем»): состояние словами без числа — норма / предупреждения / требует внимания", () => {
  const f = P().verdictOf;
  eq(f([], "ok"), { key: "ok", tone: "ok" });
  eq(f([], "wait"), { key: "wait", tone: "wait" });
  eq(f([{ tone: "warning" }], "ok"), { key: "warn", tone: "warn" });
  eq(f([], "warn"), { key: "warn", tone: "warn" });                       // жёлтое здоровье без строк — «есть предупреждения»
  eq(f([{ tone: "warning" }, { tone: "accent" }], "warn"), { key: "warn", tone: "warn" });
  eq(f([{ tone: "warning" }, { tone: "danger" }], "ok"), { key: "bad", tone: "crit" });
  eq(f([], "crit"), { key: "bad", tone: "crit" });                        // красное здоровье без строк — всё равно «Требует внимания»
});

test("verdictOf (REV-it1 №3): выключенный шлюз — не «работает»; без данных о здоровье — не «нормально»", () => {
  const f = P().verdictOf;
  eq(f([{ key: "gwoff", tone: "accent" }], "ok"), { key: "off", tone: "none" });         // было «Работает, есть предупреждения»
  eq(f([{ key: "gwoff", tone: "accent" }, { key: "w:x", tone: "warning" }], "warn"), { key: "off", tone: "none" });
  eq(f([{ key: "gwoff", tone: "accent" }, { key: "err", tone: "danger" }], "ok"), { key: "bad", tone: "crit" });   // красное — важнее
  eq(f([], "off"), { key: "nohc", tone: "none" });                                          // было «Работает нормально»
  eq(f([{ key: "rf", tone: "warning" }], "off"), { key: "warn", tone: "warn" });
  for (const lv of [undefined, null, "", "na", "zzz"]) eq(f([], lv), { key: "wait", tone: "wait" });   // неизвестный уровень — не «нормально»
  for (const lv of ["ok", "warn", "crit", "wait", "off"]) for (const rows of [[], [{ key: "gwoff", tone: "accent" }]]) ok(!(f(rows, lv).key === "ok" && (lv !== "ok" || rows.length)), lv);
});

test("d30Of: the last 30 good days, unknown colours count as «no data»", () => {
  const days = [];
  for (let i = 0; i < 33; i++) days.push({ day: "2026-10-" + String(i % 28 + 1).padStart(2, "0"), color: i === 32 ? "purple" : i === 31 ? "red" : i % 3 ? "green" : "yellow" });
  days.push({ day: "bad" }, null, "x");
  const r = P().d30Of(days);
  eq(r.list.length, 30);
  eq(r.cnt.green + r.cnt.yellow + r.cnt.red + r.cnt.grey, 30);
  eq([r.cnt.red, r.cnt.grey], [1, 1]);
  eq(P().d30Of(null), { list: [], cnt: { green: 0, yellow: 0, red: 0, grey: 0 } });
});

test("trafficOf: sums in buckets of 10 min (day) or an hour (7 days) -> Mbit/s, totals in bytes, the peak and its time", () => {
  const h = { cols: ["busy", "rx", "tx"], points: [[600, 1, 30e6, 1e6], [900, 2, 45e6, 2e6], [1200, 3, 15e6, null], [1500, 4, "x", 5e6], ["bad"], null] };
  const d = P().trafficOf(h, false);
  eq(d.ts, [600, 1200]);
  eq(d.rx, [1, 0.2]);                                                  // (30 + 45) МБ × 8 / 600 с = 1 Мбит/с
  eq([d.R, d.T, d.pk, d.pkAt, d.step], [90e6, 8e6, 1, 600, 600]);
  const w = P().trafficOf(h, true);
  eq([w.ts, w.step, w.R], [[0], 3600, 90e6]);
  eq(P().trafficOf({ cols: "x" }, false).ts, []);
});

test("xnPts: a log scale 10–500 ms in 96 × 22; no value or 0 — a gap", () => {
  const p = P().xnPts([10, null, 500, 0, 5000], 96, 22);
  eq(p[0], [0, 21]);
  eq(p[1], null);
  eq(p[2][1], 1);
  eq(p[3], null);
  eq(p[4][1], 1);                                                      // выше шкалы — по верху
});

test("deltaOf: links of two checks by the size of the change, pairs with a gap left out", () => {
  const a = { links: { net_bw: 800, net_q: 700, cpu: null, stab: 900, down: 810, up: 500, loss: 880, crypto: 790 } };
  const b = { links: { net_bw: 790, net_q: 720, cpu: 700, stab: 900, down: 815, up: 610, loss: 880, crypto: null } };
  eq(P().deltaOf(a, b).map((x) => [x.id, x.d]), [["up", -110], ["net_q", -20], ["net_bw", 10], ["down", -5], ["stab", 0], ["loss", 0]]);
  eq(P().deltaOf(a, null), []);
});

test("dlName: «what changed» rows start with a capital letter for every link, RU and EN (М-3 3.3.7.13)", () => {
  // своя загрузка части со словарём языка: у общей t() — ключи, по ним регистр не проверить
  for (const lang of ["ru", "en"]) {
    const w = {};
    vm.runInNewContext(fs.readFileSync(path.join(STATIC, `pages/ovb-i18n-${lang}.js`), "utf8"), { window: w }, { filename: `ovb-i18n-${lang}.js` });
    const dict = Object.assign({}, (w.KASKAD_I18N_ADD || {}).ovb, (w.KASKAD_I18N_ADD_EN || {}).ovb);
    let def = null;
    const b = { window: {}, console, document: {}, Intl, Date, Math };
    b.window.KaskadPanel = { el: () => null, t: (k) => dict[k] || k, tn: (k) => k, U: {}, lang,
      part: (name, d) => { def = d; return true; } };
    vm.runInNewContext(fs.readFileSync(path.join(STATIC, "pages/ovb.js"), "utf8"), b, { filename: "pages/ovb.js" });
    const ids = ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"];
    ids.forEach((id) => ok(dict["ovb.ln." + id], lang + " " + id));
    const names = ids.map((id) => def.pure.dlName(id));
    eq(names.filter((s) => s.charAt(0) !== s.charAt(0).toLocaleUpperCase(lang)), [], lang);
    eq(names.map((s) => s.toLocaleLowerCase(lang)), ids.map((id) => dict["ovb.ln." + id].toLocaleLowerCase(lang)), lang);
  }
});

test("whyOf (D-143): the block only when the limit of the weak link holds the total (lim); else free; no total — incomplete or none", () => {
  const f = P().whyOf;
  const syn = { fresh: { total: 346, raw: 790, cap: 346, weak: "up", limited: true }, links: [{ id: "up", fresh: 77 }] };
  eq(f(syn), { key: "lim", id: "up", v: 77, cap: 346, raw: 790 });
  // слабое звено есть, но предел выше итога — блока нет (владелец 29.10: «только когда предел действительно держит итог»)
  eq(f({ fresh: { total: 791, raw: 805, cap: 862, weak: "up", limited: false }, links: [{ id: "up", fresh: 512 }] }), { key: "free" });
  // слабого звена нет (не ниже среднего больше чем на 30 %) — нет и предела
  eq(f({ fresh: { total: 832, raw: 832, cap: null, weak: null, limited: false }, links: [{ id: "net_q", fresh: 766 }] }), { key: "free" });
  eq(f({ fresh: { total: 346, raw: 790, cap: 346, weak: "up", limited: "yes" }, links: [{ id: "up", fresh: 77 }] }), { key: "free" });   // только true
  eq(f({ fresh: { total: 346, raw: 790, cap: 346, weak: "up", limited: true }, links: [] }), { key: "free" });                       // нет звена — нечего сказать
  eq(f({ fresh: { total: null, why: "incomplete" }, links: [] }), { key: "incomplete" });
  eq(f({ fresh: { total: null, why: "nothing_measured" } }), { key: "none" });
  eq(f(null), { key: "none" });
});

test("mono and segs: an empty series — no path, one point — a dot, gaps split the line", () => {
  eq(P().mono([]), "");
  eq(P().mono([[1, 2]]), "M1 2h.01");
  ok(/^M0 10C/.test(P().mono([[0, 10], [1, 5], [2, 7]])));
  eq(P().segs([[0, 1], null, [2, 3], [3, 4], null]), [[[0, 1]], [[2, 3], [3, 4]]]);
});

test("geoOf: 1000 of the first axis is at the top of the circle, 0 — in the centre, 8 axes evenly", () => {
  const g = P().geoOf("drawer", 400, 8);
  eq(g.pt(1000, 0), [g.cx, g.cy - g.r]);
  eq(g.pt(0, 3), [g.cx, g.cy]);
  eq(g.pt(2000, 0), g.pt(1000, 0));                                    // выше 1000 — по краю
  eq(g.ang(2), 90);
});

test("стенд it1 Н-1: polyOf — звено без данных не точка в центре: многоугольник по измеренным, соседей соединяет хорда", () => {
  const { polyOf, geoOf, ndPt } = P(), g = geoOf("diag", 560, 8);
  const v = [712, 830, 900, 870, 820, null, 860, 840];                 // «Отдача» (up) — fresh=null, как на стенде
  const pts = polyOf(g, v).split(" ");
  eq(pts.length, 7);                                                   // было 8: null → 0 → точка в центре
  ok(pts.indexOf(g.cx + "," + g.cy) < 0, "нет точки в центре");
  eq(pts[4], g.pt(820, 4).join(",")); eq(pts[5], g.pt(860, 6).join(","));   // «Загрузка» → «Потери» — хорда
  eq(polyOf(g, [null, null]), "");
  eq(polyOf(g, [500, 0]), g.pt(500, 0).join(",") + " " + g.pt(0, 1).join(","));   // настоящий 0 — по-прежнему 0
  // отметка «нет данных» — на оси «Отдачи», где её пересекает хорда (не в центре, не на краю)
  const m = ndPt(g, v, 5), d = Math.hypot(m[0] - g.cx, m[1] - g.cy), a = (g.ang(5) * Math.PI) / 180;
  ok(d > g.r * 0.5 && d < g.r * 0.85, "на хорде: " + d / g.r);
  ok(Math.abs((m[0] - g.cx) * Math.cos(a) + (m[1] - g.cy) * Math.sin(a)) < 0.2, "на своей оси");
  eq(ndPt(g, [null, 500, null, null, null, null, null, null], 0), [g.cx, g.cy]);   // одна точка — центр
  eq(ndPt(g, [null, null, null, null, null, null, null, null], 0), [g.cx, g.cy]);
  const half = ndPt(g, [900, null, null, null, null, null, null, null], 4);        // по ту сторону центра — центр
  eq(half, [g.cx, g.cy]);
});

test("стенд it1 Н-2: xnJoin — пропуск 1–2 мин и минуты «работает» без замера не рвут линию; «не работает» и долгая тишина — рвут", () => {
  const { xnJoin, xnPts, segs } = P();
  const n = (rtt, st, loss) => segs(xnJoin(xnPts(rtt, 96, 22), st, loss)).length;
  const alt = Array.from({ length: 60 }, (_x, i) => (i % 2 ? null : 110));        // HTTP-проба раз в 2 мин (стенд: 29 из 60)
  eq(segs(xnPts(alt, 96, 22)).length, 30);                                       // было: 30 обрывков-точек
  eq(n(alt, Array(60).fill(1), Array(60).fill(0)), 1);                           // живой выход — одна линия
  eq(n(alt, Array(60).fill(0), Array(60).fill(null)), 1);                        // без строк, но ≤ 2 мин — тоже
  const g3 = [110, null, null, null, 120], st3 = [1, 0, 0, 0, 1];
  eq(n(g3, st3, []), 2);                                                         // 3 мин без строк — честный разрыв
  eq(n(g3, [1, 1, 2, 1, 1], []), 1);                                             // 3 мин «работает» без замера (12P) — линия
  eq(n([110, null, 120], [1, 3, 1], []), 2);                                     // минута «не работает» — разрыв всегда
  eq(n([110, null, 120], [1, 1, 1], [0, 100, 0]), 2);                            // полная потеря — разрыв
  eq(n([null, null, 110, 120, null], [1, 1, 1, 1, 1], []), 1);                   // края не достраиваем
  eq(xnJoin(xnPts([null, null, 110, 120, null], 96, 22), [1, 1, 1, 1, 1], []).length, 5);
  eq(xnJoin([], null, undefined), []);
});

test("provOf / calibOf (владелец 28.10, решения 4 и 5): «предварительно» — по ответу сервера, не по догадке панели", () => {
  const { provOf, calibOf } = P();
  const F = { fresh: { total: 640 } };
  eq(provOf(Object.assign({ provisional: { why: "net_young", have_h: 5, need_h: 12 } }, F)), { have: 5, need: 12 });
  eq(provOf(Object.assign({ provisional: { have_h: 0, need_h: 12 } }, F)), { have: 0, need: 12 });          // первый час — 0 из 12
  eq(provOf({ provisional: { have_h: 5, need_h: 12 }, fresh: { total: null, why: "incomplete" } }), null);   // итога нет — помечать нечего
  for (const syn of [null, {}, F, Object.assign({ provisional: null }, F), Object.assign({ provisional: { have_h: "5", need_h: 12 } }, F), Object.assign({ provisional: [] }, F)]) eq(provOf(syn), null);
  const L = (id, calib) => ({ id, calib });
  eq(calibOf({ links: [L("net_bw", false), L("cpu", true), L("crypto", true), L("up", false)] }),
    { ok: ["cpu", "crypto"], prov: ["net_bw", "net_q", "stab", "down", "up", "loss"] });
  eq(calibOf({ links: [L("cpu", "true"), L("crypto", 1)] }).ok, []);                      // только true
  eq(calibOf(null), { ok: [], prov: ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"] });
});

let fails = 0;
for (const [name, fn] of tests) {
  try { fn(); } catch (e) { fails += 1; console.log("FAIL " + name + "\n" + (e && e.stack)); }
}
if (fails) { console.log("OVB FAILED: " + fails); process.exit(1); }
console.log("OVB OK: " + n + " проверок в " + tests.length + " тестах");
