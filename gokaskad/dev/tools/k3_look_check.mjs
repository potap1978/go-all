// k3_look_check.mjs — «Вид» панели в настоящем Chrome (3.3.6, BACKLOG-3.3.6 п. 4): шторка (компьютер), лист снизу
// (телефон 390) и карточка «Настроек» (компьютер — #settings, телефон — #settings?tab=look) на моке (?mock=1), светлая
// и тёмная, RU и EN. Для каждого места — строка JSON:
//   axes      — оси по порядку ([data-look] внутри места);
//   themes    — подписи темы по порядку (ось theme);
//   swatches  — акценты: { data-accent: [background-color, background-image, background-image у ::before] };
//   halves    — «Каскад» по виду (снимок экрана): цвет у верхнего левого и у нижнего правого края кружка и ожидаемые
//               { a: тёмная тема, b: светлая } — [r, g, b]; плюс фон подложки галочки и фон поверхности;
//   rows      — сколько кружков акцента в каждом ряду (девятый не один в ряду);
//   keys      — стрелки по кружкам: { tabs: остановок Tab, start, right: акцент и фокус после →, left: после ← };
//   small     — на телефоне: что меньше 44 px по высоте или ширине (кнопки, переключатели, ползунки, крестик, подвал);
//   checked   — сколько целей касания измерено (на телефоне);
//   ok        — место открылось.
// node tools/k3_look_check.mjs <адрес статики панели> <chrome> [папка снимков]  → JSON (массив строк) в stdout
// С папкой снимков — ещё PNG шторки/листа и карточки «Настроек» (look-<место>-<ширина>-<тема>-<язык>.png).
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { inflateSync } from "node:zlib";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3], SHOTS = process.argv[4] || "";
const prof = mkdtempSync(join(tmpdir(), "k3look-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_look_check: время вышло"); process.exit(3); }, 300000).unref();
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
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { if (await js(expr)) return true; await sleep(150); } return false; }
await cdp("Page.enable");

const READY = `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('.gx-skel'); })()`;
async function open(w, theme, lang, hash) {
  const phone = w < 700;
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: phone ? 844 : 1000, deviceScaleFactor: 1, mobile: phone });
  // сначала пустая страница: адрес отличается от прежнего только «#…» — без неё Chrome не перезагружает документ, и
  // старая страница (ещё «компьютер» после смены ширины) сама уводила #more на «Обзор» (лист «Вид» не открывался)
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=${lang}#${hash}` }); await sleep(300);
  await js(`localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); true`);
  await cdp("Page.reload"); await sleep(500);
  return until(READY, 20000);
}
// что внутри места: оси, подписи темы, акценты, мелкие цели касания (на телефоне)
const PROBE = (sel, phone) => `(() => {
  const box = document.querySelector(${JSON.stringify(sel)}); if (!box) return null;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== "hidden"; };
  const axes = [...box.querySelectorAll("[data-look]")].filter(vis).map((e) => e.dataset.look);
  const th = box.querySelector('[data-look="theme"]');
  const themes = th ? [...th.querySelectorAll("button")].map((b) => b.textContent.trim()) : [];
  const swatches = {};
  box.querySelectorAll(".gx-swatch").forEach((b) => { const cs = getComputedStyle(b); swatches[b.dataset.accent || b.getAttribute("aria-label")] = [cs.backgroundColor, cs.backgroundImage, getComputedStyle(b, "::before").backgroundImage]; });
  const tops = [...box.querySelectorAll(".gx-swatch")].map((b) => Math.round(b.getBoundingClientRect().top));
  const rows = [...new Set(tops)].map((y) => tops.filter((v) => v === y).length);
  const small = []; let checked = 0;
  if (${phone}) {
    const scope = box.closest(".gx-sheet") || box;
    scope.querySelectorAll("button, input, [role=radio], [role=switch]").forEach((e) => {
      if (!vis(e) || e.closest(".kk-pv")) return;
      const r = e.getBoundingClientRect(); checked++;
      if (r.height < 43.5 || r.width < 43.5) small.push((e.className || e.tagName) + " «" + (e.getAttribute("aria-label") || e.textContent.trim()).slice(0, 24) + "» " + Math.round(r.width) + "×" + Math.round(r.height));
    });
  }
  return { axes, themes, swatches, rows, small, checked };
})()`;
// PNG (8 бит, RGB или RGBA, без чересстрочности) → { w, h, px(x, y) → [r, g, b] }: свой разбор, без зависимостей
function png(buf) {
  let o = 8, w = 0, h = 0, ct = 0; const idat = [];
  while (o < buf.length) {
    const n = buf.readUInt32BE(o), type = buf.toString("latin1", o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + n);
    if (type === "IHDR") { w = d.readUInt32BE(0); h = d.readUInt32BE(4); ct = d[9]; if (d[8] !== 8 || d[12] !== 0 || (ct !== 2 && ct !== 6)) throw new Error("png: не 8-бит RGB(A)"); }
    if (type === "IDAT") idat.push(d);
    o += 12 + n;
  }
  const bpp = ct === 6 ? 4 : 3, row = w * bpp, raw = inflateSync(Buffer.concat(idat)), img = Buffer.alloc(row * h);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (row + 1)], src = raw.subarray(y * (row + 1) + 1, (y + 1) * (row + 1)), cur = img.subarray(y * row, (y + 1) * row), up = y ? img.subarray((y - 1) * row, y * row) : null;
    for (let i = 0; i < row; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0, b = up ? up[i] : 0, c = up && i >= bpp ? up[i - bpp] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      const pr = f === 0 ? 0 : f === 1 ? a : f === 2 ? b : f === 3 ? (a + b) >> 1 : pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      cur[i] = (src[i] + pr) & 255;
    }
  }
  return { w, h, px: (x, y) => { const i = Math.round(y) * row + Math.round(x) * bpp; return [img[i], img[i + 1], img[i + 2]]; } };
}
// «Каскад» по виду: кружок — в видимую область, снимок экрана, цвет у двух противоположных краёв (мимо галочки)
async function halves(sel) {
  const at = await js(`(() => {
    const b = document.querySelector(${JSON.stringify(sel)} + " .gx-swatch--auto"); if (!b) return null;
    b.scrollIntoView({ block: "center" });
    const rgb = (c) => { const x = document.createElement("canvas").getContext("2d"); x.fillStyle = c; x.fillRect(0, 0, 1, 1); return [...x.getImageData(0, 0, 1, 1).data].slice(0, 3); };
    let hue = 200; try { hue = Number(JSON.parse(localStorage.getItem("kaskad-appearance-v1") || "{}").hue) || 200; } catch (e) { /* по умолчанию */ }
    const g = b.querySelector(".gi"), s = document.createElement("i");
    s.style.color = "var(--surface)"; b.parentNode.append(s); const surface = getComputedStyle(s).color; s.remove();
    return { r: b.getBoundingClientRect().toJSON(), a: rgb(KaskadLook.accents("dark", hue).auto), b: rgb(KaskadLook.accents("light", hue).auto),
      chip: g ? getComputedStyle(g).backgroundColor : null, surface };
  })()`);
  if (!at) return null;
  await sleep(150);
  const img = png(Buffer.from((await cdp("Page.captureScreenshot", { format: "png" })).data, "base64"));
  const { x, y, width: w, height: h } = at.r;
  return { tl: img.px(x + w * 0.27, y + h * 0.27), br: img.px(x + w * 0.73, y + h * 0.73), a: at.a, b: at.b, chip: at.chip, surface: at.surface };
}
// стрелки по кружкам акцента (настоящие нажатия клавиш)
async function arrows(sel) {
  const st = `(() => { const box = document.querySelector(${JSON.stringify(sel)} + " .gx-swatches"); const a = document.activeElement;
    return box ? { tabs: [...box.querySelectorAll(".gx-swatch")].filter((b) => b.tabIndex === 0).length, on: (box.querySelector('[aria-checked="true"]') || {}).dataset?.accent || null,
      focus: a && a.classList.contains("gx-swatch") && a.closest(${JSON.stringify(sel)}) ? a.dataset.accent : null } : null; })()`;
  if (!(await js(`(() => { const b = document.querySelector(${JSON.stringify(sel)} + ' .gx-swatches [aria-checked="true"]'); if (!b) return false; b.focus(); return true; })()`))) return null;
  const start = await js(st);
  const press = async (key) => { for (const type of ["rawKeyDown", "keyUp"]) await cdp("Input.dispatchKeyEvent", { type, key, code: key, windowsVirtualKeyCode: key === "ArrowRight" ? 39 : 37 }); await sleep(120); return js(st); };
  const right = await press("ArrowRight"), left = await press("ArrowLeft");
  return { tabs: start.tabs, start: start.on, right: [right.on, right.focus], left: [left.on, left.focus] };
}
async function shot(name, sel) {
  if (!SHOTS) return;
  if (!sel) { const v = await cdp("Page.captureScreenshot", { format: "png" }); mkdirSync(SHOTS, { recursive: true }); writeFileSync(join(SHOTS, name + ".png"), Buffer.from(v.data, "base64")); return; }
  mkdirSync(SHOTS, { recursive: true });
  const r = await js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.left + scrollX, y: b.top + scrollY, w: b.width, h: b.height }; })()`);
  const p = r ? { clip: { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.max(1, r.w), height: Math.max(1, r.h), scale: 1 }, captureBeyondViewport: true } : {};
  const img = await cdp("Page.captureScreenshot", Object.assign({ format: "png" }, p));
  writeFileSync(join(SHOTS, name + ".png"), Buffer.from(img.data, "base64"));
}

const out = [];
for (const lang of ["ru", "en"]) for (const theme of ["dark", "light"]) for (const w of [390, 768, 1440]) {
  const phone = w < 700, tag = `${w}-${theme}-${lang}`;
  // лист: телефон — «Ещё» → строка «Внешний вид». Компьютер (UI-6, один вход): «Вид» в шапке ведёт в «Настройки › Вид»
  let viewbtn = null;
  if (!phone) {
    let go = await open(w, theme, lang, "overview");
    if (go) go = await js(`(() => { const b = document.querySelector(".gx-viewbtn"); if (!b) return false; b.click(); return true; })()`);
    if (go) go = await until(`location.hash.indexOf("#settings") === 0 && location.hash.indexOf("tab=look") > 0 && !!document.querySelector(".kk-look")`, 8000);
    viewbtn = !!go && !(await js(`!!document.querySelector(".gx-sheet--look")`));
  }
  let ok = phone ? await open(w, theme, lang, "more") : false;
  if (ok) ok = await js(`(() => { const b = document.querySelector("[data-look-open]")
    || [...document.querySelectorAll("button.gx-mrow")].find((x) => /^(Вид|Внешний вид|Appearance)$/.test((x.querySelector(".gx-mrow__t") || x).textContent.trim()));
    if (!b) return false; b.click(); return true; })()`);
  if (ok) ok = await until(`!!document.querySelector(".gx-sheet--look .gx-sheet__body > *")`, 5000);
  await sleep(450);
  const sheet = ok ? await js(PROBE(".gx-sheet--look .gx-sheet__body", phone)) : null;
  if (sheet) { sheet.halves = await halves(".gx-sheet--look .gx-sheet__body"); sheet.keys = await arrows(".gx-sheet--look .gx-sheet__body"); }
  if (phone) out.push(Object.assign({ where: "sheet", w, theme, lang, ok: !!sheet }, sheet || {}));
  if (sheet && w !== 768) await shot(`look-sheet-${tag}`, phone ? "" : ".gx-sheet--look");
  if (sheet && w === 1440) await shot(`look-accent-${tag}`, ".gx-sheet--look .gx-swatches");
  // «Настройки → Вид»
  ok = await open(w, theme, lang, phone ? "settings?tab=look" : "settings");
  if (ok) ok = await until(`!!document.querySelector(".kk-look .gx-look__wide > *")`, 8000);
  const card = ok ? await js(PROBE(".kk-look .gx-look__wide", phone)) : null;
  if (card) { card.halves = await halves(".kk-look .gx-look__wide"); card.keys = await arrows(".kk-look .gx-look__wide"); }
  out.push(Object.assign({ where: "settings", w, theme, lang, ok: !!card, viewbtn }, card || {}));
  if (card && (w === 390 || w === 1440)) {
    await js(`(() => { const e = document.querySelector(".kk-look"); if (e) e.scrollIntoView({ block: "start" }); return true; })()`); await sleep(200);
    await shot(`look-settings-${tag}`, phone ? "" : ".kk-look .gx-look__wide");       // телефон: прокручивается #main — снимок экрана
  }
}
console.log(JSON.stringify(out));
process.exit(0);
