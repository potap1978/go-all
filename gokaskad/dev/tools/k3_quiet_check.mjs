// k3_quiet_check.mjs — обычный путь панели без ответов 4xx и без ошибок в консоли (T-3377, критерий С4 стенда: «ни одной
// ошибки в консоли»). Браузер пишет в консоль любой ответ 4xx как ошибку, даже если панель его ждёт, — поэтому обычный путь
// не должен получать отказы вовсе. Настоящий Chrome на моке (?mock=1), ответы мока — как у сервера:
//   people  — «Люди» у сервера без сбора активности (act=none: /api/gw/activity — 404, у /api/session has.activity = false;
//             Н-2): панель её не спрашивает, линии — «нет данных»;
//   enter   — первый вход (#overview) у того же сервера: тоже без 404 (Н-2 «и при входе»);
//   bench   — «Обзор › Проверка сервера › Сейчас» → «Проверить сейчас» → галочка программ → «Проверить» (pkgs=1; Н-15):
//             цена — чтением 200, запуск — 200, окно закрылось.
// Для каждого места и ширины — строка JSON: {where, w, ok, bad: [{m, u, s}] — ответы мока ≥ 400, errors: [...] — консоль,
// исключения и журнал браузера (error), steps: что сделано}. Ответы мока ловятся обёрткой KASKAD_MOCK.fetch до загрузки
// страницы (Page.addScriptToEvaluateOnNewDocument): мок и его дополнения (dev-mock-diag.js) ставят fetch цепочкой.
// node tools/k3_quiet_check.mjs <адрес статики панели> <chrome>  → JSON (массив строк) в stdout
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3quiet-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_quiet_check: время вышло"); process.exit(3); }, 240000).unref();
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
// берут прежний fetch и ставят свой поверх); один ответ — одна запись (вложенные обёртки видят тот же объект Response)
await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
  const seen = new WeakSet(); window.__k3net = [];
  const wrap = (f) => function (url, init) {
    return Promise.resolve(f.call(this, url, init)).then((r) => {
      if (r && typeof r.status === "number" && !seen.has(r)) {
        seen.add(r);
        window.__k3net.push({ m: String((init && init.method) || "GET").toUpperCase(), u: String(url).replace(/^https?:\\/\\/[^/]+/, ""), s: r.status });
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
  await sleep(1500);                                         // запросы после первой отрисовки (активность, сводка, живые данные)
  return ok;
}
const net = () => js("window.__k3net || []");
const out = [];
const row = async (where, w, ok, steps) => { const all = await net(); out.push({ where, w, ok, n: all.length, bad: all.filter((x) => x.s >= 400), errors: errors(), steps }); };

for (const w of [1440, 390]) {
  // Н-2: «Люди» и первый вход у сервера без сбора активности
  for (const [where, hash] of [["people", "people"], ["enter", "overview"]]) {
    const ok = await open(w, "&act=none", hash);
    const steps = [];
    if (where === "people") steps.push("activity:" + (await js("(window.KASKAD_DEBUG && window.KASKAD_DEBUG.R.activity) ? (window.KASKAD_DEBUG.R.activity.missing ? 'missing' : 'data') : 'none'")));
    await row(where, w, ok, steps);
  }
  // Н-15: окно цены → согласие на программы → «Проверить»
  let ok = await open(w, "&pkgs=1", "overview?tab=now");
  const steps = [];
  const click = (label, scope) => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(scope || "button")})].find((x) => x.textContent.trim().indexOf(${JSON.stringify(label)}) === 0 && !x.disabled); if (!b) return false; b.click(); return true; })()`);
  if (ok) {
    steps.push("open:" + (await click("Проверить сейчас")));
    // окно посчитало цену: появилась галочка программ (pkgs=1)
    ok = await until(`[...document.querySelectorAll('.gx-cost button, .gx-cost [role=checkbox]')].some((b) => b.textContent.trim().indexOf('Разрешаю поставить') === 0)`, 10000) && ok;
    steps.push("tick:" + (await click("Разрешаю поставить", ".gx-cost button, .gx-cost [role=checkbox], .gx-cost label")));
    await sleep(300);
    steps.push("go:" + (await js(`(() => { const b = [...document.querySelectorAll("button")].filter((x) => x.textContent.trim() === "Проверить" && !x.disabled).pop(); if (!b) return false; b.click(); return true; })()`)));
    steps.push("closed:" + (await until("!document.querySelector('.gx-cost')", 6000)));
    await sleep(500);                                        // перечитывание после запуска (счёт, история) — тоже без 4xx
    steps.push("posts:" + (await net()).filter((x) => x.m === "POST" && /\/api\/host\/bench$/.test(x.u)).map((x) => x.s).join(","));
  }
  await row("bench", w, ok, steps);
}
console.log(JSON.stringify(out));
process.exit(0);
