/* Kaskad — показ QR-кодов выданных настроек: одна рамка, подпись «для какого приложения и в каком формате», а у
   ключа из нескольких частей (AmneziaWG 3.1: vpn:// не влезает в один код) — части по очереди в той же рамке
   (3.3.2 №8, №10, решение владельца): «Часть 1 из 2» под рамкой, смена сама раз в 2 с, без кнопок и без паузы (под
   курсором и при prefers-reduced-motion тоже — это смена для сканирования). Сами данные частей не меняются: приложение Amnezia
   собирает ключ из частей в любом порядке (vendor/awgkeys.qr_chunks).

   Один компонент на мастер (setup.js), панель (app.js) и страницу человека (user.js). Как нарисовать код, решает
   страница (draw: KaskadQR.svg у мастера и панели, готовый путь SVG от сервера у страницы человека); тексты — из
   словаря страницы (texts), сам файл слов не держит. ES5, без внешних адресов и без HTML из текста.

   API:
     KaskadQRSlides.make({ count, draw(i) -> Node, label, sub?, texts: { part }, interval?, onShow?(i), cls? })
                           -> { el, go(i), stop(), current() }
     KaskadQRSlides.kind(text) -> "vpnpart" | "vpn" | "vless" | "hy2" | "awgconf" | "wgconf" | "url" | "text"
         для какого приложения код: по его тексту (то же правило — kaskad.qr.qr_for на сервере)
     KaskadQRSlides.part(text) -> { n, i } | null — заголовок части Amnezia (magic 1984, число частей, номер)
     KaskadQRSlides.group(items) -> { groups: [{ app, qr, extra }], rest } — записи выдачи по приложениям
     KaskadQRSlides.fit(item, {frame, minPx}?) -> { ver, px, ok, max } — возьмёт ли камера коды записи qr с экрана
         (3.3.3 №38: версия ≤ maxVersionFor(рамка, порог)); frameAt(ширина окна телефона) — CSS px внутри рамки
     KaskadQRSlides.versionOf(text | {n}) -> версия кода (как его нарисуют KaskadQR.encode и /p/), 0 — не влезет
     KaskadQRSlides.hintId(app, platform) -> id подсказки «что поставить и нажать» ("" — своей нет)
     KaskadQRSlides.switcher({ groups, label, build, hint?, pick?, aria? }) -> { el, pick(i), current() } — одна рамка на
         выдачу: сегменты «DefaultVPN / AmneziaVPN · AmneziaWG · WireGuard» над ней (у одного приложения — без них) */
(function (g) {
  'use strict';

  var INTERVAL = 2000;                                  // мс: часть ключа раз в 2 секунды (решение владельца)
  var QR_MAGIC = 1984;                                   // vendor/awgkeys.QR_MAGIC: заголовок части «>hBBI»
  var B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';

  function b64head(s, nbytes) {                          // первые байты base64url без разбора всей строки
    var out = [], bits = 0, acc = 0, i, v;
    for (i = 0; i < s.length && out.length < nbytes; i++) {
      v = B64.indexOf(s.charAt(i) === '+' ? '-' : s.charAt(i) === '/' ? '_' : s.charAt(i));
      if (v < 0) { return null; }
      acc = (acc << 6) | v; bits += 6;
      if (bits >= 8) { bits -= 8; out.push((acc >> bits) & 255); }
    }
    return out.length === nbytes ? out : null;
  }
  function part(text) {
    var s = String(text || '').replace(/^\s+|\s+$/g, '');
    if (s.length < 12 || !/^[A-Za-z0-9_-]+=*$/.test(s)) { return null; }
    var b = b64head(s, 4);
    if (!b || ((b[0] << 8) | b[1]) !== QR_MAGIC || !b[2] || b[3] >= b[2]) { return null; }
    return { n: b[2], i: b[3] };
  }
  function kind(text) {
    var s = String(text || '').replace(/^\s+|\s+$/g, '');
    if (/^vpn:\/\//i.test(s)) { return 'vpn'; }
    if (/^vless:\/\//i.test(s)) { return 'vless'; }
    if (/^(hysteria2|hy2):\/\//i.test(s)) { return 'hy2'; }
    if (/^\[Interface\]/m.test(s)) {
      return /^\s*(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5])\s*=/m.test(s) ? 'awgconf' : 'wgconf';
    }
    if (/^https?:\/\//i.test(s)) { return 'url'; }
    if (part(s)) { return 'vpnpart'; }
    return 'text';
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }
  function fill(s, vars) {
    return String(s || '').replace(/\{(\w+)\}/g, function (m, k) { return vars[k] === undefined ? m : String(vars[k]); });
  }
  function reduced() {
    try { return g.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }

  function make(o) {
    o = o || {};
    var n = Math.max(1, o.count | 0), tx = o.texts || {}, every = o.interval || INTERVAL;
    var root = el('figure', 'kq' + (o.cls ? ' ' + o.cls : ''));
    var frame = el('div', 'kq__frame');
    var cap = el('figcaption', 'kq__cap');
    var parts = [], cur = 0, timer = 0, seen = false;
    root.setAttribute('data-n', String(n));
    if (o.label) { root.setAttribute('aria-label', o.label); }
    cap.appendChild(el('b', 'kq__app', o.label || ''));
    if (o.sub) { cap.appendChild(el('span', 'kq__sub', o.sub)); }
    root.appendChild(frame);
    root.appendChild(cap);

    var of = null;
    function show(i) {
      var k;
      cur = ((i % n) + n) % n;
      if (!parts[cur]) {
        parts[cur] = el('div', 'kq__part');
        var pic = null;
        try { pic = o.draw ? o.draw(cur) : null; } catch (e) { pic = null; }
        if (pic) { parts[cur].appendChild(pic); }
        frame.appendChild(parts[cur]);
      }
      for (k = 0; k < parts.length; k++) { if (parts[k]) { parts[k].hidden = k !== cur; } }
      if (of) { of.textContent = fill(tx.part || '{i} / {n}', { i: cur + 1, n: n }); }
      if (o.onShow) { try { o.onShow(cur); } catch (e) { /* подпись страницы — не повод ломать показ */ } }
    }
    // Смена частей — функциональная (камера телефона собирает ключ из всех частей), не украшение: всегда, и при
    // prefers-reduced-motion, и под курсором мыши (иначе с компьютера камера застревает на одной части). Скрытая
    // вкладка — без смены; рамка ушла со страницы — таймер снят.
    function tick() {
      if (root.isConnected) { seen = true; } else if (seen) { stop(); return; } else { return; }
      if (!(g.document && g.document.hidden)) { show(cur + 1); }
    }
    function stop() { if (timer) { clearInterval(timer); timer = 0; } }
    if (n > 1) {
      of = el('span', 'kq__of');
      of.setAttribute('aria-live', 'off');
      cap.appendChild(of);
      root.setAttribute('data-auto', 'on');
      timer = setInterval(tick, every);
    }
    show(0);
    return {
      el: root,
      go: function (i) { show(i); },
      stop: stop,
      current: function () { return cur; }
    };
  }

  // ── Одна рамка на выдачу: переключатель приложения над ней (3.3.2, координатор: «QR рядом не показывать») ──
  // Записи выдачи (vpn, qr, conf, link…) раскладываются по приложениям: у каждого — свой код и то, что к нему
  // (ссылка vpn:// с «Скопировать», файл .conf со «Скачать»). Первым — приложение первого кода выдачи: сервер ставит
  // первым рекомендованное для платформы (issue_os: iPhone → AmneziaVPN/DefaultVPN).
  var APP = { vpnpart: 'amnezia', vpn: 'amnezia', awgconf: 'awg', wgconf: 'wg', vless: 'vless', hy2: 'hy2', url: 'url' };
  var OTHER = { fields: 1, guide: 1, ovpn: 1, mobileconfig: 1, ps1: 1 };
  function appOf(it) {
    if (!it || !it.format || OTHER[it.format]) { return ''; }
    var k = it.qr_for || kind(Array.isArray(it.body) ? it.body[0] : it.body);
    return APP[k] || '';
  }
  // items -> { groups: [{ app, qr, extra: [] }] — по коду на приложение, в порядке выдачи; rest: остальное }
  function group(items) {
    var groups = [], rest = [], by = {}, list = Array.isArray(items) ? items : [];
    list.forEach(function (it) {
      if (!it || it.format !== 'qr') { return; }
      var a = appOf(it) || 'qr' + groups.length;
      if (by[a]) { rest.push(it); return; }
      by[a] = { app: a, qr: it, extra: [] };
      groups.push(by[a]);
    });
    list.forEach(function (it) {
      if (!it || it.format === 'qr') { return; }
      var a = appOf(it);
      if (a && by[a]) { by[a].extra.push(it); } else { rest.push(it); }
    });
    return { groups: groups, rest: rest };
  }
  // switcher({ groups, label(group) -> text, build(group) -> Node, pick?, aria?, cls? }) -> { el, pick(i), current() }
  function switcher(o) {
    var groups = o.groups || [], cur = -1, bodies = [], btns = [];
    var root = el('div', 'kq-apps' + (o.cls ? ' ' + o.cls : ''));
    var seg = el('div', 'kq-apps__seg');
    var body = el('div', 'kq-apps__body');
    // подсказка «что поставить и что нажать» — одной строкой над переключателем, у каждого приложения своя
    var hint = o.hint ? el('p', 'kq-apps__hint') : null;
    seg.setAttribute('role', 'group');
    if (o.aria) { seg.setAttribute('aria-label', o.aria); }
    function pick(i) {
      var k;
      i = Math.max(0, Math.min(groups.length - 1, i | 0));
      if (i === cur) { return; }
      cur = i;
      if (!bodies[i]) {
        bodies[i] = el('div', 'kq-apps__one');
        var n = null;
        try { n = o.build ? o.build(groups[i], i) : null; } catch (e) { n = null; }
        if (n) { bodies[i].appendChild(n); }
        body.appendChild(bodies[i]);
      }
      for (k = 0; k < bodies.length; k++) { if (bodies[k]) { bodies[k].hidden = k !== i; } }
      for (k = 0; k < btns.length; k++) { btns[k].setAttribute('aria-pressed', k === i ? 'true' : 'false'); }
      if (hint) {
        var txt = '';
        try { txt = o.hint(groups[i], i) || ''; } catch (e) { txt = ''; }
        hint.textContent = txt;
        hint.hidden = !txt;
      }
      if (o.onPick) { try { o.onPick(i, groups[i]); } catch (e) { /* подпись страницы */ } }
    }
    if (hint) { root.appendChild(hint); }
    if (groups.length > 1) {
      groups.forEach(function (gr, i) {
        var b = el('button', 'kq-apps__btn', o.label ? o.label(gr) : gr.app);
        b.type = 'button';
        b.setAttribute('data-app', gr.app);
        b.addEventListener('click', function () { pick(i); });
        btns.push(b);
        seg.appendChild(b);
      });
      root.appendChild(seg);
    }
    root.appendChild(body);
    pick(o.pick || 0);
    return { el: root, pick: pick, current: function () { return cur; } };
  }

  // ── Возьмёт ли камера телефона код с экрана (3.3.3 №38) ──────────────────────────────────────────────────────
  // Код ключа рисуется в белой рамке окна (give.css .gs-qr__frame, pad белого с каждой стороны) с полем quiet модулей.
  // Рамка — по порогу модуля (владелец, 3.3.3: «не более 2 частей», стандартное приложение AmneziaVPN ждёт 2):
  // компьютер — сторона ⌈minPx × модулей⌉ между frame (312 снаружи) и frameMax (maxOuter снаружи); телефон — вся
  // ширина: min(maxOuter, 100vw − gutter) без pad. Порог — физический размер модуля (≈4–5 пикселей кадра камеры, которая
  // не фокусируется ближе 15–20 см): компьютер — minPx (4, правило владельца), телефон — minPxPhone. Код выше
  // maxVersionFor(рамка, порог) не показывается как способ взять ключ. Части ключа AmneziaVPN — не больше maxParts.
  // Числа и правило уровня — те же, что kaskad.qr (tests/test_333q.py сверяет), таблица — ёмкость байтового режима
  // при «L», версии 1–40.
  var FIT = { outer: 312, maxOuter: 436, pad: 8, gutter: 72, frame: 296, frameMax: 420, quiet: 2, minPx: 4, minPxPhone: 3.6,
    maxVersion: 21, maxParts: 2 };
  var CAP_L = [17, 32, 53, 78, 106, 134, 154, 192, 230, 271, 321, 367, 425, 458, 520, 586, 644, 718, 792, 858, 929,
    1003, 1091, 1171, 1273, 1367, 1465, 1528, 1628, 1732, 1840, 1952, 2068, 2188, 2303, 2431, 2563, 2699, 2809, 2953];
  function utf8len(s) {
    var n = 0, i, c;
    s = String(s);
    for (i = 0; i < s.length; i++) {
      c = s.charCodeAt(i);
      if (c < 0x80) { n += 1; } else if (c < 0x800) { n += 2; } else if (c >= 0xd800 && c < 0xdc00) { n += 4; i++; } else { n += 3; }
    }
    return n;
  }
  // версия кода: по готовой матрице ({n} — сторона в модулях, у /p/ её рисует сервер) или по тексту; 0 — не влезет в 40
  function versionOf(x) {
    var v;
    if (x && typeof x === 'object' && typeof x.n === 'number') { return x.n >= 21 && (x.n - 17) % 4 === 0 ? (x.n - 17) / 4 : 0; }
    var n = utf8len(x == null ? '' : x);
    for (v = 1; v <= 40; v++) { if (CAP_L[v - 1] >= n) { return v; } }
    return 0;
  }
  function modulePx(v, frame) { return (frame || FIT.frameMax) / (4 * v + 17 + 2 * FIT.quiet); }
  // наибольшая сторона рамки внутри, CSS px: компьютер (viewport не задан) — FIT.frameMax, телефон шириной viewport —
  // как @media в give.css
  function frameAt(viewport) {
    if (typeof viewport !== 'number' || !(viewport > 0)) { return FIT.frameMax; }
    return Math.max(0, Math.min(FIT.maxOuter, viewport - FIT.gutter) - 2 * FIT.pad);
  }
  // сторона рамки внутри для кода версии v: телефон — вся ширина (cap), компьютер — по порогу между FIT.frame и cap
  function sideFor(v, cap, minPx, phone) {
    cap = typeof cap === 'number' ? cap : FIT.frameMax;
    if (phone) { return cap; }
    var need = v ? Math.ceil((minPx || FIT.minPx) * (4 * v + 17 + 2 * FIT.quiet) - 1e-9) : FIT.frame;
    return Math.max(Math.min(FIT.frame, cap), Math.min(cap, need));
  }
  // наибольшая версия с модулем ≥ minPx в рамке frame; 0 — никакая
  function maxVersionFor(frame, minPx) {
    var v;
    for (v = 40; v >= 1; v--) { if (modulePx(v, frame) >= (minPx || FIT.minPx)) { return v; } }
    return 0;
  }
  // записи qr выдачи: версия самого плотного кода ({ver, px, ok, max, side, n}); side — сторона рамки внутри, в которой
  // его покажут (opts.frame — наибольшая рамка сейчас, opts.phone — телефон: рамка во всю ширину), px — модуль в ней;
  // ok — камера возьмёт каждый код при пороге opts.minPx (по умолчанию — рамка и порог компьютера)
  function fit(item, opts) {
    opts = opts || {};
    var cap = typeof opts.frame === 'number' ? opts.frame : FIT.frameMax, minPx = opts.minPx || FIT.minPx;
    var max = maxVersionFor(cap, minPx);
    var codes = !item ? [] : Array.isArray(item.qr) && item.qr.length ? item.qr : Array.isArray(item.texts) ? item.texts :
      Array.isArray(item.body) ? item.body : [item.body];
    var worst = 0, i, v;
    for (i = 0; i < codes.length; i++) {
      v = versionOf(codes[i]);
      if (!v) { return { ver: 0, px: 0, ok: false, max: max, side: cap, n: codes.length }; }
      if (v > worst) { worst = v; }
    }
    var side = sideFor(worst, cap, minPx, !!opts.phone);
    return { ver: worst, px: worst ? modulePx(worst, side) : 0, ok: worst > 0 && worst <= max, max: max, side: side, n: codes.length };
  }

  // какая подсказка у приложения на платформе: id для словаря страницы ("" — нет своей)
  function hintId(app, platform) {
    if (app === 'amnezia') { return platform === 'ios' || platform === 'android' ? 'amnezia_' + platform : 'amnezia'; }
    return { awg: 'awg', wg: 'wg', vless: 'vless', url: 'url' }[app] || '';
  }

  g.KaskadQRSlides = { make: make, hintId: hintId, fit: fit, versionOf: versionOf, modulePx: modulePx, frameAt: frameAt, sideFor: sideFor, maxVersionFor: maxVersionFor, FIT: FIT, kind: kind, part: part, appOf: appOf, group: group, switcher: switcher, INTERVAL: INTERVAL };
})(typeof window !== 'undefined' ? window : globalThis);
