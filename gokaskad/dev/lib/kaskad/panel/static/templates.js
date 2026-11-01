/* Kaskad 3 — шаблоны страниц панели (window.KaskadT, в коде — const Tpl = window.KaskadT), слой L2 каркаса
   k3/handoff/UX-FOUNDATION.md §3 и §5. F0: шапка раздела, вкладки, состояния; остальные шаблоны (T1–T12) — по ходу
   задач этапа 1.

   Имя T не используем: в app.js это транспорт SSE. При загрузке файл лишь объявляет функции. Помощники, слои и Kit
   приходят один раз из app.js (boot) через
     KaskadT.init({el, t, tn, icon, isPhone, U, Kit, layers})
   Шаблоны собирают экран из переданных U.* (кнопки, вкладки, скелетон, пустое, ошибка) и Kit.*; объект ядра
   (window.KaskadPanel) не зовут. Вызов шаблона до init — ошибка в консоли и исключение.

   API (F0):
     Tpl.head(o)   o: {title, text, action: {label, onClick, icon}, more: [пункты U.menu], moreLabel} — шапка
                   раздела: заголовок, одна строка пояснения, одна акцентная кнопка справа, «⋯» для редкого
     Tpl.tabs(o)   o: {items: [{id, label, icon, n, dis}], value, onChange(id), aria} — вкладки раздела на U.seg
                   (role=tablist); адрес вкладки — ?tab=<id> (опись SECTIONS в app.js)
     Tpl.state(kind, o)  kind: loading | empty | error
                   loading — o: {rows, card}; empty — o: {icon, title, text, action: Node}; error — o: {title, text, onRetry} */
"use strict";

(() => {
  const HELPERS = ["el", "t", "tn", "icon", "isPhone", "U", "Kit", "layers"];
  const LAYERS = ["openDialog", "openSheet", "openMenu", "confirmDialog", "typedConfirm", "toast"];
  let H = null;

  function fail(msg) {
    const e = new Error(msg);
    try { console.error(e.message); } catch (x) { /* консоли нет */ }
    throw e;
  }
  const need = (what) => H || fail("KaskadT." + what + ": KaskadT.init was not called");

  function head(o = {}) {
    const h = need("head");
    const tt = h.el("div", { class: "gx-card__tt" }, h.el("h2", { class: "gx-h2", text: o.title || "" }));
    if (o.text) tt.append(h.el("div", { class: "gx-desc", text: o.text }));
    const n = h.el("div", { class: "gx-card__head" }, h.el("div", { class: "gx-card__lead" }, tt));
    const acts = [];
    if (o.action) acts.push(h.U.btn(o.action.label, "primary", o.action.onClick, { md: true, icon: o.action.icon || null }));
    if (o.more && o.more.length) acts.push(h.U.menu(o.more, { label: o.moreLabel || o.title || "" }));
    if (acts.length) n.append(h.el("div", { class: "gx-card__acts" }, acts));
    return n;
  }

  function tabs(o = {}) {
    const h = need("tabs");
    const pick = (id) => () => { if (id !== o.value && o.onChange) o.onChange(id); };
    const items = (o.items || []).map((it) => ({ label: it.label, icon: it.icon || null, n: it.n, dis: !!it.dis, on: it.id === o.value, pick: pick(it.id) }));
    return h.U.seg(items, { tabs: true, aria: o.aria || h.t("kit.tabs") });
  }

  function state(kind, o = {}) {
    const h = need("state");
    if (kind === "loading") return h.U.skel(o.rows || 3, { card: !!o.card });
    if (kind === "empty") return h.U.empty(o.icon || "circle-info", o.title || h.t("kit.empty"), o.text || "", { btns: o.action ? [o.action] : [] });
    if (kind === "error") return h.U.state("error", o.text || "", o.onRetry || null, o.title || null);
    return fail("KaskadT.state: unknown kind " + kind);
  }

  window.KaskadT = {
    init(h) {
      const miss = [];
      HELPERS.forEach((k) => { if (!h || !h[k]) miss.push(k); });
      if (h && h.layers) LAYERS.forEach((k) => { if (typeof h.layers[k] !== "function") miss.push("layers." + k); });
      if (miss.length) fail("KaskadT.init: missing " + miss.join(", "));
      H = Object.freeze(Object.assign({}, h));
      return true;
    },
    ready: () => !!H,
    head,
    tabs,
    state,
  };
})();
