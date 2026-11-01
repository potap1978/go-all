// speed-worker.js — движок замера «от вашего устройства до сервера» (SPEC-browser-channel §6.2). Web Worker; расчёт —
// speed-calc.js (importScripts), экран — не здесь (BC5). Сеть — только маршруты /api/host/speed/* той же панели.
//
// Сообщения страницы -> воркер:
//   {type: "start", base, lang, phone, nextHop?}  base — путь панели («/», «/<префикс>/»; нет — каталог воркера),
//                                                lang — язык текстов сервера (X-Kaskad-Lang), phone — меньшие куски отдачи
//   {type: "stop", reason: "user" | "hidden"}     прервать: открытые запросы обрываются, аренда закрывается (finish)
// Воркер -> страница:
//   {type: "phase", phase: "start" | "idle" | "down" | "up" | "finish"}
//   {type: "progress", side: "down" | "up", t, bps}          раз в 100 мс: мс от начала стороны, текущая скорость
//   {type: "sample", ms, good}                               отклик в покое (для цифры хода; в итог идёт медиана)
//   {type: "partial", ping, jitter} | {type: "partial", down} готовое по ходу: отклик после покоя, скачивание после стороны
//   {type: "done", result}                                   итог (см. engine.run), всегда ровно один
//
// Движок сделан фабрикой createEngine(env): часы, таймеры, fetch, XHR и Resource Timing приходят снаружи, поэтому
// тест (tests/panel/test_speed_worker.mjs) гоняет его на подменённых часах и сервере без браузера.
(function (root) {
  "use strict";
  // speed-calc.js — той же версии, что воркер (?v= панели: кэш браузера не подсунет прежний расчёт после обновления)
  if (typeof root.SpeedCalc === "undefined" && typeof root.importScripts === "function") {
    const v = root.location && /^\?v=[\w.%-]{1,64}$/.test(root.location.search) ? root.location.search : "";
    root.importScripts("speed-calc.js" + v);
  }
  const S = root.SpeedCalc;
  const C = S.C;

  class Stop extends Error {
    constructor(reason) { super("stopped"); this.name = "Stop"; this.reason = reason; }
  }
  class Refused extends Error {
    constructor(info) { super(info.kind); this.name = "Refused"; this.info = info; }
  }
  class Cut extends Error {                   // запрос оборван концом своей стороны — не ошибка
    constructor() { super("cut"); this.name = "Cut"; }
  }

  // env: {now(), setTimeout(fn, ms), clearTimeout(id), fetch(url, init), XHR (конструктор XMLHttpRequest),
  //       timing(url) -> запись Resource Timing | null, makeBlob(size) -> Blob случайных байт, post(msg), log(...)}
  // opts: {base, lang, phone, nextHop}
  function createEngine(env, opts) {
    const o = opts || {};
    const base = String(o.base || "/").replace(/\/?$/, "/");
    const lang = o.lang === "ru" ? "ru" : "en";
    let stopped = null;                       // причина прерывания
    const live = new Set();                   // {abort()} всех открытых запросов
    const waits = new Set();                  // разбудить спящих при прерывании
    let lease = null;
    let upBase = null;

    const post = (m) => { try { env.post(m); } catch (e) { /* страница ушла */ } };
    const rnd = () => Math.random().toString(36).slice(2, 10);
    const url = (route, q) => {
      const p = new URLSearchParams();
      Object.keys(q || {}).forEach((k) => p.set(k, String(q[k])));
      p.set("r", rnd());
      return base + "api/host/speed/" + route + "?" + p.toString();
    };
    const check = () => { if (stopped) throw new Stop(stopped); };

    function sleep(ms) {
      return new Promise((resolve) => {
        const w = { done: resolve };
        w.id = env.setTimeout(() => { waits.delete(w); resolve(); }, ms);
        waits.add(w);
      });
    }

    function stop(reason) {
      if (stopped) return;
      stopped = reason || "user";
      live.forEach((h) => { try { h.abort(); } catch (e) { /* уже закрыт */ } });
      waits.forEach((w) => { env.clearTimeout(w.id); w.done(); });
      waits.clear();
    }

    async function bodyOf(res) {
      try { const t = await res.text(); return t ? JSON.parse(t) : null; } catch (e) { return null; }
    }
    async function refusedOf(res) {
      const h = res.headers && res.headers.get ? res.headers.get("Retry-After") : null;
      return new Refused(S.refusal(res.status, await bodyOf(res), h));
    }

    // fetch с обрывом по stop(), по концу стороны (``side.end`` — Cut) и по сроку ``ms`` (срок — net).
    async function call(u, init, ms, side, keep) {
      const ctl = new AbortController();
      const h = { abort: () => ctl.abort() };
      if (!keep) live.add(h);                 // keep — finish: «Остановить» его не обрывает
      let late = false;
      const tid = ms ? env.setTimeout(() => { late = true; ctl.abort(); }, ms) : null;
      try {
        return await env.fetch(u, Object.assign({ credentials: "same-origin", cache: "no-store", signal: ctl.signal }, init));
      } catch (e) {
        if (stopped && !late) throw new Stop(stopped);
        if (side && side.end && !late) throw new Cut();
        throw new Refused(S.refusal(0, null));
      } finally {
        if (tid !== null) env.clearTimeout(tid);
        live.delete(h);
      }
    }

    const headers = (post_) => {
      const h = { Accept: "application/json", "X-Kaskad-Lang": lang };
      if (post_) { h["X-Kaskad-Admin"] = "1"; h["Content-Type"] = "application/json"; }
      return h;
    };

    async function api(route, body, ms, keep) {
      const res = await call(base + "api/host/speed/" + route, { method: "POST", headers: headers(true), body: JSON.stringify(body || {}) }, ms, null, keep);
      if (!res.ok) throw await refusedOf(res);
      const doc = await bodyOf(res);
      if (!doc || doc.ok === false) throw new Refused(S.refusal(res.status || 500, doc));
      return doc;
    }

    // Один отклик -> {ms, good, rt}; заодно nextHopProtocol для протокола (front).
    let nextHop = o.nextHop || "";
    async function ping(side) {
      const u = url("ping", { id: lease });
      const t0 = env.now();
      const res = await call(u, { method: "GET", headers: headers(false) }, 10000, side);
      if (!res.ok) throw await refusedOf(res);
      try { await res.arrayBuffer(); } catch (e) { /* 204 */ }
      const wall = env.now() - t0;
      let e = env.timing(u);
      // запись Resource Timing браузер кладёт своей задачей по концу загрузки — может прийти после тела (REV-bc4 З-3):
      // ещё раз через задачу; нет и тогда — отсчёт без записи (rt: false)
      if (!e) { await sleep(0); e = env.timing(u); }
      if (e && e.nextHopProtocol) nextHop = e.nextHopProtocol;
      const st = e && Array.isArray(e.serverTiming) ? e.serverTiming.find((x) => x && x.name === "app") : null;
      const srv = st ? st.duration : S.serverMs(res.headers && res.headers.get ? res.headers.get("Server-Timing") : "");
      return S.sample(e, wall, srv);
    }

    const lastT = (series) => (series.length ? series[series.length - 1][0] : 0);

    // ── сторона ──
    // Первая оценка скорости потока — по первому целому запросу (256 КиБ), пока отсчётов за 1 с ещё нет (§6.2 п. 3).
    function probe(side, bytes, t0) {
      const ms = env.now() - t0;
      if (side.streamBps === null && ms > 0 && bytes > 0) side.streamBps = 8 * bytes * 1000 / ms;
    }

    async function getDown(size, side, slot) {
      const t0 = env.now();
      const res = await call(url("down", { id: lease, bytes: size }), { method: "GET", headers: headers(false) }, 0, side);
      if (!res.ok) throw await refusedOf(res);
      const reader = res.body.getReader();
      const h = { abort: () => { try { reader.cancel(); } catch (e) { /* закрыт */ } } };
      live.add(h);
      let got = 0;
      try {
        for (;;) {
          let r;
          try { r = await reader.read(); } catch (e) { check(); if (side.end) throw new Cut(); throw new Refused(S.refusal(0, null)); }
          if (r.done) break;
          got += r.value.byteLength;
          side.inflight.set(slot, got);
          if (side.end) { h.abort(); break; }
        }
      } finally {
        live.delete(h);
        side.inflight.delete(slot);
        side.done += got;
      }
      check();
      if (got < size && !side.end) throw new Refused(S.refusal(0, null));
      if (got >= size) probe(side, got, t0);
    }

    function upBody(size) {
      const bsize = o.phone ? C.UP_BASE_PHONE : C.UP_BASE;
      if (!upBase) upBase = env.makeBlob(bsize);
      if (size <= upBase.size) return upBase.slice(0, size);
      const parts = [];
      for (let n = 0; n < size; n += upBase.size) parts.push(upBase);
      return new Blob(parts).slice(0, size);           // части — ссылки на базовый Blob, не копии
    }

    function putUp(size, side, slot) {
      const t0 = env.now();
      return new Promise((resolve, reject) => {
        const x = new env.XHR();
        const h = { abort: () => { try { x.abort(); } catch (e) { /* закрыт */ } } };
        let sent = 0;
        const end = (err) => {
          live.delete(h);
          side.inflight.delete(slot);
          side.done += sent;
          if (err) reject(err); else resolve();
        };
        x.open("POST", url("up", { id: lease }));
        x.setRequestHeader("X-Kaskad-Admin", "1");
        x.setRequestHeader("X-Kaskad-Lang", lang);
        x.setRequestHeader("Content-Type", "application/octet-stream");
        x.upload.onprogress = (e) => { sent = e.loaded; side.inflight.set(slot, sent); };
        x.onload = () => {
          if (x.status === 200) { sent = size; probe(side, size, t0); end(null); return; }
          let doc = null;
          try { doc = JSON.parse(x.responseText || "null"); } catch (e) { doc = null; }
          const ra = x.getResponseHeader ? x.getResponseHeader("Retry-After") : null;
          end(new Refused(S.refusal(x.status, doc, ra)));
        };
        // §6.2 п. 4: в direct отказ до чтения тела браузер видит как сброс — это ещё не «связь прервалась»
        x.onerror = () => end(stopped ? new Stop(stopped) : Object.assign(new Refused(S.refusal(0, null)), { upError: true }));
        x.onabort = () => end(stopped ? new Stop(stopped) : new Cut());
        x.ontimeout = x.onerror;
        live.add(h);
        x.send(upBody(size));
      });
    }

    // Одна сторона: потоки 1→4, отсчёты каждые 100 мс, отклики под нагрузкой каждые 250 мс, конец по ровной скорости,
    // 10 с или остатку. -> {bps (по часам браузера), windowMs, streams, endedBy, loaded, upError?, error?}
    async function runSide(kind, left0) {
      const side = { kind, left: Math.max(0, Math.floor(left0 || 0)), steps: kind === "down" ? S.downSteps() : S.upSteps(o.phone),
                     series: [[0, 0]], done: 0, inflight: new Map(), active: 0, end: false, streamBps: null,
                     endedBy: null, loaded: [], error: null, upError: false, drainAt: null };
      const t0 = env.now();
      const rampMs = kind === "down" ? C.DOWN_RAMP_MS : C.UP_RAMP_MS;
      let slots = 0, rampAt = null, lastAdd = 0, prevBps = null, nextRamp = C.RAMP_STEP_MS;
      const tasks = [];

      const stream = async () => {
        const slot = ++slots;
        side.active++;
        try {
          while (!side.end && !stopped) {
            const size = S.sizeFor(side.left, side.steps, side.streamBps);
            if (!size) {                                       // остаток кончился — конец стороны по остатку
              // первый поток без работы, пока другие ещё качают: дальше потоков всё меньше — не часть окна (R-bc5)
              if (side.drainAt === null && side.active > 1) side.drainAt = env.now() - t0;
              break;
            }
            side.left -= size;                                 // начатый запрос — целиком, назад не возвращается
            await (kind === "down" ? getDown(size, side, slot) : putUp(size, side, slot));
          }
        } catch (e) {
          if (e instanceof Refused && e.info.kind === "budget") { /* конец стороны, тихо (§6.2 п. 3) */ }
          else if (e instanceof Stop || e instanceof Cut) { /* прервано или конец стороны */ }
          else if (e instanceof Refused && e.info.kind === "streams") {
            if (env.log) env.log("speed: 429 streams", kind);
            side.error = side.error || e.info;
            side.end = true;
          } else if (e instanceof Refused && e.upError && kind === "up") {
            side.upError = true;                               // итог решит finish
            side.end = true;
          } else {
            side.error = side.error || (e instanceof Refused ? e.info : S.refusal(0, null));
            side.fatal = true;
            side.end = true;
          }
        } finally {
          side.active--;
        }
      };
      const add = () => { tasks.push(stream()); };

      const pinger = (async () => {
        while (!side.end && !stopped) {
          await sleep(C.LOADED_GAP_MS);
          if (side.end || stopped) break;
          try { side.loaded.push(await ping(side)); } catch (e) { if (e instanceof Stop) break; }
        }
      })();

      add();
      while (!stopped) {
        await sleep(C.SAMPLE_MS);
        if (stopped) break;
        const t = env.now() - t0;
        let total = side.done;
        side.inflight.forEach((v) => { total += v; });
        side.series.push([t, total]);
        const cur = S.rate(side.series, Math.max(0, t - C.RATE_SPAN_MS), t);
        if (cur !== null) side.streamBps = cur / Math.max(1, side.active || slots);
        post({ type: "progress", side: kind, t, bps: cur });
        if (rampAt === null && t >= nextRamp) {
          nextRamp = t + C.RAMP_STEP_MS;
          const bps = S.rate(side.series, Math.max(0, t - C.RAMP_STEP_MS), t);
          const step = S.rampStep(slots, prevBps, bps);
          if (step === "add") { prevBps = bps; lastAdd = t; add(); }
          else if (step === "done") rampAt = lastAdd;
        }
        const why = S.stopReason(side.series, t, rampAt, rampMs);
        if (why) { side.endedBy = why; break; }
        if (side.end || side.active === 0) {
          side.endedBy = side.error ? "error" : side.upError ? "error" : "left";
          break;
        }
      }
      side.end = true;
      // открытые запросы стороны — оборвать (их часть сервер вернёт себе сам)
      live.forEach((h) => { try { h.abort(); } catch (e) { /* закрыт */ } });
      await Promise.all(tasks.map((p) => p.catch(() => null)));
      await pinger.catch(() => null);
      if (stopped) side.endedBy = "stopped";
      side.series = S.sideSeries(side.series, side.endedBy, side.drainAt);     // слив и хвост — не часть окна
      let w = S.windowOf(side.series, rampAt === null ? lastAdd : rampAt, rampMs);
      if (!(w.ms >= 1000) && side.endedBy === "left") {
        // очень быстрый канал: остаток кончился раньше конца разгона — последние ≤ 2 с (после последнего нового потока,
        // если с него прошла хотя бы секунда; иначе он ничего не изменил — от начала)
        const to = side.series[side.series.length - 1][0];
        const from = Math.max(to - lastAdd >= 1000 ? lastAdd : 0, to - C.STABLE_SPAN_MS);
        w = to > from ? { bps: S.rate(side.series, from, to), ms: to - from } : w;
      }
      return { bps: w.bps, windowMs: w.ms, windowEnd: lastT(side.series), streams: slots, endedBy: side.endedBy, loaded: side.loaded,
               upError: side.upError, error: side.error, fatal: !!side.fatal };
    }

    // Итог. Вид — см. шапку файла и §6.3.
    async function run() {
      const res = { ok: false, t: Date.now(), via: null, vpn: null, proto: null, ping: null, jitter: null,
                    idle: { good: 0, total: 0, rt: 0 }, rt: { load: 0, loadTotal: 0 }, down: null, up: null, loaded: null, cpuLimited: { down: null, up: null },
                    flags: [], bytes: { down: null, up: null }, stopped: null, error: null, finished: false };
      post({ type: "phase", phase: "start" });
      let st;
      try {
        check();
        st = await api("start", {}, 15000, true);             // не обрывается: выданную аренду закроет finish
      } catch (e) {
        res.stopped = stopped;
        res.error = e instanceof Refused ? e.info : null;
        if (!res.error && !stopped) res.error = S.refusal(0, null);
        return res;                                            // аренды нет — закрывать нечего
      }
      lease = st.id;
      res.via = st.via || null;
      res.vpn = st.vpn === undefined ? null : st.vpn;
      const lim = st.limits || {};
      let downSide = null, upSide = null, idleS = [];
      try {
        check();                                               // «Остановить» пришло, пока шёл start
        post({ type: "phase", phase: "idle" });
        for (let i = 0; i < C.WARM_N; i++) await ping();
        for (let i = 0; i < C.IDLE_N; i++) {
          const smp = await ping();
          idleS.push(smp);
          if (smp) post({ type: "sample", ms: smp.ms, good: !!smp.good });
          if (i < C.IDLE_N - 1) await sleep(C.IDLE_GAP_MS);
          check();
        }
        const idl = S.idle(idleS);
        res.ping = idl.ping; res.jitter = idl.jitter;
        res.idle = { good: idl.good, total: idl.total, rt: idleS.filter((x) => x && x.rt).length };
        res.proto = S.protoOf(res.via, nextHop);
        post({ type: "partial", ping: res.ping, jitter: res.jitter });
        check();
        post({ type: "phase", phase: "down" });
        downSide = await runSide("down", lim.down_bytes);
        if (downSide.fatal) throw new Refused(downSide.error);
        post({ type: "partial", down: downSide.bps });
        check();
        post({ type: "phase", phase: "up" });
        upSide = await runSide("up", lim.up_bytes);
        if (upSide.fatal) throw new Refused(upSide.error);
        check();
      } catch (e) {
        if (e instanceof Refused) res.error = e.info;
        else if (!(e instanceof Stop)) res.error = S.refusal(0, null);
      }
      res.stopped = stopped;
      post({ type: "phase", phase: "finish" });
      let fin = null, finErr = null;
      try {
        fin = await api("finish", { id: lease }, C.FINISH_TIMEOUT_MS, true);  // и после «Остановить»
      } catch (e) {
        fin = null;
        finErr = e instanceof Refused ? e.info : S.refusal(0, null);
      }
      // finish ответил отказом (410 — аренда кончилась по времени, 5xx…) — сервер жив: причина — его, а не «связь
      // прервалась» (REV-bc4 З-1, §6.2 п. 4: «связь прервалась» — только когда finish не ответил)
      const answered = !!fin || !!(finErr && finErr.status);
      res.finished = !!fin;
      if (!res.proto) res.proto = S.protoOf(res.via, nextHop);
      if (fin) {
        res.cpuLimited = fin.cpu_limited || res.cpuLimited;
        res.bytes = { down: fin.bytes_down === undefined ? null : fin.bytes_down, up: fin.bytes_up === undefined ? null : fin.bytes_up };
      }
      const fup = fin && fin.up ? fin.up : null;
      if (downSide) res.down = { bps: downSide.bps, windowMs: downSide.windowMs, windowEnd: downSide.windowEnd, streams: downSide.streams,
                                 endedBy: downSide.endedBy };
      if (upSide) {
        // итог отдачи — по часам сервера (finish.up), цифры onprogress в итог не идут
        res.up = { bps: fup && fup.bps ? fup.bps : null, windowMs: fup ? fup.window_ms || 0 : 0, streams: upSide.streams,
                   endedBy: upSide.endedBy, clientBps: upSide.bps, clientWindowMs: upSide.windowMs };
        if (upSide.upError && !answered && !res.error) res.error = S.refusal(0, null);   // сеть правда пропала
      }
      if (!fin && answered && !res.error && !stopped) res.error = finErr;
      if (!answered && !res.error && !stopped) res.error = S.refusal(0, null);
      const loads = (downSide ? downSide.loaded : []).concat(upSide ? upSide.loaded : []);
      res.rt = { load: loads.filter((x) => x && x.rt).length, loadTotal: loads.length };
      if (downSide && upSide && !res.error) {
        res.loaded = S.loaded(res.ping, downSide.loaded, upSide.loaded, res.cpuLimited, res.proto);
      }
      const sideErr = (downSide && downSide.error) || (upSide && upSide.error) || null;
      if (sideErr && !res.error) res.sideError = sideErr;      // 429 streams: сторона кончилась, текст сервера
      res.flags = S.flags({ via: res.via, vpn: res.vpn, proto: res.proto, nextHop, cpu: res.cpuLimited, down: res.down,
                            up: res.up && { endedBy: res.up.endedBy, windowMs: res.up.clientWindowMs }, hidden: stopped === "hidden" });
      res.ok = !res.error && !stopped;
      return res;
    }

    return { run, stop };
  }

  root.SpeedWorker = { createEngine, Stop, Refused };

  // ── сам воркер ──
  if (typeof root.importScripts === "function" && typeof root.postMessage === "function" && !root.SPEED_WORKER_NO_AUTO) {
    let engine = null;
    const makeBlob = (size) => {
      const chunk = new Uint8Array(C.UP_CHUNK);
      root.crypto.getRandomValues(chunk);
      const parts = [];
      for (let n = 0; n < size; n += C.UP_CHUNK) parts.push(chunk);
      return new Blob(parts);
    };
    const timing = (u) => {
      try {
        const abs = new URL(u, root.location.href).href;
        const list = root.performance.getEntriesByName(abs, "resource");
        root.performance.clearResourceTimings();               // буфер записей невелик: всё прочитанное — прочь
        return list.length ? list[list.length - 1] : null;
      } catch (e) { return null; }
    };
    try { root.performance.setResourceTimingBufferSize(1000); } catch (e) { /* нет — по умолчанию */ }
    root.onmessage = (ev) => {
      const m = ev.data || {};
      if (m.type === "start" && !engine) {
        const base = m.base || new URL(".", root.location.href).pathname;
        engine = createEngine({
          now: () => root.performance.now(), setTimeout: (f, ms) => root.setTimeout(f, ms), clearTimeout: (id) => root.clearTimeout(id),
          fetch: (u, i) => root.fetch(u, i), XHR: root.XMLHttpRequest, timing, makeBlob,
          post: (msg) => root.postMessage(msg), log: (...a) => root.console.error(...a),
        }, { base, lang: m.lang, phone: !!m.phone, nextHop: m.nextHop });
        engine.run().then((result) => root.postMessage({ type: "done", result }),
                          () => root.postMessage({ type: "done", result: { ok: false, error: S.refusal(0, null), stopped: null } }))
          .then(() => { engine = null; });
      } else if (m.type === "stop" && engine) {
        engine.stop(m.reason === "hidden" ? "hidden" : "user");
      }
    };
  }
})(typeof self !== "undefined" ? self : globalThis);
