/* Kaskad 3 — карточка «От вашего устройства до сервера» (BC5; SPEC-browser-channel §6.2–§6.6, макет Claude Design
   «Browser Speed r1», export-2026-10-24-browser-speed-r1). Часть "speed": её грузит раздел «Проверка сервера» (pages/diag.js,
   K.loadPart) — pages/speed-i18n-ru.js (+ -en.js), speed-calc.js, затем этот файл; он регистрирует часть через K.part().
   Место — своя карточка под карточкой проверки сервера на «Сейчас»; узел карточки живёт между перерисовками раздела.

   Сеть — только воркер speed-worker.js (маршруты /api/host/speed/*) и K.API.host.speed.budget (пределы до нажатия
   «Проверить» и путь входа). На время замера панель молчит: K.quiet (app.js) останавливает поток событий и опросы этой
   и других вкладок. Результаты — только в этом браузере: localStorage["kaskad.speed.v1"] (до 30 записей, без адресов;
   чтение и запись — в try/catch, приватный режим — просто без истории). В балл, звезду и «Поделиться» не попадают.

   Классы — макета (gx-spd*, gx-spdh*, speed.css) и дизайн-системы (gx-card, gx-tile, gx-al, ga-btn, ga-kbd); атрибута
   style нет (CSP): график — сетка графиков KaskadSpark.grid (spark.js, grid.css; атрибуты SVG и CSSOM). Без шаблонных строк. */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const { el, t, tn, U } = K;
  const SC = window.SpeedCalc;
  const ID = "speed";
  const KEY = "kaskad.speed.v1";
  const SVG = "http://www.w3.org/2000/svg";
  const QUIET_MS = 45000;                 // пауза панели на замер (страховка самой панели — 50 с, app.js)
  const WATCH_MS = 60000;                 // воркер молчит дольше — замер считается оборванным
  const EST = { idle: 2500, side: 10000 };   // ожидаемая длина фаз, пока они идут (ось графика): покой ≈ 2,5 с, сторона ≤ 10 с
  const NOTES = ["tunnel", "vpn", "cascade", "one_conn", "cpu", "fast"];
  const DASH = "—";

  // ── чистые помощники (тесты в node: tests/panel/test_speed_card.mjs) ───────────────────────────────────────────
  const mbit = (bps) => (typeof bps === "number" && isFinite(bps) && bps > 0 ? bps / 1e6 : null);
  // границы фаз, мс от начала покоя: [0, начало скачивания, начало отдачи, конец]; ещё не начатые — по ожидаемой длине,
  // идущая — не короче прошедшего. at — {idle, down, up, finish} по часам страницы, now — сейчас
  function bounds(at, now) {
    const o = at.idle;
    const pass = (from) => Math.max(0, now - from);
    const d = at.down !== undefined ? at.down - o : Math.max(EST.idle, pass(o));
    const u = at.up !== undefined ? at.up - o : d + (at.down !== undefined ? Math.max(EST.side, pass(at.down)) : EST.side);
    const e = at.finish !== undefined ? Math.max(u, at.finish - o) : u + (at.up !== undefined ? Math.max(EST.side, pass(at.up)) : EST.side);
    return [0, d, u, Math.max(e, 1)];
  }
  // путь линии: точки [мс от начала покоя, Мбит/с] -> «M x yLx y…» в поле W × H (по умолчанию 600 × 160; ноль — y H − 1,5,
  // верх шкалы — y 1,5, как у сетки графиков)
  function pathOf(pts, end, ym, W, H) {
    if (!pts || !pts.length) return "";
    return "M" + pts.map((p) => xOf(p[0], end, W) + " " + yOf(p[1], ym, H)).join("L");
  }
  const xOf = (ms, end, W) => (Math.max(0, Math.min(1, ms / end)) * (W || 600)).toFixed(1);
  const yOf = (v, ym, H) => (1.5 + (1 - Math.max(0, Math.min(1, v / ym))) * ((H || 160) - 3)).toFixed(1);
  // график: g — {pts: {down, up}, b: bounds, cur: "down" | "up" | null (готовые стороны — серой линией, идущая —
  // цветной с точкой на конце), min — пик итога, когда точек нет}, W × H — поле -> {done, live, dot, sc, ym}. Верх — правило
  // сетки графиков (Claude Design «Сетка на графиках r1»: 1-2-5, не ниже пика; 92 → 0 · 50 · 100), поле 160–180 — 3 деления
  function chartOf(g, W, H) {
    const down = g.pts.down || [], up = g.pts.up || [], end = g.b[3];
    const peak = down.concat(up).reduce((m, p) => Math.max(m, p[1]), 0);
    const sc = window.KaskadSpark.grid.scale(Math.max(peak, g.min || 0), 3), ym = sc.top;
    const live = g.cur === "down" ? down : g.cur === "up" ? up : [];
    const done = g.cur === "down" ? [] : g.cur === "up" ? [down] : [down, up];
    const last = live.length ? live[live.length - 1] : null;
    return { done: done.map((s) => pathOf(s, end, ym, W, H)).join(""), live: pathOf(live, end, ym, W, H),
             dot: last ? "M" + xOf(last[0], end, W) + " " + yOf(last[1], ym, H) + "h0" : "", sc, ym };
  }
  // что сказать по итогу движка (r — result воркера) -> {st, tone, key, n?, gb?, wait?} или null (всё хорошо)
  // st: lim — предел (час, сутки, трафик), busy — идёт другая проверка, err — ошибка или прервано, auth — нужен вход,
  // idle — остановлено человеком. wait — секунды до «можно снова» (кнопка aria-disabled до них)
  function sayOf(r) {
    const e = r && r.error;
    if (!e) {
      if (r && r.stopped === "hidden") return { st: "err", tone: "", key: "panel.speed.fail.hidden" };
      if (r && r.stopped) return { st: "idle" };
      return null;
    }
    const wait = typeof e.retryAfter === "number" && e.retryAfter > 0 ? Math.ceil(e.retryAfter) : null;
    if (e.kind === "auth") return { st: "auth" };
    // 409: идёт проверка сервера (what: "check" — её запустили, может быть, в этой же панели) или другой замер (T-3310b М-5)
    if (e.kind === "busy") return { st: "busy", tone: "", key: e.what === "check" ? "panel.speed.checkBusy" : "panel.speed.other", wait: wait || 15 };
    if (e.kind === "hour" || e.kind === "day") return { st: "lim", tone: "warning", key: "panel.speed.lim." + e.kind, n: e.n || 0, wait: wait || 60, plural: true };
    if (e.kind === "traffic") return { st: "lim", tone: "warning", key: "panel.speed.lim.traffic", gb: e.limit, wait: wait || 3600 };
    if (e.kind === "expired") return { st: "err", tone: "danger", key: "panel.speed.fail.expired" };
    if (e.kind === "server_busy") return { st: "err", tone: "", key: "panel.speed.fail.server_busy" };
    if (e.kind === "net") return { st: "err", tone: "danger", key: "panel.speed.fail.net" };
    return { st: "err", tone: "danger", key: "panel.speed.fail.server" };   // 5xx, 429 streams, прочие отказы
  }
  // строка «Под нагрузкой» (loaded движка) -> {n, word} | {why} | null
  function loadOf(ld) {
    if (!ld) return null;
    if (ld.show) return { n: ld.deltaMs, word: "panel.speed.word." + ld.scale };
    return ld.reason ? { why: "panel.speed.why." + ld.reason } : null;
  }
  const notesOf = (flags) => NOTES.filter((f) => (flags || []).indexOf(f) >= 0).map((f) => "panel.speed.note." + f);
  // жирное в сообщении — первая фраза (до «.» или «?» с пробелом или концом), остальное — обычным (HANDOFF r1)
  function boldSplit(s) {
    const m = /^(.+?[.?])(\s|$)/.exec(String(s || ""));
    return m ? [m[1], String(s).slice(m[1].length).trim()] : [String(s || ""), ""];
  }
  // история: чтение и запись — в try/catch (приватный режим, запрет хранилища — без истории)
  function histRead(store) {
    try { return SC.historyClean(JSON.parse(store.getItem(KEY) || "[]")); } catch (e) { return []; }
  }
  function histWrite(store, list) {
    try { if (list.length) store.setItem(KEY, JSON.stringify(list)); else store.removeItem(KEY); return true; } catch (e) { return false; }
  }
  const PURE = Object.freeze({ mbit, bounds, pathOf, chartOf, sayOf, loadOf, notesOf, boldSplit, histRead, histWrite, KEY });

  // ── состояние ───────────────────────────────────────────────────────────────────────────────────────────────
  const P = {
    st: "idle",            // idle | ask | run | res | lim | busy | err (кадры макета 1–8; история — внутри)
    res: null,             // итог последнего удачного замера (кадры 3–4)
    say: null,             // сообщение кадров 5–8 (sayOf) и когда можно снова (until, мс эпохи)
    until: 0,
    budget: null,          // ответ budget: caps.side_bytes (цена), via (вход через порт VPN)
    hist: [],
    histOpen: false,
    run: null,             // идущий замер: фаза, времена фаз, точки графика, готовые цифры
    worker: null,
    focus: null,           // куда вернуть фокус после перерисовки: "go" | "again" | "stop" | "yes" | "hist"
  };
  let card = null;
  let said = null;         // скрытая строка aria-live: итог замера словами (живёт между перерисовками карточки)
  const R = {};            // узлы идущего замера (обновляются по сообщениям воркера, без перерисовки карточки)
  let waitTimer = null, watchTimer = null;
  const store = () => { try { return window.localStorage; } catch (e) { return null; } };
  const mock = () => (K.mock() && window.KASKAD_SPEED_MOCK) || null;

  // ── числа ───────────────────────────────────────────────────────────────────────────────────────────────────
  const fmtMs = (v) => (typeof v === "number" && isFinite(v) ? K.fmtInt(Math.round(v)) : DASH);
  // Мбит/с: от 10 — целыми; меньше — с десятыми, но без «,0» («8», «4,2»)
  const fmtMbit = (v) => (v === null || v === undefined ? DASH : v < 10 && Math.round(v * 10) % 10 !== 0 ? K.fmtNum1(v) : K.fmtInt(Math.round(v)));
  const WAIT = () => ({ s: t("panel.speed.wait.s"), m: t("panel.speed.wait.m"), h: t("panel.speed.wait.h"), hm: t("panel.speed.wait.hm") });
  const gbText = (bytes) => SC.fmtGb(bytes, K.lang);
  const priceGb = () => {
    const c = P.budget && P.budget.caps;
    return gbText(c && typeof c.side_bytes === "number" && c.side_bytes > 0 ? 2 * c.side_bytes : 1.6e9);
  };
  function sayText(s) {
    const left = Math.max(1, Math.ceil((P.until - Date.now()) / 1000));
    const vars = { wait: SC.fmtWait(left, K.lang, WAIT()) };
    if (s.gb !== undefined) vars.gb = gbText(s.gb);
    return s.plural ? tn(s.key, s.n, vars) : t(s.key, vars);
  }
  function whenText(ms) {
    const d = new Date(ms), L = window.KaskadLang;
    if (!L) return "";
    return d.toDateString() === new Date().toDateString() ? t("panel.speed.today", { hm: L.time(d) }) : L.dateTime(d);
  }

  // ── сеть: пределы до старта ─────────────────────────────────────────────────────────────────────────────────
  function loadBudget() {
    const api = K.API.host.speed;
    return api.budget().then((d) => { P.budget = d && typeof d === "object" ? d : null; }, () => null).then(() => paint());
  }

  // ── замер ───────────────────────────────────────────────────────────────────────────────────────────────────
  const blocked = () => P.until > Date.now();
  function cellular() {
    const m = mock();
    if (m && typeof m.cellular === "boolean") return m.cellular;
    const c = navigator.connection;
    return !!(c && c.type === "cellular");
  }
  function go(asked) {
    if (P.run || blocked()) return;
    if (!asked && cellular()) { P.st = "ask"; P.say = null; P.focus = "yes"; paint(); return; }
    const m = mock();
    let w = null;
    try {
      const Wk = (m && m.Worker) || window.Worker;
      w = new Wk("speed-worker.js" + (K.ver ? "?v=" + encodeURIComponent(K.ver) : ""));
    } catch (e) { w = null; }
    if (!w) { done({ ok: false, error: SC.refusal(500, null), stopped: null }); return; }
    P.worker = w;
    P.run = { phase: "start", at: {}, pts: { down: [], up: [] }, cur: null, ping: null, jitter: null, down: null };
    P.st = "run"; P.say = null; P.until = 0; P.focus = "stop";
    K.quiet.start(Date.now() + QUIET_MS);
    document.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", onHide);
    clearTimeout(watchTimer);
    watchTimer = setTimeout(() => done({ ok: false, error: SC.refusal(0, null), stopped: null }), WATCH_MS);
    w.onmessage = (ev) => onMsg(ev && ev.data);
    w.onerror = () => done({ ok: false, error: SC.refusal(500, null), stopped: null });
    paint();
    w.postMessage({ type: "start", base: K.base, lang: K.lang, phone: !!K.isPhone() });
  }
  function stop(reason) {
    if (P.worker) { try { P.worker.postMessage({ type: "stop", reason }); } catch (e) { /* воркер ушёл */ } }
  }
  // Esc — только пока карточка на экране (на другом разделе Esc закрывает своё, а не замер)
  const onKey = (ev) => { if (ev.key === "Escape" && P.run && !ev.defaultPrevented && card && card.getClientRects().length) stop("user"); };
  const onHide = () => { if (document.hidden && P.run) stop("hidden"); };
  function onMsg(m) {
    const r = P.run;
    if (!m || !r) return;
    const now = performance.now();
    if (m.type === "phase") {
      r.at[m.phase] = now;
      if (m.phase === "idle" || m.phase === "down" || m.phase === "up") { r.phase = m.phase; r.cur = null; }
      live(true);
    } else if (m.type === "sample") {
      r.cur = m.ms; live(false);
    } else if (m.type === "partial") {
      if ("ping" in m) { r.ping = m.ping; r.jitter = m.jitter; }
      if ("down" in m) r.down = mbit(m.down);
      live(true);
    } else if (m.type === "progress" && (m.side === "down" || m.side === "up") && r.at[m.side] !== undefined && r.at.idle !== undefined) {
      const v = mbit(m.bps) || 0;
      r.pts[m.side].push([r.at[m.side] - r.at.idle + (Number(m.t) || 0), v]);
      r.cur = v; live(false);
    } else if (m.type === "done") {
      done(m.result || { ok: false, error: SC.refusal(0, null), stopped: null });
    }
  }
  function done(res) {
    if (!P.run && !P.worker) return;
    const r = P.run;
    clearTimeout(watchTimer);
    document.removeEventListener("keydown", onKey);
    document.removeEventListener("visibilitychange", onHide);
    if (P.worker) { try { P.worker.terminate(); } catch (e) { /* уже */ } }
    P.worker = null; P.run = null;
    K.quiet.end();
    const s = sayOf(res);
    const inCard = !!(card && card.contains(document.activeElement));
    if (!s) {
      P.res = { r: res, g: r ? { pts: r.pts, at: r.at } : null };
      const st = store();
      if (st) { P.hist = SC.historyPush(histRead(st), SC.historyEntry(res)); histWrite(st, P.hist); }
      else P.hist = SC.historyPush(P.hist, SC.historyEntry(res));
      P.st = "res"; P.say = null; P.focus = inCard ? "again" : null;
      if (said) said.textContent = resAria(res);
      // 429 streams посреди стороны: итог есть, но сторона кончилась отказом сервера — сказать об этом
      if (res.sideError) P.say = { st: "res", tone: "danger", key: "panel.speed.fail.server" };
    } else if (s.st === "auth") {
      P.st = P.res ? "res" : "idle"; P.say = null;
      K.API.host.speed.budget().catch(() => null);         // 401 у request() — экран входа панели
    } else if (s.st === "idle") {
      P.st = "idle"; P.say = null; P.focus = inCard ? "go" : null;
    } else {
      P.st = s.st; P.say = s; P.focus = inCard ? "go" : null;
      P.until = s.wait ? Date.now() + s.wait * 1000 : 0;
      if (P.until) armWait();
    }
    loadBudget();
    paint();
  }
  // «Проверить» выключена до P.until; текст {wait} идёт вместе со временем; срок вышел — сообщение уходит
  function armWait() {
    clearTimeout(waitTimer);
    const left = P.until - Date.now();
    if (left <= 0) {
      P.until = 0;
      if (P.st === "lim" || P.st === "busy") { P.st = "idle"; P.say = null; }
      paint();
      return;
    }
    if (R.sayText && P.say) R.sayText.replaceChildren(...sayNodes(sayText(P.say)));
    waitTimer = setTimeout(armWait, left < 120000 ? 1000 : 30000);
  }

  // ── вид ─────────────────────────────────────────────────────────────────────────────────────────────────────
  function svgEl(tag, attrs) {
    const n = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach((k) => n.setAttribute(k, attrs[k]));
    return n;
  }
  function sayNodes(text) {
    const [b, rest] = boldSplit(text);
    return rest ? [el("b", { text: b }), document.createTextNode(" " + rest)] : [el("b", { text: b })];
  }
  // сообщение: s — sayOf (тон, ключ, числа); text — свой готовый текст вместо ключа; btns — кнопки под ним
  function sayBox(s, btns, text) {
    const tone = s.tone === "warning" || s.tone === "danger" ? s.tone : "";
    const tx = el("p", { class: "gx-al__text" }, sayNodes(text || sayText(s)));
    R.sayText = tx;
    return el("div", { class: "gx-al" + (tone ? " gx-al--" + tone : ""), role: tone === "danger" ? "alert" : "status" },
      el("span", { class: "gx-al__icon" }, K.icon(tone === "danger" ? "xmark" : tone === "warning" ? "clock" : "circle-info", tone ? 14 : 16)),
      el("div", { class: "gx-al__body" }, tx, btns ? el("div", { class: "gx-al__btns" }, btns) : null));
  }
  function goRow() {
    const b = U.btn(t("panel.speed.btn"), "primary", () => go(false), { md: true });
    b.dataset.spd = "go";
    if (blocked()) b.setAttribute("aria-disabled", "true");
    return el("div", { class: "gx-spd__go" }, b, el("span", { class: "gx-spd__price", text: t("panel.speed.price", { gb: priceGb() }) }));
  }
  function vpnList() {
    const front = !!(P.budget && P.budget.via === "front");
    return el("ul", { class: "gx-spd__vpn" }, el("li", { text: t("panel.speed.vpn") }), front ? el("li", { text: t("panel.speed.front") }) : null);
  }
  // phase — идущая фаза (подписи оси is-done / is-cur); null — итог, все подписи ровно. Сетка графиков (KaskadSpark.grid,
  // Claude Design «Сетка на графиках r1», кадр Ск1): деления 1-2-5 с нулём и «Мбит/с» над колонкой чисел, вертикальные
  // линии — границы фаз, подписи фаз — по центру своей фазы (не прячутся). Высота поля — speed.css (--grid-h)
  function chartBox(g, aria, phase) {
    const st = { g };                                     // данные графика: замер идёт — set() в progress
    const box = window.KaskadSpark.grid.box(Object.assign({ unit: t("panel.speed.u.mbps"), keep: true, paint: (v, W, H) => {
      const c = chartOf(st.g, W, H);
      if (c.done) v.append(svgEl("path", { class: "gx-spd__line", d: c.done }));
      if (c.live) v.append(svgEl("path", { class: "gx-spd__line gx-spd__line--live", d: c.live }));
      if (c.dot) v.append(svgEl("path", { class: "gx-grid__dot gx-grid__dot--acc", d: c.dot }));
    } }, gridOf(g, phase)));
    const fig = el("figure", { class: "gx-spd__chart", role: "img", "aria-label": aria }, box);
    Object.assign(R, { chart: { st, grid: box.__grid }, fig });
    return fig;
  }
  const PH = ["idle", "down", "up"];
  // шкала, подписи делений, границы фаз (линии) и подписи фаз для сетки
  function gridOf(g, phase) {
    const sc = chartOf(g).sc, b = g.b, end = b[3] || 1, pi = phase ? PH.indexOf(phase) : -1;
    return { sc, ys: window.KaskadSpark.grid.labels(sc, K.lang === "en" ? "." : ","), lines: [b[1] / end, b[2] / end],
      ax: ["ping", "down", "up"].map((k, i) => ({ at: (b[i] + b[i + 1]) / 2 / end, t: t("panel.speed.k." + k), cls: pi < 0 ? "" : i < pi ? "is-done" : i === pi ? "is-cur" : "" })) };
  }
  function runLeft() {
    const r = P.run;
    R.ph = el("ol", { class: "gx-spd__ph" });
    R.nowk = el("span", { class: "gx-spd__nowk", "aria-live": "polite" });
    R.nowv = el("b");
    R.nowu = el("span");
    R.done = el("div", { class: "gx-spd__done" });
    const stopB = U.btn(t("panel.speed.stop"), "tertiary", () => stop("user"), { md: true, icon: "xmark" });
    stopB.dataset.spd = "stop";
    const left = el("div", { class: "gx-spd__l" }, R.ph, el("div", { class: "gx-spd__now" }, R.nowk, el("div", { class: "gx-spd__nowv" }, R.nowv, R.nowu)), R.done,
      el("div", { class: "gx-spd__stop" }, stopB, el("kbd", { class: "ga-kbd", text: "Esc" })));
    const fig = chartBox({ pts: r.pts, b: bounds(r.at.idle === undefined ? { idle: performance.now() } : r.at, performance.now()), cur: null }, "", "idle");
    return el("div", { class: "gx-spd__grid" }, left, fig);
  }
  // идущий замер: цифры, фазы, график — по месту; phaseToo — сменилась фаза или готовые цифры (подпись aria-live)
  function live(phaseToo) {
    const r = P.run;
    if (!r || !R.nowv || !R.nowv.isConnected) return;
    const phase = PH.indexOf(r.phase) >= 0 ? r.phase : "idle";
    const pi = PH.indexOf(phase);
    const kk = ["ping", "down", "up"][pi];
    if (phaseToo) {
      R.ph.replaceChildren(...PH.map((p, i) => el("li", { class: i < pi ? "is-done" : i === pi ? "is-cur" : "", text: t("panel.speed.phase." + p) })));
      if (R.nowk.textContent !== t("panel.speed.k." + kk)) R.nowk.textContent = t("panel.speed.k." + kk);
      const bits = [];
      if (r.ping !== null || r.jitter !== null) {
        bits.push([t("panel.speed.k.ping"), fmtMs(r.ping) + (r.ping === null ? "" : " " + t("panel.speed.u.ms"))]);
        bits.push([t("panel.speed.k.jitter"), fmtMs(r.jitter) + (r.jitter === null ? "" : " " + t("panel.speed.u.ms"))]);
      } else if (pi >= 1) bits.push([t("panel.speed.k.ping"), DASH]);
      if (r.down !== null && pi >= 2) bits.push([t("panel.speed.k.down"), fmtMbit(r.down) + " " + t("panel.speed.u.mbps")]);
      R.done.replaceChildren(...bits.map((x) => el("span", { text: x[0] + " " }, el("b", { text: x[1] }))));
      R.done.hidden = !bits.length;
    }
    const v = pi === 0 ? fmtMs(r.cur) : fmtMbit(r.cur === null ? null : r.cur);
    R.nowv.textContent = r.cur === null ? DASH : v;
    R.nowu.textContent = t(pi === 0 ? "panel.speed.u.ms" : "panel.speed.u.mbps");
    if (r.at.idle === undefined) return;
    const cur = pi === 0 ? null : phase;
    if (R.chart) { R.chart.st.g = { pts: r.pts, b: bounds(r.at, performance.now()), cur }; R.chart.grid.set(gridOf(R.chart.st.g, phase)); }
    R.fig.setAttribute("aria-label", t("panel.speed.k." + kk) + ": " + R.nowv.textContent + " " + R.nowu.textContent);
  }
  function resLeft(res) {
    const r = res.r;
    const down = mbit(r.down && r.down.bps), up = mbit(r.up && r.up.bps);
    const tile = (k, v, u, s) => el("div", { class: "gx-tile" }, el("span", { class: "gx-tile__k", text: t("panel.speed.k." + k) }),
      el("span", { class: "gx-tile__v gx-tile__v--big" }, v, v === DASH ? null : el("span", { class: "gx-tile__u", text: t(u) })),
      s ? el("span", { class: "gx-tile__s", text: s }) : null);
    const tiles = el("div", { class: "gx-tiles gx-tiles--2" }, tile("ping", fmtMs(r.ping), "panel.speed.u.ms"), tile("jitter", fmtMs(r.jitter), "panel.speed.u.ms"),
      tile("down", fmtMbit(down), "panel.speed.u.mbps"), tile("up", fmtMbit(up), "panel.speed.u.mbps", up === null ? t("panel.speed.noup") : ""));
    const ld = loadOf(r.loaded);
    let line = null;
    if (ld && ld.why) line = el("p", { class: "gx-spd__load gx-spd__load--why", text: t(ld.why) });
    else if (ld) line = el("p", { class: "gx-spd__load" }, el("span", { text: t("panel.speed.load") }), " " + t("panel.speed.grows", { n: K.fmtInt(ld.n) }) + " ", el("b", { text: t(ld.word) }));
    return el("div", { class: "gx-spd__l" }, tiles, line);
  }
  // итог словами: «Скачивание: 87 Мбит/с; Отдача: 41 Мбит/с» (замена графика и строка aria-live)
  function resAria(r) {
    const down = mbit(r.down && r.down.bps), up = mbit(r.up && r.up.bps);
    return t("panel.speed.k.down") + ": " + fmtMbit(down) + (down === null ? "" : " " + t("panel.speed.u.mbps")) + "; " +
      t("panel.speed.k.up") + ": " + fmtMbit(up) + (up === null ? "" : " " + t("panel.speed.u.mbps"));
  }
  function resChart(res) {
    const r = res.r, g = res.g;
    const down = mbit(r.down && r.down.bps), up = mbit(r.up && r.up.bps);
    const aria = resAria(r);
    const ok = g && g.at && g.at.idle !== undefined;
    return chartBox({ pts: ok ? g.pts : { down: [], up: [] }, b: ok ? bounds(g.at, g.at.finish || performance.now()) : [0, 1, 2, 3], cur: undefined, min: Math.max(down || 0, up || 0) }, aria, null);
  }
  function noteBox(keys) {
    if (!keys.length) return null;
    return el("div", { class: "gx-al", role: "note" }, el("span", { class: "gx-al__icon" }, K.icon("circle-info", 16)),
      el("div", { class: "gx-al__body" }, keys.map((k) => el("p", { class: "gx-al__text", text: t(k) }))));
  }
  function histBox() {
    if (!P.hist.length || P.st === "run" || P.st === "ask") return null;
    const open = P.histOpen;
    const tog = U.btn(t("panel.speed.histN", { n: K.fmtInt(P.hist.length) }), "ghost", () => { P.histOpen = !P.histOpen; P.focus = "hist"; paint(); }, { icon: "chevron-down", cls: "gx-spd__tog" });
    tog.dataset.spd = "hist";
    tog.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open) return el("div", { class: "gx-spd__hist" }, tog);
    const L = window.KaskadLang;
    const cols = [["ping", "panel.speed.u.ms"], ["jitter", "panel.speed.u.ms"], ["down", "panel.speed.u.mbps"], ["up", "panel.speed.u.mbps"]];
    const cell = (s) => el("span", { role: "cell", class: s === DASH ? "gx-spdh__nil" : null, text: s });
    const rows = P.hist.map((h) => {
      const d = new Date(h.t);
      return el("div", { class: "gx-spdh__r", role: "row" }, el("span", { class: "gx-spdh__d", role: "cell" }, el("small", { text: L ? L.day(d) : "" }), el("b", { text: L ? L.time(d) : "" })),
        cell(fmtMs(h.ping)), cell(fmtMs(h.jitter)), cell(fmtMbit(mbit(h.down))), cell(fmtMbit(mbit(h.up))));
    });
    const table = el("div", { class: "gx-spdh", role: "table", "aria-label": t("panel.speed.hist") },
      el("div", { class: "gx-spdh__h", role: "row" }, el("span", { role: "columnheader" }),
        cols.map((c) => el("span", { role: "columnheader", text: t("panel.speed.k." + c[0]) }, el("small", { text: t(c[1]) })))), rows);
    const clear = U.btn(t("panel.speed.clear"), "ghost", () => {
      const st = store();
      P.hist = []; P.histOpen = false;
      if (st) histWrite(st, []);
      P.focus = P.st === "res" ? "again" : "go";
      paint();
    }, { icon: "trash-bin" });
    return el("div", { class: "gx-spd__hist" }, tog, table, clear);
  }
  function paint() {
    if (!card) return;
    const st = P.st;
    const act = document.activeElement;
    const had = !!(act && card.contains(act));
    const was = had && act.dataset ? act.dataset.spd || null : null;     // перерисовка не теряет фокус кнопки
    Object.keys(R).forEach((k) => { delete R[k]; });
    card.setAttribute("aria-busy", st === "run" ? "true" : "false");
    const sub = st !== "run" && st !== "res";
    const acts = st === "res" && P.res ? el("div", { class: "gx-card__acts" }, el("span", { class: "gx-spd__when", text: whenText(P.res.r.t) }),
      (() => { const b = U.btn(t("panel.speed.again"), "tertiary", () => go(false), { md: true, icon: "arrow-rotate-right" }); b.dataset.spd = "again"; if (blocked()) b.setAttribute("aria-disabled", "true"); return b; })()) : null;
    const head = el("div", { class: "gx-card__head" },
      el("div", { class: "gx-card__lead" }, el("span", { class: "gx-card__ic gx-card__ic--muted" }, K.icon("display", 20)),
        el("div", { class: "gx-card__tt" }, el("h3", { class: "gx-card__title", text: t("panel.speed.title") }), sub ? el("span", { class: "gx-card__sub", text: t("panel.speed.sub") }) : null)),
      acts);
    const kids = [head];
    if (st === "ask") {
      const yes = U.btn(t("panel.speed.yes"), "primary", () => go(true), { md: true });
      yes.dataset.spd = "yes";
      const no = U.btn(t("panel.speed.no"), "ghost", () => { P.st = P.res ? "res" : "idle"; P.focus = P.res ? "again" : "go"; paint(); }, { md: true });
      kids.push(sayBox({ tone: "" }, [yes, no], t("panel.speed.mobile", { gb: priceGb() })));
      kids.push(vpnList());
    } else if (st === "run") {
      kids.push(runLeft());
    } else if (st === "res" && P.res) {
      if (P.say) kids.push(sayBox(P.say));
      kids.push(el("div", { class: "gx-spd__grid" }, resLeft(P.res), resChart(P.res)));
      kids.push(noteBox(notesOf(P.res.r.flags)));
      kids.push(el("p", { class: "gx-spd__warn", text: t("panel.speed.warn") }));
    } else {
      if (P.say && st !== "idle") kids.push(sayBox(P.say));
      kids.push(goRow(), vpnList());
    }
    kids.push(histBox());
    card.replaceChildren(said, ...kids.filter(Boolean));
    if (st === "run") live(true);
    const f = P.focus || was;
    P.focus = null;
    if (f && (had || f === "stop" || f === "yes")) {
      const n = card.querySelector('[data-spd="' + f + '"]') || card.querySelector('[data-spd="go"]') || card.querySelector('[data-spd="again"]');
      if (n) { try { n.focus(); } catch (e) { /* не в документе */ } }
    }
  }
  // кнопка «Проверить» в aria-disabled — не нажимается (но в фокусе, клавиатура видит её)
  function guard(ev) {
    const b = ev.target && ev.target.closest ? ev.target.closest('[aria-disabled="true"]') : null;
    if (b && card.contains(b)) { ev.preventDefault(); ev.stopImmediatePropagation(); }
  }
  function build() {
    if (card) return card;
    card = el("article", { class: "gx-card gx-spd", "aria-label": t("panel.speed.title"), "aria-busy": "false" });
    card.addEventListener("click", guard, true);
    said = el("p", { class: "gx-vh", "aria-live": "polite" });
    const st = store();
    P.hist = st ? histRead(st) : [];
    // запись истории в другой вкладке — список здесь тот же
    window.addEventListener("storage", (ev) => { if (ev.key === KEY && !P.run) { const s2 = store(); P.hist = s2 ? histRead(s2) : []; paint(); } });
    const m = mock();                                       // демо (?mock=1, dev-mock-speed.js): сцена сама
    if (m && m.histOpen) P.histOpen = true;
    paint();
    loadBudget();
    if (m && m.auto) setTimeout(() => go(false), 0);
    return card;
  }

  // build(box) — как у других частей; card() — сам узел карточки (раздел «Сейчас» вставляет его в свою стопку)
  K.part(ID, { build: (box) => box.replaceChildren(build()), card: build, pure: PURE });
})();
