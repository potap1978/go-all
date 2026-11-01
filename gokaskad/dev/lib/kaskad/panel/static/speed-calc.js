// speed-calc.js — чистые функции замера канала «от устройства до сервера» из браузера (SPEC-browser-channel §6.2–6.4).
// Ядро без DOM, без сети и без панели (k3/handoff/process/BLOCKS.md): работает в воркере (importScripts), на странице
// (<script>) и в node (vm или require) — тесты tests/panel/test_speed_calc.mjs. Единицы: время — мс, объём — байты,
// скорость — бит/с. Ряд байт — [[t, байт с начала стороны], …] по возрастанию t (t — мс от начала стороны).
// Готовых фраз нет, кроме коротких единиц fmtWait (§6.4: «через N мин»); их словарь можно передать свой.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.SpeedCalc = api;
})(typeof self !== "undefined" ? self : typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const KIB = 1024;
  const MIB = 1024 * 1024;
  const freeze = (o) => {
    Object.keys(o).forEach((k) => { if (Array.isArray(o[k])) Object.freeze(o[k]); });
    return Object.freeze(o);
  };

  // Числа хода замера (§6.2, §5). Сервер держит свои пределы сам; здесь — то, что решает клиент.
  const C = freeze({
    QUEUE_MS: 20,               // годный отклик: requestStart − startTime ≤ 20 мс (не ждал очереди и нового соединения)
    WARM_N: 2,                  // прогрев: 2 отклика не в счёт (соединения, TLS)
    IDLE_N: 20,                 // покой: 20 откликов…
    IDLE_GAP_MS: 100,           // …через 100 мс
    IDLE_MIN_GOOD: 10,          // годных меньше — отклик не показываем
    LOADED_GAP_MS: 250,         // под нагрузкой: отклик каждые 250 мс
    LOADED_MIN_GOOD: 8,
    LOADED_GOOD_SHARE: 2 / 3,
    SAMPLE_MS: 100,             // отсчёт байт стороны
    RAMP_STEP_MS: 300,          // +1 поток каждые 300 мс…
    RAMP_GROWTH: 0.10,          // …пока скорость растёт больше чем на 10 %…
    STREAMS_MAX: 4,             // …и потоков меньше 4
    DOWN_RAMP_MS: 1500,         // разгон после выхода на число потоков — в мусор (скачивание)
    UP_RAMP_MS: 2000,           // то же для отдачи (§6.2 п. 4: минимум 2 с разгона)
    WINDOW_MIN_MS: 4000,        // ранний конец — не раньше 4 с окна
    STABLE_SPAN_MS: 2000,       // последние 2 с против предыдущих 2 с…
    STABLE_DIFF: 0.05,          // …отличаются не больше чем на 5 %
    SIDE_MAX_MS: 10000,         // сторона ≤ 10 с
    REQ_TARGET_MS: 1000,        // размер запроса — чтобы он шёл ≈ 1 с на своём потоке
    RATE_SPAN_MS: 1000,         // текущая скорость (график, оценка размера) — по последней секунде
    DOWN_STEPS: [256 * KIB, 1 * MIB, 8 * MIB, 32 * MIB, 128 * MIB, 256 * MIB],
    UP_STEPS: [256 * KIB, 1 * MIB, 4 * MIB, 16 * MIB, 64 * MIB],
    UP_STEP_MAX_PHONE: 16 * MIB,
    UP_BASE: 16 * MIB,          // базовый Blob отдачи
    UP_BASE_PHONE: 4 * MIB,
    UP_CHUNK: 64 * KIB,         // crypto.getRandomValues — не больше 65536 байт за раз
    SCALE_LOW_MS: 30,           // прибавка под нагрузкой: < 30 «почти не растёт»
    SCALE_HIGH_MS: 100,         // 30–100 «заметно», > 100 «сильно»
    FINISH_TIMEOUT_MS: 5000,
    HISTORY_MAX: 30,
  });

  const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

  // ── отклик ──
  function median(xs) {
    const a = (xs || []).map(num).filter((v) => v !== null).sort((x, y) => x - y);
    if (!a.length) return null;
    const m = a.length >> 1;
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  }

  // Разброс — среднее |Δ| соседних значений (в порядке замера).
  function spread(xs) {
    const a = (xs || []).map(num).filter((v) => v !== null);
    if (a.length < 2) return null;
    let s = 0;
    for (let i = 1; i < a.length; i++) s += Math.abs(a[i] - a[i - 1]);
    return s / (a.length - 1);
  }

  // Время сервера из заголовка Server-Timing: «app;dur=12.5» -> 12.5; нет — 0.
  function serverMs(header) {
    const m = /(?:^|,)\s*app\s*;(?:[^,]*?;)?\s*dur=([0-9]+(?:\.[0-9]+)?)/i.exec(String(header || ""));
    return m ? Number(m[1]) : 0;
  }

  // Один отклик. entry — запись Resource Timing (startTime, requestStart, responseStart) или null; wallMs — разница
  // часов вокруг запроса; srvMs — время сервера. -> {ms, good, rt}. Без записи (rt: false) — по часам вокруг запроса;
  // очередь тогда не видна: для покоя отсчёт годный (§6.2 п. 2 — часы вместо записи), а «под нагрузкой» его не берёт
  // (loaded: без записи очередь за потоками данных не отличить от роста отклика — REV-bc4 З-3).
  function sample(entry, wallMs, srvMs) {
    const srv = Math.max(0, num(srvMs) || 0);
    const e = entry || null;
    if (e && num(e.requestStart) > 0 && num(e.responseStart) >= e.requestStart && num(e.startTime) !== null) {
      return { ms: Math.max(0, e.responseStart - e.requestStart - srv), good: e.requestStart - e.startTime <= C.QUEUE_MS, rt: true };
    }
    return { ms: Math.max(0, (num(wallMs) || 0) - srv), good: true, rt: false };
  }

  // Покой: отклик — медиана годных, разброс — среднее |Δ| соседних годных; годных < 10 — не показываем.
  function idle(samples) {
    const all = samples || [];
    const good = all.filter((s) => s && s.good).map((s) => s.ms);
    const ok = good.length >= C.IDLE_MIN_GOOD;
    return { ping: ok ? median(good) : null, jitter: ok ? spread(good) : null, good: good.length, total: all.length, ok };
  }

  // ── скорость по ряду байт ──
  function bytesAt(series, t) {
    const s = series || [];
    if (!s.length) return 0;
    if (t <= s[0][0]) return s[0][1];
    for (let i = 1; i < s.length; i++) {
      if (t <= s[i][0]) {
        const a = s[i - 1], b = s[i];
        return b[0] === a[0] ? b[1] : a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]);
      }
    }
    return s[s.length - 1][1];
  }

  // Средняя скорость (бит/с) на отрезке [t0, t1] по ряду байт.
  function rate(series, t0, t1) {
    if (!(t1 > t0)) return null;
    return 8 * (bytesAt(series, t1) - bytesAt(series, t0)) * 1000 / (t1 - t0);
  }

  const lastT = (series) => (series && series.length ? series[series.length - 1][0] : 0);

  // Окно после разгона: от rampAt + rampMs до последнего отсчёта. -> {bps, ms, from, to}; окна нет — bps: null.
  function windowOf(series, rampAt, rampMs) {
    const to = lastT(series);
    if (num(rampAt) === null) return { bps: null, ms: 0, from: null, to };
    const from = rampAt + rampMs;
    const ms = Math.max(0, to - from);
    return { bps: ms > 0 ? rate(series, from, to) : null, ms, from, to };
  }

  // Ряд стороны для окна. Только при конце по остатку ("left"): (1) слив — с drainAt (мс, когда первый поток остался
  // без работы, пока другие ещё качали) потоков всё меньше, скорость падает не из-за канала — ряд режется на drainAt
  // (R-bc5, к REV-bc4 З-2); (2) хвост без новых байт — все запросы уже кончились. При конце по времени или по ровной
  // скорости ряд не трогается: остановка потока — честная часть пути (REV-bc4 П-2). -> новый ряд.
  function sideSeries(series, endedBy, drainAt) {
    let s = (series || []).slice();
    if (endedBy !== "left") return s;
    if (num(drainAt) !== null) {
      const cut = s.filter((p) => p[0] <= drainAt);
      if (cut.length >= 2) s = cut;
    }
    while (s.length > 2 && s[s.length - 1][1] === s[s.length - 2][1]) s.pop();
    return s;
  }

  // Ровно ли идёт: средняя последних 2 с против средней предыдущих 2 с — разница не больше 5 %.
  function stable(series, now) {
    const a = rate(series, now - C.STABLE_SPAN_MS, now);
    const b = rate(series, now - 2 * C.STABLE_SPAN_MS, now - C.STABLE_SPAN_MS);
    if (!(b > 0) || a === null) return false;
    return Math.abs(a - b) / b <= C.STABLE_DIFF + 1e-12;
  }

  // Конец стороны по времени: "time" — 10 с стороны; "stable" — окно ≥ 4 с и скорость ровная; иначе null. Конец по
  // остатку ("left") видит сам движок: размер следующего запроса — 0 (sizeFor) и открытых запросов нет.
  function stopReason(series, now, rampAt, rampMs) {
    if (now >= C.SIDE_MAX_MS) return "time";
    if (num(rampAt) === null) return null;
    const from = rampAt + rampMs;
    if (now - from >= C.WINDOW_MIN_MS && now - 2 * C.STABLE_SPAN_MS >= from && stable(series, now)) return "stable";
    return null;
  }

  // Подъём потоков (каждые 300 мс): "add" — +1 поток; "wait" — данных ещё нет; "done" — число потоков найдено.
  function rampStep(streams, prevBps, bps) {
    if (streams >= C.STREAMS_MAX) return "done";
    if (!(bps > 0)) return "wait";
    if (!(prevBps > 0)) return "add";
    return bps > prevBps * (1 + C.RAMP_GROWTH) ? "add" : "done";
  }

  // ── размеры запросов ──
  const upSteps = (phone) => (phone ? C.UP_STEPS.filter((s) => s <= C.UP_STEP_MAX_PHONE) : C.UP_STEPS.slice());
  const downSteps = () => C.DOWN_STEPS.slice();

  // Ступень, с которой запрос идёт ≈ 1 с на одном потоке: ближайшая к (скорость потока × 1 с) по отношению (лестница —
  // кратная, поэтому «ближе» — по логарифму: 6 МБ за секунду — 8 МиБ, а не 1 МиБ); скорость неизвестна — наименьшая.
  function pickStep(steps, streamBps) {
    const want = (num(streamBps) || 0) / 8 * C.REQ_TARGET_MS / 1000;
    if (!(want > 0)) return steps[0];
    let pick = steps[0], best = Infinity;
    for (const s of steps) {
      const d = Math.abs(Math.log(s / want));
      if (d < best) { best = d; pick = s; }
    }
    return pick;
  }

  // Правило размера (§6.2 п. 3, обе стороны): размер нового запроса = min(ступень, left), пока left ≥ наименьшей
  // ступени; иначе 0 — новых запросов нет, сторона кончается по остатку. left ведёт движок: вычитает размер каждого
  // начатого запроса и ничего не возвращает (оборванный запрос считается целиком).
  function sizeFor(left, steps, streamBps) {
    const l = Math.floor(num(left) || 0);
    if (l < steps[0]) return 0;
    return Math.min(pickStep(steps, streamBps), l);
  }

  // ── протокол и «под нагрузкой» ──
  // Главный признак — via из start: proxy — nginx с http2; direct и tunnel — сервер панели (HTTP/1.1); front и прочее —
  // по nextHopProtocol, пустой — h2 (лучше скрыть строку, чем показать ложную).
  function protoOf(via, nextHop) {
    if (via === "proxy") return "h2";
    if (via === "direct" || via === "tunnel") return "http/1.1";
    return /^http\/1(?:\.|$)/i.test(String(nextHop || "").trim()) ? "http/1.1" : "h2";
  }

  // Шкала прибавки отклика под нагрузкой: "barely" < 30 мс ≤ "noticeably" ≤ 100 мс < "a_lot".
  function loadScale(ms) {
    if (ms < C.SCALE_LOW_MS) return "barely";
    return ms <= C.SCALE_HIGH_MS ? "noticeably" : "a_lot";
  }

  // Строка «под нагрузкой». down/up — отклики сторон ({ms, good, rt}); cpu — {down, up} из finish.cpu_limited.
  // -> {show, deltaMs, scale, reason}; reason (почему строки нет): "one_conn" — h2, "cpu" — обе стороны упирались
  // в процессор, "queue" — годных < 8 или меньше двух третей, "idle" — нет отклика в покое. Годный под нагрузкой — только
  // с записью Resource Timing (rt: false — очередь не видна, REV-bc4 З-3); нет записей — "queue".
  function loaded(idlePing, down, up, cpu, proto) {
    const no = (reason) => ({ show: false, deltaMs: null, scale: null, reason });
    const c = cpu || {};
    if (proto === "h2") return no("one_conn");
    if (c.down === true && c.up === true) return no("cpu");
    const all = (c.down === true ? [] : down || []).concat(c.up === true ? [] : up || []);
    const good = all.filter((s) => s && s.good && s.rt !== false).map((s) => s.ms);
    if (good.length < C.LOADED_MIN_GOOD || good.length < all.length * C.LOADED_GOOD_SHARE) return no("queue");
    if (num(idlePing) === null) return no("idle");
    const delta = Math.max(0, Math.round(median(good) - idlePing));
    return { show: true, deltaMs: delta, scale: loadScale(delta), reason: null };
  }

  // Пометки «как измерено» (§4.7, §6.4) — коды по порядку показа. «Одно соединение» — только когда оно известно: вход
  // через nginx панели (via proxy) или браузер сам назвал h2/h3 (nextHop); догадка h2 у front с пустым nextHop лишь
  // прячет строку «под нагрузкой», пометкой не утверждается (REV-bc4 П-3).
  function flags(o) {
    const x = o || {};
    const cpu = x.cpu || {};
    const out = [];
    if (x.via === "tunnel") out.push("tunnel");
    if (x.vpn === "self") out.push("vpn");
    if (x.vpn === "cascade") out.push("cascade");
    if (x.proto === "h2" && (x.via === "proxy" || /^h[23]/i.test(String(x.nextHop || "").trim()))) out.push("one_conn");
    if (cpu.down === true || cpu.up === true) out.push("cpu");
    const short = (s) => s && s.endedBy === "left" && (s.windowMs || 0) < C.WINDOW_MIN_MS;
    if (short(x.down) || short(x.up)) out.push("fast");
    if (x.hidden) out.push("hidden");
    return out;
  }

  // ── ответы сервера ──
  const OURS_429 = ["hour", "day", "traffic", "budget", "streams"];

  // Отказ по коду и телу ответа ({ok: false, error, code, data}) и заголовку Retry-After.
  // -> {kind, status, text, retryAfter, n, limit, left, what}. kind: busy (409; what: "check" — идёт проверка сервера,
  // T-3310b М-5; null — другой замер), expired (410), lease (403 аренды),
  // forbidden, auth (401), too_big (413), hour|day|traffic|budget|streams (429 сервера панели), server_busy (429 без
  // нашего тела — nginx limit_conn), server (5xx), net (нет ответа), http (прочее).
  function refusal(status, body, retryHeader) {
    const b = body && typeof body === "object" ? body : {};
    const d = b.data && typeof b.data === "object" ? b.data : {};
    const code = typeof b.code === "string" ? b.code : typeof d.reason === "string" ? d.reason : null;
    let kind;
    if (!status) kind = "net";
    else if (status === 409) kind = "busy";
    else if (status === 410) kind = "expired";
    else if (status === 401) kind = "auth";
    else if (status === 403) kind = code === "lease" ? "lease" : "forbidden";
    else if (status === 413) kind = "too_big";
    else if (status === 429) kind = OURS_429.indexOf(code) >= 0 ? code : "server_busy";
    else if (status >= 500) kind = "server";
    else kind = "http";
    let retry = num(d.retry_after);
    if (retry === null && retryHeader !== undefined && retryHeader !== null && /^\s*\d+\s*$/.test(String(retryHeader))) {
      retry = Number(retryHeader);
    }
    return { kind, status: status || 0, text: typeof b.error === "string" ? b.error : null, retryAfter: retry,
             n: num(d.used), limit: num(d.limit), left: num(d.left), what: typeof d.what === "string" ? d.what : null };
  }

  // ── числа для текстов ──
  const WAIT = {
    ru: { s: "через {n} с", m: "через {n} мин", h: "через {h} ч", hm: "через {h} ч {m} мин" },
    en: { s: "in {n} s", m: "in {n} min", h: "in {h} h", hm: "in {h} h {m} min" },
  };
  const fill = (tpl, kv) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in kv ? String(kv[k]) : m));

  // «Следующая — {wait}» из retry_after (секунды), относительно: < 60 с — секунды; иначе минуты вверх; от 60 минут —
  // часы и минуты (минуты 0 не пишутся). dict — свой словарь {s, m, h, hm} (по умолчанию RU/EN выше).
  function fmtWait(s, lang, dict) {
    const w = dict || WAIT[lang === "ru" ? "ru" : "en"];
    const sec = Math.max(0, Math.ceil(num(Number(s)) || 0));
    if (sec < 60) return fill(w.s, { n: sec });
    const mins = Math.ceil(sec / 60);
    if (mins < 60) return fill(w.m, { n: mins });
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? fill(w.hm, { h, m }) : fill(w.h, { h });
  }

  // Байты -> ГБ (10⁹) без лишних нулей, до двух знаков; в RU — запятая.
  function fmtGb(bytes, lang) {
    const v = Math.round((num(bytes) || 0) / 1e7) / 100;
    const s = String(v);
    return lang === "ru" ? s.replace(".", ",") : s;
  }

  // ── история в браузере (§6.6) ──
  const HIST_NUMS = ["t", "ping", "jitter", "down", "up", "loaded"];

  // Запись истории из итога движка: {t, ping, jitter, down, up, loaded, via, proto, flags} — без адресов.
  function historyEntry(r) {
    const x = r || {};
    const ld = x.loaded && x.loaded.show ? x.loaded.deltaMs : null;
    return { t: num(x.t), ping: num(x.ping), jitter: num(x.jitter), down: num(x.down && x.down.bps),
             up: num(x.up && x.up.bps), loaded: num(ld), via: typeof x.via === "string" ? x.via : null,
             proto: typeof x.proto === "string" ? x.proto : null, flags: Array.isArray(x.flags) ? x.flags.slice() : [] };
  }

  // Прочитанный список (JSON из хранилища) — только годные записи, не больше 30, новые первыми как были.
  function historyClean(list) {
    if (!Array.isArray(list)) return [];
    const out = [];
    for (const e of list) {
      if (!e || typeof e !== "object" || num(e.t) === null) continue;
      const c = {};
      HIST_NUMS.forEach((k) => { c[k] = num(e[k]); });
      c.via = typeof e.via === "string" ? e.via : null;
      c.proto = typeof e.proto === "string" ? e.proto : null;
      c.flags = Array.isArray(e.flags) ? e.flags.filter((f) => typeof f === "string") : [];
      out.push(c);
      if (out.length >= C.HISTORY_MAX) break;
    }
    return out;
  }

  // Новая запись первой, всего не больше 30.
  const historyPush = (list, entry) => [entry].concat(historyClean(list)).slice(0, C.HISTORY_MAX);

  return Object.freeze({
    C, median, spread, serverMs, sample, idle, bytesAt, rate, windowOf, sideSeries, stable, stopReason, rampStep, upSteps,
    downSteps, pickStep, sizeFor, protoOf, loadScale, loaded, flags, refusal, fmtWait, fmtGb, historyEntry,
    historyClean, historyPush, WAIT,
  });
});
