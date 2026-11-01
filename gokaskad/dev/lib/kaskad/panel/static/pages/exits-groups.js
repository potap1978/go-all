/* Kaskad 3 — «Выходы → Группы» и деталь выхода (этап 1, T8; SE §7.1–7.3, §7.9; SPEC-groups §8). Часть раздела
   «Выходы»: её грузит app.js (K.loadPart("exits-groups")) при первом входе на вкладку «Группы» или в деталь выхода —
   pages/exits-groups-i18n-ru.js (+ -en.js при английском), затем этот файл; он регистрирует часть через K.part().

   Данные — ресурсы ядра R.policy (GET /api/gw/policy) и R.exits (GET /api/gw/exits); записи — K.API (контракт §7.9).
   Правила записей (SE §7 «Версия политики»): форма группы копит правки до «Сохранить» (Kit.saveBar, base_rev, один
   PUT /api/gw/policy с изменённой группой от сохранённой политики); кнопки над одним объектом — сразу, своим запросом,
   подтверждение на месте со своим ?dry=1 (Kit.confirmAction); пока у группы несохранённые правки, кнопки «сразу» над
   ней, её выходами и над ней как целью выключены. После каждой своей записи — перечитать политику и выходы; открытые
   формы перекладываются (Kit.rebaseModel): объект не менялся — молча, менялся — список «Применить снова».
   Вид (UI-A2, HANDOFF r3 «Выходы › Группы»): группы и «Тест скорости» — свёрнутыми строками (имя, уровни, выходы,
   устройства, состояние), раскрытие по нажатию на всю ширину; «Поведение» и «Проверки» — вторым раскрытием в группе;
   что раскрыто, помнят переменные части (живая перерисовка не схлопывает). Одна акцентная кнопка на экран (accentOf).
   Классы — компонентов (gx-*, ga-btn) и свои kk-eg-* (styles.css); атрибутов style нет (CSP). */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const { el, t, U, Kit } = K;
  const ID = "exits-groups";
  // Ключи формы группы (Kit.rebaseModel): что копится до «Сохранить»
  // new_exits_tier — «Куда встают новые и перенесённые» (РФ-группы Ш2, REV-plan З-2: счётчик правок, «что изменится»,
  // перекладка при конфликте видят и его)
  const KEYS = ["fail", "preset", "tiers", "new_exits_tier", "evict_degraded", "return_to_primary", "night_rebalance", "drop_on_degrade", "per_connection",
    "flap_standby", "rules", "checks.freq", "checks.speed", "checks.services", "checks.geo"];
  const PRESET_KEYS = ["flap_standby", "evict_degraded", "return_to_primary", "night_rebalance", "drop_on_degrade", "rules"];
  const PRESETS = ["economy", "standard", "strict", "custom"];
  // policy.PRESETS["ru"] — у внутренней группы «Дом» и «Свой» (решение 11; РФ-группы 29.10: наборы «Стандарт» и др. для
  // неё — не в этом выпуске, формат данных и откат — REV-plan С-4). Набор задаёт проверки и поведение, а уровни, способ
  // выбора и последний шаг — владельца (policy.apply_preset): их правка набор не меняет
  const RU_PRESETS = ["ru_home", "custom"];
  const METHODS = ["even", "best_rtt", "weights", "capacity"];
  const FREQ = [["lazy", null], ["often", 10], ["normal", 30], ["rare", 120], ["custom", null]];
  const SPEED_H = [1, 3, 6, 12, 24];
  const MAX_TIERS = 10;                                   // policy.MAX_TIERS (T7 круг 2)
  const RULE_TPL = {                                      // шаблоны условий (SE §7.2)
    rtt: () => rule("rtt", ">", 300, "ms", "degraded", [cond("rtt", "<", 200, "ms")], 2, 5),
    rttNorm: () => rule("rtt", ">", 2, "x_norm", "degraded", [cond("rtt", "<", 1.5, "x_norm")], 5, 10),
    loss: () => rule("loss", ">", 10, "pct", "degraded", [cond("loss", "<", 5, "pct")], 2, 5),
    slow: () => rule("speed", "<", 5, "mbps", "degraded", [cond("speed", ">", 10, "mbps")], 30, 60),
    // «Сервис не открывается» — только с сервисом в проверках группы; «Страна не та» — country != код → ухудшен (§3.3)
    service: (g) => { const sid = (((g && g.checks && g.checks.services) || [])[0] || {}).id || ""; return rule("service", "==", false, "ok", "degraded", [Object.assign(cond("service", "==", true, "ok"), { service: sid })], 5, 10, sid); },
    // «Страна не та»: ожидаемая страна группы (geo.expect или общая страна её выходов) — сразу в обе стороны
    country: (g) => { const iso = expectedCountry(g); return rule("country", "!=", iso, "iso", "degraded", [cond("country", "==", iso, "iso")], 5, 10); },
  };
  function cond(metric, op, value, unit) { return { metric, op, value, unit }; }
  function expectedCountry(g) {
    const ex = g && g.checks && g.checks.geo ? g.checks.geo.expect : "";
    if (typeof ex === "string" && /^[A-Z]{2}$/.test(ex)) return ex;
    const codes = [];
    ((g && g.tiers) || []).forEach((tr) => tr.exits.forEach((id) => { const r = rowOf(id), c = r && r.country ? r.country.was || r.country.code : ""; if (c && codes.indexOf(c) < 0) codes.push(c); }));
    return codes.length === 1 && /^[A-Z]{2}$/.test(codes[0]) ? codes[0] : "";
  }
  function rule(metric, op, value, unit, then, back, leave, ret, service) {
    const c = cond(metric, op, value, unit);
    if (service !== undefined) c.service = service;
    return { id: "r-" + Math.random().toString(16).slice(2, 8), name: "", enabled: true, exits: "all", when: { match: "all", conds: [c], hold: { minutes: leave } },
      then, back: { match: "all", conds: back, hold: { minutes: ret }, manual: false }, push: true };
  }
  // Ключи, которые часть собирает из кодов сервера и своих списков (состояние, набор, способ выбора, причина…): тексты
  // берутся только из этого списка — неизвестный код не показывает ключ на экране
  const DYN_KEYS = new Set([
    "ep.preset.economy", "ep.preset.standard", "ep.preset.strict", "ep.preset.custom", "ep.preset.compat",
    "ep.presetD.economy", "ep.presetD.standard", "ep.presetD.strict", "ep.presetD.custom", "ep.presetD.compat",
    "ep.method.even", "ep.method.best_rtt", "ep.method.weights", "ep.method.capacity", "ep.freq.lazy",
    "ep.freq.often", "ep.freq.normal", "ep.freq.rare", "ep.freq.custom", "ep.freqD.lazy", "ep.freqD.often",
    "ep.freqD.normal", "ep.freqD.rare", "ep.cond.rtt", "ep.cond.loss", "ep.cond.jitter", "ep.cond.speed",
    "ep.cond.service", "ep.cond.country", "ep.unit.ms", "ep.unit.pct", "ep.unit.x_norm", "ep.unit.mbps",
    "ep.then.degraded", "ep.then.standby", "ep.then.remove", "ep.then.push", "ep.tpl.rtt", "ep.tpl.rttNorm",
    "ep.tpl.loss", "ep.tpl.slow", "ep.tpl.service", "ep.tpl.country", "ep.cond.traffic", "ep.unit.gb_day", "ep.unit.gb_month", "ep.key.fail", "ep.key.preset", "ep.key.tiers", "ep.key.new_exits_tier", "ep.key.evict_degraded",
    "ep.key.return_to_primary", "ep.key.night_rebalance", "ep.key.drop_on_degrade", "ep.key.per_connection",
    "ep.key.flap_standby", "ep.key.rules", "ep.key.checks_freq", "ep.key.checks_speed", "ep.key.checks_services",
    "ep.key.checks_geo", "ep.warn.fail_direct", "ep.warn.xray_restart", "ep.warn.pins_move",
    "ep.warn.flap_standby_on", "ep.sp.max_mb", "ep.sp.max_s", "ep.sp.budget_day_mb", "ep.sp.budget_month_gb",
    "ep.sp.only_quiet", "ep.spH.max_mb", "ep.spH.max_s", "ep.spH.budget_day_mb", "ep.spH.budget_month_gb",
    "ep.spH.only_quiet", "ep.st.ok", "ep.st.degraded", "ep.st.standby", "ep.st.off", "ep.st.unknown", "ep.st.nodata",
    "ep.st.dead", "ep.metric.rtt", "ep.metric.loss", "ep.metric.jitter", "ep.metric.speed", "ep.metric.traffic",
    "ep.metric.country", "ep.metric.country_changed", "ep.metric.service", "ep.op.gt", "ep.op.lt", "ep.op.eq", "ep.op.ne", "ep.why.rule", "ep.why.dead", "ep.why.sub_confirm", "ep.why.manual_standby",
    "ep.why.manual_maintenance", "ep.why.quota", "ep.why.disabled", "ep.why.country", "ep.why.unstable", "ep.quotaAction.remove", "ep.quotaAction.standby",
    "ep.quotaAction.push", "ep.whyT.rule", "ep.whyT.dead", "ep.whyT.disabled", "ep.whyT.country", "ep.whyT.sub_confirm",
    "ep.whyT.manual_standby", "ep.whyT.manual_maintenance", "ep.whyT.quota", "ep.whyT.unstable",
    "ep.preset.ru_home", "ep.presetD.ru_home", "ep.methodInfo.even", "ep.methodInfo.best_rtt", "ep.methodInfo.weights", "ep.methodInfo.capacity",
    "ep.method.manual", "ep.methodInfo.manual", "ep.ord.note.rtt", "ep.ord.note.x_norm", "ep.ord.note.weights", "ep.ord.note.capacity",
    "ep.ord.note.even", "ep.ord.note.per_connection", "ep.ord.why.degraded", "ep.ord.why.standby", "ep.ord.why.off", "ep.ord.why.unknown",
    "ep.ord.why.nometric",
    "ep.scn.t.standard", "ep.scn.t.custom", "ep.scn.t.economy", "ep.scn.t.strict", "ep.scn.t.compat", "ep.scn.t.ru_home",
    "ep.scn.line.economy", "ep.scn.line.strict", "ep.scn.line.compat", "ep.scn.line.ru_home"
  ]);
  const has = (k) => DYN_KEYS.has(k);
  const tk = (prefix, code, vars) => (has(prefix + code) ? t(prefix + code, vars) : String(code === undefined || code === null ? "" : code));
  const copy = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // ── данные ─────────────────────────────────────────────────────────────────────────────────────────────────────
  const pol = () => (K.R.policy && K.R.policy.data) || null;
  const rows = () => (K.R.exits && K.R.exits.loaded && K.R.exits.data ? K.R.exits.data.rows : []);
  const rowOf = (id) => rows().find((r) => r.id === id) || null;
  const exitsKnown = () => !!(K.R.exits && K.R.exits.loaded);
  const exitName = (id) => { const r = rowOf(id); return r ? r.name : id; };
  const groupOf = (gid) => { const p = pol(); return p ? p.policy.groups.find((g) => g.id === gid) || null : null; };
  const groupLabel = (gid) => { const f = forms[gid]; const g = f ? f.draft : groupOf(gid); return g ? g.label || (g.id === "main" ? t("ep.main") : g.name || g.id) : gid; };
  const tierLabel = (tr, i) => tr.name || t("ep.tierN", { n: i + 1 });
  const readRev = () => { const p = pol(); return p ? p.rev : null; };
  const kindText = (r) => (r.kind === "awg" ? "AmneziaWG" + (r.version ? " " + r.version : "") : r.wg ? "WireGuard" : String(r.proto || r.kind || "").toUpperCase());

  // ── формы групп (base_rev, base, draft) ──────────────────────────────────────────────────────────────────────
  const forms = Object.create(null);                     // gid -> {base, draft, rev, sb, list: [правки «Применить снова»], what, open}
  // раскрыто (сразу + без «Отменить» — вид экрана; по умолчанию всё свёрнуто, UI-A2): карточка группы, её второй
  // уровень «Поведение и проверки», полный текст строки про адреса сайтов, «Что изменится» плашки «Появились группы»
  const open = Object.create(null);
  const MORE_OPEN = Object.create(null);
  const DNS_OPEN = Object.create(null);
  let hintMoreOpen = false;
  const dirtyOf = (gid) => { const f = forms[gid]; return !!f && !f.gone && (Kit.dirtyKeys(f.base, f.draft, KEYS).length > 0 || f.list.length > 0); };
  // «Тест скорости — общее на сервер» с круга 1 Т1 — в «Настройки › Проверка сервера» (его правки спрашивают там)
  const anyDirty = () => Object.keys(forms).some(dirtyOf) || exDirty();
  // причина неактивности кнопок «сразу» над выходом (его группа и группа-цель): несохранённые правки
  const unsavedWhy = (...gids) => (gids.some((g) => g && dirtyOf(g)) ? t("kit.unsavedFirst") : "");
  function formOf(g, rev) {
    let f = forms[g.id];
    if (!f) { f = forms[g.id] = { base: copy(g), draft: copy(g), rev, list: [], what: null, card: null, sb: null }; return f; }
    return f;
  }
  // Свежие данные (после чтения): форма без правок — берёт их; с правками и другой rev — перекладка (SE §7)
  function syncForms() {
    const p = pol();
    if (!p) return;
    Object.keys(forms).forEach((gid) => {
      const f = forms[gid], fresh = p.policy.groups.find((g) => g.id === gid) || null;
      if (f.rev === p.rev && fresh) return;
      if (f.sb && f.sb.saving()) return;                 // своё «Сохранить» в полёте — перекладка по его исходу
      if (!dirtyOf(gid)) {
        if (!fresh) { delete forms[gid]; return; }
        f.base = copy(fresh); f.draft = copy(fresh); f.rev = p.rev; f.list = []; f.what = null;
        return;
      }
      rebase(gid, fresh, p.rev);
    });
  }
  function rebase(gid, fresh, rev) {
    const f = forms[gid];
    const r = Kit.rebaseModel(f.base, f.draft, fresh, KEYS);
    if (r.mode === "gone") { f.gone = true; if (f.sb) f.sb.gone(); return r; }
    f.base = copy(fresh); f.rev = rev; f.draft = r.draft;
    if (r.mode === "list") {
      f.list = r.edits;
      const what = Kit.changedWhat(r.what, exitName);
      f.listText = t("ep.changedList", { g: groupLabel(gid), what: what ? ": " + what : "" });
    }
    return r;
  }

  // ── записи ──────────────────────────────────────────────────────────────────────────────────────────────────────
  const reread = () => K.loadAll(["policy", "exits", "overview"]);
  // тело PUT /api/gw/policy: сохранённая политика, в ней — только изменённая группа (другие группы не трогаются)
  function policyBody(gid) {
    const p = pol(), f = forms[gid];
    const doc = copy(p.policy);
    doc.groups = doc.groups.map((g) => (g.id === gid ? stripLabels(copy(f.draft)) : stripLabels(g)));
    return { policy: doc, rev: f.rev };
  }
  function stripLabels(g) { delete g.label; (g.tiers || []).forEach((tr) => delete tr.label); return g; }
  const impact = (extra) => (res) => Kit.impactLines(res, { group: groupLabel, warn: (c) => (has("ep.warn." + c) ? t("ep.warn." + c) : ""), extra });

  // ── кусочки интерфейса ─────────────────────────────────────────────────────────────────────────────────────────
  const STATE_TONE = { ok: "success", degraded: "warning", standby: "default", off: "danger", unknown: "default", nodata: "default" };
  const stateChip = (r) => {
    const c = U.chip(tk("ep.st.", r.pstate || "unknown"), STATE_TONE[r.pstate] || "default");
    return r.pstate === "standby" ? infoPop(c, t("ep.standbyInfoT"), t("ep.standbyInfo"), null, "gx-pop--l") : c;
  };
  // Подсказка-окошко (макет it1 Д3: gx-ib / gx-pop): видна на наведении и фокусе, нажатие закрепляет (касание), Esc —
  // закрыть. trig — кнопка ⓘ или метка (метке даётся tabindex=0 и role=button); title — заголовок, text — строка, list —
  // [[название, текст]]
  function infoPop(trig, title, text, list, cls) {
    const pop = el("div", { class: "gx-pop" + (cls ? " " + cls : ""), role: "tooltip", id: "gx-pop-" + (++popSeq) },
      title ? el("span", { class: "gx-pop__t", text: title }) : null, text ? el("span", { text }) : null,
      list ? el("ul", { class: "gx-pop__l" }, ...list.map((x) => el("li", null, el("b", { text: x[0] }), el("span", { text: x[1] })))) : null);
    if (trig.tagName !== "BUTTON") { trig.setAttribute("tabindex", "0"); trig.setAttribute("role", "button"); }
    trig.setAttribute("aria-describedby", pop.id);
    trig.setAttribute("aria-expanded", "false");
    const w = el("span", { class: "gx-popw" }, trig, pop);
    const set = (on) => { w.classList.toggle("is-pop", on); trig.setAttribute("aria-expanded", String(on)); };
    trig.addEventListener("click", (ev) => { ev.stopPropagation(); set(!w.classList.contains("is-pop")); });
    trig.addEventListener("keydown", (ev) => { if (ev.key === "Escape") set(false); else if ((ev.key === "Enter" || ev.key === " ") && trig.tagName !== "BUTTON") { ev.preventDefault(); set(!w.classList.contains("is-pop")); } });
    trig.addEventListener("blur", () => set(false));
    return w;
  }
  let popSeq = 0;
  // ⓘ у «Способ выбора»: общая строка и по строке на способ (тексты сверены с ядром, R-it1-logic «Тексты для Claude Design»);
  // «По порядку (вручную)» — флаг уровня поверх способа (PLAN-order §3)
  function methodInfo() {
    const b = el("button", { type: "button", class: "gx-ib", "aria-label": t("ep.methodInfoL") }, K.icon("circle-info", 14));
    return infoPop(b, "", t("ep.methodInfo"), METHODS.concat(["manual"]).map((m) => [t("ep.method." + m), t("ep.methodInfo." + m)]));
  }
  // ряды «силы» групп из живых данных — в ключе перерисовки «Групп» (PLAN-order Ш5)
  const ranksSig = () => { const lg = K.liveMark ? K.liveMark("groups") : null; return lg ? JSON.stringify(Object.keys(lg).map((k) => [k, (lg[k] && lg[k].ranks) || null])) : ""; };
  function swLive(title, desc, get, set, o = {}) {
    const b = U.sw(title, desc, get(), null, o);
    b.addEventListener("click", () => { if (b.disabled) return; const v = !get(); set(v); b.setAttribute("aria-checked", String(v)); });
    return b;
  }
  function radioLive(items, get, set, o = {}) {
    const n = U.radio(items.map((it) => ({ title: it.title, desc: it.desc, note: it.note, chips: it.chips, on: get() === it.v, dis: it.dis, pick: () => {} })), o);
    Array.from(n.children).forEach((b, i) => b.addEventListener("click", () => {
      if (items[i].dis) return;
      set(items[i].v);
      Array.from(n.children).forEach((x, j) => x.setAttribute("aria-checked", String(i === j)));
    }));
    return n;
  }
  const sub = (title, text) => el("div", { class: "kk-eg-sub" }, el("h3", { class: "kk-eg-h3", text: title }), text ? el("span", { class: "gx-desc", text }) : null);
  const numIn = (value, min, max, aria, onSet) => {
    const i = U.input({ value, aria, type: "number", num: true, im: "numeric" });
    i.min = String(min); i.max = String(max);
    i.addEventListener("input", () => { const v = Number(i.value); const ok = i.value !== "" && v >= min && v <= max; i.setAttribute("aria-invalid", ok ? "false" : "true"); onSet(ok ? v : null); });
    return i;
  };

  // ── «Включить «Стандарт»» и сброс — окно со строкой «что изменится» (SE §7.1, §7.9 reset) ─────────────────────
  // у группы несохранённые правки — «Включить «Стандарт»» неактивна и в окне (SE §7: кнопки «сразу» над объектом формы)
  function presetDialog(gid, preset) {
    if (unsavedWhy(gid)) return;
    let ca = null;
    const d = K.openDialog({ icon: "shield-check", title: t("ep.stdTitle", { g: groupLabel(gid) }), blocks: [U.p(t("ep.stdText"))], onClose: () => { if (ca) actionsLive.delete(ca); } });
    ca = track(Kit.confirmAction({ label: t("ep.stdBtn"), anywayLabel: t("ep.stdBtnLow"), kind: "tertiary", md: true,
      dis: () => unsavedWhy(gid), readRev, reread, dry: () => K.API.policy.reset({ preset, group: gid }, true),
      run: (rev) => K.API.policy.reset({ preset, group: gid, rev }), impact: impact([t("ep.stdLine")]),
      onDone: (res, how) => { reread(); if (how === "ok") { d.close(); K.toast(t("ep.stdDone"), "", "success"); } } }));
    d.body.append(ca.el);
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true })]);
    ca.open();
  }
  // «Оставить как раньше»: только флаг плашки (POST /api/gw/policy/hint-seen, вне версии — как имена) — без записи
  // политики и без применения шлюза (REV-s1-t8-a З-8); повторное нажатие, пока запрос в пути, ничего не шлёт
  function hintDismiss(ev, b) {
    if (!b || b.disabled) return;
    b.disabled = true; b.setAttribute("aria-busy", "true");
    K.API.policy.hintSeen().then(() => reread(), (e) => {
      b.disabled = false; b.removeAttribute("aria-busy");
      K.toast(t("kit.failed"), (e && e.message) || "", "danger");
    });
  }
  // «Включить «Стандарт»» (плашка и карточка «Как раньше»): неактивна, пока у группы несохранённые правки
  function stdBtn(gid, kind, acc) {
    const b = U.btn(t("ep.stdBtn"), kind, () => presetDialog(gid, "standard"));
    b.dataset.egStd = gid;
    if (acc) b.dataset.egAcc = acc;
    const w = unsavedWhy(gid); b.disabled = !!w; b.title = w;
    return b;
  }

  // ── «Разложить заново» (F14; Claude Design «Разложить заново» r1, HANDOFF-regroup) ──────────────────────────────────
  // Ночная перебалансировка сейчас, для одной группы. Кнопка — справа в строке «Уровни» (на узкой части — под
  // пояснением); подтверждение — на месте под строкой (телефон — лист снизу): «сейчас / после» полосками по выходам,
  // итог и «кто куда» из прогона POST …/regroup {dry: true} (ничего не пишет), строка сервера, «Отмена · Разложить».
  // «Разложить» — запрос сборщику: ответ сразу {accepted}, итог — событием group_regrouped ленты (тост — ядро), затем
  // выходы перечитываются; изменившиеся числа «Устройств» — подложкой kk-rg-upd до следующего чтения выходов. Отказы,
  // которые панель знает до нажатия, — кнопка aria-disabled (остаётся в фокусе) и причина строкой; остальные — 409
  // после нажатия той же строкой (кнопка доступна: когда условие пройдёт, панель не знает).
  const RG_ROWS = 3;                       // строк «кто куда» до «Показать ещё» (сервер отдаёт до 50)
  const rg = {};                           // gid -> {open, busy, res, err, blind, more, acc, snap, refused, sheet, seq}
  const rgUpd = {};                        // gid -> {data: K.R.exits.data при подсветке, ids: {exit: true}}
  const rgOf = (gid) => rg[gid] || (rg[gid] = { seq: 0 });
  const rgGroup = (gid) => (forms[gid] ? forms[gid].base : groupOf(gid));
  // причина, по которой раскладывать нельзя, — по сохранённой группе (сервер раскладывает по сохранённым правилам)
  function rgWhy(gid) {
    const g = rgGroup(gid);
    if (!g) return "";
    if (perConn(gid, g)) return t("ep.rg.no.conn");
    if (K.R.gw && K.R.gw.data && K.R.gw.data.enabled === false) return t("ep.rg.no.off");
    const pl = K.liveMark ? K.liveMark("policy") : null;
    if (pl && pl.node_offline) return t("ep.rg.no.offline");
    if ((g.role || "abroad") === "ru" && pl && pl.node_ru_offline) return t("ep.rg.no.ru");
    return unsavedWhy(gid);
  }
  const rgUpdOf = (gid, id) => { const u = rgUpd[gid]; return !!u && u.data === (K.R.exits && K.R.exits.data) && !!u.ids[id]; };
  // кто: «Пользователь — устройство» по людям ядра (R.people раздела «Выходы»), иначе имя устройства из ответа
  function rgWho(m) {
    const p = K.R.people && K.R.people.loaded && K.R.people.data ? K.R.people.data : null;
    const d = p && Array.isArray(p.devices) ? p.devices.find((x) => x.id === m.device) : null;
    const u = d && Array.isArray(p.users) ? p.users.find((x) => x.id === d.user) : null;
    const dev = (d && d.name) || m.name || m.device || "—";
    return u && u.name ? t("ep.rg.move", { who: u.name, device: dev }) : t("ep.rg.moveOne", { device: dev });
  }
  // выходы полосок — в порядке уровней группы, только где устройства есть сейчас или будут; пока считаем — все выходы
  function rgExits(gid, res) {
    const all = exitsOfG(rgGroup(gid));
    if (!res) return all;
    const b = res.before || {}, a = res.after || {};
    const ids = all.filter((id) => (b[id] || 0) + (a[id] || 0) > 0);
    Object.keys(b).concat(Object.keys(a)).forEach((id) => { if (ids.indexOf(id) < 0) ids.push(id); });
    return ids;
  }
  // полоски «сейчас / после»: шкала одна на группу — максимум из всех чисел (HANDOFF, ответ 3)
  function rgBars(gid, res) {
    const ids = rgExits(gid, res), b = (res && res.before) || {}, a = (res && res.after) || {};
    const max = Math.max(1, ...ids.map((id) => Math.max(b[id] || 0, a[id] || 0)));
    const bar = (n, now) => {
      const fill = el("i");
      if (res && n && fill.style) fill.style.width = Math.round((n / max) * 100) + "%";      // CSSOM — CSP это пускает
      return el("span", { class: "kk-rg__bar" + (now ? " kk-rg__bar--now" : "") + (!res || !n ? " is-empty" : "") }, fill);
    };
    return el("div", { class: "kk-rg__g" }, el("div", { class: "kk-rg__bars" },
      el("span", { class: "kk-rg__lg", "aria-hidden": "true" }, el("span", { class: "is-now", text: t("ep.rg.now") }), el("span", { text: t("ep.rg.after") })),
      ids.map((id) => el("div", { class: "kk-rg__row" }, el("span", { class: "kk-rg__n", title: exitName(id), text: exitName(id) }),
        el("span", { class: "kk-rg__pair", "aria-hidden": "true" }, bar(b[id] || 0, true), bar(a[id] || 0, false)),
        res ? el("span", { class: "kk-rg__num" }, (b[id] || 0) + " → ", el("b", { text: String(a[id] || 0) })) : el("span", { class: "kk-rg__num", text: "—" })))));
  }
  // содержимое подтверждения по состоянию: считаем · не удалось (и «без предпросмотра») · переедут · некому
  function rgBox(gid) {
    const s = rgOf(gid);
    const cancel = U.btn(t("c.cancel"), "tertiary", () => rgClose(gid, true));
    const go = (kind, dis) => { const b = U.btn(t("ep.rg.go"), kind, () => rgGo(gid)); b.dataset.rgGo = gid; if (dis) b.setAttribute("aria-disabled", "true"); return b; };
    const box = el("div", { class: "kk-rg" + (s.busy ? " is-busy" : ""), role: "group", "aria-label": t("ep.rg.aria", { g: groupLabel(gid) }), "data-rg-box": gid });
    const foot = (...kids) => el("div", { class: "kk-rg__foot" }, ...kids);
    if (s.busy) {
      box.append(rgBars(gid, null), el("div", { class: "kk-rg__wait", role: "status" }, el("span", { class: "gx-spin gx-spin--sm", "aria-hidden": "true" }), t("ep.rg.wait")),
        foot(cancel, go("primary", true)));
    } else if (s.err) {
      // прогон не удался: «Повторить»; разложить всё равно — вторым подтверждением (кто переедет — неизвестно)
      box.append(U.al("danger", t("ep.rg.err"), s.err, { row: true, btns: [U.btn(t("kit.retry"), "tertiary", () => rgPreview(gid))] }),
        s.blind ? foot(el("span", { class: "kk-rg__q", text: t("ep.rg.blindQ") }), cancel, go("primary"))
          : foot(cancel, U.btn(t("ep.rg.blind"), "tertiary", () => { s.blind = true; rgPaint(gid); })));
    } else if (s.res) {
      const r = s.res, moved = Number(r.moved) || 0;
      const moves = (r.moves || []).filter((m) => m && m.why === "rebalance");      // «кто куда» — те, что в «Переедут»
      const shown = s.more ? moves : moves.slice(0, RG_ROWS);
      const list = moves.length ? el("ul", { class: "kk-rg__list" }, shown.map((m) => el("li", {}, el("span", { class: "kk-rg__who", text: rgWho(m) }),
        el("span", { class: "kk-rg__mv", text: t("ep.rg.path", { from: m.from ? exitName(m.from) : "—", to: m.to ? exitName(m.to) : "—" }) })))) : null;
      const more = moves.length > shown.length ? U.btn(t("ep.moreTrans"), "link", () => { s.more = true; rgPaint(gid); }) : null;
      const sum = el("p", { class: "kk-rg__sum" + (moved ? "" : " is-none"), text: String(r.text || "") });
      const also = r.also ? el("p", { class: "kk-rg__also", text: t("ep.rg.also", { n: K.fmtInt(r.also) }) }) : null;
      // некому — одна строка без полосок, «Разложить» не акцентом (предпросмотр примерный, повтор безвреден)
      if (!moved) box.append(sum, also, foot(cancel, go("tertiary")));
      else box.append(rgBars(gid, r), el("div", { class: "kk-rg__t" }, sum, also, list, more, r.note ? el("p", { class: "kk-rg__note", text: String(r.note) }) : null),
        foot(cancel, go("primary")));
    }
    return box;
  }
  // строка «Уровни» с кнопкой и под ней — подтверждение («Запрос принят…» после нажатия); телефон — лист (rgPaint)
  function rgHead(gid, subEl) {
    const s = rgOf(gid), why = rgWhy(gid), line = why || s.refused || "", whyId = "eg-rgw-" + gid;
    const b = U.btn(t("ep.rg.btn"), "tertiary", () => rgToggle(gid), { icon: "arrow-rotate-right" });
    b.dataset.rg = gid;
    b.setAttribute("aria-expanded", String(!!s.open && !why));
    if (why) b.setAttribute("aria-disabled", "true");
    if (line) b.setAttribute("aria-describedby", whyId);
    if (s.acc) b.setAttribute("aria-busy", "true");
    const row = el("div", { class: "kk-rg-h" }, subEl, b, line ? el("p", { class: "kk-rg-why", id: whyId }, K.icon("circle-info", 14), el("span", { text: line })) : null);
    const acc = s.acc ? el("div", { class: "kk-rg-acc", role: "status" }, el("span", { class: "gx-spin gx-spin--sm", "aria-hidden": "true" }), el("span", { text: s.acc })) : null;
    return [row, acc || (s.open && !why && !K.isPhone() ? rgBox(gid) : null)];
  }
  // перерисовать подтверждение: компьютер — карточку (фокус возвращается на кнопку или «Разложить»), телефон — лист
  function rgPaint(gid, focusSel) {
    const s = rgOf(gid);
    if (s.sheet) {
      const box = rgBox(gid);
      s.sheet.body.replaceChildren(box);
      if (focusSel) { const n = box.querySelector(focusSel); if (n) try { n.focus({ preventScroll: true }); } catch (e) { /* узел ушёл */ } }
      return;
    }
    const f = forms[gid];
    if (!f || !f.card) return;
    // фокус был в карточке (нажали кнопку, «Отмена», «Повторить») — туда же или на focusSel; иначе не трогаем
    const had = document.activeElement && f.card.contains(document.activeElement) ? (focusSel || '[data-rg="' + CSS.escape(gid) + '"]') : null;
    renderCard(gid);
    if (had) focusIn(f.card, had);
  }
  function rgClose(gid, back) {
    const s = rgOf(gid);
    s.open = false; s.seq += 1; s.busy = false;
    const sh = s.sheet; s.sheet = null;
    if (sh) sh.close(null, true);
    rgPaint(gid, back ? '[data-rg="' + CSS.escape(gid) + '"]' : null);
  }
  function rgToggle(gid) {
    const s = rgOf(gid);
    if (rgWhy(gid) || s.acc) return;                       // недоступна (причина строкой) или запрос уже принят
    if (s.open) { rgClose(gid, true); return; }
    s.open = true; s.refused = "";
    if (K.isPhone() && K.openBottom) {
      const g = rgGroup(gid);
      // подпись листа — как в макете (кадр Р2т): уровни · ключи · устройства, без «выбрали: …» (длинное имя группы её
      // вытесняло)
      s.sheet = K.openBottom({ title: t("ep.rg.title", { g: groupLabel(gid) }), sub: g ? ((g.role || "abroad") === "ru" ? ruMeta(g, true) : groupMeta(g)) : "",
        onClose: () => { if (s.sheet) { s.sheet = null; s.open = false; s.seq += 1; s.busy = false; rgPaint(gid); } } });
    }
    rgPreview(gid);
  }
  // 409 (раскладка заморожена, шлюз выключен, по соединениям — то, чего панель не знала) — строкой под кнопкой;
  // 404 — группы уже нет; остальное — в подтверждении с «Повторить»
  function rgFail(gid, e, preview) {
    const s = rgOf(gid);
    if (e && e.status === 409) { s.refused = e.message || ""; rgClose(gid, true); return; }
    if (Kit.errKind(e) === "gone") { rgClose(gid); K.toast(t("kit.goneElse"), "", "danger"); reread(); return; }
    if (preview) { s.err = (e && e.message) || ""; rgPaint(gid); return; }
    K.toast((e && e.message) || t("ep.rg.err"), "", "danger");
    rgPaint(gid, "[data-rg-go]");
  }
  function rgPreview(gid) {
    const s = rgOf(gid), seq = ++s.seq;
    Object.assign(s, { busy: true, res: null, err: "", blind: false, more: false });
    rgPaint(gid, "[data-rg-go]");
    return K.API.groups.regroup(gid, { dry: true }).then((res) => {
      if (s.seq !== seq || !s.open) return;
      s.busy = false; s.res = res || {};
      rgPaint(gid, "[data-rg-go]");
    }, (e) => { if (s.seq !== seq || !s.open) return; s.busy = false; rgFail(gid, e, true); });
  }
  function rgGo(gid) {
    const s = rgOf(gid);
    if (s.busy || s.sending || !s.open) return null;
    s.sending = true;
    const snap = {};
    rgExits(gid, null).forEach((id) => { const r = rowOf(id); snap[id] = r && typeof r.devices === "number" ? r.devices : null; });
    return K.API.groups.regroup(gid, {}).then((res) => {
      s.sending = false; s.snap = snap; s.acc = String((res && res.text) || "");
      const wait = Math.max(30, Number(res && res.wait_s) || 60);
      clearTimeout(s.accTimer);
      // события не было дольше двух сроков (сборщик стоит — придёт «ждал дольше 5 мин») — строка уходит сама
      s.accTimer = setTimeout(() => { if (s.acc) { s.acc = null; rgPaint(gid); } }, wait * 2000);
      rgClose(gid, true);
    }, (e) => { s.sending = false; rgFail(gid, e, false); });
  }
  // итог — событие ленты group_regrouped своей группы: тост показало ядро; перечитать выходы и подсветить числа,
  // которые изменились с момента «Разложить»
  if (K.alertSub) K.alertSub((o) => {
    const a = (o && o.kind === "group_regrouped" && o.args) || null, gid = a && a.group, s = gid ? rg[gid] : null;
    if (!s || !s.acc) return;
    s.acc = null; clearTimeout(s.accTimer);
    const snap = s.snap || {};
    reread().then(() => {
      const ids = {};
      Object.keys(snap).forEach((id) => { const r = rowOf(id), n = r && typeof r.devices === "number" ? r.devices : null; if (n !== null && snap[id] !== null && n !== snap[id]) ids[id] = true; });
      rgUpd[gid] = { data: K.R.exits && K.R.exits.data, ids };
      rgPaint(gid);
    }, () => rgPaint(gid));
  });

  // ── карточка группы ─────────────────────────────────────────────────────────────────────────────────────────────
  function nowLine(g) {
    // «сейчас: уровень N» — первый уровень с годным или ухудшенным выходом; нет — последний шаг (fail)
    const i = g.tiers.findIndex((tr) => tr.exits.some((id) => { const r = rowOf(id); return r && r.enabled && (r.pstate === "ok" || r.pstate === "degraded"); }));
    if (i >= 0) return U.chip(t("ep.nowTier", { n: tierLabel(g.tiers[i], i) }), "success");
    if (!g.tiers.some((tr) => tr.exits.length)) return null;
    // «… — с ЧЧ:ММ» (не сегодня — с датой, REV-s1-t8m П-3): последняя смена состояния среди выходов группы (с неё не
    // осталось ни одного годного)
    let since = 0;
    g.tiers.forEach((tr) => tr.exits.forEach((id) => { const r = rowOf(id); if (r && typeof r.since === "number" && r.since > since) since = r.since; }));
    const direct = g.fail === "direct";
    return U.chip(since ? t(direct ? "ep.nowDirectSince" : "ep.nowBlockSince", { t: K.fmtTs(since) }) : t(direct ? "ep.nowDirect" : "ep.nowBlock"), "danger");
  }
  function devicesIn(g) { let n = 0; g.tiers.forEach((tr) => tr.exits.forEach((id) => { const r = rowOf(id); if (r && r.devices) n += r.devices; })); return n; }
  function countExits(g) { return g.tiers.reduce((s, tr) => s + tr.exits.length, 0); }

  // Шапка свёрнутой строки (UI-A2; HANDOFF r3: gx-xc строкой 64): кнопка с именем растянута на всю шапку (::after) —
  // нажатие по свободному месту шапки раскрывает, Enter / пробел — сама кнопка (aria-expanded, aria-controls); кнопки
  // в шапке (карандаш имени) — поверх растяжки. key — ряд, по которому живая перерисовка возвращает фокус (rowFocus ядра).
  // lead — свой значок слева вместо o.icon (внутренняя группа — роутер, K.domMark)
  function foldHead(o) {
    const tog = el("button", { type: "button", class: "kk-eg-tog", "aria-expanded": String(!!o.open), "aria-controls": o.controls, title: o.title, text: o.title });
    tog.addEventListener("click", o.onToggle);
    return el("div", { class: "kk-eg-head", "data-hold-row": o.key },
      o.lead || el("span", { class: "kk-eg-head__ic" }, K.icon(o.icon, 18)),
      el("div", { class: "kk-eg-head__b" }, el("div", { class: "kk-eg-head__t" }, el("h2", { class: "kk-eg-head__n" }, tog), ...(o.after || [])),
        o.meta ? el("div", { class: "kk-eg-head__m", text: o.meta }) : null),
      el("div", { class: "kk-eg-head__s" }, ...(o.state || []).filter(Boolean), o.menu || null, el("span", { class: "kk-eg-chev", "aria-hidden": "true" }, K.icon("chevron-down", 16))));
  }
  // «⋯» шапки группы (круг 1 №29, макет it1 Д3 Г3): «Переименовать · Удалить группу…»; кнопки удаления внизу карточки нет.
  // «Основную» не удалить (ядро: 400 main) — у неё только «Переименовать»
  // data-eg-del — у групп, которые можно удалить: проверки находят по нему меню группы
  function groupMenu(gid) {
    const b = U.menu([{ label: t("kit.rename"), icon: "pencil", on: () => renameGroup(gid) },
      gid !== "main" ? { label: t("ep.delGroupMenu"), icon: "trash-bin", danger: true, sep: true, on: () => delGroupDialog(gid) } : null].filter(Boolean),
    { label: t("ep.groupMenuOf", { g: groupLabel(gid) }), aria: t("ep.groupMenuOf", { g: groupLabel(gid) }), cls: "kk-eg-pen kk-gmw" });
    if (gid !== "main") b.setAttribute("data-eg-del", gid);
    return b;
  }
  // после раскрытия или свёртки карточка собрана заново — фокус на её новую кнопку раскрытия (sel), а не в body
  function focusIn(card, sel) { const b = card && card.isConnected ? card.querySelector(sel) : null; if (b) try { b.focus({ preventScroll: true }); } catch (e) { /* узел ушёл */ } }
  // «2 уровня · 3 выхода · 10 устройств» — по черновику (как было «Выходов: N · устройств: M»)
  const groupMeta = (g) => [K.tn("ep.tiersN", g.tiers.length), K.tn("ep.exitsN", countExits(g)), K.tn("ep.devicesN", devicesIn(g))].join(" · ");
  // у российской — «ключи» и кто её выбрал: «1 уровень · 2 ключа · 2 устройства · выбрали: 2 пользователя»
  const ruMeta = (g, noWho) => { const w = ruWho(g.id); return [K.tn("ep.tiersN", g.tiers.length), K.tn("ep.ru.keysN", countExits(g)), K.tn("ep.devicesN", devicesIn(g)),
    !noWho && w.known && (w.u || w.d) ? t("ep.ru.whoMeta", { w: ruWhoText(w) }) : ""].filter(Boolean).join(" · "); };
  // имя группы — окном (Kit.renameDialog): строка перерисовывается живыми данными, правка на месте пропадала бы
  function renameGroup(gid) {
    const g = groupOf(gid) || (forms[gid] ? forms[gid].draft : null);
    if (!g) return;
    Kit.renameDialog({ title: t("ep.groupName"), value: g.name || "", display: groupLabel(gid), empty: gid === "main", aria: t("ep.groupName"),
      reset: gid === "main" ? { label: t("ep.groupNameDefault") } : null,          // RN §4.1: запись "" — имя по умолчанию
      save: (v, dup) => K.API.groups.rename(gid, { name: v, allow_duplicate: dup || undefined }).then((r) => { reread(); return r; }, (e) => { if (Kit.errKind(e) === "gone") { reread(); throw new Error(t("kit.goneElse")); } throw e; }) });
  }
  function groupCard(gid) {
    const f = forms[gid];
    const card = el("section", { class: "gx-card kk-eg-card", "data-find": "exits.group." + gid, "aria-label": groupLabel(gid) });
    f.card = card;
    renderCard(gid);
    return card;
  }
  function renderCard(gid) {
    const f = forms[gid], g = f.draft, card = f.card, p = pol();
    if (!card) return;
    const isOpen = open[gid] === true;
    // российская группа (решение 10 — только в «Российских группах»; РФ-группы Ш1, владелец 28.10 «как у выходов») —
    // та же карточка: шапка со значком роутера, «сейчас: …» по её ключам, в теле — те же блоки и её факты (ruFacts)
    const ru = (g.role || "abroad") === "ru";
    const chips = [];
    if (p && p.policy.new_exits_group === gid) chips.push(U.chip(t("ep.defaultGroup"), "accent"));
    if (p && p.policy.tg_proxy_group === gid) chips.push(U.chip(t("ep.tgProxy"), "default"));
    const bodyId = "eg-b-" + gid;
    const shown = isOpen;
    card.classList.toggle("kk-eg-card--ru", ru);
    const head = foldHead({ key: "egh:" + gid, icon: "pool", lead: ru ? K.domMark(18, "kk-eg-head__ic kk-eg-head__ic--rf") : null,
      title: groupLabel(gid), open: isOpen, controls: bodyId, after: chips, meta: ru ? ruMeta(g) : groupMeta(g),
      state: [ru ? ruNowChip(f.base, ruNow(f.base)) : nowLine(f.base)], menu: groupMenu(gid), onToggle: () => { open[gid] = !isOpen; renderCard(gid); focusIn(card, ".kk-eg-tog"); } });
    card.classList.toggle("is-open", shown && !f.gone);
    const kids = [head];
    // группу с правками удалили в другом месте: карточка остаётся с «Уже удалено в другом месте» до «Закрыть» (REV-s1-t8-a П-4)
    if (f.gone) kids.push(el("div", { class: "kk-eg-body" }, U.al("danger", t("kit.goneElse"), "", { btns: [U.btn(t("kit.close"), "tertiary", () => { delete forms[gid]; renderAll(); })] })));
    if (!f.sb) f.sb = Kit.saveBar({ count: () => Kit.dirtyKeys(f.base, f.draft, KEYS).length, valid: () => validGroup(f.draft),
      // ответ прогона с rev ≠ base_rev — перекладка (SE §7): перечитать и сравнить объект формы
      onDry: () => K.API.policy.put(policyBody(gid), true).then((res) => { if (res && res.rev !== undefined && res.rev !== null && res.rev !== f.rev) K.loadAll(["policy", "exits"]).then(() => { syncForms(); renderCard(gid); }); return res; }),
      // строки, известные заранее, — по черновику на момент прогона (не на момент сборки полосы)
      impact: (res) => impact([freqChanged(f) ? t("ep.xrayRestart") : "", f.draft.fail === "direct" && f.base.fail !== "direct" ? t("ep.warn.fail_direct") : ""].concat(ordImpact(f)).filter(Boolean))(res),
      onSave: () => K.API.policy.put(policyBody(gid)),
      onCancel: () => { f.draft = copy(f.base); f.list = []; renderCard(gid); },
      onSaved: (res, how) => { if (how === "ok") { f.base = copy(f.draft); f.list = []; refreshButtons(); K.toast(t("ep.saved", { g: groupLabel(gid) }), "", "success"); } reread().then(() => { syncForms(); renderAll(); }); },
      onConflict: () => K.loadAll(["policy", "exits"]).then(() => { const r = rebase(gid, groupOf(gid), readRev()); renderCard(gid); return { mode: r.mode, listText: f.listText }; }),
      list: () => f.list.map((ed) => ({ text: t("ep.editLine", { what: tk("ep.key.", ed.key.replace(".", "_")) }), apply: () => { f.draft = Kit.applyAgain(f.draft, ed); f.list = f.list.filter((x) => x !== ed); edited(gid); } })) });
    // тело — на всю ширину под шапкой: первый уровень (что делать без выходов, набор, уровни, условия), второй —
    // «Поведение и проверки», удаление и полоса «Сохранить»; свёрнутая — пустое скрытое тело (цель aria-controls)
    kids.push(shown && !f.gone ? el("div", { class: "kk-eg-body", id: bodyId }, failRow(gid), presetBlock(gid), tiersBlock(gid), ru ? ruFacts(gid) : null, rulesBlock(gid), moreBlock(gid), f.sb.el)
      : f.gone ? null : el("div", { class: "kk-eg-body", id: bodyId, hidden: true }));
    card.replaceChildren(...kids.filter(Boolean));
    if (f.list.length) f.sb.setList(f.listText); else f.sb.refresh();
    refreshButtons();
  }
  // правка формы, после которой карточка собирается заново: прежний «что изменится» устарел
  const edited = (gid) => { renderCard(gid); const f = forms[gid]; if (f && f.sb && !f.list.length) f.sb.update(); };
  const changed = (gid) => { const f = forms[gid]; if (f && f.sb) f.sb.update(); refreshButtons(); };
  const freqChanged = (f) => !same(f.base.checks.freq, f.draft.checks.freq);
  // «Свой интервал» — 10–600 с (policy._validate); поле этого числа — во втором уровне группы
  const freqValid = (g) => { const fr = g.checks && g.checks.freq; return !(fr && fr.mode === "custom" && !(fr.every_s >= 10 && fr.every_s <= 600)); };
  // «Куда встают новые»: last | first | id уровня группы (policy._validate ep.err.new_tier)
  const newTierValid = (g) => { const nt = g.new_exits_tier || "last"; return nt === "last" || nt === "first" || g.tiers.some((tr) => tr.id === nt); };
  function validGroup(g) {
    if (!freqValid(g)) return false;
    if (!newTierValid(g)) return false;
    if (g.return_to_primary && g.return_to_primary.enabled && !(g.return_to_primary.hold_min >= 1 && g.return_to_primary.hold_min <= 120)) return false;
    if ((g.rules || []).some((r) => Object.keys(ruleErrors(r, g)).length)) return false;
    if (Object.keys(groupRuleErrors(g)).length) return false;
    return true;
  }
  // «Свой»: правка поля пресета превращает набор в «Свой» (§1.3)
  function touchPreset(g) { if (g.preset !== "custom" && g.preset !== "compat") g.preset = "custom"; else if (g.preset === "compat") g.preset = "custom"; }

  function failRow(gid) {
    const g = forms[gid].draft;
    const warn = el("div", { class: "gx-hint kk-eg-warn", text: g.fail === "direct" ? t("ep.failDirectWarn") : "" });
    // строка про адреса сайтов (Р-Г9): одной строкой + «Подробнее» — полный текст (UX-аудит 20.10: абзац ~400 знаков)
    const dns = el("div", { class: "kk-eg-dns", "data-eg-dns": gid });
    const drawDns = (focus) => {
      const full = dnsLine(gid, g), id = "eg-dns-" + gid;
      dns.hidden = !full;
      if (!full) { dns.replaceChildren(); return; }
      const on = !!DNS_OPEN[gid];
      const more = U.btn(t(on ? "ep.collapse" : "kit.more"), "link", () => { DNS_OPEN[gid] = !on; drawDns(true); });
      more.setAttribute("aria-expanded", String(on)); more.setAttribute("aria-controls", id);
      dns.replaceChildren(el("div", { class: "kk-eg-dns__l" }, el("span", { class: "gx-hint", text: dnsShort(gid) }), more),
        el("div", { class: "gx-hint", id, hidden: !on, text: full }));
      if (focus) try { more.focus({ preventScroll: true }); } catch (e) { /* узел ушёл */ }
    };
    const sel = U.select([{ v: "block", l: t("ep.failBlock") }, { v: "direct", l: t("ep.failDirect") }], g.fail, (v) => { g.fail = v; warn.textContent = v === "direct" ? t("ep.failDirectWarn") : ""; drawDns(); changed(gid); }, { aria: t("ep.failLabel") });
    drawDns();
    return el("div", { class: "kk-eg-row" }, el("span", { class: "kk-eg-l", text: t("ep.failLabel") }), sel, warn, dns);
  }
  // Р-Г9 (SPEC-stage1-exit-groups §5): адреса сайтов узел узнаёт через «Основную» (один DNS на всех, gw-all); она лежит
  // и её последний шаг — блок — gw-all берёт лучший выход следующей зарубежной группы (assign.gw_all), и только когда
  // недоступны и они — DNS молчит: российские сайты по имени не открываются даже при «Россия напрямую» и у людей
  // российских групп; кроме xray-устройств российской группы с прокси-выходом — им имя разрешает роутер (§4.4).
  // Строка — у «Основной» (по её черновику) и у российской группы (по сохранённой «Основной»)
  function dnsLine(gid, g) {
    if (gid === "main") return g.fail === "block" ? t("ep.dnsMainBlock") : "";
    if ((g.role || "abroad") !== "ru") return "";
    const m = groupOf("main");
    return m && m.fail === "block" ? t("ep.dnsRuGroup", { g: groupLabel("main") }) : "";
  }
  // та же мысль одной строкой (полный текст — по «Подробнее»): у «Основной» — про неё, у российской — про «Основную»
  const dnsShort = (gid) => (gid === "main" ? t("ep.dnsMainShort") : t("ep.dnsRuShort", { g: groupLabel("main") }));
  function presetBlock(gid) {
    const f = forms[gid], g = f.draft, p = pol(), ru = (g.role || "abroad") === "ru";
    const fc = p && p.forecast && p.forecast.probe_mb_day ? p.forecast.probe_mb_day[gid] : null;
    const pick = (v) => {
      const src = p && p.presets && p.presets[ru ? "ru" : "abroad"] ? p.presets[ru ? "ru" : "abroad"][v] : null;
      if (src) applyPreset(g, src);
      g.preset = v;
      edited(gid);
    };
    const items = (ru ? RU_PRESETS : PRESETS).map((v) => ({ v, title: t("ep.preset." + v), desc: t("ep.presetD." + v), note: v === g.preset && fc !== null && fc !== undefined ? t("ep.probeDay", { n: K.fmtInt(fc) }) : "", dis: v === "custom" && g.preset !== "custom" }));
    const list = radioLive(items, () => g.preset, pick, { aria: t("ep.presetLabel") });
    list.classList.add("kk-eg-presets");
    if (ru) { list.classList.add("kk-eg-presets--ru"); return el("div", { class: "kk-eg-blk" }, sub(t("ep.presetLabel"), t("ep.presetTextRu")), list); }
    const compat = el("div", { class: "kk-eg-compat" + (g.preset === "compat" ? " is-on" : "") },
      el("div", { class: "kk-eg-compat__t" }, el("b", { text: t("ep.preset.compat") }), el("span", { class: "gx-desc", text: t("ep.presetD.compat") })),
      g.preset === "compat" ? stdBtn(gid, "tertiary") : U.btn(t("ep.compatPick"), "ghost", () => pick("compat")));
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.presetLabel"), t("ep.presetText")), list, compat);
  }
  function applyPreset(g, src) {
    PRESET_KEYS.forEach((k) => { if (src[k] !== undefined) g[k] = copy(src[k]); });
    const c = src.checks || src;
    if (c.freq) g.checks.freq = copy(c.freq);
    if (c.services) g.checks.services = copy(c.services);
    if (c.geo) g.checks.geo = copy(c.geo);
    if (c.speed && c.speed.enabled !== undefined) g.checks.speed.enabled = !!c.speed.enabled;
  }

  // Уровни: упорядоченный список «в форме» (↑ ↓ и выбор уровня; без перетаскивания на 390 px)
  // Способы выбора уровня: у российской группы «По скорости» — только при замере скорости группы (без замера он как
  // «Поровну», assign._capacity_w); уже сохранённый «По скорости» остаётся в списке как есть (РФ-группы 29.10)
  const methodsOf = (g, tr) => METHODS.filter((m) => m !== "capacity" || (g.role || "abroad") !== "ru" || tr.method === "capacity" || !!(g.checks && g.checks.speed && g.checks.speed.enabled));
  // смена способа или новый выход в уровне двигает только новые устройства: уже закреплённые остаются, пока их выход
  // работает (assign: «stays: never moves inside a tier»), и переходят при ночной перекладке (REV-plan С-1, вариант а)
  // «По соединениям» у «Основной» — закреплений нет: ядро выбирает выход каждому соединению сразу (assign per_conn) —
  // подсказка про уже подключённые там неверна (REV-3312 П-1)
  function laterHint(gid, tr) {
    const f = forms[gid], g = f.draft, bt = (f.base.tiers || []).find((x) => x.id === tr.id);
    if (gid === "main" && g.per_connection) return null;
    if (tr.exits.length < 2 || (bt && bt.method === tr.method && (bt.order || "auto") === (tr.order || "auto") && tr.exits.every((id) => bt.exits.indexOf(id) >= 0))) return null;
    const nr = g.night_rebalance || {};
    return el("p", { class: "gx-hint kk-eg-later", role: "status", text: nr.enabled ? t("ep.laterNight", { at: nr.at || "04:00" }) : t("ep.laterNoNight") });
  }
  // ── Порядок в уровне (PLAN-order Ш5): ряды «силы» с сервера (live.groups.<gid>.ranks — assign.rows, тот же расчёт,
  // что выбор), ручной порядок (order: "manual"), перетаскивание и стрелки одним ходом (Kit.orderMove) ──────────────
  // режим уровня для хода: «по соединениям» у «Основной» — locked (порядок не действует), ручной — free, иначе ranked
  const perConn = (gid, g) => gid === "main" && !!g.per_connection;
  const tierMode = (gid, g, tr) => (perConn(gid, g) ? "locked" : tr.order === "manual" ? "free" : "ranked");
  const sameSet = (a, b) => (a || []).length === (b || []).length && (a || []).every((x) => (b || []).indexOf(x) >= 0);
  const ORD_SHOWN = Object.create(null);                 // «gid:tid» -> порядок автоуровня, показанный последним
  // способ уровня -> чем ядро меряет ряды (assign._ranked, поле by); «Лучший отклик» — мс или отклик к своей норме
  const BY_OF = { best_rtt: ["rtt", "x_norm"], weights: ["weights"], capacity: ["capacity"], even: ["even"] };
  // ряды сервера посчитаны для такого уровня: тот же флаг порядка и способ. После «Сохранить» база уже новая, а ряды —
  // до следующего круга сборщика старые (REV-order З-1). Участников нет — ядро отдаёт «even» при любом способе
  function ranksFit(gid, g, tr, r) {
    const pc = perConn(gid, g), man = !pc && tr.order === "manual";
    if ((r.order || "auto") !== (man ? "manual" : "auto")) return false;
    if (!r.rows.length) return true;
    return (pc ? ["per_connection"] : man ? ["manual"] : BY_OF[tr.method] || ["even"]).indexOf(r.by) >= 0;
  }
  // ряды уровня: ok — свежие и уровень не правили; changed — правили, пока не сохранено (второго расчёта в браузере нет),
  // или ряды ещё от прежнего способа; none — сборщик молчит или рядов нет
  function rankOf(gid, tr) {
    const f = forms[gid], lg = K.liveMark ? K.liveMark("groups") : null, lv = lg && lg[gid];
    const r = lv && lv.ranks && lv.ranks[tr.id];
    if (!r || !Array.isArray(r.rows)) return { st: "none", r: null };
    const bt = ((f && f.base.tiers) || []).find((x) => x.id === tr.id);
    const keep = (x) => [x.method, x.order || "auto", x.tolerance_ms, x.tolerance_pct, JSON.stringify(x.weights || {})];
    const same = !!bt && sameSet(bt.exits, tr.exits) && JSON.stringify(keep(bt)) === JSON.stringify(keep(tr)) && !!f.base.per_connection === !!f.draft.per_connection;
    return { st: same && ranksFit(gid, f.draft, tr, r) ? "ok" : "changed", r };
  }
  // список уровня держит фокус или поднятую строку: строки не переставляются по свежим рядам (PLAN-order §2.2)
  const ordHeld = (gid) => {
    if (Kit.orderLifted && Kit.orderLifted("eg:" + gid)) return true;
    const ae = typeof document !== "undefined" ? document.activeElement : null;
    return !!(ae && ae.closest && ae.closest('[data-ord-root="eg:' + gid + '"]'));
  };
  // порядок строк уровня на экране: ручной — список; автоуровень со свежими рядами — ряды, затем «не участвуют»; иначе —
  // сохранённый список. Ход начинается с этого порядка: ручной порядок — тот, что человек видел
  function shownOrder(gid, g, tr) {
    const rk = rankOf(gid, tr);
    if (tierMode(gid, g, tr) !== "ranked" || rk.st !== "ok") return tr.exits.slice();
    const key = gid + ":" + tr.id, was = ORD_SHOWN[key];
    if (was && ordHeld(gid) && sameSet(was, tr.exits)) return was.slice();
    const ids = [].concat(...rk.r.rows.map((row) => row.map((x) => x.id)), (rk.r.out || []).map((x) => x.id))
      .filter((id, i, a) => tr.exits.indexOf(id) >= 0 && a.indexOf(id) === i);
    tr.exits.forEach((id) => { if (ids.indexOf(id) < 0) ids.push(id); });
    ORD_SHOWN[key] = ids.slice();
    return ids;
  }
  function ordModel(gid) {
    const g = forms[gid].draft;
    return { steps: (g.role || "abroad") === "ru", sections: g.tiers.map((tr) => ({ id: tr.id, mode: tierMode(gid, g, tr), items: shownOrder(gid, g, tr) })) };
  }
  const ordOpts = (gid) => ({ max: MAX_TIERS, newId: () => newTierId(forms[gid].draft) });
  // ход в черновик: уровни в порядке модели, их выходы — как в модели; ставший ручным — order: "manual"; опустевший шаг
  // внутренней группы уходит (выбранный в «Куда встают новые» — снова «на последний»)
  function ordApply(gid, res) {
    const g = forms[gid].draft, by = Object.create(null);
    g.tiers.forEach((tr) => { by[tr.id] = tr; });
    g.tiers = res.model.sections.map((s) => {
      const tr = by[s.id] || { id: s.id, name: "", exits: [], method: "even", weights: {}, tolerance_ms: 30, tolerance_pct: 20 };
      tr.exits = s.items.slice();
      if (res.manual.indexOf(s.id) >= 0) tr.order = "manual";
      return tr;
    });
    res.dropped.forEach((id) => { if (g.new_exits_tier === id) g.new_exits_tier = "last"; });
  }
  function ordMove(gid, from, to, how) {
    const res = Kit.orderMove(ordModel(gid), from, to, ordOpts(gid));
    if (!res.ok) return false;
    const play = Kit.orderFlip ? Kit.orderFlip(forms[gid].card) : null;   // Ш6: строки переезжают плавно (FLIP)
    ordApply(gid, res);
    edited(gid);
    if (play) play();
    return true;
  }
  // стрелка ↑↓ строки (key — id выхода) или уровня («@id»): тот же ход; фокус — на ту же стрелку своей строки (имена
  // выходов в разных уровнях могут совпасть — REV-order П-1), ушла — на ручку
  function ordArrow(gid, key, dir, label) {
    const m = ordModel(gid), at = Kit.orderAt(m, key), to = at ? Kit.orderStep(m, at, dir) : null;
    if (!to || !ordMove(gid, at, to, "arrow")) return;
    const card = forms[gid].card;
    if (!card || !card.querySelector) return;
    const b = label ? card.querySelector('[data-ord-row="' + CSS.escape(key) + '"] button[aria-label="' + CSS.escape(label) + '"]') : null;
    const g = card.querySelector('[data-ord-grip="' + CSS.escape(key) + '"]');
    try { (b && !b.disabled ? b : g || b).focus(); } catch (e) { /* узел ушёл */ }
  }
  // «Способ выбора»: «По порядку (вручную)» — флаг уровня с порядком, как на экране; другой способ — флаг снят
  function setMethod(gid, tr, v) {
    const g = forms[gid].draft;
    if (v === "manual") { tr.exits = shownOrder(gid, g, tr); tr.order = "manual"; } else { delete tr.order; tr.method = v; }
    edited(gid);
  }
  // «Вернуть автовыбор»: флаг снят; пока не сохранено — и порядок, как был (состав тот же)
  function autoBack(gid, tr) {
    const bt = (forms[gid].base.tiers || []).find((x) => x.id === tr.id);
    delete tr.order;
    if (bt && bt.order !== "manual" && sameSet(bt.exits, tr.exits)) tr.exits = bt.exits.slice();
    edited(gid);
  }
  // строка под шапкой уровня: что значат ряды (по способу), «пересчитаются после сохранения», «сила неизвестна»
  // Ш6 (макет): ряды есть — их причина строкой над каждым рядом (rowWhy), под шапкой ничего; здесь — только «нет рядов»
  // и уровень из выходов обоих видов (сравнение к своей норме — словами)
  function ordNote(gid, g, tr, mode, rk) {
    if (mode === "free" || tr.exits.length < 1) return "";
    if (rk.st === "changed") return t("ep.ord.note.changed");
    if (rk.st !== "ok") return t(mode === "locked" ? "ep.ord.note.per_connection" : "ep.ord.note.none");
    const r = rk.r;
    return r.by === "x_norm" && !r.nodata ? t("ep.ord.note.x_norm", { pct: K.fmtInt(tr.tolerance_pct === undefined ? 20 : tr.tolerance_pct) }) : "";
  }
  // строки «Что изменится» при «Сохранить» (PLAN-order §3.3, §3.4): ручной порядок включён или снят; первым стал другой
  // уровень — трафик переедет (возврат на основной уровень — через hold_min; «по соединениям» — сразу)
  function ordImpact(f) {
    const out = [], bt = f.base.tiers || [], dt = f.draft.tiers || [], ru = (f.draft.role || "abroad") === "ru";
    dt.forEach((tr, i) => {
      const b = bt.find((x) => x.id === tr.id), was = !!b && b.order === "manual", now = tr.order === "manual";
      if (now && !was) out.push(t("ep.ord.saveManual", { n: tierLabel(tr, i) }));
      else if (was && !now) out.push(t("ep.ord.saveAuto", { n: tierLabel(tr, i) }));
      else if (was && now && sameSet(b.exits, tr.exits) && b.exits.join("\n") !== tr.exits.join("\n")) out.push(t("ep.ord.saveOrder", { n: tierLabel(tr, i) }));
    });
    const both = (a, o) => a.map((x) => x.id).filter((id) => o.some((y) => y.id === id));
    const bo = both(bt, dt), dO = both(dt, bt);
    // внутренняя группа: два шага поменялись местами — так и сказано (макет В1 · 2); строка о первом уровне ниже — тоже
    // (REV-order П-4: раньше здесь был выход из функции)
    if (ru && bo.length === dO.length) {
      const d = bo.map((id, i) => (id !== dO[i] ? i : -1)).filter((i) => i >= 0);
      if (d.length === 2 && bo[d[0]] === dO[d[1]] && bo[d[1]] === dO[d[0]]) {
        const at = (id) => dt.findIndex((x) => x.id === id) + 1;
        out.push(t("ep.ord.ruSwap", { g: groupLabel(f.draft.id), a: K.fmtInt(at(dO[d[0]])), b: K.fmtInt(at(dO[d[1]])) }));
      }
    }
    // внутренняя группа: ключ бросили на ключ другого шага — два ключа в одном шаге; только явно и с подписью (макет В2,
    // REV-order С-2): ключ, которого в этом шаге не было, а в другом шаге — был
    if (ru) dt.forEach((tr) => {
      const b = bt.find((x) => x.id === tr.id), host = b ? tr.exits.filter((id) => b.exits.indexOf(id) >= 0) : [];
      if (!host.length) return;
      tr.exits.forEach((id) => {
        if (host.indexOf(id) < 0 && bt.some((x) => x.id !== tr.id && (x.exits || []).indexOf(id) >= 0)) out.push(t("ep.ord.ruJoin", { g: groupLabel(f.draft.id), a: exitName(id), b: exitName(host[0]) }));
      });
    });
    if (dt.length > 1 && bo.length && dO.length && bo[0] !== dO[0] && dt[0].exits.length) {
      const rtp = f.draft.return_to_primary || {};
      const key = f.draft.id === "main" && f.draft.per_connection ? "ep.ord.saveTiersConn" : rtp.enabled === false ? "ep.ord.saveTiersNoBack" : "ep.ord.saveTiers";
      out.push(t(key, { n: tierLabel(dt[0], 0), min: K.fmtInt(rtp.hold_min || 10) }));
    }
    return out;
  }
  // что в строке выхода от рядов: метка ряда / места, доля, число отклика, по которому выбирает сервер
  function ordCell(rk, mode, id, ei) {
    const r = rk && rk.st === "ok" ? rk.r : null, out = r ? (r.out || []).find((x) => x.id === id) : null;
    // ручной уровень — строгая очередь: место — число и у того, кто не участвует (причина — фишкой; REV-order С-3)
    if (mode === "free") return Object.assign({ tag: K.fmtInt(ei + 1), tip: t("ep.ord.qt", { n: ei + 1 }) }, out ? { out: tk("ep.ord.why.", out.why) } : {});
    if (out) return { out: tk("ep.ord.why.", out.why), tag: "—" };
    if (!r) return { tag: "" };
    let at = null;
    r.rows.forEach((row, ri) => row.forEach((x) => { if (x.id === id) at = { ri, x }; }));
    if (!at || r.nodata || r.by === "even" || r.by === "per_connection") return { tag: "" };
    const res = { tag: t("ep.ord.R", { n: at.ri + 1 }), tip: t("ep.ord.Rt", { n: at.ri + 1 }) };
    if (r.by === "weights" || r.by === "capacity") res.share = t("ep.ord.share", { p: K.fmtInt(Math.round((at.x.share || 0) * 100)) });
    if (r.by === "rtt" && typeof at.x.v === "number") res.ms = { text: t("ep.ms", { n: K.fmtInt(at.x.v) }), tip: t("ep.ord.msT") };
    if (r.by === "x_norm" && typeof at.x.v === "number") res.ms = { text: "× " + K.fmtNum1(at.x.v), tip: t("ep.ord.xnT") };   // знак и число — без языка
    return res;
  }
  // подпись линии вставки: бросили в другой автоуровень — место не важно, «встанет по силе» (zone: линии нет, рамка у
  // уровня — макет П5). Внутренняя группа: бросок на ключ другого шага — «в один шаг с «…»» (target: шаг подсвечен
  // целиком, макет В2; REV-order С-2) — ключ, над которым палец или указатель, иначе первый ключ шага
  function ordTip(gid, m, from, to, hit) {
    if (to.s === undefined || to.s === from.s) return "";
    const sec = m.sections[to.s];
    if (!sec || !sec.items.length) return "";
    if (m.steps) return { text: t("ep.ord.oneStep", { name: exitName(sec.items[hit && typeof hit.i === "number" ? hit.i : 0] || sec.items[0]) }), target: true };
    return sec.mode === "ranked" ? { text: t("ep.ord.byStrength"), zone: true } : "";
  }
  // ── Вид «Уровней» (PLAN-order Ш6, макет Claude Design «Порядок в группах» r1, классы ko-*): ряд силы — метка «Р1» и
  // причина строкой над рядом, линия от метки к его строкам; полоска силы перед числом (сначала графика); «Не участвуют
  // · N» с причиной фишкой; правка до «Сохранить» — подложка строки и «было: …» ───────────────────────────────────────
  const OUT_TONE = { degraded: "warning", standby: "default", off: "default", unknown: "default", nometric: "default" };
  // допуск «равных» у «Лучшего отклика» — как у ядра (assign._tol): max(tolerance_ms, tolerance_pct от лучшего)
  function tolMs(tr, r) {
    const ms = tr.tolerance_ms === undefined ? 30 : tr.tolerance_ms, pct = tr.tolerance_pct === undefined ? 20 : tr.tolerance_pct;
    const vs = (r.rows[0] || []).map((x) => x.v).filter((v) => typeof v === "number");
    return Math.max(Number(ms) || 0, vs.length ? Math.round((Math.min(...vs) * (Number(pct) || 0)) / 100) : 0);
  }
  // причина ряда ri (строка с меткой над рядом); "" — метка у первой строки ряда (по весам Р2, к норме Р1)
  function rowWhy(tr, r, ri) {
    if (!r) return "";
    if (r.by === "per_connection") return ri ? "" : t("ep.ord.rw.conn");
    if (r.nodata) return ri ? "" : t(r.by === "capacity" ? "ep.ord.note.nodata_cap" : "ep.ord.note.nodata_rtt");
    if (r.by === "rtt") return ri ? t("ep.ord.rw.queue", { n: ri }) : t("ep.ord.rw.rtt", { ms: K.fmtInt(tolMs(tr, r)) });
    if (r.by === "x_norm") return ri ? t("ep.ord.rw.queue", { n: ri }) : "";
    if (r.by === "weights" || r.by === "capacity") return ri ? "" : t("ep.ord.rw.shares");
    return ri ? "" : t("ep.method.even");
  }
  // колонка чисел и полоска строки: «Лучший отклик» — мс (полоска — лучший / свой), к норме — «× 1,2»; доли — «· 60 %»
  // после веса, «420 Мбит/с · 55 %», «≈ 25 %» (полоска — доля новых); Р2 и ниже — полоска приглушена. Ручной уровень —
  // мс и полоска по отклику строк, справочно (решение координатора 1); рядов нет — мс и пустая дорожка (bar: null)
  function ordVal(rk, mode, id, c, r, bestMs) {
    const r0 = rk.st === "ok" && mode !== "free" ? rk.r : null, ms = r && typeof r.rttMs === "number" ? t("ep.ms", { n: K.fmtInt(r.rttMs) }) : "—";
    let x = null, ri = -1;
    if (r0) r0.rows.forEach((row, i) => row.forEach((y) => { if (y.id === id) { x = y; ri = i; } }));
    if (!x) return { bar: mode === "free" && r && r.rttMs > 0 && isFinite(bestMs) ? bestMs / r.rttMs : null, mute: false, num: ms };
    const pct = (v) => K.fmtInt(Math.round((v || 0) * 100)) + "%", by = r0.nodata ? "even" : r0.by;
    if (by === "rtt" || by === "x_norm") {
      const vs = r0.rows[0].map((y) => y.v).filter((v) => typeof v === "number" && v > 0), best = vs.length ? Math.min(...vs) : 0;
      return { bar: typeof x.v === "number" && x.v > 0 && best ? best / x.v : null, mute: ri > 0, num: c.ms ? c.ms.text : ms, tip: c.ms ? c.ms.tip : null };
    }
    if (by === "weights") return { bar: x.share, mute: ri > 0, num: pct(x.share) };
    if (by === "capacity") return { bar: x.share, mute: ri > 0, num: (typeof x.v === "number" ? t("ep.mbps", { n: K.fmtInt(x.v) }) + " · " : "") + pct(x.share) };
    return { bar: x.share, mute: false, num: (r0.rows[0].length > 1 ? "≈ " : "") + pct(x.share) };   // поровну, по соединениям, нет замеров
  }
  function ordBar(v) {
    const p = typeof v.bar === "number" && isFinite(v.bar) ? Math.max(0, Math.min(1, v.bar)) : null;
    const fill = p === null ? null : el("i");
    if (fill && fill.style) fill.style.width = Math.round(p * 100) + "%";     // CSSOM — CSP style-src 'self' это пускает
    return el("span", { class: "ko-bar" + (v.mute ? " ko-bar--mute" : "") + (p === null ? " ko-bar--none" : ""), "aria-hidden": "true" }, fill);
  }
  // «было: …» у строки, правленой до «Сохранить»: ручной уровень — ряд или место до правки; пришла из другого уровня —
  // его имя; во внутренней группе — шаг, где ключ стоял, а ход внутри шага с несколькими ключами (у него ряды Р1/Р2) —
  // как в обычной группе: ряд или место (Н-2 стенда 3.3.7.13). "" — строка на прежнем месте (автоуровень пересчитает сервер)
  function wasOf(gid, ti, id, tag, mode, rk, ei) {
    const f = forms[gid], bts = f.base.tiers || [], tr = f.draft.tiers[ti], bi = bts.findIndex((x) => (x.exits || []).indexOf(id) >= 0);
    if (bi < 0) return "";
    if ((f.draft.role || "abroad") === "ru" && (bts[bi].id !== tr.id || bi !== ti)) return t("ep.ru.stepN", { n: bi + 1 });
    if (bts[bi].id !== tr.id) return tierLabel(bts[bi], bi);
    if (mode !== "free") return "";
    // ручная база — место до правки против места сейчас (у «не участвует» метки «—» нет: REV-order С-3)
    if (bts[bi].order === "manual") { const p = bts[bi].exits.indexOf(id); return p !== ei ? K.fmtInt(p + 1) : ""; }
    let was = "";
    if (rk.r) rk.r.rows.forEach((row, i) => { if (row.some((y) => y.id === id)) was = t("ep.ord.R", { n: i + 1 }); });   // «не участвовал» — без «было»
    return was && was !== tag ? was : "";
  }
  // ⓘ у «Отклика»: по какому числу выбирает сервер (медиана последних 10 замеров; к норме — отношение)
  function ordInfo(by) {
    return infoPop(el("button", { type: "button", class: "gx-ib", "aria-label": t("ep.ord.msL") }, K.icon("circle-info", 14)), "", t(by === "rtt" ? "ep.ord.msT" : "ep.ord.xnT"), null, "gx-pop--l");
  }
  // шапка колонок уровня (на узкой части не видна): графика «Сила» перед числом; у внутренней группы — «Состояние»,
  // силы нет (выход выбирается по шагам)
  function colHead(ru, rk, mode, tr) {
    // у внутренней группы число — отклик; рядов нет, а способ «По весам» — в колонке поле веса
    const by = ru || mode === "free" ? "" : rk.st !== "ok" ? (tr && tr.method === "weights" ? "weights" : "") : rk.r.nodata ? "even" : rk.r.by;
    const num = by === "weights" ? "ep.ord.col.w" : by === "capacity" ? "ep.ord.col.cap" : by === "even" || by === "per_connection" ? "ep.ord.col.share" : "ep.rtt";
    const info = by === "rtt" || by === "x_norm" ? ordInfo(by) : null;
    return el("li", { class: "ko-ch" }, el("span", { class: "ko-ch__o" }), el("span", { text: t(ru ? "ep.ru.key" : "ep.ord.col.exit") }),
      el("span", { text: ru ? t("ep.chStates") : t("ep.ord.col.str") }), el("span", { class: "ko-ch__num" }, el("span", { text: t(num) }), info),
      el("span", { text: t("ep.ord.col.dev") }), el("span", { text: t("ep.ord.col.tier") }));
  }
  function tiersBlock(gid) {
    const f = forms[gid], g = f.draft;
    const ru = (g.role || "abroad") === "ru";
    // строка «Уровни» с «Разложить заново» справа и подтверждением под ней (F14, rgHead)
    const box = el("div", { class: "kk-eg-blk" }, rgHead(gid, sub(t("ep.tiers"), t("ep.tiersText"))),
      el("p", { class: "kk-g2__gl" }, K.icon("circle-info", 14), el("span", { text: t("ep.groupVsTier") })),
      ru && !countExits(g) ? el("p", { class: "gx-hint", text: t("ep.ru.emptyGroup") }) : null);
    const model = ordModel(gid), opts = ordOpts(gid), lifted = Kit.orderLifted ? Kit.orderLifted("eg:" + gid) : null;
    const can = (key, dir) => { const at = Kit.orderAt(model, key), to = at ? Kit.orderStep(model, at, dir) : null; return !!to && Kit.orderMove(model, at, to, opts).ok; };
    const where = (s) => (ru ? t("ep.ru.stepN", { n: s + 1 }) : tierLabel(g.tiers[s], s));
    g.tiers.forEach((tr, ti) => {
      const sec = model.sections[ti], mode = sec.mode, rk = rankOf(gid, tr);
      // способ выбора и вес — не поля набора (policy.PRESET_KEYS): их правка набор не меняет; «По порядку (вручную)» —
      // флаг уровня (способ остаётся), у «по соединениям» его нет: порядок там не действует
      const items = methodsOf(g, tr).map((m) => ({ v: m, l: t("ep.method." + m) }));
      // у шага внутренней группы из одного ключа порядка нет (REV-order П-3)
      if ((mode !== "locked" && !(ru && tr.exits.length < 2)) || tr.order === "manual") items.push({ v: "manual", l: t("ep.method.manual") });
      const meth = U.select(items, tr.order === "manual" ? "manual" : tr.method, (v) => setMethod(gid, tr, v), { aria: t("ep.methodName", { n: tierLabel(tr, ti) }) });
      // «По порядку (вручную)» в поле 176 px может не уместиться — полное имя подсказкой (REV-order, отклонение 6)
      meth.title = (items.find((x) => x.v === (tr.order === "manual" ? "manual" : tr.method)) || {}).l || "";
      // убрали уровень, выбранный в «Куда встают новые» — поле снова «на последний» (иначе ядро не примет политику:
      // ep.err.new_tier; REV-plan З-2). Поднять / опустить уровень — тот же ход, что ручка в шапке
      const tmenu = U.menu([{ label: t("ep.tierRename"), on: () => tierRename(gid, ti) },
        can("@" + tr.id, -1) ? { label: t("ep.ord.tierUp"), on: () => ordArrow(gid, "@" + tr.id, -1) } : null,
        can("@" + tr.id, 1) ? { label: t("ep.ord.tierDown"), on: () => ordArrow(gid, "@" + tr.id, 1) } : null,
        g.tiers.length > 1 && !tr.exits.length ? { label: t("ep.tierDrop"), on: () => { g.tiers.splice(ti, 1); if (g.new_exits_tier === tr.id) g.new_exits_tier = "last"; edited(gid); } } : null].filter(Boolean), { label: tierLabel(tr, ti) });
      const tgrip = g.tiers.length > 1 ? Kit.orderGrip({ key: "@" + tr.id, name: tierLabel(tr, ti), pressed: lifted === "@" + tr.id }) : null;
      const bt = (f.base.tiers || []).find((x) => x.id === tr.id), mWas = bt && bt.method !== tr.method ? t("ep.ord.was", { x: tk("ep.method.", bt.method) }) : "";
      const head = el("div", { class: "kk-eg-tier__h" + (mWas ? " is-dirty" : ""), "data-ord-head": tr.id },
        el("span", { class: "kk-eg-tier__tt" }, tgrip, el("b", { text: tr.name ? t("ep.tierHead", { n: ti + 1, name: tr.name }) : t("ep.tierN", { n: ti + 1 }) }),
          el("span", { class: "gx-desc", text: K.tn(ru ? "ep.ru.keysN" : "ep.exitsN", tr.exits.length) })),
        el("span", { class: "kk-eg-l kk-eg-tier__m" }, mWas ? el("span", { class: "ko-was", text: mWas }) : null, el("span", { text: t("ep.method") }), methodInfo()),
        el("span", { class: "kk-eg-tier__pw" }, meth), tmenu);
      const note = ordNote(gid, g, tr, mode, rk);
      // ручной порядок — строкой внутри уровня, без окна (макет: gx-al --plain на поверхности, ko-al)
      const man = mode === "free" && tr.exits.length ? U.al("info", t("ep.ord.manualT"), t("ep.ord.manual", { m: tk("ep.method.", tr.method) }),
        { row: true, btns: [U.btn(t("ep.ord.back"), "tertiary", () => autoBack(gid, tr), { icon: "arrow-rotate-left" })] }) : null;
      if (man) { man.classList.add("kk-eg-man"); man.classList.add("ko-al"); }
      const list = el("ul", { class: "kk-eg-exits", "aria-label": tierLabel(tr, ti) });
      if (!tr.exits.length) list.append(el("li", { class: "kk-eg-empty", "data-ord-empty": tr.id, text: t("ep.tierEmpty") }));
      else list.append(colHead(ru, rk, mode, tr));
      // ряды сервера по строкам: номер ряда (−1 — вне рядов: ручной, «не участвуют», рядов нет), первая и последняя
      // строка ряда — для метки и линии от неё; во внутренней группе с одним ключом на шаг меток нет
      const r0 = rk.st === "ok" && mode !== "free" ? rk.r : null, cells = sec.items.map((id, ei) => ordCell(rk, mode, id, ei));
      const ris = sec.items.map((id, ei) => { let ri = -1; if (r0 && !cells[ei].out) r0.rows.forEach((row, i) => { if (row.some((y) => y.id === id)) ri = i; }); return ri; });
      const labs = !(ru && tr.exits.length < 2), nOut = cells.filter((c) => c.out).length;
      const rtts = sec.items.map((id) => { const r = rowOf(id); return r && typeof r.rttMs === "number" && r.rttMs > 0 ? r.rttMs : Infinity; });
      const bestMs = Math.min(...rtts.concat([Infinity]));
      let outHead = false, anyLab = false;
      sec.items.forEach((id, ei) => {
        const r = rowOf(id), c = cells[ei], ri = ris[ei];
        if (c.out && !outHead && mode !== "free") { outHead = true; list.append(el("li", { class: "kk-eg-outh", text: t("ep.ord.out", { n: K.fmtInt(nOut) }) })); }
        const first = ri >= 0 && ris[ei - 1] !== ri, last = ri >= 0 && ris[ei + 1] !== ri, many = ri >= 0 && !(first && last);
        const why = first && labs ? rowWhy(tr, r0, ri) : "";
        // «По скорости» без замеров — рядом переход к тесту скорости группы (макет П6 · 5, REV-order П-5)
        const toSp = why && !ri && r0 && r0.nodata && r0.by === "capacity" ? U.btn(t("ep.ord.speedL"), "link", () => toSpeed(gid)) : null;
        if (why) list.append(el("li", { class: "ko-why" + (many ? " ko-in0" : "") }, el("span", { class: "ko-rk" + (ri ? " ko-rk--2" : ""), text: t("ep.ord.R", { n: ri + 1 }) }),
          el("span", { class: "ko-why__t", text: why }, toSp ? " " : null, toSp)));
        // метка у строки: место в ручном; ряд без строки причины — у первой его строки; иначе скрыта (читается экранным диктором)
        const labOn = labs && !!c.tag && c.tag !== "—" && (mode === "free" || (first && !why));
        anyLab = anyLab || labOn;
        const line = !many ? "" : (first && !why ? " ko-in0" : " ko-in") + (last ? " ko-end" : "");
        const was = wasOf(gid, ti, id, c.tag, mode, rk, ei), v = ru ? null : ordVal(rk, mode, id, c, r, bestMs);
        // фишка вместо полоски и числа: причина «не участвует»; рядов нет, а выход не работает — его состояние
        // (ухудшен — коротко, полная причина — подсказкой); во внутренней группе — колонка «Состояние», как была
        const oc = c.out ? (rk.r.out.find((x) => x.id === id) || {}).why : "";
        const chip = ru ? (r ? stateChip(r) : U.chip(t("ep.st.unknown"), "default"))
          : c.out ? U.chip(oc === "degraded" ? t("ep.st.degraded") : c.out, OUT_TONE[oc] || "default")
          : r && r.pstate && r.pstate !== "ok" && rk.st !== "ok" ? stateChip(r) : null;
        if (c.out && chip && chip.setAttribute) chip.setAttribute("title", c.out);
        const upL = t("ep.upName", { name: exitName(id) }), dnL = t("ep.downName", { name: exitName(id) });
        const up = U.btn("", "ghost", () => ordArrow(gid, id, -1, upL), { icon: "arrow-up-from-line", tip: t("ep.up"), aria: upL });
        const dn = U.btn("", "ghost", () => ordArrow(gid, id, 1, dnL), { icon: "arrow-down-to-line", tip: t("ep.down"), aria: dnL });
        up.disabled = !can(id, -1); dn.disabled = !can(id, 1);
        const grip = Kit.orderGrip({ key: id, name: exitName(id), dis: mode === "locked", tip: t("ep.ord.locked"), pressed: lifted === id });
        const tierSel = U.select(g.tiers.map((x, i) => ({ v: String(i), l: tierLabel(x, i) })), String(ti), (v) => { moveTo(gid, id, Number(v)); }, { aria: t("ep.tierOf", { name: exitName(id) }) });
        const wt = tr.method === "weights" ? numIn(tr.weights[id] || 1, 1, 100, t("ep.weightOf", { name: exitName(id) }), (v) => { if (v !== null) { tr.weights[id] = v; changed(gid); } }) : null;
        const more = U.menu([{ label: t("ep.details"), on: () => openExit(id) }, { label: t("ep.moveTo"), on: () => moveDialog(id) },
          { label: t("ep.removeFrom"), on: () => removeDialog(id, gid) }], { label: exitName(id) });
        const desc = [was ? t("ep.ord.was", { x: was }) : "", r ? kindText(r) : ""].filter(Boolean).join(" · ");
        const ms = c.ms ? c.ms.text : r && r.rttMs !== null && r.rttMs !== undefined ? t("ep.ms", { n: K.fmtInt(r.rttMs) }) : "—";
        const cls = "kk-eg-exit" + (c.out ? " is-out" : "") + (rk.st === "none" && mode === "ranked" && tr.exits.length > 1 ? " is-stale" : "") + (lifted === id ? " is-lifted" : "")
          + (was ? " is-dirty" : "") + (labOn ? " ko-lab" + (ri > 0 ? " ko-r2" : "") : "") + line + (chip ? " ko-chip" : "");
        list.append(el("li", { class: cls, "data-hold-row": "eg:" + id, "data-ord-row": id },
          // метка ряда или места — первой (колонка меток макета), за ней ручка и ↑↓
          el("span", { class: "kk-eg-ord" }, el("span", { class: "kk-eg-rk" + (labOn ? "" : " gx-vh"), title: c.tip || null, text: c.tag || "" }), grip, up, dn),
          el("button", { type: "button", class: "kk-eg-exit__n", onclick: () => openExit(id) }, el("b", { text: exitName(id), title: exitName(id) }), el("span", { class: "gx-desc", text: desc })),
          v && !chip ? ordBar(v) : null,
          // отклик и устройства на выходе (РФ-группы Ш1: по «Устройств» видно, как разложены устройства) — две колонки
          // на компьютере, одной строкой под именем в узкой части (kk-eg-nums: display contents / flex; не kk-eg-num —
          // это класс полей чисел форм условия и квоты: display contents прятал их, владелец 29.10). В автоуровне с
          // «Лучшим откликом» — число, по которому выбирает сервер (медиана последних 10 замеров, PLAN-order вопрос 6)
          // Ш6: число — колонкой «Отклик» / «Вес · доля» / «Доля» после полоски; вес — полем в той же колонке перед долей;
          // у строки с фишкой числа нет (фишка — на месте полоски и числа)
          // фишка (состояние, причина) — в той же обёртке: на компьютере своей колонкой, в узкой части — строкой под именем
          el("span", { class: "kk-eg-nums" }, chip ? el("span", { class: "kk-eg-st" }, chip) : null, chip && !ru ? null : el("span", { class: "ko-num" },
            wt ? el("label", { class: "kk-eg-w" }, el("span", { text: t("ep.weight") }), wt) : null,
            // вес есть, а доли нет (рядов нет или правка до «Сохранить») — только поле: мс после веса читалось бы как доля
            wt && v && v.bar === null ? null : el("span", { class: "kk-eg-rtt gx-mono", title: (v ? v.tip : c.ms && c.ms.tip) || null, text: v ? v.num : ms })),
            el("span", { class: "kk-eg-dev" + (rgUpdOf(gid, id) ? " kk-rg-upd" : ""), title: t("ep.devOnExit"), text: r && typeof r.devices === "number" ? K.tn("ep.devicesN", r.devices) : "—" })),
          el("span", { class: "kk-eg-tier__pw" }, tierSel), more));
      });
      box.append(el("div", { class: "kk-eg-tier" + (ru ? " is-ru" : "") + (mode === "free" ? " ko-man" : "") + (anyLab ? " ko-hasrk" : "") + (labs ? "" : " ko-nolab") + (lifted === "@" + tr.id ? " is-lifted" : ""), "data-ord-sec": tr.id }, head,
        // нет рядов (сервер молчит, правка до «Сохранить», к норме) — строкой под шапкой (ko-note), со значком
        note ? el("p", { class: "ko-note kk-eg-rkn", role: "status" }, K.icon(rk.st === "none" && mode === "ranked" ? "triangle-exclamation" : "circle-info", 14), el("span", { text: note })) : null,
        man, laterHint(gid, tr), list));
    });
    const addWhy = unsavedWhy(gid);
    // у российской — «Добавить ключ в «<группа>»» (окно ключа с этой группой, REV-plan С-3); «Добавить выход…» (перенос
    // из другой группы) у неё не нужен: перенос — «Перенести в группу…» в «⋯» ключа
    const add = ru ? U.btn(t("ep.ru.addKeyTo", { g: groupLabel(gid) }), "tertiary", () => addKey(gid), { icon: "plus" }) : U.btn(t("ep.addExit"), "tertiary", () => addExitDialog(gid), { icon: "plus" });
    add.disabled = !!addWhy;
    if (addWhy) add.title = addWhy;
    add.dataset.egAdd = gid;
    // в группе не больше MAX_TIERS уровней (policy.MAX_TIERS, ошибка ядра ep.err.tiers_many): 11-й не добавить
    const full = g.tiers.length >= MAX_TIERS;
    const addTier = U.btn(t("ep.addTier"), "tertiary", () => { if (g.tiers.length >= MAX_TIERS) return; g.tiers.push({ id: newTierId(g), name: "", exits: [], method: "even", weights: {}, tolerance_ms: 30, tolerance_pct: 20 }); edited(gid); }, { icon: "plus" });
    addTier.disabled = full;
    if (full) addTier.title = t("ep.tiersMax", { n: MAX_TIERS });
    // «Разложить по протоколу» — что именно сделает, словами рядом (№30); у российской группы (ключи по шагам) — нет
    // data-ord-foot — нижняя граница списка уровней для перетаскивания: ниже — «сюда нельзя» (REV-order С-1)
    box.append(el("div", { class: "kk-eg-acts", "data-ord-foot": "" }, addTier,
      ru ? null : U.btn(t("ep.byProto"), "tertiary", () => byProto(gid)), ru ? null : el("p", { class: "gx-hint kk-lv__bp", text: t("ep.byProtoD") }), add,
      addWhy ? el("span", { class: "gx-hint", text: addWhy }) : null, full ? el("span", { class: "gx-hint", text: t("ep.tiersMax", { n: MAX_TIERS }) }) : null));
    // перетаскивание (мышь, перо, палец) и клавиатура — Kit.orderList (PLAN-order Ш4) над теми же ходами, что стрелки
    if (Kit.orderList) Kit.orderList({ root: box, key: "eg:" + gid, model: () => ordModel(gid), max: MAX_TIERS, newId: () => newTierId(forms[gid].draft),
      name: (key) => (key.charAt(0) === "@" ? tierLabel(g.tiers.find((x) => "@" + x.id === key) || {}, g.tiers.findIndex((x) => "@" + x.id === key)) : exitName(key)),
      where: (m, at) => where(at.s), tip: (m, from, to, hit) => ordTip(gid, m, from, to, hit),
      onMove: (from, to, how) => { ordMove(gid, from, to, how); if (how === "key") focusIn(forms[gid].card, '[data-ord-grip="' + CSS.escape(Kit.orderLifted("eg:" + gid) || "") + '"]'); },
      snapshot: () => ({ tiers: copy(forms[gid].draft.tiers), nt: forms[gid].draft.new_exits_tier }),
      restore: (s) => { if (!s || !forms[gid]) return; const key = document.activeElement && document.activeElement.getAttribute ? document.activeElement.getAttribute("data-ord-grip") : null;
        forms[gid].draft.tiers = s.tiers; forms[gid].draft.new_exits_tier = s.nt; edited(gid); if (key) focusIn(forms[gid].card, '[data-ord-grip="' + CSS.escape(key) + '"]'); } });
    return box;
  }
  function newTierId(g) { let i = g.tiers.length + 1; while (g.tiers.some((x) => x.id === "t" + i)) i++; return "t" + i; }
  function moveTo(gid, id, ti) {
    const g = forms[gid].draft;
    g.tiers.forEach((tr) => { tr.exits = tr.exits.filter((x) => x !== id); });
    g.tiers[ti].exits.push(id);
    edited(gid);
  }
  // «Разложить по протоколу»: AmneziaWG — уровень 1, xray — уровень 2
  function byProto(gid) {
    const g = forms[gid].draft, all = [].concat(...g.tiers.map((tr) => tr.exits));
    while (g.tiers.length < 2) g.tiers.push({ id: newTierId(g), name: "", exits: [], method: "even", weights: {}, tolerance_ms: 30, tolerance_pct: 20 });
    g.tiers.forEach((tr) => { tr.exits = []; });
    all.forEach((id) => { const r = rowOf(id); g.tiers[r && r.kind === "awg" ? 0 : 1].exits.push(id); });
    edited(gid);
  }
  function tierRename(gid, ti) {
    const g = forms[gid].draft, tr = g.tiers[ti];
    const inp = U.input({ value: tr.name || "", max: 32, aria: t("ep.tierRename"), ph: t("ep.tierN", { n: ti + 1 }) });
    const d = K.openDialog({ title: t("ep.tierRename"), blocks: [U.field(t("ep.tierNameL"), inp, { hint: t("ep.tierNameHint") })] });
    const ok = () => { tr.name = inp.value.trim(); d.close(); edited(gid); };
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ok(); } });
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true }), U.btn(t("ep.apply"), "primary", ok, { md: true })]);
    setTimeout(() => inp.focus(), 30);
  }

  // Второй уровень группы (UI-A2, UX-аудит 20.10 «Группы»): «Поведение» и «Проверки» — свёрнуты строкой, раскрытие
  // помнит MORE_OPEN. Неверный «Свой интервал» (Сохранить неактивна) — второй уровень раскрыт и не сворачивается:
  // ошибку не спрятать, а пересборка показала бы в поле 60 вместо набранного
  function moreBlock(gid) {
    const g = forms[gid].draft, id = "eg-m-" + gid;
    const bad = !freqValid(g), isOpen = !!MORE_OPEN[gid] || bad;
    const b = el("button", { type: "button", class: "kk-eg-more__tog", "data-eg-more": gid, "aria-expanded": String(isOpen), "aria-controls": id, "aria-disabled": bad ? "true" : null },
      el("span", { class: "kk-eg-more__t" }, el("span", { class: "kk-eg-h3", text: t("ep.moreT") }), el("span", { class: "gx-desc", text: t("ep.moreD") })),
      el("span", { class: "kk-eg-chev", "aria-hidden": "true" }, K.icon("chevron-down", 16)));
    b.addEventListener("click", () => { if (!freqValid(g)) return; MORE_OPEN[gid] = !isOpen; renderCard(gid); focusIn(forms[gid] && forms[gid].card, ".kk-eg-more__tog"); });
    return el("div", { class: "kk-eg-more" + (isOpen ? " is-open" : ""), "data-hold-row": "egm:" + gid }, b,
      isOpen ? el("div", { class: "kk-eg-more__b", id }, behaviourBlock(gid), checksBlock(gid)) : el("div", { class: "kk-eg-more__b", id, hidden: true }));
  }
  // «Тест скорости» у ряда «Поровну — нет теста скорости» (макет П6 · 5, REV-order П-5): раскрыть «Ещё» группы и встать
  // на переключатель «Тест скорости группы»
  function toSpeed(gid) {
    MORE_OPEN[gid] = true;
    renderCard(gid);
    const card = forms[gid] && forms[gid].card, b = card && card.querySelector ? card.querySelector("[data-eg-speed]") : null;
    if (!b) return;
    try { b.scrollIntoView({ block: "center" }); b.focus({ preventScroll: true }); } catch (e) { /* узел ушёл */ }
  }
  // «Что будет при наборе …» (№30, макет it1 Д3 Г2): у «Стандарта» и «Своего» — три сценария (Своему за основу —
  // «Стандарт»), у «Экономно», «Строго», «Как раньше» и «Дом» — их строка; справа в шапке — как часто проверки
  const SCN_ROWS = [["xmark", "ep.scn.downW", "ep.scn.downX"], ["arrow-rotate-right", "ep.scn.flapW", "ep.scn.flapX"], ["gauge", "ep.scn.slowW", "ep.scn.slowX"]];
  function scnBlock(g) {
    const pr = g.preset || "custom", rowsOn = pr === "standard" || pr === "custom";
    const p = freqS(g.checks && g.checks.freq);
    const s = p ? t("ep.scn.every", { n: K.fmtInt(p) }) : t("ep.scn.lazy");
    return el("div", { class: "kk-scn" },
      el("div", { class: "kk-scn__h" }, el("span", { class: "kk-scn__t", text: has("ep.scn.t." + pr) ? t("ep.scn.t." + pr) : t("ep.scn.t.custom") }), el("span", { class: "kk-scn__s", text: s })),
      ...(rowsOn ? SCN_ROWS.map((x) => el("div", { class: "kk-scn__r" }, el("span", { class: "kk-scn__ic", "aria-hidden": "true" }, K.icon(x[0], 14)),
        el("span", { class: "kk-scn__w", text: t(x[1]) }), el("span", { class: "kk-scn__a", "aria-hidden": "true", text: "→" }), el("span", { class: "kk-scn__x", text: t(x[2]) })))
        : [el("p", { class: "kk-scn__line", text: has("ep.scn.line." + pr) ? t("ep.scn.line." + pr) : "" })]));
  }
  // «Поведение» (пачка А п. 3, владелец 28.10 «да»; макет it1 Д3 Г2): на виду — три переключателя макета (уводить с
  // ухудшенного, возврат, нестабильный — в запас), у российской группы ещё ночная перекладка (REV-plan С-1, вариант а:
  // только она раскладывает уже подключённые устройства по ключам уровня); редкие — под «Ещё»: ночная перекладка
  // (зарубежные), обрыв соединений, «По соединениям» («Основная»), куда встают новые и перенесённые выходы. «Ещё»
  // раскрыто само, если там что-то включено или выбрано не по умолчанию — включённое не прячется
  const RARE_OPEN = Object.create(null);
  const rareOn = (g, ru) => (!ru && !!(g.night_rebalance && g.night_rebalance.enabled)) || !!g.drop_on_degrade || !!g.per_connection || (g.new_exits_tier || "last") !== "last";
  // «Куда встают новые и перенесённые»: на последний уровень (у российской — каждый новый ключ своим уровнем, как
  // policy.new_tier), на первый или в выбранный уровень; действует и на «Перенести в группу», и на «Удалить группу →
  // выходы туда» (policy_ops: P.new_tier)
  function newTierRow(gid) {
    const g = forms[gid].draft, ru = (g.role || "abroad") === "ru";
    const cur = g.new_exits_tier || "last";
    const items = [{ v: "last", l: t(ru ? "ep.newTier.lastRu" : "ep.newTier.last") }, { v: "first", l: t("ep.newTier.first") }]
      .concat(g.tiers.map((tr, i) => ({ v: tr.id, l: tierLabel(tr, i) })));
    const sel = U.select(items, items.some((x) => x.v === cur) ? cur : "last", (v) => { g.new_exits_tier = v; changed(gid); }, { aria: t(ru ? "ep.newTierRu" : "ep.newTier") });
    return el("div", { class: "kk-eg-row kk-eg-nt", "data-eg-nt": gid }, el("span", { class: "kk-eg-l", text: t(ru ? "ep.newTierRu" : "ep.newTier") }), sel,
      el("span", { class: "gx-hint", text: t(ru ? "ep.newTierDRu" : "ep.newTierD") }));
  }
  function behaviourBlock(gid) {
    const g = forms[gid].draft, ru = (g.role || "abroad") === "ru";
    const set = (fn) => (v) => { fn(v); touchPreset(g); changed(gid); };
    const hold = numIn(g.return_to_primary.hold_min, 1, 120, t("ep.returnMin"), (v) => { if (v !== null) g.return_to_primary.hold_min = v; touchPreset(g); changed(gid); });
    const at = U.input({ value: g.night_rebalance.at, type: "time", aria: t("ep.nightAt") });
    at.addEventListener("input", () => { if (/^([01]\d|2[0-3]):[0-5]\d$/.test(at.value)) { g.night_rebalance.at = at.value; touchPreset(g); changed(gid); } });
    const night = el("div", { class: "kk-eg-swrow" }, swLive(t("ep.night"), t(ru ? "ep.nightDRu" : "ep.nightD"), () => g.night_rebalance.enabled, set((v) => { g.night_rebalance.enabled = v; })), at);
    const rare = [ru ? null : night, swLive(t("ep.drop"), t("ep.dropD"), () => g.drop_on_degrade, set((v) => { g.drop_on_degrade = v; })),
      // «По соединениям» — только у «Основной» (ядро: policy.validate ep.err.per_connection_main)
      gid === "main" ? swLive(t("ep.perConn"), t("ep.perConnD"), () => !!g.per_connection, (v) => { g.per_connection = v; changed(gid); }) : null,
      newTierRow(gid)].filter(Boolean);
    const isOpen = RARE_OPEN[gid] !== undefined ? RARE_OPEN[gid] : rareOn(forms[gid].base, ru) || rareOn(g, ru);
    const id = "eg-r-" + gid;
    const tog = el("button", { type: "button", class: "kk-eg-rare__tog", "data-eg-rare": gid, "aria-expanded": String(isOpen), "aria-controls": id },
      el("span", { class: "kk-eg-rare__t" }, el("b", { text: t("ep.rareT") }), el("span", { class: "gx-desc", text: t(ru ? "ep.rareDRu" : gid === "main" ? "ep.rareDMain" : "ep.rareD") })),
      el("span", { class: "kk-eg-chev", "aria-hidden": "true" }, K.icon("chevron-down", 14)));
    tog.addEventListener("click", () => { RARE_OPEN[gid] = !isOpen; renderCard(gid); focusIn(forms[gid] && forms[gid].card, '[data-eg-rare="' + CSS.escape(gid) + '"]'); });
    return el("div", { class: "kk-eg-blk" }, scnBlock(g), sub(t("ep.behaviour"), ""),
      swLive(t("ep.evict"), t("ep.evictD"), () => g.evict_degraded, set((v) => { g.evict_degraded = v; })),
      el("div", { class: "kk-eg-swrow" }, swLive(t("ep.return"), t("ep.returnD", { n: K.fmtInt(g.return_to_primary.hold_min || 10) }), () => g.return_to_primary.enabled, set((v) => { g.return_to_primary.enabled = v; })), el("label", { class: "kk-eg-w" }, el("span", { text: t("ep.minutes") }), hold)),
      ru ? night : null,
      swLive(t("ep.flap"), t("ep.flapD"), () => g.flap_standby, set((v) => { g.flap_standby = v; }), { last: true }),
      el("div", { class: "kk-eg-rare" + (isOpen ? " is-open" : ""), "data-hold-row": "egq:" + gid }, tog,
        el("div", { class: "kk-eg-rare__b", id, hidden: !isOpen }, ...rare)));
  }
  // период проверок группы, с (§3.2): «Лениво» — null (по трафику); для пересчёта «≈ МБ проб» и «реакции на отказ»
  const freqS = (fr) => (!fr || fr.mode === "lazy" ? null : fr.mode === "custom" ? Number(fr.every_s) || null : FREQ_P[fr.mode] || null);
  function checksBlock(gid) {
    const f = forms[gid], g = f.draft, c = g.checks;
    const every = numIn(c.freq.every_s || 60, 10, 600, t("ep.freqCustomS"), (v) => { c.freq.every_s = v; touchPreset(g); hints(); changed(gid); });
    const everyBox = el("label", { class: "kk-eg-w", hidden: c.freq.mode !== "custom" }, el("span", { text: t("ep.freqCustomS") }), every);
    const restart = el("div", { class: "gx-hint", role: "status" });
    const note = () => { restart.textContent = freqChanged(f) ? t("ep.xrayRestart") : ""; };
    const freq = radioLive(FREQ.map((x) => ({ v: x[0], title: t("ep.freq." + x[0]), desc: x[0] === "custom" ? "" : t("ep.freqD." + x[0]) })), () => c.freq.mode,
      (v) => { c.freq = { mode: v, every_s: v === "custom" ? Number(every.value) || 60 : null }; everyBox.hidden = v !== "custom"; touchPreset(g); note(); hints(); changed(gid); }, { aria: t("ep.freq") });
    freq.classList.add("kk-eg-freq");
    note();
    // «≈ МБ проб в сутки на выход» (сервер, при сохранённой частоте — пересчёт по новой), «реакция на отказ ≈ …» и строка
    // общей проверки xray — по черновику, при каждой смене частоты (SE §7.1, §3.2; REV-s1-t8-a П-2)
    const ids = [].concat(...g.tiers.map((tr) => tr.exits));
    const r0 = rows().find((r) => ids.indexOf(r.id) >= 0);
    const hasXray = ids.some((id) => { const r = rowOf(id); return r && r.kind !== "awg" && !r.wg; });
    const freqHint = el("div", { class: "gx-hint" });
    const obsHint = el("div", { class: "gx-hint" });
    const hints = () => {
      const now = freqS(c.freq), was = freqS(f.base.checks.freq);
      let mb = "";
      if (r0 && r0.probeMbDay !== null) {
        if (same(c.freq, f.base.checks.freq)) mb = t("ep.probeExit", { n: K.fmtNum1(r0.probeMbDay) });
        else if (now && was) mb = t("ep.probeExit", { n: K.fmtNum1(r0.probeMbDay * was / now) });
      }
      // «Лениво»: выход без трафика проверяется раз в LAZY_WAKE_MIN мин (collect.LAZY_WAKE_S) — реакция до минут (T-q Н-lazy)
      // сроки отказа — только в сценариях «Поведения» (круг 1: «≈ 3P» расходился с ядром); у «Лениво» — его строка
      const react = c.freq.mode === "lazy" ? t("ep.reactLazy", { n: LAZY_WAKE_MIN + 1, w: LAZY_WAKE_MIN }) : "";
      freqHint.textContent = [mb, react].filter(Boolean).join(" · ");
      const obs = hasXray ? obsLineOf(g) : null;
      obsHint.textContent = obs ? t("ep.obsLine", { s: K.fmtInt(obs.s), g: groupLabel(obs.gid) }) : "";
      obsHint.hidden = !obsHint.textContent;
    };
    hints();
    const sp = c.speed;
    // прогноз теста скорости группы «≈ N ГБ/мес»: формула forecast сервера, но по выходам группы без «не проверять
    // скорость»; бюджет в месяц (общий на сервер) — потолок, как у forecast: больше него тесты не потратят (REV-s1-t8m П-1)
    const spMonth = el("span", { class: "gx-hint" });
    const spHint = () => {
      const pr = (pol() && pol().policy.probes && pol().policy.probes.speed) || {};
      const n = ids.filter((id) => { const r = rowOf(id); return !(r && r.speedSkip); }).length;
      const gb = n * (30 * 24 / (sp.every_h || 6)) * (Number(pr.max_mb) || 10) / 1000, cap = Number(pr.budget_month_gb);
      spMonth.textContent = !n ? "" : cap > 0 && gb > cap ? t("ep.speedMonthCap", { cap: K.fmtNum1(cap), n: K.fmtNum1(gb) }) : t("ep.speedMonth", { n: K.fmtNum1(gb) });
    };
    spHint();
    const speedOpts = el("div", { class: "kk-eg-speed", hidden: !sp.enabled },
      el("label", { class: "kk-eg-w" }, el("span", { text: t("ep.speedEvery") }), U.select(SPEED_H.map((h) => ({ v: String(h), l: t("ep.hours", { n: h }) })), String(sp.every_h), (v) => { sp.every_h = Number(v); spHint(); changed(gid); }, { aria: t("ep.speedEvery") })),
      spMonth, el("span", { class: "gx-hint", text: t("ep.speedCommon") }));
    const speedSw = swLive(t("ep.speed"), t("ep.speedD"), () => sp.enabled, (v) => { sp.enabled = v; speedOpts.hidden = !v; touchPreset(g); changed(gid); });
    speedSw.setAttribute("data-eg-speed", gid);           // сюда ведёт «Тест скорости» из «Уровней» (toSpeed)
    // страна: вкл./выкл. и частота (geo.every_h, 1–168 ч — policy._validate)
    const geoH = [6, 12, 24, 48];
    if (c.geo.every_h && geoH.indexOf(c.geo.every_h) < 0) geoH.push(c.geo.every_h);
    const geoOpts = el("label", { class: "kk-eg-w", hidden: !c.geo.enabled }, el("span", { text: t("ep.geoEvery") }),
      U.select(geoH.sort((a, b) => a - b).map((h) => ({ v: String(h), l: t("ep.hours", { n: h }) })), String(c.geo.every_h || 24), (v) => { c.geo.every_h = Number(v); touchPreset(g); changed(gid); }, { aria: t("ep.geoEvery") }));
    const geo = swLive(t("ep.geo"), t("ep.geoD"), () => !!c.geo.enabled, (v) => { c.geo.enabled = v; geoOpts.hidden = !v; touchPreset(g); changed(gid); }, { last: true });
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.checks"), ""), el("span", { class: "kk-eg-l", text: t("ep.freq") }), freq,
      everyBox, freqHint, obsHint, restart, speedSw, speedOpts, geo, geoOpts);
  }
  // ── условия (SE §7.2): пересказ, вкл/выкл, удалить, «+ условие» из шаблонов и форма условия целиком ─────────────
  // метрика -> единицы и операторы (как policy.METRICS); service — только если у группы есть сервисы в проверках
  const METRIC_UI = {
    rtt: { units: ["ms", "x_norm"], ops: [">", "<"] }, loss: { units: ["pct"], ops: [">", "<"] }, jitter: { units: ["ms"], ops: [">", "<"] },
    speed: { units: ["mbps"], ops: ["<", ">"] }, traffic: { units: ["gb_day", "gb_month"], ops: [">", "<"] },
    country: { units: ["iso"], ops: ["!=", "=="] }, country_changed: { units: ["bool"], ops: ["=="] }, service: { units: ["ok"], ops: ["=="] },
  };
  const NUM_METRIC = (m) => ["rtt", "loss", "jitter", "speed", "traffic"].indexOf(m) >= 0;
  const holdText = (h) => (h && h.samples ? t("ep.holdN", { n: h.samples }) : t("ep.holdMin", { n: (h && h.minutes) || 1 }));
  function condText(c) {
    if (c.metric === "country_changed") return t("ep.cond.country_changed");
    if (c.metric === "service") return t(c.value ? "ep.cond.serviceOk" : "ep.cond.serviceBad", { s: c.service || "" });
    return tk("ep.cond.", c.metric, { op: c.op, v: c.value, u: c.unit === "iso" ? "" : tk("ep.unit.", c.unit) }).trim();
  }
  function ruleText(r) {
    const cs = (r.when && r.when.conds) || [];
    const what = cs.map(condText).join(r.when.match === "any" ? t("ep.or") : t("ep.and"));
    return t("ep.ruleText2", { what, hold: holdText(r.when.hold), then: tk("ep.then.", r.then) });
  }
  // период замера группы, с: «Лениво» — 15 (§3.2), «Своя» — every_s; для пересказа «≈ 30 с до ухода»
  // Период замера правила — как policy.p_nom ядра (С3): наибольший номинальный период среди видов выходов правила —
  // xray: период единой обсерватории узла (самая частая зарубежная группа с выходами, «Лениво» — 120 с); AWG: проба
  // отклика под трафиком max(3P, 30); российская группа и «Лениво» — 15 с; виды неизвестны — оба вида.
  const FREQ_P = { lazy: 15, often: 10, normal: 30, rare: 120 };   // policy.FREQ_P
  const LAZY_WAKE_MIN = 5;                                          // collect.LAZY_WAKE_S / 60
  function freqP(g) {
    const fr = (g && g.checks && g.checks.freq) || {};
    if (fr.mode === "custom") return typeof fr.every_s === "number" && fr.every_s > 0 ? Math.trunc(fr.every_s) : FREQ_P.often;
    if (g && g.role === "ru" && !(fr.mode in FREQ_P)) return 15;
    return FREQ_P[fr.mode] || FREQ_P.often;
  }
  const isLazy = (g) => !!(g && g.checks && g.checks.freq && g.checks.freq.mode === "lazy");
  const exitsOfG = (g) => [].concat(...((g && g.tiers) || []).map((tr) => tr.exits || []));
  const kindOf = (id) => { const r = rowOf(id); return !r ? null : r.kind === "xray" || r.kind === "vless" ? "xray" : r.kind === "awg" ? "awg" : null; };
  // общая проверка xray узла (policy.observatory_s): {s, gid} самой частой зарубежной группы с выходами; у группы g —
  // её черновик. observatoryS — только период (для pNom), observatoryOf — ещё и чья (строка «её задаёт группа «G»»)
  function observatoryOf(g) {
    const p = pol(), groups = p ? p.policy.groups.map((x) => (x.id === g.id ? g : x)) : [g];
    // как ядро с pool: выход без строки в списке выходов (удалён, ещё не в пуле) не считается; список не прочитан —
    // считаются все (ядро без pool)
    const known = exitsKnown();
    let best = null;
    groups.forEach((x) => {
      if ((x.role || "abroad") !== "abroad") return;
      const ids = exitsOfG(x).filter((id) => { const r = rowOf(id); return known ? !!r && (r.role || "abroad") === "abroad" : true; });
      if (!ids.length) return;
      const v = isLazy(x) ? 120 : freqP(x);
      if (best === null || v < best.s) best = { s: v, gid: x.id };
    });
    return best;
  }
  // строка «xray-выходы проверяются общей проверкой … её задаёт группа «G»»: только у зарубежной группы (ядро:
  // policy.period — у российской xray-выходы проверяются её частотой; REV-s1-t8m З-4), когда общую задаёт другая
  // группа и она чаще своей частоты группы
  function obsLineOf(g) {
    if (!g || (g.role || "abroad") !== "abroad") return null;
    const obs = observatoryOf(g), mine = isLazy(g) ? 120 : freqP(g);
    return obs && obs.gid !== g.id && mine > obs.s ? obs : null;
  }
  function observatoryS(g) { const o = observatoryOf(g); return o ? o.s : null; }
  function pNom(g, r) {
    if ((g && g.role === "ru") || isLazy(g)) return 15;
    const sel = r && r.exits !== undefined ? r.exits : "all";
    const ids = Array.isArray(sel) ? sel : exitsOfG(g);
    const want = new Set(ids.map(kindOf).filter(Boolean));
    if (sel && typeof sel === "object" && !Array.isArray(sel) && sel.kind) { want.clear(); want.add(sel.kind); }
    if (!want.size) { want.add("awg"); want.add("xray"); }
    const out = [];
    if (want.has("xray")) out.push(observatoryS(g) || freqP(g));
    if (want.has("awg")) out.push(Math.max(3 * freqP(g), 30));
    return Math.max(...out);
  }
  const holdS = (h, p) => (!h ? null : typeof h.minutes === "number" ? h.minutes * 60 : typeof h.samples === "number" ? h.samples * p : null);
  const durText = (sec) => (sec < 120 ? t("ep.aboutS", { n: Math.round(sec) }) : t("ep.aboutMin", { n: Math.round(sec / 60) }));
  function ruleTimes(r, g) {
    const p = pNom(g, r), ls = holdS(r.when.hold, p), bs = r.back && !r.back.manual ? holdS(r.back.hold, p) : null;
    const leave = ls !== null ? t("ep.leaveIn", { d: durText(ls) }) : "";
    const back = r.back && r.back.manual ? t("ep.backManual") : bs !== null ? t("ep.backIn", { d: durText(bs) }) : "";
    return [leave, back].filter(Boolean).join(", ");
  }
  // Ошибки формы условия — те же правила и в том же порядке, что у ядра (policy._validate_rule, _side_ok): {место:
  // текст}; есть ошибка — «Сохранить» неактивна. Место: when | when.<i> | when.hold | back | back.<i> | back.hold | exits.
  // Сверх ядра одно: пустой выбор выходов («для выходов: выбрать…» без галочек). Сверка с ядром — tests/test_panel_live_s1.
  function ruleErrors(r, g) {
    const e = {};
    const put = (k, v) => { if (!e[k]) e[k] = v; };
    const svc = ((g.checks && g.checks.services) || []).map((s) => s && s.id);
    const side = (sd, key) => {                          // -> false: дальше ядро не смотрит (неверная выдержка)
      const cs = (sd && sd.conds) || [];
      if (!Array.isArray(sd && sd.conds) || !cs.length || cs.length > 5) put(key, t("ep.err.conds"));
      cs.forEach((c, i) => {
        const k = key + "." + i, ui = METRIC_UI[c.metric], v = c.value;
        if (!ui) { put(k, t("ep.err.value")); return; }
        if (ui.ops.indexOf(c.op) < 0 && !(c.metric === "service" && c.op === "!=")) put(k, t("ep.err.value"));
        if (ui.units.indexOf(c.unit === undefined ? ui.units[0] : c.unit) < 0) put(k, t("ep.err.value"));
        if (c.metric === "service" || c.metric === "country_changed") { if (typeof v !== "boolean") put(k, t("ep.err.value")); }
        else if (c.metric === "country") { if (!(typeof v === "string" && /^[A-Z]{2}$/.test(v))) put(k, t("ep.err.iso")); }
        else if (!(typeof v === "number" && isFinite(v) && v >= 0 && (c.metric !== "loss" || v <= 100))) put(k, t("ep.err.value"));
        if (c.metric === "service" && svc.indexOf(c.service) < 0) put(k, t("ep.err.service"));
        if (c.metric === "loss" && c.min_fails !== undefined && !(Number.isInteger(c.min_fails) && c.min_fails >= 1 && c.min_fails <= 10)) put(k, t("ep.err.value"));
      });
      const h = sd && sd.hold;
      if (h !== undefined && h !== null) {
        const keys = ["samples", "minutes"].filter((x) => x in h);
        const ok = keys.length === 1 && ((Number.isInteger(h.samples) && h.samples >= 1 && h.samples <= 100) || (typeof h.minutes === "number" && isFinite(h.minutes) && h.minutes > 0 && h.minutes <= 1440));
        if (!ok) { put(key + ".hold", t("ep.err.hold")); return false; }
      }
      return true;
    };
    const sel = r.exits === undefined ? "all" : r.exits;
    if (Array.isArray(sel)) { const own = exitsOfG(g); if (!sel.length || sel.some((id) => own.indexOf(id) < 0)) put("exits", t("ep.err.exits")); }
    if (!side(r.when, "when")) return e;
    const back = r.back;
    if (back === null || back === undefined) {
      if (["degraded", "standby", "remove"].indexOf(r.then) >= 0) put("back", t("ep.err.back"));
      return e;
    }
    const manual = back.manual === true;
    if ((!manual || (back.conds || []).length) && !side(back, "back")) return e;
    if (r.then === "remove" && !manual && !back.hold) put("back.hold", t("ep.err.removeBack"));
    ((r.when && r.when.conds) || []).forEach((c) => {
      if (c.op !== ">" && c.op !== "<") return;
      (back.conds || []).forEach((b) => {
        if (b.metric !== c.metric || (b.unit === undefined ? c.unit : b.unit) !== c.unit || typeof b.value !== "number" || typeof c.value !== "number") return;
        const good = c.op === ">" ? b.op === "<" && b.value < c.value : b.op === ">" && b.value > c.value;
        if (!good) put("back", t("ep.err.backSide"));
      });
    });
    const p = pNom(g, r), ls = holdS(r.when.hold, p), bs = holdS(back.hold, p);
    if (ls !== null && bs !== null && bs < ls) put("back.hold", t("ep.err.backHold"));
    return e;
  }
  const BACK_KEPT = new WeakMap();                        // условие -> отложенные conds/hold возврата («Только вручную»)
  function setManual(r, on) {
    if (on) {
      if ((r.back.conds || []).length || r.back.hold) BACK_KEPT.set(r, { conds: r.back.conds || [], hold: r.back.hold });
      r.back = { match: r.back.match || "all", conds: [], manual: true };
      return;
    }
    const kept = BACK_KEPT.get(r);
    r.back = Object.assign({ match: r.back.match || "all" }, kept ? copy(kept) : { conds: [], hold: { minutes: 5 } }, { manual: false });
    if (!kept) {                                          // по первой проверке ухода, с хорошей стороны — как при «ухудшен»
      const c0 = r.when.conds[0];
      if (c0) { const bc = copy(c0); if (bc.op === ">" || bc.op === "<") { bc.op = c0.op === ">" ? "<" : ">"; if (typeof c0.value === "number") bc.value = Math.round(c0.value * (c0.op === ">" ? 0.7 : 1.5) * 10) / 10; } else if (typeof bc.value === "boolean") bc.value = !bc.value; r.back.conds = [bc]; }
    }
  }
  // ошибки условий на всю группу (policy.validate: проверок ухода во всех условиях группы — не больше 5)
  const whenConds = (g) => (g.rules || []).reduce((n, r) => n + ((r && r.when && Array.isArray(r.when.conds)) ? r.when.conds.length : 0), 0);
  function groupRuleErrors(g) { const n = whenConds(g); return n > 5 ? { rules: t("ep.err.condsGroup", { n }) } : {}; }
  const RULE_OPEN = Object.create(null);                 // gid -> id условия, у которого открыта форма
  function rulesBlock(gid) {
    const g = forms[gid].draft;
    const list = el("ul", { class: "kk-eg-rules" });
    if (!g.rules.length) list.append(el("li", { class: "kk-eg-empty", text: t("ep.noRules") }));
    g.rules.forEach((r, i) => {
      const bad = Object.keys(ruleErrors(r, g)).length > 0;
      // строка 40 (HANDOFF r3): переключатель · пересказ · «≈ … до ухода, … до возврата» · «Изменить» · ×; подпись —
      // <label> переключателя (нажатие по тексту переключает, как строка gx-sw раньше)
      const sid = "eg-rs-" + gid + "-" + i;
      const sw = el("button", { type: "button", class: "kk-eg-rule__sw", id: sid, role: "switch", "aria-checked": String(!!r.enabled), "aria-labelledby": sid + "-l" }, el("span", { class: "gx-track" }));
      sw.addEventListener("click", () => { r.enabled = !r.enabled; sw.setAttribute("aria-checked", String(r.enabled)); touchPreset(g); changed(gid); });
      const times = ruleTimes(r, g);
      const isOpen = RULE_OPEN[gid] === r.id;
      // открытое условие (владелец 29.10, экран Г): сроки «≈ … до ухода, ≈ … до возврата» — один раз, под формой;
      // фраза в строке — живая (правки формы карточку не пересобирают)
      const head = el("span", { text: ruleText(r) });
      const text = el("label", { class: "kk-eg-rule__t", id: sid + "-l", for: sid }, head, times && !isOpen ? el("span", { class: "kk-eg-rule__d", text: " · " + times }) : null);
      const edit = U.btn(t(isOpen ? "ep.ruleClose" : "ep.ruleEdit"), "ghost", () => { RULE_OPEN[gid] = isOpen ? null : r.id; renderCard(gid); }, { icon: isOpen ? "chevron-up" : "pencil" });
      edit.setAttribute("aria-expanded", String(isOpen));
      const del = U.btn("", "ghost", () => { g.rules.splice(i, 1); if (RULE_OPEN[gid] === r.id) RULE_OPEN[gid] = null; touchPreset(g); edited(gid); }, { icon: "xmark", tip: t("ep.ruleDel"), aria: t("ep.ruleDel") });
      list.append(el("li", { class: "kk-eg-rule" + (bad ? " is-bad" : ""), "data-hold-row": "egr:" + gid + ":" + i }, sw, text, el("span", { class: "kk-eg-rule__btns" }, edit, del)));
      if (isOpen) list.append(el("li", { class: "kk-eg-rulef" }, ruleForm(gid, r, (s) => { head.textContent = s; })));
    });
    const tpl = Object.keys(RULE_TPL).filter((k) => k !== "service" || ((g.checks && g.checks.services) || []).length).map((k) => ({ label: t("ep.tpl." + k), on: () => {
      if (g.rules.length >= 20) return;
      const r = RULE_TPL[k](g); g.rules.push(r); RULE_OPEN[gid] = r.id; touchPreset(g); edited(gid);
    } }));
    const add = U.btn(t("ep.addRule"), "tertiary", (ev, b) => K.openMenu(b, tpl, { label: t("ep.addRule") }), { icon: "plus" });
    add.setAttribute("aria-haspopup", "menu");
    const full = whenConds(g) >= 5;                        // у шаблона одна проверка ухода — шестая не пройдёт ядро
    if (full) { add.disabled = true; add.title = t("ep.err.condsFull"); }
    const gErr = groupRuleErrors(g).rules;
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.rules"), t("ep.rulesText")), list, gErr ? el("span", { class: "gx-hint gx-hint--bad kk-eg-err", role: "alert", text: gErr }) : null,
      el("div", { class: "kk-eg-acts" }, add, full ? el("span", { class: "gx-hint", text: t("ep.err.condsFull") }) : null));
  }
  // Форма условия (SE §7.2): «Если [все из ▼] … держится … → … Вернуть, когда … Для выходов … Сообщить в бот».
  // Правки — в черновике группы (полоса «Сохранить» группы); значения — без пересборки карточки (фокус остаётся в поле),
  // строки условий (+/×) и смена метрики — с пересборкой. Вид — экран Г «Условие фразой» (PROMPT-stage1-ui §Г, владелец
  // 29.10): колонка подписей и колонка полей, проверка — поля в строку и «×» справа, «Только вручную» — в строке
  // «Вернуть, когда», «Сообщить в бот» — в строке «Для выходов»; под фразой — только сроки. onText(фраза) — в строку.
  function ruleForm(gid, r, onText) {
    const g = forms[gid].draft;
    const errBox = {};                                    // место -> элемент текста ошибки
    const isoBack = [];                                   // поля кода страны в возврате (зеркало кода из ухода)
    const errEl = (key) => (errBox[key] = el("span", { class: "gx-hint gx-hint--bad kk-eg-err", role: "alert" }));
    const summary = el("div", { class: "gx-hint kk-eg-rsum" });
    const says = () => { const s = ruleTimes(r, g); summary.textContent = s; summary.hidden = !s; if (onText) onText(ruleText(r)); };
    const refresh = () => {
      const e = ruleErrors(r, g);
      Object.keys(errBox).forEach((k) => { errBox[k].textContent = e[k] || ""; errBox[k].hidden = !e[k]; });
      says();
      touchPreset(g); changed(gid);
    };
    const rebuild = () => { touchPreset(g); edited(gid); };
    const services = (g.checks && g.checks.services) || [];
    const metrics = Object.keys(METRIC_UI).filter((m) => m !== "service" || services.length);
    // строка фразы: подпись (колонка подписей) и поля; переключатель в конце строки — справа
    const rrow = (label, ...f) => el("div", { class: "kk-eg-rrow" }, el("span", { class: "kk-eg-l", text: label }), el("div", { class: "kk-eg-rrow__f" }, ...f.filter(Boolean)));
    // переключатель в строке (экран Г: «[ ] только вручную», «[✓] Сообщить в бот»); пояснение — подсказкой
    const swIn = (title, desc, get, set) => {
      const b = el("button", { type: "button", class: "kk-eg-rsw", role: "switch", "aria-checked": String(!!get()), title: desc }, el("span", { class: "gx-track" }), el("span", { text: title }));
      b.addEventListener("click", () => { const v = !get(); set(v); b.setAttribute("aria-checked", String(v)); });
      return b;
    };
    const matchSel = (sd) => U.select([{ v: "all", l: t("ep.matchAll") }, { v: "any", l: t("ep.matchAny") }], sd.match || "all", (v) => { sd.match = v; refresh(); }, { aria: t("ep.match") });
    function condRow(sd, key, c, i) {
      const ui = METRIC_UI[c.metric] || METRIC_UI.rtt;
      const metric = U.select(metrics.map((m) => ({ v: m, l: tk("ep.metric.", m) })), c.metric, (v) => {
        const u = METRIC_UI[v];
        Object.keys(c).forEach((k) => delete c[k]);
        Object.assign(c, { metric: v, op: u.ops[0], unit: u.units[0], value: v === "country" ? "" : v === "country_changed" ? true : v === "service" ? false : 0 });
        if (v === "service") c.service = services[0] ? services[0].id : "";
        rebuild();
      }, { aria: t("ep.metricL") });
      const parts = [metric];
      if (c.metric === "service") {
        parts.push(U.select(services.map((x) => ({ v: x.id, l: x.name || x.id })), c.service, (v) => { c.service = v; refresh(); }, { aria: t("ep.serviceL") }),
          U.select([{ v: "false", l: t("ep.svcBad") }, { v: "true", l: t("ep.svcOk") }], String(!!c.value), (v) => { c.value = v === "true"; refresh(); }, { aria: t("ep.svcL") }));
      } else if (c.metric !== "country_changed") {
        if (ui.ops.length > 1) parts.push(U.select(ui.ops.map((o) => ({ v: o, l: tk("ep.op.", o === ">" ? "gt" : o === "<" ? "lt" : o === "==" ? "eq" : "ne") })), c.op, (v) => { c.op = v; refresh(); }, { aria: t("ep.opL") }));
        if (c.metric === "country") {
          const inp = U.input({ value: c.value || "", aria: t("ep.isoL"), ph: "DE", max: 2 });
          inp.classList.add("kk-eg-iso");
          if (key === "back") isoBack.push({ c, inp });
          inp.addEventListener("input", () => {
            const was = c.value;
            c.value = inp.value.trim().toUpperCase();
            // «Страна не та»: код вводится один раз — возврат с тем же (пустым или прежним) кодом берёт новый
            if (key === "when") isoBack.forEach((b) => { if (b.c.metric === "country" && (!b.c.value || b.c.value === was)) { b.c.value = c.value; b.inp.value = c.value; } });
            refresh();
          });
          parts.push(inp);
        } else {
          const inp = U.input({ value: c.value, aria: t("ep.valueL"), type: "number", num: true, im: "decimal" });
          inp.classList.add("kk-eg-num");
          inp.addEventListener("input", () => { const v = Number(inp.value); c.value = inp.value === "" || !isFinite(v) ? null : v; refresh(); });
          parts.push(inp);
          if (ui.units.length > 1) parts.push(U.select(ui.units.map((u) => ({ v: u, l: tk("ep.unit.", u) })), c.unit, (v) => { c.unit = v; refresh(); }, { aria: t("ep.unitL") }));
          else parts.push(el("span", { class: "gx-desc", text: tk("ep.unit.", c.unit) }));
        }
      }
      // «×» у каждой проверки (экран Г); у единственной — неактивен: ядро требует от 1 до 5 проверок
      const one = sd.conds.length < 2;
      const del = U.btn("", "ghost", one ? null : () => { sd.conds.splice(i, 1); rebuild(); }, { icon: "xmark", tip: t(one ? "ep.err.conds" : "ep.condDel"), aria: t("ep.condDel"), dis: one });
      // перенос — между парами «метрика и оператор» / «значение и единица» (на узкой форме — две строки)
      const pair = (xs) => (xs.length ? el("span", { class: "kk-eg-cond__p" }, ...xs) : null);
      return el("div", { class: "kk-eg-cond" }, el("div", { class: "kk-eg-cond__f" }, pair(parts.slice(0, 2)), pair(parts.slice(2))), del, errEl(key + "." + i));
    }
    function condsBlock(sd, key) {
      const rowsN = sd.conds.map((c, i) => condRow(sd, key, c, i));
      const addC = sd.conds.length < 5 && (key !== "when" || whenConds(g) < 5) ? U.btn(t("ep.addCond"), "tertiary", () => { const m = sd.conds[0] ? sd.conds[0].metric : "rtt"; const u = METRIC_UI[m]; sd.conds.push({ metric: m, op: u.ops[0], value: 0, unit: u.units[0] }); rebuild(); }, { icon: "plus", cls: "kk-eg-add" }) : null;
      return [...rowsN, addC, errEl(key)];
    }
    function holdRow(sd, key) {
      const kind = sd.hold && sd.hold.samples ? "samples" : "minutes";
      const n = kind === "samples" ? sd.hold.samples : (sd.hold && sd.hold.minutes) || 1;
      const inp = U.input({ value: n, aria: t("ep.holdL"), type: "number", num: true, im: "numeric" });
      inp.classList.add("kk-eg-num");
      inp.addEventListener("input", () => { const v = Number(inp.value); sd.hold = kind === "samples" ? { samples: inp.value === "" ? 0 : v } : { minutes: inp.value === "" ? 0 : v }; refresh(); });
      const how = U.select([{ v: "samples", l: t("ep.holdSamples") }, { v: "minutes", l: t("ep.holdMinutes") }], kind, (v) => { sd.hold = v === "samples" ? { samples: Math.max(1, Math.round(n)) } : { minutes: n }; rebuild(); }, { aria: t("ep.holdHow") });
      return rrow(t("ep.holds"), inp, how, errEl(key + ".hold"));
    }
    const thenSel = U.select(["degraded", "standby", "remove", "push"].map((v) => ({ v, l: tk("ep.then.", v) })), r.then, (v) => {
      r.then = v;
      if (v === "push") r.back = null;
      else if (!r.back) {
        // возврат по первой проверке ухода — с хорошей стороны порога (для «>» — 70 % от него), держится дольше ухода
        const c0 = Object.assign({ metric: "rtt", op: ">", value: 300, unit: "ms" }, r.when.conds[0] || {});
        const bc = Object.assign({}, c0);
        if (c0.op === ">" || c0.op === "<") { bc.op = c0.op === ">" ? "<" : ">"; if (typeof c0.value === "number") bc.value = Math.round(c0.value * (c0.op === ">" ? 0.7 : 1.5) * 10) / 10; }
        else if (typeof c0.value === "boolean") bc.value = !c0.value;      // сервис, «страна сменилась»: оператор один — «==»
        else if (c0.op === "!=" || c0.op === "==") bc.op = c0.op === "!=" ? "==" : "!=";
        r.back = { match: "all", conds: [bc], hold: { minutes: Math.max(5, (r.when.hold && r.when.hold.minutes) || 0) }, manual: false };
      }
      rebuild();
    }, { aria: t("ep.thenL") });
    const kids = [
      rrow(t("ep.if"), matchSel(r.when)),
      ...condsBlock(r.when, "when"), holdRow(r.when, "when"),
      rrow(t("ep.then"), thenSel),
    ];
    if (r.then !== "push" && r.back) {
      const manual = !!r.back.manual;
      // «Только вручную» не шлёт условия возврата (ядро проверило бы и их, policy._validate_rule): они откладываются и
      // возвращаются, если снять галочку
      kids.push(rrow(t("ep.backWhen"), manual ? null : matchSel(r.back), swIn(t("ep.backManualL"), t("ep.backManualD"), () => !!r.back.manual, (v) => { setManual(r, v); rebuild(); })));
      if (!manual) kids.push(...condsBlock(r.back, "back"), holdRow(r.back, "back"));
      else kids.push(errEl("back"), errEl("back.hold"));
    }
    // «Для выходов»: все выходы этой группы | AWG | xray | выбрать… (SE §7.2)
    const exMode = Array.isArray(r.exits) ? "pick" : r.exits && r.exits.kind ? r.exits.kind : "all";
    const exSel = U.select([{ v: "all", l: t("ep.exAll") }, { v: "awg", l: "AmneziaWG" }, { v: "xray", l: "xray" }, { v: "pick", l: t("ep.exPick") }], exMode, (v) => {
      r.exits = v === "all" ? "all" : v === "pick" ? [].concat(...g.tiers.map((x) => x.exits)) : { kind: v };
      rebuild();
    }, { aria: t("ep.exL") });
    kids.push(rrow(t("ep.exL"), exSel, swIn(t("ep.pushL"), t("ep.pushD"), () => r.push !== false, (v) => { r.push = v; refresh(); })));
    if (Array.isArray(r.exits)) {
      const box = el("div", { class: "kk-eg-expick" });
      [].concat(...g.tiers.map((x) => x.exits)).forEach((id) => {
        const cb = el("input", { type: "checkbox", "aria-label": exitName(id) });
        cb.checked = r.exits.indexOf(id) >= 0;
        cb.addEventListener("change", () => { r.exits = cb.checked ? r.exits.concat([id]) : r.exits.filter((x) => x !== id); refresh(); });
        box.append(el("label", { class: "kk-eg-expick__i" }, cb, el("span", { text: exitName(id) })));
      });
      kids.push(box, errEl("exits"));
    }
    kids.push(summary);
    const n = el("div", { class: "kk-eg-rform", role: "group", "aria-label": t("ep.ruleForm") }, ...kids.filter(Boolean));
    // ошибки и пересказ — сразу (без пересборки)
    setTimeout(() => { const e = ruleErrors(r, g); Object.keys(errBox).forEach((k) => { errBox[k].textContent = e[k] || ""; errBox[k].hidden = !e[k]; }); says(); }, 0);
    return n;
  }
  // «Удалить группу» несохранёнными правками не блокируется (круг 1 №29б): окно удаления говорит, что они отменятся
  // кнопки «сразу» над группами: выключены, пока у группы несохранённые правки (обновить без пересборки карточек)
  function refreshButtons() {
    document.querySelectorAll("[data-eg-add]").forEach((b) => { const w = unsavedWhy(b.dataset.egAdd); b.disabled = !!w; b.title = w; });
    document.querySelectorAll("[data-eg-std]").forEach((b) => { const w = unsavedWhy(b.dataset.egStd); b.disabled = !!w; b.title = w; });
    document.querySelectorAll("[data-eg-move]").forEach((b) => { const r = rowOf(b.dataset.egMove); const w = (r && unsavedWhy(r.group)) || exUnsaved(b.dataset.egMove); b.disabled = !!w; b.title = w; });
    document.querySelectorAll("[data-eg-more]").forEach((b) => { const f = forms[b.dataset.egMore]; if (f) { if (freqValid(f.draft)) b.removeAttribute("aria-disabled"); else b.setAttribute("aria-disabled", "true"); } });
    syncAccent();
    actionsLive.forEach((ca) => ca.refresh());
  }
  // Одна акцентная кнопка на экран (UX-аудит 20.10, «Акценты»): правки в раскрытой форме — акцент у «Сохранить» её
  // полосы; иначе плашка «Появились группы» — у «Включить «Стандарт»»; иначе — у «+ Группа». Остальные — обычные
  const hintShown = () => { const p = pol(), m = p ? p.policy.groups.find((g) => g.id === "main") : null; return !!(p && !p.policy.hint_seen && m && m.preset === "compat"); };
  const editShown = () => Object.keys(forms).some((gid) => open[gid] === true && dirtyOf(gid)) || (speedForm.open && speedDirty());
  const accentOf = (which) => (editShown() ? "save" : hintShown() ? "hint" : "add") === which;
  function syncAccent() {
    document.querySelectorAll("[data-eg-acc]").forEach((b) => { const on = accentOf(b.dataset.egAcc); b.classList.toggle("ga-btn--primary", on); b.classList.toggle("ga-btn--tertiary", !on); });
  }
  const actionsLive = new Set();
  const track = (ca) => { actionsLive.add(ca); return ca; };

  // ── окна: новая группа, удаление группы, «Добавить выход…», «Перенести в группу…» ─────────────────────────────
  // «+ Группа» (SPEC-groups §8.1; кадр 1.7): имя → роль радио без номеров «Зарубежная / Российская» (роль задаётся при
  // создании и потом не меняется) → у российской сервер сразу ставит набор «Дом» и последний шаг «с сервера напрямую».
  // role0 — роль по умолчанию (из части «Российские группы» — российская)
  function addGroupDialog(role0) {
    const st = { role: role0 === "ru" ? "ru" : "abroad" };
    const inp = U.input({ max: 64, aria: t("ep.groupName"), ph: t(st.role === "ru" ? "ep.groupNamePhRu" : "ep.groupNamePh") });
    const err = el("div", { class: "kk-dlg-err", role: "alert" });
    const ruNote = el("div", { class: "kk-rsnote", hidden: st.role !== "ru" }, K.icon("circle-info", 14), el("span", { text: t("ep.roleRuNote") }));
    const role = radioLive([{ v: "abroad", title: t("ep.roleAbroad"), desc: t("ep.roleAbroadD") },
      { v: "ru", title: t("ep.roleRu"), desc: t("ep.roleRuD"), chips: [K.domMark(12, "gx-chip gx-chip--tag")] }], () => st.role,
    (v) => { st.role = v; ruNote.hidden = v !== "ru"; inp.placeholder = t(v === "ru" ? "ep.groupNamePhRu" : "ep.groupNamePh"); }, { aria: t("ep.role") });
    const d = K.openDialog({ icon: "pool", size: "560px", title: t("ep.newGroup"), sub: t("ep.newGroupSub"),
      blocks: [U.field(t("ep.groupName"), inp), el("span", { class: "kk-eg-l", text: t("ep.role") }), role, el("span", { class: "gx-hint", text: t("ep.roleOnce") }), ruNote, err] });
    const go = async (dup) => {
      const name = inp.value.trim();
      if (!name) { inp.setAttribute("aria-invalid", "true"); err.textContent = t("kit.nameEmpty"); inp.focus(); return; }
      try {
        await K.API.groups.add({ name, role: st.role, allow_duplicate: dup || undefined, rev: readRev() });
        d.close(); K.toast(t("ep.groupAdded", { g: name }), "", "success"); reread();
      } catch (e) {
        const k = Kit.errKind(e);
        if (k === "dup") { err.replaceChildren(el("span", { text: t("kit.dupQ", { name }) }), U.btn(t("kit.dupYes"), "tertiary", () => go(true))); return; }
        if (k === "changed") { await reread(); err.textContent = t("kit.recount"); return; }
        err.textContent = k === "busy" ? t("kit.busy") : (e && e.message) || t("kit.failed");
      }
    };
    inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); go(false); } });
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true }), U.btn(t("ep.createGroup"), "primary", () => go(false), { md: true })]);
    setTimeout(() => inp.focus(), 30);
  }
  // «Удалить группу» (SE §7.1, D-077; SPEC-groups §8.5): что спросить, говорит сервер — прогон без параметров отвечает
  // 409 «need» (выходы; люди, устройства и маршрут узла с этой группой; прокси бота, новые ключи, подписки). Окно
  // показывает только нужные выборы и шлёт только их; появилось новое (кто-то сослался на группу, пока окно открыто) —
  // выбор дорисовывается и прогон повторяется. Людям «напрямую» не предлагается: это трафик мимо выходов.
  const isNeed = (e) => !!e && e.status === 409 && e.code === "need";
  const isRuLast = (e) => !!e && e.status === 409 && e.code === "ru_last" && !!e.data && Array.isArray(e.data.exits) && e.data.exits.length > 0;
  function mergeNeed(a, b) {
    const out = Object.assign({}, a || {}, b || {});
    out.uses = [].concat((a && a.uses) || [], (b && b.uses) || []).filter((u, i, all) => all.indexOf(u) === i);
    return out;
  }
  // параметры DELETE по ответу «need»: только нужные (subs_to лишний раз не берёт замок подписок)
  function delParams(need, st) {
    const n = need || {}, uses = n.uses || [], o = {};
    if (st.drop) o.exits = "drop";                    // решение 8а: последняя российская — вместе с ключами
    else if (n.exits) o.exits_to = st.to;
    if (n.refs) o.reassign = st.reassign;
    if (uses.indexOf("tg_proxy") >= 0) o.tg_proxy_to = st.to;
    if (uses.indexOf("new_exits") >= 0) o.new_exits_to = st.to;
    if (uses.indexOf("subs") >= 0) o.subs_to = st.to;
    return o;
  }
  function delGroupDialog(gid) {
    const p = pol(), g = groupOf(gid);
    if (!p || !g) return;
    const ru = (g.role || "abroad") === "ru";
    const same = p.policy.groups.filter((x) => x.id !== gid && (x.role || "abroad") === (g.role || "abroad"));
    const abroad = p.policy.groups.filter((x) => x.id !== gid && (x.role || "abroad") !== "ru");
    // кого перевести: у зарубежной — в зарубежную группу; у российской (выбор «Российские сайты», ru_via) — в другую
    // российскую или «с сервера напрямую» (ядро: reassign той же роли или direct)
    const refsTo = ru ? same.map((x) => ({ v: x.id, l: groupLabel(x.id) })).concat([{ v: "direct", l: t("ep.delRuDirect") }]) : abroad.map((x) => ({ v: x.id, l: groupLabel(x.id) }));
    const st = { to: same.length ? same[0].id : "main", reassign: refsTo.length ? refsTo[0].v : "main", need: null, err: "", drop: null };
    const fields = el("div", { class: "kk-stack" });
    // несохранённые правки группы (полоса «Изменений: N») отменятся вместе с ней — сказать до удаления (круг 1 №29б)
    const dirty = dirtyOf(gid) ? U.al("warning", t("ep.delDirty", { g: groupLabel(gid) }), "") : null;
    // у российской — свой текст: её ключи встают шагами другой российской группы, а не «на последний уровень» (круг 1)
    const lead = U.p(t(ru ? "ep.delGroupTextRu" : "ep.delGroupText", { g: groupLabel(gid) }));
    const d = K.openDialog({ icon: "trash-bin", title: t("ep.delGroupTitle", { g: groupLabel(gid) }), blocks: [lead, dirty, fields].filter(Boolean) });
    const drawFields = () => {
      const n = st.need, kids = [];
      // последняя российская группа с ключами (решение владельца 8а): удаляется вместе с ключами одним действием —
      // в окне список ключей (каждый можно открыть) и что будет: ключи уходят с сервера, российские сайты у выбравших
      // группу — с сервера напрямую. Подписка группы держит её ключи — сначала удалить подписку (сервер: need uses subs)
      if (st.drop) {
        lead.textContent = t("ep.delRuDropLead", { g: groupLabel(gid) });
        kids.push(U.al("warning", t("ep.delRuLastT", { g: groupLabel(gid) }), t("ep.delRuDropX")));
        kids.push(el("div", { class: "kk-dlg3__sec" }, el("span", { class: "kk-dlg3__h", text: K.tn("ep.delRuKeysN", st.drop.length) }),
          ...st.drop.map((id) => el("div", { class: "kk-dlg3__k" }, K.icon("router", 15), el("span", { class: "kk-dlg3__kn", text: t("ep.delRuKey", { name: exitName(id) }) }),
            U.btn(t("ep.delRuOpen"), "tertiary", () => { d.close(); openExit(id); }, { aria: t("ep.delRuOpenName", { name: exitName(id) }) })))));
        if (n && (n.uses || []).indexOf("subs") >= 0) kids.push(U.al("danger", t("ep.delRuSubs"), ""));
        else if (!n && st.err) kids.push(U.al("danger", st.err, "", { btns: [U.btn(t("kit.retry"), "tertiary", () => { st.err = ""; drawFields(); learn(); })] }));
        else if (!n) kids.push(el("div", { class: "gx-hint", role: "status", text: t("kit.counting") }));
      } else if (!n) kids.push(st.err ? U.al("danger", st.err, "", { btns: [U.btn(t("kit.retry"), "tertiary", () => { st.err = ""; drawFields(); learn(); })] }) : el("div", { class: "gx-hint", role: "status", text: t("kit.counting") }));
      else {
        const uses = n.uses || [];
        if (n.exits || uses.length) {
          kids.push(U.field(t("ep.delGroupTo"), U.select(same.map((x) => ({ v: x.id, l: groupLabel(x.id) })), st.to, (v) => { st.to = v; ca.refresh(); }, { aria: t("ep.delGroupTo") }),
            { hint: [n.exits ? K.tn("ep.delMovesN", n.exits.length) : "", uses.indexOf("tg_proxy") >= 0 ? t("ep.delTg") : "", uses.indexOf("new_exits") >= 0 ? t("ep.delNew") : "",
              uses.indexOf("subs") >= 0 ? t("ep.delSubs") : ""].filter(Boolean).join(" ") }));
        }
        if (n.refs) {
          const r = n.refs;
          kids.push(U.field(t("ep.delPeopleTo"), U.select(refsTo, st.reassign, (v) => { st.reassign = v; }, { aria: t("ep.delPeopleTo") }),
            { hint: [t("ep.delRefs", { u: (r.users || []).length, d: (r.devices || []).length }), r["default"] ? t("ep.delRefsDefault") : ""].filter(Boolean).join(" ") }));
        }
      }
      fields.replaceChildren(...kids);
      ca.refresh();
    };
    // последняя российская: второй прогон уже с exits=drop — что ещё нужно (кого перевести, подписка). ask=1 (пачка А):
    // «чего не хватает» сервер отвечает 200 {ask: {code, data}}, а не 409 — пробное удаление не пишет ошибку в консоль
    // браузера; ответ 409 прежнего вида тоже понятен (isNeed, isRuLast)
    const learnFrom = (e) => {
      if (isNeed(e)) st.need = e.data || {};
      else if (isRuLast(e) && !st.drop) { st.drop = e.data.exits.map(String); st.reassign = "direct"; drawFields(); return learn(); }
      else st.err = (e && e.message) || t("kit.failed");
      drawFields();
      return null;
    };
    const learn = () => K.API.groups.del(gid, Object.assign(st.drop ? { dry: 1, exits: "drop" } : { dry: 1 }, { ask: 1 })).then((res) => {
      const a = res && res.ask;
      if (a && (a.code === "need" || a.code === "ru_last")) return learnFrom({ status: 409, code: a.code, data: a.data || {} });
      st.need = {}; drawFields();
      return null;
    }, learnFrom);
    // вызов с параметрами: сервер просит ещё (ссылка появилась, пока окно открыто) — выбор дорисовывается
    const call = (extra) => K.API.groups.del(gid, Object.assign(delParams(st.need, st), extra)).catch((e) => {
      if (!isNeed(e)) throw e;
      st.need = mergeNeed(st.need, e.data); drawFields();
      const x = new Error(t("ep.delNeedMore")); x.status = 409; x.code = "object_changed"; x.data = {};
      throw x;
    });
    const label = () => t(st.drop ? "ep.delGroupKeys" : "ep.delGroup");
    const ca = Kit.confirmAction({ get label() { return label(); }, anywayLabel: t("ep.delGroupLow"), kind: "dsoft", md: true, del: true,
      // выходы уезжают в группу «Куда перенести» — как окно переноса (moveDialog): у группы-цели или у выхода группы
      // несохранённые правки (квота в листе выхода) — сначала сохранить или отменить
      dis: () => (!st.need ? t("kit.counting") : st.drop ? ((st.need.uses || []).indexOf("subs") >= 0 ? t("ep.delRuSubs") : st.drop.map(exUnsaved).find(Boolean) || "")
        : (st.need.exits && st.need.exits.length ? unsavedWhy(st.to) || st.need.exits.map(exUnsaved).find(Boolean) || "" : "")), readRev, reread,
      dry: () => call({ dry: 1 }).catch((e) => (Kit.errKind(e) === "changed" && st.need ? call({ dry: 1 }) : Promise.reject(e))),
      run: (rev) => call({ rev }),
      // у удаления с ключами — строка «Удалятся ключи: …» до строк «кто куда»
      impact: (res) => impact(st.drop ? [t("ep.delDropLine", { list: st.drop.map((id) => "«" + exitName(id) + "»").join(", ") })] : [])(res),
      onDone: (res, how) => { reread(); if (how === "ok" || how === "gone") { delete forms[gid]; d.close(); if (how === "ok") K.toast(t(st.drop ? "ep.groupDeletedKeys" : "ep.groupDeleted", { g: groupLabel(gid) }), "", "success"); } } });
    d.body.append(ca.el);
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true })]);
    const draw0 = drawFields;
    // подпись кнопки — «Удалить группу и ключи», когда стало известно, что это последняя российская
    st.redraw = () => { draw0(); const b = ca.el && ca.el.querySelector ? ca.el.querySelector("button") : null; if (b && st.drop && b.textContent === t("ep.delGroup")) b.lastChild.textContent = label(); };
    drawFields();
    learn().then(() => st.redraw && st.redraw());
  }
  // «Добавить выход…» в группу gid — выбор выхода другой группы; это перенос (POST …/move, сразу, с подтверждением)
  function addExitDialog(gid) {
    const p = pol();
    if (!p) return;
    // кандидаты — выходы других групп той же роли: российский ключ в зарубежную группу (и наоборот) — только через
    // «Перенести в группу…» с предупреждением о смене роли (сервер без confirm_role отвечает 409)
    const ru = (groupOf(gid) || {}).role === "ru";
    const cand = rows().filter((r) => r.group && r.group !== gid && r.hasPolicy !== false && (r.role === "ru") === ru);
    const st = { id: null };
    const ca = track(Kit.confirmAction({ label: t("ep.addHere", { g: groupLabel(gid) }), anywayLabel: t("ep.addHereLow"), md: true, kind: "tertiary",
      dis: () => (!st.id ? t("ep.pickExit") : unsavedWhy(gid, rowOf(st.id) && rowOf(st.id).group)), readRev, reread,
      dry: () => K.API.exits.move(st.id, { group: gid }, true), run: (rev) => K.API.exits.move(st.id, { group: gid, rev }),
      impact: impact([t("ep.pinsMove")]), onDone: (res, how) => { reread(); if (how === "ok") { d.close(); K.toast(t("ep.moved", { name: exitName(st.id), g: groupLabel(gid) }), "", "success"); } } }));
    const list = cand.length ? radioLive(cand.map((r) => {
      const why = unsavedWhy(r.group);
      return { v: r.id, title: r.name, desc: t("ep.fromGroup", { g: groupLabel(r.group) }) + (why ? " · " + why : ""), chips: [stateChip(r)], dis: !!why };
    }), () => st.id, (v) => { st.id = v; ca.refresh(); }, { aria: t("ep.addExit") }) : U.p(t("ep.noCandidates"));
    const d = K.openDialog({ icon: "plus", title: t("ep.addExitTitle", { g: groupLabel(gid) }), blocks: [U.p(t("ep.addExitText")), list, ca.el], onClose: () => actionsLive.delete(ca) });
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true })]);
  }
  // «Перенести в группу…» (T15 Q6, кадр 3.2): группы своей роли первыми, другой роли — ниже своим списком; своя
  // группа — «сейчас здесь, шаг 1», выбрать нельзя. Группа другой роли — жёлтое предупреждение о смене роли (состояние,
  // история и ожидаемая страна выхода начнутся заново, policy_ops.exit_move) и confirm_role: сервер без него отвечает
  // 409 даже на прогон «что изменится»
  function moveDialog(id) {
    const r = rowOf(id), p = pol();
    if (!r || !p) return;
    const cur = groupOf(r.group), mine = cur ? cur.role || "abroad" : (r.role === "ru" ? "ru" : "abroad");
    const st = { to: null };
    const toRole = () => { const g = st.to ? groupOf(st.to) : null; return g ? g.role || "abroad" : mine; };
    const body = () => (toRole() !== mine ? { group: st.to, confirm_role: true } : { group: st.to });
    const warn = el("div", { class: "kk-stack", "aria-live": "polite" });
    const drawWarn = () => {
      if (!st.to || toRole() === mine) { warn.replaceChildren(); return; }
      const g = groupLabel(st.to), word = (x) => t(x === "ru" ? "ep.roleWordRu" : "ep.roleWordAbroad");
      warn.replaceChildren(U.al("warning", t("ep.roleChangeT", { from: word(mine), to: word(toRole()) }),
        [t("ep.roleChangeReset", { name: r.name }), t(toRole() === "ru" ? "ep.roleChangeToRu" : "ep.roleChangeToAbroad", { g })].join(" ")));
    };
    const ca = track(Kit.confirmAction({ label: t("ep.moveBtn"), anywayLabel: t("ep.moveBtnLow"), md: true, kind: "tertiary",
      dis: () => (!st.to ? t("ep.pickGroup") : unsavedWhy(r.group, st.to) || exUnsaved(id)), readRev, reread,
      dry: () => K.API.exits.move(id, body(), true), run: (rev) => K.API.exits.move(id, Object.assign(body(), { rev })),
      impact: impact([t("ep.pinsMove")]), onDone: (res, how) => { reread(); if (how === "ok") { d.close(); K.toast(t("ep.moved", { name: r.name, g: groupLabel(st.to) }), "", "success"); } } }));
    const here = (g) => { const i = g.tiers.findIndex((x) => x.id === r.tier); return t("ep.moveHere", { s: i < 0 ? "" : g.role === "ru" ? t("ep.ru.stepN", { n: i + 1 }) : tierLabel(g.tiers[i], i) }).replace(/,\s*$/, ""); };
    const btns = [];
    const blocks = (mine === "ru" ? ["ru", "abroad"] : ["abroad", "ru"]).map((role) => {
      const gs = p.policy.groups.filter((g) => (g.role || "abroad") === role);
      if (!gs.length) return null;
      const items = gs.map((g) => {
        const why = g.id === r.group ? "" : unsavedWhy(g.id), n = exitsOfGroup(g).length;
        return { v: g.id, title: groupLabel(g.id), desc: g.id === r.group ? here(g) : why || (n ? K.tn("ep.exitsN", n) : t("ep.ru.noExits")), dis: g.id === r.group || !!why,
          chips: role === "ru" ? [K.domMark(12, "gx-chip gx-chip--tag")] : [] };
      });
      const list = U.radio(items.map((it) => ({ title: it.title, desc: it.desc, chips: it.chips, on: false, dis: it.dis, pick: () => {} })), { aria: t(role === "ru" ? "ep.moveRu" : "ep.moveAbroad") });
      Array.from(list.children).forEach((b, i) => {
        btns.push(b);
        b.addEventListener("click", () => {
          if (items[i].dis) return;
          st.to = items[i].v;
          btns.forEach((x) => x.setAttribute("aria-checked", String(x === b)));
          drawWarn(); ca.refresh();
        });
      });
      return el("div", { class: "kk-stack" }, el("span", { class: "kk-eg-l", text: t(role === "ru" ? "ep.moveRu" : "ep.moveAbroad") }), list);
    }).filter(Boolean);
    const any = p.policy.groups.some((g) => g.id !== r.group);
    const d = K.openDialog({ icon: "split", size: "560px", title: t("ep.moveTitle", { name: r.name }), sub: t("ep.moveText"),
      blocks: any ? blocks.concat([warn, ca.el]) : [U.p(t("ep.noTargets"))], onClose: () => actionsLive.delete(ca) });
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true })]);
  }

  // «Убрать из группы» (круг 1 №29а). Выход всегда в какой-то группе (policy.place_new: зарубежный без группы сам
  // встаёт в «Основную», российский без группы трафика не получает): из зарубежной группы — перенос в «Основную»
  // (POST …/move, сразу, с подтверждением и сухим прогоном — как «Перенести в группу…»); из «Основной» и из российской
  // группы убрать некуда — окно говорит это и ведёт к переносу или к карточке ключа («Удалить ключ»)
  function removeDialog(id, gid) {
    const g = groupOf(gid), r = rowOf(id), name = exitName(id);
    if (!g) return;
    const ru = (g.role || "abroad") === "ru";
    if (gid === "main" || ru) {
      const d = K.openDialog({ icon: "split", title: t("ep.removeTitle", { name }), blocks: [U.p(t(ru ? "ep.removeRu" : "ep.removeMain", { g: groupLabel("main") }))] });
      d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true }),
        U.btn(t("ep.details"), "tertiary", () => { d.close(); openExit(id); }, { md: true }),
        U.btn(t("ep.moveTo"), "primary", () => { d.close(); moveDialog(id); }, { md: true, dis: !r })]);
      return;
    }
    const ca = track(Kit.confirmAction({ label: t("ep.removeBtn"), anywayLabel: t("ep.removeBtnLow"), md: true, kind: "tertiary",
      dis: () => unsavedWhy(gid, "main") || exUnsaved(id), readRev, reread,
      dry: () => K.API.exits.move(id, { group: "main" }, true), run: (rev) => K.API.exits.move(id, { group: "main", rev }),
      impact: impact([t("ep.pinsMove")]), onDone: (res, how) => { reread(); if (how === "ok") { d.close(); K.toast(t("ep.moved", { name, g: groupLabel("main") }), "", "success"); } } }));
    const d = K.openDialog({ icon: "split", title: t("ep.removeTitle", { name }), blocks: [U.p(t("ep.removeText", { g: groupLabel(gid), main: groupLabel("main") })), ca.el],
      onClose: () => actionsLive.delete(ca) });
    d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(), { md: true })]);
  }

  // ── карточка «Тест скорости — общее на сервер» (своя форма с ver, без сухого прогона; SE §7.1, §7.9) ─────────────
  const SPEED_FIELDS = [["max_mb", 1, 100], ["max_s", 2, 30], ["budget_day_mb", 10, 10000], ["budget_month_gb", 1, 1000]];
  const speedForm = { base: null, draft: null, ver: null, sb: null, draw: null, err: "", open: false, loading: false, list: [], bad: {} };
  // несохранённое (вопрос при уходе, «Сохранить» вкладки): правки, красное поле (число вне границ черновик не меняет)
  // и список «Применить снова» после 409 — как count/dirty() его полосы
  const speedDirty = () => !!speedForm.base && (!same(speedForm.base, speedForm.draft) || Object.keys(speedForm.bad || {}).length > 0
    || (speedForm.list || []).length > 0);
  // круг 1 Т1: карточка живёт в «Настройки › Проверка сервера» (app.js берёт её экспортом части); там раскрыта сразу
  function speedCard(o) {
    if (o && o.open && !speedForm.open) { speedForm.open = true; if (!speedForm.base) loadSpeed(() => (speedForm.draw ? speedForm.draw() : null)); }
    const box = el("section", { class: "gx-card kk-eg-card", "data-find": "settings.speedtest" });
    // полоса «Сохранить» одна на форму и живёт дольше карточки (вкладка собирается заново при каждом входе) — она
    // зовёт перерисовку текущей карточки (speedForm.draw), а не той, при которой создана (REV-s1-t8-2 С-1)
    const draw = () => {
      const s = speedForm;
      if (s.draw !== draw && s.draw && !box.isConnected) { s.draw(); return; }
      // свёрнутой строкой, как группы (UI-A2): шапка-кнопка, тело — под ней
      const head = foldHead({ key: "egh:speed", icon: "chart-column", title: t("ep.speedCard"), meta: t("ep.speedCardD"), open: s.open, controls: "eg-b-speed",
        onToggle: () => { s.open = !s.open; if (s.open && !s.base) loadSpeed(draw); draw(); focusIn(box, ".kk-eg-tog"); syncAccent(); } });
      box.classList.toggle("is-open", s.open);
      const kids = [];
      if (s.open) {
        if (s.err) kids.push(U.al("danger", s.err, ""));
        else if (!s.draft) kids.push(U.skel(2));
        else {
          // красное поле (число вне границ черновик не меняет) помнит набранное: карточку свернули и раскрыли или
          // собрали заново — набранное и красная рамка на месте, «Сохранить» неактивна (REV-s1-t8m З-2)
          s.bad = s.bad || {};
          SPEED_FIELDS.forEach(([k, min, max]) => {
            const raw = Object.prototype.hasOwnProperty.call(s.bad, k);
            const inp = numIn(raw ? s.bad[k] : s.draft[k], min, max, t("ep.sp." + k), (v) => { if (v !== null) { s.draft[k] = v; delete s.bad[k]; } else s.bad[k] = inp.value; s.sb.update(); syncAccent(); });
            if (raw) inp.setAttribute("aria-invalid", "true");
            kids.push(U.field(t("ep.sp." + k), inp, { hint: t("ep.spH." + k) }));
          });
          kids.push(swLive(t("ep.sp.only_quiet"), t("ep.spH.only_quiet"), () => !!s.draft.only_quiet, (v) => { s.draft.only_quiet = v; s.sb.update(); syncAccent(); }, { last: true }));
          // число вне границ — правка (полоса видна) и «Сохранить» неактивна; шлются только изменённые ключи; 409 —
          // перекладка (Kit.rebaseModel): чужая правка других ключей — молча, тех же — свежие данные и «Применить снова»
          if (!s.sb) s.sb = Kit.saveBar({ count: () => Kit.dirtyKeys(s.base, s.draft, SPEED_KEYS).length + Object.keys(s.bad || {}).filter((k) => same(s.draft[k], s.base[k])).length,
            valid: () => !Object.keys(s.bad || {}).length,
            impact: () => [t("ep.spImpact")],
            onSave: () => { const b = { ver: s.ver }; Kit.dirtyKeys(s.base, s.draft, SPEED_KEYS).forEach((k) => { b[k] = s.draft[k]; }); return K.API.probesSpeed.put(b); },
            onCancel: () => { s.draft = copy(s.base); s.list = []; s.bad = {}; s.draw(); },
            onSaved: (res, how) => { if (how === "ok" && res) { s.base = pickSpeed(res); s.draft = copy(s.base); s.ver = res.ver; s.list = []; s.bad = {}; K.toast(t("ep.spSaved"), "", "success"); s.draw(); } },
            onConflict: () => loadSpeed(() => s.draw(), true).then((r) => (r && r.mode === "list" ? { mode: "list", listText: t("ep.spChanged") } : { mode: "silent" })),
            list: () => (s.list || []).map((ed) => ({ text: t("ep.editLine", { what: t("ep.sp." + ed.key) + " — " + (ed.key === "only_quiet" ? t(ed.mine ? "ep.on" : "ep.off") : ed.mine) }),
              apply: () => { s.draft = Kit.applyAgain(s.draft, ed); s.list = s.list.filter((x) => x !== ed); s.draw(); } })) });
          kids.push(s.sb.el);
          if (s.list && s.list.length) s.sb.setList(t("ep.spChanged")); else s.sb.update();
        }
      }
      box.replaceChildren(head, el("div", { class: "kk-eg-body kk-eg-body--form", id: "eg-b-speed", hidden: !s.open }, ...kids));
      syncAccent();
    };
    speedForm.draw = draw;
    draw();
    return box;
  }
  const pickSpeed = (j) => ({ max_mb: j.max_mb, max_s: j.max_s, only_quiet: !!j.only_quiet, budget_day_mb: j.budget_day_mb, budget_month_gb: j.budget_month_gb });
  const SPEED_KEYS = ["max_mb", "max_s", "only_quiet", "budget_day_mb", "budget_month_gb"];
  function loadSpeed(draw, keepDraft) {
    return K.API.probesSpeed.get().then((j) => {
      const fresh = pickSpeed(j), sf = speedForm;
      let r = { mode: "silent" };
      sf.ver = j.ver; sf.err = "";
      if (keepDraft && sf.draft && sf.base) {
        r = Kit.rebaseModel(sf.base, sf.draft, fresh, Kit.dirtyKeys(sf.base, sf.draft, SPEED_KEYS));   // поля независимы
        sf.draft = r.draft; sf.list = r.mode === "list" ? r.edits : [];
      } else { sf.draft = copy(fresh); sf.list = []; sf.bad = {}; }
      sf.base = fresh; draw();
      return r;
    }, (e) => { speedForm.err = (e && e.message) || t("kit.failed"); draw(); return null; });
  }

  // ── вкладка «Группы» ───────────────────────────────────────────────────────────────────────────────────────────
  let cardsBox = null;
  const isRuForm = (gid) => !!forms[gid] && (forms[gid].base.role || "abroad") === "ru";
  // карточки своей роли: группы политики и формы с правками, удалённые в другом месте («Уже удалено», до «Закрыть»)
  function cardsOf(p, ru) {
    syncForms();
    const ids = p.policy.groups.map((g) => g.id);
    Object.keys(forms).forEach((gid) => { if (ids.indexOf(gid) < 0 && !forms[gid].gone) delete forms[gid]; });
    // группы свёрнуты, пока их не раскрыли (UI-A2: список групп — в один экран 1440×900); раскрытые — помнит open
    const kids = p.policy.groups.filter((g) => ((g.role || "abroad") === "ru") === ru).map((g) => { formOf(g, p.rev); return groupCard(g.id); });
    Object.keys(forms).filter((gid) => ids.indexOf(gid) < 0 && forms[gid].gone && isRuForm(gid) === ru).forEach((gid) => kids.push(groupCard(gid)));
    return kids;
  }
  // обе части: «Группы» (зарубежные) и «Российские группы» — какая на экране
  function renderAll() {
    const p = pol();
    if (!p) return;
    if (cardsBox) cardsBox.replaceChildren(...cardsOf(p, false).concat([ruLinkRow()]).filter(Boolean));
    renderRuCards();
  }
  // «Российские группы» — строкой-ссылкой внизу «Групп» (макет it1 Д3 Г1–Г3): число и наборы, переход в свою часть
  function ruLinkRow() {
    const gs = ruGroups();
    if (!gs.length) return null;
    const sets = gs.map((g) => (g.preset === "ru_home" ? t("ep.preset.ru_home") : t("ep.preset.custom"))).filter((x, i, a) => a.indexOf(x) === i).map((x) => "«" + x + "»").join(", ");
    const a = el("a", { class: "gx-card kk-g2__ru", href: "#exits?tab=ru", "data-find": "exits.groups.ruLink" },
      K.domMark(18, "kk-flag", { deco: true }),
      el("span", { class: "kk-grp__b" }, el("span", { class: "kk-g2__rt" }, el("b", { text: t("ep.ruLinkT") }), U.chip(gs.length + " · " + sets, "", { nodot: true })),
        el("span", { class: "kk-g2__rs", text: t("ep.ruLinkD") })), K.icon("chevron-right", 16));
    a.addEventListener("click", (ev) => { ev.preventDefault(); K.goNav("exits", { tab: "ru" }); });
    return a;
  }
  function build(root) {
    cardsBox = el("div", { class: "kk-eg-cards" });
    const top = K.blk(() => ({ st: K.R.policy.loaded ? (K.R.policy.missing ? "missing" : "ready") : K.R.policy.error ? "error" : "loading",
      rev: pol() ? pol().rev : null, sig: pol() ? JSON.stringify(pol().policy) : "", ex: rows().map((r) => [r.id, r.name, r.pstate, r.group, r.tier, r.rttMs, r.enabled]),
      rk: ranksSig() }), (v) => {
      if (v.st === "loading") return K.Tpl.state("loading", { rows: 3, card: true });
      if (v.st === "error") return K.Tpl.state("error", { text: K.R.policy.error ? K.R.policy.error.message : "", onRetry: () => K.loadAll(["policy"]) });
      if (v.st === "missing") return K.Tpl.state("empty", { icon: "pool", title: t("ep.missingT"), text: t("ep.missing") });
      const p = pol();
      const out = [];
      const main = p.policy.groups.find((g) => g.id === "main");
      if (!p.policy.hint_seen && main && main.preset === "compat") {
        // «Что изменится» помнит hintMoreOpen: живая перерисовка (отклик выходов) не прячет открытый текст
        const more = el("div", { class: "gx-hint", id: "eg-hint-more", hidden: !hintMoreOpen, text: t("ep.hintMore") });
        const why = U.btn(t("kit.what"), "ghost", () => { hintMoreOpen = !hintMoreOpen; more.hidden = !hintMoreOpen; why.setAttribute("aria-expanded", String(hintMoreOpen)); });
        why.setAttribute("aria-expanded", String(hintMoreOpen)); why.setAttribute("aria-controls", "eg-hint-more");
        out.push(el("div", { class: "gx-al gx-al--plain gx-al--card kk-eg-hint", role: "status", "data-hold-row": "egt:hint" },
          el("div", { class: "kk-eg-hint__t" }, el("b", { text: t("ep.hintT") }), el("span", { text: t("ep.hint") }), more),
          el("div", { class: "kk-eg-acts" }, stdBtn("main", accentOf("hint") ? "primary" : "tertiary", "hint"), U.btn(t("ep.hintKeep"), "tertiary", hintDismiss), why)));
      }
      // «+ Группа» — акцентная, только когда на экране нет другой акцентной кнопки (accentOf)
      const head = K.Tpl.head({ title: t("ep.title"), text: t("ep.text") });
      const add = U.btn(t("ep.addGroup"), accentOf("add") ? "primary" : "tertiary", () => addGroupDialog(), { md: true, icon: "plus" });
      add.dataset.egAcc = "add";
      head.append(el("div", { class: "gx-card__acts", "data-hold-row": "egt:add" }, add));
      out.push(head);
      renderAll();
      out.push(cardsBox);
      return out;
    });
    root.append(top);
    const n = K.navState();
    if (n.focus && /^e-[0-9a-f]{10}$/.test(n.focus) && n.open === "exit") setTimeout(() => openExit(n.focus), 0);
    if (n.focus && /^(main|g-[0-9a-f]{6})$/.test(n.focus)) K.loadAll(["policy"]).then(() => focusGroup(n.focus));
    // #exits?tab=groups&open=rule — форма первого условия первой группы с условиями (ссылка и проверка вёрстки)
    if (n.open === "rule") K.loadAll(["policy"]).then(() => {
      const p = pol(), g = p ? p.policy.groups.find((x) => x.rules.length) : null;
      if (!g) return;
      open[g.id] = true; RULE_OPEN[g.id] = g.rules[0].id; formOf(g, p.rev); renderAll();
    });
  }

  // карточка группы раскрыта, прокручена в вид и подсвечена (как ?focus= ядра: класс is-found на 2 с), фокус — на ней
  // форма, которую «Сохранить» из вопроса при уходе не сохранила: раскрыть и прокрутить к ней (деталь выхода открыта сама)
  function showFailed(what) {
    if (what === "exit") return;
    focusGroup(what);
  }
  function focusGroup(gid) {
    const g0 = groupOf(gid), ru = !!g0 && (g0.role || "abroad") === "ru";
    if (ru && !(ruBox && ruBox.isConnected)) { K.goNav("exits", { tab: "ru", focus: gid }); return; }
    const box = ru ? ruCardsBox : cardsBox;
    if (!box || !g0) return;
    open[gid] = true;
    if (ru) renderRu(); else renderAll();                // часть РФ — блок собран заново (политика уже прочитана)
    const c = box.querySelector('[data-find="exits.group.' + CSS.escape(gid) + '"]');
    if (!c) return;
    c.classList.add("is-found");
    c.setAttribute("tabindex", "-1");
    setTimeout(() => c.classList.remove("is-found"), 2000);
    try { c.scrollIntoView({ block: "start" }); c.focus({ preventScroll: true }); } catch (e) { /* старый браузер */ }
  }

  // ── деталь выхода (боковая панель; SE §7.3) ─────────────────────────────────────────────────────────────────────
  const RANGES = [["1d", "ep.r1d"], ["7d", "ep.r7d"], ["90d", "ep.r90d"]];
  const timeOf = (ts) => K.fmtTs(ts);
  const hhmm = (ts) => { try { return new Date(ts * 1000).toLocaleTimeString(K.lang === "en" ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; } };
  // Причина состояния (снимок health: dead, disabled, maintenance, manual_standby, quota, country, unstable, rule:<id> →
  // {code: rule, args: {rule}}) и причина перехода (health._why_word: manual, sub_confirm, rule:<id>, …) — один словарь:
  // условие называется своим пересказом из политики группы выхода; неизвестный код — без текста
  const WHY_ALIAS = { manual: "manual_standby", maintenance: "manual_maintenance" };
  function whyText(prefix, code, args, gid) {
    let c = String(code || ""), a = args || {};
    if (c.indexOf("rule:") === 0) { a = { rule: c.slice(5) }; c = "rule"; }
    c = WHY_ALIAS[c] || c;
    if (c === "rule") {
      const g = groupOf(gid), rl = g ? g.rules.find((x) => x.id === a.rule) : null;
      return t(prefix + "rule", { r: rl ? ruleText(rl) : String(a.rule || "") });
    }
    return has(prefix + c) ? t(prefix + c, a) : "";
  }
  function reasonText(r) {
    // строже всего — первая по порядку снимка: упал, выключен, обслуживание, квота, страна; затем остальные
    const order = ["dead", "disabled", "maintenance", "quota", "country"];
    const why = r.reasons.slice().sort((x, y) => (order.indexOf(x.code) + 1 || 99) - (order.indexOf(y.code) + 1 || 99))[0];
    if (!why) return "";
    if (why.code === "manual_standby" && r.manualWhy === "sub_confirm") return t("ep.why.sub_confirm");
    return whyText("ep.why.", why.code, why.args, r.group);
  }
  // метки карточки из полей (SE §7.9, правка 5): «отвечает снова · …», «нестабилен · до …», «в запасе — подтвердите»
  function marks(r) {
    const out = [];
    if (r.recovering) out.push(U.chip(t("ep.recovering", { t: r.offUntil ? hhmm(r.offUntil) : "—" }), "warning"));
    if (r.unstable) out.push(U.chip(t("ep.unstable", { t: r.unstableUntil ? hhmm(r.unstableUntil) : "—" }), "warning"));
    if (r.manualWhy === "sub_confirm") out.push(U.chip(t("ep.subConfirm"), "warning"));
    return out;
  }
  function quotaBlock(r) {
    const q = r.quota;
    if (!q) return null;
    const pct = Math.max(0, Math.min(100, q.pct || 0));
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    bar.setAttribute("viewBox", "0 0 100 6"); bar.setAttribute("preserveAspectRatio", "none"); bar.setAttribute("class", "kk-eg-qbar"); bar.setAttribute("aria-hidden", "true");
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect"); bg.setAttribute("class", "kk-eg-qbar__bg"); bg.setAttribute("width", "100"); bg.setAttribute("height", "6"); bg.setAttribute("rx", "3");
    const fg = document.createElementNS("http://www.w3.org/2000/svg", "rect"); fg.setAttribute("class", "kk-eg-qbar__fg" + (pct >= (q.warnPct || 80) ? " is-warn" : "")); fg.setAttribute("width", String(pct)); fg.setAttribute("height", "6"); fg.setAttribute("rx", "3");
    bar.append(bg, fg);
    const kids = [sub(t("ep.quota"), ""), el("div", { class: "kk-eg-kv" }, el("span", { text: t("ep.quotaUsed", { u: K.fmtNum1(q.usedGb), l: K.fmtInt(q.limitGb) }) }), el("span", { class: "gx-desc", text: q.reset ? t("ep.quotaReset", { d: q.reset }) : "" })), bar,
      el("div", { class: "gx-hint", text: tk("ep.quotaAction.", q.action) })];
    if (q.pausedUntil) kids.push(U.al("warning", t("ep.quotaPaused", { d: q.pausedUntil }), ""));
    kids.push(el("div", { class: "gx-hint", text: t("ep.quotaHonest") }));
    return el("div", { class: "kk-eg-blk" }, ...kids);
  }
  // Графики — по ширине места (подписи осей 11 px на любой ширине, T8m): ширина известна, только когда блок стоит в
  // листе, и меняется — лист открыт на компьютере и окно сузили до 390, поворот телефона, ответ истории пришёл раньше
  // вёрстки (ширина 0 → график по 600, подписи 8,8 px на 390). onWidth(fn): fn() при смене ширины места на 8 px и больше;
  // блок ушёл из страницы — наблюдение снято
  function onWidth(n, fn) {
    if (typeof ResizeObserver !== "function") return;
    let w = n.clientWidth || 0, seen = false;
    const ro = new ResizeObserver(() => {
      if (n.isConnected) seen = true;
      else if (seen) { ro.disconnect(); return; }
      const now = n.clientWidth || 0;
      if (now && Math.abs(now - w) >= 8) { w = now; fn(); }
    });
    ro.observe(n);
  }
  // shown() — один раз, когда графики впервые нарисованы (или пришла ошибка): окно, открытое на «Графиках», листается
  // к ним ещё раз — до этого под блоком мало места, и он не доходит до верха
  function chartsBlock(id, shown) {
    const st = { range: "1d", seq: 0, last: null };
    const box = el("div", { class: "kk-eg-charts" });
    const seg = el("div");
    const paint = (j) => {
      st.last = j;
      if (!j || !j.charts) { box.replaceChildren(el("div", { class: "kc__empty", text: t("ep.noCharts") })); return; }
      const cols = j.cols || [], pts = (j.points || []).map((p) => p.slice());     // перерисовка — с тех же точек
      const col = (name) => cols.indexOf(name) + 1;
      // трафик — rx + tx за шаг (байты, metrics.history): свой столбец в конце точки
      const rx = col("rx"), tx = col("tx");
      if (rx > 0 && tx > 0) pts.forEach((p) => { p.push(p[rx] === null && p[tx] === null ? null : (p[rx] || 0) + (p[tx] || 0)); });
      const trafficCol = rx > 0 && tx > 0 ? cols.length + 1 : 0;
      const fmtT = (ts) => (st.range === "1d" ? hhmm(ts) : K.fmtDay ? K.fmtDay(new Date(ts * 1000)) : timeOf(ts));
      const C = window.KaskadCharts;
      // сетка (Claude Design «Сетка на графиках r1»): поле 150, на телефоне 120 (не больше 2 делений); одна колонка чисел
      // на всю шторку — по самому длинному числу всех рядов; единица — в строке названия, объём — одной единицей на ось
      // и за шаг ряда; отметки: сутки — каждые 4 ч (телефон 6), 7 дней — полночь, 90 дней — 1-е число месяца (Claude
      // Design r1b №2: понедельников за 90 дней 13 — подписи прятались бы через одну)
      // ширина места — в полосу «Состояние»; ряды на сетке меряют поле сами (ResizeObserver), onWidth перерисует всё
      const width = box.clientWidth || 0;
      const sm = !!K.isPhone(), sep = K.lang === "en" ? "." : ",";
      const units = t("fmt.bytes").split(" "), step = Number(j.step) || 0;
      const per = step === 300 ? "grid.per5m" : step === 3600 ? "grid.perH" : step === 86400 ? "grid.perD" : "grid.perS";
      const stepT = step >= 3600 ? step / 3600 + " " + t("fmt.h") : Math.max(1, Math.round(step / 60)) + " " + t("fmt.m");
      // ряд: столбец истории, название, единица (null — объём за шаг ряда), подсказка, вид, правило оси
      const mk = (name, title, unit, fmt, kind, o) => {
        const c = name === "traffic" ? trafficCol : col(name);
        if (!(c > 0)) return null;
        const axis = C.axis(pts, c, Object.assign({ K: sm ? 2 : 3, sep }, o || {}));
        return { title, unit: unit === null ? t(per, { u: units[axis.k + 1] || "", d: stepT }) : unit, fmt, kind, c, axis };
      };
      const rows = [mk("rtt_med", t("ep.chRtt"), t("grid.ms"), (v) => t("ep.ms", { n: K.fmtInt(v) })), mk("loss", t("ep.chLoss"), "%", (v) => K.fmtInt(v) + "%"),
        mk("mbps", t("ep.chSpeed"), t("grid.mbps"), (v) => t("ep.mbps", { n: K.fmtInt(v) }), "dots"), mk("traffic", t("ep.chTraffic"), null, (v) => K.fmtBytes(v), null, { bytes: true }),
        mk("devices", t("ep.chDevices"), "", (v) => K.fmtInt(v), null, { min: 1 })].filter(Boolean);
      const yw = C.yw([].concat(...rows.map((r) => (C.scale(pts, r.c).n ? r.axis.ys : []))));
      box.style.setProperty("--grid-yw", yw + "px");                    // и отступ легенды полосы «Состояние»
      // 90 дней — вид отметок по длине данных, а не по вкладке: ось идёт от первой точки ряда, и у выхода моложе ~2 мес.
      // 1-х чисел на ней 0–1 (REV-33714 Б-1); ось берёт первый вид с ≥ 2 отметками: 1-е число → понедельник → полночь
      const time = { kind: st.range === "1d" ? "h" : st.range === "7d" ? "d" : ["m", "w", "d"], hours: sm ? 6 : 4, lang: K.lang, narrow: sm };
      const ts0 = pts.length ? pts[0][0] : 0, ts1 = pts.length ? pts[pts.length - 1][0] : 1;
      box.replaceChildren(...[C.band({ title: t("ep.chStates"), states: j.states || [], ts0, ts1, width, yw, label: (s) => tk("ep.st.", s) }),
        ...rows.map((r) => C.chart({ title: r.title, unit: r.unit, points: pts, col: r.c, fmt: r.fmt, fmtTs: fmtT, kind: r.kind, axis: r.axis, yw, sm, time, end: t("grid.now"), emptyText: t("ep.noData") }))]);
    };
    const mark = () => { const m = K.liveMark ? K.liveMark("metrics") : null; return m && Number.isFinite(Number(m.ts)) ? Number(m.ts) : null; };
    const draw = (quiet) => {
      seg.replaceChildren(U.seg(RANGES.map(([v, k]) => ({ label: t(k), on: st.range === v, pick: () => { st.range = v; draw(); } })), { aria: t("ep.range") }));
      if (!quiet) { box.replaceChildren(U.skel(2)); st.last = null; }
      const seq = ++st.seq;                                   // ответ прежнего диапазона не рисует поверх нового
      st.at = mark();
      const once = () => { const f = shown; shown = null; if (f) f(); };
      K.API.exits.history(id, st.range).then((j) => { if (seq === st.seq) { paint(j); once(); } },
        (e) => { if (seq === st.seq && !quiet) { box.replaceChildren(U.al("danger", (e && e.message) || t("kit.failed"), "")); once(); } });
    };
    onWidth(box, () => { if (st.last) paint(st.last); });
    // автообновление (3.3.7.10, владелец 24.10: «графики молчат»): сборщик записал минуту истории (live.json → metrics.ts)
    // — «Сутки» перечитываются тихо, без «скелета»; «7 дней» и «90 дней» копятся прореживанием раз в час — не чаще раза
    // в 10 мин. Вкладка скрыта — поток закрыт, событий нет; деталь закрыли — подписка снимается
    const off = K.liveSub ? K.liveSub(() => {
      if (!box.isConnected) { if (st.seen) off(); return; }
      st.seen = true;
      const m = mark();
      if (m === null || st.at === null || !st.last || document.hidden) { if (st.at === null) st.at = m; return; }
      if (m - st.at >= (st.range === "1d" ? 60 : 600)) draw(true);
    }) : null;
    draw();
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.charts"), ""), seg, box);
  }
  function transitionsBlock(id) {
    const list = el("ul", { class: "kk-eg-trans" });
    const more = el("div", { hidden: true });          // «Ещё переходы» — только когда есть следующая страница (UI-1 F14)
    const page = (before) => K.API.exits.transitions(id, { days: 90, limit: 20, before }).then((j) => {
      if (!j.db) { list.replaceChildren(el("li", { class: "kk-eg-empty", text: t("ep.noCharts") })); return; }
      if (!before && !j.items.length) list.replaceChildren(el("li", { class: "kk-eg-empty", text: t("ep.noTrans") }));
      j.items.forEach((x) => list.append(el("li", null, el("span", { class: "gx-mono kk-eg-trans__t", text: timeOf(x.ts) }),
        el("span", null, el("span", { class: "kk-eg-trans__s", text: tk("ep.st.", x.from) + " → " + tk("ep.st.", x.to) }), el("span", { class: "gx-desc", text: x.why ? whyText("ep.whyT.", x.why, x.args, rowOf(id) ? rowOf(id).group : null) : "" })))));
      more.replaceChildren(...(j.next ? [U.btn(t("ep.moreTrans"), "ghost", () => page(j.next))] : []));
      more.hidden = !j.next;
    }, (e) => list.replaceChildren(el("li", { class: "kk-eg-empty", text: (e && e.message) || t("kit.failed") })));
    page(null);
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.trans"), ""), list, more);
  }
  // ── квота и «Не проверять скорость» выхода — своя форма со своей полосой (SE §7.3; PUT /api/gw/exits/<id> с rev, ?dry=1) ──
  // base/draft: {quota: {period, limit_gb, reset_day, warn_pct, action} | null, speed_skip}; правки копятся до «Сохранить».
  // Форма — вне перерисовки детали по данным (отклик меняется каждые секунды — фокус в поле не теряется); свежие данные:
  // правок нет — берёт их, правки есть и объект менялся — список «Применить снова» (Kit.rebaseModel), как у групп.
  const XKEYS = ["quota.period", "quota.limit_gb", "quota.reset_day", "quota.warn_pct", "quota.action", "speed_skip"];
  const QUOTA_NEW = { period: "month", limit_gb: 100, reset_day: 1, warn_pct: 80, action: "push" };
  let exForm = null;                                     // {id, base, draft, rev, sb, list, listText, v}
  const quotaOf = (r) => (r && r.quota ? { period: r.quota.period, limit_gb: r.quota.limitGb, reset_day: r.quota.resetDay || 1, warn_pct: r.quota.warnPct || 80, action: r.quota.action } : null);
  const exModel = (r) => ({ quota: quotaOf(r), speed_skip: !!(r && r.speedSkip) });
  const exCount = (f) => (!same(!!f.base.quota, !!f.draft.quota) ? 1 : 0) + Kit.dirtyKeys(f.base, f.draft, XKEYS).length;
  const exDirty = () => !!exForm && (exCount(exForm) > 0 || exForm.list.length > 0);
  // причина неактивности кнопок «сразу» над выходом id: несохранённая квота или «не проверять скорость» этого выхода
  const exUnsaved = (id) => (exForm && exForm.id === id && exDirty() ? t("kit.unsavedFirst") : "");
  function exValid(f) {
    const q = f.draft.quota;
    if (!q) return true;
    const lim = q.limit_gb;
    return typeof lim === "number" && lim > 0 && Math.abs(Math.round(lim * 10) - lim * 10) < 1e-6 && Number.isInteger(q.reset_day) && q.reset_day >= 1 && q.reset_day <= 28 &&
      Number.isInteger(q.warn_pct) && q.warn_pct >= 50 && q.warn_pct <= 99;
  }
  // тело записи: только изменённое (квота — целиком: пять настроек, §7.9), rev — base_rev формы
  function exBody(f) {
    const b = {};
    if (!same(f.base.quota, f.draft.quota)) b.quota = f.draft.quota ? copy(f.draft.quota) : null;
    if (f.base.speed_skip !== f.draft.speed_skip) b.speed_skip = f.draft.speed_skip;
    b.rev = f.rev;
    return b;
  }
  // conflict — из onConflict: запись уже отвергнута (409), полоса ещё «сохраняет»; без него перекладка ждала следующей
  // перерисовки наперегонки с прогоном «пересчитал» (как defSync маршрута по умолчанию, мелочи после 3.3.7.12 п. 5)
  function exSync(id, conflict) {
    const r = rowOf(id), f = exForm;
    if (!f || f.id !== id || !r) return;
    const fresh = exModel(r), rev = readExRev();
    if (same(fresh, f.base) && f.rev === rev) return;
    if (!conflict && f.sb && f.sb.saving()) return;
    if (!exDirty()) { f.base = copy(fresh); f.draft = copy(fresh); f.rev = rev; f.v++; return; }
    const res = Kit.rebaseModel(f.base, f.draft, fresh, XKEYS.concat(["quota"]));
    f.base = copy(fresh); f.rev = rev; f.draft = res.draft; f.v++;
    if (res.mode === "list") { f.list = res.edits; f.listText = t("ep.exChanged", { name: r.name }); }
  }
  const readExRev = () => (K.R.exits && K.R.exits.data ? K.R.exits.data.rev : null);
  function settingsBlock(id) {
    const r0 = rowOf(id);
    exForm = { id, base: exModel(r0), draft: exModel(r0), rev: readExRev(), sb: null, list: [], listText: "", v: 0 };
    const f = exForm;
    let box = null;
    const renderX = () => { if (box) K.blkForce(box); };
    f.sb = Kit.saveBar({ count: () => exCount(f), valid: () => exValid(f),
      onDry: () => K.API.exits.set(id, exBody(f), true),
      impact: impact([]),
      onSave: () => K.API.exits.set(id, exBody(f)),
      onCancel: () => { f.draft = copy(f.base); f.list = []; f.v++; renderX(); refreshButtons(); },
      onSaved: (res, how) => { if (how === "ok") { f.base = copy(f.draft); f.list = []; K.toast(t("ep.xSaved"), "", "success"); } refreshButtons(); K.loadAll(["exits", "policy"]).then(() => { f.v++; renderX(); }); },
      onConflict: () => K.loadAll(["exits", "policy"]).then(() => { const r = rowOf(id); if (!r) return { mode: "gone" }; const before = f.list.length; exSync(id, true); renderX(); return { mode: f.list.length > before || f.list.length ? "list" : "silent", listText: f.listText }; }),
      list: () => f.list.map((ed) => ({ text: t("ep.editLine", { what: t(ed.key === "speed_skip" ? "ep.speedSkip" : "ep.qOn") }), apply: () => { f.draft = Kit.applyAgain(f.draft, ed); f.list = f.list.filter((x) => x !== ed); f.v++; renderX(); } })) });
    box = K.blk(() => { exSync(id); const r = rowOf(id); const g = r ? groupOf(r.group) : null;
      return { v: f.v, base: JSON.stringify(f.base), list: f.list.length, sp: !!(g && g.checks && g.checks.speed && g.checks.speed.enabled), gone: !r }; }, (v) => {
      if (v.gone) return null;
      const d = f.draft;
      const rebuild = () => { f.v++; f.sb.update(); refreshButtons(); renderX(); };
      const kids = [sub(t("ep.xTitle"), t("ep.xText"))];
      kids.push(swLive(t("ep.qOn"), t("ep.qOnD"), () => !!d.quota, (on) => { d.quota = on ? copy(f.base.quota || QUOTA_NEW) : null; rebuild(); }));
      if (d.quota) {
        const q = d.quota;
        const num = (key, min, max, label, step) => {
          const i = U.input({ value: q[key], aria: label, type: "number", num: true, im: step ? "decimal" : "numeric" });
          i.classList.add("kk-eg-num"); i.min = String(min); i.max = String(max);
          i.addEventListener("input", () => { const n = Number(i.value); q[key] = i.value === "" || !isFinite(n) ? null : n; i.setAttribute("aria-invalid", String(!exValid(f))); f.sb.update(); refreshButtons(); });
          return el("label", { class: "kk-eg-qf" }, el("span", { class: "kk-eg-l", text: label }), i);
        };
        const sel = (key, items, label) => el("label", { class: "kk-eg-qf" }, el("span", { class: "kk-eg-l", text: label }),
          U.select(items, q[key], (x) => { q[key] = x; if (key === "period") rebuild(); else { f.sb.update(); refreshButtons(); } }, { aria: label }));
        kids.push(el("div", { class: "kk-eg-qform" },
          sel("period", [{ v: "month", l: t("ep.qMonth") }, { v: "day", l: t("ep.qDay") }], t("ep.qPeriod")),
          num("limit_gb", 0.1, 100000, t("ep.qLimit"), true),
          q.period === "month" ? num("reset_day", 1, 28, t("ep.qReset")) : null,
          num("warn_pct", 50, 99, t("ep.qWarn")),
          sel("action", [{ v: "remove", l: t("ep.then.remove") }, { v: "standby", l: t("ep.then.standby") }, { v: "push", l: t("ep.then.push") }], t("ep.qAction"))));
        kids.push(el("div", { class: "gx-hint", text: t("ep.qErr") }), el("div", { class: "gx-hint", text: t("ep.quotaHonest") }));
      }
      // «Не проверять скорость этого выхода» — когда тест скорости включён у группы (§5.3)
      if (v.sp || f.base.speed_skip) kids.push(swLive(t("ep.speedSkip"), t("ep.speedSkipD"), () => d.speed_skip, (on) => { d.speed_skip = on; f.sb.update(); refreshButtons(); }));
      kids.push(f.sb.el);
      if (f.list.length) f.sb.setList(f.listText); else f.sb.refresh();
      return el("div", { class: "kk-eg-blk", "data-eg-xform": id }, ...kids);
    }, { tag: "div" });
    return box;
  }
  let sheet = null;
  // at — "charts": нажатие на линию выхода в списке — окно сразу на «Графиках», период «Сутки» (HANDOFF «Выходы (В1, В2)»)
  function openExit(id, at) {
    if (sheet) sheet.close();
    // несохранённые квота/«не проверять скорость» — вопрос при закрытии листа (SE §7, правка 7)
    const s = K.openSheet({ title: exitName(id), wide: true, guard: exDirty, save: () => (exForm && exForm.sb ? exForm.sb.save() : Promise.resolve(true)),
      onDiscard: () => { exForm = null; refreshButtons(); },
      onClose: () => { if (sheet === s) sheet = null; dropDetailActs(true); actionsLive.clear(); exForm = null; refreshButtons(); } });
    sheet = s;
    const body = K.blk(() => {
      const r = rowOf(id);
      return { r: r ? [r.name, r.pstate, r.enabled, r.group, r.tier, r.rttMs, r.lossPct, r.devices, r.country, r.quota, r.recovering, r.unstable, r.manualWhy, r.speed, r.reasons] : null, rev: readRev() };
    }, (v) => {
      const r = rowOf(id);
      if (!r) { s.setHead([el("div", { class: "kk-eg-dhead" }, el("h2", { class: "gx-h2", text: exitName(id) })), s.closeBtn()]); return [K.R.exits.loaded ? U.al("warning", t("kit.goneElse"), "") : U.skel(3)]; }
      // шапка — имя выхода с правкой на месте (RN §4.1: PUT /api/gw/exits/<id> {name}, без rev) и вид ключа
      const name = Kit.rename({ value: r.name, aria: t("ep.exitName"), empty: true, reset: { label: t("rn.resetExit") },
        save: (x, dup) => K.API.exits.set(id, dup ? { name: x, allow_duplicate: true } : { name: x }).then((res) => { K.loadAll(["exits", "people"]); return { label: (res && res.shown) || x, note: res && res.note }; }) });
      s.setHead([el("div", { class: "kk-eg-dhead" }, el("h2", { class: "gx-h2 kk-eg-name" }, name.el), el("span", { class: "gx-desc", text: kindText(r) })), s.closeBtn()]);
      return detail(r);
    }, { hold: true });
    // на «Графики» — сразу и ещё раз, когда они нарисованы, если человек сам ещё не листал окно
    const go = at === "charts" ? { on: true } : null;
    const toCharts = () => { if (go && go.on && charts.isConnected) charts.scrollIntoView({ block: "start" }); };
    const charts = chartsBlock(id, go ? toCharts : null);
    s.body.append(body, settingsBlock(id), charts, transitionsBlock(id));
    if (go) {
      ["wheel", "touchstart", "keydown"].forEach((ev) => s.body.addEventListener(ev, () => { go.on = false; }, { once: true, passive: true }));
      requestAnimationFrame(toCharts);
    }
  }
  // подтверждения детали: деталь пересобирается по данным (отклик — каждые секунды) — прежние уходят, а не копятся.
  // Раскрытое подтверждение держит свой ряд «eg-detail:<id>» (blkHold ядра): блок собирается заново, а на место нового
  // ряда встаёт прежний (holdRows) — его действия не уничтожаем, иначе ряд вернётся пустым и подтверждение пропадёт
  // (409 на «Всё равно» → reread → новая rev → пересборка, REV-s1-t8m-3 З-2). Их снимет следующая сборка, когда ряд
  // отпустят или он ушёл со страницы, и закрытие листа (all)
  let detailActs = [];
  function dropDetailActs(all) {
    const held = (ca) => { if (all || !ca.el.isConnected) return false; const row = ca.el.closest('[data-hold-row^="eg-detail:"]'); return !!row && !!row.querySelector(".kk-ca.is-confirm"); };
    const keep = detailActs.filter(held);
    detailActs.forEach((ca) => { if (keep.indexOf(ca) < 0) { actionsLive.delete(ca); ca.destroy(); } });
    detailActs = keep;
  }
  const trackD = (ca) => { detailActs.push(ca); return track(ca); };
  // «Перенести в группу…» детали — кнопка «сразу»: неактивна при правках группы выхода и его квоты (refreshButtons)
  function moveBtn(id, dis) {
    const b = U.btn(t("ep.moveTo"), "tertiary", () => { if (!dis()) moveDialog(id); }, { icon: "split" });
    b.dataset.egMove = id;
    const w = dis(); b.disabled = !!w; b.title = w;
    return b;
  }
  function detail(r) {
    dropDetailActs();
    const id = r.id, g = r.group;
    const why = reasonText(r);
    const gi = groupOf(g), ti = gi ? gi.tiers.findIndex((x) => x.id === r.tier) : -1;
    const kv = (k, v) => el("div", { class: "kk-eg-kv" }, el("span", { class: "gx-desc", text: k }), el("span", { text: v }));
    // внутренний выход (T15 Q6, кадры 3.1, 3.3): роутер (K.domMark), шаг вместо уровня и что сейчас с российскими сайтами
    const isRu = r.role === "ru" || !!(gi && gi.role === "ru");
    const kids = [el("div", { class: "kk-eg-dstate" }, stateChip(r), isRu ? K.domMark(12, "gx-chip gx-chip--tag") : null, ...marks(r), why ? el("span", { class: "gx-desc", text: why }) : null),
      kv(t("ep.group"), g ? groupLabel(g) + (ti >= 0 ? " · " + (isRu ? t("ep.ru.stepN", { n: ti + 1 }) : tierLabel(gi.tiers[ti], ti)) : "") : "—"),
      kv(t("ep.devices"), r.devices !== null ? K.fmtInt(r.devices) : "—"),
      isRu && gi ? kv(t("ep.ru.fNow"), ruNowText(gi, ruNow(gi))) : null,
      kv(t("ep.rtt"), r.rttMs !== null ? t("ep.ms", { n: K.fmtInt(r.rttMs) }) : "—"),
      kv(t("ep.loss"), r.lossPct !== null ? K.fmtInt(r.lossPct) + "%" : "—"),
      kv(t("ep.country"), r.country ? r.country.code : "—")];
    const rev = readRev;
    const dis = () => unsavedWhy(g) || exUnsaved(id);
    const done = (okKey) => (res, how) => { reread(); if (how === "ok" && okKey) K.toast(t(okKey, { name: r.name }), "", "success"); if (how === "gone") K.toast(t("kit.goneElse"), "", "info"); };
    if (r.country && r.country.was) {
      const acc = trackD(Kit.confirmAction({ label: t("ep.countryAccept"), anywayLabel: t("ep.countryAcceptLow"), dis, readRev: rev, reread, dry: () => K.API.exits.country(id, { accept: true }, true),
        run: (x) => K.API.exits.country(id, { accept: true, rev: x }), impact: impact([]), onDone: done("ep.countryDone") }));
      // российский: без условия «Страна не та» выход из-за страны не снимается — сказать и предложить условие
      const keep = isRu && gi && !(gi.rules || []).some((x) => x.when && (x.when.conds || []).some((c) => c.metric === "country"));
      kids.push(el("div", { class: "gx-al gx-al--warning kk-eg-country", role: "status" }, el("span", { text: t("ep.countryChanged", { was: r.country.was, now: r.country.code }) + (keep ? " " + t("ep.ru.countryKeep") : "") }), acc.el,
        keep ? U.btn(t("ep.ru.addCountryCond"), "tertiary", () => toGroups(gi.id)) : null));
    }
    if (r.speed) kids.push(kv(t("ep.speedLast"), t("ep.speedVal", { d: K.fmtInt(r.speed.mbps), u: r.speed.up !== null ? K.fmtInt(r.speed.up) : "—", at: timeOf(r.speed.ts) })));
    const q = quotaBlock(r);
    if (q) kids.push(q);
    // кнопки над выходом: сразу, своим запросом, подтверждение на месте со своим ?dry=1 (SE §7.3)
    // «Вернуть»: в запасе, ждёт проверки ключа подписки или квота исчерпана (проверки остановлены до сброса, §5.6)
    const standby = r.pstate === "standby" || r.manualWhy || r.manual === "maintenance" || (r.quota && r.quota.pausedUntil) || r.reasons.some((x) => x.code === "quota");
    const acts = [
      U.btn(t("ep.speedNow"), "tertiary", () => K.API.exits.speedtest(id).then(() => K.toast(t("ep.speedStarted"), "", "info"), (e) => K.toast(t("kit.failed"), (e && e.message) || "", "danger")), { icon: "chart-column" }),
      trackD(Kit.confirmAction({ label: t(standby ? "ep.restore" : "ep.standby"), anywayLabel: t(standby ? "ep.restoreLow" : "ep.standbyLow"), dis, readRev: rev, reread,
        question: t(standby ? "ep.restoreQ" : "ep.standbyQ", { name: r.name }),
        dry: () => K.API.exits.manual(id, { state: standby ? "restore" : "standby" }, true), run: (x) => K.API.exits.manual(id, { state: standby ? "restore" : "standby", rev: x }),
        impact: impact([]), onDone: done(standby ? "ep.restored" : "ep.standbyDone") })).el,
      trackD(Kit.confirmAction({ label: t("ep.maint"), anywayLabel: t("ep.maintLow"), dis, readRev: rev, reread, question: t("ep.maintQ", { name: r.name }),
        dry: () => K.API.exits.manual(id, { state: "maintenance" }, true), run: (x) => K.API.exits.manual(id, { state: "maintenance", rev: x }), impact: impact([]), onDone: done("ep.maintDone") })).el,
      moveBtn(id, dis),
      trackD(Kit.confirmAction({ label: t(r.enabled ? "ep.disable" : "ep.enable"), anywayLabel: t(r.enabled ? "ep.disableLow" : "ep.enableLow"), dis, readRev: rev, reread,
        question: t(r.enabled ? "ep.disableQ" : "ep.enableQ", { name: r.name }),
        dry: () => K.API.exits.enabled(id, { on: !r.enabled }, true), run: (x) => K.API.exits.enabled(id, { on: !r.enabled, rev: x }), impact: impact([]), onDone: done(r.enabled ? "ep.disabled" : "ep.enabled") })).el,
      trackD(Kit.confirmAction({ label: t("ep.delKey"), anywayLabel: t("ep.delKeyLow"), kind: "dsoft", del: true, dis, readRev: rev, reread, question: t("ep.delKeyQ", { name: r.name }),
        dry: () => K.API.exits.del(id, null, true), run: (x) => K.API.exits.del(id, x), impact: impact([]),
        onDone: (res, how) => { reread(); if (how === "ok" || how === "gone") { if (sheet) sheet.close(); K.toast(how === "ok" ? t("ep.keyDeleted", { name: r.name }) : t("kit.gone"), how === "ok" && res && typeof res.text === "string" ? res.text : "", how === "ok" ? "success" : "info"); } } })).el,
    ];
    kids.push(el("div", { class: "kk-eg-dacts", "data-hold-row": "eg-detail:" + id }, ...acts));
    return kids;
  }

  // ── часть «Выходы › Российские группы» (T15 Q6, SPEC-groups §8.1; РФ-группы Ш1 29.10) ─────────────────────────────
  // Группа — та же карточка, что в «Группах» (groupCard: владелец 28.10 «как у выходов» — уровни, способ выбора, условия,
  // «Поведение и проверки», «Сохранить»), свёрнута строкой: «РФ», имя, «1 уровень · 2 ключа · 2 устройства · выбрали: …»,
  // «сейчас: …»; в теле, после уровней, — её факты (кто выбрал и «Список», проверки и их трафик, DNS через «Основную»,
  // страна и «Добавить условие»). Над карточками — «+ Группа · + Ключ» и схема; под ними — российские ключи вне групп.
  // Шаг группы — из live.groups сборщика (SSE), без него — по состоянию её ключей. Групп нет — «Российских групп пока
  // нет» (узел после обновления: российские сайты у всех идут напрямую, как раньше).
  const ruGroups = () => { const p = pol(); return p ? p.policy.groups.filter((g) => g.role === "ru") : []; };
  const usable = (r) => !!r && r.enabled !== false && (r.pstate === "ok" || r.pstate === "degraded");
  const exitsOfGroup = (g) => [].concat(...g.tiers.map((tr) => tr.exits));
  // шаг группы: {exit — через какой ключ идут, tier/ti/n — уровень, где годных ключей два и больше (устройства разложены
  // между ними способом выбора: REV-plan З-5 — не «через первый»), last — "direct" | "block" (последний шаг), since,
  // wait — сервер не достаёт до российских адресов}. live.groups — решение сборщика; без него — первый уровень с годным
  function ruNow(g) {
    const lg = K.liveMark("groups"), lv = lg && lg[g.id] && typeof lg[g.id] === "object" ? lg[g.id] : null;
    const pl = K.liveMark("policy"), wait = !!(pl && pl.node_ru_offline);
    const ids = exitsOfGroup(g);
    if (!ids.length) return { exit: null, last: g.fail === "block" ? "block" : "direct", since: null, empty: true, wait };
    const good = (tr) => tr.exits.filter((id) => usable(rowOf(id)));
    // сборщик назвал рабочий уровень — он (годные ключи уровня, иначе первый: выбирает способ уровня)
    let tr0 = lv && lv.active_tier ? g.tiers.find((tr) => tr.id === lv.active_tier && tr.exits.length) || null : null;
    if (!tr0 && !(lv && lv.last_step)) tr0 = g.tiers.find((tr) => good(tr).length) || null;
    if (tr0) {
      // кому достаются новые устройства — по рядам сборщика (PLAN-order Ш5: тот же расчёт, что выбор): очередь — первый
      // ряд, доли — все ряды; без рядов — рабочие ключи уровня. Один — его имя; несколько — «уровень · N ключей»
      const rk = lv && lv.ranks ? lv.ranks[tr0.id] : null;
      const rr = rk && Array.isArray(rk.rows) && rk.rows.length ? (rk.queue ? [rk.rows[0]] : rk.rows) : null;
      const ok = rr ? [].concat(...rr.map((row) => row.map((x) => x.id))).filter((id) => tr0.exits.indexOf(id) >= 0) : good(tr0);
      const n = ok.length || tr0.exits.length;
      if (n >= 2) return { exit: null, tier: tr0, ti: g.tiers.indexOf(tr0), n, last: null, since: null, wait };
      return { exit: ok[0] || tr0.exits[0], last: null, since: null, wait };
    }
    let since = lv && lv.since ? lv.since : 0;
    if (!since) ids.forEach((id) => { const r = rowOf(id); if (r && typeof r.since === "number" && r.since > since) since = r.since; });
    return { exit: null, last: lv && lv.last_step ? lv.last_step : (g.fail === "block" ? "block" : "direct"), since: since || null, wait };
  }
  // метка «сейчас: …» шапки карточки: через выход — зелёная; последний шаг — жёлтая (зарубежные сайты работают, поэтому
  // не красная «нет выхода», D-026); сервер не достаёт до российских адресов — жёлтая «ждём связи»
  function ruNowText(g, now) {
    if (now.wait) return t("ep.ru.nowWait");
    if (now.exit) return t("ep.ru.nowVia", { x: exitName(now.exit) });
    if (now.tier) return t("ep.ru.nowTier", { n: tierLabel(now.tier, now.ti), k: K.tn("ep.ru.keysN", now.n) });
    if (now.empty) return t("ep.ru.nowDirect");
    const at = now.since ? K.fmtTs(now.since) : "";
    return t(now.last === "block" ? (at ? "ep.ru.nowBlockSince" : "ep.ru.nowBlock") : (at ? "ep.ru.nowDirectSince" : "ep.ru.nowDirectDown"), { t: at });
  }
  const ruNowChip = (g, now) => U.chip(ruNowText(g, now), (now.exit || now.tier) && !now.wait ? "success" : now.empty && !now.wait ? "default" : "warning");
  // кто выбрал группу (GET /api/rules: своё ru_via у пользователя и у устройства; «inherit» — не выбор)
  function ruWho(gid) {
    const rd = K.R.rules && K.R.rules.loaded && !K.R.rules.missing && K.R.rules.data ? K.R.rules.data : null;
    if (!rd || !Array.isArray(rd.users)) return { u: 0, d: 0, known: false };
    return { u: rd.users.filter((x) => x && x.ru_via === gid).length, d: (Array.isArray(rd.devices) ? rd.devices : []).filter((x) => x && x.ru_via === gid).length, known: true };
  }
  const ruWhoText = (w) => (!w.known ? "—" : !w.u && !w.d ? t("ep.ru.whoNone") : [w.u ? K.tn("ep.ru.usersN", w.u) : "", w.d ? K.tn("ep.ru.devicesN", w.d) : ""].filter(Boolean).join(", "));
  // к карточке группы: российская — раскрыть в «Российских группах» (решение 10), зарубежная — в «Группах»
  const toGroups = (gid) => { const g = groupOf(gid); if (g && (g.role || "abroad") === "ru") focusGroup(gid); else K.goNav("exits", { tab: "groups", focus: gid }); };
  const ruPick = (gid) => (K.ruPick ? K.ruPick(gid) : K.goNav("rules", { tab: "profile" }));
  const addKey = (gid) => (K.addKey ? K.addKey({ group: gid }) : null);
  // «≈ до 80 МБ в сутки на проверки» и по выходам: «Роутер» (AWG) до 10 МБ (forecast политики и probe_mb_day выходов)
  function ruChecks(g) {
    const p = pol(), fc = p && p.forecast && p.forecast.probe_mb_day ? p.forecast.probe_mb_day[g.id] : null;
    const fr = (g.checks && g.checks.freq) || {};
    const per = exitsOfGroup(g).map((id) => { const r = rowOf(id); return r && r.probeMbDay !== null && r.probeMbDay !== undefined ? t("ep.ru.probeExit", { x: r.name, k: r.kind === "awg" ? "AWG" : kindText(r), n: K.fmtNum1(r.probeMbDay) }) : ""; }).filter(Boolean);
    const how = fr.mode === "lazy" ? t("ep.ru.lazy") : tk("ep.freq.", fr.mode || "normal");
    return { v: fc !== null && fc !== undefined ? t("ep.ru.probeDay", { n: K.fmtInt(fc) }) : t("ep.ru.probeUnknown"), s: [how].concat(per.length ? [per.join(", ")] : []).join(" "),
      word: fr.mode === "lazy" ? t("ep.freq.lazy") : tk("ep.freq.", fr.mode || "normal"), per: per.join(", ") };
  }
  function ruCountry(g) {
    const geo = (g.checks && g.checks.geo) || {};
    if (geo.enabled === false) return { v: t("ep.ru.countryOff"), s: "" };
    const every = geo.every_h === 24 || !geo.every_h ? t("ep.ru.daily") : t("ep.ru.everyH", { n: K.fmtInt(geo.every_h) });
    return { v: t("ep.ru.countryExp", { c: geo.expect || "RU", every }), s: (g.rules || []).some((r) => r.when && (r.when.conds || []).some((c) => c.metric === "country")) ? "" : t("ep.ru.countryHint") };
  }
  // кто выбрал: имена пользователей (для подписи «Мама, Папа»), до трёх и «…»
  function ruWhoNames(gid) {
    const rd = K.R.rules && K.R.rules.loaded && !K.R.rules.missing && K.R.rules.data ? K.R.rules.data : null;
    const us = rd && Array.isArray(rd.users) ? rd.users.filter((x) => x && x.ru_via === gid && typeof x.name === "string").map((x) => x.name) : [];
    return us.length > 3 ? us.slice(0, 3).join(", ") + ", …" : us.join(", ");
  }
  // настройки группы (макет it1 Д3 Р1, kk-rukv) — в общей карточке после уровней: подпись · значение и пояснение ·
  // действие у правого края. «Если все выходы недоступны» — сама форма (failRow) выше, здесь не повторяется
  function ruFacts(gid) {
    const g = forms[gid].draft, w = ruWho(gid), ch = ruChecks(g), co = ruCountry(g), m = groupOf("main");
    const btn = (label, on) => U.btn(label, "ghost", on, { sm: true });
    const dns = m && m.fail === "block" ? t("ep.ru.dnsBlock", { g: groupLabel("main") }) : t("ep.ru.dnsProxy");
    const row = (k, v, s2, act) => el("div", { class: "kk-rukv__r" }, el("span", { class: "kk-rukv__k", text: k }),
      el("span", { class: "kk-rukv__v" }, el("b", { text: v }), s2 ? el("span", { text: s2 }) : null), el("span", { class: "kk-rukv__e" }, act || null));
    return el("div", { class: "kk-eg-blk" }, sub(t("ep.ru.factsT"), ""), el("div", { class: "kk-rukv", role: "group", "aria-label": t("ep.ru.factsL", { g: groupLabel(gid) }), "data-eg-rukv": gid },
      row(t("ep.ru.fWho"), ruWhoText(w), ruWhoNames(gid), btn(t("ep.ru.list"), () => ruPick(gid))),
      row(t("ep.ru.fChecks"), ch.word, [ch.v, ch.per].filter(Boolean).join(" · ")),          // как в макете: частота, под ней объём
      row(t("ep.ru.fDns"), t("ep.ru.dnsV", { g: groupLabel("main") }), dns),
      row(t("ep.ru.fCountry"), co.v, co.s, co.s ? btn(t("ep.ru.addCond"), () => addCountryRule(gid)) : null)));
  }
  // «Добавить условие» у строки «Страна»: условие «Страна не та» в черновик группы и его форма раскрыта (сохраняет
  // «Сохранить» группы, как любое условие); условий уже 20 или проверок ухода 5 — только раскрыть «Условия»
  function addCountryRule(gid) {
    const f = forms[gid];
    if (!f || f.gone) return;
    const g = f.draft;
    open[gid] = true;
    if (g.rules.length < 20 && whenConds(g) < 5) { const r = RULE_TPL.country(g); g.rules.push(r); RULE_OPEN[gid] = r.id; touchPreset(g); }
    edited(gid);
    const at = f.card && f.card.isConnected ? f.card.querySelector(".kk-eg-rulef") || f.card.querySelector(".kk-eg-rules") : null;
    if (at) try { at.scrollIntoView({ block: "center" }); } catch (e) { /* старый браузер */ }
  }
  // схема «Пользователь → Роутер или сервер в России → Российский сайт» и «Зачем» (№31а, макет it1 Д3 Р1 r2):
  // на всю ширину части, первой карточкой — сначала графика, потом цифры
  function ruScheme() {
    const node = (ic, b, s, mid) => el("li", { class: "kk-rus__n" + (mid ? " kk-rus__n--mid" : "") }, el("span", { class: "kk-rus__ic", "aria-hidden": "true" }, K.icon(ic, 20)), el("b", { text: b }), el("span", { text: s }));
    const arr = (s) => el("li", { class: "kk-rus__a", "aria-hidden": "true" }, el("span", { text: s }));
    return el("section", { class: "gx-card kk-rus", "aria-label": t("ep.ruScheme.l") },
      el("ol", { class: "kk-rus__flow" }, node("persons", t("ep.ruScheme.user"), t("ep.ruScheme.userS")), arr(t("ep.ruScheme.a1")),
        node("router", t("ep.ruScheme.router"), t("ep.ruScheme.routerS"), true), arr(t("ep.ruScheme.a2")), node("globe", t("ep.ruScheme.site"), t("ep.ruScheme.siteS"))),
      el("p", { class: "kk-rus__why" }, el("b", { text: t("ep.ruScheme.whyB") }), " " + t("ep.ruScheme.why")));
  }
  // состояние российского выхода словом и значком (kk-xst): годен · ухудшен · в запасе · не работает · проверяется
  const XST = { ok: ["ok", "check"], degraded: ["warn", "triangle-exclamation"], standby: ["off", "pause"], off: ["bad", "xmark"], unknown: ["none", "dot-o"], nodata: ["none", "dot-o"] };
  function ruState(r) {
    const k = r.enabled === false ? "off" : r.pstate || "unknown", x = XST[k] || XST.unknown;
    const word = r.enabled === false ? t("ep.ru.stOff") : tk("ep.st.", k);
    const why = r.enabled === false ? "" : reasonText(r);
    return el("span", { class: "kk-xst kk-xst--" + x[0], title: [word, why, r.since && k !== "ok" ? t("ep.ru.since", { t: K.fmtTs(r.since) }) : ""].filter(Boolean).join(" · ") },
      el("span", { class: "kk-xst__ic", "aria-hidden": "true" }, K.icon(x[1], 13)), el("span", { class: "kk-xst__w", text: word }));
  }
  function ruStepOf(r) {
    const g = groupOf(r.group), i = g ? g.tiers.findIndex((tr) => tr.id === r.tier) : -1;
    return g ? groupLabel(g.id) + (i >= 0 ? " · " + t("ep.ru.stepN", { n: i + 1 }) : "") : "—";
  }
  function ruExitsCard(list, phone) {
    if (!list.length) return null;
    if (phone) {
      return Kit.msec(t("ep.ru.exits"), [Kit.moreLine(t("ep.ru.exitsS"), { title: t("ep.ru.exits") }), Kit.mlist(list.map((r) => Kit.mrow({ lead: { icon: "router" }, title: r.name,
        sub: ruStepOf(r) + " · " + (r.enabled === false ? t("ep.ru.stOff") : tk("ep.st.", r.pstate || "unknown")), key: "exit:" + r.id, aria: r.name, onClick: () => openExit(r.id) })))]);
    }
    const head = el("div", { class: "kk-rux__h", "aria-hidden": "true" }, el("span", { text: t("ep.ru.cExit") }), el("span", { text: t("ep.ru.cStep") }), el("span", { text: t("ep.ru.cState") }),
      el("span", { class: "kk-rux__c4", text: t("ep.ru.cDev") }));   // 5-я колонка (меню строки) без подписи — пустой span дал бы лишний зазор
    const rowsN = list.map((r) => {
      const where = [kindText(r), r.unnamed ? "" : r.host].filter(Boolean).join(" · ");
      const open = el("button", { type: "button", class: "kk-rux__n", "aria-label": t("ep.ru.openExit", { x: r.name }) }, el("span", { class: "kk-rux__ic" }, K.icon("router", 15)),
        el("span", { class: "kk-rux__nb" }, el("span", { class: "kk-rux__nt", text: r.name, title: r.name }), el("span", { class: "kk-rux__ns", text: where, title: where })));
      open.addEventListener("click", () => openExit(r.id));
      const menu = U.menu([{ label: t("ep.ru.details"), icon: "sliders", on: () => openExit(r.id) }, { label: t("ep.moveTo"), icon: "split", on: () => moveDialog(r.id) },
        r.group ? { label: t("ep.removeFrom"), icon: "xmark", on: () => removeDialog(r.id, r.group) } : null],
        { label: t("ep.ru.moreOf", { x: r.name }), aria: t("ep.ru.moreOf", { x: r.name }), cls: "ga-btn--xs" });
      return el("div", { class: "kk-rux__r", "data-hold-row": "rux:" + r.id, "data-eid": r.id }, open, el("span", { class: "kk-rux__g", text: ruStepOf(r), title: ruStepOf(r) }), ruState(r),
        el("span", { class: "kk-rux__d kk-rux__c4", text: r.devices !== null && r.devices !== undefined ? K.fmtInt(r.devices) : "—" }), menu);
    });
    return el("section", { class: "gx-card kk-rux", "aria-label": t("ep.ru.exits") },
      el("div", { class: "kk-rux__top" }, el("div", { class: "kk-rux__tt" }, el("h2", { class: "gx-card__title", text: t("ep.ru.exits") }), el("span", { class: "kk-rux__s", text: t("ep.ru.exitsS") }))),
      head, ...rowsN);
  }
  function ruEmpty() {
    return el("section", { class: "gx-card kk-empty", "data-find": "exits.rugEmpty" },
      el("div", { class: "gx-card__ic gx-card__ic--muted" }, K.icon("layers", 18)),
      el("h2", { class: "kk-empty__t", text: t("ep.ru.emptyT") }),
      el("p", { class: "kk-empty__p", text: t("ep.ru.emptyText") }),
      el("div", { class: "kk-empty__b" }, U.btn(t("ep.ru.addRuKey"), "primary", () => addKey("+ru"), { md: true, icon: "plus" }),
        U.btn(t("ep.addGroup"), "tertiary", () => addGroupDialog("ru"), { md: true, icon: "plus" })));
  }
  let ruBox = null, ruCardsBox = null;
  function renderRu() { if (ruBox && ruBox.isConnected) K.blkForce(ruBox); }
  // карточки российских групп в своей коробке (как cardsBox «Групп»): renderAll после записи и перекладки форм
  // перерисовывает и их
  function renderRuCards() {
    const p = pol();
    if (!p || !ruCardsBox) return;
    ruCardsBox.replaceChildren(...cardsOf(p, true));
  }
  function buildRu(root, o) {
    const phone = !!(o && o.phone);
    ruCardsBox = el("div", { class: "kk-eg-cards" });
    const top = K.blk(() => {
      const gs = ruGroups(), lg = K.liveMark("groups"), pl = K.liveMark("policy");
      const ids = gs.map((g) => g.id);
      return { st: K.R.policy.loaded ? (K.R.policy.missing ? "missing" : "ready") : K.R.policy.error ? "error" : "loading",
        rev: pol() ? pol().rev : null, g: JSON.stringify(gs),
        ex: rows().filter((r) => r.role === "ru" || ids.indexOf(r.group) >= 0).map((r) => [r.id, r.name, r.pstate, r.group, r.tier, r.devices, r.enabled, r.since, r.probeMbDay, r.rttMs]),
        lv: lg ? ids.map((id) => lg[id] || null) : null, wait: !!(pl && pl.node_ru_offline), gone: Object.keys(forms).filter((k) => forms[k].gone && isRuForm(k)).join(","),
        who: ids.map((id) => { const w = ruWho(id); return [w.u, w.d, w.known, ruWhoNames(id)]; }), main: [(groupOf("main") || {}).fail, groupLabel("main")], fc: pol() ? JSON.stringify(pol().forecast || {}) : "" };
    }, (v) => {
      if (v.st === "loading") return K.Tpl ? K.Tpl.state("loading", { rows: 3, card: true }) : U.skel(3);
      if (v.st === "error") return K.Tpl ? K.Tpl.state("error", { text: K.R.policy.error ? K.R.policy.error.message : "", onRetry: () => K.loadAll(["policy"]) }) : U.skel(1);
      if (v.st === "missing") return K.Tpl ? K.Tpl.state("empty", { icon: "pool", title: t("ep.missingT"), text: t("ep.missing") }) : U.skel(1);
      const gs = ruGroups(), ids = gs.map((g) => g.id);
      const list = rows().filter((r) => r.role === "ru" || ids.indexOf(r.group) >= 0);
      // №31 (макет it1 Д3 Р1 r2): «+ Группа · + Ключ» строкой над схемой у правого края, схема и карточки групп — на всю
      // ширину части. Российские ключи вне групп (редкость: группа удалена по-старому) — отдельной карточкой ниже,
      // чтобы не пропали из вида
      const out = [];
      if (gs.length) {
        const addG = U.btn(t("ep.addGroup"), "tertiary", () => addGroupDialog("ru"), { md: true, icon: "plus" });
        const addK = U.btn(t("ep.ru.key"), "primary", () => addKey(gs[0].id), { md: true, icon: "plus" });
        out.push(el("div", { class: "kk-ru2__btns", "data-hold-row": "rut:top" }, addG, addK));
      }
      out.push(ruScheme());
      renderRuCards();
      if (!gs.length) { out.push(ruEmpty()); if (ruCardsBox.childNodes.length) out.push(ruCardsBox); return out; }
      const loose = list.filter((r) => ids.indexOf(r.group) < 0);
      out.push(el("div", { class: "kk-ru2" }, ruCardsBox, ruExitsCard(loose, phone)));
      return out;
    });
    ruBox = top;
    root.append(top);
    const nv = K.navState();
    if (nv.focus && /^g-[0-9a-f]{6}$/.test(nv.focus)) K.loadAll(["policy"]).then(() => { const g = groupOf(nv.focus); if (g && (g.role || "abroad") === "ru") focusGroup(nv.focus); });
    // «Кто использует» — из GET /api/rules: раздел «Выходы» его не читает сам
    if (!(K.R.rules && (K.R.rules.loaded || K.R.rules.run))) K.loadAll(["rules"]);
  }
  // для подменю «Выходов»: число российских групп и жёлтый тон, если хоть одна на последнем шаге (HANDOFF «Где»)
  function ruSummary() {
    const gs = ruGroups();
    return { n: gs.length, warn: gs.some((g) => { const nw = ruNow(g); return !nw.exit && !nw.tier && !nw.empty; }) };
  }

  K.part(ID, {
    build,
    buildRu,
    ruSummary,
    openExit,
    addGroup: addGroupDialog,
    // несохранённые правки вкладки: вопрос при уходе (меню, вкладки раздела, закрытие страницы)
    dirty: anyDirty,
    dirtyGroup: dirtyOf,
    // «Сохранить» из вопроса при уходе: формы вкладки по одной (каждая — своей полосой, со своей версией); после записи —
    // перечитать, чтобы следующая форма перекладывалась на свежую rev, а не получила 409. true — правок не осталось
    // Не удалось (ошибка, список «Применить снова», красное поле) — человек остаётся, а форма, которая не сохранилась,
    // раскрыта и показана: её полоса с причиной видна (свёрнутая карточка полосы не рисует; REV-s1-t8m З-2)
    save() {
      let failed = null;
      const fail = (what) => (ok) => { if (!ok && !failed) failed = what; return ok; };
      const jobs = Object.keys(forms).filter(dirtyOf).map((gid) => () => (forms[gid] && forms[gid].sb ? forms[gid].sb.save() : Promise.resolve(false))
        .then(fail(gid)).then((ok) => (ok ? K.loadAll(["policy", "exits"]).then(() => { syncForms(); return true; }) : false)));
      if (exDirty()) jobs.push(() => (exForm && exForm.sb ? exForm.sb.save() : Promise.resolve(false)).then(fail("exit")));
      return jobs.reduce((p, job) => p.then((ok) => (ok ? job() : false)), Promise.resolve(true)).then((ok) => {
        renderAll();
        if (failed) showFailed(failed);
        return ok && !anyDirty();
      });
    },
    // «Не сохранять»: правки форм вкладки пропадают
    discard() { Object.keys(forms).forEach((gid) => { delete forms[gid]; }); exForm = null; },
    leave() { cardsBox = null; ruBox = null; ruCardsBox = null; },
    // «Тест скорости — общее на сервер» для «Настройки › Проверка сервера» (круг 1 Т1): карточка, её правки и «Сохранить»
    speedCard: (o) => speedCard(o),
    speedDirty: () => speedDirty(),
    // не сохранилось (красное поле, список «Применить снова») — карточка раскрыта, её полоса с причиной видна
    speedSave: () => (speedDirty() && speedForm.sb ? speedForm.sb.save().then((ok) => {
      if (!ok && !speedForm.open) { speedForm.open = true; if (speedForm.draw) speedForm.draw(); }
      return ok;
    }) : Promise.resolve(true)),
    speedDiscard: () => { speedForm.base = null; speedForm.draft = null; speedForm.sb = null; speedForm.bad = {}; speedForm.list = []; },
  });
  // закрытие или обновление вкладки с правками форм — окно браузера; на «Российских» карточки в ruCardsBox, а cardsBox = null
  // (leave() при переходе «Группы» → «Российские»; REV-3312 З-1)
  function unloadGuard(ev) { if ((cardsBox || ruCardsBox) && anyDirty()) { ev.preventDefault(); ev.returnValue = ""; } }
  window.addEventListener("beforeunload", unloadGuard);
})();
