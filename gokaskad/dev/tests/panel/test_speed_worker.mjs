// BC4 (SPEC-browser-channel §6.2): движок static/speed-worker.js на подменённых часах и сервере — без браузера.
// Сервер-подделка держит остатки сторон как настоящий (резерв при старте запроса, 429 budget сверх остатка), канал —
// общая полоса на открытые потоки. Запуск: node tests/panel/test_speed_worker.mjs -> «SPEED WORKER OK: …».
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../lib/kaskad/panel/static");
const CALC = path.join(STATIC, "speed-calc.js");
const WORKER = path.join(STATIC, "speed-worker.js");

// как в воркере: self с importScripts — грузит speed-calc.js в тот же контекст (а postMessage нет — без автозапуска)
const ctx = vm.createContext({ console, URL, URLSearchParams, AbortController, Blob, Promise, Map, Set, Math, Date,
                               JSON, Error, Object, Array, String, Number, Uint8Array });
ctx.self = ctx;
ctx.importScripts = (name) => vm.runInContext(fs.readFileSync(path.join(STATIC, name), "utf8"), ctx, { filename: name });
vm.runInContext(fs.readFileSync(WORKER, "utf8"), ctx, { filename: WORKER });
const W = ctx.SpeedWorker;
const C = ctx.SpeedCalc.C;
const MIB = 1024 * 1024;

let checks = 0;
const ok = (v, m) => { checks++; assert.ok(v, m); };
const eq = (a, b, m) => { checks++; assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), b, m || "eq"); };
const near = (a, b, rel, m) => { checks++; assert.ok(a !== null && Math.abs(a - b) <= b * rel, `${m || ""}: ${a} ≉ ${b} ±${rel * 100}%`); };

function aborted() { const e = new Error("aborted"); e.name = "AbortError"; return e; }

// Мир: часы, таймеры, сервер, канал. o: {downBps, upBps, limits, start: {status, body, headers}, serverLeft,
// down: (n) -> null | {status, body}, upFail, finishFail, finish: {status, body}, rtt, timing: "ok" | "late" | "none",
// queue: (k) -> мс очереди k-го отклика (Resource Timing), nextHop, streamCap — потолок одного потока скачивания, бит/с}
function world(o) {
  let now = 0, seq = 0;
  const timers = [];
  const msgs = [];
  const log = { start: 0, finish: 0, ping: 0, down: [], up: [], logs: [], cut: 0, downEnd: [], timingReads: 0 };
  const records = new Map();                         // Resource Timing откликов: url -> {rec, reads}
  const lim = Object.assign({ down_bytes: 800e6, up_bytes: 800e6, req_down_max: 256 * MIB, req_up_max: 64 * MIB, streams_max: 4,
                              side_s_max: 10 }, o.limits || {});
  const srv = { down: o.serverLeft ? o.serverLeft.down : lim.down_bytes, up: o.serverLeft ? o.serverLeft.up : lim.up_bytes };
  const active = { down: 0, up: 0 };
  const rtt = o.rtt || 20;
  const env = {
    now: () => now,
    setTimeout: (fn, ms) => { const t = { at: now + Math.max(0, ms || 0), fn, id: ++seq }; timers.push(t); return t.id; },
    clearTimeout: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    timing: (u) => {
      log.timingReads++;
      const r = records.get(u);
      if (!r || o.timing === "none") return null;
      r.reads++;
      return o.timing === "late" && r.reads === 1 ? null : r.rec;     // «late»: запись — только следующей задачей
    },
    makeBlob: (n) => new Blob([new Uint8Array(n)]),
    post: (m) => msgs.push(Object.assign({ at: now }, m)),
    log: (...a) => log.logs.push(a.join(" ")),
  };
  const later = (ms, signal) => new Promise((resolve, reject) => {
    const id = env.setTimeout(resolve, ms);
    if (signal) signal.addEventListener("abort", () => { env.clearTimeout(id); reject(aborted()); }, { once: true });
  });
  const H = (h) => ({ get: (k) => { const f = Object.keys(h || {}).find((x) => x.toLowerCase() === k.toLowerCase()); return f ? h[f] : null; } });
  const json = (status, body, headers) => ({ ok: status >= 200 && status < 300, status, headers: H(headers),
                                             text: async () => JSON.stringify(body), arrayBuffer: async () => new ArrayBuffer(0) });
  // скорость канала: постоянная или по времени от начала стороны (o.downBpsAt(ms) — для остановки потока в конце)
  const sideT = (side) => { const m = msgs.find((x) => x.type === "phase" && x.phase === side); return m ? now - m.at : 0; };
  const bytesPerMs = (side) => (side === "down" ? (o.downBpsAt ? o.downBpsAt(sideT("down")) : o.downBps) : o.upBps) / 8 / 1000;

  env.fetch = async (u, init) => {
    const q = new URL(u, "https://node.example.com");
    const route = q.pathname.replace(/^.*\/api\/host\/speed\//, "");
    ok(q.pathname.startsWith("/app-x/api/host/speed/"), "путь от base: " + q.pathname);
    eq(init.credentials, "same-origin");
    if (init.method === "POST") eq([init.headers["X-Kaskad-Admin"], init.headers["X-Kaskad-Lang"]], ["1", "ru"]);
    await later(rtt / 2, init.signal);
    if (route === "start") {
      log.start++;
      const st = o.start || {};
      return json(st.status || 200, st.body || { ok: true, id: "L1", until_s: 45, limits: lim, via: o.via || "direct", vpn: false }, st.headers);
    }
    if (route === "finish") {
      log.finish++;
      eq(JSON.parse(init.body), { id: "L1" });
      if (o.finishFail) throw new TypeError("network");
      if (o.finish) return json(o.finish.status, o.finish.body, o.finish.headers);
      return json(200, { ok: true, bytes_down: 1, bytes_up: 2, up: { bps: 41e6, window_ms: 4000, buckets: [] },
                         cpu_limited: { down: false, up: false } });
    }
    eq(q.searchParams.get("id"), "L1");
    ok(/^[a-z0-9]+$/.test(q.searchParams.get("r") || ""), "r — против кэша");
    eq(init.cache, "no-store");
    if (route === "ping") {
      log.ping++;
      const q0 = o.queue ? o.queue(log.ping) : 0, t0 = now - rtt / 2;
      records.set(u, { reads: 0, rec: { startTime: t0, requestStart: t0 + 0.5 + q0, responseStart: t0 + 0.5 + q0 + rtt,
                                        nextHopProtocol: o.nextHop || "", serverTiming: [{ name: "app", duration: 1 }] } });
      await later(rtt / 2, init.signal);
      return { ok: true, status: 204, headers: H({ "Server-Timing": "app;dur=1" }), arrayBuffer: async () => new ArrayBuffer(0) };
    }
    if (route === "down") {
      const n = Number(q.searchParams.get("bytes"));
      log.down.push(n);
      const bad = o.down && o.down(n, log.down.length);
      if (bad) return json(bad.status, bad.body, bad.headers);
      if (n > srv.down) return json(429, { ok: false, error: "budget", code: "budget", data: { reason: "budget", left: srv.down } });
      srv.down -= n;                                     // резерв — до первого байта
      let left = n, cancelled = false, counted = false;
      const k = log.down.length - 1;
      const body = {
        getReader: () => {
          active.down++; counted = true;
          const fin = () => { if (counted) { counted = false; active.down--; } };
          return {
            read: () => new Promise((resolve, reject) => {
              if (cancelled) { resolve({ done: true }); return; }
              const pump = () => env.setTimeout(() => {
                if (cancelled) { resolve({ done: true }); return; }
                if (left <= 0) { fin(); log.downEnd[k] = now; resolve({ done: true }); return; }
                if (!(bytesPerMs("down") > 0)) { pump(); return; }        // канал стоит: байт нет вовсе
                const share = bytesPerMs("down") / Math.max(1, active.down);    // полоса — поровну на открытые потоки…
                const per = o.streamCap ? Math.min(share, o.streamCap / 8 / 1000) : share;   // …не больше потолка потока
                const c = Math.min(left, Math.max(1, Math.floor(per * 10)));
                left -= c;
                resolve({ done: false, value: { byteLength: c } });
              }, 10);
              pump();
            }),
            cancel: () => { if (!cancelled && left > 0) { log.cut++; log.downEnd[k] = now; } cancelled = true; fin(); },
          };
        },
      };
      return { ok: true, status: 200, headers: H({}), body };
    }
    throw new Error("неизвестный маршрут " + route);
  };

  class XHR {
    constructor() { this.upload = {}; this.status = 0; this.responseText = ""; this.h = {}; }
    open(m, u) { this.m = m; this.u = new URL(u, "https://node.example.com"); }
    setRequestHeader(k, v) { this.h[k] = v; }
    getResponseHeader() { return null; }
    send(body) {
      eq([this.m, this.h["X-Kaskad-Admin"], this.h["X-Kaskad-Lang"]], ["POST", "1", "ru"]);
      ok(this.u.pathname === "/app-x/api/host/speed/up" && this.u.searchParams.get("id") === "L1");
      const size = body.size;
      log.up.push(size);
      if (o.upFail) { this.tid = env.setTimeout(() => this.onerror(), 50); return; }
      if (size > srv.up) {
        this.tid = env.setTimeout(() => { this.status = 429; this.responseText = JSON.stringify({ ok: false, code: "budget", data: { reason: "budget", left: srv.up } }); this.onload(); }, rtt);
        return;
      }
      srv.up -= size;
      active.up++;
      this.live = true;
      let sent = 0;
      const tick = () => {
        const c = Math.min(size - sent, Math.max(1, Math.floor(bytesPerMs("up") * 10 / Math.max(1, active.up))));
        sent += c;
        this.upload.onprogress({ loaded: sent });
        if (sent >= size) {
          active.up--; this.live = false;
          this.tid = env.setTimeout(() => { this.status = 200; this.responseText = JSON.stringify({ ok: true, bytes: size }); this.onload(); }, rtt / 2);
        } else this.tid = env.setTimeout(tick, 10);
      };
      this.tid = env.setTimeout(tick, rtt / 2);
    }
    abort() {
      env.clearTimeout(this.tid);
      if (this.live) { this.live = false; active.up--; }
      if (this.onabort) this.onabort();
    }
  }
  env.XHR = XHR;

  async function drive(promise, limitMs) {
    let done = false, out, err;
    promise.then((v) => { done = true; out = v; }, (e) => { done = true; err = e; });
    while (!done) {
      for (let i = 0; i < 4 && !done; i++) await new Promise((r) => setImmediate(r));
      if (done) break;
      if (!timers.length) throw new Error("движок встал на " + now + " мс");
      timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const t = timers.shift();
      now = Math.max(now, t.at);
      t.fn();
      if (now > (limitMs || 60000)) throw new Error("дольше " + (limitMs || 60000) + " мс");
    }
    if (err) throw err;
    return out;
  }
  return { env, msgs, log, drive, now: () => now, active };
}

function engine(w, opts) {
  return W.createEngine(w.env, Object.assign({ base: "/app-x/", lang: "ru", phone: false }, opts || {}));
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("full run on 100/50 Mbit/s: idle, ramp, stable end, upload from finish, under load", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6 });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error, r.stopped, r.finished], [true, null, null, true]);
  eq(w.msgs.filter((m) => m.type === "phase").map((m) => m.phase), ["start", "idle", "down", "up", "finish"]);
  eq([w.log.start, w.log.finish], [1, 1]);
  eq([r.ping, r.jitter, r.idle], [19, 0, { good: 20, total: 20, rt: 20 }]);   // 20 мс по записи − 1 мс сервера
  ok(r.rt.load >= 8 && r.rt.load === r.rt.loadTotal, "под нагрузкой — все с записью: " + JSON.stringify(r.rt));
  near(r.down.bps, 100e6, 0.05, "скачивание");
  eq(r.down.endedBy, "stable");
  ok(r.down.windowMs >= C.WINDOW_MIN_MS && r.down.streams >= 2 && r.down.streams <= 4, JSON.stringify(r.down));
  eq([r.up.bps, r.up.windowMs, r.up.endedBy], [41e6, 4000, "stable"]);  // итог отдачи — по часам сервера
  near(r.up.clientBps, 50e6, 0.05, "отдача по onprogress");
  eq(r.proto, "http/1.1");
  eq(r.loaded, { show: true, deltaMs: 0, scale: "barely", reason: null });
  eq(r.flags, []);
  eq(r.bytes, { down: 1, up: 2 });
  const prog = w.msgs.filter((m) => m.type === "progress");
  ok(prog.length > 50 && prog.every((m) => m.side === "down" || m.side === "up"));
  // ступени: первая — 256 КиБ, дальше — по скорости потока (≈ 1 с), не больше лимита сторон
  eq(w.log.down[0], 256 * 1024);
  ok(w.log.down.every((n) => C.DOWN_STEPS.includes(n)), "ступени скачивания");
  ok(w.log.up.every((n) => C.UP_STEPS.includes(n)), "ступени отдачи");
  ok(w.log.down.reduce((a, b) => a + b, 0) <= 800e6);
  eq(w.active, { down: 0, up: 0 });                                    // все потоки закрыты
  // ход для экрана (BC5): отклики покоя по одному, готовые итоги — после покоя и после скачивания, до своих фаз
  const smp = w.msgs.filter((m) => m.type === "sample");
  eq([smp.length, smp.every((m) => m.ms === 19 && m.good === true)], [20, true]);
  const kinds = w.msgs.filter((m) => m.type === "phase" || m.type === "partial")
    .map((m) => (m.type === "phase" ? m.phase : "partial:" + Object.keys(m).filter((k) => k !== "type" && k !== "at").join(",")));
  eq(kinds, ["start", "idle", "partial:ping,jitter", "down", "partial:down", "up", "finish"]);
  const pd = w.msgs.find((m) => m.type === "partial" && "down" in m);
  eq([w.msgs.find((m) => m.type === "partial" && "ping" in m).ping, pd.down], [19, r.down.bps]);
});

test("a fast channel ends by the rest of the lease: no 429, sum of requests ≤ the rest, «fast»", async () => {
  const w = world({ downBps: 2e9, upBps: 2e9, limits: { down_bytes: 30e6, up_bytes: 20e6 } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error], [true, null]);
  eq([r.down.endedBy, r.up.endedBy], ["left", "left"]);
  ok(w.log.down.reduce((a, b) => a + b, 0) <= 30e6 && w.log.up.reduce((a, b) => a + b, 0) <= 20e6);
  ok(30e6 - w.log.down.reduce((a, b) => a + b, 0) < 256 * 1024, "остаток — меньше наименьшей ступени");
  ok(r.down.bps > 0.5e9, "цифра есть: " + r.down.bps);
  ok(r.flags.includes("fast"));
});

test("4 Gbit/s with the usual 800 MB: the side ends by the rest, the number is close", async () => {
  const w = world({ downBps: 4e9, upBps: 4e9 });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.down.endedBy, r.up.endedBy], [true, "left", "left"]);
  near(r.down.bps, 4e9, 0.25, "скачивание 4 Гбит/с");
  ok(r.flags.includes("fast"));
  ok(w.log.down.reduce((a, b) => a + b, 0) <= 800e6);
});

test("429 budget from the server is the quiet end of the side", async () => {
  const w = world({ downBps: 200e6, upBps: 100e6, serverLeft: { down: 3 * MIB, up: 800e6 } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error, r.down.endedBy], [true, null, "left"]);
  ok(r.sideError === undefined, "429 budget — без текста");
});

test("429 streams ends the side with the server's text; the test goes on", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6,
                    down: (n, k) => (k === 3 ? { status: 429, body: { ok: false, error: "много потоков", code: "streams", data: { reason: "streams" } } } : null) });
  const r = await w.drive(engine(w).run());
  eq([r.error, r.down.endedBy, r.sideError.kind, r.sideError.text], [null, "error", "streams", "много потоков"]);
  ok(r.up && r.up.bps === 41e6);
  ok(w.log.logs.some((l) => l.includes("streams")), "запись в консоль");
});

test("a refused start: no lease, no finish, the reason with retry_after", async () => {
  const w = world({ downBps: 1e6, upBps: 1e6, start: { status: 409, body: { ok: false, error: "идёт другая", code: "busy", data: { busy: true, retry_after: 30 } } } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error.kind, r.error.retryAfter, r.error.text], [false, "busy", 30, "идёт другая"]);
  eq([w.log.finish, w.log.ping], [0, 0]);
  const w2 = world({ downBps: 1e6, upBps: 1e6, start: { status: 429, body: { ok: false, error: "x", code: "hour", data: { reason: "hour", used: 6, limit: 6, retry_after: 600 } } } });
  const r2 = await w2.drive(engine(w2).run());
  eq([r2.error.kind, r2.error.n, r2.error.retryAfter], ["hour", 6, 600]);
  const w3 = world({ downBps: 1e6, upBps: 1e6, start: { status: 429, body: null, headers: { "Retry-After": "5" } } });
  eq((await w3.drive(engine(w3).run())).error.kind, "server_busy");
});

test("stop (hidden tab) in the middle of the download: requests cut, finish once, «hidden»", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6 });
  const e = engine(w);
  w.env.setTimeout(() => e.stop("hidden"), 4000);
  const r = await w.drive(e.run());
  eq([r.ok, r.error, r.stopped, r.finished, w.log.finish], [false, null, "hidden", true, 1]);
  eq(r.up, null);
  eq(r.down.endedBy, "stopped");
  ok(r.flags.includes("hidden"));
  eq(w.active, { down: 0, up: 0 });
  ok(w.now() < 4500, "прервано сразу: " + w.now());
});

test("stop while start is on the way: the lease it got is closed", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6 });
  const e = engine(w);
  w.env.setTimeout(() => e.stop("user"), 5);
  const r = await w.drive(e.run());
  eq([r.stopped, w.log.start, w.log.finish, w.log.ping], ["user", 1, 1, 0]);
});

test("an upload error: finish answered — no «connection lost»; finish silent — it is", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6, upFail: true });
  const r = await w.drive(engine(w).run());
  eq([r.error, r.up.endedBy, r.up.bps, r.finished], [null, "error", 41e6, true]);
  const w2 = world({ downBps: 100e6, upBps: 50e6, upFail: true, finishFail: true });
  const r2 = await w2.drive(engine(w2).run());
  eq([r2.ok, r2.error.kind, r2.finished], [false, "net", false]);
});

test("the lease ended (410) during the download: the test stops with its reason", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6,
                    down: (n, k) => (k === 4 ? { status: 410, body: { ok: false, error: "истекло", code: "expired", data: { expired: true } } } : null) });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error.kind, r.up, w.log.finish], [false, "expired", null, 1]);
});

test("a phone sends smaller pieces of the upload (≤ 16 MiB)", async () => {
  const w = world({ downBps: 50e6, upBps: 500e6 });
  const r = await w.drive(engine(w, { phone: true }).run());
  ok(r.ok && Math.max(...w.log.up) <= 16 * MIB, "наибольший кусок " + Math.max(...w.log.up));
});

test("proxy (nginx with http2): one connection — no «under load», the mark", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6, via: "proxy" });
  const r = await w.drive(engine(w).run());
  eq([r.proto, r.loaded.reason, r.flags], ["h2", "one_conn", ["one_conn"]]);
});

// ── REV-bc4 З-1…З-3, П-2: каждая проверка падает на прежнем коде (порчи М5, М6, М9, М10, М11 — R-bc5) ──
test("З-1: an upload error, then finish refuses (410, 5xx): the server's reason, not «connection lost»", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6, upFail: true,
                    finish: { status: 410, body: { ok: false, error: "истекло", code: "expired", data: { expired: true } } } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.error.kind, r.error.text, r.finished], [false, "expired", "истекло", false]);
  const w2 = world({ downBps: 100e6, upBps: 50e6, finish: { status: 503, body: { ok: false, error: "сбой" } } });
  const r2 = await w2.drive(engine(w2).run());
  eq([r2.ok, r2.error.kind, r2.finished], [false, "server", false]);
});

test("М5: the end of a side cuts its open requests at once (the next side does not wait for them)", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6 });
  const r = await w.drive(engine(w).run());
  eq(r.down.endedBy, "stable");
  const upAt = w.msgs.find((m) => m.type === "phase" && m.phase === "up").at;
  const lastDown = w.msgs.filter((m) => m.type === "progress" && m.side === "down").pop().at;
  ok(w.log.cut >= 1, "открытый запрос оборван: " + w.log.cut);
  ok(upAt - lastDown <= 150, "отдача — сразу после конца скачивания: " + (upAt - lastDown) + " мс");
  ok(Math.max(...w.log.downEnd.filter((x) => x !== undefined)) <= lastDown + 30, "ни один запрос не докачан после конца");
});

test("М10/М11: a fast channel whose rest lasts ≥ 1 s after the last stream — the number within 5 %", async () => {
  // один поток — не больше 600 Мбит/с, канал — 2 Гбит/с: разгон потоков 1→4 честно медленнее итога
  const w = world({ downBps: 2e9, upBps: 2e9, streamCap: 600e6, limits: { down_bytes: 700e6, up_bytes: 600e6 } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.down.endedBy, r.up.endedBy, r.down.streams], [true, "left", "left", 4]);
  near(r.down.bps, 2e9, 0.05, "скачивание 2 Гбит/с по последним ≤ 2 с: окно — до первого потока без работы");
  ok(r.flags.includes("fast"));
});

test("М9: «fast» by the upload alone (the download had a wide rest)", async () => {
  const w = world({ downBps: 100e6, upBps: 2e9, limits: { down_bytes: 800e6, up_bytes: 30e6 } });
  const r = await w.drive(engine(w).run());
  eq([r.ok, r.down.endedBy, r.up.endedBy], [true, "stable", "left"]);
  ok(r.flags.includes("fast"), JSON.stringify(r.flags));
});

test("М6: 409 without retry_after in the body takes the header Retry-After", async () => {
  const w = world({ downBps: 1e6, upBps: 1e6, start: { status: 409, body: { ok: false, error: "идёт другая", code: "busy", data: { busy: true } },
                                                        headers: { "Retry-After": "12" } } });
  const r = await w.drive(engine(w).run());
  eq([r.error.kind, r.error.retryAfter], ["busy", 12]);
});

test("П-2: a stream that stood still at the end of a side by time stays in the window (no tail cut)", async () => {
  // скорость растёт на 10 Мбит/с в секунду — ровной нет, конец по 10 с; последние 300 мс канал стоит
  const at = (ms) => (ms >= 9700 ? 0 : 30e6 + ms * 10e3);
  const w = world({ downBpsAt: at, upBps: 50e6 });
  const r = await w.drive(engine(w).run());
  eq(r.down.endedBy, "time");
  ok(r.down.windowEnd >= 9950, "остановка в конце — часть окна: " + JSON.stringify(r.down));
  ok(r.down.windowMs >= 4000, JSON.stringify(r.down));
});

test("З-3: Resource Timing — a queue > 20 ms is not good; a late record is still read; no records — no «under load»", async () => {
  // все отклики ждали очереди 30 мс: в покое годных нет — отклика нет, под нагрузкой — «queue»
  const w = world({ downBps: 100e6, upBps: 50e6, queue: () => 30 });
  const r = await w.drive(engine(w).run());
  eq([r.ping, r.idle.good, r.idle.total, r.idle.rt, r.loaded.reason], [null, 0, 20, 20, "queue"]);
  // запись приходит следующей задачей после тела — всё равно прочитана: всё годно, строка есть
  const w2 = world({ downBps: 100e6, upBps: 50e6, timing: "late" });
  const r2 = await w2.drive(engine(w2).run());
  eq([r2.ping, r2.idle.rt, r2.loaded.show], [19, 20, true]);
  ok(r2.rt.load === r2.rt.loadTotal && r2.rt.load >= 8, JSON.stringify(r2.rt));
  // записей нет вовсе (браузер их не даёт): отклик в покое — по часам, «под нагрузкой» не показываем
  const w3 = world({ downBps: 100e6, upBps: 50e6, timing: "none" });
  const r3 = await w3.drive(engine(w3).run());
  eq([r3.ping, r3.idle.good, r3.idle.rt, r3.rt.load, r3.loaded.reason], [19, 20, 0, 0, "queue"]);
  ok(r3.rt.loadTotal >= 8, JSON.stringify(r3.rt));
});

test("П-3: front with an empty nextHop — the line «under load» is hidden, but no mark «one connection»", async () => {
  const w = world({ downBps: 100e6, upBps: 50e6, via: "front" });
  const r = await w.drive(engine(w).run());
  eq([r.proto, r.loaded.reason, r.flags], ["h2", "one_conn", []]);
  const w2 = world({ downBps: 100e6, upBps: 50e6, via: "front", nextHop: "h2" });
  const r2 = await w2.drive(engine(w2).run());
  eq([r2.proto, r2.flags], ["h2", ["one_conn"]]);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); } catch (e) { failed++; console.error("FAIL " + name + "\n" + (e && e.stack || e)); }
}
if (failed) { console.error(`SPEED WORKER FAILED: ${failed} из ${tests.length}`); process.exit(1); }
console.log(`SPEED WORKER OK: ${checks} проверок в ${tests.length} тестах`);
