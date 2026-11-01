/* Kaskad v3.3.7.15 (F10–F13, F12а) — демо «Настройки › Сервер › Адреса сервера» для ?mock=1 (поверх dev-mock.js):
   GET /api/gw/addresses (compose: addresses, addresses6, checked_at; mip; page — строки с кодами, как
   gw/addresses.page; ver) и записи PUT …/in, …/in6, …/give, …/direct, …/out, …/exit, POST …/reset с ver из чтения
   и POST …/spread (F12б «Разнести выходы»; прогон dry — без ver) (нет ver — 400 ver_required, другой — 409 object_changed), замками (409 отказ словами) и подтверждениями (409
   need: "confirm" с consequences; тот же запрос с yes: true пишет). Только для проверки вида без сервера.
   Адреса по умолчанию (кадр Д1): IPv4 — адрес сервера (host=, по умолчанию 203.0.113.5; кадры макета —
   host=203.0.113.10), 203.0.113.11, 198.51.100.7 (NAT 10.0.0.5), 198.51.100.8 (добавлен вручную, на сервере его нет);
   IPv6 — 2001:db8:10::5 (статический, в AAAA-записи, выдаётся строкой) и 2001:db8:10::6 (SLAAC). Домен в строках
   «Домен» — домен сайта (site=ok и др. в dev-mock.js); A-записи домена мока — на адрес сервера и 203.0.113.11.
   Сцены — параметром адреса adr=:
     c1  один IPv4, IPv6 нет            c2  IPv6 нет      c3  IPv6 выключен в ядре (ipv6_off: true)
     c4  IPv6 без маршрута наружу        c5  адрес пропал (как по умолчанию: 198.51.100.8)
     c6  префикс IPv6 сменился (событие ленты ip6_prefix_changed; старый адрес устаревает, 3 ч)
     c7  IPv6 снаружи виден другим адресом   c8  загрузка (ответа нет)   c9  ошибка чтения (500)
     c10 шлюз выключен (GET /api/gw — enabled: false)
   Обрыв связи (П2 REV-3315-fin: запись при VLESS Reality перезапускает xray — панель за 443 теряет ответ):
     drop     запись прошла, её ответ оборвался, чтения ~1,5 с после неё — тоже (перечитывание через 2 с отвечает)
     dropget  запись прошла и ответила, следующее чтение оборвалось (одно)
     down     запись прошла, ответ оборвался, чтения не отвечают 15 с (перечитывание не выходит — обычная ошибка)
     empty    запись прошла, ответ — пустое тело (200)
     after    запись ответила с xray_after: true (ядро П3: перезапуск xray — после ответа): чтения 1,5 с обрываются,
              дальше чтения несут warnings «xray не перезапустился…» (как ядро: пока xray не догонит; здесь — до конца)
   Без adr= — обычный ход (Д1); 203.0.113.11 — второй адрес eth0 (secondary). ex6=1 — ещё выход «Стокгольм · VLESS» по
   IPv6 (адрес узла 2001:db8:50::7): узел «маршрут IPv6», пунктир и строка «подключается по IPv6» в «Выходе». */
(function (g) {
  'use strict';
  var M = g.KASKAD_MOCK;
  if (!M || !M.fetch) { return; }
  var Q = new URLSearchParams(location.search);
  var scene = Q.get('adr') || '', EX6 = Q.get('ex6') === '1';
  var SRV = /^\d{1,3}(\.\d{1,3}){3}$/.test(Q.get('host') || '') ? Q.get('host') : '203.0.113.5';
  var IP2 = SRV === '203.0.113.11' ? '203.0.113.12' : '203.0.113.11', DOM = 'example.com';
  var R5 = '2001:db8:10::5', R6 = '2001:db8:10::6', N5 = '2001:db8:20::5';
  var base = M.fetch;
  var EN = function () { return typeof document !== 'undefined' && document.documentElement.lang === 'en'; };
  var L = function (ru, en) { return EN() ? en : ru; };
  var now = function () { return Math.floor(Date.now() / 1000); };
  var iso = function (s) { return new Date(s * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'); };

  // ── состояние мока ─────────────────────────────────────────────────────────────────────────────────────────────────
  var st = {
    v4: [{ ip: SRV, iface: 'eth0', origin: 'iface', found: true, primary: true, client: true, route_ok: true, route: 'default', in_dns: true },
      { ip: IP2, iface: 'eth0', origin: 'iface', found: true, primary: false, client: false, route_ok: true, route: 'default', in_dns: true },
      { ip: '198.51.100.7', iface: 'eth1', origin: 'nat', local: '10.0.0.5', found: true, primary: false, client: false, route_ok: true, route: 'eth1', in_dns: false },
      { ip: '198.51.100.8', iface: '', origin: 'manual', found: false, primary: false, client: false, route_ok: false, route: null, in_dns: false }],
    v6: [{ ip: R5, iface: 'eth0', prefix: 64, deprecated: false, dynamic: false, valid_lft: null, route_ok6: true, echo6: R5, in_dns: true },
      { ip: R6, iface: 'eth0', prefix: 64, deprecated: false, dynamic: true, valid_lft: 86400, route_ok6: true, echo6: R6, in_dns: false }],
    in4: [SRV, IP2, '198.51.100.7'], in6: null, give6: R5, out: [SRV, IP2], direct: null, src: {}, at: now() - 120,
    panel4: [SRV], panel6: [], aaaa: [R5]
  };
  if (scene === 'c1') { st.v4 = st.v4.slice(0, 1); st.v6 = []; st.in4 = null; st.out = null; st.give6 = null; st.aaaa = []; }
  if (scene === 'c2' || scene === 'c3') { st.v6 = []; st.give6 = null; st.aaaa = []; }
  if (scene === 'c4') { st.v6.forEach(function (a) { a.route_ok6 = false; }); st.give6 = null; }
  if (scene === 'c6') {
    st.v6 = [{ ip: R5, iface: 'eth0', prefix: 64, deprecated: true, dynamic: false, valid_lft: 3 * 3600, route_ok6: true, echo6: R5, in_dns: true },
      { ip: N5, iface: 'eth0', prefix: 64, deprecated: false, dynamic: false, valid_lft: null, route_ok6: true, echo6: N5, in_dns: false }];
    if (M.emit) {
      M.emit('alert', { id: Date.now(), ts: now() - 600, kind: 'ip6_prefix_changed', key: 'gw_live.event.ip6_prefix_changed',
        args: { iface: 'eth0', old: '2001:db8:10::/64', new: '2001:db8:20::/64', suggest: N5 },
        text: L('Префикс IPv6 на eth0 сменился: 2001:db8:10::/64 → 2001:db8:20::/64. Новый адрес — ' + N5 + '; роль сама не переносится.',
          'The IPv6 prefix on eth0 changed: 2001:db8:10::/64 → 2001:db8:20::/64. The new address is ' + N5 + '; the role doesn’t move by itself.') });
    }
  }
  if (scene === 'c7') { st.v6[0].echo6 = '2001:db8:77::1'; st.give6 = null; }

  // ── как gw/addresses.page и mip.status ─────────────────────────────────────────────────────────────────────────────
  function found4(ip) { return st.v4.some(function (a) { return a.ip === ip && a.found; }); }
  function a4(ip) { return st.v4.filter(function (a) { return a.ip === ip; })[0] || null; }
  function a6(ip) { return st.v6.filter(function (a) { return a.ip === ip; })[0] || null; }
  function directWhy() {
    var d = st.direct; if (!d) return null;
    var a = a4(d);
    if (!a || !a.found) return 'gone';
    if (st.out && st.out.indexOf(d) < 0) return 'not_out';
    if (a.route_ok !== true) return 'route';
    return null;
  }
  function give6State() {
    var ip = st.give6; if (!ip) return { ip: null, on: false, why: null };
    if (st.in6 && st.in6.indexOf(ip) < 0) return { ip: ip, on: false, why: 'off' };
    var a = a6(ip), why = null;
    if (!a) why = 'gone'; else if (a.deprecated) why = 'deprecated'; else if (!a.route_ok6) why = 'no_route6'; else if (a.echo6 && a.echo6 !== ip) why = 'echo_other';
    return { ip: ip, on: why === null, why: why };
  }
  function page() {
    var acts = !!st.direct && directWhy() === null, v4 = [], v6 = [], here6 = {};
    st.v4.forEach(function (a) {
      var c = [st.in4 === null || st.in4.indexOf(a.ip) >= 0 ? 'accept' : 'off'];
      if (a.origin === 'host' || a.found === false) c.push('gone');
      if (st.panel4.indexOf(a.ip) >= 0) c.push('lock_panel');
      if (a.client) c.push('lock_host');
      if (a.primary && !acts) c.push('route_src');
      if (a.ip === st.direct) c.push('direct');
      v4.push({ ip: a.ip, iface: a.iface || '', codes: c, out: st.out === null || st.out.indexOf(a.ip) >= 0 });
    });
    st.v6.forEach(function (a) {
      here6[a.ip] = 1;
      var c = [st.in6 !== null && st.in6.indexOf(a.ip) < 0 ? 'off' : a.ip === st.give6 ? 'accept_give' : 'reserve'], row = { ip: a.ip, iface: a.iface || '', codes: c };
      if (a.deprecated) c.push('deprecated');
      if (a.dynamic && a.valid_lft !== null) c.push('dynamic');
      if ((a.deprecated || a.dynamic) && typeof a.valid_lft === 'number') row.hours = Math.max(0, Math.ceil((a.valid_lft - Math.max(0, now() - st.at)) / 3600));
      if (!a.route_ok6) c.push('no_route6');
      if (a.echo6 && a.echo6 !== a.ip) { c.push('echo_other'); row.seen_as = a.echo6; }
      if (st.panel6.indexOf(a.ip) >= 0) c.push('lock_panel');
      if (st.aaaa.indexOf(a.ip) >= 0) c.push('lock_aaaa');
      v6.push(row);
    });
    (st.in6 || []).forEach(function (ip) { if (!here6[ip]) { here6[ip] = 1; v6.push({ ip: ip, iface: '', codes: [ip === st.give6 ? 'accept_give' : 'reserve', 'gone'] }); } });
    if (st.give6 && !here6[st.give6] && st.in6 === null) v6.push({ ip: st.give6, iface: '', codes: ['accept_give', 'gone'] });
    return { v4: v4, v6: v6, codes: ['accept', 'accept_give', 'reserve', 'off', 'deprecated', 'dynamic', 'no_route6', 'echo_other', 'gone', 'lock_panel', 'lock_host', 'lock_aaaa', 'route_src', 'direct'] };
  }
  function ver() {
    var s = JSON.stringify([st.in4, st.in6, st.give6, st.out, st.direct, st.src]), h = 5381;
    for (var i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
    return ('00000000' + h.toString(16)).slice(-8);
  }
  function mipDoc() {
    var gone = (st.in4 || []).filter(function (ip) { return !found4(ip); }), dw = directWhy();
    return { 'in': { all: st.in4 === null, list: st.in4 || [], gone: gone, active: !!st.in4 && gone.length < st.in4.length },
      in6: { all: st.in6 === null, list: st.in6 || [], none: !!st.in6 && !st.in6.length, gone: (st.in6 || []).filter(function (ip) { return !a6(ip); }) },
      give6: give6State(), exits: Object.keys(st.src).sort().map(function (id) { return { id: id, src: st.src[id], here: found4(st.src[id]) }; }),
      limits: [], primary: SRV, multi: st.v4.filter(function (a) { return a.found; }).length > 1, h4: null,
      direct: { ip: st.direct, on: !!st.direct && dw === null, why: dw }, out: st.out, back: '17 → 4',
      // REV-3315-b3 З-2: адрес маршрута не «для выхода», а выходы без своего адреса подключаются с него
      route_out: st.out && st.out.indexOf(SRV) < 0 && EXITS.filter(function (x) { return !st.src[x.id]; }).length ? { ip: SRV, ids: EXITS.filter(function (x) { return !st.src[x.id]; }).map(function (x) { return x.id; }) } : null };
  }
  function doc(refresh) {
    if (refresh) st.at = now();
    var v4 = st.v4.map(function (a) { return { ip: a.ip, local: a.local || a.ip, iface: a.iface, origin: a.origin, found: a.found, primary: a.primary, client: a.client, route_ok: a.route_ok, route: a.route, in_dns: a.in_dns,
      secondary: a.origin === 'manual' ? null : a.ip === IP2 }; });
    return { ok: true, addresses: v4, primary: SRV, client: SRV, show: v4.map(function (a) { return a.ip; }), more: [], 'private': ['10.0.0.5'], v6: st.v6.length > 0,
      nat_wanted: false, nat_at: iso(st.at), checked_at: iso(st.at), ipv6_off: scene === 'c3',
      addresses6: st.v6.map(function (a) { return { ip: a.ip, iface: a.iface, prefix: a.prefix, deprecated: a.deprecated, dynamic: a.dynamic, valid_lft: a.valid_lft, route_ok6: a.route_ok6, echo6: a.echo6, in_dns: a.in_dns }; }),
      mip: mipDoc(), page: page(), ver: ver() };
  }

  // ── ответы ─────────────────────────────────────────────────────────────────────────────────────────────────────────
  function json(status, d) { return Promise.resolve(new Response(JSON.stringify(d), { status: status, headers: { 'Content-Type': 'application/json' } })); }
  function no(text, code, data) { return json(409, { ok: false, error: text, code: code || 3, data: data || null }); }
  function confirm(items, yes) {
    if (!items.length || yes) return null;
    return no(L('Нужно подтверждение: ', 'Needs a confirmation: ') + items.join('; '), 3, { reason: 'confirm', need: 'confirm', consequences: items });
  }
  function exitsOf() { return base('/api/gw/exits', { method: 'GET', headers: { Accept: 'application/json' } }).then(function (r) { return r.json(); }).then(function (d) { return (d.exits || []).filter(function (x) { return x && x.id; }); }); }
  var srcDone = false, EXITS = [];                   // выходы IPv4 мока (для route_out и «Разнести»)
  function ready() {
    if (srcDone || scene === 'c1') return Promise.resolve();
    return exitsOf().then(function (ex) {
      srcDone = true;
      var v4 = ex.filter(function (x) { return String(x.host || '').indexOf(':') < 0; });
      EXITS = v4;
      if (v4[1]) st.src[v4[1].id] = IP2;                  // второй выход — с 203.0.113.11 (кадр Д4: «Хельсинки»)
    });
  }
  var names = function (ex, ids) { return ids.map(function (id) { var x = ex.filter(function (e) { return e.id === id; })[0]; return x ? x.name : id; }); };
  function write(path, method, body) {
    var yes = !!body.yes;
    if (path === '/api/gw/addresses/spread' && body.dry) body.ver = body.ver || ver();   // прогон — без ver (как api)
    if (!body.ver) return json(400, { ok: false, error: L('Нужна версия адресов (ver): перечитайте страницу.', 'The version of the addresses (ver) is required: reload the page.'), code: 'ver_required' });
    if (body.ver !== ver()) return no(L('Адреса изменили в другом месте — перечитайте страницу.', 'The addresses were changed elsewhere — reload the page.'), 'object_changed');
    var c, ip = body.ip, list;
    if (path === '/api/gw/addresses/in') {
      list = body.all ? null : (body.ips || []);
      if (list && list.indexOf(SRV) < 0) return no(L('Клиенты подключаются на ' + SRV + ' (адрес для клиентов), а его нет в списке — устройства потеряют связь. Сначала смените адрес для клиентов.',
        'Clients connect to ' + SRV + ' (the client address), and it is not in the list — the devices would lose the connection. Change the client address first.'));
      var closing = st.v4.filter(function (a) { return a.in_dns && a.found && (st.in4 === null || st.in4.indexOf(a.ip) >= 0) && list && list.indexOf(a.ip) < 0; });
      c = confirm(closing.map(function (a) { return L('у домена панели ' + DOM + ' A-запись на ' + a.ip + ': на этом адресе панель и сайт перестанут открываться, а браузер может попасть именно туда',
        'the panel domain ' + DOM + ' has an A record on ' + a.ip + ': the panel and the site stop opening on this address, and the browser may go exactly there'); }), yes);
      if (c) return c;
      st.in4 = list;
      return json(200, { ok: true, 'in': mipDoc()['in'], text: list ? L('Подключения принимаются только на: ' + list.join(', ') + '.', 'Connections are taken only on: ' + list.join(', ') + '.') : L('Подключения принимаются на всех адресах.', 'Connections are taken on every address.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/in6') {
      list = body.all ? null : body.none ? [] : (body.ips || []);
      var shut = st.v6.filter(function (a) { return (st.in6 === null || st.in6.indexOf(a.ip) >= 0) && list && list.indexOf(a.ip) < 0; }), items = [];
      shut.forEach(function (a) {
        if (st.aaaa.indexOf(a.ip) >= 0) items.push(L('у ' + DOM + ' есть AAAA-запись на ' + a.ip + ': устройства с IPv6 пойдут туда и не подключатся — удалите AAAA-запись или оставьте приём',
          DOM + ' has an AAAA record on ' + a.ip + ': devices with IPv6 go there and won’t connect — delete the AAAA record or keep accepting'));
        if (st.give6 === a.ip) items.push(L('IPv6 ' + a.ip + ' выдаётся строкой подписки: пока он не принимает подключения, строк IPv6 в подписке нет',
          'IPv6 ' + a.ip + ' is handed out as a subscription line: while it doesn’t accept connections there are no IPv6 lines in the subscription'));
      });
      c = confirm(items, yes);
      if (c) return c;
      st.in6 = list;
      return json(200, { ok: true, in6: mipDoc().in6, text: list === null ? L('IPv6: подключения принимаются на всех адресах.', 'IPv6: connections are taken on every address.') : !list.length ? L('IPv6: подключения не принимаются.', 'IPv6: no connections are taken.') : L('IPv6: подключения принимаются только на: ' + list.join(', ') + '.', 'IPv6: connections are taken only on: ' + list.join(', ') + '.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/give') {
      ip = body.ip6;
      if (ip && ip.indexOf(':') < 0) return no(L('Выдача по адресам IPv4 — в следующем выпуске.', 'Giving out IPv4 addresses comes in the next release.'));
      c = confirm([ip ? L('VLESS Reality начнёт слушать и IPv6: xray перезапустится — открытые подключения VLESS и Hysteria2 переподключатся', 'VLESS Reality starts listening on IPv6 too: xray restarts — open VLESS and Hysteria2 connections reconnect')
        : L('VLESS Reality перестанет слушать IPv6: xray перезапустится — открытые подключения VLESS и Hysteria2 переподключатся', 'VLESS Reality stops listening on IPv6: xray restarts — open VLESS and Hysteria2 connections reconnect')], yes);
      if (c) return c;
      st.give6 = ip || null;
      return json(200, { ok: true, give6: give6State(), text: ip ? L('Готово: ' + ip + ' выдаётся дополнительной строкой подписки после строк IPv4 — у VLESS Reality, Hysteria2, WireGuard и AmneziaWG; файлы и QR — по-прежнему IPv4.', 'Done: ' + ip + ' goes as an extra subscription line after the IPv4 ones — for VLESS Reality, Hysteria2, WireGuard and AmneziaWG; files and QR codes stay on IPv4.')
        : L('Готово: строки IPv6 в подписке больше нет.', 'Done: the subscription has no IPv6 line any more.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/direct') {
      if (ip) {
        var a = a4(ip);
        if (!a || !a.found) return no(L('Адреса ' + ip + ' на сервере нет.', 'The server has no address ' + ip + '.'));
        if (st.out && st.out.indexOf(ip) < 0) return no(L(ip + ' — не «для выхода»: сначала включите «Для выхода» у этого адреса.', ip + ' is not “for exits”: turn on “For exits” on this address first.'));
      }
      st.direct = ip || null;
      return json(200, { ok: true, direct: mipDoc().direct, text: ip ? L('«Напрямую» — с ' + ip + ': сайты видят ' + ip + '.', '“Direct” goes from ' + ip + ': sites see ' + ip + '.') : L('«Напрямую» — с адреса маршрута ' + SRV + '.', '“Direct” goes from the route address ' + SRV + '.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/out') {
      return exitsOf().then(function (ex) {
        var on = !!body.on, cur = st.out === null ? st.v4.filter(function (a) { return a.found; }).map(function (a) { return a.ip; }) : st.out.slice();
        if (on) { if (st.out !== null && cur.indexOf(ip) < 0) cur.push(ip); st.out = st.out === null ? null : cur;
          return json(200, { ok: true, out: st.out, direct: mipDoc().direct, moved: [], text: L('Готово: с ' + ip + ' можно ходить «напрямую» и к выходам.', 'Done: “direct” and exits can go from ' + ip + '.'), warnings: [], ver: ver() }); }
        var dHere = st.direct === ip || (!st.direct && ip === SRV);
        var exHere = ex.filter(function (x) { return String(x.host || '').indexOf(':') < 0 && (st.src[x.id] === ip || (!st.src[x.id] && ip === SRV)); }).map(function (x) { return x.id; });
        var others = cur.filter(function (x) { var a = a4(x); return x !== ip && a && a.found && a.route_ok; });
        var to = st.direct && st.direct !== ip && others.indexOf(st.direct) >= 0 ? st.direct : others[0];
        if ((dHere || exHere.length) && !to) return no(L('Переносить некуда: другого адреса «для выхода» с путём наружу нет.', 'Nowhere to move: no other “for exits” address with a way out.'));
        var items = [];
        if (dHere) items.push(L('«напрямую» перейдёт на ' + to + ': сайты будут видеть ' + to, '“direct” moves to ' + to + ': sites will see ' + to));
        if (exHere.length) items.push(L('выходы ' + names(ex, exHere).join(', ') + ' будут подключаться с ' + to, 'the exits ' + names(ex, exHere).join(', ') + ' will connect from ' + to));
        if (items.length && ip === SRV) items.push(L('собственный трафик сервера (обновления, бот) останется на адресе маршрута ' + SRV, 'the server’s own traffic (updates, the bot) stays on the route address ' + SRV));
        c = confirm(items, yes);
        if (c) return c;
        if (dHere) st.direct = to;
        exHere.forEach(function (id) { st.src[id] = to; });
        st.out = cur.filter(function (x) { return x !== ip; });
        return json(200, { ok: true, out: st.out, direct: mipDoc().direct, moved: exHere, text: L('Готово: «для выхода» у ' + ip + ' выключено.', 'Done: “for exits” is off on ' + ip + '.'), warnings: [], ver: ver() });
      });
    }
    if (path === '/api/gw/addresses/exit') {
      if (ip === 'main') delete st.src[body.id];
      else {
        var b = a4(ip);
        if (!b || !b.found) return no(L('Адреса ' + ip + ' на сервере нет.', 'The server has no address ' + ip + '.'));
        if (st.out && st.out.indexOf(ip) < 0) return no(L(ip + ' — не «для выхода».', ip + ' is not “for exits”.'));
        st.src[body.id] = ip;
      }
      return json(200, { ok: true, ids: [body.id], src: ip, text: ip === 'main' ? L('Готово: выходов — 1, подключаются по маршруту системы (основной адрес).', 'Done: exits — 1, connect by the system’s route (the main address).') : L('Выход подключается с ' + ip + '.', 'The exit connects from ' + ip + '.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/spread') {
      // F12б: выходам IPv4 без своего адреса — адреса «для выхода» (найдены, с путём наружу) по кругу
      var outs = (st.out === null ? st.v4.map(function (x) { return x.ip; }) : st.out).filter(function (x) { var a = a4(x); return a && a.found && a.route_ok; });
      if (outs.length < 2) return no(L('Разносить некуда: адресов «для выхода» на сервере сейчас меньше двух (' + outs.join(', ') + '). Добавьте серверу адрес или включите «Для выхода» у другого.',
        'Nowhere to spread: the server has fewer than two “for exits” addresses now (' + outs.join(', ') + '). Add an address to the server or turn on “For exits” on another one.'));
      var loose = EXITS.filter(function (x) { return !st.src[x.id]; }), plan = {};
      if (!loose.length) return json(200, { ok: true, dry: false, moves: [], skipped: [], consequences: [], flushed: [], text: L('Разносить нечего: у каждого выхода уже свой адрес. Разнести заново — сначала верните выходам адрес маршрута: {back}.', 'Nothing to spread: every exit has an address of its own already. To spread them anew, first give the exits the route address back: {back}.'), warnings: [], ver: ver() });
      loose.forEach(function (x, i) { var to = outs[i % outs.length]; (plan[to] = plan[to] || []).push(x); });
      var cons = Object.keys(plan).map(function (to) { return L('выходы ' + plan[to].map(function (x) { return x.name; }).join(', ') + ' будут подключаться с ' + to, 'the exits ' + plan[to].map(function (x) { return x.name; }).join(', ') + ' will connect from ' + to); })
        .concat([L('сайты по-прежнему видят адреса выходов: меняется только адрес, с которого сервер подключается к выходам', 'sites still see the addresses of the exits: only the address the server connects to the exits from changes')]);
      if (body.dry) return json(200, { ok: true, dry: true, moves: [], skipped: [], consequences: cons, flushed: [], text: '', warnings: [], ver: ver() });
      c = confirm(cons, yes);
      if (c) return c;
      var moves = [];
      Object.keys(plan).forEach(function (to) { plan[to].forEach(function (x) { st.src[x.id] = to; moves.push({ id: x.id, src: to, kind: x.kind }); }); });
      return json(200, { ok: true, dry: false, moves: moves, skipped: [], consequences: cons, flushed: [], text: L('Готово: выходов — ' + moves.length + ', разнесены по адресам.', 'Done: ' + moves.length + ' exits spread over the addresses.'), warnings: [], ver: ver() });
    }
    if (path === '/api/gw/addresses/reset') {
      c = confirm(st.give6 ? [L('VLESS Reality перестанет слушать IPv6: xray перезапустится — открытые подключения VLESS и Hysteria2 переподключатся', 'VLESS Reality stops listening on IPv6: xray restarts — open VLESS and Hysteria2 connections reconnect')] : [], yes);
      if (c) return c;
      st.out = null; st.direct = null; st.give6 = null;
      return json(200, { ok: true, text: L('Готово: «для выхода», «напрямую» и строка IPv6 сброшены; приём не менялся.', 'Done: “for exits”, “direct” and the IPv6 line are reset; accepting is as it was.'), warnings: [], ver: ver() });
    }
    return json(404, { ok: false, error: 'not found' });
  }

  // обрыв: fetch отклоняется, как у браузера при разорванном соединении (app.js: ApiError «нет связи», code "net")
  var downUntil = 0, dropGets = 0, afterWarn = false;
  var cut = function () { return Promise.reject(new TypeError('Failed to fetch')); };
  var DROPS = { drop: 1, dropget: 1, down: 1, empty: 1, after: 1 };
  function afterWrite(r, body) {
    if (!DROPS[scene] || body.dry || !r || r.status !== 200) { return r; }
    if (scene === 'dropget') { dropGets = 1; return r; }
    if (scene === 'empty') { return new Response('', { status: 200 }); }
    if (scene === 'after') {
      afterWarn = true; downUntil = Date.now() + 1500;
      return r.json().then(function (d) { d.xray_after = true; return new Response(JSON.stringify(d), { status: 200, headers: { 'Content-Type': 'application/json' } }); });
    }
    downUntil = Date.now() + (scene === 'down' ? 15000 : 1500);
    return cut();
  }

  M.fetch = function (url, init) {
    var u = new URL(String(url), location.href), path = u.pathname.replace(/^.*?\/api\//, '/api/'), method = ((init && init.method) || 'GET').toUpperCase();
    if (scene === 'c10' && method === 'GET' && path === '/api/gw') {
      return base(url, init).then(function (r) { return r.json().then(function (d) { d.enabled = false; return new Response(JSON.stringify(d), { status: r.status, headers: { 'Content-Type': 'application/json' } }); }); });
    }
    if (EX6 && method === 'GET' && path === '/api/gw/exits') {
      return base(url, init).then(function (r) { return r.json().then(function (d) {
        var x = (d.exits || []).filter(function (e) { return e && e.proto === 'vless'; })[0];
        if (x) d.exits.push(Object.assign({}, x, { id: 'e-5e6d7c8b9a', index: (d.exits || []).length + 1, host: '2001:db8:50::7', name: L('Стокгольм', 'Stockholm'),
          where: 'VLESS · 2001:db8:50::7', named: true, devices: 0 }));
        return new Response(JSON.stringify(d), { status: r.status, headers: { 'Content-Type': 'application/json' } }); }); });
    }
    if (path.indexOf('/api/gw/addresses') !== 0) return base(url, init);
    if (method === 'GET' && path === '/api/gw/addresses') {
      if (scene === 'c8') return new Promise(function () { /* загрузка — ответа нет */ });
      if (Date.now() < downUntil) return cut();
      if (dropGets > 0) { dropGets -= 1; return cut(); }
      if (scene === 'c9') return new Promise(function (ok) { setTimeout(function () { ok(json(500, { ok: false, error: L('Сервер не ответил вовремя. Попробуйте позже.', 'The server didn’t respond in time. Try again later.'), code: 1 })); }, 300); });
      var refresh = u.searchParams.get('refresh') === '1';
      return ready().then(function () { return new Promise(function (ok) { setTimeout(function () {
        var d = doc(refresh);
        if (afterWarn) { var wx = L('xray не перезапустился после последнего изменения адресов: Job for kaskad-gw-xray.service failed. Сборщик повторит сам; если xray откажет снова — это будет в ленте событий.',
          'xray didn’t restart after the last change of the addresses: Job for kaskad-gw-xray.service failed. The collector will retry by itself; if xray refuses again, it will be in the event feed.'); d.warnings = [wx, wx]; }
        ok(json(200, d)); }, refresh ? 700 : 200); }); });
    }
    var body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    return ready().then(function () { return write(path, method, body); }).then(function (r) { return afterWrite(r, body); });
  };
})(window);
