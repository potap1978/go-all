// k3_vless_quiet.mjs — окна согласия и цены VLESS без ответов 4xx и без ошибок в консоли (T-3378 Н-2 / REV-3378-final С1;
// критерий С4 «ни одной ошибки в консоли»). Браузер пишет в консоль любой ответ 4xx как ошибку, даже ожидаемый панелью,
// а окна прежде узнавали вопрос отказом ядра (409 reality_consent / vless_change). Теперь — чтением GET /api/gw/vless/ask
// (200 с need), запись — прежний запрос с ответом. Настоящий Chrome на моке (?mock=1), ответы мока — как у сервера.
// Каждое окно открывается и закрывается отказом — ничего не меняется:
//   consent — vless=tls: «Протоколы» › VLESS › «Маскировка…» › сайт списка › «Применить» → окно согласия Reality → «Назад»,
//             окно маскировки — «Отмена»;
//   tls     — vless=ready (Ц4): карточка VLESS › «Перейти на TLS» → окно цены → «Отмена»;
//   card_on — vless=nosite&site=le (Ц2 из карточки): «Включить сайт на домене» → окно цены → «Отмена»;
//   off     — site=on&vless=tls (Ц1): «Настройки › Домен и сайт» › «Выключить сайт» → окно цены → «Отмена»;
//   on      — site=le&vless=nosite (Ц2): «Настройки › Домен и сайт» › «Включить сайт» → окно цены → «Отмена»;
//   clear   — site=on&vless=tls: «Настройки › Домен и сайт» › «Убрать домен» → окно цены (одно) → «Отмена».
// Для каждого окна и ширины — строка JSON: {where, w, ok, n — ответов мока, bad: [{m, u, s}] — ответы ≥ 400, errors —
// консоль, исключения и журнал браузера (error), asks — коды GET /api/gw/vless/ask, writes — запросы не-GET (кроме проверки
// сайтов POST /api/gw/vless/masks/check — она ничего не меняет), before/after — режим VLESS и сайта у мока, steps}.
// Ответы мока ловятся обёрткой KASKAD_MOCK.fetch до загрузки страницы (Page.addScriptToEvaluateOnNewDocument).
// node tools/k3_vless_quiet.mjs <адрес статики панели> <chrome>  → JSON (массив строк) в stdout
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3], ONLY = process.argv[4] || "";
const prof = mkdtempSync(join(tmpdir(), "k3vlq-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_vless_quiet: время вышло"); process.exit(3); }, 280000).unref();
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function target() {
  for (let i = 0; i < 100; i++) {
    try {
      const port = Number(readFileSync(join(prof, "DevToolsActivePort"), "utf8").split("\n")[0]);
      const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) return p.webSocketDebuggerUrl;
    } catch (e) { /* ещё поднимается */ }
    await sleep(150);
  }
  throw new Error("chrome не поднялся");
}
const ws = new WebSocket(await target());
await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map(); const events = [];
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) events.push(m); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* грузится */ } await sleep(150); } return false; }
await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Log.enable");

// ответы мока: window.KASKAD_MOCK — посредник, у которого fetch — обёртка над тем fetch, что стоит сейчас (дополнения мока
// берут прежний fetch и ставят свой поверх); один ответ — одна запись (вложенные обёртки видят тот же объект Response).
// Свои чтения инструмента (режим до и после) идут мимо записи: заголовок X-K3-Probe
await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
  const seen = new WeakSet(); window.__k3net = [];
  const wrap = (f) => function (url, init) {
    return Promise.resolve(f.call(this, url, init)).then((r) => {
      const probe = init && init.headers && init.headers["X-K3-Probe"];
      if (r && typeof r.status === "number" && !seen.has(r)) {
        seen.add(r);
        if (!probe) window.__k3net.push({ m: String((init && init.method) || "GET").toUpperCase(), u: String(url).replace(/^https?:\\/\\/[^/]+/, ""), s: r.status });
      }
      return r;
    });
  };
  let real;
  Object.defineProperty(window, "KASKAD_MOCK", { configurable: true, enumerable: true, get() { return real; },
    set(x) { real = x && typeof x === "object" ? new Proxy(x, { get(t, k) { const v = t[k]; return k === "fetch" && typeof v === "function" ? wrap(v) : v; } }) : x; } });
})();` });

const errors = () => events.filter((e) => (e.method === "Runtime.consoleAPICalled" && ["error", "assert"].includes(e.params.type))
    || e.method === "Runtime.exceptionThrown" || (e.method === "Log.entryAdded" && e.params.entry.level === "error"))
  .map((e) => e.method === "Runtime.exceptionThrown" ? "exception: " + ((e.params.exceptionDetails.exception || {}).description || e.params.exceptionDetails.text)
    : e.method === "Log.entryAdded" ? "log: " + e.params.entry.text + " " + (e.params.entry.url || "")
    : "console.error: " + e.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
const READY = `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('.gx-skel'); })()`;
async function open(w, query, hash) {
  const phone = w < 700;
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: phone ? 844 : 1000, deviceScaleFactor: 1, mobile: phone });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  events.length = 0;
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru${query}#${hash}` });
  const ok = await until(READY, 20000);
  await sleep(1200);                                         // запросы после первой отрисовки (живые данные, сводка)
  return ok;
}
const net = () => js("window.__k3net || []");
// режим у мока — мимо записи ответов: VLESS (security, reason) и сайт (mode)
const modeNow = () => js(`(async () => {
  const get = async (p) => { const r = await window.KASKAD_MOCK.fetch(p, { method: "GET", headers: { "X-Kaskad-Admin": "1", "X-K3-Probe": "1" } }); return r.json(); };
  const ib = (await get("/api/gw/inbounds")).inbounds.find((x) => x.kind === "vless") || {}; const rec = ib.record || {};
  const s = await get("/api/site");
  return [String(rec.security || ""), String(rec.reason || ""), String((s && (s.mode || (s.site && s.site.mode))) || "")].join("/");
})()`);
// видимая кнопка (или строка-кнопка) с подписью, начинающейся с label, внутри scope; последнее совпадение — верхнее окно
const click = (label, scope, exact) => js(`(() => {
  const L = ${JSON.stringify(label)}, X = ${exact ? "true" : "false"};
  const vis = (x) => !!(x.offsetWidth || x.offsetHeight || x.getClientRects().length);
  const all = [...document.querySelectorAll(${JSON.stringify(scope || "button, [role=button], [role=radio]")})].filter((x) => vis(x) && !x.disabled && x.getAttribute("aria-disabled") !== "true");
  const hit = all.filter((x) => { const s = x.textContent.trim(), a = x.getAttribute("aria-label") || ""; return X ? s === L : s.indexOf(L) === 0 || a.indexOf(L) === 0; });
  const b = hit.pop(); if (!b) return false; b.scrollIntoView({ block: "center" }); b.click(); return true; })()`);
const shown = (sel) => `(() => { const x = document.querySelector(${JSON.stringify(sel)}); return !!x && !!(x.offsetWidth || x.offsetHeight); })()`;
const gone = (sel) => `!document.querySelector(${JSON.stringify(sel)})`;
const dump = () => js(`[...document.querySelectorAll("button, [role=button]")].filter((x) => x.offsetWidth).map((x) => x.textContent.trim().slice(0, 40) + (x.getAttribute("aria-label") ? "{" + x.getAttribute("aria-label") + "}" : "")).filter(Boolean).slice(0, 80)`);

// VLESS на «Протоколах»: раскрыть строку протокола (компьютер и телефон)
async function vlessCard(w, steps) {
  // Т3 (вариант Б): и на телефоне строка «Протоколов» раскрывается на месте (отдельной страницы .kk-vphone нет);
  // «Маскировка…» — одна кнопка в теле строки (REV-it1-b С-3, З-1)
  const ready = `[...document.querySelectorAll("button")].some((x) => x.offsetWidth && x.textContent.trim().indexOf("Маскировка…") === 0)`;
  if (await js(ready)) { steps.push("card:open"); return true; }
  const ok = await js(`(() => { const c = [...document.querySelectorAll("[data-kind='vless'], [data-fold='vless'], .kk-pcard, .gx-card")].find((x) => /VLESS/.test(x.textContent || "")); if (!c) return false;
    const h = c.querySelector("[aria-expanded='false'], button, [role=button]") || c; h.click(); return true; })()`);
  steps.push("card:" + ok);
  if (!(ok && (await until(ready, 8000)))) return false;
  // REV-it1-b З-1: в раскрытой строке VLESS одна кнопка «Маскировка…» (без второй «Маскировка VLESS…»)
  steps.push("maskBtns:" + (await js(`[...document.querySelectorAll("[data-kind='vless'] button, [data-hold-row='ib:vless'] button")].filter((x) => x.offsetWidth && /^Маскировка/.test(x.textContent.trim())).length`)));
  return true;
}
// окно с вопросом (sel) открылось после нажатия; закрыть отказом (no) и дождаться, что его нет
async function askAndRefuse(steps, sel, no) {
  const seen = await until(shown(sel), 10000);
  steps.push("dialog:" + seen);
  if (!seen) return false;
  // о чём спросило окно: заголовок и есть ли цена («Кому выдать заново» / «выдать заново»)
  steps.push("title:" + (await js(`(() => { const d = document.querySelector(${JSON.stringify(sel)}); const h = d.querySelector("h1, h2, h3, [class*='__title'], [class*='__t']"); return (h ? h.textContent : "").trim().slice(0, 80); })()`)));
  steps.push("price:" + (await js(`/выдать заново|выдайте заново|Кому выдать/i.test(document.querySelector(${JSON.stringify(sel)}).textContent)`)));
  steps.push("refuse:" + (await click(no, sel + " button", true)));
  const closed = await until(gone(sel), 6000);
  steps.push("closed:" + closed);
  return closed;
}

const PRICE = ".gx-dlg--price", RISK = ".gx-dlg--risk", MASK = ".gx-dlg--mask";
const SCENES = {
  consent: { query: "&vless=tls", hash: "inbounds", run: async (w, steps) => {
    if (!(await vlessCard(w, steps))) return false;
    steps.push("mask:" + (await click("Маскировка…")));
    if (!(await until(shown(MASK), 8000))) { steps.push("maskWin:false"); return false; }
    // сайты списка пришли (GET /api/gw/vless/masks): выбрать первый
    const listed = await until(`!!document.querySelector("${MASK} .gx-vsites [role=radio], ${MASK} .gx-vsites button, ${MASK} .gx-vsites input")`, 8000);
    steps.push("list:" + listed);
    steps.push("site:" + (await js(`(() => { const b = document.querySelector("${MASK} .gx-vsites [role=radio], ${MASK} .gx-vsites button, ${MASK} .gx-vsites input"); if (!b) return false; b.click(); return true; })()`)));
    await sleep(200);
    steps.push("apply:" + (await click("Применить", MASK + " button", true)));
    const ok = await askAndRefuse(steps, RISK, "Назад");
    steps.push("mask:" + (await until(shown(MASK), 3000)));       // «Назад» — снова окно маскировки, без перехода
    steps.push("cancel:" + (await click("Отмена", MASK + " button", true)));
    steps.push("maskClosed:" + (await until(gone(MASK), 6000)));
    return ok;
  } },
  tls: { query: "&vless=ready", hash: "inbounds", run: async (w, steps) => {
    if (!(await vlessCard(w, steps))) return false;
    steps.push("go:" + (await click("Перейти на TLS")));
    return askAndRefuse(steps, PRICE, "Отмена");
  } },
  card_on: { query: "&vless=nosite&site=le", hash: "inbounds", run: async (w, steps) => {
    if (!(await vlessCard(w, steps))) return false;
    steps.push("go:" + (await click("Включить сайт на домене")));
    return askAndRefuse(steps, PRICE, "Отмена");
  } },
  off: { query: "&site=on&vless=tls", hash: "settings?tab=site", run: async (w, steps) => {
    steps.push("go:" + (await click("Выключить сайт", "button", true)));
    return askAndRefuse(steps, PRICE, "Отмена");
  } },
  on: { query: "&site=le&vless=nosite", hash: "settings?tab=site", run: async (w, steps) => {
    steps.push("go:" + (await click("Включить сайт", "button", true)));
    return askAndRefuse(steps, PRICE, "Отмена");
  } },
  clear: { query: "&site=on&vless=tls", hash: "settings?tab=site", run: async (w, steps) => {
    steps.push("go:" + (await click("Убрать домен", "button", true)));
    const ok = await askAndRefuse(steps, PRICE, "Отмена");
    steps.push("one:" + (await js(`!document.querySelector(".gx-dlg")`)));   // после «Отмены» — второго вопроса нет (Н-10)
    return ok;
  } },
};

const out = [];
for (const w of [1440, 390]) {
  for (const [where, sc] of Object.entries(SCENES)) {
    if (ONLY && ONLY !== where) continue;
    const steps = [];
    let ok = await open(w, sc.query, sc.hash), before = "", after = "";
    try {
      before = await modeNow();
      ok = (await sc.run(w, steps)) && ok;
      await sleep(400);                                      // перечитывание после окна — тоже без 4xx
      after = await modeNow();
    } catch (e) { ok = false; steps.push("error:" + String(e.message || e).slice(0, 200)); }
    if (!ok && process.env.K3_DEBUG) steps.push("buttons:" + JSON.stringify(await dump()));
    const all = await net();
    out.push({ where, w, ok, n: all.length, bad: all.filter((x) => x.s >= 400), errors: errors(),
      asks: all.filter((x) => x.m === "GET" && /^\/api\/gw\/vless\/ask(\?|$)/.test(x.u)).map((x) => x.s),
      writes: all.filter((x) => x.m !== "GET" && !/^\/api\/gw\/vless\/masks\/check$/.test(x.u)).map((x) => x.m + " " + x.u + " " + x.s),
      before, after, steps });
  }
}
console.log(JSON.stringify(out));
process.exit(0);
