/* Kaskad — мастер «Быстрый старт» (setup.js). Чистый JS без сборки и сторонних библиотек.

   Дизайн — k3/design/quick-start-design-v2 (HANDOFF §4–§14, QuickStart.dc.html), классы — qs.css поверх ../tokens.css и
   ../components.css. Контракт API — docs/k3/SPEC-setup-api.md: состояние, рельс и итоги шагов хранит сервер
   (/etc/kaskad/setup.json), страница их только показывает; после перезагрузки экран восстанавливается из GET state и
   идущего задания. Все адреса относительные (страница живёт под секретным путём /setup-<секрет>/).

   Безопасность: токен ссылки сервер убирает из адреса сам (303); пароль, ключи и токены живут только в полях и в теле
   POST — не в localStorage, не в адресе и не в журнале консоли. Выданные настройки устройства (шаг issue) — только в
   памяти этой вкладки, до ухода с этапа.

   Разработка без сервера: python3 -m http.server 8765 --directory lib/kaskad/panel/static →
   http://127.0.0.1:8765/setup/index.html?mock=1 — ответы dev-mock-setup.js и панель просмотра dev-view-setup.js
   (этапы и состояния, как в прототипе). Под секретным путём сервера ?mock=1 не действует. */
(function () {
  "use strict";

  const QS = new URLSearchParams(location.search);
  // стенд разработки: не под секретным путём и только на своём компьютере (на адресе панели мок не поднимется)
  const DEV = !/\/setup-[^/]+\//.test(location.pathname) && /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  let MOCK = DEV && QS.get("mock") === "1";
  // Язык (3.3.2): boot.js решает его до первого кадра (window.KaskadLang); английский словарь грузит boot() ниже
  // поверх русского (нет перевода — русский текст). Тексты сервера — на том же языке: заголовок X-Kaskad-Lang.
  // До входа страница может говорить на другом языке (переключатель экранов входа и ссылки, выбор — в браузере,
  // PRE_LANG); после входа — всегда язык сервера (SERVER_LANG): он один на сервер.
  const KL = window.KaskadLang || null;
  const SERVER_LANG = KL && KL.lang === "en" ? "en" : "ru";
  let LANG = SERVER_LANG;
  let LOCALE = (KL && KL.locale) || "ru-RU";
  const L_RU = window.KASKAD_SETUP_I18N || {};
  let L_EN = null;
  let L = L_RU;
  const DATES = KL;                    // даты — одно правило с панелью и ядром (boot.js kaskadDates)

  // ── Тексты ─────────────────────────────────────────────────────────────────
  function t(key, vars) {
    let s = Object.prototype.hasOwnProperty.call(L, key) ? L[key] : key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined || vars[k] === null ? m : String(vars[k])));
    return s;
  }
  // формы числа: «одна|несколько|много» (русский) или «one|other» (английский) — Intl.PluralRules языка страницы
  function plural(n, forms) {
    if (KL && typeof KL.form === "function" && KL.lang === LANG) return KL.form(n, forms);
    const f = String(forms).split("|"), m = Math.abs(n) % 100, d = m % 10;
    try {                                                  // язык страницы до входа не тот, что у boot.js
      const c = new Intl.PluralRules(LANG).select(Math.abs(Number(n) || 0));
      return f[Math.min(c === "one" ? 0 : c === "few" ? 1 : 2, f.length - 1)];
    } catch (e) { /* старый браузер — правило русского ниже */ }
    if (f.length < 3) return f[0];
    return m > 10 && m < 20 ? f[2] : d === 1 ? f[0] : d > 1 && d < 5 ? f[1] : f[2];
  }
  const tn = (key, n) => plural(n, t(key));
  const pad2 = (n) => String(n).padStart(2, "0");
  const mmss = (s) => { s = Math.max(0, Math.floor(s || 0)); return pad2(Math.floor(s / 60)) + ":" + pad2(s % 60); };
  const rng = (r) => (!r ? "" : r[0] === r[1] ? String(r[0]) : r[0] + "–" + r[1]);
  const arr = (x) => (Array.isArray(x) ? x : []);
  const now = () => Date.now() / 1000;
  let NF1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });
  const size = (b) => { const u = t("c.bytes").split(" "); return b >= 1048576 ? NF1.format(b / 1048576) + " " + u[2] : b >= 1024 ? Math.round(b / 1024) + " " + u[1] : b + " " + u[0]; };
  // «SSTP и OpenConnect» / «SSTP and OpenConnect»
  const andList = (xs) => { try { return new Intl.ListFormat(LOCALE, { type: "conjunction" }).format(xs); } catch (e) { return xs.join(", "); } };

  // ── DOM без innerHTML ──────────────────────────────────────────────────────
  function h(tag, a, ...kids) {
    const el = document.createElement(tag);
    if (a) {
      for (const k of Object.keys(a)) {
        const v = a[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "on") for (const ev of Object.keys(v)) el.addEventListener(ev, v[ev]);
        else if (k === "value") el.value = v;
        else if (k === "checked" || k === "disabled" || k === "hidden" || k === "readOnly") el[k] = !!v;
        else el.setAttribute(k, v === true ? "" : String(v));
      }
    }
    add(el, kids);
    return el;
  }
  function add(el, kids) {
    for (const k of kids) {
      if (k === null || k === undefined || k === false) continue;
      if (Array.isArray(k)) add(el, k);
      else el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return el;
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, a, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const k of Object.keys(a || {})) if (a[k] !== null && a[k] !== undefined) el.setAttribute(k, String(a[k]));
    for (const k of kids.flat(Infinity)) if (k) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return el;
  }
  function icon(name, px) {
    const g = window.KaskadIcons && window.KaskadIcons.svg(name, px || 16);
    return h("span", { class: "gi", "aria-hidden": "true" }, g || "");
  }
  function btn(label, kind, act, o) {
    o = o || {};
    const b = h("button", { type: "button", class: "ga-btn ga-btn--" + (kind || "tertiary") + (o.sm ? " ga-btn--sm" : "") + (o.cls ? " " + o.cls : ""),
      disabled: o.disabled, "aria-busy": o.busy ? "true" : null, "aria-label": o.aria || null, title: o.title || null, "data-k": o.k || null },
    o.icon ? icon(o.icon, o.sm ? 14 : 16) : null, label ? h("span", { text: label }) : null);
    if (act) b.addEventListener("click", (e) => { if (!b.disabled && b.getAttribute("aria-busy") !== "true") act(e); });
    return b;
  }
  function chip(text, tone) { return h("span", { class: "gx-chip gx-chip--" + (tone || "tag") }, text); }
  function note(tone, title, text, o) {
    o = o || {};
    const ic = { success: "check", warning: "exclamation", danger: "xmark", accent: "circle-info", info: "circle-info" }[tone] || "circle-info";
    return h("div", { class: "qs-note", "data-tone": tone || null, role: tone === "danger" ? "alert" : null, "data-hl-target": o.hl || null },
      h("span", { class: "qs-note__ic" }, icon(o.icon || ic, 14)),
      h("div", { class: "qs-note__b" }, title ? h("b", { text: title }) : null, text ? h("span", { text }) : null, o.extra || null),
      o.timer ? h("span", { class: "qs-timer", "data-since": o.timer }, mmss(now() - o.timer)) : null,
      o.btns && o.btns.length ? h("div", { class: "qs-note__end" }, o.btns) : null);
  }
  function copyText(text, said) {
    const done = () => toast(said || t("c.copied"));
    try {
      if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text));
    } catch (e) { /* старый браузер */ }
    fallbackCopy(text, said);
    return Promise.resolve();
  }
  function fallbackCopy(text, said) {
    const ta = h("textarea", { class: "qs-vh", "aria-hidden": "true" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    toast(ok ? said || t("c.copied") : t("c.copyFail"));
  }
  function copyBtn(text, o) {
    o = o || {};
    return btn(o.label === false ? "" : (o.label || ""), o.kind || "ghost", () => copyText(typeof text === "function" ? text() : text),
      { icon: "copy", sm: true, aria: t("c.copy"), title: t("c.copy"), cls: o.label ? "" : "ga-btn--icon" });
  }
  function code(text, o) {
    o = o || {};
    return h("div", { class: "qs-code", "data-hl-target": o.hl || null }, o.k ? h("span", { class: "qs-code__k", text: o.k }) : null,
      h("code", { title: text, text }), copyBtn(text, { label: o.copyLabel }));
  }
  function pre(text) { return h("div", { class: "qs-pre" }, text, h("span", { class: "qs-pre__copy" }, copyBtn(text))); }
  function saveFile(name, text, mime) {
    const url = URL.createObjectURL(new Blob([text], { type: mime || "text/plain;charset=utf-8" }));
    const a = h("a", { href: url, download: name, class: "qs-vh" });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    toast(t("c.downloaded"));
  }
  // «Подробнее» — только редкие детали, нужные немногим (3.3.2 №16, №17): сейчас это модуль ядра на этапе протоколов.
  // Остальные объяснения «зачем и как устроено» — в роликах этапа (k3/design/quick-start/video-scripts/), важное —
  // коротко на самом экране.
  const DETAILS = { protocols: "protocols" };
  function details(kind) {
    const k = DETAILS[kind];
    if (!k) return null;
    const open = !!S.detOpen[kind];
    return h("div", { class: "qs-details" },
      h("button", { type: "button", class: "qs-details__t", "aria-expanded": open ? "true" : "false", "data-k": "det-" + kind,
        on: { click: () => { S.detOpen[kind] = !open; render(); } } }, icon("chevron-right", 14), t("det." + k)),
      open ? h("p", { class: "qs-details__b", text: t("det." + k + "B", { cmd: "kaskadctl setup step …" }) }) : null);
  }

  // ── Состояние страницы ────────────────────────────────────────────────────
  const S = {
    sys: null,                 // системный экран вместо мастера: expired | used | unknown | limit | link | login | closed
    session: null, st: null, product: null, rail: null, ipcert: null,
    job: null, jobSeen: {}, res: {},                  // res[шаг] — последний ответ шага на этой странице
    data: {},                   // данные этапов: scan, plan, exits, extras, platforms, offers…
    ui: {}, detOpen: {}, hl: null, sheet: false, modal: null,
    net: { online: true, since: 0 }, noConn: false, streamLost: false,
    modePick: null,                                  // режим, выбранный карточкой, но ещё не сохранённый «Дальше»
    move: null, moveAt: 0, moveStay: false,          // переезд на домен (этап 4): адрес с одноразовым токеном, срок
    moveCheck: "",                                   // проверка домена перед автопереходом: "" | "wait" | "late"
    entered: "", busy: {}, timers: {}
  };
  const ui = (kind) => (S.ui[kind] = S.ui[kind] || {});
  const cur = () => (S.rail && S.rail.current) || "welcome";
  const classic = () => !!(S.st && S.st.mode === "classic");
  const stageOf = (kind) => arr(S.rail && S.rail.stages).find((x) => x.kind === kind);
  const ip = () => (S.ipcert && S.ipcert.ip) || "";
  const stepState = (name) => ((S.st && S.st.steps) || {})[name] || null;

  // ── API ────────────────────────────────────────────────────────────────────
  async function api(path, body, o) {
    o = o || {};
    const f = MOCK && window.KASKAD_SETUP_MOCK ? window.KASKAD_SETUP_MOCK.fetch : window.fetch.bind(window);
    const init = { credentials: "same-origin", headers: { Accept: "application/json", "X-Kaskad-Lang": LANG }, cache: "no-store" };
    if (body !== undefined) {
      init.method = "POST";
      init.headers["X-Kaskad-Admin"] = "1";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let r;
    try { r = await f("api/setup/" + path, init); }
    catch (e) {
      if (!S.noConn) { S.noConn = true; every("noconn", 5, () => api("net").catch(() => {})); render(); }
      throw Object.assign(new Error(t("sys.noConn")), { net: true });
    }
    if (S.noConn) { S.noConn = false; stop("noconn"); render(); }
    let doc = {};
    try { doc = await r.json(); } catch (e) { doc = {}; }
    const out = { status: r.status, body: doc || {} };
    const data = (doc && doc.data) || {};
    if (o.raw) return out;
    if (r.status === 410) { showSys("expired", { cmd: data.cmd }); throw Object.assign(new Error(doc.error || ""), { sys: true }); }
    if (r.status === 401 && path !== "login") { await sessionLost(); throw Object.assign(new Error(doc.error || ""), { sys: true }); }
    if (o.own) return out;                               // общие компоненты (api/setup/ux/…) разбирают отказ сами
    if (r.status === 404 && data.reason !== "unknown_step") { showSys("closed"); throw Object.assign(new Error(doc.error || ""), { sys: true }); }
    if (r.status === 409 && data.reason !== "stage" && data.reason !== "password_first" && (data.job || data.step)) {
      if (!o.quiet) toast(doc.error || t("sys.busy"));       // quiet: ответ покажет само окно (ключ выхода)
      pollJob();
    } else if (r.status >= 500 && r.status !== 503) {
      toast((doc && doc.error) || t("sys.error"));
    }
    return out;
  }
  // шаг: короткий отвечает 200 и итогом; долгий — 202 и заданием (дальше SSE / опрос)
  async function step(name, body, o) {
    S.busy[name] = true;
    render();
    try {
      const r = await api("step/" + name, body || {}, o);
      if (r.status === 202 && r.body.job) { onJob(r.body.job); return { job: r.body.job }; }
      const res = r.status === 200 ? r.body : Object.assign({ ok: false, state: "error", step: name,
        error: { code: (r.body.data || {}).reason || "http", text: r.body.error || t("sys.error") } }, {});
      if (!res.error && !res.ok && r.status !== 200) res.error = { code: "http", text: r.body.error || t("sys.error") };
      S.res[name] = res;
      if (res.state === "offline") setOffline(true);
      return res;
    } catch (e) {
      if (!e.sys) S.res[name] = { ok: false, state: "error", step: name, error: { code: "net", text: e.message } };
      return S.res[name] || { ok: false, state: "error" };
    } finally {
      delete S.busy[name];
      render();
    }
  }

  async function loadState() {
    const r = await api("state");
    if (r.status !== 200) return;
    const prev = cur();
    S.st = r.body;
    S.stAt = now();
    S.product = r.body.product || S.product;
    S.rail = r.body.rail;
    if (r.body.job) onJob(r.body.job, true);
    applyLook();
    if (cur() !== prev || !S.entered) enter(cur());
    render();
  }
  let stateTimer = 0;
  function stateSoon() { clearTimeout(stateTimer); stateTimer = setTimeout(() => loadState().catch(() => {}), 250); }

  async function stage(kind, action) {
    S.busy.stage = true;
    render();
    try {
      const r = await api("stage", { kind, action });
      if (r.status === 200) { S.rail = r.body.rail; if (S.st) S.st.rail = r.body.rail; enter(cur()); }
      else if (r.status === 409) toast(r.body.error || t("rail.aheadLocked"));
      await loadState();
    } catch (e) { /* экран системы уже показан */ }
    finally { delete S.busy.stage; render(); }
  }
  const next = (kind) => stage(kind || cur(), "done");
  const skip = (kind) => stage(kind || cur(), "skip");
  const open = (kind) => stage(kind, "open");

  // ── Ход долгих действий: SSE и запасной опрос ───────────────────────────────
  let es = null, lastEvent = 0, pollTimer = 0;
  function startEvents() {
    try {
      const ES = MOCK && window.KASKAD_SETUP_MOCK ? window.KASKAD_SETUP_MOCK.EventSource : window.EventSource;
      if (!ES) { pollJob(); return; }
      es = new ES("api/setup/events");
      es.addEventListener("job", (ev) => { lastEvent = now(); S.streamLost = false; try { onJob(JSON.parse(ev.data)); } catch (e) { /* пустое задание */ } });
      es.addEventListener("state", () => { lastEvent = now(); stateSoon(); });
      es.addEventListener("open", () => { lastEvent = now(); });
      es.onerror = () => {
        if (es && es.readyState === 2) { es = null; pollJob(); setTimeout(() => { if (!es && !S.sys) startEvents(); }, 15000); }
        else if (jobRunning()) pollJob();
      };
    } catch (e) { es = null; pollJob(); }
  }
  const jobRunning = () => !!(S.job && (S.job.state === "starting" || S.job.state === "running"));
  function pollJob() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const r = await api("job");
        if (r.status === 200) onJob(r.body.job);
      } catch (e) { /* связь вернётся — опрос продолжится */ }
      if (jobRunning() && (!es || es.readyState !== 1 || S.streamLost)) pollJob();
    }, 2000);
  }
  function onJob(job, quiet) {
    // итог задания уже пришёл (SSE раньше ответа 202, опрос) — запоздалый документ «starting/running» того же задания
    // его не затирает (R-333i С1)
    if (job && S.jobSeen[job.id] && !(job.state === "done" || job.state === "failed")) return;
    S.job = job || null;
    if (jobRunning() && (!es || es.readyState !== 1 || S.streamLost)) pollJob();     // без потока — опрос раз в 2 с
    if (job && (job.state === "done" || job.state === "failed") && job.result && !S.jobSeen[job.id]) {
      S.jobSeen[job.id] = true;
      S.res[job.step] = job.result;
      if (!quiet) jobDone(job.step, job.result, job);
      exitsAddDone(job, job.result);                     // и в тихом пути (loadState): ждущее поле выходов не зависает
    }
    if (!quiet) render();
  }
  function jobDone(name, res, job) {
    if (res && res.state === "offline") setOffline(true);
    const after = { "import": () => reload("scan"), "import-undo": () => reload("scan"), "import-file": () => reload("scan"),
      protocols: () => { reload("protocols-plan"); ui("done").offers = {}; }, "exits-add": () => reload("exits"), cert: () => { switchDone(res); moveSoon(); },
      site: () => { if (res && res.ok) res.at = now(); siteAnswer(res); }, "vless-mode": () => vlessAnswer(res), template: () => {} }[name];
    if (after) after();
    stateSoon();
  }

  // ── Опросы: сеть, выходы, DNS, подключение ──────────────────────────────────
  function every(name, sec, fn) {
    clearInterval(S.timers[name]);
    S.timers[name] = setInterval(() => { if (!document.hidden) fn(); }, sec * 1000);
  }
  function stop(name) { clearInterval(S.timers[name]); delete S.timers[name]; }
  function setOffline(off) {
    if (off && S.net.online) { S.net = { online: false, since: now() }; every("net", 10, () => checkNet(false)); }
    if (!off && !S.net.online) { S.net = { online: true, since: 0 }; stop("net"); }
    render();
  }
  async function checkNet(force) {
    try {
      const r = await api("net" + (force ? "?now=1" : ""));
      if (r.status === 200) setOffline(!r.body.online);
    } catch (e) { /* нет связи со страницей — своя плашка */ }
  }

  // ── Тост ───────────────────────────────────────────────────────────────────
  let toastTimer = 0;
  function toast(text) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2400);
  }

  // ── Вид: тема и акцент продукта (KaskadLook из ../boot.js) ──────────────────
  // Тема — по выбору в этом браузере (look.js, общий с панелью ключ «Вида»): тёмная по умолчанию, светлая — по
  // переключателю (3.3.2 №1); тему ОС мастер сам не берёт. ?theme= — только для разработки, не запоминается.
  function theme() { return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark"; }
  function savedTheme() { return window.KaskadSetupLook ? window.KaskadSetupLook.theme() : "dark"; }
  function setTheme(th) {
    if (window.KaskadSetupLook) window.KaskadSetupLook.set(th);
    S.themeOverride = th;
    applyLook();
    render();
  }
  function themeBtn(compact) {
    const light = theme() === "light", label = light ? t("look.toDark") : t("look.toLight");
    return btn(compact ? "" : label, "ghost", () => setTheme(light ? "dark" : "light"),
      { icon: light ? "moon" : "sun", sm: true, aria: label, title: label, cls: "qs-theme" + (compact ? " ga-btn--icon" : ""), k: compact ? "theme-bar" : "theme" });
  }
  function applyLook() {
    const root = document.documentElement, p = S.product || {};
    let th = S.themeOverride || savedTheme();
    if (!S.themeOverride && (QS.get("theme") === "light" || QS.get("theme") === "dark")) th = QS.get("theme");
    root.setAttribute("data-theme", th);
    root.setAttribute("data-tint", "neutral");          // палитра v2 токенов (3.3.0): тёмная — та же, что у прототипа мастера
    root.setAttribute("data-qs", "");
    root.setAttribute("data-device", matchMedia("(max-width: 767px)").matches ? "phone" : "desktop");
    if (p.hue) root.style.setProperty("--qs-hue", String(p.hue));
    const accent = th === "light" ? (p.accentLight || "oklch(.53 .17 288)") : (p.accent || "#45D0C0");
    if (window.KaskadLook) window.KaskadLook.apply(root, accent, th);
  }

  // ── Вход на этап: данные этапа и опросы ─────────────────────────────────────
  const ENTER = {};
  const RELOAD = {};
  function enter(kind) {
    if (S.entered === kind) return;
    const prev = S.entered;
    S.entered = kind;
    S.hl = null; S.sheet = false; S.modal = null;
    if (prev && prev !== kind) {                          // секреты этапа живут, пока он открыт (шапка файла)
      const d = ui("done"), im = ui("import");
      d.issue = null; d.held = null; d.qrs = {}; S.res.issue = null; im.file = null; im.bakPw = ""; ui("gh").key = ""; ui("x-backup").pw = "";
      if (im.bak) { closeBackup(); im.view = "found"; }
      setTimeout(() => { try { document.getElementById("qs-main").focus({ preventScroll: true }); } catch (e) { /* нет каркаса */ } }, 0);
    }
    ["exits", "dns", "conn"].forEach(stop);
    if (ENTER[kind]) ENTER[kind]();
    const sc = document.getElementById("qs-scroll");
    if (sc) sc.scrollTop = 0;
  }
  function reload(name) { if (RELOAD[name]) RELOAD[name](); }

  // ── Отрисовка ──────────────────────────────────────────────────────────────
  let queued = false;
  function render() {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => { queued = false; draw(); });
  }
  const D = {};                                   // постоянные узлы каркаса: прокрутка не сбрасывается
  function shell() {
    if (D.root && D.root.isConnected) return;
    D.rail = h("nav", { class: "qs-rail", "aria-label": t("rail.label") });
    D.bar = h("header", { class: "qs-bar" });
    D.main = h("main", { class: "qs-main", id: "qs-main", tabindex: "-1" });
    D.layout = h("div", { class: "qs-layout" }, D.main);              // 3.3.3: колонки «Видео к шагу» нет — ссылка в шапке
    D.scroll = h("div", { class: "qs-scroll", id: "qs-scroll" }, D.layout);
    D.alerts = h("div", { class: "qs-alerts", "aria-live": "polite" });      // «нет связи» — над прокруткой, видна всегда
    D.footRow = h("div", { class: "qs-foot__row" });
    D.footGrid = h("div", { class: "qs-foot__grid" }, D.footRow);
    D.foot = h("footer", { class: "qs-foot" }, D.footGrid);
    D.root = h("div", { class: "qs", id: "qs" }, D.rail, h("div", { class: "qs-body" }, D.bar, D.alerts, D.scroll, D.foot));
    document.getElementById("app").replaceChildren(D.root);
  }
  // Фокус между отрисовками: свои узлы мастера — data-k; окно выдачи (give.js) — data-gk внутри .qs-give (FIX-332g №3);
  // общие компоненты (ux.js, [data-ux]) живут между отрисовками — тот же узел: вернуть фокус ему (поле ключа выхода,
  // токен бота не теряют фокус от опроса раз в 10 с)
  function remember() {
    const a = document.activeElement;
    if (!a || !a.getAttribute) return null;
    if (a.closest && a.closest("[data-ux]")) {
      let s0 = null;
      try { s0 = typeof a.selectionStart === "number" ? [a.selectionStart, a.selectionEnd] : null; } catch (e) { s0 = null; }
      return { node: a, sel: s0 };
    }
    const k = a.getAttribute("data-k");
    const gk = !k && a.closest && a.closest(".qs-give") ? a.getAttribute("data-gk") : null;
    if (!k && !gk) return null;
    let sel = null;
    try { sel = [a.selectionStart, a.selectionEnd]; } catch (e) { sel = null; }
    return k ? { k, sel } : { gk, sel };
  }
  function restore(f) {
    if (!f) return;
    if (f.node) {
      const n = f.node;
      if (!n.isConnected || n === document.activeElement) return;
      try { n.focus({ preventScroll: true }); } catch (e) { n.focus(); }
      if (f.sel && typeof n.setSelectionRange === "function") { try { n.setSelectionRange(f.sel[0], f.sel[1]); } catch (e) { /* не текстовое поле */ } }
      return;
    }
    const esc = (v) => (window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/["\\]/g, "\\$&"));
    const el = document.querySelector(f.gk ? '.qs-give [data-gk="' + esc(f.gk) + '"]' : '[data-k="' + esc(f.k) + '"]');
    if (!el || el === document.activeElement) return;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    if (f.sel && f.sel[0] !== null && typeof el.setSelectionRange === "function") { try { el.setSelectionRange(f.sel[0], f.sel[1]); } catch (e) { /* не текстовое поле */ } }
  }
  function draw() {
    const app = document.getElementById("app"), layers = document.getElementById("layers");
    if (S.sys) {
      D.root = null;
      const f = remember();
      app.replaceChildren(sysScreen());
      layers.replaceChildren();
      restore(f);
      return;
    }
    if (!S.rail) return;
    shell();
    const f = remember();
    const kind = cur();
    const view = (VIEW[kind] || VIEW.welcome)();
    D.rail.replaceChildren(...railKids(false));
    D.bar.replaceChildren(...barKids());
    D.main.replaceChildren(...banners(), head(kind), ...arr(view.main).filter(Boolean), ...[details(kind)].filter(Boolean));
    dropGive(); dropUx();
    D.alerts.replaceChildren(...alerts());
    D.layout.toggleAttribute("data-wide", !!view.wide);
    D.footGrid.toggleAttribute("data-wide", !!view.wide);
    D.footRow.replaceChildren(...footKids(view.foot || {}));
    D.footRow.setAttribute("data-stage", kind);          // чей этап внизу (проход этапов в layout_scan --walk)
    const layerOpen = !!(S.sheet || S.modal), opener = layerOpen && !D.layer && f ? f.k : null;   // data-k кнопки, открывшей окно
    layers.replaceChildren(...[S.sheet ? sheet() : null, S.modal ? modal() : null].filter(Boolean));
    D.root.inert = layerOpen;                            // под окном ничего не нажимается и не получает фокус
    restore(f);
    if (layerOpen && !D.layer) {                         // окно открылось: фокус внутрь, запомнить, кто открыл
      D.opener = opener;
      // окно может назвать свою кнопку фокуса (согласие на Reality — «Назад», HANDOFF-vless §3)
      const own = S.modal && S.modal.focus ? layers.querySelector('[data-k="' + S.modal.focus + '"]') : null;
      const first = own || layers.querySelector("textarea, input, [role=tab][aria-selected=true], button:not(.gx-close), .gx-close");
      if (first) try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); }
    } else if (!layerOpen && D.layer && D.opener) {
      const el = document.querySelector('[data-k="' + D.opener + '"]');
      if (el) el.focus({ preventScroll: true });
      D.opener = null;
    }
    D.layer = layerOpen;
    applyHl();
  }
  // окно и шторка: Escape закрывает, Tab не уходит под них
  document.addEventListener("keydown", (e) => {
    if (!S.modal && !S.sheet) return;
    if (e.key === "Escape") { e.preventDefault(); if (S.modal) { if (S.modal.onClose) S.modal.onClose(); S.modal = null; } else S.sheet = false; render(); return; }
    if (e.key !== "Tab") return;
    const box = document.querySelector("#layers [role=dialog]");
    if (!box) return;
    const f = [...box.querySelectorAll("button:not([disabled]), input, textarea, [tabindex]:not([tabindex='-1'])")].filter((x) => x.offsetParent !== null);
    if (!f.length) return;
    const a = f[0], z = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === a || !box.contains(document.activeElement))) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && (document.activeElement === z || !box.contains(document.activeElement))) { e.preventDefault(); a.focus(); }
  });
  setInterval(() => {
    for (const el of document.querySelectorAll("[data-since]")) el.textContent = mmss(now() - Number(el.getAttribute("data-since")));
  }, 1000);

  // ── Рельс, полоса телефона, шторка ──────────────────────────────────────────
  function leftRange(fromWelcome) {
    const st = arr(S.rail && S.rail.stages);
    if (fromWelcome || cur() === "welcome") return [st.reduce((a, x) => a + x.range[0], 0), st.reduce((a, x) => a + x.range[1], 0)];
    return (S.rail && S.rail.left) || [S.rail.left_min, S.rail.left_min];
  }
  function railKids(inSheet) {
    const p = S.product || {}, rl = S.rail, st = arr(rl.stages), w = rl.welcome || {}, atWelcome = cur() === "welcome";
    const lr = leftRange();
    const out = [];
    if (!inSheet) {
      out.push(h("div", { class: "qs-brand" }, h("span", { class: "qs-logo" }, icon("logo", 22)),
        h("div", { class: "qs-brand__t" }, h("span", { class: "qs-brand__name", text: p.name || "Kaskad PRO" }),
          ip() ? h("span", { class: "qs-brand__ip", text: ip() }) : null)));
      out.push(h("div", { class: "qs-rail__progress" }, h("span", { class: "qs-caption", text: t("rail.caption") }),
        h("span", { class: "qs-rail__t", text: atWelcome ? t("rail.entry", { n: st.length, w: tn("c.stages", st.length) }) : t("rail.stepOf", { i: rl.position, n: rl.total }) }),
        h("span", { class: "qs-rail__left", text: t("rail.left", { range: rng(lr), w: tn("c.minutes", lr[1]) }) }),
        h("progress", { class: "qs-progress", max: String(rl.total || 1), value: String(atWelcome ? 0 : rl.position), "aria-label": t("rail.stepOf", { i: rl.position, n: rl.total }) })));
    }
    const wState = atWelcome ? "current" : (w.state === "done" ? "done" : "ahead");
    out.push(h("button", { type: "button", class: "qs-welcome", "data-state": wState, "aria-current": atWelcome ? "step" : null,
      "aria-label": t("rail.welcomeCap") + " — " + t("rail.welcomeSub"), "data-k": "rail-welcome",
      on: { click: () => { S.sheet = false; if (!atWelcome && S.st && S.st.password_set) open("welcome"); } } },
    h("span", { class: "qs-caption", text: t("rail.welcomeCap") }), h("span", { text: t("rail.welcomeSub") }),
    wState === "done" ? icon("check", 14) : null, h("span", { class: "qs-welcome__ic" }, icon("lock", 18))));
    out.push(h("ol", { class: "qs-steps" }, st.map((x) => {
      const can = x.state === "done" || x.state === "skipped" || x.state === "current";
      const sub = x.state === "current" ? x.sub : x.state === "skipped" ? t("rail.skipped") : x.state === "done" ? "" : t("rail.ahead", { range: rng(x.range) });
      return h("li", null, h("button", { type: "button", class: "qs-step", "data-state": x.state, "aria-current": x.state === "current" ? "step" : null,
        "aria-disabled": can ? null : "true", title: can ? null : t("rail.aheadLocked"), "data-k": "rail-" + x.kind,
        "aria-label": x.n + ". " + x.title + (sub ? " — " + sub : x.state === "done" ? " — " + t("rail.passed") : ""),
        on: { click: () => { if (!can) return; S.sheet = false; if (x.state !== "current") open(x.kind); else render(); } } },
      h("span", { class: "qs-step__n", "aria-hidden": "true", text: String(x.n) }),
      h("span", { class: "qs-step__b" }, stepTitle(x.title, x.state === "done"),
        sub ? h("span", { class: "qs-step__d" + (x.state === "ahead" ? " qs-step__d--mono" : ""), text: sub }) : null)));
    })));
    if (!inSheet) {
      out.push(h("div", { class: "qs-rail__fill" }));
      out.push(themeBtn(false));
      out.push(h("div", { class: "qs-rail__ssh" }, h("span", { text: t("rail.ssh") }), h("code", { text: p.ssh || "gokaskad" })));
    }
    out.push(langSwitch());
    return out;
  }
  // Язык: один на сервер (KASKAD_LANG — SSH-меню, бот, панель, мастер). Смена — шаг lang (только с сессией мастера:
  // рельс виден лишь после входа), затем перезагрузка: словарь и тексты сервера берутся при загрузке страницы.
  // На экранах входа и ссылки (S.sys, сессии нет) тот же переключатель меняет только язык этой страницы — без
  // запроса к серверу (язык сервера без сессии не меняется); выбор запоминает браузер до входа (keepPrefLang).
  function langSwitch() {
    const local = !!S.sys;
    const pick = async (v) => {
      if (v === LANG || S.langBusy) return;
      S.langBusy = true; render();
      if (local) {
        let done = false;
        try { done = await usePageLang(v); } catch (e) { done = false; }
        if (done) keepPrefLang(v);
        S.langBusy = false;
        if (!done) toast(t("sys.error"));
        render();
        return;
      }
      let r = null;
      try { r = await api("lang", { lang: v }); } catch (e) { r = null; }
      if (r && r.status === 200 && r.body && r.body.ok !== false) { location.reload(); return; }
      S.langBusy = false;
      toast((r && r.body && r.body.error) || t("sys.error"));
      render();
    };
    return h("div", { class: "qs-lang", role: "group", "aria-label": t("c.lang") },
      [["ru", "RU", t("c.langRu")], ["en", "EN", t("c.langEn")]].map(([v, code, name]) => h("button", { type: "button", class: "qs-lang__b",
        "aria-pressed": LANG === v ? "true" : "false", "aria-label": name, title: name, lang: v, "data-k": "lang-" + v, disabled: S.langBusy && LANG !== v,
        on: { click: () => pick(v) } }, code)));
  }
  // название этапа и галочка «пройден»: галочка — за последним словом и не уходит одна на новую строку (T-M6-qs330 №15)
  function stepTitle(title, done) {
    const words = String(title || "").split(" "), last = words.pop();
    if (!done) return h("span", { class: "qs-step__t" }, title);
    return h("span", { class: "qs-step__t" }, words.length ? words.join(" ") + " " : "", h("span", { class: "qs-step__last" }, last, icon("check", 14)));
  }
  function barKids() {
    const rl = S.rail, st = arr(rl.stages), c = stageOf(cur());
    const title = cur() === "welcome" ? t("rail.phoneEntry") : t("rail.phoneStep", { i: rl.position, n: rl.total, t: c ? c.title : "" });
    return [h("span", { class: "qs-logo" }, icon("logo", 18)),
      h("div", { class: "qs-bar__b" }, h("span", { class: "qs-bar__t", text: title }),
        h("span", { class: "qs-bar__dots", "aria-hidden": "true" }, st.map((x) => h("i", { "data-state": x.state })))),
      h("span", { class: "qs-bar__left", text: t("rail.leftShort", { range: rng(leftRange()) }) }),
      themeBtn(true),
      btn("", "tertiary", () => { S.sheet = true; render(); }, { icon: "chevron-down", sm: true, aria: t("rail.open"), cls: "ga-btn--icon", k: "bar-open" })];
  }
  function sheet() {
    const close = () => { S.sheet = false; render(); };
    return h("div", null, h("div", { class: "qs-backdrop", on: { click: close } }),
      h("div", { class: "qs-sheet", role: "dialog", "aria-modal": "true", "aria-label": t("rail.open") },
        h("div", { class: "qs-sheet__head" }, h("b", { text: t("rail.open") }), h("button", { type: "button", class: "gx-close", "aria-label": t("c.close"), "data-k": "sheet-close", on: { click: close } }, icon("xmark", 16))),
        railKids(true)));
  }

  // ── Шапка этапа и плашки системы ────────────────────────────────────────────
  function head(kind) {
    const p = S.product || {}, rl = S.rail, c = stageOf(kind);
    let cap, title, why;
    if (kind === "welcome") {
      const noDom = arr(rl.stages).filter((x) => x.kind !== "domain").reduce((a, x) => a + x.range[1], 0);
      cap = t("head.welcomeCap", { name: p.name || "Kaskad PRO" }); title = t("head.welcome"); why = t("head.welcomeWhy", { n: noDom });
    } else {
      const k = kind === "done" && classic() ? "doneClassic" : kind;
      cap = t("head.cap", { i: pad2(c ? c.n : 0), n: pad2(rl.total), range: rng(c ? c.range : null) });
      title = t("head." + k); why = t("head." + k + "Why");
    }
    return h("header", { class: "qs-head" }, h("div", { class: "qs-head__top" }, h("span", { class: "qs-caption", text: cap }), videoLink(kind)),
      h("h1", { class: "qs-h1", text: title }), h("p", { class: "qs-why", text: why }));
  }
  function banners() {
    const out = [];
    if (!S.net.online) {
      out.push(note("danger", t("sys.offline"), "", { icon: "xmark", extra: h("span", null, t("sys.offlineB1") + " · ", h("span", { class: "qs-mono", "data-since": S.net.since }, mmss(now() - S.net.since)), ". " + t("sys.offlineB2")),
        btns: [btn(t("sys.offlineCheck"), "inverse", () => checkNet(true), { sm: true, k: "net-check" })] }));
    }
    return out;
  }
  // нет связи со страницей: липкая плашка над прокруткой. Мастер открыт по IP, а сертификат домена уже выпускался —
  // значит, адрес по IP больше не открывается (без сертификата на IP сервер отдаёт ему доменный): ведём на домен.
  function alerts() {
    if (!S.noConn) return [];
    if (S.move && !onDomain()) {
      return [note("warning", t("sys.ipGone"), t("sys.ipGoneB", { d: S.move.host }), { extra: [code(S.move.clean, { copyLabel: t("c.copy") }),
        h("div", { class: "qs-btns" }, btn(t("sys.ipGoneGo"), "inverse", () => goMove(), { sm: true, k: "move-go-noconn" }))] })];
    }
    return [note("warning", t("sys.noConn"), t("sys.noConnB"))];
  }

  // ── Видео ───────────────────────────────────────────────────────────────────
  // 3.3.3 (UX-333): колонки «Видео к шагу» нет. В шапке этапа — маленькая ссылка на ролик (откроется отдельно), пока
  // ролика нет — некликабельное «▶ Видео — скоро»; у этапа без ролика — ничего.
  function videoLink(kind) {
    const v = ((S.product && S.product.videos) || {})[kind];
    if (!v) return null;
    if (v.src) return h("a", { class: "qs-head__vid", href: v.src, target: "_blank", rel: "noopener", "data-k": "vid-open" }, icon("play", 12), t("vid.watch", { dur: v.dur || "" }));
    return h("span", { class: "qs-head__vid", "aria-disabled": "true", "data-k": "vid-soon" }, icon("play", 12), t("vid.soon"));
  }
  function applyHl() {
    for (const el of document.querySelectorAll("[data-hl-active]")) el.removeAttribute("data-hl-active");
    if (S.hl) for (const el of document.querySelectorAll('[data-hl-target="' + S.hl + '"]')) el.setAttribute("data-hl-active", "");
  }

  // ── Нижняя панель ───────────────────────────────────────────────────────────
  function prevKind() {
    const st = arr(S.rail.stages), i = st.findIndex((x) => x.kind === cur());
    return i > 0 ? st[i - 1].kind : "welcome";
  }
  function footKids(f) {
    const out = [], k = cur();
    const busy = !!S.busy.stage;
    if (f.back !== false && k !== "welcome") {
      out.push(h("button", { type: "button", class: "ga-btn ga-btn--tertiary qs-foot__back", "aria-label": t("c.back"), "data-k": "foot-back", disabled: busy || jobRunning(),
        on: { click: () => (typeof f.back === "function" ? f.back() : open(prevKind())) } },
      h("span", { class: "qs-foot__back-ic" }, icon("chevron-left", 18)), h("span", { class: "qs-foot__back-t", text: t("c.back") })));
    }
    out.push(h("span", { class: "qs-foot__spacer" }));
    const n = f.next || {};
    if (n.hint) out.push(h("span", { class: "qs-foot__hint", role: "status" }, icon("lock", 14), n.hint));
    // «Пропустить» — только там, где она не то же самое, что «Дальше» (3.3.3, UX-333): этап сам её просит (f.skip:
    // true — импорт: найденное не переносим). Во время задания её нет совсем; на коротком запросе этапа она не
    // пропадает и не гаснет (R-333 П5: на ≤ 400 px погашенная пряталась — нижняя панель дёргалась), второе нажатие —
    // ничего не делает
    if (f.skip === true && k !== "welcome" && k !== "done" && !jobRunning()) {
      // пока идёт любой запрос этапа («Установить» нажата, задание ещё не началось) — нажатие ничего не делает (R-333 П-5)
      out.push(btn(t("c.skip"), "ghost", () => { if (!Object.keys(S.busy).some((x) => S.busy[x])) skip(); }, { k: "foot-skip" }));
    }
    // подпись одна на все ширины (T-332 №38): «короткой» подписи для телефона нет — шаг называется одинаково везде
    const label = n.label || t("c.next");
    out.push(btn(label, "primary", () => (n.act ? n.act() : next()), { disabled: !!n.disabled || busy, busy: !!n.busy || busy, k: "foot-next" }));
    return out;
  }

  // ── Системные экраны ────────────────────────────────────────────────────────
  function showSys(kind, o) {
    S.sys = Object.assign({ kind }, o || {});
    Object.keys(S.timers).forEach(stop);
    if (es && kind !== "login") { try { es.close(); } catch (e) { /* уже закрыт */ } es = null; }
    render();
  }
  async function sessionLost() {
    try {
      const r = await api("session", undefined, { raw: true });
      const b = r.body || {};
      S.session = b;
      if (r.status === 404) { showSys("closed"); return; }
      showSys(b.reason === "expired" ? "expired" : b.reason === "password" ? "login" : "link");
    } catch (e) { showSys("link"); }
  }
  function sysScreen() {
    const k = S.sys.kind, cmd = (S.session && S.session.cmd) || S.sys.cmd || "kaskadctl setup link", ssh = (S.session && S.session.ssh) || "gokaskad";
    const box = (cap, title, kids, tone) => h("div", { class: "qs-sys" }, h("section", { class: "qs-sys__box", role: "main", "aria-labelledby": "qs-sys-t" },
      h("div", { class: "qs-sys__head" }, h("span", { class: "qs-sys__cap", "data-tone": tone || null, text: cap }), langSwitch()),
      h("h1", { class: "qs-sys__title", id: "qs-sys-t", text: title }), kids));
    if (k === "expired") return box(t("sys.expiredCap"), t("sys.expired"), [h("p", { class: "qs-muted", text: t("sys.expiredB") }), code(cmd, { copyLabel: t("c.copy") }), h("p", { class: "qs-small", text: t("sys.expiredS") })]);
    if (k === "used" || k === "unknown") return box(t("sys.usedCap"), t(k === "used" ? "sys.used" : "sys.unknown"), [h("p", { class: "qs-muted", text: t(k === "used" ? "sys.usedB" : "sys.unknownB") }), code(cmd, { copyLabel: t("c.copy") }), h("p", { class: "qs-small", text: t("sys.linkS", { ssh }) })]);
    if (k === "limit") return box(t("sys.limitCap"), t("sys.limit"), [h("p", { class: "qs-muted", text: t("sys.limitB") })]);
    if (k === "closed") return box(t("sys.closedCap"), t("sys.closed"), [h("p", { class: "qs-muted", text: t("sys.closedB") }), code(cmd, { copyLabel: t("c.copy") }),
      h("div", { class: "qs-btns" }, h("a", { class: "ga-btn ga-btn--primary", href: "/", text: t("sys.openPanel") }))], "accent");
    if (k === "login") return loginBox(box);
    if (k === "noconn") return box(t("sys.noConn"), t("sys.noConnT"), [h("p", { class: "qs-muted", text: t("sys.noConnB") }),
      h("div", { class: "qs-btns" }, btn(t("c.retryShort"), "primary", () => { S.sys = null; render(); boot(); }, { k: "sys-retry" }))]);
    return box(t("sys.linkCap"), t("sys.link"), [h("p", { class: "qs-muted", text: t("sys.linkB") }), code(cmd, { copyLabel: t("c.copy") }), h("p", { class: "qs-small", text: t("sys.linkS", { ssh }) })], "accent");
  }
  function loginBox(box) {
    const u = ui("login");
    const go = async () => {
      if (!u.pw || u.busy) return;
      u.busy = true; u.err = ""; render();
      try {
        const r = await api("login", { password: u.pw }, { raw: true });
        const d = (r.body && r.body.data) || {};
        if (r.status === 200) { u.pw = ""; S.sys = null; u.busy = false; await start(); return; }
        u.err = r.status === 401 ? (d.left !== undefined ? t("sys.loginLeft", { n: d.left }) : t("sys.loginBad"))
          : (r.status === 429 || r.status === 503) ? t("sys.loginWait", { n: d.retry_after || 60 })
            : r.status === 410 ? "" : (r.body.error || t("sys.error"));
        if (r.status === 410) { showSys("expired"); return; }
        if (r.status === 409) { showSys("link"); return; }
      } catch (e) { u.err = e.message; }
      u.busy = false; render();
    };
    const inp = h("input", { class: "gx-input", type: "password", autocomplete: "current-password", "aria-label": t("sys.login"), "data-k": "login-pw", value: u.pw || "",
      "aria-invalid": u.err ? "true" : null, "aria-describedby": u.err ? "login-err" : null,
      on: { input: (e) => { u.pw = e.target.value; }, keydown: (e) => { if (e.key === "Enter") go(); } } });
    return box(t("sys.loginCap"), t("sys.login"), [h("p", { class: "qs-muted", text: t("sys.loginB") }),
      h("div", { class: "qs-field__row" }, inp, btn(t("sys.loginBtn"), "primary", go, { busy: u.busy, k: "login-go" })),
      u.err ? h("p", { class: "qs-err", id: "login-err", role: "alert", text: u.err }) : null], "accent");
  }

  // ── Окно поверх этапа ───────────────────────────────────────────────────────
  function modal() {
    const m = S.modal, close = () => { if (m.onClose) m.onClose(); S.modal = null; render(); };
    return h("div", { class: "qs-modal", on: { click: (e) => { if (e.target === e.currentTarget) close(); } } },
      h("div", { class: "qs-modal__box" + (m.cls ? " " + m.cls : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": "qs-modal-t" },
        // m.icon — окно-вопрос (согласие, цена VLESS; макет М3, Ц4): значок над заголовком, без крестика — выход
        // кнопкой «Назад»/«Отмена», Esc или щелчком мимо, как у окон-стопок панели (DQA-3378 М3)
        h("div", { class: "qs-modal__head" + (m.icon ? " qs-modal__head--ic" : "") }, h("div", null, m.icon ? h("span", { class: "gx-dlg__ic qs-modal__ic", "aria-hidden": "true" }, icon(m.icon, 20)) : null,
          h("h2", { class: "qs-modal__title", id: "qs-modal-t", text: m.title }), m.sub ? h("span", { class: "qs-small", text: m.sub }) : null),
          m.icon ? null : h("button", { type: "button", class: "gx-close", "aria-label": t("c.close"), "data-k": "modal-close", on: { click: close } }, icon("xmark", 16))),
        h("div", { class: "qs-modal__body" }, m.body()),
        m.foot ? h("div", { class: "qs-modal__foot" }, m.foot(close)) : null));
  }

  // ── Общие куски этапов ──────────────────────────────────────────────────────
  const VIEW = {};
  const card = (a, ...kids) => h("section", Object.assign({ class: "qs-card" }, a || {}), kids);
  function cardHead(title, sub, o) {
    o = o || {};
    return h("div", { class: "qs-card__h" }, o.icon ? h("span", { class: "qs-card__ic", "data-tone": o.tone || null }, icon(o.icon, 18)) : null,
      h("div", { class: "qs-card__tt" }, h("h2", { class: "qs-card__title", text: title }), sub ? h("span", { class: "qs-card__sub", text: sub }) : null),
      o.end ? h("div", { class: "qs-card__end" }, o.end) : null);
  }
  function choice(o) {
    const role = o.radio ? "radio" : "checkbox";
    const b = h("button", { type: "button", class: "qs-choice" + (o.line ? " qs-choice--line" : "") + (o.cls ? " " + o.cls : ""), role,
      "aria-checked": o.on ? "true" : "false", "aria-disabled": o.disabled ? "true" : null, "data-k": o.k || null, "data-hl-target": o.hl || null,
      on: { click: () => { if (!o.disabled && o.pick) o.pick(); } } }, o.kids);
    return b;
  }
  const box = () => h("span", { class: "qs-choice__box", "aria-hidden": "true" }, icon("check", 14));
  const radio = () => h("span", { class: "qs-choice__radio", "aria-hidden": "true" });
  function field(label, input, hint, o) {
    o = o || {};
    const id = "f-" + (input.getAttribute("data-k") || Math.random().toString(36).slice(2));
    input.id = id;
    return h("div", { class: "qs-field", "data-hl-target": o.hl || null }, h("label", { for: id, text: label }), o.row ? h("div", { class: "qs-field__row" }, input, o.row) : input,
      o.err ? h("span", { class: "qs-err", role: "alert", text: o.err }) : null, hint ? h("span", { class: "qs-field__h", text: hint }) : null);
  }
  function input(k, value, onInput, o) {
    o = o || {};
    return h(o.area ? "textarea" : "input", { class: "gx-input" + (o.mono ? " gx-input--mono" : "") + (o.area ? " qs-area" : ""), type: o.area ? null : (o.type || "text"),
      "data-k": k, value: value || "", placeholder: o.ph || null, autocomplete: o.ac || "off", spellcheck: "false", autocapitalize: "off",
      inputmode: o.mode || null, maxlength: o.max ? o.max * 2 : null, rows: o.rows || null, "aria-invalid": o.bad ? "true" : null, "aria-label": o.aria || null, disabled: !!o.disabled,
      // max — знаков (кодовых точек), как сервер (names.MAX): эмодзи — две единицы UTF-16, лишнее срезается здесь
      on: { input: (e) => { if (o.max && !e.isComposing) { const a = Array.from(e.target.value); if (a.length > o.max) e.target.value = a.slice(0, o.max).join(""); }
        onInput(e.target.value); if (o.live !== false) render(); }, keydown: (e) => { if (e.key === "Enter" && o.enter && !o.area) o.enter(); } } });
  }
  // строки живой проверки (HANDOFF §5): значок · что · подробность · время/действие
  const ROW_IC = { ok: "check", warn: "exclamation", err: "xmark" };
  function liveRow(r, o) {
    o = o || {};
    const wait = r.state === "wait";
    return h("li", { "data-state": r.state || "idle" },
      h("span", { class: "qs-live__icon", "aria-hidden": "true" }, ROW_IC[r.state] ? icon(ROW_IC[r.state], 13) : null),
      h("div", { class: "qs-live__text" }, h("div", { class: "qs-live__t", text: r.title || r.item || "" }),
        r.detail ? h("div", { class: "qs-live__detail", text: r.detail }) : null),
      wait && r.since ? h("span", { class: "qs-live__time", "data-since": r.since }, mmss(now() - r.since)) : null,
      o.act ? h("span", { class: "qs-live__act" }, o.act) : null);
  }
  function liveList(title, rows, o) {
    o = o || {};
    const start = o.started || (rows[0] && rows[0].since) || 0;
    return h("ul", { class: "qs-live", "aria-live": "polite", "data-hl-target": o.hl || null },
      title ? h("li", { class: "qs-live__head qs-live__first" }, h("span", { text: title }),
        o.count ? h("span", { text: o.count }) : (o.timer && start ? h("span", { "data-since": start }, mmss(now() - start)) : null)) : null,
      rows.map((r, i) => { const li = liveRow(r, { act: o.acts && o.acts[i] }); if (!title && i === 0) li.classList.add("qs-live__first"); return li; }));
  }
  function jobFor(names) { return S.job && names.indexOf(S.job.step) >= 0 ? S.job : null; }
  function running(names) { const j = jobFor(names); return j && (j.state === "starting" || j.state === "running") ? j : null; }
  function resNote(res, o) {
    o = o || {};
    if (!res) return null;
    const st = res.state;
    if (st === "ok") return o.ok === false ? null : note("success", res.text || o.okText || t("c.done"), "", { btns: o.okBtns });
    if (st === "partial") return note("warning", res.text, arr((res.data || {}).failed).map((x) => x.text).join(" · ") || arr((res.data || {}).warnings).join(" · "), { btns: o.warnBtns });
    if (st === "unavailable") return note("info", "", (res.error && res.error.text) || res.text || t("c.soon"));
    if (st === "offline") return note("danger", t("sys.offline"), res.text);
    if (st === "need_input") return note("warning", res.text, "", { btns: actionBtns(res, o) });
    if (st === "checking") return note("info", res.text);
    const e = res.error || {};
    return note("danger", e.text || res.text || t("sys.error"), "", { btns: actionBtns(res, o).concat(o.retry ? [btn(t("c.retryShort"), "inverse", o.retry, { sm: true })] : []) });
  }
  function actionBtns(res, o) {
    const a = res && res.error && res.error.action;
    if (!a || !a.label || !o.onAction) return [];
    return [btn(a.label, "inverse", () => o.onAction(a), { sm: true })];
  }
  // Предупреждения удачного шага (data.warnings: «выданные ссылки VLESS не подключатся — выдайте заново», почта
  // Let's Encrypt, порт 80…) — плашкой «Внимание» (T-M6-qs330e №1: сервер их отдавал, страница молчала). Итог шага —
  // из state.job.result (S.res[шаг] заполняет loadState), поэтому плашка переживает перезагрузку и переезд мастера на
  // новый домен. data.reissue — кому выдать заново: кнопка ведёт туда, где выдаются настройки.
  function warnNote(res, o) {
    o = o || {};
    const d = (res && res.ok && res.data) || {}, list = arr(d.warnings).filter((w) => typeof w === "string" && w);
    if (!list.length) return null;
    const who = arr(d.reissue);
    return note("warning", t("c.attention"), list.length === 1 ? list[0] : "", { hl: o.hl,
      extra: list.length > 1 ? list.map((w) => h("span", { text: w })) : null,
      btns: (who.length ? reissueBtns(who) : []).concat(o.btns || []) });
  }
  // «Выдать заново»: своё устройство мастера (этап «Готово», создано на нём) — туда, вкладка «Подключить» с тем же
  // входом; чужие устройства (или своего нет) — панель → «Люди и устройства» (новая вкладка: мастер остаётся открытым)
  function reissueBtns(who) {
    const a = (S.st && S.st.answers) || {}, mine = !!a.device && who.some((x) => x.id === a.device);
    const others = who.filter((x) => x.id !== a.device), out = [];
    if (mine) out.push(btn(t("dm.reissue"), "inverse", () => { const d = ui("done"); d.tab = "connect"; d.issue = null; d.reissue = true; open("done"); }, { sm: true, k: "reissue" }));
    if (others.length) {
      const focus = others.length === 1 && /^d-[0-9a-f]{8}$/.test(others[0].id) ? "?focus=" + others[0].id : "";
      out.push(h("a", { class: "ga-btn ga-btn--" + (mine ? "tertiary" : "inverse") + " ga-btn--sm", href: "/#people" + focus, target: "_blank", rel: "noopener", "data-k": "reissue-panel" },
        h("span", { text: t(mine ? "dm.reissuePanel" : "dm.reissue") })));
    }
    return out;
  }
  function stuckRunning() { return S.streamLost ? h("p", { class: "qs-small", text: t("c.streamLost") }) : null; }
  function runningNote(j) { return j ? [h("div", { class: "qs-running", role: "status" }, t("c.running")), stuckRunning()] : []; }
  // строки задания не менялись 30 с: поток мог оборваться без ошибки — сверяемся опросом; плашку показываем, только если
  // поток и правда не открыт (сервер шлёт «: hb» комментарием — EventSource его не отдаёт)
  function watchStream() {
    if (!jobRunning() || !lastEvent || now() - lastEvent < 30) return;
    if ((!es || es.readyState !== 1) && !S.streamLost) { S.streamLost = true; render(); }
    lastEvent = now();
    pollJob();
  }
  setInterval(watchStream, 5000);

  // ── 0. Вход ────────────────────────────────────────────────────────────────
  ENTER.welcome = () => { if (!S.ipcert) loadIpcert(); };
  async function loadIpcert() {
    try { const r = await api("ipcert"); if (r.status === 200) { S.ipcert = r.body; render(); } } catch (e) { /* плашка останется «проверяю» */ }
  }
  function strength(pw) {
    if (!pw) return 0;
    if (pw.length < 10) return 1;
    let sc = 0;
    if (pw.length >= 12) sc++;
    if (pw.length >= 16) sc++;
    if (/[a-zа-яё]/.test(pw) && /[A-ZА-ЯЁ]/.test(pw)) sc++;
    if (/\d/.test(pw)) sc++;
    if (/[^A-Za-zА-Яа-яЁё0-9]/.test(pw)) sc++;
    return sc <= 1 ? 2 : sc <= 3 ? 3 : 4;
  }
  function genPw() {
    const ch = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789", a = new Uint32Array(16);
    crypto.getRandomValues(a);
    return Array.from(a, (x) => ch[x % ch.length]).join("");
  }
  function ipcertNote() {
    const c = S.ipcert;
    if (!c) return note("info", t("w.certLoading"), "", { hl: "trust", icon: "lock" });
    // текст сервера «главное · подробности» (SPEC-setup-api §1.5): до « · » — жирным, дальше — приглушённо
    const txt = String(c.text || ""), i = txt.indexOf(" · "), head = i > 0 ? txt.slice(0, i) : txt, tail = i > 0 ? txt.slice(i + 3) : "";
    if (c.serving || c.domain) return h("p", { class: "qs-small qs-line", "data-hl-target": "trust" }, h("span", { class: "qs-dot", "data-tone": "success" }, icon("lock", 12)), h("span", { text: head }));
    return note("warning", head, tail, { hl: "trust", icon: "lock", extra: c.fingerprint ? h("span", { class: "qs-note__fp", text: t("w.fp", { fp: c.fingerprint }) }) : null });
  }
  VIEW.welcome = () => {
    const u = ui("welcome"), pwSet = !!(S.st && S.st.password_set);
    const main = [];
    const lv = strength(u.pw || ""), ok = (u.pw || "").length >= 10;
    if (pwSet) main.push(note("success", t("w.pwSet"), t("w.pwSetS"), { hl: "pw" }));
    else {
      const pw = input("pw", u.pw, (v) => { u.pw = v; u.err = ""; u.gen = false; }, { type: u.show ? "text" : "password", ph: t("w.pwPh"), ac: "new-password",
        aria: t("w.pw"), bad: !!u.err || ((u.pw || "").length > 0 && !ok), enter: () => submitPw(), max: 200 });
      main.push(card({ "data-hl-target": "pw" }, h("div", { class: "qs-row" }, h("h2", { class: "qs-card__title", text: t("w.pw") }), h("span", { class: "qs-small", text: t("w.pwFor") })),
        h("div", { class: "qs-pw" }, h("div", { class: "qs-pw__box" }, pw,
          btn(u.show ? t("c.hide") : t("c.show"), "ghost", () => { u.show = !u.show; render(); }, { sm: true, cls: "qs-pw__show", k: "pw-show", aria: u.show ? t("c.hide") : t("c.show") })),
        btn(t("w.pwGen"), "tertiary", () => { u.pw = genPw(); u.show = true; u.gen = true; u.err = ""; render(); }, { k: "pw-gen" })),
        h("div", { class: "qs-meter", "data-lv": String(lv), "aria-live": "polite" }, h("i"), h("i"), h("i"), h("i"), h("span", { text: t("w.lv" + lv) })),
        u.err ? h("p", { class: "qs-err", role: "alert", text: u.err }) : null,
        u.gen ? note("warning", t("w.pwGenNote"), "", { icon: "key" }) : null));
    }
    main.push(ipcertNote());
    const nextKind = (arr(S.rail.stages).find((x) => x.state !== "done" && x.state !== "skipped") || arr(S.rail.stages)[0] || {}).kind;
    return { main, foot: { next: pwSet ? { label: t("c.next"), act: () => open(nextKind) } : { label: t("w.start"), disabled: !ok, busy: !!S.busy.password, act: submitPw, hint: ok ? "" : (u.pw ? t("w.hintShort") : t("w.hintEmpty")) } } };
  };
  async function submitPw() {
    const u = ui("welcome");
    if ((u.pw || "").length < 10 || S.busy.password) return;
    const res = await step("password", { password: u.pw });
    if (res.ok) { u.pw = ""; u.err = ""; u.show = false; await loadState(); }
    else u.err = (res.error && res.error.text) || res.text || t("sys.error");
    render();
  }

  // ── 1. Импорт ──────────────────────────────────────────────────────────────
  const IMP = ["import", "import-undo", "import-file"];
  ENTER.import = () => {
    const u = ui("import");
    if (!u.view) u.view = "found";
    if (S.job && IMP.indexOf(S.job.step) >= 0) u.last = S.job.step;
    reload("scan");
  };
  RELOAD.scan = async () => {
    const res = await step("scan");
    const u = ui("import"), sc = (res && res.data) || {};
    if (!u.sel) { u.sel = {}; arr(sc.found).forEach((f) => { if (f.move && !f.done) u.sel[f.id] = true; }); }
    render();
  };
  const FILE_MAX = 700 * 1024;                           // тело запроса сервера — до 1 МБ (panel/server.py MAX_BODY), JSON с файлом внутри
  // копия настроек (.kbk) — до 16 МБ, как в панели (backup.FILE_MAX): её шаг «backup-open» сервер читает до 20 МБ
  // (panel/server.py SETUP_BIG_BODY_PATHS) — только при живой сессии мастера
  const BACKUP_FILE_MAX = 16 * 1024 * 1024;
  function readFile(file, kind) {
    const u = ui("import"), f = { kind, name: file.name, size: file.size, err: "", text: "" };
    u.file = f;
    const max = kind === "backup" ? BACKUP_FILE_MAX : FILE_MAX;
    if (file.size > max) { f.err = t("imp.fileBig", { n: size(max) }); render(); return; }
    const rd = new FileReader();
    rd.onload = () => {
      f.text = String(rd.result || "");
      if (kind === "awg") { try { f.doc = JSON.parse(f.text); } catch (e) { f.err = t("imp.fileBad"); } }
      render();
    };
    rd.onerror = () => { f.err = t("imp.fileBad"); render(); };
    rd.readAsText(file);
  }
  function uploadCard(kind, avail, soonText) {
    const u = ui("import"), f = u.file && u.file.kind === kind ? u.file : null;
    const pick = h("input", { type: "file", class: "qs-vh", tabindex: "-1", "aria-hidden": "true", accept: kind === "awg" ? ".json,application/json" : null,
      on: { change: (e) => { if (e.target.files[0]) readFile(e.target.files[0], kind); } } });
    const title = kind === "awg" ? t("imp.fileTitle") : t("imp.bakTitle"), sub = kind === "awg" ? t("imp.fileSub") : t("imp.bakSub");
    const kids = [cardHead(title, sub, { icon: "file-text" })];
    if (!avail) kids.push(note("info", "", soonText || t("c.soon")));
    else if (!f) {
      // одна строка: «Выбрать файл» у заголовка, файл можно и бросить на карточку (3.3.2 №16 — без большой зоны)
      kids[0] = cardHead(title, sub, { icon: "file-text", end: [btn(t("imp.dropPick"), "tertiary", () => pick.click(), { sm: true, k: "pick-" + kind }), pick] });
      return card({ "data-hl-target": "other", class: "qs-card qs-dropcard", "data-k": "drop-" + kind,
        on: { dragover: (e) => { e.preventDefault(); e.currentTarget.setAttribute("data-over", ""); }, dragleave: (e) => e.currentTarget.removeAttribute("data-over"),
          drop: (e) => { e.preventDefault(); e.currentTarget.removeAttribute("data-over"); if (e.dataTransfer.files[0]) readFile(e.dataTransfer.files[0], kind); } } }, kids);
    } else {
      kids.push(h("div", { class: "qs-file" }, icon("file-text", 18), h("div", null, h("b", { text: f.name }),
        h("span", { class: "qs-small", text: t("imp.fileSize", { size: size(f.size), kind: kind === "awg" ? t("imp.fileJson") : t("imp.fileEnc") }) })),
      btn(t("imp.fileOther"), "tertiary", () => { u.file = null; u.bakPw = ""; render(); }, { sm: true, k: "file-other-" + kind })));
      if (f.err) kids.push(note("danger", f.err));
      const rk = kind === "awg" ? "import-file" : "backup-open", res = S.res[rk];
      if (kind === "backup") kids.push(field(t("imp.filePw"), input("bak-pw", u.bakPw, (v) => { u.bakPw = v; }, { type: "password", ac: "off",
        bad: !!(res && res.state === "error" && u.last === rk && res.error && res.error.field === "password"), enter: () => openBackup(f) }), t("imp.bakPwHint")));
      if (res && res.state !== "ok" && u.last === rk) kids.push(resNote(res));
      if (!f.err) kids.push(h("div", { class: "qs-btns" }, btn(kind === "awg" ? t("imp.fileGo") : t("imp.fileOpen"), "primary", async () => {
        if (kind === "backup") { await openBackup(f); return; }
        u.last = "import-file";
        await step("import-file", { kind: "awg", doc: f.doc });
      }, { disabled: !!running(IMP) || (kind === "backup" && !(u.bakPw || "").length), busy: !!S.busy[rk], k: "file-go-" + kind })));
    }
    return card({ "data-hl-target": "other" }, kids);
  }
  function previewGroups(sc) {
    const u = ui("import"), found = arr(sc.found), k2 = found.find((f) => f.id === "k2"), plan = sc.plan || {};
    const sel = k2 && u.sel && u.sel.k2;
    const g = (title, tone, items) => h("div", { class: "qs-group", "data-tone": tone },
      h("div", { class: "qs-group__h" }, h("b", { text: title }), h("span", { text: items.length + " " + tn("c.items", items.length) })),
      items.map((it) => h("div", { class: "qs-group__i" }, h("b", { text: it[0] }), it[1] ? h("span", { text: it[1] }) : null)));
    const move = sel ? [[t("imp.forwards", { n: plan.imports || 0 }), t("imp.forwardsS")], [k2.title, k2.detail]] : [[t("imp.nothingSel"), t("imp.nothingSelS", { t: k2 ? k2.title : "Kaskad 2.x" })]];
    const repl = sel ? [[t("imp.k2Replace"), t("imp.k2ReplaceS")]] : [];
    const keep = found.filter((f) => !f.move && !f.upload).map((f) => [f.title, f.detail]);
    return [h("section", { class: "qs-card qs-card--flush", "data-hl-target": "preview" }, g(t("imp.gMove"), "success", move), repl.length ? g(t("imp.gReplace"), "warning", repl) : null, keep.length ? g(t("imp.gKeep"), "idle", keep) : null),
      plan.preview ? note("info", "", plan.preview) : null,
      arr(plan.blockers).length ? note("danger", arr(plan.blockers).map((b) => (b && b.text) || String(b)).join(" · ")) : null,
      h("div", { class: "qs-row" }, h("span", { class: "qs-small", text: t("imp.undoNote") }), btn(t("imp.change"), "tertiary", () => { u.view = "found"; render(); }, { k: "imp-change" }))];
  }
  VIEW["import"] = () => {
    const u = ui("import"), scan = S.res.scan, sc = (scan && scan.data) || null, j = running(IMP), lastRes = u.last ? S.res[u.last] : null;
    const main = [];
    if (!scan || S.busy.scan && !sc) return { main: [card(null, h("p", { class: "qs-muted", text: t("c.loading") }))], foot: { next: { busy: true, label: t("c.next"), disabled: true } } };
    if (scan.state !== "ok" && !(sc && sc.found)) return { main: [resNote(scan, { retry: () => reload("scan") })], foot: {} };
    const found = arr(sc.found), k2 = found.find((f) => f.id === "k2");
    const sel = !!(k2 && k2.move && !k2.done && u.sel && u.sel.k2);
    // ход переноса, итог и «Вернуть как было»
    const showLive = j || (lastRes && (u.last === "import" || u.last === "import-undo"));
    if (showLive) {
      const job = jobFor(IMP);
      const rows = j ? arr(j.items) : arr(lastRes && lastRes.items).length ? lastRes.items : arr(job && job.items);
      const title = u.last === "import-undo" ? (j ? t("imp.undoing") : t("imp.undone")) : (j ? t("imp.liveRun") : t("imp.liveDone"));
      main.push(liveList(title, rows, { timer: true, started: job && job.started, hl: "found" }));
      main.push(...runningNote(j));
      if (!j && lastRes) {
        const undoable = u.last === "import" && lastRes.data && lastRes.data.undo;
        main.push(resNote(lastRes, { okBtns: undoable ? [undoBtn()] : null, warnBtns: undoable ? [undoBtn()] : null, retry: () => startImport() }));
      }
      if (j || u.last === "import") return { main, foot: { next: j ? { label: u.last === "import-undo" ? t("imp.undoing") : t("imp.applying"), busy: true } : {} } };
    }
    if (u.view === "preview") return { main: previewGroups(sc), foot: { back: () => { u.view = "found"; render(); }, skip: true, next: { label: t("imp.apply"), act: startImport, busy: !!S.busy.import } } };
    if (u.view === "backup" && u.bak) return backupView(u);
    // найдено на сервере; «Чистый сервер» — только если на нём и правда ничего нет (T-M6-qs330 №4)
    const cards = found.filter((f) => !f.upload), here = sc.here || {}, hereItems = arr(here.items);
    if (sc.clean) main.push(note("success", t("imp.clean"), t("imp.cleanB"), { hl: "found" }));
    else if (hereItems.length) main.push(note("info", t("imp.here"), t("imp.hereB", { list: here.text || hereItems.map((x) => x.text).join(", ") }), { hl: "found", icon: "server" }));
    else if (!cards.length) main.push(note("info", t("imp.checkedOnly"), t("imp.checkedOnlyB"), { hl: "found" }));
    if (cards.length) {
      main.push(h("span", { class: "qs-label", text: t("imp.found", { n: cards.length }) }));
      cards.forEach((f) => {
        const b = h("div", { class: "qs-choice__b" }, h("span", { class: "qs-choice__t", text: f.title }), h("span", { class: "qs-found__d", text: f.detail }));
        if (f.move && !f.done) {
          main.push(choice({ on: !!(u.sel && u.sel[f.id]), k: "imp-" + f.id, hl: "found", line: true,
            pick: () => { u.sel = Object.assign({}, u.sel, { [f.id]: !(u.sel && u.sel[f.id]) }); render(); },
            kids: [b, h("span", { class: "qs-small", text: t("imp.move") }), box()] }));
        } else {
          main.push(h("div", { class: "qs-choice qs-choice--line", "data-keep": "", "data-hl-target": f.move ? "found" : "keep" }, b,
            chip(f.done ? t("imp.moved") : t("imp.keep"), f.done ? "success" : "off")));
        }
      });
    }
    found.filter((f) => f.upload).forEach((f) => main.push(uploadCard(f.id === "awgfile" ? "awg" : "backup", f.available !== false, f.detail)));
    if (lastRes && u.last === "import-file" && !u.bakGo) {
      const jf = running(["import-file"]);
      if (jf || lastRes.state === "ok") main.push(liveList(jf ? t("imp.liveRun") : t("imp.liveDone"), arr(jf ? jf.items : lastRes.items), { timer: true }), resNote(lastRes), jf ? null : warnNote(lastRes));
    }
    const fileJob = running(["import-file"]);
    // «Пропустить» — когда есть что перенести: «Дальше» тогда ведёт к переносу, а пропуск — мимо него
    const movable = cards.some((f) => f.move && !f.done);
    return { main, foot: { skip: movable && !fileJob, next: fileJob ? { label: t("imp.applying"), busy: true } : sel ? { label: t("imp.nextPreview"), act: () => { u.view = "preview"; render(); } } : {} } };
  };
  // ── Копия настроек: открыть (предпросмотр, система не меняется) → «Перенести» с подтверждением потерь ─────────
  // Пароль уходит только в запросе «backup-open» и сразу стирается со страницы; расшифрованная копия ждёт на сервере
  // (/run, 0600, 15 минут) под билетом — странице остаются билет и список «что изменится».
  async function openBackup(f) {
    const u = ui("import"), pw = u.bakPw || "";
    if (!pw || !f || !f.text || S.busy["backup-open"]) return;
    u.bakPw = "";
    u.last = "backup-open";
    const r = await step("backup-open", { file: f.text, password: pw });
    if (r && r.data && r.data.ticket) { f.text = ""; showBackup(r.data); }
  }
  function showBackup(d) {
    const u = ui("import");
    u.bak = d; u.bakOk = false; u.bakGo = false; u.view = "backup";
    delete S.res["import-file"];
    render();
  }
  async function reopenBackup() {
    const u = ui("import");
    if (!u.bak) return;
    u.last = "backup-open";
    const r = await step("backup-open", { ticket: u.bak.ticket });
    if (r && r.data && r.data.ticket) showBackup(r.data);
    else { toast((r && r.error && r.error.text) || t("sys.error")); backupAgain(); }
  }
  function closeBackup() {
    const u = ui("import");
    if (u.bak) api("step/backup-open", { drop: true }).catch(() => {});
    u.bak = null; u.bakOk = false; u.bakGo = false;
  }
  function backupAgain() {                             // билет истёк — файл заново
    const u = ui("import");
    closeBackup();
    u.file = null; u.view = "found"; u.last = null;
    render();
  }
  async function applyBackup() {
    const u = ui("import"), b = u.bak;
    if (!b || running(IMP) || arr(b.blockers).length || (b.confirm && !u.bakOk)) return;
    u.last = "import-file"; u.bakGo = true;
    delete S.res["import-file"];
    await step("import-file", { kind: "backup", ticket: b.ticket, confirm: !!u.bakOk });
  }
  const dayOf = (iso) => DATES.date(iso);         // «09.01.2027» / «Jan 9, 2027»
  function backupView(u) {
    const b = u.bak, j = running(["import-file"]), res = u.bakGo ? S.res["import-file"] : null;
    const back = () => { closeBackup(); u.file = null; u.view = "found"; u.last = null; render(); };      // файл — заново (пароль стёрт)
    if (u.bakGo && (j || res)) {                         // перенос идёт или закончился
      const rows = j ? arr(j.items) : arr(res && res.items), failed = !j && res && !res.ok;
      const main = failed && !rows.length ? [] : [liveList(j ? t("imp.liveRun") : failed ? t("imp.liveStopped") : t("imp.liveDone"), rows, { timer: true, started: j && j.started, hl: "other" }), ...runningNote(j)];
      if (!j && res) {
        const code = (res.error && res.error.code) || "";
        if (res.ok) main.push(resNote(res), note("info", "", t("imp.bakUndo")));
        else if (code === "changed") main.push(note("danger", res.error.text, "", { btns: [btn(t("imp.bakReopen"), "inverse", reopenBackup, { sm: true, busy: !!S.busy["backup-open"], k: "bak-reopen" })] }));
        else if (code === "expired") main.push(note("danger", res.error.text, "", { btns: [btn(t("imp.bakAgain"), "inverse", backupAgain, { sm: true, k: "bak-again" })] }));
        else main.push(resNote(res, { retry: () => { u.bakGo = false; render(); } }));
      }
      if (j) return { main, foot: { back: false, skip: false, next: { label: t("imp.applying"), busy: true } } };
      if (res && res.ok) return { main, foot: { back: () => { u.bak = null; u.bakGo = false; u.file = null; u.view = "found"; u.last = null; render(); } } };
      return { main, foot: { back, next: { label: t("imp.apply"), disabled: true } } };
    }
    const g = (title, tone, items, k) => items.length ? h("div", { class: "qs-group", "data-tone": tone, "data-k": k },
      h("div", { class: "qs-group__h" }, h("b", { text: title }), h("span", { text: items.length + " " + tn("c.items", items.length) })),
      items.map((it) => h("div", { class: "qs-group__i" }, h("b", { text: it[0] }), it[1] ? h("span", { text: it[1] }) : null))) : null;
    const parts = arr(b.parts), cap = (x) => String(x || "").charAt(0).toUpperCase() + String(x || "").slice(1);
    const move = parts.filter((p) => p.action !== "none").map((p) => [t("imp.p." + p.part), p.text]);
    const absent = parts.filter((p) => p.action === "none").map((p) => [t("imp.p." + p.part), p.text]);
    const lost = arr(b.lost).map((x) => [cap(x.text)]), changed = arr(b.changed).map((x) => [cap(x.text)]);
    const meta = [u.file && u.file.name, b.created ? t("imp.bakFrom", { day: dayOf(b.created) }) : "", b.version ? "Kaskad " + b.version : ""].filter(Boolean).join(" · ");
    const main = [card({ "data-hl-target": "other" }, cardHead(t("imp.bakOpened"), meta, { icon: "hard-drive" }),
      h("p", { class: "qs-muted", text: b.empty ? t("imp.bakEmpty") : b.confirm ? t("imp.bakLoss") : t("imp.bakNoLoss") })),
    h("section", { class: "qs-card qs-card--flush", "data-hl-target": "preview" }, g(t("imp.gLost"), "danger", lost, "bak-lost"),
      g(t("imp.gReplace"), "warning", changed, "bak-changed"), g(t("imp.gMove"), "success", move, "bak-move"), g(t("imp.gAbsent"), "idle", absent, "bak-absent"))];
    arr(b.warnings).forEach((w) => main.push(note("warning", "", w)));
    if (arr(b.blockers).length) main.push(note("danger", t("imp.bakBlocked"), arr(b.blockers).join(" ")));
    if (res && !res.ok) main.push(resNote(res));
    const lostWords = arr(b.lost).map((x) => x.text.split(":")[0]).concat(arr(b.changed).length ? [t("imp.bakChangedN", { n: arr(b.changed).length })] : []).join(", ");
    if (b.confirm && !arr(b.blockers).length) {
      main.push(choice({ on: !!u.bakOk, k: "bak-ok", line: true, pick: () => { u.bakOk = !u.bakOk; render(); },
        kids: [h("div", { class: "qs-choice__b" }, h("span", { class: "qs-choice__t", text: t("imp.bakConfirm") }), h("span", { class: "qs-found__d", text: lostWords })), box()] }));
    }
    main.push(h("p", { class: "qs-small", text: t("imp.bakExpires") }));
    const blocked = !!arr(b.blockers).length, need = b.confirm && !u.bakOk;
    return { main, foot: { back, skip: false, next: { label: t("imp.bakGo"), act: applyBackup, disabled: blocked || need, busy: !!S.busy["import-file"],
      hint: blocked ? t("imp.bakBlocked") : need ? t("imp.bakNeedOk") : "" } } };
  }
  function undoBtn() {
    return btn(t("imp.undo"), "inverse", async () => { ui("import").last = "import-undo"; await step("import-undo", {}); }, { sm: true, busy: !!S.busy["import-undo"], k: "imp-undo" });
  }
  async function startImport() {
    const u = ui("import");
    u.last = "import";
    u.view = "found";
    await step("import", {});
  }

  // ── 2. Режим и протоколы (узел) · Пробросы (классический) ───────────────────
  // Мини-схемы режимов (3.3.3, снимок владельца 2026-10-12): плашки — HTML по ширине своего текста с полями, линии —
  // отдельные элементы между плашками (никогда под ними), всё по центру карточки; в EN длинные слова не вылезают.
  // data-lay — метки для проверки раскладки (tests/setup/layout_scan.mjs: линия не пересекает плашку и текст).
  // плашка сервера — второй строкой его адрес (3.3.3, владелец 2026-10-12): публичный IP из состояния мастера, иначе
  // адрес, по которому открыта страница
  const diaP = (label, on, addr) => addr ? h("span", { class: "qs-dia__p qs-dia__p--srv" + (on ? " qs-dia__p--on" : ""), "data-lay": "box" },
    h("span", { class: "qs-dia__n", text: label }), h("span", { class: "qs-dia__ip", "data-k": "dia-ip", text: addr }))
    : h("span", { class: "qs-dia__p" + (on ? " qs-dia__p--on" : ""), "data-lay": "box", text: label });
  const nodeAddr = () => ip() || location.hostname || "";
  const diaL = () => h("i", { class: "qs-dia__l", "data-lay": "line" });
  // веер линий: из точек слева (ys, доли высоты) в точки справа; растягивается по высоте столбца плашек
  function diaFan(from, to) {
    const d = [];
    from.forEach((a) => to.forEach((b) => { d.push("M0 " + a + "C50 " + a + " 50 " + b + " 100 " + b); }));
    return s("svg", { class: "qs-dia__fan", "data-lay": "line", viewBox: "0 0 100 100", preserveAspectRatio: "none" },
      s("path", { d: d.join(""), class: "qs-dia__flow", "vector-effect": "non-scaling-stroke" }));
  }
  function miniClassic() {
    return h("div", { class: "qs-dia qs-mode__pic", "aria-hidden": "true" },
      diaP(t("mode.picDevice")), diaL(), diaP(t("mode.picServer"), true, nodeAddr()), diaL(), diaP(t("mode.picOther")));
  }
  function miniNode() {
    // три устройства слева (центры плашек — 1/6, 3/6, 5/6 высоты), два выхода справа (столбец по центру)
    const col = (labels) => h("span", { class: "qs-dia__col" }, labels.map((x) => diaP(x)));
    return h("div", { class: "qs-dia qs-dia--node qs-mode__pic", "aria-hidden": "true" },
      col([t("mode.picPhone"), t("mode.picLaptop"), t("mode.picRouter")]), diaFan([100 / 6, 50, 500 / 6], [50]),
      diaP(t("mode.picNode"), true, nodeAddr()), diaFan([50], [100 * 23 / 72, 100 * 49 / 72]),
      col([t("mode.picExit", { n: 1 }), t("mode.picExit", { n: 2 })]));
  }
  // Выбор режима живёт на странице до «Дальше» (T-M6-qs330b №18): карточка не пишет setup.json — рельс и отметки
  // этапов не меняются от щелчка; «Дальше» сохраняет режим и открывает его этап (протоколы или проброс)
  const savedMode = () => (S.st && S.st.mode) || "node";
  function modePending() {
    const want = S.modePick;
    if (!want || want === savedMode()) return null;
    return { main: [modeCards(), note("info", t("mode.pendT", { m: t(want === "classic" ? "mode.classic" : "mode.node") }), t("mode.pendB"), { hl: "mode" })],
      foot: { next: { busy: !!S.busy.mode, act: applyMode } } };
  }
  async function applyMode() {
    const want = S.modePick;
    if (!want || want === savedMode()) { S.modePick = null; render(); return; }
    const r = await step("mode", { mode: want });
    if (r.ok) { S.modePick = null; await loadState(); }
  }
  function modeCards() {
    const m = S.modePick || savedMode();
    const pick = (mode) => { if (S.busy.mode) return; S.modePick = mode === savedMode() ? null : mode; render(); };
    const c = (id, pic, title, desc, rec) => choice({ radio: true, on: m === id, k: "mode-" + id, pick: () => pick(id),
      kids: [pic, h("span", { class: "qs-choice__row" }, radio(), h("span", { class: "qs-choice__t qs-card__title", text: title })), h("span", { class: "qs-choice__d", text: desc }),
        rec ? h("span", { class: "qs-choice__tags" }, chip(t("c.recommended"), "accent")) : null] });
    return h("div", { class: "qs-grid-2 qs-grid-modes", role: "radiogroup", "aria-label": t("head.protocols"), "data-hl-target": "mode" },
      c("classic", miniClassic(), t("mode.classic"), t("mode.classicD")), c("node", miniNode(), t("mode.node"), t("mode.nodeD"), true));
  }
  ENTER.protocols = () => { reload("protocols-plan"); };
  RELOAD["protocols-plan"] = async () => {
    const res = await step("protocols-plan");
    const u = ui("protocols"), d = (res && res.data) || {};
    if (!u.sel && res.ok) {
      u.sel = {};
      const def = arr(d.defaults);
      arr(d.protocols).forEach((p) => { if (p.supported !== false && (p.installed || def.indexOf(p.kind) >= 0)) u.sel[p.kind] = true; });
    }
    render();
  };
  // порт, который встанет на деле: занятый заменяем на свободный (его и передаём установке); случайный (AmneziaWG)
  // установка тянет сама — «порт выберем сами», а не число-образец (T-M6-qs330 №11)
  function portText(p) {
    if (!p.port) return arr(p.fixed).map((f) => f.port).join(", ") + (arr(p.fixed).length ? "/" + (p.fixed[0].proto || "") : "");
    const busy = arr(p.port.conflicts).length;
    if (p.port.random && !busy && !p.installed) return t("pr.portAuto", { t: p.title });
    return (busy && p.port.free ? p.port.free : p.port.port) + "/" + arr(p.port.protos).join(",");
  }
  function ghCard(gh, after) {
    const u = ui("gh"), res = S.res["github-key"];
    const save = async (force) => { const r = await step("github-key", { key: (u.key || "").trim(), force: !!force }); if (r.ok) { u.key = ""; if (after) after(); } };
    const missing = gh.state === "none";
    const kids = [cardHead(missing ? t("pr.ghMissing") : t("pr.ghBad"), missing ? t("pr.ghMissingS") : t("pr.ghBadS"), { icon: "key", tone: "warning" }),
      h("div", { class: "qs-field__row" }, input("gh-key", u.key, (v) => { u.key = v; }, { mono: true, ph: matchMedia("(max-width: 400px)").matches ? "github_pat_…" : t("pr.ghPh"), aria: t("pr.ghPh"), bad: !!(res && !res.ok), enter: () => save(false), type: "password" }),
        btn(t("c.check"), "tertiary", () => save(false), { busy: !!S.busy["github-key"], disabled: !(u.key || "").trim(), k: "gh-check" }))];
    if (res) kids.push(res.ok ? note(res.state === "partial" ? "warning" : "success", res.state === "partial" ? res.text : t("pr.ghSaved")) : resNote(res, { onAction: (a) => save(!!a.force) }));
    return card({ "data-hl-target": "gh" }, kids);
  }
  VIEW.protocols = () => {
    const pend = modePending();
    if (pend) return pend;
    const u = ui("protocols"), res = S.res["protocols-plan"], d = (res && res.data) || null, j = running(["protocols"]), done = S.res.protocols;
    const main = [modeCards()];
    if ((j || done) && u.view !== "pick") {
      const job = jobFor(["protocols"]), rows = arr(j ? j.items : (done && arr(done.items).length ? done.items : job && job.items));
      const okN = rows.filter((r) => r.state === "ok" || r.state === "warn" || r.state === "err").length;
      main.push(liveList(j ? t("pr.liveRun") : t("pr.liveDone"), rows, { count: t("pr.liveOf", { i: okN, n: rows.length }) }));
      main.push(...runningNote(j));
      if (!j && done) main.push(resNote(withoutBusy(done), { retry: () => installProtocols() }), ...busyNotes(done), h("div", { class: "qs-btns" }, btn(t("pr.more"), "ghost", () => { u.view = "pick"; render(); }, { k: "pr-more" })));
      return { main, foot: { next: j ? { label: t("pr.installing"), busy: true } : {} } };
    }
    if (!d) {
      if (res && res.state !== "ok") main.push(resNote(res, { retry: () => reload("protocols-plan") }));
      else main.push(...protoSkel());
      // план не пришёл — «Повторить» в плашке и «Пропустить» внизу: этап не тупик (R-333 С4)
      return { main, foot: { skip: !!res && res.state !== "ok", next: { label: t("pr.install"), disabled: true, busy: !res } } };
    }
    const list = arr(d.protocols), sel = u.sel || {}, chosen = list.filter((p) => sel[p.kind]);
    main.push(h("div", { class: "qs-row" }, h("h2", { class: "qs-card__title", text: t("pr.title") }), h("span", { class: "qs-small", text: t("pr.count", { n: chosen.length, m: list.length }) })));
    // отказ шлюза — под заголовком списка, а не над ним: заголовок и сетка стоят там же, где стояли их заготовки
    if (d.gateway && d.gateway.supported === false) main.push(note("danger", t("pr.gatewayNo", { why: d.gateway.reason || "" })));
    main.push(h("div", { class: "qs-grid-protos", "data-hl-target": "protos" }, list.map((p) => {
      const off = p.supported === false, on = !!sel[p.kind];
      const conf = p.port && arr(p.port.conflicts)[0];
      const why = off ? t("pr.unsupported", { why: p.why_not || "" }) : p.why;
      return choice({ on, disabled: off || p.installed, k: "pr-" + p.kind, cls: "qs-choice--proto", pick: () => { u.sel = Object.assign({}, sel, { [p.kind]: !on }); render(); },
        // 3.3.3: название целиком в одну строку; метки — своей строкой внизу карточки, одинаково у всех; описание —
        // полностью, без многоточия. Строки карточек одного ряда — общая сетка (subgrid в qs.css): описания, жёлтые
        // строки и метки соседей стоят на одной высоте
        kids: [h("span", { class: "qs-choice__row" }, box(), h("span", { class: "qs-choice__t", text: p.title })),
          h("span", { class: "qs-choice__d", text: why }),
          (conf || p.warning) ? h("span", { class: "qs-choice__warns" },
            conf ? h("span", { class: "qs-choice__warn", text: p.port.free ? t("pr.portBusy", { port: p.port.port, who: conf.detail, free: p.port.free }) : t("pr.portBusyNoFree", { port: p.port.port, who: conf.detail }) }) : null,
            p.warning ? h("span", { class: "qs-choice__warn", text: p.warning }) : null) : null,
          (arr(p.tags).length || p.installed) ? h("span", { class: "qs-choice__tags" }, p.installed ? chip(t("pr.installed"), "success") : null,
            arr(p.tags).map((tg) => chip(tg, tg === t("pr.tagRec") ? "accent" : tg === t("pr.tagWeak") ? "warning" : "tag"))) : null] });
    })));
    // порты для файрвола хостера — в конце, списком из настоящих портов сервера (3.3.3, UX-333 §7: PortsSummary);
    // здесь — только что так будет (план портов — подсказкой при наведении)
    const awg = chosen.some((p) => p.awg && !p.installed), gh = d.github || {};
    const ports = chosen.map(portText).filter(Boolean).join(" · ");
    main.push(h("p", { class: "qs-small qs-line qs-ports", "data-hl-target": "summary", title: ports || null }, h("span", { class: "qs-dot", "data-tone": "warning" }, icon("shield", 12)),
      h("span", { text: ports ? t("pr.fw") : t("pr.pickOne") })));
    if (awg && gh.ask) main.push(ghCard(gh, () => reload("protocols-plan")));
    if (done && u.view === "pick") main.push(resNote(done, { ok: false }));
    const todo = chosen.filter((p) => !p.installed);
    // «Пропустить» — пока «Дальше» значит «Установить» (или закрыта): пропуск идёт дальше без установки
    return { main, foot: { skip: !!todo.length || !chosen.length, next: todo.length ? { label: t("pr.install"), act: installProtocols, busy: !!S.busy.protocols } : chosen.length ? {} : { label: t("pr.install"), disabled: true, hint: t("pr.pickOne") } } };
  };
  // Место под список, пока план не пришёл (3.3.2 №3: после «Смотрю, что можно поставить…» список вставал на место
  // строки, страница прыгала, и щелчок по VLESS попадал в PPTP). Заготовка — тот же заголовок, та же сетка из стольких
  // же карточек (по словам карточек продукта) той же высоты и строка портов: список встаёт на их место.
  function protoSkel() {
    const n = Object.keys((S.product && S.product.protocol_cards) || {}).length || 12;
    const sk = (cls) => h("div", { class: cls + " qs-sk" }, h("i"), h("i"));
    return [h("div", { class: "qs-row" }, h("h2", { class: "qs-card__title", text: t("pr.title") }),
      h("span", { class: "qs-small", role: "status", "aria-live": "polite", text: t("pr.loading") })),
    h("div", { class: "qs-grid-protos", "data-hl-target": "protos", "aria-busy": "true" }, Array.from({ length: n }, () => sk("qs-choice qs-choice--proto"))),
    h("p", { class: "qs-small qs-line qs-ports qs-sk", "data-hl-target": "summary", "aria-hidden": "true" }, h("i"))];
  }
  // R-333 П2: порт протокола занят другой программой — с экрана понятно, что за протокол, почему, что сделать и где
  // нажать: команда для SSH (если держит служба с простым именем), «Установить снова», «Не ставить {протокол}»
  const busyOf = (res) => arr(res && res.data && res.data.failed).filter((f) => f && f.code === "port_busy");
  function withoutBusy(res) {
    const busy = busyOf(res);
    if (!busy.length || res.state !== "partial") return res;
    return Object.assign({}, res, { data: Object.assign({}, res.data, { failed: arr(res.data.failed).filter((f) => busy.indexOf(f) < 0) }) });
  }
  function busyNotes(res) {
    const u = ui("protocols");
    // команда — из данных сервера: «stop» — остановить службу, что держит порт; «look» — посмотреть, кто держит
    // (служба не определилась или она общая — сайт, SSH): «disable» наугад не предлагаем (R-333 С1)
    return busyOf(res).map((f) => note("warning", f.text, t(f.do === "look" ? "pr.busyLook" : "pr.busyDo"), { extra: f.cmd ? code(f.cmd, { copyLabel: t("c.copy") }) : null,
      btns: [btn(t("pr.again"), "inverse", () => installProtocols(), { sm: true, busy: !!S.busy.protocols, k: "pr-again-" + f.kind }),
        btn(t("pr.busyDrop", { t: f.title || f.kind }), "tertiary", () => { u.sel = Object.assign({}, u.sel, { [f.kind]: false }); u.view = "pick"; render(); }, { sm: true, k: "pr-drop-" + f.kind })] }));
  }
  async function installProtocols() {
    const u = ui("protocols"), d = (S.res["protocols-plan"] || {}).data || {}, sel = u.sel || {};
    const todo = arr(d.protocols).filter((p) => sel[p.kind] && !p.installed && p.supported !== false);
    if (!todo.length) return next();
    const ports = {};
    todo.forEach((p) => { if (p.port && arr(p.port.conflicts).length && p.port.free) ports[p.kind] = p.port.free; });
    u.view = "live";
    await step("protocols", Object.keys(ports).length ? { kinds: todo.map((p) => p.kind), ports } : { kinds: todo.map((p) => p.kind) });
  }

  // классический Каскад: первый проброс
  const isIp = (v) => /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(String(v || "").trim());
  const isPort = (v) => /^\d{1,5}$/.test(String(v || "")) && +v >= 1 && +v <= 65535;
  let fwTimer = 0;
  function fwCheckSoon() {
    clearTimeout(fwTimer);
    const u = ui("forward");
    u.check = null;
    if (!isIp(u.to) || !isPort(u.toPort)) return;
    fwTimer = setTimeout(async () => {
      const key = [u.to, u.toPort, u.proto].join("|");
      const r = await step("forward-check", { to: u.to.trim(), to_port: u.toPort, proto: u.proto || "tcp" });
      if (key === [u.to, u.toPort, u.proto].join("|")) { u.check = r; render(); }
    }, 700);
  }
  ENTER.forward = () => {
    const u = ui("forward"), f = ((S.st && S.st.answers) || {}).forward;
    if (!u.port) Object.assign(u, { proto: "tcp", port: "8443", to: "", toPort: "443" });
    if (f && f.to && !S.res.forward) {                   // проброс уже создан (перезагрузка) — показать его
      Object.assign(u, { proto: f.proto, port: String(f.port), to: f.to, toPort: String(f.to_port) });
      S.res.forward = { ok: true, state: "ok", text: t("fw.made") + ": " + t("d.fwMadeS", { p: f.port, proto: f.proto, to: f.to + ":" + f.to_port }), data: { forward: f } };
    }
  };
  VIEW.forward = () => {
    const pend = modePending();
    if (pend) return pend;
    const u = ui("forward"), res = S.res.forward, made = (res && res.ok && res.data && res.data.forward) || null;
    const main = [modeCards()];
    const dom = stageOf("domain"), noSite = ((S.st && S.st.answers) || {}).domain_plan === "none" || (dom && dom.state === "skipped");
    const p443 = (u.proto || "tcp") === "tcp" && String(u.port) === "443" && !noSite;   // как сервер: 443 нужен сайту, пока домен не отменён
    const bad = { port: u.port && !isPort(u.port), to: u.to && !isIp(u.to), toPort: u.toPort && !isPort(u.toPort) };
    const onIn = (k) => (v) => { u[k] = v.replace(/[^\d.]/g, "").slice(0, k === "to" ? 15 : 5); S.res.forward = null; if (k !== "port") fwCheckSoon(); };
    const kids = [cardHead(t("fw.title"), t("fw.sub")),
      h("div", { class: "gx-seg", role: "group", "aria-label": "TCP / UDP" }, ["tcp", "udp"].map((pr) => h("button", { type: "button", "aria-pressed": (u.proto || "tcp") === pr ? "true" : "false", "data-k": "fw-" + pr,
        on: { click: () => { u.proto = pr; fwCheckSoon(); render(); } } }, pr.toUpperCase()))),
      h("div", { class: "qs-grid-fit" },
        field(t("fw.here"), input("fw-port", u.port, onIn("port"), { mono: true, mode: "numeric", bad: bad.port || p443 })),
        field(t("fw.to"), input("fw-to", u.to, onIn("to"), { mono: true, mode: "decimal", ph: "192.0.2.4", bad: bad.to })),
        field(t("fw.toPort"), input("fw-toport", u.toPort, onIn("toPort"), { mono: true, mode: "numeric", bad: bad.toPort })))];
    if (p443) kids.push(note("warning", t("fw.443")));
    if (bad.port || bad.toPort) kids.push(h("p", { class: "qs-err", text: t("fw.badPort") }));
    if (bad.to) kids.push(h("p", { class: "qs-err", text: t("fw.badIp") }));
    if (u.check) kids.push(u.check.ok ? note(u.check.data && u.check.data.checked === false ? "info" : "success", u.check.text, "", { hl: "fwdcheck" }) : note("danger", u.check.text || (u.check.error || {}).text, "", { hl: "fwdcheck" }));
    else if (S.busy["forward-check"]) kids.push(note("info", t("fw.checking", { to: u.to + ":" + u.toPort })));
    if (isPort(u.port) && isIp(u.to) && isPort(u.toPort)) kids.push(h("p", { class: "qs-small" }, t("fw.hint", { addr: (ip() || "IP") + ":" + u.port, to: u.to + ":" + u.toPort })));
    if (res) kids.push(made ? note("success", res.text, "", { btns: [btn(t("fw.another"), "inverse", () => { S.res.forward = null; Object.assign(u, { port: "", to: "", toPort: "", check: null }); render(); }, { sm: true })] })
      : resNote(res, { onAction: (a) => createForward(a) }));
    main.push(card({ "data-hl-target": "fwd" }, kids));
    const ready = isPort(u.port) && isIp(u.to) && isPort(u.toPort);
    // подсказка у серой кнопки — что не так: неверный адрес / порт, а не «заполните», когда поле заполнено (T-M6-qs330 №15)
    const hint = ready ? "" : bad.to ? t("fw.badAddr") : bad.port || bad.toPort ? t("fw.badPortShort") : t("fw.fill");
    const fwDone = made || stepState("forward") && stepState("forward").state === "ok" && !ready;
    return { main, foot: { skip: !fwDone, next: fwDone ? {} : { label: t("fw.create"), disabled: !ready, hint, busy: !!S.busy.forward, act: () => createForward() } } };
  };
  async function createForward(flags) {
    const u = ui("forward");
    const body = Object.assign({ proto: u.proto || "tcp", port: u.port, to: (u.to || "").trim(), to_port: u.toPort }, flags && flags.force ? { force: true } : {}, flags && flags.force_443 ? { force_443: true } : {});
    await step("forward", body);
  }

  // ── 3. Выходы ──────────────────────────────────────────────────────────────
  ENTER.exits = () => {
    const u = ui("exits"), a = ((S.st && S.st.answers) || {}).exits;
    // 3.3.3 (UX-333): два варианта — «Сейчас без выхода» и «Через выходы». Прежний ответ «без выхода» или «Позже»
    // (3.3.2) решаем по данным, а не по слову (R-333 С2): выходы добавили потом в панели и маршрут идёт через них —
    // это «Через выходы», и «Дальше» маршрут не трогает; «Позже» тем выходам маршрут оставлял (D1). Пока ответа нет —
    // ни один вариант не выбран, «Дальше» закрыта.
    if (!u.choice || u.choice === "later") {
      if (a === "direct" || a === "later") { u.choice = ""; u.fromData = true; } else u.choice = "exits";
    }
    if (u.choice === "exits") startExitsPoll(); else reload("exits");    // список нужен и без выходов: строка «Нет сервера…»
  };
  // «Через выходы», если выходы есть и маршрут идёт через них (не «напрямую» и не «блокировать»), иначе «без выхода»
  function exitsByData(d) {
    const via = ((d && d.route) || {}).via || "pool";
    return arr(d && d.exits).length && via !== "direct" && via !== "block" ? "exits" : "direct";
  }
  function startExitsPoll() { reload("exits"); every("exits", 10, () => { if (!running(["exits-add"])) reload("exits"); }); }
  RELOAD.exits = async () => {                         // опрос: сбой одного ответа не стирает список ключей
    try {
      const r = await api("step/exits", {});
      if (r.status === 200 && r.body.ok) {
        S.res.exits = r.body;
        const u = ui("exits");
        u.loadFail = false;
        if (u.fromData && !u.choice) { u.fromData = false; u.choice = exitsByData(r.body.data); if (u.choice === "exits") startExitsPoll(); }
        render();
      } else exitsLoadFail();
    } catch (e) { exitsLoadFail(); }                     // связь вернётся — следующий опрос
  };
  // первый ответ не пришёл — вместо вечного «Загружаю…» повтор и подсказка (R-333 П-10); список уже есть — молча
  function exitsLoadFail() { if (!S.res.exits) { ui("exits").loadFail = true; render(); } }
  function exitsData() { const r = S.res.exits; return (r && r.data) || null; }
  function routeCard(d) {
    const u = ui("exits"), r = (d && d.route) || {}, via = r.via || "pool", fail = r.fail || "block";
    const alive = arr(d && d.exits).filter((e) => e.usable), one = alive[0] || arr(d && d.exits)[0];
    const set = async (nv, nf) => { const res = await step("route", { via: nv, fail: nf }); if (res.ok && res.data && res.data.route && S.res.exits && S.res.exits.data) S.res.exits.data.route = res.data.route; u.routeText = res.text || ""; render(); };
    const pill = (on, label, act, k) => h("button", { type: "button", class: "qs-pill", "aria-pressed": on ? "true" : "false", "data-k": k, on: { click: act } }, label);
    const viaOne = /^exit:/.test(via) ? via : null, oneName = viaOne ? ((arr(d && d.exits).find((e) => "exit:" + e.id === via) || {}).name || via.slice(5)) : (one && one.name);
    const hint = via === "pool" ? t("ex.rPoolH") : via === "direct" ? t("ex.rDirectH") : t("ex.rOneH", { n: oneName || "" });
    return card({ "data-hl-target": "route" }, h("h2", { class: "qs-card__title", text: t("ex.route") }),
      h("div", { class: "qs-pills", role: "group", "aria-label": t("ex.route") }, pill(via === "pool", t("ex.rPool"), () => set("pool", fail), "rt-pool"),
        one ? pill(!!viaOne, t("ex.rOne", { n: oneName }), () => set(viaOne || "exit:" + one.id, fail), "rt-one") : null,
        pill(via === "direct", t("ex.rDirect"), () => set("direct", fail), "rt-direct")),
      h("p", { class: "qs-small", text: hint }), h("hr", { class: "qs-hr" }),
      h("h2", { class: "qs-card__title", text: t("ex.fail") }),
      h("div", { class: "qs-pills", role: "group", "aria-label": t("ex.fail") }, pill(fail === "block", t("ex.fBlock"), () => set(via, "block"), "fl-block"), pill(fail === "direct", t("ex.fDirect"), () => set(via, "direct"), "fl-direct")),
      fail === "block" ? null : note("warning", "", u.routeText || t("ex.fDirectH")));
  }
  // ── общие компоненты (ux.js): выходы, бот, ключ агента ─────────────────────
  // Тот же код, что в панели (3.3.3, UX-333, OWNER-FEEDBACK №35–37, №45). Компонент живёт между отрисовками мастера
  // (секрет в поле не теряется, фокус возвращает restore); ушли с этапа — снимается (dropUx), поле стирается.
  const UX = () => window.KaskadUX || null;
  // ответ шага мастера -> отказ в форме ApiError панели: message, data.reason (код), остальное data шага (cert_domain…)
  function stepErr(r) {
    const e = (r && r.error) || {};
    return Object.assign(new Error(e.text || (r && r.text) || t("sys.error")), { code: e.code || "error", data: Object.assign({}, (r && r.data) || {}, { reason: e.code || "error" }) });
  }
  function dropUx() {
    ["exits", "extras"].forEach((st) => {
      const u = S.ui[st];
      if (!u || !u.ux) return;
      // в u.ux только компоненты (язык каждого — в u.uxLang): строка здесь роняла draw() до нижней панели (R-333i Б1)
      Object.keys(u.ux).forEach((k) => { const c = u.ux[k]; if (!c || !c.el || !c.el.isConnected) { try { if (c && c.destroy) c.destroy(); } catch (e) { /* уже */ } delete u.ux[k]; if (u.uxLang) delete u.uxLang[k]; } });
    });
  }
  // «Нет сервера за рубежом?» — KaskadUX.noServer: партнёрские хостинги из product.links.hosting (одно место адресов —
  // product.HOSTING; панель берёт их же через GET /api/hosting, №45); инструкция — только настоящая (links.exit_guide)
  function noServerLine() {
    const X = UX(), links = (S.product && S.product.links) || {};
    const node = X ? X.noServer({ lang: LANG, hosting: arr(links.hosting), guide: links.exit_guide ? { href: links.exit_guide } : null }) : null;
    return node ? h("div", { class: "qs-noserver", "data-k": "ex-noserver" }, node) : null;
  }
  // ключ или подписка — шаг «exits-add» (долгий: ход строками на странице, итог — по заданию); Promise для ExitsInput
  function exitsAdd(text) {
    const u = ui("exits");
    return step("exits-add", { text }, { quiet: true }).then((r) => new Promise((ok, no) => {
      const settle = (res) => (res && res.ok && res.state !== "error" ? ok(Object.assign({ text: res.text || "" }, res.data || {})) : no(stepErr(res)));
      if (r && r.job) {
        // задание успело закончиться до ответа (быстрый отказ) — итог уже есть
        if (S.jobSeen[r.job.id]) { const j = S.job; settle(j && j.id === r.job.id && j.result ? j.result : S.res["exits-add"]); return; }
        u.addWait = { id: r.job.id, settle };
        return;
      }
      settle(r);
    }));
  }
  function exitsAddDone(job, res) {
    const u = ui("exits"), w = u.addWait;
    if (!w || !job || w.id !== job.id) return;
    u.addWait = null;
    w.settle(res || { ok: false, state: "error", error: { code: "error", text: t("sys.error") } });
  }
  // строки «Добавлено»: подписки (её ключи — одной строкой «Подписка · N ключей») и ключи, добавленные вручную
  // состояние подписки — как в панели (app.js subRows): устарела — «старая» или «ошибка» с текстом, иначе «обновляется сама»
  // (R-333i П2); строку удаляют — «Удаляю…» вместо состояния, × нет (R-333i З1)
  function exitUxRows(d) {
    const u = ui("exits");
    const subs = arr(d && d.sub_rows).map((x) => ({ id: x.id, kind: "sub", name: x.name, keys: x.keys, auto: x.auto,
      status: x.stale ? (x.error ? "err" : "old") : "ok", statusText: x.stale ? String(x.text || "") : "",
      busy: u.deleting === x.id ? t("ex.deleting") : "", onDel: () => askSubDel(x, d) }));
    const keys = arr(d && d.exits).filter((e) => !e.source).map((e) => {
      const st = e.state || (e.usable ? "ok" : "err");
      return { id: e.id, kind: "key", name: e.name || e.id, type: e.title || "", host: e.where && e.where !== e.name ? e.where : "",
        status: st === "ok" ? "ok" : "err", statusText: st === "ok" ? "" : (e.text || ""), busy: u.deleting === e.id ? t("ex.deleting") : "",
        retry: st === "ok" ? null : () => reload("exits"), onDel: () => delExitRow(() => step("exit-del", { id: e.id }), e.id, e.name || e.id) };
    });
    return subs.concat(keys);
  }
  // × подписки — тот же вопрос, что в панели (app.js subDeleteChoice): «Удалить с её ключами / Оставить ключи в пуле /
  // Отмена»; ключ подписки выбран маршрутом по умолчанию — предупреждение (R-333i З1)
  function askSubDel(x, d) {
    const u = ui("exits");
    if (u.deleting) return;
    const via = String((d && d.route && d.route.via) || ""), pinned = arr(x.exits).filter((id) => via === "exit:" + id)
      .map((id) => (arr(d && d.exits).find((e) => e.id === id) || {}).name || id);
    const go = (keep) => { S.modal = null; delExitRow(() => step("exit-sub-del", keep ? { id: x.id, keep: true } : { id: x.id }), x.id, x.name); };
    S.modal = { title: t("ex.subDelT", { name: x.name }),
      body: () => [h("p", { text: x.keys ? tn("ex.subDelS", x.keys).replace("{n}", x.keys) : t("ex.subDelNone") }),
        x.keys && pinned.length ? note("warning", t("ex.subDelPinned"), pinned.join(", ")) : null],
      foot: (close) => [btn(t("c.cancel"), "tertiary", close, { k: "sd-no" }),
        x.keys ? btn(t("ex.subDelKeep"), "tertiary", () => go(true), { k: "sd-keep" }) : null,
        btn(x.keys ? t("ex.subDelWith") : t("ex.subDel"), "danger", () => go(false), { k: "sd-yes" })] };
    render();
  }
  // × в строке: строка занята («Удаляю…»), второе × до ответа ничего не делает; удалено — тост «Удалено: {имя}», отказ —
  // тост с текстом шага (R-333i З1: отказ был молчаливым)
  async function delExitRow(run, id, title) {
    const u = ui("exits");
    if (u.deleting) return;                              // по одному
    u.deleting = id; u.uxSig = "";
    render();
    const r = await run();
    u.deleting = ""; u.uxSig = "";
    if (r && r.ok) toast(t("ex.removed", { n: title }));
    else toast((r && r.error && r.error.text) || (r && r.text) || t("ex.delFail", { n: title }));
    reload("exits");
  }
  function exitsUx(d) {
    const X = UX(), u = ui("exits");
    if (!X) return note("warning", t("sys.error"));
    u.ux = u.ux || {}; u.uxLang = u.uxLang || {};
    if (!u.ux.input || u.uxLang.input !== LANG) {
      if (u.ux.input) u.ux.input.destroy();
      u.uxLang.input = LANG; u.uxSig = "";
      u.ux.input = X.exitsInput({ lang: LANG, rows: [],
        api: { preview: (b) => api("step/exits-preview", { text: String(b.key || "").trim() }).then((r) => { if (r.status === 200 && r.body.ok) return r.body.data || {}; throw stepErr(r.body); }),
          // подписка уже добавлена — «Добавить» закрыта (предпроверка мастера, R-333i П3)
          checkSub: (b) => api("step/exits-preview", { text: String(b.url || "").trim() }).then((r) => { if (r.status === 200 && r.body.ok) return { already: !!(r.body.data || {}).sub_already, text: r.body.text || "" }; throw stepErr(r.body); }),
          addKey: (b) => exitsAdd(b.key), addSub: (b) => exitsAdd(b.url) },
        onAdded: (kind, r) => { toast((r && r.text) || t(kind === "sub" ? "ex.subAdded" : "ex.added")); reload("exits"); },
        onError: (e, retry) => { if (e && e.code === "github_key") { u.ghRetry = retry; render(); } } });
    }
    // строки перерисовываются, только когда поменялись (опрос раз в 10 с не трогает кнопки под фокусом)
    const rows = exitUxRows(d), sig = JSON.stringify(rows.map((x) => [x.id, x.name, x.keys, x.type, x.host, x.status, x.statusText, x.busy]));
    if (sig !== u.uxSig) { u.uxSig = sig; u.ux.input.set(rows, 0); }
    return u.ux.input.el;
  }
  function keysCard(d) {
    const u = ui("exits"), list = arr(d && d.exits), j = running(["exits-add"]), add = S.res["exits-add"];
    const kids = [exitsUx(d)];
    if (j) kids.push(liveList(t("ex.liveRun"), arr(j.items), { timer: true, started: j.started }), ...runningNote(j));
    // ключ GitHub для первого ключа AmneziaWG: карточка здесь, после ключа — та же попытка ещё раз (текст в поле)
    if (!j && add && add.error && add.error.code === "github_key") {
      kids.push(note("warning", t("ex.ghNeed"), add.error.text),
        ghCard({ state: "none" }, () => { const again = u.ghRetry; S.res["exits-add"] = null; u.ghRetry = null; render(); if (again) again(); }));
    }
    const busy = jobRunning() && !j ? note("warning", t("ex.busyOther"), "", { icon: "clock" }) : null;
    if (busy) kids.push(busy);
    if (list.some((e) => e.kind === "xray" || e.kind === "vless")) kids.push(h("p", { class: "qs-small", "data-k": "ex-vless-ping", text: t("ex.vlessPing") }));   // T-M6-qs330c №3
    return h("div", { class: "qs-stack", "data-hl-target": "keys" }, kids);
  }
  VIEW.exits = () => {
    const u = ui("exits"), d = exitsData();
    const opt = (id, title, desc, rec) => choice({ radio: true, on: u.choice === id, k: "ex-" + id,
      pick: () => { u.choice = id; if (id === "exits") chooseExits(); else stop("exits"); render(); },
      kids: [h("span", { class: "qs-choice__row" }, radio(), h("span", { class: "qs-choice__t", text: title })), h("span", { class: "qs-choice__d", text: desc }),
        rec ? h("span", { class: "qs-choice__tags" }, chip(t("c.recommended"), "accent")) : null] });
    // выбор — две карточки в ряд (на 1100–1260 не три узких столбца), на телефоне — столбцом
    const main = [h("div", { class: "qs-grid-2 qs-grid-pick", role: "radiogroup", "aria-label": t("head.exits"), "data-hl-target": "choice" },
      opt("direct", t("ex.direct"), t("ex.directD")), opt("exits", t("ex.exits"), t("ex.exitsD"), true))];
    // пока выходов нет — откуда их взять (UX-333 §1, №45)
    if (d && !arr(d.exits).length && !arr(d.sub_rows).length) main.push(noServerLine());
    const res = S.res["exits-choice"];
    // выходы уже есть — сказать, что «без выхода» перестанет вести через них (они останутся в списке)
    if (u.choice === "direct") main.push(h("p", { class: "qs-plate", "data-k": "ex-direct-note", text: t(d && arr(d.exits).length ? "ex.directNoteHave" : "ex.directNote", { ip: ip() || "—" }) }));
    if (!u.choice) main.push(u.loadFail ? note("warning", "", t("ex.loadFail"), { btns: [btn(t("c.retryShort"), "inverse", () => { u.loadFail = false; reload("exits"); render(); }, { sm: true, k: "ex-reload" })] })
      : h("p", { class: "qs-small", role: "status", "data-k": "ex-wait", text: t("c.loading") }));
    if (res && res.state === "error") main.push(resNote(res));
    let nextBtn = {};
    if (u.choice === "exits") {
      main.push(keysCard(d));
      // ключ добавлен, окно закрылось тостом — предупреждения скрипта (предел ключей, «AWG-ядро недоступно…») остаются
      // здесь до «Понятно» (T-M6-qs330e: сервер отдавал data.warnings, страница молчала)
      const add = running(["exits-add"]) ? null : S.res["exits-add"], wk = arr(add && add.data && add.data.warnings).join("\n");
      if (wk && u.warnSeen !== wk) main.push(warnNote(add, { btns: [btn(t("c.gotIt"), "tertiary", () => { u.warnSeen = wk; render(); }, { sm: true, k: "ex-warn-ok" })] }));
      if (d && arr(d.exits).length) main.push(routeCard(d));
      const total = d ? arr(d.exits).length : 0;
      nextBtn = total || running(["exits-add"]) ? { disabled: !!running(["exits-add"]) } : { disabled: true, hint: t("ex.hintNoKeys") };
    } else if (u.choice === "direct") {
      nextBtn = { busy: !!S.busy["exits-choice"], act: async () => { const r = await step("exits-choice", { choice: "direct" }); if (r.ok) next(); } };
    } else nextBtn = { disabled: true };               // выбор ещё не определён (ждём данные) — молча ничего не шлём
    return { main, foot: { next: nextBtn } };
  };
  // «Через выходы» после «Напрямую»: маршрут сервер возвращает в пул — список и подсказка маршрута сразу по новому ответу
  async function chooseExits() {
    stop("exits");
    await step("exits-choice", { choice: "exits" });
    startExitsPoll();
  }

  // ── 4. Домен и сайт ────────────────────────────────────────────────────────
  // 3.3.3 (UX-333 §6, DomainSteps): четыре шага — купить домен, вписать домен и поддомен, создать A-запись, проверка
  // сама. Кнопок «Домен есть / нет» и «Проверить» нет: имя верного вида проверяется само (DNS, только смотрим — на
  // сервере ничего не меняется), запись видна — выпуск сертификата (шаг «cert» сам ставит имя узлу). Без домена —
  // «Дальше без домена» (этап пропущен).
  // Имя узла (answers.domain) — целиком, рядом — домен, как его вписали в поле (answers.domain_zone, у новой попытки —
  // domain_try.zone): по нему поля и восстанавливаются, без догадок (REV-334 №4: example.co.nz делилось на «example» и
  // «co.nz» — A-запись «example» вместо «@»). Зоны нет (имя записано до 3.3.4 или задано не мастером) — делим так:
  // три и больше частей — первая поддомен, кроме зон второго уровня вида co.uk из трёх частей.
  // Список один (на сервере своего нет): зоны второго уровня у регистраторов и адреса сервисов динамического DNS —
  // у них example.duckdns.org и есть домен человека (REV-334: example.pp.ua давало «Ваш домен — pp.ua?»).
  const SLD = ["co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "ac.uk", "gov.uk", "net.uk", "sch.uk", "nhs.uk",
    "com.ru", "net.ru", "org.ru", "pp.ru", "msk.ru", "spb.ru",
    "com.ua", "net.ua", "org.ua", "in.ua", "co.ua", "kiev.ua", "pp.ua", "kh.ua", "biz.ua", "gov.ua", "edu.ua", "od.ua", "dp.ua",
    "lviv.ua", "zp.ua", "com.kz", "org.kz", "net.kz", "com.by", "net.by", "org.by",
    "com.tr", "net.tr", "org.tr", "gen.tr", "com.br", "net.br", "org.br",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au", "asn.au", "co.nz", "net.nz", "org.nz", "gen.nz", "ac.nz", "school.nz",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "gr.jp", "ad.jp", "ed.jp", "lg.jp", "co.kr", "or.kr", "ne.kr", "ac.kr",
    "co.il", "org.il", "net.il", "ac.il", "gov.il", "co.za", "org.za", "net.za", "ac.za", "co.ke", "or.ke", "ac.ke", "go.ke",
    "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in", "co.id", "or.id", "web.id", "my.id", "co.th", "in.th", "or.th",
    "com.cn", "net.cn", "org.cn", "com.hk", "net.hk", "org.hk", "com.tw", "net.tw", "org.tw", "com.sg", "net.sg", "org.sg",
    "com.my", "net.my", "com.vn", "net.vn", "com.mx", "org.mx", "com.ar", "net.ar", "com.co", "net.co", "com.pl", "net.pl", "org.pl",
    "com.es", "org.es", "co.at", "or.at", "com.de", "com.ng", "com.eg", "com.ph", "com.pk", "com.sa", "co.ae",
    // динамический DNS
    "duckdns.org", "eu.org", "ddns.net", "no-ip.org", "no-ip.biz", "hopto.org", "zapto.org", "sytes.net", "myftp.org",
    "dynu.net", "mooo.com", "afraid.org", "dyndns.org", "dynv6.net", "nsupdate.info", "ydns.eu", "dedyn.io", "freeddns.org"];
  function domSplit(name, zone) {
    const n = domName(name), z = domName(zone), p = n.split(".");
    if (z.indexOf(".") > 0 && (n === z || (n.endsWith("." + z) && n.slice(0, -z.length - 1).indexOf(".") < 0)))
      return { sub: n === z ? "" : n.slice(0, -z.length - 1), dom: z };
    if (p.length >= 3 && !(p.length === 3 && SLD.indexOf(p.slice(1).join(".")) >= 0)) return { sub: p[0], dom: p.slice(1).join(".") };
    return { sub: "", dom: n };
  }
  // REV-334 П2: в «Ваш домен» вписали адрес целиком (vpn.example.com), поддомен пуст — A-запись вышла бы «@» у
  // vpn.example.com, а человек пойдёт с ней к регистратору example.com. Молча не делим (зона может быть делегирована):
  // подсказка с частями из ввода и кнопкой «Перенести». Делим как запасное правило domSplit: три и больше частей,
  // кроме зон второго уровня вида example.co.uk; поддомен — первая часть.
  function domGuess(v) {
    if (!domOk(v)) return null;
    const p = domSplit(v);
    return p.sub ? { s: p.sub, d: p.dom } : null;
  }
  // тело шага с именем: и домен из поля (zone), если поля сейчас дают это имя — сервер его запомнит для перезагрузки
  function domBody(name) {
    const u = ui("domain");
    return name && domFull(u) === name ? { name, zone: domName(u.dom) } : name ? { name } : {};
  }
  const LBL = /^[a-z0-9\u0430-\u044f\u0451]([a-z0-9\u0430-\u044f\u0451-]{0,61}[a-z0-9\u0430-\u044f\u0451])?$/;   // и кириллица
  // домен: буквы (и кириллица), цифры, дефис, хотя бы одна точка; поддомен — по тем же правилам части имени (subOk ниже)
  function domOk(v) {
    const n = domName(v), p = n.split(".");
    return n.length <= 253 && p.length >= 2 && p.every((x) => LBL.test(x)) && /^([a-z\u0430-\u044f\u0451]{2,63}|xn--[a-z0-9-]{2,59})$/.test(p[p.length - 1]);
  }
  // поддомен — как часть домена: латиница, кириллица, цифры, дефис (R-333 З2: сохранённое «впн.пример.рф» открывалось
  // красной ошибкой поддомена)
  const subOk = (v) => !String(v || "").trim() || LBL.test(String(v).trim().toLowerCase());
  const domFull = (u) => { const sb = String(u.sub || "").trim().toLowerCase(), d = domName(u.dom); return d ? (sb ? sb + "." + d : d) : ""; };
  const domReady = (u) => domOk(u.dom) && subOk(u.sub);
  ENTER.domain = () => {
    const u = ui("domain"), a = (S.st && S.st.answers) || {}, st = (S.st && S.st.steps) || {};
    // после перезагрузки: домен узла из ответов, DNS — заново; новое имя рядом с работающим (answers.domain_try) —
    // в поле, только пока идёт его смена (задание «cert»), иначе — подсказкой «Последняя проверка» (T-M6-qs330e №3)
    const tr = a.domain_try && a.domain_try.name && a.domain_try.state === "ok" && running(["cert"]) ? a.domain_try.name : "";
    const saved = tr || a.domain || workDomain()[1] || "";          // домен задан в панели — он и в полях (буквами)
    if (saved && !u.name) {
      setDomFields(u, saved, tr ? a.domain_try.zone : saved === a.domain ? a.domain_zone : "");
      u.checked = u.name; u.dnsSince = now(); u.certAuto = false;
      // «Сайт открыт» — только если сайт и вправду включён (T-M6-qs330c №4: после «site remove» запись шага осталась)
      if (st.site && st.site.state === "ok" && !S.res.site && siteOn()) S.res.site = { ok: true, state: "ok", text: st.site.text, data: {} };
      step("dns", domBody(u.checked)).then((r) => { if (r && r.data && r.data.dns) { u.dns = r.data; if (r.data.dns === "ok") autoCert(); else startDnsPoll(); } render(); });
    } else if (u.dns && u.dns.dns !== "ok") startDnsPoll();
  };
  function setDomFields(u, name, zone) { const p = domSplit(name, zone); u.sub = p.sub; u.dom = p.dom; u.name = domFull(u); }
  function startDnsPoll() {
    every("dns", 10, async () => {
      const u = ui("domain"), asked = u.checked;
      if (!asked) { stop("dns"); return; }
      const r = await step("dns", domBody(asked));
      if (u.checked !== asked) return;                    // имя сменили, пока шёл опрос — ответ не про него
      if (r && r.data && r.data.dns) { u.dns = r.data; if (r.data.dns === "ok") { stop("dns"); autoCert(); } }
      render();
    });
  }
  // Проверка домена — одна на всё (имя вписано, «Проверить снова» у сертификата, последняя попытка) и всегда по
  // ТЕКУЩЕМУ значению полей (T-M6-qs330c №1). 3.3.3: только DNS (шаг «dns» с именем — ничего не меняет на сервере);
  // запись видна — выпуск сертификата (шаг «cert» с этим именем: сам ставит имя узлу, рабочий домен меняет только
  // после выпуска для нового — T-M6-qs330d №1). Не видна — опрос раз в 10 с.
  const domName = (v) => String(v || "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  async function checkDomain() {
    const u = ui("domain"), name = domName(u.name);
    if (!name || u.checking === name || running(["cert"])) return;
    if (name !== u.checked) dropDomainResults(u);
    u.checked = name; u.dnsSince = u.dnsSince && u.dns ? u.dnsSince : now(); u.certAuto = false; u.checking = name;
    if (S.res.cert && !S.res.cert.ok) S.res.cert = null;                // «Проверить снова»: прежний отказ — не про эту попытку
    const r = await step("dns", domBody(name));
    if (u.checking === name) u.checking = "";
    if (domName(u.name) !== name) { if (u.checked === name) u.checked = ""; return; }   // имя в полях сменили — ответ не про него
    if (r && r.data && r.data.dns) {
      u.dns = r.data;
      if (r.data.dns === "ok") autoCert(); else startDnsPoll();
    } else u.dns = null;                                // отказ шага — его слова в шаге 4
    render();
  }
  // результаты прежнего имени (DNS, отказ или итог выпуска) к новому не относятся: убрать, опрос DNS остановить
  function dropDomainResults(u) {
    u.checked = ""; u.dns = null; u.certAuto = false; u.checking = "";
    stop("dns");
    S.res.domain = null; S.res.dns = null;
    if (!running(["cert"])) S.res.cert = null;
  }
  // правка полей: имя ушло от проверенного — блоки DNS и сертификата прежнего имени исчезают сразу; новое имя верного
  // вида проверяется само через 0,8 с после последней буквы
  let domTimer = 0;
  function domainEdited() {
    const u = ui("domain");
    u.name = domFull(u);
    clearTimeout(domTimer);
    if (running(["cert", "site", "template"])) return;
    if (u.checked && u.name !== u.checked) dropDomainResults(u);
    if (domReady(u) && u.name !== u.checked) domTimer = setTimeout(() => { if (domReady(u) && domFull(u) === u.name) checkDomain(); }, 800);
  }
  // Сам — только проверка сертификата домена, который уже работает (то же имя: на сервере ничего не меняется). Новое
  // имя узлу ставит выпуск по кнопке «Выпустить сертификат» (R-333 З1: проверка DNS сама выпускала сертификат и меняла
  // имя узла — для недопечатанного имени, для корня с A-записью, для любого имени под «*.»)
  function autoCert() {
    const u = ui("domain");
    if (!isWorkDomain(u.checked) || u.certAuto || running(["cert"]) || (S.res.cert && S.res.cert.ok)) return;
    u.certAuto = true;
    startCert();
  }
  function certNow() {
    const u = ui("domain");
    if (running(["cert"]) || S.busy.cert || S.busy.certPrep || !u.checked || u.checked !== domFull(u)) return;
    if (S.res.cert && !S.res.cert.ok) S.res.cert = null;
    startCert();
  }
  // Переезд на домен. Кука мастера привязана к адресу, а после выпуска сертификата домена адрес по IP может больше не
  // открыться: одноразовый токен переезда берём ДО выпуска, пока связь есть, и по нему открываем мастер на домене.
  const onDomain = () => { const d = (ui("domain").name || "").toLowerCase(); return !!d && location.hostname.toLowerCase() === d; };
  async function prepMove() {
    const name = ui("domain").checked;
    try { const r = await api("move", name ? { host: name } : {}); if (r.status === 200 && r.body.url) S.move = Object.assign({ at: now() }, r.body); }
    catch (e) { /* без связи — останется прежний токен */ }
    return S.move;
  }
  // занято с первого нажатия: токен переезда — свой запрос до задания «cert»; поля и кнопка закрыты сразу, второго
  // выпуска нет (R-333 П-9)
  async function startCert() {
    const name = ui("domain").checked;
    if (S.busy.certPrep) return null;
    S.busy.certPrep = true; render();
    try {
      if (!onDomain()) await prepMove();
      return await step("cert", domBody(name));
    } finally { delete S.busy.certPrep; render(); }
  }
  // домен сменён: прежний снят, сайт (если был) — на новом имени; мастер на прежнем домене переезжает (moveSoon)
  function switchDone(res) {
    const d = res && res.ok && res.data;
    if (!d || !d.changed_from) return;
    const u = ui("domain");
    if (u.dns) u.dns = Object.assign({}, u.dns, { kept: null, switch: false });
    if (d.site_on) S.res.site = { ok: true, state: "ok", text: "", data: { template: d.template || {} }, at: now() };
  }
  function moveSoon() {
    if (onDomain() || S.moveStay || !(S.res.cert && S.res.cert.ok)) return;
    const go = () => { if (!S.move) return; S.moveAt = now() + 6; S.moveCheck = ""; render(); setTimeout(() => { if (S.moveAt && !S.moveStay) goMove(true); }, 6000); };
    if (S.move && now() - S.move.at < 60) go(); else prepMove().then(go);
  }
  // Сам переход — только когда домен уже отвечает своим сертификатом (T-M6-qs330b №4): сервер перечитывает
  // сертификат и пожимает руку сам себе с именем домена. Ошибка сертификата в браузере не ловится fetch (это просто
  // TypeError, а CSP страницы и не пустит запрос на чужой адрес) — поэтому спрашиваем свой сервер.
  const MOVE_TRIES = 10, MOVE_GAP = 1500;
  const pause = (ms) => new Promise((ok) => setTimeout(ok, ms));
  async function moveReady() {
    for (let i = 0; i < MOVE_TRIES; i++) {
      if (S.moveStay) return false;
      try {
        const r = await api("move-ready", undefined, { raw: true });
        if (r.status === 200 && r.body.ready) return true;
      } catch (e) { /* нет связи — ещё попытка */ }
      if (i < MOVE_TRIES - 1) await pause(MOVE_GAP);
    }
    return false;
  }
  async function goMove(auto) {
    if (!S.move) return;
    S.moveAt = 0;
    if (auto) {
      S.moveCheck = "wait"; render();
      const ready = await moveReady();
      if (S.moveStay) { S.moveCheck = ""; render(); return; }
      if (!ready) { S.moveCheck = "late"; render(); return; }     // остаёмся: «Перейти сейчас» — по кнопке
      S.moveCheck = "";
    }
    if (now() - S.move.at > 60) await prepMove();          // свежий токен, если страница ещё достаёт до сервера
    if (MOCK) { toast(t("dm.moveMock", { d: S.move.host })); render(); return; }
    location.assign(S.move.url);
  }
  function moveNote() {
    if (!S.move || onDomain() || S.moveStay) return null;
    const body = S.moveCheck === "late" ? t("dm.moveLate", { d: S.move.host }) : S.moveCheck === "wait" ? t("dm.moveWait") : t("dm.moveB");
    return note(S.moveCheck === "late" ? "warning" : "accent", t("dm.moveT", { d: S.move.host }), body, { extra: [code(S.move.clean, { copyLabel: t("c.copy") }),
      h("div", { class: "qs-btns" }, btn(t("dm.moveGo"), "primary", () => goMove(), { sm: true, k: "move-go" }),
        btn(t("dm.moveStay"), "tertiary", () => { S.moveStay = true; S.moveAt = 0; render(); }, { sm: true, k: "move-stay" }))] });
  }
  // каталог шаблонов (33 тыс.) — страницами по 25: категория/слово → первая страница, «Показать ещё» → следующая.
  // Ответ на устаревший запрос (сменили категорию или слово, пока шёл прежний) отбрасывается; ошибка — плашка с
  // «Повторить» (нужен ключ GitHub — его карточка), не вечное «Загружаю…».
  let tplSeq = 0;
  const tplNum = (n) => String(n || 0).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");     // 33 467
  async function loadTemplates(more) {
    const u = ui("domain"), seq = ++tplSeq;
    const body = more && u.tpls ? { page: (u.tpls.page || 1) + 1 } : {};
    if (u.cat) body.category = u.cat;
    if ((u.q || "").trim()) body.query = u.q.trim();
    u.tplErr = null;
    u.tplLoading = more && u.tpls ? "more" : "list";
    render();
    const r = await step("templates", body);
    if (seq !== tplSeq) return;
    u.tplLoading = "";
    if (r && r.ok) u.tpls = more && u.tpls ? Object.assign({}, r.data, { items: arr(u.tpls.items).concat(arr(r.data.items)) }) : r.data;
    else u.tplErr = Object.assign({}, r && (r.error || r.text) ? r : { ok: false, state: "error", error: { code: "error", text: t("dm.tplErr") } }, { more: !!more && !!u.tpls });
    render();
  }
  function tplList(u) {
    const out = [], tp = u.tpls, items = arr(tp && tp.items), cats = arr(tp && tp.categories);
    const catT = (id) => ((cats.find((c) => c.id === id) || {}).title || id || "").toLowerCase();
    if (u.tplErr) {
      const e = u.tplErr.error || {};
      const again = () => loadTemplates(!!u.tplErr.more);
      if (e.code === "github_key") out.push(note("warning", t("dm.tplErr"), e.text), ghCard({ state: "none" }, again));
      else out.push(resNote(u.tplErr, { retry: again }));
    }
    if (u.tplLoading === "list") out.push(h("p", { class: "qs-small", role: "status", "aria-live": "polite", text: tp ? t("c.loading") : t("dm.tplLoading") }));
    if (items.length) {
      out.push(h("p", { class: "qs-small", text: t("dm.tplCount", { n: tplNum(tp.total || items.length), shown: tplNum(items.length) }) }));
      out.push(h("div", { class: "qs-tpls", "aria-busy": u.tplLoading === "list" ? "true" : null }, items.map((x) => h("button", { type: "button", class: "qs-tpl", "aria-pressed": u.tpl === x.id ? "true" : "false", "aria-disabled": x.gone ? "true" : null, "data-k": "tpl-" + x.id,
        on: { click: () => { if (!x.gone) { u.tpl = x.id; render(); } } } }, h("span", { class: "qs-tpl__pic", "aria-hidden": "true" }),
      h("span", { class: "qs-tpl__t" }, x.name, h("span", { text: x.gone ? t("dm.tplGone") : catT(x.category) }))))));
    } else if (tp && !u.tplLoading && !u.tplErr) out.push(h("p", { class: "qs-small", text: t("dm.noTpl") }));
    if (tp && (tp.page || 1) < (tp.pages || 1) && !u.tplErr) out.push(h("div", { class: "qs-btns" }, btn(t("dm.more", { n: tplNum(Math.max(0, (tp.total || 0) - items.length)) }), "ghost", () => loadTemplates(true), { busy: u.tplLoading === "more", disabled: u.tplLoading === "list", k: "tpl-more" })));
    return out;
  }
  let tplTimer = 0;
  // Последняя проверка нового имени рядом с работающим доменом (T-M6-qs330e №3): сервер держит её отдельно
  // (answers.domain_try), в поле после перезагрузки — рабочий домен; неудачная попытка — подсказкой под полем,
  // «Проверить снова» вернёт её имя в поле. Пока в поле то же имя — говорит сам блок DNS, подсказка не нужна.
  function lastTry(u) {
    const tr = ((S.st && S.st.answers) || {}).domain_try;
    if (!tr || !tr.name || ui("domain").checking || running(["cert"])) return null;
    const name = domName(tr.name);
    if (name === u.checked || name === domName(u.name)) return null;
    const why = tr.state === "error" ? t(tr.why === "limit" ? "dm.tryLimit" : "dm.tryErr") : t("dm.try_" + (tr.state || "wait"));
    return h("div", { class: "qs-row", "data-k": "dm-last" }, h("span", { class: "qs-small", text: t("dm.lastTry", { d: tr.name, r: why }) }),
      btn(t("dm.certAgain"), "ghost", () => { setDomFields(u, tr.name, tr.zone); checkDomain(); }, { sm: true, k: "dm-last-again" }));
  }
  // Шаг 4 «Проверим сами»: что видно в DNS — словами UX-333 §6; «нет записи» — то же «пока не видно» (запись только
  // что создали). Смена работающего домена — плюс «{прежний} работает как раньше».
  function dnsBlock(u) {
    const d = u.dns, addr = u.checked || domName(u.name), want = ip() || arr(d && d.own)[0] || "—";
    if (!domReady(u)) return h("p", { class: "qs-dsteps__none", text: t("dm.noName") });
    const fail = S.res.dns && !S.res.dns.ok && S.res.dns.state === "error" ? S.res.dns : null;
    if (!d && fail) return resNote(fail, { retry: () => checkDomain() });
    const kept = d && d.kept ? t("dm.kept", { d: d.kept }) : "";
    const box = (tone, title, text, o) => h("div", { class: "qs-dstat", "data-tone": tone, role: "status", "aria-live": "polite", "data-hl-target": "dns" },
      h("span", { class: "qs-dstat__ic", "aria-hidden": "true" }, tone === "wait" ? h("span", { class: "qs-spin" }) : icon(tone === "ok" ? "check" : tone === "warn" ? "exclamation" : "xmark", 13)),
      h("div", { class: "qs-dstat__b" }, h("b", { text: title }), text ? h("span", { text }) : null, kept ? h("span", { text: kept }) : null, (o && o.extra) || null),
      o && o.since ? h("span", { class: "qs-dstat__t", "data-since": o.since }, mmss(now() - o.since)) : null);
    if (!d) return box("wait", t("dm.waitT"), t("dm.waitS"), { since: u.dnsSince || now() });
    if (d.dns === "ok") return box("ok", t("dm.ok"), addr + " → " + arr(d.a).join(", ") + (d.kept ? " · " + t("dm.switchNext", { d: d.kept }) : ""));
    if (d.dns === "cloudflare") return box("warn", t("dm.cf"), t("dm.cfB"));
    if (d.dns === "elsewhere") return box("err", t("dm.elsewhere"), arr(d.a).length ? t("dm.elsewhereB", { d: addr, a: arr(d.a).join(", "), ip: want }) : (d.advice || t("dm.waitS")));
    const hints = u.dnsSince && now() - u.dnsSince > (d.hint_after || 900) ? h("div", { class: "qs-hints" }, h("b", { text: t("dm.hints") }),
      [t("dm.hint1"), t("dm.hint2"), t("dm.hint3")].map((x, i) => h("div", { class: "qs-hint" }, h("i", { text: String(i + 1) }), h("span", { text: x })))) : null;
    return box("wait", t("dm.waitT"), t("dm.waitS"), { since: u.dnsSince || now(), extra: hints });
  }
  // Отказ выпуска словами сервера. Срок лимита Let's Encrypt сервер пишет по своим часам (``retry_text`` — эта часть
  // текста), страница — по часам этого компьютера (T-M6-qs330c №2: «04:23 UTC» человеку в Москве — лишняя арифметика)
  function certErrText(res) {
    const e = (res && res.error) || {}, text = String(e.text || (res && res.text) || "");
    const at = e.retry_after ? new Date(e.retry_after) : null;
    if (!at || isNaN(at.getTime()) || !e.retry_text || text.indexOf(e.retry_text) < 0) return text;
    return text.replace(e.retry_text, localWhen(at));
  }
  function localWhen(at) {
    const day = DATES.dateTime(at);
    const left = (at.getTime() - Date.now()) / 1000, hours = Math.floor(left / 3600);
    if (left <= 0) return t("dm.localAt", { at: day });
    const rel = hours < 1 ? t("dm.inLess") : hours < 48 ? t("dm.inHours", { n: hours }) : t("dm.inDays", { n: Math.floor(hours / 24) });
    return t("dm.localAtRel", { at: day, rel });
  }
  // сайт и вправду включён — по факту сервера (state.site: режим из настроек узла), а не по записи шага: после
  // «kaskadctl site remove» запись «site: ok» осталась, и мастер писал «Сайт открыт» при выключенном nginx (T-M6-qs330c
  // №4). Итог шага, пришедший после последнего состояния, новее факта — ему и верим, пока состояние не перечитано.
  // рабочий домен узла — с сертификатом Let's Encrypt (state.site.cert): [xn--…, буквами] (R-333 З2, З3)
  function workDomain() {
    const f = S.st && S.st.site;
    return f && f.cert && f.domain ? [domName(f.domain), domName(f.domain_u || f.domain)] : [];
  }
  const isWorkDomain = (name) => !!name && workDomain().indexOf(domName(name)) >= 0;
  function siteOn() {
    const f = S.st && S.st.site, r = S.res.site;
    if (!f) return true;                                  // сервер без этого поля — как раньше, по итогу шага
    if (r && r.ok && r.at && r.at > (S.stAt || 0)) return true;
    return f.mode === "site";
  }
  function certCard(u) {
    const j = running(["cert"]), res = S.res.cert, dom = u.checked || domName(u.name);
    const rows = [], acts = [];
    const sw = !!(u.dns && u.dns.switch);                 // смена работающего домена: строки DNS → сертификат → переключение
    if (j && sw) return h("div", { "data-hl-target": "cert" }, liveList("", arr(j.items)), ...runningNote(j));
    if (j) rows.push(Object.assign({}, arr(j.items)[0] || {}, { title: t("dm.certRun", { d: dom }), detail: t("dm.certRunS") }));
    else if (res && res.ok) {
      const c = (res.data && res.data.cert) || {};
      rows.push({ state: "ok", title: t("dm.certOk", { d: arr(c.names)[0] || dom }), detail: t("dm.certOkS", { date: DATES.date(c.not_after) || String(c.not_after || "").slice(0, 10) }) });
      const imp = (res.data && res.data.use_impact) || {}, n = (imp.sstp || 0) + (imp.ocserv || 0);
      const tls = [imp.sstp !== undefined ? "SSTP" : null, imp.ocserv !== undefined ? "OpenConnect" : null].filter(Boolean);
      if (n) rows.push({ state: "ok", title: t("dm.tlsMove", { list: andList(tls) }), detail: t("dm.tlsMoveS", { n }) });
    } else if (res) {
      rows.push({ state: "err", title: t(sw ? "dm.switchErr" : "dm.certErr"), detail: certErrText(res) });
      acts[0] = btn(t("dm.certAgain"), "tertiary", () => checkDomain(), { sm: true, k: "cert-again" });
    } else if (u.certAuto || isWorkDomain(dom)) rows.push({ state: "idle", title: t("dm.certRun", { d: dom }), detail: t("c.queued") });
    else if (sw) {                                        // смена работающего домена — только по кнопке, внизу «Дальше» с прежним
      rows.push({ state: "idle", title: t("dm.switchAsk", { d: dom }), detail: t("dm.switchAskS", { d: u.dns.kept || "" }) });
      acts[0] = btn(t("dm.switchGo"), "primary", certNow, { sm: true, busy: !!(S.busy.cert || S.busy.certPrep), k: "cert-go" });
    } else rows.push({ state: "idle", title: t("dm.certOk", { d: dom }), detail: t("dm.certAskS") });   // кнопка — внизу
    const warn = j ? null : warnNote(res), mail = j ? null : emailNote(res);
    return h("div", { "data-hl-target": "cert", class: warn || mail ? "qs-stack" : null }, liveList("", rows, { acts }), ...runningNote(j), warn, mail);
  }
  // «Почта для Let's Encrypt не задана» — с полем почты прямо в плашке (3.3.2 №5): необязательно; «Сохранить» — шаг
  // «site-email», та же функция, что «kaskadctl site email» и панель (Настройки → «Домен и сайт»). Почта уже задана
  // (state.site.email — после перезагрузки тоже) — плашки нет; сохранили здесь — короткое «Почта: …».
  const isMail = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(v || "").trim());
  function emailNote(res) {
    const e = res && res.ok && res.data && res.data.email, u = ui("domain"), r = S.res["site-email"];
    if (r && r.ok && r.data && r.data.email) return note(r.state === "partial" ? "warning" : "success", r.text, arr(r.data.warnings).join(" "));
    if (!e || !e.need || (S.st && S.st.site && S.st.site.email)) return null;
    const save = async () => { if (!isMail(u.email) || S.busy["site-email"]) return; const x = await step("site-email", { email: String(u.email).trim() }); if (x.ok) u.email = ""; };
    const bad = !!(r && !r.ok) || (!!(u.email || "").trim() && !isMail(u.email));
    const inp = input("le-email", u.email, (v) => { u.email = v; S.res["site-email"] = null; }, { type: "email", ph: t("dm.emailPh"), mode: "email", ac: "email", aria: t("dm.email"), bad, enter: save, max: 254 });
    return note("warning", t("dm.email"), "", { extra: [
      h("div", { class: "qs-field__row qs-mail" }, inp, btn(t("c.save"), "inverse", save, { busy: !!S.busy["site-email"], disabled: !isMail(u.email), k: "le-email-save" })),
      r && !r.ok ? h("span", { class: "qs-err", role: "alert", text: (r.error && r.error.text) || r.text || t("sys.error") }) : null,
      h("span", { class: "qs-small", text: t("dm.emailLater") })] });
  }
  function siteCard(u) {
    const j = running(["site", "template"]), res = S.res.site, tpl = S.res.template;
    const v = vlessSite();                                // VLESS Ш4: М1 / М2 / выбор Reality — или null (как раньше)
    const kids = [cardHead(t("dm.site"), v && v.sub ? v.sub : t("dm.siteSub", { d: u.checked || domName(u.name) }),
      v && v.auto ? { end: chip(t("dm.site.auto"), "accent") } : v && v.rec ? { end: chip(t("dm.site.off")) } : null)];      // М2 — «не включён» (DQA-3378 М-5)
    if (res && res.ok && siteOn()) {
      const f = (S.st && S.st.site) || {}, fact = f.template && f.template.name;
      const url = res.data && res.data.panel_url, tn_ = fact || (tpl && tpl.ok && tpl.data && tpl.data.name) || (res.data && res.data.template && res.data.template.name);
      kids.push(note("success", t("dm.siteDone"), tn_ ? t("dm.tplNow", { n: tn_ }) : "", { btns: [btn(t("dm.tplOther"), "inverse", () => step("template", { template: "random" }), { sm: true, busy: !!running(["template"]), k: "tpl-other" })] }));
      kids.push(warnNote(res));
      if (url) kids.push(h("div", { class: "qs-field" }, h("span", { class: "qs-field__l", text: t("dm.panelUrl") }), code(url, { copyLabel: t("c.copy") })), note("warning", t("dm.panelWarn")));
      if (j) kids.push(liveList("", arr(j.items)), ...runningNote(j));
      return card({ "data-hl-target": "site" }, kids);
    }
    if (v) kids.push(...vlessKids(v, u));
    kids.push(h("div", { class: "gx-seg qs-seg-fit", role: "group" }, [["auto", t("dm.auto")], ["manual", t("dm.manual")]].map((x) => h("button", { type: "button", "aria-pressed": (u.site || "auto") === x[0] ? "true" : "false", "data-k": "site-" + x[0],
      on: { click: () => { u.site = x[0]; if (x[0] === "manual" && !u.tpls && !u.tplLoading) loadTemplates(); render(); } } }, x[1]))));
    if ((u.site || "auto") !== "auto") {
      kids.push(input("tpl-q", u.q, (v) => { u.q = v; clearTimeout(tplTimer); tplTimer = setTimeout(loadTemplates, 400); }, { ph: t("dm.searchPh"), aria: t("dm.search"), type: "search" }));
      const cats = arr(u.tpls && u.tpls.categories);
      kids.push(h("div", { class: "qs-cats", role: "group" }, [{ id: "", title: t("dm.allCats") }].concat(cats).map((c) => h("button", { type: "button", class: "qs-cat", "aria-pressed": (u.cat || "") === c.id ? "true" : "false", "data-k": "cat-" + (c.id || "all"),
        on: { click: () => { u.cat = c.id; loadTemplates(); } } }, c.title, c.count ? h("span", { class: "qs-cat__n", text: tplNum(c.count) }) : null))));
      kids.push(...tplList(u));
    }
    if (j) kids.push(liveList("", arr(j.items)), ...runningNote(j));
    // «ok» прежнего включения — не про сейчас. Цена VLESS (ядро: CONFLICT «vless_change», §3.7) — кнопка действия
    // шага открывает окно с ценой и повторяет шаг с yes; «Повторить» без ответа дала бы тот же отказ
    else if (res && !res.ok) kids.push(resNote(res, { retry: sitePriced(res) ? null : () => installSite(), onAction: (a) => sitePrice(a, res.data) }));
    kids.push(h("div", { class: "qs-btns" }, btn(t("dm.siteGo"), "primary", installSite, { disabled: !!j || ((u.site === "manual") && !u.tpl), busy: !!S.busy.site, k: "site-go" })));
    return card({ "data-hl-target": "site" }, kids);
  }
  function installSite() { const u = ui("domain"); return step("site", { template: u.site === "manual" && u.tpl ? u.tpl : "random" }); }

  // ── VLESS на этапе «Домен и сайт» (VLESS Ш4: SPEC-vless-tls-default §3.2, §3.7, §4.3; HANDOFF-vless §3–§5) ──────
  // state.vless (steps.vless_now): null — VLESS не установлен, карточка сайта как раньше. Пока сайт выключен и VLESS не
  // на TLS:
  //   М1 — устройств VLESS нет, Reality человек не выбирал (причина «сайт выключен» или запись до 3.3.7.8): сайт
  //        «включится сам» — нижняя «Дальше» сначала включает его (шаг site), при успехе идёт дальше. Без нажатия сайт
  //        не включается: иначе ссылка «Не включать сайт» не успевала бы ничего решить;
  //   М2 — устройства VLESS уже есть (копия настроек) и ядро советует TLS: сайт сам не включается, плашка с ценой;
  //   Reality выбран человеком — строка «выбрано вами».
  // Выбор режима — шаг vless-mode (gw vless mode, via «setup»): отказ ядра «reality_consent» — окно рисков, повтор с
  // согласием; «vless_change» — окно цены, повтор с yes.
  function vlessSite() {
    const v = S.st && S.st.vless;
    if (!v || v.security === "tls" || siteOn()) return null;
    if (v.reason === "chosen") return { chosen: true };
    // «из копии» — запись до 3.3.7.8 без выбора (импорт копии снимает want); прочие причины — подпись как раньше
    if (v.devices > 0 && v.recommend) return { rec: v.recommend, sub: v.legacy ? t("dm.site.import") : "" };
    if (!v.devices && (v.reason === "no_site" || v.reason === "legacy")) return { auto: true, sub: t("dm.site.need") };
    return null;
  }
  function vlessKids(v, u) {
    const jv = running(["vless-mode"]), r = S.res["vless-mode"];
    const busy = !!jv || !!S.busy["vless-mode"] || !!running(["site", "template"]), out = [];
    if (v.auto) {
      const dom = (S.st.vless && S.st.vless.node_domain) || u.checked || domName(u.name);
      out.push(h("ul", { class: "qs-vimpact", "data-k": "site-vless" }, [["lock", t("dm.site.l1")], ["globe", t("dm.site.l2")],
        ["link", t("dm.site.l3", { url: "https://" + dom + "/…" })]].map((x) =>
        h("li", null, h("span", { class: "qs-vimpact__m", "aria-hidden": "true" }, icon(x[0], 14)), h("span", { text: x[1] })))));
      out.push(h("div", { class: "qs-vlink" }, btn(t("dm.site.decline"), "tertiary", () => vlessMode({ mode: "reality" }), { cls: "gx-link", disabled: busy, k: "site-decline" })));
    }
    if (v.rec) {
      const tx = String(v.rec.text || ""), i = tx.indexOf(". ");                      // «Можно перейти на TLS…. Цена: …»
      // кнопки — под текстом цены, как в макете М2 (справа они сжимали текст в узкую колонку, DQA-3378 М-5)
      out.push(note("accent", i > 0 ? tx.slice(0, i + 1) : tx, i > 0 ? tx.slice(i + 2) : "", { extra: h("div", { class: "qs-vbtns" },
        btn(t("dm.vl.toTls"), "primary", () => vlessMode({ mode: "tls" }), { sm: true, disabled: busy, k: "vl-tls" }),
        btn(t("dm.vl.keep"), "tertiary", () => vlessMode({ mode: "reality" }), { sm: true, disabled: busy, k: "vl-keep" })) }));
    }
    if (v.chosen) out.push(h("p", { class: "qs-small", "data-k": "site-chosen", text: t("dm.site.chosen") }));
    if (jv) out.push(liveList("", arr(jv.items)), ...runningNote(jv));
    else if (r && !r.ok && !vlessAsks(r)) out.push(resNote(r));     // TLS невозможен / не включился / сайт не прошёл проверку
    return out;
  }
  const vlessAsks = (r) => { const n = r && r.data && r.data.need; return n === "reality_consent" || n === "vless_change"; };
  // шаг долгий: ответ приходит в jobDone (vlessAnswer); сразу — только отказ до задания (занято, нет связи)
  async function vlessMode(body) {
    const u = ui("domain");
    if (S.busy["vless-mode"] || running(["vless-mode", "site", "template", "cert"])) return null;
    u.vlBody = body;
    const r = await step("vless-mode", body);
    if (r && !r.job) vlessAnswer(r);
    return r;
  }
  function vlessAnswer(res) {
    const u = ui("domain"), body = u.vlBody, d = (res && res.data) || {};
    u.vlBody = null;
    if (!res) return;
    if (res.ok) {
      const x = d.vless || {};
      // перешли на TLS: сайт включило ядро (vless_to_tls) — карточка «Сайт открыт», кому выдать ключи заново
      if (x.security === "tls") S.res.site = { ok: true, state: "ok", text: res.text || "", at: now(), data: { template: d.template || {}, panel_url: d.panel_url || null,
        warnings: (x.text ? [x.text] : []).concat(arr(d.warnings)), reissue: arr(d.reissue) } };
      return;
    }
    if (d.need === "reality_consent") askReality(d, res);
    else if (d.need === "vless_change") {
      const b = Object.assign({}, body || { mode: (d.to && d.to.security) || "tls" }, { yes: true });
      const tls = b.mode === "tls";
      askPrice({ title: t(tls ? "dm.vl.r2tQ" : "dm.vl.keep"), go: t(tls ? "dm.vl.toTls" : "dm.vl.keep"), text: d.text, devices: d.devices, icon: tls ? "lock" : "shield",
        subs: tls, back: tls ? t("dm.vl.r2tBack") : "", run: () => vlessMode(b) });
    }
  }
  // текст рисков ядра (front.risks_data): строки «- » — список, остальное — абзацы; пустая строка — граница
  function riskBlock(text) {
    const parts = [];
    String(text || "").split("\n").forEach((line) => {
      const x = line.trim(), last = parts[parts.length - 1];
      if (!x) parts.push(null);
      else if (x.indexOf("- ") === 0 && last && last.list) last.list.push(x.slice(2));
      else if (x.indexOf("- ") === 0) parts.push({ list: [x.slice(2)] });
      else parts.push({ p: x });
    });
    return parts.filter(Boolean).map((x) => (x.list ? h("ul", { class: "qs-vrisk" }, x.list.map((li) => h("li", { text: li }))) : h("p", { class: "qs-vtx", text: x.p })));
  }
  // согласие на Reality при домене (§4.3, HANDOFF-vless §3): «Понимаю риски» — не основная; «Назад» — основная, фокус
  // на ней; Esc и щелчок мимо — тоже «Назад». Сайт и версия текста — те, что показаны (ядро сверяет их, С2)
  function askReality(d, res) {
    const site = d.site, can = !!(site && d.risks);
    S.modal = { title: d.title || t("dm.vl.keep"), cls: "qs-modal--risk", focus: "consent-back", icon: "shield-exclamation",
      body: () => riskBlock(d.block).concat(can ? [] : [note("warning", (res.error && res.error.text) || res.text || "", "")]),
      foot: (close) => [can ? btn(d.phrase || t("dm.vl.phrase"), "tertiary", async () => { S.modal = null; render();
        await vlessMode({ mode: "reality", site, accept_risks: true, risks: d.risks, yes: true }); }, { k: "consent-yes" }) : null,
        btn(t("dm.vl.back"), "primary", close, { k: "consent-back" })] };
    render();
  }
  // цена новых ссылок VLESS (HANDOFF-vless §4, Ц2/Ц4): текст ядра, «Подписки обновятся сами» (subs — переход на TLS,
  // второй пункт макета Ц4, DQA-3378 М-3), как откатится, «Кому выдать заново» — трое и «и ещё N» (последним, как в панели)
  function askPrice(o) {
    S.modal = { title: o.title, cls: "qs-modal--price", focus: "price-no", icon: o.icon || "globe",          // фокус — на безопасной «Отмена»
      body: () => [h("p", { class: "qs-vtx", text: o.text || "" }), o.subs ? h("p", { class: "qs-vtx", text: t("dm.vl.subs") }) : null,
        o.back ? h("p", { class: "qs-vtx", text: o.back }) : null, whoList(o.devices)],
      foot: (close) => [btn(t("c.cancel"), "tertiary", close, { k: "price-no" }),
        btn(o.go, "primary", async () => { S.modal = null; render(); await o.run(); }, { k: "price-yes" })] };
    render();
  }
  function whoList(devs) {
    const names = arr(devs).map((x) => (x.user_name ? x.name + " (" + x.user_name + ")" : x.name || x.id));
    if (!names.length) return null;
    return h("p", { class: "qs-vdevs" }, h("b", { text: t("dm.vl.who") }), " " + names.slice(0, 3).join(", ") + (names.length > 3 ? " " + t("dm.vl.more", { n: names.length - 3 }) : ""));
  }
  const sitePriced = (res) => !!(res && res.error && res.error.action && res.data && res.data.need === "vless_change");
  function sitePrice(a, d) {
    const tls = !d || !d.to || d.to.security === "tls";        // иначе Reality уходит с 443 на свой порт — откатывать нечего
    askPrice({ title: t("dm.site.onQ"), go: t("dm.site.onGo"), text: (d && d.text) || "", devices: d && d.devices, subs: tls, back: tls ? t("dm.site.onBack") : "",
      run: () => step("site", { template: a.template || "random", yes: true }) });
  }
  // итог шага site (jobDone): М1 — «Дальше» ждала его и идёт дальше; цена VLESS — сразу окно с ценой
  function siteAnswer(res) {
    const u = ui("domain"), go = u.siteNext;
    u.siteNext = false;
    if (res && res.ok) { if (go) next(); return; }
    if (sitePriced(res)) sitePrice(res.error.action, res.data);
  }
  async function siteThenNext() {
    const u = ui("domain");
    if (S.busy.site || running(["site", "template", "vless-mode"])) return;
    u.siteNext = true;
    const r = await installSite();
    if (r && !r.job) siteAnswer(r);
  }
  // DomainSteps (UX-333 §6): номер шага — пройден / нет; шаг 1 и 2 пройдены, когда имя верного вида; 3 и 4 — когда
  // домен смотрит на этот сервер
  function dStep(n, done, title, why, kids, o) {
    o = o || {};
    return h("div", { class: "qs-dstep", "data-done": done ? "" : null, "data-hl-target": o.hl || null },
      h("span", { class: "qs-dstep__n", "aria-hidden": "true" }, done ? icon("check", 14) : String(n)),
      h("div", { class: "qs-dstep__b" }, h("div", { class: "qs-dstep__h" }, h("h2", { class: "qs-dstep__t" }, h("span", { class: "qs-vh", text: done ? t("dm.stepDone", { n }) : t("dm.stepN", { n }) }), title), o.tip || null),
        why ? h("p", { class: "qs-dstep__why", text: why }) : null, kids));
  }
  // треугольник «Важно» (Cloudflare): подсказка по наведению, фокусу или нажатию
  function warnTip(text, k) {
    const u = ui("domain"), on = u.tip === k;
    return h("span", { class: "qs-tip", "data-open": on ? "" : null },
      h("button", { type: "button", class: "qs-tip__b", "aria-label": t("c.attention") + ": " + text, "aria-expanded": on ? "true" : "false", "data-k": "tip-" + k,
        on: { click: () => { u.tip = on ? "" : k; render(); } } }, h("span", { class: "qs-tip__dot" }, icon("exclamation", 12))),
      h("span", { class: "qs-tip__p", role: "tooltip", text }));
  }
  function domainSteps(u) {
    const p = (S.product && S.product.links) || {};
    const named = domReady(u), ok = !!(u.dns && u.dns.dns === "ok" && u.checked === domFull(u)), addr = domFull(u), sub = String(u.sub || "").trim().toLowerCase();
    // промокода нет (OWNER-FEEDBACK №44): пояснение и «Открыть AdminVPS ↗» — партнёрский адрес AdminVPS из
    // product.HOSTING (links.domain_buy, №47; rel без noreferrer — метка доходит) и где на сайте домены
    const buy = dStep(1, named, t("dm.s1"), t("dm.s1Why"), [
      /^https:\/\//.test(p.domain_buy || "") ? h("div", { class: "qs-dstep__row" },
        h("a", { class: "ga-btn ga-btn--tertiary qs-dlink", href: p.domain_buy, target: "_blank", rel: "noopener", "data-k": "dm-buy" }, h("span", { text: t("dm.s1Open") }), " ↗"),
        h("span", { class: "qs-small", "data-k": "dm-buy-where", text: t("dm.s1Where") })) : null], { hl: "record" });
    const subBad = !subOk(u.sub), domBad = !!String(u.dom || "").trim() && !domOk(u.dom);
    // пока идёт выпуск сертификата или настройка сайта — имя не меняется: ход выпуска виден, новое имя не теряется
    // без проверки (R-333 С3)
    const locked = !!running(["cert", "site", "template"]) || !!S.busy.certPrep;
    // полный адрес в поле домена (REV-334 П2); имя уже смотрит на сервер — подсказка не нужна (домен работает как есть)
    const guess = !sub && !locked && !ok ? domGuess(u.dom) : null;
    // «Сам домен останется свободным» — только с поддоменом: без него A-запись как раз для самого домена (REV-334 №2)
    const names = dStep(2, named, t("dm.s2"), t("dm.s2Why") + (sub ? " " + t("dm.s2Free") : ""), [
      h("div", { class: "qs-dnames" },
        h("label", { class: "qs-dnames__sub" }, h("span", { text: t("dm.sub") }),
          input("dm-sub", u.sub, (v) => { u.sub = v; domainEdited(); }, { mono: true, ph: "vpn", aria: t("dm.sub"), bad: subBad, max: 63, mode: "url", disabled: locked })),
        h("span", { class: "qs-dnames__dot", "aria-hidden": "true", "data-off": sub ? null : "", text: "." }),   // без поддомена точки нет
        h("label", { class: "qs-dnames__dom" }, h("span", { text: t("dm.your") }),
          input("dm-name", u.dom, (v) => { u.dom = v; domainEdited(); }, { mono: true, ph: t("dm.ph"), aria: t("dm.your"), bad: domBad, max: 253, mode: "url", disabled: locked, enter: () => { if (domReady(u)) checkDomain(); } }))),
      subBad ? h("span", { class: "qs-err", role: "alert", text: t("dm.subBad") }) : domBad ? h("span", { class: "qs-err qs-err--warn", role: "alert", text: t("dm.domBad") }) : null,
      // поддомен необязателен (решение владельца): пусто — адрес сам домен, A-запись «@»; это обычная подсказка, не ошибка
      // пока виден вопрос «Ваш домен — …?» — «Оставьте пустым» не к месту, его прячем
      guess ? null : h("p", { class: "qs-small", "data-k": "dm-sub-hint", text: t("dm.subHint") }),
      guess ? h("p", { class: "qs-small qs-dguess", "data-k": "dm-sub-guess" }, h("span", { text: t("dm.subGuess", guess) }),
        btn(t("dm.subGuessMove"), "tertiary", () => {
          u.sub = guess.s; u.dom = guess.d; domainEdited(); render();
          setTimeout(() => { const f = document.querySelector('[data-k="dm-sub"]'); if (f) f.focus({ preventScroll: true }); }, 0);   // кнопка ушла — фокус в поле
        }, { sm: true, k: "dm-sub-move" })) : null,
      !sub && domOk(u.dom) ? h("p", { class: "qs-small", "data-k": "dm-sub-none", text: t("dm.subNone") }) : null,
      locked ? h("p", { class: "qs-small", role: "status", "data-k": "dm-locked", text: t("dm.locked", { d: u.checked || addr }) }) : null,
      h("p", { class: "qs-daddr" }, h("span", { text: t("dm.addr") }), h("code", { "data-empty": addr ? null : "", text: addr || t("dm.ph") }))]);
    const rec = [[t("dm.aType"), "A"], [t("dm.aName"), sub || "@"], [t("dm.aValue"), ip() || "—"], [t("dm.aTtl"), "300"]];
    const arec = dStep(3, ok, t("dm.s3"), t("dm.s3Why"), [
      h("div", { class: "qs-arec", "data-hl-target": "ipcopy" }, rec.map((r, i) => h("div", { class: "qs-arec__r" },
        h("span", { class: "qs-arec__k", text: r[0] }), h("code", { class: "qs-arec__v" + (i === 2 ? " qs-arec__v--ip" : ""), title: r[1], text: r[1] }),
        r[1] !== "—" ? copyBtn(r[1]) : h("span"))))], { tip: warnTip(t("dm.cfTip"), "cf") });
    const check = dStep(4, ok, t("dm.s4"), t("dm.s4Why"), [lastTry(u), dnsBlock(u)], { hl: "dns" });
    return card({ class: "qs-card qs-dsteps", "data-k": "dm-steps" }, buy, names, arec, check);
  }
  VIEW.domain = () => {
    const u = ui("domain"), main = [domainSteps(u)];
    const ok = !!(u.dns && u.dns.dns === "ok" && u.checked === domFull(u)), issued = !!(S.res.cert && S.res.cert.ok);
    // ход выпуска виден, пока он идёт (R-333 С3) — и когда ответ DNS ещё не пришёл после перезагрузки
    if (ok || running(["cert"])) {
      main.push(certCard(u));
      if (issued && !running(["cert"])) main.push(moveNote());
      if (issued) main.push(siteCard(u));
    }
    const busy = !!running(["cert", "site", "template", "vless-mode"]) || !!S.busy.certPrep;
    if (busy) return { main, foot: { next: { disabled: true } } };
    // домен смотрит на сервер, сертификата ещё нет — «Дальше» значит «Выпустить сертификат» (явное действие, R-333 З1);
    // смена работающего домена — кнопкой в карточке, внизу «Дальше» с прежним доменом
    // Выпуск не удался (лимит Let's Encrypt, закрытый 80 у хостера) — внизу не тупик, а «Дальше без домена»; повтор —
    // «Проверить снова» в карточке (R-333 Н1)
    const certFailed = !!(S.res.cert && !S.res.cert.ok);
    if (ok && !issued && !certFailed && !isWorkDomain(u.checked) && !(u.dns && u.dns.switch))
      return { main, foot: { next: { label: t("dm.certGo"), act: certNow, busy: !!(S.busy.cert || S.busy.certPrep) } } };
    // VLESS Ш4 М1: сайт «включится сам» — «Дальше» сначала включает его; не включился — дальше без него (не тупик)
    const sv = ok && issued ? vlessSite() : null;
    if (sv && sv.auto && !(S.res.site && !S.res.site.ok)) return { main, foot: { next: { act: siteThenNext, busy: !!S.busy.site } } };
    // домен уже работает (сертификат выдан) — обычная «Дальше», даже пока DNS нового имени не видно (R-333 З3)
    if ((ok && !certFailed) || workDomain().length) return { main, foot: { next: {} } };
    // пока домен не смотрит на сервер — «Дальше без домена»: этап пропущен (в рельсе жёлтый). Пока идёт первая
    // проверка вписанного имени — она закрыта (не пропуск по ошибке), подпись та же: панель не прыгает
    const phone = matchMedia("(max-width: 767px)").matches, label = phone ? t("dm.noDomShort") : t("dm.noDom");
    const checking = !!u.checking || (domReady(u) && !!u.checked && u.checked === domFull(u) && !u.dns && !(S.res.dns && S.res.dns.state === "error"));
    if (checking) return { main, foot: { next: { label, disabled: true, busy: true } } };
    return { main, foot: { next: { label, busy: !!S.busy.domain,
      act: async () => { const r = await step("domain", { plan: "none" }); if (r.ok) skip(); } } } };
  };

  // ── 5. Дополнительно ───────────────────────────────────────────────────────
  // «Telegram-бот» и «Ключ ИИ-агента» — общие компоненты панели (ux.js TelegramBot, AgentKey; UX-333 §2–3, №36): те же
  // обработчики панели за сессией мастера (api/setup/ux/…, kaskad.setup.web). «Копия настроек» — своя. Все свёрнуты,
  // раскрывается выбранный (T-332 №40). «Автообновление подписки выходов» убрано: подписка — в поле этапа 3 (№37).
  ENTER.extras = () => { const u = ui("extras"); if (u.open === undefined) u.open = ""; reload("extras-list"); step("rules", {}); };
  RELOAD["extras-list"] = async () => { await step("extras-list"); };
  // ответ /api/setup/ux/… -> тело {ok, …} или отказ в форме ApiError панели (message, status, data.reason)
  function uxCall(path, body) {
    return api("ux/" + path, body, { own: true }).then((r) => {
      if (r.status === 200 && r.body && r.body.ok) return r.body;
      const d = (r.body && r.body.data) || {};
      throw Object.assign(new Error((r.body && r.body.error) || t("sys.error")), { status: r.status, code: d.reason || "http", data: d });
    });
  }
  const UX_API = {
    bot: { get: () => uxCall("bot"), check: (b) => uxCall("bot/check", b), test: (b) => uxCall("bot/test", b) },
    mcp: { get: () => uxCall("mcp"), add: (b) => uxCall("mcp/keys", b), del: (fp) => uxCall("mcp/keys/" + encodeURIComponent(fp) + "/del", {}) },
  };
  const uxToast = (a, b) => toast(b ? a + " — " + b : a);
  function extraUx(it) {
    const X = UX(), u = ui("extras");
    if (!X) return card(null, note("warning", t("sys.error")));
    u.ux = u.ux || {}; u.uxLang = u.uxLang || {};
    const id = it.id, have = u.ux[id];
    if (!have || u.uxLang[id] !== LANG) {
      if (have) have.destroy();
      // раскрыт один (T-332 №40, R-333i П4): открыли этот — остальные сворачивает следующая отрисовка
      const o = { lang: LANG, open: u.open === id, onOpen: (on) => { const was = u.open; u.open = on ? id : (u.open === id ? "" : u.open); if (on && was && was !== id) render(); }, toast: uxToast };
      u.ux[id] = id === "bot" ? X.telegramBot(Object.assign(o, { api: UX_API.bot, onGoExits: () => open("exits") }))
        : X.agentKey(Object.assign(o, { api: UX_API.mcp }));
      u.uxLang[id] = LANG;
      // поле стёрто самим компонентом (токен сохранён тестом, ключ добавлен) — подсказку внизу тоже пересчитать
      try { new MutationObserver(unsentSoon).observe(u.ux[id].el, { childList: true, subtree: true, characterData: true }); } catch (e) { /* старый браузер */ }
    }
    if (u.open === id) { if (u.ux[id].open) u.ux[id].open(); }   // раскрыт по выбору мастера (сцена, возврат на этап)
    else if (u.ux[id].close) u.ux[id].close();
    // ввод в поле компонента — подсказка внизу сразу, а не со следующей отрисовкой (R-333i З2)
    return h("div", { class: "qs-xux", "data-hl-target": id === "bot" ? "xbot" : "xmcp", on: { input: unsentSoon } }, u.ux[id].el);
  }
  // вписано, но не сохранено: токен бота (сохраняет «Отправить тест») и ключ агента («Добавить») — подписи кнопок берутся
  // у самих компонентов (R-333i З2)
  function unsentHint() {
    const val = (sel) => { const n = document.querySelector(sel); return n ? String(n.value || "").trim() : ""; };
    const bot = !!val('[data-ux="bot"] .ux-in--token'), key = !!val('[data-ux="agent"] textarea'), X = UX();
    if (!bot && !key) return "";
    const b = { bot: X ? X.t(LANG, "tb.send") : "", key: X ? X.t(LANG, "c.add") : "" };
    return bot && key ? t("x.unsentBoth", b) : bot ? t("x.unsentBot", b) : t("x.unsentKey", b);
  }
  let unsentTimer = 0;
  function unsentSoon() {
    clearTimeout(unsentTimer);
    unsentTimer = setTimeout(() => { const u = ui("extras"); if (cur() === "extras" && unsentHint() !== (u.unsent || "")) render(); }, 0);
  }
  function backupBody(it) {
    const u = ui("x-backup"), res = S.res.backup;
    if (it.available === false) return [note("info", "", it.text || t("c.soon"))];
    const go = async () => {
      const r = await step("backup", { password: u.pw || "" });
      u.pw = "";
      const d = r.ok && r.data;                          // data.file — текст копии (.kbk): сохранить в браузере, нигде не держать
      if (d && d.file) { saveFile(d.name || "kaskad-backup.kbk", String(d.file), "application/octet-stream"); d.file = null; }
    };
    return [field(t("x.bakPw"), input("bak-pw2", u.pw, (v) => { u.pw = v; }, { type: "password", ac: "new-password" }), t("x.bakPwH"),
      { row: btn(t("x.bakDl"), "tertiary", go, { icon: "arrow-down-to-line", busy: !!S.busy.backup, disabled: (u.pw || "").length < 8, k: "bak-dl" }) }),   // 8 — backup.MIN_PASSWORD, как в панели и SSH
    res ? resNote(res) : null];
  }
  // «Россия напрямую» (R7, SPEC-stage1-rf-mark-builtin §5.3, D-019): по умолчанию на новом узле — всё через выход.
  // Включить — только с отдельной галочкой ⚠РФ «Скрипт не используется на территории РФ», по умолчанию снятой: блок и
  // текст галочки — из ответа шага rules (ядро, rfgate), «Сохранить» неактивна без галочки; сервер без неё ответит
  // need_input и ничего не поменяет. С галочкой уходят и коды показанных организаций (rf.orgs): согласие — только на
  // них; список успел измениться — need_input с новым блоком, галочку ставить заново (SEC-s1-r67 №1).
  // Уже выбрана, но без подтверждения (legacy — работает как до обновления; need — не включена) и новая организация в
  // списке (full + uncovered) — сразу блок, галочка и «Подтвердить», а не «Включить» (REV-s1-r67 П-2).
  function rulesItem() {
    const u = ui("x-rules"), x = ui("extras"), r = S.res.rules, d = (r && r.data) || {}, rf = d.rf || {};
    const isOpen = x.open === "rules", on = d.profile === "ru_direct" && rf.mode === "full";
    const fresh = on && arr(rf.uncovered).length > 0, ask = d.profile === "ru_direct" && !on;
    const st = !d.profile ? chip(t("c.loading"), "off") : fresh ? chip(t("x.ruNew"), "warning") : on ? chip(t("x.on"), "success")
      : ask ? chip(t("x.ruNeed"), "warning") : chip(t("x.ruOff"), "off");
    const off = (k) => btn(t("x.ruTurnOff"), "inverse", () => step("rules", { ru_direct: false }), { sm: true, busy: !!S.busy.rules, k });
    const save = async () => {
      const res = await step("rules", { ru_direct: true, confirm_not_rf: u.tick === true, orgs: arr(rf.orgs) });
      if (res && res.ok && res.state !== "need_input") { u.want = false; u.tick = false; }
      else if (res && res.state === "need_input") u.tick = false;      // блок мог измениться — галочку заново
    };
    const tick = (label) => [note("warning", rf.block || "", ""),
      choice({ on: !!u.tick, k: "ru-tick", line: true, pick: () => { u.tick = !u.tick; render(); },
        kids: [h("div", { class: "qs-choice__b" }, h("span", { class: "qs-choice__t", text: rf.consent || "" })), box()] }),
      h("div", null, btn(label, "tertiary", save, { busy: !!S.busy.rules, disabled: !u.tick, k: "ru-save" }))];
    const body = [];
    if (on && !fresh) {
      body.push(note("success", t("x.ruOnNow"), "", { btns: [off("ru-off")] }));
    } else if (ask || fresh) {
      const why = fresh ? t("x.ruNewD") : rf.mode === "legacy" ? t("x.ruLegacyD") : t("x.ruNeedD");
      body.push(note("info", why, "", { btns: [off("ru-off")] }));
      body.push(...tick(t("x.ruConfirm")));
    } else if (d.profile) {
      body.push(choice({ on: !!u.want, k: "ru-want", line: true, pick: () => { u.want = !u.want; if (!u.want) u.tick = false; render(); },
        kids: [h("div", { class: "qs-choice__b" }, h("span", { class: "qs-choice__t", text: t("x.ruWant") }), h("span", { class: "qs-found__d", text: t("x.ruWantD") })), box()] }));
      if (u.want) body.push(...tick(t("x.ruSave")));
    }
    if (r && r.text) body.push(resNote(r));
    return h("section", { class: "qs-x", "data-open": isOpen ? "" : null, "data-hl-target": "xrules" },
      h("button", { type: "button", class: "qs-x__h", "aria-expanded": isOpen ? "true" : "false", "data-k": "x-rules", on: { click: () => { x.open = isOpen ? "" : "rules"; render(); } } },
        h("span", { class: "qs-card__ic" }, icon("globe", 18)),
        h("span", { class: "qs-card__tt" }, h("span", { class: "qs-card__title", text: t("x.ruTitle") }), h("span", { class: "qs-card__sub", text: t("x.ruDesc") })), st, icon("chevron-right", 16)),
      isOpen ? h("div", { class: "qs-x__b" }, body) : null);
  }
  VIEW.extras = () => {
    const u = ui("extras"), r = S.res["extras-list"], items = arr(r && r.data && r.data.items);
    const main = items.length ? [rulesItem()].concat(items.map((it) => {
      if (it.id === "bot" || it.id === "mcp") return extraUx(it);
      const isOpen = u.open === it.id, res = S.res.backup;
      const st = it.available === false ? chip(t("x.soon"), "off") : res && res.state === "error" ? chip(t("x.err"), "danger")
        : res && res.state === "ok" ? chip(t("x.on"), "success") : chip(t("x.off"), "off");
      return h("section", { class: "qs-x", "data-open": isOpen ? "" : null, "data-hl-target": it.hl || "xbak" },
        h("button", { type: "button", class: "qs-x__h", "aria-expanded": isOpen ? "true" : "false", "data-k": "x-" + it.id, on: { click: () => { u.open = isOpen ? "" : it.id; render(); } } },
          h("span", { class: "qs-card__ic" }, icon("hard-drive", 18)),
          h("span", { class: "qs-card__tt" }, h("span", { class: "qs-card__title", text: it.title }), h("span", { class: "qs-card__sub", text: it.desc })), st, icon("chevron-right", 16)),
        isOpen ? h("div", { class: "qs-x__b" }, backupBody(it)) : null);
    })) : [r && !r.ok ? resNote(r, { retry: () => reload("extras-list") }) : card(null, h("p", { class: "qs-muted", text: t("c.loading") }))];
    // токен бота или ключ агента вписаны, но не отправлены — сказать до «Дальше» (поля живут в компонентах)
    u.unsent = unsentHint();
    return { main, foot: { next: { hint: u.unsent } } };
  };

  // ── 6. Готово ──────────────────────────────────────────────────────────────
  const PF_ORDER = ["ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus", "other"];
  ENTER.done = () => {
    const u = ui("done"), a = (S.st && S.st.answers) || {};
    if (!u.tab) u.tab = classic() ? "check" : "connect";
    if (!u.pf) u.pf = "ios";
    if (u.who === undefined) u.who = "";
    if (!classic()) {
      if (!S.res.platforms) step("platforms");
      u.offers = {};                                     // протоколы могли поменяться на этапе 2
      if (a.device && !u.device) {                       // устройство уже создано: не плодить второе после перезагрузки
        u.device = a.device; u.devWho = a.device_user || ""; u.devPf = a.device_platform || u.pf;
        u.who = u.who || a.device_user || ""; u.pf = a.device_platform || u.pf;
        u.devName = devName(u.devWho, u.pf);
        if (!u.inp && a.issued_kind) u.inp = a.issued_kind;      // тот же вход, что выдавали (VLESS — снова VLESS)
      }
      const offers = loadOffers(u.pf);
      // «Выдать заново» с этапа «Домен и сайт» (T-M6-qs330e №1): новые настройки того же устройства — сразу
      if (u.reissue && u.device) {
        u.reissue = false;
        Promise.resolve(offers).then(() => { const ok = offersOf(u.pf).list.some((o) => o.inbound === u.inp); return doIssue(ok ? u.inp : undefined); }).catch(() => {});
      }
      if (u.device && u.issue) startConnPoll();
    } else fwPeerCheck();
    if (u.tab === "summary") step("summary");
  };
  function platformsList() {
    const r = S.res.platforms, list = arr(r && r.data && r.data.platforms);
    if (list.length) return list;
    const pt = (S.product && S.product.platforms) || {};
    return PF_ORDER.filter((k) => pt[k]).map((k) => ({ id: k, title: pt[k] }));
  }
  const pfTitle = (id) => ((platformsList().find((p) => p.id === id) || {}).title || id);
  async function loadOffers(pf) {
    const u = ui("done");
    u.offers = u.offers || {};
    if (u.offers[pf]) return;
    u.offers[pf] = { loading: true, offers: [], notes: [] };
    const r = await api("step/platforms", { platform: pf }).catch(() => null);
    if (r && r.status === 200 && r.body.ok) u.offers[pf] = r.body.data;
    else u.offers[pf] = { failed: (r && r.body && (r.body.text || r.body.error)) || t("sys.noConn"), offers: [], notes: [] };
    render();
  }
  function offersOf(pf) {
    const u = ui("done"), d = u.offers && u.offers[pf];
    return { list: arr(d && d.offers).filter((o) => o.enabled), notes: arr(d && d.notes), loaded: !!d && !d.loading && !d.failed, failed: d && d.failed };
  }
  function startConnPoll() {
    const u = ui("done");
    const tick = async () => {
      if (!u.device) return;
      const r = await api("step/connection", { device: u.device }).catch(() => null);
      if (!r || r.status !== 200) return;
      // перерисовка — только когда ответ другой: иначе раз в 4 с окно выдачи вынималось бы из страницы, и терялись
      // фокус, выделение текста ключа и нажатие (FIX-332g №3); секунды «ждём …» идут сами (data-since)
      const key = JSON.stringify([r.body.data || {}, r.body.text || ""]);
      if (u.connKey === key) return;
      u.connKey = key; u.conn = r.body.data || {}; u.connRes = r.body; if (u.conn.conn === "traffic") stop("conn"); render();
    };
    tick();
    every("conn", 4, tick);
  }
  // имя устройства «{кто} · {платформа}»: без имени человека — «Устройство · …», а не «Без имени · …» — имя ключа в
  // приложении берёт его без платформы (issue_os.device_title), и слово-заглушка туда не попадает (R-333-qr, ≤2: №8).
  // Не длиннее 40 знаков (names.MAX, как считает сервер — кодовыми точками): длинное «кто» — с «…» в конце, платформа
  // целиком (как names.fit; REV-s1-t13 Л-1 — иначе человек от 25 знаков не получал устройства)
  const NAME_MAX = 40;
  const cps = (s) => Array.from(String(s).normalize("NFC").trim().split(/\s+/).join(" ")).length;
  function devName(who, pf) {
    const w0 = String(who || "").trim(), p = pfTitle(pf);
    const w = w0 && w0 !== t("d.noName") ? w0 : t("d.anyDevice");
    let s = t("d.devName", { who: w, p });
    const ch = Array.from(w.normalize("NFC").split(/\s+/).join(" "));
    // хвост не висит на «…»: то же, что names._invisible сервера — знаки формата (ZWJ, теги флага Англии), селекторы
    // варианта, заполнители, несамостоятельные комбинирующие (Mn, Me; Mc — видимый, остаётся), пробелы (REV-s1-t13-2 Л-1а)
    const loose = /[\p{Cf}\p{Mn}\p{Me}\p{Co}\p{Cc}\p{Zs}\p{Zl}\p{Zp}\p{Cs}\s\u115f\u1160\u2800\u3164\uffa0\u034f\u17b4\u17b5\u180b-\u180d\u180f\ufe00-\ufe0f\u{e0100}-\u{e01ef}]/u;
    while (cps(s) > NAME_MAX && ch.length) {
      ch.pop();
      while (ch.length && loose.test(ch[ch.length - 1])) ch.pop();
      s = t("d.devName", { who: ch.join("") + "\u2026", p });
    }
    return s;
  }
  async function issueDevice() {
    const u = ui("done"), pf = u.pf, who = (u.who || "").trim() || t("d.noName");
    const offers = offersOf(pf).list, inp = u.inp && offers.some((o) => o.inbound === u.inp) ? u.inp : (u.inp ? "" : (offers[0] || {}).inbound);
    if (!inp) return;                                   // выбранного протокола на этой платформе нет — не подменяем
    // «Выдать заново»: прежнее окно выдачи остаётся на месте приглушённым, пока сервер не ответил, — высота шага та же,
    // кнопка «Выдать» не уезжает из-под курсора (решение 3.3.2 №7; без колонки видео с 3.3.3 — держим сам блок)
    u.held = u.issue && u.give && u.give.issue === u.issue ? u.issue : null;
    u.issue = null; u.conn = null; u.connKey = ""; S.res.issue = null;
    if (!u.device || u.devPf !== pf || u.devWho !== who) {
      const r = await step("person", { user: who, device: devName(who, pf), platform: pf, inbound: inp || undefined });
      if (!r.ok) { u.held = null; render(); return; }
      u.device = r.data.device; u.devPf = pf; u.devWho = who; u.devName = devName(who, pf);
    }
    await doIssue(inp);
  }
  async function doIssue(inp) {
    const u = ui("done");
    const r = await step("issue", Object.assign({ device: u.device, os: u.pf }, inp ? { inbound: inp } : {}));
    u.held = null;
    if (r.ok) { u.issue = r.data; u.qrs = {}; u.inp = r.data.kind; u.issuedAt = now(); u.conn = null; u.connKey = ""; startConnPoll(); }
    render();
  }
  // Выданные настройки — тот же компонент, что окно «Выдать настройки» панели и страница человека /p/ (give.js, дизайн
  // k3/design/issue-qr): 1 Приложение · 2 Ключ (один QR, части раз в 2 с) · 3 Включите VPN; у роутера — подготовьте,
  // добавьте, проверьте; нет пары «платформа × протокол» — «Подойдёт:». Платформа и протокол выбираются выше, в мастере.
  // Компонент живёт между отрисовками (опрос подключения — раз в 4 с): иначе каждая возвращала бы части QR к первой.
  function issueCard() {
    const u = ui("done"), G = window.KaskadGive, Q = window.KaskadQR;
    const held = !u.issue && !!u.held && !!u.give && u.give.issue === u.held, d = u.issue || (held ? u.held : null);
    if (!d) return null;
    const head = h("div", { class: "qs-row" }, h("h2", { class: "qs-card__title", text: t("d.giveT") }), h("span", { class: "qs-small", text: t("d.device", { n: d.name || u.devName || "" }) }));
    if (!G) return card(null, [head, note("warning", t("d.qrFail"))]);
    const qr = (text, quiet) => { try { return h("div", { class: "gx-qrart" }, Q.svg(Q.encode(String(text || "")), "", quiet)); } catch (e) { return null; } };
    if (!u.give || u.give.issue !== d || u.give.lang !== LANG) {
      if (u.give) u.give.g.destroy();
      const g = G.mount({ viewer: "wizard", lang: LANG, device: { name: d.name || u.devName || "" },
        onProto: (k) => { u.inp = k; doIssue(k); },
        // состояние Б (FIX-332g №2): хозяин мастера — сам администратор: «Добавить устройству» добавляет протокол
        // устройству этого мастера и выдаёт его; «Включить на сервере» ведёт на этап «Режим и протоколы»
        onGrant: async (k) => { const r = await step("grant", { device: u.device, inbound: k }); if (r.ok) { u.inp = k; doIssue(k); } else render(); },
        onEnable: () => open("protocols"),
        drawQr: (it, i, q) => qr(arr(it.body)[i], q), drawLinkQr: (l) => qr(l.url), save: saveFile, iconBase: "../static/" });   // иконки — статика панели: /static/apps/<id>.png
      g.set(d);
      u.give = { g, issue: d, lang: LANG };
    }
    // прежние настройки, пока выдаются новые: видны приглушёнными и не нажимаются (inert), чтение с экрана — «занято»
    return card(held ? { "data-held": "", inert: "", "aria-busy": "true" } : null, [head, h("div", { class: "qs-give", "data-hl-target": "qr" }, u.give.g.el)]);
  }
  // ушли с шага 6 (другой этап, выдача сброшена): окно выдачи снимается — таймер частей QR, слушатели document и
  // ширины экрана не живут без него (FIX-332g №17); вернулись — issueCard() смонтирует заново
  function dropGive() {
    const u = S.ui.done;
    if (u && u.give && !u.give.g.el.isConnected) { try { u.give.g.destroy(); } catch (e) { /* уже */ } u.give = null; }
  }
  function connRows() {
    const u = ui("done"), c = (u.conn && u.conn.conn) || "wait", since = u.issuedAt || now();
    const rows = [{ state: "ok", title: t("d.created", { n: u.devName || (u.issue && u.issue.name) || "" }), detail: t("d.createdS") }];
    if (c === "connected" || c === "traffic") rows.push({ state: "ok", title: t("d.conn"), detail: t("d.connS") });
    else if (c === "stuck") rows.push({ state: "warn", title: t("d.stuck"), detail: t("d.stuckS") });
    else if (c === "unknown") rows.push({ state: "wait", title: t("d.unknown"), detail: (u.connRes && u.connRes.text) || "", since });
    else rows.push({ state: "wait", title: t("d.wait"), detail: t("d.waitS"), since });
    if (c === "traffic") rows.push({ state: "ok", title: t("d.traffic"), detail: t("d.trafficS", { size: size(u.conn.bytes || 0) }) });
    else if (c === "connected") rows.push({ state: "wait", title: t("d.trafficWait"), detail: t("d.trafficWaitS"), since: u.conn.online_since || since });
    else rows.push({ state: "idle", title: t("d.trafficIdle"), detail: t("d.trafficIdleS") });
    const out = [liveList("", rows, { hl: "status" })];
    const hints = arr(u.conn && u.conn.hints);
    if (c === "stuck" && hints.length) {
      out.push(h("div", { class: "qs-hints", role: "status" }, h("b", { text: t("d.hintsT") }), hints.map((x, i) => h("div", { class: "qs-hint" }, h("i", { text: String(i + 1) }), h("span", { text: x.text }),
        x.action && x.action.label ? btn(x.action.label, "tertiary", () => { u.inp = x.action.inbound; doIssue(x.action.inbound); }, { sm: true, k: "hint-" + x.code }) : null))));
    }
    return out;
  }
  function connectView() {
    const u = ui("done"), plats = platformsList(), of = offersOf(u.pf);
    // протокол и платформа — независимые выборы (владелец, 3.3.2): выбранный протокол не подменяется другим при смене
    // платформы; на этой платформе его нет — честная строка, «Выдать» ждёт выбора
    const kept = !!u.inp && of.loaded && !of.list.some((o) => o.inbound === u.inp);
    const inp = kept ? "" : u.inp && of.list.some((o) => o.inbound === u.inp) ? u.inp : (of.list[0] || {}).inbound;
    const off = of.list.find((o) => o.inbound === inp);
    const issued = !!u.issue, changed = issued && (u.devPf !== u.pf || u.inp !== inp || u.devWho !== ((u.who || "").trim() || t("d.noName")));
    const main = [card({ "data-hl-target": "device" },
      field(t("d.who"), input("who", u.who, (v) => { u.who = v; }, { ph: t("d.whoPh"), max: 40 })),
      h("span", { class: "qs-field__l", text: t("d.platform") }),
      h("div", { class: "qs-grid-platforms", role: "radiogroup", "aria-label": t("d.platform") }, plats.map((p) => h("button", { type: "button", role: "radio", "aria-checked": u.pf === p.id ? "true" : "false", "data-k": "pf-" + p.id, title: p.title,
        on: { click: () => { u.pf = p.id; loadOffers(p.id); render(); } } }, p.title))),
      h("span", { class: "qs-field__l", text: t("d.input") }),
      of.failed ? note("danger", of.failed, "", { btns: [btn(t("c.retryShort"), "inverse", () => { delete ui("done").offers[u.pf]; loadOffers(u.pf); }, { sm: true })] })
        : of.loaded && !of.list.length ? note("warning", t("d.noInputs"), "", { btns: [btn(t("head.protocols"), "inverse", () => open("protocols"), { sm: true })] })
        : inputPills(of, inp),
      // строка подсказки входа — всегда на месте (пустая, пока входы грузятся): кнопка ниже не съезжает (3.3.2 №7)
      kept ? note("warning", "", t("d.noApp", { p: inboundTitle(u.inp), os: pfTitle(u.pf) })) : h("p", { class: "qs-small qs-inp-note", text: off ? off.note : "\u00a0" }),
      of.notes.length ? h("p", { class: "qs-small", text: of.notes.join(" ") }) : null,
      // кнопка — на своём месте и после выдачи («Настройки выданы»): страница не прыгает под курсором (3.3.2 №7)
      h("div", { class: "qs-btns" }, issued && !changed
        ? btn(t("d.issued"), "tertiary", null, { disabled: true, icon: "check", k: "issue" })
        : btn(issued ? t("d.issueAgain") : t("d.issue"), issued ? "tertiary" : "primary", () => issueDevice(),
          { disabled: !inp, busy: !!(S.busy.person || S.busy.issue), icon: "qr-code", k: "issue" })),
      S.res.person && !S.res.person.ok ? resNote(S.res.person) : null, S.res.issue && !S.res.issue.ok ? resNote(S.res.issue) : null, S.res.grant && !S.res.grant.ok ? resNote(S.res.grant) : null)];
    // «Выдать заново»: пока сервер не ответил, прежнее окно выдачи и строки подключения на месте (issueCard: held)
    const holding = !issued && !!u.held && !!u.give && u.give.issue === u.held;
    if (issued || holding) main.push(issueCard(), ...connRows());
    return main;
  }
  // входы платформы; пока список не пришёл — заготовки той же высоты в том же ряду (3.3.2 №7: ряд появлялся позже и
  // сдвигал «Выдать настройки» из-под курсора)
  function inputPills(of, inp) {
    const u = ui("done");
    if (!of.loaded) return h("div", { class: "qs-pills qs-pills--inp", "aria-busy": "true", "aria-label": t("d.input") },
      [0, 1, 2].map(() => h("span", { class: "qs-pill qs-sk", "aria-hidden": "true" }, h("i"))));
    return h("div", { class: "qs-pills qs-pills--inp", role: "radiogroup", "aria-label": t("d.input") }, of.list.map((o, i) => h("button", { type: "button", class: "qs-pill", role: "radio", "aria-checked": o.inbound === inp ? "true" : "false", "data-k": "inp-" + o.inbound,
      on: { click: () => { u.inp = o.inbound; render(); } } }, o.title, i === 0 ? h("span", { class: "qs-pill__tag", text: t("d.offer") }) : null)));
  }
  function inboundTitle(kind) {
    const pl = ((S.res["protocols-plan"] || {}).data || {}).protocols;
    const p = arr(pl).find((x) => x.kind === kind);
    if (p) return p.title;
    for (const k of Object.keys((ui("done").offers) || {})) { const o = arr(ui("done").offers[k].offers).find((x) => x.inbound === kind); if (o) return o.title; }
    return kind || "—";
  }
  // PortsSummary (3.3.3, UX-333 §7): «Откройте порты у хостера» — настоящие порты сервера (шаг «summary»: входы, что
  // включены, пробросы, сайт на домене, панель); «Скопировать списком» — каждый порт один раз, по строке
  function portRows(p) {
    const rows = [], seen = new Set();
    const grp = (list) => { const by = {}; list.forEach((x) => { const [n, pr] = String(x).split("/"); (by[pr || "tcp"] = by[pr || "tcp"] || []).push(n); }); return Object.keys(by).map((pr) => by[pr].join(", ") + "/" + pr).join(" · "); };
    const add = (name, list) => { list = list.filter(Boolean); if (!list.length) return; list.forEach((x) => seen.add(x)); rows.push([name, grp(list)]); };
    // протоколы IP без портов (PPTP — GRE, IP 47): строкой «GRE для PPTP · GRE (47)», в список — «gre» (R-333 З6)
    arr(p.inbounds).forEach((x) => { add(x.title || inboundTitle(x.kind), arr(x.ports));
      arr(x.ip_protos).forEach((g) => { const n = String(g.name || "").toLowerCase(); if (!/^[a-z]+$/.test(n) || seen.has(n)) return;
        seen.add(n); rows.push([t("ps.ipProto", { p: n.toUpperCase(), t: x.title || inboundTitle(x.kind) }), n.toUpperCase() + (g.num ? " (" + g.num + ")" : "")]); }); });
    arr(p.forwards).forEach((f) => add(t("ps.forward"), [f.port + "/" + f.proto]));
    if (p.domain) { if (!seen.has("443/tcp")) add(t("ps.site"), ["443/tcp"]); add(t("ps.le"), ["80/tcp"]); }
    if (p.panel && !seen.has(p.panel + "/tcp")) add(t("ps.panel"), [p.panel + "/tcp"]);
    return { rows, list: [...seen] };
  }
  function portsSummary(d) {
    const p = d && d.ports;
    if (!p) return null;
    const { rows, list } = portRows(p);
    if (!rows.length) return null;
    const said = t("ps.copied", { n: list.length, w: tn("c.ports", list.length) });
    return h("section", { class: "qs-card qs-ports-card", "data-k": "ports-summary" },
      h("div", { class: "qs-card__h" }, h("div", { class: "qs-card__tt" }, h("h2", { class: "qs-card__title", text: t("ps.title") }),
        h("span", { class: "qs-card__sub", text: t("ps.why") })),
      h("div", { class: "qs-card__end" }, btn(t("ps.copy"), "tertiary", () => copyText(list.join("\n"), said), { icon: "copy", k: "ports-copy" }))),
      h("ul", { class: "qs-plist" }, rows.map((r) => h("li", null, h("span", { class: "qs-plist__n", text: r[0] }), h("code", { class: "qs-plist__p", text: r[1] })))));
  }
  function summaryView() {
    const r = S.res.summary, d = (r && r.data) || null, u = ui("done");
    if (!d) return [r && !r.ok ? resNote(r, { retry: () => step("summary") }) : card(null, h("p", { class: "qs-muted", text: t("c.loading") }))];
    const row = (k, v, o) => { o = o || {};
      return [h("dt", { text: k }), h("dd", null, h("span", { class: o.mono ? "qs-mono" : null, title: v, text: v }), o.copy ? copyBtn(v) : null, o.chip ? h("span", { class: "qs-cred__chip" }, o.chip) : null, o.btn || null)]; };
    const rows = [row(t("d.sPanel"), d.panel_url || "—", { mono: true, copy: !!d.panel_url })];
    if (classic()) {
      const f = ((S.st && S.st.answers) || {}).forward;
      if (f) rows.push(row(t("d.sForward"), f.port + "/" + f.proto + " → " + (f.to ? f.to + ":" + f.to_port : "—"), { mono: true }));
    } else {
      rows.push(row(t("d.sProtocols"), arr(d.protocols).map((k) => (d.titles || {})[k] || inboundTitle(k)).join(", ") || "—", { chip: arr(d.protocols).length ? chip(t("d.cWork"), "success") : null }));
      const gh = d.github || {};
      if (gh.state === "ok" && arr(d.protocols).some((k) => /^awg/.test(k))) rows.push(row(t("d.sGithub"), gh.from ? t("d.ghFound", { from: gh.from }) : (gh.text || ""), { chip: chip(t("d.cOk"), "success") }));
      const ex = d.exits || {};
      // «Выходы» как в ExitsInput (UX-333 §8): ключи вручную по именам, подписка — одной записью «имя (N ключей)»
      const exNames = arr(ex.names).concat(arr(ex.subs).map((x) => t("d.sExitsSub", { name: x.name, n: x.keys, k: tn("c.keys", x.keys) })));
      if (ex.total) rows.push(row(t("d.sExits"), exNames.join(", ") || "—", { chip: chip(t("d.cAlive", { a: ex.alive, n: ex.total }), ex.alive === ex.total ? "success" : ex.alive ? "warning" : "danger") }));
      const c = u.conn && u.conn.conn;
      arr(d.people).forEach((p) => rows.push(row(t("d.sPeople"), peopleText(p), { chip: personChip(p, c) })));
    }
    rows.push(row(t("d.sDomain"), d.domain || t("d.noDomain"), { mono: !!d.domain }));
    arr(d.skipped).forEach((k) => { const st = stageOf(k); if (st) rows.push(row(t("d.sSkipped"), st.title, { chip: chip(t("d.cSkipped"), "warning"), btn: btn(t("d.sBack"), "tertiary", () => open(k), { sm: true, k: "back-" + k }) })); });
    return [portsSummary(d), h("section", { class: "qs-card qs-card--flush" }, h("dl", { class: "qs-cred qs-cred--plain" }, rows))].filter(Boolean);
  }
  // человек и его устройства без повтора имени (3.3.2 №13: «Телефон: Телефон · iPhone и iPad»): устройство мастера
  // называется «<кто> · <платформа>» (d.devName) — имя человека в начале не повторяем, совпадает целиком — один раз
  function peopleText(p) {
    const name = String(p.name || ""), sep = t("d.devName", { who: "", p: "" });
    // имя человека в начале устройства не повторяем — и урезанное «…» (devName: «Бабушка Валентина Петро… · iPhone»)
    const full = name.normalize("NFC").split(/\s+/).join(" ");
    const own = (x) => {
      if (!name || !sep) return x;
      if (x.indexOf(name + sep) === 0) return x.slice((name + sep).length);
      const i = x.indexOf("\u2026" + sep);
      return i > 0 && full.indexOf(x.slice(0, i)) === 0 ? x.slice(i + 1 + sep.length) : x;
    };
    const devs = arr(p.devices).map(String).map((x) => (x === name ? "" : own(x))).filter(Boolean);
    return devs.length ? name + ": " + devs.join(", ") : name;
  }
  // «на связи» — по факту сервера (сборщик, то же, что «Устройство подключилось»), а не только по опросу этой вкладки:
  // после перезагрузки страницы подключённое устройство не должно стать «ждём»
  function personChip(p, c) {
    const u = ui("done"), mine = u.devWho === p.name || !!(u.issue && arr(p.devices).indexOf(u.issue.name) >= 0);
    const on = arr(p.online).length > 0 || (mine && (c === "connected" || c === "traffic"));
    if (on) return chip(t("d.cOnline"), "success");
    if (!mine) return null;
    return chip(c === "stuck" ? t("d.cStuck") : t("d.cWaiting"), "warning");
  }
  async function fwPeerCheck() {
    const u = ui("done"), f = ((S.st && S.st.answers) || {}).forward;
    if (!f || !f.to) return;
    u.peer = null;
    const r = await api("step/forward-check", { to: f.to, to_port: f.to_port, proto: f.proto }).catch(() => null);
    u.peer = r && r.status === 200 ? r.body : { ok: false, text: t("sys.error") };
    render();
  }
  function checkView() {
    const u = ui("done"), f = ((S.st && S.st.answers) || {}).forward;
    if (!f) return [note("warning", t("d.fwNone"), "", { btns: [btn(t("c.back"), "inverse", () => open("forward"), { sm: true })] })];
    const to = f.to ? f.to + ":" + f.to_port : "—";
    const peer = u.peer, rows = [{ state: "ok", title: t("d.fwMade"), detail: t("d.fwMadeS", { p: f.port, proto: f.proto, to }) }];
    if (f.proto === "udp") rows.push({ state: "idle", title: t("d.fwPeer", { to }), detail: t("d.fwPeerUdp") });
    else if (!peer) rows.push({ state: "wait", title: t("d.fwPeerWait", { to }), detail: "", since: now() });
    else rows.push({ state: peer.ok ? "ok" : "err", title: t("d.fwPeer", { to }), detail: peer.text || (peer.error || {}).text || "" });
    rows.push({ state: "warn", title: t("d.fwOutside", { p: f.port }), detail: t("d.fwOutsideS", { p: f.port, proto: f.proto }) });
    return [liveList("", rows, { hl: "fwcheck", acts: [null, peer && !peer.ok ? btn(t("c.retry"), "tertiary", fwPeerCheck, { sm: true }) : null] }),
      h("div", { class: "qs-field", "data-hl-target": "fwaddr" }, h("span", { class: "qs-field__l", text: t("d.fwConnect", { addr: "" }).replace(/:\s*$/, "") }), code((ip() || "IP") + ":" + f.port))];
  }
  async function finish() {
    S.busy.finish = true; render();
    try {
      const r = await api("finish", {}, { raw: true });
      if (r.status === 200) {
        toast(t("d.closing"));
        const url = r.body.proxy_url || r.body.panel_url || "/";
        if (MOCK) { showSys("closed"); return; }
        location.assign(url);
        return;
      }
      toast(r.body.error || t("sys.error"));
    } catch (e) { /* плашка связи */ }
    delete S.busy.finish; render();
  }
  // «Готово»: «Подключение» и «Сводка», в конце — «Открыть панель». Схема пути и тур по панели — не здесь: они
  // перегружали первую установку (3.3.2 №14, решение владельца) и переехали в первое знакомство с самой панелью.
  // Ширина колонок одна на всех вкладках, колонка видео не пропадает (3.3.2 №12).
  VIEW.done = () => {
    const u = ui("done"), cl = classic();
    const tabs = cl ? [["check", t("d.tCheck")], ["summary", t("d.tSummary")]] : [["connect", t("d.tConnect")], ["summary", t("d.tSummary")]];
    if (!tabs.some((x) => x[0] === u.tab)) u.tab = tabs[0][0];
    const go = (k) => { u.tab = k; if (k === "summary") step("summary"); render(); const sc = document.getElementById("qs-scroll"); if (sc) sc.scrollTop = 0; };
    const bar = h("div", { class: "qs-tabs", role: "tablist", "aria-label": t("head.done") }, tabs.map((x) => h("button", { type: "button", role: "tab", "aria-selected": u.tab === x[0] ? "true" : "false", "data-k": "dt-" + x[0], on: { click: () => go(x[0]) } }, x[1])));
    const body = u.tab === "summary" ? summaryView() : cl ? checkView() : connectView();
    const order = tabs.map((x) => x[0]), i = order.indexOf(u.tab);
    const nx = i < order.length - 1
      ? { act: () => go(order[i + 1]) }
      : { label: t("d.openPanel"), act: finish, busy: !!S.busy.finish };
    return { main: [bar].concat(body), foot: { back: i > 0 ? () => go(order[i - 1]) : undefined, next: nx } };
  };

  // ── Запуск ─────────────────────────────────────────────────────────────────
  const LINK_SYS = { used: "used", unknown: "unknown", expired: "expired", limit: "limit", revoked: "login" };
  async function start() {
    applyLook();
    const r = await api("session", undefined, { raw: true });
    S.session = r.body || {};
    if (r.status === 404) { showSys("closed"); return; }
    if (S.session.reason === "expired") { showSys("expired"); return; }
    if (!S.session.auth) { showSys(S.session.reason === "password" ? "login" : "link"); return; }
    // вошли: выбор языка до входа стирается, дальше — язык сервера (и тексты сервера на нём же)
    keepPrefLang("");
    if (LANG !== SERVER_LANG) await usePageLang(SERVER_LANG);
    S.sys = null;
    S.entered = "";                                        // после повторного входа — снова опросы и данные этапа
    S.net = { online: true, since: 0 };
    await Promise.all([loadState(), loadIpcert(), checkNet(false)]);
    if (!es) startEvents();
    render();
  }
  // Английский словарь — рядом с setup.js, с той же версией (CSP 'self'); нет файла — русские тексты
  async function loadEn() {
    if (L_EN) return true;
    let v = "";
    try { const me = document.querySelector('script[src*="setup.js"]'); v = me ? (new URL(me.src).searchParams.get("v") || "") : ""; } catch (e) { v = ""; }
    try { await load("setup-i18n-en.js" + (v ? "?v=" + encodeURIComponent(v) : "")); } catch (e) { return false; }
    // окно выдачи без своего словаря было бы по-русски среди английского — это та же ошибка загрузки языка (№17)
    try { await load("../give-i18n-en.js" + (v ? "?v=" + encodeURIComponent(v) : "")); } catch (e) { return false; }
    // общие компоненты (ux.js: выходы, бот, ключ агента) — тоже со своим словарём
    try { await load("../ux-i18n-en.js" + (v ? "?v=" + encodeURIComponent(v) : "")); } catch (e) { return false; }
    L_EN = window.KASKAD_SETUP_I18N_EN || null;
    return !!L_EN;
  }
  // Язык этой страницы: словарь, локаль чисел, <html lang>, заголовок вкладки. Сервер не трогает (его язык — шаг lang).
  // Английского словаря нет — false, страница остаётся на прежнем языке.
  async function usePageLang(v) {
    v = v === "en" ? "en" : "ru";
    if (v === "en" && !(await loadEn())) return false;
    LANG = v;
    L = v === "en" ? Object.assign({}, L_RU, L_EN) : L_RU;
    LOCALE = KL && KL.lang === v && KL.locale ? KL.locale : (v === "en" ? "en-US" : "ru-RU");
    NF1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 });
    try { document.documentElement.setAttribute("lang", v); } catch (e) { /* нет документа */ }
    document.title = t("c.docTitle");
    return true;
  }
  // Выбор языка до входа — только в этом браузере; после входа стирается (start). Хранилище закрыто — без памяти.
  const PRE_LANG = "kaskad-setup-prelogin-lang";
  function prefLang() {
    try { const v = localStorage.getItem(PRE_LANG); return v === "ru" || v === "en" ? v : ""; } catch (e) { return ""; }
  }
  function keepPrefLang(v) {
    try { if (v) localStorage.setItem(PRE_LANG, v); else localStorage.removeItem(PRE_LANG); } catch (e) { /* закрытое хранилище */ }
  }
  async function loadLang() {
    const pre = prefLang();
    if (pre && pre !== LANG && await usePageLang(pre)) return;
    if (LANG === "en") await usePageLang("en");
  }
  async function boot() {
    await loadLang();
    document.title = t("c.docTitle");
    applyLook();
    // тема ОС важна только при выборе «как в системе» в панели (look.js): мастер следует ему, как панель
    try { matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { applyLook(); render(); }); } catch (e) { /* старый браузер */ }
    try { matchMedia("(max-width: 767px)").addEventListener("change", () => { applyLook(); render(); }); } catch (e) { /* старый браузер */ }
    const link = QS.get("link");
    if (link && Object.prototype.hasOwnProperty.call(LINK_SYS, link)) {
      try { const u = new URL(location.href); u.searchParams.delete("link"); history.replaceState(null, "", u.pathname + u.search + u.hash); } catch (e) { /* старый браузер */ }
      if (link !== "revoked") {
        try { const r = await api("session", undefined, { raw: true }); S.session = r.body || {}; } catch (e) { /* покажем экран и без сессии */ }
        showSys(LINK_SYS[link]);
        return;
      }
    }
    try { await start(); } catch (e) { if (!e.sys) showSys("noconn"); }
  }
  // режим просмотра для разработки (только ?mock=1 вне секретного пути)
  window.KaskadSetup = MOCK ? { S, ui, render, step, stage, loadState, onJob, enter, reload, showSys, setOffline, toast, prepMove, stop } : undefined;
  function load(src) {
    return new Promise((ok, no) => { const el = document.createElement("script"); el.src = src; el.onload = ok; el.onerror = no; document.head.append(el); });
  }
  function go() {
    if (!MOCK) { boot(); return; }
    load("../dev-mock-install.js").catch(() => null).then(() => load("dev-mock-setup.js")).then(() => load("../dev-mock-ux.js").catch(() => null))
      .then(() => load("dev-view-setup.js").catch(() => null))
      .then(() => (LANG === "en" ? load("../dev-mock-en.js").catch(() => null) : null)).then(boot, () => { MOCK = false; boot(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
})();
