// k3_ovb_r3_check.mjs — круг 1, шаг Т3, «Обзор Б r3» (Claude Design it1 Д2 r3, кадры Б1, Б2, Т1, Т2, Б3, Т3) в
// настоящем Chrome на заглушке панели (?mock=1). tests/panel/test_ovb_r3.py читает JSON из stdout. Только мерит:
//   hero   — E: Сводка 1440 — полоса 30 дней в колонке справа от «паутинки» (сверху вниз: вердикт и балл, замечания,
//            30 дней), а не строкой во всю ширину под ними; без замечаний колонка — по центру «паутинки»;
//   heroPh — E: телефон 390 — прежняя раскладка: 30 дней во всю ширину карточки под «паутинкой»;
//   few    — F: «Сейчас» (?score=links — у части звеньев «обычно» не набралось): ячейка «"обычно" — с N-го дня» на месте
//            колонок «Обычно» и «От обычного» (ПК 1440, EN 1440, окно 1100 — карточка 520–720), в карточке уже 520 (окно 1024)
//            и на телефоне 390 — третьей строкой под линией.
// node tools/k3_ovb_r3_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3ovr3-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_ovb_r3_check: время вышло"); process.exit(3); }, 240000).unref();
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
let seq = 0; const wait = new Map();
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
async function until(expr, ms = 20000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* грузится */ } await sleep(120); } throw new Error("не дождался: " + expr); }
async function view(w, h) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 });
  await cdp("Emulation.setTouchEmulationEnabled", w < 500 ? { enabled: true, maxTouchPoints: 5 } : { enabled: false });
}
async function open(qs, hash, ready) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page]:not([hidden])')`);
  await until(ready);
  await sleep(500);
}
// прямоугольник узла (округлённый): l, t, r, b, w, h
const RECT = `const R = (n) => { if (!n) return null; const b = n.getBoundingClientRect(); return { l: Math.round(b.left), t: Math.round(b.top), r: Math.round(b.right), b: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height) }; };`;
const HERO = `(() => { ${RECT}
  const h = document.querySelector('section[data-page="overview"] .kb-hero'); if (!h) return null;
  const st = getComputedStyle(h), pl = parseFloat(st.paddingLeft), pr = parseFloat(st.paddingRight);
  const box = R(h);
  return { hero: box, inner: { l: Math.round(box.l + pl), r: Math.round(box.r - pr) }, star: R(h.querySelector('.kb-hero__star')), v: R(h.querySelector('.kb-hero__v')),
    iss: R(h.querySelector('.kb-iss')), d30: R(h.querySelector('.kb-30')), dev: document.documentElement.dataset.device || "",
    // колонка справа — от верха вердикта до низа нижнего из «вердикт · замечания · 30 дней»
    col: (() => { const ns = [".kb-hero__v", ".kb-iss", ".kb-30"].map((q) => h.querySelector(q)).filter(Boolean).map(R);
      return { t: Math.min(...ns.map((x) => x.t)), b: Math.max(...ns.map((x) => x.b)) }; })() }; })()`;
const FEW = `(() => { ${RECT}
  const lk = document.querySelector('section[data-page="overview"] .kb-lk'); if (!lk) return null;
  const hd = [...lk.querySelectorAll('.kb-lk__h > span')].map(R);
  const rows = [...lk.querySelectorAll('.kb-lk__r')].map((r) => { const f = r.querySelector('.kb-lk__few');
    return { name: r.querySelector('.kb-lk__n > b').textContent, row: R(r), few: f ? { box: R(f), text: f.textContent, tip: f.getAttribute('title'), span: f.getAttribute('aria-colspan'),
      pips: f.querySelectorAll('.kb-lk__pips > i').length, on: f.querySelectorAll('.kb-lk__pips > i.is-on').length, sw: f.scrollWidth, cw: f.clientWidth } : null,
      v: R(r.querySelector('.kb-lk__v')), line: R(r.querySelector('.kb-lk__c')), us: R(r.querySelector('.kb-lk__us')), rel: R(r.querySelector('.kb-lk__rel')), mk: !!r.querySelector('.kb-lk__n > .gx-mk') }; });
  return { head: hd, rows }; })()`;
const out = {};

// ── E: Сводка 1440 (Б1/Б2) и телефон 390 (Т1) ──
await view(1440, 900);
await open("&lang=ru", "overview", `!!document.querySelector('section[data-page="overview"] .kb-hero .kb-30')`);
out.hero = await js(HERO);
// без замечаний (Б1 «Работает нормально»): колонка — вердикт и 30 дней, по центру «паутинки»
await js(`(() => { const i = document.querySelector('section[data-page="overview"] .kb-iss'); if (i) i.remove(); return true; })()`);
out.heroNoIss = await js(HERO);
await view(390, 844);
await open("&lang=ru", "overview", `!!document.querySelector('section[data-page="overview"] .kb-hero .kb-30')`);
out.heroPh = await js(HERO);

// ── F: «Сейчас», звенья с малой историей (Б3, Т3) ──
const FEW_READY = `!!document.querySelector('section[data-page="overview"] .kb-lk .kb-lk__r')`;
for (const [key, w, h, lang] of [["few", 1440, 900, "ru"], ["fewEn", 1440, 900, "en"], ["few1100", 1100, 800, "ru"], ["few1024", 1024, 768, "ru"], ["fewPh", 390, 844, "ru"]]) {
  await view(w, h);
  await open(`&lang=${lang}&score=links`, "overview?tab=now", FEW_READY);
  out[key] = await js(FEW);
}
console.log(JSON.stringify(out));
ws.close();
process.exit(0);
