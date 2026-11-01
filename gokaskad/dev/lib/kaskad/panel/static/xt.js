/* Kaskad — xt.js: «строка списка с раскрытием» gx-xt и переключатель плотности (UI-4, Claude Design People r1 К1,
   HANDOFF «gx-xt»). Общий блок для «Людей», потом «Выходов» и «Пробросов» (k3/handoff/process/BLOCKS.md): без зависимостей
   от app.js, тексты приходят параметрами (готовых фраз здесь нет), состояние плотности — в браузере, каждый список
   отдельно под своим ключом (параметр key — ключ хранилища целиком; try/catch: приватное окно и запрет хранилища —
   просто «Средне»). Классы продукта — тоже параметрами (o.cls); у блока свои: gx-xt*, gx-dn, kk-dn--*.

   Строение (стили — xt.css):
     div.gx-xt.gx-xt--<сетка>          таблица; колонки — --cols (у списка своя сетка, xt.css)
       div.gx-xt__hw > div.gx-xt__h    шапка (липкая); вторая копия — для «Строки» (две колонки), aria-hidden
       div.gx-xt__list
         div.gx-xt__g[.is-open]        группа: строка + подстроки
           div.gx-xt__r                строка (нажатие по свободному месту — раскрыть)
             button.gx-xt__x[aria-expanded][aria-controls]   кнопка раскрытия (Enter / пробел)
           div.gx-xt__sub[hidden]      подстроки (устройства), id — для aria-controls
             div.gx-xt__r.gx-xt__r--sub[tabindex=-1]          строка подстроки (фокус стрелками)
   Стрелки вверх/вниз ходят по видимым строкам: кнопкам раскрытия и строкам раскрытых подстрок (Home / End — края).
   Плашки в ячейке (slots): span.gx-xt__slots > [data-si=i] — плашки одним порядком, плотно слева, + span.gx-xt__more
   «+N». Сколько помещается — по ширине ячейки у каждой строки (XT.slots): лишние скрыты, в «+N» — сколько скрыто
   (полные имена — в подсказке); плашки не сжимаются и не обрезаются. --slot-max у ячейки — не больше N плашек, дальше
   «+N»; --pw у таблицы — самая широкая ячейка по тому, что в ней показано: сетка может взять её пределом колонки.
   Плотность — класс kk-dn--lg | kk-dn--md | kk-dn--row на обёртке списка: переключение не пересобирает строки. */
(function (g) {
  "use strict";
  var DN = ["lg", "md", "row"];
  var seq = 0;
  // значки плотности (Claude Design People Activity r1: i-dn-lg / i-dn-md / i-dn-row), 16 × 16, линией
  var DN_ICON = {
    lg: [["rect", { x: 2.5, y: 2.5, width: 11, height: 4.5, rx: 1.2 }], ["rect", { x: 2.5, y: 9, width: 11, height: 4.5, rx: 1.2 }]],
    md: [["path", { d: "M2.5 3.5h11M2.5 6.5h11M2.5 9.5h11M2.5 12.5h11" }]],
    row: [["path", { d: "M2 3.5h5M2 6.5h5M2 9.5h5M2 12.5h5M9 3.5h5M9 6.5h5M9 9.5h5M9 12.5h5" }]]
  };
  // значки сворачиваемого столбца (Claude Design it1 Д3: i-ccol — «свернуть», i-cexp — «развернуть»), 16 × 16, линией
  DN_ICON.ccol = [["path", { d: "M2.5 4.5 6 8l-3.5 3.5M13.5 4.5 10 8l3.5 3.5" }]];
  DN_ICON.cexp = [["path", { d: "M5.5 4.5 2 8l3.5 3.5M10.5 4.5 14 8l-3.5 3.5" }]];
  function dnIcon(d, cls) {
    var NS = "http://www.w3.org/2000/svg", v = document.createElementNS(NS, "svg");
    v.setAttribute("class", cls || "gx-dn__ic");
    v.setAttribute("viewBox", "0 0 16 16");
    v.setAttribute("width", "16");
    v.setAttribute("height", "16");
    v.setAttribute("aria-hidden", "true");
    v.setAttribute("focusable", "false");
    DN_ICON[d].forEach(function (x) { var n = document.createElementNS(NS, x[0]); Object.keys(x[1]).forEach(function (k) { n.setAttribute(k, String(x[1][k])); }); v.appendChild(n); });
    return v;
  }
  function lsGet(k) { try { return g.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { g.localStorage.setItem(k, v); } catch (e) { /* хранилище закрыто — помним до перезагрузки */ } }
  var mem = {};
  function mk(tag, cls, attrs) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (attrs) { Object.keys(attrs).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) { n.setAttribute(k, String(attrs[k])); } }); }
    return n;
  }
  var XT = {
    DENSITIES: DN.slice(),
    // плотность списка: сохранённая под ключом key, иначе «Средне»
    density: function (key) {
      var v = lsGet(key) || mem[key];
      return DN.indexOf(v) >= 0 ? v : "md";
    },
    setDensity: function (key, v) {
      if (DN.indexOf(v) < 0) { return; }
      mem[key] = v;
      lsSet(key, v);
    },
    applyDensity: function (node, v) {
      if (!node) { return; }
      DN.forEach(function (d) { node.classList.toggle("kk-dn--" + d, d === v); });
      // у плотности своя сетка: свёрнутые столбцы — заново по ней
      Array.prototype.forEach.call(node.querySelectorAll ? node.querySelectorAll(".gx-xt") : [], function (t) { if (t.__xtCc) { XT.ccFit(t); } });
    },
    // Переключатель «Крупно · Средне · Строка»: role=radiogroup, стрелки влево/вправо, выбранный — в порядке Tab.
    // o: {key — ключ хранилища, labels: {lg, md, row}, tips, aria, cls — ещё класс, icons — значок перед словом (на узком
    // окне слово прячет стиль списка, остаётся значок), target: () => узел с классом плотности, onChange(v)}
    densitySeg: function (o) {
      var cur = XT.density(o.key);
      var box = mk("div", "gx-seg gx-dn" + (o.cls ? " " + o.cls : ""), { role: "radiogroup", "aria-label": o.aria || null });
      var btns = DN.map(function (d) {
        var b = mk("button", null, { type: "button", role: "radio", "data-dn": d, title: (o.tips && o.tips[d]) || null });
        if (o.icons) {
          b.setAttribute("aria-label", o.labels[d]);
          b.appendChild(dnIcon(d));
          var w = mk("span", "gx-dn__t");
          w.textContent = o.labels[d];
          b.appendChild(w);
        } else { b.textContent = o.labels[d]; }
        b.addEventListener("click", function () { pick(d, false); });
        box.appendChild(b);
        return b;
      });
      function mark() {
        btns.forEach(function (b) {
          var on = b.getAttribute("data-dn") === cur;
          b.setAttribute("aria-checked", String(on));
          b.tabIndex = on ? 0 : -1;
        });
      }
      function pick(d, focus) {
        cur = d;
        XT.setDensity(o.key, d);
        mark();
        XT.applyDensity(o.target ? o.target() : null, d);
        if (focus) { btns[DN.indexOf(d)].focus(); }
        if (o.onChange) { o.onChange(d); }
      }
      box.addEventListener("keydown", function (ev) {
        var i = DN.indexOf(cur), k = ev.key;
        if (k === "ArrowRight" || k === "ArrowDown") { i = (i + 1) % DN.length; }
        else if (k === "ArrowLeft" || k === "ArrowUp") { i = (i + DN.length - 1) % DN.length; }
        else if (k === "Home") { i = 0; }
        else if (k === "End") { i = DN.length - 1; }
        else { return; }
        ev.preventDefault();
        pick(DN[i], true);
      });
      mark();
      return box;
    },
    // Таблица: o {cls, head: [{t, cls, k, cc}], twin — вторая шапка для «Строки», cc — сворачиваемые столбцы (ниже)};
    // список — узел или массив групп
    table: function (o, list) {
      var t = mk("div", "gx-xt" + (o.cls ? " " + o.cls : ""));
      var hw = mk("div", "gx-xt__hw");
      var cc = o.cc && o.cc.key ? { key: o.cc.key, text: o.cc.text || {}, set: XT.ccGet(o.cc.key), labels: {}, onChange: o.cc.onChange || null,
        auto: o.cc.auto && o.cc.auto.guard ? o.cc.auto : null, am: [], keep: {} } : null;
      [0, 1].forEach(function (i) {
        if (i && !o.twin) { return; }
        var h = mk("div", "gx-xt__h", i ? { "aria-hidden": "true" } : null);
        o.head.forEach(function (c) {
          if (cc && c.cc && c.k) { cc.labels[c.k] = c.t || ""; h.appendChild(XT.ccHead(t, c, !!i)); return; }
          var s = mk("span", c.cls || null); s.textContent = c.t || ""; h.appendChild(s);
        });
        hw.appendChild(h);
      });
      var l = list && list.nodeType ? list : mk("div", "gx-xt__list");
      if (Array.isArray(list)) { list.forEach(function (n) { if (n) { l.appendChild(n); } }); }
      t.appendChild(hw);
      t.appendChild(l);
      if (cc) {
        // свёрнутые ключи, которых в этой таблице нет (столбец убран из списка), — не считаются и не хранятся заново
        cc.set = cc.set.filter(function (k) { return Object.prototype.hasOwnProperty.call(cc.labels, k); });
        t.__xtCc = cc;
        XT.ccApply(t, true);
      }
      XT.bindKeys(l);
      XT.slots(t, o.slots || {});
      return t;
    },
    // ── Сворачиваемые столбцы (круг 1 №25, решение владельца 12; HANDOFF it1 «Сворачивание столбцов gx-xt») ──
    // Общий узор списков gx-xt («Выходы», потом «Пользователи», «Пробросы»): заголовок столбца с c.cc — кнопка
    // button.gx-xt__hc[data-ck] (aria-expanded, aria-label «Свернуть / Развернуть столбец «…»», подсказка gx-xt__tip на
    // наведении и фокусе). Свёрнутый — is-cc: колонка 24 px (в --cols через CSSOM вместо её ширины, ccFit), ячейки
    // .gx-xt__c[data-ck] — gx-xt__c--cc: полоска во всю высоту строки, содержимое скрыто, значение — в title ячейки;
    // узлы [data-ccx=k] (строки раскрытия) видны, только пока столбец k свёрнут. Выбор — в браузере под o.cc.key
    // (массив свёрнутых ключей, как плотность — свой ключ у каждого списка); тексты — o.cc.text: collapse(l),
    // expand(l), tipC(l), tipE(l), cell(l, v) — функции (готовых фраз здесь нет). o.cc.onChange(set) — после нажатия.
    ccGet: function (key) {
      var raw = lsGet(key), v = null;
      if (raw === null || raw === undefined) { raw = mem[key]; }
      try { v = typeof raw === "string" ? JSON.parse(raw) : raw; } catch (e) { v = null; }
      return Array.isArray(v) ? v.filter(function (k, i, a) { return typeof k === "string" && k && a.indexOf(k) === i; }).slice(0, 32) : [];
    },
    ccSet: function (key, set) {
      var v = JSON.stringify(set || []);
      mem[key] = v;
      lsSet(key, v);
    },
    ccHead: function (t, c, twin) {
      var b = mk("button", "gx-xt__hc" + (c.cls ? " " + c.cls : ""), { type: "button", "data-ck": c.k, tabindex: twin ? "-1" : null });
      // c.tr — подпись для «Строки» (узкая колонка; Д2 r3 «За сутки»): две подписи, плотность показывает свою (xt.css)
      var l = mk("span", "gx-xt__hl" + (c.tr ? " kk-nrow" : ""));
      l.textContent = c.t || "";
      if (c.t) { l.setAttribute("title", c.t); }                 // узкая колонка (окно 1100, текст «крупнее») — подпись целиком
      b.appendChild(l);
      if (c.tr) { var lr = mk("span", "gx-xt__hl kk-nmd kk-nlg"); lr.textContent = c.tr; b.appendChild(lr); }
      b.appendChild(dnIcon("ccol", "gx-xt__hi"));
      b.appendChild(mk("span", "gx-xt__tip", { "aria-hidden": "true" }));
      b.addEventListener("click", function (ev) { ev.stopPropagation(); XT.ccToggle(t, c.k); });
      return b;
    },
    ccToggle: function (t, k) {
      var cc = t && t.__xtCc;
      if (!cc) { return; }
      var i = cc.set.indexOf(k);
      // свёрнут сам (auto, мало места) — нажатие разворачивает его до перезагрузки страницы (keep), выбор человека не меняется
      if (i < 0 && cc.am.indexOf(k) >= 0) { cc.keep[k] = true; cc.am.splice(cc.am.indexOf(k), 1); XT.ccApply(t); return; }
      if (i >= 0) { cc.set.splice(i, 1); } else { cc.set.push(k); delete cc.keep[k]; }
      XT.ccSet(cc.key, cc.set);
      XT.ccApply(t);
      if (cc.onChange) { cc.onChange(cc.set.slice()); }
    },
    isCc: function (t, k) { return !!(t && t.__xtCc && t.__xtCc.set.indexOf(k) >= 0); },
    // свёрнут ли столбец на экране: выбором человека или сам (auto)
    ccOn: function (t, k) { var cc = t && t.__xtCc; return !!(cc && (cc.set.indexOf(k) >= 0 || cc.am.indexOf(k) >= 0)); },
    // классы, подписи и подсказки по набору свёрнутых; first — сразу после сборки (ширина — когда таблица на экране)
    ccApply: function (t, first) {
      if (!XT.ccMark(t)) { return; }
      if (first) { g.requestAnimationFrame(function () { XT.ccFit(t); }); } else { XT.fitSlots(t); }
    },
    ccMark: function (t) {
      var cc = t && t.__xtCc;
      if (!cc) { return false; }
      var tx = cc.text, on = function (k) { return XT.ccOn(t, k); }, fn = function (f, a, b) { return typeof f === "function" ? f(a, b) : ""; };
      Array.prototype.forEach.call(t.querySelectorAll(".gx-xt__hc[data-ck]"), function (b) {
        var k = b.getAttribute("data-ck"), c = on(k), lb = cc.labels[k] || "";
        b.classList.toggle("is-cc", c);
        b.setAttribute("aria-expanded", String(!c));
        b.setAttribute("aria-label", fn(c ? tx.expand : tx.collapse, lb));
        var ic = b.querySelector(".gx-xt__hi");
        if (ic) { b.replaceChild(dnIcon(c ? "cexp" : "ccol", "gx-xt__hi"), ic); }
        var tip = b.querySelector(".gx-xt__tip");
        if (tip) { tip.textContent = fn(c ? tx.tipE : tx.tipC, lb); }
      });
      Array.prototype.forEach.call(t.querySelectorAll(".gx-xt__c[data-ck]"), function (x) {
        var k = x.getAttribute("data-ck"), c = on(k);
        if (x.__xtTitle === undefined) { x.__xtTitle = x.getAttribute("title"); }
        x.classList.toggle("gx-xt__c--cc", c);
        // значение свёрнутой ячейки — data-cct (у линии и плашек своего текста нет), иначе прежняя подсказка или текст
        if (c) { x.setAttribute("title", fn(tx.cell, cc.labels[k] || "", (x.getAttribute("data-cct") || x.__xtTitle || x.textContent || "").trim())); }
        else if (x.__xtTitle === null) { x.removeAttribute("title"); }
        else { x.setAttribute("title", x.__xtTitle); }
      });
      Array.prototype.forEach.call(t.querySelectorAll("[data-ccx]"), function (n) { n.hidden = !on(n.getAttribute("data-ccx")); });
      return true;
    },
    // дорожки сетки верхнего уровня: «32px minmax(0, 1fr) calc(…)» → ["32px", "minmax(0, 1fr)", "calc(…)"]
    tracks: function (s) {
      var out = [], d = 0, cur = "", i, ch;
      for (i = 0; i < s.length; i++) {
        ch = s.charAt(i);
        if (ch === "(") { d += 1; } else if (ch === ")") { d -= 1; }
        if (!d && /\s/.test(ch)) { if (cur) { out.push(cur); cur = ""; } } else { cur += ch; }
      }
      if (cur) { out.push(cur); }
      return out;
    },
    // ширина свёрнутых: --cols шапки и списка — та же сетка, что в стилях (у плотности и ширины окна — своя), где у
    // видимого свёрнутого столбца вместо его ширины 24px; сетка не та, что видно (число дорожек не сходится), — как в
    // стилях. Пересчёт — при смене набора, плотности (applyDensity) и ширины (ResizeObserver таблицы, fitSlots)
    CC_W: "24px",
    // Сами (cc.auto = {guard, min, order}): столбец guard уже min px (узкое окно, крупный текст «A A A») — сворачиваются
    // столбцы order по одному, пока guard не станет шире min или они не кончатся. Свёрнутые человеком и развёрнутые им же
    // нажатием (keep) — не трогаются; свёрнутые сами в его выбор (localStorage) не попадают
    ccFit: function (t) {
      var cc = t && t.__xtCc;
      if (!cc || !t.isConnected) { return false; }
      if (!cc.auto) { return XT.ccFit1(t); }
      // свернуть больше нечего, а guard всё ещё уже min — класс is-cc-tight: сетка списка (её стили) отдаёт ему min за счёт
      // имени (имя сжимается до своего предела)
      // предел — --cc-min таблицы (у плотности свой, стили списка), иначе A.min
      var A = cc.auto, was = cc.am.join(" "), i, k, w, ok, mn = parseFloat(g.getComputedStyle(t).getPropertyValue("--cc-min")) || A.min;
      cc.am = [];
      t.classList.remove("is-cc-tight");
      if (was) { XT.ccMark(t); }
      ok = XT.ccFit1(t);
      for (i = 0; i <= (A.order || []).length; i++) {
        w = XT.ccWidth(t, A.guard);
        if (w === null || w >= mn - 0.5) { break; }
        k = (A.order || [])[i];
        if (k === undefined) { t.classList.add("is-cc-tight"); ok = XT.ccFit1(t); break; }
        if (cc.set.indexOf(k) >= 0 || cc.keep[k] || !cc.labels[k]) { continue; }
        cc.am.push(k);
        XT.ccMark(t);
        ok = XT.ccFit1(t);
      }
      if (was !== cc.am.join(" ") && !cc.am.length) { XT.ccMark(t); }
      return ok;
    },
    // ширина столбца k — по первой видимой ячейке списка (у кнопки шапки поля −6 px с боков, она шире колонки); столбец
    // свёрнут, спрятан или строк нет — null
    ccWidth: function (t, k) {
      if (XT.ccOn(t, k)) { return null; }
      var c = Array.prototype.filter.call(t.querySelectorAll(".gx-xt__list .gx-xt__r > [data-ck='" + k + "']"), function (x) { return x.getClientRects().length; })[0];
      return c ? c.getBoundingClientRect().width : null;
    },
    ccFit1: function (t) {
      var cc = t && t.__xtCc;
      if (!cc || !t.isConnected) { return false; }
      var parts = Array.prototype.filter.call(t.children, function (n) { return n.classList.contains("gx-xt__hw") || n.classList.contains("gx-xt__list"); });
      var had = parts.some(function (p) { return !!p.style.getPropertyValue("--cols"); }), any = cc.set.length + cc.am.length;
      if (!any && !had) { return false; }
      parts.forEach(function (p) { p.style.removeProperty("--cols"); });
      if (!any) { return true; }
      var h = t.querySelector(".gx-xt__hw > .gx-xt__h");
      if (!h) { return false; }
      var tr = XT.tracks(g.getComputedStyle(h).getPropertyValue("--cols").trim());
      var vis = Array.prototype.filter.call(h.children, function (c) { return g.getComputedStyle(c).display !== "none"; });
      if (!tr.length || vis.length !== tr.length) { return false; }
      var hit = false;
      vis.forEach(function (c, i) { if (c.classList.contains("is-cc")) { tr[i] = XT.CC_W; hit = true; } });
      if (!hit) { return true; }
      var v = tr.join(" ");
      parts.forEach(function (p) { p.style.setProperty("--cols", v); });
      return true;
    },
    // «Строка» — две колонки сверху вниз (эталон 23.10, А3; HANDOFF r1 «две колонки»): первая половина групп — в левой
    // колонке, вторая — в правой. Колонка каждой группы задана здесь, поэтому раскрытие растит свою колонку и группы между
    // колонками не перескакивают (REV-ui4 З-2: при column-count перескакивали). В «Крупно» и «Средне» обёртки не видны
    // (display: contents, xt.css) — один столбец в том же порядке
    cols: function (groups) {
      var all = (groups || []).filter(Boolean), h = Math.ceil(all.length / 2), w = mk("div", "gx-xt__cols");
      [all.slice(0, h), all.slice(h)].forEach(function (part) {
        var c = mk("div", "gx-xt__col");
        part.forEach(function (n) { c.appendChild(n); });
        w.appendChild(c);
      });
      return w;
    },
    // Группа: строка row (в ней кнопка .gx-xt__x), подстроки subs; open — раскрыта; onToggle(open) — запомнить
    group: function (row, subs, open, onToggle, attrs) {
      var gr = mk("div", "gx-xt__g" + (open ? " is-open" : ""), attrs || null);
      var id = "gx-xt-s" + (++seq);
      var sub = mk("div", "gx-xt__sub", { id: id });
      (subs || []).forEach(function (n) { if (n) { sub.appendChild(n); } });
      sub.hidden = !open;
      var x = row.querySelector(".gx-xt__x");
      if (x) {
        x.setAttribute("aria-expanded", String(!!open));
        x.setAttribute("aria-controls", id);
        x.addEventListener("click", function (ev) { ev.stopPropagation(); XT.toggle(gr, null, onToggle); });
      }
      // нажатие по свободному месту строки — как по кнопке раскрытия; по кнопкам, полям и ссылкам — их действие
      row.addEventListener("click", function (ev) {
        if (ev.target.closest("button, a, input, select, textarea, label, [role=menu]")) { return; }
        XT.toggle(gr, null, onToggle);
      });
      gr.appendChild(row);
      gr.appendChild(sub);
      return gr;
    },
    toggle: function (gr, open, onToggle) {
      var now = open === null || open === undefined ? !gr.classList.contains("is-open") : !!open;
      gr.classList.toggle("is-open", now);
      var x = gr.querySelector(".gx-xt__x"), sub = gr.querySelector(".gx-xt__sub");
      if (x) { x.setAttribute("aria-expanded", String(now)); }
      if (sub) { sub.hidden = !now; }
      var t = gr.closest(".gx-xt");
      if (t && now) { XT.fitSlots(t); }                           // имена устройств — в ширину колонки имени
      if (onToggle) { onToggle(now); }
      return now;
    },
    isOpen: function (gr) { return !!gr && gr.classList.contains("is-open"); },
    // Стрелки вверх/вниз — по видимым строкам: кнопки раскрытия строк и строки раскрытых подстрок (span.gx-xt__x--sub —
    // не цель: у него нет фокуса); Home / End — первая и последняя
    NAV: "button.gx-xt__x, .gx-xt__r--sub[tabindex]",
    bindKeys: function (list) {
      if (!list || list.__xtKeys) { return; }
      list.__xtKeys = true;
      list.addEventListener("keydown", function (ev) {
        var k = ev.key;
        if (k !== "ArrowDown" && k !== "ArrowUp" && k !== "Home" && k !== "End") { return; }
        var a = document.activeElement;
        if (!a || !a.matches || !a.matches(XT.NAV)) { return; }
        var all = Array.prototype.filter.call(list.querySelectorAll(XT.NAV), function (b) { return b.offsetParent !== null || b === a; });
        var i = all.indexOf(a);
        if (i < 0) { return; }
        var j = k === "ArrowDown" ? i + 1 : k === "ArrowUp" ? i - 1 : k === "Home" ? 0 : all.length - 1;
        if (j < 0 || j >= all.length) { return; }
        ev.preventDefault();
        all[j].focus();
      });
    },
    // Плашки в ячейке (см. выше): у каждой строки — сколько помещается вместе с «+N». Пересчёт — при смене ширины
    // ячейки (окно, плотность, боковая панель), раскрытии строки и после шрифтов.
    // o.tip(names) — подсказка «+N» (names — полные имена скрытых плашек, data-full).
    slots: function (t, o) {
      t.__xtSlots = o || {};
      var first = t.querySelector(".gx-xt__slots") || t.querySelector(".gx-xt__list .gx-xt__g > .gx-xt__r");
      if (!first) { return; }
      first.__xtT = t;
      if (g.ResizeObserver) {
        if (!XT.__ro) {
          XT.__ro = new g.ResizeObserver(function (es) {
            es.forEach(function (e) { var tt = e.target.__xtT; if (!tt || !tt.isConnected) { XT.__ro.unobserve(e.target); } else { XT.fitSlots(tt); } });
          });
        }
        XT.__ro.observe(first);
      }
      if (document.fonts && document.fonts.ready) { document.fonts.ready.then(function () { XT.fitSlots(t); }); }
      g.requestAnimationFrame(function () { XT.fitSlots(t); });
    },
    // Колонка по содержимому (решение координатора 23.10, плотность): --fw на таблице — самая широкая ячейка .gx-xt__fit
    // видимых строк (дети по естественной ширине, зазоры, отступы); сетка может взять её пределом колонки
    fitCol: function (t) {
      var max = 0;
      Array.prototype.forEach.call(t.querySelectorAll(".gx-xt__r > .gx-xt__fit"), function (c) {
        if (!c.getClientRects().length) { return; }
        var cs = g.getComputedStyle(c), gap = parseFloat(cs.columnGap) || 0;
        var w = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
        Array.prototype.filter.call(c.children, function (k) { return k.getClientRects().length; })
          .forEach(function (k, j) { w += Math.max(k.scrollWidth, k.getBoundingClientRect().width) + (j ? gap : 0); });
        max = Math.max(max, w);
      });
      if (max > 0) { t.style.setProperty("--fw", Math.ceil(max + 1) + "px"); }
      return max;
    },
    // Колонка имени по содержимому (владелец 20.10: без «резиновой» пустоты между именем и следующей колонкой): --nm на
    // таблице — самое широкое имя видимых строк (шеврон, значок, имя и подпись рядом; в «Крупно» — и вторая строка);
    // сетка списка берёт его пределом колонки (minmax(…, min(var(--nm), предел))), свободное место уходит вправо.
    // --nm1 — то же, но от имени только первое слово: нижний предел колонки, когда места мало (people.css is-cc-tight)
    fitName: function (t) {
      var max = 0, max1 = 0;
      Array.prototype.forEach.call(t.querySelectorAll(".gx-xt__r .gx-xt__x"), function (x) {
        if (!x.getClientRects().length) { return; }
        var line = x.querySelector(".gx-xt__nl"), sub = x.querySelector(".gx-xt__sub2"), xb = x.getBoundingClientRect();
        if (!line) { return; }
        var kids = Array.prototype.filter.call(line.children, function (k) { return k.getClientRects().length; });
        var gap = parseFloat(g.getComputedStyle(line).columnGap) || 0, w = 0;
        var w1 = 0;
        kids.forEach(function (k, j) {
          var kw = Math.max(k.scrollWidth, k.getBoundingClientRect().width);
          w += kw + (j ? gap : 0);
          w1 += (k.classList.contains("gx-xt__n") ? XT.wordW(k) : kw) + (j ? gap : 0);
        });
        if (sub && sub.getClientRects().length) { w = Math.max(w, sub.scrollWidth); }
        max = Math.max(max, line.getBoundingClientRect().left - xb.left + w);
        max1 = Math.max(max1, line.getBoundingClientRect().left - xb.left + w1);
      });
      if (max > 0) { t.style.setProperty("--nm", Math.ceil(max + 2) + "px"); }
      if (max1 > 0) { t.style.setProperty("--nm1", Math.ceil(max1 + 2) + "px"); }
      return max;
    },
    // ширина первого слова текста элемента его шрифтом (canvas; без браузерной раскладки — 0)
    wordW: function (el) {
      var w = String(el.textContent || "").trim().split(/\s+/)[0] || "", cs = g.getComputedStyle(el);
      if (!w) { return 0; }
      try {
        var c = XT.__cv || (XT.__cv = document.createElement("canvas").getContext("2d"));
        c.font = [cs.fontStyle, cs.fontWeight, cs.fontSize, cs.fontFamily].join(" ");
        return Math.ceil(c.measureText(w).width + (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0));
      } catch (e) { return 0; }
    },
    // Сколько плашек (ширины w, зазор gap) помещается в ячейку ширины W в rows строк вместе с «+N» (ширина mw) на
    // последней строке; строки заполняются по порядку, плашка, не влезшая в строку, переносится целиком
    fitCap: function (w, gap, W, rows, mw) {
      var n = w.length, cap, i, line, x, ok;
      for (cap = n; cap > 0; cap--) {
        line = 0; x = 0; ok = true;
        for (i = 0; i < cap && ok; i++) {
          if (!x) { x = w[i]; if (w[i] > W) { ok = false; } }
          else if (x + gap + w[i] <= W) { x += gap + w[i]; }
          else { line += 1; x = w[i]; if (line >= rows || w[i] > W) { ok = false; } }
        }
        if (ok && cap < n && x + gap + mw > W && (line + 1 >= rows || mw > W)) { ok = false; }
        if (ok) { return cap; }
      }
      return 0;
    },
    fitSlots: function (t) {
      if (!t || !t.isConnected) { return -1; }
      if (t.__xtCc) { XT.ccFit(t); }                              // ширина окна сменила сетку — свёрнутые по новой
      XT.fitName(t);
      XT.fitCol(t);
      var boxes = Array.prototype.filter.call(t.querySelectorAll(".gx-xt__slots"), function (b) { return b.getClientRects().length; });
      if (!boxes.length) { return -1; }                           // колонка скрыта («Строка») — считать нечего
      var tip = t.__xtSlots && t.__xtSlots.tip, most = 0;
      // сначала всё показать и измерить (одно чтение раскладки на все строки), потом скрыть лишнее
      var plan = boxes.map(function (b) {
        var items = Array.prototype.slice.call(b.querySelectorAll("[data-si]")), more = b.querySelector(".gx-xt__more");
        items.forEach(function (c) { c.classList.remove("is-cut", "is-fit"); });
        if (more) { more.textContent = "+" + items.length; more.hidden = false; }
        return { b: b, items: items, more: more };
      });
      plan.forEach(function (p) {
        var cs = g.getComputedStyle(p.b);
        p.W = p.b.clientWidth + 0.5;
        p.gap = parseFloat(cs.columnGap) || 0;
        p.rows = Math.max(1, parseInt(cs.getPropertyValue("--slot-rows"), 10) || 1);
        p.max = Math.max(0, parseInt(cs.getPropertyValue("--slot-max"), 10) || 0);       // 0 — сколько поместится
        p.w = p.items.map(function (c) { return c.getBoundingClientRect().width; });
        p.mw = p.more ? p.more.getBoundingClientRect().width : 0;
      });
      // --pw — самая широкая ячейка плашек по содержимому (с --slot-max — первые N и «+N»): сетка может взять её пределом
      // колонки (без пустоты справа от плашек)
      var nat = 0;
      plan.forEach(function (p) {
        var k = p.max ? Math.min(p.max, p.w.length) : p.w.length, w = 0, i;
        for (i = 0; i < k; i++) { w += p.w[i] + (i ? p.gap : 0); }
        if (k < p.w.length) { w += (k ? p.gap : 0) + p.mw; }
        nat = Math.max(nat, w);
      });
      if (nat > 0) { t.style.setProperty("--pw", Math.ceil(nat + 1) + "px"); }
      // --pw1 — первая плашка: нижний предел колонки, когда места мало (people.css is-cc-tight) — в строке видно хотя бы
      // одно имя протокола
      var one = 0;
      plan.forEach(function (p) { if (p.w.length) { one = Math.max(one, p.w[0]); } });
      if (one > 0) { t.style.setProperty("--pw1", Math.ceil(one + 1) + "px"); }
      plan.forEach(function (p) {
        var n = p.items.length, cap = XT.fitCap(p.w, p.gap, p.W, p.rows, p.mw);
        if (p.max && cap > p.max) { cap = p.max; }
        // не влезает и первая плашка с «+N» — первая всё равно видна, без «+N» (весь список — в подсказке ячейки
        // data-cct); не влезает и она сама — сжимается с многоточием (is-fit), а не пропадает и не режется посреди слова
        var solo = !cap && n > 0;
        if (solo) { cap = 1; }
        var names = [];
        p.items.forEach(function (c, k) { var cut = k >= cap; c.classList.toggle("is-cut", cut); c.classList.toggle("is-fit", solo && !k && p.w[0] > p.W); if (cut && !solo) { names.push(c.getAttribute("data-full") || c.textContent); } });
        most = Math.max(most, cap);
        var m = p.more;
        if (!m) { return; }
        m.hidden = !names.length;
        m.textContent = names.length ? "+" + names.length : "";
        var tt = names.length ? (tip ? tip(names) : names.join(", ")) : null;
        if (tt) { m.setAttribute("title", tt); m.setAttribute("aria-label", tt); } else { m.removeAttribute("title"); m.removeAttribute("aria-label"); }
      });
      t.setAttribute("data-slots", String(most));
      return most;
    }
  };
  g.KaskadXT = XT;
})(window);
