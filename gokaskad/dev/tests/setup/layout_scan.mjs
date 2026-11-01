// Kaskad 3.3.3 — проверка раскладки мастера «Быстрый старт» в настоящем браузере (headless Chrome по CDP).
// Проходит сцены dev-view-setup.js (?mock=1) и на каждой ищет то, что глаз владельца находил на снимках:
//   cut      — текст обрезан многоточием или спрятан (scrollWidth > clientWidth у текста с overflow: hidden);
//   out      — текст вылез за рамку своей карточки/плашки;
//   overlap  — текст одного элемента налезает на текст или плашку другого (бейдж на названии, линия на «узел»);
//   rows     — карточки одного ряда сетки разной высоты, жёлтые строки одного ряда не на одной высоте;
//   hscroll  — страница шире окна;
//   js       — исключение на странице или console.error (R-333i Б1: отрисовка падала, а вёрстка оставалась целой).
// --walk: проход этапов подряд без сброса между сценами (2 → 3 «Через выходы» → 4 → отказ выпуска → 5 → 3 → 6):
//   foot     — внизу кнопки чужого этапа или не та кнопка («Дальше без домена» после отказа выпуска).
// node layout_scan.mjs <base> [--langs ru,en] [--themes dark,light] [--widths 1440,390] [--only <подстрока id>]
//                       [--shots <каталог>] [--cyr] [--walk]  — --cyr: найти кириллицу на английских экранах
// Вывод — JSON {problems: [...], scenes: N}; код 0 всегда (решает тест).
import { spawn } from "node:child_process";
import { readFileSync, mkdtempSync, writeFileSync, mkdirSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const A = process.argv.slice(2);
const BASE = A[0];
const opt = (k, d) => { const i = A.indexOf("--" + k); return i > 0 ? A[i + 1] : d; };
const LANGS = opt("langs", "ru,en").split(","), THEMES = opt("themes", "dark,light").split(",");
const WIDTHS = opt("widths", "1440,390").split(",").map(Number), ONLY = opt("only", ""), SHOTS = opt("shots", "");
const CYR = A.includes("--cyr"), WALK = A.includes("--walk");
const CHROME = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/snap/bin/chromium"].find((p) => p && existsSync(p));
if (!CHROME) { console.error("layout_scan: пропущено: нет Chrome"); console.log(JSON.stringify({ skip: "пропущено: нет Chrome", scenes: 0, problems: [] })); process.exit(0); }
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

// порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля: два набора разом
// не попадут в один порт и не подключатся к чужому браузеру (tools/k3_unit_par.py гоняет модули параллельно)
const prof = mkdtempSync(join(tmpdir(), "k3lay-"));
// R-333 П3: под root (VPS) Chrome без --no-sandbox не стартует — ждали бы CDP до таймаута вместо проверки
const ROOTARGS = process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : [];
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", ...ROOTARGS, "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* пусть */ } };
process.on("exit", stop);
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function target() {
  for (let i = 0; i < 100; i++) {
    try { const port = Number(readFileSync(join(prof, "DevToolsActivePort"), "utf8").split("\n")[0]); const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch (e) { /* ещё нет */ }
    await sleep(150);
  }
  throw new Error("chrome не поднялся");
}
const ws = new WebSocket(await target());
await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map();
// ошибки страницы — находка той сцены, на которой они случились
let jsErrs = [];
const onEvent = (m) => {
  if (m.method === "Runtime.exceptionThrown") { const d = m.params.exceptionDetails || {}; jsErrs.push(((d.exception && d.exception.description) || d.text || "exception").split("\n").slice(0, 2).join(" | ")); }
  else if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "assert")) jsErrs.push("console." + m.params.type + ": " + (m.params.args || []).map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" ").slice(0, 300));
};
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) onEvent(m); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { if (await js(expr)) return true; await sleep(100); } throw new Error("не дождался: " + expr); }
const view = (w, h) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });

// Проверка в странице: возвращает список находок
const CHECK = String.raw`(() => {
  const out = [], app = document.getElementById("app") || document.body;
  const vis = (e) => { const s = getComputedStyle(e); if (s.display === "none" || s.visibility === "hidden" || +s.opacity === 0) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const name = (e) => { let s = e.tagName.toLowerCase(); const c = (e.getAttribute("class") || "").split(/\s+/).filter(Boolean).slice(0, 2); if (c.length) s += "." + c.join("."); return s; };
  const txt = (e) => (e.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
  const hidden = (e) => { for (let p = e; p && p !== document.body; p = p.parentElement) { if (!vis(p) || p.closest("[hidden],[aria-hidden='true'] .qs-sk,.qs-sk")) return true; } return false; };
  const clipped = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const s = getComputedStyle(p); if (/(auto|scroll)/.test(s.overflowX + s.overflowY) && p.id !== "qs-scroll" && !p.classList.contains("qs-scroll")) return true; } return false; };
  const boxy = (e) => { const s = getComputedStyle(e); return parseFloat(s.borderLeftWidth) > 0 || parseFloat(s.borderTopWidth) > 0 || (s.backgroundColor !== "rgba(0, 0, 0, 0)" && s.backgroundColor !== "transparent"); };
  // 1. обрезанный текст
  for (const e of app.querySelectorAll("*")) {
    if (!e.childNodes.length || hidden(e) || e.closest("svg, .qs-vh")) continue;      // .qs-vh — текст только для чтеца экрана
    const s = getComputedStyle(e);
    if (s.textOverflow === "ellipsis" || (s.overflowX === "hidden" && s.whiteSpace === "nowrap") || s.webkitLineClamp !== "none" && s.webkitLineClamp) {
      if (e.scrollWidth > e.clientWidth + 1 || (s.webkitLineClamp && s.webkitLineClamp !== "none" && e.scrollHeight > e.clientHeight + 1))
        if (!/^(input|textarea|select)$/i.test(e.tagName) && !e.closest("[data-lay-cut]")) out.push({ k: "cut", el: name(e), t: txt(e) });
    }
  }
  // 1б. подсказка пустого поля не влезает в поле (R-333 №6: «ghp_… or github_pat_…» срезана на 390)
  const cv = document.createElement("canvas").getContext("2d");
  for (const e of app.querySelectorAll("input[placeholder]:not([type=hidden])")) {
    if (e.value || hidden(e)) continue;
    const s = getComputedStyle(e);
    cv.font = s.fontWeight + " " + s.fontSize + " " + s.fontFamily;
    const room = e.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    if (cv.measureText(e.placeholder).width > room + 1) out.push({ k: "cut", el: name(e), t: "placeholder: " + e.placeholder.slice(0, 40) });
  }
  // текстовые куски: элемент-владелец и прямоугольники строк
  const runs = [];
  const scE = document.getElementById("qs-scroll") || document.querySelector(".qs-scroll"), scR = scE ? scE.getBoundingClientRect() : null;
  const tw = document.createTreeWalker(app, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode());) {
    if (!n.nodeValue.trim()) continue;
    const e = n.parentElement;
    if (!e || hidden(e) || clipped(e) || e.closest("input,textarea,select,option,.qsdev,[role=tooltip],.qs-vh")) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    // видимая часть: внутри прокрутки мастера — только то, что в её окне; нижняя полоса кнопок — отдельный слой
    const box = scR && e.closest("#qs-scroll, .qs-scroll") ? scR : null;
    // спрятанное обрезкой (overflow: hidden у владельца или предка) не налезает — его ищет проверка cut
    let rs = [...rg.getClientRects()].filter((r) => r.width > 1 && r.height > 1 && (!box || (r.bottom > box.top + 2 && r.top < box.bottom - 2)));
    for (let q = e; q && q !== app && rs.length; q = q.parentElement) {
      const qs = getComputedStyle(q);
      if (!/(hidden|clip)/.test(qs.overflowX)) continue;
      const qb = q.getBoundingClientRect();
      rs = rs.map((r) => ({ left: Math.max(r.left, qb.left), right: Math.min(r.right, qb.right), top: r.top, bottom: r.bottom })).filter((r) => r.right - r.left > 1);
    }
    if (rs.length) runs.push({ e, rs, n, foot: !!e.closest(".qs-foot, footer") });
  }
  // 2. текст за рамкой: ближайший предок с рамкой или фоном
  for (const r of runs) {
    let p = r.e;
    while (p && p !== app && !boxy(p)) p = p.parentElement;
    if (!p || p === app || p === document.body || (scE && p.contains(scE))) continue;
    const b = p.getBoundingClientRect(), s = getComputedStyle(p);
    const L = b.left + parseFloat(s.borderLeftWidth), R = b.right - parseFloat(s.borderRightWidth), T = b.top, B = b.bottom;
    for (const x of r.rs) if (x.left < L - 1 || x.right > R + 1 || x.top < T - 2 || x.bottom > B + 2) { out.push({ k: "out", el: name(r.e), in: name(p), t: txt(r.e), dx: Math.round(Math.max(L - x.left, x.right - R)) }); break; }
  }
  // 2б. плашка (схема, бейдж) за рамкой своей карточки — сама плашка с рамкой, её текст проверка 2 не видит (R-333 №2)
  for (const e of app.querySelectorAll(".qs-dia__p, .gx-chip, [data-lay='box']")) {
    if (hidden(e) || clipped(e)) continue;
    let p = e.parentElement;
    while (p && p !== app && !boxy(p)) p = p.parentElement;
    if (!p || p === app || p === document.body || (scE && p.contains(scE))) continue;
    const b = p.getBoundingClientRect(), r = e.getBoundingClientRect();
    const dx = Math.max(b.left - r.left, r.right - b.right), dy = Math.max(b.top - r.top, r.bottom - b.bottom);
    if (dx > 1 || dy > 2) out.push({ k: "out", el: name(e), in: name(p), t: txt(e), dx: Math.round(Math.max(dx, dy)) });
  }
  // 3. наложения: текст на текст другого элемента, текст на плашку (бейдж, кнопку схемы), линии схемы на текст
  const hit = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1.5 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1.5;
  const rel = (a, b) => a === b || a.contains(b) || b.contains(a);
  const pills = [...app.querySelectorAll(".gx-chip, .qs-dia__p, [data-lay='box']")].filter((e) => !hidden(e)).map((e) => ({ e, r: e.getBoundingClientRect() }));
  const lines = [...app.querySelectorAll("[data-lay='line']")].filter((e) => !hidden(e)).map((e) => ({ e, r: e.getBoundingClientRect() }));
  for (let i = 0; i < runs.length; i++) {
    const a = runs[i];
    for (let j = i + 1; j < runs.length; j++) {
      const b = runs[j];
      if (rel(a.e, b.e) || a.foot !== b.foot) continue;
      if (a.rs.some((x) => b.rs.some((y) => hit(x, y)))) { out.push({ k: "overlap", a: name(a.e), at: txt(a.e), b: name(b.e), bt: txt(b.e) }); }
    }
    for (const p of pills) if (!p.e.contains(a.e) && !!p.e.closest(".qs-foot, footer") === a.foot && !rel(a.e, p.e) && a.rs.some((x) => hit(x, p.r))) out.push({ k: "overlap", a: name(a.e), at: txt(a.e), b: name(p.e), bt: txt(p.e) });
    for (const l of lines) if (a.rs.some((x) => hit(x, l.r))) out.push({ k: "overlap", a: name(a.e), at: txt(a.e), b: "line", bt: name(l.e) });
  }
  for (const l of lines) for (const p of pills) if (hit(l.r, p.r)) out.push({ k: "overlap", a: "line", at: name(l.e), b: name(p.e), bt: txt(p.e) });
  // 3б. схемы на SVG: линия (path/line/polyline со stroke) проходит по тексту — точки текста в штрихе линии
  for (const svg of app.querySelectorAll("svg")) {
    if (hidden(svg) || svg.closest(".qsdev")) continue;
    const texts = [...svg.querySelectorAll("text")].filter((t) => t.textContent.trim());
    if (!texts.length) continue;
    const strokes = [...svg.querySelectorAll("path, line, polyline")].filter((g) => { const st = getComputedStyle(g); return st.stroke && st.stroke !== "none" && parseFloat(st.strokeWidth) > 0 && st.display !== "none"; });
    for (const t of texts) {
      const r = t.getBoundingClientRect();
      let hitIt = null;
      for (let fx = 0.1; fx < 1 && !hitIt; fx += 0.2) for (let fy = 0.3; fy < 0.8 && !hitIt; fy += 0.2) {
        for (const g of strokes) {
          const gm = g.getScreenCTM && g.getScreenCTM();
          if (!gm) continue;
          const pt = new DOMPoint(r.left + r.width * fx, r.top + r.height * fy).matrixTransform(gm.inverse());
          try { if (g.isPointInStroke(pt)) { hitIt = g; break; } } catch (e) { /* старый движок */ }
        }
      }
      if (hitIt) out.push({ k: "overlap", a: "svg text", at: t.textContent.trim().slice(0, 40), b: "svg line", bt: (hitIt.getAttribute("class") || hitIt.tagName) });
    }
  }
  // 4. ряды сеток: карточки одного ряда — одной высоты; жёлтые строки — на одной высоте
  for (const g of app.querySelectorAll(".qs-grid-protos, .qs-grid-2, .qs-grid-3, .qs-needs")) {
    if (hidden(g)) continue;
    const kids = [...g.children].filter(vis), rows = {};
    kids.forEach((k) => { const r = k.getBoundingClientRect(); (rows[Math.round(r.top)] = rows[Math.round(r.top)] || []).push({ k, r }); });
    for (const top in rows) {
      const row = rows[top];
      if (row.length < 2) continue;
      const hs = row.map((x) => Math.round(x.r.height));
      if (Math.max(...hs) - Math.min(...hs) > 1) out.push({ k: "rows", el: name(g), t: "высоты " + hs.join("/") });
      const ws = row.map((x) => x.k.querySelector(".qs-choice__warn")).filter(Boolean).map((w) => Math.round(w.getBoundingClientRect().top));
      if (ws.length > 1 && Math.max(...ws) - Math.min(...ws) > 1) out.push({ k: "rows", el: name(g), t: "жёлтые строки " + ws.join("/") });
    }
  }
  // 4б. окно и шторка (#layers): кнопка или текст за краем экрана (R-333i: три кнопки окна на 320 уходили влево)
  const lay = document.getElementById("layers");
  const shown = (e) => { for (let p = e; p && p !== lay; p = p.parentElement) if (!vis(p)) return false; return true; };   // у самого #layers размера нет
  if (lay) for (const e of lay.querySelectorAll("button, a, p, h2")) {
    if (!shown(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.left < -1 || r.right > innerWidth + 1) out.push({ k: "out", el: name(e), t: txt(e) + " (" + Math.round(r.left) + "…" + Math.round(r.right) + " из " + innerWidth + ")" });
  }
  // 5. страница шире окна
  const sc = document.getElementById("qs-scroll") || document.scrollingElement;
  if (document.scrollingElement.scrollWidth > innerWidth + 1 || (sc && sc.scrollWidth > sc.clientWidth + 1)) out.push({ k: "hscroll", t: document.scrollingElement.scrollWidth + "/" + innerWidth });
  if (${CYR} && document.documentElement.lang === "en") {
    const seen = new Set();
    for (const r of runs) if (/[А-Яа-яЁё]/.test(r.n.nodeValue) && !r.e.closest("[lang=ru]") && !seen.has(r.n.nodeValue)) { seen.add(r.n.nodeValue); out.push({ k: "cyr", el: name(r.e), t: r.n.nodeValue.trim().slice(0, 80) }); }
  }
  return out;
})()`;

const problems = [];
let scenes = 0;
// Проход этапов подряд, как у человека (сцены без сброса): внизу — кнопки своего этапа, ошибок страницы нет.
// [сцена, этап, ключ подписи «Дальше» (dm.noDom: на телефоне — dm.noDomShort) или null — любая]
const WALK_STEPS = [["protocols.pick", "protocols", null], ["exits.empty", "exits", null], ["exits.subs", "exits", null],
  ["domain.ready", "domain", "dm.certGo"], ["domain.certerr", "domain", "dm.noDom"], ["extras.bot", "extras", null],
  ["extras.mcp", "extras", null], ["exits.empty", "exits", null], ["done.summary", "done", null]];
async function walk(lang, theme, w, flush) {
  for (const [id, stage, key] of WALK_STEPS) {
    try { await js(`Promise.resolve(window.KaskadDev.run(${JSON.stringify(id)}, { keep: true })).then(() => true)`); }
    catch (e) { problems.push({ lang, theme, w, id, k: "error", t: String(e.message).slice(0, 200) }); flush({ id }); continue; }
    await sleep(350);
    scenes++;
    const got = await js(`(() => {
      const row = document.querySelector(".qs-foot__row"), nx = row && row.querySelector('[data-k="foot-next"]');
      const L = document.documentElement.lang === "en" ? window.KASKAD_SETUP_I18N_EN : window.KASKAD_SETUP_I18N;
      const key = ${JSON.stringify(key)} === "dm.noDom" && innerWidth < 768 ? "dm.noDomShort" : ${JSON.stringify(key)};
      return { cur: window.KaskadSetup.S.rail.current, foot: row ? row.getAttribute("data-stage") : null,
        label: nx ? nx.textContent.replace(/\\s+/g, " ").trim() : null, want: key && L ? L[key] : null };
    })()`);
    if (got.cur !== stage || got.foot !== stage) problems.push({ lang, theme, w, id, k: "foot", t: "этап " + got.cur + ", внизу кнопки этапа " + got.foot + " (ждали " + stage + ")" });
    else if (got.want && got.label !== got.want) problems.push({ lang, theme, w, id, k: "foot", t: "внизу «" + got.label + "», ждали «" + got.want + "»" });
    for (const p of await js(CHECK)) problems.push(Object.assign({ lang, theme, w, id }, p));
    flush({ id });
  }
}
await cdp("Page.enable"); await cdp("Runtime.enable");
await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
for (const lang of LANGS) for (const theme of THEMES) for (const w of WIDTHS) {
  const H = w < 600 ? 844 : 900;
  await view(w, H);
  for (let a = 0; ; a++) {                // медленная машина: одна повторная загрузка
    await cdp("Page.navigate", { url: `${BASE}/setup/index.html?mock=1&bar=0&lang=${lang}&theme=${theme}` });
    try { await until(`!!(window.KaskadDev && window.KaskadSetup && window.KaskadSetup.S.rail)`, 30000); break; } catch (e) { if (a) throw e; }
  }
  await js(`document.fonts.ready.then(() => true)`);
  const flush = (where) => { for (const t of jsErrs.splice(0)) problems.push(Object.assign({ lang, theme, w }, where, { k: "js", t: t.slice(0, 300) })); };
  flush({ id: "load" });
  // fonts.ready ждёт и шрифт, который не загрузился: запасной шрифт шире — «cut» на всех сценах этой ширины
  // (R-s1-flaky, rail.welcomeSub). Находка — сам незагруженный шрифт, а не обрезки из-за него
  for (const f of await js(`[...document.fonts].filter((f) => f.status === "error").map((f) => f.family)`)) problems.push({ lang, theme, w, id: "load", k: "font", t: "шрифт не загрузился: " + f });
  if (WALK) { await walk(lang, theme, w, flush); continue; }
  const ids = (await js(`window.KaskadDev.scenes()`)).filter((x) => !x.startsWith("sys.") && (!ONLY || ONLY.split("|").some((o) => x === o || x.startsWith(o + "."))));
  for (const id of ids) {
    try { await js(`Promise.resolve(window.KaskadDev.run(${JSON.stringify(id)})).then(() => true)`); } catch (e) { problems.push({ lang, theme, w, id, k: "error", t: String(e.message).slice(0, 200) }); continue; }
    await sleep(350);
    scenes++;
    const got = await js(CHECK);
    for (const p of got) problems.push(Object.assign({ lang, theme, w, id }, p));
    flush({ id });
    if (SHOTS) {
      const need = await js(`(() => { const s = document.getElementById("qs-scroll") || document.querySelector(".qs-scroll"); return s ? s.scrollHeight - s.clientHeight : 0; })()`);
      if (need > 0) { await view(w, H + need); await sleep(250); }
      const r = await cdp("Page.captureScreenshot", { format: "png" });
      writeFileSync(join(SHOTS, `${id}-${w}-${theme}-${lang}.png`), Buffer.from(r.data, "base64"));
      if (need > 0) { await view(w, H); await sleep(150); }
    }
  }
}
console.log(JSON.stringify({ scenes, problems }));
stop();
process.exit(0);
