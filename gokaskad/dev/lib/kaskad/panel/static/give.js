/* Kaskad 3 — «Выдать настройки»: один компонент на окно панели (app.js), страницу человека /p/ (user.js) и шаг 6
   мастера «Быстрый старт» (setup.js). Дизайн — k3/design/issue-qr/export-2026-10-11 (HANDOFF-give-settings.md:
   правила, состояния, размеры, тексты), решения владельца — handoff/OWNER-FEEDBACK-3.3.md №25–32, логика подсказок —
   handoff/SPEC-issue-fallbacks.md. Данные — ответ выдачи сервера (ops.issue: items, install, support, alternatives;
   у /p/ — то же из userpage.build_keys), здесь только вид и поведение.

   Правила (HANDOFF §1): на экране всегда ОДИН QR — ключа; QR магазина — в карточке «QR · установить», пока она
   открыта, QR ключа размыт и закрыт плашкой «Сначала установите приложение». Части ключа (AmneziaWG 3.1) сменяются
   сами раз в 2 с (qr-slides.js). Треугольник — пометка: наведение открывает, нажатие закрепляет, Esc и клик мимо
   снимают; на телефоне — только нажатие, во всю ширину. Телефон (< 768 px): QR свёрнут строкой, наверху «Открыть в
   App Store» и «Скопировать ключ», кнопок «QR · установить» нет.

   ES5, без библиотек и без HTML из текста: всё с сервера — через textContent и проверенные атрибуты (ссылки — только
   https://). Тексты — window.KASKAD_GIVE_I18N (give-i18n-ru.js) и KASKAD_GIVE_I18N_EN (give-i18n-en.js).

   API:
     KaskadGive.model(r, o) -> модель окна (чистая функция: тесты гоняют её в node на настоящем каталоге)
         r — ответ выдачи {kind, os, items, install, support, alternatives, available, howto, name};
         o — {viewer: "admin"|"person"|"wizard", lang, device: {name}, app, phone, actions: {grant, enable}}
     KaskadGive.mount(o) -> {el, set(r), update(o2), destroy()}
         o — то же плюс: only: 'apps' — только список «Установите приложение» (вкладка «Подписка» окна панели: r —
         {kind: 'sub', os, install: блок install_apps.sub_block}, строка приложения — «ссылка подписки»; шапку шага,
         ссылку и шаги рисует хозяин, выбранное приложение — onRender(vm).app), selectors (ряды «Платформа / Протокол»,
         только панель), platforms, chips (протоколы
         устройства на /p/), onPlatform(p), onProto(k), onGrant(k), onEnable(k), drawQr(item, i) -> Node,
         drawLinkQr(link) -> Node, save(name, text, mime), iconBase — адрес папки со статикой, к нему приписывается
         apps/<id>.png (один путь на место: панель «static/», мастер «../static/», /p/ — «<префикс>/p/»; маршруты —
         server.py /static/…, userpage.py /p/apps/…); не загрузилась — плитка с буквой
     KaskadGive.platforms, routers, isRouter(p), t(lang, key, vars) */
(function (g) {
  'use strict';

  var DEVICES = ['ios', 'android', 'androidtv', 'windows', 'macos', 'linux'];
  var ROUTERS = ['keenetic', 'mikrotik', 'openwrt', 'tplink', 'huawei', 'xiaomi', 'asus'];
  var PLATFORMS = DEVICES.concat(ROUTERS, ['other']);
  var KINDS = ['awg3', 'awg2', 'awg1', 'wg', 'vless', 'hy2', 'openvpn', 'ocserv', 'ikev2', 'l2tp', 'sstp', 'pptp', 'socks5'];
  var TITLES = { awg3: 'AmneziaWG 3.1', awg2: 'AmneziaWG 2.0', awg1: 'AmneziaWG 1.5', wg: 'WireGuard', vless: 'VLESS', hy2: 'Hysteria2',
    openvpn: 'OpenVPN', ocserv: 'OpenConnect', ikev2: 'IKEv2', l2tp: 'L2TP/IPsec', sstp: 'SSTP', pptp: 'PPTP', socks5: 'SOCKS5' };
  var BRAND = 'goKaskad PRO';                                  // issue_os.app_label: имя ключа в приложении
  var AMNEZIA_LINK = { amneziavpn: 1, defaultvpn: 1, 'amneziavpn-site': 1 };   // эти берут ссылку vpn://
  var FILE_FMT = { conf: 1, ovpn: 1, mobileconfig: 1, ps1: 1 };
  var MIME = { mobileconfig: 'application/x-apple-aspen-config', ps1: 'text/plain;charset=utf-8', ovpn: 'application/x-openvpn-profile' };
  var HTTPS = /^https:\/\/[^\s"'<>]+$/;
  var ICON_SRC = /^(?:\/p\/)?(apps\/[a-z0-9-]{1,40}\.png)$/;   // из каталога: apps/<id>.png (у /p/ — /p/apps/<id>.png)
  // файл для скачивания на /p/ (userpage.py: /p/<токен>/file/<выдача>/<имя>, под префиксом сайта); иначе — blob из body
  var FILE_URL = /^(?:\/[A-Za-z0-9._~-]+)*\/p\/[A-Za-z0-9_-]{16,64}\/file\/[A-Za-z0-9_-]{8,64}\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  var OUR_GUIDE = /^https:\/\/(?:www\.)?anten-ka\.com(?:[\/?#]|$)/;   // «наша инструкция» — только наш сайт
  var PHONE_MQ = '(max-width: 767px)';
  // имя устройства в имени ключа — без платформы, которую добавляет мастер («Мой телефон · Android TV»): то же, что
  // issue_os.device_title (названия платформ мастера на обоих языках — tests/test_333q.py сверяет с setup.product)
  var PLATFORM_TAIL = / · (?:iPhone и iPad|iPhone \/ iPad|Android TV|Android|Windows|macOS|Linux|Keenetic|MikroTik|OpenWrt|TP-Link|Huawei|Xiaomi|ASUS|Другое|Other)$/;
  function deviceTitle(name) {
    var s = String(name == null ? '' : name).replace(/\s+/g, ' ').trim(), cut = s.replace(PLATFORM_TAIL, '');
    return cut.trim() ? cut.trim() : s;
  }
  // QR адреса страницы в «QR слишком большой» (give.css .gs-dense__qr): поле в модулях, белый отступ рамки, наименьшая
  // сторона без отступа — рамка растёт под порог камеры при длинном адресе (3.3.3 №38)
  var PAGE_QUIET = 2, PAGE_PAD = 6, PAGE_MIN = 164;
  var TIP_DELAY = 150;                                         // мс: подсказка не гаснет, пока мышь идёт к ссылке

  // ── тексты ───────────────────────────────────────────────────────────────
  function dict(lang) {
    var ru = g.KASKAD_GIVE_I18N || null, en = g.KASKAD_GIVE_I18N_EN || null;
    if (lang === 'en' && en) { return en; }
    return ru || en || {};
  }
  function get(d, key) {
    var parts = String(key).split('.'), v = d, i;
    for (i = 0; i < parts.length; i++) {
      if (!v || typeof v !== 'object' || !Object.prototype.hasOwnProperty.call(v, parts[i])) { return undefined; }
      v = v[parts[i]];
    }
    return v;
  }
  function fill(s, vars) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) {
      return vars && Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }
  function t(lang, key, vars) {
    var v = get(dict(lang), key);
    return typeof v === 'string' ? fill(v, vars) : '';
  }
  function has(lang, key) { return typeof get(dict(lang), key) === 'string'; }

  // ── мелочи данных ────────────────────────────────────────────────────────
  function arr(x) { return Array.isArray(x) ? x : []; }
  function str(x) { return typeof x === 'string' ? x : x === null || x === undefined ? '' : String(x); }
  function isObj(x) { return !!x && typeof x === 'object' && !Array.isArray(x); }
  function isRouter(p) { return ROUTERS.indexOf(p) >= 0; }
  function title(k) { return TITLES[k] || str(k); }
  function okUrl(u) { return HTTPS.test(str(u)); }
  function bodies(it) { return Array.isArray(it.body) ? it.body.map(str) : [str(it.body)]; }
  function family(kind) {
    if (kind.indexOf('awg') === 0) { return 'awg'; }
    if (kind === 'l2tp' || kind === 'pptp' || kind === 'sstp') { return 'ppp'; }
    return kind;
  }
  function K() { return g.KaskadQRSlides || null; }
  // для какого приложения запись выдачи (qr-slides.js appOf: по тексту, у /p/ — по qr_for сервера)
  function appOf(it) {
    var k = K();
    if (k && k.appOf) { return k.appOf(it) || ''; }
    return '';
  }
  // записи выдачи по приложениям: {amnezia: {qr, items}, awg: …} в порядке выдачи; без приложения — rest
  function bundles(items) {
    var by = {}, order = [], rest = [];
    items.forEach(function (it) {
      var a = appOf(it);
      if (!a) { rest.push(it); return; }
      if (!by[a]) { by[a] = { app: a, qr: null, items: [] }; order.push(a); }
      if (it.format === 'qr') { if (!by[a].qr) { by[a].qr = it; } } else { by[a].items.push(it); }
    });
    return { by: by, order: order, rest: rest };
  }
  // какой вид ключа берёт приложение каталога: AmneziaVPN и DefaultVPN — ссылку vpn://, остальные — файл .conf
  // (AmneziaWG, WireGuard, incy…), клиенты VLESS — ссылку vless://
  function wantOf(appId, kind) {
    if (kind === 'sub') { return 'sub'; }                     // подписка: любое приложение списка берёт ссылку подписки
    if (kind === 'vless') { return 'vless'; }
    if (kind === 'hy2') { return 'hy2'; }
    if (kind === 'wg') { return 'wg'; }
    if (kind.indexOf('awg') === 0) { return AMNEZIA_LINK[appId] ? 'amnezia' : 'awg'; }
    return '';
  }
  // вид ключа для строки приложения и шага 2: связка приложения (amnezia, vless, awg, wg) или — у протоколов без
  // связки (OpenVPN, OpenConnect, IKEv2, L2TP…) — то, что сервер выдал: файл (.ovpn, .mobileconfig…) или поля для
  // ввода (FIX-332g №4: раньше всем им писалось «файл .conf»)
  function fmtOf(bundleApp, items) {
    if (bundleApp === 'sub') { return { word: 'fmtSub', ext: '' }; }
    if (bundleApp === 'amnezia') { return { word: 'fmtLink', ext: 'vpn://' }; }
    if (bundleApp === 'vless') { return { word: 'fmtLink', ext: 'vless://' }; }
    if (bundleApp === 'hy2') { return { word: 'fmtLink', ext: 'hysteria2://' }; }
    if (bundleApp === 'url') { return { word: 'fmtLink', ext: 'https://' }; }
    if (bundleApp) { return { word: 'fmtFile', ext: '.conf' }; }
    var f = fileItem(items || []), n = f ? str(f.filename) : '', dot = n.lastIndexOf('.');
    if (f) { return { word: 'fmtFile', ext: dot > 0 ? n.slice(dot) : n }; }
    if (arr(items).some(function (it) { return it.format === 'fields'; })) { return { word: 'fmtFields', ext: '' }; }
    return { word: 'fmtFile', ext: '.conf' };
  }
  function fileItem(items) {
    var i, it;
    for (i = 0; i < items.length; i++) {
      it = items[i];
      if (FILE_FMT[it.format] && it.filename && !/\.rsc$/i.test(it.filename) && (FILE_URL.test(str(it.file)) || str(it.body))) { return it; }
    }
    return null;
  }
  function fileOf(it) {
    if (!it) { return null; }
    var name = str(it.filename), dot = name.lastIndexOf('.');
    return { name: name, ext: dot > 0 ? name.slice(dot) : '', body: str(it.body), url: FILE_URL.test(str(it.file)) ? str(it.file) : '', mime: MIME[it.format] || '' };
  }
  function noteOf(s) {
    // пометка пары «платформа × протокол» (support): условие, пометка, «не проверено» и ссылки (инструкция первой)
    var lines = [], links = [];
    if (!isObj(s)) { return null; }
    if (s.min) { lines.push({ min: str(s.min) }); }
    [s.note, s.unverified].forEach(function (x) { if (str(x)) { lines.push({ text: str(x) }); } });
    if (okUrl(s.guide)) { links.push({ guide: true, url: s.guide }); }
    arr(s.links).forEach(function (l) {
      if (isObj(l) && okUrl(l.url) && l.url !== s.guide) { links.push({ label: str(l.label), url: l.url }); }
    });
    return lines.length ? { lines: lines, links: links } : null;
  }

  // ── модель окна ──────────────────────────────────────────────────────────
  function model(r, o) {
    o = o || {};
    r = isObj(r) ? r : {};
    var lang = o.lang === 'en' ? 'en' : 'ru', viewer = o.viewer || 'admin';
    var kind = str(r.kind), os = PLATFORMS.indexOf(r.os) >= 0 ? r.os : 'other';
    var s = isObj(r.support) ? r.support : {};
    var level = s.level || 'native';
    var items = arr(r.items).filter(isObj);
    var osTitle = t(lang, 'plat.' + os) || os;
    var vm = { lang: lang, viewer: viewer, kind: kind, os: os, router: isRouter(os), osTitle: osTitle, protoTitle: title(kind),
      level: level, state: level === 'none' ? 'none' : level === 'workaround' ? 'work' : 'ok', note: noteOf(s), guide: okUrl(s.guide) ? s.guide : '' };

    // протоколы в шапке: выданные устройству и включённые на сервере (available) + открытый сейчас; приглушён — «none»
    // платформы, треугольник — обходной путь (SPEC п. 4)
    var levels = isObj(s.levels) ? s.levels : {};
    var avail = arr(o.chips || r.available).map(str);
    if (kind && avail.indexOf(kind) < 0) { avail.push(kind); }
    vm.chips = KINDS.filter(function (k) { return avail.indexOf(k) >= 0; }).concat(avail.filter(function (k) { return KINDS.indexOf(k) < 0; }))
      .map(function (k) {
        var lv = levels[k] || '';
        return { kind: k, title: title(k), on: k === kind, dim: lv === 'none', work: lv === 'workaround',
          tip: lv === 'none' ? t(lang, 'chipNone', { os: osTitle, proto: title(k) }) : lv === 'workaround' ? t(lang, 'chipWork') : '' };
      });

    // Б и В: платформа протокол не умеет — другие протоколы платформы с их состоянием (SPEC п. 3)
    var alts = arr(r.alternatives).length ? arr(r.alternatives) : arr(s.alternatives);
    vm.alts = alts.filter(function (a) { return isObj(a) && a.proto; }).map(function (a) {
      var st = a.state === 'ready' ? 'ready' : a.state === 'device_off' ? 'device_off' : 'server_off', act = 'ask';
      var person = viewer === 'person';
      if (st === 'ready') { act = 'pick'; } else if (!person && st === 'device_off' && o.actions && o.actions.grant) { act = 'grant'; } else if (!person && st === 'server_off' && o.actions && o.actions.enable) { act = 'enable'; }
      // без кнопки: человеку на /p/ — «Попросите администратора добавить/включить…» по состоянию (на сервере есть, но не
      // у устройства — добавить; на сервере нет — включить, T-67-332 №3); администратору (панель, мастер) — куда идти самому
      var at = str(a.title) || title(a.proto);
      return { proto: str(a.proto), title: at, state: st, act: act, note: str(a.note),
        status: t(lang, person ? (st === 'ready' ? 'pReady' : st === 'device_off' ? 'pDevice' : 'pServer') : (st === 'ready' ? 'altReady' : st === 'device_off' ? 'altDevice' : 'altServer')),
        ask: t(lang, person ? (st === 'device_off' ? 'askAdd' : 'ask') : st === 'device_off' ? 'askDev' : 'askSrv', { proto: at }) };
    });
    if (vm.state === 'none') {
      vm.noneText = vm.alts.length ? t(lang, vm.router ? 'noneRouter' : 'noneDevice', { os: osTitle, proto: vm.protoTitle }) : t(lang, 'nothing', { os: osTitle });
      if (!vm.alts.length) { vm.state = 'nothing'; }
      return vm;
    }
    if (vm.state === 'work') { vm.workText = t(lang, 'workT', { os: osTitle, proto: vm.protoTitle }); }

    var b = bundles(items);
    var devName = deviceTitle(o.device ? o.device.name : '');
    var keyName = BRAND + ' · ' + vm.protoTitle + (devName ? ' · ' + devName : '');
    vm.keyName = keyName;

    if (vm.router) {                                           // роутер: подготовьте → добавьте → проверьте
      vm.rt = routerModel(lang, os, kind, level, items, b);
      return vm;
    }

    // шаг 1: приложения этой пары из каталога сервера; пометки — серой строкой под именем (appRow noteLine)
    var inst = isObj(r.install) ? r.install : {};
    vm.builtin = str(inst.builtin);
    vm.apps = arr(inst.apps).filter(function (a) { return isObj(a) && a.id; }).map(function (a, i, all) {
      var links = arr(a.links).filter(function (l) { return isObj(l) && okUrl(l.url); });
      var want = wantOf(str(a.id), kind), bun = b.by[want] ? want : (b.order[0] || '');
      var f = fmtOf(bun || want, items);
      // «есть подделки» — одной строкой над списком (fakesLine), не у каждой строки: и у подписки, и у ключа, /p/,
      // мастера (круг 1, правило владельца «без дублей предупреждений»)
      var fakes = str(a.fakes), lines = [a.note, a.unverified].map(str).filter(Boolean);
      links.slice(1).forEach(function (l) { if (str(l.note)) { lines.push(str(l.label) + ': ' + str(l.note)); } });
      var icon = ICON_SRC.exec(str(a.icon));
      return { id: str(a.id), name: str(a.name) || str(a.id), icon: icon ? icon[1] : '', fakes: !!fakes,
        recommended: !!a.recommended && all.length > 1, bundle: bun, fmtWord: t(lang, f.word), ext: f.ext,
        store: links[0] || null, more: links.slice(1), tip: lines.length || links.length > 1 ? { lines: lines, links: links.slice(1).map(function (l) { return { label: str(l.label), url: l.url }; }) } : null };
    });
    var app = null, i;
    for (i = 0; i < vm.apps.length; i++) { if (vm.apps[i].id === o.app) { app = vm.apps[i]; } }
    app = app || vm.apps[0] || null;
    vm.app = app;
    // подписка (kind 'sub'): только список приложений — ключа, файла и пути в приложении у неё здесь нет
    if (kind === 'sub') { vm.keyName = ''; vm.key = null; vm.others = []; vm.path = null; vm.pathText = ''; return vm; }
    var want0 = app ? app.bundle : wantOf('', kind);
    var bun = b.by[want0] || b.by[b.order[0]] || null;
    if (bun) {
      var qr = bun.qr, link = null, conf = null;
      bun.items.forEach(function (it) {
        if (!link && (it.format === 'vpn' || it.format === 'link')) { link = it; }
        if (!conf && FILE_FMT[it.format]) { conf = it; }
      });
      var copy = link ? bodies(link)[0] : conf ? str(conf.body) : qr && Array.isArray(qr.body) && qr.body.length === 1 ? str(qr.body[0]) : '';
      var parts = qr ? (Array.isArray(qr.qr) ? qr.qr.length : bodies(qr).filter(Boolean).length) : 0;
      var fileIt = (bun.app === 'vless' || bun.app === 'hy2') ? null : (conf && conf.filename ? conf : fileItem(items));
      var f2 = fmtOf(bun.app, items);
      vm.key = { bundle: bun.app, qr: qr && parts ? qr : null, parts: parts, copy: copy, text: copy,
        file: fileOf(fileIt), fmtWord: t(lang, f2.word), ext: f2.ext };
      // 3.3.3 №38: возьмёт ли камера телефона код с экрана (KaskadQRSlides.fit — версия против порога рамки, какая она
      // сейчас: o.fit = {frame, minPx}; без него — компьютер). Слишком плотный код не показывается основным способом:
      // вместо него — «QR слишком большой», файл и страница на телефоне
      var fit = vm.key.qr && K() && K().fit ? K().fit(vm.key.qr, o.fit || null) : null;
      vm.key.fit = fit;
      vm.key.dense = !!(fit && !fit.ok);
      // «Появится как …»: ссылку vpn:// / vless:// приложение называет по описанию в ней (goKaskad PRO · …), файл .conf —
      // по имени файла без расширения; у прочего (https://, нет файла) имя не знаем — подсказки нет (T-67-332 №2)
      var keyFile = vm.key.file && vm.key.file.name ? vm.key.file.name.replace(/\.conf$/i, '') : '';
      vm.keyName = (bun.app === 'amnezia' || bun.app === 'vless' || bun.app === 'hy2') ? keyName : (bun.app !== 'url' && keyFile && keyFile !== vm.key.file.name ? keyFile : '');
      vm.others = [];
    } else {
      vm.key = null;
      vm.others = b.rest.concat(b.order.map(function (k) { return b.by[k].items; }).reduce(function (x, y) { return x.concat(y); }, []));
    }
    // шаг 3: путь в приложении плашками (каталог give-i18n path.*); у файла — свой путь, если есть (и когда код слишком
    // плотный и не показан «всё равно» (o.shown) — ключ идёт файлом, 3.3.3 №38); нет пути — шаг сервера
    var how = arr(r.howto).map(str).filter(Boolean);
    var pathKey = app ? ((vm.key && vm.key.bundle !== 'amnezia' && vm.key.bundle !== 'vless' && vm.key.bundle !== 'hy2' && (!vm.key.qr || (vm.key.dense && !o.shown)) && has(lang, 'pathFile.' + app.id)) ? 'pathFile.' + app.id : 'path.' + app.id) : '';
    vm.path = pathKey && has(lang, pathKey) ? t(lang, pathKey).split(' › ') : null;
    vm.pathText = vm.path ? '' : (app && vm.apps[0] && app.id !== vm.apps[0].id ? t(lang, 's3plain') : (how[how.length - 1] || t(lang, 's3plain')));
    return vm;
  }

  function routerModel(lang, os, kind, level, items, b) {
    var fam = family(kind), work = level === 'workaround';
    var pick = function (base, alts) {
      var i, k;
      for (i = 0; i < alts.length; i++) {
        k = 'rt.' + os + '.' + base + alts[i];
        if (has(lang, k)) { return t(lang, k); }
      }
      return '';
    };
    var how = '', item = null, i, it;
    for (i = 0; i < items.length && !how; i++) {
      it = items[i];
      if (it.format === 'link' || (it.format === 'vpn' && kind === 'vless')) { how = 'link'; item = it; }
    }
    for (i = 0; i < items.length && !how; i++) {
      it = items[i];
      if (/\.rsc$/i.test(str(it.filename))) { how = 'cmd'; item = it; }
    }
    for (i = 0; i < items.length && !how; i++) { if (items[i].format === 'fields') { how = 'fields'; item = items[i]; } }
    if (!how) { item = fileItem(items); if (item) { how = 'file'; } }
    for (i = 0; i < items.length && !how; i++) { if (items[i].format === 'guide') { how = 'guide'; item = items[i]; } }
    var sfx = work ? ['_work', '_' + fam, ''] : ['_' + fam, ''];
    var prep = pick('prep', sfx) || t(lang, 'rt.any.prep');
    var add = (work ? pick('add', ['_work_' + how, '_work']) : '') || pick('add', ['_' + how]) || t(lang, 'rt.any.add_' + (how || 'file'));
    var check = (work ? pick('check', ['_work']) : '') || pick('check', ['']);
    var tail = (work ? pick('checkTail', ['_work']) : '') || pick('checkTail', ['']) || t(lang, 'rt.any.checkTail');
    var notes = item && how !== 'link' ? arr(item.notes).map(str).filter(Boolean) : [];   // у ссылки — подсказки приложений, роутеру не к месту
    return { how: how, item: item, prep: prep, add: add, notes: notes, check: check ? check.split(' › ') : [], checkTail: tail,
      term: how === 'cmd' ? t(lang, 'term', { os: os === 'openwrt' ? 'OpenWrt' : 'RouterOS' }) : t(lang, 'vlessLink'),
      file: how === 'file' ? fileOf(item) : null, text: item ? (how === 'fields' ? '' : bodies(item).join('\n')) : '',
      fields: how === 'fields' ? arr(item.fields || item.body).filter(function (p) { return Array.isArray(p) && p.length >= 2; }).map(function (p) { return [str(p[0]), str(p[1])]; }) : [] };
  }

  // ── DOM ──────────────────────────────────────────────────────────────────
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function h(tag, attrs, kids) {
    var n = document.createElement(tag), k;
    attrs = attrs || {};
    for (k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k) || attrs[k] === null || attrs[k] === undefined || attrs[k] === false) { continue; }
      if (k === 'text') { n.textContent = str(attrs[k]); } else if (k === 'on') {
        Object.keys(attrs.on).forEach(function (ev) { n.addEventListener(ev, attrs.on[ev]); });
      } else if (k === 'cls') { n.className = attrs[k]; } else { n.setAttribute(k, attrs[k] === true ? '' : str(attrs[k])); }
    }
    add(n, kids);
    return n;
  }
  function add(n, kids) {
    (Array.isArray(kids) ? kids : [kids]).forEach(function (c) {
      if (c === null || c === undefined || c === false) { return; }
      if (Array.isArray(c)) { add(n, c); return; }
      n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
  }
  function ic(name, size) {
    var I = g.KaskadIcons, s = null;
    try { s = I && I.svg ? I.svg(name, size || 16) : null; } catch (e) { s = null; }
    if (s) { s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false'); }
    return s;
  }
  // треугольник-пометка: заливка --warning-icon, «!» тёмный (HANDOFF §3)
  function triangle(size) {
    var s = document.createElementNS(SVG_NS, 'svg'), p = document.createElementNS(SVG_NS, 'path'), q = document.createElementNS(SVG_NS, 'path');
    s.setAttribute('viewBox', '0 0 20 20'); s.setAttribute('width', String(size || 18)); s.setAttribute('height', String(size || 18));
    s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false'); s.setAttribute('class', 'gs-tri__svg');
    p.setAttribute('d', 'M8.6 2.6a1.6 1.6 0 0 1 2.8 0l7.2 12.9A1.6 1.6 0 0 1 17.2 18H2.8a1.6 1.6 0 0 1-1.4-2.5z');
    p.setAttribute('class', 'gs-tri__fill');
    q.setAttribute('d', 'M10 7v4.6M10 14.4v.1');
    q.setAttribute('class', 'gs-tri__mark');
    s.appendChild(p); s.appendChild(q);
    return s;
  }
  function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) { return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return legacy(text); }); }
    } catch (e) { /* старый браузер — ниже */ }
    return { then: function (f) { f(legacy(text)); } };
  }
  function legacy(text) {
    var ta = document.createElement('textarea'), ok = false;
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function saveFile(name, text, mime) {
    var blob = new Blob([text], { type: mime || 'text/plain;charset=utf-8' }), url = URL.createObjectURL(blob);
    var a = h('a', { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  // ── компонент ────────────────────────────────────────────────────────────
  function mount(o) {
    var lang = o.lang === 'en' ? 'en' : 'ru';
    var T = function (k, v) { return t(lang, k, v); };
    var root = h('div', { cls: 'gs', 'data-viewer': o.viewer || 'admin' });
    // отрисовка меняет только body; «Скопировано» объявляет одна постоянная область live (№14: aria-live на кнопке
    // пересоздавался при каждой отрисовке и не объявлялся)
    var body = h('div', { cls: 'gs-body' }), live = h('span', { cls: 'gs-live', role: 'status', 'aria-live': 'polite' });
    root.appendChild(body); root.appendChild(live);
    var st = { r: null, app: '', card: '', tip: '', pin: false, text: false, qrPh: false, dense: false, copied: '', menu: false, routerRow: false, reveal: false, sqr: false };
    var qrCache = {}, badIcon = {}, tipTimer = 0, copyTimer = 0, mq = null, phone = false, alive = true;
    // «Режим стрима» панели (stream.js; /p/ и мастер его не грузят): QR не рисуется вовсе до «Показать QR» (st.sqr),
    // текст ключа, поля и блоки — маской с глазом, «Скопировать» — не показывая, ссылка файла — без href
    var SK = function () { return g.KaskadStream && g.KaskadStream.on ? g.KaskadStream : null; };
    var secMemo = {};
    var qrLive = { hide: function () { st.sqr = false; if (alive) { render(); } } };
    function streamQr(draw) {
      var S = SK();
      if (!S || st.sqr) { return draw(); }
      return S.qrCover(function () { st.sqr = true; st.reveal = true; S.track(qrLive); render(); focusKey('sqrhide'); });
    }
    // «Скрыть QR» — под показанным кодом ключа и под QR страницы в плотном виде: шторка снова, код из DOM уходит
    function sqrHideBtn() {
      return btn(SK().t('stm.qrHide'), 'eye-slash', function () { st.sqr = false; SK().untrack(qrLive); render(); focusKey('sqrshow'); }, 'gs-btn--sm ks-qrhide', { 'data-gk': 'sqrhide' });
    }
    // одно и то же значение — один узел на окно: перерисовка (копирование, смена вкладки) не сбрасывает показ
    function secNode(value, what, pre, id) {
      var S = SK(), k = (pre ? 'p|' : 'v|') + (id || '') + '|' + what + '|' + value;
      if (!secMemo[k]) { var s = S.secret(value, { what: S.t(what), pre: !!pre }); secMemo[k] = h('div', { cls: 'ks-key' }, [s.el, s.eye]); }
      return secMemo[k];
    }
    try { mq = g.matchMedia ? g.matchMedia(PHONE_MQ) : null; } catch (e) { mq = null; }
    phone = !!(mq && mq.matches);
    // смена ширины (поворот телефона, окно) — только перерисовка: открытая карточка или подсказка остаются за своим видом
    var onMq = function () { var p = !!(mq && mq.matches); if (p !== phone) { phone = p; render(); } };
    if (mq) { if (mq.addEventListener) { mq.addEventListener('change', onMq); } else if (mq.addListener) { mq.addListener(onMq); } }
    // на телефоне рамка кода зависит от ширины окна (3.3.3 №38): другая ширина — другой предел версии, перерисовка
    var lastMax = -1;
    // место под рамку — ширина блока ключа (.gs-key без полей), CSS px, как он стоит сейчас; 0 — ещё не измерен (узел
    // не в документе): рамка тогда — по ширине окна (frameAt). Мастер с колонкой видео, панель и /p/ на 1000–1300,
    // поля мастера на телефоне — рамка не шире своего блока, порог считается по ней (R-333-qr, ≤2 частей: №1, №7)
    var room = 0, roomDepth = 0;
    function keyRoom() {
      var el = body.querySelector ? body.querySelector('.gs-key') : null, cs = null;
      if (!el || !g.getComputedStyle || typeof el.clientWidth !== 'number') { return 0; }
      try { cs = g.getComputedStyle(el); } catch (e) { return 0; }
      var w = el.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
      return w > 0 ? Math.floor(w) : 0;
    }
    // после отрисовки и при смене размера: другое место — другая рамка (и, может быть, другой предел версии)
    function checkRoom() {
      var w = keyRoom();
      if (!w || w === room || !st.r) { return; }
      var a = fitNow();
      room = w;
      var b = fitNow();
      if (roomDepth < 2 && (!a || !b || a.frame !== b.frame)) { roomDepth++; try { render(); } finally { roomDepth--; } }
    }
    var onResize = function () {
      // нет кода ключа (L2TP, только поля) — нечего пересчитывать; перерисовка — только когда меняется рамка или предел
      // версии (код становится плотным или перестаёт) (R-333-qr, повтор: Б)
      checkRoom();
      // окно стало выше или ниже (развернули, закрыли DevTools, масштаб): блок ключа своей ширины не меняет, и
      // ResizeObserver молчит — «Показать QR целиком» сверяется с окном здесь (R-333-qr, третий круг №1)
      markCut();
      if (!phone || !st.r || lastMax < 0 || !K() || !K().maxVersionFor) { return; }
      var f = fitNow();
      if (f && K().maxVersionFor(f.frame, f.minPx) !== lastMax) { render(); }
    };
    try { if (g.addEventListener) { g.addEventListener('resize', onResize); } } catch (e) { /* нет окна */ }
    // блок меняет ширину и без смены окна (колонка видео мастера, окно панели открылось, узел вставлен позже set)
    var ro = null;
    // (перерисовка — в следующем кадре: внутри обратного вызова она меняет высоту наблюдаемого узла — «ResizeObserver loop»)
    var roTick = 0;
    // сразу после показа блок ещё растёт (шрифты, адрес страницы): в это время код снова доводится до вида
    var onRo = function () { roTick = 0; if (alive) { checkRoom(); if (Date.now() < revealUntil) { st.reveal = true; } revealSoon(); markCut(); } };
    try {
      ro = g.ResizeObserver ? new g.ResizeObserver(function () { if (!roTick) { roTick = g.requestAnimationFrame ? g.requestAnimationFrame(onRo) : setTimeout(onRo, 16); } }) : null;
      if (ro) { ro.observe(root); }
    } catch (e) { ro = null; }

    // T-67-332b №2: после Esc узел-треугольник под неподвижным указателем пересоздаётся, и браузер шлёт ему
    // mouseenter — подсказка открывалась снова, а Esc гасился без конца. Открытие наведением молчит, пока мышь
    // на самом деле не сдвинется (mousemove с другими координатами: ложный mousemove после перестройки — те же)
    var hoverMute = false, mouseXY = '';
    var onMove = function (e) {
      var xy = e.clientX + ',' + e.clientY;
      if (hoverMute && xy !== mouseXY) { hoverMute = false; }
      mouseXY = xy;
    };
    document.addEventListener('mousemove', onMove, true);
    function closePops(focusBack) {
      var had = st.card || st.tip || st.menu, back = st.card ? 'store:' + st.card : st.tip ? 'tip:' + st.tip : st.menu ? 'router' : '';
      st.card = ''; st.tip = ''; st.pin = false; st.menu = false;
      if (had) { hoverMute = true; render(); if (focusBack && back) { focusKey(back); } }
      return !!had;
    }
    // Esc сначала закрывает подсказку, карточку или список марок, и только следующий — диалог (№13): слушаем document
    // на погружении — раньше диалога, и даже когда подсказка открыта наведением, а фокус вне окна
    var onKey = function (e) {
      if ((e.key === 'Escape' || e.key === 'Esc') && alive && closePops(root.contains(document.activeElement))) { e.stopPropagation(); e.preventDefault(); }
    };
    document.addEventListener('keydown', onKey, true);
    var onDoc = function (e) {                     // клик мимо закреплённой подсказки, карточки или списка марок
      if (!st.pin && !st.menu && !st.card) { return; }
      var n = e.target;
      while (n && n !== document) {
        if (n.getAttribute && (n.getAttribute('data-pop') !== null)) { return; }
        n = n.parentNode;
      }
      if (st.pin || st.menu) { st.tip = ''; st.pin = false; st.menu = false; render(); }
    };
    document.addEventListener('click', onDoc, true);
    // 3.3.3 (R-333 №8): правый край ряда фишек гаснет, только пока за ним есть ещё (класс is-more); прокрутили до конца — край чёткий
    function edge(c) { c.classList.toggle('is-more', c.scrollWidth - c.clientWidth - c.scrollLeft > 2); }
    function edges() { Array.prototype.forEach.call(root.querySelectorAll('.gs-chips'), edge); }
    var onEdge = function (e) { var n = e.target; if (n && n.classList && n.classList.contains('gs-chips')) { edge(n); } };
    root.addEventListener('scroll', onEdge, true);
    if (g.addEventListener) { g.addEventListener('resize', edges); }

    function cssEsc(v) { return g.CSS && g.CSS.escape ? g.CSS.escape(v) : String(v).replace(/["\\\]\[]/g, '\\$&'); }
    function focusKey(k) {
      var n = null;
      try { n = root.querySelector('[data-gk="' + cssEsc(k) + '"]'); } catch (e) { n = null; }   // id из каталога — экранирован (№19)
      // фокус возвращается на узел, пересозданный перерисовкой, — окно ради него не прокручивается: прокрутка
      // перерисовки та же, что была (R-333-qr, третий круг №4)
      if (n && n.focus) { try { n.focus({ preventScroll: true }); } catch (e) { /* узел уже ушёл */ } }
    }
    // рамка кода ключа сейчас и порог камеры (3.3.3 №38): компьютер — рамка и порог компьютера, телефон — рамка по
    // ширине окна (как @media в give.css) и порог телефона (KaskadQRSlides.FIT)
    // ширина окна в CSS px, как её видит @media (100vw): documentElement.clientWidth — innerWidth на iOS меняется от
    // щипка-увеличения (R-333-qr, повтор: В)
    function viewWidth() {
      var de = g.document && g.document.documentElement, w = de && typeof de.clientWidth === 'number' ? de.clientWidth : 0;
      return w > 0 ? w : (typeof g.innerWidth === 'number' ? g.innerWidth : 0);
    }
    function fitNow() {
      var Q = K();
      if (!Q || !Q.FIT || !Q.frameAt) { return null; }
      var f = phone ? { frame: Q.frameAt(viewWidth()), minPx: Q.FIT.minPxPhone, phone: true } : { frame: Q.frameAt(), minPx: Q.FIT.minPx };
      // сторона = min(сторона по окну, место в блоке): узкая колонка — рамка меньше, порог — по ней (не проходит — свёрнут)
      if (room > 0) { f.frame = Math.max(0, Math.min(f.frame, room - 2 * Q.FIT.pad)); f.room = room; }
      return f;
    }
    // QR ключа и «Часть 1 из 2» целиком на экране — только по действию человека: «Всё равно показать», «QR для другого
    // устройства», «Показать QR целиком». При открытии окна и смене платформы, протокола или приложения окно не
    // прокручивается: выбор и шаг 1 остаются на виду (R-333-qr ≤2: повтор №2)
    var revealUntil = 0;
    // окно «код ещё растёт» — только до следующего действия человека (колесо, касание, мышь, клавиша): дальше смена
    // размера блока окно к коду не прокручивает, прокрутка — только по явному действию (R-333-qr, четвёртый круг, п. 4)
    var INPUT = ['wheel', 'touchstart', 'pointerdown', 'keydown'];
    var onInput = function () { revealUntil = 0; };
    INPUT.forEach(function (ev) { try { document.addEventListener(ev, onInput, { capture: true, passive: true }); } catch (e) { /* нет документа */ } });
    // видна ли рамка кода целиком: в окне и в каждом предке с прокруткой
    function wholeOnScreen(q) {
      var b = q.getBoundingClientRect(), vh = g.innerHeight || 0;
      if (!b || !(b.height > 0)) { return true; }
      if (vh && (b.top < -0.5 || b.bottom > vh + 0.5)) { return false; }
      for (var p = q.parentElement; p && p.getBoundingClientRect; p = p.parentElement) {
        var ov = g.getComputedStyle ? g.getComputedStyle(p).overflowY : '';
        if (ov === 'auto' || ov === 'scroll' || ov === 'hidden') {
          var pb = p.getBoundingClientRect();
          if (b.top < pb.top - 0.5 || b.bottom > pb.bottom + 0.5) { return false; }
        }
      }
      return true;
    }
    // код ключа виден не целиком (высота ноутбука, колонки друг под другом) — показать кнопку «Показать QR целиком».
    // Место под неё занимается один раз до новой отрисовки: дальше код стал виден целиком (прокрутили) — кнопка только
    // гаснет (data-off: visibility), снова срезан — загорается; код при этом не двигается и на границе не мигает
    function markCut() {
      if (!alive || !root.isConnected || !body.querySelector) { return; }
      var bt = body.querySelector('[data-gk="qrwhole"]'), q = body.querySelector('.gs-qr .gs-qr__frame');
      if (!bt || !q || !q.getBoundingClientRect || typeof bt.hidden !== 'boolean') { return; }
      var cut = !wholeOnScreen(q.parentNode);
      if (bt.hidden) { if (cut) { bt.hidden = false; } return; }
      if (cut) { bt.removeAttribute('data-off'); } else if (!bt.hasAttribute('data-off')) {
        // погасшая кнопка не держит фокус: он — на «Скопировать ключ» (или «Скачать»), если она видна целиком, иначе —
        // на сам код (он теперь целиком на виду): фокус не прячется за нижним краем диалога, окно при этом не двигается
        // (R-333-qr, четвёртый круг №1)
        if (bt === document.activeElement) {
          var nx = body.querySelector('[data-gk="copykey"]') || body.querySelector('[data-gk="dl"]');
          if (!nx || !wholeOnScreen(nx)) { nx = q.parentNode; }
          try { nx.focus({ preventScroll: true }); } catch (e) { /* узел ушёл */ }
        }
        bt.setAttribute('data-off', '');
      }
    }
    var scrollTick = 0;
    var onScroll = function () {
      if (scrollTick || !alive) { return; }
      scrollTick = g.requestAnimationFrame ? g.requestAnimationFrame(function () { scrollTick = 0; markCut(); }) : setTimeout(function () { scrollTick = 0; markCut(); }, 50);
    };
    try { document.addEventListener('scroll', onScroll, true); } catch (e) { /* нет документа */ }
    function revealSoon() {
      if (!st.reveal) { return; }
      setTimeout(function () {
        if (!alive || !st.reveal || !root.isConnected) { return; }
        var q = body.querySelector('.gs-qr');
        if (!q) { st.reveal = false; return; }       // кода на экране нет (свёрнут, файл, поля) — нечего показывать
        if (!q.getBoundingClientRect || !q.scrollIntoView) { st.reveal = false; return; }
        var b = q.getBoundingClientRect();
        if (!b || !(b.height > 0)) { return; }
        st.reveal = false;
        if (!revealUntil || Date.now() >= revealUntil) { revealUntil = Date.now() + 1200; }
        try { q.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (e) { /* старый браузер */ }
        markCut();                                   // прокрутки могло не быть (код уже целиком) — события scroll нет
      }, 0);
    }
    function vmOf() {
      return model(st.r, { lang: lang, viewer: o.viewer, device: o.device, app: st.app, chips: o.chips,
        actions: { grant: !!o.onGrant, enable: !!o.onEnable }, fit: fitNow(), shown: st.dense, only: o.only });
    }

    // подсказка у треугольника: компьютер — наведение и нажатие (закрепить), телефон — нажатие, во всю ширину
    function tip(id, content, opts) {
      opts = opts || {};
      var open = st.tip === id;
      var btn = h('button', { type: 'button', cls: 'gs-tri', 'data-gk': 'tip:' + id, 'data-pop': '', 'aria-label': opts.aria || T('noteAriaStep'),
        'aria-expanded': open ? 'true' : 'false', 'aria-controls': 'gs-tip-' + id.replace(/[^a-z0-9-]/gi, '') }, triangle(18));
      var wrap = h('span', { cls: 'gs-tipw' + (opts.down ? ' gs-tipw--down' : ''), 'data-pop': '' }, btn);
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        clearTimeout(tipTimer);
        var close = st.tip === id && st.pin;
        st.tip = close ? '' : id; st.pin = !close; render(); focusKey('tip:' + id);
      });
      if (!phone) {
        wrap.addEventListener('mouseenter', function () { clearTimeout(tipTimer); if (!hoverMute && !st.pin && st.tip !== id) { st.tip = id; render(); } });
        wrap.addEventListener('mouseleave', function () {
          if (st.pin) { return; }
          clearTimeout(tipTimer);
          tipTimer = setTimeout(function () { if (!st.pin && st.tip === id && alive) { st.tip = ''; render(); } }, TIP_DELAY);
        });
      }
      if (open) {
        // в подсказке бывают ссылки — это группа, до которой доходит Tab, а не role=tooltip (№14)
        var box = h('div', { cls: 'gs-tip', id: 'gs-tip-' + id.replace(/[^a-z0-9-]/gi, ''), role: 'group', 'aria-label': opts.aria || T('noteAriaStep'), 'data-pop': '' }, content);
        if (phone && opts.row) { opts.row.push(box); } else { wrap.appendChild(box); }
      }
      return wrap;
    }
    function tipContent(note) {
      var out = [];
      note.lines.forEach(function (l) { out.push(h('p', { cls: 'gs-tip__p', text: l.min ? T('needs', { min: l.min }) : (l.text || l) })); });
      if (note.links && note.links.length) {
        out.push(h('div', { cls: 'gs-tip__links' }, note.links.map(function (l) {
          return h('a', { cls: 'gs-tip__a', href: l.url, target: '_blank', rel: 'noopener noreferrer' }, [l.guide ? (OUR_GUIDE.test(l.url) ? T('ourGuide') : T('guide')) : (l.label || l.url), h('span', { cls: 'gs-ext', 'aria-hidden': 'true', text: ' ↗' })]);
        })));
      }
      return out;
    }

    function chipRow(label, chips, aria, onPick) {
      var row = h('div', { cls: 'gs-chips', role: 'radiogroup', 'aria-label': aria });
      chips.forEach(function (c) { row.appendChild(c); });
      roving(row);
      return h('div', { cls: 'gs-sel__row' }, [h('span', { cls: 'gs-sel__l', 'aria-hidden': 'true', text: label }), row]);
    }
    // стрелки ходят по плашкам ряда, как у радиокнопок; Tab — один вход в ряд (выбранная)
    function roving(row) {
      var items = Array.prototype.slice.call(row.querySelectorAll('[role="radio"]'));
      var on = items.filter(function (b) { return b.getAttribute('aria-checked') === 'true'; })[0] || items[0];
      items.forEach(function (b) { b.tabIndex = b === on ? 0 : -1; });
      row.addEventListener('keydown', function (e) {
        var i = items.indexOf(document.activeElement), d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (i < 0 || !d) { return; }
        e.preventDefault();
        var n = items[(i + d + items.length) % items.length];
        n.focus(); n.click();
      });
    }
    function chip(text, on, extra) {
      extra = extra || {};
      // «Роутер» открывает список — это кнопка со списком (aria-haspopup у radio недопустим, №14), не радиокнопка
      var b = h('button', { type: 'button', role: extra.popup ? null : 'radio', cls: 'gs-chip' + (on ? ' is-on' : '') + (extra.dim ? ' is-dim' : ''),
        'aria-checked': extra.popup ? null : on ? 'true' : 'false', 'aria-pressed': extra.popup ? (on ? 'true' : 'false') : null,
        'data-gk': extra.gk || null, 'aria-label': extra.aria || null, 'aria-haspopup': extra.popup ? 'listbox' : null, 'aria-expanded': extra.popup ? (extra.open ? 'true' : 'false') : null },
        [h('span', { text: text }), extra.work ? triangle(15) : null, extra.popup ? ic('chevron-down', 14) : null]);
      if (extra.pick) { b.addEventListener('click', extra.pick); }
      return b;
    }

    function selectors(vm) {
      var routerOn = isRouter(vm.os), out = [];
      var plats = DEVICES.map(function (p) {
        return chip(T('plat.' + p), vm.os === p, { gk: 'plat:' + p, pick: function () { if (vm.os !== p) { st.app = ''; st.routerRow = false; o.onPlatform(p); } } });
      });
      var rt = chip(routerOn ? T('routerOf', { b: T('plat.' + vm.os) }) : T('router'), routerOn, { gk: 'router', popup: true, open: st.menu || st.routerRow,
        pick: function (e) { e.stopPropagation(); if (phone) { st.routerRow = !st.routerRow; } else { st.menu = !st.menu; } render(); focusKey('router'); } });
      rt.setAttribute('data-pop', '');
      var rtWrap = h('span', { cls: 'gs-rtw', 'data-pop': '' }, rt);
      if (st.menu && !phone) {
        var list = h('div', { cls: 'gs-menu', role: 'listbox', 'aria-label': T('routerList'), 'data-pop': '' }, ROUTERS.map(function (p) {
          var b = h('button', { type: 'button', role: 'option', cls: 'gs-menu__i' + (vm.os === p ? ' is-on' : ''), 'aria-selected': vm.os === p ? 'true' : 'false', 'data-gk': 'rt:' + p,
            text: T('platList.' + p) || T('plat.' + p) });
          b.addEventListener('click', function () { st.menu = false; st.app = ''; if (vm.os !== p) { o.onPlatform(p); } else { render(); } });
          return b;
        }));
        list.addEventListener('keydown', function (e) {
          var bs = Array.prototype.slice.call(list.querySelectorAll('button')), i = bs.indexOf(document.activeElement);
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); bs[(i + (e.key === 'ArrowDown' ? 1 : -1) + bs.length) % bs.length].focus(); }
        });
        rtWrap.appendChild(list);
        setTimeout(function () { var f = list.querySelector('.is-on') || list.querySelector('button'); if (f) { f.focus(); } }, 0);
      }
      plats.push(rtWrap);
      plats.push(chip(T('plat.other'), vm.os === 'other', { gk: 'plat:other', pick: function () { if (vm.os !== 'other') { st.app = ''; o.onPlatform('other'); } } }));
      out.push(chipRow(T('platform'), plats, T('platform')));
      if (phone && (st.routerRow || routerOn)) {
        out.push(h('div', { cls: 'gs-sel__row gs-sel__row--sub' }, h('div', { cls: 'gs-chips', role: 'radiogroup', 'aria-label': T('routerList') }, ROUTERS.map(function (p) {
          return chip(T('plat.' + p), vm.os === p, { gk: 'rt:' + p, pick: function () { if (vm.os !== p) { st.app = ''; o.onPlatform(p); } } });
        }))));
      }
      out.push(protoRow(vm));
      return h('div', { cls: 'gs-sel' }, out);
    }
    function protoRow(vm, labelless) {
      var chips = vm.chips.map(function (c) {
        var b = chip(c.title, c.on, { dim: c.dim, work: c.work, gk: 'proto:' + c.kind, aria: c.tip ? c.title + ' — ' + c.tip : null,
          pick: function () { if (!c.on) { o.onProto(c.kind); } } });
        if (c.tip) {                                   // подсказка: «Huawei не поддерживает AmneziaWG» / «Не штатно…»
          b.setAttribute('title', c.tip);
          var w = h('span', { cls: 'gs-chipw' }, [b, h('span', { cls: 'gs-chiptip', role: 'tooltip', 'aria-hidden': 'true', text: c.tip })]);
          return w;
        }
        return b;
      });
      return labelless ? h('div', { cls: 'gs-chips', role: 'radiogroup', 'aria-label': T('protocol') }, chips) : chipRow(T('protocol'), chips, T('protocol'));
    }

    function stepHead(n, text, extra) {
      return h('div', { cls: 'gs-step__h' }, [h('span', { cls: 'gs-num', 'aria-hidden': 'true', text: String(n) }), h('h3', { cls: 'gs-step__t' }, [text, extra || null])]);
    }
    function btn(text, iconName, onClick, cls, attrs) {
      var b = h('button', Object.assign({ type: 'button', cls: 'gs-btn' + (cls ? ' ' + cls : '') }, attrs || {}), [iconName ? ic(iconName, 16) : null, h('span', { text: text })]);
      if (onClick) { b.addEventListener('click', onClick); }
      return b;
    }
    function copyBtn(text, label, cls, gk) {
      var done = st.copied === gk;
      return btn(done ? T('copied') : label, done ? 'check' : 'copy', function () {
        copyText(text).then(function (ok) {
          if (!ok || !alive) { return; }
          st.copied = gk; render(); focusKey(gk); live.textContent = T('copied');
          clearTimeout(copyTimer);
          copyTimer = setTimeout(function () { if (alive) { st.copied = ''; live.textContent = ''; render(); } }, 1600);
        });
      }, cls, { 'data-gk': gk });
    }
    function dlBtn(file, cls) {
      if (!file || !file.name || (!file.url && !file.body)) { return null; }
      var label = T('download', { ext: file.ext || file.name });
      if (file.url) {
        var a = h('a', { cls: 'gs-btn ' + (cls || ''), href: file.url, download: file.name, 'data-gk': 'dl' }, [ic('arrow-down-to-line', 16), h('span', { text: label })]);
        return SK() ? SK().link(a, file.url) : a;
      }
      return btn(label, 'arrow-down-to-line', function () { (o.save || saveFile)(file.name, file.body, file.mime); }, cls, { 'data-gk': 'dl' });
    }
    // иконка приложения: iconBase + apps/<id>.png; не загрузилась — плитка с первой буквой, и до конца жизни окна
    // этот адрес больше не просим (каждая перерисовка иначе мигала бы битой картинкой)
    function letterIcon(a) { return h('span', { cls: 'gs-appic gs-appic--none', 'aria-hidden': 'true', text: a ? a.name.charAt(0).toUpperCase() : '?' }); }
    function appIcon(a, size) {
      var src = a && a.icon ? str(o.iconBase) + a.icon : '';
      if (!src || badIcon[src]) { return letterIcon(a); }
      var img = h('img', { cls: 'gs-appic', src: src, alt: '', width: size || 40, height: size || 40, loading: 'lazy' });
      img.addEventListener('error', function () {
        badIcon[src] = true;
        if (img.parentNode) { img.parentNode.replaceChild(letterIcon(a), img); }
      });
      return img;
    }
    function storeLabel(l) { return str(l.kind) === 'site' ? T('openSite') : T('openIn', { store: str(l.label) }); }

    // QR ключа: одна рамка, части сами раз в 2 с (qr-slides.js), полоски и «Часть N из M» под рамкой
    function keyQr(vm, covered) {
      var key = vm.key, k = K(), id = vm.kind + '|' + vm.os + '|' + key.bundle + '|' + key.parts + '|' + (key.copy.length || 0);
      if (!qrCache[id]) {
        var bars = h('span', { cls: 'gs-bars', 'aria-hidden': 'true' }), of = h('span', { cls: 'gs-of' });
        var n = key.parts, barEls = [];
        for (var i = 0; i < n; i++) { barEls.push(h('span', { cls: 'gs-bar' })); bars.appendChild(barEls[i]); }
        // подпись кода для чтения с экрана — одна, у группы .gs-qr (R-333-qr, пятый круг №2): рисунок части скрыт,
        // рамка qr-slides — без своей подписи и без роли figure; «QR не получился» остаётся словами
        var draw = function (i2) {
          var x = null; try { x = o.drawQr ? o.drawQr(key.qr, i2, k && k.FIT ? k.FIT.quiet : 2) : null; } catch (e) { x = null; }
          if (x && x.setAttribute) { x.setAttribute('aria-hidden', 'true'); }
          return x || h('p', { cls: 'gs-qrfail', text: T('qrFail') });
        };
        var slides = k ? k.make({ count: n, texts: { part: T('part', { i: '{i}', n: '{n}' }) }, cls: 'gs-kq',
          onShow: function (c) { barEls.forEach(function (b, j) { b.className = 'gs-bar' + (j === c ? ' is-on' : ''); }); of.textContent = T('part', { i: c + 1, n: n }); } , draw: draw }) : { el: draw(0), stop: function () {} };
        if (k && slides.el && slides.el.setAttribute) { slides.el.setAttribute('role', 'none'); }
        // «Часть N из M» — над кодом: на высоте ноутбука видна сразу, без прокрутки (R-333-qr ≤2: повтор №2)
        var frame = h('div', { cls: 'gs-qr__frame' }, slides.el);
        // фокус с клавиатуры после «Показать QR целиком» (markCut): tabindex -1 — в обход Tab, data-gk — фокус
        // переживает перерисовку (focusKey)
        qrCache[id] = { el: h('div', { cls: 'gs-qr', tabindex: '-1', role: 'group', 'aria-label': T('keyQr'), 'data-gk': 'qr' }, [n > 1 ? h('div', { cls: 'gs-parts' }, [bars, of]) : null, frame]), frame: frame, slides: slides };
      }
      var c = qrCache[id].el;
      c.className = 'gs-qr' + (covered ? ' is-covered' : '');
      // 3.3.3: сторона рамки по порогу модуля (KaskadQRSlides.fit → side): компьютер — под код (312…436 снаружи),
      // телефон — вся ширина из give.css (@media), свой размер не ставим. CSSOM: CSP без style-атрибутов
      // место в блоке измерено (fitNow: room) — рамка по нему и на телефоне: поля мастера уже 100vw − 72 (№7)
      var fr = qrCache[id].frame, outer = key.fit && key.fit.side && k && k.FIT ? key.fit.side + 2 * k.FIT.pad : 0;
      var own = outer && (!phone || room > 0);
      if (fr && fr.style) {
        fr.style.width = own ? outer + 'px' : '';
        fr.style.height = own ? outer + 'px' : '';
        fr.setAttribute('data-side', own ? String(outer) : '');
      }
      return c;
    }

    // 3.3.3 №38: код ключа плотнее, чем берёт камера с экрана (версия выше предела рамки — KaskadQRSlides.fit).
    // Приложение, которое не читает части (AmneziaWG, incy, WireGuard берут один код .conf), — «QR слишком большой для
    // этого приложения»; части AmneziaVPN бывают плотными только в узкой рамке телефона — «слишком плотный для этого
    // экрана». Честный путь: файл (скачать, поделиться — слова только там, где кнопка есть), на компьютере — QR адреса
    // страницы человека (рамка по порогу, поле 2 модуля; под карточкой магазина накрыт, как QR ключа), сам код — по
    // просьбе («Всё равно показать QR»: на телефоне сразу раскрыт)
    function denseBox(vm, covered) {
      var key = vm.key, app = vm.app ? vm.app.name : '', kids = [], file = key.file, page = str(typeof o.pageUrl === 'function' ? o.pageUrl() : o.pageUrl);
      var parts = key.bundle === 'amnezia';
      var share = null;
      if (!parts && file && file.body && g.navigator && g.navigator.canShare && g.File) {
        var f = null, ok = false;
        try { f = new g.File([file.body], file.name, { type: file.mime || 'text/plain' }); } catch (e) { f = null; }
        try { ok = !!(f && g.navigator.canShare({ files: [f] })); } catch (e) { ok = false; }
        if (ok) {
          share = btn(T('shareFile'), 'arrow-up-from-line', function () {
            try { g.navigator.share({ files: [f], title: file.name }).catch(function () { /* отменили */ }); } catch (e) { /* нет */ }
          }, 'gs-btn--key', { 'data-gk': 'share' });
        }
      }
      // части (vpn://) и ключ без файла — скопировать; файл — скачать (и поделиться, только если кнопка есть)
      // админ выдаёт ключ другому — «перешлите человеку»; сам человек (/p/) и мастер — «себе» (R-333-qr, повтор: А)
      var other = o.viewer === 'admin' ? 'Admin' : '';
      // мешает экран, а не приложение: части, телефон (R-333-qr, ≤2 частей: №5), узкое место на компьютере — код взяли
      // бы в полной рамке компьютера; «для этого приложения» — только когда не берётся и в ней
      var Q0 = K(), full = !!(Q0 && Q0.maxVersionFor && Q0.FIT && key.fit && key.fit.ver > 0 && key.fit.ver <= Q0.maxVersionFor(Q0.FIT.frameMax, Q0.FIT.minPx));
      // компьютер, узкое окно: дело в ширине, а не в камере — «Расширьте окно» или ключ/файл на телефон (R-333-qr ≤2: повтор №1, №3)
      var narrow = !phone && full ? 'Narrow' : '';
      var words = parts || !file ? (phone ? 'denseTextPhone' : 'denseText' + narrow + other) :
        (phone ? (share ? 'denseFilePhoneShare' : 'denseFilePhone') : 'denseFile' + narrow) + other;
      kids.push(h('p', { cls: 'gs-dense__t', text: T(parts || phone || full ? 'denseTScreen' : 'denseT') }));
      kids.push(h('p', { cls: 'gs-dense__x', text: T(words, { app: app || T('theApp') }) }));
      if (share) { kids.push(h('div', { cls: 'gs-keybtns' }, [share])); }
      if (page && !phone && /^https?:\/\/[^\s"'<>]+$/.test(page)) {
        var pic = null, Q = K();
        try { pic = SK() && !st.sqr ? streamQr(null) : (o.drawLinkQr ? o.drawLinkQr({ url: page, qr: o.pageQr || null, quiet: PAGE_QUIET }) : null); } catch (e) { pic = null; }
        // рамка под порог компьютера при любой длине адреса (длинный домен с портом — версия 5–7): не меньше PAGE_MIN
        var v = Q && Q.versionOf ? Q.versionOf(page) : 0, minPx = Q && Q.FIT ? Q.FIT.minPx : 3;
        var side = Math.max(PAGE_MIN, Math.ceil(minPx * ((v ? 4 * v + 17 : 33) + 2 * PAGE_QUIET))) + 2 * PAGE_PAD;
        var box = pic ? h('div', { cls: 'gs-dense__qr', 'data-side': String(side) }, pic) : null;
        if (box && !(SK() && !st.sqr)) { box.style.width = side + 'px'; box.style.height = side + 'px'; }   // CSSOM: CSP без style-атрибутов
        kids.push(h('div', { cls: 'gs-dense__page' + (covered ? ' is-covered' : ''), 'aria-hidden': covered ? 'true' : null }, [box,
          h('p', { cls: 'gs-dense__x', text: T(o.viewer === 'person' ? 'densePage' : 'densePageAdmin') })]));
        // режим стрима: показанный QR страницы прячется тут же, как QR ключа (REV-stream З-2)
        if (box && SK() && st.sqr) { kids.push(sqrHideBtn()); }
      }
      kids.push(h('button', { type: 'button', cls: 'gs-textbtn', 'data-gk': 'denseshow', on: { click: function () { st.dense = true; st.reveal = true; if (phone) { st.qrPh = true; } render(); focusKey(phone ? 'qrph' : 'denseshow'); } } },
        [h('span', { text: T('denseShow') }), ic('chevron-down', 14)]));
      return h('div', { cls: 'gs-dense', role: 'note', 'data-ver': String(key.fit ? key.fit.ver : ''), 'data-max': String(key.fit ? key.fit.max : '') }, kids);
    }

    // пометка под плотным кодом на телефоне советует кнопку, которая есть под ним: файл .conf — «Скачать .conf»,
    // ключ vpn:// / ссылка — «Скопировать ключ» вверху окна (R-333-qr, ≤2 частей: №6)
    function denseWarn(key) {
      if (!phone) { return T('denseWarn'); }
      if (key.bundle !== 'amnezia' && key.file && key.file.name) { return T('denseWarnPhoneFile', { dl: T('download', { ext: key.file.ext || key.file.name }) }); }
      return T(key.copy ? 'denseWarnPhone' : 'denseWarn');
    }
    function keyBlock(vm) {
      var key = vm.key, kids = [], covered = !!st.card && !phone;
      if (!key) { return null; }
      if (key.qr && key.dense && !st.dense) {
        kids.push(denseBox(vm, covered));
      } else if (key.qr) {
        if (phone) {
          kids.push(h('button', { type: 'button', cls: 'gs-qrph', 'aria-expanded': st.qrPh ? 'true' : 'false', 'data-gk': 'qrph', on: { click: function () { st.qrPh = !st.qrPh; st.reveal = st.qrPh; render(); focusKey('qrph'); } } },
            [ic('qr-code', 16), h('span', { text: T('qrOther') }), ic('chevron-down', 14)]));
        }
        if (!phone || st.qrPh) {
          // код не помещается в видимую часть окна — к нему ведёт кнопка, сам окно не прокручиваем (markCut)
          if (!phone) {
            kids.push(h('button', { type: 'button', cls: 'gs-textbtn gs-qrwhole', hidden: true, 'data-gk': 'qrwhole', on: { click: function () {
              st.reveal = true; revealSoon();
            } } }, [h('span', { text: T('qrWhole') }), ic('chevron-down', 14)]));
          }
          var qrBox = h('div', { cls: 'gs-qrwrap' }, streamQr(function () { return keyQr(vm, covered); }));
          if (covered && !(SK() && !st.sqr)) {
            qrBox.appendChild(h('div', { cls: 'gs-cover' }, [h('p', { text: T('cover') }), btn(T('showKey'), 'eye', function () { var a = st.card; st.card = ''; render(); focusKey('store:' + a); }, 'gs-btn--sm', { 'data-gk': 'showkey' })]));
          }
          kids.push(qrBox);
          if (SK() && st.sqr) { kids.push(sqrHideBtn()); }
          if (key.dense) { kids.push(h('p', { cls: 'gs-dense__warn', text: denseWarn(key) })); }
        }
      }
      var row = [];
      if (SK() && (key.copy || key.text)) { kids.push(secNode(key.copy || key.text, 'stm.w.key', !key.copy)); }
      if (!phone && key.copy) { row.push(copyBtn(key.copy, T('copyKey'), 'gs-btn--key', 'copykey')); }
      if (key.file) { row.push(dlBtn(key.file, 'gs-btn--key')); }
      if (row.length) { kids.push(h('div', { cls: 'gs-keybtns' }, row)); }
      if (key.text && !SK()) {
        kids.push(h('button', { type: 'button', cls: 'gs-textbtn', 'aria-expanded': st.text ? 'true' : 'false', 'data-gk': 'showtext', on: { click: function () { st.text = !st.text; render(); focusKey('showtext'); } } },
          [h('span', { text: st.text ? T('hideText') : T('showText') }), ic('chevron-down', 14)]));
        if (st.text) { kids.push(h('pre', { cls: 'gs-keytext', tabindex: '0', text: key.text })); }
      }
      return h('div', { cls: 'gs-key' }, kids);
    }

    function appRow(vm, a) {
      var on = vm.app && vm.app.id === a.id, open = st.card === a.id && !phone;
      // вкладка «Подписка» на ПК (макет Б r3, О5): строка в одну линию — значок, имя, «рекомендуем», магазин, QR значком;
      // подпись «ссылка подписки» не нужна (вся вкладка о ней)
      var line = o.only === 'apps' && !phone;
      // радиокнопка — сам кружок с подписью приложения; ссылки пометки — рядом, не внутри радио
      // (FIX-332g №14: вложенный интерактив в role=radio недоступен экранному диктору); нажатие по строке — выбор
      var radio = h('span', { cls: 'gs-radio', role: 'radio', 'aria-checked': on ? 'true' : 'false', tabindex: on ? '0' : '-1', 'data-gk': 'app:' + a.id,
        'aria-label': a.name + (a.recommended ? ', ' + T('recommended') : '') + ', ' + a.fmtWord + (a.ext ? ' ' + a.ext : '') });
      // треугольника в строке приложения нет нигде — подписка, статический ключ, /p/, мастер (решение координатора,
      // круг 1; поручение владельца): справочные пометки каталога («Бесплатно…», «Платно…», «Нет в российском App
      // Store…», «не проверено») серой строкой под именем, до двух строк, у выбранного — целиком (нажатие на строку);
      // другие ссылки приложения — там же словами. Подделки — одной строкой над списком (fakesLine).
      // У ключа формат приложения («файл .conf») остаётся строкой над пометкой; у подписки его нет — пометка вместо него
      var nt = a.tip ? noteLine(a) : null;
      var fmt = line ? null : h('span', { cls: 'gs-app__f' }, a.ext ? [a.fmtWord + ' ', h('code', { text: a.ext })] : a.fmtWord);
      var main = h('div', { cls: 'gs-app__main' },
        [radio, appIcon(a, phone ? 36 : line ? 32 : 40), h('span', { cls: 'gs-app__tx' }, [h('span', { cls: 'gs-app__n' }, [h('b', { text: a.name }),
          a.recommended ? h('span', { cls: 'gs-rec', text: T('recommended') }) : null]),
          o.only === 'apps' ? nt || fmt : fmt, o.only === 'apps' ? null : nt])]);
      var pick = function () { if (st.app !== a.id) { st.app = a.id; st.card = ''; st.dense = false; render(); focusKey('app:' + a.id); } };
      main.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('[data-pop]')) { return; } pick(); });
      radio.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      var acts = [];
      if (!phone && a.store) {
        acts.push(h('a', { cls: 'gs-btn gs-btn--row', href: a.store.url, target: '_blank', rel: 'noopener noreferrer', 'data-gk': 'go:' + a.id },
          [line ? null : ic('arrow-right-from-square', 16), h('span', { text: str(a.store.label) })]));
        var qb = h('button', { type: 'button', cls: 'gs-btn gs-btn--row' + (line ? ' gs-btn--ic' : '') + (open ? ' is-open' : ''), 'aria-expanded': open ? 'true' : 'false', 'data-gk': 'store:' + a.id, 'data-pop': '',
          'aria-controls': 'gs-card-' + a.id, 'aria-label': line ? T('qrInstall') + ' · ' + a.name : null, title: line ? T('qrInstall') : null },
          line ? [ic('qr-code', 16)] : [ic('qr-code', 16), h('span', { text: T('qrInstall') })]);
        qb.addEventListener('click', function (e) { e.stopPropagation(); st.card = st.card === a.id ? '' : a.id; st.tip = ''; st.pin = false; render(); focusKey('store:' + a.id); });
        var qw = h('span', { cls: 'gs-qrbw', 'data-pop': '' }, qb);
        if (open) { qw.appendChild(storeCard(a)); }
        acts.push(qw);
      }
      return h('div', { cls: 'gs-app' + (line ? ' gs-app--line' : '') + (on ? ' is-on' : '') }, [main, acts.length ? h('div', { cls: 'gs-app__acts' }, acts) : null]);
    }
    // серая строка пометок приложения: до двух строк с многоточием (give.css), весь текст — в title; ссылки
    // кроме первой (она — кнопка магазина) — словами после текста
    function noteLine(a) {
      var text = a.tip.lines.join(' · '), kids = [];
      if (text) { kids.push(h('span', { cls: 'gs-app__nt-t', text: text, title: text })); }
      a.tip.links.forEach(function (l) {
        kids.push(h('a', { cls: 'gs-app__nt-a', href: l.url, target: '_blank', rel: 'noopener noreferrer', 'data-pop': '', text: l.label || l.url }));
      });
      return kids.length ? h('span', { cls: 'gs-app__nt' }, kids) : null;
    }
    function storeCard(a) {
      var art = null;
      try { art = o.drawLinkQr ? o.drawLinkQr(a.store) : null; } catch (e) { art = null; }
      var x = h('button', { type: 'button', cls: 'gs-card__x', 'aria-label': T('closeQr'), 'data-gk': 'cardx' }, ic('xmark', 14));
      x.addEventListener('click', function (e) { e.stopPropagation(); st.card = ''; render(); focusKey('store:' + a.id); });
      return h('div', { cls: 'gs-card', id: 'gs-card-' + a.id, role: 'dialog', 'aria-label': T('camera', { store: str(a.store.label), app: a.name }), 'data-pop': '' },
        [h('div', { cls: 'gs-card__h' }, [h('p', { text: T('camera', { store: str(a.store.label), app: a.name }) }), x]),
          art ? h('div', { cls: 'gs-card__qr' }, art) : null]);
    }

    // «Открыть в App Store» выбранного приложения — сверху на телефоне (в строках там кнопок магазина нет)
    function openLink(a) {
      return h('a', { cls: 'gs-open', href: a.store.url, target: '_blank', rel: 'noopener noreferrer', 'data-gk': 'open' },
        [appIcon(a, 36), h('span', { cls: 'gs-open__tx' }, [h('b', { text: storeLabel(a.store) }), h('span', { text: a.name })]), ic('arrow-right-from-square', 18)]);
    }
    // список приложений шага 1: радиокнопки строк, стрелки ходят по ним
    function appList(vm) {
      var list = h('div', { cls: 'gs-apps', role: 'radiogroup', 'aria-label': T('s1') }, vm.apps.map(function (a) { return appRow(vm, a); }));
      list.addEventListener('keydown', function (e) {
        var d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (!d || !e.target.getAttribute || e.target.getAttribute('role') !== 'radio') { return; }
        e.preventDefault();
        var ids = vm.apps.map(function (a) { return a.id; }), i = ids.indexOf(vm.app.id), n = ids[(i + d + ids.length) % ids.length];
        st.app = n; st.card = ''; render(); focusKey('app:' + n);
      });
      return list;
    }
    // только список (o.only = 'apps', вкладка «Подписка» окна панели): те же строки, пометки (подделки, «не проверено»)
    // и «QR · установить», что у шага 1 ключа; на телефоне — «Открыть в …» выбранного сверху, как у ключа
    function appsOnly(vm) {
      if (!vm.apps || !vm.apps.length) { return []; }
      return [phone && vm.app && vm.app.store ? h('div', { cls: 'gs-top' }, [openLink(vm.app)]) : null, fakesLine(vm), appList(vm)];
    }
    // пометка пары «платформа × протокол» у шага «Установите приложение» — серым текстом под заголовком, без треугольника
    // (решение координатора 29.10 по R-it1-tri, вопрос 1): условие, пометка, «не проверено», затем ссылки (инструкция
    // первой); без пометки — только ссылка на инструкцию пары, если она есть (раньше она жила лишь в подсказке)
    function pairNote(vm) {
      var n = vm.note, lines = n ? n.lines : [], links = n ? n.links : vm.guide ? [{ guide: true, url: vm.guide }] : [];
      if (!lines.length && !links.length) { return null; }
      var kids = lines.map(function (l) { return h('p', { cls: 'gs-pair__p', text: l.min ? T('needs', { min: l.min }) : (l.text || l) }); });
      if (links.length) {
        kids.push(h('p', { cls: 'gs-pair__links' }, links.map(function (l) {
          var w = l.guide ? (OUR_GUIDE.test(l.url) ? T('ourGuide') : T('guide')) : (l.label || l.url);
          return h('a', { cls: 'gs-more', href: l.url, target: '_blank', rel: 'noopener noreferrer' }, [w.charAt(0).toUpperCase() + w.slice(1), h('span', { cls: 'gs-ext', 'aria-hidden': 'true', text: ' ↗' })]);
        })));
      }
      return h('div', { cls: 'gs-step__sub gs-pair' }, kids);
    }
    // «в магазинах есть подделки» — одна строка над списком приложений (у строк её нет), если хоть у одного она в каталоге
    function fakesLine(vm) {
      return vm.apps.some(function (a) { return a.fakes; }) ? h('p', { cls: 'gs-fakes' }, [triangle(16), h('span', { text: T('fakesAll') })]) : null;
    }

    // шаги устройства: 1 Приложение · 2 Ключ · 3 Включите VPN
    function deviceSteps(vm) {
      var out = [], cols = [];
      if (phone && vm.app && (vm.app.store || (vm.key && vm.key.copy))) {
        var top = [];
        if (vm.app.store) { top.push(openLink(vm.app)); }
        if (vm.key && vm.key.copy) { top.push(copyBtn(vm.key.copy, T('copyKey'), 'gs-btn--big', 'copytop')); }
        out.push(h('div', { cls: 'gs-top' }, top));
      }
      var s1 = [];
      var pair = pairNote(vm);
      if (vm.apps && vm.apps.length) {
        s1.push(stepHead(1, T('s1')));
        if (vm.apps.length > 1) { s1.push(h('p', { cls: 'gs-step__sub', text: T('s1sub') })); }
        s1.push(pair);
        s1.push(fakesLine(vm));
        s1.push(appList(vm));
      } else if (vm.builtin) {
        s1.push(stepHead(1, T('s1builtin')), h('p', { cls: 'gs-step__sub', text: vm.builtin }), pair);
      } else if (vm.note) {
        s1.push(stepHead(1, T('s1')), pair);
      }
      var s2 = [];
      var num2 = s1.length ? 2 : 1;
      if (vm.key) {
        s2.push(stepHead(num2, T('s2')));
        if (vm.app) {
          s2.push(h('p', { cls: 'gs-step__sub' }, [T('s2for', { app: vm.app.name, fmt: vm.key.fmtWord }) + ' ', h('code', { text: vm.key.ext })]));
          if (vm.keyName) { s2.push(h('p', { cls: 'gs-step__as', text: T('shows', { name: vm.keyName }) })); }
        }
        s2.push(keyBlock(vm));
      } else if (vm.others && vm.others.length) {
        s2.push(stepHead(num2, T('s2plain')));
        vm.others.forEach(function (it) { s2.push(itemNode(it)); });
      }
      if (s1.length) { cols.push(h('section', { cls: 'gs-step gs-col gs-col--l' }, s1)); }
      if (s2.length) { cols.push(h('section', { cls: 'gs-step gs-col gs-col--r' }, s2)); }
      out.push(h('div', { cls: 'gs-cols' }, cols));
      var n3 = (s1.length ? 1 : 0) + (s2.length ? 1 : 0) + 1;
      if (vm.app || vm.pathText) {
        var path = vm.path ? h('span', { cls: 'gs-path' }, vm.path.map(function (p, i) { return [i ? h('span', { cls: 'gs-path__sep', 'aria-hidden': 'true' }, ic('chevron-right', 14)) : null, h('span', { cls: 'gs-pill', text: p })]; })) : h('span', { cls: 'gs-step__sub', text: vm.pathText });
        out.push(h('section', { cls: 'gs-step3' }, [h('div', { cls: 'gs-step__h' }, [h('span', { cls: 'gs-num', 'aria-hidden': 'true', text: String(n3) }), h('h3', { cls: 'gs-step__t', text: T('s3') })]),
          vm.path && vm.app ? h('span', { cls: 'gs-step3__in', text: T('inApp', { app: vm.app.name }) }) : null, path]));
      }
      return out;
    }
    function itemNode(it) {
      var fmt = it.format;
      if (fmt === 'fields') { return fieldsNode(arr(it.fields || it.body)); }
      if (fmt === 'qr') { return null; }
      if (FILE_FMT[fmt] && it.filename) { return fileCard(fileOf(it), str(it.title)); }
      if (fmt === 'guide') { return h('div', { cls: 'gs-guide' }, bodies(it).join('\n').split('\n').filter(function (x) { return x.trim(); }).map(function (x) { return h('p', { text: x }); })); }
      var text = bodies(it).join('\n');
      if (!text) { return null; }
      return codeBlock(str(it.title), text, 'item:' + fmt);
    }
    function fieldsNode(rows) {
      return h('dl', { cls: 'gs-fields' }, rows.filter(function (p) { return Array.isArray(p) && p.length >= 2; }).map(function (p, i) {
        return h('div', { cls: 'gs-field' }, [h('dt', { text: str(p[0]) }), h('dd', null, [SK() ? h('span', { cls: 'gs-field__v' }, secNode(str(p[1]), 'stm.what', false, 'f' + i)) : h('span', { cls: 'gs-field__v', text: str(p[1]) }),
          (function () { var b = copyBtn(str(p[1]), '', 'gs-btn--icon', 'f' + i); b.setAttribute('aria-label', T('copy') + ': ' + str(p[0])); return b; })()])]);
      }));
    }
    function fileCard(f, sub) {
      if (!f) { return null; }
      return h('div', { cls: 'gs-file' }, [h('span', { cls: 'gs-file__ic', 'aria-hidden': 'true' }, ic('file-text', 20)),
        h('span', { cls: 'gs-file__tx' }, [h('b', { text: f.name }), sub ? h('span', { text: sub }) : null]), dlBtn(f, 'gs-btn--file')]);
    }
    function codeBlock(label, text, gk) {
      return h('div', { cls: 'gs-code' }, [h('div', { cls: 'gs-code__h' }, [h('span', { text: label }), copyBtn(text, T('copy'), 'gs-btn--sm', gk)]),
        SK() ? secNode(text, 'stm.w.key', true) : h('pre', { cls: 'gs-code__pre', tabindex: '0', text: text })]);
    }

    // шаги роутера: 1 Подготовьте роутер ⚠ · 2 Добавьте настройки (файл / команды / поля) · 3 Проверьте
    function routerSteps(vm) {
      var rt = vm.rt, out = [], extra = [];
      var note = vm.note ? tip('router', tipContent(vm.note), { down: true, row: extra }) : null;
      out.push(h('section', { cls: 'gs-step gs-rstep' }, [stepHead(1, T('prep'), note), extra,
        h('p', { cls: 'gs-step__sub' }, [rt.prep + ' ', vm.guide ? h('a', { href: vm.guide, target: '_blank', rel: 'noopener noreferrer', cls: 'gs-more', text: T('more') }) : null])]));
      var s2 = [stepHead(2, T('add')), h('p', { cls: 'gs-step__sub', text: rt.add })];
      if (rt.how === 'file') { s2.push(fileCard(rt.file, rt.notes[0] || T('fileSub'))); }
      else if (rt.how === 'cmd' || rt.how === 'link') { s2.push(codeBlock(rt.term, rt.text, 'cmd')); }
      else if (rt.how === 'fields') { s2.push(fieldsNode(rt.fields)); }
      else if (rt.how === 'guide') { s2.push(h('div', { cls: 'gs-guide' }, rt.text.split('\n').filter(function (x) { return x.trim(); }).map(function (x) { return h('p', { text: x }); }))); }
      if (rt.how !== 'file') { rt.notes.forEach(function (n) { s2.push(h('p', { cls: 'gs-step__as', text: n })); }); }
      else { rt.notes.slice(1).forEach(function (n) { s2.push(h('p', { cls: 'gs-step__as', text: n })); }); }
      out.push(h('section', { cls: 'gs-step gs-rstep' }, s2));
      var ck = rt.check.length ? h('span', { cls: 'gs-path' }, rt.check.map(function (p, i) { return [i ? h('span', { cls: 'gs-path__sep', 'aria-hidden': 'true' }, ic('chevron-right', 14)) : null, h('span', { cls: 'gs-pill', text: p })]; })) : null;
      out.push(h('section', { cls: 'gs-step3' }, [h('div', { cls: 'gs-step__h' }, [h('span', { cls: 'gs-num', 'aria-hidden': 'true', text: '3' }), h('h3', { cls: 'gs-step__t', text: T('check') })]),
        ck, h('span', { cls: 'gs-step3__tail', text: (ck ? '— ' : '') + rt.checkTail })]));
      return out;
    }

    // Б — «Подойдёт:» строки других протоколов; В — «нет подходящего» и ссылка
    function altsView(vm) {
      var out = [h('h3', { cls: 'gs-none__t', text: vm.noneText })];
      if (vm.state === 'nothing') {
        if (vm.guide) { out.push(h('p', { cls: 'gs-step__sub' }, h('a', { href: vm.guide, target: '_blank', rel: 'noopener noreferrer', cls: 'gs-more' }, [OUR_GUIDE.test(vm.guide) ? T('ourGuide') : T('guide'), ' ↗']))); }
        return [h('div', { cls: 'gs-nothing' }, out)];
      }
      out.push(h('p', { cls: 'gs-step__sub', text: T('fits') }));
      out.push(h('div', { cls: 'gs-alts' }, vm.alts.map(function (a) {
        var extra = [];
        var act = null;
        if (a.act === 'pick') { act = btn(T('pick'), null, function () { o.onProto(a.proto); }, 'gs-btn--primary', { 'data-gk': 'alt:' + a.proto }); add(act, ic('chevron-right', 14)); }
        else if (a.act === 'grant') { act = btn(T('addDev'), 'plus', function () { o.onGrant(a.proto); }, '', { 'data-gk': 'alt:' + a.proto }); }
        else if (a.act === 'enable') { act = btn(T('enableSrv'), 'plug-connection', function () { o.onEnable(a.proto); }, '', { 'data-gk': 'alt:' + a.proto }); }
        else { act = h('span', { cls: 'gs-alt__ask', text: a.ask }); }
        var row = h('div', { cls: 'gs-alt', 'data-state': a.state }, [h('div', { cls: 'gs-alt__tx' }, [h('b', { cls: 'gs-alt__n' }, [a.title, a.note ? tip('alt-' + a.proto, tipContent({ lines: [{ text: a.note }], links: [] }), { row: extra }) : null]),
          h('span', { cls: 'gs-alt__st' }, [h('span', { cls: 'gs-dot gs-dot--' + a.state, 'aria-hidden': 'true' }), a.status])]), act]);
        if (a.act === 'pick') { row.addEventListener('click', function (e) { if (e.target === row) { o.onProto(a.proto); } }); }
        return extra.length ? h('div', null, [row, extra]) : row;
      })));
      return [h('div', { cls: 'gs-none' }, out)];
    }

    function render() {
      if (!alive) { return; }
      var r = st.r;
      if (!r) { return; }
      var vm = vmOf();
      lastMax = vm.key && vm.key.fit ? vm.key.fit.max : -1;
      root.setAttribute('data-phone', phone ? '1' : '0');
      var kids = [];
      if (o.selectors) { kids.push(selectors(vm)); }
      else if (o.protoRow) { kids.push(h('div', { cls: 'gs-sel' }, protoRow(vm, true))); }
      if (o.selectors) { kids.push(h('hr', { cls: 'gs-sep' })); }
      if (vm.state === 'work') {
        var w = [h('b', { text: vm.workText }), ' ', T('workHow'), ' '];
        if (vm.guide) { w.push(h('a', { href: vm.guide, target: '_blank', rel: 'noopener noreferrer', cls: 'gs-more' }, [OUR_GUIDE.test(vm.guide) ? T('ourGuide') : T('guide'), ' ↗'])); }
        kids.push(h('div', { cls: 'gs-banner', role: 'note' }, [h('span', { cls: 'gs-banner__i', 'aria-hidden': 'true' }, ic('circle-info', 20)), h('p', null, w)]));
        // SPEC п. 2: под обходным путём — короткая строка о штатных протоколах этой системы (готовый — нажать)
        if (vm.alts.length) {
          // коротко: готовые первыми, всего не больше четырёх (порядок каталога — лучшие раньше)
          var also = vm.alts.filter(function (a) { return a.act === 'pick'; }).concat(vm.alts.filter(function (a) { return a.act !== 'pick'; })).slice(0, 4);
          kids.push(h('p', { cls: 'gs-also' }, [T('also') + ' '].concat(also.map(function (a, i) {
            var name = a.act === 'pick' ? h('button', { type: 'button', cls: 'gs-also__a', 'data-gk': 'also:' + a.proto, on: { click: function () { o.onProto(a.proto); } } }, a.title)
              : h('span', { cls: 'gs-also__n', title: a.status, text: a.title });
            return i ? [', ', name] : name;
          }))));
        }
        kids.push(h('hr', { cls: 'gs-sep' }));
      }
      if (o.only === 'apps') { kids = appsOnly(vm); }
      else if (vm.state === 'none' || vm.state === 'nothing') { kids = kids.concat(altsView(vm)); }
      else if (vm.router) { kids = kids.concat(routerSteps(vm)); }
      else { kids = kids.concat(deviceSteps(vm)); }
      var active = document.activeElement && root.contains(document.activeElement) ? document.activeElement.getAttribute('data-gk') : null;
      // «Показать QR целиком» новой отрисовки — в том же состоянии, что прошлая: место под неё не пропадает и код не
      // поднимается на её высоту; гаснет она или загорается — решает markCut сразу ниже (R-333-qr, третий круг №3)
      var wasBt = body.querySelector ? body.querySelector('[data-gk="qrwhole"]') : null;
      if (wasBt && (typeof wasBt.hidden !== 'boolean' || !wasBt.hasAttribute)) { wasBt = null; }   // не браузер
      var wasShown = !!wasBt && !wasBt.hidden, wasOff = !!wasBt && wasBt.hasAttribute('data-off');
      // прокрутка диалога и окна: пока старое вынуто, высота падает, и браузер обрезает scrollTop — вернуть как было
      // (R-333-qr, третий круг №4)
      var keep = [];
      for (var sp = root.parentElement; sp; sp = sp.parentElement) { if (sp.scrollTop > 0) { keep.push([sp, sp.scrollTop]); } }
      while (body.firstChild) { body.removeChild(body.firstChild); }
      add(body, kids);
      var nb = wasShown && body.querySelector ? body.querySelector('[data-gk="qrwhole"]') : null;
      if (nb) { nb.hidden = false; if (wasOff) { nb.setAttribute('data-off', ''); } }
      for (var ki = 0; ki < keep.length; ki++) { if (keep[ki][0].scrollTop !== keep[ki][1]) { keep[ki][0].scrollTop = keep[ki][1]; } }
      edges();
      if (active) { focusKey(active); }
      if (o.onRender) { try { o.onRender(vm); } catch (e) { /* подпись хозяина */ } }
      checkRoom();
      revealSoon();
      markCut();
      setTimeout(markCut, 0);                        // узел ещё не в документе (первая отрисовка до вставки) — после неё
    }

    return {
      el: root,
      set: function (r, keep) {
        var prev = st.r;
        st.r = r;
        if (!keep || !prev || prev.kind !== r.kind || prev.os !== r.os) { st.card = ''; st.tip = ''; st.pin = false; st.text = false; st.qrPh = false; st.dense = false; st.menu = false; }
        if (prev && prev.os !== r.os) { st.app = ''; }
        render();
      },
      model: function () { return st.r ? vmOf() : null; },
      // адрес страницы человека для «Откройте страницу на телефоне» (панель узнаёт его отдельным запросом)
      page: function (url) { o.pageUrl = url || ''; if (st.r) { render(); } },
      closePops: closePops,
      destroy: function () {
        alive = false;
        if (g.KaskadStream) { g.KaskadStream.untrack(qrLive); }
        document.removeEventListener('click', onDoc, true);
        document.removeEventListener('keydown', onKey, true);
        if (g.removeEventListener) { g.removeEventListener('resize', edges); }
        document.removeEventListener('mousemove', onMove, true);
        try { document.removeEventListener('scroll', onScroll, true); } catch (e) { /* нет документа */ }
        INPUT.forEach(function (ev) { try { document.removeEventListener(ev, onInput, { capture: true }); } catch (e) { /* нет документа */ } });
        if (mq) { if (mq.removeEventListener) { mq.removeEventListener('change', onMq); } else if (mq.removeListener) { mq.removeListener(onMq); } }
        try { if (g.removeEventListener) { g.removeEventListener('resize', onResize); } } catch (e) { /* нет окна */ }
        try { if (ro) { ro.disconnect(); } } catch (e) { /* уже */ }
        Object.keys(qrCache).forEach(function (k) { try { qrCache[k].slides.stop(); } catch (e) { /* уже */ } });
        clearTimeout(tipTimer); clearTimeout(copyTimer);
      }
    };
  }

  g.KaskadGive = { model: model, mount: mount, t: t, platforms: PLATFORMS, devices: DEVICES, routers: ROUTERS, kinds: KINDS,
    isRouter: isRouter, titles: TITLES, triangle: triangle };
})(typeof window !== 'undefined' ? window : globalThis);
