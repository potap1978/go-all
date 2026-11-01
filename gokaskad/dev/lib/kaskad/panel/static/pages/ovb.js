/* Kaskad 3 — «Обзор» по варианту Б Claude Design (круг 1, шаг Т1; export-2026-10-27-it1-r3, «Kaskad Обзор it1 -
   вариант Б.dc.html», css/it1-b.css → ovb.css). Часть ядра: K.loadPart("ovb") грузит pages/ovb-i18n-ru.js (+ -en.js),
   этот файл; он регистрирует часть через K.part("ovb", def).

   Что рисует (правило владельца «сначала графика, потом цифры»):
   - summary(box, ctx) — Сводка: «Сервер сейчас» (маленький радар «Балл сервера» → окно, вердикт словами, балл,
     сбои с действием, полоса 30 дней) · [Трафик сервера | Выходы · час] · [Здоровье сервера 2 × 2 | События];
   - openScore() — окно «Балл сервера» (K.openSheet: ПК — справа, телефон — снизу): радар со слоями «Обычно» и
     «Свежие», итог, что изменилось к прошлой проверке, итог по проверкам, почему итог не выше, как считается;
   - health(box, ctx) — экран «Здоровье сервера» (вариант А в стиле Б: метрика → ряд за сутки → цифра → что сделать);
   - scoreCard(o) — карточка балла для «Проверка сервера › Сейчас» (pages/diag.js даёт свои «от обычного», кнопки).

   Данные: GET /api/host/synth (балл 0–1000, звенья, проверки; калибровка по звеньям — links[].calib, без самих
   эталонов; первые часы нового узла — provisional), GET /api/host/score (▲/▼ и полоса
   30 дней), GET /api/host/history?range=1d|7d (трафик и ряды здоровья), GET /api/gw/exits/hour (выходы за час одним
   ответом); здоровье, замечания, выходы и события — из ядра (ctx). Без «за свои деньги», цены, тарифа, ёмкости.
   Атрибутов style в разметке нет (CSP): положение подписей оси — CSSOM. Без шаблонных строк. */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const { el, t, U } = K;
  const NS = "http://www.w3.org/2000/svg";
  const LINKS = ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"];
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const num = (v) => (isNum(v) ? v : null);
  const arr = (v) => (Array.isArray(v) ? v : []);
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r1 = (v) => Math.round(v * 10) / 10;
  const DASH = "—";
  const NB = " ";
  const phone = () => !!(K.isPhone && K.isPhone());

  // ── чистые помощники (тесты в node: tests/panel/test_panel_ovb.py) ──────────────────────────────────────────────
  // пояс итога словом (RESEARCH «Главное» п. 10): 0–99, 100–299, 300–599, 600–799, 800+
  function bandOf(v) { return !isNum(v) ? -1 : v < 100 ? 0 : v < 300 ? 1 : v < 600 ? 2 : v < 800 ? 3 : 4; }
  // монотонная кривая Фрича — Карлсона по точкам [[x, y]] (как KaskadSpark.mono и макет)
  function mono(P) {
    const n = P.length;
    if (!n) return "";
    if (n === 1) return "M" + r1(P[0][0]) + " " + r1(P[0][1]) + "h.01";
    const dx = [], m = [];
    for (let i = 0; i < n - 1; i++) { dx[i] = P[i + 1][0] - P[i][0] || 1e-6; m[i] = (P[i + 1][1] - P[i][1]) / dx[i]; }
    const tg = [m[0]];
    for (let i = 1; i < n - 1; i++) tg[i] = m[i - 1] * m[i] <= 0 ? 0 : (3 * (dx[i - 1] + dx[i])) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
    tg[n - 1] = m[n - 2];
    let d = "M" + r1(P[0][0]) + " " + r1(P[0][1]);
    for (let i = 0; i < n - 1; i++) {
      const h = dx[i] / 3;
      d += "C" + r1(P[i][0] + h) + " " + r1(P[i][1] + tg[i] * h) + " " + r1(P[i + 1][0] - h) + " " + r1(P[i + 1][1] - tg[i + 1] * h) + " " + r1(P[i + 1][0]) + " " + r1(P[i + 1][1]);
    }
    return d;
  }
  // разрывы ряда: [[x, y] | null] -> отрезки без null
  function segs(pts) {
    const out = [];
    let cur = [];
    pts.forEach((p) => { if (!p) { if (cur.length) out.push(cur); cur = []; } else cur.push(p); });
    if (cur.length) out.push(cur);
    return out;
  }
  // состояние Сводки словами, без числа (владелец 28.10 «замечания не пишем»): нет сбоев — «Работает нормально»;
  // предупреждение ядра или жёлтое здоровье — «Работает, есть предупреждения»; красное — «Требует внимания». rows — [{key, tone}]
  // REV-it1 №3: выключенный шлюз (строка gwoff) — не «работает»: «Шлюз выключен»; «Работает нормально» — только при
  // зелёном здоровье: проверки здоровья выключены — «Работает, проверки здоровья выключены», нет данных — «Ждём данных»
  function verdictOf(rows, level) {
    if (rows.some((r) => r.tone === "danger") || level === "crit") return { key: "bad", tone: "crit" };
    if (rows.some((r) => r.key === "gwoff")) return { key: "off", tone: "none" };
    if (rows.length || level === "warn") return { key: "warn", tone: "warn" };
    if (level === "ok") return { key: "ok", tone: "ok" };
    if (level === "off") return { key: "nohc", tone: "none" };
    return { key: "wait", tone: "wait" };
  }
  // полоса 30 дней: цвет дня /api/host/score (green / yellow / red / grey) -> класс и счёт
  const DAY_CLS = { green: "", yellow: "is-warn", red: "is-fail", grey: "is-none" };
  function d30Of(days) {
    const list = arr(days).filter((d) => isObj(d) && /^\d{4}-\d{2}-\d{2}$/.test(String(d.day))).slice(-30);
    const cnt = { green: 0, yellow: 0, red: 0, grey: 0 };
    list.forEach((d) => { cnt[DAY_CLS[d.color] !== undefined ? d.color : "grey"] += 1; });
    return { list, cnt };
  }
  // ряд /api/host/history -> точки ведрами по bucket секунд: {ts, v: {col: число | null}} (sum — суммы, иначе среднее)
  function buckets(h, bucket, cols, sums) {
    if (!isObj(h) || !Array.isArray(h.cols) || !Array.isArray(h.points)) return [];
    const idx = {};
    cols.forEach((c) => { idx[c] = h.cols.indexOf(c) + 1; });
    const by = new Map();
    h.points.forEach((p) => {
      if (!Array.isArray(p) || !isNum(p[0])) return;
      const b = Math.floor(p[0] / bucket) * bucket;
      let e = by.get(b);
      if (!e) { e = { ts: b, s: {}, n: {} }; by.set(b, e); }
      cols.forEach((c) => { const v = idx[c] > 0 ? p[idx[c]] : null; if (isNum(v)) { e.s[c] = (e.s[c] || 0) + v; e.n[c] = (e.n[c] || 0) + 1; } });
    });
    return Array.from(by.values()).sort((a, b) => a.ts - b.ts).map((e) => {
      const v = {};
      cols.forEach((c) => { v[c] = e.n[c] ? (sums && sums.indexOf(c) >= 0 ? e.s[c] : e.s[c] / e.n[c]) : null; });
      return { ts: e.ts, v };
    });
  }
  // трафик: ведра 10 мин (сутки) или час (7 дней) -> Мбит/с, итоги в байтах, пик
  function trafficOf(h, week) {
    const step = week ? 3600 : 600;
    const bs = buckets(h, step, ["rx", "tx"], ["rx", "tx"]);
    const mb = (b) => (isNum(b) ? (b * 8) / step / 1e6 : null);
    const rx = bs.map((b) => mb(b.v.rx)), tx = bs.map((b) => mb(b.v.tx));
    let R = 0, T = 0, pk = null, pkAt = null;
    bs.forEach((b, i) => { R += b.v.rx || 0; T += b.v.tx || 0; if (isNum(rx[i]) && (pk === null || rx[i] > pk)) { pk = rx[i]; pkAt = b.ts; } });
    return { ts: bs.map((b) => b.ts), rx, tx, R, T, pk, pkAt, step };
  }
  // точки линии задержки выхода за час: [[x, y] | null], лог-шкала 10–500 мс (макет: высота 22, ширина 96)
  function xnPts(rtt, W, H) {
    const a = arr(rtt), n = a.length || 60;
    return a.map((v, i) => (isNum(v) && v > 0 ? [r1((i / Math.max(1, n - 1)) * W), r1(H - 1 - clamp(Math.log(v / 10) / Math.log(50), 0, 1) * (H - 2))] : null));
  }
  // стенд it1 Н-2: ответ выхода AmneziaWG меряется не каждую минуту — HTTP-проба раз в max(3P, 30) с под трафиком и
  // max(12P, 120) с без него (collect.probe_plan, policy.awg_rtt_period), минута без замера у живого выхода — не обрыв.
  // Пропуск внутри ряда соединяем, если в нём нет минуты «не работает» (st 3) или полной потери и он не длиннее XN_GAP
  // минут либо во все его минуты выход «работает» / «с перебоями» (st 1–2). Минута «не работает», полная потеря, пропуск
  // без строк (st 0) длиннее XN_GAP и края ряда — честный разрыв. pts — xnPts; -> те же точки без соединённых null
  const XN_GAP = 2;
  function xnJoin(pts, st, loss) {
    const P = arr(pts), S = arr(st), L = arr(loss), drop = [];
    for (let i = 0; i < P.length; i++) {
      if (P[i]) continue;
      let j = i;
      while (j < P.length && !P[j]) j++;
      let down = false, alive = true;
      for (let k = i; k < j; k++) {
        if (S[k] === 3 || (isNum(L[k]) && L[k] >= 100)) down = true;
        if (S[k] !== 1 && S[k] !== 2) alive = false;
      }
      if (i > 0 && j < P.length && !down && (j - i <= XN_GAP || alive)) for (let k = i; k < j; k++) drop[k] = true;
      i = j;
    }
    return P.filter((_p, k) => !drop[k]);
  }
  const lastNum = (a) => { const x = arr(a); for (let i = x.length - 1; i >= 0; i--) if (isNum(x[i])) return x[i]; return null; };
  // «что изменилось к прошлой проверке»: звенья по величине изменения (новая − прошлая), без пар с пропуском
  function deltaOf(c0, c1) {
    if (!isObj(c0) || !isObj(c1) || !isObj(c0.links) || !isObj(c1.links)) return [];
    return LINKS.filter((id) => isNum(c0.links[id]) && isNum(c1.links[id])).map((id) => ({ id, d: c0.links[id] - c1.links[id], v: c0.links[id], was: c1.links[id] }))
      .sort((a, b) => Math.abs(b.d) - Math.abs(a.d) || LINKS.indexOf(a.id) - LINKS.indexOf(b.id));
  }
  // «почему итог не выше» (владелец 29.10, D-143): блок — одна строка и только когда предел слабого звена держит итог
  // (lim: звено, его балл, предел и итог без предела). Итог есть, предел не держит (или слабого звена нет) — free, блока
  // нет; итога нет — incomplete | none (своя строка под числом, не «Почему итог не выше»)
  function whyOf(syn) {
    const f = isObj(syn) && isObj(syn.fresh) ? syn.fresh : null;
    if (!f || !isNum(f.total)) return { key: f && f.why === "incomplete" ? "incomplete" : "none" };
    const lk = arr(syn.links).find((l) => l && l.id === f.weak);
    const v = lk ? num(lk.fresh) : null;
    if (f.limited !== true || !isNum(v) || !isNum(f.cap)) return { key: "free" };
    return { key: "lim", id: f.weak, v, cap: f.cap, raw: f.raw };
  }
  // «предварительно» (владелец 28.10): итог — первые часы нового узла, пока «Сеть» копит пробы (решение 4: provisional
  // сервера, порог — правило кода need_h); звено — без эталонных серверов (решение 5: links[].calib false).
  // -> {have, need} | null; без итога помечать нечего (у «нет итога» своё объяснение в «Почему итог не выше»)
  function provOf(syn) {
    const p = isObj(syn) && isObj(syn.provisional) ? syn.provisional : null;
    return p && isNum(p.have_h) && isNum(p.need_h) && isObj(syn.fresh) && isNum(syn.fresh.total) ? { have: p.have_h, need: p.need_h } : null;
  }
  // -> {ok: [id], prov: [id]} в порядке радара; звено без поля calib — предварительное (так честнее)
  function calibOf(syn) {
    const ls = isObj(syn) ? arr(syn.links) : [];
    const ok = LINKS.filter((id) => ls.some((l) => l && l.id === id && l.calib === true));
    return { ok, prov: LINKS.filter((id) => ok.indexOf(id) < 0) };
  }
  // геометрия радара (lk-star.js): mode tile | drawer | diag; w — ширина; -> {cx, cy, r, H, pt(v, i)}
  const GEO = { diag: { px: 112, py: 56 }, drawer: { px: 88, py: 48 }, phone: { px: 64, py: 38 }, tile: { px: 6, py: 6 } };
  function geoOf(mode, w, n) {
    const G = GEO[mode] || GEO.tile, r = w / 2 - G.px, cx = w / 2, cy = r + G.py, H = Math.ceil(2 * r + 2 * G.py);
    const ang = (i) => (i * 360) / n;
    const pt = (v, i) => { const q = (r * clamp(isNum(v) ? v : 0, 0, 1000)) / 1000, a = (ang(i) * Math.PI) / 180; return [r1(cx + q * Math.sin(a)), r1(cy - q * Math.cos(a))]; };
    return { cx, cy, r, H, w, pt, ang };
  }
  // стенд it1 Н-1: звено без данных — не 0 (многоугольник проваливался к центру): точки только измеренных звеньев,
  // соседние измеренные соединяет хорда
  const polyOf = (g, vals) => vals.map((v, i) => (isNum(v) ? g.pt(v, i).join(",") : null)).filter(Boolean).join(" ");
  // отметка звена i без данных: где хорда между ближайшими измеренными соседями пересекает его ось; нет пары соседей
  // или хорда проходит по ту сторону центра (пропуск в полкруга и больше) — центр. -> [x, y]
  function ndPt(g, vals, i) {
    const n = vals.length;
    let a = -1, b = -1;
    for (let k = 1; k < n && a < 0; k++) if (isNum(vals[(i - k + n) % n])) a = (i - k + n) % n;
    for (let k = 1; k < n && b < 0; k++) if (isNum(vals[(i + k) % n])) b = (i + k) % n;
    if (a < 0 || b < 0 || a === b) return [r1(g.cx), r1(g.cy)];
    const A = g.pt(vals[a], a), B = g.pt(vals[b], b), an = (g.ang(i) * Math.PI) / 180;
    const dx = Math.sin(an), dy = -Math.cos(an), ex = B[0] - A[0], ey = B[1] - A[1], px = A[0] - g.cx, py = A[1] - g.cy;
    const det = ex * dy - dx * ey;
    if (Math.abs(det) < 1e-9) return [r1(g.cx), r1(g.cy)];
    const s = (ex * py - ey * px) / det, u = (dx * py - dy * px) / det;
    if (s < 0 || u < -1e-9 || u > 1 + 1e-9) return [r1(g.cx), r1(g.cy)];
    const q = Math.min(s, g.r);
    return [r1(g.cx + q * dx), r1(g.cy + q * dy)];
  }

  // ── SVG ─────────────────────────────────────────────────────────────────────────────────────────────────────────
  function svg(tag, a, kids) {
    const n = document.createElementNS(NS, tag);
    Object.keys(a || {}).forEach((k) => { if (a[k] !== null && a[k] !== undefined && a[k] !== false) n.setAttribute(k, String(a[k])); });
    (kids || []).forEach((c) => { if (c) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return n;
  }
  const svgText = (x, y, s, cls, anchor) => svg("text", { x: r1(x), y: r1(y), class: cls, "text-anchor": anchor || "middle" }, [String(s)]);

  // ── данные ──────────────────────────────────────────────────────────────────────────────────────────────────────
  const D = { syn: null, sc: null, err: null, at: 0, run: null, tr: {}, trAt: {}, trRun: {}, xh: null, xhRun: null, xhMark: undefined, v: 0 };
  const SCORE_EVERY = 60000, HIST_EVERY = 300000;
  const refresh = () => { D.v += 1; if (K.refresh) K.refresh(); };
  function wantScore(force) {
    if (D.run || (!force && D.at && Date.now() - D.at < SCORE_EVERY)) return;
    D.run = Promise.all([K.API.host.synth(), K.API.host.score()]).then((x) => { D.syn = isObj(x[0]) ? x[0] : null; D.sc = isObj(x[1]) ? x[1] : null; D.err = null; },
      (e) => { D.err = e; }).then(() => { D.run = null; D.at = Date.now(); refresh(); });
  }
  function wantHist(rng) {
    if (D.trRun[rng] || (D.trAt[rng] && Date.now() - D.trAt[rng] < HIST_EVERY)) return;
    D.trRun[rng] = K.API.host.history(rng).then((j) => { D.tr[rng] = isObj(j) ? j : null; }, () => { /* прежний ответ остаётся */ })
      .then(() => { D.trRun[rng] = null; D.trAt[rng] = Date.now(); refresh(); });
  }
  // выходы за час: перечитать, когда сборщик записал новую минуту (live.json → metrics.ts), иначе раз в минуту
  function wantHour() {
    const m = K.liveMark ? K.liveMark("metrics") : null, mark = m && isNum(m.ts) ? m.ts : Math.floor(Date.now() / 60000);
    if (D.xhRun || mark === D.xhMark) return;
    D.xhMark = mark;
    D.xhRun = K.API.exits.hour().then((j) => { D.xh = isObj(j) && j.charts && isObj(j.exits) ? j : D.xh && j === undefined ? D.xh : null; }, () => { /* прежний */ })
      .then(() => { D.xhRun = null; refresh(); });
  }
  // пороги правил здоровья (warn_at / crit_at / down): их отдаёт GET /api/host/health; у живого потока (live.json) их
  // нет — правила постоянные, спрашиваем раз в 10 минут, пока открыта часть со здоровьем
  const LIM = { data: null, at: 0, run: null };
  function wantLimits() {
    if (LIM.run || (LIM.at && Date.now() - LIM.at < 600000)) return;
    LIM.run = K.API.host.health().then((j) => {
      const c = isObj(j) && isObj(j.health) && isObj(j.health.checks) ? j.health.checks : null;
      if (!c) return;
      const out = {};
      Object.keys(c).forEach((k) => { const x = c[k]; if (isObj(x) && isNum(x.warn_at)) out[k] = { warn_at: x.warn_at, crit_at: num(x.crit_at), down: x.down === true }; });
      LIM.data = out;
    }, () => { /* без пунктира */ }).then(() => { LIM.run = null; LIM.at = Date.now(); refresh(); });
  }
  const linkOf = (id) => (D.syn ? arr(D.syn.links).find((l) => l && l.id === id) : null) || null;
  const provL = (l) => !!l && l.calib !== true;            // звено без эталонов (решение 5): значение с «≈»
  // подпись под большим радаром: «≈ — предварительно», если на нём есть такое значение
  const provCap = () => (LINKS.some((id) => { const l = linkOf(id); return provL(l) && isNum(l.fresh); }) ? el("p", { class: "kb-st__cap", text: t("ovb.provCap") }) : null);
  // пометка итога «предварительно» (решение 4) и её объяснение с часами проб
  const gapOf = (syn) => String(isObj(syn) && isNum(syn.weak_gap) ? syn.weak_gap : 30);       // % слабого звена (D-143)
  const provTxt = (p) => t("ovb.provX", { have: K.fmtInt ? K.fmtInt(p.have) : String(p.have), need: K.fmtInt ? K.fmtInt(p.need) : String(p.need) });
  const provChip = (syn) => { const p = provOf(syn); return p ? el("span", { class: "gx-need", title: provTxt(p), text: t("ovb.prov") }) : null; };
  const lname = (id, short) => (short && ["stab", "loss", "crypto"].indexOf(id) >= 0 ? t("ovb.lns." + id) : t("ovb.ln." + id));
  const capFirst = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  // подпись строки списка («Что изменилось к прошлой проверке»): с заглавной у всех звеньев — у групп («Канал») она и
  // так есть, у замеров («скачивание») — нет, и список шёл в разном регистре (М-3 стенда 3.3.7.13)
  const dlName = (id) => capFirst(lname(id));
  const bandWord = (v) => (bandOf(v) >= 0 ? t("ovb.band." + bandOf(v)) : "");
  const L = () => window.KaskadLang || null;
  function whenOf(ts) {
    if (!isNum(ts) || ts <= 0) return "";
    const d = new Date(ts * 1000), now = new Date(), y = new Date(now.getTime() - 86400000), lg = L();
    const hm = lg ? lg.time(d) : String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    if (d.toDateString() === now.toDateString()) return t("ovb.today", { t: hm });
    if (d.toDateString() === y.toDateString()) return t("ovb.yesterday", { t: hm });
    return lg ? lg.dateTime(d) : d.toLocaleString();
  }
  const hmOf = (ts) => { const d = new Date(ts * 1000), lg = L(); return lg ? lg.time(d) : String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  const dayShort = (day) => { const p = String(day).split("-"); const d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2])); return new Intl.DateTimeFormat(K.lang === "en" ? "en" : "ru", { day: "numeric", month: "short" }).format(d); };
  const fmtN = (v, dg) => (isNum(v) ? new Intl.NumberFormat(K.lang === "en" ? "en" : "ru", { minimumFractionDigits: dg || 0, maximumFractionDigits: dg || 0 }).format(v) : DASH);
  const pct = (v, dg) => (isNum(v) ? (dg === 0 ? K.fmtInt(Math.round(v)) + "%" : K.fmtPct(v)) : DASH);   // как у ядра (fmtPct)
  const mbit = (v) => (isNum(v) ? fmtN(v, v < 10 ? 1 : 0) + NB + t("ovb.mbit") : DASH);
  const gb = (b) => K.fmtBytes(b);

  // звено без балла — короткая причина сервера (hostscore.why_missing: why у звена /api/host/synth, Л3 №8) для подсказки
  // сектора и aria-label радара; подпись оси остаётся «нет данных». Код без своего слова — пусто (говорим «нет данных»)
  const NDW = { probes: "ovb.ndw.probes", never: "ovb.ndw.never", not_run: "ovb.ndw.never", failed: "ovb.ndw.failed", load: "ovb.ndw.load",
    stale: "ovb.ndw.stale", no_upload_ru: "ovb.ndw.inRu", excluded: "ovb.ndw.off", no_target: "ovb.ndw.nopt", no_iperf3: "ovb.ndw.nopt", upload_off: "ovb.ndw.upOff",
    young: "ovb.ndw.young" };
  const ndWhy = (l) => (l && typeof l.why === "string" && Object.prototype.hasOwnProperty.call(NDW, l.why) ? t(NDW[l.why]) : "");

  // ── радар «Балл сервера» ────────────────────────────────────────────────────────────────────────────────────────
  // o: {mode: tile | drawer | diag | phone, w, labels: bool, hits: bool, layers: {retro, now}}
  function star(o) {
    const syn = D.syn, n = LINKS.length, g = geoOf(o.mode, o.w, n), big = o.mode !== "tile";
    const ls = LINKS.map(linkOf);
    const fresh = ls.map((l) => (l ? num(l.fresh) : null)), usual = ls.map((l) => (l ? num(l.usual) : null));
    const lo = ls.map((l) => (l ? num(l.lo) : null)), hi = ls.map((l) => (l ? num(l.hi) : null));
    const kids = [], has = fresh.some(isNum), nd = (i) => has && !isNum(fresh[i]);     // nd — звено без данных (Н-1)
    LINKS.forEach((_id, i) => { const p = g.pt(1000, i); kids.push(svg("line", { class: "kb-st__ax" + (nd(i) ? " kb-st__ax--nd" : ""), x1: g.cx, y1: g.cy, x2: p[0], y2: p[1] })); });
    (big ? [100, 500, 700, 900, 1000] : [500, 700, 900, 1000]).forEach((v) => kids.push(svg("circle", { class: "kb-st__ring" + (v === 1000 ? " kb-st__ring--edge" : ""), cx: g.cx, cy: g.cy, r: r1((g.r * v) / 1000) })));
    if (o.mode === "diag" || o.mode === "drawer") {
      const half = 180 / n;
      [100, 500, 700, 900].forEach((v) => { const a = (half * Math.PI) / 180, q = (g.r * v) / 1000; kids.push(svgText(g.cx + q * Math.sin(a) + 3, g.cy - q * Math.cos(a) - 2, v, "kb-st__rl", "start")); });
      const a = (half * Math.PI) / 180;
      kids.push(svgText(g.cx + g.r * Math.sin(a) + 4, g.cy - g.r * Math.cos(a) - 5, t("ovb.unreach"), "kb-st__rl", "start"));
    }
    // полоса «обычно» — по звеньям, где есть и низ, и верх (звено без истории её не обнуляет и не прячет целиком, Н-1)
    const both = lo.map((v, i) => isNum(v) && isNum(hi[i])), blo = lo.map((v, i) => (both[i] ? v : null)), bhi = hi.map((v, i) => (both[i] ? v : null));
    if (both.filter(Boolean).length >= 3) {
      kids.push(svg("path", { class: "kb-st__band", d: "M" + polyOf(g, bhi).split(" ").join("L") + "Z M" + polyOf(g, blo).split(" ").join("L") + "Z" }));
      kids.push(svg("polygon", { class: "kb-st__edge", points: polyOf(g, blo) }), svg("polygon", { class: "kb-st__edge", points: polyOf(g, bhi) }));
    }
    if (usual.some(isNum)) kids.push(svg("polygon", { class: "kb-st__med", points: polyOf(g, usual) }));
    const weak = syn && syn.fresh ? LINKS.indexOf(syn.fresh.weak) : -1;
    if (has) {
      kids.push(svg("polygon", { class: "kb-st__now", points: polyOf(g, fresh) }));
      fresh.forEach((_v, i) => { if (!nd(i)) return; const p = ndPt(g, fresh, i); kids.push(svg("circle", { class: "kb-st__nd", cx: p[0], cy: p[1], r: big ? 3.5 : 2.5 })); });
      fresh.forEach((v, i) => { if (!isNum(v)) return; const p = g.pt(v, i); kids.push(svg("circle", { class: "kb-st__dot" + (i === weak ? " kb-st__dot--weak" : ""), cx: p[0], cy: p[1], r: big ? (i === weak ? 5 : 3.5) : i === weak ? 3.5 : 2.5 })); });
    } else if (big) kids.push(svgText(g.cx, g.cy + 4, t("ovb.noChecks"), "kb-st__empty"));
    if (big && o.labels !== false) {
      LINKS.forEach((id, i) => {
        const l = ls[i], p = g.pt(1000, i), a = (g.ang(i) * Math.PI) / 180, sn = Math.sin(a), cs = Math.cos(a);
        const dev = l && (l.dev === "down" || l.dev === "up") ? l.dev : null, wk = i === weak && isNum(fresh[i]);
        // слабое звено (D-143): подпись тем же предупреждающим цветом, что «хуже обычного», и строка «слабое звено»; на
        // телефоне у верхней и нижней оси третьей строке нет места по высоте — слово идёт в строку значения
        const join = wk && o.mode === "phone" && Math.abs(sn) < 0.2;
        const lines = [lname(id, o.mode === "phone")].concat(isNum(fresh[i]) ? [(provL(l) ? "≈" : "") + String(fresh[i]) + (dev ? " " + (dev === "down" ? "▼" : "▲") : "") + (join ? " · " + t("ovb.weak") : "")] : nd(i) ? [t("ovb.nd")] : [])
          .concat(wk && !join ? [t("ovb.weak")] : []);
        const lh = o.mode === "phone" ? 13 : 14, k = lines.length;
        let an, x, y0;
        if (Math.abs(sn) < 0.2) { an = "middle"; x = p[0]; y0 = cs > 0 ? p[1] - 10 - (k - 1) * lh : p[1] + 18; }
        else { an = sn > 0 ? "start" : "end"; x = p[0] + sn * 9; y0 = cs > 0.2 ? p[1] - 2 - (k - 1) * lh : cs < -0.2 ? p[1] + 13 : p[1] - ((k - 1) * lh) / 2 + 4; }
        lines.forEach((s, j) => kids.push(svgText(x, y0 + j * lh, s, "kb-st__lab" + (wk && j === 2 ? "" : j ? (nd(i) ? " kb-st__lab--nd" : " kb-st__lab--v") : "")
          + (dev === "down" || wk ? " kb-st__lab--warn" : ""), an)));
      });
    }
    if (big && o.hits) {
      const half = 180 / n, R2 = g.r * 1.18;
      LINKS.forEach((id, i) => {
        const a1 = ((g.ang(i) - half) * Math.PI) / 180, a2 = ((g.ang(i) + half) * Math.PI) / 180, l = ls[i];
        const tip = !l || !isNum(l.fresh) ? (ndWhy(l) ? capFirst(lname(id)) + ": " + ndWhy(l) : t("ovb.tipNo", { name: capFirst(lname(id)) }))
          : t("ovb.tip", { name: capFirst(lname(id)), v: String(l.fresh), u: isNum(l.usual) ? String(l.usual) : DASH }) + (l.dev === "down" ? " · " + t("ovb.tipDn") : l.dev === "up" ? " · " + t("ovb.tipUp") : "")
            + (provL(l) ? " · " + t("ovb.prov") : "") + (i === weak ? " · " + t("ovb.weakTip", { gap: gapOf(syn) }) : "");
        kids.push(svg("path", { class: "kb-st__hit", d: "M" + g.cx + " " + g.cy + "L" + r1(g.cx + R2 * Math.sin(a1)) + " " + r1(g.cy - R2 * Math.cos(a1)) + "A" + r1(R2) + " " + r1(R2) + " 0 0 1 " + r1(g.cx + R2 * Math.sin(a2)) + " " + r1(g.cy - R2 * Math.cos(a2)) + "Z" }, [svg("title", {}, [tip])]));
      });
    }
    // «· отдача: в России не меряется, потери: нет данных» — у каждого звена без балла своя причина
    const miss = LINKS.map((id, i) => (nd(i) ? lname(id) + ": " + (ndWhy(ls[i]) || t("ovb.nd")) : null)).filter(Boolean);
    const label = t("ovb.score") + (syn && syn.fresh && isNum(syn.fresh.total) ? ": " + syn.fresh.total + " " + bandWord(syn.fresh.total) : "")
      + (weak >= 0 && has && isNum(fresh[weak]) ? " · " + t("ovb.weak") + ": " + lname(LINKS[weak]) + " " + fresh[weak] : "")
      + (miss.length ? " · " + miss.join(", ") : "");
    const s = svg("svg", { width: g.w, height: g.H, viewBox: "0 0 " + g.w + " " + g.H, role: "img", "aria-label": label }, kids);
    const box = el("div", { class: "kb-st kb-st--" + o.mode });
    if (o.layers) { box.classList.toggle("is-noretro", o.layers.retro === false); box.classList.toggle("is-nonow", o.layers.now === false); }
    box.append(s);
    return box;
  }

  // ── Сводка ──────────────────────────────────────────────────────────────────────────────────────────────────────
  // строки ядра (ovIssues): сбои и то, что ждёт действия, — у каждой своё действие. Замечаний балла («… хуже обычного»)
  // и здоровья здесь нет (владелец 28.10): ▲▼ — у звеньев на «Проверка сервера», здоровье — плиткой и на своём экране
  function remarksOf(ctx) {
    // glyph — свой значок строки вместо значка тона: «ждёт новых настроек» — ключ, как у пометки (№13 r2 О1, Д1)
    const rows = arr(ctx.issues ? ctx.issues() : []).map((r) => ({ key: r.key, tone: r.tone === "danger" ? "danger" : r.tone === "accent" ? "accent" : "warning", title: r.n > 1 ? r.title + " ×" + r.n : r.title,
      glyph: typeof r.glyph === "string" ? r.glyph : "", sub: r.sub || "", btn: r.act ? { label: r.act.label, on: r.act.on, primary: !!r.act.primary } : r.go ? { label: r.link || t("ovb.goOpen"), on: r.go } : null }));
    return rows;
  }
  function openScoreFrom() { openScore(); }
  function hero(ctx) {
    return K.blk(() => {
      wantScore(false);
      const hv = ctx.health ? ctx.health() : null;
      const rows = remarksOf(ctx);
      return { v: D.v, rows: rows.map((r) => [r.key, r.tone, r.title, r.sub]), lv: hv && hv.view ? hv.view.level : "wait", up: hv && hv.host ? num(hv.host.uptime) : null, seen: ctx.seen ? ctx.seen() : "", ph: phone() };
    }, (v) => {
      const rows = remarksOf(ctx), vd = verdictOf(rows, v.lv), syn = D.syn, f = syn && syn.fresh ? syn.fresh : null;
      const title = t("ovb.v." + vd.key);
      const starBtn = el("button", { type: "button", class: "kb-hero__star", "aria-haspopup": "dialog", "aria-label": t("ovb.scoreOpen"), onclick: openScoreFrom }, star({ mode: "tile", w: v.ph ? 112 : 200 }));
      const sc = el("button", { type: "button", class: "kb-hero__sc", "aria-haspopup": "dialog", onclick: openScoreFrom }, t("ovb.scoreChip") + " ", el("b", { text: f && isNum(f.total) ? String(f.total) : DASH }), " " + (f && isNum(f.total) ? bandWord(f.total) : t("ovb.scoreNone")));
      const meta = el("div", { class: "kb-hero__m" }, sc, provChip(syn),
        el("span", { text: syn && isNum(syn.last_at) ? t("ovb.checkedAt", { when: whenOf(syn.last_at) }) : t("ovb.noChecks") }),
        isNum(v.up) ? el("span", { text: t("ovb.uptime", { d: K.fmtDur(v.up) }) }) : null);
      const iss = rows.length ? el("div", { class: "kb-iss" }, rows.map((r) => el("div", { class: "kb-iss__r" + (r.tone === "danger" ? " is-danger" : r.tone === "accent" ? " is-accent" : ""), "data-ovs": r.key },
        K.icon(r.glyph || (r.tone === "danger" ? "circle-exclamation" : r.tone === "accent" ? "play" : "triangle-exclamation"), 20),
        el("span", { class: "kb-iss__b" }, el("b", { text: r.title }), r.sub ? el("span", { text: r.sub }) : null),
        r.btn ? U.btn(r.btn.label, r.btn.primary ? "primary" : "tertiary", r.btn.on) : null))) : null;
      const dd = d30Of(D.sc ? D.sc.days : null);
      // на телефоне день не дорастёт до цели касания 44 px: вся полоса — одна кнопка в «Историю», дни — просто клетки
      const strip = !dd.list.length ? null : v.ph ? el("button", { type: "button", class: "kb-30 kb-30--go", "aria-label": t("ovb.d30go", { sum: t("ovb.d30aria", { ok: dd.cnt.green, warn: dd.cnt.yellow, fail: dd.cnt.red, none: dd.cnt.grey }) }), onclick: () => K.goNav("overview", { tab: "history" }) },
        el("span", { class: "kb-30__row", "aria-hidden": "true" }, dd.list.map((d) => el("i", { class: DAY_CLS[d.color] !== undefined ? DAY_CLS[d.color] || null : "is-none" }))),
        el("span", { class: "kb-30__h" }, el("b", { text: t("ovb.d30") }), el("span", { text: t("ovb.d30sum", { ok: dd.cnt.green, warn: dd.cnt.yellow, fail: dd.cnt.red, none: dd.cnt.grey }) }))) : el("div", { class: "kb-30" },
        el("div", { class: "kb-30__row", role: "group", "aria-label": t("ovb.d30aria", { ok: dd.cnt.green, warn: dd.cnt.yellow, fail: dd.cnt.red, none: dd.cnt.grey }) },
          dd.list.map((d) => el("button", { type: "button", class: DAY_CLS[d.color] !== undefined ? DAY_CLS[d.color] || null : "is-none", title: t("ovb.d30day", { day: dayShort(d.day), what: t("ovb.dc." + (DAY_CLS[d.color] !== undefined ? d.color : "grey")) }),
            "aria-label": t("ovb.d30day", { day: dayShort(d.day), what: t("ovb.dc." + (DAY_CLS[d.color] !== undefined ? d.color : "grey")) }), onclick: () => K.goNav("overview", { tab: "history", focus: d.day }) }))),
        el("div", { class: "kb-30__h" }, el("b", { text: t("ovb.d30") }), el("span", { text: t("ovb.d30sum", { ok: dd.cnt.green, warn: dd.cnt.yellow, fail: dd.cnt.red, none: dd.cnt.grey }) })));
      const dot = el("i", { class: vd.tone === "crit" ? "is-crit" : vd.tone === "warn" ? "is-warn" : vd.tone === "ok" ? "" : "is-none" });
      return el("section", { class: "gx-card kb-hero", "aria-label": t("ovb.now"), "data-find": "overview.sum" }, starBtn,
        el("div", { class: "kb-hero__v" }, el("span", { class: "kb-hero__k" }, dot, t("ovb.now") + (v.seen ? " · " + t("ovb.updated", { t: v.seen }) : "")),
          el("h2", { class: "kb-hero__t", text: title }), meta), iss, strip);
    }, { cls: "kb-hero-wrap" });
  }
  // трафик: график первым, три цифры под ним. Сетка (Claude Design «Сетка на графиках r1», HANDOFF «Трафик сервера»):
  // шкала 1-2-5 с нулём (пик 48 → 0 · 20 · 40 · 60), «Мбит/с» один раз над колонкой чисел, вертикальные линии — отметки
  // (сутки — каждые 4 ч, телефон — 6; 7 дней — полночь), правый край — «сейчас»; заливка приёма — под горизонтальными
  // линиями, линии приёма и отдачи — над ними. Поле 150, телефон 120
  const TR = { week: false };
  function traffic() {
    return K.blk(() => { const rng = TR.week ? "7d" : "1d"; wantHist(rng); if (!TR.week) wantHist("1d"); return { v: D.v, w: TR.week }; }, (v) => {
      const h = D.tr[v.w ? "7d" : "1d"];
      const seg = U.seg([{ label: t("ovb.trDay"), on: !v.w, pick: () => { TR.week = false; refresh(); } }, { label: t("ovb.trWeek"), on: v.w, pick: () => { TR.week = true; refresh(); } }], { aria: t("ovb.trPer") });
      seg.classList.add("gx-seg--sm");
      const head = el("div", { class: "kb-h" }, el("h2", { class: "kb-t", text: t("ovb.trT") }), el("span", { class: "kb-sp" }), seg);
      if (!h) return el("section", { class: "gx-card", "aria-label": t("ovb.trT") }, head, U.skel(3));
      const tr = h.charts && h.host ? trafficOf(h.host, v.w) : null;
      if (!tr || tr.ts.length < 2) return el("section", { class: "gx-card", "aria-label": t("ovb.trT") }, head, el("p", { class: "kb-empty", text: t("ovb.trNone") }));
      const G = window.KaskadSpark.grid, ph = phone(), kind = v.w ? "d" : "h";
      const N = tr.ts.length, mx = Math.max(0.1, ...tr.rx.filter(isNum), ...tr.tx.filter(isNum)), sc = G.scale(mx, ph ? 2 : 3);
      const pts = (a, W, y) => segs(a.map((x, i) => (isNum(x) ? [r1((i / (N - 1)) * W), r1(y(x))] : null)));
      const ax = G.ticks(tr.ts[0], tr.ts[N - 1], kind, ph ? 6 : 4).map((x) => ({ at: x.at, t: G.lab(kind, x.d, K.lang, ph) }));
      const per = t(v.w ? "ovb.trForWeek" : "ovb.trForDay");
      const plot = G.box({ sm: ph, unit: t("ovb.mbit"), ys: G.labels(sc, K.lang === "en" ? "." : ","), sc, ax, end: t("ovb.trNow"),
        under: (s, W, H, y) => { const b = r1(y(0)), d = pts(tr.rx, W, y).map((g) => mono(g) + "L" + g[g.length - 1][0] + " " + b + "L" + g[0][0] + " " + b + "Z").join(""); if (d) s.appendChild(svg("path", { class: "gx-trf__a", d })); },
        paint: (s, W, H, y) => { [["gx-trf__tx", tr.tx], ["gx-trf__rx", tr.rx]].forEach(([cls, a]) => { const d = pts(a, W, y).map(mono).join(""); if (d) s.appendChild(svg("path", { class: cls, d })); }); } });
      plot.setAttribute("role", "img");
      plot.setAttribute("aria-label", t("ovb.trAria", { per, rx: gb(tr.R), tx: gb(tr.T) }));
      const pk = isNum(tr.pk) ? (v.w ? t("ovb.trPeakW", { v: mbit(tr.pk) }) : t("ovb.trPeak", { v: mbit(tr.pk), t: hmOf(tr.pkAt) })) : "";
      const c = (k, b, s, cls) => el("div", { class: "kb-trf__c" }, el("span", {}, cls !== undefined ? el("i", { class: cls }) : null, t(k)), el("b", { text: b }), el("small", { text: s }));
      return el("section", { class: "gx-card", "aria-label": t("ovb.trT"), "data-find": "overview.traffic" }, head, plot,
        el("div", { class: "kb-trf__sum" }, c("ovb.trAll", gb(tr.R + tr.T), per), c("ovb.trRx", gb(tr.R), pk, ""), c("ovb.trTx", gb(tr.T), t("ovb.trTxS"), "is-tx")));
    });
  }
  // выходы за час: имя и состояние → линия задержки за час → цифра «сейчас»
  // слово состояния выхода — от ядра (exitWord: те же слова, что в «Выходах»), здесь только цвет точки по тону
  const XS_CLS = { success: "gx-mst--ok", warning: "gx-mst--warn", danger: "gx-mst--bad", off: "gx-mst--off" };
  const XN_MAX = 6;
  function exitsHour(ctx) {
    return K.blk(() => {
      wantHour();
      const xs = arr(ctx.exits ? ctx.exits() : []);
      return { v: D.v, xs: xs.map((x) => [x.id, x.name, x.word, x.tone, !!x.ru]) };
    }, () => {
      const xs = arr(ctx.exits ? ctx.exits() : []);
      const all = U.btn(t("ovb.xnAll"), "link", () => K.goNav("exits", { tab: "list" }));
      const head = el("div", { class: "kb-h" }, el("h2", { class: "kb-t", text: t("ovb.xnT") }), el("span", { class: "kb-sp" }), xs.length ? all : null);
      if (!xs.length) return el("section", { class: "gx-card", "aria-label": t("ovb.xnT") }, head, el("p", { class: "kb-empty", text: t("ovb.xnNone") }));
      const j = D.xh;
      // российские — после зарубежных; больше XN_MAX — последняя строка отдаётся первому российскому (решение 6)
      const ru1 = xs.find((x) => x.ru), shown = ru1 && xs.indexOf(ru1) >= XN_MAX ? xs.slice(0, XN_MAX - 1).concat(ru1) : xs.slice(0, XN_MAX);
      const rows = shown.map((x) => {
        const e = j && Object.prototype.hasOwnProperty.call(j.exits, x.id) && isObj(j.exits[x.id]) ? j.exits[x.id] : null;
        const off = x.tone === "off";
        const pts = e && !off ? xnJoin(xnPts(e.rtt, 96, 22), e.st, e.loss) : [];
        const pL = segs(pts).map(mono).join(""), now = e && !off ? lastNum(e.rtt) : null;
        const vtxt = isNum(now) ? fmtN(now, 0) + NB + t("ovb.ms") : DASH;
        const stw = String(x.word || DASH);
        const b = el("button", { type: "button", class: "kb-xn__r", title: isNum(now) ? t("ovb.xnTip", { name: x.name, st: stw, v: vtxt }) : t("ovb.xnTipNo", { name: x.name, st: stw }), onclick: () => (ctx.openExit ? ctx.openExit(x.id) : null) },
          el("span", { class: "kb-xn__b" }, el("span", null, x.name, x.ru ? K.domMark(12, "kb-xn__rf", { tip: t("ovb.xnRfT") }) : null),
            el("span", { class: "gx-mst " + (XS_CLS[x.tone] || "gx-mst--none"), text: stw })),
          el("span", { class: "gx-spark", "aria-hidden": "true" }, svg("svg", { viewBox: "0 0 96 22", preserveAspectRatio: "none" },
            [svg("path", { class: "gx-spark__nd", d: !pL ? "M0 21H96" : "M0 0" }), svg("path", { class: "gx-spark__l", d: pL || "M0 0" })])),
          el("span", { class: "kb-xn__v", text: vtxt }));
        return b;
      });
      return el("section", { class: "gx-card", "aria-label": t("ovb.xnT"), "data-find": "overview.exitsHour" }, head, el("div", { class: "kb-xn" }, rows),
        el("span", { class: "kb-mut", text: j ? t("ovb.xnNote") : t("ovb.xnNoData") }));
    });
  }

  // ── здоровье: плитки Сводки и экран ─────────────────────────────────────────────────────────────────────────────
  // метрики: проверка /api/host/health (checks[rid]), столбец ряда host/history, верх шкалы
  const MET = [{ id: "cpu", chk: "cpu", col: "busy", max: 100 }, { id: "mem", chk: "mem", col: "mem_avail", max: 100 },
    { id: "disk", chk: "disk", col: "disk", max: 100 }, { id: "net", chk: "retrans", col: "retrans", max: 4 }];
  // ряд метрики за сутки: 144 ведра по 10 мин (сеть — повторы в % от отправленных сегментов)
  function metSeries(h, m) {
    if (!isObj(h) || !h.charts || !isObj(h.host)) return [];
    if (m.id === "net") return buckets(h.host, 600, ["retrans", "out_segs"], ["retrans", "out_segs"]).map((b) => ({ ts: b.ts, v: isNum(b.v.retrans) && b.v.out_segs > 0 ? (100 * b.v.retrans) / b.v.out_segs : null }));
    return buckets(h.host, 600, [m.col]).map((b) => ({ ts: b.ts, v: b.v[m.col] }));
  }
  const top1 = (v) => { const G = window.KaskadSpark && window.KaskadSpark.grid; return G ? G.scale(v, 1, { one: true }).top : v; };
  const LV_OF = (c) => { const x = isObj(c) ? c.shown || c.level : null; return x === "ok" || x === "warn" || x === "crit" ? x : "wait"; };
  function metNow(m, chk, host, ser) {
    const v = isObj(chk) && isNum(chk.value) ? chk.value : null;
    if (m.id === "cpu" && host && isNum(host.cpu)) return host.cpu;
    if (m.id === "mem" && host && isNum(host.mem_used) && isNum(host.mem_total) && host.mem_total > 0 && !isNum(v)) return 100 - (100 * host.mem_used) / host.mem_total;
    return isNum(v) ? v : lastNum(ser.map((p) => p.v));
  }
  function metSub(m, checks, ser) {
    const c = isObj(checks) ? checks : {};
    if (m.id === "cpu") { let pk = null, at = null; ser.forEach((p) => { if (isNum(p.v) && (pk === null || p.v > pk)) { pk = p.v; at = p.ts; } }); return isNum(pk) ? t("ovb.s.cpuPeak", { v: pct(pk, 0), t: hmOf(at) }) : t("ovb.s.cpu"); }
    if (m.id === "mem") { const sw = isObj(c.swap) ? num(c.swap.value) : null; return isNum(sw) ? (sw > 0.5 ? t("ovb.s.memSwap", { v: pct(sw, 0) }) : t("ovb.s.memNoSwap")) : t("ovb.s.mem"); }
    if (m.id === "disk") { const h = isObj(c.disk_eta) ? num(c.disk_eta.value) : null; return isNum(h) ? t("ovb.s.diskEta", { d: K.fmtDur(h * 3600) }) : t("ovb.s.disk"); }
    const dr = isObj(c.drops) ? num(c.drops.value) : null;
    return isNum(dr) ? (dr > 0 ? t("ovb.s.netDrops", { v: pct(dr, 2) }) : t("ovb.s.netNoDrops")) : t("ovb.s.net");
  }
  // путь ряда в коробке W × H (верх шкалы max), порог — пунктиром
  function metPaths(ser, max, W, H, th) {
    const n = ser.length;
    const Y = (x) => r1(H - (clamp(x, 0, max) / max) * (H - 2));
    const pts = ser.map((p, i) => (isNum(p.v) ? [r1((i / Math.max(1, n - 1)) * W), Y(p.v)] : null));
    const ss = segs(pts);
    return { line: ss.map(mono).join(""), area: ss.map((s) => mono(s) + "L" + s[s.length - 1][0] + " " + H + "L" + s[0][0] + " " + H + "Z").join(""), th: isNum(th) ? "M0 " + Y(th) + "H" + W : "", last: pts.filter(Boolean).slice(-1)[0] || null };
  }
  function metView(m, hv, h) {
    const checks = hv && hv.raw && isObj(hv.raw.checks) ? hv.raw.checks : {};
    const chk = checks[m.chk], ser = metSeries(h, m);
    const lim = isObj(chk) && isNum(chk.warn_at) ? chk : LIM.data && LIM.data[m.chk] ? LIM.data[m.chk] : null;
    const th = lim ? lim.warn_at : null;
    // верх — правилом сетки (Claude Design «Сетка на графиках r1»: подпись верха и «0» — 1-2-5, верх ≥ максимума); сетки нет
    const max = top1(m.id === "net" ? Math.max(m.max, isNum(th) ? th * 2 : 0, ...ser.map((p) => (isNum(p.v) ? Math.ceil(p.v) : 0))) : m.max);
    const lv = LV_OF(chk), now = metNow(m, chk, hv ? hv.host : null, ser);
    return { m, chk, ser, max, lv, now, th, down: !!lim && lim.down === true, sub: metSub(m, checks, ser) };
  }
  function healthTiles(ctx) {
    return K.blk(() => { wantHist("1d"); wantLimits(); const hv = ctx.health ? ctx.health() : null; return { v: D.v, lv: hv && hv.view ? hv.view.level : "wait", raw: hv && hv.raw ? JSON.stringify(hv.raw.checks || {}) : "", cpu: hv && hv.host ? hv.host.cpu : null }; }, (v) => {
      const hv = ctx.health ? ctx.health() : null, h = D.tr["1d"];
      const lvw = ["ok", "warn", "crit"].indexOf(v.lv) >= 0 ? v.lv : "wait";
      const chip = U.chip(t("ovb.lv." + lvw), lvw === "ok" ? "success" : lvw === "warn" ? "warning" : lvw === "crit" ? "danger" : "", { nodot: lvw === "wait" });
      const more = U.btn(t("ovb.hlMore"), "link", () => K.goNav("overview", { tab: "health" }));
      const tiles = MET.map((m) => {
        const x = metView(m, hv, h), p = metPaths(x.ser, x.max, 143, 34, x.th);
        return el("button", { type: "button", class: "kb-hl__c" + (x.lv === "warn" ? " is-warn" : x.lv === "crit" ? " is-crit" : ""), onclick: () => K.goNav("overview", { tab: "health" }) },
          el("span", { class: "kb-hl__h" }, el("span", { text: t("ovb.m." + m.id) }), el("span", { class: "gx-mst " + (x.lv === "ok" ? "gx-mst--ok" : x.lv === "warn" ? "gx-mst--warn" : x.lv === "crit" ? "gx-mst--bad" : "gx-mst--none"), text: t("ovb.lv." + x.lv) })),
          svg("svg", { class: "kb-hl__svg", viewBox: "0 0 143 34", preserveAspectRatio: "none", role: "img", "aria-label": t("ovb.hlAria", { name: t("ovb.m." + m.id), v: pct(x.now, m.id === "net" ? 2 : 0) }) },
            [svg("path", { class: "kk-hh__a", d: p.area || "M0 0" }), svg("path", { class: "kk-hh__th", d: p.th || "M0 0" }), svg("path", { class: "kk-hh__l", d: p.line || "M0 0" })]),
          el("span", { class: "kb-hl__v" }, el("b", { text: pct(x.now, m.id === "net" ? 1 : 0) }), el("span", { text: x.sub })));
      });
      return el("section", { class: "gx-card", "aria-label": t("ovb.hlT"), "data-find": "overview.health" },
        el("div", { class: "kb-h" }, el("h2", { class: "kb-t", text: t("ovb.hlT") }), chip, el("span", { class: "kb-sp" }), more),
        el("div", { class: "kb-hl" }, tiles), el("span", { class: "kb-mut", text: t("ovb.hlNote") }));
    });
  }
  function summary(box, ctx) {
    box.classList.add("kb");
    box.replaceChildren(hero(ctx),
      el("div", { class: "kb-row" }, traffic(), exitsHour(ctx)),
      el("div", { class: "kb-row" }, healthTiles(ctx), ctx.events ? ctx.events() : null));
  }
  // экран «Здоровье сервера» (Д9 / Т4 варианта А в стиле Б)
  const OK_TEXT = { cpu: "ovb.hh.okCpu", mem: "ovb.hh.okMem", disk: "ovb.hh.okDisk", net: "ovb.hh.okNet" };
  const TOP_OF = { cpu: ["cpu", "load", "steal", "psi_cpu", "iowait"], mem: ["mem", "swap", "oom", "oom_svc", "psi_mem"], disk: ["disk", "disk_eta", "inodes"], net: ["retrans", "drops", "nic_err", "softnet", "port", "listen"] };
  function health(box, ctx) {
    box.classList.add("kk-hh");
    box.replaceChildren(K.blk(() => { wantHist("1d"); wantLimits(); const hv = ctx.health ? ctx.health() : null; return { v: D.v, raw: hv && hv.raw ? JSON.stringify(hv.raw) : "", host: hv && hv.host ? JSON.stringify(hv.host) : "", ver: ctx.version ? ctx.version() : "" }; }, (v) => {
      const hv = ctx.health ? ctx.health() : null, h = D.tr["1d"], vw = hv ? hv.view : null;
      // владелец 28.10 «замечания не пишем»: состояние словами, без числа замечаний
      const lv = vw ? vw.level : "wait";
      const head = t("ovb.hh." + (lv === "ok" || lv === "warn" || lv === "crit" || lv === "off" ? lv : "wait"));
      const al = el("div", { class: "kk-hh__al" + (lv === "warn" ? " is-warn" : lv === "crit" ? " is-crit" : ""), role: "status", "data-find": "overview.health" },
        el("div", { class: "kk-hh__st" }, el("b", { text: head }), vw && isNum(vw.since) && lv !== "wait" && lv !== "off" ? el("span", { text: t("ovb.hh.since", { t: K.fmtTs(vw.since) }) }) : null),
        vw && vw.noHistory ? el("span", { class: "kk-hh__note", text: t("ovb.hh.instant") }) : null);
      const tops = vw ? arr(hv.raw && hv.raw.top) : [];
      const hd = el("div", { class: "kk-hh__hd", "aria-hidden": "true" }, el("span", { text: t("ovb.hh.colM") }), el("span", { text: t("ovb.hh.colCh") }), el("span", { text: t("ovb.hh.colNow") }), el("span", { text: t("ovb.hh.colDo") }));
      const rows = MET.map((m) => {
        const x = metView(m, hv, h), W = 300, H = 48, p = metPaths(x.ser, x.max, W, H, x.th);
        const f = tops.find((tp) => isObj(tp) && TOP_OF[m.id].indexOf(tp.id) >= 0);
        const host = hv ? hv.host : null;
        const extra = m.id === "cpu" && host && Array.isArray(host.load) && host.load.length >= 3 ? t("ovb.hh.load", { a: K.fmtNum1(host.load[0]), b: K.fmtNum1(host.load[1]), c: K.fmtNum1(host.load[2]) })
          : m.id === "mem" && host && isNum(host.mem_used) && isNum(host.mem_total) ? t("ovb.hh.memUsed", { a: K.fmtBytes(host.mem_used), b: K.fmtBytes(host.mem_total) }) : "";
        const thl = isNum(x.th) ? t(x.down ? "ovb.hh.thDown" : "ovb.hh.th", { v: pct(x.th, x.th < 10 ? 1 : 0) }) : "";
        const plot = el("div", { class: "kk-hh__plot" }, svg("svg", { class: "kk-hh__svg", viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", role: "img", "aria-label": t("ovb.hlAria", { name: t("ovb.m." + m.id), v: pct(x.now, 1) }) },
          [svg("path", { class: "kk-hh__g", d: "M0 0H" + W + "M0 " + H + "H" + W }), svg("path", { class: "kk-hh__a", d: p.area || "M0 0" }), svg("path", { class: "kk-hh__th", d: p.th || "M0 0" }), svg("path", { class: "kk-hh__l", d: p.line || "M0 0" }),
            p.last ? svg("path", { class: "kk-hh__dot" + (x.lv === "warn" ? " is-warn" : x.lv === "crit" ? " is-crit" : ""), d: "M" + p.last[0] + " " + p.last[1] + "h.01" }) : null]),
          el("span", { class: "kk-hh__y kk-hh__y--t", text: pct(x.max, 0) }), el("span", { class: "kk-hh__y kk-hh__y--b", text: "0" }), thl ? el("span", { class: "kk-hh__thl", text: thl }) : null);
        if (thl && isNum(x.th)) plot.lastChild.style.top = ((1 - clamp(x.th, 0, x.max) / x.max) * 100).toFixed(1) + "%";
        const ch = el("div", { class: "kk-hh__ch" }, x.ser.length ? plot : el("span", { class: "kk-hh__s", text: t("ovb.hh.noHist") }),
          x.ser.length ? el("div", { class: "kk-hh__ax" }, el("span", { text: t("ovb.hh.yest") }), el("span", { text: "" }), el("span", { text: "" }), el("span", { text: "" }), el("span", { text: t("ovb.hh.now") })) : null);
        const lvCls = x.lv === "ok" ? "gx-mst--ok" : x.lv === "warn" ? "gx-mst--warn" : x.lv === "crit" ? "gx-mst--bad" : "gx-mst--none";
        const doBox = x.lv === "warn" || x.lv === "crit"
          ? el("div", { class: "kk-hh__do" }, el("span", { class: "kk-hh__dk", text: t("ovb.hh.do") }), el("span", { text: f ? [f.text, f.advice].filter(Boolean).join(". ") : DASH }))
          : el("div", { class: "kk-hh__do" }, el("span", { class: "kk-hh__dk", text: t("ovb.hh.norm") }), el("span", { text: t(OK_TEXT[m.id]) }));
        return el("div", { class: "kk-hh__r" + (x.lv === "warn" ? " is-warn" : x.lv === "crit" ? " is-crit" : "") },
          el("div", { class: "kk-hh__n" }, el("span", { class: "kk-hh__t", text: t("ovb.m." + m.id) }), el("span", { class: "gx-mst " + lvCls, text: t("ovb.lv." + x.lv) })),
          ch,
          el("div", { class: "kk-hh__v" }, el("span", { class: "kk-hh__big" }, pct(x.now, m.id === "net" ? 2 : 0), " ", el("small", { text: x.sub })), extra ? el("span", { class: "kk-hh__s", text: extra }) : null),
          doBox);
      });
      const host = hv ? hv.host : null;
      const kv = (k, b, s) => el("div", { class: "kk-hh__kv" }, el("span", { text: t(k) }), el("b", { text: b || DASH }), s ? el("small", { text: s }) : null);
      const sys = el("section", { class: "gx-card kk-hh__sys", "aria-label": t("ovb.hh.sys"), "data-find": "overview.host" },
        kv("ovb.hh.up", host && isNum(host.uptime) ? K.fmtDur(host.uptime) : DASH), kv("ovb.hh.ld", host && Array.isArray(host.load) ? host.load.map((y) => K.fmtNum1(y)).join(" · ") : DASH),
        kv("ovb.hh.ram", host && isNum(host.mem_total) ? K.fmtBytes(host.mem_total) : DASH), kv("ovb.hh.ver", v.ver || DASH));
      return [al, el("section", { class: "gx-card kk-hh__list", "aria-label": t("ovb.hlT") }, hd, rows), sys];
    }, { cls: "kk-hh" }));
  }

  // ── окно «Балл сервера» ─────────────────────────────────────────────────────────────────────────────────────────
  const LAY = { retro: true, now: true };
  let drw = null;
  function drawerBody() {
    const syn = D.syn, f = syn && syn.fresh ? syn.fresh : null, cks = syn ? arr(syn.checks) : [], c0 = cks[0] || null, c1 = cks[1] || null;
    const ph = phone(), out = [];
    const st = star({ mode: ph ? "phone" : "drawer", w: ph ? 326 : 400, hits: true, layers: LAY });
    out.push(el("div", { class: "kb-drw__star" }, st));
    const lg = el("div", { class: "kb-drw__lg", role: "group", "aria-label": t("ovb.dr.layers") });
    [["retro", "ovb.dr.usual", "is-band"], ["now", "ovb.dr.fresh", "is-now"]].forEach((x) => {
      const b = el("button", { type: "button", "aria-pressed": String(LAY[x[0]]) }, el("i", { class: x[2] }), el("span", { text: t(x[1]) }));
      b.addEventListener("click", () => { LAY[x[0]] = !LAY[x[0]]; b.setAttribute("aria-pressed", String(LAY[x[0]])); st.classList.toggle(x[0] === "retro" ? "is-noretro" : "is-nonow", !LAY[x[0]]); });
      lg.append(b);
    });
    out.push(lg);
    const cap = provCap();
    if (cap) out.push(cap);
    const dl = c0 && c1 && isNum(c0.total) && isNum(c1.total) ? c0.total - c1.total : null;
    out.push(el("div", { class: "kb-drw__sc" }, el("b", { text: f && isNum(f.total) ? String(f.total) : DASH }), el("span", { text: f && isNum(f.total) ? bandWord(f.total) : t("ovb.scoreNone") }),
      c0 && isNum(c0.total) ? el("small", { text: isNum(dl) ? t("ovb.dr.sub", { v: String(c0.total), d: dl > 0 ? "+" + dl : dl < 0 ? "−" + Math.abs(dl) : t("ovb.dr.same") }) : t("ovb.dr.subOne", { v: String(c0.total) }) }) : null));
    // что изменилось к прошлой проверке
    const ds = deltaOf(c0, c1);
    const chg = el("div", { class: "kb-sec" }, el("h3", { text: t("ovb.dr.chT") }));
    if (!ds.length) chg.append(el("span", { class: "kb-mut", text: t("ovb.dr.chNone") }));
    else {
      chg.append(el("div", { class: "kb-dl" }, ds.map((x) => {
        const w = clamp(Math.abs(x.d), 0, 30), sg = x.d > 0 ? "+" + x.d : x.d < 0 ? "−" + Math.abs(x.d) : "0";
        return el("div", { class: "kb-dl__r", title: dlName(x.id) + ": " + x.was + " → " + x.v },
          el("span", { text: dlName(x.id) }),
          svg("svg", { class: "kb-dl__svg", viewBox: "-30 0 60 12", preserveAspectRatio: "none", "aria-hidden": "true" },
            [svg("path", { class: "kb-dl__z", d: "M0 0V12" }), svg("rect", { class: x.d < 0 ? "kb-dl__n" : "kb-dl__p", x: x.d < 0 ? -w : 0, y: 2, width: Math.max(w, 0.5), height: 8, rx: 1 })]),
          el("span", { class: "kb-dl__d" + (x.d > 2 ? " is-up" : x.d < -2 ? " is-dn" : ""), text: sg }), el("span", { class: "kb-dl__v", text: String(x.v) }));
      })), el("span", { class: "kb-mut", text: t("ovb.dr.chNote") }));
    }
    out.push(chg);
    // итог по проверкам — ряд, пунктир «обычно»
    const tc = cks.filter((c) => isNum(c.total)).slice(0, 12).reverse();
    if (tc.length >= 2) {
      const tots = tc.map((c) => c.total), u = syn.totals && isNum(syn.totals.usual) ? syn.totals.usual : null;
      const mn = Math.min(...tots, isNum(u) ? u : Infinity) - 8, mx = Math.max(...tots, isNum(u) ? u : -Infinity) + 8;
      const Y = (x) => r1(52 - ((x - mn) / (mx - mn || 1)) * 48), X = (i) => r1((i / (tc.length - 1)) * 300);
      const lab = tc.map((c) => dayShort(c.day));
      const picks = tc.length <= 6 ? lab.map((x, i) => i) : [0, Math.round((tc.length - 1) / 3), Math.round((2 * (tc.length - 1)) / 3), tc.length - 1];
      out.push(el("div", { class: "kb-sec" }, el("h3", { text: t("ovb.dr.trT") }),
        svg("svg", { class: "kb-tr", viewBox: "0 0 300 56", preserveAspectRatio: "none", role: "img", "aria-label": t("ovb.dr.trAria", { list: tc.map((c, i) => lab[i] + " — " + c.total).join(", ") }) },
          [isNum(u) ? svg("path", { class: "kb-tr__m", d: "M0 " + Y(u) + "H300" }) : null, svg("path", { class: "kb-tr__l", d: "M" + tc.map((c, i) => X(i) + " " + Y(c.total)).join("L") }),
            svg("path", { class: "kb-tr__p", d: tc.map((c, i) => "M" + X(i) + " " + Y(c.total) + "h.01").join("") })]),
        el("div", { class: "kb-tr__x" }, picks.map((i) => el("span", { text: lab[i] }))),
        el("span", { class: "kb-mut", text: isNum(u) ? t("ovb.dr.trSum", { u: String(u), a: String(Math.min(...tots)), b: String(Math.max(...tots)), n: K.fmtInt(tots.length) })
          : t("ovb.dr.trSumNo", { a: String(Math.min(...tots)), b: String(Math.max(...tots)), n: K.fmtInt(tots.length), need: "4" }) })));
    }
    out.push(...whyBox());
    const how = el("details", { class: "kb-sec" }, el("summary", { class: "kb-how", text: t("ovb.dr.how") }), el("p", { class: "kb-mut", text: t("ovb.dr.howX", { gap: gapOf(syn) }) }), provHow(syn), ...calibBox(syn));
    out.push(how);
    return out;
  }
  // «Как считается балл» (решение 5): какие звенья выверены по эталонным серверам, какие предварительные. Сами эталоны
  // (хостер, тариф, процессор, город) не показываем — владелец 29.10, D-142; API их больше не отдаёт
  function calibBox(syn) {
    const c = calibOf(syn), names = (ids) => ids.map((id) => lname(id).toLocaleLowerCase(K.lang === "en" ? "en" : "ru")).join(", ");
    return [el("p", { class: "kb-mut", text: !c.ok.length ? t("ovb.calibNone") : !c.prov.length ? t("ovb.calibAll", { ok: names(c.ok) })
      : t("ovb.calib", { ok: names(c.ok), prov: names(c.prov) }) })];
  }
  // правило первых часов (решение 4) — часы окна проб из ответа сервера (net_window_h), не числом в словаре
  const provHow = (syn) => (isObj(syn) && isNum(syn.net_window_h) ? el("p", { class: "kb-mut", text: t("ovb.provHow", { need: K.fmtInt ? K.fmtInt(syn.net_window_h) : String(syn.net_window_h) }) }) : null);
  // -> [узлы]: «Почему итог не выше» — только когда предел держит итог (D-143); «предварительно» с часами проб и «итога
  // пока нет» — строками под числом, без этого заголовка
  function whyBox() {
    const w = whyOf(D.syn), p = provOf(D.syn), out = [];
    if (p) out.push(el("p", { class: "kb-mut kb-why__prov", text: provTxt(p) }));
    if (w.key === "lim") out.push(el("div", { class: "kb-why" }, el("b", { text: t("ovb.why") }), t("ovb.why.lim", { name: lname(w.id), v: String(w.v), cap: String(w.cap), raw: String(w.raw) })));
    else if (w.key === "incomplete" || w.key === "none") out.push(el("p", { class: "kb-mut", text: t(w.key === "incomplete" ? "ovb.why.incomplete" : "ovb.why.none") }));
    return out;
  }
  function openScore() {
    if (drw && drw.el && drw.el.isConnected) return;
    wantScore(true);
    drw = K.openSheet({ wide: true, title: t("ovb.score"), onClose: () => { drw = null; } });
    const c0 = D.syn ? arr(D.syn.checks)[0] : null;
    const sub = c0 ? whenOf(c0.ts) + " · " + t(c0.mode === "full" ? "ovb.dr.full" : "ovb.dr.quick") : t("ovb.noChecks");
    drw.setHead([el("div", { class: "kb-drw__h" }, el("div", {}, el("h2", { text: t("ovb.score") }), el("span", { text: sub + (provOf(D.syn) ? " · " + t("ovb.prov") : "") }))), drw.closeBtn()]);
    const body = el("div", { class: "kb-drw" });
    drw.body.append(body);
    body.append(...(D.syn ? drawerBody() : [D.err ? U.al("danger", t("ovb.loadFailed"), (D.err && D.err.message) || "") : U.skel(4)]));
    if (!D.syn) { const was = D.v; const tick = () => { if (!drw) return; if (D.v !== was && D.syn) body.replaceChildren(...drawerBody()); else if (D.run) setTimeout(tick, 200); }; setTimeout(tick, 200); }
  }

  // ── карточка балла на «Проверка сервера › Сейчас» (pages/diag.js) ───────────────────────────────────────────────
  // o: {top: [узлы] — когда проверено, «от обычного»; bottom: [узлы] — отметки, кнопки, следующая проверка; zone — пояс}
  // правая колонка — ещё и gx-chk__main: её текст читают тесты и поиск «Сейчас» (как у прежней шапки проверки)
  function scoreCard(o) {
    wantScore(false);
    const ph = phone(), syn = D.syn, f = syn && syn.fresh ? syn.fresh : null;
    const right = el("div", { class: "kb-ck__r gx-chk__main" },
      el("div", { class: "kb-ck__sc" }, el("b", { text: f && isNum(f.total) ? String(f.total) : DASH }), el("span", { text: f && isNum(f.total) ? bandWord(f.total) : t("ovb.scoreNone") })),
      o.top || [], whyBox(), o.bottom || []);
    const head = el("div", { class: "kb-h" }, el("h2", { class: "kb-t", text: t("ovb.score") }), provChip(syn),
      el("span", { class: "kb-sp" }), o.zone ? el("span", { class: "kb-mut", text: o.zone }) : null);
    const st = el("div", { class: "kb-ck__star" }, star({ mode: ph ? "phone" : "diag", w: ph ? 326 : 560, hits: true }), provCap());
    return el("section", { class: "gx-card", "aria-label": t("ovb.score") }, head, el("div", { class: "kb-ck" }, st, right));
  }
  K.part("ovb", {
    build: summary,
    summary,
    health,
    openScore,
    scoreCard,
    // diag.js «Сейчас»: метка данных (растёт с каждым ответом) — в compute блока, чтобы он перерисовался с баллом
    ver: () => D.v,
    reload: () => wantScore(true),
    // чистые помощники — для тестов в node
    pure: { bandOf, mono, segs, verdictOf, d30Of, buckets, trafficOf, xnPts, xnJoin, deltaOf, dlName, whyOf, geoOf, polyOf, ndPt, provOf, calibOf },
  });
})();
