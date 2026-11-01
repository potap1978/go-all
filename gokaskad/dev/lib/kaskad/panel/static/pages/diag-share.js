/* Kaskad 3 — «Поделиться» проверкой сервера: кадр ролика, PNG и GIF в браузере (C10; k3/handoff/SPEC-node-score-schedule-share.md
   §5.3 и «Сверка 24.10», п. 2–4; макет Claude Design «Проверка сервера r1», check-share.js, lk-sched.js). Грузит
   pages/diag.js при первом нажатии «Поделиться» (или по адресу #overview/now?open=share из бота); регистрирует
   window.KaskadShare: кадр и кодировщики (чистые, тесты в node: tests/test_panel_share_gif.py) и окно open(K, opt).

   Кадр — список фигур (rect, text, clip) по времени t (0…15 с; владелец 24.10: движение с прежней скоростью, готовые
   результаты стоят дольше — моменты в AT): 0–1,5 заставка «Мой сервер · Kaskad Pro» без движения · 1,5–2,6 перекат
   сырых цифр звеньев, стоят до 5,0 · 5,0–5,9 «в целом N от обычного», стоит до 8,5 · 8,5–9,7 полоса 30 дней по дню ·
   9,7–15 удержание (он же PNG и превью). Строки кадра готовит clipOf из ответа GET /api/host/share (слова и числа —
   на языке ролика), frame — только геометрия. Рисует одна функция draw (превью, PNG и кадры GIF — один и тот же холст), цвета — токены
   темы ролика через пробный элемент (тема ролика не зависит от темы панели).

   GIF89a — свой кодировщик (макет: «кодировщик — прежний»): одна палитра на ролик (до 256 цветов по частоте из трёх
   опорных кадров, корзины 5 бит на канал), LZW 9–12 бит (классическая таблица на 5003, как в GIFEncoder), каждый
   кадр — прямоугольник изменившихся точек поверх прошлого («не стирать»), одинаковые кадры склеиваются, задержки в
   сотых секунды — сумма всегда 1500 (15 с); повтор бесконечно. Внешних адресов, eval, сети и воркеров нет; атрибута
   style нет (CSP) — пробный элемент прячет класс gx-vh. Без шаблонных строк. */
"use strict";

(() => {
  const DUR = 15;                                                 // с, ролик целиком
  const AT = Object.freeze({ intro: 1.5, total: 5.0, days: 8.5 }); // с: конец заставки, начало «в целом», начало полосы
  const SIZES = { sq: [1080, 1080], post: [1080, 1350] };
  const COL = { green: "var(--success)", yellow: "var(--warning)", red: "var(--danger)", grey: "var(--surface-3)" };
  const FG = "var(--foreground)", T2 = "var(--text-2)", BG = "var(--background)";
  const FONT = { head: "Rubik, sans-serif", body: "Onest, sans-serif" };
  const E3 = (x) => 1 - Math.pow(1 - x, 3);
  const c01 = (x) => Math.max(0, Math.min(1, x));
  const seg = (t, a, b) => c01((t - a) / (b - a));

  // ширина строки: настоящая (measureText холста) или оценка (тесты в node без холста)
  const guess = (s, fs) => Array.from(String(s)).reduce((a, ch) => a + (/[0-9]/.test(ch) ? 0.6 : /[\s,.  ]/.test(ch) ? 0.3 : 0.62), 0) * fs;
  function measurer(ctx) {
    if (!ctx || typeof ctx.measureText !== "function") return guess;
    const cache = new Map();
    return (s, fs, fw, ff) => {
      const k = [s, fs, fw || 400, ff || "body"].join("|");
      if (!cache.has(k)) { ctx.font = (fw || 400) + " " + fs + "px " + FONT[ff || "body"]; cache.set(k, ctx.measureText(String(s)).width); }
      return cache.get(k);
    };
  }

  // ── кадр ──
  // o: {fmt: sq|post, title, cond, det, metrics: [{l, v, u, src?}], total (строка|null), totalL, totalU, totalNote, ping?,
  //     strip, days: [30 цветов green|yellow|red|grey], date, brand}; src и ping — «чьи это цифры» (владелец 24.10)
  function frame(t, o, mw) {
    const m = mw || guess, wh = SIZES[o.fmt] || SIZES.sq, W = wh[0], H = wh[1], post = o.fmt === "post", P = 72, dy = post ? 90 : 0, it = [];
    it.push({ k: "rect", x: 0, y: 0, w: W, h: H, fill: BG });
    it.push(Object.assign({ k: "text", x: P, y: H - 64, fill: T2 }, fit(o.date, 24, 16, W - 2 * P - m(o.brand, 22, 500) - 24, m)));
    it.push({ k: "text", x: W - P, y: H - 64, s: o.brand, fs: 22, fw: 500, fill: T2, a: "end" });
    if (t < AT.intro) {
      const cy = H / 2;
      it.push({ k: "rect", x: W / 2 - 60, y: cy - 170, w: 120, h: 120, rx: 32, fill: "var(--accent)" });
      it.push({ k: "text", x: W / 2, y: cy - 86, s: "K", fs: 72, fw: 700, ff: "head", fill: "var(--accent-foreground)", a: "middle" });
      it.push(Object.assign({ k: "text", x: W / 2, y: cy + 50, fw: 600, ff: "head", fill: FG, a: "middle" }, fit(o.title, 56, 40, W - 2 * P, m, 600, "head")));
      it.push(Object.assign({ k: "text", x: W / 2, y: cy + 104, fill: T2, a: "middle" }, fit(o.cond, 26, 18, W - 2 * P, m)));
      return { w: W, h: H, items: it };
    }
    it.push(Object.assign({ k: "text", x: P, y: 128, fw: 600, ff: "head", fill: FG }, fit(o.title, 44, 32, W - 2 * P, m, 600, "head")));
    it.push(Object.assign({ k: "text", x: P, y: 176, fill: T2 }, fit(o.cond, 26, 18, W - 2 * P, m)));
    if (o.det) it.push(Object.assign({ k: "text", x: P, y: 214, fill: T2 }, fit(o.det, 24, 16, W - 2 * P, m)));
    (o.metrics || []).slice(0, 4).forEach((x, j) => {
      const px = P + (j % 2) * 480, py = 320 + dy + Math.floor(j / 2) * 150;
      it.push({ k: "text", x: px, y: py - 62, s: x.l, fs: 26, fill: T2 });
      ticker(it, x.v, px, py, 72, t, AT.intro + j * 0.12, AT.intro + 0.8 + j * 0.06, m);
      it.push({ k: "text", x: px + m(x.v, 72, 600, "head") + 14, y: py, s: x.u, fs: 30, fill: T2 });
      if (x.src) it.push(Object.assign({ k: "text", x: px, y: py + 38, fill: T2 }, fit(x.src, 22, 16, 440, m)));
    });
    if (o.ping) it.push(Object.assign({ k: "text", x: P, y: 470 + dy + 38, fill: T2 }, fit(o.ping, 22, 16, W - 2 * P, m)));
    const yT = 700 + dy * 2;
    it.push({ k: "text", x: P, y: yT - 100, s: o.totalL, fs: 28, fill: T2 });
    if (o.total !== null && o.total !== undefined && t >= AT.total) {   // до своего переката числа нет (не «000» 2,5 с)
      const s = String(o.total);
      ticker(it, s, P, yT, 120, t, AT.total, AT.total + 0.8, m);
      it.push({ k: "text", x: P + m(s, 120, 600, "head") + 20, y: yT, s: o.totalU, fs: 40, fw: 500, fill: FG });
    }
    it.push(Object.assign({ k: "text", x: P, y: yT + 48, fill: T2 }, fit(o.totalNote, 26, 18, W - 2 * P, m)));
    const sy = post ? 1186 : 850, sh = post ? 48 : 52, gap = 6, bw = (W - 2 * P - 29 * gap) / 30;
    it.push(Object.assign({ k: "text", x: P, y: sy - 18, fill: T2 }, fit(o.strip, 24, 18, W - 2 * P, m)));
    (o.days || []).slice(-30).forEach((d, i) => {
      const lit = t >= AT.days + i * 0.04;
      it.push({ k: "rect", x: P + i * (bw + gap), y: sy, w: bw, h: sh, rx: 6, fill: lit ? COL[d] || COL.grey : COL.grey });
    });
    return { w: W, h: H, items: it };
  }
  // строка в ширину кадра: кегль от fs вниз до min, не влезла и так — обрезка с «…» (длинная модель процессора и
  // страна вместе с тарифом, условие проверки по-английски)
  function fit(str, fs, min, maxW, m, fw, ff) {
    let s = String(str == null ? "" : str);
    const w = m(s, fs, fw, ff);
    if (w <= maxW) return { s, fs };
    const f = Math.max(min, Math.floor(fs * maxW / w));
    if (m(s, f, fw, ff) <= maxW) return { s, fs: f };
    let a = Array.from(s);
    while (a.length > 1 && m(a.join("") + "…", f, fw, ff) > maxW) a = a.slice(0, -1);
    return { s: a.join("").replace(/[\s·,]+$/, "") + "…", fs: f };
  }
  // перекат: у каждой цифры столбец 0…9 в своём окне; запятая и пробел стоят сразу
  function ticker(it, str, x, y, fs, t, a, b, m) {
    let cx = x;
    const hh = fs * 0.92, top = y - fs * 0.76;
    Array.from(String(str)).forEach((ch, k) => {
      const w = m(ch, fs, 600, "head");
      if (!/[0-9]/.test(ch)) { it.push({ k: "text", x: cx, y, s: ch, fs, fw: 600, ff: "head", fill: FG }); cx += w; return; }
      const pos = Number(ch) * E3(seg(t, a + k * 0.06, b + k * 0.04)), bb = Math.floor(pos), fr = pos - bb, kids = [];
      [bb, bb + 1].forEach((dg, j) => { if (dg <= 9) kids.push({ k: "text", x: cx + w / 2, y: y + (j - fr) * hh, s: String(dg), fs, fw: 600, ff: "head", fill: FG, a: "middle" }); });
      it.push({ k: "clip", x: cx, y: top, w, h: hh, kids });
      cx += w;
    });
  }

  // ── холст ──
  // цвета токенов темы ролика: пробный элемент data-hero data-theme=<тема> (оттенок и яркость — как у панели), цвет
  // строки -> пиксель холста 1×1 -> rgba()
  function resolver(theme) {
    const root = document.documentElement;
    const box = document.createElement("div");
    box.className = "gx-vh";
    box.setAttribute("aria-hidden", "true");
    box.setAttribute("data-hero", "");
    box.setAttribute("data-theme", theme === "light" ? "light" : "dark");
    ["data-tint", "data-vibrant", "data-brand"].forEach((a) => { if (root.hasAttribute(a)) box.setAttribute(a, root.getAttribute(a)); });
    const pr = document.createElement("span");
    // без перехода цвета: у панели color меняется плавно (data-motion), и getComputedStyle отдавал бы прошлый цвет
    pr.style.transition = "none";
    pr.style.animation = "none";
    box.appendChild(pr);
    document.body.appendChild(box);
    const one = document.createElement("canvas");
    one.width = 1; one.height = 1;
    const c1 = one.getContext("2d", { willReadFrequently: true });
    const cache = new Map();
    const col = (s) => {
      if (!s) return "rgba(0,0,0,0)";
      if (cache.has(s)) return cache.get(s);
      pr.style.color = "";
      pr.style.color = s;
      const cs = getComputedStyle(pr).color;
      c1.clearRect(0, 0, 1, 1);
      c1.fillStyle = "#000";
      c1.fillStyle = cs;
      c1.fillRect(0, 0, 1, 1);
      const d = c1.getImageData(0, 0, 1, 1).data;
      const v = "rgba(" + d[0] + "," + d[1] + "," + d[2] + "," + (d[3] / 255).toFixed(3) + ")";
      cache.set(s, v);
      return v;
    };
    return { col, done: () => box.remove() };
  }
  // scale — доля 1080: превью рисуется тем же списком на холсте меньше
  function draw(ctx, dl, col, scale) {
    const k = scale || 1;
    ctx.setTransform(k, 0, 0, k, 0, 0);
    const one = (x) => {
      ctx.save();
      if (x.k === "rect") {
        ctx.beginPath();
        if (ctx.roundRect && x.rx) ctx.roundRect(x.x, x.y, x.w, x.h, x.rx); else ctx.rect(x.x, x.y, x.w, x.h);
        ctx.fillStyle = col(x.fill);
        ctx.fill();
      } else if (x.k === "text") {
        ctx.font = (x.fw || 400) + " " + x.fs + "px " + FONT[x.ff || "body"];
        ctx.textAlign = x.a === "middle" ? "center" : x.a === "end" ? "right" : "left";
        ctx.textBaseline = "alphabetic";
        ctx.fillStyle = col(x.fill);
        ctx.fillText(x.s, x.x, x.y);
      } else if (x.k === "clip") {
        ctx.beginPath();
        ctx.rect(x.x, x.y, x.w, x.h);
        ctx.clip();
        x.kids.forEach(one);
      }
      ctx.restore();
    };
    dl.items.forEach(one);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }
  function fontsReady() {
    try {
      return Promise.all(["600 100px Rubik", "700 100px Rubik", "400 30px Onest", "500 30px Onest"].map((f) => document.fonts.load(f))).catch(() => null);
    } catch (e) { return Promise.resolve(null); }
  }

  // ── GIF89a ──
  // палитра: корзины 5 бит на канал по частоте из опорных кадров (RGBA), до 256 цветов (средний цвет корзины)
  function palette(imgs) {
    const n = new Uint32Array(32768), r = new Float64Array(32768), g = new Float64Array(32768), b = new Float64Array(32768);
    imgs.forEach((d) => {
      for (let i = 0; i < d.length; i += 4) {
        const k = ((d[i] >> 3) << 10) | ((d[i + 1] >> 3) << 5) | (d[i + 2] >> 3);
        n[k] += 1; r[k] += d[i]; g[k] += d[i + 1]; b[k] += d[i + 2];
      }
    });
    const keys = [];
    for (let k = 0; k < 32768; k++) if (n[k]) keys.push(k);
    keys.sort((x, y) => n[y] - n[x] || x - y);
    const pal = keys.slice(0, 256).map((k) => [Math.round(r[k] / n[k]), Math.round(g[k] / n[k]), Math.round(b[k] / n[k])]);
    if (!pal.length) pal.push([0, 0, 0]);
    return pal;
  }
  // индекс цвета: ближайший цвет палитры для корзины точки (взвешенно 2:4:3), один раз на корзину
  function indexer(pal) {
    const lut = new Int16Array(32768).fill(-1);
    return (data, out) => {
      for (let i = 0, j = 0; j < out.length; i += 4, j++) {
        const k = ((data[i] >> 3) << 10) | ((data[i + 1] >> 3) << 5) | (data[i + 2] >> 3);
        let v = lut[k];
        if (v < 0) {
          const rr = data[i], gg = data[i + 1], bb = data[i + 2];
          let bd = Infinity;
          for (let p = 0; p < pal.length; p++) {
            const c = pal[p], dr = c[0] - rr, dg = c[1] - gg, db = c[2] - bb, dd = dr * dr * 2 + dg * dg * 4 + db * db * 3;
            if (dd < bd) { bd = dd; v = p; }
          }
          lut[k] = v;
        }
        out[j] = v;
      }
      return out;
    };
  }
  // байты с ростом (Uint8Array)
  function Bytes(n0) {
    let buf = new Uint8Array(n0 || 4096), len = 0;
    const grow = (need) => { if (len + need <= buf.length) return; let n = buf.length * 2; while (n < len + need) n *= 2; const nb = new Uint8Array(n); nb.set(buf.subarray(0, len)); buf = nb; };
    return {
      byte(v) { grow(1); buf[len++] = v & 255; },
      bytes(a) { grow(a.length); buf.set(a, len); len += a.length; },
      u16(v) { grow(2); buf[len++] = v & 255; buf[len++] = (v >> 8) & 255; },
      str(s) { for (let i = 0; i < s.length; i++) this.byte(s.charCodeAt(i)); },
      get length() { return len; },
      done() { return buf.subarray(0, len); },
    };
  }
  // LZW GIF (код 8 бит, 9–12 бит): таблица на 5003 с двойным хешем, как в GIFEncoder (jsgif); -> байты без деления на блоки
  const HSIZE = 5003, MAXCODE = 4096, MASKS = [0, 1, 3, 7, 15, 31, 63, 127, 255, 511, 1023, 2047, 4095, 8191, 16383, 32767, 65535];
  function lzw(px) {
    const out = Bytes(Math.max(4096, px.length >> 2)), htab = new Int32Array(HSIZE).fill(-1), codetab = new Int32Array(HSIZE);
    const CLEAR = 256, EOI = 257, INIT = 9;
    let nBits = INIT, maxcode = (1 << nBits) - 1, free = CLEAR + 2, clearFlg = false, acc = 0, bits = 0;
    const put = (code) => {
      acc &= MASKS[bits];
      acc = bits > 0 ? acc | (code << bits) : code;
      bits += nBits;
      while (bits >= 8) { out.byte(acc & 255); acc >>= 8; bits -= 8; }
      if (free > maxcode || clearFlg) {
        if (clearFlg) { nBits = INIT; maxcode = (1 << nBits) - 1; clearFlg = false; } else { nBits += 1; maxcode = nBits === 12 ? MAXCODE : (1 << nBits) - 1; }
      }
      if (code === EOI) while (bits > 0) { out.byte(acc & 255); acc >>= 8; bits -= 8; }
    };
    put(CLEAR);
    let ent = px[0];
    outer: for (let i = 1; i < px.length; i++) {
      const c = px[i], fcode = (c << 12) + ent;
      let h = (c << 4) ^ ent;
      if (htab[h] === fcode) { ent = codetab[h]; continue; }
      if (htab[h] >= 0) {
        const disp = h === 0 ? 1 : HSIZE - h;
        do {
          h -= disp;
          if (h < 0) h += HSIZE;
          if (htab[h] === fcode) { ent = codetab[h]; continue outer; }
        } while (htab[h] >= 0);
      }
      put(ent);
      ent = c;
      if (free < MAXCODE) { codetab[h] = free++; htab[h] = fcode; } else { htab.fill(-1); free = CLEAR + 2; clearFlg = true; put(CLEAR); }
    }
    put(ent);
    put(EOI);
    return out.done();
  }
  // прямоугольник изменившихся точек (индексы), null — кадры одинаковы
  function diffRect(a, b, W, H) {
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let y = 0; y < H; y++) {
      const row = y * W;
      let l = -1, r = -1;
      for (let x = 0; x < W; x++) if (a[row + x] !== b[row + x]) { l = x; break; }
      if (l < 0) continue;
      for (let x = W - 1; x >= l; x--) if (a[row + x] !== b[row + x]) { r = x; break; }
      if (l < x0) x0 = l;
      if (r > x1) x1 = r;
      if (y < y0) y0 = y;
      y1 = y;
    }
    return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  }
  function crop(px, W, r) {
    const out = new Uint8Array(r.w * r.h);
    for (let y = 0; y < r.h; y++) out.set(px.subarray((r.y + y) * W + r.x, (r.y + y) * W + r.x + r.w), y * r.w);
    return out;
  }
  // задержки кадров в сотых: k-й кадр при fps — сумма всех ровно 1500 (15 с)
  function delays(fps) {
    const n = DUR * fps, out = [];
    for (let k = 0; k < n; k++) out.push(Math.round(((k + 1) * 100) / fps) - Math.round((k * 100) / fps));
    return out;
  }
  // файл: заголовок, общая палитра (2^n), бесконечный повтор, кадры [{rect, delay, data}] -> Uint8Array
  function gifFile(w, h, pal, frames) {
    let bitsN = 1;
    while ((1 << bitsN) < pal.length) bitsN += 1;
    const size = 1 << bitsN, b = Bytes(1024 + frames.reduce((a, f) => a + f.data.length + Math.ceil(f.data.length / 255) + 32, 0));
    b.str("GIF89a"); b.u16(w); b.u16(h); b.byte(0x80 | ((bitsN - 1) << 4) | (bitsN - 1)); b.byte(0); b.byte(0);
    for (let i = 0; i < size; i++) { const c = pal[i] || [0, 0, 0]; b.byte(c[0]); b.byte(c[1]); b.byte(c[2]); }
    b.byte(0x21); b.byte(0xff); b.byte(11); b.str("NETSCAPE2.0"); b.byte(3); b.byte(1); b.u16(0); b.byte(0);
    frames.forEach((f) => {
      const r = f.rect;
      b.byte(0x21); b.byte(0xf9); b.byte(4); b.byte(0x04); b.u16(f.delay); b.byte(0); b.byte(0);   // «не стирать», без прозрачности
      b.byte(0x2c); b.u16(r.x); b.u16(r.y); b.u16(r.w); b.u16(r.h); b.byte(0);
      b.byte(8);
      for (let i = 0; i < f.data.length; i += 255) { const n = Math.min(255, f.data.length - i); b.byte(n); b.bytes(f.data.subarray(i, i + n)); }
      b.byte(0);
    });
    b.byte(0x3b);
    return b.done();
  }
  // кадры -> GIF: grab(t) даёт RGBA кадра в момент t; onProg(k, n); stop() -> true — прервать
  async function encode(W, H, grab, fps, onProg, stop) {
    const pal = palette([grab(0.3), grab(AT.total + 0.4), grab(DUR - 0.01)]), ix = indexer(pal), ds = delays(fps), frames = [];
    let prev = null, cur = new Uint8Array(W * H);
    for (let k = 0; k < ds.length; k++) {
      if (stop && stop()) throw new Error("cancelled");
      ix(grab(k / fps), cur);
      if (!prev) frames.push({ delay: ds[k], rect: { x: 0, y: 0, w: W, h: H }, data: lzw(cur) });
      else {
        const r = diffRect(prev, cur, W, H);
        if (!r) frames[frames.length - 1].delay += ds[k];
        else frames.push({ delay: ds[k], rect: r, data: lzw(crop(cur, W, r)) });
      }
      const t = prev || new Uint8Array(W * H);
      prev = cur;
      cur = t;
      if (onProg) onProg(k + 1, ds.length);
      await new Promise((res) => setTimeout(res, 0));
    }
    return { bytes: gifFile(W, H, pal, frames), frames: frames.length, colors: pal.length };
  }

  const MAX_BYTES = 3 * 1024 * 1024;                              // §5.3: ≤ 3 МБ, иначе ступень 10 кадров/с
  function canvasFor(o) { const wh = SIZES[o.fmt] || SIZES.sq, cv = document.createElement("canvas"); cv.width = wh[0]; cv.height = wh[1]; return cv; }
  async function makeGIF(o, onProg, stop) {
    await fontsReady();
    const cv = canvasFor(o), ctx = cv.getContext("2d", { willReadFrequently: true }), rs = resolver(o.theme), mw = measurer(ctx);
    const grab = (t) => { ctx.clearRect(0, 0, cv.width, cv.height); draw(ctx, frame(t, o, mw), rs.col); return ctx.getImageData(0, 0, cv.width, cv.height).data; };
    try {
      let fps = 15, got = await encode(cv.width, cv.height, grab, fps, onProg, stop);
      if (got.bytes.length > MAX_BYTES) { fps = 10; got = await encode(cv.width, cv.height, grab, fps, onProg, stop); }
      return { blob: new Blob([got.bytes], { type: "image/gif" }), fps, frames: got.frames };
    } finally { rs.done(); }
  }
  async function makePNG(o) {
    await fontsReady();
    const cv = canvasFor(o), ctx = cv.getContext("2d"), rs = resolver(o.theme);
    try { draw(ctx, frame(DUR - 0.01, o, measurer(ctx)), rs.col); } finally { rs.done(); }
    return new Promise((res, rej) => cv.toBlob((b) => (b ? res(b) : rej(new Error("png"))), "image/png"));
  }
  // живое превью на своём холсте: свой цикл (requestAnimationFrame), без движения — последний кадр
  function preview(cv, o, still) {
    const wh = SIZES[o.fmt] || SIZES.sq, ctx = cv.getContext("2d"), rs = resolver(o.theme), mw = measurer(ctx);
    const k = cv.width / wh[0];
    let raf = 0, gone = false;
    const paint = (t) => { ctx.clearRect(0, 0, cv.width, cv.height); draw(ctx, frame(t, o, mw), rs.col, k); };
    fontsReady().then(() => {
      if (gone) return;
      if (still) { paint(DUR - 0.01); return; }
      const t0 = performance.now();
      const tick = (now) => { if (gone) return; paint(((now - t0) / 1000) % DUR); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    });
    return () => { gone = true; cancelAnimationFrame(raf); rs.done(); };
  }


  // ── окно «Поделиться» (макет r1: окно 900 px на ПК, лист снизу на телефоне) ──
  // Данные — только GET /api/host/share (K.API.host.share) со всеми тремя галочками сразу: значения видны рядом с
  // галочками, как в макете, а в кадр идёт только отмеченное; галочки выключены при каждом открытии. Формат, тема и
  // язык ролика — localStorage браузера («Сверка 24.10» п. 2: на узле ничего не хранится). Строки кадра — на языке
  // ролика, не панели: словари раздела diag на обоих языках (английский — pages/diag-i18n-en.js, если панель
  // по-русски: файл только кладёт словарь в KASKAD_I18N_ADD_EN, панель его не вливает).
  const PREF = "kaskad.share";
  const FLAGS = ["tariff", "country", "cpu"];
  // «до N МБ» до сборки — верхняя граница 15-секундного ролика: замер tools/k3_share_gif_size.mjs на заглушке 24.10 —
  // 0,14–0,19 МБ, «худший» кадр (полоса четырёх цветов, длинные числа, все галочки) — до 0,22 МБ у обоих форматов;
  // 0,4 — запас под другие шрифты и сглаживание. Настоящие данные стенда (T-3310 М-4) — 0,06 МБ: одинаковых кадров
  // больше, поэтому «≈ 0,4» обещало в 6 раз больше — честно только «до». Макет r1 (1,4/1,7) считал 5 с без склейки
  // одинаковых кадров. Проверка — tests/test_share_gif_size.py (граница не меньше замера и не больше 2,5 замера)
  const EST_MB = { sq: 0.4, post: 0.4 };
  const LABEL = { down: "dg.ck.down", up: "dg.ck.up", rtt: "dg.ck.rtt", loss: "dg.ck.loss" };
  const UNIT = { down: "dg.u.mbps", up: "dg.u.mbps", rtt: "dg.u.ms" };
  const PARTW = { night: "dg.part.night", morning: "dg.part.morning", day: "dg.part.day", evening: "dg.part.evening", mixed: "dg.part.mixed" };
  const DAYC = ["green", "yellow", "red", "grey"];
  const BRAND = "Kaskad Pro · anten-ka";                        // подпись кадра — имя продукта и канала, не перевод
  const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
  const isNum = (v) => typeof v === "number" && isFinite(v);

  function readPrefs(lang, theme) {
    let v = null;
    try { v = JSON.parse(localStorage.getItem(PREF) || "null"); } catch (e) { v = null; }
    if (!v || typeof v !== "object") v = {};
    return { fmt: v.fmt === "post" ? "post" : "sq", theme: v.theme === "light" || v.theme === "dark" ? v.theme : theme,
      L: v.L === "en" || v.L === "ru" ? v.L : lang };
  }
  function savePrefs(p) {
    try { localStorage.setItem(PREF, JSON.stringify({ fmt: p.fmt, theme: p.theme, L: p.L })); } catch (e) { /* хранилище закрыто — до перезагрузки */ }
  }
  // словарь языка L: русский раздела, английский поверх; нет ключа — сам ключ (как t панели)
  function dictOf(L) {
    const ru = (window.KASKAD_I18N_ADD || {}).diag || {}, en = (window.KASKAD_I18N_ADD_EN || {}).diag || {};
    return L === "en" ? Object.assign({}, ru, en) : ru;
  }
  const fillIn = (s, vars) => (vars ? String(s).replace(/\{(\w+)\}/g, (m, k) => (vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m)) : String(s));
  const tx = (d, key, vars) => fillIn(d[key] !== undefined ? d[key] : key, vars);
  // число на языке ролика: от 10 — целое, от 1 — десятые, меньше — сотые (как числа «Сейчас»)
  function num(L, v) {
    const a = Math.abs(v), dg = a >= 10 ? 0 : a >= 1 ? 1 : 2;
    try { return new Intl.NumberFormat(L === "en" ? "en-GB" : "ru-RU", { maximumFractionDigits: dg }).format(v); } catch (e) { return String(Math.round(v * 100) / 100); }
  }
  // «2 ядра» — формы через «|», как tn панели
  function txn(d, L, key, n) {
    const forms = tx(d, key).split("|");
    let i = 2;
    try { const c = new Intl.PluralRules(L === "en" ? "en" : "ru").select(Math.abs(n)); i = c === "one" ? 0 : c === "few" ? 1 : 2; } catch (e) { i = 2; }
    return fillIn(forms[Math.min(i, forms.length - 1)], { n: num(L, n) });
  }
  function tariffText(d, L, tf) {
    if (!tf || typeof tf !== "object") return null;
    const out = [];
    if (isNum(tf.vcpu) && tf.vcpu > 0) out.push(txn(d, L, "dg.sh.fCores", tf.vcpu));
    if (isNum(tf.ram_gb) && tf.ram_gb > 0) out.push(tx(d, "dg.sh.fRam", { n: num(L, tf.ram_gb) }));
    if (isNum(tf.port_mbit) && tf.port_mbit > 0) {
      out.push(tx(d, "dg.sh.fPort", { v: tf.port_mbit >= 1000 ? tx(d, "dg.sh.fGbit", { n: num(L, tf.port_mbit / 1000) }) : num(L, tf.port_mbit) + " " + tx(d, "dg.u.mbps") }));
    }
    return out.length ? out.join(" · ") : null;
  }
  function countryText(L, iso) {
    if (typeof iso !== "string" || !/^[A-Z]{2}$/.test(iso)) return null;
    try { return new Intl.DisplayNames([L === "en" ? "en" : "ru"], { type: "region" }).of(iso) || iso; } catch (e) { return iso; }
  }
  // модель процессора — уже прошла share_text сервера; здесь ещё раз: только печатные ASCII, не длиннее 48
  const cpuText = (s) => (typeof s === "string" && /^[\x20-\x7e]{1,48}$/.test(s) ? s : null);
  // «23 октября 2026» / «October 23, 2026» — день проверки по часам узла (YYYY-MM-DD ответа)
  function dateText(L, day) {
    if (typeof day !== "string" || !DAY_RE.test(day)) return "";
    const dt = new Date(day + "T12:00:00Z");
    try {
      if (L === "en") return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" }).format(dt);
      return new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", day: "numeric", month: "long" }).format(dt) + " " + day.slice(0, 4);
    } catch (e) { return day; }
  }
  const capFirst = (x) => (x ? x.charAt(0).toUpperCase() + x.slice(1) : x);
  // значение «что показать» (язык L) или null — нет данных / сервер его не отдал
  function extraText(d, L, doc, f) {
    const ex = (doc && doc.extra) || {};
    return f === "tariff" ? tariffText(d, L, ex.tariff) : f === "country" ? countryText(L, ex.country) : cpuText(ex.cpu);
  }
  // ответ сервера + настройки окна -> строки кадра (frame) на языке ролика; адресов, имён и времени здесь нет — их нет и в ответе
  // время проверки рядом с датой (владелец 24.10): « · 04:10 UTC · 07:10 МСК»; Москва — UTC+3 без перевода часов
  function timesText(L, at) {
    if (!isNum(at) || at <= 0) return "";
    const hm = (s) => { const x = new Date(s * 1000); return String(x.getUTCHours()).padStart(2, "0") + ":" + String(x.getUTCMinutes()).padStart(2, "0"); };
    return " · " + hm(at) + " UTC · " + hm(at + 3 * 3600) + (L === "en" ? " MSK" : " МСК");
  }
  // «чьи это цифры» (владелец 24.10): до какой точки мерили скорость, до каких адресов — пинг; неизвестная точка — без строки
  const PT_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
  function pointName(d, id) {
    if (typeof id !== "string" || !PT_RE.test(id)) return "";
    const k = "dg.sh.pt." + id.split("-")[0];
    return d[k] !== undefined ? d[k] : "";
  }
  function srcText(d, id, wh) {
    const p = id === "down" || id === "up" ? pointName(d, wh[id]) : "";
    return p ? tx(d, id === "down" ? "dg.sh.srcDown" : "dg.sh.srcUp", { p }) : "";
  }
  const DNS = ["cloudflare", "google", "yandex"];                  // публичные DNS по умолчанию (share.DNS_OF)
  function pingText(d, wh) {
    const a = wh.anchors;
    if (Array.isArray(a) && a.length && a.every((x) => DNS.indexOf(x) >= 0)) return tx(d, "dg.sh.srcPing", { list: a.map((x) => tx(d, "dg.sh.dns." + x)).join(" · ") });
    if (a === "own" && isNum(wh.n) && wh.n > 0) return tx(d, "dg.sh.srcPingOwn", { n: String(Math.round(wh.n)) });
    return "";
  }
  function clipOf(doc, p, flags) {
    const L = p.L === "en" ? "en" : "ru", d = dictOf(L), ch = (doc && doc.checked) || {}, wh = (doc && doc.where) || {};
    const ppl = tx(d, ch.people === true ? "dg.sh.fPplYes" : "dg.sh.fPplNo");
    const part = PARTW[ch.part];
    const det = FLAGS.filter((f) => flags[f]).map((f) => extraText(d, L, doc, f)).filter(Boolean).join(" · ");
    const metrics = (Array.isArray(doc.metrics) ? doc.metrics : []).filter((x) => x && LABEL[x.id]).slice(0, 4).map((x) => {
      const ok = isNum(x.v);
      const out = { l: tx(d, LABEL[x.id]), v: ok ? num(L, x.v) : "—", u: !ok ? "" : x.id === "loss" ? "%" : tx(d, UNIT[x.id]) };
      const src = ok ? srcText(d, x.id, wh) : "";
      if (src) out.src = src;
      return out;
    });
    const total = isNum(doc.rel_total) ? num(L, Math.round(doc.rel_total)) : null;
    const n = isNum(doc.rel_n) ? doc.rel_n : 0, need = isNum(doc.rel_need) && doc.rel_need > 0 ? doc.rel_need : 3;
    const ping = pingText(d, wh);
    const o = { fmt: p.fmt === "post" ? "post" : "sq", theme: p.theme === "light" ? "light" : "dark", title: tx(d, "dg.sh.fTitle"),
      cond: part ? tx(d, "dg.sh.fCond", { part: tx(d, part), ppl }) : capFirst(ppl), det, metrics, total,
      totalL: tx(d, "dg.totK"), totalU: tx(d, "dg.sh.fTotU"),
      totalNote: total !== null ? tx(d, "dg.sh.fTotN") : capFirst(tx(d, "dg.relNoneLinks", { n: num(L, n), need: num(L, need) })),
      strip: tx(d, "dg.sh.fStrip"), days: (Array.isArray(doc.days) ? doc.days : []).slice(-30).map((c) => (DAYC.indexOf(c) >= 0 ? c : "grey")),
      date: dateText(L, doc.date) + (dateText(L, doc.date) ? timesText(L, doc.at) : ""), brand: BRAND };
    if (ping) o.ping = ping;
    return o;
  }
  const fileName = (doc, ext) => "kaskad-check-" + (doc && typeof doc.date === "string" && DAY_RE.test(doc.date) ? doc.date : "now") + "." + ext;
  function download(blob, name) {
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.className = "gx-vh";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }
  const reduced = () => { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  const mb = (L, bytes) => num(L, Math.max(0.1, Math.round(bytes / 104857.6) / 10));

  // окно. K — KaskadPanel; opt.onCheck() — «Проверить сейчас» (нет годной проверки): окно цены раздела
  function open(K, opt) {
    const { el, t, U } = K;
    const lang = K.lang === "en" ? "en" : "ru";
    // по умолчанию — квадрат, тёмная тема, язык панели (макет r1); дальше — как человек выбрал в прошлый раз
    const st = Object.assign(readPrefs(lang, "dark"), { flags: { tariff: false, country: false, cpu: false }, doc: null, err: null,
      busy: false, k: 0, n: 75, ready: null, msg: "", job: null });
    let gone = false, halt = null, pvKey = "", cv = null, progNode = null, progBtn = null;
    const box = el("div", { class: "gx-sharebox" });
    const dlg = K.openDialog({ size: "lg", title: t("dg.share"), blocks: [box],
      onClose: () => { gone = true; st.job = null; if (halt) halt(); halt = null; } });
    const say = (m) => { st.msg = m || ""; draw(); };
    // английский словарь раздела для ролика при русской панели — один раз, до перерисовки (не загрузился — по-русски)
    const enReady = () => (st.L === "en" && !((window.KASKAD_I18N_ADD_EN || {}).diag) ? K.loadScript("pages/diag-i18n-en.js").catch(() => null) : Promise.resolve(null));
    const set = (patch) => {
      if (st.busy) return;
      Object.assign(st, patch);
      savePrefs(st);
      st.ready = null;
      st.msg = "";
      enReady().then(() => draw());
    };
    const load = () => {
      st.doc = null;
      st.err = null;
      draw();
      return K.API.host.share({ tariff: 1, country: 1, cpu: 1 }).then((d) => { st.doc = d && typeof d === "object" ? d : { ready: false, why: "no_check" }; },
        (e) => { st.err = e || new Error("share"); }).then(() => enReady()).then(() => draw());
    };
    const clip = () => clipOf(st.doc, st, st.flags);
    const pvWidth = () => Math.round(380 * Math.min(2, Math.max(1, window.devicePixelRatio || 1)));
    function pvCanvas(o) {
      const key = JSON.stringify(o);
      if (cv && key === pvKey) return cv;
      if (halt) halt();
      const wh = SIZES[o.fmt] || SIZES.sq, w = pvWidth();
      cv = el("canvas", { class: "gx-share__cv", width: String(w), height: String(Math.round(w * wh[1] / wh[0])), role: "img", "aria-label": t("dg.sh.pvAria") });
      halt = preview(cv, o, reduced());
      pvKey = key;
      return cv;
    }
    function build() {
      if (st.busy || !st.doc || !st.doc.ready) return null;
      const o = clip(), job = {};
      Object.assign(st, { job, busy: true, k: 0, n: 75, ready: null, msg: "" });
      draw();
      makeGIF(o, (k, n) => {
        if (st.job !== job || gone) return;
        st.k = k;
        st.n = n;
        const p = Math.round(100 * k / Math.max(1, n));
        if (progNode) { progNode.max = n; progNode.value = k; progNode.setAttribute("aria-label", t("dg.sh.making", { p })); }
        if (progBtn) progBtn.lastChild.textContent = t("dg.sh.making", { p });
      }, () => st.job !== job || gone).then((r) => {
        if (st.job !== job || gone) return;
        Object.assign(st, { job: null, busy: false, ready: r });
        draw();
      }, () => {
        if (st.job !== job || gone) return;
        Object.assign(st, { job: null, busy: false, ready: null, msg: t("dg.sh.failed") });
        draw();
      });
      return null;
    }
    const png = () => makePNG(clip()).then((b) => download(b, fileName(st.doc, "png")), () => say(t("dg.sh.failed")));
    function copy() {
      try {
        if (!window.ClipboardItem || !navigator.clipboard || !navigator.clipboard.write) { say(t("dg.sh.copyNo")); return null; }
        // обещание PNG прямо в ClipboardItem: Safari не теряет «нажатие» человека, пока картинка собирается
        return navigator.clipboard.write([new window.ClipboardItem({ "image/png": makePNG(clip()) })]).then(() => say(t("dg.sh.copied")), () => say(t("dg.sh.copyNo")));
      } catch (e) { say(t("dg.sh.copyNo")); return null; }
    }
    // «Отправить» — только по нажатию на готовый файл (лист «Поделиться» телефона); отказ человека — молча
    function send() {
      const r = st.ready;
      if (!r) return null;
      let file = null, can = false;
      try { file = new File([r.blob], fileName(st.doc, "gif"), { type: "image/gif" }); can = !!(navigator.canShare && navigator.share && navigator.canShare({ files: [file] })); } catch (e) { can = false; }
      if (!can) { say(t("dg.sh.sendNo")); return null; }
      return navigator.share({ files: [file] }).then(() => null, (e) => { if (!(e && e.name === "AbortError")) say(t("dg.sh.sendNo")); });
    }
    const field = (label, node) => el("div", { class: "gx-share__f" }, el("span", { class: "gx-share__k", text: label }), node);
    function checks() {
      const d = dictOf(lang), dropped = Array.isArray(st.doc.dropped) ? st.doc.dropped : [];
      const name = { tariff: "dg.sh.tariff", country: "dg.sh.country", cpu: "dg.sh.cpu" };
      return el("div", { class: "gx-check gx-share__checks", role: "group", "aria-label": t("dg.sh.what") }, FLAGS.map((f) => {
        const v = extraText(d, lang, st.doc, f), off = v === null;
        if (off) st.flags[f] = false;
        const b = el("button", { type: "button", class: "gx-check__i", role: "checkbox", "aria-checked": String(!!st.flags[f]), disabled: off || st.busy ? true : null },
          el("span", { class: "gx-box" }, K.icon("check", 14)),
          el("span", { class: "gx-share__ct" }, el("span", { text: t(name[f]) }),
            el("small", { text: off ? t(dropped.indexOf(f) >= 0 ? "dg.sh.dropped" : "dg.sh.none") : v })));
        if (!off) b.addEventListener("click", () => { const fl = Object.assign({}, st.flags); fl[f] = !fl[f]; set({ flags: fl }); });
        return b;
      }));
    }
    function actions() {
      progNode = null;
      progBtn = null;
      const out = [];
      if (st.busy) {
        const p = Math.round(100 * st.k / Math.max(1, st.n));
        progBtn = U.btn(t("dg.sh.making", { p }), "primary", null, { md: true, dis: true });
        progBtn.setAttribute("aria-busy", "true");
        progNode = el("progress", { class: "gx-prog", max: String(st.n), value: String(st.k), "aria-label": t("dg.sh.making", { p }) });
        out.push(el("div", { class: "gx-share__btns" }, progBtn), progNode, el("span", { class: "gx-share__note", role: "status", text: t("dg.sh.makingSub") }));
      } else if (st.ready) {
        out.push(el("span", { class: "gx-share__done", role: "status" }, K.icon("circle-check-fill", 16),
          el("span", { text: t("dg.sh.made", { mb: mb(lang, st.ready.blob.size), fps: K.fmtInt(st.ready.fps) }) })));
        out.push(el("div", { class: "gx-share__btns" },
          U.btn(t("dg.sh.send"), "primary", () => send(), { md: true, icon: "arrow-up-from-line", tip: t("dg.sh.sendHint") }),
          U.btn(t("dg.sh.dl"), "tertiary", () => download(st.ready.blob, fileName(st.doc, "gif")), { md: true, icon: "arrow-down-to-line" }),
          U.btn(t("dg.sh.png"), "ghost", () => png(), { md: true })));
        out.push(el("span", { class: "gx-share__note", text: t("dg.sh.sendHint") }));
      } else {
        out.push(el("div", { class: "gx-share__btns" },
          U.btn(t("dg.sh.gif"), "primary", () => build(), { md: true, icon: "arrow-down-to-line" }),
          U.btn(t("dg.sh.png"), "tertiary", () => png(), { md: true }),
          U.btn(t("dg.sh.copy"), "tertiary", () => copy(), { md: true, icon: "copy", tip: t("dg.sh.copyHint") })));
        out.push(el("span", { class: "gx-share__note", text: t("dg.sh.info", { mb: num(lang, EST_MB[st.fmt] || EST_MB.sq) }) }));
      }
      if (st.msg && !st.busy) out.push(el("span", { class: "gx-share__msg", role: "status", text: st.msg }));
      return out;
    }
    function draw() {
      if (gone) return;
      if (st.err) {
        box.replaceChildren(U.al("danger", t("dg.sh.loadFailed"), (st.err && st.err.message) || "", { btns: [U.btn(t("dg.sh.retry"), "tertiary", () => load(), { md: true })] }));
        return;
      }
      if (!st.doc) { box.replaceChildren(U.skel(3)); return; }
      if (!st.doc.ready) {
        box.replaceChildren(el("div", { class: "gx-share__first" },
          el("b", { class: "gx-share__ft", text: t("dg.sh.first") }),
          el("span", { class: "gx-share__note", text: t(st.doc.why === "stale" ? "dg.sh.firstStale" : "dg.sh.firstNone") }),
          el("div", { class: "gx-share__btns" }, U.btn(t("dg.checkBtn"), "primary", () => { dlg.close(null); if (opt && opt.onCheck) opt.onCheck(); }, { md: true }))));
        return;
      }
      const o = clip(), dis = st.busy;
      const seg = (label, items, full) => field(label, U.seg(items.map((x) => Object.assign({ dis }, x)), { full: !!full, aria: label }));
      box.replaceChildren(el("div", { class: "gx-share" },
        el("div", { class: "gx-share__pv" }, pvCanvas(o), el("span", { class: "gx-share__cap", text: t("dg.sh.preview") })),
        el("div", { class: "gx-share__set" },
          seg(t("dg.sh.fmt"), [{ label: t("dg.sh.fmtSq"), on: st.fmt === "sq", pick: () => set({ fmt: "sq" }) }, { label: t("dg.sh.fmtPost"), on: st.fmt === "post", pick: () => set({ fmt: "post" }) }], true),
          el("div", { class: "gx-share__row" },
            seg(t("dg.sh.theme"), [{ label: t("dg.sh.dark"), on: st.theme === "dark", pick: () => set({ theme: "dark" }) }, { label: t("dg.sh.light"), on: st.theme === "light", pick: () => set({ theme: "light" }) }]),
            seg(t("dg.sh.lang"), [{ label: "RU", on: st.L === "ru", pick: () => set({ L: "ru" }) }, { label: "EN", on: st.L === "en", pick: () => set({ L: "en" }) }])),
          field(t("dg.sh.what"), checks()),
          el("div", { class: "gx-share__priv" }, K.icon("shield-check", 16), el("span", { text: t("dg.sh.privacy") })),
          ...actions())));
    }
    load();
    return dlg;
  }

  window.KaskadShare = Object.freeze({ DUR, AT, SIZES, MAX_BYTES, frame, draw, palette, indexer, lzw, diffRect, crop, delays, gifFile, encode,
    makeGIF, makePNG, preview, guess, fit, clipOf, timesText, readPrefs, fileName, open });
})();
