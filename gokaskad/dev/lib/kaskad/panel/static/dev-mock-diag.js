/* Kaskad 3 (B15) — демо-данные раздела «Диагностика» для ?mock=1 (поверх dev-mock.js): расписание проверок и история.
   Только для проверки вида без сервера (снимки, tools/k3_layout_check.mjs). Ответы — по контракту
   SPEC-node-score-schedule-share §2.6, §3.4 (panel/host.py); правила расписания здесь — упрощённые, только чтобы
   показать ошибки сервера на экране (панель своих правил не имеет).
   Сцены — параметрами адреса:
     sched=empty            пустое расписание: предложение «быстрая + полная» (propose)
     armed=no_manual|no_pkgs|other_machine   расписание не взведено — плашка над списком
     hist=none              истории нет (пустое состояние)
     tzerr=1                PUT отвечает 409 tz_changed (проверка текста и «Обновить»)
     score=dip|conf|two|few|stale|links|none   «Сейчас» (UI-C, сцены макета «Проверка сервера r1»; пусто — ровный):
                            разовый провал сегодня · падение подтвердилось · подтверждённая ▼ · история 2 из 3 дней ·
                            давно не проверялся · «от обычного» только у двух звеньев · данных нет
     pkgs=1                 запуск ставит sysbench и fio: без ответа install — 409 need_install
     bench=running|limit|limitfull   запуск: уже идёт · предел дня · предел полных
     now=long               «Сейчас»: длинные строки правой колонки балла — две «Новая база с …» и «Расписание выключено —
                            Настройки › Проверка сервера» (все записи расписания выключены; стенд T-it1b Н-5) */
(function (g) {
  'use strict';
  var M = g.KASKAD_MOCK;
  if (!M || !M.fetch) { return; }
  var Q = new URLSearchParams(location.search);
  var en = (typeof document !== 'undefined' && (document.documentElement.lang || '').indexOf('en') === 0) || /lang=en/.test(location.search);
  var L = function (ru, enText) { return en ? enText : ru; };
  var now = function () { return Math.floor(Date.now() / 1000); };
  var TZ = 'Europe/Moscow', OFF = 3 * 3600;
  function resp(status, doc, headers) { return Promise.resolve(new Response(JSON.stringify(doc), { status: status, headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}) })); }
  function later(ms, fn) { return new Promise(function (ok) { setTimeout(function () { ok(fn()); }, ms); }); }

  // ── расписание ──
  var list = Q.get('sched') === 'empty' ? [] : [
    { id: 'a1c201', time: '02:00', mode: 'full', days: [1, 4], on: true, quiet: true, monthly: false },
    { id: 'a1c202', time: '04:30', mode: 'quick', days: [1, 2, 3, 4, 5, 6, 7], on: true, quiet: true, monthly: false },
    { id: 'a1c203', time: '09:00', mode: 'quick', days: [6, 7], on: false, quiet: false, monthly: false },
    { id: 'a1c204', time: '13:00', mode: 'quick', days: [1, 2, 3, 4, 5, 6, 7], on: true, quiet: true, monthly: false },
    { id: 'a1c205', time: '16:00', mode: 'full', days: [7], on: true, quiet: true, monthly: true },
    { id: 'a1c206', time: '21:00', mode: 'quick', days: [1, 2, 3, 4, 5, 6, 7], on: true, quiet: true, monthly: false }];
  var LONG = Q.get('now') === 'long';
  if (LONG) list.forEach(function (e) { e.on = false; });
  var retry = true, ver = 1, seq = 0;
  var ARMED = Q.get('armed') || '';
  var RUN = { quick: { rx_mb: 250.03, tx_mb: 250, cpu_min: 0.9 }, full: { rx_mb: 2000.03, tx_mb: 2000, cpu_min: 1.0 } };
  var mins = function (t) { return Number(t.slice(0, 2)) * 60 + Number(t.slice(3)); };
  function cost(entries, rt) {
    var week = { quick: 0, full: 0 };
    entries.forEach(function (e) { if (e.on) { week[e.mode] += e.monthly ? 7 / 30.4 : (e.days || []).length; } });
    var month = { rx_mb: 0, tx_mb: 0, cpu_min: 0 }, day = {};
    ['quick', 'full'].forEach(function (m) { Object.keys(month).forEach(function (k) { month[k] += week[m] / 7 * 30.4 * RUN[m][k]; }); });
    Object.keys(month).forEach(function (k) { day[k] = Math.round(month[k] / 30.4 * 100) / 100; month[k] = Math.round(month[k] * 100) / 100; });
    var notes = [];
    if ((month.rx_mb + month.tx_mb) >= 100000) { notes.push({ code: 'heavy', args: { gb: Math.round((month.rx_mb + month.tx_mb) / 1000) }, text: L('Много трафика: ≈ ', 'A lot of traffic: ≈ ') + Math.round((month.rx_mb + month.tx_mb) / 1000) + L(' ГБ в месяц.', ' GB a month.') }); }
    var used = week.quick + week.full > 0;
    if (rt !== false && used) { notes.push({ code: 'retry', args: { n: 2, mb: 8000 }, text: L('При резком падении — до 2 повторов в сутки, до 8 000 МБ.', 'After a sharp drop — up to 2 repeats a day, up to 8,000 MB.') }); }
    var perDay = [1, 2, 3, 4, 5, 6, 7].map(function (d) { return entries.filter(function (e) { return e.on && (e.days || []).indexOf(d) >= 0; }).length; });
    var maxDay = Math.max.apply(null, perDay);
    return { max_per_day: maxDay, per_day_even: perDay.every(function (x) { return x === maxDay; }),
      week: { quick: Math.round(week.quick * 100) / 100, full: Math.round(week.full * 100) / 100 }, day: day, month: month,
      per_run: RUN, basis: 'caps', basis_mode: { quick: 'caps', full: 'caps' }, retry: rt !== false && used ? { per_day: 2, max_mb: 8000 } : null, notes: notes };
  }
  // упрощённые пределы (сервер — bench/schedule.py): дни, «раз в месяц» — один день, 6 в день, 2 полных, 2 ч между любыми
  var DAYW = { 1: L('пн', 'Mon'), 2: L('вт', 'Tue'), 3: L('ср', 'Wed'), 4: L('чт', 'Thu'), 5: L('пт', 'Fri'), 6: L('сб', 'Sat'), 7: L('вс', 'Sun') };
  function check(entries) {
    var errs = [];
    var on = [];
    entries.forEach(function (e, i) {
      var n = i + 1;
      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(e.time || '')) { errs.push({ code: 'time', id: e.id || null, args: { n: n }, text: L('Время — ЧЧ:ММ.', 'Time is HH:MM.') }); return; }
      if (!e.days || !e.days.length) { errs.push({ code: 'days', id: e.id || null, args: { n: n }, text: L('Выберите хотя бы один день.', 'Pick at least one day.') }); return; }
      if (e.monthly && e.days.length !== 1) { errs.push({ code: 'monthly_days', id: e.id || null, args: { n: n }, text: L('Для записи «раз в месяц» выберите один день недели.', 'For a monthly entry, pick one day of the week.') }); return; }
      if (!e.on) { return; }
      e.days.forEach(function (d) {
        var at = (d - 1) * 1440 + mins(e.time);
        on.forEach(function (o) {
          var diff = Math.abs(o.at - at), gap = Math.min(diff, 7 * 1440 - diff);
          if (gap < 120 && !errs.some(function (x) { return x.code === 'gap' && x.args.n === n && x.args.ia === o.id; })) {
            errs.push({ code: 'gap', id: e.id || null, args: { a: o.time, b: e.time, min: gap, ia: o.id, ib: e.id || null, n: n },
              text: L('Проверки в ' + o.time + ' и ' + e.time + ' — через ' + gap + ' мин, нужно не меньше 2 ч.', 'Checks at ' + o.time + ' and ' + e.time + ' are ' + gap + ' min apart, at least 2 h needed.') });
          }
        });
      });
      e.days.forEach(function (d) { on.push({ at: (d - 1) * 1440 + mins(e.time), d: d, time: e.time, mode: e.mode, id: e.id || null }); });
    });
    for (var d = 1; d <= 7; d += 1) {
      var day = on.filter(function (o) { return o.d === d; });
      if (day.length > 6) { errs.push({ code: 'per_day', id: null, args: { day: d, n: day.length, max: 6 }, text: L('Не больше 6 проверок в день — ', 'No more than 6 checks a day — ') + DAYW[d] + ': ' + day.length + '.' }); }
      if (day.filter(function (o) { return o.mode === 'full'; }).length > 2) { errs.push({ code: 'full_per_day', id: null, args: { day: d, n: 3, max: 2 }, text: L('Полных проверок не больше 2 в день — ', 'No more than 2 full checks a day — ') + DAYW[d] + '.' }); }
    }
    return errs;
  }
  function nextRuns(entries) {
    var out = [], t0 = now();
    for (var k = 0; k < 8 * 24 * 60 && out.length < 5; k += 1) {
      var ts = Math.floor(t0 / 60) * 60 + 60 * (k + 1);
      var loc = new Date((ts + OFF) * 1000), hm = ('0' + loc.getUTCHours()).slice(-2) + ':' + ('0' + loc.getUTCMinutes()).slice(-2);
      var wd = loc.getUTCDay() || 7;
      entries.forEach(function (e) { if (e.on && e.time === hm && e.days.indexOf(wd) >= 0 && (!e.monthly || loc.getUTCDate() <= 7)) { out.push({ key: hm, id: e.id, mode: e.mode, quiet: e.quiet, ts: ts }); } });
    }
    return out.slice(0, 5);
  }
  function etag() { return '"' + ('00000000000000000000' + ver.toString(16)).slice(-20) + '"'; }
  function doc() {
    var armed = !ARMED;
    var d = { ok: true, schedule: list.map(function (e) { return Object.assign({ since: now() - 86400, off_why: null }, e); }), retry: retry, armed: armed, armed_why: armed ? null : ARMED,
      pkgs: ARMED === 'no_pkgs' ? 'no' : 'yes', tz: TZ, tz_warn: null, next: nextRuns(list), cost: cost(list, retry),
      limits: { per_day: 6, full_per_day: 2, gap_min: 120, total: 12, jitter_min: 15 }, timer: { active: armed && list.length > 0, applied: true, why: null }, etag: etag() };
    if (!list.length) {
      var q = { time: '04:30', mode: 'quick', days: [3], on: true, quiet: true, monthly: false };
      var f = { time: '04:30', mode: 'full', days: [1], on: true, quiet: true, monthly: true };
      d.propose = { entries: [q, f], basis: 'default', cost: cost([q, f], retry), cost_quick: cost([q], retry) };
    }
    return d;
  }
  function clean(e) { return { id: e.id || null, time: e.time, mode: e.mode, days: (e.days || []).slice(), on: e.on !== false, quiet: !!e.quiet, monthly: !!e.monthly }; }

  // ── история: 30 дней, день — быстрые 04:30 / 13:00 / 21:00, полные пн и чт 02:00; ручные, пропуск, нагрузка, падение ──
  var LINKS = ['net_bw', 'net_q', 'cpu', 'stab', 'down', 'up', 'loss', 'crypto'];
  function hid(n) { return ('0000000000' + (0x5a000000 + n).toString(16)).slice(-10); }
  function at(daysAgo, h, m) {
    var t = now() + OFF, day0 = Math.floor(t / 86400) * 86400 - OFF;
    return day0 - daysAgo * 86400 + h * 3600 + m * 60;
  }
  var RUNS = [];
  (function build() {
    if (Q.get('hist') === 'none') { return; }
    var n = 0;
    for (var dd = 0; dd < 30; dd += 1) {
      [[4, 30, 'quick', 'schedule'], [13, 0, 'quick', 'schedule'], [21, 0, 'quick', 'schedule']].forEach(function (s) {
        var ts = at(dd, s[0], s[1]);
        if (ts > now()) { return; }
        n += 1;
        RUNS.push({ id: hid(n), ts: ts, end_ts: ts + 70, mode: s[2], by: s[3], sched_id: 'a1c202', retry_of: null, status: 'ok', why: null, why_text: null,
          rx: 230e6 + (n % 7) * 9e6, tx: 210e6, dur_s: 68 + (n % 9), cpu_s: 41, under_load: false, drops: [] });
      });
      var wd = new Date((at(dd, 12, 0) + OFF) * 1000).getUTCDay() || 7;
      if (wd === 1 || wd === 4) {
        var ts2 = at(dd, 2, 0);
        if (ts2 < now()) { n += 1; RUNS.push({ id: hid(n), ts: ts2, end_ts: ts2 + 122, mode: 'full', by: 'schedule', sched_id: 'a1c201', retry_of: null, status: 'ok', why: null, why_text: null, rx: 1.2e9, tx: 1.0e9, dur_s: 122, cpu_s: 46, under_load: false, drops: [] }); }
      }
    }
    // сегодня 04:10 — полная из бота; вчера 15:20 — из панели; 2 дня назад — падение 04:30 и автоповтор 06:10;
    // 3 дня назад 21:00 — под нагрузкой; 4 дня назад 13:00 — пропуск; 5 дней назад — проверка не удалась
    function add(o) { n += 1; RUNS.push(Object.assign({ id: hid(n), end_ts: o.ts + 120, sched_id: null, retry_of: null, why: null, why_text: null, rx: 1.1e9, tx: 1.0e9, dur_s: 122, cpu_s: 46, under_load: false, drops: [], status: 'ok' }, o)); return RUNS[RUNS.length - 1]; }
    if (at(0, 4, 10) < now()) { add({ ts: at(0, 4, 10), mode: 'full', by: 'bot' }); }
    add({ ts: at(1, 15, 20), mode: 'full', by: 'panel' });
    var drop = RUNS.find(function (r) { return r.ts === at(2, 4, 30); });
    if (drop) { drop.drops = [{ link: 'up', rel: 10, test: 'net_up', value: 30, usual: 300, unit: 'Мбит/с' }]; }
    add({ ts: at(2, 6, 10), mode: 'quick', by: 'auto', retry_of: drop ? drop.id : null, rx: 260e6, tx: 240e6, dur_s: 58 });
    var load = RUNS.find(function (r) { return r.ts === at(3, 21, 0); });
    if (load) { load.under_load = true; }
    var skip = RUNS.find(function (r) { return r.ts === at(4, 13, 0); });
    if (skip) { Object.assign(skip, { status: 'skipped', why: 'busy', why_text: L('Пропущена: сервер был занят (чужой трафик 4,2 Мбит/с при пороге 2 Мбит/с)', 'Skipped: the server was busy (other traffic 4.2 Mbit/s, limit 2 Mbit/s)'), rx: 0, tx: 0, dur_s: 3600 }); }
    var fail = RUNS.find(function (r) { return r.ts === at(5, 13, 0); });
    if (fail) { Object.assign(fail, { status: 'failed', why: 'error', why_text: L('Проверка прервалась ошибкой', 'The check stopped on an error'), dur_s: 12, rx: 1e6, tx: 0 }); }
    RUNS.sort(function (a, b) { return b.ts - a.ts; });
  })();
  var TESTS_Q = [['net_down', 675, 'Mbit/s'], ['net_up', 503, 'Mbit/s'], ['loss', 0.06, '%'], ['rtt', 38, 'ms']];
  var TESTS_F = TESTS_Q.concat([['cpu_1t', 1180, 'ev/s'], ['cpu_mt', 2310, 'ev/s'], ['chacha_1t', 1318, 'MB/s'], ['aes_1t', 2900, 'MB/s'], ['mem_bw', 9100, 'MiB/s'], ['disk_rr', 41000, 'IOPS']]);
  function relRun(r) { return r.status === 'ok' || r.status === 'partial' ? (r.drops.length ? 72 : 96 + parseInt(r.id, 16) % 9) : null; }
  function detail(r) {
    var tests = r.status === 'ok' ? (r.mode === 'full' ? TESTS_F : TESTS_Q).map(function (x) {
      var v = x[0] === 'net_up' && r.drops.length ? 30 : x[0] === 'net_down' ? x[1] + (parseInt(r.id, 16) % 9) * 7 : x[1];
      return { test: x[0], ts: r.ts + 10, value: v, unit: x[2], mode: r.mode, ok: 1, why: null, under_load: r.under_load ? 1 : 0, value_corr: null, people: null, ca: null, dns_src: null };
    }) : [];
    if (r.mode === 'full' && r.status === 'ok') { tests.push({ test: 'disk_fsync', ts: r.ts + 90, value: null, unit: 'ms', mode: 'full', ok: 0, why: 'no_fio', under_load: 0, value_corr: null, people: null, ca: null, dns_src: null }); }
    // rel (UI-C): «от обычного» проверки и её тестов — к «обычно» на её время
    var per = {};
    tests.forEach(function (x) { var tv = TV[x.test]; if (tv && typeof x.value === 'number') { per[x.test] = { usual: tv[0], usual_n: 12, rel: relOf(x.test, x.value, tv[0]) }; } });
    var rt = relRun(r);
    return Object.assign({ ok: true, tests: tests, cost_real: { rx: r.rx, tx: r.tx, cpu_s: r.cpu_s, dur_s: r.dur_s, estimate: null }, rel: rt === null ? null : { rel_total: rt, tests: per } }, r);
  }
  // ── «Сейчас» (GET /api/host/score; NS-D30–NS-D35, UI-C): сцены макета «Проверка сервера r1» — score= пусто (ровный),
  // dip (разовый провал сегодня: отдача упала, повтор ждёт; потери ждут подтверждения), conf (падение подтвердилось),
  // two (подтверждённая ▼ отдачи), few (история 2 из 3 дней — числа нет), stale (давно не проверялся), links (rel
  // только у двух звеньев), none (данных нет). Тест: [обычно, единица ядра, больше — лучше, разброс]
  var TV = { net_down: [760, 'Мбит/с', 1, 0.05], net_up: [495, 'Мбит/с', 1, 0.06], rtt: [8, 'мс', 0, 0.1], loss: [0.05, '%', 0, 0.3],
    jitter: [1.1, 'мс', 0, 0.18], dns: [15, 'мс', 0, 0.1], cpu_1t: [1200, 'событий/с', 1, 0.03], cpu_mt: [1540, 'событий/с', 1, 0.03],
    chacha_1t: [1620, 'МБ/с', 1, 0.03], aes_1t: [2950, 'МБ/с', 1, 0.03], steal_p95: [1.1, '%', 0, 0.2], cpu_cv: [3.4, '%', 0, 0.1],
    cpu_sustain: [0.98, '×', 1, 0.01], cpu_canary_cv: [2, '%', 0, 0.1] };
  var FL = { rtt: 1, loss: 0.1, jitter: 0.5, dns: 1, steal_p95: 1, cpu_cv: 1, cpu_canary_cv: 1 };
  var LK = [['net_bw', ['net_down', 'net_up'], 'tests', 1], ['net_q', ['rtt', 'loss', 'jitter', 'dns'], 'probes', 1],
    ['cpu', ['cpu_1t', 'cpu_mt', 'chacha_1t', 'aes_1t'], 'tests', 1], ['stab', ['steal_p95', 'cpu_cv', 'cpu_sustain', 'cpu_canary_cv'], 'probes', 1],
    ['down', ['net_down'], 'tests', 0], ['up', ['net_up'], 'tests', 0], ['loss', ['loss'], 'probes', 0], ['crypto', ['chacha_1t'], 'tests', 0]];
  var PROBE_T = ['rtt', 'loss', 'jitter', 'dns', 'steal_p95', 'cpu_cv', 'cpu_sustain', 'cpu_canary_cv'];
  var FULL_T = ['cpu_1t', 'cpu_mt', 'chacha_1t', 'aes_1t'];
  var LOWER_T = { rtt: 1, loss: 1, jitter: 1, dns: 1, steal_p95: 1, cpu_cv: 1, cpu_canary_cv: 1, net_cv: 1, disk_fsync: 1 };
  var RULE = { pct: 10, use_spread: true };
  function median(a) {
    var x = a.slice().sort(function (p, q) { return p - q; }), n = x.length;
    return n ? (n % 2 ? x[(n - 1) / 2] : Math.round((x[n / 2 - 1] + x[n / 2]) / 2)) : null;
  }
  function relOf(t, f, u) {
    if (f === null || u === null) { return null; }
    if (TV[t][2]) { return u > 0 ? Math.max(0, Math.min(300, Math.round(100 * f / u))) : null; }
    var fl = FL[t] || 0;
    return Math.max(0, Math.min(300, Math.round(100 * Math.max(u, fl) / Math.max(f, fl) || 0)));
  }
  function r3(v) { return Math.round(v * 1000) / 1000; }
  var noise = function (k) { var x = Math.sin(k * 12.9898) * 43758.5453; return (x - Math.floor(x)) * 2 - 1; };
  function scoreDoc() {
    var SQ = Q.get('score') || '';
    if (SQ === 'none') { return { ok: true, charts: false }; }
    var stale = SQ === 'stale', few = SQ === 'few', dip = SQ === 'dip', conf = SQ === 'conf', two = SQ === 'two', fewLinks = SQ === 'links';
    // score=young — первые часы нового узла (Т-3312 «Мелочь 2»): пробы набрали 5 ч из 12, у отклика, дрожания и потерь
    // значения ещё нет — как сервер (hostscore.why_missing, provisional): «ещё измеряется: 5 ч из 12»
    var young = SQ === 'young' ? { why: 'net_young', have_h: 5, need_h: 12 } : null;
    var youngWhy = { fresh_raw: null, rel: null, why: 'young', why_text: L('Ещё измеряется: фоновые пробы набрали 5 ч из 12 — итог пока без этого звена',
      'Still being measured: the background probes have 5 h of 12 — the total leaves this link out for now') };
    var days = [], series = {};
    Object.keys(TV).forEach(function (t) { series[t] = []; });
    for (var i = 29; i >= 0; i -= 1) {
      var ts0 = now() - i * 86400, day = dayOf(ts0);
      var grey = few ? i > 1 : (i === 27 || i === 28) && !stale;
      var col = grey ? 'grey' : i === 0 && dip ? 'red' : i === 0 && conf ? 'yellow' : i === 17 || i === 21 ? 'yellow' : 'green';
      var test = !grey && !stale;
      var full = test && i % 7 === 0;
      var rs = col === 'yellow' ? (i === 0 ? L('Отдача резко упала: 6 Мбит/с, обычно 495', 'Upload dropped sharply: 6 Mbit/s, usually 495') : L('Отклик рос до 24 мс — 30 мин, с 19:40', 'Latency rose to 24 ms — 30 min, from 19:40'))
        : col === 'red' ? L('Потери 3,1% с 14:10 до 14:40', 'Loss 3.1% from 14:10 to 14:40') : null;
      var k = 30 - i;
      var m = grey ? { rtt: null, rtt_p90: null, loss: null, loss_peak: null, jit: null, off: null }
        : { rtt: r3(8 + noise(k) * 0.6), rtt_p90: r3(14 + noise(k + 3)), loss: col === 'red' ? 0.41 : r3(0.05 + noise(k + 5) * 0.01), loss_peak: col === 'red' ? 3.1 : 0.2,
          jit: r3(1.1 + noise(k + 7) * 0.15), off: 0 };
      var checks = [];
      if (test) {
        var dn = Math.round(760 * (1 + noise(k + 11) * 0.04)), upv = i === 0 && (dip || conf) ? 5 : Math.round(495 * (1 + noise(k + 13) * 0.04));
        checks.push({ hhmm: '04:10', mode: full ? 'full' : 'quick', by: 'schedule', status: 'ok', down: dn, up: upv });
        if (i === 0 && conf) { checks.push({ hhmm: '16:14', mode: 'quick', by: 'auto', status: 'ok', down: 735, up: 6 }); }
        if (i === 0 && dip) { checks.push({ hhmm: '14:52', mode: 'quick', by: 'panel', status: 'ok', down: 735, up: 5 }); }
        var t0 = Math.min(ts0 - ((ts0 + OFF) % 86400) + 4 * 3600 + 600, now() - 120);      // сегодня раньше 04:10 — «только что»
        if (t0 <= now()) {
          series.net_down.push([t0, dn]);
          series.net_up.push([t0, upv]);
          if (full) { FULL_T.forEach(function (t, j) { series[t].push([t0 + 60, r3(TV[t][0] * (1 + noise(k + 17 + j) * 0.02))]); }); }
        }
      }
      if (!grey) {
        series.rtt.push([day, m.rtt]); series.jitter.push([day, m.jit]); series.loss.push([day, m.loss]);
        ['dns', 'steal_p95', 'cpu_cv', 'cpu_sustain', 'cpu_canary_cv'].forEach(function (t, j) { series[t].push([day, r3(TV[t][0] * (1 + noise(k + 31 + j) * 0.05))]); });
      }
      days.push({ day: day, color: col, reason: rs ? { code: col === 'red' ? 'loss' : 'warn', args: {}, msg: rs } : null, test: test, full: full, partial: false, m: m, checks: checks });
    }
    var nOf = function (t) {
      if (few) { return 2; }
      if (fewLinks) { return t === 'cpu_mt' || t === 'chacha_1t' ? 3 : 2; }
      return PROBE_T.indexOf(t) >= 0 ? 30 : 12;
    };
    var last = function (t) { var s = series[t]; return s.length ? s[s.length - 1][1] : null; };
    var links = LK.map(function (l) {
      var parts = l[1].map(function (t) {
        var n = nOf(t), u = n >= 3 ? TV[t][0] : null;
        var f = stale && PROBE_T.indexOf(t) < 0 ? null : last(t);
        if (t === 'loss' && dip) { f = 0.4; }
        if (young && ['rtt', 'jitter', 'loss'].indexOf(t) >= 0) { return Object.assign({ test: t, usual_raw: null, usual_n: 0, unit: TV[t][1] }, youngWhy); }
        return { test: t, fresh_raw: f, usual_raw: u, usual_n: n, unit: TV[t][1], rel: n >= 3 ? relOf(t, f, u) : null };
      });
      var rels = parts.map(function (p) { return p.rel; }).filter(function (r) { return r !== null; });
      var rel = !rels.length ? null : l[3] ? median(rels) : Math.min.apply(null, rels);
      var one = l[3] ? null : parts[0];
      var t1 = one ? one.test : null, sp = one ? TV[t1][3] : 0, u1 = one ? one.usual_raw : null;
      var row = { id: l[0], source: l[2], rel: rel, test: t1, fresh_raw: one ? one.fresh_raw : null, fresh_n: 3, unit: one ? one.unit : null,
        usual_raw: u1, usual_n: Math.max.apply(null, parts.map(function (p) { return p.usual_n; })), usual_mode: null,
        p25_raw: u1 !== null ? r3(u1 * (1 - sp)) : null, p75_raw: u1 !== null ? r3(u1 * (1 + sp)) : null, now_id: null, now_day: null,
        dev: null, dev_test: null, dev_pending: null, dev_pending_kind: null, parts: parts, hint: null };
      if (l[0] === 'up' && (dip || conf)) {
        Object.assign(row, { dev_pending: 'down', dev_pending_kind: dip ? 'retry_wait' : 'retry_confirmed', hint: { test: 'net_up', now: dip ? 5 : 6, usual: 495, unit: 'Мбит/с', days: null } });
      }
      if (l[0] === 'up' && two) {
        Object.assign(row, { dev: 'down', dev_test: 'net_up', hint: { test: 'net_up', now: 120, usual: 495, unit: 'Мбит/с', days: [[dayOf(now() - 86400), 130], [dayOf(now()), 120]] } });
        row.fresh_raw = 120; row.rel = relOf('net_up', 120, 495); row.parts[0].fresh_raw = 120; row.parts[0].rel = row.rel;
      }
      // ?nd=up — узел в России (стенд it1, Л3 №8): отдача не меряется, причина раннера — у звена и его теста
      if (l[0] === 'up' && Q.get('nd') === 'up') {
        var nw = { fresh_raw: null, rel: null, why: 'no_upload_ru', why_text: 'Не измерено: сервер в России, а точки для замера отдачи нет' };
        Object.assign(row, nw); Object.assign(row.parts[0], nw);
      }
      if (young && one && one.why === 'young') { Object.assign(row, youngWhy); }
      if (l[0] === 'loss' && dip) {
        Object.assign(row, { dev_pending: 'down', dev_pending_kind: 'day', hint: { test: 'loss', now: 0.4, usual: 0.05, unit: '%', days: [[dayOf(now()), 0.4]] } });
      }
      return row;
    });
    var have = links.map(function (l) { return l.rel; }).filter(function (r) { return r !== null; });
    return { ok: true, charts: true, dev_rule: RULE, links: links, provisional: young, days: days, series: series, rows: { people: null, load: null }, stab: null,
      summary: { rel_total: have.length >= 3 ? median(have) : null, rel_n: have.length, rel_need: 3, last_id: RUNS.length ? RUNS[0].id : null,
        last_at: now() - (stale ? 52 : 0) * 86400, fresh_from: null, first: false, stale: stale,
        checked: stale || few ? null : dip ? { part: 'day', hhmm: '14:52' } : conf ? { part: 'day', hhmm: '16:14' } : { part: 'night', hhmm: '04:10' },
        retry: dip ? { at: '16:10', links: ['up'], link: 'up', test: 'net_up', value: 5, unit: 'Мбит/с' } : null,
        base_since: LONG ? [{ kind: 'engines', day: dayOf(now() - 3 * 86400) }, { kind: 'tariff', day: dayOf(now() - 86400) }] : [] } };
  }
  // «Проверить сервер» (6C): цена вида с ключом, предел дня (?bench=limit|limitfull — тело 429), программы (?pkgs=1)
  function benchPrice(full) { return { rx_mb: full ? 2000.03 : 250.03, tx_mb: full ? 2000 : 250, cpu_min: full ? 1 : 0.9, basis: 'caps', max_min: 6, confirm: full ? 'f0f0f0f0' : '0a0a0a0a' }; }
  function benchOver(bq, full) {
    if (!(bq === 'limit' || (bq === 'limitfull' && full))) return null;
    return { ok: false, code: 'limit', limits: { per_day: 6, full_per_day: 2 }, today: { all: bq === 'limit' ? 6 : 2, full: 2 },
      error: bq === 'limit' ? L('Сегодня из панели уже 6 проверок — это предел дня.', 'There have already been 6 checks from the panel today — the limit of a day.') : L('Сегодня из панели уже 2 полные проверки — это предел дня.', 'There have already been 2 full checks from the panel today — the limit of a day.') };
  }
  // up — набор полной проверки с замером отдачи (С2: iperf3 только в нём; ?pkgs=3 — нет только iperf3)
  function benchPkgs(up) {
    var q = Q.get('pkgs'), i3 = !!up && (q === '1' || q === '3');
    if (q === '1' && !i3) return { missing: ['sysbench', 'fio'], available: true, install: ['fio', 'libaio1t64', 'sysbench'], upgrade: [], kib: 2380, why: null, question: L('Поставить sysbench, fio (≈ 2 МБ)?', 'Install sysbench, fio (≈ 2 MB)?') };
    if (!i3) return { missing: [], available: true, install: [], kib: 0, why: null, question: null };
    var miss = (q === '1' ? ['sysbench', 'fio'] : []).concat(['iperf3']);
    return { missing: miss, available: true, install: (q === '1' ? ['fio', 'libaio1t64', 'sysbench'] : []).concat(['iperf3']), upgrade: [],
      kib: (q === '1' ? 2380 : 0) + 330, why: null, question: L('Поставить ' + miss.join(', ') + '?', 'Install ' + miss.join(', ') + '?') };
  }
  function dayOf(ts) { var d = new Date((ts + OFF) * 1000); return d.toISOString().slice(0, 10); }

  var base = M.fetch;
  M.fetch = function (url, init) {
    init = init || {};
    var method = (init.method || 'GET').toUpperCase();
    var u = new URL(url, location.href), path = u.pathname.replace(/^.*?(\/api\/)/, '/api/').replace(/\/+$/, '');
    var body = {};
    try { body = init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    var hdr = init.headers || {};
    var m;
    if (path === '/api/host/bench/schedule' && method === 'GET') { return later(120, function () { var d = doc(); return new Response(JSON.stringify(d), { status: 200, headers: { 'Content-Type': 'application/json', ETag: d.etag } }); }); }
    if (path === '/api/host/bench/schedule/preview' && method === 'POST') {
      var entries = Array.isArray(body.schedule) ? body.schedule.map(clean) : [];
      var errs = check(entries);
      seq += 1;
      return later(140, function () {
        return new Response(JSON.stringify({ ok: true, schedule: entries, errors: errs, cost: cost(entries, body.retry !== undefined ? body.retry : retry), next: nextRuns(entries) }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
    }
    if (path === '/api/host/bench/schedule' && method === 'PUT') {
      var want = hdr['If-Match'] || hdr['if-match'];
      if (!want) { return resp(428, { ok: false, code: 'need_etag', error: L('Нет версии расписания (If-Match).', 'No schedule version (If-Match).') }); }
      if (want !== etag()) { return resp(409, { ok: false, code: 'stale', error: L('Расписание уже изменили.', 'The schedule has changed.') }); }
      if (Q.get('tzerr') === '1' || ('tz' in body && body.tz !== TZ)) { return resp(409, { ok: false, code: 'tz_changed', error: L('Часовой пояс сервера сменился.', 'The server time zone changed.') }); }
      var ent = Array.isArray(body.schedule) ? body.schedule.map(clean) : [];
      var bad = check(ent);
      if (bad.length) { return resp(400, { ok: false, code: 'invalid', error: bad[0].text, errors: bad }); }
      var rt = typeof body.retry === 'boolean' ? body.retry : retry;
      var c = cost(ent, rt);
      if (c.notes.some(function (x) { return x.code === 'heavy'; }) && body.confirm !== true) { return resp(409, { ok: false, code: 'need_confirm', error: L('Подтвердите много трафика.', 'Confirm the large traffic.'), cost: c }); }
      list = ent.map(function (e) { return Object.assign({}, e, { id: e.id || ('b' + Math.random().toString(16).slice(2, 7)) }); });
      retry = rt;
      ver += 1;
      return later(300, function () { var d = doc(); d.apply = { applied: true, armed: d.armed, why: null }; return new Response(JSON.stringify(d), { status: 200, headers: { 'Content-Type': 'application/json', ETag: d.etag } }); });
    }
    if (path === '/api/host/bench/compare' && method === 'GET') {
      var a = RUNS.find(function (r) { return r.id === u.searchParams.get('a'); }), b = RUNS.find(function (r) { return r.id === u.searchParams.get('b'); });
      if (!a || !b) { return resp(404, { ok: false, error: L('Такой проверки нет.', 'No such check.') }); }
      var da = detail(a), db = detail(b), ta = {}, tb = {};
      da.tests.forEach(function (x) { ta[x.test] = x; });
      db.tests.forEach(function (x) { tb[x.test] = x; });
      var keys = Object.keys(ta).concat(Object.keys(tb).filter(function (k) { return !ta[k]; }));
      var rows = keys.map(function (k) {
        var x = ta[k] || {}, y = tb[k] || {};
        var d = typeof x.value === 'number' && typeof y.value === 'number' ? Math.round((y.value - x.value) * 1000) / 1000 : null;
        return { test: k, a: x.value === undefined ? null : x.value, b: y.value === undefined ? null : y.value, delta: d,
          delta_pct: d !== null && x.value ? Math.round(1000 * d / x.value) / 10 : null, unit: x.unit || y.unit, dir: LOWER_T[k] ? 'lower' : 'higher' };
      });
      // «от обычного» каждой проверки — к «обычно» на её время; у неудачной — нет
      var out = { ok: true, a: da, b: db, mode_differs: a.mode !== b.mode, rows: rows, rel_a: relRun(a), rel_b: relRun(b) };
      if (out.rel_a !== null && out.rel_b !== null) { out.better = out.rel_a === out.rel_b ? 'same' : out.rel_a > out.rel_b ? 'a' : 'b'; }
      return resp(200, out);
    }
    if (path === '/api/host/bench/export' && method === 'GET') {
      var fmt = u.searchParams.get('format') || 'csv';
      var from = u.searchParams.get('from'), to = u.searchParams.get('to');
      var rs = RUNS.filter(function (r) { return (!from || dayOf(r.ts) >= from) && (!to || dayOf(r.ts) <= to); }).slice().reverse();
      var name = 'kaskad-bench-' + (from || dayOf(now() - 30 * 86400)).replace(/-/g, '') + '-' + (to || dayOf(now())).replace(/-/g, '') + '.' + fmt;
      var text = fmt === 'json' ? JSON.stringify({ v: 1, tz: TZ, runs: rs.map(detail) }) :
        'run_id,run_ts_iso,mode,by,status,why,test,value,unit,under_load\n' + rs.map(function (r) {
          return r.id + ',' + new Date(r.ts * 1000).toISOString() + ',' + r.mode + ',' + r.by + ',' + r.status + ',' + (r.why || '') + ',,,,' + (r.under_load ? 1 : 0);
        }).join('\n') + '\n';
      return later(150, function () { return new Response(text, { status: 200, headers: { 'Content-Type': fmt === 'json' ? 'application/json; charset=utf-8' : 'text/csv; charset=utf-8', 'Content-Disposition': 'attachment; filename="' + name + '"' } }); });
    }
    // окно цены — чтением, как сервер (T-3377 Н-15): GET /api/host/bench/price — 200 с ценой, пределом дня и «уже идёт» в теле
    if (path === '/api/host/bench/price' && method === 'GET') {
      var BQp = Q.get('bench') || '', lim = {}, modes = {};
      if (BQp === 'running') { return resp(200, { ok: true, running: 'fedcba9876', modes: {}, limit: {}, pkgs: null, upload_ru: null }); }
      ['quick', 'full'].forEach(function (md) {
        var over = benchOver(BQp, md === 'full');
        if (over) lim[md] = { text: over.error, limits: over.limits, today: over.today }; else modes[md] = benchPrice(md === 'full');
      });
      // D-144: сервер, который может быть в России (?ru=0 — за рубежом), — вопрос об отдаче у полной
      var upq = Q.get('ru') !== '0' && modes.full ? { text: L('Замерить отдачу до серверов замера скорости российских провайдеров (iperf3): выбранная точка, а если она недоступна — следующая по списку, увидит адрес этого сервера', 'Measure the upload to the speed test servers of Russian providers (iperf3): the chosen point, and when it does not answer the next one of the list, will see the address of this server'),
        choices: [[1, L('Замерить отдачу', 'Measure the upload')], [2, L('Не замерять отдачу', 'Do not measure the upload')]], default: 1, pkgs: benchPkgs(true) } : null;
      return resp(200, { ok: true, running: null, modes: modes, limit: lim, pkgs: Object.keys(modes).length ? benchPkgs() : null, upload_ru: upq });
    }
    if ((m = /^\/api\/host\/bench\/([^/]+)$/.exec(path)) && method === 'GET') {
      var one = RUNS.find(function (r) { return r.id === m[1]; });
      if (!/^[0-9a-f]{10}$/.test(m[1]) || !one) { return resp(404, { ok: false, error: L('Такой проверки нет.', 'No such check.') }); }
      return later(120, function () { return new Response(JSON.stringify(detail(one)), { status: 200, headers: { 'Content-Type': 'application/json' } }); });
    }
    // 6C «Сейчас»: «от обычного» (score=none|stale|low|first|young|few), день полосы; запуск — как сервер: без ключа — 409 need_confirm с
    // ценой, чужой ключ — confirm_changed, программы без ответа — need_install (pkgs=1), bench=running|limit|limitfull
    // «Поделиться» (C9/C10; GET /api/host/share): как сервер (lib/kaskad/bench/share.py) — только поля кадра; галочки
    // tariff|country|cpu строго 0|1, иначе 400; score=none — проверок нет, stale — давняя; ?sharecpu=bad — модель
    // процессора отброшена (dropped)
    if (path === '/api/host/share' && method === 'GET') {
      var bad = null, on = {};
      u.searchParams.forEach(function (v, k) { if (['tariff', 'country', 'cpu'].indexOf(k) < 0 || (v !== '0' && v !== '1')) { bad = bad || k; } else { on[k] = v === '1'; } });
      if (bad) { return resp(400, { ok: false, error: L('Галочки «Поделиться» — tariff, country, cpu со значением 0 или 1.', 'The switches of “Share” are tariff, country, cpu with 0 or 1.'), data: { field: ['tariff', 'country', 'cpu'].indexOf(bad) >= 0 ? bad : 'query' } }); }
      var sd = scoreDoc(), sm = sd.summary || {};
      if (!sd.links || !sm.last_at) { return later(120, function () { return new Response(JSON.stringify({ v: 1, ok: true, ready: false, why: 'no_check' }), { status: 200, headers: { 'Content-Type': 'application/json' } }); }); }
      if (sm.stale) { return later(120, function () { return new Response(JSON.stringify({ v: 1, ok: true, ready: false, why: 'stale' }), { status: 200, headers: { 'Content-Type': 'application/json' } }); }); }
      var byId = {};
      sd.links.forEach(function (l) { byId[l.id] = l; });
      var rttP = (byId.net_q && byId.net_q.parts || []).find(function (x) { return x.test === 'rtt'; });
      var mv = function (v) { return typeof v === 'number' ? v : null; };
      var dropCpu = Q.get('sharecpu') === 'bad';
      // at — секунды проверки (время UTC и МСК рядом с датой), where — «чьи это цифры» (b5cd50fb): точка теста 8 и DNS
      var out = { v: 1, ok: true, ready: true, why: null, date: sd.days[sd.days.length - 1].day, at: Math.floor(sm.last_at),
        where: { down: 'cloudflare', up: 'cloudflare', anchors: ['cloudflare', 'google', 'yandex'], n: 3 },
        checked: { part: (sm.checked && sm.checked.part) || 'mixed', people: false },
        metrics: [{ id: 'down', v: mv(byId.down && byId.down.fresh_raw), unit: 'Мбит/с' }, { id: 'up', v: mv(byId.up && byId.up.fresh_raw), unit: 'Мбит/с' },
          { id: 'rtt', v: mv(rttP && rttP.fresh_raw), unit: 'мс' }, { id: 'loss', v: mv(byId.loss && byId.loss.fresh_raw), unit: '%' }],
        rel_total: sm.rel_total, rel_n: sm.rel_n, rel_need: sm.rel_need, days: sd.days.map(function (d) { return d.color; }),
        extra: { tariff: on.tariff ? { vcpu: 2, ram_gb: 4, port_mbit: 1000 } : null, country: on.country ? 'FI' : null, cpu: on.cpu && !dropCpu ? 'AMD EPYC 7763 64-Core Processor' : null },
        dropped: on.cpu && dropCpu ? ['cpu'] : [] };
      return later(160, function () { return new Response(JSON.stringify(out), { status: 200, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } }); });
    }
    if (path === '/api/host/score' && method === 'GET') { return later(120, function () { return new Response(JSON.stringify(scoreDoc()), { status: 200, headers: { 'Content-Type': 'application/json' } }); }); }
    if ((m = /^\/api\/host\/day\/([^/]+)$/.exec(path)) && method === 'GET') {
      var dd = scoreDoc().days ? scoreDoc().days.find(function (x) { return x.day === m[1]; }) : null;
      if (!dd) { return resp(404, { ok: false, error: L('Такого дня в полосе нет.', 'No such day in the strip.'), field: 'day' }); }
      // звенья дня — без parts и отметок
      var dl = scoreDoc().links.map(function (l) {
        return { id: l.id, rel: l.rel, test: l.test, fresh_raw: l.fresh_raw, unit: l.unit, usual_raw: l.usual_raw, usual_n: l.usual_n, now_day: m[1], dev: null };
      });
      return later(120, function () { return new Response(JSON.stringify(Object.assign({ ok: true, charts: true, links: dl }, dd)), { status: 200, headers: { 'Content-Type': 'application/json' } }); });
    }
    // порог ▲/▼ (UI-C, POST /api/host/dev-rule): ровно {pct: целое 5…30}
    if (path === '/api/host/dev-rule' && method === 'POST') {
      var keys = Object.keys(body || {});
      if (keys.length !== 1 || keys[0] !== 'pct' || typeof body.pct !== 'number' || body.pct % 1 !== 0 || body.pct < 5 || body.pct > 30) {
        return resp(400, { ok: false, error: L('Порог отметок — целое число от 5 до 30.', 'The threshold of the marks is a whole number from 5 to 30.'), field: 'pct' });
      }
      var was = RULE.pct; RULE = { pct: body.pct, use_spread: RULE.use_spread };
      return later(150, function () { return new Response(JSON.stringify({ ok: true, dev_rule: RULE, changed: was !== RULE.pct }), { status: 200, headers: { 'Content-Type': 'application/json' } }); });
    }
    if (path === '/api/host/bench' && method === 'POST') {
      if (body.mode !== 'quick' && body.mode !== 'full') { return resp(400, { ok: false, code: 'invalid', error: L('Параметр «mode»: такого значения нет.', 'Parameter “mode”: no such value.'), field: 'mode' }); }
      var full = body.mode === 'full';
      var pr = benchPrice(full);
      var BQ = Q.get('bench') || '';
      if (BQ === 'running') { return resp(409, { ok: false, code: 'running', id: 'fedcba9876', error: L('Уже идёт проверка.', 'A check is already running.') }); }
      var over0 = benchOver(BQ, full);
      if (over0) { return resp(429, over0); }
      var PK = benchPkgs(full && body.iperf !== false && Q.get('ru') !== '0');
      if (body.confirm !== pr.confirm) {
        return resp(409, { ok: false, code: body.confirm ? 'confirm_changed' : 'need_confirm', price: pr,
          pkgs: PK,
          error: L('Проверка тратит трафик и время процессора — посмотрите цену и подтвердите. Ничего не запущено.', 'A check uses traffic and CPU time — look at its price and confirm. Nothing was started.') });
      }
      if (PK.missing.length && typeof body.install !== 'boolean') { return resp(409, { ok: false, code: 'need_install', price: pr, pkgs: PK, error: L('Для проверки нужны программы — ответьте, ставить ли их. Ничего не запущено.', 'The check needs programs — answer whether to install them. Nothing was started.') }); }
      if (body.iperf !== undefined && typeof body.iperf !== 'boolean') { return resp(400, { ok: false, code: 'invalid', error: L('Параметр «iperf»: такого значения нет.', 'Parameter “iperf”: no such value.'), field: 'iperf' }); }
      return resp(200, { ok: true, id: 'abcdef0123', unit: 'kaskad-bench-abcdef0123.service', mode: body.mode, missing: [], price: pr,
        upload_ru: full && body.iperf !== false && Q.get('ru') !== '0' });
    }
    if (path === '/api/host/bench' && method === 'GET') {
      var f2 = u.searchParams.get('from'), t2 = u.searchParams.get('to'), mode = u.searchParams.get('mode'), by = u.searchParams.get('by'), before = Number(u.searchParams.get('before') || 0);
      var lo = f2 ? null : now() - 30 * 86400;
      var got = RUNS.filter(function (r) {
        return (!f2 || dayOf(r.ts) >= f2) && (!t2 || dayOf(r.ts) <= t2) && (lo === null || r.ts >= lo) && (!mode || r.mode === mode) && (!by || r.by === by) && (!before || r.ts < before);
      });
      // rel — «от обычного» проверки (UI-C: сервер — check_rel к «обычно» на её время)
      got = got.map(function (r) { return Object.assign({}, r, { rel: relRun(r) }); });
      return later(160, function () { return new Response(JSON.stringify({ ok: true, runs: got.slice(0, 40), more: got.length > 40, db: true, charts: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }); });
    }
    return base(url, init);
  };
})(window);
