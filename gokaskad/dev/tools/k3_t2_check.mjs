// k3_t2_check.mjs — круг 1, шаг Т2 (Claude Design it1 Д3 «Выходы и Настройки») в настоящем Chrome на заглушке панели.
// tests/test_it1_t2_panel.py читает JSON из stdout. Разделы:
//   cc     — №25: сворачиваемые столбцы «Все выходы» (общий узор xt.js): какие сворачиваются, ширина 24, ячейки-полоски,
//            значение в подсказке и в раскрытии строки, память в браузере, плотность, клавиатура, развернуть обратно;
//   grp    — №28–31 «Группы»: «⋯» в шапке карточки с «Удалить группу…» (внизу кнопки нет), российских групп в списке нет —
//            строка-ссылка в «Российские группы», сценарии над переключателями, подсказка «Способ выбора» по нажатию и Esc,
//            пояснение у «Разложить по протоколу»;
//   ru     — №31: схема первой карточкой, ключи таблицей, редактор группы здесь же и в нём только набор «Дом»;
//   drop   — решение 8а: последняя российская группа с ключами (rug=one) — окно со списком ключей, удаление вместе с ключами.
// node tools/k3_t2_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3t2-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_t2_check: время вышло"); process.exit(3); }, 240000).unref();
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
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* грузится */ } await sleep(120); } throw new Error("не дождался: " + expr); }
const key = async (k, code, text) => {
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: { Enter: 13, " ": 32, ArrowDown: 40, ArrowUp: 38, ArrowRight: 39, ArrowLeft: 37, Escape: 27 }[k], text });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: { Enter: 13, " ": 32, ArrowDown: 40, ArrowUp: 38, ArrowRight: 39, ArrowLeft: 37, Escape: 27 }[k] });
  await sleep(150);
};
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
async function open(qs, hash, ready) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page]:not([hidden])')`, 20000);
  if (ready) await until(ready, 20000);
  await sleep(400);
}
const out = {};

// ── №25: сворачиваемые столбцы «Все выходы» ──
{
  await open("&lang=ru", "exits?tab=list", "!!document.querySelector('.gx-xt--exa .gx-xt__g')");
  await js(`(localStorage.removeItem("kaskad-cols-exits"), localStorage.removeItem("kaskad-density-exits"), true)`);
  await cdp("Page.reload"); await until(`!!document.querySelector('.gx-xt--exa .gx-xt__g')`, 20000); await sleep(500);
  const H = (k) => `document.querySelector('.gx-xt--exa .gx-xt__hw > .gx-xt__h:first-child > [data-ck=${k}]')`;
  const S = (k) => js(`(() => { const b = ${H(k)}; const c = document.querySelector('.gx-xt--exa .gx-xt__g .gx-xt__r > [data-ck=${k}]');
    const nm = document.querySelector('.gx-xt--exa .gx-xt__g .gx-xt__r > .gx-xt__x');
    return { cc: b.classList.contains('is-cc'), exp: b.getAttribute('aria-expanded'), aria: b.getAttribute('aria-label'), w: Math.round(b.getBoundingClientRect().width),
      cw: Math.round(c.getBoundingClientRect().width), cellCc: c.classList.contains('gx-xt__c--cc'), title: c.getAttribute('title'), nameW: Math.round(nm.getBoundingClientRect().width),
      kidsShown: [...c.children].some((x) => x.getClientRects().length > 0), store: localStorage.getItem("kaskad-cols-exits") }; })()`);
  const r = {};
  r.heads = await js(`[...document.querySelectorAll('.gx-xt--exa .gx-xt__hw > .gx-xt__h:first-child > *')].map((x) => (x.tagName === 'BUTTON' ? 'B:' : '') + (x.getAttribute('data-ck') || x.textContent))`);
  r.s0 = await S("type");
  await js(`${H("type")}.click(), true`); await sleep(200);
  r.s1 = await S("type");
  // раскрытие строки: значение свёрнутого столбца — строкой «Тип»
  await js(`document.querySelector('.gx-xt--exa .gx-xt__g .gx-xt__x').click(), true`); await sleep(200);
  r.subRow = await js(`(() => { const s = document.querySelector('.gx-xt--exa .gx-xt__g.is-open .gx-xt__sub'); const x = s.querySelector('[data-ccx=type]'), y = s.querySelector('[data-ccx=grp]');
    return { type: x && !x.hidden && x.getClientRects().length > 0 ? x.textContent : null, grpHidden: !!y && (y.hidden || !y.getClientRects().length) }; })()`);
  // память: после перезагрузки свёрнут
  await cdp("Page.reload"); await until(`!!document.querySelector('.gx-xt--exa .gx-xt__g')`, 20000); await sleep(500);
  r.reload = await S("type");
  // плотность «Крупно» — у неё своя сетка: свёрнутый всё равно 24
  await js(`[...document.querySelectorAll('[role=radio]')].find((b) => /Крупно/.test(b.textContent)).click(), true`); await sleep(300);
  r.lg = await S("type");
  await js(`[...document.querySelectorAll('[role=radio]')].find((b) => /Средне/.test(b.textContent)).click(), true`); await sleep(300);
  // клавиатура: фокус на «Группа», Enter — свернуть; подсказка видна в фокусе
  await js(`${H("grp")}.focus(), true`);
  await key("Enter", "Enter", "\r"); await sleep(150);
  r.kbd = await S("grp");
  r.tipFocus = await js(`(() => { const t = ${H("grp")}.querySelector('.gx-xt__tip'); return t && t.getClientRects().length > 0 ? t.textContent : null; })()`);
  // развернуть обратно оба
  await js(`${H("type")}.click(), ${H("grp")}.click(), true`); await sleep(200);
  r.back = await S("type");
  r.backCols = await js(`document.querySelector('.gx-xt--exa .gx-xt__hw').style.getPropertyValue('--cols')`);
  out.cc = r;
}
// ── №28–31: «Группы» ──
{
  await open("&rug=last", "exits?tab=groups", `!!document.querySelector('.kk-eg-card')`);
  out.grp = await js(`(async () => {
    const s = (ms) => new Promise((r) => setTimeout(r, ms));
    const cards = [...document.querySelectorAll('.kk-eg-card')];
    const names = cards.map((c) => (c.querySelector('.kk-eg-head h2, .kk-eg-head .gx-card__title') || {}).textContent || "");
    const menus = cards.map((c) => !!c.querySelector('.kk-eg-head .kk-gmw'));
    const tog = document.querySelector('.kk-eg-tog[aria-expanded="false"]'); if (tog) { tog.click(); await s(500); }
    const card = document.querySelector('.kk-eg-card');
    const bottomDel = [...card.querySelectorAll('button')].filter((b) => /Удалить группу/.test(b.textContent)).length;
    // «⋯» основной группы: только «Переименовать»; у «Стриминга» — ещё «Удалить группу…»
    const items = async (btn) => { btn.click(); await s(300); const r = [...document.querySelectorAll('.gx-menu [role="menuitem"], .gx-menu button')].map((b) => b.textContent.trim()); document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await s(200); return r; };
    const mainItems = await items(card.querySelector('.kk-gmw'));
    const delBtn = document.querySelector('[data-eg-del="g-5f1a2b"]');
    const streamItems = delBtn ? await items(delBtn) : null;
    const more = document.querySelector('.kk-eg-more__tog'); if (more && more.getAttribute('aria-expanded') !== 'true') { more.click(); await s(400); }
    const scn = document.querySelector('.kk-scn');
    const m = document.querySelector('.kk-eg-tier__m [aria-describedby]'), w = m && m.closest('.gx-popw');
    const vis = (n) => !!n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden' && getComputedStyle(n).display !== 'none';
    let pop = null;
    if (m) { m.click(); await s(150); const p0 = vis(w.querySelector('.gx-pop')) && w.classList.contains('is-pop');
      m.focus(); m.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await s(100);
      pop = { open: p0, exp: m.getAttribute('aria-expanded'), closed: !w.classList.contains('is-pop'), text: w.querySelector('.gx-pop').textContent.slice(0, 80) }; }
    const ru = document.querySelector('.kk-g2__ru');
    const r = { names, menus, bottomDel, mainItems, streamItems, scn: scn ? { t: scn.querySelector('.kk-scn__t').textContent, rows: scn.querySelectorAll('.kk-scn__r').length } : null,
      pop, bp: (document.querySelector('.kk-lv__bp') || {}).textContent || "", ruLink: ru ? ru.textContent : null };
    if (ru) { ru.click(); await s(800); r.ruHash = location.hash; }
    return r;
  })()`);
}
// ── №31: «Российские группы» ──
{
  await open("&rug=last", "exits?tab=ru", `!!document.querySelector('.kk-eg-card--ru')`);
  // российская группа — общая карточка групп (РФ-группы Ш1): набор, уровни и ключи — прямо в карточке, без отдельного
  // «Настроить группу»; «Если все выходы недоступны» — строкой выбора вверху, не последней строкой таблицы
  out.ru = await js(`(async () => {
    const s = (ms) => new Promise((r) => setTimeout(r, ms));
    const part = document.querySelector('.kk-part'), kids = [...part.querySelectorAll('.kk-ru2__btns, .kk-rus, .kk-eg-card--ru')];
    const order = kids.map((n) => n.classList.contains('kk-rus') ? "scheme" : n.classList.contains('kk-ru2__btns') ? "btns" : "card");
    const schemeW = Math.round(document.querySelector('.kk-rus').getBoundingClientRect().width), cardW = Math.round(document.querySelector('.kk-eg-card--ru').getBoundingClientRect().width);
    const tog = document.querySelector('.kk-eg-card--ru .kk-eg-tog'); if (tog.getAttribute('aria-expanded') !== 'true') { tog.click(); await s(500); }
    const ed = document.querySelector('.kk-eg-card--ru');
    const keys = [...ed.querySelectorAll('.kk-eg-exit__n > b')].map((b) => b.textContent);
    const presets = [...ed.querySelectorAll('.kk-eg-presets [role="radio"]')].map((r) => r.textContent.trim().split(/\\s{2,}|\\n/)[0]);
    return { order, schemeW, cardW, keys, ed: !!ed.querySelector('.kk-eg-warn'), presets, ruCls: !!ed.querySelector('.kk-eg-presets--ru'), compat: !!ed.querySelector('.kk-eg-compat'),
      edHead: ed.querySelectorAll('.kk-eg-head').length !== 1 };
  })()`);
}
// ── решение 8а: последняя российская группа вместе с ключами ──
{
  await open("&rug=one", "exits?tab=ru", `!!document.querySelector('.kk-eg-card--ru')`);
  out.drop = await js(`(async () => {
    const s = (ms) => new Promise((r) => setTimeout(r, ms));
    const db0 = KASKAD_MOCK.db(), ru0 = db0.exits.filter((e) => e.role === "ru").map((e) => e.id);
    document.querySelector('[data-eg-del]').click(); await s(300);
    [...document.querySelectorAll('.gx-menu [role="menuitem"], .gx-menu button')].find((b) => /Удалить группу/.test(b.textContent)).click(); await s(1500);
    const d = [...document.querySelectorAll('.gx-dlg')].pop();
    const keys = [...d.querySelectorAll('.kk-dlg3__kn')].map((x) => x.textContent);
    const al = (d.querySelector('.gx-al') || {}).textContent || "";
    const btn = [...d.querySelectorAll('button')].find((b) => /Удалить группу/.test(b.textContent));
    const r = { keys, al, btn: btn ? btn.textContent.trim() : null, dis: btn ? btn.disabled : null, ru0: ru0.length };
    btn.click(); await s(1200);
    const yes = [...document.querySelectorAll('.gx-dlg button')].find((b) => /Удалить группу и ключи|Да|Удалить/.test(b.textContent) && b !== btn && b.isConnected); if (yes && !/Отмена/.test(yes.textContent)) { yes.click(); await s(1500); }
    const db = KASKAD_MOCK.db();
    r.groupsRu = (db.policy.groups || []).filter((g) => g.role === "ru").length;
    r.left = db.exits.filter((e) => ru0.indexOf(e.id) >= 0).length;
    r.toast = [...document.querySelectorAll('.gx-toast')].map((x) => x.textContent).join(" | ");
    r.empty = !!document.querySelector('[data-find="exits.rugEmpty"]');
    return r;
  })()`);
}
console.log(JSON.stringify(out));
stop();
process.exit(0);
