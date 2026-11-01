// k3_rules_check.mjs — форма условия группы (exits-groups.js ruleForm, экран Г «Условие фразой») в настоящем Chrome на
// моке (?mock=1, #exits?tab=groups&open=rule): 720, 768, 1024 и 1440 (на телефоне «Группы» нет — T8, exPhoneCur), тёмная и светлая, RU и EN. Владелец 29.10: нет полей чисел
// (порог, время), нет колонки подписей, пересказ дважды, нет [×] у условия. Для каждого вида — строка JSON:
//   nums     — поля чисел формы: [ширина, высота] (у невидимого — 0×0);
//   qnums    — поля чисел формы квоты выхода (тот же класс kk-eg-num): [ширина, высота];
//   labels   — подпись строки → x первого поля её строки (колонка подписей: на компьютере у всех одно x);
//   conds    — строки проверок: [x первого поля, есть ли «×»];
//   adds     — x кнопок «+ условие»;
//   cols     — x колонки полей формы (по первой строке «Если»);
//   sw       — переключатели формы: текст → { right: правый край − правый край формы, line: та же строка, что подпись }
//   times    — сколько раз в карточке группы сказано «… до ухода» / «… to leave»;
//   over     — что выходит за правый край формы;
//   h        — высота списков формы (select);
//   vars     — то же после правок: lossPick (потери, «выбрать…» выходы), push (только уведомить); live — порог 30:
//              [фокус остался в поле, текст строки условия].
// node tools/k3_rules_check.mjs <адрес статики панели> <chrome> [папка снимков] [приставка снимков]  → JSON в stdout
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3], SHOTS = process.argv[4] || "", TAG = process.argv[5] || "rules";
const prof = mkdtempSync(join(tmpdir(), "k3rules-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_rules_check: время вышло"); process.exit(3); }, 240000).unref();
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

const READY = (sel) => `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('.gx-skel') && !document.querySelector('#progress:not([hidden])') && !!document.querySelector(${JSON.stringify(sel)}); })()`;
async function open(w, theme, lang, hash, sel) {
  const phone = w < 700;
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: phone ? 844 : 1200, deviceScaleFactor: 1, mobile: phone });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=${lang}#${hash}` }); await sleep(300);
  await js(`localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); true`);
  await cdp("Page.reload"); await sleep(500);
  return until(READY(sel), 20000);
}
const PROBE = `(() => {
  const f = document.querySelector(".kk-eg-rform"); if (!f) return null;
  const card = f.closest(".kk-eg-card") || document.body;
  const R = (e) => e.getBoundingClientRect(), fr = R(f), X = (e) => Math.round(R(e).left - fr.left);
  const vis = (e) => { const r = R(e); return r.width > 0 && r.height > 0; };
  const CTL = "select, input, button";
  const firstCtl = (box, after) => [...box.querySelectorAll(CTL)].find((c) => vis(c) && c.getAttribute("role") !== "switch" && (!after || (after.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)));
  const nums = [...f.querySelectorAll('input[type="number"]')].map((i) => [Math.round(R(i).width), Math.round(R(i).height)]);
  const labels = {};
  f.querySelectorAll(".kk-eg-l").forEach((l) => { const row = l.parentElement; const c = row ? firstCtl(row, l) : null; labels[l.textContent.trim()] = c ? X(c) : null; });
  const conds = [...f.querySelectorAll(".kk-eg-cond")].map((c) => { const a = firstCtl(c); const del = [...c.querySelectorAll("button")].some((b) => vis(b) && /Убрать проверку|Remove the check/.test(b.getAttribute("aria-label") || "")); return [a ? X(a) : null, del]; });
  const adds = [...f.querySelectorAll("button")].filter((b) => vis(b) && /^(условие|condition)$/.test(b.textContent.trim())).map(X);
  const sw = {};
  const lineOf = (txt) => { const l = [...f.querySelectorAll(".kk-eg-l")].find((x) => x.textContent.trim() === txt); return l ? R(l) : null; };
  const pair = { "Только вручную": "Вернуть, когда", "Only by hand": "Return when", "Сообщить в бот": "Для выходов", "Tell the bot": "For exits" };
  f.querySelectorAll('[role="switch"]').forEach((s) => {
    const t = (s.textContent.trim().match(/^(Только вручную|Only by hand|Сообщить в бот|Tell the bot)/) || [s.textContent.trim().slice(0, 20)])[0];
    const r = R(s), lr = pair[t] ? lineOf(pair[t]) : null;
    sw[t] = { right: Math.round(r.right - fr.right), line: lr ? Math.abs((r.top + r.bottom) / 2 - (lr.top + lr.bottom) / 2) < Math.max(r.height, lr.height) / 2 + 2 : null };
  });
  const text = card.innerText || "";
  const times = (text.match(/до ухода|to leave/g) || []).length;
  const over = [...f.querySelectorAll(CTL + ", .kk-eg-l, .gx-desc")].filter((e) => vis(e) && R(e).right > fr.right + 1).map((e) => (e.getAttribute("aria-label") || e.textContent.trim()).slice(0, 30));
  if (f.scrollWidth > f.clientWidth + 1) over.push("scroll " + f.scrollWidth + ">" + f.clientWidth);
  const h = [...new Set([...f.querySelectorAll("select")].filter(vis).map((s) => Math.round(R(s).height)))];
  return { nums, labels, conds, adds, sw, times, over, h, fw: Math.round(fr.width) };
})()`;
const QPROBE = `(() => { const f = document.querySelector("[data-eg-xform]"); if (!f) return null; return [...f.querySelectorAll(".kk-eg-num")].map((i) => { const r = i.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }); })()`;
async function shot(name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  // строка условия (шапка) и его форма — как на снимке владельца
  const r = await js(`(() => { const f = document.querySelector(".kk-eg-rform"); if (!f) return null; const li = f.closest(".kk-eg-rulef"); const head = li && li.previousElementSibling; const a = (head || f).getBoundingClientRect(), b = f.getBoundingClientRect(); return { x: Math.min(a.left, b.left) + scrollX - 8, y: a.top + scrollY - 8, w: Math.max(a.width, b.width) + 16, h: b.bottom - a.top + 16 }; })()`);
  if (!r) return;
  const img = await cdp("Page.captureScreenshot", { format: "png", clip: { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.max(1, r.w), height: Math.max(1, r.h), scale: 1 } });
  writeFileSync(join(SHOTS, name + ".png"), Buffer.from(img.data, "base64"));
}

const WIDTHS = (process.env.K3_RULES_W || "720,768,1024,1440").split(",").map(Number);
const out = [];
for (const lang of ["ru", "en"]) for (const theme of ["dark", "light"]) for (const w of WIDTHS) {
  const ok = await open(w, theme, lang, "exits?tab=groups&open=rule", ".kk-eg-rform");
  if (ok) { await js(`(() => { const f = document.querySelector(".kk-eg-rform"); const li = f && f.closest(".kk-eg-rulef"); (li && li.previousElementSibling || f).scrollIntoView({ block: "start" }); return true; })()`); await sleep(250); }
  const p = ok ? await js(PROBE) : null;
  if (p) await shot(`${TAG}-${w}-${theme}-${lang}`);
  // как на снимке владельца: проверка «потери» (единица — подписью), выходы «выбрать…» (галочки под строкой); затем
  // «только уведомить» (без возврата) — колонка та же, ничего не торчит
  const vars = {};
  if (p) {
    const set = (aria, v) => js(`(() => { const s = document.querySelector('.kk-eg-rform select[aria-label="${aria}"]'); if (!s) return false; s.value = ${JSON.stringify(v)}; s.dispatchEvent(new Event("change")); return true; })()`);
    const ru = lang === "ru";
    let go = await set(ru ? "Что проверять" : "What to check", "loss") && await set(ru ? "Для выходов" : "For exits", "pick");
    if (go) go = await until(`!!document.querySelector(".kk-eg-rform .kk-eg-expick")`, 4000);
    vars.lossPick = go ? await js(PROBE) : null;
    if (go) await shot(`${TAG}-${w}-${theme}-${lang}-loss`);
    // порог 30 — фраза в строке условия над формой меняется сразу (без пересборки карточки)
    if (go) vars.live = await js(`(() => { const f = document.querySelector(".kk-eg-rform"), n = f.querySelector('input[type="number"]'); n.focus(); n.value = "30"; n.dispatchEvent(new Event("input"));
      const head = f.closest(".kk-eg-rulef").previousElementSibling; return [document.activeElement === n || null, head.querySelector(".kk-eg-rule__t").textContent]; })()`);
    go = go && await set(ru ? "Что сделать" : "What to do", "push");
    if (go) go = await until(`!document.querySelector(".kk-eg-rform .kk-eg-rsw[role=switch]") || document.querySelectorAll(".kk-eg-rform [role=switch]").length === 1`, 4000);
    vars.push = go ? await js(PROBE) : null;
  }
  const q = (await open(w, theme, lang, "exits?focus=e-41a19b9cbe&open=exit", "[data-eg-xform] .kk-eg-num")) ? await js(QPROBE) : null;
  out.push(Object.assign({ w, theme, lang, ok: !!p, qnums: q, vars }, p || {}));
}
console.log(JSON.stringify(out));
process.exit(0);
