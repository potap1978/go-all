/* Kaskad — тема мастера «Быстрый старт» (3.3.2 №1, решение владельца): мастер открывается тёмным, светлая — только
   по переключателю; тему ОС (prefers-color-scheme) по умолчанию не берём. Выбор хранится в том же ключе «Вида», что
   у панели (localStorage «kaskad-appearance-v1»), и в её формате (normLook/migrateLook в app.js, первый кадр —
   boot.js): объект «Вида» с v: 3; нет темы — тёмная; «system» без v: 3 (умолчание старых версий) — тёмная; «system»
   с v: 3 — явный выбор «как в системе», ему мастер следует, как панель. Поэтому мастер и панель в одном браузере
   показывают одну тему. Мастер меняет только theme (прочие поля «Вида» — как есть, с теми же переводами старых
   версий, что делает панель) и пишет v: 3.

   Грузится синхронно в <head> сразу после ../boot.js — и до первой отрисовки переводит страницу мастера на свою
   тему: без белой вспышки. Внешний файл: CSP — script-src 'self'.
   API: window.KaskadSetupLook.theme() -> "dark" | "light"; .set("dark" | "light") — запомнить выбор. */
(function () {
  "use strict";
  var KEY = "kaskad-appearance-v1";
  var ACCENT = { dark: "#45D0C0", light: "oklch(.53 .17 288)" };      // как setup.js: мятный в тёмной, фиолетовый в светлой

  function read() {
    try {
      var x = JSON.parse(localStorage.getItem(KEY) || "null");
      return x && typeof x === "object" && !Array.isArray(x) ? x : null;
    } catch (e) { return null; }
  }
  var V = 3;                                             // LOOK_V панели (app.js)
  function migrate(x) {                                  // как migrateLook в app.js
    var v = Number(x.v) || 1;
    if (v < 2 && x.accent === "ga") x.accent = "auto";
    if (v < 3 && x.theme === "system") x.theme = "dark";
    return x;
  }
  function theme() {
    var x = read(), th = x && migrate(x).theme;
    if (th === "light") return "light";
    if (th === "system") {
      try { return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; } catch (e) { return "dark"; }
    }
    return "dark";
  }
  function set(th) {
    if (th !== "light" && th !== "dark") return false;
    var x = migrate(read() || {});
    x.v = V;
    x.theme = th;
    try { localStorage.setItem(KEY, JSON.stringify(x)); return true; } catch (e) { return false; }   // приватное окно: только на эту вкладку
  }

  window.KaskadSetupLook = { KEY: KEY, theme: theme, set: set, read: read };

  // первый кадр мастера
  var root = document.documentElement, th = theme();
  root.setAttribute("data-theme", th);
  if (window.KaskadLook) window.KaskadLook.apply(root, ACCENT[th], th);
}());
