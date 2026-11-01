/* Kaskad 3 — компоненты панели (window.Kit), слой L1 каркаса k3/handoff/UX-FOUNDATION.md §3 (F0; компоненты этапа 1 — T8).

   Телефон (3.3.6, макеты Claude Design «Kaskad Mobile», классы — mobile.css): строки вместо карточек и деталь во весь
   экран. Простые сборщики узлов (данные — аргументами, действия — колбэками):
     Kit.mrow(o)  строка списка: lead (узел | {icon, tone} | {tag} | {glyph}), title, sub, end, btn (одна кнопка), onClick
     Kit.mlist(rows, o) · Kit.msec(title, body, o) · Kit.mkv(label, value, o) · Kit.mst(text, tone) — слово состояния
     Kit.mnav(o)  «‹ Назад · ⋯» детали · Kit.mhero(o) — имя, состояние, две главные кнопки
     Kit.mkpis(tiles) · Kit.evs(list) · Kit.moreLine(text, o) — одна строка и «Подробнее» (лист снизу: layers.openBottom)
     Kit.mstTone(tone) — тон метки U.chip → тон слова (чистая, тест в node).

   Только панель: мастер и /p/ этот файл не грузят (общие с ними компоненты — ES5 в give.js/ux.js, без Kit.init).
   При загрузке файл лишь объявляет функции и ни к чему не обращается. Помощники, слои и словарь ядра приходят
   один раз из app.js (boot) через
     Kit.init({el, t, tn, icon, isPhone, U, layers})
     layers = {openDialog, openSheet, openMenu, confirmDialog, typedConfirm, toast}
   Тексты — ключи «kit.*» (kit-i18n-ru.js / kit-i18n-en.js) в общем словаре ядра через переданные t/tn. Объект
   ядра (window.KaskadPanel) компоненты не зовут: данные — аргументами, действия — колбэками o.api.

   Новый компонент (F1, UX §3.3): чистая Kit.<имя>Model(data, o) (тест в node) + Kit.<имя>(o) ->
   {el, set(data), update(o2), destroy()}; внутри — need("<имя>"): вызов до Kit.init — ошибка в консоли. */
"use strict";

(() => {
  const HELPERS = ["el", "t", "tn", "icon", "isPhone", "U", "layers"];
  const LAYERS = ["openDialog", "openSheet", "openMenu", "confirmDialog", "typedConfirm", "toast"];
  let H = null;

  // Проверка состава init: чего не хватает (пустой список — всё на месте)
  function lacks(h, names, layers) {
    const out = [];
    names.forEach((k) => { if (!h || !h[k]) out.push(k); });
    if (h && h.layers) layers.forEach((k) => { if (typeof h.layers[k] !== "function") out.push("layers." + k); });
    return out;
  }
  // Помощники для компонента; до Kit.init — ошибка в консоли и исключение (тест F0)
  function need(what) {
    if (H) return H;
    const e = new Error("Kit." + what + ": Kit.init was not called");
    try { console.error(e.message); } catch (x) { /* консоли нет */ }
    throw e;
  }

  // ── Телефон (3.3.6, макеты «Kaskad Mobile» М1–М15; классы — mobile.css) ─────────────────────────────────
  // Состояние словом: тон метки (U.chip: success | danger | warning | off | checking | none | accent) → тон слова
  const MST = { success: "ok", ok: "ok", danger: "bad", bad: "bad", warning: "warn", warn: "warn", checking: "warn", off: "off", none: "none", accent: "ok" };
  const mstTone = (tone) => MST[tone] || "";
  // тон значка строки и героя: «акцент» — цвет акцента (не «работает», как у слова; REV-336-mobile З-2), прочие — как слово
  const icTone = (tone) => (tone === "accent" ? "accent" : mstTone(tone));
  // Kit.mst(text, tone) — «● в сети»: точка цвета состояния и слово, без подложки
  function mst(text, tone) {
    const h = need("mst");
    const k = mstTone(tone);
    return h.el("span", { class: "gx-mst" + (k ? " gx-mst--" + k : ""), title: text || null }, h.el("span", { text: text || "" }));
  }
  let mrowSeq = 0;                                       // id второй строки для aria-describedby
  // Kit.mrow(o) — строка списка: lead (узел | {icon, tone} | {tag} | {glyph}), title, mono, sub, subTone, end (узел справа:
  // слово состояния или значение), btn — одна кнопка в строке (тогда строка не нажимается сама), onClick — нажатие
  // строки (деталь или лист), chev — стрелка (по умолчанию — при onClick), cls, key (data-hold-row), find (data-find),
  // cur — выбрана (планшет), aria — подпись строки-кнопки, dis — недоступна
  function mrow(o = {}) {
    const h = need("mrow");
    const press = typeof o.onClick === "function" && !o.btn;
    const n = h.el(press ? "button" : "div", { type: press ? "button" : null, class: "gx-mrow" + (press ? "" : " gx-mrow--static") + (o.cls ? " " + o.cls : "") + (o.cur ? " is-cur" : ""),
      "data-hold-row": o.key || null, "data-find": o.find || null, "aria-current": o.cur ? "true" : null, disabled: press && o.dis ? true : null });
    const lead = o.lead;
    if (lead && lead.nodeType) n.append(lead);
    else if (lead && lead.tag) n.append(h.el("span", { class: "gx-mrow__tag", text: lead.tag }));
    else if (lead && lead.glyph) n.append(h.el("span", { class: "gx-mrow__gl" }, h.icon(lead.glyph, 18)));
    else if (lead && lead.icon) n.append(h.el("span", { class: "gx-mrow__ic" + (lead.tone ? " gx-mrow__ic--" + (icTone(lead.tone) || lead.tone) : "") }, h.icon(lead.icon, 18)));
    const b = h.el("span", { class: "gx-mrow__b" }, h.el("span", { class: "gx-mrow__t" + (o.mono ? " gx-mrow__t--mono" : ""), text: o.title || "", title: o.title || null }));
    if (o.sub || o.subNode) {
      const sk = mstTone(o.subTone);
      b.append(h.el("span", { class: "gx-mrow__s" + (sk === "bad" || sk === "warn" ? " gx-mrow__s--" + sk : ""), title: o.sub || null }, o.subNode || h.el("span", { text: o.sub })));
    }
    n.append(b);
    const end = h.el("span", { class: "gx-mrow__end" });
    if (o.end) end.append(typeof o.end === "string" ? h.el("span", { class: "gx-mrow__val", text: o.end, title: o.end }) : o.end);
    if (o.btn) end.append(o.btn);
    if (press && o.chev !== false) end.append(h.el("span", { class: "gx-mrow__chev" }, h.icon("chevron-right", 16)));
    if (end.childNodes.length) n.append(end);
    // подпись строки-кнопки для диктора: aria (имя) и то, что видно, — подробности и состояние («Мама, 2 устройства,
    // в сети»), иначе aria-label глушит состояние (REV-336-mobile З-8)
    // Живая вторая строка (subNode: счётчики обновляются на месте) в подпись не копируется — она замёрзла бы на
    // значении при сборке («—» вместо пакетов); её читают описанием (aria-describedby) — всегда текущей (REV-336-mobile-2 П-10)
    if (press && o.aria) {
      const seen = [o.title, o.subNode ? "" : o.sub, o.end ? end.firstChild && end.firstChild.textContent : ""]
        .map((x) => String(x || "").trim()).filter((x) => x && x !== o.aria);
      n.setAttribute("aria-label", [o.aria].concat(seen).join(", "));
      const s = o.subNode ? b.querySelector(".gx-mrow__s") : null;
      if (s) { s.id = "mrs" + (++mrowSeq); n.setAttribute("aria-describedby", s.id); }
    }
    if (press) n.addEventListener("click", (ev) => { ev.stopPropagation(); o.onClick(ev, n); });
    return n;
  }
  // Kit.mkv(label, value, o) — строка «подпись — значение» (значение моноширинным при o.mono)
  function mkv(label, value, o = {}) {
    const h = need("mkv");
    const v = value && value.nodeType ? value : h.el("span", { class: o.mono ? "gx-mrow__v" : "gx-mrow__val", text: value === undefined || value === null ? "" : String(value), title: value ? String(value) : null });
    return h.el("div", { class: "gx-mrow gx-mrow--static gx-mrow--kv" }, h.el("span", { class: "gx-mrow__b" }, h.el("span", { class: "gx-mrow__t", text: label, title: label })), h.el("span", { class: "gx-mrow__end" }, v));
  }
  // Kit.mlist(rows, o) — список строк одной карточкой; o: {tour, find, cls}
  function mlist(rows, o = {}) {
    const h = need("mlist");
    return h.el("div", { class: "gx-mlist" + (o.cls ? " " + o.cls : ""), "data-tour": o.tour || null, "data-find": o.find || null }, (rows || []).filter(Boolean));
  }
  // Kit.msec(title, body, o) — раздел страницы: подпись (и ссылка справа: o.link {label, on}) и содержимое
  function msec(title, body, o = {}) {
    const h = need("msec");
    const head = title || o.link ? h.el("div", { class: "gx-msec__h" }, title ? h.el("h2", { class: "gx-msec__t", text: title, title }) : h.el("span"),
      o.link ? h.U.btn(o.link.label, "link", o.link.on) : null) : null;
    return h.el("section", { class: "gx-msec" + (o.cls ? " " + o.cls : ""), "data-tour": o.tour || null, "aria-label": title || null }, head, [].concat(body).filter(Boolean));
  }
  // Kit.mnav(o) — строка детали: «‹ Назад» (o.back — подпись, o.onBack), o.title посередине (необязательно), «⋯» (o.more —
  // пункты U.menu, o.moreLabel — его подпись)
  function mnav(o = {}) {
    const h = need("mnav");
    const back = h.el("button", { type: "button", class: "gx-mnav__back", onclick: () => o.onBack && o.onBack() }, h.icon("chevron-left", 20), h.el("span", { text: o.back || h.t("kit.back") }));
    const more = o.more && o.more.filter(Boolean).length ? h.U.menu(o.more.filter(Boolean), { label: o.moreLabel || o.title || "" }) : null;
    return h.el("div", { class: "gx-mnav" }, back, o.title ? h.el("div", { class: "gx-mnav__t", text: o.title, title: o.title }) : h.el("span", { class: "gx-mnav__sp" }),
      h.el("div", { class: "gx-mnav__end" }, more));
  }
  // Kit.mhero(o) — шапка детали: lead (аватар или {icon, tone}), name, sub — узлы строки состояния, acts — до двух кнопок
  function mhero(o = {}) {
    const h = need("mhero");
    const lead = o.lead && o.lead.nodeType ? o.lead : (o.lead && o.lead.icon ? h.el("span", { class: "gx-mhero__ic" + (icTone(o.lead.tone) ? " gx-mhero__ic--" + icTone(o.lead.tone) : "") }, h.icon(o.lead.icon, 24)) : null);
    const acts = (o.acts || []).filter(Boolean);
    return h.el("div", { class: "gx-mhero" }, h.el("div", { class: "gx-mhero__top" }, lead,
      h.el("div", { class: "gx-mhero__tt" }, h.el("h2", { class: "gx-mhero__n", text: o.name || "" }), (o.sub || []).filter(Boolean).length ? h.el("div", { class: "gx-mhero__s" }, o.sub.filter(Boolean)) : null),
      o.end || null),
    acts.length ? h.el("div", { class: "gx-mhero__acts" }, acts) : null);
  }
  // Kit.mkpis(tiles) — плитки показателей 2 в ряд: [{k, v, u, s, sTone, mono, on, aria}]; нажатие — в раздел
  function mkpis(tiles) {
    const h = need("mkpis");
    return h.el("div", { class: "kk-mkpis" }, (tiles || []).filter(Boolean).map((x) => {
      const sk = mstTone(x.sTone);
      return h.el("button", { type: "button", class: "kk-mkpi", "aria-label": x.aria || null, onclick: () => x.on && x.on() },
        h.el("span", { class: "kk-mkpi__k", text: x.k }),
        h.el("span", { class: "kk-mkpi__v", title: x.v + (x.u ? " " + x.u : "") }, h.el("span", { text: x.v }), x.u ? h.el("span", { class: "kk-mkpi__u", text: x.u }) : null),
        x.s ? h.el("span", { class: "kk-mkpi__s" + (sk === "bad" || sk === "warn" || sk === "ok" ? " kk-mkpi__s--" + sk : "") + (x.mono ? " kk-mkpi__s--mono" : ""), text: x.s, title: x.s }) : null);
    }));
  }
  // Kit.evs(list) — события строками: [{t — время, tone, text}]
  function evs(list) {
    const h = need("evs");
    return h.el("div", { class: "gx-mlist" }, (list || []).map((e) => h.el("div", { class: "gx-ev" }, h.el("span", { class: "gx-ev__t", text: e.t || "" }),
      h.el("span", { class: "gx-ev__d" + (mstTone(e.tone) && mstTone(e.tone) !== "off" && mstTone(e.tone) !== "none" ? " gx-ev__d--" + mstTone(e.tone) : "") }), h.el("span", { class: "gx-ev__b", text: e.text || "" }))));
  }
  // Kit.moreLine(text, o) — длинное пояснение одной строкой и «Подробнее»: полный текст — в листе снизу (o.title — его
  // заголовок); без слоя листа — текст целиком
  function moreLine(text, o = {}) {
    const h = need("moreLine");
    const open = h.layers.openBottom;
    if (typeof open !== "function") return h.el("p", { class: "gx-hint", text });
    return h.el("div", { class: "gx-more-line" }, h.el("span", { text, title: text }),
      h.U.btn(h.t("kit.more"), "link", () => { open({ title: o.title || "", blocks: [h.el("p", { class: "gx-bsheet__txt", text })] }); }));
  }

  // ── T8 (этап 1): общие правила записей, SE §7 «Версия политики», «Ошибки»; §7.9 ─────────────────────────────────
  const DRY_MS = 20000;                                  // срок сухого прогона — свой таймер (проверка 19, С-2)

  // Вид ошибки записи или прогона: gone | changed | busy | stale | dup | timeout | net | fail
  function errKind(e) {
    if (!e) return "fail";
    const code = e.code, d = e.data || {};
    if (e.status === 404 && (code === "gone" || d.gone === true)) return "gone";
    if (e.status === 409 && (code === "policy_changed" || code === "object_changed")) return "changed";
    if (code === 6 || code === "busy") return "busy";
    if (e.status === 400 && (code === "rev_required" || code === "ver_required")) return "stale";
    if (e.status === 409 && d.need === "confirm_duplicate") return "dup";
    if (code === "timeout") return "timeout";
    if (code === "net") return "net";
    return "fail";
  }
  // Обещание со своим сроком: не ответило за ms — отказ {code: "dry_timeout"}
  function withTimeout(p, ms) {
    return new Promise((res, rej) => {
      const tm = setTimeout(() => rej({ code: "dry_timeout", status: 0 }), ms);
      Promise.resolve(p).then((v) => { clearTimeout(tm); res(v); }, (e) => { clearTimeout(tm); rej(e); });
    });
  }

  // «Что изменится» из ответа прогона (policy_ops.impact, SE §7.9): {moves: {total, by_group: {gid: {total, by_tier,
  // last_block, last_direct}}, block, direct}, warnings: [строка сервера | {code, text?}]}. Уход в блок и «напрямую» —
  // отдельными строками до действия («3 устройства останутся без интернета», «… пойдут с IP сервера напрямую»):
  // moves.block/direct — свой маршрут устройства, by_group[g].last_* — последний шаг группы. Предупреждение-строка —
  // текст сервера как есть; объект — o.warn(code), иначе его text, иначе общая фраза. o.group(gid) — имя группы;
  // o.extra — строки, известные заранее (их считает панель). Чистая — тест в node.
  function impactLines(res, o = {}) {
    const h = need("impactLines");
    const out = [];
    const add = (x) => { if (x && out.indexOf(x) < 0) out.push(x); };
    const num = (v) => Number(v) || 0;
    const mv = res && res.moves && typeof res.moves === "object" ? res.moves : null;
    const total = mv ? num(mv.total) : 0;
    if (mv) {
      const groups = mv.by_group && typeof mv.by_group === "object" ? mv.by_group : {};
      let block = num(mv.block), direct = num(mv.direct), moved = 0, grouped = 0;
      // «Переедут» — только те, кто переходит на другой выход: ушедшие в блок или «напрямую» сказаны своими строками
      // («переедут: 3» рядом с «3 устройства останутся без интернета» противоречило, стенд КТ2, сценарий 5)
      const by = Object.keys(groups).map((gid) => {
        const g = groups[gid] || {};
        block += num(g.last_block); direct += num(g.last_direct);
        const all = g.total !== undefined ? num(g.total) : Object.keys(g.by_tier || {}).reduce((s, k) => s + num(g.by_tier[k]), 0);
        grouped += all;
        const n = Math.max(0, all - num(g.last_block) - num(g.last_direct));
        moved += n;
        return n > 0 ? h.t("kit.byGroup", { g: o.group ? o.group(gid) : gid, n }) : "";
      }).filter(Boolean);
      moved += Math.max(0, total - grouped - num(mv.block) - num(mv.direct));   // свой маршрут без группы — на выход
      if (moved > 0) add(h.t("kit.moves", { n: moved }) + (by.length > 1 ? " (" + by.join("; ") + ")" : ""));
      else if (block + direct === 0) add(h.t("kit.noMoves"));
      if (block > 0) add(h.tn("kit.noNet", block));
      if (direct > 0) add(h.tn("kit.direct", direct));
    }
    ((res && res.warnings) || []).forEach((w) => {
      if (typeof w === "string") { add(w); return; }
      const code = w && w.code ? String(w.code) : "";
      add((o.warn && code && o.warn(code)) || (w && typeof w.text === "string" && w.text) || h.t("kit.warnOther"));
    });
    (o.extra || []).forEach(add);
    return out;
  }

  // ── ConfirmAction: кнопка над одним объектом — сразу, своим запросом, подтверждение на месте со своим ?dry=1 ─────
  // Фазы: idle → counting → ready | failed | busy | gone; run → running → done | changed (пересчёт) | busy | error.
  // Модель — чистая (тест в node): что показать при фазе.
  function confirmActionModel(s) {
    const h = need("confirmActionModel");
    const label = s.label || "";
    const v = { open: s.phase !== "idle" && s.phase !== "done", text: "", lines: [], tone: "", primary: null, cancel: true, busy: false };
    switch (s.phase) {
      // s.note — 409 на «Всё равно»: «Данные изменились — обновите и повторите», идёт новый прогон в том же подтверждении
      case "counting": v.text = h.t(s.note ? "kit.changedRefresh" : "kit.counting"); v.primary = { label, kind: "run", enabled: false }; v.busy = true; break;
      case "ready": v.text = s.note ? h.t("kit.changedRefresh") : s.recounted ? h.t("kit.recount") : ""; v.lines = s.lines || []; v.primary = { label, kind: "run", enabled: true }; break;
      // после 409 на «Всё равно» прогон снова не удался — сказать и про изменившиеся данные (ТЗ 09-ui-common, строка 24б)
      case "failed": v.text = s.note ? h.t("kit.changedRefresh") + ". " + h.t("kit.noCount") : h.t("kit.noCount"); v.tone = "warning"; v.primary = { label: h.t("kit.anyway", { action: s.anywayLabel || label }), kind: "anyway", enabled: true }; break;
      case "busy": v.text = h.t("kit.busy"); v.tone = "warning"; v.primary = { label: h.t("kit.retry"), kind: "retry", enabled: true }; break;
      case "running": v.text = s.lines && s.lines.length ? "" : h.t("kit.running"); v.lines = s.lines || []; v.primary = { label, kind: "run", enabled: false }; v.busy = true; break;
      case "changedAnyway": v.text = h.t("kit.changedRefresh"); v.tone = "warning"; v.primary = null; break;
      case "gone": v.text = s.del ? h.t("kit.gone") : h.t("kit.goneElse"); v.primary = null; break;
      case "stale": v.text = h.t("kit.stale"); v.tone = "danger"; v.primary = null; break;
      // отказ сервера (400/500): текст под рядом, действие снова активно (s.again — «run» или «anyway»); обрыв — «Повторить»
      case "error": v.text = s.error || h.t("kit.failed"); v.tone = "danger";
        v.primary = s.retry ? { label: h.t("kit.retry"), kind: "retry", enabled: true }
          : s.again ? { label: s.again === "anyway" ? h.t("kit.anyway", { action: s.anywayLabel || label }) : label, kind: s.again, enabled: true } : null;
        if (s.again) v.lines = s.lines || [];
        break;
      default: break;
    }
    return v;
  }
  // o: {label, kind ("tertiary"|"dsoft"…), icon, question, anywayLabel, del (удаление: «Уже удалено» — успех),
  //     dis: () => "" | причина неактивности (несохранённые правки), readRev: () => rev чтения, из которого построен объект,
  //     reread: () => Promise — перечитать данные (409 на «Всё равно»: потом новый прогон и readRev() заново),
  //     dry: () => Promise<{rev, moves, warnings}>, run: (rev) => Promise<res>, impact: (res) => [строки],
  //     onDone(res, how) — после записи (how: "ok" | "gone" | "timeout"), host — куда раскрыть подтверждение (по умолчанию —
  //     сам компонент)}. -> {el, open(), close(), refresh(), destroy()}
  function confirmAction(o) {
    const h = need("confirmAction");
    const st = { phase: "idle", lines: [], rev: null, anywayRev: null, recounted: false, error: "", retry: null, seq: 0, again: null, note: false };
    let life = null;                                       // жизнь открытого подтверждения (layers.confirmLife ядра)
    const btn = o.iconOnly ? h.U.btn("", o.kind || "tertiary", () => open(), { icon: o.icon, tip: o.label, aria: o.aria || o.label, cls: o.cls || null })
      : h.U.btn(o.label, o.kind || "tertiary", () => open(), { icon: o.icon || null, md: !!o.md });
    btn.classList.add("kk-ca__btn");
    const box = h.el("div", { class: "kk-ca", role: "group", "aria-label": o.question || o.label, hidden: true });
    // o.hostOf(btn) — куда раскрыть подтверждение (плитка: под рядом кнопок); иначе — рядом с кнопкой
    const root = h.el("div", { class: "kk-ca__wrap" }, btn);
    const place = () => { const host = o.hostOf ? o.hostOf(btn) : null; (host || root).append(box); };
    const reason = () => (o.dis ? o.dis() || "" : "");
    // кнопка действия в раскрытом подтверждении: причина неактивности (правки появились, пока оно открыто) держит и её
    let yes = null, yesOn = false;
    function refresh() {
      const why = reason();
      btn.disabled = !!why || st.phase !== "idle";
      btn.title = why || "";
      if (why) btn.setAttribute("aria-description", why); else btn.removeAttribute("aria-description");
      if (yes) { yes.disabled = !yesOn || !!why; yes.title = why || ""; }
    }
    function render() {
      const v = confirmActionModel(Object.assign({ label: o.label, anywayLabel: o.anywayLabel, del: !!o.del }, st));
      box.hidden = !v.open;
      box.classList.toggle("is-confirm", v.open);           // открытое подтверждение держит перерисовку блока (blkHold)
      yes = null;
      if (!v.open) { refresh(); box.replaceChildren(); return; }
      const kids = [];
      if (o.question) kids.push(h.el("div", { class: "kk-ca__q", text: o.question }));
      // o.warn — что случится, известное заранее (как у U.askDelete): видно сразу, ещё до ответа прогона
      if (o.warn) kids.push(h.el("div", { class: "kk-ca__t kk-ca__t--warning gx-confirm__warn", text: o.warn }));
      if (v.text) kids.push(h.el("div", { class: "kk-ca__t" + (v.tone ? " kk-ca__t--" + v.tone : ""), role: v.tone === "danger" ? "alert" : "status", text: v.text }));
      if (v.lines.length) kids.push(h.el("ul", { class: "kk-ca__lines" }, ...v.lines.map((x) => h.el("li", { text: x }))));
      const no = h.U.btn(v.primary ? h.t("c.cancel") : h.t("kit.close"), "tertiary", () => close());
      const acts = [no];
      if (v.primary) {
        yes = h.U.btn(v.primary.label, o.kind === "dsoft" && v.primary.kind !== "retry" ? "dsoft" : "primary", () => press(v.primary.kind));
        yesOn = !!v.primary.enabled;
        if (v.busy) yes.setAttribute("aria-busy", "true");
        acts.push(yes);
      }
      refresh();
      kids.push(h.el("div", { class: "kk-ca__acts" }, ...acts));
      const hadFocus = typeof document !== "undefined" && box.contains(document.activeElement);
      box.replaceChildren(...kids);
      // фокус — на «Отмена» (безопасный выбор), как у U.askDelete; перерисовка подтверждения его не теряет
      if (st.phase === "counting" || hadFocus) setTimeout(() => { if (box.contains(no)) no.focus(); }, 0);
    }
    function count(recounted) {
      const my = ++st.seq;
      st.phase = "counting"; st.recounted = false; st.again = null; render();
      withTimeout(o.dry(), DRY_MS).then((res) => {
        if (my !== st.seq) return;
        st.rev = res && res.rev !== undefined ? res.rev : null;
        st.lines = o.impact ? o.impact(res) : impactLines(res);
        st.phase = "ready"; st.recounted = !!recounted; render();
      }, (e) => {
        if (my !== st.seq) return;
        const k = errKind(e);
        if (k === "gone") { st.phase = "gone"; render(); if (o.onDone) o.onDone(null, "gone"); return; }
        if (k === "busy") { st.phase = "busy"; st.retry = () => count(recounted); render(); return; }
        if (k === "stale") { st.phase = "stale"; render(); return; }
        // прогон не удался или не ответил за 20 с: «Всё равно» — с rev чтения, запомненной сейчас (проверка 20)
        st.anywayRev = o.readRev ? o.readRev() : null;
        st.phase = "failed"; render();
      });
    }
    function exec(rev, anyway) {
      const my = ++st.seq;
      st.phase = "running"; st.again = null; st.note = false; render();
      Promise.resolve().then(() => o.run(rev)).then((res) => {
        if (my !== st.seq) return;
        if (life) { life(); life = null; }
        st.phase = "idle"; render();
        if (o.onDone) o.onDone(res, "ok");
      }, (e) => {
        if (my !== st.seq) return;
        const k = errKind(e);
        if (k === "gone") { st.phase = "gone"; render(); if (o.onDone) o.onDone(null, "gone"); return; }
        // 409 версии: у кнопки — «пересчитал»; у «Всё равно» — «обновите и повторите» и новый прогон в том же подтверждении
        // «Всё равно» шло с rev чтения — сначала перечитать (o.reread), иначе новый прогон и следующее «Всё равно» снова
        // со старой rev (REV-s1-t8m З-3)
        if (k === "changed") { if (anyway) { st.note = true; rereadThenCount(my); } else count(true); return; }
        if (k === "busy") { st.phase = "busy"; st.retry = () => exec(rev, anyway); render(); return; }
        if (k === "stale") { st.phase = "stale"; render(); return; }
        if (k === "timeout") { st.phase = "error"; st.error = h.t("err.timeoutChange"); st.retry = null; render(); if (o.onDone) o.onDone(null, "timeout"); return; }
        st.phase = "error"; st.error = (e && e.message) || h.t("kit.failed"); st.retry = k === "net" ? () => exec(rev, anyway) : null;
        st.again = k === "net" ? null : anyway ? "anyway" : "run"; render();
        if (o.onDone && k !== "net") o.onDone(null, "error");
      });
    }
    function rereadThenCount(my) {
      st.phase = "counting"; render();
      Promise.resolve().then(() => (o.reread ? o.reread() : null)).catch(() => null).then(() => { if (my === st.seq) count(false); });
    }
    function press(kind) {
      if (reason()) { refresh(); return; }
      if (kind === "run") exec(st.rev, false);
      else if (kind === "anyway") exec(st.anywayRev, true);
      else if (kind === "retry" && st.retry) st.retry();
    }
    function open() {
      if (reason() || st.phase !== "idle") return;
      place(); count(false);
      // одно за раз; щелчок или фокус мимо закрывают; 30 с без человека — закрывается само (фокус — на кнопку)
      if (h.layers.confirmLife) life = h.layers.confirmLife([box, btn], (away) => close(away));
    }
    function close(away) {
      if (life) { const f = life; life = null; f(); }
      st.seq++; st.phase = "idle"; st.note = false; st.again = null; render();
      if (away !== true && btn.isConnected) btn.focus();
    }
    box.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { ev.stopPropagation(); close(); } });
    refresh();
    return { el: root, open, close: () => close(), refresh, destroy() { if (life) { life(); life = null; } st.seq++; root.remove(); box.remove(); }, phase: () => st.phase };
  }

  // ── Перекладка формы (Kit.rebaseModel; SE §7 «Перекладка формы») ───────────────────────────────────────────────
  // base — объект формы в копии base_rev, draft — с правками, fresh — объект в свежих данных (null — удалён).
  // Правки — по ключам o.keys (пути через точку: "fail", "checks.freq", "tiers"); у упорядоченного списка («tiers»)
  // «Применить снова» накладывает порядок на свежий состав. -> {mode: silent | list | gone, draft, edits: [{key, mine,
  // theirs}], what: {added, removed}}. Чистая — тест в node.
  const getAt = (o, k) => k.split(".").reduce((x, p) => (x && typeof x === "object" ? x[p] : undefined), o);
  function setAt(o, k, v) {
    const ps = k.split(".");
    let x = o;
    ps.slice(0, -1).forEach((p) => { if (!x[p] || typeof x[p] !== "object") x[p] = {}; x = x[p]; });
    x[ps[ps.length - 1]] = v;
  }
  const same = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
  const copy = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
  function dirtyKeys(base, draft, keys) { return keys.filter((k) => !same(getAt(base, k), getAt(draft, k))); }
  // Состав упорядоченного списка уровней: id выходов по порядку
  const membersOf = (tiers) => [].concat(...(Array.isArray(tiers) ? tiers : []).map((t) => (t && t.exits) || []));
  // Порядок правки на свежем составе: выходы, которых нет в свежих, уходят; новые остаются на своих местах
  function reorder(mine, fresh) {
    const have = membersOf(fresh), placed = membersOf(mine).filter((id) => have.indexOf(id) >= 0);
    const out = copy(mine).map((t) => Object.assign(t, { exits: (t.exits || []).filter((id) => have.indexOf(id) >= 0) }));
    (fresh || []).forEach((ft, i) => (ft.exits || []).forEach((id) => {
      if (placed.indexOf(id) >= 0) return;
      const tgt = out.find((t) => t.id === ft.id) || out[Math.min(i, out.length - 1)];
      if (tgt) tgt.exits.push(id);
    }));
    return out;
  }
  function rebaseModel(base, draft, fresh, keys) {
    if (!fresh) return { mode: "gone", draft: null, edits: [], what: { added: [], removed: [] } };
    const mine = dirtyKeys(base, draft, keys);
    const was = membersOf(base && base.tiers), now = membersOf(fresh.tiers);
    const what = { added: now.filter((id) => was.indexOf(id) < 0), removed: was.filter((id) => now.indexOf(id) < 0) };
    if (keys.every((k) => same(getAt(base, k), getAt(fresh, k)))) {
      const nd = copy(fresh);
      mine.forEach((k) => setAt(nd, k, copy(getAt(draft, k))));
      return { mode: "silent", draft: nd, edits: [], what };
    }
    // объект изменился: свежие данные на экране, правки — списком; правка, которая уже такая же, снимается
    const edits = mine.filter((k) => !same(getAt(fresh, k), getAt(draft, k))).map((k) => ({ key: k, mine: copy(getAt(draft, k)), theirs: copy(getAt(fresh, k)) }));
    return { mode: "list", draft: copy(fresh), edits, what };
  }
  // «Применить снова»: правка поверх текущего черновика (порядок списка — на текущий состав)
  function applyAgain(draft, edit) {
    const nd = copy(draft);
    setAt(nd, edit.key, edit.key === "tiers" ? reorder(edit.mine, getAt(draft, "tiers")) : copy(edit.mine));
    return nd;
  }
  // <что> строки «<Объект> изменили, пока вы правили: <что>»: «новый выход «X»; выход «Z» убран» (проверка 19, п. 6)
  function changedWhat(what, name) {
    const h = need("changedWhat");
    // кавычки — из текста «новый выход «{x}»» своего языка: «X», «Y» / “X”, “Y”
    const tpl = String(h.t("kit.addedOne", { x: "\u0000" })), at = tpl.indexOf("\u0000");
    const open = at > 0 ? tpl.charAt(at - 1) : "", close = at >= 0 ? tpl.charAt(at + 1) : "";
    const q = (ids) => ids.map((id) => (name ? name(id) : id)).join(close + ", " + open);
    const out = [];
    if (what.added.length) out.push(h.t(what.added.length > 1 ? "kit.addedMany" : "kit.addedOne", { x: q(what.added) }));
    if (what.removed.length) out.push(h.t(what.removed.length > 1 ? "kit.removedMany" : "kit.removedOne", { x: q(what.removed) }));
    return out.join("; ");
  }

  // ── SaveBar: «Изменений: N · Что изменится · Отменить · Сохранить» у каждой формы (SE §7, UX §6) ─────────────────
  // Фазы: idle | counting | ready | failed | saving | busy | error | recount | list | gone. Модель — чистая.
  function saveBarModel(s) {
    const h = need("saveBarModel");
    const v = { show: s.count > 0 || s.phase === "list" || s.phase === "gone" || s.phase === "saving", count: h.t("kit.count", { n: s.count }), text: "", tone: "",
      lines: s.showImpact ? s.lines || [] : [], canDry: !!s.hasDry && s.count > 0, canCancel: s.phase !== "saving" && s.count > 0,
      canSave: s.count > 0 && s.valid !== false && ["counting", "saving", "recount", "gone"].indexOf(s.phase) < 0, saving: s.phase === "saving", retry: false };
    if (s.phase === "counting") v.text = h.t("kit.counting");
    else if (s.phase === "recount") { v.text = h.t("kit.recount"); v.canSave = false; }
    else if (s.phase === "failed") { v.text = h.t("kit.noCount"); v.tone = "warning"; }
    else if (s.phase === "busy") { v.text = h.t("kit.busy"); v.tone = "warning"; v.retry = true; }
    else if (s.phase === "error") { v.text = s.error || h.t("kit.failed"); v.tone = "danger"; v.retry = !!s.canRetry; }
    else if (s.phase === "list") { v.text = s.listText || ""; v.tone = "warning"; }
    else if (s.phase === "gone") { v.text = h.t("kit.goneElse"); v.tone = "danger"; v.canSave = false; v.canCancel = false; }
    else if (s.phase === "stale") { v.text = h.t("kit.stale"); v.tone = "danger"; v.canSave = false; }
    else if (s.phase === "rebased") { v.text = h.t("kit.rebased"); v.tone = "warning"; }
    if (s.phase === "ready" && s.recounted) v.text = h.t("kit.recount");
    if (s.valid === false && s.count > 0) { v.text = s.invalidText || h.t("kit.invalid"); v.tone = "danger"; }
    return v;
  }
  // o: {count: () => n, valid?: () => bool, onDry?: () => Promise<res>, impact?: (res) => строки, onSave: () =>
  //     Promise<res>, onCancel(), onSaved(res, how), onConflict(e) -> Promise<{mode, listText}> (перекладка),
  //     list?: () => [{text, apply()}] — «Применить снова»}. -> {el, update(), recount(), refresh(), setList(text), gone(),
  //     saving(), dirty(), save() -> Promise<bool> (вопрос при уходе), destroy()}
  function saveBar(o) {
    const h = need("saveBar");
    const st = { phase: "idle", lines: [], showImpact: false, seq: 0, error: "", canRetry: false, listText: "", recounted: false };
    const root = h.el("div", { class: "kk-sb", role: "region", "aria-label": h.t("kit.saveBar"), hidden: true });
    function render() {
      const v = saveBarModel(Object.assign({ count: o.count(), hasDry: !!o.onDry, valid: o.valid ? o.valid() : true }, st));
      root.hidden = !v.show;
      if (!v.show) { root.replaceChildren(); return; }
      const what = o.onDry ? h.U.btn(h.t("kit.what"), "ghost", () => { st.showImpact = !st.showImpact; if (st.showImpact && st.phase !== "ready") dry(false); else render(); }) : null;
      if (what) { what.disabled = !v.canDry; what.setAttribute("aria-expanded", String(st.showImpact)); }
      const cancel = h.U.btn(h.t("kit.cancel"), "tertiary", () => { st.seq++; st.phase = "idle"; st.showImpact = false; o.onCancel(); render(); });
      cancel.disabled = !v.canCancel;
      const save = h.U.btn(v.retry ? h.t("kit.retry") : h.t("kit.save"), "primary", () => doSave());
      save.disabled = !v.canSave;
      if (v.saving) save.setAttribute("aria-busy", "true");
      const kids = [h.el("div", { class: "kk-sb__row" }, h.el("span", { class: "kk-sb__n", text: v.count }), what, h.el("span", { class: "gx-spacer" }), cancel, save)];
      if (v.text) kids.push(h.el("div", { class: "kk-sb__t" + (v.tone ? " kk-sb__t--" + v.tone : ""), role: v.tone === "danger" ? "alert" : "status", text: v.text }));
      if (v.lines.length) kids.push(h.el("ul", { class: "kk-ca__lines" }, ...v.lines.map((x) => h.el("li", { text: x }))));
      if (st.phase === "list" && o.list) {
        kids.push(h.el("ul", { class: "kk-sb__list" }, ...o.list().map((it) => h.el("li", null, h.el("span", { text: it.text }), h.U.btn(h.t("kit.applyAgain"), "tertiary", () => { it.apply(); render(); })))));
      }
      root.replaceChildren(...kids);
    }
    function dry(recounted) {
      if (!o.onDry || !o.count()) {
        // формы без прогона: после перекладки (409, чужая правка других ключей) — «Сохранить» снова доступна
        if (["saving", "recount", "counting"].indexOf(st.phase) >= 0) { st.phase = recounted && o.count() ? "rebased" : "idle"; st.showImpact = false; }
        render(); return Promise.resolve();
      }
      const my = ++st.seq;
      st.phase = recounted ? "recount" : "counting"; render();
      return (st.dryP = withTimeout(o.onDry(), DRY_MS).then((res) => {
        if (my !== st.seq) return;
        st.lines = o.impact ? o.impact(res) : impactLines(res);
        st.phase = "ready"; st.recounted = !!recounted; render();
      }, (e) => {
        if (my !== st.seq) return;
        const k = errKind(e);
        if (k === "busy") { st.phase = "busy"; st.retryDry = true; } else if (k === "stale") st.phase = "stale"; else st.phase = "failed";
        render();
      }));
    }
    // -> Promise<bool>: true — записано (полоса пуста), false — правки на месте (ошибка, перекладка, список)
    function doSave() {
      if (st.phase === "busy" && st.retryDry) { st.retryDry = false; dry(false); return Promise.resolve(false); }
      const my = ++st.seq;
      st.phase = "saving"; render();
      return Promise.resolve().then(() => o.onSave()).then((res) => {
        if (my !== st.seq) return false;
        st.phase = "idle"; st.showImpact = false; st.lines = [];
        if (o.onSaved) o.onSaved(res, "ok");             // форма берёт сохранённое как основу — полоса прячется сразу
        render();
        return true;
      }, (e) => {
        if (my !== st.seq) return false;
        const k = errKind(e);
        if (k === "changed" && o.onConflict) {
          // перекладка: объект не менялся — «Данные изменились — пересчитал», «Сохранить» — после нового прогона
          return Promise.resolve(o.onConflict(e)).then((r) => {
            if (my !== st.seq) return false;
            if (r && r.mode === "gone") { st.phase = "gone"; render(); return false; }
            if (r && r.mode === "list") { st.phase = "list"; st.listText = r.listText || ""; render(); return false; }
            st.showImpact = true; dry(true);
            return false;
          });
        }
        if (k === "gone") { st.phase = "gone"; render(); if (o.onSaved) o.onSaved(null, "gone"); return false; }
        if (k === "busy") { st.phase = "busy"; st.retryDry = false; render(); return false; }
        if (k === "stale") { st.phase = "stale"; render(); return false; }
        st.phase = "error"; st.canRetry = k === "net"; st.error = (e && e.message) || h.t("kit.failed"); render();
        if (o.onSaved && (k === "timeout" || k === "net")) o.onSaved(null, k);
        return false;
      });
    }
    // «Сохранить» из вопроса при уходе (SE §7, D-077): то же, что кнопка, и только когда кнопка была бы активна; после
    // перекладки (сам ответ 409), при списке «Применить снова», ошибках полей и «занято» — false (человек остаётся и
    // видит полосу); уже переложенная форма («нажмите «Сохранить» ещё раз») сохраняется, как кнопкой.
    // Первый прогон ещё считает — дождаться его. -> Promise<bool>: true — правок больше нет
    function saveNow() {
      if (!o.count()) return Promise.resolve(st.phase !== "list" && st.phase !== "gone");
      if (["list", "gone", "stale", "saving", "recount", "busy"].indexOf(st.phase) >= 0 || (o.valid && !o.valid())) { render(); return Promise.resolve(false); }
      // «rebased» (перекладка формы без прогона, правки на свежих данных): кнопка «Сохранить» активна — сохраняем
      const go = () => (["ready", "failed", "idle", "error", "rebased"].indexOf(st.phase) >= 0 ? doSave() : false);
      const p = st.phase === "counting" && st.dryP ? st.dryP.then(go) : Promise.resolve(go());
      return p.catch(() => false);
    }
    render();
    return {
      el: root,
      update() { if (st.phase === "ready" || st.phase === "failed" || st.phase === "rebased") { st.phase = "idle"; st.lines = []; st.showImpact = false; } if (!o.count() && st.phase !== "list" && st.phase !== "gone") st.phase = "idle"; render(); },
      recount: () => dry(true),
      // перерисовать без сброса прогона (форма собрана заново по тем же правкам — данные обновились, правок не было)
      refresh: () => render(),
      setList(text) { st.phase = text ? "list" : "idle"; st.listText = text || ""; render(); },
      gone() { st.phase = "gone"; render(); },
      saving: () => st.phase === "saving",
      // несохранённое: правки или список «Применить снова» (вопрос при уходе, кнопки «сразу»)
      dirty: () => o.count() > 0 || st.phase === "list",
      save: saveNow,
      destroy() { st.seq++; root.remove(); },
    };
  }

  // ── InlineRename (Kit.rename): имя на месте — текст и карандаш; Enter — сохранить, Esc — назад; дубль — вопрос ─────
  // o: {value, display?, aria, max (40 — names.MAX), empty?: допускается пустое (имя по умолчанию),
  //     save(name, allowDup) -> Promise<{label?, note?}>, edit?: сразу правка, onCancel?, onSaved?(r),
  //     reset?: {label} — кнопка «Вернуть имя из ключа» (сохраняет ""), count?: false — без счётчика знаков}
  // T13: счётчик «n из 40», подсказка ответа (note) — всплывающим сообщением
  const NAME_MAX = 40;
  function rename(o) {
    const h = need("rename");
    const max = o.max || NAME_MAX;
    const st = { editing: !!o.edit, value: o.value || "", shown: o.display || o.value || "", busy: false, err: "", dup: null };
    const root = h.el("span", { class: "kk-rn" });
    function view() {
      if (!st.editing) {
        const pen = h.U.btn("", "ghost", () => { st.editing = true; st.err = ""; st.dup = null; view(); }, { icon: "pencil", tip: h.t("kit.rename"), aria: h.t("kit.renameName", { name: st.shown }) });
        root.replaceChildren(h.el("span", { class: "kk-rn__t", text: st.shown }), pen);
        return;
      }
      const inp = h.U.input({ value: st.value, max, aria: o.aria || h.t("kit.rename") });
      inp.classList.add("kk-rn__in");
      const cnt = o.count === false ? null : h.el("span", { class: "kk-rn__n gx-small", "aria-live": "polite" });
      const count = () => { if (cnt) cnt.textContent = h.t("kit.nameCount", { n: [...inp.value].length, max }); };
      inp.addEventListener("input", count);
      count();
      const go = (dup, reset) => {
        const v = reset ? "" : inp.value.trim();
        if (st.busy) return;                             // двойной Enter — один запрос
        if (!v && !o.empty && !reset) { st.err = h.t("kit.nameEmpty"); view(); return; }
        st.busy = true;
        Promise.resolve(o.save(v, !!dup)).then((r) => {
          st.busy = false; st.editing = false; st.value = v; st.shown = (r && r.label) || v || st.shown; st.dup = null; view();
          if (r && r.note) h.layers.toast(h.t("kit.renamed"), r.note);
          if (o.onSaved) o.onSaved(r || {});
        }, (e) => {
          st.busy = false; st.value = reset ? st.value : inp.value;
          if (errKind(e) === "dup") { st.dup = v; st.err = ""; } else { st.dup = null; st.err = (e && e.message) || h.t("kit.failed"); }
          view();
        });
      };
      const cancel = () => { st.editing = false; st.err = ""; st.dup = null; view(); if (o.onCancel) o.onCancel(); };
      inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); go(false); } else if (ev.key === "Escape") { ev.stopPropagation(); cancel(); } });
      const ok = h.U.btn(h.t("kit.renameSave"), "primary", () => go(false));
      const no = h.U.btn(h.t("c.cancel"), "tertiary", cancel);
      const kids = [inp, cnt, no, ok];
      if (o.reset && o.reset.label) kids.push(h.U.btn(o.reset.label, "ghost", () => go(false, true)));
      if (st.dup !== null) kids.push(h.el("span", { class: "kk-rn__dup", role: "alert" }, h.el("span", { text: h.t("kit.dupQ", { name: st.dup }) }), h.U.btn(h.t("kit.dupYes"), "tertiary", () => go(true))));
      if (st.err) kids.push(h.el("span", { class: "kk-rn__err", role: "alert", text: st.err }));
      root.replaceChildren(h.el("span", { class: "kk-rn__edit" }, ...kids.filter(Boolean)));
      setTimeout(() => { inp.focus(); inp.select(); }, 0);
    }
    view();
    return { el: root, set(v, shown) { if (st.editing) return; st.value = v || ""; st.shown = shown || v || ""; view(); }, editing: () => st.editing, destroy() { root.remove(); } };
  }
  // T13: то же имя в небольшом окне — для карточек и строк, которые перерисовываются живыми данными (правка не
  // теряется при перерисовке). o — как у rename, плюс title; закрывается после сохранения и по «Отмена»/Esc
  function renameDialog(o) {
    const h = need("renameDialog");
    let d = null;
    const r = rename(Object.assign({}, o, { edit: true, onCancel: () => d && d.close(), onSaved: (res) => { if (d) d.close(); if (o.onSaved) o.onSaved(res); } }));
    d = h.layers.openDialog({ stack: true, icon: "pencil", size: "sm", title: o.title || h.t("kit.rename"), blocks: [h.el("div", { class: "kk-rn-dlg" }, r.el)] });
    return d;
  }
  // T13 (RN §4.1): технический id мелко и во всплывающей подсказке — после переименования старый слаг в строке путает.
  // parts — строки подсказки («d-1a2b3c4d», «логин telefon-0001»)
  function idHint(parts) {
    const h = need("idHint");
    const text = (Array.isArray(parts) ? parts : [parts]).filter(Boolean).join(" · ");
    return h.el("span", { class: "kk-idh gx-mono", tabindex: "0", title: text, "aria-label": h.t("kit.idHint", { id: text }), text: "id" });
  }

  // ── Порядок (PLAN-order Ш4): перетаскивание вместе со стрелками — один ход для мыши, пальца и клавиатуры ──────────
  // Модель: {steps, sections: [{id, mode, items: [id…]}]} — секции (уровни) и их строки в том порядке, как их видит
  // человек. mode: "free" — ручной (место решает), "ranked" — автовыбор (ход внутри секции ставит ручной: в ответе
  // manual), "locked" — «по соединениям» (внутри секции порядок не действует: ход отказан, перенос в другую — можно).
  // steps — внутренняя группа «по ключу на шаг»: на границе шага ход меняет шаги местами (или ставит ключ своим шагом),
  // а не сливает ключи. Координаты хода — по модели ДО хода: {s, i} — «встать перед i-й строкой секции s» (i = длина —
  // в конец), {gap: k} — «своей секцией между k−1 и k» (только steps); секция целиком — из {s} в {gap: k}.
  // -> {ok, model, manual: [id секций, ставших ручными], dropped: [опустевшие шаги], created: [новые шаги], why}
  function orderMove(model, from, to, o = {}) {
    const no = (why) => ({ ok: false, why, model, manual: [], dropped: [], created: [] });
    const secs = (model && Array.isArray(model.sections)) ? model.sections : [];
    if (!from || !to || !secs[from.s]) return no("bad");
    if (to.out) return no("outside");                                       // вне списка уровней группы — «сюда нельзя»
    const m = { steps: !!model.steps, sections: secs.map((x) => Object.assign({}, x, { items: (x.items || []).slice() })) };
    const S = m.sections;
    const done = (extra) => Object.assign({ ok: true, why: "", model: m, manual: [], dropped: [], created: [] }, extra || {});
    const moveSec = (s, gap) => {
      if (typeof gap !== "number" || gap < 0 || gap > S.length) return no("bad");
      const k = gap > s ? gap - 1 : gap;
      if (k === s) return no("same");
      const [sec] = S.splice(s, 1);
      S.splice(k, 0, sec);
      return done();
    };
    if (from.i === undefined || from.i === null) return to.gap === undefined ? no("bad") : moveSec(from.s, to.gap);
    const src = S[from.s], id = src.items[from.i];
    if (id === undefined) return no("bad");
    if (to.gap !== undefined) {
      if (!m.steps) return no("bad");
      if (src.items.length === 1) return moveSec(from.s, to.gap);           // один ключ — шаг целиком
      if (typeof to.gap !== "number" || to.gap < 0 || to.gap > S.length) return no("bad");
      if (S.length >= (o.max || Infinity)) return no("full");
      const nid = o.newId ? o.newId(m) : null;
      if (!nid) return no("bad");
      src.items.splice(from.i, 1);
      S.splice(to.gap, 0, { id: nid, mode: "ranked", items: [id] });
      return done({ created: [nid] });
    }
    const dst = S[to.s];
    if (!dst || typeof to.i !== "number" || to.i < 0) return no("bad");
    if (to.s === from.s) {
      if (src.mode === "locked") return no("locked");
      const k = Math.min(to.i > from.i ? to.i - 1 : to.i, src.items.length - 1);
      if (k === from.i) return no("same");
      src.items.splice(from.i, 1);
      src.items.splice(k, 0, id);
      if (src.mode !== "ranked") return done();
      src.mode = "free";
      return done({ manual: [src.id] });
    }
    src.items.splice(from.i, 1);
    dst.items.splice(Math.min(to.i, dst.items.length), 0, id);
    if (m.steps && !src.items.length) { S.splice(from.s, 1); return done({ dropped: [src.id] }); }
    return done();
  }
  // Куда ведёт стрелка (↑ dir −1, ↓ +1) строки {s, i} или секции {s}: координаты для orderMove или null (дальше некуда)
  function orderStep(model, at, dir) {
    const S = (model && model.sections) || [], sec = S[at && at.s];
    if (!sec) return null;
    const up = dir < 0;
    if (at.i === undefined || at.i === null) return up ? (at.s > 0 ? { gap: at.s - 1 } : null) : (at.s < S.length - 1 ? { gap: at.s + 2 } : null);
    const n = sec.items.length, i = at.i;
    if (up && i > 0) return { s: at.s, i: i - 1 };
    if (!up && i < n - 1) return { s: at.s, i: i + 2 };
    if (model.steps) {
      if (n === 1) return up ? (at.s > 0 ? { gap: at.s - 1 } : null) : (at.s < S.length - 1 ? { gap: at.s + 2 } : null);
      return { gap: up ? at.s : at.s + 1 };                                    // ключ из шага с несколькими — своим шагом рядом
    }
    if (up) return at.s > 0 ? { s: at.s - 1, i: S[at.s - 1].items.length } : null;
    return at.s < S.length - 1 ? { s: at.s + 1, i: 0 } : null;
  }
  // Где строка или секция («@id секции»): {s, i} | {s} | null
  function orderAt(model, key) {
    const S = (model && model.sections) || [];
    if (typeof key === "string" && key.charAt(0) === "@") {
      const s = S.findIndex((x) => x.id === key.slice(1));
      return s >= 0 ? { s } : null;
    }
    for (let s = 0; s < S.length; s++) { const i = S[s].items.indexOf(key); if (i >= 0) return { s, i }; }
    return null;
  }
  // Попадание указателя -> координаты хода: hit = {s, i, after} (над строкой: нижняя половина — after), {s, head: true}
  // (шапка или пустой уровень), {end: true} (ниже всех, до подвала списка), {out: true} (вне списка — ход {out: true},
  // orderMove его отказывает: «сюда нельзя»); секцию тянут — {s, after} по половине секции
  function orderTarget(model, from, hit) {
    const S = (model && model.sections) || [];
    if (!hit || !S.length) return null;
    if (hit.out) return { out: true };
    if (from.i === undefined || from.i === null) return hit.end ? { gap: S.length } : { gap: hit.after ? hit.s + 1 : hit.s };
    if (hit.end) return model.steps ? { gap: S.length } : { s: S.length - 1, i: S[S.length - 1].items.length };
    if (hit.head) return model.steps ? { gap: hit.s } : { s: hit.s, i: 0 };
    return { s: hit.s, i: hit.after ? hit.i + 1 : hit.i };
  }

  // Ручка и поведение списка (DOM). Поднятое клавиатурой живёт здесь, а не в узлах: список перерисовывают после
  // каждого хода, ряды собираются заново (orderLifted(root) — какой ключ поднят, чтобы отметить ряд)
  let LIFT = null;                                       // {root, key, snap}
  let SAY = null;
  function say(text) {
    const h = need("orderList");
    if (!SAY || !SAY.isConnected) { SAY = h.el("div", { class: "gx-vh", role: "status", "aria-live": "polite" }); document.body.append(SAY); }
    SAY.textContent = "";
    SAY.textContent = text;
  }
  const orderLifted = (root) => (LIFT && LIFT.root === root ? LIFT.key : null);
  // конец подъёма: commit — положить (ряд остаётся, где стоит), иначе вернуть данные до подъёма (restore снаружи)
  function liftEnd(commit) {
    const l = LIFT;
    LIFT = null;
    document.removeEventListener("pointerdown", outside, true);
    if (l) document.querySelectorAll('[data-ord-root="' + CSS.escape(l.root) + '"] .is-lifted').forEach((n) => n.classList.remove("is-lifted"));
    if (l) document.querySelectorAll('[data-ord-root="' + CSS.escape(l.root) + '"] [data-ord-grip][aria-pressed="true"]').forEach((n) => n.setAttribute("aria-pressed", "false"));
    return l && !commit ? l : null;
  }
  // нажатие мимо поднявшего списка — положить там, где стоит
  function outside(ev) {
    if (!LIFT) return;
    const r = ev.target && ev.target.closest ? ev.target.closest("[data-ord-root]") : null;
    if (!r || r.getAttribute("data-ord-root") !== LIFT.root) liftEnd(true);
  }
  // FLIP (PLAN-order Ш6): после хода строки и секции переезжают с прежнего места плавно, а не прыгают. play =
  // orderFlip(root) — до перерисовки, play() — после (узлы ищутся заново по data-ord-row / data-ord-sec). Строка внутри
  // секции, которая сама поехала, сдвигается только на свою разницу. «Уменьшить движение» или нет Web Animations — без
  // анимации; во время перетаскивания строки не переставляются, так что прямоугольники для попадания не врут
  function orderFlip(root) {
    const none = () => {};
    if (!root || typeof root.querySelectorAll !== "function" || typeof matchMedia !== "function") return none;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return none;
    const SEL = "[data-ord-row], [data-ord-sec]";
    const id = (n) => (n.hasAttribute("data-ord-row") ? "r:" + n.getAttribute("data-ord-row") : "s:" + n.getAttribute("data-ord-sec"));
    const was = new Map();
    root.querySelectorAll(SEL).forEach((n) => { was.set(id(n), n.getBoundingClientRect()); });
    return () => {
      const moved = new Map();
      root.querySelectorAll(SEL).forEach((n) => {
        const b = was.get(id(n));
        if (!b || typeof n.animate !== "function") return;
        const a = n.getBoundingClientRect(), sec = n.hasAttribute("data-ord-row") ? n.closest("[data-ord-sec]") : null;
        const up = sec ? moved.get(sec) : null;
        const dx = b.left - a.left - (up ? up[0] : 0), dy = b.top - a.top - (up ? up[1] : 0);
        if (!sec) moved.set(n, [b.left - a.left, b.top - a.top]);
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
        n.animate([{ transform: "translate(" + Math.round(dx) + "px, " + Math.round(dy) + "px)" }, { transform: "none" }], { duration: 180, easing: "cubic-bezier(.2, 0, 0, 1)" });
      });
    };
  }
  // Ручка строки или секции: кнопка (клавиатура: пробел/Enter — взять и положить); key — id строки или «@id» секции;
  // pressed — поднята (aria-pressed, макет П5: пока поднята — «нажата»; список перерисовывают после каждого хода)
  function orderGrip(o) {
    const h = need("orderGrip");
    const b = h.el("button", { type: "button", class: "gx-grip" + (o.dis ? " is-dis" : ""), "data-ord-grip": o.key,
      "aria-label": h.t("kit.ord.grip", { name: o.name }), "aria-roledescription": h.t("kit.ord.role"),
      "aria-pressed": o.pressed ? "true" : "false", "aria-disabled": o.dis ? "true" : null, title: o.dis ? o.tip || "" : h.t("kit.ord.tip") }, h.icon("grip", 16));
    return b;
  }
  // o = {root, key (data-ord-root), model() -> модель, name(key), where(model, at) -> «уровень 1», onMove(from, to, how)
  //      — применить ход к данным (перерисовка — его), snapshot() — данные до подъёма, restore(snap), tip(model, from, to,
  //      hit) -> подпись линии: "" | {text, zone} («встанет по силе» — рамка у секции) | {text, target} («в один шаг с …» —
  //      секция подсвечена целиком), max, newId(model)}
  function orderList(o) {
    const h = need("orderList");
    const root = o.root;
    root.setAttribute("data-ord-root", o.key);
    const opts = { max: o.max, newId: o.newId };
    const posText = (m, key) => {
      const at = orderAt(m, key);
      if (!at) return "";
      const n = at.i === undefined ? m.sections.length : m.sections[at.s].items.length;
      return h.t("kit.ord.at", { name: o.name(key), where: o.where(m, at), i: (at.i === undefined ? at.s : at.i) + 1, n });
    };
    const lifted = () => (LIFT && LIFT.root === o.key ? LIFT : null);
    function step(key, to, how) {
      const m = o.model(), at = orderAt(m, key);
      if (!at || !to) return false;
      const r = orderMove(m, at, to, opts);
      if (!r.ok) { if (r.why === "locked" || r.why === "full") say(h.t(r.why === "full" ? "kit.ord.full" : "kit.ord.no")); return false; }
      o.onMove(at, to, how);
      return true;
    }
    function key(ev) {
      const g = ev.target && ev.target.closest ? ev.target.closest("[data-ord-grip]") : null;
      if (!g || !root.contains(g)) return;
      const k = g.getAttribute("data-ord-grip"), l = lifted();
      if (ev.key === " " || ev.key === "Enter") {
        ev.preventDefault();
        if (l && l.key === k) { liftEnd(true); say(h.t("kit.ord.dropped", { at: posText(o.model(), k) })); return; }
        if (g.getAttribute("aria-disabled") === "true") { say(g.title || h.t("kit.ord.no")); return; }   // «по соединениям»
        if (LIFT) liftEnd(true);
        LIFT = { root: o.key, key: k, snap: o.snapshot ? o.snapshot() : null };
        document.addEventListener("pointerdown", outside, true);
        g.closest("[data-ord-row], [data-ord-sec]").classList.add("is-lifted");
        if (g.setAttribute) g.setAttribute("aria-pressed", "true");
        say(h.t("kit.ord.lifted", { at: posText(o.model(), k) }));
        return;
      }
      if (!l || l.key !== k) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        const was = liftEnd(false);
        if (was && o.restore) o.restore(was.snap);
        say(h.t("kit.ord.cancel", { name: o.name(k) }));
        return;
      }
      const m = o.model(), at = orderAt(m, k);
      if (!at) return;
      let to = null;
      if (ev.key === "ArrowUp" || ev.key === "ArrowDown") to = orderStep(m, at, ev.key === "ArrowUp" ? -1 : 1);
      else if (ev.key === "Home") to = at.i === undefined ? { gap: 0 } : { s: at.s, i: 0 };
      else if (ev.key === "End") to = at.i === undefined ? { gap: m.sections.length } : { s: at.s, i: m.sections[at.s].items.length };
      else return;
      ev.preventDefault();
      if (step(k, to, "key")) say(posText(o.model(), k));
    }
    root.addEventListener("keydown", key);

    // мышь, перо и палец — только на ручке; палец: удержание ~250 мс или сдвиг на 6 px по вертикали
    let drag = null;
    const LONG_MS = 250, MOVE_PX = 6;
    // REV-order С-1: вне списка уровней — выше первого, ниже подвала списка ([data-ord-foot], «+ Уровень»; без него —
    // ниже последнего), левее или правее блока — {out: true}: «сюда нельзя», отпустили — строка на месте (макет П5 · 6)
    function hitAt(x, y, from) {
      const secs = Array.from(root.querySelectorAll("[data-ord-sec]"));
      if (!secs.length) return null;
      const first = secs[0].getBoundingClientRect(), last = secs[secs.length - 1].getBoundingClientRect(), box = root.getBoundingClientRect();
      const foot = root.querySelector("[data-ord-foot]"), bottom = Math.max(last.bottom, foot ? foot.getBoundingClientRect().bottom : last.bottom);
      if (y < first.top || y > bottom || (typeof x === "number" && (x < box.left || x > box.right))) return { out: true };
      if (y > last.bottom) return { end: true };
      let s = secs.findIndex((x) => y <= x.getBoundingClientRect().bottom);
      if (s < 0) s = secs.length - 1;
      const sr = secs[s].getBoundingClientRect();
      if (from.i === undefined) return { s, after: y > sr.top + sr.height / 2 };
      const head = secs[s].querySelector("[data-ord-head]");
      if (head && y <= head.getBoundingClientRect().bottom) return { s, head: true };
      const rows = Array.from(secs[s].querySelectorAll("[data-ord-row]"));
      if (!rows.length) return { s, head: true };
      for (let i = 0; i < rows.length; i++) {
        const r = rows[i].getBoundingClientRect();
        if (y <= r.bottom || i === rows.length - 1) return { s, i, after: y > r.top + r.height / 2 };
      }
      return { s, head: true };
    }
    function lineY(m, to) {
      const secs = Array.from(root.querySelectorAll("[data-ord-sec]"));
      const top = (n) => n.getBoundingClientRect().top, bot = (n) => n.getBoundingClientRect().bottom;
      if (to.gap !== undefined) return to.gap < secs.length ? top(secs[to.gap]) - 2 : bot(secs[secs.length - 1]) + 2;
      const rows = Array.from(secs[to.s].querySelectorAll("[data-ord-row]"));
      if (!rows.length) { const e = secs[to.s].querySelector("[data-ord-empty]") || secs[to.s]; return top(e) + e.getBoundingClientRect().height / 2; }
      return to.i < rows.length ? top(rows[to.i]) : bot(rows[rows.length - 1]);
    }
    function scroller() {
      let n = root.parentElement;
      while (n && n !== document.body) {
        const s = getComputedStyle(n);
        if (/(auto|scroll)/.test(s.overflowY) && n.scrollHeight > n.clientHeight) return n;
        n = n.parentElement;
      }
      return null;
    }
    function start() {
      drag.on = true;
      document.addEventListener("keydown", esc, true);   // только пока тянут: список перерисовывают часто
      root.classList.add("is-ord-drag");
      drag.row.classList.add("is-lifted");
      drag.grip.setAttribute("aria-pressed", "true");
      drag.line = h.el("div", { class: "gx-ord-line", "aria-hidden": "true" }, h.el("span", { class: "gx-ord-line__t" }));
      root.append(drag.line);
      drag.sc = scroller();
      if (drag.type === "touch" && navigator.vibrate) try { navigator.vibrate(10); } catch (e) { /* нет отклика */ }
      const tick = () => {
        if (!drag || !drag.on) return;
        const edge = 48, vh = document.documentElement.clientHeight, dy = drag.y < edge ? -12 : drag.y > vh - edge ? 12 : 0;
        if (dy) { if (drag.sc) drag.sc.scrollTop += dy; else (document.scrollingElement || document.documentElement).scrollTop += dy; place(); }
        drag.raf = requestAnimationFrame(tick);
      };
      drag.raf = requestAnimationFrame(tick);
      place();
    }
    function place() {
      const m = o.model(), from = orderAt(m, drag.key), hit = from ? hitAt(drag.x, drag.y, from) : null;
      const to = from ? orderTarget(m, from, hit) : null;
      const r = to ? orderMove(m, from, to, opts) : null;
      drag.to = r && r.ok ? to : null;
      const show = !!to && !!r && r.why !== "same" && r.why !== "bad";
      const no = show && !r.ok ? h.t(r.why === "full" ? "kit.ord.full" : "kit.ord.no") : "";
      drag.no = no;
      // Ш6 (макет П5): «сюда нельзя» — без линии, у поднятой строки красная рамка и подпись; «встанет по силе»
      // (o.tip отдал {text, zone}) — без линии, рамка у секции, куда встанет, подпись у её верха; «в один шаг с …»
      // ({text, target}, макет В2) — так же, секция подсвечена целиком (is-target)
      const tip = show && r.ok && o.tip ? o.tip(m, from, to, hit) : "", zone = !!(tip && tip.zone), target = !!(tip && tip.target);
      const secs = root.querySelectorAll("[data-ord-sec]");
      secs.forEach((n, i) => { n.classList.toggle("is-drop", zone && i === to.s); n.classList.toggle("is-target", target && i === to.s); });
      drag.row.classList.toggle("is-no", !!no);
      drag.line.hidden = !show;
      if (!show) return;
      drag.line.classList.toggle("is-no", !r.ok);
      drag.line.classList.toggle("is-zone", zone || target);
      drag.line.classList.toggle("is-target", target);
      drag.line.firstChild.textContent = no || (zone || target ? tip.text : (tip || ""));
      const y = !r.ok ? drag.row.getBoundingClientRect().top : zone || target ? secs[to.s].getBoundingClientRect().top : lineY(m, to);
      root.style.setProperty("--ord-y", Math.round(y - root.getBoundingClientRect().top) + "px");
    }
    function stop(commit) {
      const d = drag;
      drag = null;
      if (!d) return;
      clearTimeout(d.timer);
      if (d.raf) cancelAnimationFrame(d.raf);
      try { d.grip.releasePointerCapture(d.id); } catch (e) { /* уже отпущен */ }
      if (!d.on) return;
      document.removeEventListener("keydown", esc, true);
      root.classList.remove("is-ord-drag");
      d.row.classList.remove("is-lifted");
      d.row.classList.remove("is-no");
      d.grip.setAttribute("aria-pressed", "false");
      root.querySelectorAll("[data-ord-sec].is-drop, [data-ord-sec].is-target").forEach((n) => { n.classList.remove("is-drop"); n.classList.remove("is-target"); });
      if (d.line) d.line.remove();
      root.style.removeProperty("--ord-y");
      if (commit && d.to) { const m = o.model(), at = orderAt(m, d.key); if (at) { o.onMove(at, d.to, "drag"); say(h.t("kit.ord.dropped", { at: posText(o.model(), d.key) })); } }
      else if (commit && d.no) say(d.no);                // «сюда нельзя»: ход не применён, строка на месте
    }
    function down(ev) {
      const g = ev.target && ev.target.closest ? ev.target.closest("[data-ord-grip]") : null;
      if (!g || !root.contains(g) || ev.button > 0 || drag) return;
      if (g.getAttribute("aria-disabled") === "true") return;
      const row = g.closest("[data-ord-row], [data-ord-sec]");
      if (!row) return;
      drag = { key: g.getAttribute("data-ord-grip"), grip: g, row, id: ev.pointerId, type: ev.pointerType, x0: ev.clientX, y0: ev.clientY, x: ev.clientX, y: ev.clientY, on: false };
      try { g.setPointerCapture(ev.pointerId); } catch (e) { /* нет захвата */ }
      if (ev.pointerType === "touch") drag.timer = setTimeout(() => { if (drag && !drag.on) start(); }, LONG_MS);
      else ev.preventDefault();                          // мышь: без выделения текста; фокус — по щелчку
    }
    function move(ev) {
      if (!drag || ev.pointerId !== drag.id) return;
      drag.x = ev.clientX;
      drag.y = ev.clientY;
      if (!drag.on) {
        const dy = Math.abs(ev.clientY - drag.y0);
        if (dy >= (drag.type === "touch" ? MOVE_PX : 3)) start(); else return;
      }
      ev.preventDefault();
      place();
    }
    const up = (ev) => { if (drag && ev.pointerId === drag.id) stop(true); };
    const cancel = (ev) => { if (drag && ev.pointerId === drag.id) stop(false); };
    const esc = (ev) => { if (drag && drag.on && ev.key === "Escape") { ev.preventDefault(); stop(false); } };
    root.addEventListener("pointerdown", down);
    root.addEventListener("pointermove", move);
    root.addEventListener("pointerup", up);
    root.addEventListener("pointercancel", cancel);
    root.addEventListener("lostpointercapture", cancel);
    return { destroy() { stop(false); root.removeEventListener("keydown", key); } };
  }

  window.Kit = {
    mstTone,
    mst,
    mrow,
    mkv,
    mlist,
    msec,
    mnav,
    mhero,
    mkpis,
    evs,
    moreLine,
    init(h) {
      const miss = lacks(h, HELPERS, LAYERS);
      if (miss.length) {
        const e = new Error("Kit.init: missing " + miss.join(", "));
        try { console.error(e.message); } catch (x) { /* консоли нет */ }
        throw e;
      }
      H = Object.freeze(Object.assign({}, h));
      return true;
    },
    ready: () => !!H,
    // для компонентов этого файла и для теста «до init — ошибка»
    need,
    lacks,
    // T8 (этап 1; UX-FOUNDATION §6): записи с версией, подтверждение на месте, полоса формы, имя на месте
    DRY_MS,
    errKind,
    withTimeout,
    impactLines,
    confirmActionModel,
    confirmAction,
    rebaseModel,
    applyAgain,
    dirtyKeys,
    changedWhat,
    saveBarModel,
    saveBar,
    rename,
    renameDialog,
    idHint,
    NAME_MAX,
    // PLAN-order Ш4: порядок — перетаскивание и стрелки одним ходом
    orderMove,
    orderStep,
    orderAt,
    orderTarget,
    orderGrip,
    orderList,
    orderLifted,
    orderFlip,
  };
})();
