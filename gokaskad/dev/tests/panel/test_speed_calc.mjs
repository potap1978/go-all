// BC4 (k3/handoff/SPEC-browser-channel.md §6.2–6.4, §8.1): чистые функции static/speed-calc.js — офлайн, только node.
// Запуск: node tests/panel/test_speed_calc.mjs -> «SPEED CALC OK: N проверок»; обёртка — tests/panel/test_speed_js.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FILE = path.resolve(HERE, "../../lib/kaskad/panel/static/speed-calc.js");
const SRC = fs.readFileSync(FILE, "utf8");

// как в воркере (importScripts): глобальный self, без module
const box = { self: {} };
vm.runInNewContext(SRC, box, { filename: FILE });
const S = box.self.SpeedCalc;
const C = S.C;
const KIB = 1024, MIB = 1024 * 1024;

let n = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
// объекты из другого контекста vm — со своими прототипами: сравнение по JSON
const plain = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const eq = (a, b, m) => { n++; if (m === undefined) assert.deepStrictEqual(plain(a), b); else assert.deepStrictEqual(plain(a), b, m); };
const ok = (v, m) => { n++; assert.ok(v, m); };
const near = (a, b, rel, m) => { n++; assert.ok(Math.abs(a - b) <= Math.abs(b) * rel, (m || "") + ` ${a} ≉ ${b}`); };

// ряд байт с постоянной скоростью bps от t0 (мс) до t1 шагом 100 мс; seg — [[до мс, бит/с], …]
function series(seg) {
  const out = [[0, 0]];
  let t = 0, b = 0;
  for (const [until, bps] of seg) {
    while (t < until) { t += 100; b += bps / 8 * 0.1; out.push([t, b]); }
  }
  return out;
}

test("core is pure: no DOM, no network, no panel", () => {
  const code = SRC.replace(/\/\/[^\n]*/g, "");
  for (const bad of [/\bdocument\b/, /\bwindow\b/, /\bfetch\s*\(/, /XMLHttpRequest/, /localStorage/, /kaskad/i, /\brequire\s*\(/, /\bimport\b/]) {
    ok(!bad.test(code), "в ядре: " + bad);
  }
  // и в node через require — тот же объект
  const mod = { exports: {} };
  vm.runInNewContext(SRC, { module: mod }, { filename: FILE });
  eq(Object.keys(mod.exports).sort(), Object.keys(S).sort());
});

test("median and spread", () => {
  eq(S.median([]), null);
  eq(S.median([3, 1, 2]), 2);
  eq(S.median([4, 1, 3, 2]), 2.5);
  eq(S.median([5, NaN, "x", 7]), 6);
  eq(S.spread([10]), null);
  eq(S.spread([10, 12, 11]), 1.5);
  eq(S.spread([20, 20, 20, 20]), 0);
});

test("Server-Timing", () => {
  eq(S.serverMs("app;dur=12.5"), 12.5);
  eq(S.serverMs("db;dur=3, app;desc=\"x\";dur=4"), 4);
  eq(S.serverMs(""), 0);
  eq(S.serverMs(null), 0);
  eq(S.serverMs("app"), 0);
});

test("good samples: requestStart − startTime 20 ms yes, 21 ms no", () => {
  const e = (q) => ({ startTime: 100, requestStart: 100 + q, responseStart: 100 + q + 30 });
  eq(S.sample(e(20), 99, 2), { ms: 28, good: true, rt: true });
  eq(S.sample(e(21), 99, 2), { ms: 28, good: false, rt: true });
  eq(S.sample(null, 25, 1), { ms: 24, good: true, rt: false });            // нет записи — по часам вокруг запроса
  eq(S.sample({ startTime: 0, requestStart: 0, responseStart: 0 }, 25, 1).rt, false);
  eq(S.sample(null, 1, 5).ms, 0);                                           // не меньше нуля
});

test("idle: median of good, spread of neighbouring good, < 10 good — none", () => {
  const good = (ms) => ({ ms, good: true });
  const bad = (ms) => ({ ms, good: false });
  const ten = [20, 22, 21, 23, 20, 22, 21, 500, 20, 22].map((ms, i) => (i === 7 ? bad(ms) : good(ms))).concat([good(21)]);
  const r = S.idle(ten);
  eq([r.good, r.total, r.ok], [10, 11, true]);
  eq(r.ping, 21);
  near(r.jitter, (2 + 1 + 2 + 3 + 2 + 1 + 1 + 2 + 1) / 9, 1e-9);
  const nine = ten.slice(0, 10);
  eq(S.idle(nine), { ping: null, jitter: null, good: 9, total: 10, ok: false });
});

test("rate and window after the ramp", () => {
  const s = series([[1000, 10e6], [6000, 100e6]]);
  near(S.rate(s, 2000, 6000), 100e6, 1e-9);
  near(S.rate(s, 0, 1000), 10e6, 1e-9);
  eq(S.rate(s, 5, 5), null);
  near(S.bytesAt(s, 1050), 10e6 / 8 + 100e6 / 8 * 0.05, 1e-9);           // между отсчётами — по прямой
  const w = S.windowOf(s, 1000, C.DOWN_RAMP_MS);
  eq([w.from, w.to, w.ms], [2500, 6000, 3500]);
  near(w.bps, 100e6, 1e-9);                                                 // разгон 10 Мбит/с выброшен
  eq(S.windowOf(s, null, 1500).bps, null);
  eq(S.windowOf(s, 5000, 1500).bps, null);                                  // окно ещё не началось
});

test("the series of a side for its window: the drain and the tail — only at the end by the rest (REV-bc4 З-2, П-2)", () => {
  const s = [[0, 0], [100, 10], [200, 20], [300, 30], [400, 35], [500, 38], [600, 38], [700, 38]];
  // по остатку: ряд — до первого потока без работы (300), хвост без байт — прочь
  eq(S.sideSeries(s, "left", 300), [[0, 0], [100, 10], [200, 20], [300, 30]]);
  eq(S.sideSeries(s, "left", null), s.slice(0, 6));                      // слива нет — только хвост
  eq(S.sideSeries(s, "left", 50), s.slice(0, 6));                        // до drainAt меньше двух точек — без среза
  // по времени и по ровной скорости ряд не трогается (остановка потока — часть пути)
  eq(S.sideSeries(s, "time", 300), s);
  eq(S.sideSeries(s, "stable", null), s);
  eq(S.sideSeries([[0, 0], [100, 0]], "left", null), [[0, 0], [100, 0]]);   // две точки — не меньше
  ok(S.sideSeries(s, "left", 300) !== s, "новый ряд, исходный не тронут");
  eq(s.length, 8);
});

test("early end: last 2 s vs previous 2 s — 4.9 % ends, 5.1 % does not", () => {
  const ramp = 300, from = ramp + C.DOWN_RAMP_MS;                           // окно с 1800
  const mk = (k) => series([[from + 2000, 100e6], [from + 4000, 100e6 * k]]);
  eq(S.stopReason(mk(1.049), from + 4000, ramp, C.DOWN_RAMP_MS), "stable");
  eq(S.stopReason(mk(0.951), from + 4000, ramp, C.DOWN_RAMP_MS), "stable");
  eq(S.stopReason(mk(1.051), from + 4000, ramp, C.DOWN_RAMP_MS), null);
  eq(S.stopReason(mk(0.949), from + 4000, ramp, C.DOWN_RAMP_MS), null);
  eq(S.stopReason(mk(1.0), from + 3900, ramp, C.DOWN_RAMP_MS), null);     // окно < 4 с
  eq(S.stopReason(mk(1.0), from + 4000, null, C.DOWN_RAMP_MS), null);      // разгон не кончился
  eq(S.stopReason(mk(1.3), 10000, null, C.DOWN_RAMP_MS), "time");          // 10 с стороны
  // отдача: разгон 2 с, то же окно 4 с
  const up = series([[6300, 50e6]]);
  eq(S.stopReason(up, 6200, 300, C.UP_RAMP_MS), null);
  eq(S.stopReason(up, 6300, 300, C.UP_RAMP_MS), "stable");
});

test("ramp of streams: +1 while the speed grows > 10 % and < 4 streams", () => {
  eq(S.rampStep(1, null, 0), "wait");
  eq(S.rampStep(1, null, 5e6), "add");
  eq(S.rampStep(2, 10e6, 11.1e6), "add");
  eq(S.rampStep(2, 10e6, 11e6), "done");
  eq(S.rampStep(3, 10e6, 9e6), "done");
  eq(S.rampStep(4, 10e6, 40e6), "done");
});

test("ladders of both sides", () => {
  eq(S.downSteps(), [256 * KIB, MIB, 8 * MIB, 32 * MIB, 128 * MIB, 256 * MIB]);
  eq(S.upSteps(false), [256 * KIB, MIB, 4 * MIB, 16 * MIB, 64 * MIB]);
  eq(S.upSteps(true), [256 * KIB, MIB, 4 * MIB, 16 * MIB]);
  eq(S.pickStep(S.downSteps(), null), 256 * KIB);                          // скорость неизвестна — первая ступень
  eq(S.pickStep(S.downSteps(), 1e6), 256 * KIB);                           // 1 Мбит/с — меньше ступени
  eq(S.pickStep(S.downSteps(), 50e6), 8 * MIB);                            // 6,25 МБ за 1 с: 8 МиБ ≈ 1,3 с, 1 МиБ ≈ 0,17 с
  eq(S.pickStep(S.downSteps(), 100e6), 8 * MIB);                           // 12,5 МБ: 8 МиБ ≈ 0,7 с, 32 МиБ ≈ 2,7 с
  eq(S.pickStep(S.downSteps(), 2e9), 256 * MIB);                           // 250 МБ: 256 МиБ ≈ 1,07 с
  eq(S.pickStep(S.downSteps(), 1e9), 128 * MIB);
  eq(S.pickStep(S.downSteps(), 1e12), 256 * MIB);
  eq(S.pickStep(S.downSteps(), 0), 256 * KIB);
  eq(S.pickStep(S.upSteps(false), 100e6), 16 * MIB);                       // 12,5 МБ: 16 МиБ ≈ 1,3 с
  eq(S.pickStep(S.upSteps(false), 20e6), 4 * MIB);
  eq(S.pickStep(S.upSteps(false), 1e12), 64 * MIB);                        // = req_up_max
  eq(S.pickStep(S.upSteps(true), 1e12), 16 * MIB);
  const steps = S.downSteps();
  for (let i = 1; i < steps.length; i++) eq(S.pickStep(steps, steps[i] * 8), steps[i]);   // ровно на границе
});

test("size rule: left minus every started request, < 256 KiB — end without a request", () => {
  const steps = S.downSteps();
  eq(S.sizeFor(300 * MIB, steps, 1e12), 256 * MIB);
  eq(S.sizeFor(10 * MIB, steps, 1e12), 10 * MIB);                          // min(ступень, left)
  eq(S.sizeFor(256 * KIB, steps, 1e12), 256 * KIB);
  eq(S.sizeFor(256 * KIB - 1, steps, 1e12), 0);
  eq(S.sizeFor(0, steps, null), 0);
  eq(S.sizeFor(-5, steps, null), 0);
  // ход стороны: 4 потока, лимит 800 МБ; оборванный запрос не возвращается — сумма начатых ≤ лимита, потом 0
  const limit = 800e6;
  let left = limit, started = 0, k = 0;
  for (;;) {
    const size = S.sizeFor(left, steps, 2e9 / 4);
    if (!size) break;
    left -= size; started += size; k++;
    if (k % 3 === 0) { /* этот запрос оборван посреди — назад в left ничего */ }
  }
  ok(started <= limit, "начато больше лимита");
  ok(left < 256 * KIB && left >= 0);
  eq(S.sizeFor(left, steps, 2e9), 0);
});

test("protocol by via", () => {
  eq(S.protoOf("proxy", "http/1.1"), "h2");
  eq(S.protoOf("direct", "h2"), "http/1.1");
  eq(S.protoOf("tunnel", ""), "http/1.1");
  eq(S.protoOf("front", ""), "h2");
  eq(S.protoOf("front", undefined), "h2");
  eq(S.protoOf("front", "http/1.1"), "http/1.1");
  eq(S.protoOf("front", "h2"), "h2");
  eq(S.protoOf("front", "h3"), "h2");
  eq(S.protoOf(null, "HTTP/1.1"), "http/1.1");
});

test("scale of the growth: 29/30/100/101 ms", () => {
  eq(S.loadScale(0), "barely");
  eq(S.loadScale(29), "barely");
  eq(S.loadScale(30), "noticeably");
  eq(S.loadScale(100), "noticeably");
  eq(S.loadScale(101), "a_lot");
});

test("under load: hidden for h2, cpu of both sides, < 8 good, < 2/3 good, no idle", () => {
  const g = (ms, k) => Array.from({ length: k }, () => ({ ms, good: true }));
  const b = (k) => Array.from({ length: k }, () => ({ ms: 900, good: false }));
  const none = { down: false, up: false };
  eq(S.loaded(20, g(60, 5), g(80, 5), none, "http/1.1"), { show: true, deltaMs: 50, scale: "noticeably", reason: null });
  eq(S.loaded(20, g(60, 5), g(80, 5), none, "h2").reason, "one_conn");
  eq(S.loaded(20, g(60, 5), g(80, 5), { down: true, up: true }, "http/1.1").reason, "cpu");
  // одна сторона в квоте — её отсчёты выброшены
  eq(S.loaded(20, g(500, 8), g(30, 8), { down: true, up: false }, "http/1.1").deltaMs, 10);
  eq(S.loaded(20, g(500, 8), g(30, 4), { down: true, up: null }, "http/1.1").reason, "queue");   // осталось 4 < 8
  eq(S.loaded(20, g(30, 7), [], none, "http/1.1").reason, "queue");                              // 7 < 8
  eq(S.loaded(20, g(30, 8), [], none, "http/1.1").show, true);
  // REV-bc4 З-3: под нагрузкой отсчёт без записи Resource Timing (rt: false) не годен — очередь не видна
  const nort = (ms, k) => Array.from({ length: k }, () => ({ ms, good: true, rt: false }));
  eq(S.loaded(20, nort(30, 12), nort(30, 12), none, "http/1.1").reason, "queue");
  eq(S.loaded(20, g(30, 8).map((x) => Object.assign({ rt: true }, x)), nort(30, 3), none, "http/1.1").show, true);
  eq(S.loaded(20, g(30, 7).map((x) => Object.assign({ rt: true }, x)), nort(30, 1), none, "http/1.1").reason, "queue");
  eq(S.loaded(20, g(30, 10), b(6), none, "http/1.1").reason, "queue");                           // 10/16 < 2/3
  eq(S.loaded(20, g(30, 12), b(6), none, "http/1.1").show, true);                                // 12/18 = 2/3
  eq(S.loaded(null, g(30, 12), [], none, "http/1.1").reason, "idle");
  eq(S.loaded(40, g(30, 12), [], none, "http/1.1").deltaMs, 0);                                  // не меньше нуля
  eq(S.loaded(20, g(121, 12), [], none, "http/1.1").scale, "a_lot");
});

test("flags of «how it was measured»", () => {
  eq(S.flags({ via: "direct", vpn: false, proto: "http/1.1", cpu: { down: false, up: null } }), []);
  eq(S.flags({ via: "tunnel", vpn: "self", proto: "h2", nextHop: "h2", cpu: { down: null, up: true }, hidden: true }),
     ["tunnel", "vpn", "one_conn", "cpu", "hidden"]);
  // REV-bc4 П-3: «одно соединение» — только известное (proxy или nextHop h2/h3); догадка h2 у front — без пометки
  eq(S.flags({ via: "front", proto: "h2", nextHop: "" }), []);
  eq(S.flags({ via: "front", proto: "h2" }), []);
  eq(S.flags({ via: "front", proto: "h2", nextHop: "h2" }), ["one_conn"]);
  eq(S.flags({ via: "front", proto: "h2", nextHop: "h3" }), ["one_conn"]);
  eq(S.flags({ via: "front", proto: "http/1.1", nextHop: "http/1.1" }), []);
  eq(S.flags({ via: "proxy", vpn: "cascade", proto: "h2" }), ["cascade", "one_conn"]);
  eq(S.flags({ down: { endedBy: "left", windowMs: 2500 } }), ["fast"]);
  eq(S.flags({ down: { endedBy: "left", windowMs: 4000 }, up: { endedBy: "stable", windowMs: 1000 } }), []);
});

test("refusals: data, retry_after and the header Retry-After", () => {
  const body = (code, data) => ({ ok: false, error: "текст", code, data });
  eq(S.refusal(409, body("busy", { busy: true, retry_after: 30 })),
     { kind: "busy", status: 409, text: "текст", retryAfter: 30, n: null, limit: null, left: null, what: null });
  eq(S.refusal(409, body("busy", { busy: true, retry_after: 30, what: "check" })).what, "check");   // М-5: проверка сервера
  const h = S.refusal(429, body("hour", { reason: "hour", limit: 6, used: 6, retry_after: 1800 }));
  eq([h.kind, h.n, h.limit, h.retryAfter], ["hour", 6, 6, 1800]);
  eq(S.refusal(429, body("traffic", { reason: "traffic", limit: 10e9, used: 9.99e9, retry_after: 7200 })).kind, "traffic");
  eq(S.refusal(429, body("budget", { reason: "budget", left: 5 })).left, 5);
  eq(S.refusal(429, body("streams", { reason: "streams" })).kind, "streams");
  eq(S.refusal(429, { ok: false, data: { reason: "day", used: 30, limit: 30 } }).kind, "day");   // без code — по reason
  const nginx = S.refusal(429, null, "12");
  eq([nginx.kind, nginx.retryAfter, nginx.text], ["server_busy", 12, null]);
  eq(S.refusal(409, body("busy", {}), "7").retryAfter, 7);                 // нет в data — из заголовка
  eq(S.refusal(409, body("busy", {}), "soon").retryAfter, null);
  eq(S.refusal(410, body("expired")).kind, "expired");
  eq(S.refusal(403, body("lease")).kind, "lease");
  eq(S.refusal(403, { ok: false, error: "x" }).kind, "forbidden");
  eq(S.refusal(401, null).kind, "auth");
  eq(S.refusal(413, body("too_big")).kind, "too_big");
  eq(S.refusal(502, null).kind, "server");
  eq(S.refusal(0, null).kind, "net");
  eq(S.refusal(418, null).kind, "http");
});

test("fmtWait: 0, 59, 60, 61, 3599, 3600, 3660, 86399 s — RU and EN", () => {
  const cases = [
    [0, "через 0 с", "in 0 s"], [59, "через 59 с", "in 59 s"], [60, "через 1 мин", "in 1 min"],
    [61, "через 2 мин", "in 2 min"],                                        // минуты — вверх
    [3599, "через 1 ч", "in 1 h"],                                          // 60 минут вверх — это час
    [3600, "через 1 ч", "in 1 h"], [3660, "через 1 ч 1 мин", "in 1 h 1 min"], [3601, "через 1 ч 1 мин", "in 1 h 1 min"],
    [86399, "через 24 ч", "in 24 h"], [5400, "через 1 ч 30 мин", "in 1 h 30 min"],
    [59.2, "через 1 мин", "in 1 min"],                                      // 59,2 с вверх — 60 с — минута
  ];
  for (const [s, ru, en] of cases) {
    eq(S.fmtWait(s, "ru"), ru, String(s));
    eq(S.fmtWait(s, "en"), en, String(s));
  }
  eq(S.fmtWait(-5, "ru"), "через 0 с");
  eq(S.fmtWait("x", "en"), "in 0 s");
  eq(S.fmtWait(90, "de"), "in 2 min");                                      // нет языка — английский
  eq(S.fmtWait(3660, "ru", { s: "{n}s", m: "{n}m", h: "{h}h", hm: "{h}h{m}m" }), "1h1m");    // свой словарь
});

test("GB without extra zeros", () => {
  eq(S.fmtGb(10e9, "ru"), "10");
  eq(S.fmtGb(1.5e9, "ru"), "1,5");
  eq(S.fmtGb(1.5e9, "en"), "1.5");
  eq(S.fmtGb(1.25e9, "en"), "1.25");
  eq(S.fmtGb(1.254e9, "en"), "1.25");
  eq(S.fmtGb(0, "en"), "0");
});

test("history in the browser: entry, clean, 30 newest first", () => {
  const r = { t: 1000, ping: 24, jitter: 3, down: { bps: 87e6 }, up: { bps: 41e6 }, loaded: { show: true, deltaMs: 35 },
              via: "direct", proto: "http/1.1", flags: ["vpn"], error: null, ip: "198.51.100.9" };
  const e = S.historyEntry(r);
  eq(e, { t: 1000, ping: 24, jitter: 3, down: 87e6, up: 41e6, loaded: 35, via: "direct", proto: "http/1.1", flags: ["vpn"] });
  eq(S.historyEntry({ t: 1, loaded: { show: false, deltaMs: null } }).loaded, null);
  eq(S.historyClean("x"), []);
  eq(S.historyClean([null, { t: "x" }, { t: 5, ping: "9", flags: [1, "a"], ip: "1.2.3.4" }]),
     [{ t: 5, ping: null, jitter: null, down: null, up: null, loaded: null, via: null, proto: null, flags: ["a"] }]);
  let list = [];
  for (let i = 0; i < 35; i++) list = S.historyPush(list, S.historyEntry({ t: i }));
  eq(list.length, 30);
  eq([list[0].t, list[29].t], [34, 5]);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); } catch (e) { failed++; console.error("FAIL " + name + "\n" + (e && e.stack || e)); }
}
if (failed) { console.error(`SPEED CALC FAILED: ${failed} из ${tests.length}`); process.exit(1); }
console.log(`SPEED CALC OK: ${n} проверок в ${tests.length} тестах`);
