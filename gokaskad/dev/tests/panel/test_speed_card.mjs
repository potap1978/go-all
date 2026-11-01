// BC5 (k3/handoff/SPEC-browser-channel.md §6.3–§6.6, макет «Browser Speed r1»): чистые помощники карточки
// static/pages/speed.js — ось и линии графика, что сказать по итогу движка, строка «под нагрузкой», пометки, жирная первая
// фраза, история в браузере. Офлайн, только node. Запуск: node tests/panel/test_speed_card.mjs -> «SPEED CARD OK: N …»;
// обёртка — tests/panel/test_speed_js.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../lib/kaskad/panel/static");
const box = { self: {}, window: {}, console };
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "speed-calc.js"), "utf8"), box, { filename: "speed-calc.js" });
box.window.SpeedCalc = box.self.SpeedCalc;
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "spark.js"), "utf8"), box, { filename: "spark.js" });   // шкала — сетка графиков
let part = null;
box.window.KaskadPanel = { el: () => null, t: (k) => k, tn: (k) => k, U: {}, mock: () => false,
  part: (name, def) => { part = { name, def }; return true; } };
vm.runInNewContext(fs.readFileSync(path.join(STATIC, "pages/speed.js"), "utf8"), box, { filename: "pages/speed.js" });

let n = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const plain = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const eq = (a, b, m) => { n++; assert.deepStrictEqual(plain(a), b, m || "eq"); };
const ok = (v, m) => { n++; assert.ok(v, m); };

test("the part registers itself as «speed» with build, card and the pure helpers", () => {
  eq(part.name, "speed");
  eq([typeof part.def.build, typeof part.def.card, typeof part.def.pure], ["function", "function", "object"]);
});
const P = () => part.def.pure;

test("bounds of the phases: known — by the page clock, running — not shorter than passed, future — by the estimate", () => {
  const b = P().bounds;
  eq(b({ idle: 1000, down: 3200, up: 11600, finish: 20000 }, 99999), [0, 2200, 10600, 19000]);       // итог: как в макете
  eq(b({ idle: 1000 }, 2000), [0, 2500, 12500, 22500]);                                                // покой идёт: 2,5 с
  eq(b({ idle: 1000 }, 4500), [0, 3500, 13500, 23500]);                                                // покой дольше оценки
  eq(b({ idle: 1000, down: 3200 }, 5000), [0, 2200, 12200, 22200]);                                    // скачивание идёт
  eq(b({ idle: 1000, down: 3200 }, 15200), [0, 2200, 14200, 24200]);                                   // дольше 10 с
  eq(b({ idle: 1000, down: 3200, up: 9000 }, 12000), [0, 2200, 8000, 18000]);                          // отдача идёт
  eq(b({ idle: 1000, down: 3200, up: 9000, finish: 8000 }, 0), [0, 2200, 8000, 8000]);                 // конец не раньше отдачи
});

test("chart: the running side — accent line and a dot at its end, done sides — grey; the top — the grid rule (1-2-5, not below the peak)", () => {
  const c = P().chartOf;
  const pts = { down: [[2300, 0], [6200, 87]], up: [[10700, 10], [18900, 41]] };
  const run = c({ pts: { down: pts.down, up: [] }, b: [0, 2200, 12200, 22200], cur: "down" });
  eq([run.done, run.ym, plain(run.sc)], ["", 100, { step: 50, n: 2, top: 100 }]);   // 87 → 0 · 50 · 100
  eq(run.live, "M62.2 158.5L167.6 21.9");                                                 // ноль — H − 1,5, верх — 1,5
  eq(run.dot, "M167.6 21.9h0");
  eq(c({ pts: { down: pts.down, up: [] }, b: [0, 2200, 12200, 22200], cur: "down" }, 300, 180).live, "M31.1 178.5L83.8 24.5");   // поле W × H
  const up = c({ pts, b: [0, 2200, 10600, 19000], cur: "up" });
  ok(up.done.startsWith("M") && up.live.startsWith("M") && up.dot.endsWith("h0"), JSON.stringify(up));
  const res = c({ pts, b: [0, 2200, 10600, 19000], cur: undefined });
  eq([res.live, res.dot], ["", ""]);
  eq(res.done.split("M").length - 1, 2);                                                  // две линии: скачивание и отдача
  eq(c({ pts: { down: [], up: [] }, b: [0, 1, 2, 3], cur: undefined, min: 450 }).ym, 600);   // пусто — по итогу (min): 0 · 200 · 400 · 600
  eq(c({ pts: { down: [], up: [] }, b: [0, 1, 2, 3], cur: undefined, min: 92 }).ym, 100);    // Ск1: 92 → 100 (было 200 по «≥ 1,1 × пик»)
  eq(c({ pts: { down: [[100, 900]], up: [] }, b: [0, 1, 2, 3], cur: "down" }).live, "M600.0 17.2");  // за краем по времени — к краю
  eq(P().pathOf([[5, 200], [-1, -3]], 10, 100), "M300.0 1.5L0.0 158.5");                                // и по скорости — в рамку
  eq(P().pathOf([], 10, 100), "");
});

test("what to say after the engine: limits, another test, errors, a hidden tab, «Stop», success", () => {
  const s = P().sayOf;
  const er = (kind, more) => ({ ok: false, error: Object.assign({ kind, status: 1, retryAfter: null, n: null, limit: null }, more || {}), stopped: null });
  eq(s({ ok: true, error: null, stopped: null }), null);
  eq(s({ ok: false, error: null, stopped: "user" }), { st: "idle" });
  eq(s({ ok: false, error: null, stopped: "hidden" }), { st: "err", tone: "", key: "panel.speed.fail.hidden" });
  eq(s(er("hour", { n: 6, retryAfter: 1100 })), { st: "lim", tone: "warning", key: "panel.speed.lim.hour", n: 6, wait: 1100, plural: true });
  eq(s(er("day", { n: 30, retryAfter: 20400.2 })), { st: "lim", tone: "warning", key: "panel.speed.lim.day", n: 30, wait: 20401, plural: true });
  eq(s(er("traffic", { limit: 1e10, retryAfter: 33100 })), { st: "lim", tone: "warning", key: "panel.speed.lim.traffic", gb: 1e10, wait: 33100 });
  eq(s(er("busy", { retryAfter: 65 })), { st: "busy", tone: "", key: "panel.speed.other", wait: 65 });
  // T-3310b М-5: идёт проверка сервера (её могли запустить в этой же панели) — не «в другой вкладке»
  eq(s(er("busy", { retryAfter: 40, what: "check" })), { st: "busy", tone: "", key: "panel.speed.checkBusy", wait: 40 });
  eq(s(er("busy")).wait, 15);                                                              // нет retry_after — 15 с
  eq(s(er("hour")).wait, 60);
  eq(s(er("expired")), { st: "err", tone: "danger", key: "panel.speed.fail.expired" });
  eq(s(er("server_busy")), { st: "err", tone: "", key: "panel.speed.fail.server_busy" });   // 429 nginx — нейтрально
  eq(s(er("net")), { st: "err", tone: "danger", key: "panel.speed.fail.net" });
  eq(s(er("auth")), { st: "auth" });
  for (const k of ["server", "streams", "lease", "forbidden", "too_big", "http", "budget"]) {
    eq(s(er(k)), { st: "err", tone: "danger", key: "panel.speed.fail.server" }, k);
  }
  eq(s(er("busy", { retryAfter: -5 })).wait, 15);
});

test("under load: the word of the scale, or why there is no line", () => {
  const l = P().loadOf;
  eq(l(null), null);
  eq(l({ show: true, deltaMs: 35, scale: "noticeably", reason: null }), { n: 35, word: "panel.speed.word.noticeably" });
  eq(l({ show: false, deltaMs: null, scale: null, reason: "one_conn" }), { why: "panel.speed.why.one_conn" });
  eq(l({ show: false, deltaMs: null, scale: null, reason: "idle" }), { why: "panel.speed.why.idle" });
  eq(l({ show: false, reason: null }), null);
});

test("notes: only known flags, in the order of §6.4; «hidden» is an error, not a note", () => {
  eq(P().notesOf(["fast", "hidden", "tunnel", "x", "cpu", "one_conn", "vpn", "cascade"]),
     ["panel.speed.note.tunnel", "panel.speed.note.vpn", "panel.speed.note.cascade", "panel.speed.note.one_conn", "panel.speed.note.cpu", "panel.speed.note.fast"]);
  eq(P().notesOf(null), []);
});

test("bold — the first sentence: decimals and brackets do not cut it", () => {
  const b = P().boldSplit;
  eq(b("Сейчас идёт другая проверка (в другой вкладке или с другого устройства). Повторите через 15 с."),
     ["Сейчас идёт другая проверка (в другой вкладке или с другого устройства).", "Повторите через 15 с."]);
  eq(b("The test uses your mobile data: up to 1.6 GB. Continue?"), ["The test uses your mobile data: up to 1.6 GB.", "Continue?"]);
  eq(b("Проверка прервана: истекло время."), ["Проверка прервана: истекло время.", ""]);
  eq(b("без точки"), ["без точки", ""]);
});

test("history in the browser: try/catch around the storage, only clean records", () => {
  const { histRead, histWrite, KEY } = P();
  eq(KEY, "kaskad.speed.v1");
  const mem = {};
  const st = { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } };
  eq(histRead(st), []);
  mem[KEY] = "{broken";
  eq(histRead(st), []);
  mem[KEY] = JSON.stringify([{ t: 5, ping: 24, ip: "1.2.3.4" }, { t: "x" }]);
  eq(histRead(st), [{ t: 5, ping: 24, jitter: null, down: null, up: null, loaded: null, via: null, proto: null, flags: [] }]);
  eq(histWrite(st, [{ t: 1 }]), true);
  eq(mem[KEY], '[{"t":1}]');
  eq(histWrite(st, []), true);
  eq(KEY in mem, false);                                                                    // пусто — ключа нет
  const bad = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); }, removeItem: () => { throw new Error("x"); } };
  eq([histRead(bad), histWrite(bad, [{ t: 1 }]), histWrite(bad, [])], [[], false, false]);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); } catch (e) { failed++; console.error("FAIL " + name + "\n" + (e && e.stack || e)); }
}
if (failed) { console.error(`SPEED CARD FAILED: ${failed} из ${tests.length}`); process.exit(1); }
console.log(`SPEED CARD OK: ${n} проверок в ${tests.length} тестах`);
