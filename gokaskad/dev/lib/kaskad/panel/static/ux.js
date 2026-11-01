/* Kaskad 3.3.3 — общие компоненты «понятности» (Claude Design UX-333, k3/design/ux-333/export-2026-10-12,
   HANDOFF-ux-333.md): AgentKey, TelegramBot, ProtocolCard (+ журнал справа), ShareMenu, ExitsInput. Один код для
   панели (app.js) и мастера «Быстрый старт» (setup.js) — как give.js. Решения владельца — OWNER-FEEDBACK-3.3.md №35–39.

   ES5, без библиотек и без HTML из текста: данные сервера — только textContent и проверенные атрибуты. Стили —
   ux.css (токены tokens.css панели; у мастера те же имена). Тексты — window.KASKAD_UX_I18N (ux-i18n-ru.js) и
   KASKAD_UX_I18N_EN (ux-i18n-en.js); «a|b|c» — формы числа (одна, несколько, много).

   Данные и действия — у хоста: компоненты зовут o.api.* (Promise; ошибка — Error с .message и .data.reason, как
   ApiError панели) и колбэки. Ничего не хранят между показами, секреты (токен бота, ключ выхода) живут только в поле.

   API:
     KaskadUX.t(lang, key, vars)
     KaskadUX.agentKey(o)      o: {lang, api: {get(), add({pubkey, label}), del(fp)}, open, onOpen(on), fmtTime(epoch), toast(t, s, tone), poll}
     KaskadUX.telegramBot(o)   o: {lang, api: {get(), check({token}), test({token?, chat_id}), proxy?({group, rev})}, open, onOpen(on), onGoExits(), toast}
     KaskadUX.protocolCard(r, o)  r: protoRow (см. protoModel); o: {lang, onToggle(on), onPort(), onRestart() -> Promise, onApply() -> Promise,
                               onLog(), onDelete() -> Promise, onOption(key, on), extraMenu: [{label, on}], extraBody: [Node], extraMore: [Node], tipPorts,
                               moreOpen, onMore(open) — «Подробнее» открыто / его открыли или закрыли; warnLine — строка-предупреждение
                               или их список, по важности: на виду — первой, раньше плашек extraBody}
                               extraBody — короткое под описанием (плашки-предупреждения); extraMore — подробности входа
                               (режим, сертификат, ключи): свёрнутый пункт «Подробнее», закрыт (UI-1 F01: в строке описания
                               ряда карточек — только короткое, иначе одна карточка растягивает строку всего ряда)
                               collapsible — свёрнутая карточка списка компьютера (UI-3): open, onOpen(open) — раскрыта ли
                               (у владельца: переживает пересборку), alertLine — красные строки свёрнутой (например,
                               предупреждения движка AmneziaWG), sessionsLive(fmt) -> Node — живое «· N сессий» для шапки
                               row — строка списка «Протоколов» (круг 1 Т3, вариант Б): все действия кнопками, «⋯» нет;
                               cells: {dev, roles, meta} — узлы хозяина (устройства точками, «для чего», порт для узкого
                               списка), delText — текст подтверждения удаления, extraMenu[i].tail — кнопка после «Журнала»
     KaskadUX.foldGrid(cards, o) сетка свёрнутых карточек (UI-3 круг 2): шапка раскрытой остаётся на своём месте в ряду,
                               тело — полосой на всю ширину сразу после её ряда (тот же порядок и для Tab). o.cols() — число
                               колонок (по умолчанию — из вычисленной сетки)
     KaskadUX.logDrawer(o)     o: {lang, title, cmd, reason, lines: [{t, err}], onClose}
     KaskadUX.shareMenu(o)     o: {lang, name, up, getPage(), getSub(), enable(kind) -> Promise, more(kind), onGive()}
     KaskadUX.exitsInput(o)    o: {lang, api: {preview({key}), addKey({key}), addSub({url}), checkSub({url}) -> {already, text}
                               (по желанию: подписка уже добавлена — «Добавить» закрыта)}, rows (row.busy — текст «Удаляю…»:
                               строка занята, × нет), onAdded(kind, res), onError(e, retry), errFix(e) -> {label, on} | null
                               (по желанию: кнопка у ошибки «Добавить», например «Сменить сеть шлюза на …»; пока она на
                               экране, «Добавить» недоступна до правки текста — тот же ключ не пройдёт; ключ, помеченный
                               предпросмотром с suggest_base, — та же кнопка у предупреждения: errFix({data: {suggest_base}})),
                               hosting: [{id, name, region, url}], guide: {href, text} (только https), listLabel(n)}
                               -> {el, set(rows, others), setHosting(hosting, guide), recheck(), destroy()}; recheck() —
                               причина ошибки снята не в поле (сеть шлюза сменили кнопкой errFix): ошибка и её кнопка
                               уходят, тот же текст разбирается заново, «Добавить» снова доступна
     KaskadUX.noServer(o)      o: {lang, hosting, guide} — «Нет сервера за рубежом?» (мастер этап 3 и ExitsInput панели)
   Чистые функции (тесты гоняют их в node): agentState, pubCheck, agentCommands, clientName, tokenOk, botChip,
   protoModel, exitType, exitRowText. */
(function (g) {
  'use strict';

  var HTTPS = /^https:\/\/[^\s"'<>]+$/;
  // ctrl+щелчок — контекстное меню только на Mac (REV-336-2 П-3); navigator может не быть (тесты в node)
  var MAC = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');

  // ── тексты ───────────────────────────────────────────────────────────────
  function dict(lang) {
    var ru = g.KASKAD_UX_I18N || null, en = g.KASKAD_UX_I18N_EN || null;
    if (lang === 'en' && en) { return en; }
    return ru || en || {};
  }
  function plural(lang, n) {
    n = Math.abs(Number(n) || 0);
    if (lang === 'en') { return n === 1 ? 0 : 2; }
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) { return 0; }
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) { return 1; }
    return 2;
  }
  function t(lang, key, vars) {
    var d = dict(lang), s = Object.prototype.hasOwnProperty.call(d, key) ? d[key] : (dict('ru')[key] !== undefined ? dict('ru')[key] : key);
    s = String(s);
    if (s.indexOf('|') >= 0 && vars && vars.n !== undefined) {
      var forms = s.split('|');
      s = forms[Math.min(plural(lang, vars.n), forms.length - 1)];
    }
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m; });
    }
    return s;
  }

  // ── DOM ──────────────────────────────────────────────────────────────────
  function h(tag, attrs) {
    var n = document.createElement(tag), k, v, i;
    attrs = attrs || {};
    for (k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k)) { continue; }
      v = attrs[k];
      if (v === undefined || v === null || v === false) { continue; }
      if (k === 'class') { n.className = v; }
      else if (k === 'text') { n.textContent = String(v); }
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') { n.addEventListener(k.slice(2), v); }
      else if (k === 'value') { n.value = v; }
      else if (k === 'hidden') { n.hidden = !!v; }
      else { n.setAttribute(k, v === true ? '' : String(v)); }
    }
    for (i = 2; i < arguments.length; i++) { add(n, arguments[i]); }
    return n;
  }
  function add(n, c) {
    var i;
    if (c === null || c === undefined || c === false) { return; }
    if (Object.prototype.toString.call(c) === '[object Array]') { for (i = 0; i < c.length; i++) { add(n, c[i]); } return; }
    n.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }
  function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } return n; }
  function put(n) { clear(n); for (var i = 1; i < arguments.length; i++) { add(n, arguments[i]); } return n; }
  function ic(name, size) {
    var w = h('span', { class: 'ux-ic', 'aria-hidden': 'true' });
    var s = g.KaskadIcons && g.KaskadIcons.svg ? g.KaskadIcons.svg(name, size || 16) : null;
    if (s) { w.appendChild(s); }
    return w;
  }
  function spin(size) { return h('span', { class: 'ux-spin' + (size === 'sm' ? ' ux-spin--sm' : ''), 'aria-hidden': 'true' }); }
  function chip(tone, text) {
    return h('span', { class: 'ux-chip ux-chip--' + tone }, h('span', { class: 'ux-chip__dot', 'aria-hidden': 'true' }), h('span', { text: text }));
  }
  function copyText(text) {
    var done = function () { return true; };
    try {
      if (g.navigator && g.navigator.clipboard && g.isSecureContext !== false) {
        return g.navigator.clipboard.writeText(text).then(done, function () { return legacyCopy(text); });
      }
    } catch (e) { /* старый браузер */ }
    return Promise.resolve(legacyCopy(text));
  }
  function legacyCopy(text) {
    var ta = h('textarea', { class: 'ux-vh', 'aria-hidden': 'true', readonly: true });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  // «Скопировать» → «Скопировано» на 1,6 с
  function copyBtn(lang, getText, o) {
    o = o || {};
    var label = h('span', { text: o.label || t(lang, 'c.copy') });
    var b = h('button', { type: 'button', class: 'ux-copy' + (o.icon ? ' ux-copy--icon' : ''), 'aria-label': o.aria || (o.icon ? t(lang, 'c.copy') : null), title: o.icon ? t(lang, 'c.copy') : null },
      ic('copy', 16), o.icon ? null : label);
    var timer = null;
    b.addEventListener('click', function (ev) {
      ev.stopPropagation();
      copyText(String(getText() || '')).then(function (ok) {
        b.classList.toggle('is-done', !!ok);
        if (!o.icon) { label.textContent = ok ? t(lang, 'c.copied') : t(lang, 'c.copyFailed'); }
        else { b.setAttribute('title', ok ? t(lang, 'c.copied') : t(lang, 'c.copyFailed')); }
        clearTimeout(timer);
        timer = setTimeout(function () { b.classList.remove('is-done'); if (!o.icon) { label.textContent = o.label || t(lang, 'c.copy'); } }, 1600);
      });
    });
    return b;
  }
  function code(lang, caption, text) {
    return h('div', { class: 'ux-code' },
      h('div', { class: 'ux-code__h' }, h('span', { class: 'ux-code__cap', text: caption, title: caption }), copyBtn(lang, function () { return text; })),
      h('pre', { class: 'ux-code__pre', text: text }));
  }
  // Оранжевый треугольник: наведение открывает, нажатие закрепляет, Esc и клик мимо снимают
  // закрыватели всплывающих подсказок и меню: узел ушёл из документа — закрыватель выбрасывается при следующем событии
  var openTips = [], menus = [];
  function reg(list, node, fn) { list.push({ n: node, f: fn }); }
  function runAll(list) {
    for (var i = list.length - 1; i >= 0; i--) {
      if (list[i].n && list[i].n.isConnected === false) { list.splice(i, 1); } else { list[i].f(); }
    }
  }
  function tri(text, cls) {
    var tip = h('span', { class: 'ux-tip', role: 'tooltip', hidden: true, text: text });
    var b = h('button', { type: 'button', class: 'ux-tri' + (cls ? ' ' + cls : ''), 'aria-label': text, 'aria-expanded': 'false' }, ic('triangle-exclamation', 18));
    var w = h('span', { class: 'ux-triw' }, b, tip);
    var pinned = false;
    var show = function (on) { tip.hidden = !on; b.setAttribute('aria-expanded', on ? 'true' : 'false'); };
    w.addEventListener('mouseenter', function () { show(true); });
    w.addEventListener('mouseleave', function () { if (!pinned) { show(false); } });
    b.addEventListener('click', function (ev) { ev.stopPropagation(); pinned = !pinned; show(pinned); });
    reg(openTips, w, function () { pinned = false; show(false); });
    return w;
  }
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { runAll(openTips); } }, true);
    document.addEventListener('click', function () { runAll(openTips); });
  }
  function tabs(items, cur, pick, aria) {
    var box = h('div', { class: 'ux-tabs', role: 'tablist', 'aria-label': aria || null });
    items.forEach(function (it) {
      box.appendChild(h('button', { type: 'button', role: 'tab', class: 'ux-tab' + (it.id === cur ? ' is-on' : ''), 'aria-selected': it.id === cur ? 'true' : 'false',
        'data-akf': it.akf || null, text: it.label, onclick: function () { pick(it.id); } }));
    });
    return box;
  }
  function stepNum(n, done) {
    return h('span', { class: 'ux-num' + (done ? ' is-done' : ''), 'aria-hidden': 'true' }, done ? ic('check', 14) : String(n));
  }
  // Шаг: номер, заголовок (и треугольник), пояснение, тело
  function step(n, done, title, sub, body, triNode) {
    return h('div', { class: 'ux-step' }, stepNum(n, done),
      h('div', { class: 'ux-step__b' },
        h('div', {}, h('div', { class: 'ux-step__t' }, h('span', { text: title }), triNode || null), sub ? h('div', { class: 'ux-step__s' }, sub) : null),
        body));
  }
  // Раскрывающаяся карточка AgentKey / TelegramBot: шапка-кнопка (иконка, заголовок, подзаголовок, чип, шеврон), тело
  function foldCard(o) {
    var chipBox = h('span', { class: 'ux-fold__chip' });
    var body = h('div', { class: 'ux-fold__body', hidden: !o.open });
    var head = h('button', { type: 'button', class: 'ux-fold__head', 'aria-expanded': o.open ? 'true' : 'false' },
      h('span', { class: 'ux-fold__ic' }, ic(o.icon, 20)),
      h('span', { class: 'ux-fold__tt' }, h('span', { class: 'ux-fold__title', text: o.title }), h('span', { class: 'ux-fold__sub', text: o.sub })),
      chipBox, h('span', { class: 'ux-fold__chev' }, ic('chevron-down', 18)));
    var el = h('section', { class: 'ux-fold' + (o.open ? ' is-open' : ''), 'data-ux': o.name }, head, body);
    head.addEventListener('click', function () {
      var on = body.hidden;
      body.hidden = !on;
      el.classList.toggle('is-open', on);
      head.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (o.onToggle) { o.onToggle(on); }
    });
    return { el: el, body: body, head: head, chip: chipBox, isOpen: function () { return !body.hidden; },
      open: function () { if (body.hidden) { head.click(); } }, close: function () { if (!body.hidden) { head.click(); } } };
  }
  function hm(sec) {
    var d = sec ? new Date(Number(sec) * 1000) : new Date();
    return (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
  }
  function errText(lang, e) { return (e && e.message) ? String(e.message) : t(lang, 'c.failed'); }
  // Причина ответа панели. Ответа не было (status 0: обрыв или срок запроса браузера) — причины нет: код транспорта
  // «net» иначе читался бы как причина сервера «net» (Telegram недоступен) — R-333 П4″
  function reason(e) {
    if (e && e.status === 0) { return ''; }
    return e && e.data && e.data.reason ? String(e.data.reason) : (e && e.code ? String(e.code) : '');
  }
  function fmtTime(lang, sec) {
    var d = new Date(Number(sec) * 1000);
    if (isNaN(d.getTime())) { return ''; }
    var hm = (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
    var today = new Date();
    if (d.toDateString() === today.toDateString()) { return t(lang, 'c.todayAt', { t: hm }); }
    return d.toLocaleDateString(lang === 'en' ? 'en-US' : 'ru-RU', { day: 'numeric', month: 'short' }) + ', ' + hm;
  }

  // ═════════════════════════════════════════════════════════════════════════
  // AgentKey — ключ ИИ-агента (MCP), HANDOFF §2
  // ═════════════════════════════════════════════════════════════════════════
  var PUB_RE = /^(ssh-(ed25519|rsa)|ecdsa-sha2-\S+) AAAA\S+/;
  // Что вставили: {state: empty|ok|bad|private, name}. Имя — из комментария после «@» (kaskad-agent@MacBook → MacBook)
  function pubCheck(text) {
    var s = String(text || '').trim();
    if (!s) { return { state: 'empty', name: '' }; }
    if (/PRIVATE KEY/.test(s)) { return { state: 'private', name: '' }; }
    if (!PUB_RE.test(s) || s.split(/\r?\n/).filter(function (x) { return x.trim(); }).length > 1) { return { state: 'bad', name: '' }; }
    var parts = s.split(/\s+/), comment = parts.slice(2).join(' '), at = comment.lastIndexOf('@');
    return { state: 'ok', name: at >= 0 ? comment.slice(at + 1).slice(0, 32) : '' };
  }
  // none — ключей нет; key — ключ есть, агент не подключался; ok — был вызов kaskad-mcp с ключом
  function agentState(doc) {
    var keys = (doc && doc.keys) || [];
    if (!keys.length) { return 'none'; }
    for (var i = 0; i < keys.length; i++) { if (keys[i] && keys[i].seen && keys[i].seen.at) { return 'ok'; } }
    return 'key';
  }
  // есть ли ключ без «подключено» — его ждёт опрос
  function agentPending(doc) {
    var keys = (doc && doc.keys) || [];
    for (var i = 0; i < keys.length; i++) { if (!(keys[i] && keys[i].seen && keys[i].seen.at)) { return true; } }
    return false;
  }
  // clientInfo.name из initialize → имя приложения
  function clientName(client) {
    var c = String(client || '').toLowerCase().split('/')[0];
    if (!c || c === '?' || c === '-') { return ''; }
    if (c.indexOf('cursor') >= 0) { return 'Cursor'; }
    if (c === 'claude-code' || c.indexOf('claude-code') >= 0) { return 'Claude Code'; }
    if (c.indexOf('claude') >= 0) { return 'Claude Desktop'; }
    return String(client).split('/')[0].slice(0, 40);
  }
  var KEYGEN = {
    // ключ есть, а .pub потерян — открытая половина восстанавливается из закрытого (ssh-keygen -y), а не «нет файла»;
    // через временный файл: если ssh-keygen -y не смог (права закрытого ключа), пустой .pub не остаётся и команда
    // кончается ошибкой ssh-keygen, а не пустой строкой (R-333 П5′)
    mac: 'mkdir -p ~/.ssh && { [ -f ~/.ssh/kaskad_agent ] || ssh-keygen -q -t ed25519 -N "" -C "kaskad-agent@$(hostname -s)" -f ~/.ssh/kaskad_agent; } && { [ -s ~/.ssh/kaskad_agent.pub ] || { ssh-keygen -y -f ~/.ssh/kaskad_agent > ~/.ssh/kaskad_agent.pub.new && mv ~/.ssh/kaskad_agent.pub.new ~/.ssh/kaskad_agent.pub; } || { rm -f ~/.ssh/kaskad_agent.pub.new; false; }; } && cat ~/.ssh/kaskad_agent.pub',
    // cmd /c: пустой пароль -N "" одинаково доходит до ssh-keygen из PowerShell 5.1 и 7.3+ (cmd.exe в 7.3+ получает
    // аргументы по старым правилам — $PSNativeCommandArgumentPassing=Windows), %USERPROFILE% раскрывает cmd
    win: 'cmd /c \'(if not exist "%USERPROFILE%\\.ssh" mkdir "%USERPROFILE%\\.ssh") & (if not exist "%USERPROFILE%\\.ssh\\kaskad_agent" ssh-keygen -q -t ed25519 -N "" -C "kaskad-agent@%COMPUTERNAME%" -f "%USERPROFILE%\\.ssh\\kaskad_agent") & (if not exist "%USERPROFILE%\\.ssh\\kaskad_agent.pub" (ssh-keygen -y -f "%USERPROFILE%\\.ssh\\kaskad_agent" > "%USERPROFILE%\\.ssh\\kaskad_agent.pub.new" && move /y "%USERPROFILE%\\.ssh\\kaskad_agent.pub.new" "%USERPROFILE%\\.ssh\\kaskad_agent.pub" >nul || del "%USERPROFILE%\\.ssh\\kaskad_agent.pub.new")) & type "%USERPROFILE%\\.ssh\\kaskad_agent.pub"\''
  };
  var CONF_PATH = {
    desktop: { mac: '~/Library/Application Support/Claude/claude_desktop_config.json', win: '%APPDATA%\\Claude\\claude_desktop_config.json' },
    cursor: { mac: '~/.cursor/mcp.json', win: '%USERPROFILE%\\.cursor\\mcp.json' }
  };
  // Команда шага 3 по приложению и ОС: {caption, text, how}. connect — ответ сервера (mcp.keys.connect)
  function agentCommands(lang, os, app, connect) {
    var c = connect || {}, host = c.host || t(lang, 'ak.hostPh');
    var keyfile = c.keyfile || '~/.ssh/kaskad_agent';
    var args = ['-i', keyfile, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', 'root@' + host, 'kaskad-mcp'];
    if (app === 'code') {
      var cc = c.claude_code || ('claude mcp add kaskad -- ssh ' + args.join(' '));
      // PowerShell съедает голый «--» у claude.ps1 (установка через npm) — тогда -i, -o достаются самому claude.
      // '--' в кавычках доходит как есть и до claude.ps1, и до claude.exe
      if (os === 'win') { cc = cc.replace(/^(claude mcp add \S+) -- /, "$1 '--' "); }
      return { caption: os === 'win' ? 'PowerShell' : t(lang, 'ak.terminal'), text: cc, how: t(lang, 'ak.howCode') };
    }
    var json = c.claude_desktop && c.claude_desktop.mcpServers ? c.claude_desktop : { mcpServers: { kaskad: { command: 'ssh', args: args } } };
    var p = CONF_PATH[app === 'cursor' ? 'cursor' : 'desktop'][os === 'win' ? 'win' : 'mac'];
    return { caption: p, text: JSON.stringify(json, null, 2), how: t(lang, app === 'cursor' ? 'ak.howCursor' : 'ak.howDesktop') };
  }

  // Фокус через перерисовку тела карточки: элемент с тем же data-akf (кнопка «×» ключа, «Отмена», поле ключа…) —
  // иначе тот же путь в дереве с тем же тегом и подписью — иначе шапка карточки; не document.body (R-333 П1‴)
  function akfFind(root, v) {
    var kids = (root && root.children) || [];
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c.getAttribute && c.getAttribute('data-akf') === v) { return c; }
      var r = akfFind(c, v);
      if (r) { return r; }
    }
    return null;
  }
  function akfPath(root, n) {
    var path = [];
    while (n && n !== root) {
      var p = n.parentNode;
      if (!p) { return null; }
      path.unshift(Array.prototype.indexOf.call(p.children || [], n));
      n = p;
    }
    return n === root ? path : null;
  }
  function akfAt(root, path) {
    var n = root;
    for (var i = 0; n && i < path.length; i++) { n = (n.children || [])[path[i]]; }
    return n || null;
  }
  function akfSig(n) { return n ? String(n.tagName) + '|' + ((n.getAttribute && n.getAttribute('aria-label')) || '') + '|' + n.textContent : ''; }
  function focusable(n) { return !!n && !n.disabled && n.focus; }

  function agentKey(o) {
    var lang = o.lang || 'ru', api = o.api;
    var S = { doc: null, err: '', os: /Win/.test((g.navigator && g.navigator.platform) || '') ? 'win' : 'mac', app: 'code',
      text: '', name: '', nameTouched: false, adding: false, addErr: '', more: false, askDel: '', deleting: '', focusTo: null };
    var card = foldCard({ name: 'agent', icon: 'terminal', title: t(lang, 'ak.title'), sub: t(lang, 'ak.sub'), open: !!o.open,
      onToggle: function (on) { if (o.onOpen) { o.onOpen(on); } if (on) { refresh(); } } });
    var timer = null, dead = false;
    // что было в фокусе внутри тела до перерисовки — чтобы вернуть его после (опрос, смена вкладки, вопрос)
    function focusWas() {
      var ae = typeof document !== 'undefined' ? document.activeElement : null;
      if (!ae || ae === card.body || !card.body.contains(ae)) { return null; }
      return { akf: ae.getAttribute ? ae.getAttribute('data-akf') : null, path: akfPath(card.body, ae), sig: akfSig(ae),
        sel: typeof ae.selectionStart === 'number' ? [ae.selectionStart, ae.selectionEnd] : null };
    }
    function focusBack(want, was) {
      var n = null, i;
      for (i = 0; want && i < want.length && !n; i++) { var c = akfFind(card.body, want[i]); if (focusable(c)) { n = c; } }
      if (!n && was) {
        var c2 = was.akf ? akfFind(card.body, was.akf) : null;
        if (!focusable(c2) && was.path) { c2 = akfAt(card.body, was.path); if (akfSig(c2) !== was.sig) { c2 = null; } }
        n = focusable(c2) ? c2 : card.head;
      }
      if (!n && want) { n = card.head; }
      if (!n) { return; }
      n.focus({ preventScroll: true });
      if (was && was.sel && n.setSelectionRange && akfFind(card.body, was.akf || '') === n) {
        try { n.setSelectionRange(was.sel[0], was.sel[1]); } catch (e) { /* поле без выделения */ }
      }
    }
    var fmt = o.fmtTime || function (s) { return fmtTime(lang, s); };

    function state() { return S.doc ? agentState(S.doc) : 'none'; }
    function chipNode() {
      var st = state();
      if (!S.doc && S.err) { return chip('err', t(lang, 'c.noData')); }
      return st === 'ok' ? chip('ok', t(lang, 'ak.chipOk')) : st === 'key' ? chip('wait', t(lang, 'ak.chipKey')) : chip('off', t(lang, 'ak.chipNone'));
    }
    function draw() {
      if (dead) { return; }
      var was = focusWas(), want = S.focusTo;
      S.focusTo = null;
      put(card.chip, chipNode());
      var st = state(), keys = (S.doc && S.doc.keys) || [];
      var shield = h('div', { class: 'ux-note' }, ic('shield-check', 18), h('span', { text: t(lang, 'ak.shield') }));
      // шаг 1
      var s1 = step(1, st !== 'none', t(lang, 'ak.s1'), t(lang, S.os === 'win' ? 'ak.s1win' : 'ak.s1mac'),
        [tabs([{ id: 'mac', label: t(lang, 'ak.osMac'), akf: 'os:mac' }, { id: 'win', label: t(lang, 'ak.osWin'), akf: 'os:win' }], S.os, function (v) { S.os = v; draw(); }, t(lang, 'ak.s1')),
          code(lang, t(lang, S.os === 'win' ? 'ak.capWin' : 'ak.capMac'), KEYGEN[S.os])],
        tri(t(lang, 'ak.s1tip')));
      // шаг 2
      var s2body = [];
      keys.forEach(function (k) {
        // удаление прерывает открытые подключения агента (а если журнал не назвал ключ — всех агентов): сначала вопрос,
        // как у удаления протокола (R-333 З1″). Удаляется один ключ за раз: «Удаляю…» — только в его строке, «×» у
        // остальных выключены до ответа, вопрос к другому ключу не переезжает (R-333 З1‴)
        var fp = String(k.fingerprint || ''), asking = !!fp && S.askDel === fp, busy = asking && S.deleting === fp;
        var name = k.label || t(lang, 'ak.noName');
        var ask = null;
        if (asking) {
          var q = t(lang, 'ak.delQ', { name: name });
          var no = h('button', { type: 'button', class: 'ux-btn ux-btn--ghost', text: t(lang, 'c.cancel'), disabled: busy || undefined, 'data-akf': 'no',
            onclick: function () { cancelDel(fp); } });
          var yes = h('button', { type: 'button', class: 'ux-btn ux-btn--danger', text: t(lang, busy ? 'ak.deleting' : 'c.delete'), disabled: busy || undefined,
            'data-akf': 'yes', onclick: function () { delKey(k); } });
          ask = h('div', { class: 'ux-confirm', role: 'alertdialog', 'aria-label': q, 'aria-busy': busy ? 'true' : null, tabindex: '-1', 'data-akf': 'ask',
            onkeydown: function (e) { if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); e.stopPropagation(); if (!S.deleting) { cancelDel(fp); } } } },
            h('div', { class: 'ux-confirm__t' }, h('b', { text: q }), ' ', t(lang, 'ak.delS')), h('div', { class: 'ux-row' }, no, yes));
        }
        s2body.push(h('div', { class: 'ux-added' + (asking ? ' is-ask' : ''), role: asking ? null : 'status' }, h('span', { class: 'ux-added__ok' }, ic('check', 14)),
          h('div', { class: 'ux-added__b' }, h('div', { class: 'ux-added__t', text: t(lang, 'ak.added', { name: name }) }),
            h('div', { class: 'ux-mono ux-added__fp', text: String(k.type || '') + ' ' + fp, title: String(k.type || '') + ' ' + fp })),
          asking ? null : h('button', { type: 'button', class: 'ux-x', 'aria-label': t(lang, 'ak.del', { name: k.label || fp }), title: t(lang, 'ak.del', { name: k.label || fp }),
            disabled: S.deleting ? true : undefined, 'data-akf': 'x:' + fp,
            onclick: function () { if (S.deleting) { return; } S.askDel = fp; S.focusTo = ['no']; draw(); } }, ic('xmark', 16)),
          ask));
      });
      if (!keys.length || S.more) { s2body.push(pasteForm()); }
      else { s2body.push(h('button', { type: 'button', class: 'ux-link', text: t(lang, 'ak.more'), 'data-akf': 'more', onclick: function () { S.more = true; S.focusTo = ['ta']; draw(); } })); }
      var s2 = step(2, keys.length > 0, t(lang, 'ak.s2'), t(lang, 'ak.s2s'), s2body);
      // шаг 3
      var cmd = agentCommands(lang, S.os, S.app, S.doc && S.doc.connect);
      var s3 = step(3, st === 'ok', t(lang, 'ak.s3'), cmd.how,
        [tabs([{ id: 'code', label: 'Claude Code', akf: 'app:code' }, { id: 'desktop', label: 'Claude Desktop', akf: 'app:desktop' }, { id: 'cursor', label: 'Cursor', akf: 'app:cursor' }], S.app, function (v) { S.app = v; draw(); }, t(lang, 'ak.s3')),
          code(lang, cmd.caption, cmd.text)],
        S.app === 'code' ? null : tri(t(lang, 'ak.s3tip')));
      // шаг 4
      var phrase = t(lang, 'ak.phrase');
      var line = h('div', { class: 'ux-live', role: 'status', 'aria-live': 'polite' });
      if (st === 'none') { put(line, h('span', { class: 'ux-tx3', text: t(lang, 'ak.s4none') })); }
      else if (st === 'key') { put(line, spin('sm'), h('span', { text: t(lang, 'ak.s4wait') })); }
      else {
        var k1 = keys.filter(function (k) { return k.seen && k.seen.at; }).sort(function (a, b) { return b.seen.at - a.seen.at; })[0] || {};
        var bits = [clientName(k1.seen && k1.seen.client), k1.label || '', k1.seen ? fmt(k1.seen.at) : ''].filter(Boolean);
        put(line, h('span', { class: 'ux-okdot' }, ic('check', 12)), h('span', {}, h('b', { text: t(lang, 'ak.s4ok') }), bits.length ? ' · ' + bits.join(' · ') : ''));
      }
      var s4 = step(4, st === 'ok', t(lang, 'ak.s4'),
        h('span', { class: 'ux-ask' }, h('span', { text: t(lang, 'ak.s4s') + ' ' }),
          h('span', { class: 'ux-phrase' }, h('span', { text: '«' + phrase + '»' }), copyBtn(lang, function () { return phrase; }, { icon: true }))),
        [line]);
      var inner = h('div', { class: 'ux-steps' }, shield, s1, s2, s3, s4);
      if (S.err && !S.doc) { inner.insertBefore(h('div', { class: 'ux-bad', text: S.err }), inner.firstChild); }
      put(card.body, inner);
      if (want || was) { focusBack(want, was); }
      schedule();
    }
    function pasteForm() {
      var chk = pubCheck(S.text);
      var ta = h('textarea', { class: 'ux-area ux-area--key' + (chk.state === 'ok' ? ' is-ok' : chk.state === 'bad' ? ' is-warn' : chk.state === 'private' ? ' is-bad' : ''),
        rows: '3', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', 'aria-label': t(lang, 'ak.keyAria'), placeholder: t(lang, 'ak.keyPh'), 'data-akf': 'ta' });
      ta.value = S.text;
      var msg = h('div', { class: 'ux-msg', 'aria-live': 'polite' });
      var nameIn = h('input', { class: 'ux-in', type: 'text', maxlength: '32', placeholder: t(lang, 'ak.namePh'), autocomplete: 'off', 'data-akf': 'name' });
      nameIn.value = S.name;
      var btn = h('button', { type: 'button', class: 'ux-btn ux-btn--primary ux-btn--lg', text: t(lang, 'c.add'), 'data-akf': 'add' });
      var sync = function () {
        var c = pubCheck(S.text);
        ta.className = 'ux-area ux-area--key' + (c.state === 'ok' ? ' is-ok' : c.state === 'bad' ? ' is-warn' : c.state === 'private' ? ' is-bad' : '');
        msg.className = 'ux-msg' + (c.state === 'private' || S.addErr ? ' is-bad' : c.state === 'bad' ? ' is-warn' : '');
        msg.textContent = S.addErr || (c.state === 'bad' ? t(lang, 'ak.notKey') : c.state === 'private' ? t(lang, 'ak.private') : '');
        if (c.state === 'ok' && !S.nameTouched && c.name) { S.name = c.name; nameIn.value = c.name; }
        btn.disabled = c.state !== 'ok' || S.adding;
        btn.textContent = S.adding ? t(lang, 'c.adding') : t(lang, 'c.add');
      };
      ta.addEventListener('input', function () { S.text = ta.value; S.addErr = ''; sync(); });
      nameIn.addEventListener('input', function () { S.name = nameIn.value; S.nameTouched = true; });
      btn.addEventListener('click', function () { addKey(sync); });
      sync();
      return h('div', { class: 'ux-paste' }, ta, msg,
        h('div', { class: 'ux-row ux-row--end' }, h('label', { class: 'ux-lab' }, h('span', { text: t(lang, 'ak.name') }), nameIn), btn));
    }
    function addKey(sync) {
      if (S.adding || pubCheck(S.text).state !== 'ok') { return; }
      S.adding = true; sync();
      api.add({ pubkey: S.text.trim(), label: S.name.trim() || undefined }).then(function () {
        S.adding = false; S.text = ''; S.name = ''; S.nameTouched = false; S.more = false; S.addErr = '';
        return refresh();
      }, function (e) {
        S.adding = false;
        var r = reason(e);
        S.addErr = r === 'private_key' ? t(lang, 'ak.private') : errText(lang, e);
        draw();
      });
    }
    function cancelDel(fp) { S.askDel = ''; S.focusTo = ['x:' + fp]; draw(); }
    function delKey(k) {
      // удаление обрывает открытые подключения ключа; если журнал не назвал ключ подключения — все подключения агентов
      if (S.deleting) { return; }
      var fp = String(k.fingerprint || '');
      var fps = ((S.doc && S.doc.keys) || []).map(function (x) { return String(x.fingerprint || ''); });
      var at = fps.indexOf(fp);
      // фокус после удаления: «×» соседнего ключа (следующего, иначе предыдущего), иначе добавление ключа
      var next = fps.slice(at + 1).concat(fps.slice(0, Math.max(at, 0)).reverse()).map(function (x) { return 'x:' + x; }).concat(['more', 'ta']);
      S.deleting = fp; S.askDel = fp; S.focusTo = ['ask']; draw();
      var done = function () { S.deleting = ''; S.askDel = ''; };
      api.del(k.fingerprint).then(function (r) {
        done();
        var why = r && r.unsure ? t(lang, 'ak.cutAll') : (r && r.ended ? t(lang, 'ak.cut') : '');
        if (o.toast) { o.toast(t(lang, 'ak.removed', { name: k.label || '' }), why, r && r.unsure ? 'warning' : 'success'); }
        S.focusTo = next;
        return refresh(true);
      },
        function (e) { done(); S.focusTo = ['x:' + fp].concat(next); if (o.toast) { o.toast(errText(lang, e), '', 'danger'); } return refresh(true); });
    }
    // опрос раз в 5 с перерисовывает карточку, только если ключи или «подключено» сменились: иначе поле ввода
    // второго ключа теряло бы фокус, а «Скопировано» — отметку
    // force — перерисовать и без перемен в ответе (итог удаления: вопрос закрыт, фокус на месте)
    function refresh(force) {
      return api.get().then(function (d) {
        var was = JSON.stringify(S.doc), now = d || { keys: [] };
        S.doc = now;
        if (!force && was === JSON.stringify(now) && !S.err) { schedule(); return; }
        S.err = ''; draw();
      }, function (e) { S.err = errText(lang, e); draw(); });
    }
    // есть ключ, с которым агент ещё не подключался («ждём агента», в том числе второй ключ после подключённого
    // первого) → опрос раз в o.poll мс, пока карточка в документе, открыта и вкладка видна
    function schedule() {
      clearTimeout(timer);
      if (dead || !agentPending(S.doc) || !card.isOpen()) { return; }
      timer = setTimeout(function () {
        if (dead || !card.el.isConnected) { return; }
        // скрытая вкладка или пауза панели на замер из браузера (o.paused) — без запроса, спросим позже
        if ((typeof document !== 'undefined' && document.hidden) || (o.paused && o.paused())) { schedule(); return; }
        refresh();
      }, o.poll || 5000);
    }
    draw();
    refresh();
    return { el: card.el, refresh: refresh, open: card.open, close: card.close, destroy: function () { dead = true; clearTimeout(timer); } };
  }

  // ═════════════════════════════════════════════════════════════════════════
  // TelegramBot — HANDOFF §3
  // ═════════════════════════════════════════════════════════════════════════
  var TOKEN_RE = /^\d{6,}:[\w-]{30,}$/;
  function tokenOk(s) { return TOKEN_RE.test(String(s || '').trim()); }
  // Статус-чип — сверху вниз: тест прошёл / бот работает · нужен выход · нет связи с Telegram · токен не подошёл ·
  // токен проверен · не настроен. «Токен не подошёл» — только когда Telegram его отверг (bad_token, bad_format): сбой
  // сети, выхода или ответ Telegram 429/5xx — «нет связи», токен тут ни при чём
  function botChip(st, local) {
    st = st || {}; local = local || {};
    if (local.tok === 'fail') { local = { tok: '', send: local.send }; }     // сбой панели: о боте ничего нового
    var failed = local.send === 'nostart' || local.send === 'err';         // тест не прошёл — не «работает», хоть служба и запущена
    if (local.send === 'ok' || (st.token && st.chat && st.running && !st.need_exit && !local.tok && !failed)) { return 'ok'; }
    if (st.need_exit || local.tok === 'noexit') { return 'warn'; }
    if (local.tok === 'net') { return 'net'; }
    if (local.tok === 'bad') { return 'err'; }
    if (local.tok === 'ok' || (st.token && !local.tok)) { return 'wait'; }
    return 'off';
  }
  var BOT_CHIP = { ok: 'tb.chipOk', warn: 'tb.chipExit', net: 'tb.chipNet', err: 'tb.chipBad', wait: 'tb.chipChecked', off: 'tb.chipNone' };
  var BOT_TONE = { ok: 'ok', warn: 'warn', net: 'warn', err: 'err', wait: 'wait', off: 'off' };
  // Причина ответа сервера на проверку токена → состояние поля: bad — Telegram отверг токен; noexit — выхода нет, а
  // Telegram напрямую недоступен; net — выход есть, но не везёт, или Telegram ответил 429/5xx; fail — сбой самой панели
  // (таймаут, 500, нет причины в ответе): это не «нет связи с Telegram», и чип статуса бота не меняется
  function tokState(reason) {
    return reason === 'bad_token' || reason === 'bad_format' ? 'bad' : reason === 'net' ? 'noexit'
      : reason === 'net_proxy' || reason === 'telegram' ? 'net' : 'fail';
  }

  function telegramBot(o) {
    var lang = o.lang || 'ru', api = o.api;
    var S = { st: null, err: '', token: '', tok: '', tokWhy: '', user: '', tokErr: '', chat: '', chatTouched: false, send: '', sendErr: '', px: '', pxErr: '' };
    var card = foldCard({ name: 'bot', icon: 'paper-plane', title: t(lang, 'tb.title'), sub: t(lang, 'tb.sub'), open: !!o.open,
      onToggle: function (on) { if (o.onOpen) { o.onOpen(on); } } });
    var debounce = null, seq = 0, dead = false;
    var tokIn = h('input', { class: 'ux-in ux-mono ux-in--token', type: 'text', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'off', 'aria-label': t(lang, 'tb.s2') });
    // «Режим стрима» панели: токен — точками (type=password) и глаз; вне панели (мастер) stream.js нет
    var KSt = g.KaskadStream && g.KaskadStream.on ? g.KaskadStream : null;
    var tokEye = KSt ? KSt.field(tokIn, { what: KSt.t('stm.w.token') }) : null;
    var idIn = h('input', { class: 'ux-in ux-mono ux-in--id', type: 'text', inputmode: 'numeric', autocomplete: 'off', maxlength: '16', 'aria-label': t(lang, 'tb.s3') });
    var tokLine = h('div', { class: 'ux-live', role: 'status', 'aria-live': 'polite' });
    var sendLine = h('div', { class: 'ux-live', role: 'status', 'aria-live': 'polite' });
    var sendBtn = h('button', { type: 'button', class: 'ux-btn ux-btn--primary', text: t(lang, 'tb.send') });
    var banner = h('div');
    // «Как бот ходит в Telegram» (SPEC-tg-proxy Р5): группа выходов или «Напрямую»; под выбором — как сейчас (ядро,
    // st.path.text). Только у настроенного бота и с ответом ядра о группах (st.choice)
    var pxSel = h('select', { class: 'gx-input', 'aria-label': t(lang, 'tb.pxT') });
    var pxLine = h('div', { class: 'ux-live', role: 'status', 'aria-live': 'polite' });
    var pxBox = h('div', { class: 'ux-step', hidden: true }, h('span', { class: 'ux-num', 'aria-hidden': 'true' }, ic('route', 14)),
      h('div', { class: 'ux-step__b' },
        h('div', {}, h('div', { class: 'ux-step__t' }, h('span', { text: t(lang, 'tb.pxT') })), h('div', { class: 'ux-step__s', text: t(lang, 'tb.pxS') })),
        pxSel, pxLine));
    var pxOpts = '';
    pxSel.addEventListener('change', function () {
      var v = pxSel.value, ch = (S.st && S.st.choice) || {};
      if (!api.proxy || v === ch.group || S.px === 'saving') { return; }
      S.px = 'saving'; S.pxErr = ''; paint();
      api.proxy({ group: v, rev: ch.rev }).then(function () {
        if (dead) { return; }
        S.px = '';
        if (o.toast) { o.toast(t(lang, v === 'direct' ? 'tb.pxSavedDirect' : 'tb.pxSaved'), '', 'success'); }
        return load();
      }, function (e) {
        if (dead) { return; }
        S.px = 'err'; S.pxErr = errText(lang, e);
        return load();
      });
    });

    tokIn.addEventListener('input', function () {
      S.token = tokIn.value.trim(); S.tok = ''; S.tokErr = ''; S.tokWhy = ''; S.send = '';
      seq++;                                                    // ответ на прежний текст поля больше не нужен
      clearTimeout(debounce);
      if (!S.token) { paint(); return; }
      if (tokenOk(S.token)) { S.tok = 'checking'; paint(); debounce = setTimeout(checkToken, 250); return; }
      paint();
      if (S.token.length > 12) { debounce = setTimeout(function () { if (!tokenOk(S.token) && S.token.length > 12) { S.tok = 'bad'; S.tokErr = ''; paint(); } }, 700); }
    });
    idIn.addEventListener('input', function () {
      // цифры; минус — только первым (ID группы, его мог записать мастер: -100…)
      var raw = idIn.value.trim(), v = (raw.charAt(0) === '-' ? '-' : '') + raw.replace(/\D+/g, '').slice(0, 15);
      if (v !== idIn.value) { idIn.value = v; }
      S.chat = v; S.chatTouched = true; S.send = ''; paint();
    });
    sendBtn.addEventListener('click', sendTest);

    function checkToken() {
      var my = ++seq, tok = S.token;
      api.check({ token: tok }).then(function (r) {
        if (my !== seq || dead) { return; }
        S.tok = 'ok'; S.user = (r && r.username) || ''; paint();
      }, function (e) {
        if (my !== seq || dead) { return; }
        var r = reason(e);
        S.tok = tokState(r); S.tokWhy = r;
        // net_proxy — выход задан, но не везёт; telegram — 429/5xx: слова сервера с причиной и совет по делу;
        // fail — слова ошибки панели под своей строкой
        S.tokErr = S.tok === 'net' || S.tok === 'fail' ? errText(lang, e) : '';
        // fail без ответа панели (обрыв или срок запроса браузера) — не «сбой на сервере»: связь браузера с панелью
        S.tokNoAns = S.tok === 'fail' && !!e && e.status === 0;
        paint();
      });
    }
    function idOk() { return /^-?\d{5,15}$/.test(S.chat); }
    function haveToken() { return S.tok === 'ok' || (!S.token && S.st && S.st.token); }
    function sendTest() {
      if (!haveToken() || !idOk() || S.send === 'sending') { return; }
      S.send = 'sending'; S.sendErr = ''; paint();
      var body = { chat_id: S.chat };
      if (S.token && S.tok === 'ok') { body.token = S.token; }
      api.test(body).then(function (r) {
        if (dead) { return; }
        if (r && r.username) { S.user = r.username; }
        S.send = r && r.sent ? 'ok' : (r && r.sent_why === 'nostart' ? 'nostart' : 'err');
        // не дошло из-за связи — не «проверьте ID»
        // не дошло из-за связи или Telegram занят (429/5xx) — не «проверьте ID»
        S.sendErr = S.send === 'err' ? t(lang, r && r.sent_why === 'net' ? 'tb.sendNet' : r && r.sent_why === 'busy' ? 'tb.sendBusy' : 'tb.sendFail') : '';
        S.token = ''; tokIn.value = ''; S.tok = '';
        return load();
      }, function (e) {
        if (dead) { return; }
        var r = reason(e);
        if (r === 'net') { S.tok = 'noexit'; S.send = ''; }
        else if (r === 'bad_token' || r === 'bad_format') { S.tok = 'bad'; S.tokErr = ''; S.send = ''; }
        else { S.send = 'err'; S.sendErr = errText(lang, e); }
        paint();
      });
    }
    function paint() {
      if (dead) { return; }
      var st = S.st || {}, local = { tok: S.tok === 'checking' ? '' : S.tok, send: S.send };
      var c = botChip(st, local);
      put(card.chip, !S.st && S.err ? chip('err', t(lang, 'c.noData')) : chip(BOT_TONE[c], t(lang, BOT_CHIP[c])));
      // плашка «нужен выход»; «из РФ блокируется» — только когда сервер в РФ (st.ru), иначе нейтрально
      if (st.need_exit || S.tok === 'noexit') {
        // выбрано «Напрямую», а напрямую Telegram закрыт: совет — группа выходов ниже, а не «добавьте выход» (SPEC-tg-proxy)
        var chosen = !!(st.path && st.path.why === 'chosen' && S.tok !== 'noexit');
        put(banner, h('div', { class: 'ux-warnbar' }, ic('triangle-exclamation', 20),
          h('div', { class: 'ux-warnbar__t' }, h('b', { text: t(lang, chosen ? 'tb.exitChosenT' : st.ru === true ? 'tb.exitT' : 'tb.exitTany') }), ' ', t(lang, chosen ? 'tb.exitChosenS' : 'tb.exitS')),
          o.onGoExits && !chosen ? h('button', { type: 'button', class: 'ux-btn', text: t(lang, 'tb.toExits'), onclick: function () { o.onGoExits(); } }) : null));
      } else { clear(banner); }
      // строка токена
      tokIn.placeholder = st.token && !S.token ? t(lang, 'tb.tokenSaved', { name: st.username ? '@' + st.username : '' }).trim() : '123456789:AA…';
      // режим стрима: точки поля-секрета (класс ks-sectext от KaskadStream.field) перерисовка не снимает (REV-stream З-3)
      tokIn.className = 'ux-in ux-mono ux-in--token' + (S.tok === 'ok' ? ' is-ok' : S.tok === 'bad' ? ' is-bad' : S.tok === 'noexit' || S.tok === 'net' || S.tok === 'fail' || (S.token && !tokenOk(S.token) && S.token.length > 12) ? ' is-warn' : '') +
        (tokIn.classList.contains('ks-sectext') ? ' ks-sectext' : '');
      if (S.tok === 'checking') { put(tokLine, spin('sm'), h('span', { text: t(lang, 'tb.checking') })); }
      else if (S.tok === 'ok') { put(tokLine, h('span', { class: 'ux-oktx' }, ic('check', 14)), h('span', {}, t(lang, 'tb.found1'), h('b', { text: '@' + S.user }), t(lang, 'tb.found2'))); }
      else if (S.tok === 'bad') { put(tokLine, h('span', { class: 'ux-errtx', text: S.tokErr || t(lang, 'tb.bad') })); }
      else if (S.tok === 'noexit') { put(tokLine, h('span', { class: 'ux-warntx', text: t(lang, 'tb.noexit') })); }
      else if (S.tok === 'net') {
        var proxyWhy = S.tokWhy === 'net_proxy';
        put(tokLine, h('div', { class: 'ux-netwhy' }, h('span', { class: 'ux-warntx', text: S.tokErr || t(lang, 'tb.netAny') }),
          h('span', { class: 'ux-tx2', text: t(lang, proxyWhy ? 'tb.netProxyTip' : 'tb.netTip') }),
          h('span', { class: 'ux-row' },
            h('button', { type: 'button', class: 'ux-link', text: t(lang, 'tb.again'), onclick: function () { if (tokenOk(S.token)) { S.tok = 'checking'; paint(); checkToken(); } } }),
            proxyWhy && o.onGoExits ? h('button', { type: 'button', class: 'ux-link', text: t(lang, 'tb.toExits'), onclick: function () { o.onGoExits(); } }) : null)));
      }
      else if (S.tok === 'fail') {
        put(tokLine, h('div', { class: 'ux-netwhy' }, h('span', { class: 'ux-warntx', text: t(lang, S.tokNoAns ? 'tb.failLink' : 'tb.failAny') }),
          S.tokErr && S.tokErr !== t(lang, 'c.failed') ? h('span', { class: 'ux-tx2', text: S.tokErr }) : null,
          h('button', { type: 'button', class: 'ux-link', text: t(lang, 'tb.again'), onclick: function () { if (tokenOk(S.token)) { S.tok = 'checking'; paint(); checkToken(); } } })));
      }
      else if (!S.token && st.token) { put(tokLine, h('span', { class: 'ux-tx2', text: st.username ? t(lang, 'tb.savedAs', { name: '@' + st.username }) : t(lang, 'tb.saved') })); }
      else { clear(tokLine); }
      // ID
      if (!S.chatTouched && st.chat_id && !S.chat && /^-?\d{1,20}$/.test(String(st.chat_id))) { S.chat = String(st.chat_id); idIn.value = S.chat; }
      idIn.placeholder = t(lang, 'tb.idPh');
      // тест
      sendBtn.disabled = !haveToken() || !idOk() || S.send === 'sending';
      if (S.send === 'sending') { put(sendLine, spin('sm'), h('span', { text: t(lang, 'tb.sending') })); }
      else if (S.send === 'ok') { put(sendLine, h('span', { class: 'ux-okdot' }, ic('check', 12)), h('span', {}, h('b', { text: t(lang, 'tb.sentT') }), ' · ' + t(lang, 'tb.sentS'))); }
      else if (S.send === 'nostart') { put(sendLine, h('span', { class: 'ux-warntx', text: t(lang, 'tb.nostart', { name: '@' + (S.user || st.username || 'bot') }) })); }
      else if (S.send === 'err') { put(sendLine, h('span', { class: 'ux-errtx', text: S.sendErr })); }
      else if (!haveToken()) { put(sendLine, h('span', { class: 'ux-tx3', text: t(lang, 'tb.needToken') })); }
      else if (!idOk()) { put(sendLine, h('span', { class: 'ux-tx3', text: t(lang, 'tb.needId') })); }
      else if (st.token && st.chat && st.running && !S.token) { put(sendLine, h('span', { class: 'ux-tx2', text: t(lang, 'tb.running') })); }
      else { clear(sendLine); }
      steps.n1.className = stepNum(1, haveToken()).className; put(steps.n1, haveToken() ? ic('check', 14) : '1');
      steps.n2.className = stepNum(2, haveToken()).className; put(steps.n2, haveToken() ? ic('check', 14) : '2');
      steps.n3.className = stepNum(3, idOk()).className; put(steps.n3, idOk() ? ic('check', 14) : '3');
      var done4 = c === 'ok';
      steps.n4.className = stepNum(4, done4).className; put(steps.n4, done4 ? ic('check', 14) : '4');
      paintPx(st);
    }
    var PX_TONE = { group: 'ok', fallback: 'warn', direct: 'ok', none: 'err', off: 'off' };
    function paintPx(st) {
      var ch = st.choice;
      pxBox.hidden = !(st.token && ch && ch.groups && api.proxy);
      if (pxBox.hidden) { return; }
      var opts = ch.groups.map(function (g) { return [g.id, t(lang, 'tb.pxGroup', { name: g.label || g.id, n: g.keys || 0 })]; });
      opts.push(['direct', t(lang, 'tb.pxDirect')]);
      var sig = JSON.stringify(opts);
      if (sig !== pxOpts) {                                    // свои варианты — заново, только если они другие
        pxOpts = sig;
        put(pxSel, opts.map(function (x) { return h('option', { value: x[0], text: x[1] }); }));
      }
      pxSel.value = ch.group;
      pxSel.disabled = S.px === 'saving';
      var p = st.path, tone = p ? PX_TONE[p.state] || 'off' : 'off';
      if (p && p.state === 'direct' && p.why !== 'chosen') { tone = p.why === 'pending' ? 'wait' : 'warn'; }
      if (S.px === 'saving') { put(pxLine, spin('sm'), h('span', { text: t(lang, 'tb.pxSaving') })); }
      else if (S.px === 'err') { put(pxLine, h('span', { class: 'ux-errtx', text: S.pxErr })); }
      else if (p && p.text) { put(pxLine, h('span', { class: 'ux-tx2', text: t(lang, 'tb.pxNow') + ' ' }), chip(tone, p.text)); }
      else { clear(pxLine); }
    }
    var steps = {};
    function build() {
      var s1 = step(1, false, t(lang, 'tb.s1'), t(lang, 'tb.s1s'), [h('div', { class: 'ux-row' },
        h('a', { class: 'ux-btn', href: 'https://t.me/BotFather', target: '_blank', rel: 'noopener noreferrer' }, h('span', { text: t(lang, 'tb.openBf') }), ic('arrow-right-from-square', 14)),
        h('span', { class: 'ux-cmdchip ux-mono' }, h('span', { text: '/newbot' }), copyBtn(lang, function () { return '/newbot'; }, { icon: true })))]);
      var s2 = step(2, false, t(lang, 'tb.s2'), h('span', {}, t(lang, 'tb.s2s'), ' ', h('code', { text: '123456789:AAH…' })), [tokEye ? h('div', { class: 'ux-row' }, tokIn, tokEye) : tokIn, tokLine]);
      var s3 = step(3, false, t(lang, 'tb.s3'), t(lang, 'tb.s3s'), [idIn, h('div', { class: 'ux-tx2 ux-small' }, h('b', { text: t(lang, 'tb.whereT') }), ' ', t(lang, 'tb.where1'),
        h('a', { href: 'https://t.me/userinfobot', target: '_blank', rel: 'noopener noreferrer', text: '@userinfobot' }), t(lang, 'tb.where2'))]);
      var s4 = step(4, false, t(lang, 'tb.s4'), t(lang, 'tb.s4s'), [h('div', { class: 'ux-row' }, sendBtn), sendLine]);
      [s1, s2, s3, s4].forEach(function (s, i) { steps['n' + (i + 1)] = s.firstChild; });
      put(card.body, h('div', { class: 'ux-steps' }, banner, s1, s2, s3, s4, pxBox));
    }
    function load() {
      return api.get().then(function (d) { S.st = d || {}; S.err = ''; paint(); }, function (e) { S.err = errText(lang, e); paint(); });
    }
    build();
    paint();
    load();
    return { el: card.el, refresh: load, open: card.open, close: card.close, destroy: function () { dead = true; clearTimeout(debounce); tokIn.value = ''; S.token = ''; } };
  }

  // ═════════════════════════════════════════════════════════════════════════
  // ProtocolCard — HANDOFF §4
  // ═════════════════════════════════════════════════════════════════════════
  // r: {kind, title, engine, desc, enabled, running (true|false|null), devices, sessions, ports: ["51820/udp"],
  //     fail: {why: busy|closed|other, port, proto, line, human} | null, fixedPort: bool, why, chip: {tone, text} (свой
  //     чип хоста: «ждёт шлюза»), switches: [{key, title, sub, on, warnOn}] (L2TP без шифрования: warnOn — треугольник)}
  // -> {state, chip: {tone, text}, desc, ports: {text, tip}, reason: {tone, l1, l2} | null, buttons: [id], menu: [id]}
  var DESC_SHORT = 100;
  // [на виду, в «Подробнее»]: короткое (≤ DESC_SHORT — не больше трёх строк в самой узкой карточке ряда) — целиком;
  // длинное — до конца первой фразы или её части (. ! ? … ;), если она не пустяк и сама короткая; «;» на виду — точкой,
  // остальное — с большой буквы. Строка описания общая на ряд карточек (subgrid): длинная растянула бы весь ряд (UI-2)
  function descParts(d) {
    var x = String(d || '');
    if (x.length <= DESC_SHORT) { return [x, '']; }
    var re = /[.!?…;]\s/g, r, i = -1;
    while ((r = re.exec(x))) { if (r.index >= 20) { i = r.index; break; } }
    if (i < 0 || i >= DESC_SHORT || i >= x.length - 2) { return [x, '']; }
    var rest = x.slice(i + 2).trim();
    return [x.slice(0, i) + (x.charAt(i) === ';' ? '.' : x.charAt(i)), rest.charAt(0).toUpperCase() + rest.slice(1)];
  }
  // последняя фраза описания (предупреждение в конце: «Блокировки UDP … не проверены»), одна фраза — ''
  function descLast(d) {
    var x = String(d || '').trim(), re = /[.!?…]\s+/g, r, at = -1;
    while ((r = re.exec(x))) { at = r.index + r[0].length; }
    return at > 0 ? x.slice(at) : '';
  }
  var LOG_MAX = 160;
  function logLine(s) {
    var x = String(s || '').replace(/(\\x[0-9a-fA-F]{2})+/g, '…').replace(/[\u0000-\u001f\u007f-\u009f]+/g, ' ').replace(/\s+/g, ' ').trim();
    return x.length > LOG_MAX ? x.slice(0, LOG_MAX - 1).replace(/\s+\S*$/, '') + '…' : x;
  }
  function protoModel(r, lang) {
    var fail = r.fail || null, st;
    if (!r.enabled) { st = 'off'; }
    else if (r.running === false) { st = fail && fail.why === 'busy' ? 'busy' : 'other'; }
    // служба идёт, а сертификат Hysteria2 негоден (нет файла, битый, чужой ключ, срок; стенд 3.3.7.2 Н-8.2) — клиенты не
    // подключатся: «не работает» с причиной словами, как у остановленной
    else if (r.running === true) { st = fail && fail.why === 'closed' ? 'closed' : fail && fail.why === 'cert' ? 'other' : 'run'; }
    else { st = 'unknown'; }
    var CH = { run: ['ok', 'pc.run'], off: ['off', 'pc.off'], busy: ['err', 'pc.down'], other: ['err', 'pc.down'], closed: ['warn', 'pc.noconn'], unknown: ['wait', 'pc.unknown'] };
    var ports = (r.ports || []).filter(Boolean);
    var port1 = ports.length ? String(ports[0]) : '';
    var pnum = fail && fail.port ? String(fail.port) : port1.split('/')[0];
    var pproto = fail && fail.proto ? String(fail.proto) : (port1.split('/')[1] || 'udp');
    var rs = null;
    if (st === 'busy') { rs = { tone: 'err', l1: t(lang, 'pc.busy', { port: pnum }), l2: '' }; }
    // несколько портов (L2TP/IPsec: 1701, 500, 4500) — открыть нужно все
    else if (st === 'closed') { rs = { tone: 'warn', l1: t(lang, 'pc.closed', { port: ports.length > 1 ? ports.join(', ') : pnum + '/' + pproto }), l2: t(lang, 'pc.closed2') }; }
    else if (st === 'other') {
      var line = fail && fail.line ? String(fail.line) : '', human = fail && fail.human ? String(fail.human) : '';
      // КТ3-10: the raw line of the journal is never the main text — words first, the line only as «Из журнала: …»
      // UI-1 F02: строка журнала без управляющих символов и экранированных байтов, не длиннее LOG_MAX; целиком — в подсказке
      var raw = line;
      line = logLine(line);
      rs = { tone: 'err', l1: human || (r.why ? String(r.why) : t(lang, 'pc.downText')), l2: line ? t(lang, 'pc.fromLog', { line: line }) : '', log: !!line, full: raw };
    }
    // перезапустили, а служба снова стоит — строкой в той же плашке (хост помнит время: r.againAt, эпоха)
    if (rs && rs.tone === 'err' && r.againAt) { rs.l3 = t(lang, 'pc.again', { t: hm(r.againAt) }); }
    var portBtn = !r.fixedPort;
    var B = { run: ['toggle'].concat(portBtn ? ['port'] : []), off: ['toggle'].concat(portBtn ? ['port'] : []),
      busy: (portBtn ? ['port'] : ['restart']).concat(['log']), closed: ['toggle'].concat(portBtn ? ['port'] : []),
      other: ['restart', 'log'], unknown: ['toggle'].concat(portBtn ? ['port'] : []) };
    // REV-3373 З-3: негодный сертификат Hysteria2 — «Применить настройки» (копия пары заново, xray перезапускается);
    // простой перезапуск файл не перепишет
    if (st === 'other' && fail && fail.why === 'cert') { B.other = ['apply', 'log']; }
    var M = { run: ['delete'], off: ['delete'], unknown: ['delete'], busy: ['toggle', 'delete'], closed: ['log', 'delete'],
      other: ['toggle'].concat(portBtn ? ['port'] : []).concat(['delete']) };
    var menu = M[st].slice();
    // переключатели протокола (L2TP без шифрования, UDP у SOCKS5) — первыми пунктами меню «⋯»
    (r.switches || []).forEach(function (x, i) { menu.splice(i, 0, 'sw:' + x.key); });
    return { state: st, chip: r.chip && st !== 'busy' && st !== 'other' && st !== 'closed' ? r.chip : { tone: CH[st][0], text: t(lang, CH[st][1]) },
      desc: r.desc ? String(r.desc) : '',
      ports: ports.length > 1 ? { text: t(lang, 'pc.ports', { n: ports.length }), tip: r.kind === 'l2tp' ? t(lang, 'pc.l2tpPorts') : ports.join('\n') }
        : { text: port1 || '—', tip: '' },
      reason: rs, buttons: B[st], menu: menu };
  }

  var PROTO_ICON = { wg: 'shield-check', awg1: 'shield', awg2: 'shield', awg3: 'shield', l2tp: 'lock', pptp: 'lock-open', sstp: 'lock', ikev2: 'key',
    openvpn: 'plug-connection', ocserv: 'globe', socks5: 'terminal', vless: 'link', hy2: 'arrow-up-arrow-down' };

  // Подтверждение на месте живёт, пока человек с ним — уговор с панелью (app.js confirmLife, REV-335-5 №2): открылось
  // второе (событие kaskad:confirm-open на document) — первое закрывается; щелчок или фокус вне него — закрывается; 30 с без
  // щелчков и клавиш в нём — закрывается само. close(had) — закрыть; had — фокус был в нём (закрылось само): вернуть фокус
  // туда, откуда пришли, как «Отмена» (REV-335-6 Н2). Пока кнопка мыши нажата, уход фокуса не закрывает — закроет щелчок
  // (стенд П8); касание — до click или TOUCH_DOWN_MS (pointerup приходит раньше ухода фокуса, REV-335-7 Т1); нажатие —
  // только основной кнопкой (П3). Возвращает «снять слежку».
  var CONFIRM_IDLE_MS = 30000, TOUCH_DOWN_MS = 700;
  function confirmLife(box, close) {
    if (typeof document === 'undefined' || !document.addEventListener) { return function () {}; }
    var tm = null, down = false, downTm = null;
    function off() {
      clearTimeout(tm);
      clearTimeout(downTm);
      document.removeEventListener('kaskad:confirm-open', other);
      document.removeEventListener('click', away, true);
      document.removeEventListener('focusin', away, true);
      document.removeEventListener('pointerdown', press, true);
      document.removeEventListener('pointerup', lift, true);
      document.removeEventListener('pointercancel', lift, true);
      document.removeEventListener('keydown', poke, true);
    }
    function done() { var had = box.contains(document.activeElement); off(); if (box.isConnected) { close(had); } }
    function arm() { clearTimeout(tm); tm = setTimeout(done, CONFIRM_IDLE_MS); }
    function other(ev) { if (ev.detail !== box) { done(); } }
    function away(ev) {
      if (!box.isConnected) { off(); } else if (!box.contains(ev.target) && !(down && ev.type === 'focusin')) { done(); }
      if (ev.type === 'click') { unpress(); }
    }
    function poke(ev) { if (box.contains(ev.target)) { arm(); } }
    function press(ev) { if (ev.button === 0 && !(MAC && ev.ctrlKey)) { clearTimeout(downTm); down = true; } poke(ev); }   // ctrl+щелчок на Mac — меню (REV-336 П-3); на Windows и Linux — обычный щелчок (REV-336-2 П-3)
    function lift(ev) {
      clearTimeout(downTm);
      if (ev.type === 'pointerup' && ev.pointerType && ev.pointerType !== 'mouse') { downTm = setTimeout(unpress, TOUCH_DOWN_MS); } else { down = false; }
    }
    function unpress() { clearTimeout(downTm); down = false; }
    var ev0;
    try { ev0 = new CustomEvent('kaskad:confirm-open', { detail: box }); } catch (e) { ev0 = null; }
    if (ev0 && document.dispatchEvent) { document.dispatchEvent(ev0); }
    document.addEventListener('kaskad:confirm-open', other);
    document.addEventListener('click', away, true);
    document.addEventListener('focusin', away, true);
    document.addEventListener('pointerdown', press, true);
    document.addEventListener('pointerup', lift, true);
    document.addEventListener('pointercancel', lift, true);
    document.addEventListener('keydown', poke, true);
    arm();
    return off;
  }
  function protocolCard(r, o) {
    var lang = o.lang || 'ru', m = protoModel(r, lang);
    var warnSw = (r.switches || []).filter(function (x) { return x.on && x.warnOn; })[0];
    // UI-3 ч.2: o.collapsible — свёрнутая карточка (список компьютера): шапка-кнопка, тело раскрывается на всю ширину ряда.
    // Круг 1 Т3 (Д2 вариант Б r3, №12, №15): o.row — строка списка «Протоколов» (rowWrap): раскрытая — строка описания и
    // строка кнопок; все действия кнопками, «⋯» нет, удаление — «Удалить протокол» справа с подтверждением на месте
    var row = !!o.row;
    var fold = !!o.collapsible || row;
    var head = fold ? null : h('div', { class: 'ux-pc__head' },
      h('span', { class: 'ux-pc__ic' + (m.state === 'run' ? ' is-on' : '') }, ic(PROTO_ICON[r.kind] || 'plug-connection', 20)),
      h('div', { class: 'ux-pc__tt' }, h('div', { class: 'ux-pc__name' }, h('h3', { class: 'ux-pc__title', text: r.title, title: r.title }),
        warnSw ? tri(warnSw.warnOn) : null),
        r.engine ? h('div', { class: 'ux-pc__eng ux-mono', text: r.engine, title: r.engine }) : null),
      chip(m.chip.tone, m.chip.text));
    var row2 = h('div', { class: 'ux-pc__desc' });
    if (m.reason) {
      var rb = h('div', { class: 'ux-reason ux-reason--' + m.reason.tone }, h('div', { class: 'ux-reason__l1', text: m.reason.l1 }), m.reason.l2 ? h('div', { class: 'ux-reason__l2' + (m.reason.log ? ' ux-reason__l2--log' : ''), text: m.reason.l2, title: m.reason.log ? m.reason.full : null }) : null,
        m.reason.l3 ? h('div', { class: 'ux-reason__l3', text: m.reason.l3 }) : null);
      row2.appendChild(rb);
    }
    // UI-1 F01: длинное описание — первая фраза на виду, остальное в «Подробнее» (строка описания общая на ряд карточек)
    var dsc = descParts(m.desc || '');
    if (!m.reason) { row2.appendChild(h('div', { class: 'ux-pc__d', text: fold ? String(m.desc || '') : dsc[0] })); }
    // UI-1 круг 2, UI-2: предупреждения (плашки-предупреждения extraBody и warnLine) — одной строкой на виду, в строке
    // «Подробнее» (первое — текстом, все целиком — в подсказке, для диктора и внутри): строка описания общая на ряд
    // карточек, плашка в пять строк растягивала весь ряд (живой узел 3.3.7.3: пустота 83–164 px у соседей). Плашки
    // «что-то не так» (danger) — на виду целиком, как в UI-1: тревога важнее ровного ряда
    var danger = function (n) { return !!(n.classList && /--danger\b/.test(n.className)); };
    var body = (o.extraBody || []).filter(Boolean);
    body.filter(danger).forEach(function (n) { row2.appendChild(n); });
    var plaques = body.filter(function (n) { return !danger(n); });
    // UI-2 круг 2 (REV-ui2 З2): порядок — по смыслу, а не по месту в массиве: warnLine — главное предупреждение карточки
    // (его выбирает владелец карточки: у Hysteria2 — «UDP в мобильных сетях РФ не проверен, держите рядом VLESS»), плашки — за «+N»
    var flags = [].concat(o.warnLine || []).concat(plaques.map(function (n) { return (n.textContent || '').trim(); }));
    flags = flags.filter(function (x, i) { return x && flags.indexOf(x) === i; });
    var warn = flags.length ? h('span', { class: 'ux-pc__warn', title: flags.join('\n') }, ic('triangle-exclamation', 14), h('span', { class: 'ux-pc__warnT', text: flags[0] }),
      flags.length > 1 ? h('span', { class: 'ux-pc__warnN', text: '+' + (flags.length - 1) }) : null) : null;
    var more = plaques.concat((o.extraMore || []).filter(Boolean));
    if (fold) {
      // раскрытая карточка — всё целиком, без «Подробнее»: плашки и подробности входа как есть; строкой — только
      // предупреждения, которых нет среди плашек (переключатель «без шифрования» и т. п.)
      var shown = more.map(function (n) { return (n.textContent || '').trim(); });
      var rest = flags.concat(warnSw ? [warnSw.warnOn] : []).filter(function (x, i, a) { return x && a.indexOf(x) === i && shown.indexOf(x) < 0; });
      if (rest.length) { row2.appendChild(h('div', { class: 'ux-pc__warn' }, ic('triangle-exclamation', 14), h('span', { text: rest.join(' · ') }))); }
      more.forEach(function (n) { row2.appendChild(n); });
      more = [];
    }
    if (!m.reason && dsc[1] && !fold) { more.splice(plaques.length, 0, h('div', { class: 'ux-pc__d', text: dsc[1] })); }
    if (more.length) {
      // открыто ли — у владельца карточки (moreOpen / onMore): карточку пересобирают при любом изменении данных
      var det = h('details', { class: 'ux-pc__more' }, h('summary', { class: 'ux-pc__moret' + (warn ? ' ux-pc__moret--warn' : ''), 'aria-label': t(lang, 'pc.details') + ': ' + r.title + (warn ? '. ' + flags.join(' ') : '') },
        warn, h('span', { class: 'ux-pc__moreL', text: t(lang, 'pc.details') }), ic('chevron-down', 14)));
      more.forEach(function (n) { det.appendChild(n); });
      if (o.moreOpen) { det.open = true; }
      if (o.onMore) { det.addEventListener('toggle', function () { o.onMore(det.open); }); }
      row2.appendChild(det);
    } else if (warn && !fold) { row2.appendChild(warn); }
    var portNode = m.ports.tip ? (function () {
      var tip = h('span', { class: 'ux-tip ux-tip--r ux-mono', role: 'tooltip', hidden: true, text: m.ports.tip });
      var b = h('button', { type: 'button', class: 'ux-dotted', text: m.ports.text, 'aria-label': m.ports.text + ': ' + m.ports.tip.replace(/\n/g, ', ') });
      var w = h('span', { class: 'ux-triw' }, b, tip);
      var pin = false;
      w.addEventListener('mouseenter', function () { tip.hidden = false; });
      w.addEventListener('mouseleave', function () { if (!pin) { tip.hidden = true; } });
      b.addEventListener('click', function (ev) { ev.stopPropagation(); pin = !pin; tip.hidden = !pin; });
      reg(openTips, w, function () { pin = false; tip.hidden = true; });
      return w;
    })() : h('span', { class: 'ux-mono', text: m.ports.text });
    var nums = h('div', { class: 'ux-pc__nums' },
      h('div', { class: 'ux-kv' }, h('span', { text: t(lang, 'pc.port') }), portNode),
      h('div', { class: 'ux-kv' }, h('span', { text: t(lang, 'pc.devices') }), h('b', { text: r.devices === null || r.devices === undefined ? '—' : String(r.devices) })),
      h('div', { class: 'ux-kv' }, h('span', { text: t(lang, 'pc.sessions') }), o.sessionsNode || h('b', { text: r.sessions === null || r.sessions === undefined ? '—' : String(r.sessions) })));
    var acts = h('div', { class: 'ux-pc__acts' });
    var restartLine = null;
    function btn(id) {
      if (id === 'toggle') { return h('button', { type: 'button', class: 'ux-btn', text: t(lang, r.enabled ? 'pc.disable' : 'pc.enable'), onclick: function () { o.onToggle(!r.enabled); } }); }
      if (id === 'port') { return h('button', { type: 'button', class: 'ux-btn', text: r.portLabel || t(lang, 'pc.changePort'), onclick: function () { o.onPort(); } }); }
      if (id === 'log') { return h('button', { type: 'button', class: 'ux-btn', text: t(lang, 'pc.log'), onclick: function () { o.onLog(); } }); }
      if (id === 'apply') {
        if (!o.onApply) { return btn('restart'); }
        var ab = h('button', { type: 'button', class: 'ux-btn ux-btn--primary', text: t(lang, 'pc.apply') });
        ab.addEventListener('click', function () {
          if (ab.disabled) { return; }
          ab.disabled = true;
          Promise.resolve(o.onApply()).then(function () { ab.disabled = false; }, function () { ab.disabled = false; });
        });
        return ab;
      }
      if (id === 'restart') {
        var b = h('button', { type: 'button', class: 'ux-btn', text: t(lang, 'pc.restart') });
        restartBtn = b;
        b.addEventListener('click', function () {
          if (b.disabled) { return; }
          // общая служба (ядро шлюза у VLESS/SOCKS5, accel-ppp у L2TP/PPTP/SSTP, strongSwan у L2TP/IKEv2) — сначала
          // честно сказать, у кого ещё оборвётся связь
          if (sharedText) { askRestart(); return; }
          goRestart(b);
        });
        return b;
      }
      return null;
    }
    function goRestart(b) {
      if (!b || b.disabled) { return; }
      b.disabled = true;
      put(b, spin('sm'), h('span', { text: t(lang, 'pc.restarting') }));
      Promise.resolve(o.onRestart()).then(function (res) {
        b.disabled = false;
        put(b, t(lang, 'pc.restart'));
        if (res && res.running === false) {
          if (restartLine && restartLine.parentNode) { restartLine.parentNode.removeChild(restartLine); }
          var old = row2.querySelector('.ux-reason__l3');
          if (old && old.parentNode) { old.parentNode.removeChild(old); }
          restartLine = h('div', { class: 'ux-reason__l3', text: t(lang, 'pc.again', { t: hm(res.at) }) });
          var box = row2.querySelector('.ux-reason');
          (box || row2).appendChild(restartLine);
        }
      }, function () { b.disabled = false; put(b, t(lang, 'pc.restart')); });
    }
    var restartBtn = null, sh = r.shared || null, shWith = sh && sh.with ? sh.with.filter(Boolean).map(String) : [];
    var sharedText = !sh ? '' : sh.gateway ? t(lang, 'pc.rsGw') + (shWith.length ? ' ' + t(lang, 'pc.rsAlso', { list: shWith.join(', ') }) : '')
      : shWith.length ? t(lang, 'pc.rsWith', { list: shWith.join(', ') }) : '';
    function askRestart() {
      // «Отмена» и закрылось само с фокусом в нём — фокус на «Перезапустить», откуда пришли
      var back = function (had) { drawActs(); if (had !== false && restartBtn && restartBtn.isConnected) { restartBtn.focus({ preventScroll: true }); } };
      var no = h('button', { type: 'button', class: 'ux-btn ux-btn--ghost', text: t(lang, 'c.cancel'), onclick: back });
      var yes = h('button', { type: 'button', class: 'ux-btn ux-btn--primary', text: t(lang, 'pc.restart') });
      yes.addEventListener('click', function () { drawActs(); goRestart(restartBtn); });
      // Esc — как «Отмена» (как у подтверждения удаления)
      var box = h('div', { class: 'ux-confirm' + (row ? ' ux-confirm--row' : ''), role: 'alertdialog', 'aria-label': t(lang, 'pc.rsQ', { title: r.title }),
        onkeydown: function (e) { if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); e.stopPropagation(); back(); } } },
        h('div', { class: 'ux-confirm__t' }, h('b', { text: t(lang, 'pc.rsQ', { title: r.title }) }), ' ', sharedText),
        h('div', { class: 'ux-row' }, no, yes));
      put(acts, box);
      lifeOff = confirmLife(box, back);
      no.focus();
    }
    // меню «⋯»
    var menuWrap = h('span', { class: 'ux-menuw' });
    var menuBtn = h('button', { type: 'button', class: 'ux-btn ux-btn--icon', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-label': t(lang, 'pc.more', { title: r.title }), title: t(lang, 'pc.more', { title: r.title }) }, ic('ellipsis', 18));
    var menu = h('div', { class: 'ux-menu ux-menu--up ux-menu--r', role: 'menu', hidden: true });
    var closeMenu = function () { menu.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); };
    function item(label, icon, on, cls) {
      return h('button', { type: 'button', role: 'menuitem', class: 'ux-mi' + (cls ? ' ' + cls : ''), onclick: function (ev) { ev.stopPropagation(); closeMenu(); on(); } }, icon ? ic(icon, 16) : null, h('span', { text: label }));
    }
    // те же пункты для листа хозяина на телефоне (o.sheetMenu — openMenuSheet панели, REV-336-mobile З-9): один вид «⋯»
    // с меню человека — лист с подложкой, шапкой и захватом
    var sheetItems = [];
    if (!row) m.menu.forEach(function (id) {
      if (id.slice(0, 3) === 'sw:') {
        var x = (r.switches || []).filter(function (s) { return 'sw:' + s.key === id; })[0];
        var sw = h('span', { class: 'ux-sw' + (x.on ? ' is-on' : ''), 'aria-hidden': 'true' }, h('span', { class: 'ux-sw__k' }));
        sheetItems.push({ label: x.title, sub: x.sub, checked: !!x.on, on: function () { o.onOption(x.key, !x.on); } });
        menu.appendChild(h('button', { type: 'button', role: 'menuitemcheckbox', 'aria-checked': x.on ? 'true' : 'false', class: 'ux-mi ux-mi--sw',
          onclick: function (ev) { ev.stopPropagation(); closeMenu(); o.onOption(x.key, !x.on); } },
          h('span', { class: 'ux-mi__b' }, h('b', { text: x.title }), x.sub ? h('span', { class: 'ux-mi__s', text: x.sub }) : null), sw));
      } else if (id === 'toggle') { menu.appendChild(item(t(lang, r.enabled ? 'pc.disable' : 'pc.enable'), r.enabled ? 'pause' : 'play', function () { o.onToggle(!r.enabled); }));
        sheetItems.push({ label: t(lang, r.enabled ? 'pc.disable' : 'pc.enable'), icon: r.enabled ? 'pause' : 'play', on: function () { o.onToggle(!r.enabled); } }); }
      else if (id === 'port') { menu.appendChild(item(r.portLabel || t(lang, 'pc.changePort'), 'sliders', o.onPort)); sheetItems.push({ label: r.portLabel || t(lang, 'pc.changePort'), icon: 'sliders', on: o.onPort }); }
      else if (id === 'log') { menu.appendChild(item(t(lang, 'pc.log'), 'file-text', o.onLog)); sheetItems.push({ label: t(lang, 'pc.log'), icon: 'file-text', on: o.onLog }); }
      else if (id === 'delete') {
        (o.extraMenu || []).forEach(function (x) { menu.appendChild(item(x.label, x.icon, x.on)); sheetItems.push({ label: x.label, icon: x.icon, on: x.on }); });
        menu.appendChild(item(t(lang, 'pc.delete'), 'trash-bin', askDelete, 'ux-mi--danger'));
        sheetItems.push({ label: t(lang, 'pc.delete'), icon: 'trash-bin', on: askDelete, danger: true });
      }
    });
    menuBtn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      // хозяин не взял (не его телефон: у ux.js и панели свои пороги) — своё меню
      if (phone() && typeof o.sheetMenu === 'function') { closeAllMenus(); if (o.sheetMenu(menuBtn, sheetItems, { label: t(lang, 'pc.more', { title: r.title }) })) { return; } }
      var on = menu.hidden;
      closeAllMenus();
      menu.hidden = !on;
      menuBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (on) { placeMenu(menu, menuBtn, true, true); var f = menu.querySelector('button'); if (f) { f.focus({ preventScroll: true }); } }
    });
    reg(menus, menuWrap, closeMenu);
    menuWrap.appendChild(menuBtn);
    menuWrap.appendChild(menu);
    var lifeOff = null;                  // слежка открытого подтверждения (confirmLife): ряд вернулся — снять
    // строка списка (o.row): «Удалить протокол» — кнопкой справа, подтверждение — на месте ряда кнопок
    var delBtn = row ? h('button', { type: 'button', class: 'ux-btn ux-btn--sm ux-btn--ghost ux-pc__del', onclick: function () { askDelete(); } },
      ic('trash-bin', 16), h('span', { text: t(lang, 'pc.delete') })) : null;
    function drawActs() {
      if (lifeOff) { lifeOff(); lifeOff = null; }
      if (row) { put(acts, h('div', { class: 'ux-pc__btns' }, rowButtons(r, o, lang, m, btn)), h('span', { class: 'ux-spacer' }), delBtn); return; }
      put(acts, h('div', { class: 'ux-pc__btns' }, m.buttons.map(btn)), h('span', { class: 'ux-spacer' }), menuWrap);
    }
    // подтверждение удаления — на месте ряда действий
    // «Отмена», Esc и итог — ряд действий обратно, фокус на «⋯» (у строки списка — на «Удалить протокол»), откуда пришли,
    // а не в document.body (R-333 П1‴)
    function askDelete() {
      var from = row ? delBtn : menuBtn;
      var back = function (had) { drawActs(); if (had !== false && from.isConnected) { from.focus({ preventScroll: true }); } };
      var no = h('button', { type: 'button', class: 'ux-btn ux-btn--ghost', text: t(lang, 'c.cancel'), onclick: back });
      var yes = h('button', { type: 'button', class: 'ux-btn ux-btn--danger', text: t(lang, 'c.delete') });
      // «Удалить» нажато — ждём ответа, а не времени и не щелчка мимо
      yes.addEventListener('click', function () { if (lifeOff) { lifeOff(); lifeOff = null; } yes.disabled = true; no.disabled = true; var fin = function () { back(); }; Promise.resolve(o.onDelete()).then(fin, fin); });
      // o.delText — что будет с устройствами (хозяин знает их число), иначе общее «перестанут подключаться»
      var box = h('div', { class: 'ux-confirm' + (row ? ' ux-confirm--row' : ''), role: 'alertdialog', 'aria-label': t(lang, 'pc.delQ', { title: r.title }),
        onkeydown: function (e) { if (e.key === 'Escape' || e.key === 'Esc') { e.preventDefault(); e.stopPropagation(); if (!yes.disabled) { back(); } } } },
        h('div', { class: 'ux-confirm__t' }, h('b', { text: t(lang, 'pc.delQ', { title: r.title }) }), ' ', o.delText ? String(o.delText) : t(lang, 'pc.delS')),
        h('div', { class: 'ux-row' }, no, yes));
      put(acts, box);
      lifeOff = confirmLife(box, back);               // ушли от него — ряд действий обратно, фокус не трогаем (had — вернуть)
      no.focus();
    }
    drawActs();
    if (row) { return rowWrap(r, o, lang, m, { flags: flags.concat(warnSw ? [warnSw.warnOn] : []), loud: body.filter(danger), row2: row2, acts: acts }); }
    if (fold) { return foldWrap(r, o, lang, m, { flags: flags.concat(warnSw ? [warnSw.warnOn] : []), loud: body.filter(danger), row2: row2, nums: nums, acts: acts }); }
    return h('article', { class: 'ux-pc' + (m.state === 'off' ? ' is-off' : ''), 'data-kind': r.kind, 'data-state': m.state }, head, row2, nums, acts);
  }
  // UI-3 ч.2: свёрнутая карточка протокола. Шапка — кнопка (Enter/пробел, aria-expanded): значок, имя, движок мелко, чип,
  // справа — «порт · устройства · сессии», шеврон. Поломка (причина «не работает», плашка danger, o.alertLine — например,
  // предупреждение движка AmneziaWG) — одной красной строкой и в свёрнутой; жёлтое — значком у имени (текст — в раскрытой).
  // Раскрытая — на всю ширину ряда (CSS .ux-pc--fold.is-open): описание, подробности, числа, действия. Открыта ли — у
  // владельца (o.open / o.onOpen(open)): карточку пересобирают при любом изменении данных
  var foldSeq = 0;
  function foldWrap(r, o, lang, m, p) {
    var id = 'ux-pcb-' + (++foldSeq), open = !!o.open;
    var dev = r.devices === null || r.devices === undefined ? '' : t(lang, 'pc.sumDev', { n: r.devices });
    var sessFmt = function (x) { return x === null || x === undefined || x !== x ? '' : ' · ' + t(lang, 'pc.sumSess', { n: x }); };
    var sess = typeof o.sessionsLive === 'function' ? o.sessionsLive(sessFmt) : document.createTextNode(sessFmt(r.sessions));
    var sum = h('span', { class: 'ux-pc__sum' }, m.ports.text + (dev ? ' · ' + dev : ''), sess);
    var yellow = p.flags.filter(function (x, i, a) { return x && a.indexOf(x) === i; });
    var name = h('div', { class: 'ux-pc__name' }, h('h3', { class: 'ux-pc__title', text: r.title, title: r.title }),
      yellow.length ? h('span', { class: 'ux-pc__flag', title: yellow.join('\n') }, ic('triangle-exclamation', 14), h('span', { class: 'ux-vh', text: yellow.join('. ') })) : null);
    // П4: подпись для диктора — «Имя, состояние, порт, устройства» (иначе имя склеивалось из всех строк шапки)
    var aria = [r.title, m.chip.text, m.ports.text, dev, o.hint && o.hint.text ? String(o.hint.text) : ''].filter(function (x) { return x; }).join(', ');
    var head = h('div', { class: 'ux-pc__head ux-pc__tog', role: 'button', tabindex: '0', 'aria-expanded': open ? 'true' : 'false', 'aria-controls': id, 'aria-label': aria },
      h('span', { class: 'ux-pc__ic' + (m.state === 'run' ? ' is-on' : '') }, ic(PROTO_ICON[r.kind] || 'plug-connection', 20)),
      h('div', { class: 'ux-pc__tt' }, h('div', { class: 'ux-pc__l1' }, name, chip(m.chip.tone, m.chip.text)),
        h('div', { class: 'ux-pc__l2' }, r.engine ? h('span', { class: 'ux-pc__eng ux-mono', text: r.engine, title: r.engine }) : null, sum),
        // третья строка свёрнутой (o.hint {text, rec}): у VLESS — причина Reality, у рекомендации — акцентом; в раскрытой
        // скрыта (одно предупреждение — один раз; макет vless-tls r1, К1)
        o.hint && o.hint.text ? h('div', { class: 'ux-pc__hint' + (o.hint.rec ? ' ux-pc__hint--rec' : ''), title: String(o.hint.text), text: String(o.hint.text) }) : null),
      h('span', { class: 'ux-pc__chev' }, ic('chevron-down', 18)));
    var reds = [].concat(m.reason && m.reason.tone === 'err' ? [m.reason.l1] : [])
      .concat(p.loud.map(function (n) { return (n.textContent || '').trim(); })).concat(o.alertLine || [])
      .filter(function (x, i, a) { return x && a.indexOf(x) === i; });
    var alert = reds.length ? h('div', { class: 'ux-pc__alert', title: reds.join('\n') }, ic('circle-exclamation', 14), h('span', { class: 'ux-pc__alertT', text: reds[0] }),
      reds.length > 1 ? h('span', { class: 'ux-pc__warnN', text: '+' + (reds.length - 1) }) : null) : null;
    var main = h('div', { class: 'ux-pc__main' }, p.row2);
    var body = h('div', { class: 'ux-pc__body', id: id }, main, p.nums, p.acts);
    var card = h('article', { class: 'ux-pc ux-pc--fold' + (m.state === 'off' ? ' is-off' : ''), 'data-kind': r.kind, 'data-state': m.state }, head, alert, body);
    card.uxBody = body;                    // foldGrid переносит тело в полосу под рядом (card.uxDrawer)
    function set(on, user) {
      open = on;
      card.classList.toggle('is-open', on);
      head.setAttribute('aria-expanded', on ? 'true' : 'false');
      body.hidden = !on;
      if (card.uxLayout) { card.uxLayout(); }          // и находит полосу, если её подменило удержание ряда (data-hold-row)
      if (card.uxDrawer) { card.uxDrawer.hidden = !on; card.uxDrawer.classList.toggle('is-open', on); }
      if (alert) { alert.hidden = on; }
      if (user) {
        if (o.onOpen) { o.onOpen(on); }
        if (!on) { runAll(menus); }
        // тело — полосой под рядом: держать его в виду (шапка остаётся на месте)
        var shown = card.uxDrawer || card;
        if (on && shown.scrollIntoView) { try { shown.scrollIntoView({ block: 'nearest' }); } catch (e) { shown.scrollIntoView(false); } }
      }
    }
    head.addEventListener('click', function () { set(!open, true); });
    head.addEventListener('keydown', function (e) {
      if (e.target !== head) { return; }
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') { e.preventDefault(); set(!open, true); }
    });
    set(open, false);
    return card;
  }
  // Круг 1 Т3 (Д2 вариант Б r3, №15): кнопки строки списка «Протоколов» — всё, что было в карточке и её «⋯», кнопками:
  // главные по состоянию (protoModel: Выключить / Включить, Сменить порт, Перезапустить, Применить настройки), то, что
  // лежало в «⋯» (Включить, Сменить порт), свои действия протокола (o.extraMenu: «Маскировка VLESS…», «Смена портов»…),
  // «Журнал» — всегда, затем пункты с tail («Подпись…»), переключатели протокола (role="switch"). «Удалить протокол» —
  // отдельно, справа (protocolCard). Порядок — как в макете Пр1: Выключить · Сменить порт · свои · Журнал · Подпись…
  function rowButtons(r, o, lang, m, btn) {
    var ids = m.buttons.slice();
    m.menu.forEach(function (id) { if ((id === 'toggle' || id === 'port' || id === 'log') && ids.indexOf(id) < 0) { ids.push(id); } });
    if (ids.indexOf('log') < 0) { ids.push('log'); }
    var head = [], tail = [];
    ids.forEach(function (id) { if (id !== 'log') { head.push(btn(id)); } });
    var mine = [], late = [];
    (o.extraMenu || []).forEach(function (x) {
      var b = h('button', { type: 'button', class: 'ux-btn', onclick: function () { x.on(); } }, h('span', { text: x.label }));
      (x.tail ? late : mine).push(b);
    });
    tail = mine.concat([btn('log')], late);
    (r.switches || []).forEach(function (x) {
      tail.push(h('button', { type: 'button', role: 'switch', 'aria-checked': x.on ? 'true' : 'false', class: 'ux-btn ux-btn--swr', title: x.sub || null,
        onclick: function () { o.onOption(x.key, !x.on); } },
        h('span', { class: 'ux-sw' + (x.on ? ' is-on' : ''), 'aria-hidden': 'true' }, h('span', { class: 'ux-sw__k' })), h('span', { text: x.title })));
    });
    return head.concat(tail).filter(Boolean).map(function (b) { b.classList.add('ux-btn--sm'); return b; });
  }
  // Круг 1 Т3 (Д2 вариант Б r3, №11, №12): строка списка «Протоколов» (.kb2-pl). Свёрнутая — кнопка-строка: значок, имя и
  // состояние, устройства точками и «N из M на связи» (o.cells.dev — узел хозяина, живой), порт (несколько — «3 порта», все —
  // в подсказке и в раскрытой), «Для чего» (o.cells.roles), шеврон. Поломка (причина «не работает», плашка danger,
  // o.alertLine) — красной строкой под строкой; у VLESS — причина Reality или рекомендация (o.hint); жёлтое — значком у имени.
  // Раскрытая (№12) — строка описания (с причиной и подробностями протокола: режим VLESS, движок AmneziaWG, сертификат…),
  // o.cells.meta (порт и «для чего» — где их колонок нет: узкий список, телефон), строка кнопок. Открыта ли — у владельца
  // (o.open / o.onOpen), как у свёрнутой карточки
  function rowWrap(r, o, lang, m, p) {
    var id = 'ux-pcb-' + (++foldSeq), open = !!o.open, c = o.cells || {};
    var yellow = p.flags.filter(function (x, i, a) { return x && a.indexOf(x) === i; });
    var TONE = { ok: 'ok', warn: 'warn', err: 'err' };
    var name = h('span', { class: 'kb2-pl__n' },
      h('span', { class: 'kb2-pl__t' }, h('b', { text: r.title, title: r.title }),
        yellow.length ? h('span', { class: 'ux-pc__flag', title: yellow.join('\n') }, ic('triangle-exclamation', 14), h('span', { class: 'ux-vh', text: yellow.join('. ') })) : null),
      h('span', { class: 'kb2-st kb2-st--' + (TONE[m.chip.tone] || 'off'), text: m.chip.text }),
      // 3.3.7.15 (№13 r2, F1): «N устройств ждут новых настроек» — под состоянием; r.wait {tone: hard|soft, text, tip}
      r.wait && r.wait.text ? h('span', { class: 'kb2-wait kb2-wait--' + (r.wait.tone === 'hard' ? 'hard' : 'soft'), title: r.wait.tip || r.wait.text }, ic('key', 12), r.wait.text) : null);
    var port = h('span', { class: 'kb2-port', title: m.ports.tip ? m.ports.text + ': ' + m.ports.tip.replace(/\n/g, ', ') : null },
      h('span', { class: 'ux-mono', text: m.ports.text }), r.engine ? h('small', { text: r.engine, title: r.engine }) : null);
    var head = h('button', { type: 'button', class: 'kb2-pl__r ux-pc__tog', 'aria-expanded': open ? 'true' : 'false', 'aria-controls': id },
      h('span', { class: 'kb2-pl__ic ux-pc__ic' + (m.state === 'run' ? ' is-on' : '') }, ic(PROTO_ICON[r.kind] || 'plug-connection', 20)),
      name, c.dev || h('span', { class: 'kb2-dv' }), port, c.roles || h('span', { class: 'kb2-rl' }),
      h('span', { class: 'kb2-pl__chev ux-pc__chev' }, ic('chevron-down', 16)));
    var reds = [].concat(m.reason && m.reason.tone === 'err' ? [m.reason.l1] : [])
      .concat(p.loud.map(function (n) { return (n.textContent || '').trim(); })).concat(o.alertLine || [])
      .filter(function (x, i, a) { return x && a.indexOf(x) === i; });
    var alert = reds.length ? h('div', { class: 'kb2-pl__al ux-pc__alert', title: reds.join('\n') }, ic('circle-exclamation', 14), h('span', { class: 'ux-pc__alertT', text: reds[0] }),
      reds.length > 1 ? h('span', { class: 'ux-pc__warnN', text: '+' + (reds.length - 1) }) : null) : null;
    // у VLESS — причина Reality или рекомендация (одно предупреждение — один раз: в раскрытой — плашкой в описании)
    var hint = !alert && o.hint && o.hint.text ? h('div', { class: 'kb2-pl__al ux-pc__hint' + (o.hint.rec ? ' ux-pc__hint--rec' : ''), title: String(o.hint.text), text: String(o.hint.text) }) : null;
    // o.rowExtra — свои строки раскрытой (3.3.7.15: «Порт · MTU · TLS» и «Применено … · Вернуть прежние») — под описанием
    var body = h('div', { class: 'kb2-pl__b ux-pc__body', id: id }, p.row2, (o.rowExtra || []).filter(Boolean), c.meta || null, p.acts);
    var g = h('div', { class: 'ux-pc ux-pc--row kb2-pl__g' + (m.state === 'off' ? ' is-off' : ''), 'data-kind': r.kind, 'data-state': m.state }, head, alert, hint, body);
    function set(on, user) {
      open = on;
      g.classList.toggle('is-open', on);
      head.setAttribute('aria-expanded', on ? 'true' : 'false');
      body.hidden = !on;
      if (alert) { alert.hidden = on; }
      if (hint) { hint.hidden = on; }
      if (user) {
        if (o.onOpen) { o.onOpen(on); }
        if (on && body.scrollIntoView) { try { body.scrollIntoView({ block: 'nearest' }); } catch (e) { body.scrollIntoView(false); } }
      }
    }
    head.addEventListener('click', function () { set(!open, true); });
    set(open, false);
    return g;
  }
  // UI-3 круг 2 (З1): сетка свёрнутых карточек. Раскрытая не уходит со своего места: её шапка остаётся в ряду (соседи на
  // местах, под курсором та же карточка), тело — полосой .ux-pc--drawer на всю ширину сразу после последней карточки её
  // ряда. Порядок задаёт DOM (не CSS order и не dense) — Tab идёт так же, как видно: шапки ряда, затем тела раскрытых.
  // Раскладка — при раскрытии и при смене числа колонок (ResizeObserver); двигаются только полосы
  function foldGrid(cards, o) {
    o = o || {};
    var grid = h('div', { class: 'ux-pgrid ux-pgrid--fold' });
    var list = (cards || []).filter(function (c) { return c; });
    var cols = typeof o.cols === 'function' ? o.cols : function () {
      try { var v = g.getComputedStyle(grid).gridTemplateColumns; return v && v !== 'none' ? v.trim().split(/\s+/).length : 1; } catch (e) { return 1; }
    };
    function layout() {
      // прежняя сетка после перерисовки (наблюдатель зовёт и отцепленную) — не трогать: её полосу удержание ряда уже
      // перенесло в новую сетку, перенос назад унёс бы открытое подтверждение со страницы
      if (grid.isConnected === false) { return; }
      // удержание ряда (открытое «⋯» или подтверждение в теле) ставит в новую сетку прежнюю полосу вместо новой — берём её
      list.forEach(function (c) {
        var d = c.uxDrawer;
        if (!d || d.parentNode === grid) { return; }
        var alt = Array.prototype.filter.call(grid.children, function (x) { return x !== d && x.classList && x.classList.contains('ux-pc--drawer') && x.getAttribute('data-drawer') === d.getAttribute('data-drawer'); })[0];
        if (alt) { c.uxDrawer = alt; }
      });
      var n = Math.max(1, Number(cols()) || 1), want = [];
      for (var i = 0; i < list.length; i += n) {
        var row = list.slice(i, i + n);
        want = want.concat(row);
        row.forEach(function (c, k) {
          if (!c.uxDrawer || (c.uxDrawer.parentNode && c.uxDrawer.parentNode !== grid)) { return; }     // чужую полосу не забираем
          want.push(c.uxDrawer);
          if (c.uxDrawer.style && c.uxDrawer.style.setProperty) { c.uxDrawer.style.setProperty('--ux-col', String(k)); c.uxDrawer.style.setProperty('--ux-cols', String(n)); }
        });
      }
      var cur = Array.prototype.slice.call(grid.children);
      if (cur.length === want.length && cur.every(function (x, k) { return x === want[k]; })) { return; }
      // узел с фокусом (шапка или полоса с открытым подтверждением) не трогаем — переставляем всё вокруг него: перенос
      // узла с фокусом в DOM снимает фокус, а подтверждение на месте от этого закрывается
      var ae = document.activeElement, keep = null;
      cur.forEach(function (x) { if (ae && x.contains && x.contains(ae)) { keep = x; } });
      var at = keep ? want.indexOf(keep) : -1;
      if (at < 0 || !grid.insertBefore) { want.forEach(function (x) { grid.appendChild(x); }); return; }
      want.slice(0, at).forEach(function (x) { grid.insertBefore(x, keep); });
      want.slice(at + 1).forEach(function (x) { grid.appendChild(x); });
    }
    list.forEach(function (c) {
      grid.appendChild(c);
      if (!c.uxBody) { return; }
      var key = c.getAttribute && c.getAttribute('data-hold-row');
      var d = h('div', { class: 'ux-pc ux-pc--drawer' + (c.classList.contains('is-open') ? ' is-open' : ''), 'data-drawer': c.getAttribute('data-kind') || '',
        'data-hold-row': key ? key + ':open' : null }, c.uxBody);
      d.hidden = !c.classList.contains('is-open');
      c.uxDrawer = d;
      c.uxLayout = layout;
      grid.appendChild(d);
    });
    layout();
    if (typeof g.ResizeObserver === 'function') {
      var last = 0;
      try { new g.ResizeObserver(function () { var n = cols(); if (n !== last) { last = n; layout(); } }).observe(grid); } catch (e) { /* без наблюдателя — раскладка при раскрытии */ }
    }
    grid.uxLayout = layout;
    return grid;
  }
  function closeAllMenus() { runAll(menus); }
  // Меню — position: fixed у кнопки (как openMenu панели): карточки ниже не накрывают его, края карточек не режут.
  // Вверх, если просили и есть место (или вниз не влезает); по горизонтали — в пределах окна. Телефон — лист снизу (CSS).
  function phone() { try { return g.matchMedia('(max-width: 767px)').matches; } catch (e) { return false; } }
  function placeMenu(menu, anchor, up, right) {
    if (phone()) { menu.removeAttribute('style'); return; }
    var st = menu.style;
    st.position = 'fixed'; st.left = '0px'; st.top = '0px'; st.right = 'auto'; st.bottom = 'auto';
    var r = anchor.getBoundingClientRect(), vw = g.innerWidth, vh = g.innerHeight, w = menu.offsetWidth, hh = menu.offsetHeight;
    var left = Math.max(8, Math.min(right ? r.right - w : r.left, vw - w - 8));
    var below = r.bottom + 6, above = r.top - 6 - hh;
    var top = up ? (above >= 8 ? above : below) : (below + hh <= vh - 8 || above < 8 ? below : above);
    st.left = Math.round(left) + 'px';
    st.top = Math.round(Math.max(8, Math.min(top, vh - hh - 8))) + 'px';
  }
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('scroll', function (ev) {
      if (ev.target && ev.target.closest && ev.target.closest('.ux-menu')) { return; }
      closeAllMenus();
    }, true);
    if (g.addEventListener) { g.addEventListener('resize', closeAllMenus); }
  }
  if (typeof document !== 'undefined' && document.addEventListener) {
    document.addEventListener('click', closeAllMenus);
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { closeAllMenus(); } });
  }

  // Журнал — выезжающая панель справа: последние 30 строк, ошибки подсвечены, «Скопировать»
  function logDrawer(o) {
    var lang = o.lang || 'ru', prev = document.activeElement;
    var lines = o.lines || [];
    var body = h('div', { class: 'ux-log__body ux-mono', tabindex: '0' });
    function fill(ls) {
      lines = ls || [];
      put(body, lines.length ? lines.map(function (l) { return h('div', { class: 'ux-log__l' + (l.err ? ' is-err' : ''), text: String(l.t || '') }); })
        : h('div', { class: 'ux-tx3', text: o.loading ? t(lang, 'pc.logLoading') : t(lang, 'pc.logEmpty') }));
    }
    fill(lines);
    var reasonLine = h('div', { class: 'ux-log__why', text: o.reason || '', hidden: !o.reason });
    var cmdNode = h('code', { text: o.cmd || '' });
    var x = h('button', { type: 'button', class: 'ux-x', 'aria-label': t(lang, 'c.close'), title: t(lang, 'c.close') }, ic('xmark', 18));
    var panel = h('aside', { class: 'ux-log', role: 'dialog', 'aria-modal': 'true', 'aria-label': t(lang, 'pc.logTitle', { title: o.title }) },
      h('div', { class: 'ux-log__h' },
        h('div', { class: 'ux-log__tt' }, h('div', { class: 'ux-log__title', text: t(lang, 'pc.logTitle', { title: o.title }) }),
          h('div', { class: 'ux-log__sub' }, h('span', { text: t(lang, 'pc.logSub') }), cmdNode)),
        copyBtn(lang, function () { return lines.map(function (l) { return l.t; }).join('\n'); }), x),
      reasonLine, body);
    var back = h('div', { class: 'ux-log__back' });
    var root = h('div', { class: 'ux-logroot' }, back, panel);
    function close() {
      document.removeEventListener('keydown', onKey, true);
      if (root.parentNode) { root.parentNode.removeChild(root); }
      if (prev && prev.focus) { try { prev.focus(); } catch (e) { /* узла больше нет */ } }
      if (o.onClose) { o.onClose(); }
    }
    function onKey(ev) { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } }
    back.addEventListener('click', close);
    x.addEventListener('click', close);
    document.addEventListener('keydown', onKey, true);
    (o.host || document.body).appendChild(root);
    x.focus();
    return { el: root, close: close, set: function (d) { if (d.reason !== undefined) { reasonLine.textContent = d.reason || ''; reasonLine.hidden = !d.reason; } if (d.cmd) { cmdNode.textContent = d.cmd; } o.loading = false; fill(d.lines || []); } };
  }

  // ═════════════════════════════════════════════════════════════════════════
  // ShareMenu — «Поделиться ▾» у человека, HANDOFF §5
  // ═════════════════════════════════════════════════════════════════════════
  function shareMenu(o) {
    var lang = o.lang || 'ru';
    var btn = h('button', { type: 'button', class: 'ux-btn ux-share__btn', 'aria-haspopup': 'menu', 'aria-expanded': 'false' },
      ic('arrow-up-from-line', 16), h('span', { text: t(lang, 'sm.share') }), h('span', { class: 'ux-share__chev' }, ic('chevron-down', 16)));
    var menu = h('div', { class: 'ux-menu ux-share__menu' + (o.up ? ' ux-menu--up' : ''), role: 'menu', hidden: true });
    var wrap = h('span', { class: 'ux-menuw ux-share' }, btn, menu);
    var view = 'list', seq = 0;
    function close() { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('is-open'); }
    reg(menus, wrap, close);
    menu.addEventListener('click', function (ev) { ev.stopPropagation(); });
    btn.addEventListener('click', function (ev) {
      ev.stopPropagation();
      var on = menu.hidden;
      closeAllMenus();
      if (on) { view = 'list'; draw(); menu.hidden = false; btn.setAttribute('aria-expanded', 'true'); btn.classList.add('is-open'); placeMenu(menu, btn, !!o.up, true); var f = menu.querySelector('button'); if (f) { f.focus({ preventScroll: true }); } }
    });
    menu.addEventListener('keydown', function (ev) {
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') { return; }
      var items = Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
      var i = items.indexOf(document.activeElement);
      if (i < 0 || !items.length) { return; }
      ev.preventDefault();
      items[(i + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    });
    function item(id, icon, title, sub, on) {
      return h('button', { type: 'button', role: 'menuitem', class: 'ux-smi', 'data-item': id, onclick: on },
        h('span', { class: 'ux-smi__ic' }, ic(icon, 18)), h('span', { class: 'ux-smi__b' }, h('b', { text: title }), h('span', { class: 'ux-smi__s', text: sub })));
    }
    function draw() {
      if (view === 'list') {
        put(menu,
          item('page', 'link', t(lang, 'sm.page'), t(lang, 'sm.pageS'), function () { view = 'page'; draw(); }),
          item('sub', 'smartphone', t(lang, 'sm.sub'), t(lang, 'sm.subS'), function () { view = 'sub'; draw(); }),
          item('give', 'qr-code', t(lang, 'sm.give'), t(lang, 'sm.giveS'), function () { close(); o.onGive(); }));
        if (!menu.hidden) { placeMenu(menu, btn, !!o.up, true); var f = menu.querySelector('button'); if (f) { f.focus({ preventScroll: true }); } }
        return;
      }
      var kind = view, my = ++seq;
      var backB = h('button', { type: 'button', class: 'ux-btn ux-btn--icon ux-btn--sm', 'aria-label': t(lang, 'c.back'), title: t(lang, 'c.back'), onclick: function () { view = 'list'; draw(); } }, ic('chevron-left', 16));
      var body = h('div', { class: 'ux-share__lv', 'aria-live': 'polite' }, h('div', { class: 'ux-share__load' }, spin('sm'), h('span', { text: t(lang, 'c.loading') })));
      put(menu, h('div', { class: 'ux-share__v' }, h('div', { class: 'ux-share__vh' }, backB,
        h('span', { class: 'ux-share__vt', text: kind === 'page' ? t(lang, 'sm.pageT', { name: o.name }) : t(lang, 'sm.sub') })), body));
      placeMenu(menu, btn, !!o.up, true);
      backB.focus({ preventScroll: true });
      (kind === 'page' ? o.getPage() : o.getSub()).then(function (p) { if (my === seq) { link(body, kind, p || {}); placeMenu(menu, btn, !!o.up, true); } },
        function (e) { if (my === seq) { put(body, h('div', { class: 'ux-errtx', text: errText(lang, e) })); } });
    }
    function link(body, kind, p) {
      var more = h('button', { type: 'button', class: 'ux-link', text: t(lang, 'sm.more'), onclick: function () { close(); o.more(kind); } });
      // подписка пустая (нет включённых устройств с VLESS, человек на паузе) — слова сервера, как в прежнем окне:
      // иначе приложение молча получит пустой список
      var emptyN = kind === 'sub' && p.empty_note ? h('div', { class: 'ux-warntx ux-small ux-share__empty', role: 'note', text: String(p.empty_note) }) : null;
      if (!p.enabled) {
        var en = h('button', { type: 'button', class: 'ux-btn ux-btn--primary ux-btn--sm', text: t(lang, kind === 'page' ? 'sm.makePage' : 'sm.makeSub') });
        en.addEventListener('click', function () {
          en.disabled = true;
          Promise.resolve(o.enable(kind)).then(function (r) { if (r && r.enabled !== undefined) { link(body, kind, r); } else { draw(); } }, function (e) { en.disabled = false; put(body, h('div', { class: 'ux-errtx', text: errText(lang, e) })); });
        });
        put(body, h('div', { class: 'ux-tx2 ux-small', text: t(lang, kind === 'page' ? 'sm.noPage' : 'sm.noSub') }), emptyN, h('div', { class: 'ux-row' }, en, more));
        return;
      }
      var url = p.url ? String(p.url) : '';
      if (!url || !/^https?:\/\//.test(url)) {
        put(body, h('div', { class: 'ux-tx2 ux-small', text: p.note ? String(p.note) : t(lang, 'sm.noUrl') }), emptyN, h('div', { class: 'ux-row' }, more));
        return;
      }
      var KSs = g.KaskadStream && g.KaskadStream.on ? g.KaskadStream : null, sv = KSs ? KSs.secret(url, { what: KSs.t(kind === 'page' ? 'stm.w.page' : 'stm.w.sub') }) : null;
      put(body, h('div', { class: 'ux-share__link' }, sv ? h('span', { class: 'ux-share__url ux-mono' }, sv.el) : h('span', { class: 'ux-share__url ux-mono', text: url, title: url }), sv ? sv.eye : null, copyBtn(lang, function () { return url; })),
        emptyN, h('div', { class: 'ux-tx2 ux-small', text: t(lang, kind === 'page' ? 'sm.pageHow' : 'sm.subHow') }), h('div', { class: 'ux-row' }, more));
    }
    return { el: wrap, close: close };
  }

  // ═════════════════════════════════════════════════════════════════════════
  // ExitsInput — ключи и подписки выходов, HANDOFF §1
  // ═════════════════════════════════════════════════════════════════════════
  // Тип по началу текста: awg (vpn://), vless, conf ([Interface]), sub (http/https), bad, empty
  function exitType(text) {
    var s = String(text || '').trim();
    if (!s) { return 'empty'; }
    if (/^vpn:\/\//i.test(s)) { return 'awg'; }
    if (/^vless:\/\//i.test(s)) { return 'vless'; }
    if (/^\[Interface\]/i.test(s) || /\n\s*\[Interface\]/i.test('\n' + s)) { return 'conf'; }
    if (/^https?:\/\/\S+$/i.test(s)) { return 'sub'; }
    return 'bad';
  }
  // Строка списка «Добавлено»: {title, meta, status: {tone, text}}
  function exitRowText(row, lang) {
    var st = row.status || 'ok';
    if (row.busy) {                                              // строку удаляют: знак занятости вместо состояния
      var bx = exitRowText(Object.assign({}, row, { busy: '' }), lang);
      return Object.assign(bx, { status: { tone: 'wait', text: String(row.busy) } });
    }
    if (row.kind === 'sub') {
      var stText = st === 'wait' ? t(lang, 'ei.subChecking', { n: row.keys || 0 }) : st === 'err' ? (row.statusText || t(lang, 'ei.subErr'))
        : st === 'old' ? (row.statusText || t(lang, 'ei.subOld')) : t(lang, row.auto === false ? 'ei.subManual' : 'ei.subAuto');
      return { title: row.name, meta: t(lang, 'ei.subMeta', { n: row.keys || 0 }), status: { tone: st === 'err' ? 'err' : st === 'old' ? 'warn' : st === 'wait' ? 'wait' : 'ok', text: stText } };
    }
    var TX = { ok: 'ei.keyOk', wait: 'ei.keyWait', build: 'ei.keyBuild', err: 'ei.keyErr' };
    return { title: row.name, meta: row.host || '', type: row.type || '',
      status: { tone: st === 'err' ? 'err' : st === 'wait' || st === 'build' ? 'wait' : 'ok', text: row.statusText || t(lang, TX[st] || 'ei.keyOk') } };
  }
  var TYPE_TEXT = { awg: 'ei.tAwg', vless: 'ei.tVless', conf: 'ei.tConf', sub: 'ei.tSub' };
  // «подписка goAmnezia Pro» — только по её адресу (/s-<43>/…, как kaskad.gw.exitsubs._GA_PATH); любая другая — «ссылка
  // подписки»: сервер принимает подписки любого источника
  var GA_SUB = /^https?:\/\/[^\/\s?#]+\/s-[A-Za-z0-9_-]{43}(?:\/[A-Za-z0-9._-]*)?(?:[?#]\S*)?$/i;
  function typeKey(type, text) { return type === 'sub' && !GA_SUB.test(String(text || '').trim()) ? 'ei.tSubAny' : TYPE_TEXT[type]; }

  // «Нет сервера за рубежом?» (OWNER-FEEDBACK №45): партнёрские хостинги — кнопками «имя · где его серверы ↗» и просьба
  // выбрать страну не Россию (выход в России бесполезен). Адреса — только из product.HOSTING (мастер — product.links.hosting,
  // панель — GET /api/hosting), здесь их нет; только https; rel без noreferrer — партнёрская метка доходит. Инструкция —
  // только настоящая (product.LINKS.exit_guide). Ни хостингов, ни инструкции — null (строку не показываем).
  function noServer(o) {
    var lang = o.lang || 'ru';
    var hosts = (o.hosting || []).filter(function (x) { return x && HTTPS.test(x.url || ''); });
    var guide = o.guide && HTTPS.test(o.guide.href || '') ? o.guide : null;
    if (!hosts.length && !guide) { return null; }
    return h('div', { class: 'ux-ns', 'data-ux': 'noserver' },
      h('p', {}, h('b', { text: t(lang, 'ns.q') }), ' ', hosts.length ? t(lang, 'ns.b') : t(lang, 'ns.guideB'),
        guide ? [' ', h('a', { href: guide.href, target: '_blank', rel: 'noopener', 'data-ux-k': 'guide', text: guide.text || t(lang, 'ns.guide') })] : null),
      hosts.length ? h('div', { class: 'ux-ns__hosts' }, hosts.map(function (x) {
        return h('a', { class: 'ux-btn ux-btn--sm ux-ns__host', href: x.url, target: '_blank', rel: 'noopener', 'data-ux-k': 'host-' + String(x.id || '') },
          h('span', { text: String(x.name || '') }), x.region ? h('span', { class: 'ux-ns__r', text: '· ' + String(x.region) }) : null, ' ↗');
      })) : null,
      hosts.length ? h('p', { class: 'ux-tx2', text: t(lang, 'ns.ru') }) : null);
  }
  // https://<IP> с сертификатом на домен (T-M6-qs330b №10, T-X6): сервер называет домен (data.cert_domain) и ведёт ли он
  // на тот же адрес (same_server) — тот же адрес подписки по домену, одной кнопкой. Адрес собирает страница из поля —
  // сервер секретную ссылку не возвращает.
  function byDomain(e, text) {
    var d = (e && e.data) || {};
    var dom = d.same_server === true && typeof d.cert_domain === 'string' ? d.cert_domain : '';
    if (!dom) { return null; }
    try { var u = new URL(String(text || '').trim()); u.hostname = dom; return { name: dom, url: u.toString() }; } catch (x) { return null; }
  }

  function exitsInput(o) {
    var lang = o.lang || 'ru', api = o.api;
    var S = { rows: o.rows || [], others: o.others || 0, text: '', file: '', type: 'empty', name: '', preview: null, adding: false, pending: null, err: '', fix: null, open: false, drag: false, t0: 0,
      hosting: o.hosting || [], guide: o.guide || null };
    var KEY_FILE_MAX = 64 * 1024, seq = 0, debounce = null, tick = null, dead = false;
    var area = h('textarea', { id: 'ux-ei-' + Math.random().toString(36).slice(2, 8), class: 'ux-area ux-area--exit', rows: '3', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off',
      placeholder: t(lang, 'ei.ph') });
    var pick = h('input', { type: 'file', accept: '.conf,.txt,.vpn,text/plain', class: 'ux-vh', tabindex: '-1', 'aria-hidden': 'true' });
    var state = h('div', { class: 'ux-ei__state', 'aria-live': 'polite' });
    var fileBtn = h('button', { type: 'button', class: 'ux-btn', onclick: function () { pick.click(); } }, ic('arrow-up-from-line', 16), h('span', { text: t(lang, 'ei.file') }));
    var addBtn = h('button', { type: 'button', class: 'ux-btn ux-btn--primary', text: t(lang, 'c.add') });
    var explain = h('div', { class: 'ux-ei__explain' });
    var whatLink = h('button', { type: 'button', class: 'ux-link', 'aria-expanded': 'false' });
    var banner = h('div', { class: 'ux-ei__banner' });     // слот плашки: пустой — не в раскладке (UI-1 F14)
    var list = h('div', { class: 'ux-ei__list' });
    var el = h('section', { class: 'ux-ei', 'data-ux': 'exits' },
      h('label', { class: 'ux-ei__label', for: area.id, text: t(lang, 'ei.label') }),
      h('div', { class: 'ux-ei__hint' }, t(lang, 'ei.hint1'), h('code', { text: 'vpn://' }), t(lang, 'ei.hint2'), h('code', { text: '.conf' }),
        t(lang, 'ei.hint3'), h('code', { text: 'vless://' }), t(lang, 'ei.hint4')),
      area, pick, h('div', { class: 'ux-ei__row' }, state, h('div', { class: 'ux-ei__btns' }, fileBtn, addBtn)),
      explain, banner, list);

    area.addEventListener('input', function () { S.file = ''; S.text = area.value; S.err = ''; S.fix = null; changed(); });
    area.addEventListener('dragover', function (ev) { ev.preventDefault(); if (!S.drag) { S.drag = true; area.classList.add('is-drop'); } });
    area.addEventListener('dragleave', function () { S.drag = false; area.classList.remove('is-drop'); });
    area.addEventListener('drop', function (ev) {
      ev.preventDefault(); S.drag = false; area.classList.remove('is-drop');
      readFile(ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0]);
    });
    pick.addEventListener('change', function () { readFile(pick.files && pick.files[0]); pick.value = ''; });
    addBtn.addEventListener('click', go);
    whatLink.addEventListener('click', function () { S.open = !S.open; paint(); });

    function readFile(f) {
      if (!f) { return; }
      if (f.size > KEY_FILE_MAX) { S.err = t(lang, 'ei.fileBig'); paint(); return; }
      var rd = new FileReader();
      rd.onload = function () {
        var s = String(rd.result || '');
        if (s.indexOf('\u0000') >= 0) { S.err = t(lang, 'ei.fileBin'); paint(); return; }
        area.value = s; S.text = s; S.err = ''; S.fix = null; changed();
      };
      rd.onerror = function () { S.err = t(lang, 'ei.fileBin'); paint(); };
      rd.readAsText(f);
    }
    function changed() {
      S.type = exitType(S.text); S.name = ''; S.preview = null; S.dupe = ''; S.dupeAct = null;
      clearTimeout(debounce);
      var my = ++seq, text = S.text;                            // поздний ответ разбора прежнего текста не нужен
      paint();
      if (S.type === 'awg' || S.type === 'vless' || S.type === 'conf') {
        debounce = setTimeout(function () {
          api.preview({ key: text }).then(function (r) {
            if (my !== seq || dead) { return; }
            S.preview = r || null;
            var items = (r && r.items) || [], it = items[0];
            S.name = it ? String(it.name || it.title || '') : '';
            // нечего добавлять: все ключи текста уже в пуле или с изъяном (T-M6-qs330c: тот же сервер VLESS) — сказать
            // сразу, «Добавить» закрыта
            var none = items.length && items.every(function (x) { return x && (x.in_pool || x.problem); });
            var bad = none ? items.filter(function (x) { return x.problem; })[0] : null;
            // 3.3.5 №4: тот же ключ другой строкой — «этот ключ уже есть как e-…, по другому адресу» (слова ядра)
            var same = none ? items.filter(function (x) { return x.same_text; })[0] : null;
            S.dupe = none ? (bad ? String(bad.problem) : (same ? String(same.same_text) : t(lang, 'ei.inPool'))) : '';
            // Н-2 REV-3315-fin: ядро помечает ключ с адресом в сети шлюза уже в предпросмотре (problem + suggest_base) —
            // у предупреждения та же кнопка «Сменить сеть шлюза на …», что у ошибки «Добавить» (после смены — recheck)
            S.dupeAct = bad && bad.suggest_base && o.errFix ? o.errFix({ data: { suggest_base: String(bad.suggest_base) } }) : null;
            paint();
          }, function () { /* разбор не удался — «Добавить» скажет причину */ });
        }, 350);
      } else if (S.type === 'sub' && api.checkSub) {
        // подписка уже добавлена (R-333i П3) — сказать сразу, «Добавить» закрыта; ничего не скачивается
        debounce = setTimeout(function () {
          api.checkSub({ url: text.trim() }).then(function (r) {
            if (my !== seq || dead) { return; }
            S.dupe = r && r.already ? String(r.text || t(lang, 'ei.subIn')) : '';
            paint();
          }, function () { /* проверка не удалась — «Добавить» скажет причину */ });
        }, 350);
      }
    }
    function go() {
      if (S.adding || S.type === 'empty' || S.type === 'bad' || S.dupe) { return; }
      var text = S.text.trim(), kind = S.type === 'sub' ? 'sub' : 'key';
      var build = !!(S.preview && S.preview.module_build);
      S.adding = true; S.err = ''; S.fix = null; S.t0 = Date.now();
      S.pending = { kind: kind, name: S.name || t(lang, typeKey(S.type, text)), status: build ? 'build' : 'wait', keys: 0 };
      paint();
      if (build) { tick = setInterval(paintBanner, 1000); }
      var p = kind === 'sub' ? api.addSub({ url: text, auto: true }) : api.addKey({ key: text });
      var sent = text;
      text = null;
      Promise.resolve(p).then(function (res) {
        S.adding = false; S.pending = null; clearInterval(tick);
        area.value = ''; S.text = ''; S.type = 'empty'; S.name = ''; S.preview = null;
        paint();
        if (o.onAdded) { o.onAdded(kind, res); }
      }, function (e) {
        S.adding = false; S.pending = null; clearInterval(tick);
        S.err = errText(lang, e);
        S.fix = kind === 'sub' ? byDomain(e, sent) : null;
        S.act = !S.fix && o.errFix ? o.errFix(e) : null;
        sent = null;
        paint();
        if (o.onError) { o.onError(e, go); }
      });
    }
    function paintBanner() {
      if (!S.pending || S.pending.status !== 'build') { clear(banner); return; }
      var sec = Math.floor((Date.now() - S.t0) / 1000);
      put(banner, h('div', { class: 'ux-build' }, spin('sm'), h('div', {},
        h('b', { text: t(lang, 'ei.buildT') }), ' · ', t(lang, 'ei.buildS', { name: S.pending.name }), ' ',
        h('span', { class: 'ux-mono', text: Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2) }))));
    }
    function paint() {
      if (dead) { return; }
      var ty = S.type;
      area.className = 'ux-area ux-area--exit' + (ty === 'bad' ? ' is-warn' : ty !== 'empty' ? ' is-ok' : '') + (S.drag ? ' is-drop' : '');
      area.disabled = S.adding;
      addBtn.disabled = S.adding || ty === 'empty' || ty === 'bad' || !!S.dupe || (!!S.err && !!S.act);
      addBtn.textContent = S.adding ? t(lang, 'c.adding') : t(lang, 'c.add');
      fileBtn.disabled = S.adding;
      if (S.err) {
        var fix = S.fix, ea = S.act;
        put(state, h('span', { class: 'ux-errtx', text: S.err }), fix ? h('button', { type: 'button', class: 'ux-btn ux-btn--sm', 'data-ux-k': 'by-domain', text: t(lang, 'ei.byDomain', { name: fix.name }),
          onclick: function () { area.value = fix.url; S.text = fix.url; S.type = exitType(fix.url); S.err = ''; S.fix = null; go(); } })
          : ea && ea.label && typeof ea.on === 'function' ? h('button', { type: 'button', class: 'ux-btn ux-btn--sm', 'data-ux-k': 'err-fix', text: String(ea.label), onclick: function () { ea.on(); } }) : null);
      }
      else if (ty === 'bad') { put(state, h('span', { class: 'ux-warntx', text: t(lang, 'ei.bad') })); }
      else if (S.dupe) {
        var da = S.dupeAct;
        put(state, h('span', { class: 'ux-warntx', text: S.dupe }), da && da.label && typeof da.on === 'function'
          ? h('button', { type: 'button', class: 'ux-btn ux-btn--sm', 'data-ux-k': 'err-fix', text: String(da.label), onclick: function () { da.on(); } }) : null);
      }
      else if (ty !== 'empty') {
        var what = t(lang, typeKey(ty, S.text)) + (S.name ? ' · ' + S.name : '');
        var firstAwg = S.preview && S.preview.module_build ? tri(t(lang, 'ei.firstAwg')) : null;
        put(state, h('span', { class: 'ux-okc' }, ic('check', 12)), h('span', {}, t(lang, 'ei.known'), ' ', h('b', { text: what })), firstAwg);
      } else { clear(state); }
      // пояснение про подписку: выходов нет — всегда; есть (подписки списком или ключи карточками хоста: o.others) —
      // ссылкой
      var rows = S.rows || [];
      var ex = h('div', { class: 'ux-ei__ex' },
        h('p', {}, h('b', { text: t(lang, 'ei.whatT') }), ' ', t(lang, 'ei.whatS')),
        h('p', { class: 'ux-ei__path' }, h('b', { text: t(lang, 'ei.whereT') }), ' ',
          [t(lang, 'ei.p1'), t(lang, 'ei.p2'), t(lang, 'ei.p3'), t(lang, 'ei.p4')].map(function (x, i) {
            return [i ? h('span', { class: 'ux-chev' }, ic('chevron-right', 14)) : null, h('span', { class: 'ux-pathchip', text: x })];
          })),
        noServer({ lang: lang, hosting: S.hosting, guide: S.guide }));
      if (!rows.length && !S.pending && !S.others) { put(explain, ex); }
      else {
        whatLink.textContent = S.open ? t(lang, 'ei.hide') : t(lang, 'ei.what');
        whatLink.setAttribute('aria-expanded', S.open ? 'true' : 'false');
        put(explain, whatLink, S.open ? ex : null);
      }
      paintBanner();
      // список
      var all = rows.slice();
      if (S.pending) { all.unshift(S.pending); }
      if (!all.length) { clear(list); return; }
      put(list, h('div', { class: 'ux-ei__lt', text: o.listLabel ? o.listLabel(rows.length) : t(lang, 'ei.added', { n: rows.length }) }), all.map(function (row) {
        var x = exitRowText(row, lang);
        var busy = row === S.pending || !!row.busy;
        // data-hold-row — ключ ряда: открытое меню «⋯» держит при живой перерисовке только свой ряд (app.js blkHold)
        return h('div', { class: 'ux-ei__item' + (busy ? ' is-busy' : ''), 'data-id': row.id || '', 'data-hold-row': row.id && row !== S.pending ? 'ei:' + row.id : null },
          h('span', { class: 'ux-ei__ic' }, ic(row.kind === 'sub' ? 'arrow-rotate-right' : 'key', 18)),
          h('div', { class: 'ux-ei__b' },
            h('div', { class: 'ux-ei__t' }, h('b', { text: x.title }), x.type ? h('span', { class: 'ux-tx2', text: ' · ' + x.type }) : null, ' · ',
              h('span', { class: 'ux-st ux-st--' + x.status.tone }, x.status.tone === 'wait' ? spin('sm') : null, h('span', { text: x.status.text })),
              row.retry ? h('button', { type: 'button', class: 'ux-link', text: t(lang, 'ei.retry'), onclick: row.retry }) : null),
            x.meta ? h('div', { class: row.kind === 'sub' ? 'ux-ei__m' : 'ux-ei__m ux-mono', text: x.meta, title: x.meta }) : null),
          (row.extra || []).filter(Boolean),
          busy ? null : h('button', { type: 'button', class: 'ux-x', 'aria-label': t(lang, 'ei.del', { name: x.title }), title: t(lang, 'ei.del', { name: x.title }),
            onclick: function () { if (row.onDel) { row.onDel(); } } }, ic('xmark', 16)));
      }));
    }
    paint();
    // set(rows, others): others — сколько ещё выходов у хоста вне списка (ключи карточками); без числа — прежнее
    return { el: el, set: function (rows, others) { S.rows = rows || []; if (typeof others === 'number') { S.others = others; } paint(); },
      setHosting: function (hosting, guide) { S.hosting = hosting || []; S.guide = guide || null; paint(); },
      // З-1 REV-3315-fin: после «Сменить сеть шлюза» → «Применить» прежняя ошибка и её кнопка ложны (вторая звала бы к
      // «Вернуть прежнюю сеть»): снять их и разобрать тот же текст заново
      recheck: function () { if (dead || S.adding) { return; } S.err = ''; S.fix = null; S.act = null; changed(); },
      destroy: function () { dead = true; clearTimeout(debounce); clearInterval(tick); area.value = ''; S.text = ''; } };
  }

  g.KaskadUX = {
    t: t, agentKey: agentKey, telegramBot: telegramBot, protocolCard: protocolCard, foldGrid: foldGrid, descLast: descLast, logDrawer: logDrawer, shareMenu: shareMenu,
    exitsInput: exitsInput, noServer: noServer, closeMenus: closeAllMenus,
    agentState: agentState, agentPending: agentPending, pubCheck: pubCheck, agentCommands: agentCommands, clientName: clientName, keygen: KEYGEN,
    tokenOk: tokenOk, botChip: botChip, tokState: tokState, protoModel: protoModel, descParts: descParts, exitType: exitType, exitRowText: exitRowText, exitTypeKey: typeKey
  };
})(typeof window !== 'undefined' ? window : this);
