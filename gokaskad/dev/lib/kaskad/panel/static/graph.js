/* Kaskad 3 · раздел «Схема» (graph.js, WP-P3): люди → устройства → входы → правила → пул → выходы.
   Ванильный JS без зависимостей и без сборки: window.KaskadGraph = {mount(el, ctx), unmount()},
   ctx = {api(method, path, body) -> Promise, onLive(fn), t(key)} (docs/k3/SPEC-wave3.md §3.5, §9.3).
   Пути API передаются полностью ('/api/scheme'); если ctx.api сам добавляет префикс — ctx.apiPrefix = ''.

   Основа — прототип hub-plan/src/proto.js: граф, жесты, связи, подсказки canConnect, отмена, «Применить»
   с диалогом, «Куда пойдёт», раскладка по колонкам (барицентр + перестановки), клавиатура. Иначе, чем в
   прототипе: граф и проверки — на сервере (GET /api/scheme, POST /api/scheme/apply с dry_run), живые цифры —
   из события live (без таймеров и случайных данных), SVG связей не пересобирается на каждый тик (цифры,
   толщина и классы меняются на месте), бесконечных CSS-анимаций нет, DOM — только createElement и
   textContent, тексты — ключи ctx.t(...) со словарём graph-i18n-ru.js.

   Иконки: часть Gravity UI Icons (MIT) и свои рисунки прототипа (роутер, правила, пул, планшет, ТВ, ноутбук). */
(function () {
  'use strict';

  var SCRIPT_SRC = (document.currentScript && document.currentScript.src) ||
    ((document.querySelector('script[src*="graph.js"]') || {}).src) || '';
  var SVGNS = 'http://www.w3.org/2000/svg';
  var API = '/api';
  var NARROW = 680;                 // уже — список по этапам, холст только для просмотра
  var GAP_X = 88, GAP_Y = 26;       // раскладка по колонкам
  var ZMAX = 1.8;
  var READABLE = 0.75;              // первый вид: вписать, но не мельче — подписи блоков читаются (T-G1b, N10)
  var PHONE_K = 0.5;                // телефон: подсветка не мельче этого — читается, остальное — прокруткой
  var MIN_TRACE_K = 0.55;           // широкий экран: подсветка с панелью не мельче этого — подписи читаются
  var SAVE_DELAY = 900;             // мс после последнего перетаскивания — сохранить раскладку
  var REFETCH_DELAY = 1500;
  var LIVE_STALE = 60;              // с без свежих данных — цифры «—»
  var HIST_MAX = 80;
  var DEV_ICON = { phone: 'smartphone', tablet: 'tablet', laptop: 'laptop', pc: 'display', tv: 'tv', router: 'router', other: 'gadget' };
  var LAYER = { user: 0, inbound: 1, filter: 2, rules: 3, pool: 4, direct: 4, block: 4, exit: 5, ru_group: 4 };
  var TARGET = { pool: 1, exit: 1, direct: 1, block: 1 };
  var OWN = Object.prototype.hasOwnProperty;

  /* ── Иконки ─────────────────────────────────────────────────────────── */
  var GI = {
    persons: 'M5.5 6a1.5 1.5 0 1 0 0-3a1.5 1.5 0 0 0 0 3m0 1.5a3 3 0 1 0 0-6a3 3 0 0 0 0 6m-3.029 2.886c-.777.54-.971 1.063-.971 1.306c0 .446.362.808.808.808h6.384a.81.81 0 0 0 .808-.808c0-.244-.194-.767-.971-1.306C7.792 9.875 6.719 9.5 5.5 9.5s-2.292.375-3.029.886M0 11.692C0 9.846 2.475 8 5.5 8c1.18 0 2.278.281 3.177.734A5.67 5.67 0 0 1 11.5 8c2.475 0 4.5 1.538 4.5 3.077A1.923 1.923 0 0 1 14.077 13h-3.483c-.416.604-1.113 1-1.902 1H2.308A2.31 2.31 0 0 1 0 11.692m10.991-.192h3.086c.234 0 .423-.19.423-.423c0-.103-.096-.472-.688-.89c-.554-.393-1.375-.687-2.312-.687c-.517 0-.999.09-1.42.236c.526.534.854 1.146.911 1.764M12.5 5a1 1 0 1 1-2 0a1 1 0 0 1 2 0M14 5a2.5 2.5 0 1 1-5 0a2.5 2.5 0 0 1 5 0',
    smartphone: 'M12 3.5v9a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 4 12.5v-9A1.5 1.5 0 0 1 5.5 2h5A1.5 1.5 0 0 1 12 3.5m-1.5-3a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-5a3 3 0 0 1-3-3v-9a3 3 0 0 1 3-3zM6.25 11a.75.75 0 0 0 0 1.5h3.5a.75.75 0 0 0 0-1.5z',
    display: 'M12 3H4a1.5 1.5 0 0 0-1.5 1.5v4A1.5 1.5 0 0 0 4 10h8a1.5 1.5 0 0 0 1.5-1.5v-4A1.5 1.5 0 0 0 12 3M4 1.5a3 3 0 0 0-3 3v4a3 3 0 0 0 3 3h3.25V13h-2.5a.75.75 0 0 0 0 1.5h6.5a.75.75 0 0 0 0-1.5h-2.5v-1.5H12a3 3 0 0 0 3-3v-4a3 3 0 0 0-3-3z',
    plug: 'M15.53 1.53A.75.75 0 0 0 14.47.47l-1.29 1.29a4.24 4.24 0 0 0-5.423.483l-.58.58a.96.96 0 0 0 0 1.354l4.646 4.646a.96.96 0 0 0 1.354 0l.58-.58a4.24 4.24 0 0 0 .484-5.423zm-8.5 4.94a.75.75 0 0 1 0 1.06L5.78 8.78l1.44 1.44 1.25-1.25a.75.75 0 0 1 1.06 1.06l-1.25 1.25.543.543a.96.96 0 0 1 0 1.354l-.58.58a4.24 4.24 0 0 1-5.423.484l-1.29 1.29A.75.75 0 0 1 .47 14.47l1.29-1.29a4.24 4.24 0 0 1 .483-5.423l.58-.58a.96.96 0 0 1 1.354 0l.543.543 1.25-1.25a.75.75 0 0 1 1.06 0M3.5 8.62l-.197.197a2.743 2.743 0 0 0 3.879 3.879l.197-.197zm9.197-1.439-.197.197L8.621 3.5l.197-.197a2.743 2.743 0 0 1 3.879 3.879',
    globe: 'M9.208 12.346c-.485 1-.953 1.154-1.208 1.154s-.723-.154-1.208-1.154c-.372-.768-.647-1.858-.749-3.187a21 21 0 0 0 3.914 0c-.102 1.329-.377 2.419-.75 3.187m.788-4.699C9.358 7.714 8.69 7.75 8 7.75s-1.358-.036-1.996-.103c.037-1.696.343-3.075.788-3.993C7.277 2.654 7.745 2.5 8 2.5s.723.154 1.208 1.154c.445.918.75 2.297.788 3.993m1.478 1.306c-.085 1.516-.375 2.848-.836 3.874a5.5 5.5 0 0 0 2.843-4.364c-.621.199-1.295.364-2.007.49m1.918-2.043c-.572.204-1.21.379-1.901.514-.056-1.671-.354-3.14-.853-4.251a5.5 5.5 0 0 1 2.754 3.737m-8.883.514c.056-1.671.354-3.14.853-4.251A5.5 5.5 0 0 0 2.608 6.91c.572.204 1.21.379 1.901.514M2.52 8.463a5.5 5.5 0 0 0 2.843 4.364c-.46-1.026-.75-2.358-.836-3.874a15.5 15.5 0 0 1-2.007-.49M15 8A7 7 0 1 0 1 8a7 7 0 0 0 14 0',
    house: 'M12.5 12.618c.307-.275.5-.674.5-1.118V6.977a1.5 1.5 0 0 0-.585-1.189l-3.5-2.692a1.5 1.5 0 0 0-1.83 0l-3.5 2.692A1.5 1.5 0 0 0 3 6.978V11.5A1.496 1.496 0 0 0 4.493 13H5V9.5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2V13h.507c.381-.002.73-.146.993-.382m2-1.118a3 3 0 0 1-3 3h-7a3 3 0 0 1-3-3V6.977A3 3 0 0 1 2.67 4.6l3.5-2.692a3 3 0 0 1 3.66 0l3.5 2.692a3 3 0 0 1 1.17 2.378zm-5-2A.5.5 0 0 0 9 9H7a.5.5 0 0 0-.5.5V13h3z',
    ban: 'M11.323 12.383a5.5 5.5 0 0 1-7.706-7.706zm1.06-1.06L4.677 3.617a5.5 5.5 0 0 1 7.706 7.706M15 8A7 7 0 1 1 1 8a7 7 0 0 1 14 0',
    magnifier: 'M11.5 7a4.5 4.5 0 1 1-9 0a4.5 4.5 0 0 1 9 0m-.82 4.74a6 6 0 1 1 1.06-1.06l2.79 2.79a.75.75 0 1 1-1.06 1.06z',
    undo: 'M8 1.5a6.5 6.5 0 1 1-6.445 7.348a.75.75 0 1 1 1.487-.194A5.001 5.001 0 1 0 4.43 4.5h1.32a.75.75 0 0 1 0 1.5h-3A.75.75 0 0 1 2 5.25v-3a.75.75 0 0 1 1.5 0v1.06A6.48 6.48 0 0 1 8 1.5',
    sliders: 'M7.5 5a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0m1.405.75a3.001 3.001 0 0 1-5.81 0H1.747a.75.75 0 0 1 0-1.5h1.348a3.001 3.001 0 0 1 5.81 0h5.345a.75.75 0 0 1 0 1.5zm-7.158 4.5h5.345a3.001 3.001 0 0 1 5.811 0h1.347a.75.75 0 1 1 0 1.5h-1.347a3.001 3.001 0 0 1-5.81 0H1.746a.75.75 0 0 1 0-1.5m8.25-.75a1.5 1.5 0 1 0 0 3a1.5 1.5 0 0 0 0-3',
    plus: 'M8 1.75a.75.75 0 0 1 .75.75v4.75h4.75a.75.75 0 0 1 0 1.5H8.75v4.75a.75.75 0 0 1-1.5 0V8.75H2.5a.75.75 0 0 1 0-1.5h4.75V2.5A.75.75 0 0 1 8 1.75',
    minus: 'M1.75 8a.75.75 0 0 1 .75-.75h11a.75.75 0 0 1 0 1.5h-11A.75.75 0 0 1 1.75 8',
    xmark: 'M3.47 3.47a.75.75 0 0 1 1.06 0L8 6.94l3.47-3.47a.75.75 0 1 1 1.06 1.06L9.06 8l3.47 3.47a.75.75 0 1 1-1.06 1.06L8 9.06l-3.47 3.47a.75.75 0 0 1-1.06-1.06L6.94 8L3.47 4.53a.75.75 0 0 1 0-1.06',
    check: 'M13.488 3.43a.75.75 0 0 1 .081 1.058l-6 7a.75.75 0 0 1-1.1.042l-3.5-3.5A.75.75 0 0 1 4.03 6.97l2.928 2.927l5.473-6.385a.75.75 0 0 1 1.057-.081',
    warn: 'M7.134 2.994L2.217 11.5a1 1 0 0 0 .866 1.5h9.834a1 1 0 0 0 .866-1.5L8.866 2.993a1 1 0 0 0-1.732 0m3.03-.75c-.962-1.665-3.366-1.665-4.329 0L.918 10.749c-.963 1.666.24 3.751 2.165 3.751h9.834c1.925 0 3.128-2.085 2.164-3.751zM8 5a.75.75 0 0 1 .75.75v2a.75.75 0 0 1-1.5 0v-2A.75.75 0 0 1 8 5m1 5.75a1 1 0 1 1-2 0a1 1 0 0 1 2 0',
    link: 'M3.47 6.53a.75.75 0 0 1 1.06 1.061l-.727.727a2.743 2.743 0 0 0 3.879 3.879l.727-.727a.75.75 0 0 1 1.06 1.06l-.726.727a4.243 4.243 0 0 1-6-6zm8 1.879a.75.75 0 0 0 1.06 1.06l.727-.726a4.243 4.243 0 0 0-6-6l-.727.727a.75.75 0 0 0 1.061 1.06l.727-.727a2.743 2.743 0 0 1 3.879 3.879zm-.94-1.879a.75.75 0 1 0-1.06-1.06l-4 4a.75.75 0 1 0 1.06 1.06z',
    info: 'M8 13.5a5.5 5.5 0 1 0 0-11a5.5 5.5 0 0 0 0 11M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14m1-9.5a1 1 0 1 1-2 0a1 1 0 0 1 2 0m-.25 3a.75.75 0 0 0-1.5 0V11a.75.75 0 0 0 1.5 0z',
    list: 'M5.28 2.22a.75.75 0 0 1 0 1.06l-2 2a.75.75 0 0 1-1.06 0l-1-1a.75.75 0 0 1 1.06-1.06l.47.47 1.47-1.47a.75.75 0 0 1 1.06 0M6.5 3.75c0 .414.336.75.75.75h7a.75.75 0 0 0 0-1.5h-7a.75.75 0 0 0-.75.75m.75 3.5a.75.75 0 0 0 0 1.5h7a.75.75 0 0 0 0-1.5zm-1.97.03a.75.75 0 0 0-1.06-1.06L2.75 7.69l-.47-.47a.75.75 0 0 0-1.06 1.06l1 1a.75.75 0 0 0 1.06 0zm0 3.19a.75.75 0 0 1 0 1.06l-2 2a.75.75 0 0 1-1.06 0l-1-1a.75.75 0 1 1 1.06-1.06l.47.47 1.47-1.47a.75.75 0 0 1 1.06 0m1.97 1.03a.75.75 0 0 0 0 1.5h7a.75.75 0 0 0 0-1.5z',
    eye: 'M1.87 8.515 1.641 8l.229-.515a6.708 6.708 0 0 1 12.26 0l.228.515-.229.515a6.708 6.708 0 0 1-12.259 0M.5 6.876l-.26.585a1.33 1.33 0 0 0 0 1.079l.26.584a8.208 8.208 0 0 0 15 0l.26-.584a1.33 1.33 0 0 0 0-1.08l-.26-.584a8.208 8.208 0 0 0-15 0M9.5 8a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0M11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0'
  };
  /* свои рисунки прототипа: [тег, атрибуты, обводка] */
  var CUSTOM = {
    router: [['rect', { x: 1.75, y: 8.25, width: 12.5, height: 5.5, rx: 1.75 }, 1], ['path', { d: 'M4.6 8.2 3.6 2.9M11.4 8.2l1-5.3' }, 1], ['circle', { cx: 4.9, cy: 11, r: 0.95 }], ['circle', { cx: 7.4, cy: 11, r: 0.95 }]],
    split: [['path', { d: 'M1.75 8h4.5M6.25 8c2 0 2.5-4.25 5-4.25h2.5M6.25 8c2 0 2.5 4.25 5 4.25h2.5M12 1.9l1.85 1.85L12 5.6M12 10.4l1.85 1.85L12 14.1' }, 1]],
    pool: [['rect', { x: 1.75, y: 1.75, width: 12.5, height: 3.5, rx: 1.25 }, 1], ['rect', { x: 1.75, y: 6.25, width: 12.5, height: 3.5, rx: 1.25, opacity: 0.55 }, 1], ['rect', { x: 1.75, y: 10.75, width: 12.5, height: 3.5, rx: 1.25, opacity: 0.3 }, 1], ['circle', { cx: 4.25, cy: 3.5, r: 0.95 }]],
    tablet: [['rect', { x: 2.75, y: 1.75, width: 10.5, height: 12.5, rx: 1.75 }, 1], ['path', { d: 'M7 11.75h2' }, 1]],
    tv: [['rect', { x: 1.75, y: 2.75, width: 12.5, height: 8.5, rx: 1.5 }, 1], ['path', { d: 'M5.5 14.25h5M8 11.25v3' }, 1]],
    laptop: [['rect', { x: 3.25, y: 3.25, width: 9.5, height: 7, rx: 1 }, 1], ['path', { d: 'M1.25 12.75h13.5' }, 1]],
    gadget: [['path', { d: 'M5 5.25h6a3 3 0 0 1 2.9 3.75l-.55 2.1a1.75 1.75 0 0 1-3 .7L9.6 11H6.4l-.75.8a1.75 1.75 0 0 1-3-.7L2.1 9A3 3 0 0 1 5 5.25z' }, 1], ['path', { d: 'M5.25 7.5v2M4.25 8.5h2' }, 1], ['circle', { cx: 10.6, cy: 8.1, r: 0.85 }], ['circle', { cx: 11.6, cy: 9.4, r: 0.85 }]],
    refresh: [['path', { d: 'M13.25 8a5.25 5.25 0 1 1-1.6-3.77' }, 1], ['path', { d: 'M12.1 1.9v2.6H9.5' }, 1]]
  };

  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) if (OWN.call(attrs, k) && attrs[k] != null) el.setAttribute(k, String(attrs[k]));
    return el;
  }
  function icon(name, size) {
    var s = svgEl('svg', { viewBox: '0 0 16 16', width: size || 16, height: size || 16, fill: 'currentColor', 'aria-hidden': 'true', focusable: 'false', 'class': 'kg-ico' });
    var c = CUSTOM[name], g = null;
    if (c) {
      c.forEach(function (it) {
        var el = svgEl(it[0], it[1]);
        if (it[2]) {
          if (!g) { g = svgEl('g', { fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }); s.appendChild(g); }
          g.appendChild(el);
        } else s.appendChild(el);
      });
    } else if (GI[name]) s.appendChild(svgEl('path', { 'fill-rule': 'evenodd', 'clip-rule': 'evenodd', d: GI[name] }));
    return s;
  }

  /* ── Мелочи ─────────────────────────────────────────────────────────── */
  var S = null;                     // смонтированный экземпляр (он один: unmount() без аргументов)

  function h(tag, a, kids) {
    var el = document.createElement(tag);
    if (a) for (var k in a) {
      if (!OWN.call(a, k)) continue;
      var v = a[k];
      if (v == null || v === false) continue;
      if (k === 'text') el.textContent = String(v);
      else if (k === 'cls') el.className = v;
      else if (k === 'value' || k === 'checked' || k === 'selected') el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    return append(el, kids);
  }
  function append(el, kids) {
    if (kids == null || kids === false) return el;
    if (!Array.isArray(kids)) kids = [kids];
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c == null || c === false) continue;
      el.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    }
    return el;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  // язык страницы — boot.js (window.KaskadLang); формы числа: «одна|несколько|много» или «one|other»
  var KL = window.KaskadLang || null;
  var LANG = KL && KL.lang === 'en' ? 'en' : 'ru';
  var LOCALE = (KL && KL.locale) || 'ru-RU';
  function pickForm(n, forms) {
    if (KL && typeof KL.form === 'function') return KL.form(n, forms.join('|'));
    n = Math.abs(n) % 100; var m = n % 10;
    var i = n > 10 && n < 20 ? 2 : m === 1 ? 0 : m >= 2 && m <= 4 ? 1 : 2;
    return forms[Math.min(i, forms.length - 1)];
  }
  var nf0 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
  var nf1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  function fmtRate(bps) {
    if (bps == null || !isFinite(bps)) return '—';
    if (bps < 1e6) return t('graph.unit.kbit', { v: nf0.format(Math.round(bps / 1000)) });
    var m = bps / 1e6;
    return t('graph.unit.mbit', { v: m >= 100 ? nf0.format(m) : nf1.format(m) });
  }
  // инициалы в кружке человека — от того, что на экране вместо имени: с «И имена» режима стрима — от «Пользователь N»
  // (Н-4 стенда 3.3.7.13: сетка stream.js меняет имя только целиком, по двум буквам человек узнаётся)
  function initials(s) { var ks = typeof window !== 'undefined' ? window.KaskadStream : null; if (ks && ks.face) s = ks.face(s); var w = String(s || '?').trim().split(/\s+/); return ((w[0] || '?').charAt(0) + (w[1] ? w[1].charAt(0) : (w[0] || '').charAt(1))).toUpperCase(); }
  function copyRoute(r) { return r ? { via: r.via, fail: r.fail || 'block' } : null; }
  function sameRoute(a, b) { return (!a && !b) || (!!a && !!b && a.via === b.via && (a.fail || 'block') === (b.fail || 'block')); }
  function reducedMotion() { try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (_) { return false; } }

  /* Тексты: сначала ctx.t, затем словарь graph-i18n-ru.js; «{n}» и подобные — подстановки, «a|b|c» — формы числа. */
  function dict() { return window.KaskadGraphI18n || {}; }
  function raw(key) {
    var s = null;
    if (S && S.ctx && typeof S.ctx.t === 'function') {
      try { s = S.ctx.t(key); } catch (_) { s = null; }
      if (typeof s !== 'string' || !s || s === key) s = null;
    }
    if (s == null && OWN.call(dict(), key)) s = dict()[key];
    return s == null ? key : s;
  }
  function fill(s, vars) { return vars ? s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; }) : s; }
  function t(key, vars) { return fill(raw(key), vars); }
  function tn(key, n, vars) {
    var s = raw(key), forms = s.split('|');
    if (forms.length > 1) s = pickForm(n, forms);
    var v = {}; if (vars) for (var k in vars) if (OWN.call(vars, k)) v[k] = vars[k];
    if (v.n == null) v.n = nf0.format(n);
    return fill(s, v);
  }

  /* ctx.api может вернуть разобранный JSON, Response или отказ с {status, data}: приводим к {ok, status, data}.
     data отказа — ВСЁ тело ответа сервера {ok: false, error, code, data}: «stale» (data.reason), построчные ошибки
     намерений (data.results), 409 раскладки (data.version) и текст для человека (error) читаются из него. */
  function call(method, path, body) {
    var ctx = S && S.ctx;
    var prefix = ctx && typeof ctx.apiPrefix === 'string' ? ctx.apiPrefix : API;
    return new Promise(function (resolve) {
      var p;
      try { p = ctx.api(method, prefix + path, body); } catch (e) { resolve({ ok: false, status: 0, data: { error: String((e && e.message) || e) } }); return; }
      Promise.resolve(p).then(function (res) {
        if (res && typeof res.json === 'function' && typeof res.status === 'number') {
          res.json().then(function (j) { resolve({ ok: res.status < 300 && !(j && j.ok === false), status: res.status, data: j }); },
            function () { resolve({ ok: false, status: res.status, data: null }); });
          return;
        }
        resolve({ ok: !(res && res.ok === false), status: 200, data: res });
      }, function (err) {
        resolve({ ok: false, status: (err && (err.status || err.statusCode)) || 0, data: errBody(err) });
      });
    });
  }
  /* Тело ответа-ошибки из отказа ctx.api. Ошибка, у которой есть только вложенное data ответа ({reason, version} —
     так его кладёт ApiError панели) без error и ok, оборачивается: текст — её message, data — то, что было. */
  function errBody(err) {
    var d = err && (err.body || err.data || err.json || err.payload || err.response);
    if (typeof d === 'string') { try { d = JSON.parse(d); } catch (_) { d = { error: d }; } }
    var msg = err && err.message ? String(err.message) : (typeof err === 'string' ? err : '');
    if (!d || typeof d !== 'object' || Array.isArray(d)) return { ok: false, error: msg || null, code: err && err.code != null ? err.code : null, data: null };
    if (d.error == null && d.ok == null) return { ok: false, error: msg || null, code: err && err.code != null ? err.code : null, data: d };
    return d;
  }
  function errText(r) { return (r && r.data && (r.data.error || r.data.message)) || t('graph.err.network'); }

  /* ── Монтирование ───────────────────────────────────────────────────── */
  function mount(el, ctx) {
    if (S) unmount();
    if (!el) throw new Error('KaskadGraph.mount: no element');
    S = {
      el: el, ctx: ctx || {}, dom: {}, off: [], timers: {},
      base: null, idx: null, draft: null, view: null, intents: [], hist: [],
      pos: {}, order: {}, ports: {}, nodeEl: {}, rowEl: {}, liveEl: {}, edgeEl: {}, labelEl: {},
      V: { x: 0, y: 0, k: 1 }, sel: null, trace: null, onlyActive: false, mode: '', userMode: false,
      live: { devices: {}, exits: {}, inbounds: {}, groups: {}, ts: null }, rates: {}, prev: {}, liveAt: 0,
      layoutVersion: 0, dirtyPos: {}, dirtyOrders: null, loadSeq: 0, gesture: null, pointers: {},
      hover: null, dialog: null, busy: false, laidOut: false,
      focus: null, nav: ctx && ctx.nav && typeof ctx.nav === 'object' ? ctx.nav : null,
      sim: null, simLast: null, simSeq: 0,
      rfTick: false, rfOrgs: null, rfFor: null   // галочка ⚠РФ черновика, организации показанного блока и набор выбора под ней (R4)
    };
    var me = S;
    ensureDict(function () {
      if (S !== me) return;                              // успели размонтировать или смонтировать заново
      buildShell();
      subscribe();
      load({ initial: true });
    });
  }

  /* Словарь: если index.html его не подключил — подгрузить рядом с graph.js (тот же источник, CSP 'self').
     Английский — первым: русский потом добирает только отсутствующие ключи (нет перевода — русский текст). */
  function ensureDict(done) {
    if (window.KaskadGraphI18n || !SCRIPT_SRC) { done(); return; }
    var files = LANG === 'en' ? ['graph-i18n-en.js', 'graph-i18n-ru.js'] : ['graph-i18n-ru.js'];
    (function next(i) {
      if (i >= files.length) { done(); return; }
      var src = SCRIPT_SRC.replace(/graph\.js(\?|$)/, files[i] + '$1');
      if (src === SCRIPT_SRC) { done(); return; }
      var s = document.createElement('script');
      s.src = src; s.async = true;
      s.onload = s.onerror = function () { next(i + 1); };
      document.head.appendChild(s);
    }(0));
  }

  function unmount() {
    if (!S) return;
    var st = S;
    S = null;
    st.off.forEach(function (f) { try { f(); } catch (_) { } });
    Object.keys(st.timers).forEach(function (k) { clearTimeout(st.timers[k]); });
    if (st.raf) cancelAnimationFrame(st.raf);
    if (st.dom.root && st.dom.root.parentNode) st.dom.root.parentNode.removeChild(st.dom.root);
  }

  function listen(target, type, fn, opts) {
    target.addEventListener(type, fn, opts || false);
    S.off.push(function () { target.removeEventListener(type, fn, opts || false); });
  }
  function later(name, ms, fn) {
    if (!S) return;
    clearTimeout(S.timers[name]);
    S.timers[name] = setTimeout(function () { if (S) { delete S.timers[name]; fn(); } }, ms);
  }

  function subscribe() {
    var ctx = S.ctx;
    if (typeof ctx.onLive === 'function') {
      var off = ctx.onLive(function (payload) { if (S) onLive(payload); });
      if (typeof off === 'function') S.off.push(off);
    }
    if (typeof ctx.onState === 'function') {
      var off2 = ctx.onState(function () { if (S) scheduleRefetch(); });
      if (typeof off2 === 'function') S.off.push(off2);
    }
  }

  /* ── Каркас ─────────────────────────────────────────────────────────── */
  function btn(cls, act, label, ic, extra) {
    var a = { type: 'button', cls: 'kg-btn ' + cls, 'data-act': act };
    if (extra) for (var k in extra) if (OWN.call(extra, k)) a[k] = extra[k];
    return h('button', a, [ic ? icon(ic, 14) : null, label ? h('span', { text: label }) : null]);
  }

  function buildShell() {
    var d = S.dom;
    d.apply = btn('kg-btn--primary kg-btn--sm', 'apply', t('graph.apply'), 'check', { disabled: true });
    d.applyN = d.apply.querySelector('span');
    d.viewSeg = h('div', { cls: 'kg-seg', role: 'tablist', 'aria-label': t('graph.view.aria') }, [
      h('button', { type: 'button', role: 'tab', 'data-act': 'view', 'data-v': 'canvas', 'aria-selected': 'true', text: t('graph.view.canvas') }),
      h('button', { type: 'button', role: 'tab', 'data-act': 'view', 'data-v': 'list', 'aria-selected': 'false', text: t('graph.view.list') })
    ]);
    d.kOn = h('span', { cls: 'kg-chip', text: '—' });
    d.kRate = h('span', { cls: 'kg-chip kg-chip--plain', text: '—' });
    d.kExit = h('span', { cls: 'kg-chip', text: '—' });
    d.gwNote = h('div', { cls: 'kg-banner', hidden: true });
    d.focusBar = h('div', { cls: 'kg-focus', hidden: true, role: 'status', 'aria-live': 'polite' });
    d.pinBar = h('div', { cls: 'kg-pin', hidden: true, role: 'status' });
    d.top = h('div', { cls: 'kg-top' }, [
      h('div', { cls: 'kg-title' }, [h('span', { cls: 'kg-logo' }, icon('split', 18)), h('div', null, [h('h3', { text: t('graph.title') }), h('div', { cls: 'kg-sub', text: t('graph.subtitle') })])]),
      h('div', { cls: 'kg-kpis', 'aria-live': 'polite' }, [d.kOn, d.kRate, d.kExit]),
      h('div', { cls: 'kg-actions' }, [d.viewSeg, d.apply])
    ]);
    d.undo = btn('kg-btn--ghost kg-btn--sm', 'undo', t('graph.undo'), 'undo', { title: t('graph.undo.hint'), disabled: true });
    // у значка на телефоне подписи нет — имя и подсказка у кнопки всегда (Н-9)
d.onlyAct = btn('kg-btn--ghost kg-btn--sm', 'only-active', t('graph.onlyActive'), 'link', { 'aria-pressed': 'false', 'aria-label': t('graph.onlyActive'), title: t('graph.onlyActive.hint') });
    d.tools = h('div', { cls: 'kg-tools' }, [
      h('div', { cls: 'kg-tip' }, [h('b', { text: t('graph.tip.access') }), ' ', t('graph.tip.accessText'), ' ', h('b', { text: t('graph.tip.route') }), ' ', t('graph.tip.routeText')]),
      h('div', { cls: 'kg-tools__r' }, [d.onlyAct, btn('kg-btn--ghost kg-btn--sm', 'trace', t('graph.sim.open'), 'magnifier'), d.undo,
        btn('kg-btn--ghost kg-btn--sm', 'layout', t('graph.layout'), 'sliders'), btn('kg-btn--ghost kg-btn--sm kg-btn--icon', 'reload', '', 'refresh', { 'aria-label': t('graph.reload'), title: t('graph.reload') })])
    ]);
    d.svg = svgEl('svg', { 'class': 'kg-edges', 'aria-hidden': 'true' });
    d.gEdges = svgEl('g'); d.gTemp = svgEl('g');
    d.svg.appendChild(d.gEdges); d.svg.appendChild(d.gTemp);
    d.labels = h('div', { cls: 'kg-labels' });
    d.nodes = h('div', { cls: 'kg-nodes' });
    d.world = h('div', { cls: 'kg-world' }, [d.svg, d.labels, d.nodes]);
    d.zoomV = h('span', { cls: 'kg-zoom__v', text: '100 %' });
    d.hint = h('div', { cls: 'kg-hint', hidden: true, role: 'status' });
    d.empty = h('div', { cls: 'kg-empty', hidden: true });
    d.stage = h('div', { cls: 'kg-stage', tabindex: '0', role: 'application', 'aria-label': t('graph.stage.aria') }, [
      d.world,
      h('div', { cls: 'kg-ui kg-zoom' }, [btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'zout', '', 'minus', { 'aria-label': t('graph.zoom.out') }), d.zoomV,
        btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'zin', '', 'plus', { 'aria-label': t('graph.zoom.in') }), btn('kg-btn--ghost kg-btn--sm', 'fit', t('graph.zoom.fit'))]),
      d.hint, d.empty
    ]);
    d.list = h('div', { cls: 'kg-list' });
    d.insp = h('aside', { cls: 'kg-insp', hidden: true, 'aria-label': t('graph.insp.aria') });
    d.toast = h('div', { cls: 'kg-toast', role: 'status', 'aria-live': 'polite', hidden: true });
    d.dlg = h('div', { cls: 'kg-dlgback', hidden: true });
    d.loading = h('div', { cls: 'kg-loading', role: 'status', text: t('graph.loading') });
    d.main = h('div', { cls: 'kg-main' }, [d.stage, d.list, d.insp, d.toast, d.loading]);
    d.root = h('div', { cls: 'kg', 'data-view': 'canvas' }, [d.top, d.gwNote, d.tools, d.focusBar, d.pinBar, d.main, d.dlg]);
    clear(S.el).appendChild(d.root);

    listen(d.root, 'click', onClick);
    listen(d.root, 'change', onChange);
    listen(d.root, 'input', onInput);
    listen(d.root, 'submit', onSubmit);
    listen(d.root, 'keydown', onKey);
    listen(document, 'keydown', function (e) {
      if (e.key !== 'Escape' || !S.focus || S.dialog || e.defaultPrevented) return;
      if (e.target === document || e.target === document.body || e.target === document.documentElement) setFocus(null);
    });
    listen(d.stage, 'pointerdown', onPointerDown);
    listen(d.stage, 'pointermove', onPointerMove);
    listen(d.stage, 'pointerup', onPointerUp);
    listen(d.stage, 'pointercancel', onPointerUp);
    listen(d.stage, 'wheel', onWheel, { passive: false });
    listen(d.stage, 'pointerover', onPointerOver);
    listen(d.stage, 'pointerleave', function () { if (S.hover) { setHover(null); } });
    if ('ResizeObserver' in window) {
      var ro = new ResizeObserver(function () { if (S) onResize(); });
      ro.observe(d.root);
      S.off.push(function () { ro.disconnect(); });
    } else listen(window, 'resize', onResize);
    listen(window, 'resize', function () { if (S) sizeStage(); });          // высота окна: холст по ней (Н-2)
    S.mode = '';
  }

  function onResize() {
    var w = S.dom.root.clientWidth;
    if (!w || (Math.abs(w - (S.lastW || 0)) < 24 && S.mode)) return;
    S.lastW = w;
    S.dom.root.classList.toggle('is-narrow', w < NARROW);
    var before = S.mode;
    if (!S.userMode) setMode(w < NARROW ? 'list' : 'canvas');
    if (S.mode === 'canvas') sizeStage();
    if (S.mode === before && S.mode === 'canvas' && S.base) { if (S.trace && S.trace.focus) fitNodes(traceIds(S.trace)); else fit(); }
  }
  /* Первый вид — по ширине, без отрисовки (её сделает setBase). */
  function initMode() {
    var w = S.dom.root.clientWidth || NARROW;
    S.lastW = w;
    S.mode = w < NARROW ? 'list' : 'canvas';
    S.dom.root.setAttribute('data-view', S.mode);
    S.dom.root.classList.toggle('is-narrow', w < NARROW);
    syncViewSeg();
  }
  function syncViewSeg() {
    Array.prototype.forEach.call(S.dom.viewSeg.querySelectorAll('button'), function (b) { b.setAttribute('aria-selected', String(b.getAttribute('data-v') === S.mode)); });
  }

  /* ── Данные ─────────────────────────────────────────────────────────── */
  function load(opts) {
    if (!S) return;
    var seq = ++S.loadSeq;
    call('GET', '/scheme').then(function (r) {
      if (!S || seq !== S.loadSeq) return;
      S.dom.loading.hidden = true;
      if (!r.ok || !r.data || !Array.isArray(r.data.nodes)) {
        if (!S.base) { S.dom.loading.hidden = false; S.dom.loading.textContent = t('graph.err.load', { why: errText(r) }); }
        else toast(t('graph.err.load', { why: errText(r) }));
        return;
      }
      setBase(r.data, opts || {});
    });
  }
  function scheduleRefetch() {
    later('refetch', REFETCH_DELAY, function () {
      if (S.dialog || S.gesture) { scheduleRefetch(); return; }
      load({ keep: true });
    });
  }

  function indexBase(doc) {
    var I = { node: {}, user: {}, dev: {}, devList: [], users: [], exits: [], rules: [], pool: null, ip: {}, ruGroups: [], ruEdge: {} };
    // адрес устройства на входе — data.ip рёбер access (scheme.py _ip_of): ключи device.ips — пулы адресов (ppp у L2TP,
    // PPTP и SSTP, ovpn_u/ovpn_t у OpenVPN), а не виды входов
    (doc.edges || []).forEach(function (e) {
      if (e && e.type === 'access' && e.data && e.data.ip) I.ip[e.data.device + ' ' + e.data.kind] = String(e.data.ip);
      // российские сайты устройства идут через его российскую группу — по серверу (exits.ru_target, T15 Q6)
      if (e && e.type === 'ru_via' && e.data && e.data.device) I.ruEdge[e.data.device] = e.data.group;
    });
    doc.nodes.forEach(function (n) {
      I.node[n.id] = n;
      if (n.type === 'user') {
        I.users.push(n); I.user[n.id] = n.data;
        (n.data.devices || []).forEach(function (d) {
          var c = {}; for (var k in d) if (OWN.call(d, k)) c[k] = d[k];
          c.user = n.id; I.dev[d.id] = c; I.devList.push(c);
        });
      } else if (n.type === 'exit') I.exits.push(n);
      else if (n.type === 'ru_group') I.ruGroups.push(n);
      else if (n.type === 'rules') I.rules.push(n);
      else if (n.type === 'pool') I.pool = n;
    });
    return I;
  }

  /* Адрес устройства на входе (с сервера; у намерения «+ вход», ещё не применённого, его нет). */
  function ipOf(did, kind) { return (S.idx && S.idx.ip[did + ' ' + kind]) || null; }
  /* Повтор ключа в пуле: id e-<10 hex>-<номер строки> (kaskad.gw.exits.pool). Шлюз его не использует, маршрутом он
     не бывает и настроек у него нет — сервер такой маршрут не примет. */
  function dupExit(id) { return /^(exit:)?e-[0-9a-f]{10}-[0-9]+$/.test(String(id || '')); }

  function setBase(doc, opts) {
    var first = !S.base;
    S.base = doc;
    S.idx = indexBase(doc);
    S.layoutVersion = typeof doc.layout_version === 'number' ? doc.layout_version : 0;
    // позиции: сохранённые на сервере; свои несохранённые перемещения важнее
    var missing = [];
    doc.nodes.forEach(function (n) {
      if (S.dirtyPos[n.id] && S.pos[n.id]) return;
      if (n.position && isFinite(n.position.x) && isFinite(n.position.y)) S.pos[n.id] = { x: n.position.x, y: n.position.y };
      else if (!S.pos[n.id]) missing.push(n.id);
    });
    Object.keys(S.pos).forEach(function (id) { if (!S.idx.node[id]) delete S.pos[id]; });
    S.idx.users.forEach(function (u) {
      var ids = (u.data.devices || []).map(function (d) { return d.id; }), mine = S.dirtyOrders && S.dirtyOrders[u.id];
      if (mine) ids = mine.filter(function (d) { return ids.indexOf(d) >= 0; }).concat(ids.filter(function (d) { return mine.indexOf(d) < 0; }));
      S.order[u.id] = ids;
    });
    if (doc.live && (!S.live.ts || (doc.live.ts && doc.live.ts >= S.live.ts))) mergeLive(doc.live, true);
    // намерения, которые больше не к чему применить (устройство удалили в боте), убираем
    var before = S.intents.length;
    S.intents = S.intents.filter(intentStillValid);
    if (S.intents.length !== before) { resyncHist(); toast(t('graph.intents.dropped')); }
    rebuildDraft();
    computeView();
    retrace();
    if (S.sim && S.sim.res && !first) runSim(true, true);    // настройки изменились (бот, меню, другая вкладка): спросить снова
    renderGwNote();
    if (!S.mode) initMode();
    var nav = S.nav;
    S.nav = null;
    if (nav) prepNav(nav);
    if (S.mode === 'canvas') {
      renderNodes();
      if (missing.length === doc.nodes.length && doc.nodes.length) firstLayout();
      else if (missing.length) { placeNew(missing); applyPositions(); }
      renderEdges();
      updateLive(); deOverlap();                            // живые цифры меняют высоты — и сразу без наложений (Н12)
      sizeStage();
      if (first || opts.fit) fit(true);                   // первый взгляд — схема целиком (Н-2); телефон — её начало (Н-4)
    } else renderList();
    renderEmpty();
    refreshSelection();
    renderFocusBar();
    updateLive();
    updateToolbar();
    if (nav) finishNav(nav);
  }

  function intentStillValid(it) {
    var I = S.idx;
    if (it.type === 'grant' || it.type === 'revoke') return !!I.dev[it.device] && (it.type === 'revoke' || !!I.node['in:' + it.kind]);
    if (it.type === 'route' || it.type === 'rules') {
      if (it.type === 'route' && it.via && it.via !== 'inherit' && !I.node[it.via]) return false;
      return it['default'] ? true : it.device ? !!I.dev[it.device] : !!I.user[it.user];
    }
    // «Российские сайты»: группу удалили (другая вкладка, меню) — намерение устарело
    if (it.type === 'ru_via') return (it.via === 'inherit' || it.via === 'direct' || !!I.node['rug:' + it.via]) && (it.device ? !!I.dev[it.device] : !!I.user[it.user]);
    return false;
  }

  /* ── Черновик: база сервера + намерения ─────────────────────────────── */
  function rebuildDraft() {
    var I = S.idx, D = { users: {}, devices: {}, def: copyRoute(S.base.default_route) || { via: 'pool', fail: 'block' } };
    I.users.forEach(function (u) { D.users[u.id] = { route: copyRoute(u.data.route), rules: u.data.rules || null, ru_via: u.data.ru_via || null }; });
    I.devList.forEach(function (d) { D.devices[d.id] = { access: d.access.slice(), route: copyRoute(d.route), rules: d.rules || null, user: d.user, ru_via: d.ru_via || null }; });
    S.intents.forEach(function (it) { applyToDraft(D, it); });
    S.draft = D;
    rfSync();
  }
  function applyToDraft(D, it) {
    var o;
    if (it.type === 'grant' || it.type === 'revoke') {
      o = D.devices[it.device]; if (!o) return;
      var i = o.access.indexOf(it.kind);
      if (it.type === 'grant' && i < 0) o.access.push(it.kind);
      if (it.type === 'revoke' && i >= 0) o.access.splice(i, 1);
    } else if (it.type === 'route') {
      if (it['default']) { D.def = { via: it.via, fail: it.fail || D.def.fail }; return; }
      o = it.device ? D.devices[it.device] : D.users[it.user]; if (!o) return;
      o.route = it.via === 'inherit' ? null : { via: it.via, fail: it.fail || 'block' };
    } else if (it.type === 'rules') {
      o = it.device ? D.devices[it.device] : D.users[it.user]; if (!o) return;
      o.rules = it.profile === 'inherit' ? null : it.profile;
    } else if (it.type === 'ru_via') {
      o = it.device ? D.devices[it.device] : D.users[it.user]; if (!o) return;
      o.ru_via = it.via === 'inherit' ? null : it.via;
    }
  }
  function effRoute(did) {
    var d = S.draft.devices[did]; if (!d) return null;
    if (d.route) return { via: d.route.via, fail: d.route.fail, from: 'device' };
    var u = S.draft.users[d.user];
    if (u && u.route) return { via: u.route.via, fail: u.route.fail, from: 'user' };
    return { via: S.draft.def.via, fail: S.draft.def.fail, from: 'default' };
  }
  /* Профиль узла (gateway.rules.default) — у кого нет своего и нет у человека. Из коробки «Россия напрямую», не «всё
     через выход» (ревизия схемы, Б3); сервер без profiles.default — профиль устройства, взятый «по умолчанию». */
  function defProfile() {
    var P = S.base && S.base.profiles;
    if (!P || !P.supported) return 'all_exit';
    if (P['default']) return P['default'];
    var b = S.idx && S.idx.devList.filter(function (x) { return x.profile_from === 'default'; })[0];
    return b ? b.profile : 'all_exit';
  }
  function effProfile(did) {
    var d = S.draft.devices[did];
    if (!S.base.profiles || !S.base.profiles.supported) return ['all_exit', 'default'];
    if (d && d.rules) return [d.rules, 'device'];
    var u = d && S.draft.users[d.user];
    if (u && u.rules) return [u.rules, 'user'];
    return [defProfile(), 'default'];
  }
  function userProfile(uid) {
    var u = S.draft.users[uid];
    return (u && u.rules) || defProfile();
  }

  function intentKey(it) {
    if (it.type === 'grant' || it.type === 'revoke') return 'a|' + it.device + '|' + it.kind;
    if (it.type === 'route') return 'r|' + (it['default'] ? 'default' : it.device ? 'd|' + it.device : 'u|' + it.user);
    if (it.type === 'ru_via') return 'v|' + (it.device ? 'd|' + it.device : 'u|' + it.user);
    return 'p|' + (it.device ? 'd|' + it.device : 'u|' + it.user);
  }
  function sameAsBase(it) {
    var I = S.idx, cur;
    if (it.type === 'grant' || it.type === 'revoke') {
      var d = I.dev[it.device]; if (!d) return false;
      var has = d.access.indexOf(it.kind) >= 0;
      return it.type === 'grant' ? has : !has;
    }
    if (it.type === 'route') {
      cur = it['default'] ? S.base.default_route : it.device ? (I.dev[it.device] || {}).route : (I.user[it.user] || {}).route;
      if (it.via === 'inherit') return !cur;
      return sameRoute(cur, { via: it.via, fail: it.fail });
    }
    if (it.type === 'rules') {
      cur = it.device ? (I.dev[it.device] || {}).rules : (I.user[it.user] || {}).rules;
      return (it.profile === 'inherit' ? null : it.profile) === (cur || null);
    }
    if (it.type === 'ru_via') {
      cur = it.device ? (I.dev[it.device] || {}).ru_via : (I.user[it.user] || {}).ru_via;
      return (it.via === 'inherit' ? null : it.via) === (cur || null);
    }
    return false;
  }
  /* Что отправить: по каждому ключу — последнее намерение, без совпадающих с сервером, в порядке последнего. */
  function pending() {
    var last = {};
    S.intents.forEach(function (it, i) { last[intentKey(it)] = { it: it, i: i }; });
    return Object.keys(last).map(function (k) { return last[k]; })
      .sort(function (a, b) { return a.i - b.i; })
      .map(function (x) { return x.it; })
      .filter(function (it) { return !sameAsBase(it); });
  }

  /* Маршрут, который объект наследует (без своего): устройство — человека, иначе по умолчанию; человек — по умолчанию.
     {via, fail, from: 'user' | 'default'}. */
  function inheritedRoute(kind, id) {
    if (kind === 'dev') {
      var D = S.draft.devices[id], U = D && S.draft.users[D.user];
      if (U && U.route) return { via: U.route.via, fail: U.route.fail || 'block', from: 'user', name: nodeTitle(D.user) };
    }
    return { via: S.draft.def.via, fail: S.draft.def.fail || 'block', from: 'default' };
  }
  /* Новая цель маршрута сохраняет политику отказа — свою, иначе унаследованную: выбор «Пул» не ставит «блок» молча вместо
     унаследованного «напрямую» (T-scheme-3.2.15, Н1; так же делает ядро — model.kept_fail). */
  function keptFail(it) {
    if (it['default']) return S.draft.def.fail || 'block';
    var own = it.device ? (S.draft.devices[it.device] || {}).route : (S.draft.users[it.user] || {}).route;
    if (own) return own.fail || 'block';
    return inheritedRoute(it.device ? 'dev' : 'user', it.device || it.user).fail;
  }
  function addIntent(it, quiet) {
    if (it.type === 'route' && it.via !== 'inherit' && !it.fail) it.fail = keptFail(it);
    S.intents.push(it);
    S.hist.push({ kind: 'intent' });
    if (S.hist.length > HIST_MAX) S.hist.shift();
    rebuildDraft();
    structureChanged();
    if (!quiet) toast(intentText(it), true);
  }

  /* Намерения убраны не по одному (устарели, ошибочные): отмена — снова по одному на оставшиеся. */
  function resyncHist() {
    S.hist = S.hist.filter(function (e) { return e.kind !== 'intent'; });
    S.intents.forEach(function () { S.hist.push({ kind: 'intent' }); });
  }
  function undo() {
    var e = S.hist.pop();
    if (!e) { toast(t('graph.undo.nothing')); return; }
    if (e.kind === 'intent') { S.intents.pop(); rebuildDraft(); structureChanged(); }
    else if (e.kind === 'move' || e.kind === 'layout') {
      Object.keys(e.before).forEach(function (id) { if (e.before[id]) S.pos[id] = { x: e.before[id].x, y: e.before[id].y }; S.dirtyPos[id] = 1; });
      if (e.orders) { Object.keys(e.orders).forEach(function (u) { S.order[u] = e.orders[u].slice(); }); S.dirtyOrders = e.orders; reorderRows(); }
      applyPositions(); layoutEdges(); later('save', SAVE_DELAY, saveLayout);
    }
    toast(t('graph.undo.done'));
    updateToolbar();
  }

  /* ── Вид: узлы и видимые связи ──────────────────────────────────────── */
  function nodeTitle(id) {
    var n = S.idx.node[id];
    if (!n) return id;
    if (n.type === 'user') return n.data.name;
    if (n.type === 'inbound') return n.data.title;
    if (n.type === 'rules') return n.data.title;
    if (n.type === 'pool') return t('graph.pool');
    if (n.type === 'exit') return n.data.name || n.data.id;
    if (n.type === 'ru_group') return n.data.name;
    if (n.type === 'direct') return t('graph.direct');
    if (n.type === 'block') return t('graph.block');
    return id;
  }
  function viaTitle(via) {
    if (via === 'pool') return t('graph.pool');
    if (via === 'direct') return t('graph.direct');
    if (via === 'block') return t('graph.block');
    return t('graph.exitNamed', { name: nodeTitle(via) });
  }
  function devTitle(did) { var d = S.idx.dev[did]; return d ? d.name : did; }
  function kindTitle(k) { return (S.base.kinds && S.base.kinds[k]) || k; }
  /* «Россия напрямую» — как она работает СЕЙЧАС, по таблице ядра (ревизия схемы, С2): data.ru узла правил
     {state: ok | no_lists | not_loaded | unknown, domains, error, next}; сервер без ru — по «списки скачаны». */
  function ruInfo() {
    var n = S.idx && S.idx.node['rules:ru_direct'], d = n && n.data;
    if (d && d.ru && d.ru.state) return d.ru;
    var ru = S.base && S.base.profiles && S.base.profiles.ru;
    if (ru && ru.state) return ru;
    return { state: d && d.lists === false ? 'no_lists' : 'ok' };
  }
  function ruWorks() { return ruInfo().state === 'ok'; }
  function hhmm(sec) {
    var d = new Date(Number(sec) * 1000);
    if (!isFinite(d.getTime())) return '';
    return (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
  }
  function ruNoteText(rd) {
    var ru = rd && rd.ru && rd.ru.state ? rd.ru : ruInfo();
    if (ru.state === 'no_lists') return t('graph.rules.noLists');
    if (ru.state === 'no_consent') return t('graph.rules.noConsent');
    if (ru.state === 'not_loaded') return t('graph.rules.notLoaded', { why: ru.error ? ' (' + ru.error + ')' : '', at: ru.next ? t('graph.rules.nextTry', { at: hhmm(ru.next) }) : '' });
    if (ru.state === 'unknown') return t('graph.rules.notYet');
    if (ru.domains === false) return t('graph.rules.noDomains');
    return '';
  }
  /* Идёт ли Россия напрямую у ЭТОГО устройства при маршруте r: при «блоке» по выбору Россия закрыта, при отказе
     «напрямую» напрямую идёт всё. Этап 1 (SE §2.3, Б1): при запасном блоке группы (выходы непригодны) Россия идёт
     напрямую и на входах ядра (gw_ruonly), как на входах xray, — ветка ruFailBlock ниже это ещё не знает (показ — R4).
     -> {open, why}. */
  function ruOpen(did, r) {
    if (!ruWorks()) return { open: false, why: 'graph.trace.ruNotWorking' };
    if (r.via === 'block') return { open: false, why: 'graph.trace.ruBlock' };
    if (r.via === 'direct') return { open: true, why: 'graph.trace.ruRouteDirect' };
    var b = S.idx.dev[did], now = b && b.now;
    var same = b && b.effective && b.effective.via === r.via && b.effective.fail === r.fail;
    if (same && now && now.target === 'block') return { open: false, why: 'graph.trace.ruFailBlock' };
    if (same && now && now.target === 'direct') return { open: true, why: 'graph.trace.ruFailDirect' };
    return { open: true, why: 'graph.trace.ruDirect' };
  }
  /* ── Путь трафика: ОДНА цепочка (ревизия схемы, этап 2) ─────────────────
     устройство → вход (один) → фильтр узла → правила (профиль) → одна строка → цель маршрута → выход. Ид звеньев — те
     же, что у сервера (kaskad.gw.trace.chain, scheme.py): access:<устр>:<вход>, flow:<вход>, filter:<ads|torrent>,
     pass:<профиль>, cat:<профиль>:ru, route:<профиль>:<цель>, fail:<профиль>:<цель>:<direct|block>, member:<выход>.
     Входы устройства — способы подключения, а не ответвления пути: трафик идёт через ОДИН из них. */
  /* ── Российские группы (T15 Q6, SPEC-groups §8.3–8.4) ─────────────────
     «Российские сайты» устройства: своё → пользователя → напрямую с сервера (как model.ru_via_of; группы нет — напрямую,
     следующий уровень не спрашиваем). Работает выбор, только когда «Россия напрямую» действует для устройства (как
     rules.apply.device_rd: включено, профиль «Россия напрямую», списки и согласие ⚠РФ узла, маршрут не «блок») — по
     черновику; совпадает с сервером — ответ сервера (ru.acts). -> {via, from, group, acts}. */
  function ruKnown(v) { return v === 'direct' || !!(S.idx && S.idx.node['rug:' + v]); }
  function ruNodeOk() { var st = ruInfo().state; return st !== 'no_lists' && st !== 'no_consent'; }
  function ruOf(did) {
    var b = S.idx.dev[did], D = S.draft && S.draft.devices[did]; if (!b || !D) return null;
    var U = S.draft.users[D.user] || {}, via = 'direct', from = 'default';
    if (D.ru_via) { via = ruKnown(D.ru_via) ? D.ru_via : 'direct'; from = 'device'; }
    else if (U.ru_via) { via = ruKnown(U.ru_via) ? U.ru_via : 'direct'; from = 'user'; }
    var p = effProfile(did)[0], r = effRoute(did), bru = b.ru || {};
    var same = p === (b.profile || defProfile()) && (r.via === 'block') === (!!b.effective && b.effective.via === 'block');
    var acts = same && typeof bru.acts === 'boolean' ? bru.acts : !!b.active && p === 'ru_direct' && ruNodeOk() && r.via !== 'block';
    return { via: via, from: from, group: via !== 'direct', acts: acts };
  }
  /* «Напрямую с сервера» | «Через «Дом»» — значение поля (группа — её показанным именем). */
  function ruValueText(v) { var n = v && v !== 'direct' ? S.idx.node['rug:' + v] : null; return n ? t('graph.ru.via', { name: n.data.name }) : t('graph.ru.direct'); }
  /* Где российские сайты группы сейчас: по живым данным сборщика (live.groups — шаг группы, его время), иначе — по
     серверу (data.now: pins.json и снимок выходов). -> {step: exit | last_direct | last_block | unknown, exit, since, empty}. */
  function ruNowOf(n) {
    var dt = n.data, now = dt.now || { step: 'unknown' }, lg = liveFresh() && S.live.groups ? S.live.groups[dt.group] : null;
    if (!lg || now.empty) return now;
    if (lg.last_step === 'direct' || lg.last_step === 'block') return { step: 'last_' + lg.last_step, exit: null, since: lg.since || null, empty: false };
    var ok = function (r) { return r.enabled && !r.missing && exitSt(r.id, r.state) === 'alive'; }, rows = dt.exits || [];
    // первым — кому сборщик отдаёт новые устройства (первый ряд «силы» уровня, PLAN-order Ш5), а не первый по списку
    var rk = lg.ranks && lg.ranks[lg.active_tier], r1 = rk && rk.rows && rk.rows[0] ? rk.rows[0].map(function (x) { return x.id; }) : [];
    var lead = r1.map(function (id) { return rows.filter(function (r) { return r.id === id && r.tier === lg.active_tier && ok(r); })[0]; }).filter(Boolean)[0];
    var pick = lead || rows.filter(function (r) { return r.tier === lg.active_tier && ok(r); })[0] || rows.filter(ok)[0];
    return pick ? { step: 'exit', exit: pick.id, since: null, empty: false } : now;
  }
  /* «сейчас» группы в карточке: через ключ — спокойно-зелёным, последний шаг — жёлтым, нет данных — нейтрально. */
  function ruNowCls(n) {
    var w = ruNowOf(n);
    if (exitsNoData() && !w.empty) return 'kg-alert kg-alert--plain';
    return w.step === 'exit' ? 'kg-alert kg-alert--ok' : w.step === 'last_direct' || w.step === 'last_block' ? 'kg-alert' : 'kg-alert kg-alert--plain';
  }
  function ruExitName(n, eid) { var r = (n.data.exits || []).filter(function (x) { return x.id === eid; })[0]; return r ? r.name : eid; }
  function ruNowText(n) {
    var w = ruNowOf(n), at = w.since ? t('graph.ru.since', { at: hhmm(w.since) }) : '';
    if (exitsNoData() && !w.empty) return t('graph.nodata');
    if (w.step === 'exit') return t('graph.ru.nowExit', { name: ruExitName(n, w.exit) });
    if (w.step === 'last_direct') return w.empty ? t('graph.ru.emptyDirect') : t('graph.ru.lastDirect', { at: at });
    if (w.step === 'last_block') return w.empty ? t('graph.ru.emptyBlock') : t('graph.ru.lastBlock', { at: at });
    return t('graph.ru.unknown');
  }

  function kindOn(k) { var n = S.idx.node['in:' + k]; return !!(n && n.data.enabled); }
  /* Вход пути устройства: заданный (want), выбранный в «Куда пойдёт трафик?», тот, через который оно на связи, иначе
     первый включённый по порядку выдачи. base — по настройкам сервера, иначе по черновику (отозванный — не путь). */
  function pathInbound(did, want, base) {
    var d = base ? S.idx.dev[did] : S.draft.devices[did]; if (!d) return null;
    var acc = (d.access || []).filter(kindOn);
    if (want && acc.indexOf(want) >= 0) return want;
    if (!base && S.sim && S.sim.who === did && S.sim.inbound && acc.indexOf(S.sim.inbound) >= 0) return S.sim.inbound;
    var ld = S.live.devices[did];
    if (ld && ld.online && acc.indexOf(ld.inbound) >= 0) return ld.inbound;
    return acc[0] || null;
  }
  function liveExits() { return S.idx.exits.filter(function (n) { return n.data.usable && !dupExit(n.data.id); }).map(function (n) { return n.data.id; }); }
// выход через xray (этап 1, T1: вид 'xray', протокол — в proto; 'vless' — вид такого выхода в 3.3.5 и раньше)
function isXrayKind(k) { return k === 'xray' || k === 'vless'; }
function isVless(eid) { var n = S.idx.node['exit:' + eid]; return !!(n && isXrayKind(n.data.kind)); }
  /* Путь устройства: вход, профиль, маршрут и куда трафик идёт сейчас. «Сейчас» сервер знает только для настроек без
     изменений (после «Применить» выход выберет шлюз); base — путь по настройкам сервера (что было до черновика). */
  function devPath(did, want, base) {
    var d = S.idx.dev[did]; if (!d) return null;
    var r = base ? d.effective : effRoute(did), p = base ? [d.profile || defProfile(), d.profile_from || 'default'] : effProfile(did), now = d.now || {};
    var same = !!d.effective && d.effective.via === r.via && d.effective.fail === r.fail;
    var P = { user: d.user, device: did, inbound: pathInbound(did, want, base), profile: p[0], pfrom: p[1], via: r.via, rfrom: r.from, fail: r.fail || 'block',
      target: r.via, exit: null, balancer: false, exits: [], active: !!d.active, changed: !same, category: 'rest' };
    if (!P.active || !P.inbound) { P.category = 'off'; return P; }
    if (same && r.via !== 'direct' && r.via !== 'block') {
      // «сейчас» — ответ сервера (exits.now_of, тот же, что у «Куда пойдёт трафик?», Н-1) для входа, через который он
      // считал (now.inbound — первый выданный); путь через другой вход (на связи через него, выбран в «Через»): вход xray
      // (SOCKS5, VLESS) — любой живой выход, ядро после входа xray — выход назначит шлюз (его hash здесь не считаем)
      // ответ сервера для ЭТОГО входа (scheme._now_by: ядро — выход по hash, xray — любой живой); нет его (вход только в
      // черновике) — правила ниже
      var by = d.now_by && d.now_by[P.inbound];
      if (by) now = by;
      var sameIn = !!by || !now.inbound || now.inbound === P.inbound;
      if (now.target === 'direct' || now.target === 'block') P.target = now.target;      // выходы маршрута не работают: отказ
      else if (!sameIn && xrayIn(P.inbound)) { if (r.via === 'pool') { P.balancer = 'any'; P.exits = liveExits(); } else if (now.exit) P.exit = now.exit; }
      else if (!sameIn && now.balancer === 'any') { /* ядро: выход по hash устройства — назначит шлюз */ }
      else if (now.balancer) { P.balancer = now.balancer === 'any' ? 'any' : 'xray'; P.exits = (now.exits || []).slice(); }
      else if (r.via === 'pool' && now.exit) P.exit = now.exit;
    }
    return P;
  }
  /* Путь нового устройства человека (его правила и маршрут) или нового устройства по умолчанию — без входа. */
  function whoPath(who) {
    if (S.idx.dev[who]) return devPath(who);
    var uid = S.idx.user[who] ? who : null, U = uid ? S.draft.users[uid] : null;
    var r = U && U.route ? U.route : S.draft.def;
    return { user: uid, device: null, inbound: null, profile: uid ? userProfile(uid) : defProfile(), pfrom: U && U.rules ? 'user' : 'default',
      via: r.via, rfrom: U && U.route ? 'user' : 'default', fail: r.fail || 'block', target: r.via, exit: null, balancer: false, exits: [],
      active: true, changed: false, category: 'rest' };
  }
  /* Звенья одного пути — как kaskad.gw.trace.chain: {nodes, edges, rows} (rows — какая строка блока горит). */
  function chainOf(P, category) {
    var c = category || P.category || 'rest', out = { nodes: [], edges: [], rows: {}, alts: [] };
    function n(x) { if (x && out.nodes.indexOf(x) < 0) out.nodes.push(x); }
    function e(x) { if (x && out.edges.indexOf(x) < 0) out.edges.push(x); }
    n(P.user);
    if (c === 'off') return out;
    if (P.device && P.inbound) { e('access:' + P.device + ':' + P.inbound); n('in:' + P.inbound); e('flow:' + P.inbound); }
    n('filter');
    if (c === 'ads' || c === 'torrent') { out.rows.filter = c; return out; }      // отсеивает сам фильтр (М2)
    out.rows.filter = 'pass';
    var prof = P.profile || 'all_exit', rules = 'rules:' + prof;
    e('pass:' + prof); n(rules);
    if (c === 'ru') { out.rows[rules] = 'ru'; e('cat:' + prof + ':ru'); n('direct'); return out; }
    out.rows[rules] = 'rest';
    var via = P.via || 'pool', tgt = P.target || via;
    if (tgt !== via && (tgt === 'direct' || tgt === 'block')) { e('fail:' + prof + ':' + via + ':' + tgt); n(tgt); return out; }
    e('route:' + prof + ':' + via); n(via);
    if (via === 'pool') {
      // выбор делает xray (балансировщик VLESS или SOCKS5): ствол — до пула, ветви — только в конце, к кандидатам (alts)
      if (P.balancer) { if (isXrayKind(P.balancer)) out.rows.pool = 'vless'; (P.exits || []).forEach(function (x) { if (out.alts.indexOf('member:' + x) < 0) out.alts.push('member:' + x); }); }
      else if (P.exit) { out.rows.pool = P.exit; e('member:' + P.exit); n('exit:' + P.exit); }
    }
    return out;
  }
  /* Где звено на холсте: блок и ручка начала, блок конца — из его ид; null — блоков нет на схеме. */
  function linkGeom(id) {
    var I = S.idx, m, g = null, d;
    if ((m = /^access:(d-[0-9a-f]{8}):([a-z0-9]+)$/.exec(id))) { d = I.dev[m[1]]; g = d ? { type: 'conn', s: d.user, sh: 'dv:' + m[1], t: 'in:' + m[2], dev: m[1], kind: m[2] } : null; }
    else if ((m = /^flow:([a-z0-9]+)$/.exec(id))) g = { type: 'flow', s: 'in:' + m[1], sh: 'out', t: 'filter', kind: m[1] };
    else if ((m = /^filter:(ads|torrent)$/.exec(id))) g = { type: 'rule', s: 'filter', sh: 'f:' + m[1], t: 'block', cat: m[1] };
    else if ((m = /^pass:([a-z0-9_]+)$/.exec(id))) g = { type: 'pass', s: 'filter', sh: 'f:pass', t: 'rules:' + m[1], profile: m[1] };
    else if ((m = /^cat:([a-z0-9_]+):ru$/.exec(id))) g = { type: 'rule', s: 'rules:' + m[1], sh: 'c:ru', t: 'direct', cat: 'ru', profile: m[1] };
    else if ((m = /^fail:([a-z0-9_]+):(.+):(direct|block)$/.exec(id))) g = { type: 'fail', s: 'rules:' + m[1], sh: 'c:rest', t: m[3], profile: m[1], via: m[2], fall: m[3] };
    else if ((m = /^route:([a-z0-9_]+):(.+)$/.exec(id))) g = { type: 'route', s: 'rules:' + m[1], sh: 'c:rest', t: m[2], profile: m[1], via: m[2] };
    else if ((m = /^member:(.+)$/.exec(id))) g = { type: 'member', s: 'pool', sh: 'm:' + (isVless(m[1]) ? 'vless' : m[1]), t: 'exit:' + m[1], exit: m[1] };
    else if ((m = /^rulink:(g-[0-9a-f]{6})$/.exec(id))) g = { type: 'ru', s: 'rules:ru_direct', sh: 'c:ru', t: 'rug:' + m[1], group: m[1] };
    if (!g || !I.node[g.s] || !I.node[g.t] || (g.type === 'member' && dupExit(g.exit))) return null;
    g.k = id;
    return g;
  }
  /* Звено в виде (создаётся при первой нужде); did — устройство, чей путь через него идёт. */
  function viewPut(V, id, did) {
    var e = V.byKey[id];
    if (!e) { var g = linkGeom(id); if (!g) return null; g.devs = []; g.state = ''; e = V.byKey[id] = g; V.edges.push(e); }
    if (did && e.devs.indexOf(did) < 0) e.devs.push(did);
    return e;
  }

  function computeView() {
    // повтор ключа пула — только строкой в пуле («не используется»): ни блока, ни линии (ревизия М8)
var V = { nodes: S.base.nodes.filter(function (n) { return !(n.type === 'exit' && dupExit(n.id)); }), byId: S.idx.node, edges: [], byKey: {} }, I = S.idx;
    // пути устройств: у каждого — одна цепочка; одинаковые звенья разных устройств — один пучок с числом устройств.
    // Звено, которого не было в пути устройства до черновика, — «новое» (видно до «Применить»).
    I.devList.forEach(function (d) {
      var P = devPath(d.id); if (!P || P.category === 'off') return;
      var B = devPath(d.id, null, true), was = B && B.category !== 'off' ? chainOf(B).edges : [];
      chainOf(P).edges.forEach(function (id) { var e = viewPut(V, id, d.id); if (e && was.indexOf(id) < 0) e.state = 'new'; });
      // российские сайты — через российскую группу: строка «Сайты и сети России» → группа, только при действующем
      // выборе (T15 Q6, §8.4); у сервера такой линии не было — «новая»
      var ro = ruOf(d.id);
      if (ro && ro.acts && ro.group) { var re = viewPut(V, 'rulink:' + ro.via, d.id); if (re && I.ruEdge[d.id] !== ro.via) re.state = 'new'; }
    });
    // постоянные звенья: фильтр узла → блок, «Россия» → напрямую, пул → выходы
    S.base.edges.forEach(function (e) { if (e.type === 'rule' || e.type === 'member') viewPut(V, e.id); });
    var ruOff = !ruWorks();
    V.edges.forEach(function (e) { if (e.type === 'rule') e.off = e.cat === 'ru' && ruOff; });
    S.view = V;
    traceEdges();
  }
  /* Звенья подсветки, которых нет среди путей (новое устройство, маршрут, по которому ещё никто не идёт), — в вид. */
  function traceEdges() {
    if (!S.trace || !S.view) return;
    Object.keys(S.trace.edges).forEach(function (id) { if (!viewPut(S.view, id)) delete S.trace.edges[id]; });
Object.keys(S.trace.alts || {}).forEach(function (id) { if (!viewPut(S.view, id)) delete S.trace.alts[id]; });
  }
  /* Линия — черновик, если у её устройств после «Применить» другой маршрут или другие правила (Б4: смена профиля видна
     так же, как смена маршрута). */
  /* ── Узлы ───────────────────────────────────────────────────────────── */
  function pt(dir, hid, kind) {
    return h('span', { cls: 'kg-pt kg-pt--' + dir, 'data-dir': dir, 'data-h': hid, 'data-kind': kind || null, 'aria-hidden': 'true' });
  }
  function nodeShell(n, label) {
    return h('div', { cls: 'kg-nd kg-nd--' + n.type, 'data-id': n.id, tabindex: '0', role: 'group', 'aria-label': label });
  }
  function head(icoEl, eyebrow, name, sub, extra) {
    return h('div', { cls: 'kg-nd__head' }, [icoEl, h('div', { cls: 'kg-nd__txt' }, [h('div', { cls: 'kg-nd__eb', text: eyebrow }),
      h('div', { cls: 'kg-nd__name', text: name }), sub != null ? h('div', { cls: 'kg-nd__sub', text: sub, title: String(sub) }) : null])].concat(extra || []));
  }
  function ico(name, cls) { return h('span', { cls: 'kg-nd__ico' + (cls ? ' ' + cls : '') }, icon(name, 17)); }
  /* значок внутренней группы — роутер (порядок в группах, макет kaskad-order В1/В2): метки «РФ» после имени больше нет;
     подсказка — что идёт через группу, роль словами уже рядом (надстрочник «Внутренняя группа» или заголовок списка) */
  function ruIco(size) { return h('span', { cls: 'kg-nd__ico', title: t('graph.ru.tagHint') }, icon('router', size || 17)); }
  /* «по умолчанию» — по черновику: смену маршрута по умолчанию видно до «Применить». */
  function defaultBadge(n) {
    if (!S.draft || S.draft.def.via !== n.id) return null;
    return h('span', { cls: 'kg-badge' + (S.base.default_route && S.base.default_route.via === n.id ? '' : ' is-new'), text: t('graph.default.badge') });
  }
  /* Строки пула: выходы AmneziaWG (и неразобранные) — каждый своей строкой: ядро закрепляет за ним устройства; выходы
     VLESS — одной строкой «балансировщик»: ядро отдаёт трафик xray, а выход среди живых выбирает он (ревизия С6). */
  function poolRows() {
    var ids = S.idx.pool ? S.idx.pool.data.exits || [] : [], out = [], vl = [];
    ids.forEach(function (eid) { if (dupExit(eid)) out.push({ id: eid, dup: true }); else if (isVless(eid)) vl.push(eid); else out.push({ id: eid }); });
    if (vl.length) out.push({ id: 'vless', group: true, exits: vl });
    return out;
  }
  /* Политика отказа у цели маршрута (пул, выход): у скольких устройств при отказе — блок, у скольких — напрямую мимо
     выхода (ревизия С3); по черновику. */
  function failCounts(nid) {
    var c = { block: 0, direct: 0 };
    S.idx.devList.forEach(function (d) { if (!d.active) return; var r = effRoute(d.id); if (r.via === nid) c[r.fail === 'direct' ? 'direct' : 'block'] += 1; });
    return c;
  }
  function footTarget(el, n, noBadge) {
    var c = failCounts(n.id), kids = [];
    if (!noBadge && defaultBadge(n)) kids.push(defaultBadge(n));
    if (c.block) kids.push(h('span', { cls: 'kg-chip', title: t('graph.fail.blockHint'), text: t('graph.fail.chipBlock', { n: c.block }) }));
    if (c.direct) kids.push(h('span', { cls: 'kg-chip kg-chip--warn', title: t('graph.fail.directHint'), text: t('graph.fail.chipDirect', { n: c.direct }) }));
    if (kids.length) el.appendChild(h('div', { cls: 'kg-nd__foot kg-nd__foot--fail' }, kids));
  }
  function profShort(p) { return t('graph.prof.short.' + (p === 'ru_direct' ? 'ru_direct' : 'all_exit')); }

  function buildNode(n) {
    var el, L = S.liveEl[n.id] = {};
    if (n.type === 'user') {
      var u = n.data, U = S.draft.users[n.id] || {};
      el = nodeShell(n, t('graph.user.aria', { name: u.name }));
      L.sub = h('div', { cls: 'kg-nd__sub', text: tn('graph.devices', (u.devices || []).length) });
      el.appendChild(h('div', { cls: 'kg-nd__head' }, [h('span', { cls: 'kg-nd__ava', 'aria-hidden': 'true', text: initials(u.name) }),
        h('div', { cls: 'kg-nd__txt' }, [h('div', { cls: 'kg-nd__eb', text: t('graph.user') }), h('div', { cls: 'kg-nd__name', text: u.name }), L.sub]),
        pt('out', 'u', 'user')]));
      // свои настройки человека — то, что наследуют его устройства без своих
      if (U.route || U.rules) el.appendChild(h('div', { cls: 'kg-dv__chips kg-nd__own' }, [
        U.route ? h('span', { cls: 'kg-dv__rt is-own', title: t('graph.from.user'), text: '→ ' + viaTitle(U.route.via) }) : null,
        U.rules ? h('span', { cls: 'kg-dv__pf is-own', title: t('graph.pfrom.user'), text: profShort(U.rules) }) : null]));
      var devs = h('div', { cls: 'kg-nd__devs' });
      var byId = {}; (u.devices || []).forEach(function (d) { byId[d.id] = d; });
      (S.order[n.id] || []).forEach(function (did) { if (byId[did]) devs.appendChild(devRow(S.idx.dev[did])); });
      if (!(u.devices || []).length) devs.appendChild(h('div', { cls: 'kg-dv-empty', text: t('graph.user.noDevices') }));
      el.appendChild(devs);
      if (!u.enabled) el.classList.add('is-off');
    } else if (n.type === 'inbound') {
      var ib = n.data;
      el = nodeShell(n, t('graph.inbound.aria', { name: ib.title }));
      el.appendChild(pt('in', 'in'));
      el.appendChild(head(ico('plug'), t('graph.inbound'), ib.title, (ib.ports || []).join(' · ') || '—', [pt('out', 'out', 'flow')]));
      L.cnt = h('span', { cls: 'kg-nd__rl', text: '—' }); L.rate = h('span', { cls: 'kg-nd__val', text: '' });
      L.state = h('span', { cls: 'kg-chip', text: '—' });
      el.appendChild(h('div', { cls: 'kg-nd__rows' }, h('div', { cls: 'kg-nd__row' }, [L.cnt, L.rate])));
      el.appendChild(h('div', { cls: 'kg-nd__foot' }, L.state));
      if (!ib.enabled) el.classList.add('is-off');
    } else if (n.type === 'filter') {
      var fd = n.data, frows = h('div', { cls: 'kg-nd__rows' });
      el = nodeShell(n, t('graph.filter.aria'));
      el.appendChild(pt('in', 'in'));
      el.appendChild(head(ico('ban'), t('graph.filter.eyebrow'), t('graph.filter'), t('graph.filter.sub')));
      (fd.rows || []).forEach(function (r) {
        var val = h('span', { cls: 'kg-nd__val', text: r.id === 'pass' ? t('graph.filter.toRules') : r.on ? t('graph.filter.drop') : t('graph.filter.off') });
        frows.appendChild(h('div', { cls: 'kg-nd__row' + (r.on ? '' : ' is-off'), 'data-row': r.id, title: r.id !== 'pass' && r.on ? t('graph.filter.dropHint') : null },
          [h('span', { cls: 'kg-nd__rl', text: r.title, title: r.title }), val, r.id === 'pass' ? pt('out', 'f:pass', 'pass') : null]));
      });
      el.appendChild(frows);
      if (fd.lists === false) el.appendChild(h('div', { cls: 'kg-nd__foot kg-nd__foot--note', text: t('graph.filter.noLists') }));
    } else if (n.type === 'rules') {
      var rd = n.data, rows = h('div', { cls: 'kg-nd__rows' });
      // сколько устройств на профиле — по черновику: смену правил видно до «Применить»
      var onIt = S.draft ? S.idx.devList.filter(function (x) { return effProfile(x.id)[0] === rd.profile; }).length : (rd.devices || 0);
      var ruOff = rd.profile === 'ru_direct' && !ruWorks();
      el = nodeShell(n, t('graph.rules.aria', { name: rd.title }));
      el.appendChild(pt('in', 'in'));
      el.appendChild(head(ico('split'), t('graph.rules'), rd.title, tn('graph.devices', onIt)));
      (rd.categories || []).forEach(function (c) {
        var off = ruOff && c.target === 'direct';
        var val = h('span', { cls: 'kg-nd__val', text: off ? t('graph.rules.ruOff') : c.target === 'route' ? t('graph.rules.toRoute') : c.target === 'direct' ? t('graph.rules.toDirect') : t('graph.rules.toBlock') });
        L['c:' + c.id] = val;
        rows.appendChild(h('div', { cls: 'kg-nd__row' + (off ? ' is-off' : '') + (c.note ? ' kg-nd__row--2l' : ''), 'data-row': c.id }, [
          h('span', { cls: 'kg-nd__rl', title: c.title }, [c.title, c.note ? h('small', { cls: 'kg-nd__rn', text: c.note }) : null]), val, pt('out', 'c:' + c.id, 'rule')]));
      });
      el.appendChild(rows);
      var note = !rd.supported ? (S.base.profiles.soon || t('graph.rules.soon')) : rd.profile === 'ru_direct' ? ruNoteText(rd) : '';
      if (!note && !onIt) note = t('graph.rules.unused');
      if (note) el.appendChild(h('div', { cls: 'kg-nd__foot kg-nd__foot--note', text: note }));
      if (rd.supported && rd.profile === defProfile()) el.appendChild(h('div', { cls: 'kg-nd__foot' }, h('span', { cls: 'kg-badge', text: t('graph.default.badge') })));
      if (!onIt) el.classList.add('is-unused');
    } else if (n.type === 'pool') {
      var prow = h('div', { cls: 'kg-nd__rows' });
      el = nodeShell(n, t('graph.pool.aria'));
      el.appendChild(pt('in', 'in'));
      L.sub = h('div', { cls: 'kg-nd__sub', text: tn('graph.devices', 0) });
      el.appendChild(h('div', { cls: 'kg-nd__head' }, [ico('pool'), h('div', { cls: 'kg-nd__txt' }, [h('div', { cls: 'kg-nd__eb', text: t('graph.pool.eyebrow') }),
        h('div', { cls: 'kg-nd__name', text: t('graph.pool') }), L.sub])]));
      poolRows().forEach(function (r, i) {
        // строка балансировщика — в две строки: название и «живых n из m · устройств» под ним (иначе обрезается)
        // каждая строка — в две строки: имя и под ним «работает · закреплено n · на связи m» (в одну не помещалось, Н4)
        var val = h('small', { cls: 'kg-nd__rn', text: '' });
        L['m:' + r.id] = val;
        prow.appendChild(h('div', { cls: 'kg-nd__row kg-nd__mem kg-nd__row--2l' + (r.group ? ' kg-nd__mem--grp' : ''), 'data-row': r.id, 'data-exit': r.group ? null : r.id }, [
          h('span', { cls: 'kg-nd__idx', text: r.group ? '⇄' : String(i + 1) }),
          h('span', { cls: 'kg-nd__rl' }, [r.group ? t('graph.pool.vless', { n: r.exits.length }) : nodeTitle('exit:' + r.id), val]),
          r.dup ? null : pt('out', 'm:' + r.id, 'member')]));
      });
      if (!poolRows().length) prow.appendChild(h('div', { cls: 'kg-nd__row kg-nd__row--note', text: t('graph.pool.empty') }));
      el.appendChild(prow);
      footTarget(el, n);
    } else if (n.type === 'ru_group') {
      // внутренняя группа (T15 Q6, §8.4): значок роутера, ключи — по шагам внутри (цели маршрута они не бывают), в конце —
      // последний шаг; «сейчас» — под именем
      var rg = n.data, rrows = h('div', { cls: 'kg-nd__rows' });
      el = nodeShell(n, t('graph.ru.aria', { name: rg.name }));
      el.appendChild(pt('in', 'in'));
      L.sub = h('div', { cls: 'kg-nd__sub', text: '' });
      el.appendChild(h('div', { cls: 'kg-nd__head' }, [ruIco(), h('div', { cls: 'kg-nd__txt' }, [h('div', { cls: 'kg-nd__eb', text: t('graph.ru.eyebrow') }),
        h('div', { cls: 'kg-nd__nm' }, [h('div', { cls: 'kg-nd__name', text: rg.name })]), L.sub])]));
      (rg.exits || []).forEach(function (r) {
        var val = h('small', { cls: 'kg-nd__rn', text: '' });
        L['x:' + r.id] = val;
        rrows.appendChild(h('div', { cls: 'kg-nd__row kg-nd__rex kg-nd__row--2l', 'data-row': r.id }, [h('span', { cls: 'kg-nd__idx', text: String(r.step) }),
          h('span', { cls: 'kg-nd__rl' }, [r.name, val])]));
      });
      rrows.appendChild(h('div', { cls: 'kg-nd__row kg-nd__row--note', text: rg.exits && rg.exits.length ? t(rg.fail === 'block' ? 'graph.ru.endBlock' : 'graph.ru.endDirect') : t('graph.ru.noKeys') }));
      el.appendChild(rrows);
      L.state = h('span', { cls: 'kg-chip', text: '' });
      L.foot = h('div', { cls: 'kg-nd__foot', hidden: true }, L.state);
      el.appendChild(L.foot);
      var mine = S.idx.devList.filter(function (x) { var o = ruOf(x.id); return x.active && o && o.acts && o.via === rg.group; }).length;
      if (!mine) el.classList.add('is-unused');
    } else if (n.type === 'exit') {
      var e = n.data;
      el = nodeShell(n, t('graph.exit.aria', { name: e.name || e.id }));
      el.appendChild(pt('in', 'in'));
      // ключ без своего имени: имя — «вид · сервер:порт», адрес под ним его не повторяет (№17 живых проверок 3.3.0)
      var unnamedExit = !!e.where && e.name === e.where;
      el.appendChild(head(ico('globe'), dupExit(e.id) ? t('graph.exit.kind.dup') : t('graph.exit.kind.' + (e.kind || 'bad')), e.name || e.id, unnamedExit ? '' : (e.host || (e.missing ? t('graph.exit.missingSub') : ''))));
      L.rate = h('span', { cls: 'kg-nd__val', text: '—' }); L.cnt = h('span', { cls: 'kg-nd__val', text: '—' });
      L.on = h('span', { cls: 'kg-nd__val', text: '—' });
      // «закреплено» — чей путь сейчас кончается здесь (на связи или нет), «на связи» — из них живые (ревизия С7, Н4)
      el.appendChild(h('div', { cls: 'kg-nd__rows kg-nd__rows--2' }, [
        h('div', { cls: 'kg-nd__row', title: isXrayKind(e.kind) ? t('graph.exit.byBalancerHint') : t('graph.pin.hint') }, [h('span', { text: t('graph.pin') }), L.cnt]),
        h('div', { cls: 'kg-nd__row', title: t('graph.onNow.hint') }, [h('span', { text: t('graph.onNow') }), L.on]),
        h('div', { cls: 'kg-nd__row kg-nd__row--wide', title: t('graph.rate.hint') }, [h('span', { text: t('graph.rate') }), L.rate])]));
      L.state = h('span', { cls: 'kg-chip', text: '—' });
      L.cand = h('span', { cls: 'kg-chip kg-chip--accent', hidden: true, title: t('graph.exit.candHint'), text: t('graph.exit.cand') });
el.appendChild(h('div', { cls: 'kg-nd__foot' }, [L.state, L.cand, isXrayKind(e.kind) ? h('span', { cls: 'kg-chip', title: t('graph.exit.byBalancerHint'), text: t('graph.exit.byBalancer') }) : null, defaultBadge(n)]));
      footTarget(el, n, true);
    } else {
      var isDirect = n.type === 'direct';
      el = nodeShell(n, isDirect ? t('graph.direct') : t('graph.block'));
      el.appendChild(pt('in', 'in'));
      el.appendChild(head(ico(isDirect ? 'house' : 'ban'), t('graph.target'), isDirect ? t('graph.direct') : t('graph.block'),
        isDirect ? t('graph.direct.sub') : t('graph.block.sub')));
      L.rate = h('span', { cls: 'kg-nd__val', text: '—' }); L.cnt = h('span', { cls: 'kg-nd__val', text: '—' });
      L.on = h('span', { cls: 'kg-nd__val', text: '—' });
      el.appendChild(h('div', { cls: 'kg-nd__rows kg-nd__rows--2' }, [
        h('div', { cls: 'kg-nd__row', title: t('graph.pin.hint') }, [h('span', { text: t('graph.pin') }), L.cnt]),
        h('div', { cls: 'kg-nd__row', title: t('graph.onNow.hint') }, [h('span', { text: t('graph.onNow') }), L.on]),
        isDirect ? h('div', { cls: 'kg-nd__row kg-nd__row--wide', title: t('graph.rate.directHint') }, [h('span', { text: t('graph.rate') }), L.rate]) : null]));
      if (defaultBadge(n)) el.appendChild(h('div', { cls: 'kg-nd__foot' }, defaultBadge(n)));
    }
    return el;
  }

  function devRow(d) {
    var D = S.draft.devices[d.id], r = effRoute(d.id), p = effProfile(d.id), path = pathInbound(d.id);
    var chips = h('span', { cls: 'kg-dv__chips' });
    var kinds = d.access.slice(); D.access.forEach(function (k) { if (kinds.indexOf(k) < 0) kinds.push(k); });
    // входы — способы подключения (плашки); путь идёт через один из них — он отмечен
    kinds.forEach(function (k) {
      var inDraft = D.access.indexOf(k) >= 0, inBase = d.access.indexOf(k) >= 0, miss = !S.idx.node['in:' + k], off = !miss && !kindOn(k);
      chips.appendChild(h('span', { cls: 'kg-pc' + (miss || off ? ' is-bad' : '') + (!inDraft ? ' is-gone' : !inBase ? ' is-new' : '') + (k === path && d.active ? ' is-path' : ''), 'data-kind': k,
        title: miss ? t('graph.dev.kindMissing') : off ? t('graph.dev.kindOff') : k === path ? t('graph.dev.pathInbound') : t('graph.dev.otherInbound'), text: shortKind(k) }));
    });
    if (!kinds.length) chips.appendChild(h('span', { cls: 'kg-pc is-bad', text: t('graph.dev.noAccess') }));
    else if (!path && d.active) chips.appendChild(h('span', { cls: 'kg-pc is-bad', text: t('graph.dev.noPath') }));
    if (!d.active) chips.appendChild(h('span', { cls: 'kg-pc is-bad', text: t('graph.dev.off') }));
    chips.appendChild(h('span', { cls: 'kg-dv__rt' + (r.from === 'device' ? ' is-own' : ''), title: t('graph.from.' + r.from), text: '→ ' + viaTitle(r.via) }));
    if (S.base.profiles && S.base.profiles.supported) chips.appendChild(h('span', { cls: 'kg-dv__pf' + (p[1] === 'device' ? ' is-own' : ''), title: t('graph.pfrom.' + p[1]), text: profShort(p[0]) }));
    // российские сайты — через российскую группу (T15 Q6): только когда группа выбрана; не действует — так и сказано
    var ro = ruOf(d.id), rgn = ro && ro.group ? S.idx.node['rug:' + ro.via] : null;
    if (rgn) chips.appendChild(h('span', { cls: 'kg-dv__ru' + (ro.from === 'device' ? ' is-own' : '') + (ro.acts ? '' : ' is-idle'), title: ro.acts ? t('graph.ru.chipHint') : t('graph.ru.onlyRd'),
      text: t(ro.acts ? 'graph.ru.chip' : 'graph.ru.chipOff', { name: rgn.data.name }) }));
    var L = S.rowEl[d.id] = {};
    L.dot = h('span', { cls: 'kg-dv__dot' });
    L.val = h('span', { cls: 'kg-dv__v', text: '' });
    L.row = h('div', { cls: 'kg-dv' + (d.active ? '' : ' is-off'), 'data-dev': d.id, tabindex: '0', role: 'button', 'aria-label': devAria(d) }, [
      h('span', { cls: 'kg-dv__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 15)),
      h('span', { cls: 'kg-dv__main' }, [h('span', { cls: 'kg-dv__top' }, [L.dot, h('b', { cls: 'kg-dv__n', text: d.name }), L.val]), chips]),
      pt('out', 'dv:' + d.id, 'dev')
    ]);
    return L.row;
  }
  function shortKind(k) { var m = { awg1: 'AWG 1.5', awg2: 'AWG 2.0', awg3: 'AWG 3.1', wg: 'WG', l2tp: 'L2TP', pptp: 'PPTP', sstp: 'SSTP', ikev2: 'IKEv2', openvpn: 'OpenVPN', ocserv: 'OpenConnect', socks5: 'SOCKS5', vless: 'VLESS', hy2: 'Hysteria2' }; return m[k] || k; }
  /* Вход xray шлюза (model.XRAY_KINDS): SOCKS5, VLESS Reality и Hysteria2 — без адресов, путь решает xray (пул: любой живой выход).
     Не путать с ВЫХОДАМИ VLESS пула (exit kind 'vless', балансировщик 'vless'). */
  function xrayIn(k) { return k === 'socks5' || k === 'vless' || k === 'hy2'; }
  function devAria(d) { return t('graph.dev.aria', { name: d.name, kind: t('graph.kind.' + (d.kind || 'other')), user: nodeTitle(d.user) }); }

  function renderNodes() {
    var box = S.dom.nodes;
    clear(box);
    S.nodeEl = {}; S.liveEl = {}; S.rowEl = {};
    S.view.nodes.forEach(function (n) {
      var el = buildNode(n);
      S.nodeEl[n.id] = el;
      // 3.3.3 (T-333 №4): мелкий масштаб (вся схема) — вместо нечитаемых строк крупное имя блока
      var nm = el.querySelector('.kg-nd__name');
      if (nm) el.appendChild(h('div', { cls: 'kg-nd__far', 'aria-hidden': 'true' }, [h('span', { cls: 'kg-nd__farnm', text: nm.textContent }), h('span', { cls: 'kg-nd__farst', hidden: true })]));
      var p = S.pos[n.id] || { x: 0, y: 0 };
      el.style.left = p.x + 'px'; el.style.top = p.y + 'px';
      box.appendChild(el);
    });
    measure();
    markSelection();
    farMarks();
  }
  /* 3.3.3 (R-333 №1): в общем виде строки блока спрятаны — состояние не должно пропадать вместе с ними. Худшая метка блока
     («не работает», «отказ → напрямую», мёртвый выход в пуле) — крупно под именем, рамка блока — её цветом. */
  function farMarks() {
    Object.keys(S.nodeEl).forEach(function (id) {
      var el = S.nodeEl[id], st = el.querySelector('.kg-nd__farst'); if (!st) return;
      var bad = [].slice.call(el.querySelectorAll('.kg-chip--bad')), warn = [].slice.call(el.querySelectorAll('.kg-chip--warn'));
      // строки пула: живость ставится на саму строку .kg-nd__mem
      var mem = el.querySelectorAll('.kg-nd__mem.is-down').length, memOk = el.querySelectorAll('.kg-nd__mem.is-active').length;
      var sub = el.querySelector('.kg-nd__sub'), txt = [];
      bad.forEach(function (c) { txt.push(c.textContent); });
      if (mem && sub) txt.push(sub.textContent);
      warn.forEach(function (c) { txt.push(c.textContent); });
      txt = txt.filter(function (x, i) { return x && txt.indexOf(x) === i; }).slice(0, 2);
      var lvl = bad.length || el.classList.contains('is-down') || (mem && !memOk) ? 'bad' : mem || warn.length ? 'warn' : '';
      el.classList.toggle('is-far-bad', lvl === 'bad'); el.classList.toggle('is-far-warn', lvl === 'warn');
      st.className = 'kg-nd__farst' + (lvl ? ' is-' + lvl : '');
      setText(st, txt.join(' · ')); st.hidden = !lvl || !txt.length;
    });
  }

  /* Размеры блоков и точки подключения относительно блока (как offIn/measurePorts прототипа). */
  function offIn(el, anc) { var x = 0, y = 0; while (el && el !== anc) { x += el.offsetLeft; y += el.offsetTop; el = el.offsetParent; } return { x: x + anc.clientLeft, y: y + anc.clientTop }; }
  /* Текст живых цифр может сменить высоту блока (перенос строки): тогда перемерить точки и линии. */
  function remeasureIfResized() {
    if (S.mode !== 'canvas') return;
    var changed = Object.keys(S.nodeEl).some(function (id) { var m = S.ports[id]; return !m || m.h !== S.nodeEl[id].offsetHeight || m.w !== S.nodeEl[id].offsetWidth; });
    if (changed) { measure(); if (!deOverlap()) layoutEdges(); }
  }
  /* Карточки не накладываются (Н12): высоты меняются, когда приходят живые цифры, — после «Выровнять» и при первой
     раскладке тоже. Нижняя из двух перекрытых (по горизонтали и вертикали) уходит под верхнюю; позиции сохраняются. */
  function deOverlap() {
    if (S.mode !== 'canvas') return false;
    var moved = overlapFix(S.view.nodes.map(function (n) { return n.id; }));
    if (moved.length) { moved.forEach(function (id) { S.dirtyPos[id] = 1; }); applyPositions(); layoutEdges(); later('save', SAVE_DELAY, saveLayout); }
    return moved.length > 0;
  }
  /* Сдвинуть вниз карточки, наложенные на верхние (по размерам S.ports): -> сдвинутые ид. */
  function overlapFix(all) {
    var ids = all.filter(function (id) { return S.pos[id]; }), moved = [];
    for (var pass = 0; pass < 4; pass++) {
      var again = false;
      ids.sort(function (a, b) { return S.pos[a].y - S.pos[b].y; });
      for (var i = 0; i < ids.length; i++) for (var j = 0; j < i; j++) {
        var a = S.pos[ids[j]], b = S.pos[ids[i]];
        var xo = a.x < b.x + nodeW(ids[i]) && b.x < a.x + nodeW(ids[j]), need = a.y + nodeH(ids[j]) + GAP_Y / 2;
        if (xo && b.y < need && b.y + nodeH(ids[i]) > a.y) { S.pos[ids[i]] = { x: b.x, y: Math.round(need) }; if (moved.indexOf(ids[i]) < 0) moved.push(ids[i]); again = true; }
      }
      if (!again) break;
    }
    return moved;
  }
  function measure() {
    S.ports = {};
    Object.keys(S.nodeEl).forEach(function (id) {
      var el = S.nodeEl[id], m = S.ports[id] = { w: el.offsetWidth, h: el.offsetHeight, p: {} };
      Array.prototype.forEach.call(el.querySelectorAll('.kg-pt'), function (p) {
        var o = offIn(p, el);
        m.p[p.getAttribute('data-dir') + ':' + p.getAttribute('data-h')] = { x: o.x + p.offsetWidth / 2, y: o.y + p.offsetHeight / 2 };
      });
    });
  }
  function nodeW(id) { var m = S.ports[id]; return m && m.w ? m.w : 220; }
  function nodeH(id) { var m = S.ports[id]; return m && m.h ? m.h : 100; }
  function portOff(id, dir, hid) { var m = S.ports[id], p = m && m.p[dir + ':' + hid]; return p ? p.y : nodeH(id) / 2; }
  function portXY(id, dir, hid) {
    var n = S.pos[id], m = S.ports[id];
    if (!n || !m) return null;
    var p = m.p[dir + ':' + hid] || (dir === 'in' ? { x: -8, y: m.h / 2 } : { x: m.w + 8, y: m.h / 2 });
    return { x: n.x + p.x, y: n.y + p.y };
  }
  function applyPositions() {
    Object.keys(S.nodeEl).forEach(function (id) { var p = S.pos[id] || { x: 0, y: 0 }, el = S.nodeEl[id]; el.style.left = p.x + 'px'; el.style.top = p.y + 'px'; });
  }
  function moveNodeEl(id) { var el = S.nodeEl[id], p = S.pos[id]; if (el && p) { el.style.left = p.x + 'px'; el.style.top = p.y + 'px'; } }

  /* Новые блоки (сервер их ещё не раскладывал) — в свою колонку, под остальными. */
  function placeNew(ids) {
    var colX = {}, bottom = {};
    S.view.nodes.forEach(function (n) {
      if (ids.indexOf(n.id) >= 0 || !S.pos[n.id]) return;
      var l = LAYER[n.type], p = S.pos[n.id];
      colX[l] = colX[l] == null ? p.x : Math.min(colX[l], p.x);
      bottom[l] = Math.max(bottom[l] || 0, p.y + nodeH(n.id) + GAP_Y);
    });
    ids.forEach(function (id) {
      var n = S.idx.node[id]; if (!n) return;
      var l = LAYER[n.type];
      var x = colX[l] != null ? colX[l] : 24 + l * (240 + GAP_X);
      S.pos[id] = { x: x, y: bottom[l] || 24 };
      bottom[l] = S.pos[id].y + nodeH(id) + GAP_Y;
      S.dirtyPos[id] = 1;
    });
    later('save', SAVE_DELAY, saveLayout);
  }

  /* ── Связи ──────────────────────────────────────────────────────────── */
  function curve(a, b) {
    var dx = Math.max(40, Math.abs(b.x - a.x) * 0.5);
    return 'M' + a.x.toFixed(1) + ',' + a.y.toFixed(1) + ' C' + (a.x + dx).toFixed(1) + ',' + a.y.toFixed(1) + ' ' + (b.x - dx).toFixed(1) + ',' + b.y.toFixed(1) + ' ' + b.x.toFixed(1) + ',' + b.y.toFixed(1);
  }
  /* 3.3.3 (T-333 №4): подпись связи — на свободном месте кривой, не под блоком. Точки кривой от середины к концам;
     не влезает в одну строку — две строки («3 устройства» / «711 кбит/с»); места нет совсем — середина, как раньше. */
  function bez(a, b, t) {
    var dx = Math.max(40, Math.abs(b.x - a.x) * 0.5), u = 1 - t;
    return { x: u * u * u * a.x + 3 * u * u * t * (a.x + dx) + 3 * u * t * t * (b.x - dx) + t * t * t * b.x,
      y: u * u * u * a.y + 3 * u * u * t * a.y + 3 * u * t * t * b.y + t * t * t * b.y };
  }
  function nodeBoxes() {
    var out = [];
    S.view.nodes.forEach(function (n) { var p = S.pos[n.id]; if (p) out.push({ x1: p.x - 1, y1: p.y - 1, x2: p.x + nodeW(n.id) + 1, y2: p.y + nodeH(n.id) + 1 }); });
    return out;
  }
  function labelSpot(lab, a, b, boxes, taken) {
    var TS = [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82];
    if (lab.hidden) return mid(a, b);
    var free = function (c, w, hh) {
      var r = { x1: c.x - w / 2, y1: c.y - hh / 2, x2: c.x + w / 2, y2: c.y + hh / 2 };
      var hit = function (q) { return r.x1 < q.x2 && q.x1 < r.x2 && r.y1 < q.y2 && q.y1 < r.y2; };
      return !boxes.some(hit) && !taken.some(hit) ? r : null;
    };
    for (var pass = 0; pass < 2; pass++) {
      lab.classList.toggle('is-2l', pass === 1);
      var w = lab.offsetWidth, hh = lab.offsetHeight;
      for (var i = 0; i < TS.length; i++) {
        var c = bez(a, b, TS[i]), r = free(c, w, hh);
        if (r) { taken.push(r); return c; }
      }
    }
    lab.classList.remove('is-2l');
    return mid(a, b);
  }
  function mid(a, b) {
    var dx = Math.max(40, Math.abs(b.x - a.x) * 0.5), p1 = { x: a.x + dx, y: a.y }, p2 = { x: b.x - dx, y: b.y };
    return { x: (a.x + 3 * p1.x + 3 * p2.x + b.x) / 8, y: (a.y + 3 * p1.y + 3 * p2.y + b.y) / 8 };
  }
  /* «Только активные»: пути устройств, которые на связи сейчас (и подсвеченные, и выбранное); постоянные звенья — всегда. */
  /* «Выделить активные» (ревизия М3): пути устройств не на связи не прячутся (узлы оставались без связей), а
     приглушаются; живые, подсвеченные и выбранное — как были. */
  function edgeLive(e) {
    if (!S.onlyActive || !e.devs || !e.devs.length || e.state) return true;
    if (e.devs.some(function (d) { return devOnline(d); })) return true;
    if (S.trace && S.trace.edges[e.k]) return true;
    return !!(S.sel && ((S.sel.t === 'dev' && e.devs.indexOf(S.sel.id) >= 0) || (S.sel.t === 'node' && (S.sel.id === e.t || S.sel.id === e.s))));
  }

  /* Пересобрать слой связей — только при изменении структуры (данные, черновик, видимость), не на каждый тик. */
  function renderEdges() {
    var g = S.dom.gEdges;
    clear(g); clear(S.dom.labels);
    S.edgeEl = {}; S.labelEl = {};
    var order = { member: 0, rule: 1, ru: 1, fail: 2, route: 3, pass: 4, flow: 5, conn: 6 };
    S.view.edges.slice().sort(function (a, b) { return (order[a.type] || 0) - (order[b.type] || 0); }).forEach(function (e) {
      var base = svgEl('path', { 'class': 'kg-e__base' }), hit = svgEl('path', { 'class': 'kg-e__hit', 'data-k': e.k });
      var dot = svgEl('circle', { 'class': 'kg-e__dot', r: 3.2 });
      var el = svgEl('g', { 'class': edgeClass(e), 'data-k': e.k });
      el.appendChild(hit); el.appendChild(base); el.appendChild(dot);
      g.appendChild(el);
      S.edgeEl[e.k] = { g: el, base: base, hit: hit, dot: dot };
      if (e.type !== 'rule') {
        var lab = h('div', { cls: 'kg-elab', hidden: true });
        S.dom.labels.appendChild(lab);
        S.labelEl[e.k] = lab;
      }
    });
    layoutEdges();
    updateEdgesLive();
  }
  function edgeClass(e) {
    var c = 'kg-e kg-e--' + e.type;
    if (e.state === 'new') c += ' is-new';
    if (e.type === 'fail') c += ' is-fail-' + e.fall;
    if (e.off) c += ' is-off';
if (!edgeLive(e)) c += ' is-idle';
    if (S.sel && S.sel.t === 'edge' && S.sel.id === e.k) c += ' is-sel';
    if (S.trace && S.trace.edges[e.k]) c += ' is-trace';
    else if (S.trace && S.trace.alts && S.trace.alts[e.k]) c += ' is-alt';
    return c;
  }

  /* Геометрия: концы линий у блока — рядом друг с другом в порядке источников (прототип: spreadEnds). */
  function layoutEdges() {
    if (S.mode !== 'canvas') return;
    var list = [];
    S.view.edges.forEach(function (e) {
      if (!S.edgeEl[e.k]) return;
      var a = portXY(e.s, 'out', e.sh), b = portXY(e.t, 'in', 'in');
      if (a && b) list.push({ e: e, a: a, b: b });
    });
    var byT = {}, byS = {};
    list.forEach(function (o) { (byT[o.e.t] = byT[o.e.t] || []).push(o); var sk = o.e.s + '|' + o.e.sh; (byS[sk] = byS[sk] || []).push(o); });
    Object.keys(byT).forEach(function (id) {
      var grp = byT[id], p = S.pos[id], hh = nodeH(id), cnt = grp.length;
      if (!p) return;
      grp.sort(function (x, y) { return (x.a.y - y.a.y) || (x.a.x - y.a.x); });
      var step = cnt > 1 ? Math.min(22, Math.max(4, (hh - 28) / (cnt - 1))) : 0, start = p.y + hh / 2 - step * (cnt - 1) / 2;
      grp.forEach(function (o, i) { o.b = { x: p.x - 2, y: start + i * step }; });
    });
    Object.keys(byS).forEach(function (k) {
      var grp = byS[k]; if (grp.length < 2) return;
      grp.sort(function (x, y) { return x.b.y - y.b.y; });
      var step = Math.min(8, 24 / (grp.length - 1)), c = grp[0].a.y;
      grp.forEach(function (o, i) { o.a = { x: o.a.x, y: c + (i - (grp.length - 1) / 2) * step }; });
    });
    var boxes = nodeBoxes(), taken = [];
    list.forEach(function (o) {
      var r = S.edgeEl[o.e.k], d = curve(o.a, o.b);
      r.base.setAttribute('d', d); r.hit.setAttribute('d', d);
      r.dot.setAttribute('cx', o.b.x.toFixed(1)); r.dot.setAttribute('cy', o.b.y.toFixed(1));
      var lab = S.labelEl[o.e.k];
      if (lab) { var m = labelSpot(lab, o.a, o.b, boxes, taken); lab.style.left = m.x.toFixed(1) + 'px'; lab.style.top = m.y.toFixed(1) + 'px'; }
    });
    if (S.gesture && S.gesture.type === 'connect') drawTemp();
  }
  function scheduleEdges() {
    if (S.raf) return;
    var run = function () { if (!S || !S.raf) return; S.raf = 0; layoutEdges(); };
    S.raf = requestAnimationFrame(run) || 1;
    setTimeout(run, 150);                                  // кадр не идёт в скрытом окне — запасной таймер
  }

  /* ── Живые цифры ────────────────────────────────────────────────────── */
  function onLive(p) {
    if (typeof p === 'string') { try { p = JSON.parse(p); } catch (_) { return; } }
    if (!p || typeof p !== 'object') return;
    if (!p.devices && !p.exits && !p.inbounds) {                // обёртка события: {live: …} или {type: 'live', data: …}
      if (p.live && typeof p.live === 'object') p = p.live;
      else if (p.data && typeof p.data === 'object') p = p.data;
    }
    var exitsBefore = {};
    Object.keys(S.live.exits).forEach(function (k) { exitsBefore[k] = !!S.live.exits[k].alive; });
    mergeLive(p, !!p.full);
    if (!S.base) return;
    // выход ожил или умер, появилось незнакомое устройство — сервер пересчитает, куда идёт трафик
    var structural = false;
    Object.keys(p.exits || {}).forEach(function (k) { var v = S.live.exits[k]; if (v && OWN.call(exitsBefore, k) && exitsBefore[k] !== !!v.alive) structural = true; });
    Object.keys(p.devices || {}).forEach(function (k) { if (!S.idx.dev[k] && p.devices[k]) structural = true; });
    if (structural) scheduleRefetch();
    updateLive();
  }
  /* JSON merge patch (RFC 7386): null — убрать ключ, объекты — слить, остальное — заменить. */
  function mergePatch(target, patch) {
    if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) return patch;
    if (target === null || typeof target !== 'object' || Array.isArray(target)) target = {};
    for (var k in patch) {
      if (!OWN.call(patch, k)) continue;
      if (patch[k] === null) delete target[k];
      else target[k] = mergePatch(target[k], patch[k]);
    }
    return target;
  }
  /* Событие live: первое — {"full": true, …} (весь live.json), дальше — merge patch по RFC 7386.
     Часы — ts сборщика (часы узла: их же несёт live из GET /api/scheme), не браузера. Полное событие без ts —
     {"full": true}: свежих данных нет (сборщик молчит, live.json старше 30 с, или его ещё нет) — «нет данных»,
     а не «0 на связи» по старому снимку. */
  function mergeLive(p, full) {
    var L = S.live, ts = typeof p.ts === 'number' ? p.ts : null, seen = {};
    ['devices', 'exits', 'inbounds', 'groups'].forEach(function (sec) {
      if (!OWN.call(p, sec) && !full) return;
      var src = p[sec];
      if (full || src === null) L[sec] = {};
      if (!src || typeof src !== 'object') return;
      L[sec] = mergePatch(L[sec], src);
      if (sec === 'devices') Object.keys(src).forEach(function (id) { if (src[id] !== null) seen[id] = 1; });
    });
    if (full && ts == null) { L.ts = null; S.rates = {}; S.prev = {}; S.liveAt = 0; return; }
    if (ts == null) ts = L.ts;                           // патч без ts: часы прежние
    if (ts == null) return;                              // патчу не на что лечь (полного события ещё не было)
    var advanced = L.ts == null || ts > L.ts;
    // скорость: разница счётчиков rx+tx между снимками; устройство не пришло в разнице — счётчики не менялись
    Object.keys(L.devices).forEach(function (id) {
      var v = L.devices[id], b = (typeof v.rx === 'number' && typeof v.tx === 'number') ? v.rx + v.tx : null, pr = S.prev[id];
      if (b == null) { S.rates[id] = null; delete S.prev[id]; return; }
      if (!pr) { S.prev[id] = { b: b, t: ts }; if (!(id in S.rates)) S.rates[id] = v.online ? null : 0; return; }
      if (seen[id] || full) {
        if (b < pr.b || ts <= pr.t) { S.prev[id] = { b: b, t: ts }; return; }
        S.rates[id] = (b - pr.b) * 8 / (ts - pr.t);
        S.prev[id] = { b: b, t: ts };
      } else if (advanced) S.rates[id] = 0;
      if (!v.online) S.rates[id] = 0;
    });
    if (advanced) L.ts = ts;
    S.liveAt = Date.now();
  }
  function liveFresh() { return S.live.ts != null && (Date.now() - S.liveAt) / 1000 < LIVE_STALE; }
  // live.json пишет состояние выхода «ok|dead|unknown», /api/scheme — «alive|dead|unknown»: схема говорит словами /api/scheme
  function liveExitSt(le) { if (!le) return null; return le.state === 'ok' ? 'alive' : le.state || (le.alive === true ? 'alive' : null); }
  /* Сборщик молчит на включённом шлюзе: состояние выходов — тоже его (сторож пишет exits.json), снимок — прошлая
     проверка, а не сейчас: «нет данных», а не «работает» (T-G1b, N2). */
  function exitsNoData() { return !liveFresh() && !!(S.base && S.base.gateway && S.base.gateway.enabled); }
  function exitSt(id, st) { return exitsNoData() ? 'nodata' : (liveExitSt(S.live.exits[id]) || st); }
  function devRate(did) { if (!liveFresh()) return null; var r = S.rates[did]; return r == null ? null : r; }
  /* Сумма скоростей устройств на связи; никого нет на связи — 0 (если данные свежие), скорость ещё не известна — null. */
  function sumRates(dids) {
    var sum = 0, known = false, anyOn = false;
    dids.forEach(function (d) {
      if (!devOnline(d)) return;
      anyOn = true;
      var r = devRate(d);
      if (r != null) { sum += r; known = true; }
    });
    if (!anyOn) return liveFresh() ? 0 : null;
    return known ? sum : null;
  }
  function devOnline(did) { var v = S.live.devices[did]; return v ? !!v.online : null; }

  /* Обновить цифры на месте: тексты, классы, толщину линий — без перестройки узлов и SVG. */
  function updateLive() {
    if (!S.base) return;
    var I = S.idx, fresh = liveFresh();
    I.devList.forEach(function (d) {
      var L = S.rowEl[d.id], on = devOnline(d.id);
      if (!L) return;
      L.row.classList.toggle('is-on', !!on);
      setText(L.val, on ? fmtRate(devRate(d.id)) : on === false ? t('graph.dev.offline') : '');
      var ld = S.live.devices[d.id];
      Array.prototype.forEach.call(L.row.querySelectorAll('.kg-pc[data-kind]'), function (c) { c.classList.toggle('is-on', !!(on && ld && ld.inbound === c.getAttribute('data-kind'))); });
    });
    I.users.forEach(function (u) {
      var L = S.liveEl[u.id]; if (!L || !L.sub) return;
      var ds = u.data.devices || [], onl = ds.filter(function (d) { return devOnline(d.id); }).length;
      setText(L.sub, tn('graph.devices', ds.length) + (fresh ? ' · ' + tn('graph.online', onl) : ''));
    });
    // «сейчас» — по серверу: выход, через который устройство идёт, или балансировщик VLESS (тогда выхода не назвать)
    var nowExit = function (eid) { return I.devList.filter(function (d) { return d.active && d.now && d.now.exit === eid; }).map(function (d) { return d.id; }); };
    // строка балансировщика VLESS — только устройства ядра, которых hash отдал VLESS («vless»); SOCKS5 и вход VLESS («any») выбирает
    // xray среди всех живых — ни за одним выходом и ни за балансировщиком VLESS не закреплён (Н-1)
    var balanced = I.devList.filter(function (d) { return d.active && d.now && (d.now.balancer === 'xray' || d.now.balancer === 'vless' || d.now.balancer === true); }).map(function (d) { return d.id; });
    S.view.nodes.forEach(function (n) {
      var L = S.liveEl[n.id]; if (!L) return;
      if (n.type === 'inbound') {
        var k = n.data.kind, li = S.live.inbounds[k], ds = I.devList.filter(function (d) { return d.access.indexOf(k) >= 0; });
        var here = ds.filter(function (d) { var v = S.live.devices[d.id]; return v && v.online && v.inbound === k; });
        setText(L.cnt, !ds.length ? t('graph.inbound.nobody') : fresh ? t('graph.inbound.online', { n: here.length, m: ds.length }) : tn('graph.devices', ds.length));
        setText(L.rate, fresh && here.length ? fmtRate(sumRates(here.map(function (d) { return d.id; }))) : '');
        var running = li ? li.running : null;
        setChip(L.state, !n.data.enabled ? ['', t('graph.inbound.disabled')] : running === true ? ['ok', li && typeof li.sessions === 'number' ? tn('graph.sessions', li.sessions) : t('graph.inbound.running')]
          : running === false ? ['bad', t('graph.inbound.down')] : ['', t('graph.nodata')]);
      } else if (n.type === 'exit') {
        var e = n.data, st = e.missing ? 'missing' : !e.enabled ? 'off' : exitSt(e.id, e.state);
        // VLESS в балансировщике: те же числа, что у его строки в пуле (подпись «в балансировщике»), а не «—» (Н-8)
        var devs = nowExit(e.id), vl = isXrayKind(e.kind) && !devs.length;
        if (vl) devs = balanced;
        setText(L.rate, fresh ? fmtRate(sumRates(devs)) : '—');
        setText(L.cnt, String(devs.length));
        setText(L.on, !fresh ? '—' : String(onNow(devs)));
        setChip(L.state, e.problem ? ['bad', e.problem] : st === 'alive' ? ['ok', t('graph.exit.alive')] : st === 'dead' ? ['bad', t('graph.exit.dead')]
          : st === 'missing' ? ['bad', t('graph.exit.missing')] : st === 'off' ? ['', t('graph.exit.off')] : st === 'nodata' ? ['', t('graph.nodata')] : ['', t('graph.exit.unknown')]);
        S.nodeEl[n.id] && S.nodeEl[n.id].classList.toggle('is-down', st === 'dead' || st === 'missing' || !!e.problem);
      } else if (n.type === 'pool') {
        var alive = 0, ids = n.data.exits || [];
        var okOf = function (eid) { var en = I.node['exit:' + eid], s2 = en ? exitSt(eid, en.data.state) : 'missing'; return { ok: s2 === 'alive' && en && en.data.enabled && !en.data.problem, st: s2, en: en }; };
        ids.forEach(function (eid) { if (okOf(eid).ok) alive++; });
        poolRows().forEach(function (r) {
          var v = L['m:' + r.id]; if (!v) return;
          var row = v.parentNode.parentNode;   // .kg-nd__mem: CSS подсвечивает строку целиком (R-333 повтор №2)
          if (r.group) {
            var nOk = r.exits.filter(function (eid) { return okOf(eid).ok; }).length;
            setText(v, (exitsNoData() ? t('graph.nodata') : t('graph.pool.vlessAlive', { n: nOk, m: r.exits.length })) + pinText(balanced, fresh));
            row.classList.toggle('is-active', nOk > 0); row.classList.toggle('is-down', !exitsNoData() && !nOk);
            return;
          }
          if (r.dup) { setText(v, t('graph.pool.dupRow')); row.classList.add('is-off'); return; }
        var o = okOf(r.id);
          setText(v, (o.ok ? t('graph.exit.alive') : o.st === 'dead' ? t('graph.exit.dead') : o.en && !o.en.data.enabled ? t('graph.exit.off') : o.st === 'nodata' ? t('graph.nodata') : t('graph.exit.unknown'))
            + pinText(nowExit(r.id), fresh));
          row.classList.toggle('is-active', !!o.ok); row.classList.toggle('is-down', o.st === 'dead');
        });
        setText(L.sub, !ids.length ? t('graph.pool.empty') : exitsNoData() ? t('graph.nodata') : t('graph.pool.alive', { n: alive, m: ids.length }));
      } else if (n.type === 'ru_group') {
        var rw = ruNowOf(n);
        (n.data.exits || []).forEach(function (r) {
          var v = L['x:' + r.id]; if (!v) return;
          var row = v.parentNode.parentNode, st3 = r.missing ? 'missing' : !r.enabled ? 'off' : exitSt(r.id, r.state), cur = rw.step === 'exit' && rw.exit === r.id;
          setText(v, (st3 === 'alive' ? t('graph.exit.alive') : st3 === 'dead' ? t('graph.exit.dead') : st3 === 'missing' ? t('graph.exit.missing') : st3 === 'off' ? t('graph.exit.off')
            : st3 === 'nodata' ? t('graph.nodata') : t('graph.exit.unknown')) + (cur && !exitsNoData() ? ' · ' + t('graph.ru.nowRow') : ''));
          row.classList.toggle('is-active', st3 === 'alive'); row.classList.toggle('is-down', st3 === 'dead' || st3 === 'missing'); row.classList.toggle('is-cur', cur && !exitsNoData());
        });
        setText(L.sub, ruNowText(n));
        var last = (rw.step === 'last_direct' || rw.step === 'last_block') && !(exitsNoData() && !rw.empty);
        L.sub.classList.toggle('is-warn', last);
        // худшая метка блока в общем виде (farMarks берёт .kg-chip--warn): только когда группа на последнем шаге
        L.state.className = 'kg-chip' + (last ? ' kg-chip--warn' : '');
        setText(L.state, !last ? '' : rw.empty ? t('graph.ru.noKeysChip') : t('graph.ru.lastChip'));
        if (L.foot.hidden !== !last) L.foot.hidden = !last;
      } else if (n.type === 'direct' || n.type === 'block') {
        var ds2 =I.devList.filter(function (d) { return d.active && d.now && d.now.target === n.id; }).map(function (d) { return d.id; });
        setText(L.rate, fresh ? fmtRate(sumRates(ds2)) : '—');
        setText(L.cnt, String(ds2.length));
        setText(L.on, fresh ? String(onNow(ds2)) : '—');
      }
    });
    farMarks();
    remeasureIfResized();
    updateEdgesLive();
    renderKpis();
    if (S.mode === 'list') updateListLive();
    if (S.sel && S.dom.insp && !S.dom.insp.hidden) updateInspLive();
  }
  /* Закреплено и на связи — одними словами везде (ревизия С7, Н4): «закреплено 2 · на связи 0»; без свежих данных — только
   закреплено. */
function onNow(ids) { return ids.filter(function (d) { return devOnline(d); }).length; }
function pinText(ids, fresh) { return ids.length ? ' · ' + (fresh ? t('graph.pin.both', { n: ids.length, m: onNow(ids) }) : t('graph.pin.only', { n: ids.length })) : ''; }
function setChip(el, v) { var c = 'kg-chip' + (v[0] === 'ok' ? ' kg-chip--ok' : v[0] === 'bad' ? ' kg-chip--bad' : ''); if (el.className !== c) el.className = c; setText(el, v[1]); }
  function setText(el, s) { s = String(s); if (el.textContent !== s) el.textContent = s; }

  function updateEdgesLive() {
    var fresh = liveFresh(), far = S.V.k < 0.62, moved = false;
    S.view.edges.forEach(function (e) {
      var r = S.edgeEl[e.k]; if (!r) return;
      var rate = null, active = false, lab = S.labelEl[e.k], text = '';
      if (e.devs && e.devs.length) {
        rate = sumRates(e.devs);
        active = e.devs.some(function (d) { return devOnline(d); });
      }
      if (e.type === 'member') {
        var en = S.idx.node['exit:' + e.exit], st = en ? exitSt(e.exit, en.data.state) : 'missing';
        r.g.classList.toggle('is-down', st === 'dead' || st === 'missing' || !!(en && en.data.problem));
        r.g.classList.toggle('is-standby', !!en && en.data.enabled && st !== 'dead');
        if (st === 'dead') text = t('graph.exit.dead');
      }
      r.g.classList.toggle('is-active', active);
      r.g.classList.toggle('is-idle', !edgeLive(e));
      var w = active && rate != null && rate > 0 ? 1.6 + Math.log10(1 + rate / 1e6) * 1.3 : 0;
      if (w) r.base.setAttribute('stroke-width', w.toFixed(2)); else r.base.removeAttribute('stroke-width');
      if (!lab) return;
      // у пучка всегда видно, сколько в нём устройств (а не только при наведении): веер читается без подсказок
      var many = e.devs && e.devs.length > 1 && e.type !== 'conn';
      var show = !far && (text || many || (active && rate != null && rate > 0) || S.hover === e.k || (S.sel && S.sel.t === 'edge' && S.sel.id === e.k));
      if (!text) {
        var parts = [];
        if (many || S.hover === e.k || (S.sel && S.sel.id === e.k)) parts.push(edgeShort(e));
        if (fresh && active && rate != null && rate > 0) parts.push(fmtRate(rate));
        text = parts.filter(Boolean).map(function (x) { return String(x).replace(/ /g, '\u00a0'); }).join('\u00a0· ');
      }
      var hide = !show || !text;
      if (lab.hidden !== hide) { lab.hidden = hide; moved = true; }
      if (!hide && lab.textContent !== text) { if (lab.textContent.length !== text.length) moved = true; lab.textContent = text; }
      var lc = 'kg-elab' + ((e.type === 'member' && text === t('graph.exit.dead')) || e.type === 'fail' ? ' kg-elab--down' : '') + (lab.classList.contains('is-2l') ? ' is-2l' : '');
      if (lab.className !== lc) lab.className = lc;
    });
    if (moved) scheduleEdges();                          // подпись появилась или стала другой длины — найти ей место
  }
  function edgeShort(e) {
    if (e.type === 'conn') return kindTitle(e.kind);
    if (e.type === 'fail') return t('graph.edge.failShort', { n: (e.devs || []).length });
    if (e.devs && e.devs.length) return tn('graph.devices', e.devs.length);
    return '';
  }

  function renderKpis() {
    var d = S.dom, I = S.idx, fresh = liveFresh();
    var on = I.devList.filter(function (x) { return devOnline(x.id); }).length;
    setText(d.kOn, fresh ? t('graph.kpi.online', { n: on, m: I.devList.length }) : tn('graph.devices', I.devList.length));
    d.kOn.className = 'kg-chip' + (fresh && on ? ' kg-chip--ok' : '');
    var total = sumRates(I.devList.map(function (x) { return x.id; }));
    setText(d.kRate, t('graph.kpi.rate', { v: fresh ? fmtRate(total) : '—' }));
    var ex = I.exits.filter(function (n) { return !n.data.missing; }), alive = ex.filter(function (n) { return exitSt(n.data.id, n.data.state) === 'alive' && n.data.enabled; }).length;
    var nodata = ex.length && exitsNoData();
    setText(d.kExit, !ex.length ? t('graph.kpi.noExits') : nodata ? t('graph.kpi.exitsNoData') : t('graph.kpi.exits', { n: alive, m: ex.length }));
    d.kExit.className = 'kg-chip' + (nodata ? '' : (!ex.length || !alive ? ' kg-chip--bad' : ' kg-chip--accent'));
  }

  /* ── Вид холста ─────────────────────────────────────────────────────── */
  function applyView() {
    var V = S.V;
    S.dom.world.style.transform = 'translate(' + V.x.toFixed(1) + 'px,' + V.y.toFixed(1) + 'px) scale(' + V.k.toFixed(3) + ')';
    var g = 22 * V.k;
    S.dom.stage.style.backgroundSize = g.toFixed(1) + 'px ' + g.toFixed(1) + 'px';
    S.dom.stage.style.backgroundPosition = V.x.toFixed(1) + 'px ' + V.y.toFixed(1) + 'px';
    S.dom.zoomV.textContent = Math.round(V.k * 100) + '%';
    var far = V.k < 0.62;
    S.dom.stage.style.setProperty('--kg-far-fs', Math.min(40, 11.5 / V.k).toFixed(1) + 'px');   // ≈11,5 px на экране
    if (far !== S.far) { S.far = far; S.dom.stage.classList.toggle('is-far', far); updateEdgesLive(); }
  }
  function zoomAt(cx, cy, f) {
    var r = S.dom.stage.getBoundingClientRect(), px = cx - r.left, py = cy - r.top, V = S.V;
    var k2 = clamp(V.k * f, zmin(), ZMAX), wx = (px - V.x) / V.k, wy = (py - V.y) / V.k;
    V.k = k2; V.x = px - wx * k2; V.y = py - wy * k2; applyView();
  }
  function zoomCenter(f) { var r = S.dom.stage.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, f); }
  function bounds() {
    var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    S.view.nodes.forEach(function (n) { var p = S.pos[n.id]; if (!p) return; x1 = Math.min(x1, p.x); y1 = Math.min(y1, p.y); x2 = Math.max(x2, p.x + nodeW(n.id)); y2 = Math.max(y2, p.y + nodeH(n.id)); });
    return { x1: x1, y1: y1, x2: x2, y2: y2 };
  }
  /* Вид, в который вписана схема (границы b, холст w×hh). all — «Вписать» и клавиша 0: вся схема, хоть и мелко (до
     zlo). Иначе (первый вид, новый размер окна, «Разложить») — не мельче READABLE: большая схема по ширине, если так
     читается, иначе в READABLE; видно её начало — люди слева сверху, остальное — прокруткой (на 30 % подписи не
     читались, T-G1b N10). */
  function fitView(b, w, hh, all, zlo) {
    var pad = 28, bw = Math.max(1, b.x2 - b.x1), bh = Math.max(1, b.y2 - b.y1), aw = w - pad * 2, ah = hh - pad * 2 - 40;
    var k = clamp(Math.min(aw / bw, ah / bh), zlo, 1.1);
    if (all || k >= READABLE) return { k: k, x: (w - bw * k) / 2 - b.x1 * k, y: pad + (ah - bh * k) / 2 - b.y1 * k };
    k = Math.max(READABLE, Math.min(aw / bw, 1));
    return { k: k, x: bw * k <= aw ? (w - bw * k) / 2 - b.x1 * k : pad - b.x1 * k, y: pad - b.y1 * k };
  }
  function fit(all) {
    if (S.mode !== 'canvas') return;
    var b = bounds(), w = S.dom.stage.clientWidth, hh = S.dom.stage.clientHeight;
    if (!w || !hh || !isFinite(b.x1)) { applyView(); return; }
    if (w < NARROW && !all) { S.V = { k: PHONE_K, x: 12 - b.x1 * PHONE_K, y: 12 - b.y1 * PHONE_K }; applyView(); return; }   // телефон: начало схемы крупно, без пустоты сверху
    S.V = fitView(b, w, hh, !!all, w < NARROW && all ? zmin() : zmin());
    applyView();
  }
  function toWorld(cx, cy) { var r = S.dom.stage.getBoundingClientRect(), V = S.V; return { x: (cx - r.left - V.x) / V.k, y: (cy - r.top - V.y) / V.k }; }
  function centerOn(id) {
    var p = S.pos[id]; if (!p || S.mode !== 'canvas') return;
    var w = S.dom.stage.clientWidth, hh = S.dom.stage.clientHeight, off = S.dom.insp.hidden || w < NARROW ? 0 : 200;
    S.V.x = (w - off) / 2 - (p.x + nodeW(id) / 2) * S.V.k; S.V.y = hh / 2 - (p.y + nodeH(id) / 2) * S.V.k; applyView();
  }
  function zmin() { return S.dom.root.clientWidth < NARROW ? 0.18 : 0.3; }
  function editable() { return S.mode === 'canvas' && S.dom.root.clientWidth >= NARROW && !S.busy; }

  /* ── Жесты: панорама, щипок, перетаскивание блоков, новые связи ─────── */
  function pcount() { return Object.keys(S.pointers).length; }
  function onPointerDown(e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (e.target.closest('.kg-ui, .kg-insp, .kg-dlgback, .kg-toast')) return;
    S.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    if (pcount() === 2) { startPinch(); return; }
    var ptEl = e.target.closest('.kg-pt'), nd = e.target.closest('.kg-nd'), hit = e.target.closest('.kg-e__hit');
    try { S.dom.stage.setPointerCapture(e.pointerId); } catch (_) { }
    if (ptEl && ptEl.getAttribute('data-dir') === 'out' && editable() && startConnect(e, ptEl)) { e.preventDefault(); return; }
    if (nd) {
      var dv = e.target.closest('.kg-dv'), id = nd.getAttribute('data-id'), p = S.pos[id] || { x: 0, y: 0 };
      S.gesture = { type: 'drag', id: id, el: nd, dev: dv ? dv.getAttribute('data-dev') : null, sx: e.clientX, sy: e.clientY, nx: p.x, ny: p.y, moved: false };
      return;
    }
    if (hit) { S.gesture = { type: 'edge', k: hit.getAttribute('data-k') }; return; }
    S.gesture = { type: 'pan', sx: e.clientX, sy: e.clientY, vx: S.V.x, vy: S.V.y, moved: false };
    S.dom.stage.classList.add('is-panning');
  }
  function onPointerMove(e) {
    if (S.pointers[e.pointerId]) S.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
    var G = S.gesture; if (!G) return;
    if (G.type === 'pinch') { movePinch(); return; }
    if (G.type === 'pan') {
      var dx = e.clientX - G.sx, dy = e.clientY - G.sy;
      if (Math.abs(dx) + Math.abs(dy) > 3) G.moved = true;
      S.V.x = G.vx + dx; S.V.y = G.vy + dy; applyView();
    } else if (G.type === 'drag') moveDrag(e);
    else if (G.type === 'connect') moveConnect(e);
  }
  function onPointerUp(e) {
    delete S.pointers[e.pointerId];
    var G = S.gesture; if (!G) return;
    if (G.type === 'pinch') { if (pcount() < 2) S.gesture = null; return; }
    S.gesture = null;
    if (G.type === 'pan') { S.dom.stage.classList.remove('is-panning'); if (!G.moved && e.type === 'pointerup' && !S.sim) { dropTrace(); select(null); } }
    else if (G.type === 'drag') endDrag(G);
    else if (G.type === 'connect') endConnect(G);
    else if (G.type === 'edge' && e.type === 'pointerup') select({ t: 'edge', id: G.k });
  }
  function startPinch() {
    var ids = Object.keys(S.pointers), a = S.pointers[ids[0]], b = S.pointers[ids[1]], r = S.dom.stage.getBoundingClientRect(), G = S.gesture;
    if (G && G.type === 'drag') G.el.classList.remove('is-dragging');
    if (G && G.type === 'connect') clearTargets();
    S.dom.stage.classList.remove('is-panning');
    S.gesture = { type: 'pinch', d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, k0: S.V.k, mx: (a.x + b.x) / 2 - r.left, my: (a.y + b.y) / 2 - r.top, vx: S.V.x, vy: S.V.y };
  }
  function movePinch() {
    var ids = Object.keys(S.pointers); if (ids.length < 2) return;
    var G = S.gesture, a = S.pointers[ids[0]], b = S.pointers[ids[1]], r = S.dom.stage.getBoundingClientRect();
    var k = clamp(G.k0 * Math.hypot(a.x - b.x, a.y - b.y) / G.d0, zmin(), ZMAX);
    var mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top, wx = (G.mx - G.vx) / G.k0, wy = (G.my - G.vy) / G.k0;
    S.V.k = k; S.V.x = mx - wx * k; S.V.y = my - wy * k; applyView();
  }
  function onWheel(e) {
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0022)); }
    else if (!S.wheelHinted) { S.wheelHinted = true; flashHint(t('graph.hint.zoom')); }
  }
  function onPointerOver(e) {
    if (S.gesture) return;
    var hEl = e.target.closest && e.target.closest('.kg-e__hit'), k = hEl ? hEl.getAttribute('data-k') : null;
    if (k !== S.hover) setHover(k);
  }
  function setHover(k) { S.hover = k; updateEdgesLive(); }
  function flashHint(text) { var el = S.dom.hint; el.textContent = text; el.hidden = false; later('hint', 2600, function () { S.dom.hint.hidden = true; }); }

  function moveDrag(e) {
    var G = S.gesture, dx = (e.clientX - G.sx) / S.V.k, dy = (e.clientY - G.sy) / S.V.k;
    if (!G.moved && Math.abs(dx) + Math.abs(dy) < 3 / S.V.k) return;
    if (!editable()) return;
    if (!G.moved) { G.moved = true; G.el.classList.add('is-dragging'); }
    S.pos[G.id] = { x: Math.max(0, Math.round((G.nx + dx) / 4) * 4), y: Math.max(0, Math.round((G.ny + dy) / 4) * 4) };
    moveNodeEl(G.id); scheduleEdges();
  }
  function endDrag(G) {
    G.el.classList.remove('is-dragging');
    if (!G.moved) { dropTrace(); select(G.dev ? { t: 'dev', id: G.dev } : { t: 'node', id: G.id }); return; }
    var p = S.pos[G.id];
    S.pos[G.id] = { x: Math.round(p.x / 8) * 8, y: Math.round(p.y / 8) * 8 };
    moveNodeEl(G.id); layoutEdges();
    var before = {}; before[G.id] = { x: G.nx, y: G.ny };
    S.hist.push({ kind: 'move', before: before }); if (S.hist.length > HIST_MAX) S.hist.shift();
    S.dirtyPos[G.id] = 1;
    later('save', SAVE_DELAY, saveLayout);
    updateToolbar();
  }

  /* Подсказки при проведении связи: только подсказки, решает сервер. */
  /* Подсказки при проведении связи (решает сервер). Одна ручка — одно действие: от устройства или человека тянут только
     маршрут — к пулу, выходу, «Напрямую» или «Блоку». Вход не перепривязывают (настройки на устройстве под свой вход:
     выдают новые — в карточке устройства), правила выбирают в карточке (ревизия схемы, Б1). */
  function canConnect(src, dst) {
    var n = S.idx.node[dst];
    if (!n || dst === src.node) return { ok: false };
    var isTarget = !!TARGET[n.type];
    if (n.type === 'inbound') return { ok: false, soft: true, why: t(src.kind === 'dev' ? 'graph.cc.inbound' : 'graph.cc.userInbound') };
    if (n.type === 'rules' || n.type === 'filter') return { ok: false, soft: true, why: t(n.type === 'filter' ? 'graph.cc.filter' : 'graph.cc.rules') };
    if (n.type === 'ru_group') return { ok: false, soft: true, why: t('graph.cc.ruGroup') };
    if (!isTarget) return { ok: false, why: t(src.kind === 'dev' ? 'graph.cc.devWhere' : 'graph.cc.userWhere') };
    if (n.data && n.data.missing) return { ok: false, soft: true, why: t('graph.cc.exitMissing') };
    if (dupExit(dst)) return { ok: false, soft: true, why: t('graph.cc.exitDup') };
    var r = src.kind === 'dev' ? S.draft.devices[src.dev].route : S.draft.users[src.user].route;
    if (r && r.via === dst) return { ok: false, soft: true, why: t('graph.cc.same') };
    return { ok: true };
  }
  function startConnect(e, ptEl) {
    var hid = ptEl.getAttribute('data-h'), nodeEl = ptEl.closest('.kg-nd'), nid = nodeEl.getAttribute('data-id'), src;
    if (hid === 'u') src = { kind: 'user', node: nid, user: nid, h: hid };
    else if (hid.indexOf('dv:') === 0) src = { kind: 'dev', node: nid, dev: hid.slice(3), h: hid };
    else return false;                                   // строки правил и пула — производные, их не тянут
    S.gesture = { type: 'connect', src: src, a: portXY(nid, 'out', hid), b: toWorld(e.clientX, e.clientY), snap: null, bad: null };
    S.view.nodes.forEach(function (n) {
      var el = S.nodeEl[n.id]; if (!el || n.id === nid) return;
      var c = canConnect(src, n.id);
      el.classList.add(c.ok ? 'is-target' : c.soft ? 'is-soft' : 'is-dim');
      if (c.ok) { var pin = el.querySelector('.kg-pt--in'); if (pin) pin.classList.add('is-ok'); }
    });
    S.dom.stage.classList.add('is-connecting');
    drawTemp();
    return true;
  }
  function moveConnect(e) {
    var G = S.gesture, w = toWorld(e.clientX, e.clientY), best = null, bd = 46 / S.V.k;
    S.view.nodes.forEach(function (n) {
      if (n.id === G.src.node) return;
      var p = S.pos[n.id]; if (!p) return;
      var inside = w.x >= p.x && w.x <= p.x + nodeW(n.id) && w.y >= p.y && w.y <= p.y + nodeH(n.id);
      var ip = portXY(n.id, 'in', 'in'); if (!ip) return;
      var dist = inside ? 0 : Math.hypot(ip.x - w.x, ip.y - w.y);
      if (dist < bd) { bd = dist; best = { id: n.id, p: ip }; }
    });
    G.snap = null; G.bad = null;
    if (best) {
      var c = canConnect(G.src, best.id);
      if (c.ok) { G.snap = best.id; G.b = best.p; if (c.warn) flashHint(c.warn); } else { G.bad = c; G.b = w; }
    } else G.b = w;
    if (G.bad && G.bad.why) flashHint(G.bad.why);
    drawTemp();
  }
  function drawTemp() {
    var G = S.gesture, g = S.dom.gTemp;
    clear(g);
    if (!G || G.type !== 'connect' || !G.a || !G.b) return;
    g.appendChild(svgEl('path', { 'class': 'kg-e__temp' + (G.snap ? ' is-snap' : '') + (G.bad ? ' is-bad' : ''), d: curve(G.a, G.b) }));
  }
  function clearTargets() {
    Object.keys(S.nodeEl).forEach(function (id) { S.nodeEl[id].classList.remove('is-target', 'is-dim', 'is-soft'); });
    Array.prototype.forEach.call(S.dom.nodes.querySelectorAll('.kg-pt.is-ok'), function (p) { p.classList.remove('is-ok'); });
    S.dom.stage.classList.remove('is-connecting');
    clear(S.dom.gTemp);
  }
  function endConnect(G) {
    clearTargets();
    S.dom.hint.hidden = true;
    if (G.snap) connect(G.src, G.snap);
    else if (G.bad && G.bad.why) toast(G.bad.why);
  }
  function connect(src, dst) {
    var r = { type: 'route', via: dst };
    if (src.kind === 'dev') r.device = src.dev; else r.user = src.user;
    addIntent(r);
  }

  /* Удаление связи — намерения, ровно то, что задано там, откуда линия (ревизия схемы, Б4): у связи со входом —
     отозвать вход; у линии от строки устройства — снять его СВОИ правила и/или СВОЙ маршрут; от шапки человека — правила
     и/или маршрут ЧЕЛОВЕКА (у всех его устройств без своих). Линия «по умолчанию узла» не снимается. */
  /* Подпись кнопки снятия: что снимется и что станет вместо (у человека — у скольких устройств). */
  /* Звенья пути следуют из настроек — их не удаляют: вход отзывают в карточке устройства, маршрут и правила меняют там
     же (или у человека). Удаление выбранного звена только подсказывает, где это сделать. */
  function removeEdge(k) {
    var e = S.view.byKey[k];
    toast(t(e && e.type === 'conn' ? 'graph.edge.connNoDelete' : 'graph.edge.noDelete'));
  }

  /* ── Выделение и «Куда пойдёт» ──────────────────────────────────────── */
  function select(s) {
    S.sel = s;
    markSelection();
    if (S.onlyActive) structureChanged(true);
    else { updateEdgeClasses(); updateEdgesLive(); }
    renderInspector();
  }
  function refreshSelection() {
    if (!S.sel) return;
    var s = S.sel;
    if ((s.t === 'dev' && !S.idx.dev[s.id]) || (s.t === 'node' && !S.idx.node[s.id]) || (s.t === 'edge' && !S.view.byKey[s.id])) { S.sel = null; S.dom.insp.hidden = true; }
    else renderInspector();
  }
  function markSelection() {
    var s = S.sel, tr = S.trace;
    Object.keys(S.nodeEl).forEach(function (id) {
      var el = S.nodeEl[id], row = tr && tr.rows ? tr.rows[id] : null;
      el.classList.toggle('is-sel', !!(s && s.t === 'node' && s.id === id));
      el.classList.toggle('is-trace', !!(tr && tr.nodes[id]));
      var cand = !!(tr && tr.cands && tr.cands[id]);
el.classList.toggle('is-cand', cand);
var cl = S.liveEl[id] && S.liveEl[id].cand; if (cl && cl.hidden === cand) cl.hidden = !cand;
      Array.prototype.forEach.call(el.querySelectorAll('[data-row]'), function (r) { r.classList.toggle('is-trace', !!row && r.getAttribute('data-row') === row); });
    });
    Object.keys(S.rowEl).forEach(function (id) {
      var el = S.rowEl[id].row;
      el.classList.toggle('is-sel', !!(s && s.t === 'dev' && s.id === id));
      el.classList.toggle('is-trace', !!(tr && tr.devs[id]));
    });
    S.dom.stage.classList.toggle('is-tracing', !!tr);
  }
  function updateEdgeClasses() {
    S.view.edges.forEach(function (e) {
      var r = S.edgeEl[e.k]; if (!r) return;
      var keep = r.g.classList.contains('is-active'), down = r.g.classList.contains('is-down'), sb = r.g.classList.contains('is-standby');
      r.g.setAttribute('class', edgeClass(e));
      r.g.classList.toggle('is-active', keep); r.g.classList.toggle('is-down', down); r.g.classList.toggle('is-standby', sb);
      if ((S.sel && S.sel.t === 'edge' && S.sel.id === e.k) || (S.trace && (S.trace.edges[e.k] || (S.trace.alts && S.trace.alts[e.k])))) r.g.parentNode.appendChild(r.g);
    });
  }
  /* Структура поменялась (черновик, данные, видимость): пересобрать узлы и связи; цифры — следом. */
  function structureChanged(edgesOnly) {
    computeView();
    retrace();
    if (S.sim) renderSimOut();
    renderFocusBar();
    if (S.mode === 'canvas') {
      if (!edgesOnly) { renderNodes(); }
      renderEdges();
      markSelection();
    } else renderList();
    updateLive();
    refreshSelection();
    updateToolbar();
  }

  /* Путь одного устройства. all — все его входы (подсветка «На схеме»), иначе — тот, которым оно на связи сейчас. */
  /* Подсветка из звеньев пути: {nodes, devs, edges, rows, cands (кандидаты балансировщика), steps}. */
  function mkTrace(c, P) {
    var T = { nodes: {}, devs: {}, edges: {}, rows: {}, cands: {}, alts: {}, steps: [] };
    c.nodes.forEach(function (x) { if (S.idx.node[x]) T.nodes[x] = 1; });
    c.edges.forEach(function (x) { T.edges[x] = 1; });
    (c.alts || []).forEach(function (x) { T.alts[x] = 1; });
    for (var k in c.rows) if (OWN.call(c.rows, k)) T.rows[k] = c.rows[k];
    if (P && P.balancer) (P.exits || []).forEach(function (x) { if (S.idx.node['exit:' + x]) T.cands['exit:' + x] = 1; });
    return T;
  }
  /* Путь одного устройства — ОДНА цепочка (ревизия схемы, этап 2): вход — тот, через который оно на связи, иначе первый
     выданный (или выбранный в «Через»); строка правил — основная «остальное → маршрут», другие строки — в подсказке
     шагов, без линий. Маршрут и профиль — по черновику, как будет после «Применить». */
  function traceOf(did) {
    var I = S.idx, d = I.dev[did]; if (!d) return null;
    var P = devPath(did), T = mkTrace(chainOf(P), P), ld = S.live.devices[did], D = S.draft.devices[did];
    T.dev = did; T.devs[did] = 1; T.path = P;
    T.steps.push({ t: d.name, s: t('graph.kind.' + d.kind) + ' · ' + nodeTitle(d.user) + ' · ' + (ld ? (ld.online ? t('graph.dev.online') : t('graph.dev.offline')) : t('graph.nodata')) });
    if (!P.active) { T.steps.push({ t: t('graph.trace.inactive'), s: t('graph.dev.inactive'), warn: true, last: true }); return T; }
    if (!P.inbound) { T.steps.push({ t: t('graph.trace.noInbound'), s: D.access.length ? t('graph.trace.inboundsOff') : t('graph.trace.noInboundWhy'), warn: true, last: true }); return T; }
    var others = D.access.filter(function (k) { return k !== P.inbound && kindOn(k); });
    var online = ld && ld.online && ld.inbound === P.inbound;
    T.steps.push({ t: t('graph.trace.inbound', { name: kindTitle(P.inbound) }), s: (online ? t('graph.trace.inboundNow') : S.sim && S.sim.picked && S.sim.who === did && S.sim.inbound === P.inbound && simKinds(did).length > 1 ? t('graph.trace.inboundChosen') : t('graph.trace.inboundFirst'))
      + (others.length ? ' · ' + t('graph.trace.inboundOthers', { names: others.map(kindTitle).join(', ') }) : '') + (xrayIn(P.inbound) ? ' · ' + t('graph.trace.socks') : '') });
    T.steps.push({ t: t('graph.trace.filter'), s: filterHint() });
    var rn = I.node['rules:' + P.profile], ru = P.profile === 'ru_direct' ? ruOpen(did, { via: P.via, fail: P.fail }) : null;
    var rog = P.profile === 'ru_direct' ? ruOf(did) : null;
    if (rn) T.steps.push({ t: t('graph.trace.rules', { name: rn.data.title }), s: t('graph.pfrom.' + P.pfrom) + (ru ? ' · ' + t(ru.why) : '')
      + (rog && rog.acts && rog.group ? ' · ' + t('graph.trace.ruGroup', { name: nodeTitle('rug:' + rog.via) }) : ''), warn: !!(ru && !ru.open) });
    T.steps.push({ t: t('graph.trace.route', { name: viaTitle(P.via) }), s: t('graph.from.' + P.rfrom) + (P.via !== 'direct' && P.via !== 'block' ? ' · ' + t('graph.trace.fail.' + P.fail) : '') });
    var now = d.now || {};
    if (P.changed) T.steps.push({ t: t('graph.trace.afterApply'), s: t('graph.trace.nowBefore', { where: now.target ? viaTitle(now.target) : t('graph.trace.nothing') }), warn: true, last: true });
    else if (P.balancer) T.steps.push({ t: t(P.balancer === 'any' ? 'graph.trace.anyT' : 'graph.trace.balancerT'), s: t('graph.trace.balancerS', { names: P.exits.map(function (x) { return nodeTitle('exit:' + x); }).join(', ') || '—' }), last: true });
    else if (P.exit) T.steps.push({ t: viaTitle('exit:' + P.exit), s: t('graph.trace.nowExit'), last: true });
    else if (P.target !== P.via) T.steps.push({ t: t('graph.trace.failNow', { where: viaTitle(P.target) }), s: now.why || '', warn: true, last: true });
    else T.steps[T.steps.length - 1].last = true;
    return T;
  }
  /* Новое устройство человека или по умолчанию: без входа — от фильтра узла до цели. */
  function whoTrace(who) {
    var P = whoPath(who), T = mkTrace(chainOf(P), P);
    T.dev = null; T.path = P;
    T.steps.push({ t: P.user ? t('graph.sim.newOfUser', { name: nodeTitle(P.user) }) : t('graph.sim.newDefault'), s: t('graph.trace.newInbound') });
    T.steps.push({ t: t('graph.trace.filter'), s: filterHint() });
    var rn = S.idx.node['rules:' + P.profile];
    if (rn) T.steps.push({ t: t('graph.trace.rules', { name: rn.data.title }), s: t('graph.pfrom.' + P.pfrom) });
    T.steps.push({ t: t('graph.trace.route', { name: viaTitle(P.via) }), s: t('graph.from.' + P.rfrom), last: true });
    return T;
  }
  /* Что ещё делает фильтр узла — подсказкой к шагу (строки «блок» горят только для такого домена). */
  function filterHint() {
    var f = S.idx.node.filter, d = f ? f.data : {};
    return d.ads ? (d.torrent ? t('graph.trace.filterAdsTorrent') : t('graph.trace.filterAds')) : d.torrent ? t('graph.trace.filterTorrent') : t('graph.trace.filterNone');
  }
  /* Строки профиля правил (Россия — напрямую, реклама — блок): их стрелки и цели. */
  /* ── «Куда пойдёт трафик?»: домен или IP → путь, подсвеченный на схеме ──
     Ответ даёт сервер (POST /api/gw/trace — kaskad.gw.trace, одна правда для панели, меню 17, kaskadctl и бота) по
     СОХРАНЁННЫМ настройкам; его highlight — id блоков и связей этого графа (scheme.py): simTrace переводит их в ключи
     вида. Без домена — путь самого устройства (traceOf, по черновику). Адрес раздела: #scheme?trace=<кто>:<куда>,
     кто — d-…, u-… или «-» (новое устройство по умолчанию); «Назад» браузера возвращает прежний запрос. */
  var SIM_EXAMPLES = ['youtube.com', 'ya.ru', '8.8.8.8', 'doubleclick.net'];
  function simRef(v) {
    var m = /^(d-[0-9a-f]{8}|u-[0-9a-f]{8}|-):([^\s]{1,253})$/.exec(String(v || ''));
    return m ? { who: m[1], target: m[2] } : null;
  }
  function simWhoOk(who) { return who === '-' || !!(S.idx && (S.idx.dev[who] || S.idx.user[who])); }
  /* Кого проверять: выбранное на схеме устройство, прежний выбор, иначе первое устройство (нет устройств — новое). */
  function simDefaultWho() {
    if (S.sel && S.sel.t === 'dev' && S.idx.dev[S.sel.id]) return S.sel.id;
    var last = S.sim || S.simLast;
    if (last && last.who && simWhoOk(last.who)) return last.who;
    return S.idx.devList.length ? S.idx.devList[0].id : '-';
  }
  /* Подсветка ответа сервера: блоки, строки устройств, связи (access → a|…, rule → x|…, member → m|…), линия маршрута
     устройства (или человека — по его маршруту и профилю), строка правила в блоке профиля и — если трафик идёт по
     маршруту — связь от профиля к цели маршрута. Чего на схеме нет (черновик другой) — не подсвечивается. */
  /* Подсветка ответа сервера: его highlight — та же одна цепочка (kaskad.gw.trace.chain) с теми же ид звеньев, что и
     на схеме: блоки, звенья, строка в блоке (фильтр, правила, пул), кандидаты балансировщика. */
  function simTrace(res) {
    var I = S.idx, hl = (res && res.highlight) || {};
    var T = { sim: true, dev: null, nodes: {}, devs: {}, edges: {}, rows: {}, cands: {}, alts: {}, steps: [] };
(hl.alts || []).forEach(function (id) { T.alts[id] = 1; });
    (hl.nodes || []).forEach(function (id) { if (I.node[id]) T.nodes[id] = 1; });
    (hl.devices || []).forEach(function (id) { if (I.dev[id]) { T.devs[id] = 1; T.dev = id; } });
    (hl.edges || []).forEach(function (id) { T.edges[id] = 1; });
    var rows = hl.rows || {};
    for (var k in rows) if (OWN.call(rows, k)) T.rows[k] = rows[k];
    if (hl.balancer) (hl.exits || []).forEach(function (x) { if (I.node['exit:' + x]) { T.cands['exit:' + x] = 1; delete T.nodes['exit:' + x]; } });
    return T;
  }
  function openSim(o) {
    o = o || {};
    if (S.focus) setFocus(null, !!o.quiet);
    var prev = S.sim || S.simLast || {};
    if (S.pin) { S.pin = null; renderPinBar(); }
    S.dom.insp.classList.remove('is-min');
    var who = o.who && simWhoOk(o.who) ? o.who : simDefaultWho();
    if (o.who && !simWhoOk(o.who)) toast(t('graph.sim.whoGone'));
    S.sel = null;
    S.sim = { who: who, inbound: simInbound(who, o.inbound), target: o.target != null ? String(o.target) : (prev.target || ''), res: null, err: '', busy: false, asked: null };
    S.userMode = true;
    if (S.mode !== 'canvas') setMode('canvas');
    S.dom.root.classList.add('has-sim');
    structureChanged();
    renderTraceInsp(true);                                // панель — до вписывания: подсвеченное вписывается в видимую часть
    simPreview();
    if (o.run && S.sim.target) runSim(o.quiet);
    else if (!o.quiet && !isTouch()) { var inp = S.dom.insp.querySelector('[data-act="sim-target"]'); if (inp) { try { inp.focus({ preventScroll: true }); } catch (_) { inp.focus(); } } }
  }
  /* Через какой вход проверять: заданный, иначе тот, через который устройство на связи, иначе первый выданный (по
     сохранённым настройкам — сервер отвечает по ним). Человек и новое устройство — без входа. */
  function simInbound(who, want) { return S.idx.dev[who] ? pathInbound(who, want, true) : null; }
  function simKinds(who) { var d = S.idx.dev[who]; return d ? (d.access || []).filter(kindOn) : []; }
  function isTouch() { try { return window.matchMedia('(pointer: coarse)').matches; } catch (_) { return false; } }
  /* Адрес раздела просит проверку (ссылка, «Назад» браузера): та же — ничего не делать, иначе показать и спросить. */
  function simNav(r) {
    if (S.sim && S.sim.asked && S.sim.asked.who === r.who && S.sim.asked.target === r.target) return;
    openSim({ who: r.who, target: r.target, run: true, quiet: true });
  }
  /* Без домена — путь устройства по черновику (как прежнее «Куда пойдёт»); человек или новое — ничего. */
  /* Без домена — основной путь устройства (traceOf: строка «остальное → маршрут»; другие строки — подсказкой в шагах), у
     человека и нового устройства — от фильтра узла до цели; с ответом сервера — ровно его цепочка. */
  function simPreview(keepView) {
    S.trace = S.sim ? simPathTrace() : null;
    traceEdges();
    if (S.mode === 'canvas') {
      renderEdges(); markSelection(); updateEdgeClasses();
      if (S.trace && !keepView) fitTrace(S.trace);
    }
  }
  /* Закреплённый путь (панель закрыта, ответ остался): подсветка ответа сервера и чип «Путь: кто → куда ✕» (Н-3). */
  /* Адрес с закреплённым путём (#scheme?trace=…&pin=1, перезагрузка, «Назад»): спросить сервер и показать путь чипом,
     без панели. */
  function pinNav(r) {
    if (S.pin && S.pin.who === r.who && S.pin.target === r.target) return;
    if (S.sim) closeSim(true, true);
    var seq = ++S.simSeq;
    call('POST', '/gw/trace', { who: r.who, target: r.target }).then(function (x) {
      if (!S || seq !== S.simSeq || S.sim || !x.ok || !x.data || !x.data.highlight) return;
      S.pin = { who: r.who, target: r.target, inbound: x.data.who && x.data.who.inbound || null, res: x.data };
      S.trace = pinTrace(); traceEdges(); renderPinBar();
      if (S.mode === 'canvas') { renderEdges(); markSelection(); updateEdgeClasses(); fit(true); }
    });
  }
  function pinTrace() { var T = simTrace(S.pin.res); T.pin = true; return T; }
  function renderPinBar() {
    var el = S.dom.pinBar; if (!el) return;
    clear(el);
    if (!S.pin) { el.hidden = true; sizeStage(); return; }
    var who = S.idx.dev[S.pin.who] ? devTitle(S.pin.who) : S.idx.user[S.pin.who] ? t('graph.sim.newOfUser', { name: nodeTitle(S.pin.who) }) : t('graph.sim.newDefault');
    append(el, [h('span', { cls: 'kg-chip kg-chip--accent kg-pin__chip' }, [icon('magnifier', 13), h('span', { text: t('graph.pin.path', { who: who, target: S.pin.target }) }),
      h('button', { type: 'button', cls: 'kg-pin__x', 'data-act': 'pin-off', 'aria-label': t('graph.pin.off'), title: t('graph.pin.off') }, icon('xmark', 13))]),
      btn('kg-btn--ghost kg-btn--sm', 'pin-open', t('graph.pin.open'), null)]);
    el.hidden = false;
    sizeStage();
  }
  function unpin(quiet) {
    if (!S.pin) return;
    S.pin = null;
    if (S.trace && S.trace.pin) S.trace = null;
    renderPinBar(); markSelection(); updateEdgeClasses();
    if (!quiet && typeof S.ctx.onTrace === 'function') S.ctx.onTrace(null);
  }
  /* Вписать подсветку: на широком экране — не мельче MIN_TRACE_K (подписи и плашка «выберет автоматически» читаются);
     не влезает — виден хвост пути (фильтр → правила → цель → выходы-кандидаты), начало (устройство и вход, оно названо
     в панели) — прокруткой (Н-2). Телефон — как fitNodes: начало пути крупно у левого края. */
  function fitTrace(T) {
    var ids = traceIds(T), w = S.dom.stage.clientWidth;
    if (w < NARROW) { fitNodes(ids); return; }
    var bb = boxOf(ids); if (!bb) return;
    var hh = S.dom.stage.clientHeight, pad = 36, aw = w - (!S.dom.insp.hidden ? S.dom.insp.offsetWidth + 24 : 0);
    var k = clamp(Math.min((aw - pad * 2) / Math.max(1, bb.x2 - bb.x1), (hh - pad * 2) / Math.max(1, bb.y2 - bb.y1)), zmin(), 1);
    if (k >= MIN_TRACE_K) { fitNodes(ids); return; }
    k = MIN_TRACE_K;
    // хвост пути — от фильтра (или от правил, от цели — что влезает) до кандидатов: прижат к правому краю видимого,
    // путь входит в него слева (Н-2)
    var tb = null;
    for (var L = 2; L <= 5; L++) {
      var tail = ids.filter(function (id) { var n = S.idx.node[id]; return n && LAYER[n.type] >= L; });
      if (!tail.length) break;
      tb = boxOf(tail);
      if ((tb.x2 - tb.x1) * k <= aw - pad * 2) break;
    }
    tb = tb || boxOf(ids);
    var x = aw - pad - tb.x2 * k;
    var y = pad + (hh - pad * 2 - (tb.y2 - tb.y1) * k) / 2 - tb.y1 * k;
    if ((tb.y2 - tb.y1) * k > hh - pad * 2) y = pad - tb.y1 * k;
    S.V = { k: k, x: x, y: y };
    applyView();
  }
  function boxOf(ids) {
    var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    ids.forEach(function (id) { var p = S.pos[id]; if (!p) return; x1 = Math.min(x1, p.x); y1 = Math.min(y1, p.y); x2 = Math.max(x2, p.x + nodeW(id)); y2 = Math.max(y2, p.y + nodeH(id)); });
    return isFinite(x1) ? { x1: x1, y1: y1, x2: x2, y2: y2 } : null;
  }
  /* Холст — по высоте окна (Н-2, Н-4): от своего верха до низа окна или до нижней навигации панели (телефон), чтобы
     полоса масштаба и тосты были на экране, а страница не прокручивалась. */
  function sizeStage() {
    var st = S && S.dom.stage; if (!st || S.mode !== 'canvas' || !st.getBoundingClientRect) return;
    var tabs = document.getElementById('tabs'), tr = tabs && !tabs.hidden && tabs.getBoundingClientRect ? tabs.getBoundingClientRect() : null;
    var limit = window.innerHeight, navH = 0;
    if (tr && tr.height && tr.top > window.innerHeight / 2) { limit = tr.top; navH = window.innerHeight - tr.top; }
    S.dom.root.style.setProperty('--kg-nav', Math.max(0, Math.round(navH)) + 'px');
    var sc = scrollParent(st), top = st.getBoundingClientRect().top + (sc ? sc.scrollTop : (window.scrollY || 0));   // верх в покое
    var hgt = Math.max(320, Math.round(limit - top - 12));
    if (Math.abs((parseFloat(st.style.height) || 0) - hgt) > 1) st.style.height = hgt + 'px';
    // то, что ниже холста (поля карточки, отступ main), не должно прокручивать main: срезаем ровно перебор (Н-2)
    var box = sc || document.scrollingElement;
    var over = box ? box.scrollHeight - box.clientHeight : 0;
    if (over > 0 && hgt > 320) st.style.height = Math.max(320, hgt - over) + 'px';
  }
  function scrollParent(el) {
    for (var p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      var o = window.getComputedStyle(p).overflowY;
      if (o === 'auto' || o === 'scroll') return p;
    }
    return null;
  }
  function simPathTrace() {
    var sim = S.sim;
    return sim.res ? simTrace(sim.res) : S.idx.dev[sim.who] ? traceOf(sim.who) : whoTrace(sim.who);
  }
  function runSim(quiet, keepView) {
    var sim = S.sim; if (!sim) return;
    var target = String(sim.target || '').trim();
    if (!target) { sim.res = null; sim.err = ''; sim.asked = null; simPreview(keepView); renderSimOut(); return; }
    var seq = ++S.simSeq, who = sim.who, body = { who: who, target: target };
    if (sim.inbound && S.idx.dev[who]) body.inbound = sim.inbound;
    if (!keepView || !sim.res) { sim.busy = true; sim.err = ''; renderSimOut(); }
    call('POST', '/gw/trace', body).then(function (r) {
      if (!S || S.sim !== sim || seq !== S.simSeq) return;
      sim.busy = false;
      if (!r.ok || !r.data || !r.data.highlight) {
        sim.res = null; sim.asked = null; sim.err = errText(r);
        S.trace = null; markSelection(); updateEdgeClasses(); renderSimOut();
        return;
      }
      sim.res = r.data; sim.asked = { who: who, target: target };
      if (r.data.who && r.data.who.inbound) sim.inbound = r.data.who.inbound;
      renderSimOut(); simOutInView();
      simPreview(keepView);
      if (!quiet && typeof S.ctx.onTrace === 'function') S.ctx.onTrace(who + ':' + target);
    });
  }
  /* Закрыть проверку; quiet — адрес раздела уже другой (пришёл от app.js), сообщать не надо. */
  function closeSim(quiet, drop) {
    if (!S.sim) return;
    var last = S.sim;
    S.simLast = { who: S.sim.who, target: S.sim.target };
    S.sim = null;
    S.simSeq += 1;
    S.dom.root.classList.remove('has-sim');
    S.dom.insp.classList.remove('kg-insp--sim', 'is-min');
    // ответ с доменом остаётся на схеме (Н-3): подсветка — до «✕» у чипа «Путь: …»; без ответа — снимается
    var keep = !drop && last && last.res && last.res.highlight;
    S.pin = keep ? { who: last.who, target: String(last.asked ? last.asked.target : last.target || ''), inbound: last.inbound, res: last.res } : null;
    if (S.trace && !S.trace.focus) S.trace = null;
    structureChanged();                                   // «Через» двигал линию устройства — вернуть её к его входу
    if (!S.sel) { S.dom.insp.hidden = true; clear(S.dom.insp); }
    renderPinBar();
    if (S.mode === 'canvas') fit(true);                   // вид был ужат под панель — схема целиком (Н-2), путь горит
    // адрес хранит состояние: закреплённый путь — #scheme?trace=…&pin=1 (без новой записи истории), без него — #scheme
    if (!quiet && typeof S.ctx.onTrace === 'function') S.ctx.onTrace(S.pin ? S.pin.who + ':' + S.pin.target : null, !!S.pin);
  }
  function onInput(e) {
    var el = e.target;
    if (S.sim && el && el.getAttribute && el.getAttribute('data-act') === 'sim-target') S.sim.target = el.value;
  }
  function onSubmit(e) {
    var f = e.target;
    if (!f || !f.getAttribute || f.getAttribute('data-act') !== 'sim-form') return;
    e.preventDefault();
    if (!S.sim) return;
    var inp = f.querySelector('[data-act="sim-target"]');
    if (inp) S.sim.target = inp.value;
    if (!String(S.sim.target || '').trim()) { S.sim.err = t('graph.sim.empty'); renderSimOut(); if (inp) inp.focus(); return; }
    runSim();
  }
  /* Показать эти блоки целиком — в части холста, не закрытой панелью свойств. */
  /* Блоки подсветки и кандидаты балансировщика (конец пути) — всё, что должно быть видно. */
  function traceIds(T) { return Object.keys(T.nodes).concat(Object.keys(T.cands || {})); }
  function fitNodes(ids) {
    var x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    ids.forEach(function (id) { var p = S.pos[id]; if (!p) return; x1 = Math.min(x1, p.x); y1 = Math.min(y1, p.y); x2 = Math.max(x2, p.x + nodeW(id)); y2 = Math.max(y2, p.y + nodeH(id)); });
    if (!isFinite(x1)) return;
    var w = S.dom.stage.clientWidth, hh = S.dom.stage.clientHeight, pad = 36;
    // панель справа (широкий экран) закрывает часть холста: вписываем в то, что видно слева от неё
    var aw = w - (!S.dom.insp.hidden && w >= NARROW ? S.dom.insp.offsetWidth + 24 : 0);
    var k = clamp(Math.min((aw - pad * 2) / Math.max(1, x2 - x1), (hh - pad * 2) / Math.max(1, y2 - y1)), zmin(), 1);
    if (w < NARROW && k < PHONE_K) {
      // телефон: не мельчить до нечитаемого — начало пути крупно у левого края, дальше палец ведёт по холсту (Н7)
      k = PHONE_K;
      S.V = { k: k, x: pad / 2 - x1 * k, y: pad / 2 - y1 * k };
    } else S.V = { k: k, x: pad + (aw - pad * 2 - (x2 - x1) * k) / 2 - x1 * k, y: pad + (hh - pad * 2 - (y2 - y1) * k) / 2 - y1 * k };
    applyView();
  }
  /* «Куда пойдёт» снимается щелчком мимо; подсветка «На схеме» — только своей кнопкой или Esc. */
  function clearTrace() { if (!S.trace || S.trace.focus) return; S.trace = null; markSelection(); updateEdgeClasses(); }
  /* То же и для «Куда пойдёт трафик?»: её панель закрывается вместе с подсветкой. */
  function dropTrace() { if (S.sim) closeSim(); else clearTrace(); }
  function retrace() {
    if (S.sim) S.trace = simPathTrace();
    else if (S.pin) S.trace = pinTrace();
    else if (S.trace && !S.trace.focus) S.trace = traceOf(S.trace.dev);
    else if (S.trace) { S.trace = focusOf(S.trace.focus); if (!S.trace) dropFocus(); }
    traceEdges();
  }

  /* ── «На схеме»: подсветка человека или устройства ───────────────────
     Человек — объединение путей его устройств (traceOf со всеми входами): узел и строки устройств, стрелки к входам и
     сами входы, линии маршрутов (свой, «как у пользователя», «по умолчанию» — пунктиром), профиль правил и его строки
     (реклама → блок, Россия → напрямую), куда уходит трафик и выход, через который устройства идут сейчас (now
     сервера). Маршруты и профили — из черновика (effRoute/effProfile): то, что будет после «Применить», как у
     «Куда пойдёт». Адрес раздела (#scheme?focus=u-…&from=…) ведёт app.js: mount получает ctx.nav, дальше — show(). */
  function focusRef(id) {
    id = String(id || '');
    return /^u-[0-9a-f]{8}$/.test(id) ? { t: 'user', id: id } : /^d-[0-9a-f]{8}$/.test(id) ? { t: 'dev', id: id } : null;
  }
  /* Человек — объединение ОДИНОЧНЫХ путей его устройств (каждое — через свой текущий или первый вход); человек без
     устройств — путь его нового устройства (его правила и маршрут). Устройство — его путь. */
  function focusOf(f) {
    var I = S.idx, d = f.t === 'dev' ? I.dev[f.id] : null, uid = f.t === 'user' ? f.id : d && d.user;
    if (!uid || !I.user[uid] || (f.t === 'dev' && !d)) return null;
    var T = { focus: f, nodes: {}, devs: {}, edges: {}, rows: {}, cands: {}, alts: {}, steps: [] };
    T.nodes[uid] = 1;
    function merge(one) {
      if (!one) return;
      ['nodes', 'devs', 'edges', 'cands', 'alts'].forEach(function (k) { for (var x in one[k] || {}) if (OWN.call(one[k], x)) T[k][x] = 1; });
      for (var r in one.rows) if (OWN.call(one.rows, r) && !T.rows[r]) T.rows[r] = one.rows[r];
    }
    var dids = f.t === 'dev' ? [f.id] : (S.order[uid] || []).filter(function (x) { return I.dev[x]; });
    dids.forEach(function (did) { merge(traceOf(did)); });
    if (f.t === 'user' && !dids.length) merge(whoTrace(uid));
    return T;
  }
  /* f — {t, id} или null; quiet — адрес страницы уже такой (пришёл от app.js), сообщать не надо. */
  function setFocus(f, quiet, from) {
    var had = !!S.focus, gone = false;
    if (f && S.sim) { S.simLast = { who: S.sim.who, target: S.sim.target }; S.sim = null; S.dom.root.classList.remove('has-sim'); S.dom.insp.classList.remove('kg-insp--sim'); }
    if (f && S.pin) unpin(true);                                     // подсветка человека или устройства заменяет закреплённый путь
    S.focus = f ? { t: f.t, id: f.id, from: from || '' } : null;
    if (S.trace && (S.trace.focus || S.focus)) S.trace = null;      // подсветка заменяет «Куда пойдёт»
    if (S.focus) {
      S.trace = focusOf(S.focus);
      if (S.trace) S.sel = null;
      else { S.focus = null; gone = true; toast(t('graph.focus.gone')); }
    }
    renderFocusBar();
    if (S.mode === 'canvas') {
      if (S.onlyActive) structureChanged(true);
      else { markSelection(); updateEdgeClasses(); updateEdgesLive(); }
      if (S.trace && S.trace.focus) {
        // плашка — вверху окна, под ней карта целиком; подсвеченное — целиком на карте
        // к началу страницы: заголовок, плашка и карта видны, плашка не уходит под липкую шапку панели (Н9)
        try { window.scrollTo({ top: 0, behavior: 'auto' }); } catch (_) { window.scrollTo(0, 0); }
        fitNodes(traceIds(S.trace));
      }
    }
    renderInspector();
    if ((!quiet || gone) && (had || gone || S.focus) && typeof S.ctx.onFocus === 'function') S.ctx.onFocus(S.focus ? S.focus.id : null);
  }
  /* Человека или устройство удалили (бот, другая вкладка): подсветку снять и сказать почему. */
  function dropFocus() {
    S.focus = null;
    renderFocusBar();
    toast(t('graph.focus.gone'));
    if (typeof S.ctx.onFocus === 'function') S.ctx.onFocus(null);
  }
  function lc1(s) { s = String(s || ''); return s.charAt(0).toLowerCase() + s.slice(1); }
  function profTitle(p) { return (S.base.profiles.titles || {})[p] || p; }
  function focusRoute(via, from) { return t('graph.focus.via', { via: lc1(viaTitle(via)), from: from }); }
  /* Плашка над картой: кто показан, его маршрут и правила; «Вернуться к настройкам» и «Снять подсветку». */
  function renderFocusBar() {
    var el = S.dom.focusBar, f = S.focus;
    clear(el);
    if (!f || !S.idx || !S.draft) { el.hidden = true; sizeStage(); return; }
    var I = S.idx, who, text;
    if (f.t === 'user') {
      var U = S.draft.users[f.id], dids = (S.order[f.id] || []).filter(function (x) { return I.dev[x]; });
      var route = U.route ? focusRoute(U.route.via, t('graph.focus.own')) : focusRoute(S.draft.def.via, t('graph.from.default'));
      var ownN = dids.filter(function (x) { return S.draft.devices[x].route; }).length;
      if (ownN) route += ', ' + tn('graph.focus.ownDevs', ownN);
      var profs = [];
      (dids.length ? dids.map(function (x) { return effProfile(x)[0]; }) : [userProfile(f.id)]).forEach(function (p) { if (profs.indexOf(p) < 0) profs.push(p); });
      who = [t('graph.focus.userShown'), ' ', h('b', { text: nodeTitle(f.id) })];
      text = t('graph.focus.userText', { devs: tn('graph.devices', dids.length), route: route, rules: profs.map(profTitle).join(', ') });
      // путь каждого устройства — через один вход; у кого их несколько, через другие путь бывает другим (Н5)
      var multi = dids.filter(function (x) { return simKinds(x).length > 1; }).length;
      if (multi) text += '. ' + tn('graph.focus.userVia', multi);
    } else {
      var d = I.dev[f.id], r = effRoute(f.id);
      who = [t('graph.focus.devShown'), ' ', h('b', { text: d.name })];
      text = t('graph.focus.devText', { user: nodeTitle(d.user), route: focusRoute(r.via, t('graph.from.' + r.from)), rules: profTitle(effProfile(f.id)[0]) });
      var kinds = simKinds(f.id), via = pathInbound(f.id);
      var oth = kinds.filter(function (k) { return k !== via; });
      if (kinds.length > 1 && via) text += '. ' + t(oth.some(xrayIn) ? 'graph.focus.devViaSocks' : 'graph.focus.devVia',
        { via: kindTitle(via), others: oth.map(kindTitle).join(', '), them: oth.length > 1 ? t('graph.focus.them') : t('graph.focus.it') });
    }
    var btns = h('div', { cls: 'kg-btns' });
    if (typeof S.ctx.back === 'function') btns.appendChild(btn('kg-btn--tertiary kg-btn--sm', 'focus-back', f.from === 'list' ? t('graph.focus.backList') : t('graph.focus.back'), 'undo'));
    btns.appendChild(btn('kg-btn--ghost kg-btn--sm', 'focus-off', t('graph.focus.off'), 'xmark', { title: t('graph.focus.offHint'), 'aria-label': t('graph.focus.off') }));
    append(el, [h('span', { cls: 'kg-focus__ico', 'aria-hidden': 'true' }, icon('eye', 16)), h('span', { cls: 'kg-focus__t' }, who.concat([' — ', text])), btns]);
    el.hidden = false;
  }
  /* Вид, который просит адрес: до первой отрисовки — только выбрать режим (её сделает setBase). */
  function prepNav(nav) {
    var m = nav.view === 'list' ? 'list' : (focusRef(nav.focus) || simRef(nav.trace)) ? 'canvas' : null;
    if (!m) return;
    S.userMode = true;
    if (S.mode !== m) { S.mode = m; S.dom.root.setAttribute('data-view', m); syncViewSeg(); }
  }
  function finishNav(nav) {
    if (simRef(nav.trace) && nav.pin) { pinNav(simRef(nav.trace)); return; }
    if (simRef(nav.trace)) { simNav(simRef(nav.trace)); return; }
    if (S.sim) closeSim(true, true);                    // адрес без проверки: ни панели, ни закреплённого пути
    if (S.pin) unpin(true);
    if (nav.view === 'list') { if (S.focus) setFocus(null, true); flashListItem(nav.focus); return; }
    var f = focusRef(nav.focus);
    if (f || S.focus) setFocus(f, true, nav.from);
  }
  /* Адрес раздела сменился (#scheme?focus=…, «Назад» браузера): показать, не перемонтируя схему. */
  function show(nav) {
    if (!S) return;
    nav = nav && typeof nav === 'object' ? nav : {};
    if (!S.base) { S.nav = nav; return; }
    var m = nav.view === 'list' ? 'list' : (focusRef(nav.focus) || simRef(nav.trace)) ? 'canvas' : null;
    if (m) {
      S.userMode = true;
      if (m === 'list' && S.focus) setFocus(null, true);
      setMode(m);
    }
    finishNav(nav);
  }
  /* «На схеме» в списке: через адрес страницы (ссылку можно отправить, «Назад» вернёт к списку). */
  function goMap(id) {
    if (typeof S.ctx.showOnMap === 'function') S.ctx.showOnMap(id, 'list');
    else show({ focus: id, from: 'list' });
  }
  /* Вернулись в список с карты: строка человека или устройства — в виду, коротко подсвечена, фокус на её кнопке. */
  function flashListItem(id) {
    var f = focusRef(id); if (!f) return;
    var el = S.dom.list.querySelector(f.t === 'user' ? '[data-user="' + f.id + '"]' : '[data-dev="' + f.id + '"]');
    if (!el) return;
    el.classList.add('is-flash');
    try { el.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }); } catch (_) { el.scrollIntoView(); }
    var b = el.querySelector('[data-act="on-map"]');
    if (b) { try { b.focus({ preventScroll: true }); } catch (_) { b.focus(); } }
    later('flash', 2400, function () { el.classList.remove('is-flash'); });
  }

  /* ── Инспектор ──────────────────────────────────────────────────────── */
  function inspHead(icEl, eyebrow, title, chip) {
    return h('div', { cls: 'kg-insp__head' }, [h('span', { cls: 'kg-nd__ico kg-nd__ico--lg' }, icEl), h('div', { cls: 'kg-insp__t' }, [h('div', { cls: 'kg-nd__eb', text: eyebrow }), h('div', { cls: 'kg-insp__name', text: title })]), chip || null,
      btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'close', '', 'xmark', { 'aria-label': t('graph.close') })]);
  }
  function kv(k, v) { return h('div', { cls: 'kg-kv' }, [h('span', { text: k }), h('b', { text: v })]); }
  function field(label, ctl) { return h('label', { cls: 'kg-lbl' }, [h('span', { text: label }), ctl]); }
  function routeSelect(act, cur, inheritLabel, id) {
    var sel = h('select', { cls: 'kg-field', 'data-act': act, 'data-id': id });
    if (inheritLabel) sel.appendChild(h('option', { value: 'inherit', text: inheritLabel, selected: !cur }));
    targetsList().forEach(function (o) { sel.appendChild(h('option', { value: o.id, text: o.label, selected: !!cur && cur.via === o.id, disabled: o.disabled || null })); });
    return sel;
  }
  /* Политика отказа: свой маршрут — его политика; без своего — первая строка «как у …: <унаследованная>» (не «блок»
     по умолчанию поля: T-scheme-3.2.15, Н1). Выбор блока или «напрямую» без своего маршрута задаёт свой маршрут с той же
     целью и этой политикой. inh — inheritedRoute(); у «напрямую» и «блока» политики нет. */
  function failSelect(act, cur, id, inh) {
    var r = cur || inh, sel = h('select', { cls: 'kg-field', 'data-act': act, 'data-id': id, disabled: !r || r.via === 'direct' || r.via === 'block' || null });
    // у «блока» и «напрямую» политики отказа нет — хранимую (она вернётся с пулом) не показываем
    if (r && (r.via === 'direct' || r.via === 'block')) { sel.appendChild(h('option', { value: '', selected: true, text: t('graph.fail.none') })); return sel; }
    if (!cur && inh) sel.appendChild(h('option', { value: 'inherit', selected: true,
      text: t(inh.from === 'user' ? 'graph.fail.asUser' : 'graph.fail.asDefault', { name: inh.name || '', fail: t('graph.fail.' + (inh.fail || 'block')) }) }));
    ['block', 'direct'].forEach(function (f) { sel.appendChild(h('option', { value: f, text: t('graph.fail.' + f), selected: !!cur && (cur.fail || 'block') === f })); });
    return sel;
  }
  function profileSelect(act, cur, inheritLabel, id) {
    var sel = h('select', { cls: 'kg-field', 'data-act': act, 'data-id': id });
    sel.appendChild(h('option', { value: 'inherit', text: inheritLabel, selected: !cur }));
    (S.base.profiles.names || []).forEach(function (p) { sel.appendChild(h('option', { value: p, text: (S.base.profiles.titles || {})[p] || p, selected: cur === p })); });
    return sel;
  }
  /* ⚠РФ (SPEC-stage1-rf-mark-builtin §5.1, R4): S.base.profiles.rf = {mode, required, need {block, consent, orgs}}.
     Выбрали «Россия напрямую» в инспекторе, а согласия нет — под выбором блок владельца и отдельная галочка, снятая по
     умолчанию; галочка — у всех намерений rules черновика поле confirm_not_rf и orgs показанного блока. Пробный проход
     отдаёт results[].need — окно «Применить» показывает тот же блок, «Применить» неактивна без галочки. */
  function rfInfo() { return (S.base && S.base.profiles && S.base.profiles.rf) || null; }
  function rfAsks() { var rf = rfInfo(); return !rf || rf.required !== false; }
  function rfBox(need) {
    var n = need || (rfInfo() || {}).need || {};
    var cb = h('input', { type: 'checkbox', 'data-act': 'rf-tick', checked: S.rfTick || null });
    cb.setAttribute('data-orgs', JSON.stringify(n.orgs || []));
    return h('div', { cls: 'kg-rf', 'data-rf': 'box' }, [h('div', { cls: 'kg-alert', role: 'alert', text: n.block || t('graph.rf.need') }),
      h('label', { cls: 'kg-rf__tick' }, [cb, h('span', { text: n.consent || t('graph.rf.consent') })])]);
  }
  function rfOffMark(prof) {
    var rf = rfInfo();
    return prof === 'ru_direct' && rf && rf.mode === 'need' ? h('p', { cls: 'kg-muted', 'data-rf': 'off', text: t('graph.rf.off') }) : null;
  }
  /* Галочка дана под набором «Россия напрямую» черновика (rfKey в момент щелчка): набор изменился — отмена, «Убрать
     ошибочные», отсев устаревших намерений, новый выбор — галочка снята (ТЗ §5.1 «снят выбор — галочка сбрасывается»). */
  function rfKey() {
    return pending().filter(function (it) { return it.type === 'rules' && it.profile === 'ru_direct'; }).map(intentKey).sort().join(',');
  }
  function rfReset() { S.rfTick = false; S.rfOrgs = null; S.rfFor = null; }
  function rfSync() { if (S.rfTick && S.rfFor !== rfKey()) rfReset(); }
  // намерения с галочкой (её ставят под блоком в инспекторе или в окне «Применить»)
  function withRf(list) {
    rfSync();
    if (!S.rfTick) return list;
    return list.map(function (it) {
      if (it.type !== 'rules') return it;
      var c = {}; for (var k in it) if (OWN.call(it, k)) c[k] = it[k];
      c.confirm_not_rf = true;
      c.orgs = (S.rfOrgs || []).slice();               // без orgs сервер не примет галочку панели (П-4)
      return c;
    });
  }
  function targetsList() {
    var out = [{ id: 'pool', label: t('graph.pool') }];
    S.idx.exits.forEach(function (n) {
      var dup = dupExit(n.id);
      out.push({ id: n.id, label: t('graph.exitNamed', { name: n.data.name || n.data.id }) + (n.data.missing ? ' — ' + t('graph.exit.missing') : dup ? ' — ' + t('graph.exit.dup') : n.data.state === 'dead' ? ' — ' + t('graph.exit.dead') : ''),
        disabled: n.data.missing || dup });
    });
    out.push({ id: 'direct', label: t('graph.direct') }, { id: 'block', label: t('graph.block') });
    return out;
  }

  /* Поле «Российские сайты» (SPEC-groups §8.3): устройство — «Как у пользователя | Напрямую с сервера | Через «Дом» | …»,
     пользователь — «Напрямую с сервера | Через «Дом» | …» (своего нет — по умолчанию узла, это «напрямую»). Без «Россия
     напрямую» неактивно с подсказкой; выбрана группа — строка про интернет-провайдера роутера. Узел без российских
     групп и без своего значения — поля нет (как в 3.3.7.9). Запись — намерение ru_via (сервер проверяет id и группу). */
  function ruField(kind, id) {
    var I = S.idx, D = kind === 'dev' ? S.draft.devices[id] : S.draft.users[id]; if (!D) return null;
    var own = D.ru_via || null;
    if (!I.ruGroups.length && !own) return null;
    var sel = h('select', { cls: 'kg-field', 'data-act': kind === 'dev' ? 'ru-dev' : 'ru-user', 'data-id': id });
    if (kind === 'dev') {
      var U = S.draft.users[D.user] || {};
      sel.appendChild(h('option', { value: 'inherit', selected: !own, text: t('graph.ru.asUser', { what: lc1(ruValueText(U.ru_via && ruKnown(U.ru_via) ? U.ru_via : 'direct')) }) }));
      sel.appendChild(h('option', { value: 'direct', selected: own === 'direct', text: t('graph.ru.direct') }));
    } else sel.appendChild(h('option', { value: 'direct', selected: !own || own === 'direct', text: t('graph.ru.direct') }));
    I.ruGroups.forEach(function (n) { sel.appendChild(h('option', { value: n.data.group, selected: own === n.data.group, text: t('graph.ru.via', { name: n.data.name }) })); });
    // своё значение — группа, которой больше нет: сервер считает «напрямую»; видно, что выбрать заново
    if (own && !ruKnown(own)) sel.appendChild(h('option', { value: '', selected: true, disabled: true, text: t('graph.ru.gone') }));
    var on, why = '';
    if (kind === 'dev') on = effProfile(id)[0] === 'ru_direct';
    else on = userProfile(id) === 'ru_direct' || (S.order[id] || []).some(function (did) { return effProfile(did)[0] === 'ru_direct'; });
    if (!on) why = t('graph.ru.onlyRd');
    else if (!ruNodeOk()) { on = false; why = ruNoteText() || t('graph.ru.onlyRd'); }
    if (!on) sel.disabled = true;
    var box = h('div', { cls: 'kg-ru', 'data-ru': 'field' }, [field(t('graph.ru.field'), sel)]);
    if (!on) { box.appendChild(h('p', { cls: 'kg-muted', 'data-ru': 'off', text: why })); return box; }
    if (kind === 'dev') {
      var o = ruOf(id);
      if (o.group && o.acts) box.appendChild(h('p', { cls: 'kg-ru__isp', 'data-ru': 'isp', text: t('graph.ru.ispDev') }));
      else if (o.group) box.appendChild(h('p', { cls: 'kg-muted', 'data-ru': 'idle', text: t('graph.ru.idleDev') }));
      var nt = ruDevNow(id); if (nt) box.appendChild(h('p', { cls: 'kg-muted', 'data-ru': 'now', text: nt }));
    } else if (own && own !== 'direct' && ruKnown(own)) box.appendChild(h('p', { cls: 'kg-ru__isp', 'data-ru': 'isp', text: t('graph.ru.ispUser') }));
    return box;
  }
  /* «сейчас: через «Дом», выход «Роутер»» — ответ сервера (exits.ru_target), пока черновик выбора не меняет. */
  function ruDevNow(did) {
    var b = S.idx.dev[did], o = ruOf(did), bn = b && b.ru; if (!o || !o.acts || !bn || bn.acts !== true || bn.via !== o.via) return '';
    if (!o.group) return t('graph.ru.nowDevDirect');
    var n = S.idx.node['rug:' + o.via], w = bn.now; if (!n || !w) return '';
    if (w.step === 'exit') return t('graph.ru.nowDevExit', { group: n.data.name, name: ruExitName(n, w.exit) });
    if (w.step === 'last_direct') return t('graph.ru.nowDevLastDirect', { group: n.data.name });
    if (w.step === 'last_block') return t('graph.ru.nowDevLastBlock', { group: n.data.name });
    return '';
  }

  function renderInspector() {
    var box = S.dom.insp, s = S.sel;
    if (S.sim || (S.trace && !S.trace.focus)) { renderTraceInsp(); return; }
    if (!s) { box.hidden = true; clear(box); return; }
    clear(box);
    var body = h('div', { cls: 'kg-insp__body' });
    if (s.t === 'dev') devInsp(box, body, s.id);
    else if (s.t === 'edge') edgeInsp(box, body, s.id);
    else nodeInsp(box, body, s.id);
    box.appendChild(body);
    box.hidden = false;
  }
  function devInsp(box, body, did) {
    var d = S.idx.dev[did], D = S.draft.devices[did]; if (!d) return;
    var ld = S.live.devices[did];
    box.appendChild(inspHead(icon(DEV_ICON[d.kind] || 'gadget', 18), t('graph.kind.' + d.kind) + ' · ' + nodeTitle(d.user), d.name,
      h('span', { cls: 'kg-chip' + (ld && ld.online ? ' kg-chip--ok' : ''), 'data-live': 'dev-state', text: ld ? (ld.online ? t('graph.dev.online') : t('graph.dev.offline')) : t('graph.nodata') })));
    body.appendChild(h('div', { cls: 'kg-kvs' }, [kv(t('graph.insp.platform'), t('graph.platform.' + d.platform)), kv(t('graph.insp.login'), d.login || '—'),
      kv(t('graph.now'), '', null)]));
    var nowKv = body.lastChild.lastChild.lastChild; nowKv.setAttribute('data-live', 'dev-rate'); nowKv.textContent = ld && ld.online ? fmtRate(devRate(did)) : '—';
    if (!d.active) body.appendChild(h('div', { cls: 'kg-alert', text: t('graph.dev.inactive') }));
    body.appendChild(accessCard(d, D));
    // маршрут и правила
    var u = S.draft.users[d.user], inh = u.route ? t('graph.insp.asUser', { name: viaTitle(u.route.via) }) : t('graph.insp.asDefault', { name: viaTitle(S.draft.def.via) });
    body.appendChild(field(t('graph.insp.route'), routeSelect('route-dev', D.route, inh, did)));
    body.appendChild(field(t('graph.insp.fail'), failSelect('fail-dev', D.route, did, inheritedRoute('dev', did))));
    if (S.base.profiles.supported) {
      var up = S.draft.users[d.user].rules;
      body.appendChild(field(t('graph.insp.rules'), profileSelect('rules-dev', D.rules, up ? t('graph.insp.rulesInherit', { name: profTitle(up) })
        : t('graph.insp.rulesInheritDefault', { name: profTitle(defProfile()) }), did)));
      if (D.rules === 'ru_direct' && d.rules !== 'ru_direct' && rfAsks()) body.appendChild(rfBox());
      else { var off = rfOffMark(D.rules || up || defProfile()); if (off) body.appendChild(off); }
    }
    var rfd = ruField('dev', did); if (rfd) body.appendChild(rfd);
    var bs = [btn('kg-btn--tertiary kg-btn--sm', 'trace-dev', t('graph.sim.open'), 'magnifier', { 'data-id': did })];
    if (typeof S.ctx.back === 'function') bs.push(btn('kg-btn--ghost kg-btn--sm', 'issue-dev', t('graph.acc.issue'), 'link', { 'data-id': did }));
    body.appendChild(h('div', { cls: 'kg-btns' }, bs));
    body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.insp.devHelp') }));
  }
  /* «Протоколы» карточки устройства (до 3.3.2 — «Способы подключения»; ревизия схемы, Б1): выданные входы — это настройки, установленные на самом устройстве. Их не
     «перепривязывают»: отзыв стирает ключи (связь через них пропадёт, включить снова — новые ключи), выдача — новые
     настройки, которые нужно отдать устройству. Путь трафика идёт через один из входов — он отмечен. */
  function accessCard(d, D) {
    var did = d.id, ld = S.live.devices[did], path = pathInbound(did), list = h('div', { cls: 'kg-acc' });
    var kinds = d.access.slice(); D.access.forEach(function (k) { if (kinds.indexOf(k) < 0) kinds.push(k); });
    kinds.forEach(function (k) {
      var inBase = d.access.indexOf(k) >= 0, inDraft = D.access.indexOf(k) >= 0, ip = ipOf(did, k);
      var miss = !S.idx.node['in:' + k], off = !miss && !kindOn(k), here = !!(ld && ld.online && ld.inbound === k);
      var state = !inDraft ? t('graph.acc.willRevoke') : !inBase ? t('graph.acc.willGrant') : miss ? t('graph.dev.kindMissing') : off ? t('graph.dev.kindOff')
        : here ? t('graph.inbound.here') : t('graph.inbound.waits');
      var act = !inDraft || !inBase ? btn('kg-btn--ghost kg-btn--sm', 'acc-undo', t('graph.acc.undo'), 'undo', { 'data-dev': did, 'data-kind': k })
        : btn('kg-btn--ghost kg-btn--sm kg-btn--dangerish', 'acc-ask', t('graph.acc.revoke'), 'xmark', { 'data-dev': did, 'data-kind': k });
      list.appendChild(h('div', { cls: 'kg-acc__i' + (!inDraft ? ' is-gone' : !inBase ? ' is-new' : '') + (k === path ? ' is-path' : '') }, [
        h('span', { cls: 'kg-acc__n' }, [kindTitle(k), k === path ? h('small', { text: t('graph.acc.path') }) : null]),
        ip ? h('span', { cls: 'kg-acc__ip', text: ip }) : null,
        h('span', { cls: 'kg-chip' + (here ? ' kg-chip--ok' : miss || off ? ' kg-chip--bad' : ''), text: state }), act]));
      if (S.askRevoke === did + '|' + k && inDraft && inBase) {
        var why = [t('graph.acc.revokeWhy', { kind: kindTitle(k) })];
        if (here) why.push(t('graph.acc.revokeOnline'));
        if (D.access.length === 1) why.push(t('graph.acc.revokeLast'));
        list.appendChild(h('div', { cls: 'kg-alert kg-acc__ask', role: 'alert' }, [h('p', { text: why.join(' ') }),
          h('div', { cls: 'kg-btns' }, [btn('kg-btn--danger kg-btn--sm', 'acc-revoke', t('graph.acc.revokeYes', { kind: kindTitle(k) }), 'xmark', { 'data-dev': did, 'data-kind': k }),
            btn('kg-btn--ghost kg-btn--sm', 'acc-cancel', t('graph.cancel'))])]));
      }
    });
    if (!kinds.length) list.appendChild(h('p', { cls: 'kg-muted', text: t('graph.acc.none') }));
    // выдать ещё способ: установленные входы, которых у устройства нет
    var free = S.view.nodes.filter(function (n) { return n.type === 'inbound' && kinds.indexOf(n.data.kind) < 0; });
    var add = null;
    if (free.length) {
      var sel = h('select', { cls: 'kg-field', 'data-act': 'acc-pick', 'aria-label': t('graph.acc.more') });
      sel.appendChild(h('option', { value: '', text: t('graph.acc.pick') }));
      free.forEach(function (n) {
        var unsuited = d.suits && d.suits.indexOf(n.data.kind) < 0 && d.platform !== 'other';
        sel.appendChild(h('option', { value: n.data.kind, text: n.data.title + (unsuited ? ' — ' + t('graph.cc.unsuitedShort') : '') + (n.data.enabled ? '' : ' — ' + t('graph.inbound.disabled')) }));
      });
      add = h('div', { cls: 'kg-acc__add' }, [h('span', { cls: 'kg-sim__row' }, [sel, btn('kg-btn--tertiary kg-btn--sm', 'acc-grant', t('graph.acc.grant'), 'plus', { 'data-dev': did })]),
        h('p', { cls: 'kg-muted', text: t('graph.acc.grantHelp') })]);
    }
    return h('div', { cls: 'kg-lbl kg-acc-card' }, [h('span', { text: t('graph.acc.title') }), h('p', { cls: 'kg-muted', text: t('graph.acc.help') }), list,
      add ? h('span', { text: t('graph.acc.more') }) : null, add]);
  }
  function nodeInsp(box, body, id) {
    var n = S.idx.node[id]; if (!n) return;
    var dt = n.data;
    if (n.type === 'user') {
      var U = S.draft.users[id];
      box.appendChild(inspHead(icon('persons', 18), t('graph.user'), dt.name));
      var ml = h('div', { cls: 'kg-mlist' });
      (S.order[id] || []).forEach(function (did) {
        var d = S.idx.dev[did]; if (!d) return;
        ml.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i', 'data-act': 'go-dev', 'data-id': did }, [h('span', { cls: 'kg-dv__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 15)),
          h('span', { cls: 'kg-mlist__t' }, [h('b', { text: d.name }), h('span', { cls: 'kg-muted', text: S.draft.devices[did].access.map(shortKind).join(', ') || t('graph.dev.noAccess') })])]));
      });
      if (!(dt.devices || []).length) ml.appendChild(h('p', { cls: 'kg-muted', text: t('graph.user.noDevicesHelp') }));
      body.appendChild(ml);
      body.appendChild(field(t('graph.insp.userRoute'), routeSelect('route-user', U.route, t('graph.insp.asDefault', { name: viaTitle(S.draft.def.via) }), id)));
      body.appendChild(field(t('graph.insp.fail'), failSelect('fail-user', U.route, id, inheritedRoute('user', id))));
      if (S.base.profiles.supported) {
        body.appendChild(field(t('graph.insp.rules'), profileSelect('rules-user', U.rules, t('graph.insp.rulesDefault', { name: profTitle(defProfile()) }), id)));
        if (U.rules === 'ru_direct' && dt.rules !== 'ru_direct' && rfAsks()) body.appendChild(rfBox());
        else { var uoff = rfOffMark(U.rules || defProfile()); if (uoff) body.appendChild(uoff); }
      }
      var rfu = ruField('user', id); if (rfu) body.appendChild(rfu);
      body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.insp.userHelp') }));
      if (!dt.enabled) body.appendChild(h('div', { cls: 'kg-alert', text: t('graph.user.disabled') }));
    } else if (n.type === 'inbound') {
      box.appendChild(inspHead(icon('plug', 18), t('graph.inbound'), dt.title));
      body.appendChild(h('div', { cls: 'kg-kvs' }, [kv(t('graph.insp.ports'), (dt.ports || []).join(', ') || '—'), kv(t('graph.insp.subnet'), dt.subnet || '—'),
        kv(t('graph.insp.state'), dt.enabled ? t('graph.inbound.enabled') : t('graph.inbound.disabled'))]));
      var ml2 = h('div', { cls: 'kg-mlist' });
      S.idx.devList.filter(function (d) { return S.draft.devices[d.id].access.indexOf(dt.kind) >= 0; }).forEach(function (d) {
        var ld = S.live.devices[d.id], here = ld && ld.online && ld.inbound === dt.kind;
        ml2.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i', 'data-act': 'go-dev', 'data-id': d.id }, [h('span', { cls: 'kg-dv__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 15)),
          h('span', { cls: 'kg-mlist__t' }, [h('b', { text: d.name }), h('span', { cls: 'kg-muted', text: ipOf(d.id, dt.kind) || nodeTitle(d.user) })]),
          h('span', { cls: 'kg-chip' + (here ? ' kg-chip--ok' : ''), text: here ? t('graph.inbound.here') : t('graph.inbound.waits') })]));
      });
      body.appendChild(ml2.childNodes.length ? ml2 : h('p', { cls: 'kg-muted', text: t('graph.inbound.help') }));
    } else if (n.type === 'rules') {
      box.appendChild(inspHead(icon('split', 18), t('graph.rules'), dt.title));
      var rl = h('div', { cls: 'kg-mlist' });
      (dt.categories || []).forEach(function (c, i) {
        rl.appendChild(h('div', { cls: 'kg-mlist__i' }, [h('span', { cls: 'kg-nd__idx', text: String(i + 1) }), h('span', { cls: 'kg-mlist__t' }, [h('b', { text: c.title })]),
          h('span', { cls: 'kg-tag', text: c.target === 'route' ? t('graph.rules.toRoute') : c.target === 'direct' ? t('graph.direct') : t('graph.block') })]));
      });
      body.appendChild(rl);
      body.appendChild(h('p', { cls: 'kg-muted', text: dt.profile === 'ru_direct' ? t('graph.rules.ruHelp') : t('graph.rules.allHelp') }));
      if (!dt.supported) body.appendChild(h('div', { cls: 'kg-alert', text: S.base.profiles.soon || t('graph.rules.soon') }));
      else if (dt.profile === 'ru_direct' && ruNoteText(dt)) body.appendChild(h('div', { cls: 'kg-alert', text: ruNoteText(dt) }));
      body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.rules.where') }));
    } else if (n.type === 'ru_group') {
      box.appendChild(inspHead(icon('router', 18), t('graph.ru.eyebrow'), dt.name));
      body.appendChild(h('div', { cls: ruNowCls(n), 'data-live': 'ru-now', text: ruNowText(n) }));
      var sl = h('div', { cls: 'kg-mlist' });
      (dt.exits || []).forEach(function (r) {
        sl.appendChild(h('div', { cls: 'kg-mlist__i' }, [h('span', { cls: 'kg-nd__idx', text: String(r.step) }), h('span', { cls: 'kg-mlist__t' }, [h('b', { text: r.name }),
          h('span', { cls: 'kg-muted', text: r.missing ? t('graph.exit.missing') : !r.enabled ? t('graph.exit.off') : exitSt(r.id, r.state) === 'alive' ? t('graph.exit.alive') : exitSt(r.id, r.state) === 'dead' ? t('graph.exit.dead') : t('graph.exit.unknown') })])]));
      });
      sl.appendChild(h('div', { cls: 'kg-mlist__i' }, [h('span', { cls: 'kg-nd__idx', text: '→' }), h('span', { cls: 'kg-mlist__t' }, [h('b', { text: t(dt.fail === 'block' ? 'graph.ru.lastBlockStep' : 'graph.ru.lastDirectStep') })])]));
      body.appendChild(field(t('graph.ru.steps'), sl));
      var here = [], idle = 0;
      S.idx.devList.forEach(function (d) { var o = ruOf(d.id); if (!o || o.via !== dt.group) return; if (o.acts && d.active) here.push(d); else idle += 1; });
      var dl = h('div', { cls: 'kg-mlist' });
      here.forEach(function (d) {
        dl.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i', 'data-act': 'go-dev', 'data-id': d.id }, [h('span', { cls: 'kg-dv__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 15)),
          h('span', { cls: 'kg-mlist__t' }, [h('b', { text: d.name }), h('span', { cls: 'kg-muted', text: nodeTitle(d.user) })])]));
      });
      body.appendChild(field(t('graph.ru.devicesHere', { n: here.length }), here.length ? dl : h('p', { cls: 'kg-muted', text: t('graph.ru.nobody') })));
      if (idle) body.appendChild(h('p', { cls: 'kg-muted', text: tn('graph.ru.idleN', idle) }));
      body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.ru.help') }));
      body.appendChild(h('div', { cls: 'kg-btns' }, h('a', { cls: 'kg-btn kg-btn--tertiary kg-btn--sm', href: '#exits', text: t('graph.ru.toExits') })));
    } else if (n.type === 'filter') {
      box.appendChild(inspHead(icon('ban', 18), t('graph.filter.eyebrow'), t('graph.filter')));
      var fl = h('div', { cls: 'kg-mlist' });
      (dt.rows || []).forEach(function (r) {
        fl.appendChild(h('div', { cls: 'kg-mlist__i' }, [h('span', { cls: 'kg-mlist__t' }, [h('b', { text: r.title })]),
          h('span', { cls: 'kg-tag', text: r.id === 'pass' ? t('graph.filter.toRules') : r.on ? t('graph.block') : t('graph.filter.off') })]));
      });
      body.appendChild(fl);
      body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.filter.help') }));
      body.appendChild(h('div', { cls: 'kg-btns' }, h('a', { cls: 'kg-btn kg-btn--tertiary kg-btn--sm', href: '#rules', text: t('graph.filter.toRulesPage') })));
    } else {
      var title = nodeTitle(id), isDef = S.draft.def.via === id;
      box.appendChild(inspHead(icon(n.type === 'exit' ? 'globe' : n.type === 'pool' ? 'pool' : n.type === 'direct' ? 'house' : 'ban', 18),
        n.type === 'exit' ? (dupExit(id) ? t('graph.exit.kind.dup') : t('graph.exit.kind.' + (dt.kind || 'bad'))) : n.type === 'pool' ? t('graph.pool.eyebrow') : t('graph.target'), title));
      if (n.type === 'exit') {
        body.appendChild(h('div', { cls: 'kg-kvs' }, [kv(t('graph.insp.host'), dt.host || '—'), kv(t('graph.insp.state'), dt.problem || dt.detail || '—'),
          kv(t('graph.insp.enabled'), dt.enabled ? t('graph.yes') : t('graph.no'))]));
      } else if (n.type === 'pool') {
        var pl = h('div', { cls: 'kg-mlist' });
        (dt.exits || []).forEach(function (eid, i) {
          var en = S.idx.node['exit:' + eid];
          pl.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i', 'data-act': 'go-node', 'data-id': 'exit:' + eid }, [h('span', { cls: 'kg-nd__idx', text: String(i + 1) }),
            h('span', { cls: 'kg-mlist__t' }, [h('b', { text: nodeTitle('exit:' + eid) }), h('span', { cls: 'kg-muted', text: en ? (en.data.problem || en.data.detail || '') : '' })])]));
        });
        // пустой пул: где добавить ключ (путь SSH-меню с номером от сервера) и — если маршрут при отказе блокирует — что
        // устройства шлюза сейчас без интернета (exits.add_hint, offline_empty)
        if (dt.offline) body.appendChild(h('div', { cls: 'kg-alert kg-alert--bad', role: 'alert', text: t('graph.pool.offline') }));
        body.appendChild(pl.childNodes.length ? pl : h('p', { cls: 'kg-muted', text: poolEmptyHelp(dt) }));
        body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.pool.help') }));
      } else body.appendChild(h('p', { cls: 'kg-muted', text: n.type === 'direct' ? t('graph.direct.help') : t('graph.block.help') }));
      var followers = S.idx.devList.filter(function (d) { var r = effRoute(d.id); return r && r.via === id; }).length;
      body.appendChild(kv(t('graph.insp.routedHere'), String(followers)));
      if (n.type === 'pool' || n.type === 'exit') {
        var fc = failCounts(id);
        if (fc.block || fc.direct) body.appendChild(kv(t('graph.insp.failHere'), t('graph.insp.failCounts', { b: fc.block, d: fc.direct })));
      }
      body.appendChild(h('div', { cls: 'kg-btns' }, [isDef ? h('span', { cls: 'kg-badge', text: t('graph.default.badge') })
        : btn('kg-btn--tertiary kg-btn--sm', 'make-default', t('graph.default.make'), 'check', { 'data-id': id, disabled: (dt && dt.missing) || dupExit(id) || null })]));
      // политика отказа маршрута по умолчанию — здесь, у его цели (ревизия С3: раньше её нельзя было сменить)
      if (isDef && n.type !== 'direct' && n.type !== 'block') body.appendChild(field(t('graph.insp.failDefault'), failSelect('fail-default', S.draft.def, 'default')));
    }
  }
  function poolEmptyHelp(dt) {
    return dt && dt.add_menu ? t('graph.pool.emptyHelp', { menu: dt.add_menu, what: dt.add_what || '' }) : t('graph.pool.emptyHelpOld');
  }
  function edgeInsp(box, body, k) {
    var e = S.view.byKey[k]; if (!e) return;
    var title = e.type === 'conn' ? devTitle(e.dev) + ' → ' + kindTitle(e.kind) : nodeTitle(e.s) + ' → ' + nodeTitle(e.t);
    box.appendChild(inspHead(icon('link', 18), t('graph.edge.' + e.type), title));
    if (e.type === 'fail') body.appendChild(h('div', { cls: 'kg-alert' + (e.fall === 'direct' ? ' kg-alert--bad' : ''), text: t('graph.edge.failNow.' + e.fall, { via: viaTitle(e.via) }) }));
    if (e.devs && e.devs.length) {
      var ml = h('div', { cls: 'kg-mlist' });
      e.devs.forEach(function (did) {
        var d = S.idx.dev[did]; if (!d) return;
        ml.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i', 'data-act': 'go-dev', 'data-id': did }, [h('span', { cls: 'kg-dv__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 15)),
          h('span', { cls: 'kg-mlist__t' }, [h('b', { text: d.name }), h('span', { cls: 'kg-muted', text: nodeTitle(d.user) })])]));
      });
      body.appendChild(field(t('graph.insp.devices'), ml));
    }
    var rate = sumRates(e.devs || []);
    if (e.devs && e.devs.length) body.appendChild(kv(t('graph.now'), liveFresh() ? fmtRate(rate) : '—'));
    body.appendChild(h('p', { cls: 'kg-muted', text: t('graph.edge.help.' + e.type) }));
    if (e.type === 'conn') body.appendChild(h('div', { cls: 'kg-btns' }, btn('kg-btn--tertiary kg-btn--sm', 'go-dev', t('graph.edge.openDev'), 'link', { 'data-id': e.dev })));
  }
  /* Панель «Куда пойдёт трафик?»: кто, домен или IP, «Проверить», примеры; ниже — ответ (renderSimOut). full —
     собрать панель заново (иначе меняется только ответ: поле ввода не теряет фокус и набранное). */
  function renderTraceInsp(full) {
    var box = S.dom.insp;
    if (!S.sim) { box.hidden = true; return; }
    if (!full && S.dom.simOut && box.contains(S.dom.simOut)) { renderSimOut(); box.hidden = false; return; }
    var sim = S.sim, I = S.idx;
    clear(box);
    box.classList.add('kg-insp--sim');
    // телефон: нижний лист — ручка сворачивает и разворачивает (Н-4)
    box.appendChild(h('button', { type: 'button', cls: 'kg-sheet__handle', 'data-act': 'sheet', 'aria-label': t('graph.sheet.toggle'), title: t('graph.sheet.toggle') }));
    box.appendChild(inspHead(icon('magnifier', 18), t('graph.sim.eyebrow'), t('graph.sim.title')));
    var body = h('div', { cls: 'kg-insp__body' });
    var sel = h('select', { cls: 'kg-field', 'data-act': 'sim-who' });
    if (I.devList.length) {
      var g1 = h('optgroup', { label: t('graph.sim.devices') });
      I.devList.forEach(function (d) { g1.appendChild(h('option', { value: d.id, text: d.name + ' · ' + nodeTitle(d.user), selected: sim.who === d.id })); });
      sel.appendChild(g1);
    }
    if (I.users.length) {
      var g2 = h('optgroup', { label: t('graph.sim.newOf') });
      I.users.forEach(function (u) { g2.appendChild(h('option', { value: u.id, text: t('graph.sim.newOfUser', { name: u.data.name }), selected: sim.who === u.id })); });
      sel.appendChild(g2);
    }
    sel.appendChild(h('option', { value: '-', text: t('graph.sim.newDefault'), selected: sim.who === '-' }));
    // «Через»: трафик идёт через ОДИН вход устройства — у устройства с несколькими входами его выбирают (SOCKS5 и VLESS решает
    // xray, остальные — ядро: это и есть разница путей); по умолчанию — тот, через который оно на связи, иначе первый
    var kinds = simKinds(sim.who), via = null;
    if (kinds.length > 1) {
      via = h('select', { cls: 'kg-field', 'data-act': 'sim-in' });
      kinds.forEach(function (k) { via.appendChild(h('option', { value: k, text: kindTitle(k) + (devOnline(sim.who) && (S.live.devices[sim.who] || {}).inbound === k ? ' · ' + t('graph.dev.online') : ''), selected: sim.inbound === k })); });
    }
    var inp = h('input', { cls: 'kg-field', type: 'text', 'data-act': 'sim-target', value: sim.target || '', placeholder: t('graph.sim.ph'),
      autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', inputmode: 'url', enterkeyhint: 'go', maxlength: '253' });
    var go = h('button', { type: 'submit', cls: 'kg-btn kg-btn--primary kg-btn--sm' }, [icon('magnifier', 14), h('span', { text: t('graph.sim.go') })]);
    var ex = h('div', { cls: 'kg-sim__ex' }, [h('span', { cls: 'kg-muted', text: t('graph.sim.examples') })]);
    SIM_EXAMPLES.forEach(function (x) { ex.appendChild(h('button', { type: 'button', cls: 'kg-chip kg-sim__chip', 'data-act': 'sim-ex', 'data-v': x, text: x })); });
    body.appendChild(h('form', { cls: 'kg-sim', 'data-act': 'sim-form', novalidate: true }, [
      field(t('graph.sim.who'), sel),
      via ? field(t('graph.sim.via'), via) : null,
      h('label', { cls: 'kg-lbl' }, [h('span', { text: t('graph.sim.target') }), h('span', { cls: 'kg-sim__row' }, [inp, go])]),
      ex]));
    S.dom.simOut = h('div', { cls: 'kg-sim__out', role: 'status', 'aria-live': 'polite' });
    body.appendChild(S.dom.simOut);
    box.appendChild(body);
    box.hidden = false;
    renderSimOut();
  }
  var SIM_ICON = { exit: 'globe', direct: 'house', block: 'ban' };
  function renderSimOut() {
    var out = S.dom.simOut, sim = S.sim;
    if (!out || !sim) return;
    clear(out);
    if (sim.busy) { out.appendChild(h('div', { cls: 'kg-busy' }, [h('span', { cls: 'kg-spin', 'aria-hidden': 'true' }), h('span', { text: t('graph.sim.busy') })])); return; }
    if (sim.err) { out.appendChild(h('div', { cls: 'kg-alert kg-alert--bad', text: sim.err })); return; }
    var res = sim.res;
    if (!res) {
      var T = S.trace && !S.trace.sim && !S.trace.focus ? S.trace : null;
      if (T) {
        out.appendChild(h('div', { cls: 'kg-sim__h', text: T.dev ? t('graph.sim.devPath', { name: devTitle(T.dev) }) : t('graph.sim.newPath') }));
        out.appendChild(stepsList(T.steps.map(function (s) { return { text: s.t, note: s.s, tone: s.warn ? 'warn' : '' }; })));
      }
      out.appendChild(h('p', { cls: 'kg-muted', text: t('graph.sim.help') }));
      return;
    }
    var v = res.verdict || 'exit';
    out.appendChild(h('div', { cls: 'kg-verdict kg-verdict--' + v }, [h('span', { cls: 'kg-verdict__ico', 'aria-hidden': 'true' }, icon(SIM_ICON[v] || 'info', 18)),
      h('div', { cls: 'kg-verdict__t' }, [h('span', { cls: 'kg-verdict__eb', text: t('graph.sim.for', { q: (res.query && res.query.value) || '' }) }), h('b', { text: res.summary || '' }),
      // с какого адреса сайт увидит этот трафик — сразу в итоге («непонятно новичку», T-scheme-3.2.16)
      res.origin ? h('span', { cls: 'kg-verdict__eb', text: res.origin }) : null])]));
    // телефон: «Показать на схеме» — сразу под итогом, не после всех шагов (Н-4); на широком экране кнопки нет
    out.appendChild(h('div', { cls: 'kg-btns kg-sim__map' }, btn('kg-btn--tertiary kg-btn--sm', 'sim-map', t('graph.sim.toMap'), 'eye')));
    out.appendChild(stepsList(res.steps || []));
    var notes = (res.notes || []).slice();
    if (pending().length) notes.push(t('graph.sim.saved'));
    if (notes.length) {
      var ul = h('ul', { cls: 'kg-todo kg-todo--warn' });
      notes.forEach(function (n) { ul.appendChild(h('li', { text: n })); });
      out.appendChild(ul);
    }
  }
  /* Телефон: пришёл ответ — итог в листе на виду (лист не выше 55 % экрана, форма над итогом его бы прятала). */
  function simOutInView() {
    if (!S.dom.root.classList.contains('is-narrow')) return;
    var body = S.dom.insp.querySelector('.kg-insp__body'), v = S.dom.simOut && S.dom.simOut.querySelector('.kg-verdict');
    if (!body || !v || !v.getBoundingClientRect) return;
    body.scrollTop += v.getBoundingClientRect().top - body.getBoundingClientRect().top - 8;
  }
  function stepsList(steps) {
    var ol = h('ol', { cls: 'kg-trace' });
    steps.forEach(function (s, i) {
      ol.appendChild(h('li', { cls: (i === steps.length - 1 ? 'is-last' : '') + (s.tone === 'warn' ? ' is-warn' : s.tone === 'bad' ? ' is-bad' : '') },
        [h('b', { text: s.text }), s.note ? h('span', { text: s.note }) : null]));
    });
    return ol;
  }
  function updateInspLive() {
    var box = S.dom.insp;
    if (S.sel && S.sel.t === 'dev') {
      var ld = S.live.devices[S.sel.id], st = box.querySelector('[data-live="dev-state"]'), rt = box.querySelector('[data-live="dev-rate"]');
      if (st) { setText(st, ld ? (ld.online ? t('graph.dev.online') : t('graph.dev.offline')) : t('graph.nodata')); st.className = 'kg-chip' + (ld && ld.online ? ' kg-chip--ok' : ''); }
      if (rt) setText(rt, ld && ld.online ? fmtRate(devRate(S.sel.id)) : '—');
    }
    var rn = S.sel && S.sel.t === 'node' && S.idx.node[S.sel.id], rnow = rn && rn.type === 'ru_group' ? box.querySelector('[data-live="ru-now"]') : null;
    if (rnow) { setText(rnow, ruNowText(rn)); if (rnow.className !== ruNowCls(rn)) rnow.className = ruNowCls(rn); }
  }

  /* ── Список по этапам (телефон) ─────────────────────────────────────── */
  function setMode(m) {
    if (m === S.mode) return;
    S.mode = m;
    S.dom.root.setAttribute('data-view', m);
    syncViewSeg();
    if (!S.base) return;
    if (m === 'list') renderList();
    else {
      renderNodes();
      var missing = S.view.nodes.filter(function (n) { return !S.pos[n.id]; }).map(function (n) { return n.id; });
      if (missing.length === S.view.nodes.length && missing.length) firstLayout();
      else if (missing.length) { placeNew(missing); applyPositions(); }
      sizeStage();                                         // холст был скрыт — его высота по окну (Н-4)
      renderEdges(); fit(); updateLive(); markSelection();
    }
  }
  /* Раскладки ещё нет (первое открытие): колонки, строки устройств в новом порядке, сохранить для всех вкладок. */
  function firstLayout() {
    columnsLayout();
    reorderRows();
    applyPositions();
    Object.keys(S.pos).forEach(function (id) { S.dirtyPos[id] = 1; });
    S.dirtyOrders = {}; Object.keys(S.order).forEach(function (u) { S.dirtyOrders[u] = S.order[u].slice(); });
    later('save', SAVE_DELAY, saveLayout);
  }
  /* Строки устройств — в порядке S.order: те же элементы переставляются, затем заново меряются точки. */
  function reorderRows() {
    S.idx.users.forEach(function (u) {
      var el = S.nodeEl[u.id], box = el && el.querySelector('.kg-nd__devs');
      if (!box) return;
      (S.order[u.id] || []).forEach(function (did) { var r = S.rowEl[did]; if (r && r.row.parentNode === box) box.appendChild(r.row); });
    });
    measure();
  }
  /* Путь устройства в списке — та же одна цепочка: вход (один; остальные — мелко) → правила → маршрут → выход. */
  function pathChips(did) {
    var P = devPath(did), ld = S.live.devices[did], box = h('span', { cls: 'kg-lr__path' }), D = S.draft.devices[did];
    function arrow() { box.appendChild(h('span', { cls: 'kg-pp__arr', 'aria-hidden': 'true', text: '→' })); }
    if (!P.active) { box.appendChild(h('span', { cls: 'kg-pp kg-pp--bad', text: t('graph.dev.off') })); return box; }
    if (!P.inbound) { box.appendChild(h('span', { cls: 'kg-pp kg-pp--bad', text: D.access.length ? t('graph.dev.noPath') : t('graph.dev.noAccess') })); return box; }
    var others = D.access.filter(function (k) { return k !== P.inbound && kindOn(k); });
    box.appendChild(h('span', { cls: 'kg-pp kg-pp--in' }, [(ld && ld.online && ld.inbound === P.inbound ? '● ' : '') + shortKind(P.inbound),
      others.length ? h('small', { text: t('graph.list.alsoIn', { names: others.map(shortKind).join(', ') }) }) : null]));
    arrow();
    var rol = P.profile === 'ru_direct' ? ruOf(did) : null;
    box.appendChild(h('span', { cls: 'kg-pp kg-pp--rules' }, [profTitle(P.profile), h('small', { text: t('graph.pfrom.' + P.pfrom) }),
      // коротко, как чип устройства на схеме: у соседних строк одна и та же длинная фраза — шум (ui_audit DUP)
      rol && rol.acts && rol.group ? h('small', { title: t('graph.ru.chipHint'), text: t('graph.ru.chip', { name: nodeTitle('rug:' + rol.via) }) }) : null]));
    arrow();
    box.appendChild(h('span', { cls: 'kg-pp kg-pp--' + (P.rfrom === 'device' ? 'own' : 'exit') }, [viaTitle(P.via), h('small', { text: t('graph.from.' + P.rfrom) })]));
    if (P.target !== P.via) { arrow(); box.appendChild(h('span', { cls: 'kg-pp kg-pp--bad', text: t('graph.list.fail', { where: viaTitle(P.target) }) })); }
    else if (P.balancer) { arrow(); box.appendChild(h('span', { cls: 'kg-pp kg-pp--exit', text: t(P.balancer === 'any' ? 'graph.list.any' : 'graph.list.balancer') })); }
    else if (P.exit) { arrow(); box.appendChild(h('span', { cls: 'kg-pp kg-pp--exit', text: nodeTitle('exit:' + P.exit) })); }
    return box;
  }
  function renderList() {
    var box = clear(S.dom.list), I = S.idx;
    if (!I) return;
    var lst = h('div', { cls: 'kg-lst' });
    if (pending().length) lst.appendChild(h('div', { cls: 'kg-lst__bar' }, [h('span', { text: tn('graph.pending', pending().length) }),
      btn('kg-btn--ghost kg-btn--sm', 'undo', t('graph.undo'), 'undo'), btn('kg-btn--primary kg-btn--sm', 'apply', t('graph.apply'), 'check')]));
    I.users.forEach(function (u) {
      lst.appendChild(h('div', { cls: 'kg-lst__u', 'data-user': u.id }, [h('span', { cls: 'kg-nd__ava', 'aria-hidden': 'true', text: initials(u.data.name) }), h('b', { text: u.data.name }),
        h('span', { cls: 'kg-muted', 'data-live': 'ucnt-' + u.id, text: tn('graph.devices', (u.data.devices || []).length) }),
        h('div', { cls: 'kg-btns' }, [btn('kg-btn--ghost kg-btn--sm', 'on-map', t('graph.list.onMap'), 'eye', { 'data-id': u.id, title: t('graph.list.onMapHint') }),
          btn('kg-btn--ghost kg-btn--sm', 'send-user', t('graph.list.sendAll'), null, { 'data-id': u.id })])]));
      (S.order[u.id] || []).forEach(function (did) {
        var d = I.dev[did]; if (!d) return;
        var ld = S.live.devices[did];
        lst.appendChild(h('div', { cls: 'kg-lr', 'data-dev': did }, [
          h('div', { cls: 'kg-lr__who' }, [h('span', { cls: 'kg-nd__ico' }, icon(DEV_ICON[d.kind] || 'gadget', 16)),
            h('span', { cls: 'kg-lr__t' }, [h('b', { text: d.name }), h('span', { cls: 'kg-muted', text: t('graph.kind.' + d.kind) + ' · ' + t('graph.platform.' + d.platform) })]),
            h('span', { cls: 'kg-chip' + (ld && ld.online ? ' kg-chip--ok' : ''), 'data-live': 'lrate-' + did, text: ld && ld.online ? fmtRate(devRate(did)) : t('graph.dev.offline') })]),
          pathChips(did),
          h('div', { cls: 'kg-btns' }, [btn('kg-btn--tertiary kg-btn--sm', 'send-dev', t('graph.list.send'), null, { 'data-id': did }),
            btn('kg-btn--ghost kg-btn--sm', 'on-map', t('graph.list.onMap'), 'eye', { 'data-id': did, title: t('graph.list.onMapHint') }),
            btn('kg-btn--ghost kg-btn--sm', 'trace-dev', t('graph.trace'), 'magnifier', { 'data-id': did })])
        ]));
      });
    });
    if (!I.users.length) lst.appendChild(h('p', { cls: 'kg-muted', text: t('graph.empty') }));
    lst.appendChild(h('div', { cls: 'kg-lst__u kg-lst__u--x' }, h('b', { text: t('graph.list.exits') })));
    I.exits.forEach(function (n) {
      lst.appendChild(h('div', { cls: 'kg-lr kg-lr--x' }, [h('span', { cls: 'kg-nd__ico' }, icon('globe', 15)),
        h('span', { cls: 'kg-lr__t' }, [h('b', { text: n.data.name || n.data.id }), h('span', { cls: 'kg-muted', text: n.data.host || '' })]),
        h('span', { cls: 'kg-chip', 'data-live': 'lexit-' + n.data.id, text: '' })]));
    });
    if (I.ruGroups.length) lst.appendChild(h('div', { cls: 'kg-lst__u kg-lst__u--x' }, h('b', { text: t('graph.list.ruGroups') })));
    I.ruGroups.forEach(function (n) {
      lst.appendChild(h('div', { cls: 'kg-lr kg-lr--x' }, [ruIco(15),
        h('span', { cls: 'kg-lr__t' }, [h('b', { text: n.data.name }), h('span', { cls: 'kg-muted', 'data-live': 'lru-' + n.data.group, text: ruNowText(n) })])]));
    });
    box.appendChild(lst);
    updateListLive();
  }
  function updateListLive() {
    var box = S.dom.list;
    S.idx.ruGroups.forEach(function (n) { var el = box.querySelector('[data-live="lru-' + n.data.group + '"]'); if (el) setText(el, ruNowText(n)); });
    S.idx.devList.forEach(function (d) {
      var el = box.querySelector('[data-live="lrate-' + d.id + '"]'), ld = S.live.devices[d.id];
      if (el) { setText(el, ld && ld.online ? fmtRate(devRate(d.id)) : t('graph.dev.offline')); el.className = 'kg-chip' + (ld && ld.online ? ' kg-chip--ok' : ''); }
    });
    S.idx.exits.forEach(function (n) {
      var el = box.querySelector('[data-live="lexit-' + n.data.id + '"]'); if (!el) return;
      var st = n.data.missing ? 'missing' : exitSt(n.data.id, n.data.state);
      setChip(el, st === 'alive' ? ['ok', t('graph.exit.alive')] : st === 'dead' || st === 'missing' ? ['bad', st === 'dead' ? t('graph.exit.dead') : t('graph.exit.missing')] : st === 'nodata' ? ['', t('graph.nodata')] : ['', t('graph.exit.unknown')]);
    });
  }
  /* «Направить в…»: лист с целями маршрута. */
  function openSend(kind, id) {
    var cur = kind === 'dev' ? S.draft.devices[id].route : S.draft.users[id].route;
    var title = kind === 'dev' ? devTitle(id) : nodeTitle(id);
    var inh = kind === 'dev' ? (S.draft.users[S.idx.dev[id].user].route ? t('graph.insp.asUser', { name: viaTitle(S.draft.users[S.idx.dev[id].user].route.via) }) : t('graph.insp.asDefault', { name: viaTitle(S.draft.def.via) }))
      : t('graph.insp.asDefault', { name: viaTitle(S.draft.def.via) });
    var opts = [{ id: 'inherit', label: inh }].concat(targetsList());
    var list = h('div', { cls: 'kg-mlist', role: 'radiogroup' });
    opts.forEach(function (o) {
      var on = o.id === 'inherit' ? !cur : !!cur && cur.via === o.id;
      list.appendChild(h('button', { type: 'button', cls: 'kg-mlist__i' + (on ? ' is-on' : ''), role: 'radio', 'aria-checked': on ? 'true' : 'false', 'data-act': 'send-pick',
        'data-kind': kind, 'data-id': id, 'data-v': o.id, disabled: o.disabled || null }, [h('span', { cls: 'kg-mlist__t' }, h('b', { text: o.label }))]));
    });
    openDialog([h('div', { cls: 'kg-dlg__h' }, [h('div', null, [h('div', { cls: 'kg-nd__eb', text: t('graph.list.send') }), h('h4', { text: title })]),
      btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'dlg-close', '', 'xmark', { 'aria-label': t('graph.close') })]), list]);
  }

  /* ── «Применить»: сначала dry_run, потом — по подтверждению ─────────── */
  function openApply(note) {
    var list = pending();
    if (!list.length) { toast(t('graph.apply.nothing')); return; }
    S.busy = true; updateToolbar();
    call('POST', '/scheme/apply', { intents: withRf(list), dry_run: true }).then(function (r) {
      if (!S) return;
      S.busy = false; updateToolbar();
      if (!r.ok || !r.data || !r.data.dry_run) { showApplyError(r, list); return; }
      showDiff(r.data, list, note);
    });
  }
  function changeLine(c) {
    var s = c.op === '+' ? '+' : c.op === '-' ? '−' : '~';
    return h('li', { cls: 'kg-chg kg-chg--' + (c.op === '+' ? 'add' : c.op === '-' ? 'del' : 'mod') }, [h('span', { cls: 'kg-chg__s', 'aria-hidden': 'true', text: s }), h('span', { text: c.text })]);
  }
  function showDiff(doc, list, note) {
    var items = [h('div', { cls: 'kg-dlg__h' }, [h('div', null, [h('div', { cls: 'kg-nd__eb', text: t('graph.apply.title') }), h('h4', { text: tn('graph.changes', (doc.changes || []).length) })]),
      btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'dlg-close', '', 'xmark', { 'aria-label': t('graph.close') })])];
    if (note) items.push(h('div', { cls: 'kg-alert', text: note }));
    var bad = (doc.results || []).filter(function (r) { return !r.ok; });
    if (bad.length) {
      var ul = h('ul', { cls: 'kg-chgs' });
      bad.forEach(function (r) { ul.appendChild(h('li', { cls: 'kg-chg kg-chg--bad' }, [h('span', { cls: 'kg-chg__s', text: '!' }), h('span', { text: intentText(list[r.i]) + ': ' + (r.error || '') })])); });
      items.push(h('div', { cls: 'kg-nd__eb', text: t('graph.apply.errors') }), ul);
    }
    if ((doc.changes || []).length) {
      var ch = h('ul', { cls: 'kg-chgs' });
      doc.changes.forEach(function (c) { ch.appendChild(changeLine(c)); });
      items.push(ch);
    } else if (!bad.length) items.push(h('p', { cls: 'kg-muted', text: t('graph.apply.noop') }));
    // кого затронет: путь трафика (маршрут, отказ, правила) и подключение (выданный или отозванный вход) — С8
    var paths = (doc.affected || []).filter(function (a) { return a.kind !== 'access'; });
    var accs = (doc.affected || []).filter(function (a) { return a.kind === 'access'; });
    if (paths.length) {
      var af = h('ul', { cls: 'kg-todo' });
      paths.forEach(function (a) { af.appendChild(h('li', { text: t('graph.apply.affected', { name: a.name, before: a.before, after: a.after }) })); });
      items.push(h('div', { cls: 'kg-nd__eb', text: tn('graph.apply.affectedN', paths.length) }), af);
    }
    if (accs.length) {
      var ac = h('ul', { cls: 'kg-todo kg-todo--warn' });
      accs.forEach(function (a) { ac.appendChild(h('li', { text: t('graph.apply.accessLine', { name: a.name, text: a.text }) })); });
      items.push(h('div', { cls: 'kg-nd__eb', text: tn('graph.apply.accessN', accs.length) }), ac);
    }
    // что изменение перезапустит у ВСЕХ (xray шлюза, DNS) — ревизия С5
    if ((doc.disruptions || []).length) {
      var dl = h('ul', { cls: 'kg-todo kg-todo--warn' });
      doc.disruptions.forEach(function (x) { dl.appendChild(h('li', { text: x.text })); });
      items.push(h('div', { cls: 'kg-nd__eb', text: t('graph.apply.disruptions') }), dl);
    }
    if ((doc.warnings || []).length) {
      var wl = h('ul', { cls: 'kg-todo kg-todo--warn' });
      doc.warnings.forEach(function (w) { wl.appendChild(h('li', { text: w })); });
      items.push(h('div', { cls: 'kg-nd__eb', text: t('graph.apply.warnings') }), wl);
    }
    var need = null;
    (doc.results || []).forEach(function (r) { if (r.ok && r.need && !need) need = r.need; });
    if (need) { rfReset(); items.push(h('div', { cls: 'kg-nd__eb', text: t('graph.rf.need') }), rfBox(need)); }
    if (S.base.gateway && !S.base.gateway.enabled) items.push(h('p', { cls: 'kg-muted', text: t('graph.apply.gwOff') }));
    items.push(h('div', { cls: 'kg-busy', hidden: true, 'data-busy': '1' }, [h('span', { cls: 'kg-spin', 'aria-hidden': 'true' }), h('span', { text: t('graph.apply.busy') })]));
    var foot = h('div', { cls: 'kg-dlg__f' }, [btn('kg-btn--tertiary kg-btn--sm', 'dlg-close', t('graph.cancel'))]);
    if (bad.length) foot.appendChild(btn('kg-btn--ghost kg-btn--sm', 'drop-bad', t('graph.apply.dropBad'), null));
    var ok = doc.valid && !doc.noop;
    foot.appendChild(btn('kg-btn--primary kg-btn--sm', 'do-apply', t('graph.apply.confirm'), 'check', { disabled: !ok || !!need || null }));
    items.push(foot);
    S.applyCtx = { version: doc.version, list: list, bad: bad.map(function (r) { return r.i; }), rf: !!need, ok: ok };
    openDialog(items, 'kg-dlg--wide');
  }
  function doApply() {
    var A = S.applyCtx; if (!A) return;
    var busy = S.dom.dlg.querySelector('[data-busy]');
    if (busy) busy.hidden = false;
    Array.prototype.forEach.call(S.dom.dlg.querySelectorAll('button'), function (b) { b.disabled = true; });
    S.busy = true; updateToolbar();
    call('POST', '/scheme/apply', { intents: withRf(A.list), version: A.version }).then(function (r) {
      if (!S) return;
      S.busy = false;
      var d = r.data || {};
      if (r.ok && d.ok && d.graph) {
        S.intents = []; S.hist = S.hist.filter(function (e) { return e.kind !== 'intent'; });
        S.applyCtx = null; rfReset();
        setBase(d.graph, {});
        var warns = (d.warnings || []).filter(Boolean);
        if (warns.length) {
          var ul = h('ul', { cls: 'kg-todo kg-todo--warn' }); warns.forEach(function (w) { ul.appendChild(h('li', { text: w })); });
          openDialog([h('div', { cls: 'kg-dlg__h' }, [h('h4', { text: t('graph.apply.doneWarn') })]), ul,
            h('div', { cls: 'kg-dlg__f' }, btn('kg-btn--primary kg-btn--sm', 'dlg-close', t('graph.ok')))]);
        } else { closeDialog(); toast(d.applied === false ? t('graph.apply.noop') : tn('graph.apply.done', (d.changes || []).length)); }
        return;
      }
      if (d.data && d.data.reason === 'stale') { closeDialog(); load({ keep: true }); openApply(t('graph.apply.stale')); return; }
      // ⚠РФ: сервер просит галочку (старая вкладка, блок изменился, пока читали) — снова окно с его блоком
      if (d.data && d.data.need === 'rf_consent') { rfReset(); closeDialog(); openApply(t('graph.rf.tickFirst')); return; }
      if (d.code === 'timeout') load({ keep: true });       // узел не ответил вовремя: изменение могло примениться — перечитать
      showApplyError(r, A.list);
    });
  }
  function showApplyError(r, list) {
    var d = r.data || {}, items = [h('div', { cls: 'kg-dlg__h' }, [h('h4', { text: t('graph.apply.failed') }), btn('kg-btn--ghost kg-btn--icon kg-btn--sm', 'dlg-close', '', 'xmark', { 'aria-label': t('graph.close') })]),
      h('div', { cls: 'kg-alert kg-alert--bad', text: errText(r) })];
    var res = (d.data && d.data.results) || [];
    if (res.length) {
      var ul = h('ul', { cls: 'kg-chgs' });
      res.forEach(function (x) { if (!x.ok && !x.skipped) ul.appendChild(h('li', { cls: 'kg-chg kg-chg--bad' }, [h('span', { cls: 'kg-chg__s', text: '!' }), h('span', { text: intentText(list[x.i]) + ': ' + x.error })])); });
      items.push(ul);
    }
    items.push(h('p', { cls: 'kg-muted', text: t('graph.apply.keptDraft') }));
    items.push(h('div', { cls: 'kg-dlg__f' }, btn('kg-btn--primary kg-btn--sm', 'dlg-close', t('graph.ok'))));
    S.busy = false; updateToolbar();
    openDialog(items);
  }
  function dropBad() {
    var A = S.applyCtx; if (!A) return;
    var keys = {}; A.bad.forEach(function (i) { if (A.list[i]) keys[intentKey(A.list[i])] = 1; });
    S.intents = S.intents.filter(function (it) { return !keys[intentKey(it)]; });
    resyncHist();
    rebuildDraft(); structureChanged(); closeDialog();
    if (pending().length) openApply();
  }
  function intentText(it) {
    if (!it) return '';
    if (it.type === 'grant') return t('graph.it.grant', { dev: devTitle(it.device), kind: kindTitle(it.kind) });
    if (it.type === 'revoke') return t('graph.it.revoke', { dev: devTitle(it.device), kind: kindTitle(it.kind) });
    if (it.type === 'route') {
      var who = it['default'] ? t('graph.it.default') : it.device ? devTitle(it.device) : nodeTitle(it.user);
      return it.via === 'inherit' ? t('graph.it.unroute', { who: who }) : t('graph.it.route', { who: who, where: viaTitle(it.via) });
    }
    if (it.type === 'ru_via') return t('graph.it.ru', { who: it.device ? devTitle(it.device) : nodeTitle(it.user),
      what: it.via === 'inherit' ? t(it.device ? 'graph.ru.itAsUser' : 'graph.ru.itDefault') : lc1(ruValueText(it.via)) });
    if (it.type === 'rules') return t('graph.it.rules', { who: it.device ? devTitle(it.device) : nodeTitle(it.user), name: it.profile === 'inherit' ? t('graph.it.inherit') : ((S.base.profiles.titles || {})[it.profile] || it.profile) });
    return it.type;
  }

  /* ── Окно, тост ─────────────────────────────────────────────────────── */
  function openDialog(kids, cls) {
    var back = S.dom.dlg;
    clear(back);
    S.dialog = { prevFocus: document.activeElement };
    var box = h('div', { cls: 'kg-dlg' + (cls ? ' ' + cls : ''), role: 'dialog', 'aria-modal': 'true' }, kids);
    back.appendChild(box); back.hidden = false;
    var f = box.querySelector('button:not([disabled]), select, input'); if (f) f.focus();
  }
  function closeDialog() {
    if (!S.dialog) return;
    var prev = S.dialog.prevFocus;
    if (S.applyCtx && S.applyCtx.rf) rfReset();              // окно с блоком закрыли — галочка из него не остаётся
    S.dialog = null; S.applyCtx = null;
    S.dom.dlg.hidden = true; clear(S.dom.dlg);
    if (prev && prev.focus && document.body.contains(prev)) prev.focus();
  }
  function toast(text, withUndo) {
    var el = S.dom.toast;
    clear(el);
    el.appendChild(h('span', { text: text }));
    if (withUndo) el.appendChild(btn('kg-btn--ghost kg-btn--sm', 'undo', t('graph.toast.undo')));
    el.hidden = false;
    el.classList.remove('is-in'); void el.offsetWidth; el.classList.add('is-in');
    later('toast', withUndo ? 5200 : 3800, function () { S.dom.toast.hidden = true; });
  }

  /* ── Панели ─────────────────────────────────────────────────────────── */
  function updateToolbar() {
    if (!S.dom.apply) return;
    var n = S.base ? pending().length : 0;
    S.dom.applyN.textContent = n ? t('graph.applyN', { n: n }) : t('graph.apply');
    S.dom.apply.disabled = !n || S.busy;
    S.dom.undo.disabled = !S.hist.length;
    S.dom.onlyAct.setAttribute('aria-pressed', String(S.onlyActive));
    S.dom.root.classList.toggle('has-pending', !!n);
  }
  function renderGwNote() {
    var g = S.base.gateway || {}, el = S.dom.gwNote;
    if (g.enabled) { el.hidden = true; return; }
    el.textContent = t('graph.gwOff');
    el.hidden = false;
  }
  function renderEmpty() {
    var el = S.dom.empty;
    if (S.idx.users.length) { el.hidden = true; return; }
    clear(el).appendChild(h('div', null, [h('b', { text: t('graph.emptyTitle') }), h('p', { text: t('graph.empty') })]));
    el.hidden = false;
  }

  /* ── События ────────────────────────────────────────────────────────── */
  function onClick(e) {
    var b = e.target.closest('[data-act]');
    if (!b || !S.dom.root.contains(b)) {
      if (e.target === S.dom.dlg) closeDialog();
      return;
    }
    if (b.tagName === 'SELECT') return;
    var a = b.getAttribute('data-act'), id = b.getAttribute('data-id');
    switch (a) {
      case 'apply': openApply(); break;
      case 'do-apply': doApply(); break;
      case 'drop-bad': dropBad(); break;
      case 'dlg-close': closeDialog(); break;
      case 'undo': undo(); break;
      case 'view': S.userMode = true; if (b.getAttribute('data-v') === 'list' && S.focus) setFocus(null); setMode(b.getAttribute('data-v')); break;
      case 'only-active': S.onlyActive = !S.onlyActive; structureChanged(true);
        toast(!S.onlyActive ? t('graph.onlyActive.off') : S.idx.devList.some(function (x) { return devOnline(x.id); }) ? t('graph.onlyActive.on') : t('graph.onlyActive.none')); break;
      case 'trace': openSim({}); break;
      case 'trace-dev': openSim({ who: id }); break;
      case 'sim-ex': if (S.sim) { S.sim.target = b.getAttribute('data-v') || ''; var inp = S.dom.insp.querySelector('[data-act="sim-target"]'); if (inp) inp.value = S.sim.target; runSim(); } break;
      case 'sim-map':          // телефон: лист сворачивается, путь — на схеме с начала, не мельче 50 % (Н-4)
        S.dom.insp.classList.add('is-min'); if (S.trace) fitNodes(traceIds(S.trace)); break;
      case 'sheet': S.dom.insp.classList.toggle('is-min'); break;
      case 'pin-off': unpin(); if (S.mode === 'canvas') fit(true); break;
      case 'pin-open': if (S.pin) openSim({ who: S.pin.who, target: S.pin.target, inbound: S.pin.inbound, run: true }); break;
      case 'layout': autoLayout(); break;
      case 'reload': load({ keep: true }); break;
      case 'zin': zoomCenter(1.2); break;
      case 'zout': zoomCenter(1 / 1.2); break;
      case 'fit': fit(true); break;
      case 'close': dropTrace(); S.sel = null; S.dom.insp.hidden = true; markSelection(); updateEdgeClasses(); break;
      case 'go-dev': select({ t: 'dev', id: id }); if (S.idx.dev[id]) centerOn(S.idx.dev[id].user); break;
      case 'go-node': select({ t: 'node', id: id }); centerOn(id); break;
      case 'acc-ask': S.askRevoke = b.getAttribute('data-dev') + '|' + b.getAttribute('data-kind'); renderInspector(); break;
      case 'acc-cancel': S.askRevoke = null; renderInspector(); break;
      case 'acc-revoke': S.askRevoke = null; addIntent({ type: 'revoke', device: b.getAttribute('data-dev'), kind: b.getAttribute('data-kind') }); break;
      case 'acc-undo': {
        var ud = b.getAttribute('data-dev'), uk = b.getAttribute('data-kind'), inD = S.draft.devices[ud] && S.draft.devices[ud].access.indexOf(uk) >= 0;
        addIntent({ type: inD ? 'revoke' : 'grant', device: ud, kind: uk }, true); break;
      }
      case 'acc-grant': {
        var pick = b.closest('.kg-acc__add'), sk = pick && pick.querySelector('[data-act="acc-pick"]');
        if (!sk || !sk.value) { toast(t('graph.acc.pickFirst')); break; }
        addIntent({ type: 'grant', device: b.getAttribute('data-dev'), kind: sk.value }); break;
      }
      case 'issue-dev': if (typeof S.ctx.back === 'function') S.ctx.back(id, 'settings'); break;
      case 'del-edge': removeEdge(id, b.getAttribute('data-i')); break;
      case 'make-default': addIntent({ type: 'route', 'default': true, via: id }); break;
      case 'send-dev': openSend('dev', id); break;
      case 'on-map': goMap(id); break;
      case 'focus-off': setFocus(null); break;
      case 'focus-back': if (S.focus && typeof S.ctx.back === 'function') S.ctx.back(S.focus.id, S.focus.from); break;
      case 'send-user': openSend('user', id); break;
      case 'send-pick': {
        var kind = b.getAttribute('data-kind'), v = b.getAttribute('data-v'), it = { type: 'route', via: v };
        if (kind === 'dev') it.device = id; else it.user = id;
        closeDialog(); addIntent(it); break;
      }
      default: break;
    }
  }
  function onChange(e) {
    var el = e.target, a = el.getAttribute && el.getAttribute('data-act'); if (!a) return;
    var id = el.getAttribute('data-id'), v = el.value;
    if (a === 'route-dev' || a === 'route-user') {
      var it = { type: 'route', via: v };
      if (a === 'route-dev') it.device = id; else it.user = id;
      addIntent(it);
    } else if (a === 'fail-default') {
      addIntent({ type: 'route', 'default': true, via: S.draft.def.via, fail: v });
    } else if (a === 'fail-dev' || a === 'fail-user') {
      var cur = a === 'fail-dev' ? S.draft.devices[id].route : S.draft.users[id].route;
      if (v === 'inherit' || !v) return;
      if (!cur) cur = inheritedRoute(a === 'fail-dev' ? 'dev' : 'user', id);     // своя политика = свой маршрут с той же целью
      var it2 = { type: 'route', via: cur.via, fail: v };
      if (a === 'fail-dev') it2.device = id; else it2.user = id;
      addIntent(it2);
    } else if (a === 'rules-dev' || a === 'rules-user') {
      var it3 = { type: 'rules', profile: v };
      if (a === 'rules-dev') it3.device = id; else it3.user = id;
      if (v !== 'ru_direct') rfReset();                       // снят выбор — галочка сбрасывается
      addIntent(it3);
      renderInspector();
    } else if (a === 'ru-dev' || a === 'ru-user') {
      if (!v) return;
      // у пользователя «напрямую» — без своего значения: по умолчанию узла это и есть «напрямую с сервера»
      var it4 = { type: 'ru_via', via: a === 'ru-user' && v === 'direct' ? 'inherit' : v };
      if (a === 'ru-dev') it4.device = id; else it4.user = id;
      addIntent(it4);
      renderInspector();
    } else if (a === 'rf-tick') {
      S.rfTick = !!el.checked;
      try { S.rfOrgs = JSON.parse(el.getAttribute('data-orgs') || '[]'); } catch (x) { S.rfOrgs = null; }
      S.rfFor = S.rfTick ? rfKey() : null;
      var go = S.dom.dlg.querySelector('[data-act="do-apply"]');
      if (go && S.applyCtx && S.applyCtx.rf) go.disabled = !S.rfTick || !S.applyCtx.ok;
    } else if (a === 'sim-who' && S.sim) { S.sim.who = v || '-'; S.sim.picked = false; S.sim.inbound = simInbound(S.sim.who); S.sim.res = null; structureChanged(); renderTraceInsp(true); runSim(); }
    else if (a === 'sim-in' && S.sim) { S.sim.inbound = v || null; S.sim.picked = true; S.sim.res = null; structureChanged(); renderTraceInsp(true); runSim(); }
  }
  function onKey(e) {
    var tag = (e.target.tagName || '').toLowerCase();
    if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z') && tag !== 'input' && tag !== 'textarea') { e.preventDefault(); undo(); return; }
    if (e.key === 'Escape') {
      if (S.gesture && S.gesture.type === 'connect') { clearTargets(); S.gesture = null; return; }
      if (S.dialog) { closeDialog(); return; }
      if (S.sel || S.sim || (S.trace && !S.trace.focus)) { dropTrace(); S.sel = null; S.dom.insp.hidden = true; markSelection(); updateEdgeClasses(); S.dom.stage.focus(); return; }
      if (S.focus) { e.preventDefault(); setFocus(null); }
      else if (S.pin) { e.preventDefault(); unpin(); }
      return;
    }
    if (S.dialog) { trapFocus(e); return; }
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    var row = e.target.closest && e.target.closest('.kg-dv'), nd = e.target.closest && e.target.closest('.kg-nd');
    if ((e.key === 'Enter' || e.key === ' ') && (row || nd) && e.target === (row || nd)) {
      e.preventDefault();
      select(row ? { t: 'dev', id: row.getAttribute('data-dev') } : { t: 'node', id: nd.getAttribute('data-id') });
      return;
    }
    if ((e.key === 'Delete' || e.key === 'Backspace') && S.sel && S.sel.t === 'edge') { e.preventDefault(); removeEdge(S.sel.id); return; }
    if (e.target === S.dom.stage || nd) {
      if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomCenter(1.2); return; }
      if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomCenter(1 / 1.2); return; }
      if (e.key === '0') { e.preventDefault(); fit(true); return; }
    }
    if (S.sel && S.sel.t === 'node' && /^Arrow/.test(e.key) && editable()) {
      e.preventDefault();
      var id = S.sel.id, p = S.pos[id], s = e.shiftKey ? 32 : 8; if (!p) return;
      var before = {}; before[id] = { x: p.x, y: p.y };
      var np = { x: p.x, y: p.y };
      if (e.key === 'ArrowLeft') np.x = Math.max(0, p.x - s); if (e.key === 'ArrowRight') np.x = p.x + s;
      if (e.key === 'ArrowUp') np.y = Math.max(0, p.y - s); if (e.key === 'ArrowDown') np.y = p.y + s;
      S.pos[id] = np; moveNodeEl(id); layoutEdges();
      S.hist.push({ kind: 'move', before: before }); if (S.hist.length > HIST_MAX) S.hist.shift();
      S.dirtyPos[id] = 1; later('save', SAVE_DELAY, saveLayout); updateToolbar();
    }
  }
  function trapFocus(e) {
    if (e.key !== 'Tab') return;
    var f = Array.prototype.filter.call(S.dom.dlg.querySelectorAll('button, select, input, [tabindex="0"]'), function (x) { return !x.disabled && !x.hidden; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  /* ── Раскладка: сохранение с версией ────────────────────────────────── */
  function saveLayout(retried) {
    if (!S || !S.base) return;
    var ids = Object.keys(S.dirtyPos), orders = S.dirtyOrders;
    if (!ids.length && !orders) return;
    if (S.saving) { later('save', SAVE_DELAY, saveLayout); return; }
    var positions = {};
    ids.forEach(function (id) { var p = S.pos[id]; if (p && S.idx.node[id]) positions[id] = [Math.round(p.x), Math.round(p.y)]; });
    var body = { version: S.layoutVersion, positions: positions };
    if (orders) body.orders = orders;
    S.dirtyPos = {}; S.dirtyOrders = null; S.saving = true;
    call('PUT', '/scheme/layout', body).then(function (r) {
      if (!S) return;
      S.saving = false;
      if (r.ok && r.data && typeof r.data.version === 'number') { S.layoutVersion = r.data.version; return; }
      var d = r.data && r.data.data;
      ids.forEach(function (id) { S.dirtyPos[id] = 1; });
      if (orders) S.dirtyOrders = orders;
      if (d && typeof d.version === 'number' && retried !== true) {
        // другая вкладка сохранила раньше: берём её позиции для блоков, которые мы не двигали, и повторяем
        S.layoutVersion = d.version;
        var moved = false;
        Object.keys(d.positions || {}).forEach(function (id) {
          if (S.dirtyPos[id] || !S.idx.node[id] || !Array.isArray(d.positions[id])) return;
          S.pos[id] = { x: d.positions[id][0], y: d.positions[id][1] }; moveNodeEl(id); moved = true;
        });
        if (moved) layoutEdges();
        saveLayout(true);
        return;
      }
      toast(t('graph.layout.notSaved'));
    });
  }

  /* ── «Выровнять»: колонки, порядок по барицентру, перестановки (прототип, columnsLayout) ── */
  function layoutEdgeList() {
    return S.view.edges.filter(function (e) { return S.pos[e.s] && S.pos[e.t] && e.s !== e.t && e.state !== 'gone'; });
  }
  function crossings(es) {
    var s = es.map(function (e) { var f = S.pos[e.s], tt = S.pos[e.t]; return { x1: f.x + nodeW(e.s), y1: f.y + portOff(e.s, 'out', e.sh), x2: tt.x, y2: tt.y + nodeH(e.t) / 2, f: e.s, fp: e.sh, t: e.t }; });
    var c = 0;
    function o(ax, ay, bx, by, cx, cy) { var v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); return v > 0 ? 1 : v < 0 ? -1 : 0; }
    for (var i = 0; i < s.length; i++) for (var k = i + 1; k < s.length; k++) {
      var A = s[i], B = s[k];
      if ((A.f === B.f && A.fp === B.fp) || A.t === B.t) continue;
      if (o(A.x1, A.y1, A.x2, A.y2, B.x1, B.y1) * o(A.x1, A.y1, A.x2, A.y2, B.x2, B.y2) < 0 &&
          o(B.x1, B.y1, B.x2, B.y2, A.x1, A.y1) * o(B.x1, B.y1, B.x2, B.y2, A.x2, A.y2) < 0) c++;
    }
    return c;
  }
  function columnsLayout() {
    var NL = 6, layers = [], X = [], x = 24, es = layoutEdgeList(), byId = S.idx.node;
    for (var q = 0; q < NL; q++) layers.push([]);
    S.view.nodes.forEach(function (n) { layers[LAYER[n.type] != null ? LAYER[n.type] : NL - 1].push(n.id); });
    layers.forEach(function (L) { var w = 0; L.forEach(function (id) { w = Math.max(w, nodeW(id)); }); X.push(x); x += (w || 160) + GAP_X; });
    layers.forEach(function (L, li) {
      L.sort(function (a, b) { return (S.pos[a] ? S.pos[a].y : 0) - (S.pos[b] ? S.pos[b].y : 0); });
      var y = 24; L.forEach(function (id) { S.pos[id] = { x: X[li], y: y }; y += nodeH(id) + GAP_Y; });
    });
    es = layoutEdgeList();
    function layer(id) { var n = byId[id]; return n ? LAYER[n.type] : NL - 1; }
    function place(L, want) {
      var off = [], acc = 0, blocks = [];
      L.forEach(function (id) { off.push(acc); acc += nodeH(id) + GAP_Y; });
      L.forEach(function (id, i) {
        blocks.push({ sum: want[id] - off[i], cnt: 1 });
        while (blocks.length > 1) { var a = blocks[blocks.length - 2], b = blocks[blocks.length - 1]; if (a.sum / a.cnt <= b.sum / b.cnt) break; a.sum += b.sum; a.cnt += b.cnt; blocks.pop(); }
      });
      var i = 0;
      blocks.forEach(function (b) { var v = b.sum / b.cnt; for (var k = 0; k < b.cnt; k++, i++) S.pos[L[i]].y = v + off[i]; });
    }
    function wantLeft(id, li) {
      var ins = es.filter(function (e) { return e.t === id && layer(e.s) < li; });
      return ins.length ? ins.reduce(function (s, e) { return s + S.pos[e.s].y + portOff(e.s, 'out', e.sh); }, 0) / ins.length - nodeH(id) / 2 : null;
    }
    function wantRight(id, li) {
      var outs = es.filter(function (e) { return e.s === id && layer(e.t) > li; });
      return outs.length ? outs.reduce(function (s, e) { return s + S.pos[e.t].y + nodeH(e.t) / 2 - portOff(id, 'out', e.sh); }, 0) / outs.length : null;
    }
    function sweep(li, dir, keep) {
      var L = layers[li], want = {}; if (!L.length) return;
      L.forEach(function (id) { var w = dir === 'r' ? wantLeft(id, li) : wantRight(id, li); want[id] = w == null ? S.pos[id].y : w; });
      if (!keep) L.sort(function (a, b) { return want[a] - want[b]; });
      place(L, want);
    }
    function restack(L, li) {
      var want = {};
      L.forEach(function (id) { var a = wantLeft(id, li), b = wantRight(id, li); want[id] = a != null && b != null ? (a + b) / 2 : a != null ? a : b != null ? b : S.pos[id].y; });
      place(L, want);
    }
    function sweeps(n, keep) { for (var it = 0; it < n; it++) { for (var a = 1; a < NL; a++) sweep(a, 'r', keep); for (var b = NL - 2; b >= 0; b--) sweep(b, 'l', keep); } for (var c = 1; c < NL; c++) sweep(c, 'r', keep); }
    sweeps(6, false);
    // строки устройств внутри пользователя: меняем соседние, если пересечений становится меньше
    S.idx.users.forEach(function (u) {
      var m = S.ports[u.id], ord = S.order[u.id] || []; if (!m || ord.length < 2) return;
      var slots = ord.map(function (did) { var p = m.p['out:dv:' + did]; return p ? p.y : 0; });
      function put() { ord.forEach(function (did, i) { var k = 'out:dv:' + did; if (m.p[k]) m.p[k] = { x: m.p[k].x, y: slots[i] }; }); }
      for (var guard = 0, improved = true; improved && guard < 6; guard++) {
        improved = false;
        for (var i = 0; i < ord.length - 1; i++) {
          var before = crossings(es), tmp = ord[i]; ord[i] = ord[i + 1]; ord[i + 1] = tmp; put();
          if (crossings(es) < before) improved = true; else { tmp = ord[i]; ord[i] = ord[i + 1]; ord[i + 1] = tmp; put(); }
        }
      }
    });
    // соседние блоки в колонках
    [1, 3, 4, 5, 0, 2, 1].forEach(function (li) {
      var L = layers[li];
      for (var guard = 0, improved = true; improved && guard < 6; guard++) {
        improved = false;
        for (var i = 0; i < L.length - 1; i++) {
          var before = crossings(es), tmp = L[i]; L[i] = L[i + 1]; L[i + 1] = tmp; restack(L, li);
          if (crossings(es) < before) improved = true; else { tmp = L[i]; L[i] = L[i + 1]; L[i + 1] = tmp; restack(L, li); }
        }
      }
    });
    sweeps(3, true);
    var minY = Infinity;
    Object.keys(S.pos).forEach(function (id) { minY = Math.min(minY, S.pos[id].y); });
    Object.keys(S.pos).forEach(function (id) { S.pos[id].y = Math.round((S.pos[id].y - minY + 24) / 2) * 2; });
  }
  function autoLayout() {
    if (S.mode !== 'canvas') setMode('canvas');
    if (!S.view || !S.view.nodes.length) return;
    var before = {}, orders = {};
    Object.keys(S.pos).forEach(function (id) { before[id] = { x: S.pos[id].x, y: S.pos[id].y }; });
    Object.keys(S.order).forEach(function (u) { orders[u] = S.order[u].slice(); });
    columnsLayout();
    S.hist.push({ kind: 'layout', before: before, orders: orders }); if (S.hist.length > HIST_MAX) S.hist.shift();
    reorderRows();                                       // те же элементы в новом порядке: переход left/top виден
    var anim = !reducedMotion();
    if (anim) S.dom.nodes.classList.add('is-anim');
    applyPositions();
    Object.keys(S.pos).forEach(function (id) { S.dirtyPos[id] = 1; });
    S.dirtyOrders = {}; Object.keys(S.order).forEach(function (u) { S.dirtyOrders[u] = S.order[u].slice(); });
    if (anim) {
      // линии следуют за блоками 0,4 с (переход left/top), потом — вписать
      var t0 = performance.now();
      (function loop(now) {
        if (!S) return;
        layoutEdgesAnimated();
        if (now - t0 < 400) requestAnimationFrame(loop); else { S.dom.nodes.classList.remove('is-anim'); renderEdges(); fit(); }
      })(t0);
    } else { renderEdges(); fit(); }
    later('save', 200, saveLayout);
    toast(t('graph.layout.done'), true);
    updateToolbar();
  }
  /* Во время перехода left/top блоки ещё в пути: берём их текущее место из DOM. */
  function layoutEdgesAnimated() {
    var saved = {};
    Object.keys(S.nodeEl).forEach(function (id) { var el = S.nodeEl[id]; saved[id] = S.pos[id]; S.pos[id] = { x: el.offsetLeft, y: el.offsetTop }; });
    layoutEdges();
    Object.keys(saved).forEach(function (id) { S.pos[id] = saved[id]; });
  }

  window.KaskadGraph = { mount: mount, unmount: unmount, show: show };
})();
