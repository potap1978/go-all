#!/usr/bin/env node
// k3_share_gif_size.mjs — настоящий размер 15-секундного GIF «Поделиться» (pages/diag-share.js, makeGIF) в Chrome без
// окна на заглушке панели (?mock=1): для подписи «≈ N МБ» у «Скачать GIF» (EST_MB). Сочетания: формат × тема × язык ×
// галочки; doc — ответ заглушки как есть и «худший» (30 дней четырёх цветов вперемешку, длинные числа, все галочки,
// строки «чьи это цифры»). Печатает одну строку JSON {res: [{fmt, theme, L, flags, worst, bytes, fps, frames}]}.
//   node tools/k3_share_gif_size.mjs [форматы sq,post] [темы dark,light] [языки ru,en] [doc: mock,worst]
//   CHROME=/путь — свой браузер. Нет Chrome — {"skip": "…"} и код 0. Chrome свой (профиль во временной папке), гасится
//   по группе процессов при выходе.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, extname, normalize, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ROOT = normalize(join(dirname(fileURLToPath(import.meta.url)), "..", "lib", "kaskad", "panel", "static"));
const [F0, T0, L0, D0] = process.argv.slice(2);
const FMTS = (F0 || "sq,post").split(","), THEMES = (T0 || "dark,light").split(","), LANGS = (L0 || "ru,en").split(",");
const DOCS = (D0 || "mock,worst").split(",");
const CHROME = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].filter(Boolean).find((p) => existsSync(p));
if (!CHROME || typeof WebSocket === "undefined") { console.log(JSON.stringify({ skip: !CHROME ? "нет Chrome" : "нужен Node ≥ 22 (WebSocket)" })); process.exit(0); }
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".json": "application/json" };
const srv = createServer(async (req, res) => {
  const p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  const f = normalize(join(ROOT, p === "/" ? "index.html" : p));
  if (!f.startsWith(ROOT + sep)) { res.writeHead(403); res.end(); return; }    // только файлы static, не соседние папки
  try { const b = await readFile(f); res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" }); res.end(b); }
  catch (e) { res.writeHead(404); res.end(); }
});
await new Promise((ok) => srv.listen(0, "127.0.0.1", ok));
const prof = await mkdtemp(join(tmpdir(), "k3-gif-size-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run", "--hide-scrollbars", "about:blank"], { stdio: "ignore", detached: true });
let done = false;
const stop = () => { if (done) return; done = true; try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { /* уже нет */ } srv.close(); try { rmSync(prof, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch (e) { /* останется в $TMPDIR */ } };  // REV-speed-2b С3: и профиль Chrome
process.on("exit", stop);
const fail = (m) => { console.log(JSON.stringify({ error: String(m).slice(0, 500) })); stop(); rm(prof, { recursive: true, force: true }).finally(() => process.exit(1)); };
setTimeout(() => fail("тайм-аут 240 с"), 240000).unref();
let wsUrl = null;
for (let i = 0; i < 75 && !wsUrl; i++) {
  try {
    const port = Number((await readFile(join(prof, "DevToolsActivePort"), "utf8")).split("\n")[0]);
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) wsUrl = p.webSocketDebuggerUrl;
  } catch (e) { /* ещё стартует */ }
  if (!wsUrl) await sleep(200);
}
if (!wsUrl) fail("Chrome не отвечает");
const ws = new WebSocket(wsUrl); await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map();
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
try {
  await cdp("Page.enable"); await cdp("Runtime.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await cdp("Page.navigate", { url: `http://127.0.0.1:${srv.address().port}/index.html?mock=1#overview/now` });
  for (let i = 0; i < 100; i++) { await sleep(200); if (await js("!!(window.KaskadPanel && window.KaskadPanel.API)").catch(() => false)) break; }
  const out = await js(`(async () => {
    const K = window.KaskadPanel, A = ${JSON.stringify({ FMTS, THEMES, LANGS, DOCS })};
    await K.loadScript("pages/diag-share.js");
    await K.loadScript("pages/diag-i18n-en.js").catch(() => null);
    const S = window.KaskadShare, mock = await K.API.host.share({ tariff: 1, country: 1, cpu: 1 }), C = ["green", "yellow", "red", "grey"];
    if (!mock || !mock.ready) return { error: "заглушка не отдала готовый кадр" };
    const worst = Object.assign({}, mock, { days: Array.from({ length: 30 }, (_, i) => C[(i * 7 + (i >> 1)) % 4]), rel_total: 287,
      metrics: (mock.metrics || []).map((m, i) => Object.assign({}, m, { v: [9876.5, 8765.4, 287.3, 12.7][i] })),
      where: { down: "cloudflare", up: "cloudflare", anchors: ["cloudflare", "google", "yandex"], n: 3 } });
    const res = [];
    for (const dn of A.DOCS) for (const fmt of A.FMTS) for (const theme of A.THEMES) for (const L of A.LANGS) for (const fl of (dn === "worst" ? [1] : [0, 1])) {
      const o = S.clipOf(dn === "worst" ? worst : mock, { fmt, theme, L }, { tariff: !!fl, country: !!fl, cpu: !!fl });
      const r = await S.makeGIF(o);
      res.push({ fmt, theme, L, flags: fl, worst: dn === "worst", bytes: r.blob.size, fps: r.fps, frames: r.frames });
    }
    return { res };
  })()`);
  console.log(JSON.stringify(out));
} catch (e) { fail(e.message || e); }
stop();
await rm(prof, { recursive: true, force: true }).catch(() => null);
process.exit(0);
