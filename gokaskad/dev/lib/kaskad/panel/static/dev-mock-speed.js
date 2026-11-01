/* Kaskad 3 (BC5) — демо карточки «От вашего устройства до сервера» для ?mock=1 (поверх dev-mock.js): пределы
   (GET /api/host/speed/budget) и подменённый воркер замера (сеть настоящего воркера здесь некуда вести). Только для
   проверки вида без сервера (снимки, k3/stand/ui_audit_run.mjs). Числа и ход — как в макете «Browser Speed r1».
   Сцены — параметрами адреса:
     speed=run | run-up       замер идёт и замирает на скачивании (6,4 с) или на отдаче (14,6 с) — кадры «Идёт»
     speed=res[-вариант]      замер проходит сам (быстро) и показывает итог: res (обычный), res-slow, res-tunnel,
                              res-self, res-cascade, res-one, res-cpu, res-fast, res-queue, res-noping, res-noup
     speed=lim-hour | lim-day | lim-traffic        отказ 429 предела
     speed=busy-tab | busy-chk | busy-sched        409: другая вкладка · проверка сервера по кнопке · по расписанию
     speed=err-net | err-srv | err-time | err-tab | err-busy   ошибка или прервано
     speed=ask                мобильный трафик: вопрос после «Проверить»
     speedfront=1             вход через порт VPN (via: front) — вторая строка про VPN
     speedgb=1                пределы сервера 2 × 500 МБ — «до 1 ГБ»
     speedhist=N              история на этом устройстве: N записей макета (без параметра — пусто); speedopen=1 — раскрыта
   Без speed= — «до первого замера»; «Проверить» запускает обычный замер (≈ 19 с). */
(function (g) {
  'use strict';
  var M = g.KASKAD_MOCK;
  if (!M || !M.fetch) { return; }
  var Q = new URLSearchParams(location.search);
  var scene = Q.get('speed') || '';
  var P = [0, 2200, 10600, 19000];                 // границы фаз хода макета, мс: покой, скачивание, отдача, конец
  var R = {
    base: { ping: 24, jit: 3, down: 87, up: 41, load: 35 },
    tunnel: { ping: 31, jit: 4, down: 38, up: 22, load: 48, flags: ['tunnel'], via: 'tunnel' },
    self: { ping: 29, jit: 5, down: 61, up: 33, load: 62, flags: ['vpn'] },
    cascade: { ping: 57, jit: 6, down: 74, up: 36, load: 41, flags: ['cascade'] },
    one: { ping: 24, jit: 3, down: 79, up: 37, why: 'one_conn', flags: ['one_conn'], via: 'proxy' },
    cpu: { ping: 25, jit: 3, down: 212, up: 168, why: 'cpu', flags: ['cpu'] },
    fast: { ping: 6, jit: 1, down: 940, up: 870, load: 12, flags: ['fast'] },
    queue: { ping: 24, jit: 3, down: 86, up: 40, why: 'queue' },
    noping: { ping: null, jit: null, down: 88, up: 42, why: 'idle' },
    noup: { ping: 24, jit: 3, down: 87, up: null, load: 35 },
    slow: { ping: 112, jit: 18, down: 12, up: 4, load: 240 } };
  var HIST = [['24.10', '14:32', 24, 3, 87, 41], ['24.10', '09:10', 31, 5, 64, 38], ['23.10', '22:47', 58, 14, 22, null], ['23.10', '19:05', 27, 4, 81, 40], ['23.10', '08:55', 23, 3, 90, 42],
    ['22.10', '21:30', 44, 9, 35, 18], ['22.10', '13:12', 25, 3, 86, 41], ['21.10', '20:18', 26, 4, 84, 39], ['20.10', '23:40', 61, 17, 19, 8], ['20.10', '10:02', 24, 3, 88, 42],
    ['19.10', '18:44', 29, 5, 72, 36], ['18.10', '12:20', 24, 2, 89, 43], ['17.10', '22:05', 52, 12, 28, 11], ['16.10', '09:31', 25, 3, 85, 40]];

  // история: своя на каждую загрузку (снимки не копят записи друг друга)
  try {
    var n = Math.max(0, Math.min(30, Number(Q.get('speedhist')) || 0));
    if (n) {
      var list = HIST.slice(0, n).map(function (h) {
        var dm = h[0].split('.').map(Number), hm = h[1].split(':').map(Number);
        return { t: new Date(2026, dm[1] - 1, dm[0], hm[0], hm[1]).getTime(), ping: h[2], jitter: h[3], down: h[4] * 1e6, up: h[5] === null ? null : h[5] * 1e6, loaded: 35, via: 'direct', proto: 'http/1.1', flags: [] };
      });
      localStorage.setItem('kaskad.speed.v1', JSON.stringify(list));
    } else { localStorage.removeItem('kaskad.speed.v1'); }
  } catch (e) { /* без хранилища */ }

  var base = M.fetch;
  M.fetch = function (url, init) {
    var u = new URL(String(url), location.href);
    if (u.pathname.replace(/^.*?\/api\//, '/api/') === '/api/host/speed/budget') {
      var side = Q.get('speedgb') === '1' ? 500e6 : 800e6;
      var doc = { ok: true, today_bytes: 0, day_cap_bytes: 10e9, tests_hour: 0, tests_day: 0,
        caps: { tests_hour: 6, tests_day: 30, side_bytes: side, day_bytes: 10e9, req_down_max: 268435456, req_up_max: 67108864 },
        via: Q.get('speedfront') === '1' ? 'front' : 'direct' };
      return Promise.resolve(new Response(JSON.stringify(doc), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return base(url, init);
  };

  // ── подменённый воркер: те же сообщения, что speed-worker.js (phase, sample, partial, progress, done) ──
  function rng(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  // ход стороны: разгон к S Мбит/с с постоянной tau, шум ±8 %, отсчёт раз в 100 мс -> [[мс от начала стороны, бит/с]]
  function side(S, ms, tau, r) {
    var a = [], e = 0;
    for (var t = 100; t <= ms + 1e-9; t += 100) { var b = S * (1 - Math.exp(-t / tau)); e = e * 0.55 + b * (1 + 0.16 * (r() - 0.5)) * 0.45; a.push([t, Math.max(0, e) * 1e6]); }
    return a;
  }
  function result(key) {
    var x = R[key] || R.base;
    var loaded = x.why ? { show: false, deltaMs: null, scale: null, reason: x.why }
      : { show: true, deltaMs: x.load, scale: x.load < 30 ? 'barely' : x.load <= 100 ? 'noticeably' : 'a_lot', reason: null };
    return { ok: true, t: Date.now(), via: x.via || 'direct', vpn: x.flags && x.flags[0] === 'vpn' ? 'self' : x.flags && x.flags[0] === 'cascade' ? 'cascade' : null,
      proto: x.why === 'one_conn' ? 'h2' : 'http/1.1', ping: x.ping, jitter: x.jit, idle: { good: x.ping === null ? 4 : 20, total: 20, rt: 20 }, rt: { load: 60, loadTotal: 60 },
      down: { bps: x.down * 1e6, windowMs: 4000, windowEnd: 8400, streams: 3, endedBy: 'stable' },
      up: { bps: x.up === null ? null : x.up * 1e6, windowMs: x.up === null ? 0 : 4000, streams: 3, endedBy: 'stable', clientBps: (x.up || 18) * 1e6, clientWindowMs: 4000 },
      loaded: loaded, cpuLimited: { down: x.why === 'cpu', up: x.why === 'cpu' }, flags: (x.flags || []).slice(), bytes: { down: 1, up: 1 }, stopped: null, error: null, finished: true };
  }
  var FAIL = {
    'lim-hour': { kind: 'hour', status: 429, n: 6, limit: 6, retryAfter: 1100 },
    'lim-day': { kind: 'day', status: 429, n: 30, limit: 30, retryAfter: 20400 },
    'lim-traffic': { kind: 'traffic', status: 429, n: null, limit: 10e9, retryAfter: 33100 },
    'busy-tab': { kind: 'busy', status: 409, retryAfter: 15 }, 'busy-chk': { kind: 'busy', status: 409, retryAfter: 65 }, 'busy-sched': { kind: 'busy', status: 409, retryAfter: 125 },
    'err-net': { kind: 'net', status: 0 }, 'err-srv': { kind: 'server', status: 500 }, 'err-time': { kind: 'expired', status: 410 }, 'err-busy': { kind: 'server_busy', status: 429 } };

  function FakeWorker() { this.onmessage = null; this.onerror = null; this.timers = []; this.dead = false; }
  FakeWorker.prototype.post = function (m) { var self = this; if (!self.dead && self.onmessage) { self.onmessage({ data: m }); } };
  FakeWorker.prototype.at = function (ms, fn) { var self = this; self.timers.push(setTimeout(function () { if (!self.dead) { fn(); } }, ms)); };
  FakeWorker.prototype.terminate = function () { this.dead = true; this.timers.forEach(clearTimeout); };
  FakeWorker.prototype.postMessage = function (m) {
    var self = this;
    if (m && m.type === 'stop') {
      self.timers.forEach(clearTimeout); self.timers = [];
      self.at(150, function () { self.post({ type: 'phase', phase: 'finish' }); self.post({ type: 'done', result: { ok: false, error: null, stopped: m.reason === 'hidden' ? 'hidden' : 'user', flags: [] } }); });
      return;
    }
    if (!m || m.type !== 'start') { return; }
    var sc = scene;
    self.post({ type: 'phase', phase: 'start' });
    if (FAIL[sc] && (sc.indexOf('lim') === 0 || sc.indexOf('busy') === 0)) {      // отказ start — аренды нет
      self.at(250, function () { self.post({ type: 'done', result: { ok: false, error: FAIL[sc], stopped: null, flags: [] } }); });
      return;
    }
    var fast = sc.indexOf('res') === 0 || sc.indexOf('err') === 0;               // итог и ошибки — в 10 раз быстрее
    var k = fast ? 0.1 : 1;
    var key = sc.indexOf('res-') === 0 ? sc.slice(4) : 'base';
    var x = R[key] || R.base, r = rng(key.length * 7919 + 13);
    var freeze = sc === 'run' ? 6400 : sc === 'run-up' ? 14600 : Infinity;
    var dn = side(x.down, P[2] - P[1], 800, r), up = side(x.up === null ? 18 : x.up, x.up === null ? 1600 : P[3] - P[2], x.up === null ? 500 : 1000, r);
    var step = function (ms, fn) { if (ms <= freeze) { self.at(ms * k, fn); } };
    step(80, function () { self.post({ type: 'phase', phase: 'idle' }); });
    for (var i = 0; i < 20; i++) { (function (i) { step(80 + i * 106, function () { self.post({ type: 'sample', ms: 22 + Math.round(4 * Math.abs(Math.sin(i * 0.7))), good: true }); }); })(i); }
    step(P[1] - 20, function () { self.post({ type: 'partial', ping: x.ping, jitter: x.jit }); });
    step(P[1], function () { self.post({ type: 'phase', phase: 'down' }); });
    dn.forEach(function (p) { step(P[1] + p[0], function () { self.post({ type: 'progress', side: 'down', t: p[0] * k, bps: p[1] }); }); });
    step(P[2] - 20, function () { self.post({ type: 'partial', down: x.down * 1e6 }); });
    step(P[2], function () { self.post({ type: 'phase', phase: 'up' }); });
    up.forEach(function (p) { step(P[2] + p[0], function () { self.post({ type: 'progress', side: 'up', t: p[0] * k, bps: p[1] }); }); });
    var endMs = x.up === null ? P[2] + 1600 : P[3];
    if (sc === 'err-tab') { step(P[1] + 3000, function () { self.post({ type: 'phase', phase: 'finish' }); self.post({ type: 'done', result: { ok: false, error: null, stopped: 'hidden', flags: ['hidden'] } }); }); return; }
    if (FAIL[sc]) { step(P[1] + 3000, function () { self.post({ type: 'phase', phase: 'finish' }); self.post({ type: 'done', result: { ok: false, error: FAIL[sc], stopped: null, flags: [] } }); }); return; }
    step(endMs, function () { self.post({ type: 'phase', phase: 'finish' }); });
    step(endMs + 100, function () { self.post({ type: 'done', result: result(key) }); });
  };

  g.KASKAD_SPEED_MOCK = { Worker: FakeWorker, cellular: scene === 'ask', auto: !!scene, histOpen: Q.get('speedopen') === '1' };
})(window);
