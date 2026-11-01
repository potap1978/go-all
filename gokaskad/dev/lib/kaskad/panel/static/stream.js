/* Kaskad — «Режим стрима» (handoff/briefs/PLAN-stream-mode-2026-10-29.md §2; вид — Claude Design
   kaskad-stream/export-2026-10-29-r1, раздел HANDOFF «## stream · Режим стрима r1», стили — stream.css).

   Две защиты в одном модуле:
     1. Поле-секрет (secret, field, qr, link): значения нет в DOM, пока его не показали; «Скопировать» берёт его из
        замыкания. Ставится в общие помощники панели (U.kv, U.code, U.input, hostName, give.js, ux.js, graph.js).
     2. Сетка — страховка без показа: пока режим включён, MutationObserver заменяет в тексте и атрибутах известные
        чувствительные значения (реестр: note() и harvest() ответов API) и узнаваемые по виду (ключи vless://…,
        публичные IP, токены, /p/…). Колбэк наблюдателя — микрозадача, она идёт до отрисовки кадра: мигания нет.
        Поля ввода, в которых оказалось такое значение (вставили ключ, адрес), закрываются точками (ks-sectext).

   Флаг — только в этом браузере: localStorage "kaskad.stream" = "1" (режим касается экрана, с которого идёт стрим;
   сервер, API и данные не меняются). Приватное окно — в памяти до перезагрузки. boot.js ставит <html data-stream="on">
   до первого кадра. Другая вкладка той же панели включила или выключила — событие storage, вкладки идут вместе.
   Тексты и значки — из app.js (bind): stream.js грузится раньше словарей и app.js, чтобы сетка уже стояла, когда
   панель рисует первые данные. Мастер установки и страница /p/ этот файл не грузят — там режима нет (вопрос №6).

   API (window.KaskadStream; в node — module.exports, тест tests/panel/test_stream.mjs):
     on, names, active, MASK, _ttl        — режим, «И имена», наблюдатель стоит, маска, время показа (мс)
     set(on), setNames(on), onChange(fn)  — переключить (вызывает fn(KS)), подписка на смену
     note(v, kind), harvest(json), safe(v), alias(name, label), face(name), forget()
     scrub(text, o), isPublicIp(s), looks(text)
     bind({t, tn, icon}), secret(value, o), field(input, o), qr(o), link(a, url), track(entry), hideRevealed() */
(function (g) {
  "use strict";

  var doc = g.document || null;
  var KEY = "kaskad.stream", KEY_NAMES = "kaskad.stream.names";
  var MASK = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";   // всегда 8 точек: длина маски не выдаёт длину значения

  // ── Флаг: localStorage, в приватном окне — память до перезагрузки ──
  var mem = {};
  function lsGet(k) {
    var v = null;
    try { v = g.localStorage ? g.localStorage.getItem(k) : null; } catch (e) { v = null; }
    return v === null && Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : v;
  }
  function lsSet(k, v) {
    mem[k] = v;
    try { if (g.localStorage) { if (v === null) g.localStorage.removeItem(k); else g.localStorage.setItem(k, v); } } catch (e) { /* приватное окно */ }
  }

  // ── Адреса: публичный IPv4/IPv6 (частные, служебные, документация и точки проверки — не прячем) ──
  // Точки проверки сервера и DNS — общеизвестные, не наши: «Точка 8.8.8.8 теряет 40%» остаётся читаемой
  var PROBES = { "1.1.1.1": 1, "1.0.0.1": 1, "8.8.8.8": 1, "8.8.4.4": 1, "9.9.9.9": 1, "149.112.112.112": 1, "77.88.8.8": 1,
    "77.88.8.1": 1, "208.67.222.222": 1, "94.140.14.14": 1 };
  function ip4(s) {
    var m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(s));
    if (!m) return null;
    var o = [+m[1], +m[2], +m[3], +m[4]];
    for (var i = 0; i < 4; i++) { if (o[i] > 255 || (m[i + 1].length > 1 && m[i + 1].charAt(0) === "0")) return null; }
    return o;
  }
  function isPublic4(o) {
    var a = o[0], b = o[1], c = o[2];
    if (a === 0 || a === 10 || a === 127 || a >= 224) return false;               // своя сеть, частная, петля, мультикаст и выше
    if (a === 100 && b >= 64 && b <= 127) return false;                            // CGNAT 100.64/10
    if (a === 169 && b === 254) return false;                                      // link-local
    if (a === 172 && b >= 16 && b <= 31) return false;
    if (a === 192 && b === 168) return false;
    if (a === 192 && b === 0 && (c === 0 || c === 2)) return false;                // служебная, TEST-NET-1
    if (a === 198 && (b === 18 || b === 19)) return false;                         // замеры
    if (a === 198 && b === 51 && c === 100) return false;                          // TEST-NET-2
    if (a === 203 && b === 0 && c === 113) return false;                           // TEST-NET-3
    return true;
  }
  function isPublic6(s) {
    var v = String(s).toLowerCase();
    if (!/^[23][0-9a-f]{3}:/.test(v)) return false;                                 // глобальные — 2000::/3
    if (/^2001:0?db8:/.test(v)) return false;                                       // документация
    return true;
  }
  function isPublicIp(s) {
    var v = String(s || "").trim().replace(/^\[|\]$/g, "");
    if (PROBES[v]) return false;
    var o = ip4(v);
    if (o) return isPublic4(o);
    return v.indexOf(":") > 0 ? isPublic6(v) : false;
  }
  // частный — не виден снаружи (10/8, 172.16/12, 192.168/16, 100.64/10, петля, link-local): в реестр не берём. Адреса
  // документации (TEST-NET) — не частные: в моке они стоят на месте настоящих и попадают в реестр, как настоящие
  function isPrivateIp(s) {
    var o = ip4(s);
    if (!o) return false;
    var a = o[0], b = o[1];
    return a === 0 || a === 10 || a === 127 || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  }

  // ── Реестр известных значений ──
  var known = Object.create(null);     // значение в нижнем регистре → вид (host, login, url, fp…)
  var aliases = Object.create(null);   // имя в нижнем регистре → «Пользователь 1» (галочка «И имена»)
  var safeSet = Object.create(null);   // строки, которые не трогаем никогда (версия панели похожа на IPv4)
  var reKnown = null, reDirty = true;
  var WORD = /[\p{L}\p{N}_]/u;
  function hostOf(s) {
    var m = /^[a-z][a-z0-9+.-]*:\/\/(?:[^@/?#]*@)?(\[[^\]]+\]|[^:/?#]+)/i.exec(s);
    return m ? m[1].replace(/^\[|\]$/g, "") : "";
  }
  function add(map, s, val) {
    var k = s.toLowerCase();
    if (map[k] === val) return;
    map[k] = val; reDirty = true;
  }
  // note(v, kind): v — строка или массив. Короче 4 знаков, чистые числа короче 6 (порты), частные IP и маска — нет.
  // URL — ещё и его хост: адрес сервера в любой фразе прячется и без схемы
  function note(v, kind) {
    if (v === null || v === undefined || v === "") return;
    if (Array.isArray(v)) { for (var i = 0; i < v.length; i++) note(v[i], kind); return; }
    if (typeof v === "object") return;
    var s = String(v).trim();
    if (s.length < 4 || s.indexOf(MASK) >= 0 || safeSet[s.toLowerCase()]) return;
    if (/^\d+$/.test(s) && s.length < 6) return;
    if (!/[\p{L}\p{N}]/u.test(s)) return;
    if (isPrivateIp(s) || PROBES[s]) return;
    var h = hostOf(s);
    if (h) {
      note(h, "host");
      if (s.replace(/\/+$/, "") === s.slice(0, s.indexOf(h) + h.length)) return;   // «https://хост/» — хватит хоста
    }
    var hp = /^([^\s:/]+):(\d{1,5})$/.exec(s);                                        // «хост:порт»
    if (hp) note(hp[1], "host");
    add(known, s, kind || "x");
  }
  function safe(v) { var s = String(v || "").trim(); if (s) { safeSet[s.toLowerCase()] = 1; delete known[s.toLowerCase()]; reDirty = true; } }
  function alias(name, label) { var s = String(name || "").trim(); if (s.length >= 2 && label) add(aliases, s, String(label)); }
  // face(name): от чего строить производные имени — инициалы и цвет аватара (Н-4 стенда 3.3.7.13). Сетка меняет текст
  // имени целиком, а две буквы в кружке и оттенок от настоящего имени узнаются: с «И имена» — подпись-замена
  // «Пользователь N»; имя, которого реестр не знает, — "" (нейтральный кружок «?»); без галочки — само имя, как было
  function face(name) {
    if (!KS.on || !KS.names) return name;
    var s = String(name === null || name === undefined ? "" : name).trim();
    return (s && aliases[s.toLowerCase()]) || "";
  }
  function forget() { known = Object.create(null); aliases = Object.create(null); reDirty = true; }

  // harvest(json): ключи ответа, под которыми лежат адреса, логины, отпечатки и личное (один обход на ответ API, в
  // одном месте — а не в каждом разборе). Адрес-ссылку берём целиком только свою: /p/, login?t=, подписка, хост-IP
  // или уже известный хост; ссылки магазинов приложений — нет (их href нужен ссылке)
  var HKEYS = { host: "host", hosts: "host", cert_host: "host", domain: "host", domain_unicode: "host", ip: "host", ips_public: "host",
    addr: "addr", address: "addr", here: "host", old: "host", endpoint: "host", server: "host", a: "host", aaaa: "host", listen: "addr",
    login: "login", username: "bot", bot_username: "bot", chat_id: "bot", chat: "bot", email: "mail", fingerprint: "fp", pin: "fp",
    pin_sha256: "fp", password: "secret", psk: "secret", token: "secret", sub_url: "url", page_url: "url", url: "url", link: "url",
    page: "url", sub: "url", full: "url", panel_url: "url" };
  var ARR_ONLY = { a: 1, aaaa: 1 };                                    // записи DNS сайта — только списком
  // «Адреса сервера», «Приём подключений» и «IP выхода» (3.3.7.13, PLAN-multi-ip): адреса машины в ответах и отказах —
  // known (отказ address_not_here), ips и in (приём), src (адрес выхода), show, client, local, pub. Ключи общие (src
  // бывает словами, ips — адресами устройств в туннеле), поэтому под ними берём только публичный IP. Адрес документации
  // (TEST-NET) — как публичный: в моке он стоит на месте настоящего (см. isPrivateIp)
  // 3.3.7.15 (F10, F11): IPv6 «Адресов» — in6 (приём по IPv6), echo6 (как адрес виден снаружи), suggest и new (смена
  // префикса IPv6 в ленте); addresses6[].ip и give.items[].ip — под ключом ip (HKEYS). F13: give6 (IPv6 строкой
  // подписки — ответ PUT …/give; в mip.give6 — объект, его ip — под ключом ip), seen_as и seen (как адрес виден снаружи:
  // строка страницы и отказ echo_other). F12а: direct и out («напрямую», «для выхода»), route (адрес маршрута в отказе),
  // primary (адрес маршрута в документе «Адресов» и mip), kernel и xray (Н4: два адреса «напрямую»)
  var IPKEYS = { known: 1, ips: 1, "in": 1, src: 1, xsrc: 1, show: 1, client: 1, local: 1, pub: 1, want: 1, gone: 1,
    in6: 1, echo6: 1, suggest: 1, "new": 1, give6: 1, seen_as: 1, seen: 1, ip6: 1, direct: 1, out: 1, route: 1,
    primary: 1, kernel: 1, xray: 1 };
  function ipOnly(v) {
    if (typeof v !== "string" && typeof v !== "number") return;
    var s = String(v).trim().replace(/^\[|\]$/g, ""), o = ip4(s);
    if (o ? isPublic4(o) || /^(192\.0\.2|198\.51\.100|203\.0\.113)\./.test(s) : s.indexOf(":") > 0 && isPublic6(s)) note(s, "host");
  }
  function ownUrl(s) {
    if (!/^https?:\/\//i.test(s)) return false;
    if (/\/p\/|login\?t=|\/sub\b/i.test(s)) return true;
    var h = hostOf(s);
    return !!h && (!!ip4(h) || h.indexOf(":") >= 0 || !!known[h.toLowerCase()]);
  }
  function harvest(j, depth) {
    depth = depth || 0;
    if (!j || typeof j !== "object" || depth > 8) return;
    if (Array.isArray(j)) { for (var i = 0; i < j.length && i < 2000; i++) harvest(j[i], depth + 1); return; }
    for (var k in j) {
      if (!Object.prototype.hasOwnProperty.call(j, k)) continue;
      var v = j[k], kind = HKEYS[k];
      if (!kind && IPKEYS[k] && !(v && typeof v === "object" && !Array.isArray(v))) {
        if (Array.isArray(v)) { for (var y = 0; y < v.length && y < 2000; y++) { if (v[y] && typeof v[y] === "object") harvest(v[y], depth + 1); else ipOnly(v[y]); } }
        else ipOnly(v);
        continue;
      }
      if (kind && ARR_ONLY[k] && !Array.isArray(v)) { if (v && typeof v === "object") harvest(v, depth + 1); continue; }
      if (kind && (typeof v === "string" || typeof v === "number")) {
        var s = String(v);
        if (kind === "url") { if (ownUrl(s)) note(s, "url"); else if (/^https?:\/\//i.test(s) && ip4(hostOf(s))) note(hostOf(s), "host"); }
        else if (kind === "host" && /^https?:\/\//i.test(s) && !ownUrl(s)) { /* чужой сайт ссылкой — не наш адрес */ }
        else note(s, kind);
      } else if (kind && Array.isArray(v)) {
        for (var x = 0; x < v.length; x++) { if (typeof v[x] === "string") note(v[x], kind); else harvest(v[x], depth + 1); }
      } else if (v && typeof v === "object") harvest(v, depth + 1);
    }
  }

  // ── Сетка: scrub(text) ──
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"); }
  function buildRe() {
    var list = Object.keys(known).concat(KS.names ? Object.keys(aliases) : []).sort(function (a, b) { return b.length - a.length; });
    reKnown = list.length ? new RegExp(list.map(esc).join("|"), "giu") : null;
    reDirty = false;
    // реестр пополнился — то, что сетка уже правила, проходит её снова: «Выход 203.0.113.x через домен» получил маску на
    // IP раньше, чем домен попал в реестр, и иначе остался бы с доменом (REV-stream С-1)
    if (wrote) wrote = new WeakMap();
  }
  function edgeOk(str, i, j) {
    var a = str.charAt(i), b = str.charAt(j - 1);
    if (WORD.test(a) && i > 0 && WORD.test(str.charAt(i - 1))) return false;
    if (WORD.test(b) && j < str.length && WORD.test(str.charAt(j))) return false;
    return true;
  }
  var VER_CTX = /(?:\bv|верси[яиюейё]?|version|выпуск[а-я]*|release|обновлени[а-я]*|update[ds]?|kaskad)\s*:?\s*$/i;
  var P_KEY = /\b(?:vless|vmess|trojan|ssr?|hy2|hysteria2?|tuic|vpn|wireguard|awg|wg|socks5?|amnezia|anytls|naive\+https)\:\/\/[A-Za-z0-9][^\s"'<>]{7,}/gi;   // пример «vless://…» в подсказке — не ключ
  var P_PAGE = /\bhttps?:\/\/[^\s"'<>]*?\/p\/[^\s"'<>]*/gi;
  var P_PREL = /(^|[^\w/.:])(\/p\/[A-Za-z0-9_-]{8,}[^\s"'<>]*)/g;
  var P_LOGIN = /(?:\bhttps?:\/\/[^\s"'<>]*)?\blogin\?t=[^\s"'<>]+/gi;
  var P_BOT = /\b\d{6,12}:[A-Za-z0-9_-]{30,}/g;
  var P_GH = /\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})/g;
  var P_WG = /\b((?:PrivateKey|PresharedKey)\s*=\s*)[^\s"'<>]+/g;
  var P_B64 = /(^|[^A-Za-z0-9+/=])([A-Za-z0-9+/]{43}=)(?![A-Za-z0-9+/=])/g;
  var P_IP4 = /(^|[^\d.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d)/g;
  var P_IP6 = /(^|[^0-9A-Fa-f:.])([23][0-9A-Fa-f]{3}(?::[0-9A-Fa-f]{0,4}){2,7})(?![0-9A-Fa-f:])/g;
  // o.known — только реестр (подсказка-пример в поле «203.0.113.5» — не наш адрес)
  function scrub(text, o) {
    var s = String(text === null || text === undefined ? "" : text);
    // короче 4 знаков реестра и образцов нет, а имя бывает и в 2–3 буквы («Ян», «Оля»): с «И имена» — от 2 (Н-4)
    if (!s || s.length < (KS.names ? 2 : 4)) return s;
    if (reDirty) buildRe();
    if (reKnown) {
      reKnown.lastIndex = 0;
      s = s.replace(reKnown, function (m, at, str) {
        if (safeSet[m.toLowerCase()] || !edgeOk(str, at, at + m.length)) return m;
        var al = KS.names ? aliases[m.toLowerCase()] : undefined;
        return al !== undefined && !known[m.toLowerCase()] ? al : MASK;
      });
    }
    if (o && o.known) return s;
    s = s.replace(P_KEY, MASK).replace(P_PAGE, MASK).replace(P_PREL, function (m, a) { return a + MASK; })
      .replace(P_LOGIN, MASK).replace(P_BOT, MASK).replace(P_GH, MASK)
      .replace(P_WG, function (m, a) { return a + MASK; })
      .replace(P_B64, function (m, a) { return a + MASK; });
    s = s.replace(P_IP4, function (m, a, ip, at, str) {
      if (!isPublicIp(ip) || safeSet[ip]) return m;
      var before = str.slice(Math.max(0, at + a.length - 24), at + a.length), after = str.charAt(at + m.length);
      if (VER_CTX.test(before) || (after === "-" && /^-[0-9a-z]/i.test(str.slice(at + m.length, at + m.length + 2)))) return m;
      // «3.3.7.12 → 3.3.7.13»: цепочка версий (слово «версия» стоит только перед последней)
      if (/^\s*(?:→|->)\s*\d\.\d+\.\d+/.test(str.slice(at + m.length)) || /\d\.\d+\.\d+(?:\.\d+)?\s*(?:→|->)\s*$/.test(before)) return m;
      return a + MASK;
    });
    s = s.replace(P_IP6, function (m, a, ip) { return isPublicIp(ip) ? a + MASK : m; });
    return s;
  }
  function looks(text) { var s = String(text || ""); return !!s && scrub(s) !== s; }

  // ── Наблюдатель ──
  var ATTRS = ["title", "aria-label", "aria-description", "placeholder", "alt", "href", "value", "data-tip"];
  var mo = null, wrote = typeof WeakMap === "function" ? new WeakMap() : null;
  var FIELD = /^(INPUT|TEXTAREA)$/;
  function shownIn(n) {
    var e = n && n.nodeType === 1 ? n : (n ? n.parentNode : null);
    return !!(e && e.closest && e.closest("[data-sec-shown]"));
  }
  function fixText(n) {
    if (!KS.on || shownIn(n)) return;
    var v = n.data;
    if (reDirty) buildRe();                                      // сначала свежий реестр (он сбрасывает wrote), потом пропуск
    if (wrote && wrote.get(n) === v) return;                     // наш же текст: сетку не гоняем по кругу
    var s = scrub(v);
    if (s !== v) { if (wrote) wrote.set(n, s); n.data = s; }
  }
  function fixAttr(e, a) {
    if (!KS.on || (a === "value" && e.tagName === "OPTION")) return;   // значение пункта списка не на экране, а форме нужно
    var v = e.getAttribute(a);
    if (!v || shownIn(e)) return;
    var s = scrub(v, a === "placeholder" ? { known: true } : null);
    if (s !== v) e.setAttribute(a, s);
  }
  function plainField(e) { return e.tagName === "TEXTAREA" || !/^(password|hidden|checkbox|radio|range|color|file|button|submit)$/i.test(e.type || "text"); }
  function fixField(e) {
    if (!KS.on || !plainField(e) || e.hasAttribute("data-sec-open")) return;
    // classList.add и с тем же классом пишет атрибут — новая запись наблюдателю; без проверки — вечный круг
    if (e.value && !e.classList.contains("ks-sectext") && looks(e.value)) e.classList.add("ks-sectext");
  }
  function fixEl(e) {
    for (var i = 0; i < ATTRS.length; i++) if (e.hasAttribute(ATTRS[i])) fixAttr(e, ATTRS[i]);
    if (FIELD.test(e.tagName)) fixField(e);
  }
  function walk(root) {
    if (!root || !root.isConnected) return;
    if (root.nodeType === 3) { fixText(root); return; }
    if (root.nodeType !== 1 || shownIn(root)) return;
    fixEl(root);
    var tw = doc.createTreeWalker(root, 5 /* SHOW_ELEMENT | SHOW_TEXT */, { acceptNode: function (n) {
      return n.nodeType === 1 && n.hasAttribute("data-sec-shown") ? 2 /* FILTER_REJECT */ : 1;
    } });
    var n;
    while ((n = tw.nextNode())) { if (n.nodeType === 3) fixText(n); else fixEl(n); }
  }
  function onMut(list) {
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.type === "characterData") { if (m.target.isConnected) fixText(m.target); }
      else if (m.type === "attributes") {
        if (!m.target.isConnected) continue;
        if (m.attributeName === "class") { if (FIELD.test(m.target.tagName)) fixField(m.target); }   // перерисовка сменила класс поля — точки снова
        else fixAttr(m.target, m.attributeName);
      }
      else for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
    }
  }
  function onInput(ev) { var e = ev.target; if (e && FIELD.test(e.tagName || "")) fixField(e); }
  function start() {
    if (mo || !doc || typeof g.MutationObserver !== "function") return;
    mo = new g.MutationObserver(onMut);
    mo.observe(doc.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat(["class"]) });
    doc.addEventListener("input", onInput, true);
    doc.addEventListener("change", onInput, true);
    KS.active = true;
    walk(doc.documentElement);
  }
  function stop() {
    if (mo) { mo.disconnect(); mo = null; }
    if (doc) { doc.removeEventListener("input", onInput, true); doc.removeEventListener("change", onInput, true); }
    KS.active = false;
  }
  // реестр пополнился (пришёл ответ API) — то, что уже на экране, проходит сетку ещё раз
  var resweep = 0;
  function sweepSoon() {
    if (!mo || resweep) return;
    resweep = 1;
    Promise.resolve().then(function () { resweep = 0; if (mo && doc) walk(doc.documentElement); });
  }

  // ── Тексты и значки — из app.js ──
  var B = { t: function (k) { return k; }, tn: null, icon: null };
  function T(k, vars) { try { return B.t(k, vars); } catch (e) { return k; } }
  function mk(tag, cls, text) { var n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function ico(name, px) {
    if (B.icon) return B.icon(name, px);
    var w = mk("span", "gi"); w.setAttribute("aria-hidden", "true");
    var s = g.KaskadIcons ? g.KaskadIcons.svg(name, px || 16) : null;
    if (s) w.appendChild(s);
    return w;
  }
  var reduced = function () { try { return g.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };

  // ── Показанное: всё, что сейчас открыто (поля, QR, ввод); «Скрыть показанное», смена раздела, уход со вкладки ──
  var LIVE = [];
  function track(entry) { if (LIVE.indexOf(entry) < 0) LIVE.push(entry); return function () { untrack(entry); }; }
  function untrack(entry) { var i = LIVE.indexOf(entry); if (i >= 0) LIVE.splice(i, 1); }
  function hideRevealed() { LIVE.slice().forEach(function (e) { try { e.hide(); } catch (x) { /* узел уже убран */ } }); LIVE.length = 0; }

  // Таймер показа: ttl мс, «ещё N с» и полоса отсчёта (role=timer); при prefers-reduced-motion — только текст
  function countdown(left, cd, bar, done) {
    var t0 = Date.now(), ttl = KS._ttl, id = null;
    function tick() {
      var ms = ttl - (Date.now() - t0);
      if (ms <= 0 || (left && !left.isConnected)) { stopIt(); done(); return; }
      var txt = T("stm.left", { n: Math.ceil(ms / 1000) });
      if (left) left.textContent = txt;
      if (cd) { cd.setAttribute("aria-label", txt); cd.hidden = reduced(); }
      if (bar && bar.style) bar.style.setProperty("--p", String(Math.max(0, ms / ttl).toFixed(3)));
    }
    function stopIt() { if (id) { clearInterval(id); id = null; } }
    tick();
    id = setInterval(tick, Math.min(250, Math.max(50, ttl / 8)));
    return stopIt;
  }

  // secret(value, o) → {el, eye, show, hide}: маска «••••••••» на месте значения и глаз. Значение — в замыкании.
  // o.what — что это словами (для читалки: «Скрыто: адрес сервера. Показать»); o.eyeCls — класс глаза (по умолчанию
  // gx-kv__copy — та же кнопка 32, что «Скопировать»); o.size — значок глаза
  function secret(value, o) {
    o = o || {};
    var v = String(value === null || value === undefined ? "" : value), what = o.what || T("stm.what");
    var box = mk("span", "ks-sv" + (o.cls ? " " + o.cls : "")), line = mk("span", "ks-sv__line");
    var mask = mk("span", "ks-mask", MASK); mask.setAttribute("aria-hidden", "true");
    var val = mk("span", "ks-sv__v" + (o.pre ? " ks-sv__v--pre" : "")), left = mk("span", "ks-sv__left");
    var cd = mk("span", "ks-cd"), bar = mk("i");
    cd.setAttribute("role", "timer"); cd.appendChild(bar);
    val.hidden = true; left.hidden = true; cd.hidden = true;
    line.appendChild(mask); line.appendChild(val); line.appendChild(left);
    box.appendChild(line); box.appendChild(cd);
    var eye = mk("button", o.eyeCls || "gx-kv__copy ks-eye");
    eye.type = "button";
    var shown = false, stopT = null, entry = null;
    function paint() {
      eye.setAttribute("aria-pressed", shown ? "true" : "false");
      eye.setAttribute("aria-label", T(shown ? "stm.hide" : "stm.hidden", { what: what }));
      eye.setAttribute("title", shown ? T("stm.hide", { what: what }) : T("stm.showTip"));
      while (eye.firstChild) eye.removeChild(eye.firstChild);
      eye.appendChild(ico(shown ? "eye-slash" : "eye", o.size || 14));
    }
    function show() {
      if (shown || !v) return;
      shown = true;
      box.setAttribute("data-sec-shown", "");                    // сначала метка — потом значение: сетка его не трогает
      val.textContent = v; val.hidden = false; mask.hidden = true; left.hidden = false;
      paint();
      entry = { hide: hide };
      track(entry);
      stopT = countdown(left, cd, bar, hide);
    }
    function hide() {
      if (!shown) return;
      shown = false;
      if (stopT) { stopT(); stopT = null; }
      if (entry) { untrack(entry); entry = null; }
      val.textContent = ""; val.hidden = true; mask.hidden = false; left.hidden = true; left.textContent = ""; cd.hidden = true;
      box.removeAttribute("data-sec-shown");
      paint();
    }
    eye.addEventListener("click", function (ev) { ev.stopPropagation(); if (shown) hide(); else show(); });
    mask.addEventListener("click", function (ev) { ev.stopPropagation(); show(); });
    paint();
    return { el: box, eye: eye, show: show, hide: hide, shown: function () { return shown; } };
  }

  // field(input, o) → глаз (кнопка) для поля ввода в режиме стрима. Точки — через -webkit-text-security (класс ks-sectext),
  // а не type=password: менеджер паролей не подставит в «пароль» сохранённый пароль панели и не предложит его сохранить;
  // type=password — только где свойства нет. <textarea> — ещё строка «Вставлено N знаков · Показать». Выключен — null.
  var TS = (function () { try { return !!(g.CSS && g.CSS.supports && g.CSS.supports("-webkit-text-security", "disc")); } catch (e) { return false; } })();
  function field(input, o) {
    o = o || {};
    if (!KS.on || !input) return null;
    var area = input.tagName === "TEXTAREA", dots = area || TS, what = o.what || T("stm.what");
    var open = false, stopT = null, entry = null;
    var eye = mk("button", o.eyeCls || "ga-btn ga-btn--tertiary ga-btn--icon ks-eye");
    eye.type = "button";
    var note = area ? mk("div", "ks-paste") : null, noteN = note ? mk("b") : null, noteT = note ? mk("span") : null;
    if (note) { note.appendChild(noteN); note.appendChild(noteT); note.appendChild(eye); eye.className = "ga-btn gx-link ks-eye"; }
    function paint() {
      eye.setAttribute("aria-pressed", open ? "true" : "false");
      eye.setAttribute("aria-label", T(open ? "stm.hide" : "stm.hidden", { what: what }));
      if (!area) eye.setAttribute("title", open ? T("stm.hide", { what: what }) : T("stm.showTip"));
      while (eye.firstChild) eye.removeChild(eye.firstChild);
      if (area) { eye.appendChild(doc.createTextNode(T(open ? "stm.hideW" : "stm.showW"))); }
      else eye.appendChild(ico(open ? "eye-slash" : "eye", 16));
      if (note) {
        var n = Array.from(input.value || "").length;
        note.hidden = !n;
        noteN.textContent = B.tn ? B.tn("stm.pasted", n) : T("stm.pasted", { n: n });
        noteT.textContent = " · ";
      }
    }
    function hide() {
      open = false;
      if (stopT) { stopT(); stopT = null; }
      if (entry) { untrack(entry); entry = null; }
      if (dots) { input.classList.add("ks-sectext"); input.removeAttribute("data-sec-open"); } else input.type = "password";
      paint();
    }
    function show() {
      open = true;
      if (dots) { input.setAttribute("data-sec-open", ""); input.classList.remove("ks-sectext"); } else input.type = "text";
      entry = { hide: hide }; track(entry);
      stopT = countdown(null, null, null, hide);
      paint();
    }
    if (dots) input.classList.add("ks-sectext"); else input.type = "password";
    input.setAttribute("autocomplete", "off");
    input.addEventListener("input", function () { if (note) paint(); });
    eye.addEventListener("click", function (ev) { ev.preventDefault(); ev.stopPropagation(); if (open) hide(); else show(); });
    paint();
    return area ? note : eye;
  }

  // qr(o) → узел: в режиме стрима — шторка «QR скрыт» и «Показать QR»; o.draw() вызывается только после нажатия (кода в
  // DOM нет вовсе, не размытие); «Скрыть QR» убирает. Выключен — сразу o.draw(). o.open — QR уже попросили (кнопка
  // «QR-код» окна): сразу код и «Скрыть QR». o.onShow/o.onHide — хозяину
  function qr(o) {
    o = o || {};
    if (!KS.on) return o.draw();
    var box = mk("div", "ks-qrbox" + (o.cls ? " " + o.cls : ""));
    var entry = { hide: function () { cover(); } };
    function cover() {
      untrack(entry);
      while (box.firstChild) box.removeChild(box.firstChild);
      box.appendChild(qrCover(function () { open(); }, o));
      if (o.onHide) o.onHide();
    }
    function open() {
      while (box.firstChild) box.removeChild(box.firstChild);
      var x = o.draw();
      if (x) box.appendChild(x);
      var b = mk("button", "ga-btn ga-btn--ghost ga-btn--sm ga-btn--wi ks-qrhide");
      b.type = "button";
      b.appendChild(ico("eye-slash", 14)); b.appendChild(doc.createTextNode(T("stm.qrHide")));
      b.addEventListener("click", function (ev) { ev.stopPropagation(); cover(); var f = box.querySelector(".ks-qrc button"); if (f) f.focus(); });
      box.appendChild(b);
      track(entry);
      if (o.onShow) o.onShow();
    }
    if (o.open) open(); else cover();
    return box;
  }
  // шторка без кода (раскладка .gs-cover): значок, «QR скрыт: включён режим стрима», «Показать QR»
  function qrCover(onShow, o) {
    o = o || {};
    var c = mk("div", "gs-cover ks-qrc" + (o.coverCls ? " " + o.coverCls : ""));
    c.setAttribute("role", "group");
    c.setAttribute("aria-label", T("stm.qrHid"));
    var ic = mk("span", "ks-qrc__ic"); ic.appendChild(ico("qr-code", 22));
    var b = mk("button", "ga-btn ga-btn--tertiary ga-btn--sm ga-btn--wi");
    b.type = "button";
    b.setAttribute("data-gk", "sqrshow");
    b.appendChild(ico("eye", 14)); b.appendChild(doc.createTextNode(T("stm.qrShow")));
    b.addEventListener("click", function (ev) { ev.stopPropagation(); onShow(); });
    c.appendChild(ic); c.appendChild(mk("p", null, T("stm.qrHid"))); c.appendChild(b);
    return c;
  }

  // link(a, url): ссылка на свой адрес (/p/, файл, превью) — в режиме стрима без href (строка состояния браузера его не
  // покажет при наведении); переход или скачивание — в момент нажатия временной ссылкой
  function link(a, url) {
    if (!KS.on || !a) return a;
    var href = String(url || a.getAttribute("href") || ""), target = a.getAttribute("target"), dl = a.getAttribute("download");
    a.removeAttribute("href");
    a.setAttribute("role", a.getAttribute("role") || "link");
    if (!a.hasAttribute("tabindex")) a.setAttribute("tabindex", "0");
    var go = function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      var t = doc.createElement("a");
      t.href = href;
      if (target) { t.target = target; t.rel = "noopener noreferrer"; }
      if (dl !== null) t.download = dl;
      t.className = "gx-vh";
      doc.body.appendChild(t); t.click(); doc.body.removeChild(t);
    };
    a.addEventListener("click", go);
    a.addEventListener("keydown", function (ev) { if (ev.key === "Enter") go(ev); });
    return a;
  }

  // ── Включение ──
  var subs = [];
  function emit() { subs.slice().forEach(function (fn) { try { fn(KS); } catch (e) { if (g.console) g.console.error(e); } }); }
  function apply(on) {
    KS.on = !!on;
    if (doc && doc.documentElement) { if (KS.on) doc.documentElement.setAttribute("data-stream", "on"); else doc.documentElement.removeAttribute("data-stream"); }
    if (KS.on) start(); else { hideRevealed(); stop(); }
  }
  function set(on) {
    on = !!on;
    lsSet(KEY, on ? "1" : null);
    if (on === KS.on) return;
    apply(on);
    emit();
  }
  function setNames(on) { KS.names = !!on; reDirty = true; lsSet(KEY_NAMES, on ? "1" : null); emit(); }

  var KS = {
    MASK: MASK, on: false, names: false, active: false, _ttl: 10000,
    set: set, setNames: setNames, onChange: function (fn) { subs.push(fn); },
    note: function (v, kind) { note(v, kind); sweepSoon(); }, harvest: function (j) { harvest(j); if (reDirty) sweepSoon(); },
    safe: safe, alias: function (n, l) { alias(n, l); sweepSoon(); }, face: face, forget: forget,
    scrub: scrub, isPublicIp: isPublicIp, looks: looks,
    bind: function (b) { if (b) { if (b.t) B.t = b.t; if (b.tn) B.tn = b.tn; if (b.icon) B.icon = b.icon; } },
    secret: secret, field: field, qr: qr, qrCover: qrCover, link: link, track: track, untrack: untrack, hideRevealed: hideRevealed, t: T,
    _known: function () { return Object.keys(known); },
  };

  // ── Старт: флаг из браузера («1» — включён; нет ключа или мусор — выключен) ──
  if (doc) {
    KS.names = lsGet(KEY_NAMES) === "1";
    if (g.location) {                                            // адрес этой вкладки: хост с портом и секретный путь панели
      note(g.location.host, "host");
      var p = String(g.location.pathname || "").replace(/[^/]*$/, "").replace(/^\/+|\/+$/g, "");
      if (p.length >= 8) note(p, "path");
    }
    apply(lsGet(KEY) === "1");
    doc.addEventListener("visibilitychange", function () { if (doc.visibilityState === "hidden") hideRevealed(); });
    if (g.addEventListener) g.addEventListener("storage", function (ev) {
      if (ev.key === KEY) { var on = ev.newValue === "1"; if (on !== KS.on) { apply(on); emit(); } }
      else if (ev.key === KEY_NAMES) { KS.names = ev.newValue === "1"; reDirty = true; emit(); }
    });
  }
  g.KaskadStream = KS;
  if (typeof module === "object" && module.exports) module.exports = KS;
}(typeof window !== "undefined" ? window : globalThis));
