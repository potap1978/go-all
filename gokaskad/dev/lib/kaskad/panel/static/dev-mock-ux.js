/* Kaskad 3.3.3 — демо-данные общих компонентов ux.js для ?mock=1 (поверх dev-mock.js): бот, ключ ИИ-агента, журнал и
   перезапуск протокола, причина «не работает». Только для проверки вида без сервера (снимки, tools/k3_layout_check.mjs).
   Сцены — параметрами адреса:
     bot=none|ok|exit       Telegram-бот: не настроен | работает | сервер без выхода (Telegram недоступен напрямую)
     mcp=none|key|ok        ключ ИИ-агента: нет | добавлен, ждём агента | агент подключился
     uxfail=awg3:other,wg:busy,l2tp:closed   причина у установленных протоколов (сервер: fail {why, port, proto, line, human})
     uxcut=all              удаление ключа: журнал не назвал ключ подключения — прерваны все подключения агентов
     ru=1                   сервер в РФ: плашка «Telegram из РФ блокируется» (без него — «Telegram недоступен с сервера»)
   Мастер (setup/index.html?mock=1, этап 5) берёт те же ответы: его адреса api/setup/ux/… — это /api/bot и /api/mcp
   панели (kaskad.setup.web зовёт её же обработчики), удаление ключа — POST …/del вместо DELETE.
   Токен «…:AAbad…» — Telegram не принял; «…:AAnet…» — нет связи и выхода; «…:AAprx…» — выход есть, но не везёт;
   «…:AAtg5…» — Telegram ответил 429; «…:AAe500…» — сбой самой панели (500 без причины); ID 111111 — «бот не может написать» (403). */
(function (g) {
  'use strict';
  var M = g.KASKAD_MOCK || g.KASKAD_SETUP_MOCK;
  if (!M || !M.fetch) { return; }
  var Q = new URLSearchParams(location.search);
  var BOT = Q.get('bot') || 'none', MCP = Q.get('mcp') || 'none';
  var SRV = /^\d{1,3}(\.\d{1,3}){3}$/.test(Q.get('host') || '') ? Q.get('host') : '203.0.113.5';   // host= — как в dev-mock.js
  var FAILS = {};
  (Q.get('uxfail') || '').split(',').forEach(function (x) { var p = x.split(':'); if (p[0] && p[1]) { FAILS[p[0]] = p[1]; } });
  var now = function () { return Math.floor(Date.now() / 1000); };
  var en = (typeof document !== 'undefined' && (document.documentElement.lang || '').indexOf('en') === 0) || /lang=en/.test(location.search);
  var bot = { token: BOT !== 'none', chat: BOT === 'ok', chat_id: BOT === 'ok' ? '123456789' : '', username: BOT !== 'none' ? 'kaskad_k3_bot' : '',
    running: BOT === 'ok', last_error: '', proxy: false, reach: BOT !== 'exit', need_exit: BOT === 'exit', ru: BOT === 'exit' && Q.get('ru') === '1' ? true : null };
  var L = function (ru, enText) { return en ? enText : ru; };       // в EN демо — ни одной русской строки
  // «Как бот ходит в Telegram» (SPEC-tg-proxy Р5): ?px=group|fallback|direct|none|nokeys — что показывает строка
  var PX = Q.get('px') || 'group', mainL = L('Основная', 'Main');
  bot.choice = { group: PX === 'direct' ? 'direct' : 'main', rev: 7, groups: [{ id: 'main', label: mainL, keys: PX === 'nokeys' ? 0 : 2 }, { id: 'g-00a1b2', label: L('Дом', 'Home'), keys: 1 }] };
  function pxPath() {
    var g = bot.choice.group, lb = g === 'main' ? mainL : L('Дом', 'Home');
    if (g === 'direct') { return { state: 'direct', why: 'chosen', choice: g, group: '', label: '', proxy: false, via: 'direct', text: L('напрямую — так выбрано в панели', 'directly — chosen in the panel') }; }
    if (PX === 'nokeys') { return { state: 'direct', why: 'no_keys', choice: g, group: g, label: lb, proxy: false, via: 'direct', text: L('напрямую: в группе «' + lb + '» нет включённых ключей', 'directly: the group «' + lb + '» has no enabled keys') }; }
    if (PX === 'fallback') { return { state: 'fallback', why: '', choice: g, group: g, label: lb, proxy: true, via: 'direct', text: L('напрямую: выходы группы «' + lb + '» не отвечают, бот проверяет их раз в 5 минут', 'directly: the exits of the group «' + lb + '» do not answer, the bot checks them every 5 minutes') }; }
    if (PX === 'none') { return { state: 'none', why: '', choice: g, group: g, label: lb, proxy: true, via: 'group', text: L('нет связи с Telegram (через выходы группы «' + lb + '» и напрямую)', 'no connection to Telegram (through the exits of the group «' + lb + '» and directly)') }; }
    return { state: 'group', why: '', choice: g, group: g, label: lb, proxy: true, via: 'group', text: L('через выходы группы «' + lb + '»', 'through the exits of the group «' + lb + '»') };
  }
  bot.path = pxPath();
  // общие службы, как kaskad.gw.inlog.shared: ядро шлюза (VLESS, SOCKS5), accel-ppp (L2TP, PPTP, SSTP), strongSwan (L2TP, IKEv2)
  var UNITS = { vless: ['xray'], socks5: ['xray'], hy2: ['xray'], l2tp: ['accel', 'ipsec'], pptp: ['accel'], sstp: ['accel'], ikev2: ['ipsec'] };
  function sharedOf(kind, rows) {
    var mine = UNITS[kind] || [kind];
    var withT = rows.filter(function (x) { return x.kind !== kind && x.installed && x.enabled && (UNITS[x.kind] || [x.kind]).some(function (u) { return mine.indexOf(u) >= 0; }); })
      .map(function (x) { return x.title; });
    return { gateway: mine.indexOf('xray') >= 0, with: withT };
  }
  var FP = 'SHA256:q3v9Yx0bZp2kLm7Nf4Rt8Hc1Wd6Ej5Ga0Ks2Uo9Ty3Iu';
  var keys = MCP === 'none' ? [] : [{ fingerprint: FP, type: 'ssh-ed25519', label: 'MacBook-Anna', seen: MCP === 'ok' ? { at: now() - 300, client: 'claude-code/2.1.4' } : null }];
  var connect = function () {
    var host = SRV, args = ['-i', '~/.ssh/kaskad_agent', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', 'root@' + host, 'kaskad-mcp'];
    return { host: host, keyfile: '~/.ssh/kaskad_agent', claude_code: 'claude mcp add kaskad -- ssh ' + args.join(' '),
      claude_desktop: { mcpServers: { kaskad: { command: 'ssh', args: args } } } };
  };
  // журнал упавшей службы — по её протоколу и движку на карточке (R-333 П3″): у AmneziaWG на программе (amneziawg-go —
  // так демо подписывает движок) — ошибка настроек, на модуле ядра — модуль не загрузился; у прочих — строки systemd
  function logOf(kind, ib) {
    var u = 'kaskad-gw-' + kind, start = ['systemd[1]: Starting ' + u + '.service…', 0];   // без названия из демо: в EN — ни одной русской строки
    var end = [['systemd[1]: ' + u + '.service: Main process exited, code=exited, status=1/FAILURE', 1],
      ['systemd[1]: ' + u + '.service: Failed with result \'exit-code\'.', 1]];
    if (FAILS[kind] === 'busy') { return [start, [(/^awg/.test(kind) ? 'awg-quick' : 'wg-quick') + ': RTNETLINK answers: Address already in use', 1]].concat(end.slice(0, 1)); }
    if (!/^awg/.test(kind)) { return [start].concat(end); }
    if (ib.core === 'kernel') {
      return [start, ['modprobe: FATAL: Module amneziawg not found in directory /lib/modules/6.8.0-51-generic', 1], ['awg-quick: Unknown device type', 1]].concat(end);
    }
    return [start, ['awg-quick: Line unrecognized: `Jmin=abc\'', 1], ['awg-quick: Configuration parsing error', 1]].concat(end);
  }
  function failOf(kind, ports, core) {
    var why = FAILS[kind];
    if (!why) { return null; }
    var m = /^(\d+)\/(udp|tcp)/.exec((ports || [])[0] || '');
    if (why === 'busy') { return { why: 'busy', port: m ? Number(m[1]) : null, proto: m ? m[2] : null, line: 'RTNETLINK answers: Address already in use', human: '' }; }
    if (why === 'closed') { return { why: 'closed', port: m ? Number(m[1]) : null, proto: m ? m[2] : null, line: '', human: '' }; }
    // «модуль ядра AmneziaWG» — только у AmneziaWG; у прочих — строка systemd, без придуманной причины (R-333 П7′)
    if (!/^awg/.test(kind)) { return { why: 'other', port: null, proto: null, line: 'kaskad-gw-' + kind + '.service: Main process exited, code=exited, status=1/FAILURE', human: '' }; }
    // причина — по движку, который видно на карточке: модуль ядра (core: kernel) или программа amneziawg-go (R-333 П3″);
    // слова — как у сервера (gw_inlog.human.kmod_awg / .config)
    if (core === 'kernel') {
      return { why: 'other', port: null, proto: null, line: 'modprobe: FATAL: Module amneziawg not found in directory /lib/modules/6.8.0-51-generic',
        human: L('Модуль ядра AmneziaWG не загрузился — после обновления ядра его нужно собрать заново.', 'The AmneziaWG kernel module did not load — after a kernel update it has to be rebuilt.') };
    }
    return { why: 'other', port: null, proto: null, line: 'awg-quick: Line unrecognized: `Jmin=abc\'',
      human: L('Ошибка в настройках протокола — переустановите его.', 'The protocol’s settings have an error — reinstall it.') };
  }
  function resp(status, doc) { return Promise.resolve(new Response(JSON.stringify(doc), { status: status, headers: { 'Content-Type': 'application/json' } })); }
  function err(status, text, reason) { return resp(status, { ok: false, error: text, code: null, data: { reason: reason } }); }
  function later(ms, fn) { return new Promise(function (r) { setTimeout(function () { r(fn()); }, ms); }); }
  // сборщик демо (живые данные) тоже должен видеть упавшие службы
  try {
    var db = M.db && M.db();
    Object.keys(FAILS).forEach(function (k) {
      if (!db || !db.inbounds || !db.inbounds[k]) { return; }
      // служба стоит или порт закрыт — сессий нет (сборщик демо иначе насчитал бы их по устройствам в сети)
      Object.defineProperty(db.inbounds[k], 'sessions', { get: function () { return 0; }, set: function () {}, configurable: true });
      if (FAILS[k] !== 'closed') {
        db.inbounds[k].running = false;
        db.inbounds[k].why = L('служба kaskad-gw-' + k + '.service завершилась с ошибкой (код 1)', 'unit kaskad-gw-' + k + '.service failed (code 1)');
      }
    });
  } catch (e) { /* старый dev-mock.js */ }
  var base = M.fetch;
  M.fetch = function (url, init) {
    init = init || {};
    var method = (init.method || 'GET').toUpperCase();
    var u = new URL(url, location.href), path = u.pathname.replace(/^.*?(\/api\/)/, '/api/').replace(/\/+$/, '');
    if (path.indexOf('/api/setup/ux/') === 0) {                   // мастер: те же обработчики панели
      path = '/api/' + path.slice('/api/setup/ux/'.length);
      var dm = /^(\/api\/mcp\/keys\/.+)\/del$/.exec(path);
      if (dm && method === 'POST') { path = dm[1]; method = 'DELETE'; }
    }
    var body = {};
    try { body = init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    var m;
    if (path === '/api/bot' && method === 'GET') { return resp(200, Object.assign({ ok: true }, bot)); }
    // «Нет сервера за рубежом?» — настоящие адреса только в product.HOSTING; в макете заглушки
    if (path === '/api/hosting' && method === 'GET') {
      return resp(200, { ok: true, guide: null, hosting: [{ id: 'adminvps', name: 'AdminVPS', url: 'https://example.com/adminvps', region: L('РФ и Европа', 'Russia and Europe') },
        { id: 'cloudvps', name: 'Cloud VPS', url: 'https://example.com/cloudvps', region: L('Беларусь', 'Belarus') }] });
    }
    if (path === '/api/bot/check' && method === 'POST') {
      var tok = String(body.token || '');
      return later(700, function () {
        if (/net/i.test(tok)) { return err(502, L('Не достучались до Telegram — сначала добавьте выход.', 'Couldn’t reach Telegram — add an exit first.'), 'net'); }
        if (/prx/i.test(tok)) { return err(502, L('Нет связи с Telegram через прокси бота: timed out', 'No link to Telegram through the bot’s proxy: timed out'), 'net_proxy'); }
        if (/e500/i.test(tok)) { return resp(500, { ok: false, error: L('Внутренняя ошибка панели', 'Internal panel error'), code: null }); }
        if (/tg5/i.test(tok)) { return err(502, L('Telegram ответил ошибкой: Too Many Requests: retry after 30', 'Telegram returned an error: Too Many Requests: retry after 30'), 'telegram'); }
        if (/bad/i.test(tok) || !/^\d{6,}:[\w-]{30,}$/.test(tok)) { return err(400, L('Telegram не принял токен — проверьте его у @BotFather.', 'Telegram rejected the token — check it with @BotFather.'), 'bad_token'); }
        return resp(200, { ok: true, username: 'kaskad_k3_bot' });
      });
    }
    if (path === '/api/bot/proxy' && method === 'POST') {
      return later(500, function () {
        var g = String(body.group || '');
        if (g !== 'direct' && !bot.choice.groups.some(function (x) { return x.id === g; })) { return err(400, L('Выберите группу выходов или «Напрямую».', 'Choose a group of exits or «Directly».'), 'field'); }
        bot.choice.group = g; bot.choice.rev += 1; PX = 'group'; bot.path = pxPath();
        return resp(200, { ok: true, rev: bot.choice.rev, group: g, moves: [], warnings: [], path: bot.path });
      });
    }
    if (path === '/api/bot/test' && method === 'POST') {
      return later(900, function () {
        if (!/^\d{5,15}$/.test(String(body.chat_id || ''))) { return err(400, L('Chat ID — число (у группы с минусом впереди).', 'The chat ID is a number (a group’s starts with a minus).'), 'bad_chat'); }
        bot.token = true; bot.chat = true; bot.chat_id = String(body.chat_id); bot.username = 'kaskad_k3_bot'; bot.running = true;
        var no = body.chat_id === '111111';
        return resp(200, { ok: true, username: 'kaskad_k3_bot', chat_id: bot.chat_id, started: true, sent: !no, sent_why: no ? 'nostart' : null });
      });
    }
    if (path === '/api/mcp' && method === 'GET') { return resp(200, { ok: true, keys: keys, connect: connect(), host: SRV }); }
    if (path === '/api/mcp/keys' && method === 'POST') {
      var pub = String(body.pubkey || '');
      if (/PRIVATE KEY/.test(pub)) { return err(400, L('Это закрытый ключ.', 'This is a private key.'), 'private_key'); }
      keys.push({ fingerprint: FP.slice(0, -4) + ('000' + keys.length).slice(-4), type: pub.split(' ')[0], label: String(body.label || '').replace(/[^A-Za-z0-9._@-]/g, '-').slice(0, 32), seen: null });
      // демо: агент «подключается» через 8 с
      setTimeout(function () { keys.forEach(function (k) { if (!k.seen) { k.seen = { at: now(), client: 'claude-code/2.1.4' }; } }); }, 8000);
      return resp(200, { ok: true, fingerprint: keys[keys.length - 1].fingerprint, connect: connect() });
    }
    if ((m = /^\/api\/mcp\/keys\/(.+)$/.exec(path)) && method === 'DELETE') {
      var fp = decodeURIComponent(m[1]);
      var had = keys.filter(function (k) { return k.fingerprint === fp && k.seen; }).length;
      keys = keys.filter(function (k) { return k.fingerprint !== fp; });
      // демо: у подключённого ключа подключение обрывается; ?uxcut=all — журнал не назвал ключ
      var all = Q.get('uxcut') === 'all';
      return resp(200, { ok: true, removed: 1, ended: had || all ? 1 : 0, unsure: all });
    }
    if ((m = /^\/api\/gw\/inbounds\/([a-z0-9]+)\/log$/.exec(path)) && method === 'GET') {
      var ib = (M.db && M.db() && M.db().inbounds && M.db().inbounds[m[1]]) || {};
      var lines = logOf(m[1], ib).map(function (x, i) { return { t: '2026-10-12T14:3' + i + ':0' + i + '+03:00 node ' + x[0], err: !!x[1] }; });
      return later(400, function () { return resp(200, { ok: true, kind: m[1], lines: lines, units: ['kaskad-gw-' + m[1] + '.service'], cmd: 'journalctl -u kaskad-gw-' + m[1] + '.service -n 30', fail: null }); });
    }
    if ((m = /^\/api\/gw\/inbounds\/([a-z0-9]+)\/restart$/.exec(path)) && method === 'POST') {
      return later(1200, function () { return resp(200, { ok: true, kind: m[1], running: !FAILS[m[1]], at: now(), fail: null }); });
    }
    var p = base(url, init);
    if (path === '/api/gw/inbounds' && method === 'GET') {
      return p.then(function (r) {
        return r.text().then(function (txt) {
          var d = JSON.parse(txt);
          (d.inbounds || []).forEach(function (x) {
            x.shared = x.installed ? sharedOf(x.kind, d.inbounds || []) : null;
            if (!x.installed || !FAILS[x.kind]) { return; }
            x.fail = failOf(x.kind, x.ports, x.core);
            x.sessions = 0;
            if (x.fail.why !== 'closed') { x.running = false; }
          });
          return new Response(JSON.stringify(d), { status: r.status, headers: { 'Content-Type': 'application/json' } });
        });
      });
    }
    return p;
  };
})(window);
