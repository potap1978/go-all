/* Kaskad 3 — страница человека (/p/<ссылка>). Ванильный JS: без библиотек, без внешних адресов, без сборки HTML из текста —
   всё, что пришло с сервера (имена, заметки, ключи), попадает на страницу только через textContent и
   проверенные атрибуты. Тексты — window.KASKAD_USER_I18N (user-i18n-ru.js).

   Как это работает. Первый HTML ключей не содержит. Человек нажимает «Показать настройки» — настоящее нажатие
   (event.isTrusted: click из скрипта, например element.click() у сканера ссылок, не считается) — страница шлёт
   POST <адрес>/keys с одноразовым значением из <meta name="kaskad-nonce"> и рисует устройства строками с плашками
   протоколов; под списком — та же выдача, что в окне панели «Выдать настройки» (give.js, режим «person»: без кнопок
   администратора). Первым открыт протокол устройства системы посетителя (подсказка сервера по User-Agent, а у
   iPadOS, который называет себя Macintosh, — по maxTouchPoints). QR-коды — путь SVG с сервера. */
(function () {
  'use strict';

  var I18N = window.KASKAD_USER_I18N || {};
  // Язык страницы — тот, чей словарь подключил сервер (<html lang>: cookie посетителя kaskad_lang, иначе язык
  // сервера). Тексты выдачи сервер готовит на том же языке: заголовок X-Kaskad-Lang запроса ключей.
  var LANG = document.documentElement.getAttribute('lang') === 'en' ? 'en' : 'ru';
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var RELATED = {ios: ['macos'], macos: ['ios'], android: ['androidtv'], androidtv: ['android']};
  var FILE_URL = /^\/p\/[A-Za-z0-9_-]{43}\/file\/[A-Za-z0-9_-]{32}\/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  var FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
  var PATH_DATA = /^[MmHh0-9. ]+$/;

  var S = {nonce: '', os: 'other', data: null, busy: false, retries: 0, refreshed: false, action: 'reveal', sel: null};
  var elGate, elReveal, elStatus, elOut;

  // ── texts ────────────────────────────────────────────────────────────────

  function t(key, vars) {
    var s = I18N[key];
    if (typeof s !== 'string') { s = key; }
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, function (m, k) { return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m; });
    }
    return s;
  }
  function tn(group, key) {
    var g = I18N[group];
    return g && typeof g[key] === 'string' ? g[key] : key;
  }

  // ── DOM: only createElement / textContent / checked attributes ───────────

  function el(tag, props, kids) {
    var node = document.createElement(tag);
    var k;
    if (props) {
      for (k in props) {
        if (!Object.prototype.hasOwnProperty.call(props, k)) { continue; }
        var v = props[k];
        if (v === null || v === undefined || v === false) { continue; }
        if (k === 'class') { node.className = v; }
        else if (k === 'text') { node.textContent = v; }
        else if (k === 'on') { for (var ev in v) { if (Object.prototype.hasOwnProperty.call(v, ev)) { node.addEventListener(ev, v[ev]); } } }
        else if (/^on/i.test(k) || k === 'style' || k === 'srcdoc') { continue; }
        else { node.setAttribute(k, v === true ? '' : String(v)); }
      }
    }
    add(node, kids);
    return node;
  }
  function add(node, kids) {
    if (kids === null || kids === undefined || kids === false) { return node; }
    if (!Array.isArray(kids)) { kids = [kids]; }
    kids.forEach(function (c) {
      if (c === null || c === undefined || c === false) { return; }
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }
  function svgEl(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    for (var k in attrs) { if (Object.prototype.hasOwnProperty.call(attrs, k)) { node.setAttribute(k, String(attrs[k])); } }
    return node;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function meta(name) {
    var m = document.querySelector('meta[name="' + name + '"]');
    return m ? (m.getAttribute('content') || '') : '';
  }
  function base() { return location.pathname.replace(/\/+$/, ''); }
  // the prefix the page is under behind nginx (/app-…/p/<token> -> /app-…; /p/<token> -> ""): the server gives the
  // addresses of files from /p/
  function siteBase() { var p = location.pathname, i = p.lastIndexOf('/p/'); return i > 0 ? p.slice(0, i) : ''; }
  function setStatus(text, kind) {
    elStatus.textContent = text || '';
    if (kind) { elStatus.setAttribute('data-kind', kind); } else { elStatus.removeAttribute('data-kind'); }
  }

  // ── the visitor's system ─────────────────────────────────────────────────

  function detectOs() {
    var os = meta('kaskad-os') || 'other';
    // iPadOS 13+ shows a desktop User-Agent («Macintosh»); a Mac has no touch screen, an iPad does
    if (os === 'macos' && (navigator.maxTouchPoints || 0) > 1) { os = 'ios'; }
    return os;
  }
  function platformName(p) { return tn('platforms', p); }

  // ── QR codes: the path of an SVG comes from the server (qrencode), the page draws no code itself ──────────

  function qrNode(code, q) {
    if (!code || typeof code.d !== 'string' || !PATH_DATA.test(code.d)) { return null; }
    var n = code.n;
    if (typeof n !== 'number' || n % 1 !== 0 || n < 21 || n > 177) { return null; }
    var quiet = typeof q === 'number' && q >= 0 && q <= 4 ? q : 2, side = n + 2 * quiet;   // поле — как считает порог (FIT.quiet)
    var svg = svgEl('svg', {viewBox: (-quiet) + ' ' + (-quiet) + ' ' + side + ' ' + side, role: 'img',
                            'aria-label': t('qr_alt'), focusable: 'false', 'class': 'up-qr-svg'});
    svg.appendChild(svgEl('rect', {x: -quiet, y: -quiet, width: side, height: side, fill: '#ffffff'}));
    svg.appendChild(svgEl('path', {d: code.d, fill: 'none', stroke: '#000000', 'stroke-width': 1,
                                   'shape-rendering': 'crispEdges'}));
    return svg;
  }

  // the address of this page without the hash and the query (the token is in the path) and its QR, drawn here by
  // KaskadQR (qr.js — the same encoder as in the panel): a short link, a small code (3.3.3 №38)
  function pageUrl() {
    try { return /^https?:$/.test(location.protocol) ? location.origin + location.pathname : ''; } catch (e) { return ''; }
  }
  function localQr(text, quiet) {
    var Q = window.KaskadQR;
    if (!Q || !text) { return null; }
    try { return Q.svg(Q.encode(String(text)), '', quiet); } catch (e) { return null; }
  }

  // ── devices: the same «Выдать настройки» as the panel (give.js, viewer «person») ─────────────────────────
  // Строки устройств с плашками протоколов; нажатие на плашку открывает под списком те же шаги, что в панели:
  // 1 Приложение · 2 Ключ · 3 Включите VPN (у роутера — подготовьте, добавьте, проверьте). Кнопок администратора нет:
  // протокол не включён — «Попросите администратора включить …». Первым открыт протокол устройства системы посетителя.

  var DEV_ICON = {ios: 'smartphone', android: 'smartphone', androidtv: 'tv', windows: 'laptop', macos: 'laptop', linux: 'laptop', other: 'display'};
  function icon(name, size) {
    var I = window.KaskadIcons, s = null;
    try { s = I && I.svg ? I.svg(name, size || 18) : null; } catch (e) { s = null; }
    if (s) { s.setAttribute('aria-hidden', 'true'); }
    return s;
  }
  function initials(name) {
    var w = String(name || '').trim().split(/\s+/).filter(function (x) { return x; });
    var s = w.length > 1 ? w[0].charAt(0) + w[1].charAt(0) : (w[0] || '?').slice(0, 2);
    return s.toUpperCase();
  }
  // a file of the issuance: only the page's own address (/p/<token>/file/…), under the prefix of the site
  function safeItems(items) {
    return (Array.isArray(items) ? items : []).filter(function (it) { return it && typeof it === 'object'; }).map(function (it) {
      var o = {}, k;
      for (k in it) { if (Object.prototype.hasOwnProperty.call(it, k)) { o[k] = it[k]; } }
      if (typeof o.file === 'string' && FILE_URL.test(o.file) && typeof o.filename === 'string' && FILE_NAME.test(o.filename)) { o.file = siteBase() + o.file; }
      else { delete o.file; }
      return o;
    });
  }
  function safeInstall(b) {
    if (!b || typeof b !== 'object') { return null; }
    var apps = (Array.isArray(b.apps) ? b.apps : []).map(function (a) {
      var o = {}, k;
      for (k in a) { if (Object.prototype.hasOwnProperty.call(a, k)) { o[k] = a[k]; } }
      o.icon = typeof a.icon === 'string' && /^\/p\/apps\/[a-z0-9-]+\.png$/.test(a.icon) ? a.icon : '';   // адрес — iconBase
      return o;
    });
    return {supported: b.supported, builtin: b.builtin, text: b.text, apps: apps};
  }
  // one inbound of a device in the shape of the panel's issuance (ops.issue) for give.js
  function issueOf(dev, inb) {
    var s = inb.support && typeof inb.support === 'object' ? inb.support : null;
    return {kind: inb.kind, os: dev.platform || 'other', name: dev.name, items: safeItems(inb.items), howto: inb.howto,
            install: safeInstall(inb.install), support: s, alternatives: s && Array.isArray(s.alternatives) ? s.alternatives : [],
            available: (dev.inbounds || []).map(function (x) { return x.kind; })};
  }
  function pickFirst(devices) {
    var by = function (pred) { for (var i = 0; i < devices.length; i++) { if (devices[i].enabled !== false && (devices[i].inbounds || []).length && pred(devices[i])) { return devices[i]; } } return null; };
    var rel = RELATED[S.os] || [];
    return by(function (d) { return d.platform === S.os; }) || by(function (d) { return rel.indexOf(d.platform) >= 0; }) || by(function () { return true; });
  }

  var G = null;                                   // the mounted give.js of the open device
  function showInbound(holder, dev, kind) {
    var inbs = dev.inbounds || [], inb = null, i;
    for (i = 0; i < inbs.length; i++) { if (inbs[i].kind === kind) { inb = inbs[i]; } }
    inb = inb || inbs[0];
    S.sel = {dev: dev.id, kind: inb ? inb.kind : ''};
    Array.prototype.forEach.call(document.querySelectorAll('.up-dev'), function (row) {
      var on = row.getAttribute('data-dev') === dev.id;
      row.classList.toggle('is-on', on);
      Array.prototype.forEach.call(row.querySelectorAll('.gs-chip'), function (c) {
        var sel = on && c.getAttribute('data-kind') === S.sel.kind;
        c.classList.toggle('is-on', sel);
        c.setAttribute('aria-pressed', sel ? 'true' : 'false');
      });
    });
    if (G) { G.destroy(); G = null; }
    clear(holder);
    if (!inb) { return; }
    if (inb.error) { holder.appendChild(el('p', {class: 'up-error', text: t('inbound_error', {error: String(inb.error)})})); return; }
    if (!window.KaskadGive) { holder.appendChild(el('p', {class: 'up-error', text: t('failed')})); return; }
    G = window.KaskadGive.mount({
      viewer: 'person', lang: LANG, device: {name: dev.name}, iconBase: siteBase() + '/p/',   // /p/apps/<id>.png под префиксом сайта
      onProto: function (k) { showInbound(holder, dev, k); },
      drawQr: function (item, i, q) { return qrNode(Array.isArray(item.qr) ? item.qr[i] : null, q); },
      drawLinkQr: function (link) { return link ? (link.qr ? qrNode(link.qr) : localQr(link.url, link.quiet)) : null; },
      // 3.3.3 №38: код ключа плотнее, чем берёт камера, — «откройте эту страницу на телефоне» (QR её адреса)
      pageUrl: pageUrl()
    });
    holder.appendChild(G.el);
    G.set(issueOf(dev, inb));
  }

  function deviceRow(dev, holder) {
    var inbs = Array.isArray(dev.inbounds) ? dev.inbounds : [];
    var router = window.KaskadGive && window.KaskadGive.isRouter(dev.platform);
    var plat = platformName(dev.platform) ;
    var row = el('div', {class: 'up-dev', 'data-dev': dev.id}, [
      el('span', {class: 'up-dev__ic', 'aria-hidden': 'true'}, icon(router ? 'router' : DEV_ICON[dev.platform] || 'display', 20)),
      el('div', {class: 'up-dev__tx'}, [el('b', {class: 'up-dev__n', text: dev.name || ''}),
        el('span', {class: 'up-dev__p', text: router ? t('router_of', {b: plat}) : plat})])]);
    if (dev.enabled === false) {
      row.appendChild(el('span', {class: 'up-dev__off', text: t('device_off')}));
    } else if (!inbs.length) {
      row.appendChild(el('span', {class: 'up-dev__off', text: t('no_inbounds')}));
    } else {
      var G0 = window.KaskadGive;
      row.appendChild(el('div', {class: 'up-dev__protos', role: 'group', 'aria-label': t('protocols') + ': ' + (dev.name || '')}, inbs.map(function (inb) {
        // как в панели (SPEC п. 4, FIX-332g №5): платформа протокол не умеет — плашка приглушена и с подсказкой;
        // обходной путь — треугольник
        var sup = inb.support && typeof inb.support === 'object' ? inb.support : {};
        var lv = (sup.levels && typeof sup.levels === 'object' && sup.levels[inb.kind]) || sup.level || '';
        var name = inb.title || inb.kind || '';
        var tip = !G0 ? '' : lv === 'none' ? G0.t(LANG, 'chipNone', {os: plat, proto: name}) : lv === 'workaround' ? G0.t(LANG, 'chipWork') : '';
        var b = el('button', {type: 'button', class: 'gs-chip' + (lv === 'none' ? ' is-dim' : ''), 'data-kind': inb.kind, 'aria-pressed': 'false',
          title: tip || null, 'aria-label': tip ? name + ' — ' + tip : null,
          on: {click: function () { showInbound(holder, dev, inb.kind); }}}, [el('span', {text: name})]);
        if (lv === 'workaround' && G0 && G0.triangle) { b.appendChild(G0.triangle(15)); }
        return b;
      })));
    }
    return row;
  }

  function render() {
    var d = S.data, devices = Array.isArray(d.devices) ? d.devices : [];
    clear(elOut);
    var name = d.user && d.user.name ? String(d.user.name) : (document.getElementById('up-name') || {}).textContent || '';
    var card = el('section', {class: 'up-card up-pcard'});
    card.appendChild(el('div', {class: 'up-phead'}, [
      el('span', {class: 'up-ava', 'aria-hidden': 'true', text: initials(name)}),
      el('div', {class: 'up-phead__tx'}, [el('h2', {class: 'up-phead__t', text: t('title')}), el('p', {class: 'up-phead__n', text: name})]),
      el('button', {type: 'button', class: 'up-btn up-btn--ghost up-btn--sm', text: t('hide'), on: {click: hide}})]));
    if (d.gateway && d.gateway.enabled === false) { card.appendChild(el('p', {class: 'up-warn', text: t('gateway_off')})); }
    var holder = el('div', {class: 'up-give'});
    if (!devices.length) {
      card.appendChild(el('p', {class: 'up-empty', text: t('no_devices')}));
    } else {
      card.appendChild(el('p', {class: 'up-label', text: t('devices')}));
      card.appendChild(el('div', {class: 'up-devs'}, devices.map(function (dev) { return deviceRow(dev, holder); })));
      card.appendChild(el('hr', {class: 'gs-sep up-sep'}));
      card.appendChild(holder);
    }
    card.appendChild(el('p', {class: 'up-foot'}, [icon('lock', 18), el('span', {text: t('personal')})]));
    elOut.appendChild(card);
    elGate.hidden = true;
    elOut.hidden = false;
    var first = pickFirst(devices);
    if (first) { showInbound(holder, first, (first.inbounds[0] || {}).kind); }
    try { elOut.focus({preventScroll: true}); } catch (e) { /* old browsers */ }
  }

  function hide() {
    if (G) { G.destroy(); G = null; }
    S.data = null;
    S.busy = false;
    clear(elOut);
    elOut.hidden = true;
    elGate.hidden = false;
    setStatus('');
    S.action = 'reveal';
    elReveal.textContent = t('reveal');
    elReveal.disabled = false;
  }

  // ── asking the server for the settings ───────────────────────────────────

  function fail(text, action) {
    S.busy = false;
    setStatus(text, 'error');
    S.action = action || 'none';
    if (action === 'retry') { elReveal.textContent = t('retry'); elReveal.disabled = false; }
    else if (action === 'reload') { elReveal.textContent = t('reload'); elReveal.disabled = false; }
    else { elReveal.hidden = true; }
  }

  function showPaused() {
    clear(elGate);
    elGate.appendChild(el('h2', {class: 'up-dev-name', text: t('paused_title')}));
    elGate.appendChild(el('p', {class: 'up-lead', text: t('paused_text')}));
  }

  // Ссылка ничего не открывает (её заменили, выключили или скопировали не целиком): сервер отдаёт одну и ту же страницу
  // для любой такой ссылки (meta kaskad-state = gone) — без имени и без кнопки, только что делать дальше
  function showGone() {
    clear(elGate);
    var who = document.querySelector('.up-who');
    if (who) { who.hidden = true; }
    elGate.appendChild(el('h2', {class: 'up-dev-name', text: t('gone_title')}));
    elGate.appendChild(el('p', {class: 'up-lead', text: t('gone_text')}));
  }

  function refreshNonce() {
    return fetch(base(), {credentials: 'same-origin', cache: 'no-store'}).then(function (r) {
      if (!r.ok) { throw new Error('http'); }
      return r.text();
    }).then(function (txt) {
      var doc = new DOMParser().parseFromString(txt, 'text/html');   // inert: nothing in it runs
      var m = doc.querySelector('meta[name="kaskad-nonce"]');
      var n = m ? m.getAttribute('content') : '';
      if (!n) { throw new Error('nonce'); }
      S.nonce = n;
    });
  }

  function onAnswer(res) {
    var js = res.js || {};
    if (res.status === 200 && js.ok) {
      S.nonce = js.nonce || '';
      S.data = js;
      S.busy = false;
      setStatus('');
      render();
      return;
    }
    if (res.status === 404) { fail(t('gone'), 'none'); return; }
    if (res.status === 403 && js.reason === 'paused') { showPaused(); return; }
    if (res.status === 409 && js.reason === 'reveal') {
      if (!S.refreshed) {                       // the value is spent or old: take a fresh one from the shell, once
        S.refreshed = true;
        refreshNonce().then(requestKeys, function () { fail(t('stale'), 'reload'); });
      } else {
        fail(t('stale'), 'reload');
      }
      return;
    }
    if (res.status === 503 && S.retries < 3) {
      S.retries += 1;
      if (js.nonce) { S.nonce = js.nonce; }
      setStatus(t('busy'), 'info');
      setTimeout(requestKeys, (js.retry_after > 0 && js.retry_after <= 30 ? js.retry_after : 3) * 1000);
      return;
    }
    fail(t('failed'), 'retry');
  }

  function requestKeys() {
    fetch(base() + '/keys', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Kaskad-Lang': LANG}, body: JSON.stringify({nonce: S.nonce})
    }).then(function (r) {
      return r.text().then(function (txt) {
        var js = null;
        try { js = JSON.parse(txt); } catch (e) { js = null; }
        return {status: r.status, js: js};
      });
    }).then(onAnswer, function () { fail(t('failed'), 'retry'); });
  }

  // A real tap, click or key press on the button: an event made by a script (isTrusted === false) is not a person.
  function onReveal(e) {
    if (e && e.isTrusted === false) { return; }
    if (S.action === 'reload') { location.reload(); return; }
    if (S.busy) { return; }
    S.busy = true;
    S.retries = 0;
    S.refreshed = false;
    elReveal.disabled = true;
    setStatus(t('loading'), 'info');
    requestKeys();
  }

  // ── theme: dark by default (the owner, 3.3.2), light — a button in the header ─────────────────────────────
  // For this visit only: the page leaves nothing in the browser (no storage, no cookies — the rule of the page).

  var theme = 'dark';
  function themeIcon(th) {
    var svg = svgEl('svg', {viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false'});
    if (th === 'light') {                                   // the sun: the button turns the light theme on
      svg.appendChild(svgEl('circle', {cx: 12, cy: 12, r: 4}));
      svg.appendChild(svgEl('path', {d: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'}));
    } else {                                                // the moon: back to the dark one
      svg.appendChild(svgEl('path', {d: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z'}));
    }
    return svg;
  }
  function themeButton() {
    var head = document.querySelector('.up-header');
    if (!head) { return; }
    var btn = el('button', {type: 'button', 'class': 'up-theme'});
    var draw = function () {
      var next = theme === 'light' ? 'dark' : 'light';
      btn.setAttribute('aria-label', t(next === 'light' ? 'theme_light' : 'theme_dark'));
      btn.setAttribute('title', t(next === 'light' ? 'theme_light' : 'theme_dark'));
      clear(btn);
      btn.appendChild(themeIcon(next));
    };
    btn.addEventListener('click', function () {
      theme = theme === 'light' ? 'dark' : 'light';
      if (theme === 'light') { document.documentElement.setAttribute('data-theme', 'light'); }
      else { document.documentElement.removeAttribute('data-theme'); }
      draw();
    });
    draw();
    head.appendChild(btn);
  }

  // Свой язык посетителя: RU · EN в шапке — ссылки «?lang=…»: сервер запоминает выбор в cookie страницы и отдаёт её
  // на этом языке (скрипт cookie и хранилищ не трогает). Здесь — подписи и отметка текущего.
  function langSwitch() {
    var box = document.getElementById('up-lang');
    if (!box) { return; }
    if (meta('kaskad-state') === 'gone') { box.parentNode.removeChild(box); return; }   // ссылки нет — и выбирать нечего
    box.setAttribute('aria-label', t('lang'));
    Array.prototype.forEach.call(box.querySelectorAll('a[hreflang]'), function (a) {
      var v = a.getAttribute('hreflang');
      a.setAttribute('title', t(v === 'en' ? 'lang_en' : 'lang_ru'));
      if (v === LANG) { a.setAttribute('aria-current', 'true'); }
    });
  }

  function init() {
    themeButton();
    elGate = document.getElementById('up-gate');
    elReveal = document.getElementById('up-reveal');
    elStatus = document.getElementById('up-status');
    elOut = document.getElementById('up-out');
    if (!elGate || !elReveal || !elStatus || !elOut) { return; }
    Array.prototype.forEach.call(document.querySelectorAll('[data-t]'), function (n) {
      n.textContent = t(n.getAttribute('data-t'));
    });
    document.title = t('title');
    var ava = document.getElementById('up-ava'), nm = document.getElementById('up-name');
    if (ava && nm && meta('kaskad-state') !== 'gone') { ava.textContent = nm.textContent.trim() ? initials(nm.textContent) : ''; }
    langSwitch();
    S.nonce = meta('kaskad-nonce');
    S.os = detectOs();
    if (meta('kaskad-state') === 'paused') { showPaused(); return; }
    if (meta('kaskad-state') === 'gone') { showGone(); return; }
    if (!window.fetch || !window.DOMParser) { fail(t('old_browser'), 'none'); return; }
    elReveal.addEventListener('click', onReveal);
  }

  init();
})();
