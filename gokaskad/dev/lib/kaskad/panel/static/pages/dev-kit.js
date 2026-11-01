/* Kaskad 3 — раздел «Витрина компонентов» (#dev-kit), пробный ленивый раздел каркаса F0
   (k3/handoff/UX-FOUNDATION.md §3.3, §7). Его нет в меню: открывается по адресу #dev-kit, вкладка — #dev-kit?tab=…
   Грузит его K.loadPage("dev-kit") при первом входе: pages/dev-kit-i18n-ru.js, при английском ещё -en.js поверх
   (нет ключа — русский текст), затем этот файл; он регистрирует раздел через K.page(id, def). Экран собирается только
   из шаблонов (K.Tpl) и U.*; классы компонентов руками не пишет. Витрину с осями «Вида» достраивает F2. */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const Tpl = K.Tpl;
  const t = K.t;
  const ID = "dev-kit";
  const TABS = [["loading", "dk.tabLoading"], ["empty", "dk.tabEmpty"], ["error", "dk.tabError"]];
  let tabsBox = null;
  let body = null;

  function draw(tab) {
    if (!body) return;
    const cur = TABS.some((x) => x[0] === tab) ? tab : TABS[0][0];
    tabsBox.replaceChildren(Tpl.tabs({ items: TABS.map((x) => ({ id: x[0], label: t(x[1]) })), value: cur, onChange: (id) => K.goNav(ID, { tab: id }) }));
    let st;
    if (cur === "empty") st = Tpl.state("empty", { text: t("dk.emptyText"), action: K.U.btn(t("dk.act"), "tertiary", () => K.toast(t("dk.actDone"), "", "info"), { md: true }) });
    else if (cur === "error") st = Tpl.state("error", { text: t("dk.errText"), onRetry: () => K.toast(t("dk.retried"), "", "info") });
    else st = Tpl.state("loading", { rows: 3 });
    body.replaceChildren(st);
  }

  K.page(ID, {
    res: [],
    build(root) {
      tabsBox = K.el("div");
      body = K.el("div");
      const head = Tpl.head({ title: K.pageTitle(ID), text: t("dk.text"),
        action: { label: t("dk.act"), onClick: () => K.toast(t("dk.actDone"), "", "info") },
        more: [{ label: t("dk.menuItem"), on: () => K.toast(t("dk.actDone"), "", "info") }] });
      root.append(K.U.sec("", "", [head, tabsBox, body]));
      draw(K.navState().tab);
    },
    // смена вкладки (#dev-kit?tab=…, «Назад» браузера) — без пересборки раздела
    nav(n) {
      if (!body) return false;
      draw(n.tab);
      return true;
    },
    leave() { tabsBox = null; body = null; },
  });
})();
