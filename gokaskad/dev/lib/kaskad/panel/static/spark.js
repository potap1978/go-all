/* Kaskad — spark.js: «кардиограмма» активности за сутки и окно «График» (UI-B 23.10; Claude Design People Activity r1,
   HANDOFF «Активность — кардиограмма за сутки», SPEC-activity §6.1). Переносимый блок (k3/handoff/process/BLOCKS.md):
   без зависимостей от app.js, тексты и числа приходят параметрами (готовых фраз здесь нет), стили — charts.css
   (gx-spark*, gx-gr*, gx-sptip, gx-actd*). Без атрибута style (CSP style-src 'self'): размеры SVG — атрибутами,
   место подсказки — через CSSOM (style.left).

   Линия строки (KaskadSpark.make / draw): один <svg> на ячейку, три элемента — path.gx-spark__nd (пунктир «нет данных»),
   path.gx-spark__l (линия), circle.gx-spark__dot («сейчас»).
     1. viewBox — пиксели ячейки (0 0 W H, preserveAspectRatio=none): линия 1,5 px не растягивается. Размер берётся из
        вёрстки (--spark-w / --spark-h), поэтому рисуем, когда ячейка видна (fit / watch), и заново — при смене размера.
     2. 48 окон по 30 мин; окно i — точка в центре окна: x = (i + 0,5)·sx, sx = W / (47 + f), f — доля последнего
        (неполного) окна. Отрезок начинается у левого края первого окна и кончается в x = W («сейчас»).
     3. Высота — объём окна (КиБ rx + tx), логарифм, одна шкала на весь список: t = clamp(ln(kb/lo) / ln(hi/lo), 0, 1)^1,7,
        y = base − (base − top)·t, base = H − 0,75, top = 1. Люди: lo = 20 КиБ, hi = 500 МиБ; выходы: 2 МиБ и 5 ГиБ.
     4. Нет трафика, не в сети, выключено — линия на нуле; нет данных (окно без раунда сборщика) — разрыв, по нулю
        пунктир с зазором 1,5 px от концов линии.
     5. Сглаживание — монотонная кубическая (Fritsch–Carlson): без выбросов ниже нуля и выше пика.
     6. Цвет один на линию: был на связи за сутки — gx-spark__l, не был — gx-spark__l--mute. Точка «сейчас» — r = 2.
   Окно «График» (KaskadSpark.graph): та же линия без заливки на сетке (KaskadSpark.grid, Claude Design «Сетка на
   графиках r1» 30.10): шкала 1-2-5 с нулём, единица над осью, вертикальные линии — отметки времени; точное значение —
   в подсказке точки; разрывы — пунктиром, до начала истории линии нет (подпись на пустом месте). */
(function (g) {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var NW = 48;
  var SCALE = { people: [20, 512000], exits: [2048, 5242880] };
  var r2 = function (v) { return Math.round(v * 100) / 100; };
  function svg(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) { n.setAttribute(k, String(attrs[k])); } });
    return n;
  }
  function mk(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = String(text); }
    return n;
  }
  // монотонная кубическая (Fritsch–Carlson) по точкам P = [[x, y], …]: гладко, без выбросов ниже нуля и выше пика
  function mono(P) {
    var n = P.length, i;
    if (!n) { return ""; }
    if (n === 1) { return "M" + r2(P[0][0]) + " " + r2(P[0][1]) + "h.01"; }
    var m = [], t = [];
    for (i = 0; i < n - 1; i++) { m[i] = (P[i + 1][1] - P[i][1]) / ((P[i + 1][0] - P[i][0]) || 1e-6); }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (i = 1; i < n - 1; i++) { t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2; }
    for (i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      var a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
      if (h > 9) { var k = 3 / Math.sqrt(h); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
    var d = "M" + r2(P[0][0]) + " " + r2(P[0][1]);
    for (i = 0; i < n - 1; i++) {
      var dx = (P[i + 1][0] - P[i][0]) / 3;
      d += "C" + r2(P[i][0] + dx) + " " + r2(P[i][1] + t[i] * dx) + " " + r2(P[i + 1][0] - dx) + " " + r2(P[i + 1][1] - t[i + 1] * dx) + " " + r2(P[i + 1][0]) + " " + r2(P[i + 1][1]);
    }
    return d;
  }
  // путь линии строки: o {kb: [48] КиБ за окно, nd: [48] true — нет данных, frac — доля последнего окна (0–1],
  // scale: [lo, hi] КиБ}; → {l, nd, cy, sx}
  function line(o, W, H) {
    var sc = o.scale || SCALE.people, L = Math.log(sc[1] / sc[0]);
    var f = Math.min(1, Math.max(0.05, o.frac === undefined ? 1 : +o.frac || 0));
    var sx = W / (NW - 1 + f), base = H - 0.75, top = 1;
    var y = function (kb) { return !(kb > sc[0]) ? base : base - (base - top) * Math.pow(Math.min(1, Math.log(kb / sc[0]) / L), 1.7); };
    var isNd = function (i) { return !o.nd || !!o.nd[i]; };
    var cx = function (i) { return i === NW - 1 ? (NW - 1 + f / 2) * sx : (i + 0.5) * sx; };
    var pL = "", pNd = "", seg = [], ndFrom = -1, last = base, i;
    var flush = function () { if (seg.length) { pL += mono(seg); seg = []; } };
    for (i = 0; i < NW; i++) {
      if (isNd(i)) {
        if (ndFrom < 0) { if (seg.length) { seg.push([i * sx, last]); } flush(); ndFrom = i; }
        continue;
      }
      if (ndFrom >= 0) { pNd += "M" + r2(ndFrom * sx + 1.5) + " " + r2(base) + "H" + r2(Math.max(ndFrom * sx + 1.5, i * sx - 1.5)); ndFrom = -1; }
      var yy = y(o.kb ? +o.kb[i] || 0 : 0);
      if (!seg.length) { seg.push([i * sx, yy]); }
      seg.push([cx(i), yy]);
      last = yy;
    }
    if (ndFrom >= 0) { pNd += "M" + r2(ndFrom * sx + 1.5) + " " + r2(base) + "H" + r2(W); }
    if (seg.length) { seg.push([W, last]); }
    flush();
    return { l: pL, nd: pNd, cy: r2(isNd(NW - 1) ? base : last), sx: sx };
  }

  // окно строки под курсором: одна черта и одна подсказка на всю страницу
  var tip = null, hx = null;
  function hideTip() { if (tip) { tip.remove(); } if (hx) { hx.remove(); } }
  function showTip(span, x, lines, right) {
    if (!tip) { tip = mk("span", "gx-sptip"); tip.setAttribute("aria-hidden", "true"); hx = mk("span", "gx-spark__hx"); hx.setAttribute("aria-hidden", "true"); }
    tip.replaceChildren();
    lines.forEach(function (s, k) { if (s) { tip.appendChild(mk(k ? "span" : "b", null, s)); } });
    tip.classList.toggle("gx-sptip--r", !!right);
    // место — через CSSOM (left), не атрибутом style: CSP style-src 'self' запрещает атрибут, но не CSSOM
    tip.style.left = r2(x) + "px";
    hx.style.left = r2(x) + "px";
    if (tip.parentNode !== span) { span.appendChild(hx); span.appendChild(tip); }
  }

  // ── Сетка графиков (Claude Design «Сетка на графиках r1» 30.10, HANDOFF «grid»; стили — grid.css): одна шкала 1-2-5 и
  // одна разметка gx-grid на все линейные графики панели — окно «График» (graph), графики выхода (charts.js), «Трафик
  // сервера» (pages/ovb.js), скорость из браузера (pages/speed.js); «Здоровье сервера» — только верх (scale с one).
  // Чистые части (scale, bytes, labels, yw, ticks, lab, fit) — тест в node: tests/panel/test_grid.mjs.
  var STEPS = [];
  (function () { var e, j; for (e = -3; e <= 12; e++) { for (j = 0; j < 3; j++) { STEPS.push(+([1, 2, 5][j] * Math.pow(10, e)).toPrecision(1)); } } })();
  // K — сколько делений помещается: поле 150 px → 3 (до 4 линий с нулём), 120 → 2
  var kOf = function (h) { return h >= 150 ? 3 : 2; };
  // шаг s — самый мелкий из 1·2·5 × 10ⁿ, при котором n = ⌈max / s⌉ ≤ K; верх = n·s ≥ max (линия не выше верхней);
  // n = 1 — шаг на ступень мельче (на 120 px бывает 4 линии: 4,5 → 0 · 2 · 4 · 6). o.min — шаг не мельче («Устройств» —
  // целые); o.one — только верх, без «мельче» («Здоровье сервера»: подпись верха и «0»)
  function scale(max, K, o) {
    var mn = (o && o.min) || 0, one = !!(o && o.one), k = Math.max(1, K || 3), j, s, n;
    if (!(max > 0) || !isFinite(max)) { s = mn > 0 ? mn : 1; return { step: s, n: 1, top: s }; }
    for (j = 0; j < STEPS.length; j++) {
      s = STEPS[j];
      n = Math.ceil(max / s - 1e-9);
      if (s < mn || n > k) { continue; }
      if (n === 1 && !one && j > 0 && STEPS[j - 1] >= mn) { s = STEPS[j - 1]; n = Math.ceil(max / s - 1e-9); }
      return { step: s, n: n, top: +(n * s).toPrecision(12) };
    }
    return { step: max, n: 1, top: max };
  }
  // объём: одна единица на ось — по максимуму (≥ 1 ГБ → ГБ, ≥ 1 МБ → МБ, иначе КБ; основание 1024, как fmtBytes); верх
  // в этой единице ≥ 1000 — следующая единица и пересчёт. → {k: 0 КБ | 1 МБ | 2 ГБ, f — байт в единице, sc — в единицах}
  function bytes(max, K) {
    var b = Math.max(0, +max || 0), k = b >= 1073741824 ? 2 : b >= 1048576 ? 1 : 0, f, sc;
    for (;;) {
      f = Math.pow(1024, k + 1);
      sc = scale(b / f, K);
      if (sc.top >= 1000 && k < 2) { k += 1; continue; }
      return { k: k, f: f, sc: sc };
    }
  }
  // числа делений снизу вверх, без лишних нулей («0 · 0,5 · 1»); sep — десятичный знак языка (RU «,», EN «.»)
  function labels(sc, sep) {
    var dec = sc.step >= 1 ? 0 : Math.ceil(-Math.log10(sc.step) - 1e-9), out = [], j;
    for (j = 0; j <= sc.n; j++) { out.push(String(+(j * sc.step).toFixed(dec)).replace(".", sep || ".")); }
    return out;
  }
  // ширина знака подписи: моно max(.6875rem, 11px), знак — 0,6 em (11 px → 6,6; шаг «крупнее» — шире)
  function cw() {
    var px = 16;
    try { px = parseFloat(g.getComputedStyle(g.document.documentElement).fontSize) || 16; } catch (e) { px = 16; }
    return Math.max(11, 0.6875 * px) * 0.6;
  }
  // колонка чисел: самая длинная подпись × ширина знака, не меньше 14, вверх до целого (px)
  function yw(ys, w) {
    var m = 0;
    (ys || []).forEach(function (s) { m = Math.max(m, String(s).length); });
    return Math.max(14, Math.ceil(m * (w || cw()) - 1e-9));
  }
  // отметки времени по часам браузера внутри [t0, t1): kind "h" — каждые hours ч (сутки: 4, телефон 6), "d" — полночь
  // каждого дня, "w" — понедельник 00:00, "m" — 1-е число месяца 00:00 (90 дней выхода, Claude Design r1b №2) →
  // [{ts, at — доля 0–1, d — Date}]. На круглые часы, а не на доли периода
  function ticks(t0, t1, kind, hours) {
    var out = [], h = hours || 4, d, ms, end = t1 * 1000;
    if (!(t1 > t0)) { return out; }
    d = new Date(t0 * 1000);
    d.setMinutes(0, 0, 0);
    ms = d.getTime() < t0 * 1000 ? d.getTime() + 3600000 : d.getTime();
    for (; ms < end; ms += 3600000) {
      d = new Date(ms);
      if (d.getMinutes() !== 0) { continue; }                          // смена пояса на полчаса — не круглый час
      var hr = d.getHours();
      if (kind === "h" ? hr % h === 0 : hr === 0 && (kind === "d" || (kind === "m" ? d.getDate() === 1 : d.getDay() === 1))) { out.push({ ts: ms / 1000, at: (ms / 1000 - t0) / (t1 - t0), d: d }); }
    }
    return out;
  }
  // вид отметок по длине оси: kinds — вид или список от крупного к мелкому; берётся первый, при котором на оси [t0, t1)
  // ≥ 2 отметок, иначе последний (90 дней выхода: ось от первой точки ряда, у молодого выхода 1-х чисел 0–1 — REV-33714 Б-1)
  function pick(t0, t1, kinds, hours) {
    var ks = [].concat(kinds);
    for (var i = 0; i < ks.length - 1; i++) { if (ticks(t0, t1, ks[i], hours).length >= 2) { return ks[i]; } }
    return ks[ks.length - 1];
  }
  // подпись отметки: "h" — «20:00»; "d" — RU «сб 24», EN «Sat 24», на телефоне (narrow) — день недели без числа;
  // "w" и "m" — RU «26 окт.», «1 окт.», EN «Oct 26», «Oct 1»
  var FMT = {};
  function fmtOf(lang, k, o) {
    var key = (lang === "en" ? "en" : "ru") + k;
    if (!(key in FMT)) { try { FMT[key] = new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ru-RU", o); } catch (e) { FMT[key] = null; } }
    return FMT[key];
  }
  var p2 = function (v) { return (v < 10 ? "0" : "") + v; };
  function lab(kind, d, lang, narrow) {
    if (kind === "h") { return p2(d.getHours()) + ":" + p2(d.getMinutes()); }
    if (kind === "d") {
      var f = fmtOf(lang, "w", { weekday: "short" }), w = f ? f.format(d) : "";
      return narrow ? w : w + " " + d.getDate();
    }
    var m = fmtOf(lang, "m", { day: "numeric", month: "short" });
    return m ? m.format(d) : d.getDate() + "." + (d.getMonth() + 1);
  }
  // подписи времени: по центру своей линии; у левого края не помещается — от линии вправо (is-start); налезает на
  // соседнюю (зазор < 8 px), на «сейчас» или за край — прячется (is-hid: visibility, место не сдвигается), линия
  // остаётся. xs — места подписей (px), ws — ширины подписей, W — ширина поля, we — ширина «сейчас» (0 — нет), keep —
  // не прятать (фазы замера скорости: у левого края — от края поля, налезающая — правее соседней, как прежние колонки
  // фаз; Claude Design «Подтверждение переноса», ответ 5) → классы; lefts — левый край подписи is-start, px
  function fit(xs, ws, W, we, keep, lefts) {
    var out = [], prevR = -1e9;
    xs.forEach(function (px, i) {
      var w = ws[i] || 0, st = px - w / 2 < 0, L = st ? (keep ? 0 : Math.max(0, px)) : px - w / 2;
      if (keep && L < prevR + 8) { L = prevR + 8; st = true; }
      var R = L + w, hid = !keep && (L < prevR + 8 || (we > 0 && R > W - we - 8) || R > W);
      if (!hid) { prevR = R; }
      out.push(hid ? "is-hid" : st ? "is-start" : "");
      if (lefts) { lefts.push(L); }
    });
    return out;
  }
  // Разметка (HANDOFF «Строение»): div.gx-gr.gx-grid[.gx-grid--sm] — единица над колонкой чисел (gx-grid__u), числа
  // делений (gx-grid__y), поле (gx-gr__plot: svg — вертикальные → [under] → горизонтальные → [paint] → точка курсора),
  // подписи времени (gx-grid__x). viewBox — пиксели поля: перерисовка при смене размера. Атрибута style нет —
  // --grid-yw, top и left подписей, курсор — через CSSOM (CSP style-src 'self').
  // o {sm — поле 120 (иначе 150; своя высота — --grid-h в CSS), unit — единица (нет — без строки), ys — подписи
  //   делений снизу вверх, sc {step, n, top} — шкала в единицах данных, yw — ширина колонки, px (нет — по подписям),
  //   ax [{at 0–1, t, cls}] — отметки (линия и подпись), lines [at] — свои линии вместо линий отметок (границы фаз
  //   замера, подписи — по центру фаз), end — подпись правого края, keep — подписи времени не прятать,
  //   under(svg, W, H, y, mk) / paint(svg, W, H, y, mk) — слои данных под горизонтальными линиями и над ними,
  //   hover(x, W, H, y) → {x, y | null, lines, acc} | null — черта, точка на линии и подсказка, cls — ещё классы}
  // → div; div.__grid: {draw(force), set(o2) — новые данные (замер идёт), plot}
  function box(o) {
    var root = mk("div", "gx-gr gx-grid" + (o.sm ? " gx-grid--sm" : "") + (o.cls ? " " + o.cls : ""));
    var u = mk("span", "gx-grid__u"), yc = mk("div", "gx-grid__y"), plot = mk("div", "gx-gr__plot"), xc = mk("div", "gx-grid__x");
    yc.setAttribute("aria-hidden", "true");
    xc.setAttribute("aria-hidden", "true");
    root.appendChild(yc); root.appendChild(plot); root.appendChild(xc);
    var st = { W: 0, H: 0, y: null, svg: null, tip: null, hx: null, dot: null };
    var nom = function (H) { return function (v) { return 1.5 + (1 - Math.max(0, +v || 0) / o.sc.top) * (H - 3); }; };
    function head() {
      u.textContent = o.unit || "";
      if (o.unit && u.parentNode !== root) { root.insertBefore(u, root.firstChild); }
      if (!o.unit && u.parentNode) { u.remove(); }
      yc.replaceChildren();
      var y0 = nom(o.sm ? 120 : 150);                                  // до первого замера — по высоте из макета
      (o.ys || []).forEach(function (s, j) { var x = mk("span", null, s); x.style.top = r2(y0(j * o.sc.step)) + "px"; yc.appendChild(x); });
      root.style.setProperty("--grid-yw", (o.yw || yw(o.ys)) + "px");
      xc.replaceChildren();
      (o.ax || []).forEach(function (a) { var x = mk("span", a.cls || null, a.t); x.style.left = r2(a.at * 100) + "%"; xc.appendChild(x); });
      if (o.end) { var e = mk("span", "is-end", o.end); e.style.left = "100%"; xc.appendChild(e); }
    }
    function fitX(W) {
      var ax = o.ax || [], sp = Array.prototype.slice.call(xc.children, 0, ax.length);
      if (!sp.length) { return; }
      sp.forEach(function (x, i) { x.className = ax[i].cls || ""; x.style.left = r2(ax[i].at * 100) + "%"; });
      var we = o.end && xc.lastChild ? xc.lastChild.offsetWidth : 0, lefts = [];
      var cl = fit(ax.map(function (a) { return a.at * W; }), sp.map(function (x) { return x.offsetWidth; }), W, we, !!o.keep, lefts);
      sp.forEach(function (x, i) {
        x.className = [ax[i].cls, cl[i]].filter(Boolean).join(" ");
        if (cl[i] === "is-start") { x.style.left = r2(lefts[i]) + "px"; }
        // спрятанная у правого края не должна раздвигать ось (её коробка за краем поля — прокрутка вбок): к началу оси
        if (cl[i] === "is-hid") { x.classList.add("is-start"); x.style.left = "0px"; }
      });
    }
    function hide() {
      if (st.tip) { st.tip.remove(); st.hx.remove(); }
      if (st.dot) { st.dot.forEach(function (x) { x.remove(); }); st.dot = null; }
    }
    function draw(force) {
      if (!root.isConnected) { return; }
      var b = plot.getBoundingClientRect(), W = Math.round(b.width), H = Math.round(b.height);
      if (!W || !H || (!force && W === st.W && H === st.H)) { return; }
      st.W = W; st.H = H;
      var y = nom(H), sc = o.sc, ph = "", pv = "", j;
      Array.prototype.forEach.call(yc.children, function (x, k) { x.style.top = r2(y(k * sc.step)) + "px"; });
      for (j = 0; j <= sc.n; j++) { ph += "M0 " + (Math.round(y(j * sc.step) - 0.5) + 0.5) + "H" + W; }
      (o.lines || (o.ax || []).map(function (a) { return a.at; })).forEach(function (at) { pv += "M" + (Math.round(at * W - 0.5) + 0.5) + " 0V" + H; });
      var v = svg("svg", { class: "gx-gr__svg", viewBox: "0 0 " + W + " " + H, width: W, height: H, preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" });
      if (pv) { v.appendChild(svg("path", { class: "gx-grid__v", d: pv })); }
      if (o.under) { o.under(v, W, H, y, svg); }
      v.appendChild(svg("path", { class: "gx-grid__h", d: ph }));
      if (o.paint) { o.paint(v, W, H, y, svg); }
      hide();
      if (st.svg) { st.svg.remove(); }
      plot.insertBefore(v, plot.firstChild);
      st.svg = v; st.y = y;
      fitX(W);
    }
    function show(ev) {
      var b = plot.getBoundingClientRect();
      if (!b.width || !st.y) { return; }
      var h = o.hover(ev.clientX - b.left, st.W, st.H, st.y);
      if (!h) { hide(); return; }
      if (!st.tip) { st.tip = mk("span", "gx-sptip"); st.tip.setAttribute("aria-hidden", "true"); st.hx = mk("span", "gx-gr__hx"); st.hx.setAttribute("aria-hidden", "true"); }
      st.tip.replaceChildren();
      (h.lines || []).forEach(function (s, k) { if (s) { st.tip.appendChild(mk(k ? "span" : "b", null, s)); } });
      st.tip.className = "gx-sptip " + (h.x > st.W / 2 ? "gx-sptip--r" : "gx-sptip--l");
      st.tip.style.left = r2(h.x) + "px";
      st.hx.style.left = r2(h.x) + "px";
      if (st.tip.parentNode !== plot) { plot.appendChild(st.hx); plot.appendChild(st.tip); }
      if (st.dot) { st.dot.forEach(function (x) { x.remove(); }); st.dot = null; }
      if (h.y !== null && h.y !== undefined) {
        var d = "M" + r2(h.x) + " " + r2(h.y) + "h0";               // кольцо цвета поверхности + точка
        st.dot = [svg("path", { class: "gx-grid__ring", d: d }), svg("path", { class: "gx-grid__pt" + (h.acc ? " gx-grid__pt--acc" : ""), d: d })];
        st.dot.forEach(function (x) { st.svg.appendChild(x); });
      }
    }
    head();
    if (o.hover) {
      plot.addEventListener("mousemove", show);
      plot.addEventListener("pointerdown", show);                     // касание на телефоне
      plot.addEventListener("mouseleave", hide);
    }
    g.requestAnimationFrame(function () { draw(); });
    // свои шрифты (font-display: swap) приходят после первого кадра: ширины подписей времени — заново
    if (g.document && g.document.fonts && g.document.fonts.ready) { g.document.fonts.ready.then(function () { if (st.W) { fitX(st.W); } }); }
    if (g.ResizeObserver) {
      var ro = new g.ResizeObserver(function () { if (!root.isConnected) { if (st.W) { ro.disconnect(); } return; } draw(); });
      ro.observe(plot);
    }
    root.__grid = { draw: draw, plot: plot, set: function (p) { Object.keys(p || {}).forEach(function (k) { o[k] = p[k]; }); head(); draw(true); } };
    return root;
  }
  var GRID = { K: kOf, scale: scale, bytes: bytes, labels: labels, cw: cw, yw: yw, ticks: ticks, pick: pick, lab: lab, fit: fit, box: box };


  var S = {
    NW: NW,
    SCALE: SCALE,
    mono: mono,
    line: line,
    // Ячейка с линией: o {kb, nd, frac, scale, any — был на связи за сутки, dot — "on" | "warn" | "bad" | "" (серая) |
    // null (без точки), label — подсказка строки (title и aria-label), win(i) → [время, текст] — подсказка окна,
    // open() — нажатие (окно «График»); paint(v, W, H, svg) — своя линия вместо линии активности в готовом <svg> v (UI-C)};
    // cls — ещё классы (размер: gx-spark--lg | --w140 | --row | --sheet | --sm | gx-cl)
    make: function (o, cls) {
      var s = mk("span", "gx-spark" + (cls ? " " + cls : ""));
      s.__sp = o || {};
      if (o && o.label) { s.setAttribute("title", o.label); s.setAttribute("aria-label", o.label); s.setAttribute("role", "img"); }
      if (o && o.open) {
        s.classList.add("is-btn");
        s.addEventListener("click", function (ev) { ev.stopPropagation(); hideTip(); o.open(ev); });
      }
      if (o && o.win) {
        s.addEventListener("mousemove", function (ev) {
          var b = s.getBoundingClientRect(), x = ev.clientX - b.left;
          if (!b.width || !s.__sx) { return; }
          var i = Math.max(0, Math.min(NW - 1, Math.floor(x / s.__sx)));
          var cx = i === NW - 1 ? b.width : (i + 0.5) * s.__sx;
          showTip(s, cx, o.win(i) || [], cx > b.width / 2);
        });
        s.addEventListener("mouseleave", hideTip);
      }
      return s;
    },
    // нарисовать линию по размеру ячейки (без размера — скрыта: пропустить; тот же размер — не перерисовывать)
    draw: function (s) {
      var o = s.__sp;
      if (!o || !s.isConnected || !s.getClientRects().length) { return false; }
      var b = s.getBoundingClientRect(), W = Math.round(b.width * 2) / 2, H = Math.round(b.height * 2) / 2;
      if (!W || !H || (s.__w === W && s.__h === H && s.firstChild)) { return false; }
      s.__w = W; s.__h = H;
      var v = svg("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" });
      if (o.paint) {
        // своя линия (UI-C, «Проверка сервера»: звено за 30 дней к «обычно»): o.paint(v, W, H, svg) кладёт элементы в
        // готовый <svg> того же размера; размер, перерисовка при смене размера и CSP — общие
        s.__sx = 0;
        o.paint(v, W, H, svg);
      } else {
        var p = line(o, W, H);
        s.__sx = p.sx;
        v.appendChild(svg("path", { class: "gx-spark__nd", d: p.nd || "M0 0" }));
        v.appendChild(svg("path", { class: "gx-spark__l" + (o.any ? "" : " gx-spark__l--mute"), d: p.l || "M0 0" }));
        if (o.dot !== null && o.dot !== undefined) { v.appendChild(svg("circle", { class: "gx-spark__dot" + (o.dot ? " gx-spark__dot--" + o.dot : ""), cx: W, cy: p.cy, r: 2 })); }
      }
      Array.prototype.forEach.call(s.querySelectorAll("svg"), function (x) { x.remove(); });
      s.insertBefore(v, s.firstChild);
      return true;
    },
    // все видимые линии внутри root
    fit: function (root) {
      if (!root || !root.querySelectorAll) { return 0; }
      var n = 0;
      Array.prototype.forEach.call(root.querySelectorAll(".gx-spark"), function (s) { if (S.draw(s)) { n += 1; } });
      return n;
    },
    // рисовать сразу и при каждой смене размера root (окно, плотность, раскрытие строки, боковая панель) или линии
    watch: function (root) {
      if (!root || root.__spWatch) { return root; }
      root.__spWatch = true;
      var ro = g.ResizeObserver ? new g.ResizeObserver(function () { if (!root.isConnected) { ro.disconnect(); return; } S.fit(root); }) : null;
      g.requestAnimationFrame(function () {
        S.fit(root);
        // и каждая линия: ширина ячейки меняется без смены размера root (колонка «по содержимому» у соседей)
        if (ro) { Array.prototype.forEach.call(root.querySelectorAll(".gx-spark"), function (s) { ro.observe(s); }); }
      });
      if (ro) { ro.observe(root); }
      return root;
    },
    hideTip: hideTip,
    grid: GRID,
    // полоса доли (устройства в «Графике»): ширина — атрибутом rect (CSP)
    bar: function (frac, w, h) {
      var W = w || 160, H = h || 6, v = svg("svg", { class: "gx-actd__bar", viewBox: "0 0 " + W + " " + H, width: W, height: H, "aria-hidden": "true", focusable: "false" });
      v.appendChild(svg("rect", { class: "gx-actd__tr", width: W, height: H, rx: H / 2 }));
      var x = Math.max(0, Math.min(1, +frac || 0)) * W;
      if (x > 0) { v.appendChild(svg("rect", { class: "gx-actd__v", width: r2(Math.max(H, x)), height: H, rx: H / 2 })); }
      return v;
    },
    // значок линии для легенды
    key: function (kind) {
      var v = svg("svg", { class: "gx-key-l", viewBox: "0 0 18 10", width: 18, height: 10, "aria-hidden": "true", focusable: "false" });
      if (kind === "nd") { v.appendChild(svg("path", { class: "gx-spark__nd", d: "M1 9H17" })); }
      else { v.appendChild(svg("path", { class: "gx-spark__l", d: "M1 9H6L8 2L10 9H17" })); }
      return v;
    },
    // Окно «График» на сетке (grid.box): o {vals: [байт за точку | null — нет данных], pre — точек до начала истории
    // (линии нет), preText — подпись на пустом месте, unit — единица оси («МБ за 5 мин»), sc {step, n, top} — шкала в
    // байтах, ys — подписи делений, ax: [{at: 0–1, t}] — отметки времени, end — подпись правого края («сейчас»),
    // dot — "on" | "" | null, tip(i) → [строки] — подсказка точки, sm — поле 120 (телефон), иначе 150}
    graph: function (o) {
      var n = (o.vals || []).length, pre = Math.max(0, Math.min(n, o.pre || 0)), preBox = null;
      var isNd = function (i) { return o.vals[i] === null || o.vals[i] === undefined; };
      var box = GRID.box({ sm: o.sm, unit: o.unit, ys: o.ys, sc: o.sc, ax: o.ax, end: o.end,
        paint: function (v, W, H, y) {
          var sx = W / Math.max(1, n), base = y(0), pL = "", pNd = "", seg = [], ndFrom = -1, lastY = base, i;
          var flush = function () { if (seg.length) { pL += mono(seg); seg = []; } };
          for (i = pre; i < n; i++) {
            if (isNd(i)) {
              if (ndFrom < 0) { if (seg.length) { seg.push([i * sx, lastY]); } flush(); ndFrom = i; }
              continue;
            }
            if (ndFrom >= 0) { pNd += "M" + r2(ndFrom * sx + 1.5) + " " + r2(base) + "H" + r2(Math.max(ndFrom * sx + 1.5, i * sx - 1.5)); ndFrom = -1; }
            var yy = y(o.vals[i]);
            if (!seg.length) { seg.push([i * sx, yy]); }
            seg.push([(i + 0.5) * sx, yy]);
            lastY = yy;
          }
          if (ndFrom >= 0) { pNd += "M" + r2(ndFrom * sx + 1.5) + " " + r2(base) + "H" + r2(W); }
          if (seg.length) { seg.push([W, lastY]); }
          flush();
          if (pNd) { v.appendChild(svg("path", { class: "gx-gr__nd", d: pNd })); }
          if (pL) { v.appendChild(svg("path", { class: "gx-gr__line", d: pL })); }
          // точка «сейчас» — круглый конец нулевого отрезка (круг при любой ширине поля)
          if (o.dot !== null && o.dot !== undefined && n > pre) {
            v.appendChild(svg("path", { class: "gx-grid__dot" + (o.dot === "on" ? " gx-grid__dot--on" : ""), d: "M" + W + " " + r2(isNd(n - 1) ? base : lastY) + "h0" }));
          }
          if (preBox) { preBox.style.width = r2(pre * sx) + "px"; }       // «История с …» — по пустому месту
        },
        hover: o.tip ? function (x, W, H, y) {
          if (!n) { return null; }
          var i = Math.max(0, Math.min(n - 1, Math.floor(x / (W / n))));
          if (i < pre) { return null; }
          return { x: (i + 0.5) * W / n, y: isNd(i) ? null : y(o.vals[i]), lines: o.tip(i) || [] };
        } : null });
      if (pre > 0 && o.preText) { preBox = mk("span", "gx-gr__pre", o.preText); box.__grid.plot.appendChild(preBox); }
      box.__draw = box.__grid.draw;
      return box;
    }
  };
  g.KaskadSpark = S;
})(window);
