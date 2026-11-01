#!/usr/bin/env node
// k3_find_check.mjs — поиск по панели «как в Настройках iPhone» (3.3.6, BACKLOG-3.3.6 п. 3) в настоящем Chrome на
// мок-данных (?mock=1), RU и EN:
//   anchor   — каждая запись описи SECTIONS (настройка, карточка, действие с кнопкой) — ровно один элемент
//              [data-find="<id>"] в своём разделе, и наоборот: каждый [data-find] раздела — запись, которая сейчас есть в
//              поиске (условие need совпадает с тем, что раздел показывает). Компьютер 1440 — все разделы; телефон 390 —
//              части «Настроек» (?tab=), остальные разделы телефона — только счёт (своя раскладка, якоря не обязательны);
//   keys     — ⌘K (Mac) / Ctrl+K (прочие; и прогон с платформой Windows) открывает и закрывает, «/» открывает вне поля, Esc закрывает и возвращает фокус на
//              кнопку поиска; вторая клавиша (Ctrl+K на Mac) в поле ввода не перехватывается (REV-336-search З-3);
//              во время тура поиск не открывается (З-4);
//   aria     — поле combobox с aria-activedescendant на выбранный option, стрелки ходят по кругу;
//   go       — «пароль» → первым «Пароль панели» с путём «Настройки › Доступ к панели», Enter — адрес
//              #settings?tab=access и окно пароля; «реклама» → «Правила», переключатель подсвечен (is-flash) и в фокусе;
//              человек из мока → #people?focus=u-…; протокол и выход на компьютере — плитка подсвечена ровно (без
//              мигания, З-2); после выбора фокус не на <body> (З-1); «порт 443» → проброс, строка подсвечена, список не
//              отфильтрован, и когда поле «Пробросов» было заполнено до выбора (З-6, З-7; REV-336-int З-1); «язык» → «Вид» (З-6);
//              «порт N» / «port N» и число N → протокол на порту N (REV-T-336 Н-5);
//   recent   — пустой запрос показывает «Недавние» с последним выбором первым; пусто — «Ничего не нашлось» с подсказками;
//   phone    — на 390 окно во весь экран, поле 16 px, строки ≥ 44 px, поле поиска в шапке ≥ 44 px;
//   cmdk     — выдача gx-cmdk (nav2 r1, кадр Н2; UI-A2) на 1440: строка ввода — на месте поля шапки, выдача 520 под ним;
//              у первой группы — «· Enter — перейти»; ↵ — только у выбранной (.is-act); «маршрут» / «route» — одна строка
//              «Маршрут по умолчанию» в «Частях разделов» с путём «Выходы › Маршрут по умолчанию» (карточка и часть не
//              двоятся); будущая часть — серым, aria-disabled, с меткой выпуска, Enter её не открывает; нажатие мимо
//              выдачи закрывает поиск; на 1024 (поле в шапке — значком) — окно 520 сверху.
// Запуск: node tools/k3_find_check.mjs [ru,en]; K3_FIND_SHOTS=<папка> — снимки окна поиска 390 и 1440. Нужны Node ≥ 22 и
// Chrome (нет — SKIP, код 77).
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp, writeFile } from "node:fs/promises";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, extname, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "lib", "kaskad", "panel", "static");
const LANGS = (process.argv[2] || "ru,en").split(",");
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const CHROMES = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter(Boolean);
const CHROME = CHROMES.find((p) => existsSync(p));
if (!CHROME || typeof WebSocket === "undefined") { console.log("SKIP: нет Chrome или Node без WebSocket — поиск панели не проверен"); process.exit(77); }

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".json": "application/json" };
const srv = createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p.startsWith("/static/")) p = p.slice(7);
  const f = normalize(join(ROOT, p === "/" ? "index.html" : p));
  if (!f.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
  try { const b = await readFile(f); res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" }); res.end(b); }
  catch (e) { res.writeHead(404); res.end(); }
});
await new Promise((ok) => srv.listen(0, "127.0.0.1", ok));
const BASE = `http://127.0.0.1:${srv.address().port}/index.html`;

// R-333 повтор №7: порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля — к чужому
// браузеру на занятом порту набор не подключится. Под root на Linux Chrome без --no-sandbox не стартует.
const prof = await mkdtemp(join(tmpdir(), "k3-find-"));
const root = typeof process.getuid === "function" && process.getuid() === 0;
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", ...(root ? ["--no-sandbox"] : []), "about:blank"], { stdio: ["ignore", "ignore", "pipe"], detached: true });
let chromeErr = "", chromeGone = null;
chrome.stderr.on("data", (b) => { chromeErr = (chromeErr + b).slice(-2000); });
chrome.on("error", (e) => { chromeGone = e.message; });
chrome.on("exit", (code, sig) => { chromeGone = chromeGone || "код " + code + (sig ? ", " + sig : ""); });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } srv.close(); try { rmSync(prof, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch (e) { /* останется в $TMPDIR */ } };  // REV-speed-2b С3: и профиль Chrome
process.on("exit", stop);
let wsUrl = null;
for (let i = 0; i < 75 && !wsUrl && !chromeGone; i++) {
  try {
    const port = Number((await readFile(join(prof, "DevToolsActivePort"), "utf8")).split("\n")[0]);
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) wsUrl = p.webSocketDebuggerUrl;
  } catch (e) { /* ещё поднимается */ }
  if (!wsUrl) await sleep(200);
}
// Chrome не поднялся — это среда, а не вёрстка: SKIP с причиной (как без Chrome), k3_test.sh не краснеет
if (!wsUrl) {
  const why = chromeGone ? "Chrome завершился (" + chromeGone + ")" : "Chrome не ответил за 15 с";
  const tail = chromeErr.trim().split("\n").filter(Boolean).slice(-1)[0] || "";
  console.log("SKIP: " + why + (tail ? ": " + tail.slice(0, 200) : "") + " — поиск панели не проверен"); process.exit(77);
}
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map();
// ошибки страницы — находка того вида, на котором случились
const jsErrs = [];
const onEvent = (m) => {
  if (m.method === "Runtime.exceptionThrown") { const d = m.params.exceptionDetails || {}; jsErrs.push(((d.exception && d.exception.description) || d.text || "exception").split("\n").slice(0, 2).join(" | ")); }
  else if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "assert")) jsErrs.push("console." + m.params.type + ": " + (m.params.args || []).map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" ").slice(0, 300));
};
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) onEvent(m); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
const until = async (expr, ms) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* страница грузится */ } await sleep(150); } return false; };
const view = (w, h = 900) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });

await cdp("Page.enable"); await cdp("Runtime.enable");
let bad = 0, runs = 0;
const fail = (where, what) => { bad++; console.log("FAIL " + where + ": " + what); };
const jsFail = (where) => { for (const e of jsErrs.splice(0)) fail(where, "js | " + e.slice(0, 300)); };
const READY = `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!window.KaskadPanel && !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel'); })()`;
async function open(lang, w, hash) {
  await view(w);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}#${hash}` });
  await sleep(300);
  await cdp("Page.reload"); await sleep(300);           // другой адрес с тем же документом — только hashchange: вид телефона ставит загрузка
  if (!(await until(READY, 8000))) fail(`${hash} ${w} ${lang}`, "раздел не собрался");
  await sleep(500);
}
const KEYS = { ArrowDown: 40, ArrowUp: 38, Enter: 13, Escape: 27, k: 75, "/": 191 };
async function key(k, mod = 0) {
  const o = { key: k, code: k === "k" ? "KeyK" : k === "/" ? "Slash" : k, windowsVirtualKeyCode: KEYS[k] || 0, modifiers: mod };
  await cdp("Input.dispatchKeyEvent", Object.assign({ type: "keyDown" }, o, k === "/" ? { text: "/" } : {}));
  await cdp("Input.dispatchKeyEvent", Object.assign({ type: "keyUp" }, o));
  await sleep(120);
}
// Подсветка после выбора — по ходу, а не одним замером (REV-336-search З-2): в странице раз в 50 мс отмечаем, есть ли
// is-flash (sel), 2,8 с от нажатия Enter. Годится: появилась не позже 1,5 с, без пропусков до конца и держалась ≥ 1,5 с.
const flashStart = (sel) => js(`(() => { window.__fl = []; clearInterval(window.__flT); const t0 = performance.now();
  window.__flT = setInterval(() => { const t = performance.now() - t0; window.__fl.push([Math.round(t), document.querySelectorAll(${JSON.stringify(sel)}).length ? 1 : 0]);
    if (t > 2800) clearInterval(window.__flT); }, 50); return 1; })()`);
async function flashSeen() {
  await until(`!!window.__fl && window.__fl.length && window.__fl[window.__fl.length - 1][0] > 2800`, 6000);
  const f = await js(`window.__fl || []`);
  const on = f.filter((x) => x[1]), line = f.map((x) => (x[1] ? "F" : "-")).join("");
  if (!on.length) return { ok: false, why: "подсветки нет", line };
  const a = on[0][0], b = on[on.length - 1][0], gap = f.some((x) => x[0] > a && x[0] < b && !x[1]);
  return { ok: a <= 1500 && !gap && b - a >= 1500, why: a > 1500 ? "поздно (" + a + " мс)" : gap ? "мигает" : b - a < 1500 ? "коротко (" + (b - a) + " мс)" : "", line };
}
async function type(text) { await js(`(() => { const i = document.querySelector('#findQ'); if (i) { i.value = ""; i.dispatchEvent(new Event("input")); } return 1; })()`); await cdp("Input.insertText", { text }); await sleep(250); }
const ST = `(() => {
  const box = document.querySelector('.gx-find'), q = document.querySelector('#findQ');
  const opts = [...document.querySelectorAll('.gx-cmdk__r')];
  const sel = opts.findIndex((o) => o.getAttribute('aria-selected') === 'true');
  const heads = [...document.querySelectorAll('.gx-cmdk__cap > span:first-child')].map((h) => h.textContent);
  return { open: !!box, focus: document.activeElement ? (document.activeElement.id || document.activeElement.className) : '', act: q ? q.getAttribute('aria-activedescendant') : null,
    sel, n: opts.length, heads, first: opts[0] ? { t: opts[0].querySelector('.gx-cmdk__t').textContent, p: (opts[0].querySelector('.gx-cmdk__p') || {}).textContent || '' } : null,
    empty: !!document.querySelector('.gx-find__empty'), sugg: document.querySelectorAll('.gx-find__s').length, hash: location.hash,
    dlg: (document.querySelector('.gx-dlg .gx-sheet__title, .gx-dlg .gx-dlg__title') || {}).textContent || '' };
})()`;
const st = () => js(ST);
const T = { ru: { pw: "Пароль панели", pwPath: "Настройки › Доступ к панели", ads: "реклама", recent: "Недавние", sett: "Настройки" },
  en: { pw: "Panel password", pwPath: "Settings › Panel access", ads: "ads", recent: "Recent", sett: "Settings" } };

// ── anchor: опись ↔ [data-find] раздела ──
const ANCH = (page, tab) => `(() => {
  const recs = window.KaskadPanel.findRecords().filter((r) => r.sec === ${JSON.stringify(page)} && /^(card|field|action)$/.test(r.kind) && r.mark !== false);
  const tabOf = (r) => r.tab || (r.id === "settings.bot" || r.id === "settings.mcp" ? "bot" : "");
  const want = recs.filter((r) => ${tab ? "tabOf(r) === " + JSON.stringify(tab) : "true"}).map((r) => r.id);
  const have = [...document.querySelectorAll('section[data-page="${page}"] [data-find]')].map((n) => n.dataset.find);
  const out = [];
  want.forEach((id) => { const n = have.filter((x) => x === id).length; if (n !== 1) out.push(id + " — элементов " + n); });
  have.forEach((id) => { if (want.indexOf(id) < 0) out.push(id + " — элемент есть, а записи в поиске нет"); });
  return { out, want: want.length, have: have.length };
})()`;
const SECS = ["overview", "forwards", "inbounds", "people", "exits", "rules", "scheme", "settings"];
const TABS = ["look", "access", "gateway", "bot", "site", "github", "backup", "about"];
for (const lang of LANGS) {
  let total = 0;
  for (const page of SECS) {
    await open(lang, 1440, page);
    if (page === "settings") { await sleep(600); }
    const r = await js(ANCH(page, "")); runs++; total += r.want;
    r.out.forEach((x) => fail(`anchor ${page} 1440 ${lang}`, x));
    jsFail(`anchor ${page} 1440 ${lang}`);
  }
  console.log(`anchor 1440 ${lang}: ${total} записей на своих местах`);
  for (const tab of TABS) {
    await open(lang, 390, "settings?tab=" + tab);
    const r = await js(ANCH("settings", tab)); runs++;
    r.out.forEach((x) => fail(`anchor settings?tab=${tab} 390 ${lang}`, x));
    jsFail(`anchor settings?tab=${tab} 390 ${lang}`);
  }
  const info = [];
  for (const page of SECS.filter((x) => x !== "settings")) {
    await open(lang, 390, page);
    const r = await js(ANCH(page, "")); info.push(page + " " + (r.want - r.out.filter((x) => / — элементов 0$/.test(x)).length) + "/" + r.want);
  }
  console.log(`anchor 390 ${lang} (телефон, только счёт): ` + info.join(", "));
}

// ⌘K (Mac) / Ctrl+K (прочие): открывает и закрывает, «/» открывает, Esc закрывает; вторая клавиша в поле ввода — не наша
// (на Mac Ctrl+K — «удалить до конца строки»). MAC — как его узнаёт панель (app.js, ux.js); подсказка в шапке — та же
const MAC_JS = `/mac|iphone|ipad/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "")`;
async function keysCheck(where, MAC) {
  const MOD = MAC ? 4 : 2, OTHER = MAC ? 2 : 4;
  const hint = await js(`(() => { const k = document.querySelector('.kk-hfind__k'); return k ? k.textContent : null; })()`);
  if (hint !== null && hint !== (MAC ? "⌘K" : "Ctrl K")) fail(`keys ${where}`, "подсказка в шапке «" + hint + "», ждали «" + (MAC ? "⌘K" : "Ctrl K") + "»");
  await key("k", MOD); let s = await st(); runs++;
  if (!s.open || s.focus !== "findQ") fail(`keys ${where}`, (MAC ? "⌘K" : "Ctrl+K") + " не открыл поиск с фокусом в поле: " + JSON.stringify(s));
  await key("k", MOD); s = await st();
  if (s.open) fail(`keys ${where}`, (MAC ? "⌘K" : "Ctrl+K") + " не закрыл поиск");
  else if (s.focus !== "findBtn" && s.focus.indexOf("kk-hfind") < 0) fail(`keys ${where}`, "фокус после закрытия не на кнопке поиска: " + s.focus);
  await key("/"); s = await st();
  if (!s.open) fail(`keys ${where}`, "«/» не открыл поиск");
  await key("Escape"); s = await st();
  if (s.open) fail(`keys ${where}`, "Esc не закрыл поиск");
  if (await js(`(() => { const i = document.createElement("input"); i.id = "k3fq"; document.body.append(i); i.focus(); window.__kp = null;
    i.addEventListener("keydown", (e) => { setTimeout(() => { window.__kp = e.defaultPrevented; }, 0); }); return true; })()`)) {
    await key("k", OTHER); s = await st();
    const pv = await js(`(() => { const v = window.__kp; document.querySelector("#k3fq").remove(); return v; })()`);
    if (s.open || pv) fail(`keys ${where}`, (MAC ? "Ctrl+K" : "⌘K") + " в поле перехвачен: окно " + s.open + ", preventDefault " + pv);
    if (s.open) { await key("Escape"); }
  }
  // вне поля ввода вторая клавиша поиск тоже не открывает (на Windows ⌘ — это клавиша Windows)
  await js(`(document.activeElement && document.activeElement.blur && document.activeElement.blur(), 1)`);
  await key("k", OTHER); s = await st();
  if (s.open) { fail(`keys ${where}`, (MAC ? "Ctrl+K" : "⌘K") + " открыл поиск"); await key("Escape"); }
}
// ── keys, aria, go, recent ──
for (const lang of LANGS) for (const w of [1440, 390]) {
  const where = `${w} ${lang}`;
  await open(lang, w, "overview");
  await js(`(() => { try { localStorage.removeItem("kaskad-find-recent"); } catch (e) {} return 1; })()`);
  // поле в шапке
  const btn = await js(`(() => { const b = document.querySelector('#findBtn'); if (!b) return null; const r = b.getBoundingClientRect(); return { w: r.width, h: r.height, top: r.top, label: b.getAttribute('aria-label') }; })()`);
  if (!btn) fail(`head ${where}`, "нет кнопки поиска в шапке");
  else if (w < 700 && (btn.h < 44 || btn.w < 300)) fail(`head ${where}`, "поле поиска на телефоне " + Math.round(btn.w) + "×" + Math.round(btn.h));
  // ⌘K на Mac, Ctrl+K на прочих (модификаторы CDP: 2 — Ctrl, 4 — ⌘)
  const MAC = await js(MAC_JS);
  const MOD = MAC ? 4 : 2;
  if (w > 700) await keysCheck(where, MAC);
  // «пароль» → «Пароль панели», путь; стрелки; Enter → окно пароля
  await js(`document.querySelector('#findBtn').click(), 1`); await sleep(200);
  let s = await st(); runs++;
  if (!s.open) { fail(`go ${where}`, "кнопка не открыла поиск"); continue; }
  if (w < 700) {
    const ph = await js(`(() => { const b = document.querySelector('.gx-find').getBoundingClientRect(); const q = getComputedStyle(document.querySelector('#findQ')); const o = [...document.querySelectorAll('.gx-cmdk__r')].map((x) => x.getBoundingClientRect().height); return { w: b.width, h: b.height, vh: innerHeight, fs: q.fontSize, minRow: Math.min(...o) }; })()`);
    if (ph.w < 389 || ph.h < ph.vh - 2) fail(`phone ${where}`, "окно не во весь экран: " + JSON.stringify(ph));
    if (ph.fs !== "16px") fail(`phone ${where}`, "поле не 16 px: " + ph.fs);
    if (ph.minRow < 44) fail(`phone ${where}`, "строка ниже 44 px: " + ph.minRow);
  }
  if (!s.heads.length || s.heads.indexOf(T[lang].recent) >= 0) fail(`recent ${where}`, "пустой запрос: " + JSON.stringify(s.heads));
  await type(lang === "ru" ? "пароль" : "password"); s = await st();
  if (process.env.K3_FIND_SHOTS) {                     // снимки для отчёта: K3_FIND_SHOTS=<папка>
    await sleep(300);
    const png = await cdp("Page.captureScreenshot", { format: "png" });
    await writeFile(join(process.env.K3_FIND_SHOTS, `find-${w}-${lang}.png`), Buffer.from(png.data, "base64"));
  }
  if (!s.first || s.first.t !== T[lang].pw || s.first.p !== T[lang].pwPath) fail(`go ${where}`, "первым не «Пароль панели»: " + JSON.stringify(s.first));
  if (s.sel !== 0 || s.act !== "findO0") fail(`aria ${where}`, "выбран не первый: " + s.sel + " " + s.act);
  await key("ArrowDown"); s = await st();
  if (s.n > 1 && (s.sel !== 1 || s.act !== "findO1")) fail(`aria ${where}`, "↓ не перешла ко второму: " + s.sel + " " + s.act);
  await key("ArrowUp"); await key("ArrowUp"); s = await st();
  if (s.sel !== s.n - 1) fail(`aria ${where}`, "↑ с первого не ушла на последний: " + s.sel + " из " + s.n);
  await key("ArrowDown"); await key("Enter"); await sleep(900); s = await st();
  if (s.open) fail(`go ${where}`, "окно поиска не закрылось");
  if (s.hash !== "#settings?tab=access") fail(`go ${where}`, "адрес " + s.hash);
  if (!s.dlg) fail(`go ${where}`, "окно пароля не открылось");
  await key("Escape"); await sleep(300);
  // «реклама» → «Правила», переключатель подсвечен и в фокусе
  await js(`document.querySelector('#findBtn') ? document.querySelector('#findBtn').click() : null, 1`); await sleep(200);
  s = await st();
  if (!s.heads.length || s.heads[0] !== T[lang].recent) fail(`recent ${where}`, "«Недавние» не первыми: " + JSON.stringify(s.heads));
  else if (!s.first || s.first.t !== T[lang].pw) fail(`recent ${where}`, "последний выбор не первым: " + JSON.stringify(s.first));
  await type(T[lang].ads); await flashStart('section[data-page="rules"] [data-find="rules.ads"].is-flash'); await key("Enter");
  const fls = await flashSeen();
  const fl = await js(`(() => { const n = document.querySelector('section[data-page="rules"] [data-find="rules.ads"]'); return { hash: location.hash, has: !!n, focus: !!n && document.activeElement === n }; })()`);
  if (fl.hash !== "#rules") fail(`go ${where}`, "реклама: адрес " + fl.hash);
  if (w > 700 && (!fls.ok || !fl.focus)) fail(`go ${where}`, "реклама: подсветка " + (fls.why || "есть") + " " + fls.line + ", фокус " + fl.focus);
  if (await js(`document.activeElement === document.body || !document.activeElement`)) fail(`go ${where}`, "реклама: фокус на <body>");
  // человек из мока → #people?focus=u-…
  const who = await js(`(() => { const p = window.KaskadPanel.findRecords().find((r) => r.kind === "person"); return p ? { id: p.id, label: p.label } : null; })()`);
  if (!who) fail(`go ${where}`, "в моке нет пользователей для поиска");
  else {
    await js(`document.querySelector('#findBtn').click(), 1`); await sleep(200);
    await type(who.label); s = await st();
    if (!s.first || s.first.t !== who.label) fail(`go ${where}`, "пользователь не первым: " + JSON.stringify(s.first));
    await key("Enter"); await sleep(1200);
    const h = await js(`location.hash`);
    if (h.indexOf("#people") !== 0) fail(`go ${where}`, "пользователь: адрес " + h);
  }
  // выход и протокол на компьютере — плитка подсвечена
  if (w > 700) for (const kind of ["exit", "proto"]) {
    const r = await js(`(() => { const p = window.KaskadPanel.findRecords().find((r) => r.kind === ${JSON.stringify(kind)}); return p ? { label: p.label, href: p.href } : null; })()`);
    if (!r) { fail(`go ${where}`, "в моке нет " + kind); continue; }
    await js(`document.querySelector('#findBtn').click(), 1`); await sleep(200);
    await type(r.label); await flashStart(".gx-page:not([hidden]) .is-flash"); await key("Enter");
    const fs = await flashSeen(), h = await js(`location.hash`);
    if (!fs.ok) fail(`go ${where}`, kind + " «" + r.label + "»: " + fs.why + " " + fs.line + ", адрес " + h);
    if (await js(`document.activeElement === document.body || !document.activeElement`)) fail(`go ${where}`, kind + " «" + r.label + "»: фокус на <body>");
  }
  // «порт 443» → проброс: строка подсвечена ровно, список не отфильтрован, поле поиска пробросов пустое и после ухода
  {
    const fw = await js(`(() => { const p = window.KaskadPanel.findRecords().find((r) => r.kind === "forward" && /-443$/.test(r.id)); return p ? { id: p.id, label: p.label } : null; })()`);
    if (!fw) fail(`go ${where}`, "в моке нет проброса 443");
    else {
      // поле «Пробросов» заполнено до выбора (REV-336-int З-1): строка 443 им скрыта; на компьютере ищем с самого раздела
      // (тот же раздел, другой адрес), на телефоне — с «Обзора» (поле живёт всю сессию)
      await open(lang, w, "forwards");                   // деталь человека на телефоне — без шапки и кнопки поиска
      const pre = await js(`(() => { const q = document.querySelector('section[data-page="forwards"] [data-keep="fwq"]'); if (!q) return null;
        q.value = "zzq-${w}"; q.dispatchEvent(new Event("input")); return 1; })()`);
      if (!pre) fail(`go ${where}`, "проброс 443: нет поля поиска «Пробросов»");
      else if (!(await until(`!document.querySelector('section[data-page="forwards"] [data-hold-row^="fw:"]')`, 3000))) fail(`go ${where}`, "проброс 443: поле «Пробросов» не отфильтровало список");
      if (w < 700) { await js(`(location.hash = "#overview", 1)`); await until(READY, 4000); await sleep(300); }
      await js(`document.querySelector('#findBtn').click(), 1`); await sleep(200);
      await type(lang === "ru" ? "порт 443" : "port 443"); s = await st();
      if (!s.first || s.first.t !== fw.label) fail(`go ${where}`, "«порт 443»: первым не проброс " + fw.label + ": " + JSON.stringify(s.first));
      const key443 = fw.id.replace(/^fw-(tcp|udp)-/, "fw:$1:");
      await flashStart(`section[data-page="forwards"] [data-hold-row="${key443}"].is-flash`); await key("Enter");
      const fs = await flashSeen();
      if (!fs.ok) fail(`go ${where}`, "проброс 443: подсветка " + fs.why + " " + fs.line);
      const v = await js(`(() => { const sec = document.querySelector('section[data-page="forwards"]'); const q = sec && sec.querySelector('[data-keep="fwq"]');
        return { hash: location.hash, q: q ? q.value : null, rows: sec ? sec.querySelectorAll('[data-hold-row^="fw:"]').length : 0, all: window.KaskadPanel.findRecords().filter((r) => r.kind === "forward").length,
          body: document.activeElement === document.body }; })()`);
      if (v.q) fail(`go ${where}`, "проброс 443: поле поиска пробросов заполнено «" + v.q + "»");
      if (v.rows < v.all) fail(`go ${where}`, "проброс 443: список отфильтрован (" + v.rows + " из " + v.all + ")");
      if (v.body) fail(`go ${where}`, "проброс 443: фокус на <body>");
    }
  }
  // «язык» / «language» → «Вид» (язык сервера меняется там)
  await js(`document.querySelector('#findBtn') ? document.querySelector('#findBtn').click() : null, 1`); await sleep(200);
  await type(lang === "ru" ? "язык" : "language"); s = await st();
  if (!s.first || !/^(Вид|Appearance)$/.test(s.first.t)) fail(`go ${where}`, "«язык»: первым не «Вид»: " + JSON.stringify(s.first));
  await key("Escape");
  // протокол по порту (REV-T-336 Н-5): «порт N» / «port N» и само число → первым протокол на этом порту
  const ibp = await js(`(() => { const p = window.KaskadPanel.findRecords().find((r) => r.id === "ib:awg2"); const n = p && (p.words || []).find((x) => /^\\d+$/.test(x)); return n ? { label: p.label, n } : null; })()`);
  if (!ibp) fail(`go ${where}`, "в моке нет порта AmneziaWG 2.0 в словах поиска");
  else for (const q of [(lang === "ru" ? "порт " : "port ") + ibp.n, ibp.n]) {
    await js(`document.querySelector('#findBtn') ? document.querySelector('#findBtn').click() : null, 1`); await sleep(200);
    await type(q); s = await st();
    if (!s.first || s.first.t !== ibp.label) fail(`go ${where}`, "«" + q + "»: первым не " + ibp.label + ": " + JSON.stringify(s.first));
    await key("Escape"); await sleep(150);
  }
  // тур: поиск поверх него не открывается
  await open(lang, w, "overview");                       // ?open= — один раз: адресом, без перезагрузки
  await js(`(location.hash = "#settings?tab=about&open=tour", 1)`);
  if (await until(`!!document.querySelector('.kk-tour')`, 6000)) {
    await js(`document.querySelector('#findBtn') ? document.querySelector('#findBtn').click() : null, 1`); await sleep(250);
    if (w > 700) await key("k", MOD);
    if ((await st()).open) { fail(`tour ${where}`, "поиск открылся поверх тура"); await key("Escape"); }
  } else fail(`tour ${where}`, "тур не открылся по #settings?tab=about&open=tour");
  // пусто (деталь человека на телефоне — во весь экран без шапки: поиск — с раздела)
  await open(lang, w, "overview");
  await js(`document.querySelector('#findBtn').click(), 1`); await sleep(200);
  await type("zzqqxx"); s = await st();
  if (!s.empty || s.sugg < 2) { fail(`empty ${where}`, "нет «Ничего не нашлось» с подсказками: " + JSON.stringify(s)); continue; }
  await js(`document.querySelector('.gx-find__s').click(), 1`); await sleep(250); s = await st();
  if (s.empty || !s.n) fail(`empty ${where}`, "подсказка не искала");
  await key("Escape");
  jsFail(`flow ${where}`);
  runs++;
}
// ── cmdk: выдача gx-cmdk по кадру Н2 (nav2 r1; UI-A2) — место окна, подпись, ↵, части без дублей, будущая часть ──
const CM = `(() => { const R = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };
  const rows = [...document.querySelectorAll('.gx-cmdk__r')];
  return { at: !!document.querySelector('.gx-find--at'), inp: R(document.querySelector('.gx-find__in')), list: R(document.querySelector('.gx-cmdk')), btn: R(document.querySelector('#findBtn')),
    cap: (document.querySelector('.gx-cmdk__cap') || {}).textContent || '', keys: rows.map((r) => !!r.querySelector('.gx-cmdk__k kbd')), act: rows.map((r) => r.classList.contains('is-act')),
    rows: rows.map((r) => ({ t: r.querySelector('.gx-cmdk__t').textContent, p: (r.querySelector('.gx-cmdk__p') || {}).textContent || '', g: r.closest('.gx-cmdk__g').querySelector('.gx-cmdk__cap > span').textContent,
      dis: r.getAttribute('aria-disabled') === 'true', soon: (r.querySelector('.gx-soon') || {}).textContent || '' })) }; })()`;
const CMT = { ru: { q: "маршрут", part: "Части разделов", go: "Enter — перейти", t: "Маршрут по умолчанию", p: "Выходы › Маршрут по умолчанию", soon: "внутренние группы", soonT: "Внутренние группы", soonP: "Выходы · в следующих версиях" },
  en: { q: "route", part: "Subsections", go: "Enter — go", t: "Default route", p: "Exits › Default route", soon: "domestic groups", soonT: "Domestic groups", soonP: "Exits · in upcoming versions" } };
for (const lang of LANGS) {
  const where = `1440 ${lang}`, L = CMT[lang];
  await open(lang, 1440, "exits?tab=list");
  await js(`document.querySelector('#findBtn').click(), 1`); await sleep(250);
  await type(L.q); let c = await js(CM); runs++;
  // строка ввода — ровно на месте поля шапки, выдача 520 — под ним
  const near = (a, b) => a && b && a.every((x, i) => Math.abs(x - b[i]) <= 1);
  if (!c.at || !near(c.inp, c.btn)) fail(`cmdk ${where}`, "строка ввода не на месте поля шапки: " + JSON.stringify([c.at, c.inp, c.btn]));
  if (!c.list || c.list[2] !== 520 || c.list[1] < c.btn[1] + c.btn[3] || c.list[1] > c.btn[1] + c.btn[3] + 10) fail(`cmdk ${where}`, "выдача не 520 под полем: " + JSON.stringify(c.list));
  if (c.cap.indexOf(L.go) < 0) fail(`cmdk ${where}`, "у первой группы нет «" + L.go + "»: " + c.cap);
  if (c.act.indexOf(true) !== 0 || c.act.filter(Boolean).length !== 1 || c.keys.indexOf(true) !== 0 || c.keys.filter(Boolean).length !== 1) fail(`cmdk ${where}`, "↵ не только у выбранной: " + JSON.stringify([c.act, c.keys]));
  const same = c.rows.filter((r) => r.t === L.t);
  if (same.length !== 1 || same[0].g !== L.part || same[0].p !== L.p) fail(`cmdk ${where}`, "«" + L.t + "» — не одной строкой в «" + L.part + "» с путём «" + L.p + "»: " + JSON.stringify(same));
  await key("ArrowDown"); c = await js(CM);
  if (c.act.indexOf(true) !== 1 || c.keys.indexOf(true) !== 1) fail(`cmdk ${where}`, "↓: ↵ не перешёл ко второй строке: " + JSON.stringify([c.act, c.keys]));
  // будущая часть: серым, недоступна, метка выпуска; Enter не открывает (окно открыто, адрес тот же)
  await type(L.soon); c = await js(CM);
  const so = c.rows.find((r) => r.t === L.soonT);
  if (!so || !so.dis || !so.soon || so.p !== L.soonP || c.rows.indexOf(so) !== 0) fail(`cmdk ${where}`, "будущая часть: " + JSON.stringify(so || c.rows));
  else if (c.keys[0]) fail(`cmdk ${where}`, "у будущей части ↵");
  const h0 = await js(`location.hash`); await key("Enter"); await sleep(400); let s = await st();
  if (!s.open || s.hash !== h0) fail(`cmdk ${where}`, "Enter открыл будущую часть: " + JSON.stringify([s.open, s.hash]));
  // нажатие мимо выдачи — закрыть
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: 300, y: 600, button: "left", clickCount: 1 });
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: 300, y: 600, button: "left", clickCount: 1 }); await sleep(250);
  s = await st(); if (s.open) { fail(`cmdk ${where}`, "нажатие мимо выдачи не закрыло поиск"); await key("Escape"); }
  // поле в шапке значком (1024): окно 520 сверху, строка ввода в окне
  await open(lang, 1024, "exits?tab=list");
  await js(`document.querySelector('#findBtn').click(), 1`); await sleep(250);
  await type(L.q); c = await js(CM);
  if (c.at || !c.list || Math.abs(c.inp[2] - 520) > 1 || c.inp[1] > c.list[1]) fail(`cmdk 1024 ${lang}`, "окно поиска при значке в шапке: " + JSON.stringify([c.at, c.inp, c.list]));
  await key("Escape");
  jsFail(`cmdk ${where}`);
}
// ── keys на не-Mac (REV-336-int П-2): та же проверка с платформой Windows (UA, navigator.platform и userAgentData) —
// на Mac иначе проходит только ветка ⌘K. Один язык, компьютер
{
  const ua0 = await js(`navigator.userAgent`);
  const ver = (/Chrome\/([\d.]+)/.exec(ua0) || [, "130.0.0.0"])[1];
  await cdp("Emulation.setUserAgentOverride", { userAgent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${ver} Safari/537.36`,
    platform: "Win32", userAgentMetadata: { brands: [{ brand: "Chromium", version: ver.split(".")[0] }], fullVersion: ver, platform: "Windows", platformVersion: "10.0.0", architecture: "x86", model: "", mobile: false } });
  await open(LANGS[0], 1440, "overview");
  const where = `1440 ${LANGS[0]} Windows`;
  if (await js(MAC_JS)) fail(`keys ${where}`, "подмена платформы не сработала: страница считает себя Mac");
  else await keysCheck(where, false);
  jsFail(`keys ${where}`);
  await cdp("Emulation.setUserAgentOverride", { userAgent: ua0 });
}
console.log(bad ? `FIND: ${bad} поломок на ${runs} проверках` : `FIND OK: ${runs} проверок`);
stop();
process.exit(bad ? 1 : 0);
