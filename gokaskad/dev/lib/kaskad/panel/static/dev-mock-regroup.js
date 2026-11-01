/* Kaskad v3.3.7.15 (F14) — демо «Разложить заново» для ?mock=1 (поверх dev-mock.js): POST /api/gw/groups/<gid>/regroup
   как policy_ops.group_regroup — прогон {dry: true} (before / after по выходам группы, moves, moved, also, text, note,
   approx) и запрос {} (сразу {accepted, wait_s, text, note}; через 2,5 с — событие group_regrouped ленты, а «Устройств» у
   выходов группы — как в предпросмотре). Только для проверки вида без сервера (снимки, k3_layout_check.mjs).
   Раскладка мока: устройства активного уровня (первый уровень с работающим выходом) — поровну по его выходам.
   Сцены — параметром адреса rg=:
     hold      прогон не отвечает — «Считаем…»            err      прогон — 500 («Не удалось посчитать»)
     none      переезжать некому                         also     ещё 2 — по правилам группы
     frozen    после «Разложить» — событие «раскладка заморожена» (ошибка)
     late      после «Разложить» — событие «ждал дольше 5 мин» (внимание)
     r409      прогон — 409 «Раскладка заморожена: у сервера нет связи» (панель не знала заранее)
   Без rg= — обычный ход. */
(function (g) {
  'use strict';
  var M = g.KASKAD_MOCK;
  if (!M || !M.fetch) { return; }
  var Q = new URLSearchParams(location.search);
  var scene = Q.get('rg') || '';
  var base = M.fetch;
  var over = {};                                   // exit -> устройств после «Разложить» (GET /api/gw/exits)
  var EN = function () { return typeof document !== 'undefined' && document.documentElement.lang === 'en'; };
  var T = {
    ru: { many: ['Переедет примерно {n} устройство', 'Переедут примерно {n} устройства', 'Переедут примерно {n} устройств'],
      none: 'Все устройства уже на своих выходах — переезжать некому.',
      note: 'Открытые соединения доживут; у переехавших сайты увидят другой адрес — банки и почта могут попросить войти заново.',
      acc: 'Запрос принят: сборщик переложит устройства в течение минуты.',
      conn: 'В группе «по соединениям» раскладывать нечего: каждое новое соединение само выбирает выход.',
      frozen: 'Раскладка заморожена: у сервера нет связи. Разложить заново можно, когда связь вернётся.',
      ev: '«{g}»: устройства разложены заново — {n}', mv: ['переехало {n} устройство', 'переехали {n} устройства', 'переехали {n} устройств'], evNone: '«{g}»: разложено заново — все устройства уже на своих выходах',
      evFrozen: '«{g}»: разложить заново не удалось — у сервера нет связи, раскладка заморожена',
      evLate: '«{g}»: разложить заново не удалось — запрос ждал дольше 5 мин, сборщик не работал. Нажмите ещё раз',
      err: 'Сервер не ответил вовремя. Попробуйте позже.' },
    en: { many: ['About {n} device will move', 'About {n} devices will move'], none: 'Every device is already on its own exit — nobody moves.',
      note: 'Open connections stay up; for the devices that move, sites will see another address — banks and mail may ask to sign in again.',
      acc: 'Request accepted: the collector will move the devices within a minute.',
      conn: 'Nothing to regroup in a “per connection” group: each new connection picks its exit itself.',
      frozen: 'The layout is frozen: the server is offline. You can regroup once it is back online.',
      ev: '“{g}”: devices regrouped — {n}', mv: ['{n} device moved', '{n} devices moved'], evNone: '“{g}”: regrouped — every device is already on its own exit',
      evFrozen: '“{g}”: could not regroup — the server is offline, the layout is frozen',
      evLate: '“{g}”: could not regroup — the request waited over 5 min while the collector was not running. Press it again',
      err: 'The server didn’t respond in time. Try again later.' } };
  function L() { return EN() ? T.en : T.ru; }
  function plural(n, key) {
    var f = L()[key || 'many'];
    if (f.length === 2) return (n === 1 ? f[0] : f[1]).replace('{n}', n);
    var m10 = n % 10, m100 = n % 100;
    return f[m10 === 1 && m100 !== 11 ? 0 : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? 1 : 2].replace('{n}', n);
  }
  function json(status, doc) { return Promise.resolve(new Response(JSON.stringify(doc), { status: status, headers: { 'Content-Type': 'application/json' } })); }
  function fail(status, text, code) { return json(status, { ok: false, error: text, code: code || 3 }); }
  function read(path) { return base(path, { method: 'GET', headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); }); }
  // раскладка мока: устройства первого уровня с работающим выходом — поровну по его работающим выходам (в порядке уровня)
  function plan(gid) {
    return Promise.all([read('/api/gw/policy'), read('/api/gw/exits'), read('/api/gw/people')]).then(function (d) {
      var grp = ((d[0].policy || {}).groups || []).filter(function (x) { return x.id === gid; })[0];
      if (!grp) return null;
      var rows = {}; (d[1].exits || []).forEach(function (r) { rows[r.id] = r; });
      var cnt = function (id) { return over[id] !== undefined ? over[id] : (rows[id] && rows[id].devices) || 0; };
      var before = {}, after = {};
      grp.tiers.forEach(function (tr) { tr.exits.forEach(function (id) { if (cnt(id)) { before[id] = cnt(id); after[id] = cnt(id); } }); });
      var tier = grp.tiers.filter(function (tr) { return tr.exits.some(function (id) { return rows[id] && rows[id].usable; }); })[0];
      var moves = [];
      if (tier && scene !== 'none') {
        var ok = tier.exits.filter(function (id) { return rows[id] && rows[id].usable; });
        var total = tier.exits.reduce(function (s, id) { return s + cnt(id); }, 0);
        tier.exits.forEach(function (id) { delete after[id]; });
        ok.forEach(function (id, i) { var n = Math.floor(total / ok.length) + (i < total % ok.length ? 1 : 0); if (n) after[id] = n; });
        var devs = (d[2].devices || []).slice(), users = {};
        (d[2].users || []).forEach(function (u) { users[u.id] = u; });
        var from = [], to = [];
        tier.exits.forEach(function (id) { var x = (before[id] || 0) - (after[id] || 0); for (var k = 0; k < x; k++) from.push(id); for (var j = 0; j < -x; j++) to.push(id); });
        from.forEach(function (id, i) { var dv = devs[i % Math.max(1, devs.length)] || { id: 'd-0000000' + i, name: 'device ' + (i + 1) };
          moves.push({ device: dv.id, name: dv.name, from: id, to: to[i] || null, why: 'rebalance' }); });
      }
      var moved = moves.length;
      if (scene === 'also') moves.push({ device: 'd-00000001', name: '', from: null, to: null, why: 'evict' }, { device: 'd-00000002', name: '', from: null, to: null, why: 'evict' });
      return { grp: grp, before: before, after: after, moves: moves.slice(0, 50), moved: moved, also: moves.length - moved };
    });
  }
  M.fetch = function (url, init) {
    var u = new URL(String(url), location.href), path = u.pathname.replace(/^.*?\/api\//, '/api/'), method = ((init && init.method) || 'GET').toUpperCase();
    // данные выходов после «Разложить»: «Устройств» — как в предпросмотре
    if (method === 'GET' && path === '/api/gw/exits' && Object.keys(over).length) {
      return base(url, init).then(function (r) { return r.json().then(function (doc) {
        (doc.exits || []).forEach(function (row) { if (over[row.id] !== undefined) row.devices = over[row.id]; });
        return new Response(JSON.stringify(doc), { status: r.status, headers: { 'Content-Type': 'application/json' } }); }); });
    }
    var m = /^\/api\/gw\/groups\/([a-z0-9-]{1,40})\/regroup$/.exec(path);
    if (!m || method !== 'POST') return base(url, init);
    var gid = m[1], body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    var dry = !!body.dry;
    return plan(gid).then(function (p) {
      if (!p) return json(404, { ok: false, error: 'Группы «' + gid + '» нет.', code: 'gone' });
      if (gid === 'main' && p.grp.per_connection) return fail(409, L().conn);
      if (scene === 'r409') return fail(409, L().frozen);
      if (dry) {
        if (scene === 'hold') return new Promise(function () { /* «Считаем…» — ответа нет */ });
        if (scene === 'err') return new Promise(function (ok) { setTimeout(function () { ok(fail(500, L().err, 1)); }, 400); });
        var res = { ok: true, dry: true, group: gid, moves: p.moves, moved: p.moved, also: p.also, before: p.before, after: p.after, approx: true,
          text: p.moved ? plural(p.moved) : L().none, note: L().note };
        return new Promise(function (ok) { setTimeout(function () { ok(json(200, res)); }, 500); });
      }
      setTimeout(function () {
        var name = p.grp.name || (gid === 'main' ? (EN() ? 'Main' : 'Основная') : gid), args = { group: gid, role: p.grp.role || 'abroad', moved: p.moved, group_name: name };
        if (scene === 'frozen') args.frozen = 'offline';
        if (scene === 'late') args.late = true;
        if (!args.frozen && !args.late) Object.keys(p.after).concat(Object.keys(p.before)).forEach(function (id) { over[id] = p.after[id] || 0; });
        var text = (args.frozen ? L().evFrozen : args.late ? L().evLate : p.moved ? L().ev : L().evNone).replace('{g}', name).replace('{n}', plural(p.moved, 'mv'));
        var ev = { id: Date.now(), ts: Math.floor(Date.now() / 1000), kind: 'group_regrouped', key: 'gw_live.event.group_regrouped', text: text, args: args };
        if (M.emit) M.emit('alert', ev);
      }, 2500);
      return json(200, { ok: true, accepted: true, group: gid, wait_s: 60, text: L().acc, note: L().note });
    });
  };
})(window);
