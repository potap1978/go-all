// Сетка графиков (Claude Design «Сетка на графиках r1» 30.10, HANDOFF «grid · Сетка на графиках r1»): одна шкала 1-2-5
// для всех линейных графиков панели — KaskadSpark.grid (static/spark.js) и ось рядов выхода KaskadCharts.axis
// (static/charts.js). Примеры — таблица «Шкала: шаг и верх» HANDOFF. Офлайн, только node. Часы — Москва (TZ ниже).
// Запуск: node tests/panel/test_grid.mjs -> «GRID OK: N …»; обёртка — tests/panel/test_panel_grid.py.
process.env.TZ = "Europe/Moscow";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../lib/kaskad/panel/static");
const box = { window: {}, console, Intl, Date, Math };
box.window.requestAnimationFrame = () => 0;
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "spark.js"), "utf8"), box, { filename: "spark.js" });
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "charts.js"), "utf8"), box, { filename: "charts.js" });
const G = box.window.KaskadSpark && box.window.KaskadSpark.grid;
const C = box.window.KaskadCharts;

// вид отметок графиков выхода: строка time из exits-groups.js (вид или список) → ось charts.js берёт его через
// KaskadSpark.grid.pick по своим ts0/ts1 (первая и последняя точки ряда)
const EG = fs.readFileSync(path.join(STATIC, "pages", "exits-groups.js"), "utf8");
const egTimeLine = (EG.match(/^\s*const time = .*$/m) || [""])[0];
const egKinds = vm.runInNewContext("(function (st, sm, K) {\n" + egTimeLine + "\nreturn time.kind;\n})", {});
const egTime = (range, t0, t1) => {
  const ts1 = t1 > t0 ? t1 : t0 + 1, kinds = egKinds({ range }, false, { lang: "ru" });
  return { ts0: t0, ts1, time: { kind: G.pick(t0, ts1, kinds, 4) } };
};

let n = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const plain = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const eq = (a, b, m) => { n++; assert.deepStrictEqual(plain(a), b, m || "eq"); };
const MB = 1048576;
const U = ["КБ", "МБ", "ГБ"];
const axis = (max, h) => G.labels(G.scale(max, G.K(h)), ",").join(" · ");
const vol = (bytes, h) => { const b = G.bytes(bytes, G.K(h)); return G.labels(b.sc, ",").join(" · ") + " " + U[b.k]; };

test("the grid lives in spark.js (one scale for every chart)", () => {
  eq(typeof G, "object");
  eq(["K", "scale", "bytes", "labels", "yw", "ticks", "lab", "fit", "box"].map((k) => typeof G[k]), Array(9).fill("function"));
});

test("K: a 150 px field holds 3 steps (up to 4 lines with zero), 120 px — 2", () => {
  eq([G.K(150), G.K(160), G.K(180), G.K(120)], [3, 3, 3, 2]);
});

test("the HANDOFF table: the smallest 1-2-5 step with ⌈max / step⌉ ≤ K, the top ≥ max", () => {
  eq([vol(11.6 * MB, 150), vol(11.6 * MB, 120)], ["0 · 5 · 10 · 15 МБ", "0 · 10 · 20 МБ"]);
  eq([axis(330, 150), axis(330, 120)], ["0 · 200 · 400", "0 · 200 · 400"]);
  eq([axis(92, 150), axis(92, 120)], ["0 · 50 · 100", "0 · 50 · 100"]);
  eq([axis(12, 150), axis(12, 120)], ["0 · 5 · 10 · 15", "0 · 10 · 20"]);
  eq([axis(48, 150), axis(48, 120)], ["0 · 20 · 40 · 60", "0 · 20 · 40 · 60"]);       // п. 4: n = 1 — шаг мельче
  eq([vol(170 * MB, 150), vol(170 * MB, 120)], ["0 · 100 · 200 МБ", "0 · 100 · 200 МБ"]);
  eq([vol(922 * MB, 150), vol(922 * MB, 120)], ["0 · 0,5 · 1 ГБ", "0 · 0,5 · 1 ГБ"]);   // в МБ верх был бы 1000
  eq(axis(4.5, 120), "0 · 2 · 4 · 6");
});

test("the top is never below the peak; the old ticks() «≤ max» left the peak above the top line", () => {
  for (const K of [2, 3]) for (const max of [0.03, 0.7, 1, 3.3, 9.99, 11.6, 48, 92, 101, 330, 999, 1234, 87654]) {
    const sc = G.scale(max, K);
    eq(sc.top >= max && sc.n * sc.step === sc.top, true, max + " / " + K);
    eq([1, 2, 5].includes(Math.round(sc.step / Math.pow(10, Math.floor(Math.log10(sc.step) + 1e-9)))), true, "1-2-5: " + sc.step);
  }
  eq(C.ticks(0, 330, 3), [0, 200, 400]);                                              // KaskadCharts.ticks — то же правило
});

test("volume: one unit per axis, by the max (base 1024); the top ≥ 1000 — the next unit", () => {
  eq([G.bytes(500 * 1024, 3).k, G.bytes(900 * 1024, 3).k, G.bytes(1.5 * MB, 3).k, G.bytes(2 * 1024 * MB, 3).k, G.bytes(990 * MB, 3).k], [0, 1, 1, 2, 2]);
  eq(vol(900 * 1024, 150), "0 · 0,5 · 1 МБ");                                          // не «900 КБ» и «1,2 МБ» на одной оси
  eq(vol(0, 150), "0 · 1 КБ");                                                         // пусто — 0 и 1, без NaN
});

test("tick labels: no extra zeros, the decimal sign of the language", () => {
  eq(G.labels({ step: 0.5, n: 2, top: 1 }, ","), ["0", "0,5", "1"]);
  eq(G.labels({ step: 0.2, n: 3, top: 0.6 }, "."), ["0", "0.2", "0.4", "0.6"]);
  eq(G.labels({ step: 0.05, n: 3, top: 0.15 }, ","), ["0", "0,05", "0,1", "0,15"]);
});

test("whole numbers (Devices) and the top only (server health)", () => {
  eq([G.scale(1, 3, { min: 1 }), G.scale(3, 3, { min: 1 }), G.scale(0, 3, { min: 1 })].map((s) => [s.step, s.top]), [[1, 1], [1, 3], [1, 1]]);
  eq([4, 7, 100, 12].map((v) => G.scale(v, 1, { one: true }).top), [5, 10, 100, 20]);
});

test("the column of numbers: the longest label × 6,6 px (mono 11), not less than 14, up to whole px", () => {
  eq([G.yw(["0", "5", "10", "15"], 6.6), G.yw(["0", "200", "400"], 6.6), G.yw(["0", "0,5", "1"], 6.6), G.yw(["0", "1"], 6.6)], [14, 20, 20, 14]);
});

test("time marks on round hours of the clock, not on equal shares of the period", () => {
  const t0 = Date.UTC(2026, 9, 29, 16, 10) / 1000;                                  // 29.10 19:10 МСК
  const day = G.ticks(t0, t0 + 86400, "h", 4);
  eq(day.map((x) => G.lab("h", x.d, "ru")), ["20:00", "00:00", "04:00", "08:00", "12:00", "16:00"]);
  eq(Math.round(day[0].at * 1440), 50);                                              // 20:00 — через 50 мин после начала
  eq(G.ticks(t0, t0 + 86400, "h", 6).map((x) => G.lab("h", x.d, "ru")), ["00:00", "06:00", "12:00", "18:00"]);
  const week = G.ticks(t0 - 6 * 86400, t0 + 86400, "d");
  eq(week.map((x) => G.lab("d", x.d, "ru")), ["сб 24", "вс 25", "пн 26", "вт 27", "ср 28", "чт 29", "пт 30"]);
  eq(week.slice(0, 2).map((x) => G.lab("d", x.d, "en")), ["Sat 24", "Sun 25"]);
  eq(week.slice(0, 2).map((x) => G.lab("d", x.d, "en", true)), ["Sat", "Sun"]);      // телефон — без числа
  const month = G.ticks(t0 - 29 * 86400, t0 + 86400, "w");
  eq(month.map((x) => G.lab("w", x.d, "ru")), ["5 окт.", "12 окт.", "19 окт.", "26 окт."]);
  eq(G.lab("w", month[3].d, "en"), "Oct 26");
  eq(G.ticks(t0, t0, "h", 4), []);
});

test("90 days of an exit: marks on the 1st of the month, not on Mondays (Claude Design, grid r1b №2)", () => {
  const t0 = Date.UTC(2026, 9, 29, 16, 10) / 1000;                                  // 29.10 19:10 МСК
  const q = G.ticks(t0 - 90 * 86400, t0, "m");                                      // 31.07 19:10 … 29.10 19:10
  eq(q.map((x) => G.lab("m", x.d, "ru")), ["1 авг.", "1 сент.", "1 окт."]);
  eq(q.map((x) => G.lab("m", x.d, "en")), ["Aug 1", "Sep 1", "Oct 1"]);
  eq(q.map((x) => G.lab("m", x.d, "ru", true)), ["1 авг.", "1 сент.", "1 окт."]);   // телефон — так же, с числом
  eq(q.map((x) => [x.d.getDate(), x.d.getHours(), x.d.getMinutes()]), [[1, 0, 0], [1, 0, 0], [1, 0, 0]]);
  eq(Math.round(q[2].at * 90 * 144), 61 * 144 + 29);                                // 1.10 00:00 — через 61 сут 4 ч 50 мин (шаг 10 мин)
  eq(G.ticks(t0 - 89 * 86400, t0 + 86400, "m").map((x) => G.lab("m", x.d, "ru")), ["1 сент.", "1 окт."]);
  // «30 дней» — понедельники, как были
  eq(G.ticks(t0 - 29 * 86400, t0 + 86400, "w").map((x) => x.d.getDay()), [1, 1, 1, 1]);
  // графики выхода: «90 дней» с полной историей — отметки "m", «7 дней» — "d", «Сутки» — "h" (строки exits-groups.js)
  eq(egTime("90d", t0 - 90 * 86400, t0).time.kind, "m", "exits-groups.js: 90d → m");
  eq(egTime("7d", t0 - 7 * 86400, t0).time.kind, "d");
  eq(egTime("1d", t0 - 86400, t0).time.kind, "h");
  // ось графика выхода берёт вид через pick по своим ts0/ts1 (вид-строка — как есть)
  const ch = fs.readFileSync(path.join(STATIC, "charts.js"), "utf8");
  eq(ch.includes('kind = G().pick(sc.ts0, sc.ts1, tm.kind || "h", tm.hours)'), true, "charts.js: pick");
  eq([G.pick(t0, t0 + 3600, "h", 4), G.pick(t0, t0 + 3600, ["m", "w", "d"]), G.pick(t0, t0, ["m"])], ["h", "d", "m"]);
});

// REV-33714 Б-1: ось идёт от первой точки ряда, у нового выхода «90 сут» — его 10–25 дней, 1-х чисел на ней 0–1.
// Вид отметок — по длине данных: самый крупный, при котором на оси ≥ 2 отметок (1-е число → понедельник → полночь)
test("a young exit on 90 days: marks by the length of the data, ≥ 2 of them (REV-33714 Б-1)", () => {
  const t0 = Date.UTC(2026, 9, 29, 16, 10) / 1000;                                  // 29.10 19:10 МСК
  const marks = (r) => G.ticks(r.ts0, r.ts1, r.time.kind);
  const h20 = egTime("90d", t0 - 20 * 86400, t0);                                   // история 20 дней
  eq(h20.time.kind, "w", "20 days on 90d → w");
  eq(marks(h20).map((x) => G.lab("w", x.d, "ru")), ["12 окт.", "19 окт.", "26 окт."]);
  eq([12, 25, 29].map((d) => egTime("90d", t0 - d * 86400, t0).time.kind), ["w", "w", "w"]);   // 1-х чисел 0, 0, 1
  eq(egTime("90d", t0 - 5 * 86400, t0).time.kind, "d");                             // понедельник один — полночи
  eq(egTime("90d", t0 - 60 * 86400, t0).time.kind, "m");                            // 1 сент. и 1 окт.
  // от 2 суток до 63 (дальше — всегда два 1-х числа) — на оси ≥ 2 отметок при любом конце: 4 мес., шаг конца 3 сут 5 ч
  for (let end = t0; end < t0 + 122 * 86400; end += 77 * 3600) {
    for (let d = 2; d <= 63; d++) {
      const r = egTime("90d", end - d * 86400, end), k = marks(r).length;
      if (k < 2) { eq([d, new Date(end * 1000).toISOString(), r.time.kind, k], "≥ 2 marks"); }
    }
  }
  n++;
  eq(egTime("90d", t0, t0).time.kind, "d");                                         // одна точка — без отметок, без ошибки
});

test("fit: a label over the left edge leans on its line, one over a neighbour or «now» hides, the line stays", () => {
  // поле 300, «сейчас» 40: подписи 33 px у 10 (край), 60 (налезает на первую), 150, 270 (налезает на «сейчас»)
  eq(G.fit([10, 60, 150, 270], [33, 33, 33, 33], 300, 40, false), ["is-start", "is-hid", "", "is-hid"]);
  const lefts = [];
  eq(G.fit([10, 60, 150, 270], [33, 33, 33, 33], 300, 40, true, lefts), ["is-start", "", "", ""]);   // фазы замера — не прячутся;
  eq(lefts.map(Math.round), [0, 44, 134, 254]);                                                        // у края — от края поля (П-1)
  const ph = [];                                                         // короткая фаза: от края поля, соседняя — правее на 8
  eq(G.fit([10, 60], [50, 50], 300, 0, true, ph), ["is-start", "is-start"]);
  eq(ph, [0, 58]);
  eq(G.fit([100, 141], [33, 33], 300, 0, false), ["", ""]);                                   // зазор 8 — помещается
  eq(G.fit([100, 140], [33, 33], 300, 0, false), ["", "is-hid"]);
});

test("exit rows: the traffic axis in bytes is one unit per axis, «Devices» are whole", () => {
  const pts = [[0, 330, 210 * MB, 3], [60, 20, 0, 1]];
  eq(C.axis(pts, 1, { K: 3 }).ys, ["0", "200", "400"]);
  const tr = C.axis(pts, 2, { K: 3, bytes: true, sep: "," });
  eq([tr.ys, tr.k, tr.f], [["0", "100", "200", "300"], 1, MB]);
  eq(C.axis(pts, 3, { K: 3, min: 1 }).ys, ["0", "1", "2", "3"]);
  eq(C.axis(pts, 1, { K: 2 }).ys, ["0", "200", "400"]);
});

for (const [name, fn] of tests) {
  try { fn(); } catch (e) { console.error("FAIL: " + name + "\n" + (e && e.stack ? e.stack : e)); process.exit(1); }
}
console.log("GRID OK: " + n + " проверок в " + tests.length + " тестах");
