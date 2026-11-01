/* Kaskad 3 — графики выхода (window.KaskadCharts), этап 1 T8, SE §7.3: SVG своими силами, без внешних библиотек.
   Отклик, потери, трафик, устройства — линия; скорость — точки; полоса состояний (годен / ухудшен / в запасе / снят).
   Поле, деления, подписи времени и подсказка — общая сетка KaskadSpark.grid (spark.js, grid.css; Claude Design «Сетка на
   графиках r1» 30.10): шкала 1-2-5 с верхом не ниже пика, числа — колонкой слева (одна ширина на шторку), единица — в
   строке названия ряда, вертикальные линии — отметки времени, правый край — «сейчас». Без атрибута style (CSP панели
   style-src 'self'); цвета — классы charts.css / grid.css на токенах темы. Без зума и легенд-переключателей.

   При загрузке файл только объявляет функции. Чистые части — для теста в node (с spark.js):
     KaskadCharts.scale(points, col)  -> {min, max, ts0, ts1, n} по колонке col (null пропускаются)
     KaskadCharts.ticks(min, max, K)  -> деления по правилу сетки (0 · шаг · … · верх ≥ максимума)
     KaskadCharts.axis(points, col, o) -> {sc, ys, f} ось ряда: o {K — делений (150 px → 3, 120 → 2), bytes — объём
                                       в байтах (одна единица на ось), min — шаг не мельче, sep — десятичный знак}
     KaskadCharts.yw(ys)              -> ширина колонки чисел, px (одна на шторку: подписи всех рядов)
     KaskadCharts.path(points, col, sc, W, y) -> строка d для <path> (разрывы на null)
   Сборка:
     KaskadCharts.chart(o)  o: {title, unit — единица в строке названия, points: [[ts, v…]], col, kind: "line"|"dots",
                               axis — KaskadCharts.axis(…), yw — ширина колонки чисел (одна на шторку), sm — поле 120,
                               time {kind "h"|"d"|"w"|"m" или список от крупного к мелкому — первый с ≥ 2 отметками на оси,
                               hours, lang, narrow} — отметки, end — «сейчас», fmt(v) -> текст,
                               fmtTs(ts) -> текст, aria, emptyText} -> <figure> (пустой ряд — «нет данных»)
     KaskadCharts.band(o)   o: {states: [[from, to, state]], ts0, ts1, label(state) -> текст, aria, yw, width} -> <figure> */
"use strict";

(() => {
  const NS = "http://www.w3.org/2000/svg";
  const W = 600, H = 150;
  const G = () => window.KaskadSpark.grid;

  function svgEl(tag, attrs, kids) {
    const n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach((k) => { if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, String(attrs[k])); });
    (kids || []).forEach((c) => { if (c) n.appendChild(c); });
    return n;
  }
  function htmlEl(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  const has = (v) => v !== null && v !== undefined && !isNaN(v);

  function scale(points, col) {
    let min = Infinity, max = -Infinity, n = 0;
    const ts0 = points.length ? points[0][0] : 0, ts1 = points.length ? points[points.length - 1][0] : 1;
    points.forEach((p) => {
      const v = p[col];
      if (!has(v)) return;
      n += 1; if (v < min) min = v; if (v > max) max = v;
    });
    if (!n) return { min: 0, max: 1, ts0, ts1: ts1 > ts0 ? ts1 : ts0 + 1, n: 0 };
    if (min > 0) min = 0;                                     // ось от нуля: отклик и потери читаются честно
    if (max <= min) max = min + 1;
    return { min, max, ts0, ts1: ts1 > ts0 ? ts1 : ts0 + 1, n };
  }
  // деления по правилу сетки: самый мелкий шаг 1-2-5, при котором до верха ≤ K делений; верх ≥ максимума
  function ticks(min, max, K) {
    const sc = G().scale(max - min, K || 3), out = [];
    for (let j = 0; j <= sc.n; j++) out.push(Math.round((min + j * sc.step) * 1e6) / 1e6);
    return out;
  }
  function axis(points, col, o) {
    const s = scale(points, col), K = (o && o.K) || 3, sep = (o && o.sep) || ".";
    if (o && o.bytes) { const b = G().bytes(s.n ? s.max : 0, K); return { sc: b.sc, ys: G().labels(b.sc, sep), f: b.f, k: b.k }; }
    const sc = G().scale(s.max, K, { min: o && o.min });
    return { sc, ys: G().labels(sc, sep), f: 1, k: -1 };
  }
  // одна колонка чисел на шторку: ширина по самой длинной подписи всех рядов (px)
  const yw = (ys) => G().yw(ys);
  const xOf = (ts, sc, w) => ((ts - sc.ts0) / (sc.ts1 - sc.ts0)) * (w || W);
  const yDef = (sc) => (v) => 1.5 + (1 - (v - sc.min) / (sc.max - sc.min)) * (H - 3);
  // путь линии в поле w × высота (y — место значения; без y — шкала ряда на 150 px), разрывы — на пропусках
  function path(points, col, sc, w, y) {
    const Y = y || yDef(sc);
    let d = "", up = false;
    points.forEach((p) => {
      const v = p[col];
      if (!has(v)) { up = false; return; }
      d += (up ? "L" : "M") + xOf(p[0], sc, w).toFixed(1) + " " + Y(v).toFixed(1);
      up = true;
    });
    return d;
  }
  // ближайшая к x (px поля шириной w) точка с данными
  function nearest(points, col, sc, x, w) {
    let best = null, bd = Infinity;
    points.forEach((p) => {
      if (!has(p[col])) return;
      const d = Math.abs(xOf(p[0], sc, w) - x);
      if (d < bd) { bd = d; best = p; }
    });
    return best;
  }

  function chart(o) {
    const fig = htmlEl("figure", "kc");
    const cap = htmlEl("figcaption", "kc__t", o.title || "");
    if (o.unit) cap.appendChild(htmlEl("span", "gx-grid__u", o.unit));     // единица — в строке названия ряда
    fig.appendChild(cap);
    const pts = Array.isArray(o.points) ? o.points : [];
    const col = o.col || 1;
    const sc = scale(pts, col);
    if (!sc.n) { fig.appendChild(htmlEl("div", "kc__empty", o.emptyText || "")); return fig; }
    const A = o.axis || axis(pts, col, { K: o.sm ? 2 : 3 }), f = A.f || 1;
    const fmt = o.fmt || ((v) => String(v)), fmtTs = o.fmtTs || ((ts) => String(ts));
    const tm = o.time || {}, kind = G().pick(sc.ts0, sc.ts1, tm.kind || "h", tm.hours);
    const ax = G().ticks(sc.ts0, sc.ts1, kind, tm.hours).map((x) => ({ at: x.at, t: G().lab(kind, x.d, tm.lang, tm.narrow) }));
    const box = G().box({ sm: o.sm, ys: A.ys, sc: A.sc, yw: o.yw, ax, end: o.end,
      paint: (v, w, h, y) => {
        const Y = (x) => y(x / f);
        if (o.kind === "dots") {
          let d = "";
          pts.forEach((p) => { if (has(p[col])) d += "M" + xOf(p[0], sc, w).toFixed(1) + " " + Y(p[col]).toFixed(1) + "h0"; });
          if (d) v.appendChild(svgEl("path", { class: "gx-grid__dot gx-grid__dot--acc", d }));
        } else {
          const d = path(pts, col, sc, w, Y);
          if (d) v.appendChild(svgEl("path", { class: "kc__line", d }));
        }
      },
      // подсказка: черта и точка на линии (gx-grid__pt--acc), текст «08:40 · 330 мс»
      hover: (x, w, h, y) => {
        const p = nearest(pts, col, sc, x, w);
        return p ? { x: xOf(p[0], sc, w), y: y(p[col] / f), lines: ["", fmtTs(p[0]) + " · " + fmt(p[col])], acc: true } : null;
      } });
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", o.aria || o.title || "");
    fig.appendChild(box);
    return fig;
  }

  // полоса состояний — в колонке сетки (gx-grid--band): начинается под полем рядов, легенда — с отступом колонки
  function band(o) {
    const fig = htmlEl("figure", "kc kc--band");
    fig.appendChild(htmlEl("figcaption", "kc__t", o.title || ""));
    const st = Array.isArray(o.states) ? o.states : [];
    const yw = o.yw || 16, w = Math.max(80, (typeof o.width === "number" && isFinite(o.width) && o.width > 0 ? Math.round(o.width) : W) - yw - 8);
    const sc = { ts0: o.ts0, ts1: o.ts1 > o.ts0 ? o.ts1 : o.ts0 + 1 };
    const kids = st.map((s) => {
      const x0 = Math.max(0, xOf(s[0], sc, w)), x1 = Math.min(w, xOf(s[1], sc, w));
      if (x1 <= x0) return null;
      const r = svgEl("rect", { class: "kc__st kc__st--" + String(s[2] || "unknown").replace(/[^a-z]/g, ""), x: x0.toFixed(1), y: 0, width: (x1 - x0).toFixed(1), height: 16 });
      if (o.label) r.appendChild(svgEl("title", {}, [document.createTextNode(o.label(s[2]))]));
      return r;
    });
    const row = htmlEl("div", "gx-grid gx-grid--band");
    row.style.setProperty("--grid-yw", yw + "px");
    row.append(htmlEl("span", "gx-grid__y"), svgEl("svg", { class: "kc__svg kc__svg--band", viewBox: "0 0 " + w + " 16", preserveAspectRatio: "none", role: "img", "aria-label": o.aria || o.title || "", focusable: "false" }, kids));
    fig.appendChild(row);
    // подписи состояний, что встречаются в ряду, — словами (цвет не единственный носитель смысла)
    const seen = [];
    st.forEach((s) => { if (seen.indexOf(s[2]) < 0) seen.push(s[2]); });
    if (o.label && seen.length) {
      const leg = htmlEl("div", "kc__leg");
      seen.forEach((k) => { const it = htmlEl("span", "kc__legi kc__legi--" + String(k).replace(/[^a-z]/g, ""), o.label(k)); leg.appendChild(it); });
      fig.appendChild(leg);
    }
    return fig;
  }

  window.KaskadCharts = { scale, ticks, axis, yw, path, nearest, chart, band, W, H };
})();
