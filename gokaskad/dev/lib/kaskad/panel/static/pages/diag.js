/* Kaskad 3 — раздел «Диагностика» (#diag): вкладки «История» и «Расписание» проверок сервера (B15;
   k3/handoff/SPEC-node-score-schedule-share.md §2.6–2.7, §3.4–3.5, §11.1 п. 24–25; макет Claude Design «Kaskad Балл
   расписание и история», export-2026-10-19-r3). Грузит его K.loadPage("diag"): pages/diag-i18n-ru.js (+ -en.js), затем
   этот файл; он регистрирует раздел через K.page(id, def).

   Данные — только API §2.6/§3.4 через K.API.host.bench (app.js): GET/PUT расписания, предпросмотр черновика, список и
   деталь проверок, сравнение, выгрузка файлом. Правил расписания в этом файле нет (§11.1 п. 25): пределы, промежутки и
   «полных в день» проверяет сервер; ошибки у полей и выключенная «Сохранить» — только по `errors` ответа предпросмотра
   (при любом статусе ответа: 200 и 400), цена — числа `cost` ответа. Время записей и дни истории — по часам узла
   (`tz` ответа GET расписания); `tz` уходит в PUT ровно таким, каким пришёл (и null).

   Классы — дизайн-системы (gx-*, ga-btn) и новые по HANDOFF макета (gx-sched, gx-wheel, gx-propose, gx-retry, gx-hrow —
   components.css; «Проверка сервера r1» 23.10: gx-chk, gx-rel, gx-ct, gx-mct, gx-mk, gx-cl, gx-days, gx-day, gx-daytip —
   check.css); атрибутов style нет (CSP), переменные значения — CSSOM. Без шаблонных строк. */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const { el, t, tn, U } = K;
  const ID = "diag";
  // «Расписание» с круга 1 Т1 — не вкладка «Проверки сервера», а часть «Настройки › Проверка сервера» (schedule(box) ниже)
  const TABS = ["now", "history"];
  let SCHED = false;                                             // модуль собран в «Настройках» — только расписание
  const API = () => K.API.host.bench;

  // ── чистые помощники (тесты в node: tests/test_panel_diag.py) ─────────────────────────────────────────────
  const DAYS = [1, 2, 3, 4, 5, 6, 7];                           // ISO: 1 = пн … 7 = вс (§2.1)
  const ROW_PX = 40;                                             // строка барабана, px (HANDOFF: 40, поля по 80)
  const pad2 = (n) => (n < 10 ? "0" : "") + String(n);
  const hhmm = (h, m) => pad2(h) + ":" + pad2(m);
  // барабан: выбранное = round(scrollTop / 40) в пределах 0…n-1
  function wheelPick(top, row, n) {
    const v = Math.round((Number(top) || 0) / row);
    return Math.max(0, Math.min(n - 1, v));
  }
  // барабан с клавиатуры: стрелки ±1, PageUp/PageDown ±5 (минуты) или ±3, Home/End; по кругу — нет (как список)
  function wheelStep(v, key, n) {
    const big = n > 30 ? 5 : 3;
    const to = { ArrowUp: v - 1, ArrowDown: v + 1, PageUp: v - big, PageDown: v + big, Home: 0, End: n - 1 }[key];
    if (to === undefined) return null;
    return Math.max(0, Math.min(n - 1, to));
  }
  // «HH:MM» записи -> [ч, м]; не то — [4, 0]
  function timeParts(s) {
    const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(s || ""));
    return m ? [Number(m[1]), Number(m[2])] : [4, 0];
  }
  // подпись дней строки записи: ежедневно / по будням / по выходным / «Пн, Чт»
  function daysKind(days) {
    const s = (days || []).slice().sort((a, b) => a - b).join(",");
    return s === "1,2,3,4,5,6,7" ? "daily" : s === "1,2,3,4,5" ? "weekdays" : s === "6,7" ? "weekends" : "list";
  }
  // запись для тела запроса: только поля записи (§2.1); id — у сохранённой
  function entryOut(e) {
    const o = { time: e.time, mode: e.mode, days: (e.days || []).slice(), on: !!e.on, quiet: !!e.quiet, monthly: !!e.monthly };
    if (e.id) o.id = e.id;
    return o;
  }
  // тело PUT/предпросмотра: весь список, выключатель повтора и пояс — ровно как в ответе GET (B13: и null)
  function bodyOf(list, doc, o) {
    const b = { schedule: (list || []).map(entryOut) };
    if (doc && typeof doc.retry === "boolean") b.retry = o && typeof o.retry === "boolean" ? o.retry : doc.retry;
    if (o && o.tz) b.tz = doc ? doc.tz : null;
    if (o && o.confirm) b.confirm = true;
    return b;
  }
  // ответ предпросмотра — и обычный, и отказ 400 (ApiError с телом): errors — при любом статусе
  function previewOf(r) {
    const doc = r && r.body && typeof r.body === "object" ? r.body : r;
    if (!doc || typeof doc !== "object") return { errors: [], cost: null, next: [], failed: true };
    const errors = Array.isArray(doc.errors) ? doc.errors.filter((e) => e && typeof e === "object") : [];
    return { errors, cost: doc.cost && typeof doc.cost === "object" ? doc.cost : null, next: Array.isArray(doc.next) ? doc.next : [],
      failed: !Array.isArray(doc.errors) && !doc.cost };
  }
  // поле листа для кода ошибки сервера (bench.sched.err.<code>)
  const FIELD = { time: "time", gap: "time", mode: "type", full_per_day: "type", days: "days", per_day: "days", monthly: "rep", monthly_days: "days" };
  // ошибки ответа -> по полям листа записи номер n (1…) с id и днями me: поля записи — по args.n, промежуток —
  // по id пары (args.ia / args.ib; у новой записи id нет — null), «в день» — по дню записи; чужие и общие — в form.
  // canSave — ошибок нет совсем (сервер не сохранит список с ошибкой)
  function sheetErrs(errors, n, me) {
    const id = me && me.id ? me.id : null, days = (me && me.days) || [];
    const out = { time: [], type: [], days: [], rep: [], form: [], canSave: !(errors && errors.length) };
    (errors || []).forEach((e) => {
      const text = String(e.text || e.code || "");
      const a = e.args || {};
      const mine = e.code === "gap" ? a.ia === id || a.ib === id
        : e.code === "per_day" || e.code === "full_per_day" ? days.indexOf(a.day) >= 0 : a.n === n;
      const f = FIELD[e.code];
      (mine && f ? out[f] : out.form).push(text);
    });
    return out;
  }
  // метки строки истории (§3.5; макет flags): drop — резкое падение (REV-design-2026-10-19-3 Н3-З4), retry — повтор,
  // load — под нагрузкой, skip — пропущена, failed / cancelled / partial — итог проверки
  function runFlags(r) {
    const f = [];
    if (r.drops && r.drops.length) f.push("drop");
    if (r.retry_of || r.by === "auto") f.push("retry");
    if (r.under_load) f.push("load");
    if (r.status === "skipped") f.push("skip");
    if (r.status === "failed" || r.status === "cancelled" || r.status === "partial") f.push(r.status);
    return f;
  }
  // фильтр списка: все · быстрые · полные · с проблемами (есть метка); пропуски — своим выключателем
  function runFilter(rows, f, skips) {
    return (rows || []).filter((r) => (skips || r.status !== "skipped") && (f === "quick" ? r.mode === "quick" : f === "full" ? r.mode === "full" : f === "issues" ? runFlags(r).length > 0 : true));
  }
  // «от обычного» (NS-D30): rel сервера — «96 от обычного» (100 — как обычно у этого сервера); числа нет — «обычно» ещё
  // не набралось (usual_n — дней истории — меньше нужного need: «сравнивать пока не с чем: история N из 3 дней», NS-D35)
  // или история есть, а свежего значения нет («—»).
  // Возвращает ключ и подстановки: слова — у t(), правила — сервера (rel, usual_n, rel_need)
  function relOf(rel, n, need) {
    if (typeof rel === "number" && isFinite(rel)) return { k: "dg.rel", v: { n: Math.round(rel) } };
    const k = Math.max(0, Math.floor(Number(n) || 0));
    return k < need ? { k: "dg.relNone", v: { n: k, need } } : null;
  }
  // сравнение двух проверок: «новее минус раньше» со знаком (минус — «−»), процент — от прежней; числа нет — null
  function signOf(d) {
    return typeof d !== "number" || !isFinite(d) ? null : d > 0 ? "+" : d < 0 ? "−" : "";
  }

  // ── тексты по кодам (ключи — литералами: проверка словаря видит каждый) ───────────────────────────────────
  const BY = { schedule: "dg.by.schedule", panel: "dg.by.panel", bot: "dg.by.bot", cli: "dg.by.cli", mcp: "dg.by.mcp", ssh: "dg.by.ssh", auto: "dg.by.auto" };
  const MODE = { quick: "dg.quick", full: "dg.full" };
  const TYPES = ["quick", "full"];
  const DAY_SHORT = { 1: "dg.d1", 2: "dg.d2", 3: "dg.d3", 4: "dg.d4", 5: "dg.d5", 6: "dg.d6", 7: "dg.d7" };
  const DAY_LONG = { 1: "dg.dl1", 2: "dg.dl2", 3: "dg.dl3", 4: "dg.dl4", 5: "dg.dl5", 6: "dg.dl6", 7: "dg.dl7" };
  const KIND = { daily: "dg.daily", weekdays: "dg.weekdays", weekends: "dg.weekends" };
  const LINK = { net_bw: "dg.link.net_bw", net_q: "dg.link.net_q", cpu: "dg.link.cpu", stab: "dg.link.stab", down: "dg.link.down",
    up: "dg.link.up", loss: "dg.link.loss", crypto: "dg.link.crypto" };
  const TEST = { net_down: "dg.test.net_down", net_up: "dg.test.net_up", rtt: "dg.test.rtt", loss: "dg.test.loss", jitter: "dg.test.jitter",
    dns: "dg.test.dns", cpu_1t: "dg.test.cpu_1t", cpu_mt: "dg.test.cpu_mt", chacha_1t: "dg.test.chacha_1t", aes_1t: "dg.test.aes_1t",
    steal_p95: "dg.test.steal_p95", cpu_cv: "dg.test.cpu_cv", cpu_sustain: "dg.test.cpu_sustain", cpu_canary_cv: "dg.test.cpu_canary_cv",
    net_cv: "dg.test.net_cv", mem_gb: "dg.test.mem_gb", mem_bw: "dg.test.mem_bw", disk_rr: "dg.test.disk_rr", disk_rw: "dg.test.disk_rw",
    disk_fsync: "dg.test.disk_fsync", disk_seq: "dg.test.disk_seq", now_loss: "dg.test.now_loss", now_rtt: "dg.test.now_rtt",
    // строки замера под нагрузкой (bench.channel.TESTS; круг 1 П1 — было «Load_retr», «Load_rtt»)
    load_retr: "dg.test.load_retr", load_rtt: "dg.test.load_rtt" };
  // единицы: у тестов проверки — коды (bench.suite), у звеньев «Сейчас» и резких падений — русские слова ядра (score.py);
  // на экране — свои слова панели на языке читателя; «%», «×» — как есть
  const UNIT = { "Mbit/s": "dg.u.mbps", "MB/s": "dg.u.mbs", "MiB/s": "dg.u.mibs", ms: "dg.u.ms", GB: "dg.u.gb", "ev/s": "dg.u.evs", IOPS: "dg.u.iops",
    "Мбит/с": "dg.u.mbps", "МБ/с": "dg.u.mbs", "МиБ/с": "dg.u.mibs", "мс": "dg.u.ms", "ГБ": "dg.u.gb", "событий/с": "dg.u.evs" };
  const FLAG = { drop: "dg.flag.drop", retry: "dg.flag.retry", load: "dg.flag.load", skip: "dg.flag.skip", failed: "dg.flag.failed",
    cancelled: "dg.flag.cancelled", partial: "dg.flag.partial" };
  const FLAG_TONE = { drop: "danger", retry: "", load: "warning", skip: "warning", failed: "danger", cancelled: "", partial: "warning" };
  const FILTERS = [["all", "dg.fAll"], ["quick", "dg.fQuick"], ["full", "dg.fFull"], ["issues", "dg.fIssues"]];
  const ARMED = { no_manual: "dg.armed.no_manual", other_machine: "dg.armed.other_machine", no_machine_id: "dg.armed.no_machine_id", no_pkgs: "dg.armed.no_pkgs" };
  const OFF_WHY = { time: "dg.offBad", mode: "dg.offBad", days: "dg.offBad", monthly_days: "dg.offBad", monthly: "dg.offBad", id: "dg.offBad",
    per_day: "dg.offLimit", full_per_day: "dg.offLimit", gap: "dg.offLimit", total: "dg.offLimit" };
  // «кто запустил» — слово сервера (by_text, один каталог host_sched.by.* у панели, CLI и MCP); без него — dg.by.*,
  // те же слова (tests/test_bench_by_labels.py)
  const byText = (r) => (r && r.by_text) || t(BY[r && r.by] || "dg.by.panel");
  const linkName = (id) => (LINK[id] ? t(LINK[id]) : String(id));
  const testName = (id) => (TEST[id] ? t(TEST[id]) : String(id));
  // подпись в таблице — с заглавной (в словаре — как в середине фразы: «резкое падение: отдача …», З-4)
  const capFirst = (x) => (x ? x.charAt(0).toUpperCase() + x.slice(1) : x);
  const unitText = (u) => (UNIT[u] ? t(UNIT[u]) : u === "x" ? "×" : String(u || ""));
  // число без лишних нулей: от 10 — целое, от 1 — десятые, меньше — сотые («0,05%», «1,1 мс», «742 Мбит/с»; UI-C)
  function fv(v) {
    const a = Math.abs(v), d = a >= 10 ? 0 : a >= 1 ? 1 : 2;
    try { return new Intl.NumberFormat(LOC(), { maximumFractionDigits: d }).format(v); } catch (e) { return String(Math.round(v * 100) / 100); }
  }
  const numText = (v) => fv(Number(v));
  // число с единицей: через пробел, проценты — слитно («5%», правило текстов pct)
  const withUnit = (v, u) => numText(v) + (!u ? "" : u === "%" ? "%" : " " + unitText(u));
  const valText = (v, u) => (v === null || v === undefined ? t("dg.nm") : withUnit(v, u));
  // число звена «Сейчас» (fresh_raw, usual_raw): нет — «—»
  const rawText = (v, u) => (typeof v === "number" && isFinite(v) ? withUnit(v, u) : "—");
  const relText = (rel, n, need) => {
    const r = relOf(rel, n, need);
    return !r ? "—" : r.k === "dg.rel" ? t("dg.rel", { n: K.fmtInt(r.v.n) }) : t("dg.relNone", { n: K.fmtInt(r.v.n), need: K.fmtInt(r.v.need) });
  };

  // ── часы узла (Intl по имени зоны; зоны нет — UTC+3, как запасная зона сервера) ───────────────────────────
  let zone = "Etc/GMT-3";
  const LOC = () => (K.lang === "en" ? "en-GB" : "ru-RU");
  function setZone(name) {
    zone = "Etc/GMT-3";
    if (typeof name !== "string" || !name) return;
    try { new Intl.DateTimeFormat("en", { timeZone: name }).format(0); zone = name; } catch (e) { /* неизвестная браузеру зона */ }
  }
  function parts(ts) {
    const o = {};
    try {
      new Intl.DateTimeFormat("en-GB", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", weekday: "short" })
        .formatToParts(new Date(ts * 1000)).forEach((p) => { o[p.type] = p.value; });
    } catch (e) { return null; }
    const wd = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 }[o.weekday] || 1;
    return { day: o.year + "-" + o.month + "-" + o.day, time: pad2(Number(o.hour) % 24) + ":" + o.minute, wd };
  }
  const dayOf = (ts) => { const p = parts(ts); return p ? p.day : ""; };
  const timeOf = (ts) => { const p = parts(ts); return p ? p.time : "—"; };
  const nowSec = () => Math.floor(Date.now() / 1000);
  // «17 октября» дня YYYY-MM-DD (день уже по часам узла)
  function dateWord(day) {
    try { return new Intl.DateTimeFormat(LOC(), { timeZone: "UTC", day: "numeric", month: "long" }).format(new Date(day + "T12:00:00Z")); } catch (e) { return day; }
  }
  // подпись группы дня: «Сегодня», «Вчера», дальше — «14 октября, вторник»
  function dayWord(day) {
    const today = dayOf(nowSec()), yest = dayOf(nowSec() - 86400);
    if (day === today) return t("dg.today");
    if (day === yest) return t("dg.yesterday");
    try { return dateWord(day) + ", " + new Intl.DateTimeFormat(LOC(), { timeZone: "UTC", weekday: "long" }).format(new Date(day + "T12:00:00Z")); } catch (e) { return day; }
  }
  function zoneLine(doc) {
    let off = "";
    try {
      const p = new Intl.DateTimeFormat("en", { timeZone: zone, timeZoneName: "shortOffset" }).formatToParts(new Date()).find((x) => x.type === "timeZoneName");
      off = p ? p.value.replace("GMT", "UTC") : "";
    } catch (e) { off = ""; }
    if (off === "UTC") off = "UTC+0";
    return doc && doc.tz ? t("dg.tz", { zone: doc.tz, off }) : t("dg.tzNone", { off: off || "UTC+3" });
  }
  // числа цены: МБ -> ГБ с одной цифрой, минуты процессора -> «мин» или «с»
  const gbOf = (mb) => K.fmtNum1(Math.round((Number(mb) || 0) / 100) / 10);
  const mbOf = (c) => (c ? (Number(c.rx_mb) || 0) + (Number(c.tx_mb) || 0) : 0);
  function cpuText(min) {
    const m = Number(min) || 0;
    return m < 1 ? t("dg.sec", { n: K.fmtInt(Math.round(m * 60)) }) : t("dg.min", { n: K.fmtNum1(Math.round(m * 10) / 10) });
  }
  // «≈» — по прошлым проверкам, «до» — предел (basis caps)
  const pre = (cost) => t(cost && cost.basis === "measured" ? "dg.approx" : "dg.upTo");

  // ── состояние раздела ─────────────────────────────────────────────────────────────────────────────────
  let root = null, head = null, body = null;
  let tab = "now";
  const S = {
    doc: null, docErr: null, docLoading: false,          // GET расписания (и пояс узла для истории)
    rowMsg: {},                                          // id записи -> «Не включилась: …» (переключатель)
    propNo: false,                                       // «Не нужно» на предложении (этот браузер)
    busy: false,
  };
  const H = { rows: null, more: false, err: null, loading: false, filter: "all", by: "", skips: true, day: null,
    cmp: false, picked: [], detail: null, det: null, detErr: null, cv: null, cvErr: null, seq: 0 };
  const PROP_KEY = "kaskad-diag-propose-no";
  try { S.propNo = localStorage.getItem(PROP_KEY) === "1"; } catch (e) { S.propNo = false; }

  function draw() {
    if (!body) return;
    // карточка замера (BC5) переносится в новую стопку «Сейчас» тем же узлом: фокус внутри неё (кнопка «Остановить»,
    // «Повторить») при переносе теряется — вернуть его тому же элементу
    const keep = spdNode && spdNode.contains(document.activeElement) ? document.activeElement : null;
    try {
      head.replaceChildren(...headNodes());
      if (tab === "now") body.dataset.find = "diag.now";            // расписание — без якоря: его ищут как «settings.health»
      else if (tab === "history") body.dataset.find = "diag.history";
      body.replaceChildren(tab === "schedule" ? schedView() : tab === "now" ? nowView() : histView());
      if (keep && keep.isConnected && document.activeElement !== keep) { try { keep.focus({ preventScroll: true }); } catch (e) { /* не в документе */ } }
    } catch (e) {
      try { console.error(e); } catch (x) { /* консоли нет */ }
      body.replaceChildren(U.al("danger", t("dg.renderFailed"), String((e && e.message) || "")));
    }
  }
  // под заголовком раздела (его рисует ядро): время сервера слева, вкладки справа (на телефоне — во всю ширину)
  function headNodes() {
    if (SCHED) return [el("p", { class: "gx-diag__tz", text: S.doc ? zoneLine(S.doc) : "" })];
    const tabs = K.Tpl.tabs({ items: TABS.map((id) => ({ id, label: t(id === "now" ? "nav.diagNow" : "nav.diagHistory") })), value: tab,
      onChange: (id) => K.goNav(ID, { tab: id }), aria: t("nav.diag") });
    return [el("p", { class: "gx-diag__tz", text: S.doc ? zoneLine(S.doc) : "" }), el("div", { class: "gx-diag__tabs" }, tabs)];
  }
  function loadDoc() {
    S.docLoading = true;
    return API().schedule().then((d) => { S.doc = d; S.docErr = null; setZone(d.tz); }, (e) => { S.docErr = e; })
      .then(() => { S.docLoading = false; draw(); });
  }

  // ── «Расписание» ──────────────────────────────────────────────────────────────────────────────────────
  function entrySub(e) {
    const kind = daysKind(e.days);
    const days = e.monthly ? t("dg.monthlyOn", { day: t(DAY_SHORT[(e.days || [1])[0]] || "dg.d1") })
      : kind === "list" ? (e.days || []).map((d) => t(DAY_SHORT[d] || "dg.d1")).join(", ") : t(KIND[kind]);
    return [t(MODE[e.mode] || "dg.quick"), days, e.quiet ? t("dg.quietShort") : null].filter(Boolean).join(" · ");
  }
  function nextLine(doc) {
    const n = doc && doc.next && doc.next[0];
    if (!n || typeof n.ts !== "number") return null;
    const p = parts(n.ts);
    if (!p) return null;
    const day = p.day === dayOf(nowSec()) ? t("dg.nToday") : p.day === dayOf(nowSec() + 86400) ? t("dg.nTomorrow") : t(DAY_LONG[p.wd] || "dg.dl1");
    const min = doc.limits && typeof doc.limits.jitter_min === "number" ? doc.limits.jitter_min : 15;
    return el("p", { class: "gx-sched__next", text: t("dg.next", { day, time: p.time, min: K.fmtInt(min) }) });
  }
  function armedNode(doc) {
    if (!doc || doc.armed !== false) return null;
    const why = ARMED[doc.armed_why] ? doc.armed_why : "no_manual";
    const lines = [t(ARMED[why])];
    if (why === "other_machine") lines.push(t("dg.armed.copy"));
    // «Проверить сейчас» / «Установить» — то же окно цены, что и «Проверить сервер» (6C)
    const act = why === "no_manual" || why === "other_machine" ? U.btn(t("dg.armed.runNow"), "tertiary", () => openCost(), { md: true })
      : why === "no_pkgs" ? U.btn(t("dg.armed.install"), "tertiary", () => openCost(), { md: true }) : null;   // «Установить и проверить»: программы ставит проверка (П-2)
    return U.al("warning", lines[0], lines.slice(1).concat([t(why === "no_pkgs" ? "dg.armed.howPkgs" : "dg.armed.how")]).join(" "), { btns: [act] });
  }
  function sumNode(doc) {
    const c = doc.cost;
    const on = (doc.schedule || []).some((e) => e.on);
    if (!on || !c) return el("div", { class: "gx-sched__sum", "aria-live": "polite" }, el("span", { text: t("dg.sumOff") }));
    const week = (Number(c.week && c.week.quick) || 0) + (Number(c.week && c.week.full) || 0);
    const count = week >= 1 ? tn("dg.sumWeek", Math.round(week)) : tn("dg.sumMonth", Math.max(1, Math.round((week * 30.4) / 7)));
    const per = Number(c.max_per_day) || 0;                // больше всего проверок в один день недели (повторы не входят)
    const items = [el("b", { text: count }), per > 0 ? el("span", { text: tn(c.per_day_even ? "dg.sumPerDay" : "dg.sumPerDayMax", per) }) : null,
      el("span", { text: t("dg.sumDay", { pre: pre(c), gb: gbOf(mbOf(c.day)) }) }),
      el("span", { text: t("dg.sumMon", { pre: pre(c), gb: gbOf(mbOf(c.month)) }) }),
      el("span", { text: t("dg.sumCpu", { pre: pre(c), cpu: cpuText(c.day && c.day.cpu_min) }) })];
    (c.notes || []).forEach((x) => { if (x && x.text && x.code !== "no_pkgs") items.push(el("span", { text: String(x.text).replace(/\.$/, "") })); });
    return el("div", { class: "gx-sched__sum", "aria-live": "polite" }, items.filter(Boolean));
  }
  function schedRow(e, i) {
    const tm = e.time || "—";                            // битая сохранённая запись приходит с time: null (З-2)
    const sw = U.sw("", "", !!e.on, (v) => toggleEntry(i, v), { dis: S.busy });
    sw.classList.add("gx-sched__sw");
    sw.setAttribute("aria-label", t("dg.swAria", { time: tm }));
    const open = el("button", { type: "button", class: "gx-sched__open", "aria-label": t("dg.editAria", { time: tm }) },
      el("span", { class: "gx-sched__time" + (e.on ? "" : " is-off"), text: tm }),
      el("span", { class: "gx-sched__sub", text: entrySub(e) }));
    open.addEventListener("click", () => openSheet(i));
    const row = el("div", { class: "gx-sched__row" }, el("div", { class: "gx-sched__line" }, open, sw));
    const msg = S.rowMsg[e.id || ""];
    if (msg) row.append(el("div", { class: "gx-sched__err", role: "alert", text: t("dg.rowOff", { why: msg }) }));
    else if (!e.on && e.off_why && OFF_WHY[e.off_why]) row.append(el("div", { class: "gx-sched__err", text: t(OFF_WHY[e.off_why]) }));
    return row;
  }
  function proposeNode(doc) {
    const p = doc.propose;
    if (!p || !Array.isArray(p.entries) || p.entries.length < 2 || S.propNo) return null;
    const q = p.entries[0], f = p.entries[1];
    const run = (mode) => (p.cost && p.cost.per_run ? p.cost.per_run[mode] : null);
    const per = (mode) => t("dg.propRun", { pre: pre(p.cost), gb: gbOf(mbOf(run(mode))), cpu: cpuText(run(mode) && run(mode).cpu_min) });
    const lineQ = t("dg.propQ", { day: t(DAY_SHORT[q.days[0]] || "dg.d1"), time: q.time, run: per("quick") });
    const lineF = t("dg.propF", { day: t(DAY_SHORT[f.days[0]] || "dg.d1"), time: f.time, run: per("full") });
    const box = el("div", { class: "gx-propose" },
      el("b", { text: t("dg.propT") }), el("span", { text: lineQ }), el("span", { text: lineF }),
      el("span", { class: "gx-propose__sum", text: t("dg.propSum", { pre: pre(p.cost), gb: gbOf(mbOf(p.cost && p.cost.month)) }) }),
      el("span", { text: t("dg.propQuiet") }));
    const acts = el("div", { class: "gx-propose__acts" },
      U.btn(t("dg.propAdd"), "primary", () => saveOrSay([q, f]), { md: true, icon: "plus", dis: S.busy }),
      U.btn(t("dg.propQuick"), "tertiary", () => saveOrSay([q]), { md: true, dis: S.busy }),
      U.btn(t("dg.propNo"), "ghost", () => { S.propNo = true; try { localStorage.setItem(PROP_KEY, "1"); } catch (e) { /* без памяти */ } draw(); }, { md: true }));
    return [box, acts];
  }
  function schedView() {
    const doc = S.doc;
    if (!doc) {
      if (S.docErr) return K.Tpl.state("error", { text: S.docErr.message || "", onRetry: () => loadDoc() });
      return K.Tpl.state("loading", { rows: 3 });
    }
    const list = doc.schedule || [];
    const add = U.btn("", "tertiary", () => openSheet(-1), { icon: "plus", tip: t("dg.add"), dis: S.busy });
    const titleRow = el("div", { class: "gx-sched__head" }, el("h3", { class: "gx-sched__title", text: t("dg.sTitle") }), add);
    const out = [titleRow, armedNode(doc)];
    if (doc.timer && doc.timer.applied === false && doc.armed) out.push(U.al("warning", t("dg.timerT"), t("dg.timerX")));
    if (!list.length) {
      const prop = proposeNode(doc);
      const empty = el("div", { class: "gx-card gx-sched__empty" },
        el("span", { class: "gx-sched__emptyic" }, K.icon("clock", 22)), el("div", { class: "gx-sched__emptyt", text: t("dg.emptyT") }),
        prop || U.btn(t("dg.add"), "tertiary", () => openSheet(-1), { md: true, icon: "plus" }));
      out.push(empty);
    } else {
      out.push(nextLine(doc), sumNode(doc));
      out.push(el("div", { class: "gx-card gx-sched__list" }, list.map(schedRow)));
    }
    const retry = U.sw(t("dg.retryT"), t("dg.retryS"), doc.retry !== false, (v) => saveRetry(v), { dis: S.busy });
    retry.classList.add("gx-retry");
    out.push(retry);
    return el("div", { class: "gx-sched" }, out.filter(Boolean));
  }

  // запись всего списка (PUT): If-Match — etag того ответа GET, из которого собран список (from.doc; нет — S.doc),
  // а не последнего прочитанного: список, собранный до «Обновить», не уйдёт с новым etag (REV-ns-b15 С-1).
  // from.reloaded — что сделать после «Обновить» в окне «Расписание изменили» (лист пересобирает черновик).
  // Ответ — как GET; отказы — словами и «Обновить»
  function saveList(list, o, from) {
    const doc = (from && from.doc) || S.doc;
    if (!doc || S.busy) return Promise.resolve(false);
    S.busy = true;
    draw();
    const opts = Object.assign({ tz: true }, o || {});
    return API().save(bodyOf(list, doc, opts), doc.etag).then((d) => {
      S.doc = d; S.rowMsg = {}; setZone(d.tz); S.busy = false; draw();     // список записан — «Не включилась» устарело (З-1)
      return true;
    }, (e) => {
      S.busy = false;
      draw();
      return putFailed(e, list, opts, from);
    });
  }
  function putFailed(e, list, opts, from) {
    const code = e && e.code;
    if (code === "need_confirm") {
      const c = e.body && e.body.cost;
      const heavy = c && (c.notes || []).find((x) => x && x.code === "heavy");
      return K.confirmDialog({ title: t("dg.heavyT"), text: (heavy && heavy.text ? heavy.text + " " : "") + t("dg.heavyX"), okLabel: t("dg.heavyOk") })
        .then((ok) => (ok ? saveList(list, Object.assign({}, opts, { confirm: true }), from) : false));
    }
    if (code === "stale" || code === "tz_changed" || code === "need_etag") {
      const sheet = !!(from && from.reloaded);
      const d = K.openDialog({ title: t(code === "tz_changed" ? "dg.tzChangedT" : "dg.staleT"), icon: "arrow-rotate-right", tone: "warning",
        blocks: [U.p(t(code === "tz_changed" ? "dg.tzChangedX" : !sheet ? "dg.staleX" : from.op === "delete" ? "dg.staleDelX" : "dg.staleSheetX"))] });
      d.setFoot([U.btn(t("dg.cancel"), "tertiary", () => d.close(), { md: true }),
        U.btn(t("dg.reload"), "primary", () => { d.close(); return loadDoc().then(() => { if (sheet) from.reloaded(); }); }, { md: true, icon: "arrow-rotate-right" })]);
      return Promise.resolve(false);
    }
    if (code === "invalid" && e.body && Array.isArray(e.body.errors)) return Promise.resolve({ errors: e.body.errors });
    K.toast(t("dg.saveFailed"), (e && e.message) || "", "danger");
    return Promise.resolve(false);
  }
  // запись не из листа (предложение, переключатель, повтор, удаление): отказ сервера с ошибками — тостом
  function saveOrSay(list, o, from) {
    return saveList(list, o, from).then((r) => {
      if (r && r.errors) K.toast(t("dg.saveFailed"), String((r.errors[0] && (r.errors[0].text || r.errors[0].code)) || ""), "danger");
      return r === true;
    });
  }
  function saveRetry(v) {
    if (!S.doc) return Promise.resolve(false);
    return saveOrSay(S.doc.schedule || [], { retry: v });
  }
  // переключатель записи: выключить — сразу; включить — сначала предпросмотр (ошибка записи — «Не включилась: …»)
  function toggleEntry(i, v) {
    const doc = S.doc;
    if (!doc || S.busy) return Promise.resolve(false);
    const list = (doc.schedule || []).map((e, j) => (j === i ? Object.assign({}, e, { on: v }) : e));
    const id = (doc.schedule[i] && doc.schedule[i].id) || "";
    delete S.rowMsg[id];
    if (!v) return saveOrSay(list);
    S.busy = true;
    draw();
    return API().preview(bodyOf(list, doc)).then((r) => r, (e) => e).then((r) => {
      S.busy = false;
      const pv = previewOf(r);
      const mine = sheetErrs(pv.errors, i + 1, list[i]);
      const bad = [].concat(mine.time, mine.type, mine.days, mine.rep);
      if (pv.errors.length) {
        S.rowMsg[id] = (bad.length ? bad : pv.errors.map((x) => String(x.text || x.code)))[0];
        draw();
        return false;
      }
      if (pv.failed) { draw(); K.toast(t("dg.saveFailed"), (r && r.message) || "", "danger"); return false; }
      return saveOrSay(list, null, { doc });
    });
  }

  // ── лист записи: барабан 24 ч, тип с ценой, дни, повтор, «только когда свободен»; предпросмотр живьём ──────
  let wheelSeq = 0;                                      // id вариантов барабана: уникальны на странице
  function wheel(n, value, label, onPick) {
    const box = el("div", { class: "gx-wheel__col", role: "listbox", tabindex: "0", "aria-label": label });
    const opts = [], idp = "gx-wheel-" + String(++wheelSeq) + "-";
    box.append(el("div", { class: "gx-wheel__pad" }));
    for (let i = 0; i < n; i += 1) {
      const o = el("div", { class: "gx-wheel__opt", role: "option", "aria-selected": String(i === value), id: idp + String(i), text: pad2(i) });
      o.addEventListener("click", () => set(i, true));
      opts.push(o);
      box.append(o);
    }
    box.append(el("div", { class: "gx-wheel__pad" }));
    let cur = value, timer = null;
    function mark(v) { opts.forEach((o, i) => o.setAttribute("aria-selected", String(i === v))); box.setAttribute("aria-activedescendant", idp + String(v)); }
    function set(v, scroll) {
      cur = v;
      mark(v);
      if (scroll) box.scrollTo({ top: v * ROW_PX, behavior: "smooth" });
      onPick(v);
    }
    box.addEventListener("scroll", () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { timer = null; const v = wheelPick(box.scrollTop, ROW_PX, n); if (v !== cur) set(v, false); else mark(v); }, 90);
    });
    box.addEventListener("keydown", (ev) => {
      const v = wheelStep(cur, ev.key, n);
      if (v === null) return;
      ev.preventDefault();
      set(v, true);
    });
    mark(cur);
    // to — поставить значение без onPick (лист после «Обновить» берёт время новой записи, правки человека нет)
    return { el: box, place: () => { box.scrollTop = cur * ROW_PX; }, to: (v) => { cur = v; mark(v); box.scrollTop = v * ROW_PX; } };
  }
  // paint() листа пересоздаёт кнопки блоков: фокус с клавиатуры возвращается на ту же по счёту кнопку своего блока
  // (REV-ns-b15-2 П-1: после «Пн» Пробелом фокус уходил в начало страницы)
  const focusables = (box) => Array.from(box.querySelectorAll("button, input, [tabindex]")).filter((n) => !n.disabled);
  function focusIn(boxes) {
    const a = document.activeElement;
    const box = a ? boxes.find((b) => b !== a && b.contains(a)) : null;
    if (!box) return () => {};
    const k = focusables(box).indexOf(a);
    return () => {
      if (box.contains(a)) return;                       // узел остался — фокус на месте
      const f = focusables(box), n = f[k] || f[f.length - 1];
      if (n) n.focus();
    };
  }
  function openSheet(i) {
    const doc = S.doc;
    if (!doc || S.busy) return;
    // база листа — ответ GET, из которого собран черновик: его список и его etag уходят в PUT; после «Обновить» в окне
    // «Расписание изменили» — новый ответ (rebase), правка этого листа остаётся поверх нового списка (REV-ns-b15 С-1)
    let base = doc, list = (doc.schedule || []).slice(), pos = i;
    const isNew = i < 0;
    const src = isNew ? { time: "04:00", mode: "quick", days: DAYS.slice(), on: true, quiet: true, monthly: false } : list[i];
    const tp = timeParts(src.time);
    let hh = tp[0], mm = tp[1];
    // время черновика — то, что показывает барабан (битая запись с time: null — тоже, З-2)
    const d = { id: src.id || null, time: hhmm(hh, mm), mode: src.mode, days: (src.days || []).slice(), on: isNew ? true : !!src.on, quiet: !!src.quiet, monthly: !!src.monthly };
    // поля, которые человек менял в этом листе: после «Обновить» поверх новой записи ложатся только они, прочие (и «вкл»,
    // его лист не правит) — из новой записи (REV-ns-b15-2 Н-1: правка другого окна в той же записи не теряется)
    const dirty = new Set();
    const edit = (k) => { dirty.add(k); changed(); };
    const idx = () => (isNew ? list.length : pos);       // номер записи в черновике (n ошибок = idx + 1)
    const draft = () => { const l = list.slice(); if (isNew) l.push(d); else l[pos] = d; return l; };
    const from = (op) => ({ doc: base, reloaded: rebase, op: op || "save" });
    let pv = { errors: [], cost: doc.cost, next: [], failed: false };
    let seq = 0, tmr = null, pending = false;
    const setTime = () => { d.time = hhmm(hh, mm); edit("time"); };
    const wh = wheel(24, hh, t("dg.hours"), (v) => { hh = v; setTime(); });
    const wm = wheel(60, mm, t("dg.minutes"), (v) => { mm = v; setTime(); });
    const timeBox = el("div", { class: "gx-wheel" }, el("span", { class: "gx-wheel__band", "aria-hidden": "true" }), wh.el, el("span", { class: "gx-wheel__colon", "aria-hidden": "true", text: ":" }), wm.el);
    const errTime = el("div", { class: "gx-sheet-err", role: "alert" });
    const typeBox = el("div", { class: "gx-sched__types", role: "radiogroup", "aria-label": t("dg.type") });
    typeBox.addEventListener("keydown", (ev) => {
      const k = TYPES.indexOf(d.mode), to = { ArrowRight: k + 1, ArrowDown: k + 1, ArrowLeft: k - 1, ArrowUp: k - 1 }[ev.key];
      if (to === undefined) return;
      ev.preventDefault();
      d.mode = TYPES[(to + TYPES.length) % TYPES.length];
      edit("mode");
      const b = typeBox.querySelector('[data-mode="' + d.mode + '"]');
      if (b) b.focus();
    });
    const errType = el("div", { class: "gx-sheet-err", role: "alert" });
    const daysBox = el("div", { class: "gx-days", role: "group", "aria-label": t("dg.days") });
    const errDays = el("div", { class: "gx-sheet-err", role: "alert" });
    const repBox = el("div");
    const errRep = el("div", { class: "gx-sheet-err", role: "alert" });
    const quietBox = el("div");
    const costLine = el("p", { class: "gx-sched__cost", "aria-live": "polite" });
    const errForm = el("div", { class: "gx-sheet-err", role: "alert" });
    const save = U.btn(t("dg.save"), "primary", () => doSave(), { md: true });
    const block = (label, kids) => el("div", { class: "gx-sched__blk" }, el("span", { class: "gx-sched__lbl", text: label }), kids);
    function fill(n, arr) { n.replaceChildren(...arr.map((x) => el("span", { text: x }))); n.hidden = !arr.length; }
    function paint() {
      const back = focusIn([typeBox, daysBox, repBox, quietBox]);
      const run = pv.cost && pv.cost.per_run ? pv.cost.per_run : null;
      // радиогруппа: одна остановка Tab (выбранная карточка), стрелки меняют выбор (З-6)
      typeBox.replaceChildren(...TYPES.map((m) => {
        const b = el("button", { type: "button", role: "radio", class: "gx-sched__type", "aria-checked": String(d.mode === m), tabindex: d.mode === m ? "0" : "-1", "data-mode": m },
          el("b", { text: t(MODE[m]) }), el("span", { text: run && run[m] ? t("dg.typeCost", { pre: pre(pv.cost), gb: gbOf(mbOf(run[m])), cpu: cpuText(run[m].cpu_min) }) : "" }));
        b.addEventListener("click", () => { d.mode = m; edit("mode"); });
        return b;
      }));
      daysBox.setAttribute("data-rep", d.monthly ? "month" : "week");
      daysBox.replaceChildren(...DAYS.map((x) => {
        const b = el("button", { type: "button", class: "gx-days__d", "aria-pressed": String(d.days.indexOf(x) >= 0), "aria-label": t(DAY_LONG[x]), text: t(DAY_SHORT[x]) });
        b.addEventListener("click", () => {
          if (d.monthly) d.days = [x];
          else d.days = d.days.indexOf(x) >= 0 ? d.days.filter((y) => y !== x) : d.days.concat([x]).sort((a, b2) => a - b2);
          edit("days");
        });
        return b;
      }));
      repBox.replaceChildren(U.seg([{ label: t("dg.repWeek"), on: !d.monthly, pick: () => { d.monthly = false; edit("monthly"); } },
        { label: t("dg.repMonth"), on: d.monthly, pick: () => { d.monthly = true; if (d.days.length > 1) { d.days = [d.days[0]]; dirty.add("days"); } edit("monthly"); } }], { full: true, aria: t("dg.repeat") }),
      d.monthly ? el("span", { class: "gx-sched__hint", text: t("dg.monthHint") }) : null);
      quietBox.replaceChildren(U.sw(t("dg.quiet"), t("dg.quietHint"), d.quiet, (v) => { d.quiet = v; edit("quiet"); }, { last: true }));
      const c = pv.cost;
      costLine.textContent = c ? t("dg.draftCost", { pre: pre(c), gb: gbOf(mbOf(c.month)), cpu: cpuText(c.month && c.month.cpu_min) }) : "";
      const er = sheetErrs(pv.errors, idx() + 1, d);
      fill(errTime, er.time); fill(errType, er.type); fill(errDays, er.days); fill(errRep, er.rep); fill(errForm, er.form);
      save.disabled = !er.canSave;
      save.setAttribute("aria-busy", pending ? "true" : "false");
      back();
    }
    function changed() {
      paint();
      if (tmr) clearTimeout(tmr);
      pending = true;
      tmr = setTimeout(ask, 300);                         // не чаще раза в 300 мс (§2.6)
    }
    function ask() {
      tmr = null;
      const mine = ++seq;
      API().preview(bodyOf(draft(), base)).then((r) => r, (e) => e).then((r) => {
        if (mine !== seq || !dlg.box.isConnected) return;  // устаревший ответ — мимо
        pending = false;
        const got = previewOf(r);
        if (got.failed) {                                  // старые ошибки полей — от прежнего черновика: снять (П-2)
          pv = Object.assign({}, pv, { errors: [] });
          paint();
          errForm.hidden = false;
          errForm.replaceChildren(el("span", { text: t("dg.previewFailed") }));
          return;
        }
        pv = got;
        paint();
      });
    }
    // «Обновить» после 409: новый список и etag; запись этого листа ищется по id (удалили в другом окне — лист закрыт)
    function rebase() {
      const nd = S.doc;
      if (!nd || nd === base || !dlg.box.isConnected) return;
      const nl = (nd.schedule || []).slice();
      const j = isNew ? -1 : nl.findIndex((e) => !!(e && d.id && e.id === d.id));
      if (!isNew && j < 0) { dlg.close(); K.toast(t("dg.goneT"), t("dg.goneX"), "warning"); return; }
      base = nd; list = nl;
      if (!isNew) {
        pos = j;
        const ne = nl[j];
        d.on = !!ne.on;
        if (!dirty.has("mode")) d.mode = ne.mode;
        if (!dirty.has("days")) d.days = (ne.days || []).slice();
        if (!dirty.has("monthly")) d.monthly = !!ne.monthly;
        if (!dirty.has("quiet")) d.quiet = !!ne.quiet;
        // «раз в месяц» человека поверх дней другого окна — один день, как у переключателя (REV-ns-p4a П-2)
        if (d.monthly && d.days.length > 1) d.days = [d.days[0]];
        if (!dirty.has("time")) {
          const np = timeParts(ne.time);
          hh = np[0]; mm = np[1]; d.time = hhmm(hh, mm);
          wh.to(hh); wm.to(mm);
        }
      }
      pv = { errors: [], cost: nd.cost, next: [], failed: false };
      changed();
    }
    function doSave() {
      if (tmr) { clearTimeout(tmr); tmr = null; }
      save.disabled = true;
      return saveList(draft(), null, from()).then((ok) => {
        if (ok === true) { dlg.close(); return; }
        if (ok && ok.errors) { pv = Object.assign({}, pv, { errors: ok.errors }); paint(); return; }
        paint();
      });
    }
    const del = isNew ? null : U.btn(t("dg.delete"), "dsoft", () => K.confirmDialog({ title: t("dg.deleteT", { time: d.time }), text: t("dg.deleteX"), okLabel: t("dg.delete"), danger: true })
      .then((ok) => (ok ? saveOrSay(list.filter((x, j) => j !== pos), null, from("delete")).then((r) => { if (r) dlg.close(); }) : null)), { md: true, icon: "trash-bin" });
    const dlg = K.openDialog({ size: "md", title: t(isNew ? "dg.newT" : "dg.editT"), blocks: [
      block(t("dg.time"), [timeBox, el("span", { class: "gx-sched__hint", text: t("dg.jitter", { min: K.fmtInt((doc.limits && doc.limits.jitter_min) || 15) }) }), errTime]),
      block(t("dg.type"), [typeBox, errType]),
      block(t("dg.days"), [daysBox, errDays]),
      block(t("dg.repeat"), [repBox, errRep]),
      quietBox, costLine, errForm] });
    dlg.box.classList.add("gx-sched__sheet");
    dlg.setFoot([del, U.btn(t("dg.cancel"), "tertiary", () => dlg.close(), { md: true }), save]);
    paint();
    requestAnimationFrame(() => { wh.place(); wm.place(); });
    changed();
  }

  // ── «История» ─────────────────────────────────────────────────────────────────────────────────────────
  function histQuery(more) {
    const q = {};
    if (H.day) { q.from = H.day; q.to = H.day; }
    if (H.filter === "quick" || H.filter === "full") q.mode = H.filter;
    if (H.by) q.by = H.by;
    if (more && H.rows && H.rows.length) q.before = H.rows[H.rows.length - 1].ts;
    return q;
  }
  function loadRuns(more) {
    const mine = ++H.seq;
    H.loading = true;
    if (!more) { H.rows = null; H.err = null; }
    draw();
    return API().runs(histQuery(more)).then((d) => {
      if (mine !== H.seq) return;
      H.rows = (more ? H.rows || [] : []).concat(Array.isArray(d.runs) ? d.runs : []);
      H.more = !!d.more; H.err = null;
    }, (e) => { if (mine === H.seq) H.err = e; }).then(() => { if (mine === H.seq) { H.loading = false; draw(); } });
  }
  function flagChips(r) {
    return runFlags(r).map((f) => {
      let text = t(FLAG[f]);
      if (f === "drop") { const x = r.drops[0]; text = t("dg.flag.dropOf", { link: linkName(x.link), value: x.value === null || x.value === undefined ? "" : valText(x.value, x.unit) }).replace(/\s+$/, ""); }
      return U.chip(text, FLAG_TONE[f] || "", { nodot: !FLAG_TONE[f] });
    });
  }
  const trafficOf = (r) => (typeof r.rx === "number" || typeof r.tx === "number" ? K.fmtBytes((Number(r.rx) || 0) + (Number(r.tx) || 0)) : "");
  const durOf = (r) => (typeof r.dur_s === "number" ? K.fmtDur(r.dur_s) : "");
  function histRow(r) {
    const picked = H.picked.indexOf(r.id);
    const sub = [t(MODE[r.mode] || "dg.quick"), byText(r)].join(" · ");
    const meta = r.status === "skipped" ? "" : [durOf(r), trafficOf(r)].filter(Boolean).join(" · ");   // пропуск: ждал, не мерил
    const b = el("button", { type: "button", class: "gx-hrow" + (H.detail === r.id ? " is-cur" : ""), "aria-pressed": H.cmp ? String(picked >= 0) : null },
      H.cmp ? el("span", { class: "gx-hrow__ck" + (picked >= 0 ? " is-on" : ""), "aria-hidden": "true" }, picked >= 0 ? K.icon("check", 14) : null) : null,
      el("span", { class: "gx-hrow__mid" },
        el("span", { class: "gx-hrow__l1" }, el("b", { class: "gx-hrow__time", text: timeOf(r.ts) }), el("span", { class: "gx-hrow__sub", text: sub })),
        meta ? el("span", { class: "gx-hrow__meta", text: meta }) : null,
        r.why_text && r.status !== "ok" ? el("span", { class: "gx-hrow__why", text: r.why_text }) : null,
        runFlags(r).length ? el("span", { class: "gx-hrow__flags" }, flagChips(r)) : null),
      // «от обычного» проверки (rel сервера: её значения к «обычно» на её время, NS-D33); нет — ничего
      typeof r.rel === "number" && isFinite(r.rel) ? el("span", { class: "gx-hrow__rel" }, el("b", { text: K.fmtInt(r.rel) }), el("span", { text: t("dg.relOf") })) : null,
      el("span", { class: "gx-hrow__chev", "aria-hidden": "true" }, K.icon("chevron-right", 16)));
    b.addEventListener("click", () => (H.cmp ? pick(r.id) : openRun(r.id)));
    return b;
  }
  function pick(id) {
    const i = H.picked.indexOf(id);
    if (i >= 0) H.picked.splice(i, 1);
    else if (H.picked.length < 2) H.picked.push(id);
    if (H.picked.length === 2) return openCompare(H.picked[0], H.picked[1]);
    draw();
    return null;
  }
  // деталь на узком экране встаёт вместо списка: показать её начало, если список был прокручен
  function showSide() {
    const n = body && body.querySelector(".gx-hist2__det");
    if (n && n.getBoundingClientRect().top < 0) n.scrollIntoView({ block: "start" });
  }
  function openRun(id) {
    H.detail = id; H.det = null; H.detErr = null; H.cv = null;
    draw();
    showSide();
    return API().run(id).then((d) => { if (H.detail === id) H.det = d; }, (e) => { if (H.detail === id) H.detErr = e; }).then(() => draw());
  }
  // старшая проверка — в a: delta и delta_pct сервера (b − a, в % от a) тогда и есть «новее минус раньше» в % от прежней
  function openCompare(a, b) {
    const tsOf = (id) => { const r = (H.rows || []).find((x) => x.id === id); return r ? r.ts : null; };
    if (tsOf(a) !== null && tsOf(b) !== null && tsOf(a) > tsOf(b)) { const x = a; a = b; b = x; }
    H.cv = { loading: true }; H.detail = null;
    draw();
    showSide();
    return API().compare(a, b).then((d) => { H.cv = d; }, (e) => { H.cv = { err: e }; }).then(() => { H.cmp = false; H.picked = []; draw(); });
  }
  function exportFile(format) {
    const q = { format };
    if (H.day) { q.from = H.day; q.to = H.day; }
    return API().exportFile(q).then((f) => {
      const url = URL.createObjectURL(new Blob([f.text], { type: f.type || "text/plain;charset=utf-8" }));
      const a = el("a", { href: url, download: f.name || ("kaskad-bench." + format), class: "gx-vh" });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }, (e) => K.toast(t("dg.exportFailed"), (e && e.message) || "", "danger"));
  }
  function listCard() {
    const filters = U.seg(FILTERS.map((f) => ({ label: t(f[1]), on: H.filter === f[0], pick: () => { const was = H.filter; H.filter = f[0]; if ((was === "quick" || was === "full" || f[0] === "quick" || f[0] === "full") && was !== f[0]) loadRuns(false); else draw(); } })),
      { full: true, aria: t("dg.filterAria") });
    const byOpts = [{ v: "", l: t("dg.byAll") }].concat(Object.keys(BY).map((k) => ({ v: k, l: t(BY[k]) })));
    const by = U.select(byOpts, H.by, (v) => { H.by = v; loadRuns(false); }, { aria: t("dg.byAria") });
    const skips = U.sw(t("dg.skips"), "", H.skips, (v) => { H.skips = v; draw(); }, { last: true });
    const acts = el("div", { class: "gx-hist2__acts" },
      U.btn(t(H.cmp ? "dg.cmpCancel" : "dg.compare"), "tertiary", () => { H.cmp = !H.cmp; H.picked = []; draw(); }, { icon: "arrow-right-arrow-left" }),
      U.menu([{ label: t("dg.toCsv"), on: () => exportFile("csv") }, { label: t("dg.toJson"), on: () => exportFile("json") }], { label: t("dg.export"), tip: t("dg.export") }));
    const top = [el("div", { class: "gx-hist2__head" }, el("h3", { class: "gx-sched__title", text: t("dg.hTitle") }), acts), filters,
      el("div", { class: "gx-hist2__more" }, U.field(t("dg.byLabel"), by), skips)];
    if (H.day) top.push(el("div", { class: "gx-hist2__day" }, U.chip(t("dg.dayOnly", { day: dayWord(H.day) }), "accent"),
      U.btn(t("dg.dayAll"), "ghost", () => K.goNav(ID, { tab: "history" }))));          // день уходит из адреса — nav перечитает
    if (H.cmp) top.push(el("p", { class: "gx-hist2__pick", text: t("dg.cmpPick", { n: K.fmtInt(H.picked.length) }) }));
    let list;
    if (H.err) list = K.Tpl.state("error", { text: H.err.message || "", onRetry: () => loadRuns(false) });
    else if (!H.rows) list = K.Tpl.state("loading", { rows: 4 });
    else {
      const rows = runFilter(H.rows, H.filter, H.skips);
      const groups = [];
      rows.forEach((r) => { const d = dayOf(r.ts); const g = groups[groups.length - 1]; if (g && g.day === d) g.rows.push(r); else groups.push({ day: d, rows: [r] }); });
      list = groups.length ? el("div", { class: "gx-hist2__groups" }, groups.map((g) => el("div", { class: "gx-hist2__g" },
        el("span", { class: "gx-hist2__gl", text: dayWord(g.day) }), el("div", { class: "gx-card gx-hist2__rows" }, g.rows.map(histRow)))))
        : U.empty("clock", t(H.rows.length ? "dg.noRows" : "dg.noRuns"), H.rows.length ? "" : t("dg.noRunsX"));
      if (H.more) list = el("div", { class: "gx-hist2__groups" }, list, U.btn(t("dg.moreRuns"), "tertiary", () => loadRuns(true), { md: true, dis: H.loading }));
    }
    return el("div", { class: "gx-hist2__list" }, top.concat([list]));
  }
  function detailCard() {
    const back = U.btn(t("dg.back"), "ghost", () => { H.detail = null; H.det = null; draw(); }, { icon: "chevron-left", cls: "gx-hist2__back" });
    if (H.detErr) return el("article", { class: "gx-card gx-hist2__det" }, back, K.Tpl.state("error", { text: H.detErr.message || "", onRetry: () => openRun(H.detail) }));
    const r = H.det;
    if (!r) return el("article", { class: "gx-card gx-hist2__det" }, back, K.Tpl.state("loading", { rows: 3 }));
    const p = parts(r.ts);
    const title = t("dg.dTitle", { mode: t(MODE[r.mode] || "dg.quick"), day: p ? dateWord(p.day) : "", time: p ? p.time : "" });
    const sub = [byText(r), durOf(r), trafficOf(r)].filter(Boolean).join(" · ");
    const cmpWith = U.btn(t("dg.cmpWith"), "ghost", () => { H.cmp = true; H.picked = [r.id]; H.detail = null; H.det = null; draw(); }, { icon: "arrow-right-arrow-left" });
    const top = el("div", { class: "gx-hist2__dhead" }, el("div", { class: "gx-hist2__dtt" }, el("h3", { class: "gx-sched__title", text: title }),
      el("span", { class: "gx-hist2__dsub", text: sub }), runFlags(r).length ? el("span", { class: "gx-hrow__flags" }, flagChips(r)) : null), cmpWith);
    const out = [back, top];
    if (r.status === "skipped" || r.status === "failed" || r.status === "cancelled") {
      out.push(U.p(r.why_text || t(r.status === "skipped" ? "dg.skipped" : "dg.notDone")));
    }
    // «от обычного» проверки и её тестов (rel сервера: к «обычно» на её время, NS-D33; UI-C) — без балла (NS-D34)
    const rel = r.rel && typeof r.rel === "object" ? r.rel : null;
    const rt = rel && typeof rel.rel_total === "number" && isFinite(rel.rel_total) ? rel.rel_total : null;
    if (rt !== null) out.push(el("div", { class: "gx-rel gx-rel--sm" }, el("div", { class: "gx-rel__v" }, el("span", { class: "gx-rel__n", text: K.fmtInt(rt) }),
      el("span", { class: "gx-rel__u", text: t("dg.relOf") })), el("div", { class: "gx-rel__b" }, el("span", { class: "gx-rel__s", text: t("dg.relNote") }))));
    const per = rel && rel.tests && typeof rel.tests === "object" ? rel.tests : {};
    const rows = (r.tests || []).map((x) => {
      const bad = x.ok === 0 || x.ok === false || x.value === null || x.value === undefined;
      const { usual: qu, rel: qr } = per[x.test] || {};
      const us = typeof qu === "number" && isFinite(qu) ? valText(qu, x.unit) : "—";
      // Н-1 (3.3.7.13): «под нагрузкой» по короткому окну — отдача быстро дошла до предела трафика; в подсказке
      const win = !bad && typeof x.win === "number" && isFinite(x.win) && x.win > 0
        ? t("dg.loadShort", { s: K.fmtNum1(Math.round(x.win * 10) / 10) }) : "";
      const val = bad ? nmText(x) : valText(x.value, x.unit);
      return { dim: bad, cells: [capFirst(testName(x.test)), win ? { t: val, tip: win } : val, us,
        { t: !bad && typeof qr === "number" && isFinite(qr) ? K.fmtInt(qr) : "—", al: "right" }] };
    });
    if (rows.length) out.push(U.table([t("dg.lnLink"), t("dg.value"), t("dg.lnUsual"), { t: t("dg.lnRel"), al: "right" }], rows,
      { cols: "minmax(0, 1.3fr) minmax(0, 1.1fr) minmax(0, 1.1fr) max-content" }));
    out.push(U.kv([{ k: t("dg.dur"), v: durOf(r) || "—" }, { k: t("dg.traffic"), v: trafficOf(r) || "—" }, { k: t("dg.started"), v: byText(r) }]));
    return el("article", { class: "gx-card gx-hist2__det" }, out);
  }
  // разница строки сравнения «новее минус раньше» (макет r1): «▲ 32 Мбит/с · 4%» — стрелка по направлению теста сервера
  // (dir: higher — больше лучше, lower — меньше лучше; нет — без стрелки), число — в единицах теста, % — от прежней;
  // меньше 2% — «почти без разницы», ноль — «без разницы». Числа нет — «—»
  function deltaText(d, old, unit, dir) {
    if (signOf(d) === null) return "—";
    if (d === 0) return t("dg.cmpSame");
    const pct = typeof old === "number" && isFinite(old) && old !== 0 ? Math.abs((d / old) * 100) : null;
    if (pct !== null && pct < 2) return t("dg.cmpAlmost");
    const ar = dir === "higher" ? (d > 0 ? "▲ " : "▼ ") : dir === "lower" ? (d < 0 ? "▲ " : "▼ ") : signOf(d);
    return ar + withUnit(Math.abs(d), unit) + (pct === null ? "" : " · " + K.fmtInt(Math.round(pct)) + "%");
  }
  // первая строка сравнения — «от обычного» каждой проверки (rel_a / rel_b сервера: каждая — к «обычно» на её время) и
  // какая лучше (better сервера, только когда числа есть у обеих: ▲ — новее лучше, ▼ — хуже, = — так же)
  function cmpRel(cv, aIsNew) {
    const rn = aIsNew ? cv.rel_a : cv.rel_b, ro = aIsNew ? cv.rel_b : cv.rel_a;
    const word = (v) => (typeof v === "number" && isFinite(v) ? t("dg.rel", { n: K.fmtInt(v) }) : "—");
    const nb = cv.better === "same" ? "same" : cv.better === "a" || cv.better === "b" ? ((cv.better === "a") === aIsNew ? "new" : "old") : null;
    const ok = nb && typeof rn === "number" && typeof ro === "number";
    const d = !ok ? "—" : nb === "same" ? "=" : (nb === "new" ? "▲ " : "▼ ") + K.fmtInt(Math.abs(rn - ro));
    return { cells: [{ t: t("dg.cmpRel"), b: true }, { t: word(rn), b: true }, word(ro), { t: d, al: "right", b: true }] };
  }
  function compareCard() {
    const close = U.btn(t("dg.cmpClose"), "ghost", () => { H.cv = null; draw(); }, { icon: "xmark" });
    const cv = H.cv;
    if (cv.loading) return el("article", { class: "gx-card gx-hist2__det" }, close, K.Tpl.state("loading", { rows: 3 }));
    if (cv.err) return el("article", { class: "gx-card gx-hist2__det" }, close, K.Tpl.state("error", { text: cv.err.message || "" }));
    // новее — первой колонкой; одно время — новее b (запрос уходит старшей в a, openCompare)
    const newer = cv.b.ts >= cv.a.ts ? cv.b : cv.a, older = newer === cv.a ? cv.b : cv.a;
    const lbl = (r) => { const p = parts(r.ts); return t(MODE[r.mode] || "dg.quick") + " · " + (p ? dateWord(p.day) + ", " + p.time : ""); };
    const out = [el("div", { class: "gx-hist2__dhead" }, el("h3", { class: "gx-sched__title", text: t("dg.cmpT") }), close),
      el("div", { class: "gx-hist2__legend" }, el("span", { class: "gx-hist2__lg is-new" }, el("b", { text: t("dg.cmpNew") + ": " + lbl(newer) })),
        el("span", { class: "gx-hist2__lg is-old" }, el("span", { text: t("dg.cmpOld") + ": " + lbl(older) })))];
    if (cv.mode_differs) out.push(U.al("info", t("dg.cmpModes"), ""));
    const aIsNew = newer === cv.a;
    const rows = [cmpRel(cv, aIsNew)].concat((cv.rows || []).map((x) => {
      const nv = aIsNew ? x.a : x.b, ov = aIsNew ? x.b : x.a;
      // a новее (не через openCompare) — знак наоборот; процент — от прежней (своим счётом, не delta_pct сервера)
      const d = typeof x.delta === "number" ? (aIsNew ? -x.delta : x.delta) : null;
      return { cells: [capFirst(testName(x.test)), valText(nv, x.unit), valText(ov, x.unit), { t: deltaText(d, ov, x.unit, x.dir), al: "right" }] };
    }));
    out.push(U.table([t("dg.test"), t("dg.cmpNew"), t("dg.cmpOld"), { t: t("dg.delta"), al: "right" }], rows, { cols: "minmax(0, 1.1fr) minmax(0, 1fr) minmax(0, 1fr) max-content" }));
    return el("article", { class: "gx-card gx-hist2__det" }, out);
  }
  function histView() {
    if (!S.doc && !S.docErr) return K.Tpl.state("loading", { rows: 4 });       // пояс узла — из GET расписания
    const side = H.cv ? compareCard() : H.detail ? detailCard() : null;
    return el("div", { class: "gx-hist2" + (side ? " has-side" : "") }, listCard(), side);
  }


  // ── «Сейчас» (UI-C; макет Claude Design «Проверка сервера r1» 23.10, NS-D30–NS-D35): сервер только относительно своей
  // истории — «в целом N от обычного» (summary.rel_total), таблица звеньев «сейчас · обычно · от обычного · отметка ·
  // 30 дней · замеров», полоса 30 дней с подсказкой дня. Числа и правила — сервера (GET /api/host/score: links, series,
  // days[].m / checks); экран их только показывает. Балла 0–1000, поясов и вердиктов нет (NS-D34)
  const PART = { night: "dg.part.night", morning: "dg.part.morning", day: "dg.part.day", evening: "dg.part.evening", mixed: "dg.part.mixed" };
  const BASE = { cpu: "dg.base.cpu", mem: "dg.base.mem", tariff: "dg.base.tariff", engines: "dg.base.engines", openssl: "dg.base.openssl" };
  const DCOL = { green: "dg.c.ok", yellow: "dg.c.warn", red: "dg.c.fail", grey: "dg.c.none" };
  const DCLS = { green: "", yellow: " gx-day--warn", red: " gx-day--fail", grey: " gx-day--none" };
  const DICO = { green: "is-ok", yellow: "is-warn", red: "is-fail", grey: "is-none" };
  const POP_MS = 220;                                            // уход курсора со дня -> закрыть подсказку (HANDOFF)
  const N = { doc: null, err: null, lastOn: false, last: null, lastErr: null, seq: 0 };
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const num = (v) => (isNum(v) ? v : null);
  const phone = () => !!(K.isPhone && K.isPhone());

  function loadScore() {
    const api = K.API.host;
    if (!api || !api.score) return Promise.resolve();
    return api.score().then((d) => { N.doc = d || {}; N.err = null; }, (e) => { N.err = e; }).then(() => { if (tab === "now") draw(); });
  }
  // сколько нужно для сравнения (summary.rel_need сервера; нет — 3, NS-D30)
  const needOf = (sm) => (isNum(sm && sm.rel_need) && sm.rel_need > 0 ? sm.rel_need : 3);
  // «давно не проверялся · последняя проверка 8 октября» — день summary.last_at (последняя годная проверка): stale значит,
  // что проверки нет 45 дней, в полосе 30 дней её уже нет (REV-ns-p4b З-1)
  const lastTestDay = (sm) => (isNum(sm.last_at) && sm.last_at > 0 ? dayOf(sm.last_at) || null : null);

  // строки таблицы — звенья макета (r1: скачивание, отдача, отклик, дрожание, потери, шифрование, процессор); в ответе
  // сервера звено — тест или группа тестов: строка берёт числа звена (link) или одного теста группы (test). Диска среди
  // звеньев сервера нет — строки нет
  const CROWS = [{ id: "down", link: "down" }, { id: "up", link: "up" }, { id: "rtt", link: "net_q", test: "rtt" },
    { id: "jit", link: "net_q", test: "jitter" }, { id: "loss", link: "loss" }, { id: "enc", link: "crypto" }, { id: "cpu", link: "cpu", test: "cpu_mt" }];
  const CNAME = { down: "dg.ck.down", up: "dg.ck.up", rtt: "dg.ck.rtt", jit: "dg.ck.jit", loss: "dg.ck.loss", enc: "dg.ck.enc", cpu: "dg.ck.cpu" };
  // «меньше — лучше» и пол отношения (score.SPEC rel_floor, NS-D30) — только для высоты линии: 0 % потерь не уходит
  // в бесконечность
  const LOWER = { rtt: 1, jitter: 0.5, loss: 0.1 };
  // строка звена из ответа: числа, ▲/▼ и «ждёт» — свои (у теста группы — только если отметка этого теста)
  function rowOf(doc, cr) {
    const l = (doc.links || []).find((x) => x && x.id === cr.link);
    if (!l) return null;
    const p = cr.test ? (Array.isArray(l.parts) ? l.parts.find((x) => x && x.test === cr.test) : null) : null;
    if (cr.test && !p) return null;
    const src = p || l;
    const mine = !cr.test || l.dev_test === cr.test || !!(l.hint && l.hint.test === cr.test);
    return { id: cr.id, link: l.id, test: cr.test || l.test || null, source: l.source, cur: num(src.fresh_raw), base: num(src.usual_raw),
      unit: src.unit || l.unit || "", rel: num(src.rel), n: Math.max(0, Math.floor(Number(src.usual_n) || 0)),
      lo: cr.test ? null : num(l.p25_raw), hi: cr.test ? null : num(l.p75_raw),
      dev: mine ? l.dev || null : null, pend: mine ? l.dev_pending || null : null, kind: mine ? l.dev_pending_kind || null : null, hint: mine ? l.hint || null : null,
      // нет свежего значения — почему (круг 1 №8; hostscore.why_missing): код и текст сервера
      why: typeof src.why === "string" && WHY[src.why] ? src.why : null, whyText: typeof src.why_text === "string" ? src.why_text : "",
      // young — первые часы нового узла: часы окна проб из provisional сервера (D-141, его правило need_h), не из текста
      young: src.why === "young" ? youngOf(doc) : null };
  }
  function youngOf(doc) {
    const p = doc && doc.provisional && typeof doc.provisional === "object" ? doc.provisional : null;
    return p && num(p.have_h) !== null && num(p.need_h) !== null ? { have: p.have_h, need: p.need_h } : null;
  }
  // «Сейчас» без значения: короткая причина в ячейке вместо «—», полный текст сервера — подсказкой строки
  const WHY = { probes: "dg.why.probes", never: "dg.why.never", not_run: "dg.why.never", failed: "dg.why.failed", load: "dg.why.load", stale: "dg.why.stale",
    // Л3 №8: причина раннера (hostscore.RUN_WHY) — точнее «ошибки замера»; настройки точки отдачи из России в панели нет
    no_upload_ru: "dg.why.inRu", excluded: "dg.why.off", no_target: "dg.why.nopt", no_iperf3: "dg.why.nopt", upload_off: "dg.why.upOff",
    // Т-3312 «Мелочь 2»: пробы идут, окно ещё набирается — «ещё измеряется: N ч из 12», как «Почему итог не выше»
    young: "dg.why.young" };
  // тест проверки без значения в «Истории»: не измеряли по плану (выключили при запуске, не входит в проверку, в России не
  // меряется) — причина словами (владелец 29.10, D-144); сбой замера — «не измерено», как было
  const PLANNED = ["upload_off", "excluded", "no_upload_ru"];
  const nmText = (x) => (x && PLANNED.indexOf(x.why) >= 0 ? t(WHY[x.why]) : t("dg.nm"));
  const whyShort = (r) => (r.cur === null && r.why
    ? (r.young ? t("dg.why.youngH", { have: String(r.young.have), need: String(r.young.need) }) : t(WHY[r.why])) : "");
  const rowTitle = (r, m, sm, need) => (r.cur === null && r.whyText ? r.whyText + ". " : "") + markText(r, m, sm, need);
  const rowsOf = (doc) => CROWS.map((cr) => rowOf(doc, cr)).filter(Boolean);
  // отметка строки (HANDOFF r1): two — подтверждённая ▲/▼, pend — ждёт подтверждения, drop — резкое падение (ждёт
  // повтора), conf — падение подтвердилось, few — мало истории, nm — не измерено; цвет — только у two
  function markOf(r, need) {
    if (r.dev === "down" || r.dev === "up") return { st: "two", dir: r.dev };
    if (r.kind === "retry_wait" || r.kind === "drop") return { st: "drop", wait: r.kind === "retry_wait" };
    if (r.kind === "retry_confirmed") return { st: "conf" };
    if (r.kind === "day" && (r.pend === "down" || r.pend === "up")) return { st: "pend", dir: r.pend };
    if (r.rel === null && r.n < need) return { st: "few" };
    if (r.cur === null) return { st: "nm" };
    return null;
  }
  const MK = { dn: "dg.mk.dn", up: "dg.mk.up", pend: "dg.mk.pend", drop: "dg.mk.drop", conf: "dg.mk.conf", few: "dg.mk.few", nm: "dg.mk.nm" };
  const mkKey = (m) => (m.st === "two" ? (m.dir === "up" ? MK.up : MK.dn) : MK[m.st]);
  const mkCls = (m) => "gx-mk" + (!m ? "" : m.st === "two" ? " gx-mk--" + (m.dir === "up" ? "up" : "dn") : m.st === "few" || m.st === "nm" ? "" : " gx-mk--wait");
  const vu = (v, u) => (isNum(v) ? fv(v) + (!u ? "" : u === "%" ? "%" : " " + unitText(u)) : null);
  // полный текст отметки — подсказка строки и лист телефона; чисел у сервера нет — короткая подпись с заглавной
  function markText(r, m, sm, need) {
    if (!m) return t("dg.mt.none");
    const { now: hNow, usual: hUsual, unit: hUnit, days: hDays } = r.hint || {};
    const u = hUnit || r.unit, tests = r.source === "tests";
    const now = vu(num(hNow), u);
    const short = capFirst(t(mkKey(m)));
    if (m.st === "few") return capFirst(t("dg.relNone", { n: K.fmtInt(r.n), need: K.fmtInt(need) }));
    if (m.st === "nm") return t("dg.mt.nm");
    if (m.st === "drop") {
      const at = m.wait && sm.retry && sm.retry.at ? sm.retry.at : "";
      return now ? t(at ? "dg.mt.drop" : "dg.mt.dropBare", { now, at }) : at ? short + ". " + t("dg.retryAt", { at }) : short;
    }
    if (m.st === "conf") return now ? t("dg.mt.conf", { now }) : t("dg.mt.confBare");
    const up = m.dir === "up";
    if (m.st === "pend") return now ? t(tests ? "dg.mt.pendT" : "dg.mt.pendP", { w: t(up ? "dg.mt.better" : "dg.mt.worse"), a: up ? "▲" : "▼", now }) : short;
    const two = Array.isArray(hDays) ? hDays.filter((d) => Array.isArray(d) && isNum(d[1])).slice(-2) : [];
    if (two.length < 2) return short;
    return t(tests ? "dg.mt.twoT" : "dg.mt.twoP", { a: fv(two[0][1]), b: vu(two[1][1], u), mk: up ? "▲" : "▼", w: t(up ? "dg.mt.betterL" : "dg.mt.worseL"),
      usual: vu(num(hUsual), u) || vu(r.base, r.unit) || "—" });
  }
  // строка отметок под итогом (HANDOFF marksLine): по видам, звенья через запятую
  const ML = { two: "dg.ml.two", pend: "dg.ml.pend", drop: "dg.ml.drop", conf: "dg.ml.conf", nm: "dg.ml.nm", few: "dg.ml.few" };
  function marksLine(rows, ms) {
    const g = { two: [], pend: [], drop: [], conf: [], nm: [], few: [] };
    rows.forEach((r, i) => { const m = ms[i]; if (m) g[m.st].push(t(CNAME[r.id]).toLowerCase() + (m.st === "two" ? " " + (m.dir === "up" ? "▲" : "▼") : "")); });
    if (rows.length && g.few.length === rows.length) return t("dg.ml.allFew");
    const out = Object.keys(ML).filter((k) => g[k].length).map((k) => t(ML[k], { list: g[k].join(", ") }));
    return out.length ? out.join(" · ") : t("dg.ml.ok");
  }

  // ── линия звена за 30 дней (HANDOFF r1 «Как рисовать линию звена»; ячейка — KaskadSpark, своя отрисовка paint) ──
  // X — день полосы (проверка — по её времени, проба — середина суток); Y — отношение к «обычно», выше — лучше у всех:
  // r = v / обычно (у отклика, дрожания, потерь — наоборот, с полом), u = ln r, g = u / (|u| + 0,25), y = H/2 − g·(H/2 − 1,5).
  // «Обычно» ещё нет — середина высоты по медиане точек. Нет данных (день серый) — разрыв и пунктир по середине; тест
  // давно не запускался — пунктир от последней точки до края; коридор — обычный разброс сервера (p25…p75)
  function linePts(r, series, days) {
    const idx = {};
    days.forEach((d, i) => { idx[d.day] = i; });
    const pts = [];
    (Array.isArray(series) ? series : []).forEach((s) => {
      if (!Array.isArray(s) || !isNum(s[1])) return;
      let x = null;
      if (typeof s[0] === "string" && idx[s[0]] !== undefined) x = idx[s[0]] + 0.5;
      else if (isNum(s[0])) {
        const p = parts(s[0]);
        if (p && idx[p.day] !== undefined) x = idx[p.day] + (Number(p.time.slice(0, 2)) + Number(p.time.slice(3)) / 60) / 24;
      }
      if (x !== null) pts.push([x, s[1]]);
    });
    pts.sort((a, b) => a[0] - b[0]);
    const vs = pts.map((p) => p[1]).sort((a, b) => a - b);
    const ref = r.base !== null && r.base > 0 ? r.base : vs.length ? vs[Math.floor(vs.length / 2)] : null;
    return { pts, ref, gap: days.map((d) => d.color === "grey"), n: days.length || 30 };
  }
  function linePaint(r, m, L) {
    const r2 = (v) => Math.round(v * 100) / 100;
    const fl = LOWER[r.test];
    return (v, W, H, svg) => {
      const mid = H / 2, amp = H / 2 - 1.5, sx = W / L.n;
      const y = (x) => {
        if (!L.ref || !isNum(x)) return mid;
        const q = fl !== undefined ? Math.max(L.ref, fl) / Math.max(x, fl) : x / L.ref;
        const u = Math.log(Math.max(q, 1e-3)), g = u / (Math.abs(u) + 0.25);
        return r2(mid - g * amp);
      };
      if (r.lo !== null && r.hi !== null && L.ref) {
        const a = y(r.lo), b = y(r.hi);
        v.appendChild(svg("rect", { class: "gx-cl__band", x: 0, y: Math.min(a, b), width: W, height: Math.max(1.5, Math.abs(b - a)) }));
      }
      let nd = "", from = -1;
      for (let i = 0; i <= L.n; i++) {
        if (i < L.n && L.gap[i]) { if (from < 0) from = i; continue; }
        if (from >= 0) { nd += "M" + r2(from * sx + 1.5) + " " + r2(mid) + "H" + r2(Math.max(from * sx + 1.5, i * sx - 1.5)); from = -1; }
      }
      let d = "", seg = [], dots = "", prev = null;
      const flush = () => { if (seg.length) { d += K0S.mono(seg); seg = []; } };
      const rad = r.source === "tests" ? 1.4 : 1;
      L.pts.forEach((p) => {
        const x = r2(p[0] * sx), yy = y(p[1]);
        if (prev !== null && L.gap.slice(Math.floor(prev) + 1, Math.floor(p[0])).some(Boolean)) flush();
        seg.push([x, yy]);
        dots += "M" + r2(x - rad) + " " + yy + "a" + rad + " " + rad + " 0 1 0 " + 2 * rad + " 0a" + rad + " " + rad + " 0 1 0 " + -2 * rad + " 0";
        prev = p[0];
      });
      const last = L.pts.length ? L.pts[L.pts.length - 1] : null;
      const cy = r.cur !== null ? y(r.cur) : last ? y(last[1]) : mid;
      if (last && r.source === "tests" && last[0] < L.n - 4) { flush(); nd += "M" + r2(last[0] * sx + 2) + " " + y(last[1]) + "H" + r2(W); }
      else if (last) seg.push([W, cy]);
      flush();
      v.appendChild(svg("path", { class: "gx-cl__nd", d: nd || "M0 0" }));
      v.appendChild(svg("path", { class: "gx-cl__l", d: d || "M0 0" }));
      v.appendChild(svg("path", { class: "gx-cl__pt", d: dots || "M0 0" }));
      if (last || r.cur !== null) v.appendChild(svg("circle", { class: "gx-cl__dot" + (m && m.st === "two" ? " gx-cl__dot--" + (m.dir === "up" ? "up" : "dn") : ""), cx: W, cy, r: 2.2 }));
    };
  }
  const K0S = window.KaskadSpark || null;
  function lineCell(r, m, doc, days, ph, few) {
    // подпись линии: число и «обычно»; у звена без значения — причина (Л3 №8), «обычно» — только если есть; истории
    // мало (Д2 r3 F) — «появится, когда наберётся история» (номер дня не обещаем: REV-it1-b С-4)
    const us = vu(r.base, r.unit);
    const label = t(CNAME[r.id]) + ": " + (vu(r.cur, r.unit) || whyShort(r) || t("dg.mk.nm")) + (few ? ", " + t("dg.fewHist")
      : us || r.cur !== null ? ", " + t("dg.usually", { v: us || "—" }) : "");
    if (!K0S) return el("span", { class: "gx-cl" + (ph ? " gx-cl--ph" : ""), role: "img", "aria-label": label });
    return K0S.make({ paint: linePaint(r, m, linePts(r, (doc.series || {})[r.test], days)), label }, "gx-cl" + (ph ? " gx-cl--ph" : ""));
  }

  // ── шапка: «в целом», когда проверено, повтор, следующая, отметки; справа — «Проверить сейчас», «Поделиться» ──
  function runOf(id) { return id ? (H.rows || []).find((x) => x && x.id === id) || (N.last && N.last.id === id ? N.last : null) : null; }
  function nextText(sm) {
    const doc = S.doc;
    if (!doc || !Array.isArray(doc.schedule)) return "";
    if (!doc.schedule.some((e) => e && e.on)) return t("dg.schedOff");
    const nx = (Array.isArray(doc.next) ? doc.next : []).find((x) => x && isNum(x.ts));
    if (!nx || (sm.retry && sm.retry.at)) return "";
    const day = dayOf(nx.ts), today = dayOf(nowSec()), tom = dayOf(nowSec() + 86400);
    const when = day === today ? t("dg.whenToday") : day === tom ? t("dg.whenTomorrow") : dateWord(day);
    return t("dg.nextChk", { when: when, hhmm: timeOf(nx.ts) });
  }
  function relBlock(doc, rows) {
    const sm = doc.summary || {}, stale = !!sm.stale, need = needOf(sm);
    if (isNum(sm.rel_total)) {
      const day = stale ? lastTestDay(sm) : null;
      return el("div", { class: "gx-rel" },
        el("div", { class: "gx-rel__v" }, el("span", { class: "gx-rel__n" + (stale || sm.first ? " is-grey" : ""), text: K.fmtInt(sm.rel_total) }),
          el("span", { class: "gx-rel__u", text: t("dg.relOf") })),
        el("div", { class: "gx-rel__b" }, el("span", { class: "gx-rel__s", text: t("dg.relNote") + (day ? " · " + t("dg.relStale", { date: dateWord(day) }) : "") })));
    }
    // числа нет: звеньев со своим «от обычного» меньше нужного — сколько их; ни одного — сколько дней истории набралось
    // (NS-D35); история есть, а свежего значения нет (тесты не прошли) — «—», как у звеньев (REV-score-cut З-1)
    const few = isNum(sm.rel_n) && sm.rel_n > 0;
    const n = few ? sm.rel_n : Math.max(0, ...(doc.links || []).map((l) => Math.floor(Number(l && l.usual_n) || 0)), ...rows.map((r) => r.n));
    if (!few && n >= need) {
      return el("div", { class: "gx-rel" }, el("div", { class: "gx-rel__v" }, el("span", { class: "gx-rel__n is-grey", text: "—" }),
        el("span", { class: "gx-rel__u", text: t("dg.relOf") })), el("div", { class: "gx-rel__b" }, el("span", { class: "gx-rel__s", text: t("dg.relNote") })));
    }
    return el("div", { class: "gx-rel" },
      el("div", { class: "gx-rel__v" }, el("span", { class: "gx-rel__none", text: capFirst(few ? t("dg.relNoneLinks", { n: K.fmtInt(n), need: K.fmtInt(need) })
        : t("dg.relNone", { n: K.fmtInt(n), need: K.fmtInt(need) })) })),
      el("div", { class: "gx-rel__b" }, el("span", { class: "gx-rel__s", text: t(few ? "dg.noRelSLinks" : "dg.noRelS") })));
  }
  function lastBox(sm) {
    const r = runOf(sm.last_id);
    if (!r) return el("div", { class: "gx-chklast" }, N.lastErr ? el("span", { text: N.lastErr.message || t("dg.dayFailed") }) : K.Tpl.state("loading", { rows: 1 }));
    const p = parts(r.ts);
    return el("div", { class: "gx-chklast" },
      el("span", { text: [p ? dateWord(p.day) + ", " + p.time : "", t(MODE[r.mode] || "dg.quick").toLowerCase(), byText(r)].filter(Boolean).join(" · ") }),
      el("span", { text: [durOf(r), trafficOf(r)].filter(Boolean).join(" · ") || "—" }));
  }
  function toggleLast(sm) {
    N.lastOn = !N.lastOn;
    if (N.lastOn && sm.last_id && !runOf(sm.last_id)) {
      const id = sm.last_id;
      N.lastErr = null;
      API().run(id).then((d) => { N.last = d && d.id ? d : null; }, (e) => { N.lastErr = e; }).then(() => draw());
    }
    draw();
  }
  // круг 1 Т1 (вариант Б): правая колонка карточки «Балл сервера» — когда проверено, «от обычного» (relBlock), [почему
  // итог не выше — часть ovb], отметки, кнопки, следующая проверка и ссылка на расписание (оно — в «Настройки › Проверка
  // сервера»). top — до «почему», bottom — после
  function scoreParts(doc, rows, ms) {
    const sm = doc.summary || {}, stale = !!sm.stale, need = needOf(sm);
    const top = [];
    const lr = runOf(sm.last_id);
    if (stale) {
      const day = lastTestDay(sm);
      top.push(el("span", { class: "gx-chk__s", text: day ? t("dg.stale", { date: dateWord(day) }) : t("dg.staleBare") }));
    } else {
      const ch = sm.checked;
      if (ch && PART[ch.part] && ch.hhmm) top.push(el("span", { class: "gx-chk__s", text: t("dg.checked", { part: t(PART[ch.part]), hhmm: ch.hhmm }) + (lr ? " · " + byText(lr) : "") }));
      if (sm.first) top.push(el("span", { class: "gx-chk__s", text: t("dg.first") }));
    }
    top.push(relBlock(doc, rows));
    const bottom = [];
    if (rows.length) bottom.push(el("span", { class: "gx-rel__mk", text: marksLine(rows, ms) }));
    const under = [];
    if (sm.retry && sm.retry.at) under.push(t("dg.retryAt", { at: sm.retry.at }));
    const ci = ms.findIndex((m) => m && m.st === "conf");
    if (ci >= 0) under.push(markText(rows[ci], ms[ci], sm, need));
    (sm.base_since || []).forEach((b) => { if (b && BASE[b.kind] && b.day) under.push(t("dg.baseLine", { date: dateWord(b.day), what: t(BASE[b.kind]) })); });
    under.forEach((x) => bottom.push(el("span", { class: "gx-chk__u", text: x })));
    const go = U.btn(t("dg.checkBtn"), "primary", () => openCost(), { md: true, tip: t("dg.checkX"), cls: phone() ? "kk-mbtn" : "" });
    // «Поделиться» (C10): только здесь, у балла на «Сейчас» (в истории, детали и сравнении проверки её нет, §11.1 п. 24)
    const share = U.btn(t("dg.share"), "tertiary", () => openShare(), { md: true, icon: "arrow-up-from-line", tip: t("dg.shareTip"), cls: phone() ? "kk-mbtn" : "" });
    const lastBtn = sm.last_id ? el("button", { type: "button", class: "gx-chk__last", "aria-expanded": String(N.lastOn) }, t("dg.lastBtn"), K.icon("chevron-down", 14)) : null;
    if (lastBtn) lastBtn.addEventListener("click", () => toggleLast(sm));
    bottom.push(el("div", { class: "gx-chk__btns" }, go, share, lastBtn), N.lastOn && sm.last_id ? lastBox(sm) : null);
    // следующая проверка · расписание — ссылкой в «Настройки › Проверка сервера» (круг 1 Т1: своего адреса у него больше нет)
    const nx = nextText(sm), off = nx === t("dg.schedOff");
    bottom.push(el("span", { class: "gx-chk__u kb-ck__next" }, nx && !off ? nx + " · " : "",
      el("button", { type: "button", class: "ga-btn gx-link", onclick: () => K.goNav("settings", { tab: "health" }) }, off ? nx : t("dg.schedWhere"))));
    return { top, bottom: bottom.filter(Boolean) };
  }
  // окно «Поделиться» (C10; SPEC-node-score-schedule-share «Сверка 24.10»; макет r1): кадр, GIF, PNG и само окно —
  // pages/diag-share.js, грузится при первом нажатии; данные окна — только GET /api/host/share. «Проверить сейчас» из
  // окна (годной проверки нет) — то же окно цены, что у кнопки
  function openShare() {
    return K.loadScript("pages/diag-share.js").then(() => {
      if (window.KaskadShare && window.KaskadShare.open) window.KaskadShare.open(K, { onCheck: () => openCost() });
    }, (e) => K.toast(t("dg.sh.loadFailed"), (e && e.message) || "", "danger"));
  }

  // ── таблица звеньев (ПК) и список (телефон) ──
  function relCell(r) { return r.rel === null ? "—" : K.fmtInt(r.rel); }
  // круг 1 Т1 (вариант Б): «Звенья» — звено и отметка · линия 30 дней · сейчас · обычно · от обычного; на телефоне те же
  // строки (раскладку меняет ovb.css). Сколько замеров в базе и полный текст отметки — подсказкой строки
  // единица после числа — неразрывно, мельче («%» — вплотную)
  const unitOf = (v, u) => (isNum(v) && u ? el("small", { text: u === "%" ? "%" : "\u00a0" + unitText(u) }) : null);
  // Д2 r3 F: значение есть, а «обычно» ещё не набралось (отметка few: rel нет, usual_n < need) — вместо «— —» в «Обычно» и
  // «От обычного» одна ячейка: точки дней истории + «"обычно" появится, когда наберётся история», «k из n» — подсказкой.
  // n и число точек — правило сервера: summary.rel_need (= kaskad.score.REL_N_MIN, «обычно» и rel — от 3 дней истории),
  // закрашено — usual_n. Номер дня не обещаем: история — дни ДО окна трёх последних значений (score._series_dev,
  // _probe_dev), при одном значении в день «обычно» есть с 6-го дня, при нескольких — раньше (REV-it1-b С-4).
  // Точек больше FEW_PIPS не рисуем (только текст): ряд не растягивает строку, если правило сервера сменится
  const FEW_PIPS = 10;
  function fewOf(r, m, need) {
    if (r.cur === null || !m || m.st !== "few" || r.base !== null) return null;
    const n = Math.max(1, Math.floor(need));
    return { n, k: Math.min(r.n, n) };
  }
  function fewCell(f) {
    const tip = t("dg.fewHistTip", { k: K.fmtInt(f.k), n: K.fmtInt(f.n) });
    return el("span", { class: "kb-lk__few", role: "cell", "aria-colspan": "2", title: tip, "aria-label": tip },
      f.n <= FEW_PIPS ? el("span", { class: "kb-lk__pips", "aria-hidden": "true" }, Array.from({ length: f.n }, (_, i) => el("i", { class: i < f.k ? "is-on" : null }))) : null,
      el("span", { text: t("dg.fewHist") }));
  }
  function linkCard(doc, rows, ms, need, days) {
    const sm = doc.summary || {}, ph = phone();
    const cols = ["dg.lnLink", "dg.ln30", "dg.lnNow", "dg.lnUsual", "dg.lnRel"];
    const head = el("div", { class: "kb-lk__h", role: "row" }, cols.map((c) => el("span", { role: "columnheader", title: c === "dg.ln30" ? t("dg.ln30Tip") : null, text: t(c) })));
    const body = rows.map((r, i) => {
      const m = ms[i];
      const dn = !!m && m.st === "two" && m.dir !== "up";
      // Л3 №8 (владелец: «незаполненные данные»): у звена без свежего значения — только причина на месте «сейчас ·
      // обычно · от обычного», без «— —»; отметка «мало истории» / «не измерено» у имени её не дублирует
      // Д2 r3 F: истории мало — точки и «"обычно" появится…» на месте «обычно · от обычного», отметки у имени тоже нет
      const none = r.cur === null, few = fewOf(r, m, need), mk = m && !((none || few) && (m.st === "few" || m.st === "nm")) ? m : null;
      const cur = () => el("span", { class: "kb-lk__v", role: "cell" }, fv(r.cur), unitOf(r.cur, r.unit));
      const cells = none ? [el("span", { class: "kb-lk__v kb-lk__why", role: "cell", "aria-colspan": "3", text: whyShort(r) || t("dg.mk.nm") })]
        : few ? [cur(), fewCell(few)]
        : [cur(),
          el("span", { class: "kb-lk__us", role: "cell" }, isNum(r.base) ? fv(r.base) : "—", unitOf(r.base, r.unit)),
          el("span", { class: "kb-lk__rel" + (dn ? " is-dn" : ""), role: "cell", text: relCell(r) })];
      return el("div", { class: "kb-lk__r" + (none ? " is-dim" : few ? " is-few" : ""), role: "row", title: rowTitle(r, m, sm, need) },
        el("span", { class: "kb-lk__n", role: "cell" }, el("b", { text: t(CNAME[r.id]) }), mk ? el("span", { class: mkCls(mk), text: t(mkKey(mk)) }) : null),
        el("span", { class: "kb-lk__c", role: "cell" }, lineCell(r, m, doc, days, ph, few)), cells);
    });
    const ds = days.length ? [days[0], days[Math.floor((days.length - 1) / 2)], days[days.length - 1]] : [];
    const ax = ds.length ? el("div", { class: "kb-lk__ax", "aria-hidden": "true" }, el("span"), el("span", {}, ds.map((d, j) => el("span", { text: j === 2 ? t("dg.whenToday") : dateWord(d.day) })))) : null;
    return el("section", { class: "gx-card", "aria-label": t("dg.lnAria") },
      el("div", { class: "kb-h" }, el("h2", { class: "kb-t", text: t("dg.lnT") }), el("span", { class: "kb-mut", text: t("dg.lnNote") })),
      el("div", { class: "kb-lk", role: "table", "aria-label": t("dg.lnAria") }, head, body, ax));
  }

  // ── полоса 30 дней: цвет — по событиям, причина словами; подсказка дня — наведением, нажатие — закрепить ──
  function dayAria(d) {
    return dateWord(d.day) + " — " + (d.reason && d.reason.msg ? String(d.reason.msg) : t(DCOL[d.color] || "dg.c.none")) + (d.full ? " · " + t("dg.c.full") : "");
  }
  // число и единица — неразрывно; «Мбит/с» в конце строки проверки — целиком (Chrome переносит после «/»)
  function tipRows(d) {
    const m = d.m || {}, out = [];
    const ms = "\u00a0" + t("dg.u.ms");
    if (isNum(m.rtt)) out.push([t("dg.tip.rtt"), fv(m.rtt) + ms + (isNum(m.rtt_p90) ? " · " + t("dg.tip.p90", { v: fv(m.rtt_p90) + ms }) : "")]);
    if (isNum(m.loss)) out.push([t("dg.tip.loss"), fv(m.loss) + "%" + (isNum(m.loss_peak) ? " · " + t("dg.tip.peak", { v: fv(m.loss_peak) + "%" }) : "")]);
    if (isNum(m.jit)) out.push([t("dg.tip.jit"), fv(m.jit) + ms]);
    if (isNum(m.off)) out.push([t("dg.tip.off"), t("dg.min", { n: K.fmtInt(m.off) })]);
    (Array.isArray(d.checks) ? d.checks : []).forEach((c) => {
      if (!c) return;
      const k = t(MODE[c.mode] || "dg.quick") + " " + (c.hhmm || "");
      if (c.status === "skipped") return out.push([k, t("dg.tip.skip")]);
      if (c.status !== "ok" && c.status !== "partial") return out.push([k, t("dg.tip.fail")]);
      const bits = [isNum(c.down) ? t("dg.tip.down", { v: fv(c.down) }) : "", isNum(c.up) ? t("dg.tip.up", { v: fv(c.up) }) : ""].filter(Boolean);
      out.push([k, bits.length ? [bits.join(" · ") + "\u00a0", el("span", { class: "gx-nw", text: t("dg.u.mbps") })] : "—"]);
    });
    return out;
  }
  function tipKids(d, o) {
    let wd = "";
    try { wd = new Intl.DateTimeFormat(LOC(), { timeZone: "UTC", weekday: "long" }).format(new Date(d.day + "T12:00:00Z")); } catch (e) { wd = ""; }
    const kids = [el("div", { class: "gx-daytip__d" }, el("i", { class: DICO[d.color] || "is-none", "aria-hidden": "true" }), el("span", { text: dateWord(d.day) + (wd ? ", " + wd : "") }),
      o && o.close ? U.btn("", "ghost", o.close, { icon: "xmark", tip: t("dg.close"), cls: "gx-daytip__x" }) : null),
      el("div", { class: "gx-daytip__r", text: d.reason && d.reason.msg ? String(d.reason.msg) : t(d.color === "grey" ? "dg.tip.none" : "dg.tip.even") })];
    const rows = tipRows(d);
    if (rows.length) kids.push(el("div", { class: "gx-daytip__m" }, rows.map((x) => [el("span", { class: "gx-daytip__k", text: x[0] }), el("span", null, x[1])]).flat()));
    if (o && o.close && d.test) kids.push(el("div", { class: "gx-daytip__a" }, U.btn(t("dg.dayHist"), "link", () => { o.close(); K.goNav(ID, { tab: "history", focus: d.day }); })));
    return kids;
  }
  function stripCard(doc) {
    const days = (doc.days || []).filter((d) => d && /^\d{4}-\d{2}-\d{2}$/.test(String(d.day))).slice(-30);
    const ph = phone();
    const tip = el("div", { class: "gx-daytip", role: "dialog", hidden: true });
    let pinned = -1, timer = 0, cur = -1, from = null, quiet = false;
    const btns = [];
    const hide = () => { clearTimeout(timer); pinned = -1; cur = -1; tip.hidden = true; tip.replaceChildren(); btns.forEach((b) => b.setAttribute("aria-pressed", "false")); };
    // Esc и «×» закрывают подсказку (и закреплённую); фокус из неё — обратно на её день (REV-ns-p4b П-1); он вернётся
    // без новой подсказки: quiet глушит показ по фокусу дня
    const shut = () => {
      const back = tip.contains(document.activeElement) ? from : null;
      hide();
      if (back && back.focus) { quiet = true; try { back.focus(); } finally { quiet = false; } }
    };
    const esc = (ev) => {
      if (ev.key !== "Escape" || tip.hidden) return;
      shut();
      if (ev.preventDefault) ev.preventDefault();
    };
    tip.addEventListener("keydown", esc);
    const later = () => { clearTimeout(timer); if (pinned < 0) timer = setTimeout(hide, POP_MS); };
    function show(i, pin) {
      clearTimeout(timer);
      if (pin) pinned = i;
      if (cur === i && !pin) return;
      cur = i;
      const d = days[i];
      tip.setAttribute("aria-label", dateWord(d.day));
      tip.className = "gx-daytip" + (pinned >= 0 ? " is-pin" : "") + (ph ? "" : i < 5 ? " gx-daytip--l" : i > days.length - 6 ? " gx-daytip--r" : "");
      tip.replaceChildren(...tipKids(d, { close: pinned >= 0 ? shut : null }));
      if (!ph) tip.style.setProperty("left", String(Math.round(((i + 0.5) / Math.max(1, days.length)) * 10000) / 100) + "%");
      btns.forEach((b, j) => b.setAttribute("aria-pressed", String(j === pinned)));
      tip.hidden = false;
    }
    tip.addEventListener("mouseenter", () => clearTimeout(timer));
    tip.addEventListener("mouseleave", later);
    // с клавиатуры — как курсор: фокус в подсказке снимает таймер угасания, уход фокуса из неё — как уход курсора (REV-ns-p4d П-2)
    tip.addEventListener("focusin", () => clearTimeout(timer));
    tip.addEventListener("focusout", later);
    days.forEach((d, i) => {
      const b = el("button", { type: "button", class: "gx-day" + (DCLS[d.color] !== undefined ? DCLS[d.color] : DCLS.grey) + (d.full ? " is-test" : ""), "aria-label": dayAria(d), "aria-pressed": "false" });
      b.addEventListener("mouseenter", () => { if (!ph && pinned < 0) show(i, false); });
      b.addEventListener("mouseleave", later);
      // с клавиатуры — как наведение: фокус на дне показывает подсказку, уход фокуса закрывает незакреплённую (П-1)
      b.addEventListener("focus", () => { from = b; if (!ph && pinned < 0 && !quiet) show(i, false); });
      b.addEventListener("blur", later);
      b.addEventListener("keydown", esc);
      b.addEventListener("click", () => {
        from = b;
        if (pinned === i) { hide(); return; }                    // второе нажатие на закреплённый день — снять
        pinned = -1;
        show(i, true);
        // закреплённая подсказка — сразу под фокусом: «Открыть в истории» без Tab по оставшимся дням (П-2)
        const a = tip.querySelector(".gx-daytip__a"), go = (a && a.querySelector("button")) || tip.querySelector("button");
        if (go && go.focus && !ph) go.focus();
      });
      btns.push(b);
    });
    const cnt = { green: 0, yellow: 0, red: 0, grey: 0 };
    days.forEach((d) => { cnt[DCLS[d.color] !== undefined ? d.color : "grey"] += 1; });
    const sum = t("dg.dSum", { ok: K.fmtInt(cnt.green), warn: K.fmtInt(cnt.yellow), fail: K.fmtInt(cnt.red) }) + (cnt.grey ? " · " + t("dg.dSumNone", { n: K.fmtInt(cnt.grey) }) : "");
    const sw = (cls, key) => el("span", null, el("i", { class: cls || null, "aria-hidden": "true" }), t(key));
    const legend = ph ? null : el("div", { class: "gx-legend" }, sw("", "dg.c.ok"), sw("is-warn", "dg.c.warn"), sw("is-bad", "dg.c.fail"), sw("is-off", "dg.c.none"),
      el("span", { title: t("dg.c.dotTip") }, t("dg.c.dot")));
    const short = (day) => { try { return new Intl.DateTimeFormat(LOC(), { timeZone: "UTC", day: "numeric", month: "short" }).format(new Date(day + "T12:00:00Z")); } catch (e) { return day; } };
    const ax = !days.length ? [] : ph ? [short(days[0].day), t("dg.axToday")]
      : [0, 7, 14, 21].filter((i) => i < days.length - 3).map((i) => short(days[i].day)).concat([t("dg.axToday")]);
    return el("div", { class: "gx-days" + (ph ? " gx-days--ph" : "") },
      el("div", { class: "gx-days__h" }, el("span", { class: "gx-days__t", text: t("dg.d30T") }), el("span", { class: "gx-days__s", text: days.length ? sum : t("dg.d30None") }), legend),
      days.length ? el("div", { class: "gx-days__row", role: "group", "aria-label": t("dg.d30T") }, btns, tip) : null,
      days.length ? el("div", { class: "gx-days__ax", "aria-hidden": "true" }, ax.map((x) => el("span", { text: x }))) : null);
  }
  // «От вашего устройства до сервера» (BC5): своя карточка под проверкой сервера — часть "speed" (pages/speed.js), грузится
  // один раз; её узел живёт между перерисовками раздела (замер идёт и пока «Сейчас» перерисовывается). Не загрузилась —
  // раздел без неё, следующая перерисовка попробует снова
  let spdNode = null, spdLoad = null;
  function speedCard() {
    if (spdNode) return spdNode;
    if (!spdLoad) {
      spdLoad = K.loadPart("speed").then((pt) => { spdNode = pt.card(); if (tab === "now") draw(); }, (e) => {
        spdLoad = null;
        try { console.error(e); } catch (x) { /* консоли нет */ }
      });
    }
    return null;
  }
  // «Сейчас» (круг 1 Т1, вариант Б): карточка «Балл сервера» (радар и итог 0–1000 — часть ovb; пока часть грузится — та же
  // колонка без радара), «Звенья», полоса 30 дней, «От вашего устройства до сервера»
  let OVB = null;
  function wantOvb() {
    if (OVB) return;
    K.loadPart("ovb").then((p) => { if (!p || typeof p.scoreCard !== "function") return; OVB = p; if (body && tab === "now") draw(); }, () => { /* без радара — колонка как есть */ });
  }
  function scoreBox(doc, rows, ms) {
    if (!OVB) {
      const sp = scoreParts(doc, rows, ms);
      return el("section", { class: "gx-card", "aria-label": t("dg.totK") }, el("div", { class: "kb-ck__r gx-chk__main" }, sp.top, sp.bottom));
    }
    // радар и итог перерисовываются, когда пришёл балл (метка части в compute); колонка — заново из того же ответа
    return K.blk(() => ({ v: OVB.ver(), ph: phone(), on: N.lastOn }), () => { const sp = scoreParts(doc, rows, ms); return OVB.scoreCard({ top: sp.top, bottom: sp.bottom }); }, { cls: "kb-ck-wrap" });
  }
  function nowView() {
    wantOvb();
    if (!N.doc) {
      const st = N.err ? K.Tpl.state("error", { text: N.err.message || "", onRetry: () => loadScore() }) : K.Tpl.state("loading", { rows: 4 });
      return el("div", { class: "gx-now" }, st, speedCard());
    }
    const doc = N.doc, sm = doc.summary || {}, need = needOf(sm);
    const rows = rowsOf(doc), ms = rows.map((r) => markOf(r, need));
    const days = (doc.days || []).filter((d) => d && /^\d{4}-\d{2}-\d{2}$/.test(String(d.day))).slice(-30);
    const links = rows.length ? linkCard(doc, rows, ms, need, days) : null;
    if (links && K0S && K0S.watch) K0S.watch(links);
    return el("div", { class: "gx-now kb" }, scoreBox(doc, rows, ms), links, el("section", { class: "gx-card", "aria-label": t("dg.ln30") }, stripCard(doc)), speedCard());
  }

  // ── окно цены «Проверить сервер» (gx-cost): цена каждого вида с ключом — чтением GET /api/host/bench/price (ответ 200:
  // прежние отказы POST без ключа давали 4xx в консоли на каждое открытие, T-3377 Н-15), запуск — с ключом этой цены. Программы — отдельная галочка (без неё «Проверить» выключена); «без программ» —
  // install: false с предупреждением, что расписание не начнётся (NS-S4). 8b (iperf, D-144): у полной проверки сервера,
  // который может быть в России (upload_ru цены), — галочка «Замерить отдачу …» со строкой «точка увидит адрес», включена;
  // снятая — iperf: false в теле запуска (быстрая 8b не запускает никогда)
  // место на диске — как в SSH-меню и kaskadctl (bench.pkgs._mb: МБ по 10^6 байт, не меньше 1), чтобы число было одно (T-3377 Н-16)
  const diskMb = (kib) => Math.max(1, Math.round(kib * 1024 / 1e6));
  const willInstall = (p) => !!(p && Array.isArray(p.missing) && p.missing.length && p.available !== false);
  function priceText(p) {
    if (!p) return "";
    const bits = [t("dg.costTraffic", { pre: pre(p), gb: gbOf(mbOf(p)) }), t("dg.costCpu", { pre: pre(p), cpu: cpuText(p.cpu_min) })];
    if (isNum(p.max_min)) bits.push(t("dg.costDur", { n: K.fmtInt(p.max_min) }));
    return bits.join(" · ");
  }
  function openCost(mode0) {
    const C = { mode: mode0 === "full" ? "full" : "quick", price: {}, err: {}, pkgs: null, consent: false, busy: false, msg: null, loading: true, done: false,
      upq: false, up: true, pk3: null, cset: "" };
    const box = el("div", { class: "gx-cost" });
    const dlg = K.openDialog({ size: "md", title: t("dg.costT"), sub: t("dg.costSub"), blocks: [box], onClose: () => { C.done = true; } });
    const start = API().start, readPrice = API().price;
    // набор программ от сервера; согласие дано на показанный набор — другой набор (409 confirm_changed / need_install
    // с иным missing) снимает галочку (REV-ns-p4b З-2: без отдельного согласия fio не ставится)
    const setOf = (p) => (willInstall(p) ? p.missing.map(String).sort().join("\n") : "");
    // программы того, что пойдёт (С2 REV-score-rename): полная с замером отдачи — набор с iperf3 (upload_ru.pkgs),
    // иначе — без него; согласие — на набор, который был показан: другой набор (галочка отдачи, другой вид) его снимает
    const withUp = () => C.mode === "full" && C.upq && C.up && !!C.pk3;
    const cur = () => (withUp() ? C.pk3 : C.pkgs);
    function setPkgs(p, up) {
      if (!p) return;
      if (up) C.pk3 = p;
      else C.pkgs = p;
    }
    // фокус с клавиатуры: paint() пересоздаёт радио, галочку и кнопки — фокус возвращается на ту же по счёту (З-3)
    const boxes = () => [box, dlg.foot].filter(Boolean);
    function paint() {
      if (C.done) return;
      const back = focusIn(boxes());
      const kids = [];
      const ready = !C.loading && !C.busy && !!C.price[C.mode] && !C.err[C.mode] && !C.stop;
      if (C.consent && C.cset !== setOf(cur())) C.consent = false;
      const need = willInstall(cur());
      if (C.loading) kids.push(K.Tpl.state("loading", { rows: 2 }));
      else {
        kids.push(U.radio(TYPES.map((m) => ({ title: t(m === "quick" ? "dg.costQ" : "dg.costF"), desc: t(m === "quick" ? "dg.costQd" : "dg.costFd"),
          note: C.err[m] || priceText(C.price[m]), on: C.mode === m, dis: !C.price[m] || C.busy, pick: () => { C.mode = m; C.msg = null; paint(); } })), { aria: t("dg.costMode") }));
        if (C.upq && C.mode === "full") kids.push(U.check([{ title: t("dg.upRu"), desc: t("dg.upRuX"), on: C.up, dis: C.busy, pick: (v) => { C.up = !!v; paint(); } }]));
        const p = cur();
        if (willInstall(p)) {
          const names = p.missing.join(", ");
          const kib = Number(p.kib) || 0;
          kids.push(el("div", { class: "gx-cost__consent" },
            el("p", { text: t("dg.pkgX", { pkgs: names }) }),
            U.check([{ title: t("dg.pkgOk", { pkgs: names }), desc: kib ? t("dg.pkgSize", { n: K.fmtInt(Array.isArray(p.install) && p.install.length ? p.install.length : p.missing.length), mb: K.fmtInt(diskMb(kib)) }) : null, on: C.consent, dis: C.busy,
              pick: (v) => { C.consent = !!v; C.cset = setOf(cur()); paint(); } }])));
          // без одного iperf3 расписание не страдает — пропадает только отдача (С2)
          const only3 = p.missing.every((x) => x === "iperf3");
          kids.push(U.al("warning", t(only3 ? "dg.pkgNoUpT" : "dg.pkgNoT"), t("dg.pkgNoX"),
            { btns: [U.btn(t("dg.costGoNo"), "tertiary", () => run(false), { md: true, dis: !ready })] }));
        } else if (p && Array.isArray(p.missing) && p.missing.length && p.available === false) {
          kids.push(U.al("", t("dg.pkgNone", { pkgs: p.missing.join(", ") }), ""));
        }
      }
      if (C.msg) kids.push(U.al(C.msg.tone, C.msg.title, C.msg.text));
      if (need && !C.consent && ready) kids.push(el("p", { class: "gx-cost__why", text: t("dg.consentWhy") }));   // почему «Проверить» выключена
      box.replaceChildren(...kids);
      const go = U.btn(t("dg.costGo"), "primary", () => run(need ? true : null), { md: true, dis: !ready || (need && !C.consent) });
      const foot = [U.btn(t("dg.costNo"), "ghost", () => dlg.close(null), { md: true }), go];
      dlg.setFoot(foot);
      back();
    }
    // отказ сервера -> слова окна (409/429 — текст сервера, «уже идёт» — свой с подсказкой)
    function said(e, m) {
      const b = (e && e.body) || {};
      const code = (e && e.code) || b.code;
      if (code === "need_confirm" || code === "confirm_changed") {
        if (b.price) C.price[m] = b.price;
        setPkgs(b.pkgs, withUp());          // набор запроса: галочка и вид во время запроса заперты (busy)
        return code === "confirm_changed" ? { tone: "warning", title: t("dg.costChanged"), text: String(b.error || "") } : null;
      }
      if (code === "need_install") {
        setPkgs(b.pkgs, withUp());          // набор запроса: галочка и вид во время запроса заперты (busy)
        return { tone: "warning", title: t("dg.pkgAsk"), text: String(b.error || "") };
      }
      if (code === "running") { C.stop = true; return { tone: "warning", title: t("dg.costRunning"), text: t("dg.costRunningX") }; }
      // идёт замер от устройства до сервера (BC5): проверка сервера ждёт его — текст сервера с остатком секунд
      if (code === "speed_test") return { tone: "warning", title: String(b.error || t("dg.costSpeedTest")), text: "" };
      if (code === "limit") return overDay(b, m);
      return { tone: "danger", title: t("dg.costFailed"), text: String((e && e.message) || b.error || "") };
    }
    // предел дня вида m: {error|text, today} — 429 запуска или limit[m] чтения цены
    function overDay(b, m) {
      const td = b.today || {};
      C.err[m] = t("dg.costLimitShort");
      return { tone: "warning", title: String(b.error || b.text || t("dg.costLimitShort")), text: isNum(td.all) ? t("dg.costToday", { all: K.fmtInt(td.all), full: K.fmtInt(td.full || 0) }) : "" };
    }
    // цена обоих видов одним чтением (сервер считает цену и пакеты, ничего не запускает) -> первое, что сказать, или null
    function ask() {
      return readPrice().then((d) => {
        const doc = d || {}, modes = doc.modes || {}, lim = doc.limit || {};
        if (doc.running) { C.stop = true; return { tone: "warning", title: t("dg.costRunning"), text: t("dg.costRunningX") }; }
        const said1 = [];
        TYPES.forEach((m) => {
          if (lim[m]) said1.push(overDay(lim[m], m));
          else if (modes[m] && modes[m].confirm) C.price[m] = modes[m];
        });
        setPkgs(doc.pkgs, false);
        C.upq = !!(doc.upload_ru && typeof doc.upload_ru === "object");
        C.pk3 = C.upq && doc.upload_ru.pkgs && typeof doc.upload_ru.pkgs === "object" ? doc.upload_ru.pkgs : null;
        return said1[0] || null;
      }, (e) => ({ tone: "danger", title: t("dg.costFailed"), text: String((e && e.message) || "") }));
    }
    function run(install) {
      if (C.busy || !C.price[C.mode]) return null;
      const m = C.mode;
      const b = { mode: m, confirm: C.price[m].confirm };
      if (willInstall(cur()) && install !== null) b.install = !!install;
      if (m === "full" && C.upq) b.iperf = !!C.up;
      C.busy = true; C.msg = null;
      paint();
      return start(b).then((r) => {
        C.busy = false;
        dlg.close(true);
        K.toast(t("dg.costStarted"), t("dg.costStartedX", { n: K.fmtInt((r && r.price && r.price.max_min) || C.price[m].max_min || 0) }), "success");
        loadScore();
        loadDoc();
      }, (e) => {
        C.busy = false;
        C.msg = said(e, m);
        paint();
      });
    }
    paint();
    return ask().then((said1) => {
      C.loading = false;
      const ok = (m) => !!C.price[m] && !C.err[m];
      if (!ok(C.mode)) C.mode = ok("quick") ? "quick" : ok("full") ? "full" : C.mode;
      C.msg = said1;
      paint();
    });
  }

  // ── раздел ────────────────────────────────────────────────────────────────────────────────────────────
  // адрес -> вкладка и день истории (день — только из адреса: «Назад» браузера возвращает и его); -> перечитать ли список
  function apply(n) {
    tab = TABS.indexOf(n.tab) >= 0 ? n.tab : n.focus === "diag.history" ? "history" : "now";
    const day = n.focus && /^\d{4}-\d{2}-\d{2}$/.test(n.focus) ? n.focus : null;
    if (day) tab = "history";
    if (day === H.day || (!day && tab !== "history")) return false;
    H.day = day;
    return true;
  }
  K.page(ID, {
    res: [],
    build(sec) {
      SCHED = false;
      root = sec;
      head = el("div", { class: "gx-diag__head" });
      body = el("div", { class: "gx-diag__body" });
      root.append(el("div", { class: "gx-diag" }, head, body));
      apply(K.navState());
      draw();
      loadDoc();
      loadRuns(false);
      loadScore();
    },
    // смена вкладки или дня (#diag?tab=…, ?focus=YYYY-MM-DD, «Назад» браузера) — без пересборки раздела
    nav(n) {
      if (!body) return false;
      if (apply(n)) loadRuns(false);
      else draw();
      return true;
    },
    // ?open=share — окно «Поделиться» (ссылка бота; app.js PAGES.overview.opens)
    openShare: () => openShare(),
    // «Настройки › Проверка сервера» (круг 1 Т1): только расписание — время сервера строкой и список с листом правки
    schedule(box) {
      SCHED = true;
      tab = "schedule";
      root = box;
      head = el("div", { class: "gx-diag__head" });
      body = el("div", { class: "gx-diag__body" });
      box.append(el("div", { class: "gx-diag gx-diag--sched" }, head, body));
      draw();
      loadDoc();
    },
    // «Последняя проверка» раскрыта только пока раздел открыт
    leave() { SCHED = false; root = null; head = null; body = null; N.lastOn = false; N.last = null; N.lastErr = null; N.seq += 1; },
  });
})();
