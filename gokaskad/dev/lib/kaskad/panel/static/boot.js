/* Kaskad — первый кадр. Грузится в <head> до стилей и ставит вид из копии в localStorage, пока app.js не получил его с
   узла (GET /api/appearance): иначе тёмная панель мигала бы белым на каждой загрузке. Внешний файл, а не вложенный
   скрипт: CSP панели — script-src 'self' (без хэшей и 'unsafe-inline').

   Здесь же — общий для панели и мастера расчёт акцента (window.KaskadLook, docs/k3/DESIGN-SYSTEM.md): цвета акцентов
   «Вида» и подбор цвета текста на акценте и текста акцентом по контрасту (не ниже 4,5 : 1, WCAG). app.js берёт их
   отсюда, а не держит свою копию. */
(function () {
  "use strict";

  // ── Цвет: oklch(L C H) и #rrggbb → линейный sRGB → яркость; контраст по WCAG 2 ──
  function parse(c) {
    var s = String(c || "").trim(), m;
    if ((m = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)/i.exec(s))) {
      return { L: Number(m[1]) / (m[2] ? 100 : 1), C: Number(m[3]), H: Number(m[4]) };
    }
    if ((m = /^#([0-9a-f]{6})$/i.exec(s))) {
      var n = parseInt(m[1], 16);
      return fromRgb([(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }));
    }
    return null;
  }
  function toLin(o) {                                    // oklch → линейный sRGB (Björn Ottosson, OKLab)
    var h = o.H * Math.PI / 180, a = o.C * Math.cos(h), b = o.C * Math.sin(h);
    var l = Math.pow(o.L + 0.3963377774 * a + 0.2158037573 * b, 3);
    var m = Math.pow(o.L - 0.1055613458 * a - 0.0638541728 * b, 3);
    var s = Math.pow(o.L - 0.0894841775 * a - 1.2914855480 * b, 3);
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s].map(function (v) { return Math.max(0, Math.min(1, v)); });
  }
  function fromRgb(rgb) {                                // линейный sRGB → oklch
    var l = Math.cbrt(0.4122214708 * rgb[0] + 0.5363325363 * rgb[1] + 0.0514459929 * rgb[2]);
    var m = Math.cbrt(0.2119034982 * rgb[0] + 0.6806995451 * rgb[1] + 0.1073969566 * rgb[2]);
    var s = Math.cbrt(0.0883024619 * rgb[0] + 0.2817188376 * rgb[1] + 0.6299787005 * rgb[2]);
    var L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
    var a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
    var b = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;
    var H = Math.atan2(b, a) * 180 / Math.PI;
    return { L: L, C: Math.sqrt(a * a + b * b), H: H < 0 ? H + 360 : H };
  }
  function lum(o) { var r = toLin(o); return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]; }
  function ratio(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  // смесь в OKLab, как color-mix(in oklab, a p, b): мягкий фон акцента поверх поверхности
  function mix(a, b, p) {
    var ha = a.H * Math.PI / 180, hb = b.H * Math.PI / 180;
    var L = a.L * p + b.L * (1 - p), x = a.C * Math.cos(ha) * p + b.C * Math.cos(hb) * (1 - p), y = a.C * Math.sin(ha) * p + b.C * Math.sin(hb) * (1 - p);
    var H = Math.atan2(y, x) * 180 / Math.PI;
    return { L: L, C: Math.sqrt(x * x + y * y), H: H < 0 ? H + 360 : H };
  }
  function str(o) { return "oklch(" + o.L.toFixed(3) + " " + o.C.toFixed(3) + " " + o.H.toFixed(1) + ")"; }
  var WHITE = { L: 1, C: 0, H: 0 };
  var SURF = { dark: { L: 0.205, C: 0.015, H: 255 }, light: { L: 1, C: 0, H: 0 } };   // --surface палитры v2

  /* tones(accent, theme) → { fg, text, near }:
     fg   — текст на акценте: белый, если контраст не ниже 4,5 : 1, иначе тёмный тон того же оттенка (у кораллового,
            мятного, неба — тёмный); если не дотягивают оба — тот, что контрастнее;
     text — акцент для текста и значков на поверхности: светлее в тёмной теме, темнее в светлой, до 4,5 : 1;
     near — оттенок ближе 40° к красному (опасность, oklch ≈ 25°): опасные действия остаются мягкими и со словом. */
  function tones(accent, theme) {
    var a = parse(accent);
    if (!a) return { fg: "#fff", text: null, near: false };
    var dark = { L: 0.2, C: Math.min(a.C, 0.05), H: a.H };
    var cw = ratio(a, WHITE), cd = ratio(a, dark);
    var fg = cw >= 4.5 ? "#fff" : (cd >= 4.5 || cd > cw ? str(dark) : "#fff");
    var surf = SURF[theme === "dark" ? "dark" : "light"];
    // текст акцентом стоит и на поверхности, и на мягком фоне акцента (метка «рекомендуем», шаг, кубик «Случайная»):
    // 4,5 : 1 — против худшего из двух (доля мягкого фона — как --accent-soft в tokens.css)
    var soft = mix(a, surf, theme === "dark" ? 0.17 : 0.13);
    var worst = function (x) { return Math.min(ratio(x, surf), ratio(x, soft)); };
    var t = { L: a.L, C: a.C, H: a.H };
    if (theme === "dark") {
      t.L = Math.max(t.L, 0.72); t.C = Math.min(t.C, 0.14);
      while (worst(t) < 4.5 && t.L < 0.97) t.L += 0.01;
    } else {
      t.L = Math.min(t.L, 0.56); t.C = Math.min(t.C, 0.17);
      while (worst(t) < 4.5 && t.L > 0.2) t.L -= 0.01;
    }
    var dh = Math.abs(((a.H - 25) % 360 + 540) % 360 - 180);
    return { fg: fg, text: str(t), near: a.C > 0.06 && dh < 40, ratio: Math.max(cw, cd) };
  }

  /* Акценты «Вида». auto — по умолчанию (решение владельца 3.3.0, как мастер): мятный в тёмной теме, фиолетовый в
     светлой; ga/kaskad — синий «Каскад» (готовая тема); коралловый, мятный и лаванда — значения дизайн-системы. */
  var AUTO = { dark: "#45D0C0", light: "oklch(0.53 0.17 288)" };
  function accents(theme, hue) {
    var h = Math.max(0, Math.min(359, Math.round(Number(hue)) || 0));
    return {
      auto: theme === "dark" ? AUTO.dark : AUTO.light,
      ga: "oklch(0.54 0.13 222)", kaskad: "oklch(0.54 0.13 222)", blue: "oklch(0.6204 0.195 253.83)", sky: "oklch(0.72 0.13 228)",
      lavender: "oklch(0.62 0.17 290)", mint: "#45D0C0", coral: "oklch(0.68 0.17 38)",
      graphite: theme === "dark" ? "oklch(0.95 0 0)" : "oklch(0.24 0.006 286)", custom: "oklch(0.63 0.17 " + h + ")"
    };
  }

  /* apply(root, accent, theme) — переменные акцента на <html>: --accent, --accent-foreground, --accent-text,
     data-accent-near (оттенок рядом с опасностью). */
  function apply(root, accent, theme) {
    var tn = tones(accent, theme);
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-foreground", tn.fg);
    if (tn.text) root.style.setProperty("--accent-text", tn.text); else root.style.removeProperty("--accent-text");
    root.setAttribute("data-accent-near", tn.near ? "danger" : "");
  }

  window.KaskadLook = { parse: parse, ratio: ratio, tones: tones, accents: accents, apply: apply };

  // ── Язык (3.3.2): один на сервер (KASKAD_LANG, выбор при установке; docs/k3/GLOSSARY-en.md, k3/handoff/I18N-3.3.2.md).
  // Сервер ставит его в <html lang> страницы панели и мастера при отдаче (__KASKAD_LANG__) — первый кадр сразу на
  // нужном языке. Без сервера (разработка, ?mock=1) — по языку браузера: ru* → русский, иначе English.
  // window.KaskadLang — общий для панели, «Схемы» и мастера. Страница человека (user.js) решает язык сама.
  function browserLang() {
    var list = [];
    try { list = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]) || []; } catch (e) { list = []; }
    for (var i = 0; i < list.length; i++) {
      var l = String(list[i] || "").toLowerCase();
      if (l) return l.slice(0, 2) === "ru" ? "ru" : "en";        // первый язык браузера решает
    }
    return "ru";
  }
  // Локаль Intl: русский — ru-RU; английский — английская локаль браузера (en-GB, en-AU…), иначе en-US.
  function localeOf(lang) {
    if (lang === "ru") return "ru-RU";
    try {
      var list = navigator.languages || [navigator.language];
      for (var i = 0; i < list.length; i++) if (/^en(-|$)/i.test(String(list[i] || ""))) return String(list[i]);
    } catch (e) { /* нет navigator */ }
    return "en-US";
  }
  var htmlLang = "";
  try { htmlLang = String(document.documentElement.getAttribute("lang") || "").toLowerCase(); } catch (e) { htmlLang = ""; }
  var devLang = "";                  // разработка без сервера: ?lang=en|ru (под сервером <html lang> уже подставлен)
  try { devLang = String(new URLSearchParams(location.search).get("lang") || ""); } catch (e) { devLang = ""; }
  var lang = htmlLang === "ru" || htmlLang === "en" ? htmlLang : (devLang === "ru" || devLang === "en" ? devLang : browserLang());
  var pr = null;
  try { pr = new Intl.PluralRules(lang); } catch (e) { pr = null; }
  // Даты (3.3.2, находка №2 живой проверки): одно правило с ядром (kaskad.i18n.fmt_date / fmt_datetime) и SSH (k3_date):
  // русский — «09.01.2027», английский — «Jan 9, 2027» (en-GB браузера — «9 Jan 2027»), никогда не 01/09/2027.
  // Дата и время — по часам браузера, 24 часа; год — только если не текущий: «12.10, 02:40» / «Oct 12, 02:40»,
  // иначе «09.01.2027, 02:40» / «Jan 9, 2027, 02:40». Значение — Date, секунды эпохи или текст ISO; не дата — "".
  // Чистая функция (тесты вырезают её и зовут в node со своим «сейчас»); toLocale*String без опций — нигде.
  function kaskadDates(lang, locale, now) {
    var DAY = lang === "ru" ? { day: "2-digit", month: "2-digit" } : { month: "short", day: "numeric" };
    var CLOCK = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    var fmts = {};
    var nowOf = now || function () { return new Date(); };
    function fmtOf(year, clock) {
      var k = (year === null ? "n" : year ? "y" : "d") + (clock ? "c" : "");
      if (!fmts[k]) {
        var o = {}, x;
        if (year !== null) { for (x in DAY) o[x] = DAY[x]; if (year) o.year = "numeric"; }
        if (clock) for (x in CLOCK) o[x] = CLOCK[x];
        fmts[k] = new Intl.DateTimeFormat(lang === "ru" ? "ru-RU" : (locale || "en-US"), o);
      }
      return fmts[k];
    }
    function dateOf(v) {
      if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
      var d = v instanceof Date ? v : (typeof v === "number" || /^\d+(\.\d+)?$/.test(String(v))) ? new Date(Number(v) * 1000) : new Date(String(v));
      return isNaN(d.getTime()) || (!(v instanceof Date) && d.getTime() <= 0) ? null : d;
    }
    return {
      // «09.01.2027» / «Jan 9, 2027»
      date: function (v) { var d = dateOf(v); return d ? fmtOf(true, false).format(d) : ""; },
      // «19.10» / «Oct 19» в этом году, иначе с годом — короткая дата для узких колонок (UI-4, «Люди»: «с …»)
      day: function (v) { var d = dateOf(v); return d ? fmtOf(d.getFullYear() !== nowOf().getFullYear(), false).format(d) : ""; },
      // «12.10, 02:40» / «Oct 12, 02:40» в этом году, иначе с годом; year === true — всегда с годом
      dateTime: function (v, year) {
        var d = dateOf(v);
        if (!d) return "";
        return fmtOf(year === true || d.getFullYear() !== nowOf().getFullYear(), true).format(d);
      },
      // «02:40» (24 часа)
      time: function (v) { var d = dateOf(v); return d ? fmtOf(null, true).format(d) : ""; }
    };
  }
  var locale = localeOf(lang);
  var dates = kaskadDates(lang, locale);
  window.KaskadLang = {
    lang: lang,                       // язык этой страницы (меняется только перезагрузкой)
    server: lang === htmlLang,        // язык пришёл с сервера (иначе — догадка по браузеру)
    locale: locale,
    date: dates.date,                 // даты — kaskadDates выше
    day: dates.day,
    dateTime: dates.dateTime,
    time: dates.time,
    // формы числа «одна|несколько|много» (русский) или «one|other» (английский)
    form: function (n, forms) {
      var f = String(forms).split("|"), c = pr ? pr.select(Math.abs(Number(n) || 0)) : "other";
      var i = c === "one" ? 0 : c === "few" ? 1 : 2;
      return f[Math.min(i, f.length - 1)];
    }
  };
  try { document.documentElement.setAttribute("lang", lang); } catch (e) { /* нет документа */ }

  // ── Первый кадр ──
  var root = document.documentElement;
  var ap = {};
  try { ap = JSON.parse(localStorage.getItem("kaskad-appearance-v1") || "{}") || {}; } catch (e) { ap = {}; }
  var pick = function (v, list, d) { return list.indexOf(v) !== -1 ? v : d; };
  // Тема по умолчанию — тёмная (решение владельца 3.3.2), не из системы. «Вид» до 3.3.2 (v меньше 3) записывал
  // «как в системе» вместе с любой другой правкой — это прежнее умолчание, а не выбор: он становится тёмной (как
  // migrateLook в app.js и appearance_read на сервере). Светлая и тёмная, выбранные явно, не трогаются.
  var ver = Number(ap.v) || 1;
  var theme = pick(ap.theme, ["light", "dark", "system"], "dark");
  if (theme === "system" && ver < 3) theme = "dark";
  var th = theme;
  if (theme === "system") {
    try { th = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; } catch (e) { th = "dark"; }
  }
  var AC = accents(th, parseInt(ap.hue, 10) || 200);
  var phone = false;
  try { phone = matchMedia("(max-width: 699.98px)").matches; } catch (e) { phone = false; }
  var radius = parseInt(ap.radius, 10);
  root.setAttribute("data-hero", "");
  root.setAttribute("data-theme", th);
  root.setAttribute("data-tint", pick(ap.tint, ["neutral", "warm", "cool"], "neutral"));
  root.setAttribute("data-density", pick(ap.density, ["min", "balanced", "detailed"], "detailed"));
  root.setAttribute("data-btn", pick(ap.btn, ["pill", "round"], "pill"));
  root.setAttribute("data-cards", pick(ap.cards, ["shadow", "border", "flat"], "shadow"));
  // «Размер текста» (Т3 №17) — в этом браузере (kaskad-font); не выбран — прежний «Вид» (fs), затем «обычный»
  var fz = null;
  try { fz = localStorage.getItem("kaskad-font"); } catch (e) { fz = null; }
  root.setAttribute("data-fs", pick(fz, ["s", "m", "l"], pick(ap.fs, ["s", "m", "l"], "m")));
  root.setAttribute("data-vibrant", ap.vibrant === true ? "true" : "false");
  root.setAttribute("data-motion", ap.motion === false ? "off" : "on");
  root.setAttribute("data-nav", pick(ap.nav, ["full", "icons", "top"], "full"));
  root.setAttribute("data-device", phone ? "phone" : "desktop");
  // «Вид» до 3.3.0 (v меньше 2) с прежним синим по умолчанию — новое умолчание (как migrateLook в app.js)
  apply(root, AC[ver < 2 && ap.accent === "ga" ? "auto" : ap.accent] || AC.auto, th);
  root.style.setProperty("--radius", Math.max(0, Math.min(16, radius >= 0 ? radius : 8)) + "px");
  // «Режим стрима» (stream.js) — флаг этого браузера: «1» — включён с первого кадра; нет ключа или мусор — выключен
  var sm = null;
  try { sm = localStorage.getItem("kaskad.stream"); } catch (e) { sm = null; }
  if (sm === "1") root.setAttribute("data-stream", "on");
}());
